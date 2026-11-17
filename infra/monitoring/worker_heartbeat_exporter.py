#!/usr/bin/env python3
"""Convertit le fichier de santé d'un worker en métrique Prometheus (A-09).

Le worker publie un battement (``app/workers/runtime.py``). Cet exportateur le
transforme en ``vericlaim_worker_heartbeat_age_seconds``, que la règle
``VeriClaimWorkerHeartbeatStale`` surveille.

Pourquoi un fichier texte plutôt qu'un push : un worker qui pousse des
métriques le fait depuis sa boucle de travail. Quand la boucle est bloquée —
le cas qui nous intéresse — il ne pousse plus rien, et l'absence de métrique
est indistinguable d'un worker qui n'existe pas. Un âge calculé par un tiers
est, lui, sans ambiguïté.

Usage::

    python infra/monitoring/worker_heartbeat_exporter.py /run/worker.json
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path


def read_age(path: Path) -> float | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        beat = datetime.fromisoformat(payload["heartbeat_at"])
    except (OSError, ValueError, KeyError):
        return None
    return max(0.0, time.time() - beat.timestamp())


def render(path: Path) -> str:
    age = read_age(path)
    lines = [
        "# HELP vericlaim_worker_heartbeat_age_seconds Âge du dernier battement du worker.",
        "# TYPE vericlaim_worker_heartbeat_age_seconds gauge",
    ]
    if age is None:
        # Pas de battement lisible = pas de preuve de vie. On exporte +Inf
        # plutôt que 0, qui se lirait comme « parfaitement sain ».
        lines.append("vericlaim_worker_heartbeat_age_seconds +Inf")
    else:
        lines.append(f"vericlaim_worker_heartbeat_age_seconds {age:.3f}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    arguments = argv if argv is not None else sys.argv[1:]
    if not arguments:
        print("Usage: worker_heartbeat_exporter.py <fichier-de-sante>", file=sys.stderr)
        return 2
    print(render(Path(arguments[0])), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
