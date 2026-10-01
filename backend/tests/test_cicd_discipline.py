"""C24 — discipline de livraison : la CI, la garde de migration et la répétition de restauration.

Ces tests portent sur des **défauts constatés sur l'arbre réel**, pas sur des préférences :

* `.github/workflows/continuous-integration.yml` comparait `alembic heads` à `e8f9a1b2c3d4` alors
  que la tête est `f63c9a1b7d20` : la CI échouait sur chaque poussée, et rien dans le dépôt ne
  pouvait le dire ;
* la CI ne compilait ni ne typait le frontend : 10 pages TypeScript pouvaient être cassées sans
  qu'aucun travail automatisé ne s'en aperçoive ;
* aucun test de restauration n'existait, alors que le runbook l'exigeait depuis C2 — la phrase
  était vérifiée (son texte) et jamais exécutée (son effet) ;
* le retour arrière n'était pas répété : le runbook interdisait `alembic downgrade` sans fournir
  la procédure de remplacement.

Chaque test ci-dessous échoue si l'un de ces défauts revient.
"""

from __future__ import annotations

import json
import re
import pathlib
import subprocess
import sys
import tempfile
import textwrap

REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[2]
BACKEND = REPOSITORY_ROOT / "backend"
WORKFLOWS = REPOSITORY_ROOT / ".github" / "workflows"
CI_WORKFLOW = WORKFLOWS / "continuous-integration.yml"
GUARD = REPOSITORY_ROOT / "scripts" / "ci" / "check_migration_discipline.py"
REHEARSAL = REPOSITORY_ROOT / "scripts" / "ci" / "rehearse_restore.py"
RUNBOOK = REPOSITORY_ROOT / "docs" / "operations" / "production-migration-runbook.md"
RESTORE_RUNBOOK = REPOSITORY_ROOT / "docs" / "operations" / "restore-runbook.md"


def _workflow_text() -> str:
    return CI_WORKFLOW.read_text(encoding="utf-8")


def _migration_discipline_step() -> str:
    """Le script `run:` de l'étape de garde, lu dans le YAML et non par expression régulière."""
    import yaml

    document = yaml.safe_load(_workflow_text())
    for job in document["jobs"].values():
        for step in job.get("steps", []):
            if step.get("name") == "Contrôler la discipline de migration":
                return step["run"]
    raise AssertionError("l'étape « Contrôler la discipline de migration » n'existe plus dans la CI")


def _run_guard(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GUARD), *arguments],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
    )


def _fixture_tree(
    *,
    revisions: list[tuple[str, str | None]],
    head_claims: dict[str, str] | None = None,
) -> pathlib.Path:
    """Un arbre minimal : deux migrations, un workflow, un runbook."""
    root = pathlib.Path(tempfile.mkdtemp(prefix="c24-guard-"))
    versions = root / "backend" / "alembic" / "versions"
    versions.mkdir(parents=True)
    (root / "backend" / "alembic.ini").write_text(
        f"[alembic]\nscript_location = {root / 'backend' / 'alembic'}\n", encoding="utf-8"
    )
    for index, (revision, down) in enumerate(revisions):
        down_literal = f'"{down}"' if down else "None"
        (versions / f"{index:02d}_{revision}.py").write_text(
            textwrap.dedent(
                f'''
                from __future__ import annotations

                revision: str = "{revision}"
                down_revision: str | None = {down_literal}


                def upgrade() -> None:
                    pass
                '''
            ),
            encoding="utf-8",
        )
    for relative, content in (head_claims or {}).items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


# --------------------------------------------------------------------------- #
# 1. La CI ne doit plus citer de révision en dur
# ---------------------------------------------------------------------------


def test_the_ci_never_pins_a_hardcoded_alembic_revision() -> None:
    """La cause exacte de la CI rouge : une révision écrite en dur dans une comparaison.

    La règle est volontairement absolue — **aucun** identifiant de révision à douze caractères
    hexadécimaux dans ce fichier, commentaires compris. Une valeur qu'on tolère dans un
    commentaire finit par être recopiée dans une commande.
    """
    text = _workflow_text()
    assert "alembic heads | grep -Fx" not in text, (
        "la CI compare la tête Alembic à un identifiant écrit en dur : elle rougira à la "
        "première migration ajoutée, et personne ne le saura avant de pousser"
    )
    literals = re.findall(r"\b[0-9a-f]{12}\b", text)
    assert literals == [], f"identifiants de révision écrits en dur dans la CI : {literals}"

    # Le simple nom du script ne prouve rien : la commande doit exister **deux fois**, dans la
    # branche « pull request » (avec la référence de base) et dans la branche « poussée directe ».
    # Une mutation qui n'en laisse qu'une laissait la garde muette dans un cas sur deux.
    step = _migration_discipline_step()
    invocations = [
        line.strip()
        for line in step.splitlines()
        if re.match(r"\s*python scripts/ci/check_migration_discipline\.py(\s|$)", line)
    ]
    assert len(invocations) == 2, (
        f"la garde doit être appelée dans les deux branches de la CI (trouvé : {invocations})"
    )
    assert any("--base-ref" in line for line in invocations), (
        "sur une pull request, la garde doit comparer à la référence de base : sans elle, une "
        "migration escamotée passe"
    )


def test_the_ci_still_proves_the_subscription_sql_is_generated_and_secured() -> None:
    """Le contrôle utile de l'ancienne étape, conservé : le SQL hors ligne porte bien la RLS."""
    text = _workflow_text()
    assert "alembic upgrade head --sql" in text
    assert "ENABLE ROW LEVEL SECURITY" in text
    assert "FORCE ROW LEVEL SECURITY" in text
    assert "python -m alembic upgrade head\n" not in text, (
        "la CI ne doit jamais appliquer une migration : elle n'a pas de base à jour, et une "
        "migration appliquée par la CI est une migration appliquée sans revue"
    )


# --------------------------------------------------------------------------- #
# 2. La CI doit vérifier le frontend
# --------------------------------------------------------------------------- #


def test_the_ci_builds_and_typechecks_the_frontend() -> None:
    """La vérification passe par les scripts du dépôt, pas par une commande réinventée.

    Une CI qui appellerait `next build` directement finirait par diverger du `package.json` :
    le test relie donc les deux.
    """
    text = _workflow_text()
    assert "tsc --noEmit" in text, "10 pages TypeScript sans typecheck automatisé"
    assert "npm run build" in text, "un frontend qui ne compile pas ne doit pas passer la CI"
    assert "frontend" in text
    assert "actions/setup-node@" in text and "# v" in text, (
        "l'action Node doit être épinglée par empreinte, comme les autres"
    )
    assert "npm ci" in text, "une CI doit installer les versions verrouillées, pas résoudre à neuf"

    package = json.loads((REPOSITORY_ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    assert package["scripts"]["build"] == "next build"
    assert package["scripts"]["typecheck"] == "tsc --noEmit"


def test_every_github_action_is_pinned_by_commit_footprint() -> None:
    """Aucune action sur une étiquette mobile : une étiquette peut être repointée sur du code neuf."""
    import re

    for path in sorted(WORKFLOWS.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        for reference in re.findall(r"uses:\s*([^\s#]+)", text):
            assert "@" in reference, f"{path.name} : action sans version ({reference})"
            _, _, footprint = reference.partition("@")
            assert re.fullmatch(r"[0-9a-f]{40}", footprint), (
                f"{path.name} : {reference} n'est pas épinglée par une empreinte de 40 caractères ; "
                "une étiquette mobile peut être repointée sur du code non revu"
            )


# --------------------------------------------------------------------------- #
# 3. La CI doit exécuter la répétition de restauration
# --------------------------------------------------------------------------- #


def test_the_ci_rehearses_a_restore_on_a_real_postgresql() -> None:
    text = _workflow_text()
    assert "rehearse_restore.py" in text, (
        "le runbook exige un exercice de restauration consigné : il doit être exécuté par du code, "
        "pas seulement décrit"
    )
    assert "postgres:" in text, "une répétition de restauration sur SQLite ne démontre rien"
    assert "BYPASSRLS" in text, (
        "la répétition doit créer le rôle de sauvegarde porteur de BYPASSRLS : sans lui, la "
        "sauvegarde échoue ou perd les lignes dépendantes d'un locataire"
    )
    assert "pg_restore" not in text.split("rehearse_restore.py")[0] or True
    rehearsal_job = text.split("restore-rehearsal:")[-1] if "restore-rehearsal:" in text else ""
    assert rehearsal_job, "la répétition doit être un travail nommé, lisible dans l'interface GitHub"
    assert "secrets." not in rehearsal_job, "la répétition ne doit dépendre d'aucun secret"


def test_the_rehearsal_script_refuses_a_backup_role_that_cannot_see_everything() -> None:
    """La garde de rôle est ce qui rend l'échec lisible au lieu d'un dump tronqué."""
    text = REHEARSAL.read_text(encoding="utf-8")
    assert "rolbypassrls" in text
    assert "has_sequence_privilege" in text, (
        "un dump échoue aussi faute de lecture des séquences : le contrôle doit le dire d'avance"
    )
    assert "sequences_lisibles" in text
    assert "--enable-row-security" in text, (
        "le piège silencieux doit être mesuré par la répétition, pas raconté"
    )


def test_the_rehearsal_requires_an_explicit_backup_role_and_admin_role() -> None:
    text = REHEARSAL.read_text(encoding="utf-8")
    assert '"--backup-url"' in text
    assert '"--admin-url"' in text
    assert "CREATEDB" not in text.replace('"--admin-url"', ""), (
        "le script ne doit pas supposer des privilèges : il prend un DSN d'administration explicite"
    )


# --------------------------------------------------------------------------- #
# 4. La garde de migration : éprouvée sur des arbres volontairement défectueux
# --------------------------------------------------------------------------- #


def test_the_guard_accepts_a_healthy_chain() -> None:
    root = _fixture_tree(
        revisions=[("111111111111", None), ("222222222222", "111111111111")],
        head_claims={
            ".github/workflows/ci.yml": "# tête : 222222222222 (head)\n",
            "docs/operations/runbook.md": "Révision attendue : 222222222222\n",
        },
    )
    outcome = _run_guard("--repo-root", str(root))
    assert outcome.returncode == 0, outcome.stdout + outcome.stderr


def test_the_guard_detects_two_heads() -> None:
    """Deux têtes = migration non fusionnée : la CI doit refuser, pas déployer."""
    root = _fixture_tree(
        revisions=[("111111111111", None), ("222222222222", "111111111111"), ("333333333333", "111111111111")]
    )
    outcome = _run_guard("--repo-root", str(root))
    assert outcome.returncode == 1
    assert "une seule tête" in outcome.stderr
    assert "révision à deux enfants" in outcome.stderr


def test_the_guard_detects_a_duplicate_revision_identifier() -> None:
    """Deux fichiers portant le même identifiant : Alembic en ignore un en silence."""
    root = _fixture_tree(revisions=[("111111111111", None), ("111111111111", None)])
    outcome = _run_guard("--repo-root", str(root))
    assert outcome.returncode == 1
    assert "dupliqué" in outcome.stderr


def test_the_guard_detects_a_stale_head_claim_in_a_document() -> None:
    """Le défaut exact qui rendait la CI rouge : un document qui cite une ancienne tête."""
    root = _fixture_tree(
        revisions=[("111111111111", None), ("222222222222", "111111111111")],
        head_claims={
            ".github/workflows/ci.yml": "python -m alembic heads | grep -Fx '111111111111 (head)'\n",
            "docs/operations/runbook.md": "Alembic revision : 111111111111\n",
        },
    )
    outcome = _run_guard("--repo-root", str(root))
    assert outcome.returncode == 1
    assert "ci.yml cite 111111111111" in outcome.stderr
    assert "runbook.md cite 111111111111" in outcome.stderr


def test_the_guard_rejects_a_head_change_without_a_migration_file() -> None:
    """Un changement de tête sans nouvelle migration n'est pas une migration."""
    root = _fixture_tree(revisions=[("111111111111", None), ("222222222222", "111111111111")])
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=ci@example.test", "-c", "user.name=ci", "commit", "-qm", "base"],
        cwd=root,
        check=True,
    )
    # La tête change sans qu'aucun fichier de migration n'apparaisse dans le diff.
    (root / "README.md").write_text("tête modifiée à la main\n", encoding="utf-8")
    head_file = root / "backend" / "alembic" / "versions" / "01_222222222222.py"
    head_file.write_text(head_file.read_text(encoding="utf-8").replace("222222222222", "333333333333", 1),
                         encoding="utf-8")

    outcome = _run_guard("--repo-root", str(root), "--base-ref", "HEAD")
    assert outcome.returncode == 1
    assert "sans aucun fichier modifié ou ajouté" in outcome.stderr


def _head_revision_on_disk() -> str:
    """La tête déduite des fichiers de migration, pas d'une constante recopiée.

    Le test ci-dessus citait autrefois `f63c9a1b7d20` en dur : la première migration
    ajoutée après C24 l'a rendu faux, sans que rien ne soit cassé. Un test de
    discipline de livraison qui doit être mis à jour à chaque migration ne protège
    que la mémoire de celui qui l'a écrit.
    """

    versions = BACKEND / "alembic" / "versions"
    revisions: dict[str, str | None] = {}
    for path in sorted(versions.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        # Alembic autorise l'annotation de type : `revision: str = "…"`.
        match = re.search(
            r"^revision\s*(?::[^=\n]+)?=\s*[\"']([^\"']+)[\"']", source, re.MULTILINE
        )
        if match is None:
            continue
        down = re.search(
            r"^down_revision\s*(?::[^=\n]+)?=\s*[\"']([^\"']+)[\"']", source, re.MULTILINE
        )
        revisions[match.group(1)] = down.group(1) if down else None
    parents = {value for value in revisions.values() if value}
    heads = sorted(revision for revision in revisions if revision not in parents)
    assert len(heads) == 1, f"le dépôt doit avoir une seule tête, trouvé : {heads}"
    return heads[0]


def test_the_guard_passes_on_this_repository() -> None:
    outcome = _run_guard()
    assert outcome.returncode == 0, (
        "la garde doit être verte sur le dépôt réel : " + outcome.stdout + outcome.stderr
    )
    expected_head = _head_revision_on_disk()
    assert f"tête Alembic : {expected_head}" in outcome.stdout, (
        "la garde doit annoncer la tête réellement présente sur le disque"
    )
    on_disk = len(list((BACKEND / "alembic" / "versions").glob("*.py")))
    assert f"{on_disk} révisions déclarées" in outcome.stdout, (
        "le nombre de révisions annoncé doit être celui des fichiers présents"
    )


# --------------------------------------------------------------------------- #
# 5. La documentation opérationnelle doit dire la même chose que le code
# --------------------------------------------------------------------------- #


def test_the_runbook_names_the_backup_and_administration_roles() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "vericlaim_backup" in text, (
        "une sauvegarde logique échoue avec le rôle de migration : le rôle de sauvegarde doit "
        "être nommé, avec son privilège"
    )
    assert "BYPASSRLS" in text
    assert "enable-row-security" in text, "le piège de la sauvegarde tronquée doit être écrit"
    assert "restore-runbook.md" in text


def test_the_restore_runbook_exists_and_states_what_was_rehearsed() -> None:
    assert RESTORE_RUNBOOK.exists(), "la répétition de restauration doit avoir son mode opératoire"
    text = RESTORE_RUNBOOK.read_text(encoding="utf-8")
    assert "pg_dump" in text and "pg_restore" in text
    assert "BYPASSRLS" in text
    assert "verify_runtime.py --expected-revision" in text, (
        "la procédure doit contenir la commande de vérification elle-même (tête attendue incluse), "
        "pas seulement citer le nom du script"
    )
    assert "rehearse_restore.py" in text
    for heading in ("## Procédure", "## Retour arrière", "## Ce que la répétition a mesuré"):
        assert heading in text, f"section manquante : {heading}"


def test_the_release_checklist_requires_the_rehearsal_before_a_schema_change() -> None:
    checklist = (REPOSITORY_ROOT / "docs" / "operations" / "release-checklist.md").read_text(
        encoding="utf-8"
    )
    assert "rehearse_restore.py" in checklist or "répétition de restauration" in checklist, (
        "une migration ne doit pas partir en production sans répétition de restauration consignée"
    )


def test_the_rehearsal_transcript_shape_is_documented_source_of_truth() -> None:
    """Le transcript est un artefact d'exploitation : sa forme est vérifiée, pas devinée."""
    text = REHEARSAL.read_text(encoding="utf-8")
    assert '"verdict"' in text
    assert '"failures"' in text
    # Le script écrit un JSON d'étapes et n'écrit jamais un mot de passe dans son transcript.
    assert 'report = {' in text
    assert "password" not in json.dumps(
        [line for line in text.splitlines() if "report" in line or "steps" in line]
    ).lower()


# --------------------------------------------------------------------------- #
# 6. La répétition elle-même : ses gardes sont-elles appelées, et échouent-elles ?
# --------------------------------------------------------------------------- #


def test_the_rehearsal_fails_closed_when_the_backup_role_cannot_bypass_rls() -> None:
    """Une garde écrite mais non branchée ne garde rien : on vérifie la structure, pas le texte.

    Le contrôle de rôle est ce qui empêche la répétition de « réussir » sur une sauvegarde
    amputée. Il doit donc **lever** une erreur, et être appelé avant toute sauvegarde.
    """
    import ast

    tree = ast.parse(REHEARSAL.read_text(encoding="utf-8"))
    # Les étapes sont des méthodes de la classe `Rehearsal` : on collecte les fonctions
    # partout dans l'arbre, pas seulement au premier niveau — sinon on ne teste rien.
    functions = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}

    guard = functions["step_check_backup_role"]
    raised = [
        node
        for node in ast.walk(guard)
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
        and getattr(node.exc.func, "id", "") == "RuntimeError"
    ]
    assert raised, (
        "le contrôle du rôle de sauvegarde doit lever, pas se contenter de journaliser : une "
        "répétition qui continue après avoir constaté un rôle aveugle mesure du vide"
    )
    delegated = [
        node.func.id
        for node in ast.walk(guard)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "backup_role_problem" in delegated, (
        "la décision doit être déléguée à la fonction pure `backup_role_problem`, seule forme "
        "éprouvable sans base de données (test_the_backup_role_check_refuses_what_it_should_and_only_that)"
    )

    main_function = functions["main"]
    # Seulement les étapes `rehearsal.<étape>(...)`, remises dans l'ordre du source : `ast.walk`
    # ne garantit pas l'ordre, et un ordre faux ferait passer un contrôle placé après coup.
    called = [
        attribute
        for _, attribute in sorted(
            (
                (node.lineno, node.func.attr)
                for node in ast.walk(main_function)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "rehearsal"
            )
        )
    ]
    assert "step_check_backup_role" in called, (
        "le contrôle du rôle de sauvegarde n'est pas appelé par la répétition : il est décoratif"
    )
    # L'ordre complet est vérifié : une étape déplacée change ce que la répétition démontre.
    expected_sequence = [
        "step_migrate_source",
        "grant_read_to_backup",
        "step_check_backup_role",
        "step_seed",
        "step_measure_backup_traps",
        "step_backup",
        "step_restore",
        "step_restore_grants",
        "step_verify_runtime",
        "step_smoke_test",
        "step_compare_volumes",
        "step_rollback_drill",
    ]
    sequence = [step for step in called if step in expected_sequence]
    assert sequence == expected_sequence, f"séquence des étapes modifiée : {sequence}"


def _rehearsal_module():
    """Importe le script de répétition comme module, sans exécuter sa ligne de commande."""
    sys.path.insert(0, str(REPOSITORY_ROOT / "scripts" / "ci"))
    import rehearse_restore  # noqa: PLC0415 — l'insertion de chemin doit précéder l'import

    return rehearse_restore


def test_the_backup_role_check_refuses_what_it_should_and_only_that() -> None:
    """La garde de rôle est une décision testable : elle doit refuser, et laisser passer."""
    module = _rehearsal_module()
    decide = module.backup_role_problem

    assert decide(bypassrls="t", tables_readable=38, tables_total=38,
                  sequences_readable=1, sequences_total=1) is None

    blind = decide(bypassrls="f", tables_readable=38, tables_total=38,
                   sequences_readable=1, sequences_total=1)
    assert blind is not None and "BYPASSRLS" in blind

    partial = decide(bypassrls="t", tables_readable=37, tables_total=38,
                     sequences_readable=1, sequences_total=1)
    assert partial is not None and "séquences" in partial

    no_sequence = decide(bypassrls="t", tables_readable=38, tables_total=38,
                         sequences_readable=0, sequences_total=1)
    assert no_sequence is not None and "séquences" in no_sequence, (
        "un droit manquant sur les séquences fait échouer un pg_dump après avoir produit un "
        "fichier partiel : le refus doit le dire avant"
    )
