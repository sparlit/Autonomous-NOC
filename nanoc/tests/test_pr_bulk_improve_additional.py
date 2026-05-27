"""
Additional comprehensive tests for the bulk_improve PR changes.

These tests supplement test_pr_bulk_improve_changes.py and cover gaps including:
  - bulk_improve.py: task description content, file open mode, print output
  - memory.create_task: parent_id, all-params, no priority in SQL INSERT
  - analyst.analyze_failure: event payload structure, call chain with mocked memory
  - base.TeamLeader: incoming-job event payload (no 'leader'), active_projects knowledge
  - security.SecurityAgent: init attributes, empty-stdout path
  - llm.LLMProvider: model override from knowledge base, ollama provider path
  - orchestrator.Orchestrator: result stored in DB on success, retry_count incremented on failure
  - network.DiscoveryTool: cache hit path, XML parse-failure fallback, nonzero returncode fallback
"""
import asyncio
import inspect
import json
import os
import sqlite3
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
    db_path = str(tmp_path / "test_additional_pr.db")
    mem = Memory(db_path)
    yield mem
    if os.path.exists(db_path):
        os.remove(db_path)


# ===========================================================================
# bulk_improve.py – additional coverage
# ===========================================================================

class TestBulkImproveTaskDescriptionContent:
    """Verify specific content of the task description written by main()."""

    def _run_main_capture_writes(self):
        """Helper: run bulk_improve.main() and capture all written content."""
        import bulk_improve as bm

        written_data = []

        def fake_makedirs(path, exist_ok=False):
            pass

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

        return written_data

    def test_task_description_mentions_autonomous_noc(self):
        """Task description references 'Autonomous Network Operating Center'."""
        written_data = self._run_main_capture_writes()
        assert len(written_data) == 100
        for content in written_data:
            assert "Autonomous Network Operating Center" in content

    def test_task_description_mentions_multi_thread(self):
        """Task description mentions 'Multi Thread Parallel Processing'."""
        written_data = self._run_main_capture_writes()
        for content in written_data:
            assert "Multi Thread" in content

    def test_task_description_mentions_continuous_codebase_improvement(self):
        """Task description contains 'Continuous Codebase Improvement'."""
        written_data = self._run_main_capture_writes()
        for content in written_data:
            assert "Continuous Codebase Improvement" in content

    def test_task_description_mentions_blind_spots(self):
        """Task description mentions 'blind spots' (key improvement directive)."""
        written_data = self._run_main_capture_writes()
        for content in written_data:
            assert "blind spots" in content

    def test_all_100_files_have_identical_content(self):
        """All 100 files should contain the same task description."""
        written_data = self._run_main_capture_writes()
        assert len(written_data) == 100
        # All content should be identical
        assert len(set(written_data)) == 1

    def test_file_opened_in_write_mode(self):
        """Each file is opened in write ('w') mode, not append or read."""
        import bulk_improve as bm

        open_modes = []

        def fake_makedirs(path, exist_ok=False):
            pass

        def capturing_open(path, mode="r"):
            open_modes.append(mode)
            handle = MagicMock()
            handle.__enter__ = lambda s: s
            handle.__exit__ = MagicMock(return_value=False)
            handle.write = lambda c: None
            return handle

        with patch.object(bm.os, "makedirs", side_effect=fake_makedirs), \
             patch("builtins.open", side_effect=capturing_open), \
             patch.object(bm.time, "sleep"), \
             patch.object(bm.time, "time", return_value=1700000000):
            bm.main()

        assert len(open_modes) == 100
        assert all(m == "w" for m in open_modes)

    def test_main_prints_progress_messages(self, capsys):
        """main() prints 'Queued task N/100' for each file."""
        import bulk_improve as bm

        def fake_makedirs(path, exist_ok=False):
            pass

        with patch.object(bm.os, "makedirs", side_effect=fake_makedirs), \
             patch("builtins.open", unittest.mock.mock_open()), \
             patch.object(bm.time, "sleep"), \
             patch.object(bm.time, "time", return_value=1700000000):
            bm.main()

        captured = capsys.readouterr()
        assert "Queued task 1/100" in captured.out
        assert "Queued task 50/100" in captured.out
        assert "Queued task 100/100" in captured.out

    def test_filenames_use_int_timestamp(self):
        """Filenames use int(time.time()) as the timestamp component."""
        import bulk_improve as bm

        opened_paths = []

        def fake_makedirs(path, exist_ok=False):
            pass

        def capturing_open(path, mode="r"):
            opened_paths.append(path)
            handle = MagicMock()
            handle.__enter__ = lambda s: s
            handle.__exit__ = MagicMock(return_value=False)
            handle.write = lambda c: None
            return handle

        fixed_ts = 1700000000

        with patch.object(bm.os, "makedirs", side_effect=fake_makedirs), \
             patch("builtins.open", side_effect=capturing_open), \
             patch.object(bm.time, "sleep"), \
             patch.object(bm.time, "time", return_value=fixed_ts):
            bm.main()

        for path in opened_paths:
            assert str(fixed_ts) in path


# ===========================================================================
# nanoc/memory/memory.py – additional create_task coverage
# ===========================================================================

class TestMemoryCreateTaskAdditional:
    """Additional tests for the updated create_task (no priority parameter)."""

    def test_create_task_with_parent_id_stores_correctly(self, memory):
        """create_task stores parent_id in the database."""
        parent_id = memory.create_task("Parent task")
        child_id = memory.create_task("Child task", parent_id=parent_id)

        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT parent_id FROM tasks WHERE id = ?", (child_id,))
            row = cursor.fetchone()

        assert row[0] == parent_id

    def test_create_task_all_parameters_stored(self, memory):
        """create_task with all valid parameters stores all values correctly."""
        parent_id = memory.create_task("Parent")
        task_id = memory.create_task(
            "Full task",
            assigned_to="Planner",
            parent_id=parent_id,
            project_id="proj_full"
        )

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            row = dict(cursor.fetchone())

        assert row["description"] == "Full task"
        assert row["assigned_to"] == "Planner"
        assert row["parent_id"] == parent_id
        assert row["project_id"] == "proj_full"
        assert row["status"] == "pending"
        assert row["priority"] == 0

    def test_create_task_no_priority_in_method_signature(self, memory):
        """create_task signature has exactly these parameters and not 'priority'."""
        sig = inspect.signature(memory.create_task)
        param_names = list(sig.parameters.keys())
        assert "description" in param_names
        assert "assigned_to" in param_names
        assert "parent_id" in param_names
        assert "project_id" in param_names
        assert "priority" not in param_names

    def test_create_task_null_assigned_to(self, memory):
        """create_task stores NULL assigned_to when not specified."""
        task_id = memory.create_task("Unassigned task")
        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT assigned_to FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()
        assert row[0] is None

    def test_create_task_null_parent_id(self, memory):
        """create_task stores NULL parent_id when not specified."""
        task_id = memory.create_task("Root task")
        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT parent_id FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()
        assert row[0] is None

    def test_create_task_timestamps_are_set(self, memory):
        """create_task sets both created_at and updated_at."""
        task_id = memory.create_task("Timestamped task")
        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT created_at, updated_at FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()
        assert row[0] is not None
        assert row[1] is not None

    def test_create_multiple_tasks_all_have_pending_status(self, memory):
        """Multiple tasks created in sequence all start as 'pending'."""
        ids = [memory.create_task(f"Task {i}") for i in range(5)]
        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            placeholders = ",".join("?" for _ in ids)
            cursor.execute(f"SELECT status FROM tasks WHERE id IN ({placeholders})", ids)
            rows = cursor.fetchall()
        assert all(row[0] == "pending" for row in rows)
        assert len(rows) == 5


# ===========================================================================
# nanoc/agents/analyst.py – additional coverage
# ===========================================================================

class TestAnalystAdditional:
    """Additional tests for Analyst.analyze_failure."""

    @pytest.mark.asyncio
    async def test_analyze_failure_event_strategy_field_contains_llm_response(self, memory):
        """The 'analysis/completed' event's 'strategy' field contains the LLM response."""
        from nanoc.agents.analyst import Analyst

        analyst = Analyst("Analyst1", memory)
        mock_llm = MockLLM()
        mock_llm.default_response = "Restart the failing service"
        analyst.llm = mock_llm

        with patch.object(memory, "create_task", return_value=1):
            await analyst.analyze_failure({
                "project_id": "proj_strategy",
                "error": "ServiceCrash"
            })

        events = memory.get_events(topic="analysis/completed")
        payload = json.loads(events[-1]["payload"])
        assert "strategy" in payload
        assert "Restart the failing service" in payload["strategy"]

    @pytest.mark.asyncio
    async def test_analyze_failure_logs_the_error_message(self, memory):
        """analyze_failure writes a log entry containing the error message."""
        from nanoc.agents.analyst import Analyst

        analyst = Analyst("Analyst1", memory)
        analyst.llm = MockLLM()

        error_msg = "UnhandledExceptionAtLine99"

        with patch.object(memory, "create_task", return_value=1):
            await analyst.analyze_failure({
                "project_id": "proj_log",
                "error": error_msg
            })

        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT content FROM logs WHERE agent_id = ? AND content LIKE ?",
                ("Analyst1", f"%{error_msg}%")
            )
            rows = cursor.fetchall()

        assert len(rows) >= 1

    @pytest.mark.asyncio
    async def test_analyze_failure_uses_project_id_in_prompt(self, memory):
        """analyze_failure includes the project_id in the LLM prompt."""
        from nanoc.agents.analyst import Analyst

        analyst = Analyst("Analyst1", memory)
        captured_prompts = []

        async def capturing_complete(prompt, system_prompt=""):
            captured_prompts.append(prompt)
            return "fix strategy"

        analyst.llm = MagicMock()
        analyst.llm.complete = capturing_complete

        with patch.object(memory, "create_task", return_value=1):
            await analyst.analyze_failure({
                "project_id": "proj_xyz99",
                "error": "SomeError"
            })

        assert any("proj_xyz99" in p for p in captured_prompts)

    @pytest.mark.asyncio
    async def test_analyze_failure_unknown_project_id_default(self, memory):
        """analyze_failure uses 'unknown' project_id when not provided."""
        from nanoc.agents.analyst import Analyst

        analyst = Analyst("Analyst1", memory)
        analyst.llm = MockLLM()

        create_task_calls = []

        def capturing_create_task(desc, **kwargs):
            create_task_calls.append(kwargs)
            return 1

        with patch.object(memory, "create_task", side_effect=capturing_create_task):
            await analyst.analyze_failure({"error": "SomeError"})

        assert len(create_task_calls) == 1
        assert create_task_calls[0].get("project_id") == "unknown"


# ===========================================================================
# nanoc/agents/base.py – TeamLeader additional coverage
# ===========================================================================

class TestTeamLeaderAdditional:
    """Additional tests for TeamLeader.delegate_tasks after the project_id simplification."""

    @pytest.mark.asyncio
    async def test_incoming_job_event_does_not_contain_leader_field(self, memory):
        """The 'project/incoming-job' event payload no longer has a 'leader' field."""
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("LeaderX", "Team Leader", memory, MockLLM())
        await leader.delegate_tasks("Build a monitoring dashboard")

        events = memory.get_events(topic="project/incoming-job")
        assert len(events) >= 1
        payload = json.loads(events[-1]["payload"])
        assert "leader" not in payload

    @pytest.mark.asyncio
    async def test_incoming_job_event_contains_project_id(self, memory):
        """The 'project/incoming-job' event payload includes 'project_id'."""
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("LeaderX", "Team Leader", memory, MockLLM())
        project_id = await leader.delegate_tasks("Build a logging system")

        events = memory.get_events(topic="project/incoming-job")
        payload = json.loads(events[-1]["payload"])
        assert payload["project_id"] == project_id

    @pytest.mark.asyncio
    async def test_incoming_job_event_contains_description(self, memory):
        """The 'project/incoming-job' event payload includes 'description'."""
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("LeaderX", "Team Leader", memory, MockLLM())
        await leader.delegate_tasks("Build an alerting pipeline")

        events = memory.get_events(topic="project/incoming-job")
        payload = json.loads(events[-1]["payload"])
        assert "description" in payload
        assert "alerting pipeline" in payload["description"]

    @pytest.mark.asyncio
    async def test_project_id_added_to_active_projects_knowledge(self, memory):
        """delegate_tasks adds the new project_id to the 'active_projects' knowledge entry."""
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("LeaderX", "Team Leader", memory, MockLLM())
        project_id = await leader.delegate_tasks("Implement flow control")

        active = memory.get_knowledge("active_projects")
        assert active is not None
        assert project_id in active

    @pytest.mark.asyncio
    async def test_architecture_stored_in_knowledge_base(self, memory):
        """delegate_tasks stores the LLM architecture response in knowledge base."""
        from nanoc.agents.base import TeamLeader

        mock_llm = MockLLM()
        mock_llm.default_response = "Microservices with event bus"
        leader = TeamLeader("LeaderX", "Team Leader", memory, mock_llm)
        project_id = await leader.delegate_tasks("Design the system")

        arch = memory.get_knowledge(f"project_{project_id}_arch")
        assert arch is not None

    @pytest.mark.asyncio
    async def test_delegate_tasks_creates_architect_task(self, memory):
        """delegate_tasks creates a task assigned to 'Architect' in the database."""
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("LeaderX", "Team Leader", memory, MockLLM())
        project_id = await leader.delegate_tasks("Build distributed cache")

        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM tasks WHERE project_id = ? AND assigned_to = 'Architect'",
                (project_id,)
            )
            rows = cursor.fetchall()

        assert len(rows) >= 1

    @pytest.mark.asyncio
    async def test_project_id_format_is_proj_followed_by_digits_only(self, memory):
        """The generated project_id matches 'proj_<digits>' exactly (no hex suffix)."""
        from nanoc.agents.base import TeamLeader
        import re

        leader = TeamLeader("LeaderX", "Team Leader", memory, MockLLM())
        project_id = await leader.delegate_tasks("New project")

        # Should match proj_<integer timestamp> only
        assert re.match(r"^proj_\d+$", project_id), \
            f"project_id '{project_id}' does not match expected format 'proj_<digits>'"

    @pytest.mark.asyncio
    async def test_delegate_tasks_same_project_not_duplicated_in_active(self, memory):
        """Calling delegate_tasks with the same project_id prefix does not duplicate it in active_projects."""
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("LeaderX", "Team Leader", memory, MockLLM())

        # First call with an explicit project id in description
        await leader.delegate_tasks("proj_fixed: Build the feature")
        await leader.delegate_tasks("proj_fixed: Build the feature again")

        active = memory.get_knowledge("active_projects")
        count = active.count("proj_fixed") if active else 0
        # Should appear only once
        assert count == 1


# ===========================================================================
# nanoc/agents/security.py – additional coverage
# ===========================================================================

class TestSecurityAgentAdditional:
    """Additional tests for SecurityAgent after vulnerability analysis was removed."""

    def test_security_agent_role_attribute(self, memory):
        """SecurityAgent.role is set to 'Security'."""
        from nanoc.agents.security import SecurityAgent
        agent = SecurityAgent("SecTest", memory)
        assert agent.role == "Security"

    def test_security_agent_id_attribute(self, memory):
        """SecurityAgent.agent_id is stored correctly."""
        from nanoc.agents.security import SecurityAgent
        agent = SecurityAgent("Scanner42", memory)
        assert agent.agent_id == "Scanner42"

    def test_security_agent_has_memory_reference(self, memory):
        """SecurityAgent stores the memory reference."""
        from nanoc.agents.security import SecurityAgent
        agent = SecurityAgent("SecTest", memory)
        assert agent.memory is memory

    @pytest.mark.asyncio
    async def test_audit_service_returns_stdout_string(self, memory):
        """audit_service returns the stdout string on a successful nmap run."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("SecTest", memory)
        agent.llm = MockLLM()

        nmap_output = "22/tcp open ssh OpenSSH 8.2\n80/tcp open http"
        nmap_result = {"stdout": nmap_output, "stderr": "", "returncode": 0}

        with patch("nanoc.tools.network.AsyncRunner.run_command",
                   new_callable=AsyncMock, return_value=nmap_result):
            result = await agent.audit_service("192.168.1.10")

        assert result == nmap_output

    @pytest.mark.asyncio
    async def test_audit_service_returns_error_dict_when_nmap_fails(self, memory):
        """audit_service returns the error dict when nmap is unavailable."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("SecTest", memory)
        agent.llm = MockLLM()

        error_result = {"error": "nmap: command not found", "returncode": 1, "stdout": "", "stderr": ""}

        with patch("nanoc.tools.network.AsyncRunner.run_command",
                   new_callable=AsyncMock, return_value=error_result):
            result = await agent.audit_service("10.0.0.1")

        assert result == error_result

    @pytest.mark.asyncio
    async def test_audit_service_no_event_when_error_present(self, memory):
        """audit_service does NOT publish event when nmap returns an error dict."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("SecTest", memory)
        agent.llm = MockLLM()

        error_result = {"error": "Permission denied"}

        with patch("nanoc.tools.network.AsyncRunner.run_command",
                   new_callable=AsyncMock, return_value=error_result):
            await agent.audit_service("192.168.1.1")

        events = memory.get_events(topic="security/audit-complete")
        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_audit_service_event_target_field(self, memory):
        """The 'security/audit-complete' event payload 'target' matches the queried host."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("SecTest", memory)
        agent.llm = MockLLM()

        nmap_result = {"stdout": "scan result", "stderr": "", "returncode": 0}
        target = "172.16.0.50"

        with patch("nanoc.tools.network.AsyncRunner.run_command",
                   new_callable=AsyncMock, return_value=nmap_result):
            await agent.audit_service(target)

        events = memory.get_events(topic="security/audit-complete")
        payload = json.loads(events[-1]["payload"])
        assert payload["target"] == target

    @pytest.mark.asyncio
    async def test_audit_service_event_has_only_two_keys(self, memory):
        """Simplified event payload has exactly 'target' and 'report' keys."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("SecTest", memory)
        agent.llm = MockLLM()

        nmap_result = {"stdout": "443/tcp open https", "stderr": "", "returncode": 0}

        with patch("nanoc.tools.network.AsyncRunner.run_command",
                   new_callable=AsyncMock, return_value=nmap_result):
            await agent.audit_service("10.10.0.1")

        events = memory.get_events(topic="security/audit-complete")
        payload = json.loads(events[-1]["payload"])
        assert set(payload.keys()) == {"target", "report"}

    @pytest.mark.asyncio
    async def test_audit_service_uses_nmap_sv_flag(self, memory):
        """audit_service runs nmap with the -sV flag for service version detection."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("SecTest", memory)
        agent.llm = MockLLM()

        nmap_result = {"stdout": "22/tcp open ssh", "stderr": "", "returncode": 0}
        captured_cmds = []

        async def capturing_run(cmd):
            captured_cmds.append(cmd)
            return nmap_result

        with patch("nanoc.tools.network.AsyncRunner.run_command", side_effect=capturing_run):
            await agent.audit_service("192.168.1.5")

        assert len(captured_cmds) == 1
        cmd = captured_cmds[0]
        assert "nmap" in cmd
        assert "-sV" in cmd
        assert "192.168.1.5" in cmd


# ===========================================================================
# nanoc/core/llm.py – additional coverage
# ===========================================================================

class TestLLMProviderAdditional:
    """Additional tests for LLMProvider after retry loop was removed."""

    @pytest.mark.asyncio
    async def test_complete_uses_model_override_from_knowledge_base(self, memory):
        """complete() uses model from knowledge base when override is set."""
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="openrouter", model="default-model")
        memory.upsert_knowledge("system/model_override", "override-model-v2")

        captured_models = []

        async def capturing_complete(prompt, system_prompt, model):
            captured_models.append(model)
            return "response"

        with patch("nanoc.core.llm.Memory", return_value=memory), \
             patch.object(provider, "_openrouter_complete", side_effect=capturing_complete), \
             patch.object(provider, "_record_telemetry"), \
             patch("nanoc.core.llm.settings") as mock_settings:
            mock_settings.DB_PATH = memory.db_path
            await provider.complete("test prompt")

        assert len(captured_models) == 1
        assert captured_models[0] == "override-model-v2"

    @pytest.mark.asyncio
    async def test_complete_uses_default_model_when_no_override(self, memory):
        """complete() uses the default model when no override is set in knowledge base."""
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="openrouter", model="my-default-model")

        captured_models = []

        async def capturing_complete(prompt, system_prompt, model):
            captured_models.append(model)
            return "response"

        with patch("nanoc.core.llm.Memory", return_value=memory), \
             patch.object(provider, "_openrouter_complete", side_effect=capturing_complete), \
             patch.object(provider, "_record_telemetry"), \
             patch("nanoc.core.llm.settings") as mock_settings:
            mock_settings.DB_PATH = memory.db_path
            await provider.complete("test prompt")

        assert len(captured_models) == 1
        assert captured_models[0] == "my-default-model"

    @pytest.mark.asyncio
    async def test_complete_calls_ollama_complete_for_ollama_provider(self, memory):
        """complete() calls _ollama_complete for the 'ollama' provider."""
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="ollama", model="llama3")

        ollama_called = []

        async def mock_ollama(prompt, system_prompt, model):
            ollama_called.append(True)
            return "ollama response"

        with patch("nanoc.core.llm.Memory", return_value=memory), \
             patch.object(provider, "_ollama_complete", side_effect=mock_ollama), \
             patch.object(provider, "_record_telemetry"), \
             patch("nanoc.core.llm.settings") as mock_settings:
            mock_settings.DB_PATH = memory.db_path
            result = await provider.complete("test prompt")

        assert len(ollama_called) == 1
        assert result == "ollama response"

    @pytest.mark.asyncio
    async def test_complete_does_not_call_openrouter_for_ollama_provider(self, memory):
        """complete() does NOT call _openrouter_complete for 'ollama' provider."""
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="ollama", model="llama3")
        openrouter_called = []

        async def mock_ollama(prompt, system_prompt, model):
            return "ollama response"

        async def mock_openrouter(prompt, system_prompt, model):
            openrouter_called.append(True)
            return "openrouter response"

        with patch("nanoc.core.llm.Memory", return_value=memory), \
             patch.object(provider, "_ollama_complete", side_effect=mock_ollama), \
             patch.object(provider, "_openrouter_complete", side_effect=mock_openrouter), \
             patch.object(provider, "_record_telemetry"), \
             patch("nanoc.core.llm.settings") as mock_settings:
            mock_settings.DB_PATH = memory.db_path
            await provider.complete("test prompt")

        assert len(openrouter_called) == 0

    @pytest.mark.asyncio
    async def test_complete_error_is_re_raised_after_recording(self, memory):
        """complete() re-raises the original exception after calling _record_error."""
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="openrouter", model="test-model")

        original_error = RuntimeError("original error message")

        async def failing_complete(prompt, system_prompt, model):
            raise original_error

        recorded_errors = []

        with patch("nanoc.core.llm.Memory", return_value=memory), \
             patch.object(provider, "_openrouter_complete", side_effect=failing_complete), \
             patch.object(provider, "_record_error", side_effect=lambda e: recorded_errors.append(e)), \
             patch("nanoc.core.llm.settings") as mock_settings:
            mock_settings.DB_PATH = memory.db_path
            with pytest.raises(RuntimeError) as exc_info:
                await provider.complete("test prompt")

        assert exc_info.value is original_error
        assert len(recorded_errors) == 1

    def test_llm_provider_default_provider_from_settings(self):
        """LLMProvider uses settings.DEFAULT_PROVIDER when provider not specified."""
        from nanoc.core.llm import LLMProvider
        from nanoc.core.config import settings

        provider = LLMProvider()
        assert provider.provider == settings.DEFAULT_PROVIDER

    def test_llm_provider_default_model_from_settings(self):
        """LLMProvider uses settings.DEFAULT_MODEL when model not specified."""
        from nanoc.core.llm import LLMProvider
        from nanoc.core.config import settings

        provider = LLMProvider()
        assert provider.model == settings.DEFAULT_MODEL


# ===========================================================================
# nanoc/core/orchestrator.py – additional coverage
# ===========================================================================

class TestOrchestratorAdditional:
    """Additional tests for Orchestrator.process_task after explicit dispatch was added."""

    def _get_task(self, memory, task_id):
        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            return dict(cursor.fetchone())

    @pytest.mark.asyncio
    async def test_process_task_stores_result_in_db_on_success(self, memory):
        """process_task writes the agent result into the 'result' column of the task."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_coder = MagicMock()
        mock_coder.role = "Coder"
        mock_coder.log = AsyncMock()
        mock_coder.write_code = AsyncMock(return_value="def solution(): return 42")
        orch.add_agent(mock_coder)

        task_id = memory.create_task("Write code", assigned_to="Coder", project_id="proj_result")
        task = self._get_task(memory, task_id)

        await orch.process_task(task)

        updated = self._get_task(memory, task_id)
        assert updated["result"] == "def solution(): return 42"

    @pytest.mark.asyncio
    async def test_process_task_marks_completed_on_success(self, memory):
        """process_task sets status='completed' after a successful agent execution."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_planner = MagicMock()
        mock_planner.role = "Planner"
        mock_planner.log = AsyncMock()
        mock_planner.create_todo_list = AsyncMock(return_value="TASK: step1\nTASK: step2")
        orch.add_agent(mock_planner)

        task_id = memory.create_task("Plan the work", assigned_to="Planner", project_id="proj_done")
        task = self._get_task(memory, task_id)

        await orch.process_task(task)

        updated = self._get_task(memory, task_id)
        assert updated["status"] == "completed"

    @pytest.mark.asyncio
    async def test_process_task_increments_retry_count_on_failure(self, memory):
        """process_task increments retry_count in DB when agent raises an exception."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_architect = MagicMock()
        mock_architect.role = "Architect"
        mock_architect.log = AsyncMock()
        mock_architect.design_solution = AsyncMock(side_effect=RuntimeError("design failed"))
        orch.add_agent(mock_architect)

        task_id = memory.create_task("Design", assigned_to="Architect", project_id="proj_retry")
        task = self._get_task(memory, task_id)
        # Ensure retry_count starts at 0
        assert task.get("retry_count", 0) == 0

        await orch.process_task(task)

        updated = self._get_task(memory, task_id)
        assert updated["retry_count"] == 1

    @pytest.mark.asyncio
    async def test_process_task_sets_pending_when_retry_count_below_max(self, memory):
        """process_task sets status='pending' when retry_count < max_retries."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_coder = MagicMock()
        mock_coder.role = "Coder"
        mock_coder.log = AsyncMock()
        mock_coder.write_code = AsyncMock(side_effect=RuntimeError("code failed"))
        orch.add_agent(mock_coder)

        task_id = memory.create_task("Write code", assigned_to="Coder", project_id="proj_pend")
        task = self._get_task(memory, task_id)
        # At retry_count=0 with max_retries=3, should go to 'pending'
        task["retry_count"] = 0
        task["max_retries"] = 3

        await orch.process_task(task)

        updated = self._get_task(memory, task_id)
        assert updated["status"] == "pending"

    @pytest.mark.asyncio
    async def test_process_task_sets_failed_when_retry_count_equals_max(self, memory):
        """process_task sets status='failed' when retry_count reaches max_retries."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_reviewer = MagicMock()
        mock_reviewer.role = "Reviewer"
        mock_reviewer.log = AsyncMock()
        mock_reviewer.review_work = AsyncMock(side_effect=RuntimeError("review failed"))
        orch.add_agent(mock_reviewer)

        task_id = memory.create_task("Review code", assigned_to="Reviewer", project_id="proj_fail")
        task = self._get_task(memory, task_id)
        # Simulate being at max retries
        task["retry_count"] = 3
        task["max_retries"] = 3

        await orch.process_task(task)

        updated = self._get_task(memory, task_id)
        assert updated["status"] == "failed"

    @pytest.mark.asyncio
    async def test_process_task_publishes_task_failed_event_on_permanent_failure(self, memory):
        """process_task publishes 'task/failed' event when retries are exhausted."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_coder = MagicMock()
        mock_coder.role = "Coder"
        mock_coder.log = AsyncMock()
        mock_coder.write_code = AsyncMock(side_effect=RuntimeError("permanent failure"))
        orch.add_agent(mock_coder)

        task_id = memory.create_task("Write code", assigned_to="Coder", project_id="proj_ev")
        task = self._get_task(memory, task_id)
        task["retry_count"] = 3
        task["max_retries"] = 3

        await orch.process_task(task)

        events = memory.get_events(topic="task/failed")
        assert len(events) >= 1
        payload = json.loads(events[-1]["payload"])
        assert payload["task_id"] == task_id
        assert payload["project_id"] == "proj_ev"

    @pytest.mark.asyncio
    async def test_process_task_no_failed_event_when_still_retrying(self, memory):
        """process_task does NOT publish 'task/failed' event when still retrying."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_coder = MagicMock()
        mock_coder.role = "Coder"
        mock_coder.log = AsyncMock()
        mock_coder.write_code = AsyncMock(side_effect=RuntimeError("transient"))
        orch.add_agent(mock_coder)

        task_id = memory.create_task("Write code", assigned_to="Coder", project_id="proj_noev")
        task = self._get_task(memory, task_id)
        task["retry_count"] = 0
        task["max_retries"] = 3

        await orch.process_task(task)

        events = memory.get_events(topic="task/failed")
        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_process_task_reviewer_fix_task_description_contains_review_result(self, memory):
        """The fix task created on review failure includes the review result in the description."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        review_result = "STATUS: FAILED\nMissing null check on line 42"
        mock_reviewer = MagicMock()
        mock_reviewer.role = "Reviewer"
        mock_reviewer.log = AsyncMock()
        mock_reviewer.review_work = AsyncMock(return_value=review_result)
        orch.add_agent(mock_reviewer)

        task_id = memory.create_task("Review code", assigned_to="Reviewer", project_id="proj_desc")
        task = self._get_task(memory, task_id)

        await orch.process_task(task)

        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT description FROM tasks WHERE assigned_to = 'Coder'")
            rows = cursor.fetchall()

        assert len(rows) == 1
        fix_desc = rows[0][0]
        assert "Fix flaws" in fix_desc
        assert "Missing null check on line 42" in fix_desc

    def test_orchestrator_add_agent_registers_by_role(self, memory):
        """add_agent registers the agent using its role as the key."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader, Architect

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        arch = Architect("Arch1", "Architect", memory, MockLLM())
        orch.add_agent(arch)

        assert "Architect" in orch.agents
        assert orch.agents["Architect"] is arch


# ===========================================================================
# nanoc/tools/network.py – additional coverage
# ===========================================================================

class TestDiscoveryToolAdditional:
    """Additional tests for DiscoveryTool._get_fallback_topology and discover_topology."""

    def test_fallback_topology_returns_dict_with_nodes_and_edges(self, memory):
        """_get_fallback_topology always returns a dict with 'nodes' and 'edges' keys."""
        from nanoc.tools.network import DiscoveryTool

        topology = DiscoveryTool._get_fallback_topology(memory)

        assert isinstance(topology, dict)
        assert "nodes" in topology
        assert "edges" in topology

    def test_fallback_topology_returned_topology_is_same_as_cached(self, memory):
        """_get_fallback_topology returns the same topology object it stores in the cache."""
        from nanoc.tools.network import DiscoveryTool

        topology = DiscoveryTool._get_fallback_topology(memory)
        cached = memory.get_knowledge("network_topology")

        assert topology == cached

    def test_fallback_topology_second_call_returns_same_data(self, memory):
        """Calling _get_fallback_topology twice returns consistent data."""
        from nanoc.tools.network import DiscoveryTool

        topo1 = DiscoveryTool._get_fallback_topology(memory)
        topo2 = DiscoveryTool._get_fallback_topology(memory)

        assert topo1["nodes"] == topo2["nodes"]
        assert topo1["edges"] == topo2["edges"]

    @pytest.mark.asyncio
    async def test_discover_topology_returns_cached_result_immediately(self, memory):
        """discover_topology returns the cached topology without scanning if cache exists."""
        from nanoc.tools.network import DiscoveryTool

        cached_topology = {
            "nodes": [{"id": "10.0.0.1", "label": "cached-host", "type": "host", "status": "online"}],
            "edges": []
        }
        memory.upsert_knowledge("network_topology", cached_topology)

        scan_called = []

        async def mock_scan(ip_range):
            scan_called.append(True)
            return {"stdout": "", "stderr": "", "returncode": 0}

        with patch("nanoc.tools.network.Memory", return_value=memory), \
             patch("nanoc.tools.network.settings") as mock_settings, \
             patch("nanoc.tools.network.NetworkScanner.scan_local_network",
                   side_effect=mock_scan):
            mock_settings.DB_PATH = memory.db_path
            result = await DiscoveryTool.discover_topology("192.168.1.0/24")

        # Scan should NOT have been called because cache was hit
        assert len(scan_called) == 0
        assert result == cached_topology

    @pytest.mark.asyncio
    async def test_discover_topology_falls_back_on_nonzero_returncode(self, memory):
        """discover_topology falls back to localhost topology when nmap returncode != 0."""
        from nanoc.tools.network import DiscoveryTool

        scan_result = {"stdout": "", "stderr": "error", "returncode": 1}

        with patch("nanoc.tools.network.Memory", return_value=memory), \
             patch("nanoc.tools.network.settings") as mock_settings, \
             patch("nanoc.tools.network.NetworkScanner.scan_local_network",
                   new_callable=AsyncMock, return_value=scan_result):
            mock_settings.DB_PATH = memory.db_path
            topology = await DiscoveryTool.discover_topology("192.168.0.0/24")

        assert len(topology["nodes"]) == 1
        assert topology["nodes"][0]["id"] == "127.0.0.1"

    @pytest.mark.asyncio
    async def test_discover_topology_falls_back_on_xml_parse_failure(self, memory):
        """discover_topology falls back when nmap stdout is not valid XML."""
        from nanoc.tools.network import DiscoveryTool

        scan_result = {"stdout": "NOT VALID XML <broken>", "stderr": "", "returncode": 0}

        with patch("nanoc.tools.network.Memory", return_value=memory), \
             patch("nanoc.tools.network.settings") as mock_settings, \
             patch("nanoc.tools.network.NetworkScanner.scan_local_network",
                   new_callable=AsyncMock, return_value=scan_result):
            mock_settings.DB_PATH = memory.db_path
            topology = await DiscoveryTool.discover_topology("192.168.0.0/24")

        assert len(topology["nodes"]) == 1
        assert topology["nodes"][0]["id"] == "127.0.0.1"
        assert topology["edges"] == []

    @pytest.mark.asyncio
    async def test_discover_topology_fallback_caches_result(self, memory):
        """When falling back, discover_topology caches the localhost topology."""
        from nanoc.tools.network import DiscoveryTool

        scan_result = {"error": "nmap not found", "returncode": 1, "stdout": "", "stderr": ""}

        with patch("nanoc.tools.network.Memory", return_value=memory), \
             patch("nanoc.tools.network.settings") as mock_settings, \
             patch("nanoc.tools.network.NetworkScanner.scan_local_network",
                   new_callable=AsyncMock, return_value=scan_result):
            mock_settings.DB_PATH = memory.db_path
            await DiscoveryTool.discover_topology("192.168.0.0/24")

        cached = memory.get_knowledge("network_topology")
        assert cached is not None
        assert cached["nodes"][0]["id"] == "127.0.0.1"

    def test_fallback_topology_localhost_node_has_all_required_fields(self, memory):
        """The localhost node in fallback topology has 'id', 'label', 'type', and 'status'."""
        from nanoc.tools.network import DiscoveryTool

        topology = DiscoveryTool._get_fallback_topology(memory)
        node = topology["nodes"][0]

        required_fields = {"id", "label", "type", "status"}
        assert required_fields.issubset(set(node.keys()))


# ===========================================================================
# nanoc/agents/base.py – handle_task removal regression tests
# ===========================================================================

class TestHandleTaskRemovedRegression:
    """Verify that handle_task is gone from all relevant classes."""

    def test_base_agent_handle_task_attribute_not_present(self, memory):
        """BaseAgent does not have a handle_task attribute."""
        from nanoc.agents.base import BaseAgent
        agent = BaseAgent("base1", "TestRole", memory, MockLLM())
        assert not hasattr(agent, "handle_task")

    def test_architect_handle_task_not_present(self, memory):
        """Architect does not have a handle_task method."""
        from nanoc.agents.base import Architect
        agent = Architect("arch1", "Architect", memory, MockLLM())
        assert not hasattr(agent, "handle_task")

    def test_planner_handle_task_not_present(self, memory):
        """Planner does not have a handle_task method."""
        from nanoc.agents.base import Planner
        agent = Planner("plan1", "Planner", memory, MockLLM())
        assert not hasattr(agent, "handle_task")

    def test_coder_handle_task_not_present(self, memory):
        """Coder does not have a handle_task method."""
        from nanoc.agents.base import Coder
        agent = Coder("coder1", "Coder", memory, MockLLM())
        assert not hasattr(agent, "handle_task")

    def test_reviewer_handle_task_not_present(self, memory):
        """Reviewer does not have a handle_task method."""
        from nanoc.agents.base import Reviewer
        agent = Reviewer("rev1", "Reviewer", memory, MockLLM())
        assert not hasattr(agent, "handle_task")

    def test_architect_still_has_design_solution(self, memory):
        """After handle_task removal, Architect still has design_solution method."""
        from nanoc.agents.base import Architect
        agent = Architect("arch1", "Architect", memory, MockLLM())
        assert hasattr(agent, "design_solution")
        assert asyncio.iscoroutinefunction(agent.design_solution)

    def test_planner_still_has_create_todo_list(self, memory):
        """After handle_task removal, Planner still has create_todo_list method."""
        from nanoc.agents.base import Planner
        agent = Planner("plan1", "Planner", memory, MockLLM())
        assert hasattr(agent, "create_todo_list")
        assert asyncio.iscoroutinefunction(agent.create_todo_list)

    def test_coder_still_has_write_code(self, memory):
        """After handle_task removal, Coder still has write_code method."""
        from nanoc.agents.base import Coder
        agent = Coder("coder1", "Coder", memory, MockLLM())
        assert hasattr(agent, "write_code")
        assert asyncio.iscoroutinefunction(agent.write_code)

    def test_reviewer_still_has_review_work(self, memory):
        """After handle_task removal, Reviewer still has review_work method."""
        from nanoc.agents.base import Reviewer
        agent = Reviewer("rev1", "Reviewer", memory, MockLLM())
        assert hasattr(agent, "review_work")
        assert asyncio.iscoroutinefunction(agent.review_work)


# ===========================================================================
# nanoc/core/llm.py – no retry loop structural test
# ===========================================================================

class TestLLMProviderNoRetryStructural:
    """Structural tests verifying the retry loop was removed from complete()."""

    def test_complete_method_has_no_for_loop(self):
        """complete() source does not contain a 'for attempt in range' loop."""
        from nanoc.core.llm import LLMProvider
        src = inspect.getsource(LLMProvider.complete)
        assert "for attempt" not in src
        assert "range(max_retries)" not in src

    def test_complete_method_has_no_asyncio_sleep_for_retry(self):
        """complete() source does not use asyncio.sleep for retry backoff."""
        from nanoc.core.llm import LLMProvider
        src = inspect.getsource(LLMProvider.complete)
        # The old retry used asyncio.sleep for backoff delay
        assert "retry_delay" not in src

    def test_complete_method_has_single_try_block(self):
        """complete() source has exactly one try/except block (the new simplified version)."""
        from nanoc.core.llm import LLMProvider
        src = inspect.getsource(LLMProvider.complete)
        # Count try occurrences - should be just one
        try_count = src.count("\n        try:")
        assert try_count == 1
