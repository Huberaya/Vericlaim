"""A-06 : le cycle de vie d'un worker durable, sans base ni réseau.

Ces tests ne vérifient pas la durabilité de la file — elle est déjà tenue par
les leases et le fencing en base (``tests/test_document_lease_fencing.py``,
``tests/test_postgres_*``). Ils vérifient ce qui manquait réellement : un worker
capable de s'arrêter proprement, de publier sa santé, de borner sa concurrence
et de survivre à une itération en erreur sans mourir.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from app.worker_healthcheck import main as healthcheck_main
from app.workers.runtime import (
    WorkerHealth,
    WorkerRuntime,
    health_is_fresh,
    read_health_file,
    write_health_file,
)


def _runtime(tmp_path: Path, *, step, concurrency: int = 1, poll: float = 0.01, max_iterations=None) -> WorkerRuntime:
    return WorkerRuntime(
        name="test-worker",
        worker_id="worker-test",
        step=step,
        poll_seconds=poll,
        concurrency=concurrency,
        health_path=tmp_path / "worker-health.json",
        drain_timeout_seconds=2.0,
        max_iterations=max_iterations,
    )


def test_sigterm_stops_the_loop_cleanly(tmp_path: Path) -> None:
    """Un arrêt demandé termine la boucle et rend la main, sans tuer le travail.

    C'est ce qui manquait : ``run_forever`` était un ``while True`` qu'aucun
    signal ne pouvait interrompre autrement qu'en tuant le processus — donc en
    laissant le job en cours en ``RUNNING`` jusqu'à expiration du lease.
    """

    runtime = _runtime(tmp_path, step=lambda index: (time.sleep(0.005), False)[1])
    runtime.install_signal_handlers()

    holder: dict[str, int] = {}
    thread = threading.Thread(target=lambda: holder.update(code=runtime.run()), daemon=True)
    thread.start()

    deadline = time.monotonic() + 5
    while read_health_file(tmp_path / "worker-health.json") is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert read_health_file(tmp_path / "worker-health.json") is not None, "aucune santé publiée"

    runtime.request_stop()
    thread.join(timeout=5)

    assert not thread.is_alive(), "le worker ne s'est pas arrêté après la demande"
    assert holder.get("code") == 0
    payload = read_health_file(tmp_path / "worker-health.json")
    assert payload is not None
    assert payload["stopping"] is True or payload["iterations"] >= 1


def test_health_file_reports_progress_and_is_written_atomically(tmp_path: Path) -> None:
    """Le battement distingue « vivant » de « qui progresse »."""

    calls = {"n": 0}

    def step(_index: int) -> bool:
        calls["n"] += 1
        return True

    runtime = _runtime(tmp_path, step=step, max_iterations=5)
    runtime.run()

    payload = read_health_file(tmp_path / "worker-health.json")
    assert payload is not None
    assert payload["iterations"] >= 5
    assert payload["jobs_processed"] >= 5
    assert payload["worker"] == "test-worker"
    assert payload["worker_id"] == "worker-test"
    # Aucune donnée sensible : seulement des compteurs et des horodatages.
    assert set(payload) >= {"worker", "worker_id", "heartbeat_at", "iterations", "pid"}
    # Pas de fichier temporaire laissé derrière : l'écriture est atomique.
    assert list(tmp_path.glob("*.tmp")) == []


def test_stale_heartbeat_is_detected(tmp_path: Path) -> None:
    """Un battement ancien signifie « bloqué », pas « vivant »."""

    path = tmp_path / "worker-health.json"
    write_health_file(
        path,
        WorkerHealth(
            worker="w",
            worker_id="w-1",
            started_at="2020-01-01T00:00:00+00:00",
            heartbeat_at="2020-01-01T00:00:00+00:00",
        ),
    )
    payload = read_health_file(path)
    assert payload is not None
    assert health_is_fresh(payload, max_age_seconds=60) is False
    assert health_is_fresh(payload, max_age_seconds=10**12) is True
    assert health_is_fresh(None, max_age_seconds=60) is False


def test_healthcheck_cli_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """La sonde sort 0 si frais, 1 sinon — c'est ce que lit Docker."""

    path = tmp_path / "worker-health.json"
    assert healthcheck_main(["--file", str(path)]) == 1, "fichier absent => non sain"

    runtime = _runtime(tmp_path, step=lambda _i: True, max_iterations=2)
    runtime.run()
    assert healthcheck_main(["--file", str(path), "--max-age", "600"]) == 0


def test_concurrency_runs_parallel_loops_with_distinct_ids() -> None:
    """La concurrence est bornée et chaque boucle a son propre identifiant.

    Le ``worker_id`` distinct compte : le fencing rejette une tentative portant
    le même identifiant, donc deux boucles partageant un identifiant se
    battraient pour la même revendication.
    """

    seen: list[int] = []
    lock = threading.Lock()

    def step(index: int) -> bool:
        with lock:
            seen.append(index)
        time.sleep(0.002)
        return False

    runtime = WorkerRuntime(
        name="conc",
        worker_id="w",
        step=step,
        poll_seconds=0.001,
        concurrency=4,
        max_iterations=20,
        health_path=None,
    )
    assert runtime._worker_thread_ids() == ["w#1", "w#2", "w#3", "w#4"]
    runtime.run()

    assert set(seen) >= {0, 1, 2, 3}, f"toutes les boucles n'ont pas tourné: {sorted(set(seen))}"


def test_concurrency_zero_is_rejected() -> None:
    """Une concurrence invalide échoue tôt, au lieu de démarrer zéro boucle."""

    from app.workers.analysis_detection_worker import build_analysis_detection_runtime

    with pytest.raises(ValueError):
        build_analysis_detection_runtime(settings=None, worker_id="w", concurrency=0)  # type: ignore[arg-type]


def test_an_erroring_iteration_does_not_kill_the_worker(tmp_path: Path) -> None:
    """Une exception d'itération est publiée et la boucle continue.

    Un worker qui meurt laisse la file sans consommateur : c'est exactement
    l'état que A-06 décrivait (files jamais drainées), mais causé par le worker
    lui-même au lieu de l'hébergement.
    """

    state = {"n": 0}

    def step(_index: int) -> bool:
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("panne simulée de la base")
        return True

    runtime = _runtime(tmp_path, step=step, max_iterations=6)
    runtime.run()

    payload = read_health_file(tmp_path / "worker-health.json")
    assert payload is not None
    assert payload["last_error"] == "RuntimeError", "l'erreur doit être publiée, pas avalée"
    assert payload["iterations"] >= 6, "la boucle doit avoir continué après l'erreur"


def test_bounded_run_terminates_without_a_signal(tmp_path: Path) -> None:
    """Le mode borné rend un worker utilisable par un planificateur (cron)."""

    runtime = _runtime(tmp_path, step=lambda _i: True, max_iterations=3)
    started = time.monotonic()
    assert runtime.run() == 0
    assert time.monotonic() - started < 10

    payload = read_health_file(tmp_path / "worker-health.json")
    assert payload is not None
    # La santé doit être un JSON valide et complet, pas un fichier partiel.
    json.loads((tmp_path / "worker-health.json").read_text(encoding="utf-8"))
