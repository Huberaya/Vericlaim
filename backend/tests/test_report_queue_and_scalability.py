"""C22 — la génération des rapports est un travail, pas une requête.

Défaut constaté avant ce chantier : ``POST /api/v1/reports/pdf`` rendait le PDF
**dans la requête HTTP**. Un rendu long était un time-out, un redémarrage perdait
le travail sans trace, et « scaler » revenait à allonger un délai.

Ces tests échouent contre ce défaut :

* ils exigent qu'une demande de rapport ne renvoie **aucun octet de document** ;
* ils exigent que le travail survive à un worker qui meurt (bail expiré) ;
* ils exigent que la file refuse du travail au lieu de l'empiler (contre-pression) ;
* ils exigent qu'un locataire ne puisse pas occuper tous les workers (équité).

Un test qui se contenterait de vérifier qu'un rapport finit par être produit
passerait aussi avec l'ancien code : ce n'est donc pas ce qui est vérifié ici.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.identity.service import set_db_request_context
from app.main import app
from app.models.domain import (
    Report,
    ReportJob,
    ReportJobStatus,
    ReportStatus,
)
from app.reports import queue as report_queue
from app.reports.generation import report_storage_key
from app.reports.signing import file_sha256
from tests.auth_support import authenticate_client
from tests.report_support import (
    WORKER_ID,
    issue_report,
    report_storage,
    request_report,
    run_report_worker,
)
from tests.test_pdf_reporting import _persisted_analysis

WORKER_CONTEXT_USER_ID = UUID(int=0)


def _jobs(organization_id: UUID) -> list[ReportJob]:
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(ReportJob)
                .where(ReportJob.organization_id == organization_id)
                .order_by(ReportJob.created_at.asc())
            ).all()
        )


def _reports(organization_id: UUID) -> list[Report]:
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(Report)
                .where(Report.organization_id == organization_id)
                .order_by(Report.version_number.asc())
            ).all()
        )


def _patch_job(organization_id: UUID, job_id: UUID, **values: object) -> None:
    with SessionLocal() as db:
        set_db_request_context(
            db, user_id=WORKER_CONTEXT_USER_ID, organization_id=organization_id
        )
        job = db.scalar(
            select(ReportJob).where(
                ReportJob.organization_id == UUID(str(organization_id)),
                ReportJob.id == UUID(str(job_id)),
            )
        )
        assert job is not None
        for field, value in values.items():
            setattr(job, field, value)
        db.commit()


def _fill_queue(organization_id: UUID, count: int) -> None:
    """Remplit la file d'une organisation avec des travaux en attente.

    Les lignes sont écrites directement : ce qui est testé ici est la
    **contre-pression** — ce qui se passe quand la file est pleine —, pas la façon
    dont elle s'est remplie.
    """

    with SessionLocal() as db:
        set_db_request_context(
            db, user_id=WORKER_CONTEXT_USER_ID, organization_id=organization_id
        )
        for _ in range(count):
            db.add(
                ReportJob(
                    id=uuid4(),
                    organization_id=organization_id,
                    analysis_version_id=uuid4(),
                    report_id=None,
                    report_format="pdf",
                    options_json={},
                    status=ReportJobStatus.QUEUED,
                    attempt_count=0,
                    max_attempts=settings.report_max_attempts,
                    available_at=datetime.now(timezone.utc),
                )
            )
        db.commit()


# --------------------------------------------------------------------------- #
# 1. La requête ne rend plus le document
# --------------------------------------------------------------------------- #


def test_each_report_route_is_declared_once():
    """Une route déclarée deux fois est une route dont on ne sait plus laquelle sert.

    Ce test vient d'un vrai défaut : la réécriture C22 avait laissé l'ancien
    ``get_analysis_pdf`` en place sous le nouveau. FastAPI sert le premier inscrit —
    donc l'ancien handler, qui répondait du JSON là où un fichier est attendu, restait
    mort mais aurait repris du service au premier réordonnancement.
    """

    from app.api.v1.reports import public_router, router

    def paths(api_router) -> list[tuple[str, tuple[str, ...]]]:
        return [
            (route.path, tuple(sorted(route.methods or ())))
            for route in api_router.routes
            if hasattr(route, "methods")
        ]

    for api_router, name in ((router, "router"), (public_router, "public_router")):
        declared = paths(api_router)
        duplicates = {route for route in declared if declared.count(route) > 1}
        assert duplicates == set(), f"routes déclarées plusieurs fois dans {name} : {duplicates}"

    assert ("/api/v1/reports/analyses/{analysis_id}/pdf", ("GET",)) in paths(router)
    assert ("/api/v1/reports/analyses/{analysis_id}/dossier", ("GET",)) in paths(router)


def test_a_report_request_returns_a_job_and_never_a_document():
    """Le cœur du chantier : plus aucun rendu dans la requête HTTP."""

    with TestClient(app) as client, report_storage() as storage:
        identity = authenticate_client(client, role_code="analyst")
        analysis_id, _ = _persisted_analysis(client, identity)

        response = client.post("/api/v1/reports/pdf", json={"analysis_id": analysis_id})
        assert response.status_code == 202, response.text
        assert response.headers["content-type"].startswith("application/json")
        assert response.content[:5] != b"%PDF-", "la requête rend encore le document"

        body = response.json()
        assert body["status"] == "queued"
        assert body["job_url"].startswith("/api/v1/reports/jobs/")
        assert body["download_url"].startswith("/api/v1/reports/")
        assert body["reused"] is False

        # Rien n'a encore été produit : ni objet, ni rapport prêt.
        assert storage.objects == {}
        state = client.get(body["job_url"]).json()
        assert state["job_status"] == "queued"
        assert state["download_available"] is False

        early = client.get(body["download_url"])
        assert early.status_code == 409, early.text
        assert early.json()["detail"]["code"] == "report_not_ready"
        assert early.json()["detail"]["job_url"] == body["job_url"]


def test_the_old_analysis_download_route_reads_and_does_not_generate():
    """``GET /analyses/{id}/pdf`` lit un rapport prêt ; il n'en produit pas."""

    with TestClient(app) as client, report_storage() as storage:
        identity = authenticate_client(client, role_code="analyst")
        analysis_id, _ = _persisted_analysis(client, identity)

        missing = client.get(f"/api/v1/reports/analyses/{analysis_id}/pdf")
        assert missing.status_code == 409, missing.text
        assert missing.json()["detail"]["code"] == "report_not_generated"
        assert _jobs(identity.organization_id) == []

        issued = issue_report(
            client, analysis_id=analysis_id, storage=storage, organization_id=identity.organization_id
        )
        assert issued.status_code == 200
        assert issued.content[:5] == b"%PDF-"

        again = client.get(f"/api/v1/reports/analyses/{analysis_id}/pdf")
        assert again.status_code == 200
        assert again.headers["x-verification-reference"] == issued.headers["x-verification-reference"]


# --------------------------------------------------------------------------- #
# 2. Le worker produit, stocke, et la vérification tient toujours
# --------------------------------------------------------------------------- #


def test_the_worker_stores_the_artifact_and_the_seal_survives_the_move():
    with TestClient(app) as client, report_storage() as storage:
        identity = authenticate_client(client, role_code="analyst")
        analysis_id, version_id = _persisted_analysis(client, identity)

        queued = request_report(client, analysis_id=analysis_id)
        assert run_report_worker(storage=storage, organization_id=identity.organization_id) == 1

        state = client.get(queued["job_url"]).json()
        assert state["job_status"] == "completed"
        assert state["report_status"] == "ready"
        assert state["attempt_count"] == 1
        assert state["download_available"] is True
        assert state["duration_ms"] is not None and state["duration_ms"] >= 0

        report = _reports(identity.organization_id)[0]
        assert report.status == ReportStatus.READY
        assert report.storage_key == report_storage_key(
            organization_id=identity.organization_id,
            report_id=report.id,
            report_format="pdf",
        )
        stored = storage.objects[(settings.document_clean_bucket, report.storage_key)]
        assert stored.content_type == "application/pdf"
        assert report.size_bytes == len(stored.payload)
        assert report.content_type == "application/pdf"
        assert report.sha256 == file_sha256(stored.payload)

        download = client.get(queued["download_url"])
        assert download.status_code == 200
        assert download.content == stored.payload
        assert download.headers["x-report-file-sha256"] == report.sha256
        assert download.headers["x-verification-reference"] == report.verification_reference

        # Le scellement C7 fonctionne sur le fichier réellement distribué.
        verified = client.get(
            f"/api/v1/reports/verify/{report.verification_reference}",
            params={"sha256": download.headers["x-report-file-sha256"]},
        ).json()
        assert verified["signature_valid"] is True
        assert verified["analysis_unchanged"] is True
        assert verified["file_matches"] is True

        # La version scellée est celle que le rapport a rendue, et c'est celle que la
        # vérification recompose : le numéro de règle publié doit être le sien.
        with SessionLocal() as db:
            from app.models.domain import AnalysisVersion

            version = db.get(AnalysisVersion, report.analysis_version_id)
            assert version is not None
            assert str(version.id) == state["analysis_version_id"]
            assert verified["rulebook_version"] == version.rulebook_version


def test_a_missing_object_is_reported_instead_of_serving_an_empty_file():
    with TestClient(app) as client, report_storage() as storage:
        identity = authenticate_client(client, role_code="analyst")
        analysis_id, _ = _persisted_analysis(client, identity)
        queued = request_report(client, analysis_id=analysis_id)
        run_report_worker(storage=storage, organization_id=identity.organization_id)

        report = _reports(identity.organization_id)[0]
        storage.objects.pop((settings.document_clean_bucket, report.storage_key))

        response = client.get(queued["download_url"])
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "report_object_missing"
        assert response.content[:5] != b"%PDF-"


def test_an_artifact_larger_than_the_download_cap_is_refused_not_truncated():
    """Un plafond de lecture doit refuser, jamais servir un fichier amputé."""

    from unittest.mock import patch

    with TestClient(app) as client, report_storage() as storage:
        identity = authenticate_client(client, role_code="analyst")
        analysis_id, _ = _persisted_analysis(client, identity)
        queued = issue_report(
            client,
            analysis_id=analysis_id,
            storage=storage,
            organization_id=identity.organization_id,
        )
        assert len(queued.content) > 1024

        download_url = f"/api/v1/reports/{_reports(identity.organization_id)[0].id}/download"
        strict = dataclasses.replace(settings, report_download_max_bytes=1024)
        with patch("app.api.v1.reports.settings", strict):
            too_large = client.get(download_url)
        assert too_large.status_code == 503, too_large.text
        assert too_large.json()["detail"]["code"] == "report_storage_unavailable"
        assert too_large.content[:5] != b"%PDF-"

        # Le plafond réel laisse passer le même objet : c'est bien la limite qui a joué.
        assert client.get(download_url).status_code == 200


# --------------------------------------------------------------------------- #
# 3. Idempotence : une demande en attente ne devient pas deux travaux
# --------------------------------------------------------------------------- #


def test_a_second_request_while_pending_reuses_the_same_job():
    with TestClient(app) as client, report_storage() as storage:
        identity = authenticate_client(client, role_code="analyst")
        analysis_id, _ = _persisted_analysis(client, identity)

        first = request_report(client, analysis_id=analysis_id)
        second = request_report(client, analysis_id=analysis_id)

        assert second["reused"] is True
        assert second["job_id"] == first["job_id"]
        assert second["report_id"] == first["report_id"]
        assert len(_jobs(identity.organization_id)) == 1
        assert len(_reports(identity.organization_id)) == 1
        assert client.get("/api/v1/reports/queue").json()["pending"] == 1


def test_a_request_after_readiness_creates_a_new_version_with_its_own_reference():
    with TestClient(app) as client, report_storage() as storage:
        identity = authenticate_client(client, role_code="analyst")
        analysis_id, _ = _persisted_analysis(client, identity)

        first = issue_report(
            client, analysis_id=analysis_id, storage=storage, organization_id=identity.organization_id
        )
        second = issue_report(
            client, analysis_id=analysis_id, storage=storage, organization_id=identity.organization_id
        )

        assert (
            first.headers["x-verification-reference"]
            != second.headers["x-verification-reference"]
        ), "deux demandes successives sont deux artefacts probatoires distincts"
        reports = _reports(identity.organization_id)
        assert [report.version_number for report in reports] == [1, 2]
        assert all(report.status == ReportStatus.READY for report in reports)


# --------------------------------------------------------------------------- #
# 4. Contre-pression : refuser vaut mieux qu'empiler
# --------------------------------------------------------------------------- #


def test_the_queue_refuses_a_request_when_the_organization_is_already_backed_up():
    with TestClient(app) as client, report_storage() as storage:
        identity = authenticate_client(client, role_code="analyst")
        analysis_id, _ = _persisted_analysis(client, identity)

        _fill_queue(identity.organization_id, settings.report_max_pending_per_organization)
        assert client.get("/api/v1/reports/queue").json()["saturated"] is True

        refused = client.post("/api/v1/reports/pdf", json={"analysis_id": analysis_id})
        assert refused.status_code == 429, refused.text
        detail = refused.json()["detail"]
        assert detail["code"] == "report_queue_saturated"
        assert detail["limit"] == settings.report_max_pending_per_organization
        assert detail["pending"] >= detail["limit"]
        assert refused.headers["Retry-After"] == str(detail["retry_after_seconds"])

        assert len(_reports(identity.organization_id)) == 0, (
            "un refus de contre-pression ne doit pas laisser de rapport en attente"
        )
        assert len(_jobs(identity.organization_id)) == detail["pending"]


def test_backpressure_uses_the_configured_limit_and_not_a_hardcoded_one():
    """La limite vient de la configuration, elle est donc publiée et ajustable."""

    with TestClient(app) as client, report_storage() as storage:
        identity = authenticate_client(client, role_code="analyst")
        analysis_id, _ = _persisted_analysis(client, identity)
        _fill_queue(identity.organization_id, 1)
        strict = dataclasses.replace(settings, report_max_pending_per_organization=1)
        assert client.get("/api/v1/reports/queue").json()["pending"] == 1

        with SessionLocal() as db:
            set_db_request_context(
                db,
                user_id=WORKER_CONTEXT_USER_ID,
                organization_id=identity.organization_id,
            )
            with pytest.raises(report_queue.ReportQueueSaturatedError) as captured:
                report_queue.enqueue_report(
                    db,
                    settings=strict,
                    organization_id=identity.organization_id,
                    analysis_id=UUID(analysis_id),
                    report_format="pdf",
                    options={},
                    requested_by_user_id=identity.user_id,
                )
                db.flush()
            assert captured.value.limit == 1
            db.rollback()


# --------------------------------------------------------------------------- #
# 5. Équité : un locataire ne prend pas tous les workers
# --------------------------------------------------------------------------- #


def test_one_organization_cannot_occupy_every_worker_slot():
    """Un locataire plafonné ne doit pas retarder les autres.

    Ce test échoue contre l'ancien monde (un rendu par requête, aucun plafond) :
    il exige que l'organisation A, avec plus de travaux que son plafond, laisse
    l'organisation B avancer **dans le même cycle de worker**.
    """

    with TestClient(app) as client_a, TestClient(app) as client_b, report_storage() as storage:
        identity_a = authenticate_client(client_a, role_code="analyst")
        identity_b = authenticate_client(client_b, role_code="analyst")
        analysis_a, _ = _persisted_analysis(client_a, identity_a)
        analysis_b, _ = _persisted_analysis(client_b, identity_b)

        # A met plus de travaux en file que son plafond de travaux simultanés.
        for report_format in ("pdf", "dossier_zip"):
            request_report(client_a, analysis_id=analysis_a, report_format=report_format)
        _fill_queue(identity_a.organization_id, settings.report_max_running_per_organization - 1)
        assert len(_jobs(identity_a.organization_id)) == settings.report_max_running_per_organization + 1
        queued_b = request_report(client_b, analysis_id=analysis_b)

        # Le plafond de A est atteint : un quatrième travail n'est plus réclamable.
        for _ in range(settings.report_max_running_per_organization):
            with SessionLocal() as db:
                set_db_request_context(
                    db,
                    user_id=WORKER_CONTEXT_USER_ID,
                    organization_id=identity_a.organization_id,
                )
                claim = report_queue.claim_next_report_job(
                    db,
                    settings=settings,
                    organization_id=identity_a.organization_id,
                    worker_id="worker-equite",
                )
                db.commit()
            assert claim is not None, "le plafond ne doit pas bloquer avant d'être atteint"
        with SessionLocal() as db:
            set_db_request_context(
                db,
                user_id=WORKER_CONTEXT_USER_ID,
                organization_id=identity_a.organization_id,
            )
            assert (
                report_queue.claim_next_report_job(
                    db,
                    settings=settings,
                    organization_id=identity_a.organization_id,
                    worker_id="worker-equite",
                )
                is None
            ), "au-delà du plafond, l'organisation ne peut plus occuper de worker"
            db.rollback()

        # …et B avance quand même, dans le cycle où A est plafonnée.
        assert (
            run_report_worker(
                storage=storage,
                organization_id=[identity_a.organization_id, identity_b.organization_id],
            )
            == 1
        )
        assert client_b.get(queued_b["job_url"]).json()["job_status"] == "completed"
        assert all(
            job.status != ReportJobStatus.RUNNING
            for job in _jobs(identity_b.organization_id)
        )


# --------------------------------------------------------------------------- #
# 6. Reprise : un worker qui meurt ne perd pas le travail
# --------------------------------------------------------------------------- #


def test_an_expired_lease_is_reclaimed_and_the_old_worker_cannot_write():
    with TestClient(app) as client, report_storage() as storage:
        identity = authenticate_client(client, role_code="analyst")
        analysis_id, _ = _persisted_analysis(client, identity)
        queued = request_report(client, analysis_id=analysis_id)

        with SessionLocal() as db:
            set_db_request_context(
                db,
                user_id=WORKER_CONTEXT_USER_ID,
                organization_id=identity.organization_id,
            )
            stale = report_queue.claim_next_report_job(
                db,
                settings=settings,
                organization_id=identity.organization_id,
                worker_id="worker-qui-va-mourir",
            )
            db.commit()
        assert stale is not None and stale.attempt_count == 1

        # Le worker disparaît : son bail expire sans qu'il écrive quoi que ce soit.
        _patch_job(
            identity.organization_id,
            queued["job_id"],
            lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=30),
        )

        assert run_report_worker(storage=storage, organization_id=identity.organization_id) == 1
        state = client.get(queued["job_url"]).json()
        assert state["job_status"] == "completed"
        assert state["attempt_count"] == 2, "le travail repris doit compter sa seconde tentative"

        # Le worker d'origine ne peut plus écrire : son bail est perdu.
        with SessionLocal() as db:
            set_db_request_context(
                db,
                user_id=WORKER_CONTEXT_USER_ID,
                organization_id=identity.organization_id,
            )
            with pytest.raises(report_queue.ReportJobConflictError):
                report_queue.complete_report_job(
                    db, claim=stale, report_fields={"sha256": "f" * 64}, duration_ms=1
                )
            db.rollback()


# --------------------------------------------------------------------------- #
# 7. Échec : reprise avec délai croissant, puis abandon lisible
# --------------------------------------------------------------------------- #


def test_a_storage_failure_is_retried_with_a_backoff_and_then_gives_up():
    with TestClient(app) as client, report_storage() as storage:
        identity = authenticate_client(client, role_code="analyst")
        analysis_id, _ = _persisted_analysis(client, identity)
        queued = request_report(client, analysis_id=analysis_id)

        storage.fail_operations.add("put_bytes")
        assert run_report_worker(storage=storage, organization_id=identity.organization_id) == 1

        retried = client.get(queued["job_url"]).json()
        assert retried["job_status"] == "queued", retried
        assert retried["error_code"] == "report_storage_unavailable"
        assert retried["attempt_count"] == 1
        assert retried["download_available"] is False
        job = _jobs(identity.organization_id)[0]
        assert job.available_at > datetime.now(timezone.utc).replace(tzinfo=job.available_at.tzinfo), (
            "un nouvel essai doit être différé, pas relancé en boucle"
        )
        assert _reports(identity.organization_id)[0].status == ReportStatus.REQUESTED

        # Dernière tentative autorisée : l'échec devient définitif et lisible. Le
        # nouvel essai est délibérément ramené à maintenant : ce qui est vérifié ici
        # est le caractère définitif de l'échec, pas la durée du report.
        _patch_job(
            identity.organization_id,
            queued["job_id"],
            max_attempts=2,
            available_at=datetime.now(timezone.utc),
        )
        run_report_worker(storage=storage, organization_id=identity.organization_id)
        terminal = client.get(queued["job_url"]).json()
        assert terminal["job_status"] == "failed"
        assert terminal["report_status"] == "failed"
        assert terminal["error_code"] == "report_storage_unavailable"
        assert terminal["download_available"] is False
        assert client.get(queued["download_url"]).status_code == 409
        assert _reports(identity.organization_id)[0].failure_code == "report_storage_unavailable"


def test_a_failed_report_does_not_block_a_new_request():
    with TestClient(app) as client, report_storage() as storage:
        identity = authenticate_client(client, role_code="analyst")
        analysis_id, _ = _persisted_analysis(client, identity)
        queued = request_report(client, analysis_id=analysis_id)
        _patch_job(identity.organization_id, queued["job_id"], max_attempts=1)
        storage.fail_operations.add("put_bytes")
        run_report_worker(storage=storage, organization_id=identity.organization_id)
        assert client.get(queued["job_url"]).json()["job_status"] == "failed"

        storage.fail_operations.clear()
        fresh = issue_report(
            client, analysis_id=analysis_id, storage=storage, organization_id=identity.organization_id
        )
        assert fresh.status_code == 200
        assert _reports(identity.organization_id)[-1].status == ReportStatus.READY


# --------------------------------------------------------------------------- #
# 8. Ce que la file publie, et à qui
# --------------------------------------------------------------------------- #


def test_the_queue_state_is_readable_and_only_for_its_own_tenant():
    with TestClient(app) as client, TestClient(app) as foreign, report_storage() as storage:
        identity = authenticate_client(client, role_code="analyst")
        authenticate_client(foreign, role_code="analyst")
        analysis_id, _ = _persisted_analysis(client, identity)
        queued = request_report(client, analysis_id=analysis_id)

        snapshot = client.get("/api/v1/reports/queue").json()
        assert snapshot["pending"] == 1
        assert snapshot["running"] == 0
        assert snapshot["pending_limit"] == settings.report_max_pending_per_organization
        assert snapshot["saturated"] is False
        assert "report-worker" in snapshot["workers_note"]

        assert foreign.get(queued["job_url"]).status_code == 404
        assert foreign.get(queued["download_url"]).status_code == 404
        assert foreign.get("/api/v1/reports/queue").json()["pending"] == 0


def test_a_report_stays_downloadable_without_an_active_subscription():
    """Un client qui a payé son rapport le garde : le téléchargement n'est pas un achat."""

    from app.billing.plans import Metric  # noqa: F401 - lu plus bas par le test de garde
    from app.models.domain import BillingSubscription

    with TestClient(app) as client, report_storage() as storage:
        identity = authenticate_client(client, role_code="owner")
        analysis_id, _ = _persisted_analysis(client, identity)
        issued = issue_report(
            client, analysis_id=analysis_id, storage=storage, organization_id=identity.organization_id
        )
        reference = issued.headers["x-verification-reference"]
        download_url = f"/api/v1/reports/{_reports(identity.organization_id)[0].id}/download"

        # L'essai Pro a expiré : toute génération est refusée…
        with SessionLocal() as db:
            from app.models.domain import Organization

            organization = db.get(Organization, identity.organization_id)
            assert organization is not None
            organization.created_at = datetime.now(timezone.utc) - timedelta(days=400)
            db.commit()
        blocked = client.post("/api/v1/reports/pdf", json={"analysis_id": analysis_id})
        assert blocked.status_code == 402, blocked.text

        # …mais la pièce déjà produite reste téléchargeable et vérifiable.
        still_there = client.get(download_url)
        assert still_there.status_code == 200
        assert still_there.headers["x-verification-reference"] == reference
        verified = client.get(f"/api/v1/reports/verify/{reference}").json()
        assert verified["known"] is True and verified["signature_valid"] is True
        with SessionLocal() as db:
            assert (
                db.scalar(
                    select(BillingSubscription).where(
                        BillingSubscription.organization_id == identity.organization_id
                    )
                )
                is None
            )


def test_the_dedicated_worker_is_declared_for_deployment():
    """Le worker doit exister dans le déploiement, sinon la file ne se vide jamais."""

    import pathlib

    compose = pathlib.Path(__file__).resolve().parents[2] / "docker-compose.yml"
    text = compose.read_text(encoding="utf-8")
    assert "report-worker:" in text, "aucun service de génération de rapports dans Compose"
    assert "app.workers.report_generation_worker" in text
    assert 'user: "10001:10001"' in text
    # La file est le seul mécanisme : aucun service ne doit rendre un rapport au nom de l'API.
    assert "reports/pdf" not in text


def test_the_worker_id_is_stable_enough_to_be_read_in_the_logs():
    """Un identifiant de worker anonyme rendrait un bail perdu impossible à attribuer."""

    assert WORKER_ID.startswith("pytest-")
    with TestClient(app) as client, report_storage() as storage:
        identity = authenticate_client(client, role_code="analyst")
        analysis_id, _ = _persisted_analysis(client, identity)
        request_report(client, analysis_id=analysis_id)
        run_report_worker(storage=storage, organization_id=identity.organization_id)
        with SessionLocal() as db:
            events = db.scalars(
                select(ReportJob).where(ReportJob.organization_id == identity.organization_id)
            ).all()
        assert events and events[0].worker_id == WORKER_ID
