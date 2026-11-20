"""Déclenchement des workers par le planificateur de la plateforme.

**Le problème que ce module résout.** Les trois workers (`document_extraction`,
`analysis_detection`, `report_generation`) sont écrits comme des boucles infinies,
lancées par `docker-compose.yml` : un conteneur, un processus, `while True`. Déployés
sur une plateforme sans serveur, il n'existe plus aucun processus pour les porter — et
**rien dans le code n'exposait de quoi exécuter un cycle**. Une analyse importée par un
client restait donc en file indéfiniment.

**Ce que fait cette route.** Elle exécute *un* cycle de travail (`run_once`) en boucle
jusqu'à ce qu'il n'y ait plus rien à faire ou que le budget de temps soit épuisé. Le
planificateur l'appelle à intervalle régulier ; la file se vide par petits pas au lieu
de reposer sur un processus permanent.

**Trois propriétés, et pourquoi elles sont ici :**

* **Ce n'est pas une opération utilisateur.** La route ne prend ni jeton de session, ni
  identifiant de locataire, et ne retourne aucune donnée métier — seulement des
  compteurs. Elle est donc **hors du modèle de rôles**, et c'est délibéré : lui donner
  un rôle aurait obligé à inventer un compte « opérateur de plateforme » qui n'existe
  pas dans ce produit.
* **Elle est fermée par défaut.** Sans secret configuré, elle répond 503 et ne fait
  rien. On ne « protège » pas une commande en espérant que personne ne connaisse l'URL :
  soit le secret est là, soit la file ne se vide pas et cela se voit.
* **Le secret est celui de la plateforme.** Vercel envoie automatiquement
  `Authorization: Bearer $CRON_SECRET` sur chaque invocation planifiée : la valeur
  attendue est donc `CRON_SECRET`, et la comparaison est à temps constant. Un appel
  manuel (supervision, rattrapage) se fait avec le même en-tête.

**Ce que la boucle doit au code existant.** `run_once` est déjà sûr à plusieurs
répliques : la réclamation d'un travail passe par `FOR UPDATE SKIP LOCKED` et un bail,
et un travail interrompu (fonction tuée en cours de route, ce qui arrive sur une
plateforme sans serveur) redevient disponible à l'expiration du bail. Deux invocations
qui se chevauchent ne se marchent donc pas dessus.
"""

from __future__ import annotations

import hmac
import logging
import time
from typing import Any, Protocol

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from app.core.config import settings
from app.documents.storage import build_object_storage
from app.workers.analysis_detection_worker import AnalysisDetectionWorker
from app.workers.document_extraction_worker import DocumentExtractionWorker
from app.workers.report_generation_worker import ReportGenerationWorker

logger = logging.getLogger("vericlaim.internal_workers")

router = APIRouter(prefix="/api/v1/internal/workers", tags=["internal"])

#: Les trois files du produit. Un nom inconnu est un 404 : la route ne devine pas.
WORKER_KINDS = ("document_extraction", "analysis_detection", "report_generation")


class TickableWorker(Protocol):
    def run_once(self) -> bool: ...


class WorkerTickResponse(BaseModel):
    """Compteurs d'un cycle de planificateur. Aucune donnée métier."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    worker_id: str
    processed: int
    budget_seconds: int
    duration_ms: int
    drained: bool


def build_worker(kind: str, *, worker_id: str) -> TickableWorker:
    """Construit le worker demandé.

    Deux workers ont besoin du stockage objet ; la détection n'en a pas besoin, et lui
    en donner un la ferait dépendre d'un service dont elle ne se sert pas.
    """
    if kind == "document_extraction":
        return DocumentExtractionWorker(
            settings=settings,
            storage=build_object_storage(settings),
            worker_id=worker_id,
        )
    if kind == "analysis_detection":
        return AnalysisDetectionWorker(settings=settings, worker_id=worker_id)
    if kind == "report_generation":
        return ReportGenerationWorker(
            settings=settings,
            storage=build_object_storage(settings),
            worker_id=worker_id,
        )
    raise KeyError(kind)


def _authorize(request: Request) -> None:
    configured = settings.internal_worker_secret
    if not configured:
        # Fail closed: pas de secret, pas de travail. La file reste pleine et le
        # dit — c'est visible, contrairement à une route « ouverte en attendant ».
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Le déclencheur de travaux n’est pas configuré (CRON_SECRET absent) : "
                "aucune file n’est drainée."
            ),
        )
    header = request.headers.get("authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(token.strip(), configured):
        logger.warning(
            "Rejected worker tick",
            extra={"event": "internal_worker_tick_rejected", "path": request.url.path},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Déclencheur de travaux non autorisé.",
        )


@router.api_route(
    "/{kind}/tick",
    methods=["GET", "POST"],
    response_model=WorkerTickResponse,
    summary="Exécute un cycle de travail (planificateur de la plateforme)",
)
def tick_worker(kind: str, request: Request) -> WorkerTickResponse:
    """Exécute un cycle pour une file, dans la limite du budget de temps configuré.

    `GET` comme `POST` : le planificateur de Vercel n'émet que des `GET`, un humain qui
    rattrape un retard préfère souvent un `POST`. Le traitement est identique — il n'y a
    pas de corps de requête.
    """
    _authorize(request)
    if kind not in WORKER_KINDS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File de travail inconnue. Attendu : {', '.join(WORKER_KINDS)}.",
        )

    budget_seconds = settings.internal_worker_budget_seconds
    worker_id = f"platform-scheduler:{kind}"
    started = time.monotonic()
    deadline = started + budget_seconds

    worker = build_worker(kind, worker_id=worker_id)
    processed = 0
    drained = True
    while True:
        if time.monotonic() >= deadline:
            # Budget épuisé : du travail reste peut-être. Le prochain cycle reprendra ;
            # on ne prétend pas que la file est vide.
            drained = False
            break
        if not worker.run_once():
            break
        processed += 1

    duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "Worker tick completed",
        extra={
            "event": "internal_worker_tick",
            "kind": kind,
            "processed": processed,
            "duration_ms": duration_ms,
            "drained": drained,
        },
    )
    return WorkerTickResponse(
        kind=kind,
        worker_id=worker_id,
        processed=processed,
        budget_seconds=budget_seconds,
        duration_ms=duration_ms,
        drained=drained,
    )


__all__: list[Any] = ["router", "build_worker", "WORKER_KINDS", "WorkerTickResponse"]
