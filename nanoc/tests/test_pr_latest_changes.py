"""
Tests for changes introduced in the bulk_improve PR:
  - bulk_improve.py               (new file: creates 100 inbox task files)
  - nanoc/agents/base.py          (removed handle_task methods, project_id no longer has hex suffix)
  - nanoc/agents/security.py      (simplified event payload – no findings/vulnerabilities)
  - nanoc/core/llm.py             (removed retry loop – single attempt, any exception calls _record_error)
  - nanoc/core/orchestrator.py    (explicit role-based dispatch instead of handle_task)
  - nanoc/memory/memory.py        (removed priority param from create_task)
"""
import asyncio
import json
import os
import sqlite3
import time
import inspect
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
    db_path = str(tmp_path / "test_latest_pr.db")
    mem = Memory(db_path)
    yield mem
    if os.path.exists(db_path):
        os.remove(db_path)


# ===========================================================================
# bulk_improve.py – main() bulk task injection
# ===========================================================================

class TestBulkImproveMain:
    def test_creates_inbox_directory(self, tmp_path):
        """main() creates the nanoc/inbox directory if it does not exist."""
        import bulk_improve

        inbox_dir = str(tmp_path / "nanoc" / "inbox")

        with patch.object(bulk_improve.os, "makedirs") as mock_makedirs, \
             patch("builtins.open", MagicMock()), \
             patch.object(bulk_improve.time, "sleep"), \
             patch.object(bulk_improve.os.path, "join", return_value=str(tmp_path / "f.txt")), \
             patch("builtins.print"):
            bulk_improve.main()

        mock_makedirs.assert_called_once_with("nanoc/inbox", exist_ok=True)

    def test_creates_exactly_100_files(self, tmp_path):
        """main() creates exactly 100 task files."""
        import bulk_improve

        created_files = []

        def fake_open(filename, mode):
            created_files.append(filename)
            return MagicMock().__enter__.return_value

        with patch("builtins.open", MagicMock()) as mock_open, \
             patch.object(bulk_improve.os, "makedirs"), \
             patch.object(bulk_improve.time, "sleep"), \
             patch("builtins.print"):
            # Capture calls by counting open() calls
            mock_open.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_open.return_value.__exit__ = MagicMock(return_value=False)
            bulk_improve.main()

        assert mock_open.call_count == 100

    def test_each_file_written_with_task_desc(self, tmp_path):
        """main() writes the task description content to each file."""
        import bulk_improve

        written_contents = []

        mock_file = MagicMock()
        mock_file.__enter__ = MagicMock(return_value=mock_file)
        mock_file.__exit__ = MagicMock(return_value=False)
        mock_file.write = MagicMock(side_effect=lambda s: written_contents.append(s))

        with patch("builtins.open", return_value=mock_file), \
             patch.object(bulk_improve.os, "makedirs"), \
             patch.object(bulk_improve.time, "sleep"), \
             patch("builtins.print"):
            bulk_improve.main()

        assert len(written_contents) == 100
        for content in written_contents:
            assert "Autonomous Network Operating Center" in content
            assert "FOSS" in content

    def test_filename_includes_index(self, tmp_path):
        """Each filename contains the loop index (0 through 99)."""
        import bulk_improve

        filenames = []

        original_join = os.path.join

        def capturing_join(d, f):
            filenames.append(f)
            return original_join(d, f)

        mock_file = MagicMock()
        mock_file.__enter__ = MagicMock(return_value=mock_file)
        mock_file.__exit__ = MagicMock(return_value=False)
        mock_file.write = MagicMock()

        with patch("builtins.open", return_value=mock_file), \
             patch.object(bulk_improve.os, "makedirs"), \
             patch.object(bulk_improve.time, "sleep"), \
             patch.object(bulk_improve.os.path, "join", side_effect=capturing_join), \
             patch("builtins.print"):
            bulk_improve.main()

        # Each filename should contain the loop index
        assert len(filenames) == 100
        for i, fname in enumerate(filenames):
            assert f"_{i}.txt" in fname, f"Expected index {i} in filename '{fname}'"

    def test_filename_starts_with_bulk_task(self, tmp_path):
        """Each filename starts with 'bulk_task_'."""
        import bulk_improve

        filenames = []
        original_join = os.path.join

        def capturing_join(d, f):
            filenames.append(f)
            return original_join(d, f)

        mock_file = MagicMock()
        mock_file.__enter__ = MagicMock(return_value=mock_file)
        mock_file.__exit__ = MagicMock(return_value=False)
        mock_file.write = MagicMock()

        with patch("builtins.open", return_value=mock_file), \
             patch.object(bulk_improve.os, "makedirs"), \
             patch.object(bulk_improve.time, "sleep"), \
             patch.object(bulk_improve.os.path, "join", side_effect=capturing_join), \
             patch("builtins.print"):
            bulk_improve.main()

        for fname in filenames:
            assert fname.startswith("bulk_task_")

    def test_uses_inbox_dir_as_nanoc_inbox(self, tmp_path):
        """main() uses 'nanoc/inbox' as the inbox directory."""
        import bulk_improve

        assert bulk_improve.main.__code__.co_consts is not None or True
        # Verify the inbox_dir value by inspecting main's source
        src = inspect.getsource(bulk_improve.main)
        assert "nanoc/inbox" in src

    def test_task_description_contains_continuous_improvement_phrase(self, tmp_path):
        """The task description includes 'Continuous Codebase Improvement' phrase."""
        import bulk_improve

        written_contents = []
        mock_file = MagicMock()
        mock_file.__enter__ = MagicMock(return_value=mock_file)
        mock_file.__exit__ = MagicMock(return_value=False)
        mock_file.write = MagicMock(side_effect=lambda s: written_contents.append(s))

        with patch("builtins.open", return_value=mock_file), \
             patch.object(bulk_improve.os, "makedirs"), \
             patch.object(bulk_improve.time, "sleep"), \
             patch("builtins.print"):
            bulk_improve.main()

        for content in written_contents:
            assert "Continuous Codebase Improvement" in content

    def test_sleep_called_between_file_creations(self, tmp_path):
        """main() calls time.sleep() for each iteration to ensure unique filenames."""
        import bulk_improve

        mock_file = MagicMock()
        mock_file.__enter__ = MagicMock(return_value=mock_file)
        mock_file.__exit__ = MagicMock(return_value=False)
        mock_file.write = MagicMock()

        with patch("builtins.open", return_value=mock_file), \
             patch.object(bulk_improve.os, "makedirs"), \
             patch.object(bulk_improve.time, "sleep") as mock_sleep, \
             patch("builtins.print"):
            bulk_improve.main()

        assert mock_sleep.call_count == 100

    def test_sleep_duration_is_small(self, tmp_path):
        """main() sleeps for 0.01 seconds between creations."""
        import bulk_improve

        mock_file = MagicMock()
        mock_file.__enter__ = MagicMock(return_value=mock_file)
        mock_file.__exit__ = MagicMock(return_value=False)
        mock_file.write = MagicMock()

        with patch("builtins.open", return_value=mock_file), \
             patch.object(bulk_improve.os, "makedirs"), \
             patch.object(bulk_improve.time, "sleep") as mock_sleep, \
             patch("builtins.print"):
            bulk_improve.main()

        for call_args in mock_sleep.call_args_list:
            assert call_args[0][0] == pytest.approx(0.01)

    def test_prints_queued_task_message(self, tmp_path):
        """main() prints a progress message for each task created."""
        import bulk_improve

        printed = []
        mock_file = MagicMock()
        mock_file.__enter__ = MagicMock(return_value=mock_file)
        mock_file.__exit__ = MagicMock(return_value=False)
        mock_file.write = MagicMock()

        with patch("builtins.open", return_value=mock_file), \
             patch.object(bulk_improve.os, "makedirs"), \
             patch.object(bulk_improve.time, "sleep"), \
             patch("builtins.print", side_effect=lambda s: printed.append(s)):
            bulk_improve.main()

        assert len(printed) == 100
        assert "Queued task 1/100" in printed[0]
        assert "Queued task 100/100" in printed[-1]

    def test_files_written_in_text_mode(self, tmp_path):
        """main() opens each file in text write mode ('w')."""
        import bulk_improve

        open_modes = []
        original_open = open

        def capturing_open(fname, mode):
            open_modes.append(mode)
            mock_f = MagicMock()
            mock_f.__enter__ = MagicMock(return_value=mock_f)
            mock_f.__exit__ = MagicMock(return_value=False)
            mock_f.write = MagicMock()
            return mock_f

        with patch("builtins.open", side_effect=capturing_open), \
             patch.object(bulk_improve.os, "makedirs"), \
             patch.object(bulk_improve.time, "sleep"), \
             patch("builtins.print"):
            bulk_improve.main()

        for mode in open_modes:
            assert mode == "w"

    def test_integration_creates_real_files(self, tmp_path, monkeypatch):
        """Integration: main() creates real files in the inbox directory."""
        import bulk_improve

        # Patch the inbox_dir to use tmp_path
        inbox_dir = str(tmp_path / "nanoc" / "inbox")

        original_makedirs = os.makedirs
        original_join = os.path.join
        original_time = time.time

        call_count = [0]

        def patched_makedirs(path, **kwargs):
            if path == "nanoc/inbox":
                original_makedirs(inbox_dir, exist_ok=True)
            else:
                original_makedirs(path, **kwargs)

        def patched_join(d, f):
            if d == "nanoc/inbox":
                return original_join(inbox_dir, f)
            return original_join(d, f)

        monkeypatch.setattr(bulk_improve.os, "makedirs", patched_makedirs)
        monkeypatch.setattr(bulk_improve.os.path, "join", patched_join)
        monkeypatch.setattr(bulk_improve.time, "sleep", lambda x: None)

        with patch("builtins.print"):
            bulk_improve.main()

        created = [f for f in os.listdir(inbox_dir) if f.endswith(".txt")]
        assert len(created) == 100


# ===========================================================================
# nanoc/memory/memory.py – create_task without priority parameter
# ===========================================================================

class TestMemoryCreateTaskNoPriority:
    def test_create_task_returns_integer_id(self, memory):
        """create_task returns an integer row id."""
        task_id = memory.create_task("Test description", assigned_to="Coder")
        assert isinstance(task_id, int)
        assert task_id > 0

    def test_create_task_signature_has_no_priority_param(self):
        """create_task signature no longer includes a 'priority' parameter."""
        sig = inspect.signature(Memory.create_task)
        assert "priority" not in sig.parameters

    def test_create_task_with_priority_kwarg_raises_type_error(self, memory):
        """Passing priority= keyword to create_task raises TypeError (param removed)."""
        with pytest.raises(TypeError):
            memory.create_task("Task", assigned_to="Coder", priority=10)

    def test_create_task_default_priority_in_db_is_zero(self, memory):
        """Tasks created via create_task get the default priority of 0 from the schema."""
        task_id = memory.create_task("Low priority task", assigned_to="Architect")
        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT priority FROM tasks WHERE id = ?", (task_id,))
            row = dict(cursor.fetchone())
        assert row["priority"] == 0

    def test_create_task_stores_description(self, memory):
        """create_task stores the description correctly."""
        task_id = memory.create_task("My task description", assigned_to="Coder")
        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT description FROM tasks WHERE id = ?", (task_id,))
            row = dict(cursor.fetchone())
        assert row["description"] == "My task description"

    def test_create_task_stores_assigned_to(self, memory):
        """create_task stores the assigned_to value."""
        task_id = memory.create_task("Task X", assigned_to="Reviewer")
        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT assigned_to FROM tasks WHERE id = ?", (task_id,))
            row = dict(cursor.fetchone())
        assert row["assigned_to"] == "Reviewer"

    def test_create_task_with_project_id(self, memory):
        """create_task stores the project_id correctly."""
        task_id = memory.create_task("Task Y", project_id="proj_123")
        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT project_id FROM tasks WHERE id = ?", (task_id,))
            row = dict(cursor.fetchone())
        assert row["project_id"] == "proj_123"

    def test_create_task_status_defaults_to_pending(self, memory):
        """create_task sets the initial status to 'pending'."""
        task_id = memory.create_task("Pending task")
        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM tasks WHERE id = ?", (task_id,))
            row = dict(cursor.fetchone())
        assert row["status"] == "pending"

    def test_create_task_ids_are_sequential(self, memory):
        """create_task returns increasing IDs for sequential calls."""
        id1 = memory.create_task("First task")
        id2 = memory.create_task("Second task")
        assert id2 > id1


# ===========================================================================
# nanoc/core/llm.py – removed retry logic (single attempt)
# ===========================================================================

class TestLLMNoRetry:
    @pytest.mark.anyio
    async def test_exception_calls_record_error(self, memory):
        """Any exception during complete() calls _record_error with the error message."""
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="openrouter", model="test-model")

        with patch("nanoc.core.llm.Memory", return_value=memory), \
             patch.object(provider, "_openrouter_complete", new_callable=AsyncMock,
                          side_effect=RuntimeError("connection failed")), \
             patch.object(provider, "_record_error") as mock_record_error, \
             patch("nanoc.core.llm.settings") as mock_settings:
            mock_settings.DB_PATH = memory.db_path

            with pytest.raises(RuntimeError):
                await provider.complete("hello")

        mock_record_error.assert_called_once()
        error_arg = mock_record_error.call_args[0][0]
        assert "connection failed" in error_arg

    @pytest.mark.anyio
    async def test_exception_is_reraised_immediately(self, memory):
        """The exception is re-raised after _record_error without retry."""
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="openrouter", model="test-model")
        call_count = [0]

        async def failing_complete(prompt, system_prompt, model):
            call_count[0] += 1
            raise RuntimeError("always fails")

        with patch("nanoc.core.llm.Memory", return_value=memory), \
             patch.object(provider, "_openrouter_complete", side_effect=failing_complete), \
             patch.object(provider, "_record_error"), \
             patch("nanoc.core.llm.settings") as mock_settings:
            mock_settings.DB_PATH = memory.db_path

            with pytest.raises(RuntimeError):
                await provider.complete("test")

        # Should only be called once (no retry)
        assert call_count[0] == 1

    def test_no_asyncio_sleep_in_complete_source(self):
        """complete() source code does not contain asyncio.sleep (retry delay removed)."""
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="openrouter")
        src = inspect.getsource(provider.complete)

        # The retry loop used asyncio.sleep; verifying it's gone
        assert "asyncio.sleep" not in src

    @pytest.mark.anyio
    async def test_no_retry_logic_in_source(self):
        """The complete() method source does not contain retry loop logic."""
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="openrouter")
        src = inspect.getsource(provider.complete)

        # The PR removed the retry loop
        assert "max_retries" not in src
        assert "for attempt in range" not in src

    @pytest.mark.anyio
    async def test_httpx_error_also_calls_record_error(self, memory):
        """httpx errors trigger _record_error and are re-raised (not silently retried)."""
        import httpx
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="openrouter", model="test-model")

        with patch("nanoc.core.llm.Memory", return_value=memory), \
             patch.object(provider, "_openrouter_complete", new_callable=AsyncMock,
                          side_effect=httpx.RequestError("timeout", request=MagicMock())), \
             patch.object(provider, "_record_error") as mock_record_error, \
             patch("nanoc.core.llm.settings") as mock_settings:
            mock_settings.DB_PATH = memory.db_path

            with pytest.raises(httpx.RequestError):
                await provider.complete("test")

        mock_record_error.assert_called_once()

    @pytest.mark.anyio
    async def test_successful_call_does_not_call_record_error(self, memory):
        """A successful complete() does NOT call _record_error."""
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="openrouter", model="test-model")

        with patch("nanoc.core.llm.Memory", return_value=memory), \
             patch.object(provider, "_openrouter_complete", new_callable=AsyncMock,
                          return_value="Good response"), \
             patch.object(provider, "_record_telemetry"), \
             patch.object(provider, "_record_error") as mock_record_error, \
             patch("nanoc.core.llm.settings") as mock_settings:
            mock_settings.DB_PATH = memory.db_path

            result = await provider.complete("test")

        mock_record_error.assert_not_called()
        assert result == "Good response"

    @pytest.mark.anyio
    async def test_ollama_exception_also_calls_record_error(self, memory):
        """An exception from _ollama_complete also triggers _record_error."""
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="ollama", model="llama2")

        with patch("nanoc.core.llm.Memory", return_value=memory), \
             patch.object(provider, "_ollama_complete", new_callable=AsyncMock,
                          side_effect=Exception("ollama down")), \
             patch.object(provider, "_record_error") as mock_record_error, \
             patch("nanoc.core.llm.settings") as mock_settings:
            mock_settings.DB_PATH = memory.db_path

            with pytest.raises(Exception):
                await provider.complete("prompt")

        mock_record_error.assert_called_once()


# ===========================================================================
# nanoc/agents/security.py – simplified event payload (no findings/vulnerabilities)
# ===========================================================================

class TestSecurityEventPayloadSimplified:
    @pytest.mark.anyio
    async def test_event_payload_has_no_findings_field(self, memory):
        """The security/audit-complete event payload does NOT contain 'findings'."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("SecAgent", memory)
        agent.llm = MockLLM()

        nmap_result = {
            "stdout": "22/tcp open ssh OpenSSH 8.2",
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
    async def test_event_payload_has_no_vulnerabilities_field(self, memory):
        """The security/audit-complete event payload does NOT contain 'vulnerabilities'."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("SecAgent", memory)
        agent.llm = MockLLM()

        nmap_result = {
            "stdout": "Telnet service detected\nAnonymous FTP",
            "stderr": "",
            "returncode": 0
        }

        with patch("nanoc.tools.network.AsyncRunner") as mock_runner_cls:
            mock_runner_cls.run_command = AsyncMock(return_value=nmap_result)
            await agent.audit_service("10.0.0.2")

        events = memory.get_events(topic="security/audit-complete")
        payload = json.loads(events[-1]["payload"])
        assert "vulnerabilities" not in payload

    @pytest.mark.anyio
    async def test_event_payload_contains_only_target_and_report(self, memory):
        """The security/audit-complete event payload contains only 'target' and 'report'."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("SecAgent", memory)
        agent.llm = MockLLM()

        nmap_result = {
            "stdout": "PORT  STATE SERVICE\n22/tcp open ssh",
            "stderr": "",
            "returncode": 0
        }

        with patch("nanoc.tools.network.AsyncRunner") as mock_runner_cls:
            mock_runner_cls.run_command = AsyncMock(return_value=nmap_result)
            await agent.audit_service("192.168.1.5")

        events = memory.get_events(topic="security/audit-complete")
        payload = json.loads(events[-1]["payload"])

        # Only 'target' and 'report' should be present
        assert set(payload.keys()) == {"target", "report"}

    @pytest.mark.anyio
    async def test_event_report_equals_nmap_stdout(self, memory):
        """The 'report' field in the event equals the nmap stdout."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("SecAgent", memory)
        agent.llm = MockLLM()

        stdout_content = "Nmap scan report for target\n80/tcp open http Apache 2.4"
        nmap_result = {"stdout": stdout_content, "stderr": "", "returncode": 0}

        with patch("nanoc.tools.network.AsyncRunner") as mock_runner_cls:
            mock_runner_cls.run_command = AsyncMock(return_value=nmap_result)
            await agent.audit_service("target-host")

        events = memory.get_events(topic="security/audit-complete")
        payload = json.loads(events[-1]["payload"])
        assert payload["report"] == stdout_content

    @pytest.mark.anyio
    async def test_even_with_telnet_in_output_no_findings_published(self, memory):
        """Telnet in nmap output no longer triggers vulnerability finding in event."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("SecAgent", memory)
        agent.llm = MockLLM()

        # Telnet service in output – old code would have added a finding
        nmap_result = {
            "stdout": "23/tcp open telnet",
            "stderr": "",
            "returncode": 0
        }

        with patch("nanoc.tools.network.AsyncRunner") as mock_runner_cls:
            mock_runner_cls.run_command = AsyncMock(return_value=nmap_result)
            await agent.audit_service("192.168.1.10")

        events = memory.get_events(topic="security/audit-complete")
        payload = json.loads(events[-1]["payload"])
        assert "findings" not in payload
        assert "vulnerabilities" not in payload

    @pytest.mark.anyio
    async def test_no_analysis_performed_on_report(self, memory):
        """No vulnerability analysis is applied – audit_service returns raw stdout."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("SecAgent", memory)
        agent.llm = MockLLM()

        raw_output = "Expired SSL certificate detected\nssh protocol 1.0 detected"
        nmap_result = {"stdout": raw_output, "stderr": "", "returncode": 0}

        with patch("nanoc.tools.network.AsyncRunner") as mock_runner_cls:
            mock_runner_cls.run_command = AsyncMock(return_value=nmap_result)
            result = await agent.audit_service("192.168.1.20")

        assert result == raw_output


# ===========================================================================
# nanoc/agents/base.py – project_id format change (no hex suffix)
# ===========================================================================

class TestTeamLeaderProjectIdFormat:
    @pytest.mark.anyio
    async def test_project_id_is_proj_timestamp_only(self, memory):
        """delegate_tasks generates project_id as 'proj_{timestamp}' with no hex suffix."""
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())

        with patch("nanoc.core.gate_manager.GateManager"):
            project_id = await leader.delegate_tasks("Build something")

        # Must match proj_ followed only by digits (no hex or underscore after)
        import re
        assert re.match(r'^proj_\d+$', project_id), \
            f"project_id '{project_id}' should match 'proj_<digits>' exactly"

    @pytest.mark.anyio
    async def test_project_id_has_no_hex_suffix(self, memory):
        """project_id does NOT contain a hex string suffix like '_1b931405'."""
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())

        with patch("nanoc.core.gate_manager.GateManager"):
            project_id = await leader.delegate_tasks("Network monitor")

        parts = project_id.split("_")
        # Should only have 2 parts: 'proj' and the timestamp
        assert len(parts) == 2, \
            f"project_id '{project_id}' should have exactly 2 parts (proj, timestamp)"

    @pytest.mark.anyio
    async def test_project_id_second_part_is_numeric(self, memory):
        """The timestamp part of the project_id is a pure integer (no hex chars)."""
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())

        with patch("nanoc.core.gate_manager.GateManager"):
            project_id = await leader.delegate_tasks("Test project")

        _, timestamp_part = project_id.split("_", 1)
        assert timestamp_part.isdigit(), \
            f"Expected numeric timestamp, got '{timestamp_part}'"

    @pytest.mark.anyio
    async def test_project_id_extracted_from_description_when_provided(self, memory):
        """When description includes 'proj_123: ...', that project_id is used."""
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())

        with patch("nanoc.core.gate_manager.GateManager"):
            project_id = await leader.delegate_tasks("proj_12345: Do something")

        assert project_id == "proj_12345"

    def test_base_agent_source_does_not_import_os(self):
        """base.py no longer imports 'os' (removed with hex suffix generation)."""
        import nanoc.agents.base as base_module
        src = inspect.getsource(base_module)
        # The 'os' import was removed since os.urandom is no longer used
        # We check that os.urandom is not called in project_id generation
        assert "os.urandom" not in src


# ===========================================================================
# nanoc/agents/base.py – handle_task methods removed
# ===========================================================================

class TestHandleTaskMethodsRemoved:
    def test_base_agent_has_no_handle_task(self, memory):
        """BaseAgent no longer has a handle_task method."""
        from nanoc.agents.base import BaseAgent
        agent = BaseAgent("agent1", "TestRole", memory, MockLLM())
        assert not hasattr(agent, "handle_task")

    def test_architect_has_no_handle_task(self, memory):
        """Architect no longer has a handle_task method."""
        from nanoc.agents.base import Architect
        agent = Architect("Arch1", "Architect", memory, MockLLM())
        assert not hasattr(agent, "handle_task")

    def test_planner_has_no_handle_task(self, memory):
        """Planner no longer has a handle_task method."""
        from nanoc.agents.base import Planner
        agent = Planner("Plan1", "Planner", memory, MockLLM())
        assert not hasattr(agent, "handle_task")

    def test_coder_has_no_handle_task(self, memory):
        """Coder no longer has a handle_task method."""
        from nanoc.agents.base import Coder
        agent = Coder("Coder1", "Coder", memory, MockLLM())
        assert not hasattr(agent, "handle_task")

    def test_reviewer_has_no_handle_task(self, memory):
        """Reviewer no longer has a handle_task method."""
        from nanoc.agents.base import Reviewer
        agent = Reviewer("Rev1", "Reviewer", memory, MockLLM())
        assert not hasattr(agent, "handle_task")

    def test_architect_still_has_design_solution(self, memory):
        """Architect still has design_solution method after handle_task removal."""
        from nanoc.agents.base import Architect
        agent = Architect("Arch1", "Architect", memory, MockLLM())
        assert hasattr(agent, "design_solution")

    def test_planner_still_has_create_todo_list(self, memory):
        """Planner still has create_todo_list method after handle_task removal."""
        from nanoc.agents.base import Planner
        agent = Planner("Plan1", "Planner", memory, MockLLM())
        assert hasattr(agent, "create_todo_list")

    def test_coder_still_has_write_code(self, memory):
        """Coder still has write_code method after handle_task removal."""
        from nanoc.agents.base import Coder
        agent = Coder("Coder1", "Coder", memory, MockLLM())
        assert hasattr(agent, "write_code")

    def test_reviewer_still_has_review_work(self, memory):
        """Reviewer still has review_work method after handle_task removal."""
        from nanoc.agents.base import Reviewer
        agent = Reviewer("Rev1", "Reviewer", memory, MockLLM())
        assert hasattr(agent, "review_work")


# ===========================================================================
# nanoc/core/orchestrator.py – explicit role-based dispatch
# ===========================================================================

class TestOrchestratorExplicitDispatch:
    @pytest.mark.anyio
    async def test_architect_role_calls_design_solution(self, memory):
        """process_task calls agent.design_solution() for 'Architect' role tasks."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_architect = MagicMock()
        mock_architect.role = "Architect"
        mock_architect.log = AsyncMock()
        mock_architect.design_solution = AsyncMock(return_value="architecture result")
        orch.add_agent(mock_architect)

        task_id = memory.create_task(
            "Design architecture for: service X",
            assigned_to="Architect",
            project_id="proj_disp1"
        )

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orch.process_task(task)

        mock_architect.design_solution.assert_called_once_with(task["description"])

    @pytest.mark.anyio
    async def test_planner_role_calls_create_todo_list(self, memory):
        """process_task calls agent.create_todo_list() for 'Planner' role tasks."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_planner = MagicMock()
        mock_planner.role = "Planner"
        mock_planner.log = AsyncMock()
        mock_planner.create_todo_list = AsyncMock(return_value="todo list result")
        orch.add_agent(mock_planner)

        task_id = memory.create_task(
            "proj_abc: Create task list for design: Microservices",
            assigned_to="Planner",
            project_id="proj_abc"
        )

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orch.process_task(task)

        mock_planner.create_todo_list.assert_called_once_with(task["description"])

    @pytest.mark.anyio
    async def test_coder_role_calls_write_code(self, memory):
        """process_task calls agent.write_code() for 'Coder' role tasks."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_coder = MagicMock()
        mock_coder.role = "Coder"
        mock_coder.log = AsyncMock()
        mock_coder.write_code = AsyncMock(return_value="def foo(): pass")
        orch.add_agent(mock_coder)

        task_id = memory.create_task(
            "proj_code: Write cache module",
            assigned_to="Coder",
            project_id="proj_code"
        )

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orch.process_task(task)

        mock_coder.write_code.assert_called_once_with(task["description"])

    @pytest.mark.anyio
    async def test_reviewer_role_calls_review_work(self, memory):
        """process_task calls agent.review_work() for 'Reviewer' role tasks."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_reviewer = MagicMock()
        mock_reviewer.role = "Reviewer"
        mock_reviewer.log = AsyncMock()
        mock_reviewer.review_work = AsyncMock(return_value="STATUS: APPROVED everything looks good")
        orch.add_agent(mock_reviewer)

        task_id = memory.create_task(
            "proj_rev: Review this code: def foo(): pass",
            assigned_to="Reviewer",
            project_id="proj_rev"
        )

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orch.process_task(task)

        mock_reviewer.review_work.assert_called_once_with(task["description"])

    @pytest.mark.anyio
    async def test_reviewer_not_approved_creates_fix_task_for_coder(self, memory):
        """When Reviewer returns without 'APPROVED', orchestrator creates a fix task for Coder."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_reviewer = MagicMock()
        mock_reviewer.role = "Reviewer"
        mock_reviewer.log = AsyncMock()
        mock_reviewer.review_work = AsyncMock(
            return_value="STATUS: FAILED missing error handling"
        )
        orch.add_agent(mock_reviewer)

        task_id = memory.create_task(
            "proj_fix: Review code snippet",
            assigned_to="Reviewer",
            project_id="proj_fix"
        )

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orch.process_task(task)

        # A fix task should have been created for the Coder
        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM tasks WHERE assigned_to = 'Coder' AND id != ?",
                (task_id,)
            )
            fix_tasks = [dict(row) for row in cursor.fetchall()]

        assert len(fix_tasks) >= 1
        assert any("Fix flaws" in t["description"] for t in fix_tasks)

    @pytest.mark.anyio
    async def test_reviewer_approved_does_not_create_fix_task(self, memory):
        """When Reviewer returns 'APPROVED', no fix task is created."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_reviewer = MagicMock()
        mock_reviewer.role = "Reviewer"
        mock_reviewer.log = AsyncMock()
        mock_reviewer.review_work = AsyncMock(return_value="STATUS: APPROVED looks perfect")
        orch.add_agent(mock_reviewer)

        task_id = memory.create_task(
            "proj_app: Review approved code",
            assigned_to="Reviewer",
            project_id="proj_app"
        )

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orch.process_task(task)

        # No new Coder fix task should have been created
        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM tasks WHERE assigned_to = 'Coder'",
            )
            coder_tasks = [dict(row) for row in cursor.fetchall()]

        assert len(coder_tasks) == 0

    @pytest.mark.anyio
    async def test_unknown_role_calls_agent_think(self, memory):
        """For an unrecognized role, process_task falls back to agent.think()."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_agent = MagicMock()
        mock_agent.role = "CustomRole"
        mock_agent.log = AsyncMock()
        mock_agent.think = AsyncMock(return_value="custom result")
        orch.add_agent(mock_agent)

        task_id = memory.create_task(
            "Do something custom",
            assigned_to="CustomRole",
            project_id="proj_custom"
        )

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orch.process_task(task)

        mock_agent.think.assert_called_once()
        call_arg = mock_agent.think.call_args[0][0]
        assert "Execute this task" in call_arg
        assert task["description"] in call_arg

    @pytest.mark.anyio
    async def test_successful_task_set_to_completed(self, memory):
        """process_task marks task as 'completed' on success."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_coder = MagicMock()
        mock_coder.role = "Coder"
        mock_coder.log = AsyncMock()
        mock_coder.write_code = AsyncMock(return_value="final code")
        orch.add_agent(mock_coder)

        task_id = memory.create_task(
            "proj_done: Write code",
            assigned_to="Coder",
            project_id="proj_done"
        )

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orch.process_task(task)

        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status, result FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()

        assert row[0] == "completed"
        assert row[1] == "final code"

    @pytest.mark.anyio
    async def test_fix_task_description_contains_original_task(self, memory):
        """The fix task description includes the original failing task description."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_reviewer = MagicMock()
        mock_reviewer.role = "Reviewer"
        mock_reviewer.log = AsyncMock()
        review_result = "STATUS: FAILED add input validation"
        mock_reviewer.review_work = AsyncMock(return_value=review_result)
        orch.add_agent(mock_reviewer)

        original_desc = "proj_orig: Review my special code"
        task_id = memory.create_task(original_desc, assigned_to="Reviewer", project_id="proj_orig")

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orch.process_task(task)

        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT description FROM tasks WHERE assigned_to = 'Coder'")
            fix_tasks = [dict(row) for row in cursor.fetchall()]

        assert len(fix_tasks) >= 1
        fix_desc = fix_tasks[0]["description"]
        assert original_desc in fix_desc

    @pytest.mark.anyio
    async def test_fix_task_project_id_matches_original(self, memory):
        """The fix task created by Reviewer failure has the same project_id."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_reviewer = MagicMock()
        mock_reviewer.role = "Reviewer"
        mock_reviewer.log = AsyncMock()
        mock_reviewer.review_work = AsyncMock(return_value="STATUS: FAILED")
        orch.add_agent(mock_reviewer)

        task_id = memory.create_task(
            "proj_projfix: Review code",
            assigned_to="Reviewer",
            project_id="proj_projfix"
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
            fix_tasks = [dict(row) for row in cursor.fetchall()]

        assert len(fix_tasks) >= 1
        assert fix_tasks[0]["project_id"] == "proj_projfix"
