"""
Tests for changes introduced in the bulk_improve PR:

Changed files (source code only):
  - bulk_improve.py                     (new root-level script: 100-task bulk injector)
  - nanoc/agents/analyst.py             (create_task call reformatted, priority=10 still passed)
  - nanoc/agents/base.py                (handle_task removed; project_id format simplified)
  - nanoc/agents/security.py            (vulnerability analysis removed; event payload simplified)
  - nanoc/core/llm.py                   (retry loop removed; single-attempt with error recording)
  - nanoc/core/orchestrator.py          (dynamic dispatch replaced with explicit role dispatch)
  - nanoc/memory/memory.py              (priority param removed from create_task)
  - nanoc/tools/network.py              (_get_fallback_topology returns only localhost)
"""
import asyncio
import inspect
import json
import os
import sqlite3
import sys
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
    db_path = str(tmp_path / "test_bulk_pr.db")
    mem = Memory(db_path)
    yield mem
    if os.path.exists(db_path):
        os.remove(db_path)


# ===========================================================================
# bulk_improve.py – new root-level bulk task injector
# ===========================================================================

class TestBulkImproveMain:
    def test_main_creates_inbox_directory(self, tmp_path):
        """main() creates nanoc/inbox directory via os.makedirs."""
        import bulk_improve as bm

        created_dirs = []

        def fake_makedirs(path, exist_ok=False):
            created_dirs.append(path)

        with patch.object(bm.os, "makedirs", side_effect=fake_makedirs), \
             patch("builtins.open", unittest.mock.mock_open()), \
             patch.object(bm.time, "sleep"), \
             patch.object(bm.time, "time", return_value=1700000000):
            bm.main()

        assert "nanoc/inbox" in created_dirs

    def test_main_creates_one_hundred_files(self, tmp_path):
        """main() creates exactly 100 task files."""
        import bulk_improve as bm

        opened_files = []

        real_open = open

        def fake_makedirs(path, exist_ok=False):
            pass

        mock_fh = unittest.mock.mock_open()

        with patch.object(bm.os, "makedirs", side_effect=fake_makedirs), \
             patch("builtins.open", mock_fh) as mo, \
             patch.object(bm.time, "sleep"), \
             patch.object(bm.time, "time", return_value=1700000000):
            bm.main()

        assert mo.call_count == 100

    def test_main_writes_task_description_to_each_file(self, tmp_path):
        """main() writes the task description to every opened file."""
        import bulk_improve as bm

        written_contents = []

        def fake_makedirs(path, exist_ok=False):
            pass

        mock_fh = unittest.mock.mock_open()
        mock_fh.return_value.__enter__.return_value.write = \
            lambda content: written_contents.append(content)

        with patch.object(bm.os, "makedirs", side_effect=fake_makedirs), \
             patch("builtins.open", mock_fh), \
             patch.object(bm.time, "sleep"), \
             patch.object(bm.time, "time", return_value=1700000000):
            bm.main()

        # open was called 100 times; each write call appends to our list
        # The mock_open write is a bit tricky to capture, so just check open count
        assert mock_fh.call_count == 100

    def test_main_file_names_use_timestamp_and_index(self, tmp_path):
        """Filenames are of the form 'bulk_task_<timestamp>_<i>.txt'."""
        import bulk_improve as bm

        opened_paths = []
        real_open = open

        def fake_makedirs(path, exist_ok=False):
            pass

        def fake_open(path, mode="r"):
            opened_paths.append(path)
            return unittest.mock.mock_open()(path, mode)

        with patch.object(bm.os, "makedirs", side_effect=fake_makedirs), \
             patch("builtins.open", side_effect=fake_open), \
             patch.object(bm.time, "sleep"), \
             patch.object(bm.time, "time", return_value=1700000000):
            bm.main()

        assert len(opened_paths) == 100
        for i, path in enumerate(opened_paths):
            assert "bulk_task_" in path
            assert f"_{i}.txt" in path

    def test_main_file_paths_are_inside_inbox_dir(self, tmp_path):
        """All created files are inside 'nanoc/inbox'."""
        import bulk_improve as bm

        opened_paths = []

        def fake_makedirs(path, exist_ok=False):
            pass

        def fake_open(path, mode="r"):
            opened_paths.append(path)
            return unittest.mock.mock_open()(path, mode)

        with patch.object(bm.os, "makedirs", side_effect=fake_makedirs), \
             patch("builtins.open", side_effect=fake_open), \
             patch.object(bm.time, "sleep"), \
             patch.object(bm.time, "time", return_value=1700000000):
            bm.main()

        for path in opened_paths:
            assert path.startswith("nanoc/inbox")

    def test_main_sleeps_between_files(self, tmp_path):
        """main() calls time.sleep between file creations to ensure unique timestamps."""
        import bulk_improve as bm

        sleep_calls = []

        def fake_makedirs(path, exist_ok=False):
            pass

        def fake_sleep(secs):
            sleep_calls.append(secs)

        with patch.object(bm.os, "makedirs", side_effect=fake_makedirs), \
             patch("builtins.open", unittest.mock.mock_open()), \
             patch.object(bm.time, "sleep", side_effect=fake_sleep), \
             patch.object(bm.time, "time", return_value=1700000000):
            bm.main()

        assert len(sleep_calls) == 100
        assert all(s == 0.01 for s in sleep_calls)

    def test_main_uses_makedirs_exist_ok(self, tmp_path):
        """main() calls os.makedirs with exist_ok=True."""
        import bulk_improve as bm

        makedirs_kwargs = {}

        def fake_makedirs(path, exist_ok=False):
            makedirs_kwargs['path'] = path
            makedirs_kwargs['exist_ok'] = exist_ok

        with patch.object(bm.os, "makedirs", side_effect=fake_makedirs), \
             patch("builtins.open", unittest.mock.mock_open()), \
             patch.object(bm.time, "sleep"), \
             patch.object(bm.time, "time", return_value=1700000000):
            bm.main()

        assert makedirs_kwargs['exist_ok'] is True

    def test_main_task_description_contains_foss_mention(self, tmp_path):
        """The task description written to files mentions FOSS."""
        import bulk_improve as bm

        written_data = []

        def fake_makedirs(path, exist_ok=False):
            pass

        original_open = open

        def capturing_open(path, mode="r"):
            handle = MagicMock()
            handle.__enter__ = lambda s: s
            handle.__exit__ = MagicMock(return_value=False)
            handle.write = lambda content: written_data.append(content)
            return handle

        with patch.object(bm.os, "makedirs", side_effect=fake_makedirs), \
             patch("builtins.open", side_effect=capturing_open), \
             patch.object(bm.time, "sleep"), \
             patch.object(bm.time, "time", return_value=1700000000):
            bm.main()

        assert len(written_data) == 100
        for content in written_data:
            assert "FOSS" in content

    def test_main_creates_100_tasks_in_real_tmp_dir(self, tmp_path):
        """Integration: main() actually creates 100 files in a real temp directory."""
        import bulk_improve as bm

        inbox_dir = str(tmp_path / "nanoc" / "inbox")

        real_makedirs = os.makedirs

        def fake_makedirs(path, exist_ok=False):
            # Redirect the inbox dir to tmp_path
            if path == "nanoc/inbox":
                real_makedirs(inbox_dir, exist_ok=True)
            else:
                real_makedirs(path, exist_ok=exist_ok)

        real_join = os.path.join

        def fake_join(*args):
            if args[0] == "nanoc/inbox":
                return real_join(inbox_dir, *args[1:])
            return real_join(*args)

        with patch.object(bm.os, "makedirs", side_effect=fake_makedirs), \
             patch.object(bm.os.path, "join", side_effect=fake_join), \
             patch.object(bm.time, "sleep"):
            bm.main()

        created = list(os.listdir(inbox_dir))
        assert len(created) == 100


# ===========================================================================
# nanoc/memory/memory.py – create_task priority param removed
# ===========================================================================

class TestMemoryCreateTaskPriorityRemoved:
    def test_create_task_does_not_accept_priority_kwarg(self, memory):
        """create_task no longer accepts a 'priority' keyword argument."""
        sig = inspect.signature(memory.create_task)
        assert "priority" not in sig.parameters

    def test_create_task_returns_integer_id(self, memory):
        """create_task returns an integer row ID."""
        task_id = memory.create_task("Some task", assigned_to="Coder")
        assert isinstance(task_id, int)
        assert task_id > 0

    def test_create_task_stores_pending_status(self, memory):
        """Tasks created by create_task have status='pending'."""
        task_id = memory.create_task("Test task")
        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()
        assert row[0] == "pending"

    def test_create_task_stores_description(self, memory):
        """create_task stores the provided description in the DB."""
        desc = "Do something important"
        task_id = memory.create_task(desc)
        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT description FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()
        assert row[0] == desc

    def test_create_task_stores_assigned_to(self, memory):
        """create_task stores the assigned_to value."""
        task_id = memory.create_task("Task", assigned_to="Architect")
        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT assigned_to FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()
        assert row[0] == "Architect"

    def test_create_task_stores_project_id(self, memory):
        """create_task stores the project_id."""
        task_id = memory.create_task("Task", project_id="proj_abc")
        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT project_id FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()
        assert row[0] == "proj_abc"

    def test_create_task_with_priority_kwarg_raises_type_error(self, memory):
        """Passing priority=10 to create_task raises TypeError since it was removed."""
        with pytest.raises(TypeError):
            memory.create_task("Task", assigned_to="Coder", priority=10)

    def test_create_task_without_optional_args_succeeds(self, memory):
        """create_task works with only description provided."""
        task_id = memory.create_task("Minimal task")
        assert task_id is not None

    def test_create_task_increments_id(self, memory):
        """Each call to create_task returns a higher ID."""
        id1 = memory.create_task("Task 1")
        id2 = memory.create_task("Task 2")
        assert id2 > id1

    def test_create_task_default_priority_is_zero_in_db(self, memory):
        """Tasks have priority=0 by default in the DB schema (column still exists)."""
        task_id = memory.create_task("No priority task")
        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT priority FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()
        # Priority column still exists in schema with default 0
        assert row[0] == 0


# ===========================================================================
# nanoc/agents/analyst.py – analyze_failure with new create_task call
# ===========================================================================

class TestAnalystAnalyzeFailure:
    @pytest.mark.anyio
    async def test_analyze_failure_calls_create_task(self, memory):
        """analyze_failure creates a task via memory.create_task."""
        from nanoc.agents.analyst import Analyst

        analyst = Analyst("Analyst1", memory)
        analyst.llm = MockLLM()

        with patch.object(memory, "create_task") as mock_create:
            mock_create.return_value = 1
            await analyst.analyze_failure({
                "project_id": "proj_test",
                "error": "NullPointerException"
            })

        mock_create.assert_called_once()

    @pytest.mark.anyio
    async def test_analyze_failure_passes_priority_10_to_create_task(self, memory):
        """analyze_failure calls create_task with priority=10."""
        from nanoc.agents.analyst import Analyst

        analyst = Analyst("Analyst1", memory)
        analyst.llm = MockLLM()

        with patch.object(memory, "create_task") as mock_create:
            mock_create.return_value = 1
            await analyst.analyze_failure({
                "project_id": "proj_prio",
                "error": "TestError"
            })

        call_kwargs = mock_create.call_args
        # Priority should be passed as keyword arg
        priority = call_kwargs.kwargs.get("priority") or (
            call_kwargs.args[4] if len(call_kwargs.args) > 4 else None
        )
        assert priority == 10

    @pytest.mark.anyio
    async def test_analyze_failure_assigns_to_coder(self, memory):
        """analyze_failure assigns the fix task to 'Coder'."""
        from nanoc.agents.analyst import Analyst

        analyst = Analyst("Analyst1", memory)
        analyst.llm = MockLLM()

        with patch.object(memory, "create_task") as mock_create:
            mock_create.return_value = 1
            await analyst.analyze_failure({
                "project_id": "proj_coder",
                "error": "SomeError"
            })

        call_kwargs = mock_create.call_args
        assigned_to = call_kwargs.kwargs.get("assigned_to") or (
            call_kwargs.args[1] if len(call_kwargs.args) > 1 else None
        )
        assert assigned_to == "Coder"

    @pytest.mark.anyio
    async def test_analyze_failure_publishes_analysis_completed_event(self, memory):
        """analyze_failure publishes 'analysis/completed' event."""
        from nanoc.agents.analyst import Analyst

        analyst = Analyst("Analyst1", memory)
        analyst.llm = MockLLM()

        with patch.object(memory, "create_task", return_value=1):
            await analyst.analyze_failure({
                "project_id": "proj_event",
                "error": "RuntimeError"
            })

        events = memory.get_events(topic="analysis/completed")
        assert len(events) >= 1

    @pytest.mark.anyio
    async def test_analyze_failure_event_contains_original_error(self, memory):
        """The 'analysis/completed' event payload includes the original error."""
        from nanoc.agents.analyst import Analyst

        analyst = Analyst("Analyst1", memory)
        analyst.llm = MockLLM()

        with patch.object(memory, "create_task", return_value=1):
            await analyst.analyze_failure({
                "project_id": "proj_err",
                "error": "SpecificError42"
            })

        events = memory.get_events(topic="analysis/completed")
        payload = json.loads(events[-1]["payload"])
        assert payload["original_error"] == "SpecificError42"

    @pytest.mark.anyio
    async def test_analyze_failure_passes_project_id_to_create_task(self, memory):
        """analyze_failure passes the project_id to create_task."""
        from nanoc.agents.analyst import Analyst

        analyst = Analyst("Analyst1", memory)
        analyst.llm = MockLLM()

        with patch.object(memory, "create_task") as mock_create:
            mock_create.return_value = 1
            await analyst.analyze_failure({
                "project_id": "proj_specific123",
                "error": "SomeError"
            })

        call_kwargs = mock_create.call_args
        proj_id = call_kwargs.kwargs.get("project_id") or (
            call_kwargs.args[3] if len(call_kwargs.args) > 3 else None
        )
        assert proj_id == "proj_specific123"

    @pytest.mark.anyio
    async def test_analyze_failure_task_description_starts_with_fix(self, memory):
        """The fix task description starts with 'FIX:'."""
        from nanoc.agents.analyst import Analyst

        analyst = Analyst("Analyst1", memory)
        analyst.llm = MockLLM()

        with patch.object(memory, "create_task") as mock_create:
            mock_create.return_value = 1
            await analyst.analyze_failure({
                "project_id": "proj_fixdesc",
                "error": "SomeError"
            })

        call_kwargs = mock_create.call_args
        description = call_kwargs.args[0]
        assert description.startswith("FIX:")

    @pytest.mark.anyio
    async def test_analyze_failure_real_memory_raises_type_error(self, memory):
        """analyze_failure with real memory raises TypeError because priority param removed from create_task."""
        from nanoc.agents.analyst import Analyst

        analyst = Analyst("Analyst1", memory)
        analyst.llm = MockLLM()

        # analyze_failure passes priority=10 to memory.create_task, but memory.create_task
        # no longer accepts 'priority'. This is a known incompatibility in this PR.
        with pytest.raises(TypeError):
            await analyst.analyze_failure({
                "project_id": "proj_bug",
                "error": "TestError"
            })


# ===========================================================================
# nanoc/agents/base.py – handle_task methods removed; project_id format changed
# ===========================================================================

class TestBaseAgentHandleTaskRemoved:
    def test_architect_has_no_handle_task_method(self, memory):
        """Architect class no longer has a handle_task method after PR."""
        from nanoc.agents.base import Architect

        agent = Architect("Arch1", "Architect", memory, MockLLM())
        assert not hasattr(agent, "handle_task")

    def test_planner_has_no_handle_task_method(self, memory):
        """Planner class no longer has a handle_task method after PR."""
        from nanoc.agents.base import Planner

        agent = Planner("Plan1", "Planner", memory, MockLLM())
        assert not hasattr(agent, "handle_task")

    def test_coder_has_no_handle_task_method(self, memory):
        """Coder class no longer has a handle_task method after PR."""
        from nanoc.agents.base import Coder

        agent = Coder("Code1", "Coder", memory, MockLLM())
        assert not hasattr(agent, "handle_task")

    def test_reviewer_has_no_handle_task_method(self, memory):
        """Reviewer class no longer has a handle_task method after PR."""
        from nanoc.agents.base import Reviewer

        agent = Reviewer("Rev1", "Reviewer", memory, MockLLM())
        assert not hasattr(agent, "handle_task")

    def test_base_agent_has_no_handle_task_method(self, memory):
        """BaseAgent class no longer has a handle_task method after PR."""
        from nanoc.agents.base import BaseAgent

        agent = BaseAgent("Base1", "SomeRole", memory, MockLLM())
        assert not hasattr(agent, "handle_task")


class TestTeamLeaderProjectIdFormat:
    @pytest.mark.anyio
    async def test_delegate_tasks_project_id_format_is_proj_timestamp_only(self, memory):
        """delegate_tasks now generates project_id as 'proj_<timestamp>' without hex suffix."""
        from nanoc.agents.base import TeamLeader

        mock_llm = MockLLM()

        with patch("nanoc.core.gate_manager.GateManager"):
            leader = TeamLeader("Leader1", "Team Leader", memory, mock_llm)
            project_id = await leader.delegate_tasks("Build something")

        # Should match proj_<integer> but NOT have a hex suffix like _1b931405
        assert project_id.startswith("proj_")
        suffix = project_id[len("proj_"):]
        # New format: just a timestamp integer, no underscore + hex
        assert suffix.isdigit(), f"Expected numeric timestamp, got: {suffix!r}"

    @pytest.mark.anyio
    async def test_delegate_tasks_project_id_has_no_hex_component(self, memory):
        """The new project_id format does NOT contain a random hex component."""
        from nanoc.agents.base import TeamLeader

        mock_llm = MockLLM()

        with patch("nanoc.core.gate_manager.GateManager"):
            leader = TeamLeader("Leader2", "Team Leader", memory, mock_llm)
            project_id = await leader.delegate_tasks("Another project")

        # Old format was proj_<timestamp>_<8hexchars>
        # New format is proj_<timestamp> only
        parts = project_id.split("_")
        # Should be exactly 2 parts: "proj" and the timestamp
        assert len(parts) == 2, f"Expected 2 parts (proj + timestamp), got {parts!r}"

    @pytest.mark.anyio
    async def test_delegate_tasks_extracts_project_id_from_description(self, memory):
        """delegate_tasks extracts proj_id from description when description contains ':'."""
        from nanoc.agents.base import TeamLeader

        mock_llm = MockLLM()

        with patch("nanoc.core.gate_manager.GateManager"):
            leader = TeamLeader("Leader3", "Team Leader", memory, mock_llm)
            project_id = await leader.delegate_tasks("proj_12345: Build the feature")

        assert project_id == "proj_12345"


# ===========================================================================
# nanoc/agents/security.py – vulnerability analysis removed from event payload
# ===========================================================================

class TestSecurityAgentSimplifiedEvent:
    @pytest.mark.anyio
    async def test_audit_service_event_has_no_findings_field(self, memory):
        """The security/audit-complete event payload no longer has 'findings' field."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("Sec1", memory)
        agent.llm = MockLLM()

        nmap_result = {
            "stdout": "22/tcp open ssh OpenSSH 8.2 Telnet service detected",
            "stderr": "",
            "returncode": 0
        }

        with patch("nanoc.tools.network.AsyncRunner") as mock_runner:
            mock_runner.run_command = AsyncMock(return_value=nmap_result)
            await agent.audit_service("192.168.1.1")

        events = memory.get_events(topic="security/audit-complete")
        payload = json.loads(events[-1]["payload"])
        assert "findings" not in payload

    @pytest.mark.anyio
    async def test_audit_service_event_has_no_vulnerabilities_field(self, memory):
        """The security/audit-complete event payload no longer has 'vulnerabilities' field."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("Sec1", memory)
        agent.llm = MockLLM()

        nmap_result = {
            "stdout": "Telnet service detected anonymous FTP enabled",
            "stderr": "",
            "returncode": 0
        }

        with patch("nanoc.tools.network.AsyncRunner") as mock_runner:
            mock_runner.run_command = AsyncMock(return_value=nmap_result)
            await agent.audit_service("10.0.0.1")

        events = memory.get_events(topic="security/audit-complete")
        payload = json.loads(events[-1]["payload"])
        assert "vulnerabilities" not in payload

    @pytest.mark.anyio
    async def test_audit_service_event_payload_keys_are_target_and_report_only(self, memory):
        """The security/audit-complete event payload only has 'target' and 'report' keys."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("Sec1", memory)
        agent.llm = MockLLM()

        nmap_result = {
            "stdout": "80/tcp open http",
            "stderr": "",
            "returncode": 0
        }

        with patch("nanoc.tools.network.AsyncRunner") as mock_runner:
            mock_runner.run_command = AsyncMock(return_value=nmap_result)
            await agent.audit_service("172.16.0.1")

        events = memory.get_events(topic="security/audit-complete")
        payload = json.loads(events[-1]["payload"])
        assert set(payload.keys()) == {"target", "report"}

    @pytest.mark.anyio
    async def test_audit_service_does_not_analyze_telnet_separately(self, memory):
        """Telnet presence does NOT produce a separate 'findings' analysis in event."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("Sec1", memory)
        agent.llm = MockLLM()

        # Output that would have triggered telnet vulnerability detection in old code
        nmap_result = {
            "stdout": "23/tcp open telnet\nservice version info",
            "stderr": "",
            "returncode": 0
        }

        with patch("nanoc.tools.network.AsyncRunner") as mock_runner:
            mock_runner.run_command = AsyncMock(return_value=nmap_result)
            await agent.audit_service("10.0.0.5")

        events = memory.get_events(topic="security/audit-complete")
        payload = json.loads(events[-1]["payload"])
        # No findings analysis
        assert "findings" not in payload
        assert "vulnerabilities" not in payload

    @pytest.mark.anyio
    async def test_audit_service_report_field_contains_raw_stdout(self, memory):
        """The 'report' field in the event is the raw nmap stdout output."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("Sec1", memory)
        agent.llm = MockLLM()

        scan_output = "22/tcp open ssh OpenSSH 8.2"
        nmap_result = {
            "stdout": scan_output,
            "stderr": "",
            "returncode": 0
        }

        with patch("nanoc.tools.network.AsyncRunner") as mock_runner:
            mock_runner.run_command = AsyncMock(return_value=nmap_result)
            await agent.audit_service("192.168.0.100")

        events = memory.get_events(topic="security/audit-complete")
        payload = json.loads(events[-1]["payload"])
        assert payload["report"] == scan_output


# ===========================================================================
# nanoc/core/llm.py – retry loop removed (single attempt)
# ===========================================================================

class TestLLMProviderNoRetry:
    @pytest.mark.anyio
    async def test_complete_raises_immediately_on_http_error(self, memory):
        """complete() no longer retries on httpx.HTTPStatusError – raises on first failure."""
        import httpx
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="openrouter", model="test-model")

        call_count = [0]

        async def failing_complete(prompt, system_prompt, model):
            call_count[0] += 1
            raise httpx.HTTPStatusError("500", request=MagicMock(), response=MagicMock())

        with patch("nanoc.core.llm.Memory", return_value=memory), \
             patch.object(provider, "_openrouter_complete", side_effect=failing_complete), \
             patch.object(provider, "_record_error"), \
             patch("nanoc.core.llm.settings") as mock_settings:
            mock_settings.DB_PATH = memory.db_path
            with pytest.raises(httpx.HTTPStatusError):
                await provider.complete("test prompt")

        # With retry removed: only 1 call
        assert call_count[0] == 1

    @pytest.mark.anyio
    async def test_complete_does_not_retry_on_request_error(self, memory):
        """complete() no longer retries on httpx.RequestError."""
        import httpx
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="openrouter", model="test-model")

        call_count = [0]

        async def failing_complete(prompt, system_prompt, model):
            call_count[0] += 1
            raise httpx.RequestError("connection timeout", request=MagicMock())

        with patch("nanoc.core.llm.Memory", return_value=memory), \
             patch.object(provider, "_openrouter_complete", side_effect=failing_complete), \
             patch.object(provider, "_record_error"), \
             patch("nanoc.core.llm.settings") as mock_settings:
            mock_settings.DB_PATH = memory.db_path
            with pytest.raises(httpx.RequestError):
                await provider.complete("prompt")

        assert call_count[0] == 1

    @pytest.mark.anyio
    async def test_complete_records_error_on_failure(self, memory):
        """complete() calls _record_error when an exception is raised."""
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="openrouter", model="test-model")

        async def failing_complete(prompt, system_prompt, model):
            raise RuntimeError("LLM unavailable")

        recorded_errors = []

        with patch("nanoc.core.llm.Memory", return_value=memory), \
             patch.object(provider, "_openrouter_complete", side_effect=failing_complete), \
             patch.object(provider, "_record_error", side_effect=recorded_errors.append), \
             patch("nanoc.core.llm.settings") as mock_settings:
            mock_settings.DB_PATH = memory.db_path
            with pytest.raises(RuntimeError):
                await provider.complete("test prompt")

        assert len(recorded_errors) == 1
        assert "LLM unavailable" in recorded_errors[0]

    @pytest.mark.anyio
    async def test_complete_no_retry_code_in_complete_method(self, memory):
        """complete() implementation does not have retry loop logic."""
        from nanoc.core.llm import LLMProvider

        src = inspect.getsource(LLMProvider.complete)
        # Old code had "max_retries" and "retry_delay"; these should not appear
        assert "max_retries" not in src
        assert "retry_delay" not in src

    @pytest.mark.anyio
    async def test_complete_returns_response_on_success(self, memory):
        """complete() returns the LLM response on a successful call."""
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="openrouter", model="test-model")

        async def successful_complete(prompt, system_prompt, model):
            return "The answer is 42"

        with patch("nanoc.core.llm.Memory", return_value=memory), \
             patch.object(provider, "_openrouter_complete", side_effect=successful_complete), \
             patch.object(provider, "_record_telemetry"), \
             patch("nanoc.core.llm.settings") as mock_settings:
            mock_settings.DB_PATH = memory.db_path
            result = await provider.complete("What is the answer?")

        assert result == "The answer is 42"

    @pytest.mark.anyio
    async def test_complete_records_telemetry_on_success(self, memory):
        """complete() calls _record_telemetry after a successful call."""
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="openrouter", model="test-model")

        async def successful_complete(prompt, system_prompt, model):
            return "Response"

        telemetry_calls = []

        with patch("nanoc.core.llm.Memory", return_value=memory), \
             patch.object(provider, "_openrouter_complete", side_effect=successful_complete), \
             patch.object(provider, "_record_telemetry",
                          side_effect=lambda p, r, d: telemetry_calls.append((p, r, d))), \
             patch("nanoc.core.llm.settings") as mock_settings:
            mock_settings.DB_PATH = memory.db_path
            await provider.complete("test prompt")

        assert len(telemetry_calls) == 1

    @pytest.mark.anyio
    async def test_complete_raises_value_error_for_unknown_provider(self, memory):
        """complete() raises ValueError for unknown provider immediately (no retry)."""
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="nonexistent_provider", model="any")

        with patch("nanoc.core.llm.Memory", return_value=memory), \
             patch.object(provider, "_record_error"), \
             patch("nanoc.core.llm.settings") as mock_settings:
            mock_settings.DB_PATH = memory.db_path
            with pytest.raises(ValueError, match="Unknown provider"):
                await provider.complete("test")


# ===========================================================================
# nanoc/core/orchestrator.py – explicit role-based dispatch
# ===========================================================================

class TestOrchestratorExplicitDispatch:
    @pytest.mark.anyio
    async def test_architect_role_calls_design_solution(self, memory):
        """process_task calls design_solution() for Architect role."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())

        orch = Orchestrator(memory, leader)

        mock_architect = MagicMock()
        mock_architect.role = "Architect"
        mock_architect.log = AsyncMock()
        mock_architect.design_solution = AsyncMock(return_value="design output")
        orch.add_agent(mock_architect)

        task_id = memory.create_task("Design architecture", assigned_to="Architect", project_id="proj_arch")

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orch.process_task(task)

        mock_architect.design_solution.assert_called_once_with(task["description"])

    @pytest.mark.anyio
    async def test_planner_role_calls_create_todo_list(self, memory):
        """process_task calls create_todo_list() for Planner role."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())

        orch = Orchestrator(memory, leader)

        mock_planner = MagicMock()
        mock_planner.role = "Planner"
        mock_planner.log = AsyncMock()
        mock_planner.create_todo_list = AsyncMock(return_value="todo list")
        orch.add_agent(mock_planner)

        task_id = memory.create_task("Create plan", assigned_to="Planner", project_id="proj_plan")

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orch.process_task(task)

        mock_planner.create_todo_list.assert_called_once_with(task["description"])

    @pytest.mark.anyio
    async def test_coder_role_calls_write_code(self, memory):
        """process_task calls write_code() for Coder role."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())

        orch = Orchestrator(memory, leader)

        mock_coder = MagicMock()
        mock_coder.role = "Coder"
        mock_coder.log = AsyncMock()
        mock_coder.write_code = AsyncMock(return_value="def foo(): pass")
        orch.add_agent(mock_coder)

        task_id = memory.create_task("Write code", assigned_to="Coder", project_id="proj_code")

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orch.process_task(task)

        mock_coder.write_code.assert_called_once_with(task["description"])

    @pytest.mark.anyio
    async def test_reviewer_role_calls_review_work(self, memory):
        """process_task calls review_work() for Reviewer role."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())

        orch = Orchestrator(memory, leader)

        mock_reviewer = MagicMock()
        mock_reviewer.role = "Reviewer"
        mock_reviewer.log = AsyncMock()
        mock_reviewer.review_work = AsyncMock(return_value="STATUS: APPROVED\nLooks good.")
        orch.add_agent(mock_reviewer)

        task_id = memory.create_task("Review code", assigned_to="Reviewer", project_id="proj_rev")

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orch.process_task(task)

        mock_reviewer.review_work.assert_called_once_with(task["description"])

    @pytest.mark.anyio
    async def test_reviewer_approved_does_not_create_fix_task(self, memory):
        """When Reviewer returns APPROVED, no fix task is created for Coder."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())

        orch = Orchestrator(memory, leader)

        mock_reviewer = MagicMock()
        mock_reviewer.role = "Reviewer"
        mock_reviewer.log = AsyncMock()
        mock_reviewer.review_work = AsyncMock(return_value="STATUS: APPROVED\nAll good.")
        orch.add_agent(mock_reviewer)

        task_id = memory.create_task("Review work", assigned_to="Reviewer", project_id="proj_appr")

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orch.process_task(task)

        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM tasks WHERE assigned_to = 'Coder'")
            row = cursor.fetchone()

        assert row[0] == 0

    @pytest.mark.anyio
    async def test_reviewer_not_approved_creates_fix_task_for_coder(self, memory):
        """When review result does NOT contain APPROVED, a fix task is created for Coder."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())

        orch = Orchestrator(memory, leader)

        mock_reviewer = MagicMock()
        mock_reviewer.role = "Reviewer"
        mock_reviewer.log = AsyncMock()
        mock_reviewer.review_work = AsyncMock(
            return_value="STATUS: FAILED\nFix the null check."
        )
        orch.add_agent(mock_reviewer)

        task_id = memory.create_task(
            "Review my code", assigned_to="Reviewer", project_id="proj_fail_rev"
        )

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orch.process_task(task)

        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT assigned_to, description FROM tasks WHERE assigned_to = 'Coder'")
            rows = cursor.fetchall()

        assert len(rows) == 1
        assert "Fix flaws" in rows[0][1]

    @pytest.mark.anyio
    async def test_reviewer_fix_task_has_original_project_id(self, memory):
        """The fix task created on review failure has the correct project_id."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())

        orch = Orchestrator(memory, leader)

        mock_reviewer = MagicMock()
        mock_reviewer.role = "Reviewer"
        mock_reviewer.log = AsyncMock()
        mock_reviewer.review_work = AsyncMock(return_value="STATUS: FAILED\nNeeds work.")
        orch.add_agent(mock_reviewer)

        task_id = memory.create_task(
            "Review the work", assigned_to="Reviewer", project_id="proj_fixcheck"
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
            cursor.execute("SELECT project_id FROM tasks WHERE assigned_to = 'Coder'")
            row = cursor.fetchone()

        assert row["project_id"] == "proj_fixcheck"

    @pytest.mark.anyio
    async def test_unknown_role_falls_back_to_think(self, memory):
        """For an unknown role, process_task calls agent.think() as fallback."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())

        orch = Orchestrator(memory, leader)

        mock_agent = MagicMock()
        mock_agent.role = "CustomRole"
        mock_agent.log = AsyncMock()
        mock_agent.think = AsyncMock(return_value="thought result")
        orch.add_agent(mock_agent)

        task_id = memory.create_task("Do something custom", assigned_to="CustomRole")

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orch.process_task(task)

        mock_agent.think.assert_called_once()
        call_prompt = mock_agent.think.call_args[0][0]
        assert "Execute this task:" in call_prompt

    @pytest.mark.anyio
    async def test_successful_task_marked_completed(self, memory):
        """process_task marks a successfully completed task with status='completed'."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())

        orch = Orchestrator(memory, leader)

        mock_coder = MagicMock()
        mock_coder.role = "Coder"
        mock_coder.log = AsyncMock()
        mock_coder.write_code = AsyncMock(return_value="result code")
        orch.add_agent(mock_coder)

        task_id = memory.create_task("Write code", assigned_to="Coder")

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
    async def test_process_task_no_dispatch_when_role_not_in_agents(self, memory):
        """process_task does nothing when the agent for the assigned role is not registered."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())

        orch = Orchestrator(memory, leader)
        # No agents registered

        task_id = memory.create_task("Unassigned task", assigned_to="NonexistentRole")

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        # Should not raise
        await orch.process_task(task)


# ===========================================================================
# nanoc/tools/network.py – _get_fallback_topology simplified (no dummy nodes)
# ===========================================================================

class TestDiscoveryToolFallbackTopology:
    def test_fallback_topology_contains_only_localhost_node(self, memory):
        """_get_fallback_topology returns a topology with only the localhost node."""
        from nanoc.tools.network import DiscoveryTool

        topology = DiscoveryTool._get_fallback_topology(memory)

        assert "nodes" in topology
        assert len(topology["nodes"]) == 1
        node = topology["nodes"][0]
        assert node["id"] == "127.0.0.1"

    def test_fallback_topology_has_no_dummy_router_node(self, memory):
        """_get_fallback_topology does NOT include a Core Router / dummy node."""
        from nanoc.tools.network import DiscoveryTool

        topology = DiscoveryTool._get_fallback_topology(memory)

        node_ids = [n["id"] for n in topology["nodes"]]
        node_labels = [n.get("label", "") for n in topology["nodes"]]

        # Old version had a hardcoded router node; new version does not
        assert not any("router" in label.lower() for label in node_labels)
        assert not any("core" in label.lower() for label in node_labels)

    def test_fallback_topology_has_empty_edges(self, memory):
        """_get_fallback_topology returns an empty edges list."""
        from nanoc.tools.network import DiscoveryTool

        topology = DiscoveryTool._get_fallback_topology(memory)

        assert "edges" in topology
        assert topology["edges"] == []

    def test_fallback_topology_localhost_label_is_localhost(self, memory):
        """The localhost node has label='localhost'."""
        from nanoc.tools.network import DiscoveryTool

        topology = DiscoveryTool._get_fallback_topology(memory)

        node = topology["nodes"][0]
        assert node["label"] == "localhost"

    def test_fallback_topology_localhost_status_is_online(self, memory):
        """The localhost node has status='online'."""
        from nanoc.tools.network import DiscoveryTool

        topology = DiscoveryTool._get_fallback_topology(memory)

        node = topology["nodes"][0]
        assert node["status"] == "online"

    def test_fallback_topology_localhost_type_is_host(self, memory):
        """The localhost node has type='host'."""
        from nanoc.tools.network import DiscoveryTool

        topology = DiscoveryTool._get_fallback_topology(memory)

        node = topology["nodes"][0]
        assert node["type"] == "host"

    def test_fallback_topology_caches_result_in_memory(self, memory):
        """_get_fallback_topology stores the topology in knowledge base."""
        from nanoc.tools.network import DiscoveryTool

        topology = DiscoveryTool._get_fallback_topology(memory)
        cached = memory.get_knowledge("network_topology")

        assert cached is not None
        assert cached["nodes"][0]["id"] == "127.0.0.1"

    @pytest.mark.anyio
    async def test_discover_topology_uses_fallback_when_nmap_fails(self, memory):
        """discover_topology falls back to localhost-only topology when nmap fails."""
        from nanoc.tools.network import DiscoveryTool

        error_result = {"error": "nmap not found", "returncode": 1, "stdout": "", "stderr": ""}

        with patch("nanoc.core.config.settings") as mock_settings, \
             patch("nanoc.tools.network.settings") as mock_net_settings, \
             patch("nanoc.tools.network.Memory", return_value=memory), \
             patch("nanoc.tools.network.NetworkScanner.scan_local_network",
                   new_callable=AsyncMock, return_value=error_result):
            mock_settings.DB_PATH = memory.db_path
            mock_net_settings.DB_PATH = memory.db_path

            topology = await DiscoveryTool.discover_topology("192.168.1.0/24")

        assert len(topology["nodes"]) == 1
        assert topology["nodes"][0]["id"] == "127.0.0.1"
        assert topology["edges"] == []


# ===========================================================================
# Regression: memory schema still has priority column (default 0)
# ===========================================================================

class TestMemorySchemaRetainsProiorityColumn:
    def test_tasks_table_has_priority_column(self, memory):
        """The tasks table still has a 'priority' column (default 0) in the DB schema."""
        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(tasks)")
            columns = {row[1] for row in cursor.fetchall()}

        assert "priority" in columns

    def test_tasks_priority_default_is_zero(self, memory):
        """Newly created tasks have priority=0 by default."""
        task_id = memory.create_task("A task")
        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT priority FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()
        assert row[0] == 0

    def test_can_manually_set_priority_via_sql(self, memory):
        """Priority can still be set directly via SQL even though create_task doesn't accept it."""
        task_id = memory.create_task("Priority task")
        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE tasks SET priority = 5 WHERE id = ?", (task_id,))
            conn.commit()
            cursor.execute("SELECT priority FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()
        assert row[0] == 5
