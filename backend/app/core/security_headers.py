"""En-têtes de sécurité HTTP (E-04).

Avant ce module, aucune réponse de l'API ne portait d'en-tête de sécurité :
ni ``X-Content-Type-Options``, ni politique de référent, ni protection
d'encadrement, ni HSTS. Les rapports PDF/ZIP et les textes extraits étant
servis depuis le même domaine que l'API, ces en-têtes sont de la défense en
profondeur gratuite.

Le middleware est placé **au plus externe** de la chaîne applicative : les
réponses du limiteur de débit (429), les erreurs de base (503) et les réponses
CORS de pré-vol reçoivent les mêmes en-têtes que les réponses métier.

``Strict-Transport-Security`` n'est émis que lorsque la configuration impose
des cookies ``Secure`` (staging/production) : l'annoncer depuis une réponse
HTTP en clair serait ignoré par les navigateurs et mentirait sur le service.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import settings as module_settings
from app.core.config import settings_of

#: Directives CSP sûres pour une API JSON/téléchargements : elles n'ont aucun
#: effet sur du contenu scripté servi par le frontend Next.js (domaine distinct)
#: et interdisent l'encadrement et les plugins depuis les réponses de l'API.
CONTENT_SECURITY_POLICY = "frame-ancestors 'none'; object-src 'none'; base-uri 'none'"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        active = settings_of(getattr(request, "app", None), fallback=module_settings)
        if getattr(active, "auth_cookie_secure", False):
            headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


#: E-05 : surface publique de la documentation interactive. Les routes FastAPI
#: de ``/docs`` et ``/openapi.json`` exposent le schéma complet de l'API. Ce
#: garde les ferme dynamiquement selon ``api_docs_enabled`` : la construction de
#: l'application décide du montage par défaut (refusé en staging/production),
#: et ce middleware rend la décision réversible et testable à l'exécution —
#: un opt-in ``API_DOCS_ENABLED=true`` reste possible sans redéploiement.
LOCKED_DOC_PATHS = frozenset({"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"})


class DocsLockdownMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path in LOCKED_DOC_PATHS:
            active = settings_of(getattr(request, "app", None), fallback=module_settings)
            if not getattr(active, "api_docs_enabled", True):
                from starlette.responses import JSONResponse

                return JSONResponse(
                    status_code=404,
                    content={"detail": "Not Found"},
                    headers={"Cache-Control": "no-store"},
                )
        return await call_next(request)
