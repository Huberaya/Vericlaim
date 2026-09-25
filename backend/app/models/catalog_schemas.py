"""Typed HTTP contracts for the tenant-scoped supplier and product catalog."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_COUNTRY_CODE_PATTERN = re.compile(r"^[A-Za-z]{2}$")
_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_MAX_METADATA_BYTES = 16_384
_MAX_METADATA_DEPTH = 5
_MAX_METADATA_ENTRIES = 50
_MAX_METADATA_STRING_LENGTH = 4_000

ProductLifecycleStatus = Literal["active", "inactive", "discontinued"]


def _normalise_required(
    value: str, *, label: str, maximum: int, minimum: int = 1
) -> str:
    normalised = value.strip()
    if len(normalised) < minimum or len(normalised) > maximum:
        raise ValueError(
            f"{label} doit contenir entre {minimum} et {maximum} caractères."
        )
    if any(ord(character) < 32 for character in normalised):
        raise ValueError(f"{label} contient un caractère de contrôle.")
    return normalised


def _normalise_optional(value: str | None, *, label: str, maximum: int) -> str | None:
    if value is None:
        return None
    normalised = value.strip()
    if not normalised:
        return None
    return _normalise_required(normalised, label=label, maximum=maximum)


def _normalise_country_code(value: str | None) -> str | None:
    if value is None:
        return None
    normalised = value.strip().upper()
    if not normalised:
        return None
    if not _COUNTRY_CODE_PATTERN.fullmatch(normalised):
        raise ValueError("Le code pays doit contenir exactement deux lettres ISO.")
    return normalised


def _normalise_email(value: str | None) -> str | None:
    if value is None:
        return None
    normalised = value.strip().lower()
    if not normalised:
        return None
    if len(normalised) > 320 or not _EMAIL_PATTERN.fullmatch(normalised):
        raise ValueError("L’adresse e-mail de contact est invalide.")
    return normalised


def _validate_metadata_value(value: Any, *, depth: int = 0) -> None:
    if depth > _MAX_METADATA_DEPTH:
        raise ValueError("Les métadonnées dépassent la profondeur autorisée.")
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(
                "Les métadonnées ne peuvent pas contenir de nombre non fini."
            )
        return
    if isinstance(value, str):
        if len(value) > _MAX_METADATA_STRING_LENGTH or any(
            ord(character) < 32 for character in value
        ):
            raise ValueError(
                "Une valeur de métadonnée est trop longue ou contient un caractère de contrôle."
            )
        return
    if isinstance(value, list):
        if len(value) > _MAX_METADATA_ENTRIES:
            raise ValueError("Une liste de métadonnées dépasse la taille autorisée.")
        for item in value:
            _validate_metadata_value(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > _MAX_METADATA_ENTRIES:
            raise ValueError("Un objet de métadonnées dépasse la taille autorisée.")
        for key, item in value.items():
            if (
                not isinstance(key, str)
                or not key.strip()
                or len(key) > 80
                or any(ord(character) < 32 for character in key)
            ):
                raise ValueError("Une clé de métadonnée est invalide.")
            _validate_metadata_value(item, depth=depth + 1)
        return
    raise ValueError(
        "Les métadonnées doivent être composées uniquement de valeurs JSON."
    )


def _normalise_metadata(value: dict[str, Any]) -> dict[str, Any]:
    _validate_metadata_value(value)
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    if len(encoded.encode("utf-8")) > _MAX_METADATA_BYTES:
        raise ValueError("Les métadonnées dépassent 16 Ko.")
    return value


class _CatalogModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SupplierCreateRequest(_CatalogModel):
    legal_name: str = Field(min_length=2, max_length=255)
    trading_name: str | None = Field(default=None, max_length=255)
    external_reference: str | None = Field(default=None, max_length=128)
    country_code: str | None = Field(default=None, max_length=2)
    contact_email: str | None = Field(default=None, max_length=320)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("legal_name")
    @classmethod
    def normalise_legal_name(cls, value: str) -> str:
        return _normalise_required(value, label="legal_name", maximum=255, minimum=2)

    @field_validator("trading_name")
    @classmethod
    def normalise_trading_name(cls, value: str | None) -> str | None:
        return _normalise_optional(value, label="trading_name", maximum=255)

    @field_validator("external_reference")
    @classmethod
    def normalise_external_reference(cls, value: str | None) -> str | None:
        return _normalise_optional(value, label="external_reference", maximum=128)

    @field_validator("country_code")
    @classmethod
    def normalise_country_code(cls, value: str | None) -> str | None:
        return _normalise_country_code(value)

    @field_validator("contact_email")
    @classmethod
    def normalise_contact_email(cls, value: str | None) -> str | None:
        return _normalise_email(value)

    @field_validator("metadata")
    @classmethod
    def normalise_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _normalise_metadata(value)


class SupplierUpdateRequest(_CatalogModel):
    legal_name: str | None = Field(default=None, max_length=255)
    trading_name: str | None = Field(default=None, max_length=255)
    external_reference: str | None = Field(default=None, max_length=128)
    country_code: str | None = Field(default=None, max_length=2)
    contact_email: str | None = Field(default=None, max_length=320)
    metadata: dict[str, Any] | None = None

    @field_validator("legal_name")
    @classmethod
    def normalise_legal_name(cls, value: str | None) -> str | None:
        return (
            None
            if value is None
            else _normalise_required(value, label="legal_name", maximum=255, minimum=2)
        )

    @field_validator("trading_name")
    @classmethod
    def normalise_trading_name(cls, value: str | None) -> str | None:
        return _normalise_optional(value, label="trading_name", maximum=255)

    @field_validator("external_reference")
    @classmethod
    def normalise_external_reference(cls, value: str | None) -> str | None:
        return _normalise_optional(value, label="external_reference", maximum=128)

    @field_validator("country_code")
    @classmethod
    def normalise_country_code(cls, value: str | None) -> str | None:
        return _normalise_country_code(value)

    @field_validator("contact_email")
    @classmethod
    def normalise_contact_email(cls, value: str | None) -> str | None:
        return _normalise_email(value)

    @field_validator("metadata")
    @classmethod
    def normalise_metadata(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return None if value is None else _normalise_metadata(value)

    @model_validator(mode="after")
    def require_change(self) -> SupplierUpdateRequest:
        if not self.model_fields_set:
            raise ValueError("Au moins un champ de fournisseur doit être fourni.")
        if "legal_name" in self.model_fields_set and self.legal_name is None:
            raise ValueError("legal_name ne peut pas être nul.")
        if "metadata" in self.model_fields_set and self.metadata is None:
            raise ValueError(
                "metadata ne peut pas être nul ; utilisez un objet vide pour l’effacer."
            )
        return self


class ProductCreateRequest(_CatalogModel):
    supplier_id: UUID
    reference: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=2, max_length=255)
    category: str | None = Field(default=None, max_length=128)
    country_of_sale: str | None = Field(default=None, max_length=2)
    lifecycle_status: ProductLifecycleStatus = "active"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("reference")
    @classmethod
    def normalise_reference(cls, value: str) -> str:
        return _normalise_required(value, label="reference", maximum=128)

    @field_validator("name")
    @classmethod
    def normalise_name(cls, value: str) -> str:
        return _normalise_required(value, label="name", maximum=255, minimum=2)

    @field_validator("category")
    @classmethod
    def normalise_category(cls, value: str | None) -> str | None:
        return _normalise_optional(value, label="category", maximum=128)

    @field_validator("country_of_sale")
    @classmethod
    def normalise_country_of_sale(cls, value: str | None) -> str | None:
        return _normalise_country_code(value)

    @field_validator("metadata")
    @classmethod
    def normalise_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _normalise_metadata(value)


class ProductUpdateRequest(_CatalogModel):
    reference: str | None = Field(default=None, max_length=128)
    name: str | None = Field(default=None, max_length=255)
    category: str | None = Field(default=None, max_length=128)
    country_of_sale: str | None = Field(default=None, max_length=2)
    lifecycle_status: ProductLifecycleStatus | None = None
    metadata: dict[str, Any] | None = None

    @field_validator("reference")
    @classmethod
    def normalise_reference(cls, value: str | None) -> str | None:
        return (
            None
            if value is None
            else _normalise_required(value, label="reference", maximum=128)
        )

    @field_validator("name")
    @classmethod
    def normalise_name(cls, value: str | None) -> str | None:
        return (
            None
            if value is None
            else _normalise_required(value, label="name", maximum=255, minimum=2)
        )

    @field_validator("category")
    @classmethod
    def normalise_category(cls, value: str | None) -> str | None:
        return _normalise_optional(value, label="category", maximum=128)

    @field_validator("country_of_sale")
    @classmethod
    def normalise_country_of_sale(cls, value: str | None) -> str | None:
        return _normalise_country_code(value)

    @field_validator("metadata")
    @classmethod
    def normalise_metadata(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return None if value is None else _normalise_metadata(value)

    @model_validator(mode="after")
    def require_change(self) -> ProductUpdateRequest:
        if not self.model_fields_set:
            raise ValueError("Au moins un champ de produit doit être fourni.")
        for field_name in ("reference", "name"):
            if (
                field_name in self.model_fields_set
                and getattr(self, field_name) is None
            ):
                raise ValueError(f"{field_name} ne peut pas être nul.")
        if "metadata" in self.model_fields_set and self.metadata is None:
            raise ValueError(
                "metadata ne peut pas être nul ; utilisez un objet vide pour l’effacer."
            )
        return self


class SupplierResponse(_CatalogModel):
    id: UUID
    legal_name: str
    trading_name: str | None
    external_reference: str | None
    country_code: str | None
    contact_email: str | None
    metadata: dict[str, Any]
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ProductResponse(_CatalogModel):
    id: UUID
    supplier_id: UUID
    reference: str
    name: str
    category: str | None
    country_of_sale: str | None
    lifecycle_status: ProductLifecycleStatus
    metadata: dict[str, Any]
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SupplierCreateResponse(_CatalogModel):
    supplier: SupplierResponse
    idempotent_replay: bool


class ProductCreateResponse(_CatalogModel):
    product: ProductResponse
    idempotent_replay: bool


class SupplierListResponse(_CatalogModel):
    items: list[SupplierResponse]
    next_cursor: str | None = None


class ProductListResponse(_CatalogModel):
    items: list[ProductResponse]
    next_cursor: str | None = None
