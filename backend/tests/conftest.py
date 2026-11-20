"""Configuration partagée de la suite de tests.

Deux garde-fous, tous deux appris d'un défaut réel.

**1. Les clés étrangères SQLite sont activées.** SQLite compile avec la
vérification des clés étrangères **désactivée** par défaut : la suite isolée
acceptait donc des insertions dont la ligne parente n'existait pas encore, alors
que PostgreSQL — la seule base autorisée hors développement/test (ADR-002) — les
rejette. Cette divergence masquait un vrai défaut : `complete_document_upload`
renseignait `document_uploads.finalized_document_version_id` avant d'insérer la
ligne `document_versions`, ce qui produit un 500 sur PostgreSQL et un succès
silencieux sur SQLite. Activer le pragma fait échouer la suite isolée pour les
mêmes raisons que la production.

**2. La base de développement est remise à zéro, sans jamais toucher une base
réelle.** Constat mesuré : la suite n'était pas rejouable. Lancée deux fois de
suite sans supprimer `backend/vericlaim.db`, elle passait puis échouait (7 à 14
échecs selon les exécutions) : données accumulées, identifiants déjà présents.
Un développeur qui lance `pytest` deux fois voyait du rouge sans avoir rien
cassé. La base n'est donc supprimée qu'avant la première utilisation de la
session, et uniquement si la cible est bien le fichier SQLite de développement :
si `DATABASE_URL` pointe vers PostgreSQL, **rien n'est touché**.
"""

from __future__ import annotations

import pathlib

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine


@event.listens_for(Engine, "connect")
def _enforce_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    """Match PostgreSQL's referential integrity in isolated SQLite runs."""

    if type(dbapi_connection).__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def _development_database_path() -> pathlib.Path | None:
    from app.core.config import settings

    url = str(settings.database_url)
    if not url.startswith("sqlite"):
        return None
    _, _, tail = url.partition("///")
    candidate = tail.split("?", 1)[0]
    if not candidate or candidate == ":memory:":
        return None
    path = pathlib.Path(candidate)
    if not path.is_absolute():
        path = pathlib.Path(__file__).resolve().parents[1] / path
    # Garde-fou : on ne supprime que dans le dépôt, et seulement un fichier de base.
    repository = pathlib.Path(__file__).resolve().parents[1]
    if repository not in path.resolve().parents:
        return None
    if path.suffix not in {".db", ".sqlite", ".sqlite3"}:
        return None
    return path


@pytest.fixture(scope="session", autouse=True)
def _fresh_development_database() -> None:
    path = _development_database_path()
    if path is None:
        return
    for suffix in ("", "-wal", "-shm"):
        candidate = pathlib.Path(str(path) + suffix)
        if candidate.exists():
            candidate.unlink()
