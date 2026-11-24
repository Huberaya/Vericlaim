"""E-02 — la limitation de débit existe, est mesurable et refuse réellement.

Trois niveaux : le limiteur à fenêtre fixe (unité), le middleware sur les
mutations cookie/anonymes, et la limite contractuelle par clé API. Le
commutateur global est désactivé par défaut en APP_ENV=test ; ces tests
l'activent explicitement avec des limites minuscules.
"""

from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.rate_limit import LIMITER, RateLimiter
from app.main import app
from tests.auth_support import authenticate_client


def test_fixed_window_limiter_unit():
    limiter = RateLimiter()
    now = 1_000_000.0
    results = [limiter.check("bucket", "key", limit=3, now=now + i) for i in range(4)]
    assert [r[0] for r in results] == [True, True, True, False]
    assert results[2][1] == 0  # dernier slot consommé
    assert results[3][2] > 0  # retry_after strictement positif
    # La fenêtre suivante réouvre le budget.
    allowed, remaining, _ = limiter.check("bucket", "key", limit=3, now=now + 61)
    assert allowed and remaining == 2
    # Les seaux et les clés sont indépendants.
    assert limiter.check("bucket", "other", limit=3, now=now)[0] is True
    assert limiter.check("other", "key", limit=3, now=now)[0] is True


def test_middleware_refuses_after_the_configured_budget():
    active = replace(
        settings,
        api_rate_limit_enabled=True,
        api_rate_limit_auth_per_minute=3,
        api_rate_limit_evaluate_per_minute=2,
        api_rate_limit_write_per_minute=5,
    )
    previous = app.state.settings
    app.state.settings = active
    LIMITER.reset()
    try:
        with TestClient(app) as client:
            # Les mutations auth sont bornées par IP, quel que soit le résultat
            # de la route (ici dev-login désactivé en APP_ENV=test → 404).
            statuses = [client.post("/api/v1/auth/dev-login").status_code for _ in range(4)]
            assert statuses[:3] == [404, 404, 404]
            assert statuses[3] == 429
            refused = client.post("/api/v1/auth/dev-login")
            assert refused.status_code == 429
            assert int(refused.headers["Retry-After"]) >= 1
            assert refused.headers["X-RateLimit-Limit"] == "3"

            # Les lectures ne consomment aucun budget : la santé reste servie.
            for _ in range(10):
                assert client.get("/healthz").status_code == 200

            # Le seau évaluation est distinct : sa propre fenêtre.
            authenticate_client(client, role_code="analyst")
            payload = {
                "source_text": "Texte neutre.",
                "context": {
                    "as_of_date": "2026-09-24",
                    "jurisdiction": "FR",
                    "surface": "packaging",
                    "consumer_facing": True,
                    "product_identifier": "SKU-1",
                },
                "evidence": {"items": [], "legal_person": True},
            }
            first = client.post("/api/v1/engine/evaluate", json=payload)
            second = client.post("/api/v1/engine/evaluate", json=payload)
            third = client.post("/api/v1/engine/evaluate", json=payload)
            assert [first.status_code, second.status_code] == [200, 200]
            assert third.status_code == 429
    finally:
        LIMITER.reset()
        app.state.settings = previous


def test_middleware_is_inactive_when_disabled():
    assert settings.api_rate_limit_enabled is False, "APP_ENV=test désactive le limiteur global par défaut"
    previous = app.state.settings
    app.state.settings = replace(settings, api_rate_limit_enabled=False, api_rate_limit_write_per_minute=1)
    LIMITER.reset()
    try:
        with TestClient(app) as client:
            # Même avec une limite à 1, le commutateur off laisse passer.
            authenticate_client(client, role_code="analyst")
            for _ in range(3):
                res = client.post(
                    "/api/v1/engine/evaluate",
                    json={
                        "source_text": "Texte neutre.",
                        "context": {
                            "as_of_date": "2026-09-24",
                            "jurisdiction": "FR",
                            "surface": "packaging",
                            "consumer_facing": True,
                            "product_identifier": "SKU-1",
                        },
                        "evidence": {"items": [], "legal_person": True},
                    },
                )
                assert res.status_code == 200
    finally:
        LIMITER.reset()
        app.state.settings = previous
