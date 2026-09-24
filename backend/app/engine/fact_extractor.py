"""Deterministic extraction of environmental assertions from supplied copy.

Regular expressions are used only as lexical sensors. They feed typed claim
facts into the rule book, temporal/scope checks and evidence validators; no
LLM, fuzzy score or inferred legal conclusion is used here.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from app.models.legal_types import ClaimType, DetectedClaim


# The lexicon is intentionally explicit and versionable. A lexical hit is a
# candidate assertion, not a final legal qualification.
CLAIM_PATTERNS: tuple[tuple[ClaimType, re.Pattern[str]], ...] = (
    (
        ClaimType.BIODEGRADABLE,
        re.compile(r"\b(?:bio)?d[eé]gradable(?:s)?\b", re.IGNORECASE),
    ),
    (
        ClaimType.NATURE_FRIENDLY,
        re.compile(
            r"\b(?:respectueux|respectueuse|respectueux|respectueuses)\s+de\s+l['’]environnement\b"
            r"|\b(?:ami|amie)s?\s+de\s+la\s+nature\b"
            r"|\b(?:bon|bonne|favorable)\s+(?:pour|à)\s+(?:l['’]environnement|la\s+plan[eè]te|la\s+nature)\b"
            r"|\benvironmentally[- ]friendly\b"
            r"|\bnature[- ]friendly\b"
            r"|\bgentle\s+on\s+the\s+environment\b",
            re.IGNORECASE,
        ),
    ),
    (
        ClaimType.CARBON_NEUTRALITY,
        re.compile(
            r"\bneutre\s+en\s+carbone\b"
            r"|\bneutralit[eé]\s+carbone\b"
            r"|\bz[eé]ro[- ]carbone\b"
            r"|\bempreinte\s+carbone\s+(?:nulle|z[eé]ro)\b"
            r"|\bclimatiquement\s+neutre\b"
            r"|\bneutre\s+pour\s+le\s+climat\b"
            r"|\bimpact\s+climatique\s+(?:neutre|r[eé]duit|positif|n[eé]gatif)\b"
            r"|\b(?:carbon|climate)[- ](?:neutral|net[- ]zero|positive|negative|compensated)\b"
            r"|\bnet[- ]zero\b"
            r"|\bCO\s?2[- ]neutral\b"
            r"|\bcarbon\s+neutral\b"
            r"|\b(?:z[eé]ro|aucun)\s+impact\s+(?:carbone|climatique)\b",
            re.IGNORECASE,
        ),
    ),
    (
        ClaimType.COMPARATIVE,
        re.compile(
            r"\b\d+(?:[.,]\d+)?\s*(?:fois|x|times)\s+moins\s+(?:polluant|polluante|polluting|carbon[- ]intensive)\b"
            r"|\b(?:\d+(?:[.,]\d+)?\s?%|\d+(?:[.,]\d+)?\s+fois)\s+(?:moins|de\s+r[eé]duction)\b.{0,70}\b(?:que|vs\.?|versus|compar[eé](?:e|s|es)?\s+[àa])\b"
            r"|\b(?:moins|plus)\s+(?:polluant(?:e|s|es)?|d['’]?[eé]missions|de\s+CO\s?2|carbon[- ]intensive)\b.{0,70}\b(?:que|vs\.?|versus|compared\s+(?:to|with)|than)\b"
            r"|\b(?:twice|\d+\s?x)\s+less\s+(?:polluting|pollutant|carbon[- ]intensive)\b"
            r"|\b(?:lower|less|reduced)\b.{0,60}\b(?:CO\s?2|carbon|emissions|environmental\s+impact)\b.{0,40}\b(?:than|vs\.?|versus)\b",
            re.IGNORECASE,
        ),
    ),
    (
        ClaimType.RECYCLABLE,
        re.compile(r"\b(?:recyclable|recyclables|recyclability)\b", re.IGNORECASE),
    ),
    (
        ClaimType.GENERIC_ENVIRONMENTAL,
        re.compile(
            r"\b(?:[eé]cologique(?:s)?|[eé]co[- ]?(?:con[cç]u(?:e|s|es)?|responsable|friendly)|[eé]co)\b"
            r"|\bvert(?:e|s|es)?\b"
            r"|\bnaturel(?:le|s|les)?\b"
            r"|\bgreen\b|\bnatural\b|\beco[- ]friendly\b|\beco\b"
            r"|\bsustainable\b|\bclimate[- ]friendly\b|\bcarbon[- ]friendly\b"
            r"|\bconscious\b|\bresponsible\b|\bnature['’]s\s+friend\b",
            re.IGNORECASE,
        ),
    ),
)

OFFSET_SIGNAL = re.compile(
    r"\b(?:compens[eé](?:e|s|es)?|compensation|cr[eé]dits?\s+carbone|quotas?\s+(?:carbone|d['’]?[eé]mission)|offset(?:ting)?|carbon\s+credits?|verified\s+carbon\s+standard|VCS|Gold\s+Standard)\b",
    re.IGNORECASE,
)

ENVIRONMENTAL_METRIC = re.compile(
    r"\b(?:CO\s?2|carbone|[eé]missions?|empreinte|climat(?:ique)?|greenhouse\s+gas|GHG|carbon\s+footprint)\b",
    re.IGNORECASE,
)
NUMBER = re.compile(r"(?<!\w)([-−]?\d+(?:[.,]\d+)?)\s*(%|kg|g|t|tonnes?|kg\s?CO\s?2|g\s?CO\s?2)?", re.IGNORECASE)
OFFSET_NEGATION = re.compile(
    r"(?:\b(?:ne\s+\w+(?:\s+\w+){0,3}\s+pas|n['’]est\s+pas|pas|non|sans)\s*|\b(?:not|never|without|no\s+longer)\s*|\bnon[- ])$",
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
                )
            )
        return claims
