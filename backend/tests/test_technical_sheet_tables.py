"""C23 — les tableaux d'une fiche technique sortent structurés et citables.

Ce que ces tests mesurent, et ce qu'ils interdisent :

* une ligne de tableau n'est **pas** rangée dans le même segment qu'un paragraphe :
  `SegmentType.TABLE` existait dans l'énumération depuis C4 et n'était jamais produit ;
* le texte n'est **pas** perdu en chemin : l'en-tête d'une fiche et la phrase qui suit un
  tableau restent des segments citables (le premier essai de ce chantier les faisait
  disparaître, c'est mesuré ici) ;
* une allusion détectée dans une cellule est citée **cellule par cellule** — la citation
  dit la ligne lue (critère / valeur / unité) au lieu du bloc entier ;
* une prose qui contient une barre verticale isolée **n'est pas** un tableau.
"""

from __future__ import annotations

import hashlib
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import settings
from app.core.database import SessionLocal
from app.documents.extraction import claim_next_document_extraction_job, process_claimed_document_extraction
from app.engine.document_extractor import DocumentTextExtractor
from app.engine.table_reader import find_entries, parse_page_tables
from app.identity.service import set_db_request_context
from app.models.domain import DocumentSegment, SegmentType
from tests.auth_support import authenticate_client
from tests.test_secure_documents import (
    _create_document,
    _install_document_fakes,
    _request_upload,
)

#: Une fiche technique comme il en arrive : un titre, un tableau à trois colonnes, et une
#: phrase de commentaire qui reprend le chiffre du tableau.
TECHNICAL_SHEET = """FICHE TECHNIQUE — SHAMPOOING DOUX
Fournisseur : exemple.test
Critère | Valeur | Unité
Taux de matière recyclée | 62 | %
Contenu biosourcé | 12 | %
Masse d'emballage | 24 | g
Recyclabilité de l'emballage | 100 | %
Ce shampooing contient 62 % de matière recyclée.
Son emballage est entièrement recyclable et pèse 24 g.
""".encode()


def test_a_table_row_is_not_part_of_a_paragraph_segment():
    """Le défaut : `SegmentType.TABLE` annoncé dans le modèle, jamais produit."""

    result = DocumentTextExtractor().extract_result("fiche.txt", "text/plain", TECHNICAL_SHEET)

    types = [segment.segment_type for segment in result.segments]
    assert "table" in types, (
        "un tableau de fiche technique doit sortir en segment `table`, "
        f"types obtenus : {types}"
    )
    table_segments = [segment for segment in result.segments if segment.segment_type == "table"]
    assert len(table_segments) == 1
    table_segment = table_segments[0]
    assert "Taux de matière recyclée" in table_segment.text
    assert "Fournisseur : exemple.test" not in table_segment.text, (
        "la ligne de fournisseur n'est pas une ligne de tableau : elle ne doit pas être "
        "absorbée dans le segment tableau"
    )

    # Et une ligne de tableau ne doit pas être **aussi** dans un paragraphe : sinon la
    # même ligne aurait deux citations concurrentes, une par segment, et la citation
    # cellule ne servirait à rien.
    for segment in result.segments:
        if segment.segment_type == "table":
            continue
        assert "Taux de matière recyclée |" not in segment.text, (
            f"ligne de tableau encore présente dans un segment {segment.segment_type}"
        )


def test_the_table_is_read_as_criterion_value_unit_with_cell_offsets():
    """Critère, valeur, unité : une lecture par colonne, pas une ligne de texte."""

    result = DocumentTextExtractor().extract_result("fiche.txt", "text/plain", TECHNICAL_SHEET)

    table_segment = next(segment for segment in result.segments if segment.segment_type == "table")
    payload = table_segment.table
    assert isinstance(payload, dict)
    assert payload["kind"] == "structured_table"
    assert payload["columns"] == ["criterion", "value", "unit"]
    assert payload["has_header"] is True

    entries = payload["entries"]
    assert [entry["criterion"] for entry in entries] == [
        "Taux de matière recyclée",
        "Contenu biosourcé",
        "Masse d'emballage",
        "Recyclabilité de l'emballage",
    ]
    assert [entry["value"] for entry in entries] == ["62", "12", "24", "100"]
    assert [entry["unit"] for entry in entries] == ["%", "%", "g", "%"]

    # La valeur « 62 » est citée à sa position exacte dans le document canonique :
    # c'est ce qui permet de désigner une cellule plutôt qu'un bloc.
    first = entries[0]
    start, end = first["value_offsets"]
    assert result.text[start:end] == "62"
    criterion_start, criterion_end = first["criterion_offsets"]
    assert result.text[criterion_start:criterion_end] == "Taux de matière recyclée"
    unit_start, unit_end = first["unit_offsets"]
    assert result.text[unit_start:unit_end] == "%"

    # Les offsets de **cellule** de la charge utile publiée désignent le bon texte : c'est
    # cette lecture que l'analyse utilise pour citer la cellule plutôt que le bloc.
    data_row = next(row for row in payload["rows"] if not row["is_header"])
    value_cell = data_row["cells"][1]
    assert value_cell["role"] == "value"
    assert result.text[value_cell["start_offset"]:value_cell["end_offset"]] == "62"
    criterion_cell = data_row["cells"][0]
    assert criterion_cell["role"] == "criterion"
    assert (
        result.text[criterion_cell["start_offset"]:criterion_cell["end_offset"]]
        == "Taux de matière recyclée"
    )


def test_no_line_of_the_sheet_disappears_when_a_table_is_carved_out():
    """Le piège trouvé en branchant le chantier : jeter le paragraphe qui porte le tableau.

    La première version retirait le paragraphe entier dès qu'il contenait une ligne
    tabulaire : le titre de la fiche et la phrase qui commente le chiffre disparaissaient
    des segments, donc du manifeste d'analyse, donc des citations.
    """

    result = DocumentTextExtractor().extract_result("fiche.txt", "text/plain", TECHNICAL_SHEET)

    covered = "\n".join(segment.text for segment in result.segments)
    for needle in (
        "FICHE TECHNIQUE — SHAMPOOING DOUX",
        "Fournisseur : exemple.test",
        "Ce shampooing contient 62 % de matière recyclée.",
        "Son emballage est entièrement recyclable et pèse 24 g.",
    ):
        assert needle in covered, f"texte perdu par le découpage des tableaux : {needle!r}"

    # Et l'ordre du document est conservé : les offsets sont croissants.
    offsets = [segment.start_offset for segment in result.segments]
    assert offsets == sorted(offsets)


def test_prose_with_a_single_pipe_is_not_a_table():
    """Deux cellules séparées par une barre isolée : une phrase, pas un tableau."""

    prose = "Nous vendons des cosmétiques bio | naturels depuis 1998.".encode()
    result = DocumentTextExtractor().extract_result("prose.txt", "text/plain", prose)
    assert [segment.segment_type for segment in result.segments] == ["paragraph"]
    assert result.segments[0].text == prose.decode()


def test_a_two_column_block_is_not_read_as_a_table():
    """Un couple critère/valeur sans unité n'est pas retenu : il n'y a pas de tableau."""

    page = "Matière recyclée | 62 %\nContenu biosourcé | 12 %\n"
    assert parse_page_tables(page, page_number=1) == ()


def test_multi_space_columns_are_read_like_pipe_columns():
    """Les fiches alignées par espaces doivent sortir en tableau, pas en texte brut."""

    page = (
        "Critère              Valeur   Unité\n"
        "Matière recyclée     62       %\n"
        "Contenu biosourcé    12       %\n"
    )
    tables = parse_page_tables(page, page_number=3)
    assert len(tables) == 1
    assert tables[0].columns == ("criterion", "value", "unit")
    assert tables[0].entries[0].criterion == "Matière recyclée"
    assert tables[0].entries[0].value == "62"
    assert tables[0].entries[0].unit == "%"


def test_a_dash_is_not_published_as_a_unit():
    """« - » n'est pas une unité : le produit publie « pas d'unité », pas un tiret."""

    page = "Critère | Valeur | Unité\nProduit | Shampooing | -\nContenu biosourcé | 12 | %\n"
    entry = parse_page_tables(page, page_number=1)[0].entries[0]
    assert entry.criterion == "Produit"
    assert entry.value == "Shampooing"
    assert entry.unit is None


def test_find_entries_matches_the_criterion_without_concluding():
    """La lecture rend une valeur déclarée ; elle ne conclut rien du chiffre."""

    page = "Critère | Valeur | Unité\nTaux de matière recyclée | 62 | %\nMasse d'emballage | 24 | g\n"
    tables = parse_page_tables(page, page_number=1)
    found = find_entries(tables, criterion_keywords=("recyclée",))
    assert len(found) == 1
    assert found[0].numeric_value is not None
    assert str(found[0].numeric_value) == "62"
    assert found[0].unit == "%"


def test_the_worker_stores_the_structured_table_next_to_the_plain_text():
    """Bout en bout : le worker écrit le tableau structuré et le type `table` en base."""

    app, storage, _scanner, old_storage, old_scanner = _install_document_fakes()
    try:
        with TestClient(app) as client:
            identity = authenticate_client(client, role_code="analyst")
            document = _create_document(client, title="Fiche technique tabulaire")
            instruction = _request_upload(
                client,
                str(document["id"]),
                filename="fiche.txt",
                payload=TECHNICAL_SHEET,
            )
            storage.upload_latest(payload=TECHNICAL_SHEET)
            completed = client.post(f"/api/v1/document-uploads/{instruction['upload']['id']}/complete")
            assert completed.status_code == 201, completed.text
            version_id = UUID(completed.json()["version"]["id"])

            with SessionLocal() as db:
                set_db_request_context(
                    db, user_id=identity.user_id, organization_id=identity.organization_id
                )
                claim = claim_next_document_extraction_job(
                    db,
                    settings=settings,
                    organization_id=identity.organization_id,
                    worker_id="pytest-table-worker",
                )
                assert claim is not None
                process_claimed_document_extraction(
                    db, settings=settings, storage=storage, claim=claim
                )
                db.commit()

            with SessionLocal() as db:
                set_db_request_context(
                    db, user_id=identity.user_id, organization_id=identity.organization_id
                )
                segments = list(
                    db.scalars(
                        select(DocumentSegment)
                        .where(DocumentSegment.document_version_id == version_id)
                        .order_by(DocumentSegment.sequence_number)
                    ).all()
                )

            assert segments, "l'extraction doit publier des segments"
            table_segments = [
                segment for segment in segments if segment.segment_type == SegmentType.TABLE
            ]
            assert len(table_segments) == 1, (
                f"un seul tableau attendu, obtenu {[s.segment_type for s in segments]}"
            )
            table = table_segments[0]
            assert table.bounding_box_json is not None, (
                "la lecture structurée doit être persistée : sans elle, l'API ne peut pas "
                "citer la cellule"
            )
            assert table.bounding_box_json["kind"] == "structured_table"
            assert table.bounding_box_json["columns"] == ["criterion", "value", "unit"]
            assert len(table.bounding_box_json["entries"]) == 4
            assert table.page_number == 1

            # Et le texte reste intégralement présent dans les segments publiés.
            published = "\n".join(segment.text for segment in segments)
            assert "FICHE TECHNIQUE — SHAMPOOING DOUX" in published
            assert "Ce shampooing contient 62 % de matière recyclée." in published

            # L'API publie la lecture structurée telle quelle, sur la route qui expose
            # les segments d'une version (`GET /documents/{id}/versions/{v}/segments`).
            version_response = client.get(f"/api/v1/document-versions/{version_id}/segments")
            assert version_response.status_code == 200, version_response.text
            published_segments = version_response.json()["segments"]
            published_table = next(
                segment for segment in published_segments if segment["segment_type"] == "table"
            )
            assert published_table["bounding_box"]["kind"] == "structured_table"
            assert published_table["bounding_box"]["entries"][0]["unit"] == "%"

            # L'empreinte du manifeste doit couvrir le type de segment : sans cela, un
            # tableau requalifié en paragraphe passerait inaperçu à l'analyse.
            from app.analyses.service import _segments_sha256

            first_digest = _segments_sha256(segments)
            table_segments[0].segment_type = SegmentType.PARAGRAPH
            assert _segments_sha256(segments) != first_digest, (
                "requalifier un tableau en paragraphe doit changer l'empreinte du manifeste "
                "d'entrée, sinon l'analyse ne verrait pas la requalification"
            )
    finally:
        app.state.document_storage = old_storage
        app.state.document_scanner = old_scanner


def _analysis_engine():
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    from app.core.database import Base

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


def test_a_claim_detected_in_a_table_row_is_cited_cell_by_cell():
    """Bout en bout d'analyse : la citation désigne la cellule, pas le bloc de texte.

    C'est la raison d'être du chantier. Avant C23, la citation d'une allégation trouvée
    dans une fiche technique portait le segment entier, souvent quatre lignes de tableau
    mêlées à d'autres critères — le relecteur ne pouvait pas savoir *ce que le document
    déclarait*.
    """

    from uuid import uuid4

    from sqlalchemy.orm import Session

    from app.analyses.service import (
        claim_next_analysis_detection_job,
        create_analysis,
        list_version_claims,
        process_claimed_analysis_detection,
    )
    from app.core.database import Base  # noqa: F401  (le schéma est créé par _analysis_engine)
    from app.identity.service import set_db_request_context
    from app.models.domain import (
        Claim,
        Document,
        DocumentVersion,
        ExtractionStatus,
        Organization,
        SegmentType,
        User,
        UserStatus,
    )

    # On part du **vrai** extracteur, pas d'un segment fabriqué à la main : la chaîne
    # complète (extraction → tableau structuré → détection → citation) est exercée.
    result = DocumentTextExtractor().extract_result("fiche.txt", "text/plain", TECHNICAL_SHEET)
    table_segment = next(segment for segment in result.segments if segment.segment_type == "table")
    text_segment = next(
        segment for segment in result.segments if "contient 62 %" in segment.text
    )

    engine = _analysis_engine()
    suffix = uuid4().hex[:8]
    with Session(engine, expire_on_commit=False) as db:
        organization = Organization(name=f"Organisation {suffix}", slug=f"c23-{suffix}")
        user = User(email=f"analyste.{suffix}@example.test", status=UserStatus.ACTIVE)
        db.add_all((organization, user))
        db.flush()
        document = Document(
            organization_id=organization.id, document_key=f"DOC-{suffix}", title="Fiche technique"
        )
        db.add(document)
        db.flush()
        version = DocumentVersion(
            organization_id=organization.id,
            document_id=document.id,
            version_number=1,
            source_filename="fiche.txt",
            content_type="text/plain",
            storage_key=f"organizations/{organization.id}/fiche.txt",
            sha256="c" * 64,
            size_bytes=len(TECHNICAL_SHEET),
            source_language="fr",
            extraction_status=ExtractionStatus.COMPLETED,
            extraction_engine_version="vericlaim-document-extractor-v2",
            extracted_text_sha256=hashlib.sha256(result.text.encode()).hexdigest(),
        )
        db.add(version)
        db.flush()
        for index, segment in enumerate(result.segments):
            db.add(
                DocumentSegment(
                    organization_id=organization.id,
                    document_version_id=version.id,
                    sequence_number=index,
                    page_number=segment.page_number,
                    segment_type=SegmentType(segment.segment_type),
                    text=segment.text,
                    start_offset=segment.start_offset,
                    end_offset=segment.end_offset,
                    bounding_box_json=segment.table,
                    source_sha256=version.sha256,
                )
            )
        db.commit()
        organization_id, user_id, version_id = organization.id, user.id, version.id

    with Session(engine, expire_on_commit=False) as db:
        set_db_request_context(db, user_id=user_id, organization_id=organization_id)
        created = create_analysis(
            db,
            settings=settings,
            organization_id=organization_id,
            actor_user_id=user_id,
            document_version_ids=[version_id],
            idempotency_key="c23-cell-citation",
        )
        db.commit()
        analysis_version_id = created.version.id

    with Session(engine, expire_on_commit=False) as db:
        set_db_request_context(db, user_id=user_id, organization_id=organization_id)
        claim = claim_next_analysis_detection_job(
            db, settings=settings, organization_id=organization_id, worker_id="pytest-c23-worker"
        )
        assert claim is not None
        db.commit()
    with Session(engine, expire_on_commit=False) as db:
        set_db_request_context(db, user_id=user_id, organization_id=organization_id)
        process_claimed_analysis_detection(db, settings=settings, claim=claim)
        db.commit()

    with Session(engine) as db:
        claims = list_version_claims(
            db, organization_id=organization_id, analysis_version_id=analysis_version_id
        )
        # La sérialisation d'API est exercée : un décalage entre la charge utile stockée
        # et le schéma de réponse ferait échouer la validation (500) au lieu de passer
        # inaperçu ici.
        from sqlalchemy import select as _select

        from app.api.v1.analyses import _present_claim
        from app.models.domain import DocumentSegment as _DocumentSegment

        segments_by_id = {
            segment.id: segment
            for segment in db.scalars(
                _select(_DocumentSegment).where(
                    _DocumentSegment.document_version_id == version_id
                )
            ).all()
        }
        presented = [
            _present_claim(claim, segment=segments_by_id.get(claim.document_segment_id))
            for claim in claims
        ]

    # On range les allégations par la provenance réellement publiée : le type du segment
    # est dans la provenance, on ne devine rien.
    paragraph_claims = [
        claim for claim in claims if claim.attributes_json["segment_type"] == "paragraph"
    ]
    table_claims = [
        claim
        for claim in claims
        if claim.attributes_json["segment_type"] == "table"
        and claim.attributes_json["table_citation"] is not None
    ]

    # L'allégation trouvée dans la phrase qui commente le tableau reste citée par
    # paragraphe : la citation cellule ne s'applique pas partout.
    assert paragraph_claims, "la phrase de commentaire doit rester une allégation citée"
    assert all(
        claim.attributes_json["table_citation"] is None for claim in paragraph_claims
    )
    assert "62 %" in paragraph_claims[0].claim_text

    # L'allégation trouvée dans une ligne de tableau porte la cellule et la ligne lue.
    assert table_claims, "aucune allégation de tableau : la citation cellule n'est pas exercée"
    citation = table_claims[0].attributes_json["table_citation"]
    assert citation["cell"]["role"] == "criterion", (
        "le déclencheur « matière recyclée » est dans la colonne critère ; "
        f"rôle obtenu : {citation['cell']['role']}"
    )
    assert citation["cell"]["text"] in {"Taux de matière recyclée", "Recyclabilité de l'emballage"}
    start, end = citation["cell"]["start_offset"], citation["cell"]["end_offset"]
    assert result.text[start:end] == citation["cell"]["text"], (
        "l'offset publié de la cellule doit désigner exactement le texte de la cellule"
    )
    entry = citation["row_entry"]
    assert entry is not None
    assert entry["criterion"] == citation["cell"]["text"]
    assert entry["value"] in {"62", "100"}
    assert entry["unit"] == "%"
    assert citation["columns"] == ["criterion", "value", "unit"]
    assert citation["page_number"] == 1

    # Et le bloc de texte entier n'est plus la citation : la cellule est plus courte que
    # le segment, sans quoi le changement de citation serait cosmétique.
    assert len(citation["cell"]["text"]) < len(table_segment.text)

    # Ce que le client reçoit réellement, après validation du schéma de réponse.
    published = next(item for item in presented if item.citation.table_citation is not None)
    assert published.citation.segment_type == SegmentType.TABLE
    assert published.citation.table_citation.cell.text == citation["cell"]["text"]
    assert published.citation.table_citation.cell.role == "criterion"
    assert published.citation.table_citation.row_entry is not None
    assert published.citation.table_citation.row_entry.value == citation["row_entry"]["value"]
    assert published.citation.table_citation.row_entry.unit == "%"
    assert published.citation.table_citation.columns == ["criterion", "value", "unit"]
    # Et les allégations hors tableau restent sans citation de cellule.
    assert any(item.citation.table_citation is None for item in presented)
