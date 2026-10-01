"""Deep readiness probes (chantier C18).

`/healthz` used to answer `{"status": "ok"}` without touching anything. An
orchestrator, a load balancer or a pager could therefore keep sending traffic to a
process whose database, object storage or antivirus was unreachable — the incident
was invisible until a client reported it.

The split implemented here is the standard one, and it is a *functional* split, not
a cosmetic one:

* **liveness** (`/healthz`) answers "is this process alive?". It never touches a
  dependency: a process that cannot reach its database must **not** be restarted in
  a loop, it must be taken out of rotation.
* **readiness** (`/readyz`) answers "can this instance do useful work right now?".
  It probes the database, the object storage, the antivirus and the migration
  revision, and returns **503** as soon as a dependency required by the current
  configuration fails.

Three rules this module refuses to break:

1. **A probe that cannot answer is not a passing probe.** An unexpected exception
   yields `failed`, never `ok`.
2. **A dependency deliberately disabled is reported as such** (`disabled`) and
   published in `capabilities`, so a deployment without document storage is not
   silently believed to be complete. In staging/production the configuration
   already refuses that state; in development it is visible, not hidden.
3. **No infrastructure detail leaks to an unauthenticated caller.** A public probe
   returns the exception *class*; the full message goes to the structured log.
   Bucket names, endpoints and DSNs stay out of the HTTP response.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from app.core.config import Settings

logger = logging.getLogger("vericlaim.readiness")

#: A readiness probe must answer fast: an orchestrator waits on it. Object storage
#: and antivirus probes are given a short budget, well under any sane probe timeout.
PROBE_TIMEOUT_SECONDS = 3.0


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of one dependency probe."""

    name: str
    status: str  # ok | failed | disabled | unknown
    detail: str
    required: bool
    latency_ms: int

    @property
    def is_ok(self) -> bool:
        return self.status in {"ok", "disabled"}

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "required": self.required,
            "latency_ms": self.latency_ms,
        }


@dataclass
class ReadinessReport:
    ready: bool
    checks: list[ProbeResult] = field(default_factory=list)
    capabilities: dict[str, bool] = field(default_factory=dict)
    environment: str = "unknown"

    def as_dict(self) -> dict[str, object]:
        return {
            "status": "ready" if self.ready else "not_ready",
            "environment": self.environment,
            "checks": [check.as_dict() for check in self.checks],
            "capabilities": self.capabilities,
            "failed": [check.name for check in self.checks if not check.is_ok and check.required],
        }


def _timed(name: str, *, required: bool, call) -> ProbeResult:
    """Run one probe, and treat any surprise as a failure rather than a success."""
    started = time.monotonic()
    try:
        ok, detail = call()
    except Exception as exc:  # noqa: BLE001 — a probe must never propagate
        logger.warning(
            "Readiness probe raised",
            extra={"event": "probe_raised", "probe": name, "error": repr(exc)},
        )
        ok, detail = False, f"exception: {type(exc).__name__}"
    latency_ms = int((time.monotonic() - started) * 1000)
    return ProbeResult(
        name=name,
        status="ok" if ok else "failed",
        detail=detail,
        required=required,
        latency_ms=latency_ms,
    )


def _disabled(name: str, detail: str) -> ProbeResult:
    return ProbeResult(name=name, status="disabled", detail=detail, required=False, latency_ms=0)


def probe_database() -> ProbeResult:
    """`SELECT 1` on the configured database, on a connection this probe opens."""

    def run() -> tuple[bool, str]:
        from sqlalchemy import text

        from app.core.database import SessionLocal

        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        return True, "SELECT 1 exécuté"

    return _timed("database", required=True, call=run)


def probe_migrations(settings: Settings) -> ProbeResult:
    """Compare the revision actually applied in the database with the packaged head.

    A shared deployment creates its schema through Alembic and refuses to start when
    it is not at head (`auto_create_schema=False`). A local/test instance builds its
    schema from the SQLAlchemy metadata, where no `alembic_version` row exists: the
    probe then reports `unknown`, which is the truth, and does not fail readiness —
    unless the deployment is staging/production-like, where an unversioned schema is
    precisely the drift this probe exists to catch.
    """

    def run() -> tuple[bool, str]:
        from alembic.runtime.migration import MigrationContext

        from app.core.database import SessionLocal, _expected_alembic_revision

        expected = _expected_alembic_revision()
        with SessionLocal() as db:
            current = MigrationContext.configure(db.connection()).get_current_revision()
        if current is None:
            if settings.auto_create_schema:
                return True, f"schéma local créé par SQLAlchemy (aucune révision Alembic ; tête attendue {expected})"
            return False, f"aucune révision Alembic appliquée (tête attendue {expected})"
        if current != expected:
            return False, f"révision appliquée {current} ≠ tête {expected}"
        return True, f"révision {current} = tête"

    return _timed("migrations", required=True, call=run)


def probe_object_storage(settings: Settings, storage: object) -> ProbeResult:
    """HEAD a real bucket, through the same adapter the application uses."""
    configured = getattr(settings, "document_storage_backend", "disabled") != "disabled"
    if not configured:
        return _disabled(
            "document_storage",
            "stockage désactivé par configuration : les documents ne peuvent pas être importés",
        )

    def run() -> tuple[bool, str]:
        probe = getattr(storage, "probe", None)
        if probe is None:
            # Fail closed: an adapter that cannot be probed is not a verified dependency.
            return False, "adaptateur de stockage sans sonde (probe) : indisponible, donc non vérifiable"
        return probe(bucket=settings.document_quarantine_bucket)

    # Required as soon as it is *configured*: a deployment that points at a bucket
    # and cannot reach it is not able to do its job, whatever the environment says.
    return _timed(
        "document_storage", required=configured or settings.is_production_like, call=run
    )


def probe_malware_scanner(settings: Settings, scanner: object) -> ProbeResult:
    """PING the antivirus; a documents pipeline that cannot scan must not accept files."""
    configured = getattr(settings, "document_scanner_mode", "disabled") != "disabled"
    if not configured:
        return _disabled(
            "malware_scanner",
            "antivirus désactivé par configuration : aucun document ne peut être importé",
        )

    def run() -> tuple[bool, str]:
        probe = getattr(scanner, "probe", None)
        if probe is None:
            return False, "scanner sans sonde (probe) : indisponible, donc non vérifiable"
        return probe()

    return _timed(
        "malware_scanner", required=configured or settings.is_production_like, call=run
    )


def build_readiness_report(settings: Settings, *, storage: object, scanner: object) -> ReadinessReport:
    checks = [
        probe_database(),
        probe_migrations(settings),
        probe_object_storage(settings, storage),
        probe_malware_scanner(settings, scanner),
    ]
    # "disabled" is acceptable only for a dependency that is not required in this
    # configuration; a required dependency must answer `ok`.
    ready = all(
        check.status == "ok" for check in checks if check.required
    )
    capabilities = {
        "document_storage": next(c for c in checks if c.name == "document_storage").status == "ok",
        "malware_scanning": next(c for c in checks if c.name == "malware_scanner").status == "ok",
        "database": next(c for c in checks if c.name == "database").status == "ok",
    }
    return ReadinessReport(
        ready=ready, checks=checks, capabilities=capabilities, environment=settings.environment
    )
