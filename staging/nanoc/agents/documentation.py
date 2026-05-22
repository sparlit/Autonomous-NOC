import asyncio
import os
from nanoc.agents.base import BaseAgent
from nanoc.memory.memory import Memory
from nanoc.core.config import settings

class DocumentationAgent(BaseAgent):
    def __init__(self, agent_id: str, role: str, memory: Memory, provider=None):
        super().__init__(agent_id, role, memory, provider)
        self.docs_dir = os.path.join(settings.LOGS_DIR, "docs")
        os.makedirs(self.docs_dir, exist_ok=True)

    async def update_docs(self, project_id: str, content: str):
        await self.log(f"Updating documentation for project {project_id}")

        # Persist to Knowledge Base
        self.memory.upsert_knowledge(f"docs:{project_id}", content)

        # Persist to Markdown file
        doc_path = os.path.join(self.docs_dir, f"{project_id}.md")
        mode = "a" if os.path.exists(doc_path) else "w"
        with open(doc_path, mode) as f:
            f.write(f"\n## Update at {asyncio.get_event_loop().time()}\n")
            f.write(content + "\n")

        self.memory.publish_event("docs/updated", {
            "project_id": project_id,
            "status": "success",
            "path": doc_path
        })
