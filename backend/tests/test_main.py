from fastapi.testclient import TestClient

from app.config import settings
from app.main import app

client = TestClient(app)


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "recoverai"}


def test_config_loads():
    assert settings.DATABASE_URL is not None
    assert settings.LLM_PROVIDER in ("gemini", "openai", "anthropic")
