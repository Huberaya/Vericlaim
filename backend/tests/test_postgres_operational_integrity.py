"""Real PostgreSQL runtime-role tests, not Neon or external service proofs."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.audit.service import verify_audit_chain
from app.identity.service import append_audit_event, set_db_request_context
from app.models.domain import Document
from tests.test_postgres_tenant_isolation import ADMIN_DSN, APP_ROLE, APP_PASSWORD, _sqlalchemy_url, cluster, tenants

pytestmark = pytest.mark.skipif(not ADMIN_DSN, reason="Disposable PostgreSQL URL not configured")


@pytest.fixture()
def runtime_engine(cluster):
    engine = create_engine(_sqlalchemy_url(ADMIN_DSN, dbname=cluster.database, user=APP_ROLE, password=APP_PASSWORD))
    try:
        yield engine
    finally:
        engine.dispose()


def context(db, organization):
    set_db_request_context(db, user_id=UUID(int=0), organization_id=organization)


def test_runtime_tenant_context_survives_commit_and_rollback(runtime_engine, tenants):
    with Session(runtime_engine) as db:
        context(db, tenants.organization_a)
        db.add(Document(organization_id=tenants.organization_a, document_key=uuid4().hex, title="Commit boundary"))
        db.commit()
        assert db.scalar(select(func.count()).select_from(Document)) == 1
        db.rollback()
        assert db.scalar(select(func.count()).select_from(Document)) == 1
    with Session(runtime_engine) as db:
        # A different Session must not inherit the pool connection's prior tenant.
        assert db.scalar(select(func.count()).select_from(Document)) == 0


def test_concurrent_genesis_audit_writers_serialize(runtime_engine, tenants):
    first_written, release_first = Event(), Event()

    def append_first():
        with Session(runtime_engine) as db:
            context(db, tenants.organization_a)
            append_audit_event(db, organization_id=tenants.organization_a, actor_user_id=None,
                entity_type="test", entity_id=None, action="first", payload={})
            first_written.set()
            assert release_first.wait(8)
            db.commit()

    def append_second():
        with Session(runtime_engine) as db:
            context(db, tenants.organization_a)
            append_audit_event(db, organization_id=tenants.organization_a, actor_user_id=None,
                entity_type="test", entity_id=None, action="second", payload={})
            db.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(append_first)
        try:
            if not first_written.wait(5):
                first.result(timeout=1)  # surface DB/permission failures, not a misleading wait assertion
                raise AssertionError("first writer did not append")
            second = pool.submit(append_second)
            # First row is still uncommitted: SELECT FOR UPDATE of the latest
            # row alone cannot serialize genesis. The second append must wait.
            with pytest.raises(TimeoutError):
                second.result(timeout=1)
        finally:
            release_first.set()
        first.result(timeout=5)
        second.result(timeout=5)
    with Session(runtime_engine) as db:
        context(db, tenants.organization_a)
        verification = verify_audit_chain(db, tenants.organization_a)
        assert verification.is_valid, verification.error_detail
        assert verification.total_events == 2


def test_engine_audit_envelopes_work_without_update_grants(runtime_engine, tenants):
    from app.core.database import append_audit_record
    with Session(runtime_engine) as db:
        context(db, tenants.organization_a)
        previous, first, _ = append_audit_record(db, organization_id=tenants.organization_a, audit_id=str(uuid4()),
            source_sha256="a" * 64, evidence_manifest_sha256="b" * 64, report_sha256="c" * 64, summary={})
        assert previous is None
        db.commit()
        previous, second, _ = append_audit_record(db, organization_id=tenants.organization_a, audit_id=str(uuid4()),
            source_sha256="a" * 64, evidence_manifest_sha256="b" * 64, report_sha256="c" * 64, summary={})
        assert previous == first and second != first
        db.commit()
