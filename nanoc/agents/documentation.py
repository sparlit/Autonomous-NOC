import asyncio
import os
from nanoc.agents.base import BaseAgent
from nanoc.memory.memory import Memory

class DocumentationAgent(BaseAgent):
    def __init__(self, agent_id: str, role: str, memory: Memory, provider=None):
        super().__init__(agent_id, role, memory, provider)
        self.docs_dir = "nanoc/docs"
        os.makedirs(self.docs_dir, exist_ok=True)

    async def update_docs(self, project_id: str, content: str):
        await self.log(f"Updating documentation for project {project_id}")

        # Write to filesystem
        filepath = os.path.join(self.docs_dir, f"{project_id}.md")
        with open(filepath, "a") as f:
            f.write(f"\n## Update at {asyncio.get_event_loop().time()}\n")
            f.write(content + "\n")

        # Update knowledge base
        existing = self.memory.get_knowledge(f"docs:{project_id}") or ""
        self.memory.upsert_knowledge(f"docs:{project_id}", existing + "\n" + content)

        self.memory.publish_event("docs/updated", {
            "project_id": project_id,
            "filepath": filepath,
            "status": "success"
        })
