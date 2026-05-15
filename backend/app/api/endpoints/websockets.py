from fastapi import WebSocket, WebSocketDisconnect
from typing import List, Dict, Any
import asyncio
import json

class ConnectionManager:
    def __init__(self):
        """
        Initialize a ConnectionManager instance.
        
        Creates an empty list used to track active FastAPI WebSocket connections (`active_connections`).
        """
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        """
        Accepts an incoming WebSocket connection and registers it for future broadcasts.
        
        Parameters:
            websocket (WebSocket): The incoming WebSocket connection to accept and store for later use.
        """
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        """
        Remove a WebSocket from the manager's active connections.
        
        Parameters:
            websocket (WebSocket): The WebSocket connection to remove from active_connections.
        
        Raises:
            ValueError: If the provided websocket is not present in active_connections.
        """
        self.active_connections.remove(websocket)

    async def broadcast(self, message: Dict[str, Any]):
        """
        Broadcast a JSON-serializable mapping to all currently active WebSocket connections.
        
        Sends the provided `message` to each active connection by calling `send_json`. If sending to a particular connection fails, the error is suppressed and broadcasting continues to the remaining connections.
        
        Parameters:
            message (Dict[str, Any]): The mapping to be sent as JSON to all active WebSocket clients.
        """
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                # Handle stale connections
                pass

manager = ConnectionManager()
