"""S3 adapter contract only: scripted SDK responses, NOT a real S3/MinIO proof."""
from uuid import uuid4

import pytest

from app.documents.storage import S3ObjectStorage


class ScriptedS3:
    def __init__(self, pages):
        self.pages = iter(pages)
        self.deleted = []

    def list_objects_v2(self, **kwargs):
        page = next(self.pages)
        if isinstance(page, Exception):
            raise page
        return page

    def delete_objects(self, **kwargs):
        self.deleted.extend(item["Key"] for item in kwargs["Delete"]["Objects"])
        # S3 returns HTTP 200 even when a key could not be removed.
        return {"Errors": [{"Key": self.deleted[-1], "Code": "AccessDenied"}]}


def adapter(pages):
    storage = object.__new__(S3ObjectStorage)
    storage._client = ScriptedS3(pages)
    return storage


def prefix():
    return f"quarantine/{uuid4()}/{uuid4()}/{uuid4()}/"


def page(*keys, truncated=False, **extra):
    return {"Contents": [{"Key": key} for key in keys], "IsTruncated": truncated, **extra}


def test_partial_delete_response_is_checked_against_remaining_objects():
    scope = prefix()
    storage = adapter([page(scope + "source", scope + "fragment"), page(scope + "fragment")])
    result = storage.delete_prefix(bucket="quarantine", prefix=scope)
    assert not result.fully_released
    assert result.deleted_keys == (scope + "source",)
    assert result.retained_keys == (scope + "fragment",)


def test_failed_verification_never_proves_deletion():
    scope = prefix()
    storage = adapter([page(scope + "source"), OSError("listing interrupted")])
    result = storage.delete_prefix(bucket="quarantine", prefix=scope)
    assert not result.fully_released
    assert result.deleted_keys == ()
    assert result.listing_complete is False


def test_incomplete_pagination_stays_open_for_another_pass():
    scope = prefix()
    storage = adapter([page(scope + "source", truncated=True, NextContinuationToken="next"), page()])
    storage._MAX_LIST_PAGES = 1
    assert not storage.delete_prefix(bucket="quarantine", prefix=scope).fully_released


def test_confirmed_empty_prefix_is_idempotently_released():
    storage = adapter([page(), page()])
    result = storage.delete_prefix(bucket="quarantine", prefix=prefix())
    assert result.fully_released and result.state == "already_absent"
    assert storage._client.deleted == []


@pytest.mark.parametrize("invalid", [
    {},
    {"IsTruncated": "false"},
    {"IsTruncated": False, "Contents": [{"Key": prefix() + "foreign"}]},
])
def test_invalid_or_out_of_scope_listing_never_authorizes_deletion(invalid):
    storage = adapter([invalid, page()])
    result = storage.delete_prefix(bucket="quarantine", prefix=prefix())
    assert not result.fully_released
    assert storage._client.deleted == []
