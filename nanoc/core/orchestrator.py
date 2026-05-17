import asyncio
import json
import sqlite3
from datetime import datetime
from typing import List
from nanoc.agents.base import BaseAgent
from nanoc.memory.memory import Memory
from nanoc.core.event_bus import EventBus
from nanoc.core.gate_manager import GateManager
from nanoc.agents.analyst import Analyst
from nanoc.agents.documentation import DocumentationAgent

class Debater:
    def __init__(self, agents: List[BaseAgent]):
        self.agents = agents

    async def debate(self, topic: str) -> str:
        # Select distinct agents if possible
        pro_agent = self.agents[0]
        con_agent = self.agents[1] if len(self.agents) > 1 else self.agents[0]
        # Use a third agent for synthesis if available
        synthesizer = self.agents[2] if len(self.agents) > 2 else self.agents[0]

        pro_view = await pro_agent.think(f"Debate PRO for: {topic}")
        con_view = await con_agent.think(f"Debate CON for: {topic}")

        decision = await synthesizer.think(f"Synthesize this debate and make a final decision.\nPRO: {pro_view}\nCON: {con_view}")

        return decision

class Orchestrator:
    def __init__(self, memory, leader):
        self.memory = memory
        self.leader = leader
        self.agents = {}

    def add_agent(self, agent: BaseAgent):
        self.agents[agent.role] = agent

    async def scale_workers(self, target_count: int, worker_tasks: List[asyncio.Task], worker_func):
        """Scale the worker pool to the target count."""
        current_count = len(worker_tasks)
        if target_count > current_count:
            print(f"[Orchestrator] Scaling UP: {current_count} -> {target_count}")
            for _ in range(target_count - current_count):
                worker_tasks.append(asyncio.create_task(worker_func()))
        elif target_count < current_count:
            print(f"[Orchestrator] Scaling DOWN: {current_count} -> {target_count}")
            for _ in range(current_count - target_count):
                task = worker_tasks.pop()
                task.cancel()

    async def run_loop(self):
        # Start event bus listener
        bus = EventBus(self.memory)
        gm = GateManager(self.memory)
        analyst = self.agents.get("Analyst") or Analyst("SystemAnalyst", self.memory)

        async def handle_gate_result(payload):
            # payload: { "type": "...", "status": "pass/fail", "gate_id": "..." }
            project_id = payload.get("project_id")
            print(f"[Orchestrator] Handling gate result: {payload.get('status')} for project {project_id}")
            if payload.get("status") == "fail":
                await analyst.analyze_failure(payload)
            else:
                gate_id = payload.get("gate_id")
                if gate_id:
                    gm.add_result(gate_id, payload)

        async def handle_gate_resolved(payload):
            project_id = payload.get("project_id")
            gate_type = payload.get("type")
            print(f"[Orchestrator] Gate {gate_type} resolved for project {project_id}")

            doc_agent = DocumentationAgent("SystemDoc", "Documentation", self.memory)
            await doc_agent.update_docs(project_id, f"Gate {gate_type} resolved at {datetime.now()}")

            if gate_type == "design":
                # Create planning task
                arch = self.memory.get_knowledge(f"project_{project_id}_arch")
                self.memory.create_task(f"{project_id}: Create task list for design: {arch[:50]}", assigned_to="Planner")
            elif gate_type == "code":
                await analyst.log(f"Project {project_id} completed successfully.")

        bus.subscribe("gate/result-added", handle_gate_result)
        bus.subscribe("gate/resolved", handle_gate_resolved)
        asyncio.create_task(bus.start_polling())

        task_queue = asyncio.Queue()

        async def process_task(task):
            role = task['assigned_to']
            if role in self.agents:
                agent = self.agents[role]
                await agent.log(f"Processing task {task['id']} in worker pool: {task['description']}")

                try:
                    # Use the unified agent interface
                    if role == "Architect" and len(self.agents) > 1:
                        # Use internal debate for architectural decisions
                        debater = Debater(list(self.agents.values()))
                        result = await debater.debate(task['description'])

                        # Publish gate result to unblock workflow
                        project_id = task.get('project_id')
                        gate_id = gm.get_active_gate(project_id)
                        self.memory.publish_event("gate/result-added", {
                            "gate_id": gate_id,
                            "project_id": project_id,
                            "type": "design_review",
                            "status": "pass",
                            "content": result
                        })
                    else:
                        result = await agent.handle_task(task)

                    # Special case for Reviewer to re-assign if failed
                    if role == "Reviewer" and "APPROVED" not in result:
                        self.memory.create_task(
                            f"Fix flaws in previous work based on review: {result}\nOriginal Task: {task['description']}",
                            assigned_to="Coder",
                            project_id=task.get('project_id')
                        )

                    with sqlite3.connect(self.memory.db_path) as conn:
                        cursor = conn.cursor()
                        cursor.execute("UPDATE tasks SET status = 'completed', result = ?, updated_at = ? WHERE id = ?",
                                       (result, datetime.now(), task['id']))
                        conn.commit()
                except Exception as e:
                    await agent.log(f"Error processing task {task['id']}: {e}")

                    # Retry logic
                    retry_count = task.get('retry_count', 0) + 1
                    max_retries = task.get('max_retries', 3)

                    with sqlite3.connect(self.memory.db_path) as conn:
                        cursor = conn.cursor()
                        if retry_count <= max_retries:
                            # Re-queue for retry
                            cursor.execute("UPDATE tasks SET status = 'pending', retry_count = ?, result = ?, updated_at = ? WHERE id = ?",
                                           (retry_count, str(e), datetime.now(), task['id']))
                        else:
                            # Final failure
                            cursor.execute("UPDATE tasks SET status = 'failed', result = ?, updated_at = ? WHERE id = ?",
                                           (str(e), datetime.now(), task['id']))
                        conn.commit()

        async def worker():
            while True:
                task = await task_queue.get()
                try:
                    await process_task(task)
                finally:
                    task_queue.task_done()

        # Start initial workers
        workers = [asyncio.create_task(worker()) for _ in range(5)]

        async def handle_scale_up(payload):
            await self.scale_workers(len(workers) + 2, workers, worker)

        async def handle_scale_down(payload):
            new_count = max(1, len(workers) - 2)
            await self.scale_workers(new_count, workers, worker)

        bus.subscribe("system/scale-up", handle_scale_up)
        bus.subscribe("system/scale-down", handle_scale_down)

        while True:
            # Check for all pending tasks in memory
            with sqlite3.connect(self.memory.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                # Use a transaction to mark tasks as 'processing' to avoid duplicate work
                cursor.execute("BEGIN IMMEDIATE")
                cursor.execute("SELECT * FROM tasks WHERE status = 'pending' ORDER BY priority DESC, created_at ASC")
                tasks = [dict(row) for row in cursor.fetchall()]
                for task in tasks:
                    cursor.execute("UPDATE tasks SET status = 'processing', updated_at = ? WHERE id = ?", (datetime.now(), task['id']))
                conn.commit()

            for task in tasks:
                await task_queue.put(task)

            await asyncio.sleep(5)
