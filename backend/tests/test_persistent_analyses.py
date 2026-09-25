from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.analyses.service import (
    AnalysisDetectionTerminalError,
    AnalysisIdempotencyConflictError,
    AnalysisInputError,
    claim_next_analysis_detection_job,
    create_analysis,
    list_version_claims,
    process_claimed_analysis_detection,
    retry_analysis,
)
from app.core.config import settings
from app.core.database import Base, SessionLocal
from app.identity.service import set_db_request_context
from app.models.domain import (
    AnalysisDetectionJob,
    AnalysisDetectionJobStatus,
    AnalysisStatus,
    AuditEvent,
    Claim,
    ClaimStatus,
    Document,
    DocumentSegment,
    DocumentVersion,
    ExtractionStatus,
    Organization,
    SegmentType,
    User,
    UserStatus,
)
from app.workers.analysis_detection_worker import AnalysisDetectionWorker
from tests.auth_support import authenticate_client


def _hash(character: str) -> str:
    return character * 64


@dataclass(frozen=True)
class Seed:
    organization_id: UUID
    user_id: UUID
    document_version_id: UUID
    native_segment_id: UUID
    ocr_segment_id: UUID


def _engine():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


def _seed_extracted_document(engine, *, slug: str = "tenant-a", include_ocr: bool = True) -> Seed:
    suffix = uuid4().hex[:10]
    with Session(engine, expire_on_commit=False) as db:
        organization = Organization(name=f"Organisation {suffix}", slug=f"{slug}-{suffix}")
        user = User(email=f"analyst.{suffix}@example.test", status=UserStatus.ACTIVE)
        db.add_all((organization, user))
        db.flush()
        document = Document(
            organization_id=organization.id,
            document_key=f"DOC-{suffix}",
            title="Déclaration fournisseur",
        )
        db.add(document)
        db.flush()
        version = DocumentVersion(
            organization_id=organization.id,
            document_id=document.id,
            version_number=1,
            source_filename="declaration.txt",
            content_type="text/plain",
            storage_key=f"organizations/{organization.id}/declaration.txt",
            sha256=_hash("a"),
            size_bytes=64,
            source_language="fr",
            extraction_status=ExtractionStatus.COMPLETED,
            extraction_engine_version="vericlaim-document-extractor-v1",
            extracted_text_sha256=_hash("b"),
        )
        db.add(version)
        db.flush()
        native = DocumentSegment(
            organization_id=organization.id,
            document_version_id=version.id,
            sequence_number=0,
            page_number=1,
            segment_type=SegmentType.PARAGRAPH,
            text="Cet emballage est recyclable.",
            start_offset=0,
            end_offset=29,
            source_sha256=version.sha256,
        )
        db.add(native)
        db.flush()
        ocr_id = uuid4()
        if include_ocr:
            ocr = DocumentSegment(
                id=ocr_id,
                organization_id=organization.id,
                document_version_id=version.id,
                sequence_number=1,
                page_number=2,
                segment_type=SegmentType.IMAGE_OCR,
                text="Produit neutre en carbone.",
                start_offset=31,
                end_offset=58,
                source_sha256=version.sha256,
            )
            db.add(ocr)
        db.commit()
        return Seed(
            organization_id=organization.id,
            user_id=user.id,
            document_version_id=version.id,
            native_segment_id=native.id,
            ocr_segment_id=ocr_id,
        )


def _enqueue(engine, seed: Seed, *, key: str = "persistent-analysis-key"):
    with Session(engine, expire_on_commit=False) as db:
        set_db_request_context(db, user_id=seed.user_id, organization_id=seed.organization_id)
        result = create_analysis(
            db,
            settings=settings,
            organization_id=seed.organization_id,
            actor_user_id=seed.user_id,
            document_version_ids=[seed.document_version_id],
            idempotency_key=key,
        )
        values = (result.analysis.id, result.version.id, result.job.id)
        db.commit()
        return values


def _claim_and_process(engine, seed: Seed):
    with Session(engine, expire_on_commit=False) as db:
        set_db_request_context(db, user_id=seed.user_id, organization_id=seed.organization_id)
        claim = claim_next_analysis_detection_job(
            db,
            settings=settings,
            organization_id=seed.organization_id,
            worker_id="pytest-analysis-worker",
        )
        assert claim is not None
        db.commit()
    with Session(engine, expire_on_commit=False) as db:
        set_db_request_context(db, user_id=seed.user_id, organization_id=seed.organization_id)
        completion = process_claimed_analysis_detection(db, claim=claim)
        db.commit()
        return completion


def test_persistent_detection_uses_segments_preserves_citations_and_marks_ocr_for_review():
    engine = _engine()
    seed = _seed_extracted_document(engine)
    analysis_id, version_id, job_id = _enqueue(engine, seed)

    completion = _claim_and_process(engine, seed)
    assert completion.analysis.id == analysis_id
    assert completion.version.id == version_id
    assert completion.job.id == job_id
    assert completion.version.status == AnalysisStatus.COMPLETED
    assert completion.job.status == AnalysisDetectionJobStatus.COMPLETED
    assert completion.claim_count == 2
    assert completion.review_required_claim_count == 1
    assert completion.version.overall_risk_level is None
    assert completion.version.risk_score is None
    assert completion.version.rulebook_version == "not-applicable-deterministic-claims-v1"
    assert completion.version.result_sha256 is not None
    assert completion.version.result_json["scope"] == "citeable_claim_detection_only"
    assert completion.version.result_json["input_manifest_sha256"] == completion.version.input_manifest_sha256
    assert "evaluations" not in completion.version.result_json
    assert "risk_score" not in completion.version.result_json

    with Session(engine) as db:
        claims = list_version_claims(db, organization_id=seed.organization_id, analysis_version_id=version_id)
        assert {claim.document_segment_id for claim in claims} == {seed.native_segment_id, seed.ocr_segment_id}
        native = next(claim for claim in claims if claim.document_segment_id == seed.native_segment_id)
        ocr = next(claim for claim in claims if claim.document_segment_id == seed.ocr_segment_id)
        assert native.status == ClaimStatus.DETECTED
        assert ocr.status == ClaimStatus.REVIEW_REQUIRED
        assert native.confidence_score is None
        assert ocr.confidence_score is None
        for claim in claims:
            provenance = claim.attributes_json
            assert provenance["segment_id"] == str(claim.document_segment_id)
            assert provenance["document_source_sha256"] == _hash("a")
            assert provenance["segment_source_sha256"] == _hash("a")
            assert provenance["page_number"] in {1, 2}
            assert claim.start_offset == provenance["sentence_start_offset_in_document"]
            assert claim.end_offset == provenance["sentence_end_offset_in_document"]
        assert native.start_offset == 0
        assert ocr.start_offset == 31
        actions = set(
            db.scalars(
                select(AuditEvent.action).where(AuditEvent.organization_id == seed.organization_id)
            ).all()
        )
        assert {"analysis.created", "analysis.claim_detection_queued", "analysis.claim_detection_started", "analysis.claim_detection_completed"} <= actions
        assert db.scalar(select(func.count()).select_from(AnalysisDetectionJob)) == 1


def test_create_and_retry_are_idempotent_and_retry_creates_a_new_immutable_version():
    engine = _engine()
    seed = _seed_extracted_document(engine)
    analysis_id, version_one_id, _ = _enqueue(engine, seed, key="create-key")
    first_completion = _claim_and_process(engine, seed)
    first_hash = first_completion.version.result_sha256

    with Session(engine, expire_on_commit=False) as db:
        set_db_request_context(db, user_id=seed.user_id, organization_id=seed.organization_id)
        replay = create_analysis(
            db,
            settings=settings,
            organization_id=seed.organization_id,
            actor_user_id=seed.user_id,
            document_version_ids=[seed.document_version_id],
            idempotency_key="create-key",
        )
        assert replay.idempotent_replay is True
        assert replay.analysis.id == analysis_id
        assert replay.version.id == version_one_id

        with pytest.raises(AnalysisIdempotencyConflictError):
            create_analysis(
                db,
                settings=settings,
                organization_id=seed.organization_id,
                actor_user_id=seed.user_id,
                document_version_ids=[seed.document_version_id],
                analysis_key="different-request",
                idempotency_key="create-key",
            )

        retried = retry_analysis(
            db,
            settings=settings,
            organization_id=seed.organization_id,
            actor_user_id=seed.user_id,
            analysis_id=analysis_id,
            idempotency_key="retry-key",
        )
        assert retried.idempotent_replay is False
        assert retried.version.version_number == 2
        assert retried.version.id != version_one_id
        retry_version_id = retried.version.id
        replay_retry = retry_analysis(
            db,
            settings=settings,
            organization_id=seed.organization_id,
            actor_user_id=seed.user_id,
            analysis_id=analysis_id,
            idempotency_key="retry-key",
        )
        assert replay_retry.idempotent_replay is True
        assert replay_retry.version.id == retry_version_id
        db.commit()

    _claim_and_process(engine, seed)
    with Session(engine) as db:
        first = db.get(type(first_completion.version), version_one_id)
        second = db.get(type(first_completion.version), retry_version_id)
        assert first is not None and second is not None
        assert first.status == AnalysisStatus.COMPLETED
        assert first.result_sha256 == first_hash
        assert second.status == AnalysisStatus.COMPLETED
        assert second.version_number == 2
        assert len(list_version_claims(db, organization_id=seed.organization_id, analysis_version_id=version_one_id)) == 2
        assert len(list_version_claims(db, organization_id=seed.organization_id, analysis_version_id=retry_version_id)) == 2


def test_rejects_unextracted_or_foreign_document_versions_without_cross_tenant_leakage():
    engine = _engine()
    local = _seed_extracted_document(engine, slug="local", include_ocr=False)
    foreign = _seed_extracted_document(engine, slug="foreign", include_ocr=False)
    with Session(engine, expire_on_commit=False) as db:
        pending = db.get(DocumentVersion, local.document_version_id)
        assert pending is not None
        pending.extraction_status = ExtractionStatus.PENDING
        db.commit()

    with Session(engine) as db:
        set_db_request_context(db, user_id=local.user_id, organization_id=local.organization_id)
        with pytest.raises(AnalysisInputError):
            create_analysis(
                db,
                settings=settings,
                organization_id=local.organization_id,
                actor_user_id=local.user_id,
                document_version_ids=[local.document_version_id],
                idempotency_key="pending-version",
            )
        with pytest.raises(AnalysisInputError):
            create_analysis(
                db,
                settings=settings,
                organization_id=local.organization_id,
                actor_user_id=local.user_id,
                document_version_ids=[foreign.document_version_id],
                idempotency_key="foreign-version",
            )


def test_worker_refuses_a_segment_snapshot_that_no_longer_matches_the_hashed_manifest():
    engine = _engine()
    seed = _seed_extracted_document(engine)
    _enqueue(engine, seed)
    with Session(engine, expire_on_commit=False) as db:
        segment = db.get(DocumentSegment, seed.native_segment_id)
        assert segment is not None
        segment.text = "Texte modifié après création du manifeste."
        db.commit()
    with Session(engine, expire_on_commit=False) as db:
        set_db_request_context(db, user_id=seed.user_id, organization_id=seed.organization_id)
        claim = claim_next_analysis_detection_job(
            db,
            settings=settings,
            organization_id=seed.organization_id,
            worker_id="manifest-integrity-test",
        )
        assert claim is not None
        db.commit()
    with Session(engine) as db:
        set_db_request_context(db, user_id=seed.user_id, organization_id=seed.organization_id)
        with pytest.raises(AnalysisDetectionTerminalError) as raised:
            process_claimed_analysis_detection(db, claim=claim)
        assert raised.value.code == "input_segment_manifest_mismatch"


def test_separate_worker_claims_and_processes_tenant_scoped_job(monkeypatch):
    engine = _engine()
    seed = _seed_extracted_document(engine)
    _enqueue(engine, seed)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    worker = AnalysisDetectionWorker(settings=settings, session_factory=factory, worker_id="worker-test")
    monkeypatch.setattr(worker, "_active_organization_ids", lambda: [seed.organization_id])

    assert worker.run_once() is True
    with Session(engine) as db:
        job = db.scalar(select(AnalysisDetectionJob))
        assert job is not None
        assert job.status == AnalysisDetectionJobStatus.COMPLETED
        assert db.scalar(select(func.count()).select_from(Claim)) == 2


def test_analysis_api_queues_is_idempotent_and_exposes_persisted_snapshot():
    """The public API requires CSRF/RBAC and returns 202 before worker processing."""
    from app.main import app

    suffix = uuid4().hex[:10]
    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="analyst")
        with SessionLocal() as db:
            document = Document(
                organization_id=identity.organization_id,
                document_key=f"API-DOC-{suffix}",
                title="Déclaration API",
            )
            db.add(document)
            db.flush()
            version = DocumentVersion(
                organization_id=identity.organization_id,
                document_id=document.id,
                version_number=1,
                source_filename="api.txt",
                content_type="text/plain",
                storage_key=f"api/{suffix}",
                sha256=_hash("c"),
                size_bytes=24,
                extraction_status=ExtractionStatus.COMPLETED,
                extracted_text_sha256=_hash("d"),
            )
            db.add(version)
            db.flush()
            db.add(
                DocumentSegment(
                    organization_id=identity.organization_id,
                    document_version_id=version.id,
                    sequence_number=0,
                    page_number=1,
                    text="Emballage recyclable.",
                    start_offset=0,
                    end_offset=22,
                    source_sha256=version.sha256,
                )
            )
            version_id = version.id
            db.commit()

        missing_key = client.post("/api/v1/analyses", json={"document_version_ids": [str(version_id)]})
        assert missing_key.status_code == 422
        created = client.post(
            "/api/v1/analyses",
            json={"document_version_ids": [str(version_id)]},
            headers={"Idempotency-Key": f"api-{suffix}"},
        )
        assert created.status_code == 202, created.text
        payload = created.json()
        assert payload["version"]["status"] == "queued"
        assert payload["version"]["rulebook_version"] == "not-applicable-deterministic-claims-v1"
        assert payload["detection_job"]["status"] == "queued"
        assert "avis juridique" in payload["disclaimer"]
        replay = client.post(
            "/api/v1/analyses",
            json={"document_version_ids": [str(version_id)]},
            headers={"Idempotency-Key": f"api-{suffix}"},
        )
        assert replay.status_code == 202
        assert replay.json()["idempotent_replay"] is True
        assert replay.json()["analysis"]["id"] == payload["analysis"]["id"]

        summary = client.get(f"/api/v1/analyses/{payload['analysis']['id']}")
        assert summary.status_code == 200, summary.text
        assert summary.json()["latest_version"]["input_manifest_sha256"] == payload["version"]["input_manifest_sha256"]

        with SessionLocal() as db:
            set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
            worker_claim = claim_next_analysis_detection_job(
                db,
                settings=settings,
                organization_id=identity.organization_id,
                worker_id="api-test-worker",
            )
            assert worker_claim is not None
            db.commit()
        with SessionLocal() as db:
            set_db_request_context(db, user_id=identity.user_id, organization_id=identity.organization_id)
            process_claimed_analysis_detection(db, claim=worker_claim)
            db.commit()

        snapshot = client.get(f"/api/v1/analyses/{payload['analysis']['id']}/versions/1")
        assert snapshot.status_code == 200, snapshot.text
        snapshot_body = snapshot.json()
        assert snapshot_body["version"]["status"] == "completed"
        assert len(snapshot_body["claims"]) == 1
        claim = snapshot_body["claims"][0]
        assert claim["confidence_score"] is None
        assert claim["citation"]["page_number"] == 1
        claim_detail = client.get(f"/api/v1/claims/{claim['id']}")
        assert claim_detail.status_code == 200
        assert claim_detail.json()["document_segment_id"] == claim["document_segment_id"]

        retried = client.post(
            f"/api/v1/analyses/{payload['analysis']['id']}/retry",
            headers={"Idempotency-Key": f"retry-{suffix}"},
        )
        assert retried.status_code == 202, retried.text
        assert retried.json()["version"]["version_number"] == 2
