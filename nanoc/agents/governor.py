import asyncio
from typing import Dict, Any, List
from nanoc.agents.base import BaseAgent
from nanoc.memory.memory import Memory

class Governor(BaseAgent):
    def __init__(self, agent_id: str, memory: Memory, config: Dict[str, Any]):
        super().__init__(agent_id, "Governor", memory)
        self.config = config
        self.thresholds = config.get("thresholds", {
            "error_rate": 0.2,
            "cost_limit": 10.0,
            "latency_ms": 30000
        })

    async def decide_action(self, metrics: Dict[str, Any]) -> str:
        """
        Decision logic based on observable KPIs.
        Returns: 'CONTINUE', 'SCALE_UP', 'SCALE_DOWN', 'ABORT_HUMAN_REVIEW'
        """
        error_rate = metrics.get("error_rate", 0)
        total_cost = metrics.get("total_cost", 0)

        if error_rate > self.thresholds["error_rate"]:
            await self.log(f"Critical error rate detected: {error_rate}")
            return "ABORT_HUMAN_REVIEW"

        if total_cost > self.thresholds["cost_limit"]:
            await self.log(f"Cost limit exceeded: {total_cost}")
            return "ABORT_HUMAN_REVIEW"

        # Example scaling logic
        backlog_size = metrics.get("backlog_size", 0)
        if backlog_size > 10:
            return "SCALE_UP"

        if total_cost > self.thresholds["cost_limit"] * 0.8:
            return "THROTTLE_COST"

        return "CONTINUE"

    async def run_governance_cycle(self):
        while True:
            # Gather metrics
            metrics = await self.gather_metrics()
            action = await self.decide_action(metrics)

            self.memory.publish_event("gov/event/decision", {
                "decision": action,
                "metrics": metrics,
                "justification": f"Automated decision based on metrics. Action: {action}"
            })

            if action == "ABORT_HUMAN_REVIEW":
                self.memory.publish_event("alert/action-needed", {
                    "reason": "Threshold exceeded",
                    "metrics": metrics
                })
            elif action == "SCALE_UP":
                await self.log("Autonomous scaling: increasing agent capacity.")
                # Logic to start more agent workers
                self.memory.publish_event("system/scale-up", {"role": "Coder", "reason": "High backlog"})
            elif action == "THROTTLE_COST":
                await self.log("Budget warning: throttling model usage.")
                # Logic to switch to cheaper models

            await asyncio.sleep(60) # Run every minute

    async def gather_metrics(self) -> Dict[str, Any]:
        metrics = {
            "error_rate": 0.0,
            "total_cost": 0.0,
            "backlog_size": 0,
            "latency_ms": 0
        }

        with self.memory.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Estimate error rate from metrics table
            cursor.execute("SELECT COUNT(*) as count FROM metrics WHERE metric_name = 'agent_error'")
            errors = cursor.fetchone()['count']
            cursor.execute("SELECT COUNT(*) as count FROM events WHERE topic LIKE 'worker/response%'")
            total_tasks = cursor.fetchone()['count']
            if total_tasks > 0:
                metrics["error_rate"] = errors / total_tasks

            # Sum cost
            cursor.execute("SELECT SUM(value) as total FROM metrics WHERE metric_name = 'llm_cost'")
            row = cursor.fetchone()
            metrics["total_cost"] = row['total'] if row['total'] else 0.0

            # Backlog size
            cursor.execute("SELECT COUNT(*) as count FROM tasks WHERE status = 'pending'")
            metrics["backlog_size"] = cursor.fetchone()['count']

        return metrics
