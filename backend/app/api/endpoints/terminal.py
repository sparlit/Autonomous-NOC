import os
import pty
import fcntl
import termios
import struct
import asyncio
import signal
import json
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()
logger = logging.getLogger(__name__)

class TerminalSession:
    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.fd = None
        self.pid = None

    def set_winsize(self, row, col, xpixel=0, ypixel=0):
        if self.fd:
            winsize = struct.pack("HHHH", row, col, xpixel, ypixel)
            fcntl.ioctl(self.fd, termios.TIOCSWINSZ, winsize)

    async def start(self):
        self.pid, self.fd = pty.fork()
        if self.pid == 0:  # Child process
            os.environ["TERM"] = "xterm-256color"
            os.environ["SHELL"] = "/bin/bash"
            # Start in the user's home directory
            os.chdir(os.path.expanduser("~"))
            os.execv("/bin/bash", ["/bin/bash"])
        else:  # Parent process
            # Set non-blocking
            flags = fcntl.fcntl(self.fd, fcntl.F_GETFL)
            fcntl.fcntl(self.fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            await self.run()

    async def run(self):
        loop = asyncio.get_event_loop()
        
        def on_pty_read():
            try:
                data = os.read(self.fd, 8192)
                if data:
                    # We use a synchronous-looking call but we are in a callback
                    # WebSocket.send_bytes is async, so we need to schedule it
                    asyncio.create_task(self.websocket.send_bytes(data))
                else:
                    # EOF
                    loop.remove_reader(self.fd)
                    asyncio.create_task(self.websocket.close())
            except (BlockingIOError, InterruptedError):
                pass
            except Exception as e:
                logger.error(f"PTY read error: {e}")
                loop.remove_reader(self.fd)
                asyncio.create_task(self.websocket.close())

        loop.add_reader(self.fd, on_pty_read)

        try:
            while True:
                data = await self.websocket.receive()
                if "text" in data:
                    payload = json.loads(data["text"])
                    if payload.get("type") == "input":
                        os.write(self.fd, payload["data"].encode())
                    elif payload.get("type") == "resize":
                        self.set_winsize(payload["rows"], payload["cols"])
                elif "bytes" in data:
                    os.write(self.fd, data["bytes"])
        except WebSocketDisconnect:
            logger.info("WebSocket disconnected")
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
        finally:
            loop.remove_reader(self.fd)
            if self.fd:
                try:
                    os.close(self.fd)
                except:
                    pass
            if self.pid:
                try:
                    os.kill(self.pid, signal.SIGTERM)
                    # Wait a bit for child to exit
                    await asyncio.sleep(0.1)
                    os.waitpid(self.pid, os.WNOHANG)
                except:
                    pass

@router.websocket("/ws")
async def terminal_websocket(websocket: WebSocket):
    await websocket.accept()
    session = TerminalSession(websocket)
    try:
        await session.start()
    except Exception as e:
        logger.error(f"Terminal session failed to start: {e}")
        await websocket.close()
