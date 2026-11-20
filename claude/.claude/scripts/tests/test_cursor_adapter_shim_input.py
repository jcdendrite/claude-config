"""hook-shim.py: payload validation, unknown tools and the payload cap, and the text rules."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from helpers import CLAUDE_DIR, HOOKS_DIR

from ._cursor_shim_helpers import (
    PAYLOAD_CAP,
    SHIM_MODULE,
    _assert_deny_text_rules,
    _pin_prefix,
    _probe_ran,
    _run_hook_body,
    _run_probe,
    _scratch,
    _walkless_env,
    _workspace,
)

sys.path.insert(0, str(CLAUDE_DIR))
from hooks.tests._cursor_payloads import (  # noqa: E402 (the import needs the sys.path entry above)
    PARITY_EXCEPTIONS_JSON,
    SHIM,
    TEXT_RULE_CORPUS,
    TOOL_MAP_JSON,
    ShimEnv,
    build_spec,
    copy_adapter,
    embed_at_each_position,
    file_tool_payload,
    hook_args,
    load_json,
    make_shim_env,
    read_log_lines,
    recording_probe_script,
    run_shim,
    shell_pre_payload,
    shim_json,
    write_hook_script,
)

# --- payload validation ---


def _framing_cases(
    root: Path, separator: str, tmp_path: Path
) -> dict[str, tuple[dict, str, str | None, Callable[[dict], object], object]]:
    """field -> (payload, Claude event, matcher, how to read the field back from the translated payload, its expected value)."""
    command = shell_pre_payload(root, f"echo a{separator}b")
    path_text = str(root / "src" / f"a{separator}b.txt")
    path = file_tool_payload(root, "Write", path_text, content="x")
    odd_root = tmp_path / f"odd{separator}root"
    odd_root.mkdir()
    odd_root = odd_root.resolve()
    roots = shell_pre_payload(odd_root, "ls")
    roots["workspace_roots"] = [str(odd_root)]
    tool = shell_pre_payload(root, "ls")
    tool["tool_name"] = f"MCP:t{separator}x"
    tool["tool_input"] = {}
    return {
        "command": (command, "PreToolUse", None, lambda t: t["tool_input"]["command"], f"echo a{separator}b"),
        "path": (path, "PreToolUse", "Edit|Write|MultiEdit", lambda t: t["tool_input"]["file_path"], path_text),
        "root": (roots, "PreToolUse", None, lambda t: t["cwd"], str(odd_root)),
        "tool-name": (tool, "PreToolUse", None, lambda t: t["tool_name"], f"MCP:t{separator}x"),
    }


FRAMING_FIELDS = ["command", "path", "root", "tool-name"]


class TestPayloadValidation:
    @pytest.mark.parametrize("separator", ["\x1f", "\x1e"], ids=["unit-separator", "record-separator"])
    @pytest.mark.parametrize("field", FRAMING_FIELDS)
    @pytest.mark.parametrize("fail_closed", [True, False], ids=["fail-closed", "informational"])
    def test_a_framing_byte_in_any_field_reaches_the_hook_as_the_escape(
        self, tmp_path: Path, field: str, separator: str, fail_closed: bool
    ) -> None:
        root = _workspace(tmp_path)
        payload, event, matcher, read_back, expected = _framing_cases(root, separator, tmp_path)[field]
        kwargs = {"matcher": matcher} if matcher else {}
        output, _ = _run_probe(tmp_path, payload, claude_event=event, fail_closed=fail_closed, **kwargs)
        assert output == {}
        assert read_back(json.loads((tmp_path / "saved.json").read_text())) == expected

    @pytest.mark.parametrize("separator", ["\x1f", "\x1e"], ids=["unit-separator", "record-separator"])
    def test_a_framing_byte_in_a_session_id_is_refused_by_the_id_grammar_first(self, tmp_path: Path, separator: str) -> None:
        payload = shell_pre_payload(_workspace(tmp_path), "ls")
        payload["conversation_id"] = f"abc{separator}def"
        output, _ = _run_probe(tmp_path, payload)
        assert "session-id-grammar" in output["agent_message"]
        assert not _probe_ran(tmp_path)

    def test_a_nul_in_a_command_reaches_the_hook_as_the_escape(self, tmp_path: Path) -> None:
        payload = shell_pre_payload(_workspace(tmp_path), "echo a\x00b")
        output, _ = _run_probe(tmp_path, payload)
        assert output == {}
        translated = json.loads((tmp_path / "saved.json").read_text())
        assert translated["tool_input"]["command"] == "echo a\x00b"

    def test_a_nul_in_the_directory_input_takes_the_refusal(self, tmp_path: Path) -> None:
        payload = shell_pre_payload(_workspace(tmp_path), "ls", cwd="/x\x00y")
        output, env = _run_probe(tmp_path, payload, mode="pin")
        assert output["permission"] == "deny"
        assert "dir-input-refused" in output["agent_message"]
        assert read_log_lines(env)[-1]["out"] == "dir-input-refused"

    def test_a_nul_in_a_write_path_resolves_under_the_first_root_and_the_write_keeps_its_name(self, tmp_path: Path) -> None:
        root = _workspace(tmp_path)
        payload = file_tool_payload(root, "Write", str(root / "src" / "a\x00b.txt"), content="x")
        output, _ = _run_probe(tmp_path, payload, matcher="Edit|Write|MultiEdit")
        assert output == {}
        translated = json.loads((tmp_path / "saved.json").read_text())
        assert (translated["tool_name"], translated["cwd"]) == ("Write", str(root))
        assert translated["tool_input"]["file_path"] == str(root / "src" / "a\x00b.txt")

    @pytest.mark.parametrize("fail_closed", [True, False], ids=["fail-closed", "informational"])
    def test_a_nul_in_a_workspace_root_is_payload_malformed(self, tmp_path: Path, fail_closed: bool) -> None:
        root = _workspace(tmp_path)
        payload = shell_pre_payload(root, "ls")
        payload["workspace_roots"] = [f"{root}\x00x"]
        output, env = _run_probe(tmp_path, payload, fail_closed=fail_closed)
        assert not _probe_ran(tmp_path)
        assert read_log_lines(env)[-1]["out"] == "payload-malformed"
        assert (output["permission"] == "deny") if fail_closed else (output == {})

    def test_a_nul_in_a_tool_name_takes_the_unclassified_tool_deny(self, tmp_path: Path) -> None:
        root = _workspace(tmp_path)
        payload = shell_pre_payload(root, "ls")
        payload["tool_name"] = "Shell\x00"
        output, env = _run_probe(tmp_path, payload, mode="classify")
        assert output["permission"] == "deny"
        assert "unknown-tool" in output["agent_message"]
        assert read_log_lines(env)[-1]["out"] == "unknown-tool"

    @pytest.mark.parametrize("mode", ["pin", "pin-check", "hook"])
    def test_a_pin_shaped_first_line_holding_a_nul_is_agent_text(self, tmp_path: Path, mode: str) -> None:
        root = _workspace(tmp_path)
        command = f"builtin cd -- '{root}' || exit 1\x00\nmake"
        if mode == "pin-check":
            payload = build_spec("shell_before_execution", root).cursor
            payload["command"] = command
        else:
            payload = shell_pre_payload(root, command)
        env = _walkless_env(tmp_path)
        output, env = _run_probe(tmp_path, payload, env=env, mode=mode)
        if mode == "pin":
            assert output["updated_input"]["command"] == _pin_prefix(env, root) + command
        elif mode == "pin-check":
            assert output["permission"] == "deny"
            assert read_log_lines(env)[-1]["out"] == "pin-check"
        else:
            assert output == {}
            assert json.loads((tmp_path / "saved.json").read_text())["tool_input"]["command"] == command

    @pytest.mark.parametrize("bad", ['0,"fail_closed":false,"exit":0', "", {"a": 1}, -1, 1.5, True, [1], "7", False], ids=repr)
    def test_a_loop_count_that_is_not_a_non_negative_integer_denies_on_any_event(self, tmp_path: Path, bad: object) -> None:
        payload = shell_pre_payload(_workspace(tmp_path), "ls")
        payload["loop_count"] = bad
        output, env = _run_probe(tmp_path, payload)
        assert output["permission"] == "deny"
        assert "payload-malformed" in output["agent_message"] and "loop_count" in output["agent_message"]
        assert not _probe_ran(tmp_path)
        assert read_log_lines(env)[-1]["out"] == "payload-malformed"

    @pytest.mark.parametrize("loop_count", [None, 0, 3], ids=["null", "zero", "three"])
    def test_a_valid_loop_count_still_lets_a_gates_exit_two_deny(self, tmp_path: Path, loop_count: int | None) -> None:
        env = make_shim_env(tmp_path)
        payload = shell_pre_payload(_workspace(tmp_path), "ls")
        payload["loop_count"] = loop_count
        script = write_hook_script(tmp_path / "hook.sh", "echo 'gate says no' >&2\nexit 2\n")
        output = shim_json(run_shim(env, payload, hook_args("PreToolUse", script=script), cwd=_scratch(tmp_path)))
        assert output == {"permission": "deny", "user_message": "gate says no", "agent_message": "gate says no"}

    @pytest.mark.parametrize(
        ("literal", "accepted"), [("-0", True), ("100.0", False), ("1E2", False), ("1e2", False), ("0.0", False), ("-0.0", False)]
    )
    def test_only_a_json_integer_spelling_of_loop_count_is_accepted(self, tmp_path: Path, literal: str, accepted: bool) -> None:
        env = make_shim_env(tmp_path)
        script = write_hook_script(tmp_path / "hook.sh", "echo 'gate says no' >&2\nexit 2\n")
        payload_text = json.dumps(shell_pre_payload(_workspace(tmp_path), "ls"))[:-1] + f', "loop_count": {literal}}}'
        output = shim_json(run_shim(env, payload_text, hook_args("PreToolUse", script=script), cwd=_scratch(tmp_path)))
        if accepted:
            assert output == {"permission": "deny", "user_message": "gate says no", "agent_message": "gate says no"}
        else:
            assert "payload-malformed" in output["agent_message"] and "loop_count" in output["agent_message"]

    @pytest.mark.parametrize(
        "roots",
        ["/a", {"x": "/a"}, [5, "/b"], [""], None, 7, True, [["/a"]]],
        ids=["string", "object", "non-string-element", "empty-string-root", "null", "number", "bool", "nested-list"],
    )
    @pytest.mark.parametrize("fail_closed", [True, False], ids=["fail-closed", "informational"])
    def test_workspace_roots_that_are_not_a_list_of_non_empty_strings_deny_by_name(
        self, tmp_path: Path, roots: object, fail_closed: bool
    ) -> None:
        payload = shell_pre_payload(_workspace(tmp_path), "ls")
        payload["workspace_roots"] = roots
        output, env = _run_probe(tmp_path, payload, fail_closed=fail_closed)
        assert not _probe_ran(tmp_path)
        assert read_log_lines(env)[-1]["out"] == "payload-malformed"
        if fail_closed:
            assert output["permission"] == "deny"
            assert "workspace_roots" in output["agent_message"]
        else:
            assert output == {}

    def test_absent_and_empty_workspace_roots_are_accepted_and_pin_home(self, tmp_path: Path) -> None:
        env = make_shim_env(tmp_path)
        for index, roots in enumerate([None, []]):
            run_dir = tmp_path / f"run{index}"
            run_dir.mkdir()
            payload = shell_pre_payload(_workspace(tmp_path), "ls")
            if roots is None:
                del payload["workspace_roots"]
            else:
                payload["workspace_roots"] = roots
            script = recording_probe_script(run_dir / "hook.sh", run_dir / "saved.json")
            run_shim(env, payload, hook_args("PreToolUse", script=script), cwd=_scratch(tmp_path))
            assert json.loads((run_dir / "saved.json").read_text())["cwd"] == str(env.home.resolve())

    def test_a_hook_whose_stdout_holds_a_nul_after_exit_zero_is_the_non_json_outcome(self, tmp_path: Path) -> None:
        output, env = _run_hook_body(tmp_path, "printf 'x\\0y'\n")
        assert "non-json" in output["agent_message"]
        assert read_log_lines(env)[-1]["out"] == "non-json"

    def test_a_hooks_nul_in_stdout_does_not_hide_its_exit_two(self, tmp_path: Path) -> None:
        output, _ = _run_hook_body(tmp_path, "printf 'x\\0y'\necho 'no' >&2\nexit 2\n")
        assert output["agent_message"] == "no"

    @pytest.mark.parametrize("fail_closed", [True, False], ids=["fail-closed", "informational"])
    @pytest.mark.parametrize(
        ("spec_id", "own_event", "other_event"),
        [("shell_pre", "PreToolUse", "PostToolUse"), ("shell_post", "PostToolUse", "PreToolUse")],
    )
    def test_an_entry_registered_for_another_claude_event_skips_the_call_without_running_its_hook(
        self, tmp_path: Path, spec_id: str, own_event: str, other_event: str, fail_closed: bool
    ) -> None:
        payload = build_spec(spec_id, _workspace(tmp_path)).cursor

        output, env = _run_probe(tmp_path, payload, claude_event=other_event, fail_closed=fail_closed)

        assert output == {}
        assert not _probe_ran(tmp_path)
        assert read_log_lines(env)[-1]["out"] == "skipped"

        control_dir = tmp_path / "control"
        control_dir.mkdir()
        control_output, _ = _run_probe(control_dir, payload, claude_event=own_event, fail_closed=fail_closed)
        assert control_output == {}
        assert _probe_ran(control_dir), "the entry on its own event never ran its hook, so the skip above proved nothing"


# --- unknown tools and the payload cap ---


class TestUnknownToolsAndCap:
    def _classify(
        self, tmp_path: Path, tool_name: object, *, fail_closed: bool = True, shim: Path = SHIM, env: ShimEnv | None = None
    ) -> dict:
        env = env or make_shim_env(tmp_path)
        root = _workspace(tmp_path) if not (tmp_path / "workspace").exists() else (tmp_path / "workspace").resolve()
        payload = build_spec("shell_pre", root).cursor
        payload["tool_name"] = tool_name
        args = hook_args("PreToolUse", mode="classify", fail_closed=fail_closed)
        return shim_json(
            run_shim(
                env, payload, args, cwd=_scratch(tmp_path) if not (tmp_path / "cwd").exists() else tmp_path / "cwd", shim=shim
            )
        )

    def test_an_mcp_name_no_file_lists_passes_the_classify_entry(self, tmp_path: Path) -> None:
        assert self._classify(tmp_path, "MCP:some_server_tool") == {}

    def test_an_unlisted_bare_name_denies_until_it_is_classified(self, tmp_path: Path) -> None:
        env = make_shim_env(tmp_path)
        output = self._classify(tmp_path, "BrandNewTool", env=env)
        assert output["permission"] == "deny"
        assert "unknown-tool" in output["agent_message"]
        assert "BrandNewTool" not in output["agent_message"]
        _assert_deny_text_rules(output)
        # The log line, not the deny text, carries the name the engineer must classify.
        last = read_log_lines(env)[-1]
        assert (last["out"], last["tool"], last["ev"]) == ("unknown-tool", "BrandNewTool", "preToolUse")

    def test_a_tool_name_outside_the_log_grammar_is_logged_as_the_overflow_token(self, tmp_path: Path) -> None:
        env = make_shim_env(tmp_path)
        self._classify(tmp_path, "Brand New Tool!", env=env)
        assert read_log_lines(env)[-1]["tool"] == SHIM_MODULE.LOG_OVERFLOW_TOKEN

    @pytest.mark.parametrize("tool_name", [5, True, ["Shell"], {"name": "Shell"}], ids=["number", "bool", "list", "object"])
    def test_a_non_string_tool_name_denies_on_a_fail_closed_classify_entry_and_prints_empty_on_an_informational_one(
        self, tmp_path: Path, tool_name: object
    ) -> None:
        env = make_shim_env(tmp_path)
        output = self._classify(tmp_path, tool_name, env=env)
        assert output["permission"] == "deny"
        assert "unknown-tool" in output["agent_message"]
        assert read_log_lines(env)[-1]["out"] == "unknown-tool"
        assert self._classify(tmp_path, tool_name, env=env, fail_closed=False) == {}

    @pytest.mark.parametrize("refusal", ["missing-key", "session-id-grammar", "payload-malformed"])
    def test_other_refusals_log_the_call_event_and_tool(self, tmp_path: Path, refusal: str) -> None:
        env = make_shim_env(tmp_path)
        payload = shell_pre_payload(_workspace(tmp_path), "echo hi")
        if refusal == "missing-key":
            del payload["tool_input"]["command"]
        elif refusal == "session-id-grammar":
            payload["conversation_id"] = "has space"
        else:
            payload["workspace_roots"] = "not-a-list"
        _run_probe(tmp_path, payload, env=env)
        last = read_log_lines(env)[-1]
        assert (last["out"], last["tool"], last["ev"]) == (refusal, "Shell", "preToolUse")

    def test_an_unlisted_bare_name_prints_empty_output_on_an_informational_entry(self, tmp_path: Path) -> None:
        assert self._classify(tmp_path, "BrandNewTool", fail_closed=False) == {}

    @pytest.mark.parametrize("tool", ["Shell", "Read", "Write", "Grep", "Delete", "Task", "WebFetch", "WebSearch"])
    def test_every_mapped_tool_passes_the_classify_entry(self, tmp_path: Path, tool: str) -> None:
        assert self._classify(tmp_path, tool) == {}

    @pytest.mark.parametrize("name", ["shell", "SHELL", "MCP", "mcp:tool", "Shell "], ids=repr)
    def test_a_near_miss_spelling_is_unlisted(self, tmp_path: Path, name: str) -> None:
        assert self._classify(tmp_path, name)["permission"] == "deny"

    def test_a_cursor_tool_entry_in_the_live_file_lifts_the_deny_with_no_install(self, tmp_path: Path) -> None:
        parity = load_json(PARITY_EXCEPTIONS_JSON)
        parity["entries"].append({"kind": "cursor-tool", "key": "BrandNewTool", "reason": "unsupported"})
        shim = copy_adapter(tmp_path, parity=parity)
        assert self._classify(tmp_path, "BrandNewTool", shim=shim) == {}

    def test_an_entry_of_another_kind_does_not_lift_the_deny(self, tmp_path: Path) -> None:
        parity = load_json(PARITY_EXCEPTIONS_JSON)
        parity["entries"].append({"kind": "registration", "key": "BrandNewTool", "reason": "unsupported"})
        shim = copy_adapter(tmp_path, parity=parity)
        assert self._classify(tmp_path, "BrandNewTool", shim=shim)["permission"] == "deny"

    def test_the_shim_classifies_only_from_the_recorded_map(self, tmp_path: Path) -> None:
        recorded = load_json(TOOL_MAP_JSON)
        del recorded["tools"]["Task"]
        env = make_shim_env(tmp_path, tool_map=recorded)
        assert self._classify(tmp_path, "Task", env=env)["permission"] == "deny"

    def _write_payload_of_size(self, root: Path, size: int, head: str = "", tail: str = "") -> bytes:
        payload = file_tool_payload(root, "Write", str(root / "src" / "big.txt"), content="")
        encoded = json.dumps(payload).encode()
        padding = size - len(encoded) - len(head) - len(tail)
        payload["tool_input"]["content"] = head + "x" * padding + tail
        return json.dumps(payload).encode()

    def test_a_payload_past_the_cap_denies_naming_the_cap_and_never_runs_the_hook(self, tmp_path: Path) -> None:
        env = make_shim_env(tmp_path)
        root = _workspace(tmp_path)
        credential_shape = "ghp_" + "A1b2C3d4" * 5
        # The head is read before the cap on every platform. The tail is read only when the refusing read also carries it.
        head_credential_shape = "ghp_" + "Z9y8X7w6" * 5
        data = self._write_payload_of_size(root, PAYLOAD_CAP + 1024, head=head_credential_shape, tail=credential_shape)
        script = recording_probe_script(tmp_path / "hook.sh", tmp_path / "saved.json")
        result = run_shim(env, data, hook_args("PreToolUse", script=script), cwd=_scratch(tmp_path))
        output = shim_json(result)
        assert output["permission"] == "deny"
        assert "payload-over-cap" in output["agent_message"]
        assert str(PAYLOAD_CAP) in output["agent_message"]
        assert not (tmp_path / "saved.json").exists()
        for canary in (credential_shape, head_credential_shape):
            assert canary.encode() not in result.stdout
            assert canary not in env.log_path.read_text()

    def test_a_payload_past_the_cap_prints_empty_output_on_an_informational_entry(self, tmp_path: Path) -> None:
        env = make_shim_env(tmp_path)
        root = _workspace(tmp_path)
        data = self._write_payload_of_size(root, PAYLOAD_CAP + 1024)
        script = recording_probe_script(tmp_path / "hook.sh", tmp_path / "saved.json")
        result = run_shim(env, data, hook_args("PreToolUse", script=script, fail_closed=False), cwd=_scratch(tmp_path))
        assert shim_json(result) == {}
        assert read_log_lines(env)[-1]["out"] == "payload-over-cap"

    def test_a_payload_just_under_the_cap_is_translated_whole(self, tmp_path: Path) -> None:
        env = make_shim_env(tmp_path)
        root = _workspace(tmp_path)
        data = self._write_payload_of_size(root, PAYLOAD_CAP - 4096)
        script = recording_probe_script(tmp_path / "hook.sh", tmp_path / "saved.json")
        result = run_shim(env, data, hook_args("PreToolUse", script=script), cwd=_scratch(tmp_path))
        assert result.returncode == 0
        translated = json.loads((tmp_path / "saved.json").read_text())
        assert len(translated["tool_input"]["content"]) > PAYLOAD_CAP - 8192


# --- text rules ---

# Parser edges: each takes the payload-malformed outcome, never a shim fault.
PARSER_EDGES = {
    "invalid-utf-8": b'{"conversation_id": "\xff"}',
    "top-level-array": b"[1]",
    "nan": b'{"loop_count": NaN}',
    "infinity": b'{"loop_count": Infinity}',
    "negative-infinity": b'{"loop_count": -Infinity}',
    "float-too-large": b'{"loop_count": 1e999}',
    "integer-past-the-digit-limit": b'{"loop_count": ' + b"9" * 5000 + b"}",
    "nesting-past-the-recursion-limit": b"[" * 200_000 + b"]" * 200_000,
    "lone-surrogate-in-a-value": b'{"command": "\\ud800"}',
    "lone-surrogate-in-a-key": b'{"\\ud800": 1}',
}

# `leading-cd` at the middle position puts the value between the path and `&&`, where only a space, tab or newline is allowed.
MIDDLE_KEEPS_LEADING_CD = {"trailing-newline"}


def _nesting_depth_between_encoder_and_decoder_limits() -> int:
    """A nesting depth that this interpreter's JSON decoder accepts and its encoder rejects."""

    def first_failing_depth(fails: Callable[[int], bool]) -> int:
        passing, failing = 1, 200_000
        while failing - passing > 1:
            middle = (passing + failing) // 2
            if fails(middle):
                failing = middle
            else:
                passing = middle
        return failing

    def decoder_fails(depth: int) -> bool:
        try:
            json.loads("[" * depth + "]" * depth)
        except RecursionError:
            return True
        return False

    def encoder_fails(depth: int) -> bool:
        nested: list = []
        for _ in range(depth):
            nested = [nested]
        try:
            json.dumps(nested)
        except RecursionError:
            return True
        return False

    decoder_limit = first_failing_depth(decoder_fails)
    encoder_limit = first_failing_depth(encoder_fails)
    if encoder_limit >= decoder_limit:
        pytest.skip("this interpreter has no depth the decoder accepts and the encoder rejects")
    return (encoder_limit + decoder_limit) // 2


class TestShimTextRules:
    @pytest.mark.parametrize("edge", list(PARSER_EDGES))
    @pytest.mark.parametrize("fail_closed", [True, False], ids=["fail-closed", "informational"])
    def test_a_parser_edge_takes_the_payload_malformed_outcome_never_a_shim_fault(
        self, tmp_path: Path, edge: str, fail_closed: bool
    ) -> None:
        output, env = _run_probe(tmp_path, PARSER_EDGES[edge], fail_closed=fail_closed)
        assert not _probe_ran(tmp_path)
        assert read_log_lines(env)[-1]["out"] == "payload-malformed"
        assert (output["permission"] == "deny") if fail_closed else (output == {})

    @pytest.mark.parametrize("fail_closed", [True, False], ids=["fail-closed", "informational"])
    def test_nesting_the_parser_accepts_but_the_encoder_rejects_takes_the_payload_malformed_outcome_never_a_shim_fault(
        self, tmp_path: Path, fail_closed: bool
    ) -> None:
        depth = _nesting_depth_between_encoder_and_decoder_limits()
        template = json.dumps(shell_pre_payload(_workspace(tmp_path), "echo a", description="@@")).encode()
        payload = template.replace(b'"@@"', b"[" * depth + b"]" * depth)
        assert len(payload) > 2 * depth

        output, env = _run_probe(tmp_path, payload, fail_closed=fail_closed)

        assert not _probe_ran(tmp_path)
        assert read_log_lines(env)[-1]["out"] == "payload-malformed"
        assert (output["permission"] == "deny") if fail_closed else (output == {})

    def test_serialize_refuses_a_document_nested_past_the_encoder_limit_on_any_interpreter_with_the_payload_malformed_outcome(
        self,
    ) -> None:
        nested: list = []
        # Far beyond any interpreter's json recursion limit, so the depth needs no per-platform tuning.
        for _ in range(200_000):
            nested = [nested]

        with pytest.raises(SHIM_MODULE.Refusal) as refusal:
            SHIM_MODULE.serialize(nested)

        assert refusal.value.outcome == "payload-malformed"

    @pytest.mark.parametrize("name", [name for name, value in TEXT_RULE_CORPUS.items() if value != "\ud800"])
    def test_a_corpus_value_in_a_command_reaches_the_hook_as_json_the_hook_parses_under_jq(
        self, tmp_path: Path, name: str
    ) -> None:
        value = TEXT_RULE_CORPUS[name]
        env = make_shim_env(tmp_path)
        saved = tmp_path / "saved.json"
        script = write_hook_script(tmp_path / "hook.sh", f"cat > '{saved}'\njq -e . '{saved}' > /dev/null\n")
        payload = shell_pre_payload(_workspace(tmp_path), f"echo a{value}b")
        output = shim_json(run_shim(env, payload, hook_args("PreToolUse", script=script), cwd=_scratch(tmp_path)))
        assert output == {}
        assert saved.read_bytes().isascii()
        assert json.loads(saved.read_text())["tool_input"]["command"] == f"echo a{value}b"

    @pytest.mark.parametrize("name", list(TEXT_RULE_CORPUS))
    def test_the_session_id_grammar_matches_ascii_only_at_every_position(self, name: str) -> None:
        for candidate in embed_at_each_position("abc-123", TEXT_RULE_CORPUS[name]):
            assert SHIM_MODULE.is_valid_session_id(candidate) is False
        assert SHIM_MODULE.is_valid_session_id("abc-123") is True

    @pytest.mark.parametrize("name", list(TEXT_RULE_CORPUS))
    def test_the_log_grammar_replaces_any_value_holding_a_corpus_value_with_the_overflow_token(self, name: str) -> None:
        for candidate in embed_at_each_position("my-hook", TEXT_RULE_CORPUS[name]):
            assert SHIM_MODULE.log_value("reg", candidate) == SHIM_MODULE.LOG_OVERFLOW_TOKEN
        assert SHIM_MODULE.log_value("reg", "my-hook") == "my-hook"

    def test_every_bmp_code_point_outside_the_allowed_sets_is_refused_by_both_grammars_at_every_position(self) -> None:
        session_allowed = set("-_0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
        log_allowed = session_allowed | set(".:+")
        for code_point in range(0x10000):
            character = chr(code_point)
            for candidate in embed_at_each_position("abc", character):
                assert SHIM_MODULE.is_valid_session_id(candidate) is (character in session_allowed), hex(code_point)
                overflowed = SHIM_MODULE.log_value("reg", candidate) == SHIM_MODULE.LOG_OVERFLOW_TOKEN
                assert overflowed is (character not in log_allowed), hex(code_point)

    @pytest.mark.parametrize("name", list(TEXT_RULE_CORPUS))
    def test_the_pin_prefix_recognition_holds_only_for_a_value_after_the_pin_line(self, name: str, tmp_path: Path) -> None:
        roots = SHIM_MODULE.Roots([str(tmp_path.resolve())], "")
        valid = SHIM_MODULE.pin_line(roots.canonical[0]) + "\nmake"
        start, middle, end = embed_at_each_position(valid, TEXT_RULE_CORPUS[name])
        assert SHIM_MODULE.recognize_pin(start, roots)[1] is False
        assert SHIM_MODULE.recognize_pin(middle, roots)[1] is False
        recognized_end = SHIM_MODULE.recognize_pin(end, roots)
        assert recognized_end == ("make" + TEXT_RULE_CORPUS[name], True)

    @pytest.mark.parametrize("name", list(TEXT_RULE_CORPUS))
    def test_the_leading_cd_syntax_is_ascii_and_the_path_is_not(self, name: str) -> None:
        value = TEXT_RULE_CORPUS[name]
        start, middle, end = embed_at_each_position("cd /x && make", value)
        assert SHIM_MODULE.leading_cd_candidate(start) is None
        assert SHIM_MODULE.leading_cd_candidate(middle) == ("/x" if name in MIDDLE_KEEPS_LEADING_CD else None)
        assert SHIM_MODULE.leading_cd_candidate(end) == "/x"

    @pytest.mark.parametrize("name", list(TEXT_RULE_CORPUS))
    def test_the_matcher_recheck_selects_by_whole_ascii_names(self, name: str) -> None:
        exact = SHIM_MODULE.compile_matcher("MCP:tool")
        wide = SHIM_MODULE.compile_matcher(r"MCP:tool\w*")
        for candidate in embed_at_each_position("MCP:tool", TEXT_RULE_CORPUS[name]):
            assert SHIM_MODULE.matcher_selects(exact, candidate) is False
            assert SHIM_MODULE.matcher_selects(wide, candidate) is False
        assert SHIM_MODULE.matcher_selects(exact, "MCP:tool") is True

    def test_a_translated_write_whose_path_holds_a_unit_separator_takes_the_hooks_own_field_shift_deny(
        self, tmp_path: Path
    ) -> None:
        root = _workspace(tmp_path)
        env = make_shim_env(tmp_path)
        saved = tmp_path / "saved.json"
        script = write_hook_script(
            tmp_path / "hook.sh",
            f"cat > '{saved}'\nexec < '{saved}'\n. '{HOOKS_DIR / '_lib.sh'}'\n"
            'emit_deny() { _lib_emit_deny "$1"; }\n_lib_parse_tool_input_or_deny \'could not parse\'\n',
        )
        payload = file_tool_payload(root, "Write", str(root / "src" / "a\x1fb.txt"), content="x")
        result = run_shim(
            env, payload, hook_args("PreToolUse", script=script, matcher="Edit|Write|MultiEdit"), cwd=_scratch(tmp_path)
        )
        output = shim_json(result)
        assert output["permission"] == "deny"
        assert "Unit Separator" in output["agent_message"]
        assert json.loads(saved.read_text())["tool_input"]["file_path"] == str(root / "src" / "a\x1fb.txt")

    @pytest.mark.parametrize("locale", ["C", "POSIX", "en_US.ISO8859-1"])
    def test_non_ascii_command_and_path_reach_the_hook_unchanged_under_any_locale(self, tmp_path: Path, locale: str) -> None:
        root = _workspace(tmp_path)
        accented = "déjà ファイル"
        received = []
        for index, extra in enumerate(({"LC_ALL": locale}, {"LC_ALL": "C"})):
            run_dir = tmp_path / f"run{index}"
            run_dir.mkdir()
            env = make_shim_env(run_dir)
            payload = file_tool_payload(root, "Write", str(root / "src" / f"{accented}.txt"), content="x")
            script = recording_probe_script(run_dir / "hook.sh", run_dir / "saved.json")
            run_shim(
                env,
                payload,
                hook_args("PreToolUse", script=script, matcher="Edit|Write|MultiEdit"),
                cwd=_scratch(tmp_path),
                extra_env=extra,
            )
            received.append((run_dir / "saved.json").read_bytes())
        assert received[0] == received[1]
        assert json.loads(received[0])["tool_input"]["file_path"] == str(root / "src" / f"{accented}.txt")
