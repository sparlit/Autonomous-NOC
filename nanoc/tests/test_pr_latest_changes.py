"""
Comprehensive tests for the latest PR changes, covering gaps not addressed
in test_pr_new_changes.py:

  - bulk_improve.py                   (new top-level script: file creation, content, naming)
  - nanoc/memory/memory.py            (create_task: priority param removed)
  - nanoc/core/llm.py                 (no retry logic: immediate failure on errors)
  - nanoc/core/orchestrator.py        (explicit role-based dispatch, Reviewer fix-task)
  - nanoc/agents/base.py              (handle_task removed from all agent subclasses)
  - nanoc/agents/security.py          (event payload has no findings/vulnerabilities)
  - nanoc/agents/base.TeamLeader      (project_id format: no hex suffix)
"""
import asyncio
import json
import os
import re
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
    db_path = str(tmp_path / "test_latest_pr.db")
    mem = Memory(db_path)
    yield mem
    if os.path.exists(db_path):
        os.remove(db_path)


# ===========================================================================
# bulk_improve.py – new top-level script
# ===========================================================================

class TestBulkImprove:
    def test_main_creates_inbox_directory(self, tmp_path):
        """main() calls os.makedirs for the inbox directory."""
        import bulk_improve

        with patch("bulk_improve.os.makedirs") as mock_makedirs, \
             patch("builtins.open", MagicMock()), \
             patch("bulk_improve.time.sleep"):
            bulk_improve.main()

        mock_makedirs.assert_called_once_with("nanoc/inbox", exist_ok=True)

    def test_main_creates_100_files(self, tmp_path):
        """main() creates exactly 100 task files in the inbox directory."""
        import bulk_improve

        created_files = []

        real_makedirs = os.makedirs

        def fake_open(path, mode="r"):
            created_files.append(path)
            m = MagicMock()
            m.__enter__ = lambda s: s
            m.__exit__ = MagicMock(return_value=False)
            m.write = MagicMock()
            return m

        with patch("bulk_improve.os.makedirs"), \
             patch("builtins.open", side_effect=fake_open), \
             patch("bulk_improve.time.sleep"):
            bulk_improve.main()

        assert len(created_files) == 100

    def test_main_files_have_bulk_task_prefix(self, tmp_path):
        """All created files have names starting with 'bulk_task_'."""
        import bulk_improve

        created_files = []

        def fake_open(path, mode="r"):
            created_files.append(path)
            m = MagicMock()
            m.__enter__ = lambda s: s
            m.__exit__ = MagicMock(return_value=False)
            m.write = MagicMock()
            return m

        with patch("bulk_improve.os.makedirs"), \
             patch("builtins.open", side_effect=fake_open), \
             patch("bulk_improve.time.sleep"):
            bulk_improve.main()

        for fpath in created_files:
            basename = os.path.basename(fpath)
            assert basename.startswith("bulk_task_"), f"Unexpected filename: {basename}"

    def test_main_file_names_include_index(self, tmp_path):
        """File names include the loop index (0..99) for uniqueness."""
        import bulk_improve

        created_files = []

        def fake_open(path, mode="r"):
            created_files.append(path)
            m = MagicMock()
            m.__enter__ = lambda s: s
            m.__exit__ = MagicMock(return_value=False)
            m.write = MagicMock()
            return m

        with patch("bulk_improve.os.makedirs"), \
             patch("builtins.open", side_effect=fake_open), \
             patch("bulk_improve.time.sleep"):
            bulk_improve.main()

        # Each file name should contain _0 through _99 as a suffix
        basenames = [os.path.basename(f) for f in created_files]
        # The pattern is bulk_task_{timestamp}_{i}.txt
        for i in range(100):
            suffix = f"_{i}.txt"
            matching = [b for b in basenames if b.endswith(suffix)]
            assert len(matching) >= 1, f"No file found ending with index _{i}.txt"

    def test_main_writes_task_description_to_files(self, tmp_path):
        """main() writes content containing 'NANOC' and 'FOSS' to each file."""
        import bulk_improve

        written_contents = []

        def fake_open(path, mode="r"):
            m = MagicMock()
            m.__enter__ = lambda s: s
            m.__exit__ = MagicMock(return_value=False)
            m.write = lambda content: written_contents.append(content)
            return m

        with patch("bulk_improve.os.makedirs"), \
             patch("builtins.open", side_effect=fake_open), \
             patch("bulk_improve.time.sleep"):
            bulk_improve.main()

        assert len(written_contents) == 100
        for content in written_contents:
            assert "NANOC" in content or "Autonomous Network" in content
            assert "FOSS" in content

    def test_main_files_written_to_inbox_directory(self, tmp_path):
        """All file paths start with the 'nanoc/inbox' directory prefix."""
        import bulk_improve

        created_files = []

        def fake_open(path, mode="r"):
            created_files.append(path)
            m = MagicMock()
            m.__enter__ = lambda s: s
            m.__exit__ = MagicMock(return_value=False)
            m.write = MagicMock()
            return m

        with patch("bulk_improve.os.makedirs"), \
             patch("builtins.open", side_effect=fake_open), \
             patch("bulk_improve.time.sleep"):
            bulk_improve.main()

        for fpath in created_files:
            assert fpath.startswith("nanoc/inbox"), f"File not in inbox: {fpath}"

    def test_main_sleeps_between_file_creations(self, tmp_path):
        """main() calls time.sleep(0.01) between each file creation."""
        import bulk_improve

        sleep_calls = []

        mock_file = MagicMock()
        mock_file.__enter__ = MagicMock(return_value=mock_file)
        mock_file.__exit__ = MagicMock(return_value=False)

        with patch("bulk_improve.os.makedirs"), \
             patch("builtins.open", return_value=mock_file), \
             patch("bulk_improve.time.sleep", side_effect=lambda s: sleep_calls.append(s)):
            bulk_improve.main()

        assert len(sleep_calls) == 100
        assert all(s == 0.01 for s in sleep_calls)

    def test_main_creates_files_on_real_filesystem(self, tmp_path):
        """Integration: main() actually writes 100 files in a real temp inbox directory."""
        import bulk_improve

        inbox_dir = tmp_path / "nanoc" / "inbox"
        inbox_dir.mkdir(parents=True, exist_ok=True)

        # We override the inbox_dir value inside main by patching os.path.join
        # to redirect to our tmp directory, and os.makedirs to be a no-op.
        real_join = os.path.join

        def redirected_join(directory, filename):
            # Redirect from "nanoc/inbox" to tmp_path
            if directory == "nanoc/inbox":
                return real_join(str(inbox_dir), filename)
            return real_join(directory, filename)

        with patch("bulk_improve.os.makedirs"), \
             patch("bulk_improve.os.path.join", side_effect=redirected_join), \
             patch("bulk_improve.time.sleep"):
            bulk_improve.main()

        files = list(inbox_dir.iterdir())
        assert len(files) == 100


# ===========================================================================
# nanoc/memory/memory.py – create_task priority param removed
# ===========================================================================

class TestMemoryCreateTaskPriorityRemoved:
    def test_create_task_accepts_basic_params(self, memory):
        """create_task accepts description, assigned_to, parent_id, project_id."""
        task_id = memory.create_task(
            "Test task",
            assigned_to="Coder",
            project_id="proj_test"
        )
        assert isinstance(task_id, int)
        assert task_id > 0

    def test_create_task_does_not_accept_priority_kwarg(self, memory):
        """create_task raises TypeError when called with a priority keyword argument."""
        with pytest.raises(TypeError):
            memory.create_task(
                "Task with priority",
                assigned_to="Coder",
                project_id="proj_p",
                priority=10
            )

    def test_create_task_default_priority_is_zero(self, memory):
        """Tasks created by create_task have priority=0 by default in the database."""
        task_id = memory.create_task("Test task", assigned_to="Coder", project_id="proj_p")

        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT priority FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()

        assert row[0] == 0

    def test_create_task_returns_incrementing_ids(self, memory):
        """Successive calls to create_task return distinct, incrementing IDs."""
        id1 = memory.create_task("Task 1", assigned_to="Coder")
        id2 = memory.create_task("Task 2", assigned_to="Coder")
        assert id2 > id1

    def test_create_task_stores_correct_description(self, memory):
        """create_task stores the exact description text in the database."""
        desc = "Unique task description XYZ123"
        task_id = memory.create_task(desc, assigned_to="Architect")

        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT description FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()

        assert row[0] == desc

    def test_create_task_initial_status_is_pending(self, memory):
        """create_task sets the initial status to 'pending'."""
        task_id = memory.create_task("Some task", assigned_to="Planner")

        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()

        assert row[0] == "pending"

    def test_create_task_without_priority_does_not_raise(self, memory):
        """Calling create_task without any priority parameter succeeds silently."""
        # This is the key behavioral test: the old signature had priority=0 as default,
        # the new one does NOT accept priority at all.
        task_id = memory.create_task("No priority task")
        assert task_id is not None

    def test_create_task_stores_project_id(self, memory):
        """create_task stores the provided project_id."""
        pid = "proj_test_123"
        task_id = memory.create_task("Task", project_id=pid)

        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT project_id FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()

        assert row[0] == pid

    def test_create_task_stores_assigned_to(self, memory):
        """create_task stores the provided assigned_to value."""
        task_id = memory.create_task("Task", assigned_to="Reviewer")

        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT assigned_to FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()

        assert row[0] == "Reviewer"


# ===========================================================================
# nanoc/core/llm.py – no retry logic (immediate failure)
# ===========================================================================

class TestLLMProviderNoRetry:
    @pytest.mark.anyio
    async def test_http_error_propagates_immediately(self, memory):
        """complete() does NOT retry on HTTP errors – exception propagates immediately."""
        import httpx
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="openrouter", model="test-model")
        call_count = [0]

        async def failing_openrouter(prompt, system_prompt, model):
            call_count[0] += 1
            raise httpx.HTTPStatusError("500 error", request=MagicMock(), response=MagicMock())

        with patch("nanoc.core.llm.Memory", return_value=memory), \
             patch.object(provider, "_openrouter_complete", side_effect=failing_openrouter), \
             patch.object(provider, "_record_error"), \
             patch("nanoc.core.llm.settings") as mock_settings:
            mock_settings.DB_PATH = memory.db_path
            with pytest.raises(httpx.HTTPStatusError):
                await provider.complete("test prompt")

        # Should have been called exactly once (no retry)
        assert call_count[0] == 1

    @pytest.mark.anyio
    async def test_request_error_propagates_immediately(self, memory):
        """complete() does NOT retry on network request errors."""
        import httpx
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="openrouter", model="test-model")
        call_count = [0]

        async def failing_openrouter(prompt, system_prompt, model):
            call_count[0] += 1
            raise httpx.RequestError("connection failed", request=MagicMock())

        with patch("nanoc.core.llm.Memory", return_value=memory), \
             patch.object(provider, "_openrouter_complete", side_effect=failing_openrouter), \
             patch.object(provider, "_record_error"), \
             patch("nanoc.core.llm.settings") as mock_settings:
            mock_settings.DB_PATH = memory.db_path
            with pytest.raises(httpx.RequestError):
                await provider.complete("test prompt")

        assert call_count[0] == 1

    @pytest.mark.anyio
    async def test_no_asyncio_sleep_called_on_failure(self, memory):
        """complete() does not call asyncio.sleep on failure (no retry delay)."""
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="openrouter", model="test-model")
        sleep_calls = []

        async def record_sleep(delay):
            sleep_calls.append(delay)

        async def failing_openrouter(prompt, system_prompt, model):
            raise RuntimeError("always fails")

        with patch("nanoc.core.llm.Memory", return_value=memory), \
             patch.object(provider, "_openrouter_complete", side_effect=failing_openrouter), \
             patch.object(provider, "_record_error"), \
             patch("asyncio.sleep", side_effect=record_sleep), \
             patch("nanoc.core.llm.settings") as mock_settings:
            mock_settings.DB_PATH = memory.db_path
            with pytest.raises(RuntimeError):
                await provider.complete("test prompt")

        assert len(sleep_calls) == 0

    @pytest.mark.anyio
    async def test_record_error_called_on_failure(self, memory):
        """complete() calls _record_error when an exception occurs."""
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="openrouter", model="test-model")
        recorded_errors = []

        async def failing_openrouter(prompt, system_prompt, model):
            raise RuntimeError("test error message")

        with patch("nanoc.core.llm.Memory", return_value=memory), \
             patch.object(provider, "_openrouter_complete", side_effect=failing_openrouter), \
             patch.object(provider, "_record_error", side_effect=lambda e: recorded_errors.append(e)), \
             patch("nanoc.core.llm.settings") as mock_settings:
            mock_settings.DB_PATH = memory.db_path
            with pytest.raises(RuntimeError):
                await provider.complete("some prompt")

        assert len(recorded_errors) == 1
        assert "test error message" in recorded_errors[0]

    @pytest.mark.anyio
    async def test_ollama_error_propagates_without_retry(self, memory):
        """complete() with ollama provider does NOT retry on failure."""
        import httpx
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="ollama", model="llama3")
        call_count = [0]

        async def failing_ollama(prompt, system_prompt, model):
            call_count[0] += 1
            raise httpx.ConnectError("connection refused")

        with patch("nanoc.core.llm.Memory", return_value=memory), \
             patch.object(provider, "_ollama_complete", side_effect=failing_ollama), \
             patch.object(provider, "_record_error"), \
             patch("nanoc.core.llm.settings") as mock_settings:
            mock_settings.DB_PATH = memory.db_path
            with pytest.raises(httpx.ConnectError):
                await provider.complete("test")

        assert call_count[0] == 1

    @pytest.mark.anyio
    async def test_telemetry_recorded_on_success_not_on_failure(self, memory):
        """_record_telemetry is called only on success, not on failure."""
        from nanoc.core.llm import LLMProvider

        provider = LLMProvider(provider="openrouter", model="test-model")
        telemetry_calls = []

        async def failing_openrouter(prompt, system_prompt, model):
            raise RuntimeError("fail")

        with patch("nanoc.core.llm.Memory", return_value=memory), \
             patch.object(provider, "_openrouter_complete", side_effect=failing_openrouter), \
             patch.object(provider, "_record_error"), \
             patch.object(provider, "_record_telemetry", side_effect=lambda *a: telemetry_calls.append(a)), \
             patch("nanoc.core.llm.settings") as mock_settings:
            mock_settings.DB_PATH = memory.db_path
            with pytest.raises(RuntimeError):
                await provider.complete("prompt")

        assert len(telemetry_calls) == 0


# ===========================================================================
# nanoc/agents/base.py – handle_task removed from agent subclasses
# ===========================================================================

class TestHandleTaskRemoved:
    def test_base_agent_has_no_handle_task(self, memory):
        """BaseAgent does not have a handle_task method after PR."""
        from nanoc.agents.base import BaseAgent
        agent = BaseAgent("A1", "TestRole", memory, MockLLM())
        assert not hasattr(agent, "handle_task")

    def test_architect_has_no_handle_task(self, memory):
        """Architect does not have a handle_task method after PR."""
        from nanoc.agents.base import Architect
        agent = Architect("Arch1", "Architect", memory, MockLLM())
        assert not hasattr(agent, "handle_task")

    def test_planner_has_no_handle_task(self, memory):
        """Planner does not have a handle_task method after PR."""
        from nanoc.agents.base import Planner
        agent = Planner("Plan1", "Planner", memory, MockLLM())
        assert not hasattr(agent, "handle_task")

    def test_coder_has_no_handle_task(self, memory):
        """Coder does not have a handle_task method after PR."""
        from nanoc.agents.base import Coder
        agent = Coder("Code1", "Coder", memory, MockLLM())
        assert not hasattr(agent, "handle_task")

    def test_reviewer_has_no_handle_task(self, memory):
        """Reviewer does not have a handle_task method after PR."""
        from nanoc.agents.base import Reviewer
        agent = Reviewer("Rev1", "Reviewer", memory, MockLLM())
        assert not hasattr(agent, "handle_task")

    def test_architect_still_has_design_solution(self, memory):
        """Architect retains its design_solution method."""
        from nanoc.agents.base import Architect
        agent = Architect("Arch1", "Architect", memory, MockLLM())
        assert hasattr(agent, "design_solution")
        assert callable(agent.design_solution)

    def test_planner_still_has_create_todo_list(self, memory):
        """Planner retains its create_todo_list method."""
        from nanoc.agents.base import Planner
        agent = Planner("Plan1", "Planner", memory, MockLLM())
        assert hasattr(agent, "create_todo_list")
        assert callable(agent.create_todo_list)

    def test_coder_still_has_write_code(self, memory):
        """Coder retains its write_code method."""
        from nanoc.agents.base import Coder
        agent = Coder("Code1", "Coder", memory, MockLLM())
        assert hasattr(agent, "write_code")
        assert callable(agent.write_code)

    def test_reviewer_still_has_review_work(self, memory):
        """Reviewer retains its review_work method."""
        from nanoc.agents.base import Reviewer
        agent = Reviewer("Rev1", "Reviewer", memory, MockLLM())
        assert hasattr(agent, "review_work")
        assert callable(agent.review_work)


# ===========================================================================
# nanoc/agents/base.py – TeamLeader project_id format (no hex suffix)
# ===========================================================================

class TestTeamLeaderProjectIdFormat:
    @pytest.mark.anyio
    async def test_project_id_has_no_hex_suffix(self, memory):
        """delegate_tasks produces project_id without hex suffix (format: proj_{digits})."""
        from nanoc.agents.base import TeamLeader

        with patch("nanoc.core.gate_manager.GateManager"):
            leader = TeamLeader("Leader1", "Team Leader", memory, MockLLM())
            project_id = await leader.delegate_tasks("Build something")

        # Should match proj_{digits} exactly - no additional _hex segment
        assert re.match(r"^proj_\d+$", project_id), \
            f"project_id '{project_id}' does not match expected pattern proj_<digits>"

    @pytest.mark.anyio
    async def test_project_id_format_is_proj_timestamp(self, memory):
        """project_id follows the exact format: proj_{integer_timestamp}."""
        from nanoc.agents.base import TeamLeader

        with patch("nanoc.core.gate_manager.GateManager"), \
             patch("nanoc.agents.base.datetime") as mock_dt:
            mock_dt.now.return_value.timestamp.return_value = 1700000042.5
            leader = TeamLeader("Leader1", "Team Leader", memory, MockLLM())
            project_id = await leader.delegate_tasks("Build something")

        assert project_id == "proj_1700000042"

    @pytest.mark.anyio
    async def test_project_id_extracted_from_description_when_present(self, memory):
        """delegate_tasks extracts existing project_id from 'proj_xxx: ...' descriptions."""
        from nanoc.agents.base import TeamLeader

        with patch("nanoc.core.gate_manager.GateManager"):
            leader = TeamLeader("Leader1", "Team Leader", memory, MockLLM())
            project_id = await leader.delegate_tasks("proj_existing123: Do some work")

        assert project_id == "proj_existing123"

    @pytest.mark.anyio
    async def test_project_id_no_hex_pattern_regression(self, memory):
        """Regression: project_id must NOT contain an underscore-separated hex suffix."""
        from nanoc.agents.base import TeamLeader

        with patch("nanoc.core.gate_manager.GateManager"):
            leader = TeamLeader("Leader1", "Team Leader", memory, MockLLM())
            project_id = await leader.delegate_tasks("Test project")

        # The old format was proj_{ts}_{hex8}, new format is just proj_{ts}
        parts = project_id.split("_")
        # Should have exactly 2 parts: "proj" and the timestamp
        assert len(parts) == 2, \
            f"project_id '{project_id}' has unexpected format (old hex suffix?)"


# ===========================================================================
# nanoc/core/orchestrator.py – explicit role-based dispatch
# ===========================================================================

class TestOrchestratorRoleDispatch:
    @pytest.mark.anyio
    async def test_architect_role_dispatches_to_design_solution(self, memory):
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
            "Design architecture for: test system",
            assigned_to="Architect",
            project_id="proj_dispatch"
        )
        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orch.process_task(task)

        mock_agent.design_solution.assert_called_once_with(task["description"])

    @pytest.mark.anyio
    async def test_planner_role_dispatches_to_create_todo_list(self, memory):
        """process_task calls agent.create_todo_list() for 'Planner' role."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_agent = MagicMock()
        mock_agent.role = "Planner"
        mock_agent.log = AsyncMock()
        mock_agent.create_todo_list = AsyncMock(return_value="TODO list")
        orch.add_agent(mock_agent)

        task_id = memory.create_task(
            "Create task list for: test",
            assigned_to="Planner",
            project_id="proj_planner"
        )
        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orch.process_task(task)

        mock_agent.create_todo_list.assert_called_once_with(task["description"])

    @pytest.mark.anyio
    async def test_coder_role_dispatches_to_write_code(self, memory):
        """process_task calls agent.write_code() for 'Coder' role."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_agent = MagicMock()
        mock_agent.role = "Coder"
        mock_agent.log = AsyncMock()
        mock_agent.write_code = AsyncMock(return_value="def hello(): pass")
        orch.add_agent(mock_agent)

        task_id = memory.create_task(
            "Write hello world function",
            assigned_to="Coder",
            project_id="proj_coder"
        )
        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orch.process_task(task)

        mock_agent.write_code.assert_called_once_with(task["description"])

    @pytest.mark.anyio
    async def test_reviewer_role_dispatches_to_review_work(self, memory):
        """process_task calls agent.review_work() for 'Reviewer' role."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_agent = MagicMock()
        mock_agent.role = "Reviewer"
        mock_agent.log = AsyncMock()
        mock_agent.review_work = AsyncMock(return_value="STATUS: APPROVED")
        orch.add_agent(mock_agent)

        task_id = memory.create_task(
            "Review this code: def foo(): pass",
            assigned_to="Reviewer",
            project_id="proj_reviewer"
        )
        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orch.process_task(task)

        mock_agent.review_work.assert_called_once_with(task["description"])

    @pytest.mark.anyio
    async def test_unknown_role_dispatches_to_think(self, memory):
        """process_task calls agent.think() for unrecognized roles."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_agent = MagicMock()
        mock_agent.role = "CustomRole"
        mock_agent.log = AsyncMock()
        mock_agent.think = AsyncMock(return_value="thought result")
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
        think_prompt = mock_agent.think.call_args[0][0]
        assert "Do something custom" in think_prompt

    @pytest.mark.anyio
    async def test_completed_task_status_set_after_success(self, memory):
        """process_task sets task status to 'completed' after successful execution."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_agent = MagicMock()
        mock_agent.role = "Coder"
        mock_agent.log = AsyncMock()
        mock_agent.write_code = AsyncMock(return_value="# code")
        orch.add_agent(mock_agent)

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


# ===========================================================================
# nanoc/core/orchestrator.py – Reviewer fix-task creation
# ===========================================================================

class TestOrchestratorReviewerFixTask:
    @pytest.mark.anyio
    async def test_reviewer_approved_does_not_create_fix_task(self, memory):
        """When Reviewer returns 'APPROVED', no fix task is created."""
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
            "Review this code",
            assigned_to="Reviewer",
            project_id="proj_approved"
        )
        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        with patch.object(memory, "create_task", wraps=memory.create_task) as mock_create:
            await orch.process_task(task)

        # Only the original task creation was called (before the test)
        # After process_task, no new create_task call should happen
        mock_create.assert_not_called()

    @pytest.mark.anyio
    async def test_reviewer_not_approved_creates_fix_task(self, memory):
        """When Reviewer result lacks 'APPROVED', orchestrator creates a fix task for Coder."""
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
            "Review this code: def foo(): pass",
            assigned_to="Reviewer",
            project_id="proj_failed_review"
        )
        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = dict(cursor.fetchone())

        await orch.process_task(task)

        # Check that a fix task was created assigned to Coder
        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM tasks WHERE assigned_to = 'Coder' AND id != ?",
                (task_id,)
            )
            fix_tasks = [dict(row) for row in cursor.fetchall()]

        assert len(fix_tasks) >= 1

    @pytest.mark.anyio
    async def test_reviewer_fix_task_description_contains_review_result(self, memory):
        """The fix task description includes the reviewer's feedback."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        review_result = "STATUS: FAILED - needs input validation"

        mock_agent = MagicMock()
        mock_agent.role = "Reviewer"
        mock_agent.log = AsyncMock()
        mock_agent.review_work = AsyncMock(return_value=review_result)
        orch.add_agent(mock_agent)

        task_id = memory.create_task(
            "Review the authentication module",
            assigned_to="Reviewer",
            project_id="proj_review_desc"
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
                "SELECT description FROM tasks WHERE assigned_to = 'Coder' AND id != ?",
                (task_id,)
            )
            fix_tasks = [dict(row) for row in cursor.fetchall()]

        assert len(fix_tasks) >= 1
        assert "needs input validation" in fix_tasks[0]["description"]

    @pytest.mark.anyio
    async def test_reviewer_fix_task_uses_original_project_id(self, memory):
        """The fix task created by orchestrator uses the original task's project_id."""
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
            "Review this code",
            assigned_to="Reviewer",
            project_id="proj_fix_pid"
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
                "SELECT project_id FROM tasks WHERE assigned_to = 'Coder' AND id != ?",
                (task_id,)
            )
            rows = cursor.fetchall()

        assert len(rows) >= 1
        assert rows[0]["project_id"] == "proj_fix_pid"

    @pytest.mark.anyio
    async def test_reviewer_fix_task_description_contains_original_task(self, memory):
        """The fix task description references the original task description."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        original_desc = "Review the login handler code"

        mock_agent = MagicMock()
        mock_agent.role = "Reviewer"
        mock_agent.log = AsyncMock()
        mock_agent.review_work = AsyncMock(return_value="STATUS: FAILED - no tests")
        orch.add_agent(mock_agent)

        task_id = memory.create_task(
            original_desc,
            assigned_to="Reviewer",
            project_id="proj_orig_task"
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
                "SELECT description FROM tasks WHERE assigned_to = 'Coder' AND id != ?",
                (task_id,)
            )
            rows = cursor.fetchall()

        assert len(rows) >= 1
        assert original_desc in rows[0]["description"]

    @pytest.mark.anyio
    async def test_reviewer_approved_task_marked_completed(self, memory):
        """When Reviewer approves, the task is marked 'completed' in DB."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)

        mock_agent = MagicMock()
        mock_agent.role = "Reviewer"
        mock_agent.log = AsyncMock()
        mock_agent.review_work = AsyncMock(return_value="STATUS: APPROVED everything is fine")
        orch.add_agent(mock_agent)

        task_id = memory.create_task(
            "Review the module",
            assigned_to="Reviewer",
            project_id="proj_complete"
        )
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


# ===========================================================================
# nanoc/agents/security.py – event payload has no findings/vulnerabilities
# ===========================================================================

class TestSecurityAgentEventPayloadSimplified:
    @pytest.mark.anyio
    async def test_audit_complete_event_has_no_findings_field(self, memory):
        """After PR, 'security/audit-complete' event payload does NOT include 'findings'."""
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
            await agent.audit_service("192.168.1.100")

        events = memory.get_events(topic="security/audit-complete")
        assert len(events) >= 1
        payload = json.loads(events[-1]["payload"])
        assert "findings" not in payload

    @pytest.mark.anyio
    async def test_audit_complete_event_has_no_vulnerabilities_field(self, memory):
        """After PR, 'security/audit-complete' event payload does NOT include 'vulnerabilities'."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("Sec1", memory)
        agent.llm = MockLLM()

        nmap_result = {
            "stdout": "23/tcp open telnet",
            "stderr": "",
            "returncode": 0
        }

        with patch("nanoc.tools.network.AsyncRunner") as mock_runner:
            mock_runner.run_command = AsyncMock(return_value=nmap_result)
            await agent.audit_service("10.0.0.50")

        events = memory.get_events(topic="security/audit-complete")
        assert len(events) >= 1
        payload = json.loads(events[-1]["payload"])
        assert "vulnerabilities" not in payload

    @pytest.mark.anyio
    async def test_audit_complete_event_payload_has_only_target_and_report(self, memory):
        """The 'security/audit-complete' event payload contains exactly 'target' and 'report'."""
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
    async def test_telnet_scan_does_not_add_findings_to_event(self, memory):
        """Even when telnet is detected in output, no vulnerability analysis in event."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("Sec1", memory)
        agent.llm = MockLLM()

        nmap_result = {
            "stdout": "23/tcp open telnet  Linux telnetd",
            "stderr": "",
            "returncode": 0
        }

        with patch("nanoc.tools.network.AsyncRunner") as mock_runner:
            mock_runner.run_command = AsyncMock(return_value=nmap_result)
            await agent.audit_service("192.168.0.10")

        events = memory.get_events(topic="security/audit-complete")
        payload = json.loads(events[-1]["payload"])
        # After the PR, vulnerability analysis logic was removed
        assert "findings" not in payload
        assert "vulnerabilities" not in payload
        # But report is still present
        assert "report" in payload

    @pytest.mark.anyio
    async def test_expired_ssl_scan_no_findings_in_event(self, memory):
        """Expired SSL in output does not trigger vulnerability findings in event payload."""
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("Sec1", memory)
        agent.llm = MockLLM()

        nmap_result = {
            "stdout": "443/tcp open ssl/https -- certificate expired",
            "stderr": "",
            "returncode": 0
        }

        with patch("nanoc.tools.network.AsyncRunner") as mock_runner:
            mock_runner.run_command = AsyncMock(return_value=nmap_result)
            await agent.audit_service("10.0.0.99")

        events = memory.get_events(topic="security/audit-complete")
        payload = json.loads(events[-1]["payload"])
        assert "findings" not in payload
        assert "vulnerabilities" not in payload
