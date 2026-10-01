from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse


PRODUCTION_LIKE_ENVIRONMENTS = frozenset({"staging", "production"})
KNOWN_ENVIRONMENTS = frozenset({"development", "test", *PRODUCTION_LIKE_ENVIRONMENTS})
DEV_SESSION_SECRET = "development-only-session-secret-change-before-shared-use"
# A report signature attests that a PDF was produced from a given immutable
# analysis version. A development key cannot provide that guarantee outside a
# local machine, so production-like environments must supply a real one.
DEV_REPORT_SIGNING_KEY = "development-only-report-signing-key-not-a-guarantee"
# C13 — le prestataire de paiement « local » signe ses événements comme un vrai
# prestataire, mais avec un secret de développement. Il est refusé hors de
# development/test : un plan accordé sans encaissement ne doit pas pouvoir
# arriver chez un client.
DEV_BILLING_WEBHOOK_SECRET = "development-only-billing-webhook-secret"

# Déploiement — un cycle de travail déclenché par le planificateur de la plateforme
# ne peut pas durer plus longtemps que ce qu'une fonction sans serveur accepte. Le
# budget est un plafond dur : au-delà, le cycle est coupé et le travail réclamé par
# un bail redevient disponible, plutôt que de laisser croire à un succès.
MAX_INTERNAL_WORKER_BUDGET_SECONDS = 240

# Serverless platforms inject their own environment indicator. ``APP_ENV`` has a
# permissive default ("development") which is never the right answer on a shared
# host: it re-enables the credential-less pilot route, defaults to SQLite,
# disables secure cookies and accepts localhost CORS. When the platform tells us
# which environment is being served, APP_ENV must be set explicitly and agree.
PLATFORM_ENVIRONMENT_VARIABLE = "VERCEL_ENV"
# Vercel also sets VERCEL=1; used only to detect "on the platform at all".
PLATFORM_PRESENCE_VARIABLES = ("VERCEL", "VERCEL_ENV")
APP_ENV_FOR_PLATFORM_ENVIRONMENT = {
    "production": frozenset({"production"}),
    "preview": frozenset({"staging", "production"}),
    "development": frozenset({"development", "test"}),
}
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
MAX_DOCUMENT_UPLOAD_TTL_SECONDS = 60 * 60
MAX_DOCUMENT_DOWNLOAD_TTL_SECONDS = 15 * 60
MAX_DOCUMENT_EXTRACTION_ATTEMPTS = 10
MAX_DOCUMENT_EXTRACTION_LEASE_SECONDS = 60 * 60
MAX_DOCUMENT_OCR_TIMEOUT_SECONDS = 60
MAX_DOCUMENT_IMAGE_PIXELS = 80_000_000
MAX_ANALYSIS_DETECTION_ATTEMPTS = 10
MAX_ANALYSIS_DETECTION_LEASE_SECONDS = 15 * 60
MAX_REPORT_ATTEMPTS = 10
MAX_REPORT_LEASE_SECONDS = 30 * 60


@dataclass(frozen=True)
class Settings:
    app_name: str = "VeriClaim — Regulatory Rule Engine"
    environment: str = "development"
    database_url: str = "sqlite:///./vericlaim.db"
    auto_create_schema: bool = True
    allow_local_sqlite_fallback: bool = False
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
    # --- C22 — file de génération des rapports ------------------------------- #
    # La génération PDF/ZIP ne se fait plus dans la requête HTTP : elle est
    # réclamée par un worker dédié, avec bail, reprise après incident et
    # contre-pression. Ces valeurs sont publiées par /api/v1/reports/queue.
    report_max_attempts: int = 3
    report_retry_base_seconds: int = 60
    report_lease_seconds: int = 10 * 60
    report_poll_seconds: int = 3
    # Contre-pression : au-delà, la demande est refusée (429) au lieu d'empiler
    # indéfiniment des travaux qu'aucun worker ne rattrapera.
    report_max_pending_per_organization: int = 25
    # Équité : un locataire ne peut pas occuper tous les workers à la fois.
    report_max_running_per_organization: int = 3
    # Plafond de lecture du binaire stocké (garde-fou anti-fuite mémoire).
    report_download_max_bytes: int = 64 * 1024 * 1024
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
    # --- Déploiement : workers portés par le planificateur de la plateforme ----
    # Sur un hôte sans processus permanent, les trois workers ne peuvent plus tourner
    # en boucle : le planificateur appelle une route qui exécute un cycle. Cette route
    # est une **commande**, pas une opération utilisateur ; elle attend le secret que
    # la plateforme injecte elle-même (`CRON_SECRET`, envoyé en `Authorization: Bearer`).
    # Un secret absent ne bloque pas le démarrage — il rend la file inerte et la route
    # répond 503. Refuser de démarrer pour cela empêcherait l'API de servir le reste.
    internal_worker_secret: str | None = None
    internal_worker_budget_seconds: int = 45
    auth_session_secret: str = DEV_SESSION_SECRET
    report_signing_key: str = DEV_REPORT_SIGNING_KEY
    #: Adresse à laquelle les demandes d'exercice des droits sont reçues. Vide par
    #: défaut : l'API publie alors ``null`` au lieu d'inventer une adresse.
    data_rights_contact_email: str = ""
    #: Adresse de support publiée. Vide par défaut : l'API publie alors ``null`` et le
    #: formulaire dans l'application reste le canal — aucune adresse plausible inventée.
    support_contact_email: str = ""
    auth_session_ttl_seconds: int = 8 * 60 * 60
    auth_cookie_secure: bool = False
    # Local pilot helper that mints an owner session with no credentials. It is
    # enabled automatically only in development/test and can never be enabled in
    # a shared environment (see the check in ``get_settings``).
    enable_dev_login: bool = True
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_redirect_uri: str | None = None
    oidc_discovery_url: str | None = None

    # --- C13: plans, quotas et facturation ------------------------------------
    # ``billing_provider`` dit QUI encaisse, et l'API le publie tel quel.
    #   "local"    → prestataire de développement : aucune carte, aucun débit ;
    #                les événements sont signés par un secret local. Refusé dans
    #                un environnement de production (contrôle dans ``get_settings``).
    #   "stripe"   → vrai prestataire ; exige une clé secrète *et* un secret de webhook.
    #   "disabled" → aucune souscription possible : les organisations gardent leur
    #                essai puis sont bloquées, faute de pouvoir payer. Un produit
    #                qui ne peut pas encaisser doit le dire, pas le simuler.
    billing_provider: str = "local"
    billing_local_webhook_secret: str = DEV_BILLING_WEBHOOK_SECRET
    billing_checkout_ttl_seconds: int = 60 * 60
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    stripe_api_base: str = "https://api.stripe.com"
    # Correspondance plan ↔ tarif du prestataire : {"starter": "price_...", ...}.
    stripe_price_ids_json: str = "{}"

    # --- C14: self-service identity and transactional e-mail ------------------
    # ``email_backend`` is explicit about what the deployment can actually do.
    #   "outbox"   → messages are recorded, nothing leaves the instance (dev/test)
    #   "smtp"     → messages are really handed to an SMTP server
    #   "disabled" → the product refuses to pretend: no message is recorded as sent
    # There is no default that silently claims delivery.
    email_backend: str = "outbox"
    email_from_address: str = "no-reply@vericlaim.invalid"
    email_from_name: str = "VeriClaim"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_starttls: bool = True
    email_verification_ttl_seconds: int = 24 * 60 * 60
    password_reset_ttl_seconds: int = 60 * 60
    invitation_ttl_seconds: int = 7 * 24 * 60 * 60
    password_login_max_failures: int = 8
    password_login_lockout_seconds: int = 15 * 60

    @property
    def oidc_configured(self) -> bool:
        return bool(self.oidc_issuer and self.oidc_client_id and self.oidc_redirect_uri and self.oidc_discovery_url)

    @property
    def billing_collects_money(self) -> bool:
        """Vrai si le prestataire configuré encaisse réellement de l'argent."""

        return self.billing_provider == "stripe" and bool(self.stripe_secret_key)

    @property
    def is_production_like(self) -> bool:
        return self.environment in PRODUCTION_LIKE_ENVIRONMENTS

    def describe_runtime(self) -> dict[str, str | bool | None]:
        """Non-secret summary of the resolved runtime, for structured startup logs."""
        return {
            "environment": self.environment,
            "database_backend": self.database_url.split(":", 1)[0],
            "database_host": _safe_database_host(self.database_url),
            "auto_create_schema": self.auto_create_schema,
            "dev_login_enabled": self.enable_dev_login,
            "document_storage_backend": self.document_storage_backend,
            "document_scanner_mode": self.document_scanner_mode,
            "oidc_configured": self.oidc_configured,
            "billing_provider": self.billing_provider,
            "secure_cookies": self.auth_cookie_secure,
        }


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


def _safe_database_host(database_url: str) -> str | None:
    """Host of the configured database, without credentials or query parameters."""
    parsed = urlparse(database_url)
    return parsed.hostname or parsed.path or None


def _normalise_base_url(value: str) -> str:
    return value.rstrip("/")


def _validate_url(name: str, value: str, *, require_https: bool) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError(f"{name} must be an absolute http(s) URL.")
    if require_https and parsed.scheme != "https":
        raise RuntimeError(f"{name} must use HTTPS in staging/production.")
    return value


def _is_strong_secret(value: str) -> bool:
    """Reject secrets that are long but not random.

    A length check alone accepts ``changeme`` repeated to 40 characters, and even
    ``k`` * 48. Both are guesses an attacker makes first, and a report signed with
    such a key can be forged by anyone. This is a heuristic, not a proof of
    randomness: it raises the floor, it does not establish entropy.
    """
    if len(value) < 32:
        return False
    if value == DEV_REPORT_SIGNING_KEY:
        return False
    lowered = value.lower()
    markers = (
        "replace-",
        "replace_",
        "change-me",
        "changeme",
        "change_me",
        "change-",
        "dev-",
        "dev_",
        "example",
        "placeholder",
        "your-",
        "your_",
        "secret",
        "password",
        "vericlaim",
        "todo",
    )
    if any(marker in lowered for marker in markers):
        return False
    return len(set(value)) >= 16


def get_settings() -> Settings:
    # --- Platform coherence must be decided before anything else: the default
    # environment is permissive, so an unset APP_ENV on a shared platform would
    # silently reopen the credential-less pilot route and use a local database.
    raw_app_env = os.getenv("APP_ENV", "").strip()
    app_env_is_explicit = bool(raw_app_env)
    platform_environment = os.getenv(PLATFORM_ENVIRONMENT_VARIABLE, "").strip().lower()
    on_shared_platform = any(os.getenv(name) for name in PLATFORM_PRESENCE_VARIABLES)

    if on_shared_platform and not app_env_is_explicit:
        raise RuntimeError(
            f"APP_ENV must be set explicitly on {PLATFORM_ENVIRONMENT_VARIABLE} deployments. "
            "The 'development' default would enable the credential-less pilot login route, a local "
            "SQLite database and non-secure cookies. Set APP_ENV=production (or staging)."
        )
    if platform_environment:
        allowed = APP_ENV_FOR_PLATFORM_ENVIRONMENT.get(platform_environment)
        if allowed is None:
            raise RuntimeError(
                f"{PLATFORM_ENVIRONMENT_VARIABLE} has an unsupported value "
                f"'{platform_environment}'; expected one of "
                f"{sorted(APP_ENV_FOR_PLATFORM_ENVIRONMENT)}."
            )
        if raw_app_env.lower() not in allowed:
            raise RuntimeError(
                f"APP_ENV='{raw_app_env}' is inconsistent with "
                f"{PLATFORM_ENVIRONMENT_VARIABLE}='{platform_environment}'. "
                f"Expected one of {sorted(allowed)}."
            )

    environment = raw_app_env.lower() or "development"
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

    # Local-only resilience aid. When the configured database is unreachable and
    # this flag is true, the API falls back to a throwaway SQLite file. It is
    # strictly forbidden outside development/test because it silently bypasses
    # PostgreSQL RLS and loses data on an ephemeral filesystem.
    allow_local_sqlite_fallback = _bool_from_env(
        os.getenv("ALLOW_LOCAL_SQLITE_FALLBACK"),
        default=False,
    )

    database_url = _optional_env("DATABASE_URL")
    if not database_url:
        if environment not in {"development", "test"}:
            raise RuntimeError(
                "DATABASE_URL must be configured outside development/test; "
                "refusing the SQLite development default."
            )
        database_url = "sqlite:///./vericlaim.db"
    else:
        # Normalize postgres / postgresql connection strings to use psycopg (v3) driver
        if database_url.startswith("postgres://"):
            database_url = "postgresql+psycopg://" + database_url[len("postgres://"):]
        elif database_url.startswith("postgresql://") and not database_url.startswith("postgresql+"):
            database_url = "postgresql+psycopg://" + database_url[len("postgresql://"):]

    if is_production_like and not database_url.startswith(("postgresql://", "postgresql+")):
        raise RuntimeError(
            "DATABASE_URL must use PostgreSQL in staging/production so tenant RLS is enforceable."
        )
    if allow_local_sqlite_fallback and environment not in {"development", "test"}:
        raise RuntimeError(
            "ALLOW_LOCAL_SQLITE_FALLBACK may only be enabled in development/test: it silently "
            "substitutes a throwaway database and disables PostgreSQL row-level security."
        )
    if on_shared_platform and allow_local_sqlite_fallback:
        raise RuntimeError(
            "ALLOW_LOCAL_SQLITE_FALLBACK must not be enabled on a shared platform."
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

    # Déploiement — secret du déclencheur de travaux. Vercel lit `CRON_SECRET` lui-même
    # et l'envoie en `Authorization: Bearer` ; le nom de la variable est donc imposé par
    # la plateforme, pas choisi ici.
    internal_worker_secret = _optional_env("CRON_SECRET")
    if internal_worker_secret is not None and len(internal_worker_secret) < 32:
        raise RuntimeError(
            "CRON_SECRET must be at least 32 characters: this value authorises the "
            "command that drains the work queues."
        )
    internal_worker_budget_seconds = _positive_int_from_env("INTERNAL_WORKER_BUDGET_SECONDS", 45, 5)
    if internal_worker_budget_seconds > MAX_INTERNAL_WORKER_BUDGET_SECONDS:
        raise RuntimeError(
            "INTERNAL_WORKER_BUDGET_SECONDS must not exceed "
            f"{MAX_INTERNAL_WORKER_BUDGET_SECONDS} seconds."
        )

    data_rights_contact_email = (os.getenv("DATA_RIGHTS_CONTACT_EMAIL") or "").strip()
    support_contact_email = (os.getenv("SUPPORT_CONTACT_EMAIL") or "").strip()
    report_signing_key = _optional_env("REPORT_SIGNING_KEY")
    if not report_signing_key:
        if is_production_like:
            raise RuntimeError(
                "REPORT_SIGNING_KEY must be configured in staging/production: without it a report "
                "cannot be bound to the analysis it describes."
            )
        report_signing_key = DEV_REPORT_SIGNING_KEY
    if is_production_like and not _is_strong_secret(report_signing_key):
        raise RuntimeError(
            "REPORT_SIGNING_KEY must be a random value of at least 32 characters, with no "
            "placeholder marker and at least 16 distinct characters, in staging/production."
        )

    email_backend = (_optional_env("EMAIL_BACKEND") or "outbox").strip().lower()
    if email_backend not in {"outbox", "smtp", "disabled"}:
        raise RuntimeError("EMAIL_BACKEND must be one of: outbox, smtp, disabled.")
    smtp_host = _optional_env("SMTP_HOST")
    if email_backend == "smtp" and not smtp_host:
        raise RuntimeError("EMAIL_BACKEND=smtp requires SMTP_HOST to be configured.")
    if email_backend == "smtp" and is_production_like and not _optional_env("SMTP_USERNAME"):
        raise RuntimeError(
            "EMAIL_BACKEND=smtp requires SMTP_USERNAME in staging/production: an "
            "unauthenticated relay is not an acceptable production transport."
        )

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

    # The unauthenticated pilot route must fail closed. Default: available in
    # development/test only, never in a shared environment, and an explicit
    # opt-in is required to disable it locally. Enabling it in staging or
    # production is a startup error rather than a silent exposure.
    enable_dev_login = _bool_from_env(
        os.getenv("ENABLE_DEV_LOGIN"),
        default=environment in {"development", "test"},
    )
    if is_production_like and enable_dev_login:
        raise RuntimeError(
            "ENABLE_DEV_LOGIN must not be enabled in staging/production: this route issues an owner "
            "session without any credential. Remove the variable or set APP_ENV=development."
        )

    # --- C13: qui encaisse ----------------------------------------------------
    # Le prestataire « local » accorde des plans sans qu'aucun euro n'entre : il
    # n'a rien à faire dans un environnement partagé, où un client pourrait
    # souscrire. Même traitement que la route de développement : une erreur au
    # démarrage, jamais une exposition silencieuse.
    # Défaut : le prestataire de développement en développement/test, et **aucune
    # vente** dans un environnement partagé — jamais un encaissement simulé.
    default_billing_provider = "local" if environment in {"development", "test"} else "disabled"
    billing_provider = (os.getenv("BILLING_PROVIDER") or default_billing_provider).strip().lower()
    if billing_provider not in {"local", "stripe", "disabled"}:
        raise RuntimeError(
            "BILLING_PROVIDER must be one of local, stripe or disabled "
            f"(received {billing_provider!r})."
        )
    if is_production_like and billing_provider == "local":
        raise RuntimeError(
            "BILLING_PROVIDER=local is refused in staging/production: no payment is ever collected "
            "while plans are granted. Set BILLING_PROVIDER=stripe, or 'disabled' to stop selling."
        )
    stripe_secret_key = _optional_env("STRIPE_SECRET_KEY")
    stripe_webhook_secret = _optional_env("STRIPE_WEBHOOK_SECRET")
    if billing_provider == "stripe" and not (stripe_secret_key and stripe_webhook_secret):
        raise RuntimeError(
            "BILLING_PROVIDER=stripe requires STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET. Without "
            "the webhook secret, an unauthenticated caller could change anyone's subscription."
        )
    stripe_api_base = _validate_url(
        "STRIPE_API_BASE",
        _normalise_base_url(os.getenv("STRIPE_API_BASE", "https://api.stripe.com").strip()),
        require_https=is_production_like,
    )
    billing_local_webhook_secret = (
        _optional_env("BILLING_LOCAL_WEBHOOK_SECRET") or DEV_BILLING_WEBHOOK_SECRET
    )
    billing_checkout_ttl_seconds = _positive_int_from_env("BILLING_CHECKOUT_TTL_SECONDS", 60 * 60, 60)

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

    report_max_attempts = _positive_int_from_env("REPORT_MAX_ATTEMPTS", 3, 1)
    if report_max_attempts > MAX_REPORT_ATTEMPTS:
        raise RuntimeError(f"REPORT_MAX_ATTEMPTS must not exceed {MAX_REPORT_ATTEMPTS}.")
    report_retry_base_seconds = _positive_int_from_env("REPORT_RETRY_BASE_SECONDS", 60, 1)
    report_lease_seconds = _positive_int_from_env("REPORT_LEASE_SECONDS", 10 * 60, 30)
    if report_lease_seconds > MAX_REPORT_LEASE_SECONDS:
        raise RuntimeError(
            f"REPORT_LEASE_SECONDS must not exceed {MAX_REPORT_LEASE_SECONDS} seconds."
        )
    report_poll_seconds = _positive_int_from_env("REPORT_POLL_SECONDS", 3, 1)
    report_max_pending_per_organization = _positive_int_from_env(
        "REPORT_MAX_PENDING_PER_ORGANIZATION", 25, 1
    )
    report_max_running_per_organization = _positive_int_from_env(
        "REPORT_MAX_RUNNING_PER_ORGANIZATION", 3, 1
    )
    report_download_max_bytes = _positive_int_from_env(
        "REPORT_DOWNLOAD_MAX_BYTES", 64 * 1024 * 1024, 1024
    )

    return Settings(
        app_name=os.getenv("APP_NAME", "VeriClaim — Regulatory Rule Engine"),
        environment=environment,
        database_url=database_url,
        auto_create_schema=auto_create_schema,
        allow_local_sqlite_fallback=allow_local_sqlite_fallback,
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
        report_max_attempts=report_max_attempts,
        report_retry_base_seconds=report_retry_base_seconds,
        report_lease_seconds=report_lease_seconds,
        report_poll_seconds=report_poll_seconds,
        report_max_pending_per_organization=report_max_pending_per_organization,
        report_max_running_per_organization=report_max_running_per_organization,
        report_download_max_bytes=report_download_max_bytes,
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
        internal_worker_secret=internal_worker_secret,
        internal_worker_budget_seconds=internal_worker_budget_seconds,
        auth_session_secret=auth_session_secret,
        report_signing_key=report_signing_key,
        auth_session_ttl_seconds=_positive_int_from_env("AUTH_SESSION_TTL_SECONDS", 8 * 60 * 60, 300),
        auth_cookie_secure=auth_cookie_secure,
        enable_dev_login=enable_dev_login,
        oidc_issuer=oidc_issuer,
        oidc_client_id=oidc_client_id,
        oidc_client_secret=oidc_client_secret,
        oidc_redirect_uri=oidc_redirect_uri,
        oidc_discovery_url=oidc_discovery_url,
        billing_provider=billing_provider,
        billing_local_webhook_secret=billing_local_webhook_secret,
        billing_checkout_ttl_seconds=billing_checkout_ttl_seconds,
        stripe_secret_key=stripe_secret_key,
        stripe_webhook_secret=stripe_webhook_secret,
        stripe_api_base=stripe_api_base,
        stripe_price_ids_json=(_optional_env("STRIPE_PRICE_IDS_JSON") or "{}"),
        email_backend=email_backend,
        email_from_address=(_optional_env("EMAIL_FROM_ADDRESS") or "no-reply@vericlaim.invalid"),
        email_from_name=(_optional_env("EMAIL_FROM_NAME") or "VeriClaim"),
        smtp_host=smtp_host,
        smtp_port=_positive_int_from_env("SMTP_PORT", 587, 1),
        smtp_username=_optional_env("SMTP_USERNAME"),
        smtp_password=_optional_env("SMTP_PASSWORD"),
        smtp_use_starttls=_bool_from_env(os.getenv("SMTP_USE_STARTTLS"), default=True),
        email_verification_ttl_seconds=_positive_int_from_env(
            "EMAIL_VERIFICATION_TTL_SECONDS", 24 * 60 * 60, 300
        ),
        password_reset_ttl_seconds=_positive_int_from_env(
            "PASSWORD_RESET_TTL_SECONDS", 60 * 60, 300
        ),
        invitation_ttl_seconds=_positive_int_from_env("INVITATION_TTL_SECONDS", 7 * 24 * 60 * 60, 3600),
        password_login_max_failures=_positive_int_from_env("PASSWORD_LOGIN_MAX_FAILURES", 8, 3),
        password_login_lockout_seconds=_positive_int_from_env(
            "PASSWORD_LOGIN_LOCKOUT_SECONDS", 15 * 60, 60
        ),
    )


settings = get_settings()
