#!/usr/bin/env python3
"""Sonde de santé d'un worker durable — pour Docker, systemd ou Kubernetes.

Usage::

    python -m app.worker_healthcheck            # utilise WORKER_HEALTH_FILE
    python -m app.worker_healthcheck --file /run/worker.json --max-age 120

Sortie 0 si le worker avance, 1 sinon. La distinction « vivant » / « qui
progresse » est le cœur du contrôle : un processus bloqué sur une base
injoignable a un PID, mais n'écrit plus de battement.

Aucun secret n'est lu ni affiché : le fichier ne contient que des compteurs et
des horodatages.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from app.workers.runtime import health_is_fresh, read_health_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Vérifie la fraîcheur du battement d'un worker.")
    parser.add_argument(
        "--file",
        default=os.getenv("WORKER_HEALTH_FILE", ""),
        help="Fichier de santé (défaut : $WORKER_HEALTH_FILE)",
    )
    parser.add_argument(
        "--max-age",
        type=float,
        default=float(os.getenv("WORKER_HEALTH_MAX_AGE_SECONDS", "120")),
        help="Âge maximal accepté du dernier battement, en secondes",
    )
    args = parser.parse_args(argv)

    if not args.file:
        print("Aucun fichier de santé fourni", file=sys.stderr)
        return 1

    payload = read_health_file(Path(args.file))
    if payload is None:
        print(f"État illisible ou absent : {args.file}", file=sys.stderr)
        return 1
    if payload.get("stopping"):
        print("Worker en cours d'arrêt", file=sys.stderr)
        return 1
    if not health_is_fresh(payload, max_age_seconds=args.max_age):
        print(
            f"Battement trop ancien (dernier {payload.get('heartbeat_at')}, max {args.max_age}s)",
            file=sys.stderr,
        )
        return 1

    print(
        f"ok worker={payload.get('worker')} itérations={payload.get('iterations')} "
        f"jobs={payload.get('jobs_processed')} heartbeat={payload.get('heartbeat_at')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
