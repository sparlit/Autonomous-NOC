"""
Tests for changes introduced in the bulk_improve PR:
  - bulk_improve.py                (new root-level script: task file injection)
  - nanoc/agents/base.py           (handle_task methods removed; project_id no hex suffix)
  - nanoc/agents/security.py       (simplified event payload: no findings/vulnerabilities)
  - nanoc/core/llm.py              (retry loop removed; single attempt only)
  - nanoc/core/orchestrator.py     (explicit role-based dispatch; Reviewer fix-task in orchestrator)
  - nanoc/memory/memory.py         (create_task: priority parameter removed)
"""
import asyncio
import json
import os
import sqlite3
import tempfile
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanoc.memory.memory import Memory
from nanoc.tests.mocks import MockLLM


# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------

def _fresh_memory(db_path: str) -> Memory:
    if os.path.exists(db_path):
        os.remove(db_path)
    return Memory(db_path)


@pytest.fixture
def memory(tmp_path):
    db_path = str(tmp_path / "test_bulk_pr.db")
    mem = Memory(db_path)
    yield mem
    if os.path.exists(db_path):
        os.remove(db_path)


# ===========================================================================
# bulk_improve.py – main()
# ===========================================================================

class TestBulkImproveMain:
    def test_creates_inbox_directory(self, tmp_path):
        """main() creates the nanoc/inbox directory if it does not exist."""
        import bulk_improve

        inbox_dir = str(tmp_path / "inbox")
        with patch.object(bulk_improve, "time") as mock_time, \
             patch("builtins.open", MagicMock()), \
             patch("bulk_improve.os.makedirs") as mock_makedirs, \
             patch("bulk_improve.os.path.join", side_effect=lambda *a: os.path.join(*a)):
            mock_time.time.return_value = 1700000000
            mock_time.sleep = MagicMock()
            bulk_improve.main()

        mock_makedirs.assert_called_once_with("nanoc/inbox", exist_ok=True)

    def test_creates_exactly_100_files(self, tmp_path):
        """main() creates exactly 100 task files."""
        import bulk_improve

        created_files = []

        real_open = open

        def fake_open(path, mode="r"):
            if mode == "w":
                created_files.append(path)
                return MagicMock().__enter__.return_value
            return real_open(path, mode)

        with patch("bulk_improve.os.makedirs"), \
             patch("bulk_improve.time") as mock_time, \
             patch("builtins.open") as mock_open:
            mock_time.time.return_value = 1700000000
            mock_time.sleep = MagicMock()
            # Track each open call
            mock_open.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_open.return_value.__exit__ = MagicMock(return_value=False)
            bulk_improve.main()

        assert mock_open.call_count == 100

    def test_filenames_contain_bulk_task_prefix(self, tmp_path):
        """main() names files with 'bulk_task_' prefix."""
        import bulk_improve

        filenames = []

        class FakeFile:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def write(self, content): pass

        def fake_open(path, mode="r"):
            if mode == "w":
                filenames.append(path)
            return FakeFile()

        with patch("bulk_improve.os.makedirs"), \
             patch("bulk_improve.time") as mock_time, \
             patch("builtins.open", side_effect=fake_open):
            mock_time.time.return_value = 1700000001
            mock_time.sleep = MagicMock()
            bulk_improve.main()

        assert len(filenames) == 100
        for fname in filenames:
            assert "bulk_task_" in fname

    def test_filenames_include_index_suffix(self, tmp_path):
        """main() includes the loop index in each filename for uniqueness."""
        import bulk_improve

        filenames = []

        class FakeFile:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def write(self, content): pass

        def fake_open(path, mode="r"):
            if mode == "w":
                filenames.append(path)
            return FakeFile()

        with patch("bulk_improve.os.makedirs"), \
             patch("bulk_improve.time") as mock_time, \
             patch("builtins.open", side_effect=fake_open):
            mock_time.time.return_value = 1700000001
            mock_time.sleep = MagicMock()
            bulk_improve.main()

        # Check that index 0 and 99 appear in filenames
        assert any("_0.txt" in f for f in filenames)
        assert any("_99.txt" in f for f in filenames)

    def test_files_written_to_inbox_subdir(self, tmp_path):
        """main() places files inside nanoc/inbox directory."""
        import bulk_improve

        filenames = []

        class FakeFile:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def write(self, content): pass

        def fake_open(path, mode="r"):
            if mode == "w":
                filenames.append(path)
            return FakeFile()

        with patch("bulk_improve.os.makedirs"), \
             patch("bulk_improve.time") as mock_time, \
             patch("builtins.open", side_effect=fake_open):
            mock_time.time.return_value = 1700000001
            mock_time.sleep = MagicMock()
            bulk_improve.main()

        for fname in filenames:
            assert fname.startswith("nanoc/inbox/")

    def test_file_content_contains_project_name(self, tmp_path):
        """Each task file contains the expected project description."""
        import bulk_improve

        written_contents = []

        class FakeFile:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def write(self, content):
                written_contents.append(content)

        def fake_open(path, mode="r"):
            return FakeFile()

        with patch("bulk_improve.os.makedirs"), \
             patch("bulk_improve.time") as mock_time, \
             patch("builtins.open", side_effect=fake_open):
            mock_time.time.return_value = 1700000001
            mock_time.sleep = MagicMock()
            bulk_improve.main()

        assert len(written_contents) == 100
        for content in written_contents:
            assert "Autonomous Network Operating Center" in content

    def test_file_content_mentions_foss(self, tmp_path):
        """Each task file mentions FOSS principle."""
        import bulk_improve

        written_contents = []

        class FakeFile:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def write(self, content):
                written_contents.append(content)

        def fake_open(path, mode="r"):
            return FakeFile()

        with patch("bulk_improve.os.makedirs"), \
             patch("bulk_improve.time") as mock_time, \
             patch("builtins.open", side_effect=fake_open):
            mock_time.time.return_value = 1700000001
            mock_time.sleep = MagicMock()
            bulk_improve.main()

        for content in written_contents:
            assert "FOSS" in content

    def test_sleeps_between_file_writes(self, tmp_path):
        """main() calls time.sleep() between file writes to ensure unique timestamps."""
        import bulk_improve

        class FakeFile:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def write(self, content): pass

        with patch("bulk_improve.os.makedirs"), \
             patch("bulk_improve.time") as mock_time, \
             patch("builtins.open", return_value=FakeFile()):
            mock_time.time.return_value = 1700000001
            mock_time.sleep = MagicMock()
            bulk_improve.main()

        # Should sleep 100 times (once per iteration)
        assert mock_time.sleep.call_count == 100
        # Each sleep should be 0.01 seconds
        for call in mock_time.sleep.call_args_list:
            assert call[0][0] == 0.01

    def test_main_integration_creates_real_files(self, tmp_path, monkeypatch):
        """Integration: main() creates 100 actual files in the inbox directory."""
        import bulk_improve

        inbox_dir = tmp_path / "inbox"
        monkeypatch.chdir(tmp_path)
        os.makedirs(str(tmp_path / "nanoc"), exist_ok=True)

        # Run with real file I/O but patched sleep
        with patch("bulk_improve.time") as mock_time:
            mock_time.time.side_effect = lambda: time.time()
            mock_time.sleep = MagicMock()
            bulk_improve.main()

        created = list((tmp_path / "nanoc" / "inbox").glob("bulk_task_*.txt"))
        assert len(created) == 100


# ===========================================================================
# nanoc/agents/base.py – handle_task methods removed
# ===========================================================================

class TestHandleTaskRemoved:
    def test_architect_does_not_have_handle_task(self, memory):
        """Architect no longer has a handle_task method after PR change."""
        from nanoc.agents.base import Architect

        agent = Architect("Arch1", "Architect", memory, MockLLM())
        assert not hasattr(agent, "handle_task")

    def test_planner_does_not_have_handle_task(self, memory):
        """Planner no longer has a handle_task method after PR change."""
        from nanoc.agents.base import Planner

        agent = Planner("Planner1", "Planner", memory, MockLLM())
        assert not hasattr(agent, "handle_task")

    def test_coder_does_not_have_handle_task(self, memory):
        """Coder no longer has a handle_task method after PR change."""
        from nanoc.agents.base import Coder

        agent = Coder("Coder1", "Coder", memory, MockLLM())
        assert not hasattr(agent, "handle_task")

    def test_reviewer_does_not_have_handle_task(self, memory):
        """Reviewer no longer has a handle_task method after PR change."""
        from nanoc.agents.base import Reviewer

        agent = Reviewer("Rev1", "Reviewer", memory, MockLLM())
        assert not hasattr(agent, "handle_task")

    def test_base_agent_does_not_have_handle_task(self, memory):
        """BaseAgent no longer has a handle_task method after PR change."""
        from nanoc.agents.base import BaseAgent

        agent = BaseAgent("Base1", "Base", memory, MockLLM())
        assert not hasattr(agent, "handle_task")

    def test_architect_still_has_design_solution(self, memory):
        """Architect still has design_solution method (not removed, only handle_task was)."""
        from nanoc.agents.base import Architect

        agent = Architect("Arch1", "Architect", memory, MockLLM())
        assert hasattr(agent, "design_solution")
        assert callable(agent.design_solution)

    def test_planner_still_has_create_todo_list(self, memory):
        """Planner still has create_todo_list method."""
        from nanoc.agents.base import Planner

        agent = Planner("P1", "Planner", memory, MockLLM())
        assert hasattr(agent, "create_todo_list")

    def test_coder_still_has_write_code(self, memory):
        """Coder still has write_code method."""
        from nanoc.agents.base import Coder

        agent = Coder("C1", "Coder", memory, MockLLM())
        assert hasattr(agent, "write_code")

    def test_reviewer_still_has_review_work(self, memory):
        """Reviewer still has review_work method."""
        from nanoc.agents.base import Reviewer

        agent = Reviewer("R1", "Reviewer", memory, MockLLM())
        assert hasattr(agent, "review_work")


# ===========================================================================
# nanoc/agents/base.py – TeamLeader project_id format (no random hex)
# ===========================================================================

class TestTeamLeaderProjectIdFormat:
    @pytest.mark.anyio
    async def test_project_id_has_no_hex_component(self, memory):
        """project_id is now 'proj_{timestamp}' without a random hex suffix."""
        from nanoc.agents.base import TeamLeader

        with patch("nanoc.core.gate_manager.GateManager"):
            leader = TeamLeader("L1", "Team Leader", memory, MockLLM())
            project_id = await leader.delegate_tasks("Build something")

        # Should be exactly 'proj_' followed by digits only
        suffix = project_id[len("proj_"):]
        assert suffix.isdigit(), f"Expected only digits after 'proj_', got: '{suffix}'"

    @pytest.mark.anyio
    async def test_project_id_no_underscore_after_timestamp(self, memory):
        """project_id does not contain an additional underscore-hex portion."""
        from nanoc.agents.base import TeamLeader

        with patch("nanoc.core.gate_manager.GateManager"):
            leader = TeamLeader("L1", "Team Leader", memory, MockLLM())
            project_id = await leader.delegate_tasks("Build something")

        # Old format was proj_{timestamp}_{hex8chars}; new format should have only one underscore
        # after 'proj' prefix
        parts = project_id.split("_")
        # parts[0] = "proj", parts[1] = timestamp digits
        assert len(parts) == 2, f"Expected 2 parts split by '_', got {parts}"

    @pytest.mark.anyio
    async def test_project_id_starts_with_proj_(self, memory):
        """project_id still starts with 'proj_'."""
        from nanoc.agents.base import TeamLeader

        with patch("nanoc.core.gate_manager.GateManager"):
            leader = TeamLeader("L1", "Team Leader", memory, MockLLM())
            project_id = await leader.delegate_tasks("Test project")

        assert project_id.startswith("proj_")


# ===========================================================================
# nanoc/memory/memory.py – create_task: priority parameter removed
# ===========================================================================

class TestMemoryCreateTaskNoPriority:
    def test_create_task_does_not_accept_priority_kwarg(self, memory):
        """create_task raises TypeError if priority is passed as a keyword argument."""
        import inspect

        sig = inspect.signature(memory.create_task)
        assert "priority" not in sig.parameters

    def test_create_task_returns_int_task_id(self, memory):
        """create_task still returns an integer task ID."""
        task_id = memory.create_task("Do something", assigned_to="Coder")
        assert isinstance(task_id, int)
        assert task_id > 0

    def test_create_task_stores_description(self, memory):
        """create_task stores the description correctly without priority."""
        task_id = memory.create_task("Critical task description", assigned_to="Architect")

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            row = dict(cursor.fetchone())

        assert row["description"] == "Critical task description"
        assert row["assigned_to"] == "Architect"
        assert row["status"] == "pending"

    def test_create_task_priority_defaults_to_zero(self, memory):
        """Without explicit priority, the DB default value of 0 is used."""
        task_id = memory.create_task("Default priority task", assigned_to="Coder")

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT priority FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()

        assert row["priority"] == 0

    def test_create_task_raises_on_priority_kwarg(self, memory):
        """Passing priority= keyword arg to create_task raises TypeError."""
        with pytest.raises(TypeError):
            memory.create_task("Task with priority", priority=10)

    def test_create_task_stores_project_id(self, memory):
        """create_task correctly stores project_id."""
        task_id = memory.create_task("Task for project", project_id="proj_123")

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT project_id FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()

        assert row["project_id"] == "proj_123"

    def test_create_multiple_tasks_returns_unique_ids(self, memory):
        """Multiple create_task calls return different IDs."""
        id1 = memory.create_task("Task 1")
        id2 = memory.create_task("Task 2")
        assert id1 != id2


# ===========================================================================
# nanoc/agents/security.py – simplified event payload (no findings/vulnerabilities)
# ===========================================================================

class TestSecurityAgentSimplifiedEvent:
    @pytest.mark.anyio
    async def test_event_payload_has_no_findings_key(self, memory):
        """security/audit-complete event no longer includes 'findings' key."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("Sec1", memory)
        agent.llm = MockLLM()

        nmap_result = {
            "stdout": "22/tcp open ssh Telnet service detected",
            "stderr": "",
            "returncode": 0
        }

        with patch("nanoc.tools.network.AsyncRunner") as mock_runner_cls:
            mock_runner_cls.run_command = AsyncMock(return_value=nmap_result)
            await agent.audit_service("10.0.0.1")

        events = memory.get_events(topic="security/audit-complete")
        assert len(events) >= 1
        payload = json.loads(events[-1]["payload"])
        assert "findings" not in payload

    @pytest.mark.anyio
    async def test_event_payload_has_no_vulnerabilities_key(self, memory):
        """security/audit-complete event no longer includes 'vulnerabilities' key."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("Sec1", memory)
        agent.llm = MockLLM()

        nmap_result = {
            "stdout": "Telnet open ftp anonymous ssl expired protocol 1.0",
            "stderr": "",
            "returncode": 0
        }

        with patch("nanoc.tools.network.AsyncRunner") as mock_runner_cls:
            mock_runner_cls.run_command = AsyncMock(return_value=nmap_result)
            await agent.audit_service("192.168.1.100")

        events = memory.get_events(topic="security/audit-complete")
        assert len(events) >= 1
        payload = json.loads(events[-1]["payload"])
        assert "vulnerabilities" not in payload

    @pytest.mark.anyio
    async def test_event_payload_contains_only_target_and_report(self, memory):
        """security/audit-complete event payload contains exactly 'target' and 'report' keys."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("Sec1", memory)
        agent.llm = MockLLM()

        nmap_result = {"stdout": "scan result data", "stderr": "", "returncode": 0}

        with patch("nanoc.tools.network.AsyncRunner") as mock_runner_cls:
            mock_runner_cls.run_command = AsyncMock(return_value=nmap_result)
            await agent.audit_service("172.16.0.1")

        events = memory.get_events(topic="security/audit-complete")
        payload = json.loads(events[-1]["payload"])
        assert set(payload.keys()) == {"target", "report"}

    @pytest.mark.anyio
    async def test_telnet_in_report_does_not_add_findings(self, memory):
        """Telnet in report no longer triggers vulnerability findings in the event."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("Sec1", memory)
        agent.llm = MockLLM()

        nmap_result = {
            "stdout": "23/tcp open telnet",
            "stderr": "",
            "returncode": 0
        }

        with patch("nanoc.tools.network.AsyncRunner") as mock_runner_cls:
            mock_runner_cls.run_command = AsyncMock(return_value=nmap_result)
            await agent.audit_service("10.0.0.5")

        events = memory.get_events(topic="security/audit-complete")
        payload = json.loads(events[-1]["payload"])
        # No vulnerability analysis happens
        assert "findings" not in payload
        assert "vulnerabilities" not in payload

    @pytest.mark.anyio
    async def test_error_result_has_no_event_with_findings(self, memory):
        """When nmap fails, no event is published (so no findings key anywhere)."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("Sec1", memory)
        agent.llm = MockLLM()

        error_result = {"error": "nmap: command not found"}

        with patch("nanoc.tools.network.AsyncRunner") as mock_runner_cls:
            mock_runner_cls.run_command = AsyncMock(return_value=error_result)
            await agent.audit_service("10.0.0.1")

        events = memory.get_events(topic="security/audit-complete")
        assert len(events) == 0


# ===========================================================================
# nanoc/core/llm.py – retry logic removed (single attempt)
# ===========================================================================

class TestLLMNoRetry:
    @pytest.mark.anyio
    async def test_http_error_is_not_retried(self, memory):
        """LLMProvider does not retry on httpx.HTTPStatusError; raises immediately."""
        import httpx
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="openrouter", model="test-model")

        http_error = httpx.HTTPStatusError(
            "500 Server Error",
            request=MagicMock(),
            response=MagicMock()
        )

        call_count = 0

        async def always_fails(prompt, system_prompt, model):
            nonlocal call_count
            call_count += 1
            raise http_error

        with patch("nanoc.core.llm.Memory", return_value=memory), \
             patch.object(provider, "_openrouter_complete", side_effect=always_fails), \
             patch.object(provider, "_record_error"), \
             patch("nanoc.core.llm.settings") as mock_settings:
            mock_settings.DB_PATH = memory.db_path

            with pytest.raises(httpx.HTTPStatusError):
                await provider.complete("test prompt")

        # Should only be called once - no retries
        assert call_count == 1

    @pytest.mark.anyio
    async def test_request_error_is_not_retried(self, memory):
        """LLMProvider does not retry on httpx.RequestError; raises immediately."""
        import httpx
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="openrouter", model="test-model")

        call_count = 0

        async def always_fails(prompt, system_prompt, model):
            nonlocal call_count
            call_count += 1
            raise httpx.RequestError("Connection refused")

        with patch("nanoc.core.llm.Memory", return_value=memory), \
             patch.object(provider, "_openrouter_complete", side_effect=always_fails), \
             patch.object(provider, "_record_error"), \
             patch("nanoc.core.llm.settings") as mock_settings:
            mock_settings.DB_PATH = memory.db_path

            with pytest.raises(httpx.RequestError):
                await provider.complete("test prompt")

        assert call_count == 1

    @pytest.mark.anyio
    async def test_exception_triggers_record_error(self, memory):
        """Any exception during complete() triggers _record_error."""
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="openrouter", model="test-model")

        async def raises(prompt, system_prompt, model):
            raise RuntimeError("Some failure")

        with patch("nanoc.core.llm.Memory", return_value=memory), \
             patch.object(provider, "_openrouter_complete", side_effect=raises), \
             patch.object(provider, "_record_error") as mock_record_error, \
             patch("nanoc.core.llm.settings") as mock_settings:
            mock_settings.DB_PATH = memory.db_path

            with pytest.raises(RuntimeError):
                await provider.complete("prompt")

        mock_record_error.assert_called_once()

    @pytest.mark.anyio
    async def test_exception_is_reraised_after_record_error(self, memory):
        """The original exception is re-raised after _record_error is called."""
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="openrouter", model="test-model")

        class CustomError(Exception):
            pass

        async def raises(prompt, system_prompt, model):
            raise CustomError("custom error details")

        with patch("nanoc.core.llm.Memory", return_value=memory), \
             patch.object(provider, "_openrouter_complete", side_effect=raises), \
             patch.object(provider, "_record_error"), \
             patch("nanoc.core.llm.settings") as mock_settings:
            mock_settings.DB_PATH = memory.db_path

            with pytest.raises(CustomError, match="custom error details"):
                await provider.complete("prompt")

    @pytest.mark.anyio
    async def test_no_sleep_between_attempts(self, memory):
        """No asyncio.sleep is called between attempts (retry logic removed)."""
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="openrouter", model="test-model")

        async def raises(prompt, system_prompt, model):
            raise RuntimeError("fail")

        with patch("nanoc.core.llm.Memory", return_value=memory), \
             patch.object(provider, "_openrouter_complete", side_effect=raises), \
             patch.object(provider, "_record_error"), \
             patch("nanoc.core.llm.asyncio") as mock_asyncio, \
             patch("nanoc.core.llm.settings") as mock_settings:
            mock_settings.DB_PATH = memory.db_path
            mock_asyncio.get_event_loop.return_value = MagicMock()
            mock_asyncio.get_event_loop.return_value.time.return_value = 0.0

            with pytest.raises(RuntimeError):
                await provider.complete("prompt")

        mock_asyncio.sleep.assert_not_called()

    @pytest.mark.anyio
    async def test_successful_call_does_not_record_error(self, memory):
        """A successful complete() call does NOT invoke _record_error."""
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="openrouter", model="test-model")

        async def succeeds(prompt, system_prompt, model):
            return "response text"

        with patch("nanoc.core.llm.Memory", return_value=memory), \
             patch.object(provider, "_openrouter_complete", side_effect=succeeds), \
             patch.object(provider, "_record_telemetry"), \
             patch.object(provider, "_record_error") as mock_record_error, \
             patch("nanoc.core.llm.settings") as mock_settings:
            mock_settings.DB_PATH = memory.db_path
            result = await provider.complete("prompt")

        mock_record_error.assert_not_called()
        assert result == "response text"


# ===========================================================================
# nanoc/core/orchestrator.py – role-based dispatch
# ===========================================================================

class TestOrchestratorRoleDispatch:
    @pytest.mark.anyio
    async def test_architect_role_calls_design_solution(self, memory):
        """process_task calls agent.design_solution() for 'Architect' role."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_agent = MagicMock()
        mock_agent.role = "Architect"
        mock_agent.log = AsyncMock()
        mock_agent.design_solution = AsyncMock(return_value="Architecture design")
        orch.add_agent(mock_agent)

        task_id = memory.create_task(
            "proj_1: Design the system",
            assigned_to="Architect",
            project_id="proj_1"
        )
        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orch.process_task(task)

        mock_agent.design_solution.assert_called_once_with(task["description"])

    @pytest.mark.anyio
    async def test_planner_role_calls_create_todo_list(self, memory):
        """process_task calls agent.create_todo_list() for 'Planner' role."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_agent = MagicMock()
        mock_agent.role = "Planner"
        mock_agent.log = AsyncMock()
        mock_agent.create_todo_list = AsyncMock(return_value="Task list")
        orch.add_agent(mock_agent)

        task_id = memory.create_task(
            "proj_2: Create task list",
            assigned_to="Planner",
            project_id="proj_2"
        )
        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orch.process_task(task)

        mock_agent.create_todo_list.assert_called_once_with(task["description"])

    @pytest.mark.anyio
    async def test_coder_role_calls_write_code(self, memory):
        """process_task calls agent.write_code() for 'Coder' role."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_agent = MagicMock()
        mock_agent.role = "Coder"
        mock_agent.log = AsyncMock()
        mock_agent.write_code = AsyncMock(return_value="def foo(): pass")
        orch.add_agent(mock_agent)

        task_id = memory.create_task(
            "proj_3: Write authentication module",
            assigned_to="Coder",
            project_id="proj_3"
        )
        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orch.process_task(task)

        mock_agent.write_code.assert_called_once_with(task["description"])

    @pytest.mark.anyio
    async def test_reviewer_role_calls_review_work(self, memory):
        """process_task calls agent.review_work() for 'Reviewer' role."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_agent = MagicMock()
        mock_agent.role = "Reviewer"
        mock_agent.log = AsyncMock()
        mock_agent.review_work = AsyncMock(return_value="STATUS: APPROVED - looks good")
        orch.add_agent(mock_agent)

        task_id = memory.create_task(
            "proj_4: Review this code",
            assigned_to="Reviewer",
            project_id="proj_4"
        )
        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orch.process_task(task)

        mock_agent.review_work.assert_called_once_with(task["description"])

    @pytest.mark.anyio
    async def test_unknown_role_calls_think(self, memory):
        """process_task calls agent.think() for unrecognized roles."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_agent = MagicMock()
        mock_agent.role = "CustomRole"
        mock_agent.log = AsyncMock()
        mock_agent.think = AsyncMock(return_value="Thought response")
        orch.add_agent(mock_agent)

        task_id = memory.create_task(
            "Execute something custom",
            assigned_to="CustomRole",
            project_id="proj_5"
        )
        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orch.process_task(task)

        mock_agent.think.assert_called_once()
        call_args = mock_agent.think.call_args[0][0]
        assert "Execute this task:" in call_args

    @pytest.mark.anyio
    async def test_task_marked_completed_on_success(self, memory):
        """process_task sets task status to 'completed' on successful execution."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_agent = MagicMock()
        mock_agent.role = "Coder"
        mock_agent.log = AsyncMock()
        mock_agent.write_code = AsyncMock(return_value="def solution(): return 42")
        orch.add_agent(mock_agent)

        task_id = memory.create_task("Write solution", assigned_to="Coder", project_id="proj_6")
        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orch.process_task(task)

        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()

        assert row[0] == "completed"

    @pytest.mark.anyio
    async def test_architect_method_not_called_for_coder(self, memory):
        """design_solution is NOT called for Coder role tasks."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_agent = MagicMock()
        mock_agent.role = "Coder"
        mock_agent.log = AsyncMock()
        mock_agent.write_code = AsyncMock(return_value="code")
        mock_agent.design_solution = AsyncMock(return_value="design")
        orch.add_agent(mock_agent)

        task_id = memory.create_task("Write code", assigned_to="Coder", project_id="proj_7")
        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orch.process_task(task)

        mock_agent.design_solution.assert_not_called()


# ===========================================================================
# nanoc/core/orchestrator.py – Reviewer fix-task creation (moved to orchestrator)
# ===========================================================================

class TestOrchestratorReviewerFixTask:
    @pytest.mark.anyio
    async def test_reviewer_not_approved_creates_fix_task(self, memory):
        """process_task creates a fix task when Reviewer returns non-APPROVED result."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_agent = MagicMock()
        mock_agent.role = "Reviewer"
        mock_agent.log = AsyncMock()
        mock_agent.review_work = AsyncMock(return_value="STATUS: FAILED - missing error handling")
        orch.add_agent(mock_agent)

        task_id = memory.create_task(
            "Review authentication code",
            assigned_to="Reviewer",
            project_id="proj_r1"
        )
        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orch.process_task(task)

        # Verify a fix task was created assigned to Coder
        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM tasks WHERE assigned_to = 'Coder' AND description LIKE '%Fix flaws%'"
            )
            fix_tasks = [dict(row) for row in cursor.fetchall()]

        assert len(fix_tasks) >= 1

    @pytest.mark.anyio
    async def test_reviewer_approved_does_not_create_fix_task(self, memory):
        """process_task does NOT create a fix task when Reviewer returns APPROVED result."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_agent = MagicMock()
        mock_agent.role = "Reviewer"
        mock_agent.log = AsyncMock()
        mock_agent.review_work = AsyncMock(return_value="STATUS: APPROVED - all looks good")
        orch.add_agent(mock_agent)

        task_id = memory.create_task(
            "Review good code",
            assigned_to="Reviewer",
            project_id="proj_r2"
        )
        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM tasks")
            count_before = cursor.fetchone()[0]

        await orch.process_task(task)

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM tasks")
            count_after = cursor.fetchone()[0]

        # No new fix tasks should be created for APPROVED
        assert count_after == count_before

    @pytest.mark.anyio
    async def test_reviewer_fix_task_assigned_to_coder(self, memory):
        """The fix task created by orchestrator Reviewer logic is assigned to 'Coder'."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_agent = MagicMock()
        mock_agent.role = "Reviewer"
        mock_agent.log = AsyncMock()
        mock_agent.review_work = AsyncMock(return_value="STATUS: FAILED - needs tests")
        orch.add_agent(mock_agent)

        task_id = memory.create_task(
            "Review code",
            assigned_to="Reviewer",
            project_id="proj_r3"
        )
        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orch.process_task(task)

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT assigned_to FROM tasks WHERE description LIKE '%Fix flaws%'"
            )
            fix_task = cursor.fetchone()

        assert fix_task is not None
        assert fix_task["assigned_to"] == "Coder"

    @pytest.mark.anyio
    async def test_reviewer_fix_task_references_original_task(self, memory):
        """The fix task description references the original task description."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_agent = MagicMock()
        mock_agent.role = "Reviewer"
        mock_agent.log = AsyncMock()
        mock_agent.review_work = AsyncMock(return_value="STATUS: FAILED - incomplete")
        orch.add_agent(mock_agent)

        task_id = memory.create_task(
            "Review payment processor code",
            assigned_to="Reviewer",
            project_id="proj_r4"
        )
        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orch.process_task(task)

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT description FROM tasks WHERE description LIKE '%Fix flaws%'"
            )
            fix_task = cursor.fetchone()

        assert fix_task is not None
        assert "Review payment processor code" in fix_task["description"]

    @pytest.mark.anyio
    async def test_reviewer_fix_task_preserves_project_id(self, memory):
        """The fix task created by orchestrator uses the same project_id."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_agent = MagicMock()
        mock_agent.role = "Reviewer"
        mock_agent.log = AsyncMock()
        mock_agent.review_work = AsyncMock(return_value="STATUS: FAILED - too slow")
        orch.add_agent(mock_agent)

        task_id = memory.create_task(
            "Review performance code",
            assigned_to="Reviewer",
            project_id="proj_special_123"
        )
        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orch.process_task(task)

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT project_id FROM tasks WHERE description LIKE '%Fix flaws%'"
            )
            fix_task = cursor.fetchone()

        assert fix_task is not None
        assert fix_task["project_id"] == "proj_special_123"

    @pytest.mark.anyio
    async def test_reviewer_with_approved_in_result_no_fix_task_for_coder(self, memory):
        """Even partial APPROVED match avoids fix task creation."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_agent = MagicMock()
        mock_agent.role = "Reviewer"
        mock_agent.log = AsyncMock()
        # Contains "APPROVED" anywhere in the string
        mock_agent.review_work = AsyncMock(
            return_value="Minor suggestions but STATUS: APPROVED overall"
        )
        orch.add_agent(mock_agent)

        task_id = memory.create_task(
            "Review logging code",
            assigned_to="Reviewer",
            project_id="proj_r5"
        )
        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orch.process_task(task)

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM tasks WHERE assigned_to = 'Coder' AND description LIKE '%Fix flaws%'"
            )
            fix_tasks = [dict(row) for row in cursor.fetchall()]

        assert len(fix_tasks) == 0


# ===========================================================================
# nanoc/core/orchestrator.py – retry boundary conditions
# ===========================================================================

class TestOrchestratorRetryBoundary:
    @pytest.mark.anyio
    async def test_retry_count_equals_max_retries_means_failed(self, memory):
        """When retry_count == max_retries, the task is set to 'failed' (not 'pending')."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_agent = MagicMock()
        mock_agent.role = "Coder"
        mock_agent.log = AsyncMock()
        mock_agent.write_code = AsyncMock(side_effect=RuntimeError("Boundary failure"))
        orch.add_agent(mock_agent)

        task_id = memory.create_task("Boundary test task", assigned_to="Coder", project_id="proj_b1")

        # Set retry_count to exactly max_retries (both = 3)
        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE tasks SET retry_count = 3, max_retries = 3 WHERE id = ?", (task_id,))
            conn.commit()

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orch.process_task(task)

        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()

        assert row[0] == "failed"

    @pytest.mark.anyio
    async def test_retry_count_one_less_than_max_means_pending(self, memory):
        """When retry_count + 1 == max_retries, the new count is still <= max so status is 'pending'."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_agent = MagicMock()
        mock_agent.role = "Coder"
        mock_agent.log = AsyncMock()
        mock_agent.write_code = AsyncMock(side_effect=RuntimeError("Transient"))
        orch.add_agent(mock_agent)

        task_id = memory.create_task("Near max retries task", assigned_to="Coder", project_id="proj_b2")

        # retry_count = 2, max_retries = 3 → after failure retry_count = 3 ≤ 3 → pending
        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE tasks SET retry_count = 2, max_retries = 3 WHERE id = ?", (task_id,))
            conn.commit()

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orch.process_task(task)

        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status, retry_count FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()

        assert row[0] == "pending"
        assert row[1] == 3


# ===========================================================================
# nanoc/memory/memory.py – schema: no migration code (priority always DEFAULT 0)
# ===========================================================================

class TestMemorySchemaNoMigration:
    def test_schema_has_priority_column_via_default(self, tmp_path):
        """Tasks table includes priority column with DEFAULT 0 from CREATE TABLE."""
        db_path = str(tmp_path / "schema_test.db")
        mem = Memory(db_path)

        task_id = mem.create_task("Test task")

        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(tasks)")
            columns = {row[1]: row for row in cursor.fetchall()}

        assert "priority" in columns
        # Default value should be 0
        assert columns["priority"][4] == "0"

    def test_creating_memory_twice_does_not_raise(self, tmp_path):
        """Creating Memory on an existing DB does not raise (no duplicate migration)."""
        db_path = str(tmp_path / "migration_test.db")
        # Create once
        mem1 = Memory(db_path)
        mem1.create_task("First task")
        # Create again (simulates restart) - should not raise from migration code
        mem2 = Memory(db_path)
        task_id = mem2.create_task("Second task")
        assert task_id is not None
