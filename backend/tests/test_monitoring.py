"""
Tests for backend/app/api/endpoints/monitoring.py (as modified in this PR).

The PR simplified monitoring.py to:
  - GET /status  → static mock dict (no DB/Prometheus dependency)
  - GET /metrics → proxy to Prometheus; raises HTTPException(500) on failure
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# GET /api/monitoring/status
# ---------------------------------------------------------------------------

class TestMonitoringStatus:
    def test_returns_200(self):
        response = client.get("/api/monitoring/status")
        assert response.status_code == 200

    def test_response_is_json(self):
        response = client.get("/api/monitoring/status")
        assert "application/json" in response.headers["content-type"]

    def test_latency_field_present_and_correct(self):
        response = client.get("/api/monitoring/status")
        assert response.json()["latency"] == "12ms"

    def test_uptime_field_present_and_correct(self):
        response = client.get("/api/monitoring/status")
        assert response.json()["uptime"] == "99.99%"

    def test_traffic_field_present_and_correct(self):
        response = client.get("/api/monitoring/status")
        assert response.json()["traffic"] == "1.2 Gbps"

    def test_status_field_present_and_correct(self):
        response = client.get("/api/monitoring/status")
        assert response.json()["status"] == "nominal"

    def test_response_has_expected_keys(self):
        response = client.get("/api/monitoring/status")
        body = response.json()
        assert {"latency", "uptime", "traffic", "status", "backlog"}.issubset(set(body.keys()))

    def test_backlog_key_present(self):
        response = client.get("/api/monitoring/status")
        assert "backlog" in response.json()

    def test_status_is_idempotent(self):
        """Calling the endpoint twice returns the same value."""
        r1 = client.get("/api/monitoring/status")
        r2 = client.get("/api/monitoring/status")
        assert r1.json() == r2.json()


# ---------------------------------------------------------------------------
# GET /api/monitoring/metrics
# ---------------------------------------------------------------------------

class TestMonitoringMetrics:
    def test_requires_query_param(self):
        """Missing required `query` param → 422 Unprocessable Entity."""
        response = client.get("/api/monitoring/metrics")
        assert response.status_code == 422

    def test_empty_query_param_accepted_by_routing(self):
        """An empty string query param still reaches the handler (may still fail at httpx level)."""
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.json.return_value = {"status": "success", "data": {}}
            mock_async_client = AsyncMock()
            mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
            mock_async_client.__aexit__ = AsyncMock(return_value=False)
            mock_async_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_async_client

            response = client.get("/api/monitoring/metrics", params={"query": ""})
            assert response.status_code == 200

    def test_proxies_prometheus_response_on_success(self):
        """When Prometheus returns data, /metrics relays the JSON body."""
        fake_data = {"status": "success", "data": {"resultType": "vector", "result": []}}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.json.return_value = fake_data
            mock_async_client = AsyncMock()
            mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
            mock_async_client.__aexit__ = AsyncMock(return_value=False)
            mock_async_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_async_client

            response = client.get("/api/monitoring/metrics", params={"query": "up"})
            assert response.status_code == 200
            assert response.json() == fake_data

    def test_returns_500_when_prometheus_connection_fails(self):
        """A connection error to Prometheus must produce HTTP 500."""
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_async_client = AsyncMock()
            mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
            mock_async_client.__aexit__ = AsyncMock(return_value=False)
            mock_async_client.get = AsyncMock(
                side_effect=httpx.ConnectError("Connection refused")
            )
            mock_client_cls.return_value = mock_async_client

            response = client.get("/api/monitoring/metrics", params={"query": "up"})
            assert response.status_code == 500

    def test_500_detail_contains_exception_message(self):
        """HTTPException detail must carry the underlying error message."""
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_async_client = AsyncMock()
            mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
            mock_async_client.__aexit__ = AsyncMock(return_value=False)
            mock_async_client.get = AsyncMock(
                side_effect=RuntimeError("prometheus gone")
            )
            mock_client_cls.return_value = mock_async_client

            response = client.get("/api/monitoring/metrics", params={"query": "cpu"})
            assert response.status_code == 500
            assert "prometheus gone" in response.json()["detail"]

    def test_raises_on_http_error_status(self):
        """If Prometheus returns a non-2xx response, raise_for_status triggers 500."""
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_response = MagicMock()
            mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "503 Service Unavailable",
                request=MagicMock(),
                response=MagicMock(),
            )
            mock_async_client = AsyncMock()
            mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
            mock_async_client.__aexit__ = AsyncMock(return_value=False)
            mock_async_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_async_client

            response = client.get("/api/monitoring/metrics", params={"query": "up"})
            assert response.status_code == 500

    def test_query_param_forwarded_to_prometheus(self):
        """The `query` parameter should be forwarded in the Prometheus API call."""
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.json.return_value = {}
            mock_async_client = AsyncMock()
            mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
            mock_async_client.__aexit__ = AsyncMock(return_value=False)
            mock_async_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_async_client

            client.get("/api/monitoring/metrics", params={"query": "node_memory_free"})

            call_kwargs = mock_async_client.get.call_args
            # params dict must include our query string
            assert call_kwargs[1]["params"]["query"] == "node_memory_free"

    def test_history_endpoint_exists(self):
        response = client.get("/api/monitoring/history", params={"name": "latency"})
        assert response.status_code == 200