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
        """
        Evaluate the gate identified by `gate_id` and update its status and events based on results.
        
        If the gate exists, updates persistent gate data and emits lifecycle events:
        - If at least one result has status `"pass"`, sets the gate status to `DONE` then to `COMPLETE`, persisting after each change and emitting `gate/completed` and `gate/resolved` respectively.
        - If no passing results are present, emits `gate/failed` with the current gate data.
        
        Parameters:
            gate_id (str): Identifier of the gate to evaluate.
        
        """
        gate_data = self.memory.get_knowledge(f"gate:{gate_id}")
        if not gate_data:
            return

        # Simple logic: if we have at least one result and it's a 'pass'
        # In a real system, this would be more complex
        passes = [r for r in gate_data["results"] if r.get("status") == "pass"]

        if len(passes) >= 1: # Placeholder condition
            gate_data["status"] = GateStatus.DONE.value
            self.memory.upsert_knowledge(f"gate:{gate_id}", gate_data)
            self.memory.publish_event("gate/completed", gate_data)

            # If all checks pass, mark as COMPLETE
            gate_data["status"] = GateStatus.COMPLETE.value
            self.memory.upsert_knowledge(f"gate:{gate_id}", gate_data)
            self.memory.publish_event("gate/resolved", gate_data)
        else:
            self.memory.publish_event("gate/failed", gate_data)
