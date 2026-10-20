"""Protocol tests with a real local TCP peer, NOT a ClamAV engine/signature proof."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import socket
import struct
import time

import pytest

from app.documents.scanner import ClamAvMalwareScanner, MalwareScanError, MalwareScannerUnavailableError


def receive_exact(connection, size):
    payload = bytearray()
    while len(payload) < size:
        chunk = connection.recv(size - len(payload))
        if not chunk:
            raise AssertionError("client truncated INSTREAM")
        payload.extend(chunk)
    return bytes(payload)


@contextmanager
def protocol_peer(response, *, stall=0):
    with socket.socket() as listener, ThreadPoolExecutor(max_workers=1) as pool:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(3)

        def serve():
            with listener.accept()[0] as connection:
                connection.settimeout(3)
                assert receive_exact(connection, 10) == b"zINSTREAM\0"
                received = bytearray()
                while True:
                    size = struct.unpack("!I", receive_exact(connection, 4))[0]
                    if size == 0:
                        break
                    received.extend(receive_exact(connection, size))
                time.sleep(stall)
                try:
                    connection.sendall(response)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # expected when the client rejects a timeout/oversize
                return bytes(received)

        result = pool.submit(serve)
        scanner = ClamAvMalwareScanner(host="127.0.0.1", port=listener.getsockname()[1], timeout_seconds=1)
        yield scanner, result
        result.result(timeout=4)


@pytest.mark.parametrize("response", [
    b"stream: OK",  # missing mandatory z-protocol terminator
    b"not-a-clamd-response OK\0",
    b"stream: ERROR OK\0",
    b"stream: OK\n\0",
    b"stream: OK\0stream: Eicar FOUND\0",
    b"stream: " + b"x" * (16 * 1024) + b" OK\0",
    b"stream: \xff FOUND\0",
    b"stream: size limit exceeded ERROR\0",
    b"",
])
def test_uncertain_verdict_never_becomes_clean(response):
    with protocol_peer(response) as (scanner, _):
        with pytest.raises(MalwareScanError):
            scanner.scan(b"untrusted bytes")


@pytest.mark.parametrize("response,clean", [(b"stream: OK\0", True), (b"stream: Eicar-Test-Signature FOUND\0", False)])
def test_instream_roundtrip_and_definitive_verdicts(response, clean):
    payload = b"x" * (64 * 1024 + 3)
    with protocol_peer(response) as (scanner, received):
        assert scanner.scan(payload).is_clean is clean
        assert received.result(timeout=3) == payload


def test_scanner_timeout_is_unavailable_not_clean():
    with protocol_peer(b"stream: OK\0", stall=0.15) as (scanner, _):
        scanner._timeout_seconds = 0.05
        with pytest.raises(MalwareScannerUnavailableError):
            scanner.scan(b"untrusted bytes")


def test_response_deadline_is_total_not_renewed_per_fragment(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("app.documents.scanner.time.monotonic", lambda: clock[0])

    class SlowPeer:
        def settimeout(self, timeout):
            assert timeout > 0

        def recv(self, size):
            clock[0] += 0.6
            return b"s"  # every read arrives within a one-second socket timeout

    with pytest.raises(socket.timeout):
        ClamAvMalwareScanner._read_response(SlowPeer(), deadline=1.0)
    assert clock[0] == 1.2


def test_fragmented_clean_verdict_is_accepted():
    class FragmentedPeer:
        chunks = iter([b"str", b"eam: ", b"OK", b"\0"])

        def settimeout(self, timeout):
            pass

        def recv(self, size):
            return next(self.chunks)

    assert ClamAvMalwareScanner._read_response(FragmentedPeer(), deadline=time.monotonic() + 1) == "stream: OK"


def test_invalid_tcp_verdict_leaves_upload_unavailable_not_clean():
    from fastapi.testclient import TestClient
    from tests.auth_support import authenticate_client
    from tests.test_secure_documents import _install_document_fakes, _create_document, _request_upload

    app, storage, _, old_storage, old_scanner = _install_document_fakes()
    try:
        with protocol_peer(b"not-a-verdict OK\0") as (scanner, _), TestClient(app) as client:
            app.state.document_scanner = scanner
            authenticate_client(client, role_code="analyst")
            document = _create_document(client)
            instruction = _request_upload(client, document["id"], filename="scan.txt", payload=b"text")
            storage.upload_latest(payload=b"text")
            response = client.post(f"/api/v1/document-uploads/{instruction['upload']['id']}/complete")
            assert response.status_code == 503, response.text
            assert response.json()["upload"]["status"] == "failed"
            assert response.json()["upload"]["scan_error_code"] == "scanner_error"
            assert response.json()["version"] is None
            assert not storage.copy_requests
    finally:
        app.state.document_storage = old_storage
        app.state.document_scanner = old_scanner
