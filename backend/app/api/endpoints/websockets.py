"""WebSocket fan-out for the NOC control plane.

Two responsibilities live here:

1. :class:`ConnectionManager` tracks every socket registered on ``/ws`` and fans
   one message out to all of them without letting a single slow or dead client
   break delivery for the rest.
2. :func:`envelope` turns a bus event into the wire message the frontend
   consumes.

Wire format
-----------
The message is deliberately **flat**. Agent events have always been delivered
as ``payload + _topic/_event_id/_timestamp`` and ``/agents`` reads those keys
straight off the root object, so anything nested would break it. A ``type``
discriminator is added on top and nothing is moved::

    {
      "type": "metric" | "alert" | "data_update" | "agent" | "event",
      "topic": "metric/snapshot",
      "ts": "2026-04-20T12:00:00",
      "event_id": 42,
      ...event payload keys (flat, unchanged)...
      "_topic": "metric/snapshot",      # legacy, unchanged
      "_event_id": 42,                  # legacy, unchanged
      "_timestamp": "2026-04-20T12:00:00"  # legacy, unchanged
    }
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import WebSocket

logger = logging.getLogger(__name__)

#: Topic prefix -> message ``type``. Longest prefix wins, so keep the more
#: specific entries first when two of them could match the same topic.
TOPIC_TYPE_PREFIXES: tuple[tuple[str, str], ...] = (
    ("metric/", "metric"),
    ("metrics/", "metric"),
    ("alert/", "alert"),
    ("alerts/", "alert"),
    ("topology/", "data_update"),
    ("data/", "data_update"),
    ("agent/", "agent"),
    ("automation/", "automation"),
    ("system/", "event"),
)

DEFAULT_MESSAGE_TYPE = "event"


def classify_topic(topic: Optional[str]) -> str:
    """
    Map an event-bus topic onto the wire-level ``type`` discriminator.

    Unknown or missing topics fall back to ``"event"`` so a consumer that
    switches on ``type`` never has to handle ``undefined``.

    Parameters:
        topic (Optional[str]): Event-bus topic, e.g. ``"agent/thought/start"``.

    Returns:
        str: One of ``metric``, ``alert``, ``data_update``, ``agent``, ``event``.
    """
    if not topic:
        return DEFAULT_MESSAGE_TYPE
    for prefix, message_type in TOPIC_TYPE_PREFIXES:
        if topic.startswith(prefix):
            return message_type
    return DEFAULT_MESSAGE_TYPE


def envelope(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build the flat wire message for one event-bus payload.

    The event bus enriches wildcard subscribers with ``_topic``, ``_event_id``
    and ``_timestamp``; this function keeps all of that intact and adds the
    ``type``/``topic``/``ts``/``event_id`` aliases used by the new consumers.

    Parameters:
        event (Dict[str, Any]): Event payload as delivered by the EventBus.

    Returns:
        Dict[str, Any]: JSON-serialisable message ready for ``broadcast``.
    """
    topic = event.get("_topic")
    message: Dict[str, Any] = dict(event)
    message["type"] = classify_topic(topic)
    message["topic"] = topic
    message["ts"] = event.get("_timestamp")
    message["event_id"] = event.get("_event_id")
    return message


class ConnectionManager:
    def __init__(self):
        """Create a manager with no registered connections."""
        self.active_connections: List[WebSocket] = []

    @property
    def connection_count(self) -> int:
        """Number of sockets currently registered for broadcasts."""
        return len(self.active_connections)

    async def connect(self, websocket: WebSocket):
        """
        Accept an incoming WebSocket and register it for future broadcasts.

        Parameters:
            websocket (WebSocket): The incoming WebSocket connection to accept and store.
        """
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        """
        Remove a socket from the registry.

        Idempotent: a socket can be dropped by the browser, by the receive loop
        and by a failed broadcast, so "already gone" is a normal outcome rather
        than an error path.

        Parameters:
            websocket (WebSocket): The connection to remove from ``active_connections``.
        """
        try:
            self.active_connections.remove(websocket)
        except ValueError:
            pass

    async def send_to(self, websocket: WebSocket, message: Dict[str, Any]) -> bool:
        """
        Send one message to a single socket.

        Parameters:
            websocket (WebSocket): Target connection.
            message (Dict[str, Any]): JSON-serialisable message.

        Returns:
            bool: ``True`` when the send succeeded, ``False`` when the socket is
            dead (in which case it is unregistered).
        """
        try:
            await websocket.send_json(message)
            return True
        except Exception as exc:  # noqa: BLE001 - any transport error means "gone"
            logger.debug("Dropping dead websocket: %s", exc)
            self.disconnect(websocket)
            return False

    async def broadcast(self, message: Dict[str, Any]):
        """
        Broadcast a JSON-serialisable mapping to all currently active connections.

        Iterates over a snapshot of the registry so connections added or removed
        by ``send_to`` cannot mutate the sequence mid-flight, and prunes sockets
        whose send failed instead of retrying them forever.

        Parameters:
            message (Dict[str, Any]): The mapping to be sent as JSON to all active clients.
        """
        for connection in list(self.active_connections):
            await self.send_to(connection, message)


manager = ConnectionManager()
