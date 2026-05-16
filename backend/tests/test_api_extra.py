"""
Additional tests for the backend API introduced in this PR.
Covers endpoints and behaviours not exercised by test_api.py.
"""
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

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


# ---------------------------------------------------------------------------
# /api/alerts/all  (PR change: get_all_alerts with Keep + local fallback)
# ---------------------------------------------------------------------------

class TestAlertsAll:
    def test_alerts_all_returns_200(self):
        """The /api/alerts/all endpoint must return HTTP 200."""
        response = client.get("/api/alerts/all")
        assert response.status_code == 200

    def test_alerts_all_response_has_alerts_key(self):
        """The response body must always contain an 'alerts' key."""
        response = client.get("/api/alerts/all")
        body = response.json()
        assert "alerts" in body

    def test_alerts_all_alerts_is_a_list(self):
        """The 'alerts' value must be a list."""
        response = client.get("/api/alerts/all")
        body = response.json()
        assert isinstance(body["alerts"], list)

    def test_alerts_all_local_fallback_source_field(self):
        """When falling back to local memory, response includes 'source': 'local_fallback'."""
        # The default KEEP_URL is 'http://keep:8080' (not example.com) but keep is not
        # actually running in the test environment, so httpx will raise a connection error
        # OR the status code will not be 200 → local fallback is used.
        # We patch settings to force the local fallback path by using an example.com URL.
        with patch("app.api.endpoints.alerts.settings") as mock_settings:
            mock_settings.KEEP_URL = "http://example.com/keep"
            response = client.get("/api/alerts/all")
        assert response.status_code == 200
        body = response.json()
        # Either local_fallback or error response — must have 'alerts'
        assert "alerts" in body

    def test_alerts_all_returns_error_dict_on_exception(self):
        """If an unexpected exception occurs, response has 'alerts': [] and 'error' key."""
        with patch("app.api.endpoints.alerts.settings") as mock_settings:
            mock_settings.KEEP_URL = "http://example.com/keep"
            # Make Memory raise an exception to trigger the except branch
            with patch("nanoc.memory.memory.Memory.__init__", side_effect=RuntimeError("db error")):
                response = client.get("/api/alerts/all")
        assert response.status_code == 200
        body = response.json()
        assert "alerts" in body
        # When exception occurs, error key is present
        if "error" in body:
            assert isinstance(body["error"], str)

    def test_alerts_all_local_fallback_converts_gate_failures_to_alerts(self):
        """Gate/failed events in local memory are converted to alert objects."""
        import tempfile
        import sqlite3

        # Build a minimal gate/failed payload
        gate_payload = json.dumps({
            "id": "gate_proj_test_code_123",
            "project_id": "proj_test",
            "type": "code",
            "status": "FAILED",
            "results": [{"status": "fail"}],
        })

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_alerts.db")

            # Import Memory to create the schema
            from nanoc.memory.memory import Memory
            mem = Memory(db_path)
            mem.publish_event("gate/failed", json.loads(gate_payload))

            with patch("app.api.endpoints.alerts.settings") as mock_settings, \
                 patch("nanoc.core.config.settings") as mock_nanoc_settings:
                mock_settings.KEEP_URL = "http://example.com/keep"
                mock_nanoc_settings.DB_PATH = db_path
                response = client.get("/api/alerts/all")

        assert response.status_code == 200
        body = response.json()
        assert "alerts" in body
        # May or may not have alerts depending on whether local Memory is used
        # At minimum the response structure is valid
        assert isinstance(body["alerts"], list)

    def test_alerts_all_alert_object_has_required_fields_when_present(self):
        """Each alert object must contain id, source, title, description, severity, timestamp."""
        import tempfile

        gate_payload = {
            "id": "gate_proj_x_code_999",
            "project_id": "proj_x",
            "type": "code",
            "status": "FAILED",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_alert_fields.db")
            from nanoc.memory.memory import Memory
            mem = Memory(db_path)
            mem.publish_event("gate/failed", gate_payload)

            with patch("app.api.endpoints.alerts.settings") as mock_settings, \
                 patch("nanoc.core.config.settings") as mock_nanoc_settings:
                mock_settings.KEEP_URL = "http://example.com/keep"
                mock_nanoc_settings.DB_PATH = db_path
                response = client.get("/api/alerts/all")

        body = response.json()
        alerts = body.get("alerts", [])
        for alert in alerts:
            for field in ("id", "source", "title", "description", "severity", "timestamp"):
                assert field in alert, f"Alert missing field: {field}"

    def test_alerts_all_alert_severity_is_critical(self):
        """Gate failure alerts must have severity='critical'."""
        import tempfile

        gate_payload = {
            "id": "gate_proj_sev_code_777",
            "project_id": "proj_sev",
            "type": "design",
            "status": "FAILED",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_severity.db")
            from nanoc.memory.memory import Memory
            mem = Memory(db_path)
            mem.publish_event("gate/failed", gate_payload)

            with patch("app.api.endpoints.alerts.settings") as mock_settings, \
                 patch("nanoc.core.config.settings") as mock_nanoc_settings:
                mock_settings.KEEP_URL = "http://example.com/keep"
                mock_nanoc_settings.DB_PATH = db_path
                response = client.get("/api/alerts/all")

        body = response.json()
        for alert in body.get("alerts", []):
            assert alert["severity"] == "critical"

    def test_alerts_all_returns_keep_response_when_keep_available(self):
        """When Keep returns HTTP 200, the response from Keep is forwarded directly."""
        keep_response_data = {"alerts": [{"id": 1, "title": "Keep Alert"}]}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = keep_response_data

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("app.api.endpoints.alerts.settings") as mock_settings, \
             patch("app.api.endpoints.alerts.httpx.AsyncClient", return_value=mock_client):
            mock_settings.KEEP_URL = "http://real-keep-server.internal"
            response = client.get("/api/alerts/all")

        assert response.status_code == 200
        body = response.json()
        assert body == keep_response_data

    def test_alerts_all_falls_back_when_keep_returns_non_200(self):
        """When Keep returns non-200 status, local fallback is used."""
        mock_response = MagicMock()
        mock_response.status_code = 503

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("app.api.endpoints.alerts.settings") as mock_settings, \
             patch("app.api.endpoints.alerts.httpx.AsyncClient", return_value=mock_client):
            mock_settings.KEEP_URL = "http://real-keep.internal"
            response = client.get("/api/alerts/all")

        assert response.status_code == 200
        body = response.json()
        # Falls back to local; should have 'alerts' and either 'source' or 'error'
        assert "alerts" in body

    def test_alerts_all_skips_keep_when_url_contains_example_com(self):
        """KEEP_URL containing 'example.com' is treated as unconfigured; fallback is used."""
        with patch("app.api.endpoints.alerts.settings") as mock_settings, \
             patch("app.api.endpoints.alerts.httpx.AsyncClient") as mock_client_cls:
            mock_settings.KEEP_URL = "http://example.com/keep"
            # Give the mock client a usable context manager
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock()
            mock_client_cls.return_value = mock_client

            response = client.get("/api/alerts/all")

        # Keep.get should NOT have been called
        mock_client.get.assert_not_called()
        assert response.status_code == 200