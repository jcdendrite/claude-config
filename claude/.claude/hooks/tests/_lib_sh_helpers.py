"""Helpers shared by two or more of the test_lib*.py files that unit-test _lib.sh."""
from __future__ import annotations

import subprocess
from pathlib import Path

from helpers import HOOKS_DIR

# Path to _lib.sh: this module lives in hooks/tests/, _lib.sh is in hooks/.
_LIB_SH = Path(__file__).resolve().parents[1] / "_lib.sh"
# The skill-management plugin ships its own copy of _lib_capped_for, since plugin hooks cannot source the stowed _lib.sh.
_SKILL_MANAGEMENT_PLUGIN_LIB = HOOKS_DIR.parent.parent.parent / "plugins" / "skill-management" / "hooks" / "_lib.sh"


def _run_lib_call(call: str, env: dict) -> subprocess.CompletedProcess:
    """Source _lib.sh, then run one statement that calls a helper directly."""
    harness = f". {_LIB_SH}; {call}"
    return subprocess.run(
        ["bash", "-c", harness],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

