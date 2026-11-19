"""Métriques actionnables pour A-09, au format Prometheus.

A-09 constatait qu'aucune métrique n'existait : ``/readyz`` disait « prêt » ou
« pas prêt », et rien ne permettait de savoir *pourquoi* la file n'avançait
plus. Ce module expose les quatre signaux qui distinguent les causes :

* **API** — requêtes, erreurs 5xx, latence ;
* **base** — disponibilité de la sonde de connexion ;
* **files** — profondeur par état, âge du plus vieux job en attente ;
* **antivirus** — joignabilité et refus.

Le format est du texte Prometheus, volontairement sans dépendance : une
bibliothèque de métriques serait une dépendance de plus pour un besoin de
16 lignes. Aucun label ne contient de tenant, d'identifiant ou de nom de
fichier : une métrique n'est pas un journal.

Ce que ce module ne fait pas : il n'envoie d'alerte à personne. Les seuils
sont déclarés dans ``infra/monitoring/`` et c'est le collecteur qui décide.

**Fusion C16 (PR #7).** Le chantier C16 a apporté un second registre, plus
large : compteurs et jauges nommés, histogrammes configurables, jauges métier
lues en base au moment du scrape (profondeur de file, âge du plus vieux job,
échecs) et battements de cœur des workers. Les deux registres répondaient au
même besoin et n'étaient pas interchangeables : A-09 expose
``observe_request``/``observe_scanner``/``render(extra)``, C16 expose
``increment``/``set_gauge``/``observe`` et les fonctions ``record_*``.

Le registre de A-09 est conservé **tel quel** (les tests et le middleware A-09
en dépendent), et la surface C16 est greffée dessus : les compteurs et jauges
nommés sont tenus en interne, ``render`` continue de produire exactement le
texte de A-09, et ``render_metrics`` ajoute les séries C16. Aucun signal de
A-09 n'est perdu, aucun signal C16 n'est inventé.

Portée, dite honnêtement : ce registre est **en processus**. Les jauges métier
sont calculées en base au moment du scrape, donc partagées entre réplicas ;
les compteurs, eux, sont locaux au processus — c'est ce que Prometheus attend
d'une cible, mais il ne faut pas additionner un ``histogram_quantile`` entre
réplicas.
"""

from __future__ import annotations

import math
import sys
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Iterable

from sqlalchemy import func, select

_LATENCY_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

#: Buckets des histogrammes nommés (C16), en secondes : sous-la-seconde pour
#: l'API, dizaines de secondes pour l'extraction et l'analyse.
DURATION_BUCKETS_SECONDS: tuple[float, ...] = (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 300)

#: Familles déjà rendues par la voie A-09 (``render``). Les publier aussi via la
#: voie C16 dupliquerait les lignes ``# TYPE`` et les séries : Prometheus
#: rejetterait l'exposition comme malformée.
_OWNED_BY_A09 = frozenset({"vericlaim_http_requests_total", "vericlaim_http_request_duration_seconds"})


@dataclass
class _Counters:
    requests: int = 0
    server_errors: int = 0
    latency_sum: float = 0.0
    latency_count: int = 0
    latency_buckets: dict[float, int] = field(default_factory=lambda: defaultdict(int))


class MetricsRegistry:
    """Registre minimal, sûr entre threads.

    Les routes FastAPI s'exécutent dans un pool : un compteur non verrouillé
    perdrait des incréments, ce qui rendrait une alerte de taux d'erreur
    silencieusement fausse.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, str, str], _Counters] = defaultdict(_Counters)
        self._started_at = time.monotonic()
        self.scanner_reachable = 0
        self.scanner_checks = 0
        # Surface C16 : compteurs, jauges et histogrammes nommés.
        self._named_counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._histograms: dict[tuple[str, tuple[tuple[str, str], ...]], dict[str, object]] = {}
        self._help: dict[str, str] = {}
        self._types: dict[str, str] = {}

    def observe_request(self, *, method: str, route: str, status_code: int, duration: float) -> None:
        # Le chemin est normalisé par l'appelant : jamais l'URL brute, qui
        # contiendrait des identifiants de documents.
        key = (method, route, str(status_code))
        with self._lock:
            counter = self._counters[key]
            counter.requests += 1
            if status_code >= 500:
                counter.server_errors += 1
            counter.latency_sum += duration
            counter.latency_count += 1
            for bound in _LATENCY_BUCKETS:
                if duration <= bound:
                    counter.latency_buckets[bound] += 1

    def observe_scanner(self, reachable: bool) -> None:
        with self._lock:
            self.scanner_checks += 1
            if reachable:
                self.scanner_reachable += 1

    def uptime_seconds(self) -> float:
        return time.monotonic() - self._started_at

    # -- surface C16 ------------------------------------------------------- #

    def describe(self, name: str, *, metric_type: str, help_text: str = "") -> None:
        self._types.setdefault(name, metric_type)
        if help_text:
            self._help[name] = help_text

    def increment(self, name: str, labels: dict[str, str] | None = None, amount: float = 1.0) -> None:
        key = (name, _labels_key(labels or {}))
        with self._lock:
            self._named_counters[key] = self._named_counters.get(key, 0.0) + amount

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

    def counter_total(self, name: str, labels: dict[str, str] | None = None) -> float:
        """Valeur d'un compteur nommé.

        Sans labels, c'est la **somme de toutes les séries** de ce nom : un
        compteur à labels dimensionnels n'a pas de série unique, et renvoyer 0
        parce que la série « sans label » n'existe pas donnerait un chiffre faux
        (un taux d'erreur calculé dessus serait silencieusement à zéro).
        """
        if labels:
            key = (name, _labels_key(labels))
            with self._lock:
                return float(self._named_counters.get(key, 0.0))
        with self._lock:
            return float(
                sum(value for (metric, _series), value in self._named_counters.items() if metric == name)
            )

    def counter_total_by_label(self, name: str, labels: dict[str, str]) -> float:
        """Somme des séries dont les labels contiennent ``labels`` (sous-ensemble)."""
        wanted = _labels_key(labels)
        with self._lock:
            total = 0.0
            for (metric, series), value in self._named_counters.items():
                if metric != name:
                    continue
                current = dict(series)
                if all(current.get(key) == expected for key, expected in wanted):
                    total += value
            return total

    def histogram_snapshot(self, name: str) -> dict[str, float]:
        with self._lock:
            count = 0
            total = 0.0
            for (metric, _labels), bucket in self._histograms.items():
                if metric == name:
                    count += int(bucket["count"])  # type: ignore[arg-type]
                    total += float(bucket["sum"])  # type: ignore[arg-type]
            return {"count": float(count), "sum": total}

    def reset(self) -> None:
        """Vide le registre : réservé aux tests, jamais appelé par le service."""
        with self._lock:
            self._counters.clear()
            self._named_counters.clear()
            self._gauges.clear()
            self._histograms.clear()
            self.scanner_reachable = 0
            self.scanner_checks = 0

    def render_c16(self) -> str:
        """Séries C16 au format Prometheus : compteurs, jauges et histogrammes nommés."""
        lines: list[str] = []
        with self._lock:
            counters = dict(self._named_counters)
            gauges = dict(self._gauges)
            histograms = {key: dict(value) for key, value in self._histograms.items()}
            help_texts = dict(self._help)

        for name in sorted({metric for metric, _ in counters}):
            if name in _OWNED_BY_A09:
                continue
            if name in help_texts:
                lines.append(f"# HELP {name} {help_texts[name]}")
            lines.append(f"# TYPE {name} counter")
        for (name, labels), value in sorted(counters.items()):
            if name in _OWNED_BY_A09:
                continue
            lines.append(f"{name}{_format_labels(dict(labels))} {_render_number(value)}")

        for name in sorted({metric for metric, _ in gauges}):
            if name in help_texts:
                lines.append(f"# HELP {name} {help_texts[name]}")
            lines.append(f"# TYPE {name} gauge")
        for (name, labels), value in sorted(gauges.items()):
            lines.append(f"{name}{_format_labels(dict(labels))} {_render_number(value)}")

        for name in sorted({metric for metric, _ in histograms}):
            if name in _OWNED_BY_A09:
                continue
            if name in help_texts:
                lines.append(f"# HELP {name} {help_texts[name]}")
            lines.append(f"# TYPE {name} histogram")
        for (name, labels), bucket in sorted(histograms.items()):
            if name in _OWNED_BY_A09:
                continue
            for bound in DURATION_BUCKETS_SECONDS:
                cumulative = int(bucket["buckets"][bound])  # type: ignore[index]
                lines.append(
                    f"{name}_bucket{_format_labels({**dict(labels), 'le': _render_number(bound)})} {cumulative}"
                )
            lines.append(f"{name}_bucket{_format_labels({**dict(labels), 'le': '+Inf'})} {int(bucket['count'])}")
            lines.append(f"{name}_count{_format_labels(dict(labels))} {int(bucket['count'])}")
            lines.append(f"{name}_sum{_format_labels(dict(labels))} {_render_number(float(bucket['sum']))}")

        return "\n".join(lines)

    def render(self, extra: dict[str, str] | None = None) -> str:
        """Rend le registre au format texte Prometheus."""

        lines: list[str] = []
        lines.append("# HELP vericlaim_uptime_seconds Durée depuis le démarrage du processus.")
        lines.append("# TYPE vericlaim_uptime_seconds gauge")
        lines.append(f"vericlaim_uptime_seconds {self.uptime_seconds():.3f}")

        lines.append("# HELP vericlaim_http_requests_total Requêtes HTTP traitées.")
        lines.append("# TYPE vericlaim_http_requests_total counter")
        for (method, route, status_code), counter in sorted(self._counters.items()):
            labels = f'method="{method}",route="{route}",status="{status_code}"'
            lines.append(f"vericlaim_http_requests_total{{{labels}}} {counter.requests}")

        lines.append("# HELP vericlaim_http_request_duration_seconds Durée des requêtes.")
        lines.append("# TYPE vericlaim_http_request_duration_seconds histogram")
        for (method, route, status_code), counter in sorted(self._counters.items()):
            base = f'method="{method}",route="{route}",status="{status_code}"'
            for bound in _LATENCY_BUCKETS:
                lines.append(
                    f'vericlaim_http_request_duration_seconds_bucket{{{base},le="{bound}"}} '
                    f"{counter.latency_buckets[bound]}"
                )
            lines.append(f'vericlaim_http_request_duration_seconds_bucket{{{base},le="+Inf"}} {counter.latency_count}')
            lines.append(f"vericlaim_http_request_duration_seconds_sum{{{base}}} {counter.latency_sum:.6f}")
            lines.append(f"vericlaim_http_request_duration_seconds_count{{{base}}} {counter.latency_count}")

        lines.append("# HELP vericlaim_scanner_checks_total Sondes de joignabilité du moteur antivirus.")
        lines.append("# TYPE vericlaim_scanner_checks_total counter")
        lines.append(f"vericlaim_scanner_checks_total {self.scanner_checks}")
        lines.append("# HELP vericlaim_scanner_reachable_total Sondes ayant obtenu une réponse du moteur.")
        lines.append("# TYPE vericlaim_scanner_reachable_total counter")
        lines.append(f"vericlaim_scanner_reachable_total {self.scanner_reachable}")

        for name, value in (extra or {}).items():
            safe_name = "".join(character if character.isalnum() or character == "_" else "_" for character in name)
            lines.append(f"# TYPE vericlaim_{safe_name} gauge")
            lines.append(f"vericlaim_{safe_name} {value}")

        return "\n".join(lines) + "\n"


# ------------------------------------------------------- helpers de format -- #


def _labels_key(labels: dict[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(key), str(value)) for key, value in labels.items()))


def _escape(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _format_labels(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    rendered = ",".join(f'{key}="{_escape(value)}"' for key, value in sorted(labels.items()))
    return "{" + rendered + "}"


def _render_number(value: float) -> str:
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return f"{value:.6g}"


REGISTRY = MetricsRegistry()

# -------------------------------------------------- jauges métier (C16) ----- #

#: Jauges calculées en base au moment du scrape. Elles ne peuvent pas être des
#: compteurs incrémentés par le processus qui empile : avec plusieurs workers et
#: plusieurs réplicas API, les comptes divergent immédiatement.
_GAUGE_PROVIDERS: list[tuple[str, dict[str, str], Callable[[object], float]]] = []


def gauge_provider(name: str, labels: dict[str, str], provider: Callable[[object], float]) -> None:
    """Déclare une jauge calculée depuis la base au moment du scrape."""
    _GAUGE_PROVIDERS.append((name, labels, provider))


def _collect_business_gauges() -> None:
    from app.core.database import SessionLocal

    try:
        with SessionLocal() as db:
            for name, labels, provider in _GAUGE_PROVIDERS:
                try:
                    REGISTRY.set_gauge(name, float(provider(db)), labels)
                except Exception:  # noqa: BLE001 — une jauge en échec ne casse pas le scrape
                    REGISTRY.set_gauge(name, float("nan"), labels)
    except Exception:  # noqa: BLE001 — base injoignable : on expose ce qui a été collecté
        return


def render_metrics(*, include_business_gauges: bool = True) -> str:
    """Texte Prometheus complet : séries A-09 + séries C16 (+ jauges métier)."""
    if include_business_gauges:
        _collect_business_gauges()
    body = REGISTRY.render()
    extra = REGISTRY.render_c16()
    return body + extra if extra else body


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




# ------------------------------------------------- métriques de processus --- #


def main_process_memory_mb() -> float | None:
    """Mémoire résidente de ce processus, en Mo. None si la plateforme ne sait pas."""
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except Exception:  # noqa: BLE001 — pas disponible partout
        return None
    # Linux rapporte des kilo-octets, macOS des octets. Se tromper donnerait un
    # chiffre faux d'un facteur 1024.
    if sys.platform == "darwin":
        return round(usage / (1024 * 1024), 2)
    return round(usage / 1024, 2)


def process_cpu_percent() -> float | None:
    """CPU consommé par ce processus depuis son démarrage, en part d'un cœur.

    Mesuré depuis `getrusage` rapporté à l'uptime du processus : c'est un chiffre
    vérifiable, à la différence du `cpu_utilization_percent` fabriqué d'avant.
    Ce n'est explicitement pas une charge instantanée.
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
