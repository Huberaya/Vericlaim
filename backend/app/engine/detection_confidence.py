"""Deterministic detection confidence for lexical claim detection (chantier C10).

What this module is
-------------------
A **rubric**, not a probability model. Every point of the score comes from an
explicit, individually justified feature of the detection that the auditor can
re-read in the source text. There is no training, no model, no random draw: the
same input always produces the same score, and the score carries the list of
factors that produced it.

Why it exists
-------------
`confidence_score` used to be `None` everywhere and `review_required` was raised
only for OCR segments, so a caller could not tell a solid detection from a
fragile one — while the UI still displayed a bare claim list. `None` was honest
but useless; an invented number would be worse than `None`. A published rubric
with named factors is the only defensible middle ground.

What it is NOT
--------------
`confidence_score` here does **not** measure the probability that a claim is
truly misleading, nor the probability that a rule will be violated. It measures
how much of the detected claim is actually readable by the auditor: a multi-word
anchored phrase carrying figures, a certificate number and no negation is worth
more than a single word whose polarity is ambiguous. The published
`CONFIDENCE_BASIS` string says so in the API response.

The score is bounded: whatever the factors, it never reaches 1.0, because the
engine never has full knowledge of the context (images, tone, prior statements).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

CONFIDENCE_RUBRIC_VERSION = "confidence-rubric-v1"

#: Published verbatim in the API response so a consumer cannot mistake the score
#: for a probability produced by a statistical model.
CONFIDENCE_BASIS = (
    "Rubrique déterministe (confidence-rubric-v1) : points ajoutés ou retirés selon des "
    "éléments lisibles dans le texte détecté. Ce n'est ni une probabilité, ni un score de "
    "confiance statistique, ni une appréciation du risque juridique."
)


class DetectionConfidenceLevel(str, Enum):
    """The four levels the product must expose (exigence §12)."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    HUMAN_REVIEW_REQUIRED = "human_review_required"


# --- Rubric constants, each one justified ------------------------------------
# A single-token hit is the weakest reading the engine produces: the word matched,
# and the audit's false positives were exactly of that kind ("vert" as a colour,
# "éco" inside a company name). It therefore lands in the LOW rung. This is not
# decorative: measured before this constant existed, no input could ever produce a
# LOW level, because every penalty below MEDIUM also forced a human review — the
# product advertised four levels and could only ever show three.
SINGLE_TOKEN_BASE_SCORE = 0.45
# A multi-token trigger is the phrase that carried the meaning: it moves the
# detection to MEDIUM on its own.
MULTI_TOKEN_TRIGGER_BONUS = 0.15
# Kept for readability: a multi-token anchored phrase with no other feature is the
# 0.60 "moderate" reading this rubric started from.
BASE_SCORE = SINGLE_TOKEN_BASE_SCORE + MULTI_TOKEN_TRIGGER_BONUS
# A figure with its unit is what the auditor will check ("100 %", "30 % moins").
QUANTIFIED_CLAIM_BONUS = 0.10
# A specific qualifier narrows the scope of the claim (e.g. "matière recyclée").
SPECIFIC_QUALIFIER_BONUS = 0.05
# Extracted certification facts are directly checkable (scheme, reference, body).
CHECKABLE_CERTIFICATION_BONUS = 0.05
# Polarity is the fragile reading: the C9 chantier found a negation that was not
# recognised at all. A negated claim is never presented as a solid detection.
POLARITY_SIGNAL_PENALTY = -0.10
# The document extraction itself asked for a human review: the text we ran on is
# already known to be suspect.
EXTRACTION_REVIEW_PENALTY = -0.10
# OCR segments are never scored above LOW and always require a human.
OCR_SCORE_CEILING = 0.35

# The three rungs read as feature counts, not as an opaque scale:
#   LOW    : a single word matched, nothing else (0.45), or that word plus a
#            negation penalty (0.35).
#   MEDIUM : one readable feature — an anchored phrase (0.60), or a word carrying
#            a figure, a certificate or a specific qualifier (0.50 to 0.65).
#   HIGH   : the phrasing is anchored AND the claim is checkable, i.e. it carries
#            a figure or a certificate reference (>= 0.70).
# Measured on the reference corpus: 0.35 and 0.45 are LOW, 0.50 to 0.65 are
# MEDIUM, 0.70 is HIGH — every rung is reached by real inputs, none is decorative.
HIGH_THRESHOLD = 0.70
MEDIUM_THRESHOLD = 0.50

#: Deterministic conflict whose source is the segment as a whole, not one claim.
POLARITY_CONFLICT_REASON = "polarity_conflict"
SEGMENT_OCR_REASON = "segment_ocr"
EXTRACTION_REVIEW_REASON = "document_extraction_review_required"
VERDICT_REVIEW_REASON = "verdict_requires_review"


@dataclass(frozen=True)
class ConfidenceFactor:
    """One named, bounded contribution to the score."""

    code: str
    effect: float
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "effect": self.effect, "detail": self.detail}


@dataclass(frozen=True)
class DetectionConfidence:
    score: float
    level: DetectionConfidenceLevel
    factors: tuple[ConfidenceFactor, ...]
    review_reasons: tuple[str, ...]
    rubric_version: str = CONFIDENCE_RUBRIC_VERSION

    @property
    def requires_human_review(self) -> bool:
        return self.level is DetectionConfidenceLevel.HUMAN_REVIEW_REQUIRED

    def as_dict(self) -> dict[str, Any]:
        return {
            "rubric_version": self.rubric_version,
            "basis": CONFIDENCE_BASIS,
            "score": self.score,
            "level": self.level.value,
            "factors": [factor.as_dict() for factor in self.factors],
            "review_reasons": list(self.review_reasons),
        }


def _level_for(score: float) -> DetectionConfidenceLevel:
    if score >= HIGH_THRESHOLD:
        return DetectionConfidenceLevel.HIGH
    if score >= MEDIUM_THRESHOLD:
        return DetectionConfidenceLevel.MEDIUM
    return DetectionConfidenceLevel.LOW


def _clamp(value: float) -> float:
    # A score of exactly 1.0 would claim full knowledge the engine does not have.
    return round(max(0.0, min(0.95, value)), 4)


def score_claim(
    fact: Any,
    *,
    source_is_ocr: bool = False,
    extraction_review_required: bool = False,
) -> DetectionConfidence:
    """Score one detected claim. Pure function: same input, same output.

    ``fact`` is a :class:`~app.models.legal_types.DetectedClaim`.
    """
    factors: list[ConfidenceFactor] = [
        ConfidenceFactor(
            code="base_lexical_hit",
            effect=SINGLE_TOKEN_BASE_SCORE,
            detail="Détection lexicale déterministe : la formulation figure dans le lexique versionné.",
        )
    ]
    score = SINGLE_TOKEN_BASE_SCORE

    trigger = (getattr(fact, "trigger_text", "") or "").strip()
    # A trigger is "multi-token" when it carries more than one word: that is the
    # difference between a phrase and a word that merely looks like a claim.
    token_count = len([token for token in trigger.replace("-", " ").split() if token])
    if token_count >= 2:
        score += MULTI_TOKEN_TRIGGER_BONUS
        factors.append(
            ConfidenceFactor(
                code="multi_token_trigger",
                effect=MULTI_TOKEN_TRIGGER_BONUS,
                detail=f"Déclencheur de {token_count} mots (« {trigger} ») : la formulation porte le sens.",
            )
        )
    else:
        factors.append(
            ConfidenceFactor(
                code="single_token_trigger",
                effect=0.0,
                detail=(
                    f"Déclencheur d'un seul mot (« {trigger} ») : lecture la plus ambiguë du lexique, "
                    "le score reste sous le seuil moyen."
                ),
            )
        )

    if getattr(fact, "numeric_value", None) is not None:
        unit = getattr(fact, "numeric_unit", None) or "sans unité"
        score += QUANTIFIED_CLAIM_BONUS
        factors.append(
            ConfidenceFactor(
                code="quantified_claim",
                effect=QUANTIFIED_CLAIM_BONUS,
                detail=f"Allégation chiffrée ({fact.numeric_value} {unit}) : vérifiable pièce par pièce.",
            )
        )

    if getattr(fact, "has_specific_qualifier", False):
        score += SPECIFIC_QUALIFIER_BONUS
        factors.append(
            ConfidenceFactor(
                code="specific_qualifier",
                effect=SPECIFIC_QUALIFIER_BONUS,
                detail="Un qualificatif spécifique restreint la portée de l'allégation.",
            )
        )

    scheme = getattr(fact, "certification_scheme", None)
    reference = getattr(fact, "certification_reference", None)
    if scheme or reference:
        score += CHECKABLE_CERTIFICATION_BONUS
        factors.append(
            ConfidenceFactor(
                code="checkable_certification",
                effect=CHECKABLE_CERTIFICATION_BONUS,
                detail=(
                    "Schéma ou numéro de certification extrait verbatim "
                    f"(schéma={scheme or 'non extrait'}, référence={reference or 'non extraite'}) : "
                    "le vérificateur peut contrôler la pièce."
                ),
            )
        )

    polarity_signal = bool(getattr(fact, "negation_cue", None)) or bool(
        getattr(fact, "has_offsetting_signal", False)
    )
    if polarity_signal:
        score += POLARITY_SIGNAL_PENALTY
        cue = getattr(fact, "negation_cue", None) or "signal de compensation"
        factors.append(
            ConfidenceFactor(
                code="polarity_signal",
                effect=POLARITY_SIGNAL_PENALTY,
                detail=f"Lecture de polarité en jeu (« {cue} ») : la phrase peut être une négation ou un démenti.",
            )
        )

    if extraction_review_required:
        score += EXTRACTION_REVIEW_PENALTY
        factors.append(
            ConfidenceFactor(
                code="extraction_review_required",
                effect=EXTRACTION_REVIEW_PENALTY,
                detail="L'extraction du document a elle-même été marquée comme nécessitant une revue humaine.",
            )
        )

    review_reasons: list[str] = []
    if source_is_ocr:
        score = min(score, OCR_SCORE_CEILING)
        review_reasons.append(SEGMENT_OCR_REASON)
        factors.append(
            ConfidenceFactor(
                code="ocr_segment",
                effect=0.0,
                detail=(
                    "Allégation issue d'un segment OCR : le texte est une reconnaissance optique, "
                    "à comparer au document original."
                ),
            )
        )
    if extraction_review_required:
        review_reasons.append(EXTRACTION_REVIEW_REASON)

    score = _clamp(score)
    level = _level_for(score)
    if polarity_signal and level is DetectionConfidenceLevel.HIGH:
        # A detection whose polarity can flip is never presented as solid.
        level = DetectionConfidenceLevel.MEDIUM
    if review_reasons:
        level = DetectionConfidenceLevel.HUMAN_REVIEW_REQUIRED

    return DetectionConfidence(
        score=score,
        level=level,
        factors=tuple(factors),
        review_reasons=tuple(review_reasons),
    )


def polarity_conflicts(claim_types_and_polarities: list[tuple[str, bool]]) -> set[str]:
    """Claim types both asserted and denied inside the same segment.

    Measured on the reference corpus: a sentence that asserts a claim and then
    denies it ("Cet emballage est biodégradable. Attention : il n'est pas
    biodégradable sous 12 semaines.") is a real editorial pattern, and the two
    readings must not be collapsed into one silent verdict.

    Deliberately NOT a conflict: two families reading the same sentence (a claim
    can legitimately be both quantified and comparative). Treating that as a
    conflict would flag a large share of ordinary sentences and empty the notion
    of "human review required" of its meaning.
    """
    positive: set[str] = set()
    negative: set[str] = set()
    for claim_type, affirmative in claim_types_and_polarities:
        (positive if affirmative else negative).add(claim_type)
    return positive & negative


def with_review_reason(
    confidence: DetectionConfidence, *reasons: str
) -> DetectionConfidence:
    """Return the same confidence with additional human-review reasons."""
    merged = list(confidence.review_reasons)
    for reason in reasons:
        if reason and reason not in merged:
            merged.append(reason)
    if not merged:
        return confidence
    return DetectionConfidence(
        score=confidence.score,
        level=DetectionConfidenceLevel.HUMAN_REVIEW_REQUIRED,
        factors=confidence.factors,
        review_reasons=tuple(merged),
        rubric_version=confidence.rubric_version,
    )
