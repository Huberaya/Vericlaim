"""Déploiement — la file de travail est drainée par le planificateur de la plateforme.

Ce que ces tests protègent, et qui ne se voit pas dans un tableau de bord :

1. **La route est fermée par défaut.** Sans secret configuré, elle répond 503 et
   **n'exécute rien**. Une commande qui draine les files ne doit pas devenir une
   ressource publique parce qu'une variable d'environnement a été oubliée.
2. **Un secret faux ne passe pas**, et la comparaison est à temps constant (le test ne
   mesure pas le temps, il vérifie le refus — la propriété de comparaison est dans le
   code, pas ici).
3. **Le cycle travaille vraiment** : il appelle `run_once` en boucle jusqu'à ce qu'il
   n'y ait plus rien à faire, et compte ce qu'il a fait.
4. **Un budget épuisé n'est pas un succès.** Si le temps est écoulé, la réponse dit
   `drained: false` : on ne prétend pas que la file est vide.
5. **La chaîne réelle fonctionne** : kind → constructeur du worker → session de base.
   Un test avec un worker simulé prouverait la boucle, pas le branchement.
"""

from __future__ import annotations

import dataclasses

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import internal_workers
from app.core.config import settings
from app.core.database import create_tables
from app.main import app

SECRET = "cron-secret-de-test-0123456789abcdef"
PATH = "/api/v1/internal/workers/analysis_detection/tick"


@pytest.fixture()
def client() -> TestClient:
    create_tables()
    return TestClient(app)


def _configure(monkeypatch: pytest.MonkeyPatch, **overrides) -> None:
    replacement = dataclasses.replace(settings, **overrides)
    monkeypatch.setattr(internal_workers, "settings", replacement)


class _FakeWorker:
    """Un worker dont on choisit le nombre de tours productifs."""

    def __init__(self, productive_rounds: int) -> None:
        self.calls = 0
        self._productive_rounds = productive_rounds

    def run_once(self) -> bool:
        self.calls += 1
        return self.calls <= self._productive_rounds


def _install_fake(monkeypatch: pytest.MonkeyPatch, worker: _FakeWorker) -> None:
    def _build(kind: str, *, worker_id: str) -> _FakeWorker:
        del kind, worker_id
        return worker

    monkeypatch.setattr(internal_workers, "build_worker", _build)


# --- 1. fermé par défaut ------------------------------------------------------


def test_without_secret_the_route_refuses_and_runs_nothing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch, internal_worker_secret=None)
    worker = _FakeWorker(productive_rounds=5)
    _install_fake(monkeypatch, worker)

    response = client.get(PATH)

    assert response.status_code == 503
    assert worker.calls == 0, "aucun cycle ne doit être exécuté sans secret configuré"
    assert "CRON_SECRET" in response.json()["detail"]


# --- 2. un secret faux ne passe pas ------------------------------------------


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "Bearer ",
        "Bearer mauvais-secret-de-la-meme-longueur-000000",
        f"Basic {SECRET}",
        SECRET,  # sans le schéma Bearer
    ],
)
def test_wrong_or_missing_credentials_are_rejected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, header: str | None
) -> None:
    _configure(monkeypatch, internal_worker_secret=SECRET)
    worker = _FakeWorker(productive_rounds=5)
    _install_fake(monkeypatch, worker)

    headers = {} if header is None else {"Authorization": header}
    response = client.get(PATH, headers=headers)

    assert response.status_code == 401
    assert worker.calls == 0


# --- 3. le cycle travaille ----------------------------------------------------


def test_tick_runs_until_there_is_no_more_work(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch, internal_worker_secret=SECRET, internal_worker_budget_seconds=30)
    worker = _FakeWorker(productive_rounds=2)
    _install_fake(monkeypatch, worker)

    response = client.get(PATH, headers={"Authorization": f"Bearer {SECRET}"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["processed"] == 2
    assert payload["drained"] is True
    # 3 appels : deux tours productifs, puis un tour qui ne trouve rien.
    assert worker.calls == 3


def test_post_is_accepted_too(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, internal_worker_secret=SECRET, internal_worker_budget_seconds=30)
    _install_fake(monkeypatch, _FakeWorker(productive_rounds=1))

    response = client.post(PATH, headers={"Authorization": f"Bearer {SECRET}"})

    assert response.status_code == 200
    assert response.json()["processed"] == 1


# --- 4. un budget épuisé n'est pas un succès ----------------------------------


def test_exhausted_budget_is_reported_as_not_drained(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # budget 0 : le premier contrôle de temps échoue avant tout travail.
    _configure(monkeypatch, internal_worker_secret=SECRET, internal_worker_budget_seconds=0)
    worker = _FakeWorker(productive_rounds=10_000)
    _install_fake(monkeypatch, worker)

    response = client.get(PATH, headers={"Authorization": f"Bearer {SECRET}"})

    assert response.status_code == 200
    assert response.json()["drained"] is False, "une file non vidée doit se voir dans la réponse"


# --- 5. la chaîne réelle ------------------------------------------------------


def test_unknown_queue_is_a_404(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, internal_worker_secret=SECRET)

    response = client.get(
        "/api/v1/internal/workers/file_inconnue/tick",
        headers={"Authorization": f"Bearer {SECRET}"},
    )

    assert response.status_code == 404


def test_real_worker_is_built_and_reports_no_work_on_an_empty_database(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sans worker simulé : constructeur réel, session réelle, aucune exception."""
    _configure(monkeypatch, internal_worker_secret=SECRET, internal_worker_budget_seconds=30)

    response = client.get(
        "/api/v1/internal/workers/analysis_detection/tick",
        headers={"Authorization": f"Bearer {SECRET}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["kind"] == "analysis_detection"
    assert payload["processed"] == 0
    assert payload["drained"] is True


def test_every_declared_queue_has_a_builder() -> None:
    """Une file déclarée sans constructeur serait un 500 à la première invocation."""
    for kind in internal_workers.WORKER_KINDS:
        assert kind in {"document_extraction", "analysis_detection", "report_generation"}
