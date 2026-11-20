"""Outils partagés par les tests de rapports, après C22.

Depuis C22, **aucune route ne rend un rapport dans la requête**. Un test qui veut
le contenu d'un PDF doit donc faire les trois gestes d'un vrai client :

1. demander la génération (``POST /api/v1/reports/pdf``, réponse 202) ;
2. laisser le worker dédié exécuter le travail ;
3. télécharger l'artefact produit (``download_url``).

Ces trois gestes sont regroupés ici pour que les tests d'intégrité C7 continuent
de porter sur ce qu'ils vérifiaient — la signature, la référence, la fidélité du
document — et non sur la mécanique de file, qui a ses propres tests.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import SessionLocal
from app.main import app
from app.workers.report_generation_worker import ReportGenerationWorker
from tests.document_support import FakeObjectStorage

WORKER_ID = "pytest-report-worker"


@contextmanager
def report_storage() -> Iterator[FakeObjectStorage]:
    """Installe un stockage d'objets en mémoire sur l'application montée."""

    previous = app.state.document_storage
    storage = FakeObjectStorage()
    app.state.document_storage = storage
    try:
        yield storage
    finally:
        app.state.document_storage = previous


def run_report_worker(
    *,
    storage: FakeObjectStorage | None = None,
    runs: int = 1,
    organization_id=None,
) -> int:
    """Fait tourner le worker de rapports sur le travail en attente.

    Retourne le nombre de travaux traités. ``runs`` permet de dépasser un travail
    qui échoue et se replanifie immédiatement (les tests de reprise).

    ``organization_id`` restreint le worker à une organisation : la base de test
    est partagée par toute la session, et sans restriction un worker peut réclamer
    le travail laissé par un autre test — ce qui rendrait les assertions fausses
    sans que le code soit en cause.
    """

    storage = storage or app.state.document_storage
    worker = ReportGenerationWorker(
        settings=settings,
        storage=storage,
        session_factory=SessionLocal,
        worker_id=WORKER_ID,
    )
    if organization_id is not None:
        restricted = (
            list(organization_id)
            if isinstance(organization_id, (list, tuple, set))
            else [organization_id]
        )
        worker._active_organization_ids = lambda: restricted  # type: ignore[method-assign,assignment]
    processed = 0
    for _ in range(max(runs, 1)):
        if worker.run_once():
            processed += 1
    return processed


def request_report(
    client: TestClient,
    *,
    analysis_id: str,
    report_format: str = "pdf",
    expected_status: int = 202,
    **options: object,
) -> dict:
    """Met un rapport en file et vérifie le contrat de la réponse 202."""

    path = "/api/v1/reports/pdf" if report_format == "pdf" else "/api/v1/reports/dossier"
    body = {"analysis_id": analysis_id, **options}
    response = client.post(path, json=body)
    assert response.status_code == expected_status, response.text
    assert response.status_code != 200 or response.content[:5] != b"%PDF-", (
        "la requête de génération ne doit plus renvoyer de fichier : le rendu a lieu "
        "dans le worker"
    )
    return response.json()


def issue_report(
    client: TestClient,
    *,
    analysis_id: str,
    report_format: str = "pdf",
    storage: FakeObjectStorage | None = None,
    runs: int = 1,
    organization_id=None,
    **options: object,
):
    """Demande, génère, télécharge — et rend la réponse HTTP du téléchargement.

    La réponse porte les mêmes en-têtes probatoires qu'avant C22
    (``x-verification-reference``, ``x-report-signature``, ``x-report-file-sha256``) :
    les tests d'intégrité vérifient donc toujours la même chose.
    """

    queued = request_report(
        client, analysis_id=analysis_id, report_format=report_format, **options
    )
    run_report_worker(storage=storage, runs=runs, organization_id=organization_id)

    job = client.get(queued["job_url"])
    assert job.status_code == 200, job.text
    assert job.json()["job_status"] == "completed", job.text
    assert job.json()["download_available"] is True, job.text

    response = client.get(job.json()["download_url"])
    assert response.status_code == 200, response.text
    return response
