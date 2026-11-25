"""Local bridge for status subscribers running outside the service process."""
from __future__ import annotations

import asyncio
import json
import os
import queue
import stat
from dataclasses import asdict


async def serve(feed, path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError(f"Unsafe status socket directory {path.parent}")
    path.parent.chmod(0o700)
    if path.exists():
        try:
            _, writer = await asyncio.open_unix_connection(str(path))
        except (OSError, ConnectionError):
            if stat.S_ISSOCK(path.lstat().st_mode):
                path.unlink()
            else:
                raise RuntimeError(f"Refusing to replace {path}")
        else:
            writer.close()
            await writer.wait_closed()
            raise RuntimeError(f"Status feed already listening at {path}")

    clients = set()

    async def handle(reader, writer):
        clients.add(asyncio.current_task())
        sid = None
        subscription = None
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=5)
            sid = json.loads(raw).get("session_id")
            if not isinstance(sid, str) or not sid or len(sid) > 256:
                return
            subscription = feed.subscribe(sid)
            while not reader.at_eof():
                try:
                    status = subscription.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.1)
                    continue
                writer.write(json.dumps(asdict(status)).encode() + b"\n")
                await writer.drain()
        except (TimeoutError, OSError, ConnectionError, ValueError, TypeError):
            pass
        finally:
            if subscription is not None:
                feed.unsubscribe(sid, subscription)
            clients.discard(asyncio.current_task())
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, ConnectionError):
                pass

    server = await asyncio.start_unix_server(handle, str(path), limit=4096)
    os.chmod(path, 0o600)
    try:
        await asyncio.Future()
    finally:
        server.close()
        for task in tuple(clients):
            task.cancel()
        if clients:
            await asyncio.gather(*clients, return_exceptions=True)
        await server.wait_closed()
        path.unlink(missing_ok=True)
