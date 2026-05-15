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
        """
        Start the event polling loop and dispatch incoming events to registered subscribers.
        
        Continuously polls the configured memory backend for new events (starting from the last seen event id), updates internal last_seen_id for each processed event, and delivers event payloads to matching subscribers. Subscribers registered under an exact topic receive the parsed payload as-is. Subscribers registered with a prefix wildcard (e.g., "project/*") or the global "*" receive a copy of the payload augmented with `_topic` (the event's topic), `_event_id` (the event's id), and `_timestamp` (the event's timestamp). Coroutine callbacks are awaited; synchronous callbacks are invoked directly. The loop sleeps for `interval` seconds between polls and runs until `stop_polling` sets the running flag to False.
        
        Parameters:
            interval (float): Seconds to wait between polling iterations (default 1.0).
        """
        self._running = True
        while self._running:
            new_events = self.memory.get_events(since_id=self.last_seen_id)
            for event in new_events:
                self.last_seen_id = max(self.last_seen_id, event['id'])
                topic = event['topic']
                try:
                    payload = json.loads(event['payload'])
                except Exception as e:
                    print(f"[EventBus] Error decoding payload for topic {topic}: {e}")
                    continue

                # Direct match
                if topic in self.subscribers:
                    for cb in self.subscribers[topic]:
                        try:
                            if asyncio.iscoroutinefunction(cb):
                                await cb(payload)
                            else:
                                cb(payload)
                        except Exception as e:
                            print(f"[EventBus] Error in direct callback for {topic}: {e}")

                # Pattern match (simple prefix for now, e.g. project/*)
                for sub_topic in self.subscribers:
                    if sub_topic == "*" or (sub_topic.endswith('*') and topic.startswith(sub_topic[:-1])):
                        # Inject topic into payload for subscribers that need it
                        rich_payload = payload.copy()
                        rich_payload["_topic"] = topic
                        rich_payload["_event_id"] = event['id']
                        rich_payload["_timestamp"] = event['timestamp']

                        for cb in self.subscribers[sub_topic]:
                            try:
                                if asyncio.iscoroutinefunction(cb):
                                    await cb(rich_payload)
                                else:
                                    cb(rich_payload)
                            except Exception as e:
                                print(f"[EventBus] Error in callback for {sub_topic}: {e}")

            await asyncio.sleep(interval)

    def stop_polling(self):
        self._running = False
