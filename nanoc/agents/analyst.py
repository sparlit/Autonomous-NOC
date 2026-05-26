import asyncio
from typing import Dict, Any
from nanoc.agents.base import BaseAgent
from nanoc.memory.memory import Memory

class Analyst(BaseAgent):
    def __init__(self, agent_id: str, memory: Memory):
        super().__init__(agent_id, "Analyst", memory)

    async def analyze_failure(self, failure_event: Dict[str, Any]):
        project_id = failure_event.get("project_id", "unknown")
        await self.log(f"Analyzing failure for project {project_id}: {failure_event.get('error')}")
        error_msg = failure_event.get('error', 'Unknown error')

        prompt = f"Analyze this error for project {project_id} and propose a fix strategy:\n{error_msg}"
        analysis = await self.think(prompt)

        # Create a fix task
        self.memory.create_task(
            f"FIX: {analysis}",
            assigned_to="Coder",
            project_id=project_id,
            priority=10
        )
        self.memory.publish_event("analysis/completed", {
            "strategy": analysis,
            "original_error": error_msg,
            "project_id": project_id
        })
