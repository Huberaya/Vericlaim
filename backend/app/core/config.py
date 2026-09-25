from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse


PRODUCTION_LIKE_ENVIRONMENTS = frozenset({"staging", "production"})
KNOWN_ENVIRONMENTS = frozenset({"development", "test", *PRODUCTION_LIKE_ENVIRONMENTS})
DEV_SESSION_SECRET = "development-only-session-secret-change-before-shared-use"
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
MAX_DOCUMENT_UPLOAD_TTL_SECONDS = 60 * 60
MAX_DOCUMENT_DOWNLOAD_TTL_SECONDS = 15 * 60
MAX_DOCUMENT_EXTRACTION_ATTEMPTS = 10
MAX_DOCUMENT_EXTRACTION_LEASE_SECONDS = 60 * 60
MAX_DOCUMENT_OCR_TIMEOUT_SECONDS = 60
MAX_DOCUMENT_IMAGE_PIXELS = 80_000_000
MAX_ANALYSIS_DETECTION_ATTEMPTS = 10
MAX_ANALYSIS_DETECTION_LEASE_SECONDS = 15 * 60


@dataclass(frozen=True)
class Settings:
    app_name: str = "VeriClaim AI — Regulatory Rule Engine"
    environment: str = "development"
    database_url: str = "sqlite:///./vericlaim.db"
    auto_create_schema: bool = True
    cors_origins: tuple[str, ...] = ("http://localhost:3000",)
    frontend_url: str = "http://localhost:3000"
    eu_2024_825_fr_transposition_status: str = "unknown"
    verified_certificates_json: str = "{}"
    tesseract_languages: str = "fra+eng"
    max_upload_bytes: int = 15 * 1024 * 1024
    max_pdf_pages: int = 25
    max_source_chars: int = 100_000
    document_extraction_max_attempts: int = 3
    document_extraction_retry_base_seconds: int = 30
    document_extraction_lease_seconds: int = 15 * 60
    document_extraction_poll_seconds: int = 2
    document_ocr_timeout_seconds: int = 20
    document_ocr_render_scale: int = 2
    document_max_image_pixels: int = 40_000_000
    document_segment_max_chars: int = 2_000
    analysis_detection_max_attempts: int = 3
    analysis_detection_retry_base_seconds: int = 30
    analysis_detection_lease_seconds: int = 5 * 60
    analysis_detection_poll_seconds: int = 2
    document_storage_backend: str = "disabled"
    # ``endpoint`` is used by the API container. ``public_endpoint`` is used
    # only while signing browser URLs and can differ in Compose (minio vs
    # localhost) without ever exposing an internal storage hostname.
    document_storage_endpoint: str | None = None
    document_storage_public_endpoint: str | None = None
    document_storage_region: str = "eu-west-3"
    document_storage_access_key: str | None = None
    document_storage_secret_key: str | None = None
    document_quarantine_bucket: str = "vericlaim-quarantine"
    document_clean_bucket: str = "vericlaim-documents"
    document_storage_sse_mode: str = "none"
    document_storage_sse_kms_key_id: str | None = None
    document_upload_ttl_seconds: int = 15 * 60
    document_download_ttl_seconds: int = 5 * 60
    document_scanner_mode: str = "disabled"
    document_clamav_host: str | None = None
    document_clamav_port: int = 3310
    document_clamav_timeout_seconds: int = 30
    auth_session_secret: str = DEV_SESSION_SECRET
    auth_session_ttl_seconds: int = 8 * 60 * 60
    auth_cookie_secure: bool = False
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_redirect_uri: str | None = None
    oidc_discovery_url: str | None = None

    @property
    def oidc_configured(self) -> bool:
        return bool(self.oidc_issuer and self.oidc_client_id and self.oidc_redirect_uri and self.oidc_discovery_url)

    @property
    def is_production_like(self) -> bool:
        return self.environment in PRODUCTION_LIKE_ENVIRONMENTS


def _bool_from_env(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _positive_int_from_env(name: str, default: int, minimum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, value)


def _optional_env(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


def _normalise_base_url(value: str) -> str:
    return value.rstrip("/")


def _validate_url(name: str, value: str, *, require_https: bool) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError(f"{name} must be an absolute http(s) URL.")
    if require_https and parsed.scheme != "https":
        raise RuntimeError(f"{name} must use HTTPS in staging/production.")
    return value


def get_settings() -> Settings:
    environment = os.getenv("APP_ENV", "development").strip().lower() or "development"
    if environment not in KNOWN_ENVIRONMENTS:
        raise RuntimeError("APP_ENV must be one of development, test, staging or production.")
    is_production_like = environment in PRODUCTION_LIKE_ENVIRONMENTS
    frontend_url = _validate_url(
        "FRONTEND_URL",
        _normalise_base_url(os.getenv("FRONTEND_URL", "http://localhost:3000").strip()),
        require_https=is_production_like,
    )
    origins = tuple(
        origin.strip()
        for origin in os.getenv("CORS_ORIGINS", frontend_url).split(",")
        if origin.strip()
    )
    if not origins or "*" in origins:
        raise RuntimeError("CORS_ORIGINS must contain explicit origins; wildcard origins are unsafe with session cookies.")
    for origin in origins:
        _validate_url("CORS_ORIGINS", origin, require_https=is_production_like)
    status = os.getenv("EU_2024_825_FR_TRANSPOSITION_STATUS", "unknown").strip().lower()
    if status not in {"unknown", "implemented", "not_implemented"}:
        status = "unknown"

    # ``create_all`` is a convenience for isolated tests and local prototypes
    # only. Production-like environments must run the reviewed Alembic chain.
    local_schema_default = environment in {"development", "test"}
    auto_create_schema = _bool_from_env(
        os.getenv("AUTO_CREATE_SCHEMA"),
        default=local_schema_default,
    )
    if environment not in {"development", "test"} and auto_create_schema:
        raise RuntimeError(
            "AUTO_CREATE_SCHEMA may only be enabled in development/test; run Alembic migrations in shared environments."
        )

    database_url = _optional_env("DATABASE_URL")
    if not database_url:
        if environment not in {"development", "test"}:
            raise RuntimeError(
                "DATABASE_URL must be configured outside development/test; "
                "refusing the SQLite development default."
            )
        database_url = "sqlite:///./vericlaim.db"
    if is_production_like and not database_url.startswith(("postgresql://", "postgresql+")):
        raise RuntimeError(
            "DATABASE_URL must use PostgreSQL in staging/production so tenant RLS is enforceable."
        )

    document_storage_backend = (_optional_env("DOCUMENT_STORAGE_BACKEND") or ("s3" if is_production_like else "disabled")).lower()
    if document_storage_backend not in {"disabled", "s3"}:
        raise RuntimeError("DOCUMENT_STORAGE_BACKEND must be disabled or s3.")
    if is_production_like and document_storage_backend != "s3":
        raise RuntimeError("DOCUMENT_STORAGE_BACKEND=s3 is required in staging/production.")
    document_storage_endpoint = _optional_env("DOCUMENT_STORAGE_ENDPOINT")
    if document_storage_endpoint:
        document_storage_endpoint = _validate_url(
            "DOCUMENT_STORAGE_ENDPOINT",
            _normalise_base_url(document_storage_endpoint),
            require_https=is_production_like,
        )
    document_storage_public_endpoint = _optional_env("DOCUMENT_STORAGE_PUBLIC_ENDPOINT")
    if document_storage_public_endpoint:
        document_storage_public_endpoint = _validate_url(
            "DOCUMENT_STORAGE_PUBLIC_ENDPOINT",
            _normalise_base_url(document_storage_public_endpoint),
            require_https=is_production_like,
        )
    # The storage adapter speaks the S3 protocol, but the selected deployment
    # infrastructure is MinIO. Requiring explicit endpoints outside local/test
    # prevents an accidental fall-back to a cloud-provider default endpoint and
    # makes the browser-facing signed URL topology reviewable.
    if is_production_like and not document_storage_endpoint:
        raise RuntimeError("DOCUMENT_STORAGE_ENDPOINT must explicitly reference the MinIO deployment in staging/production.")
    if is_production_like and not document_storage_public_endpoint:
        raise RuntimeError(
            "DOCUMENT_STORAGE_PUBLIC_ENDPOINT must explicitly reference the browser-reachable MinIO endpoint "
            "in staging/production."
        )
    document_storage_region = _optional_env("DOCUMENT_STORAGE_REGION") or "eu-west-3"
    document_storage_access_key = _optional_env("DOCUMENT_STORAGE_ACCESS_KEY")
    document_storage_secret_key = _optional_env("DOCUMENT_STORAGE_SECRET_KEY")
    if bool(document_storage_access_key) != bool(document_storage_secret_key):
        raise RuntimeError(
            "DOCUMENT_STORAGE_ACCESS_KEY and DOCUMENT_STORAGE_SECRET_KEY must be configured together "
            "(or both omitted when workload credentials are used)."
        )
    configured_quarantine_bucket = _optional_env("DOCUMENT_QUARANTINE_BUCKET")
    configured_clean_bucket = _optional_env("DOCUMENT_CLEAN_BUCKET")
    if is_production_like and (not configured_quarantine_bucket or not configured_clean_bucket):
        raise RuntimeError(
            "DOCUMENT_QUARANTINE_BUCKET and DOCUMENT_CLEAN_BUCKET must be explicitly configured in staging/production."
        )
    document_quarantine_bucket = configured_quarantine_bucket or "vericlaim-quarantine"
    document_clean_bucket = configured_clean_bucket or "vericlaim-documents"
    if document_storage_backend == "s3" and document_quarantine_bucket == document_clean_bucket:
        raise RuntimeError("DOCUMENT_QUARANTINE_BUCKET and DOCUMENT_CLEAN_BUCKET must be distinct.")
    document_storage_sse_mode = (_optional_env("DOCUMENT_STORAGE_SSE_MODE") or ("aes256" if is_production_like else "none")).lower()
    if document_storage_sse_mode not in {"none", "aes256", "aws:kms"}:
        raise RuntimeError("DOCUMENT_STORAGE_SSE_MODE must be none, aes256 or aws:kms.")
    if is_production_like and document_storage_sse_mode == "none":
        raise RuntimeError("Object storage encryption is required in staging/production.")
    document_storage_sse_kms_key_id = _optional_env("DOCUMENT_STORAGE_SSE_KMS_KEY_ID")
    if document_storage_sse_mode == "aws:kms" and not document_storage_sse_kms_key_id:
        raise RuntimeError("DOCUMENT_STORAGE_SSE_KMS_KEY_ID is required for aws:kms encryption.")
    document_upload_ttl_seconds = _positive_int_from_env("DOCUMENT_UPLOAD_TTL_SECONDS", 15 * 60, 60)
    document_download_ttl_seconds = _positive_int_from_env("DOCUMENT_DOWNLOAD_TTL_SECONDS", 5 * 60, 30)
    if document_upload_ttl_seconds > MAX_DOCUMENT_UPLOAD_TTL_SECONDS:
        raise RuntimeError(
            f"DOCUMENT_UPLOAD_TTL_SECONDS must not exceed {MAX_DOCUMENT_UPLOAD_TTL_SECONDS} seconds."
        )
    if document_download_ttl_seconds > MAX_DOCUMENT_DOWNLOAD_TTL_SECONDS:
        raise RuntimeError(
            f"DOCUMENT_DOWNLOAD_TTL_SECONDS must not exceed {MAX_DOCUMENT_DOWNLOAD_TTL_SECONDS} seconds."
        )

    document_scanner_mode = (
        _optional_env("DOCUMENT_SCANNER_MODE") or ("clamav" if is_production_like else ("test" if environment == "test" else "disabled"))
    ).lower()
    if document_scanner_mode not in {"disabled", "clamav", "test"}:
        raise RuntimeError("DOCUMENT_SCANNER_MODE must be disabled, clamav or test.")
    if document_scanner_mode == "test" and environment != "test":
        raise RuntimeError("DOCUMENT_SCANNER_MODE=test is reserved for APP_ENV=test.")
    if is_production_like and document_scanner_mode != "clamav":
        raise RuntimeError("DOCUMENT_SCANNER_MODE=clamav is required in staging/production.")
    document_clamav_host = _optional_env("DOCUMENT_CLAMAV_HOST")
    if document_scanner_mode == "clamav" and not document_clamav_host:
        raise RuntimeError("DOCUMENT_CLAMAV_HOST is required when DOCUMENT_SCANNER_MODE=clamav.")
    document_clamav_port = _positive_int_from_env("DOCUMENT_CLAMAV_PORT", 3310, 1)
    document_clamav_timeout_seconds = _positive_int_from_env("DOCUMENT_CLAMAV_TIMEOUT_SECONDS", 30, 1)

    auth_session_secret = _optional_env("AUTH_SESSION_SECRET")
    if not auth_session_secret:
        if is_production_like:
            raise RuntimeError("AUTH_SESSION_SECRET must be configured in staging/production.")
        auth_session_secret = DEV_SESSION_SECRET
    if is_production_like and (
        len(auth_session_secret) < 32 or auth_session_secret.lower().startswith(("replace-", "change-", "dev-"))
    ):
        raise RuntimeError(
            "AUTH_SESSION_SECRET must be a non-placeholder value of at least 32 characters in staging/production."
        )

    oidc_issuer = _normalise_base_url(_optional_env("OIDC_ISSUER") or "") or None
    oidc_client_id = _optional_env("OIDC_CLIENT_ID")
    oidc_client_secret = _optional_env("OIDC_CLIENT_SECRET")
    oidc_redirect_uri = _optional_env("OIDC_REDIRECT_URI")
    oidc_discovery_url = _optional_env("OIDC_DISCOVERY_URL")
    if oidc_issuer and not oidc_discovery_url:
        oidc_discovery_url = f"{oidc_issuer}/.well-known/openid-configuration"
    if oidc_issuer:
        oidc_issuer = _validate_url("OIDC_ISSUER", oidc_issuer, require_https=is_production_like)
    if oidc_discovery_url:
        oidc_discovery_url = _validate_url("OIDC_DISCOVERY_URL", oidc_discovery_url, require_https=is_production_like)
    if oidc_redirect_uri:
        oidc_redirect_uri = _validate_url("OIDC_REDIRECT_URI", oidc_redirect_uri, require_https=is_production_like)
        if urlparse(oidc_redirect_uri).hostname != urlparse(frontend_url).hostname:
            raise RuntimeError(
                "OIDC_REDIRECT_URI must use the frontend host so session and CSRF cookies remain same-origin."
            )

    configured_identity_fields = (oidc_issuer, oidc_client_id, oidc_redirect_uri, oidc_discovery_url)
    if any(configured_identity_fields) and not all(configured_identity_fields):
        raise RuntimeError(
            "OIDC_ISSUER, OIDC_CLIENT_ID and OIDC_REDIRECT_URI must be configured together "
            "(OIDC_DISCOVERY_URL is derived from OIDC_ISSUER when omitted)."
        )
    if is_production_like and not all(configured_identity_fields):
        raise RuntimeError("OIDC SSO must be configured in staging/production.")

    auth_cookie_secure = _bool_from_env(os.getenv("AUTH_COOKIE_SECURE"), default=is_production_like)
    if is_production_like and not auth_cookie_secure:
        raise RuntimeError("AUTH_COOKIE_SECURE cannot be disabled in staging/production.")

    max_upload_bytes = _positive_int_from_env("MAX_UPLOAD_BYTES", 15 * 1024 * 1024, 1024)
    if max_upload_bytes > MAX_DOCUMENT_BYTES:
        raise RuntimeError(f"MAX_UPLOAD_BYTES must not exceed {MAX_DOCUMENT_BYTES} bytes.")
    max_pdf_pages = _positive_int_from_env("MAX_PDF_PAGES", 25, 1)
    max_source_chars = _positive_int_from_env("MAX_SOURCE_CHARS", 100_000, 1000)

    document_extraction_max_attempts = _positive_int_from_env("DOCUMENT_EXTRACTION_MAX_ATTEMPTS", 3, 1)
    if document_extraction_max_attempts > MAX_DOCUMENT_EXTRACTION_ATTEMPTS:
        raise RuntimeError(
            f"DOCUMENT_EXTRACTION_MAX_ATTEMPTS must not exceed {MAX_DOCUMENT_EXTRACTION_ATTEMPTS}."
        )
    document_extraction_retry_base_seconds = _positive_int_from_env("DOCUMENT_EXTRACTION_RETRY_BASE_SECONDS", 30, 1)
    document_extraction_lease_seconds = _positive_int_from_env("DOCUMENT_EXTRACTION_LEASE_SECONDS", 15 * 60, 60)
    if document_extraction_lease_seconds > MAX_DOCUMENT_EXTRACTION_LEASE_SECONDS:
        raise RuntimeError(
            f"DOCUMENT_EXTRACTION_LEASE_SECONDS must not exceed {MAX_DOCUMENT_EXTRACTION_LEASE_SECONDS} seconds."
        )
    document_ocr_timeout_seconds = _positive_int_from_env("DOCUMENT_OCR_TIMEOUT_SECONDS", 20, 1)
    if document_ocr_timeout_seconds > MAX_DOCUMENT_OCR_TIMEOUT_SECONDS:
        raise RuntimeError(
            f"DOCUMENT_OCR_TIMEOUT_SECONDS must not exceed {MAX_DOCUMENT_OCR_TIMEOUT_SECONDS} seconds."
        )
    minimum_lease_seconds = max_pdf_pages * document_ocr_timeout_seconds + 60
    if document_extraction_lease_seconds < minimum_lease_seconds:
        raise RuntimeError(
            "DOCUMENT_EXTRACTION_LEASE_SECONDS must cover MAX_PDF_PAGES × DOCUMENT_OCR_TIMEOUT_SECONDS plus 60 seconds."
        )
    document_extraction_poll_seconds = _positive_int_from_env("DOCUMENT_EXTRACTION_POLL_SECONDS", 2, 1)
    document_ocr_render_scale = _positive_int_from_env("DOCUMENT_OCR_RENDER_SCALE", 2, 1)
    if document_ocr_render_scale > 4:
        raise RuntimeError("DOCUMENT_OCR_RENDER_SCALE must not exceed 4.")
    document_max_image_pixels = _positive_int_from_env("DOCUMENT_MAX_IMAGE_PIXELS", 40_000_000, 1)
    if document_max_image_pixels > MAX_DOCUMENT_IMAGE_PIXELS:
        raise RuntimeError(f"DOCUMENT_MAX_IMAGE_PIXELS must not exceed {MAX_DOCUMENT_IMAGE_PIXELS}.")
    document_segment_max_chars = _positive_int_from_env("DOCUMENT_SEGMENT_MAX_CHARS", 2_000, 200)
    if document_segment_max_chars > 10_000:
        raise RuntimeError("DOCUMENT_SEGMENT_MAX_CHARS must not exceed 10000.")

    analysis_detection_max_attempts = _positive_int_from_env("ANALYSIS_DETECTION_MAX_ATTEMPTS", 3, 1)
    if analysis_detection_max_attempts > MAX_ANALYSIS_DETECTION_ATTEMPTS:
        raise RuntimeError(
            f"ANALYSIS_DETECTION_MAX_ATTEMPTS must not exceed {MAX_ANALYSIS_DETECTION_ATTEMPTS}."
        )
    analysis_detection_retry_base_seconds = _positive_int_from_env("ANALYSIS_DETECTION_RETRY_BASE_SECONDS", 30, 1)
    analysis_detection_lease_seconds = _positive_int_from_env("ANALYSIS_DETECTION_LEASE_SECONDS", 5 * 60, 30)
    if analysis_detection_lease_seconds > MAX_ANALYSIS_DETECTION_LEASE_SECONDS:
        raise RuntimeError(
            f"ANALYSIS_DETECTION_LEASE_SECONDS must not exceed {MAX_ANALYSIS_DETECTION_LEASE_SECONDS} seconds."
        )
    analysis_detection_poll_seconds = _positive_int_from_env("ANALYSIS_DETECTION_POLL_SECONDS", 2, 1)

    return Settings(
        app_name=os.getenv("APP_NAME", "VeriClaim AI — Regulatory Rule Engine"),
        environment=environment,
        database_url=database_url,
        auto_create_schema=auto_create_schema,
        cors_origins=origins,
        frontend_url=frontend_url,
        eu_2024_825_fr_transposition_status=status,
        verified_certificates_json=os.getenv("VERICLAIM_CERTIFICATE_REGISTRY_JSON", "{}"),
        tesseract_languages=os.getenv("TESSERACT_LANGUAGES", "fra+eng"),
        max_upload_bytes=max_upload_bytes,
        max_pdf_pages=max_pdf_pages,
        max_source_chars=max_source_chars,
        document_extraction_max_attempts=document_extraction_max_attempts,
        document_extraction_retry_base_seconds=document_extraction_retry_base_seconds,
        document_extraction_lease_seconds=document_extraction_lease_seconds,
        document_extraction_poll_seconds=document_extraction_poll_seconds,
        document_ocr_timeout_seconds=document_ocr_timeout_seconds,
        document_ocr_render_scale=document_ocr_render_scale,
        document_max_image_pixels=document_max_image_pixels,
        document_segment_max_chars=document_segment_max_chars,
        analysis_detection_max_attempts=analysis_detection_max_attempts,
        analysis_detection_retry_base_seconds=analysis_detection_retry_base_seconds,
        analysis_detection_lease_seconds=analysis_detection_lease_seconds,
        analysis_detection_poll_seconds=analysis_detection_poll_seconds,
        document_storage_backend=document_storage_backend,
        document_storage_endpoint=document_storage_endpoint,
        document_storage_public_endpoint=document_storage_public_endpoint,
        document_storage_region=document_storage_region,
        document_storage_access_key=document_storage_access_key,
        document_storage_secret_key=document_storage_secret_key,
        document_quarantine_bucket=document_quarantine_bucket,
        document_clean_bucket=document_clean_bucket,
        document_storage_sse_mode=document_storage_sse_mode,
        document_storage_sse_kms_key_id=document_storage_sse_kms_key_id,
        document_upload_ttl_seconds=document_upload_ttl_seconds,
        document_download_ttl_seconds=document_download_ttl_seconds,
        document_scanner_mode=document_scanner_mode,
        document_clamav_host=document_clamav_host,
        document_clamav_port=document_clamav_port,
        document_clamav_timeout_seconds=document_clamav_timeout_seconds,
        auth_session_secret=auth_session_secret,
        auth_session_ttl_seconds=_positive_int_from_env("AUTH_SESSION_TTL_SECONDS", 8 * 60 * 60, 300),
        auth_cookie_secure=auth_cookie_secure,
        oidc_issuer=oidc_issuer,
        oidc_client_id=oidc_client_id,
        oidc_client_secret=oidc_client_secret,
        oidc_redirect_uri=oidc_redirect_uri,
        oidc_discovery_url=oidc_discovery_url,
    )


settings = get_settings()
