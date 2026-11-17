"""Runtime commun aux workers durables : arrêt propre, santé, concurrence.

A-06 constatait qu'aucun worker ne tournait sur Vercel, et que les workers
existants n'exposaient qu'un ``run_forever()`` sans cycle de vie. Ce module
apporte la partie « cycle de vie » qui manquait réellement :

* **arrêt propre** — ``SIGTERM``/``SIGINT`` arrêtent la boucle, laissent le job
  en cours se terminer (dans une limite de drain), puis rendent la main ;
* **santé observable** — un fichier de battement réécrit atomiquement à chaque
  itération, avec l'âge du dernier passage, le nombre d'itérations et l'erreur
  éventuelle. Un superviseur (Docker ``healthcheck``, systemd, Kubernetes)
  peut donc distinguer « vivant mais bloqué » de « vivant et qui travaille » ;
* **concurrence bornée** — ``concurrency`` boucles parallèles dans le même
  processus, chacune avec un ``worker_id`` distinct (``base#1``, ``base#2``…).
  La revendication reste sûre puisque la base fait le tri
  (``FOR UPDATE SKIP LOCKED``) ; l'unicité du ``worker_id`` sert au *fencing*
  des leases, pas à la correction de la file.

Ce que ce module ne fait pas : il n'invente pas de durabilité. La garantie
« aucun double traitement » reste portée par les leases et le fencing en base
(``app.documents.extraction``, ``app.analysis...``), déjà couverts par
``tests/test_document_lease_fencing.py``. Le runtime ne fait que rendre le
processus observable et arrêtable.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class WorkerHealth:
    """État publié dans le fichier de santé, lu par le superviseur."""

    worker: str
    worker_id: str
    started_at: str
    heartbeat_at: str
    iterations: int = 0
    jobs_processed: int = 0
    last_error: str | None = None
    stopping: bool = False
    threads: int = 1

    def to_json(self) -> dict[str, Any]:
        return {
            "worker": self.worker,
            "worker_id": self.worker_id,
            "started_at": self.started_at,
            "heartbeat_at": self.heartbeat_at,
            "iterations": self.iterations,
            "jobs_processed": self.jobs_processed,
            "last_error": self.last_error,
            "stopping": self.stopping,
            "threads": self.threads,
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
        }


def write_health_file(path: Path, health: WorkerHealth) -> None:
    """Écrit l'état atomiquement, pour qu'un lecteur ne voie jamais un JSON partiel.

    Un fichier de santé tronqué serait pire qu'absent : un superviseur le
    lirait comme un JSON invalide et conclurait à tort à un worker mort.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(health.to_json(), sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def read_health_file(path: Path) -> dict[str, Any] | None:
    """Lit l'état publié, ou ``None`` s'il est absent ou illisible."""

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def health_is_fresh(payload: dict[str, Any] | None, *, max_age_seconds: float, now: float | None = None) -> bool:
    """Vrai si le battement est récent — c'est-à-dire si le worker avance encore.

    Un processus vivant dont la boucle est bloquée (base injoignable, OCR figé)
    n'écrit plus : c'est exactement ce cas qu'un simple « le PID existe » ne
    saurait pas voir.
    """

    if not payload:
        return False
    stamp = payload.get("heartbeat_at")
    if not isinstance(stamp, str):
        return False
    try:
        beat = datetime.fromisoformat(stamp).timestamp()
    except ValueError:
        return False
    reference = time.time() if now is None else now
    return (reference - beat) <= max_age_seconds


@dataclass
class WorkerRuntime:
    """Pilote une ou plusieurs boucles de travail avec un cycle de vie commun."""

    name: str
    worker_id: str
    #: Une itération : ``True`` si du travail a été fait (permet de dormir sinon).
    step: Callable[[int], bool]
    poll_seconds: float
    concurrency: int = 1
    health_path: Path | None = None
    drain_timeout_seconds: float = 30.0
    max_iterations: int | None = None
    sleep: Callable[[float], None] = time.sleep

    _stop: threading.Event = field(default_factory=threading.Event, repr=False)
    _done: threading.Event = field(default_factory=threading.Event, repr=False)
    _health: WorkerHealth | None = field(default=None, repr=False)
    _threads: list[threading.Thread] = field(default_factory=list, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # -- cycle de vie ---------------------------------------------------------

    def request_stop(self, *_args: object) -> None:
        """Demande l'arrêt. Idempotent : un second signal ne casse rien."""

        if not self._stop.is_set():
            logger.info("Arrêt demandé", extra={"worker": self.name, "worker_id": self.worker_id})
        self._stop.set()
        if self._health is not None:
            self._health.stopping = True

    def install_signal_handlers(self) -> None:
        """Branche ``SIGTERM``/``SIGINT`` sur un arrêt propre.

        Sans cela, ``docker stop`` tue le processus au bout de 10 s et le job en
        cours reste en ``RUNNING`` jusqu'à expiration de son lease — un délai
        d'indisponibilité parfaitement évitable.
        """

        for signum in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(signum, self.request_stop)
            except (ValueError, OSError):  # pragma: no cover - hors thread principal
                logger.warning("Impossible d'installer le gestionnaire de signal", extra={"signal": signum})

    def _worker_thread_ids(self) -> list[str]:
        if self.concurrency <= 1:
            return [self.worker_id]
        return [f"{self.worker_id}#{index + 1}" for index in range(self.concurrency)]

    def _publish(self, *, iterations: int, jobs: int, error: str | None = None) -> None:
        if self._health is None or self.health_path is None:
            return
        with self._lock:
            self._health.heartbeat_at = _utc_now_iso()
            self._health.iterations = iterations
            self._health.jobs_processed = jobs
            self._health.stopping = self._stop.is_set()
            if error is not None:
                self._health.last_error = error
            write_health_file(self.health_path, self._health)

    def _loop(self, index: int) -> None:
        iterations = 0
        processed = 0
        while not self._stop.is_set():
            if self.max_iterations is not None and iterations >= self.max_iterations:
                break
            # Initialisé avant l'appel : si ``step`` lève, la boucle doit encore
            # pouvoir décider quoi faire. Sans cela, l'itération en erreur tuait
            # le thread — donc le worker — par ``UnboundLocalError``.
            did_work = False
            try:
                did_work = bool(self.step(index))
                if did_work:
                    processed += 1
            except Exception as exc:  # noqa: BLE001 - une boucle ne doit pas mourir
                # Un worker qui meurt sur une exception laisse la file sans
                # consommateur jusqu'au prochain redémarrage. On journalise, on
                # publie l'erreur, et on continue : la boucle est le durable.
                logger.exception("Itération de worker en échec", extra={"worker": self.name})
                self._publish(iterations=iterations, jobs=processed, error=type(exc).__name__)
            iterations += 1
            self._publish(iterations=iterations, jobs=processed)
            if not did_work and not self._stop.is_set():
                # ``Event.wait`` rend le sommeil interruptible : sans cela un
                # arrêt attendrait tout le poll avant d'être pris en compte.
                self._stop.wait(self.poll_seconds)
        self._publish(iterations=iterations, jobs=processed)

    def run(self) -> int:
        """Démarre les boucles et rend le code de sortie du processus."""

        self._health = WorkerHealth(
            worker=self.name,
            worker_id=self.worker_id,
            started_at=_utc_now_iso(),
            heartbeat_at=_utc_now_iso(),
            threads=self.concurrency,
        )
        self._publish(iterations=0, jobs=0)
        logger.info(
            "Worker démarré",
            extra={"worker": self.name, "worker_id": self.worker_id, "concurrency": self.concurrency},
        )
        for index in range(self.concurrency):
            thread = threading.Thread(
                target=self._loop,
                args=(index,),
                name=f"{self.name}-{index}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)

        try:
            while any(thread.is_alive() for thread in self._threads):
                if self.max_iterations is not None:
                    # Mode « une passe » : on attend la fin naturelle, sans
                    # bloquer indéfiniment.
                    for thread in self._threads:
                        thread.join(timeout=self.poll_seconds)
                    if not any(thread.is_alive() for thread in self._threads):
                        break
                else:
                    time.sleep(0.2)
        except KeyboardInterrupt:  # pragma: no cover - couvert par les signaux
            self.request_stop()

        self._drain()
        self._publish(
            iterations=self._health.iterations if self._health else 0,
            jobs=self._health.jobs_processed if self._health else 0,
        )
        logger.info("Worker arrêté proprement", extra={"worker": self.name, "worker_id": self.worker_id})
        return 0

    def _drain(self) -> None:
        """Laisse les itérations en cours finir, sans les tuer.

        Un job interrompu au milieu d'un OCR redeviendrait réclamable après
        expiration du lease : on préfère attendre quelques secondes. Au-delà du
        délai, on sort quand même — un arrêt qui ne se termine jamais serait
        pire qu'une reprise par lease.
        """

        deadline = time.monotonic() + self.drain_timeout_seconds
        for thread in self._threads:
            remaining = max(0.0, deadline - time.monotonic())
            thread.join(timeout=remaining)
        still_running = [thread.name for thread in self._threads if thread.is_alive()]
        if still_running:
            logger.warning(
                "Drain interrompu par le délai : reprise attendue par expiration de lease",
                extra={"threads": still_running, "drain_timeout_seconds": self.drain_timeout_seconds},
            )

    # -- modes d'exécution ----------------------------------------------------

    def run_bounded(self, iterations: int) -> int:
        """Exécute un nombre borné d'itérations puis sort.

        C'est la forme utilisable depuis un planificateur (cron) qui n'a pas de
        processus long : chaque déclenchement fait avancer la file un peu, sans
        jamais dépendre d'un démon.
        """

        self.max_iterations = iterations
        return self.run()
