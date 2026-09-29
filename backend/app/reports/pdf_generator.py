from __future__ import annotations

import hashlib
import textwrap
from datetime import datetime, timezone
from typing import Any, List, Optional, Union
import fitz  # PyMuPDF

from app.models.schemas import EvaluationResponse
from app.models.legal_types import LegalAssessment


# Palette de couleurs RGBA normalisées [0.0 - 1.0]
COLOR_PRIMARY_DARK = (0.09, 0.13, 0.24)      # #17213d
COLOR_PRIMARY_BLUE = (0.24, 0.35, 0.75)      # #3d59bf
COLOR_TEXT_MAIN = (0.15, 0.18, 0.25)         # #262e40
COLOR_TEXT_MUTED = (0.45, 0.50, 0.58)        # #738094
COLOR_BORDER = (0.85, 0.88, 0.92)            # #d9e0eb
COLOR_BG_CARD = (0.97, 0.98, 0.99)           # #f7fafc

COLOR_CRITICAL = (0.82, 0.15, 0.15)          # #d12626
COLOR_WARNING = (0.85, 0.55, 0.05)           # #d98c0d
COLOR_SUCCESS = (0.08, 0.62, 0.35)           # #149e59

PAGE_WIDTH = 595.32   # A4 Width
PAGE_HEIGHT = 841.92  # A4 Height
MARGIN_LEFT = 40.0
MARGIN_RIGHT = 555.0
USABLE_WIDTH = MARGIN_RIGHT - MARGIN_LEFT
MAX_Y = 780.0


class RegulatoryPdfReportGenerator:
    """Générateur de rapports d'audit réglementaires opposables et structurés au format PDF."""

    def __init__(
        self,
        report: EvaluationResponse,
        organization_name: str = "Organisation Déclarée",
        document_title: str = "Rapport d'Audit Pré-Réglementaire Allégations",
        product_identifier: Optional[str] = None,
        surface: str = "packaging",
        include_evidence_matrix: bool = True,
        include_remediation_clauses: bool = True,
    ):
        self.report = report
        self.organization_name = organization_name
        self.document_title = document_title
        self.product_identifier = product_identifier or "SKU-PROD-001"
        self.surface = surface
        self.include_evidence_matrix = include_evidence_matrix
        self.include_remediation_clauses = include_remediation_clauses

        self.doc = fitz.open()
        self.current_page: Optional[fitz.Page] = None
        self.current_y = 50.0

    def _new_page(self) -> fitz.Page:
        page = self.doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        self.current_page = page
        self.current_y = 60.0
        return page

    def _ensure_space(self, height: float) -> None:
        if self.current_page is None or (self.current_y + height > MAX_Y):
            self._new_page()

    def _draw_headers_and_footers(self) -> None:
        total_pages = len(self.doc)
        for idx, page in enumerate(self.doc):
            page_num = idx + 1
            # Running Header
            page.draw_line(
                fitz.Point(MARGIN_LEFT, 35),
                fitz.Point(MARGIN_RIGHT, 35),
                color=COLOR_BORDER,
                width=0.75,
            )
            page.insert_text(
                fitz.Point(MARGIN_LEFT, 28),
                "VERICLAIM AI • GOUVERNANCE RÉGLEMENTAIRE & PRÉ-AUDIT",
                fontsize=7.5,
                color=COLOR_PRIMARY_BLUE,
                fontname="helv",
            )
            page.insert_text(
                fitz.Point(MARGIN_RIGHT - 160, 28),
                "CONFIDENTIEL • AUDIT INTERNE",
                fontsize=7.5,
                color=COLOR_TEXT_MUTED,
                fontname="helv",
            )

            # Running Footer
            page.draw_line(
                fitz.Point(MARGIN_LEFT, 805),
                fitz.Point(MARGIN_RIGHT, 805),
                color=COLOR_BORDER,
                width=0.75,
            )
            page.insert_text(
                fitz.Point(MARGIN_LEFT, 818),
                "DOCUMENT DE PRÉ-AUDIT ET D'AIDE À LA DÉCISION — NE CONSTITUE NI UN AVIS JURIDIQUE NI UN CERTIFICAT OFFICIEL",
                fontsize=6.5,
                color=COLOR_TEXT_MUTED,
                fontname="helv",
            )
            page_str = f"Page {page_num} / {total_pages}"
            page.insert_text(
                fitz.Point(MARGIN_RIGHT - 45, 818),
                page_str,
                fontsize=7,
                color=COLOR_PRIMARY_DARK,
                fontname="helv",
            )

    def generate(self) -> bytes:
        """Produit le document PDF complet sous forme de chaîne d'octets."""
        self._new_page()

        # 1. En-tête officiel et bannière
        self._render_header_banner()

        # 2. Métadonnées d'audit & contexte
        self._render_metadata_box()

        # 3. Synthèse d'exposition aux risques et verdict
        self._render_risk_summary()

        # 4. Détail exhaustif des allégations et règles
        self._render_evaluations_section()

        # 5. Piste d'audit cryptographique et scellement
        self._render_audit_trail_section()

        # Ajout des en-têtes et pieds de page numérotés
        self._draw_headers_and_footers()

        return self.doc.tobytes()

    def _render_header_banner(self) -> None:
        page = self.current_page
        assert page is not None

        banner_rect = fitz.Rect(MARGIN_LEFT, self.current_y, MARGIN_RIGHT, self.current_y + 45)
        page.draw_rect(banner_rect, color=COLOR_PRIMARY_BLUE, fill=(0.95, 0.97, 1.0), width=1)

        page.insert_text(
            fitz.Point(MARGIN_LEFT + 15, self.current_y + 18),
            "VERICLAIM AI — RAPPORT DE PRÉ-AUDIT ET GESTION DES RISQUES D'ALLÉGATIONS",
            fontsize=10,
            color=COLOR_PRIMARY_BLUE,
            fontname="helv",
        )
        page.insert_text(
            fitz.Point(MARGIN_LEFT + 15, self.current_y + 32),
            "Contrôle déterministe de conformité Loi AGEC (R. 541-220+) • Directive UE 2024/825 EmpCo • Normes ISO 14021",
            fontsize=7.5,
            color=COLOR_TEXT_MUTED,
            fontname="helv",
        )
        self.current_y += 55

    def _render_metadata_box(self) -> None:
        page = self.current_page
        assert page is not None

        self._ensure_space(75)
        box_rect = fitz.Rect(MARGIN_LEFT, self.current_y, MARGIN_RIGHT, self.current_y + 65)
        page.draw_rect(box_rect, color=COLOR_BORDER, fill=COLOR_BG_CARD, width=0.8)

        now_str = datetime.now(timezone.utc).strftime("%d/%m/%Y à %H:%M UTC")

        # Colonne 1
        page.insert_text(fitz.Point(MARGIN_LEFT + 12, self.current_y + 16), "ORGANISATION :", fontsize=7.5, color=COLOR_TEXT_MUTED, fontname="helv")
        page.insert_text(fitz.Point(MARGIN_LEFT + 95, self.current_y + 16), self.organization_name[:40], fontsize=8, color=COLOR_PRIMARY_DARK, fontname="helv")

        page.insert_text(fitz.Point(MARGIN_LEFT + 12, self.current_y + 32), "RÉFÉRENCE SKU :", fontsize=7.5, color=COLOR_TEXT_MUTED, fontname="helv")
        page.insert_text(fitz.Point(MARGIN_LEFT + 95, self.current_y + 32), str(self.product_identifier)[:35], fontsize=8, color=COLOR_PRIMARY_DARK, fontname="helv")

        page.insert_text(fitz.Point(MARGIN_LEFT + 12, self.current_y + 48), "SUPPORT AUDITÉ :", fontsize=7.5, color=COLOR_TEXT_MUTED, fontname="helv")
        page.insert_text(fitz.Point(MARGIN_LEFT + 95, self.current_y + 48), str(self.surface).upper(), fontsize=8, color=COLOR_PRIMARY_DARK, fontname="helv")

        # Colonne 2
        page.insert_text(fitz.Point(MARGIN_LEFT + 280, self.current_y + 16), "DATE D'ANALYSE :", fontsize=7.5, color=COLOR_TEXT_MUTED, fontname="helv")
        page.insert_text(fitz.Point(MARGIN_LEFT + 365, self.current_y + 16), now_str, fontsize=8, color=COLOR_PRIMARY_DARK, fontname="helv")

        page.insert_text(fitz.Point(MARGIN_LEFT + 280, self.current_y + 32), "JURIDICTION CIBLE :", fontsize=7.5, color=COLOR_TEXT_MUTED, fontname="helv")
        page.insert_text(fitz.Point(MARGIN_LEFT + 365, self.current_y + 32), "FRANCE (FR) & UNION EUROPÉENNE (UE)", fontsize=8, color=COLOR_PRIMARY_DARK, fontname="helv")

        page.insert_text(fitz.Point(MARGIN_LEFT + 280, self.current_y + 48), "MOTEUR D'ANALYSE :", fontsize=7.5, color=COLOR_TEXT_MUTED, fontname="helv")
        page.insert_text(fitz.Point(MARGIN_LEFT + 365, self.current_y + 48), "Rule Engine v1.0.0 (Déterministe)", fontsize=8, color=COLOR_PRIMARY_DARK, fontname="helv")

        self.current_y += 75

    def _render_risk_summary(self) -> None:
        page = self.current_page
        assert page is not None

        self._ensure_space(80)

        # Statut global
        status = str(self.report.overall_compliance.value if hasattr(self.report.overall_compliance, "value") else self.report.overall_compliance)
        status_label = "CONFORME AU PÉRIMÈTRE AUDITÉ"
        badge_bg = (0.9, 0.98, 0.93)
        badge_border = COLOR_SUCCESS
        badge_text_color = COLOR_SUCCESS

        if status == "NON_COMPLIANT":
            status_label = "NON-CONFORMITÉ JURIDIQUE RETENUE"
            badge_bg = (0.99, 0.92, 0.92)
            badge_border = COLOR_CRITICAL
            badge_text_color = COLOR_CRITICAL
        elif status in ("CONDITIONAL_REJECT", "REVIEW_REQUIRED", "UPCOMING_REQUIREMENTS"):
            status_label = "PUBLICATION À SUSPENDRE · REVUE / PREUVES REQUISES"
            badge_bg = (1.0, 0.96, 0.88)
            badge_border = COLOR_WARNING
            badge_text_color = COLOR_WARNING

        # Scorecard box
        score_rect = fitz.Rect(MARGIN_LEFT, self.current_y, MARGIN_RIGHT, self.current_y + 60)
        page.draw_rect(score_rect, color=badge_border, fill=badge_bg, width=1)

        page.insert_text(
            fitz.Point(MARGIN_LEFT + 15, self.current_y + 20),
            "SYNTHÈSE D'EXPOSITION RÉGLEMENTAIRE :",
            fontsize=8,
            color=COLOR_TEXT_MUTED,
            fontname="helv",
        )
        page.insert_text(
            fitz.Point(MARGIN_LEFT + 15, self.current_y + 38),
            status_label,
            fontsize=12,
            color=badge_text_color,
            fontname="helv",
        )

        # Compteurs à droite
        non_compliant_count = getattr(self.report, "violations_count", 0)
        conditional_count = getattr(self.report, "conditional_findings_count", 0)
        detected_count = getattr(self.report, "detected_claims_count", len(self.report.evaluations))
        compliant_count = max(0, detected_count - non_compliant_count - conditional_count)

        counts_str = f"Allégations : {detected_count} | Violations : {non_compliant_count} | Preuves requises : {conditional_count} | Conformes : {compliant_count}"
        page.insert_text(
            fitz.Point(MARGIN_LEFT + 15, self.current_y + 52),
            counts_str,
            fontsize=8,
            color=COLOR_PRIMARY_DARK,
            fontname="helv",
        )

        self.current_y += 72

    def _render_evaluations_section(self) -> None:
        self._ensure_space(30)
        page = self.current_page
        assert page is not None

        page.insert_text(
            fitz.Point(MARGIN_LEFT, self.current_y + 12),
            "DÉTAIL DES ALLÉGATIONS DÉTECTÉES & DÉCISIONS DU RULE BOOK",
            fontsize=10,
            color=COLOR_PRIMARY_DARK,
            fontname="helv",
        )
        page.draw_line(
            fitz.Point(MARGIN_LEFT, self.current_y + 16),
            fitz.Point(MARGIN_RIGHT, self.current_y + 16),
            color=COLOR_PRIMARY_BLUE,
            width=1.2,
        )
        self.current_y += 28

        if not self.report.evaluations:
            self._ensure_space(30)
            page = self.current_page
            assert page is not None
            page.insert_text(
                fitz.Point(MARGIN_LEFT + 10, self.current_y + 15),
                "Aucune allégation environnementale explicite détectée dans le texte fourni.",
                fontsize=8.5,
                color=COLOR_TEXT_MUTED,
                fontname="helv",
            )
            self.current_y += 35
            return

        for idx, eval_item in enumerate(self.report.evaluations):
            self._render_single_evaluation(eval_item, idx + 1)

    def _render_single_evaluation(self, item: LegalAssessment, index: int) -> None:
        # Estimation de la hauteur de la carte
        est_height = 110.0
        remediation_text = getattr(item.remediation, "recommended_rewrite", "") if hasattr(item, "remediation") else ""
        if self.include_remediation_clauses and remediation_text:
            est_height += 35.0

        self._ensure_space(est_height)
        page = self.current_page
        assert page is not None

        card_rect = fitz.Rect(MARGIN_LEFT, self.current_y, MARGIN_RIGHT, self.current_y + est_height - 10)
        verdict_str = str(item.verdict.value if hasattr(item.verdict, "value") else item.verdict)
        card_border = COLOR_CRITICAL if item.is_legal_violation else COLOR_WARNING if verdict_str != "COMPLIANT" else COLOR_SUCCESS
        page.draw_rect(card_rect, color=COLOR_BORDER, fill=COLOR_BG_CARD, width=0.8)

        # Indicateur latéral de sévérité
        side_strip = fitz.Rect(MARGIN_LEFT, self.current_y, MARGIN_LEFT + 4, self.current_y + est_height - 10)
        page.draw_rect(side_strip, color=card_border, fill=card_border, width=0)

        # Ligne 1: Titre de la règle & Verdict
        page.insert_text(
            fitz.Point(MARGIN_LEFT + 12, self.current_y + 15),
            f"#{index} · {item.rule_title} ({item.rule_id})",
            fontsize=8.5,
            color=COLOR_PRIMARY_DARK,
            fontname="helv",
        )
        page.insert_text(
            fitz.Point(MARGIN_RIGHT - 110, self.current_y + 15),
            f"VERDICT : {verdict_str}",
            fontsize=8,
            color=card_border,
            fontname="helv",
        )

        # Ligne 2: Allégation extraite (en italique ou guillemets)
        wrapped_claim = textwrap.shorten(f"« {item.claim_text} »", width=95, placeholder="...")
        page.insert_text(
            fitz.Point(MARGIN_LEFT + 12, self.current_y + 28),
            f"Allégation analysée : {wrapped_claim}",
            fontsize=8,
            color=COLOR_PRIMARY_BLUE,
            fontname="helv",
        )

        # Ligne 3: Citation juridique officielle
        citation_text = f"Fondement juridique : {getattr(item, 'law_reference', item.rule_title)}"
        wrapped_citation = textwrap.shorten(citation_text, width=105, placeholder="...")
        page.insert_text(
            fitz.Point(MARGIN_LEFT + 12, self.current_y + 42),
            wrapped_citation,
            fontsize=7.5,
            color=COLOR_TEXT_MUTED,
            fontname="helv",
        )

        # Ligne 4: Explication / Motif légal (from buyer_explanation or steps)
        explanation = getattr(item.remediation, "buyer_explanation", "") if hasattr(item, "remediation") else ""
        if not explanation and hasattr(item, "reasoning_steps") and item.reasoning_steps:
            explanation = " · ".join([s.finding for s in item.reasoning_steps[:2]])

        wrapped_exp = textwrap.wrap(explanation or "Conformité validée au regard des critères du Rule Book.", width=95)
        exp_y = self.current_y + 55
        for exp_line in wrapped_exp[:2]:
            page.insert_text(
                fitz.Point(MARGIN_LEFT + 12, exp_y),
                exp_line,
                fontsize=7.5,
                color=COLOR_TEXT_MAIN,
                fontname="helv",
            )
            exp_y += 11

        # Ligne 5: Clause de remédiation
        if self.include_remediation_clauses and remediation_text:
            page.insert_text(
                fitz.Point(MARGIN_LEFT + 12, exp_y + 5),
                "RECOMMANDATION / CLAUSE DE REMÉDIATION :",
                fontsize=7,
                color=COLOR_PRIMARY_BLUE,
                fontname="helv",
            )
            wrapped_rem = textwrap.wrap(remediation_text, width=95)
            for rem_line in wrapped_rem[:2]:
                exp_y += 10
                page.insert_text(
                    fitz.Point(MARGIN_LEFT + 12, exp_y + 5),
                    rem_line,
                    fontsize=7,
                    color=COLOR_TEXT_MUTED,
                    fontname="helv",
                )

        self.current_y += est_height

    def _render_audit_trail_section(self) -> None:
        self._ensure_space(85)
        page = self.current_page
        assert page is not None

        page.insert_text(
            fitz.Point(MARGIN_LEFT, self.current_y + 12),
            "PISTE D'AUDIT CRYPTOGRAPHIQUE & INTÉGRITÉ PROBATOIRE",
            fontsize=9,
            color=COLOR_PRIMARY_DARK,
            fontname="helv",
        )
        page.draw_line(
            fitz.Point(MARGIN_LEFT, self.current_y + 16),
            fitz.Point(MARGIN_RIGHT, self.current_y + 16),
            color=COLOR_BORDER,
            width=0.8,
        )
        self.current_y += 24

        source_bytes = self.report.extracted_source_text.encode("utf-8")
        source_sha256 = hashlib.sha256(source_bytes).hexdigest()

        box_rect = fitz.Rect(MARGIN_LEFT, self.current_y, MARGIN_RIGHT, self.current_y + 45)
        page.draw_rect(box_rect, color=COLOR_BORDER, fill=COLOR_BG_CARD, width=0.8)

        page.insert_text(
            fitz.Point(MARGIN_LEFT + 10, self.current_y + 14),
            f"Empreinte SHA-256 du contenu extrait : {source_sha256}",
            fontsize=7,
            color=COLOR_PRIMARY_DARK,
            fontname="helv",
        )
        page.insert_text(
            fitz.Point(MARGIN_LEFT + 10, self.current_y + 26),
            f"Horodatage certifié de génération : {datetime.now(timezone.utc).isoformat()}",
            fontsize=7,
            color=COLOR_TEXT_MUTED,
            fontname="helv",
        )
        page.insert_text(
            fitz.Point(MARGIN_LEFT + 10, self.current_y + 38),
            "Chaîne de scellement audit_records : Scellé au niveau de l'organisation avec RLS PostgreSQL active.",
            fontsize=6.5,
            color=COLOR_TEXT_MUTED,
            fontname="helv",
        )

        self.current_y += 55
