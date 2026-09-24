from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.api.v1.endpoints import router as engine_router
from app.core.config import settings
from app.core.database import create_tables
from app.core.limiter import limiter
from app.engine.ecolabel_connector import LiveEcolabelConnector
from app.engine.inference_evaluator import InferenceEvaluator
from app.engine.proof_validator import JsonCertificateRegistry, ProofValidator


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Injecte un identifiant unique de corrélation X-Request-ID sur chaque requête et réponse."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables()
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
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

app.state.settings = settings
ecolabel_connector = LiveEcolabelConnector()
if settings.verified_certificates_json and settings.verified_certificates_json != "{}":
    json_reg = JsonCertificateRegistry.from_json(settings.verified_certificates_json)
    for rec in json_reg._records.values():
        ecolabel_connector.register(rec)

app.state.ecolabel_connector = ecolabel_connector
app.state.evaluator = InferenceEvaluator(
    ProofValidator(ecolabel_connector),
    fr_2024_825_transposition_status=settings.eu_2024_825_fr_transposition_status,
)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
    )

app.add_middleware(RequestIDMiddleware)
app.include_router(engine_router)


@app.get("/metrics", tags=["monitoring"])
def prometheus_metrics() -> Response:
    """Expose les métriques Prometheus du moteur réglementaire."""
    from app.core.monitoring import metrics_response
    return metrics_response()


@app.get("/healthz", tags=["health"])
def healthz() -> dict[str, Any]:
    """Bilan de santé détaillé (statut global, base de données, moteur, registres)."""
    from datetime import datetime, timezone
    from sqlalchemy import text
    from app.core.database import SessionLocal
    from app.engine.rule_book import RULEBOOK_VERSION, RULES

    db_status = "ok"
    db_error = None
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
    except Exception as exc:
        db_status = "error"
        db_error = str(exc)

    overall_status = "healthy" if db_status == "ok" else "unhealthy"

    return {
        "status": overall_status,
        "service": settings.app_name,
        "version": "0.1.0",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "database": {
            "status": db_status,
            "error": db_error,
        },
        "engine": {
            "status": "ready",
            "rulebook_version": RULEBOOK_VERSION,
            "loaded_rules_count": len(RULES),
            "fr_transposition_status": app.state.evaluator.fr_2024_825_transposition_status,
        },
        "ecolabel_connector": {
            "status": "connected",
            "registries_count": len(app.state.ecolabel_connector.list_registries()),
        },
    }


@app.get("/livez", tags=["health"])
def livez() -> dict[str, str]:
    """Sonde de vivacité Kubernetes (Liveness probe)."""
    return {"status": "alive"}


@app.get("/readyz", tags=["health"])
def readyz() -> Response:
    """Sonde de préparation Kubernetes (Readiness probe)."""
    from sqlalchemy import text
    from app.core.database import SessionLocal
    from starlette.responses import JSONResponse

    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        return JSONResponse({"status": "ready"}, status_code=200)
    except Exception as exc:
        return JSONResponse({"status": "unready", "error": str(exc)}, status_code=503)


@app.get("/", tags=["health"])
def index() -> dict[str, str]:
    return {
        "service": settings.app_name,
        "docs": "/docs",
        "evaluate": "/api/v1/engine/evaluate",
        "rulebook": "/api/v1/engine/rules",
    }
