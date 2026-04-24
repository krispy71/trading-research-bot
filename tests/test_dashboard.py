# tests/test_dashboard.py
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from storage.db import Database

@pytest.fixture
def db():
    d = Database(":memory:")
    d.init_schema()
    return d

@pytest.fixture
def client(db):
    from dashboard.app import create_app
    app = create_app(db)
    return TestClient(app)

def test_index_returns_200(client):
    response = client.get("/")
    assert response.status_code == 200

def test_runs_returns_200(client):
    response = client.get("/runs")
    assert response.status_code == 200

def test_run_detail_404_for_missing(client):
    response = client.get("/runs/999")
    assert response.status_code == 404

def test_runs_compare_returns_200(client):
    response = client.get("/runs/compare")
    assert response.status_code == 200

def test_equity_returns_200(client):
    response = client.get("/equity")
    assert response.status_code == 200
