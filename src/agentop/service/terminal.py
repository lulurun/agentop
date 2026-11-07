"""WebSocket-to-PTY bridge. One socket is one `tmux attach-session` client.

Binary frames carry terminal bytes both ways. Text frames are JSON control
messages: {"type": "resize", ...} from the client, {"type": "closed", ...} from
the service. Disconnecting detaches; it never affects the tmux session.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import pty
import signal
import struct
import termios

from fastapi import WebSocket, WebSocketDisconnect

from agentop import tmux
from agentop.tmux import Session

LOG = logging.getLogger(__name__)

_READ_SIZE = 65536
_TERMINATE_WAIT = 2.0


def clamp_size(cols: int, rows: int) -> tuple[int, int]:
    return max(1, min(cols, 1000)), max(1, min(rows, 1000))


def _set_size(fd: int, cols: int, rows: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


async def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        try:
            view = view[os.write(fd, view) :]
        except BlockingIOError:
            await asyncio.sleep(0.01)


async def _pty_to_socket(ws: WebSocket, fd: int) -> None:
    """Forward output, reading the next chunk only once the last has been sent.

    That is the whole of the backpressure: a slow client stops the reads, the
    PTY fills, and tmux handles a slow client by dropping output and redrawing.
    The reader is removed while sending because it is level-triggered and would
    otherwise spin the event loop.
    """
    loop = asyncio.get_running_loop()
    readable = asyncio.Event()

    def on_readable():
        loop.remove_reader(fd)
        readable.set()

    try:
        while True:
            loop.add_reader(fd, on_readable)
            await readable.wait()
            readable.clear()
            try:
                data = os.read(fd, _READ_SIZE)
            except BlockingIOError:
                continue
            except OSError:  # EIO: the tmux client has gone
                return
            if not data:
                return
            await ws.send_bytes(data)
    finally:
        loop.remove_reader(fd)


async def _socket_to_pty(ws: WebSocket, fd: int, proc: asyncio.subprocess.Process) -> None:
    while True:
        message = await ws.receive()
        if message["type"] == "websocket.disconnect":
            return
        if message.get("bytes") is not None:
            await _write_all(fd, message["bytes"])
        elif message.get("text") is not None:
            try:
                payload = json.loads(message["text"])
                if payload.get("type") == "resize":
                    _set_size(fd, *clamp_size(int(payload["cols"]), int(payload["rows"])))
                    # The kernel only signals a tty's controlling process group,
                    # and the attach client is not that, so tell it directly.
                    proc.send_signal(signal.SIGWINCH)
            except ProcessLookupError:
                return
            except (ValueError, KeyError, TypeError, AttributeError):
                LOG.debug("Ignored bad control frame: %r", message["text"][:200])


async def _end_process(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None:
        proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), _TERMINATE_WAIT)
    except TimeoutError:
        proc.kill()
        await proc.wait()


async def bridge(ws: WebSocket, tmux_session: str, cols: int, rows: int, send_closed: bool = True) -> None:
    """Attach to a tmux session over an accepted WebSocket, until either side ends.

    send_closed=False leaves out the control frame, for the old dashboard,
    which would print it into the terminal.
    """
    master, slave = pty.openpty()
    try:
        _set_size(master, *clamp_size(cols, rows))
        os.set_blocking(master, False)
        env = {**os.environ, "TERM": "xterm-256color", "COLORTERM": "truecolor"}
        proc = await asyncio.create_subprocess_exec(
            *tmux.argv("-u", "attach-session", "-t", tmux.session_target(tmux_session)),
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=env,
            start_new_session=True,
        )
    except Exception:
        os.close(master)
        raise
    finally:
        os.close(slave)

    tasks = [
        asyncio.ensure_future(_pty_to_socket(ws, master)),
        asyncio.ensure_future(_socket_to_pty(ws, master, proc)),
    ]
    output_task = tasks[0]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        client_left = output_task not in done
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await _end_process(proc)
        os.close(master)

    if client_left:
        return
    try:
        if send_closed:
            ended = not await asyncio.to_thread(Session.has, tmux_session)
            await ws.send_text(json.dumps({"type": "closed", "reason": "session_ended" if ended else "detached"}))
        await ws.close()
    except (WebSocketDisconnect, RuntimeError):
        pass
