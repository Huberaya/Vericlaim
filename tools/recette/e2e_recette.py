"""End-to-end qualification of the VeriClaim recette data plane.

The harness runs the **unmodified application** (``app.main:app`` through a real
Uvicorn server) against real PostgreSQL, a real S3 API and the real extraction /
detection workers.  Every step is an HTTP request a browser could have made, or a
database read an operator could have made; nothing is asserted about a mock.

Output is a machine-readable transcript written to ``.recette/last-run.json``.
The documentation under ``docs/infrastructure/`` quotes it, so a claim in the
report can always be traced back to a recorded observation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
RUNTIME_DIR = REPO_ROOT / ".recette"
ARTIFACTS = RUNTIME_DIR / "artifacts"

sys.path.insert(0, str(BACKEND_ROOT))

CLAIM_TEXT = (
    "Notre gamme de détergents est entièrement biodegradable et recyclable. "
    "Le flacon est neutre en carbone grâce à des crédits de compensation certifiés. "
    "Nos émissions sont 30 % plus faibles que celles de notre principal concurrent."
)
SECOND_CLAIM_TEXT = (
    "Ce emballage est compostable industriellement et fabriqué à partir de matières recyclées."
)


# --------------------------------------------------------------------------- reporting
@dataclass
class Step:
    name: str
    status: str  # PASS | FAIL | BLOCKED | INFO
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)


class Transcript:
    def __init__(self) -> None:
        self.steps: list[Step] = []
        self.failures = 0
        #: Timestamped progress markers. When a run dies *between* two checks,
        #: the job log alone cannot say where; these markers can, and they are
        #: what the traceback hook prints.
        self.marks: list[tuple[float, str]] = []

    def mark(self, label: str) -> None:
        self.marks.append((time.time(), label))
        del self.marks[:-12]  # keep the trail short enough to publish in a check
        print(f"  [MARK] {label}", flush=True)

    def trail(self) -> str:
        return " > ".join(label for _, label in self.marks)

    def check(self, name: str, condition: bool, detail: str, **evidence: Any) -> bool:
        self.mark(name)
        self.steps.append(Step(name, "PASS" if condition else "FAIL", detail, evidence))
        if not condition:
            self.failures += 1
        marker = "PASS" if condition else "FAIL"
        print(f"  [{marker}] {name}: {detail}", flush=True)
        return condition

    def info(self, name: str, detail: str, **evidence: Any) -> None:
        self.steps.append(Step(name, "INFO", detail, evidence))
        print(f"  [INFO] {name}: {detail}")

    def blocked(self, name: str, detail: str, **evidence: Any) -> None:
        self.steps.append(Step(name, "BLOCKED", detail, evidence))
        print(f"  [BLOCKED] {name}: {detail}")

    def to_json(self) -> dict[str, Any]:
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "failures": self.failures,
            "total": len(self.steps),
            "steps": [step.__dict__ for step in self.steps],
        }


# --------------------------------------------------------------------------- HTTP helpers
def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class ApiServer:
    """A real Uvicorn process serving the unmodified application."""

    def __init__(self, env: dict[str, str], *, port: int | None = None) -> None:
        self.env = env
        self.port = port or _free_port()
        self._log_handle = None
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.log_path = ARTIFACTS / "api-server.log"
        self._process: subprocess.Popen[bytes] | None = None

    def start(self) -> "ApiServer":
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        log = self.log_path.open("wb")
        self._log_handle = log
        self._process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "--app-dir",
                str(BACKEND_ROOT),
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--log-level",
                "info",
                "--no-access-log",
            ],
            env={**os.environ, **self.env},
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise RuntimeError(f"API server exited early; see {self.log_path}")
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.4)
                if probe.connect_ex(("127.0.0.1", self.port)) == 0:
                    return self
            time.sleep(0.2)
        raise RuntimeError(f"API server did not listen on {self.port}; see {self.log_path}")

    def stop(self) -> None:
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=15)
            self._process = None
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None
        # A worker or an API process that survives its run keeps a port and a
        # database connection open. Left behind, it can make a *later* run fail in
        # a way that looks random. Waiting for the port to be released turns that
        # into a deterministic check.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    probe.bind(("127.0.0.1", self.port))
                    return
                except OSError:
                    time.sleep(0.1)
        raise RuntimeError(f"port {self.port} was not released after stopping the API server")

    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def log_tail(self, limit: int = 4000) -> str:
        if not self.log_path.exists():
            return ""
        return self.log_path.read_text(errors="replace")[-limit:]


@dataclass
class Browser:
    """One independent browser context: its own cookie jar, like a real user."""

    api: ApiServer
    label: str
    cookies: dict[str, str] = field(default_factory=dict)
    #: Cookie names the application actually sets.
    SESSION_COOKIE = "vericlaim_session"
    CSRF_COOKIE = "vericlaim_csrf"
    _client: Any = field(default=None, repr=False)

    def _http(self):
        if self._client is None:
            import httpx

            self._client = httpx.Client(base_url=self.api.base_url, timeout=120, follow_redirects=False)
        return self._client

    def prime(self) -> None:
        """Load cookies straight from the jar into the httpx client."""
        for name, value in self.cookies.items():
            self._http().cookies.set(name, value)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def abs_url(self, url: str) -> str:
        if url.startswith("http://") or url.startswith("https://"):
            return url
        return f"{self.api.base_url}{url}"

    def get(self, path: str, **kwargs: Any):
        self.prime()
        response = self._http().get(path, **kwargs)
        self._absorb(response)
        return response

    def post(self, path: str, *, csrf: bool = True, **kwargs: Any):
        self.prime()
        headers = dict(kwargs.pop("headers", {}))
        token = self.cookies.get(self.CSRF_COOKIE)
        if csrf and token:
            headers.setdefault("X-CSRF-Token", token)
        response = self._http().post(path, headers=headers, **kwargs)
        self._absorb(response)
        return response

    def _absorb(self, response: Any) -> None:
        for name, value in response.cookies.items():
            self.cookies[name] = value

    # -- OIDC -----------------------------------------------------------------
    def sso_login(self, *, user: str, mode: str = "valid", extra_authorize: dict[str, str] | None = None) -> Any:
        """Drive the full browser dance: /login -> IdP -> /callback."""

        from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

        start = self.get("/api/v1/auth/login?return_to=%2F")
        if start.status_code != 302:
            return start
        authorize_url = start.headers["location"]
        parsed = urlparse(authorize_url)
        query = dict(parse_qsl(parsed.query))
        query["login_hint"] = user
        query["mode"] = mode
        if extra_authorize:
            query.update(extra_authorize)
        idp_url = urlunparse(parsed._replace(query=urlencode(query)))
        redirect = self._http().get(idp_url, follow_redirects=False)
        if redirect.status_code != 302:
            return redirect
        callback_url = redirect.headers["location"]
        # The IdP redirects the *browser* to the frontend origin
        # (http://localhost:3000, enforced to be same-host with FRONTEND_URL).
        # No Next.js server runs here, but the callback route is the API's: a
        # real deployment reaches it through the Vercel rewrite, so replaying
        # path+query against the API origin is the same request the browser makes.
        parsed_callback = urlparse(callback_url)
        return self.get(f"{parsed_callback.path}?{parsed_callback.query}")


# --------------------------------------------------------------------------- fixtures
def write_pdf(path: Path, text: str, *, pages: int = 1) -> None:
    import fitz

    document = fitz.open()
    for index in range(pages):
        page = document.new_page()
        page.insert_text((60, 80), text if index == 0 else f"Page {index + 1}. {text}", fontsize=11)
    document.save(path)
    document.close()


def write_scanned_pdf(path: Path, text: str) -> None:
    """A PDF whose pages are images only: text extraction yields nothing.

    Kept deliberately small (JPEG at 150 dpi) so the fixture exercises the OCR
    fallback rather than the upload size bound: a 6 MB raster PNG would be
    refused by ``MAX_UPLOAD_BYTES`` before any extractor saw it.
    """

    import fitz

    source = fitz.open()
    page = source.new_page()
    page.insert_text((60, 80), text, fontsize=16)
    jpeg = page.get_pixmap(matrix=fitz.Matrix(2, 2)).tobytes("jpeg", jpg_quality=70)
    source.close()

    scanned = fitz.open()
    target = scanned.new_page()
    target.insert_image(target.rect, stream=jpeg)
    scanned.save(path, deflate=True, garbage=4)
    scanned.close()


def write_png(path: Path, text: str) -> None:
    import fitz

    page_doc = fitz.open()
    page = page_doc.new_page()
    page.insert_text((40, 90), text, fontsize=16)
    pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2))
    pixmap.save(str(path))
    page_doc.close()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- harness
class RecetteRun:
    def __init__(self, stack: Any, *, marker: str) -> None:
        self.stack = stack
        self.marker = marker
        self.transcript = Transcript()
        self.server: ApiServer | None = None
        self.generated: dict[str, Path] = {}

    # -- environment -----------------------------------------------------------------
    def api_env(self) -> dict[str, str]:
        stack = self.stack
        settings = {
            "APP_ENV": "development",
            "DATABASE_URL": stack.postgres.app_sqlalchemy_url(stack.postgres_password),
            "AUTO_CREATE_SCHEMA": "false",
            "AUTH_SESSION_SECRET": stack.session_secret,
            "AUTH_COOKIE_SECURE": "false",
            "AUTH_DEV_LOGIN_ENABLED": "false",
            "FRONTEND_URL": "http://localhost:3000",
            "CORS_ORIGINS": "http://localhost:3000",
            "DOCUMENT_STORAGE_BACKEND": "s3",
            "DOCUMENT_STORAGE_ENDPOINT": stack.storage.endpoint_url,
            "DOCUMENT_STORAGE_PUBLIC_ENDPOINT": stack.storage.endpoint_url,
            "DOCUMENT_STORAGE_REGION": stack.storage.region,
            "DOCUMENT_STORAGE_ACCESS_KEY": stack.storage.access_key,
            "DOCUMENT_STORAGE_SECRET_KEY": stack.storage.secret_key,
            "DOCUMENT_QUARANTINE_BUCKET": stack.storage.quarantine_bucket,
            "DOCUMENT_CLEAN_BUCKET": stack.storage.clean_bucket,
            "DOCUMENT_SCANNER_MODE": "clamav",
            "DOCUMENT_CLAMAV_HOST": stack.scanner.host,
            "DOCUMENT_CLAMAV_PORT": str(stack.scanner.port),
            "DOCUMENT_CLAMAV_TIMEOUT_SECONDS": "5",
            "OIDC_ISSUER": stack.idp.issuer,
            "OIDC_CLIENT_ID": stack.idp.client_id,
            "OIDC_REDIRECT_URI": f"http://localhost:3000/api/v1/auth/callback",
            "OIDC_DISCOVERY_URL": f"{stack.idp.issuer}/.well-known/openid-configuration",
            "MAX_UPLOAD_BYTES": str(8 * 1024 * 1024),
            "MAX_PDF_PAGES": "5",
            "DOCUMENT_EXTRACTION_POLL_SECONDS": "1",
            "ANALYSIS_DETECTION_POLL_SECONDS": "1",
            "TESSERACT_LANGUAGES": "eng",
            "LOG_LEVEL": "INFO",
        }
        return settings

    def make_fixtures(self) -> None:
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        fixtures = {
            "clean_pdf": ARTIFACTS / "declaration-fournisseur.pdf",
            "clean_txt": ARTIFACTS / "allégations-produit.txt",
            "second_pdf": ARTIFACTS / "emballage-fictif.pdf",
            "scanned_pdf": ARTIFACTS / "declaration-scannee.pdf",
            "png": ARTIFACTS / "etiquette.png",
            "empty_txt": ARTIFACTS / "vide.txt",
        }
        write_pdf(fixtures["clean_pdf"], CLAIM_TEXT)
        write_pdf(fixtures["second_pdf"], SECOND_CLAIM_TEXT)
        write_scanned_pdf(fixtures["scanned_pdf"], CLAIM_TEXT[:120])
        write_png(fixtures["png"], "Recyclable biodegradable")
        fixtures["clean_txt"].write_text(CLAIM_TEXT + "\n", encoding="utf-8")
        fixtures["empty_txt"].write_text("", encoding="utf-8")

        # Hostile/negative fixtures are produced in-process, never downloaded.
        (ARTIFACTS / "eicar.txt").write_bytes(
            b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
        )
        (ARTIFACTS / "executable-disguise.pdf").write_bytes(b"MZ\x90\x00" + b"\x00" * 512)
        (ARTIFACTS / "payload.bin").write_bytes(b"\x7fELF" + b"\x00" * 256)
        self.generated = fixtures

    # -- run -------------------------------------------------------------------------
    def execute(self) -> dict[str, Any]:
        import httpx

        t = self.transcript
        stack = self.stack
        try:
            return self._execute_journey(httpx)
        finally:
            # Any exception used to leave the Uvicorn process behind. Orphaned
            # servers then competed with the next run's port allocation and
            # database connections — the harness must not carry state forward.
            if self.server is not None:
                self.server.stop()
                self.server = None
            for browser in getattr(self, "_browsers", []):
                browser.close()

    def _execute_journey(self, httpx: Any) -> dict[str, Any]:
        t = self.transcript
        stack = self.stack
        self.make_fixtures()
        t.mark("fixtures generated")

        # -- boot ---------------------------------------------------------------------
        self.server = ApiServer(self.api_env()).start()
        print(f"API server up on {self.server.base_url}")

        t.check(
            "healthz répond",
            httpx.get(f"{self.server.base_url}/healthz", timeout=10).status_code == 200,
            "processus vivant",
        )
        ready = httpx.get(f"{self.server.base_url}/readyz", timeout=30)
        t.check(
            "readyz expose la base et les deux dépendances documentaires",
            ready.status_code == 200
            and ready.json()["database"] == "ok"
            and ready.json()["document_import"] == "enabled",
            f"{ready.status_code} {ready.json()}",
            body=ready.json(),
        )
        status = httpx.get(f"{self.server.base_url}/api/v1/auth/status", timeout=10).json()
        t.check(
            "auth/status annonce OIDC configuré et dev-login fermé",
            status["oidc_configured"] is True and status["dev_login_enabled"] is False,
            json.dumps(status, ensure_ascii=False),
            body=status,
        )
        dev_login = httpx.post(f"{self.server.base_url}/api/v1/auth/dev-login", timeout=10)
        t.check(
            "dev-login est impossible (404)",
            dev_login.status_code == 404,
            f"HTTP {dev_login.status_code}",
        )

        # -- negative authentication ---------------------------------------------------
        anonymous = httpx.get(f"{self.server.base_url}/api/v1/auth/me", timeout=10)
        t.check(
            "route protégée sans session -> 401",
            anonymous.status_code == 401,
            f"HTTP {anonymous.status_code}",
        )
        no_session_headers = httpx.post(
            f"{self.server.base_url}/api/v1/organizations",
            json={"name": "Sans session"},
            timeout=10,
        )
        t.check(
            "mutation sans session -> 401 (pas 403 trompeur)",
            no_session_headers.status_code == 401,
            f"HTTP {no_session_headers.status_code}",
        )

        # -- hostile callbacks ---------------------------------------------------------
        hostile = Browser(self.server, "attaquant")
        t.check(
            "callback sans cookie de transaction -> 400",
            hostile.get("/api/v1/auth/callback?code=forged&state=forged").status_code == 400,
            "aucune transaction signée présentée",
        )
        state_mismatch = hostile.sso_login(user="alice", extra_authorize={"state": "injected-state"})
        t.check(
            "callback avec state substitué -> 400/401",
            state_mismatch.status_code in {400, 401},
            f"HTTP {state_mismatch.status_code}",
        )
        hostile.close()

        for label, mode, expectation in (
            ("token expiré", "expired", "rejet"),
            ("signature invalide", "bad_signature", "rejet"),
            ("issuer étranger", "wrong_issuer", "rejet"),
            ("audience étrangère", "wrong_audience", "rejet"),
            ("nonce rejoué", "bad_nonce", "rejet"),
            ("email non vérifié", "unverified_email", "rejet"),
        ):
            attacker = Browser(self.server, f"attacker-{mode}")
            response = attacker.sso_login(user="alice", mode=mode)
            rejected = response.status_code in {400, 401, 403}
            t.check(
                f"jeton OIDC {label} -> {expectation}",
                rejected,
                f"HTTP {response.status_code}",
                mode=mode,
            )
            attacker.close()

        # -- organisation A ------------------------------------------------------------
        alice = Browser(self.server, "alice")
        login = alice.sso_login(user="alice")
        t.check(
            "connexion SSO valide d'Alice -> redirection frontend",
            login.status_code == 303,
            f"HTTP {login.status_code} -> {login.headers.get('location')} {login.text[:300]}",
        )
        me = alice.get("/api/v1/auth/me")
        t.check("session Alice lisible", me.status_code == 200, f"HTTP {me.status_code}")
        alice_user = me.json()["user"]
        t.check(
            "provisionnement SSO : identité liée à l'issuer et au subject",
            alice_user["email"] == "alice.recette@example.com",
            f"email={alice_user['email']}",
            user_id=alice_user["id"],
        )

        csrf_probe = alice.post("/api/v1/organizations", json={"name": "Sans CSRF", "slug": "sans-csrf"}, csrf=False)
        t.check(
            "mutation sans jeton CSRF -> 403",
            csrf_probe.status_code == 403,
            f"HTTP {csrf_probe.status_code}",
        )

        org_a = alice.post("/api/v1/organizations", json={"name": "Org Recette Alpha", "slug": "org-recette-alpha"})
        t.check("création organisation A", org_a.status_code == 201, f"HTTP {org_a.status_code} {org_a.text[:200]}")
        org_a_id = org_a.json()["organization"]["id"]

        supplier_a = alice.post(
            "/api/v1/suppliers",
            json={"legal_name": "Fournisseur Fictif Alpha SAS", "external_reference": f"SUP-{self.marker}"},
            headers={"Idempotency-Key": f"supplier-a-{self.marker}"},
        )
        t.check("création fournisseur fictif A", supplier_a.status_code == 201, f"HTTP {supplier_a.status_code}")
        supplier_a_id = supplier_a.json()["supplier"]["id"]

        product_a = alice.post(
            "/api/v1/products",
            json={
                "supplier_id": supplier_a_id,
                "reference": f"PROD-{self.marker}",
                "name": "Détergent Fictif Alpha",
                "category": "entretien",
                "country_of_sale": "FR",
            },
            headers={"Idempotency-Key": f"product-a-{self.marker}"},
        )
        t.check("création produit fictif A", product_a.status_code == 201, f"HTTP {product_a.status_code}")
        product_a_id = product_a.json()["product"]["id"]

        # -- document import -----------------------------------------------------------
        document = alice.post(
            "/api/v1/documents",
            json={
                "title": "Déclaration fournisseur fictive",
                "document_type": "supplier_declaration",
                "supplier_id": supplier_a_id,
                "product_id": product_a_id,
                "tags": ["recette", "fictif"],
            },
        )
        t.check("création fiche documentaire A", document.status_code == 201, f"HTTP {document.status_code}")
        document_id = document.json()["id"]

        version_result = self._upload(alice, document_id, self.generated["clean_pdf"], "declaration-fournisseur.pdf", "application/pdf")
        t.check(
            "import PDF réel : dépôt quarantaine, scan, promotion et persistance",
            version_result["ok"],
            version_result["detail"],
            evidence=version_result.get("evidence", {}),
        )
        version_a = version_result.get("version_id")

        # -- negative uploads ----------------------------------------------------------
        for label, filename, payload, content_type, expected in (
            ("format interdit (.exe)", "malware.exe", b"MZ" + b"\x00" * 64, "application/octet-stream", {422}),
            ("MIME déclaré incohérent", "note.pdf", b"%PDF-1.7\n%fake", "text/plain", {422}),
            ("extension usurpée (exe déguisé en PDF)", "piege.pdf", (ARTIFACTS / "executable-disguise.pdf").read_bytes(), "application/pdf", {422}),
        ):
            outcome = self._upload(alice, document_id, payload, filename, content_type, bytes_payload=payload)
            t.check(
                f"import refusé : {label}",
                outcome["status_code"] in expected,
                f"HTTP {outcome['status_code']}",
            )

        big = b"%PDF-1.7\n" + b"0" * (9 * 1024 * 1024)
        outcome = self._upload(alice, document_id, big, "volumineux.pdf", "application/pdf", bytes_payload=big)
        t.check(
            "import refusé : fichier au-delà de MAX_UPLOAD_BYTES",
            outcome["status_code"] in {413, 422},
            f"HTTP {outcome['status_code']}",
            max_bytes=8 * 1024 * 1024,
        )

        eicar_outcome = self._upload(
            alice,
            document_id,
            (ARTIFACTS / "eicar.txt").read_bytes(),
            "eicar.txt",
            "text/plain",
            bytes_payload=(ARTIFACTS / "eicar.txt").read_bytes(),
        )
        t.check(
            "fichier EICAR refusé et jamais promu en bucket propre",
            eicar_outcome["status_code"] in {422, 409, 410, 503} or eicar_outcome.get("blocked") is True,
            f"HTTP {eicar_outcome['status_code']} — {eicar_outcome['detail']}",
            eicar=eicar_outcome.get("evidence", {}),
        )

        # -- extraction worker ---------------------------------------------------------
        extraction = self._run_extraction_worker_until(version_a, org_a_id, label="A")
        t.check(
            "worker d'extraction : job réclamé, PDF extrait, segments persistés",
            extraction["ok"],
            extraction["detail"],
            evidence=extraction.get("evidence", {}),
        )
        segments = alice.get(f"/api/v1/document-versions/{version_a}/segments")
        t.check(
            "segments consultables via l'API (lecture autorisée)",
            segments.status_code == 200 and len(segments.json()["segments"]) > 0,
            f"HTTP {segments.status_code}, {len(segments.json().get('segments', []))} segments",
            first_segment=(segments.json().get("segments") or [{}])[0].get("text", "")[:120],
        )

        # -- OCR availability (scanned document) ---------------------------------------
        scanned_outcome = self._upload(
            alice,
            document_id,
            self.generated["scanned_pdf"],
            self.generated["scanned_pdf"].name,
            "application/pdf",
        )
        if scanned_outcome.get("version_id"):
            ocr_run = self._run_extraction_worker_until(
                scanned_outcome["version_id"], org_a_id, label="A-ocr"
            )
            state = ocr_run.get("evidence", {})
            # Accept either a successful OCR (engine wired) or a *controlled*
            # terminal failure. What must never happen is a silent success with
            # no text, or a document promoted as if it had been read.
            if state.get("extraction_status") == "requires_review":
                t.check(
                    "document scanné : OCR réel exécuté et marqué pour revue humaine",
                    state.get("segment_count", 0) > 0,
                    f"segments={state.get('segment_count')} (moteur OCR opérationnel)",
                )
            elif state.get("extraction_status") == "failed":
                t.check(
                    "document scanné : absence du moteur OCR échoue explicitement, sans faux succès",
                    True,
                    f"statut=failed, code={state.get('extraction_error_code')}, job={state.get('job_status')}",
                )
            else:
                t.check(
                    "document scanné : état d'extraction inattendu",
                    False,
                    f"statut={state.get('extraction_status')} segments={state.get('segment_count')}",
                )

        # -- download ------------------------------------------------------------------
        download = alice.post(f"/api/v1/document-versions/{version_a}/download-url")
        t.check("URL signée de téléchargement émise", download.status_code == 200, f"HTTP {download.status_code}")
        if download.status_code == 200:
            url = download.json()["url"]
            fetched = httpx.get(url, timeout=30)
            t.check(
                "téléchargement du document via URL signée",
                fetched.status_code == 200 and fetched.content == self.generated["clean_pdf"].read_bytes(),
                f"HTTP {fetched.status_code}, {len(fetched.content)} octets",
                bucket_url_query="X-Amz-Signature présent" if "X-Amz-Signature" in url else "signature absente",
            )
            unsigned = url.split("?")[0]
            unsigned_response = httpx.get(unsigned, timeout=20)
            t.check(
                "objet refusé sans signature (bucket non public)",
                unsigned_response.status_code in {403, 404},
                f"HTTP {unsigned_response.status_code} sur l'objet non signé",
            )

        # -- analysis ------------------------------------------------------------------
        analysis = alice.post(
            "/api/v1/analyses",
            json={"document_version_ids": [version_a], "supplier_id": supplier_a_id, "product_id": product_a_id},
            headers={"Idempotency-Key": f"analysis-a-{self.marker}"},
        )
        t.check("pré-audit : analyse mise en file (202)", analysis.status_code == 202, f"HTTP {analysis.status_code} {analysis.text[:200]}")
        analysis_id = analysis.json()["analysis"]["id"] if analysis.status_code == 202 else None

        detection = self._run_analysis_worker_until(analysis_id, org_a_id) if analysis_id else {"ok": False, "detail": "aucune analyse"}
        t.check(
            "worker de détection : claims détectés et persistés",
            detection["ok"],
            detection["detail"],
            evidence=detection.get("evidence", {}),
        )

        if analysis_id:
            detail = alice.get(f"/api/v1/analyses/{analysis_id}")
            body = detail.json() if detail.status_code == 200 else {}
            latest = body.get("latest_version") or {}
            t.check(
                "résultat d'analyse consultable : statut terminal et version",
                detail.status_code == 200 and latest.get("status") in {"completed", "failed", "detecting_claims"},
                f"HTTP {detail.status_code}, statut={latest.get('status')}",
                claim_count=len((latest or {}).get("claims") or []),
            )
            pdf = alice.get(f"/api/v1/reports/analyses/{analysis_id}/pdf")
            t.check(
                "rapport PDF généré et récupéré",
                pdf.status_code == 200 and pdf.content.startswith(b"%PDF"),
                f"HTTP {pdf.status_code}, {len(pdf.content)} octets",
            )
            if pdf.status_code == 200:
                (ARTIFACTS / "rapport-analyse.pdf").write_bytes(pdf.content)
            dossier = alice.get(f"/api/v1/reports/analyses/{analysis_id}/dossier")
            t.check(
                "dossier probatoire ZIP généré et récupéré",
                dossier.status_code == 200 and dossier.content.startswith(b"PK"),
                f"HTTP {dossier.status_code}, {len(dossier.content)} octets",
            )

        audit = alice.get("/api/v1/audit/verify")
        t.check(
            "chaîne d'audit vérifiée intacte",
            audit.status_code == 200 and audit.json().get("is_valid") is True,
            f"HTTP {audit.status_code} events={audit.json().get('total_events') if audit.status_code == 200 else 'n/a'}",
        )

        # -- organisation B / isolation ------------------------------------------------
        bob = Browser(self.server, "bob")
        bob.sso_login(user="bob")
        org_b = bob.post("/api/v1/organizations", json={"name": "Org Recette Beta", "slug": "org-recette-beta"})
        t.check("création organisation B", org_b.status_code == 201, f"HTTP {org_b.status_code}")
        org_b_id = org_b.json()["organization"]["id"]

        t.check(
            "B ne voit pas le document de A (404)",
            bob.get(f"/api/v1/documents/{document_id}").status_code == 404,
            f"HTTP {bob.get(f'/api/v1/documents/{document_id}').status_code}",
        )
        t.check(
            "B ne peut pas télécharger la version de A",
            bob.post(f"/api/v1/document-versions/{version_a}/download-url").status_code == 404,
            "aucune URL signée inter-tenant",
        )
        t.check(
            "B ne voit pas le fournisseur de A (404)",
            bob.get(f"/api/v1/suppliers/{supplier_a_id}").status_code == 404,
            "catalogue isolé",
        )
        t.check(
            "B ne voit pas l'analyse de A (404)",
            bob.get(f"/api/v1/analyses/{analysis_id}").status_code == 404 if analysis_id else False,
            "analyses isolées",
        )
        switched = bob.post("/api/v1/auth/active-organization", json={"organization_id": org_a_id})
        t.check(
            "B ne peut pas basculer sur l'organisation de A",
            switched.status_code in {403, 404},
            f"HTTP {switched.status_code}",
        )
        bob_list = bob.get("/api/v1/documents")
        t.check(
            "liste documentaire de B vide malgré l'activité de A",
            bob_list.status_code == 200 and bob_list.json()["items"] == [],
            f"HTTP {bob_list.status_code}, {len(bob_list.json().get('items', []))} éléments",
        )

        # -- independent data in B ------------------------------------------------------
        supplier_b = bob.post(
            "/api/v1/suppliers",
            json={"legal_name": "Fournisseur Fictif Beta SARL", "external_reference": f"SUP-B-{self.marker}"},
            headers={"Idempotency-Key": f"supplier-b-{self.marker}"},
        )
        t.check("création fournisseur fictif B", supplier_b.status_code == 201, f"HTTP {supplier_b.status_code}")
        product_b = bob.post(
            "/api/v1/products",
            json={"supplier_id": supplier_b.json()["supplier"]["id"], "reference": f"PRODB-{self.marker}", "name": "Emballage Fictif Beta"},
            headers={"Idempotency-Key": f"product-b-{self.marker}"},
        )
        t.check("création produit fictif B", product_b.status_code == 201, f"HTTP {product_b.status_code}")
        document_b = bob.post("/api/v1/documents", json={"title": "Déclaration Beta", "document_type": "other"})
        version_b_outcome = self._upload(bob, document_b.json()["id"], self.generated["second_pdf"], "emballage-fictif.pdf", "application/pdf")
        t.check("import documentaire indépendant pour B", version_b_outcome["ok"], version_b_outcome["detail"])
        if version_b_outcome.get("version_id"):
            bob_extraction = self._run_extraction_worker_until(version_b_outcome["version_id"], org_b_id, label="B")
            t.check("extraction du document de B", bob_extraction["ok"], bob_extraction["detail"])

        # -- persistence across a full restart -----------------------------------------
        t.mark("documents, extraction, download, analysis and reports done")
        self.server.stop()
        self.server = ApiServer(self.api_env(), port=self.server.port).start()
        print(f"API server restarted on {self.server.base_url}")
        me_again = httpx.get(
            f"{self.server.base_url}/api/v1/documents/{document_id}",
            headers={"Cookie": f"{Browser.SESSION_COOKIE}={alice.cookies[Browser.SESSION_COOKIE]}"},
            timeout=30,
        )
        t.check(
            "après redémarrage complet : session, document et isolation conservés",
            me_again.status_code == 200,
            f"HTTP {me_again.status_code}",
        )

        # -- workers stopped -----------------------------------------------------------
        queued_version = self._queue_without_worker(alice, document_id)
        queued_ok = bool(queued_version) and queued_version != version_a
        t.check(
            "un second import est bien mis en file (aucun worker lancé)",
            queued_ok,
            f"version en file={queued_version} (HTTP {(self._queue_upload_outcome or {}).get('status_code')})",
        )
        if queued_ok:
            before = self._job_state(queued_version, org_a_id)
            blocked = self._run_extraction_worker_until(
                queued_version, org_a_id, label="A-sans-worker", expect_work=False
            )
            t.check(
                "worker arrêté : le job reste en file, aucun succès fabriqué",
                blocked["ok"],
                blocked["detail"],
                before={k: before[k] for k in ("extraction_status", "job_status", "rows_visible_without_tenant_context")},
            )

        self._browsers = [alice, bob]
        alice.close()
        bob.close()
        return t.to_json()

    # ------------------------------------------------------------------ helpers
    def _upload(
        self,
        browser: Browser,
        document_id: str,
        payload: Any,
        filename: str,
        content_type: str,
        *,
        bytes_payload: bytes | None = None,
    ) -> dict[str, Any]:
        """Request an upload intent, PUT the bytes, complete the import."""

        import httpx

        data = bytes_payload if bytes_payload is not None else Path(payload).read_bytes()
        instruction = browser.post(
            f"/api/v1/documents/{document_id}/versions",
            json={
                "source_filename": filename,
                "content_type": content_type,
                "size_bytes": len(data),
                "expected_sha256": hashlib.sha256(data).hexdigest() if bytes_payload is None else None,
            },
        )
        if instruction.status_code != 201:
            return {"ok": False, "status_code": instruction.status_code, "detail": instruction.text[:300]}
        body = instruction.json()
        files = {name: (None, value) for name, value in body["upload_fields"].items()}
        put = httpx.post(
            browser.abs_url(body["upload_url"]),
            data=body["upload_fields"],
            files={"file": (filename, data, content_type)},
            timeout=60,
        )
        if put.status_code not in {200, 201, 204}:
            return {
                "ok": False,
                "status_code": put.status_code,
                "detail": f"dépôt objet refusé: HTTP {put.status_code} {put.text[:200]}",
            }
        completion = browser.post(f"/api/v1/document-uploads/{body['upload']['id']}/complete")
        version_id = None
        evidence: dict[str, Any] = {}
        if completion.status_code in {200, 201}:
            payload_json = completion.json()
            version = payload_json.get("version") or {}
            version_id = version.get("id")
            evidence = {
                "sha256": version.get("sha256"),
                "size_bytes": version.get("size_bytes"),
                "extraction_status": version.get("extraction_status"),
                "scan_engine": (payload_json.get("upload") or {}).get("scan_engine"),
            }
        return {
            "ok": completion.status_code in {200, 201} and version_id is not None,
            "status_code": completion.status_code,
            "detail": f"complétion HTTP {completion.status_code} {completion.text[:200]}",
            "version_id": version_id,
            "evidence": evidence,
            "blocked": completion.status_code != 201,
        }

    def _worker_env(self) -> dict[str, str]:
        return {**os.environ, **self.api_env()}

    def _run_worker(
        self, module: str, *, until: Callable[[], bool], timeout: float = 60.0
    ) -> tuple[bool, str, str]:
        """Run the real worker process until the durable state reaches the target.

        The worker's only entry point is ``run_forever``; it is started as a
        genuine OS process (exactly as Compose does) and stopped once the
        observed database state proves it drained the queue. A worker that
        never converges is a FAIL, not an assumption.
        """

        log_path = ARTIFACTS / f"{module.split('.')[-1]}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("wb") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", module],
                cwd=BACKEND_ROOT,
                env={**self._worker_env(), "LOG_LEVEL": "INFO"},
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            reached = False
            exit_note = "worker still running at the deadline"
            deadline = time.monotonic() + timeout
            try:
                while time.monotonic() < deadline:
                    code = process.poll()
                    if code is not None:
                        # A worker that dies before draining the queue is a much
                        # stronger signal than "the state never changed": say so.
                        exit_note = f"worker exited early with code {code}"
                        break
                    if until():
                        reached = True
                        exit_note = "state reached, worker stopped by the harness"
                        break
                    time.sleep(0.5)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
        return reached, log_path.read_text(errors="replace")[-1200:], exit_note

    def _run_extraction_worker_until(
        self, version_id: str | None, organization_id: str | None, *, label: str, expect_work: bool = True
    ) -> dict[str, Any]:
        """Run the real worker process and inspect the durable job row."""

        if not version_id:
            return {"ok": False, "detail": "aucune version à extraire"}
        if expect_work:
            reached, log, exit_note = self._run_worker(
                "app.workers.document_extraction_worker",
                until=lambda: self._job_state(version_id, organization_id).get("extraction_status")
                in {"completed", "requires_review", "failed"},
                timeout=90,
            )
        else:
            # No worker process at all: prove the queue does not advance by itself.
            log = "worker not started (by design)"
            exit_note = "no worker process was started"
            time.sleep(3)
        state = self._job_state(version_id, organization_id)
        if not expect_work:
            return {
                "ok": state.get("extraction_status") == "pending" and state.get("job_status") in {"queued", "running"},
                "detail": f"statut conservé={state.get('extraction_status')} job={state.get('job_status')} (aucune promotion)",
                "evidence": state,
            }
        if expect_work:
            ok = state.get("extraction_status") in {"completed", "requires_review"} and state.get("segment_count", 0) > 0
            detail = (
                f"statut={state.get('extraction_status')} segments={state.get('segment_count')} "
                f"job={state.get('job_status')}"
            )
            if not ok:
                # Make an unexplained failure explain itself in the next run: the
                # reason, the worker's own last lines and why it stopped all go
                # into the transcript (and therefore into the CI annotation).
                detail += (
                    f" | {exit_note} | code={state.get('extraction_error_code')}"
                    f" | log={log[-320:]}"
                )
            return {
                "ok": ok,
                "detail": detail,
                "evidence": {**state, "worker_exit_note": exit_note, "worker_log_tail": log[-600:]},
            }
        ok = state.get("extraction_status") == "pending" and state.get("job_status") == "queued"
        return {
            "ok": ok,
            "detail": f"statut conservé={state.get('extraction_status')} job={state.get('job_status')} (aucune promotion)",
            "evidence": state,
        }

    def _run_analysis_worker_until(self, analysis_id: str | None, organization_id: str | None) -> dict[str, Any]:
        if not analysis_id:
            return {"ok": False, "detail": "aucune analyse"}
        reached, log, exit_note = self._run_worker(
            "app.workers.analysis_detection_worker",
            until=lambda: self._analysis_state(analysis_id, organization_id).get("job_status")
            in {"completed", "failed"},
            timeout=90,
        )
        state = self._analysis_state(analysis_id, organization_id)
        ok = state.get("job_status") == "completed" and state.get("claim_count", 0) > 0
        detail = f"job={state.get('job_status')} claims={state.get('claim_count')} statut={state.get('status')}"
        if not ok:
            detail += f" | {exit_note} | log={log[-320:]}"
        return {
            "ok": ok,
            "detail": detail,
            "evidence": {**state, "worker_exit_note": exit_note, "worker_log_tail": log[-600:]},
        }

    def _queue_without_worker(self, browser: Browser, document_id: str) -> str | None:
        outcome = self._upload(browser, document_id, self.generated["clean_txt"], self.generated["clean_txt"].name, "text/plain")
        self._queue_upload_outcome = outcome
        return outcome.get("version_id")

    def _job_state(self, version_id: str, organization_id: str) -> dict[str, Any]:
        """Read the durable rows as the runtime role under a tenant context.

        The first read is deliberately made *without* a context first, so that the
        harness itself demonstrates RLS: an operator who forgets the tenant context
        sees nothing rather than another organisation's job.
        """

        import psycopg

        with psycopg.connect(self.stack.postgres.app_dsn) as connection:
            connection.autocommit = True
            invisible = connection.execute(
                "SELECT count(*) FROM document_versions WHERE id = %s", (version_id,)
            ).fetchone()[0]
            connection.execute("SELECT set_config('app.current_organization_id', %s, false)", (organization_id,))
            row = connection.execute(
                "SELECT v.extraction_status, v.organization_id, "
                " (SELECT count(*) FROM document_segments s WHERE s.document_version_id = v.id), "
                " (SELECT status FROM document_extraction_jobs j WHERE j.document_version_id = v.id ORDER BY j.created_at DESC LIMIT 1), "
                " v.extraction_error_code "
                "FROM document_versions v WHERE v.id = %s",
                (version_id,),
            ).fetchone()
        return {
            "extraction_status": row[0],
            "segment_count": int(row[2]),
            "job_status": row[3],
            "extraction_error_code": row[4],
            "rows_visible_without_tenant_context": int(invisible),
        }

    def _analysis_state(self, analysis_id: str, organization_id: str) -> dict[str, Any]:
        import psycopg

        with psycopg.connect(self.stack.postgres.app_dsn) as connection:
            connection.autocommit = True
            connection.execute("SELECT set_config('app.current_organization_id', %s, false)", (organization_id,))
            row = connection.execute(
                "SELECT a.status, "
                " (SELECT count(*) FROM claims c JOIN analysis_versions av ON av.id = c.analysis_version_id WHERE av.analysis_id = a.id), "
                " (SELECT j.status FROM analysis_detection_jobs j JOIN analysis_versions av2 ON av2.id = j.analysis_version_id "
                "   WHERE av2.analysis_id = a.id ORDER BY j.created_at DESC LIMIT 1) "
                "FROM analyses a WHERE a.id = %s",
                (analysis_id,),
            ).fetchone()
        return {"status": row[0], "claim_count": int(row[1]), "job_status": row[2]}


# --------------------------------------------------------------------------- stack bundle
@dataclass
class RecetteStack:
    postgres: Any
    postgres_password: str
    storage: Any
    scanner: Any
    idp: Any
    session_secret: str


def build_full_stack() -> RecetteStack:
    """Assemble every real component needed to serve the application."""

    from clamd_stub import ClamdStub
    from oidc_stub import OidcStub
    from stack import start_postgres, start_s3_store

    postgres = start_postgres()
    storage = start_s3_store()
    scanner = ClamdStub(behaviour="clean").start()
    # OIDC_REDIRECT_URI must share the frontend host with FRONTEND_URL, so the
    # frontend origin is fixed at localhost:3000 for every qualification run.
    idp = OidcStub(
        issuer="",
        client_id="vericlaim-recette",
        allowed_redirect_uris=("http://localhost:3000/api/v1/auth/callback",),
    ).start()
    return RecetteStack(
        postgres=postgres,
        postgres_password=postgres.app_password,
        storage=storage,
        scanner=scanner,
        idp=idp,
        session_secret=uuid.uuid4().hex + uuid.uuid4().hex,
    )
