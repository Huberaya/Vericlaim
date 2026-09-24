from __future__ import annotations

import os
from dataclasses import dataclass


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

    return Settings(
        app_name=os.getenv("APP_NAME", "VeriClaim AI — Regulatory Rule Engine"),
        environment=os.getenv("APP_ENV", "development"),
        database_url=os.getenv("DATABASE_URL", "sqlite:///./vericlaim.db"),
        cors_origins=origins,
        eu_2024_825_fr_transposition_status=status,
        verified_certificates_json=os.getenv("VERICLAIM_CERTIFICATE_REGISTRY_JSON", "{}"),
        tesseract_languages=os.getenv("TESSERACT_LANGUAGES", "fra+eng"),
        max_upload_bytes=max(1024, max_upload_bytes),
        max_pdf_pages=max(1, max_pdf_pages),
        max_source_chars=max(1000, int(os.getenv("MAX_SOURCE_CHARS", "100000"))),
    )


settings = get_settings()
