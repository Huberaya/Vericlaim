from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    app_name: str = "VeriClaim AI — Regulatory Rule Engine"
    environment: str = "development"
    database_url: str = "sqlite:///./vericlaim.db"
    cors_origins: tuple[str, ...] = ("http://localhost:3000",)
    eu_2024_825_fr_transposition_status: str = "unknown"
    verified_certificates_json: str = "{}"
    tesseract_languages: str = "fra+eng"
    max_upload_bytes: int = 15 * 1024 * 1024
    max_pdf_pages: int = 25
    max_source_chars: int = 100_000
    clerk_jwks_url: str = "https://meet-asp-6564.clerk.accounts.dev/.well-known/jwks.json"
    clerk_secret_key: str | None = None


def _normalize_database_url(raw_url: str) -> str:
    url = raw_url.strip()
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://") and not url.startswith("postgresql+"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def get_settings() -> Settings:
    origins = tuple(
        origin.strip()
        for origin in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
        if origin.strip()
    )
    status = os.getenv("EU_2024_825_FR_TRANSPOSITION_STATUS", "unknown").strip().lower()
    if status not in {"unknown", "implemented", "not_implemented"}:
        status = "unknown"
    try:
        max_upload_bytes = int(os.getenv("MAX_UPLOAD_BYTES", str(15 * 1024 * 1024)))
    except ValueError:
        max_upload_bytes = 15 * 1024 * 1024
    try:
        max_pdf_pages = int(os.getenv("MAX_PDF_PAGES", "25"))
    except ValueError:
        max_pdf_pages = 25

    clerk_jwks = os.getenv(
        "CLERK_JWKS_URL",
        "https://meet-asp-6564.clerk.accounts.dev/.well-known/jwks.json",
    )
    clerk_secret = os.getenv("CLERK_SECRET_KEY", "sk_test_XLUXm9CDUAcqGqU8eRw9NXhJYmlRGXKPcE7YchwkkV")

    raw_db_url = os.getenv("DATABASE_URL", "sqlite:///./vericlaim.db")
    return Settings(
        app_name=os.getenv("APP_NAME", "VeriClaim AI — Regulatory Rule Engine"),
        environment=os.getenv("APP_ENV", "development"),
        database_url=_normalize_database_url(raw_db_url),
        cors_origins=origins,
        eu_2024_825_fr_transposition_status=status,
        verified_certificates_json=os.getenv("VERICLAIM_CERTIFICATE_REGISTRY_JSON", "{}"),
        tesseract_languages=os.getenv("TESSERACT_LANGUAGES", "fra+eng"),
        max_upload_bytes=max(1024, max_upload_bytes),
        max_pdf_pages=max(1, max_pdf_pages),
        max_source_chars=max(1000, int(os.getenv("MAX_SOURCE_CHARS", "100000"))),
        clerk_jwks_url=clerk_jwks,
        clerk_secret_key=clerk_secret,
    )


settings = get_settings()
