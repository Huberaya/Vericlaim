from __future__ import annotations

import csv
import io
import json
import zipfile

from typing import Optional

from app.models.schemas import EvaluationResponse
from app.reports.pdf_generator import RegulatoryPdfReportGenerator


def build_evidence_csv(report: EvaluationResponse) -> str:
    """Génère la matrice probatoire au format CSV opposable."""
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow([
        "ID_ALLEGATION",
        "TEXTE_ALLEGATION",
        "ID_REGLE",
        "TITRE_REGLE",
        "REFERENCE_LEGALE",
        "VERDICT",
        "VIOLATION_RETENUE",
        "PREUVES_REQUISES",
        "RECOMMANDATION_REMEDIATION",
    ])

    for item in report.evaluations:
        req_proofs = ", ".join(item.required_evidence) if item.required_evidence else "Aucune preuve déclarée"
        remediation_text = (
            item.remediation.recommended_rewrite
            if hasattr(item, "remediation") and item.remediation
            else ""
        )
        verdict_val = item.verdict.value if hasattr(item.verdict, "value") else str(item.verdict)
        writer.writerow([
            item.claim_id,
            item.claim_text,
            item.rule_id,
            item.rule_title,
            getattr(item, "law_reference", item.rule_title),
            verdict_val,
            "OUI" if item.is_legal_violation else "NON",
            req_proofs,
            remediation_text,
        ])

    return output.getvalue()


def build_readme_dossier(
    organization_name: str,
    product_sku: str,
    report: EvaluationResponse,
    signature_reference: str | None = None,
    signature_value: str | None = None,
    signature_key_id: str | None = None,
) -> str:
    """Rédige la notice de pré-audit accompagnant le dossier probatoire.

    Every identifier printed here is read from the analysis that was persisted.
    The notice previously announced "Rule Engine v1.0.0" and the download date,
    neither of which described the analysis the archive actually contained.
    """
    trail = report.audit_trail
    return f"""================================================================================
VERICLAIM AI — DOSSIER DE PRÉ-AUDIT RÉGLEMENTAIRE & REGISTRE PROBATOIRE
================================================================================

Organisation : {organization_name}
Référence SKU : {product_sku}
Date d'analyse : {trail.evaluated_at_utc.strftime("%d/%m/%Y à %H:%M UTC")}
Référence temporelle du verdict : {trail.as_of_date.isoformat()}
Moteur réglementaire : {trail.engine_version}
Empreinte du Rule Book : {trail.rulebook_version}
Empreinte SHA-256 du résultat : {trail.report_sha256 or "NON DISPONIBLE"}
Scellement de la piste d'audit : {trail.record_hash or "NON DISPONIBLE"}
Référence de vérification : {signature_reference or "NON DISPONIBLE"}
Signature HMAC-SHA-256 : {signature_value or "NON DISPONIBLE"}
Identifiant de clé : {signature_key_id or "NON DISPONIBLE"}

--------------------------------------------------------------------------------
CONTENU DE L'ARCHIVE :
--------------------------------------------------------------------------------
1. `rapport_pre_audit.pdf`
   Rapport complet de pré-audit réglementaire avec scores de risque, ventilation
   des allégations détectées, fondements légaux (AGEC, EmpCo, ISO 14021) et clauses
   de remédiation proposées.

2. `manifeste_audit_scelle.json`
   Empreinte machine-readable du rapport, incluant les offsets exacts de détection,
   les condensats cryptographiques SHA-256 et la piste d'audit append-only.

3. `matrice_probatoire.csv`
   Matrice tabulaire de couverture probatoire pour exploitation dans vos ERP,
   PIM ou tableurs de contrôle interne.

--------------------------------------------------------------------------------
AVERTISSEMENT JURIDIQUE :
--------------------------------------------------------------------------------
Ce dossier est un outil technique d'aide à la décision et de gestion des risques.
Il ne constitue ni un avis juridique formel dispensé par un professionnel du droit,
ni un certificat officiel émis par une autorité de contrôle (DGCCRF, Commission Européenne).
Les informations fournies sont basées sur les règles en vigueur à la date d'analyse.
================================================================================
"""


def create_regulatory_dossier_zip(
    report: EvaluationResponse,
    organization_name: str = "Organisation Déclarée",
    product_identifier: Optional[str] = None,
    surface: str = "packaging",
    signature_reference: str | None = None,
    signature_value: str | None = None,
    signature_key_id: str | None = None,
    deprecated_reasons: Optional[list[str]] = None,
) -> bytes:
    """Génère l'archive ZIP scellée contenant l'ensemble du pack probatoire.

    The signature travels with the archive: the PDF inside carries it, and so does
    the README, so a reader who only opens the text notice still gets the handle
    needed to verify the pack.
    """
    sku = product_identifier or "SKU-EXPORT"

    # 1. Génération du PDF
    pdf_gen = RegulatoryPdfReportGenerator(
        report=report,
        organization_name=organization_name,
        product_identifier=sku,
        surface=surface,
        signature_reference=signature_reference,
        signature_value=signature_value,
        signature_key_id=signature_key_id,
        deprecated_reasons=deprecated_reasons,
    )
    pdf_bytes = pdf_gen.generate()

    # 2. Génération du Manifeste JSON
    manifest_data = report.model_dump(mode="json")
    manifest_bytes = json.dumps(manifest_data, indent=2, ensure_ascii=False).encode("utf-8")

    # 3. Génération de la matrice CSV
    csv_text = build_evidence_csv(report)
    csv_bytes = csv_text.encode("utf-8-sig")  # BOM pour Excel

    # 4. Notice README
    readme_text = build_readme_dossier(
        organization_name, sku, report, signature_reference, signature_value, signature_key_id
    )
    readme_bytes = readme_text.encode("utf-8")

    # 5. Assemblage du ZIP
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("rapport_pre_audit.pdf", pdf_bytes)
        zf.writestr("manifeste_audit_scelle.json", manifest_bytes)
        zf.writestr("matrice_probatoire.csv", csv_bytes)
        zf.writestr("README_DOSSIER_AUDIT.txt", readme_bytes)

    return zip_buffer.getvalue()
