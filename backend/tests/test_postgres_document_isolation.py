"""Document-table RLS on disposable PostgreSQL, using vericlaim_app, not owner.

Fixtures are synthetic database rows, not proof of S3/ClamAV integration.
"""
import uuid

import pytest
from sqlalchemy import create_engine, delete, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.identity.service import set_db_request_context
from app.models.domain import Document, DocumentVersion, DocumentExtractionJob
from tests.test_postgres_tenant_isolation import (
    ADMIN_DSN, APP_ROLE, APP_PASSWORD, _sqlalchemy_url, cluster, tenants,
)

pytestmark = pytest.mark.skipif(not ADMIN_DSN, reason="Disposable PostgreSQL URL not configured")


@pytest.fixture()
def document_rows(cluster, tenants):
    engine = create_engine(_sqlalchemy_url(ADMIN_DSN, dbname=cluster.database, user=APP_ROLE, password=APP_PASSWORD))
    rows = []
    try:
        for organization in (tenants.organization_a, tenants.organization_b):
            with Session(engine) as db:
                set_db_request_context(db, user_id=uuid.UUID(int=0), organization_id=organization)
                document = Document(organization_id=organization, document_key=uuid.uuid4().hex, title="Fixture RLS")
                db.add(document)
                db.flush()
                version = DocumentVersion(organization_id=organization, document_id=document.id, version_number=1,
                    source_filename="test.txt", content_type="text/plain", storage_key=uuid.uuid4().hex,
                    sha256="0" * 64, size_bytes=4)
                db.add(version)
                db.flush()
                db.add(DocumentExtractionJob(organization_id=organization, document_version_id=version.id))
                rows.append((organization, document.id))
                db.commit()
        yield engine, rows
    finally:
        engine.dispose()


def test_document_tables_hide_foreign_rows_and_no_context(document_rows):
    engine, rows = document_rows
    with Session(engine) as db:
        for model in (Document, DocumentVersion, DocumentExtractionJob):
            assert list(db.scalars(select(model.id))) == []
        for organization, document_id in rows:
            set_db_request_context(db, user_id=uuid.UUID(int=0), organization_id=organization)
            for model in (Document, DocumentVersion, DocumentExtractionJob):
                visible = db.scalars(select(model)).all()
                assert len(visible) == 1
                assert visible[0].organization_id == organization
            assert db.get(Document, document_id) is not None
            db.rollback()  # context is transaction-local; don't reuse cached objects
            db.expunge_all()


@pytest.mark.parametrize("operation", ["insert", "move", "update_delete"])
def test_document_cross_tenant_writes_are_refused(document_rows, operation):
    engine, rows = document_rows
    (organization_a, document_a), (organization_b, document_b) = rows
    with Session(engine) as db:
        set_db_request_context(db, user_id=uuid.UUID(int=0), organization_id=organization_a)
        if operation == "update_delete":
            assert db.execute(update(Document).where(Document.id == document_b).values(title="intrusion")).rowcount == 0
            assert db.execute(delete(Document).where(Document.id == document_b)).rowcount == 0
        else:
            with pytest.raises(DBAPIError) as error:
                if operation == "insert":
                    db.add(Document(organization_id=organization_b, document_key=uuid.uuid4().hex, title="intrusion"))
                    db.flush()
                else:
                    db.execute(update(Document).where(Document.id == document_a).values(organization_id=organization_b))
            assert error.value.orig.sqlstate == "42501"  # insufficient_privilege / RLS
        db.rollback()
