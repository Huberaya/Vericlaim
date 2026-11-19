"""Transactional e-mail for the self-service journey (chantier C14).

What this module refuses to do
------------------------------
It never reports a message as sent unless a transport really accepted it. An
instance with `EMAIL_BACKEND=outbox` records the message in `email_messages` with
status ``not_configured``; the API still answers truthfully to the caller (the
signup is created), and the operator can see, in the database, exactly which
messages were queued and never delivered. That distinction is the whole point:
the audit found an invitation flow that silently dropped its e-mail, and a silent
drop is what this design makes visible.

The SMTP transport is written but **cannot be exercised in this environment** (no
credentials, no relay). It is therefore reported as unverified in the C14
evidence, and the tests cover the outbox path plus the refusal paths.
"""

from __future__ import annotations

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage as StdlibEmailMessage
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.domain import EmailMessage, EmailMessageStatus

EMAIL_TRANSPORT_OUTBOX = "outbox"
EMAIL_TRANSPORT_SMTP = "smtp"
EMAIL_TRANSPORT_DISABLED = "disabled"


@dataclass(frozen=True)
class OutboundEmail:
    recipient: str
    subject: str
    body_text: str
    purpose: str
    organization_id: UUID | None = None


@dataclass(frozen=True)
class DeliveryResult:
    message_id: UUID
    status: EmailMessageStatus
    transport: str
    detail: str


def _smtp_send(settings: Settings, message: OutboundEmail) -> str:
    """Hand the message to a real SMTP relay. Raises on failure, never swallows.

    Returns the provider message id when the relay supplies one (it usually does
    not for SMTP); otherwise a deterministic local identifier so a support ticket
    can reference the attempt.
    """
    if not settings.smtp_host:
        raise RuntimeError("smtp_not_configured")

    payload = StdlibEmailMessage()
    payload["From"] = f"{settings.email_from_name} <{settings.email_from_address}>"
    payload["To"] = message.recipient
    payload["Subject"] = message.subject
    payload.set_content(message.body_text)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as client:
        if settings.smtp_use_starttls:
            client.starttls()
        if settings.smtp_username and settings.smtp_password:
            client.login(settings.smtp_username, settings.smtp_password)
        client.send_message(payload)
    return f"smtp:{settings.smtp_host}"


def deliver(db: Session, *, settings: Settings, message: OutboundEmail) -> DeliveryResult:
    """Record the message, then attempt delivery according to the configured backend.

    The row is written *before* the transport is called, so a crash during
    delivery still leaves a trace of the intent.
    """
    record = EmailMessage(
        organization_id=message.organization_id,
        recipient_email=message.recipient,
        subject=message.subject,
        body_text=message.body_text,
        purpose=message.purpose,
        status=EmailMessageStatus.QUEUED,
        attempt_count=0,
    )
    db.add(record)
    db.flush()

    backend = (settings.email_backend or EMAIL_TRANSPORT_OUTBOX).lower()
    record.attempt_count += 1

    if backend == EMAIL_TRANSPORT_DISABLED:
        record.status = EmailMessageStatus.NOT_CONFIGURED
        record.transport = EMAIL_TRANSPORT_DISABLED
        record.error_code = "email_backend_disabled"
        db.flush()
        return DeliveryResult(
            message_id=record.id,
            status=record.status,
            transport=EMAIL_TRANSPORT_DISABLED,
            detail=(
                "Aucun transport configuré (EMAIL_BACKEND=disabled) : le message est enregistré "
                "mais n'a pas été remis."
            ),
        )

    if backend == EMAIL_TRANSPORT_OUTBOX:
        # Development/test: the message exists, the operator can read it, and the
        # product does not claim a delivery that did not happen.
        record.status = EmailMessageStatus.NOT_CONFIGURED
        record.transport = EMAIL_TRANSPORT_OUTBOX
        record.error_code = "no_transport_configured"
        db.flush()
        return DeliveryResult(
            message_id=record.id,
            status=record.status,
            transport=EMAIL_TRANSPORT_OUTBOX,
            detail=(
                "Transport « outbox » : le message est enregistré dans email_messages et n'a pas "
                "été remis. Configurer EMAIL_BACKEND=smtp pour une remise réelle."
            ),
        )

    try:
        provider_id = _smtp_send(settings, message)
    except Exception as error:  # noqa: BLE001 — the failure must be recorded, not raised blindly
        record.status = EmailMessageStatus.FAILED
        record.transport = EMAIL_TRANSPORT_SMTP
        record.error_code = type(error).__name__[:64]
        db.flush()
        return DeliveryResult(
            message_id=record.id,
            status=record.status,
            transport=EMAIL_TRANSPORT_SMTP,
            detail=f"Échec de remise SMTP ({type(error).__name__}).",
        )

    record.status = EmailMessageStatus.SENT
    record.transport = EMAIL_TRANSPORT_SMTP
    record.provider_message_id = provider_id
    record.sent_at = _now()
    db.flush()
    return DeliveryResult(
        message_id=record.id,
        status=record.status,
        transport=EMAIL_TRANSPORT_SMTP,
        detail="Message remis au relais SMTP.",
    )


def _now():
    from app.identity.service import utcnow

    return utcnow()


def outbox_for(db: Session, *, recipient: str) -> list[EmailMessage]:
    """Messages recorded for a recipient, newest first. Used by tests and support."""
    return list(
        db.scalars(
            select(EmailMessage)
            .where(EmailMessage.recipient_email == recipient)
            .order_by(EmailMessage.created_at.desc())
        ).all()
    )
