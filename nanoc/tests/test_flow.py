import pytest
from nanoc.agents.base import TeamLeader, Architect, Planner
from nanoc.memory.memory import Memory
import os

@pytest.fixture
def memory():
    db_path = "nanoc/memory/test_flow.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    mem = Memory(db_path)
    yield mem
    if os.path.exists(db_path):
        os.remove(db_path)

class MockLLM:
    async def complete(self, prompt, system_prompt):
        return "Mocked Architecture response"

@pytest.mark.asyncio
async def test_agent_delegation_flow(memory):
    mock_llm = MockLLM()
    leader = TeamLeader("Leader", "Team Leader", memory, provider=mock_llm)

    project_id = await leader.delegate_tasks("Test Project")
    assert project_id is not None

    # Check if task was created in DB
    import sqlite3
    with sqlite3.connect(memory.db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        # Find the task created for this project
        cursor.execute("SELECT * FROM tasks WHERE project_id = ?", (project_id,))
        task = cursor.fetchone()
        assert task is not None
        assert task['assigned_to'] == "Architect"
