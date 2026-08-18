"""Application smoke tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from studyforge import __version__
from studyforge.config import Settings
from studyforge.main import create_app


def test_app_builds_and_reports_healthy(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_startup_creates_the_local_data_directories(settings: Settings) -> None:
    assert not settings.data_dir.exists()
    with TestClient(create_app(settings)):
        pass
    assert settings.uploads_dir.is_dir()


def test_openapi_schema_is_served(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        schema = client.get("/api/openapi.json").json()
    assert schema["info"]["title"] == "StudyForge"
    assert schema["info"]["version"] == __version__
