import time
from typing import Dict, Any, Optional
from nanoc.memory.memory import Memory

class TelemetryHub:
    def __init__(self, memory: Memory):
        self.memory = memory

    def record_token_usage(self, model: str, prompt_tokens: int, completion_tokens: int, cost: float):
        tags = {"model": model, "type": "llm_usage"}
        self.memory.record_metric("token_usage_prompt", prompt_tokens, unit="tokens", tags=tags)
        self.memory.record_metric("token_usage_completion", completion_tokens, unit="tokens", tags=tags)
        self.memory.record_metric("llm_cost", cost, unit="USD", tags=tags)

    def record_error(self, agent_role: str, error_type: str):
        tags = {"role": agent_role, "error_type": error_type}
        self.memory.record_metric("agent_error", 1.0, unit="count", tags=tags)

    def record_latency(self, operation: str, duration_ms: float):
        tags = {"operation": operation}
        self.memory.record_metric("latency", duration_ms, unit="ms", tags=tags)
