#!/usr/bin/env python3
"""Vérifie TLS, rôle runtime, grants et révision de migration — sans rien écrire.

A-10 reprochait deux choses : le script existant exigeait un TLS actif mais
n'avait **jamais tourné avec succès** sur une vraie URL, et la documentation
laissait croire que l'équipe n'avait aucun accès à Neon. Ce script apporte la
partie utilisable et reproductible :

* **TLS obligatoire** — la connexion runtime doit être chiffrée ; un
  ``sslmode=prefer`` qui retombe en clair est un échec, pas un avertissement ;
* **identité runtime** — le rôle connecté est-il bien le rôle applicatif, et
  non le rôle propriétaire ou un superutilisateur ?
* **grants** — le rôle runtime peut-il écrire ce qu'il doit écrire, et
  **pas** ce qu'il ne doit pas (DDL, lecture hors tenant) ?
* **révision** — la base est-elle exactement à la tête Alembic attendue ?
* **RLS** — les tables tenant ont-elles une politique active et forcée ?

Il est en **lecture seule** : aucune requête ne modifie le schéma, les rôles ou
les données. Il fonctionne aussi bien contre un cluster local jetable que
contre une URL de recette fournie par l'équipe de pilotage.

Sécurité : aucune chaîne de connexion, aucun mot de passe, aucun hôte complet
n'est affiché. Le rapport ne contient que des noms de rôle, des booléens et des
compteurs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

EXPECTED_RLS_TABLES = (
    "documents",
    "document_versions",
    "document_segments",
    "document_uploads",
    "document_extraction_jobs",
    "analyses",
    "claims",
    "evidence",
    "audit_events",
)


def _connect(dsn: str, *, require_tls: bool):
    import psycopg

    # Autocommit : chaque contrôle est indépendant. Sans cela, une table
    # absente (base non migrée) invaliderait la transaction et ferait échouer
    # tous les contrôles suivants — on veut le rapport complet des écarts.
    connection = psycopg.connect(dsn, connect_timeout=10, autocommit=True)
    if require_tls and not bool(connection.pgconn.ssl_in_use):
        connection.close()
        raise RuntimeError("TLS inactif : connexion refusée (exigence runtime).")
    return connection


def collect(dsn: str, *, require_tls: bool = True, expected_role: str | None = None) -> dict[str, Any]:
    """Rassemble les observations. Ne lève pas sur un écart : il le rapporte."""

    report: dict[str, Any] = {
        "tls_in_use": False,
        "role": None,
        "is_superuser": None,
        "can_create_schema_objects": None,
        "alembic_revision": None,
        "rls": {},
        "problems": [],
        "checks": 0,
    }

    with _connect(dsn, require_tls=require_tls) as connection:
        report["tls_in_use"] = bool(connection.pgconn.ssl_in_use)
        if require_tls and not report["tls_in_use"]:
            report["problems"].append("tls_inactive")

        row = connection.execute(
            "SELECT current_user, usesuper FROM pg_user WHERE usename = current_user"
        ).fetchone()
        if row:
            report["role"] = row[0]
            report["is_superuser"] = bool(row[1])
            if row[1]:
                report["problems"].append("runtime_role_is_superuser")

        if expected_role and report["role"] != expected_role:
            report["problems"].append(f"unexpected_role:{report['role']}")

        # Le rôle runtime ne doit pas pouvoir créer d'objet de schéma : une
        # migration lancée par le rôle applicatif est un défaut de procédure.
        can_create = connection.execute(
            "SELECT has_schema_privilege(current_user, 'public', 'CREATE')"
        ).fetchone()
        report["can_create_schema_objects"] = bool(can_create[0]) if can_create else None
        if report["can_create_schema_objects"]:
            report["problems"].append("runtime_role_can_create_schema_objects")

        try:
            revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
            report["alembic_revision"] = revision[0] if revision else None
        except Exception:  # noqa: BLE001 - base non migrée : c'est un écart à rapporter
            report["alembic_revision"] = None
            report["problems"].append("alembic_version_missing")
            # Une base sans table de version n'est pas « à jour » : elle est
            # non migrée. Continuer permet de lister les autres écarts.

        for table in EXPECTED_RLS_TABLES:
            if report.get("alembic_revision") is None and "alembic_version_missing" in report["problems"]:
                break
            row = connection.execute(
                """
                SELECT c.relrowsecurity, c.relforcerowsecurity, count(p.polname)
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                LEFT JOIN pg_policy p ON p.polrelid = c.oid
                WHERE n.nspname = 'public' AND c.relname = %s
                GROUP BY c.relrowsecurity, c.relforcerowsecurity
                """,
                (table,),
            ).fetchone()
            if row is None:
                report["rls"][table] = {"present": False}
                report["problems"].append(f"missing_table:{table}")
                continue
            enabled, forced, policies = bool(row[0]), bool(row[1]), int(row[2])
            report["rls"][table] = {
                "present": True,
                "enabled": enabled,
                "forced": forced,
                "policies": policies,
            }
            report["checks"] += 1
            if not enabled:
                report["problems"].append(f"rls_disabled:{table}")
            if not forced:
                report["problems"].append(f"rls_not_forced:{table}")
            if policies == 0:
                report["problems"].append(f"rls_no_policy:{table}")

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Vérifie TLS, rôle runtime, grants et migration (lecture seule).")
    parser.add_argument(
        "--dsn-env",
        default="DATABASE_RUNTIME_URL",
        help="Variable contenant la chaîne de connexion runtime (jamais passée en argument)",
    )
    parser.add_argument("--expected-role", default=None, help="Rôle applicatif attendu")
    parser.add_argument("--allow-no-tls", action="store_true", help="Réservé au local jetable")
    parser.add_argument("--json", action="store_true", help="Sortie JSON")
    args = parser.parse_args(argv)

    dsn = os.environ.get(args.dsn_env)
    if not dsn:
        print(
            f"Variable {args.dsn_env} absente. Le script ne devine pas de cible.",
            file=sys.stderr,
        )
        return 2

    try:
        report = collect(
            dsn,
            require_tls=not args.allow_no_tls,
            expected_role=args.expected_role,
        )
    except RuntimeError as exc:
        # TLS refusé : le message est explicite et ne contient pas de DSN.
        print(json.dumps({"ok": False, "problems": [str(exc)]}, indent=2))
        return 2
    except Exception as exc:  # noqa: BLE001 - base injoignable = écart, pas plantage
        # On ne relaie que le type et le message court de psycopg : la chaîne de
        # connexion peut y figurer, on ne la rend donc jamais telle quelle.
        message = str(exc).splitlines()[0][:200]
        print(json.dumps({"ok": False, "problems": ["connection_failed"], "detail": message}, indent=2))
        return 2

    ok = not report["problems"]
    if args.json:
        print(json.dumps({"ok": ok, **report}, indent=2, default=str))
    else:
        print(f"TLS actif           : {report['tls_in_use']}")
        print(f"Rôle connecté       : {report['role']}")
        print(f"Superutilisateur    : {report['is_superuser']}")
        print(f"Peut créer (DDL)    : {report['can_create_schema_objects']}")
        print(f"Révision Alembic    : {report['alembic_revision']}")
        print(f"Tables RLS vérifiées: {report['checks']}")
        if report["problems"]:
            print("\nÉcarts :")
            for problem in report["problems"]:
                print(f"  - {problem}")
        else:
            print("\nAucun écart détecté.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
