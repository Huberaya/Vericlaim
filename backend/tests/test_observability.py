"""C16 — observability: correlation, metrics, real alerts.

The chantier exists because `/api/v1/enterprise/metrics` returned literals
(`average_analysis_latency_ms: 142.5`, `error_rate_percent: 0.02`,
`memory_usage_mb: 128.4`, `cpu_utilization_percent: 4.2`, `total_api_requests =
total_events + 100`) and `/api/v1/enterprise/alerts` returned two hardcoded alerts
already marked as acknowledged. Nothing was counted, correlated or measured.

Each test below targets one property that makes the replacement trustworthy:

* a log line carries the correlation identifier of the request that produced it;
* a client-supplied identifier cannot forge a log line;
* the metrics endpoint measures *this* process and says so;
* business gauges come from the database, not from a counter in one replica;
* metrics do not leak tenant data — that is what makes an unauthenticated scrape
  acceptable;
* alerts are computed, can go red, and are never auto-acknowledged.
"""

from __future__ import annotations

import json
import logging
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal, create_tables
from app.core.logging import JsonLogFormatter, bind_log_context, normalize_request_id
from app.core.metrics import REGISTRY
from app.main import app
from tests.auth_support import authenticate_client


@pytest.fixture()
def client():
    create_tables()
    with TestClient(app) as test_client:
        yield test_client


# --------------------------------------------------------------------------- #
# Correlation
# --------------------------------------------------------------------------- #


def test_a_request_identifier_is_generated_and_echoed(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.headers.get("X-Request-Id"), "aucun identifiant de corrélation renvoyé"


def test_a_client_supplied_identifier_is_accepted_when_it_is_safe(client):
    response = client.get("/healthz", headers={"X-Request-Id": "support-2026-09-30-42"})
    assert response.headers["X-Request-Id"] == "support-2026-09-30-42"


def test_a_client_supplied_identifier_cannot_forge_a_log_line(client):
    """An identifier that could inject newlines or JSON would poison the log stream."""
    for dangerous in (
        'abc"level":"critical',
        "abc\ndef",
        "abc def",
        "a" * 200,
        "abc{}",
    ):
        response = client.get("/healthz", headers={"X-Request-Id": dangerous})
        echoed = response.headers["X-Request-Id"]
        assert echoed != dangerous, f"identifiant accepté malgré : {dangerous!r}"
        assert normalize_request_id(dangerous) is None


def test_the_correlation_context_is_attached_to_every_log_line():
    """The whole point: a service that never sees the request still logs the id."""
    bind_log_context(request_id="req-test-1", organization_id="org-test-1", user_id="user-test-1")
    record = logging.LogRecord("vericlaim.test", logging.INFO, __file__, 1, "message", None, None)
    payload = json.loads(JsonLogFormatter(service="test").format(record))
    assert payload["request_id"] == "req-test-1"
    assert payload["organization_id"] == "org-test-1"
    assert payload["user_id"] == "user-test-1"
    assert payload["message"] == "message"
    assert payload["level"] == "INFO"


def test_credentials_are_never_written_to_a_log():
    """The password work of C14 would be undone by one careless log statement."""
    record = logging.LogRecord("vericlaim.test", logging.INFO, __file__, 1, "auth", None, None)
    record.password = "Chaussette-Verte-2026"
    record.api_key = "vcl_live_0000000000"
    record.authorization = "Bearer secret"
    record.organization_id = "org-1"
    payload = json.loads(JsonLogFormatter(service="test").format(record))
    serialized = json.dumps(payload)
    assert "Chaussette-Verte-2026" not in serialized
    assert "vcl_live_0000000000" not in serialized
    assert "Bearer secret" not in serialized
    assert payload["password"] == "<masqué>"
    assert payload["organization_id"] == "org-1", "le contexte légitime doit survivre"


def test_the_json_format_is_stable_and_parseable():
    record = logging.LogRecord("vericlaim.test", logging.WARNING, __file__, 1, "attention", None, None)
    record.detail = {"count": 3, "label": "x" * 3000}
    payload = json.loads(JsonLogFormatter(service="test").format(record))
    assert payload["detail"]["count"] == 3
    assert len(payload["detail"]["label"]) < 2500, "une valeur trop longue doit être tronquée"


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #


def test_the_metrics_endpoint_exposes_the_prometheus_format(client):
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    assert "# TYPE vericlaim_http_requests_total counter" in body
    assert "# TYPE vericlaim_http_request_duration_seconds histogram" in body
    assert "vericlaim_document_extraction_queue_depth" in body


def test_requests_are_counted_with_a_route_template_not_a_raw_url(client):
    authenticate_client(client, role_code="owner")
    created = client.post(
        "/api/v1/documents",
        json={"title": "Document mesuré", "document_type": "other"},
        headers={"Idempotency-Key": "c16-metrics"},
    )
    assert created.status_code == 201
    document_id = created.json()["id"]
    client.get(f"/api/v1/documents/{document_id}")

    body = client.get("/metrics").text
    series = [line for line in body.splitlines() if line.startswith("vericlaim_http_requests_total{")]
    assert any('route="/api/v1/documents/{document_id}"' in line for line in series), (
        "la route doit apparaître sous forme de gabarit"
    )
    assert not any(document_id in line for line in series), (
        "un identifiant par requête ferait exploser la cardinalité des métriques"
    )


def test_business_gauges_are_read_from_the_database(client):
    """Queue depth is a question asked to the database, not a counter in one replica.

    The expected values are counted with the same SQL predicate, so the test measures
    the relationship instead of a literal that a leftover row would invalidate.
    """
    from sqlalchemy import func, select

    from app.models.domain import (
        AnalysisDetectionJob,
        AnalysisDetectionJobStatus,
        Document,
        DocumentExtractionJob,
        DocumentExtractionJobStatus,
        DocumentVersion,
    )

    authenticate_client(client, role_code="owner")

    # A virgin database would make this test vacuous (0 == 0). Three real queued jobs
    # are created first, so the gauge has something to be wrong about.
    with SessionLocal() as db:
        organization_id = db.query(Document.organization_id).first()
        organization_id = organization_id[0] if organization_id else None
        if organization_id is None:
            raise AssertionError("l'organisation de test doit exister")
        for index in range(3):
            document = Document(
                organization_id=organization_id,
                document_key=f"c16-gauge-{uuid4().hex[:12]}",
                title=f"En attente {index}",
                document_type="other",
            )
            db.add(document)
            db.flush()
            version = DocumentVersion(
                organization_id=organization_id,
                document_id=document.id,
                version_number=1,
                source_filename="attente.pdf",
                content_type="application/pdf",
                size_bytes=1,
                sha256="4" * 64,
                storage_key=f"c16/gauge-{uuid4().hex}",
            )
            db.add(version)
            db.flush()
            db.add(
                DocumentExtractionJob(
                    organization_id=organization_id,
                    document_version_id=version.id,
                    status=DocumentExtractionJobStatus.QUEUED,
                )
            )
        db.commit()

    with SessionLocal() as db:
        extraction = int(
            db.scalar(
                select(func.count(DocumentExtractionJob.id)).where(
                    DocumentExtractionJob.status.in_(
                        [DocumentExtractionJobStatus.QUEUED, DocumentExtractionJobStatus.RUNNING]
                    )
                )
            )
            or 0
        )
        detection = int(
            db.scalar(
                select(func.count(AnalysisDetectionJob.id)).where(
                    AnalysisDetectionJob.status.in_(
                        [AnalysisDetectionJobStatus.QUEUED, AnalysisDetectionJobStatus.RUNNING]
                    )
                )
            )
            or 0
        )
    assert extraction >= 3, "le test doit avoir de quoi mesurer"

    body = client.get("/metrics").text
    assert f'vericlaim_document_extraction_queue_depth{{queue="document_extraction"}} {extraction}' in body
    assert f'vericlaim_analysis_detection_queue_depth{{queue="analysis_detection"}} {detection}' in body
    assert "vericlaim_oldest_pending_job_age_seconds" in body
    # A failure rate that reads zero while jobs are failing would be the same defect
    # one level down: `jobs_failed` counts definitive failures, and a retryable one is
    # not in it. Both are published, and they are not the same number.
    assert "vericlaim_jobs_failed{state=\"failed\"}" in body
    assert 'vericlaim_job_failures_total{queue="document_extraction"}' in body
    assert 'vericlaim_job_failures_total{queue="analysis_detection"}' in body


def test_the_metrics_endpoint_leaks_no_tenant_identifier(client):
    """This is the property that makes an unauthenticated /metrics acceptable."""
    authenticate_client(client, role_code="owner")
    created = client.post(
        "/api/v1/documents",
        json={"title": "Titre confidentiel Gamma", "document_type": "other"},
        headers={"Idempotency-Key": "c16-leak"},
    )
    assert created.status_code == 201
    document_id = created.json()["id"]
    # A request whose *path* carries an identifier, so a raw-URL label would leak it.
    detail = client.get(f"/api/v1/documents/{document_id}")
    assert detail.status_code == 200, detail.text
    session = client.get("/api/v1/auth/me")
    organization_id = session.json()["active_organization_id"]
    user_email = session.json()["user"]["email"]

    with TestClient(app) as anonymous:
        body = anonymous.get("/metrics").text
    for forbidden in (document_id, organization_id, user_email, "Titre confidentiel Gamma"):
        assert forbidden not in body, f"{forbidden} apparaît dans /metrics"


def test_counter_values_are_measurable_and_reset(tmp_path):
    REGISTRY.reset()
    REGISTRY.increment("vericlaim_test_counter", {"k": "v"})
    assert REGISTRY.counter_total("vericlaim_test_counter") == 1
    REGISTRY.observe("vericlaim_test_histogram", 0.2)
    snapshot = REGISTRY.histogram_snapshot("vericlaim_test_histogram")
    assert snapshot["count"] == 1 and snapshot["sum"] == pytest.approx(0.2)
    REGISTRY.reset()
    assert REGISTRY.counter_total("vericlaim_test_counter") == 0


# --------------------------------------------------------------------------- #
# Alerts
# --------------------------------------------------------------------------- #


def test_a_saturated_queue_produces_a_critical_alert(client):
    """The alert the plan asks for, produced by a real measurement."""
    from datetime import datetime, timedelta, timezone

    from app.core.alerting import QUEUE_DEPTH_CRITICAL, evaluate_organization_alerts

    authenticate_client(client, role_code="owner")
    session = client.get("/api/v1/auth/me").json()
    organization_id = session["active_organization_id"]

    with SessionLocal() as db:
        from uuid import UUID

        # Real documents, versions and jobs, through the product's own models. One job
        # per version (the schema forbids more), so fifty documents are created: the
        # gauge counts rows in the database, and the test measures that.
        from app.models.domain import (
            Document,
            DocumentExtractionJob,
            DocumentExtractionJobStatus,
            DocumentVersion,
        )

        for index in range(QUEUE_DEPTH_CRITICAL):
            document = Document(
                organization_id=UUID(organization_id),
                document_key=f"c16-{uuid4().hex[:12]}",
                title=f"Document en file {index}",
                document_type="other",
            )
            db.add(document)
            db.flush()
            version = DocumentVersion(
                organization_id=UUID(organization_id),
                document_id=document.id,
                version_number=1,
                source_filename="declaration.pdf",
                content_type="application/pdf",
                size_bytes=1234,
                sha256="0" * 64,
                storage_key=f"c16/{uuid4().hex}",
            )
            db.add(version)
            db.flush()
            db.add(
                DocumentExtractionJob(
                    organization_id=UUID(organization_id),
                    document_version_id=version.id,
                    status=DocumentExtractionJobStatus.QUEUED,
                    available_at=datetime.now(timezone.utc) - timedelta(minutes=30),
                    created_at=datetime.now(timezone.utc) - timedelta(minutes=30),
                )
            )
        db.commit()
        alerts = evaluate_organization_alerts(db, organization_id=UUID(organization_id))

    titles = [alert.title for alert in alerts]
    assert any("saturée" in title for title in titles), titles
    saturated = next(alert for alert in alerts if "saturée" in alert.title)
    assert saturated.severity == "critical"
    assert saturated.observed_value == QUEUE_DEPTH_CRITICAL
    assert saturated.threshold == QUEUE_DEPTH_CRITICAL


def test_a_broken_audit_chain_raises_a_critical_alert(client):
    from uuid import UUID

    from sqlalchemy import select

    from app.core.alerting import evaluate_organization_alerts
    from app.identity.service import append_audit_event
    from app.models.domain import AuditEvent

    authenticate_client(client, role_code="owner")
    organization_id = UUID(client.get("/api/v1/auth/me").json()["active_organization_id"])

    with SessionLocal() as db:
        # Write a real event through the product's own function, then corrupt it: the
        # alert must react to a broken chain, not to a hand-built row.
        append_audit_event(
            db,
            organization_id=organization_id,
            actor_user_id=None,
            entity_type="organization",
            entity_id=organization_id,
            action="c16.test_event",
            payload={"purpose": "provoquer une alerte d'intégrité"},
        )
        db.commit()
        event = db.scalar(
            select(AuditEvent)
            .where(AuditEvent.organization_id == organization_id, AuditEvent.action == "c16.test_event")
            .limit(1)
        )
        assert event is not None, "l'événement témoin n'a pas été écrit"
        event.event_hash = "0" * 64  # un hash impossible : la chaîne ne vérifie plus
        db.commit()

        alerts = evaluate_organization_alerts(db, organization_id=organization_id)

    broken = [alert for alert in alerts if alert.category == "integrity"]
    assert broken, [alert.title for alert in alerts]
    assert broken[0].severity == "critical"
    assert broken[0].title == "Chaîne d'audit rompue"


def test_the_alerts_endpoint_separates_scopes_and_publishes_rules(client):
    authenticate_client(client, role_code="owner")
    response = client.get("/api/v1/ops/alerts")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"organization_alerts", "platform_alerts", "note"}
    assert all(alert["scope"] == "organization" for alert in body["organization_alerts"])
    assert all(alert["scope"] == "platform" for alert in body["platform_alerts"])
    assert all(alert["is_acknowledged"] is False for alert in body["organization_alerts"])

    rules = client.get("/api/v1/ops/alert-rules").json()
    assert any(rule["id"] == "queue-depth" for rule in rules["rules"])
    assert rules["not_implemented"], (
        "les alertes sans notification sortante doivent être déclarées comme telles"
    )


def test_a_viewer_cannot_read_the_alerts(client):
    authenticate_client(client, role_code="viewer")
    assert client.get("/api/v1/ops/alerts").status_code == 403
    assert client.post(
        "/api/v1/ops/client-errors", json={"message": "x", "route": "/app"}
    ).status_code == 403


def test_a_frontend_error_is_recorded_with_correlation(client, caplog):
    authenticate_client(client, role_code="owner")
    with caplog.at_level(logging.ERROR, logger="vericlaim.ops"):
        response = client.post(
            "/api/v1/ops/client-errors",
            json={
                "message": "TypeError: cannot read property of undefined",
                "route": "/app",
                "error_name": "TypeError",
                "request_id": "front-42",
            },
        )
    assert response.status_code == 202, response.text
    assert any("Frontend error reported" in record.getMessage() for record in caplog.records)
    entry = next(record for record in caplog.records if record.getMessage() == "Frontend error reported")
    assert getattr(entry, "frontend_route") == "/app"


def test_the_frontend_error_endpoint_is_bounded(client):
    authenticate_client(client, role_code="owner")
    too_long = client.post(
        "/api/v1/ops/client-errors", json={"message": "x" * 501, "route": "/app"}
    )
    assert too_long.status_code == 422
    unknown_field = client.post(
        "/api/v1/ops/client-errors",
        json={"message": "ok", "route": "/app", "password": "Chaussette-Verte-2026"},
    )
    assert unknown_field.status_code == 422, (
        "un champ inconnu doit être refusé : c'est ainsi qu'un mot de passe finirait dans un log"
    )


def test_worker_heartbeats_never_make_the_api_mistake_itself_for_a_worker(client):
    """A dead document worker must not be reported "active" because the API is alive."""
    from app.core.metrics import REGISTRY, record_worker_heartbeat, worker_heartbeats

    authenticate_client(client, role_code="owner")

    # Only the API has beaten: the instance knows nothing about its workers, and must
    # say so instead of extrapolating from its own pulse.
    REGISTRY.reset()
    record_worker_heartbeat(worker_kind="api", worker_id="api-1")
    metrics = client.get("/api/v1/enterprise/metrics").json()
    assert metrics["workers_status"] == "unknown", metrics["status_reasons"]

    # A real worker beats: now the instance can affirm something.
    record_worker_heartbeat(worker_kind="document_extraction", worker_id="worker-test")
    heartbeats = list(worker_heartbeats())
    assert any(labels.get("worker_kind") == "document_extraction" for labels, _ in heartbeats)
    metrics = client.get("/api/v1/enterprise/metrics").json()
    assert metrics["workers_status"] == "active"


def test_a_stalled_queue_is_detected_even_when_shallow(client):
    """Depth says nothing about movement: one stuck job for an hour is an incident."""
    from datetime import datetime, timedelta, timezone
    from uuid import UUID

    from app.core.alerting import QUEUE_STALL_SECONDS, evaluate_organization_alerts
    from app.models.domain import (
        Document,
        DocumentExtractionJob,
        DocumentExtractionJobStatus,
        DocumentVersion,
    )

    authenticate_client(client, role_code="owner")
    organization_id = UUID(client.get("/api/v1/auth/me").json()["active_organization_id"])

    with SessionLocal() as db:
        document = Document(
            organization_id=organization_id,
            document_key=f"c16-{uuid4().hex[:12]}",
            title="Travail bloqué",
            document_type="other",
        )
        db.add(document)
        db.flush()
        version = DocumentVersion(
            organization_id=organization_id,
            document_id=document.id,
            version_number=1,
            source_filename="bloque.pdf",
            content_type="application/pdf",
            size_bytes=10,
            sha256="1" * 64,
            storage_key=f"c16/{uuid4().hex}",
        )
        db.add(version)
        db.flush()
        db.add(
            DocumentExtractionJob(
                organization_id=organization_id,
                document_version_id=version.id,
                status=DocumentExtractionJobStatus.QUEUED,
                created_at=datetime.now(timezone.utc) - timedelta(seconds=QUEUE_STALL_SECONDS + 60),
            )
        )
        db.commit()
        alerts = evaluate_organization_alerts(db, organization_id=organization_id)

    stalled = [alert for alert in alerts if alert.id == "queue-stalled"]
    assert stalled, [alert.id for alert in alerts]
    assert stalled[0].severity == "critical"
    assert stalled[0].observed_value >= QUEUE_STALL_SECONDS
    assert stalled[0].threshold == QUEUE_STALL_SECONDS
    # The queue is at most a few jobs deep: no depth alert may fire. Otherwise the
    # alert would be about the wrong thing.
    assert not [alert for alert in alerts if alert.id.startswith("queue-depth")]


def test_repeated_failures_are_graded_by_the_worker_threshold(client):
    """The severity comes from the worker's own rule (`attempt_count >= max_attempts`).

    A failure with attempts left is a warning: the worker will retry. A failure that
    exhausted its attempts is definitive and must be critical. No invented threshold.
    """
    from uuid import UUID

    from app.core.alerting import evaluate_organization_alerts
    from app.models.domain import (
        Analysis,
        AnalysisDetectionJob,
        AnalysisDetectionJobStatus,
        AnalysisStatus,
        AnalysisVersion,
        Document,
        DocumentExtractionJob,
        DocumentExtractionJobStatus,
        DocumentVersion,
    )

    authenticate_client(client, role_code="owner")
    organization_id = UUID(client.get("/api/v1/auth/me").json()["active_organization_id"])

    with SessionLocal() as db:
        document = Document(
            organization_id=organization_id,
            document_key=f"c16-{uuid4().hex[:12]}",
            title="Échec répété",
            document_type="other",
        )
        db.add(document)
        db.flush()
        version = DocumentVersion(
            organization_id=organization_id,
            document_id=document.id,
            version_number=1,
            source_filename="echoue.pdf",
            content_type="application/pdf",
            size_bytes=10,
            sha256="2" * 64,
            storage_key=f"c16/{uuid4().hex}",
        )
        db.add(version)
        db.flush()
        extraction_job = DocumentExtractionJob(
            organization_id=organization_id,
            document_version_id=version.id,
            status=DocumentExtractionJobStatus.FAILED,
            attempt_count=1,
            max_attempts=3,
            error_code="extraction_failed",
        )
        analysis = Analysis(
            organization_id=organization_id,
            analysis_key=f"C16-{uuid4().hex[:8]}",
            status=AnalysisStatus.FAILED,
        )
        db.add(analysis)
        db.flush()
        analysis_version = AnalysisVersion(
            organization_id=organization_id,
            analysis_id=analysis.id,
            version_number=1,
            engine_version="test",
            rulebook_version="test",
            input_manifest_sha256="3" * 64,
            status=AnalysisStatus.FAILED,
        )
        db.add(analysis_version)
        db.flush()
        detection_job = AnalysisDetectionJob(
            organization_id=organization_id,
            analysis_version_id=analysis_version.id,
            status=AnalysisDetectionJobStatus.FAILED,
            attempt_count=1,
            max_attempts=3,
            error_code="job_timeout",
        )
        db.add_all([extraction_job, detection_job])
        db.commit()

        alerts = evaluate_organization_alerts(db, organization_id=organization_id)
        extraction_alert = next(a for a in alerts if "extraction documentaire" in a.title)
        detection_alert = next(a for a in alerts if "détection" in a.title)
        assert extraction_alert.severity == "warning"
        assert detection_alert.severity == "warning"
        assert extraction_alert.observed_value == 1

        # The worker retried and gave up: the same two jobs become critical.
        extraction_job.attempt_count = extraction_job.max_attempts
        detection_job.attempt_count = detection_job.max_attempts
        db.commit()
        alerts = evaluate_organization_alerts(db, organization_id=organization_id)

    extraction_alert = next(a for a in alerts if "extraction documentaire" in a.title)
    detection_alert = next(a for a in alerts if "détection" in a.title)
    assert extraction_alert.severity == "critical"
    assert detection_alert.severity == "critical"
    assert "épuisé" in str(extraction_alert.threshold)


def test_an_unreachable_antivirus_produces_a_platform_alert_and_fails_readiness():
    """Alerts and `/readyz` must tell the same story: they read the same probes."""
    from dataclasses import replace

    from app.core.alerting import evaluate_platform_alerts
    from app.core.config import settings
    from app.core.readiness import build_readiness_report
    from tests.document_support import FakeScanner

    configured = replace(
        settings, document_scanner_mode="clamav", document_clamav_host="antivirus.interne"
    )
    scanner = FakeScanner(available=False)

    report = build_readiness_report(configured, storage=None, scanner=scanner)
    assert report.ready is False, "un antivirus injoignable doit rendre l'instance non prête"

    alerts = evaluate_platform_alerts(configured, storage=None, scanner=scanner)
    scanner_alerts = [alert for alert in alerts if alert.id == "platform-malware_scanner"]
    assert scanner_alerts, [alert.id for alert in alerts]
    assert scanner_alerts[0].severity == "critical"
    assert scanner_alerts[0].scope == "platform", (
        "une panne d'antivirus est une alerte d'instance : la présenter comme propre au "
        "client lui ferait croire qu'il est le seul concerné"
    )
    assert "instance" in scanner_alerts[0].message


def test_a_storage_without_probe_is_reported_failed_not_believed():
    """Fail closed: an unverifiable dependency is not a healthy dependency."""
    from dataclasses import replace

    from app.core.alerting import evaluate_platform_alerts
    from app.core.config import settings

    class AnonymousStorage:
        """An adapter with no `probe`: it can be used, but not verified."""

    configured = replace(settings, document_storage_backend="s3", document_scanner_mode="disabled")
    alerts = evaluate_platform_alerts(configured, storage=AnonymousStorage(), scanner=None)
    storage_alerts = [alert for alert in alerts if alert.id == "platform-document_storage"]
    assert storage_alerts, [alert.id for alert in alerts]
    assert storage_alerts[0].severity == "critical"
    assert "sonde" in storage_alerts[0].message


def test_a_failed_job_leaves_a_line_in_the_logs(client, caplog):
    """A job that fails must exist in the logs, not only in the database.

    This test exists because the evidence harness caught the opposite: the worker
    recorded the failure and published the metric, but wrote nothing. An operator
    reading the log stream would have seen a perfectly quiet worker while documents
    were failing.
    """
    from uuid import UUID

    from sqlalchemy import delete

    from app.documents.storage import build_object_storage
    from app.core.config import settings as runtime_settings
    from app.models.domain import (
        Document,
        DocumentExtractionJob,
        DocumentExtractionJobStatus,
        DocumentVersion,
    )
    from app.workers.document_extraction_worker import DocumentExtractionWorker

    authenticate_client(client, role_code="owner")
    organization_id = UUID(client.get("/api/v1/auth/me").json()["active_organization_id"])

    with SessionLocal() as db:
        # The worker claims jobs round-robin across *all* organizations, and earlier
        # tests in this file leave queued jobs behind. They are removed here so the
        # test measures its own job instead of whichever tenant came first.
        db.execute(
            delete(DocumentExtractionJob).where(
                DocumentExtractionJob.status == DocumentExtractionJobStatus.QUEUED
            )
        )
        document = Document(
            organization_id=organization_id,
            document_key=f"c16-log-{uuid4().hex[:12]}",
            title="Travail en échec",
            document_type="other",
        )
        db.add(document)
        db.flush()
        version = DocumentVersion(
            organization_id=organization_id,
            document_id=document.id,
            version_number=1,
            source_filename="echec.pdf",
            content_type="application/pdf",
            size_bytes=10,
            sha256="5" * 64,
            storage_key=f"c16/log-{uuid4().hex}",
        )
        db.add(version)
        db.flush()
        db.add(
            DocumentExtractionJob(
                organization_id=organization_id,
                document_version_id=version.id,
                status=DocumentExtractionJobStatus.QUEUED,
            )
        )
        db.commit()

    # The default configuration has no object storage, so a real job really fails —
    # no failure is injected to make the test pass.
    assert runtime_settings.document_storage_backend == "disabled", (
        "ce test suppose l'absence de stockage objet ; l'adapter si la configuration change"
    )
    worker = DocumentExtractionWorker(
        settings=runtime_settings,
        storage=build_object_storage(runtime_settings),
        worker_id="c16-test",
    )
    with caplog.at_level(logging.WARNING, logger="app.workers.document_extraction_worker"):
        found = worker.run_once()
    assert found is True, "aucun travail n'a été réclamé : le test ne prouverait rien"

    failed_lines = [
        record for record in caplog.records if getattr(record, "event", None) == "job_failed"
    ]
    assert failed_lines, "un travail en échec n'a laissé aucune ligne de journal"
    line = failed_lines[-1]
    assert getattr(line, "organization_id") == str(organization_id)
    assert getattr(line, "job_id"), "la ligne ne permet pas de retrouver le travail"
    assert getattr(line, "error_code") == "clean_storage_unavailable"
    assert line.levelname == "WARNING", "un échec réessayable et un échec définitif ne se lisent pas pareil"
