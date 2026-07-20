from fastapi.testclient import TestClient

from backend.api.main import create_app


def test_health_returns_ok() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_openapi_schema_is_served() -> None:
    with TestClient(create_app()) as client:
        assert client.get("/openapi.json").status_code == 200
