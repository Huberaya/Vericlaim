#!/usr/bin/env python3
"""C24 — garde de discipline des migrations, exécutable en CI **et** en test local.

Trois défauts ont motivé ce script, tous constatés sur l'arbre réel :

1. `.github/workflows/continuous-integration.yml` vérifiait `alembic heads | grep -Fx
   'e8f9a1b2c3d4 (head)'` alors que la tête réelle est `f63c9a1b7d20` : la CI échouait sur
   **chaque** poussée, et personne ne pouvait le voir sans exécuter la CI. Une garde qui cite un
   identifiant de révision en dur pourrit toute seule.
2. Rien ne vérifiait qu'une tête **unique** existe : deux têtes (branche de migration non
   fusionnée) passaient la CI sans bruit et cassaient le déploiement.
3. Rien ne reliait un changement de tête à une **étape de migration** : on pouvait modifier la
   tête (ou la chaîne) sans qu'aucune migration ne soit ajoutée, donc sans que rien ne soit revu.

Ce script vérifie donc, dans cet ordre :

* la chaîne Alembic porte **exactement une tête** (aucune branche ouverte) ;
* aucun **identifiant de révision dupliqué**, et la chaîne est linéaire de la base à la tête ;
* la tête provient bien de `backend/alembic/versions/` et non d'un effet de bord ;
* si la tête a changé depuis une référence de base (`--base-ref`), une migration doit avoir été
  **ajoutée ou modifiée** dans le diff ;
* aucun document opérationnel, workflow ou README ne cite une révision comme « tête » ou
  « attendue » sans que ce soit la tête réelle — le défaut n° 1, rendu impossible à répéter.

Usage :

    python scripts/ci/check_migration_discipline.py                    # contrôles autonomes
    python scripts/ci/check_migration_discipline.py --base-ref origin/main
    python scripts/ci/check_migration_discipline.py --json /tmp/garde.json
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys

DEFAULT_ROOT = pathlib.Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = DEFAULT_ROOT
BACKEND = REPOSITORY_ROOT / "backend"
VERSIONS_DIR = BACKEND / "alembic" / "versions"

#: Documents qui parlent d'une révision à un opérateur ou à un relecteur. Le mot « head » ou
#: « attendue » y engage un humain : ils sont donc soumis au contrôle d'actualité.
DOCUMENTS_TO_SCAN = (
    ".github/workflows",
    "docs/operations",
    "README.md",
    "SECURITY.md",
)


def use_root(root: pathlib.Path) -> None:
    """Repointe la garde sur un arbre donné.

    Indispensable pour que les tests éprouvent la garde elle-même : sans cette entrée, on ne
    peut vérifier qu'elle **détecte** un défaut qu'en cassant le dépôt réel.
    """
    global REPOSITORY_ROOT, BACKEND, VERSIONS_DIR
    REPOSITORY_ROOT = root
    BACKEND = root / "backend"
    VERSIONS_DIR = BACKEND / "alembic" / "versions"

REVISION_PATTERN = re.compile(r"\b[0-9a-f]{12}\b")
#: Formes qui affirment une révision en vigueur : « <rev> (head) », « révision : <rev> »,
#: « révision attendue <rev> », « expected revision <rev> », « alembic_head=<rev> ».
CLAIM_PATTERNS = (
    re.compile(r"([0-9a-f]{12})\s*\(head\)"),
    re.compile(r"r[ée]vision\s*(?:attendue)?\s*[:=]\s*`?([0-9a-f]{12})`?", re.IGNORECASE),
    re.compile(r"expected[_\s-]*revision\s*[:=]\s*`?([0-9a-f]{12})`?", re.IGNORECASE),
)


class CheckFailure(Exception):
    pass


def git(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=REPOSITORY_ROOT, capture_output=True, text=True, check=True
    ).stdout


def read_heads() -> list[str]:
    """Les têtes déclarées par la chaîne de migrations, lues avec Alembic lui-même."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    configuration = Config(str(BACKEND / "alembic.ini"))
    configuration.set_main_option("script_location", str(BACKEND / "alembic"))
    return list(ScriptDirectory.from_config(configuration).get_heads())


def revision_sources() -> dict[str, pathlib.Path]:
    """Identifiant de révision → fichier qui le déclare, en lisant les sources, pas Alembic.

    Volontairement indépendant d'Alembic : si le même identifiant était déclaré deux fois,
    Alembic chargerait la première version rencontrée et la seconde resterait invisible.
    """
    found: dict[str, pathlib.Path] = {}
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        match = re.search(r"^revision:\s*str\s*=\s*[\"']([^\"']+)[\"']", text, re.MULTILINE)
        if match:
            found.setdefault(match.group(1), path)
    return found


def duplicate_revisions() -> list[str]:
    counts: dict[str, int] = {}
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        match = re.search(r"^revision:\s*str\s*=\s*[\"']([^\"']+)[\"']", text, re.MULTILINE)
        if match:
            counts[match.group(1)] = counts.get(match.group(1), 0) + 1
    return sorted(revision for revision, count in counts.items() if count > 1)


def chain_is_linear() -> tuple[bool, str]:
    """Vérifie qu'aucune révision n'a deux enfants (une seule descente jusqu'à la tête)."""
    parents: dict[str, str | None] = {}
    children: dict[str, list[str]] = {}
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        revision = re.search(r"^revision:\s*str\s*=\s*[\"']([^\"']+)[\"']", text, re.MULTILINE)
        down = re.search(
            r"^down_revision[^=]*=\s*(?:[\"']([^\"']+)[\"']|None)", text, re.MULTILINE
        )
        if not revision:
            continue
        parent = down.group(1) if down and down.group(1) else None
        parents[revision.group(1)] = parent
        if parent:
            children.setdefault(parent, []).append(revision.group(1))

    branching = {parent: kids for parent, kids in children.items() if len(kids) > 1}
    if branching:
        detail = ", ".join(f"{parent} → {kids}" for parent, kids in sorted(branching.items()))
        return False, detail
    return True, ""


def migration_files_in_diff(base_ref: str) -> list[str]:
    changed = git("diff", "--name-only", f"{base_ref}...HEAD", "--", "backend/alembic/versions")
    return [line for line in changed.splitlines() if line.strip()]


def head_at(base_ref: str) -> str | None:
    """La tête telle qu'elle était sur la référence de base, si elle est lisible."""
    try:
        listing = git("ls-tree", "-r", "--name-only", base_ref, "--", "backend/alembic/versions")
    except subprocess.CalledProcessError:
        return None
    revisions: list[str] = []
    downs: set[str] = set()
    for name in listing.splitlines():
        try:
            text = git("show", f"{base_ref}:{name}")
        except subprocess.CalledProcessError:
            continue
        revision = re.search(r"^revision:\s*str\s*=\s*[\"']([^\"']+)[\"']", text, re.MULTILINE)
        down = re.search(r"^down_revision[^=]*=\s*[\"']([^\"']+)[\"']", text, re.MULTILINE)
        if revision:
            revisions.append(revision.group(1))
        if down:
            downs.add(down.group(1))
    heads = [revision for revision in revisions if revision not in downs]
    return heads[0] if len(heads) == 1 else None


def stale_head_claims(head: str) -> list[str]:
    """Toute révision citée comme « tête » ou « attendue » qui n'est pas la tête réelle."""
    problems: list[str] = []
    for relative_root in DOCUMENTS_TO_SCAN:
        root = REPOSITORY_ROOT / relative_root
        paths = sorted(root.rglob("*")) if root.is_dir() else [root]
        for path in paths:
            if not path.is_file() or path.suffix not in {".md", ".yml", ".yaml"}:
                continue
            text = path.read_text(encoding="utf-8")
            for pattern in CLAIM_PATTERNS:
                for claimed in pattern.findall(text):
                    if claimed != head:
                        relative = path.relative_to(REPOSITORY_ROOT)
                        problems.append(f"{relative} cite {claimed} comme révision en vigueur (tête réelle : {head})")
    return problems


def run(*, base_ref: str | None) -> dict[str, object]:
    report: dict[str, object] = {"head": None, "checks": [], "failures": []}
    failures: list[str] = report["failures"]  # type: ignore[assignment]
    checks: list[dict[str, object]] = report["checks"]  # type: ignore[assignment]

    def record(name: str, passed: bool, detail: str = "") -> None:
        checks.append({"check": name, "passed": passed, "detail": detail})
        if not passed:
            failures.append(f"{name} : {detail}")

    # Tous les diagnostics sont publiés **avant** toute conclusion : un opérateur qui voit
    # « plusieurs têtes » doit apprendre dans le même rapport *pourquoi* (un parent à deux
    # enfants) et si des identifiants sont dupliqués. Un script qui s'arrête au premier constat
    # transforme un diagnostic en devinette.
    sources = revision_sources()
    duplicates = duplicate_revisions()
    record("aucun identifiant de révision dupliqué", not duplicates, ", ".join(duplicates))

    linear, detail = chain_is_linear()
    record("chaîne linéaire (aucune révision à deux enfants)", linear, detail)
    record("les fichiers de migration sont lisibles", bool(sources), f"{len(sources)} révisions déclarées")

    heads = read_heads()
    report["head"] = heads[0] if len(heads) == 1 else None
    record(
        "une seule tête Alembic",
        len(heads) == 1,
        f"têtes trouvées : {heads}" if len(heads) != 1 else heads[0],
    )
    head = report["head"]
    if head is None:
        return report

    record(
        "la tête est déclarée par un fichier de migration",
        head in sources,
        f"{head} introuvable dans backend/alembic/versions/" if head not in sources
        else str(sources[head].name),
    )

    if base_ref:
        previous = head_at(base_ref)
        changed = migration_files_in_diff(base_ref)
        report["base_ref"] = base_ref
        report["head_at_base_ref"] = previous
        report["migration_files_changed"] = changed
        if previous is None:
            record(
                "référence de base lisible",
                False,
                f"la chaîne de {base_ref} ne porte pas exactement une tête : la comparaison est "
                "impossible, donc la garde refuse plutôt que de supposer",
            )
        elif previous != head:
            record(
                "changement de tête accompagné d'une étape de migration",
                bool(changed),
                f"la tête passe de {previous} à {head} sans aucun fichier modifié ou ajouté dans "
                "backend/alembic/versions/",
            )
        else:
            record("tête inchangée depuis la référence", True, previous)

    stale = stale_head_claims(head)
    record("aucune révision périmée citée comme tête", not stale, " ; ".join(stale))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Garde de discipline des migrations (C24).")
    parser.add_argument("--base-ref", default=None, help="référence git de comparaison (ex. origin/main)")
    parser.add_argument("--repo-root", default=None, help="arbre à contrôler (défaut : ce dépôt)")
    parser.add_argument("--json", default=None, help="écrire le rapport JSON à ce chemin")
    parser.add_argument("--quiet", action="store_true")
    arguments = parser.parse_args()

    if arguments.repo_root:
        use_root(pathlib.Path(arguments.repo_root).resolve())

    try:
        report = run(base_ref=arguments.base_ref)
    except CheckFailure as failure:  # pragma: no cover — garde-fou
        print(str(failure), file=sys.stderr)
        return 2

    if not arguments.quiet:
        for check in report["checks"]:  # type: ignore[index]
            marker = "OK  " if check["passed"] else "ÉCHEC"
            print(f"{marker} {check['check']} — {check['detail']}")
        print(f"tête Alembic : {report['head']}")

    if arguments.json:
        pathlib.Path(arguments.json).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    failures = report["failures"]
    if failures:
        print("\nGarde de migration en échec :", file=sys.stderr)
        for failure in failures:  # type: ignore[union-attr]
            print(f"  - {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
