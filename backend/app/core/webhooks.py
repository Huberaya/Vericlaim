from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import Webhook

logger = logging.getLogger(__name__)


def generate_webhook_secret() -> str:
    """Génère un secret partagé sécurisé pour la signature HMAC des webhooks."""
    return f"whsec_{secrets.token_hex(24)}"


def sign_webhook_payload(secret: str, timestamp: int, payload_json_str: str) -> str:
    """Calcule la signature HMAC-SHA256 au format standard t={timestamp},v1={hash}."""
    mac = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.{payload_json_str}".encode("utf-8"),
        hashlib.sha256,
    )
    return mac.hexdigest()


def build_signature_header(secret: str, payload_json_str: str) -> tuple[str, int]:
    """Construit l'en-tête de signature X-VeriClaim-Signature avec horodatage Unix."""
    now_ts = int(time.time())
    sig = sign_webhook_payload(secret, now_ts, payload_json_str)
    return f"t={now_ts},v1={sig}", now_ts


def verify_webhook_signature(
    secret: str,
    signature_header: str,
    payload_json_str: str,
    tolerance_sec: int = 300,
) -> bool:
    """Valide l'authenticité et l'anti-rejeu d'un appel webhook entrant."""
    try:
        parts = dict(item.split("=", 1) for item in signature_header.split(","))
        ts_str = parts.get("t")
        received_sig = parts.get("v1")
        if not ts_str or not received_sig:
            return False

        ts = int(ts_str)
        if abs(time.time() - ts) > tolerance_sec:
            return False  # Replay attack prevention

        expected_sig = sign_webhook_payload(secret, ts, payload_json_str)
        return hmac.compare_digest(received_sig, expected_sig)
    except Exception:
        return False


def dispatch_webhook_event(
    db: Session,
    organization_id: str,
    event_type: str,
    data: dict[str, Any],
) -> int:
    """
    Diffuse un événement réglementaire à l'ensemble des webhooks actifs
    enregistrés pour l'organisation appelante.
    """
    webhooks = list(
        db.scalars(
            select(Webhook).where(
                Webhook.organization_id == organization_id,
                Webhook.is_active == True,
            )
        ).all()
    )

    eligible = [
        wh for wh in webhooks
        if "*" in wh.events or event_type in wh.events
    ]

    if not eligible:
        return 0

    envelope = {
        "event": event_type,
        "organization_id": organization_id,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }
    body_str = json.dumps(envelope, ensure_ascii=False, sort_keys=True)

    dispatched = 0
    now = datetime.now(timezone.utc)
    for wh in eligible:
        sig_header, ts = build_signature_header(wh.secret, body_str)
        headers = {
            "Content-Type": "application/json",
            "X-VeriClaim-Event": event_type,
            "X-VeriClaim-Timestamp": str(ts),
            "X-VeriClaim-Signature": sig_header,
            "User-Agent": "VeriClaim-Webhooks/1.0",
        }
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.post(wh.url, content=body_str.encode("utf-8"), headers=headers)
                logger.info("Webhook %s reçu par %s avec statut %s", wh.id, wh.url, resp.status_code)
                wh.last_triggered_at_utc = now
                dispatched += 1
        except Exception as exc:
            logger.warning("Échec de distribution du webhook %s (%s): %s", wh.id, wh.url, exc)

    try:
        db.commit()
    except Exception:
        pass

    return dispatched
