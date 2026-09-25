from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.analyses import claims_router
from app.api.v1.analyses import router as analyses_router
from app.api.v1.auth import router as auth_router
from app.api.v1.catalog import product_router, supplier_router
from app.api.v1.documents import router as documents_router
from app.api.v1.documents import upload_router as document_upload_router
from app.api.v1.documents import version_router as document_version_router
from app.api.v1.endpoints import router as engine_router
from app.api.v1.organizations import router as organizations_router
from app.core.config import settings
from app.core.database import SessionLocal, create_tables
from app.documents.scanner import build_malware_scanner
from app.documents.storage import build_object_storage
from app.engine.inference_evaluator import InferenceEvaluator
from app.engine.proof_validator import JsonCertificateRegistry, ProofValidator
from app.identity.oidc import OidcClient
from app.identity.roles import ensure_system_roles


@asynccontextmanager
async def lifespan(app: FastAPI):
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
app.include_router(engine_router)


@app.get("/healthz", tags=["health"])
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "vericlaim-rule-engine"}


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
