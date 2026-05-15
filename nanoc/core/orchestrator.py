import asyncio
import json
from datetime import datetime
from typing import List
from nanoc.agents.base import BaseAgent

class Debater:
    def __init__(self, agents: List[BaseAgent]):
        self.agents = agents

    async def debate(self, topic: str) -> str:
        pro_agent = self.agents[0]
        con_agent = self.agents[1] if len(self.agents) > 1 else self.agents[0]

        pro_view = await pro_agent.think(f"Debate PRO for: {topic}")
        con_view = await con_agent.think(f"Debate CON for: {topic}")

        synthesizer = self.agents[0] # Use leader or first agent to synthesize
        decision = await synthesizer.think(f"Synthesize this debate and make a final decision.\nPRO: {pro_view}\nCON: {con_view}")

        return decision

class Orchestrator:
    def __init__(self, memory, leader):
        self.memory = memory
        self.leader = leader
        self.agents = {}

    def add_agent(self, agent: BaseAgent):
        self.agents[agent.role] = agent

    async def run_loop(self):
        while True:
            # Check for pending tasks in memory
            import sqlite3
            from nanoc.core.config import settings
            with sqlite3.connect(settings.DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM tasks WHERE status = 'pending' ORDER BY created_at ASC LIMIT 1")
                task = cursor.fetchone()

            if task:
                role = task['assigned_to']
                if role in self.agents:
                    agent = self.agents[role]
                    await agent.log(f"Processing task {task['id']}: {task['description']}")

                    # Call specific methods based on role
                    if role == "Architect":
                        result = await agent.design_solution(task['description'])
                    elif role == "Planner":
                        result = await agent.create_todo_list(task['description'])
                    elif role == "Coder":
                        result = await agent.write_code(task['description'])
                    elif role == "Reviewer":
                        result = await agent.review_work(task['description'])
                        if "APPROVED" not in result:
                            # Re-assign back to Coder
                            self.memory.create_task(f"Fix flaws in previous work based on review: {result}\nOriginal Task: {task['description']}", assigned_to="Coder")
                    else:
                        result = await agent.think(f"Execute this task: {task['description']}")

                    with sqlite3.connect(settings.DB_PATH) as conn:
                        cursor = conn.cursor()
                        cursor.execute("UPDATE tasks SET status = 'completed', result = ?, updated_at = ? WHERE id = ?",
                                       (result, datetime.now(), task['id']))
                        conn.commit()

            await asyncio.sleep(5)
