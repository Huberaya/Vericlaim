"""Malware-scanning boundary for untrusted document bytes.

A document is never handed to a PDF/image/OCR parser before this boundary has
returned a clean result.  ClamAV is used through its documented clamd INSTREAM
protocol so no temporary shared file is needed.
"""

from __future__ import annotations

import logging
import socket
import struct
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger("vericlaim.documents.scanner")

from app.core.config import Settings  # noqa: E402 — kept next to the builders below


class MalwareScanError(RuntimeError):
    """The scanner could not issue a trustworthy verdict."""


class MalwareScannerUnavailableError(MalwareScanError):
    """No scanner is configured or the configured scanner cannot be reached."""


@dataclass(frozen=True)
class MalwareScanResult:
    is_clean: bool
    engine: str
    signature_version: str | None = None


class MalwareScanner(Protocol):
    def scan(self, payload: bytes) -> MalwareScanResult: ...

    def probe(self) -> tuple[bool, str]:
        """Reachability probe used by /readyz. Never raises; returns a short code."""
        ...


class DisabledMalwareScanner:
    """Fail closed rather than parse an unscanned binary in normal environments."""

    def scan(self, payload: bytes) -> MalwareScanResult:
        del payload
        raise MalwareScannerUnavailableError("Le service antivirus documentaire n’est pas configuré.")

    def probe(self) -> tuple[bool, str]:
        return False, "scanner désactivé : aucun document ne peut être importé"


class TestCleanMalwareScanner:
    """Test-only deterministic scanner; never constructed outside APP_ENV=test."""

    def scan(self, payload: bytes) -> MalwareScanResult:
        # EICAR remains rejected even in tests so security-path regressions can
        # be exercised without a running clamd daemon.
        if b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE" in payload:
            return MalwareScanResult(is_clean=False, engine="test-eicar")
        return MalwareScanResult(is_clean=True, engine="test-only")

    def probe(self) -> tuple[bool, str]:
        # Readiness must not report a fake scanner as a real antivirus. The status is
        # `ok` because the configured scanner is reachable — `capabilities` and the
        # engine name say test-only, and this scanner cannot be built outside tests.
        return True, "scanner de test (aucun antivirus réel)"


class ClamAvMalwareScanner:
    """Small synchronous clamd client using the INSTREAM command."""

    def __init__(self, *, host: str, port: int, timeout_seconds: int) -> None:
        self._host = host
        self._port = port
        self._timeout_seconds = timeout_seconds

    def scan(self, payload: bytes) -> MalwareScanResult:
        try:
            with socket.create_connection((self._host, self._port), timeout=self._timeout_seconds) as connection:
                connection.settimeout(self._timeout_seconds)
                connection.sendall(b"zINSTREAM\0")
                for start in range(0, len(payload), 64 * 1024):
                    chunk = payload[start : start + 64 * 1024]
                    connection.sendall(struct.pack("!I", len(chunk)))
                    connection.sendall(chunk)
                connection.sendall(struct.pack("!I", 0))
                response = self._read_response(connection)
        except OSError as exc:
            raise MalwareScannerUnavailableError("Le service antivirus est indisponible.") from exc

        # A clamd response is e.g. "stream: OK" or
        # "stream: Eicar-Test-Signature FOUND". Never return the signature to
        # callers: it is recorded only as a generic security rejection.
        if response.endswith(" FOUND"):
            return MalwareScanResult(is_clean=False, engine="clamav")
        if response.endswith(" OK"):
            return MalwareScanResult(is_clean=True, engine="clamav")
        raise MalwareScanError("Le service antivirus a retourné une réponse invalide.")

    def probe(self) -> tuple[bool, str]:
        """Send the documented clamd PING and expect PONG.

        `PING` (command `zPING\0`) is the cheapest documented liveness check: it
        proves the daemon answers *and* that its signature database is loadable,
        which is what a document pipeline actually depends on.
        """
        try:
            with socket.create_connection(
                (self._host, self._port), timeout=min(self._timeout_seconds, 3)
            ) as connection:
                connection.settimeout(min(self._timeout_seconds, 3))
                connection.sendall(b"zPING\0")
                response = self._read_response(connection)
        except (OSError, MalwareScanError) as exc:
            logger.warning(
                "Antivirus readiness probe failed",
                extra={"event": "scanner_probe_failed", "error": type(exc).__name__},
            )
            return False, f"ping: {type(exc).__name__}"
        if response.upper() == "PONG":
            return True, "ping: pong"
        return False, "ping: réponse inattendue"

    @staticmethod
    def _read_response(connection: socket.socket) -> str:
        payload = bytearray()
        while len(payload) < 16 * 1024:
            chunk = connection.recv(4096)
            if not chunk:
                break
            payload.extend(chunk)
            if b"\0" in chunk:
                break
        if not payload:
            raise MalwareScanError("Le service antivirus n’a retourné aucun verdict.")
        return bytes(payload).split(b"\0", 1)[0].decode("utf-8", errors="replace").strip()


def build_malware_scanner(settings: Settings) -> MalwareScanner:
    if settings.document_scanner_mode == "clamav":
        assert settings.document_clamav_host is not None
        return ClamAvMalwareScanner(
            host=settings.document_clamav_host,
            port=settings.document_clamav_port,
            timeout_seconds=settings.document_clamav_timeout_seconds,
        )
    if settings.document_scanner_mode == "test":
        if settings.environment != "test":
            raise MalwareScannerUnavailableError("Le scanner de test est réservé à APP_ENV=test.")
        return TestCleanMalwareScanner()
    return DisabledMalwareScanner()
