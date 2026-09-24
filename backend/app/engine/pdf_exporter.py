"""Génération du Certificat d'Audit Juridique officiel en PDF (VeriClaim AI).

Ce module compile le rapport d'évaluation en un document PDF opposable,
comportant l'empreinte cryptographique SHA-256, les références d'articles de loi,
le chiffrage de l'exposition financière et les clauses de remédiation contractuelle.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.models.schemas import EvaluationResponse

# Palette de couleurs corporate / compliance
C_NAVY = colors.HexColor("#0f172a")
C_BLUE = colors.HexColor("#1e40af")
C_SLATE = colors.HexColor("#475569")
C_BG_LIGHT = colors.HexColor("#f8fafc")
C_BORDER = colors.HexColor("#e2e8f0")
C_RED = colors.HexColor("#dc2626")
C_RED_BG = colors.HexColor("#fef2f2")
C_GREEN = colors.HexColor("#16a34a")
C_GREEN_BG = colors.HexColor("#f0fdf4")
C_AMBER = colors.HexColor("#d97706")
C_AMBER_BG = colors.HexColor("#fffbeb")


def _get_status_style(status: str) -> tuple[str, colors.Color, colors.Color]:
    """Retourne libellé français, couleur de texte et fond pour le badge."""
    s = str(status).upper()
    if "NON_COMPLIANT" in s:
        return "NON CONFORME — INFRACTION DÉTECTÉE", C_RED, C_RED_BG
    if "COMPLIANT" in s:
        return "CONFORME AU RÉFÉRENTIEL AUDITÉ", C_GREEN, C_GREEN_BG
    if "CONDITIONAL_REJECT" in s:
        return "PUBLICATION À SUSPENDRE — PREUVES REQUISES", C_AMBER, C_AMBER_BG
    if "REVIEW_REQUIRED" in s:
        return "REVUE JURIDIQUE / PROBATOIRE REQUISE", C_AMBER, C_AMBER_BG
    if "NO_CLAIMS_DETECTED" in s:
        return "AUCUNE ALLÉGATION DÉTECTÉE", C_SLATE, C_BG_LIGHT
    return s.replace("_", " "), C_SLATE, C_BG_LIGHT


def generate_audit_pdf(evaluation: EvaluationResponse) -> bytes:
    """Génère l'attestation PDF d'audit juridique à partir de l'EvaluationResponse."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=36,
    )

    base_styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "DocTitle",
        parent=base_styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=17,
        leading=21,
        textColor=C_NAVY,
        spaceAfter=3,
    )
    subtitle_style = ParagraphStyle(
        "DocSubtitle",
        parent=base_styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=C_SLATE,
    )
    section_title = ParagraphStyle(
        "SectionTitle",
        parent=base_styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        textColor=C_NAVY,
        spaceBefore=12,
        spaceAfter=6,
    )
    body_style = ParagraphStyle(
        "BodyDark",
        parent=base_styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11.5,
        textColor=C_NAVY,
    )
    legal_ref_style = ParagraphStyle(
        "LegalRef",
        parent=base_styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8.5,
        leading=11,
        textColor=C_BLUE,
    )
    quote_style = ParagraphStyle(
        "QuoteStyle",
        parent=base_styles["Italic"],
        fontName="Helvetica-Oblique",
        fontSize=8.5,
        leading=11.5,
        textColor=C_NAVY,
    )
    clause_style = ParagraphStyle(
        "ClauseStyle",
        parent=base_styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=11,
        textColor=colors.HexColor("#1e293b"),
    )
    footer_style = ParagraphStyle(
        "FooterNote",
        parent=base_styles["Normal"],
        fontName="Helvetica",
        fontSize=7,
        leading=9.5,
        textColor=C_SLATE,
    )

    story = []

    # 1. En-tête
    header_data = [
        [
            Paragraph("<b>VERICLAIM AI</b> — RAPPORT D'AUDIT RÉGLEMENTAIRE", title_style),
            Paragraph(
                f"<b>ID Audit :</b> {evaluation.audit_trail.audit_id[:8]}<br/>"
                f"<b>Date :</b> {datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M')} UTC",
                ParagraphStyle("HRight", parent=subtitle_style, alignment=2),
            ),
        ]
    ]
    header_table = Table(header_data, colWidths=[360, 163])
    header_table.setStyle(
        TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
        ])
    )
    story.append(header_table)
    story.append(
        Paragraph(
            "Système expert déterministe d'audit des allégations environnementales · Droit français (AGEC/C. Env) & Directives UE",
            subtitle_style,
        )
    )
    story.append(Spacer(1, 8))
    story.append(HRFlowable(width="100%", thickness=1.5, color=C_NAVY, spaceAfter=10))

    # 2. Synthèse & Badges (Card)
    status_label, status_fg, status_bg = _get_status_style(evaluation.overall_compliance.value)
    risk_color = C_RED if evaluation.risk_score >= 70 else (C_AMBER if evaluation.risk_score >= 40 else C_GREEN)

    summary_card_data = [
        [
            Paragraph("<b>STATUT GLOBAL DE CONFORMITÉ :</b>", subtitle_style),
            Paragraph("<b>INDICE DE RISQUE :</b>", subtitle_style),
            Paragraph("<b>EXPOSITION FINANCIÈRE MAX :</b>", subtitle_style),
        ],
        [
            Paragraph(f"<b>{status_label}</b>", ParagraphStyle("StatBadge", fontName="Helvetica-Bold", fontSize=9, leading=12, textColor=status_fg)),
            Paragraph(f"<b>{evaluation.risk_score} / 100</b>", ParagraphStyle("RiskScore", fontName="Helvetica-Bold", fontSize=14, leading=16, textColor=risk_color)),
            Paragraph(f"<b>{evaluation.legal_exposure_estimate}</b>", ParagraphStyle("Exposure", fontName="Helvetica-Bold", fontSize=9, leading=12, textColor=C_RED)),
        ],
    ]
    summary_table = Table(summary_card_data, colWidths=[240, 100, 183])
    summary_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), C_BG_LIGHT),
            ("BOX", (0, 0), (-1, -1), 1, C_BORDER),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, C_BORDER),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ])
    )
    story.append(summary_table)
    story.append(Spacer(1, 10))

    # 3. Paramètres de l'évaluation
    as_of = evaluation.audit_trail.as_of_date.strftime("%d/%m/%Y")
    context_data = [
        [
            Paragraph(f"<b>Juridiction de contrôle :</b> France (Loi AGEC & droit UE applicable)", body_style),
            Paragraph(f"<b>Date d'opposabilité :</b> {as_of}", body_style),
        ],
        [
            Paragraph(f"<b>Allégations détectées :</b> {evaluation.detected_claims_count}", body_style),
            Paragraph(f"<b>Infractions légales directes :</b> {evaluation.violations_count}", body_style),
        ],
    ]
    context_table = Table(context_data, colWidths=[260, 263])
    context_table.setStyle(
        TableStyle([
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ])
    )
    story.append(context_table)
    story.append(Spacer(1, 8))

    # 4. Détail des évaluations par allégation
    story.append(Paragraph("DÉCISIONS JURIDIQUES & ANALYSE DÉTAILLÉE", section_title))

    if not evaluation.evaluations:
        story.append(Paragraph("<i>Aucune allégation environnementale n'a été qualifiée dans ce document.</i>", body_style))
    else:
        for idx, ev in enumerate(evaluation.evaluations, 1):
            v_label, v_fg, _ = _get_status_style(ev.verdict.value)
            sanction_str = ev.sanction.notes if ev.sanction else "Sanction non codifiée"
            if ev.sanction and ev.sanction.max_legal_person_eur:
                sanction_str = f"Plafond légal : {ev.sanction.max_legal_person_eur:,.0f} € (Art. {ev.sanction.legal_basis})"

            claim_box_data = [
                [
                    Paragraph(f"<b>#{idx} Allégation détectée :</b> « {ev.trigger_text} »", quote_style),
                    Paragraph(f"<b>Verdict :</b> <font color='{v_fg.hexval()}'>{v_label}</font>", ParagraphStyle("VRight", fontName="Helvetica", fontSize=8, alignment=2)),
                ],
                [
                    Paragraph(f"<b>Règle appliquée :</b> {ev.rule_title}<br/><b>Fondement légal :</b> <font color='{C_BLUE.hexval()}'>{ev.law_reference}</font>", legal_ref_style),
                    Paragraph(f"<b>Gravité :</b> {ev.severity.value}<br/><b>Force :</b> {ev.legal_force.value}", body_style),
                ],
                [
                    Paragraph(f"<b>Analyse juridique :</b> {ev.remediation.buyer_explanation}", body_style),
                    Paragraph(f"<b>Sanction encourue :</b><br/>{sanction_str}", ParagraphStyle("Sanct", parent=body_style, fontSize=7.5, leading=9.5, textColor=C_RED if ev.is_legal_violation else C_SLATE)),
                ],
                [
                    Paragraph(f"<b>Réécriture recommandée :</b><br/><i>{ev.remediation.recommended_rewrite}</i>", clause_style),
                    Paragraph(f"<b>Clause fournisseur suggérée :</b><br/>{ev.remediation.supplier_contract_clause[:180]}...", ParagraphStyle("ClauseSmall", parent=clause_style, fontSize=7, leading=9)),
                ],
            ]
            claim_table = Table(claim_box_data, colWidths=[340, 183])
            claim_table.setStyle(
                TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1), C_BG_LIGHT),
                    ("BOX", (0, 0), (-1, -1), 0.75, C_RED if ev.is_legal_violation else C_BORDER),
                    ("INNERGRID", (0, 0), (-1, -1), 0.5, C_BORDER),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ])
            )
            story.append(KeepTogether([claim_table, Spacer(1, 6)]))

    story.append(Spacer(1, 8))

    # 5. Piste d'audit & Empreintes cryptographiques (Tamper-evident)
    story.append(Paragraph("EMPREINTE CRYPTOGRAPHIQUE & PISTE D'AUDIT", section_title))
    trail_data = [
        [
            Paragraph(f"<b>SHA-256 Texte Source :</b> <font name='Courier' size='6.5'>{evaluation.audit_trail.source_sha256}</font>", body_style),
        ],
        [
            Paragraph(f"<b>SHA-256 Rapport Audit :</b> <font name='Courier' size='6.5'>{evaluation.audit_trail.report_sha256}</font>", body_style),
        ],
        [
            Paragraph(f"<b>Hash d'intégrité chaîné (Audit Ledger) :</b> <font name='Courier' size='6.5'>{evaluation.audit_trail.record_hash}</font>", body_style),
        ],
    ]
    trail_table = Table(trail_data, colWidths=[523])
    trail_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), C_BG_LIGHT),
            ("BOX", (0, 0), (-1, -1), 0.5, C_BORDER),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ])
    )
    story.append(trail_table)
    story.append(Spacer(1, 8))

    # 6. Mentions légales / Avertissement
    disclaimer = (
        "<b>Avis de non-responsabilité juridique :</b> Ce document est généré de manière automatisée par le moteur "
        "réglementaire VeriClaim AI à titre d'outil d'aide à la décision et de contrôle de conformité. Il ne constitue pas un "
        "conseil juridique, une consultation d'avocat au sens de la loi du 31 décembre 1971, ni une décision officielle de la DGCCRF. "
        "Les constatations sont basées sur le RuleBook v" + evaluation.audit_trail.rulebook_version + " et les données fournies au moment de l'analyse."
    )
    story.append(Paragraph(disclaimer, footer_style))

    doc.build(story)
    return buffer.getvalue()
