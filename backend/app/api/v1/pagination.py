"""Shared opaque page cursors for the tenant list endpoints.

The catalogue already pages with a ``cursor``/``limit`` pair instead of an
offset: a stable boundary under concurrent inserts, and no way for a client to
enumerate by position. Document listing must page the same way rather than
introduce a second, incompatible contract on top of the same resources, so the
codec lives here once instead of being copied per router.
"""

from __future__ import annotations

import base64
import json
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status


def encode_page_cursor(sort_key: str | None, resource_id: UUID | None) -> str | None:
    if sort_key is None or resource_id is None:
        return None
    raw = json.dumps(
        {"sort": sort_key, "id": str(resource_id)},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_page_cursor(
    cursor: str | None,
    *,
    invalid_detail: str = "Le curseur de page est invalide.",
) -> tuple[str | None, UUID | None]:
    """Decode an opaque page cursor, refusing a malformed one as a 422.

    ``invalid_detail`` keeps the message each endpoint already returns, so the
    shared codec cannot silently rewrite a documented client contract.
    """
    if cursor is None:
        return None, None
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode((cursor + padding).encode("ascii"))
        payload: Any = json.loads(raw.decode("utf-8"))
        sort_key = payload.get("sort") if isinstance(payload, dict) else None
        resource_id = UUID(str(payload.get("id"))) if isinstance(payload, dict) else None
        if not isinstance(sort_key, str) or not sort_key:
            raise ValueError("sort absent")
        return sort_key, resource_id
    except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=invalid_detail) from exc
