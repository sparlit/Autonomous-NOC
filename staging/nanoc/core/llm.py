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
            usage = None
            if self.provider == "openrouter":
                res = await self._openrouter_complete(prompt, system_prompt, current_model)
                if isinstance(res, tuple):
                    content, usage = res
                else:
                    content, usage = res, None
            elif self.provider == "ollama":
                res = await self._ollama_complete(prompt, system_prompt, current_model)
                if isinstance(res, tuple):
                    content, usage = res
                else:
                    content, usage = res, None
            else:
                raise ValueError(f"Unknown provider: {self.provider}")

            end_time = asyncio.get_event_loop().time()
            self._record_telemetry(prompt, content, (end_time - start_time) * 1000, usage)
            return content
        except Exception as e:
            self._record_error(str(e))
            raise

    def _record_telemetry(self, prompt, response, duration_ms, usage=None):
        hub = TelemetryHub(Memory(settings.DB_PATH))

        if usage:
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
        else:
            # Fallback to estimation (roughly 3.5 chars per token for better accuracy in English)
            prompt_tokens = int(len(prompt) / 3.5)
            completion_tokens = int(len(response) / 3.5)

        # Dynamic cost estimation based on model name if possible, else default
        # Default pricing: $0.01 per 1k tokens (legacy)
        # For OpenRouter models, we might want to be more specific, but for now we'll stick to a slightly better default
        rate = 0.01
        if "gpt-4" in self.model:
            rate = 0.03
        elif "gpt-3.5" in self.model:
            rate = 0.002

        cost = ((prompt_tokens + completion_tokens) / 1000) * rate

        hub.record_token_usage(self.model, prompt_tokens, completion_tokens, cost)
        hub.record_latency("llm_complete", duration_ms)

    def _record_error(self, error_msg):
        hub = TelemetryHub(Memory(settings.DB_PATH))
        hub.record_error("LLMProvider", error_msg)

    async def _openrouter_complete(self, prompt: str, system_prompt: str, model: str) -> tuple:
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
            res_json = response.json()
            content = res_json['choices'][0]['message']['content']
            usage = res_json.get('usage', {})
            return content, usage

    async def _ollama_complete(self, prompt: str, system_prompt: str, model: str) -> tuple:
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
            res_json = response.json()
            content = res_json['message']['content']
            usage = {
                "prompt_tokens": res_json.get("prompt_eval_count", 0),
                "completion_tokens": res_json.get("eval_count", 0)
            }
            return content, usage
