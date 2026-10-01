from __future__ import annotations

import json
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.analyses import claims_router
from app.api.v1.analyses import router as analyses_router
from app.api.v1.audit import router as audit_router
from app.api.v1.auth import dev_router as auth_dev_router
from app.api.v1.auth import router as auth_router
from app.api.v1.catalog import product_router, supplier_router
from app.api.v1.documents import router as documents_router
from app.api.v1.documents import upload_router as document_upload_router
from app.api.v1.documents import version_router as document_version_router
from app.api.v1.endpoints import router as engine_router
from app.api.v1.enterprise import router as enterprise_router
from app.api.v1.evidence import (
    analyses_evidence_router,
    claims_evidence_router,
    evidence_links_router,
    router as evidence_router,
)
from app.api.v1.ops import router as ops_router
from app.api.v1.internal_workers import router as internal_workers_router
from app.api.v1.organizations import router as organizations_router
from app.api.v1.pilot import router as pilot_router
from app.api.v1.privacy import router as privacy_router
from app.api.v1.regulatory import router as regulatory_router
from app.api.v1.data_rights import public_router as data_rights_public_router
from app.api.v1.data_rights import router as data_rights_router
from app.api.v1.support import public_router as support_public_router
from app.api.v1.support import router as support_router
from app.api.v1.reports import public_router as reports_public_router
from app.api.v1.billing import local_dev_router as billing_local_dev_router
from app.api.v1.billing import router as billing_router
from app.api.v1.reports import router as reports_router
from app.api.v1.auth_self_service import router as auth_self_service_router
from app.api.v1.review import (
    analysis_validations_router,
    evidence_requests_router,
    validations_router,
)
from app.core.config import settings
from app.billing.enforcement import (
    EntitlementNotIncludedError,
    QuotaExceededError,
    SubscriptionInactiveError,
)
from app.core.database import DatabaseUnavailableError, SessionLocal, create_tables
from app.core.logging import bind_log_context, configure_logging, new_request_id, normalize_request_id
from app.core.metrics import record_http_request, record_worker_heartbeat, render_metrics
from app.core.readiness import build_readiness_report
from app.documents.scanner import build_malware_scanner
from app.documents.storage import build_object_storage
from app.engine.inference_evaluator import InferenceEvaluator
from app.engine.proof_validator import JsonCertificateRegistry, ProofValidator
from app.identity.oidc import OidcClient
from app.identity.roles import ensure_system_roles

configure_logging()

logger = logging.getLogger("vericlaim.startup")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "VeriClaim runtime resolved: %s",
        json.dumps(settings.describe_runtime(), sort_keys=True),
        extra={"event": "startup_runtime", **settings.describe_runtime()},
    )
    if settings.enable_dev_login:
        logger.warning(
            "The credential-less pilot login route is ENABLED (POST /api/v1/auth/dev-login). "
            "This is intended for local work only and is impossible outside development/test.",
            extra={"event": "dev_login_enabled", "environment": settings.environment},
        )
    if settings.allow_local_sqlite_fallback:
        logger.warning(
            "ALLOW_LOCAL_SQLITE_FALLBACK is enabled: an unreachable database will be replaced by a "
            "throwaway SQLite file. Row-level security does not apply to that fallback.",
            extra={"event": "local_sqlite_fallback_enabled"},
        )
    # The API itself is a process that must be visible in the observability surface:
    # without this, `workers_status` had nothing to report and claimed "active"
    # whenever it felt like it.
    record_worker_heartbeat(worker_kind="api", worker_id=f"pid-{os.getpid()}")
    try:
        create_tables()
        # Alembic seeds shared databases; this keeps explicit local/test schemas
        # equivalent without overwriting existing role permissions.
        with SessionLocal() as db:
            ensure_system_roles(db)
            db.commit()
    except Exception as exc:
        if settings.is_production_like:
            raise
        import logging
        logging.getLogger("uvicorn.error").warning("Database startup initialization deferred/failed: %s", exc)
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description=(
        "Moteur déterministe de détection, qualification et contrôle probatoire des allégations environnementales. "
        "Outil d'aide à la conformité, sans substitution à une revue juridique humaine."
    ),
    lifespan=lifespan,
)
app.state.settings = settings
# Discovery and token exchange are deferred to login; startup never makes a
# network call to an identity provider.
app.state.oidc_client = OidcClient(settings) if settings.oidc_configured else None
# The storage/scanner boundaries fail closed when disabled. Test code may inject
# deterministic fakes; production configuration requires S3-compatible storage
# and ClamAV before the application starts.
app.state.document_storage = build_object_storage(settings)
app.state.document_scanner = build_malware_scanner(settings)
certificate_registry = JsonCertificateRegistry.from_json(settings.verified_certificates_json)
app.state.evaluator = InferenceEvaluator(
    ProofValidator(certificate_registry),
    fr_2024_825_transposition_status=settings.eu_2024_825_fr_transposition_status,
)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        # Cookie-authenticated mutations use this header for the double-submit
        # CSRF check. It must be admitted explicitly for browser preflights.
        allow_headers=["Content-Type", "Authorization", "X-CSRF-Token", "X-Request-Id", "Idempotency-Key"],
    )

@app.middleware("http")
async def correlation_and_metrics(request: Request, call_next):
    """Attach a correlation id to every log line, and measure every request (C16).

    The client may propose `X-Request-Id`; it is accepted only if it matches a strict
    pattern, so a header cannot forge or split a log line. The identifier is echoed in
    the response, which is what makes a support conversation possible: the customer
    reads it back, and every log line of that request can be retrieved.

    `/metrics` itself is excluded from its own counters: a scrape every 15 seconds
    would otherwise dominate the request rate and quietly distort the error rate.
    """
    request_id = normalize_request_id(request.headers.get("X-Request-Id")) or new_request_id()
    bind_log_context(request_id=request_id)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        duration = time.perf_counter() - started
        if request.url.path != "/metrics":
            record_http_request(
                method=request.method,
                template=_route_template(request),
                status_code=500,
                duration_seconds=duration,
            )
        raise
    duration = time.perf_counter() - started
    response.headers["X-Request-Id"] = request_id
    if request.url.path != "/metrics":
        record_http_request(
            method=request.method,
            template=_route_template(request),
            status_code=response.status_code,
            duration_seconds=duration,
        )
    return response


def _route_template(request: Request) -> str:
    """Route pattern, never the raw URL: an identifier per request would explode cardinality."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return str(path) if path else "<inconnue>"


app.include_router(auth_router)
# The credential-less pilot route is omitted entirely unless explicitly allowed
# and is never allowed in staging/production (enforced in Settings).
if settings.enable_dev_login:
    app.include_router(auth_dev_router)
app.include_router(organizations_router)
app.include_router(supplier_router)
app.include_router(product_router)
app.include_router(documents_router)
app.include_router(document_upload_router)
app.include_router(document_version_router)
app.include_router(analyses_router)
app.include_router(claims_router)
app.include_router(evidence_router)
app.include_router(evidence_links_router)
app.include_router(claims_evidence_router)
app.include_router(analyses_evidence_router)
app.include_router(validations_router)
app.include_router(analysis_validations_router)
app.include_router(evidence_requests_router)
app.include_router(regulatory_router)
app.include_router(pilot_router)
app.include_router(privacy_router)
app.include_router(enterprise_router)
app.include_router(data_rights_router)
app.include_router(data_rights_public_router)
app.include_router(support_router)
app.include_router(support_public_router)
app.include_router(reports_router)
app.include_router(auth_self_service_router)
# Public verification: a third party checking a report has no VeriClaim account.
app.include_router(reports_public_router)
app.include_router(audit_router)
app.include_router(engine_router)
app.include_router(ops_router)
app.include_router(internal_workers_router)
app.include_router(billing_router)

# C13 — paiement simulé : la route n'est enregistrée que si le prestataire
# configuré est celui de développement **et** que l'environnement n'est pas de
# production. Deux conditions, comme pour la connexion de développement de C1 :
# une seule couche ne serait pas un contrôle.
if settings.billing_provider == "local" and not settings.is_production_like:
    app.include_router(billing_local_dev_router)


@app.exception_handler(QuotaExceededError)
async def quota_exceeded_handler(request: Request, exc: QuotaExceededError) -> JSONResponse:
    """Quota atteint : une erreur explicite, jamais une facture surprise.

    402 Payment Required est le seul code qui dit les deux choses à la fois : il
    faut décider (payer, attendre, ou changer d'offre) et rien n'a été facturé.
    """

    logger.info(
        "Import refusé pour quota atteint",
        extra={
            "event": "billing_quota_exceeded",
            "metric": exc.check.metric.value,
            "used": exc.check.used,
            "limit": exc.check.limit,
            "path": request.url.path,
        },
    )
    return JSONResponse(status_code=402, content={"detail": exc.detail})


@app.exception_handler(SubscriptionInactiveError)
async def subscription_inactive_handler(
    request: Request, exc: SubscriptionInactiveError
) -> JSONResponse:
    logger.info(
        "Action refusée : abonnement inactif",
        extra={
            "event": "billing_subscription_inactive",
            "status": exc.entitlement.status,
            "path": request.url.path,
        },
    )
    return JSONResponse(status_code=402, content={"detail": exc.detail})


@app.exception_handler(EntitlementNotIncludedError)
async def entitlement_not_included_handler(
    request: Request, exc: EntitlementNotIncludedError
) -> JSONResponse:
    logger.info(
        "Action refusée : droit absent de l'offre",
        extra={
            "event": "billing_entitlement_not_included",
            "entitlement": exc.entitlement_code,
            "path": request.url.path,
        },
    )
    return JSONResponse(status_code=402, content={"detail": exc.detail})


@app.exception_handler(DatabaseUnavailableError)
async def database_unavailable_handler(request: Request, exc: DatabaseUnavailableError) -> JSONResponse:
    """Fail closed with an explicit 503 instead of serving an empty database."""
    logger.error(
        "Request refused: database unavailable",
        extra={"event": "request_database_unavailable", "path": request.url.path},
    )
    return JSONResponse(
        status_code=503,
        content={
            "detail": str(exc),
            "code": "database_unavailable",
        },
    )


@app.get("/metrics", tags=["health"], include_in_schema=False)
def metrics(response: Response) -> Response:
    """Prometheus exposition. Unauthenticated by design — see app/api/v1/ops.py.

    Contains no organization identifier, no document title, no user address: only
    counters, durations and queue depths. A test asserts that property, because it is
    what makes an unauthenticated scrape acceptable.
    """
    payload = render_metrics()
    return Response(
        content=payload,
        media_type="text/plain; version=0.0.4; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/healthz", tags=["health"])
def healthz() -> dict[str, str]:
    """Liveness only: the process answers, nothing else is claimed.

    Deliberately dependency-free. A process whose database is unreachable must be
    removed from rotation by /readyz, not restarted in a loop by a liveness probe
    that fails for a reason a restart cannot fix.
    """
    return {"status": "alive", "service": "vericlaim-rule-engine"}


@app.get("/readyz", tags=["health"])
def readyz(response: Response) -> dict[str, object]:
    """Readiness: every dependency required by this configuration is really reachable.

    Returns 503 with the failing check names when it is not. The payload carries
    statuses, latencies and exception *classes* — never endpoints, buckets or DSNs,
    which would turn a public probe into a reconnaissance endpoint.
    """
    report = build_readiness_report(
        settings,
        storage=getattr(app.state, "document_storage", None),
        scanner=getattr(app.state, "document_scanner", None),
    )
    payload = report.as_dict()
    if not report.ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        logger.error(
            "Readiness probe failed",
            extra={
                "event": "readiness_failed",
                "failed": payload["failed"],
                "checks": payload["checks"],
            },
        )
    return payload


@app.get("/", tags=["health"])
def index() -> dict[str, str]:
    return {
        "service": settings.app_name,
        "docs": "/docs",
        "authentication": "/api/v1/auth/status",
        "organizations": "/api/v1/organizations",
        "suppliers": "/api/v1/suppliers (membre authentifié requis)",
        "products": "/api/v1/products (membre authentifié requis)",
        "documents": "/api/v1/documents (membre authentifié requis)",
        "analyses": "/api/v1/analyses (membre authentifié requis)",
        "evaluate": "/api/v1/engine/evaluate (membre authentifié requis)",
        "rulebook": "/api/v1/engine/rules (membre authentifié requis)",
    }
