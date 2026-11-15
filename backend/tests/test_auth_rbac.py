"""Role-based access control tests for the NOC control plane.

Covers the policy in :mod:`app.core.auth` and its enforcement on every surface:

* ``ADMIN`` — terminal WebSocket, manual automation runs, remediation queue.
* ``OPERATOR`` — read-only monitoring/data/alerts/automation history and ``/ws``.
* public — ``/health`` only.

The suite also pins the two deliberate defaults:

* an **unset** ``NOC_ADMIN_TOKEN`` disables privileged surfaces (fail closed), and
* an **unset** ``NOC_OPERATOR_TOKEN`` keeps reads open so a fresh clone still
  renders a live dashboard (with a startup warning).
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core import auth
from app.core.auth import Role, auth_status, extract_token, resolve_role, role_allows
from app.core.config import settings as app_settings
from main import app
from nanoc.core.config import settings as nanoc_settings

ADMIN = "unit-test-admin-token"
OPERATOR = "unit-test-operator-token"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def no_tokens(monkeypatch):
    """Neither secret configured — the default of a fresh checkout."""
    monkeypatch.setattr(app_settings, "NOC_ADMIN_TOKEN", "", raising=False)
    monkeypatch.setattr(app_settings, "NOC_OPERATOR_TOKEN", "", raising=False)
    monkeypatch.setattr(nanoc_settings, "TERMINAL_ACCESS_TOKEN", "", raising=False)


@pytest.fixture
def both_tokens(monkeypatch):
    """Full RBAC: an admin secret and a read-only secret."""
    monkeypatch.setattr(app_settings, "NOC_ADMIN_TOKEN", ADMIN, raising=False)
    monkeypatch.setattr(app_settings, "NOC_OPERATOR_TOKEN", OPERATOR, raising=False)
    monkeypatch.setattr(nanoc_settings, "TERMINAL_ACCESS_TOKEN", "", raising=False)


@pytest.fixture
def admin_only(monkeypatch):
    """Admin secret set, operator secret unset (reads stay open)."""
    monkeypatch.setattr(app_settings, "NOC_ADMIN_TOKEN", ADMIN, raising=False)
    monkeypatch.setattr(app_settings, "NOC_OPERATOR_TOKEN", "", raising=False)
    monkeypatch.setattr(nanoc_settings, "TERMINAL_ACCESS_TOKEN", "", raising=False)


@pytest.fixture
def client():
    """Plain TestClient with no default credentials."""
    return TestClient(app)


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Role resolution
# ---------------------------------------------------------------------------

class TestResolveRole:
    def test_admin_token_resolves_to_admin(self, both_tokens):
        assert resolve_role(ADMIN) is Role.ADMIN

    def test_operator_token_resolves_to_operator(self, both_tokens):
        assert resolve_role(OPERATOR) is Role.OPERATOR

    def test_unknown_token_is_anonymous_not_operator(self, both_tokens):
        """An invalid credential is not a degraded credential."""
        assert resolve_role("nope") is Role.ANONYMOUS

    def test_missing_token_is_anonymous(self, both_tokens):
        assert resolve_role(None) is Role.ANONYMOUS
        assert resolve_role("") is Role.ANONYMOUS

    def test_empty_configured_token_never_matches_empty_presentation(self, no_tokens):
        """Fail closed: "" == "" must not grant access."""
        assert resolve_role("") is Role.ANONYMOUS

    def test_legacy_terminal_access_token_still_works_as_admin(self, monkeypatch):
        monkeypatch.setattr(app_settings, "NOC_ADMIN_TOKEN", "", raising=False)
        monkeypatch.setattr(nanoc_settings, "TERMINAL_ACCESS_TOKEN", "legacy-secret", raising=False)
        assert resolve_role("legacy-secret") is Role.ADMIN

    def test_tokens_are_case_sensitive(self, both_tokens):
        assert resolve_role(ADMIN.upper()) is Role.ANONYMOUS

    def test_surrounding_whitespace_is_tolerated(self, both_tokens):
        assert resolve_role(f"  {ADMIN}\n") is Role.ADMIN

    def test_non_ascii_token_does_not_raise(self, both_tokens):
        """hmac.compare_digest raises on non-ASCII str; the helper must not."""
        assert resolve_role("tökén-✓") is Role.ANONYMOUS

    def test_role_ordering(self):
        assert role_allows(Role.ADMIN, Role.ADMIN)
        assert role_allows(Role.ADMIN, Role.OPERATOR)
        assert role_allows(Role.OPERATOR, Role.OPERATOR)
        assert not role_allows(Role.OPERATOR, Role.ADMIN)
        assert not role_allows(Role.ANONYMOUS, Role.OPERATOR)


# ---------------------------------------------------------------------------
# Token transport
# ---------------------------------------------------------------------------

class TestTokenTransport:
    def _conn(self, headers=None, query=None):
        conn = MagicMock()
        conn.headers = headers or {}
        conn.query_params = query or {}
        return conn

    def test_bearer_header(self):
        assert extract_token(self._conn({"authorization": "Bearer abc"})) == "abc"

    def test_bearer_is_case_insensitive_on_the_scheme(self):
        assert extract_token(self._conn({"Authorization": "bearer abc"})) == "abc"

    def test_x_noc_token_header(self):
        assert extract_token(self._conn({"x-noc-token": "abc"})) == "abc"

    def test_query_parameter(self):
        assert extract_token(self._conn(query={"token": "abc"})) == "abc"

    def test_bearer_wins_over_query_parameter(self):
        assert extract_token(self._conn({"authorization": "Bearer hdr"}, {"token": "q"})) == "hdr"

    def test_no_credentials(self):
        assert extract_token(self._conn()) == ""

    def test_non_string_query_value_is_ignored(self):
        assert extract_token(self._conn(query={"token": ["a", "b"]})) == ""


# ---------------------------------------------------------------------------
# Configuration reporting
# ---------------------------------------------------------------------------

class TestAuthStatus:
    def test_never_leaks_the_token(self, both_tokens):
        status = auth_status()
        assert ADMIN not in str(status)
        assert OPERATOR not in str(status)

    def test_reports_disabled_privileged_surfaces_without_admin_token(self, no_tokens):
        status = auth_status()
        assert status["admin_configured"] is False
        assert status["privileged_endpoints"] == "disabled"
        assert status["read_endpoints"] == "open"

    def test_reports_token_protected_reads(self, both_tokens):
        status = auth_status()
        assert status["read_endpoints"] == "token"
        assert status["privileged_endpoints"] == "token"


# ---------------------------------------------------------------------------
# /health — the only public surface
# ---------------------------------------------------------------------------

class TestHealthIsPublic:
    def test_health_needs_no_token(self, both_tokens, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_health_leaks_no_network_state(self, both_tokens, client):
        body = client.get("/health").json()
        assert set(body) == {"status", "auth"}
        assert "topology" not in str(body)


# ---------------------------------------------------------------------------
# Read surfaces — OPERATOR
# ---------------------------------------------------------------------------

READ_PATHS = [
    "/api/monitoring/status",
    "/api/monitoring/series",
    "/api/data/topology",
    "/api/alerts/summary",
    "/api/alerts/all",
    "/api/automation/rules",
    "/api/automation/runs",
    "/api/automation/status",
    "/api/stream/status",
]


class TestReadSurfaces:
    @pytest.mark.parametrize("path", READ_PATHS)
    def test_open_while_no_operator_token_is_configured(self, no_tokens, client, path):
        """A fresh checkout still renders a live dashboard."""
        assert client.get(path).status_code == 200

    @pytest.mark.parametrize("path", READ_PATHS)
    def test_rejects_anonymous_once_an_operator_token_is_set(self, both_tokens, client, path):
        assert client.get(path).status_code == 401

    @pytest.mark.parametrize("path", READ_PATHS)
    def test_accepts_the_operator_token(self, both_tokens, client, path):
        assert client.get(path, headers=_bearer(OPERATOR)).status_code == 200

    @pytest.mark.parametrize("path", READ_PATHS)
    def test_accepts_the_admin_token_too(self, both_tokens, client, path):
        """ADMIN is a superset of OPERATOR."""
        assert client.get(path, headers=_bearer(ADMIN)).status_code == 200

    @pytest.mark.parametrize("path", READ_PATHS)
    def test_rejects_a_wrong_token(self, both_tokens, client, path):
        assert client.get(path, headers=_bearer("wrong")).status_code == 401


# ---------------------------------------------------------------------------
# Privileged surfaces — ADMIN
# ---------------------------------------------------------------------------

class TestPrivilegedSurfaces:
    def test_manual_run_is_disabled_when_no_admin_token_is_configured(self, no_tokens, client):
        """Fail closed: an unconfigured control plane cannot run remediation."""
        response = client.post("/api/automation/rules/any/run", json={})
        assert response.status_code == 403
        assert "NOC_ADMIN_TOKEN" in response.json()["detail"]

    def test_manual_run_requires_a_credential(self, both_tokens, client):
        assert client.post("/api/automation/rules/any/run", json={}).status_code == 401

    def test_manual_run_rejects_the_operator_token(self, both_tokens, client):
        """A read-only role must never trigger remediation."""
        response = client.post(
            "/api/automation/rules/any/run", json={}, headers=_bearer(OPERATOR)
        )
        assert response.status_code == 403

    def test_manual_run_accepts_the_admin_token(self, both_tokens, client):
        with patch(
            "app.api.endpoints.automation.automation_engine.run_rule",
            new=AsyncMock(return_value=[{"outcome": "success"}]),
        ):
            response = client.post(
                "/api/automation/rules/any/run", json={}, headers=_bearer(ADMIN)
            )
        assert response.status_code == 200
        assert response.json()["trigger"] == "manual"

    def test_pending_actions_is_disabled_without_an_admin_token(self, no_tokens, client):
        response = client.get("/api/automation/pending")
        assert response.status_code == 403

    def test_pending_actions_requires_a_credential(self, both_tokens, client):
        assert client.get("/api/automation/pending").status_code == 401

    def test_pending_actions_rejects_the_operator_token(self, both_tokens, client):
        assert client.get(
            "/api/automation/pending", headers=_bearer(OPERATOR)
        ).status_code == 403

    def test_pending_actions_accepts_the_admin_token(self, both_tokens, client):
        fake_store = MagicMock()
        fake_store.list_pending_actions.return_value = ([{"command": "x"}], 1)
        with patch(
            "app.api.endpoints.automation.automation_engine._store", fake_store
        ):
            response = client.get("/api/automation/pending", headers=_bearer(ADMIN))
        assert response.status_code == 200
        assert response.json()["total"] == 1


# ---------------------------------------------------------------------------
# /ws — the dashboard event stream (OPERATOR)
# ---------------------------------------------------------------------------

class TestDashboardSocket:
    def test_unauthenticated_client_is_refused(self, both_tokens):
        client = TestClient(app)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws"):
                pass

    def test_wrong_token_is_refused(self, both_tokens):
        client = TestClient(app)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws?token=nope"):
                pass

    def test_operator_token_connects_and_receives_hello(self, both_tokens):
        client = TestClient(app)
        with client.websocket_connect(f"/ws?token={OPERATOR}") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello"
            assert hello["auth"] == "operator"

    def test_admin_token_connects_too(self, both_tokens):
        client = TestClient(app)
        with client.websocket_connect(f"/ws?token={ADMIN}") as ws:
            assert ws.receive_json()["auth"] == "admin"

    def test_bearer_header_connects(self, both_tokens):
        client = TestClient(app)
        with client.websocket_connect("/ws", headers=_bearer(OPERATOR)) as ws:
            assert ws.receive_json()["type"] == "hello"

    def test_open_when_no_operator_token_is_configured(self, no_tokens):
        client = TestClient(app)
        with client.websocket_connect("/ws") as ws:
            assert ws.receive_json()["auth"] == "anonymous"

    def test_ping_is_answered_with_pong(self, both_tokens):
        client = TestClient(app)
        with client.websocket_connect(f"/ws?token={OPERATOR}") as ws:
            ws.receive_json()  # hello
            ws.send_text('{"type": "ping", "ts": "t0"}')
            pong = ws.receive_json()
            assert pong["type"] == "pong"

    def test_garbage_frame_does_not_kill_the_stream(self, both_tokens):
        client = TestClient(app)
        with client.websocket_connect(f"/ws?token={OPERATOR}") as ws:
            ws.receive_json()
            ws.send_text("not json at all")
            ws.send_text('{"type": "ping", "ts": "t1"}')
            assert ws.receive_json()["type"] == "pong"
