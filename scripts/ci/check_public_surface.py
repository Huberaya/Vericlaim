"""Vérifier qu'aucune route métier ne répond sans identité.

La liste de contrôle de `SECURITY.md` §5 demande que toute route soit authentifiée
*et* autorisée. Une revue manuelle ne garantit pas cette propriété : le reste de
la suite s'authentifie avant d'appeler, donc une route ajoutée sans dépendance
d'authentification répondrait 200 sans que rien ne le signale.

Ce contrôle interroge réellement l'application, sans session ni en-tête, et
confronte ce qu'il observe à deux tables **explicites** :

* `PUBLIC_PAGES` — les quelques routes qui répondent volontairement sans
  identité, avec la raison ;
* `SIGNED_OR_VALIDATED` — les routes qui refusent par un autre code que 401/403
  parce qu'elles s'authentifient autrement (signature de prestataire) ou parce
  qu'elles valident un corps vide avant toute lecture.

Toute autre route qui ne répond pas 401/403, et toute exception devenue inutile,
sont des erreurs. Une liste d'exceptions qui pourrit ne protège plus.

Usage : ``cd backend && PYTHONPATH=. python ../scripts/ci/check_public_surface.py``.
"""

from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass, field

# Routes qui répondent sans identité, et pourquoi. Ce sont des pages publiques :
# aucune ne lit de donnée appartenant à une organisation.
PUBLIC_PAGES: dict[str, str] = {
    "/": "page d'accueil de l'API, sans donnée.",
    "/healthz": "sonde de vivacité : doit répondre même base arrêtée.",
    "/readyz": "sonde de disponibilité, consommée par la plateforme.",
    "/api/v1/auth/password-policy": "règle de mot de passe affichée avant l'inscription.",
    "/api/v1/auth/status": "dit si l'authentification est requise, sans révéler d'identité.",
    "/api/v1/billing/health": "état du fournisseur de paiement, sans donnée client.",
    "/api/v1/billing/plans": "catalogue commercial public.",
    "/api/v1/ops/alert-rules": "définition des règles d'alerte, sans mesure d'un client.",
    "/api/v1/ops/metric-names": "nomenclature des métriques, sans valeur.",
    "/api/v1/public/rights-procedure": "procédure RGPD à publier.",
    "/api/v1/public/support": "canal de support à publier.",
    "/api/v1/public/support/contact": "adresse de support à publier.",
    "/api/v1/reports/verify/{reference}": "vérification publique d'un rapport signé.",
}

# Routes qui refusent sans session, mais par un autre code que 401/403, et la ou
# les codes acceptés. Chacune est vérifiée : le code observé doit être dans
# l'ensemble, sinon l'entrée ne correspond plus au comportement.
SIGNED_OR_VALIDATED: dict[str, tuple[set[int], str]] = {
    "/api/v1/auth/signup": (
        {422},
        "inscription publique : un corps vide est refusé par la validation, avant toute lecture.",
    ),
    "/api/v1/auth/verify-email": (
        {422},
        "vérification d'adresse publique : même raison, validation avant lecture.",
    ),
    "/api/v1/auth/password-reset/request": (
        {422},
        "réinitialisation publique : même raison, et la réponse ne distingue pas un compte existant.",
    ),
    "/api/v1/auth/password-reset/confirm": (
        {422},
        "confirmation publique : même raison, validation avant lecture.",
    ),
    "/api/v1/billing/webhook/{provider_code}": (
        {404, 401, 403},
        "webhook prestataire : sans prestataire actif il n'existe pas (404), sinon la "
        "signature est vérifiée avant tout traitement (400 si elle est fausse).",
    ),
    "/api/v1/internal/workers/{kind}/tick": (
        {503, 401},
        "déclencheur de travaux : fermé par défaut (503 sans CRON_SECRET), 401 si un "
        "secret est configuré et que l'en-tête ne correspond pas.",
    ),
}

_SUBSTITUTIONS = (
    ("{organization_id}", str(uuid.UUID(int=1))),
    ("{document_id}", str(uuid.UUID(int=2))),
    ("{analysis_id}", str(uuid.UUID(int=3))),
    ("{claim_id}", str(uuid.UUID(int=4))),
    ("{version_id}", str(uuid.UUID(int=5))),
    ("{supplier_id}", str(uuid.UUID(int=6))),
    ("{report_id}", str(uuid.UUID(int=7))),
    ("{job_id}", str(uuid.UUID(int=8))),
    ("{request_id}", str(uuid.UUID(int=9))),
    ("{reference}", "inconnue-0000"),
    ("{provider_code}", "prestataire-inexistant"),
)

_IGNORED_METHODS = frozenset({"head", "options", "trace"})


@dataclass
class Observation:
    method: str
    path: str
    status: int


@dataclass
class Verdict:
    errors: list[str] = field(default_factory=list)
    public_count: int = 0
    inspected: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors


def evaluate(observed: list[Observation], documented: set[str]) -> Verdict:
    """Confronter ce qui a répondu sans identité aux exceptions déclarées.

    Séparé de l'exécution pour être testable : un contrôle qu'on ne peut pas
    éprouver par mutation ne prouve rien.
    """
    verdict = Verdict()
    public_paths: set[str] = set()

    for item in observed:
        if item.path in PUBLIC_PAGES:
            public_paths.add(item.path)
            verdict.public_count += 1
            continue
        allowed = SIGNED_OR_VALIDATED.get(item.path)
        if allowed is None:
            verdict.errors.append(
                f"OUVERTE SANS IDENTITÉ : {item.method} {item.path} → {item.status} "
                "(ni 401, ni 403, et route non déclarée)"
            )
            continue
        codes = allowed[0]
        if item.status not in codes:
            verdict.errors.append(
                f"COMPORTEMENT CHANGÉ : {item.method} {item.path} → {item.status}, "
                f"attendu {sorted(codes)} ; {allowed[1]}"
            )
        else:
            verdict.public_count += 1

    observed_paths = {item.path for item in observed}
    for path in sorted(set(PUBLIC_PAGES) | set(SIGNED_OR_VALIDATED)):
        if path not in observed_paths:
            verdict.errors.append(
                f"EXCEPTION DEVENUE INUTILE : {path} ne figure plus parmi les routes "
                "qui répondent sans identité (renommée, supprimée, ou soudain protégée)."
            )
    for path in sorted(observed_paths - set(PUBLIC_PAGES) - set(SIGNED_OR_VALIDATED)):
        verdict.errors.append(f"CHEMIN NON DOCUMENTÉ : {path}")

    verdict.inspected = len(documented)
    return verdict


def collect(documentation: dict) -> tuple[list[Observation], list[str]]:
    """Interroger toutes les routes documentées, sans session ni en-tête."""
    from fastapi.testclient import TestClient

    import app.main as entrypoint

    observed: list[Observation] = []
    problems: list[str] = []
    with TestClient(entrypoint.app, raise_server_exceptions=False) as client:
        for path, operations in sorted(documentation["paths"].items()):
            for method in operations:
                if method in _IGNORED_METHODS:
                    continue
                url = path
                for placeholder, value in _SUBSTITUTIONS:
                    url = url.replace(placeholder, value)
                response = client.request(method.upper(), url, json={})
                if response.status_code >= 500 and path not in SIGNED_OR_VALIDATED:
                    # Un 500 n'est pas un refus : c'est une panne qui rend la
                    # surface réelle invérifiable.
                    problems.append(f"ERREUR {method.upper()} {path} → {response.status_code} sans identité")
                    continue
                if response.status_code < 400 or path in SIGNED_OR_VALIDATED or path in PUBLIC_PAGES:
                    observed.append(Observation(method.upper(), path, response.status_code))
    return observed, problems


def main() -> int:
    os.environ.setdefault("APP_ENV", "test")
    os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/vericlaim-public-surface.db")
    os.environ.setdefault("AUTO_CREATE_SCHEMA", "true")

    import app.main as entrypoint

    documentation = entrypoint.app.openapi()
    observed, problems = collect(documentation)
    verdict = evaluate(observed, set(documentation["paths"]))

    for problem in problems:
        print(problem)
    for error in verdict.errors:
        print(error)

    if problems or not verdict.ok:
        print(
            "\nToute route qui répond sans identité doit être justifiée dans ce fichier. "
            "Une route métier ouverte est une régression d'isolation, pas un détail de "
            "configuration."
        )
        return 1

    print(
        f"{verdict.public_count} route(s) sans identité, toutes justifiées ; "
        f"{verdict.inspected} chemins inspectés."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
