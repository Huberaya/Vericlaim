from __future__ import annotations

import textwrap

# NOTE: this module deliberately imports neither ``hashlib`` nor ``datetime``.
# It must never derive a value that a reader could mistake for stored evidence;
# every fingerprint it prints comes from the audit trail it was handed.
from typing import Any, Optional

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
        signature_reference: str | None = None,
        signature_value: str | None = None,
        signature_key_id: str | None = None,
        deprecated_reasons: Optional[list[str]] = None,
    ):
        self.report = report
        # C7: a report that cannot name the analysis and the key that produced it
        # is not verifiable by a third party, so these are rendered when present
        # and explicitly reported as absent when not.
        self.signature_reference = signature_reference
        self.signature_value = signature_value
        self.signature_key_id = signature_key_id
        self.deprecated_reasons = list(deprecated_reasons or [])
        self.organization_name = organization_name
        self.document_title = document_title
        self.product_identifier = product_identifier or "SKU-PROD-001"
        self.surface = surface
        self.include_evidence_matrix = include_evidence_matrix
        self.include_remediation_clauses = include_remediation_clauses

        self.doc = fitz.open()
        self.current_page: Optional[fitz.Page] = None
        self.current_y = 50.0

    @staticmethod
    def _fit(text: str, *, fontsize: float, start_x: float | None = None) -> str:
        """Truncate a single-line string to the width actually available.

        ``insert_text`` does not wrap and does not warn: anything past
        ``MARGIN_RIGHT`` is visually clipped while remaining extractable from the
        content stream, so an automated check that reads the PDF text cannot see
        the defect. Widths are measured with the real font metrics rather than
        estimated from a character budget, because the left column starts at a
        different offset than a right-aligned label.
        """
        value = str(text)
        left = MARGIN_LEFT + 12 if start_x is None else start_x
        # ``get_text_length`` measures slightly less than the renderer draws
        # (measured ~1.2% short on accented Latin text), so the budget keeps a
        # margin. Being a little conservative costs a few characters; being
        # optimistic silently removes words from the document.
        available = (MARGIN_RIGHT - left) * 0.95
        if available <= 0:
            return ""
        if fitz.get_text_length(value, fontname="helv", fontsize=fontsize) <= available:
            return value
        # ASCII "..." rather than U+2026: the base-14 Helvetica encoding used by
        # "helv" has no ellipsis glyph and substitutes a middle dot, so a
        # truncated line read like a stray separator instead of a truncation.
        ellipsis = "..."
        budget = available - fitz.get_text_length(ellipsis, fontname="helv", fontsize=fontsize)
        truncated = value
        while truncated and fitz.get_text_length(truncated, fontname="helv", fontsize=fontsize) > budget:
            truncated = truncated[:-1]
        return truncated.rstrip() + ellipsis

    @staticmethod
    def _truncate_to_width(text: str, *, fontsize: float, width: float) -> str:
        ellipsis = "..."
        budget = width - fitz.get_text_length(ellipsis, fontname="helv", fontsize=fontsize)
        value = str(text)
        while value and fitz.get_text_length(value, fontname="helv", fontsize=fontsize) > budget:
            value = value[:-1]
        return value.rstrip() + ellipsis

    @staticmethod
    def _wrap(text: str, *, fontsize: float, max_lines: int, start_x: float | None = None) -> list[str]:
        """Wrap on measured width, never on an estimated character count."""
        left = MARGIN_LEFT + 12 if start_x is None else start_x
        available = (MARGIN_RIGHT - left) * 0.95
        words = str(text).split()
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if fitz.get_text_length(candidate, fontname="helv", fontsize=fontsize) <= available:
                current = candidate
                continue
            if current:
                lines.append(current)
            current = word
            if len(lines) == max_lines:
                break
        else:
            if current:
                lines.append(current)
            return lines[:max_lines]

        # Content remains: mark the last line as truncated rather than dropping
        # the tail silently.
        lines = lines[:max_lines]
        if lines:
            lines[-1] = RegulatoryPdfReportGenerator._truncate_to_width(
                lines[-1], fontsize=fontsize, width=available
            )
        return lines

    @staticmethod
    def _right_aligned_x(text: str, *, fontsize: float) -> float:
        width = fitz.get_text_length(str(text), fontname="helv", fontsize=fontsize)
        return max(MARGIN_LEFT + 12, MARGIN_RIGHT - width)

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

        if self.deprecated_reasons:
            self._render_deprecation_banner()

    def _render_deprecation_banner(self) -> None:
        """Make a superseded analysis impossible to mistake for a current one."""
        page = self.current_page
        assert page is not None
        lines = ["RAPPORT ÉMIS SOUS UN PIPELINE OU UN RULE BOOK DÉPRÉCIÉ"]
        lines.extend(f"· {reason}" for reason in self.deprecated_reasons)
        height = 14 + 11 * len(lines)
        self._ensure_space(height + 6)
        page = self.current_page
        assert page is not None

        rect = fitz.Rect(MARGIN_LEFT, self.current_y, MARGIN_RIGHT, self.current_y + height)
        page.draw_rect(rect, color=COLOR_WARNING, fill=(1.0, 0.96, 0.88), width=1)
        for offset, line in enumerate(lines):
            page.insert_text(
                fitz.Point(MARGIN_LEFT + 10, self.current_y + 14 + 11 * offset),
                self._fit(line, fontsize=7.5 if offset == 0 else 7, start_x=MARGIN_LEFT + 10),
                fontsize=7.5 if offset == 0 else 7,
                color=COLOR_WARNING if offset == 0 else COLOR_TEXT_MUTED,
                fontname="helv",
            )
        self.current_y += height + 12

    def _render_metadata_box(self) -> None:
        page = self.current_page
        assert page is not None

        self._ensure_space(75)
        box_rect = fitz.Rect(MARGIN_LEFT, self.current_y, MARGIN_RIGHT, self.current_y + 65)
        page.draw_rect(box_rect, color=COLOR_BORDER, fill=COLOR_BG_CARD, width=0.8)

        # The report date is the date the analysis was evaluated, not the date a
        # file was downloaded: a report must not appear to be a fresh audit.
        trail = self.report.audit_trail
        analysed_str = trail.evaluated_at_utc.strftime("%d/%m/%Y à %H:%M UTC")

        # Colonne 1
        page.insert_text(fitz.Point(MARGIN_LEFT + 12, self.current_y + 16), "ORGANISATION :", fontsize=7.5, color=COLOR_TEXT_MUTED, fontname="helv")
        page.insert_text(fitz.Point(MARGIN_LEFT + 95, self.current_y + 16), self.organization_name[:40], fontsize=8, color=COLOR_PRIMARY_DARK, fontname="helv")

        page.insert_text(fitz.Point(MARGIN_LEFT + 12, self.current_y + 32), "RÉFÉRENCE SKU :", fontsize=7.5, color=COLOR_TEXT_MUTED, fontname="helv")
        page.insert_text(fitz.Point(MARGIN_LEFT + 95, self.current_y + 32), str(self.product_identifier)[:35], fontsize=8, color=COLOR_PRIMARY_DARK, fontname="helv")

        page.insert_text(fitz.Point(MARGIN_LEFT + 12, self.current_y + 48), "SUPPORT AUDITÉ :", fontsize=7.5, color=COLOR_TEXT_MUTED, fontname="helv")
        page.insert_text(fitz.Point(MARGIN_LEFT + 95, self.current_y + 48), str(self.surface).upper(), fontsize=8, color=COLOR_PRIMARY_DARK, fontname="helv")

        # Colonne 2
        page.insert_text(fitz.Point(MARGIN_LEFT + 280, self.current_y + 16), "DATE D'ANALYSE :", fontsize=7.5, color=COLOR_TEXT_MUTED, fontname="helv")
        page.insert_text(fitz.Point(MARGIN_LEFT + 365, self.current_y + 16), analysed_str, fontsize=8, color=COLOR_PRIMARY_DARK, fontname="helv")

        page.insert_text(fitz.Point(MARGIN_LEFT + 280, self.current_y + 32), "JURIDICTION / DATE D'ANALYSE :", fontsize=7.5, color=COLOR_TEXT_MUTED, fontname="helv")
        page.insert_text(fitz.Point(MARGIN_LEFT + 365, self.current_y + 32), f"{str(trail.as_of_date.isoformat())} — réf. temporelle du verdict", fontsize=8, color=COLOR_PRIMARY_DARK, fontname="helv")

        page.insert_text(fitz.Point(MARGIN_LEFT + 280, self.current_y + 48), "MOTEUR D'ANALYSE :", fontsize=7.5, color=COLOR_TEXT_MUTED, fontname="helv")
        page.insert_text(fitz.Point(MARGIN_LEFT + 365, self.current_y + 48), str(trail.engine_version)[:44], fontsize=8, color=COLOR_PRIMARY_DARK, fontname="helv")

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
        est_height = 132.0
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

        # Ligne 1: intitulé de la décision & verdict
        page.insert_text(
            fitz.Point(MARGIN_LEFT + 12, self.current_y + 15),
            f"#{index} · DÉCISION DU RULE BOOK",
            fontsize=8.5,
            color=COLOR_PRIMARY_DARK,
            fontname="helv",
        )
        verdict_label = self._fit(f"VERDICT : {verdict_str}", fontsize=8, start_x=MARGIN_LEFT + 200)
        page.insert_text(
            fitz.Point(self._right_aligned_x(verdict_label, fontsize=8), self.current_y + 15),
            verdict_label,
            fontsize=8,
            color=card_border,
            fontname="helv",
        )

        # Ligne 2: intitulé de la règle, sur sa propre ligne et tronqué si besoin.
        page.insert_text(
            fitz.Point(MARGIN_LEFT + 12, self.current_y + 28),
            self._fit(item.rule_title, fontsize=8),
            fontsize=8,
            color=COLOR_PRIMARY_DARK,
            fontname="helv",
        )

        # Ligne 3: identifiant de règle + sévérité + force normative. Ces trois
        # valeurs sont ce qui rend le verdict vérifiable, elles ne doivent jamais
        # pouvoir être poussées hors du document.
        severity_str = str(getattr(item.severity, "value", item.severity))
        force_str = str(getattr(item.legal_force, "value", item.legal_force))
        page.insert_text(
            fitz.Point(MARGIN_LEFT + 12, self.current_y + 40),
            self._fit(
                f"Règle : {item.rule_id} · Sévérité : {severity_str} · Force : {force_str}",
                fontsize=7,
                start_x=MARGIN_LEFT + 12,
            ),
            fontsize=7,
            color=COLOR_TEXT_MUTED,
            fontname="helv",
        )

        # Ligne 4: Allégation extraite (telle qu'elle est stockée)
        wrapped_claim = self._fit(f"« {item.claim_text} »", fontsize=8, start_x=MARGIN_LEFT + 118)
        page.insert_text(
            fitz.Point(MARGIN_LEFT + 12, self.current_y + 53),
            f"Allégation analysée : {wrapped_claim}",
            fontsize=8,
            color=COLOR_PRIMARY_BLUE,
            fontname="helv",
        )

        # Ligne 5: Citation juridique officielle, repliée sur deux lignes plutôt
        # que tronquée : la référence légale est le contenu probant du rapport.
        citation_text = f"Fondement juridique : {getattr(item, 'law_reference', item.rule_title)}"
        citation_lines = textwrap.wrap(citation_text, width=118) or [citation_text]
        citation_lines = citation_lines[:2]
        citation_y = self.current_y + 66
        for citation_line in citation_lines:
            page.insert_text(
                fitz.Point(MARGIN_LEFT + 12, citation_y),
                self._fit(citation_line, fontsize=7.5, start_x=MARGIN_LEFT + 12),
                fontsize=7.5,
                color=COLOR_TEXT_MUTED,
                fontname="helv",
            )
            citation_y += 10

        # Ligne 6: Explication / Motif légal (from buyer_explanation or steps)
        explanation = getattr(item.remediation, "buyer_explanation", "") if hasattr(item, "remediation") else ""
        if not explanation and hasattr(item, "reasoning_steps") and item.reasoning_steps:
            explanation = " · ".join([s.finding for s in item.reasoning_steps[:2]])

        wrapped_exp = textwrap.wrap(explanation or "Aucun motif de remédiation persisté pour cette décision.", width=118)
        exp_y = self.current_y + 66 + 10 * len(citation_lines) + 3
        for exp_line in wrapped_exp[:2]:
            page.insert_text(
                fitz.Point(MARGIN_LEFT + 12, exp_y),
                exp_line,
                fontsize=7.5,
                color=COLOR_TEXT_MAIN,
                fontname="helv",
            )
            exp_y += 11

        # Ligne 6: Revue humaine historisée. Un lecteur doit voir qu'un verdict a été
        # contesté ou revu, jamais le découvrir ailleurs :
        # voir qu'un verdict a été contesté ou revu, jamais le découvrir par ailleurs.
        review = getattr(item, "human_review", None)
        if review is not None:
            exp_y += 11
            page.insert_text(
                fitz.Point(MARGIN_LEFT + 12, exp_y),
                self._fit(
                    f"REVUE HUMAINE : {str(review.decision).upper()}"
                    + (f" par {review.reviewer_display_name}" if review.reviewer_display_name else "")
                    + (f" — {review.rationale or review.comment}" if (review.rationale or review.comment) else ""),
                    fontsize=7,
                    start_x=MARGIN_LEFT + 12,
                ),
                fontsize=7,
                color=COLOR_PRIMARY_BLUE,
                fontname="helv",
            )
            est_height += 11

        # Ligne 8: Clause de remédiation
        if self.include_remediation_clauses and remediation_text:
            page.insert_text(
                fitz.Point(MARGIN_LEFT + 12, exp_y + 5),
                "RECOMMANDATION / CLAUSE DE REMÉDIATION :",
                fontsize=7,
                color=COLOR_PRIMARY_BLUE,
                fontname="helv",
            )
            wrapped_rem = textwrap.wrap(remediation_text, width=118)
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

        # The generator must never compute a hash. A freshly computed SHA-256 looks
        # like cryptographic proof while only proving the generator saw some text;
        # that is exactly how a report asserting "0 allégations" ended up carrying
        # a genuine-looking fingerprint. Only stored values are printed.
        trail = self.report.audit_trail
        lines: list[tuple[str, Any]] = [
            ("Version du moteur", str(trail.engine_version)),
            ("Empreinte du Rule Book", str(trail.rulebook_version)),
            ("Empreinte du résultat d'analyse (SHA-256)", str(trail.report_sha256) or "NON DISPONIBLE"),
            ("Empreinte du manifeste d'entrée (SHA-256)", str(trail.source_sha256) or "NON DISPONIBLE"),
            ("Empreinte du registre de preuves (SHA-256)", str(trail.evidence_manifest_sha256) or "NON DISPONIBLE"),
            ("Scellement de la piste d'audit", str(trail.record_hash) or "NON DISPONIBLE"),
        ]

        box_height = 14 + 12 * len(lines) + 24
        self._ensure_space(box_height)
        page = self.current_page
        assert page is not None
        box_rect = fitz.Rect(MARGIN_LEFT, self.current_y, MARGIN_RIGHT, self.current_y + box_height)
        page.draw_rect(box_rect, color=COLOR_BORDER, fill=COLOR_BG_CARD, width=0.8)

        for offset, (label, value) in enumerate(lines):
            y = self.current_y + 14 + 12 * offset
            page.insert_text(
                fitz.Point(MARGIN_LEFT + 10, y),
                f"{label} :",
                fontsize=6.5,
                color=COLOR_TEXT_MUTED,
                fontname="helv",
            )
            page.insert_text(
                fitz.Point(MARGIN_LEFT + 200, y),
                str(value)[:80],
                fontsize=6.5,
                color=COLOR_PRIMARY_DARK,
                fontname="helv",
            )

        page.insert_text(
            fitz.Point(MARGIN_LEFT + 10, self.current_y + box_height - 12),
            "Cette empreinte atteste l'intégrité du document produit, pas la véracité juridique des allégations analysées.",
            fontsize=6,
            color=COLOR_TEXT_MUTED,
            fontname="helv",
        )

        self.current_y += box_height + 14
        self._render_verification_block()

    def _render_verification_block(self) -> None:
        """Print the public handle and signature a third party needs to check us."""
        lines: list[tuple[str, str]] = [
            (
                "Référence de vérification",
                self.signature_reference or "NON DISPONIBLE",
            ),
            (
                "Signature HMAC-SHA-256",
                self.signature_value or "NON DISPONIBLE",
            ),
            (
                "Identifiant de clé",
                self.signature_key_id or "NON DISPONIBLE",
            ),
        ]
        height = 14 + 11 * len(lines) + 26
        self._ensure_space(height)
        page = self.current_page
        assert page is not None

        rect = fitz.Rect(MARGIN_LEFT, self.current_y, MARGIN_RIGHT, self.current_y + height)
        page.draw_rect(rect, color=COLOR_BORDER, fill=COLOR_BG_CARD, width=0.8)

        for offset, (label, value) in enumerate(lines):
            y = self.current_y + 14 + 11 * offset
            page.insert_text(
                fitz.Point(MARGIN_LEFT + 10, y),
                f"{label} :",
                fontsize=6.5,
                color=COLOR_TEXT_MUTED,
                fontname="helv",
            )
            page.insert_text(
                fitz.Point(MARGIN_LEFT + 200, y),
                self._fit(str(value), fontsize=6.5, start_x=MARGIN_LEFT + 200),
                fontsize=6.5,
                color=COLOR_PRIMARY_DARK,
                fontname="helv",
            )

        for offset, note in enumerate(
            (
                "Vérification publique : GET /api/v1/reports/verify/{référence}.",
                "La signature atteste la correspondance avec une analyse persistée non modifiée; "
                "elle ne certifie ni la véracité juridique ni l'identité de l'émetteur.",
            )
        ):
            page.insert_text(
                fitz.Point(MARGIN_LEFT + 10, self.current_y + height - 20 + 9 * offset),
                self._fit(note, fontsize=6, start_x=MARGIN_LEFT + 10),
                fontsize=6,
                color=COLOR_TEXT_MUTED,
                fontname="helv",
            )
        self.current_y += height
        self.current_y += 55
