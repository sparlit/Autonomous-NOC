"""Tests for the event-driven automation engine.

Coverage:

* the declarative matcher (operators, dotted paths, type tolerance),
* rule parsing and rule-file loading (including bad input),
* sustain windows, cooldowns and the bounded queue,
* the three action types and their failure modes,
* run persistence and pagination,
* the REST surface,
* one end-to-end pass through the real ``EventBus``.

The style follows ``test_streaming.py``: plain ``asyncio.run`` inside sync tests
(no pytest-asyncio mode), and a throwaway SQLite file per test.
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app.api.endpoints import automation as automation_api
from app.api.endpoints import websockets
from app.core import automation as automation_module
from app.core.automation import (
    OUTCOME_FAILED,
    OUTCOME_QUEUED,
    OUTCOME_SKIPPED,
    OUTCOME_SUCCESS,
    TOPIC_AUTOMATION_RUN,
    AutomationEngine,
    AutomationRule,
    Condition,
    get_field,
    load_rules,
    render_template,
    subscription_pattern,
)
from app.core.automation_store import AutomationStore
from nanoc.core.event_bus import EventBus
from nanoc.memory.memory import Memory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
ALERT_TOPIC = "alert/created"
METRIC_TOPIC = "metric/snapshot"


class Clock:
    """Deterministic monotonic clock for cooldown/sustain tests."""

    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class RecordingBus:
    """Minimal stand-in for ``EventBus`` that records publications."""

    def __init__(self):
        self.published = []
        self.subscriptions = {}

    def publish(self, topic, payload):
        self.published.append((topic, payload))

    def subscribe(self, topic, callback):
        self.subscriptions.setdefault(topic, []).append(callback)

    def topics(self):
        return [topic for topic, _ in self.published]


def make_store(tmp_path, name="automation.db") -> AutomationStore:
    """Create a store backed by a throwaway database."""
    return AutomationStore(str(tmp_path / name))


def make_rule(**overrides) -> AutomationRule:
    """Build a rule from a dict, with sane defaults."""
    document = {
        "id": "rule-a",
        "name": "Rule A",
        "trigger": {
            "topic": ALERT_TOPIC,
            "conditions": [{"field": "severity", "op": "eq", "value": "critical"}],
        },
        "actions": [{"type": "notify", "message": "alert {title}"}],
    }
    document.update(overrides)
    return AutomationRule.from_dict(document)


def critical_alert(**extra):
    """A realistic ``alert/created`` payload."""
    payload = {
        "id": "rule:latency_slo",
        "rule": "latency_slo",
        "title": "Latency SLO breach",
        "description": "Observed latency 900ms exceeds the 50ms SLO threshold.",
        "severity": "critical",
        "status": "active",
        "action": "created",
        "timestamp": "2026-04-20T12:00:00",
    }
    payload.update(extra)
    return payload


# ---------------------------------------------------------------------------
# Field access and operators
# ---------------------------------------------------------------------------
class TestFieldAccess:
    def test_reads_a_flat_key(self):
        assert get_field({"severity": "critical"}, "severity") == (True, "critical")

    def test_reads_a_dotted_path(self):
        payload = {"devices": {"critical": 2}}
        assert get_field(payload, "devices.critical") == (True, 2)

    def test_missing_key_is_not_found(self):
        assert get_field({"a": 1}, "b") == (False, None)

    def test_missing_nested_key_is_not_found(self):
        assert get_field({"a": 1}, "a.b") == (False, None)

    def test_explicit_none_counts_as_missing(self):
        assert get_field({"latency_ms": None}, "latency_ms") == (False, None)


class TestOperators:
    def _check(self, payload, field, op, value):
        return Condition(field=field, op=op, value=value).evaluate(payload)[0]

    def test_eq_matches_string(self):
        assert self._check({"severity": "critical"}, "severity", "eq", "critical")

    def test_eq_is_type_tolerant(self):
        assert self._check({"latency_ms": 500}, "latency_ms", "eq", "500")
        assert self._check({"latency_ms": "500"}, "latency_ms", "eq", 500)

    def test_ne_matches_absent_field(self):
        """An event with no severity is "not critical"."""
        assert self._check({}, "severity", "ne", "critical")

    def test_gt_compares_numerically(self):
        assert self._check({"latency_ms": 501}, "latency_ms", "gt", 500)
        assert not self._check({"latency_ms": 500}, "latency_ms", "gt", 500)

    def test_gte_and_lte_are_inclusive(self):
        assert self._check({"backlog": 10}, "backlog", "gte", 10)
        assert self._check({"backlog": 10}, "backlog", "lte", 10)
        assert not self._check({"backlog": 11}, "backlog", "lte", 10)

    def test_lt_handles_string_numbers(self):
        assert self._check({"latency_ms": "12.5"}, "latency_ms", "lt", 50)

    def test_in_matches_any_member(self):
        assert self._check({"severity": "critical"}, "severity", "in", ["critical", "high"])
        assert not self._check({"severity": "warning"}, "severity", "in", ["critical"])

    def test_not_in(self):
        assert self._check({"severity": "warning"}, "severity", "not_in", ["critical"])

    def test_contains_and_startswith(self):
        assert self._check({"title": "Latency SLO breach"}, "title", "contains", "SLO")
        assert self._check({"topic": "alert/created"}, "topic", "startswith", "alert/")

    def test_exists(self):
        assert self._check({"a": 1}, "a", "exists", True)
        assert not self._check({}, "a", "exists", True)
        assert self._check({}, "a", "exists", False)

    def test_reason_describes_the_comparison(self):
        matched, reason = Condition("severity", "eq", "critical").evaluate(
            {"severity": "critical"}
        )
        assert matched and "severity" in reason and "critical" in reason

    def test_unknown_operator_is_rejected_at_parse_time(self):
        with pytest.raises(ValueError):
            Condition.from_dict({"field": "a", "op": "sql_injection"})

    def test_condition_without_field_is_rejected(self):
        with pytest.raises(ValueError):
            Condition.from_dict({"op": "eq", "value": 1})


class TestTemplates:
    def test_renders_payload_fields(self):
        assert render_template("{rule}: {title}", critical_alert()) == (
            "latency_slo: Latency SLO breach"
        )

    def test_renders_nested_fields(self):
        assert render_template("crit={devices.critical}", {"devices": {"critical": 2}}) == "crit=2"

    def test_missing_fields_render_empty(self):
        assert render_template("x={nope}", {}) == "x="

    def test_literal_braces_are_left_alone(self):
        """Alert descriptions contain braces; str.format would explode here."""
        assert render_template('{"a": 1}', {}) == '{"a": 1}'


# ---------------------------------------------------------------------------
# Rule parsing and loading
# ---------------------------------------------------------------------------
class TestRuleParsing:
    def test_parses_conditions_sustain_and_cooldown(self):
        rule = make_rule(
            trigger={
                "topic": METRIC_TOPIC,
                "conditions": [{"field": "latency_ms", "op": "gt", "value": 500}],
                "sustain": {"count": 3},
                "cooldown_seconds": 60,
            }
        )
        assert rule.topic == METRIC_TOPIC
        assert rule.sustain_count == 3
        assert rule.cooldown_seconds == 60.0
        assert rule.conditions[0].op == "gt"

    def test_no_conditions_matches_everything(self):
        rule = make_rule(trigger={"topic": "alert/resolved", "conditions": []})
        assert rule.evaluate({}) == (True, "unconditional")

    def test_all_conditions_must_match(self):
        rule = make_rule(
            trigger={
                "topic": ALERT_TOPIC,
                "conditions": [
                    {"field": "severity", "op": "eq", "value": "critical"},
                    {"field": "rule", "op": "eq", "value": "latency_slo"},
                ],
            }
        )
        assert rule.evaluate(critical_alert())[0]
        assert not rule.evaluate(critical_alert(rule="device_down"))[0]

    def test_unsupported_action_is_rejected(self):
        with pytest.raises(ValueError):
            make_rule(actions=[{"type": "shell", "command": "rm -rf /"}])

    def test_rule_without_topic_is_rejected(self):
        with pytest.raises(ValueError):
            make_rule(trigger={"conditions": []})

    def test_summary_exposes_no_secrets(self):
        rule = make_rule(actions=[{"type": "webhook", "url": "http://x/secret"}])
        summary = rule.summary()
        assert summary["actions"] == [{"type": "webhook"}]
        assert "secret" not in json.dumps(summary)


class TestRuleLoading:
    def test_loads_the_shipped_rules(self):
        """The example rules must stay parseable and cover all three actions."""
        rules = load_rules()
        assert len(rules) >= 3
        topics = {rule.topic for rule in rules}
        assert "alert/created" in topics
        assert "metric/snapshot" in topics
        actions = {a["type"] for rule in rules for a in rule.actions}
        assert {"notify", "webhook", "command"} <= actions

    def test_missing_file_yields_no_rules(self, tmp_path):
        assert load_rules(str(tmp_path / "nope.json")) == []

    def test_malformed_file_yields_no_rules(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        assert load_rules(str(path)) == []

    def test_invalid_rule_is_skipped_not_fatal(self, tmp_path):
        path = tmp_path / "mixed.json"
        path.write_text(
            json.dumps(
                {
                    "rules": [
                        {"id": "bad", "trigger": {}, "actions": []},
                        {
                            "id": "good",
                            "trigger": {"topic": ALERT_TOPIC},
                            "actions": [{"type": "notify", "message": "hi"}],
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        rules = load_rules(str(path))
        assert [rule.id for rule in rules] == ["good"]

    def test_disabled_rule_is_not_loaded(self, tmp_path):
        path = tmp_path / "off.json"
        path.write_text(
            json.dumps(
                {
                    "rules": [
                        {
                            "id": "off",
                            "enabled": False,
                            "trigger": {"topic": ALERT_TOPIC},
                            "actions": [{"type": "notify"}],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        assert load_rules(str(path)) == []


# ---------------------------------------------------------------------------
# Matching, sustain, cooldown, bounded queue
# ---------------------------------------------------------------------------
class TestMatching:
    def test_topic_mismatch_never_matches(self, tmp_path):
        engine = AutomationEngine(store=make_store(tmp_path), rules=[make_rule()])
        assert engine.handle_event(METRIC_TOPIC, critical_alert()) == []

    def test_condition_mismatch_does_not_queue(self, tmp_path):
        engine = AutomationEngine(store=make_store(tmp_path), rules=[make_rule()])
        assert engine.handle_event(ALERT_TOPIC, critical_alert(severity="warning")) == []

    def test_match_is_queued_with_a_reason(self, tmp_path):
        engine = AutomationEngine(store=make_store(tmp_path), rules=[make_rule()])
        queued = engine.handle_event(ALERT_TOPIC, critical_alert())
        assert len(queued) == 1
        assert queued[0]["rule_id"] == "rule-a"
        assert "severity" in queued[0]["reason"]
        assert engine.queue_depth == 1

    def test_sustain_requires_consecutive_matches(self, tmp_path):
        rule = make_rule(
            trigger={
                "topic": METRIC_TOPIC,
                "conditions": [{"field": "latency_ms", "op": "gt", "value": 500}],
                "sustain": {"count": 3},
            }
        )
        engine = AutomationEngine(store=make_store(tmp_path), rules=[rule])
        hot = {"latency_ms": 900}
        assert engine.handle_event(METRIC_TOPIC, hot) == []
        assert engine.handle_event(METRIC_TOPIC, hot) == []
        assert len(engine.handle_event(METRIC_TOPIC, hot)) == 1
        # A single quiet sample resets the window.
        engine.handle_event(METRIC_TOPIC, {"latency_ms": 10})
        assert engine.handle_event(METRIC_TOPIC, hot) == []

    def test_cooldown_suppresses_a_second_fire(self, tmp_path):
        clock = Clock()
        rule = make_rule(
            trigger={
                "topic": ALERT_TOPIC,
                "conditions": [{"field": "severity", "op": "eq", "value": "critical"}],
                "cooldown_seconds": 60,
            }
        )
        engine = AutomationEngine(
            store=make_store(tmp_path), rules=[rule], clock=clock
        )
        assert len(engine.handle_event(ALERT_TOPIC, critical_alert())) == 1
        clock.advance(30)
        assert engine.handle_event(ALERT_TOPIC, critical_alert()) == []
        clock.advance(31)
        assert len(engine.handle_event(ALERT_TOPIC, critical_alert())) == 1

    def test_full_queue_drops_matches_instead_of_blocking(self, tmp_path):
        rules = [make_rule(id="r1"), make_rule(id="r2"), make_rule(id="r3")]
        engine = AutomationEngine(
            store=make_store(tmp_path), rules=rules, queue_size=2
        )
        queued = engine.handle_event(ALERT_TOPIC, critical_alert())
        assert len(queued) == 2
        assert engine._dropped == 1
        assert engine.queue_depth == 2

    def test_disabled_rules_are_ignored(self, tmp_path):
        rule = make_rule()
        rule.enabled = False
        engine = AutomationEngine(store=make_store(tmp_path), rules=[rule])
        assert engine.handle_event(ALERT_TOPIC, critical_alert()) == []


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------
class TestNotifyAction:
    def test_run_is_persisted(self, tmp_path):
        store = make_store(tmp_path)
        engine = AutomationEngine(store=store, rules=[make_rule()])

        async def run():
            await engine.start()
            engine.handle_event(ALERT_TOPIC, critical_alert())
            await engine._queue.join()
            await engine.stop()

        asyncio.run(run())
        runs, total = store.list_runs()
        assert total == 1
        run_record = runs[0]
        assert run_record["rule_id"] == "rule-a"
        assert run_record["action_type"] == "notify"
        assert run_record["outcome"] == OUTCOME_SUCCESS
        assert run_record["trigger_topic"] == ALERT_TOPIC
        assert "severity" in run_record["matched_condition"]
        assert run_record["detail"] == "alert Latency SLO breach"
        assert run_record["duration_ms"] is not None
        assert run_record["event_payload"]["severity"] == "critical"

    def test_one_row_per_action(self, tmp_path):
        store = make_store(tmp_path)
        rule = make_rule(
            actions=[
                {"type": "notify", "message": "a"},
                {"type": "notify", "message": "b"},
            ]
        )
        engine = AutomationEngine(store=store, rules=[rule])

        async def run():
            await engine.start()
            engine.handle_event(ALERT_TOPIC, critical_alert())
            await engine._queue.join()
            await engine.stop()

        asyncio.run(run())
        _, total = store.list_runs()
        assert total == 2


class TestWebhookAction:
    def _engine(self, tmp_path, **action):
        rule = make_rule(actions=[{"type": "webhook", **action}])
        return AutomationEngine(
            store=make_store(tmp_path), rules=[rule], retry_backoff_s=0
        )

    def test_unconfigured_url_is_skipped(self, tmp_path, monkeypatch):
        monkeypatch.setattr(automation_module.settings, "AUTOMATION_WEBHOOK_URL", "")
        engine = self._engine(tmp_path, url="${AUTOMATION_WEBHOOK_URL}")

        async def run():
            return await engine.run_rule("rule-a", critical_alert())

        runs = asyncio.run(run())
        assert runs[0]["outcome"] == OUTCOME_SKIPPED
        assert "AUTOMATION_WEBHOOK_URL" in runs[0]["detail"]

    def test_successful_post(self, tmp_path, monkeypatch):
        calls = []

        class FakeResponse:
            status_code = 200

        class FakeClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def post(self, url, content=None, headers=None):
                calls.append({"url": url, "body": json.loads(content), "headers": headers})
                return FakeResponse()

        monkeypatch.setattr(automation_module.httpx, "AsyncClient", FakeClient)
        monkeypatch.setattr(
            automation_module.settings, "AUTOMATION_WEBHOOK_URL", "http://hook.test/x"
        )
        engine = self._engine(tmp_path, url="${AUTOMATION_WEBHOOK_URL}", retries=0)

        async def run():
            return await engine.run_rule("rule-a", critical_alert())

        runs = asyncio.run(run())
        assert runs[0]["outcome"] == OUTCOME_SUCCESS
        assert calls[0]["url"] == "http://hook.test/x"
        assert calls[0]["body"]["rule_id"] == "rule-a"
        assert calls[0]["body"]["event"]["severity"] == "critical"

    def test_retries_then_fails_without_raising(self, tmp_path, monkeypatch):
        attempts = []

        class FakeResponse:
            status_code = 503

        class FakeClient:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def post(self, url, content=None, headers=None):
                attempts.append(url)
                return FakeResponse()

        monkeypatch.setattr(automation_module.httpx, "AsyncClient", FakeClient)
        engine = self._engine(tmp_path, url="http://hook.test/down", retries=2)

        async def run():
            return await engine.run_rule("rule-a", critical_alert())

        runs = asyncio.run(run())
        assert runs[0]["outcome"] == OUTCOME_FAILED
        assert len(attempts) == 3  # first attempt + 2 retries
        assert "503" in runs[0]["detail"]

    def test_client_side_error_is_not_retried(self, tmp_path, monkeypatch):
        attempts = []

        class FakeResponse:
            status_code = 404

        class FakeClient:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def post(self, url, content=None, headers=None):
                attempts.append(url)
                return FakeResponse()

        monkeypatch.setattr(automation_module.httpx, "AsyncClient", FakeClient)
        engine = self._engine(tmp_path, url="http://hook.test/gone", retries=3)

        async def run():
            return await engine.run_rule("rule-a", critical_alert())

        runs = asyncio.run(run())
        assert runs[0]["outcome"] == OUTCOME_FAILED
        assert len(attempts) == 1

    def test_network_exception_is_an_outcome_not_a_crash(self, tmp_path, monkeypatch):
        class BoomClient:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def post(self, url, content=None, headers=None):
                raise ConnectionError("dns failure")

        monkeypatch.setattr(automation_module.httpx, "AsyncClient", BoomClient)
        engine = self._engine(tmp_path, url="http://hook.test/x", retries=1)

        async def run():
            return await engine.run_rule("rule-a", critical_alert())

        runs = asyncio.run(run())
        assert runs[0]["outcome"] == OUTCOME_FAILED
        assert "dns failure" in runs[0]["detail"]

    def test_signature_header_is_sent_when_secret_is_set(self, tmp_path, monkeypatch):
        seen = {}

        class FakeResponse:
            status_code = 204

        class FakeClient:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def post(self, url, content=None, headers=None):
                seen.update(headers or {})
                return FakeResponse()

        monkeypatch.setattr(automation_module.httpx, "AsyncClient", FakeClient)
        monkeypatch.setattr(
            automation_module.settings, "AUTOMATION_WEBHOOK_SECRET", "s3cret"
        )
        engine = self._engine(tmp_path, url="http://hook.test/x", retries=0)

        asyncio.run(engine.run_rule("rule-a", critical_alert()))
        assert seen["X-NOC-Signature"].startswith("sha256=")


class TestCommandAction:
    def test_command_is_queued_not_executed(self, tmp_path):
        store = make_store(tmp_path)
        rule = make_rule(
            actions=[
                {
                    "type": "command",
                    "command": "noc-remediate latency --threshold-ms 500",
                    "reason": "sustained breach",
                    "severity": "critical",
                }
            ]
        )
        engine = AutomationEngine(store=store, rules=[rule])

        async def run():
            return await engine.run_rule("rule-a", critical_alert())

        runs = asyncio.run(run())
        assert runs[0]["outcome"] == OUTCOME_QUEUED

        actions, total = store.list_pending_actions()
        assert total == 1
        assert actions[0]["command"] == "noc-remediate latency --threshold-ms 500"
        assert actions[0]["status"] == "pending"
        assert actions[0]["requested_by"] == "automation:rule-a"
        assert actions[0]["severity"] == "critical"
        assert actions[0]["reason"] == "sustained breach"
        # The queued action cites the run that requested it (back-filled once
        # the run row exists).
        assert actions[0]["run_id"] == runs[0]["id"]

    def test_empty_command_is_skipped(self, tmp_path):
        store = make_store(tmp_path)
        engine = AutomationEngine(
            store=store, rules=[make_rule(actions=[{"type": "command", "command": ""}])]
        )
        runs = asyncio.run(engine.run_rule("rule-a", critical_alert()))
        assert runs[0]["outcome"] == OUTCOME_SKIPPED
        assert store.list_pending_actions()[1] == 0


class TestFailureIsolation:
    def test_a_broken_action_does_not_stop_the_next_one(self, tmp_path, monkeypatch):
        """Webhook explodes; the following notify still records a run."""

        class BoomClient:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def post(self, url, content=None, headers=None):
                raise RuntimeError("kaboom")

        monkeypatch.setattr(automation_module.httpx, "AsyncClient", BoomClient)
        store = make_store(tmp_path)
        rule = make_rule(
            actions=[
                {"type": "webhook", "url": "http://hook.test/x", "retries": 0},
                {"type": "notify", "message": "still here"},
            ]
        )
        engine = AutomationEngine(store=store, rules=[rule], retry_backoff_s=0)
        runs = asyncio.run(engine.run_rule("rule-a", critical_alert()))
        assert [r["outcome"] for r in runs] == [OUTCOME_FAILED, OUTCOME_SUCCESS]

    def test_engine_survives_a_store_failure(self, tmp_path):
        """A database that refuses writes must not kill the worker pool."""
        store = make_store(tmp_path)
        store.db_path = str(tmp_path / "does-not-exist-dir" / "x.db")
        engine = AutomationEngine(store=store, rules=[make_rule()])

        async def run():
            await engine.start()
            engine.handle_event(ALERT_TOPIC, critical_alert())
            await engine._queue.join()
            assert engine.running
            await engine.stop()

        asyncio.run(run())


# ---------------------------------------------------------------------------
# Publishing runs back onto the bus
# ---------------------------------------------------------------------------
class TestRunPublishing:
    def test_run_is_published_as_a_flat_automation_event(self, tmp_path):
        bus = RecordingBus()
        engine = AutomationEngine(store=make_store(tmp_path), rules=[make_rule()])
        engine.bind_bus(bus)

        asyncio.run(engine.run_rule("rule-a", critical_alert()))
        assert bus.topics() == [TOPIC_AUTOMATION_RUN]
        payload = bus.published[0][1]
        # Flat shape: run fields at the root, ready for the /ws envelope.
        assert payload["rule_id"] == "rule-a"
        assert payload["outcome"] == OUTCOME_SUCCESS

    def test_publish_failure_is_swallowed(self, tmp_path):
        class BrokenBus:
            def publish(self, topic, payload):
                raise RuntimeError("database is locked")

        engine = AutomationEngine(store=make_store(tmp_path), rules=[make_rule()])
        engine.bind_bus(BrokenBus())
        runs = asyncio.run(engine.run_rule("rule-a", critical_alert()))
        assert runs[0]["outcome"] == OUTCOME_SUCCESS

    def test_topic_maps_to_the_automation_ws_type(self):
        assert websockets.classify_topic(TOPIC_AUTOMATION_RUN) == "automation"

    def test_envelope_carries_the_run_fields_flat(self):
        message = websockets.envelope(
            {
                "_topic": TOPIC_AUTOMATION_RUN,
                "_event_id": 3,
                "_timestamp": "2026-04-20T12:00:00",
                "rule_id": "rule-a",
                "outcome": "success",
            }
        )
        assert message["type"] == "automation"
        assert message["rule_id"] == "rule-a"
        assert message["event_id"] == 3


# ---------------------------------------------------------------------------
# Manual runs
# ---------------------------------------------------------------------------
class TestManualRun:
    def test_unknown_rule_raises_key_error(self, tmp_path):
        engine = AutomationEngine(store=make_store(tmp_path), rules=[make_rule()])
        with pytest.raises(KeyError):
            asyncio.run(engine.run_rule("nope"))

    def test_manual_run_ignores_conditions_but_says_so(self, tmp_path):
        store = make_store(tmp_path)
        engine = AutomationEngine(store=store, rules=[make_rule()])
        runs = asyncio.run(
            engine.run_rule("rule-a", {"severity": "warning", "title": "quiet"})
        )
        assert runs[0]["outcome"] == OUTCOME_SUCCESS
        assert runs[0]["trigger_source"] == "manual"
        assert "manual override" in runs[0]["matched_condition"]


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------
class TestStore:
    def test_pagination_is_newest_first(self, tmp_path):
        store = make_store(tmp_path)
        for index in range(5):
            store.record_run(
                {"rule_id": "r", "action_type": "notify", "outcome": "success",
                 "detail": f"run {index}"}
            )
        page, total = store.list_runs(limit=2, offset=0)
        assert total == 5
        assert [row["detail"] for row in page] == ["run 4", "run 3"]
        page2, _ = store.list_runs(limit=2, offset=2)
        assert [row["detail"] for row in page2] == ["run 2", "run 1"]

    def test_filters_by_rule_and_outcome(self, tmp_path):
        store = make_store(tmp_path)
        store.record_run({"rule_id": "a", "outcome": "success"})
        store.record_run({"rule_id": "b", "outcome": "failed"})
        assert store.list_runs(rule_id="a")[1] == 1
        assert store.list_runs(outcome="failed")[1] == 1
        assert store.list_runs(rule_id="a", outcome="failed")[1] == 0

    def test_payload_round_trips_as_json(self, tmp_path):
        store = make_store(tmp_path)
        store.record_run({"rule_id": "a", "event_payload": {"severity": "critical"}})
        row = store.list_runs()[0][0]
        assert row["event_payload"] == {"severity": "critical"}

    def test_oversized_payload_is_truncated(self, tmp_path):
        store = make_store(tmp_path)
        store.record_run({"rule_id": "a", "event_payload": {"blob": "x" * 10000}})
        row = store.list_runs()[0][0]
        assert isinstance(row["event_payload"], str) is False
        # Truncated payloads no longer parse as JSON, so they surface as None.
        assert row["event_payload"] is None

    def test_outcome_counts(self, tmp_path):
        store = make_store(tmp_path)
        store.record_run({"rule_id": "a", "outcome": "success"})
        store.record_run({"rule_id": "a", "outcome": "success"})
        store.record_run({"rule_id": "a", "outcome": "failed"})
        assert store.outcome_counts() == {"success": 2, "failed": 1}

    def test_pending_actions_can_be_listed_by_status(self, tmp_path):
        store = make_store(tmp_path)
        store.enqueue_action({"rule_id": "a", "command": "do-it"})
        pending, total = store.list_pending_actions()
        assert total == 1 and pending[0]["status"] == "pending"
        assert store.list_pending_actions(status="executed")[1] == 0
        assert store.list_pending_actions(status=None)[1] == 1


# ---------------------------------------------------------------------------
# Engine status
# ---------------------------------------------------------------------------
class TestStatus:
    def test_status_reports_the_engine_shape(self, tmp_path):
        engine = AutomationEngine(store=make_store(tmp_path), rules=[make_rule()])
        status = engine.status()
        assert status["rules"] == 1
        assert status["running"] is False
        assert status["queue_capacity"] > 0
        assert status["run_topic"] == TOPIC_AUTOMATION_RUN

    def test_start_and_stop_are_idempotent(self, tmp_path):
        engine = AutomationEngine(store=make_store(tmp_path), rules=[make_rule()])

        async def run():
            await engine.start()
            await engine.start()
            assert engine.running
            await engine.stop()
            assert not engine.running
            await engine.stop()

        asyncio.run(run())


class TestSubscriptionPattern:
    def test_namespaced_topic_widens_to_its_namespace(self):
        assert subscription_pattern("alert/created") == "alert/*"
        assert subscription_pattern("metric/snapshot") == "metric/*"

    def test_flat_topic_is_subscribed_verbatim(self):
        assert subscription_pattern("tick") == "tick"

    def test_two_alert_rules_share_one_subscription(self, tmp_path):
        bus = RecordingBus()
        rules = [
            make_rule(id="a"),
            make_rule(id="b", trigger={"topic": "alert/resolved", "conditions": []}),
        ]
        engine = AutomationEngine(store=make_store(tmp_path), rules=rules)
        assert engine.subscribe(bus) == ["alert/*"]
        assert len(bus.subscriptions["alert/*"]) == 1

    def test_namespace_subscriber_filters_by_exact_topic(self, tmp_path):
        """An alert/resolved event must not fire an alert/created rule."""
        bus = RecordingBus()
        engine = AutomationEngine(store=make_store(tmp_path), rules=[make_rule()])
        engine.subscribe(bus)
        handler = bus.subscriptions["alert/*"][0]
        handler({"severity": "critical", "_topic": "alert/resolved", "_event_id": 9})
        assert engine.queue_depth == 0


# ---------------------------------------------------------------------------
# End to end through the real EventBus
# ---------------------------------------------------------------------------
class TestEventBusIntegration:
    def test_alert_event_fires_a_rule_end_to_end(self, tmp_path):
        """publish → EventBus poll → rule match → worker → run row + bus event."""

        async def run():
            db = str(tmp_path / "bus.db")
            memory = Memory(db)
            bus = EventBus(memory)
            store = AutomationStore(db)
            engine = AutomationEngine(
                store=store, rules=[make_rule()], max_concurrency=2
            )
            engine.bind_bus(bus)
            topics = engine.subscribe(bus)
            assert topics == ["alert/*"]

            await engine.start()
            poller = asyncio.create_task(bus.start_polling(interval=0.02))
            bus.publish(ALERT_TOPIC, critical_alert())

            deadline = asyncio.get_running_loop().time() + 3.0
            while asyncio.get_running_loop().time() < deadline:
                if store.list_runs()[1] >= 1:
                    break
                await asyncio.sleep(0.02)

            bus.stop_polling()
            await engine.stop()
            poller.cancel()
            try:
                await poller
            except asyncio.CancelledError:
                pass

            runs, total = store.list_runs()
            assert total == 1, "rule did not fire from a real bus event"
            assert runs[0]["rule_id"] == "rule-a"
            assert runs[0]["trigger_topic"] == ALERT_TOPIC
            assert runs[0]["trigger_event_id"] is not None
            assert runs[0]["outcome"] == OUTCOME_SUCCESS

            published = memory.get_events(topic=TOPIC_AUTOMATION_RUN)
            assert len(published) == 1
            body = json.loads(published[0]["payload"])
            assert body["rule_id"] == "rule-a"

        asyncio.run(run())


# ---------------------------------------------------------------------------
# REST surface
# ---------------------------------------------------------------------------
@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    """TestClient with the automation singleton pointed at a throwaway store."""
    from fastapi.testclient import TestClient

    from main import app

    store = AutomationStore(str(tmp_path / "api.db"))
    monkeypatch.setattr(automation_api.automation_engine, "_store", store)
    monkeypatch.setattr(automation_api.automation_engine, "_rules", [make_rule()])
    return TestClient(app), store


class TestApi:
    def test_rules_endpoint_lists_rules(self, api_client):
        client, _ = api_client
        body = client.get("/api/automation/rules").json()
        assert body["count"] == 1
        assert body["rules"][0]["id"] == "rule-a"

    def test_runs_endpoint_is_paged(self, api_client):
        client, store = api_client
        for index in range(3):
            store.record_run(
                {"rule_id": "rule-a", "action_type": "notify",
                 "outcome": "success", "detail": f"r{index}"}
            )
        body = client.get("/api/automation/runs?limit=2&offset=0").json()
        assert body["total"] == 3
        assert body["limit"] == 2
        assert body["has_more"] is True
        assert len(body["runs"]) == 2

        page2 = client.get("/api/automation/runs?limit=2&offset=2").json()
        assert page2["has_more"] is False

    def test_runs_filters(self, api_client):
        client, store = api_client
        store.record_run({"rule_id": "rule-a", "outcome": "success"})
        store.record_run({"rule_id": "rule-b", "outcome": "failed"})
        assert client.get("/api/automation/runs?rule_id=rule-a").json()["total"] == 1
        assert client.get("/api/automation/runs?outcome=failed").json()["total"] == 1

    def test_runs_rejects_an_oversized_page(self, api_client):
        client, _ = api_client
        assert client.get("/api/automation/runs?limit=1000").status_code == 422

    def test_manual_run_records_a_run(self, api_client):
        client, store = api_client
        response = client.post(
            "/api/automation/rules/rule-a/run", json={"title": "manual", "severity": "critical"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["trigger"] == "manual"
        assert body["runs"][0]["outcome"] == OUTCOME_SUCCESS
        assert store.list_runs()[1] == 1

    def test_manual_run_unknown_rule_is_404(self, api_client):
        client, _ = api_client
        assert client.post("/api/automation/rules/nope/run", json={}).status_code == 404

    def test_pending_endpoint(self, api_client):
        client, store = api_client
        store.enqueue_action({"rule_id": "rule-a", "command": "noc-remediate"})
        body = client.get("/api/automation/pending").json()
        assert body["total"] == 1
        assert body["actions"][0]["command"] == "noc-remediate"

    def test_status_endpoint(self, api_client):
        client, _ = api_client
        body = client.get("/api/automation/status").json()
        assert body["rules"] == 1
        assert "queue_depth" in body
        assert "outcomes" in body

    def test_existing_routes_still_registered(self, api_client):
        """Additive change: nothing pre-existing may disappear."""
        from tests.route_utils import route_paths

        from main import app

        paths = route_paths(app)
        for path in (
            "/health",
            "/ws",
            "/api/monitoring/status",
            "/api/alerts/summary",
            "/api/data/topology",
            "/api/terminal/ws",
            "/api/automation/rules",
            "/api/automation/runs",
        ):
            assert path in paths
