"""État initial de la suite : la base de développement est remise à zéro.

Constat mesuré : la suite **n'était pas rejouable**. Lancée deux fois de suite sans
supprimer `backend/vericlaim.db`, elle passait puis échouait (7 à 14 échecs selon les
exécutions) — non-déterminisme, données accumulées, identifiants déjà présents. La
recette « `rm -f vericlaim.db` » était écrite dans les notes du projet, donc connue,
mais rien ne l'appliquait : un développeur qui lance `pytest` deux fois de suite voit du
rouge sans avoir rien cassé, et un harnais de mutation qui oublierait ce geste déclarerait
« mutation détectée » pour une raison qui n'a rien à voir avec la mutation.

Ce fichier supprime donc la base **avant la première utilisation** de la session, et
uniquement si la cible est bien le fichier SQLite de développement :

* si `DATABASE_URL` pointe vers PostgreSQL, **rien n'est touché** — la suite ne doit
  jamais pouvoir effacer une base réelle, et les tests qui montent un schéma PostgreSQL
  le font sur une base dédiée créée pour eux ;
* le chemin visé est celui que l'application utilise réellement (résolu par la
  configuration), jamais un chemin inventé ici.
"""

from __future__ import annotations

import pathlib

import pytest


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
