import asyncio
from typing import Dict, Any
from nanoc.agents.base import BaseAgent
from nanoc.memory.memory import Memory

class Analyst(BaseAgent):
    def __init__(self, agent_id: str, memory: Memory):
        super().__init__(agent_id, "Analyst", memory)

    async def handle_task(self, task: Dict[str, Any]) -> str:
        desc = task['description']
        if "ANALYZE FAILURE:" in desc:
            error_msg = desc.replace("ANALYZE FAILURE:", "").strip()
            return await self.analyze_failure_task(task.get('project_id', 'unknown'), error_msg)
        return await super().handle_task(task)

    async def analyze_failure_task(self, project_id: str, error_msg: str) -> str:
        await self.log(f"Analyzing failure for project {project_id}: {error_msg}")

        prompt = f"Analyze this error for project {project_id} and propose a fix strategy:\n{error_msg}"
        analysis = await self.think(prompt)

        # Create a fix task
        self.memory.create_task(
            f"FIX: {analysis}\nOriginal Error: {error_msg}",
            assigned_to="Coder",
            project_id=project_id,
            priority=1 # Higher priority for fixes
        )

        self.memory.publish_event("analysis/completed", {
            "project_id": project_id,
            "strategy": analysis,
            "original_error": error_msg
        })
        return analysis

    async def analyze_failure(self, failure_event: Dict[str, Any]):
        """Legacy event handler, now wraps task creation."""
        project_id = failure_event.get("project_id", "unknown")
        error_msg = failure_event.get('error', 'Unknown error')
        self.memory.create_task(
            f"ANALYZE FAILURE: {error_msg}",
            assigned_to="Analyst",
            project_id=project_id,
            priority=2 # High priority for analysis
        )
