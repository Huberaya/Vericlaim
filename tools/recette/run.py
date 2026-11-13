"""Command-line entry point for the VeriClaim recette qualification.

Usage (from the repository root, with the recette extras installed)::

    python -m tools.recette.run

It builds a disposable stack (real PostgreSQL 16 with the reviewed Neon role
scripts, a real S3 API, a clamd-protocol listener, a real OIDC provider), serves
the unmodified application through Uvicorn, drives the end-to-end journey and
writes ``.recette/last-run.json``.  Nothing outside ``.recette/`` is written and
nothing persistent is contacted.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from e2e_recette import RUNTIME_DIR, RecetteRun, build_full_stack  # noqa: E402


def _annotation(kind: str, title: str, message: str) -> None:
    """Emit a GitHub Actions annotation that actually survives parsing.

    Workflow commands are line-oriented: a newline inside the message truncates
    the command and the annotation silently disappears. Test details and
    tracebacks are full of newlines, so they are flattened first.
    """

    flattened = " | ".join(line.strip() for line in message.splitlines() if line.strip())
    print(f"::{kind} title={title}::{flattened[:900]}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        choices=("postgres", "storage", "oidc", "e2e"),
        default="e2e",
        help="Run a single qualification stage instead of the full journey.",
    )
    parser.add_argument("--json-out", default=str(RUNTIME_DIR / "last-run.json"))
    args = parser.parse_args(argv)

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 78)
    print("VeriClaim — qualification de l'environnement de recette")
    print("=" * 78)

    stack = build_full_stack()
    print(f"PostgreSQL   : {stack.postgres.database} (révision {stack.postgres.revision})")
    print(f"Stockage S3  : {stack.storage.backend} @ {stack.storage.endpoint_url}")
    print(f"clamd (proto): 127.0.0.1:{stack.scanner.port} — moteur de test, PAS ClamAV")
    print(f"OIDC         : {stack.idp.issuer} — fournisseur simulé, PAS Clerk")

    run = RecetteRun(stack, marker="RECETTE")
    try:
        if args.only == "e2e":
            report = run.execute()
        else:
            report = {"steps": [], "note": f"stage {args.only} not implemented as a standalone run"}
    except Exception as exc:  # noqa: BLE001 - the harness must report, never mask
        # A crash outside a checked step would otherwise only leave "exit code 1"
        # in CI. The type, the message and the last progress markers are enough
        # to locate it; the full traceback still goes to the console.
        import traceback

        _annotation(
            "error",
            "Recette interrompue",
            f"{type(exc).__name__}: {exc} | dernière progression: {run.transcript.trail()}",
        )
        for step in run.transcript.steps:
            if step.status == "FAIL":
                _annotation("error", "Étape en échec", f"{step.name} — {step.detail}")
        # A crash may land after some checks already failed; publishing them keeps
        # the diagnosis complete instead of showing only the exception.
        traceback.print_exc()
        return 1
    finally:
        stack.scanner.stop()
        stack.idp.stop()

    Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json_out).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print("-" * 78)
    print(f"Résultat : {report['total'] - report['failures']}/{report['total']} étapes en succès")
    print(f"Transcription : {args.json_out}")
    if report["failures"]:
        # CI logs are not always reachable from an operator's machine, so every
        # failing step is echoed in a GitHub Actions annotation as well.
        for step in report["steps"]:
            if step["status"] == "FAIL":
                _annotation("error", "Étape en échec", f"{step['name']} — {step['detail']}")
    return 1 if report["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
