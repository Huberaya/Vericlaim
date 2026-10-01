"""Structured logging with a request/job correlation context (chantier C16).

Before this module there was no logging configuration at all: `logging.basicConfig`
appeared in two worker entry points and nowhere else, so the API's logs were
unstructured, undated and uncorrelated. In an incident, correlating a client
complaint with what the server did was guesswork.

What is implemented here:

* **JSON lines**, one object per log record, so a collector can index fields instead
  of parsing prose. Non-JSON output is opt-in (`LOG_FORMAT=text`) for local reading.
* **A correlation context** carried by `contextvars`: `request_id`, `organization_id`
  and `user_id` are attached to *every* record emitted while handling a request, even
  by code that never saw the request object. `contextvars` rather than globals,
  because a thread or a task must not inherit another request's identifiers.
* **No secrets, ever.** The formatter serialises `extra` fields and drops any key
  whose name looks like a credential — a log line is the easiest place to leak a
  token, and the password work of C14 would be undone by one careless `logger.info`.

`request_id` accepts a client-supplied `X-Request-Id` only if it matches a strict
pattern: an attacker must not be able to inject newlines or forged fields into the
log stream.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from typing import Any

CONTEXT_REQUEST_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "vericlaim_request_id", default=None
)
CONTEXT_ORGANIZATION_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "vericlaim_organization_id", default=None
)
CONTEXT_USER_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "vericlaim_user_id", default=None
)
CONTEXT_JOB_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "vericlaim_job_id", default=None
)

#: Accepts a client-supplied correlation identifier. Deliberately strict: no spaces,
#: quotes, newlines or braces, so a value cannot forge a field in a JSON log line.
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

#: Attribute names whose value is never written to a log, whatever the caller passes.
SENSITIVE_MARKERS = (
    "password",
    "secret",
    "token",
    "authorization",
    "cookie",
    "csrf",
    "api_key",
    "apikey",
    "signature",
    "private",
)

#: LogRecord attributes that are not application payload and must not be re-emitted.
_STANDARD_ATTRIBUTES = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename", "module",
    "exc_info", "exc_text", "stack_info", "lineno", "funcName", "created", "msecs",
    "relativeCreated", "thread", "threadName", "processName", "process", "taskName",
    "message", "asctime",
}


def normalize_request_id(candidate: str | None) -> str | None:
    """Return a safe correlation id, or None when the header must be ignored."""
    if not candidate:
        return None
    candidate = candidate.strip()
    return candidate if REQUEST_ID_PATTERN.match(candidate) else None


def new_request_id() -> str:
    return uuid.uuid4().hex


def bind_log_context(**fields: str | None) -> None:
    """Attach correlation fields to the current context. Unknown keys are ignored."""
    if "request_id" in fields and fields["request_id"] is not None:
        CONTEXT_REQUEST_ID.set(fields["request_id"])
    if "organization_id" in fields and fields["organization_id"] is not None:
        CONTEXT_ORGANIZATION_ID.set(fields["organization_id"])
    if "user_id" in fields and fields["user_id"] is not None:
        CONTEXT_USER_ID.set(fields["user_id"])
    if "job_id" in fields and fields["job_id"] is not None:
        CONTEXT_JOB_ID.set(fields["job_id"])


def current_context() -> dict[str, str | None]:
    return {
        "request_id": CONTEXT_REQUEST_ID.get(),
        "organization_id": CONTEXT_ORGANIZATION_ID.get(),
        "user_id": CONTEXT_USER_ID.get(),
        "job_id": CONTEXT_JOB_ID.get(),
    }


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in SENSITIVE_MARKERS)


def _sanitize(value: Any, *, depth: int = 0) -> Any:
    """Make a value JSON-serialisable and free of credentials."""
    if depth > 4:
        return "<tronqué>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= 2000 else value[:2000] + "…"
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, dict):
        return {
            str(key): ("<masqué>" if _is_sensitive(str(key)) else _sanitize(item, depth=depth + 1))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        truncated = items[:50]
        rendered = [_sanitize(item, depth=depth + 1) for item in truncated]
        if len(items) > 50:
            rendered.append(f"<+{len(items) - 50} éléments>")
        return rendered
    return repr(value)[:500]


class JsonLogFormatter(logging.Formatter):
    """One JSON object per line, correlation fields included."""

    def __init__(self, *, service: str) -> None:
        super().__init__()
        self._service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "service": self._service,
            "message": record.getMessage(),
        }
        for key, value in current_context().items():
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key in _STANDARD_ATTRIBUTES or key.startswith("_"):
                continue
            payload[key] = "<masqué>" if _is_sensitive(key) else _sanitize(value)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(*, level: str | None = None, fmt: str | None = None, service: str = "vericlaim-api") -> None:
    """Install the structured handler on the root logger, idempotently.

    Called once at import of `app.main` and by both workers. Calling it twice
    replaces the handler instead of stacking duplicates — otherwise every line
    would be emitted twice, which is how log pipelines double their volume after a
    reload.
    """
    resolved_level = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    resolved_format = (fmt or os.getenv("LOG_FORMAT", "json")).lower()

    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_vericlaim_structured", False):
            root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler._vericlaim_structured = True  # type: ignore[attr-defined]
    if resolved_format == "text":
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    else:
        handler.setFormatter(JsonLogFormatter(service=service))
    root.addHandler(handler)
    root.setLevel(resolved_level)

    # Uvicorn installs its own handlers; let them propagate to the structured root
    # handler instead of writing a second, unstructured copy to stderr.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
