"""
Comprehensive tests targeting coverage gaps in the bulk_improve PR changes.

Scope: Only changed/added code in this PR:
  - bulk_improve.py               (root-level bulk task injector)
  - nanoc/agents/analyst.py       (create_task reformatted, still passes priority=10)
  - nanoc/agents/base.py          (handle_task removed; project_id format simplified)
  - nanoc/agents/security.py      (vulnerability analysis removed; simplified event)
  - nanoc/core/llm.py             (retry loop removed; single-attempt)
  - nanoc/core/orchestrator.py    (explicit role dispatch)
  - nanoc/memory/memory.py        (priority param removed from create_task)
  - nanoc/tools/network.py        (_get_fallback_topology returns localhost only)

These tests are designed to complement (not duplicate) coverage already in:
  - test_pr_bulk_improve_changes.py
  - test_pr_latest_changes.py
  - test_pr_new_changes.py
"""
import asyncio
import inspect
import json
import os
import sqlite3
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from nanoc.memory.memory import Memory
from nanoc.tests.mocks import MockLLM


# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def memory(tmp_path):
    db_path = str(tmp_path / "test_gaps.db")
    mem = Memory(db_path)
    yield mem
    if os.path.exists(db_path):
        os.remove(db_path)


# ===========================================================================
# bulk_improve.py – additional edge-case tests
# ===========================================================================

class TestBulkImproveEdgeCases:
    """Edge-case and boundary tests for bulk_improve.main()."""

    def test_files_opened_in_write_mode(self, tmp_path):
        """main() opens each file in 'w' (write) mode, not append or read mode."""
        import bulk_improve as bm

        open_modes = []

        def fake_makedirs(path, exist_ok=False):
            pass

        def capturing_open(path, mode="r"):
            open_modes.append(mode)
            handle = MagicMock()
            handle.__enter__ = lambda s: s
            handle.__exit__ = MagicMock(return_value=False)
            handle.write = MagicMock()
            return handle

        with patch.object(bm.os, "makedirs", side_effect=fake_makedirs), \
             patch("builtins.open", side_effect=capturing_open), \
             patch.object(bm.time, "sleep"), \
             patch.object(bm.time, "time", return_value=1700000000):
            bm.main()

        assert len(open_modes) == 100
        assert all(m == "w" for m in open_modes), f"Expected all 'w' modes, got: {set(open_modes)}"

    def test_task_description_contains_parallel_processing(self, tmp_path):
        """Task description mentions 'Multi Thread Parallel Processing'."""
        import bulk_improve as bm

        written_contents = []

        def capturing_open(path, mode="r"):
            handle = MagicMock()
            handle.__enter__ = lambda s: s
            handle.__exit__ = MagicMock(return_value=False)
            handle.write = MagicMock(side_effect=lambda s: written_contents.append(s))
            return handle

        with patch.object(bm.os, "makedirs"), \
             patch("builtins.open", side_effect=capturing_open), \
             patch.object(bm.time, "sleep"), \
             patch.object(bm.time, "time", return_value=1700000000):
            bm.main()

        assert len(written_contents) == 100
        for content in written_contents:
            assert "Multi Thread Parallel Processing" in content

    def test_task_description_contains_continuous_codebase_improvement(self, tmp_path):
        """Task description contains the 'Continuous Codebase Improvement' phrase."""
        import bulk_improve as bm

        written_contents = []

        def capturing_open(path, mode="r"):
            handle = MagicMock()
            handle.__enter__ = lambda s: s
            handle.__exit__ = MagicMock(return_value=False)
            handle.write = MagicMock(side_effect=lambda s: written_contents.append(s))
            return handle

        with patch.object(bm.os, "makedirs"), \
             patch("builtins.open", side_effect=capturing_open), \
             patch.object(bm.time, "sleep"), \
             patch.object(bm.time, "time", return_value=1700000000):
            bm.main()

        for content in written_contents:
            assert "Continuous Codebase Improvement" in content

    def test_task_description_contains_autonomous_network_operating_center(self, tmp_path):
        """Task description contains the project name 'Autonomous Network Operating Center'."""
        import bulk_improve as bm

        written_contents = []

        def capturing_open(path, mode="r"):
            handle = MagicMock()
            handle.__enter__ = lambda s: s
            handle.__exit__ = MagicMock(return_value=False)
            handle.write = MagicMock(side_effect=lambda s: written_contents.append(s))
            return handle

        with patch.object(bm.os, "makedirs"), \
             patch("builtins.open", side_effect=capturing_open), \
             patch.object(bm.time, "sleep"), \
             patch.object(bm.time, "time", return_value=1700000000):
            bm.main()

        for content in written_contents:
            assert "Autonomous Network Operating Center" in content

    def test_print_queued_task_message_format(self, tmp_path):
        """main() prints 'Queued task X/100' for each iteration."""
        import bulk_improve as bm

        printed = []

        with patch.object(bm.os, "makedirs"), \
             patch("builtins.open", unittest.mock.mock_open()), \
             patch.object(bm.time, "sleep"), \
             patch.object(bm.time, "time", return_value=1700000000), \
             patch("builtins.print", side_effect=lambda msg: printed.append(msg)):
            bm.main()

        assert len(printed) == 100
        # First message should be "Queued task 1/100"
        assert "1/100" in printed[0]
        # Last message should be "Queued task 100/100"
        assert "100/100" in printed[-1]

    def test_filenames_contain_timestamp_from_time_time(self, tmp_path):
        """Filenames embed the integer value from time.time()."""
        import bulk_improve as bm

        opened_paths = []
        fixed_timestamp = 1700000000

        def capturing_open(path, mode="r"):
            opened_paths.append(path)
            handle = MagicMock()
            handle.__enter__ = lambda s: s
            handle.__exit__ = MagicMock(return_value=False)
            handle.write = MagicMock()
            return handle

        with patch.object(bm.os, "makedirs"), \
             patch("builtins.open", side_effect=capturing_open), \
             patch.object(bm.time, "sleep"), \
             patch.object(bm.time, "time", return_value=fixed_timestamp):
            bm.main()

        for path in opened_paths:
            assert str(fixed_timestamp) in path

    def test_loop_indices_are_zero_based(self, tmp_path):
        """File indices run from 0 to 99 (zero-based), covering all 100 files."""
        import bulk_improve as bm

        opened_paths = []

        def capturing_open(path, mode="r"):
            opened_paths.append(os.path.basename(path))
            handle = MagicMock()
            handle.__enter__ = lambda s: s
            handle.__exit__ = MagicMock(return_value=False)
            handle.write = MagicMock()
            return handle

        with patch.object(bm.os, "makedirs"), \
             patch("builtins.open", side_effect=capturing_open), \
             patch.object(bm.time, "sleep"), \
             patch.object(bm.time, "time", return_value=1700000000):
            bm.main()

        # Check that index 0 and index 99 are present (zero-based)
        assert any("_0.txt" in p for p in opened_paths)
        assert any("_99.txt" in p for p in opened_paths)
        # Index 100 should NOT be present (range is 0-99)
        assert not any("_100.txt" in p for p in opened_paths)

    def test_main_uses_os_path_join_for_file_path(self, tmp_path):
        """main() uses os.path.join to construct file paths."""
        import bulk_improve as bm

        join_calls = []
        real_join = os.path.join

        def capturing_join(*args):
            join_calls.append(args)
            return real_join(*args)

        with patch.object(bm.os, "makedirs"), \
             patch("builtins.open", unittest.mock.mock_open()), \
             patch.object(bm.time, "sleep"), \
             patch.object(bm.time, "time", return_value=1700000000), \
             patch.object(bm.os.path, "join", side_effect=capturing_join):
            bm.main()

        # os.path.join should have been called 100 times with "nanoc/inbox" as first arg
        assert len(join_calls) == 100
        for call_args in join_calls:
            assert call_args[0] == "nanoc/inbox"


# ===========================================================================
# nanoc/memory/memory.py – create_task additional edge cases
# ===========================================================================

class TestMemoryCreateTaskEdgeCases:
    """Additional tests for create_task after priority param was removed."""

    def test_create_task_stores_parent_id(self, memory):
        """create_task correctly stores a parent_id when provided."""
        parent_id = memory.create_task("Parent task")
        child_id = memory.create_task("Child task", parent_id=parent_id)

        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT parent_id FROM tasks WHERE id = ?", (child_id,))
            row = cursor.fetchone()

        assert row[0] == parent_id

    def test_create_task_null_assigned_to_stored_as_none(self, memory):
        """When assigned_to is None, it is stored as NULL in the database."""
        task_id = memory.create_task("Unassigned task", assigned_to=None)

        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT assigned_to FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()

        assert row[0] is None

    def test_create_task_null_project_id_stored_as_none(self, memory):
        """When project_id is None, it is stored as NULL in the database."""
        task_id = memory.create_task("No project task", project_id=None)

        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT project_id FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()

        assert row[0] is None

    def test_create_task_null_parent_id_stored_as_none(self, memory):
        """When parent_id is None, it is stored as NULL in the database."""
        task_id = memory.create_task("No parent task", parent_id=None)

        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT parent_id FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()

        assert row[0] is None

    def test_create_task_many_tasks_have_unique_ids(self, memory):
        """Multiple create_task calls produce unique IDs."""
        ids = [memory.create_task(f"Task {i}") for i in range(10)]
        assert len(set(ids)) == 10, "All task IDs should be unique"

    def test_create_task_result_is_positive_integer(self, memory):
        """create_task always returns a positive integer (SQLite ROWID)."""
        for i in range(5):
            task_id = memory.create_task(f"Task {i}")
            assert isinstance(task_id, int)
            assert task_id > 0

    def test_create_task_timestamps_are_set(self, memory):
        """create_task sets created_at and updated_at timestamps."""
        task_id = memory.create_task("Timestamped task")

        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT created_at, updated_at FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()

        assert row[0] is not None
        assert row[1] is not None

    def test_create_task_signature_has_four_params(self, memory):
        """create_task has exactly 4 parameters: description, assigned_to, parent_id, project_id."""
        sig = inspect.signature(memory.create_task)
        params = list(sig.parameters.keys())
        assert "description" in params
        assert "assigned_to" in params
        assert "parent_id" in params
        assert "project_id" in params
        # priority was removed in this PR
        assert "priority" not in params

    def test_tasks_table_priority_column_default_zero(self, memory):
        """The priority column in tasks table has a DEFAULT of 0."""
        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(tasks)")
            cols = {row[1]: {"dflt_value": row[4]} for row in cursor.fetchall()}

        assert "priority" in cols
        # The default value should be '0' as a string (how SQLite PRAGMA reports it)
        assert cols["priority"]["dflt_value"] == "0"

    def test_create_task_no_migration_block_in_init_db(self, memory):
        """_init_db no longer contains the ALTER TABLE migration for priority column."""
        src = inspect.getsource(Memory._init_db)
        assert "ALTER TABLE tasks ADD COLUMN priority" not in src


# ===========================================================================
# nanoc/agents/analyst.py – known incompatibility with memory.create_task
# ===========================================================================

class TestAnalystPriorityIncompatibility:
    """Tests exposing the incompatibility between analyst.py (priority=10) and memory.py (no priority param)."""

    @pytest.mark.asyncio
    async def test_analyze_failure_with_real_memory_raises_type_error(self, memory):
        """
        analyze_failure passes priority=10 to memory.create_task, but create_task no longer
        accepts a priority parameter. This should raise TypeError with real Memory.
        """
        from nanoc.agents.analyst import Analyst

        analyst = Analyst("Analyst1", memory)
        analyst.llm = MockLLM()

        with pytest.raises(TypeError):
            await analyst.analyze_failure({
                "project_id": "proj_test",
                "error": "TestError"
            })

    @pytest.mark.asyncio
    async def test_analyze_failure_task_description_prefix_is_fix(self, memory):
        """The fix task created by analyze_failure starts with 'FIX:'."""
        from nanoc.agents.analyst import Analyst

        analyst = Analyst("Analyst1", memory)
        analyst.llm = MockLLM()

        with patch.object(memory, "create_task", return_value=1) as mock_create:
            await analyst.analyze_failure({
                "project_id": "proj_prefix",
                "error": "Something broke"
            })

        description = mock_create.call_args[0][0]
        assert description.startswith("FIX:")

    @pytest.mark.asyncio
    async def test_analyze_failure_event_strategy_is_llm_response(self, memory):
        """The 'analysis/completed' event's 'strategy' field contains the LLM response."""
        from nanoc.agents.analyst import Analyst

        mock_llm = MockLLM()
        mock_llm.default_response = "Apply null check before dereferencing"

        analyst = Analyst("Analyst1", memory)
        analyst.llm = mock_llm

        with patch.object(memory, "create_task", return_value=1):
            await analyst.analyze_failure({
                "project_id": "proj_strategy",
                "error": "NPE"
            })

        events = memory.get_events(topic="analysis/completed")
        assert len(events) >= 1
        payload = json.loads(events[-1]["payload"])
        assert "strategy" in payload
        assert "Apply null check" in payload["strategy"]

    @pytest.mark.asyncio
    async def test_analyze_failure_unknown_project_id_defaults_to_unknown(self, memory):
        """When failure_event has no 'project_id', analyst uses 'unknown' as project_id."""
        from nanoc.agents.analyst import Analyst

        analyst = Analyst("Analyst1", memory)
        analyst.llm = MockLLM()

        with patch.object(memory, "create_task") as mock_create:
            mock_create.return_value = 1
            await analyst.analyze_failure({"error": "SomeError"})

        call_kwargs = mock_create.call_args
        project_id = call_kwargs.kwargs.get("project_id") or (
            call_kwargs.args[3] if len(call_kwargs.args) > 3 else None
        )
        assert project_id == "unknown"


# ===========================================================================
# nanoc/agents/base.py – TeamLeader project_id format (no hex suffix)
# ===========================================================================

class TestTeamLeaderProjectIdFormatEdgeCases:
    """Edge-case tests for the simplified project_id format in TeamLeader."""

    @pytest.mark.asyncio
    async def test_description_without_colon_generates_new_project_id(self, memory):
        """delegate_tasks with no ':' in description always generates a new proj_ ID."""
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("L1", "Team Leader", memory, MockLLM())
        project_id = await leader.delegate_tasks("No colon here at all")

        assert project_id.startswith("proj_")
        suffix = project_id[len("proj_"):]
        assert suffix.isdigit()

    @pytest.mark.asyncio
    async def test_description_with_non_proj_prefix_generates_new_project_id(self, memory):
        """When ':' is present but prefix is not 'proj_', a new ID is generated."""
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("L2", "Team Leader", memory, MockLLM())
        project_id = await leader.delegate_tasks("task: do this thing")

        # Should NOT use "task" as project_id
        assert project_id.startswith("proj_")
        # Should be a timestamp-based ID
        suffix = project_id[len("proj_"):]
        assert suffix.isdigit()

    @pytest.mark.asyncio
    async def test_project_id_has_only_two_parts(self, memory):
        """Generated project_id is 'proj_<timestamp>' with exactly 2 underscore-split parts."""
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("L3", "Team Leader", memory, MockLLM())
        project_id = await leader.delegate_tasks("Build something new")

        parts = project_id.split("_")
        assert len(parts) == 2, f"Expected proj_<timestamp>, got {project_id!r}"
        assert parts[0] == "proj"
        assert parts[1].isdigit()

    @pytest.mark.asyncio
    async def test_project_id_no_hex_suffix(self, memory):
        """Generated project_id never contains a hex-style random suffix."""
        from nanoc.agents.base import TeamLeader

        import re
        hex_pattern = re.compile(r"_[0-9a-f]{8}$")

        leader = TeamLeader("L4", "Team Leader", memory, MockLLM())
        project_id = await leader.delegate_tasks("Test project")

        assert not hex_pattern.search(project_id), \
            f"project_id {project_id!r} should not have hex suffix"

    @pytest.mark.asyncio
    async def test_delegate_tasks_adds_project_to_active_projects(self, memory):
        """delegate_tasks adds the new project_id to the 'active_projects' knowledge list."""
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("L5", "Team Leader", memory, MockLLM())
        project_id = await leader.delegate_tasks("Some project")

        active = memory.get_knowledge("active_projects")
        assert active is not None
        assert project_id in active

    @pytest.mark.asyncio
    async def test_delegate_tasks_stores_architecture_in_knowledge(self, memory):
        """delegate_tasks stores the LLM architecture response in the knowledge base."""
        from nanoc.agents.base import TeamLeader

        mock_llm = MockLLM()
        mock_llm.default_response = "Microservices-based design"
        leader = TeamLeader("L6", "Team Leader", memory, mock_llm)
        project_id = await leader.delegate_tasks("Design system")

        arch = memory.get_knowledge(f"project_{project_id}_arch")
        assert arch is not None

    @pytest.mark.asyncio
    async def test_delegate_tasks_creates_architect_task(self, memory):
        """delegate_tasks creates a task assigned to 'Architect' for the project."""
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("L7", "Team Leader", memory, MockLLM())
        project_id = await leader.delegate_tasks("Need architecture")

        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT assigned_to FROM tasks WHERE project_id = ?",
                (project_id,)
            )
            rows = cursor.fetchall()

        assigned_roles = [r[0] for r in rows]
        assert "Architect" in assigned_roles

    @pytest.mark.asyncio
    async def test_delegate_tasks_returns_string(self, memory):
        """delegate_tasks always returns a string (the project_id)."""
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("L8", "Team Leader", memory, MockLLM())
        result = await leader.delegate_tasks("Any project")

        assert isinstance(result, str)

    def test_architect_has_design_solution_method(self, memory):
        """Architect class still has design_solution method (not replaced by handle_task)."""
        from nanoc.agents.base import Architect

        arch = Architect("A1", "Architect", memory, MockLLM())
        assert hasattr(arch, "design_solution")
        assert asyncio.iscoroutinefunction(arch.design_solution)

    def test_planner_has_create_todo_list_method(self, memory):
        """Planner class still has create_todo_list method."""
        from nanoc.agents.base import Planner

        planner = Planner("P1", "Planner", memory, MockLLM())
        assert hasattr(planner, "create_todo_list")
        assert asyncio.iscoroutinefunction(planner.create_todo_list)

    def test_coder_has_write_code_method(self, memory):
        """Coder class still has write_code method."""
        from nanoc.agents.base import Coder

        coder = Coder("C1", "Coder", memory, MockLLM())
        assert hasattr(coder, "write_code")
        assert asyncio.iscoroutinefunction(coder.write_code)

    def test_reviewer_has_review_work_method(self, memory):
        """Reviewer class still has review_work method."""
        from nanoc.agents.base import Reviewer

        reviewer = Reviewer("R1", "Reviewer", memory, MockLLM())
        assert hasattr(reviewer, "review_work")
        assert asyncio.iscoroutinefunction(reviewer.review_work)


# ===========================================================================
# nanoc/agents/security.py – simplified event payload
# ===========================================================================

class TestSecurityAgentEventPayload:
    """Additional tests for the simplified security/audit-complete event."""

    @pytest.mark.asyncio
    async def test_event_target_field_matches_audit_target(self, memory):
        """The 'target' field in the event payload matches the target passed to audit_service."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("Sec1", memory)
        agent.llm = MockLLM()

        nmap_result = {"stdout": "22/tcp open ssh", "stderr": "", "returncode": 0}

        with patch("nanoc.tools.network.AsyncRunner.run_command",
                   new_callable=AsyncMock, return_value=nmap_result):
            await agent.audit_service("192.168.10.5")

        events = memory.get_events(topic="security/audit-complete")
        assert len(events) >= 1
        payload = json.loads(events[-1]["payload"])
        assert payload["target"] == "192.168.10.5"

    @pytest.mark.asyncio
    async def test_no_findings_even_with_ssh_protocol_v1_in_output(self, memory):
        """Even if nmap output mentions SSH protocol 1.0, no 'findings' field is added."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("Sec1", memory)
        agent.llm = MockLLM()

        nmap_result = {
            "stdout": "22/tcp open ssh OpenSSH protocol 1.0",
            "stderr": "",
            "returncode": 0
        }

        with patch("nanoc.tools.network.AsyncRunner.run_command",
                   new_callable=AsyncMock, return_value=nmap_result):
            await agent.audit_service("10.0.0.1")

        events = memory.get_events(topic="security/audit-complete")
        payload = json.loads(events[-1]["payload"])
        assert "findings" not in payload

    @pytest.mark.asyncio
    async def test_no_vulnerabilities_even_with_expired_ssl_in_output(self, memory):
        """Even if nmap output mentions expired SSL, no 'vulnerabilities' field is added."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("Sec1", memory)
        agent.llm = MockLLM()

        nmap_result = {
            "stdout": "expired SSL certificate detected on port 443",
            "stderr": "",
            "returncode": 0
        }

        with patch("nanoc.tools.network.AsyncRunner.run_command",
                   new_callable=AsyncMock, return_value=nmap_result):
            await agent.audit_service("10.0.0.2")

        events = memory.get_events(topic="security/audit-complete")
        payload = json.loads(events[-1]["payload"])
        assert "vulnerabilities" not in payload

    @pytest.mark.asyncio
    async def test_no_event_published_when_error_key_present(self, memory):
        """audit_service does NOT publish the event when nmap returns an error dict."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("Sec1", memory)
        agent.llm = MockLLM()

        error_result = {"error": "nmap not found", "returncode": 1, "stdout": "", "stderr": ""}

        with patch("nanoc.tools.network.AsyncRunner.run_command",
                   new_callable=AsyncMock, return_value=error_result):
            await agent.audit_service("10.0.0.3")

        events = memory.get_events(topic="security/audit-complete")
        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_audit_service_returns_error_dict_on_failure(self, memory):
        """audit_service returns the error dict directly when nmap fails."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("Sec1", memory)
        agent.llm = MockLLM()

        error_result = {"error": "permission denied", "returncode": 1, "stdout": "", "stderr": ""}

        with patch("nanoc.tools.network.AsyncRunner.run_command",
                   new_callable=AsyncMock, return_value=error_result):
            result = await agent.audit_service("10.0.0.4")

        assert result == error_result

    @pytest.mark.asyncio
    async def test_audit_service_returns_stdout_string_on_success(self, memory):
        """audit_service returns the nmap stdout string on success."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("Sec1", memory)
        agent.llm = MockLLM()

        scan_output = "Nmap done: 1 IP address (1 host up)"
        nmap_result = {"stdout": scan_output, "stderr": "", "returncode": 0}

        with patch("nanoc.tools.network.AsyncRunner.run_command",
                   new_callable=AsyncMock, return_value=nmap_result):
            result = await agent.audit_service("127.0.0.1")

        assert result == scan_output

    def test_security_agent_role_attribute(self, memory):
        """SecurityAgent initializes with role='Security'."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("SecAgent", memory)
        assert agent.role == "Security"

    def test_security_agent_vulnerability_analysis_code_removed(self):
        """The vulnerability analysis code (SSH/FTP/SSL checks) is no longer in audit_service."""
        from nanoc.agents import security
        src = inspect.getsource(security.SecurityAgent.audit_service)
        # Old code had these specific vulnerability-detection strings
        assert "protocol 1.0" not in src
        assert "anonymous" not in src
        assert "expired" not in src
        # Old code used 'vulnerabilities.append(...)' pattern
        assert "vulnerabilities.append" not in src


# ===========================================================================
# nanoc/core/llm.py – no retry, single attempt
# ===========================================================================

class TestLLMProviderSingleAttempt:
    """Tests verifying that the retry loop was removed from LLMProvider.complete()."""

    @pytest.mark.asyncio
    async def test_complete_calls_openrouter_exactly_once_on_success(self, memory):
        """complete() calls _openrouter_complete exactly once when it succeeds."""
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="openrouter", model="test-model")
        call_count = [0]

        async def mock_complete(prompt, system_prompt, model):
            call_count[0] += 1
            return "OK response"

        with patch.object(provider, "_openrouter_complete", side_effect=mock_complete), \
             patch.object(provider, "_record_telemetry"), \
             patch("nanoc.core.llm.Memory", return_value=memory):
            result = await provider.complete("test")

        assert call_count[0] == 1
        assert result == "OK response"

    @pytest.mark.asyncio
    async def test_complete_calls_ollama_for_ollama_provider(self, memory):
        """complete() dispatches to _ollama_complete when provider='ollama'."""
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="ollama", model="llama3")
        ollama_calls = [0]
        openrouter_calls = [0]

        async def mock_ollama(prompt, system_prompt, model):
            ollama_calls[0] += 1
            return "ollama response"

        async def mock_openrouter(prompt, system_prompt, model):
            openrouter_calls[0] += 1
            return "openrouter response"

        with patch.object(provider, "_ollama_complete", side_effect=mock_ollama), \
             patch.object(provider, "_openrouter_complete", side_effect=mock_openrouter), \
             patch.object(provider, "_record_telemetry"), \
             patch("nanoc.core.llm.Memory", return_value=memory):
            await provider.complete("hello ollama")

        assert ollama_calls[0] == 1
        assert openrouter_calls[0] == 0

    @pytest.mark.asyncio
    async def test_complete_does_not_call_openrouter_for_ollama(self, memory):
        """complete() with ollama provider never calls _openrouter_complete."""
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="ollama", model="llama3")

        async def mock_ollama(prompt, system_prompt, model):
            return "response"

        openrouter_called = [False]

        async def mock_openrouter(prompt, system_prompt, model):
            openrouter_called[0] = True
            return "should not be called"

        with patch.object(provider, "_ollama_complete", side_effect=mock_ollama), \
             patch.object(provider, "_openrouter_complete", side_effect=mock_openrouter), \
             patch.object(provider, "_record_telemetry"), \
             patch("nanoc.core.llm.Memory", return_value=memory):
            await provider.complete("test")

        assert not openrouter_called[0]

    @pytest.mark.asyncio
    async def test_complete_records_error_with_exception_message(self, memory):
        """_record_error is called with the string representation of the exception."""
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="openrouter", model="test")
        recorded = []

        async def mock_complete(prompt, system_prompt, model):
            raise RuntimeError("Connection refused by server")

        with patch.object(provider, "_openrouter_complete", side_effect=mock_complete), \
             patch.object(provider, "_record_error", side_effect=recorded.append), \
             patch("nanoc.core.llm.Memory", return_value=memory):
            with pytest.raises(RuntimeError):
                await provider.complete("test")

        assert len(recorded) == 1
        assert "Connection refused by server" in recorded[0]

    @pytest.mark.asyncio
    async def test_complete_re_raises_exception_after_recording(self, memory):
        """complete() re-raises the exception after calling _record_error."""
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="openrouter", model="test")

        class CustomError(Exception):
            pass

        async def mock_complete(prompt, system_prompt, model):
            raise CustomError("custom failure")

        with patch.object(provider, "_openrouter_complete", side_effect=mock_complete), \
             patch.object(provider, "_record_error"), \
             patch("nanoc.core.llm.Memory", return_value=memory):
            with pytest.raises(CustomError, match="custom failure"):
                await provider.complete("test")

    @pytest.mark.asyncio
    async def test_complete_records_telemetry_with_duration(self, memory):
        """_record_telemetry is called with a positive duration in milliseconds."""
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="openrouter", model="test")
        telemetry_args = []

        async def mock_complete(prompt, system_prompt, model):
            return "response text"

        with patch.object(provider, "_openrouter_complete", side_effect=mock_complete), \
             patch.object(provider, "_record_telemetry",
                          side_effect=lambda p, r, d: telemetry_args.append(d)), \
             patch("nanoc.core.llm.Memory", return_value=memory):
            await provider.complete("test prompt")

        assert len(telemetry_args) == 1
        # Duration should be a positive float (milliseconds)
        assert telemetry_args[0] >= 0

    def test_complete_source_has_no_retry_loop_variables(self):
        """LLMProvider.complete source code does not contain retry loop variables."""
        from nanoc.core.llm import LLMProvider

        src = inspect.getsource(LLMProvider.complete)
        assert "max_retries" not in src
        assert "retry_delay" not in src
        assert "for attempt" not in src

    @pytest.mark.asyncio
    async def test_complete_unknown_provider_raises_value_error(self, memory):
        """complete() raises ValueError for an unknown provider string."""
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="unknown_xyz", model="any-model")

        with patch.object(provider, "_record_error"), \
             patch("nanoc.core.llm.Memory", return_value=memory):
            with pytest.raises(ValueError, match="Unknown provider"):
                await provider.complete("test")

    @pytest.mark.asyncio
    async def test_complete_uses_model_override_from_knowledge(self, memory):
        """complete() uses model from knowledge base override when set."""
        from nanoc.core.llm import LLMProvider

        memory.upsert_knowledge("system/model_override", "override-model-v2")

        provider = LLMProvider(provider="openrouter", model="original-model")
        used_models = []

        async def mock_complete(prompt, system_prompt, model):
            used_models.append(model)
            return "response"

        with patch.object(provider, "_openrouter_complete", side_effect=mock_complete), \
             patch.object(provider, "_record_telemetry"), \
             patch("nanoc.core.llm.Memory", return_value=memory):
            await provider.complete("test")

        assert len(used_models) == 1
        assert used_models[0] == "override-model-v2"


# ===========================================================================
# nanoc/core/orchestrator.py – explicit role dispatch edge cases
# ===========================================================================

class TestOrchestratorDispatchEdgeCases:
    """Additional tests for the explicit role dispatch logic in Orchestrator.process_task()."""

    @pytest.mark.asyncio
    async def test_reviewer_fix_task_description_references_original_task(self, memory):
        """When review fails, the fix task description contains the original task description."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_reviewer = MagicMock()
        mock_reviewer.role = "Reviewer"
        mock_reviewer.log = AsyncMock()
        mock_reviewer.review_work = AsyncMock(
            return_value="STATUS: FAILED\nMissing error handling."
        )
        orch.add_agent(mock_reviewer)

        original_description = "Review this specific code: def foo(): pass"
        task_id = memory.create_task(
            original_description, assigned_to="Reviewer", project_id="proj_desc_test"
        )

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orch.process_task(task)

        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT description FROM tasks WHERE assigned_to = 'Coder'"
            )
            row = cursor.fetchone()

        assert row is not None
        # Fix task description should include the original task description
        assert original_description in row[0]

    @pytest.mark.asyncio
    async def test_reviewer_fix_task_mentions_fix_flaws(self, memory):
        """The fix task description starts with 'Fix flaws'."""
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
            "Review it", assigned_to="Reviewer", project_id="proj_flaws"
        )

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orch.process_task(task)

        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT description FROM tasks WHERE assigned_to = 'Coder'")
            row = cursor.fetchone()

        assert row is not None
        assert row[0].startswith("Fix flaws")

    @pytest.mark.asyncio
    async def test_no_agent_for_role_does_not_raise(self, memory):
        """process_task does not raise when no agent is registered for the task's role."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)
        # No agents registered

        task_id = memory.create_task("Do something", assigned_to="NonExistentRole")

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        # Should complete without raising
        await orch.process_task(task)

    @pytest.mark.asyncio
    async def test_process_task_marks_completed_for_coder(self, memory):
        """Coder task is marked 'completed' after write_code succeeds."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_coder = MagicMock()
        mock_coder.role = "Coder"
        mock_coder.log = AsyncMock()
        mock_coder.write_code = AsyncMock(return_value="# generated code")
        orch.add_agent(mock_coder)

        task_id = memory.create_task("Write code", assigned_to="Coder", project_id="proj_done")

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

    @pytest.mark.asyncio
    async def test_process_task_marks_failed_after_max_retries_exceeded(self, memory):
        """process_task marks task as 'failed' when retry_count >= max_retries."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_coder = MagicMock()
        mock_coder.role = "Coder"
        mock_coder.log = AsyncMock()
        mock_coder.write_code = AsyncMock(side_effect=RuntimeError("Failed hard"))
        orch.add_agent(mock_coder)

        task_id = memory.create_task("Failing task", assigned_to="Coder", project_id="proj_fail")

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        # Simulate being at max_retries already
        task["retry_count"] = 3
        task["max_retries"] = 3

        await orch.process_task(task)

        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()

        assert row[0] == "failed"

    @pytest.mark.asyncio
    async def test_process_task_sets_pending_on_first_retry(self, memory):
        """process_task sets status to 'pending' (not 'failed') on first error."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_planner = MagicMock()
        mock_planner.role = "Planner"
        mock_planner.log = AsyncMock()
        mock_planner.create_todo_list = AsyncMock(side_effect=RuntimeError("Transient error"))
        orch.add_agent(mock_planner)

        task_id = memory.create_task("Plan something", assigned_to="Planner", project_id="proj_retry")

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        # First attempt (retry_count=0, max_retries=3)
        task["retry_count"] = 0
        task["max_retries"] = 3

        await orch.process_task(task)

        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status, retry_count FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()

        # Should be 'pending' (not 'failed') since retry_count(1) <= max_retries(3)
        assert row[0] == "pending"
        assert row[1] == 1

    @pytest.mark.asyncio
    async def test_unknown_role_uses_think_as_fallback(self, memory):
        """process_task calls agent.think() for roles not in the explicit dispatch."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_agent = MagicMock()
        mock_agent.role = "SpecialRole"
        mock_agent.log = AsyncMock()
        mock_agent.think = AsyncMock(return_value="thought about it")
        orch.add_agent(mock_agent)

        task_id = memory.create_task(
            "Do special thing", assigned_to="SpecialRole", project_id="proj_special"
        )

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orch.process_task(task)

        mock_agent.think.assert_called_once()
        prompt_arg = mock_agent.think.call_args[0][0]
        assert "Execute this task:" in prompt_arg
        assert "Do special thing" in prompt_arg

    @pytest.mark.asyncio
    async def test_task_failed_event_published_on_permanent_failure(self, memory):
        """process_task publishes 'task/failed' event when task permanently fails."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_coder = MagicMock()
        mock_coder.role = "Coder"
        mock_coder.log = AsyncMock()
        mock_coder.write_code = AsyncMock(side_effect=RuntimeError("Fatal error"))
        orch.add_agent(mock_coder)

        task_id = memory.create_task("Write failing code", assigned_to="Coder", project_id="proj_ev")

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        # At max retries
        task["retry_count"] = 3
        task["max_retries"] = 3

        await orch.process_task(task)

        events = memory.get_events(topic="task/failed")
        assert len(events) >= 1
        payload = json.loads(events[-1]["payload"])
        assert payload["task_id"] == task_id


# ===========================================================================
# nanoc/tools/network.py – _get_fallback_topology additional tests
# ===========================================================================

class TestFallbackTopologyAdditional:
    """Additional tests for the simplified _get_fallback_topology method."""

    def test_fallback_topology_returns_dict(self, memory):
        """_get_fallback_topology returns a dict."""
        from nanoc.tools.network import DiscoveryTool

        result = DiscoveryTool._get_fallback_topology(memory)
        assert isinstance(result, dict)

    def test_fallback_topology_nodes_key_is_a_list(self, memory):
        """The 'nodes' value in fallback topology is a list."""
        from nanoc.tools.network import DiscoveryTool

        result = DiscoveryTool._get_fallback_topology(memory)
        assert isinstance(result["nodes"], list)

    def test_fallback_topology_edges_key_is_a_list(self, memory):
        """The 'edges' value in fallback topology is a list."""
        from nanoc.tools.network import DiscoveryTool

        result = DiscoveryTool._get_fallback_topology(memory)
        assert isinstance(result["edges"], list)

    def test_fallback_topology_has_exactly_one_node(self, memory):
        """_get_fallback_topology returns exactly 1 node (localhost only)."""
        from nanoc.tools.network import DiscoveryTool

        result = DiscoveryTool._get_fallback_topology(memory)
        assert len(result["nodes"]) == 1

    def test_fallback_topology_has_zero_edges(self, memory):
        """_get_fallback_topology returns 0 edges."""
        from nanoc.tools.network import DiscoveryTool

        result = DiscoveryTool._get_fallback_topology(memory)
        assert len(result["edges"]) == 0

    def test_fallback_topology_localhost_node_has_all_required_fields(self, memory):
        """The localhost node has all required fields: id, label, type, status."""
        from nanoc.tools.network import DiscoveryTool

        result = DiscoveryTool._get_fallback_topology(memory)
        node = result["nodes"][0]

        assert "id" in node
        assert "label" in node
        assert "type" in node
        assert "status" in node

    def test_fallback_topology_stored_in_knowledge_base(self, memory):
        """After calling _get_fallback_topology, the topology is cached in knowledge base."""
        from nanoc.tools.network import DiscoveryTool

        DiscoveryTool._get_fallback_topology(memory)

        cached = memory.get_knowledge("network_topology")
        assert cached is not None
        assert len(cached["nodes"]) == 1
        assert cached["nodes"][0]["id"] == "127.0.0.1"

    def test_fallback_topology_no_core_router_node(self, memory):
        """_get_fallback_topology does not include a 'Core Router' or similar dummy node."""
        from nanoc.tools.network import DiscoveryTool

        result = DiscoveryTool._get_fallback_topology(memory)

        for node in result["nodes"]:
            label = node.get("label", "").lower()
            assert "router" not in label
            assert "core" not in label
            assert "dummy" not in label

    @pytest.mark.asyncio
    async def test_discover_topology_returns_cached_topology_on_second_call(self, memory):
        """discover_topology returns the cached topology (from knowledge base) on second call."""
        from nanoc.tools.network import DiscoveryTool

        error_result = {"error": "nmap unavailable", "returncode": 1, "stdout": "", "stderr": ""}

        with patch("nanoc.tools.network.settings") as mock_settings, \
             patch("nanoc.tools.network.Memory", return_value=memory), \
             patch("nanoc.tools.network.NetworkScanner.scan_local_network",
                   new_callable=AsyncMock, return_value=error_result):
            mock_settings.DB_PATH = memory.db_path
            topology1 = await DiscoveryTool.discover_topology("192.168.1.0/24")

        # Second call – should use cache, not call nmap again
        with patch("nanoc.tools.network.settings") as mock_settings, \
             patch("nanoc.tools.network.Memory", return_value=memory), \
             patch("nanoc.tools.network.NetworkScanner.scan_local_network",
                   new_callable=AsyncMock) as mock_scan:
            mock_settings.DB_PATH = memory.db_path
            topology2 = await DiscoveryTool.discover_topology("192.168.1.0/24")

        # Second call should NOT have triggered a new scan
        mock_scan.assert_not_called()
        # Both should return the same topology
        assert topology1 == topology2

    def test_fallback_topology_source_has_no_core_router_code(self):
        """The _get_fallback_topology source no longer contains 'Core Router' node definition."""
        from nanoc.tools.network import DiscoveryTool

        src = inspect.getsource(DiscoveryTool._get_fallback_topology)
        # Old code created a hardcoded Core Router node; this should be gone
        assert "Core Router" not in src


# ===========================================================================
# Integration: analyst.py + memory.py incompatibility
# ===========================================================================

class TestAnalystMemoryIntegration:
    """Integration tests showing the interaction between analyst.py and memory.py changes."""

    @pytest.mark.asyncio
    async def test_analyze_failure_mocked_memory_records_fix_prefix(self, memory):
        """With mocked create_task, analyze_failure's fix description starts with 'FIX:'."""
        from nanoc.agents.analyst import Analyst

        analyst = Analyst("Analyst1", memory)
        analyst.llm = MockLLM()

        descriptions_created = []

        def mock_create(desc, **kwargs):
            descriptions_created.append(desc)
            return 1

        with patch.object(memory, "create_task", side_effect=mock_create):
            await analyst.analyze_failure({
                "project_id": "proj_int",
                "error": "ImportError: no module named foo"
            })

        assert len(descriptions_created) == 1
        assert descriptions_created[0].startswith("FIX:")

    @pytest.mark.asyncio
    async def test_analyze_failure_error_included_in_llm_prompt(self, memory):
        """analyze_failure includes the error message in the LLM prompt."""
        from nanoc.agents.analyst import Analyst

        mock_llm = MockLLM()
        analyst = Analyst("Analyst1", memory)
        analyst.llm = mock_llm

        with patch.object(memory, "create_task", return_value=1):
            await analyst.analyze_failure({
                "project_id": "proj_prompt",
                "error": "UniqueErrorString12345"
            })

        # Find the call that contains the error string
        prompts = [call["prompt"] for call in mock_llm.calls]
        assert any("UniqueErrorString12345" in p for p in prompts)


# ===========================================================================
# Regression: base.py os import removal
# ===========================================================================

class TestBaseAgentOsImportRemoved:
    """Regression test verifying that the 'import os' was removed from base.py."""

    def test_base_module_does_not_import_os_at_top_level(self):
        """base.py no longer imports 'os' module at the top level."""
        import nanoc.agents.base as base_module

        src = inspect.getsource(base_module)
        # Find only module-level imports (before any class/def)
        lines = src.split("\n")
        top_level_imports = []
        for line in lines:
            if line.startswith("import ") or line.startswith("from "):
                top_level_imports.append(line)
            elif line.startswith("class ") or line.startswith("def "):
                break

        assert not any("import os" in imp for imp in top_level_imports), \
            "base.py should not have 'import os' at module level"

    def test_team_leader_project_id_uses_datetime_not_os_urandom(self):
        """TeamLeader.delegate_tasks generates project_id via datetime, not os.urandom."""
        from nanoc.agents.base import TeamLeader

        src = inspect.getsource(TeamLeader.delegate_tasks)
        assert "os.urandom" not in src
        assert "datetime.now" in src or "datetime" in src
