import asyncio
from typing import Dict, Any
from nanoc.agents.base import BaseAgent
from nanoc.memory.memory import Memory

class AutoHealer(BaseAgent):
    def __init__(self, agent_id: str, memory: Memory):
        super().__init__(agent_id, "Auto-Healer", memory)

    async def handle_failure(self, task_id: int, error: str):
        """
        Listen for task failures and attempt to heal or fix the root cause.
        """
        await self.log(f"Healer investigating failure in task {task_id}: {error}")

        prompt = (
            f"A task with ID {task_id} failed with this error: {error}\n"
            "Analyze the error and propose a fix or mitigation. "
            "If it's a code issue, provide the corrected code. "
            "If it's a resource issue, suggest optimization."
        )
        diagnosis = await self.think(prompt)

        await self.log(f"Healer diagnosis for task {task_id}: {diagnosis[:100]}...")

        # Create a corrective task with high priority
        self.memory.create_task(
            f"HEAL: {diagnosis}\nOriginal failure: {error}",
            assigned_to="Coder",
            priority=5 # High priority for healing tasks
        )

    async def monitor_failures(self):
        """
        Poll for failed tasks and start healing.
        """
        from nanoc.core.event_bus import EventBus
        bus = EventBus(self.memory)

        async def on_task_log(payload):
            content = payload.get("content", "")
            if "failed permanently" in content:
                # Extract task ID from content if possible, or just log investigation
                await self.log("Permanent failure detected, initiating investigation...")

        bus.subscribe("agent/log", on_task_log)
        await bus.start_polling()
