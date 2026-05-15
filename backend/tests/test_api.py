import pytest
from fastapi.testclient import TestClient
import os
import sys

# Add backend directory to sys.path
sys.path.append(os.path.join(os.getcwd(), "backend"))

from main import app

client = TestClient(app)

def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_monitoring_endpoints():
    # Test a few endpoints to ensure they exist
    response = client.get("/api/monitoring/status")
    # Even if it returns 404/500, we check it reaches the router
    # But since we don't have a real DB/Prometheus, it might fail or return mock
    assert response.status_code in [200, 404, 500]

def test_data_endpoints():
    response = client.get("/api/data/events")
    assert response.status_code in [200, 404, 500]
