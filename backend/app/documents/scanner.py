"""Malware-scanning boundary for untrusted document bytes.

A document is never handed to a PDF/image/OCR parser before this boundary has
returned a clean result.  ClamAV is used through its documented clamd INSTREAM
protocol so no temporary shared file is needed.
"""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass
from typing import Protocol

from app.core.config import Settings


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


class DisabledMalwareScanner:
    """Fail closed rather than parse an unscanned binary in normal environments."""

    def scan(self, payload: bytes) -> MalwareScanResult:
        del payload
        raise MalwareScannerUnavailableError("Le service antivirus documentaire n’est pas configuré.")


class TestCleanMalwareScanner:
    """Test-only deterministic scanner; never constructed outside APP_ENV=test."""

    def scan(self, payload: bytes) -> MalwareScanResult:
        # EICAR remains rejected even in tests so security-path regressions can
        # be exercised without a running clamd daemon.
        if b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE" in payload:
            return MalwareScanResult(is_clean=False, engine="test-eicar")
        return MalwareScanResult(is_clean=True, engine="test-only")


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
