"""Actor: sends text to a tmux session and captures output after idle detection."""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
from collections.abc import Callable

from agentop.paths import state_dir
from agentop.status_feed import socket_path
from agentop.tmux import CapturePane

LOG = logging.getLogger(__name__)

_POLL_INTERVAL = 2.0
_TIMEOUT = 1800.0


class Actor:
    def __init__(
        self,
        session: str,
        name: str,
        idle_seconds: float = 5.0,
        *,
        send_text: Callable[[str, str], None],
    ):
        """`send_text(session, text)` types into the tool: its AgentCli.deliver, since each tool takes a paste its own way."""
        self.session = session
        self.name = name
        self.idle_seconds = idle_seconds
        self._send_text = send_text
        self._stop: threading.Event | None = None
        self._before_send = 0.0
        self._sent = False

    def attach(self, stop_event: threading.Event) -> Actor:
        self._stop = stop_event
        return self

    def send(self, text: str) -> None:
        self._before_send = time.time()
        self._sent = True
        self._send_text(self.session, text)

    def receive(self) -> str | None:
        """Wait for the screen to stabilise for idle_seconds, then return the full scrollback."""
        if self._sent:
            return self._receive_status()
        deadline = time.monotonic() + _TIMEOUT
        last_screen: str | None = None
        last_change = time.monotonic()

        LOG.info("[%s] wait_for_idle started", self.session)

        while not self._stop.is_set() and time.monotonic() < deadline:
            time.sleep(_POLL_INTERVAL)
            if self._stop.is_set():
                return None
            screen = CapturePane.screen(self.session)
            if screen != last_screen:
                last_screen = screen
                last_change = time.monotonic()
                LOG.info("[%s] screen changed", self.session)
            elif last_screen is not None and time.monotonic() - last_change >= self.idle_seconds:
                LOG.info("[%s] idle detected", self.session)
                return CapturePane.scrollback(self.session)

        LOG.info("[%s] wait_for_idle timeout/stopped", self.session)
        return None

    def _receive_status(self) -> str | None:
        while not self._stop.is_set():
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                    connection.settimeout(1.0)
                    connection.connect(str(socket_path(state_dir())))
                    connection.sendall(json.dumps({"session_id": self.session}).encode() + b"\n")
                    pending = b""
                    deadline = time.monotonic() + _TIMEOUT
                    while not self._stop.is_set() and time.monotonic() < deadline:
                        try:
                            data = connection.recv(4096)
                        except TimeoutError:
                            continue
                        if not data:
                            break
                        pending += data
                        while b"\n" in pending:
                            line, pending = pending.split(b"\n", 1)
                            try:
                                status = json.loads(line)
                            except ValueError:
                                continue
                            if status.get("state") == "done" and status.get("since", 0) >= self._before_send:
                                return CapturePane.scrollback(self.session)
                    if time.monotonic() >= deadline:
                        return None
            except OSError:
                self._stop.wait(1)
        return None
