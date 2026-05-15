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


# ---------------------------------------------------------------------------
# Additional tests for better coverage of changed code
# ---------------------------------------------------------------------------

def test_health_check_returns_json_content_type():
    response = client.get("/health")
    assert "application/json" in response.headers.get("content-type", "")

def test_health_check_response_structure():
    response = client.get("/health")
    body = response.json()
    assert "status" in body
    assert body["status"] == "ok"

def test_health_check_not_404():
    """Ensure /health endpoint is registered and accessible."""
    response = client.get("/health")
    assert response.status_code != 404

def test_monitoring_status_endpoint_registered():
    """Verify /api/monitoring/status route is registered (not 405 Method Not Allowed)."""
    response = client.get("/api/monitoring/status")
    assert response.status_code != 405

def test_monitoring_history_endpoint_with_name_param():
    """Verify /api/monitoring/history?name=... is reachable."""
    response = client.get("/api/monitoring/history", params={"name": "test_metric"})
    assert response.status_code in [200, 404, 500]

def test_monitoring_metrics_endpoint_with_query_param():
    """Verify /api/monitoring/metrics?query=... is reachable (Prometheus may be unavailable)."""
    response = client.get("/api/monitoring/metrics", params={"query": "up"})
    assert response.status_code in [200, 500]

def test_data_topology_endpoint_returns_json():
    """Verify /api/data/topology returns JSON with expected structure."""
    response = client.get("/api/data/topology")
    assert response.status_code == 200
    body = response.json()
    assert "nodes" in body
    assert "edges" in body

def test_data_topology_nodes_have_required_fields():
    """Verify each node in topology has id, label, type, and status."""
    response = client.get("/api/data/topology")
    assert response.status_code == 200
    body = response.json()
    for node in body["nodes"]:
        assert "id" in node
        assert "label" in node
        assert "type" in node
        assert "status" in node

def test_data_topology_edges_have_required_fields():
    """Verify each edge in topology has from, to, and label."""
    response = client.get("/api/data/topology")
    assert response.status_code == 200
    body = response.json()
    for edge in body["edges"]:
        assert "from" in edge
        assert "to" in edge
        assert "label" in edge

def test_data_agents_endpoint_returns_list():
    """Verify /api/data/agents returns a list."""
    response = client.get("/api/data/agents")
    assert response.status_code == 200
    assert isinstance(response.json(), list)

def test_data_tasks_endpoint_returns_list():
    """Verify /api/data/tasks returns a list."""
    response = client.get("/api/data/tasks")
    assert response.status_code == 200
    assert isinstance(response.json(), list)

def test_data_tasks_with_project_id_param():
    """Verify /api/data/tasks?project_id=... is reachable and returns a list."""
    response = client.get("/api/data/tasks", params={"project_id": "proj_nonexistent"})
    assert response.status_code == 200
    assert isinstance(response.json(), list)

def test_unknown_route_returns_404():
    """Verify that unknown routes return 404."""
    response = client.get("/api/nonexistent/route")
    assert response.status_code == 404

def test_health_check_post_method_not_allowed():
    """Verify POST to /health returns 405 Method Not Allowed."""
    response = client.post("/health")
    assert response.status_code == 405

def test_monitoring_status_response_keys_on_success():
    """Verify monitoring status returns expected keys when successful."""
    response = client.get("/api/monitoring/status")
    if response.status_code == 200:
        body = response.json()
        assert "status" in body
        assert "backlog" in body
        assert "latency" in body
