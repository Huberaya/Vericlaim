"""C11 acceptance: nothing may be published that the product cannot prove.

The audit's objection to the commercial surface was not cosmetic: the product was
sold as an "AI" while containing no AI, and the retention panel displayed four
invented values (C8). This module makes that class of defect mechanically
impossible to reintroduce.

Two mechanisms, both executable:

1. **Claim registry.** `frontend/src/lib/public-claims.json` is the only source of
   the sentences shown on the public site. Every claim carries an `evidence_key`,
   and this file contains one verification function per key. A claim whose key is
   unknown to this module fails `test_every_published_claim_is_verifiable`, so a
   new sentence cannot reach a customer before its behaviour exists.
2. **Forbidden marketing patterns.** Some formulations were measured false or
   unsupported (`conformité RGPD`, a hosting region, an availability figure). A
   scanner over the public pages refuses them by pattern.

**What a green run does not mean.** These tests check that the published claims
correspond to real, measured behaviour of this codebase. They do not validate the
commercial promises as a whole (pricing, SLA, contractual commitments), and they
say nothing about legal conformity — see `docs/legal/` and chantier C12.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.analyses.service import (
    claim_next_analysis_detection_job,
    process_claimed_analysis_detection,
    replay_persisted_version_verdicts,
)
from app.core.config import settings
from app.core.database import SessionLocal, create_tables
from app.engine.fact_extractor import FactExtractor
from app.engine.inference_evaluator import InferenceEvaluator
from app.engine.lexicon import LEXICON_VERSION
from app.engine.rule_book import RULEBOOK_VERSION, RULES
from app.identity.service import set_db_request_context
from app.main import app
from app.models.domain import (
    AnalysisVerdict,
    Document,
    DocumentSegment,
    DocumentVersion,
    ExtractionStatus,
    SegmentType,
)
from app.models.legal_types import ClaimType, EvidenceDossier, LegalForce
from app.models.schemas import AuditContext
from tests.auth_support import authenticate_client
from tests.report_support import issue_report, report_storage
from tests.test_pdf_reporting import _persisted_analysis

REPO = Path(__file__).resolve().parents[2]
CLAIMS_FILE = REPO / "frontend" / "src" / "lib" / "public-claims.json"
DEMO_FILE = REPO / "frontend" / "src" / "lib" / "demo-cases.json"
CORPUS_FILE = Path(__file__).parent / "corpus" / "claim_detection_corpus.json"
PUBLIC_PAGES = [
    REPO / "frontend" / "src" / "app" / "page.tsx",
    REPO / "frontend" / "src" / "app" / "demo" / "page.tsx",
    REPO / "frontend" / "src" / "app" / "legal" / "page.tsx",
    REPO / "frontend" / "src" / "app" / "aide" / "page.tsx",
    REPO / "frontend" / "src" / "components" / "SupportLauncher.tsx",
    REPO / "frontend" / "src" / "components" / "site" / "PublicShell.tsx",
]

#: La page légale est publique : les mêmes formulations interdites s'y appliquent, et
#: ses faits sont dans un fichier pour que ce scanner couvre aussi les données qu'elle
#: affiche (un mensonge écrit dans le JSON s'afficherait sur la page).
LEGAL_FACTS_FILE = REPO / "frontend" / "src" / "lib" / "legal-facts.json"

#: Sentences the audit proved false or unsupported. Each entry is refused on the
#: public surface, whatever the intent behind it.
FORBIDDEN_MARKETING_PATTERNS: dict[str, str] = {
    r"intelligence\s+artificielle": "le moteur n'utilise aucun modèle : « IA » ne peut pas être un argument",
    r"propuls[ée]\s+par\s+l['’]?(?:IA|intelligence)": "argument d'IA non fondé",
    r"conformit[ée]\s+RGPD|conforme\s+au\s+RGPD|RGPD[- ]compliant": "aucune conformité RGPD n'a été établie (C12, validation externe requise)",
    r"h[ée]berg[ée]\s+en\s+(?:UE|Europe)|r[ée]gion\s+EU\s*\(": "la région d'hébergement n'est pas constatée (C8) : elle est déclarée par le client",
    r"ISO\s*27001|SOC\s*2": "aucune certification de sécurité n'existe",
    r"99[,.]9\s*%|disponibilit[ée]\s+garantie|SLA\s+de\s+\d": "aucun SLA n'est défini ni mesuré",
    r"certifi[ée]\s+par\s+VeriClaim|VeriClaim\s+certifie": "le produit qualifie un risque, il ne certifie rien",
}


@pytest.fixture(scope="module")
def claims() -> dict:
    return json.loads(CLAIMS_FILE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def client():
    create_tables()
    with TestClient(app) as test_client:
        yield test_client


def _evidence_no_llm_dependency() -> None:
    requirements = (REPO / "backend" / "requirements.txt").read_text(encoding="utf-8").lower()
    for dependency in ("openai", "anthropic", "langchain", "llama", "transformers", "cohere", "mistralai"):
        assert dependency not in requirements, f"{dependency} est une dépendance du produit"
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (REPO / "backend" / "app").rglob("*.py")
    )
    for marker in ("import openai", "import anthropic", "from langchain", "OpenAI(", "ChatCompletion"):
        assert marker not in sources, f"appel à un modèle détecté : {marker}"


def _evidence_no_outbound_ai_transfer() -> None:
    """No module of the analysis path may talk to an external service."""
    engine_dir = REPO / "backend" / "app" / "engine"
    for path in sorted(engine_dir.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for marker in ("httpx.", "requests.", "urllib.request", "aiohttp", "socket."):
            assert marker not in source, f"{path.name} appelle le réseau ({marker})"
    findings = (REPO / "backend" / "app" / "analyses" / "service.py").read_text(encoding="utf-8")
    for marker in ("httpx.", "requests.", "urllib.request", "aiohttp"):
        assert marker not in findings, f"le service d'analyse appelle le réseau ({marker})"


def _evidence_replay_is_stable(client: TestClient) -> None:
    """Re-deriving the verdicts from the persisted rows must reproduce them."""
    identity = authenticate_client(client, role_code="analyst")
    analysis_id, _document_version_id = _persisted_analysis(client, identity)
    # `_persisted_analysis` returns a *document* version; the replay needs the
    # immutable *analysis* version, which is what the API publishes.
    detail = client.get(f"/api/v1/analyses/{analysis_id}")
    assert detail.status_code == 200, detail.text
    version_id = UUID((detail.json().get("latest_version") or {})["id"])
    with SessionLocal() as db:
        set_db_request_context(
            db, user_id=identity.user_id, organization_id=identity.organization_id
        )
        replayed = replay_persisted_version_verdicts(
            db,
            settings=settings,
            organization_id=identity.organization_id,
            analysis_version_id=version_id,
        )
        stored = list(
            db.scalars(
                select(AnalysisVerdict)
                .where(
                    AnalysisVerdict.organization_id == identity.organization_id,
                    AnalysisVerdict.analysis_version_id == version_id,
                )
                .order_by(AnalysisVerdict.sequence_number.asc())
            ).all()
        )
    assert stored, "l'analyse persistée ne contient aucun verdict"
    assert len(replayed) == len(stored), (len(replayed), len(stored))
    for row, record in zip(stored, replayed, strict=True):
        assert record["sequence_number"] == row.sequence_number
        assert record["rule_id"] == row.rule_id
        assert record["claim_type"] == str(getattr(row.claim_type, "value", row.claim_type))
        assert record["verdict"] == str(getattr(row.verdict, "value", row.verdict))
        assert record["severity"] == str(getattr(row.severity, "value", row.severity))
        assert record["legal_force"] == str(getattr(row.legal_force, "value", row.legal_force))
        assert record["is_legal_violation"] == row.is_legal_violation
        assert record["law_reference"] == row.law_reference
        assert record["reasoning_steps_json"] == (row.reasoning_steps_json or [])


def _evidence_report_verifiable(client: TestClient) -> None:
    identity = authenticate_client(client, role_code="analyst")
    analysis_id, _ = _persisted_analysis(client, identity)
    # C22 : la génération passe par la file et le worker dédié ; c'est le fichier
    # réellement téléchargé qui est vérifié, comme le ferait un client.
    with report_storage() as storage:
        issued = issue_report(client, analysis_id=analysis_id, storage=storage)
    assert issued.status_code == 200, issued.text
    reference = issued.headers["x-verification-reference"]
    verified = client.get(f"/api/v1/reports/verify/{reference}")
    assert verified.status_code == 200, verified.text
    body = verified.json()
    assert body["known"] is True
    assert body["signature_valid"] is True
    assert body["analysis_unchanged"] is True


def _evidence_corpus_rates_match_published_facts(claims: dict) -> None:
    corpus = json.loads(CORPUS_FILE.read_text(encoding="utf-8"))
    extractor = FactExtractor()
    detected = 0
    for case in corpus["positive_cases"]:
        found = {claim.claim_type.value for claim in extractor.extract(case["text"])}
        affirmative = {
            claim.claim_type.value for claim in extractor.extract(case["text"]) if claim.affirmative
        }
        if set(case["expected"]) <= found and set(case.get("expect_affirmative", [])) <= affirmative:
            detected += 1
    false_positives = 0
    for case in corpus["negative_cases"]:
        if [claim for claim in extractor.extract(case["text"]) if claim.affirmative]:
            false_positives += 1

    facts = claims["published_facts"]
    positives = len(corpus["positive_cases"])
    negatives = len(corpus["negative_cases"])
    assert facts["corpus_positive_cases"] == positives, "le nombre de cas publié a dérivé du corpus"
    assert facts["corpus_negative_cases"] == negatives
    assert facts["corpus_detection_rate"] == pytest.approx(detected / positives), (
        f"taux publié {facts['corpus_detection_rate']} vs mesuré {detected / positives}"
    )
    assert facts["corpus_false_positive_rate"] == pytest.approx(false_positives / negatives), (
        f"taux de faux positifs publié vs mesuré {false_positives / negatives}"
    )
    assert facts["lexicon_version"] == LEXICON_VERSION
    assert facts["rulebook_version"] == RULEBOOK_VERSION
    assert facts["rule_count"] == len(RULES)


def _evidence_audit_chain_verifiable(client: TestClient) -> None:
    identity = authenticate_client(client, role_code="analyst")
    _persisted_analysis(client, identity)
    response = client.get("/api/v1/audit/verify")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["is_valid"] is True, body
    assert body["total_events"] > 0, body
    assert body["head_event_hash"], "la chaîne doit publier sa tête d'empreinte"
    assert body["tampered_event_id"] is None
    assert body["organization_id"] == str(identity.organization_id)


def _evidence_confidence_published(client: TestClient) -> None:
    identity = authenticate_client(client, role_code="analyst")
    analysis_id, version_number = _analyse_text(
        client, identity, "Certifié ECOLABEL et neutre en carbone."
    )
    snapshot = client.get(f"/api/v1/analyses/{analysis_id}/versions/{version_number}")
    assert snapshot.status_code == 200, snapshot.text
    claims = snapshot.json()["claims"]
    assert claims
    for claim in claims:
        assert claim["confidence_score"] is not None
        assert claim["confidence_level"] in {"high", "medium", "low", "human_review_required"}
        assert claim["confidence_factors"]
        assert "probabilité" in claim["confidence_basis"]
    assert any(claim["review_reasons"] for claim in claims), "aucun motif de revue publié"


def _evidence_disclaimer_published(client: TestClient) -> None:
    identity = authenticate_client(client, role_code="analyst")
    analysis_id, version_number = _analyse_text(client, identity, "Produit biodégradable.")
    detail = client.get(f"/api/v1/analyses/{analysis_id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert "avis juridique" in body["disclaimer"]
    result = (body.get("latest_version") or {}).get("result") or {}
    joined = " ".join(result.get("limitations") or [])
    assert LEXICON_VERSION in joined, "la révision du lexique n'est pas publiée"
    assert "ne signifient pas que le support est conforme" in joined or "ne vaut pas conformité" in joined


def _evidence_unknown_transposition_downgrades() -> None:
    """A certificate claim with no recorded proof must not become a prohibition."""
    evaluator = InferenceEvaluator()
    context = AuditContext(
        as_of_date=date.today(),
        jurisdiction="FR",
        surface="packaging",
        consumer_facing=True,
        product_category="cosmetics",
        product_identifier="SKU-DEMO",
    )
    claims, assessments = evaluator.evaluate_text(
        "Certifié ECOLABEL.",
        EvidenceDossier(items=[]),
        context,
    )
    assert [claim.claim_type for claim in claims] == [ClaimType.CERTIFICATION], claims
    assert assessments, "la règle de certification n'a produit aucun verdict"
    assert all(not item.is_legal_violation for item in assessments), assessments
    assert any(
        str(getattr(item.verdict, "value", item.verdict)) == "REVIEW_REQUIRED"
        for item in assessments
    )
    # The rule that fired is EU-date-gated, and the engine says so in the verdict:
    # that is what allows "review required" instead of a firm prohibition.
    assert all(item.legal_force is LegalForce.EU_DIRECTIVE_DATE_GATED for item in assessments), [
        item.legal_force for item in assessments
    ]


def _evidence_demo_cases_match_the_engine(client: TestClient) -> None:
    stored = json.loads(DEMO_FILE.read_text(encoding="utf-8"))
    identity = authenticate_client(client, role_code="analyst")
    assert stored["engine"]["lexicon_version"] == LEXICON_VERSION
    assert stored["engine"]["rulebook_version"] == RULEBOOK_VERSION
    for case in stored["cases"]:
        measured = _analyse_text(client, identity, case["text"])
        snapshot = client.get(f"/api/v1/analyses/{measured[0]}/versions/{measured[1]}").json()
        assert len(snapshot["claims"]) == len(case["claims"]), case["id"]
        assert [
            (claim["claim_type"], claim["status"]) for claim in snapshot["claims"]
        ] == [(claim["claim_type"], claim["status"]) for claim in case["claims"]], case["id"]
        published_verdicts = [
            (item["rule_id"], item["verdict"])
            for item in snapshot["version"]["result"]["verdicts"]
        ]
        stored_verdicts = [(item["rule_id"], item["verdict"]) for item in case["verdicts"]]
        assert stored_verdicts == published_verdicts, case["id"]
        assert snapshot["version"]["overall_compliance"] == case["overall_compliance"], case["id"]
        scores = [claim["confidence_score"] for claim in snapshot["claims"]]
        assert scores == [claim["confidence_score"] for claim in case["claims"]], case["id"]


EVIDENCE_CHECKS = {
    "no_llm_dependency": _evidence_no_llm_dependency,
    "no_outbound_ai_transfer": _evidence_no_outbound_ai_transfer,
    "replay_is_stable": _evidence_replay_is_stable,
    "report_verifiable": _evidence_report_verifiable,
    "corpus_rates_match_published_facts": _evidence_corpus_rates_match_published_facts,
    "audit_chain_verifiable": _evidence_audit_chain_verifiable,
    "confidence_published": _evidence_confidence_published,
    "disclaimer_published": _evidence_disclaimer_published,
    "unknown_transposition_downgrades": _evidence_unknown_transposition_downgrades,
}


# --------------------------------------------------------------------------- #
# The registry itself
# --------------------------------------------------------------------------- #


def test_every_published_claim_is_verifiable(claims: dict):
    """A claim whose behaviour is not verified here cannot be published."""
    keys = {claim["evidence_key"] for claim in claims["claims"]}
    unknown = sorted(keys - set(EVIDENCE_CHECKS))
    assert not unknown, (
        f"affirmation(s) publiée(s) sans preuve exécutable : {unknown}. "
        "Écrire le contrôle correspondant dans ce fichier avant de publier la phrase."
    )
    unused = sorted(set(EVIDENCE_CHECKS) - keys)
    assert not unused, f"contrôle(s) sans affirmation publiée (morte) : {unused}"


#: Marqueurs de négation. Ils autorisent la citation du terme interdit **dans la même
#: phrase** : « aucun transfert vers un service d'intelligence artificielle » est un
#: démenti, pas un argument. Le contrôle ne raisonne qu'au niveau de la phrase — c'est un
#: filet destiné à la copie commerciale, pas une preuve de non-usage, et il ne prétend pas
#: détecter une phrase qui nierait puis affirmerait dans le même souffle.
DENIAL_MARKERS = ("aucun", "aucune", "pas de", "pas d'", "sans ", "ni ", "jamais", "non ", "ne contient")

SENTENCE_SPLIT = re.compile(r"[.;!?\n]")


def _sentences(text: str) -> list[str]:
    return [sentence.lower() for sentence in SENTENCE_SPLIT.split(text)]


def forbidden_marketing_offences(text: str, filename: str) -> list[str]:
    """Renvoie les formulations interdites présentes dans `text` (hors démentis).

    Extraite de son test pour être elle-même vérifiable : le test
    `test_the_marketing_guard_refuses_the_claim_and_allows_the_denial` s'assure qu'un
    démenti passe et qu'une affirmation reprenant le même mot est refusée. Sans cela,
    élargir la tolérance reviendrait à désarmer le garde-fou.
    """

    offences: list[str] = []
    sentences = _sentences(text)
    for pattern, reason in FORBIDDEN_MARKETING_PATTERNS.items():
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            before = text[: match.start()]
            index = len(SENTENCE_SPLIT.findall(before))
            sentence = sentences[index] if index < len(sentences) else ""
            if any(marker in sentence for marker in DENIAL_MARKERS):
                continue
            offences.append(f"{filename}: « {match.group(0)} » — {reason}")
    return offences


def test_the_marketing_guard_refuses_the_claim_and_allows_the_denial():
    """La tolérance à la négation ne doit pas ouvrir la porte à l'affirmation."""

    assert forbidden_marketing_offences(
        "Le moteur utilise l'intelligence artificielle pour lire vos documents.", "test.tsx"
    ), "une affirmation doit rester refusée"
    assert forbidden_marketing_offences(
        "Aucun document n'est transmis à un service d'intelligence artificielle.", "test.tsx"
    ) == [], "un démenti factuel doit pouvoir nommer ce qu'il nie"
    assert forbidden_marketing_offences(
        "Notre moteur est propulsé par l'IA.", "test.tsx"
    ), "l'argument d'IA reste refusé"
    assert forbidden_marketing_offences(
        "Le service est conforme au RGPD et certifié ISO 27001.", "test.tsx"
    ), "les affirmations de conformité et de certification restent refusées"


def test_public_pages_do_not_contain_forbidden_marketing(claims: dict):
    """Patterns the audit proved false: no page may ship them."""
    offences: list[str] = []
    for path in [*PUBLIC_PAGES, LEGAL_FACTS_FILE]:
        if not path.exists():
            continue
        offences.extend(
            forbidden_marketing_offences(path.read_text(encoding="utf-8"), path.name)
        )
    assert not offences, "\n".join(offences)


def test_the_landing_page_renders_claims_from_the_registry():
    """The page must not hardcode a claim: it reads the registry."""
    source = (REPO / "frontend" / "src" / "app" / "page.tsx").read_text(encoding="utf-8")
    assert 'from "@/lib/public-claims"' in source, (
        "la page d'accueil doit lire le registre d'affirmations, pas les réécrire"
    )
    assert "publicClaims.map" in source, "les affirmations doivent être rendues depuis le registre"
    for claim in json.loads(CLAIMS_FILE.read_text(encoding="utf-8"))["claims"]:
        assert claim["text"] not in source, (
            f"l'affirmation {claim['id']} est écrite en dur dans la page : elle échapperait au registre"
        )


def test_the_published_facts_are_recomputed_not_asserted(claims: dict):
    """Numbers on the page come from the registry, which is checked above."""
    facts = claims["published_facts"]
    assert 0.0 <= facts["corpus_detection_rate"] <= 1.0
    assert 0.0 <= facts["corpus_false_positive_rate"] <= 1.0
    assert facts["corpus_note"], "les chiffres publiés doivent porter leur limite"
    assert "non-régression" in facts["corpus_note"], (
        "la note doit rappeler qu'il s'agit d'une mesure de non-régression"
    )


# --------------------------------------------------------------------------- #
# The checks themselves
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("key", sorted(EVIDENCE_CHECKS))
def test_published_claim_holds(key: str, client, claims: dict):
    check = EVIDENCE_CHECKS[key]
    if check.__code__.co_argcount == 1:
        parameter = check.__code__.co_varnames[0]
        if parameter == "claims":
            check(claims)
        else:
            check(client)
    else:
        check()


def test_demo_cases_are_real_engine_output(client):
    _evidence_demo_cases_match_the_engine(client)


# --------------------------------------------------------------------------- #
# Local helpers
# --------------------------------------------------------------------------- #


def _analyse_text(client: TestClient, identity, text: str) -> tuple[str, int]:
    """Run the real pipeline over one sentence and return (analysis_id, version)."""
    from uuid import uuid4

    suffix = uuid4().hex[:10]
    with SessionLocal() as db:
        document = Document(
            organization_id=identity.organization_id,
            document_key=f"CLAIMS-{suffix}",
            title="Vérification des affirmations publiques",
        )
        db.add(document)
        db.flush()
        version = DocumentVersion(
            organization_id=identity.organization_id,
            document_id=document.id,
            version_number=1,
            source_filename=f"{suffix}.txt",
            content_type="text/plain",
            storage_key=f"organizations/{identity.organization_id}/{suffix}.txt",
            sha256="3" * 64,
            size_bytes=len(text),
            source_language="fr",
            extraction_status=ExtractionStatus.COMPLETED,
            extraction_engine_version="vericlaim-document-extractor-v1",
            extracted_text_sha256="2" * 64,
        )
        db.add(version)
        db.flush()
        db.add(
            DocumentSegment(
                organization_id=identity.organization_id,
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
        db.commit()
        version_id = version.id

    created = client.post(
        "/api/v1/analyses",
        json={"document_version_ids": [str(version_id)]},
        headers={"Idempotency-Key": f"claims-{uuid4().hex[:12]}"},
    )
    assert created.status_code == 202, created.text
    analysis_id = created.json()["analysis"]["id"]
    with SessionLocal() as db:
        set_db_request_context(
            db, user_id=identity.user_id, organization_id=identity.organization_id
        )
        claim = claim_next_analysis_detection_job(
            db, settings=settings, organization_id=identity.organization_id,
            worker_id="public-claims-worker",
        )
        assert claim is not None
        process_claimed_analysis_detection(db, settings=settings, claim=claim)
        db.commit()
    return analysis_id, 1


# --------------------------------------------------------------------------- #
# C12 : la page légale ne peut pas décrire un autre produit que celui qui tourne
# --------------------------------------------------------------------------- #


def test_the_legal_page_reads_its_facts_from_the_registry():
    """La page légale ne recopie pas la carte des droits à la main."""

    source = (REPO / "frontend" / "src" / "app" / "legal" / "page.tsx").read_text(encoding="utf-8")
    assert 'from "@/lib/legal-facts"' in source, (
        "la page légale doit lire le registre de faits, pas les réécrire"
    )
    assert "rights.handling.map" in source, "le tableau des droits doit être rendu depuis le registre"


def test_the_published_rights_table_is_the_product_rights_table():
    """Le tableau publié est celui du produit, comparé champ par champ.

    C'est le contrôle qui compte : une page qui annonce « effacement : outillé » engage
    à servir la route. Si un droit change de nature dans `app/privacy/rights.py`, ce test
    échoue jusqu'à ce que le fichier publié soit régénéré.
    """

    from app.privacy import rights as rights_module

    published = json.loads(LEGAL_FACTS_FILE.read_text(encoding="utf-8"))
    assert published["rights"]["handling"] == rights_module.published_handling_map()
    assert published["rights"]["response_window_months"] == rights_module.RESPONSE_WINDOW_MONTHS
    assert published["rights"]["extension_months"] == rights_module.EXTENSION_MONTHS


def test_the_legal_page_invents_no_company_detail():
    """L'identité de l'éditeur, l'hébergeur et le contact droits sont inconnus : dits tels quels.

    Un SIREN plausible, une région d'hébergement ou une adresse de DPO inventés seraient
    des mentions légales fausses — c'est-à-dire un défaut, pas une finition.
    """

    published = json.loads(LEGAL_FACTS_FILE.read_text(encoding="utf-8"))
    for section in ("publisher", "hosting"):
        assert published[section]["fields"], section
        for field in published[section]["fields"]:
            assert field["value"] is None, (
                f"{section}/{field['label']} porte une valeur : elle doit être vérifiée, "
                "pas inventée, avant publication"
            )
    assert published["rights"]["contact_email"] is None
    assert published["review"]["external_review"] == "not_performed"
    assert published["review"]["reviewer"] is None


def test_the_unverified_parts_of_the_product_are_published_as_such():
    """Les limites écrites dans le produit doivent rester dans le dossier légal.

    Trois manques ont été mesurés par les chantiers précédents : aucune sauvegarde du
    stockage objet (C24), aucune purge planifiée (C20), artefact de rapport hors
    rétention (C22). Les effacer de la documentation les transformerait en surprises.
    """

    published = json.loads(LEGAL_FACTS_FILE.read_text(encoding="utf-8"))
    gaps = " ".join(published["gaps"]["items"]).lower()
    assert "sauvegarde" in gaps, "l'absence de sauvegarde du stockage objet doit être publiée"
    assert "planifi" in gaps, "l'absence de purge planifiée doit être publiée"

    docs_dir = REPO / "docs" / "legal"
    for name in (
        "mentions-legales.md",
        "politique-de-confidentialite.md",
        "cgu-cgv.md",
        "politique-cookies.md",
        "dpa.md",
        "registre-des-traitements.md",
        "sous-traitants-et-transferts.md",
        "procedure-droits-des-personnes.md",
    ):
        document = docs_dir / name
        assert document.exists(), f"document légal manquant : {name}"
        assert "À COMPLÉTER" in document.read_text(encoding="utf-8") or name in (
            "politique-cookies.md",
            "procedure-droits-des-personnes.md",
        ), f"{name} ne marque aucun champ à compléter : à vérifier, il en reste toujours"


# --------------------------------------------------------------------------- #
# C15 : le centre d'aide décrit le produit, et ne promet pas de disponibilité
# --------------------------------------------------------------------------- #


def test_the_help_page_cannot_invent_its_content():
    """La page d'aide lit l'API, elle ne recopie pas une aide figée.

    C'est le même mécanisme que pour les affirmations commerciales (C11) et la page
    légale (C12) : un contenu d'aide recopié dans du JSX ne peut pas être vérifié, et
    décrit tôt ou tard une version du produit qui n'existe plus.
    """

    source = (REPO / "frontend" / "src" / "app" / "aide" / "page.tsx").read_text(encoding="utf-8")
    assert 'from "@/lib/api"' in source, "la page d'aide doit interroger l'API"
    assert "/api/v1/public/support" in source
    assert "unreachable" in source, (
        "la page doit assumer l'indisponibilité de l'API plutôt que d'afficher un contenu "
        "de secours non vérifié"
    )


def test_the_in_app_support_button_sends_the_screen_context():
    """Le bouton d'aide joint l'écran et le dernier code d'erreur (C15)."""

    source = (REPO / "frontend" / "src" / "components" / "SupportLauncher.tsx").read_text(
        encoding="utf-8"
    )
    assert "last_error_code" in source, "le code d'erreur doit partir avec la demande"
    assert "screen" in source and "screen," in source
    assert "/api/v1/support/requests" in source
    assert "objectif indicatif" in source, (
        "le délai affiché doit être présenté comme un objectif, pas comme un engagement"
    )
    dashboard = (REPO / "frontend" / "src" / "components" / "AuditDashboard.tsx").read_text(
        encoding="utf-8"
    )
    assert "SupportLauncher" in dashboard, "le bouton doit être monté dans l'application"
    assert "ApiError" in dashboard, "le code d'erreur doit être conservé, pas aplati en texte"
