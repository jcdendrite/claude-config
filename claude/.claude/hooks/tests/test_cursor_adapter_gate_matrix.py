"""Gate matrix: the unchanged harness hooks, run through the Cursor adapter's shim with no Cursor.

What the matrix does not cover: an informational hook listed in NO_WRITE_TRIGGER_YET runs a benign call only, so a state write
reached only by a trigger is not seen. A registration with a parity exception has no shim arm, only an import arm.

The process directory of the shim, near-miss and import arms is outside every repository, as Cursor runs user hooks. Each arm
first asserts that `git rev-parse --git-dir` fails there under its own environment, which carries no GIT_* name."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
from helpers import HOOKS_DIR, REPO_ROOT, SCRIPTS_DIR, _hook_class, symlink_hooks_lib_chain

from ._cursor_payloads import (
    CHILD_CONVERSATION_ID,
    CLAUDE_PROCESS_ENV_NAMES,
    CONTRACT_VERSION,
    CONVERSATION_ID,
    DELIVERED_PHASE_NUMBER,
    PARENT_CONVERSATION_ID,
    PARITY_EXCEPTIONS_JSON,
    SPEC_CLAUDE_EVENT,
    SPEC_IDS,
    TOOL_MAP_JSON,
    ShimEnv,
    assert_outside_any_repository,
    build_spec,
    file_tool_payload,
    hook_args,
    load_json,
    make_shim_env,
    parse_registration_key,
    phase_number,
    read_log_lines,
    recorded_tool_paths,
    recording_probe_script,
    registration_key,
    run_shim,
    settings_registrations,
    shell_pre_payload,
    shim_argv,
    shim_json,
    shim_process_env,
    without_git_environment,
    write_hook_script,
)

GATE_CLASSES = ("gate", "turn-gate", "batch-gate")

# --- builders ---

# Placeholder keys the builders add to every Cursor payload (shapes from recorded Cursor payloads).
COMMON_CURSOR_KEYS = {
    "conversation_id",
    "generation_id",
    "model",
    "hook_event_name",
    "cursor_version",
    "workspace_roots",
    "user_email",
}

# Expected JSON type per key, for the keys the builders set. Values are placeholders; shapes are what the tests pin.
EXPECTED_KEY_TYPES = {
    "conversation_id": str,
    "generation_id": str,
    "model": str,
    "hook_event_name": str,
    "cursor_version": str,
    "workspace_roots": list,
    "session_id": str,
    "tool_name": str,
    "tool_input": dict,
    "tool_use_id": str,
    "cwd": str,
    "tool_output": str,
    "duration": int,
    "file_path": str,
    "content": str,
    "attachments": list,
    "command": str,
    "sandbox": bool,
    "prompt": str,
    "is_background_agent": bool,
    "composer_mode": str,
    "subagent_id": str,
    "subagent_type": str,
    "subagent_model": str,
    "task": str,
    "tool_call_id": str,
    "is_parallel_worker": bool,
    "parent_conversation_id": str,
    "parent_agent_id": str,
    "status": str,
    "duration_ms": int,
    "tool_call_count": int,
    "message_count": int,
    "child_conversation_id": str,
    "loop_count": int,
    "transcript_path": str,
    "text": str,
}


class TestCursorPayloadBuilders:
    @pytest.mark.parametrize("spec_id", SPEC_IDS)
    def test_every_key_has_the_type_the_payload_records_show(self, spec_id: str, tmp_path: Path) -> None:
        cursor = build_spec(spec_id, tmp_path).cursor
        for key, value in cursor.items():
            if key == "user_email":
                assert value is None
                continue
            assert isinstance(value, EXPECTED_KEY_TYPES[key]), (spec_id, key, type(value))
        assert set(cursor) >= COMMON_CURSOR_KEYS

    @pytest.mark.parametrize("spec_id", SPEC_IDS)
    def test_the_ids_are_distinct_values(self, spec_id: str, tmp_path: Path) -> None:
        cursor = build_spec(spec_id, tmp_path).cursor
        ids = [cursor["conversation_id"], cursor["generation_id"]]
        if "session_id" in cursor:
            ids.append(cursor["session_id"])
        if "parent_agent_id" in cursor:
            ids.append(cursor["parent_agent_id"])
        assert len(set(ids)) == len(ids) - (1 if cursor.get("parent_agent_id") == cursor["conversation_id"] else 0), (
            spec_id,
            ids,
        )

    @pytest.mark.parametrize("spec_id", SPEC_IDS)
    def test_the_twin_is_a_claude_shaped_payload_for_the_same_event(self, spec_id: str, tmp_path: Path) -> None:
        twin = build_spec(spec_id, tmp_path).claude_twin
        assert twin["hook_event_name"] == SPEC_CLAUDE_EVENT[spec_id]
        assert not set(twin) & {"workspace_roots", "cursor_version", "conversation_id", "generation_id"}
        assert twin["cwd"] == str(tmp_path)

    def test_the_cursor_payloads_never_hold_the_harness_session_id_under_the_translated_name(self, tmp_path: Path) -> None:
        for spec_id in SPEC_IDS:
            spec = build_spec(spec_id, tmp_path)
            if "session_id" in spec.cursor:
                assert spec.cursor["session_id"] != spec.claude_twin["session_id"]


# --- what a hook receives ---

# Cursor's top-level keys that no translation carries over, per spec. A literal, so a field-table.json edit alone
# cannot make the test pass.
_DROPPED_EVERYWHERE = {"conversation_id", "generation_id", "model", "cursor_version", "workspace_roots", "user_email"}
EXPECTED_DROPPED = {
    "shell_pre": _DROPPED_EVERYWHERE,
    "shell_pre_child": _DROPPED_EVERYWHERE | {"parent_agent_id"},
    "shell_before_execution": _DROPPED_EVERYWHERE | {"command", "sandbox"},
    "shell_post": _DROPPED_EVERYWHERE | {"tool_output", "duration"},
    "read_post": _DROPPED_EVERYWHERE | {"tool_output", "duration"},
    "grep_post": _DROPPED_EVERYWHERE | {"tool_output", "duration"},
    "read_pre": _DROPPED_EVERYWHERE,
    "read_before_file": _DROPPED_EVERYWHERE | {"file_path", "content", "attachments"},
    "write_create": _DROPPED_EVERYWHERE,
    "write_edit": _DROPPED_EVERYWHERE,
    "write_post": _DROPPED_EVERYWHERE | {"tool_output", "duration"},
    "delete_pre": _DROPPED_EVERYWHERE,
    "grep_pre": _DROPPED_EVERYWHERE,
    "task_pre": _DROPPED_EVERYWHERE,
    "prompt_submit": _DROPPED_EVERYWHERE | {"attachments"},
    "session_start": _DROPPED_EVERYWHERE | {"is_background_agent", "composer_mode"},
    "subagent_start": _DROPPED_EVERYWHERE
    | {
        "subagent_id",
        "subagent_model",
        "task",
        "tool_call_id",
        "is_parallel_worker",
        "parent_conversation_id",
        "parent_agent_id",
        "subagent_type",
    },
    "subagent_stop": _DROPPED_EVERYWHERE
    | {
        "subagent_type",
        "status",
        "task",
        "duration_ms",
        "tool_call_count",
        "message_count",
        "child_conversation_id",
        "parent_agent_id",
    },
    "stop": _DROPPED_EVERYWHERE | {"status", "loop_count"},
    "after_agent_response": _DROPPED_EVERYWHERE | {"text"},
}
# Keys the translation adds that the Cursor payload did not carry at the top level, per spec.
EXPECTED_ADDED = {
    "shell_pre": set(),
    "shell_pre_child": set(),
    "shell_before_execution": {"session_id", "tool_name", "tool_input"},
    "shell_post": {"tool_response"},
    "read_post": {"tool_response"},
    "grep_post": {"tool_response"},
    "read_pre": set(),
    "read_before_file": {"session_id", "cwd", "tool_name", "tool_input"},
    "write_create": set(),
    "write_edit": set(),
    "write_post": {"tool_response"},
    "delete_pre": set(),
    "grep_pre": set(),
    "task_pre": set(),
    "prompt_submit": {"session_id", "cwd"},
    "session_start": {"cwd", "source"},
    "subagent_start": {"session_id", "cwd", "agent_type", "agent_id"},
    "subagent_stop": {"session_id", "cwd", "agent_type"},
    "stop": {"session_id", "cwd"},
    "after_agent_response": {"session_id", "cwd", "last_assistant_message", "prompt_id"},
}
# Fields the table renames or transforms, compared by value.
COMPARED_BY_VALUE = ("hook_event_name", "session_id", "cwd", "tool_name", "tool_input", "tool_response")


# The probe runs `write_edit` under an Edit-selecting matcher, which pairs with that spec's literal `Edit` twin.
EDIT_SELECTING_MATCHER = "Edit|Write|MultiEdit"


def _run_probe(tmp_path: Path, spec_id: str) -> tuple[dict, dict]:
    root = tmp_path.resolve()
    (root / "src").mkdir(exist_ok=True)
    env = make_shim_env(tmp_path)
    spec = build_spec(spec_id, root)
    for path, text in spec.setup_files.items():
        path.write_text(text)
    saved = tmp_path / "saved.json"
    script = recording_probe_script(tmp_path / "probe.sh", saved)
    outside = tmp_path / "outside"
    outside.mkdir()
    matcher = EDIT_SELECTING_MATCHER if spec_id == "write_edit" else None
    result = run_shim(env, spec.cursor, hook_args(SPEC_CLAUDE_EVENT[spec_id], script=script, matcher=matcher), cwd=outside)
    assert result.returncode == 0, result.stderr
    assert saved.exists(), result.stdout
    return spec.cursor, json.loads(saved.read_text())


class TestTranslatedPayload:
    @pytest.mark.parametrize("spec_id", SPEC_IDS)
    def test_the_probe_receives_the_twin_for_every_renamed_or_transformed_field(self, spec_id: str, tmp_path: Path) -> None:
        spec = build_spec(spec_id, tmp_path.resolve())
        _, received = _run_probe(tmp_path, spec_id)
        for field in COMPARED_BY_VALUE:
            assert received.get(field) == spec.claude_twin.get(field), (spec_id, field)

    @pytest.mark.parametrize("spec_id", SPEC_IDS)
    def test_the_probe_receives_exactly_the_twins_keys(self, spec_id: str, tmp_path: Path) -> None:
        spec = build_spec(spec_id, tmp_path.resolve())
        _, received = _run_probe(tmp_path, spec_id)
        assert received == spec.claude_twin

    @pytest.mark.parametrize("spec_id", SPEC_IDS)
    def test_the_dropped_and_added_keys_are_the_per_event_literals(self, spec_id: str, tmp_path: Path) -> None:
        cursor, received = _run_probe(tmp_path, spec_id)
        dropped = set(cursor) - set(received)
        added = set(received) - set(cursor)
        assert dropped == EXPECTED_DROPPED[spec_id]
        assert added == EXPECTED_ADDED[spec_id]

    def test_a_post_tool_use_write_over_an_existing_file_stays_a_write_with_its_content(self, tmp_path: Path) -> None:
        (written,) = build_spec("write_post", tmp_path.resolve()).setup_files
        _, received = _run_probe(tmp_path, "write_post")
        assert written.exists(), "the file must exist, or the overwrite probe has nothing to fire on"
        assert received["tool_name"] == "Write"
        assert received["tool_input"] == {"file_path": str(written), "content": "new text"}

    def test_a_childs_calls_take_the_parents_session_id(self, tmp_path: Path) -> None:
        spec = build_spec("shell_pre_child", tmp_path.resolve())
        _, received = _run_probe(tmp_path, "shell_pre_child")
        assert received["session_id"] == PARENT_CONVERSATION_ID == spec.claude_twin["session_id"]
        assert spec.cursor["conversation_id"] != PARENT_CONVERSATION_ID

    @pytest.mark.parametrize("carried", [None, "", "null"], ids=["absent", "empty", "null"])
    def test_an_absent_null_or_empty_parent_agent_id_is_not_carried(self, tmp_path: Path, carried: str | None) -> None:
        root = tmp_path.resolve()
        env = make_shim_env(tmp_path)
        payload = build_spec("shell_pre", root).cursor
        if carried == "null":
            payload["parent_agent_id"] = None
        elif carried == "":
            payload["parent_agent_id"] = ""
        saved = tmp_path / "saved.json"
        script = recording_probe_script(tmp_path / "probe.sh", saved)
        run_shim(env, payload, hook_args("PreToolUse", script=script), cwd=tmp_path)
        assert json.loads(saved.read_text())["session_id"] == CONVERSATION_ID

    @pytest.mark.parametrize(
        "bad_value",
        [7, "x" * 85 + "\n", "a" * 85 + ".", "has space", ["list"], True, False, 0, [], {}],
        ids=["number", "newline-suffix", "dot", "space", "list", "true", "false", "zero", "empty-list", "empty-object"],
    )
    def test_a_parent_agent_id_that_fails_the_grammar_is_not_replaced_by_the_conversation_id(
        self, tmp_path: Path, bad_value: object
    ) -> None:
        root = tmp_path.resolve()
        env = make_shim_env(tmp_path)
        payload = build_spec("shell_pre", root).cursor
        payload["parent_agent_id"] = bad_value
        saved = tmp_path / "saved.json"
        script = recording_probe_script(tmp_path / "probe.sh", saved)
        output = shim_json(run_shim(env, payload, hook_args("PreToolUse", script=script), cwd=tmp_path))
        assert not saved.exists()
        assert output["permission"] == "deny"
        assert "parent_agent_id" in output["agent_message"]
        assert "session-id-grammar" in output["agent_message"]


# bash sets the first four itself. OUT is the probe's own output prefix, which differs between the two runs on purpose.
_ENV_NOISE = {"_", "SHLVL", "PWD", "OLDPWD", "OUT"}


class TestShimEnvelope:
    PROBE = (
        'printf "%s\\n" "umask=$(umask)" "cwd=$(pwd -P)" > "$OUT.meta"\n'
        '[ -p /dev/stdin ] && echo "stdin=pipe" >> "$OUT.meta"\n'
        '[ -f /dev/stdin ] && echo "stdin=file" >> "$OUT.meta"\n'
        'cat > "$OUT.stdin"\nenv -0 > "$OUT.env"\n'
    )
    LOCALE_NAMES = ("LANG", "LC_ALL", "LC_CTYPE", "__CF_USER_TEXT_ENCODING")
    # The values the interpreter's C-locale coercion may give LC_CTYPE for the hook.
    UTF_8_LC_CTYPE_VALUES = ("C.UTF-8", "C.utf8", "UTF-8")

    def _read(self, out: Path) -> tuple[dict, bytes, dict]:
        meta = dict(line.split("=", 1) for line in Path(f"{out}.meta").read_text().splitlines())
        environment = dict(
            entry.split("=", 1) for entry in Path(f"{out}.env").read_bytes().decode().split("\0") if entry and "=" in entry
        )
        return meta, Path(f"{out}.stdin").read_bytes(), environment

    def _run_pair(self, tmp_path: Path, start_overrides: dict, drop_names: tuple[str, ...] = ()) -> dict:
        """Runs the probe under the shim and directly, from one starting environment. Returns what each run saw."""
        root = tmp_path.resolve()
        (root / "src").mkdir()
        env = make_shim_env(tmp_path)
        spec = build_spec("shell_pre", root)
        probe = write_hook_script(tmp_path / "probe.sh", self.PROBE)
        outside = tmp_path / "outside"
        outside.mkdir()
        start_env = shim_process_env(
            env,
            {
                "CLAUDECODE": "1",
                "CLAUDE_CODE_ENTRYPOINT": "cli",
                "CLAUDE_PID": "123",
                "CUSTOM_MARKER": "kept",
                "CLAUDE_CONFIG_DIR": "/not/the/routed/dir",
            },
        )
        for name in drop_names:
            start_env.pop(name, None)
        start_env.update(start_overrides)
        shim_out = tmp_path / "shim"
        run_env = {**start_env, "OUT": str(shim_out)}
        assert_outside_any_repository(outside, run_env)
        shim_result = subprocess.run(
            shim_argv(*hook_args("PreToolUse", script=probe)),
            input=json.dumps(spec.cursor).encode(),
            capture_output=True,
            check=False,
            cwd=outside,
            env=run_env,
        )
        assert shim_result.returncode == 0, shim_result.stderr
        shim_meta, shim_stdin, shim_environment = self._read(shim_out)

        record = json.loads(env.record_path.read_text())
        tool_dirs = []
        for path in record["tool_paths"].values():
            directory = str(Path(path).parent)
            if directory not in tool_dirs:
                tool_dirs.append(directory)
        record_path_value = ":".join([*tool_dirs, "/usr/bin", "/bin", "/usr/sbin", "/sbin"])

        direct_out = tmp_path / "direct"
        direct_env = {k: v for k, v in start_env.items() if k not in CLAUDE_PROCESS_ENV_NAMES}
        direct_env.update({"OUT": str(direct_out), "PATH": record_path_value, "CLAUDE_CONFIG_DIR": str(env.config_dir)})
        direct = subprocess.Popen([str(probe)], stdin=subprocess.PIPE, cwd=root, env=direct_env)
        direct.communicate(shim_stdin)
        direct_meta, direct_stdin, direct_environment = self._read(direct_out)
        return {
            "root": root,
            "env": env,
            "record_path": record_path_value,
            "shim": (shim_meta, shim_stdin, shim_environment),
            "direct": (direct_meta, direct_stdin, direct_environment),
        }

    def test_the_shim_and_a_direct_run_see_the_same_cwd_umask_stdin_and_environment_apart_from_the_listed_exceptions(
        self, tmp_path: Path
    ) -> None:
        pair = self._run_pair(tmp_path, {})
        (shim_meta, shim_stdin, shim_environment) = pair["shim"]
        (direct_meta, direct_stdin, direct_environment) = pair["direct"]
        assert shim_meta["cwd"] == direct_meta["cwd"] == str(pair["root"])
        assert shim_meta["umask"] == direct_meta["umask"]
        assert shim_meta["stdin"] == "pipe"
        assert direct_meta["stdin"] == "pipe"
        assert shim_stdin == direct_stdin
        assert not set(CLAUDE_PROCESS_ENV_NAMES) & set(shim_environment)
        assert shim_environment["PATH"] == pair["record_path"]
        assert shim_environment["CLAUDE_CONFIG_DIR"] == str(pair["env"].config_dir)
        assert "CLAUDE_CONFIG_ADAPTER_SESSION_ID" not in shim_environment
        # The interpreter may add or change the locale names its C-locale coercion owns, which the pairs below pin.
        shim_compared = {k: v for k, v in shim_environment.items() if k not in _ENV_NOISE and k != "LC_CTYPE"}
        direct_compared = {k: v for k, v in direct_environment.items() if k not in _ENV_NOISE and k != "LC_CTYPE"}
        assert shim_compared == direct_compared

    @pytest.mark.parametrize(
        "locale_variables",
        [{}, {"LC_CTYPE": "C"}, {"LANG": "C"}],
        ids=["no-locale-variable", "lc-ctype-c", "lang-c"],
    )
    def test_the_c_locale_pairs_differ_only_by_the_interpreters_utf_8_lc_ctype(
        self, tmp_path: Path, locale_variables: dict
    ) -> None:
        """Under a C locale the interpreter's coercion gives the hook a UTF-8 LC_CTYPE the direct run lacks. Any other
        new or changed name fails the pair."""
        pair = self._run_pair(tmp_path, locale_variables, drop_names=self.LOCALE_NAMES)
        shim_environment = pair["shim"][2]
        direct_environment = pair["direct"][2]
        assert shim_environment.get("LC_CTYPE") in self.UTF_8_LC_CTYPE_VALUES
        if platform.system() == "Darwin":
            assert "__CF_USER_TEXT_ENCODING" in shim_environment
        allowed_to_differ = {"LC_CTYPE", "__CF_USER_TEXT_ENCODING"}
        shim_compared = {k: v for k, v in shim_environment.items() if k not in _ENV_NOISE | allowed_to_differ}
        direct_compared = {k: v for k, v in direct_environment.items() if k not in _ENV_NOISE | allowed_to_differ}
        assert shim_compared == direct_compared


# --- the matrix ---


@dataclass(frozen=True)
class Registration:
    event: str
    matcher: str
    script: str
    hook_class: str

    @property
    def key(self) -> str:
        return registration_key(self.event, self.matcher, self.script)

    @property
    def path(self) -> Path:
        return HOOKS_DIR / self.script

    @property
    def matcher_tokens(self) -> set[str]:
        return set(self.matcher.split("|"))


def _registrations() -> list[Registration]:
    seen: dict[str, Registration] = {}
    for event, matcher, script in settings_registrations():
        registration = Registration(event, matcher, script, _hook_class(HOOKS_DIR / script) or "")
        seen.setdefault(registration.key, registration)
    return list(seen.values())


ALL_REGISTRATIONS = _registrations()
GATE_REGISTRATIONS = [r for r in ALL_REGISTRATIONS if r.hook_class in GATE_CLASSES]
INFORMATIONAL_REGISTRATIONS = [r for r in ALL_REGISTRATIONS if r.hook_class == "informational"]


def _exceptions() -> dict[str, dict]:
    return {e["key"]: e for e in load_json(PARITY_EXCEPTIONS_JSON)["entries"] if e["kind"] == "registration"}


EXCEPTIONS = _exceptions()

# The registrations that have a delivered shim arm, written out so the meta-tests below compare two lists that are not
# derived from each other.
DELIVERED_GATE_ARMS = frozenset(
    {
        "PreToolUse|Bash|require-code-review.sh",
        "PreToolUse|Bash|require-diff-size-prompt.sh",
        "PreToolUse|Bash|guard-settings-session-keys.sh",
        "PreToolUse|Bash|deny-private-project-refs.sh",
        "PreToolUse|Bash|require-stow-reminder.sh",
        "PreToolUse|Bash|deny-escaped-backticks-in-pr-body.sh",
        "PreToolUse|Bash|block-gh-pr-merge.sh",
        "PreToolUse|Bash|require-respond-pr.sh",
        "PreToolUse|Bash|check-skill-length.sh",
        "PreToolUse|Bash|check-claude-md-length.sh",
        "PreToolUse|Bash|require-ready-for-review.sh",
        "PreToolUse|Bash|deny-pii-in-commits.sh",
        "PreToolUse|Bash|deny-invisible-commit-content.sh",
        "PreToolUse|Bash|deny-credential-bash-reads.sh",
        "PreToolUse|Bash|enforce-marker-script-shape.sh",
        "PreToolUse|Bash|deny-reviewer-tree-mutation.sh",
        "PreToolUse|Bash|deny-repo-relocation.sh",
        "PreToolUse|Bash|deny-network-installs.sh",
        "PreToolUse|Bash|deny-default-branch-push.sh",
        "PreToolUse|Edit|Write|MultiEdit|require-plan-review.sh",
        "PreToolUse|Edit|Write|MultiEdit|require-memory-skill.sh",
        "PreToolUse|Edit|Write|MultiEdit|deny-reviewer-tree-mutation.sh",
        "PreToolUse|Edit|Write|MultiEdit|deny-plan-writer-off-plan-writes.sh",
        "PreToolUse|Edit|Write|MultiEdit|enforce-marker-script-shape.sh",
        "PreToolUse|Agent|require-routing-read.sh",
        "PreToolUse|Agent|Task|require-architect-consult.sh",
        "PreToolUse|Agent|Task|deny-no-op-dispatch.sh",
        "PreToolUse|Read|deny-env-reads.sh",
        "PreToolUse|Read|deny-data-file-reads.sh",
        "PreToolUse|Read|deny-credential-file-reads.sh",
    }
)

DELIVERED_INFORMATIONAL_ARMS = frozenset(
    {
        "SessionStart|startup|clear|compact|resume|session-marker-dashboard.sh",
        "SessionStart|startup|check-branch-divergence.sh",
        "SessionStart|startup|set-session-title-from-branch.sh",
        "SessionStart|startup|bootstrap-project-plugins.sh",
        "SessionStart|startup|nudge-memory-store-audit.sh",
        "SessionStart|startup|resume|clear|announce-autonomous-shipping.sh",
        "SessionStart|*|record-session-config-keys.sh",
        "SubagentStart|plan-architect|announce-branch-review-history.sh",
        "UserPromptSubmit|*|nudge-error-mode-analysis.sh",
        "UserPromptSubmit|*|nudge-worktree-anchor.sh",
        "UserPromptSubmit|*|nudge-unexpanded-skill-mention.sh",
        "PreToolUse|Edit|Write|MultiEdit|ask-review-permissions.sh",
        "PreToolUse|Edit|Write|MultiEdit|ask-new-dependency-disclosure.sh",
        "PostToolUse|Read|log-routing-read.sh",
        "PostToolUse|Read|consume-durable-continuity-file-on-read.sh",
        "PostToolUse|Bash|Read|WebFetch|Grep|Task|redact-credential-values.sh",
        "PostToolUse|Bash|announce-approved-plan-path.sh",
        "PostToolUse|Agent|Task|log-reviewer-round.sh",
        "PostToolUse|Edit|Write|MultiEdit|nudge-transcript-toolkit.sh",
        "PostToolUse|Edit|Write|MultiEdit|announce-resume-command.sh",
    }
)
SHIM_ARM_REGISTRATIONS = [r for r in GATE_REGISTRATIONS if r.key in DELIVERED_GATE_ARMS]
INFORMATIONAL_ARM_REGISTRATIONS = [r for r in INFORMATIONAL_REGISTRATIONS if r.key in DELIVERED_INFORMATIONAL_ARMS]
ARM_REGISTRATIONS = [*SHIM_ARM_REGISTRATIONS, *INFORMATIONAL_ARM_REGISTRATIONS]


def _gate_label(script: Path) -> str:
    match = re.search(r'^DENY_GATE_LABEL="([^"]+)"', script.read_text(), re.MULTILINE)
    return match.group(1) if match else script.stem


# Time values are masked before a file is hashed, so two runs that write the same line at different seconds compare equal.
# No id is normalized.
TIME_VALUE_PATTERN = re.compile(rb"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


class World:
    """The directories a hook can write, rebuilt from one pristine copy before each run so two runs share absolute paths."""

    def __init__(self, base: Path) -> None:
        self.base = base
        self.root = base / "world"
        self.pristine = base / "pristine"
        self.home = self.root / "home"
        self.config = self.root / "config"
        self.tmp = self.root / "tmp"
        self.repo = self.root / "repo"
        self.stubs = self.root / "stubs"
        self.outside = base / "outside"
        self.outside.mkdir()
        for directory in (self.home, self.config, self.tmp, self.repo, self.stubs):
            directory.mkdir(parents=True)
        # `install` creates the state dir, 0700, and the shim only logs into it.
        (self.config / "cursor-adapter-state").mkdir(mode=0o700)
        self.home = self.home.resolve()
        self.config = self.config.resolve()
        self.tmp = self.tmp.resolve()
        self.repo = self.repo.resolve()
        subprocess.run(
            ["git", "init", "-q", str(self.repo)], check=True, capture_output=True, env=without_git_environment(os.environ)
        )
        (self.repo / "src").mkdir()
        (self.repo / "README.md").write_text("fixture\n")
        # A second repository that is no workspace root, with a clean tree, for the leading-cd rows.
        clean_repo = base / "clean-repo"
        clean_repo.mkdir()
        self.clean_repo = clean_repo.resolve()
        subprocess.run(
            ["git", "init", "-q", str(self.clean_repo)], check=True, capture_output=True, env=without_git_environment(os.environ)
        )
        (self.clean_repo / "README.md").write_text("clean\n")
        self.plain_directory = base / "plain-directory"
        self.plain_directory.mkdir()
        self._write_stubs()
        self._write_install_record()
        shutil.copytree(self.root, self.pristine, symlinks=True)

    def _write_stubs(self) -> None:
        log = self.stubs / "calls.log"
        real_git = shutil.which("git")
        for name in ("curl", "wget", "gh", "nc", "ssh", "scp", "rsync", "detached-probe-command"):
            stub = self.stubs / name
            stub.write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\ntouch "{self.stubs}/sentinel-{name}"\nexit 0\n')
            stub.chmod(stat.S_IRWXU)
        git_stub = self.stubs / "git"
        git_stub.write_text(
            "#!/bin/sh\n"
            f'case "$*" in *fetch*|*pull*|*push*|*clone*|*ls-remote*) echo "git $*" >> "{log}"; exit 0;; esac\n'
            f'exec "{real_git}" "$@"\n'
        )
        git_stub.chmod(stat.S_IRWXU)

    def reset(self) -> None:
        shutil.rmtree(self.root)
        shutil.copytree(self.pristine, self.root, symlinks=True)

    def apply_setup(self, setup: Callable[[World], None] | None) -> None:
        """Run `setup` on the live tree, then make the result the pristine copy every later reset restores."""
        if setup is None:
            return
        setup(self)
        shutil.rmtree(self.pristine)
        shutil.copytree(self.root, self.pristine, symlinks=True)

    @property
    def watched(self) -> dict[str, Path]:
        return {"home": self.home, "config": self.config, "tmp": self.tmp, "repo": self.repo}

    def snapshot(self, ignore_state: bool) -> dict[str, str]:
        files: dict[str, str] = {}
        for label, directory in self.watched.items():
            for path in sorted(directory.rglob("*")):
                relative = path.relative_to(self.root)
                # The state dir itself exists in every pristine world, as `install` creates it. Its files are the shim's log.
                if (
                    ".git/index" in str(relative)
                    or relative.name == "cursor-adapter-state"
                    or (ignore_state and "cursor-adapter-state" in relative.parts)
                ):
                    continue
                if path.is_file() and not path.is_symlink():
                    files[f"{label}:{relative}"] = hashlib.sha256(
                        TIME_VALUE_PATTERN.sub(b"<time>", path.read_bytes())
                    ).hexdigest()
                else:
                    files[f"{label}:{relative}"] = "dir" if path.is_dir() else "link"
        return files

    def network_calls(self) -> list[str]:
        log = self.stubs / "calls.log"
        return log.read_text().splitlines() if log.exists() else []

    def cursor_env(self, extra: dict | None = None) -> dict:
        """The environment of a Cursor hook: no Claude Code marker, no GIT_* name, and the stubs first on PATH."""
        env = without_git_environment(
            {k: v for k, v in os.environ.items() if k not in CLAUDE_PROCESS_ENV_NAMES and k != "CLAUDE_CONFIG_DIR"}
        )
        env.update(
            {
                "HOME": str(self.home),
                "TMPDIR": str(self.tmp),
                "CURSOR_PROJECT_DIR": str(self.repo),
                "CLAUDE_PROJECT_DIR": str(self.repo),
                "CURSOR_VERSION": "0.0.0",
                "PATH": f"{self.stubs}:{os.environ['PATH']}",
            }
        )
        env.update(extra or {})
        return env

    def _write_install_record(self) -> None:
        """The record names the stubs, so the PATH the shim fixes from it holds them."""
        stub_paths = {p.name: str(p) for p in self.stubs.iterdir() if p.is_file() and p.name not in ("calls.log",)}
        tool_paths = {**stub_paths, **{k: v for k, v in recorded_tool_paths().items() if k not in stub_paths}}
        record_dir = self.home / ".cursor" / "cursor-adapter"
        record_dir.mkdir(parents=True)
        self.record_path = record_dir / "install.json"
        self.record_path.write_text(
            json.dumps(
                {
                    "routed_config_dir": str(self.config),
                    "contract_version": CONTRACT_VERSION,
                    "tool_paths": tool_paths,
                    "tool_map": load_json(TOOL_MAP_JSON),
                }
            )
        )

    def shim_env(self) -> ShimEnv:
        return ShimEnv(home=self.home, config_dir=self.config, record_path=self.record_path)


def assert_no_effects(
    before: dict[str, str],
    after: dict[str, str],
    world: World,
    listed_writes: tuple[str, ...] = (),
    listed_network: bool = False,
) -> None:
    """Fail on a write the row does not list, a network call or a spawn. A row lists writes as regular expressions on the key."""
    changed = sorted(
        key
        for key in set(before) | set(after)
        if before.get(key) != after.get(key) and not any(re.fullmatch(pattern, key) for pattern in listed_writes)
    )
    assert not changed, f"unlisted writes: {changed}"
    if not listed_network:
        assert world.network_calls() == [], f"network or spawn calls: {world.network_calls()}"


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


# --- triggers ---


def _const(value: dict) -> Callable[[World], dict]:
    return lambda world: value


def _stage_a_change(world: World) -> None:
    (world.repo / "README.md").write_text("fixture\nstaged line\n")
    subprocess.run(
        ["git", "-C", str(world.repo), "add", "README.md"],
        check=True,
        capture_output=True,
        env=without_git_environment(os.environ),
    )


def _memory_path(world: World, name: str) -> str:
    """A path under the routed config dir's projects directory, where the gate looks. Raw arms have no routed dir."""
    return str(world.config / "projects" / "placeholder-project" / "memory" / name)


def _write_memory_index(world: World) -> None:
    index = Path(_memory_path(world, "MEMORY.md"))
    index.parent.mkdir(parents=True)
    index.write_text("- index\n")


@dataclass(frozen=True)
class Trigger:
    """A deny trigger modeled on the gate's own tests, and a near miss the gate must let through.

    `overwrites` is the text of the file a Write replaces, and `setup` builds the state the trigger needs.
    """

    name: str
    tool: str
    deny_input: Callable[[World], dict]
    near_miss_input: Callable[[World], dict]
    raw_class: str = "no-op"
    setup: Callable[[World], None] | None = None
    overwrites: str | None = None

    @property
    def tool_class(self) -> str:
        if self.tool == "Write":
            return "Write-overwrite" if self.overwrites is not None else "Write-create"
        return self.tool


def _bash(command: str) -> dict:
    return {"command": command}


def _read(path: str) -> dict:
    return {"file_path": path}


# Each trigger's command or path is modeled on a deny case in the gate's own test file.
TRIGGERS: dict[str, tuple[Trigger, ...]] = {
    "block-gh-pr-merge.sh": (
        Trigger("merge", "Shell", _const(_bash("gh pr merge 291 --squash")), _const(_bash("gh pr view 291"))),
    ),
    "deny-credential-bash-reads.sh": (
        Trigger("cat-credentials", "Shell", _const(_bash("cat /foo/credentials.json")), _const(_bash("cat /foo/notes.txt"))),
    ),
    "deny-default-branch-push.sh": (
        Trigger("push-main", "Shell", _const(_bash("git push origin main")), _const(_bash("git push origin feature"))),
    ),
    "deny-network-installs.sh": (
        Trigger("npm-install", "Shell", _const(_bash("npm install lodash")), _const(_bash("npm run build"))),
    ),
    "deny-repo-relocation.sh": (
        Trigger("mv-repo", "Shell", lambda world: _bash(f"mv {REPO_ROOT} /tmp/elsewhere"), _const(_bash("mv a.txt b.txt"))),
    ),
    "deny-escaped-backticks-in-pr-body.sh": (
        Trigger(
            "escaped-backtick",
            "Shell",
            _const(_bash(r"gh pr create --body 'body with \`escaped\` backticks'")),
            _const(_bash("gh pr create --body 'clean body no escapes'")),
            raw_class="denies",
        ),
    ),
    "require-code-review.sh": (
        Trigger(
            "commit-without-review", "Shell", _const(_bash("git commit -m x")), _const(_bash("git status")), setup=_stage_a_change
        ),
    ),
    "deny-env-reads.sh": (
        Trigger("env-file", "Read", _const(_read("/foo/.env")), _const(_read("/foo/notes.txt")), raw_class="denies"),
    ),
    "deny-credential-file-reads.sh": (
        Trigger(
            "credentials-file",
            "Read",
            _const(_read("/foo/credentials.json")),
            _const(_read("/foo/notes.txt")),
            raw_class="denies",
        ),
    ),
    "require-memory-skill.sh": (
        Trigger(
            "new-topic-file",
            "Write",
            lambda world: {"file_path": _memory_path(world, "new-topic.md"), "content": "topic"},
            lambda world: {"file_path": str(world.repo / "src" / "a.txt"), "content": "text"},
        ),
        Trigger(
            "index-overwrite",
            "Write",
            lambda world: {"file_path": _memory_path(world, "MEMORY.md"), "content": "- index\n- more\n"},
            lambda world: {"file_path": str(world.repo / "src" / "a.txt"), "content": "text"},
            setup=_write_memory_index,
            overwrites="- index\n",
        ),
    ),
    "deny-no-op-dispatch.sh": (
        Trigger(
            "wait-only",
            "Task",
            _const({"description": "wait", "prompt": "wait", "subagent_type": "general-purpose"}),
            _const(
                {
                    "description": "run tests",
                    "prompt": "Run the unit tests for the parser module and report the failures.",
                    "subagent_type": "general-purpose",
                }
            ),
            raw_class="denies",
        ),
    ),
}
TRIGGER_CASES = [(r, t) for r in SHIM_ARM_REGISTRATIONS for t in TRIGGERS.get(r.script, ())]

# Gate scripts whose registration has no trigger above, each with the reason. A new gate, or a new trigger, forces an edit here.
NO_TRIGGER_YET = {
    "require-diff-size-prompt.sh": "denies a commit only when the branch's cumulative diff passes a size threshold; "
    "its roster deny keys on agent_type, which the shim does not forward, so that deny is inert under the shim",
    "guard-settings-session-keys.sh": "denies a commit only when a staged settings.json holds session keys",
    "deny-private-project-refs.sh": "denies only against a populated private-project blocklist",
    "require-stow-reminder.sh": "denies a PR open or edit that adds a stowed path to this repository",
    "require-respond-pr.sh": "denies PR comment fetches and posts until /respond-pr has run, which needs a marker",
    "check-skill-length.sh": "denies a commit only when a staged SKILL.md passes its ceiling",
    "check-claude-md-length.sh": "denies a commit only when a staged CLAUDE.md or AGENTS.md passes its limit",
    "require-ready-for-review.sh": "denies a push, ready or create only against an open PR or a missing completion marker",
    "deny-pii-in-commits.sh": "denies a commit only when its added lines hold personal data in a staged diff",
    "deny-invisible-commit-content.sh": "denies a commit only when its staged content holds invisible characters",
    "enforce-marker-script-shape.sh": "denies only a marker write or script of the wrong shape, which needs marker state; "
    "its subagent bars key on agent_type and agent_id, which the shim does not forward, so they are inert under the shim",
    "deny-reviewer-tree-mutation.sh": "keys on agent_type, which Cursor payloads do not carry until agent attribution lands",
    "require-plan-review.sh": "denies a Write only while a modified plan awaits review, which needs plan state",
    "deny-plan-writer-off-plan-writes.sh": "keys on agent_type, which Cursor payloads do not carry until attribution lands; "
    "the name outcomes run through the real gate with an injected agent_type in TestPlanWriterGateNameOutcomes",
    "require-routing-read.sh": "denies only during an active plan-review session, which needs session marker state",
    "require-architect-consult.sh": "denies a reviewer-persona dispatch only on a branch with plan state",
    "deny-data-file-reads.sh": "denies only a data-shaped file read, which needs a data file on disk",
}
# Translated Cursor tool classes that no gate trigger covers, each with the reason.
NO_TRIGGER_TOOL_CLASSES = {
    "Delete": "the only gates that inspect an rm key on agent_type, and Delete becomes `rm -- <path>` under Bash",
}
TRIGGER_TOOL_CLASSES = {"Shell", "Read", "Write-create", "Write-overwrite", "Task"}


def _commit_in_repo(world: World) -> None:
    """A commit on the repo's branch, so a hook that keys state on the branch and HEAD finds both."""
    subprocess.run(
        [
            "git",
            "-C",
            str(world.repo),
            "-c",
            "user.name=fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "fixture",
        ],
        check=True,
        capture_output=True,
        env=without_git_environment(os.environ),
    )


def _install_continuity_file(world: World) -> None:
    """A handoff file under the routed config dir, and the resume-context script the hook runs to consume it."""
    handoff = world.config / "handoffs" / "fixture-handoff.md"
    handoff.parent.mkdir(parents=True)
    handoff.write_text("fixture handoff\n")
    hooks_dir = world.config / "hooks"
    hooks_dir.mkdir()
    symlink_hooks_lib_chain(hooks_dir)
    scripts_dir = world.config / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "resume-context.sh").symlink_to(SCRIPTS_DIR / "resume-context.sh")


def _reviewer_dispatch_payload(registration: Registration, world: World) -> dict:
    payload = _benign_payload(registration, world)
    payload["tool_input"]["subagent_type"] = "staff-sdet"
    return payload


def _read_payload_for(path_of: Callable[[World], str]) -> Callable[[Registration, World], dict]:
    def build(registration: Registration, world: World) -> dict:
        payload = _benign_payload(registration, world)
        payload["tool_input"]["file_path"] = path_of(world)
        return payload

    return build


@dataclass(frozen=True)
class WriteTrigger:
    """A call that takes an informational hook down its write path, and the writes that path makes, as snapshot-key regexes."""

    payload: Callable[[Registration, World], dict]
    writes: tuple[str, ...]
    setup: Callable[[World], None] | None = None


# Each trigger is modeled on a case in the hook's own tests.
INFORMATIONAL_WRITE_TRIGGERS: dict[str, WriteTrigger] = {
    "record-session-config-keys.sh": WriteTrigger(
        lambda registration, world: _benign_payload(registration, world), (r"config:config/\.session-config-keys-log\.jsonl",)
    ),
    "log-reviewer-round.sh": WriteTrigger(
        _reviewer_dispatch_payload, (r"config:config/\.reviewer-round-state\.d(/[^/]+)?",), setup=_commit_in_repo
    ),
    "log-routing-read.sh": WriteTrigger(
        _read_payload_for(lambda world: str(world.repo / "skills" / "plan-review" / "ROUTING.md")),
        (r"config:config/\.plan-review-pending-read\.d(/[^/]+)?",),
    ),
    "consume-durable-continuity-file-on-read.sh": WriteTrigger(
        _read_payload_for(lambda world: str(world.config / "handoffs" / "fixture-handoff.md")),
        (
            r"config:config/handoffs(/[^/]+)?",
            r"tmp:tmp/resume-context\.[A-Za-z0-9]+",
            r"tmp:tmp/resume-context-index-[0-9]+(/consumed\.[0-9-]+\.tsv)?",
        ),
        setup=_install_continuity_file,
    ),
}

# Informational hooks with a delivered arm and no trigger above, each with the write it makes. Closing one forces an edit here.
NO_WRITE_TRIGGER_YET = {
    "session-marker-dashboard.sh": "none found in the hook's source",
    "check-branch-divergence.sh": "a `git fetch` of the default branch, which the PATH stubs record as a network call",
    "set-session-title-from-branch.sh": "none found in the hook's source",
    "bootstrap-project-plugins.sh": "an audit log line, reached by TestBootstrapProjectPluginsRow and by no trigger row",
    "nudge-memory-store-audit.sh": "a nudge log line under the config dir",
    "announce-autonomous-shipping.sh": "none found in the hook's source",
    "announce-branch-review-history.sh": "none found in the hook's source",
    "nudge-error-mode-analysis.sh": "a checkpoint directory, a fired marker and a nudge log line",
    "nudge-worktree-anchor.sh": "a per-session anchor state file",
    "nudge-unexpanded-skill-mention.sh": "none found in the hook's source",
    "ask-review-permissions.sh": "none found in the hook's source",
    "ask-new-dependency-disclosure.sh": "only a temporary helper-output file that the hook removes on exit",
    "redact-credential-values.sh": "none found in the hook's source",
    "announce-approved-plan-path.sh": "none found in the hook's source",
    "nudge-transcript-toolkit.sh": "none found in the hook's source",
    "announce-resume-command.sh": "none found in the hook's source",
}

# Writes a shim arm lists, as regular expressions on the snapshot key. An arm lists nothing unless it appears here.
LISTED_WRITES: dict[str, tuple[str, ...]] = {
    "require-code-review.sh": (r"config:config/\.review-ledger-compliance\.log",),
    **{script: trigger.writes for script, trigger in INFORMATIONAL_WRITE_TRIGGERS.items()},
}
# The two session writers whose registrations the adapter declines. Their import arm still writes until the host-neutral session
# identity lands, and the listing is removed then so that the decline is checked.
DECLINED_SESSION_WRITES: dict[str, tuple[str, ...]] = {
    "capture-session-id.sh": (r"home:home/\.claude(/sessions(/[0-9]+)?)?",),
    "record-session-end.sh": (r"config:config/session-end-records(/[0-9]+)?",),
}
# Rows whose hook may call a stubbed network command.
LISTED_NETWORK: frozenset[str] = frozenset()


# Events with no tool name. Cursor has no Notification or PostToolBatch event, so those two borrow the closest payload.
EVENT_SPEC_IDS = {
    "SessionStart": "session_start",
    "SubagentStart": "subagent_start",
    "SubagentStop": "subagent_stop",
    "UserPromptSubmit": "prompt_submit",
    "Stop": "stop",
    "SessionEnd": "after_agent_response",
    "Notification": "after_agent_response",
    "PostToolBatch": "after_agent_response",
}


def _benign_spec_id(registration: Registration) -> str:
    tokens = registration.matcher_tokens
    if registration.event in EVENT_SPEC_IDS:
        return EVENT_SPEC_IDS[registration.event]
    if "Bash" in tokens:
        return "shell_post" if registration.event == "PostToolUse" else "shell_pre"
    if "Read" in tokens:
        return "read_post" if registration.event == "PostToolUse" else "read_pre"
    if tokens & {"Edit", "Write"}:
        return "write_create"
    if tokens & {"Agent", "Task"}:
        return "task_pre"
    # A tool Cursor has no event for, such as Skill: a Shell call stands in, so the hook still sees a well-formed payload.
    if registration.event in ("PreToolUse", "PostToolUse"):
        return "shell_post" if registration.event == "PostToolUse" else "shell_pre"
    raise AssertionError(f"no benign payload for {registration.key}")


def _benign_payload(registration: Registration, world: World) -> dict:
    """The payload of the registration's event and tool class that no gate has an opinion on."""
    payload = build_spec(_benign_spec_id(registration), world.repo).cursor
    if registration.event == "PostToolUse" and payload["hook_event_name"] == "preToolUse":
        payload["hook_event_name"] = "postToolUse"
        payload["tool_output"] = "done"
    if registration.event in ("SubagentStart", "SubagentStop") and registration.matcher != "*":
        payload["subagent_type"] = registration.matcher
    if registration.event == "SessionEnd":
        payload["hook_event_name"] = "sessionEnd"
    return payload


def _trigger_payload(trigger: Trigger, world: World, *, near_miss: bool = False) -> dict:
    tool_input = (trigger.near_miss_input if near_miss else trigger.deny_input)(world)
    if trigger.tool == "Shell":
        return shell_pre_payload(world.repo, tool_input["command"])
    if trigger.tool == "Read":
        return file_tool_payload(world.repo, "Read", tool_input["file_path"])
    if trigger.tool == "Write":
        return file_tool_payload(world.repo, "Write", tool_input["file_path"], content=tool_input["content"])
    payload = build_spec("task_pre", world.repo).cursor
    payload["tool_input"] = tool_input
    return payload


def _cursor_payload_for(registration: Registration, world: World, trigger: Trigger | None, *, near_miss: bool = False) -> dict:
    if trigger is not None:
        return _trigger_payload(trigger, world, near_miss=near_miss)
    return _benign_payload(registration, world)


def _claude_twin_for(registration: Registration, world: World, trigger: Trigger | None, *, near_miss: bool = False) -> dict:
    if trigger is None:
        return build_spec(_benign_spec_id(registration), world.repo).claude_twin
    tool_input = (trigger.near_miss_input if near_miss else trigger.deny_input)(world)
    twin = {
        "hook_event_name": "PreToolUse",
        "session_id": CONVERSATION_ID,
        "cwd": str(world.repo),
        "tool_use_id": "tool-use-placeholder",
    }
    if trigger.tool == "Shell":
        return {**twin, "tool_name": "Bash", "tool_input": tool_input}
    if trigger.tool == "Read":
        return {**twin, "tool_name": "Read", "tool_input": tool_input}
    if trigger.tool == "Task":
        return {**twin, "tool_name": "Agent", "tool_input": tool_input}
    if near_miss or trigger.overwrites is None:
        return {**twin, "tool_name": "Write", "tool_input": tool_input}
    # An overwrite is `Edit` where the row's Claude matcher selects the name `Edit` (a `MultiEdit`-alone matcher does not), else
    # `Write`; see `write_overwrites` in hook-shim.py. The rest comes from the builder module's `write_edit` twin.
    overwrite_name = (
        "Edit" if _row_matcher(registration) and re.fullmatch(f"(?:{_row_matcher(registration)})", "Edit") else "Write"
    )
    return {**build_spec("write_edit", world.repo).claude_twin, "tool_name": overwrite_name, "tool_input": tool_input}


def _row_matcher(registration: Registration) -> str:
    return registration.matcher if registration.matcher != "*" else ""


def _arm_args(registration: Registration) -> list[str]:
    return hook_args(
        registration.event,
        script=registration.path,
        matcher=_row_matcher(registration) or None,
        label=registration.path.stem,
        fail_closed=registration.hook_class in GATE_CLASSES,
    )


def _run_outside(world: World, command: list[str], payload: dict) -> subprocess.CompletedProcess:
    """Run `command` with the payload on stdin from outside every repository, under Cursor's environment."""
    environment = world.cursor_env()
    assert_outside_any_repository(world.outside, environment)
    return subprocess.run(
        command, input=json.dumps(payload).encode(), capture_output=True, check=False, cwd=world.outside, env=environment
    )


def _shim_arm(world: World, registration: Registration, payload: dict) -> subprocess.CompletedProcess:
    world.reset()
    return _run_outside(world, shim_argv(*_arm_args(registration)), payload)


def _import_arm(world: World, registration: Registration, payload: dict) -> subprocess.CompletedProcess:
    """The raw Cursor payload straight to the script, from outside every repository."""
    return _run_outside(world, [str(registration.path)], payload)


def _direct(world: World, registration: Registration, payload: dict, *, cwd: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(registration.path)], input=json.dumps(payload).encode(), capture_output=True, check=False, cwd=cwd, env=env
    )


def _decision(result: subprocess.CompletedProcess) -> str:
    """allow | deny | ask | other | none for a direct run of a hook script. `none` is exit 0 with empty stdout."""
    if result.returncode == 2:
        return "deny"
    assert result.returncode == 0, (result.returncode, result.stderr)
    if not result.stdout.strip():
        return "none"
    output = json.loads(result.stdout)
    return output.get("hookSpecificOutput", {}).get("permissionDecision", "other")


def _import_decisions(registration: Registration) -> tuple[str, ...]:
    """What an import arm may give: no output or a deny, and for an informational hook its context output too."""
    if registration.hook_class == "informational":
        return ("none", "deny", "ask", "other")
    return ("none", "deny", "ask")


def _reference_reason(result: subprocess.CompletedProcess) -> str:
    """The text a direct run of a hook gives as the reason for its deny."""
    if result.returncode == 2:
        return result.stderr.decode().strip()
    output = json.loads(result.stdout)
    return output.get("hookSpecificOutput", {}).get("permissionDecisionReason") or output.get("reason") or ""


def _listed(registration: Registration, *, import_arm: bool = False) -> tuple[tuple[str, ...], bool]:
    writes = DECLINED_SESSION_WRITES if import_arm else LISTED_WRITES
    return writes.get(registration.script, ()), registration.script in LISTED_NETWORK


def _last_logged_outcome(world: World) -> str:
    return read_log_lines(world.shim_env())[-1]["out"]


class TestGateMatrix:
    def test_rows_are_discovered_by_glob_and_cover_every_gate_class_script(self) -> None:
        scripts = {p.name for p in HOOKS_DIR.glob("*.sh") if _hook_class(p) in GATE_CLASSES}
        assert scripts <= {r.script for r in GATE_REGISTRATIONS}
        assert GATE_REGISTRATIONS and SHIM_ARM_REGISTRATIONS

    def test_the_delivered_arms_and_the_parity_exceptions_are_disjoint(self) -> None:
        assert not (DELIVERED_GATE_ARMS | DELIVERED_INFORMATIONAL_ARMS) & set(EXCEPTIONS)

    def test_every_gate_registration_has_a_delivered_arm_or_a_parity_exception_and_every_listed_arm_exists(self) -> None:
        gate_keys = {r.key for r in GATE_REGISTRATIONS}
        assert DELIVERED_GATE_ARMS | (set(EXCEPTIONS) & gate_keys) == gate_keys
        assert gate_keys >= DELIVERED_GATE_ARMS

    def test_every_informational_registration_has_a_delivered_arm_or_a_parity_exception_and_every_listed_arm_exists(self) -> None:
        keys = {r.key for r in INFORMATIONAL_REGISTRATIONS}
        assert DELIVERED_INFORMATIONAL_ARMS | (set(EXCEPTIONS) & keys) == keys
        assert keys >= DELIVERED_INFORMATIONAL_ARMS

    def test_every_pending_phase_entry_names_a_phase_after_the_delivered_ones(self) -> None:
        for key, entry in EXCEPTIONS.items():
            if entry["reason"] == "pending-phase":
                assert phase_number(entry) > DELIVERED_PHASE_NUMBER, key

    def test_the_registration_key_parser_keeps_a_multi_alternative_matcher_whole(self) -> None:
        for registration in ALL_REGISTRATIONS:
            assert parse_registration_key(registration.key) == (registration.event, registration.matcher, registration.script)

    def test_turn_gate_and_batch_gate_registrations_are_carried_by_later_twins_or_unsupported(self) -> None:
        for registration in GATE_REGISTRATIONS:
            if registration.hook_class in ("turn-gate", "batch-gate") or registration.key.endswith(
                "send-back-skipped-self-review.sh"
            ):
                entry = EXCEPTIONS.get(registration.key)
                assert entry is not None, registration.key
                assert entry["reason"] in ("pending-phase", "unsupported"), registration.key

    def test_every_trigger_names_a_gate_script_with_a_delivered_arm(self) -> None:
        assert set(TRIGGERS) <= {r.script for r in SHIM_ARM_REGISTRATIONS}

    def test_the_gates_with_no_trigger_are_exactly_the_literal_list_with_a_reason_each(self) -> None:
        without_trigger = {r.script for r in SHIM_ARM_REGISTRATIONS} - set(TRIGGERS)
        assert without_trigger == set(NO_TRIGGER_YET)
        assert all(NO_TRIGGER_YET.values())

    def test_the_informational_hooks_with_no_write_trigger_are_exactly_the_literal_list_with_a_write_path_each(self) -> None:
        without_trigger = {r.script for r in INFORMATIONAL_ARM_REGISTRATIONS} - set(INFORMATIONAL_WRITE_TRIGGERS)
        assert without_trigger == set(NO_WRITE_TRIGGER_YET)
        assert all(NO_WRITE_TRIGGER_YET.values())

    def test_every_write_trigger_names_an_informational_hook_with_a_delivered_arm(self) -> None:
        assert set(INFORMATIONAL_WRITE_TRIGGERS) <= {r.script for r in INFORMATIONAL_ARM_REGISTRATIONS}

    def test_only_the_two_declined_session_writers_are_listed_for_the_import_arm(self) -> None:
        assert set(DECLINED_SESSION_WRITES) == {"capture-session-id.sh", "record-session-end.sh"}
        assert {e["key"].rsplit("|", 1)[-1] for e in EXCEPTIONS.values() if e["reason"] == "declined"} == set(
            DECLINED_SESSION_WRITES
        )

    def test_the_trigger_classes_are_the_five_gate_deny_classes_and_delete_is_listed_with_a_reason(self) -> None:
        """Grep and the passthrough tools (WebFetch, WebSearch, `MCP:` names) are in neither set."""
        covered = {trigger.tool_class for triggers in TRIGGERS.values() for trigger in triggers}
        assert covered == TRIGGER_TOOL_CLASSES
        assert set(NO_TRIGGER_TOOL_CLASSES) == {"Delete"}
        translated = {entry["translation"] for entry in load_json(TOOL_MAP_JSON)["tools"].values()}
        assert {"shell", "read", "write", "delete", "task"} <= translated

    @pytest.mark.parametrize("registration", ALL_REGISTRATIONS, ids=lambda r: r.key)
    def test_the_import_arm_never_allows_and_leaves_no_effect(self, world: World, registration: Registration) -> None:
        triggers = TRIGGERS.get(registration.script, ())
        for trigger in triggers or (None,):
            world.reset()
            world.apply_setup(trigger.setup if trigger else None)
            payload = _cursor_payload_for(registration, world, trigger)
            world.reset()
            before = world.snapshot(ignore_state=False)
            result = _import_arm(world, registration, payload)
            assert _decision(result) in _import_decisions(registration), (registration.key, result.stdout)
            writes, network = _listed(registration, import_arm=True)
            assert_no_effects(before, world.snapshot(ignore_state=False), world, writes, network)

    @pytest.mark.parametrize(("registration", "trigger"), TRIGGER_CASES, ids=lambda case: getattr(case, "key", None) or case.name)
    def test_the_raw_arm_from_inside_the_repo_never_allows_and_leaves_no_effect(
        self, world: World, registration: Registration, trigger: Trigger
    ) -> None:
        world.apply_setup(trigger.setup)
        payload = _cursor_payload_for(registration, world, trigger)
        world.reset()
        before = world.snapshot(ignore_state=False)
        result = _direct(world, registration, payload, cwd=world.repo, env=world.cursor_env())
        decision = _decision(result)
        assert decision == ("deny" if trigger.raw_class == "denies" else "none"), (registration.script, trigger.name)
        if decision == "none":
            assert result.returncode == 0 and result.stdout.strip() == b"", (registration.script, trigger.name)
        assert_no_effects(before, world.snapshot(ignore_state=False), world)

    @pytest.mark.parametrize(("registration", "trigger"), TRIGGER_CASES, ids=lambda case: getattr(case, "key", None) or case.name)
    def test_the_shim_arm_denies_with_the_gates_own_label(
        self, world: World, registration: Registration, trigger: Trigger
    ) -> None:
        world.apply_setup(trigger.setup)
        before_world = world.snapshot(ignore_state=False)
        result = _shim_arm(world, registration, _cursor_payload_for(registration, world, trigger))
        assert result.returncode == 0, result.stderr
        output = json.loads(result.stdout)
        label = _gate_label(registration.path)
        assert output["permission"] == "deny"
        assert f"Blocked by {label} gate:" in output["agent_message"]
        assert output["agent_message"] == output["user_message"]
        writes, network = _listed(registration)
        assert_no_effects(before_world, world.snapshot(ignore_state=True), world, writes, network)
        world.reset()
        reference = _direct(
            world,
            registration,
            _claude_twin_for(registration, world, trigger),
            cwd=world.repo,
            env=world.cursor_env({"CLAUDE_CONFIG_DIR": str(world.config)}),
        )
        reference_reason = _reference_reason(reference)
        assert reference_reason, (registration.script, trigger.name)
        assert output["agent_message"] == reference_reason, (registration.script, trigger.name)

    @pytest.mark.parametrize(("registration", "trigger"), TRIGGER_CASES, ids=lambda case: getattr(case, "key", None) or case.name)
    def test_the_shim_arms_effects_equal_a_reference_run_on_the_twin(
        self, world: World, registration: Registration, trigger: Trigger
    ) -> None:
        world.apply_setup(trigger.setup)
        _shim_arm(world, registration, _cursor_payload_for(registration, world, trigger))
        shim_effects = world.snapshot(ignore_state=True)
        world.reset()
        reference = _direct(
            world,
            registration,
            _claude_twin_for(registration, world, trigger),
            cwd=world.repo,
            env=world.cursor_env({"CLAUDE_CONFIG_DIR": str(world.config)}),
        )
        assert _decision(reference) == "deny"
        assert world.snapshot(ignore_state=True) == shim_effects

    @pytest.mark.parametrize(("registration", "trigger"), TRIGGER_CASES, ids=lambda case: getattr(case, "key", None) or case.name)
    def test_the_near_miss_arm_prints_empty_output(self, world: World, registration: Registration, trigger: Trigger) -> None:
        world.apply_setup(trigger.setup)
        result = _shim_arm(world, registration, _cursor_payload_for(registration, world, trigger, near_miss=True))
        assert json.loads(result.stdout) == {}
        # A skipped call prints `{}` too, so the `ok` log line is what shows the hook ran and had no opinion.
        assert _last_logged_outcome(world) == "ok"

    def test_an_overwrite_of_an_existing_memory_topic_file_is_an_edit_the_class_b_gate_leaves_alone(self, world: World) -> None:
        """Class (b) of the memory gate is Write-only and needs a missing file, so it denies the new topic file above and is
        silent here, where the shim names the overwrite Edit. The shim's stat and the hook's existence test are two reads of
        the file, not one atomic step."""
        registration = next(r for r in SHIM_ARM_REGISTRATIONS if r.script == "require-memory-skill.sh")
        topic = _memory_path(world, "topic.md")

        def existing_topic_file(world: World) -> None:
            _write_memory_index(world)
            Path(topic).write_text("old topic")

        world.apply_setup(existing_topic_file)
        payload = file_tool_payload(world.repo, "Write", topic, content="new topic")
        result = _shim_arm(world, registration, payload)
        assert json.loads(result.stdout) == {}
        assert _last_logged_outcome(world) == "ok"
        saved = world.base / "saved.json"
        probe = recording_probe_script(world.base / "probe.sh", saved)
        _run_outside(world, shim_argv(*hook_args("PreToolUse", script=probe, matcher=registration.matcher)), payload)
        assert json.loads(saved.read_text())["tool_name"] == "Edit"

    @pytest.mark.parametrize("registration", [r for r in SHIM_ARM_REGISTRATIONS if r.script not in TRIGGERS], ids=lambda r: r.key)
    def test_a_gate_with_no_trigger_yet_runs_on_a_benign_call_and_has_no_opinion(
        self, world: World, registration: Registration
    ) -> None:
        payload = _cursor_payload_for(registration, world, None)
        before = world.snapshot(ignore_state=False)
        result = _shim_arm(world, registration, payload)
        assert result.returncode == 0, result.stderr
        # `{}` with an `ok` log line shows the hook ran and had no opinion. A skipped call and a shim fault print `{}` or a deny.
        assert result.stdout.decode().strip() == "{}"
        assert _last_logged_outcome(world) == "ok"
        assert_no_effects(before, world.snapshot(ignore_state=True), world)

    @pytest.mark.parametrize("registration", SHIM_ARM_REGISTRATIONS, ids=lambda r: r.key)
    def test_the_import_arm_gives_a_deny_only_where_the_shim_arm_gives_the_same(
        self, world: World, registration: Registration
    ) -> None:
        for trigger in TRIGGERS.get(registration.script, ()) or (None,):
            world.reset()
            world.apply_setup(trigger.setup if trigger else None)
            payload = _cursor_payload_for(registration, world, trigger)
            imported = _decision(_import_arm(world, registration, payload))
            shim_result = _shim_arm(world, registration, payload)
            shim_denies = json.loads(shim_result.stdout).get("permission") == "deny"
            if imported == "deny":
                assert shim_denies, (registration.key, trigger.name if trigger else None)


class TestInformationalArms:
    """Every informational registration with a delivered arm runs through the shim under the temporary world."""

    @pytest.mark.parametrize("registration", INFORMATIONAL_ARM_REGISTRATIONS, ids=lambda r: r.key)
    def test_the_shim_arm_runs_the_hook_and_leaves_no_unlisted_effect(self, world: World, registration: Registration) -> None:
        payload = _benign_payload(registration, world)
        before = world.snapshot(ignore_state=False)
        result = _shim_arm(world, registration, payload)
        assert result.returncode == 0, result.stderr
        output = json.loads(result.stdout)
        assert output.get("permission") != "allow"
        assert _last_logged_outcome(world) in ("ok", "context"), _last_logged_outcome(world)
        writes, network = _listed(registration)
        assert_no_effects(before, world.snapshot(ignore_state=True), world, writes, network)

    @pytest.mark.parametrize(
        "registration",
        [r for r in INFORMATIONAL_ARM_REGISTRATIONS if r.script in INFORMATIONAL_WRITE_TRIGGERS],
        ids=lambda r: r.key,
    )
    def test_the_write_trigger_reaches_the_hooks_write_path_and_makes_only_the_listed_writes(
        self, world: World, registration: Registration
    ) -> None:
        trigger = INFORMATIONAL_WRITE_TRIGGERS[registration.script]
        world.apply_setup(trigger.setup)
        payload = trigger.payload(registration, world)
        world.reset()
        before = world.snapshot(ignore_state=False)
        result = _shim_arm(world, registration, payload)
        assert result.returncode == 0, result.stderr
        assert _last_logged_outcome(world) in ("ok", "context"), _last_logged_outcome(world)
        after = world.snapshot(ignore_state=True)
        assert {key for key in set(before) | set(after) if before.get(key) != after.get(key)}, "the trigger wrote nothing"
        writes, network = _listed(registration)
        assert_no_effects(before, after, world, writes, network)


def _child_payload(registration: Registration, world: World) -> dict:
    """The registration's trigger call when it has one, so a child's payload reaches its own path, else a benign call.

    A gate's deny trigger is taken before an informational write trigger, and a registration has only one of the two."""
    gate_triggers = TRIGGERS.get(registration.script, ())
    if gate_triggers:
        world.apply_setup(gate_triggers[0].setup)
        return _cursor_payload_for(registration, world, gate_triggers[0])
    write_trigger = INFORMATIONAL_WRITE_TRIGGERS.get(registration.script)
    if write_trigger is None:
        return _benign_payload(registration, world)
    world.apply_setup(write_trigger.setup)
    return write_trigger.payload(registration, world)


class TestChildPayloadImportArm:
    @pytest.mark.parametrize("registration", ARM_REGISTRATIONS, ids=lambda r: r.key)
    def test_a_child_payload_through_the_import_arm_leaves_no_unlisted_effect(
        self, world: World, registration: Registration
    ) -> None:
        payload = _child_payload(registration, world)
        payload["conversation_id"] = CHILD_CONVERSATION_ID
        payload["parent_agent_id"] = PARENT_CONVERSATION_ID
        world.reset()
        before = world.snapshot(ignore_state=False)
        result = _import_arm(world, registration, payload)
        assert _decision(result) in _import_decisions(registration)
        writes, network = _listed(registration, import_arm=True)
        if registration.script in INFORMATIONAL_WRITE_TRIGGERS:
            # An imported copy has no routed config dir, so its trigger writes land in the default one.
            writes = (*writes, r"home:home/\.claude(/.*)?")
        assert_no_effects(before, world.snapshot(ignore_state=False), world, writes, network)

    @pytest.mark.parametrize("registration", ARM_REGISTRATIONS, ids=lambda r: r.key)
    def test_a_child_payload_through_the_shim_arm_leaves_no_unlisted_effect(
        self, world: World, registration: Registration
    ) -> None:
        payload = _child_payload(registration, world)
        payload["conversation_id"] = CHILD_CONVERSATION_ID
        payload["parent_agent_id"] = PARENT_CONVERSATION_ID
        before = world.snapshot(ignore_state=False)
        result = _shim_arm(world, registration, payload)
        assert result.returncode == 0, result.stderr
        writes, network = _listed(registration)
        after = world.snapshot(ignore_state=True)
        assert_no_effects(before, after, world, writes, network)
        assert not [key for key in after if CHILD_CONVERSATION_ID in key]


# --- leading cd ---


@dataclass(frozen=True)
class WriteFamily:
    """A family of gates that evaluate a git write against the repository its working directory resolves to."""

    script: str
    write: str
    git_dash_c_write: Callable[[Path], str] | None
    setup: Callable[[World], None] | None


def _require_worktree_sentinel(world: World) -> None:
    sentinel = world.repo / ".claude" / "worktree-required"
    sentinel.parent.mkdir(exist_ok=True)
    sentinel.write_text("")


def _git_in(directory: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(directory), "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid", *args],
        check=True,
        capture_output=True,
        env=without_git_environment(os.environ),
    )


def _add_linked_worktrees(world: World) -> None:
    """A main checkout on its default branch with a worktree-required sentinel, and two linked worktrees of it.

    The root worktree has a staged change and the sibling is clean, so a gate that evaluates the wrong checkout
    reaches a different verdict. All three sit outside the world's watched directories."""
    main = world.base / "linked-main"
    main.mkdir()
    subprocess.run(["git", "init", "-q", str(main)], check=True, capture_output=True, env=without_git_environment(os.environ))
    subprocess.run(
        ["git", "-C", str(main), "symbolic-ref", "HEAD", "refs/heads/main"],
        check=True,
        capture_output=True,
        env=without_git_environment(os.environ),
    )
    (main / "README.md").write_text("fixture\n")
    _git_in(main, "add", "README.md")
    _git_in(main, "commit", "-q", "-m", "initial")
    world.linked_main = main.resolve()
    world.linked_root = (world.base / "linked-root").resolve()
    world.linked_sibling = (world.base / "linked-sibling").resolve()
    _git_in(main, "worktree", "add", "-q", "-b", "linked-feature", str(world.linked_root))
    _git_in(main, "worktree", "add", "-q", "-b", "linked-sibling", str(world.linked_sibling))
    sentinel = main / ".claude" / "worktree-required"
    sentinel.parent.mkdir()
    sentinel.write_text("")
    (world.linked_root / "README.md").write_text("fixture\nstaged line\n")
    _git_in(world.linked_root, "add", "README.md")


WRITE_FAMILIES = {
    "code-review": WriteFamily(
        "require-code-review.sh", "git commit -m x", lambda repo: f"git -C {repo} commit -m x", _stage_a_change
    ),
    "ready-for-review": WriteFamily("require-ready-for-review.sh", "gh pr create --title t --body b", None, None),
    "ready-for-review-push": WriteFamily("require-ready-for-review.sh", "git push origin feature", None, None),
    "ready-for-review-pr-ready": WriteFamily("require-ready-for-review.sh", "gh pr ready", None, None),
    "worktree-git-writes": WriteFamily(
        "require-worktree-for-git-writes.sh",
        "git commit -m x",
        lambda repo: f"git -C {repo} commit -m x",
        _require_worktree_sentinel,
    ),
}


# The shapes a leading cd takes. The last one needs a family with a `git -C` spelling of its write.
LEADING_CD_SHAPE_IDS = ("plain-then-repo", "clean-repo-no-root", "clean-repo-second-root", "nonexistent", "plain-then-git-dash-c")
# The shapes with one workspace root that is a linked worktree. The sibling shape reaches it through a clean sibling worktree.
LINKED_WORKTREE_SHAPE_IDS = ("linked-main-then-root", "linked-sibling-then-root")


def _leading_cd_shape(world: World, family: WriteFamily, shape_id: str) -> tuple[str, list[Path], Path] | None:
    """(command, workspace roots, the directory the reference payload's cwd names), or None when the family has no such shape."""
    repo, plain, clean = world.repo, world.plain_directory, world.clean_repo
    if shape_id == "linked-main-then-root":
        return f"cd {world.linked_main} && cd {world.linked_root} && {family.write}", [world.linked_root], world.linked_root
    if shape_id == "linked-sibling-then-root":
        return f"cd {world.linked_sibling} && cd {world.linked_root} && {family.write}", [world.linked_root], world.linked_root
    if shape_id == "plain-then-repo":
        return f"cd {plain} && cd {repo} && {family.write}", [repo], repo
    if shape_id == "clean-repo-no-root":
        return f"cd {clean} && cd {repo} && {family.write}", [repo], repo
    if shape_id == "clean-repo-second-root":
        return f"cd {clean} && cd {repo} && {family.write}", [repo, clean], clean
    if shape_id == "nonexistent":
        return f"cd /nonexistent/nowhere && {family.write}", [repo], repo
    if family.git_dash_c_write is None:
        return None
    return f"cd {plain} && {family.git_dash_c_write(repo)}", [repo], repo


LEADING_CD_CASES = [
    pytest.param(family_id, shape_id, id=f"{family_id}/{shape_id}")
    for family_id, family in WRITE_FAMILIES.items()
    for shape_id in LEADING_CD_SHAPE_IDS
    if shape_id != "plain-then-git-dash-c" or family.git_dash_c_write is not None
]
# A commit is the write the sibling shape names, so only the families that gate a commit run it.
LINKED_WORKTREE_CASES = [
    pytest.param(family_id, shape_id, id=f"{family_id}/{shape_id}")
    for family_id, family in WRITE_FAMILIES.items()
    for shape_id in LINKED_WORKTREE_SHAPE_IDS
    if shape_id != "linked-sibling-then-root" or family.write.startswith("git commit")
]


class TestLeadingCdVerdictEquality:
    def _registration(self, family: WriteFamily) -> Registration:
        return next(r for r in ALL_REGISTRATIONS if r.script == family.script)

    def _verdicts(self, world: World, family_id: str, shape_id: str) -> tuple[str, str]:
        family = WRITE_FAMILIES[family_id]
        registration = self._registration(family)
        world.apply_setup(family.setup)
        if shape_id in LINKED_WORKTREE_SHAPE_IDS:
            _add_linked_worktrees(world)
        shape = _leading_cd_shape(world, family, shape_id)
        assert shape is not None
        command, roots, reference_directory = shape
        world.reset()
        payload = shell_pre_payload(roots[0], command)
        payload["workspace_roots"] = [str(root) for root in roots]
        shim = _run_outside(world, shim_argv(*_arm_args(registration)), payload)
        assert shim.returncode == 0, shim.stderr
        shim_output = json.loads(shim.stdout)
        shim_verdict = "deny" if shim_output.get("permission") == "deny" else ("none" if shim_output == {} else "other")
        world.reset()
        twin = {
            "hook_event_name": "PreToolUse",
            "session_id": CONVERSATION_ID,
            "cwd": str(reference_directory),
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "tool_use_id": "tool-use-placeholder",
        }
        reference = _direct(
            world, registration, twin, cwd=reference_directory, env=world.cursor_env({"CLAUDE_CONFIG_DIR": str(world.config)})
        )
        return shim_verdict, _decision(reference)

    @pytest.mark.parametrize(("family_id", "shape_id"), [*LEADING_CD_CASES, *LINKED_WORKTREE_CASES])
    def test_the_shim_verdict_equals_the_claude_shaped_reference_verdict(
        self, world: World, family_id: str, shape_id: str
    ) -> None:
        shim_verdict, reference_verdict = self._verdicts(world, family_id, shape_id)
        assert shim_verdict == reference_verdict, (family_id, shape_id)

    @pytest.mark.parametrize("family_id", ["code-review", "worktree-git-writes"])
    def test_a_family_with_a_repo_only_trigger_denies_in_the_shapes_whose_directory_is_the_repo(
        self, world: World, family_id: str
    ) -> None:
        for shape_id in ("plain-then-repo", "plain-then-git-dash-c", "nonexistent"):
            shim_verdict, reference_verdict = self._verdicts(world, family_id, shape_id)
            assert (shim_verdict, reference_verdict) == ("deny", "deny"), shape_id


class TestEffectDetectorsHavePositiveControls:
    """One scratch hook per effect class, each run through the shim arm, each expected to fail the effect check."""

    def _run_scratch(self, world: World, body: str) -> None:
        script = write_hook_script(world.base / "scratch.sh", body)
        world.reset()
        before = world.snapshot(ignore_state=True)
        args = hook_args("PreToolUse", script=script, matcher="Bash")
        _run_outside(world, shim_argv(*args), shell_pre_payload(world.repo, "echo hi"))
        deadline = time.monotonic() + 10
        while not list(world.stubs.glob("sentinel-*")) and "detached" in body and time.monotonic() < deadline:
            time.sleep(0.05)
        assert_no_effects(before, world.snapshot(ignore_state=True), world)

    def test_a_clean_hook_passes_the_effect_check(self, world: World) -> None:
        self._run_scratch(world, "cat > /dev/null\n")

    @pytest.mark.parametrize(
        ("effect", "body"),
        [
            ("config-dir-write", 'cat > /dev/null\ntouch "$CLAUDE_CONFIG_DIR/written"\n'),
            ("tmpdir-write", 'cat > /dev/null\ntouch "$TMPDIR/written"\n'),
            ("work-tree-write", 'cat > /dev/null\ntouch "$CURSOR_PROJECT_DIR/written"\n'),
            ("git-metadata-write", 'cat > /dev/null\ntouch "$CURSOR_PROJECT_DIR/.git/written"\n'),
            ("git-fetch", "cat > /dev/null\ngit fetch origin\n"),
            ("gh-call", "cat > /dev/null\ngh pr list\n"),
            ("detached-spawn", "cat > /dev/null\n( detached-probe-command & )\n"),
        ],
    )
    def test_each_effect_class_fails_the_effect_check(self, world: World, effect: str, body: str) -> None:
        with pytest.raises(AssertionError, match="unlisted writes|network or spawn calls"):
            self._run_scratch(world, body)

    def test_an_import_copy_that_calls_the_network_through_a_stub_fails_the_check(self, world: World) -> None:
        script = write_hook_script(world.base / "scratch.sh", "cat > /dev/null\ncurl https://example.invalid\n")
        world.reset()
        before = world.snapshot(ignore_state=False)
        subprocess.run([str(script)], input=b"{}", capture_output=True, check=False, cwd=world.outside, env=world.cursor_env())
        with pytest.raises(AssertionError):
            assert_no_effects(before, world.snapshot(ignore_state=False), world)

    def test_a_listed_write_passes_the_check_and_an_unlisted_one_does_not(self, world: World) -> None:
        before = world.snapshot(ignore_state=True)
        (world.config / "written").write_text("x")
        after = world.snapshot(ignore_state=True)
        assert_no_effects(before, after, world, listed_writes=(r"config:config/written",))
        with pytest.raises(AssertionError):
            assert_no_effects(before, after, world)


BOOTSTRAP_PLUGIN_ID = "plugin-a@market-a"
LAUNCH_DEADLINE_SECONDS = 10.0


class TestBootstrapProjectPluginsRow:
    """The one shim arm whose effects differ from its reference run: the shim removes the Claude Code markers."""

    def _prepare(self, world: World) -> tuple[Path, Path]:
        hook = HOOKS_DIR / "bootstrap-project-plugins.sh"
        # The hook launches ../scripts/reconcile-project-plugins.sh by script-relative path, so run a copy with a recording stub.
        hooks_copy = world.base / "hooks-copy" / "hooks"
        scripts_copy = world.base / "hooks-copy" / "scripts"
        shutil.copytree(HOOKS_DIR, hooks_copy, ignore=shutil.ignore_patterns("tests", "__pycache__"))
        scripts_copy.mkdir(parents=True)
        launched = world.base / "launched"
        stub = scripts_copy / "reconcile-project-plugins.sh"
        stub.write_text(f"#!/usr/bin/env bash\ntouch '{launched}'\n")
        stub.chmod(0o755)

        def trust_the_repo_and_enable_a_missing_plugin(world: World) -> None:
            (world.repo / ".claude").mkdir()
            (world.repo / ".claude" / "settings.json").write_text(json.dumps({"enabledPlugins": {BOOTSTRAP_PLUGIN_ID: True}}))
            (world.config / ".claude.json").write_text(
                json.dumps({"projects": {str(world.repo): {"hasTrustDialogAccepted": True}}})
            )

        world.apply_setup(trust_the_repo_and_enable_a_missing_plugin)
        world.reset()
        return hooks_copy / hook.name, launched

    def _audit_lines(self, world: World) -> list[dict]:
        log = world.config / "plugin-bootstrap" / "audit.log"
        return [json.loads(line) for line in log.read_text().splitlines() if line] if log.exists() else []

    def test_the_hook_takes_its_non_interactive_exit_under_the_shim_and_launches_nothing(self, world: World) -> None:
        hook, launched = self._prepare(world)
        args = hook_args("SessionStart", script=hook, matcher="startup", fail_closed=False)
        environment = world.cursor_env({"CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDECODE": "1", "CLAUDE_PID": "1"})
        assert_outside_any_repository(world.outside, environment)
        # The shim runs from the checkout: it reads nothing under hooks/, so the hook's scratch copy needs no adapter beside it.
        result = subprocess.run(
            shim_argv(*args),
            input=json.dumps(build_spec("session_start", world.repo).cursor).encode(),
            capture_output=True,
            check=False,
            cwd=world.outside,
            env=environment,
        )
        assert result.returncode == 0, result.stderr
        lines = self._audit_lines(world)
        assert [(line["decision"], line["outcome"]) for line in lines] == [("m1a_non_interactive", "skipped entrypoint=unset")]
        assert not launched.exists()
        assert read_log_lines(world.shim_env())[-1]["out"] == "ok"

    def test_the_same_setup_run_directly_with_an_interactive_entrypoint_launches_the_reconciler(self, world: World) -> None:
        hook, launched = self._prepare(world)
        environment = world.cursor_env({"CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDE_CONFIG_DIR": str(world.config)})
        payload = {"hook_event_name": "SessionStart", "session_id": CONVERSATION_ID, "cwd": str(world.repo), "source": "startup"}
        subprocess.run(
            [str(hook)], input=json.dumps(payload).encode(), capture_output=True, check=False, cwd=world.outside, env=environment
        )
        deadline = time.monotonic() + LAUNCH_DEADLINE_SECONDS
        while not launched.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert launched.exists(), "the positive control never launched the stub, so the shim row proves nothing"
