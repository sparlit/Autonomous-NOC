import pytest
import os
import asyncio
from nanoc.memory.memory import Memory
from nanoc.agents.base import TeamLeader, Architect, Planner, Coder, Reviewer
from nanoc.core.orchestrator import Orchestrator
from nanoc.core.event_bus import EventBus
from nanoc.tests.mocks import MockLLM

@pytest.fixture
def memory():
    db_path = "nanoc/memory/test_integration.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    mem = Memory(db_path)
    yield mem
    if os.path.exists(db_path):
        os.remove(db_path)

@pytest.mark.asyncio
async def test_full_agent_workflow_with_recovery(memory):
    mock_llm = MockLLM()

    # Configure mock responses
    mock_llm.add_response("Design architecture", "Mocked Architecture: Project X")
    mock_llm.add_response("Create a granular TODO list", "TASK: Implement API\nTASK: Implement DB")
    mock_llm.add_response("Write the Python code", "print('hello world')")
    # First review fails, second succeeds
    mock_llm.add_response("Review this code", "FAIL: Needs more comments")

    leader = TeamLeader("Leader", "Team Leader", memory, provider=mock_llm)
    arch = Architect("Archie", "Architect", memory, provider=mock_llm)
    planner = Planner("Planette", "Planner", memory, provider=mock_llm)
    coder = Coder("Codey", "Coder", memory, provider=mock_llm)
    reviewer = Reviewer("Rev", "Reviewer", memory, provider=mock_llm)

    orchestrator = Orchestrator(memory, leader)
    orchestrator.add_agent(arch)
    orchestrator.add_agent(planner)
    orchestrator.add_agent(coder)
    orchestrator.add_agent(reviewer)

    # Start orchestrator loop in background
    orch_task = asyncio.create_task(orchestrator.run_loop())

    # Trigger project
    project_id = await leader.delegate_tasks("Build a hello world app")

    # Wait for progression
    # We expect: Design -> Plan -> Code -> Review (Fail) -> Code (Fix) -> Review (Pass)

    max_retries = 100
    success = False
    for i in range(max_retries):
        # Check if we have multiple tasks and final status
        import sqlite3
        with sqlite3.connect(memory.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE (project_id = ? OR description LIKE ?) AND status = 'completed'", (project_id, f"%{project_id}%"))
            tasks = cursor.fetchall()

            # Look for a successful review or completed project indicator
            # In this simple flow, we check if the last task is completed
            if len(tasks) >= 5: # Architect, Planner, Coder, Reviewer, Coder(fix)...
                success = True
                break

        # Change mock response to APPROVED after some time to simulate fix
        if i == 30:
            mock_llm.responses["Review this code"] = "APPROVED"

        await asyncio.sleep(0.5)

    orch_task.cancel()
    try:
        await orch_task
    except asyncio.CancelledError:
        pass

    assert success, f"Workflow did not complete. Completed tasks: {len(tasks)}"
