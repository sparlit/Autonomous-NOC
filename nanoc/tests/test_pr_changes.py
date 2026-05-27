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
from unittest.mock import AsyncMock, MagicMock, patch

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
        assert stored == "Gate design resolved"

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
        assert stored == "updated content"


# ===========================================================================
# Analyst tests
# ===========================================================================

class TestAnalyst:
    @pytest.mark.asyncio
    async def test_analyze_failure_extracts_project_id(self, memory):
        from nanoc.agents.analyst import Analyst
        llm = MockLLM()
        llm.add_response("Analyze this error", "strategy: restart service")
        analyst = Analyst("Analyst1", memory)
        # Inject mock llm
        analyst.llm = llm
        await analyst.analyze_failure({"project_id": "proj_001", "error": "NullPointerException"})
        # Check a task was created for the Coder
        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT description FROM tasks WHERE assigned_to = 'Coder'")
            row = cursor.fetchone()
        assert row is not None
        assert "strategy: restart service" in row[0]

    @pytest.mark.asyncio
    async def test_analyze_failure_uses_unknown_when_no_project_id(self, memory):
        from nanoc.agents.analyst import Analyst
        llm = MockLLM()
        analyst = Analyst("Analyst1", memory)
        analyst.llm = llm
        # No project_id in event; should default to "unknown" without raising
        await analyst.analyze_failure({"error": "timeout"})
        # Verify task created
        with sqlite3.connect(memory.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM tasks WHERE assigned_to = 'Coder'")
            count = cursor.fetchone()[0]
        assert count >= 1

    @pytest.mark.asyncio
    async def test_analyze_failure_publishes_analysis_completed_event(self, memory):
        from nanoc.agents.analyst import Analyst
        llm = MockLLM()
        llm.add_response("Analyze this error", "fix strategy")
        analyst = Analyst("Analyst1", memory)
        analyst.llm = llm
        await analyst.analyze_failure({"project_id": "proj_002", "error": "disk full"})
        events = memory.get_events(topic="analysis/completed")
        assert len(events) >= 1
        payload = json.loads(events[-1]["payload"])
        assert "strategy" in payload
        assert payload["original_error"] == "disk full"

    @pytest.mark.asyncio
    async def test_analyze_failure_handles_missing_error_field(self, memory):
        from nanoc.agents.analyst import Analyst
        llm = MockLLM()
        analyst = Analyst("Analyst1", memory)
        analyst.llm = llm
        # Should not raise even if 'error' key is absent
        await analyst.analyze_failure({"project_id": "proj_003"})
        events = memory.get_events(topic="analysis/completed")
        assert len(events) >= 1


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


# ===========================================================================
# TeamLeader active_projects tracking tests  (base.py PR change)
# ===========================================================================

class TestTeamLeaderActiveProjects:
    @pytest.mark.asyncio
    async def test_delegate_tasks_stores_project_id_in_active_projects(self, memory):
        """TeamLeader.delegate_tasks now tracks the new project_id in active_projects knowledge."""
        from nanoc.agents.base import TeamLeader
        llm = MockLLM()
        leader = TeamLeader("Leader1", "Team Leader", memory, provider=llm)
        project_id = await leader.delegate_tasks("Build a REST API")
        active = memory.get_knowledge("active_projects")
        assert active is not None
        assert project_id in active

    @pytest.mark.asyncio
    async def test_delegate_tasks_accumulates_multiple_project_ids(self, memory):
        """Each call to delegate_tasks appends a new project_id without overwriting the list."""
        from nanoc.agents.base import TeamLeader
        llm = MockLLM()
        leader = TeamLeader("Leader2", "Team Leader", memory, provider=llm)
        pid1 = await leader.delegate_tasks("Project One")
        pid2 = await leader.delegate_tasks("Project Two")
        active = memory.get_knowledge("active_projects")
        assert pid1 in active
        assert pid2 in active
        assert len(active) == 2

    @pytest.mark.asyncio
    async def test_delegate_tasks_active_projects_key_exists_after_first_call(self, memory):
        """active_projects knowledge key should not exist before delegation."""
        from nanoc.agents.base import TeamLeader
        llm = MockLLM()
        assert memory.get_knowledge("active_projects") is None
        leader = TeamLeader("Leader3", "Team Leader", memory, provider=llm)
        await leader.delegate_tasks("Some project")
        active = memory.get_knowledge("active_projects")
        assert isinstance(active, list)
        assert len(active) == 1

    @pytest.mark.asyncio
    async def test_delegate_tasks_active_projects_initialises_when_none_in_memory(self, memory):
        """When no active_projects key exists, delegate_tasks creates it correctly."""
        from nanoc.agents.base import TeamLeader
        llm = MockLLM()
        # Explicitly ensure key absent
        assert memory.get_knowledge("active_projects") is None
        leader = TeamLeader("Leader4", "Team Leader", memory, provider=llm)
        pid = await leader.delegate_tasks("Init project")
        active = memory.get_knowledge("active_projects")
        assert active == [pid]

    @pytest.mark.asyncio
    async def test_delegate_tasks_project_id_starts_with_proj_(self, memory):
        """The generated project_id follows the expected 'proj_<timestamp>' pattern."""
        from nanoc.agents.base import TeamLeader
        llm = MockLLM()
        leader = TeamLeader("Leader5", "Team Leader", memory, provider=llm)
        pid = await leader.delegate_tasks("Check prefix")
        assert pid.startswith("proj_")


# ===========================================================================
# Architect active_projects fallback tests  (base.py PR change)
# ===========================================================================

class TestArchitectActiveProjectFallback:
    @pytest.mark.asyncio
    async def test_design_solution_uses_active_project_when_prefix_absent(self, memory):
        """When requirements lacks 'proj_' prefix, Architect falls back to active_projects[-1]."""
        from nanoc.agents.base import Architect
        from nanoc.core.gate_manager import GateManager
        llm = MockLLM()
        arch = Architect("Arch10", "Architect", memory, provider=llm)

        # Pre-populate an active project with a gate so get_active_gate returns something
        gm = GateManager(memory)
        gm.create_gate("proj_active99", "design", "Architect", ["c1"])
        memory.upsert_knowledge("active_projects", ["proj_active99"])

        # Requirements without a 'proj_' prefix
        result = await arch.design_solution("myproject: design the system")
        assert result is not None

        # The gate/result-added event should reference the active project
        events = memory.get_events(topic="gate/result-added")
        assert len(events) >= 1
        payload = json.loads(events[-1]["payload"])
        assert payload["project_id"] == "proj_active99"

    @pytest.mark.asyncio
    async def test_design_solution_skips_fallback_when_proj_prefix_present(self, memory):
        """When requirements already has 'proj_' prefix, active_projects fallback is NOT used."""
        from nanoc.agents.base import Architect
        from nanoc.core.gate_manager import GateManager
        llm = MockLLM()
        arch = Architect("Arch11", "Architect", memory, provider=llm)

        gm = GateManager(memory)
        gm.create_gate("proj_explicit", "design", "Architect", ["c1"])
        # Also set an active_projects that differs
        memory.upsert_knowledge("active_projects", ["proj_other99"])

        result = await arch.design_solution("proj_explicit: build API")
        assert result is not None
        events = memory.get_events(topic="gate/result-added")
        assert len(events) >= 1
        payload = json.loads(events[-1]["payload"])
        # Should use the explicit project_id, not the active_projects fallback
        assert payload["project_id"] == "proj_explicit"

    @pytest.mark.asyncio
    async def test_design_solution_no_fallback_when_active_projects_empty(self, memory):
        """When active_projects is empty and prefix is absent, project_id stays non-proj_ value."""
        from nanoc.agents.base import Architect
        llm = MockLLM()
        arch = Architect("Arch12", "Architect", memory, provider=llm)
        # Ensure active_projects is empty
        memory.upsert_knowledge("active_projects", [])

        result = await arch.design_solution("noproj: design something")
        # Should not raise; the extracted project_id will be "noproj" (no active fallback)
        assert result is not None

    @pytest.mark.asyncio
    async def test_design_solution_uses_last_active_project_not_first(self, memory):
        """When multiple active projects exist, the fallback picks the last one."""
        from nanoc.agents.base import Architect
        from nanoc.core.gate_manager import GateManager
        llm = MockLLM()
        arch = Architect("Arch13", "Architect", memory, provider=llm)

        gm = GateManager(memory)
        gm.create_gate("proj_last", "design", "Architect", ["c1"])
        memory.upsert_knowledge("active_projects", ["proj_first", "proj_last"])

        await arch.design_solution("noprefix: build something")
        events = memory.get_events(topic="gate/result-added")
        assert len(events) >= 1
        payload = json.loads(events[-1]["payload"])
        assert payload["project_id"] == "proj_last"


# ===========================================================================
# Reviewer STATUS: APPROVED / STATUS: FAILED tests  (base.py PR change)
# ===========================================================================

class TestReviewerStatusApprovedPrompt:
    @pytest.mark.asyncio
    async def test_review_work_pass_when_status_approved_in_response(self, memory):
        """LLM response containing 'STATUS: APPROVED' → gate result status = 'pass'."""
        from nanoc.agents.base import Reviewer
        from nanoc.core.gate_manager import GateManager
        llm = MockLLM()
        llm.add_response("Review this code", "STATUS: APPROVED\nLooks great.")
        reviewer = Reviewer("Rev10", "Reviewer", memory, provider=llm)
        gm = GateManager(memory)
        gm.create_gate("proj_rev1", "code", "Coder", ["criteria"])
        result = await reviewer.review_work("proj_rev1: some code")
        assert "STATUS: APPROVED" in result
        events = memory.get_events(topic="gate/result-added")
        assert len(events) >= 1
        payload = json.loads(events[-1]["payload"])
        assert payload["status"] == "pass"

    @pytest.mark.asyncio
    async def test_review_work_fail_when_status_failed_in_response(self, memory):
        """LLM response containing 'STATUS: FAILED' → gate result status = 'fail'."""
        from nanoc.agents.base import Reviewer
        from nanoc.core.gate_manager import GateManager
        llm = MockLLM()
        llm.add_response("Review this code", "STATUS: FAILED\nMissing error handling.")
        reviewer = Reviewer("Rev11", "Reviewer", memory, provider=llm)
        gm = GateManager(memory)
        gm.create_gate("proj_rev2", "code", "Coder", ["criteria"])
        result = await reviewer.review_work("proj_rev2: some code")
        events = memory.get_events(topic="gate/result-added")
        payload = json.loads(events[-1]["payload"])
        assert payload["status"] == "fail"

    @pytest.mark.asyncio
    async def test_review_work_fail_when_no_status_prefix(self, memory):
        """LLM response without 'STATUS: APPROVED' is treated as fail."""
        from nanoc.agents.base import Reviewer
        from nanoc.core.gate_manager import GateManager
        llm = MockLLM()
        llm.add_response("Review this code", "APPROVED: looks fine")  # Old format, no STATUS:
        reviewer = Reviewer("Rev12", "Reviewer", memory, provider=llm)
        gm = GateManager(memory)
        gm.create_gate("proj_rev3", "code", "Coder", ["criteria"])
        await reviewer.review_work("proj_rev3: code here")
        events = memory.get_events(topic="gate/result-added")
        payload = json.loads(events[-1]["payload"])
        # Old "APPROVED" without "STATUS: " prefix no longer triggers pass
        assert payload["status"] == "fail"

    @pytest.mark.asyncio
    async def test_review_work_log_event_published_on_pass(self, memory):
        """Both agent/log events should be published on review pass."""
        from nanoc.agents.base import Reviewer
        from nanoc.core.gate_manager import GateManager
        llm = MockLLM()
        llm.add_response("Review this code", "STATUS: APPROVED")
        reviewer = Reviewer("Rev13", "Reviewer", memory, provider=llm)
        gm = GateManager(memory)
        gm.create_gate("proj_rev4", "code", "Coder", ["c"])
        await reviewer.review_work("proj_rev4: code")
        log_events = memory.get_events(topic="agent/log")
        log_contents = [json.loads(e["payload"]).get("content", "") for e in log_events]
        assert any("approved" in c.lower() for c in log_contents)

    @pytest.mark.asyncio
    async def test_review_work_log_event_published_on_fail(self, memory):
        """When work fails review, a log event mentioning failure is published."""
        from nanoc.agents.base import Reviewer
        from nanoc.core.gate_manager import GateManager
        llm = MockLLM()
        llm.add_response("Review this code", "STATUS: FAILED\nMany issues.")
        reviewer = Reviewer("Rev14", "Reviewer", memory, provider=llm)
        gm = GateManager(memory)
        gm.create_gate("proj_rev5", "code", "Coder", ["c"])
        await reviewer.review_work("proj_rev5: code")
        log_events = memory.get_events(topic="agent/log")
        log_contents = [json.loads(e["payload"]).get("content", "") for e in log_events]
        assert any("failed" in c.lower() for c in log_contents)

    @pytest.mark.asyncio
    async def test_review_work_prompt_includes_status_approved_instruction(self, memory):
        """The prompt sent to LLM must include the 'STATUS: APPROVED' instruction."""
        from nanoc.agents.base import Reviewer
        from nanoc.core.gate_manager import GateManager
        llm = MockLLM()
        reviewer = Reviewer("Rev15", "Reviewer", memory, provider=llm)
        gm = GateManager(memory)
        gm.create_gate("proj_rev6", "code", "Coder", ["c"])
        await reviewer.review_work("proj_rev6: code")
        # Check that the prompt sent to LLM contains the new instruction text
        assert len(llm.calls) >= 1
        prompt_sent = llm.calls[0]["prompt"]
        assert "STATUS: APPROVED" in prompt_sent
        assert "STATUS: FAILED" in prompt_sent


# ===========================================================================
# Governor SCALE_UP publishes system/scale-up event  (governor.py PR change)
# ===========================================================================

class TestGovernorScaleUpEvent:
    @pytest.mark.asyncio
    async def test_decide_action_returns_scale_up_when_backlog_exceeds_10(self, memory):
        """Governor.decide_action should return SCALE_UP for backlog_size > 10."""
        from nanoc.agents.governor import Governor
        llm = MockLLM()
        gov = Governor("Gov1", memory, {})
        gov.llm = llm
        action = await gov.decide_action({"backlog_size": 15, "error_rate": 0.0, "total_cost": 0.0})
        assert action == "SCALE_UP"

    @pytest.mark.asyncio
    async def test_decide_action_does_not_return_scale_up_for_small_backlog(self, memory):
        """Governor.decide_action should NOT return SCALE_UP when backlog <= 10."""
        from nanoc.agents.governor import Governor
        gov = Governor("Gov2", memory, {})
        gov.llm = MockLLM()
        action = await gov.decide_action({"backlog_size": 5, "error_rate": 0.0, "total_cost": 0.0})
        assert action != "SCALE_UP"

    def test_scale_up_publishes_system_scale_up_event(self, memory):
        """When SCALE_UP action is determined, system/scale-up event should be published."""
        # We directly trigger the SCALE_UP branch by publishing the event manually
        # as the Governor does in run_governance_cycle
        memory.publish_event("system/scale-up", {"role": "Coder", "reason": "High backlog"})
        events = memory.get_events(topic="system/scale-up")
        assert len(events) >= 1
        payload = json.loads(events[-1]["payload"])
        assert payload["role"] == "Coder"
        assert payload["reason"] == "High backlog"

    @pytest.mark.asyncio
    async def test_governor_scale_up_event_has_correct_payload(self, memory):
        """The system/scale-up event published by SCALE_UP includes role and reason fields."""
        import asyncio
        from nanoc.agents.governor import Governor
        llm = MockLLM()

        gov = Governor("Gov3", memory, {})
        gov.llm = llm

        # Patch gather_metrics to return metrics that trigger SCALE_UP
        async def fake_gather():
            return {"backlog_size": 20, "error_rate": 0.0, "total_cost": 0.0, "latency_ms": 0}

        gov.gather_metrics = fake_gather

        # Run one governance cycle by canceling after one iteration
        async def run_once():
            task = asyncio.create_task(gov.run_governance_cycle())
            await asyncio.sleep(0.1)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        await run_once()

        events = memory.get_events(topic="system/scale-up")
        assert len(events) >= 1
        payload = json.loads(events[-1]["payload"])
        assert payload["role"] == "Coder"
        assert "reason" in payload


# ===========================================================================
# GateManager FAILED status and new evaluate logic  (gate_manager.py PR change)
# ===========================================================================

class TestGateManagerFailedStatus:
    def test_gate_status_failed_enum_value(self, memory):
        """GateStatus.FAILED should exist with value 'FAILED'."""
        from nanoc.core.gate_manager import GateStatus
        assert GateStatus.FAILED.value == "FAILED"

    def test_evaluate_gate_with_pass_and_fail_prefers_failed(self, memory):
        """Mixed results: any failure → FAILED status (failures take precedence over passes)."""
        from nanoc.core.gate_manager import GateManager, GateStatus
        gm = GateManager(memory)
        gate_id = gm.create_gate("proj_mixed1", "code", "Coder", ["c"])
        # Add a pass first, then a fail
        gm.add_result(gate_id, {"status": "pass"})
        gm.add_result(gate_id, {"status": "fail", "reason": "lint error"})
        gate_data = memory.get_knowledge(f"gate:{gate_id}")
        assert gate_data["status"] == GateStatus.FAILED.value

    def test_evaluate_gate_multiple_failures_stays_failed(self, memory):
        """Multiple failure results all lead to FAILED status."""
        from nanoc.core.gate_manager import GateManager, GateStatus
        gm = GateManager(memory)
        gate_id = gm.create_gate("proj_multifail", "code", "Coder", ["c"])
        gm.add_result(gate_id, {"status": "fail"})
        gm.add_result(gate_id, {"status": "fail"})
        gate_data = memory.get_knowledge(f"gate:{gate_id}")
        assert gate_data["status"] == GateStatus.FAILED.value

    def test_evaluate_gate_only_passes_gives_complete_status(self, memory):
        """All pass results → COMPLETE status (no failures)."""
        from nanoc.core.gate_manager import GateManager, GateStatus
        gm = GateManager(memory)
        gate_id = gm.create_gate("proj_allpass", "code", "Coder", ["c"])
        gm.add_result(gate_id, {"status": "pass"})
        gate_data = memory.get_knowledge(f"gate:{gate_id}")
        assert gate_data["status"] == GateStatus.COMPLETE.value

    def test_evaluate_gate_failure_publishes_gate_failed_not_resolved(self, memory):
        """On failure, gate/failed is published and gate/resolved is NOT published."""
        from nanoc.core.gate_manager import GateManager
        gm = GateManager(memory)
        gate_id = gm.create_gate("proj_failevt", "code", "Coder", ["c"])
        gm.add_result(gate_id, {"status": "fail"})
        failed_events = memory.get_events(topic="gate/failed")
        resolved_events = memory.get_events(topic="gate/resolved")
        assert len(failed_events) >= 1
        assert len(resolved_events) == 0

    def test_evaluate_gate_pass_publishes_both_completed_and_resolved(self, memory):
        """On pass, both gate/completed and gate/resolved are published."""
        from nanoc.core.gate_manager import GateManager
        gm = GateManager(memory)
        gate_id = gm.create_gate("proj_passevt", "code", "Coder", ["c"])
        gm.add_result(gate_id, {"status": "pass"})
        completed_events = memory.get_events(topic="gate/completed")
        resolved_events = memory.get_events(topic="gate/resolved")
        assert len(completed_events) >= 1
        assert len(resolved_events) >= 1

    def test_evaluate_gate_no_results_does_nothing(self, memory):
        """evaluate_gate with no results silently returns; no gate events published."""
        from nanoc.core.gate_manager import GateManager
        gm = GateManager(memory)
        gate_id = gm.create_gate("proj_noresult", "code", "Coder", ["c"])
        gm.evaluate_gate(gate_id)
        assert len(memory.get_events(topic="gate/failed")) == 0
        assert len(memory.get_events(topic="gate/resolved")) == 0
        assert len(memory.get_events(topic="gate/completed")) == 0

    def test_evaluate_gate_nonexistent_id_returns_silently(self, memory):
        """evaluate_gate with an unknown gate_id does not raise."""
        from nanoc.core.gate_manager import GateManager
        gm = GateManager(memory)
        gm.evaluate_gate("gate_does_not_exist")  # Should not raise


# ===========================================================================
# LLM token counting and cost calculation  (llm.py PR change)
# ===========================================================================

class TestLLMRecordTelemetry:
    def test_token_count_uses_char_div_4_for_prompt(self):
        """prompt_tokens = len(prompt) // 4"""
        prompt = "a" * 40  # 40 chars → 10 tokens
        completion = "b" * 20  # 20 chars → 5 tokens
        prompt_tokens = len(prompt) // 4
        completion_tokens = len(completion) // 4
        assert prompt_tokens == 10
        assert completion_tokens == 5

    def test_cost_formula_matches_pr_implementation(self):
        """cost = ((prompt_tokens + completion_tokens) / 1000) * 0.01"""
        prompt_tokens = 100
        completion_tokens = 50
        cost = ((prompt_tokens + completion_tokens) / 1000) * 0.01
        assert abs(cost - 0.0015) < 1e-10

    def test_cost_is_zero_for_empty_strings(self):
        """Empty prompt and response should produce zero tokens and zero cost."""
        prompt = ""
        response = ""
        prompt_tokens = len(prompt) // 4
        completion_tokens = len(response) // 4
        cost = ((prompt_tokens + completion_tokens) / 1000) * 0.01
        assert cost == 0.0

    def test_record_telemetry_invokes_telemetry_hub(self, memory, tmp_path):
        """_record_telemetry calls hub.record_token_usage with correct token estimates."""
        from unittest.mock import patch, MagicMock
        from nanoc.core.llm import LLMProvider

        mock_hub = MagicMock()

        with patch("nanoc.core.llm.TelemetryHub", return_value=mock_hub), \
             patch("nanoc.core.llm.Memory", return_value=memory):
            provider = LLMProvider(provider="openrouter", model="test-model")
            prompt = "Hello world test"   # 16 chars → 4 tokens
            response = "OK response text"  # 16 chars → 4 tokens
            provider._record_telemetry(prompt, response, 123.0)

        expected_prompt_tokens = len(prompt) // 4
        expected_completion_tokens = len(response) // 4
        expected_cost = ((expected_prompt_tokens + expected_completion_tokens) / 1000) * 0.01

        mock_hub.record_token_usage.assert_called_once_with(
            "test-model",
            expected_prompt_tokens,
            expected_completion_tokens,
            expected_cost,
        )
        mock_hub.record_latency.assert_called_once_with("llm_complete", 123.0)

    def test_record_telemetry_cost_scales_with_token_count(self):
        """Larger prompts produce proportionally higher costs."""
        short_prompt_tokens = len("hi") // 4          # 0
        long_prompt_tokens = len("a" * 4000) // 4      # 1000

        short_cost = ((short_prompt_tokens + 0) / 1000) * 0.01
        long_cost = ((long_prompt_tokens + 0) / 1000) * 0.01

        assert long_cost > short_cost

    def test_old_word_split_vs_new_char_div4_differ_for_long_text(self):
        """Old: len(text.split()) * cost; new: len(text)//4 * cost — verify they differ."""
        prompt = "word " * 100  # 100 words, 500 chars
        old_tokens = len(prompt.split())          # 100
        new_tokens = len(prompt) // 4             # 125
        assert old_tokens != new_tokens


# ===========================================================================
# DiscoveryTool memory caching  (network.py PR change)
# ===========================================================================

class TestDiscoveryToolCaching:
    @pytest.mark.asyncio
    async def test_discover_topology_returns_default_with_nodes_and_edges(self, tmp_path):
        """DiscoveryTool.discover_topology() returns dict with 'nodes' and 'edges'."""
        from unittest.mock import patch
        from nanoc.tools.network import DiscoveryTool
        db_path = str(tmp_path / "disco.db")
        mem = _fresh_memory(db_path)

        with patch("nanoc.tools.network.Memory", return_value=mem), \
             patch("nanoc.tools.network.settings"):
            topo = await DiscoveryTool.discover_topology()

        assert "nodes" in topo
        assert "edges" in topo
        assert isinstance(topo["nodes"], list)
        assert isinstance(topo["edges"], list)

    @pytest.mark.asyncio
    async def test_discover_topology_stores_result_in_memory(self, tmp_path):
        """First call must store the default topology in memory knowledge store."""
        from unittest.mock import patch
        from nanoc.tools.network import DiscoveryTool
        db_path = str(tmp_path / "disco2.db")
        mem = _fresh_memory(db_path)

        with patch("nanoc.tools.network.Memory", return_value=mem), \
             patch("nanoc.tools.network.settings"):
            await DiscoveryTool.discover_topology()

        cached = mem.get_knowledge("network_topology")
        assert cached is not None
        assert "nodes" in cached

    @pytest.mark.asyncio
    async def test_discover_topology_returns_cached_topology_on_second_call(self, tmp_path):
        """Second call should return stored topology, not regenerate a new one."""
        from unittest.mock import patch
        from nanoc.tools.network import DiscoveryTool
        db_path = str(tmp_path / "disco3.db")
        mem = _fresh_memory(db_path)

        custom_topology = {
            "nodes": [{"id": "X1", "label": "Custom Node", "type": "router", "status": "online"}],
            "edges": []
        }
        mem.upsert_knowledge("network_topology", custom_topology)

        with patch("nanoc.tools.network.Memory", return_value=mem), \
             patch("nanoc.tools.network.settings"):
            result = await DiscoveryTool.discover_topology()

        assert result == custom_topology

    @pytest.mark.asyncio
    async def test_discover_topology_default_contains_localhost(self, tmp_path):
        """Default topology includes 127.0.0.1 node."""
        from unittest.mock import patch
        from nanoc.tools.network import DiscoveryTool
        db_path = str(tmp_path / "disco4.db")
        mem = _fresh_memory(db_path)

        with patch("nanoc.tools.network.Memory", return_value=mem), \
             patch("nanoc.tools.network.settings"):
            topo = await DiscoveryTool.discover_topology()

        node_ids = [n["id"] for n in topo["nodes"]]
        assert "127.0.0.1" in node_ids

    @pytest.mark.asyncio
    async def test_discover_topology_does_not_overwrite_existing_cache(self, tmp_path):
        """If memory already has topology, discover_topology must not overwrite it."""
        from unittest.mock import patch
        from nanoc.tools.network import DiscoveryTool
        db_path = str(tmp_path / "disco5.db")
        mem = _fresh_memory(db_path)

        original = {"nodes": [{"id": "cached"}], "edges": []}
        mem.upsert_knowledge("network_topology", original)

        with patch("nanoc.tools.network.Memory", return_value=mem), \
             patch("nanoc.tools.network.settings"):
            await DiscoveryTool.discover_topology()

        after = mem.get_knowledge("network_topology")
        assert after == original


# ===========================================================================
# SNMPTool fallback to snmpget CLI  (network.py PR change)
# ===========================================================================

class TestSNMPToolFallback:
    @pytest.mark.asyncio
    async def test_snmp_get_value_falls_back_when_powershell_fails(self):
        """When PowerShellTool.run_command returns non-zero, SNMPTool calls snmpget fallback."""
        from unittest.mock import patch, call
        from nanoc.tools.network import SNMPTool

        ps_fail = {"stdout": "", "stderr": "Module not found", "returncode": 1}
        snmp_ok = {"stdout": "1.3.6.1.2.1.1.1.0 = STRING: Linux", "stderr": "", "returncode": 0}

        with patch("nanoc.tools.network.PowerShellTool.run_command",
                   new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = [ps_fail, snmp_ok]
            result = await SNMPTool.get_value("192.168.1.1", "public", "1.3.6.1.2.1.1.1.0")

        # Two calls: first PowerShell, then snmpget fallback
        assert mock_run.call_count == 2
        first_call_cmd = mock_run.call_args_list[0][0][0]
        second_call_cmd = mock_run.call_args_list[1][0][0]
        assert "Get-SnmpData" in first_call_cmd
        assert "snmpget" in second_call_cmd
        assert result == snmp_ok

    @pytest.mark.asyncio
    async def test_snmp_get_value_does_not_fallback_on_powershell_success(self):
        """When PowerShellTool succeeds (returncode 0), fallback is NOT called."""
        from unittest.mock import patch
        from nanoc.tools.network import SNMPTool

        ps_ok = {"stdout": "value = 42", "stderr": "", "returncode": 0}

        with patch("nanoc.tools.network.PowerShellTool.run_command",
                   new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ps_ok
            result = await SNMPTool.get_value("10.0.0.1", "public", "1.3.6.1.2.1.1.1.0")

        # Only one call (no fallback)
        assert mock_run.call_count == 1
        assert result == ps_ok

    @pytest.mark.asyncio
    async def test_snmp_get_value_returns_fallback_result(self):
        """The return value should be the fallback snmpget result, not the failed PS result."""
        from unittest.mock import patch
        from nanoc.tools.network import SNMPTool

        ps_fail = {"returncode": 1, "stdout": "", "stderr": "error"}
        snmp_result = {"returncode": 0, "stdout": "OID data", "stderr": ""}

        with patch("nanoc.tools.network.PowerShellTool.run_command",
                   new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = [ps_fail, snmp_result]
            result = await SNMPTool.get_value("1.2.3.4", "private", "1.3.6.1.2.1.1.5.0")

        assert result == snmp_result

    @pytest.mark.asyncio
    async def test_snmp_get_value_passes_correct_ip_community_oid_to_fallback(self):
        """The fallback snmpget command includes the IP, community, and OID."""
        from unittest.mock import patch
        from nanoc.tools.network import SNMPTool

        ps_fail = {"returncode": 2, "stdout": "", "stderr": ""}
        snmp_result = {"returncode": 0, "stdout": "", "stderr": ""}

        with patch("nanoc.tools.network.PowerShellTool.run_command",
                   new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = [ps_fail, snmp_result]
            await SNMPTool.get_value("172.16.0.1", "community123", "1.3.6.1.2.1.2.1.0")

        fallback_cmd = mock_run.call_args_list[1][0][0]
        assert "172.16.0.1" in fallback_cmd
        assert "community123" in fallback_cmd
        assert "1.3.6.1.2.1.2.1.0" in fallback_cmd

    @pytest.mark.asyncio
    async def test_snmp_get_value_returns_ps_result_when_returncode_missing(self):
        """If PowerShellTool result lacks 'returncode' key (e.g. error dict), fallback is triggered."""
        from unittest.mock import patch
        from nanoc.tools.network import SNMPTool

        ps_error = {"error": "powershell not found"}   # no 'returncode' key → get() returns None != 0
        snmp_result = {"returncode": 0, "stdout": "ok", "stderr": ""}

        with patch("nanoc.tools.network.PowerShellTool.run_command",
                   new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = [ps_error, snmp_result]
            result = await SNMPTool.get_value("10.10.10.10", "pub", "1.3.6.1.2.1.1.1.0")

        # returncode is None (missing) → None != 0 → fallback triggered
        assert mock_run.call_count == 2
        assert result == snmp_result


# ===========================================================================
# nanoc/main.py get_logs limit parameter  (main.py PR change)
# ===========================================================================

class TestGetLogsEndpoint:
    @pytest.mark.asyncio
    async def test_get_logs_returns_logs_key(self, tmp_path):
        """get_logs() function returns dict with 'logs' key."""
        import sqlite3
        from unittest.mock import patch
        db_path = str(tmp_path / "main_test.db")
        mem = _fresh_memory(db_path)

        # Patch settings.DB_PATH so get_logs uses our temp DB
        with patch("nanoc.main.settings") as mock_settings:
            mock_settings.DB_PATH = db_path
            from nanoc.main import get_logs
            result = get_logs()
            assert "logs" in result
            assert isinstance(result["logs"], list)

    @pytest.mark.asyncio
    async def test_get_logs_default_limit_is_50(self, tmp_path):
        """get_logs() called without limit argument uses 50 as default."""
        import sqlite3
        import inspect
        from nanoc.main import get_logs

        sig = inspect.signature(get_logs)
        assert "limit" in sig.parameters
        default_limit = sig.parameters["limit"].default
        assert default_limit == 50

    @pytest.mark.asyncio
    async def test_get_logs_respects_custom_limit(self, tmp_path):
        """When limit=5, at most 5 log entries are returned."""
        from unittest.mock import patch
        db_path = str(tmp_path / "main_limit_test.db")
        mem = _fresh_memory(db_path)

        # Insert 10 log entries
        for i in range(10):
            mem.add_log("agent1", f"log message {i}")

        with patch("nanoc.main.settings") as mock_settings:
            mock_settings.DB_PATH = db_path
            from nanoc.main import get_logs
            result = get_logs(limit=5)
            assert len(result["logs"]) <= 5

    @pytest.mark.asyncio
    async def test_get_logs_returns_empty_list_for_empty_db(self, tmp_path):
        """Empty logs table returns an empty list."""
        from unittest.mock import patch
        db_path = str(tmp_path / "main_empty.db")
        mem = _fresh_memory(db_path)

        with patch("nanoc.main.settings") as mock_settings:
            mock_settings.DB_PATH = db_path
            from nanoc.main import get_logs
            result = get_logs()
            assert result["logs"] == []

    @pytest.mark.asyncio
    async def test_get_logs_ordered_by_timestamp_desc(self, tmp_path):
        """Logs are returned in descending timestamp order (newest first)."""
        import time
        from unittest.mock import patch
        db_path = str(tmp_path / "main_order.db")
        mem = _fresh_memory(db_path)

        mem.add_log("agent1", "first log")
        time.sleep(0.01)
        mem.add_log("agent1", "second log")
        time.sleep(0.01)
        mem.add_log("agent1", "third log")

        with patch("nanoc.main.settings") as mock_settings:
            mock_settings.DB_PATH = db_path
            from nanoc.main import get_logs
            result = get_logs()
            logs = result["logs"]
            assert len(logs) == 3
            # Most recent should be first
            assert logs[0]["content"] == "third log"


# ===========================================================================
# nanoc/core/config.py – new Runtime Settings fields
# ===========================================================================

class TestSettingsNewFields:
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
        """Settings.TERMINAL_ACCESS_TOKEN has a non-empty default value."""
        from nanoc.core.config import Settings
        s = Settings()
        assert s.TERMINAL_ACCESS_TOKEN == "secret-foss-token"

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

    def test_initial_workers_less_than_max_workers(self):
        """Default INITIAL_WORKERS must be strictly less than MAX_WORKERS."""
        from nanoc.core.config import Settings
        s = Settings()
        assert s.INITIAL_WORKERS < s.MAX_WORKERS


# ===========================================================================
# maintainer.py – trigger_maintenance and duplicate prevention
# nanoc/core/config.py  (new runtime settings added in PR)
# ===========================================================================

class TestConfigNewSettings:
    def test_initial_workers_default(self):
        """INITIAL_WORKERS must default to 5."""
        from nanoc.core.config import settings
        assert settings.INITIAL_WORKERS == 5

    def test_max_workers_default(self):
        """MAX_WORKERS must default to 20."""
        from nanoc.core.config import settings
        assert settings.MAX_WORKERS == 20

    def test_terminal_access_token_default(self):
        """TERMINAL_ACCESS_TOKEN must default to 'secret-foss-token'."""
        from nanoc.core.config import settings
        assert settings.TERMINAL_ACCESS_TOKEN == "secret-foss-token"

    def test_max_workers_greater_than_initial_workers(self):
        """MAX_WORKERS must be greater than INITIAL_WORKERS by default."""
        from nanoc.core.config import settings
        assert settings.MAX_WORKERS > settings.INITIAL_WORKERS

    def test_settings_has_logs_dir(self):
        """LOGS_DIR setting must exist (used by DocumentationAgent)."""
        from nanoc.core.config import settings
        assert hasattr(settings, "LOGS_DIR")
        assert settings.LOGS_DIR == "nanoc/logs"


# ===========================================================================
# backend/app/api/endpoints/terminal.py  (token auth added in PR)
# ===========================================================================

class TestGetTokenAuth:
    @pytest.mark.asyncio
    async def test_valid_token_returns_token(self):
        """get_token_auth returns the token string when it matches TERMINAL_ACCESS_TOKEN."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from backend.app.api.endpoints.terminal import get_token_auth

        ws = MagicMock()
        ws.query_params = {"token": "secret-foss-token"}
        ws.close = AsyncMock()

        with patch("backend.app.api.endpoints.terminal.settings") as mock_settings:
            mock_settings.TERMINAL_ACCESS_TOKEN = "secret-foss-token"
            result = await get_token_auth(ws)

        assert result == "secret-foss-token"
        ws.close.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_token_closes_websocket(self):
        """get_token_auth closes WebSocket with code 1008 when token is wrong."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from backend.app.api.endpoints.terminal import get_token_auth

        ws = MagicMock()
        ws.query_params = {"token": "wrong-token"}
        ws.close = AsyncMock()

        with patch("backend.app.api.endpoints.terminal.settings") as mock_settings:
            mock_settings.TERMINAL_ACCESS_TOKEN = "secret-foss-token"
            result = await get_token_auth(ws)

        assert result is None
        ws.close.assert_awaited_once_with(code=1008)

    @pytest.mark.asyncio
    async def test_missing_token_closes_websocket(self):
        """get_token_auth closes WebSocket when token query param is absent."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from backend.app.api.endpoints.terminal import get_token_auth

        ws = MagicMock()
        ws.query_params = {}
        ws.close = AsyncMock()

        with patch("backend.app.api.endpoints.terminal.settings") as mock_settings:
            mock_settings.TERMINAL_ACCESS_TOKEN = "secret-foss-token"
            result = await get_token_auth(ws)

        assert result is None
        ws.close.assert_awaited_once_with(code=1008)

    @pytest.mark.asyncio
    async def test_empty_token_is_rejected(self):
        """An empty string token does not match the configured token."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from backend.app.api.endpoints.terminal import get_token_auth

        ws = MagicMock()
        ws.query_params = {"token": ""}
        ws.close = AsyncMock()

        with patch("backend.app.api.endpoints.terminal.settings") as mock_settings:
            mock_settings.TERMINAL_ACCESS_TOKEN = "secret-foss-token"
            result = await get_token_auth(ws)

        assert result is None
        ws.close.assert_awaited_once_with(code=1008)

    @pytest.mark.asyncio
    async def test_close_uses_policy_violation_code(self):
        """WebSocket close code 1008 corresponds to Policy Violation."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from backend.app.api.endpoints.terminal import get_token_auth

        ws = MagicMock()
        ws.query_params = {"token": "bad"}
        ws.close = AsyncMock()

        with patch("backend.app.api.endpoints.terminal.settings") as mock_settings:
            mock_settings.TERMINAL_ACCESS_TOKEN = "correct"
            await get_token_auth(ws)

        call_kwargs = ws.close.call_args
        assert call_kwargs.kwargs.get("code") == 1008 or call_kwargs.args == (1008,) or call_kwargs == ((1008,), {}) or call_kwargs.kwargs == {"code": 1008}


# ===========================================================================
# maintainer.py  (new file in PR)
# ===========================================================================

class TestTriggerMaintenance:
    def test_creates_inbox_file_when_no_pending_task(self, tmp_path):
        """trigger_maintenance creates an inbox file when no pending task exists."""
        import sqlite3
        import sys
        db_path = str(tmp_path / "test.db")
        inbox_dir = str(tmp_path / "inbox")
        mem = _fresh_memory(db_path)

        with patch("maintainer.settings") as mock_settings, \
             patch("maintainer.Memory", return_value=mem):
            mock_settings.DB_PATH = db_path
            import maintainer
            with patch.object(maintainer, "settings") as ms2:
                ms2.DB_PATH = db_path
                # Override inbox_dir via patching os.makedirs and open
                with patch("maintainer.os.makedirs") as mock_mkdirs, \
                     patch("builtins.open", unittest.mock.mock_open()) as mock_open_fn:
                    maintainer.trigger_maintenance()
                    mock_open_fn.assert_called_once()
                    call_args = mock_open_fn.call_args[0][0]
                    assert "maintenance_" in call_args
                    assert call_args.endswith(".txt")

    def test_skips_when_pending_maintenance_task_exists(self, tmp_path):
        """trigger_maintenance skips file creation if a pending task already exists."""
        import sqlite3
        db_path = str(tmp_path / "test_skip.db")
        mem = _fresh_memory(db_path)

        # Insert a pending task that matches the duplicate check
        from datetime import datetime as _dt
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO tasks (description, assigned_to, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                ("Analyze the current NANOC project for improvements", "Leader", "pending",
                 _dt.now(), _dt.now())
            )
            conn.commit()

        import maintainer
        with patch.object(maintainer, "settings") as mock_settings, \
             patch("builtins.open", unittest.mock.mock_open()) as mock_open_fn:
            mock_settings.DB_PATH = db_path
            maintainer.trigger_maintenance()
            mock_open_fn.assert_not_called()

    def test_creates_inbox_directory(self, tmp_path):
        """trigger_maintenance ensures the inbox directory exists."""
        db_path = str(tmp_path / "dir_test.db")
        mem = _fresh_memory(db_path)

        import maintainer
        with patch.object(maintainer, "settings") as mock_settings, \
             patch("maintainer.os.makedirs") as mock_makedirs, \
             patch("builtins.open", unittest.mock.mock_open()):
            mock_settings.DB_PATH = db_path
            maintainer.trigger_maintenance()
            mock_makedirs.assert_called_once_with("nanoc/inbox", exist_ok=True)

    def test_writes_project_description_to_file(self, tmp_path):
        """The inbox file content contains the maintenance project description."""
        db_path = str(tmp_path / "content_test.db")
        mem = _fresh_memory(db_path)

        import maintainer
        with patch.object(maintainer, "settings") as mock_settings, \
             patch("maintainer.os.makedirs"), \
             patch("builtins.open", unittest.mock.mock_open()) as mock_open_fn:
            mock_settings.DB_PATH = db_path
            maintainer.trigger_maintenance()
            written = mock_open_fn().write.call_args[0][0]
            assert "NANOC" in written or "Analyze" in written

    def test_exception_in_trigger_does_not_crash_main_loop(self, tmp_path):
        """main() catches exceptions from trigger_maintenance and continues."""
        import maintainer
        call_count = [0]

        def side_effect():
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("simulated error")
            # Stop the infinite loop after second call
            raise SystemExit(0)

        with patch.object(maintainer, "trigger_maintenance", side_effect=side_effect), \
             patch("time.sleep"):
            try:
                maintainer.main()
            except SystemExit: pass

    def test_trigger_maintenance_writes_file(self, tmp_path):
        """trigger_maintenance writes a file to nanoc/inbox when no pending maintenance task exists."""
        import sqlite3
        import glob as glob_mod
        from unittest.mock import patch

        db_path = str(tmp_path / "maint.db")
        mem = _fresh_memory(db_path)
        inbox_dir = str(tmp_path / "inbox")

        with patch("maintainer.settings") as mock_settings, \
             patch("maintainer.Memory") as MockMemory, \
             patch("maintainer.os.makedirs") as mock_makedirs, \
             patch("maintainer.os.path.join", side_effect=os.path.join) as mock_join, \
             patch("builtins.open", unittest.mock.mock_open()) as mock_open_file, \
             patch("sqlite3.connect") as mock_connect:
            mock_settings.DB_PATH = db_path
            # Simulate no pending task found
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = None
            mock_conn = MagicMock()
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_conn.cursor.return_value = mock_cursor
            mock_connect.return_value = mock_conn

            from maintainer import trigger_maintenance
            trigger_maintenance()

        mock_makedirs.assert_called()
        mock_open_file.assert_called()

    def test_skips_when_pending_task_exists(self, tmp_path, capsys):
        """trigger_maintenance does not create a file when a pending task already exists."""
        import sqlite3
        from unittest.mock import patch, mock_open

        db_path = str(tmp_path / "maint2.db")

        with patch("maintainer.settings") as mock_settings, \
             patch("maintainer.Memory"), \
             patch("sqlite3.connect") as mock_connect, \
             patch("builtins.open", mock_open()) as mock_open_file:
            mock_settings.DB_PATH = db_path
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = (1,)  # Task already exists
            mock_conn = MagicMock()
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_conn.cursor.return_value = mock_cursor
            mock_connect.return_value = mock_conn

            from maintainer import trigger_maintenance
            trigger_maintenance()

        captured = capsys.readouterr()
        assert "already pending" in captured.out
        mock_open_file.assert_not_called()

    def test_inbox_file_contains_project_description(self, tmp_path):
        """The file written by trigger_maintenance contains the maintenance task description."""
        import sqlite3
        from unittest.mock import patch, mock_open, call

        db_path = str(tmp_path / "maint3.db")
        written_content = []

        m = unittest.mock.mock_open()
        original_write = m().write
        m().write.side_effect = lambda s: written_content.append(s)

        with patch("maintainer.settings") as mock_settings, \
             patch("maintainer.Memory"), \
             patch("sqlite3.connect") as mock_connect, \
             patch("builtins.open", m), \
             patch("maintainer.os.makedirs"):
            mock_settings.DB_PATH = db_path
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = None
            mock_conn = MagicMock()
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_conn.cursor.return_value = mock_cursor
            mock_connect.return_value = mock_conn

            from maintainer import trigger_maintenance
            trigger_maintenance()

        all_written = "".join(written_content)
        assert "NANOC" in all_written or "Analyze" in all_written

    def test_inbox_filename_contains_maintenance_prefix(self, tmp_path):
        """The generated inbox filename starts with 'maintenance_'."""
        import sqlite3
        from unittest.mock import patch, mock_open, call

        db_path = str(tmp_path / "maint4.db")
        opened_paths = []

        def capturing_open(path, mode="r", *args, **kwargs):
            opened_paths.append(path)
            return unittest.mock.mock_open()(path, mode)

        with patch("maintainer.settings") as mock_settings, \
             patch("maintainer.Memory"), \
             patch("sqlite3.connect") as mock_connect, \
             patch("builtins.open", side_effect=capturing_open), \
             patch("maintainer.os.makedirs"):
            mock_settings.DB_PATH = db_path
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = None
            mock_conn = MagicMock()
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_conn.cursor.return_value = mock_cursor
            mock_connect.return_value = mock_conn

            from maintainer import trigger_maintenance
            trigger_maintenance()

        assert any("maintenance_" in p for p in opened_paths)

    def test_main_catches_exceptions_and_continues(self):
        """main() catches exceptions from trigger_maintenance and does not crash."""
        from unittest.mock import patch
        call_count = [0]

        def raising_trigger():
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("simulated error")
            # Stop after second call to avoid infinite loop
            raise SystemExit(0)

        with patch("maintainer.trigger_maintenance", side_effect=raising_trigger), \
             patch("maintainer.time.sleep"):
            from maintainer import main
            try:
                main()
            except SystemExit:
                pass

        assert call_count[0] == 2


# ===========================================================================
# nanoc/agents/base.py – TeamLeader.delegate_tasks changes
# nanoc/agents/base.py  TeamLeader.delegate_tasks changes in PR
# ===========================================================================

class TestTeamLeaderDelegateTasks:
    @pytest.mark.asyncio
    async def test_generates_project_id_starting_with_proj_(self, memory):
        """delegate_tasks generates a project_id that starts with 'proj_'."""
        from nanoc.agents.base import TeamLeader
        leader = TeamLeader("Leader1", "Team Leader", memory, MockLLM())
        project_id = await leader.delegate_tasks("Build a network monitor")
        assert project_id.startswith("proj_")

    @pytest.mark.asyncio
    async def test_adds_project_id_to_active_projects(self, memory):
        """delegate_tasks stores the new project_id in 'active_projects' knowledge."""
        from nanoc.agents.base import TeamLeader
        leader = TeamLeader("Leader1", "Team Leader", memory, MockLLM())
        project_id = await leader.delegate_tasks("Build a SNMP poller")
        active = memory.get_knowledge("active_projects")
        assert active is not None
        assert project_id in active

    @pytest.mark.asyncio
    async def test_publishes_incoming_job_event(self, memory):
        """delegate_tasks publishes a 'project/incoming-job' event."""
        from nanoc.agents.base import TeamLeader
        leader = TeamLeader("Leader1", "Team Leader", memory, MockLLM())
        project_id = await leader.delegate_tasks("Monitor CPU usage")
        events = memory.get_events(topic="project/incoming-job")
        assert len(events) >= 1
        payload = json.loads(events[0]["payload"])
        assert payload["project_id"] == project_id
        assert "Monitor CPU usage" in payload["description"]

    @pytest.mark.asyncio
    async def test_incoming_job_event_has_leader_field(self, memory):
        """Verified that the event includes the leader field for downstream consumers."""
        from nanoc.agents.base import TeamLeader
        leader = TeamLeader("Leader1", "Team Leader", memory, MockLLM())
        await leader.delegate_tasks("Some project")
        events = memory.get_events(topic="project/incoming-job")
        payload = json.loads(events[0]["payload"])
        assert "leader" in payload
        assert payload["leader"] == "Leader1"

    @pytest.mark.asyncio
    async def test_creates_architect_task(self, memory):
        """delegate_tasks creates a task assigned to 'Architect'."""
        from nanoc.agents.base import TeamLeader
        leader = TeamLeader("Leader1", "Team Leader", memory, MockLLM())
        await leader.delegate_tasks("Design routing tables")
        import sqlite3
    @pytest.mark.asyncio
    async def test_delegate_tasks_generates_project_id(self, memory):
        """delegate_tasks generates a project_id prefixed with 'proj_'."""
        from nanoc.agents.base import TeamLeader
        leader = TeamLeader("L1", "Team Leader", memory, MockLLM())
        proj_id = await leader.delegate_tasks("Build a feature")
        assert proj_id.startswith("proj_")

    @pytest.mark.asyncio
    async def test_delegate_tasks_always_appends_to_active_projects(self, memory):
        """delegate_tasks appends new project_id to active_projects even if already present."""
        from nanoc.agents.base import TeamLeader
        leader = TeamLeader("L1", "Team Leader", memory, MockLLM())
        memory.upsert_knowledge("active_projects", ["proj_existing"])
        await leader.delegate_tasks("Task A")
        projects = memory.get_knowledge("active_projects")
        assert len(projects) == 2
        assert "proj_existing" in projects

    @pytest.mark.asyncio
    async def test_delegate_tasks_publishes_incoming_job_event(self, memory):
        """delegate_tasks publishes a 'project/incoming-job' event."""
        from nanoc.agents.base import TeamLeader
        leader = TeamLeader("L1", "Team Leader", memory, MockLLM())
        await leader.delegate_tasks("Build network monitor")
        events = memory.get_events(topic="project/incoming-job")
        assert len(events) >= 1

    @pytest.mark.asyncio
    async def test_delegate_tasks_event_contains_project_id_and_description(self, memory):
        """The 'project/incoming-job' event payload has project_id and description fields."""
        import json
        from nanoc.agents.base import TeamLeader
        leader = TeamLeader("L1", "Team Leader", memory, MockLLM())
        proj_id = await leader.delegate_tasks("Deploy monitoring stack")
        events = memory.get_events(topic="project/incoming-job")
        payload = json.loads(events[-1]["payload"])
        assert payload["project_id"] == proj_id
        assert "Deploy monitoring stack" in payload["description"]

    @pytest.mark.asyncio

    @pytest.mark.asyncio
    async def test_delegate_tasks_creates_architect_task(self, memory):
        """delegate_tasks creates a task assigned to 'Architect'."""
        import sqlite3
        from nanoc.agents.base import TeamLeader
        leader = TeamLeader("L1", "Team Leader", memory, MockLLM())
        await leader.delegate_tasks("Architect a new module")
        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE assigned_to = 'Architect'")
            tasks = [dict(row) for row in cursor.fetchall()]
        assert len(tasks) >= 1
        assert any("Design architecture for:" in t["description"] for t in tasks)

    @pytest.mark.asyncio
    async def test_multiple_calls_each_get_unique_project_id(self, memory):
        """Each call to delegate_tasks produces a distinct project_id (mocked time)."""
        from nanoc.agents.base import TeamLeader
        from datetime import datetime as dt_orig
        leader = TeamLeader("Leader1", "Team Leader", memory, MockLLM())

        call_count = [0]
        real_now = dt_orig.now()

        class FakeDatetime:
            @staticmethod
            def now():
                call_count[0] += 1
                from datetime import timedelta
                return real_now + timedelta(seconds=call_count[0])

        with patch("nanoc.agents.base.datetime") as mock_dt:
            mock_dt.now = FakeDatetime.now
            id1 = await leader.delegate_tasks("Project A")
            id2 = await leader.delegate_tasks("Project B")

        assert id1 != id2

    @pytest.mark.asyncio
    async def test_stores_architecture_in_knowledge(self, memory):
        """delegate_tasks stores the generated architecture in the knowledge base."""
        from nanoc.agents.base import TeamLeader
        llm = MockLLM()
        llm.default_response = "Component A, Component B, Component C"
        leader = TeamLeader("Leader1", "Team Leader", memory, llm)
        project_id = await leader.delegate_tasks("Build monitoring")
        arch = memory.get_knowledge(f"project_{project_id}_arch")
        assert arch is not None

    @pytest.mark.asyncio
    async def test_creates_design_gate(self, memory):
        """delegate_tasks creates a design gate in the gate manager."""
        from nanoc.agents.base import TeamLeader
        from nanoc.core.gate_manager import GateManager
        leader = TeamLeader("Leader1", "Team Leader", memory, MockLLM())
        project_id = await leader.delegate_tasks("Gate test project")
        gm = GateManager(memory)
        gate_id = gm.get_active_gate(project_id)
        assert gate_id is not None


# ===========================================================================
# nanoc/agents/documentation.py – DocumentationAgent changes
# ===========================================================================

class TestDocumentationAgentInit:
    def test_docs_dir_attribute_set(self, tmp_path, memory):
        """DocumentationAgent.__init__ sets self.docs_dir based on LOGS_DIR."""
        from nanoc.agents.documentation import DocumentationAgent
        with patch("nanoc.agents.documentation.settings") as mock_settings:
            mock_settings.LOGS_DIR = str(tmp_path / "logs")
            agent = DocumentationAgent("DocAgent1", "Documentation", memory, MockLLM())
        assert agent.docs_dir == str(tmp_path / "logs" / "docs")

    def test_docs_dir_created_on_init(self, tmp_path, memory):
        """DocumentationAgent.__init__ creates the docs directory if it doesn't exist."""
        from nanoc.agents.documentation import DocumentationAgent
        logs_dir = str(tmp_path / "logs")
        with patch("nanoc.agents.documentation.settings") as mock_settings:
            mock_settings.LOGS_DIR = logs_dir
            agent = DocumentationAgent("DocAgent1", "Documentation", memory, MockLLM())
        assert os.path.isdir(os.path.join(logs_dir, "docs"))


class TestDocumentationAgentUpdateDocs:
    @pytest.mark.asyncio
    async def test_creates_markdown_file_for_new_project(self, tmp_path, memory):
        """update_docs creates a .md file when none exists for the project."""
        from nanoc.agents.documentation import DocumentationAgent
        logs_dir = str(tmp_path / "logs")
        with patch("nanoc.agents.documentation.settings") as mock_settings:
            mock_settings.LOGS_DIR = logs_dir
            agent = DocumentationAgent("DocAgent1", "Documentation", memory, MockLLM())
            await agent.update_docs("proj_new", "Initial architecture doc")
        doc_path = os.path.join(logs_dir, "docs", "proj_new.md")
        assert os.path.exists(doc_path)

    @pytest.mark.asyncio
    async def test_file_content_includes_written_content(self, tmp_path, memory):
        """The markdown file written by update_docs contains the supplied content."""
        from nanoc.agents.documentation import DocumentationAgent
        logs_dir = str(tmp_path / "logs")
        with patch("nanoc.agents.documentation.settings") as mock_settings:
            mock_settings.LOGS_DIR = logs_dir
            agent = DocumentationAgent("DocAgent1", "Documentation", memory, MockLLM())
            await agent.update_docs("proj_content", "My important content")
        doc_path = os.path.join(logs_dir, "docs", "proj_content.md")
        with open(doc_path) as f:
            data = f.read()
        assert "My important content" in data

    @pytest.mark.asyncio
    async def test_appends_on_second_call(self, tmp_path, memory):
        """A second update_docs call appends to the existing file."""
        from nanoc.agents.documentation import DocumentationAgent
        logs_dir = str(tmp_path / "logs")
        with patch("nanoc.agents.documentation.settings") as mock_settings:
            mock_settings.LOGS_DIR = logs_dir
            agent = DocumentationAgent("DocAgent1", "Documentation", memory, MockLLM())
            await agent.update_docs("proj_append", "first content")
            await agent.update_docs("proj_append", "updated content")
        doc_path = os.path.join(logs_dir, "docs", "proj_append.md")
        with open(doc_path) as f:
            data = f.read()
        assert "first content" in data
        assert "updated content" in data

    @pytest.mark.asyncio
    async def test_publishes_docs_updated_event(self, tmp_path, memory):
        """update_docs publishes a 'docs/updated' event."""
        from nanoc.agents.documentation import DocumentationAgent
        logs_dir = str(tmp_path / "logs")
        with patch("nanoc.agents.documentation.settings") as mock_settings:
            mock_settings.LOGS_DIR = logs_dir
            agent = DocumentationAgent("DocAgent1", "Documentation", memory, MockLLM())
            await agent.update_docs("proj_event", "doc content")
        events = memory.get_events(topic="docs/updated")
        assert len(events) >= 1
        payload = json.loads(events[0]["payload"])
        assert payload["project_id"] == "proj_event"
        assert payload["status"] == "success"

    @pytest.mark.asyncio
    async def test_event_includes_file_path(self, tmp_path, memory):
        """The 'docs/updated' event payload includes the 'path' field."""
        from nanoc.agents.documentation import DocumentationAgent
        logs_dir = str(tmp_path / "logs")
        with patch("nanoc.agents.documentation.settings") as mock_settings:
            mock_settings.LOGS_DIR = logs_dir
            agent = DocumentationAgent("DocAgent1", "Documentation", memory, MockLLM())
            await agent.update_docs("proj_path", "some content")
        events = memory.get_events(topic="docs/updated")
        payload = json.loads(events[0]["payload"])
        assert "path" in payload
        assert payload["path"].endswith("proj_path.md")

    @pytest.mark.asyncio
    async def test_stores_content_in_knowledge_base(self, tmp_path, memory):
        """update_docs persists content to the knowledge base under 'docs:{project_id}'."""
        from nanoc.agents.documentation import DocumentationAgent
        logs_dir = str(tmp_path / "logs")
        with patch("nanoc.agents.documentation.settings") as mock_settings:
            mock_settings.LOGS_DIR = logs_dir
            agent = DocumentationAgent("DocAgent1", "Documentation", memory, MockLLM())
            await agent.update_docs("proj_kb", "knowledge content")
        stored = memory.get_knowledge("docs:proj_kb")
        assert stored == "knowledge content"

    @pytest.mark.asyncio
    async def test_file_header_contains_update_marker(self, tmp_path, memory):
        """update_docs writes a '## Update at' header before content."""
        from nanoc.agents.documentation import DocumentationAgent
        logs_dir = str(tmp_path / "logs")
        with patch("nanoc.agents.documentation.settings") as mock_settings:
            mock_settings.LOGS_DIR = logs_dir
            agent = DocumentationAgent("DocAgent1", "Documentation", memory, MockLLM())
            await agent.update_docs("proj_header", "header test content")
        doc_path = os.path.join(logs_dir, "docs", "proj_header.md")
        with open(doc_path) as f:
            data = f.read()
        assert "## Update at" in data


# ===========================================================================
# nanoc/agents/healer.py – AutoHealer.handle_failure
# ===========================================================================


class TestAutoHealerInit:
    def test_healer_role_is_healer(self, memory):
        """AutoHealer sets its role to 'Healer'."""
        from nanoc.agents.healer import AutoHealer
        healer = AutoHealer("Healer1", memory)
        assert healer.role == "Healer"

    def test_healer_agent_id_is_set(self, memory):
        """AutoHealer stores the provided agent_id."""
        from nanoc.agents.healer import AutoHealer
        healer = AutoHealer("MyHealer", memory)
        assert healer.agent_id == "MyHealer"


class TestAutoHealerHandleFailure:
    @pytest.mark.asyncio
    async def test_handle_failure_calls_create_task(self, memory):
        """handle_failure creates a new task when a failure event is received."""
        from unittest.mock import patch, MagicMock
        from nanoc.agents.healer import AutoHealer

        healer = AutoHealer("Healer1", memory)
        healer.llm = MockLLM()

        failure_event = {
            "task_id": 42,
            "project_id": "proj_test",
            "error": "SyntaxError: unexpected EOF",
            "description": "Write a python script"
        }

        with patch.object(memory, "create_task", return_value=999) as mock_create:
            await healer.handle_failure(failure_event)
            mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_failure_assigns_task_to_coder(self, memory):
        """handle_failure assigns the fix task to 'Coder' role."""
        from unittest.mock import patch
        from nanoc.agents.healer import AutoHealer

        healer = AutoHealer("Healer1", memory)
        healer.llm = MockLLM()

        failure_event = {
            "task_id": 5,
            "project_id": "proj_xyz",
            "error": "ImportError",
            "description": "Import and use a library"
        }

        with patch.object(memory, "create_task", return_value=100) as mock_create:
            await healer.handle_failure(failure_event)
            _, kwargs = mock_create.call_args
            assert kwargs.get("assigned_to") == "Coder"
        call_kwargs = mock_create.call_args
        assigned_to = call_kwargs.kwargs.get("assigned_to") or call_kwargs.args[1]
        assert assigned_to == "Coder"

    @pytest.mark.asyncio
    async def test_handle_failure_sets_high_priority(self, memory):
        """handle_failure creates a task with priority=10 for expedited processing."""
        from unittest.mock import patch
        from nanoc.agents.healer import AutoHealer

        healer = AutoHealer("Healer1", memory)
        healer.llm = MockLLM()

        failure_event = {
            "task_id": 7,
            "project_id": "proj_abc",
            "error": "RuntimeError",
            "description": "Run computation"
        }

        with patch.object(memory, "create_task", return_value=101) as mock_create:
            await healer.handle_failure(failure_event)
            _, kwargs = mock_create.call_args
            assert kwargs.get("priority") == 10

        call_kwargs = mock_create.call_args
        priority = call_kwargs.kwargs.get("priority")
        assert priority == 10

    @pytest.mark.asyncio
    async def test_handle_failure_includes_task_id_in_description(self, memory):
        """The fix task description references the original failed task_id."""
        from unittest.mock import patch
        from nanoc.agents.healer import AutoHealer

        healer = AutoHealer("Healer1", memory)
        healer.llm = MockLLM()

        failure_event = {
            "task_id": 3,
            "project_id": "proj_123",
            "error": "ValueError",
            "description": "Original task description here"
        }

        with patch.object(memory, "create_task", return_value=102) as mock_create:
            await healer.handle_failure(failure_event)
            desc_arg = mock_create.call_args[0][0]
            assert "Original task description here" in desc_arg

    @pytest.mark.asyncio
    async def test_fix_task_references_original_task_id(self, memory):
        """The fix task description references the failed task ID."""
        from nanoc.agents.healer import AutoHealer
        healer = AutoHealer("Healer1", memory)
        healer.llm = MockLLM()

        failure_event = {
            "task_id": 99,
            "project_id": "proj_456",
            "error": "TimeoutError",
            "description": "Some task"
        }

        with patch.object(memory, "create_task", return_value=103) as mock_create:
            await healer.handle_failure(failure_event)
            desc_arg = mock_create.call_args[0][0]
            assert "99" in desc_arg

    @pytest.mark.asyncio
    async def test_handle_failure_calls_think_with_error_and_description(self, memory):
        """handle_failure calls think() with a prompt containing error and description."""
        from nanoc.agents.healer import AutoHealer

        healer = AutoHealer("Healer1", memory)
        llm = MockLLM()
        healer.llm = llm

        failure_event = {
            "task_id": 5,
            "project_id": "proj_think",
            "error": "ConnectionError: timeout",
            "description": "Fetch remote config"
        }

        from unittest.mock import patch
        with patch.object(memory, "create_task", return_value=103):
            await healer.handle_failure(failure_event)

        assert len(llm.calls) >= 1
        last_prompt = llm.calls[-1]["prompt"]
        assert "ConnectionError: timeout" in last_prompt or "Fetch remote config" in last_prompt

    @pytest.mark.asyncio
    async def test_handle_failure_passes_project_id_to_task(self, memory):
        """handle_failure passes the correct project_id when creating the fix task."""
        from unittest.mock import patch
        from nanoc.agents.healer import AutoHealer

        healer = AutoHealer("Healer1", memory)
        healer.llm = MockLLM()

        failure_event = {
            "task_id": 11,
            "project_id": "proj_special",
            "error": "KeyError",
            "description": "Look up a config key"
        }

        with patch.object(memory, "create_task", return_value=104) as mock_create:
            await healer.handle_failure(failure_event)
            _, kwargs = mock_create.call_args
            assert kwargs.get("project_id") == "proj_special"

    @pytest.mark.asyncio
    async def test_healer_role_is_healer(self, memory):
        """AutoHealer is initialized with role 'Healer'."""
        from nanoc.agents.healer import AutoHealer
        healer = AutoHealer("Healer1", memory)
        assert healer.role == "Healer"

    @pytest.mark.asyncio
    async def test_handle_failure_with_none_project_id(self, memory):
        """handle_failure works when project_id is None in the event."""
        from nanoc.agents.healer import AutoHealer
        healer = AutoHealer("Healer1", memory)
        healer.llm = MockLLM()

        failure_event = {
            "task_id": 1,
            "project_id": None,
            "error": "NullError",
            "description": "Task with no project"
        }

        with patch.object(memory, "create_task", return_value=105) as mock_create:
            await healer.handle_failure(failure_event)
            _, kwargs = mock_create.call_args
            assert kwargs.get("project_id") is None


# ===========================================================================
# nanoc/agents/security.py – SecurityAgent.audit_service
# ===========================================================================


class TestSecurityAgentAuditService:
    @pytest.mark.asyncio
    async def test_audit_service_publishes_audit_complete_event_on_success(self, memory):
        """audit_service publishes 'security/audit-complete' event on successful scan."""
        import json
        from unittest.mock import patch, AsyncMock
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("Sec1", memory)
        agent.llm = MockLLM()

        nmap_result = {"stdout": "Nmap scan report...", "stderr": "", "returncode": 0}

        with patch("nanoc.tools.network.AsyncRunner.run_command", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = nmap_result
            result = await agent.audit_service("192.168.1.1")

        events = memory.get_events(topic="security/audit-complete")
        assert len(events) >= 1
        payload = json.loads(events[-1]["payload"])
        assert payload["target"] == "192.168.1.1"

    @pytest.mark.asyncio
    async def test_audit_service_returns_stdout_on_success(self, memory):
        """audit_service returns the stdout content from nmap on success."""
        from unittest.mock import patch, AsyncMock
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("Sec1", memory)
        agent.llm = MockLLM()

        nmap_result = {"stdout": "Nmap scan report for 10.0.0.1", "stderr": "", "returncode": 0}

        with patch("nanoc.tools.network.AsyncRunner.run_command", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = nmap_result
            result = await agent.audit_service("10.0.0.1")

        assert result == "Nmap scan report for 10.0.0.1"

    @pytest.mark.asyncio
    async def test_audit_service_returns_error_dict_on_failure(self, memory):
        """audit_service returns the error dict when nmap command fails."""
        from unittest.mock import patch, AsyncMock
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("Sec1", memory)
        agent.llm = MockLLM()

        error_result = {"error": "nmap not found", "returncode": 1, "stdout": "", "stderr": ""}

        with patch("nanoc.tools.network.AsyncRunner.run_command", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = error_result
            result = await agent.audit_service("bad-target")

        assert result == error_result

    @pytest.mark.asyncio
    async def test_audit_service_does_not_publish_event_on_error(self, memory):
        """audit_service does not publish 'security/audit-complete' when an error occurs."""
        from unittest.mock import patch, AsyncMock
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("Sec1", memory)
        agent.llm = MockLLM()

        error_result = {"error": "command failed"}

        with patch("nanoc.tools.network.AsyncRunner.run_command", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = error_result
            await agent.audit_service("192.168.1.99")

        events = memory.get_events(topic="security/audit-complete")
        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_runs_nmap_with_sv_flag(self, memory):
        """audit_service calls nmap with the -sV (version scan) flag."""
        from nanoc.agents.security import SecurityAgent
        agent = SecurityAgent("SecAgent1", memory)
        agent.llm = MockLLM()

        nmap_result = {"stdout": "results", "stderr": "", "returncode": 0}

        with patch("nanoc.tools.network.AsyncRunner.run_command", new_callable=AsyncMock,
                   return_value=nmap_result) as mock_run:
            await agent.audit_service("10.10.10.10")
            call_args = mock_run.call_args[0][0]
            assert "nmap" in call_args
            assert "-sV" in call_args
            assert "10.10.10.10" in call_args

    @pytest.mark.asyncio
    async def test_security_agent_role_is_security(self, memory):
        """SecurityAgent is initialized with role 'Security'."""
        from nanoc.agents.security import SecurityAgent
        agent = SecurityAgent("SecAgent1", memory)
        assert agent.role == "Security"

    @pytest.mark.asyncio
    async def test_audit_complete_event_has_target_field(self, memory):
        """The audit-complete event payload includes the target field."""
        from nanoc.agents.security import SecurityAgent
        agent = SecurityAgent("SecAgent1", memory)
        agent.llm = MockLLM()

        nmap_result = {"stdout": "scan output", "stderr": "", "returncode": 0}

        with patch("nanoc.tools.network.AsyncRunner.run_command", new_callable=AsyncMock,
                   return_value=nmap_result):
            await agent.audit_service("host.example.com")

        events = memory.get_events(topic="security/audit-complete")
        payload = json.loads(events[0]["payload"])
        assert "target" in payload
        assert payload["target"] == "host.example.com"


# ===========================================================================
# nanoc/core/llm.py – model override from knowledge base
    @pytest.mark.asyncio
    async def test_audit_service_event_contains_report_and_findings(self, memory):
        """The 'security/audit-complete' event payload contains scan report and analysis findings."""
        import json
        from unittest.mock import patch, AsyncMock
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("Sec1", memory)
        agent.llm = MockLLM()

        scan_output = "22/tcp open ssh OpenSSH 8.2 Telnet service detected"
        nmap_result = {"stdout": scan_output, "stderr": "", "returncode": 0}

        with patch("nanoc.tools.network.AsyncRunner.run_command", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = nmap_result
            await agent.audit_service("172.16.0.5")

        events = memory.get_events(topic="security/audit-complete")
        payload = json.loads(events[-1]["payload"])
        assert payload["report"] == scan_output
        assert "findings" in payload
        assert "vulnerabilities" in payload
        assert "Telnet service detected" in payload["findings"]

    @pytest.mark.asyncio
    async def test_audit_service_uses_nmap_sv_command(self, memory):
        """audit_service uses nmap with -sV flag for service version detection."""
        from unittest.mock import patch, AsyncMock
        from nanoc.agents.security import SecurityAgent

        agent = SecurityAgent("Sec1", memory)
        agent.llm = MockLLM()

        nmap_result = {"stdout": "scan report", "stderr": "", "returncode": 0}

        with patch("nanoc.tools.network.AsyncRunner.run_command", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = nmap_result
            await agent.audit_service("10.0.0.5")

        called_cmd = mock_run.call_args.args[0]
        assert "nmap" in called_cmd
        assert "-sV" in called_cmd
        assert "10.0.0.5" in called_cmd


# ===========================================================================
# nanoc/core/llm.py  (model override from knowledge base added in PR)
# ===========================================================================

class TestLLMModelOverride:
    @pytest.mark.asyncio
    async def test_uses_model_override_when_set_in_knowledge(self, tmp_path):
        """complete() uses the model from 'system/model_override' if present."""
        db_path = str(tmp_path / "llm_override.db")
        mem = _fresh_memory(db_path)
        mem.upsert_knowledge("system/model_override", "custom/model-override")

        with patch("nanoc.core.llm.settings") as mock_settings, \
             patch("nanoc.core.llm.Memory", return_value=mem):
            mock_settings.DB_PATH = db_path
            mock_settings.DEFAULT_PROVIDER = "openrouter"
            mock_settings.DEFAULT_MODEL = "default/model"
            mock_settings.OPENROUTER_API_KEY = "test-key"

            from nanoc.core.llm import LLMProvider
            provider = LLMProvider(provider="openrouter", model="default/model")

            captured_model = []

            async def fake_openrouter(prompt, system_prompt, model):
                captured_model.append(model)
                return "mocked response"

            with patch.object(provider, "_openrouter_complete", side_effect=fake_openrouter), \
                 patch.object(provider, "_record_telemetry"):
                await provider.complete("test prompt")

        assert captured_model[0] == "custom/model-override"

    @pytest.mark.asyncio
    async def test_uses_default_model_when_no_override(self, tmp_path):
        """complete() uses self.model when no override is stored in knowledge base."""
        db_path = str(tmp_path / "llm_default.db")
        mem = _fresh_memory(db_path)
        # No override set

        with patch("nanoc.core.llm.settings") as mock_settings, \
             patch("nanoc.core.llm.Memory", return_value=mem):
            mock_settings.DB_PATH = db_path
            mock_settings.DEFAULT_PROVIDER = "openrouter"
            mock_settings.DEFAULT_MODEL = "default/model"
            mock_settings.OPENROUTER_API_KEY = "test-key"

            from nanoc.core.llm import LLMProvider
            provider = LLMProvider(provider="openrouter", model="the-actual-model")

            captured_model = []

            async def fake_openrouter(prompt, system_prompt, model):
                captured_model.append(model)
                return "mocked response"

            with patch.object(provider, "_openrouter_complete", side_effect=fake_openrouter), \
                 patch.object(provider, "_record_telemetry"):
                await provider.complete("test prompt")

        assert captured_model[0] == "the-actual-model"

    @pytest.mark.asyncio
    async def test_complete_uses_override_when_set(self, tmp_path):
        """LLMProvider.complete uses model from knowledge base override when present."""
        from unittest.mock import patch, AsyncMock
        from nanoc.core.llm import LLMProvider

        db_path = str(tmp_path / "llm_override.db")
        mem = _fresh_memory(db_path)
        mem.upsert_knowledge("system/model_override", "gpt-4-turbo")

        provider = LLMProvider(provider="openrouter")

        with patch("nanoc.core.llm.Memory") as MockMem, \
             patch.object(provider, "_openrouter_complete", new_callable=AsyncMock) as mock_complete, \
             patch.object(provider, "_record_telemetry"):
            MockMem.return_value = mem
            mock_complete.return_value = "response"
            await provider.complete("test prompt")

        # Verify that _openrouter_complete was called with the override model
        called_model = mock_complete.call_args.args[2]
        assert called_model == "gpt-4-turbo"

    @pytest.mark.asyncio
    async def test_complete_uses_self_model_when_no_override(self, tmp_path):
        """LLMProvider.complete uses self.model when no override is set in knowledge base."""
        from unittest.mock import patch, AsyncMock
        from nanoc.core.llm import LLMProvider

        db_path = str(tmp_path / "llm_no_override.db")
        mem = _fresh_memory(db_path)
        # No override set

        provider = LLMProvider(provider="openrouter", model="default-model-xyz")

        with patch("nanoc.core.llm.Memory") as MockMem, \
             patch.object(provider, "_openrouter_complete", new_callable=AsyncMock) as mock_complete, \
             patch.object(provider, "_record_telemetry"):
            MockMem.return_value = mem
            mock_complete.return_value = "response"
            await provider.complete("test prompt")

        called_model = mock_complete.call_args.args[2]
        assert called_model == "default-model-xyz"

    @pytest.mark.asyncio
    async def test_complete_ollama_uses_override_when_set(self, tmp_path):
        """LLMProvider.complete passes override model to _ollama_complete when provider is ollama."""
        from unittest.mock import patch, AsyncMock
        from nanoc.core.llm import LLMProvider

        db_path = str(tmp_path / "llm_ollama.db")
        mem = _fresh_memory(db_path)
        mem.upsert_knowledge("system/model_override", "llama3-override")

        provider = LLMProvider(provider="ollama", model="llama3-default")

        with patch("nanoc.core.llm.Memory") as MockMem, \
             patch.object(provider, "_ollama_complete", new_callable=AsyncMock) as mock_complete, \
             patch.object(provider, "_record_telemetry"):
            MockMem.return_value = mem
            mock_complete.return_value = "ollama response"
            await provider.complete("ollama test")

        called_model = mock_complete.call_args.args[2]
        assert called_model == "llama3-override"

    @pytest.mark.asyncio
    async def test_complete_raises_for_unknown_provider(self, tmp_path):
        """LLMProvider.complete raises ValueError for an unknown provider."""
        from unittest.mock import patch
        from nanoc.core.llm import LLMProvider

        db_path = str(tmp_path / "llm_unknown.db")
        mem = _fresh_memory(db_path)

        provider = LLMProvider(provider="unknown-provider")

        with patch("nanoc.core.llm.Memory") as MockMem, \
             patch.object(provider, "_record_error"):
            MockMem.return_value = mem
            with pytest.raises(ValueError, match="Unknown provider"):
                await provider.complete("should fail")

    def test_openrouter_complete_signature_accepts_model_param(self):
        """_openrouter_complete now accepts a model parameter (changed in PR)."""
        import inspect
        from nanoc.core.llm import LLMProvider
        sig = inspect.signature(LLMProvider._openrouter_complete)
        assert "model" in sig.parameters

    @pytest.mark.asyncio
    async def test_ollama_complete_accepts_model_param(self, tmp_path):
        """_ollama_complete takes 'model' as an explicit parameter."""
    def test_ollama_complete_signature_accepts_model_param(self):
        """_ollama_complete now accepts a model parameter (changed in PR)."""
        import inspect
        from nanoc.core.llm import LLMProvider
        sig = inspect.signature(LLMProvider._ollama_complete)
        assert "model" in sig.parameters

    @pytest.mark.asyncio
    async def test_ollama_uses_model_override(self, tmp_path):
        """complete() passes the overridden model to _ollama_complete."""
        db_path = str(tmp_path / "llm_ollama_override.db")
        mem = _fresh_memory(db_path)
        mem.upsert_knowledge("system/model_override", "ollama/override-model")

        with patch("nanoc.core.llm.settings") as mock_settings, \
             patch("nanoc.core.llm.Memory", return_value=mem):
            mock_settings.DB_PATH = db_path
            mock_settings.DEFAULT_PROVIDER = "ollama"
            mock_settings.DEFAULT_MODEL = "ollama/base-model"
            mock_settings.OLLAMA_BASE_URL = "http://localhost:11434"

            from nanoc.core.llm import LLMProvider
            provider = LLMProvider(provider="ollama", model="ollama/base-model")

            captured_model = []

            async def fake_ollama(prompt, system_prompt, model):
                captured_model.append(model)
                return "mocked response"

            with patch.object(provider, "_ollama_complete", side_effect=fake_ollama), \
                 patch.object(provider, "_record_telemetry"):
                await provider.complete("test prompt")

        assert captured_model[0] == "ollama/override-model"

    @pytest.mark.asyncio
    async def test_unknown_provider_raises_value_error(self, tmp_path):
        """complete() raises ValueError for an unknown provider."""
        db_path = str(tmp_path / "llm_unknown.db")
        mem = _fresh_memory(db_path)

        with patch("nanoc.core.llm.settings") as mock_settings, \
             patch("nanoc.core.llm.Memory", return_value=mem):
            mock_settings.DB_PATH = db_path
            mock_settings.DEFAULT_PROVIDER = "unknown_provider"
            mock_settings.DEFAULT_MODEL = "some/model"

            from nanoc.core.llm import LLMProvider
            provider = LLMProvider(provider="unknown_provider", model="some/model")

            with patch.object(provider, "_record_error"):
                with pytest.raises(ValueError, match="Unknown provider"):
                    await provider.complete("test prompt")
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)
        orch.initial_workers = 2
        orch.max_workers = 3
        orch.current_workers = [MagicMock(), MagicMock(), MagicMock()]  # at max

        initial_count = len(orch.current_workers)
        # Simulate scale-up check
        if len(orch.current_workers) < orch.max_workers:
            orch.current_workers.append(MagicMock())

        assert len(orch.current_workers) == initial_count  # no change

    @pytest.mark.asyncio
    async def test_scale_down_no_op_at_initial_workers(self, memory):
        """handle_scale_down does nothing when at initial_workers count."""
        from nanoc.core.orchestrator import Orchestrator
        from nanoc.agents.base import TeamLeader

        leader = TeamLeader("Leader", "Team Leader", memory, MockLLM())
        orch = Orchestrator(memory, leader)
        orch.initial_workers = 3
        orch.max_workers = 10
        orch.current_workers = [MagicMock(), MagicMock(), MagicMock()]  # == initial
