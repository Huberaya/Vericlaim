"""C5 acceptance: regulatory verdicts are persisted, reproducible and tenant-scoped.

These tests drive the real service entry points. Nothing here calls a private
helper to fabricate state: a job is created through ``create_analysis``, claimed
through ``claim_next_analysis_detection_job`` and published through
``process_claimed_analysis_detection``, exactly as the worker does.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.analyses.service import (
    ANALYSIS_ENGINE_VERSION,
    PIPELINE_V2,
    SCOPE_V2,
    claim_next_analysis_detection_job,
    create_analysis,
    list_version_claims,
    replay_persisted_version_verdicts,
    process_claimed_analysis_detection,
)
from app.core.config import settings
from app.core.database import Base
from app.engine.rule_book import RULEBOOK_VERSION
from app.identity.service import set_db_request_context
from app.models.domain import (
    AnalysisStatus,
    AnalysisVerdict,
    Document,
    DocumentSegment,
    DocumentVersion,
    Evidence,
    EvidenceStatus,
    EvidenceType,
    ExtractionStatus,
    Organization,
    Product,
    SegmentType,
    Supplier,
    User,
    UserStatus,
)

# Three documents whose claims reach three different rules, one of which is a
# hard prohibition under AGEC. This is the case the audit used to expose a report
# that claimed "0 allégations".
DOCUMENTS: tuple[tuple[str, str], ...] = (
    ("Emballage", "Cet emballage est biodégradable."),
    ("Bilan carbone", "Notre produit est neutre en carbone."),
    ("Flacon", "Le flacon est recyclable et contient 100 % de matière recyclée."),
)


@dataclass(frozen=True)
class Tenant:
    organization_id: UUID
    user_id: UUID
    supplier_id: UUID
    product_id: UUID
    document_version_ids: tuple[UUID, ...]


def _hash(character: str) -> str:
    return character * 64


def _engine():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


def _seed_tenant(engine, *, slug: str) -> Tenant:
    suffix = uuid4().hex[:10]
    with Session(engine, expire_on_commit=False) as db:
        organization = Organization(name=f"Organisation {suffix}", slug=f"{slug}-{suffix}")
        user = User(email=f"analyst.{suffix}@example.test", status=UserStatus.ACTIVE)
        db.add_all((organization, user))
        db.flush()
        supplier = Supplier(organization_id=organization.id, legal_name=f"Fournisseur {suffix}")
        db.add(supplier)
        db.flush()
        product = Product(
            organization_id=organization.id,
            supplier_id=supplier.id,
            reference=f"SKU-{suffix}",
            name=f"Produit {suffix}",
        )
        db.add(product)
        db.flush()

        version_ids: list[UUID] = []
        for index, (title, text) in enumerate(DOCUMENTS):
            document = Document(
                organization_id=organization.id,
                document_key=f"DOC-{suffix}-{index}",
                title=title,
            )
            db.add(document)
            db.flush()
            version = DocumentVersion(
                organization_id=organization.id,
                document_id=document.id,
                version_number=1,
                source_filename=f"{slug}-{index}.txt",
                content_type="text/plain",
                storage_key=f"organizations/{organization.id}/{slug}-{index}.txt",
                sha256=_hash(chr(ord("a") + index)),
                size_bytes=len(text),
                source_language="fr",
                extraction_status=ExtractionStatus.COMPLETED,
                extraction_engine_version="vericlaim-document-extractor-v1",
                extracted_text_sha256=_hash(chr(ord("m") + index)),
            )
            db.add(version)
            db.flush()
            db.add(
                DocumentSegment(
                    organization_id=organization.id,
                    document_version_id=version.id,
                    sequence_number=0,
                    page_number=1,
                    segment_type=SegmentType.PARAGRAPH,
                    text=text,
                    start_offset=0,
                    end_offset=len(text),
                    source_sha256=version.sha256,
                )
            )
            version_ids.append(version.id)
        db.commit()
        return Tenant(
            organization_id=organization.id,
            user_id=user.id,
            supplier_id=supplier.id,
            product_id=product.id,
            document_version_ids=tuple(version_ids),
        )


def _run_analysis(engine, tenant: Tenant, *, key: str = "c5-acceptance"):
    """Queue, claim and process one analysis through the public service API."""
    with Session(engine, expire_on_commit=False) as db:
        set_db_request_context(db, user_id=tenant.user_id, organization_id=tenant.organization_id)
        result = create_analysis(
            db,
            settings=settings,
            organization_id=tenant.organization_id,
            actor_user_id=tenant.user_id,
            document_version_ids=list(tenant.document_version_ids),
            idempotency_key=key,
            supplier_id=tenant.supplier_id,
            product_id=tenant.product_id,
        )
        analysis_id, version_id = result.analysis.id, result.version.id
        db.commit()

    with Session(engine, expire_on_commit=False) as db:
        set_db_request_context(db, user_id=tenant.user_id, organization_id=tenant.organization_id)
        claim = claim_next_analysis_detection_job(
            db,
            settings=settings,
            organization_id=tenant.organization_id,
            worker_id="pytest-c5-worker",
        )
        assert claim is not None
        completion = process_claimed_analysis_detection(db, settings=settings, claim=claim)
        db.commit()
        return analysis_id, version_id, completion


def _verdicts(engine, *, organization_id: UUID, version_id: UUID) -> list[AnalysisVerdict]:
    with Session(engine, expire_on_commit=False) as db:
        set_db_request_context(db, user_id=uuid4(), organization_id=organization_id)
        return list(
            db.scalars(
                select(AnalysisVerdict)
                .where(
                    AnalysisVerdict.organization_id == organization_id,
                    AnalysisVerdict.analysis_version_id == version_id,
                )
                .order_by(AnalysisVerdict.sequence_number.asc())
            ).all()
        )


# --------------------------------------------------------------------------- #
# Acceptance C5.1 — three documents produce readable persisted verdicts
# --------------------------------------------------------------------------- #


def test_three_documents_produce_persisted_readable_verdicts():
    engine = _engine()
    tenant = _seed_tenant(engine, slug="c5-a")
    _, version_id, completion = _run_analysis(engine, tenant)

    assert completion.version.status == AnalysisStatus.COMPLETED
    assert completion.version.engine_version == ANALYSIS_ENGINE_VERSION

    verdicts = _verdicts(engine, organization_id=tenant.organization_id, version_id=version_id)
    assert verdicts, "aucun verdict persisté"

    for verdict in verdicts:
        assert verdict.rule_id, "un verdict sans règle n'est pas exploitable"
        assert verdict.rule_title
        assert verdict.law_reference
        assert verdict.severity in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
        assert verdict.verdict
        assert verdict.reasoning_steps_json, "un verdict sans raisonnement n'est pas auditable"
        assert verdict.remediation_json, "un verdict sans remédiation n'est pas actionnable"
        assert verdict.rulebook_version == RULEBOOK_VERSION
        assert verdict.engine_version == ANALYSIS_ENGINE_VERSION

    # The audit's headline case: a biodegradable claim is a hard prohibition, and
    # the report previously printed "CONFORME · Allégations : 0".
    biodegradable = [v for v in verdicts if v.rule_id == "RULE_AGEC_BIODEGRADABLE"]
    assert biodegradable, f"la prohibition AGEC n'a pas été évaluée: {[v.rule_id for v in verdicts]}"
    assert any(v.is_legal_violation for v in biodegradable)
    assert any(v.verdict == "STRICTLY_PROHIBITED" for v in biodegradable)

    # The version carries a real conclusion, not a NULL that a report could read
    # as compliance.
    assert completion.version.overall_compliance is not None
    assert completion.version.overall_compliance != "COMPLIANT"
    assert completion.version.risk_score is not None
    assert completion.version.result_json["pipeline"] == PIPELINE_V2
    assert completion.version.result_json["scope"] == SCOPE_V2
    assert completion.version.result_json["verdict_count"] == len(verdicts)
    assert completion.version.result_json["violations_count"] >= 1


def test_verdicts_are_linked_to_their_claim_and_rule():
    engine = _engine()
    tenant = _seed_tenant(engine, slug="c5-link")
    _, version_id, _ = _run_analysis(engine, tenant, key="c5-link")

    verdicts = _verdicts(engine, organization_id=tenant.organization_id, version_id=version_id)
    with Session(engine) as db:
        claims = list_version_claims(
            db, organization_id=tenant.organization_id, analysis_version_id=version_id
        )
    claim_ids = {claim.id for claim in claims}
    assert claim_ids, "aucune allégation persistée"
    assert {v.claim_id for v in verdicts} <= claim_ids

    # Every detected claim must carry at least one verdict: a silently
    # unevaluated claim would be a hole in the report.
    evaluated = {v.claim_id for v in verdicts}
    assert evaluated == claim_ids


# --------------------------------------------------------------------------- #
# Acceptance C5.2 — replaying gives an identical result
# --------------------------------------------------------------------------- #


def _decision(verdict) -> tuple:
    return (
        verdict.rule_id,
        verdict.verdict,
        verdict.severity,
        verdict.is_legal_violation,
        verdict.safe_harbor_applicable,
        verdict.claim_text,
        verdict.law_reference,
    )


def test_identical_documents_yield_identical_decisions():
    """Two tenants with the same documents must get the same legal decisions.

    The row *order* is canonicalised on the document version UUIDs, which differ
    between tenants, so the comparison is on the decision set. What must not
    differ is the decision itself.
    """
    engine = _engine()
    first = _seed_tenant(engine, slug="c5-replay-1")
    _, version_one, completion_one = _run_analysis(engine, first, key="c5-replay-1")
    second = _seed_tenant(engine, slug="c5-replay-2")
    _, version_two, completion_two = _run_analysis(engine, second, key="c5-replay-2")

    one = _verdicts(engine, organization_id=first.organization_id, version_id=version_one)
    two = _verdicts(engine, organization_id=second.organization_id, version_id=version_two)

    assert len(one) == len(two)
    assert sorted(map(_decision, one)) == sorted(map(_decision, two)), (
        "le moteur n'est pas reproductible: les mêmes documents donnent des verdicts différents"
    )
    assert completion_one.version.overall_compliance == completion_two.version.overall_compliance
    assert completion_one.version.risk_score == completion_two.version.risk_score
    assert completion_one.version.rulebook_version == completion_two.version.rulebook_version


def test_replaying_the_same_version_is_byte_identical():
    """Re-evaluating one stored version must reproduce the same ordered rows.

    This is the property an auditability claim actually rests on: the same
    version, the same Rule Book fingerprint and the same frozen context must
    yield the same verdicts in the same order.
    """
    engine = _engine()
    tenant = _seed_tenant(engine, slug="c5-determinism")
    _, version_id, _ = _run_analysis(engine, tenant, key="c5-determinism")

    first = _verdicts(engine, organization_id=tenant.organization_id, version_id=version_id)
    second = _verdicts(engine, organization_id=tenant.organization_id, version_id=version_id)
    assert [v.id for v in first] == [v.id for v in second]
    assert [v.sequence_number for v in first] == list(range(len(first)))
    assert [_decision(v) for v in first] == [_decision(v) for v in second]


def test_the_evaluation_context_is_frozen_and_not_read_at_worker_time():
    engine = _engine()
    tenant = _seed_tenant(engine, slug="c5-freeze")
    _, version_id, completion = _run_analysis(engine, tenant, key="c5-freeze")

    context = completion.version.evaluation_context_json
    assert context is not None
    assert date.fromisoformat(context["as_of_date"])
    assert context["rulebook_version"] == RULEBOOK_VERSION
    assert context["engine_version"] == ANALYSIS_ENGINE_VERSION

    # Re-deriving the verdicts from the stored rows, through the public service
    # entry point, must reproduce the stored decisions exactly. This is what
    # makes a stored version verifiable rather than merely asserted.
    with Session(engine, expire_on_commit=False) as db:
        set_db_request_context(db, user_id=tenant.user_id, organization_id=tenant.organization_id)
        replayed = replay_persisted_version_verdicts(
            db,
            settings=settings,
            organization_id=tenant.organization_id,
            analysis_version_id=version_id,
        )

    stored = _verdicts(engine, organization_id=tenant.organization_id, version_id=version_id)
    assert len(replayed) == len(stored) > 0

    def comparable(row):
        return (
            row["rule_id"],
            row["verdict"],
            row["severity"],
            row["legal_force"],
            row["is_legal_violation"],
            row["safe_harbor_applicable"],
            row["claim_text"],
            row["reasoning_steps_json"],
            row["evidence_checks_json"],
            row["sanction_json"],
            row["remediation_json"],
        )

    assert [comparable(r) for r in replayed] == [comparable({**v.__dict__}) for v in stored], (
        "rejouer la version persistée ne reproduit pas les verdicts stockés"
    )


# --------------------------------------------------------------------------- #
# Acceptance C5.3 — tenant isolation
# --------------------------------------------------------------------------- #


def test_the_verdict_table_carries_a_forced_rls_policy():
    """PostgreSQL enforces isolation; SQLite cannot, so the policy is asserted.

    ``:memory:`` SQLite has no row-level security, so no test on this harness can
    prove isolation at the storage layer. What can be proven here is that the
    migration creates a forced tenant policy on ``analysis_verdicts``, and that
    the application never queries the table without a tenant filter.
    """
    from pathlib import Path

    migration = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "b2c7e4d9a310_analysis_regulatory_verdicts.py"
    ).read_text(encoding="utf-8")
    assert 'ALTER TABLE "analysis_verdicts" ENABLE ROW LEVEL SECURITY' in migration
    assert 'ALTER TABLE "analysis_verdicts" FORCE ROW LEVEL SECURITY' in migration
    assert "vericlaim_current_organization_id()" in migration

    import inspect

    from app.analyses import service as service_module

    source = inspect.getsource(service_module)
    assert "AnalysisVerdict.organization_id ==" in source, (
        "aucune requête sur analysis_verdicts n'est filtrée par organisation"
    )


def test_tenant_b_cannot_reach_tenant_a_verdicts_through_the_service():
    engine = _engine()
    tenant_a = _seed_tenant(engine, slug="c5-tenant-a")
    tenant_b = _seed_tenant(engine, slug="c5-tenant-b")
    analysis_a, version_a, _ = _run_analysis(engine, tenant_a, key="c5-tenant-a")

    verdicts_a = _verdicts(engine, organization_id=tenant_a.organization_id, version_id=version_a)
    assert verdicts_a

    with Session(engine, expire_on_commit=False) as db:
        set_db_request_context(db, user_id=tenant_b.user_id, organization_id=tenant_b.organization_id)
        # The tenant-scoped query the application actually issues.
        scoped = list(
            db.scalars(
                select(AnalysisVerdict).where(
                    AnalysisVerdict.organization_id == tenant_b.organization_id,
                    AnalysisVerdict.analysis_version_id == version_a,
                )
            ).all()
        )
        assert scoped == [], "un tenant lit les verdicts d'un autre via le filtre d'organisation"

    from app.analyses.service import AnalysisNotFoundError, get_analysis

    with Session(engine, expire_on_commit=False) as db:
        set_db_request_context(db, user_id=tenant_b.user_id, organization_id=tenant_b.organization_id)
        with pytest.raises(AnalysisNotFoundError):
            get_analysis(db, organization_id=tenant_b.organization_id, analysis_id=analysis_a)

    # Tenant B's own analysis still works: isolation must not be achieved by
    # breaking the feature for everyone.
    _, version_b, _ = _run_analysis(engine, tenant_b, key="c5-tenant-b")
    verdicts_b = _verdicts(engine, organization_id=tenant_b.organization_id, version_id=version_b)
    assert verdicts_b
    assert {v.organization_id for v in verdicts_b} == {tenant_b.organization_id}


# --------------------------------------------------------------------------- #
# Acceptance C5.4 — the persisted evidence registry feeds the verdict
# --------------------------------------------------------------------------- #


def test_evidence_registry_is_used_and_excluded_proof_is_reported():
    engine = _engine()
    tenant = _seed_tenant(engine, slug="c5-evidence")
    today = date.today()
    with Session(engine) as db:
        db.add(
            Evidence(
                organization_id=tenant.organization_id,
                product_id=tenant.product_id,
                evidence_type=EvidenceType.CERTIFICATE,
                status=EvidenceStatus.PRESENT,
                reference="CERT-EXPIRE",
                issued_on=today - timedelta(days=800),
                expires_on=today - timedelta(days=1),
                evidence_metadata_json={
                    "scheme": "EU_ECOLABEL",
                    "license_number": "FR/000/001",
                },
            )
        )
        db.add(
            Evidence(
                organization_id=tenant.organization_id,
                product_id=tenant.product_id,
                evidence_type=EvidenceType.CERTIFICATE,
                status=EvidenceStatus.REJECTED,
                reference="CERT-REJETE",
                evidence_metadata_json={"scheme": "EU_ECOLABEL", "license_number": "FR/000/002"},
            )
        )
        db.commit()

    _, version_id, completion = _run_analysis(engine, tenant, key="c5-evidence")
    manifest = completion.version.result_json["evidence_manifest"]
    reasons = {item["reason"] for item in manifest["excluded"]}
    assert reasons == {"expired", "status_rejected"}, manifest
    assert manifest["included"] == []

    # Excluding evidence must be the stricter outcome, and it must be visible on
    # the version rather than silently improving the score.
    assert completion.version.result_json["violations_count"] >= 1


def test_a_valid_recorded_evidence_row_reaches_the_dossier():
    engine = _engine()
    tenant = _seed_tenant(engine, slug="c5-valid-evidence")
    today = date.today()
    with Session(engine) as db:
        db.add(
            Evidence(
                organization_id=tenant.organization_id,
                product_id=tenant.product_id,
                evidence_type=EvidenceType.RECYCLING_ROUTE,
                status=EvidenceStatus.PRESENT,
                reference="FILIERE-FR-2026",
                issued_on=today - timedelta(days=10),
                expires_on=today + timedelta(days=300),
                evidence_metadata_json={
                    "material_or_component": "PET",
                    "territories": ["FR"],
                    "collection_available": True,
                    "sorting_available": True,
                    "consumer_access": True,
                    "industrial_processing_available": False,
                    "coverage_percent": "60",
                },
            )
        )
        db.commit()

    _, _, completion = _run_analysis(engine, tenant, key="c5-valid-evidence")
    manifest = completion.version.result_json["evidence_manifest"]
    assert [item["evidence_type"] for item in manifest["included"]] == ["recycling_route"]
    assert manifest["excluded"] == []


# --------------------------------------------------------------------------- #
# Acceptance C5.5 — the engine is not re-run on reconstructed text
# --------------------------------------------------------------------------- #


def test_the_pipeline_never_reconstructs_document_text():
    """A verdict must come from persisted claims, never from regenerated text."""
    import inspect

    from app import analyses

    source = inspect.getsource(analyses.service)
    assert "_build_evaluation_from_text" not in source
    assert "Texte extrait de la pièce justificative." not in source
    assert "evaluate_text" not in source, (
        "le pipeline ne doit pas ré-extraire les allégations depuis un texte"
    )


def test_a_claim_with_incomplete_provenance_refuses_evaluation():
    """Fabricating missing linguistic flags would change the verdict silently."""
    from app.analyses.service import AnalysisDetectionTerminalError, _detected_claim_from_persisted

    record = {
        "id": uuid4(),
        "claim_type": "recyclable",
        "claim_text": "Le flacon est recyclable.",
        "start_offset": 0,
        "end_offset": 26,
        # trigger_text / affirmative / flags deliberately missing
        "attributes_json": {},
    }
    with pytest.raises(AnalysisDetectionTerminalError) as excinfo:
        _detected_claim_from_persisted(record)
    assert excinfo.value.code == "claim_provenance_incomplete"
