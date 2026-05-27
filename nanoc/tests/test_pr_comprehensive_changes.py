"""
Comprehensive tests for the bulk_improve PR changes.

Changed source files covered:
  - bulk_improve.py               (new: 100-task bulk inbox injector)
  - nanoc/agents/analyst.py       (analyze_failure passes priority=10 to create_task, which no longer accepts it)
  - nanoc/agents/base.py          (handle_task removed; project_id simplified; leader field removed from event; os import removed)
  - nanoc/agents/security.py      (vulnerability analysis removed; event payload only {target, report})
  - nanoc/core/llm.py             (retry loop removed; single attempt; all errors call _record_error)
  - nanoc/core/orchestrator.py    (explicit role-based dispatch replacing dynamic handle_task)
  - nanoc/memory/memory.py        (priority param removed from create_task; schema still has priority column)
  - nanoc/tools/network.py        (_get_fallback_topology returns only localhost node, no dummy router)
"""
import asyncio
import inspect
import json
import os
import sqlite3
import sys
import time
import unittest
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
    db_path = str(tmp_path / "test_comp_pr.db")
    mem = Memory(db_path)
    yield mem
    if os.path.exists(db_path):
        os.remove(db_path)


# ===========================================================================
# nanoc/memory/memory.py  –  priority param removed from create_task
# ===========================================================================

class TestMemoryCreateTaskSignature:
    """create_task no longer accepts a 'priority' parameter."""

    def test_priority_not_in_create_task_signature(self, memory):
        sig = inspect.signature(memory.create_task)
        assert "priority" not in sig.parameters

    def test_create_task_with_priority_kwarg_raises_type_error(self, memory):
        with pytest.raises(TypeError):
            memory.create_task("Some task", priority=10)

    def test_create_task_with_positional_priority_raises_type_error(self, memory):
        # description, assigned_to, parent_id, project_id are the 4 valid params;
        # a 5th positional arg should raise TypeError
        with pytest.raises(TypeError):
            memory.create_task("Task", "Coder", None, "proj_1", 10)

    def test_create_task_succeeds_with_required_arg_only(self, memory):
        task_id = memory.create_task("Minimal task")
        assert isinstance(task_id, int)
        assert task_id > 0

    def test_create_task_returns_auto_incremented_ids(self, memory):
        id1 = memory.create_task("Task A")
        id2 = memory.create_task("Task B")
        id3 = memory.create_task("Task C")
        assert id1 < id2 < id3

    def test_create_task_stores_description(self, memory):
        desc = "Do the important thing"
        task_id = memory.create_task(desc)
        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT description FROM tasks WHERE id=?", (task_id,))
            assert cursor.fetchone()[0] == desc

    def test_create_task_default_status_is_pending(self, memory):
        task_id = memory.create_task("Check status")
        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM tasks WHERE id=?", (task_id,))
            assert cursor.fetchone()[0] == "pending"

    def test_create_task_stores_assigned_to(self, memory):
        task_id = memory.create_task("Task", assigned_to="Planner")
        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT assigned_to FROM tasks WHERE id=?", (task_id,))
            assert cursor.fetchone()[0] == "Planner"

    def test_create_task_stores_project_id(self, memory):
        task_id = memory.create_task("Task", project_id="proj_99")
        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT project_id FROM tasks WHERE id=?", (task_id,))
            assert cursor.fetchone()[0] == "proj_99"

    def test_create_task_stores_parent_id(self, memory):
        parent_id = memory.create_task("Parent task")
        child_id = memory.create_task("Child task", parent_id=parent_id)
        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT parent_id FROM tasks WHERE id=?", (child_id,))
            assert cursor.fetchone()[0] == parent_id


class TestMemorySchemaRetainsPriorityColumn:
    """The DB schema still includes a priority column (default 0)."""

    def test_tasks_table_has_priority_column(self, memory):
        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(tasks)")
            columns = {row[1] for row in cursor.fetchall()}
        assert "priority" in columns

    def test_new_tasks_have_default_priority_zero(self, memory):
        task_id = memory.create_task("Priority zero task")
        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT priority FROM tasks WHERE id=?", (task_id,))
            assert cursor.fetchone()[0] == 0

    def test_priority_can_be_set_via_direct_sql(self, memory):
        task_id = memory.create_task("Task needing priority")
        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE tasks SET priority=7 WHERE id=?", (task_id,))
            conn.commit()
            cursor.execute("SELECT priority FROM tasks WHERE id=?", (task_id,))
            assert cursor.fetchone()[0] == 7


# ===========================================================================
# nanoc/agents/analyst.py  –  BUG: priority=10 passed to create_task
# ===========================================================================

class TestAnalystBugPriorityIncompatibility:
    """
    analyst.py calls `self.memory.create_task(..., priority=10)` but
    memory.create_task no longer accepts 'priority'. This is a known
    incompatibility introduced in this PR.
    """

    @pytest.mark.asyncio
    async def test_analyze_failure_raises_type_error_with_real_memory(self, memory):
        """analyze_failure raises TypeError because it passes priority=10 to create_task."""
        from nanoc.agents.analyst import Analyst

        analyst = Analyst("Analyst1", memory)
        analyst.llm = MockLLM()

        with pytest.raises(TypeError):
            await analyst.analyze_failure({
                "project_id": "proj_bug",
                "error": "TestError"
            })

    @pytest.mark.asyncio
    async def test_analyze_failure_with_mocked_create_task_succeeds(self, memory):
        """With a mocked create_task, analyze_failure completes without error."""
        from nanoc.agents.analyst import Analyst

        analyst = Analyst("Analyst1", memory)
        analyst.llm = MockLLM()

        with patch.object(memory, "create_task", return_value=1):
            # Should not raise if create_task is mocked to accept priority
            await analyst.analyze_failure({
                "project_id": "proj_ok",
                "error": "SomeError"
            })

    @pytest.mark.asyncio
    async def test_analyze_failure_passes_priority_10_to_create_task(self, memory):
        """analyze_failure passes priority=10 kwarg to create_task."""
        from nanoc.agents.analyst import Analyst

        analyst = Analyst("Analyst1", memory)
        analyst.llm = MockLLM()

        with patch.object(memory, "create_task") as mock_create:
            mock_create.return_value = 1
            await analyst.analyze_failure({
                "project_id": "proj_prio",
                "error": "ErrorX"
            })

        _, kwargs = mock_create.call_args
        assert kwargs.get("priority") == 10

    @pytest.mark.asyncio
    async def test_analyze_failure_assigns_to_coder(self, memory):
        """analyze_failure assigns the FIX task to 'Coder'."""
        from nanoc.agents.analyst import Analyst

        analyst = Analyst("Analyst1", memory)
        analyst.llm = MockLLM()

        with patch.object(memory, "create_task") as mock_create:
            mock_create.return_value = 1
            await analyst.analyze_failure({
                "project_id": "proj_coder",
                "error": "ErrorY"
            })

        _, kwargs = mock_create.call_args
        assert kwargs.get("assigned_to") == "Coder"

    @pytest.mark.asyncio
    async def test_analyze_failure_task_description_starts_with_fix(self, memory):
        """The fix task description starts with 'FIX:'."""
        from nanoc.agents.analyst import Analyst

        analyst = Analyst("Analyst1", memory)
        analyst.llm = MockLLM()

        with patch.object(memory, "create_task") as mock_create:
            mock_create.return_value = 1
            await analyst.analyze_failure({
                "project_id": "proj_fix",
                "error": "ErrorZ"
            })

        args, _ = mock_create.call_args
        assert args[0].startswith("FIX:")

    @pytest.mark.asyncio
    async def test_analyze_failure_passes_project_id_to_create_task(self, memory):
        """analyze_failure passes the project_id to create_task."""
        from nanoc.agents.analyst import Analyst

        analyst = Analyst("Analyst1", memory)
        analyst.llm = MockLLM()

        with patch.object(memory, "create_task") as mock_create:
            mock_create.return_value = 1
            await analyst.analyze_failure({
                "project_id": "proj_specific",
                "error": "Error"
            })

        _, kwargs = mock_create.call_args
        assert kwargs.get("project_id") == "proj_specific"

    @pytest.mark.asyncio
    async def test_analyze_failure_publishes_analysis_completed_event(self, memory):
        """analyze_failure publishes 'analysis/completed' event on success (mocked create_task)."""
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

    @pytest.mark.asyncio
    async def test_analyze_failure_event_contains_original_error(self, memory):
        """The analysis/completed event payload includes original_error."""
        from nanoc.agents.analyst import Analyst

        analyst = Analyst("Analyst1", memory)
        analyst.llm = MockLLM()

        with patch.object(memory, "create_task", return_value=1):
            await analyst.analyze_failure({
                "project_id": "proj_err_check",
                "error": "SpecificError99"
            })

        events = memory.get_events(topic="analysis/completed")
        payload = json.loads(events[-1]["payload"])
        assert payload["original_error"] == "SpecificError99"

    @pytest.mark.asyncio
    async def test_analyze_failure_event_contains_strategy_key(self, memory):
        """The analysis/completed event payload has a 'strategy' key."""
        from nanoc.agents.analyst import Analyst

        analyst = Analyst("Analyst1", memory)
        analyst.llm = MockLLM()

        with patch.object(memory, "create_task", return_value=1):
            await analyst.analyze_failure({
                "project_id": "proj_strat",
                "error": "SomeError"
            })

        events = memory.get_events(topic="analysis/completed")
        payload = json.loads(events[-1]["payload"])
        assert "strategy" in payload


# ===========================================================================
# nanoc/agents/base.py  –  handle_task removed; os import removed; project_id format
# ===========================================================================

class TestBaseAgentHandleTaskRemoved:
    """handle_task was removed from all agent classes in this PR."""

    def test_base_agent_no_handle_task(self, memory):
        from nanoc.agents.base import BaseAgent
        agent = BaseAgent("agent1", "GenericRole", memory, MockLLM())
        assert not hasattr(agent, "handle_task")

    def test_architect_no_handle_task(self, memory):
        from nanoc.agents.base import Architect
        agent = Architect("arch1", "Architect", memory, MockLLM())
        assert not hasattr(agent, "handle_task")

    def test_planner_no_handle_task(self, memory):
        from nanoc.agents.base import Planner
        agent = Planner("plan1", "Planner", memory, MockLLM())
        assert not hasattr(agent, "handle_task")

    def test_coder_no_handle_task(self, memory):
        from nanoc.agents.base import Coder
        agent = Coder("code1", "Coder", memory, MockLLM())
        assert not hasattr(agent, "handle_task")

    def test_reviewer_no_handle_task(self, memory):
        from nanoc.agents.base import Reviewer
        agent = Reviewer("rev1", "Reviewer", memory, MockLLM())
        assert not hasattr(agent, "handle_task")

    def test_project_manager_no_handle_task(self, memory):
        from nanoc.agents.base import ProjectManager
        agent = ProjectManager("pm1", memory)
        assert not hasattr(agent, "handle_task")


class TestBaseAgentOsImportRemoved:
    """The 'os' module was removed from nanoc/agents/base.py imports in this PR."""

    def test_os_not_imported_in_base_module(self):
        import nanoc.agents.base as base_module
        # If os was imported at module level, it would be in the module's namespace
        # The PR removed this import, so os should not be a direct attribute
        # (Note: it may still be accessible via other imports; we check the source)
        src = inspect.getsource(base_module)
        lines = src.split('\n')
        top_import_lines = [l.strip() for l in lines[:30]]
        assert "import os" not in top_import_lines


class TestTeamLeaderProjectIdFormat:
    """project_id is now just 'proj_<timestamp>' without the random hex suffix."""

    @pytest.mark.asyncio
    async def test_delegate_tasks_project_id_has_no_hex_suffix(self, memory):
        """project_id from delegate_tasks no longer contains a hex component after timestamp."""
        from nanoc.agents.base import TeamLeader

        with patch("nanoc.core.gate_manager.GateManager"):
            leader = TeamLeader("Leader1", "Team Leader", memory, MockLLM())
            project_id = await leader.delegate_tasks("Build something")

        # New format: proj_<integer_timestamp>  (exactly 2 parts when split by '_')
        parts = project_id.split("_")
        assert len(parts) == 2, f"Expected 'proj_<int>', got {project_id!r}"
        assert parts[0] == "proj"
        assert parts[1].isdigit(), f"Expected digit timestamp, got {parts[1]!r}"

    @pytest.mark.asyncio
    async def test_delegate_tasks_project_id_starts_with_proj_(self, memory):
        """project_id always starts with 'proj_'."""
        from nanoc.agents.base import TeamLeader

        with patch("nanoc.core.gate_manager.GateManager"):
            leader = TeamLeader("Leader2", "Team Leader", memory, MockLLM())
            project_id = await leader.delegate_tasks("Another project")

        assert project_id.startswith("proj_")

    @pytest.mark.asyncio
    async def test_delegate_tasks_project_id_timestamp_is_integer(self, memory):
        """The numeric part of the project_id is a valid integer (UNIX timestamp)."""
        from nanoc.agents.base import TeamLeader

        with patch("nanoc.core.gate_manager.GateManager"):
            leader = TeamLeader("Leader3", "Team Leader", memory, MockLLM())
            project_id = await leader.delegate_tasks("Timestamp test")

        numeric_part = project_id[len("proj_"):]
        ts = int(numeric_part)  # should not raise
        assert ts > 0

    @pytest.mark.asyncio
    async def test_delegate_tasks_extracts_project_id_from_colon_format(self, memory):
        """If description has 'proj_<id>: ...', that project_id is extracted and used."""
        from nanoc.agents.base import TeamLeader

        with patch("nanoc.core.gate_manager.GateManager"):
            leader = TeamLeader("Leader4", "Team Leader", memory, MockLLM())
            project_id = await leader.delegate_tasks("proj_9999: do something cool")

        assert project_id == "proj_9999"

    @pytest.mark.asyncio
    async def test_delegate_tasks_event_has_no_leader_field(self, memory):
        """project/incoming-job event does NOT include a 'leader' field."""
        from nanoc.agents.base import TeamLeader

        with patch("nanoc.core.gate_manager.GateManager"):
            leader = TeamLeader("Leader5", "Team Leader", memory, MockLLM())
            await leader.delegate_tasks("Test project no leader field")

        events = memory.get_events(topic="project/incoming-job")
        assert len(events) >= 1
        payload = json.loads(events[-1]["payload"])
        assert "leader" not in payload

    @pytest.mark.asyncio
    async def test_delegate_tasks_event_has_project_id_and_description(self, memory):
        """project/incoming-job event has project_id and description keys."""
        from nanoc.agents.base import TeamLeader

        with patch("nanoc.core.gate_manager.GateManager"):
            leader = TeamLeader("Leader6", "Team Leader", memory, MockLLM())
            project_id = await leader.delegate_tasks("Build event monitoring")

        events = memory.get_events(topic="project/incoming-job")
        payload = json.loads(events[-1]["payload"])
        assert "project_id" in payload
        assert "description" in payload
        assert payload["project_id"] == project_id


# ===========================================================================
# nanoc/agents/security.py  –  event payload simplified to {target, report}
# ===========================================================================

class TestSecurityAgentSimplifiedPayload:
    """The security/audit-complete event no longer has findings or vulnerabilities."""

    @pytest.mark.asyncio
    async def test_audit_service_event_payload_keys_are_target_and_report(self, memory):
        """Event payload has exactly the keys 'target' and 'report'."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("Sec1", memory)
        agent.llm = MockLLM()

        nmap_result = {"stdout": "22/tcp open ssh", "stderr": "", "returncode": 0}

        with patch("nanoc.tools.network.AsyncRunner") as mock_runner:
            mock_runner.run_command = AsyncMock(return_value=nmap_result)
            await agent.audit_service("192.168.1.1")

        events = memory.get_events(topic="security/audit-complete")
        assert len(events) >= 1
        payload = json.loads(events[-1]["payload"])
        assert set(payload.keys()) == {"target", "report"}

    @pytest.mark.asyncio
    async def test_audit_service_event_has_no_findings_key(self, memory):
        """'findings' key is absent from event payload."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("Sec1", memory)
        agent.llm = MockLLM()

        nmap_result = {
            "stdout": "telnet service detected anonymous FTP enabled expired ssl",
            "stderr": "",
            "returncode": 0
        }

        with patch("nanoc.tools.network.AsyncRunner") as mock_runner:
            mock_runner.run_command = AsyncMock(return_value=nmap_result)
            await agent.audit_service("10.0.0.1")

        events = memory.get_events(topic="security/audit-complete")
        payload = json.loads(events[-1]["payload"])
        assert "findings" not in payload

    @pytest.mark.asyncio
    async def test_audit_service_event_has_no_vulnerabilities_key(self, memory):
        """'vulnerabilities' key is absent from event payload."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("Sec1", memory)
        agent.llm = MockLLM()

        nmap_result = {
            "stdout": "23/tcp open telnet ssh protocol 1.0",
            "stderr": "",
            "returncode": 0
        }

        with patch("nanoc.tools.network.AsyncRunner") as mock_runner:
            mock_runner.run_command = AsyncMock(return_value=nmap_result)
            await agent.audit_service("172.16.0.1")

        events = memory.get_events(topic="security/audit-complete")
        payload = json.loads(events[-1]["payload"])
        assert "vulnerabilities" not in payload

    @pytest.mark.asyncio
    async def test_audit_service_event_target_matches_input(self, memory):
        """The 'target' field in the event matches the argument passed to audit_service."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("Sec1", memory)
        agent.llm = MockLLM()

        nmap_result = {"stdout": "scan output", "stderr": "", "returncode": 0}

        with patch("nanoc.tools.network.AsyncRunner") as mock_runner:
            mock_runner.run_command = AsyncMock(return_value=nmap_result)
            await agent.audit_service("10.10.10.10")

        events = memory.get_events(topic="security/audit-complete")
        payload = json.loads(events[-1]["payload"])
        assert payload["target"] == "10.10.10.10"

    @pytest.mark.asyncio
    async def test_audit_service_event_report_is_raw_stdout(self, memory):
        """The 'report' field in the event equals the raw nmap stdout."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("Sec1", memory)
        agent.llm = MockLLM()

        raw_output = "Nmap scan report\n22/tcp open ssh OpenSSH 8.2\n80/tcp open http"
        nmap_result = {"stdout": raw_output, "stderr": "", "returncode": 0}

        with patch("nanoc.tools.network.AsyncRunner") as mock_runner:
            mock_runner.run_command = AsyncMock(return_value=nmap_result)
            await agent.audit_service("192.168.0.50")

        events = memory.get_events(topic="security/audit-complete")
        payload = json.loads(events[-1]["payload"])
        assert payload["report"] == raw_output

    @pytest.mark.asyncio
    async def test_audit_service_no_event_on_nmap_error(self, memory):
        """When nmap returns an error, no event is published."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("Sec1", memory)
        agent.llm = MockLLM()

        error_result = {"error": "nmap not found", "stdout": "", "stderr": ""}

        with patch("nanoc.tools.network.AsyncRunner") as mock_runner:
            mock_runner.run_command = AsyncMock(return_value=error_result)
            await agent.audit_service("1.2.3.4")

        events = memory.get_events(topic="security/audit-complete")
        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_audit_service_returns_stdout_on_success(self, memory):
        """audit_service returns the raw stdout string on success."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("Sec1", memory)
        agent.llm = MockLLM()

        raw_output = "22/tcp open ssh"
        nmap_result = {"stdout": raw_output, "stderr": "", "returncode": 0}

        with patch("nanoc.tools.network.AsyncRunner") as mock_runner:
            mock_runner.run_command = AsyncMock(return_value=nmap_result)
            result = await agent.audit_service("192.168.1.100")

        assert result == raw_output

    @pytest.mark.asyncio
    async def test_audit_service_returns_error_dict_on_failure(self, memory):
        """audit_service returns the error dict when nmap fails."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("Sec1", memory)
        agent.llm = MockLLM()

        error_result = {"error": "permission denied", "stdout": "", "stderr": ""}

        with patch("nanoc.tools.network.AsyncRunner") as mock_runner:
            mock_runner.run_command = AsyncMock(return_value=error_result)
            result = await agent.audit_service("9.9.9.9")

        assert result == error_result

    @pytest.mark.asyncio
    async def test_vulnerability_analysis_code_removed_from_source(self, memory):
        """The vulnerability pattern-matching logic is absent from security.py source."""
        from nanoc.agents import security as sec_module

        src = inspect.getsource(sec_module)
        # Old code contained these strings
        assert "protocol 1.0" not in src
        assert "Insecure SSH protocol" not in src
        assert "Telnet service detected" not in src
        assert "Anonymous FTP" not in src

    def test_security_agent_role_is_security(self, memory):
        """SecurityAgent initializes with role='Security'."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("Sec42", memory)
        assert agent.role == "Security"


# ===========================================================================
# nanoc/core/llm.py  –  retry loop removed; single attempt
# ===========================================================================

class TestLLMProviderNoRetry:
    """The retry loop was removed; complete() now makes a single attempt only."""

    @pytest.mark.asyncio
    async def test_complete_makes_exactly_one_attempt_on_http_error(self, memory):
        """On HTTPStatusError, complete() does NOT retry – it fails immediately."""
        import httpx
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="openrouter", model="test-model")
        call_count = [0]

        async def always_fail(prompt, system_prompt, model):
            call_count[0] += 1
            raise httpx.HTTPStatusError("500", request=MagicMock(), response=MagicMock())

        with patch("nanoc.core.llm.Memory", return_value=memory), \
             patch.object(provider, "_openrouter_complete", side_effect=always_fail), \
             patch.object(provider, "_record_error"), \
             patch("nanoc.core.llm.settings") as mock_settings:
            mock_settings.DB_PATH = memory.db_path
            with pytest.raises(httpx.HTTPStatusError):
                await provider.complete("test prompt")

        assert call_count[0] == 1, "Should only try once (no retry)"

    @pytest.mark.asyncio
    async def test_complete_makes_exactly_one_attempt_on_request_error(self, memory):
        """On RequestError, complete() does NOT retry."""
        import httpx
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="openrouter", model="test-model")
        call_count = [0]

        async def connection_fail(prompt, system_prompt, model):
            call_count[0] += 1
            raise httpx.RequestError("connection refused", request=MagicMock())

        with patch("nanoc.core.llm.Memory", return_value=memory), \
             patch.object(provider, "_openrouter_complete", side_effect=connection_fail), \
             patch.object(provider, "_record_error"), \
             patch("nanoc.core.llm.settings") as mock_settings:
            mock_settings.DB_PATH = memory.db_path
            with pytest.raises(httpx.RequestError):
                await provider.complete("prompt")

        assert call_count[0] == 1

    @pytest.mark.asyncio
    async def test_complete_records_error_on_any_exception(self, memory):
        """Every exception triggers _record_error (not just HTTPStatusError)."""
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="openrouter", model="test-model")
        recorded = []

        async def random_fail(prompt, system_prompt, model):
            raise ValueError("bad value")

        with patch("nanoc.core.llm.Memory", return_value=memory), \
             patch.object(provider, "_openrouter_complete", side_effect=random_fail), \
             patch.object(provider, "_record_error", side_effect=recorded.append), \
             patch("nanoc.core.llm.settings") as mock_settings:
            mock_settings.DB_PATH = memory.db_path
            with pytest.raises(ValueError):
                await provider.complete("test")

        assert len(recorded) == 1
        assert "bad value" in recorded[0]

    @pytest.mark.asyncio
    async def test_complete_re_raises_exception_after_recording_error(self, memory):
        """After calling _record_error, the original exception is re-raised."""
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="openrouter", model="test-model")

        async def fail_with_runtime(prompt, system_prompt, model):
            raise RuntimeError("LLM unavailable")

        with patch("nanoc.core.llm.Memory", return_value=memory), \
             patch.object(provider, "_openrouter_complete", side_effect=fail_with_runtime), \
             patch.object(provider, "_record_error"), \
             patch("nanoc.core.llm.settings") as mock_settings:
            mock_settings.DB_PATH = memory.db_path
            with pytest.raises(RuntimeError, match="LLM unavailable"):
                await provider.complete("test")

    @pytest.mark.asyncio
    async def test_complete_returns_response_on_success(self, memory):
        """complete() returns the LLM response when the call succeeds."""
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="openrouter", model="test-model")

        async def success(prompt, system_prompt, model):
            return "successful response"

        with patch("nanoc.core.llm.Memory", return_value=memory), \
             patch.object(provider, "_openrouter_complete", side_effect=success), \
             patch.object(provider, "_record_telemetry"), \
             patch("nanoc.core.llm.settings") as mock_settings:
            mock_settings.DB_PATH = memory.db_path
            result = await provider.complete("test prompt")

        assert result == "successful response"

    @pytest.mark.asyncio
    async def test_complete_records_telemetry_on_success(self, memory):
        """complete() calls _record_telemetry on a successful response."""
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="openrouter", model="test-model")
        telemetry_calls = []

        async def success(prompt, system_prompt, model):
            return "good response"

        with patch("nanoc.core.llm.Memory", return_value=memory), \
             patch.object(provider, "_openrouter_complete", side_effect=success), \
             patch.object(provider, "_record_telemetry",
                          side_effect=lambda p, r, d: telemetry_calls.append((p, r, d))), \
             patch("nanoc.core.llm.settings") as mock_settings:
            mock_settings.DB_PATH = memory.db_path
            await provider.complete("prompt text")

        assert len(telemetry_calls) == 1
        prompt_used, response_used, duration_ms = telemetry_calls[0]
        assert prompt_used == "prompt text"
        assert response_used == "good response"
        assert duration_ms >= 0

    @pytest.mark.asyncio
    async def test_complete_raises_value_error_for_unknown_provider(self, memory):
        """complete() raises ValueError immediately for an unknown provider (no retry)."""
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="unknown_xyz", model="any")

        with patch("nanoc.core.llm.Memory", return_value=memory), \
             patch.object(provider, "_record_error"), \
             patch("nanoc.core.llm.settings") as mock_settings:
            mock_settings.DB_PATH = memory.db_path
            with pytest.raises(ValueError, match="Unknown provider"):
                await provider.complete("test")

    def test_complete_source_has_no_retry_logic(self):
        """complete() implementation source does not contain retry loop variables."""
        from nanoc.core.llm import LLMProvider
        src = inspect.getsource(LLMProvider.complete)
        assert "max_retries" not in src
        assert "retry_delay" not in src
        assert "for attempt" not in src

    def test_complete_source_has_no_sleep_call(self):
        """complete() no longer calls asyncio.sleep (was used for retry backoff)."""
        from nanoc.core.llm import LLMProvider
        src = inspect.getsource(LLMProvider.complete)
        # asyncio.sleep was used in the retry loop; should be gone
        assert "asyncio.sleep" not in src

    @pytest.mark.asyncio
    async def test_complete_does_not_record_telemetry_on_failure(self, memory):
        """_record_telemetry is NOT called when the LLM call raises an exception."""
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="openrouter", model="test-model")
        telemetry_calls = []

        async def fail(prompt, system_prompt, model):
            raise RuntimeError("fail")

        with patch("nanoc.core.llm.Memory", return_value=memory), \
             patch.object(provider, "_openrouter_complete", side_effect=fail), \
             patch.object(provider, "_record_error"), \
             patch.object(provider, "_record_telemetry",
                          side_effect=telemetry_calls.append), \
             patch("nanoc.core.llm.settings") as mock_settings:
            mock_settings.DB_PATH = memory.db_path
            with pytest.raises(RuntimeError):
                await provider.complete("test")

        assert len(telemetry_calls) == 0


# ===========================================================================
# nanoc/core/orchestrator.py  –  explicit role-based dispatch
# ===========================================================================

class TestOrchestratorExplicitRoleDispatch:
    """process_task now dispatches explicitly by role instead of calling handle_task."""

    @pytest.mark.asyncio
    async def test_architect_dispatched_to_design_solution(self, memory):
        """For 'Architect' role, process_task calls agent.design_solution()."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_arch = MagicMock()
        mock_arch.role = "Architect"
        mock_arch.log = AsyncMock()
        mock_arch.design_solution = AsyncMock(return_value="design done")
        orch.add_agent(mock_arch)

        task_id = memory.create_task("Build architecture", assigned_to="Architect")
        task = _get_task(memory, task_id)

        await orch.process_task(task)

        mock_arch.design_solution.assert_called_once_with(task["description"])

    @pytest.mark.asyncio
    async def test_planner_dispatched_to_create_todo_list(self, memory):
        """For 'Planner' role, process_task calls agent.create_todo_list()."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_planner = MagicMock()
        mock_planner.role = "Planner"
        mock_planner.log = AsyncMock()
        mock_planner.create_todo_list = AsyncMock(return_value="todo list")
        orch.add_agent(mock_planner)

        task_id = memory.create_task("Plan the system", assigned_to="Planner")
        task = _get_task(memory, task_id)

        await orch.process_task(task)

        mock_planner.create_todo_list.assert_called_once_with(task["description"])

    @pytest.mark.asyncio
    async def test_coder_dispatched_to_write_code(self, memory):
        """For 'Coder' role, process_task calls agent.write_code()."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_coder = MagicMock()
        mock_coder.role = "Coder"
        mock_coder.log = AsyncMock()
        mock_coder.write_code = AsyncMock(return_value="def foo(): pass")
        orch.add_agent(mock_coder)

        task_id = memory.create_task("Write code", assigned_to="Coder")
        task = _get_task(memory, task_id)

        await orch.process_task(task)

        mock_coder.write_code.assert_called_once_with(task["description"])

    @pytest.mark.asyncio
    async def test_reviewer_dispatched_to_review_work(self, memory):
        """For 'Reviewer' role, process_task calls agent.review_work()."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_reviewer = MagicMock()
        mock_reviewer.role = "Reviewer"
        mock_reviewer.log = AsyncMock()
        mock_reviewer.review_work = AsyncMock(return_value="STATUS: APPROVED\nLooks good.")
        orch.add_agent(mock_reviewer)

        task_id = memory.create_task("Review the code", assigned_to="Reviewer")
        task = _get_task(memory, task_id)

        await orch.process_task(task)

        mock_reviewer.review_work.assert_called_once_with(task["description"])

    @pytest.mark.asyncio
    async def test_unknown_role_dispatched_to_think(self, memory):
        """For an unrecognized role, process_task falls back to agent.think()."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_agent = MagicMock()
        mock_agent.role = "CustomExpertRole"
        mock_agent.log = AsyncMock()
        mock_agent.think = AsyncMock(return_value="thought output")
        orch.add_agent(mock_agent)

        task_id = memory.create_task("Custom task", assigned_to="CustomExpertRole")
        task = _get_task(memory, task_id)

        await orch.process_task(task)

        mock_agent.think.assert_called_once()
        prompt = mock_agent.think.call_args[0][0]
        assert "Execute this task:" in prompt
        assert task["description"] in prompt

    @pytest.mark.asyncio
    async def test_reviewer_approved_does_not_create_fix_task(self, memory):
        """When Reviewer result contains APPROVED, no fix task is created for Coder."""
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
        task = _get_task(memory, task_id)

        await orch.process_task(task)

        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM tasks WHERE assigned_to='Coder'")
            coder_tasks = cursor.fetchone()[0]

        assert coder_tasks == 0

    @pytest.mark.asyncio
    async def test_reviewer_not_approved_creates_fix_task_for_coder(self, memory):
        """When Reviewer result does NOT contain APPROVED, a fix task is created for Coder."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_reviewer = MagicMock()
        mock_reviewer.role = "Reviewer"
        mock_reviewer.log = AsyncMock()
        mock_reviewer.review_work = AsyncMock(
            return_value="STATUS: FAILED\nNull pointer on line 42."
        )
        orch.add_agent(mock_reviewer)

        task_id = memory.create_task("Review code", assigned_to="Reviewer", project_id="proj_fail")
        task = _get_task(memory, task_id)

        await orch.process_task(task)

        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT description, assigned_to, project_id FROM tasks WHERE assigned_to='Coder'"
            )
            rows = cursor.fetchall()

        assert len(rows) == 1
        desc, assigned, proj = rows[0]
        assert "Fix flaws" in desc
        assert assigned == "Coder"

    @pytest.mark.asyncio
    async def test_reviewer_fix_task_uses_original_project_id(self, memory):
        """The fix task created on review failure has the original project_id."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_reviewer = MagicMock()
        mock_reviewer.role = "Reviewer"
        mock_reviewer.log = AsyncMock()
        mock_reviewer.review_work = AsyncMock(return_value="STATUS: FAILED\nBad code.")
        orch.add_agent(mock_reviewer)

        task_id = memory.create_task(
            "Review my code", assigned_to="Reviewer", project_id="proj_origcheck"
        )
        task = _get_task(memory, task_id)

        await orch.process_task(task)

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT project_id FROM tasks WHERE assigned_to='Coder'")
            row = cursor.fetchone()

        assert row["project_id"] == "proj_origcheck"

    @pytest.mark.asyncio
    async def test_reviewer_fix_task_description_includes_review_result(self, memory):
        """The fix task description contains the review result."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_reviewer = MagicMock()
        mock_reviewer.role = "Reviewer"
        mock_reviewer.log = AsyncMock()
        review_result = "STATUS: FAILED\nMissing error handling in function xyz."
        mock_reviewer.review_work = AsyncMock(return_value=review_result)
        orch.add_agent(mock_reviewer)

        task_id = memory.create_task("Review task", assigned_to="Reviewer", project_id="proj_desc")
        task = _get_task(memory, task_id)

        await orch.process_task(task)

        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT description FROM tasks WHERE assigned_to='Coder'")
            row = cursor.fetchone()

        assert review_result in row[0]

    @pytest.mark.asyncio
    async def test_successful_task_marked_completed(self, memory):
        """A successfully processed task gets status='completed'."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_coder = MagicMock()
        mock_coder.role = "Coder"
        mock_coder.log = AsyncMock()
        mock_coder.write_code = AsyncMock(return_value="code result")
        orch.add_agent(mock_coder)

        task_id = memory.create_task("Write the code", assigned_to="Coder")
        task = _get_task(memory, task_id)

        await orch.process_task(task)

        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM tasks WHERE id=?", (task_id,))
            row = cursor.fetchone()

        assert row[0] == "completed"

    @pytest.mark.asyncio
    async def test_successful_task_stores_result(self, memory):
        """The result from the agent method is stored in the task record."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_planner = MagicMock()
        mock_planner.role = "Planner"
        mock_planner.log = AsyncMock()
        mock_planner.create_todo_list = AsyncMock(return_value="TASK: Build DB\nTASK: Test DB")
        orch.add_agent(mock_planner)

        task_id = memory.create_task("Plan project", assigned_to="Planner")
        task = _get_task(memory, task_id)

        await orch.process_task(task)

        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT result FROM tasks WHERE id=?", (task_id,))
            row = cursor.fetchone()

        assert "TASK: Build DB" in row[0]

    @pytest.mark.asyncio
    async def test_failed_task_increments_retry_count(self, memory):
        """When an agent raises an exception, retry_count is incremented in the DB."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_coder = MagicMock()
        mock_coder.role = "Coder"
        mock_coder.log = AsyncMock()
        mock_coder.write_code = AsyncMock(side_effect=RuntimeError("compile error"))
        orch.add_agent(mock_coder)

        task_id = memory.create_task("Write code", assigned_to="Coder", project_id="proj_retry")
        task = _get_task(memory, task_id)

        await orch.process_task(task)

        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT retry_count, status FROM tasks WHERE id=?", (task_id,))
            row = cursor.fetchone()

        assert row[0] == 1  # incremented from 0
        assert row[1] == "pending"  # still retryable

    @pytest.mark.asyncio
    async def test_failed_task_at_max_retries_sets_failed_status(self, memory):
        """When retry_count reaches max_retries, task status becomes 'failed'."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_coder = MagicMock()
        mock_coder.role = "Coder"
        mock_coder.log = AsyncMock()
        mock_coder.write_code = AsyncMock(side_effect=RuntimeError("still failing"))
        orch.add_agent(mock_coder)

        task_id = memory.create_task("Write code", assigned_to="Coder", project_id="proj_max")

        # Set retry_count to max
        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE tasks SET retry_count=3, max_retries=3 WHERE id=?", (task_id,)
            )
            conn.commit()

        task = _get_task(memory, task_id)
        await orch.process_task(task)

        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM tasks WHERE id=?", (task_id,))
            row = cursor.fetchone()

        assert row[0] == "failed"

    @pytest.mark.asyncio
    async def test_permanently_failed_task_publishes_task_failed_event(self, memory):
        """When a task truly fails, a 'task/failed' event is published."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_coder = MagicMock()
        mock_coder.role = "Coder"
        mock_coder.log = AsyncMock()
        mock_coder.write_code = AsyncMock(side_effect=RuntimeError("permanent"))
        orch.add_agent(mock_coder)

        task_id = memory.create_task("Fail code", assigned_to="Coder", project_id="proj_event")

        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE tasks SET retry_count=3, max_retries=3 WHERE id=?", (task_id,)
            )
            conn.commit()

        task = _get_task(memory, task_id)
        await orch.process_task(task)

        events = memory.get_events(topic="task/failed")
        assert len(events) >= 1
        payload = json.loads(events[-1]["payload"])
        assert payload["task_id"] == task_id
        assert payload["project_id"] == "proj_event"

    @pytest.mark.asyncio
    async def test_task_with_retries_remaining_no_task_failed_event(self, memory):
        """When retries remain, no 'task/failed' event is published."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_arch = MagicMock()
        mock_arch.role = "Architect"
        mock_arch.log = AsyncMock()
        mock_arch.design_solution = AsyncMock(side_effect=RuntimeError("transient"))
        orch.add_agent(mock_arch)

        task_id = memory.create_task("Design arch", assigned_to="Architect")
        task = _get_task(memory, task_id)

        await orch.process_task(task)

        events = memory.get_events(topic="task/failed")
        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_no_agent_registered_for_role_does_not_raise(self, memory):
        """process_task does nothing if no agent is registered for the task's role."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)
        # No agents registered

        task_id = memory.create_task("Orphan task", assigned_to="UnregisteredRole")
        task = _get_task(memory, task_id)

        # Should not raise
        await orch.process_task(task)

    def test_orchestrator_source_no_handle_task_call(self):
        """Orchestrator.process_task source no longer calls handle_task."""
        from nanoc.core.orchestrator import Orchestrator
        src = inspect.getsource(Orchestrator.process_task)
        assert "handle_task" not in src

    def test_orchestrator_source_has_explicit_role_checks(self):
        """Orchestrator.process_task source has explicit role comparisons."""
        from nanoc.core.orchestrator import Orchestrator
        src = inspect.getsource(Orchestrator.process_task)
        assert '"Architect"' in src
        assert '"Planner"' in src
        assert '"Coder"' in src
        assert '"Reviewer"' in src


# ===========================================================================
# nanoc/tools/network.py  –  _get_fallback_topology returns only localhost
# ===========================================================================

class TestDiscoveryToolFallbackTopology:
    """_get_fallback_topology now returns only localhost node, no dummy router."""

    def test_fallback_topology_has_exactly_one_node(self, memory):
        from nanoc.tools.network import DiscoveryTool

        topology = DiscoveryTool._get_fallback_topology(memory)
        assert len(topology["nodes"]) == 1

    def test_fallback_topology_node_id_is_localhost_ip(self, memory):
        from nanoc.tools.network import DiscoveryTool

        topology = DiscoveryTool._get_fallback_topology(memory)
        assert topology["nodes"][0]["id"] == "127.0.0.1"

    def test_fallback_topology_node_label_is_localhost(self, memory):
        from nanoc.tools.network import DiscoveryTool

        topology = DiscoveryTool._get_fallback_topology(memory)
        assert topology["nodes"][0]["label"] == "localhost"

    def test_fallback_topology_node_type_is_host(self, memory):
        from nanoc.tools.network import DiscoveryTool

        topology = DiscoveryTool._get_fallback_topology(memory)
        assert topology["nodes"][0]["type"] == "host"

    def test_fallback_topology_node_status_is_online(self, memory):
        from nanoc.tools.network import DiscoveryTool

        topology = DiscoveryTool._get_fallback_topology(memory)
        assert topology["nodes"][0]["status"] == "online"

    def test_fallback_topology_edges_are_empty(self, memory):
        from nanoc.tools.network import DiscoveryTool

        topology = DiscoveryTool._get_fallback_topology(memory)
        assert topology["edges"] == []

    def test_fallback_topology_has_nodes_and_edges_keys(self, memory):
        from nanoc.tools.network import DiscoveryTool

        topology = DiscoveryTool._get_fallback_topology(memory)
        assert "nodes" in topology
        assert "edges" in topology

    def test_fallback_topology_no_router_node(self, memory):
        """No dummy 'Core Router' node is present in the fallback topology."""
        from nanoc.tools.network import DiscoveryTool

        topology = DiscoveryTool._get_fallback_topology(memory)
        labels = [n.get("label", "").lower() for n in topology["nodes"]]
        node_ids = [n.get("id", "").lower() for n in topology["nodes"]]

        assert not any("router" in label for label in labels)
        assert not any("core" in label for label in labels)
        assert not any("router" in nid for nid in node_ids)

    def test_fallback_topology_stores_to_knowledge_base(self, memory):
        """_get_fallback_topology caches the topology in the knowledge base."""
        from nanoc.tools.network import DiscoveryTool

        topology = DiscoveryTool._get_fallback_topology(memory)
        cached = memory.get_knowledge("network_topology")

        assert cached is not None
        assert cached["nodes"][0]["id"] == "127.0.0.1"
        assert cached["edges"] == []

    def test_fallback_topology_idempotent_on_multiple_calls(self, memory):
        """Multiple calls to _get_fallback_topology return the same structure."""
        from nanoc.tools.network import DiscoveryTool

        topo1 = DiscoveryTool._get_fallback_topology(memory)
        topo2 = DiscoveryTool._get_fallback_topology(memory)

        assert topo1["nodes"] == topo2["nodes"]
        assert topo1["edges"] == topo2["edges"]

    @pytest.mark.asyncio
    async def test_discover_topology_falls_back_to_localhost_when_nmap_errors(self, memory):
        """discover_topology returns localhost-only topology when nmap returns an error."""
        from nanoc.tools.network import DiscoveryTool

        error_result = {"error": "nmap not found", "returncode": 1, "stdout": "", "stderr": ""}

        with patch("nanoc.tools.network.Memory", return_value=memory), \
             patch("nanoc.tools.network.settings") as mock_settings, \
             patch("nanoc.tools.network.NetworkScanner.scan_local_network",
                   new_callable=AsyncMock, return_value=error_result):
            mock_settings.DB_PATH = memory.db_path

            topology = await DiscoveryTool.discover_topology("192.168.1.0/24")

        assert len(topology["nodes"]) == 1
        assert topology["nodes"][0]["id"] == "127.0.0.1"
        assert topology["edges"] == []

    @pytest.mark.asyncio
    async def test_discover_topology_falls_back_when_nmap_returncode_nonzero(self, memory):
        """discover_topology falls back when nmap returns non-zero returncode."""
        from nanoc.tools.network import DiscoveryTool

        fail_result = {"returncode": 1, "stdout": "", "stderr": "Operation not permitted"}

        with patch("nanoc.tools.network.Memory", return_value=memory), \
             patch("nanoc.tools.network.settings") as mock_settings, \
             patch("nanoc.tools.network.NetworkScanner.scan_local_network",
                   new_callable=AsyncMock, return_value=fail_result):
            mock_settings.DB_PATH = memory.db_path

            topology = await DiscoveryTool.discover_topology("10.0.0.0/24")

        assert topology["nodes"][0]["id"] == "127.0.0.1"
        assert topology["edges"] == []


# ===========================================================================
# bulk_improve.py  –  100-task bulk inbox injector
# ===========================================================================

class TestBulkImproveNewScript:
    """Tests for the new bulk_improve.py root-level script."""

    def test_main_creates_inbox_directory(self):
        """main() calls os.makedirs for 'nanoc/inbox'."""
        import bulk_improve as bm

        made_dirs = []

        def fake_makedirs(path, exist_ok=False):
            made_dirs.append(path)

        with patch.object(bm.os, "makedirs", side_effect=fake_makedirs), \
             patch("builtins.open", unittest.mock.mock_open()), \
             patch.object(bm.time, "sleep"), \
             patch.object(bm.time, "time", return_value=1700000000):
            bm.main()

        assert "nanoc/inbox" in made_dirs

    def test_main_calls_makedirs_with_exist_ok_true(self):
        """main() calls os.makedirs(..., exist_ok=True)."""
        import bulk_improve as bm

        kwargs_seen = {}

        def fake_makedirs(path, exist_ok=False):
            kwargs_seen["path"] = path
            kwargs_seen["exist_ok"] = exist_ok

        with patch.object(bm.os, "makedirs", side_effect=fake_makedirs), \
             patch("builtins.open", unittest.mock.mock_open()), \
             patch.object(bm.time, "sleep"), \
             patch.object(bm.time, "time", return_value=1700000000):
            bm.main()

        assert kwargs_seen["exist_ok"] is True

    def test_main_opens_exactly_100_files(self):
        """main() opens exactly 100 files for writing."""
        import bulk_improve as bm

        mock_open = unittest.mock.mock_open()

        with patch.object(bm.os, "makedirs"), \
             patch("builtins.open", mock_open), \
             patch.object(bm.time, "sleep"), \
             patch.object(bm.time, "time", return_value=1700000000):
            bm.main()

        assert mock_open.call_count == 100

    def test_main_opens_files_in_write_mode(self):
        """main() opens each file with mode='w' (not 'a' or 'r')."""
        import bulk_improve as bm

        open_calls = []

        def capturing_open(path, mode="r"):
            open_calls.append((path, mode))
            return unittest.mock.mock_open()(path, mode)

        with patch.object(bm.os, "makedirs"), \
             patch("builtins.open", side_effect=capturing_open), \
             patch.object(bm.time, "sleep"), \
             patch.object(bm.time, "time", return_value=1700000000):
            bm.main()

        for _, mode in open_calls:
            assert mode == "w", f"Expected mode 'w', got {mode!r}"

    def test_main_file_paths_start_with_inbox_dir(self):
        """All file paths start with 'nanoc/inbox'."""
        import bulk_improve as bm

        opened_paths = []

        def capturing_open(path, mode="r"):
            opened_paths.append(path)
            return unittest.mock.mock_open()(path, mode)

        with patch.object(bm.os, "makedirs"), \
             patch("builtins.open", side_effect=capturing_open), \
             patch.object(bm.time, "sleep"), \
             patch.object(bm.time, "time", return_value=1700000000):
            bm.main()

        assert len(opened_paths) == 100
        for path in opened_paths:
            assert path.startswith("nanoc/inbox"), f"Path not in inbox: {path!r}"

    def test_main_file_names_contain_bulk_task_prefix(self):
        """All filenames contain 'bulk_task_' as a prefix."""
        import bulk_improve as bm

        opened_paths = []

        def capturing_open(path, mode="r"):
            opened_paths.append(path)
            return unittest.mock.mock_open()(path, mode)

        with patch.object(bm.os, "makedirs"), \
             patch("builtins.open", side_effect=capturing_open), \
             patch.object(bm.time, "sleep"), \
             patch.object(bm.time, "time", return_value=1700000000):
            bm.main()

        for path in opened_paths:
            basename = os.path.basename(path)
            assert basename.startswith("bulk_task_"), f"Unexpected filename: {basename!r}"

    def test_main_file_names_end_with_txt_extension(self):
        """All filenames end with '.txt'."""
        import bulk_improve as bm

        opened_paths = []

        def capturing_open(path, mode="r"):
            opened_paths.append(path)
            return unittest.mock.mock_open()(path, mode)

        with patch.object(bm.os, "makedirs"), \
             patch("builtins.open", side_effect=capturing_open), \
             patch.object(bm.time, "sleep"), \
             patch.object(bm.time, "time", return_value=1700000000):
            bm.main()

        for path in opened_paths:
            assert path.endswith(".txt"), f"Expected .txt extension: {path!r}"

    def test_main_file_names_include_loop_index(self):
        """Filenames embed the loop index 0..99."""
        import bulk_improve as bm

        opened_paths = []

        def capturing_open(path, mode="r"):
            opened_paths.append(path)
            return unittest.mock.mock_open()(path, mode)

        with patch.object(bm.os, "makedirs"), \
             patch("builtins.open", side_effect=capturing_open), \
             patch.object(bm.time, "sleep"), \
             patch.object(bm.time, "time", return_value=1700000000):
            bm.main()

        for i, path in enumerate(opened_paths):
            assert f"_{i}.txt" in path, f"Index {i} not found in path: {path!r}"

    def test_main_calls_sleep_100_times(self):
        """main() calls time.sleep exactly 100 times (once per file)."""
        import bulk_improve as bm

        sleep_calls = []

        with patch.object(bm.os, "makedirs"), \
             patch("builtins.open", unittest.mock.mock_open()), \
             patch.object(bm.time, "sleep", side_effect=sleep_calls.append), \
             patch.object(bm.time, "time", return_value=1700000000):
            bm.main()

        assert len(sleep_calls) == 100

    def test_main_sleep_duration_is_001(self):
        """main() sleeps for 0.01 seconds between files."""
        import bulk_improve as bm

        sleep_calls = []

        with patch.object(bm.os, "makedirs"), \
             patch("builtins.open", unittest.mock.mock_open()), \
             patch.object(bm.time, "sleep", side_effect=sleep_calls.append), \
             patch.object(bm.time, "time", return_value=1700000000):
            bm.main()

        assert all(s == 0.01 for s in sleep_calls), f"Unexpected sleep durations: {set(sleep_calls)}"

    def test_main_task_description_mentions_foss(self):
        """The task description written to files mentions 'FOSS'."""
        import bulk_improve as bm

        written = []

        def capturing_open(path, mode="r"):
            handle = MagicMock()
            handle.__enter__ = lambda s: s
            handle.__exit__ = MagicMock(return_value=False)
            handle.write = lambda content: written.append(content)
            return handle

        with patch.object(bm.os, "makedirs"), \
             patch("builtins.open", side_effect=capturing_open), \
             patch.object(bm.time, "sleep"), \
             patch.object(bm.time, "time", return_value=1700000000):
            bm.main()

        assert len(written) == 100
        for content in written:
            assert "FOSS" in content, "Expected FOSS in task description"

    def test_main_task_description_mentions_improvement(self):
        """The task description asks about improving/enhancing the project."""
        import bulk_improve as bm

        written = []

        def capturing_open(path, mode="r"):
            handle = MagicMock()
            handle.__enter__ = lambda s: s
            handle.__exit__ = MagicMock(return_value=False)
            handle.write = lambda content: written.append(content)
            return handle

        with patch.object(bm.os, "makedirs"), \
             patch("builtins.open", side_effect=capturing_open), \
             patch.object(bm.time, "sleep"), \
             patch.object(bm.time, "time", return_value=1700000000):
            bm.main()

        first_content = written[0]
        # Description should contain improvement-related text
        assert "improve" in first_content.lower() or "enhance" in first_content.lower()

    def test_main_uses_time_time_for_timestamp_in_filename(self):
        """main() calls time.time() to produce the timestamp part of filenames."""
        import bulk_improve as bm

        opened_paths = []
        time_call_count = [0]

        def capturing_open(path, mode="r"):
            opened_paths.append(path)
            return unittest.mock.mock_open()(path, mode)

        def fake_time():
            time_call_count[0] += 1
            return 9999999999  # Fixed timestamp

        with patch.object(bm.os, "makedirs"), \
             patch("builtins.open", side_effect=capturing_open), \
             patch.object(bm.time, "sleep"), \
             patch.object(bm.time, "time", side_effect=fake_time):
            bm.main()

        # time.time() should be called once per file
        assert time_call_count[0] == 100
        # All paths should contain the mocked timestamp
        for path in opened_paths:
            assert "9999999999" in path

    def test_main_real_file_creation_integration(self, tmp_path):
        """Integration: main() creates 100 real .txt files in a redirected inbox dir."""
        import bulk_improve as bm

        inbox_dir = str(tmp_path / "nanoc" / "inbox")
        real_makedirs = os.makedirs
        real_join = os.path.join

        def fake_makedirs(path, exist_ok=False):
            if path == "nanoc/inbox":
                real_makedirs(inbox_dir, exist_ok=True)
            else:
                real_makedirs(path, exist_ok=exist_ok)

        def fake_join(*args):
            if args[0] == "nanoc/inbox":
                return real_join(inbox_dir, *args[1:])
            return real_join(*args)

        with patch.object(bm.os, "makedirs", side_effect=fake_makedirs), \
             patch.object(bm.os.path, "join", side_effect=fake_join), \
             patch.object(bm.time, "sleep"):
            bm.main()

        created_files = os.listdir(inbox_dir)
        assert len(created_files) == 100
        for fname in created_files:
            assert fname.startswith("bulk_task_")
            assert fname.endswith(".txt")

    def test_bulk_improve_has_main_function(self):
        """bulk_improve.py exports a 'main' function."""
        import bulk_improve as bm
        assert callable(bm.main)

    def test_bulk_improve_inbox_dir_constant_is_nanoc_inbox(self):
        """The inbox directory used is 'nanoc/inbox' (not configurable)."""
        import bulk_improve as bm
        import ast

        src = inspect.getsource(bm.main)
        assert "nanoc/inbox" in src


# ===========================================================================
# Regression: ensure memory migration code removed (ALTER TABLE no longer attempted)
# ===========================================================================

class TestMemoryMigrationCodeRemoved:
    """The ALTER TABLE migration code was removed from _init_db."""

    def test_init_db_no_alter_table_in_source(self):
        """Memory._init_db source no longer contains ALTER TABLE statement."""
        from nanoc.memory import memory as mem_module
        src = inspect.getsource(mem_module.Memory._init_db)
        assert "ALTER TABLE" not in src

    def test_init_db_creates_priority_column_via_schema_not_migration(self, tmp_path):
        """The tasks table is created with priority column directly in schema."""
        db_path = str(tmp_path / "schema_test.db")
        mem = Memory(db_path)  # initializes DB

        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(tasks)")
            columns = {row[1]: row for row in cursor.fetchall()}

        assert "priority" in columns
        # Check default is 0
        assert columns["priority"][4] == "0"  # dflt_value


# ===========================================================================
# Helper used by orchestrator tests
# ===========================================================================

def _get_task(memory: Memory, task_id: int) -> dict:
    """Retrieve a task dict from DB by id."""
    with sqlite3.connect(memory.db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tasks WHERE id=?", (task_id,))
        return dict(cursor.fetchone())
