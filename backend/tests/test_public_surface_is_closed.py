"""La surface sans identité est vérifiée, et le contrôle sait détecter une fuite.

Deux propriétés distinctes, et les deux comptent :

1. **l'état observé est conforme** — aucune route métier ne répond sans session ;
2. **le contrôle échoue quand il le devrait** — un contrôle qui ne peut pas virer
   au rouge ne prouve rien. On le mutile donc exprès : on rouvre une route, on
   fait répondre 200 au webhook, on retire le fail-closed du déclencheur. Chaque
   mutation doit être signalée.

Le premier point tourne contre l'application réelle ; le second contre la
fonction d'évaluation pure, pour rester rapide et sans base.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ci" / "check_public_surface.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_public_surface", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def surface():
    return _load_module()


def test_the_real_application_exposes_no_undeclared_route_without_identity(surface):
    """Interroger l'application réelle, sans session, route par route."""
    from app.main import app

    documentation = app.openapi()
    observed, problems = surface.collect(documentation)
    verdict = surface.evaluate(observed, set(documentation["paths"]))

    assert not problems, problems
    assert verdict.ok, "\n".join(verdict.errors)
    assert verdict.public_count > 0, "aucune route publique détectée : le relevé a échoué"


def _baseline(surface):
    """L'état conforme, reconstruit à la main pour servir de référence."""
    documented = set(surface.PUBLIC_PAGES) | set(surface.SIGNED_OR_VALIDATED)
    observed = [surface.Observation("GET", path, 200) for path in surface.PUBLIC_PAGES]
    observed += [
        surface.Observation("POST", path, sorted(codes)[0])
        for path, (codes, _reason) in surface.SIGNED_OR_VALIDATED.items()
    ]
    return documented, observed


def test_the_baseline_is_green(surface):
    """Sans cette ancre, les mutations ci-dessous pourraient être vertes d'avance."""
    documented, observed = _baseline(surface)
    assert surface.evaluate(observed, documented).ok


def test_a_business_route_opened_without_authentication_is_reported(surface):
    documented, observed = _baseline(surface)
    observed.append(surface.Observation("GET", "/api/v1/documents/{document_id}", 200))

    verdict = surface.evaluate(observed, documented)

    assert not verdict.ok
    assert any("/api/v1/documents/{document_id}" in error for error in verdict.errors)


def test_a_webhook_that_stops_rejecting_is_reported(surface):
    documented, _ = _baseline(surface)
    observed = [surface.Observation("GET", path, 200) for path in surface.PUBLIC_PAGES]
    observed += [
        surface.Observation("POST", path, sorted(codes)[0])
        for path, (codes, _reason) in surface.SIGNED_OR_VALIDATED.items()
        if path != "/api/v1/billing/webhook/{provider_code}"
    ]
    # Le webhook répond désormais 200 à une requête non signée.
    observed.append(surface.Observation("POST", "/api/v1/billing/webhook/{provider_code}", 200))

    verdict = surface.evaluate(observed, documented)

    assert not verdict.ok
    assert any("COMPORTEMENT CHANGÉ" in error for error in verdict.errors)


def test_a_worker_trigger_that_stops_failing_closed_is_reported(surface):
    documented, _ = _baseline(surface)
    observed = [surface.Observation("GET", path, 200) for path in surface.PUBLIC_PAGES]
    observed += [
        surface.Observation("POST", path, sorted(codes)[0])
        for path, (codes, _reason) in surface.SIGNED_OR_VALIDATED.items()
        if path != "/api/v1/internal/workers/{kind}/tick"
    ]
    # Sans secret configuré, le déclencheur exécuterait du travail : 200.
    observed.append(surface.Observation("GET", "/api/v1/internal/workers/{kind}/tick", 200))

    verdict = surface.evaluate(observed, documented)

    assert not verdict.ok
    assert any("COMPORTEMENT CHANGÉ" in error for error in verdict.errors)


def test_an_exception_that_no_longer_applies_is_reported(surface):
    """Une liste d'exceptions qui pourrit finit par ne plus rien protéger."""
    documented, observed = _baseline(surface)
    dropped = observed.pop()

    verdict = surface.evaluate(observed, documented)

    assert not verdict.ok
    assert any("EXCEPTION DEVENUE INUTILE" in error for error in verdict.errors), dropped
