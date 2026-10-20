"""Libération différée des objets d'import, et registre qui en garde la preuve.

Deux règles commandent ce module:

1. aucun octet n'est détruit avant le commit qui enregistre la transition l'autorisant.
   Le service pose donc le marqueur de purge **dans la même transaction** que cette
   transition et que son événement d'audit, puis met en file le geste réel;
2. la file est vidée par un hook ``after_commit`` de la Session. Un krach entre les deux
   laisse le marqueur en base: l'objet est encore là, la passe de reprise le libère, et
   rien n'a jamais été annoncé comme supprimé à tort.

Le module n'écrit jamais en base depuis le hook: une écriture post-commit ouvrirait une
deuxième transaction par requête, hors du contrat « un commit par requête » posé par
``core.database.get_db``. La fermeture du registre est faite par la passe de reprise,
qui relit réellement le stockage.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterator
from uuid import UUID

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.documents.storage import ObjectStorage, PrefixDeletionResult, validate_tenant_scoped_prefix

if TYPE_CHECKING:  # pragma: no cover - typage only
    from app.core.config import Settings
    from app.models.domain import DocumentUpload

logger = logging.getLogger("vericlaim.documents.purge")

QUEUE_KEY = "vericlaim_document_purges"
DEFER_KEY = "vericlaim_document_purge_defer"
ADAPTERS_KEY = "vericlaim_document_purge_adapters"

QUARANTINE_PURGE_KIND = "quarantine_prefix"


@dataclass(frozen=True)
class PendingPurge:
    """Un geste sur le stockage, autorisé par un commit déjà écrit."""

    bucket: str
    prefix: str
    organization_id: UUID
    upload_id: UUID
    reason: str


def quarantine_prefix(*, organization_id: UUID, document_id: UUID, upload_id: UUID) -> str:
    """Le préfixe complet d'un import: source, et toute pièce détachée du même upload.

    On libère le préfixe et non la seule clé ``source``: un POST présigné multipart
    peut avoir laissé des fragments à côté, et un objet orphelin non listé par le
    service est exactement ce que la politique de rétention ne peut pas pardonner.
    """

    return f"quarantine/{organization_id}/{document_id}/{upload_id}/"


def pending_purges(db: Session) -> list[PendingPurge]:
    return list(db.info.get(QUEUE_KEY, ()))


def pending_purge_count(db: Session) -> int:
    return len(db.info.get(QUEUE_KEY, ()))


def trim_pending_purges(db: Session, keep: int) -> None:
    """Jette les gestes mis en file dans un savepoint qui vient d'etre annule.

    Un savepoint annule n'a rien autorise: les liberations qui y ont ete mises en file
    doivent disparaitre avec lui, alors que celles des lignes precedentes restent dues.
    """

    queue: list[PendingPurge] = list(db.info.get(QUEUE_KEY, ()))
    db.info[QUEUE_KEY] = queue[: max(0, keep)]


@contextmanager
def deferred_purge_drain(db: Session) -> Iterator[None]:
    """Empeche le hook `after_commit` de vider la file pendant un commit partiel.

    SQLAlchemy dispatche `after_commit` y compris a la liberation d'un savepoint: sans
    ce garde-fou, une passe qui traite ligne par ligne detruirait les octets de la
    premiere ligne avant le commit qui rend toute la batch durable. La file est donc
    laissee intacte pendant la boucle, et vidée au commit reel.
    """

    db.info[DEFER_KEY] = int(db.info.get(DEFER_KEY, 0)) + 1
    try:
        yield
    finally:
        db.info[DEFER_KEY] = int(db.info.get(DEFER_KEY, 0)) - 1


def schedule_upload_purge(
    db: Session,
    *,
    storage: ObjectStorage,
    settings: "Settings",
    upload: "DocumentUpload",
    organization_id: UUID,
    reason: str,
    requested_at,
) -> str:
    """Pose le marqueur transactionnel et met la libération en file. Retourne l'état publié.

    L'appelant reste seul maître du commit: ce qui est durable ici, c'est le marqueur,
    pas la suppression.
    """

    prefix = quarantine_prefix(
        organization_id=organization_id,
        document_id=upload.document_id,
        upload_id=upload.id,
    )
    # La garde de préfixe est vérifiée ici aussi: un préfixe mal construit doit échouer
    # avant l'écriture du marqueur, jamais après un commit qui ne pourra plus être annulé.
    validate_tenant_scoped_prefix(prefix)
    upload.purge_required = True
    upload.purge_kind = QUARANTINE_PURGE_KIND
    upload.purge_reason = reason
    upload.purge_requested_at = requested_at
    db.info.setdefault(ADAPTERS_KEY, (storage, settings))
    queue: list[PendingPurge] = db.info.setdefault(QUEUE_KEY, [])
    queue.append(
        PendingPurge(
            bucket=settings.document_quarantine_bucket,
            prefix=prefix,
            organization_id=organization_id,
            upload_id=upload.id,
            reason=reason,
        )
    )
    return "scheduled"


def release_prefix(storage: ObjectStorage, *, bucket: str, prefix: str) -> PrefixDeletionResult:
    """Libère un préfixe et rend le résultat brut, sans jamais l'embellir."""

    return storage.delete_prefix(bucket=bucket, prefix=prefix)


def drain_pending_purges(db: Session) -> int:
    """Vide la file après commit. Les échecs sont journalisés, jamais maquillés.

    Le marqueur reste posé: c'est lui, et non ce hook, qui décide si la libération est
    à reprendre. Aucune exception ne remonte dans le chemin de réponse - la transition
    est déjà durable à ce point, et un 500 après commit ferait croire au client que
    l'import a échoué.
    """

    if db.info.get(DEFER_KEY):
        # Commit intermediaire (savepoint): rien n'est encore durable, on ne touche a rien.
        return 0
    queue: list[PendingPurge] = list(db.info.get(QUEUE_KEY, ()))
    db.info[QUEUE_KEY] = []
    if not queue:
        return 0
    adapters = db.info.get(ADAPTERS_KEY)
    if adapters is None:  # pragma: no cover - ne peut arriver qu'en cas de faute de câblage
        logger.error("Document purge queued without storage adapters; retries will resolve it")
        return 0
    storage, _settings = adapters
    released = 0
    for action in queue:
        try:
            result = release_prefix(storage, bucket=action.bucket, prefix=action.prefix)
        except Exception:
            logger.warning(
                "Unable to release document objects after commit; purge ledger stays open",
                extra={"organization_id": str(action.organization_id), "upload_id": str(action.upload_id)},
                exc_info=True,
            )
            continue
        if result.fully_released:
            released += 1
            logger.info(
                "Document objects released after commit",
                extra={
                    "organization_id": str(action.organization_id),
                    "upload_id": str(action.upload_id),
                    "purge_state": result.state,
                    "released_count": len(result.deleted_keys),
                },
            )
        else:
            logger.warning(
                "Document objects retained after a failed release; purge ledger stays open",
                extra={
                    "organization_id": str(action.organization_id),
                    "upload_id": str(action.upload_id),
                    "purge_state": result.state,
                    "purge_error": result.error_code,
                    "retained_count": len(result.retained_keys),
                },
            )
    return released


@event.listens_for(Session, "after_commit")
def _drain_after_commit(session: Session) -> None:  # pragma: no cover - hook SQLAlchemy
    if not session.info.get(QUEUE_KEY):
        return
    drain_pending_purges(session)


@event.listens_for(Session, "after_rollback")
def _drop_after_rollback(session: Session) -> None:  # pragma: no cover - hook SQLAlchemy
    # Une transaction annulée n'a jamais autorisé une suppression: la file est jetée,
    # le marqueur n'existe pas non plus, et rien n'est détruit.
    if session.in_transaction():
        # Roulade d'un savepoint: la boucle appelante ajuste elle-meme la file.
        return
    session.info[QUEUE_KEY] = []
