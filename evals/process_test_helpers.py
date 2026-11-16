"""Process probes shared by the evals tests that spawn real children."""

from __future__ import annotations

import subprocess


def is_running(pid: int) -> bool:
    """True while `pid` is alive. A zombie (ps state Z) has exited and only awaits its parent's wait, so it is not running.

    Raises when ps itself fails (missing, denied), because "ps could not tell" must not read as "the child is gone".
    ps exits 1 with no output for a pid that does not exist.
    """
    result = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    if result.returncode != 0 and result.stderr.strip():
        raise RuntimeError(f"ps -p {pid} failed with exit {result.returncode}: {result.stderr.strip()}")
    stat = result.stdout.strip()
    return bool(stat) and not stat.startswith("Z")
