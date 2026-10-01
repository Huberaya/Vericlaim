"""In-process metrics registry with a Prometheus text exposition (chantier C16).

There was no telemetry of any kind: `GET /api/v1/enterprise/metrics` returned
literals (`average_analysis_latency_ms: 142.5`, `error_rate_percent: 0.02`,
`memory_usage_mb: 128.4`) computed from nothing, and the process itself counted
nothing at all.

Scope, stated honestly: this is an **in-process** registry with **process-local**
counters. It is correct for one instance and for a scrape-based Prometheus setup;
it is *not* an aggregation layer. With several API replicas each one exposes its own
counters, which is exactly how Prometheus expects to consume them — but a reader must
not sum a `histogram_quantile` across replicas of a *gauge* like memory usage. The
queue-depth and business gauges are computed from the database at scrape time, so
they are shared by construction.

No dependency is added: the exposition format is small and stable, and pulling a
metrics library into the runtime for four counter types would be a supply-chain
decision this chantier does not need.

Everything a metric needs is declared here; nothing is fabricated at read time.
"""

from __future__ import annotations

import math
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable

from sqlalchemy import func, select

#: Histogram buckets, in seconds, chosen for a document pipeline: sub-second API
#: calls, tens of seconds for extraction and analysis. They are published with the
#: metric so a consumer never has to guess them.
DURATION_BUCKETS_SECONDS: tuple[float, ...] = (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 300)


def _labels_key(labels: dict[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(k), str(v)) for k, v in labels.items()))


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _format_labels(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    rendered = ",".join(f'{key}="{_escape(value)}"' for key, value in sorted(labels.items()))
    return "{" + rendered + "}"


class MetricsRegistry:
    """Counters, gauges and histograms, with thread-safe updates."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._histograms: dict[tuple[str, tuple[tuple[str, str], ...]], dict[str, object]] = {}
        self._help: dict[str, str] = {}
        self._types: dict[str, str] = {}

    # -- declaration ------------------------------------------------------ #

    def describe(self, name: str, *, metric_type: str, help_text: str = "") -> None:
        self._types.setdefault(name, metric_type)
        if help_text:
            self._help.setdefault(name, help_text)

    # -- updates ---------------------------------------------------------- #

    def increment(self, name: str, labels: dict[str, str] | None = None, amount: float = 1.0) -> None:
        key = (name, _labels_key(labels or {}))
        with self._lock:
            self._counters[key] = self._counters.get(key, 0.0) + amount

    def set_gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        key = (name, _labels_key(labels or {}))
        with self._lock:
            self._gauges[key] = float(value)

    def observe(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        key = (name, _labels_key(labels or {}))
        with self._lock:
            bucket = self._histograms.get(key)
            if bucket is None:
                bucket = {
                    "buckets": {bound: 0 for bound in DURATION_BUCKETS_SECONDS},
                    "count": 0,
                    "sum": 0.0,
                }
                self._histograms[key] = bucket
            for bound in DURATION_BUCKETS_SECONDS:
                if value <= bound:
                    bucket["buckets"][bound] += 1  # type: ignore[index]
            bucket["count"] += 1  # type: ignore[operator]
            bucket["sum"] += value  # type: ignore[operator]

    # -- readers ---------------------------------------------------------- #

    def counter_total(self, name: str, labels: dict[str, str] | None = None) -> float:
        with self._lock:
            if labels is None:
                return sum(value for (metric, _), value in self._counters.items() if metric == name)
            return self._counters.get((name, _labels_key(labels)), 0.0)

    def counter_total_by_label(self, name: str, labels: dict[str, str]) -> float:
        """Sum every series of `name` whose labels include the given pairs."""
        with self._lock:
            items = list(self._counters.items())
        return sum(
            value
            for (metric, series), value in items
            if metric == name and all(dict(series).get(key) == wanted for key, wanted in labels.items())
        )

    def histogram_snapshot(self, name: str) -> dict[str, float]:
        """Total count and sum across every label set of `name`."""
        with self._lock:
            count = sum(int(b["count"]) for (metric, _), b in self._histograms.items() if metric == name)
            total = sum(float(b["sum"]) for (metric, _), b in self._histograms.items() if metric == name)
        return {"count": count, "sum": total}

    def reset(self) -> None:
        """Only used by tests, to make a metric assertion independent of other tests."""
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()

    def render(self) -> str:
        lines: list[str] = []
        with self._lock:
            counters = dict(self._counters)
            gauges = dict(self._gauges)
            histograms = {key: dict(value) for key, value in self._histograms.items()}

        for name in sorted({metric for metric, _ in counters}):
            lines.append(f"# TYPE {name} counter")
            if name in self._help:
                lines.append(f"# HELP {name} {self._help[name]}")
        for (name, labels), value in sorted(counters.items()):
            lines.append(f"{name}{_format_labels(dict(labels))} {_render_number(value)}")

        for name in sorted({metric for metric, _ in gauges}):
            lines.append(f"# TYPE {name} gauge")
            if name in self._help:
                lines.append(f"# HELP {name} {self._help[name]}")
        for (name, labels), value in sorted(gauges.items()):
            lines.append(f"{name}{_format_labels(dict(labels))} {_render_number(value)}")

        for name in sorted({metric for metric, _ in histograms}):
            lines.append(f"# TYPE {name} histogram")
            if name in self._help:
                lines.append(f"# HELP {name} {self._help[name]}")
        for (name, labels), bucket in sorted(histograms.items()):
            cumulative = 0
            for bound in DURATION_BUCKETS_SECONDS:
                cumulative = int(bucket["buckets"][bound])  # type: ignore[index]
                lines.append(
                    f"{name}_bucket{_format_labels({**dict(labels), 'le': _render_number(bound)})} {cumulative}"
                )
            lines.append(f"{name}_bucket{_format_labels({**dict(labels), 'le': '+Inf'})} {int(bucket['count'])}")
            lines.append(f"{name}_count{_format_labels(dict(labels))} {int(bucket['count'])}")
            lines.append(f"{name}_sum{_format_labels(dict(labels))} {_render_number(float(bucket['sum']))}")

        return "\n".join(lines) + "\n"


def _render_number(value: float) -> str:
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return f"{value:.6g}"


REGISTRY = MetricsRegistry()

# ------------------------------------------------------------------ metrics -- #

REGISTRY.describe(
    "vericlaim_http_requests_total", metric_type="counter",
    help_text="Requêtes HTTP servies, par méthode, route et classe de statut.",
)
REGISTRY.describe(
    "vericlaim_http_request_duration_seconds", metric_type="histogram",
    help_text="Durée des requêtes HTTP, par route (gabarit de route, pas l'URL brute).",
)
REGISTRY.describe(
    "vericlaim_jobs_processed_total", metric_type="counter",
    help_text="Traitements de job terminés, par type et résultat (processed, failed, skipped).",
)
REGISTRY.describe(
    "vericlaim_job_duration_seconds", metric_type="histogram",
    help_text="Durée d'un traitement de job, par type.",
)
REGISTRY.describe(
    "vericlaim_worker_heartbeat_timestamp_seconds", metric_type="gauge",
    help_text="Horodatage du dernier battement de cœur d'un worker (0 = jamais vu).",
)
REGISTRY.describe(
    "vericlaim_readiness_failed", metric_type="gauge",
    help_text="1 si la dernière évaluation de disponibilité a échoué, 0 sinon.",
)
REGISTRY.describe(
    "vericlaim_alerts_open", metric_type="gauge",
    help_text="Alertes ouvertes par sévérité (évaluées au moment du scrape).",
)

# Business gauges, evaluated at scrape time from the database.
_GAUGE_PROVIDERS: list[tuple[str, dict[str, str], Callable[[object], float]]] = []


def gauge_provider(name: str, labels: dict[str, str], provider: Callable[[object], float]) -> None:
    """Register a gauge computed from the database when /metrics is scraped.

    Queue depth must not be a counter incremented in the process that enqueues: with
    several workers and several API replicas the counts drift immediately. It is a
    question asked to the database at scrape time, which is also the only place where
    the answer is unique.
    """
    _GAUGE_PROVIDERS.append((name, labels, provider))


def _collect_business_gauges() -> None:
    from app.core.database import SessionLocal

    try:
        with SessionLocal() as db:
            for name, labels, provider in _GAUGE_PROVIDERS:
                try:
                    REGISTRY.set_gauge(name, float(provider(db)), labels)
                except Exception:  # noqa: BLE001 — a failing gauge must not break the scrape
                    REGISTRY.set_gauge(name, float("nan"), labels)
    except Exception:  # noqa: BLE001 — database unavailable: expose what was collected
        return


def render_metrics(*, include_business_gauges: bool = True) -> str:
    if include_business_gauges:
        _collect_business_gauges()
    return REGISTRY.render()


# ------------------------------------------------------- business gauge set -- #


def _pending_document_extraction(db) -> float:
    from app.models.domain import DocumentExtractionJob, DocumentExtractionJobStatus

    return float(
        db.scalar(
            select(func.count(DocumentExtractionJob.id)).where(
                DocumentExtractionJob.status.in_(
                    [
                        DocumentExtractionJobStatus.QUEUED,
                        DocumentExtractionJobStatus.RUNNING,
                    ]
                )
            )
        )
        or 0
    )


def _pending_analysis_detection(db) -> float:
    from app.models.domain import AnalysisDetectionJob, AnalysisDetectionJobStatus

    return float(
        db.scalar(
            select(func.count(AnalysisDetectionJob.id)).where(
                AnalysisDetectionJob.status.in_(
                    [
                        AnalysisDetectionJobStatus.QUEUED,
                        AnalysisDetectionJobStatus.RUNNING,
                    ]
                )
            )
        )
        or 0
    )


def _failed_jobs(db) -> float:
    from app.models.domain import (
        AnalysisDetectionJob,
        AnalysisDetectionJobStatus,
        DocumentExtractionJob,
        DocumentExtractionJobStatus,
    )

    return float(
        (db.scalar(
            select(func.count(DocumentExtractionJob.id)).where(
                DocumentExtractionJob.status == DocumentExtractionJobStatus.FAILED
            )
        ) or 0)
        + (db.scalar(
            select(func.count(AnalysisDetectionJob.id)).where(
                AnalysisDetectionJob.status == AnalysisDetectionJobStatus.FAILED
            )
        ) or 0)
    )


def _oldest_pending_job_age(db) -> float:
    """Age in seconds of the oldest queued job; 0 when the queues are empty."""
    from datetime import datetime, timezone

    from app.models.domain import (
        AnalysisDetectionJob,
        AnalysisDetectionJobStatus,
        DocumentExtractionJob,
        DocumentExtractionJobStatus,
    )

    oldest = None
    for model, pending in (
        (DocumentExtractionJob, DocumentExtractionJobStatus.QUEUED),
        (AnalysisDetectionJob, AnalysisDetectionJobStatus.QUEUED),
    ):
        value = db.scalar(
            select(func.min(model.created_at)).where(model.status == pending)
        )
        if value is not None and (oldest is None or value < oldest):
            oldest = value
    if oldest is None:
        return 0.0
    if oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - oldest).total_seconds())


gauge_provider(
    "vericlaim_document_extraction_queue_depth",
    {"queue": "document_extraction"},
    _pending_document_extraction,
)
gauge_provider(
    "vericlaim_analysis_detection_queue_depth",
    {"queue": "analysis_detection"},
    _pending_analysis_detection,
)
def _job_failures_document_extraction(db) -> float:
    """Jobs whose latest extraction attempt failed (retryable or definitive)."""
    from app.models.domain import DocumentExtractionJob

    return float(
        db.scalar(
            select(func.count(DocumentExtractionJob.id)).where(
                DocumentExtractionJob.error_code.is_not(None)
            )
        )
        or 0
    )


def _job_failures_analysis_detection(db) -> float:
    """Jobs whose latest claim-detection attempt failed (retryable or definitive)."""
    from app.models.domain import AnalysisDetectionJob

    return float(
        db.scalar(
            select(func.count(AnalysisDetectionJob.id)).where(AnalysisDetectionJob.error_code.is_not(None))
        )
        or 0
    )


gauge_provider("vericlaim_jobs_failed", {"state": "failed"}, _failed_jobs)
#: `vericlaim_jobs_failed` counts *definitive* failures (status FAILED). A job that
#: failed and will be retried is not in it, which is correct but insufficient: the
#: job failure rate should not read zero while documents are failing. These two
#: gauges count the last attempt's outcome, per queue, from the database.
gauge_provider(
    "vericlaim_job_failures_total",
    {"queue": "document_extraction"},
    _job_failures_document_extraction,
)
gauge_provider(
    "vericlaim_job_failures_total",
    {"queue": "analysis_detection"},
    _job_failures_analysis_detection,
)
gauge_provider("vericlaim_oldest_pending_job_age_seconds", {}, _oldest_pending_job_age)


# ------------------------------------------------------------- HTTP helpers -- #

#: Routes whose path contains an identifier are normalised before being used as a
#: label. Without this, the cardinality of the metric equals the number of documents
#: ever created, which is how a metrics backend falls over.
def route_template(request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if path:
        return str(path)
    return "<inconnue>"


@dataclass
class RequestTimer:
    started: float = field(default_factory=time.monotonic)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started


def record_http_request(*, method: str, template: str, status_code: int, duration_seconds: float) -> None:
    status_class = f"{status_code // 100}xx"
    REGISTRY.increment(
        "vericlaim_http_requests_total",
        {"method": method.upper(), "route": template, "status_class": status_class},
    )
    if not math.isfinite(duration_seconds):
        duration_seconds = 0.0
    REGISTRY.observe(
        "vericlaim_http_request_duration_seconds",
        duration_seconds,
        {"method": method.upper(), "route": template},
    )


def record_job_outcome(*, job_kind: str, outcome: str, duration_seconds: float | None) -> None:
    REGISTRY.increment("vericlaim_jobs_processed_total", {"job_kind": job_kind, "outcome": outcome})
    if duration_seconds is not None:
        REGISTRY.observe("vericlaim_job_duration_seconds", duration_seconds, {"job_kind": job_kind})


def record_worker_heartbeat(*, worker_kind: str, worker_id: str | None = None) -> None:
    labels = {"worker_kind": worker_kind}
    if worker_id:
        labels["worker_id"] = worker_id
    REGISTRY.set_gauge("vericlaim_worker_heartbeat_timestamp_seconds", time.time(), labels)


def worker_heartbeats() -> Iterable[tuple[dict[str, str], float]]:
    """Yield (labels, timestamp) for every worker that ever beat in this process."""
    with REGISTRY._lock:  # noqa: SLF001 — same module owns the registry
        for (name, labels), value in REGISTRY._gauges.items():
            if name == "vericlaim_worker_heartbeat_timestamp_seconds":
                yield dict(labels), float(value)


def error_rate_percent() -> float:
    """Share of 5xx responses among the requests counted since process start.

    `0.0` when nothing has been served yet: an empty denominator is not a 0 % error
    rate, and `total_api_requests` distinguishes the two.
    """
    total = REGISTRY.counter_total("vericlaim_http_requests_total")
    if total <= 0:
        return 0.0
    errors = REGISTRY.counter_total_by_label(
        "vericlaim_http_requests_total", {"status_class": "5xx"}
    )
    return round(errors / total * 100.0, 4)


# --------------------------------------------------------- process metrics -- #


def main_process_memory_mb() -> float | None:
    """Resident memory of this process, in MB. None when the platform cannot say."""
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except Exception:  # noqa: BLE001 — not available on every platform
        return None
    # Linux reports kilobytes, macOS bytes. The distinction matters, and guessing
    # wrong would publish a number off by a factor of 1024.
    if sys.platform == "darwin":
        return round(usage / (1024 * 1024), 2)
    return round(usage / 1024, 2)


def process_cpu_percent() -> float | None:
    """CPU consumed by this process since it started, as a share of one core.

    Measured from `getrusage` against the process's own uptime: it is a true,
    verifiable figure, unlike the fabricated `cpu_utilization_percent: 4.2` that was
    returned before. It is explicitly *not* an instantaneous load average, and the
    note in the response says so.
    """
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF)
    except Exception:  # noqa: BLE001
        return None
    elapsed = time.time() - _PROCESS_STARTED_AT
    if elapsed <= 0:
        return None
    return round((usage.ru_utime + usage.ru_stime) / elapsed * 100.0, 3)


_PROCESS_STARTED_AT = time.time()
