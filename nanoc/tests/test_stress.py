import pytest
import os
import asyncio
import time
from nanoc.memory.memory import Memory
from nanoc.core.event_bus import EventBus
from nanoc.core.config import settings

@pytest.fixture
def memory():
    """
    Provide a pytest fixture that yields a Memory instance backed by a test SQLite database.
    
    The fixture ensures the database file "nanoc/memory/test_stress.db" is removed before creating the Memory instance and removed again after the test to guarantee a clean test environment.
    
    Returns:
        Memory: A Memory instance using the test database at "nanoc/memory/test_stress.db".
    """
    db_path = "nanoc/memory/test_stress.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    mem = Memory(db_path)
    yield mem
    if os.path.exists(db_path):
        os.remove(db_path)

@pytest.mark.asyncio
async def test_event_bus_high_volume(memory):
    """
    Verify that EventBus processes a high volume of published events and delivers them to subscribers.
    
    Publishes 500 events to the "stress/test" topic in rapid succession, starts the bus polling loop, waits up to ~10 seconds for all events to be received, then stops polling and asserts that all published events were delivered. Prints the elapsed processing time.
    """
    bus = EventBus(memory)
    received_count = 0

    async def callback(payload):
        """
        Handle an incoming event payload by incrementing the enclosing received event counter.
        
        Parameters:
            payload: The event payload delivered by the EventBus (contents depend on the publisher).
        """
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
    """
    Stress-test concurrent task creation: concurrently create 200 tasks in the provided Memory instance and verify all writes persisted.
    
    This test runs many create_task calls in parallel against the given `memory` fixture and then checks the underlying SQLite database to ensure the `tasks` table contains exactly 200 rows.
    """
    num_tasks = 200

    async def create_task(i):
        """
        Create a new task in the memory store for a stress test.
        
        Parameters:
            i (int): Index used to generate the task title (e.g., creates "Stress task {i}").
        """
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
    """
    Stress-tests concurrent upserts to the memory knowledge store.
    
    Runs 300 concurrent upserts to the same key and verifies that the key exists afterwards and its stored value contains the "value" field.
    """
    num_updates = 300
    key = "stress_key"

    async def update_knowledge(i):
        """
        Upserts the knowledge entry for the shared `key` with the given value.
        
        Parameters:
            i (int): Value to store in the knowledge entry's "value" field.
        """
        memory.upsert_knowledge(key, {"value": i})

    await asyncio.gather(*[update_knowledge(i) for i in range(num_updates)])

    val = memory.get_knowledge(key)
    assert val is not None
    # Since they are concurrent, we don't know which one finished last,
    # but we verify the key exists and has a valid value.
    assert "value" in val
