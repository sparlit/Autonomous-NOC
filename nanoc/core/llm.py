import httpx
import json
import asyncio
from nanoc.core.config import settings
from nanoc.core.monitoring import TelemetryHub
from nanoc.memory.memory import Memory

class LLMProvider:
    def __init__(self, provider: str = None, model: str = None):
        self.provider = provider or settings.DEFAULT_PROVIDER
        self.model = model or settings.DEFAULT_MODEL

    async def complete(self, prompt: str, system_prompt: str = "You are a helpful NOC agent.") -> str:
        # Check for model override in knowledge base
        memory = Memory(settings.DB_PATH)
        override = memory.get_knowledge("system/model_override")
        current_model = override if override else self.model

        start_time = asyncio.get_event_loop().time()
        try:
            if self.provider == "openrouter":
                response = await self._openrouter_complete(prompt, system_prompt, current_model)
            elif self.provider == "ollama":
                response = await self._ollama_complete(prompt, system_prompt, current_model)
            else:
                raise ValueError(f"Unknown provider: {self.provider}")

            end_time = asyncio.get_event_loop().time()
            self._record_telemetry(prompt, response, (end_time - start_time) * 1000)
            return response
        except Exception as e:
            self._record_error(str(e))
            raise

    def _record_telemetry(self, prompt, response, duration_ms):
        hub = TelemetryHub(Memory(settings.DB_PATH))

        # Improved token counting estimation (roughly 4 chars per token)
        prompt_tokens = len(prompt) // 4
        completion_tokens = len(response) // 4

        # Estimate cost based on common model pricing ($0.01 per 1k tokens)
        cost = ((prompt_tokens + completion_tokens) / 1000) * 0.01

        hub.record_token_usage(self.model, prompt_tokens, completion_tokens, cost)
        hub.record_latency("llm_complete", duration_ms)

    def _record_error(self, error_msg):
        hub = TelemetryHub(Memory(settings.DB_PATH))
        hub.record_error("LLMProvider", error_msg)

    async def _openrouter_complete(self, prompt: str, system_prompt: str, model: str) -> str:
        headers = {
            "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ]
        }
        async with httpx.AsyncClient() as client:
            response = await client.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=data, timeout=60.0)
            response.raise_for_status()
            resp_json = response.json()

            # Record actual usage if available
            usage = resp_json.get("usage", {})
            if usage:
                self._record_usage(model, usage)

            return resp_json['choices'][0]['message']['content']

    async def _ollama_complete(self, prompt: str, system_prompt: str, model: str) -> str:
        data = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            "stream": False
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{settings.OLLAMA_BASE_URL}/api/chat", json=data, timeout=60.0)
            response.raise_for_status()
            resp_json = response.json()

            # Record actual usage if available
            prompt_tokens = resp_json.get("prompt_eval_count", 0)
            completion_tokens = resp_json.get("eval_count", 0)
            if prompt_tokens or completion_tokens:
                 self._record_usage(model, {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens})

            return resp_json['message']['content']

    def _record_usage(self, model, usage):
        hub = TelemetryHub(Memory(settings.DB_PATH))
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        cost = ((prompt_tokens + completion_tokens) / 1000) * 0.01 # Simple cost estimation
        hub.record_token_usage(model, prompt_tokens, completion_tokens, cost)
