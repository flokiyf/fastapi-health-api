import asyncio
from collections.abc import Iterator

import pytest
from fastmcp import Client
from starlette.testclient import TestClient

from app import app, gateway_operation, mcp
from audit import clear_memory_events, sanitize


@pytest.fixture(scope="module")
def http_client() -> Iterator[TestClient]:
    with TestClient(app) as client:
        yield client


def test_health_returns_gateway_metadata(http_client: TestClient) -> None:
    response = http_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "ai-safeguard-mcp-gateway",
        "mcp_endpoint": "/mcp",
    }


def test_exactly_one_mcp_tool_is_exposed() -> None:
    async def inspect_tools() -> list[str]:
        async with Client(mcp) as client:
            return [tool.name for tool in await client.list_tools()]

    assert asyncio.run(inspect_tools()) == ["ai_gateway"]


def test_discover_returns_no_servers_when_unconfigured(monkeypatch) -> None:
    monkeypatch.setenv("MCP_UPSTREAMS", "{}")

    result = asyncio.run(gateway_operation("discover"))

    assert result == {"ok": True, "action": "discover", "servers": {}}


def test_sensitive_values_are_redacted_recursively() -> None:
    payload = {
        "query": "hello",
        "authorization": "Bearer secret",
        "nested": {"api_key": "abc", "safe": True},
    }

    assert sanitize(payload) == {
        "query": "hello",
        "authorization": "[REDACTED]",
        "nested": {"api_key": "[REDACTED]", "safe": True},
    }


def test_audit_endpoint_is_secure_by_default(
    monkeypatch, http_client: TestClient
) -> None:
    clear_memory_events()
    monkeypatch.delenv("MCP_API_KEY", raising=False)
    monkeypatch.delenv("AUDIT_API_KEY", raising=False)

    response = http_client.get("/audit/events")

    assert response.status_code == 503


def test_audit_endpoint_accepts_configured_bearer_key(
    monkeypatch, http_client: TestClient
) -> None:
    clear_memory_events()
    monkeypatch.setenv("AUDIT_API_KEY", "test-audit-key")

    response = http_client.get(
        "/audit/events",
        headers={"Authorization": "Bearer test-audit-key"},
    )

    assert response.status_code == 200
    assert response.json() == {"events": [], "count": 0}
