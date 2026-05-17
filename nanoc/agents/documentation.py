import asyncio
from nanoc.agents.base import BaseAgent
from nanoc.memory.memory import Memory

class DocumentationAgent(BaseAgent):
    def __init__(self, agent_id: str, role: str, memory: Memory, provider=None):
        super().__init__(agent_id, role, memory, provider)

    async def update_docs(self, project_id: str, content: str):
        await self.log(f"Updating documentation for project {project_id}")

        from nanoc.core.config import settings
        import os
        from datetime import datetime

        docs_dir = os.path.join(settings.LOGS_DIR, "docs")
        os.makedirs(docs_dir, exist_ok=True)

        doc_path = os.path.join(docs_dir, f"{project_id}.md")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with open(doc_path, "a") as f:
            f.write(f"\n## Update {timestamp}\n")
            f.write(content)
            f.write("\n---\n")

        self.memory.upsert_knowledge(f"docs:{project_id}", content)
        self.memory.publish_event("docs/updated", {
            "project_id": project_id,
            "path": doc_path,
            "status": "success"
        })
