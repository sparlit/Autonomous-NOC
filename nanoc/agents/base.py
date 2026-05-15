import asyncio
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
        await self.log(f"Starting project: {project_description}")
        prompt = f"Break down this project into high-level architectural requirements:\n{project_description}"
        architecture = await self.think(prompt)
        task_id = self.memory.create_task(f"Design architecture for: {project_description}", assigned_to="Architect")
        self.memory.upsert_knowledge(f"project_{task_id}_arch", architecture)
        return task_id

class Architect(BaseAgent):
    async def design_solution(self, requirements: str):
        await self.log("Designing system architecture with internal debate...")
        prompt = f"Design a technical architecture based on these requirements:\n{requirements}\nProvide a high-level design."

        # Internal debate logic
        from nanoc.core.orchestrator import Debater
        debater = Debater([self]) # Simplification for demo: debater with itself/different prompts
        design = await debater.debate(prompt)

        task_id = self.memory.create_task(f"Create task list for design: {design[:50]}...", assigned_to="Planner")
        return design

class Planner(BaseAgent):
    async def create_todo_list(self, architecture: str):
        await self.log("Generating granular task list...")
        prompt = f"Create a granular TODO list for this architecture:\n{architecture}\nList specific coding tasks, one per line starting with 'TASK:'."
        todo_list = await self.think(prompt)
        for line in todo_list.split("\n"):
            if line.startswith("TASK:"):
                task_desc = line.replace("TASK:", "").strip()
                self.memory.create_task(task_desc, assigned_to="Coder")
        return todo_list

class Coder(BaseAgent):
    async def write_code(self, task: str):
        await self.log(f"Coding task: {task}")
        prompt = f"Write the Python code to solve this task:\n{task}\nProvide ONLY the code."
        code = await self.think(prompt)
        # Verify and review before committing
        self.memory.create_task(f"Review this code for flaws:\n{code}", assigned_to="Reviewer")
        return code

class Reviewer(BaseAgent):
    async def review_work(self, work: str):
        await self.log("Reviewing work for flaws...")
        prompt = f"Review this code/work and identify flaws:\n{work}\nIf it is perfect, say 'APPROVED'. Otherwise list improvements."
        review = await self.think(prompt)
        if "APPROVED" in review:
            await self.log("Work approved.")
        return review
