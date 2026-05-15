import asyncio
from typing import Dict, List, Any, Optional

class MockLLM:
    def __init__(self):
        """
        Initialize the MockLLM with default configuration and empty records.
        
        Attributes:
            calls (list): Recorded calls; each entry is a dict containing at least
                'prompt' and 'system_prompt'.
            responses (dict[str, str]): Mapping of substring patterns to response strings.
            default_response (str): Fallback response used when no pattern matches; default "Mocked response".
            latency (float): Artificial delay in seconds applied to completions; default 0.
            fail_rate (float): Probability between 0 and 1 that a simulated call raises an exception; default 0.
            _call_count (int): Internal counter incremented each time `complete` is invoked.
        """
        self.calls = []
        self.responses = {}
        self.default_response = "Mocked response"
        self.latency = 0
        self.fail_rate = 0
        self._call_count = 0

    def add_response(self, pattern: str, response: str):
        """
        Register a mock response for prompts containing a given substring.
        
        Parameters:
            pattern (str): Substring to match against incoming prompts.
            response (str): Response text to return when `pattern` is found in a prompt.
        """
        self.responses[pattern] = response

    async def complete(self, prompt: str, system_prompt: str = "") -> str:
        """
        Generate a completion for the given prompt using the mock LLM, returning a configured response or a default message that includes the call count.
        
        This method records the call inputs, can simulate an artificial latency and a probabilistic failure, and selects a response by matching configured substring patterns against the prompt.
        
        Parameters:
            prompt (str): The user prompt to complete.
            system_prompt (str): Optional system-level prompt/context.
        
        Returns:
            str: The configured response for the first matching pattern in insertion order, or the default response text suffixed with the current call count.
        
        Raises:
            Exception: If the mock's failure probability triggers a simulated failure.
        """
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
        """
        Initialize an in-memory telemetry collector.
        
        Creates two empty lists used to store recorded metric events (`self.metrics`) and error events (`self.errors`).
        """
        self.metrics = []
        self.errors = []

    def record_token_usage(self, model, prompt_tokens, completion_tokens, cost):
        """
        Record a token-usage metric for the given model and cost.
        
        Parameters:
            model: Identifier of the model for which token usage is recorded.
            prompt_tokens: (accepted) number of prompt tokens; not stored in the recorded metric.
            completion_tokens: (accepted) number of completion tokens; not stored in the recorded metric.
            cost: Monetary cost associated with the token usage; stored on the metric entry.
        """
        self.metrics.append({"type": "token_usage", "model": model, "cost": cost})

    def record_latency(self, name, duration_ms):
        """
        Record a latency metric event by appending an entry to the internal metrics list.
        
        Parameters:
            name (str): Identifier for the latency metric (e.g., operation or component name).
            duration_ms (float): Measured duration in milliseconds.
        """
        self.metrics.append({"type": "latency", "name": name, "duration_ms": duration_ms})

    def record_error(self, component, error_msg):
        """
        Record an error event in the telemetry hub.
        
        Parameters:
            component (str): Identifier of the component that produced the error.
            error_msg (str): Error message or description to record.
        """
        self.errors.append({"component": component, "error": error_msg})
