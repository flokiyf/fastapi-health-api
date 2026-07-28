from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from runtime import request_env

MAX_VALUE_LENGTH = 32_000
MAX_JSON_LENGTH = 200_000
SENSITIVE_PARTS = (
    "authorization",
    "cookie",
    "credential",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "private_key",
)

_MEMORY_EVENTS: dict[str, dict[str, Any]] = {}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def new_event_id() -> str:
    return str(uuid4())


def _is_sensitive(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in SENSITIVE_PARTS)


def sanitize(value: Any, *, key: str = "") -> Any:
    """Convert an arbitrary MCP value to bounded, JSON-safe, redacted data."""
    if key and _is_sensitive(key):
        return "[REDACTED]"

    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        if len(value) <= MAX_VALUE_LENGTH:
            return value
        return f"{value[:MAX_VALUE_LENGTH]}...[TRUNCATED]"
    if isinstance(value, bytes):
        return f"[BYTES:{len(value)}]"
    if callable(value):
        name = getattr(value, "__name__", type(value).__name__)
        return f"[CALLABLE:{name}]"
    if isinstance(value, dict):
        return {
            str(item_key): sanitize(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list | tuple | set):
        return [sanitize(item) for item in value]
    if hasattr(value, "model_dump"):
        try:
            return sanitize(value.model_dump(mode="python", by_alias=True))
        except (TypeError, ValueError):
            return sanitize(str(value))
    if hasattr(value, "to_py"):
        return sanitize(value.to_py())
    if hasattr(value, "__dict__"):
        return sanitize(vars(value))
    return sanitize(str(value))


def encode(value: Any) -> str:
    serialized = json.dumps(sanitize(value), ensure_ascii=False, separators=(",", ":"))
    if len(serialized) <= MAX_JSON_LENGTH:
        return serialized
    return json.dumps(
        {"truncated": True, "preview": serialized[:MAX_JSON_LENGTH]},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def decode(value: str | None) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _audit_stub(env: Any | None = None) -> Any | None:
    current_env = env if env is not None else request_env()
    namespace = getattr(current_env, "AUDIT_LOG", None) if current_env is not None else None
    if namespace is None:
        return None
    return namespace.getByName("global-audit-log")


async def create_event(event: dict[str, Any], *, env: Any | None = None) -> None:
    clean_event = sanitize(event)
    stub = _audit_stub(env)
    if stub is not None:
        await stub.create_event(clean_event)
        return
    _MEMORY_EVENTS[clean_event["id"]] = clean_event


async def finish_event(
    event_id: str,
    *,
    status: str,
    response: Any = None,
    error: str | None = None,
    duration_ms: float,
    env: Any | None = None,
) -> None:
    completion = {
        "completed_at": utc_now(),
        "status": status,
        "response_json": encode(response),
        "error": sanitize(error),
        "duration_ms": round(duration_ms, 3),
    }
    stub = _audit_stub(env)
    if stub is not None:
        await stub.finish_event(event_id, completion)
        return

    event = _MEMORY_EVENTS.get(event_id)
    if event is not None:
        event.update(completion)


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    normalized["request"] = decode(normalized.pop("request_json", None))
    normalized["response"] = decode(normalized.pop("response_json", None))
    return normalized


async def list_events(
    *,
    limit: int = 50,
    method: str | None = None,
    status: str | None = None,
    env: Any | None = None,
) -> list[dict[str, Any]]:
    bounded_limit = max(1, min(limit, 200))
    stub = _audit_stub(env)
    if stub is not None:
        rows = await stub.list_events(bounded_limit, method, status)
        return [_normalize_row(dict(row)) for row in rows]

    events = list(reversed(list(_MEMORY_EVENTS.values())))
    if method:
        events = [event for event in events if event.get("method") == method]
    if status:
        events = [event for event in events if event.get("status") == status]
    return [_normalize_row(event) for event in events[:bounded_limit]]


async def get_event(event_id: str, *, env: Any | None = None) -> dict[str, Any] | None:
    stub = _audit_stub(env)
    if stub is not None:
        row = await stub.get_event(event_id)
        return _normalize_row(dict(row)) if row else None

    event = _MEMORY_EVENTS.get(event_id)
    return _normalize_row(event) if event else None


def clear_memory_events() -> None:
    _MEMORY_EVENTS.clear()
