import asyncio
import json
import sqlite3
from datetime import datetime
from typing import List, Dict, Any
from nanoc.agents.base import BaseAgent
from nanoc.memory.memory import Memory

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
    def __init__(self, memory: Memory, leader: BaseAgent):
        from nanoc.core.config import settings
        self.memory = memory
        self.leader = leader
        self.agents = {}
        self.task_queue = asyncio.Queue()
        self.initial_workers = settings.INITIAL_WORKERS
        self.max_workers = settings.MAX_WORKERS
        self.current_workers = []

    def add_agent(self, agent: BaseAgent):
        self.agents[agent.role] = agent

    async def worker(self, worker_id: int):
        print(f"[Orchestrator] Worker {worker_id} started.")
        while True:
            task = await self.task_queue.get()
            try:
                await self.process_task(task)
            except Exception as e:
                print(f"[Orchestrator] Worker {worker_id} failed task {task.get('id')}: {e}")
            finally:
                self.task_queue.task_done()

    async def process_task(self, task: Dict[str, Any]):
        role = task['assigned_to']
        if role in self.agents:
            agent = self.agents[role]
            await agent.log(f"Processing task {task['id']}: {task['description']}")

            try:
                if role == "Architect":
                    desc = task['description']
                    result = await agent.design_solution(desc)
                elif role == "Planner":
                    desc = task['description']
                    result = await agent.create_todo_list(desc)
                elif role == "Coder":
                    desc = task['description']
                    result = await agent.write_code(desc)
                elif role == "Reviewer":
                    desc = task['description']
                    result = await agent.review_work(desc)
                    if "APPROVED" not in result:
                        self.memory.create_task(
                            f"Fix flaws in previous work based on review: {result}\nOriginal Task: {task['description']}",
                            assigned_to="Coder",
                            project_id=task.get('project_id')
                        )
                else:
                    result = await agent.think(f"Execute this task: {task['description']}")

                with sqlite3.connect(self.memory.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "UPDATE tasks SET status = 'completed', result = ?, updated_at = ? WHERE id = ?",
                        (result, datetime.now(), task['id'])
                    )
                    conn.commit()
            except Exception as e:
                await agent.log(f"Error processing task {task['id']}: {e}")
                retry_count = task.get('retry_count', 0) + 1
                max_retries = task.get('max_retries', 3)

                status = 'failed'
                if retry_count <= max_retries:
                    status = 'pending'

                with sqlite3.connect(self.memory.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "UPDATE tasks SET status = ?, result = ?, retry_count = ?, updated_at = ? WHERE id = ?",
                        (status, str(e), retry_count, datetime.now(), task['id'])
                    )
                    conn.commit()

                if status == 'failed':
                    self.memory.publish_event("task/failed", {
                        "task_id": task['id'],
                        "project_id": task.get('project_id'),
                        "error": str(e),
                        "description": task['description']
                    })

    async def run_loop(self):
        from nanoc.core.event_bus import EventBus
        from nanoc.core.gate_manager import GateManager
        from nanoc.agents.analyst import Analyst

        bus = EventBus(self.memory)
        gm = GateManager(self.memory)
        analyst = Analyst("SystemAnalyst", self.memory)

        async def handle_gate_result(payload):
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
            doc_agent = DocumentationAgent("SystemDoc", "Documentation", self.memory)
            await doc_agent.update_docs(project_id, f"Gate {gate_type} resolved at {datetime.now()}")

            if gate_type == "design":
                arch = self.memory.get_knowledge(f"project_{project_id}_arch")
                self.memory.create_task(f"{project_id}: Create task list for design: {arch[:50]}", assigned_to="Planner", project_id=project_id)
            elif gate_type == "code":
                await analyst.log(f"Project {project_id} completed successfully. Promoting changes...")
                from nanoc.core.evolution import SelfEvolutionManager
                from nanoc.core.config import settings
                sem = SelfEvolutionManager(settings.WORKSPACE_DIR, settings.STAGING_DIR)
                sem.promote_staging_to_production()

        async def handle_scale_up(payload):
            if len(self.current_workers) < self.max_workers:
                new_id = len(self.current_workers)
                task = asyncio.create_task(self.worker(new_id))
                self.current_workers.append(task)
                await self.leader.log(f"Scaled up: Worker {new_id} added. Total: {len(self.current_workers)}")

        async def handle_scale_down(payload):
            if len(self.current_workers) > self.initial_workers:
                task = self.current_workers.pop()
                task.cancel()
                await self.leader.log(f"Scaled down: One worker removed. Total: {len(self.current_workers)}")

        async def handle_task_created(payload):
            task_id = payload.get("task_id")
            with sqlite3.connect(self.memory.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
                task = dict(cursor.fetchone())
                if task['status'] == 'pending':
                    cursor.execute("UPDATE tasks SET status = 'processing', updated_at = ? WHERE id = ?", (datetime.now(), task_id))
                    conn.commit()
                    await self.task_queue.put(task)

        bus.subscribe("gate/result-added", handle_gate_result)
        bus.subscribe("gate/resolved", handle_gate_resolved)
        bus.subscribe("system/scale-up", handle_scale_up)
        bus.subscribe("system/scale-down", handle_scale_down)
        bus.subscribe("task/created", handle_task_created)
        asyncio.create_task(bus.start_polling())

        # Start initial worker pool
        self.current_workers = [asyncio.create_task(self.worker(i)) for i in range(self.initial_workers)]

        while True:
            with sqlite3.connect(self.memory.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("BEGIN IMMEDIATE")
                cursor.execute("SELECT * FROM tasks WHERE status = 'pending' ORDER BY priority DESC, created_at ASC")
                tasks = [dict(row) for row in cursor.fetchall()]
                for task in tasks:
                    cursor.execute("UPDATE tasks SET status = 'processing', updated_at = ? WHERE id = ?", (datetime.now(), task['id']))
                conn.commit()

            for task in tasks:
                await self.task_queue.put(task)

            await asyncio.sleep(5)
