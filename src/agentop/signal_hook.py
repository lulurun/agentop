"""Record only lifecycle metadata; never return a hook decision."""
import json
import sys
import time

try:
    data = json.load(sys.stdin)
    event = data.get("hook_event_name", "")
    kind = data.get("notification_type") or data.get("tool_name") or ""
    tool = sys.argv[2]
    record = json.dumps({"at": time.time(), "event": event, "kind": kind, "tool": tool}) + "\n"
    with open(sys.argv[1], "a") as stream:
        stream.write(record)
except Exception:
    pass
