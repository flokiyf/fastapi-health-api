from fastapi.testclient import TestClient

from app import app

client = TestClient(app)


def test_health_returns_ok() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["content-type"] == "application/json"


def test_only_health_route_is_exposed() -> None:
    assert {route.path for route in app.routes} == {"/health"}
