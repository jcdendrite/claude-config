"""Gate matrix: one session id across the shim, the library and the Shell rewrite."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from helpers import HOOKS_DIR, SCRIPTS_DIR

from ._cursor_identity_fixtures import (
    HOST_PROCESS_ID,
    HOST_SHOWING_PS_BODY,
    write_stub_tool_directory,
)
from ._cursor_payloads import (
    CHILD_CONVERSATION_ID,
    CONVERSATION_ID,
    LIB_SH,
    PARENT_CONVERSATION_ID,
    STUBBED_WALK_DEADLINE_SECONDS,
    ShimEnv,
    file_tool_payload,
    hook_args,
    load_shim,
    make_linked_worktree,
    make_shim_env,
    recorded_tool_paths,
    run_shim,
    shell_pre_payload,
    shim_json,
    worktree_lock_text,
)

# --- one session id across the shim, the library and the Shell rewrite ---

def _identity_env(tmp_path: Path) -> tuple[ShimEnv, Path]:
    """An install record whose `ps` shows a Cursor host, and the directory of a `ps` that fails."""
    host_ps = write_stub_tool_directory(tmp_path, "host-ps", "ps", HOST_SHOWING_PS_BODY)
    failing_ps = write_stub_tool_directory(tmp_path, "failing-ps", "ps", "exit 1\n")
    env = make_shim_env(tmp_path, tool_paths={"ps": str(host_ps / "ps"), **recorded_tool_paths()})
    return env, failing_ps


# The two gates that take a worktree lock, each with a call that is allowed in a linked worktree and denied in the main checkout.
WORKTREE_GATE_CALLS = {
    "file-writes": (
        "require-worktree-for-file-writes.sh",
        lambda root: file_tool_payload(root, "Write", str(root / "agent-note.txt"), content="x"),
    ),
    "git-writes": ("require-worktree-for-git-writes.sh", lambda root: shell_pre_payload(root, "git commit -m x")),
}


def _run_worktree_gate(env: ShimEnv, tmp_path: Path, gate: str, payload: dict) -> subprocess.CompletedProcess:
    """The gate's script as the shim runs it for a preToolUse call, from a directory outside every repository."""
    scratch = tmp_path / "gate-scratch"
    scratch.mkdir(exist_ok=True)
    script = WORKTREE_GATE_CALLS[gate][0]
    args = hook_args("PreToolUse", script=HOOKS_DIR / script, label=gate, deadline=STUBBED_WALK_DEADLINE_SECONDS)
    return run_shim(env, payload, args, cwd=scratch)


def _pin_rewrite(env: ShimEnv, tmp_path: Path, payload: dict) -> subprocess.CompletedProcess:
    scratch = tmp_path / "pin-scratch"
    scratch.mkdir(exist_ok=True)
    return run_shim(env, payload, hook_args("PreToolUse", mode="pin", deadline=STUBBED_WALK_DEADLINE_SECONDS), cwd=scratch)


class TestSessionIdentityAcrossTheShim:
    def test_the_rewritten_marker_command_prints_the_payloads_session_id_though_its_own_ps_fails(self, tmp_path: Path) -> None:
        env, failing_ps = _identity_env(tmp_path)
        root = tmp_path.resolve() / "repo"
        root.mkdir()
        command = f"bash {SCRIPTS_DIR / 'marker.sh'} resolve-session-id"
        rewritten = shim_json(_pin_rewrite(env, tmp_path, shell_pre_payload(root, command)))["updated_input"]["command"]
        assert f"CLAUDE_CONFIG_ADAPTER_HOST_PID={HOST_PROCESS_ID}" in rewritten.splitlines()[0]
        tool_directories = ":".join(sorted({str(Path(path).parent) for path in recorded_tool_paths().values()}))

        result = subprocess.run(
            ["bash", "-c", rewritten],
            capture_output=True,
            text=True,
            check=False,
            cwd=tmp_path,
            env={"HOME": str(env.home), "PATH": f"{failing_ps}:{tool_directories}:/usr/bin:/bin", "CLAUDECODE": "1"},
            timeout=60,
        )

        assert (result.returncode, result.stdout) == (0, CONVERSATION_ID), result.stderr

    # The payloads whose session id the three consumers must agree on, and the id each names (None where all three deny).
    DIFFERENTIAL_PAYLOADS = {
        "root": ({}, CONVERSATION_ID),
        "child": ({"conversation_id": CHILD_CONVERSATION_ID, "parent_agent_id": PARENT_CONVERSATION_ID}, PARENT_CONVERSATION_ID),
        "nested-child": (
            {"conversation_id": "nested-0000-4000-8000-06", "parent_agent_id": CHILD_CONVERSATION_ID},
            CHILD_CONVERSATION_ID,
        ),
        "parent-null": ({"parent_agent_id": None}, CONVERSATION_ID),
        "parent-empty": ({"parent_agent_id": ""}, CONVERSATION_ID),
        "parent-number": ({"parent_agent_id": 7}, None),
        # No grammar in the shim or `_lib_valid_session_id_component` caps an id's length, so all three accept it.
        "parent-85-characters": ({"parent_agent_id": "a" * 85}, "a" * 85),
        "parent-ending-in-newline": ({"parent_agent_id": "abc\n"}, None),
    }

    def _shim_session_id(self, shim, payload: dict) -> str | None:
        try:
            return shim.session_id_of(payload)
        except shim.Refusal:
            return None

    def _library_session_id(self, env: ShimEnv, payload: dict) -> str | None:
        completed = subprocess.run(
            ["bash", "-c", '. "$1" && _lib_adapter_session_pair "$2"', "pair", str(LIB_SH), json.dumps(payload)],
            capture_output=True,
            text=True,
            check=False,
            env={
                "HOME": str(env.home),
                "PATH": f"{Path(json.loads(env.record_path.read_text())['tool_paths']['ps']).parent}:{os.environ['PATH']}",
                "CLAUDE_CONFIG_DIR": str(env.config_dir),
            },
            timeout=60,
        )
        return completed.stdout.split()[0] if completed.returncode == 0 and completed.stdout.split() else None

    def _rewrite_session_id(self, env: ShimEnv, tmp_path: Path, payload: dict) -> str | None:
        result = _pin_rewrite(env, tmp_path, payload)
        output = shim_json(result)
        if "updated_input" not in output:
            return None
        environment_line = output["updated_input"]["command"].splitlines()[0]
        printed = subprocess.run(
            ["bash", "-c", environment_line + '; printf %s "$CLAUDE_CONFIG_ADAPTER_SESSION_ID"'],
            capture_output=True,
            text=True,
            check=True,
            env={"HOME": str(env.home), "PATH": "/usr/bin:/bin"},
            timeout=60,
        )
        return printed.stdout

    @pytest.mark.parametrize("gate", WORKTREE_GATE_CALLS)
    @pytest.mark.parametrize("caller", ["root-call", "subagent-call"])
    def test_a_worktree_gate_the_shim_runs_in_a_linked_worktree_allows_and_locks_it_for_the_host_and_the_calls_session(
        self, tmp_path: Path, gate: str, caller: str
    ) -> None:
        env, _ = _identity_env(tmp_path)
        linked, _ = make_linked_worktree(tmp_path, opted_in=True)
        _, build_call = WORKTREE_GATE_CALLS[gate]
        payload = build_call(linked)
        expected_session_id = CONVERSATION_ID
        if caller == "subagent-call":
            payload.update(conversation_id=CHILD_CONVERSATION_ID, parent_agent_id=PARENT_CONVERSATION_ID)
            expected_session_id = PARENT_CONVERSATION_ID
        result = _run_worktree_gate(env, tmp_path, gate, payload)

        assert result.returncode == 0, result.stderr
        assert shim_json(result).get("permission") != "deny"
        assert worktree_lock_text(linked) == f"claude-code pid {HOST_PROCESS_ID} session {expected_session_id}"

    @pytest.mark.parametrize("gate", WORKTREE_GATE_CALLS)
    def test_the_same_call_in_the_main_checkout_is_denied_and_takes_no_lock(self, tmp_path: Path, gate: str) -> None:
        env, _ = _identity_env(tmp_path)
        _, common_dir = make_linked_worktree(tmp_path, opted_in=True)
        main = Path(common_dir).parent
        _, build_call = WORKTREE_GATE_CALLS[gate]

        result = _run_worktree_gate(env, tmp_path, gate, build_call(main))

        assert shim_json(result).get("permission") == "deny"
        assert worktree_lock_text(main) == ""

    @pytest.mark.parametrize("case", list(DIFFERENTIAL_PAYLOADS))
    def test_the_shims_session_id_the_librarys_pair_and_the_shell_rewrites_export_agree_on_every_payload(
        self, tmp_path: Path, case: str
    ) -> None:
        overrides, expected = self.DIFFERENTIAL_PAYLOADS[case]
        env, _ = _identity_env(tmp_path)
        root = tmp_path.resolve() / "repo"
        root.mkdir()
        payload = shell_pre_payload(root, "make")
        payload.update(overrides)

        outcomes = (
            self._shim_session_id(load_shim(), payload),
            self._library_session_id(env, payload),
            self._rewrite_session_id(env, tmp_path, payload),
        )

        assert outcomes == (expected, expected, expected), case
