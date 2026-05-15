import pytest
import os
import asyncio
from nanoc.memory.memory import Memory
from nanoc.core.event_bus import EventBus
from nanoc.core.gate_manager import GateManager

@pytest.fixture
def memory():
    db_path = "nanoc/memory/test_event.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    mem = Memory(db_path)
    yield mem
    if os.path.exists(db_path):
        os.remove(db_path)

def test_event_bus_publish_subscribe(memory):
    bus = EventBus(memory)
    received = []

    def callback(payload):
        received.append(payload)

    bus.subscribe("test/topic", callback)
    bus.publish("test/topic", {"data": "hello"})

    # Manual poll for test
    events = memory.get_events(since_id=0)
    assert len(events) == 1

    # Simulate polling loop once
    import json
    for e in events:
        if e['topic'] == "test/topic":
            callback(json.loads(e['payload']))

    assert len(received) == 1
    assert received[0]["data"] == "hello"

def test_gate_manager(memory):
    gm = GateManager(memory)
    gate_id = gm.create_gate("p1", "code", "Coder", ["Test1"])

    gate_data = memory.get_knowledge(f"gate:{gate_id}")
    assert gate_data["status"] == "PENDING"

    gm.add_result(gate_id, {"status": "pass", "tester": "QA"})

    gate_data = memory.get_knowledge(f"gate:{gate_id}")
    assert gate_data["status"] == "COMPLETE"
