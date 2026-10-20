"""S3-compatible object storage boundary for untrusted documents.

The API never accepts a client-supplied storage key.  Browser uploads are made
only to a short-lived, server-generated quarantine key; clean objects are
promoted to a separate bucket after malware scanning.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from app.core.config import Settings

#: Préfixe d'objets accepté pour une libération par préfixe: un type d'objets, puis
#: l'identifiant d'une seule organisation, puis au moins un segment de nom. La
#: libération par préfixe est la seule opération de stockage qui peut effacer plus
#: que demandé; cette garde rend un préfixe non ancré, vide ou traversant impossible,
#: quel que soit l'appelant - y compris un adaptateur de test.
_TENANT_SCOPED_PREFIX = re.compile(
    r"^(?:quarantine|clean)/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/[^/].*$"
)


def validate_tenant_scoped_prefix(prefix: str) -> str:
    """Refuse any object prefix that is not anchored to exactly one organization.

    Le service construit lui-même ses préfixes; aucun n'est composé à partir d'une
    entrée du client. Cette fonction est la frontière qui rend la preuve vérifiable
    aussi bien sur MinIO que sur un faux stockage, et qui transforme une erreur de
    programmation en échec immédiat plutôt qu'en suppression inter-tenant.
    """

    if not prefix or prefix.startswith("/") or ".." in prefix or not _TENANT_SCOPED_PREFIX.match(prefix):
        raise ObjectStorageError("Préfixe de libération non rattaché à un tenant: refusé.")
    return prefix


class ObjectStorageError(RuntimeError):
    """A storage operation could not be completed safely."""


class ObjectNotFoundError(ObjectStorageError):
    """The expected quarantined object is missing or has expired."""


class ObjectStorageUnavailableError(ObjectStorageError):
    """Storage has deliberately not been configured for this environment."""


@dataclass(frozen=True)
class PresignedUpload:
    url: str
    fields: dict[str, str]


@dataclass(frozen=True)
class ObjectMetadata:
    size_bytes: int
    content_type: str | None
    etag: str | None


@dataclass(frozen=True)
class PrefixDeletionResult:
    """Ce qui a réellement été libéré sous un préfixe, et ce qui ne l'a pas été.

    Pas de booléen « ok »: un échec de liste, un échec de suppression ou une
    confirmation impossible laissent ``retained_keys`` et ``listing_complete`` dire la
    vérité, et ``fully_released`` ne devient vrai que si rien ne subsiste après
    relecture. C'est ce résultat qui décide si le registre de purge peut être fermé.
    """

    bucket: str
    prefix: str
    deleted_keys: tuple[str, ...] = ()
    retained_keys: tuple[str, ...] = ()
    listed_keys: int = 0
    listing_complete: bool = True
    error_code: str | None = None

    @property
    def fully_released(self) -> bool:
        return self.error_code is None and self.listing_complete and not self.retained_keys

    @property
    def state(self) -> str:
        if self.error_code is not None or not self.listing_complete or self.retained_keys:
            return "retained"
        return "deleted" if self.deleted_keys else "already_absent"


class ObjectStorage(Protocol):
    def create_presigned_upload(
        self,
        *,
        bucket: str,
        key: str,
        content_type: str,
        max_size_bytes: int,
        expires_in_seconds: int,
    ) -> PresignedUpload: ...

    def head(self, *, bucket: str, key: str) -> ObjectMetadata: ...

    def read_bytes(self, *, bucket: str, key: str, max_size_bytes: int, expected_etag: str | None = None) -> bytes: ...

    def put_bytes(self, *, bucket: str, key: str, payload: bytes, content_type: str) -> None: ...

    def copy(
        self,
        *,
        source_bucket: str,
        source_key: str,
        source_etag: str,
        destination_bucket: str,
        destination_key: str,
        content_type: str,
    ) -> None: ...

    def delete(self, *, bucket: str, key: str) -> None: ...

    def delete_prefix(self, *, bucket: str, prefix: str) -> PrefixDeletionResult: ...

    def create_presigned_download(
        self,
        *,
        bucket: str,
        key: str,
        download_filename: str,
        expires_in_seconds: int,
    ) -> str: ...


class DisabledObjectStorage:
    """Fail closed when a deployment has no configured document storage."""

    @staticmethod
    def _raise() -> None:
        raise ObjectStorageUnavailableError(
            "Le stockage documentaire sécurisé n’est pas configuré pour cet environnement."
        )

    def create_presigned_upload(self, **_: object) -> PresignedUpload:
        self._raise()

    def head(self, **_: object) -> ObjectMetadata:
        self._raise()

    def read_bytes(self, **_: object) -> bytes:
        self._raise()

    def put_bytes(self, **_: object) -> None:
        self._raise()

    def copy(self, **_: object) -> None:
        self._raise()

    def delete(self, **_: object) -> None:
        self._raise()

    def delete_prefix(self, **_: object) -> PrefixDeletionResult:
        self._raise()

    def create_presigned_download(self, **_: object) -> str:
        self._raise()


class S3ObjectStorage:
    """Thin adapter around S3 / MinIO using private buckets and signed URLs."""

    def __init__(
        self,
        *,
        endpoint_url: str | None,
        public_endpoint_url: str | None,
        region_name: str,
        access_key_id: str | None,
        secret_access_key: str | None,
        sse_mode: str,
        sse_kms_key_id: str | None,
    ) -> None:
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:  # pragma: no cover - packaging guard
            raise ObjectStorageUnavailableError("Le client S3 requis n’est pas installé.") from exc

        # Path-style signing gives MinIO and a public Compose hostname the same
        # canonical request. The public client signs URLs only; all object IO
        # continues through the container-internal endpoint.
        client_options = {
            "region_name": region_name,
            "aws_access_key_id": access_key_id,
            "aws_secret_access_key": secret_access_key,
            "config": Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                connect_timeout=5,
                read_timeout=30,
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        }
        self._client = boto3.client("s3", endpoint_url=endpoint_url, **client_options)
        self._signing_client = boto3.client(
            "s3",
            endpoint_url=public_endpoint_url or endpoint_url,
            **client_options,
        )
        self._sse_mode = sse_mode
        self._sse_kms_key_id = sse_kms_key_id

    def _server_side_encryption(self) -> dict[str, str]:
        if self._sse_mode == "none":
            return {}
        if self._sse_mode == "aes256":
            return {"ServerSideEncryption": "AES256"}
        if self._sse_mode == "aws:kms":
            params = {"ServerSideEncryption": "aws:kms"}
            if self._sse_kms_key_id:
                params["SSEKMSKeyId"] = self._sse_kms_key_id
            return params
        raise ObjectStorageError("Mode de chiffrement objet inconnu.")

    @staticmethod
    def _form_encryption_fields(params: dict[str, str]) -> dict[str, str]:
        fields: dict[str, str] = {}
        if "ServerSideEncryption" in params:
            fields["x-amz-server-side-encryption"] = params["ServerSideEncryption"]
        if "SSEKMSKeyId" in params:
            fields["x-amz-server-side-encryption-aws-kms-key-id"] = params["SSEKMSKeyId"]
        return fields

    @staticmethod
    def _raise_storage_error(exc: Exception) -> None:
        response = getattr(exc, "response", None)
        code = str((response or {}).get("Error", {}).get("Code", ""))
        if code in {"404", "NoSuchKey", "NoSuchBucket", "NotFound"}:
            raise ObjectNotFoundError("Objet de quarantaine introuvable.") from exc
        raise ObjectStorageError("Le stockage objet est indisponible.") from exc

    def create_presigned_upload(
        self,
        *,
        bucket: str,
        key: str,
        content_type: str,
        max_size_bytes: int,
        expires_in_seconds: int,
    ) -> PresignedUpload:
        encryption = self._server_side_encryption()
        fields = {"Content-Type": content_type, **self._form_encryption_fields(encryption)}
        conditions: list[object] = [
            {"Content-Type": content_type},
            ["content-length-range", 1, max_size_bytes],
            *[{name: value} for name, value in self._form_encryption_fields(encryption).items()],
        ]
        try:
            response = self._signing_client.generate_presigned_post(
                Bucket=bucket,
                Key=key,
                Fields=fields,
                Conditions=conditions,
                ExpiresIn=expires_in_seconds,
            )
        except Exception as exc:  # boto3's exception hierarchy is optional at import time.
            self._raise_storage_error(exc)
        return PresignedUpload(url=str(response["url"]), fields={str(k): str(v) for k, v in response["fields"].items()})

    def head(self, *, bucket: str, key: str) -> ObjectMetadata:
        try:
            response = self._client.head_object(Bucket=bucket, Key=key)
        except Exception as exc:
            self._raise_storage_error(exc)
        return ObjectMetadata(
            size_bytes=int(response["ContentLength"]),
            content_type=str(response["ContentType"]) if response.get("ContentType") else None,
            # Preserve S3's quoted ETag exactly because CopySourceIfMatch uses
            # the entity-tag syntax rather than an application checksum.
            etag=str(response["ETag"]) if response.get("ETag") else None,
        )

    def read_bytes(self, *, bucket: str, key: str, max_size_bytes: int, expected_etag: str | None = None) -> bytes:
        try:
            # Bind the bytes being scanned to the revision used for promotion.
            condition = {"IfMatch": expected_etag} if expected_etag else {}
            response = self._client.get_object(Bucket=bucket, Key=key, **condition)
            body = response["Body"]
            try:
                payload = body.read(max_size_bytes + 1)
            finally:
                body.close()
        except ObjectStorageError:
            raise
        except Exception as exc:
            self._raise_storage_error(exc)
        if len(payload) > max_size_bytes:
            raise ObjectStorageError("L’objet dépasse la taille autorisée.")
        return payload

    def put_bytes(self, *, bucket: str, key: str, payload: bytes, content_type: str) -> None:
        """Persist a server-generated derived artefact in the private clean bucket.

        This method deliberately has no caller-controlled ACL or key policy.
        The extraction worker creates an opaque key and relies on the same SSE
        policy used for promoted source documents.
        """
        try:
            self._client.put_object(
                Bucket=bucket,
                Key=key,
                Body=payload,
                ContentType=content_type,
                **self._server_side_encryption(),
            )
        except Exception as exc:
            self._raise_storage_error(exc)

    def copy(
        self,
        *,
        source_bucket: str,
        source_key: str,
        source_etag: str,
        destination_bucket: str,
        destination_key: str,
        content_type: str,
    ) -> None:
        try:
            # The browser may replay its short-lived POST while completion is
            # running. Copy only the exact object version whose bytes were
            # validated and scanned; a changed ETag fails the promotion.
            self._client.copy_object(
                Bucket=destination_bucket,
                Key=destination_key,
                CopySource={"Bucket": source_bucket, "Key": source_key},
                CopySourceIfMatch=source_etag,
                ContentType=content_type,
                MetadataDirective="REPLACE",
                **self._server_side_encryption(),
            )
        except Exception as exc:
            self._raise_storage_error(exc)

    def delete(self, *, bucket: str, key: str) -> None:
        try:
            self._client.delete_object(Bucket=bucket, Key=key)
        except Exception as exc:
            self._raise_storage_error(exc)

    #: Une page S3 contient 1 000 clés; cinq pages bornent une passe de libération à
    #: 5 000 objets. Au-delà le résultat est explicitement incomplet, donc le registre
    #: reste ouvert et la passe suivante reprend là où la précédente s'est arrêtée.
    _MAX_LIST_PAGES = 5

    def _list_prefix(self, *, bucket: str, prefix: str) -> tuple[list[str], bool]:
        keys: list[str] = []
        token: str | None = None
        for _ in range(self._MAX_LIST_PAGES):
            params: dict[str, object] = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 1000}
            if token:
                params["ContinuationToken"] = token
            response = self._client.list_objects_v2(**params)
            keys.extend(str(item["Key"]) for item in response.get("Contents", []) if item.get("Key"))
            if not response.get("IsTruncated"):
                return keys, True
            token = response.get("NextContinuationToken")
            if not token:
                return keys, False
        return keys, False

    def delete_prefix(self, *, bucket: str, prefix: str) -> PrefixDeletionResult:
        """Release every object of this tenant under ``prefix``, then re-read the listing.

        ``delete_objects`` répond 200 même pour des clés non supprimées: seule la
        relecture de la liste rend ``deleted_keys`` prouvable. Les échecs de transport
        ne deviennent jamais un succès - ils reviennent comme ``error_code`` avec les
        clés encore présentes.
        """

        validate_tenant_scoped_prefix(prefix)
        try:
            listed, complete = self._list_prefix(bucket=bucket, prefix=prefix)
        except Exception:
            return PrefixDeletionResult(bucket=bucket, prefix=prefix, listing_complete=False, error_code="list_failed")
        deleted: list[str] = []
        error_code: str | None = None
        for start in range(0, len(listed), 1000):
            batch = listed[start : start + 1000]
            try:
                self._client.delete_objects(
                    Bucket=bucket,
                    Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
                )
                deleted.extend(batch)
            except Exception:
                error_code = "delete_failed"
                break
        try:
            remaining, rechecked = self._list_prefix(bucket=bucket, prefix=prefix)
        except Exception:
            remaining, rechecked = sorted(set(deleted) | set(listed)), False
        complete = complete and rechecked
        retained = sorted(set(remaining) | set(listed[len(deleted) :]))
        proven_deleted = tuple(sorted(set(deleted) - set(retained)))
        return PrefixDeletionResult(
            bucket=bucket,
            prefix=prefix,
            deleted_keys=proven_deleted,
            retained_keys=tuple(retained),
            listed_keys=len(listed),
            listing_complete=complete,
            error_code=error_code,
        )

    def create_presigned_download(
        self,
        *,
        bucket: str,
        key: str,
        download_filename: str,
        expires_in_seconds: int,
    ) -> str:
        try:
            return str(
                self._signing_client.generate_presigned_url(
                    "get_object",
                    Params={
                        "Bucket": bucket,
                        "Key": key,
                        "ResponseContentDisposition": f'attachment; filename="{download_filename}"',
                    },
                    ExpiresIn=expires_in_seconds,
                )
            )
        except Exception as exc:
            self._raise_storage_error(exc)


def build_object_storage(settings: Settings) -> ObjectStorage:
    if settings.document_storage_backend == "disabled":
        return DisabledObjectStorage()
    if settings.document_storage_backend != "s3":
        raise ObjectStorageUnavailableError("Backend de stockage documentaire inconnu.")
    return S3ObjectStorage(
        endpoint_url=settings.document_storage_endpoint,
        public_endpoint_url=settings.document_storage_public_endpoint,
        region_name=settings.document_storage_region,
        access_key_id=settings.document_storage_access_key,
        secret_access_key=settings.document_storage_secret_key,
        sse_mode=settings.document_storage_sse_mode,
        sse_kms_key_id=settings.document_storage_sse_kms_key_id,
    )
