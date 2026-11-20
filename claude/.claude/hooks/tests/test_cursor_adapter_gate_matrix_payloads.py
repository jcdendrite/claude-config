"""Gate matrix: the Cursor payload builders, and what a hook receives through the shim, its payload and its envelope."""

from __future__ import annotations

import json
import platform
import subprocess
from pathlib import Path

import pytest

from ._cursor_gate_matrix_helpers import DIRECT_HOOK_TIMEOUT_SECONDS
from ._cursor_payloads import (
    CLAUDE_PROCESS_ENV_NAMES,
    CONVERSATION_ID,
    PARENT_CONVERSATION_ID,
    SPEC_CLAUDE_EVENT,
    SPEC_IDS,
    assert_outside_any_repository,
    build_spec,
    hook_args,
    make_shim_env,
    recording_probe_script,
    run_shim,
    shim_argv,
    shim_json,
    shim_process_env,
    write_hook_script,
)

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
    "subagent_start": {"cwd", "agent_type", "agent_id"},
    "subagent_stop": {"session_id", "cwd", "agent_type"},
    "stop": {"session_id", "cwd"},
    "after_agent_response": {"session_id", "cwd", "last_assistant_message", "prompt_id"},
}


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
    def test_the_probe_receives_exactly_the_twins_keys(self, spec_id: str, tmp_path: Path) -> None:
        spec = build_spec(spec_id, tmp_path.resolve())
        _, received = _run_probe(tmp_path, spec_id)
        all_fields = received.keys() | spec.claude_twin.keys()
        differing_fields = sorted(field for field in all_fields if received.get(field) != spec.claude_twin.get(field))
        assert received == spec.claude_twin, (spec_id, differing_fields)

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


SESSION_ID_NAME = "CLAUDE_CONFIG_ADAPTER_SESSION_ID"


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
        try:
            direct.communicate(shim_stdin, timeout=DIRECT_HOOK_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            direct.kill()
            direct.communicate()
            raise
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
        (shim_meta, _, shim_environment) = pair["shim"]
        (direct_meta, _, direct_environment) = pair["direct"]
        assert shim_meta["cwd"] == direct_meta["cwd"] == str(pair["root"])
        assert shim_meta["umask"] == direct_meta["umask"]
        assert shim_meta["stdin"] == "pipe"
        assert direct_meta["stdin"] == "pipe"
        assert not set(CLAUDE_PROCESS_ENV_NAMES) & set(shim_environment)
        assert shim_environment["PATH"] == pair["record_path"]
        assert shim_environment["CLAUDE_CONFIG_DIR"] == str(pair["env"].config_dir)
        assert shim_environment["CLAUDE_CONFIG_ADAPTER_SESSION_ID"] == CONVERSATION_ID
        assert "CLAUDE_CONFIG_ADAPTER_SESSION_ID" not in direct_environment
        # The interpreter may add or change the locale names its C-locale coercion owns, which the pairs below pin.
        shim_compared = {
            k: v for k, v in shim_environment.items() if k not in _ENV_NOISE and k not in ("LC_CTYPE", SESSION_ID_NAME)
        }
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
        shim_compared = {
            k: v for k, v in shim_environment.items() if k not in _ENV_NOISE | allowed_to_differ | {SESSION_ID_NAME}
        }
        direct_compared = {k: v for k, v in direct_environment.items() if k not in _ENV_NOISE | allowed_to_differ}
        assert shim_compared == direct_compared
