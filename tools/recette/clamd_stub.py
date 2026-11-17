"""A clamd INSTREAM listener for the recette harness — **not** an antivirus engine.

Why this exists
---------------
VeriClaim talks to ClamAV over the clamd INSTREAM protocol (``app.documents.scanner``).
A real ClamAV daemon cannot be installed in this sandbox: ``libclamav``, the
signature database (``freshclam``) and the daemon are absent and cannot be
fetched here.  Without *some* listener on the socket, ``DOCUMENT_SCANNER_MODE=clamav``
would only ever prove the fail-closed path.

This module therefore implements the **wire protocol** so that the real client
code — framing, chunking, deadline handling, NUL-terminated reply parsing,
ambiguous-reply refusal, connection teardown — is exercised against a real
socket.  Its verdict logic is deliberately trivial and only recognises the EICAR
test string.

What it does NOT prove
----------------------
There is no signature database here.  A verdict from this listener is **not**
evidence that ClamAV would detect anything, and "antivirus operational" MUST NOT
be claimed from these runs.  It is recorded in
``docs/infrastructure/06-antivirus-et-fichiers.md`` as a hard limitation and a
release blocker for any pilot that ingests third-party documents.
"""

from __future__ import annotations

import socketserver
import struct
import threading
from dataclasses import dataclass, field

EICAR_MARKER = b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE"
_CHUNK = 64 * 1024


@dataclass
class ClamdStub:
    """A clamd-compatible INSTREAM endpoint with switchable behaviour."""

    host: str = "127.0.0.1"
    #: ``clean`` replies OK, ``eicar`` answers FOUND, ``error`` answers a clamd
    #: error frame, ``ambiguous`` sends two frames, ``slow`` stalls past any
    #: reasonable deadline, ``close`` drops the connection without a verdict.
    behaviour: str = "clean"
    port: int = field(default=0, init=False)
    scans: list[dict[str, object]] = field(default_factory=list, init=False)
    _server: socketserver.ThreadingTCPServer | None = field(default=None, init=False, repr=False)
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)

    def start(self) -> "ClamdStub":
        stub = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:  # noqa: D401 - stdlib contract
                stub._serve(self.request)

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self._server = Server((self.host, 0), Handler)
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(target=self._server.serve_forever, name="clamd-stub", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _serve(self, connection: object) -> None:
        connection.settimeout(10)
        command = b""
        while not command.endswith(b"\0"):
            byte = connection.recv(1)
            if not byte:
                return
            command += byte
        if not command.startswith(b"zINSTREAM\0"[: len(command)]) or b"INSTREAM" not in command:
            connection.sendall(b"UNKNOWN COMMAND\0")
            return

        payload = bytearray()
        while True:
            header = _recv_exactly(connection, 4)
            if header is None:
                return
            (length,) = struct.unpack("!I", header)
            if length == 0:
                break
            chunk = _recv_exactly(connection, length)
            if chunk is None:
                return
            payload.extend(chunk)

        self.scans.append({"size_bytes": len(payload), "behaviour": self.behaviour})

        if self.behaviour == "close":
            return
        if self.behaviour == "slow":
            # Hold the socket open past the client's deadline: no verdict.
            threading.Event().wait(30)
            return
        if self.behaviour == "ambiguous":
            connection.sendall(b"stream: OK\0stream: OK\0")
            return
        if self.behaviour == "error":
            connection.sendall(b"INSTREAM size limit exceeded. ERROR\0")
            return
        if self.behaviour == "eicar" or EICAR_MARKER in payload:
            connection.sendall(b"stream: Eicar-Test-Signature FOUND\0")
            return
        connection.sendall(b"stream: OK\0")


def _recv_exactly(connection: object, count: int) -> bytes | None:
    buffer = b""
    while len(buffer) < count:
        chunk = connection.recv(count - len(buffer))
        if not chunk:
            return None
        buffer += chunk
    return buffer


#: The canonical EICAR test file. Harmless by construction: it is a printable
#: string, not executable code, and exists only so scanners can be verified.
EICAR_TEST_FILE = (
    b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
)
