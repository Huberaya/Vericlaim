"""Malware-scanning boundary for untrusted document bytes.

A document is never handed to a PDF/image/OCR parser before this boundary has
returned a clean result.  ClamAV is used through its documented clamd INSTREAM
protocol so no temporary shared file is needed.
"""

from __future__ import annotations

import socket
import time
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
        deadline = time.monotonic() + self._timeout_seconds
        try:
            with socket.create_connection((self._host, self._port), timeout=self._timeout_seconds) as connection:
                connection.settimeout(self._remaining_timeout(deadline))
                connection.sendall(b"zINSTREAM\0")
                for start in range(0, len(payload), 64 * 1024):
                    chunk = payload[start : start + 64 * 1024]
                    connection.settimeout(self._remaining_timeout(deadline))
                    connection.sendall(struct.pack("!I", len(chunk)))
                    connection.sendall(chunk)
                connection.settimeout(self._remaining_timeout(deadline))
                connection.sendall(struct.pack("!I", 0))
                response = self._read_response(connection, deadline=deadline)
        except OSError as exc:
            raise MalwareScannerUnavailableError("Le service antivirus est indisponible.") from exc

        # A clamd response is e.g. "stream: OK" or
        # "stream: Eicar-Test-Signature FOUND". Never return the signature to
        # callers: it is recorded only as a generic security rejection.
        if response.startswith("stream: ") and response.endswith(" FOUND") and response[8:-6].strip():
            return MalwareScanResult(is_clean=False, engine="clamav")
        if response == "stream: OK":
            return MalwareScanResult(is_clean=True, engine="clamav")
        raise MalwareScanError("Le service antivirus a retourné une réponse invalide.")

    @staticmethod
    def _remaining_timeout(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise socket.timeout("Antivirus scan deadline exceeded")
        return remaining

    @staticmethod
    def _read_response(connection: socket.socket, *, deadline: float) -> str:
        # zINSTREAM requires one NUL-terminated reply. EOF, an overlong reply,
        # decoding errors and ambiguous frames are not a trustworthy verdict.
        payload = bytearray()
        limit = 16 * 1024
        while len(payload) < limit:
            connection.settimeout(ClamAvMalwareScanner._remaining_timeout(deadline))
            chunk = connection.recv(min(4096, limit - len(payload)))
            if not chunk:
                raise MalwareScanError("Le service antivirus a interrompu son verdict.")
            payload.extend(chunk)
            if b"\0" in chunk:
                frame, _, trailing = bytes(payload).partition(b"\0")
                if trailing:
                    raise MalwareScanError("Le service antivirus a retourné une réponse ambiguë.")
                try:
                    response = frame.decode("utf-8", errors="strict")
                except UnicodeDecodeError as exc:
                    raise MalwareScanError("Le verdict antivirus est mal encodé.") from exc
                if not response or any(ord(char) < 32 or ord(char) == 127 for char in response):
                    raise MalwareScanError("Le verdict antivirus contient des caractères invalides.")
                # Outside IDSESSION, clamd closes the connection after its single
                # reply. TCP boundaries are arbitrary: a second frame may arrive in
                # another recv(), not in `trailing` above. Require EOF within the
                # same total deadline; a peer that stalls or sends more is unknown.
                connection.settimeout(ClamAvMalwareScanner._remaining_timeout(deadline))
                if connection.recv(1):
                    raise MalwareScanError("Le service antivirus a retourné une réponse ambiguë.")
                return response
        raise MalwareScanError("Le verdict antivirus dépasse la taille autorisée.")


def build_malware_scanner(settings: Settings) -> MalwareScanner:
    if settings.document_scanner_mode == "clamav":
        # Une validation par `assert` disparaîtrait sous `python -O` et laisserait
        # construire un client sans hôte: le refus doit être une branche du programme.
        if not settings.document_clamav_host:
            raise MalwareScannerUnavailableError(
                "Le mode antivirus clamav exige un hôte; aucun scan ne sera simulé."
            )
        if settings.document_clamav_timeout_seconds <= 0:
            raise MalwareScannerUnavailableError(
                "Le délai antivirus doit être strictement positif; aucun scan ne sera simulé."
            )
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
