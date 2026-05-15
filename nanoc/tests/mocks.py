import asyncio
from typing import Dict, List, Any, Optional

class MockLLM:
    def __init__(self):
        self.calls = []
        self.responses = {}
        self.default_response = "Mocked response"
        self.latency = 0
        self.fail_rate = 0
        self._call_count = 0

    def add_response(self, pattern: str, response: str):
        self.responses[pattern] = response

    async def complete(self, prompt: str, system_prompt: str = "") -> str:
        self._call_count += 1
        self.calls.append({"prompt": prompt, "system_prompt": system_prompt})

        if self.latency > 0:
            await asyncio.sleep(self.latency)

        import random
        if random.random() < self.fail_rate:
            raise Exception("Mock LLM simulated failure")

        for pattern, response in self.responses.items():
            if pattern in prompt:
                return response

        return f"{self.default_response} (Call {self._call_count})"

class MockTelemetryHub:
    def __init__(self):
        self.metrics = []
        self.errors = []

    def record_token_usage(self, model, prompt_tokens, completion_tokens, cost):
        self.metrics.append({"type": "token_usage", "model": model, "cost": cost})

    def record_latency(self, name, duration_ms):
        self.metrics.append({"type": "latency", "name": name, "duration_ms": duration_ms})

    def record_error(self, component, error_msg):
        self.errors.append({"component": component, "error": error_msg})
