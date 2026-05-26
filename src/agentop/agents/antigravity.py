from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import psutil

from agentop.agents.base import BaseAgent

_LOG_DIR = Path("~/.gemini/antigravity-cli/log")
_LOG_NAME_RE = re.compile(r"cli-(\d{8})_(\d{6})\.log")
_USER_INPUT_RE = re.compile(r'HandleUserInput called with text: "(.+)"')
_START_TOLERANCE_SEC = 15


class AntigravityAgent(BaseAgent):
    name = "antigravity"

    def matches(self, proc_name: str, cmdline: str) -> bool:
        name_lower = proc_name.lower()
        cmd_lower = cmdline.lower()
        if name_lower in ("agy", "antigravity") or name_lower.startswith("agy"):
            return True
        # May run as `node /path/to/bin/agy …`
        for token in cmd_lower.split():
            base = token.split("/")[-1]
            if base in ("agy", "antigravity") or base.startswith("agy-"):
                return True
        return False

    @property
    def launch_cmd(self) -> str:
        return "agy --dangerously-skip-permissions"

    def _find_log_file(self, pid: int) -> Optional[Path]:
        """Find the log file for this process by matching the filename timestamp to the process start time."""
        log_dir = _LOG_DIR.expanduser()
        if not log_dir.exists():
            return None
        try:
            proc_start = psutil.Process(pid).create_time()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None

        best_file: Optional[Path] = None
        best_diff = float("inf")
        for f in log_dir.glob("cli-*.log"):
            m = _LOG_NAME_RE.match(f.name)
            if not m:
                continue
            try:
                log_ts = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").timestamp()
                diff = abs(log_ts - proc_start)
                if diff < best_diff:
                    best_diff = diff
                    best_file = f
            except ValueError:
                continue

        return best_file if best_diff <= _START_TOLERANCE_SEC else None

    def get_ai_title(self, pid: int, cwd: str) -> Optional[str]:
        """Return the first user message from the session log as the title."""
        log_file = self._find_log_file(pid)
        if not log_file:
            return None
        try:
            with open(log_file) as f:
                for line in f:
                    m = _USER_INPUT_RE.search(line)
                    if m:
                        text = m.group(1)
                        return text[:80] + ("…" if len(text) > 80 else "")
        except OSError:
            return None
        return None
