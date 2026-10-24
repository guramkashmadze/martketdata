from fastapi.testclient import TestClient

from marketdata.api.app import create_app
from marketdata.config import Settings

# Nothing listens on port 1, so connections are refused at once.
UNREACHABLE = "postgresql://marketdata:marketdata@127.0.0.1:1/marketdata"


def test_health_ok(database_url: str) -> None:
    app = create_app(Settings.model_validate({"database_url": database_url}))

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok"}


def test_health_503_when_database_is_down() -> None:
    app = create_app(Settings.model_validate({"database_url": UNREACHABLE}))

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "error", "db": "down"}
