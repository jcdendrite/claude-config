"""Process probes shared by the evals tests that spawn real children."""

from __future__ import annotations

import subprocess


def is_running(pid: int) -> bool:
    """True while `pid` is alive. A zombie (ps state Z) has exited and only awaits its parent's wait, so it is not running."""
    stat = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(stat) and not stat.startswith("Z")
