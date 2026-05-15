import asyncio
import json
from typing import Callable, Dict, List, Any, Optional
from nanoc.memory.memory import Memory

class EventBus:
    def __init__(self, memory: Memory):
        self.memory = memory
        self.subscribers: Dict[str, List[Callable]] = {}
        self.last_seen_id = 0
        self._running = False

    def subscribe(self, topic: str, callback: Callable):
        if topic not in self.subscribers:
            self.subscribers[topic] = []
        self.subscribers[topic].append(callback)

    def publish(self, topic: str, payload: Dict[str, Any]):
        self.memory.publish_event(topic, payload)

    async def start_polling(self, interval: float = 1.0):
        self._running = True
        while self._running:
            new_events = self.memory.get_events(since_id=self.last_seen_id)
            for event in new_events:
                self.last_seen_id = max(self.last_seen_id, event['id'])
                topic = event['topic']
                payload = json.loads(event['payload'])

                # Direct match
                if topic in self.subscribers:
                    for cb in self.subscribers[topic]:
                        if asyncio.iscoroutinefunction(cb):
                            await cb(payload)
                        else:
                            cb(payload)

                # Pattern match (simple prefix for now, e.g. project/*)
                for sub_topic in self.subscribers:
                    if sub_topic == "*" or (sub_topic.endswith('*') and topic.startswith(sub_topic[:-1])):
                        # Inject topic into payload for subscribers that need it
                        rich_payload = payload.copy()
                        rich_payload["_topic"] = topic
                        rich_payload["_event_id"] = event['id']
                        rich_payload["_timestamp"] = event['timestamp']

                        for cb in self.subscribers[sub_topic]:
                            if asyncio.iscoroutinefunction(cb):
                                await cb(rich_payload)
                            else:
                                cb(rich_payload)

            await asyncio.sleep(interval)

    def stop_polling(self):
        self._running = False
