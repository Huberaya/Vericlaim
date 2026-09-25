"""Authenticated tenant-scoped API for suppliers and products."""

from __future__ import annotations

import base64
import json
from typing import Annotated, Any
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from sqlalchemy.orm import Session

from app.catalog.service import (
    CatalogConflictError,
    CatalogIdempotencyConflictError,
    CatalogInputError,
    CatalogNotFoundError,
    archive_product,
    archive_supplier,
    create_product,
    create_supplier,
    get_product,
    get_supplier,
    list_products,
    list_suppliers,
    update_product,
    update_supplier,
)
from app.core.database import get_db
from app.identity.dependencies import (
    TenantPrincipal,
    request_id_from_request,
    require_permission,
)
from app.models.catalog_schemas import (
    ProductCreateRequest,
    ProductCreateResponse,
    ProductListResponse,
    ProductResponse,
    ProductUpdateRequest,
    SupplierCreateRequest,
    SupplierCreateResponse,
    SupplierListResponse,
    SupplierResponse,
    SupplierUpdateRequest,
)
from app.models.domain import Product, Supplier

supplier_router = APIRouter(prefix="/api/v1/suppliers", tags=["catalog"])
product_router = APIRouter(prefix="/api/v1/products", tags=["catalog"])

DATABASE_DEPENDENCY = Depends(get_db)
CATALOG_READ_DEPENDENCY = Depends(require_permission("catalog:read"))
CATALOG_MANAGE_DEPENDENCY = Depends(
    require_permission("catalog:manage", csrf_protected=True)
)


def _present_supplier(supplier: Supplier) -> SupplierResponse:
    return SupplierResponse(
        id=supplier.id,
        legal_name=supplier.legal_name,
        trading_name=supplier.trading_name,
        external_reference=supplier.external_reference,
        country_code=supplier.country_code,
        contact_email=supplier.contact_email,
        metadata=dict(supplier.metadata_json or {}),
        archived_at=supplier.deleted_at,
        created_at=supplier.created_at,
        updated_at=supplier.updated_at,
    )


def _present_product(product: Product) -> ProductResponse:
    return ProductResponse(
        id=product.id,
        supplier_id=product.supplier_id,
        reference=product.reference,
        name=product.name,
        category=product.category,
        country_of_sale=product.country_of_sale,
        lifecycle_status=product.lifecycle_status,
        metadata=dict(product.metadata_json or {}),
        archived_at=product.deleted_at,
        created_at=product.created_at,
        updated_at=product.updated_at,
    )


def _encode_cursor(sort_key: str | None, resource_id: UUID | None) -> str | None:
    if sort_key is None or resource_id is None:
        return None
    raw = json.dumps(
        {"sort": sort_key, "id": str(resource_id)},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None) -> tuple[str | None, UUID | None]:
    if cursor is None:
        return None, None
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode((cursor + padding).encode("ascii"))
        payload: Any = json.loads(raw.decode("utf-8"))
        sort_key = payload.get("sort") if isinstance(payload, dict) else None
        resource_id = (
            UUID(str(payload.get("id"))) if isinstance(payload, dict) else None
        )
        if not isinstance(sort_key, str) or not sort_key:
            raise ValueError("sort absent")
        return sort_key, resource_id
    except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Cursor catalogue invalide.",
        ) from exc


def _raise_catalog_error(exc: Exception) -> None:
    if isinstance(exc, CatalogNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    if isinstance(exc, (CatalogIdempotencyConflictError, CatalogConflictError)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    if isinstance(exc, CatalogInputError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    raise exc


@supplier_router.post(
    "", response_model=SupplierCreateResponse, status_code=status.HTTP_201_CREATED
)
def create_supplier_endpoint(
    body: SupplierCreateRequest,
    request: Request,
    response: Response,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ],
    principal: TenantPrincipal = CATALOG_MANAGE_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> SupplierCreateResponse:
    try:
        result = create_supplier(
            db,
            organization_id=principal.organization_id,
            actor_user_id=principal.user_id,
            idempotency_key=idempotency_key,
            legal_name=body.legal_name,
            trading_name=body.trading_name,
            external_reference=body.external_reference,
            country_code=body.country_code,
            contact_email=body.contact_email,
            metadata=body.metadata,
            request_id=request_id_from_request(request),
        )
    except (CatalogNotFoundError, CatalogConflictError, CatalogInputError) as exc:
        _raise_catalog_error(exc)
    if result.idempotent_replay:
        response.status_code = status.HTTP_200_OK
    return SupplierCreateResponse(
        supplier=_present_supplier(result.resource),
        idempotent_replay=result.idempotent_replay,
    )


@supplier_router.get("", response_model=SupplierListResponse)
def list_suppliers_endpoint(
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    principal: TenantPrincipal = CATALOG_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> SupplierListResponse:
    after_sort_key, after_id = _decode_cursor(cursor)
    try:
        page = list_suppliers(
            db,
            organization_id=principal.organization_id,
            limit=limit,
            after_sort_key=after_sort_key,
            after_id=after_id,
            query=q,
        )
    except (CatalogNotFoundError, CatalogConflictError, CatalogInputError) as exc:
        _raise_catalog_error(exc)
    return SupplierListResponse(
        items=[_present_supplier(item) for item in page.items],
        next_cursor=_encode_cursor(page.next_sort_key, page.next_id),
    )


@supplier_router.get("/{supplier_id}", response_model=SupplierResponse)
def read_supplier_endpoint(
    supplier_id: UUID,
    principal: TenantPrincipal = CATALOG_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> SupplierResponse:
    try:
        return _present_supplier(
            get_supplier(
                db, organization_id=principal.organization_id, supplier_id=supplier_id
            )
        )
    except (CatalogNotFoundError, CatalogConflictError, CatalogInputError) as exc:
        _raise_catalog_error(exc)


@supplier_router.patch("/{supplier_id}", response_model=SupplierResponse)
def update_supplier_endpoint(
    supplier_id: UUID,
    body: SupplierUpdateRequest,
    request: Request,
    principal: TenantPrincipal = CATALOG_MANAGE_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> SupplierResponse:
    try:
        supplier = update_supplier(
            db,
            organization_id=principal.organization_id,
            actor_user_id=principal.user_id,
            supplier_id=supplier_id,
            changes=body.model_dump(exclude_unset=True),
            request_id=request_id_from_request(request),
        )
        return _present_supplier(supplier)
    except (CatalogNotFoundError, CatalogConflictError, CatalogInputError) as exc:
        _raise_catalog_error(exc)


@supplier_router.delete("/{supplier_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive_supplier_endpoint(
    supplier_id: UUID,
    request: Request,
    principal: TenantPrincipal = CATALOG_MANAGE_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> Response:
    try:
        archive_supplier(
            db,
            organization_id=principal.organization_id,
            actor_user_id=principal.user_id,
            supplier_id=supplier_id,
            request_id=request_id_from_request(request),
        )
    except (CatalogNotFoundError, CatalogConflictError, CatalogInputError) as exc:
        _raise_catalog_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@product_router.post(
    "", response_model=ProductCreateResponse, status_code=status.HTTP_201_CREATED
)
def create_product_endpoint(
    body: ProductCreateRequest,
    request: Request,
    response: Response,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ],
    principal: TenantPrincipal = CATALOG_MANAGE_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> ProductCreateResponse:
    try:
        result = create_product(
            db,
            organization_id=principal.organization_id,
            actor_user_id=principal.user_id,
            idempotency_key=idempotency_key,
            supplier_id=body.supplier_id,
            reference=body.reference,
            name=body.name,
            category=body.category,
            country_of_sale=body.country_of_sale,
            lifecycle_status=body.lifecycle_status,
            metadata=body.metadata,
            request_id=request_id_from_request(request),
        )
    except (CatalogNotFoundError, CatalogConflictError, CatalogInputError) as exc:
        _raise_catalog_error(exc)
    if result.idempotent_replay:
        response.status_code = status.HTTP_200_OK
    return ProductCreateResponse(
        product=_present_product(result.resource),
        idempotent_replay=result.idempotent_replay,
    )


@product_router.get("", response_model=ProductListResponse)
def list_products_endpoint(
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    supplier_id: UUID | None = None,
    principal: TenantPrincipal = CATALOG_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> ProductListResponse:
    after_sort_key, after_id = _decode_cursor(cursor)
    try:
        page = list_products(
            db,
            organization_id=principal.organization_id,
            limit=limit,
            supplier_id=supplier_id,
            after_sort_key=after_sort_key,
            after_id=after_id,
            query=q,
        )
    except (CatalogNotFoundError, CatalogConflictError, CatalogInputError) as exc:
        _raise_catalog_error(exc)
    return ProductListResponse(
        items=[_present_product(item) for item in page.items],
        next_cursor=_encode_cursor(page.next_sort_key, page.next_id),
    )


@product_router.get("/{product_id}", response_model=ProductResponse)
def read_product_endpoint(
    product_id: UUID,
    principal: TenantPrincipal = CATALOG_READ_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> ProductResponse:
    try:
        return _present_product(
            get_product(
                db, organization_id=principal.organization_id, product_id=product_id
            )
        )
    except (CatalogNotFoundError, CatalogConflictError, CatalogInputError) as exc:
        _raise_catalog_error(exc)


@product_router.patch("/{product_id}", response_model=ProductResponse)
def update_product_endpoint(
    product_id: UUID,
    body: ProductUpdateRequest,
    request: Request,
    principal: TenantPrincipal = CATALOG_MANAGE_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> ProductResponse:
    try:
        product = update_product(
            db,
            organization_id=principal.organization_id,
            actor_user_id=principal.user_id,
            product_id=product_id,
            changes=body.model_dump(exclude_unset=True),
            request_id=request_id_from_request(request),
        )
        return _present_product(product)
    except (CatalogNotFoundError, CatalogConflictError, CatalogInputError) as exc:
        _raise_catalog_error(exc)


@product_router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive_product_endpoint(
    product_id: UUID,
    request: Request,
    principal: TenantPrincipal = CATALOG_MANAGE_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> Response:
    try:
        archive_product(
            db,
            organization_id=principal.organization_id,
            actor_user_id=principal.user_id,
            product_id=product_id,
            request_id=request_id_from_request(request),
        )
    except (CatalogNotFoundError, CatalogConflictError, CatalogInputError) as exc:
        _raise_catalog_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
