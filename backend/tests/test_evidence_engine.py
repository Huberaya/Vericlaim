"""Tests for Chantier 6.2 — Persistent Evidence Registry and Claim-Evidence Matrix."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.domain import (
    Analysis,
    AnalysisStatus,
    AnalysisVersion,
    AuditEvent,
    AuthSession,
    Claim,
    ClaimSource,
    ClaimStatus,
    Document,
    DocumentSegment,
    DocumentVersion,
    Evidence,
    EvidenceLink,
    EvidenceRelation,
    EvidenceStatus,
    EvidenceType,
    Membership,
    MembershipStatus,
    Product,
    Role,
    SegmentType,
    Supplier,
)
from tests.auth_support import authenticate_client


def _hash(char: str = "a") -> str:
    return char * 64


def test_evidence_crud_and_tenant_isolation():
    from app.main import app

    with TestClient(app) as client, TestClient(app) as foreign_client, TestClient(app) as viewer_client:
        identity = authenticate_client(client, role_code="analyst")
        foreign_identity = authenticate_client(foreign_client, role_code="analyst")
        viewer_client_identity = authenticate_client(viewer_client, role_code="viewer")

        # Create Supplier and Product in identity's tenant
        supp_resp = client.post(
            "/api/v1/suppliers",
            json={"legal_name": "Fournisseur Emballage SAS", "country_code": "FR"},
            headers={"Idempotency-Key": "supp-ev-01"},
        )
        assert supp_resp.status_code == 201, supp_resp.text
        supplier_id = supp_resp.json()["supplier"]["id"]

        prod_resp = client.post(
            "/api/v1/products",
            json={"supplier_id": supplier_id, "reference": "EMB-PET-100", "name": "Bouteille PET"},
            headers={"Idempotency-Key": "prod-ev-01"},
        )
        assert prod_resp.status_code == 201, prod_resp.text
        product_id = prod_resp.json()["product"]["id"]

        # 1. Create Evidence (Valid ISO certificate)
        create_payload = {
            "evidence_type": "certificate",
            "reference": "CERT-ISO-14021-2026",
            "issuer": "Bureau Veritas",
            "issued_on": "2025-01-01",
            "expires_on": (date.today() + timedelta(days=365)).isoformat(),
            "product_scope": "Bouteilles PET recyclables et emballages alimentaires",
            "supplier_id": supplier_id,
            "product_id": product_id,
            "evidence_metadata": {"standard": "ISO 14021", "accredited": True},
        }
        res = client.post("/api/v1/evidence", json=create_payload)
        assert res.status_code == 201, res.text
        ev_data = res.json()["evidence"]
        evidence_id = ev_data["id"]
        assert ev_data["reference"] == "CERT-ISO-14021-2026"
        assert ev_data["status"] == "present"
        assert ev_data["issuer"] == "Bureau Veritas"

        # 2. Foreign tenant cannot read or update
        foreign_get = foreign_client.get(f"/api/v1/evidence/{evidence_id}")
        assert foreign_get.status_code == 404

        # 3. Viewer in different organization gets empty list and 404 on foreign ID
        viewer_list = viewer_client.get("/api/v1/evidence")
        assert viewer_list.status_code == 200
        assert viewer_list.json()["items"] == []

        viewer_foreign = viewer_client.get(f"/api/v1/evidence/{evidence_id}")
        assert viewer_foreign.status_code == 404

        # Viewer cannot create evidence
        viewer_denied = viewer_client.post(
            "/api/v1/evidence",
            json={"evidence_type": "lca_report", "reference": "LCA-DENIED"},
        )
        assert viewer_denied.status_code == 403

        # Add viewer membership in the analyst's organization to verify viewer can read same-org evidence
        with SessionLocal() as db:
            viewer_role = db.scalar(select(Role).where(Role.code == "viewer"))
            viewer_session = db.scalar(select(AuthSession).where(AuthSession.user_id == viewer_client_identity.user_id))
            assert viewer_role is not None and viewer_session is not None
            db.add(
                Membership(
                    organization_id=identity.organization_id,
                    user_id=viewer_client_identity.user_id,
                    role_id=viewer_role.id,
                    status=MembershipStatus.ACTIVE,
                )
            )
            viewer_session.active_organization_id = identity.organization_id
            db.commit()

        viewer_same_org_get = viewer_client.get(f"/api/v1/evidence/{evidence_id}")
        assert viewer_same_org_get.status_code == 200
        assert viewer_same_org_get.json()["id"] == evidence_id

        # 4. List evidence with filters
        list_res = client.get(f"/api/v1/evidence?supplier_id={supplier_id}&evidence_type=certificate")
        assert list_res.status_code == 200
        assert len(list_res.json()["items"]) == 1
        assert list_res.json()["items"][0]["id"] == evidence_id

        # 5. Update Evidence
        patch_res = client.patch(
            f"/api/v1/evidence/{evidence_id}",
            json={"issuer": "Bureau Veritas Certification France"},
        )
        assert patch_res.status_code == 200
        assert patch_res.json()["issuer"] == "Bureau Veritas Certification France"

        # 6. Check Audit Trail
        with SessionLocal() as db:
            events = db.scalars(
                select(AuditEvent.action).where(
                    AuditEvent.organization_id == identity.organization_id,
                    AuditEvent.entity_id == UUID(evidence_id),
                )
            ).all()
            assert "evidence.created" in events
            assert "evidence.updated" in events

        # 7. Delete Evidence (soft delete)
        del_res = client.delete(f"/api/v1/evidence/{evidence_id}")
        assert del_res.status_code == 204
        assert client.get(f"/api/v1/evidence/{evidence_id}").status_code == 404


def test_expired_evidence_and_date_invariants():
    from app.main import app

    with TestClient(app) as client:
        authenticate_client(client, role_code="analyst")

        # Invalid date range (expires before issue)
        invalid_dates = client.post(
            "/api/v1/evidence",
            json={
                "evidence_type": "lab_report",
                "issued_on": "2026-06-01",
                "expires_on": "2025-06-01",
            },
        )
        assert invalid_dates.status_code == 422

        # Expired certificate is automatically marked EXPIRED
        past_expired = client.post(
            "/api/v1/evidence",
            json={
                "evidence_type": "certificate",
                "reference": "EXPIRED-2023",
                "issued_on": "2022-01-01",
                "expires_on": "2023-01-01",
            },
        )
        assert past_expired.status_code == 201, past_expired.text
        assert past_expired.json()["evidence"]["status"] == "expired"


def test_claim_evidence_linking_and_matrix_generation():
    from app.main import app

    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="analyst")

        # Setup persistent analysis, version, segment and claim in DB
        analysis_id = uuid4()
        version_id = uuid4()
        segment_id = uuid4()
        claim_1_id = uuid4()
        claim_2_id = uuid4()
        doc_id = uuid4()
        doc_ver_id = uuid4()

        with SessionLocal() as db:
            doc = Document(
                id=doc_id,
                organization_id=identity.organization_id,
                document_key="DOC-EVID-01",
                title="Plaquette Bouteille RSE",
            )
            doc_ver = DocumentVersion(
                id=doc_ver_id,
                organization_id=identity.organization_id,
                document_id=doc_id,
                version_number=1,
                source_filename="plaquette.pdf",
                content_type="application/pdf",
                storage_key=f"org/docs/{uuid4().hex}.pdf",
                sha256=_hash("1"),
                size_bytes=1024,
            )
            segment = DocumentSegment(
                id=segment_id,
                organization_id=identity.organization_id,
                document_version_id=doc_ver_id,
                sequence_number=1,
                page_number=1,
                segment_type=SegmentType.PARAGRAPH,
                text="Notre emballage est 100% recyclable et neutre en carbone.",
                source_sha256=_hash("s"),
            )
            analysis = Analysis(
                id=analysis_id,
                organization_id=identity.organization_id,
                analysis_key="ANL-EVID-TEST",
                status=AnalysisStatus.COMPLETED,
            )
            version = AnalysisVersion(
                id=version_id,
                organization_id=identity.organization_id,
                analysis_id=analysis_id,
                version_number=1,
                engine_version="0.1.0",
                rulebook_version="rulebook-v1",
                input_manifest_sha256=_hash("m"),
                status=AnalysisStatus.COMPLETED,
            )
            claim_1 = Claim(
                id=claim_1_id,
                organization_id=identity.organization_id,
                analysis_version_id=version_id,
                document_segment_id=segment_id,
                claim_type="recyclable",
                category="recycling",
                claim_text="Notre emballage est 100% recyclable",
                status=ClaimStatus.DETECTED,
                source=ClaimSource.DETERMINISTIC,
            )
            claim_2 = Claim(
                id=claim_2_id,
                organization_id=identity.organization_id,
                analysis_version_id=version_id,
                document_segment_id=segment_id,
                claim_type="carbon_neutrality",
                category="carbon",
                claim_text="neutre en carbone",
                status=ClaimStatus.DETECTED,
                source=ClaimSource.DETERMINISTIC,
            )
            db.add_all([doc, doc_ver, segment, analysis, version, claim_1, claim_2])
            db.commit()

        # Create valid recycling evidence
        ev_recycling = client.post(
            "/api/v1/evidence",
            json={
                "evidence_type": "recycling_route",
                "reference": "FILIERE-CITEO-2026",
                "issuer": "Citeo / Valorplast",
                "product_scope": "Bouteilles et flacons PET",
                "expires_on": (date.today() + timedelta(days=200)).isoformat(),
            },
        ).json()["evidence"]

        # Link recycling evidence to claim 1
        link_res = client.post(
            f"/api/v1/claims/{claim_1_id}/evidence-links",
            json={
                "evidence_id": ev_recycling["id"],
                "relation": "supports",
            },
        )
        assert link_res.status_code == 201, link_res.text
        link_data = link_res.json()
        # C17 — changement de comportement assumé : cette preuve de filière est rattachée à
        # l'organisation mais à **aucun produit ni fournisseur** (périmètre en texte libre
        # seulement), et l'analyse non plus n'a pas de produit. Le produit ne peut donc pas
        # comparer les périmètres, et il ne prononce plus « present » : il dit « partial ».
        # Avant C17, ce rattachement était enregistré « present » par défaut, c'est-à-dire
        # déclaré utilisable sans qu'aucun périmètre n'ait été vérifié — précisément le cas E
        # de l'audit. Le motif conserve la référence et l'émetteur de la pièce.
        assert link_data["coverage_status"] == "pending", (
            "aucune décision humaine déclarée : le lien ne se déclare pas couvrant"
        )
        assert link_data["observed_state"] == "partial"
        assert "Citeo" in link_data["rationale"]

        # Get Claim Links
        get_links = client.get(f"/api/v1/claims/{claim_1_id}/evidence-links")
        assert get_links.status_code == 200
        assert len(get_links.json()) == 1

        # Fetch Analysis Claim-Evidence Matrix
        matrix_res = client.get(f"/api/v1/analyses/{analysis_id}/evidence-matrix")
        assert matrix_res.status_code == 200, matrix_res.text
        matrix = matrix_res.json()
        assert matrix["total_claims"] == 2
        assert matrix["claims_with_evidence"] == 1
        assert matrix["claims_missing_evidence"] == 1
        assert len(matrix["matrix_rows"]) == 2

        # Check Claim 1 in matrix
        #
        # C17 — changement de comportement assumé, et visible : la matrice ne calcule plus
        # selon sa propre règle. La pièce de filière est bien rattachée, mais ni elle ni
        # l'analyse ne portent de produit ou de fournisseur : le périmètre n'est pas
        # comparable, donc l'état est « partial » et non « present ». Avant C17, la matrice
        # prononçait « present » sur la seule présence d'un lien marqué ainsi — le cas E de
        # l'audit, où une pièce sans lien vérifiable était comptée comme couvrante.
        row_1 = next(r for r in matrix["matrix_rows"] if r["claim"]["id"] == str(claim_1_id))
        assert row_1["coverage_status"] == "partial"
        assert row_1["is_sufficient"] is False
        assert len(row_1["evidence_links"]) == 1, "la pièce reste rattachée et visible"
        assert "périmètre" in row_1["explanation"] or "comparé" in row_1["explanation"]

        # Check Claim 2 in matrix (no evidence linked -> missing)
        row_2 = next(r for r in matrix["matrix_rows"] if r["claim"]["id"] == str(claim_2_id))
        assert row_2["coverage_status"] == "missing"
        assert row_2["is_sufficient"] is False
        assert len(row_2["evidence_links"]) == 0
        assert "Aucun justificatif" in row_2["explanation"]

        # Unlink claim 1
        unlink_res = client.delete(f"/api/v1/evidence-links/{link_data['id']}")
        assert unlink_res.status_code == 204

        # Re-fetch matrix -> now both are missing
        matrix_after = client.get(f"/api/v1/analyses/{analysis_id}/evidence-matrix").json()
        assert matrix_after["claims_with_evidence"] == 0
        assert matrix_after["claims_missing_evidence"] == 2
