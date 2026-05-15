import pytest
import os
import asyncio
import time
from nanoc.memory.memory import Memory
from nanoc.core.event_bus import EventBus
from nanoc.core.config import settings

@pytest.fixture
def memory():
    db_path = "nanoc/memory/test_stress.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    mem = Memory(db_path)
    yield mem
    if os.path.exists(db_path):
        os.remove(db_path)

@pytest.mark.asyncio
async def test_event_bus_high_volume(memory):
    bus = EventBus(memory)
    received_count = 0

    async def callback(payload):
        nonlocal received_count
        received_count += 1

    bus.subscribe("stress/test", callback)

    num_events = 500
    start_time = time.time()

    # Publish events rapidly
    for i in range(num_events):
        bus.publish("stress/test", {"index": i})

    # Start polling and wait for all events to be processed
    polling_task = asyncio.create_task(bus.start_polling(interval=0.01))

    # Wait until all events are received or timeout
    for _ in range(100):
        if received_count >= num_events:
            break
        await asyncio.sleep(0.1)

    end_time = time.time()
    bus.stop_polling()
    await polling_task

    assert received_count == num_events
    print(f"Processed {num_events} events in {end_time - start_time:.2f} seconds")

@pytest.mark.asyncio
async def test_memory_concurrent_writes(memory):
    num_tasks = 200

    async def create_task(i):
        memory.create_task(f"Stress task {i}", assigned_to="Tester")

    # Run many writes in parallel
    await asyncio.gather(*[create_task(i) for i in range(num_tasks)])

    import sqlite3
    with sqlite3.connect(memory.db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM tasks")
        count = cursor.fetchone()[0]

    assert count == num_tasks

@pytest.mark.asyncio
async def test_knowledge_rapid_updates(memory):
    num_updates = 300
    key = "stress_key"

    async def update_knowledge(i):
        memory.upsert_knowledge(key, {"value": i})

    await asyncio.gather(*[update_knowledge(i) for i in range(num_updates)])

    val = memory.get_knowledge(key)
    assert val is not None
    # Since they are concurrent, we don't know which one finished last,
    # but we verify the key exists and has a valid value.
    assert "value" in val
