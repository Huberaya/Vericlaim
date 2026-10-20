"""HTTP smoke through a running local frontend proxy, NOT a staging/OIDC proof.

Run from backend with the SAME isolated SQLite DATABASE_URL and session secret
as the local API, APP_ENV=development, and VERICLAIM_ALLOW_LOCAL_HTTP_SEED=true.
This provisions test identities directly in that disposable database, never via
an application authentication bypass. No storage/scanner is substituted.
"""
from datetime import datetime, timedelta, timezone
import os
from urllib.parse import urlparse

import httpx
from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.identity.security import SESSION_COOKIE_NAME
from app.models.domain import AuthSession
from tests.auth_support import authenticate_client


def main() -> None:
    base = os.environ.get("VERICLAIM_HTTP_URL", "http://127.0.0.1:3000")
    if not (
        os.environ.get("VERICLAIM_ALLOW_LOCAL_HTTP_SEED") == "true"
        and settings.environment == "development"
        and settings.database_url.startswith("sqlite:")
        and "/.cache/" in settings.database_url
        and urlparse(base).hostname in {"localhost", "127.0.0.1"}
    ):
        raise SystemExit("Refusing to seed anything except an explicitly opted-in disposable local SQLite preview")

    with httpx.Client(base_url=base, timeout=20) as a, httpx.Client(base_url=base, timeout=20) as b:
        assert a.get("/").status_code == 200
        assert a.get("/api/v1/auth/me").status_code == 401
        assert a.post("/api/v1/auth/dev-login").status_code == 404
        assert a.get("/api/v1/auth/status").json() == {"oidc_configured": False, "dev_login_enabled": False, "authentication_required": True}
        assert a.get("/api/v1/auth/login").status_code == 503
        print("PASS frontend HTML, API proxy, anonymous refusal, dev-login closed, OIDC unavailable explicitly")

        identity_a = authenticate_client(a, role_code="analyst")
        authenticate_client(b, role_code="analyst")
        assert a.get("/api/v1/auth/me").status_code == 200
        payload = {"title": "HTTP local smoke", "document_type": "supplier_declaration"}
        csrf = a.headers.pop("X-CSRF-Token")
        assert a.post("/api/v1/documents", json=payload).status_code == 403
        a.headers["X-CSRF-Token"] = csrf
        response = a.post("/api/v1/documents", json=payload)
        assert response.status_code == 201, response.text
        document_id = response.json()["id"]
        assert a.get(f"/api/v1/documents/{document_id}").status_code == 200
        assert b.get(f"/api/v1/documents/{document_id}").status_code == 404
        verification = a.get("/api/v1/audit/verify")
        assert verification.status_code == 200, verification.text
        assert verification.json()["is_valid"] is True
        assert verification.json()["total_events"] >= 1
        print("PASS valid sessions, CSRF refusal, document metadata persistence, two-tenant API isolation and audit verification")

        response = a.post(f"/api/v1/documents/{document_id}/versions", json={
            "source_filename": "sample.txt", "content_type": "text/plain", "size_bytes": 4,
        })
        assert response.status_code == 503, response.text
        print("PASS disabled real storage fails closed (NOT a successful upload/extraction proof)")

        assert a.post("/api/v1/auth/logout").status_code == 204
        a.cookies.set(SESSION_COOKIE_NAME, identity_a.session_token)
        assert a.get("/api/v1/auth/me").status_code == 401
        with SessionLocal() as db:
            # Expire only the newly provisioned identity's sessions.
            sessions = db.scalars(select(AuthSession).where(AuthSession.user_id == identity_a.user_id)).all()
            assert sessions
            for session in sessions:
                session.revoked_at = None
                session.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            db.commit()
        assert a.get("/api/v1/auth/me").status_code == 401
        print("PASS server-side revocation and expiration over HTTP")


if __name__ == "__main__":
    main()
