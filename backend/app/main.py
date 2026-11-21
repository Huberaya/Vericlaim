from __future__ import annotations

import hmac
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

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
)
from app.api.v1.evidence import (
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
from app.core.config import settings, settings_of
from app.core.database import DatabaseUnavailableError, SessionLocal, create_tables, probe_database
from app.core.metrics import REGISTRY
from app.core.metrics_middleware import MetricsMiddleware
from app.core.rate_limit import RateLimitMiddleware
from app.core.security_headers import DocsLockdownMiddleware, SecurityHeadersMiddleware
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
    # E-05 : la documentation interactive expose le schéma complet de l'API.
    # Elle est refusée par défaut en staging/production ; API_DOCS_ENABLED=true
    # est l'ouverture explicite et assumée (transparence documentée).
    docs_url="/docs" if settings.api_docs_enabled else None,
    redoc_url="/redoc" if settings.api_docs_enabled else None,
    openapi_url="/openapi.json" if settings.api_docs_enabled else None,
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

# Chaîne de middleware (le plus externe ajouté en dernier) :
# SecurityHeaders → Metrics → RateLimit → DocsLockdown → CORS → routes.
#
# - SecurityHeaders (E-04) est le plus externe : chaque réponse — y compris les
#   429 du limiteur, les 503 d'infrastructure et les pré-vols CORS — porte les
#   en-têtes de sécurité ;
# - Metrics (A-09) mesure le trafic réel, pré-vols CORS compris : c'était
#   l'intention documentée, que l'ordre précédent (CORS ajouté après Metrics)
#   ne satisfaisait pas réellement — CORS répond aux OPTIONS sans enchaîner ;
# - RateLimit (E-02) borne les mutations et l'évaluation avant les routes ;
# - DocsLockdown (E-05) ferme /docs et /openapi.json selon la configuration ;
# - CORS reste le plus interne : les en-têtes de pré-vol ne sont ajoutés qu'aux
#   réponses qui concernent le navigateur.
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        # Cookie-authenticated mutations use this header for the double-submit
        # CSRF check. It must be admitted explicitly for browser preflights.
        allow_headers=["Content-Type", "Authorization", "X-CSRF-Token", "X-Request-Id", "Idempotency-Key", "X-API-Key"],
    )
app.add_middleware(DocsLockdownMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(MetricsMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

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

    # A-07 : « configuré » ne suffisait pas. La sonde interroge réellement le
    # moteur (PING clamd) pour que la readiness reflète la capacité de scanner,
    # et non la simple présence d'une configuration. Un échec rend l'import
    # « refused » : c'est le même fail-closed que celui appliqué à la
    # complétion, rendu visible avant même qu'un fichier soit déposé.
    # Valeurs conservées pour ne pas casser les runbooks et les consommateurs
    # existants (« configured »/« disabled ») ; l'état réel de la sonde est
    # exposé séparément dans ``malware_scanner_reachable``. Un opérateur qui ne
    # lit qu'un champ garde donc son ancienne sémantique, et celui qui veut
    # savoir si le moteur répond a la réponse.
    scanner_state = "disabled" if isinstance(scanner, DisabledMalwareScanner) else "configured"
    scanner_reason = scanner.healthcheck() if scanner_state == "configured" else "scanner_disabled"
    scanner_reachable = scanner_state == "configured" and scanner_reason is None

    database_ready = database == "ok"
    import_ready = storage_state == "configured" and scanner_reachable
    # La base conditionne la readiness du processus ; le moteur antivirus
    # conditionne la seule importation documentaire. Les confondre ferait
    # redémarrer toute l'API pour un clamd momentanément indisponible, alors
    # que le refus correct est local à l'import.
    ready = database_ready
    payload = {
        "status": "ready" if ready else "unavailable",
        "database": database,
        "document_storage": storage_state,
        "malware_scanner": scanner_state,
        "malware_scanner_reachable": scanner_reachable,
        "malware_scanner_reason": scanner_reason,
        "document_import": "enabled" if import_ready else "refused",
    }
    return JSONResponse(
        status_code=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
        content=payload,
        headers={"Cache-Control": "no-store"},
    )



@app.get("/metrics", tags=["health"], include_in_schema=False)
def metrics(request: Request) -> Response:
    """Export Prometheus (A-09).

    E-05 : l'export expose la profondeur des files et l'état des dépendances.
    En staging/production, un jeton de scrape est **obligatoire** (config
    ``METRICS_AUTH_TOKEN``) : la route refuse toute requête qui ne le porte
    pas. En développement/test sans jeton configuré, la route reste ouverte.

    La profondeur des files est lue en base : elle reste exacte même quand
    aucun worker ne tourne, ce qui est précisément le moment où l'information
    compte. Une base injoignable ne fait pas échouer la route : les métriques
    déjà connues sont servies, et la sonde de base porte l'information.
    """

    expected_token = settings_of(getattr(request, "app", None), fallback=settings).metrics_auth_token
    if expected_token:
        authorization = request.headers.get("Authorization") or ""
        scheme, _, value = authorization.partition(" ")
        provided = value.strip() if scheme.lower() == "bearer" else ""
        if not hmac.compare_digest(provided, expected_token):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Jeton de supervision requis."},
                headers={"Cache-Control": "no-store"},
            )

    queue_metrics: dict[str, object] = {}
    database_ok = probe_database()
    if database_ok:
        try:
            from app.core.database import SessionLocal
            from app.core.queue_metrics import collect_queue_metrics

            with SessionLocal() as db:
                queue_metrics = collect_queue_metrics(db)
        except Exception:  # noqa: BLE001 - l'export ne doit pas tomber avec la base
            queue_metrics = {}

    scanner = request.app.state.document_scanner
    try:
        reachable = scanner.healthcheck() is None
        REGISTRY.observe_scanner(reachable)
    except Exception:  # noqa: BLE001 - une sonde n'est pas une panne
        reachable = False

    extra = {
        "database_up": 1 if database_ok else 0,
        "scanner_reachable": 1 if reachable else 0,
    }
    extra.update({key: value for key, value in queue_metrics.items()})
    body = REGISTRY.render(extra)
    return Response(content=body, media_type="text/plain; version=0.0.4; charset=utf-8")


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
