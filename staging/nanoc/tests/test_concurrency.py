import pytest
import os
import asyncio
from nanoc.memory.memory import Memory
from nanoc.agents.base import Coder, Reviewer
from nanoc.tests.mocks import MockLLM

@pytest.fixture
def memory():
    db_path = "nanoc/memory/test_concurrency.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    mem = Memory(db_path)
    yield mem
    if os.path.exists(db_path):
        os.remove(db_path)

@pytest.mark.asyncio
async def test_parallel_agents_execution(memory):
    mock_llm = MockLLM()
    mock_llm.latency = 0.1 # Add some latency to ensure overlapping

    coder1 = Coder("Coder1", "Coder", memory, provider=mock_llm)
    coder2 = Coder("Coder2", "Coder", memory, provider=mock_llm)

    # Run two coders in parallel
    results = await asyncio.gather(
        coder1.write_code("Task 1"),
        coder2.write_code("Task 2")
    )

    assert len(results) == 2
    assert "Mocked response" in results[0]
    assert "Mocked response" in results[1]

    # Verify tasks were created correctly in DB
    import sqlite3
    with sqlite3.connect(memory.db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM tasks WHERE assigned_to = 'Reviewer'")
        count = cursor.fetchone()[0]

    assert count == 2

@pytest.mark.asyncio
async def test_event_bus_concurrent_subscribers(memory):
    from nanoc.core.event_bus import EventBus
    bus = EventBus(memory)

    results = []

    async def sub1(payload):
        await asyncio.sleep(0.05)
        results.append(f"sub1:{payload['data']}")

    async def sub2(payload):
        await asyncio.sleep(0.02)
        results.append(f"sub2:{payload['data']}")

    bus.subscribe("test/topic", sub1)
    bus.subscribe("test/topic", sub2)

    bus.publish("test/topic", {"data": "hello"})

    polling_task = asyncio.create_task(bus.start_polling(interval=0.01))

    # Wait for both to finish
    for _ in range(50):
        if len(results) >= 2:
            break
        await asyncio.sleep(0.1)

    bus.stop_polling()
    await polling_task

    assert len(results) == 2
    # Currently EventBus executes callbacks sequentially
    assert len(results) == 2
    assert results[0].startswith("sub1")
    assert results[1].startswith("sub2")
