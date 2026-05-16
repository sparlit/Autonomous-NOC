import asyncio
from nanoc.agents.base import BaseAgent
from nanoc.memory.memory import Memory

class DocumentationAgent(BaseAgent):
    def __init__(self, agent_id: str, role: str, memory: Memory, provider=None):
        super().__init__(agent_id, role, memory, provider)

    async def update_docs(self, project_id: str, content: str):
        await self.log(f"Updating documentation for project {project_id}")

        # Persist to Knowledge Base
        self.memory.upsert_knowledge(f"docs:{project_id}", content)

        # Write to local markdown file
        import os
        from nanoc.core.config import settings
        doc_dir = os.path.join(settings.LOGS_DIR, "docs")
        os.makedirs(doc_dir, exist_ok=True)
        filepath = os.path.join(doc_dir, f"{project_id}.md")

        try:
            with open(filepath, "w") as f:
                f.write(f"# Documentation for Project {project_id}\n\n")
                f.write(f"Updated: {os.path.getmtime(filepath) if os.path.exists(filepath) else 'New'}\n\n")
                f.write(content)

            self.memory.publish_event("docs/updated", {
                "project_id": project_id,
                "filepath": filepath,
                "status": "success"
            })
        except Exception as e:
            await self.log(f"Failed to write doc file: {e}")
            self.memory.publish_event("docs/updated", {
                "project_id": project_id,
                "status": "error",
                "error": str(e)
            })
