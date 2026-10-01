"""Parité entre les **modèles** et le schéma réellement **migré**.

Ce fichier existe à cause d'un défaut mesuré pendant C12 : sur PostgreSQL, une base
montée par les migrations répondait 500 sur ``GET /api/v1/privacy/export`` —

    UndefinedColumn: column legal_holds.is_active does not exist

— parce que le modèle ``LegalHold`` déclarait ``is_active`` et ``expires_at`` que la
migration ``e8f9a1b2c3d4`` ne créait pas. La même migration laissait ``api_keys``
avec ``hashed_secret``/``scopes``/``revoked_at`` là où le modèle attend
``key_hash``/``scopes_json``/``is_active``/``last_used_at``/``updated_at``.

Pourquoi aucun test ne le voyait : la suite construit son schéma avec
``create_tables()``, c'est-à-dire **depuis les modèles**. Elle ne peut donc, par
construction, jamais constater une divergence entre les modèles et les migrations —
et un déploiement, lui, ne monte sa base que par les migrations. Le droit d'accès et
le droit à l'effacement étaient inutilisables en production, avec une suite verte.

Ce que ces tests font : ils montent une base **par ``alembic upgrade head``**, puis

1. comparent table par table les colonnes déclarées et les colonnes présentes ;
2. exécutent un ``SELECT`` SQLAlchemy sur **chaque modèle mappé** — une colonne
   absente fait échouer la requête, donc la vérification ne dépend pas d'une liste
   écrite à la main ;
3. vérifient que la migration redescend (``downgrade``), parce qu'une migration dont
   la descente n'a jamais été exécutée ne redescend pas.

Ce que ces tests ne couvrent pas : PostgreSQL. Les types, les index et les politiques
RLS n'y sont pas comparés ici — la preuve HTTP (`audit/C12_evidence.md`) exerce ces
chemins sur une vraie base PostgreSQL migrée.
"""

from __future__ import annotations

import pathlib

import pytest
from sqlalchemy import create_engine, inspect, select

from alembic import command
from alembic.config import Config
from app.core.database import Base
from app.models import domain as _domain_models  # noqa: F401 - enregistre les modèles

BACKEND = pathlib.Path(__file__).resolve().parents[1]


def _alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


@pytest.fixture(scope="module")
def migrated_database(tmp_path_factory) -> str:
    """Une base montée uniquement par les migrations, plus l'empreinte de son schéma."""

    path = tmp_path_factory.mktemp("schema-parity") / "migrated.db"
    url = f"sqlite:///{path}"
    import os

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    try:
        # `alembic/env.py` lit `settings.database_url`, calculé à l'import : on pousse
        # donc aussi la variable d'environnement avant d'importer la configuration.
        from importlib import reload

        from app.core import config as config_module

        reload(config_module)
        command.upgrade(_alembic_config(url), "head")
        yield url
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        from importlib import reload

        from app.core import config as config_module

        reload(config_module)


def test_every_model_table_exists_after_migration(migrated_database: str):
    inspector = inspect(create_engine(migrated_database))
    present = set(inspector.get_table_names())
    missing = sorted(set(Base.metadata.tables) - present)
    assert missing == [], f"tables déclarées par les modèles et absentes après migration : {missing}"


def test_every_model_column_exists_after_migration(migrated_database: str):
    """La comparaison colonne par colonne, dans les deux sens."""

    inspector = inspect(create_engine(migrated_database))
    missing: dict[str, list[str]] = {}
    extra: dict[str, list[str]] = {}
    for table in sorted(Base.metadata.tables):
        declared = {column.name for column in Base.metadata.tables[table].columns}
        present = {column["name"] for column in inspector.get_columns(table)}
        if declared - present:
            missing[table] = sorted(declared - present)
        if present - declared:
            extra[table] = sorted(present - declared)
    assert missing == {}, (
        "colonnes déclarées par les modèles et absentes du schéma migré : "
        f"{missing}. Une base déployée est montée par les migrations : ces colonnes "
        "n'existeront pas en production."
    )
    assert extra == {}, (
        f"colonnes présentes dans le schéma migré et inconnues des modèles : {extra}"
    )


def test_every_model_can_be_read_after_migration(migrated_database: str):
    """Une requête par modèle : le contrôle ne dépend d'aucune liste écrite à la main.

    ``select(Model)`` rend toutes les colonnes déclarées. Si l'une manque côté base,
    la requête échoue ici — c'est exactement le 500 qui a motivé ce fichier.
    """

    engine = create_engine(migrated_database)
    failures: list[str] = []
    with engine.connect() as connection:
        for mapper in Base.registry.mappers:
            model = mapper.class_
            try:
                connection.execute(select(model).limit(0))
            except Exception as exc:  # noqa: BLE001 - on rapporte l'erreur, on ne la masque pas
                failures.append(f"{model.__tablename__}: {type(exc).__name__}: {str(exc)[:120]}")
    assert failures == [], "modèles illisibles sur une base migrée :\n" + "\n".join(failures)


def test_the_migration_can_be_rolled_back(tmp_path):
    """Une migration dont la descente n'a jamais tourné ne redescend pas.

    Le ``downgrade`` de cette campagne a échoué au premier essai : ``now()`` (fonction
    PostgreSQL) était utilisé dans un ``UPDATE``, refusé par SQLite. Sans ce test, le
    défaut n'aurait été vu qu'au moment d'un retour arrière en production.
    """

    import os

    url = f"sqlite:///{tmp_path / 'rollback.db'}"
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    from importlib import reload

    from app.core import config as config_module

    reload(config_module)
    try:
        command.upgrade(_alembic_config(url), "head")
        command.downgrade(_alembic_config(url), "-1")
        command.upgrade(_alembic_config(url), "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        reload(config_module)
