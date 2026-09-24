from __future__ import annotations

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address


def get_client_identifier(request: Request) -> str:
    """Identify client by API Key header or remote IP address."""
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return f"apikey:{api_key.strip()}"
    client_ip = get_remote_address(request)
    return f"ip:{client_ip or '127.0.0.1'}"


limiter = Limiter(
    key_func=get_client_identifier,
    default_limits=["200/minute"],
    headers_enabled=False,
    storage_uri="memory://",
)
