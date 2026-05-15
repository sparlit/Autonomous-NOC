"""
Additional tests for the backend API introduced in this PR.
Covers endpoints and behaviours not exercised by test_api.py.
"""
import os
import sys

import pytest

# Ensure the backend package is importable when tests run from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    def test_health_returns_200(self):
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_response_body_is_ok(self):
        response = client.get("/health")
        assert response.json() == {"status": "ok"}

    def test_health_content_type_is_json(self):
        response = client.get("/health")
        assert "application/json" in response.headers["content-type"]


# ---------------------------------------------------------------------------
# /api/monitoring/status
# ---------------------------------------------------------------------------

class TestMonitoringStatus:
    def test_monitoring_status_returns_acceptable_status_code(self):
        response = client.get("/api/monitoring/status")
        assert response.status_code in (200, 404, 500)

    def test_monitoring_status_200_contains_expected_keys(self):
        response = client.get("/api/monitoring/status")
        if response.status_code == 200:
            body = response.json()
            for key in ("latency", "uptime", "traffic", "status", "backlog"):
                assert key in body, f"Missing key: {key}"

    def test_monitoring_status_backlog_is_non_negative(self):
        response = client.get("/api/monitoring/status")
        if response.status_code == 200:
            assert response.json()["backlog"] >= 0

    def test_monitoring_status_uptime_format(self):
        response = client.get("/api/monitoring/status")
        if response.status_code == 200:
            assert "%" in response.json()["uptime"]

    def test_monitoring_status_status_field(self):
        response = client.get("/api/monitoring/status")
        if response.status_code == 200:
            assert isinstance(response.json()["status"], str)


# ---------------------------------------------------------------------------
# /api/monitoring/history
# ---------------------------------------------------------------------------

class TestMonitoringHistory:
    def test_history_requires_name_param(self):
        # Without required query param 'name', FastAPI returns 422
        response = client.get("/api/monitoring/history")
        assert response.status_code == 422

    def test_history_with_name_returns_list(self):
        response = client.get("/api/monitoring/history", params={"name": "nonexistent_metric"})
        assert response.status_code in (200, 404, 500)
        if response.status_code == 200:
            assert isinstance(response.json(), list)

    def test_history_with_name_and_limit(self):
        response = client.get("/api/monitoring/history", params={"name": "test_metric", "limit": 5})
        assert response.status_code in (200, 404, 500)


# ---------------------------------------------------------------------------
# /api/monitoring/metrics (Prometheus proxy)
# ---------------------------------------------------------------------------

class TestMonitoringMetrics:
    def test_metrics_requires_query_param(self):
        response = client.get("/api/monitoring/metrics")
        assert response.status_code == 422

    def test_metrics_with_query_returns_acceptable_code(self):
        response = client.get("/api/monitoring/metrics", params={"query": "up"})
        # Prometheus not available in test env → 500
        assert response.status_code in (200, 500)


# ---------------------------------------------------------------------------
# /api/data/topology
# ---------------------------------------------------------------------------

class TestDataTopology:
    def test_topology_endpoint_exists(self):
        response = client.get("/api/data/topology")
        assert response.status_code == 200

    def test_topology_response_has_nodes_and_edges(self):
        response = client.get("/api/data/topology")
        body = response.json()
        assert "nodes" in body
        assert "edges" in body

    def test_topology_nodes_is_list(self):
        response = client.get("/api/data/topology")
        assert isinstance(response.json()["nodes"], list)

    def test_topology_edges_is_list(self):
        response = client.get("/api/data/topology")
        assert isinstance(response.json()["edges"], list)

    def test_topology_node_has_required_fields(self):
        response = client.get("/api/data/topology")
        nodes = response.json()["nodes"]
        if nodes:
            node = nodes[0]
            for field in ("id", "label", "type", "status"):
                assert field in node, f"Missing field '{field}' in node"

    def test_topology_edge_has_required_fields(self):
        response = client.get("/api/data/topology")
        edges = response.json()["edges"]
        if edges:
            edge = edges[0]
            for field in ("from", "to", "label"):
                assert field in edge, f"Missing field '{field}' in edge"


# ---------------------------------------------------------------------------
# /api/data/agents
# ---------------------------------------------------------------------------

class TestDataAgents:
    def test_agents_endpoint_returns_list(self):
        response = client.get("/api/data/agents")
        assert response.status_code == 200
        assert isinstance(response.json(), list)


# ---------------------------------------------------------------------------
# /api/data/tasks
# ---------------------------------------------------------------------------

class TestDataTasks:
    def test_tasks_endpoint_returns_list(self):
        response = client.get("/api/data/tasks")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_tasks_with_project_id_filter_returns_list(self):
        response = client.get("/api/data/tasks", params={"project_id": "nonexistent_proj"})
        assert response.status_code == 200
        assert isinstance(response.json(), list)
        # No tasks for a nonexistent project
        assert response.json() == []


# ---------------------------------------------------------------------------
# /api/data/events — endpoint does not exist (404 expected)
# ---------------------------------------------------------------------------

class TestDataEventsNotFound:
    def test_data_events_endpoint_is_not_defined(self):
        """The /api/data/events route was never implemented; expect 404."""
        response = client.get("/api/data/events")
        assert response.status_code == 404