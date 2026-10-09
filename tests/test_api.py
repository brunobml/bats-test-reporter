from fastapi.testclient import TestClient
from unittest.mock import MagicMock
from src.main import app, storage
from src.storage.base import RunSummary

client = TestClient(app)


def test_healthz():
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_api_runs_empty():
    storage.list_all_runs = MagicMock(return_value=[])
    resp = client.get("/api/runs")
    assert resp.status_code == 200
    assert resp.json() == []


def test_metrics_empty():
    storage.list_all_runs = MagicMock(return_value=[])
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "No test runs" in resp.text
