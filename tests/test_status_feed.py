import asyncio
import json

from agentop.status_feed import SessionStatus, StatusFeed


def test_subscribers_receive_only_their_session_and_latest_value():
    feed = StatusFeed()
    first = feed.subscribe("one")
    second = feed.subscribe("one")
    other = feed.subscribe("two")
    feed.publish(SessionStatus("one", "working", 1, "hook"))
    feed.publish(SessionStatus("one", "done", 2, "hook", "Stop"))
    assert first.get_nowait().state == "done"
    assert second.get_nowait().state == "done"
    assert other.empty()
    late = feed.subscribe("one")
    assert late.get_nowait().state == "done"
    feed.unsubscribe("one", late)
    feed.clear()
    assert first.get_nowait().state == "unknown"
    assert feed.subscribe("one").empty()


def test_local_bridge_streams_subscribed_session(tmp_path):
    from agentop.plugins.attention.bridge import serve

    async def run():
        feed = StatusFeed()
        path = tmp_path / "s.sock"
        task = asyncio.create_task(serve(feed, path))
        try:
            for _ in range(100):
                if path.exists():
                    break
                await asyncio.sleep(0.01)
            reader, writer = await asyncio.open_unix_connection(str(path))
            writer.write(b'{"session_id":"one"}\n')
            await writer.drain()
            feed.publish(SessionStatus("two", "done", 1, "hook"))
            feed.publish(SessionStatus("one", "input", 2, "hook", "PermissionRequest"))
            item = json.loads(await asyncio.wait_for(reader.readline(), 3))
            assert item["session_id"] == "one"
            assert item["state"] == "input"
            assert path.stat().st_mode & 0o777 == 0o600
            writer.close()
            await writer.wait_closed()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert not path.exists()

    asyncio.run(run())


def test_dialogue_actor_waits_for_its_feed_and_ignores_input(tmp_path, monkeypatch):
    import threading
    import time

    from agentop.actor import Actor
    from agentop.plugins.attention.bridge import serve
    from agentop.tmux import CapturePane

    async def run():
        feed = StatusFeed()
        from agentop.status_feed import socket_path

        path = socket_path(tmp_path)
        monkeypatch.setattr("agentop.actor.state_dir", lambda: tmp_path)
        monkeypatch.setattr(CapturePane, "scrollback", lambda session: "answer")
        bridge = asyncio.create_task(serve(feed, path))
        try:
            for _ in range(100):
                if path.exists():
                    break
                await asyncio.sleep(0.01)
            actor = Actor("one", "A", send_text=lambda session, text: None).attach(threading.Event())
            actor.send("question")
            receiving = asyncio.create_task(asyncio.to_thread(actor.receive))
            await asyncio.sleep(0.1)
            feed.publish(SessionStatus("two", "done", time.time(), "hook"))
            feed.publish(SessionStatus("one", "input", time.time(), "hook"))
            await asyncio.sleep(0.1)
            assert not receiving.done()
            feed.publish(SessionStatus("one", "done", time.time(), "hook", "Stop"))
            assert await asyncio.wait_for(receiving, 3) == "answer"
        finally:
            bridge.cancel()
            await asyncio.gather(bridge, return_exceptions=True)

    asyncio.run(run())


def test_actor_reconnects_after_bridge_restart(tmp_path, monkeypatch):
    import threading
    import time

    from agentop.actor import Actor
    from agentop.plugins.attention.bridge import serve
    from agentop.status_feed import socket_path
    from agentop.tmux import CapturePane

    async def run():
        feed = StatusFeed()
        path = socket_path(tmp_path)
        monkeypatch.setattr("agentop.actor.state_dir", lambda: tmp_path)
        monkeypatch.setattr(CapturePane, "scrollback", lambda session: "answer")
        bridge = asyncio.create_task(serve(feed, path))
        for _ in range(100):
            if path.exists():
                break
            await asyncio.sleep(0.01)
        stop = threading.Event()
        actor = Actor("one", "A", send_text=lambda session, text: None).attach(stop)
        actor.send("question")
        receiving = asyncio.create_task(asyncio.to_thread(actor.receive))
        await asyncio.sleep(0.2)
        bridge.cancel()
        await asyncio.gather(bridge, return_exceptions=True)
        bridge = asyncio.create_task(serve(feed, path))
        try:
            for _ in range(100):
                if path.exists():
                    break
                await asyncio.sleep(0.01)
            feed.publish(SessionStatus("one", "done", time.time(), "hook", "Stop"))
            assert await asyncio.wait_for(receiving, 4) == "answer"
        finally:
            stop.set()
            bridge.cancel()
            await asyncio.gather(bridge, return_exceptions=True)

    asyncio.run(run())
