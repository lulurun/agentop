#!/usr/bin/env python3
"""Watch a managed session's lifecycle events or run a small isolated CLI probe."""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from agentop.signals import EventReader, event_path, launch_flags, read_status


def watch(session):
    previous = object()
    reader = EventReader(session)
    print(f"Watching {session}: {event_path(session)} (Ctrl-C to stop)", flush=True)
    try:
        while True:
            status = reader.read()
            current = (status.state, status.event, status.at) if status else None
            if current != previous:
                print(current or "No hook events; screen fallback may be in use", flush=True)
                previous = current
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass


def probe(tool):
    import shlex

    original_home = Path.home()
    with tempfile.TemporaryDirectory(prefix="agentop-status-") as directory:
        root = Path(directory)
        for name in ("codex", "claude", "home"):
            (root / name).mkdir()
        credentials = ((original_home / ".codex/auth.json", root / "codex/auth.json"),
                       (original_home / ".claude/.credentials.json", root / "claude/.credentials.json"))
        for source, destination in credentials:
            if source.is_file():
                shutil.copyfile(source, destination)
                destination.chmod(0o600)
        env = dict(os.environ, HOME=str(root / "home"), AGENTOP_HOME=str(root / "agentop"),
                   CODEX_HOME=str(root / "codex"), CLAUDE_CONFIG_DIR=str(root / "claude"),
                   XDG_CONFIG_HOME=str(root / "config"), XDG_DATA_HOME=str(root / "data"),
                   XDG_CACHE_HOME=str(root / "cache"))
        os.environ["AGENTOP_HOME"] = env["AGENTOP_HOME"]
        flags = shlex.split(launch_flags(tool, tool))
        if tool == "codex":
            cmd = ["codex", "exec", "--skip-git-repo-check", "--dangerously-bypass-hook-trust", "-m", "gpt-6-luna"]
        else:
            cmd = ["claude", "-p", "--model", "haiku", "--no-session-persistence"]
        print(f"Running {tool} with a short prompt and temporary configuration", flush=True)
        result = subprocess.run([*cmd, *flags, "Reply with exactly OK."], cwd=root, env=env, text=True,
                                capture_output=True, check=False, timeout=120)
        path = event_path(tool)
        print(path.read_text() or "No hook events recorded.")
        print(f"Exit: {result.returncode}; final status: {read_status(tool)}")
        if result.returncode:
            print(result.stderr[-1000:])
            print(result.stdout[-1000:])
        return result.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--session", help="Watch an agentop tmux session")
    group.add_argument("--probe", choices=("claude", "codex"), help="Run a short isolated live CLI test")
    args = parser.parse_args()
    if args.session:
        watch(args.session)
        return 0
    return probe(args.probe)


if __name__ == "__main__":
    raise SystemExit(main())
