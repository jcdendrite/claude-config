"""Helpers shared by two or more of the test_lib*.py files that unit-test _lib.sh."""
from __future__ import annotations

import os
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


def _gate_diff_base(
    repo: Path, env: dict | None = None, timeout: float | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'. {_LIB_SH}; _lib_gate_diff_base "$1"', "bash", str(repo)],
        capture_output=True,
        text=True,
        check=False,
        env=env if env is not None else dict(os.environ),
        timeout=timeout,
    )


def _staged_diff_hash(
    repo: Path, base: str, *pathspecs: str, env: dict | None = None, timeout: float | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "bash", "-c",
            f'. {_LIB_SH}; _lib_staged_diff_hash "$1" "$2" "${{@:3}}"',
            "bash", str(repo), base, *pathspecs,
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env if env is not None else dict(os.environ),
        timeout=timeout,
    )


def _forge_state_marker(gitdir: Path, state: str, oid: str) -> None:
    """Write the on-disk shape _lib_git_inprogress_state/_lib_gate_diff_base
    read for `state`, without running the real git operation -- for
    precedence and forged-ref tests that only need the marker shape, not a
    genuine conflict."""
    if state == "rebase":
        (gitdir / "rebase-merge").mkdir(exist_ok=True)
        (gitdir / "rebase-merge" / "head-name").write_text("refs/heads/feature\n")
        (gitdir / "REBASE_HEAD").write_text(oid + "\n")
    elif state == "merge":
        (gitdir / "MERGE_HEAD").write_text(oid + "\n")
    elif state == "cherry-pick":
        (gitdir / "CHERRY_PICK_HEAD").write_text(oid + "\n")
    elif state == "revert":
        (gitdir / "REVERT_HEAD").write_text(oid + "\n")
    else:
        raise ValueError(state)


def _assert_valid_tree_oid(repo: Path, oid: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"{oid}^{{tree}}"],
        capture_output=True,
    )
    assert result.returncode == 0, f"{oid!r} is not a valid tree oid"
