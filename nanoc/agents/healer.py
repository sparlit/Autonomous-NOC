import asyncio
from typing import Dict, Any
from nanoc.agents.base import BaseAgent
from nanoc.memory.memory import Memory

class AutoHealer(BaseAgent):
    def __init__(self, agent_id: str, memory: Memory):
        super().__init__(agent_id, "Healer", memory)

    async def handle_failure(self, failure_event: Dict[str, Any]):
        task_id = failure_event.get("task_id")
        project_id = failure_event.get("project_id")
        error = failure_event.get("error")
        description = failure_event.get("description")

        await self.log(f"Auto-healing triggered for task {task_id}: {error}")

        prompt = (
            f"The following task failed with error: {error}\n"
            f"Task Description: {description}\n"
            "Suggest a corrective action or a revised approach to solve this task."
        )
        fix_suggestion = await self.think(prompt)

        # Create a new task with the fix suggestion
        self.memory.create_task(
            f"FIX for task {task_id}: {fix_suggestion}\nOriginal description: {description}",
            assigned_to="Coder",
            project_id=project_id,
            priority=10 # Higher priority for fixes
        )

        await self.log(f"Created fix task for {task_id}")
