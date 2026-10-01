"""Bind a generated report to the immutable analysis version it describes.

The problem this module solves is narrow and specific. A PDF that merely *looks*
like a VeriClaim report proves nothing: anyone can produce one, and a client can
edit the numbers. After C6 a report is rendered from persisted rows, which
answers "does this report describe a real analysis?" — but not "was this file
produced by VeriClaim, and has the analysis behind it changed since?".

So a signature is computed at generation time over the identifiers that make the
analysis unique, and a public verification reference is returned on the document.
Verifying recomputes the HMAC **and** re-reads the stored version, so a report
fails verification if either the signature does not match or the analysis has
moved since signing.

What this does NOT prove, and the document says so:

* not that the analysis is legally correct — the Rule Book is not the law;
* not that the file was delivered unmodified — that is what the file hash is for,
  and the caller must supply it;
* not non-repudiation against VeriClaim itself: the key lives on our servers, so
  we could sign anything. A third-party timestamp or certificate would be needed
  for that, and is out of scope here.
"""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.core.database import canonical_event_occurred_at, canonical_json

SIGNATURE_SCHEMA_VERSION = "vericlaim-report-signature-v1"


def key_id(signing_key: str) -> str:
    """A non-secret fingerprint of the signing key, to identify which key signed."""
    return hashlib.sha256(signing_key.encode("utf-8")).hexdigest()[:16]


def build_signature_material(
    *,
    analysis_version_id: str,
    result_sha256: str,
    rulebook_version: str,
    engine_version: str,
    generated_at_utc: str,
    verification_reference: str,
) -> dict[str, Any]:
    """Material covered by the signature, with time canonicalised.

    ``generated_at_utc`` must already be canonical (see
    :func:`canonical_signature_timestamp`). Signing an uncanonicalised timestamp
    makes every verification fail: a timezone-aware datetime written to the
    database comes back naive, so the recomputed string differs from the signed
    one while the instant is identical. This is the same class of defect the audit
    chain had, and it was caught here by verifying a genuine report rather than by
    trusting the code.
    """
    """The exact material covered by the signature.

    Every field is included deliberately. ``rulebook_version`` is in because a
    verdict means nothing without the rule set that produced it; ``generated_at``
    because two reports of the same analysis are still distinct artefacts.
    """
    return {
        "schema_version": SIGNATURE_SCHEMA_VERSION,
        "analysis_version_id": analysis_version_id,
        "result_sha256": result_sha256,
        "rulebook_version": rulebook_version,
        "engine_version": engine_version,
        "generated_at_utc": generated_at_utc,
        "verification_reference": verification_reference,
    }


def as_utc(value: datetime | None) -> datetime | None:
    """Render a stored timestamp as explicitly UTC.

    Timestamps come back naive from SQLite and aware from PostgreSQL. A public
    verification response that says "generated_at": "2026-09-30T12:59:23" without
    an offset cannot be dated by the caller, and is wrong by the reader's local
    offset if they assume otherwise.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def canonical_signature_timestamp(value: datetime) -> str:
    """The single canonical rendering used on both the signing and verify paths."""
    return canonical_event_occurred_at(value)


def sign_material(material: dict[str, Any], *, signing_key: str) -> str:
    return hmac.new(
        signing_key.encode("utf-8"),
        canonical_json(material).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def signatures_match(expected: str | None, provided: str | None) -> bool:
    if not expected or not provided:
        return False
    return hmac.compare_digest(expected, provided)


@dataclass(frozen=True)
class ReportSignature:
    reference: str
    signature: str
    key_id: str
    material: dict[str, Any]

    @property
    def short_signature(self) -> str:
        return f"{self.signature[:32]}...{self.signature[-8:]}"


def new_verification_reference() -> str:
    """An unguessable public handle.

    Deliberately not the analysis UUID: the verification endpoint is public, and
    exposing an internal identifier would leak that an organisation exists and is
    running analyses. A random handle leaks nothing when guessed wrong.
    """
    import secrets

    return "vc-" + secrets.token_urlsafe(24)


def file_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
