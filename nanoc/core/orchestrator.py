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
        # Start event bus listener
        from nanoc.core.event_bus import EventBus
        from nanoc.core.gate_manager import GateManager
        from nanoc.agents.analyst import Analyst

        bus = EventBus(self.memory)
        gm = GateManager(self.memory)
        analyst = Analyst("SystemAnalyst", self.memory)

        async def handle_gate_result(payload):
            # payload: { "type": "...", "status": "pass/fail", "gate_id": "..." }
            project_id = payload.get("project_id")
            if payload.get("status") == "fail":
                await analyst.analyze_failure(payload)
            else:
                gate_id = payload.get("gate_id")
                if gate_id:
                    gm.add_result(gate_id, payload)

        async def handle_gate_resolved(payload):
            project_id = payload.get("project_id")
            gate_type = payload.get("type")

            from nanoc.agents.documentation import DocumentationAgent
            doc_agent = DocumentationAgent("SystemDoc", self.memory)
            await doc_agent.update_docs(project_id, f"Gate {gate_type} resolved at {datetime.now()}")

            if gate_type == "design":
                # Create planning task
                arch = self.memory.get_knowledge(f"project_{project_id}_arch")
                self.memory.create_task(f"{project_id}: Create task list for design: {arch[:50]}", assigned_to="Planner")

            # Additional flow logic here...

        bus.subscribe("gate/result-added", handle_gate_result)
        bus.subscribe("gate/resolved", handle_gate_resolved)
        asyncio.create_task(bus.start_polling())

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
