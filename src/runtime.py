from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

from fastmcp.server.dependencies import get_http_headers, get_http_request


def request_env() -> Any | None:
    """Return the Cloudflare Worker environment attached to the ASGI request."""
    try:
        return get_http_request().scope.get("env")
    except RuntimeError:
        return None


def setting(name: str, default: str | None = None) -> str | None:
    """Read a Worker binding first, then fall back to the local process environment."""
    env = request_env()
    if env is not None:
        value = getattr(env, name, None)
        if value is not None:
            return str(value)
    return os.getenv(name, default)


def bearer_token() -> str | None:
    headers = get_http_headers()
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
