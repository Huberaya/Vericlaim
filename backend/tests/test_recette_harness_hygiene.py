"""The qualification harness must not leak processes between runs.

Motivation (observation, 2026-11-12): ``RecetteRun.execute`` had no ``try/finally``
around the Uvicorn process. Every journey stopped the server on the happy path
only, so a run that raised left a live ``uvicorn`` behind — measured at **one
orphan per execution** across four runs. Orphaned servers keep a port bound and a
database connection open, which is exactly the kind of carried-over state that
makes a *later* run fail in a way that looks random.

These tests use the real ``ApiServer`` class (a real Uvicorn process on a real
port), because a mocked subprocess would not prove the property that matters.
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.version_info < (3, 10), reason="the harness targets the backend runtime"
)

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = REPO_ROOT / "tools" / "recette"


def _load_harness():
    """Import ``e2e_recette`` with the harness modules on ``sys.path``."""

    if str(HARNESS) not in sys.path:
        sys.path.insert(0, str(HARNESS))
    import e2e_recette

    return e2e_recette


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _probe_env(tmp_path: Path) -> dict[str, str]:
    return {
        "APP_ENV": "development",
        "DATABASE_URL": f"sqlite:///{tmp_path / 'leak.db'}",
        "AUTO_CREATE_SCHEMA": "true",
        "AUTH_SESSION_SECRET": "leak-test-secret-" + "a" * 48,
        "DOCUMENT_STORAGE_BACKEND": "disabled",
        "LOG_LEVEL": "WARNING",
    }


def test_stop_releases_the_port(tmp_path: Path) -> None:
    """After ``stop()`` the port must be bindable again, or the next run races it."""

    e2e = _load_harness()
    port = _free_port()
    server = e2e.ApiServer(_probe_env(tmp_path), port=port)
    server.start()
    assert server.running() is True

    server.stop()

    assert server.running() is False
    # ``stop`` already asserts this internally; re-binding proves it independently.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", port))


def test_failed_journey_does_not_leave_a_server_behind(tmp_path: Path, monkeypatch) -> None:
    """A journey that raises must still stop its server.

    Before the fix this left a live Uvicorn process per execution. The test starts
    a server through the *production* code path (``execute``'s ``finally``), raises
    inside the journey, and then checks the process is gone and the port is free.
    """

    e2e = _load_harness()
    started: list[int] = []

    def exploding_journey(self, httpx):  # signature mirrors the real hook
        self.server = e2e.ApiServer(_probe_env(tmp_path), port=_free_port()).start()
        started.append(self.server.port)
        self._browsers = []
        raise RuntimeError("injected mid-journey failure")

    monkeypatch.setattr(e2e.RecetteRun, "_execute_journey", exploding_journey)

    # ``execute`` needs a stack object only to hold references; the journey never
    # uses it here, so a minimal stand-in keeps the test fast and offline.
    class _Stack:
        postgres = None
        storage = None
        scanner = None
        idp = None
        session_secret = "x" * 64

    run = e2e.RecetteRun(_Stack(), marker="LEAK")
    with pytest.raises(RuntimeError, match="injected mid-journey failure"):
        run.execute()

    assert started, "the test never started a server; it proves nothing"
    assert run.server is None
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", started[0]))
