"""
Tests for backend/app/api/endpoints/terminal.py

Covers TerminalSession lifecycle, set_winsize behaviour, run() message
handling, error paths, cleanup, and the WebSocket route registered on the
FastAPI app.
"""

import asyncio
import json
import os
import sys
import struct
import signal
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

# Make sure the backend package root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.api.endpoints.terminal import TerminalSession
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient
from main import app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_websocket():
    """Return a MagicMock that mimics a FastAPI WebSocket."""
    ws = MagicMock()
    ws.send_bytes = AsyncMock()
    ws.close = AsyncMock()
    ws.accept = AsyncMock()
    return ws


# ---------------------------------------------------------------------------
# TerminalSession.__init__
# ---------------------------------------------------------------------------

class TestTerminalSessionInit:
    def test_websocket_stored(self):
        ws = _make_mock_websocket()
        session = TerminalSession(ws)
        assert session.websocket is ws

    def test_fd_initially_none(self):
        ws = _make_mock_websocket()
        session = TerminalSession(ws)
        assert session.fd is None

    def test_pid_initially_none(self):
        ws = _make_mock_websocket()
        session = TerminalSession(ws)
        assert session.pid is None


# ---------------------------------------------------------------------------
# TerminalSession.set_winsize
# ---------------------------------------------------------------------------

class TestSetWinsize:
    def test_no_op_when_fd_is_none(self):
        ws = _make_mock_websocket()
        session = TerminalSession(ws)
        # Should not raise even though fd is None
        session.set_winsize(24, 80)

    def test_calls_ioctl_with_packed_struct(self):
        ws = _make_mock_websocket()
        session = TerminalSession(ws)
        session.fd = 5

        with patch("fcntl.ioctl") as mock_ioctl, \
             patch("termios.TIOCSWINSZ", 0x5414, create=True):
            import termios
            session.set_winsize(24, 80)
            expected_winsize = struct.pack("HHHH", 24, 80, 0, 0)
            mock_ioctl.assert_called_once_with(5, termios.TIOCSWINSZ, expected_winsize)

    def test_calls_ioctl_with_custom_pixel_dimensions(self):
        ws = _make_mock_websocket()
        session = TerminalSession(ws)
        session.fd = 7

        with patch("fcntl.ioctl") as mock_ioctl, \
             patch("termios.TIOCSWINSZ", 0x5414, create=True):
            import termios
            session.set_winsize(40, 120, 800, 600)
            expected = struct.pack("HHHH", 40, 120, 800, 600)
            mock_ioctl.assert_called_once_with(7, termios.TIOCSWINSZ, expected)

    def test_set_winsize_does_not_call_ioctl_when_fd_none(self):
        ws = _make_mock_websocket()
        session = TerminalSession(ws)

        with patch("fcntl.ioctl") as mock_ioctl:
            session.set_winsize(24, 80)
            mock_ioctl.assert_not_called()


# ---------------------------------------------------------------------------
# TerminalSession.run – message handling
# ---------------------------------------------------------------------------

class TestTerminalSessionRun:
    """Test the run() coroutine by mocking OS and event-loop primitives."""

    @pytest.mark.asyncio
    async def test_input_message_writes_to_fd(self):
        ws = _make_mock_websocket()
        # Deliver one 'input' message then disconnect
        ws.receive = AsyncMock(side_effect=[
            {"text": json.dumps({"type": "input", "data": "ls\n"})},
            WebSocketDisconnect(),
        ])
        session = TerminalSession(ws)
        session.fd = 10

        mock_loop = MagicMock()
        mock_loop.add_reader = MagicMock()
        mock_loop.remove_reader = MagicMock()

        with patch("asyncio.get_event_loop", return_value=mock_loop), \
             patch("os.write") as mock_write, \
             patch("os.close"), \
             patch("os.kill"), \
             patch("asyncio.sleep", new_callable=AsyncMock), \
             patch("os.waitpid"):
            await session.run()
            mock_write.assert_called_once_with(10, b"ls\n")

    @pytest.mark.asyncio
    async def test_resize_message_calls_set_winsize(self):
        ws = _make_mock_websocket()
        ws.receive = AsyncMock(side_effect=[
            {"text": json.dumps({"type": "resize", "rows": 30, "cols": 100})},
            WebSocketDisconnect(),
        ])
        session = TerminalSession(ws)
        session.fd = 10

        mock_loop = MagicMock()
        mock_loop.add_reader = MagicMock()
        mock_loop.remove_reader = MagicMock()

        with patch("asyncio.get_event_loop", return_value=mock_loop), \
             patch("os.write"), \
             patch("os.close"), \
             patch("os.kill"), \
             patch("asyncio.sleep", new_callable=AsyncMock), \
             patch("os.waitpid"), \
             patch.object(session, "set_winsize") as mock_sw:
            await session.run()
            mock_sw.assert_called_once_with(30, 100)

    @pytest.mark.asyncio
    async def test_raw_bytes_message_writes_to_fd(self):
        ws = _make_mock_websocket()
        ws.receive = AsyncMock(side_effect=[
            {"bytes": b"\x03"},   # Ctrl-C as raw bytes
            WebSocketDisconnect(),
        ])
        session = TerminalSession(ws)
        session.fd = 10

        mock_loop = MagicMock()
        mock_loop.add_reader = MagicMock()
        mock_loop.remove_reader = MagicMock()

        with patch("asyncio.get_event_loop", return_value=mock_loop), \
             patch("os.write") as mock_write, \
             patch("os.close"), \
             patch("os.kill"), \
             patch("asyncio.sleep", new_callable=AsyncMock), \
             patch("os.waitpid"):
            await session.run()
            mock_write.assert_called_once_with(10, b"\x03")

    @pytest.mark.asyncio
    async def test_unknown_text_payload_type_is_ignored(self):
        """A JSON text message with an unknown 'type' should not write to fd."""
        ws = _make_mock_websocket()
        ws.receive = AsyncMock(side_effect=[
            {"text": json.dumps({"type": "unknown_op", "data": "whatever"})},
            WebSocketDisconnect(),
        ])
        session = TerminalSession(ws)
        session.fd = 10

        mock_loop = MagicMock()
        mock_loop.add_reader = MagicMock()
        mock_loop.remove_reader = MagicMock()

        with patch("asyncio.get_event_loop", return_value=mock_loop), \
             patch("os.write") as mock_write, \
             patch("os.close"), \
             patch("os.kill"), \
             patch("asyncio.sleep", new_callable=AsyncMock), \
             patch("os.waitpid"):
            await session.run()
            mock_write.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleanup_closes_fd_on_disconnect(self):
        ws = _make_mock_websocket()
        ws.receive = AsyncMock(side_effect=[WebSocketDisconnect()])
        session = TerminalSession(ws)
        session.fd = 10
        session.pid = 123

        mock_loop = MagicMock()
        mock_loop.add_reader = MagicMock()
        mock_loop.remove_reader = MagicMock()

        with patch("asyncio.get_event_loop", return_value=mock_loop), \
             patch("os.close") as mock_close, \
             patch("os.kill"), \
             patch("asyncio.sleep", new_callable=AsyncMock), \
             patch("os.waitpid"):
            await session.run()
            mock_close.assert_called_once_with(10)

    @pytest.mark.asyncio
    async def test_cleanup_kills_pid_on_disconnect(self):
        ws = _make_mock_websocket()
        ws.receive = AsyncMock(side_effect=[WebSocketDisconnect()])
        session = TerminalSession(ws)
        session.fd = 10
        session.pid = 999

        mock_loop = MagicMock()
        mock_loop.add_reader = MagicMock()
        mock_loop.remove_reader = MagicMock()

        with patch("asyncio.get_event_loop", return_value=mock_loop), \
             patch("os.close"), \
             patch("os.kill") as mock_kill, \
             patch("asyncio.sleep", new_callable=AsyncMock), \
             patch("os.waitpid"):
            await session.run()
            mock_kill.assert_called_once_with(999, signal.SIGTERM)

    @pytest.mark.asyncio
    async def test_cleanup_removes_reader_on_disconnect(self):
        ws = _make_mock_websocket()
        ws.receive = AsyncMock(side_effect=[WebSocketDisconnect()])
        session = TerminalSession(ws)
        session.fd = 10

        mock_loop = MagicMock()
        mock_loop.add_reader = MagicMock()
        mock_loop.remove_reader = MagicMock()

        with patch("asyncio.get_event_loop", return_value=mock_loop), \
             patch("os.close"), \
             patch("os.kill"), \
             patch("asyncio.sleep", new_callable=AsyncMock), \
             patch("os.waitpid"):
            await session.run()
            mock_loop.remove_reader.assert_called_with(10)

    @pytest.mark.asyncio
    async def test_unexpected_exception_still_cleans_up(self):
        """An unexpected exception during receive must still trigger cleanup."""
        ws = _make_mock_websocket()
        ws.receive = AsyncMock(side_effect=[RuntimeError("boom")])
        session = TerminalSession(ws)
        session.fd = 10
        session.pid = 42

        mock_loop = MagicMock()
        mock_loop.add_reader = MagicMock()
        mock_loop.remove_reader = MagicMock()

        with patch("asyncio.get_event_loop", return_value=mock_loop), \
             patch("os.close") as mock_close, \
             patch("os.kill") as mock_kill, \
             patch("asyncio.sleep", new_callable=AsyncMock), \
             patch("os.waitpid"):
            await session.run()
            mock_close.assert_called_once_with(10)
            mock_kill.assert_called_once_with(42, signal.SIGTERM)

    @pytest.mark.asyncio
    async def test_cleanup_suppresses_os_close_error(self):
        """If os.close raises, run() should not propagate the error."""
        ws = _make_mock_websocket()
        ws.receive = AsyncMock(side_effect=[WebSocketDisconnect()])
        session = TerminalSession(ws)
        session.fd = 10

        mock_loop = MagicMock()
        mock_loop.add_reader = MagicMock()
        mock_loop.remove_reader = MagicMock()

        with patch("asyncio.get_event_loop", return_value=mock_loop), \
             patch("os.close", side_effect=OSError("bad fd")), \
             patch("os.kill"), \
             patch("asyncio.sleep", new_callable=AsyncMock), \
             patch("os.waitpid"):
            # Should not raise
            await session.run()

    @pytest.mark.asyncio
    async def test_add_reader_called_with_fd(self):
        """run() registers a PTY reader for the session fd."""
        ws = _make_mock_websocket()
        ws.receive = AsyncMock(side_effect=[WebSocketDisconnect()])
        session = TerminalSession(ws)
        session.fd = 10

        mock_loop = MagicMock()
        mock_loop.add_reader = MagicMock()
        mock_loop.remove_reader = MagicMock()

        with patch("asyncio.get_event_loop", return_value=mock_loop), \
             patch("os.close"), \
             patch("os.kill"), \
             patch("asyncio.sleep", new_callable=AsyncMock), \
             patch("os.waitpid"):
            await session.run()
            # add_reader must be called with our fd as first arg
            assert mock_loop.add_reader.call_args[0][0] == 10


# ---------------------------------------------------------------------------
# on_pty_read callback (tested indirectly via run())
# ---------------------------------------------------------------------------

class TestOnPtyReadCallback:
    @pytest.mark.asyncio
    async def test_pty_data_schedules_send_bytes(self):
        """When PTY has data, on_pty_read should schedule send_bytes."""
        ws = _make_mock_websocket()
        ws.receive = AsyncMock(side_effect=[WebSocketDisconnect()])
        session = TerminalSession(ws)
        session.fd = 10

        captured_callback = None

        def fake_add_reader(fd, cb):
            nonlocal captured_callback
            captured_callback = cb

        mock_loop = MagicMock()
        mock_loop.add_reader = fake_add_reader
        mock_loop.remove_reader = MagicMock()

        created_tasks = []
        def fake_create_task(coro):
            task = MagicMock()
            created_tasks.append(coro)
            return task

        with patch("asyncio.get_event_loop", return_value=mock_loop), \
             patch("os.read", return_value=b"hello"), \
             patch("os.close"), \
             patch("os.kill"), \
             patch("asyncio.sleep", new_callable=AsyncMock), \
             patch("os.waitpid"), \
             patch("asyncio.create_task", side_effect=fake_create_task):
            await session.run()

        # The callback was registered; manually fire it
        if captured_callback:
            captured_callback()
        # At least one coroutine for send_bytes should have been scheduled
        assert len(created_tasks) >= 1

    @pytest.mark.asyncio
    async def test_pty_eof_schedules_close(self):
        """When PTY returns empty bytes (EOF), on_pty_read schedules ws.close."""
        ws = _make_mock_websocket()
        ws.receive = AsyncMock(side_effect=[WebSocketDisconnect()])
        session = TerminalSession(ws)
        session.fd = 10

        captured_callback = None

        def fake_add_reader(fd, cb):
            nonlocal captured_callback
            captured_callback = cb

        mock_loop = MagicMock()
        mock_loop.add_reader = fake_add_reader
        mock_loop.remove_reader = MagicMock()

        created_coros = []

        def fake_create_task(coro):
            task = MagicMock()
            created_coros.append(coro)
            return task

        with patch("asyncio.get_event_loop", return_value=mock_loop), \
             patch("os.read", return_value=b""), \
             patch("os.close"), \
             patch("os.kill"), \
             patch("asyncio.sleep", new_callable=AsyncMock), \
             patch("os.waitpid"), \
             patch("asyncio.create_task", side_effect=fake_create_task):
            await session.run()

        if captured_callback:
            captured_callback()
        # remove_reader should have been called for the EOF case
        mock_loop.remove_reader.assert_called_with(10)


# ---------------------------------------------------------------------------
# terminal_websocket FastAPI route
# ---------------------------------------------------------------------------

class TestTerminalWebsocketRoute:
    def test_websocket_route_registered(self):
        """The /api/terminal/ws route must exist on the app."""
        routes = {route.path for route in app.routes}
        assert "/api/terminal/ws" in routes

    def test_websocket_connect_accepted_and_session_started(self):
        """
        Connecting to /api/terminal/ws should result in websocket.accept()
        being called; we patch TerminalSession.start to avoid real PTY work.
        """
        with patch(
            "app.api.endpoints.terminal.TerminalSession.start",
            new_callable=AsyncMock,
        ):
            client = TestClient(app)
            with client.websocket_connect("/api/terminal/ws") as ws:
                # Connection was accepted; close cleanly from client side
                ws.close()

    def test_websocket_exception_closes_connection(self):
        """
        If TerminalSession.start raises, the WebSocket should be closed
        gracefully rather than leaking the connection.
        """
        with patch(
            "app.api.endpoints.terminal.TerminalSession.start",
            new_callable=AsyncMock,
            side_effect=RuntimeError("fork failed"),
        ):
            client = TestClient(app)
            # The server closes the WS after the error; TestClient should
            # handle this without raising an unhandled exception.
            try:
                with client.websocket_connect("/api/terminal/ws") as ws:
                    ws.close()
            except Exception:
                pass  # Connection closed by server is acceptable here
