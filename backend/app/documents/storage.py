"""S3-compatible object storage boundary for untrusted documents.

The API never accepts a client-supplied storage key.  Browser uploads are made
only to a short-lived, server-generated quarantine key; clean objects are
promoted to a separate bucket after malware scanning.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.core.config import Settings


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

    def read_bytes(self, *, bucket: str, key: str, max_size_bytes: int) -> bytes: ...

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

    def read_bytes(self, *, bucket: str, key: str, max_size_bytes: int) -> bytes:
        try:
            response = self._client.get_object(Bucket=bucket, Key=key)
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
