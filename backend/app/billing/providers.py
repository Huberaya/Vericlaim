"""C13 — adaptateurs de prestataire de paiement.

Un seul module, deux adaptateurs, une différence assumée :

* :class:`LocalProvider` — prestataire de **développement**. Aucune carte n'est
  demandée, aucun euro n'est encaissé. Il signe ses événements par HMAC-SHA-256
  exactement comme un vrai prestataire, et il est **refusé hors de
  development/test** (voir ``app.core.config``). Il sert à prouver le cycle
  complet, pas à prétendre qu'un paiement a eu lieu.
* :class:`StripeProvider` — vrai prestataire, écrit contre l'API publique de
  Stripe : ``POST /v1/checkout/sessions`` (``mode=subscription``),
  ``POST|DELETE /v1/subscriptions/{id}`` (``cancel_at_period_end``,
  ``items[0][price]``), ``GET /v1/invoices``, et la vérification d'en-tête
  ``Stripe-Signature: t=…,v1=…`` (HMAC-SHA-256 sur ``"{t}.{payload}"``, tolérance
  300 s). **Cet adaptateur n'a jamais parlé à Stripe** : aucun compte, aucune clé,
  aucun accès réseau sortant depuis l'environnement où le chantier a été mené. Il
  est livré inactif (``billing_provider`` vaut ``local`` par défaut et le produit
  publie lequel il utilise).

Deux règles pour les deux adaptateurs :

1. aucun état local n'est modifié par l'appel sortant : c'est l'événement
   (signé, journalisé, appliqué une seule fois) qui fait foi ;
2. une demande dont l'événement n'est pas encore arrivé est retournée comme
   **en attente** (``ProviderRequest(envelope=None)``), jamais comme appliquée.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Protocol
from uuid import UUID, uuid4

import httpx

from app.core.config import Settings
from app.models.domain import Organization

# Types canoniques, communs à tous les prestataires. Un prestataire traduit ses
# propres noms d'événements vers ce vocabulaire ; le domaine ne connaît qu'eux.
EVENT_CHECKOUT_COMPLETED = "checkout.completed"
EVENT_INVOICE_PAID = "invoice.paid"
EVENT_PAYMENT_FAILED = "payment.failed"
EVENT_SUBSCRIPTION_CANCELED = "subscription.canceled"
EVENT_SUBSCRIPTION_UPDATED = "subscription.updated"

CANONICAL_EVENT_TYPES = (
    EVENT_CHECKOUT_COMPLETED,
    EVENT_INVOICE_PAID,
    EVENT_PAYMENT_FAILED,
    EVENT_SUBSCRIPTION_CANCELED,
    EVENT_SUBSCRIPTION_UPDATED,
)

SIGNATURE_TOLERANCE_SECONDS = 300


class ProviderError(RuntimeError):
    """Le prestataire n'a pas pu traiter la demande."""


class ProviderNotConfiguredError(ProviderError):
    """Le prestataire est inutilisable en l'état (clé absente, encaissement coupé)."""


class ProviderSignatureError(ProviderError):
    """Signature absente, invalide ou hors tolérance : l'événement est refusé."""


@dataclass(frozen=True)
class CheckoutRequest:
    organization: Organization
    plan_code: str
    price_id: str | None
    success_url: str
    cancel_url: str
    customer_email: str | None
    trial_days: int = 0


@dataclass(frozen=True)
class CheckoutResult:
    provider_session_id: str
    url: str
    expires_at: datetime


@dataclass(frozen=True)
class ProviderEnvelope:
    """Un événement tel qu'il sera posté sur notre webhook (corps + en-têtes)."""

    provider_event_id: str
    event_type: str
    payload: dict[str, Any]
    body: bytes = field(repr=False, default=b"")
    headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderRequest:
    """Le résultat d'une demande de changement, et son état de synchronisation.

    ``envelope`` est renseigné quand le prestataire a déjà poussé l'événement
    correspondant (prestataire local, synchrone). Quand il est ``None``, la
    demande est partie mais **l'état local n'a pas changé** : c'est au webhook du
    prestataire de l'apporter.
    """

    detail: str
    envelope: ProviderEnvelope | None
    state_synced: bool


class PaymentProvider(Protocol):
    code: str

    def is_configured(self) -> bool: ...

    def create_checkout(self, request: CheckoutRequest) -> CheckoutResult: ...

    def cancel(self, *, provider_subscription_id: str, at_period_end: bool) -> ProviderRequest: ...

    def resume(self, *, provider_subscription_id: str) -> ProviderRequest: ...

    def change_plan(
        self, *, provider_subscription_id: str, plan_code: str, price_id: str | None, effective: str
    ) -> ProviderRequest: ...

    def verify_event(self, *, body: bytes, headers: Mapping[str, str]) -> ProviderEnvelope: ...


# ---------------------------------------------------------------------------
# Vérification de signature, partagée par les deux adaptateurs
# ---------------------------------------------------------------------------


def parse_signature_header(header: str | None) -> tuple[int, list[str]]:
    """Décompose un en-tête ``t=<unix>,v1=<hex>[,v1=<hex>]``."""

    if not header:
        raise ProviderSignatureError("En-tête de signature absent.")
    timestamp: int | None = None
    signatures: list[str] = []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            try:
                timestamp = int(value)
            except ValueError as exc:
                raise ProviderSignatureError("Horodatage de signature illisible.") from exc
        elif key in {"v1", "v0"}:
            signatures.append(value)
    if timestamp is None or not signatures:
        raise ProviderSignatureError("En-tête de signature incomplet.")
    return timestamp, signatures


def verify_hmac_signature(
    *,
    secret: str,
    body: bytes,
    header: str | None,
    tolerance_seconds: int = SIGNATURE_TOLERANCE_SECONDS,
    now: datetime | None = None,
) -> None:
    """Vérifie une signature HMAC-SHA-256 sur ``"{timestamp}.{corps}"``.

    Le corps brut est exigé, jamais un objet re-sérialisé : re-sérialiser change
    les espaces et invaliderait la signature — ou pire, la ferait valider sur un
    contenu différent de celui qui a été signé.
    """

    timestamp, signatures = parse_signature_header(header)
    moment = now or datetime.now(timezone.utc)
    if abs(moment.timestamp() - timestamp) > tolerance_seconds:
        raise ProviderSignatureError(
            "Signature hors tolérance : l'événement est trop ancien (ou l'horloge du serveur dérive)."
        )
    signed_payload = f"{timestamp}.{body.decode('utf-8')}".encode("utf-8")
    expected = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, candidate) for candidate in signatures):
        raise ProviderSignatureError("Signature invalide.")


def sign_payload(*, secret: str, body: bytes, timestamp: int | None = None) -> str:
    moment = timestamp or int(datetime.now(timezone.utc).timestamp())
    signed_payload = f"{moment}.{body.decode('utf-8')}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    return f"t={moment},v1={digest}"


# ---------------------------------------------------------------------------
# Prestataire local (développement) — aucun encaissement
# ---------------------------------------------------------------------------

LOCAL_WEBHOOK_PATH = "/api/v1/billing/webhook/local"


class LocalProvider:
    """Prestataire de développement : il signe, il ne débite pas."""

    code = "local"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def is_configured(self) -> bool:
        return bool(self._settings.billing_local_webhook_secret)

    def envelope(
        self, *, event_type: str, payload: dict[str, Any], event_id: str | None = None
    ) -> ProviderEnvelope:
        identifier = event_id or f"evt_local_{uuid4().hex}"
        document = {
            "id": identifier,
            "type": event_type,
            "created": int(datetime.now(timezone.utc).timestamp()),
            "data": {"object": payload},
        }
        body = json.dumps(document, ensure_ascii=False, sort_keys=True).encode("utf-8")
        headers = {
            "content-type": "application/json",
            "x-vericlaim-signature": sign_payload(
                secret=self._settings.billing_local_webhook_secret, body=body
            ),
        }
        # ``payload`` n'est pas ré-enveloppé : c'est le même dictionnaire que celui
        # reçu du prestataire (et que ``verify_event`` renvoie). L'enveloppe
        # ``{"data": {"object": …}}`` n'existe que sur le fil, dans ``body``.
        return ProviderEnvelope(
            provider_event_id=identifier,
            event_type=event_type,
            payload=payload,
            body=body,
            headers=headers,
        )

    def create_checkout(self, request: CheckoutRequest) -> CheckoutResult:
        session_id = f"cs_local_{uuid4().hex}"
        # Le prestataire local ne peut pas encaisser : l'URL mène à une page du
        # produit qui dit exactement cela et n'accepte aucune carte.
        url = f"{self._settings.frontend_url.rstrip('/')}/billing/checkout/{session_id}"
        return CheckoutResult(
            provider_session_id=session_id,
            url=url,
            expires_at=datetime.now(timezone.utc)
            + timedelta(seconds=self._settings.billing_checkout_ttl_seconds),
        )

    def subscription_activated(
        self,
        *,
        organization_id: UUID,
        plan_code: str,
        provider_session_id: str,
        provider_subscription_id: str,
        period_start: datetime,
        period_end: datetime,
        trial_end: datetime | None,
        status: str | None = None,
    ) -> ProviderEnvelope:
        payload: dict[str, Any] = {
            "organization_id": str(organization_id),
            "plan_code": plan_code,
            "provider_session_id": provider_session_id,
            "provider_subscription_id": provider_subscription_id,
            "provider_customer_id": f"cus_local_{str(organization_id)[:8]}",
            "period_start": period_start.astimezone(timezone.utc).isoformat(),
            "period_end": period_end.astimezone(timezone.utc).isoformat(),
        }
        if trial_end is not None:
            payload["trial_end"] = trial_end.astimezone(timezone.utc).isoformat()
        if status is not None:
            payload["status"] = status
        return self.envelope(event_type=EVENT_CHECKOUT_COMPLETED, payload=payload)

    def invoice_paid(
        self,
        *,
        organization_id: UUID,
        plan_code: str,
        provider_subscription_id: str,
        amount_cents: int,
        period_start: datetime,
        period_end: datetime,
    ) -> ProviderEnvelope:
        return self.envelope(
            event_type=EVENT_INVOICE_PAID,
            payload={
                "organization_id": str(organization_id),
                "plan_code": plan_code,
                "provider_subscription_id": provider_subscription_id,
                "provider_invoice_id": f"in_local_{uuid4().hex}",
                "amount_cents": amount_cents,
                "currency": "eur",
                "period_start": period_start.astimezone(timezone.utc).isoformat(),
                "period_end": period_end.astimezone(timezone.utc).isoformat(),
            },
        )

    def resume(self, *, provider_subscription_id: str) -> ProviderRequest:
        envelope = self.envelope(
            event_type=EVENT_SUBSCRIPTION_UPDATED,
            payload={
                "provider_subscription_id": provider_subscription_id,
                "cancel_at_period_end": False,
            },
        )
        return ProviderRequest(
            detail="Résiliation annulée : l'abonnement se renouvellera normalement.",
            envelope=envelope,
            state_synced=True,
        )

    def cancel(self, *, provider_subscription_id: str, at_period_end: bool) -> ProviderRequest:
        envelope = self.envelope(
            event_type=EVENT_SUBSCRIPTION_UPDATED if at_period_end else EVENT_SUBSCRIPTION_CANCELED,
            payload={
                "provider_subscription_id": provider_subscription_id,
                "cancel_at_period_end": True if at_period_end else False,
                "status": "canceled" if not at_period_end else None,
            },
        )
        detail = (
            "Résiliation programmée pour la fin de la période payée."
            if at_period_end
            else "Résiliation immédiate demandée au prestataire."
        )
        return ProviderRequest(detail=detail, envelope=envelope, state_synced=True)

    def change_plan(
        self, *, provider_subscription_id: str, plan_code: str, price_id: str | None, effective: str
    ) -> ProviderRequest:
        payload: dict[str, Any] = {"provider_subscription_id": provider_subscription_id}
        if effective == "immediate":
            now = datetime.now(timezone.utc)
            payload["plan_code"] = plan_code
            payload["period_start"] = now.isoformat()
            payload["period_end"] = (now + timedelta(days=30)).isoformat()
        # Une descente de gamme ne doit **pas** porter de changement de plan dans
        # l'événement : l'offre en cours est payée jusqu'à la fin de la période, et
        # c'est le produit qui applique la nouvelle offre au renouvellement
        # (`pending_plan_code`). L'événement ne fait qu'accuser réception.
        envelope = self.envelope(event_type=EVENT_SUBSCRIPTION_UPDATED, payload=payload)
        detail = (
            "Changement de plan appliqué immédiatement par le prestataire (effet immédiat demandé)."
            if effective == "immediate"
            else "Changement de plan transmis au prestataire : il prendra effet à la prochaine échéance."
        )
        return ProviderRequest(detail=detail, envelope=envelope, state_synced=True)

    def verify_event(self, *, body: bytes, headers: Mapping[str, str]) -> ProviderEnvelope:
        header = headers.get("x-vericlaim-signature") or headers.get("X-VeriClaim-Signature")
        verify_hmac_signature(
            secret=self._settings.billing_local_webhook_secret, body=body, header=header
        )
        try:
            document = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderError("Corps d'événement illisible.") from exc
        if not isinstance(document, dict):
            raise ProviderError("Corps d'événement inattendu.")
        event_id = document.get("id")
        event_type = document.get("type")
        data = document.get("data")
        if not isinstance(event_id, str) or not isinstance(event_type, str) or not isinstance(data, dict):
            raise ProviderError("Événement incomplet : identifiant, type et données sont requis.")
        if event_type not in CANONICAL_EVENT_TYPES:
            raise ProviderError(f"Type d'événement non pris en charge : {event_type!r}")
        obj = data.get("object")
        if not isinstance(obj, dict):
            raise ProviderError("Événement sans objet de données.")
        return ProviderEnvelope(
            provider_event_id=event_id,
            event_type=event_type,
            payload=obj,
            body=body,
            headers=dict(headers),
        )


# ---------------------------------------------------------------------------
# Stripe — écrit, jamais exécuté
# ---------------------------------------------------------------------------

STRIPE_EVENT_MAP = {
    "checkout.session.completed": EVENT_CHECKOUT_COMPLETED,
    "invoice.paid": EVENT_INVOICE_PAID,
    "invoice.payment_failed": EVENT_PAYMENT_FAILED,
    "customer.subscription.deleted": EVENT_SUBSCRIPTION_CANCELED,
    "customer.subscription.updated": EVENT_SUBSCRIPTION_UPDATED,
}


class StripeProvider:
    """Adaptateur Stripe. **Aucun appel réel n'a été effectué depuis ce dépôt.**

    Chaque méthode correspond à un point de la documentation publique Stripe. Le
    code est livré pour être relu et exercé contre les clés de test du compte de
    l'entreprise ; il n'est pas présenté comme vérifié.
    """

    code = "stripe"

    def __init__(self, settings: Settings, *, transport: httpx.BaseTransport | None = None) -> None:
        self._settings = settings
        # ``transport`` permet à un test d'enregistrer les requêtes exactes sans
        # réseau. Aucun test de ce dépôt n'appelle api.stripe.com.
        self._transport = transport

    def is_configured(self) -> bool:
        return bool(self._settings.stripe_secret_key and self._settings.stripe_webhook_secret)

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self._settings.stripe_api_base,
            auth=(self._settings.stripe_secret_key or "", ""),
            timeout=httpx.Timeout(15.0),
            transport=self._transport,
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.is_configured():
            raise ProviderNotConfiguredError(
                "Stripe n'est pas configuré : renseignez STRIPE_SECRET_KEY et STRIPE_WEBHOOK_SECRET."
            )
        try:
            with self._client() as client:
                response = client.request(method, path, data=data, params=params)
        except httpx.HTTPError as exc:  # pragma: no cover - dépend du réseau
            raise ProviderError("Prestataire injoignable.") from exc
        if response.status_code >= 400:
            # Le message du prestataire est repris, jamais la clé d'API.
            try:
                error = response.json().get("error", {})
                message = error.get("message") or "erreur inconnue"
            except (ValueError, AttributeError):
                message = "réponse illisible"
            raise ProviderError(f"Stripe a refusé la requête ({response.status_code}) : {message}")
        return response.json()

    def price_id_for(self, plan_code: str) -> str | None:
        try:
            prices = json.loads(self._settings.stripe_price_ids_json or "{}")
        except json.JSONDecodeError:
            return None
        value = prices.get(plan_code) if isinstance(prices, dict) else None
        return value if isinstance(value, str) and value else None

    def plan_code_for_price(self, price_id: str | None) -> str | None:
        if not price_id:
            return None
        try:
            prices = json.loads(self._settings.stripe_price_ids_json or "{}")
        except json.JSONDecodeError:
            return None
        if not isinstance(prices, dict):
            return None
        for plan_code, value in prices.items():
            if value == price_id:
                return plan_code
        return None

    def create_checkout(self, request: CheckoutRequest) -> CheckoutResult:
        if not request.price_id:
            raise ProviderNotConfiguredError(
                f"Aucun tarif Stripe n'est configuré pour l'offre {request.plan_code!r} "
                "(STRIPE_PRICE_IDS_JSON)."
            )
        data: dict[str, Any] = {
            "mode": "subscription",
            "success_url": request.success_url,
            "cancel_url": request.cancel_url,
            "client_reference_id": str(request.organization.id),
            "line_items[0][price]": request.price_id,
            "line_items[0][quantity]": 1,
            "subscription_data[metadata][organization_id]": str(request.organization.id),
            "subscription_data[metadata][plan_code]": request.plan_code,
        }
        if request.customer_email:
            data["customer_email"] = request.customer_email
        if request.trial_days > 0:
            data["subscription_data[trial_period_days]"] = request.trial_days
        document = self._request("POST", "/v1/checkout/sessions", data=data)
        session_id = document.get("id")
        url = document.get("url")
        if not isinstance(session_id, str) or not isinstance(url, str):
            raise ProviderError("Réponse de création de session incomplète.")
        return CheckoutResult(
            provider_session_id=session_id,
            url=url,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=3600),
        )

    def cancel(self, *, provider_subscription_id: str, at_period_end: bool) -> ProviderRequest:
        if at_period_end:
            self._request(
                "POST",
                f"/v1/subscriptions/{provider_subscription_id}",
                data={"cancel_at_period_end": "true"},
            )
            return ProviderRequest(
                detail=(
                    "Demande de résiliation en fin de période transmise à Stripe. Stripe enverra "
                    "customer.subscription.updated, qui fait foi."
                ),
                envelope=None,
                state_synced=False,
            )
        self._request("DELETE", f"/v1/subscriptions/{provider_subscription_id}")
        return ProviderRequest(
            detail=(
                "Résiliation immédiate transmise à Stripe. Stripe enverra "
                "customer.subscription.deleted, qui fait foi."
            ),
            envelope=None,
            state_synced=False,
        )

    def resume(self, *, provider_subscription_id: str) -> ProviderRequest:
        self._request(
            "POST",
            f"/v1/subscriptions/{provider_subscription_id}",
            data={"cancel_at_period_end": "false"},
        )
        return ProviderRequest(
            detail=(
                "Reprise transmise à Stripe. L'état local restera inchangé jusqu'à "
                "customer.subscription.updated."
            ),
            envelope=None,
            state_synced=False,
        )

    def change_plan(
        self, *, provider_subscription_id: str, plan_code: str, price_id: str | None, effective: str
    ) -> ProviderRequest:
        target_price = price_id or self.price_id_for(plan_code)
        if not target_price:
            raise ProviderNotConfiguredError(
                f"Aucun tarif Stripe n'est configuré pour l'offre {plan_code!r}."
            )
        item_id = self._subscription_item_id(provider_subscription_id)
        data: dict[str, Any] = {
            "items[0][id]": item_id,
            "items[0][price]": target_price,
            "proration_behavior": "create_prorations" if effective == "immediate" else "none",
            "metadata[plan_code]": plan_code,
        }
        self._request("POST", f"/v1/subscriptions/{provider_subscription_id}", data=data)
        return ProviderRequest(
            detail=(
                "Changement de plan transmis à Stripe ; la période et le plan seront confirmés par "
                "customer.subscription.updated."
            ),
            envelope=None,
            state_synced=False,
        )

    def _subscription_item_id(self, provider_subscription_id: str) -> str:
        document = self._request("GET", f"/v1/subscriptions/{provider_subscription_id}")
        items = document.get("items", {}).get("data", []) if isinstance(document, dict) else []
        if not items or not isinstance(items[0], dict) or not items[0].get("id"):
            raise ProviderError("Abonnement Stripe sans ligne d'article identifiable.")
        return str(items[0]["id"])

    def list_invoice_documents(self, *, provider_subscription_id: str) -> list[dict[str, Any]]:
        document = self._request(
            "GET", "/v1/invoices", params={"subscription": provider_subscription_id, "limit": 24}
        )
        data = document.get("data") if isinstance(document, dict) else None
        return [item for item in (data or []) if isinstance(item, dict)]

    def verify_event(self, *, body: bytes, headers: Mapping[str, str]) -> ProviderEnvelope:
        header = headers.get("stripe-signature") or headers.get("Stripe-Signature")
        verify_hmac_signature(
            secret=self._settings.stripe_webhook_secret or "", body=body, header=header
        )
        try:
            document = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderError("Corps d'événement illisible.") from exc
        if not isinstance(document, dict):
            raise ProviderError("Corps d'événement inattendu.")
        event_type = document.get("type")
        canonical = STRIPE_EVENT_MAP.get(str(event_type))
        if canonical is None:
            raise ProviderIgnoredEvent(f"Événement Stripe ignoré : {event_type!r}")
        obj = (document.get("data") or {}).get("object") or {}
        if not isinstance(obj, dict):
            raise ProviderError("Événement Stripe sans objet de données.")
        return ProviderEnvelope(
            provider_event_id=str(document.get("id") or ""),
            event_type=canonical,
            payload=self._canonical_payload(canonical=canonical, obj=obj),
            body=body,
            headers=dict(headers),
        )

    def _canonical_payload(self, *, canonical: str, obj: dict[str, Any]) -> dict[str, Any]:
        """Traduit un objet Stripe vers notre vocabulaire, sans rien inventer.

        Un champ absent reste absent : le domaine appliquera alors une transition
        partielle visible (« événement sans changement d'état ») plutôt qu'une
        valeur fabriquée.
        """

        if canonical == EVENT_CHECKOUT_COMPLETED:
            metadata = obj.get("metadata") or {}
            subscription_details = obj.get("subscription_details") or {}
            subscription_metadata = subscription_details.get("metadata") or {}
            payload: dict[str, Any] = {
                "organization_id": obj.get("client_reference_id") or metadata.get("organization_id"),
                "plan_code": subscription_metadata.get("plan_code") or metadata.get("plan_code"),
                "provider_session_id": obj.get("id"),
                "provider_subscription_id": obj.get("subscription"),
                "provider_customer_id": obj.get("customer"),
            }
            return {key: value for key, value in payload.items() if value}

        if canonical == EVENT_SUBSCRIPTION_UPDATED:
            metadata = obj.get("metadata") or {}
            items = (obj.get("items") or {}).get("data") or []
            price = (items[0].get("price") or {}) if items and isinstance(items[0], dict) else {}
            payload = {
                "organization_id": metadata.get("organization_id"),
                "provider_subscription_id": obj.get("id"),
                "plan_code": metadata.get("plan_code") or self.plan_code_for_price(price.get("id")),
                "cancel_at_period_end": bool(obj.get("cancel_at_period_end")),
                "status": obj.get("status"),
                "period_start": _stripe_timestamp(items[0].get("current_period_start"))
                if items and isinstance(items[0], dict)
                else None,
                "period_end": _stripe_timestamp(items[0].get("current_period_end"))
                if items and isinstance(items[0], dict)
                else None,
            }
            return {key: value for key, value in payload.items() if value is not None}

        if canonical == EVENT_SUBSCRIPTION_CANCELED:
            metadata = obj.get("metadata") or {}
            payload = {
                "organization_id": metadata.get("organization_id"),
                "provider_subscription_id": obj.get("id"),
                "status": "canceled",
            }
            return {key: value for key, value in payload.items() if value}

        if canonical == EVENT_INVOICE_PAID:
            lines = (obj.get("lines") or {}).get("data") or []
            line_period = (lines[0].get("period") or {}) if lines and isinstance(lines[0], dict) else {}
            payload = {
                "provider_subscription_id": obj.get("subscription"),
                "provider_invoice_id": obj.get("id"),
                "amount_cents": obj.get("amount_paid"),
                "currency": obj.get("currency"),
                "period_start": _stripe_timestamp(line_period.get("start")),
                "period_end": _stripe_timestamp(line_period.get("end")),
                "hosted_invoice_url": obj.get("hosted_invoice_url"),
            }
            return {key: value for key, value in payload.items() if value is not None}

        payload = {
            "provider_subscription_id": obj.get("subscription") or obj.get("id"),
            "amount_cents": obj.get("amount_due"),
        }
        return {key: value for key, value in payload.items() if value is not None}


class ProviderIgnoredEvent(ProviderError):
    """Événement authentique mais hors périmètre : journalisé, non appliqué."""


def _stripe_timestamp(value: Any) -> str | None:
    if not isinstance(value, int) or value <= 0:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def provider_for(settings: Settings) -> PaymentProvider | None:
    """Le prestataire configuré, ou ``None`` si la vente est fermée."""

    if settings.billing_provider == "local":
        return LocalProvider(settings)
    if settings.billing_provider == "stripe":
        return StripeProvider(settings)
    return None


def provider_by_code(settings: Settings, code: str) -> PaymentProvider | None:
    if code == "local" and settings.billing_provider == "local":
        return LocalProvider(settings)
    if code == "stripe" and settings.billing_provider == "stripe":
        return StripeProvider(settings)
    return None


__all__ = [
    "CANONICAL_EVENT_TYPES",
    "CheckoutRequest",
    "CheckoutResult",
    "EVENT_CHECKOUT_COMPLETED",
    "EVENT_INVOICE_PAID",
    "EVENT_PAYMENT_FAILED",
    "EVENT_SUBSCRIPTION_CANCELED",
    "EVENT_SUBSCRIPTION_UPDATED",
    "LOCAL_WEBHOOK_PATH",
    "LocalProvider",
    "PaymentProvider",
    "ProviderEnvelope",
    "ProviderError",
    "ProviderIgnoredEvent",
    "ProviderNotConfiguredError",
    "ProviderRequest",
    "ProviderSignatureError",
    "StripeProvider",
    "parse_signature_header",
    "provider_by_code",
    "provider_for",
    "sign_payload",
    "verify_hmac_signature",
]
