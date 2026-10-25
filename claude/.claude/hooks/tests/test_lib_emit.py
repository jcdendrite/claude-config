"""Unit tests for _lib.sh's _lib_emit_deny gate label and _lib_emit_allow_with_context."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from helpers import build_path_without

from ._lib_sh_helpers import _LIB_SH, _run_lib_call


def test_deny_gate_label_unset_falls_back_to_basename_derived_label() -> None:
    """A hook that sources _lib.sh without declaring DENY_GATE_LABEL still
    self-identifies, via a `${0##*/}`-minus-`.sh` derivation — the one
    place the $0 fallback does real work, for a forgotten declaration."""
    harness = f'. {_LIB_SH}; _lib_emit_deny "scratch body text"'
    result = subprocess.run(
        ["bash", "-c", harness, "scratch-hook.sh"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    reason = payload["hookSpecificOutput"]["permissionDecisionReason"]
    assert reason == "Blocked by scratch-hook gate: scratch body text"


def test_deny_gate_label_set_renders_in_reason() -> None:
    """The common case, direct at the _lib_emit_deny unit layer: a hook that
    declares DENY_GATE_LABEL gets that exact label in the rendered reason,
    not the $0-derived fallback the sibling test above covers."""
    harness = f'DENY_GATE_LABEL="a-declared-label"; . {_LIB_SH}; _lib_emit_deny "scratch body text"'
    result = subprocess.run(
        ["bash", "-c", harness, "scratch-hook.sh"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    reason = payload["hookSpecificOutput"]["permissionDecisionReason"]
    assert reason == "Blocked by a-declared-label gate: scratch body text"


def test_lib_emit_allow_with_context_emits_expected_envelope() -> None:
    """Emits the PreToolUse allow envelope with additionalContext set to
    the jq-encoded message, mirroring _lib_emit_deny's envelope shape but
    with permissionDecision "allow" and no permissionDecisionReason."""
    result = _run_lib_call('_lib_emit_allow_with_context "test message"', env=dict(os.environ))
    assert result.returncode == 0, repr(result)
    payload = json.loads(result.stdout)
    assert payload == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "additionalContext": "test message",
        }
    }


def test_lib_emit_allow_with_context_degrades_to_no_output_when_jq_absent(tmp_path: Path) -> None:
    """No jq on PATH -> _lib_jq's degrade path returns empty, and the
    caller-facing contract is a silent allow (no stdout). Unlike
    _lib_emit_deny, which hard-blocks (exit 2) on this same jq-absent case,
    losing an informational note is not the fail-closed case _lib_emit_deny
    protects against."""
    farm_dir = tmp_path / "path-farm"
    farm_dir.mkdir()
    restricted_path = build_path_without("jq", farm_dir)

    env = {"PATH": restricted_path, "HOME": str(tmp_path)}
    result = _run_lib_call('_lib_emit_allow_with_context "test message"', env=env)
    assert result.returncode == 0, repr(result)
    assert result.stdout == "", repr(result.stdout)
    assert result.stderr == "", repr(result.stderr)
