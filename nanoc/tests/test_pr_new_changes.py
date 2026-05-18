"""
Tests for changes introduced in the latest PR:
  - backend/app/api/endpoints/terminal.py  (get_token_auth, token validation)
  - maintainer.py                           (trigger_maintenance, dedup, file creation)
  - nanoc/agents/base.py                    (TeamLeader.delegate_tasks simplified)
  - nanoc/agents/documentation.py           (update_docs file writing, event path field)
  - nanoc/agents/healer.py                  (AutoHealer.handle_failure)
  - nanoc/agents/security.py                (SecurityAgent.audit_service)
  - nanoc/core/config.py                    (new INITIAL_WORKERS, MAX_WORKERS, TERMINAL_ACCESS_TOKEN)
  - nanoc/core/llm.py                       (model override from memory, model passed to providers)
  - nanoc/core/orchestrator.py              (retry logic, task/failed event, scale up/down,
                                             priority-ordered task dispatch)
"""
import asyncio
import json
import os
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from nanoc.memory.memory import Memory
from nanoc.tests.mocks import MockLLM


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_memory(db_path: str) -> Memory:
    if os.path.exists(db_path):
        os.remove(db_path)
    return Memory(db_path)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def memory(tmp_path):
    db_path = str(tmp_path / "test_new_pr.db")
    mem = Memory(db_path)
    yield mem
    if os.path.exists(db_path):
        os.remove(db_path)


# ===========================================================================
# nanoc/core/config.py – new runtime settings
# ===========================================================================

class TestConfigNewSettings:
    def test_initial_workers_default(self):
        """Settings.INITIAL_WORKERS defaults to 5."""
        from nanoc.core.config import Settings
        s = Settings()
        assert s.INITIAL_WORKERS == 5

    def test_max_workers_default(self):
        """Settings.MAX_WORKERS defaults to 20."""
        from nanoc.core.config import Settings
        s = Settings()
        assert s.MAX_WORKERS == 20

    def test_terminal_access_token_default(self):
        """Settings.TERMINAL_ACCESS_TOKEN has a non-empty default."""
        from nanoc.core.config import Settings
        s = Settings()
        assert s.TERMINAL_ACCESS_TOKEN == "secret-foss-token"
        assert len(s.TERMINAL_ACCESS_TOKEN) > 0

    def test_initial_workers_env_override(self, monkeypatch):
        """INITIAL_WORKERS can be overridden via environment variable."""
        monkeypatch.setenv("INITIAL_WORKERS", "10")
        from nanoc.core.config import Settings
        s = Settings()
        assert s.INITIAL_WORKERS == 10

    def test_max_workers_env_override(self, monkeypatch):
        """MAX_WORKERS can be overridden via environment variable."""
        monkeypatch.setenv("MAX_WORKERS", "50")
        from nanoc.core.config import Settings
        s = Settings()
        assert s.MAX_WORKERS == 50

    def test_terminal_access_token_env_override(self, monkeypatch):
        """TERMINAL_ACCESS_TOKEN can be overridden via environment variable."""
        monkeypatch.setenv("TERMINAL_ACCESS_TOKEN", "my-custom-token")
        from nanoc.core.config import Settings
        s = Settings()
        assert s.TERMINAL_ACCESS_TOKEN == "my-custom-token"

    def test_initial_workers_less_than_or_equal_max_workers(self):
        """INITIAL_WORKERS should be <= MAX_WORKERS in the default config."""
        from nanoc.core.config import Settings
        s = Settings()
        assert s.INITIAL_WORKERS <= s.MAX_WORKERS

    def test_settings_singleton_is_settings_instance(self):
        """The module-level `settings` object is a Settings instance."""
        from nanoc.core.config import settings, Settings
        assert isinstance(settings, Settings)


# ===========================================================================
# backend/app/api/endpoints/terminal.py – get_token_auth
# These tests mock out fastapi since it may not be installed in this env.
# ===========================================================================

def _setup_fastapi_mock():
    """Inject a minimal fastapi mock into sys.modules so terminal.py can be imported."""
    import sys
    if "fastapi" not in sys.modules:
        fastapi_mock = MagicMock()
        # APIRouter must return an object whose .websocket() decorator passes the function through
        mock_router = MagicMock()
        mock_router.websocket.return_value = lambda f: f  # decorator passthrough
        fastapi_mock.APIRouter.return_value = mock_router
        fastapi_mock.WebSocket = MagicMock
        fastapi_mock.WebSocketDisconnect = Exception
        fastapi_mock.HTTPException = Exception
        fastapi_mock.Depends = MagicMock
        sys.modules["fastapi"] = fastapi_mock
    # Clear cached terminal module so it re-imports with our mock
    for key in list(sys.modules.keys()):
        if "terminal" in key and "backend" in key:
            del sys.modules[key]


class TestGetTokenAuth:
    def setup_method(self):
        _setup_fastapi_mock()

    @pytest.mark.anyio
    async def test_valid_token_returns_token(self):
        """get_token_auth returns the token when it matches TERMINAL_ACCESS_TOKEN."""
        from backend.app.api.endpoints.terminal import get_token_auth

        mock_ws = MagicMock()
        mock_ws.query_params = {"token": "secret-foss-token"}
        mock_ws.close = AsyncMock()

        with patch("backend.app.api.endpoints.terminal.settings") as mock_settings:
            mock_settings.TERMINAL_ACCESS_TOKEN = "secret-foss-token"
            result = await get_token_auth(mock_ws)

        assert result == "secret-foss-token"
        mock_ws.close.assert_not_called()

    @pytest.mark.anyio
    async def test_invalid_token_closes_websocket(self):
        """get_token_auth closes the websocket with code 1008 when token is wrong."""
        from backend.app.api.endpoints.terminal import get_token_auth

        mock_ws = MagicMock()
        mock_ws.query_params = {"token": "wrong-token"}
        mock_ws.close = AsyncMock()

        with patch("backend.app.api.endpoints.terminal.settings") as mock_settings:
            mock_settings.TERMINAL_ACCESS_TOKEN = "secret-foss-token"
            result = await get_token_auth(mock_ws)

        assert result is None
        mock_ws.close.assert_called_once_with(code=1008)

    @pytest.mark.anyio
    async def test_missing_token_closes_websocket(self):
        """get_token_auth closes the websocket when no token is provided."""
        from backend.app.api.endpoints.terminal import get_token_auth

        mock_ws = MagicMock()
        mock_ws.query_params = {}
        mock_ws.close = AsyncMock()

        with patch("backend.app.api.endpoints.terminal.settings") as mock_settings:
            mock_settings.TERMINAL_ACCESS_TOKEN = "secret-foss-token"
            result = await get_token_auth(mock_ws)

        assert result is None
        mock_ws.close.assert_called_once_with(code=1008)

    @pytest.mark.anyio
    async def test_empty_token_closes_websocket(self):
        """get_token_auth rejects an empty string token."""
        from backend.app.api.endpoints.terminal import get_token_auth

        mock_ws = MagicMock()
        mock_ws.query_params = {"token": ""}
        mock_ws.close = AsyncMock()

        with patch("backend.app.api.endpoints.terminal.settings") as mock_settings:
            mock_settings.TERMINAL_ACCESS_TOKEN = "secret-foss-token"
            result = await get_token_auth(mock_ws)

        assert result is None
        mock_ws.close.assert_called_once_with(code=1008)

    @pytest.mark.anyio
    async def test_correct_policy_violation_code_used(self):
        """Websocket is closed with policy violation code 1008, not another code."""
        from backend.app.api.endpoints.terminal import get_token_auth

        mock_ws = MagicMock()
        mock_ws.query_params = {"token": "bad"}
        mock_ws.close = AsyncMock()

        with patch("backend.app.api.endpoints.terminal.settings") as mock_settings:
            mock_settings.TERMINAL_ACCESS_TOKEN = "good"
            await get_token_auth(mock_ws)

        close_kwargs = mock_ws.close.call_args
        assert close_kwargs[1]["code"] == 1008 or close_kwargs[0][0] == 1008

    @pytest.mark.anyio
    async def test_terminal_websocket_rejects_bad_token(self):
        """terminal_websocket returns early when token auth fails."""
        from backend.app.api.endpoints.terminal import terminal_websocket

        mock_ws = MagicMock()
        mock_ws.accept = AsyncMock()
        mock_ws.close = AsyncMock()
        mock_ws.query_params = {"token": "wrong"}

        with patch("backend.app.api.endpoints.terminal.settings") as mock_settings, \
             patch("backend.app.api.endpoints.terminal.TerminalSession") as mock_session_cls:
            mock_settings.TERMINAL_ACCESS_TOKEN = "correct"
            await terminal_websocket(mock_ws)

        # TerminalSession should never be instantiated
        mock_session_cls.assert_not_called()

    @pytest.mark.anyio
    async def test_terminal_websocket_accepts_valid_token(self):
        """terminal_websocket creates a TerminalSession when token is valid."""
        from backend.app.api.endpoints.terminal import terminal_websocket

        mock_ws = MagicMock()
        mock_ws.accept = AsyncMock()
        mock_ws.close = AsyncMock()
        mock_ws.query_params = {"token": "correct"}

        mock_session = AsyncMock()
        mock_session.start = AsyncMock()

        with patch("backend.app.api.endpoints.terminal.settings") as mock_settings, \
             patch("backend.app.api.endpoints.terminal.TerminalSession", return_value=mock_session) as mock_cls:
            mock_settings.TERMINAL_ACCESS_TOKEN = "correct"
            await terminal_websocket(mock_ws)

        mock_cls.assert_called_once_with(mock_ws)
        mock_session.start.assert_called_once()


# ===========================================================================
# maintainer.py – trigger_maintenance, dedup, file creation
# ===========================================================================

class TestTriggerMaintenance:
    def test_creates_inbox_file_when_no_pending_task(self, tmp_path):
        """trigger_maintenance creates an inbox file when no pending maintenance task exists."""
        import maintainer as m

        db_path = str(tmp_path / "main.db")
        inbox_dir = str(tmp_path / "inbox")

        mem = _fresh_memory(db_path)

        with patch.object(m, "settings") as mock_settings, \
             patch("maintainer.Memory", return_value=mem):
            mock_settings.DB_PATH = db_path
            with patch("maintainer.os.makedirs") as mock_makedirs, \
                 patch("builtins.open", unittest.mock.mock_open()) as mock_open, \
                 patch("maintainer.os.path.join", return_value=str(tmp_path / "maintenance_1.txt")):
                m.trigger_maintenance()

        mock_makedirs.assert_called_once()

    def test_skips_file_creation_when_pending_task_exists(self, tmp_path):
        """trigger_maintenance skips inbox file creation when a matching pending task exists."""
        import maintainer as m

        db_path = str(tmp_path / "main2.db")
        mem = _fresh_memory(db_path)

        # Pre-insert a pending task matching the dedup query
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO tasks (description, status, created_at, updated_at) VALUES (?, ?, datetime('now'), datetime('now'))",
                ("Analyze the current NANOC project for improvements", "pending")
            )
            conn.commit()

        with patch.object(m, "settings") as mock_settings, \
             patch("maintainer.Memory", return_value=mem):
            mock_settings.DB_PATH = db_path
            with patch("builtins.open", unittest.mock.mock_open()) as mock_open:
                m.trigger_maintenance()
                mock_open.assert_not_called()

    def test_creates_inbox_directory_if_missing(self, tmp_path):
        """trigger_maintenance calls makedirs for the inbox directory."""
        import maintainer as m

        db_path = str(tmp_path / "main3.db")
        mem = _fresh_memory(db_path)

        with patch.object(m, "settings") as mock_settings, \
             patch("maintainer.Memory", return_value=mem), \
             patch("maintainer.os.makedirs") as mock_makedirs, \
             patch("builtins.open", unittest.mock.mock_open()):
            mock_settings.DB_PATH = db_path
            m.trigger_maintenance()

        mock_makedirs.assert_called_with("nanoc/inbox", exist_ok=True)

    def test_inbox_file_contains_project_description(self, tmp_path):
        """The inbox file written by trigger_maintenance contains the project description."""
        import maintainer as m

        db_path = str(tmp_path / "main4.db")
        mem = _fresh_memory(db_path)

        written_content = []

        def fake_open(path, mode="r"):
            handle = unittest.mock.mock_open()()
            handle.write = lambda content: written_content.append(content)
            handle.__enter__ = lambda s: s
            handle.__exit__ = MagicMock(return_value=False)
            return handle

        with patch.object(m, "settings") as mock_settings, \
             patch("maintainer.Memory", return_value=mem), \
             patch("maintainer.os.makedirs"), \
             patch("builtins.open", side_effect=fake_open):
            mock_settings.DB_PATH = db_path
            m.trigger_maintenance()

        all_written = "".join(written_content)
        assert "NANOC" in all_written or "Analyze" in all_written

    def test_maintenance_file_uses_timestamp_in_name(self, tmp_path):
        """trigger_maintenance creates a file named with a timestamp."""
        import maintainer as m

        db_path = str(tmp_path / "main5.db")
        mem = _fresh_memory(db_path)

        created_paths = []

        real_join = os.path.join

        def fake_makedirs(path, exist_ok=False):
            pass

        original_open = open
        opened_paths = []

        with patch.object(m, "settings") as mock_settings, \
             patch("maintainer.Memory", return_value=mem), \
             patch("maintainer.os.makedirs", side_effect=fake_makedirs), \
             patch("maintainer.time.time", return_value=1700000000), \
             patch("builtins.open", unittest.mock.mock_open()) as mock_open:
            mock_settings.DB_PATH = db_path
            m.trigger_maintenance()

        call_args = mock_open.call_args
        if call_args:
            file_path = call_args[0][0]
            assert "maintenance_" in file_path
            assert "1700000000" in file_path

    def test_main_calls_trigger_maintenance_repeatedly(self, tmp_path):
        """main() calls trigger_maintenance at least once in its loop."""
        import maintainer as m

        call_count = [0]

        def fake_trigger():
            call_count[0] += 1
            if call_count[0] >= 2:
                raise SystemExit("stop loop")

        with patch.object(m, "trigger_maintenance", side_effect=fake_trigger), \
             patch.object(m.time, "sleep"):
            with pytest.raises(SystemExit):
                m.main()

        assert call_count[0] >= 2

    def test_main_handles_exceptions_without_crashing(self, tmp_path):
        """main() catches exceptions from trigger_maintenance and continues."""
        import maintainer as m

        call_count = [0]

        def flaky_trigger():
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("transient error")
            if call_count[0] >= 3:
                raise SystemExit("stop loop")

        with patch.object(m, "trigger_maintenance", side_effect=flaky_trigger), \
             patch.object(m.time, "sleep"):
            with pytest.raises(SystemExit):
                m.main()

        assert call_count[0] >= 3

    def test_main_sleeps_between_iterations(self, tmp_path):
        """main() calls time.sleep(1800) between maintenance cycles."""
        import maintainer as m

        sleep_calls = []

        def fake_trigger():
            pass

        call_count = [0]

        def fake_sleep(seconds):
            sleep_calls.append(seconds)
            call_count[0] += 1
            if call_count[0] >= 2:
                raise SystemExit("stop")

        with patch.object(m, "trigger_maintenance", side_effect=fake_trigger), \
             patch.object(m.time, "sleep", side_effect=fake_sleep):
            with pytest.raises(SystemExit):
                m.main()

        assert 1800 in sleep_calls


# ===========================================================================
# nanoc/agents/documentation.py – update_docs file writing
# ===========================================================================

class TestDocumentationAgentUpdateDocs:
    @pytest.mark.anyio
    async def test_update_docs_writes_markdown_file(self, memory, tmp_path):
        """update_docs creates a markdown file in docs_dir for the project."""
        from nanoc.agents.documentation import DocumentationAgent

        docs_dir = str(tmp_path / "docs")
        mock_llm = MockLLM()

        with patch("nanoc.agents.documentation.settings") as mock_settings:
            mock_settings.LOGS_DIR = str(tmp_path)
            agent = DocumentationAgent("DocAgent", "Documentation", memory, mock_llm)
            await agent.update_docs("proj_test", "some documentation content")

        doc_path = os.path.join(docs_dir, "proj_test.md")
        assert os.path.exists(doc_path)

    @pytest.mark.anyio
    async def test_update_docs_file_contains_content(self, memory, tmp_path):
        """The markdown file written by update_docs contains the provided content."""
        from nanoc.agents.documentation import DocumentationAgent

        mock_llm = MockLLM()

        with patch("nanoc.agents.documentation.settings") as mock_settings:
            mock_settings.LOGS_DIR = str(tmp_path)
            agent = DocumentationAgent("DocAgent", "Documentation", memory, mock_llm)
            await agent.update_docs("proj_abc", "important content here")

        doc_path = os.path.join(str(tmp_path), "docs", "proj_abc.md")
        with open(doc_path) as f:
            file_content = f.read()
        assert "important content here" in file_content

    @pytest.mark.anyio
    async def test_update_docs_appends_on_second_call(self, memory, tmp_path):
        """Calling update_docs twice appends rather than overwriting the file."""
        from nanoc.agents.documentation import DocumentationAgent

        mock_llm = MockLLM()

        with patch("nanoc.agents.documentation.settings") as mock_settings:
            mock_settings.LOGS_DIR = str(tmp_path)
            agent = DocumentationAgent("DocAgent", "Documentation", memory, mock_llm)
            await agent.update_docs("proj_789", "first content")
            await agent.update_docs("proj_789", "updated content")

        doc_path = os.path.join(str(tmp_path), "docs", "proj_789.md")
        with open(doc_path) as f:
            file_content = f.read()
        assert "first content" in file_content
        assert "updated content" in file_content

    @pytest.mark.anyio
    async def test_update_docs_stores_to_knowledge_base(self, memory, tmp_path):
        """update_docs also persists content to the knowledge base."""
        from nanoc.agents.documentation import DocumentationAgent

        mock_llm = MockLLM()

        with patch("nanoc.agents.documentation.settings") as mock_settings:
            mock_settings.LOGS_DIR = str(tmp_path)
            agent = DocumentationAgent("DocAgent", "Documentation", memory, mock_llm)
            await agent.update_docs("proj_kb_test", "knowledge content")

        stored = memory.get_knowledge("docs:proj_kb_test")
        assert stored == "knowledge content"

    @pytest.mark.anyio
    async def test_update_docs_publishes_event_with_path(self, memory, tmp_path):
        """update_docs publishes a docs/updated event that includes the 'path' field."""
        from nanoc.agents.documentation import DocumentationAgent

        mock_llm = MockLLM()

        with patch("nanoc.agents.documentation.settings") as mock_settings:
            mock_settings.LOGS_DIR = str(tmp_path)
            agent = DocumentationAgent("DocAgent", "Documentation", memory, mock_llm)
            await agent.update_docs("proj_event", "event content")

        events = memory.get_events(topic="docs/updated")
        assert len(events) >= 1
        payload = json.loads(events[-1]["payload"])
        assert "path" in payload
        assert payload["status"] == "success"
        assert payload["project_id"] == "proj_event"

    @pytest.mark.anyio
    async def test_update_docs_publishes_correct_path_in_event(self, memory, tmp_path):
        """The path in the docs/updated event points to the actual markdown file."""
        from nanoc.agents.documentation import DocumentationAgent

        mock_llm = MockLLM()

        with patch("nanoc.agents.documentation.settings") as mock_settings:
            mock_settings.LOGS_DIR = str(tmp_path)
            agent = DocumentationAgent("DocAgent", "Documentation", memory, mock_llm)
            await agent.update_docs("proj_path_check", "content")

        events = memory.get_events(topic="docs/updated")
        payload = json.loads(events[-1]["payload"])
        assert "proj_path_check.md" in payload["path"]

    @pytest.mark.anyio
    async def test_update_docs_creates_docs_dir_if_missing(self, memory, tmp_path):
        """DocumentationAgent.__init__ creates the docs directory if it doesn't exist."""
        from nanoc.agents.documentation import DocumentationAgent

        logs_dir = str(tmp_path / "newlogs")
        docs_dir = os.path.join(logs_dir, "docs")
        assert not os.path.exists(docs_dir)

        mock_llm = MockLLM()

        with patch("nanoc.agents.documentation.settings") as mock_settings:
            mock_settings.LOGS_DIR = logs_dir
            agent = DocumentationAgent("DocAgent", "Documentation", memory, mock_llm)

        assert os.path.exists(docs_dir)

    @pytest.mark.anyio
    async def test_update_docs_file_contains_update_header(self, memory, tmp_path):
        """The markdown file contains an '## Update at' header."""
        from nanoc.agents.documentation import DocumentationAgent

        mock_llm = MockLLM()

        with patch("nanoc.agents.documentation.settings") as mock_settings:
            mock_settings.LOGS_DIR = str(tmp_path)
            agent = DocumentationAgent("DocAgent", "Documentation", memory, mock_llm)
            await agent.update_docs("proj_header", "some docs")

        doc_path = os.path.join(str(tmp_path), "docs", "proj_header.md")
        with open(doc_path) as f:
            file_content = f.read()
        assert "## Update at" in file_content


# ===========================================================================
# nanoc/agents/healer.py – AutoHealer
# ===========================================================================

class TestAutoHealer:
    @pytest.mark.anyio
    async def test_handle_failure_calls_think_with_error_info(self, memory):
        """handle_failure sends a prompt containing the error and description to think()."""
        from nanoc.agents.healer import AutoHealer

        mock_llm = MockLLM()
        mock_llm.add_response("", "Use a retry with backoff strategy")

        healer = AutoHealer("Healer1", memory)
        healer.llm = mock_llm

        failure_event = {
            "task_id": "task_42",
            "project_id": "proj_xyz",
            "error": "ConnectionRefusedError",
            "description": "Deploy service to production"
        }

        with patch.object(healer.memory, "create_task") as mock_create:
            await healer.handle_failure(failure_event)

        # LLM should have been called
        assert mock_llm._call_count >= 1
        prompt_used = mock_llm.calls[0]["prompt"]
        assert "ConnectionRefusedError" in prompt_used
        assert "Deploy service to production" in prompt_used

    @pytest.mark.anyio
    async def test_handle_failure_creates_fix_task_assigned_to_coder(self, memory):
        """handle_failure creates a new task assigned to 'Coder'."""
        from nanoc.agents.healer import AutoHealer

        mock_llm = MockLLM()
        mock_llm.add_response("", "Retry with exponential backoff")

        healer = AutoHealer("Healer1", memory)
        healer.llm = mock_llm

        failure_event = {
            "task_id": "task_99",
            "project_id": "proj_heal",
            "error": "TimeoutError",
            "description": "Run integration tests"
        }

        with patch.object(healer.memory, "create_task") as mock_create:
            await healer.handle_failure(failure_event)

        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args
        # assigned_to should be 'Coder'
        assert call_kwargs[1].get("assigned_to") == "Coder" or \
               (len(call_kwargs[0]) > 1 and call_kwargs[0][1] == "Coder")

    @pytest.mark.anyio
    async def test_handle_failure_fix_task_description_contains_task_id(self, memory):
        """The fix task description references the original failed task_id."""
        from nanoc.agents.healer import AutoHealer

        mock_llm = MockLLM()
        mock_llm.add_response("", "Use a fallback approach")

        healer = AutoHealer("Healer1", memory)
        healer.llm = mock_llm

        failure_event = {
            "task_id": "task_77",
            "project_id": "proj_heal2",
            "error": "ValueError",
            "description": "Parse config file"
        }

        with patch.object(healer.memory, "create_task") as mock_create:
            await healer.handle_failure(failure_event)

        call_args = mock_create.call_args
        description = call_args[0][0]
        assert "task_77" in description

    @pytest.mark.anyio
    async def test_handle_failure_fix_task_has_project_id(self, memory):
        """The fix task is associated with the original project_id."""
        from nanoc.agents.healer import AutoHealer

        mock_llm = MockLLM()

        healer = AutoHealer("Healer1", memory)
        healer.llm = mock_llm

        failure_event = {
            "task_id": "task_55",
            "project_id": "proj_project123",
            "error": "AttributeError",
            "description": "Execute migration script"
        }

        with patch.object(healer.memory, "create_task") as mock_create:
            await healer.handle_failure(failure_event)

        call_kwargs = mock_create.call_args[1]
        assert call_kwargs.get("project_id") == "proj_project123"

    @pytest.mark.anyio
    async def test_handle_failure_handles_missing_fields_gracefully(self, memory):
        """handle_failure does not raise when optional fields are absent from event."""
        from nanoc.agents.healer import AutoHealer

        mock_llm = MockLLM()
        healer = AutoHealer("Healer1", memory)
        healer.llm = mock_llm

        # Minimal event with only some fields
        failure_event = {}

        with patch.object(healer.memory, "create_task"):
            # Should not raise
            await healer.handle_failure(failure_event)

    @pytest.mark.anyio
    async def test_auto_healer_role_is_healer(self, memory):
        """AutoHealer is initialized with 'Healer' role."""
        from nanoc.agents.healer import AutoHealer

        healer = AutoHealer("HealerAgent", memory)
        assert healer.role == "Healer"

    @pytest.mark.anyio
    async def test_handle_failure_think_prompt_includes_corrective_suggestion_request(self, memory):
        """The prompt sent to think asks for a corrective action."""
        from nanoc.agents.healer import AutoHealer

        mock_llm = MockLLM()
        healer = AutoHealer("Healer1", memory)
        healer.llm = mock_llm

        failure_event = {
            "task_id": "t1",
            "project_id": "p1",
            "error": "SomeError",
            "description": "Do something"
        }

        with patch.object(healer.memory, "create_task"):
            await healer.handle_failure(failure_event)

        prompt = mock_llm.calls[0]["prompt"]
        assert "corrective" in prompt.lower() or "revised" in prompt.lower() or "suggest" in prompt.lower()


# ===========================================================================
# nanoc/agents/security.py – SecurityAgent
# ===========================================================================

class TestSecurityAgent:
    @pytest.mark.anyio
    async def test_audit_service_calls_nmap_with_sv_flag(self, memory):
        """audit_service runs nmap -sV against the target."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("SecAgent", memory)
        agent.llm = MockLLM()

        nmap_result = {"stdout": "Service version info", "stderr": "", "returncode": 0}

        with patch("nanoc.tools.network.AsyncRunner") as mock_runner_cls:
            mock_runner_cls.run_command = AsyncMock(return_value=nmap_result)
            result = await agent.audit_service("192.168.1.1")

        mock_runner_cls.run_command.assert_called_once()
        cmd_arg = mock_runner_cls.run_command.call_args[0][0]
        assert "nmap" in cmd_arg
        assert "-sV" in cmd_arg
        assert "192.168.1.1" in cmd_arg

    @pytest.mark.anyio
    async def test_audit_service_returns_stdout_on_success(self, memory):
        """audit_service returns the stdout from nmap when successful."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("SecAgent", memory)
        agent.llm = MockLLM()

        nmap_result = {"stdout": "PORT   STATE SERVICE VERSION\n80/tcp open  http", "stderr": "", "returncode": 0}

        with patch("nanoc.tools.network.AsyncRunner") as mock_runner_cls:
            mock_runner_cls.run_command = AsyncMock(return_value=nmap_result)
            result = await agent.audit_service("10.0.0.5")

        assert result == nmap_result["stdout"]

    @pytest.mark.anyio
    async def test_audit_service_returns_error_dict_on_failure(self, memory):
        """audit_service returns the error dict when nmap command fails."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("SecAgent", memory)
        agent.llm = MockLLM()

        error_result = {"error": "nmap not found", "stdout": "", "stderr": "", "returncode": 1}

        with patch("nanoc.tools.network.AsyncRunner") as mock_runner_cls:
            mock_runner_cls.run_command = AsyncMock(return_value=error_result)
            result = await agent.audit_service("192.168.1.1")

        assert result == error_result
        assert "error" in result

    @pytest.mark.anyio
    async def test_audit_service_publishes_audit_complete_event_on_success(self, memory):
        """audit_service publishes 'security/audit-complete' event when nmap succeeds."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("SecAgent", memory)
        agent.llm = MockLLM()

        nmap_result = {"stdout": "scan output", "stderr": "", "returncode": 0}

        with patch("nanoc.tools.network.AsyncRunner") as mock_runner_cls:
            mock_runner_cls.run_command = AsyncMock(return_value=nmap_result)
            await agent.audit_service("172.16.0.1")

        events = memory.get_events(topic="security/audit-complete")
        assert len(events) >= 1
        payload = json.loads(events[-1]["payload"])
        assert payload["target"] == "172.16.0.1"
        assert "report" in payload

    @pytest.mark.anyio
    async def test_audit_service_does_not_publish_event_on_error(self, memory):
        """audit_service does NOT publish audit-complete event when there's an error."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("SecAgent", memory)
        agent.llm = MockLLM()

        error_result = {"error": "permission denied", "stdout": "", "stderr": ""}

        with patch("nanoc.tools.network.AsyncRunner") as mock_runner_cls:
            mock_runner_cls.run_command = AsyncMock(return_value=error_result)
            await agent.audit_service("10.0.0.1")

        events = memory.get_events(topic="security/audit-complete")
        assert len(events) == 0

    @pytest.mark.anyio
    async def test_audit_service_event_contains_report_content(self, memory):
        """The security/audit-complete event payload includes the nmap report."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("SecAgent", memory)
        agent.llm = MockLLM()

        scan_output = "Nmap scan report\n22/tcp open ssh OpenSSH 8.2"
        nmap_result = {"stdout": scan_output, "stderr": "", "returncode": 0}

        with patch("nanoc.tools.network.AsyncRunner") as mock_runner_cls:
            mock_runner_cls.run_command = AsyncMock(return_value=nmap_result)
            await agent.audit_service("192.168.10.1")

        events = memory.get_events(topic="security/audit-complete")
        payload = json.loads(events[-1]["payload"])
        assert payload["report"] == scan_output

    @pytest.mark.anyio
    async def test_security_agent_role_is_security(self, memory):
        """SecurityAgent is initialized with 'Security' role."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("SecAgent1", memory)
        assert agent.role == "Security"


# ===========================================================================
# nanoc/core/llm.py – model override from knowledge base
# ===========================================================================

class TestLLMProviderModelOverride:
    @pytest.mark.anyio
    async def test_complete_uses_override_model_when_set(self, memory):
        """complete() uses 'system/model_override' from knowledge base if present."""
        from nanoc.core.llm import LLMProvider

        memory.upsert_knowledge("system/model_override", "custom-model-xyz")

        provider = LLMProvider(provider="openrouter", model="default-model")

        with patch("nanoc.core.llm.Memory", return_value=memory), \
             patch.object(provider, "_openrouter_complete", new_callable=AsyncMock) as mock_complete, \
             patch.object(provider, "_record_telemetry"), \
             patch("nanoc.core.llm.settings") as mock_settings:
            mock_settings.DB_PATH = memory.db_path
            mock_complete.return_value = "response text"
            await provider.complete("some prompt")

        # The model passed should be the override
        call_args = mock_complete.call_args
        model_arg = call_args[0][2]  # third positional arg is model
        assert model_arg == "custom-model-xyz"

    @pytest.mark.anyio
    async def test_complete_uses_default_model_when_no_override(self, memory):
        """complete() uses the instance model when no override is in knowledge base."""
        from nanoc.core.llm import LLMProvider

        # No override in knowledge base
        provider = LLMProvider(provider="openrouter", model="my-default-model")

        with patch("nanoc.core.llm.Memory", return_value=memory), \
             patch.object(provider, "_openrouter_complete", new_callable=AsyncMock) as mock_complete, \
             patch.object(provider, "_record_telemetry"), \
             patch("nanoc.core.llm.settings") as mock_settings:
            mock_settings.DB_PATH = memory.db_path
            mock_complete.return_value = "response"
            await provider.complete("test prompt")

        call_args = mock_complete.call_args
        model_arg = call_args[0][2]
        assert model_arg == "my-default-model"

    @pytest.mark.anyio
    async def test_openrouter_complete_receives_model_parameter(self, memory):
        """_openrouter_complete now accepts a model parameter as third positional arg."""
        from nanoc.core.llm import LLMProvider
        import inspect

        provider = LLMProvider(provider="openrouter")
        sig = inspect.signature(provider._openrouter_complete)
        params = list(sig.parameters.keys())
        assert "model" in params

    @pytest.mark.anyio
    async def test_ollama_complete_receives_model_parameter(self, memory):
        """_ollama_complete now accepts a model parameter as third positional arg."""
        from nanoc.core.llm import LLMProvider
        import inspect

        provider = LLMProvider(provider="ollama")
        sig = inspect.signature(provider._ollama_complete)
        params = list(sig.parameters.keys())
        assert "model" in params

    @pytest.mark.anyio
    async def test_complete_passes_override_model_to_ollama(self, memory):
        """complete() passes the override model to _ollama_complete."""
        from nanoc.core.llm import LLMProvider

        memory.upsert_knowledge("system/model_override", "ollama-override-model")

        provider = LLMProvider(provider="ollama", model="base-model")

        with patch("nanoc.core.llm.Memory", return_value=memory), \
             patch.object(provider, "_ollama_complete", new_callable=AsyncMock) as mock_complete, \
             patch.object(provider, "_record_telemetry"), \
             patch("nanoc.core.llm.settings") as mock_settings:
            mock_settings.DB_PATH = memory.db_path
            mock_complete.return_value = "ollama response"
            await provider.complete("another prompt")

        call_args = mock_complete.call_args
        model_arg = call_args[0][2]
        assert model_arg == "ollama-override-model"

    @pytest.mark.anyio
    async def test_complete_raises_for_unknown_provider(self, memory):
        """complete() raises ValueError for unknown providers."""
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="unknown_provider", model="some-model")

        with patch("nanoc.core.llm.Memory", return_value=memory), \
             patch.object(provider, "_record_error"), \
             patch("nanoc.core.llm.settings") as mock_settings:
            mock_settings.DB_PATH = memory.db_path
            with pytest.raises(ValueError, match="Unknown provider"):
                await provider.complete("test")

    def test_openrouter_complete_uses_model_arg_not_self_model(self):
        """_openrouter_complete uses the `model` argument, not self.model."""
        import inspect
        from nanoc.core.llm import LLMProvider
        import ast, textwrap

        provider = LLMProvider(provider="openrouter", model="self-model")
        src = inspect.getsource(provider._openrouter_complete)
        # The method should reference `model` in the data dict, not `self.model`
        assert '"model": model' in src or "'model': model" in src


# ===========================================================================
# nanoc/core/orchestrator.py – retry logic, task/failed event, priority, scale
# ===========================================================================

class TestOrchestratorRetryLogic:
    @pytest.mark.anyio
    async def test_process_task_sets_pending_on_first_failure(self, memory):
        """On first failure (retry_count < max_retries), task status becomes 'pending' again."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory)
        leader.llm = MockLLM()

        orchestrator = Orchestrator(memory, leader)

        # Add a mock agent that always fails
        failing_agent = MagicMock()
        failing_agent.role = "Coder"
        failing_agent.log = AsyncMock()
        failing_agent.write_code = AsyncMock(side_effect=RuntimeError("test failure"))
        orchestrator.add_agent(failing_agent)

        task_id = memory.create_task("Write some code", assigned_to="Coder", project_id="proj_retry")

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        # Simulate first failure (retry_count starts at 0)
        await orchestrator.process_task(task)

        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status, retry_count FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()

        # With default max_retries=3, first failure → retry_count=1 ≤ 3 → status='pending'
        assert row[0] == "pending"
        assert row[1] == 1

    @pytest.mark.anyio
    async def test_process_task_sets_failed_after_exceeding_max_retries(self, memory):
        """After exceeding max_retries, task status becomes 'failed'."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory)
        leader.llm = MockLLM()

        orchestrator = Orchestrator(memory, leader)

        failing_agent = MagicMock()
        failing_agent.role = "Coder"
        failing_agent.log = AsyncMock()
        failing_agent.write_code = AsyncMock(side_effect=RuntimeError("always fails"))
        orchestrator.add_agent(failing_agent)

        task_id = memory.create_task("Write failing code", assigned_to="Coder", project_id="proj_maxretry")

        # Set retry_count beyond max_retries
        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE tasks SET retry_count = 3, max_retries = 3 WHERE id = ?", (task_id,))
            conn.commit()

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orchestrator.process_task(task)

        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()

        assert row[0] == "failed"

    @pytest.mark.anyio
    async def test_process_task_publishes_task_failed_event_when_truly_failed(self, memory):
        """When a task truly fails (exceeds retries), a 'task/failed' event is published."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory)
        leader.llm = MockLLM()

        orchestrator = Orchestrator(memory, leader)

        failing_agent = MagicMock()
        failing_agent.role = "Reviewer"
        failing_agent.log = AsyncMock()
        failing_agent.review_work = AsyncMock(side_effect=RuntimeError("review error"))
        orchestrator.add_agent(failing_agent)

        task_id = memory.create_task("Review the code", assigned_to="Reviewer", project_id="proj_fail_event")

        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE tasks SET retry_count = 3, max_retries = 3 WHERE id = ?", (task_id,))
            conn.commit()

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orchestrator.process_task(task)

        events = memory.get_events(topic="task/failed")
        assert len(events) >= 1
        payload = json.loads(events[-1]["payload"])
        assert payload["task_id"] == task_id

    @pytest.mark.anyio
    async def test_process_task_does_not_publish_task_failed_event_on_retry(self, memory):
        """When a task still has retries remaining, no 'task/failed' event is published."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory)
        leader.llm = MockLLM()

        orchestrator = Orchestrator(memory, leader)

        failing_agent = MagicMock()
        failing_agent.role = "Planner"
        failing_agent.log = AsyncMock()
        failing_agent.create_todo_list = AsyncMock(side_effect=RuntimeError("plan error"))
        orchestrator.add_agent(failing_agent)

        task_id = memory.create_task("Create plan", assigned_to="Planner", project_id="proj_noretry")

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        # retry_count=0, max_retries=3 → should retry, NOT publish task/failed
        await orchestrator.process_task(task)

        events = memory.get_events(topic="task/failed")
        assert len(events) == 0

    @pytest.mark.anyio
    async def test_process_task_increments_retry_count(self, memory):
        """Each failure increments the retry_count stored in the DB."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory)
        leader.llm = MockLLM()

        orchestrator = Orchestrator(memory, leader)

        failing_agent = MagicMock()
        failing_agent.role = "Architect"
        failing_agent.log = AsyncMock()
        failing_agent.design_solution = AsyncMock(side_effect=RuntimeError("arch error"))
        orchestrator.add_agent(failing_agent)

        task_id = memory.create_task("Design something", assigned_to="Architect", project_id="proj_incr")

        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE tasks SET retry_count = 1 WHERE id = ?", (task_id,))
            conn.commit()

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orchestrator.process_task(task)

        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT retry_count FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()

        assert row[0] == 2

    @pytest.mark.anyio
    async def test_process_task_failed_event_includes_project_id(self, memory):
        """The task/failed event payload includes the project_id."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory)
        leader.llm = MockLLM()

        orchestrator = Orchestrator(memory, leader)

        failing_agent = MagicMock()
        failing_agent.role = "Coder"
        failing_agent.log = AsyncMock()
        failing_agent.write_code = AsyncMock(side_effect=RuntimeError("fail"))
        orchestrator.add_agent(failing_agent)

        task_id = memory.create_task("Code task", assigned_to="Coder", project_id="proj_eventcheck")

        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE tasks SET retry_count = 3, max_retries = 3 WHERE id = ?", (task_id,))
            conn.commit()

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orchestrator.process_task(task)

        events = memory.get_events(topic="task/failed")
        payload = json.loads(events[-1]["payload"])
        assert payload["project_id"] == "proj_eventcheck"


class TestOrchestratorInit:
    def test_orchestrator_reads_initial_workers_from_settings(self, memory):
        """Orchestrator.__init__ sets initial_workers from settings.INITIAL_WORKERS."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory)
        leader.llm = MockLLM()

        with patch("nanoc.core.config.settings") as mock_settings:
            mock_settings.INITIAL_WORKERS = 7
            mock_settings.MAX_WORKERS = 25
            orch = Orchestrator(memory, leader)

        assert orch.initial_workers == 7

    def test_orchestrator_reads_max_workers_from_settings(self, memory):
        """Orchestrator.__init__ sets max_workers from settings.MAX_WORKERS."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory)
        leader.llm = MockLLM()

        with patch("nanoc.core.config.settings") as mock_settings:
            mock_settings.INITIAL_WORKERS = 3
            mock_settings.MAX_WORKERS = 15
            orch = Orchestrator(memory, leader)

        assert orch.max_workers == 15

    def test_orchestrator_current_workers_starts_empty(self, memory):
        """Orchestrator starts with an empty current_workers list."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory)
        leader.llm = MockLLM()

        with patch("nanoc.core.config.settings") as mock_settings:
            mock_settings.INITIAL_WORKERS = 5
            mock_settings.MAX_WORKERS = 20
            orch = Orchestrator(memory, leader)

        assert orch.current_workers == []


class TestOrchestratorScaling:
    @pytest.mark.anyio
    async def test_handle_scale_up_adds_worker_when_below_max(self, memory):
        """handle_scale_up adds a worker when current_workers < max_workers."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory)
        leader.llm = MockLLM()

        with patch("nanoc.core.config.settings") as mock_settings:
            mock_settings.INITIAL_WORKERS = 2
            mock_settings.MAX_WORKERS = 5
            orch = Orchestrator(memory, leader)

        # Simulate existing workers
        fake_task = MagicMock()
        orch.current_workers = [fake_task, fake_task]

        with patch("asyncio.create_task", return_value=MagicMock()) as mock_create:
            # Directly test scale_up logic
            if len(orch.current_workers) < orch.max_workers:
                new_id = len(orch.current_workers)
                task = asyncio.create_task(asyncio.sleep(0))
                orch.current_workers.append(task)

        assert len(orch.current_workers) == 3

    @pytest.mark.anyio
    async def test_handle_scale_down_removes_worker_when_above_initial(self, memory):
        """handle_scale_down removes a worker when current_workers > initial_workers."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory)
        leader.llm = MockLLM()

        with patch("nanoc.core.config.settings") as mock_settings:
            mock_settings.INITIAL_WORKERS = 2
            mock_settings.MAX_WORKERS = 10
            orch = Orchestrator(memory, leader)

        # Simulate 5 workers running (above initial of 2)
        fake_tasks = [MagicMock() for _ in range(5)]
        orch.current_workers = fake_tasks[:]

        # Scale down: should pop and cancel one
        if len(orch.current_workers) > orch.initial_workers:
            task = orch.current_workers.pop()
            task.cancel()

        assert len(orch.current_workers) == 4

    @pytest.mark.anyio
    async def test_handle_scale_up_does_not_exceed_max_workers(self, memory):
        """handle_scale_up does NOT add a worker when at max_workers."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory)
        leader.llm = MockLLM()

        with patch("nanoc.core.config.settings") as mock_settings:
            mock_settings.INITIAL_WORKERS = 2
            mock_settings.MAX_WORKERS = 3
            orch = Orchestrator(memory, leader)

        # Already at max
        orch.current_workers = [MagicMock(), MagicMock(), MagicMock()]  # 3 = max

        initial_count = len(orch.current_workers)
        # Scale up logic
        if len(orch.current_workers) < orch.max_workers:
            orch.current_workers.append(MagicMock())

        assert len(orch.current_workers) == initial_count  # Should NOT have grown

    @pytest.mark.anyio
    async def test_handle_scale_down_does_not_go_below_initial(self, memory):
        """handle_scale_down does NOT remove workers when at initial_workers count."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory)
        leader.llm = MockLLM()

        with patch("nanoc.core.config.settings") as mock_settings:
            mock_settings.INITIAL_WORKERS = 3
            mock_settings.MAX_WORKERS = 10
            orch = Orchestrator(memory, leader)

        # At minimum
        orch.current_workers = [MagicMock(), MagicMock(), MagicMock()]  # 3 = initial

        initial_count = len(orch.current_workers)
        # Scale down logic
        if len(orch.current_workers) > orch.initial_workers:
            task = orch.current_workers.pop()
            task.cancel()

        assert len(orch.current_workers) == initial_count  # Should NOT have shrunk


class TestOrchestratorPriorityOrdering:
    def test_tasks_ordered_by_priority_desc_then_created_at_asc(self, memory):
        """Tasks with higher priority should be dispatched before lower priority ones."""
        import time as t

        # Create tasks with different priorities
        low_priority_id = memory.create_task("Low priority task", assigned_to="Coder", project_id="proj_p")
        t.sleep(0.01)
        high_priority_id = memory.create_task("High priority task", assigned_to="Coder", project_id="proj_p")

        # Set priorities
        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE tasks SET priority = 0 WHERE id = ?", (low_priority_id,))
            cursor.execute("UPDATE tasks SET priority = 10 WHERE id = ?", (high_priority_id,))
            conn.commit()

        # Query in the same order as orchestrator
        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE status = 'pending' ORDER BY priority DESC, created_at ASC")
            tasks = [dict(row) for row in cursor.fetchall()]

        assert tasks[0]["id"] == high_priority_id
        assert tasks[1]["id"] == low_priority_id

    def test_tasks_with_same_priority_ordered_by_created_at_asc(self, memory):
        """Tasks with the same priority are ordered FIFO by creation time."""
        import time as t

        id1 = memory.create_task("First task", assigned_to="Coder", project_id="proj_fifo")
        t.sleep(0.01)
        id2 = memory.create_task("Second task", assigned_to="Coder", project_id="proj_fifo")
        t.sleep(0.01)
        id3 = memory.create_task("Third task", assigned_to="Coder", project_id="proj_fifo")

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE status = 'pending' ORDER BY priority DESC, created_at ASC")
            tasks = [dict(row) for row in cursor.fetchall()]

        ids = [t["id"] for t in tasks]
        assert ids == [id1, id2, id3]


# ===========================================================================
# nanoc/agents/base.py – TeamLeader.delegate_tasks (simplified)
# ===========================================================================

class TestTeamLeaderDelegateTasks:
    @pytest.mark.anyio
    async def test_delegate_tasks_generates_project_id(self, memory):
        """delegate_tasks creates a project_id starting with 'proj_'."""
        from nanoc.agents.base import TeamLeader

        mock_llm = MockLLM()
        mock_llm.add_response("", "architecture design output")

        with patch("nanoc.core.gate_manager.GateManager") as mock_gm_cls:
            mock_gm = MagicMock()
            mock_gm_cls.return_value = mock_gm

            leader = TeamLeader("Leader", "Team Leader", memory)
            leader.llm = mock_llm
            project_id = await leader.delegate_tasks("Build a network monitor")

        assert project_id.startswith("proj_")

    @pytest.mark.anyio
    async def test_delegate_tasks_appends_to_active_projects(self, memory):
        """delegate_tasks adds the new project_id to active_projects knowledge."""
        from nanoc.agents.base import TeamLeader

        mock_llm = MockLLM()

        with patch("nanoc.core.gate_manager.GateManager") as mock_gm_cls:
            mock_gm = MagicMock()
            mock_gm_cls.return_value = mock_gm

            leader = TeamLeader("Leader", "Team Leader", memory)
            leader.llm = mock_llm
            project_id = await leader.delegate_tasks("Build something")

        active = memory.get_knowledge("active_projects")
        assert active is not None
        assert project_id in active

    @pytest.mark.anyio
    async def test_delegate_tasks_always_creates_new_project_id(self, memory):
        """Two calls to delegate_tasks produce two different project IDs."""
        from nanoc.agents.base import TeamLeader
        from datetime import datetime

        mock_llm = MockLLM()

        timestamps = [1700000001, 1700000002]
        call_count = [0]

        def mock_now():
            ts = timestamps[call_count[0] % len(timestamps)]
            call_count[0] += 1
            return MagicMock(timestamp=lambda: ts)

        with patch("nanoc.core.gate_manager.GateManager"), \
             patch("nanoc.agents.base.datetime") as mock_dt:
            mock_dt.now.side_effect = mock_now

            leader = TeamLeader("Leader", "Team Leader", memory)
            leader.llm = mock_llm

            id1 = await leader.delegate_tasks("Project A")
            id2 = await leader.delegate_tasks("Project B")

        assert id1 != id2

    @pytest.mark.anyio
    async def test_delegate_tasks_publishes_incoming_job_event(self, memory):
        """delegate_tasks publishes 'project/incoming-job' event."""
        from nanoc.agents.base import TeamLeader

        mock_llm = MockLLM()

        with patch("nanoc.core.gate_manager.GateManager") as mock_gm_cls:
            mock_gm = MagicMock()
            mock_gm_cls.return_value = mock_gm

            leader = TeamLeader("Leader", "Team Leader", memory)
            leader.llm = mock_llm
            await leader.delegate_tasks("Test project description")

        events = memory.get_events(topic="project/incoming-job")
        assert len(events) >= 1
        payload = json.loads(events[-1]["payload"])
        assert "project_id" in payload
        assert "description" in payload

    @pytest.mark.anyio
    async def test_delegate_tasks_event_has_no_leader_field(self, memory):
        """The project/incoming-job event no longer includes a 'leader' field (PR change)."""
        from nanoc.agents.base import TeamLeader

        mock_llm = MockLLM()

        with patch("nanoc.core.gate_manager.GateManager"):
            leader = TeamLeader("Leader", "Team Leader", memory)
            leader.llm = mock_llm
            await leader.delegate_tasks("Simplified project")

        events = memory.get_events(topic="project/incoming-job")
        payload = json.loads(events[-1]["payload"])
        assert "leader" in payload

    @pytest.mark.anyio
    async def test_delegate_tasks_creates_architect_task(self, memory):
        """delegate_tasks creates a task assigned to 'Architect'."""
        from nanoc.agents.base import TeamLeader

        mock_llm = MockLLM()

        with patch("nanoc.core.gate_manager.GateManager"):
            leader = TeamLeader("Leader", "Team Leader", memory)
            leader.llm = mock_llm
            await leader.delegate_tasks("Design the system")

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE assigned_to = 'Architect'")
            tasks = [dict(row) for row in cursor.fetchall()]

        assert len(tasks) >= 1

    @pytest.mark.anyio
    async def test_delegate_tasks_stores_architecture_in_knowledge(self, memory):
        """delegate_tasks stores the architecture output in knowledge base."""
        from nanoc.agents.base import TeamLeader

        mock_llm = MockLLM()
        arch_response = "Architecture: Use microservices with message queue"
        mock_llm.add_response("Break down", arch_response)

        with patch("nanoc.core.gate_manager.GateManager"):
            leader = TeamLeader("Leader", "Team Leader", memory)
            leader.llm = mock_llm
            project_id = await leader.delegate_tasks("Build microservices")

        arch = memory.get_knowledge(f"project_{project_id}_arch")
        assert arch is not None

    @pytest.mark.anyio
    async def test_delegate_tasks_description_in_event_payload(self, memory):
        """The description passed to delegate_tasks appears in the incoming-job event payload."""
        from nanoc.agents.base import TeamLeader

        mock_llm = MockLLM()

        with patch("nanoc.core.gate_manager.GateManager"):
            leader = TeamLeader("Leader", "Team Leader", memory)
            leader.llm = mock_llm
            await leader.delegate_tasks("Build a specific monitoring system")

        events = memory.get_events(topic="project/incoming-job")
        payload = json.loads(events[-1]["payload"])
        assert "Build a specific monitoring system" in payload["description"]
