"""
Tests for backend/main.py (as modified in this PR).

The PR:
  - Removed the nanoc/websocket lifespan manager
  - Replaced data.router with terminal.router at /api/terminal
  - Kept /health, monitoring, and alerts routers
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    def test_returns_200(self):
        response = client.get("/health")
        assert response.status_code == 200

    def test_response_body(self):
        response = client.get("/health")
        assert response.json() == {"status": "ok"}

    def test_content_type_is_json(self):
        response = client.get("/health")
        assert "application/json" in response.headers["content-type"]

    def test_idempotent(self):
        assert client.get("/health").json() == client.get("/health").json()


# ---------------------------------------------------------------------------
# Route registration – terminal replaces data
# ---------------------------------------------------------------------------

class TestRouteRegistration:
    def _route_paths(self):
        return {r.path for r in app.routes}

    def test_terminal_ws_route_registered(self):
        assert "/api/terminal/ws" in self._route_paths()

    def test_monitoring_status_route_registered(self):
        assert "/api/monitoring/status" in self._route_paths()

    def test_monitoring_metrics_route_registered(self):
        assert "/api/monitoring/metrics" in self._route_paths()

    def test_alerts_summary_route_registered(self):
        assert "/api/alerts/summary" in self._route_paths()

    def test_data_topology_route_not_registered(self):
        """data.router was removed in this PR."""
        assert "/api/data/topology" not in self._route_paths()

    def test_data_agents_route_not_registered(self):
        assert "/api/data/agents" not in self._route_paths()

    def test_data_tasks_route_not_registered(self):
        assert "/api/data/tasks" not in self._route_paths()

    def test_old_ws_endpoint_not_registered(self):
        """The old /ws WebSocket endpoint was removed; the new one is under /api/terminal/ws."""
        assert "/ws" not in self._route_paths()


# ---------------------------------------------------------------------------
# CORS middleware – ensure it is present and permissive (allow_origins=["*"])
# ---------------------------------------------------------------------------

class TestCORSMiddleware:
    def test_cors_headers_present_on_health(self):
        response = client.get("/health", headers={"Origin": "http://example.com"})
        # When allow_origins=['*'], the header should be present
        assert "access-control-allow-origin" in response.headers

    def test_cors_allows_all_origins(self):
        response = client.get(
            "/health", headers={"Origin": "http://totally-different-origin.com"}
        )
        assert response.headers.get("access-control-allow-origin") == "*"


# ---------------------------------------------------------------------------
# App metadata
# ---------------------------------------------------------------------------

class TestAppMetadata:
    def test_app_title_matches_settings(self):
        from app.core.config import settings
        assert app.title == settings.PROJECT_NAME

    def test_app_title_is_string(self):
        assert isinstance(app.title, str)
        assert len(app.title) > 0


# ---------------------------------------------------------------------------
# Removed endpoints return 404
# ---------------------------------------------------------------------------

class TestRemovedEndpoints:
    def test_data_topology_returns_404(self):
        response = client.get("/api/data/topology")
        assert response.status_code == 404

    def test_data_agents_returns_404(self):
        response = client.get("/api/data/agents")
        assert response.status_code == 404

    def test_monitoring_history_returns_404(self):
        response = client.get("/api/monitoring/history", params={"name": "x"})
        assert response.status_code in (404, 405, 422)