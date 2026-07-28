import hmac
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from time import perf_counter
from typing import Any, Literal
from urllib.parse import urlparse

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from audit import (
    MAX_JSON_LENGTH,
    create_event,
    finish_event,
    get_event,
    list_events,
    new_event_id,
    sanitize,
    utc_now,
)
from runtime import bearer_token, bind_request, load_upstreams, reset_request, setting

mcp = FastMCP(
    name="AI SafeGuard Gateway",
    instructions=(
        "Use ai_gateway as the only route for external tools. First call action='discover' "
        "to inspect configured servers and tools. Then call action='execute' with the selected "
        "server, tool_name and arguments. Always include the original user request in query and "
        "a conversation or trace identifier in trace when available."
    ),
    json_response=True,
    stateless_http=True,
)


def _require_gateway_auth() -> None:
    expected = setting("MCP_API_KEY")
    if not expected:
        raise ToolError("MCP_API_KEY is not configured on the server.")

    supplied = bearer_token()
    if supplied is None or not hmac.compare_digest(supplied, expected):
        raise ToolError("Unauthorized gateway call.")


def _upstream(name: str) -> dict[str, Any]:
    upstreams = load_upstreams()
    config = upstreams.get(name)
    if config is None:
        raise ToolError(f"Unknown upstream server: {name}")

    url = config.get("url")
    if not isinstance(url, str) or not url:
        raise ToolError(f"Upstream server '{name}' has no valid URL.")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ToolError(f"Upstream server '{name}' has an invalid HTTP URL.")
    return config


def _allowed_tool(config: dict[str, Any], tool_name: str) -> bool:
    allowed = config.get("allowed_tools")
    return not isinstance(allowed, list) or tool_name in allowed


@asynccontextmanager
async def _client(config: dict[str, Any]) -> AsyncIterator[ClientSession]:
    token_env = config.get("token_env")
    token = setting(token_env) if isinstance(token_env, str) and token_env else None
    timeout = config.get("timeout_seconds", 30)
    try:
        timeout = max(1, min(int(timeout), 120))
    except (TypeError, ValueError):
        timeout = 30
    headers = {"Authorization": f"Bearer {token}"} if token else None
    async with streamablehttp_client(
        config["url"],
        headers=headers,
        timeout=timeout,
        sse_read_timeout=timeout,
    ) as (read_stream, write_stream, _), ClientSession(
        read_stream,
        write_stream,
        read_timeout_seconds=timedelta(seconds=timeout),
    ) as client:
        await client.initialize()
        yield client


def _model_dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="python", by_alias=True)
    return sanitize(value)


async def gateway_operation(
    action: Literal["discover", "execute"],
    server: str | None = None,
    tool_name: str | None = None,
    arguments: dict[str, Any] | None = None,
    query: str | None = None,
    trace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    del query, trace  # They remain visible in the audited MCP request payload.
    upstreams = load_upstreams()

    if action == "discover":
        names = [server] if server else sorted(upstreams)
        discovered: dict[str, Any] = {}
        for name in names:
            if name is None:
                continue
            config = _upstream(name)
            try:
                async with _client(config) as client:
                    tools = (await client.list_tools()).tools
            except Exception as exc:
                discovered[name] = {"ok": False, "error": str(exc)}
                continue

            visible_tools = [tool for tool in tools if _allowed_tool(config, tool.name)]
            discovered[name] = {
                "ok": True,
                "tools": [_model_dump(tool) for tool in visible_tools],
            }
        return {"ok": True, "action": "discover", "servers": discovered}

    if server is None or tool_name is None:
        raise ToolError("server and tool_name are required for action='execute'.")

    config = _upstream(server)
    if not _allowed_tool(config, tool_name):
        raise ToolError(f"Tool '{tool_name}' is not allowed on upstream '{server}'.")

    try:
        async with _client(config) as client:
            result = await client.call_tool(tool_name, arguments or {})
    except Exception as exc:
        raise ToolError(f"Upstream '{server}' call failed: {exc}") from exc

    return {
        "ok": True,
        "action": "execute",
        "server": server,
        "tool": tool_name,
        "result": _model_dump(result),
    }


@mcp.tool(
    name="ai_gateway",
    description=(
        "Mandatory audited gateway for every external tool call. Use action='discover' to list "
        "the tools available from configured servers, then action='execute' to invoke one. Pass "
        "the original user request in query and trace metadata whenever available. Never call a "
        "downstream tool directly when this gateway is configured."
    ),
)
async def ai_gateway(
    action: Literal["discover", "execute"],
    server: str | None = None,
    tool_name: str | None = None,
    arguments: dict[str, Any] | None = None,
    query: str | None = None,
    trace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _require_gateway_auth()
    return await gateway_operation(action, server, tool_name, arguments, query, trace)


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "service": "ai-safeguard-mcp-gateway",
            "mcp_endpoint": "/mcp",
        }
    )


def _audit_authorized(request: Request) -> tuple[bool, int]:
    env = request.scope.get("env")
    expected = setting("AUDIT_API_KEY", env=env) or setting("MCP_API_KEY", env=env)
    if not expected:
        return False, 503
    authorization = request.headers.get("authorization", "")
    supplied = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    if not supplied:
        supplied = request.headers.get("x-api-key", "")
    return hmac.compare_digest(supplied, expected), 401


@mcp.custom_route("/audit/events", methods=["GET"])
async def audit_events(request: Request) -> JSONResponse:
    authorized, failure_status = _audit_authorized(request)
    if not authorized:
        return JSONResponse({"detail": "Audit access denied."}, status_code=failure_status)

    try:
        limit = int(request.query_params.get("limit", "50"))
    except ValueError:
        return JSONResponse({"detail": "limit must be an integer."}, status_code=422)

    events = await list_events(
        limit=limit,
        method=request.query_params.get("method"),
        status=request.query_params.get("status"),
        env=request.scope.get("env"),
    )
    return JSONResponse({"events": events, "count": len(events)})


@mcp.custom_route("/audit/events/{event_id}", methods=["GET"])
async def audit_event(request: Request) -> JSONResponse:
    authorized, failure_status = _audit_authorized(request)
    if not authorized:
        return JSONResponse({"detail": "Audit access denied."}, status_code=failure_status)

    event = await get_event(request.path_params["event_id"], env=request.scope.get("env"))
    if event is None:
        return JSONResponse({"detail": "Audit event not found."}, status_code=404)
    return JSONResponse(event)


def _decode_json(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return raw.decode("utf-8", errors="replace")


class MCPAuditMiddleware:
    """Persist the complete JSON-RPC exchange for every request sent to /mcp."""

    def __init__(self, wrapped_app: ASGIApp):
        self.wrapped_app = wrapped_app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/mcp"):
            await self.wrapped_app(scope, receive, send)
            return

        request_messages: list[Message] = []
        request_body = bytearray()
        while True:
            message = await receive()
            request_messages.append(message)
            if message["type"] != "http.request":
                break
            request_body.extend(message.get("body", b""))
            if not message.get("more_body", False):
                break

        message_index = 0

        async def replay_receive() -> Message:
            nonlocal message_index
            if message_index < len(request_messages):
                message = request_messages[message_index]
                message_index += 1
                return message
            return await receive()

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        safe_headers = {
            key: value
            for key, value in headers.items()
            if key in {"cf-ray", "user-agent", "x-client-id", "x-request-id", "mcp-session-id"}
        }
        payload = _decode_json(bytes(request_body))
        rpc_method = payload.get("method") if isinstance(payload, dict) else None
        event_id = new_event_id()
        started = perf_counter()
        env = scope.get("env")

        await create_event(
            {
                "id": event_id,
                "created_at": utc_now(),
                "completed_at": None,
                "status": "started",
                "method": rpc_method or f"http/{scope.get('method', 'UNKNOWN')}",
                "source": "client",
                "client_id": headers.get("x-client-id"),
                "session_id": headers.get("mcp-session-id"),
                "request_id": payload.get("id") if isinstance(payload, dict) else None,
                "headers_json": safe_headers,
                "request_json": {
                    "http_method": scope.get("method"),
                    "path": scope.get("path"),
                    "body": payload,
                },
                "response_json": None,
                "error": None,
                "duration_ms": None,
            },
            env=env,
        )

        response_status = 500
        response_body = bytearray()
        request_tokens = bind_request(scope)

        async def audited_send(message: Message) -> None:
            nonlocal response_status
            if message["type"] == "http.response.start":
                response_status = message["status"]
            elif message["type"] == "http.response.body" and len(response_body) <= MAX_JSON_LENGTH:
                remaining = MAX_JSON_LENGTH + 1 - len(response_body)
                response_body.extend(message.get("body", b"")[:remaining])
            await send(message)

        try:
            await self.wrapped_app(scope, replay_receive, audited_send)
        except Exception as exc:
            await finish_event(
                event_id,
                status="error",
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=(perf_counter() - started) * 1000,
                env=env,
            )
            raise
        finally:
            reset_request(request_tokens)

        await finish_event(
            event_id,
            status="success" if response_status < 400 else "error",
            response={"http_status": response_status, "body": _decode_json(bytes(response_body))},
            duration_ms=(perf_counter() - started) * 1000,
            env=env,
        )


mcp_http_app = mcp.streamable_http_app()
app = MCPAuditMiddleware(mcp_http_app)
