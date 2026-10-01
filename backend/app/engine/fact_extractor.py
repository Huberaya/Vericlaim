"""Deterministic extraction of environmental assertions from supplied copy.

Regular expressions are used only as lexical sensors. They feed typed claim
facts into the rule book, temporal/scope checks and evidence validators; no
LLM, fuzzy score or inferred legal conclusion is used here.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from app.engine import lexicon
from app.models.legal_types import ClaimType, DetectedClaim


# The lexicon lives in :mod:`app.engine.lexicon` (C9): it is versioned and measured
# by ``tests/corpus/claim_detection_corpus.json``, separately from this walker.
CLAIM_PATTERNS: tuple[tuple[ClaimType, re.Pattern[str]], ...] = (
    (ClaimType.BIODEGRADABLE, lexicon.BIODEGRADABLE),
    (ClaimType.NATURE_FRIENDLY, lexicon.NATURE_FRIENDLY),
    (ClaimType.CARBON_NEUTRALITY, lexicon.CARBON_NEUTRALITY),
    (ClaimType.COMPARATIVE, lexicon.COMPARATIVE),
    (ClaimType.RECYCLABLE, lexicon.RECYCLABLE),
    # Before C9 this family did not exist, so a certificate was never a fact.
    (ClaimType.CERTIFICATION, lexicon.CERTIFICATION_SCHEME),
    (ClaimType.GENERIC_ENVIRONMENTAL, lexicon.GENERIC_ENVIRONMENTAL),
)

OFFSET_SIGNAL = re.compile(
    r"\b(?:compens[eé](?:e|s|es)?|compensation|cr[eé]dits?\s+carbone|quotas?\s+(?:carbone|d['’]?[eé]mission)|offset(?:ting)?|carbon\s+credits?|verified\s+carbon\s+standard|VCS|Gold\s+Standard)\b",
    re.IGNORECASE,
)

ENVIRONMENTAL_METRIC = re.compile(
    # "empreinte" alone is ambiguous: a tyre leaves an "empreinte" too. It only
    # counts as a climate metric when qualified (measured on corpus case FP14).
    r"\b(?:CO\s?2|carbone|[eé]missions?|empreinte\s+(?:carbone|climatique|environnementale|CO\s?2)"
    r"|climat(?:ique)?|greenhouse\s+gas|GHG|carbon\s+footprint)\b",
    re.IGNORECASE,
)
NUMBER = re.compile(r"(?<!\w)([-−]?\d+(?:[.,]\d+)?)\s*(%|kg|g|t|tonnes?|kg\s?CO\s?2|g\s?CO\s?2)?", re.IGNORECASE)
OFFSET_NEGATION = re.compile(
    r"(?:\b(?:ne\s+\w+(?:\s+\w+){0,3}\s+pas|n['’]est\s+pas|pas|non|sans)\s*"
    # French negation is usually followed by a determiner: "ne contient pas DE
    # matière recyclée". Without this, the cue was found but rejected, and a
    # negated claim was reported as an affirmative one (measured on case K).
    r"(?:(?:de|d['’]|du|des|la|le|les|l['’]|un|une|aucun|aucune|plus|jamais)\s+)*"
    r"|\b(?:not|never|without|no\s+longer)\s*|\bnon[- ])$",
    re.IGNORECASE,
)
SPECIFIC_DETAIL = re.compile(
    r"\b\d+(?:[.,]\d+)?\s?%\s+(?:de\s+)?(?:CO\s?2|carbone|mati[eè]re\s+recycl[eé]e|plastique\s+recycl[eé])\b"
    r"|\b(?:g|kg|t)\s?CO\s?2\s?(?:e|eq)?\s*(?:par|/|pour)\b",
    re.IGNORECASE,
)


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    """Split while preserving source offsets and decimal numbers."""
    spans: list[tuple[int, int]] = []
    start = 0
    for index, char in enumerate(text):
        boundary = char in "!?;\n"
        if char == ".":
            previous_is_digit = index > 0 and text[index - 1].isdigit()
            next_is_digit = index + 1 < len(text) and text[index + 1].isdigit()
            boundary = not (previous_is_digit and next_is_digit)
        if boundary:
            end = index + 1
            if text[start:end].strip():
                left = start + len(text[start:end]) - len(text[start:end].lstrip())
                right = end - (len(text[start:end]) - len(text[start:end].rstrip()))
                if left < right:
                    spans.append((left, right))
            start = index + 1
    if start < len(text):
        tail = text[start:]
        if tail.strip():
            left = start + len(tail) - len(tail.lstrip())
            right = len(text) - (len(tail) - len(tail.rstrip()))
            if left < right:
                spans.append((left, right))
    return spans


def _negation_before(sentence: str, trigger_start: int) -> str | None:
    prefix = sentence[max(0, trigger_start - 55) : trigger_start]
    match = OFFSET_NEGATION.search(prefix)
    return match.group(0).strip() if match else None


def _number_in_sentence(sentence: str) -> tuple[Decimal | None, str | None]:
    for match in NUMBER.finditer(sentence):
        # Numeric facts are only claim facts when the same sentence has an
        # environmental metric. Dates and unrelated dimensions are ignored.
        if ENVIRONMENTAL_METRIC.search(sentence):
            raw = match.group(1).replace("−", "-").replace(",", ".")
            try:
                return Decimal(raw), match.group(2)
            except InvalidOperation:
                return None, None
    return None, None


def _has_quantified_environmental_claim(sentence: str) -> re.Match[str] | None:
    if not ENVIRONMENTAL_METRIC.search(sentence):
        return None
    percent = re.search(r"(?<!\w)[-−]?\d+(?:[.,]\d+)?\s?%", sentence)
    emission_value = re.search(
        r"\b(?:CO\s?2|carbone|[eé]missions?|empreinte)\b.{0,35}\b\d+(?:[.,]\d+)?\s?(?:kg|g|t|tonnes?)\b",
        sentence,
        re.IGNORECASE,
    )
    return percent or emission_value


def _claim_is_asserted(sentence: str, trigger_start: int) -> tuple[bool, str | None]:
    negation = _negation_before(sentence, trigger_start)
    return (negation is None, negation)


def _certification_match(sentence: str) -> re.Match[str] | None:
    """Detect a certification claim, or nothing.

    A scheme name (FSC, ECOLABEL, ISO 14001…) is enough on its own. The bare verb
    is not: "certifié conforme" is a conformity mark and "certifié ISO 9001" is a
    quality certificate, neither of which is an environmental claim. The verb only
    counts when the same sentence carries an environmental marker, which is a
    lexical reading, not a semantic one — the limits are published with the corpus.
    """
    scheme = lexicon.CERTIFICATION_SCHEME.search(sentence)
    if scheme is not None:
        return scheme
    verb = lexicon.CERTIFICATION_VERB.search(sentence)
    if verb is None:
        return None
    if lexicon.CONFORMITY_MARK.search(sentence) or not lexicon.ENVIRONMENTAL_MARKER.search(sentence):
        return None
    return verb


def _certification_facts(sentence: str) -> dict[str, str | None]:
    """Verbatim scheme, reference and body — never inferred, never completed."""
    scheme = lexicon.CERTIFICATION_SCHEME.search(sentence)
    number = lexicon.CERTIFICATE_NUMBER.search(sentence)
    body = lexicon.CERTIFICATE_BODY.search(sentence)
    body_text = None
    if body is not None:
        # Alternatives carry their own group; take the first one that matched
        # rather than assuming a fixed position, which silently dropped bodies.
        body_text = next((group.strip().rstrip(" .;:,-") for group in body.groups() if group), None)
    return {
        "certification_scheme": scheme.group(0) if scheme else None,
        "certification_reference": (
            next((group for group in number.groups() if group), None) if number else None
        ),
        "certification_body": body_text,
    }


class FactExtractor:
    """Pure deterministic claim detector; the same text yields the same facts."""

    def extract(self, text: str) -> list[DetectedClaim]:
        if not text or not text.strip():
            return []

        candidates: list[tuple[int, int, int, ClaimType, re.Match[str], int, int]] = []
        priority = {kind: index for index, (kind, _) in enumerate(CLAIM_PATTERNS)}
        for sentence_start, sentence_end in _sentence_spans(text):
            sentence = text[sentence_start:sentence_end]
            found_for_sentence: set[ClaimType] = set()
            for claim_type, pattern in CLAIM_PATTERNS:
                if claim_type == ClaimType.CERTIFICATION:
                    match = _certification_match(sentence)
                else:
                    match = pattern.search(sentence)
                if match is None or claim_type in found_for_sentence:
                    continue
                found_for_sentence.add(claim_type)
                candidates.append(
                    (
                        sentence_start + match.start(),
                        sentence_start,
                        sentence_end,
                        claim_type,
                        match,
                        match.start(),
                        match.end(),
                    )
                )
            quantified = _has_quantified_environmental_claim(sentence)
            if quantified is not None and ClaimType.QUANTIFIED_CLIMATE not in found_for_sentence:
                candidates.append(
                    (
                        sentence_start + quantified.start(),
                        sentence_start,
                        sentence_end,
                        ClaimType.QUANTIFIED_CLIMATE,
                        quantified,
                        quantified.start(),
                        quantified.end(),
                    )
                )

        candidates.sort(key=lambda item: (item[0], priority.get(item[3], len(priority))))
        claims: list[DetectedClaim] = []
        for index, (_, sentence_start, sentence_end, claim_type, match, local_start, local_end) in enumerate(candidates, start=1):
            sentence = text[sentence_start:sentence_end]
            affirmative, negation = _claim_is_asserted(sentence, local_start)
            number_value, number_unit = _number_in_sentence(sentence)
            certification = (
                _certification_facts(sentence)
                if claim_type == ClaimType.CERTIFICATION
                else {}
            )
            trigger = match.group(0)
            trigger_start = sentence_start + local_start
            trigger_end = sentence_start + local_end
            claims.append(
                DetectedClaim(
                    claim_id=f"CLM-{index:03d}",
                    claim_text=sentence,
                    trigger_text=trigger,
                    claim_type=claim_type,
                    start_offset=sentence_start,
                    end_offset=sentence_end,
                    trigger_start_offset=trigger_start,
                    trigger_end_offset=trigger_end,
                    affirmative=affirmative,
                    negation_cue=negation,
                    has_offsetting_signal=bool(OFFSET_SIGNAL.search(sentence)),
                    has_specific_qualifier=bool(SPECIFIC_DETAIL.search(sentence)),
                    numeric_value=number_value if claim_type == ClaimType.QUANTIFIED_CLIMATE else None,
                    numeric_unit=number_unit if claim_type == ClaimType.QUANTIFIED_CLIMATE else None,
                    certification_scheme=certification.get("certification_scheme"),
                    certification_reference=certification.get("certification_reference"),
                    certification_body=certification.get("certification_body"),
                )
            )
        return claims
