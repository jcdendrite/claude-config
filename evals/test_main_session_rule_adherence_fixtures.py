"""Tripwire for evals/fixtures/main-session-rule-adherence/: the recorded streams are committed to a public repository,
so none may carry a home-rooted path, a temp-directory path, a real session id, an account listing or a live-run cost.

This tripwire plus the closed-set shape pins below are the whole contract for a recorded stream, because the scrubber
that produced the fixtures is not in the repository."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "main-session-rule-adherence"
FIXTURE_FILES = sorted(path for path in FIXTURE_DIR.rglob("*") if path.is_file())
FIXTURE_SUFFIX = ".jsonl"

_HYPHENATED_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
_UNHYPHENATED_HEX_RE = re.compile(r"[0-9a-f]{32,}", re.IGNORECASE)
_HOME_ROOT = "/" + "Users" + "/"
_LINUX_HOME_ROOT = "/" + "home" + "/"
# The project-state directory name Claude Code derives from a path replaces every "/" with "-".
_PROJECT_SLUG_FRAGMENTS = ("-" + "Users" + "-", "-" + "home" + "-", "-private-var-")
_WINDOWS_DRIVE_ROOT_RE = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]")
_FORBIDDEN_PATH_RULES = (
    *(
        (fragment, re.compile(re.escape(fragment)))
        for fragment in (
            _HOME_ROOT, _LINUX_HOME_ROOT, *_PROJECT_SLUG_FRAGMENTS, "/root/", "/Volumes/", "/opt/", "/mnt/",
            "/var/folders", "/private/var", "/private/tmp", "/tmp/",
        )
    ),
    ("<windows drive root>", _WINDOWS_DRIVE_ROOT_RE),
)

_PLACEHOLDER_MESSAGE_ID_RE = re.compile(r"msg_FIXTURE\d{4}")
_PLACEHOLDER_TOOL_USE_ID_RE = re.compile(r"toolu_FIXTURE\d{4}")
_PLACEHOLDER_SESSION_ID_RE = re.compile(r"fixture-session-\d{2}")
_PLACEHOLDER_ID_KEYS = ("session_id", "uuid", "hook_id")
_ELIDED_TEXT = "<text elided>"
_DENIAL_RESULT_CONTENTS = frozenset(
    f"PreToolUse:{tool} hook error: This call is not available in this session."
    for tool in ("Agent", "Bash", "Edit", "Skill")
)
_SYNTHETIC_HOOK_TEXTS = frozenset({"", "synthetic block reason\n"})
_SYNTHETIC_HOOK_EVENT = "UserPromptSubmit"
_SYNTHETIC_BLOCKED_PROMPT_CONTENT = (
    "UserPromptSubmit operation blocked by hook:\n[<hook-command>]: synthetic block reason\n\n\nOriginal prompt: <prompt>"
)
_SYNTHETIC_BLOCK_REASON = "synthetic block reason"

# Keys each event type carries in the committed streams; any other key fails.
_MESSAGE_EVENT_KEYS = frozenset({"type", "parent_tool_use_id", "session_id", "message"})
_ASSISTANT_MESSAGE_KEYS = frozenset({"model", "id", "role", "content"})
_USER_MESSAGE_KEYS = frozenset({"role", "content"})
_CONTENT_BLOCK_KEYS = {
    "text": frozenset({"type", "text"}),
    "tool_use": frozenset({"type", "id", "name", "input"}),
    "tool_result": frozenset({"type", "tool_use_id", "is_error", "content"}),
}
_RESULT_EVENT_KEYS = frozenset({
    "type", "subtype", "is_error", "api_error_status", "num_turns", "total_cost_usd", "stop_reason", "terminal_reason",
    "session_id",
})
_RATE_LIMIT_EVENT_KEYS = frozenset({"type", "rate_limit_info", "uuid", "session_id"})
# Only the key the harness reads from a healthy event: a live event also carries the account's window boundary and
# credit state.
_RATE_LIMIT_INFO_KEYS = frozenset({"status"})
_SYSTEM_EVENT_KEYS = {
    "hook_response": frozenset({
        "type", "subtype", "hook_id", "hook_name", "hook_event", "output", "stdout", "stderr", "exit_code", "outcome",
        "session_id",
    }),
    "informational": frozenset({
        "type", "subtype", "content", "level", "prevent_continuation", "prompt_submit_outcome", "session_id",
    }),
}
_PROMPT_SUBMIT_OUTCOME_KEYS = frozenset({"kind", "prompt_kept", "reason"})


def _json_strings(value: object) -> Iterator[str]:
    """Every object key and string value in a decoded JSON document, so escaped forms are seen decoded."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _json_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _json_strings(item)


def _text_views(text: str) -> list[str]:
    """The raw text plus the decoded strings of every line that parses as JSON."""
    views = [text]
    for line in text.splitlines():
        try:
            views.extend(_json_strings(json.loads(line)))
        except ValueError:
            continue
    return views


def path_leaks(text: str) -> list[str]:
    views = _text_views(text)
    return [label for label, rule in _FORBIDDEN_PATH_RULES if any(rule.search(view) for view in views)]


def real_session_ids(text: str) -> list[str]:
    found = (
        match
        for view in _text_views(text)
        for rule in (_HYPHENATED_UUID_RE, _UNHYPHENATED_HEX_RE)
        for match in rule.findall(view)
    )
    return list(dict.fromkeys(found))


def holds_account_listing(text: str) -> bool:
    """True when a line is a system init event, or any event carries an mcp_servers listing."""
    for line in text.splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if isinstance(event, dict) and (event.get("subtype") == "init" or "mcp_servers" in event):
            return True
    return False


def _live_costs(value: object) -> list[str]:
    """Cost-keyed values that are not a round synthetic number: a live run's cost carries float noise."""
    flagged: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if "cost" in key.lower():
                is_noisy_number = isinstance(item, Decimal) and -item.as_tuple().exponent > 4
                if is_noisy_number or isinstance(item, str):
                    flagged.append(f"{key}={item}")
            flagged.extend(_live_costs(item))
    elif isinstance(value, list):
        for item in value:
            flagged.extend(_live_costs(item))
    return flagged


def long_float_costs(text: str) -> list[str]:
    flagged: list[str] = []
    for line in text.splitlines():
        if line.strip():
            flagged.extend(_live_costs(json.loads(line, parse_float=Decimal)))
    return flagged


def non_jsonl_files(paths: list[Path]) -> list[str]:
    return [path.name for path in paths if path.suffix != FIXTURE_SUFFIX]


def _is_placeholder(rule: re.Pattern[str], value: object) -> bool:
    return isinstance(value, str) and rule.fullmatch(value) is not None


def _unexpected_keys(where: str, mapping: dict, allowed: frozenset[str]) -> list[str]:
    return [f"{where}: unexpected key {key!r}" for key in sorted(mapping.keys() - allowed)]


def _content_block_violations(block: object, allowed_types: tuple[str, ...]) -> list[str]:
    if not isinstance(block, dict):
        return ["content block is not an object"]
    block_type = block.get("type")
    if block_type not in allowed_types:
        return [f"content block type {block_type!r} is not allowed in this event"]
    violations = _unexpected_keys(f"{block_type} block", block, _CONTENT_BLOCK_KEYS[block_type])
    if block_type == "text" and block.get("text") != _ELIDED_TEXT:
        violations.append("text block is not the elided placeholder")
    if block_type == "tool_use":
        if not _is_placeholder(_PLACEHOLDER_TOOL_USE_ID_RE, block.get("id")):
            violations.append("tool_use id is not a toolu_FIXTURE placeholder")
        if not isinstance(block.get("input"), dict):
            violations.append("tool_use input is not an object")
    if block_type == "tool_result":
        if not _is_placeholder(_PLACEHOLDER_TOOL_USE_ID_RE, block.get("tool_use_id")):
            violations.append("tool_result tool_use_id is not a toolu_FIXTURE placeholder")
        if block.get("content") not in _DENIAL_RESULT_CONTENTS:
            violations.append("tool_result content is not a synthetic denial string")
    return violations


def _message_event_violations(event: dict) -> list[str]:
    kind = event["type"]
    violations = _unexpected_keys(f"{kind} event", event, _MESSAGE_EVENT_KEYS)
    parent = event.get("parent_tool_use_id")
    if parent is not None and not _is_placeholder(_PLACEHOLDER_TOOL_USE_ID_RE, parent):
        violations.append("parent_tool_use_id is not a toolu_FIXTURE placeholder")
    message = event.get("message")
    if not isinstance(message, dict):
        return [*violations, f"{kind} message is not an object"]
    is_assistant = kind == "assistant"
    violations += _unexpected_keys(
        f"{kind} message", message, _ASSISTANT_MESSAGE_KEYS if is_assistant else _USER_MESSAGE_KEYS,
    )
    if is_assistant and not _is_placeholder(_PLACEHOLDER_MESSAGE_ID_RE, message.get("id")):
        violations.append("message.id is not a msg_FIXTURE placeholder")
    content = message.get("content")
    if not isinstance(content, list):
        return [*violations, f"{kind} message content is not a list"]
    allowed_types = ("text", "tool_use") if is_assistant else ("tool_result",)
    for block in content:
        violations += _content_block_violations(block, allowed_types)
    return violations


def _system_event_violations(event: dict) -> list[str]:
    subtype = event.get("subtype")
    if subtype not in _SYSTEM_EVENT_KEYS:
        return [f"system subtype {subtype!r} is not allowed"]
    violations = _unexpected_keys(f"system {subtype} event", event, _SYSTEM_EVENT_KEYS[subtype])
    if subtype == "hook_response":
        for key in ("output", "stdout", "stderr"):
            if key in event and event[key] not in _SYNTHETIC_HOOK_TEXTS:
                violations.append(f"hook_response {key} is not a synthetic string")
        for key in ("hook_name", "hook_event"):
            if key in event and event[key] != _SYNTHETIC_HOOK_EVENT:
                violations.append(f"hook_response {key} is not the synthetic hook event")
    else:
        if "content" in event and event["content"] != _SYNTHETIC_BLOCKED_PROMPT_CONTENT:
            violations.append("informational content is not the synthetic blocked-prompt string")
        outcome = event.get("prompt_submit_outcome")
        if isinstance(outcome, dict):
            violations += _unexpected_keys("prompt_submit_outcome", outcome, _PROMPT_SUBMIT_OUTCOME_KEYS)
            if outcome.get("reason") != _SYNTHETIC_BLOCK_REASON:
                violations.append("prompt_submit_outcome reason is not the synthetic block reason")
    return violations


def shape_violations(line: str) -> list[str]:
    """Every way a recorded line steps outside the closed set of event shapes the committed streams use."""
    try:
        event = json.loads(line)
    except ValueError:
        return ["line is not valid JSON"]
    if not isinstance(event, dict):
        return ["line is not a JSON object"]
    kind = event.get("type")
    if kind in ("assistant", "user"):
        violations = _message_event_violations(event)
    elif kind == "system":
        violations = _system_event_violations(event)
    elif kind == "result":
        violations = _unexpected_keys("result event", event, _RESULT_EVENT_KEYS)
    elif kind == "rate_limit_event":
        violations = _unexpected_keys("rate_limit_event", event, _RATE_LIMIT_EVENT_KEYS)
        info = event.get("rate_limit_info")
        if isinstance(info, dict):
            violations += _unexpected_keys("rate_limit_info", info, _RATE_LIMIT_INFO_KEYS)
    else:
        return [f"unknown event type {kind!r}"]
    for key in _PLACEHOLDER_ID_KEYS:
        if key in event and not _is_placeholder(_PLACEHOLDER_SESSION_ID_RE, event[key]):
            violations.append(f"{key} is not a fixture-session placeholder")
    return violations


def test_the_fixture_directory_holds_the_recorded_streams() -> None:
    names = {path.name for path in FIXTURE_FILES}

    assert {
        "o1-adherent.jsonl", "o2-adherent.jsonl", "o3-adherent.jsonl", "o4-adherent.jsonl", "o4-non-adherent.jsonl",
        "blocked-prompt.jsonl", "healthy-allowed-rate-limit.jsonl",
    } <= names


def test_the_fixture_directory_holds_only_jsonl_streams() -> None:
    assert non_jsonl_files(FIXTURE_FILES) == []


def _fixture_id(path: Path) -> str:
    return path.relative_to(FIXTURE_DIR).as_posix()


@pytest.mark.parametrize("fixture", FIXTURE_FILES, ids=_fixture_id)
def test_no_fixture_holds_a_home_rooted_or_temp_path(fixture: Path) -> None:
    assert path_leaks(fixture.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("fixture", FIXTURE_FILES, ids=_fixture_id)
def test_no_fixture_holds_a_real_session_id(fixture: Path) -> None:
    assert real_session_ids(fixture.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("fixture", FIXTURE_FILES, ids=_fixture_id)
def test_no_fixture_holds_the_account_listing_a_system_init_event_carries(fixture: Path) -> None:
    assert holds_account_listing(fixture.read_text(encoding="utf-8")) is False


@pytest.mark.parametrize("fixture", FIXTURE_FILES, ids=_fixture_id)
def test_no_fixture_holds_a_live_run_cost(fixture: Path) -> None:
    assert long_float_costs(fixture.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("fixture", FIXTURE_FILES, ids=_fixture_id)
def test_every_fixture_line_stays_inside_the_closed_set_of_event_shapes(fixture: Path) -> None:
    violations = {
        number: shape_violations(line)
        for number, line in enumerate(fixture.read_text(encoding="utf-8").splitlines(), start=1)
        if line.strip()
    }

    assert {number: found for number, found in violations.items() if found} == {}


class TestTheTripwireFlagsASeededViolation:
    def test_a_home_rooted_path(self) -> None:
        assert path_leaks('{"cwd": "' + _HOME_ROOT + 'someone/project"}') == [_HOME_ROOT]

    @pytest.mark.parametrize(
        "path",
        [
            "/" + "root" + "/work",
            "/" + "Volumes" + "/disk/work",
            "/" + "opt" + "/work",
            "/" + "mnt" + "/work",
            "C" + ":\\work",
            "D" + ":/work",
        ],
    )
    def test_another_absolute_path_root(self, path) -> None:
        assert path_leaks(json.dumps({"cwd": path})) != []

    @pytest.mark.parametrize("slug", _PROJECT_SLUG_FRAGMENTS)
    def test_a_project_state_directory_slug(self, slug) -> None:
        assert path_leaks(json.dumps({"dir": "projects/" + slug + "someone-work"})) == [slug]

    @pytest.mark.parametrize(
        "escaped_slash",
        ["\\/", "\\u002f", "\\u002F"],
        ids=["escaped-solidus", "unicode-escape", "unicode-escape-uppercase"],
    )
    def test_a_home_rooted_path_written_with_json_escapes(self, escaped_slash) -> None:
        line = '{"cwd": "' + escaped_slash + "Users" + escaped_slash + 'someone"}'

        assert path_leaks(line) == [_HOME_ROOT]

    def test_a_url_scheme_is_not_a_drive_root(self) -> None:
        assert path_leaks('{"url": "https' + "://example.org/page" + '"}') == []

    def test_a_real_session_id(self) -> None:
        seeded = "-".join(("3f2b8c1e", "aaaa", "bbbb", "cccc", "1234567890ab"))
        assert real_session_ids('{"session_id": "' + seeded + '"}') == [seeded]

    def test_an_uppercase_session_id(self) -> None:
        seeded = "-".join(("3F2B8C1E", "AAAA", "BBBB", "CCCC", "1234567890AB"))
        assert real_session_ids('{"session_id": "' + seeded + '"}') == [seeded]

    def test_an_unhyphenated_hex_identifier(self) -> None:
        seeded = "3f2b8c1e" * 4
        assert real_session_ids('{"hardware": "' + seeded + '"}') == [seeded]

    def test_a_session_id_written_with_json_escapes(self) -> None:
        seeded = "3f2b8c1e" * 4
        escaped = seeded[:16] + "\\u0033" + seeded[17:]
        assert real_session_ids('{"hardware": "' + escaped + '"}') == [seeded]

    def test_a_placeholder_session_id_passes(self) -> None:
        assert real_session_ids('{"session_id": "fixture-session-01"}') == []

    @pytest.mark.parametrize(
        "line",
        [
            '{"type": "system", "subtype": "init", "cwd": "x"}',
            '{"type":"system","subtype":"init"}',
            '{"type": "system", "subtype": "status", "mcp_servers": []}',
        ],
    )
    def test_an_init_event_in_either_spacing_or_an_mcp_server_listing(self, line) -> None:
        assert holds_account_listing(line + "\n") is True

    def test_an_event_that_is_not_an_init_event_passes(self) -> None:
        assert holds_account_listing('{"type": "result", "subtype": "success"}\n') is False

    @pytest.mark.parametrize("cost", ["0.30000000000000004", "0.987654", "0.12345"])
    def test_a_live_run_cost(self, cost) -> None:
        assert long_float_costs(f'{{"total_cost_usd": {cost}}}') != []

    @pytest.mark.parametrize("cost", ["0.1", "0.25", "0", "0.1234"])
    def test_a_round_synthetic_cost_passes(self, cost) -> None:
        assert long_float_costs(f'{{"total_cost_usd": {cost}}}') == []

    def test_a_live_cost_under_any_key_containing_cost(self) -> None:
        assert long_float_costs('{"modelUsage": {"some-model": {"costUSD": 0.987654}}}') != []

    def test_a_cost_written_as_a_string(self) -> None:
        assert long_float_costs('{"total_cost_usd": "0.1"}') != []

    def test_a_file_that_is_not_a_jsonl_stream(self) -> None:
        paths = [Path("stream.json"), Path("stream.ndjson"), Path("stream" + FIXTURE_SUFFIX)]

        assert non_jsonl_files(paths) == ["stream.json", "stream.ndjson"]


def _assistant_tool_use_event() -> dict:
    return {
        "type": "assistant", "parent_tool_use_id": None, "session_id": "fixture-session-01",
        "message": {
            "model": "claude-sonnet-5-5", "id": "msg_FIXTURE0001", "role": "assistant",
            "content": [{"type": "tool_use", "id": "toolu_FIXTURE0001", "name": "Bash", "input": {"command": "ls"}}],
        },
    }


def _assistant_text_event() -> dict:
    event = _assistant_tool_use_event()
    event["message"]["content"] = [{"type": "text", "text": _ELIDED_TEXT}]
    return event


def _denial_event() -> dict:
    content = "PreToolUse:Bash hook error: This call is not available in this session."
    return {
        "type": "user", "parent_tool_use_id": None, "session_id": "fixture-session-01",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "toolu_FIXTURE0001", "is_error": True, "content": content}],
        },
    }


def _result_event() -> dict:
    return {
        "type": "result", "session_id": "fixture-session-01", "subtype": "success", "is_error": False,
        "api_error_status": None, "num_turns": 2, "total_cost_usd": 0.1, "stop_reason": "end_turn",
        "terminal_reason": "completed",
    }


def _rate_limit_event() -> dict:
    return {
        "type": "rate_limit_event",
        "rate_limit_info": {"status": "allowed"},
        "uuid": "fixture-session-25", "session_id": "fixture-session-03",
    }


def _hook_response_event() -> dict:
    return {
        "type": "system", "subtype": "hook_response", "hook_id": "fixture-session-11", "hook_name": "UserPromptSubmit",
        "hook_event": "UserPromptSubmit", "output": "synthetic block reason\n", "stdout": "",
        "stderr": "synthetic block reason\n", "exit_code": 2, "outcome": "error", "session_id": "fixture-session-01",
    }


def _blocked_prompt_event() -> dict:
    return {
        "type": "system", "subtype": "informational", "content": _SYNTHETIC_BLOCKED_PROMPT_CONTENT, "level": "warning",
        "prevent_continuation": True,
        "prompt_submit_outcome": {"kind": "blocked_by_hook", "prompt_kept": False, "reason": _SYNTHETIC_BLOCK_REASON},
        "session_id": "fixture-session-01",
    }


def _flags(event: dict, pin: str) -> bool:
    return any(pin in violation for violation in shape_violations(json.dumps(event)))


class TestTheShapePinsFlagASeededViolation:
    @pytest.mark.parametrize(
        "build_event",
        [
            _assistant_tool_use_event, _assistant_text_event, _denial_event, _result_event, _rate_limit_event,
            _hook_response_event, _blocked_prompt_event,
        ],
    )
    def test_a_well_formed_event_passes(self, build_event) -> None:
        assert shape_violations(json.dumps(build_event())) == []

    def test_a_line_that_is_not_json(self) -> None:
        assert shape_violations("not json") == ["line is not valid JSON"]

    def test_a_line_that_is_not_an_object(self) -> None:
        assert shape_violations("[]") == ["line is not a JSON object"]

    def test_an_unknown_top_level_event_type(self) -> None:
        assert _flags({"type": "stream_event"}, "unknown event type")

    def test_a_system_event_that_is_not_one_of_the_recorded_subtypes(self) -> None:
        assert _flags({"type": "system", "subtype": "init", "cwd": "x"}, "system subtype")

    def test_an_unexpected_key_on_a_message_event(self) -> None:
        event = _assistant_tool_use_event()
        event["cwd"] = "x"
        assert _flags(event, "unexpected key 'cwd'")

    def test_an_unexpected_key_on_a_message(self) -> None:
        event = _assistant_tool_use_event()
        event["message"]["usage"] = {}
        assert _flags(event, "unexpected key 'usage'")

    def test_an_unexpected_key_on_a_content_block(self) -> None:
        event = _assistant_tool_use_event()
        event["message"]["content"][0]["caller"] = {}
        assert _flags(event, "unexpected key 'caller'")

    @pytest.mark.parametrize("key", ["modelUsage", "usage", "permission_denials", "duration_ms", "result"])
    def test_an_unexpected_key_on_a_result_event(self, key) -> None:
        event = _result_event()
        event[key] = {}
        assert _flags(event, f"unexpected key {key!r}")

    def test_an_unexpected_key_on_a_rate_limit_info(self) -> None:
        event = _rate_limit_event()
        event["rate_limit_info"]["plan"] = "x"
        assert _flags(event, "unexpected key 'plan'")

    @pytest.mark.parametrize(
        "key", ["resetsAt", "rateLimitType", "overageStatus", "overageDisabledReason", "isUsingOverage"],
    )
    def test_a_rate_limit_info_key_that_carries_account_state(self, key) -> None:
        event = _rate_limit_event()
        event["rate_limit_info"][key] = "x"
        assert _flags(event, f"unexpected key {key!r}")

    def test_an_unexpected_key_on_a_hook_response(self) -> None:
        event = _hook_response_event()
        event["command"] = "x"
        assert _flags(event, "unexpected key 'command'")

    def test_a_message_id_that_is_not_a_placeholder(self) -> None:
        event = _assistant_tool_use_event()
        event["message"]["id"] = "msg_01" + "AbCdEfGhIjKlMnOp"
        assert _flags(event, "message.id")

    def test_a_tool_use_id_that_is_not_a_placeholder(self) -> None:
        event = _assistant_tool_use_event()
        event["message"]["content"][0]["id"] = "toolu_01" + "AbCdEfGhIjKlMnOp"
        assert _flags(event, "tool_use id")

    def test_a_tool_result_id_that_is_not_a_placeholder(self) -> None:
        event = _denial_event()
        event["message"]["content"][0]["tool_use_id"] = "toolu_01" + "AbCdEfGhIjKlMnOp"
        assert _flags(event, "tool_result tool_use_id")

    def test_a_parent_tool_use_id_that_is_not_a_placeholder(self) -> None:
        event = _assistant_tool_use_event()
        event["parent_tool_use_id"] = "toolu_01" + "AbCdEfGhIjKlMnOp"
        assert _flags(event, "parent_tool_use_id")

    @pytest.mark.parametrize("build_event", [_assistant_tool_use_event, _result_event])
    def test_a_session_id_that_is_not_a_placeholder(self, build_event) -> None:
        event = build_event()
        event["session_id"] = "session-live"
        assert _flags(event, "session_id")

    def test_a_rate_limit_uuid_that_is_not_a_placeholder(self) -> None:
        event = _rate_limit_event()
        event["uuid"] = "live-event"
        assert _flags(event, "uuid")

    def test_a_hook_id_that_is_not_a_placeholder(self) -> None:
        event = _hook_response_event()
        event["hook_id"] = "live-hook"
        assert _flags(event, "hook_id")

    def test_an_assistant_text_block_that_is_not_elided(self) -> None:
        event = _assistant_text_event()
        event["message"]["content"][0]["text"] = "I will now look at the layout code."
        assert _flags(event, "text block")

    def test_a_thinking_block(self) -> None:
        event = _assistant_text_event()
        event["message"]["content"] = [{"type": "thinking", "thinking": _ELIDED_TEXT, "signature": ""}]
        assert _flags(event, "content block type 'thinking'")

    def test_a_user_event_that_carries_text_instead_of_a_tool_result(self) -> None:
        event = _denial_event()
        event["message"]["content"] = [{"type": "text", "text": _ELIDED_TEXT}]
        assert _flags(event, "content block type 'text'")

    def test_a_tool_result_content_outside_the_synthetic_denial_strings(self) -> None:
        event = _denial_event()
        event["message"]["content"][0]["content"] = "On branch main\nnothing to commit"
        assert _flags(event, "tool_result content")

    @pytest.mark.parametrize("key", ["output", "stdout", "stderr"])
    def test_a_hook_response_text_that_is_not_synthetic(self, key) -> None:
        event = _hook_response_event()
        event[key] = "account context printed by a hook"
        assert _flags(event, f"hook_response {key}")

    def test_a_hook_name_that_is_not_the_synthetic_hook_event(self) -> None:
        event = _hook_response_event()
        event["hook_name"] = "SessionStart"
        assert _flags(event, "hook_response hook_name")

    def test_an_informational_content_that_is_not_synthetic(self) -> None:
        event = _blocked_prompt_event()
        event["content"] = "blocked: the project is rooted elsewhere"
        assert _flags(event, "informational content")

    def test_a_prompt_submit_reason_that_is_not_synthetic(self) -> None:
        event = _blocked_prompt_event()
        event["prompt_submit_outcome"]["reason"] = "account is over its limit"
        assert _flags(event, "prompt_submit_outcome reason")
