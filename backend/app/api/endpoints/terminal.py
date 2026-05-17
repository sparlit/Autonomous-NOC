import asyncio
import json
import os
import pty
import signal
import struct
import fcntl
import termios
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Depends
from nanoc.core.config import settings

router = APIRouter()

async def get_token_auth(websocket: WebSocket):
    # Simple token check from query params or headers
    token = websocket.query_params.get("token")
    if token != settings.TERMINAL_ACCESS_TOKEN:
        await websocket.close(code=1008) # Policy Violation
        return None
    return token

class TerminalSession:
    """
    Manages a pseudo-terminal (PTY) session connected to a WebSocket.
    """
    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.fd = None
        self.pid = None

    def set_winsize(self, rows, cols, xpixel=0, ypixel=0):
        """Sets the terminal window size using ioctl."""
        if self.fd is not None:
            winsize = struct.pack("HHHH", rows, cols, xpixel, ypixel)
            fcntl.ioctl(self.fd, termios.TIOCSWINSZ, winsize)

    async def start(self):
        """Forks a new PTY and executes a shell."""
        self.pid, self.fd = pty.fork()
        if self.pid == 0:  # Child process
            os.environ["TERM"] = "xterm-256color"
            os.execv("/bin/bash", ["/bin/bash"])
        else:  # Parent process
            await self.run()

    async def run(self):
        """Main loop handling bidirectional data flow between PTY and WebSocket."""
        loop = asyncio.get_event_loop()

        def on_pty_read():
            try:
                data = os.read(self.fd, 4096)
                if not data:
                    loop.remove_reader(self.fd)
                    asyncio.create_task(self.websocket.close())
                    return
                asyncio.create_task(self.websocket.send_bytes(data))
            except Exception:
                if self.fd is not None:
                    loop.remove_reader(self.fd)

        loop.add_reader(self.fd, on_pty_read)

        try:
            while True:
                msg = await self.websocket.receive()
                if "text" in msg:
                    payload = json.loads(msg["text"])
                    msg_type = payload.get("type")
                    if msg_type == "input":
                        os.write(self.fd, payload.get("data", "").encode())
                    elif msg_type == "resize":
                        self.set_winsize(payload.get("rows"), payload.get("cols"))
                elif "bytes" in msg:
                    os.write(self.fd, msg["bytes"])
        except (WebSocketDisconnect, Exception):
            pass
        finally:
            if self.fd is not None:
                loop.remove_reader(self.fd)
                try:
                    os.close(self.fd)
                except OSError:
                    pass
            if self.pid is not None:
                try:
                    os.kill(self.pid, signal.SIGTERM)
                    os.waitpid(self.pid, os.WNOHANG)
                except OSError:
                    pass

@router.websocket("/ws")
async def terminal_websocket(websocket: WebSocket):
    """
    WebSocket endpoint for terminal access with token authentication.
    """
    await websocket.accept()
    token = await get_token_auth(websocket)
    if not token:
        return

    session = TerminalSession(websocket)
    try:
        await session.start()
    except Exception:
        await websocket.close()
