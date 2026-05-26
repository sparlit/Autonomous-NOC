import asyncio
import json
from datetime import datetime
from typing import List, Dict, Any, Optional
from nanoc.core.llm import LLMProvider
from nanoc.memory.memory import Memory
from nanoc.tools.network import DiagnosticTools, DiscoveryTool

class BaseAgent:
    def __init__(self, agent_id: str, role: str, memory: Memory, provider: Optional[LLMProvider] = None):
        """
        Initialize the agent's identity, shared memory reference, LLM client, and tool registry.
        
        Parameters:
        	agent_id (str): Unique identifier for the agent.
        	role (str): Human-readable role or responsibility of the agent.
        	memory (Memory): Shared Memory instance used for logs, tasks, and knowledge storage.
        	provider (Optional[LLMProvider]): LLM provider client to use; if None, a default LLMProvider is created.
        
        Notes:
        	Creates an empty tools registry and registers the module's default tools.
        """
        self.agent_id = agent_id
        self.role = role
        self.memory = memory
        self.llm = provider or LLMProvider()
        self.tools = {}
        self._register_default_tools()

    def _register_default_tools(self):
        """
        Register the agent's default network-related tools.
        
        This adds three tool entries to the agent's tool registry:
        - "ping" mapped to DiagnosticTools.ping
        - "traceroute" mapped to DiagnosticTools.traceroute
        - "discover_topology" mapped to DiscoveryTool.discover_topology
        """
        self.register_tool("ping", DiagnosticTools.ping)
        self.register_tool("traceroute", DiagnosticTools.traceroute)
        self.register_tool("discover_topology", DiscoveryTool.discover_topology)

    async def log(self, content: str):
        """
        Record and publish an agent log message.
        
        Prints the message prefixed with the agent role, appends the entry to shared memory, and publishes an "agent/log" event containing `agent_id`, `role`, and `content`.
        
        Parameters:
            content (str): The log message to record and publish.
        """
        print(f"[{self.role}] {content}")
        self.memory.add_log(self.agent_id, content)
        self.memory.publish_event("agent/log", {
            "agent_id": self.agent_id,
            "role": self.role,
            "content": content
        })

    async def think(self, prompt: str, use_tools: bool = False) -> str:
        """
        Generate a model response to the given prompt, optionally allowing the model to call registered tools.
        
        When use_tools is True and tools are registered, the agent appends tool descriptions to the system prompt and accepts model outputs that request tool execution using the exact format:
        ACTION: tool_name ARGS: your_args
        If the model issues such an action and the named tool is registered, the agent will call the tool with the provided args, then re-enter thinking with the tool result incorporated.
        
        Parameters:
            prompt (str): The user-facing prompt sent to the language model.
            use_tools (bool): If True, include registered tool descriptions in the system prompt and enable tool-invocation parsing.
        
        Returns:
            str: The raw text response produced by the language model, or a follow-up response after a successfully executed tool call.
        
        Side effects:
            Publishes "agent/thought/start" and "agent/thought/complete" events to shared memory, logs a truncated thought, and may synchronously invoke a registered tool if the model requests it.
        """
        self.memory.publish_event("agent/thought/start", {
            "agent_id": self.agent_id,
            "role": self.role,
            "prompt": prompt[:500]
        })
        system_prompt = f"You are {self.agent_id}, a {self.role} in the NANOC team. Work autonomously and flawlessly."
        if use_tools and self.tools:
            tool_desc = "\n".join([f"- {name}: {func.__doc__}" for name, func in self.tools.items()])
            system_prompt += f"\nYou have access to these tools:\n{tool_desc}\nTo use a tool, output: ACTION: tool_name ARGS: your_args"

        response = await self.llm.complete(prompt, system_prompt)
        self.memory.publish_event("agent/thought/complete", {
            "agent_id": self.agent_id,
            "role": self.role,
            "response": response[:1000]
        })
        await self.log(f"Thought: {response[:100]}...")

        if use_tools and "ACTION:" in response:
            # Simple ReAct parsing
            try:
                parts = response.split("ACTION:")[1].split("ARGS:")
                tool_name = parts[0].strip()
                args = parts[1].strip()
                if tool_name in self.tools:
                    await self.log(f"Executing Tool: {tool_name}")
                    tool_func = self.tools[tool_name]
                    if asyncio.iscoroutinefunction(tool_func):
                        result = await tool_func(args)
                    else:
                        result = tool_func(args)
                    return await self.think(f"Tool {tool_name} returned: {result}\nContinue based on this.")
            except Exception as e:
                await self.log(f"Tool execution failed: {e}")

        return response

    def register_tool(self, name: str, func: callable):
        self.tools[name] = func

class ProjectManager(BaseAgent):
    def __init__(self, agent_id: str, memory: Memory, team_leaders: List[str] = None):
        super().__init__(agent_id, "ProjectManager", memory)
        self.team_leaders = team_leaders or []

    async def manage_project(self, project_description: str):
        project_id = f"proj_{int(datetime.now().timestamp())}"
        await self.log(f"Project Manager {self.agent_id} starting project {project_id}: {project_description}")

        active_projects = self.memory.get_knowledge("active_projects") or []
        active_projects.append(project_id)
        self.memory.upsert_knowledge("active_projects", active_projects)

        self.memory.publish_event("project/pm-assigned", {
            "project_id": project_id,
            "description": project_description,
            "manager": self.agent_id
        })

        # PM breaks down the project into work packages for Team Leaders
        prompt = f"As a Project Manager, break this project into work packages for team leaders:\n{project_description}"
        work_packages = await self.think(prompt)

        packages = [p.strip() for p in work_packages.split("\n") if p.strip()]
        for i, package in enumerate(packages):
            # Round-robin assignment to team leaders
            tl_role = self.team_leaders[i % len(self.team_leaders)]
            self.memory.create_task(
                f"Team Lead Task: {package}",
                assigned_to=tl_role,
                project_id=project_id
            )
        return project_id

class TeamLeader(BaseAgent):
    def __init__(self, agent_id: str, role: str, memory: Memory, provider: Optional[LLMProvider] = None, members: List[str] = None):
        super().__init__(agent_id, role, memory, provider)
        self.members = members or []

    async def delegate_tasks(self, project_description: str):
        # Extract project_id from description if it exists (e.g. "proj_123: ...")
        project_id = "unknown"
        if ":" in project_description:
            parts = project_description.split(":", 1)
            if parts[0].startswith("proj_"):
                project_id = parts[0].strip()
                project_description = parts[1].strip()

        if project_id == "unknown":
            project_id = f"proj_{int(datetime.now().timestamp())}"

        await self.log(f"Team Leader {self.agent_id} managing project {project_id}")

        # Track active projects
        active_projects = self.memory.get_knowledge("active_projects") or []
        if project_id not in active_projects:
            active_projects.append(project_id)
            self.memory.upsert_knowledge("active_projects", active_projects)

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

        # Delegate to members
        if self.members:
            # Assign first task to an Architect member if available, else generic
            architect_role = next((m for m in self.members if "Architect" in m), "Architect")
            task_id = self.memory.create_task(
                f"{project_id}: Design architecture for: {project_description}",
                assigned_to=architect_role,
                project_id=project_id
            )
        else:
            task_id = self.memory.create_task(
                f"{project_id}: Design architecture for: {project_description}",
                assigned_to="Architect",
                project_id=project_id
            )

        self.memory.upsert_knowledge(f"project_{project_id}_arch", architecture)
        return project_id

class Architect(BaseAgent):
    async def design_solution(self, requirements: str):
        await self.log("Designing system architecture with internal debate...")

        # Extract project_id from requirements or context (simplified here)
        project_id = requirements.split(":")[0] if ":" in requirements else "unknown"
        if not project_id.startswith("proj_"):
            # Try to find an active project in knowledge if not in requirements
            active_projects = self.memory.get_knowledge("active_projects") or []
            if active_projects:
                project_id = active_projects[-1]

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

        prompt = f"Write the Python code to solve this task:\n{task}\nProvide the response in the format:\nFILEPATH: <relative_path>\nCODE: <code>"
        response = await self.think(prompt)

        # Extract FILEPATH and CODE
        filepath = "nanoc/workspace/generated_code.py"
        code = response

        import re
        fp_match = re.search(r"FILEPATH:\s*(.*)", response)
        code_match = re.search(r"CODE:\s*(.*)", response, re.DOTALL)

        if fp_match:
            filepath = fp_match.group(1).strip()
        if code_match:
            code = code_match.group(1).strip()

        # Stage the change
        from nanoc.core.evolution import SelfEvolutionManager
        from nanoc.core.config import settings
        sem = SelfEvolutionManager(settings.WORKSPACE_DIR, settings.STAGING_DIR)
        sem.prepare_staging()
        try:
            sem.apply_change_to_staging(filepath, code)
            await self.log(f"Staged change to {filepath}")
        except Exception as e:
            await self.log(f"Staging failed: {e}")

        # Publish result to the event bus
        self.memory.publish_event("worker/response", {
            "project_id": project_id,
            "role": "Coder",
            "task": task,
            "result": code,
            "status": "pending_review",
            "filepath": filepath
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

        prompt = (
            f"Review this code/work and identify flaws for project {project_id}:\n{work}\n"
            "If it meets all requirements and follows best practices, you MUST start your response with 'STATUS: APPROVED'.\n"
            "Otherwise, start with 'STATUS: FAILED' and list specific improvements needed."
        )
        review = await self.think(prompt)

        status = "pass" if "STATUS: APPROVED" in review else "fail"

        self.memory.publish_event("gate/result-added", {
            "gate_id": gate_id,
            "project_id": project_id,
            "type": "code_review",
            "status": status,
            "content": review,
            "error": review if status == "fail" else None
        })

        if status == "pass":
            await self.log(f"Work approved for project {project_id}.")
        else:
            await self.log(f"Work failed review for project {project_id}.")
        return review
