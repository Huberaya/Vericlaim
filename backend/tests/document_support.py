"""Deterministic storage/scanner adapters for secure-document API tests.

They exercise the same service boundary as S3/ClamAV without exposing an actual
object store or accepting a test-only path in application code.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256

from app.documents.scanner import MalwareScanResult, MalwareScannerUnavailableError
from app.documents.storage import (
    ObjectMetadata,
    ObjectNotFoundError,
    ObjectStorageError,
    PrefixDeletionResult,
    PresignedUpload,
    validate_tenant_scoped_prefix,
)


@dataclass
class StoredObject:
    payload: bytes
    content_type: str
    etag: str


class FakeObjectStorage:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], StoredObject] = {}
        self.upload_requests: list[tuple[str, str, str, int, int]] = []
        self.copy_requests: list[tuple[str, str, str, str, str]] = []
        self.put_requests: list[tuple[str, str, str]] = []
        self.download_requests: list[tuple[str, str, str, int]] = []
        self.fail_operations: set[str] = set()
        self.prefix_deletions: list[tuple[str, str, tuple[str, ...]]] = []
        self.fail_prefix_deletions: set[str] = set()

    def _maybe_fail(self, operation: str) -> None:
        if operation in self.fail_operations:
            raise ObjectStorageError(f"Fake storage failure during {operation}")

    def create_presigned_upload(
        self,
        *,
        bucket: str,
        key: str,
        content_type: str,
        max_size_bytes: int,
        expires_in_seconds: int,
    ) -> PresignedUpload:
        self._maybe_fail("create_presigned_upload")
        self.upload_requests.append((bucket, key, content_type, max_size_bytes, expires_in_seconds))
        # The opaque field values mimic a browser capability. The storage key
        # stays in the fake adapter rather than an application response model.
        return PresignedUpload(
            url="https://uploads.invalid/vericlaim-capability",
            fields={"policy": "test-policy", "x-amz-signature": "test-signature"},
        )

    def upload_latest(self, *, payload: bytes, content_type: str | None = None) -> tuple[str, str]:
        if not self.upload_requests:
            raise AssertionError("No presigned upload was requested")
        bucket, key, requested_type, _, _ = self.upload_requests[-1]
        self.objects[(bucket, key)] = StoredObject(
            payload=payload,
            content_type=content_type or requested_type,
            etag=sha256(payload).hexdigest()[:32],
        )
        return bucket, key

    def head(self, *, bucket: str, key: str) -> ObjectMetadata:
        self._maybe_fail("head")
        stored = self.objects.get((bucket, key))
        if stored is None:
            raise ObjectNotFoundError("missing fake object")
        return ObjectMetadata(size_bytes=len(stored.payload), content_type=stored.content_type, etag=stored.etag)

    def read_bytes(self, *, bucket: str, key: str, max_size_bytes: int, expected_etag: str | None = None) -> bytes:
        self._maybe_fail("read_bytes")
        stored = self.objects.get((bucket, key))
        if stored is None:
            raise ObjectNotFoundError("missing fake object")
        if expected_etag is not None and stored.etag != expected_etag:
            raise ObjectStorageError("fake object changed before read")
        if len(stored.payload) > max_size_bytes:
            raise ObjectStorageError("fake object too large")
        return stored.payload

    def put_bytes(self, *, bucket: str, key: str, payload: bytes, content_type: str) -> None:
        self._maybe_fail("put_bytes")
        self.objects[(bucket, key)] = StoredObject(
            payload=payload,
            content_type=content_type,
            etag=sha256(payload).hexdigest()[:32],
        )
        self.put_requests.append((bucket, key, content_type))

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
        self._maybe_fail("copy")
        stored = self.objects.get((source_bucket, source_key))
        if stored is None:
            raise ObjectNotFoundError("missing fake source")
        if stored.etag != source_etag:
            raise ObjectStorageError("fake source changed after scan")
        self.objects[(destination_bucket, destination_key)] = StoredObject(
            payload=stored.payload,
            content_type=content_type,
            etag=stored.etag,
        )
        self.copy_requests.append((source_bucket, source_key, destination_bucket, destination_key, content_type))

    def delete(self, *, bucket: str, key: str) -> None:
        self._maybe_fail("delete")
        self.objects.pop((bucket, key), None)

    def delete_prefix(self, *, bucket: str, prefix: str) -> PrefixDeletionResult:
        """Miroir fidele du geste S3: liste, retire, relit.

        La meme garde de prefixe s'applique ici qu'a l'adaptateur reel, et le resultat
        distingue « rien sous ce prefixe », « libere » et « encore present » - un faux
        stockage qui pretendrait tout supprimer ne prouverait rien.
        """

        self._maybe_fail("delete_prefix")
        validate_tenant_scoped_prefix(prefix)
        listed = sorted(key for (bucket_name, key) in self.objects if bucket_name == bucket and key.startswith(prefix))
        self.prefix_deletions.append((bucket, prefix, tuple(listed)))
        deleted: list[str] = []
        retained: list[str] = []
        for key in listed:
            if key in self.fail_prefix_deletions or prefix in self.fail_prefix_deletions:
                retained.append(key)
                continue
            self.objects.pop((bucket, key), None)
            deleted.append(key)
        remaining = sorted(key for (bucket_name, key) in self.objects if bucket_name == bucket and key.startswith(prefix))
        return PrefixDeletionResult(
            bucket=bucket,
            prefix=prefix,
            deleted_keys=tuple(deleted),
            retained_keys=tuple(sorted(set(remaining) | set(retained))),
            listed_keys=len(listed),
            listing_complete=True,
            error_code=None,
        )

    def create_presigned_download(
        self,
        *,
        bucket: str,
        key: str,
        download_filename: str,
        expires_in_seconds: int,
    ) -> str:
        self._maybe_fail("create_presigned_download")
        if (bucket, key) not in self.objects:
            raise ObjectNotFoundError("missing fake clean object")
        self.download_requests.append((bucket, key, download_filename, expires_in_seconds))
        return "https://downloads.invalid/vericlaim-capability"


class FakeScanner:
    def __init__(self, *, clean: bool = True, available: bool = True, on_scan: Callable[[bytes], None] | None = None) -> None:
        self.clean = clean
        self.available = available
        self.on_scan = on_scan
        self.scanned_payloads: list[bytes] = []

    def scan(self, payload: bytes) -> MalwareScanResult:
        self.scanned_payloads.append(payload)
        if self.on_scan is not None:
            self.on_scan(payload)
        if not self.available:
            raise MalwareScannerUnavailableError("fake scanner unavailable")
        return MalwareScanResult(is_clean=self.clean, engine="fake-clamav", signature_version="fake-db")
