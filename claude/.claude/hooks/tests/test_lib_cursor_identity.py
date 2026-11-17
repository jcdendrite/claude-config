"""Tests for the Cursor adapter's host-neutral identity in _lib.sh and the two session hooks.

TestClaudePathSnapshot holds a literal snapshot of the touched functions' results over a fixed corpus. Its
`claude-` cases pin Claude Code behavior, so each runs with CLAUDECODE set. Their entries come from the hooks
directory at commit 59c409a0, which holds no adapter code. To check them, run the class against a checkout of
that commit:

    IDENTITY_SNAPSHOT_HOOKS_DIR=<checkout of 59c409a0>/claude/.claude/hooks pytest -k TestClaudePathSnapshot

Every `claude-` case must pass there, and exactly the `cursor-` cases in EXPECTED_DIFFERENT_CASE_IDS must fail.

A process under a fake Cursor host runs as a child of this test process, so a fake tree's nearest ancestor takes
this test process's PID (see _cursor_identity_fixtures.py).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
from helpers import (
    HOOKS_DIR,
    SCRIPTS_DIR,
    assert_cap_engaged,
    init_git_repo_with_commit,
    live_process,
    scaled_shim_sleep,
    write_prewarmed_stub,
    write_scaled_timeout_shim,
)

from ._cursor_identity_fixtures import (
    CLI_INVOKED_PATH,
    CLI_WORKER_GLOBAL_STORAGE_PATH,
    DEFAULT_PPID_PAD_WIDTH,
    FAKE_LIVE_START_TIME,
    FAKE_PID_BASE,
    HOST_NAME_LOOKALIKES,
    MACOS_CURSOR_HELPER_PATH,
    MACOS_CURSOR_MAIN_PATH,
    PID_GRAMMAR_EDGE_TABLE,
    RETITLED_HELPER_TITLE,
    FakeProcessTree,
    PsRequestRule,
    PsStub,
    build_fake_process_tree,
    install_ps_stub,
)
from ._cursor_payloads import (
    CHILD_CONVERSATION_ID,
    CONVERSATION_ID,
    PARENT_CONVERSATION_ID,
    make_linked_worktree,
    worktree_lock_text,
)
from .conftest import _dead_pid, _seed_session
from .test_lib_worktree_collision_guard import (
    _FIXED_START_TIME,
    _OTHER_CALLER_PID,
    _git_dir,
    _install_failing_ps,
    _run_lock_is_mine,
)
from .test_review_ledger_script import _append_args

SNAPSHOT_HOOKS_DIR_ENV = "IDENTITY_SNAPSHOT_HOOKS_DIR"

SESSION_VARIABLE = "CLAUDE_CONFIG_ADAPTER_SESSION_ID"
HOST_VARIABLE = "CLAUDE_CONFIG_ADAPTER_HOST_PID"
ADAPTER_SESSION_ID = "adapter-session-id"
REGISTERED_SESSION_ID = "registered-session"
VALID_HOST_PID = "4242"
ADAPTER_VARIABLES = {SESSION_VARIABLE: ADAPTER_SESSION_ID, HOST_VARIABLE: VALID_HOST_PID}

CLAUDE_CODE_PAYLOAD = {
    "session_id": "claude-code-session",
    "tool_name": "Write",
    "tool_input": {"file_path": "/scratch/f.txt"},
    "cwd": "/scratch",
}
CURSOR_PAYLOAD = {
    "conversation_id": CONVERSATION_ID,
    "session_id": CONVERSATION_ID,
    "workspace_roots": ["/scratch"],
    "cursor_version": "0.0.0",
    "tool_name": "Write",
}
CLAUDE_PAYLOAD_WITH_PREDICATE_KEY = {**CLAUDE_CODE_PAYLOAD, "workspace_roots": ["/scratch"]}

# The key sets the predicate tests: each alone, and both, as every Cursor record carries them.
PREDICATE_KEY_SETS = (
    {"workspace_roots": ["/scratch"]},
    {"cursor_version": "0.0.0"},
    {"workspace_roots": ["/scratch"], "cursor_version": "0.0.0"},
)

CLAUDE_AS_PARENT = ("claude",)
UNDER_CURSOR = ("Cursor", "shell")
UNDER_CLI = ("agent", "shell")

SESSION_HOOK_DECLINE_FRAGMENT = "untranslated Cursor payload declined"
NO_HOST_ABOVE_PYTEST_REASON = "this test process has a host ancestor, which the walk could print in place of the CLI stand-in"
SESSION_HOOKS = {
    "capture-session-id.sh": ("sessions", "lookup file not written"),
    "record-session-end.sh": ("session-end-records", "no SessionEnd record written"),
}

# The fixed ids and start time hold digit runs a worker PID can equal, so `Scene.normalize` passes them through.
NORMALIZE_FIXED_FIELDS = r"conv-[0-9-]+|\d{2}:\d{2}:\d{2} \d{4}"


@dataclass
class Scene:
    """A fake process tree, a `ps` stub for it and a home directory, in which a test runs bash children."""

    home: Path
    tree: FakeProcessTree
    stub: PsStub

    def run(
        self,
        script: str,
        *,
        hooks_dir: Path = HOOKS_DIR,
        claudecode: bool = False,
        env: dict[str, str | None] | None = None,
        stdin: str | None = None,
        prelude: str = "",
        extra_path: Path | None = None,
        argv: tuple[str, ...] = (),
    ) -> subprocess.CompletedProcess:
        """Sources `hooks_dir`/_lib.sh, then runs `script`. A None env value removes the variable."""
        lib = shlex.quote(str(hooks_dir / "_lib.sh"))
        return subprocess.run(
            ["bash", "-c", f"{prelude} . {lib}; {script}", "scratch", *argv],
            capture_output=True,
            text=True,
            input=stdin,
            env=self._environment(claudecode, env, extra_path),
            timeout=120,
            check=False,
        )

    def run_command(
        self,
        argv: list[str],
        *,
        claudecode: bool = False,
        env: dict[str, str | None] | None = None,
        stdin: str | None = None,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            input=stdin,
            cwd=cwd,
            env=self._environment(claudecode, env, None),
            timeout=120,
            check=False,
        )

    def _environment(
        self, claudecode: bool, env: dict[str, str | None] | None, extra_path: Path | None
    ) -> dict[str, str]:
        environment = {
            name: value
            for name, value in os.environ.items()
            if name not in ("CLAUDECODE", "CLAUDE_CONFIG_DIR", "CLAUDE_PID", "INPUT")
            and not name.startswith("CLAUDE_CONFIG_ADAPTER_")
        }
        environment["HOME"] = str(self.home)
        path_parts = [str(extra_path)] if extra_path else []
        environment["PATH"] = os.pathsep.join([*path_parts, self.stub.env["PATH"]])
        if claudecode:
            environment["CLAUDECODE"] = "1"
        for name, value in (env or {}).items():
            if value is None:
                environment.pop(name, None)
            else:
                environment[name] = value
        return environment

    def normalize(self, text: str, extra_pids: dict[int, str] | None = None) -> str:
        """Replaces each fake ancestor's PID with `@<depth>`, and any `extra_pids` key with its value."""
        replacements = {str(p.pid): f"@{depth}" for depth, p in enumerate(self.tree.processes)}
        replacements.update({str(pid): label for pid, label in (extra_pids or {}).items()})
        pid_alternatives = "|".join(re.escape(pid) for pid in replacements)
        return re.sub(
            rf"({NORMALIZE_FIXED_FIELDS})|(?<!\d)({pid_alternatives})(?!\d)",
            lambda match: match.group(1) or replacements[match.group(2)],
            text,
        )

    def outcome(self, result: subprocess.CompletedProcess, extra_pids: dict[int, str] | None = None) -> str:
        return (
            f"rc={result.returncode} stdout={self.normalize(result.stdout, extra_pids)!r}"
            f" stderr={self.normalize(result.stderr, extra_pids)!r}"
        )

    def state_files(self, directory_name: str) -> list[tuple[str, str]]:
        """(normalized file name, whitespace-collapsed content) of each file in the config dir's
        `directory_name`, sorted."""
        directory = self.home / ".claude" / directory_name
        if not directory.is_dir():
            return []
        return sorted(
            (self.normalize(path.name), self.normalize(" ".join(path.read_text().split())))
            for path in directory.iterdir()
        )


def make_scene(
    tmp_path: Path,
    tree_names: tuple[str, ...] | list[str],
    *,
    rules: tuple[PsRequestRule, ...] = (),
    registered: tuple[tuple[int, str], ...] = (),
    name: str = "scene",
    top_ppid_output: str = "0",
    nearest_pid: int | None = None,
) -> Scene:
    """`registered` pairs a depth with the session id a registry entry names for that ancestor."""
    scene_dir = tmp_path / name
    scene_dir.mkdir()
    home = scene_dir / "home"
    (home / ".claude").mkdir(parents=True)
    tree = build_fake_process_tree(tree_names, top_ppid_output=top_ppid_output, nearest_pid=nearest_pid)
    stub = install_ps_stub(scene_dir, tree, rules)
    for depth, session_id in registered:
        _seed_session(home, session_id, pid=tree.pid_at_depth(depth), start_time=FAKE_LIVE_START_TIME)
    return Scene(home=home, tree=tree, stub=stub)


def payload_text(payload: dict) -> str:
    return json.dumps(payload)


GUARD_SCRIPT = '_lib_worktree_collision_guard "$1" "$2"'


# --- The snapshot corpus ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class SnapshotCase:
    case_id: str
    tree_names: tuple[str, ...]
    script: Callable[[FakeProcessTree], str]
    claudecode: bool
    registered: tuple[tuple[int, str], ...] = ()
    env: tuple[tuple[str, str], ...] = ()
    stdin_payload: dict | None = None
    session_hook: str | None = None
    guard: str | None = None


def _resolve_script(tree: FakeProcessTree) -> str:
    return '_lib_resolve_claude_pid "${INPUT:-}"'


def _resolve_with_input_script(tree: FakeProcessTree) -> str:
    return 'INPUT=$(cat); _lib_resolve_claude_pid "$INPUT"'


def _emit_allow_script(tree: FakeProcessTree) -> str:
    return '_lib_emit_allow_with_context "note"'


def _emit_allow_with_input_script(tree: FakeProcessTree) -> str:
    return 'INPUT=$(cat); _lib_emit_allow_with_context "note"'


def _lock_rule_script(
    locked_pid: Callable[[FakeProcessTree], str],
    locked_session: str,
    caller_pid: Callable[[FakeProcessTree], str],
    caller_session: str,
    fifth_argument: str = "",
) -> Callable[[FakeProcessTree], str]:
    def script(tree: FakeProcessTree) -> str:
        arguments = [locked_pid(tree), locked_session, caller_pid(tree), caller_session]
        if fifth_argument:
            arguments.append(fifth_argument)
        return f"_lib_worktree_lock_is_mine {shlex.join(arguments)}; printf 'rule=%s' $?"

    return script


def _nearest(tree: FakeProcessTree) -> str:
    return str(tree.nearest_pid)


def _dead(tree: FakeProcessTree) -> str:
    return "2999999"


def _resolve_then_pid_first_rule(tree: FakeProcessTree) -> str:
    nearest = tree.nearest_pid
    return (
        '_lib_resolve_claude_pid; printf "|"; '
        f"_lib_worktree_lock_is_mine {nearest} other-session {nearest} {REGISTERED_SESSION_ID}; "
        "printf 'pid-first-rule=%s' $?"
    )


CLAUDE_CASES = (
    SnapshotCase(
        "claude-no-adapter-variables-and-no-registered-ancestor", CLAUDE_AS_PARENT, _resolve_script, claudecode=True
    ),
    SnapshotCase(
        "claude-registered-ancestor-beats-differing-adapter-variables",
        ("claude", "shell"),
        _resolve_script,
        claudecode=True,
        registered=((1, REGISTERED_SESSION_ID),),
        env=tuple(ADAPTER_VARIABLES.items()),
    ),
    SnapshotCase(
        "claude-registered-ancestor-with-session-variable-equal-keeps-pid-first-lock-rule",
        CLAUDE_AS_PARENT,
        _resolve_then_pid_first_rule,
        claudecode=True,
        registered=((0, REGISTERED_SESSION_ID),),
        env=((SESSION_VARIABLE, REGISTERED_SESSION_ID), (HOST_VARIABLE, VALID_HOST_PID)),
    ),
    SnapshotCase(
        "claude-fake-cursor-ancestor-with-no-registry-entry-and-adapter-variables",
        UNDER_CURSOR,
        _resolve_script,
        claudecode=True,
        env=tuple(ADAPTER_VARIABLES.items()),
    ),
    SnapshotCase(
        "claude-fake-agent-ancestor-with-no-registry-entry-and-adapter-variables",
        UNDER_CLI,
        _resolve_script,
        claudecode=True,
        env=tuple(ADAPTER_VARIABLES.items()),
    ),
    SnapshotCase(
        "claude-allow-emitter-keeps-allow-for-predicate-key-payload-under-claude-parent",
        CLAUDE_AS_PARENT,
        _emit_allow_with_input_script,
        claudecode=True,
        stdin_payload=CLAUDE_PAYLOAD_WITH_PREDICATE_KEY,
    ),
    SnapshotCase(
        "claude-allow-emitter-keeps-allow-for-payload-without-predicate-key",
        CLAUDE_AS_PARENT,
        _emit_allow_with_input_script,
        claudecode=True,
        stdin_payload=CLAUDE_CODE_PAYLOAD,
    ),
    SnapshotCase("claude-allow-emitter-with-no-input", CLAUDE_AS_PARENT, _emit_allow_script, claudecode=True),
    SnapshotCase(
        "claude-lock-rule-same-pid-under-another-session-is-mine",
        CLAUDE_AS_PARENT,
        _lock_rule_script(_nearest, "lock-session", _nearest, "caller-session"),
        claudecode=True,
    ),
    SnapshotCase(
        "claude-lock-rule-other-pid-under-another-session-is-not-mine",
        CLAUDE_AS_PARENT,
        _lock_rule_script(_nearest, "lock-session", lambda tree: _OTHER_CALLER_PID, "caller-session"),
        claudecode=True,
    ),
    SnapshotCase(
        "claude-lock-rule-dead-unregistered-holder-under-same-session-is-mine",
        CLAUDE_AS_PARENT,
        _lock_rule_script(_dead, "shared-session", lambda tree: _OTHER_CALLER_PID, "shared-session"),
        claudecode=True,
    ),
    SnapshotCase(
        "claude-lock-rule-live-registered-holder-under-same-session-is-not-mine",
        CLAUDE_AS_PARENT,
        _lock_rule_script(_nearest, "shared-session", lambda tree: _OTHER_CALLER_PID, "shared-session"),
        claudecode=True,
        registered=((0, "another-registered-session"),),
    ),
    SnapshotCase(
        "claude-capture-session-id-writes-predicate-key-payload-under-claude-parent",
        CLAUDE_AS_PARENT,
        _resolve_script,
        claudecode=True,
        stdin_payload=CLAUDE_PAYLOAD_WITH_PREDICATE_KEY,
        session_hook="capture-session-id.sh",
    ),
    SnapshotCase(
        "claude-record-session-end-writes-predicate-key-payload-under-claude-parent",
        CLAUDE_AS_PARENT,
        _resolve_script,
        claudecode=True,
        stdin_payload=CLAUDE_PAYLOAD_WITH_PREDICATE_KEY,
        session_hook="record-session-end.sh",
    ),
    SnapshotCase(
        "claude-guard-acquires-a-fresh-lock-for-its-registered-ancestor",
        CLAUDE_AS_PARENT,
        _resolve_script,
        claudecode=True,
        registered=((0, REGISTERED_SESSION_ID),),
        stdin_payload=CLAUDE_CODE_PAYLOAD,
        guard="fresh",
    ),
    SnapshotCase(
        "claude-guard-denies-a-write-into-a-worktree-another-live-session-holds",
        CLAUDE_AS_PARENT,
        _resolve_script,
        claudecode=True,
        registered=((0, REGISTERED_SESSION_ID),),
        stdin_payload=CLAUDE_CODE_PAYLOAD,
        guard="held",
    ),
)

CURSOR_CASES = (
    SnapshotCase(
        "cursor-fake-cursor-host-with-claudecode-unset-resolves-the-adapter-pair",
        UNDER_CURSOR,
        _resolve_script,
        claudecode=False,
        env=tuple(ADAPTER_VARIABLES.items()),
    ),
    SnapshotCase(
        "cursor-fake-cli-host-with-claudecode-unset-resolves-the-adapter-pair",
        UNDER_CLI,
        _resolve_script,
        claudecode=False,
        env=tuple(ADAPTER_VARIABLES.items()),
    ),
    SnapshotCase(
        "cursor-untranslated-payload-with-no-registered-ancestor-names-its-conversation",
        UNDER_CURSOR,
        _resolve_with_input_script,
        claudecode=False,
        stdin_payload=CURSOR_PAYLOAD,
    ),
    SnapshotCase(
        "cursor-allow-emitter-prints-nothing-for-untranslated-payload-under-cursor-host",
        UNDER_CURSOR,
        _emit_allow_with_input_script,
        claudecode=False,
        stdin_payload=CURSOR_PAYLOAD,
    ),
    SnapshotCase(
        "cursor-lock-rule-session-keyed-same-pid-under-another-session-is-not-mine",
        CLAUDE_AS_PARENT,
        _lock_rule_script(_nearest, "lock-session", _nearest, "caller-session", "session-keyed"),
        claudecode=False,
    ),
    SnapshotCase(
        "cursor-lock-rule-session-keyed-old-format-lock-is-not-mine",
        CLAUDE_AS_PARENT,
        _lock_rule_script(_nearest, "", _nearest, "caller-session", "session-keyed"),
        claudecode=False,
    ),
    SnapshotCase(
        "cursor-lock-rule-session-keyed-same-pid-with-relative-config-dir-is-not-mine",
        CLAUDE_AS_PARENT,
        _lock_rule_script(_nearest, "lock-session", _nearest, "caller-session", "session-keyed"),
        claudecode=False,
        env=(("CLAUDE_CONFIG_DIR", "relative/config-dir"),),
    ),
    SnapshotCase(
        "cursor-capture-session-id-declines-untranslated-payload-under-cursor-host",
        ("Cursor",),
        _resolve_script,
        claudecode=False,
        stdin_payload=CURSOR_PAYLOAD,
        session_hook="capture-session-id.sh",
    ),
    SnapshotCase(
        "cursor-record-session-end-declines-untranslated-payload-under-cursor-host",
        ("Cursor",),
        _resolve_script,
        claudecode=False,
        stdin_payload=CURSOR_PAYLOAD,
        session_hook="record-session-end.sh",
    ),
)

SNAPSHOT_CASES = CLAUDE_CASES + CURSOR_CASES
EXPECTED_DIFFERENT_CASE_IDS = frozenset(case.case_id for case in CURSOR_CASES)


def run_snapshot_case(case: SnapshotCase, hooks_dir: Path, tmp_path: Path) -> str:
    """Runs one case against `hooks_dir`, and returns its outcome with PIDs normalized to ancestor depth."""
    scene = make_scene(tmp_path, case.tree_names, registered=case.registered)
    stdin = payload_text(case.stdin_payload) if case.stdin_payload is not None else None
    env = dict(case.env)
    if case.session_hook:
        directory_name, _ = SESSION_HOOKS[case.session_hook]
        result = scene.run_command(
            ["bash", str(hooks_dir / case.session_hook)], claudecode=case.claudecode, env=env, stdin=stdin
        )
        return f"{scene.outcome(result)} state={scene.state_files(directory_name)}"
    if case.guard:
        return _run_guard_case(case, scene, hooks_dir, tmp_path, env, stdin)
    result = scene.run(case.script(scene.tree), hooks_dir=hooks_dir, claudecode=case.claudecode, env=env, stdin=stdin)
    return scene.outcome(result)


def _run_guard_case(
    case: SnapshotCase, scene: Scene, hooks_dir: Path, tmp_path: Path, env: dict[str, str], stdin: str | None
) -> str:
    worktree, common_dir = make_linked_worktree(tmp_path)
    guard_env = {**env, "INPUT": stdin}
    guard_argv = (str(worktree), common_dir)
    if case.guard == "fresh":
        result = scene.run(GUARD_SCRIPT, hooks_dir=hooks_dir, claudecode=case.claudecode, env=guard_env, argv=guard_argv)
        return f"{scene.outcome(result)} lock={scene.normalize(worktree_lock_text(worktree))!r}"
    with live_process() as holder_pid:
        holder = {holder_pid: "<holder>"}
        Path(_git_dir(worktree), "locked").write_text(f"claude-code pid {holder_pid} session another-live-session\n")
        result = scene.run(GUARD_SCRIPT, hooks_dir=hooks_dir, claudecode=case.claudecode, env=guard_env, argv=guard_argv)
        return f"{scene.outcome(result, holder)} lock={scene.normalize(worktree_lock_text(worktree), holder)!r}"


# The `claude-` entries come from claude/.claude/hooks/_lib.sh and the two session hooks at commit 59c409a0.
# The `cursor-` entries hold the adapter's results.
SNAPSHOT: dict[str, str] = {
    'claude-no-adapter-variables-and-no-registered-ancestor':
        "rc=2 stdout='' stderr=''",
    'claude-registered-ancestor-beats-differing-adapter-variables':
        "rc=0 stdout='registered-session @1' stderr=''",
    'claude-registered-ancestor-with-session-variable-equal-keeps-pid-first-lock-rule':
        "rc=0 stdout='registered-session @0|pid-first-rule=0' stderr=''",
    'claude-fake-cursor-ancestor-with-no-registry-entry-and-adapter-variables':
        "rc=2 stdout='' stderr=''",
    'claude-fake-agent-ancestor-with-no-registry-entry-and-adapter-variables':
        "rc=2 stdout='' stderr=''",
    'claude-allow-emitter-keeps-allow-for-predicate-key-payload-under-claude-parent':
        (
            'rc=0 stdout=\'{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDeci'
            'sion":"allow","additionalContext":"note"}}\\n\' stderr=\'\''
        ),
    'claude-allow-emitter-keeps-allow-for-payload-without-predicate-key':
        (
            'rc=0 stdout=\'{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDeci'
            'sion":"allow","additionalContext":"note"}}\\n\' stderr=\'\''
        ),
    'claude-allow-emitter-with-no-input':
        (
            'rc=0 stdout=\'{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDeci'
            'sion":"allow","additionalContext":"note"}}\\n\' stderr=\'\''
        ),
    'claude-lock-rule-same-pid-under-another-session-is-mine':
        "rc=0 stdout='rule=0' stderr=''",
    'claude-lock-rule-other-pid-under-another-session-is-not-mine':
        "rc=0 stdout='rule=1' stderr=''",
    'claude-lock-rule-dead-unregistered-holder-under-same-session-is-mine':
        "rc=0 stdout='rule=0' stderr=''",
    'claude-lock-rule-live-registered-holder-under-same-session-is-not-mine':
        "rc=0 stdout='rule=1' stderr=''",
    'claude-capture-session-id-writes-predicate-key-payload-under-claude-parent':
        "rc=0 stdout='' stderr='' state=[('@0', 'claude-code-session Tue Feb 2 03:04:05 2021')]",
    'claude-record-session-end-writes-predicate-key-payload-under-claude-parent':
        (
            'rc=0 stdout=\'\' stderr=\'\' state=[(\'@0\', \'{ "sessionId": "claude-code-session", "r'
            'eason": null }\')]'
        ),
    'claude-guard-acquires-a-fresh-lock-for-its-registered-ancestor':
        "rc=0 stdout='' stderr='' lock='claude-code pid @0 session registered-session'",
    'claude-guard-denies-a-write-into-a-worktree-another-live-session-holds':
        (
            "rc=1 stdout='this worktree is already in use by a live Claude Code session (pid "
            "<holder>) — wait for it to finish, or work in a different worktree' stderr='' lo"
            "ck='claude-code pid <holder> session another-live-session'"
        ),
    'cursor-fake-cursor-host-with-claudecode-unset-resolves-the-adapter-pair':
        "rc=0 stdout='adapter-session-id @1' stderr=''",
    'cursor-fake-cli-host-with-claudecode-unset-resolves-the-adapter-pair':
        "rc=0 stdout='adapter-session-id @1' stderr=''",
    'cursor-untranslated-payload-with-no-registered-ancestor-names-its-conversation':
        "rc=0 stdout='conv-0000-4000-8000-01 @1' stderr=''",
    'cursor-allow-emitter-prints-nothing-for-untranslated-payload-under-cursor-host':
        "rc=0 stdout='' stderr=''",
    'cursor-lock-rule-session-keyed-same-pid-under-another-session-is-not-mine':
        "rc=0 stdout='rule=1' stderr=''",
    'cursor-lock-rule-session-keyed-old-format-lock-is-not-mine':
        "rc=0 stdout='rule=1' stderr=''",
    'cursor-lock-rule-session-keyed-same-pid-with-relative-config-dir-is-not-mine':
        "rc=0 stdout='rule=1' stderr=''",
    'cursor-capture-session-id-declines-untranslated-payload-under-cursor-host':
        (
            "rc=0 stdout='' stderr='[capture-session-id] untranslated Cursor payload declined; "
            "lookup file not written\\n' state=[]"
        ),
    'cursor-record-session-end-declines-untranslated-payload-under-cursor-host':
        (
            "rc=0 stdout='' stderr='[record-session-end] untranslated Cursor payload declined; "
            "no SessionEnd record written\\n' state=[]"
        ),
}


@pytest.fixture
def hooks_dir_under_snapshot() -> Path:
    override = os.environ.get(SNAPSHOT_HOOKS_DIR_ENV)
    return Path(override) if override else HOOKS_DIR


class TestSceneNormalize:
    @pytest.mark.parametrize("worker_pid", [4000, 8000, 2021])
    def test_a_fake_pid_equal_to_a_digit_run_in_a_fixed_literal_leaves_the_literal_unchanged(
        self, tmp_path, worker_pid
    ):
        scene = make_scene(tmp_path, ("Cursor",), nearest_pid=worker_pid)
        collapsed_start_time = " ".join(FAKE_LIVE_START_TIME.split())
        fixed_fields = f"{CONVERSATION_ID} {PARENT_CONVERSATION_ID} {FAKE_LIVE_START_TIME} {collapsed_start_time}"
        assert str(worker_pid) in fixed_fields, "the row no longer collides with a fixed literal, so it guards nothing"

        assert scene.normalize(f"{fixed_fields} pid {worker_pid}") == f"{fixed_fields} pid @0"


class TestClaudePathSnapshot:
    @pytest.mark.parametrize("case", SNAPSHOT_CASES, ids=lambda case: case.case_id)
    def test_case_matches_the_snapshot(self, case, hooks_dir_under_snapshot, tmp_path):
        assert run_snapshot_case(case, hooks_dir_under_snapshot, tmp_path) == SNAPSHOT[case.case_id]

    def test_the_snapshot_holds_exactly_one_entry_per_case(self):
        assert set(SNAPSHOT) == {case.case_id for case in SNAPSHOT_CASES}
        assert len(SNAPSHOT_CASES) == len({case.case_id for case in SNAPSHOT_CASES})

    def test_every_case_is_either_a_claude_case_or_a_listed_expected_different_case(self):
        claude_ids = {case.case_id for case in CLAUDE_CASES}
        assert all(case_id.startswith("claude-") for case_id in claude_ids)
        assert all(case_id.startswith("cursor-") for case_id in EXPECTED_DIFFERENT_CASE_IDS)
        assert claude_ids.isdisjoint(EXPECTED_DIFFERENT_CASE_IDS)

    def test_every_claude_case_runs_with_claudecode_set(self):
        assert all(case.claudecode for case in CLAUDE_CASES)

    @pytest.mark.parametrize(
        "script",
        [
            "_lib_payload_is_untranslated_cursor",
            "_lib_cursor_host_pid >/dev/null || true",
            "_lib_adapter_session_pair || true",
            "_lib_resolve_claude_pid || true",
            "_lib_worktree_lock_is_mine 1 a 2 b || true",
            "_lib_emit_allow_with_context note",
        ],
        ids=lambda script: script.split(" ")[0],
    )
    def test_touched_function_runs_under_set_u_with_its_inputs_unset(self, script, tmp_path):
        scene = make_scene(tmp_path, UNDER_CURSOR)
        result = scene.run(f"{script}; true", prelude="set -u;")
        assert "unbound variable" not in result.stderr, result.stderr

    def test_the_collision_guard_runs_under_set_u_with_its_inputs_unset(self, tmp_path):
        scene = make_scene(tmp_path, CLAUDE_AS_PARENT, registered=((0, REGISTERED_SESSION_ID),))
        worktree, common_dir = make_linked_worktree(tmp_path)
        result = scene.run(GUARD_SCRIPT, prelude="set -u;", argv=(str(worktree), common_dir))
        assert result.returncode == 0, result
        assert "unbound variable" not in result.stderr, result.stderr


# --- Identity under the adapter ---------------------------------------------------------------------------

PAIR_SCRIPT = '_lib_adapter_session_pair "${PAYLOAD_UNDER_TEST:-}"'
WALK_SCRIPT = "_lib_cursor_host_pid"
FAIL_EVERY_READ = (PsRequestRule("*", "*", "fail"),)
# Each way the host walk finds no host: (tree names, ps rules, the walk's status).
UNRESOLVED_HOST_ROWS = {
    "status-2-host-name-not-recognised": (("shell",), (), 2),
    "status-3-walk-stops-at-claude": (CLAUDE_AS_PARENT, (), 3),
    "status-4-a-name-read-fails": (("Cursor",), (PsRequestRule("comm", "*", "fail"),), 4),
}
INVALID_85_CHARACTER_ID = "x" * 84 + "/"


def run_pair(
    scene: Scene,
    *,
    payload: dict | None = None,
    claudecode: bool = False,
    env: dict[str, str | None] | None = None,
) -> subprocess.CompletedProcess:
    environment = dict(ADAPTER_VARIABLES if env is None else env)
    if payload is not None:
        environment["PAYLOAD_UNDER_TEST"] = payload_text(payload)
    return scene.run(PAIR_SCRIPT, claudecode=claudecode, env=environment)


def function_source(scene: Scene, function_name: str) -> str:
    result = scene.run(f"declare -f {function_name}")
    assert result.returncode == 0, result
    return result.stdout


def bare_capture_control(scene: Scene, function_name: str, capture_pattern: str) -> str:
    """The function's source with the status capture matched by `capture_pattern` removed, so that capture is a
    bare `var=$(...)`. The pattern's one group is the text to keep."""
    control, replaced = re.subn(capture_pattern, r"\1", function_source(scene, function_name))
    assert replaced == 1, f"the control for {function_name} must change exactly one capture, changed {replaced}"
    return control


def run_plain_call_under_errexit(scene: Scene, call: str, tmp_path: Path, control_source: str | None = None):
    """Runs `call` plainly, under `set -euo pipefail`, in a child bash with its stdout sent to a file. The child
    prints `reached` on the next line when `call` returned."""
    stdout_file = tmp_path / "plain-call-stdout"
    definition = 'eval "$CONTROL_SOURCE"; ' if control_source else ""
    environment: dict[str, str | None] = {"CONTROL_SOURCE": control_source}
    return scene.run(
        f'{definition}{call} > {shlex.quote(str(stdout_file))}; echo reached',
        prelude="set -euo pipefail;",
        env={**ADAPTER_VARIABLES, **environment},
    )


class TestAdapterIdentity:
    def test_payload_session_id_beats_a_different_grammar_valid_session_variable(self, tmp_path):
        scene = make_scene(tmp_path, UNDER_CURSOR)
        result = run_pair(scene, payload=CURSOR_PAYLOAD)
        assert (result.returncode, result.stdout) == (0, f"{CONVERSATION_ID} {scene.tree.pid_at_depth(1)}")

    def test_without_a_payload_the_pair_resolves_the_session_variable_the_caller_set(self, tmp_path):
        """Pins the accepted residual: a script-side caller under a Cursor host that sets the variable takes that id."""
        scene = make_scene(tmp_path, UNDER_CURSOR)
        result = run_pair(scene)
        assert (result.returncode, result.stdout) == (0, f"{ADAPTER_SESSION_ID} {scene.tree.pid_at_depth(1)}")

    def test_a_payload_without_predicate_keys_resolves_the_session_variable(self, tmp_path):
        scene = make_scene(tmp_path, UNDER_CURSOR)
        result = run_pair(scene, payload=CLAUDE_CODE_PAYLOAD)
        assert (result.returncode, result.stdout) == (0, f"{ADAPTER_SESSION_ID} {scene.tree.pid_at_depth(1)}")

    @pytest.mark.parametrize(
        ("row_id", "parent_agent_id", "expected_session_id"),
        [
            ("child-takes-its-parents-id", PARENT_CONVERSATION_ID, PARENT_CONVERSATION_ID),
            ("null-is-not-carried", None, CONVERSATION_ID),
            ("empty-is-not-carried", "", CONVERSATION_ID),
        ],
    )
    def test_parent_agent_id_names_the_call_session_only_when_carried(
        self, tmp_path, row_id, parent_agent_id, expected_session_id
    ):
        scene = make_scene(tmp_path, UNDER_CURSOR)
        result = run_pair(scene, payload={**CURSOR_PAYLOAD, "parent_agent_id": parent_agent_id})
        assert (result.returncode, result.stdout) == (0, f"{expected_session_id} {scene.tree.pid_at_depth(1)}"), row_id

    @pytest.mark.parametrize(
        "parent_agent_id",
        [12345, False, INVALID_85_CHARACTER_ID, "valid-id\n", "../x", ["a"]],
        ids=["number", "false", "85-characters", "trailing-newline", "traversal", "array"],
    )
    def test_a_carried_parent_agent_id_that_fails_the_grammar_is_not_replaced_by_the_conversation_id(
        self, tmp_path, parent_agent_id
    ):
        scene = make_scene(tmp_path, UNDER_CURSOR)
        result = run_pair(scene, payload={**CURSOR_PAYLOAD, "parent_agent_id": parent_agent_id})
        assert (result.returncode, result.stdout) == (2, "")

    @pytest.mark.parametrize(
        "conversation_id",
        [None, 12345, "../x", INVALID_85_CHARACTER_ID],
        ids=["null", "number", "traversal", "85-characters"],
    )
    def test_an_untranslated_payload_whose_conversation_id_is_invalid_is_refused_not_given_the_session_variable(
        self, tmp_path, conversation_id
    ):
        """The session variable is valid, and the payload carries no `parent_agent_id`."""
        scene = make_scene(tmp_path, UNDER_CURSOR)
        result = run_pair(scene, payload={**CURSOR_PAYLOAD, "conversation_id": conversation_id})
        assert (result.returncode, result.stdout) == (2, "")

    def test_an_untranslated_payload_with_no_conversation_id_is_refused_not_given_the_session_variable(self, tmp_path):
        scene = make_scene(tmp_path, UNDER_CURSOR)
        payload_without_conversation_id = {
            name: value for name, value in CURSOR_PAYLOAD.items() if name != "conversation_id"
        }
        result = run_pair(scene, payload=payload_without_conversation_id)
        assert (result.returncode, result.stdout) == (2, "")

    @pytest.mark.parametrize("claudecode_tree", [("Cursor",), ("agent",)], ids=["cursor-host", "cli-host"])
    def test_a_cursor_payload_under_claudecode_is_refused_by_the_pair_though_the_host_is_healthy(
        self, tmp_path, claudecode_tree
    ):
        """No registry entry and a healthy `ps`: the refusal is CLAUDECODE's, not a failed walk's."""
        scene = make_scene(tmp_path, claudecode_tree)
        result = run_pair(scene, payload=CURSOR_PAYLOAD, claudecode=True)
        assert (result.returncode, result.stdout) == (2, "")

    @pytest.mark.parametrize(
        "options",
        [
            pytest.param({"payload": {**CURSOR_PAYLOAD, "conversation_id": "../x"}}, id="invalid-payload-id"),
            pytest.param({"env": {HOST_VARIABLE: VALID_HOST_PID}}, id="session-variable-unset"),
            pytest.param({"env": {SESSION_VARIABLE: "../x", HOST_VARIABLE: VALID_HOST_PID}}, id="session-variable-traversal"),
        ],
    )
    def test_a_caller_with_no_valid_session_id_is_refused_before_any_ps_read(self, tmp_path, options):
        scene = make_scene(tmp_path, UNDER_CURSOR)
        result = run_pair(scene, **options)
        assert (result.returncode, result.stdout) == (2, "")
        assert scene.stub.requests() == []

    def test_the_host_variable_is_taken_when_the_first_ps_read_fails(self, tmp_path):
        """The one passing configuration for the host variable. It pins the widening that variable brings: a caller
        that sets both variables takes that PID with no ancestor tie."""
        scene = make_scene(tmp_path, UNDER_CURSOR, rules=FAIL_EVERY_READ)
        result = run_pair(scene)
        assert (result.returncode, result.stdout) == (0, f"{ADAPTER_SESSION_ID} {VALID_HOST_PID}")
        assert scene.stub.requests() == [("comm", str(scene.tree.nearest_pid), "fail")]

    @pytest.mark.parametrize(
        ("tree_names", "rules", "options"),
        [
            pytest.param(("shell",), (), {}, id="ps-healthy-over-a-tree-with-no-host"),
            pytest.param(("claude",), (), {}, id="walk-stops-at-claude"),
            pytest.param(UNDER_CURSOR, FAIL_EVERY_READ, {"payload": CURSOR_PAYLOAD}, id="payload-given"),
            pytest.param(UNDER_CURSOR, FAIL_EVERY_READ, {"claudecode": True}, id="claudecode-set"),
            pytest.param(UNDER_CURSOR, FAIL_EVERY_READ, {"env": {HOST_VARIABLE: VALID_HOST_PID}}, id="session-variable-unset"),
            pytest.param(
                UNDER_CURSOR,
                FAIL_EVERY_READ,
                {"env": {SESSION_VARIABLE: "../x", HOST_VARIABLE: VALID_HOST_PID}},
                id="session-variable-traversal",
            ),
            pytest.param(
                UNDER_CURSOR,
                FAIL_EVERY_READ,
                {"env": {SESSION_VARIABLE: INVALID_85_CHARACTER_ID, HOST_VARIABLE: VALID_HOST_PID}},
                id="session-variable-85-characters",
            ),
        ],
    )
    def test_each_input_flip_from_the_passing_configuration_is_refused(self, tmp_path, tree_names, rules, options):
        scene = make_scene(tmp_path, tree_names, rules=rules)
        result = run_pair(scene, **options)
        assert (result.returncode, result.stdout) == (2, "")

    @pytest.mark.parametrize("row", PID_GRAMMAR_EDGE_TABLE, ids=lambda row: row.row_id)
    def test_the_host_variable_is_taken_only_when_it_fits_the_pid_grammar(self, tmp_path, row):
        scene = make_scene(tmp_path, UNDER_CURSOR, rules=FAIL_EVERY_READ)
        result = run_pair(scene, env={SESSION_VARIABLE: ADAPTER_SESSION_ID, HOST_VARIABLE: row.value})
        expected = (0, f"{ADAPTER_SESSION_ID} {row.value}") if row.accepted else (2, "")
        assert (result.returncode, result.stdout) == expected

    @pytest.mark.parametrize(
        ("read_kind", "capture_pattern"),
        [
            ("comm", r'(ps -o comm= -p "\$pid" 2>\s*/dev/null\)) \|\| read_status=\$\?'),
            ("ppid", r'(ps -o ppid= -p "\$pid" 2>\s*/dev/null\)) \|\| read_status=\$\?'),
        ],
    )
    def test_the_walk_returns_four_to_a_plain_call_under_errexit_where_a_bare_capture_dies_with_one(
        self, tmp_path, read_kind, capture_pattern
    ):
        """Guards `-e` live inside the walk. The control is the walk's own source with the failing read bare."""
        rules = (PsRequestRule(read_kind, "*", "fail"),)
        scene = make_scene(tmp_path, ("shell",), rules=rules)
        control = bare_capture_control(scene, "_lib_cursor_host_pid", capture_pattern)

        real = run_plain_call_under_errexit(scene, WALK_SCRIPT, tmp_path)
        bare = run_plain_call_under_errexit(scene, WALK_SCRIPT, tmp_path, control_source=control)

        assert (real.returncode, real.stdout) == (4, "")
        assert (bare.returncode, bare.stdout) == (1, "")

    def test_the_pair_returns_zero_to_a_plain_call_under_errexit_where_a_bare_walk_capture_exits_four(self, tmp_path):
        scene = make_scene(tmp_path, UNDER_CURSOR, rules=FAIL_EVERY_READ)
        control = bare_capture_control(
            scene, "_lib_adapter_session_pair", r"(host_pid=\$\(_lib_cursor_host_pid\)) \|\| host_status=\$\?"
        )

        real = run_plain_call_under_errexit(scene, "_lib_adapter_session_pair", tmp_path)
        bare = run_plain_call_under_errexit(scene, "_lib_adapter_session_pair", tmp_path, control_source=control)

        assert (real.returncode, real.stdout) == (0, "reached\n")
        assert (bare.returncode, bare.stdout) == (4, "")

    def test_under_a_healthy_cursor_host_the_pair_prints_the_walks_pid_not_the_variables(self, tmp_path):
        scene = make_scene(tmp_path, UNDER_CURSOR)
        result = run_pair(scene)
        assert result.stdout == f"{ADAPTER_SESSION_ID} {scene.tree.pid_at_depth(1)}"
        assert VALID_HOST_PID not in result.stdout

    def test_the_collision_guard_stays_on_its_no_identity_path_when_ps_fails_and_the_host_variable_is_valid(
        self, tmp_path
    ):
        scene = make_scene(tmp_path, UNDER_CURSOR, rules=FAIL_EVERY_READ)
        worktree, common_dir = make_linked_worktree(tmp_path)
        result = scene.run(
            GUARD_SCRIPT,
            env={**ADAPTER_VARIABLES, "INPUT": payload_text(CURSOR_PAYLOAD)},
            argv=(str(worktree), common_dir),
        )
        assert (result.returncode, result.stdout) == (
            1,
            "could not resolve this session's own process identity to check the worktree lock",
        )
        assert worktree_lock_text(worktree) == ""

    def test_the_allow_emitter_prints_no_allow_when_ps_fails_and_the_host_variable_is_valid(self, tmp_path):
        scene = make_scene(tmp_path, UNDER_CURSOR, rules=FAIL_EVERY_READ)
        result = scene.run(
            '_lib_emit_allow_with_context "note"', env={**ADAPTER_VARIABLES, "INPUT": payload_text(CURSOR_PAYLOAD)}
        )
        assert (result.returncode, result.stdout) == (0, "")

    @pytest.mark.parametrize("hook_name", SESSION_HOOKS)
    def test_a_session_hook_declines_when_ps_fails_and_the_host_variable_is_valid(self, tmp_path, hook_name):
        scene = make_scene(tmp_path, ("Cursor",), rules=FAIL_EVERY_READ)
        directory_name, _ = SESSION_HOOKS[hook_name]
        result = scene.run_command(
            ["bash", str(HOOKS_DIR / hook_name)], env=ADAPTER_VARIABLES, stdin=payload_text(CURSOR_PAYLOAD)
        )
        assert SESSION_HOOK_DECLINE_FRAGMENT in result.stderr
        assert scene.state_files(directory_name) == []

    @pytest.mark.parametrize("failing_walk", [False, True], ids=["healthy-walk", "failing-first-read"])
    def test_handoff_record_conversion_resolves_the_session_variable_under_its_own_errexit(self, tmp_path, failing_walk):
        scene = make_scene(tmp_path, UNDER_CURSOR, rules=FAIL_EVERY_READ if failing_walk else ())
        result = scene.run_command(
            ["bash", str(SCRIPTS_DIR / "handoff-record-conversion.sh")], env=ADAPTER_VARIABLES
        )
        assert result.returncode == 0, result
        assert (scene.home / ".claude" / ".handoff-nudge.log").read_text() == f"handoff session={ADAPTER_SESSION_ID}\n"


# --- Consumers --------------------------------------------------------------------------------------------


def _run_marker_resolve(scene: Scene, claudecode: bool, tmp_path: Path) -> bool:
    result = scene.run_command(
        ["bash", str(SCRIPTS_DIR / "marker.sh"), "resolve-session-id"],
        claudecode=claudecode,
        env=ADAPTER_VARIABLES,
    )
    return result.returncode == 0 and result.stdout == ADAPTER_SESSION_ID


def _run_review_ledger_show(scene: Scene, claudecode: bool, tmp_path: Path) -> bool:
    """Appends one row, then shows it. The row's `session_id` is what the ledger resolved as the caller."""
    repo = init_git_repo_with_commit(tmp_path / "ledger-repo", branch="main")

    def run_ledger(arguments: list[str]) -> subprocess.CompletedProcess:
        return scene.run_command(
            ["bash", str(SCRIPTS_DIR / "review-ledger.sh"), *arguments],
            claudecode=claudecode,
            env=ADAPTER_VARIABLES,
            cwd=repo,
        )

    appended = run_ledger(_append_args(source="foo.py:12"))
    shown = run_ledger(["show"])
    if appended.returncode != 0 or shown.returncode != 0:
        return False
    return [json.loads(line)["session_id"] for line in shown.stdout.splitlines()] == [ADAPTER_SESSION_ID]


def _run_review_pr_lib_resolver(scene: Scene, claudecode: bool, tmp_path: Path) -> bool:
    script = (
        f". {shlex.quote(str(SCRIPTS_DIR / '_review-pr-lib.sh'))}; "
        "review_pr_resolve_session_and_pid scratch '' 'aborted' || exit 1; printf '%s %s' \"$SESSION_ID\" \"$CLAUDE_PID\""
    )
    result = scene.run(script, claudecode=claudecode, env=ADAPTER_VARIABLES)
    return result.returncode == 0 and result.stdout == f"{ADAPTER_SESSION_ID} {scene.tree.pid_at_depth(1)}"


def _run_handoff_record_conversion(scene: Scene, claudecode: bool, tmp_path: Path) -> bool:
    scene.run_command(
        ["bash", str(SCRIPTS_DIR / "handoff-record-conversion.sh")], claudecode=claudecode, env=ADAPTER_VARIABLES
    )
    log = scene.home / ".claude" / ".handoff-nudge.log"
    return log.exists() and log.read_text() == f"handoff session={ADAPTER_SESSION_ID}\n"


def _run_resolve_session_id(scene: Scene, claudecode: bool, tmp_path: Path) -> bool:
    """pr-diff-against-base.sh reaches identity through `_lib_resolve_session_id` (its --record and --write-diff-file)."""
    result = scene.run("_lib_resolve_session_id", claudecode=claudecode, env=ADAPTER_VARIABLES)
    return result.returncode == 0 and result.stdout == ADAPTER_SESSION_ID


CONSUMERS = {
    "marker.sh": _run_marker_resolve,
    "review-ledger.sh": _run_review_ledger_show,
    "_review-pr-lib.sh": _run_review_pr_lib_resolver,
    "handoff-record-conversion.sh": _run_handoff_record_conversion,
    "_lib_resolve_session_id": _run_resolve_session_id,
}


class TestIdentityConsumers:
    @pytest.mark.parametrize("consumer", CONSUMERS)
    @pytest.mark.parametrize("tree_names", [UNDER_CURSOR, UNDER_CLI], ids=["cursor-host", "cli-host"])
    def test_consumer_resolves_the_adapter_session_under_a_fake_host(self, tmp_path, consumer, tree_names):
        scene = make_scene(tmp_path, tree_names)
        assert CONSUMERS[consumer](scene, False, tmp_path)

    @pytest.mark.parametrize("consumer", CONSUMERS)
    def test_consumer_resolves_nothing_under_claude_code_with_adapter_variables_set(self, tmp_path, consumer):
        scene = make_scene(tmp_path, UNDER_CURSOR)
        assert not CONSUMERS[consumer](scene, True, tmp_path)

    def test_an_exported_cursor_input_does_not_change_the_identity_marker_script_resolves(self, tmp_path):
        """A script resolves with no payload, so an `INPUT` it inherits never reaches the pair."""
        scene = make_scene(tmp_path, UNDER_CURSOR)
        result = scene.run_command(
            ["bash", str(SCRIPTS_DIR / "marker.sh"), "resolve-session-id"],
            env={**ADAPTER_VARIABLES, "INPUT": payload_text(CURSOR_PAYLOAD)},
        )
        assert (result.returncode, result.stdout) == (0, ADAPTER_SESSION_ID)

    def test_a_live_registered_ancestor_wins_over_the_adapter_variables_under_a_cursor_host(self, tmp_path):
        scene = make_scene(tmp_path, ("Cursor", "claude", "shell"), registered=((1, REGISTERED_SESSION_ID),))
        result = scene.run(_resolve_script(scene.tree), env=ADAPTER_VARIABLES)
        assert result.stdout == f"{REGISTERED_SESSION_ID} {scene.tree.pid_at_depth(1)}"

    @pytest.mark.parametrize("decoy", ["start-time-mismatch", "one-line-entry"])
    def test_a_registry_decoy_is_rejected_so_the_adapter_pair_resolves(self, tmp_path, decoy):
        """Rejection rows: a wrong start time (PID reuse) and an entry with no start time never resolve the
        decoy's session."""
        scene = make_scene(tmp_path, UNDER_CURSOR)
        sessions = scene.home / ".claude" / "sessions"
        sessions.mkdir()
        entries = {
            "start-time-mismatch": f"decoy-session\n{_FIXED_START_TIME}\n",
            "one-line-entry": "decoy-session\n",
        }
        (sessions / str(scene.tree.nearest_pid)).write_text(entries[decoy])

        result = scene.run(_resolve_script(scene.tree), env=ADAPTER_VARIABLES)

        assert result.stdout == f"{ADAPTER_SESSION_ID} {scene.tree.pid_at_depth(1)}"

    @pytest.mark.parametrize("session_id", ["../escape", "a/b"], ids=["traversal", "slash"])
    def test_marker_script_rejects_a_registered_session_id_that_fails_the_grammar(self, tmp_path, session_id):
        scene = make_scene(tmp_path, UNDER_CURSOR, registered=((0, session_id),))
        result = scene.run_command(["bash", str(SCRIPTS_DIR / "marker.sh"), "resolve-session-id"], env=ADAPTER_VARIABLES)
        assert (result.returncode, result.stdout) == (2, "")
        assert "is not a valid path component" in result.stderr

    def test_the_active_bypass_marker_written_through_marker_script_is_read_back_live_and_kept_by_clear_stale(
        self, tmp_path
    ):
        """Write and read sides agree: `activate` records the host PID, `_lib_active_bypass_marker_live` reads it
        live, and the sweep keeps it. The host is this test process, so its PID is live."""
        scene = make_scene(tmp_path, ("Cursor",))
        marker = scene.home / ".claude" / ".respond-pr-active.d" / ADAPTER_SESSION_ID
        marker_script = str(SCRIPTS_DIR / "marker.sh")

        activated = scene.run_command(["bash", marker_script, "activate", "respond-pr"], env=ADAPTER_VARIABLES)
        read_back = scene.run(f'_lib_active_bypass_marker_live .respond-pr-active.d {ADAPTER_SESSION_ID}')
        swept = scene.run_command(["bash", marker_script, "clear-stale"], env=ADAPTER_VARIABLES)

        assert activated.returncode == 0, activated
        assert marker.read_text().strip() == str(scene.tree.nearest_pid)
        assert read_back.returncode == 0
        assert swept.returncode == 0, swept
        assert marker.exists()

    def test_marker_script_activate_refuses_under_claude_code_with_adapter_variables_set(self, tmp_path):
        scene = make_scene(tmp_path, ("Cursor",))
        result = scene.run_command(
            ["bash", str(SCRIPTS_DIR / "marker.sh"), "activate", "respond-pr"], claudecode=True, env=ADAPTER_VARIABLES
        )
        assert result.returncode == 2
        assert not (scene.home / ".claude" / ".respond-pr-active.d").exists()


# --- The worktree lock rule keyed by session ---------------------------------------------------------------

OWN_PID = str(os.getpid())


def run_lock_rule(
    scene: Scene,
    arguments: tuple[str, ...],
    *,
    keying: str | None = None,
    config_dir_env: str | None = None,
    extra_env: dict | None = None,
    failing_ps_marker: Path | None = None,
) -> subprocess.CompletedProcess:
    """Runs _lib_worktree_lock_is_mine under `set -eu` as test_lib_worktree_collision_guard.py's `_run_lock_is_mine`
    does, with `scene`'s home. That helper takes four arguments only, so a call with a fifth argument `keying`
    runs through the scene. `failing_ps_marker` is the file a `_install_failing_ps` stub touches, which must exist
    afterwards so the row is not vacuous."""
    if keying is None:
        result = _run_lock_is_mine(scene.home, *arguments, config_dir_env=config_dir_env, extra_env=extra_env)
    else:
        # The rows register real live processes, which the scene's stub `ps` cannot answer for.
        environment: dict[str, str | None] = {"PATH": os.environ["PATH"], **(extra_env or {})}
        if config_dir_env is not None:
            environment["CLAUDE_CONFIG_DIR"] = config_dir_env
        call = shlex.join(["_lib_worktree_lock_is_mine", *arguments, keying])
        result = scene.run(f'{call} && rc=0 || rc=$?; exit "$rc"', prelude="set -eu;", env=environment)
    if failing_ps_marker is not None:
        assert failing_ps_marker.exists(), "the failing ps stub never ran"
    return result


@dataclass(frozen=True)
class LockRuleRow:
    """One row of TestWorktreeLockIsMine's decision matrix (test_lib_worktree_collision_guard.py), with the result
    the four-argument rule gives and the result `session-keyed` gives."""

    row_id: str
    four_argument_result: int
    session_keyed_result: int
    build: Callable[[Path, Path, int], tuple[tuple[str, ...], dict]]


def _row_same_session_other_pid(seed: Callable[[Path, Path], None], **kwargs) -> Callable:
    def build(home: Path, tmp_path: Path, live_pid: int):
        seed(home, tmp_path)
        return (OWN_PID, "shared-session", _OTHER_CALLER_PID, "shared-session"), kwargs

    return build


def _build_dead_leftover(home: Path, tmp_path: Path, live_pid: int):
    dead_pid = _dead_pid()
    _seed_session(home, "leftover-owner-session", pid=dead_pid, start_time=_FIXED_START_TIME)
    return (str(dead_pid), "shared-session", _OTHER_CALLER_PID, "shared-session"), {}


def _build_one_line_entry(home: Path, tmp_path: Path, live_pid: int):
    sessions = home / ".claude" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / OWN_PID).write_text("one-line-entry-session\n")
    return (OWN_PID, "shared-session", _OTHER_CALLER_PID, "shared-session"), {}


def _build_registered_but_ps_fails(home: Path, tmp_path: Path, live_pid: int):
    _seed_session(home, "cleared-successor-session", pid=live_pid)
    extra_env, failing_ps_marker = _install_failing_ps(tmp_path)
    return (
        (str(live_pid), "shared-session", _OTHER_CALLER_PID, "shared-session"),
        {"extra_env": extra_env, "failing_ps_marker": failing_ps_marker},
    )


def _build_alternate_config_dir(seeded: bool):
    def build(home: Path, tmp_path: Path, live_pid: int):
        alternate_base = tmp_path / "alt-base"
        if seeded:
            _seed_session(alternate_base, "shared-session")
        else:
            _seed_session(home, "shared-session")
        return (
            (OWN_PID, "shared-session", _OTHER_CALLER_PID, "shared-session"),
            {"config_dir_env": str(alternate_base / ".claude")},
        )

    return build


def _build_plain(arguments: tuple[str, ...], **kwargs):
    def build(home: Path, tmp_path: Path, live_pid: int):
        return arguments, kwargs

    return build


def _build_seeded_plain(session_id: str, arguments: tuple[str, ...], **seed_options):
    def build(home: Path, tmp_path: Path, live_pid: int):
        _seed_session(home, session_id, **seed_options)
        return arguments, {}

    return build


LOCK_RULE_ROWS = (
    LockRuleRow("same-pid-with-different-session", 0, 1, _build_plain((OWN_PID, "lock-session", OWN_PID, "caller-session"))),
    LockRuleRow("same-pid-with-old-format-lock", 0, 1, _build_plain((OWN_PID, "", OWN_PID, "caller-session"))),
    LockRuleRow("other-pid-dead-and-unregistered", 1, 1, lambda home, tmp_path, live_pid: (
        (str(_dead_pid()), "", _OTHER_CALLER_PID, ""), {})),
    LockRuleRow(
        "other-pid-with-different-session",
        1,
        1,
        _build_plain((OWN_PID, "lock-session", _OTHER_CALLER_PID, "caller-session")),
    ),
    LockRuleRow("same-session-other-pid-dead-with-leftover-entry", 0, 0, _build_dead_leftover),
    LockRuleRow(
        "same-session-other-pid-live-registered-to-same-session",
        1,
        1,
        _row_same_session_other_pid(lambda home, tmp_path: _seed_session(home, "shared-session")),
    ),
    LockRuleRow(
        "same-session-other-pid-live-registered-to-another-session",
        1,
        1,
        _row_same_session_other_pid(lambda home, tmp_path: _seed_session(home, "cleared-successor-session")),
    ),
    LockRuleRow(
        "same-session-other-pid-live-with-stale-start-time",
        0,
        0,
        _row_same_session_other_pid(
            lambda home, tmp_path: _seed_session(home, "stale-owner-session", start_time=_FIXED_START_TIME)
        ),
    ),
    LockRuleRow("same-session-other-pid-live-with-one-line-entry", 0, 0, _build_one_line_entry),
    LockRuleRow("same-session-other-pid-live-registered-but-ps-fails", 0, 0, _build_registered_but_ps_fails),
    LockRuleRow("registry-under-non-default-config-dir-is-read", 1, 1, _build_alternate_config_dir(seeded=True)),
    LockRuleRow(
        "registry-only-under-home-is-not-read-for-non-default-config-dir",
        0,
        0,
        _build_alternate_config_dir(seeded=False),
    ),
    LockRuleRow(
        "relative-config-dir-with-same-session-other-pid",
        1,
        1,
        _row_same_session_other_pid(lambda home, tmp_path: None, config_dir_env="relative/config-dir"),
    ),
    LockRuleRow(
        "relative-config-dir-with-same-pid",
        0,
        1,
        _build_plain((OWN_PID, "lock-session", OWN_PID, "caller-session"), config_dir_env="relative/config-dir"),
    ),
    LockRuleRow("both-pids-empty", 1, 1, _build_plain(("", "shared-session", "", "shared-session"))),
    LockRuleRow(
        "empty-caller-pid-with-dead-unregistered-lock-pid",
        1,
        1,
        lambda home, tmp_path, live_pid: ((str(_dead_pid()), "shared-session", "", "shared-session"), {}),
    ),
    LockRuleRow(
        "empty-lock-pid", 1, 1, _build_plain(("", "shared-session", _OTHER_CALLER_PID, "shared-session"))
    ),
)

class TestSessionKeyedLockRule:
    @pytest.fixture
    def live_pid(self):
        with live_process() as pid:
            yield pid

    @pytest.fixture
    def lock_rule_scene(self, tmp_path):
        return make_scene(tmp_path, ("shell",))

    @pytest.mark.parametrize("keying", [None, "", "bogus"], ids=["four-arguments", "empty-fifth", "unknown-fifth"])
    @pytest.mark.parametrize("row", LOCK_RULE_ROWS, ids=lambda row: row.row_id)
    def test_a_row_without_session_keying_keeps_its_four_argument_result(
        self, row, keying, lock_rule_scene, tmp_path, live_pid
    ):
        arguments, options = row.build(lock_rule_scene.home, tmp_path, live_pid)
        result = run_lock_rule(lock_rule_scene, arguments, keying=keying, **options)
        assert (result.returncode, result.stdout, result.stderr) == (row.four_argument_result, "", "")

    @pytest.mark.parametrize("row", LOCK_RULE_ROWS, ids=lambda row: row.row_id)
    def test_a_row_under_session_keying_has_its_stated_result(self, row, lock_rule_scene, tmp_path, live_pid):
        arguments, options = row.build(lock_rule_scene.home, tmp_path, live_pid)
        result = run_lock_rule(lock_rule_scene, arguments, keying="session-keyed", **options)
        assert (result.returncode, result.stdout, result.stderr) == (row.session_keyed_result, "", "")

    def test_the_same_pid_and_session_with_the_pid_unregistered_is_mine(self, lock_rule_scene):
        result = run_lock_rule(
            lock_rule_scene, (OWN_PID, "shared-session", OWN_PID, "shared-session"), keying="session-keyed"
        )
        assert result.returncode == 0

    def test_a_live_registered_locked_pid_under_the_same_session_is_not_mine(self, lock_rule_scene):
        _seed_session(lock_rule_scene.home, "shared-session")
        result = run_lock_rule(
            lock_rule_scene, (OWN_PID, "shared-session", OWN_PID, "shared-session"), keying="session-keyed"
        )
        assert result.returncode == 1

    def test_a_ps_failure_leaves_the_registered_locked_pid_unconfirmed_so_the_lock_is_mine(
        self, lock_rule_scene, tmp_path, live_pid
    ):
        arguments, options = _build_registered_but_ps_fails(lock_rule_scene.home, tmp_path, live_pid)
        assert run_lock_rule(lock_rule_scene, arguments, keying="session-keyed", **options).returncode == 0


GUARD_LOCK_HOLDER_SESSION = "other-session"
NESTED_CONVERSATION_ID = "conv-0000-4000-8000-06"


def _hold_lock(worktree: Path, holder_pid: int | str, session_id: str) -> None:
    Path(_git_dir(worktree), "locked").write_text(f"claude-code pid {holder_pid} session {session_id}\n")


def _assert_live_holder_deny_text(
    text: str, holder_pid: int | str, holder_session_id: str | None = None
) -> None:
    """The deny names the lock's PID, and its session id only when it records one, and no harness."""
    assert f"pid {holder_pid}" in text
    assert ("session" in text) == (holder_session_id is not None)
    if holder_session_id is not None:
        assert f"session {holder_session_id}" in text
    assert "cursor" not in text.lower()
    assert "claude" not in text.lower()
    assert "ask the engineer in chat" in text
    assert "git worktree unlock" not in text


def _guard(scene: Scene, worktree: Path, common_dir: str, payload: dict, *, claudecode: bool = False):
    return scene.run(
        GUARD_SCRIPT,
        claudecode=claudecode,
        env={"INPUT": payload_text(payload)},
        argv=(str(worktree), common_dir),
    )


def _cursor_payload(conversation_id: str, **extra) -> dict:
    return {**CURSOR_PAYLOAD, "conversation_id": conversation_id, "session_id": conversation_id, **extra}


class TestSessionKeyedGuard:
    @pytest.mark.parametrize(
        ("claudecode", "tree_names", "registered", "rules", "is_mine"),
        [
            pytest.param(
                True, ("claude",), ((0, REGISTERED_SESSION_ID),), (), True, id="claudecode-set-with-registered-ancestor"
            ),
            pytest.param(
                False, ("claude",), ((0, REGISTERED_SESSION_ID),), (), True, id="claudecode-unset-with-live-registered-ancestor"
            ),
            pytest.param(False, ("Cursor",), (), (), False, id="claudecode-unset-with-no-registered-entry"),
            pytest.param(
                False,
                ("Cursor",),
                ((0, REGISTERED_SESSION_ID),),
                (PsRequestRule("lstart", "*", "fail"),),
                False,
                id="claudecode-unset-with-the-registry-read-failing",
            ),
        ],
    )
    def test_a_same_pid_lock_under_another_session_is_mine_only_while_the_pid_is_a_registered_ancestor(
        self, tmp_path, claudecode, tree_names, registered, rules, is_mine
    ):
        scene = make_scene(tmp_path, tree_names, registered=registered, rules=rules)
        worktree, common_dir = make_linked_worktree(tmp_path)
        _hold_lock(worktree, scene.tree.nearest_pid, GUARD_LOCK_HOLDER_SESSION)

        result = _guard(scene, worktree, common_dir, _cursor_payload(CONVERSATION_ID), claudecode=claudecode)

        assert result.returncode == (0 if is_mine else 1), result
        if not is_mine:
            _assert_live_holder_deny_text(result.stdout, scene.tree.nearest_pid, GUARD_LOCK_HOLDER_SESSION)

    @pytest.mark.parametrize("tree_names", [("Cursor",), ("agent",)], ids=["cursor-host", "cli-host"])
    def test_a_cursor_payload_under_claudecode_is_denied_with_no_lock_written(self, tmp_path, tree_names):
        """No registry entry and a healthy `ps`, so the only refusal is CLAUDECODE's."""
        scene = make_scene(tmp_path, tree_names)
        worktree, common_dir = make_linked_worktree(tmp_path)

        result = _guard(scene, worktree, common_dir, CURSOR_PAYLOAD, claudecode=True)

        assert (result.returncode, result.stdout) == (
            1,
            "could not resolve this session's own process identity to check the worktree lock",
        )
        assert not Path(_git_dir(worktree), "locked").exists()

    def test_a_second_conversation_on_one_host_pid_is_denied_after_the_reread_with_text_that_defers_to_the_engineer(
        self, tmp_path
    ):
        scene = make_scene(tmp_path, ("Cursor",))
        worktree, common_dir = make_linked_worktree(tmp_path)
        first = _cursor_payload(CONVERSATION_ID)
        second = _cursor_payload(PARENT_CONVERSATION_ID)

        acquired = _guard(scene, worktree, common_dir, first)
        denied = _guard(scene, worktree, common_dir, second)
        first_again = _guard(scene, worktree, common_dir, first)

        assert acquired.returncode == 0, acquired
        assert worktree_lock_text(worktree) == f"claude-code pid {scene.tree.nearest_pid} session {CONVERSATION_ID}"
        assert denied.returncode == 1
        _assert_live_holder_deny_text(denied.stdout, scene.tree.nearest_pid, CONVERSATION_ID)
        assert first_again.returncode == 0

    def test_a_dead_holder_under_the_same_conversation_is_the_callers_own_lock_and_is_left_as_it_is(self, tmp_path):
        """A Cursor quit and relaunch: the resumed conversation's lock names the exited host."""
        scene = make_scene(tmp_path, ("Cursor",))
        worktree, common_dir = make_linked_worktree(tmp_path)
        dead_pid = _dead_pid()
        _hold_lock(worktree, dead_pid, CONVERSATION_ID)

        result = _guard(scene, worktree, common_dir, _cursor_payload(CONVERSATION_ID))

        assert (result.returncode, result.stdout) == (0, "")
        assert worktree_lock_text(worktree) == f"claude-code pid {dead_pid} session {CONVERSATION_ID}"

    def test_a_dead_holder_under_another_conversation_is_reclaimed_for_the_caller(self, tmp_path):
        scene = make_scene(tmp_path, ("Cursor",))
        worktree, common_dir = make_linked_worktree(tmp_path)
        _hold_lock(worktree, _dead_pid(), PARENT_CONVERSATION_ID)

        result = _guard(scene, worktree, common_dir, _cursor_payload(CONVERSATION_ID))

        assert (result.returncode, result.stdout) == (0, "")
        assert worktree_lock_text(worktree) == f"claude-code pid {scene.tree.nearest_pid} session {CONVERSATION_ID}"

    def test_a_dead_holder_whose_eviction_claim_is_already_spent_is_denied_with_its_pid_named(self, tmp_path):
        scene = make_scene(tmp_path, ("Cursor",))
        worktree, common_dir = make_linked_worktree(tmp_path)
        dead_pid = _dead_pid()
        _hold_lock(worktree, dead_pid, PARENT_CONVERSATION_ID)
        Path(_git_dir(worktree), f"claude-evicted-lock-{dead_pid}-{PARENT_CONVERSATION_ID}").write_text("spent\n")

        result = _guard(scene, worktree, common_dir, _cursor_payload(CONVERSATION_ID))

        assert result.returncode == 1
        assert f"locked by pid {dead_pid}, which is no longer running" in result.stdout
        assert worktree_lock_text(worktree) == f"claude-code pid {dead_pid} session {PARENT_CONVERSATION_ID}"

    def test_a_live_old_format_holder_is_named_by_its_pid_alone(self, tmp_path):
        scene = make_scene(tmp_path, ("Cursor",))
        worktree, common_dir = make_linked_worktree(tmp_path)
        Path(_git_dir(worktree), "locked").write_text(f"claude-code pid {scene.tree.nearest_pid}\n")

        result = _guard(scene, worktree, common_dir, _cursor_payload(CONVERSATION_ID))

        assert result.returncode == 1, result
        _assert_live_holder_deny_text(result.stdout, scene.tree.nearest_pid)

    @pytest.mark.parametrize(
        ("taker", "caller", "is_mine"),
        [
            pytest.param(
                _cursor_payload(CONVERSATION_ID),
                _cursor_payload(CHILD_CONVERSATION_ID, parent_agent_id=CONVERSATION_ID),
                True,
                id="child-writes-into-a-lock-its-parent-holds",
            ),
            pytest.param(
                _cursor_payload(CHILD_CONVERSATION_ID, parent_agent_id=CONVERSATION_ID),
                _cursor_payload(CONVERSATION_ID),
                True,
                id="parent-writes-into-a-lock-its-child-took-first",
            ),
            pytest.param(
                _cursor_payload(CONVERSATION_ID),
                _cursor_payload(NESTED_CONVERSATION_ID, parent_agent_id=CHILD_CONVERSATION_ID),
                False,
                id="nested-child-writes-into-the-roots-lock",
            ),
        ],
    )
    def test_a_subagent_shares_its_direct_parents_lock_and_a_nested_one_does_not_share_the_roots(
        self, tmp_path, taker, caller, is_mine
    ):
        scene = make_scene(tmp_path, ("Cursor",))
        worktree, common_dir = make_linked_worktree(tmp_path)

        assert _guard(scene, worktree, common_dir, taker).returncode == 0
        result = _guard(scene, worktree, common_dir, caller)

        assert result.returncode == (0 if is_mine else 1), result


# --- The two worktree gates, run whole ----------------------------------------------------------------------


def _file_write_payload(worktree: Path, record: dict) -> dict:
    return {**record, "tool_name": "Write", "tool_input": {"file_path": str(worktree / "file.txt"), "content": "x"}}


def _git_write_payload(worktree: Path, record: dict) -> dict:
    return {**record, "tool_name": "Bash", "tool_input": {"command": "git commit -m foo"}, "cwd": str(worktree)}


WORKTREE_GATES = {
    "file-writes": (HOOKS_DIR / "require-worktree-for-file-writes.sh", _file_write_payload),
    "git-writes": (HOOKS_DIR / "require-worktree-for-git-writes.sh", _git_write_payload),
}


class TestWorktreeGatesRunWhole:
    """Each gate reads its own payload from stdin into `INPUT`. These rows pin that the global reaches identity and the
    allow emitter, which the rows that set `INPUT` themselves cannot."""

    @pytest.mark.parametrize("gate", WORKTREE_GATES)
    def test_a_fresh_lock_under_a_cursor_host_prints_no_allow_and_names_the_conversation(self, tmp_path, gate):
        gate_script, build_payload = WORKTREE_GATES[gate]
        scene = make_scene(tmp_path, ("Cursor",))
        worktree, _ = make_linked_worktree(tmp_path, opted_in=True)

        result = scene.run_command(
            [str(gate_script)], stdin=payload_text(build_payload(worktree, CURSOR_PAYLOAD)), cwd=worktree
        )

        assert (result.returncode, result.stdout) == (0, "")
        assert worktree_lock_text(worktree) == f"claude-code pid {scene.tree.nearest_pid} session {CONVERSATION_ID}"

    @pytest.mark.parametrize("gate", WORKTREE_GATES)
    def test_a_fresh_lock_for_a_claude_code_session_still_prints_its_allow(self, tmp_path, gate):
        """The control for the row above: the same flow, with the allow the Cursor row withholds."""
        gate_script, build_payload = WORKTREE_GATES[gate]
        scene = make_scene(tmp_path, CLAUDE_AS_PARENT, registered=((0, REGISTERED_SESSION_ID),))
        worktree, _ = make_linked_worktree(tmp_path, opted_in=True)

        result = scene.run_command(
            [str(gate_script)],
            claudecode=True,
            stdin=payload_text(build_payload(worktree, CLAUDE_CODE_PAYLOAD)),
            cwd=worktree,
        )

        assert emitted_an_allow(result)
        assert worktree_lock_text(worktree) == f"claude-code pid {scene.tree.nearest_pid} session {REGISTERED_SESSION_ID}"


# --- The allow emitter and the session hooks decline a Cursor payload -----------------------------------------

EMIT_ALLOW_FROM_STDIN = 'INPUT=$(cat); _lib_emit_allow_with_context "note"'


def emitted_an_allow(result: subprocess.CompletedProcess) -> bool:
    assert result.returncode == 0, result
    if not result.stdout:
        return False
    return json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "allow"


def write_jq_failing_on_the_predicate_call(directory: Path) -> Path:
    """A `jq` that exits 2 for a call passing `-e`, as the Cursor-payload predicate's call does, and runs the real `jq`
    for every other call. Returns `directory`."""
    real_jq = shutil.which("jq")
    assert real_jq
    directory.mkdir()
    stub = directory / "jq"
    stub.write_text(
        f'#!/bin/bash\nfor argument in "$@"; do [ "$argument" = "-e" ] && exit 2; done\nexec {shlex.quote(real_jq)} "$@"\n'
    )
    stub.chmod(0o755)
    return directory


class TestCopyAllowSuppressed:
    @pytest.mark.parametrize("claudecode", [False, True], ids=["claudecode-unset", "claudecode-set"])
    @pytest.mark.parametrize("tree_names", [UNDER_CURSOR, UNDER_CLI], ids=["cursor-host", "cli-host"])
    @pytest.mark.parametrize("keys", PREDICATE_KEY_SETS, ids=["workspace-roots", "cursor-version", "both-keys"])
    def test_an_untranslated_payload_under_a_host_gets_no_allow(self, tmp_path, claudecode, tree_names, keys):
        scene = make_scene(tmp_path, tree_names)
        result = scene.run(
            EMIT_ALLOW_FROM_STDIN, claudecode=claudecode, stdin=payload_text({**CLAUDE_CODE_PAYLOAD, **keys})
        )
        assert (result.returncode, result.stdout) == (0, "")

    @pytest.mark.parametrize(
        ("tree_names", "payload"),
        [
            pytest.param(UNDER_CURSOR, CLAUDE_CODE_PAYLOAD, id="payload-without-predicate-key-under-cursor-host"),
            pytest.param(("shell",), CLAUDE_PAYLOAD_WITH_PREDICATE_KEY, id="predicate-key-payload-with-no-host"),
            pytest.param(
                ("Cursor", "shell", "claude"),
                CLAUDE_PAYLOAD_WITH_PREDICATE_KEY,
                id="predicate-key-payload-under-a-claude-below-cursor-chain",
            ),
            pytest.param(
                ("agent", "shell", "claude"), CLAUDE_PAYLOAD_WITH_PREDICATE_KEY, id="predicate-key-under-claude-under-cli"
            ),
        ],
    )
    def test_a_claudecode_process_keeps_its_allow_where_the_payload_or_the_host_walk_does_not_say_cursor(
        self, tmp_path, tree_names, payload
    ):
        scene = make_scene(tmp_path, tree_names)
        result = scene.run(EMIT_ALLOW_FROM_STDIN, claudecode=True, stdin=payload_text(payload))
        assert emitted_an_allow(result)

    def test_a_payload_without_predicate_keys_keeps_its_allow_with_claudecode_unset(self, tmp_path):
        scene = make_scene(tmp_path, UNDER_CURSOR)
        result = scene.run(EMIT_ALLOW_FROM_STDIN, stdin=payload_text(CLAUDE_CODE_PAYLOAD))
        assert emitted_an_allow(result)

    @pytest.mark.parametrize(
        ("tree_names", "rules", "walk_status"), UNRESOLVED_HOST_ROWS.values(), ids=list(UNRESOLVED_HOST_ROWS)
    )
    def test_an_untranslated_payload_gets_no_allow_with_claudecode_unset_whatever_the_host_walk_returns(
        self, tmp_path, tree_names, rules, walk_status
    ):
        scene = make_scene(tmp_path, tree_names, rules=rules)
        result = scene.run(EMIT_ALLOW_FROM_STDIN, stdin=payload_text(CURSOR_PAYLOAD))
        assert (result.returncode, result.stdout) == (0, "")
        assert_no_walk_ran_and_the_scene_walk_returns(scene, walk_status)

    @pytest.mark.parametrize(
        ("tree_names", "rules", "walk_status"), UNRESOLVED_HOST_ROWS.values(), ids=list(UNRESOLVED_HOST_ROWS)
    )
    def test_a_claudecode_process_keeps_its_allow_for_an_untranslated_payload_when_the_host_walk_finds_no_host(
        self, tmp_path, tree_names, rules, walk_status
    ):
        scene = make_scene(tmp_path, tree_names, rules=rules)
        result = scene.run(EMIT_ALLOW_FROM_STDIN, claudecode=True, stdin=payload_text(CURSOR_PAYLOAD))
        assert emitted_an_allow(result)

    def test_an_untranslated_payload_gets_no_allow_from_a_claudecode_process_under_a_cursor_host(self, tmp_path):
        scene = make_scene(tmp_path, UNDER_CURSOR)
        result = scene.run(EMIT_ALLOW_FROM_STDIN, claudecode=True, stdin=payload_text(CURSOR_PAYLOAD))
        assert (result.returncode, result.stdout) == (0, "")

    def test_a_jq_cap_kill_counts_as_a_match_so_an_unset_claudecode_gets_no_allow_with_no_host_walk(self, tmp_path):
        """Pins the unset-CLAUDECODE arm: the fake host plays no part, because the helper returns before any walk."""
        scene = make_scene(tmp_path, UNDER_CURSOR)
        hung_jq_dir = tmp_path / "hung-jq"
        hung_jq_dir.mkdir()
        write_scaled_timeout_shim(hung_jq_dir)
        write_prewarmed_stub(hung_jq_dir / "jq", f"#!/bin/bash\nsleep {scaled_shim_sleep(10)}\n")

        with assert_cap_engaged(hung_jq_dir, production_cap=5, killed_calls=2):
            result = scene.run(
                "INPUT=$(cat); _lib_payload_is_untranslated_cursor \"$INPUT\"; "
                'printf "predicate=%s|" $?; _lib_emit_allow_with_context "note"',
                stdin=payload_text(CLAUDE_CODE_PAYLOAD),
                extra_path=hung_jq_dir,
            )

        assert (result.returncode, result.stdout) == (0, "predicate=0|")

    @pytest.mark.parametrize(
        ("claudecode", "tree_names", "allows"),
        [
            pytest.param(False, UNDER_CURSOR, False, id="claudecode-unset-under-a-cursor-host"),
            pytest.param(False, ("shell",), False, id="claudecode-unset-with-no-host"),
            pytest.param(True, UNDER_CURSOR, False, id="claudecode-set-under-a-cursor-host"),
            pytest.param(True, ("shell",), True, id="claudecode-set-with-no-host"),
        ],
    )
    def test_a_jq_that_fails_only_on_the_predicate_call_leaves_the_allow_to_claudecode_and_the_host_walk(
        self, tmp_path, claudecode, tree_names, allows
    ):
        scene = make_scene(tmp_path, tree_names)
        failing_jq_dir = write_jq_failing_on_the_predicate_call(tmp_path / "predicate-failing-jq")

        result = scene.run(
            EMIT_ALLOW_FROM_STDIN,
            claudecode=claudecode,
            stdin=payload_text(CLAUDE_CODE_PAYLOAD),
            extra_path=failing_jq_dir,
        )

        assert emitted_an_allow(result) is allows
        if not claudecode:
            assert scene.stub.requests() == []

    def test_the_allow_emitter_runs_no_host_walk_for_an_empty_input(self, tmp_path):
        scene = make_scene(tmp_path, UNDER_CURSOR)
        result = scene.run('_lib_emit_allow_with_context "note"')
        assert emitted_an_allow(result)
        assert scene.stub.requests() == []


# --- The host walk and the session hooks' decline --------------------------------------------------------

PADDED_PAYLOAD_BYTES = 1_400_000


def run_walk(scene: Scene, *, prelude: str = "") -> subprocess.CompletedProcess:
    """Runs the host walk and prints `walk=<status>` after its output."""
    return scene.run(f'{WALK_SCRIPT}; printf "|walk=%s" $?', prelude=prelude)


def walk_outcome(scene: Scene, result: subprocess.CompletedProcess) -> tuple[int, str]:
    """(status, normalized printed PID) of a `run_walk` result."""
    printed, _, status = result.stdout.rpartition("|walk=")
    return int(status), scene.normalize(printed)


def assert_no_walk_ran_and_the_scene_walk_returns(scene: Scene, walk_status: int) -> None:
    """For a row run with CLAUDECODE unset: no `ps` read was issued, and the scene's own walk, run afterwards, returns
    `walk_status`. A decline that ignored the walk would otherwise pass whatever the scene returned."""
    assert scene.stub.requests() == []
    assert walk_outcome(scene, run_walk(scene))[0] == walk_status


def session_hook_decline_line(hook_name: str) -> str:
    _, written_nothing_text = SESSION_HOOKS[hook_name]
    return f"[{hook_name.removesuffix('.sh')}] {SESSION_HOOK_DECLINE_FRAGMENT}; {written_nothing_text}\n"


def run_session_hook(scene: Scene, hook_name: str, payload: dict, *, claudecode: bool = False):
    return scene.run_command(
        ["bash", str(HOOKS_DIR / hook_name)], claudecode=claudecode, stdin=payload_text(payload)
    )


@dataclass(frozen=True)
class WalkRow:
    tree_names: tuple[str, ...]
    status: int
    printed_depth: int | None = None


HOST_WALK_ROWS = {
    "macos-cursor-main-bundle-path": WalkRow((MACOS_CURSOR_MAIN_PATH,), 0, 0),
    "bare-cursor-name-as-on-linux": WalkRow(("Cursor",), 0, 0),
    "cursor-name-with-trailing-whitespace": WalkRow(("Cursor  ",), 0, 0),
    "helper-path-with-spaces-and-parentheses": WalkRow((MACOS_CURSOR_HELPER_PATH,), 2),
    "retitled-helper-title": WalkRow((RETITLED_HELPER_TITLE,), 2),
    "helper-with-no-cursor-above": WalkRow(("Cursor Helper (Plugin)",), 2),
    "cli-invoked-path": WalkRow((CLI_INVOKED_PATH,), 0, 0),
    "cli-worker-path-under-global-storage-holding-a-space-and-a-cursor-component": WalkRow(
        (CLI_WORKER_GLOBAL_STORAGE_PATH,), 0, 0
    ),
    "agent": WalkRow(("agent",), 0, 0),
    "cursor-agent": WalkRow(("cursor-agent",), 0, 0),
    "nearer-agent-below-an-agent": WalkRow(("agent", "agent"), 0, 0),
    "cursor-agent-below-an-agent": WalkRow(("agent", "cursor-agent"), 0, 0),
    "cursor-above-an-agent-with-no-claude-between": WalkRow(("Cursor", "agent"), 0, 1),
    "cursor-then-claude-then-agent-nearest-stops-at-claude-and-prints-the-agent": WalkRow(
        ("Cursor", "claude", "agent"), 0, 0
    ),
    "claude-above-an-agent-with-no-cursor": WalkRow(("claude", "agent"), 0, 0),
    "claude-under-cli-returns-three": WalkRow(("agent", "shell", "claude"), 3),
    "claude-name": WalkRow(("Cursor", "claude"), 3),
    "claude-with-a-leading-hyphen": WalkRow(("Cursor", "-claude"), 3),
    "claude-full-path": WalkRow(("Cursor", "/usr/local/bin/claude"), 3),
    "cursor-then-shell-then-claude-chain-returns-three": WalkRow(("Cursor", "shell", "claude"), 3),
    **{f"lookalike-{name}": WalkRow((name,), 2) for name in HOST_NAME_LOOKALIKES},
}


@dataclass(frozen=True)
class FailedReadRow:
    """A walk over `tree_names` whose requests are exactly `requests`, ending in `status`. `requests` names PIDs
    as the nearest ancestor (this test process) and FAKE_PID_BASE, the outermost process of a two-process tree."""

    tree_names: tuple[str, ...]
    status: int
    requests: tuple[tuple[str, int, str], ...]
    rules: tuple[PsRequestRule, ...] = ()
    top_ppid_output: str = "0"


NEAREST_PID = os.getpid()
VANISHED_PARENT_PID = 777777
FAILED_READ_ROWS = {
    "first-read-fails": FailedReadRow(("shell",), 4, (("comm", NEAREST_PID, "fail"),), FAIL_EVERY_READ),
    "read-fails-after-remembering-an-agent": FailedReadRow(
        ("shell", "agent"),
        4,
        (("comm", NEAREST_PID, "ok"), ("ppid", NEAREST_PID, "ok"), ("comm", FAKE_PID_BASE, "fail")),
        (PsRequestRule("comm", FAKE_PID_BASE, "fail"),),
    ),
    "mid-chain-parent-vanishes": FailedReadRow(
        ("shell",),
        4,
        (("comm", NEAREST_PID, "ok"), ("ppid", NEAREST_PID, "ok"), ("comm", VANISHED_PARENT_PID, "absent")),
        top_ppid_output=str(VANISHED_PARENT_PID),
    ),
    "cap-kill-exits-124": FailedReadRow(
        ("shell",), 4, (("comm", NEAREST_PID, "cap-kill"),), (PsRequestRule("comm", "*", "cap-kill"),)
    ),
    "ppid-read-prints-garbage": FailedReadRow(
        ("shell",),
        4,
        (("comm", NEAREST_PID, "ok"), ("ppid", NEAREST_PID, "output")),
        (PsRequestRule("ppid", "*", "output", "abc"),),
    ),
    **{
        f"top-ppid-prints-{label}": FailedReadRow(
            ("shell",), 2, (("comm", NEAREST_PID, "ok"), ("ppid", NEAREST_PID, "ok")), top_ppid_output=top_ppid_output
        )
        for label, top_ppid_output in (("zero", "0"), ("one", "1"), ("nothing", ""))
    },
}


def predicate_status(scene: Scene, payload_json: str) -> str:
    """`_lib_payload_is_untranslated_cursor`'s status for `payload_json`, as text."""
    result = scene.run('INPUT=$(cat); _lib_payload_is_untranslated_cursor "$INPUT"; printf "%s" $?', stdin=payload_json)
    return result.stdout


class TestCursorPayloadDecline:
    @pytest.mark.parametrize("row", HOST_WALK_ROWS.values(), ids=list(HOST_WALK_ROWS))
    def test_the_host_walk_prints_the_nearest_host_or_the_status_the_row_states(self, tmp_path, row):
        scene = make_scene(tmp_path, row.tree_names)
        status, printed = walk_outcome(scene, run_walk(scene))
        expected_printed = f"@{row.printed_depth}" if row.printed_depth is not None else ""
        assert (status, printed) == (row.status, expected_printed)
        scene.stub.assert_no_unknown_requests()

    @pytest.mark.parametrize("hook_name", SESSION_HOOKS)
    @pytest.mark.parametrize("claudecode", [False, True], ids=["claudecode-unset", "claudecode-set"])
    @pytest.mark.parametrize("tree_names", [("Cursor",), ("agent",), ("cursor-agent",)], ids=["cursor", "agent", "cursor-agent"])
    @pytest.mark.parametrize("keys", PREDICATE_KEY_SETS, ids=["workspace-roots", "cursor-version", "both-keys"])
    def test_a_session_hook_declines_a_cursor_shaped_payload_under_a_host(
        self, tmp_path, hook_name, claudecode, tree_names, keys
    ):
        scene = make_scene(tmp_path, tree_names)
        directory_name, _ = SESSION_HOOKS[hook_name]
        result = run_session_hook(scene, hook_name, {"session_id": CONVERSATION_ID, **keys}, claudecode=claudecode)
        assert result.returncode == 0
        assert SESSION_HOOK_DECLINE_FRAGMENT in result.stderr
        assert scene.state_files(directory_name) == []

    @pytest.mark.parametrize("hook_name", SESSION_HOOKS)
    @pytest.mark.parametrize(
        ("tree_names", "payload"),
        [
            pytest.param(CLAUDE_AS_PARENT, CLAUDE_PAYLOAD_WITH_PREDICATE_KEY, id="predicate-key-under-claude-parent"),
            pytest.param(
                ("Cursor", "shell", "claude"),
                CLAUDE_PAYLOAD_WITH_PREDICATE_KEY,
                id="predicate-key-under-a-claude-below-cursor-chain",
            ),
            pytest.param(
                ("agent", "shell", "claude"), CLAUDE_PAYLOAD_WITH_PREDICATE_KEY, id="predicate-key-under-claude-under-cli"
            ),
            pytest.param(("shell",), CLAUDE_PAYLOAD_WITH_PREDICATE_KEY, id="predicate-key-with-no-host"),
            pytest.param(UNDER_CURSOR, CLAUDE_CODE_PAYLOAD, id="no-predicate-key-under-a-cursor-host"),
        ],
    )
    def test_a_claudecode_session_hook_still_writes_where_the_predicate_and_the_host_walk_do_not_both_say_cursor(
        self, tmp_path, hook_name, tree_names, payload
    ):
        scene = make_scene(tmp_path, tree_names)
        directory_name, _ = SESSION_HOOKS[hook_name]
        result = run_session_hook(scene, hook_name, payload, claudecode=True)
        assert result.returncode == 0
        assert SESSION_HOOK_DECLINE_FRAGMENT not in result.stderr
        assert [name for name, _ in scene.state_files(directory_name)] == ["@0"]

    @pytest.mark.parametrize("hook_name", SESSION_HOOKS)
    @pytest.mark.parametrize(
        ("tree_names", "rules", "walk_status"), UNRESOLVED_HOST_ROWS.values(), ids=list(UNRESOLVED_HOST_ROWS)
    )
    def test_a_session_hook_declines_a_cursor_payload_with_claudecode_unset_whatever_the_host_walk_returns(
        self, tmp_path, hook_name, tree_names, rules, walk_status
    ):
        scene = make_scene(tmp_path, tree_names, rules=rules)
        directory_name, _ = SESSION_HOOKS[hook_name]
        result = run_session_hook(scene, hook_name, CURSOR_PAYLOAD)
        assert result.returncode == 0
        assert result.stderr == session_hook_decline_line(hook_name)
        assert scene.state_files(directory_name) == []
        assert_no_walk_ran_and_the_scene_walk_returns(scene, walk_status)

    @pytest.mark.parametrize("hook_name", SESSION_HOOKS)
    def test_a_session_hook_writes_for_a_payload_without_a_predicate_key_with_claudecode_unset(
        self, tmp_path, hook_name
    ):
        """The unset-CLAUDECODE decline needs the predicate to match first, so a payload with no Cursor key still
        registers under a Cursor host."""
        scene = make_scene(tmp_path, UNDER_CURSOR)
        directory_name, _ = SESSION_HOOKS[hook_name]
        result = run_session_hook(scene, hook_name, CLAUDE_CODE_PAYLOAD)
        assert result.returncode == 0
        assert SESSION_HOOK_DECLINE_FRAGMENT not in result.stderr
        assert [name for name, _ in scene.state_files(directory_name)] == ["@0"]

    @pytest.mark.parametrize("hook_name", SESSION_HOOKS)
    @pytest.mark.parametrize(
        ("tree_names", "rules", "walk_status"), UNRESOLVED_HOST_ROWS.values(), ids=list(UNRESOLVED_HOST_ROWS)
    )
    def test_a_claudecode_session_hook_writes_for_a_cursor_shaped_payload_when_the_host_walk_finds_no_host(
        self, tmp_path, hook_name, tree_names, rules, walk_status
    ):
        scene = make_scene(tmp_path, tree_names, rules=rules)
        directory_name, _ = SESSION_HOOKS[hook_name]
        result = run_session_hook(scene, hook_name, CURSOR_PAYLOAD, claudecode=True)
        assert result.returncode == 0
        assert result.stderr == ""
        assert [name for name, _ in scene.state_files(directory_name)] == ["@0"]

    def test_the_ps_stub_left_pads_a_ppid_read_as_real_ps_does(self, tmp_path):
        scene = make_scene(tmp_path, ("shell",))
        result = scene.run(f"ps -o ppid= -p {scene.tree.nearest_pid}")
        assert result.stdout == f"{'0':>{DEFAULT_PPID_PAD_WIDTH}}\n"

    def test_a_padded_mid_chain_ppid_read_is_trimmed_so_the_walk_continues_to_the_host(self, tmp_path):
        scene = make_scene(tmp_path, UNDER_CURSOR)
        status, printed = walk_outcome(scene, run_walk(scene))
        assert (status, printed) == (0, "@1")

    @pytest.mark.parametrize("top_ppid_output", ["0", "1"], ids=["zero", "one"])
    def test_a_padded_top_ppid_read_ends_the_walk_with_two_not_four(self, tmp_path, top_ppid_output):
        scene = make_scene(tmp_path, ("shell",), top_ppid_output=top_ppid_output)
        status, printed = walk_outcome(scene, run_walk(scene))
        assert (status, printed) == (2, "")

    @pytest.mark.parametrize("key", ["workspace_roots", "cursor_version"])
    @pytest.mark.parametrize("value", [[], None, ""], ids=["empty-array", "null", "empty-string"])
    def test_the_predicate_matches_a_key_that_is_present_whatever_its_value(self, tmp_path, key, value):
        scene = make_scene(tmp_path, ("shell",))
        assert predicate_status(scene, payload_text({**CLAUDE_CODE_PAYLOAD, key: value})) == "0"

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param(
                {**CLAUDE_CODE_PAYLOAD, "tool_input": {"file_path": "/scratch/f.txt", "workspace_roots": ["/scratch"]}},
                id="workspace-roots-nested-in-tool-input",
            ),
            pytest.param({**CLAUDE_CODE_PAYLOAD, "metadata": {"cursor_version": "0.0.0"}}, id="cursor-version-nested"),
            pytest.param(
                {
                    **CLAUDE_CODE_PAYLOAD,
                    "tool_input": {
                        "file_path": "/scratch/f.txt",
                        "new_string": 'has("workspace_roots") or has("cursor_version")',
                    },
                },
                id="both-names-in-an-edit-text",
            ),
        ],
    )
    def test_the_predicate_does_not_match_a_key_below_the_top_level_or_a_name_in_text(self, tmp_path, payload):
        scene = make_scene(tmp_path, ("shell",))
        assert predicate_status(scene, payload_text(payload)) == "1"

    @pytest.mark.parametrize(
        ("claudecode", "tree_names", "declines"),
        [
            pytest.param(False, ("shell",), True, id="claudecode-unset-with-no-host"),
            pytest.param(True, UNDER_CURSOR, True, id="claudecode-set-under-a-cursor-host"),
            pytest.param(True, ("shell",), False, id="claudecode-set-with-no-host"),
        ],
    )
    def test_a_jq_that_fails_only_on_the_predicate_call_makes_the_helper_decline_by_claudecode_then_the_host_walk(
        self, tmp_path, claudecode, tree_names, declines
    ):
        """A failed predicate counts as a match, so the decline falls to the `CLAUDECODE` test and then the walk."""
        scene = make_scene(tmp_path, tree_names)
        failing_jq_dir = write_jq_failing_on_the_predicate_call(tmp_path / "predicate-failing-jq")

        result = scene.run(
            'INPUT=$(cat); _lib_declines_untranslated_cursor_payload "$INPUT"; printf "%s" $?',
            claudecode=claudecode,
            stdin=payload_text(CLAUDE_CODE_PAYLOAD),
            extra_path=failing_jq_dir,
        )

        assert result.stdout == ("0" if declines else "1")
        if not claudecode:
            assert scene.stub.requests() == []

    def test_the_resolver_finds_no_registered_ancestor_after_an_import_copy_declined_under_a_cursor_host(self, tmp_path):
        scene = make_scene(tmp_path, ("Cursor",))
        hook_result = run_session_hook(scene, "capture-session-id.sh", CURSOR_PAYLOAD, claudecode=True)
        result = scene.run("_lib_resolve_claude_pid", claudecode=True, env=ADAPTER_VARIABLES)
        assert SESSION_HOOK_DECLINE_FRAGMENT in hook_result.stderr
        assert scene.state_files("sessions") == []
        assert (result.returncode, result.stdout) == (2, "")

    @pytest.mark.parametrize("walk_heals_before_the_guards", [False, True], ids=["walk-keeps-failing", "walk-recovers"])
    def test_two_conversations_under_one_runner_keep_distinct_identities_after_the_first_session_start_walk_failed(
        self, tmp_path, walk_heals_before_the_guards
    ):
        """The comm read fails and the start-time read works, so an import copy that kept going would register the
        runner's PID under the first conversation's id, and the second conversation would resolve to that id.
        The hook leg runs with CLAUDECODE unset and declines before any walk, so the failing read is the guards'
        condition, not the hook's."""
        scene = make_scene(tmp_path, ("Cursor",), rules=(PsRequestRule("comm", "*", "fail"),))
        worktree, common_dir = make_linked_worktree(tmp_path)
        first = _cursor_payload(CONVERSATION_ID)
        second = _cursor_payload(PARENT_CONVERSATION_ID)

        run_session_hook(scene, "capture-session-id.sh", first)
        if walk_heals_before_the_guards:
            scene.stub.clear_rules()
        first_guard = _guard(scene, worktree, common_dir, first)
        second_guard = _guard(scene, worktree, common_dir, second)

        assert scene.state_files("sessions") == []
        if walk_heals_before_the_guards:
            assert first_guard.returncode == 0, first_guard
            assert worktree_lock_text(worktree) == f"claude-code pid {scene.tree.nearest_pid} session {CONVERSATION_ID}"
            assert second_guard.returncode == 1
            _assert_live_holder_deny_text(second_guard.stdout, scene.tree.nearest_pid, CONVERSATION_ID)
        else:
            no_identity_text = "could not resolve this session's own process identity to check the worktree lock"
            assert [(first_guard.returncode, first_guard.stdout), (second_guard.returncode, second_guard.stdout)] == [
                (1, no_identity_text)
            ] * 2
            assert worktree_lock_text(worktree) == ""

    def test_a_registry_entry_written_before_a_relaunch_fails_the_start_time_check_after_it(self, tmp_path):
        relaunched_start_time = "Wed Mar  3 04:05:06 2021"
        scene = make_scene(
            tmp_path,
            ("Cursor",),
            registered=((0, "entry-from-before-the-relaunch"),),
            rules=(PsRequestRule("lstart", "*", "output", relaunched_start_time),),
        )
        result = scene.run(f'_lib_registered_live_sid "{scene.home / ".claude"}" {scene.tree.nearest_pid}')
        assert (result.returncode, result.stdout) == (1, "")

    @pytest.mark.parametrize("payload", [CURSOR_PAYLOAD, CLAUDE_CODE_PAYLOAD], ids=["cursor", "claude-code"])
    def test_the_predicate_answers_correctly_for_a_payload_of_1_4_megabytes(self, tmp_path, payload):
        scene = make_scene(tmp_path, ("shell",))
        padded = {**payload, "padding": "x" * PADDED_PAYLOAD_BYTES}
        result = scene.run(
            'INPUT=$(cat); _lib_payload_is_untranslated_cursor "$INPUT"; printf "%s" $?', stdin=payload_text(padded)
        )
        assert result.stdout == ("0" if "workspace_roots" in payload else "1")

    @pytest.mark.parametrize(
        ("payload_text_under_test", "expected_status"),
        [("not json at all", "0"), ("[1, 2]", "1"), ('"a string"', "1"), ("null", "1")],
        ids=["invalid-json-counts-as-a-match", "array", "string", "null"],
    )
    def test_the_predicate_counts_a_jq_error_as_a_match_and_a_non_object_as_none(
        self, tmp_path, payload_text_under_test, expected_status
    ):
        scene = make_scene(tmp_path, ("shell",))
        result = scene.run(
            'INPUT=$(cat); _lib_payload_is_untranslated_cursor "$INPUT"; printf "%s" $?', stdin=payload_text_under_test
        )
        assert result.stdout == expected_status

    def test_the_predicate_is_no_match_for_an_empty_payload_without_running_jq(self, tmp_path):
        scene = make_scene(tmp_path, ("shell",))
        jq_marker = tmp_path / "jq-ran"
        stub_dir = tmp_path / "jq-stub"
        stub_dir.mkdir()
        (stub_dir / "jq").write_text(f"#!/bin/bash\n: > {shlex.quote(str(jq_marker))}\nexit 0\n")
        (stub_dir / "jq").chmod(0o755)
        result = scene.run('_lib_payload_is_untranslated_cursor ""; printf "%s" $?', extra_path=stub_dir)
        assert result.stdout == "1"
        assert not jq_marker.exists()

    # The failed-read rows. Each runs the walk once under pipefail and once without, expecting the same status.

    @pytest.mark.parametrize("pipefail", [False, True], ids=["no-pipefail", "pipefail"])
    @pytest.mark.parametrize("row", FAILED_READ_ROWS.values(), ids=list(FAILED_READ_ROWS))
    def test_a_failed_ps_read_makes_the_walk_return_four_and_a_top_makes_it_return_two(self, tmp_path, row, pipefail):
        scene = make_scene(tmp_path, row.tree_names, rules=row.rules, top_ppid_output=row.top_ppid_output)

        result = run_walk(scene, prelude="set -o pipefail;" if pipefail else "")

        assert walk_outcome(scene, result) == (row.status, "")
        assert scene.stub.requests() == [(kind, str(pid), answer) for kind, pid, answer in row.requests]
        scene.stub.assert_no_unknown_requests()

    # Rows that run the walk under the real `ps`.

    @staticmethod
    def _bash_copied_as(directory: Path, host_name: str) -> tuple[str, Path]:
        """(the bash on PATH, a copy of it named `host_name` in `directory`). A copy that does not execute, by exit
        status or by an `OSError` such as a `noexec` mount's `PermissionError`, skips locally, as a macOS system
        binary's copy does, and fails under GITHUB_ACTIONS, as `require_zsh` does."""
        real_bash = shutil.which("bash")
        assert real_bash
        directory.mkdir(parents=True)
        host_copy = directory / host_name
        shutil.copy2(real_bash, host_copy)
        try:
            copy_executes = subprocess.run([str(host_copy), "-c", ":"], capture_output=True, check=False).returncode == 0
        except OSError:
            copy_executes = False
        if not copy_executes:
            reason = "a copy of this bash does not execute here, so it cannot stand in for a host process"
            if os.environ.get("GITHUB_ACTIONS"):
                pytest.fail(reason)
            pytest.skip(reason)
        return real_bash, host_copy

    @staticmethod
    def _real_walk_environment(scene_home: Path) -> dict[str, str]:
        environment = {name: value for name, value in os.environ.items() if name != "CLAUDECODE"}
        environment["HOME"] = str(scene_home)
        return environment

    @staticmethod
    def _walk_two_shells_below(real_bash: str) -> str:
        """A command that runs the walk in a bash that has an intermediate bash above it, so the walk's first hop
        reads that bash's PPID with the real `ps`. Each level ends in `:` so no bash `exec`s its last command."""
        walk = f". {shlex.quote(str(HOOKS_DIR / '_lib.sh'))}; _lib_cursor_host_pid"
        inner = f"{shlex.quote(real_bash)} -c {shlex.quote(walk)}; :"
        return f"{shlex.quote(real_bash)} -c {shlex.quote(inner)}; :"

    @classmethod
    def _real_walk_finds_a_host_above_this_process(cls, scene_home: Path) -> bool:
        """Whether the walk, run from a child of this process under the real `ps`, finds a host among this process's
        own ancestors."""
        walk = f". {shlex.quote(str(HOOKS_DIR / '_lib.sh'))}; _lib_cursor_host_pid >/dev/null"
        completed = subprocess.run(
            ["bash", "-c", walk],
            capture_output=True,
            check=False,
            env=cls._real_walk_environment(scene_home),
            timeout=60,
        )
        return completed.returncode == 0

    @pytest.mark.parametrize("host_name", ["Cursor", "agent", "cursor-agent"])
    def test_the_walk_finds_a_copied_bash_named_as_a_host_under_a_path_holding_a_space_with_the_real_ps(
        self, tmp_path, host_name
    ):
        # The walk keeps climbing past a CLI stand-in, so a `Cursor` ancestor of pytest would win over it.
        if host_name != "Cursor" and self._real_walk_finds_a_host_above_this_process(tmp_path):
            pytest.skip(NO_HOST_ABOVE_PYTEST_REASON)
        directory = tmp_path / "directory with a space"
        if host_name == "Cursor":
            directory = directory / "Cursor.app" / "Contents" / "MacOS"
        real_bash, host_copy = self._bash_copied_as(directory, host_name)
        host = subprocess.Popen(
            [str(host_copy), "-c", self._walk_two_shells_below(real_bash)],
            stdout=subprocess.PIPE,
            text=True,
            env=self._real_walk_environment(tmp_path),
        )
        printed, _ = host.communicate(timeout=60)
        assert printed == str(host.pid)

    @pytest.mark.skipif(sys.platform != "darwin", reason="reads macOS's `ps -o comm=` forms")
    def test_the_walk_finds_a_bash_exec_ed_under_an_agent_name_with_the_real_ps(self, tmp_path):
        """Assumes macOS reports an `exec -a` name for a bash stand-in as it did for the CLI's node [unverified]."""
        if self._real_walk_finds_a_host_above_this_process(tmp_path):
            pytest.skip(NO_HOST_ABOVE_PYTEST_REASON)
        real_bash = shutil.which("bash")
        assert real_bash
        invoked_name = tmp_path / "directory with a space" / "agent"
        walk_command = self._walk_two_shells_below(real_bash)
        launcher = f"exec -a {shlex.quote(str(invoked_name))} {shlex.quote(real_bash)} -c {shlex.quote(walk_command)}"
        host = subprocess.Popen(
            [real_bash, "-c", launcher],
            stdout=subprocess.PIPE,
            text=True,
            env=self._real_walk_environment(tmp_path),
        )
        printed, _ = host.communicate(timeout=60)
        assert printed == str(host.pid)
