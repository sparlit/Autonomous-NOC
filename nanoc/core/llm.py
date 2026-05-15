import httpx
import json
from nanoc.core.config import settings

class LLMProvider:
    def __init__(self, provider: str = None, model: str = None):
        self.provider = provider or settings.DEFAULT_PROVIDER
        self.model = model or settings.DEFAULT_MODEL

    async def complete(self, prompt: str, system_prompt: str = "You are a helpful NOC agent.") -> str:
        if self.provider == "openrouter":
            return await self._openrouter_complete(prompt, system_prompt)
        elif self.provider == "ollama":
            return await self._ollama_complete(prompt, system_prompt)
        else:
            raise ValueError(f"Unknown provider: {self.provider}")

    async def _openrouter_complete(self, prompt: str, system_prompt: str) -> str:
        headers = {
            "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ]
        }
        async with httpx.AsyncClient() as client:
            response = await client.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=data, timeout=60.0)
            response.raise_for_status()
            return response.json()['choices'][0]['message']['content']

    async def _ollama_complete(self, prompt: str, system_prompt: str) -> str:
        data = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            "stream": False
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{settings.OLLAMA_BASE_URL}/api/chat", json=data, timeout=60.0)
            response.raise_for_status()
            return response.json()['message']['content']
