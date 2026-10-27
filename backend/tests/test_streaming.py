"""Tests for the live-streaming layer: envelope, fan-out, rules and producers."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app.api.endpoints import websockets
from app.core import alert_rules
from app.core.host_metrics import (
    ThroughputSampler,
    is_relevant_interface,
    parse_net_dev,
    sum_relevant,
)
from app.core.streaming import TOPIC_ALERT_CREATED, TOPIC_DATA_UPDATE, TOPIC_METRIC, StreamEngine


# ---------------------------------------------------------------------------
# Wire format
# ---------------------------------------------------------------------------
class TestEnvelope:
    def test_metric_topic_maps_to_metric_type(self):
        assert websockets.classify_topic("metric/snapshot") == "metric"

    def test_alert_topics_map_to_alert_type(self):
        assert websockets.classify_topic("alert/created") == "alert"
        assert websockets.classify_topic("alert/resolved") == "alert"
        assert websockets.classify_topic("alerts/all") == "alert"

    def test_data_topics_map_to_data_update(self):
        assert websockets.classify_topic("data/updated") == "data_update"
        assert websockets.classify_topic("topology/changed") == "data_update"

    def test_agent_topic_maps_to_agent(self):
        assert websockets.classify_topic("agent/thought/start") == "agent"

    def test_unknown_topics_fall_back_to_event(self):
        assert websockets.classify_topic("project/created") == "event"
        assert websockets.classify_topic(None) == "event"
        assert websockets.classify_topic("") == "event"

    def test_envelope_preserves_flat_payload_for_existing_consumers(self):
        """The /agents page reads payload keys off the root object."""
        event = {
            "_topic": "agent/log",
            "_event_id": 7,
            "_timestamp": "2026-04-20T12:00:00",
            "agent_id": "a1",
            "role": "coder",
            "content": "hello",
        }
        message = websockets.envelope(event)

        assert message["type"] == "agent"
        assert message["_topic"] == "agent/log"
        assert message["_event_id"] == 7
        assert message["role"] == "coder"
        assert message["content"] == "hello"
        assert message["topic"] == "agent/log"
        assert message["event_id"] == 7
        assert message["ts"] == "2026-04-20T12:00:00"

    def test_envelope_does_not_mutate_the_input(self):
        event = {"_topic": "metric/snapshot", "_event_id": 1}
        websockets.envelope(event)
        assert "type" not in event


# ---------------------------------------------------------------------------
# Fan-out resilience
# ---------------------------------------------------------------------------
class _FakeSocket:
    def __init__(self, fail=False):
        self.fail = fail
        self.sent = []
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def send_json(self, message):
        if self.fail:
            raise RuntimeError("socket is gone")
        self.sent.append(message)


class TestConnectionManager:
    def test_connect_registers_and_accepts(self):
        async def run():
            manager = websockets.ConnectionManager()
            socket = _FakeSocket()
            await manager.connect(socket)
            assert socket.accepted is True
            assert manager.connection_count == 1

        asyncio.run(run())

    def test_disconnect_is_idempotent(self):
        async def run():
            manager = websockets.ConnectionManager()
            socket = _FakeSocket()
            await manager.connect(socket)
            manager.disconnect(socket)
            manager.disconnect(socket)  # must not raise
            assert manager.connection_count == 0

        asyncio.run(run())

    def test_broadcast_prunes_dead_sockets_and_keeps_delivering(self):
        async def run():
            manager = websockets.ConnectionManager()
            alive_one, dead, alive_two = _FakeSocket(), _FakeSocket(fail=True), _FakeSocket()
            for socket in (alive_one, dead, alive_two):
                await manager.connect(socket)

            await manager.broadcast({"type": "metric"})

            assert alive_one.sent == [{"type": "metric"}]
            assert alive_two.sent == [{"type": "metric"}]
            assert manager.active_connections == [alive_one, alive_two]

        asyncio.run(run())

    def test_broadcast_without_clients_is_a_noop(self):
        async def run():
            await websockets.ConnectionManager().broadcast({"type": "metric"})

        asyncio.run(run())


# ---------------------------------------------------------------------------
# Alert rules
# ---------------------------------------------------------------------------
BASE_SNAPSHOT = {
    "latency_ms": 12.0,
    "backlog": 0,
    "throughput_in_mbps": 10.0,
    "throughput_out_mbps": 5.0,
    "devices": {"operational": 4, "warning": 0, "critical": 0, "maintenance": 0},
    "prometheus_ok": True,
}


def snapshot(**overrides):
    merged = dict(BASE_SNAPSHOT)
    merged.update(overrides)
    return merged


class TestAlertRules:
    def test_healthy_snapshot_fires_nothing(self):
        assert alert_rules.evaluate_alerts(snapshot()) == []

    def test_latency_breach_creates_critical_alert(self):
        alerts = alert_rules.evaluate_alerts(snapshot(latency_ms=120.0))
        assert [a["id"] for a in alerts] == ["rule:latency_slo"]
        assert alerts[0]["severity"] == "critical"

    def test_prometheus_down_creates_warning(self):
        alerts = alert_rules.evaluate_alerts(snapshot(prometheus_ok=False))
        assert "rule:prometheus_unreachable" in [a["id"] for a in alerts]

    def test_backlog_and_throughput_and_device_rules(self):
        alerts = alert_rules.evaluate_alerts(
            snapshot(
                backlog=alert_rules.BACKLOG_WARN,
                throughput_in_mbps=alert_rules.THROUGHPUT_WARN_MBPS + 1,
                devices={"operational": 2, "warning": 1, "critical": 1, "maintenance": 0},
            )
        )
        ids = {a["id"] for a in alerts}
        assert ids == {
            "rule:task_backlog",
            "rule:link_saturation",
            "rule:device_degraded",
            "rule:device_down",
        }

    def test_alert_ids_are_stable_across_evaluations(self):
        first = alert_rules.evaluate_alerts(snapshot(latency_ms=99.0))
        second = alert_rules.evaluate_alerts(snapshot(latency_ms=150.0))
        assert first[0]["id"] == second[0]["id"]

    def test_diff_reports_created_and_resolved_only(self):
        previous = {a["id"]: a for a in alert_rules.evaluate_alerts(snapshot(latency_ms=99.0))}
        current = alert_rules.evaluate_alerts(snapshot(backlog=50))

        created, resolved = alert_rules.diff_alerts(previous, current)

        assert [a["id"] for a in created] == ["rule:task_backlog"]
        assert [a["id"] for a in resolved] == ["rule:latency_slo"]
        assert resolved[0]["status"] == "resolved"

    def test_diff_is_quiet_when_nothing_changed(self):
        current = alert_rules.evaluate_alerts(snapshot(latency_ms=99.0))
        previous = {a["id"]: a for a in current}
        created, resolved = alert_rules.diff_alerts(previous, current)
        assert created == [] and resolved == []


# ---------------------------------------------------------------------------
# Host throughput sampling
# ---------------------------------------------------------------------------
NET_DEV_FIXTURE = """Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo: 5000       50    0    0    0     0          0         0     5000       50    0    0    0     0       0          0
  eth0: 1000000    900    0    0    0     0          0         0   500000      400    0    0    0     0       0          0
docker0: 9999999   999    0    0    0     0          0         0  9999999      999    0    0    0     0       0          0
"""


class TestHostMetrics:
    def test_parse_net_dev_reads_rx_and_tx(self):
        counters = parse_net_dev(NET_DEV_FIXTURE)
        assert counters["eth0"] == (1000000, 500000)
        assert counters["lo"] == (5000, 5000)

    def test_parse_net_dev_skips_header_and_malformed_lines(self):
        counters = parse_net_dev("garbage\neth9: 1 2 3\neth0: 7 1 1 1 1 1 1 1 5 1 1 1 1 1 1 1\n")
        assert counters == {"eth0": (7, 5)}

    def test_virtual_interfaces_are_excluded(self):
        assert is_relevant_interface("eth0") is True
        for name in ("lo", "docker0", "br-abc", "veth12", "virbr0"):
            assert is_relevant_interface(name) is False

    def test_sum_ignores_virtual_interfaces(self):
        assert sum_relevant(parse_net_dev(NET_DEV_FIXTURE)) == (1000000, 500000)

    def test_sampler_returns_empty_on_first_call(self):
        sampler = ThroughputSampler(reader=lambda: {"eth0": (100, 50)})
        assert sampler.sample(now=0.0).inbound_mbps is None

    def test_sampler_computes_mbps_between_samples(self):
        readings = iter([{"eth0": (0, 0)}, {"eth0": (1_250_000, 625_000)}])
        sampler = ThroughputSampler(reader=lambda: next(readings))
        sampler.sample(now=0.0)
        sample = sampler.sample(now=1.0)
        assert sample.inbound_mbps == 10.0
        assert sample.outbound_mbps == 5.0

    def test_sampler_handles_counter_reset(self):
        readings = iter([{"eth0": (1000, 1000)}, {"eth0": (10, 10)}])
        sampler = ThroughputSampler(reader=lambda: next(readings))
        sampler.sample(now=0.0)
        sample = sampler.sample(now=1.0)
        assert sample.inbound_mbps == 0.0


# ---------------------------------------------------------------------------
# Stream engine
# ---------------------------------------------------------------------------
class _RecordingBus:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload):
        self.published.append((topic, payload))

    def topics(self):
        return [topic for topic, _ in self.published]


class TestStreamEngine:
    def test_unbound_engine_swallows_publications(self):
        StreamEngine().publish(TOPIC_METRIC, {"latency": "12ms"})  # must not raise

    def test_publish_forwards_to_the_bus(self):
        engine, bus = StreamEngine(), _RecordingBus()
        engine.bind(bus)
        engine.publish(TOPIC_METRIC, {"latency": "12ms"})
        assert bus.published == [(TOPIC_METRIC, {"latency": "12ms"})]

    def test_publish_swallows_bus_errors(self):
        class BrokenBus:
            def publish(self, topic, payload):
                raise RuntimeError("database is locked")

        engine = StreamEngine()
        engine.bind(BrokenBus())
        engine.publish(TOPIC_METRIC, {})  # must not raise

    def test_publish_alerts_emits_created_then_resolved(self):
        async def run():
            engine, bus = StreamEngine(), _RecordingBus()
            engine.bind(bus)

            await engine.publish_alerts(snapshot(latency_ms=200.0))
            assert bus.topics() == [TOPIC_ALERT_CREATED]
            assert bus.published[0][1]["action"] == "created"
            assert len(engine.active_alerts) == 1

            await engine.publish_alerts(snapshot())
            assert bus.topics() == [TOPIC_ALERT_CREATED, "alert/resolved"]
            assert bus.published[1][1]["action"] == "resolved"
            assert engine.active_alerts == []

        asyncio.run(run())

    def test_publish_alerts_is_quiet_when_conditions_hold(self):
        async def run():
            engine, bus = StreamEngine(), _RecordingBus()
            engine.bind(bus)
            await engine.publish_alerts(snapshot(latency_ms=200.0))
            await engine.publish_alerts(snapshot(latency_ms=210.0))
            assert bus.topics() == [TOPIC_ALERT_CREATED]

        asyncio.run(run())

    def test_data_updates_only_fire_on_change(self):
        async def run():
            engine, bus = StreamEngine(), _RecordingBus()
            engine.bind(bus)

            assert await engine.publish_data_updates({"tasks": "1:a", "topology": "4:x"}) == []
            assert bus.published == []

            changed = await engine.publish_data_updates({"tasks": "2:b", "topology": "4:x"})
            assert changed == ["tasks"]
            assert bus.topics() == [TOPIC_DATA_UPDATE]
            assert bus.published[0][1]["kinds"] == ["tasks"]

            assert await engine.publish_data_updates({"tasks": "2:b", "topology": "4:x"}) == []

        asyncio.run(run())

    def test_stop_ends_the_run_loop(self):
        async def run():
            engine, bus = StreamEngine(), _RecordingBus()
            engine.bind(bus)

            async def stopper():
                await asyncio.sleep(0)
                engine.stop()

            await asyncio.gather(engine.run(tick_interval=0.01), stopper())
            assert engine._running is False

        asyncio.run(run())


# ---------------------------------------------------------------------------
# REST twins of the stream
# ---------------------------------------------------------------------------
class TestStreamEndpoints:
    def test_ws_hello_and_ping_pong(self):
        from fastapi.testclient import TestClient
        from main import app

        with TestClient(app) as client:
            with client.websocket_connect("/ws") as websocket:
                hello = websocket.receive_json()
                assert hello["type"] == "hello"
                assert "metric" in hello["topics"]

                websocket.send_json({"type": "ping", "ts": "now"})
                pong = websocket.receive_json()
                assert pong["type"] == "pong"
                assert pong["echo"] == "now"

    def test_stream_status_reports_client_count(self):
        from fastapi.testclient import TestClient
        from main import app

        with TestClient(app) as client:
            body = client.get("/api/stream/status").json()
            assert body["clients"] == 0
            assert "metric/snapshot" in body["topics"]

    def test_metric_snapshot_endpoint_matches_status_contract(self):
        from fastapi.testclient import TestClient
        from main import app

        with TestClient(app) as client:
            body = client.get("/api/monitoring/snapshot").json()
            for key in ("latency", "uptime", "traffic", "status", "backlog"):
                assert key in body
            assert "devices" in body
            assert "prometheus_ok" in body

    def test_metric_series_endpoint_shape(self):
        from fastapi.testclient import TestClient
        from main import app

        with TestClient(app) as client:
            body = client.get("/api/monitoring/series", params={"limit": 5}).json()
            assert set(body) == {"latency", "throughput", "devices", "backlog"}
            assert len(body["devices"]) == 4
