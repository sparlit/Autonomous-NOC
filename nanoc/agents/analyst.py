import asyncio
from typing import Dict, Any
from nanoc.agents.base import BaseAgent
from nanoc.memory.memory import Memory

class Analyst(BaseAgent):
    def __init__(self, agent_id: str, memory: Memory):
        """
        Create an Analyst agent configured with the given identifier and memory.
        
        Parameters:
            agent_id (str): Unique identifier for the agent.
            memory (Memory): Memory store used by the agent for tasks and events.
        """
        super().__init__(agent_id, "Analyst", memory)

    async def analyze_failure(self, failure_event: Dict[str, Any]):
        """
        Analyze a failure event, record a proposed fix task in memory, and publish the analysis result.
        
        Parameters:
            failure_event (Dict[str, Any]): Event payload containing failure details. Expected keys:
                - "project_id" (str, optional): Identifier of the project; defaults to "unknown" when absent.
                - "error" (str, optional): Error message or description; defaults to "Unknown error" when absent.
        
        Side effects:
            - Logs an analysis message including the project id and error.
            - Creates a fix task in memory assigned to "Coder".
            - Publishes an "analysis/completed" event with a payload containing:
                - "strategy": the proposed fix analysis
                - "original_error": the original error message
        """
        from nanoc.memory.memory import Memory
        project_id = failure_event.get("project_id", "unknown")
        await self.log(f"Analyzing failure for project {project_id}: {failure_event.get('error')}")
        error_msg = failure_event.get('error', 'Unknown error')

        prompt = f"Analyze this error for project {project_id} and propose a fix strategy:\n{error_msg}"
        analysis = await self.think(prompt)

        # Create a fix task
        self.memory.create_task(f"FIX: {analysis}", assigned_to="Coder")
        self.memory.publish_event("analysis/completed", {
            "strategy": analysis,
            "original_error": error_msg
        })
