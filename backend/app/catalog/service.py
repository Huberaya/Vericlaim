"""Transactional, tenant-scoped supplier and product catalog operations.

The catalog deliberately contains no supplier score, enrichment, regulatory
conclusion or external lookup. It establishes durable identities that the
secure document and persistent-analysis workflows can reference.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Generic, TypeVar
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import sha256_json
from app.identity.service import append_audit_event
from app.models.domain import Product, Supplier


class CatalogError(RuntimeError):
    """Base class for public catalog service failures."""


class CatalogNotFoundError(CatalogError):
    pass


class CatalogConflictError(CatalogError):
    pass


class CatalogInputError(CatalogError):
    pass


class CatalogIdempotencyConflictError(CatalogConflictError):
    pass


CatalogResource = TypeVar("CatalogResource", Supplier, Product)


@dataclass(frozen=True)
class CatalogCreateResult(Generic[CatalogResource]):
    resource: CatalogResource
    idempotent_replay: bool


@dataclass(frozen=True)
class CatalogPage(Generic[CatalogResource]):
    items: list[CatalogResource]
    next_sort_key: str | None
    next_id: UUID | None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _require_tenant_context(db: Session, organization_id: UUID) -> None:
    """Catch accidental service use under an already-installed foreign context.

    HTTP dependencies set the context before reaching this module. Standalone
    unit tests and data migrations may have no context, which remains valid for
    SQLite test setup; a *conflicting* context is never accepted.
    """

    current = db.info.get("current_organization_id")
    if current is not None and current != organization_id:
        raise CatalogConflictError(
            "Le contexte organisationnel actif ne correspond pas à l’opération catalogue."
        )


def _normalise_idempotency_key(value: str | None) -> str:
    normalised = (value or "").strip()
    if not normalised:
        raise CatalogInputError(
            "L’en-tête Idempotency-Key est obligatoire pour cette création catalogue."
        )
    if len(normalised) > 128 or any(
        ord(character) < 33 or ord(character) > 126 for character in normalised
    ):
        raise CatalogInputError(
            "Idempotency-Key doit contenir entre 1 et 128 caractères ASCII imprimables sans espace."
        )
    return normalised


def _supplier_statement(
    organization_id: UUID, supplier_id: UUID, *, include_archived: bool = False
):
    statement = select(Supplier).where(
        Supplier.organization_id == organization_id,
        Supplier.id == supplier_id,
    )
    if not include_archived:
        statement = statement.where(Supplier.deleted_at.is_(None))
    return statement


def _product_statement(
    organization_id: UUID, product_id: UUID, *, include_archived: bool = False
):
    statement = select(Product).where(
        Product.organization_id == organization_id,
        Product.id == product_id,
    )
    if not include_archived:
        statement = statement.where(Product.deleted_at.is_(None))
    return statement


def get_supplier(
    db: Session,
    *,
    organization_id: UUID,
    supplier_id: UUID,
    include_archived: bool = False,
    lock: bool = False,
) -> Supplier:
    _require_tenant_context(db, organization_id)
    statement = _supplier_statement(
        organization_id, supplier_id, include_archived=include_archived
    )
    if lock:
        statement = statement.with_for_update()
    supplier = db.scalar(statement)
    if supplier is None:
        raise CatalogNotFoundError("Fournisseur introuvable ou inaccessible.")
    return supplier


def get_product(
    db: Session,
    *,
    organization_id: UUID,
    product_id: UUID,
    include_archived: bool = False,
    lock: bool = False,
) -> Product:
    _require_tenant_context(db, organization_id)
    statement = _product_statement(
        organization_id, product_id, include_archived=include_archived
    )
    if lock:
        statement = statement.with_for_update()
    product = db.scalar(statement)
    if product is None:
        raise CatalogNotFoundError("Produit introuvable ou inaccessible.")
    return product


def _supplier_request_sha256(
    *,
    legal_name: str,
    trading_name: str | None,
    external_reference: str | None,
    country_code: str | None,
    contact_email: str | None,
    metadata: dict[str, Any],
) -> str:
    return sha256_json(
        {
            "operation": "supplier_create",
            "legal_name": legal_name,
            "trading_name": trading_name,
            "external_reference": external_reference,
            "country_code": country_code,
            "contact_email": contact_email,
            "metadata": metadata,
        }
    )


def _product_request_sha256(
    *,
    supplier_id: UUID,
    reference: str,
    name: str,
    category: str | None,
    country_of_sale: str | None,
    lifecycle_status: str,
    metadata: dict[str, Any],
) -> str:
    return sha256_json(
        {
            "operation": "product_create",
            "supplier_id": str(supplier_id),
            "reference": reference,
            "name": name,
            "category": category,
            "country_of_sale": country_of_sale,
            "lifecycle_status": lifecycle_status,
            "metadata": metadata,
        }
    )


def _idempotent_supplier(
    db: Session,
    *,
    organization_id: UUID,
    idempotency_key: str,
    request_sha256: str,
) -> CatalogCreateResult[Supplier] | None:
    supplier = db.scalar(
        select(Supplier).where(
            Supplier.organization_id == organization_id,
            Supplier.idempotency_key == idempotency_key,
        )
    )
    if supplier is None:
        return None
    if supplier.request_sha256 != request_sha256:
        raise CatalogIdempotencyConflictError(
            "Cette Idempotency-Key a déjà été utilisée avec une déclaration de fournisseur différente."
        )
    return CatalogCreateResult(resource=supplier, idempotent_replay=True)


def _idempotent_product(
    db: Session,
    *,
    organization_id: UUID,
    idempotency_key: str,
    request_sha256: str,
) -> CatalogCreateResult[Product] | None:
    product = db.scalar(
        select(Product).where(
            Product.organization_id == organization_id,
            Product.idempotency_key == idempotency_key,
        )
    )
    if product is None:
        return None
    if product.request_sha256 != request_sha256:
        raise CatalogIdempotencyConflictError(
            "Cette Idempotency-Key a déjà été utilisée avec une déclaration de produit différente."
        )
    return CatalogCreateResult(resource=product, idempotent_replay=True)


def create_supplier(
    db: Session,
    *,
    organization_id: UUID,
    actor_user_id: UUID,
    idempotency_key: str | None,
    legal_name: str,
    trading_name: str | None,
    external_reference: str | None,
    country_code: str | None,
    contact_email: str | None,
    metadata: dict[str, Any],
    request_id: str | None = None,
) -> CatalogCreateResult[Supplier]:
    """Create one supplier, or safely return the first result on a replay."""

    _require_tenant_context(db, organization_id)
    key = _normalise_idempotency_key(idempotency_key)
    request_sha256 = _supplier_request_sha256(
        legal_name=legal_name,
        trading_name=trading_name,
        external_reference=external_reference,
        country_code=country_code,
        contact_email=contact_email,
        metadata=metadata,
    )
    replay = _idempotent_supplier(
        db,
        organization_id=organization_id,
        idempotency_key=key,
        request_sha256=request_sha256,
    )
    if replay is not None:
        return replay

    try:
        with db.begin_nested():
            supplier = Supplier(
                id=uuid4(),
                organization_id=organization_id,
                legal_name=legal_name,
                trading_name=trading_name,
                external_reference=external_reference,
                country_code=country_code,
                contact_email=contact_email,
                metadata_json=metadata,
                idempotency_key=key,
                request_sha256=request_sha256,
            )
            db.add(supplier)
            db.flush()
            append_audit_event(
                db,
                organization_id=organization_id,
                actor_user_id=actor_user_id,
                entity_type="supplier",
                entity_id=supplier.id,
                action="catalog.supplier_created",
                payload={
                    "external_reference_present": external_reference is not None,
                    "country_code": country_code,
                    "metadata_key_count": len(metadata),
                    "request_sha256": request_sha256,
                },
                request_id=request_id,
            )
            return CatalogCreateResult(resource=supplier, idempotent_replay=False)
    except IntegrityError as exc:
        replay = _idempotent_supplier(
            db,
            organization_id=organization_id,
            idempotency_key=key,
            request_sha256=request_sha256,
        )
        if replay is not None:
            return replay
        raise CatalogConflictError(
            "Impossible de créer le fournisseur : la référence externe est déjà utilisée dans cette organisation."
        ) from exc


def create_product(
    db: Session,
    *,
    organization_id: UUID,
    actor_user_id: UUID,
    idempotency_key: str | None,
    supplier_id: UUID,
    reference: str,
    name: str,
    category: str | None,
    country_of_sale: str | None,
    lifecycle_status: str,
    metadata: dict[str, Any],
    request_id: str | None = None,
) -> CatalogCreateResult[Product]:
    """Create one product under a live same-tenant supplier, idempotently."""

    _require_tenant_context(db, organization_id)
    key = _normalise_idempotency_key(idempotency_key)
    request_sha256 = _product_request_sha256(
        supplier_id=supplier_id,
        reference=reference,
        name=name,
        category=category,
        country_of_sale=country_of_sale,
        lifecycle_status=lifecycle_status,
        metadata=metadata,
    )
    replay = _idempotent_product(
        db,
        organization_id=organization_id,
        idempotency_key=key,
        request_sha256=request_sha256,
    )
    if replay is not None:
        return replay

    try:
        with db.begin_nested():
            supplier = get_supplier(
                db,
                organization_id=organization_id,
                supplier_id=supplier_id,
                lock=True,
            )
            product = Product(
                id=uuid4(),
                organization_id=organization_id,
                supplier_id=supplier.id,
                reference=reference,
                name=name,
                category=category,
                country_of_sale=country_of_sale,
                lifecycle_status=lifecycle_status,
                metadata_json=metadata,
                idempotency_key=key,
                request_sha256=request_sha256,
            )
            db.add(product)
            db.flush()
            append_audit_event(
                db,
                organization_id=organization_id,
                actor_user_id=actor_user_id,
                entity_type="product",
                entity_id=product.id,
                action="catalog.product_created",
                payload={
                    "supplier_id": str(supplier.id),
                    "reference": reference,
                    "lifecycle_status": lifecycle_status,
                    "metadata_key_count": len(metadata),
                    "request_sha256": request_sha256,
                },
                request_id=request_id,
            )
            return CatalogCreateResult(resource=product, idempotent_replay=False)
    except CatalogNotFoundError:
        # A foreign or archived supplier must remain indistinguishable from a
        # missing one at the public boundary.
        raise
    except IntegrityError as exc:
        replay = _idempotent_product(
            db,
            organization_id=organization_id,
            idempotency_key=key,
            request_sha256=request_sha256,
        )
        if replay is not None:
            return replay
        raise CatalogConflictError(
            "Impossible de créer le produit : sa référence est déjà utilisée dans cette organisation."
        ) from exc


def update_supplier(
    db: Session,
    *,
    organization_id: UUID,
    actor_user_id: UUID,
    supplier_id: UUID,
    changes: dict[str, Any],
    request_id: str | None = None,
) -> Supplier:
    _require_tenant_context(db, organization_id)
    if not changes:
        raise CatalogInputError("Au moins un champ de fournisseur doit être fourni.")
    allowed = {
        "legal_name": "legal_name",
        "trading_name": "trading_name",
        "external_reference": "external_reference",
        "country_code": "country_code",
        "contact_email": "contact_email",
        "metadata": "metadata_json",
    }
    unknown = set(changes).difference(allowed)
    if unknown:
        raise CatalogInputError(
            "Le fournisseur contient un champ de mise à jour non autorisé."
        )
    try:
        with db.begin_nested():
            supplier = get_supplier(
                db,
                organization_id=organization_id,
                supplier_id=supplier_id,
                lock=True,
            )
            changed_fields: list[str] = []
            for api_name, column_name in allowed.items():
                if (
                    api_name in changes
                    and getattr(supplier, column_name) != changes[api_name]
                ):
                    setattr(supplier, column_name, changes[api_name])
                    changed_fields.append(api_name)
            # A replay of the same explicit state must not produce a second
            # audit event nor bump updated_at just because a client retried.
            if not changed_fields:
                return supplier
            db.flush()
            append_audit_event(
                db,
                organization_id=organization_id,
                actor_user_id=actor_user_id,
                entity_type="supplier",
                entity_id=supplier.id,
                action="catalog.supplier_updated",
                payload={"fields": sorted(changed_fields)},
                request_id=request_id,
            )
            return supplier
    except IntegrityError as exc:
        raise CatalogConflictError(
            "Impossible de mettre à jour le fournisseur : la référence externe est déjà utilisée dans cette organisation."
        ) from exc


def update_product(
    db: Session,
    *,
    organization_id: UUID,
    actor_user_id: UUID,
    product_id: UUID,
    changes: dict[str, Any],
    request_id: str | None = None,
) -> Product:
    _require_tenant_context(db, organization_id)
    if not changes:
        raise CatalogInputError("Au moins un champ de produit doit être fourni.")
    # supplier_id is intentionally absent: moving an existing product between
    # suppliers would rewrite the context of historic documents and analyses.
    allowed = {
        "reference": "reference",
        "name": "name",
        "category": "category",
        "country_of_sale": "country_of_sale",
        "lifecycle_status": "lifecycle_status",
        "metadata": "metadata_json",
    }
    unknown = set(changes).difference(allowed)
    if unknown:
        raise CatalogInputError(
            "Le produit contient un champ de mise à jour non autorisé."
        )
    try:
        with db.begin_nested():
            product = get_product(
                db,
                organization_id=organization_id,
                product_id=product_id,
                lock=True,
            )
            changed_fields: list[str] = []
            for api_name, column_name in allowed.items():
                if (
                    api_name in changes
                    and getattr(product, column_name) != changes[api_name]
                ):
                    setattr(product, column_name, changes[api_name])
                    changed_fields.append(api_name)
            if not changed_fields:
                return product
            db.flush()
            append_audit_event(
                db,
                organization_id=organization_id,
                actor_user_id=actor_user_id,
                entity_type="product",
                entity_id=product.id,
                action="catalog.product_updated",
                payload={"fields": sorted(changed_fields)},
                request_id=request_id,
            )
            return product
    except IntegrityError as exc:
        raise CatalogConflictError(
            "Impossible de mettre à jour le produit : sa référence est déjà utilisée dans cette organisation."
        ) from exc


def archive_supplier(
    db: Session,
    *,
    organization_id: UUID,
    actor_user_id: UUID,
    supplier_id: UUID,
    request_id: str | None = None,
) -> Supplier:
    """Soft-archive a supplier only after its active products are archived."""

    _require_tenant_context(db, organization_id)
    supplier = get_supplier(
        db,
        organization_id=organization_id,
        supplier_id=supplier_id,
        include_archived=True,
        lock=True,
    )
    if supplier.deleted_at is not None:
        return supplier
    active_product = db.scalar(
        select(Product.id)
        .where(
            Product.organization_id == organization_id,
            Product.supplier_id == supplier.id,
            Product.deleted_at.is_(None),
        )
        .limit(1)
    )
    if active_product is not None:
        raise CatalogConflictError(
            "Archivez d’abord les produits actifs de ce fournisseur afin de préserver leurs rattachements historiques."
        )
    supplier.deleted_at = utcnow()
    db.flush()
    append_audit_event(
        db,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        entity_type="supplier",
        entity_id=supplier.id,
        action="catalog.supplier_archived",
        payload={},
        request_id=request_id,
    )
    return supplier


def archive_product(
    db: Session,
    *,
    organization_id: UUID,
    actor_user_id: UUID,
    product_id: UUID,
    request_id: str | None = None,
) -> Product:
    """Soft-archive a product without altering historic document/analysis FKs."""

    _require_tenant_context(db, organization_id)
    product = get_product(
        db,
        organization_id=organization_id,
        product_id=product_id,
        include_archived=True,
        lock=True,
    )
    if product.deleted_at is not None:
        return product
    product.deleted_at = utcnow()
    db.flush()
    append_audit_event(
        db,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        entity_type="product",
        entity_id=product.id,
        action="catalog.product_archived",
        payload={"supplier_id": str(product.supplier_id)},
        request_id=request_id,
    )
    return product


def list_suppliers(
    db: Session,
    *,
    organization_id: UUID,
    limit: int,
    after_sort_key: str | None = None,
    after_id: UUID | None = None,
    query: str | None = None,
) -> CatalogPage[Supplier]:
    _require_tenant_context(db, organization_id)
    sort_key = func.lower(Supplier.legal_name)
    statement = select(Supplier).where(
        Supplier.organization_id == organization_id,
        Supplier.deleted_at.is_(None),
    )
    if query:
        normalised_query = query.strip().lower()
        if normalised_query:
            statement = statement.where(
                or_(
                    func.lower(Supplier.legal_name).contains(normalised_query),
                    func.lower(func.coalesce(Supplier.trading_name, "")).contains(
                        normalised_query
                    ),
                    func.lower(func.coalesce(Supplier.external_reference, "")).contains(
                        normalised_query
                    ),
                )
            )
    if after_sort_key is not None and after_id is not None:
        statement = statement.where(
            or_(
                sort_key > after_sort_key,
                and_(sort_key == after_sort_key, Supplier.id > after_id),
            )
        )
    rows = list(
        db.scalars(
            statement.order_by(sort_key.asc(), Supplier.id.asc()).limit(limit + 1)
        ).all()
    )
    has_more = len(rows) > limit
    items = rows[:limit]
    if has_more and items:
        last = items[-1]
        return CatalogPage(
            items=items, next_sort_key=last.legal_name.lower(), next_id=last.id
        )
    return CatalogPage(items=items, next_sort_key=None, next_id=None)


def list_products(
    db: Session,
    *,
    organization_id: UUID,
    limit: int,
    supplier_id: UUID | None = None,
    after_sort_key: str | None = None,
    after_id: UUID | None = None,
    query: str | None = None,
) -> CatalogPage[Product]:
    _require_tenant_context(db, organization_id)
    if supplier_id is not None:
        get_supplier(db, organization_id=organization_id, supplier_id=supplier_id)
    sort_key = func.lower(Product.reference)
    statement = select(Product).where(
        Product.organization_id == organization_id,
        Product.deleted_at.is_(None),
    )
    if supplier_id is not None:
        statement = statement.where(Product.supplier_id == supplier_id)
    if query:
        normalised_query = query.strip().lower()
        if normalised_query:
            statement = statement.where(
                or_(
                    func.lower(Product.reference).contains(normalised_query),
                    func.lower(Product.name).contains(normalised_query),
                    func.lower(func.coalesce(Product.category, "")).contains(
                        normalised_query
                    ),
                )
            )
    if after_sort_key is not None and after_id is not None:
        statement = statement.where(
            or_(
                sort_key > after_sort_key,
                and_(sort_key == after_sort_key, Product.id > after_id),
            )
        )
    rows = list(
        db.scalars(
            statement.order_by(sort_key.asc(), Product.id.asc()).limit(limit + 1)
        ).all()
    )
    has_more = len(rows) > limit
    items = rows[:limit]
    if has_more and items:
        last = items[-1]
        return CatalogPage(
            items=items, next_sort_key=last.reference.lower(), next_id=last.id
        )
    return CatalogPage(items=items, next_sort_key=None, next_id=None)
