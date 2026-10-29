"""Privileged command execution over a WebSocket-backed pseudo-terminal.

Security posture
----------------
* **Authorization happens before ``accept()``.** A peer without the
  administrative token never gets a connected socket, so no PTY is forked, no
  file descriptors are allocated and the connection never joins the fan-out.
  The handshake is refused with close code ``1008`` (policy violation), which is
  what the frontend's elevation gate reports.
* **The token is the control plane's admin secret** (``NOC_ADMIN_TOKEN``, or the
  legacy ``TERMINAL_ACCESS_TOKEN`` alias) — see :mod:`app.core.auth`. The
  frontend gate at ``/terminal`` collects exactly that value and appends it as
  ``?token=``; ``Authorization: Bearer`` and ``X-NOC-Token`` are accepted too.
* **A bad frame is not a dead session.** Malformed JSON, an unknown message
  type, a resize frame missing ``rows``/``cols`` or a non-string ``data`` field
  is logged and skipped. Only a transport error (disconnect, or the child
  exiting) ends the loop, so one buggy client-side frame can no longer tear down
  an operator's shell mid-command.

Wire protocol (client → server)
-------------------------------
``{"type": "input", "data": "<keystrokes>"}``
``{"type": "resize", "rows": 40, "cols": 120}``
raw binary frames are written to the PTY verbatim.
"""

import asyncio
import json
import logging
import os
import pty
import signal
import struct
import fcntl
import termios
from typing import Any, Dict, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.auth import Role, authorize_websocket

logger = logging.getLogger(__name__)

router = APIRouter()

#: Terminal geometry used when a resize frame omits ``rows``/``cols`` or sends
#: something unusable. Matches the xterm.js default so a partial frame is a
#: no-op rather than an error.
DEFAULT_ROWS = 24
DEFAULT_COLS = 80
#: Upper bound for a negotiated dimension; anything larger is a broken client
#: (and some ioctl implementations reject values that overflow ``unsigned short``).
MAX_DIMENSION = 4096


def _dimension(value: Any, default: int) -> int:
    """
    Coerce one terminal dimension to a safe ``unsigned short``.

    Parameters:
        value (Any): Raw value from a resize frame (``None``, ``str``, ``float``…).
        default (int): Fallback when the value cannot be used.

    Returns:
        int: Value clamped to ``1..MAX_DIMENSION``.
    """
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed < 1:
        return default
    return min(parsed, MAX_DIMENSION)


class TerminalSession:
    """
    Manages a pseudo-terminal (PTY) session connected to a WebSocket.

    One instance per accepted connection; :meth:`start` forks the shell and
    :meth:`run` pumps bytes in both directions until the peer leaves or the
    child exits.
    """

    def __init__(self, websocket: WebSocket):
        """
        Create an idle session bound to an already-authorized socket.

        Parameters:
            websocket (WebSocket): An **accepted** WebSocket.
        """
        self.websocket = websocket
        self.fd: Optional[int] = None
        self.pid: Optional[int] = None

    def set_winsize(
        self,
        rows: Any = None,
        cols: Any = None,
        xpixel: int = 0,
        ypixel: int = 0,
    ) -> None:
        """
        Sets the terminal window size using ioctl.

        Missing or malformed values fall back to :data:`DEFAULT_ROWS` /
        :data:`DEFAULT_COLS` instead of raising, so a resize frame that carries
        only one of the two dimensions cannot kill the session.

        Parameters:
            rows (Any): Row count from the client (optional).
            cols (Any): Column count from the client (optional).
            xpixel (int): Horizontal pixels, unused by most terminals.
            ypixel (int): Vertical pixels, unused by most terminals.
        """
        if self.fd is None:
            return
        winsize = struct.pack(
            "HHHH",
            _dimension(rows, DEFAULT_ROWS),
            _dimension(cols, DEFAULT_COLS),
            _dimension(xpixel, 0),
            _dimension(ypixel, 0),
        )
        try:
            fcntl.ioctl(self.fd, termios.TIOCSWINSZ, winsize)
        except OSError as exc:
            # A closed/EOF'd PTY is not a reason to drop the WebSocket.
            logger.debug("Ignoring failed TIOCSWINSZ: %s", exc)

    async def start(self) -> None:
        """Forks a new PTY and executes a shell."""
        self.pid, self.fd = pty.fork()
        if self.pid == 0:  # Child process
            os.environ["TERM"] = "xterm-256color"
            os.execv("/bin/bash", ["/bin/bash"])
        else:  # Parent process
            await self.run()

    async def handle_text_frame(self, raw: Any) -> None:
        """
        Apply one JSON control frame from the client.

        Every failure mode is contained here: a frame that is not JSON, not an
        object, or carries an unknown ``type`` is logged and dropped. The
        session keeps running, which is the whole point — an operator's shell
        must not die because a client sent one bad frame.

        Parameters:
            raw (Any): Text payload of the received frame.
        """
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            logger.debug("Ignoring non-JSON terminal frame (%r)", raw)
            return
        if not isinstance(payload, dict):
            logger.debug("Ignoring non-object terminal frame (%r)", raw)
            return

        msg_type = payload.get("type")
        if msg_type == "input":
            data = payload.get("data")
            if not isinstance(data, str):
                logger.debug("Ignoring input frame without string data")
                return
            self._write(data.encode())
        elif msg_type == "resize":
            self.set_winsize(payload.get("rows"), payload.get("cols"))
        else:
            logger.debug("Ignoring unsupported terminal frame type %r", msg_type)

    def _write(self, data: bytes) -> None:
        """
        Write bytes to the PTY, ignoring a PTY that has already gone away.

        Parameters:
            data (bytes): Bytes to forward to the shell.
        """
        if self.fd is None or not data:
            return
        try:
            os.write(self.fd, data)
        except OSError as exc:
            logger.debug("PTY write failed (child likely exited): %s", exc)

    async def run(self) -> None:
        """Main loop handling bidirectional data flow between PTY and WebSocket."""
        loop = asyncio.get_event_loop()

        def on_pty_read() -> None:
            """
            Drain the PTY and forward the bytes to the browser.

            Runs on the event loop's reader callback, so it must never raise:
            a closed fd or a dead socket just unregisters the reader.
            """
            try:
                data = os.read(self.fd, 4096)
                if not data:
                    loop.remove_reader(self.fd)
                    asyncio.create_task(self.websocket.close())
                    return
                asyncio.create_task(self.websocket.send_bytes(data))
            except Exception:  # noqa: BLE001 - reader must not raise
                if self.fd is not None:
                    try:
                        loop.remove_reader(self.fd)
                    except Exception:  # noqa: BLE001
                        pass

        loop.add_reader(self.fd, on_pty_read)
        try:
            while True:
                message: Dict[str, Any] = await self.websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                try:
                    if message.get("text") is not None:
                        await self.handle_text_frame(message["text"])
                    elif message.get("bytes") is not None:
                        self._write(message["bytes"])
                except (WebSocketDisconnect, RuntimeError):
                    # Transport is gone; nothing left to serve.
                    break
                except Exception as exc:  # noqa: BLE001 - one bad frame ≠ dead session
                    logger.warning("Skipping bad terminal frame: %s", exc)
                    continue
        except WebSocketDisconnect:
            pass
        except Exception as exc:  # noqa: BLE001 - never leak the PTY
            logger.debug("Terminal session ended with error: %s", exc)
        finally:
            self.close()

    def close(self) -> None:
        """Release the PTY: drop the reader, close the fd and reap the child."""
        loop = asyncio.get_event_loop()
        if self.fd is not None:
            try:
                loop.remove_reader(self.fd)
            except Exception:  # noqa: BLE001 - reader may already be gone
                pass
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None
        if self.pid is not None:
            try:
                os.kill(self.pid, signal.SIGTERM)
            except OSError:
                pass
            try:
                os.waitpid(self.pid, os.WNOHANG)
            except OSError:
                pass
            self.pid = None


@router.websocket("/ws")
async def terminal_websocket(websocket: WebSocket):
    """
    WebSocket endpoint for terminal access, gated on the administrative role.

    Authorization runs **before** ``accept()``: a peer that presents no token (or
    the wrong one) is refused during the handshake with code ``1008`` and never
    reaches a PTY.

    Parameters:
        websocket (WebSocket): The incoming handshake.
    """
    role = await authorize_websocket(websocket, Role.ADMIN)
    if role is None:
        return

    await websocket.accept()
    logger.info("Terminal session opened by role=%s", role.value)
    session = TerminalSession(websocket)
    try:
        await session.start()
    except Exception as exc:  # noqa: BLE001 - always close cleanly
        logger.warning("Terminal session failed: %s", exc)
        session.close()
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass
    finally:
        logger.info("Terminal session closed by role=%s", role.value)
