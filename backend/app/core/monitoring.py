from __future__ import annotations

import time
from typing import Any

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import Response

# Prometheus Metrics definition
EVALUATION_COUNTER = Counter(
    "vericlaim_evaluations_total",
    "Nombre total d'audits réglementaires exécutés",
    ["compliance", "extraction_method"],
)

VIOLATIONS_COUNTER = Counter(
    "vericlaim_violations_total",
    "Nombre total d'infractions réglementaires constatées",
    ["rule_id"],
)

CLAIMS_COUNTER = Counter(
    "vericlaim_claims_detected_total",
    "Nombre total d'allégations environnementales détectées",
    ["claim_type"],
)

EVALUATION_LATENCY = Histogram(
    "vericlaim_evaluation_duration_seconds",
    "Temps de traitement d'un audit en secondes",
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)


def record_evaluation_metrics(
    compliance: str,
    extraction_method: str,
    duration_sec: float,
    violations: list[str],
    claims: list[str],
) -> None:
    """Record Prometheus metrics for an evaluation."""
    EVALUATION_COUNTER.labels(compliance=compliance, extraction_method=extraction_method).inc()
    EVALUATION_LATENCY.observe(duration_sec)
    for rule_id in violations:
        VIOLATIONS_COUNTER.labels(rule_id=rule_id).inc()
    for claim_type in claims:
        CLAIMS_COUNTER.labels(claim_type=claim_type).inc()


def metrics_response() -> Response:
    """Return raw Prometheus metrics formatted response."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
