import httpx
import json
import asyncio
from nanoc.core.config import settings

class LLMProvider:
    def __init__(self, provider: str = None, model: str = None):
        self.provider = provider or settings.DEFAULT_PROVIDER
        self.default_model = model or settings.DEFAULT_MODEL
        self.current_model = self.default_model

    async def complete(self, prompt: str, system_prompt: str = "You are a helpful NOC agent.") -> str:
        # Check for model override in knowledge base (e.g. from Governor)
        from nanoc.memory.memory import Memory
        mem = Memory(settings.DB_PATH)
        override = mem.get_knowledge("system/model_override")
        self.current_model = override if override else self.default_model

        start_time = asyncio.get_event_loop().time()
        try:
            if self.provider == "openrouter":
                response = await self._openrouter_complete(prompt, system_prompt)
            elif self.provider == "ollama":
                response = await self._ollama_complete(prompt, system_prompt)
            else:
                raise ValueError(f"Unknown provider: {self.provider}")

            end_time = asyncio.get_event_loop().time()
            self._record_telemetry(prompt, response, (end_time - start_time) * 1000)
            return response
        except Exception as e:
            self._record_error(str(e))
            raise

    def _record_telemetry(self, prompt, response, duration_ms):
        from nanoc.core.monitoring import TelemetryHub
        from nanoc.memory.memory import Memory
        from nanoc.core.config import settings

        hub = TelemetryHub(Memory(settings.DB_PATH))

        # Improved token counting estimation (roughly 4 chars per token)
        prompt_tokens = len(prompt) // 4
        completion_tokens = len(response) // 4

        # Estimate cost based on model (simplified)
        # premium models cost more
        rate = 0.03 if "gpt-4" in self.current_model or "claude-3" in self.current_model else 0.002
        cost = ((prompt_tokens + completion_tokens) / 1000) * rate

        hub.record_token_usage(self.current_model, prompt_tokens, completion_tokens, cost)
        hub.record_latency("llm_complete", duration_ms)

    def _record_error(self, error_msg):
        from nanoc.core.monitoring import TelemetryHub
        from nanoc.memory.memory import Memory
        from nanoc.core.config import settings
        hub = TelemetryHub(Memory(settings.DB_PATH))
        hub.record_error("LLMProvider", error_msg)

    async def _openrouter_complete(self, prompt: str, system_prompt: str) -> str:
        headers = {
            "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "model": self.current_model,
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
            "model": self.current_model,
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
