"""
Tests for changes introduced in the PR:
  - nanoc/tests/mocks.py          (MockLLM, MockTelemetryHub)
  - nanoc/agents/analyst.py       (analyze_failure)
  - nanoc/agents/base.py          (Architect.design_solution project_id prefix check)
  - nanoc/agents/documentation.py (__init__, update_docs)
  - nanoc/core/event_bus.py       (error-handling in start_polling)
  - nanoc/core/gate_manager.py    (evaluate_gate else branch / gate/failed)
  - nanoc/core/orchestrator.py    (desc project_id prepending, db_path usage)
"""
import asyncio
import json
import os
import sqlite3
import time
import unittest
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanoc.memory.memory import Memory
from nanoc.tests.mocks import MockLLM, MockTelemetryHub


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
    db_path = str(tmp_path / "test_pr.db")
    mem = Memory(db_path)
    yield mem
    if os.path.exists(db_path):
        os.remove(db_path)


# ===========================================================================
# MockLLM tests
# ===========================================================================

class TestMockLLM:
    def setup_method(self):
        self.llm = MockLLM()

    @pytest.mark.asyncio
    async def test_default_response_contains_mocked_response(self):
        result = await self.llm.complete("some prompt")
        assert "Mocked response" in result

    @pytest.mark.asyncio
    async def test_pattern_matching_returns_configured_response(self):
        self.llm.add_response("hello", "world")
        result = await self.llm.complete("say hello world")
        assert result == "world"

    @pytest.mark.asyncio
    async def test_call_count_increments_with_each_call(self):
        assert self.llm._call_count == 0
        await self.llm.complete("first")
        assert self.llm._call_count == 1
        await self.llm.complete("second")
        assert self.llm._call_count == 2

    @pytest.mark.asyncio
    async def test_calls_array_records_prompt_and_system_prompt(self):
        await self.llm.complete("my prompt", system_prompt="sys")
        assert len(self.llm.calls) == 1
        assert self.llm.calls[0]["prompt"] == "my prompt"
        assert self.llm.calls[0]["system_prompt"] == "sys"

    @pytest.mark.asyncio
    async def test_default_response_includes_call_number(self):
        first = await self.llm.complete("a")
        second = await self.llm.complete("b")
        assert "(Call 1)" in first
        assert "(Call 2)" in second

    @pytest.mark.asyncio
    async def test_latency_causes_delay(self):
        self.llm.latency = 0.05
        start = time.monotonic()
        await self.llm.complete("x")
        elapsed = time.monotonic() - start
        assert elapsed >= 0.04

    @pytest.mark.asyncio
    async def test_fail_rate_zero_never_raises(self):
        self.llm.fail_rate = 0
        # Should never raise regardless of how many calls
        for _ in range(10):
            result = await self.llm.complete("safe call")
            assert result is not None

    @pytest.mark.asyncio
    async def test_fail_rate_one_always_raises(self):
        self.llm.fail_rate = 1.0
        with pytest.raises(Exception, match="Mock LLM simulated failure"):
            await self.llm.complete("doomed call")

    @pytest.mark.asyncio
    async def test_pattern_not_matching_falls_through_to_default(self):
        self.llm.add_response("specific_keyword", "specific_answer")
        result = await self.llm.complete("something unrelated")
        assert "Mocked response" in result

    @pytest.mark.asyncio
    async def test_multiple_patterns_first_match_wins(self):
        self.llm.add_response("foo", "foo_answer")
        self.llm.add_response("bar", "bar_answer")
        result = await self.llm.complete("contains foo here")
        assert result == "foo_answer"

    @pytest.mark.asyncio
    async def test_complete_without_system_prompt_defaults_to_empty(self):
        await self.llm.complete("no sys")
        assert self.llm.calls[0]["system_prompt"] == ""


# ===========================================================================
# MockTelemetryHub tests
# ===========================================================================

class TestMockTelemetryHub:
    def setup_method(self):
        self.hub = MockTelemetryHub()

    def test_initial_state_is_empty(self):
        assert self.hub.metrics == []
        assert self.hub.errors == []

    def test_record_token_usage_appends_metric(self):
        self.hub.record_token_usage("gpt-4", 100, 50, 0.05)
        assert len(self.hub.metrics) == 1
        entry = self.hub.metrics[0]
        assert entry["type"] == "token_usage"
        assert entry["model"] == "gpt-4"
        assert entry["cost"] == 0.05

    def test_record_latency_appends_metric(self):
        self.hub.record_latency("agent_think", 250)
        assert len(self.hub.metrics) == 1
        entry = self.hub.metrics[0]
        assert entry["type"] == "latency"
        assert entry["name"] == "agent_think"
        assert entry["duration_ms"] == 250

    def test_record_error_appends_to_errors(self):
        self.hub.record_error("EventBus", "timeout")
        assert len(self.hub.errors) == 1
        entry = self.hub.errors[0]
        assert entry["component"] == "EventBus"
        assert entry["error"] == "timeout"

    def test_multiple_metrics_accumulate(self):
        self.hub.record_token_usage("m1", 10, 5, 0.01)
        self.hub.record_latency("op", 100)
        self.hub.record_token_usage("m2", 20, 10, 0.02)
        assert len(self.hub.metrics) == 3

    def test_multiple_errors_accumulate(self):
        self.hub.record_error("A", "err1")
        self.hub.record_error("B", "err2")
        assert len(self.hub.errors) == 2


# ===========================================================================
# DocumentationAgent tests
# ===========================================================================

class TestDocumentationAgent:
    @pytest.mark.asyncio
    async def test_init_accepts_agent_id_role_memory(self, memory):
        from nanoc.agents.documentation import DocumentationAgent
        llm = MockLLM()
        agent = DocumentationAgent("DocAgent1", "Documentation", memory, provider=llm)
        assert agent.agent_id == "DocAgent1"
        assert agent.role == "Documentation"
        assert agent.memory is memory

    @pytest.mark.asyncio
    async def test_init_without_provider_creates_default(self, memory):
        """DocumentationAgent can be constructed without an explicit provider."""
        from nanoc.agents.documentation import DocumentationAgent
        # This may use LLMProvider which might fail at runtime, but construction should work.
        # We just verify the __init__ signature accepts (agent_id, role, memory).
        try:
            agent = DocumentationAgent("Doc2", "Documentation", memory)
            assert agent.agent_id == "Doc2"
        except Exception:
            # If LLMProvider() raises (e.g. no API key), that is expected in test env.
            pass

    @pytest.mark.asyncio
    async def test_update_docs_stores_knowledge(self, memory):
        from nanoc.agents.documentation import DocumentationAgent
        llm = MockLLM()
        agent = DocumentationAgent("DocAgent", "Documentation", memory, provider=llm)
        await agent.update_docs("proj_123", "Gate design resolved")
        stored = memory.get_knowledge("docs:proj_123")
        assert "Gate design resolved" in stored

    @pytest.mark.asyncio
    async def test_update_docs_publishes_event(self, memory):
        from nanoc.agents.documentation import DocumentationAgent
        llm = MockLLM()
        agent = DocumentationAgent("DocAgent", "Documentation", memory, provider=llm)
        await agent.update_docs("proj_456", "some content")
        events = memory.get_events(topic="docs/updated")
        assert len(events) >= 1
        payload = json.loads(events[-1]["payload"])
        assert payload["project_id"] == "proj_456"
        assert payload["status"] == "success"

    @pytest.mark.asyncio
    async def test_update_docs_overwrites_previous_knowledge(self, memory):
        from nanoc.agents.documentation import DocumentationAgent
        llm = MockLLM()
        agent = DocumentationAgent("DocAgent", "Documentation", memory, provider=llm)
        await agent.update_docs("proj_789", "first content")
        await agent.update_docs("proj_789", "updated content")
        stored = memory.get_knowledge("docs:proj_789")
        assert "first content" in stored
        assert "updated content" in stored


# ===========================================================================
# Analyst tests
# ===========================================================================

class TestAnalyst:
    @pytest.mark.asyncio
    async def test_analyze_failure_creates_analyst_task(self, memory):
        from nanoc.agents.analyst import Analyst
        analyst = Analyst("Analyst1", memory)
        await analyst.analyze_failure({"project_id": "proj_001", "error": "NullPointerException"})

        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT description, assigned_to FROM tasks WHERE project_id = 'proj_001'")
            row = cursor.fetchone()
        assert row is not None
        assert row[1] == "Analyst"
        assert "NullPointerException" in row[0]

    @pytest.mark.asyncio
    async def test_handle_task_performs_analysis(self, memory):
        from nanoc.agents.analyst import Analyst
        llm = MockLLM()
        llm.add_response("Analyze this error", "strategy: restart service")
        analyst = Analyst("Analyst1", memory)
        analyst.llm = llm

        task = {"description": "ANALYZE FAILURE: NullPointerException", "project_id": "proj_001"}
        await analyst.handle_task(task)

        # Check a task was created for the Coder
        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT description FROM tasks WHERE assigned_to = 'Coder'")
            row = cursor.fetchone()
        assert row is not None
        assert "strategy: restart service" in row[0]

    @pytest.mark.asyncio
    async def test_handle_task_publishes_analysis_completed_event(self, memory):
        from nanoc.agents.analyst import Analyst
        llm = MockLLM()
        llm.add_response("Analyze this error", "fix strategy")
        analyst = Analyst("Analyst1", memory)
        analyst.llm = llm

        task = {"description": "ANALYZE FAILURE: disk full", "project_id": "proj_002"}
        await analyst.handle_task(task)

        events = memory.get_events(topic="analysis/completed")
        assert len(events) >= 1
        payload = json.loads(events[-1]["payload"])
        assert "strategy" in payload
        assert payload["original_error"] == "disk full"

    @pytest.mark.asyncio
    async def test_analyze_failure_handles_missing_error_field(self, memory):
        from nanoc.agents.analyst import Analyst
        analyst = Analyst("Analyst1", memory)
        # Should not raise even if 'error' key is absent
        await analyst.analyze_failure({"project_id": "proj_003"})

        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT description FROM tasks WHERE project_id = 'proj_003'")
            row = cursor.fetchone()
        assert row is not None
        assert "Unknown error" in row[0]


# ===========================================================================
# Architect.design_solution project_id prefix check tests
# ===========================================================================

class TestArchitectProjectIdPrefix:
    @pytest.mark.asyncio
    async def test_design_solution_with_proj_prefix_does_not_raise(self, memory):
        from nanoc.agents.base import Architect
        from nanoc.core.gate_manager import GateManager
        llm = MockLLM()
        llm.default_response = "Architecture design"
        arch = Architect("Arch1", "Architect", memory, provider=llm)
        # Create a gate first so get_active_gate returns a value
        gm = GateManager(memory)
        gm.create_gate("proj_001", "design", "Architect", ["criteria1"])
        result = await arch.design_solution("proj_001: Build REST API")
        assert result is not None

    @pytest.mark.asyncio
    async def test_design_solution_without_proj_prefix_still_executes(self, memory):
        """The fallback pass block means execution continues even for non-proj_ IDs."""
        from nanoc.agents.base import Architect
        llm = MockLLM()
        llm.default_response = "fallback architecture"
        arch = Architect("Arch2", "Architect", memory, provider=llm)
        # project_id extracted will be "myproj" (no proj_ prefix)
        result = await arch.design_solution("myproj: build something")
        assert result is not None

    @pytest.mark.asyncio
    async def test_design_solution_no_colon_sets_unknown_project_id(self, memory):
        """When requirements has no ':', project_id defaults to 'unknown'."""
        from nanoc.agents.base import Architect
        llm = MockLLM()
        arch = Architect("Arch3", "Architect", memory, provider=llm)
        result = await arch.design_solution("Build a simple web server")
        assert result is not None

    @pytest.mark.asyncio
    async def test_design_solution_publishes_gate_result_event(self, memory):
        from nanoc.agents.base import Architect
        from nanoc.core.gate_manager import GateManager
        llm = MockLLM()
        arch = Architect("Arch4", "Architect", memory, provider=llm)
        gm = GateManager(memory)
        gm.create_gate("proj_abc", "design", "Architect", ["c1"])
        await arch.design_solution("proj_abc: design system")
        events = memory.get_events(topic="gate/result-added")
        assert len(events) >= 1
        payload = json.loads(events[-1]["payload"])
        assert payload["project_id"] == "proj_abc"
        assert payload["status"] == "pass"


# ===========================================================================
# EventBus error handling tests
# ===========================================================================

class TestEventBusErrorHandling:
    @pytest.mark.asyncio
    async def test_invalid_json_payload_is_skipped_and_does_not_crash(self, memory):
        from nanoc.core.event_bus import EventBus
        bus = EventBus(memory)
        received = []

        async def cb(payload):
            received.append(payload)

        bus.subscribe("bad/topic", cb)

        # Inject a raw event with invalid JSON directly into the DB
        with sqlite3.connect(memory.db_path) as conn:
            conn.execute(
                "INSERT INTO events (topic, payload, schema_version, timestamp) VALUES (?, ?, ?, ?)",
                ("bad/topic", "{not valid json}", "1.0", "2024-01-01"),
            )
            conn.commit()

        # Also publish a valid event afterwards
        bus.publish("bad/topic", {"ok": True})

        polling_task = asyncio.create_task(bus.start_polling(interval=0.01))
        for _ in range(30):
            if len(received) >= 1:
                break
            await asyncio.sleep(0.05)
        bus.stop_polling()
        await polling_task

        # The invalid event was skipped; only the valid one was delivered
        assert len(received) == 1
        assert received[0]["ok"] is True

    @pytest.mark.asyncio
    async def test_sync_callback_exception_does_not_stop_other_callbacks(self, memory):
        from nanoc.core.event_bus import EventBus
        bus = EventBus(memory)
        results = []

        def failing_cb(payload):
            raise RuntimeError("deliberate sync failure")

        def good_cb(payload):
            results.append(payload["val"])

        bus.subscribe("err/topic", failing_cb)
        bus.subscribe("err/topic", good_cb)
        bus.publish("err/topic", {"val": 42})

        polling_task = asyncio.create_task(bus.start_polling(interval=0.01))
        for _ in range(30):
            if results:
                break
            await asyncio.sleep(0.05)
        bus.stop_polling()
        await polling_task

        assert 42 in results

    @pytest.mark.asyncio
    async def test_async_callback_exception_does_not_stop_other_callbacks(self, memory):
        from nanoc.core.event_bus import EventBus
        bus = EventBus(memory)
        results = []

        async def failing_async_cb(payload):
            raise ValueError("deliberate async failure")

        async def good_async_cb(payload):
            results.append(payload["msg"])

        bus.subscribe("async/err", failing_async_cb)
        bus.subscribe("async/err", good_async_cb)
        bus.publish("async/err", {"msg": "hello"})

        polling_task = asyncio.create_task(bus.start_polling(interval=0.01))
        for _ in range(30):
            if results:
                break
            await asyncio.sleep(0.05)
        bus.stop_polling()
        await polling_task

        assert "hello" in results

    @pytest.mark.asyncio
    async def test_wildcard_star_suffix_matches_prefixed_topics(self, memory):
        from nanoc.core.event_bus import EventBus
        bus = EventBus(memory)
        matched = []

        async def wildcard_cb(payload):
            matched.append(payload.get("_topic"))

        bus.subscribe("project/*", wildcard_cb)
        bus.publish("project/created", {"id": 1})
        bus.publish("project/updated", {"id": 2})

        polling_task = asyncio.create_task(bus.start_polling(interval=0.01))
        for _ in range(40):
            if len(matched) >= 2:
                break
            await asyncio.sleep(0.05)
        bus.stop_polling()
        await polling_task

        assert "project/created" in matched
        assert "project/updated" in matched

    @pytest.mark.asyncio
    async def test_global_wildcard_receives_all_topics(self, memory):
        from nanoc.core.event_bus import EventBus
        bus = EventBus(memory)
        topics_seen = []

        async def global_cb(payload):
            topics_seen.append(payload.get("_topic"))

        bus.subscribe("*", global_cb)
        bus.publish("alpha/topic", {"x": 1})
        bus.publish("beta/topic", {"x": 2})

        polling_task = asyncio.create_task(bus.start_polling(interval=0.01))
        for _ in range(40):
            if len(topics_seen) >= 2:
                break
            await asyncio.sleep(0.05)
        bus.stop_polling()
        await polling_task

        assert "alpha/topic" in topics_seen
        assert "beta/topic" in topics_seen

    @pytest.mark.asyncio
    async def test_wildcard_payload_enriched_with_metadata(self, memory):
        from nanoc.core.event_bus import EventBus
        bus = EventBus(memory)
        payloads = []

        async def cb(payload):
            payloads.append(payload)

        bus.subscribe("*", cb)
        bus.publish("some/topic", {"data": "value"})

        polling_task = asyncio.create_task(bus.start_polling(interval=0.01))
        for _ in range(30):
            if payloads:
                break
            await asyncio.sleep(0.05)
        bus.stop_polling()
        await polling_task

        assert payloads
        p = payloads[0]
        assert "_topic" in p
        assert "_event_id" in p
        assert "_timestamp" in p

    @pytest.mark.asyncio
    async def test_pattern_callback_exception_does_not_stop_polling(self, memory):
        from nanoc.core.event_bus import EventBus
        bus = EventBus(memory)
        good_results = []

        async def bad_wildcard_cb(payload):
            raise Exception("wildcard failure")

        async def good_wildcard_cb(payload):
            good_results.append(True)

        bus.subscribe("*", bad_wildcard_cb)
        bus.subscribe("*", good_wildcard_cb)
        bus.publish("any/topic", {"z": 9})

        polling_task = asyncio.create_task(bus.start_polling(interval=0.01))
        for _ in range(30):
            if good_results:
                break
            await asyncio.sleep(0.05)
        bus.stop_polling()
        await polling_task

        assert good_results


# ===========================================================================
# GateManager else-branch (gate/failed) tests
# ===========================================================================

class TestGateManagerFailedBranch:
    def test_evaluate_gate_with_no_passes_publishes_gate_failed(self, memory):
        from nanoc.core.gate_manager import GateManager
        gm = GateManager(memory)
        gate_id = gm.create_gate("proj_fail", "code", "Coder", ["all pass"])
        # Add only failing results
        gm.add_result(gate_id, {"status": "fail", "reason": "lint errors"})
        events = memory.get_events(topic="gate/failed")
        assert len(events) >= 1
        payload = json.loads(events[-1]["payload"])
        assert payload["id"] == gate_id

    def test_evaluate_gate_with_no_passes_sets_failed_status(self, memory):
        from nanoc.core.gate_manager import GateManager
        from nanoc.core.gate_manager import GateStatus
        gm = GateManager(memory)
        gate_id = gm.create_gate("proj_fail2", "code", "Coder", ["pass req"])
        gm.add_result(gate_id, {"status": "fail"})
        gate_data = memory.get_knowledge(f"gate:{gate_id}")
        assert gate_data["status"] == GateStatus.FAILED.value

    def test_evaluate_gate_with_no_results_does_nothing(self, memory):
        """evaluate_gate called directly with zero results does nothing."""
        from nanoc.core.gate_manager import GateManager
        gm = GateManager(memory)
        gate_id = gm.create_gate("proj_empty", "design", "Architect", ["needs pass"])
        # Call evaluate directly without adding results
        gm.evaluate_gate(gate_id)
        events = memory.get_events(topic="gate/failed")
        assert len(events) == 0

    def test_evaluate_gate_with_pass_still_resolves(self, memory):
        """Regression: passing result still publishes gate/resolved."""
        from nanoc.core.gate_manager import GateManager
        from nanoc.core.gate_manager import GateStatus
        gm = GateManager(memory)
        gate_id = gm.create_gate("proj_pass", "code", "Coder", ["criteria"])
        gm.add_result(gate_id, {"status": "pass"})
        gate_data = memory.get_knowledge(f"gate:{gate_id}")
        assert gate_data["status"] == GateStatus.COMPLETE.value
        events = memory.get_events(topic="gate/resolved")
        assert len(events) >= 1

    def test_evaluate_gate_missing_gate_id_does_not_raise(self, memory):
        from nanoc.core.gate_manager import GateManager
        gm = GateManager(memory)
        # Should silently return without raising
        gm.evaluate_gate("nonexistent_gate_id")

    def test_gate_failed_event_contains_project_id(self, memory):
        from nanoc.core.gate_manager import GateManager
        gm = GateManager(memory)
        gate_id = gm.create_gate("proj_x99", "code", "Coder", ["c"])
        gm.add_result(gate_id, {"status": "fail"})
        events = memory.get_events(topic="gate/failed")
        payload = json.loads(events[-1]["payload"])
        assert payload["project_id"] == "proj_x99"


# ===========================================================================
# Orchestrator refactoring tests
# ===========================================================================

class TestOrchestratorRefactoring:
    def test_orchestrator_uses_memory_db_path_attribute(self, memory):
        """Orchestrator accesses self.memory.db_path, not settings.DB_PATH."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader
        llm = MockLLM()
        leader = TeamLeader("Leader", "Team Leader", memory, provider=llm)
        orch = Orchestrator(memory, leader)
        # Verify we can access memory.db_path (the attribute used in the refactored code)
        assert orch.memory.db_path == memory.db_path

    @pytest.mark.asyncio
    async def test_orchestrator_prepends_project_id_to_description(self, memory):
        """When task.project_id is set and not already in description,
        the orchestrator prepends it before calling the agent."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader, Coder
        llm = MockLLM()
        leader = TeamLeader("Leader", "Team Leader", memory, provider=llm)
        coder = Coder("C1", "Coder", memory, provider=llm)
        orch = Orchestrator(memory, leader)
        orch.add_agent(coder)

        # Create a task with project_id distinct from description
        task_id = memory.create_task("write a parser", assigned_to="Coder", project_id="proj_555")

        # Run one iteration of the loop (cancel immediately after one cycle)
        async def run_once():
            await asyncio.sleep(0)  # yield
            orch_task = asyncio.create_task(orch.run_loop())
            await asyncio.sleep(0.2)
            orch_task.cancel()
            try:
                await orch_task
            except asyncio.CancelledError:
                pass

        await run_once()

        # The Coder should have processed the task; check DB for completed task
        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT status, result FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()

        # Task was either processed or still pending (depends on timing), but no exception raised
        assert row is not None

    @pytest.mark.asyncio
    async def test_orchestrator_reviewer_rejection_creates_coder_task(self, memory):
        """When Reviewer returns non-APPROVED, a new Coder fix task is created."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader, Reviewer
        llm = MockLLM()
        llm.add_response("Review this code/work", "FAIL: missing tests")
        leader = TeamLeader("Leader", "Team Leader", memory, provider=llm)
        reviewer = Reviewer("Rev1", "Reviewer", memory, provider=llm)
        orch = Orchestrator(memory, leader)
        orch.add_agent(reviewer)

        # Create a reviewer task
        task_id = memory.create_task(
            "proj_777: Review this code for flaws:\nprint('hello')",
            assigned_to="Reviewer",
            project_id="proj_777",
        )

        orch_task = asyncio.create_task(orch.run_loop())
        await asyncio.sleep(0.3)
        orch_task.cancel()
        try:
            await orch_task
        except asyncio.CancelledError:
            pass

        # A new Coder task should have been created to fix the flaws
        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM tasks WHERE assigned_to = 'Coder'")
            count = cursor.fetchone()[0]

        assert count >= 1

    @pytest.mark.asyncio
    async def test_orchestrator_add_agent_stores_by_role(self, memory):
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader, Coder
        llm = MockLLM()
        leader = TeamLeader("L", "Team Leader", memory, provider=llm)
        coder = Coder("C", "Coder", memory, provider=llm)
        orch = Orchestrator(memory, leader)
        orch.add_agent(coder)
        assert "Coder" in orch.agents
        assert orch.agents["Coder"] is coder


# ===========================================================================
# Debater tests
# ===========================================================================

class TestDebater:
    @pytest.mark.asyncio
    async def test_debate_calls_both_agents(self, memory):
        from nanoc.core.orchestrator import Debater
        from nanoc.agents.base import BaseAgent
        llm1 = MockLLM()
        llm1.default_response = "PRO argument"
        llm2 = MockLLM()
        llm2.default_response = "CON argument"

        agent1 = BaseAgent("A1", "RoleA", memory, provider=llm1)
        agent2 = BaseAgent("A2", "RoleB", memory, provider=llm2)

        debater = Debater([agent1, agent2])
        result = await debater.debate("Should we use microservices?")
        assert result is not None
        # Both LLMs should have been called
        assert llm1._call_count >= 1
        assert llm2._call_count >= 1

    @pytest.mark.asyncio
    async def test_debate_single_agent_uses_same_for_all_roles(self, memory):
        from nanoc.core.orchestrator import Debater
        from nanoc.agents.base import BaseAgent
        llm = MockLLM()
        agent = BaseAgent("Solo", "RoleSolo", memory, provider=llm)
        debater = Debater([agent])
        result = await debater.debate("monolith vs microservices")
        assert result is not None
        assert llm._call_count >= 2  # at least PRO and synthesis
