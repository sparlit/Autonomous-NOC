import asyncio
from nanoc.agents.base import BaseAgent
from nanoc.memory.memory import Memory

class DocumentationAgent(BaseAgent):
    def __init__(self, agent_id: str, role: str, memory: Memory, provider=None):
        """
        Initialize the DocumentationAgent with an identifier, role, memory store, and optional provider.
        
        Parameters:
            agent_id (str): Unique identifier for the agent.
            role (str): Role or purpose assigned to the agent.
            memory (Memory): Memory backend used for storing and retrieving agent data.
            provider (optional): Optional external provider or client used by the agent.
        """
        super().__init__(agent_id, role, memory, provider)

    async def update_docs(self, project_id: str, content: str):
        """
        Store the given documentation content for a project and publish a documentation-updated event.
        
        Parameters:
            project_id (str): Identifier of the project whose documentation is being updated.
            content (str): Documentation content to store; saved under the knowledge key "docs:{project_id}".
        """
        await self.log(f"Updating documentation for project {project_id}")
        # In a real system, this would write to markdown files
        self.memory.upsert_knowledge(f"docs:{project_id}", content)
        self.memory.publish_event("docs/updated", {
            "project_id": project_id,
            "status": "success"
        })
