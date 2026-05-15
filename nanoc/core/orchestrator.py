import asyncio
import json
from datetime import datetime
from typing import List
from nanoc.agents.base import BaseAgent
from nanoc.memory.memory import Memory

class Debater:
    def __init__(self, agents: List[BaseAgent]):
        """
        Store the provided list of agents on the instance.
        
        Parameters:
            agents (List[BaseAgent]): Agent instances to be managed by this Debater/Orchestrator.
        """
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
        """
        Continuously process external gate events and pending tasks, dispatching work to registered agents and updating memory.
        
        Subscribes to the event bus to handle gate results and gate resolutions (analyzing failures, forwarding results to the GateManager, updating project documentation, creating a planning task for design gates, and logging code gate completions). Starts the event bus polling in the background, then enters a persistent loop that queries the oldest pending task from the memory-backed SQLite database (self.memory.db_path). When a pending task is found and a corresponding agent is registered, dispatches the task to role-specific agent methods (Architect → design_solution, Planner → create_todo_list, Coder → write_code, Reviewer → review_work, otherwise think), ensuring the project_id is prefixed into the task description when available. If a review is not approved, creates a follow-up task assigned to the Coder. Upon task completion, writes the result and completion timestamp back to the tasks table and continues polling at regular intervals.
        """
        from nanoc.core.event_bus import EventBus
        from nanoc.core.gate_manager import GateManager
        from nanoc.agents.analyst import Analyst

        bus = EventBus(self.memory)
        gm = GateManager(self.memory)
        analyst = Analyst("SystemAnalyst", self.memory)

        async def handle_gate_result(payload):
            # payload: { "type": "...", "status": "pass/fail", "gate_id": "..." }
            """
            Handle a gate result payload by triggering failure analysis or recording the result with the gate manager.
            
            If the payload's "status" equals "fail", triggers failure analysis using the provided payload. Otherwise, if the payload contains a "gate_id", records the result with the gate manager.
            
            Parameters:
                payload (dict): Event payload containing at least:
                    - "type" (str, optional): The gate type.
                    - "status" (str): Result status, expected "pass" or "fail".
                    - "gate_id" (str, optional): Identifier of the gate.
                    - "project_id" (str, optional): Identifier of the project.
            """
            project_id = payload.get("project_id")
            print(f"[Orchestrator] Handling gate result: {payload.get('status')} for project {project_id}")
            if payload.get("status") == "fail":
                await analyst.analyze_failure(payload)
            else:
                gate_id = payload.get("gate_id")
                if gate_id:
                    gm.add_result(gate_id, payload)

        async def handle_gate_resolved(payload):
            """
            Handle a resolved gate event by updating project documentation and creating any follow-up tasks or logs.
            
            Parameters:
                payload (dict): Event payload expected to contain "project_id" (identifier of the project) and "type" (gate type, e.g., "design" or "code"). 
            
            Description:
                - Always updates the project's documentation to record the gate resolution with a timestamp.
                - If `type` is "design", creates a planning task assigned to the Planner that includes a short slice of the project's architecture.
                - If `type` is "code", records a successful completion message via the analyst.
            """
            project_id = payload.get("project_id")
            gate_type = payload.get("type")
            print(f"[Orchestrator] Gate {gate_type} resolved for project {project_id}")

            from nanoc.agents.documentation import DocumentationAgent
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

        while True:
            # Check for pending tasks in memory
            import sqlite3
            with sqlite3.connect(self.memory.db_path) as conn:
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
                        # Ensure project_id is passed if available
                        desc = task['description']
                        if task['project_id'] and task['project_id'] not in desc:
                            desc = f"{task['project_id']}: {desc}"
                        result = await agent.design_solution(desc)
                    elif role == "Planner":
                        desc = task['description']
                        if task['project_id'] and task['project_id'] not in desc:
                            desc = f"{task['project_id']}: {desc}"
                        result = await agent.create_todo_list(desc)
                    elif role == "Coder":
                        desc = task['description']
                        if task['project_id'] and task['project_id'] not in desc:
                            desc = f"{task['project_id']}: {desc}"
                        result = await agent.write_code(desc)
                    elif role == "Reviewer":
                        desc = task['description']
                        if task['project_id'] and task['project_id'] not in desc:
                            desc = f"{task['project_id']}: {desc}"
                        result = await agent.review_work(desc)
                        if "APPROVED" not in result:
                            # Re-assign back to Coder
                            self.memory.create_task(f"Fix flaws in previous work based on review: {result}\nOriginal Task: {task['description']}", assigned_to="Coder")
                    else:
                        result = await agent.think(f"Execute this task: {task['description']}")

                    with sqlite3.connect(self.memory.db_path) as conn:
                        cursor = conn.cursor()
                        cursor.execute("UPDATE tasks SET status = 'completed', result = ?, updated_at = ? WHERE id = ?",
                                       (result, datetime.now(), task['id']))
                        conn.commit()

            await asyncio.sleep(5)
