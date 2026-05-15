import asyncio
import json
from datetime import datetime
from typing import List, Dict, Any, Optional
from nanoc.core.llm import LLMProvider
from nanoc.memory.memory import Memory

class BaseAgent:
    def __init__(self, agent_id: str, role: str, memory: Memory, provider: Optional[LLMProvider] = None):
        self.agent_id = agent_id
        self.role = role
        self.memory = memory
        self.llm = provider or LLMProvider()
        self.tools = {}

    async def log(self, content: str):
        print(f"[{self.role}] {content}")
        self.memory.add_log(self.agent_id, content)

    async def think(self, prompt: str, use_tools: bool = False) -> str:
        system_prompt = f"You are {self.agent_id}, a {self.role} in the NANOC team. Work autonomously and flawlessly."
        if use_tools and self.tools:
            tool_desc = "\n".join([f"- {name}: {func.__doc__}" for name, func in self.tools.items()])
            system_prompt += f"\nYou have access to these tools:\n{tool_desc}\nTo use a tool, output: ACTION: tool_name ARGS: your_args"

        response = await self.llm.complete(prompt, system_prompt)
        await self.log(f"Thought: {response[:100]}...")

        if use_tools and "ACTION:" in response:
            # Simple ReAct parsing
            try:
                parts = response.split("ACTION:")[1].split("ARGS:")
                tool_name = parts[0].strip()
                args = parts[1].strip()
                if tool_name in self.tools:
                    await self.log(f"Executing Tool: {tool_name}")
                    result = self.tools[tool_name](args)
                    return await self.think(f"Tool {tool_name} returned: {result}\nContinue based on this.")
            except Exception as e:
                await self.log(f"Tool execution failed: {e}")

        return response

    def register_tool(self, name: str, func: callable):
        self.tools[name] = func

class TeamLeader(BaseAgent):
    async def delegate_tasks(self, project_description: str):
        project_id = f"proj_{int(datetime.now().timestamp())}"
        await self.log(f"Starting project {project_id}: {project_description}")

        self.memory.publish_event("project/incoming-job", {
            "project_id": project_id,
            "description": project_description
        })

        prompt = f"Break down this project into high-level architectural requirements:\n{project_description}"
        architecture = await self.think(prompt)

        # Create initial design gate
        from nanoc.core.gate_manager import GateManager
        gm = GateManager(self.memory)
        gm.create_gate(project_id, "design", "Architect", ["Architecture defined", "Peer reviewed"])

        task_id = self.memory.create_task(f"Design architecture for: {project_description}", assigned_to="Architect", project_id=project_id)
        self.memory.upsert_knowledge(f"project_{project_id}_arch", architecture)
        return project_id

class Architect(BaseAgent):
    async def design_solution(self, requirements: str):
        await self.log("Designing system architecture with internal debate...")

        # Extract project_id from requirements or context (simplified here)
        project_id = requirements.split(":")[0] if ":" in requirements else "unknown"
        from nanoc.core.gate_manager import GateManager
        gm = GateManager(self.memory)
        gate_id = gm.get_active_gate(project_id)

        # In a real system, we'd spawn multiple agents for debate
        pro_prompt = f"Design a technical architecture (PRO view) for: {requirements}"
        pro_view = await self.think(pro_prompt)

        con_prompt = f"Critique this architecture and suggest alternatives (CON view): {pro_view}"
        con_view = await self.think(con_prompt)

        final_prompt = f"Synthesize a final architecture design considering these views:\nPRO: {pro_view}\nCON: {con_view}"
        design = await self.think(final_prompt)

        self.memory.publish_event("gate/result-added", {
            "gate_id": gate_id,
            "project_id": project_id,
            "type": "design_review",
            "status": "pass",
            "content": design
        })

        # Wait for gate resolution before creating next task is handled by orchestrator/event bus now
        return design

class Planner(BaseAgent):
    async def create_todo_list(self, architecture: str):
        await self.log("Generating granular task list...")
        project_id = architecture.split(":")[0] if ":" in architecture else "unknown"

        prompt = f"Create a granular TODO list for this architecture:\n{architecture}\nList specific coding tasks, one per line starting with 'TASK:'."
        todo_list = await self.think(prompt)

        # Create code gate
        from nanoc.core.gate_manager import GateManager
        gm = GateManager(self.memory)
        gm.create_gate(project_id, "code", "Coder", ["Code written", "Tests passed"])

        for line in todo_list.split("\n"):
            if line.startswith("TASK:"):
                task_desc = line.replace("TASK:", "").strip()
                self.memory.create_task(f"{project_id}: {task_desc}", assigned_to="Coder")
        return todo_list

class Coder(BaseAgent):
    async def write_code(self, task: str):
        await self.log(f"Coding task: {task}")
        project_id = task.split(":")[0] if ":" in task else "unknown"

        prompt = f"Write the Python code to solve this task:\n{task}\nProvide ONLY the code."
        code = await self.think(prompt)

        # Publish result to the event bus
        self.memory.publish_event("worker/response", {
            "project_id": project_id,
            "role": "Coder",
            "task": task,
            "result": code,
            "status": "pending_review"
        })

        # Verify and review before committing
        self.memory.create_task(f"{project_id}: Review this code for flaws:\n{code}", assigned_to="Reviewer")
        return code

class Reviewer(BaseAgent):
    async def review_work(self, work: str):
        await self.log("Reviewing work for flaws...")
        project_id = work.split(":")[0] if ":" in work else "unknown"

        from nanoc.core.gate_manager import GateManager
        gm = GateManager(self.memory)
        gate_id = gm.get_active_gate(project_id)

        prompt = f"Review this code/work and identify flaws:\n{work}\nIf it is perfect, say 'APPROVED'. Otherwise list improvements."
        review = await self.think(prompt)

        status = "pass" if "APPROVED" in review else "fail"

        self.memory.publish_event("gate/result-added", {
            "gate_id": gate_id,
            "project_id": project_id,
            "type": "code_review",
            "status": status,
            "content": review,
            "error": review if status == "fail" else None
        })

        if status == "pass":
            await self.log("Work approved.")
        return review
