from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.api.v1.endpoints import router as engine_router
from app.core.config import settings
from app.core.database import create_tables
from app.core.limiter import limiter
from app.engine.ecolabel_connector import LiveEcolabelConnector
from app.engine.inference_evaluator import InferenceEvaluator
from app.engine.proof_validator import JsonCertificateRegistry, ProofValidator


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
        allow_headers=["Content-Type", "Authorization"],
    )

app.include_router(engine_router)


@app.get("/healthz", tags=["health"])
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "vericlaim-rule-engine"}


@app.get("/", tags=["health"])
def index() -> dict[str, str]:
    return {
        "service": settings.app_name,
        "docs": "/docs",
        "evaluate": "/api/v1/engine/evaluate",
        "rulebook": "/api/v1/engine/rules",
    }
