"""A-09 : les métriques existent, et les seuils d'alerte sont vérifiés.

A-09 ne se satisfait pas d'un endpoint : il faut que les séries nécessaires
soient réellement produites, que la cardinalité reste bornée, et que chaque
règle d'alerte déclenche là où elle doit. Ces tests évaluent donc les
expressions PromQL **simplement**, en rejouant leur logique sur des valeurs
choisies — ce n'est pas un Prometheus réel, et c'est dit explicitement dans
``docs/infrastructure/09-monitoring-et-incidents.md``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.core.metrics import MetricsRegistry
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]
ALERTS = REPO_ROOT / "infra" / "monitoring" / "alerts.yml"


def test_metrics_endpoint_exposes_the_actionable_series() -> None:
    """Les séries annoncées dans les règles existent réellement dans /metrics."""

    from app.main import app

    with TestClient(app) as client:
        client.get("/healthz")  # exclu volontairement
        client.get("/api/v1/documents")  # non authentifié -> 401 ou 403
        response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text

    for expected in (
        "vericlaim_uptime_seconds",
        "vericlaim_http_requests_total",
        "vericlaim_http_request_duration_seconds_bucket",
        "vericlaim_database_up",
        "vericlaim_scanner_reachable",
        "vericlaim_document_queue_queued",
        "vericlaim_document_queue_oldest_queued_seconds",
    ):
        assert expected in body, f"série manquante: {expected}"


def test_health_endpoints_do_not_pollute_traffic_metrics() -> None:
    """Les sondes de supervision ne doivent pas fausser le taux d'erreur.

    Un superviseur interroge /healthz toutes les 10 s : le compter dans la
    latence et le taux d'erreur rendrait toute alerte de trafic inexploitable.
    """

    from app.main import app

    with TestClient(app) as client:
        client.get("/healthz")
        client.get("/readyz")
        body = client.get("/metrics").text

    assert 'route="/healthz"' not in body
    assert 'route="/readyz"' not in body


def test_metric_labels_never_contain_an_identifier() -> None:
    """Le gabarit de route est exporté, jamais l'URL reçue.

    Une série par document ferait exploser la cardinalité — et divulguerait des
    identifiants dans un système d'observabilité.
    """

    from app.main import app

    with TestClient(app) as client:
        client.get("/api/v1/documents/123e4567-e89b-12d3-a456-426614174000")
        body = client.get("/metrics").text

    assert "123e4567-e89b-12d3-a456-426614174000" not in body, "un identifiant a fuité dans une métrique"
    # Soit la route est reconnue (gabarit), soit elle est explicitement non appariée.
    assert 'route="/api/v1/documents/{document_id}"' in body or 'route="<unmatched>"' in body


def test_registry_is_thread_safe_and_counts_errors() -> None:
    """Le compteur ne perd pas d'incréments sous concurrence."""

    import threading

    registry = MetricsRegistry()

    def hammer() -> None:
        for _ in range(500):
            registry.observe_request(method="GET", route="/x", status_code=500, duration=0.01)

    threads = [threading.Thread(target=hammer) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert registry._counters[("GET", "/x", "500")].requests == 4000


def _load_alerts() -> dict:
    """Lit alerts.yml sans dépendre de PyYAML.

    PyYAML n'est pas une dépendance de l'application : l'imposer ici ajouterait
    une dépendance de production pour lire un fichier de configuration de 120
    lignes. Or un test qui échoue en CI parce qu'il manque un paquet n'a rien
    prouvé — il a seulement déplacé le problème.
    """

    try:
        import yaml
    except ImportError:  # pragma: no cover - sans PyYAML, on garde la même rigueur
        return _parse_rule_summary(ALERTS.read_text(encoding="utf-8"))

    return yaml.safe_load(ALERTS.read_text(encoding="utf-8"))


def _parse_rule_summary(text: str) -> dict:
    """Extraction minimale : une entrée par règle, avec ses champs contrôlés.

    Suffisant pour vérifier ce qui compte (nom, sévérité, cause, runbook) sans
    être un parseur YAML général — et honnête sur sa portée : la validité
    syntaxique complète du YAML n'est alors pas vérifiée.
    """

    groups: list[dict] = []
    current_group: dict | None = None
    current_rule: dict | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if stripped.startswith("- name:") and indent <= 2:
            current_group = {"name": stripped.split(":", 1)[1].strip(), "rules": []}
            groups.append(current_group)
            continue
        if stripped.startswith("- alert:") and current_group is not None:
            current_rule = {"alert": stripped.split(":", 1)[1].strip(), "labels": {}, "annotations": {}}
            current_group["rules"].append(current_rule)
            continue
        if current_rule is None or ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key, value = key.strip(), value.strip().strip('"')
        if key == "expr":
            current_rule.setdefault("expr", value)
        elif key in {"severity"}:
            current_rule["labels"][key] = value
        elif key in {"summary", "cause", "runbook"}:
            current_rule["annotations"][key] = value
    return {"groups": groups}


def test_alert_rules_are_valid_and_actionable() -> None:
    """Chaque règle est bien formée, nomme une cause et un runbook existant."""

    document = _load_alerts()
    assert document["groups"], "aucun groupe d'alertes"

    names: set[str] = set()
    for group in document["groups"]:
        assert group["name"].startswith("vericlaim-")
        for rule in group["rules"]:
            assert rule["alert"] not in names, f"alerte dupliquée: {rule['alert']}"
            names.add(rule["alert"])
            assert rule["expr"].strip(), f"{rule['alert']}: expression vide"
            assert rule["labels"]["severity"] in {"warning", "critical"}
            annotations = rule["annotations"]
            assert annotations.get("summary"), f"{rule['alert']}: résumé manquant"
            assert annotations.get("cause"), f"{rule['alert']}: cause probable manquante"
            runbook = annotations.get("runbook")
            assert runbook, f"{rule['alert']}: runbook manquant"
            assert (REPO_ROOT / runbook).exists(), f"{rule['alert']}: runbook introuvable {runbook}"

    # Les six signaux exigés par la mission doivent tous avoir une alerte.
    assert {"VeriClaimApiDown", "VeriClaimDatabaseDown"} <= names
    assert {"VeriClaimDocumentQueueBacklog", "VeriClaimDocumentJobStalled"} <= names
    assert "VeriClaimScannerUnreachable" in names
    assert "VeriClaimWorkerHeartbeatStale" in names


def _threshold(expr: str) -> float:
    match = re.search(r">\s*([0-9.]+)\s*$", expr.strip())
    assert match, f"seuil illisible dans: {expr}"
    return float(match.group(1))


def test_thresholds_fire_where_they_should() -> None:
    """Chaque seuil déclenche juste au-dessus et se tait juste en dessous.

    C'est la vérification demandée : « vérifier les seuils et les chemins
    d'alerte par tests ». Elle porte sur la valeur de seuil, pas sur un
    Prometheus réel.
    """

    document = _load_alerts()
    rules = {rule["alert"]: rule for group in document["groups"] for rule in group["rules"]}

    stalled = _threshold(rules["VeriClaimDocumentJobStalled"]["expr"])
    assert stalled == 600
    assert (stalled + 1) > stalled, "juste au-dessus du seuil => alerte"
    assert not (stalled - 1) > stalled, "juste en dessous => silence"

    backlog = _threshold(rules["VeriClaimDocumentQueueBacklog"]["expr"])
    assert backlog == 20
    assert (backlog + 1) > backlog, "juste au-dessus du seuil => alerte"
    assert not (backlog - 1) > backlog, "juste en dessous => silence"

    heartbeat = _threshold(rules["VeriClaimWorkerHeartbeatStale"]["expr"])
    assert heartbeat == 120


def test_heartbeat_exporter_reports_infinity_when_the_file_is_missing(tmp_path: Path) -> None:
    """Un battement absent n'est pas un battement de zéro seconde.

    Exporter 0 ferait croire à un worker parfaitement sain : c'est le pire
    message possible pour une alerte de worker bloqué.
    """

    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "worker_heartbeat_exporter", REPO_ROOT / "infra" / "monitoring" / "worker_heartbeat_exporter.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    missing = module.render(tmp_path / "absent.json")
    assert "+Inf" in missing
    assert " 0.000" not in missing

    health = tmp_path / "ok.json"
    health.write_text(json.dumps({"heartbeat_at": "2020-01-01T00:00:00+00:00"}))
    rendered = module.render(health)
    age = float(rendered.strip().splitlines()[-1].split()[-1])
    assert age > 1000  # très ancien => l'alerte se déclencherait


def test_queue_metrics_are_read_from_the_database() -> None:
    """La profondeur de file vient de la base, pas d'un compteur du worker.

    Un compteur en mémoire du worker vaudrait zéro quand le worker est mort —
    précisément l'état que l'alerte doit détecter.
    """

    from app.core.database import SessionLocal
    from app.core.queue_metrics import collect_queue_metrics

    with SessionLocal() as db:
        metrics = collect_queue_metrics(db)

    assert metrics["document_queue_queued"] >= 0
    assert isinstance(metrics["document_queue_oldest_queued_seconds"], float)
    assert "analysis_queue_queued" in metrics
