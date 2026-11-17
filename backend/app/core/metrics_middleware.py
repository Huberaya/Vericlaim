"""Middleware de mesure HTTP (A-09).

Deux règles, toutes deux apprises d'un incident réel :

1. **le chemin est normalisé**. Une métrique qui contient
   ``/api/v1/documents/<uuid>`` crée une série par document : la cardinalité
   explose et la métrique devient inutilisable au moment où on en a besoin. On
   utilise donc le *gabarit* de route (``request.scope["route"].path``), jamais
   l'URL reçue ;
2. **rien n'est mesuré si on ne peut pas le faire**. Le middleware n'échoue
   jamais la requête : une métrique manquante est un incident d'observabilité,
   pas une panne de service.
"""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.metrics import REGISTRY

#: Les routes de santé sont interrogées toutes les 10 s par le superviseur :
#: les compter dans le trafic fausserait le taux d'erreur et la latence.
EXCLUDED_PATHS = frozenset({"/healthz", "/readyz", "/metrics"})


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str) and path:
        return path
    # Route non résolue (404, erreur de routage) : on le dit sans inventer.
    return "<unmatched>"


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in EXCLUDED_PATHS:
            return await call_next(request)

        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            duration = time.perf_counter() - started
            try:
                REGISTRY.observe_request(
                    method=request.method,
                    route=_route_template(request),
                    status_code=status_code,
                    duration=duration,
                )
            except Exception:  # noqa: BLE001 - l'observabilité ne casse jamais le service
                pass
