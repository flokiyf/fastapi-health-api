from __future__ import annotations

import json
import os
from collections.abc import Mapping
from contextvars import ContextVar, Token
from typing import Any

_REQUEST_ENV: ContextVar[Any | None] = ContextVar("request_env", default=None)
_REQUEST_HEADERS: ContextVar[dict[str, str] | None] = ContextVar("request_headers", default=None)


def bind_request(
    scope: Mapping[str, Any],
) -> tuple[Token[Any | None], Token[dict[str, str] | None]]:
    """Expose the active ASGI request to tools without framework-specific helpers."""
    headers = {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in scope.get("headers", [])
    }
    return _REQUEST_ENV.set(scope.get("env")), _REQUEST_HEADERS.set(headers)


def reset_request(tokens: tuple[Token[Any | None], Token[dict[str, str] | None]]) -> None:
    _REQUEST_ENV.reset(tokens[0])
    _REQUEST_HEADERS.reset(tokens[1])


def request_env() -> Any | None:
    """Return the Cloudflare Worker environment attached to the ASGI request."""
    return _REQUEST_ENV.get()


def setting(name: str, default: str | None = None, *, env: Any | None = None) -> str | None:
    """Read a Worker binding first, then fall back to the local process environment."""
    current_env = env if env is not None else request_env()
    if current_env is not None:
        value = getattr(current_env, name, None)
        if value is not None:
            return str(value)
    return os.getenv(name, default)


def bearer_token() -> str | None:
    headers = _REQUEST_HEADERS.get() or {}
    authorization = headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return headers.get("x-api-key")


def load_upstreams() -> dict[str, dict[str, Any]]:
    raw = setting("MCP_UPSTREAMS", "{}") or "{}"
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("MCP_UPSTREAMS must be valid JSON.") from exc

    if not isinstance(value, Mapping):
        raise RuntimeError("MCP_UPSTREAMS must be a JSON object keyed by server name.")

    upstreams: dict[str, dict[str, Any]] = {}
    for name, config in value.items():
        if not isinstance(name, str) or not isinstance(config, Mapping):
            raise RuntimeError("Every MCP upstream must have a string name and an object config.")
        upstreams[name] = dict(config)
    return upstreams
