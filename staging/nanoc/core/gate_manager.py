import json
from enum import Enum
from typing import List, Dict, Any, Optional
from datetime import datetime
from nanoc.memory.memory import Memory

class GateStatus(Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"

class GateManager:
    def __init__(self, memory: Memory):
        self.memory = memory

    def create_gate(self, project_id: str, gate_type: str, assigned_group: str, criteria: List[str]):
        gate_id = f"gate_{project_id}_{gate_type}_{int(datetime.now().timestamp())}"
        gate_data = {
            "id": gate_id,
            "project_id": project_id,
            "type": gate_type,
            "assigned_group": assigned_group,
            "status": GateStatus.PENDING.value,
            "criteria": criteria,
            "results": [],
            "created_at": datetime.now().isoformat()
        }
        self.memory.upsert_knowledge(f"gate:{gate_id}", gate_data)
        # Track active gate for project
        self.memory.upsert_knowledge(f"project:{project_id}:active_gate", gate_id)
        self.memory.publish_event("gate/opened", gate_data)
        return gate_id

    def get_active_gate(self, project_id: str) -> Optional[str]:
        return self.memory.get_knowledge(f"project:{project_id}:active_gate")

    def add_result(self, gate_id: str, result: Dict[str, Any]):
        gate_data = self.memory.get_knowledge(f"gate:{gate_id}")
        if gate_data:
            gate_data["results"].append(result)
            gate_data["status"] = GateStatus.IN_PROGRESS.value
            self.memory.upsert_knowledge(f"gate:{gate_id}", gate_data)
            self.evaluate_gate(gate_id)

    def evaluate_gate(self, gate_id: str):
        gate_data = self.memory.get_knowledge(f"gate:{gate_id}")
        if not gate_data:
            return

        results = gate_data.get("results", [])
        if not results:
            return

        # Improved logic: check for failures and ensure at least one pass
        failures = [r for r in results if r.get("status") == "fail"]
        passes = [r for r in results if r.get("status") == "pass"]

        if failures:
            gate_data["status"] = GateStatus.FAILED.value
            self.memory.upsert_knowledge(f"gate:{gate_id}", gate_data)
            self.memory.publish_event("gate/failed", gate_data)
        elif len(passes) >= 1:
            # All checks passed so far and at least one definitive pass
            gate_data["status"] = GateStatus.COMPLETE.value
            self.memory.upsert_knowledge(f"gate:{gate_id}", gate_data)
            # Maintain both for backward compatibility if needed, but resolved is primary
            self.memory.publish_event("gate/completed", gate_data)
            self.memory.publish_event("gate/resolved", gate_data)
