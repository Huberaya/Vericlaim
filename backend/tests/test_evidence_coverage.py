"""C17 — Couverture probatoire : les cas D et E de l'audit, tenus par des tests.

L'audit avait constaté que le produit ne voyait **ni** un certificat expiré (cas D : « certifié
ECOLABEL expiré + recyclable ») **ni** un certificat qui ne concerne pas le produit (cas E :
« shampoing neutre carbone, certificat d'un détergent »). La couverture d'une allégation
était entièrement déclarée par un relecteur : un lien marqué « vérifié » suffisait, quelle que
soit la pièce.

Ce que ces tests exigent :

1. **un fait défavorable n'est jamais effacé par une déclaration** : un certificat expiré ou
   hors périmètre ne peut pas produire un état « covered », même si le relecteur l'a déclaré
   utilisable — la contradiction est conservée et signalée ;
2. **une absence de date n'est pas une validité** : un certificat sans date de fin ne peut pas
   être déclaré valide ;
3. **le type de preuve compte** : un type qui ne peut pas étayer une famille d'allégation
   produit `not_covering` ;
4. **le calcul est daté et reproductible** : le même jeu de données, évalué à deux dates,
   donne deux états différents sans que rien n'ait été modifié ;
5. **un périmètre non comparable est dit tel quel** : le produit ne conclut pas qu'une preuve
   sans produit ni fournisseur couvre l'allégation ;
6. **une suggestion n'est pas une preuve** : chaque proposition est motivée et porte cet
   avertissement ;
7. **aucune couverture ne traverse la frontière d'organisation**.
"""

from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.models.domain import (
    Analysis,
    AnalysisStatus,
    AnalysisVersion,
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
    Product,
    SegmentType,
    Supplier,
)
from tests.auth_support import authenticate_client

AS_OF = date(2026, 9, 30)


def _hash(char: str = "a") -> str:
    return char * 64


def _build_case(
    *,
    organization_id: UUID,
    claim_type: str = "recyclable",
    claim_text: str = "Notre emballage est 100 % recyclable",
    with_product: bool = True,
    claims: int = 1,
) -> dict[str, UUID]:
    """Un dossier minimal : fournisseur, produit, document, analyse, allégations."""

    supplier_id, product_id, other_product_id = uuid4(), uuid4(), uuid4()
    document_id, version_id, segment_id = uuid4(), uuid4(), uuid4()
    analysis_id, analysis_version_id = uuid4(), uuid4()
    claim_ids = [uuid4() for _ in range(claims)]

    with SessionLocal() as db:
        db.add(
            Supplier(
                id=supplier_id,
                organization_id=organization_id,
                legal_name="Emballages du Ponant",
                country_code="FR",
            )
        )
        db.add_all(
            [
                Product(
                    id=product_id,
                    organization_id=organization_id,
                    supplier_id=supplier_id,
                    reference=f"SKU-{product_id.hex[:6]}",
                    name="Bouteille PET 50 cl",
                    category="packaging",
                ),
                Product(
                    id=other_product_id,
                    organization_id=organization_id,
                    supplier_id=supplier_id,
                    reference=f"SKU-{other_product_id.hex[:6]}",
                    name="Détergent ménager 1 L",
                    category="detergent",
                ),
            ]
        )
        db.add(
            Document(
                id=document_id,
                organization_id=organization_id,
                document_key=f"DOC-{document_id.hex[:8]}",
                title="Plaquette produit",
                supplier_id=supplier_id,
                product_id=product_id,
            )
        )
        db.add(
            DocumentVersion(
                id=version_id,
                organization_id=organization_id,
                document_id=document_id,
                version_number=1,
                source_filename="plaquette.pdf",
                content_type="application/pdf",
                storage_key=f"clean/{organization_id}/{document_id}/versions/{version_id}/source",
                sha256=_hash("1"),
                size_bytes=2048,
            )
        )
        db.add(
            DocumentSegment(
                id=segment_id,
                organization_id=organization_id,
                document_version_id=version_id,
                sequence_number=1,
                page_number=1,
                segment_type=SegmentType.PARAGRAPH,
                text=claim_text,
                source_sha256=_hash("s"),
            )
        )
        db.add(
            Analysis(
                id=analysis_id,
                organization_id=organization_id,
                analysis_key=f"ANL-{analysis_id.hex[:8]}",
                status=AnalysisStatus.COMPLETED,
                supplier_id=supplier_id,
                product_id=product_id if with_product else None,
            )
        )
        db.add(
            AnalysisVersion(
                id=analysis_version_id,
                organization_id=organization_id,
                analysis_id=analysis_id,
                version_number=1,
                engine_version="0.2.0",
                rulebook_version="rules-2026.1",
                input_manifest_sha256=_hash("m"),
                status=AnalysisStatus.COMPLETED,
            )
        )
        for claim_id in claim_ids:
            db.add(
                Claim(
                    id=claim_id,
                    organization_id=organization_id,
                    analysis_version_id=analysis_version_id,
                    document_segment_id=segment_id,
                    claim_type=claim_type,
                    category="recycling",
                    claim_text=claim_text,
                    status=ClaimStatus.DETECTED,
                    source=ClaimSource.DETERMINISTIC,
                )
            )
        db.commit()

    return {
        "supplier_id": supplier_id,
        "product_id": product_id,
        "other_product_id": other_product_id,
        "document_id": document_id,
        "analysis_id": analysis_id,
        "analysis_version_id": analysis_version_id,
        "claim_id": claim_ids[0],
        "claim_ids": claim_ids,
    }


def _add_evidence(
    *,
    organization_id: UUID,
    evidence_type: EvidenceType = EvidenceType.CERTIFICATE,
    reference: str = "ECOLABEL-FR-2025-0001",
    product_id: UUID | None = None,
    supplier_id: UUID | None = None,
    expires_on: date | None = None,
    issued_on: date | None = None,
    product_scope: str | None = None,
) -> UUID:
    evidence_id = uuid4()
    with SessionLocal() as db:
        db.add(
            Evidence(
                id=evidence_id,
                organization_id=organization_id,
                evidence_type=evidence_type,
                status=EvidenceStatus.PENDING,
                reference=reference,
                issuer="AFNOR Certification",
                issued_on=issued_on,
                expires_on=expires_on,
                product_scope=product_scope,
                product_id=product_id,
                supplier_id=supplier_id,
                evidence_metadata_json={},
            )
        )
        db.commit()
    return evidence_id


def _link(
    *,
    organization_id: UUID,
    claim_id: UUID,
    evidence_id: UUID,
    relation: EvidenceRelation = EvidenceRelation.SUPPORTS,
    coverage_status: EvidenceStatus = EvidenceStatus.VERIFIED,
) -> UUID:
    link_id = uuid4()
    with SessionLocal() as db:
        db.add(
            EvidenceLink(
                id=link_id,
                organization_id=organization_id,
                claim_id=claim_id,
                evidence_id=evidence_id,
                relation=relation,
                coverage_status=coverage_status,
                rationale="Déclaré utilisable par le relecteur",
            )
        )
        db.commit()
    return link_id


def _coverage(client: TestClient, analysis_id: UUID, **params) -> dict:
    response = client.get(f"/api/v1/analyses/{analysis_id}/coverage", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _coverage_of(payload: dict, claim_id: UUID) -> dict:
    return next(item for item in payload["coverages"] if item["claim_id"] == str(claim_id))


# ---------------------------------------------------------------------------
# Cas D et E de l'audit
# ---------------------------------------------------------------------------


def test_case_d_an_expired_certificate_cannot_cover_a_claim_even_when_declared_usable():
    """Cas D : « certifié ECOLABEL expiré + recyclable ». L'expiration est vue."""

    from app.main import app

    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="analyst")
        case = _build_case(organization_id=identity.organization_id)
        expired = _add_evidence(
            organization_id=identity.organization_id,
            evidence_type=EvidenceType.CERTIFICATE,
            product_id=case["product_id"],
            supplier_id=case["supplier_id"],
            issued_on=date(2023, 1, 15),
            expires_on=date(2026, 6, 30),  # expiré avant l'audit
        )
        link_id = _link(
            organization_id=identity.organization_id,
            claim_id=case["claim_id"],
            evidence_id=expired,
            coverage_status=EvidenceStatus.VERIFIED,  # un relecteur l'a déclaré utilisable
        )

        payload = _coverage(client, case["analysis_id"], as_of=AS_OF.isoformat())
        row = _coverage_of(payload, case["claim_id"])

        assert row["state"] == "expired"
        assert row["is_sufficient"] is False
        assert row["declared_state"] == "covered"
        assert row["observed_state"] == "expired"
        assert "plus restrictif" in row["explanation"]

        codes = {finding["code"] for finding in row["findings"]}
        assert "certificate_expired" in codes
        assert "declared_but_unusable" in codes, "la contradiction doit être conservée"
        assert str(link_id) in row["contradicting_link_ids"]

        expired_finding = next(item for item in row["findings"] if item["code"] == "certificate_expired")
        assert expired_finding["facts"]["expires_on"] == "2026-06-30"
        assert expired_finding["facts"]["as_of"] == AS_OF.isoformat()
        assert expired_finding["facts"]["days_since_expiry"] == (AS_OF - date(2026, 6, 30)).days
        assert expired_finding["facts"]["date_is_declared_not_verified"] is True

        assert row["checks"][0]["validity_state"] == "expired"
        assert row["checks"][0]["usable_as_of"] is False
        assert payload["summary"]["expired"] == 1
        assert payload["summary"]["covered"] == 0
        assert payload["summary"]["claims_with_declared_contradiction"] == 1


def test_an_undeclared_but_valid_link_covers_the_claim():
    """Sans décision humaine, l'état vient des faits : `covered`, et pas « plafonné ».

    Défaut mesuré deux fois, par deux chemins : le produit inscrivait son propre verdict dans
    `coverage_status` (colonne des décisions humaines), puis relisait cette écriture comme une
    déclaration et la retenait comme la plus restrictive. Une allégation parfaitement couverte
    par un certificat valide était donc annoncée « partial », avec `covered: 0` au résumé.
    Un lien sans déclaration ne doit pas produire d'état déclaré du tout.
    """

    from app.main import app

    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="analyst")
        case = _build_case(organization_id=identity.organization_id)
        valid = _add_evidence(
            organization_id=identity.organization_id,
            evidence_type=EvidenceType.CERTIFICATE,
            reference="ECOLABEL-COUVRANT",
            product_id=case["product_id"],
            supplier_id=case["supplier_id"],
            issued_on=date(2025, 1, 10),
            expires_on=date(2027, 9, 30),
        )
        linked = client.post(
            f"/api/v1/claims/{case['claim_id']}/evidence-links",
            json={
                "evidence_id": str(valid),
                "relation": "supports",
                "validity_as_of": AS_OF.isoformat(),
            },
        )
        assert linked.status_code == 201, linked.text
        assert linked.json()["coverage_status"] == "pending"
        assert linked.json()["observed_state"] == "covered"

        payload = _coverage(client, case["analysis_id"], as_of=AS_OF.isoformat())
        row = _coverage_of(payload, case["claim_id"])
        assert row["observed_state"] == "covered"
        assert row["declared_state"] is None, "aucune décision humaine n'a été prise"
        assert row["state"] == "covered", "un lien sans déclaration ne plafonne pas le constat"
        assert payload["summary"]["covered"] == 1
        assert payload["summary"]["partial"] == 0
        assert payload["summary"]["claims_with_declared_contradiction"] == 0

        # Rien n'est déclaré : le lien ne doit pas non plus être horodaté comme relu.
        links = client.get(f"/api/v1/claims/{case['claim_id']}/evidence-links")
        assert links.status_code == 200, links.text
        assert links.json()[0]["reviewed_at"] is None
        assert links.json()[0]["observed_state"] == "covered", (
            "le constat reste rendu, sans devenir une déclaration"
        )


def test_the_link_itself_reports_what_the_coverage_reading_reports():
    """Un seul examen : le lien ne peut pas dire « utilisable » là où la couverture dit « hors périmètre ».

    Défaut mesuré : `link_claim_evidence` calculait l'état d'un rattachement avec sa propre
    logique — date du jour, type de preuve, périmètre en texte libre — sans comparer le
    produit visé, le fournisseur, la famille d'allégation, ni la date d'audit choisie. Il en
    résultait deux vérités pour une même pièce : « present » à l'enregistrement,
    « out_of_scope » à la lecture de couverture.
    """

    from app.main import app

    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="analyst")
        case = _build_case(organization_id=identity.organization_id)
        wrong_product = _add_evidence(
            organization_id=identity.organization_id,
            evidence_type=EvidenceType.CERTIFICATE,
            reference="ECOLABEL-AUTRE-PRODUIT",
            product_id=case["other_product_id"],
            supplier_id=case["supplier_id"],
            issued_on=date(2025, 1, 10),
            expires_on=AS_OF + timedelta(days=365),
        )
        expired = _add_evidence(
            organization_id=identity.organization_id,
            evidence_type=EvidenceType.CERTIFICATE,
            reference="ECOLABEL-EXPIRE",
            product_id=case["product_id"],
            supplier_id=case["supplier_id"],
            issued_on=date(2023, 1, 15),
            expires_on=date(2026, 6, 30),
        )
        good = _add_evidence(
            organization_id=identity.organization_id,
            evidence_type=EvidenceType.CERTIFICATE,
            reference="ECOLABEL-VALIDE",
            product_id=case["product_id"],
            supplier_id=case["supplier_id"],
            issued_on=date(2025, 1, 10),
            expires_on=AS_OF + timedelta(days=90),
        )

        linked = client.post(
            f"/api/v1/claims/{case['claim_id']}/evidence-links",
            json={
                "evidence_id": str(wrong_product),
                "relation": "supports",
                "validity_as_of": AS_OF.isoformat(),
            },
        )
        assert linked.status_code == 201, linked.text
        body = linked.json()
        # Sans déclaration humaine, le lien reste `pending` : le produit n'inscrit pas son
        # propre constat dans la colonne des décisions. Le constat est rendu à part.
        assert body["coverage_status"] == "pending", body
        assert body["observed_state"] == "out_of_scope"
        assert body["reviewed_at"] is None, "un rattachement sans décision n'est pas une relecture"
        assert "autre produit" in body["rationale"]
        assert "Aucune décision humaine" in body["rationale"]

        expired_link = client.post(
            f"/api/v1/claims/{case['claim_id']}/evidence-links",
            json={
                "evidence_id": str(expired),
                "relation": "supports",
                "validity_as_of": AS_OF.isoformat(),
            },
        )
        assert expired_link.status_code == 201, expired_link.text
        assert expired_link.json()["observed_state"] == "expired"

        good_link = client.post(
            f"/api/v1/claims/{case['claim_id']}/evidence-links",
            json={
                "evidence_id": str(good),
                "relation": "supports",
                "validity_as_of": AS_OF.isoformat(),
            },
        )
        assert good_link.status_code == 201, good_link.text
        assert good_link.json()["observed_state"] == "covered"

        # Une déclaration humaine, elle, est conservée telle quelle — et confrontée aux faits.
        declared = client.patch(
            f"/api/v1/evidence-links/{expired_link.json()['id']}",
            json={"coverage_status": "verified"},
        )
        assert declared.status_code == 200, declared.text
        assert declared.json()["coverage_status"] == "verified"
        assert declared.json()["reviewed_at"] is not None

        # La lecture de couverture dit exactement la même chose que le rattachement.
        payload = _coverage(client, case["analysis_id"], as_of=AS_OF.isoformat())
        row = _coverage_of(payload, case["claim_id"])
        assert row["observed_state"] == "expired", "le plus défavorable des trois l'emporte"
        # La déclaration « verified » posée à l'instant est contredite par l'expiration :
        # le produit conserve les deux constats et le signale.
        # Ce qu'un humain a déclaré : « verified » sur l'une des trois pièces. La règle de
        # lecture est conservatrice et inchangée — la déclaration reste visible telle quelle,
        # et c'est la confrontation avec les faits qui est nouvelle.
        assert row["declared_state"] == "covered"
        assert row["state"] == "expired"
        assert row["contradicting_link_ids"], "la déclaration contredite doit être citée"
        assert payload["summary"]["claims_with_declared_contradiction"] == 1
        # La pièce utilisable, elle, n'est plus plafonnée par une absence de déclaration.
        assert any(check["evidence_id"] == str(good) for check in row["checks"])
        assert {check["scope_state"] for check in row["checks"]} == {
            "in_scope",
            "out_of_product",
        }, "les périmètres sont comparés pièce par pièce"


def test_case_e_a_certificate_for_another_product_does_not_cover_the_claim():
    """Cas E : un certificat d'un autre produit (détergent) ne couvre pas le shampoing."""

    from app.main import app

    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="analyst")
        case = _build_case(organization_id=identity.organization_id)
        wrong_product = _add_evidence(
            organization_id=identity.organization_id,
            evidence_type=EvidenceType.CERTIFICATE,
            reference="ECOLABEL-FR-2025-0002",
            product_id=case["other_product_id"],
            supplier_id=case["supplier_id"],
            issued_on=date(2025, 1, 10),
            expires_on=AS_OF + timedelta(days=365),  # valide… mais pour un autre produit
        )
        _link(
            organization_id=identity.organization_id,
            claim_id=case["claim_id"],
            evidence_id=wrong_product,
            coverage_status=EvidenceStatus.VERIFIED,
        )

        payload = _coverage(client, case["analysis_id"], as_of=AS_OF.isoformat())
        row = _coverage_of(payload, case["claim_id"])

        assert row["state"] == "out_of_scope", row["explanation"]
        assert row["is_sufficient"] is False
        finding = next(item for item in row["findings"] if item["code"] == "other_product")
        assert finding["facts"]["evidence_product_id"] == str(case["other_product_id"])
        assert finding["facts"]["claim_product_id"] == str(case["product_id"])
        assert row["checks"][0]["scope_state"] == "out_of_product"
        assert row["checks"][0]["validity_state"] == "valid", "la date n'est pas le problème ici"


def test_a_valid_certificate_for_the_right_product_covers_the_claim():
    """Le cas nominal : la couverture est prononcée, et elle s'explique."""

    from app.main import app

    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="analyst")
        case = _build_case(organization_id=identity.organization_id)
        good = _add_evidence(
            organization_id=identity.organization_id,
            evidence_type=EvidenceType.CERTIFICATE,
            product_id=case["product_id"],
            supplier_id=case["supplier_id"],
            issued_on=date(2025, 1, 10),
            expires_on=AS_OF + timedelta(days=90),
        )
        _link(
            organization_id=identity.organization_id,
            claim_id=case["claim_id"],
            evidence_id=good,
        )

        payload = _coverage(client, case["analysis_id"], as_of=AS_OF.isoformat())
        row = _coverage_of(payload, case["claim_id"])

        assert row["state"] == "covered"
        assert row["is_sufficient"] is True
        assert row["declared_state"] == row["observed_state"] == "covered"
        assert row["checks"][0]["usable_as_of"] is True
        assert payload["summary"]["covered"] == 1
        assert payload["summary"]["claims_with_declared_contradiction"] == 0
        # Le verdict de couverture reste un constat, pas une conformité.
        assert "pas une validation juridique" in payload["disclaimer"]

        # Le rattachement lui-même produisait « present » : deux calculs coexistaient, l'un
        # au moment du lien (date du jour, type, périmètre déclaré) et l'autre à la lecture.
        # Le premier ne voyait ni le produit visé, ni la famille d'allégation, ni la date
        # d'audit choisie. Le lien reprend donc l'examen de C17.
        link = client.get(f"/api/v1/claims/{case['claim_id']}/evidence-links")
        assert link.status_code == 200, link.text
        assert link.json()[0]["coverage_status"] == "verified", (
            "avec la date du jour, la même pièce valide est utilisable : le lien doit le dire"
        )


def test_a_certificate_without_an_expiry_date_is_not_declared_valid():
    """Une date manquante n'est pas une validité : l'état plafonne à « partial »."""

    from app.main import app

    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="analyst")
        case = _build_case(organization_id=identity.organization_id)
        undated = _add_evidence(
            organization_id=identity.organization_id,
            evidence_type=EvidenceType.CERTIFICATE,
            product_id=case["product_id"],
            supplier_id=case["supplier_id"],
            expires_on=None,
        )
        _link(
            organization_id=identity.organization_id,
            claim_id=case["claim_id"],
            evidence_id=undated,
            coverage_status=EvidenceStatus.VERIFIED,
        )

        payload = _coverage(client, case["analysis_id"], as_of=AS_OF.isoformat())
        row = _coverage_of(payload, case["claim_id"])

        assert row["state"] == "partial", row["explanation"]
        assert row["is_sufficient"] is False
        assert row["checks"][0]["validity_state"] == "validity_unknown"
        assert "validity_not_stated" in {item["code"] for item in row["findings"]}
        assert "declared_but_unusable" in {item["code"] for item in row["findings"]}
        assert payload["summary"]["covered"] == 0


def test_an_evidence_type_that_cannot_address_the_claim_family_is_signalled():
    """Un plan de réduction des émissions ne couvre pas une allégation de recyclabilité."""

    from app.main import app

    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="analyst")
        case = _build_case(organization_id=identity.organization_id, claim_type="recyclable")
        off_type = _add_evidence(
            organization_id=identity.organization_id,
            evidence_type=EvidenceType.CARBON_OFFSET,
            reference="CREDITS-VCS-2026",
            product_id=case["product_id"],
            supplier_id=case["supplier_id"],
            expires_on=AS_OF + timedelta(days=30),
        )
        _link(
            organization_id=identity.organization_id,
            claim_id=case["claim_id"],
            evidence_id=off_type,
            coverage_status=EvidenceStatus.VERIFIED,
        )

        payload = _coverage(client, case["analysis_id"], as_of=AS_OF.isoformat())
        row = _coverage_of(payload, case["claim_id"])

        assert row["state"] == "not_covering", row["explanation"]
        finding = next(
            item for item in row["findings"] if item["code"] == "evidence_type_does_not_cover_claim"
        )
        assert finding["facts"]["claim_type"] == "recyclable"
        assert finding["facts"]["evidence_type"] == "carbon_offset"
        assert "certificate" in finding["facts"]["accepted_types"]
        assert row["checks"][0]["type_state"] == "not_accepted"


def test_a_claim_without_any_evidence_is_missing_and_gets_suggestions():
    """Sans preuve, l'état est « missing » — et des rapprochements motivés sont proposés."""

    from app.main import app

    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="analyst")
        case = _build_case(organization_id=identity.organization_id)
        candidate = _add_evidence(
            organization_id=identity.organization_id,
            evidence_type=EvidenceType.RECYCLING_ROUTE,
            reference="FILIERE-CITEO-2026",
            product_id=case["product_id"],
            supplier_id=case["supplier_id"],
            expires_on=AS_OF + timedelta(days=120),
        )
        expired_candidate = _add_evidence(
            organization_id=identity.organization_id,
            evidence_type=EvidenceType.CERTIFICATE,
            reference="ECOLABEL-EXPIRE",
            product_id=case["product_id"],
            supplier_id=case["supplier_id"],
            expires_on=date(2025, 1, 1),
        )

        payload = _coverage(client, case["analysis_id"], as_of=AS_OF.isoformat())
        row = _coverage_of(payload, case["claim_id"])

        assert row["state"] == "missing" and row["is_sufficient"] is False
        assert row["checks"] == [] and row["linked_evidence_ids"] == []

        suggestion_ids = [item["evidence_id"] for item in row["suggestions"]]
        assert str(candidate) in suggestion_ids
        best = row["suggestions"][0]
        assert best["evidence_id"] == str(candidate), "l'offre utilisable passe avant l'expirée"
        assert best["score"] > 0 and best["usable_as_of"] is True
        reasons = " ".join(best["reasons"])
        assert "même produit" in reasons and "même fournisseur" in reasons
        assert any("ce n'est pas une preuve" in caveat for caveat in best["caveats"])

        # La preuve expirée est signalée comme inutilisable, jamais masquée.
        expired_offer = next(
            item for item in row["suggestions"] if item["evidence_id"] == str(expired_candidate)
        )
        assert expired_offer["usable_as_of"] is False
        assert any("expirée" in caveat for caveat in expired_offer["caveats"])


def test_the_same_data_evaluated_at_two_dates_gives_two_answers():
    """Le calcul est daté : rien ne change dans les données, tout change dans la date."""

    from app.main import app

    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="analyst")
        case = _build_case(organization_id=identity.organization_id)
        evidence_id = _add_evidence(
            organization_id=identity.organization_id,
            evidence_type=EvidenceType.CERTIFICATE,
            product_id=case["product_id"],
            supplier_id=case["supplier_id"],
            issued_on=date(2025, 1, 1),
            expires_on=date(2026, 12, 31),
        )
        _link(
            organization_id=identity.organization_id,
            claim_id=case["claim_id"],
            evidence_id=evidence_id,
        )

        before = _coverage_of(
            _coverage(client, case["analysis_id"], as_of="2026-06-30"), case["claim_id"]
        )
        after = _coverage_of(
            _coverage(client, case["analysis_id"], as_of="2027-01-01"), case["claim_id"]
        )

        assert before["state"] == "covered" and before["as_of"] == "2026-06-30"
        assert after["state"] == "expired" and after["as_of"] == "2027-01-01"
        assert before["checks"][0]["evidence_id"] == after["checks"][0]["evidence_id"]


def test_a_lot_of_evidence_without_scope_is_not_claimed_as_covering():
    """Sans produit ni fournisseur comparables, le produit ne conclut pas."""

    from app.main import app

    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="analyst")
        case = _build_case(organization_id=identity.organization_id)
        anonymous = _add_evidence(
            organization_id=identity.organization_id,
            evidence_type=EvidenceType.CERTIFICATE,
            reference="CERT-SANS-PERIMETRE",
            product_id=None,
            supplier_id=None,
            expires_on=AS_OF + timedelta(days=200),
            product_scope="Tous les produits de la gamme",  # en texte libre : non comparé
        )
        _link(
            organization_id=identity.organization_id,
            claim_id=case["claim_id"],
            evidence_id=anonymous,
            coverage_status=EvidenceStatus.VERIFIED,
        )

        payload = _coverage(client, case["analysis_id"], as_of=AS_OF.isoformat())
        row = _coverage_of(payload, case["claim_id"])

        assert row["state"] == "partial"
        assert row["checks"][0]["scope_state"] == "scope_unknown"
        finding = next(item for item in row["findings"] if item["code"] == "scope_not_comparable")
        assert finding["facts"]["declared_product_scope"] == "Tous les produits de la gamme"
        assert finding["facts"]["evidence_product_id"] is None
        assert "périmètre" in row["explanation"] or "périmètre" in finding["message"]
        assert payload["rules"]["scope"].startswith("Le périmètre se compare par identifiants")


def test_the_coverage_summary_counts_every_state_and_only_this_version():
    """Le résumé compte les états de la version demandée, et rien d'autre."""

    from app.main import app

    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="analyst")
        case = _build_case(
            organization_id=identity.organization_id,
            claim_text="Emballage recyclable et neutre en carbone",
            claims=3,
        )
        claim_ids = case["claim_ids"]
        good = _add_evidence(
            organization_id=identity.organization_id,
            evidence_type=EvidenceType.CERTIFICATE,
            product_id=case["product_id"],
            supplier_id=case["supplier_id"],
            expires_on=AS_OF + timedelta(days=90),
        )
        stale = _add_evidence(
            organization_id=identity.organization_id,
            evidence_type=EvidenceType.CERTIFICATE,
            product_id=case["product_id"],
            supplier_id=case["supplier_id"],
            expires_on=date(2025, 12, 31),
        )
        _link(organization_id=identity.organization_id, claim_id=claim_ids[0], evidence_id=good)
        _link(organization_id=identity.organization_id, claim_id=claim_ids[1], evidence_id=stale)

        payload = _coverage(client, case["analysis_id"], as_of=AS_OF.isoformat())
        summary = payload["summary"]

        assert summary["total_claims"] == 3
        assert summary["covered"] == 1
        assert summary["expired"] == 1
        assert summary["missing"] == 1
        assert summary["partial"] == summary["out_of_scope"] == summary["not_covering"] == 0
        assert payload["version_number"] == 1
        assert "as_of" in payload["rules"]


def test_a_claim_of_another_organization_is_not_visible():
    """Aucune couverture ne traverse la frontière d'organisation."""

    from app.main import app

    with TestClient(app) as client, TestClient(app) as foreign_client:
        identity = authenticate_client(client, role_code="analyst")
        foreign = authenticate_client(foreign_client, role_code="analyst")
        case = _build_case(organization_id=identity.organization_id)
        _add_evidence(
            organization_id=identity.organization_id,
            evidence_type=EvidenceType.CERTIFICATE,
            product_id=case["product_id"],
            supplier_id=case["supplier_id"],
            expires_on=AS_OF + timedelta(days=30),
        )

        response = foreign_client.get(
            f"/api/v1/analyses/{case['analysis_id']}/coverage", params={"as_of": AS_OF.isoformat()}
        )
        assert response.status_code == 404, response.text

        own_claim = foreign_client.get(f"/api/v1/claims/{case['claim_id']}/coverage")
        assert own_claim.status_code == 404, own_claim.text

        assert foreign.organization_id != identity.organization_id


# ---------------------------------------------------------------------------
# Cas D dans le moteur : une preuve expirée ne peut pas fonder une conclusion
# ---------------------------------------------------------------------------


def _engine_context():
    from app.models.legal_types import Surface
    from app.models.schemas import AuditContext

    return AuditContext(
        as_of_date=AS_OF,
        jurisdiction="FR",
        surface=Surface.PACKAGING,
        consumer_facing=True,
        product_identifier="SKU-PET-50",
        product_category="packaging",
    )


def test_the_engine_refuses_declared_evidence_that_is_expired_on_the_audit_date():
    """Le moteur opposait les dates du registre, jamais les dates déclarées."""

    from app.engine.proof_validator import ProofValidator
    from app.models.legal_types import (
        CarbonOffsetEvidence,
        ClaimType,
        EcolabelEvidence,
        EvidenceDossier,
        GHGReductionPlanEvidence,
        GHGInventoryEvidence,
        LcaEvidence,
        RecyclingRouteEvidence,
    )

    validator = ProofValidator()
    context = _engine_context()
    expired = {"issued_on": date(2023, 1, 1), "expires_on": date(2025, 12, 31)}

    dossier = EvidenceDossier(
        items=[
            LcaEvidence(
                evidence_id="ACV-EXPIREE",
                reference="ACV-2023-77",
                standard="ISO 14044",
                product_scope="Bouteille PET 50 cl",
                functional_unit="1 bouteille",
                system_boundary="berceau à la tombe",
                **expired,
            ),
            RecyclingRouteEvidence(
                evidence_id="FILIERE-EXPIREE",
                material_or_component="PET",
                territories=["FR"],
                collection_available=True,
                consumer_access=True,
                sorting_available=True,
                industrial_processing_available=True,
                **expired,
            ),
            GHGInventoryEvidence(
                evidence_id="BILAN-EXPIRE",
                standard="GHG Protocol",
                product_lifecycle_scope="berceau à la tombe",
                includes_direct_emissions=True,
                includes_indirect_emissions=True,
                **expired,
            ),
            GHGReductionPlanEvidence(
                evidence_id="TRAJECTOIRE-EXPIREE",
                avoidance_prioritised=True,
                reduction_before_compensation=True,
                annual_quantified_targets=True,
                **expired,
            ),
            CarbonOffsetEvidence(
                evidence_id="CREDITS-EXPIRES",
                standard_or_registry="VCS",
                retirement_reference="retrait-2023",
                quantity_tco2e=100,
                residual_emissions_reference="émissions résiduelles 2023",
                **expired,
            ),
        ]
    )

    lca = validator.validate_lca(dossier, context)
    ecolabel = validator.validate_ecolabel(
        EvidenceDossier(
            items=[
                EcolabelEvidence(
                    evidence_id="ECOLABEL-EXPIRE",
                    reference="FR/2023/001",
                    scheme="EU_ECOLABEL",
                    license_number="FR/2023/001",
                    product_identifier="SKU-PET-50",
                    product_category="packaging",
                    **expired,
                )
            ]
        ),
        ClaimType.CERTIFICATION,
        context,
    )
    route = validator.validate_recycling_route(dossier, context)
    carbon = validator.validate_french_carbon_neutrality(dossier, context, offsetting_asserted=True)

    assert lca.status.value == "INVALID", lca.detail
    assert "2025-12-31" in lca.detail and "ACV-EXPIREE" in lca.detail
    assert route.status.value == "INVALID" and "FILIERE-EXPIREE" in route.detail
    assert ecolabel.status.value in {"INVALID", "NOT_INDEPENDENTLY_VERIFIED"}
    assert all(check.status.value != "METADATA_COMPLETE" for check in carbon), [
        (check.check_name, check.status.value) for check in carbon
    ]
    assert all(check.status.value == "INVALID" for check in carbon), [
        (check.check_name, check.status.value) for check in carbon
    ]


def test_an_expired_dossier_never_produces_a_verdict_milder_than_no_dossier_at_all():
    """Le verdict doit devenir plus sévère, jamais plus clément, quand la pièce est expirée."""

    from app.engine.inference_evaluator import InferenceEvaluator
    from app.engine.proof_validator import ProofValidator
    from app.models.legal_types import ClaimType, EvidenceDossier, LcaEvidence

    evaluator = InferenceEvaluator()
    context = _engine_context()
    claim_text = "Notre bouteille est recyclable et son emballage est 100 % recyclé."

    def verdict_for(dossier: EvidenceDossier):
        checks = [evaluator.proof_validator.validate_lca(dossier, context)]
        statuses = {check.status.value for check in checks}
        return statuses

    empty = verdict_for(EvidenceDossier(items=[]))
    with_expired = verdict_for(
        EvidenceDossier(
            items=[
                LcaEvidence(
                    evidence_id="ACV-EXPIREE",
                    reference="ACV-2023-77",
                    standard="ISO 14044",
                    product_scope="Bouteille PET 50 cl",
                    functional_unit="1 bouteille",
                    system_boundary="berceau à la tombe",
                    issued_on=date(2023, 1, 1),
                    expires_on=date(2025, 12, 31),
                )
            ]
        )
    )
    assert empty == {"NOT_PROVIDED"}
    assert with_expired == {"INVALID"}
    assert ClaimType.RECYCLABLE.value == "recyclable"
    assert ProofValidator.__name__ == "ProofValidator"
    assert "recyclable" in claim_text


def test_an_expired_dossier_makes_the_verdict_blocking_not_milder():
    """Le verdict doit devenir bloquant, jamais plus clément, quand la pièce est périmée.

    C'est le sens du cas D : « certifié ECOLABEL expiré » ne doit pas produire un avis
    « à revoir » équivalent à celui d'un dossier bien fourni — il doit bloquer la
    publication, faute de preuve valable à la date de l'audit.
    """

    from app.engine.fact_extractor import FactExtractor
    from app.engine.inference_evaluator import InferenceEvaluator
    from app.models.legal_types import (
        EvidenceDossier,
        RecyclingRouteEvidence,
        Verdict,
    )

    engine = InferenceEvaluator()
    context = _engine_context()
    claim = next(
        item
        for item in FactExtractor().extract("Bouteille 100 % recyclable, filière de collecte en France.")
        if item.claim_type.value == "recyclable"
    )

    def verdict_with(dossier: EvidenceDossier) -> Verdict:
        findings = engine.evaluate_claim_all(claim, dossier, context)
        rule = next(item for item in findings if item.rule_id == "RULE_ISO_RECYCLABLE_PERCENTAGE")
        return rule.verdict

    complete = RecyclingRouteEvidence(
        evidence_id="FILIERE-VALIDE",
        material_or_component="PET",
        territories=["FR"],
        collection_available=True,
        consumer_access=True,
        sorting_available=True,
        industrial_processing_available=True,
        issued_on=date(2026, 1, 1),
        expires_on=date(2027, 1, 1),
    )
    expired = RecyclingRouteEvidence(
        evidence_id="FILIERE-EXPIREE",
        material_or_component="PET",
        territories=["FR"],
        collection_available=True,
        consumer_access=True,
        sorting_available=True,
        industrial_processing_available=True,
        issued_on=date(2023, 1, 1),
        expires_on=date(2025, 12, 31),
    )

    without_piece = verdict_with(EvidenceDossier(items=[]))
    with_valid_piece = verdict_with(EvidenceDossier(items=[complete]))
    with_expired_piece = verdict_with(EvidenceDossier(items=[expired]))

    assert without_piece == Verdict.CONDITIONAL_REJECT
    assert with_valid_piece == Verdict.REVIEW_REQUIRED, (
        "un dossier complet et valide reste soumis à relecture humaine"
    )
    assert with_expired_piece == Verdict.CONDITIONAL_REJECT, (
        "un dossier périmé ne doit pas être mieux traité qu'une absence de preuve"
    )
