from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.analyses import claims_router
from app.api.v1.analyses import router as analyses_router
from app.api.v1.audit import router as audit_router
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
from app.api.v1.organizations import router as organizations_router
from app.api.v1.pilot import router as pilot_router
from app.api.v1.regulatory import router as regulatory_router
from app.api.v1.reports import router as reports_router
from app.api.v1.review import (
    analysis_validations_router,
    evidence_requests_router,
    validations_router,
)
from app.core.config import settings
from app.core.database import DatabaseUnavailableError, SessionLocal, create_tables, probe_database
from app.documents.scanner import DisabledMalwareScanner, build_malware_scanner
from app.documents.storage import DisabledObjectStorage, build_object_storage
from app.engine.inference_evaluator import InferenceEvaluator
from app.engine.proof_validator import JsonCertificateRegistry, ProofValidator
from app.identity.oidc import OidcClient
from app.identity.roles import ensure_system_roles


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ADR-003: the schema is a deployment precondition, not a runtime
    # best-effort. Swallowing this failure used to let the API advertise a
    # healthy process against an absent or un-migrated database, whose requests
    # were then transparently served by an on-request SQLite fallback. That
    # fallback is removed: every environment fails visibly here, and Compose
    # plus the platform restart policies own the retry.
    create_tables()
    # Alembic seeds shared databases; this keeps explicit local/test schemas
    # equivalent without overwriting existing role permissions.
    with SessionLocal() as db:
        ensure_system_roles(db)
        db.commit()
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

app.include_router(auth_router)
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
app.include_router(enterprise_router)
app.include_router(reports_router)
app.include_router(audit_router)
app.include_router(engine_router)


@app.exception_handler(DatabaseUnavailableError)
def database_unavailable_handler(request: Request, exc: DatabaseUnavailableError) -> JSONResponse:
    """Map an infrastructure outage to a tenant-safe 503, never a 2xx.

    The response carries no driver text, path or credential: the underlying
    exception is logged server-side by the ASGI error handler instead.
    """
    del request, exc
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "Le service est temporairement indisponible; réessayez plus tard."},
        headers={"Cache-Control": "no-store"},
    )


@app.get("/healthz", tags=["health"])
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "vericlaim-rule-engine"}


@app.get("/readyz", tags=["health"])
def readyz(request: Request) -> JSONResponse:
    """Readiness for the document data plane: no self-report without a probe.

    ``/healthz`` only proves the process answers. Compose gates both workers on
    the API being *healthy*, and the README documents that as meaning the
    migrations were applied, so the healthcheck must actually reach
    PostgreSQL and the readiness view must expose whether the two mandatory
    document-safety dependencies are wired at all. No endpoint, bucket name or
    credential is ever returned.
    """
    database = "ok" if probe_database() else "unavailable"

    storage = request.app.state.document_storage
    scanner = request.app.state.document_scanner
    # Both boundaries fail closed in the application code; the readiness view
    # only reports whether a deployment has actually wired them, so an
    # operator can see why `/documents` imports are refused before testing one.
    storage_state = "disabled" if isinstance(storage, DisabledObjectStorage) else "configured"
    scanner_state = "disabled" if isinstance(scanner, DisabledMalwareScanner) else "configured"
    ready = database == "ok"
    payload = {
        "status": "ready" if ready else "unavailable",
        "database": database,
        "document_storage": storage_state,
        "malware_scanner": scanner_state,
        "document_import": "enabled" if storage_state == "configured" and scanner_state == "configured" else "refused",
    }
    return JSONResponse(
        status_code=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
        content=payload,
        headers={"Cache-Control": "no-store"},
    )



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
