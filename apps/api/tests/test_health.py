"""Smoke tests for the FastAPI app."""

from __future__ import annotations

from fastapi.testclient import TestClient

from job_assist.main import app

client = TestClient(app)


def test_health() -> None:
    """Health endpoint returns ok."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.0.1"}


def test_root() -> None:
    """Root endpoint returns app metadata."""
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["name"] == "job-assist-api"
