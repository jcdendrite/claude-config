"""Gate matrix: the generated entries' matchers, and the Recovery section every shim deny names."""

from __future__ import annotations

import ast
import re
import types
from pathlib import Path

import pytest
from helpers import REPO_ROOT

from ._cursor_gate_matrix_helpers import ALL_REGISTRATIONS, EXCEPTIONS, Registration
from ._cursor_identity_fixtures import HOST_PROCESS_ID
from ._cursor_payloads import (
    CONVERSATION_ID,
    SHIM,
    adapter_under_empty_home,  # noqa: F401 (the `adapter` fixture)
    build_spec,
    hook_args,
    load_shim,
    make_shim_env,
    recording_probe_script,
    run_shim,
    shim_json,
)

# --- generated entries: matchers and the Recovery section ---


RECOVERY_DOC = REPO_ROOT / "docs" / "cursor-adapter.md"
# The Cursor tool names each Claude tool name must reach, written here so the generator is not its own oracle. An overwrite's
# `Edit` is a Cursor `Write`, and the shim names it by the file's state (hook-shim.py `write_overwrites`).
CLAUDE_TO_CURSOR_NAMES = {
    "Bash": ("Shell", "Delete"),
    "Read": ("Read",),
    "Write": ("Write",),
    "Edit": ("Write",),
    "MultiEdit": ("Write",),
    "Agent": ("Task",),
    "Task": ("Task",),
    "Grep": ("Grep",),
    "WebFetch": ("WebFetch",),
    "WebSearch": ("WebSearch",),
}
# Per Cursor tool name: the builder spec that sends it on each tool event. A name with none gets the regex check only.
CURSOR_NAME_SPECS = {
    "Shell": {"PreToolUse": "shell_pre", "PostToolUse": "shell_post"},
    "Read": {"PreToolUse": "read_pre", "PostToolUse": "read_post"},
    "Write": {"PreToolUse": "write_create", "PostToolUse": "write_post"},
    "Grep": {"PreToolUse": "grep_pre", "PostToolUse": "grep_post"},
    "Delete": {"PreToolUse": "delete_pre"},
    "Task": {"PreToolUse": "task_pre"},
}


TOOL_EVENT_REGISTRATIONS = [
    r for r in ALL_REGISTRATIONS if r.event in ("PreToolUse", "PostToolUse") and r.key not in EXCEPTIONS
]


class TestGeneratedMatcherCoverage:
    """Every generated tool-event matcher selects each Cursor name the registration's Claude matcher reaches, and the shim
    runs the hook for exactly those names."""

    def test_the_registrations_under_test_are_the_unlisted_tool_event_ones(self) -> None:
        assert TOOL_EVENT_REGISTRATIONS, "no unlisted tool-event registration, so the matcher rows below test nothing"
        assert {r.event for r in TOOL_EVENT_REGISTRATIONS} == {"PreToolUse", "PostToolUse"}

    @pytest.mark.parametrize("registration", TOOL_EVENT_REGISTRATIONS, ids=lambda r: r.key)
    def test_every_matcher_token_of_an_unlisted_registration_has_its_own_cursor_names(self, registration: Registration) -> None:
        """The mapping above is the oracle, so a token missing from it fails here instead of being skipped by a `.get`."""
        assert registration.matcher_tokens <= set(CLAUDE_TO_CURSOR_NAMES), registration.key

    @pytest.mark.parametrize("registration", TOOL_EVENT_REGISTRATIONS, ids=lambda r: r.key)
    def test_the_generated_regex_selects_every_cursor_name_the_registrations_matcher_reaches(
        self, adapter: types.ModuleType, registration: Registration
    ) -> None:
        entries = [
            item
            for item in adapter.generate_entries(python_path="/p", only_key=registration.key)
            if item.event in ("preToolUse", "postToolUse")
        ]
        assert len(entries) == 1
        pattern = entries[0].entry["matcher"]
        for token in registration.matcher_tokens:
            for name in CLAUDE_TO_CURSOR_NAMES[token]:
                assert re.search(pattern, name), (registration.key, token, name, pattern)

    @pytest.mark.parametrize("registration", TOOL_EVENT_REGISTRATIONS, ids=lambda r: r.key)
    def test_the_shim_runs_the_hook_for_each_reached_name_and_prints_empty_output_for_every_other(
        self, tmp_path: Path, registration: Registration
    ) -> None:
        reached = {name for token in registration.matcher_tokens for name in CLAUDE_TO_CURSOR_NAMES[token]}
        checked = 0
        for name, specs in CURSOR_NAME_SPECS.items():
            spec_id = specs.get(registration.event)
            if spec_id is None:
                continue
            case_dir = tmp_path / f"case-{name}"
            case_dir.mkdir()
            env = make_shim_env(case_dir)
            outside = case_dir / "outside"
            outside.mkdir()
            saved = case_dir / "saved.json"
            probe = recording_probe_script(case_dir / "probe.sh", saved)
            payload = build_spec(spec_id, case_dir.resolve()).cursor
            args = hook_args(registration.event, script=probe, matcher=registration.matcher, label="probe", fail_closed=False)
            output = shim_json(run_shim(env, payload, args, cwd=outside))
            checked += 1
            assert saved.exists() is (name in reached), (registration.key, name, output)
            if name not in reached:
                assert output == {}, (registration.key, name)
        assert checked

    def test_no_unlisted_post_tool_use_registration_is_edit_only_because_a_post_tool_use_write_keeps_its_name(self) -> None:
        for registration in TOOL_EVENT_REGISTRATIONS:
            if registration.event == "PostToolUse":
                assert registration.matcher_tokens - {"Edit", "MultiEdit"}, registration.key

    @staticmethod
    def _pin_prefixed(shim: types.ModuleType, command: str) -> str:
        """The command as the pin rewrite leaves it: the environment line, the pin line, then the agent's text."""
        environment = shim.environment_line("/config", CONVERSATION_ID, HOST_PROCESS_ID)
        return environment + "\n" + shim.pin_line("/work") + "\n" + command

    def test_a_generated_before_shell_execution_matcher_selects_the_pin_prefixed_form_whenever_it_selects_the_command(
        self, adapter: types.ModuleType
    ) -> None:
        shim = adapter.load_shim()
        command = "git status"
        prefixed = self._pin_prefixed(shim, command)
        entries = [item for item in adapter.generate_entries(python_path="/p") if item.event == "beforeShellExecution"]
        assert entries
        assert self._entries_losing_the_prefixed_form(entries, command, prefixed) == []

    def test_the_entry_check_names_a_seeded_anchored_matcher_among_generated_entries_that_pass(
        self, adapter: types.ModuleType
    ) -> None:
        command = "git status"
        prefixed = self._pin_prefixed(adapter.load_shim(), command)
        generated = [item for item in adapter.generate_entries(python_path="/p") if item.event == "beforeShellExecution"]
        seeded = types.SimpleNamespace(label="seeded-anchored", entry={"matcher": "^git"})

        losing = self._entries_losing_the_prefixed_form([*generated, seeded], command, prefixed)

        assert losing == ["seeded-anchored"]

    @classmethod
    def _entries_losing_the_prefixed_form(cls, entries: list, command: str, prefixed: str) -> list[str]:
        return [
            item.label
            for item in entries
            if not cls._keeps_selecting_the_prefixed_form(item.entry.get("matcher"), command, prefixed)
        ]

    @staticmethod
    def _keeps_selecting_the_prefixed_form(pattern: str | None, command: str, prefixed: str) -> bool:
        return pattern is None or bool(re.search(pattern, command)) <= bool(re.search(pattern, prefixed))

    @pytest.mark.parametrize(
        ("pattern", "keeps_selecting"),
        [(None, True), ("git", True), ("^git", False), ("^builtin cd", True)],
        ids=["no-matcher", "unanchored", "anchored-at-the-command-start", "selects-neither-form"],
    )
    def test_the_anchoring_rule_flags_a_matcher_anchored_at_the_command_start_and_passes_an_unanchored_one(
        self, pattern: str | None, keeps_selecting: bool
    ) -> None:
        command = "git status"
        prefixed = self._pin_prefixed(load_shim(), command)

        assert self._keeps_selecting_the_prefixed_form(pattern, command, prefixed) is keeps_selecting


def deny_outcomes_in(source: str) -> set[str]:
    """The outcome names a shim deny carries: the first argument of each `Refusal` and the second of each `abnormal_output`."""
    argument_position = {"Refusal": 0, "abnormal_output": 1}
    outcomes = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in argument_position:
            position = argument_position[node.func.id]
            if len(node.args) > position and isinstance(node.args[position], ast.Constant):
                outcomes.add(node.args[position].value)
    return outcomes


DENY_OUTCOMES = sorted(deny_outcomes_in(SHIM.read_text()))


class TestRecoverySectionResolves:
    """Every shim deny names docs/cursor-adapter.md's Recovery section, so the entries the denies point at must exist."""

    @staticmethod
    def _recovery_section() -> tuple[str, dict[str, str]]:
        text = RECOVERY_DOC.read_text()
        section = text.split("\n## Recovery\n", 1)[1].split("\n## ", 1)[0]
        parts = re.split(r"^### ", section, flags=re.MULTILINE)
        return parts[0], {part.split("\n", 1)[0]: part for part in parts[1:]}

    def test_the_section_opens_with_the_sentence_every_deny_carries(self) -> None:
        opening, _ = self._recovery_section()
        assert opening.strip().startswith(load_shim().RECOVERY_SENTENCE)

    def test_every_deny_text_names_the_doc_and_the_recovery_section(self) -> None:
        text = load_shim().deny_text("hook", "outcome", "detail")
        assert "Recovery section of docs/cursor-adapter.md in the claude-config repository" in text

    def test_the_manual_restore_says_what_the_copy_discards_and_gives_the_lossless_form(self) -> None:
        opening, _ = self._recovery_section()
        assert "The copy discards every entry added to `hooks.json` since install." in opening
        assert (
            "delete by hand only the entries whose command holds `/.claude/scripts/cursor-adapter/` under your home directory"
            in opening
        )
        assert "The `.none` delete applies only when `hooks.json` holds nothing but those entries." in opening

    @pytest.mark.parametrize("outcome", DENY_OUTCOMES)
    def test_the_section_names_each_outcome_a_shim_deny_can_carry(self, outcome: str) -> None:
        opening, entries = self._recovery_section()
        section = opening + "\n".join(f"{heading}\n{body}" for heading, body in entries.items())
        assert f"`{outcome}`" in section, f"no Recovery entry names the deny outcome `{outcome}`"

    def test_the_outcome_reader_finds_outcomes_in_every_call_form_and_a_seeded_one(self) -> None:
        found = deny_outcomes_in("raise Refusal('seeded-refusal', 'x')\nreturn abnormal_output(meta, 'seeded-abnormal', 'x')\n")
        assert found == {"seeded-refusal", "seeded-abnormal"}
        assert {"pin-check", "exit-nonzero", "start-failed", "install-record"} <= set(DENY_OUTCOMES)

    @pytest.mark.parametrize(
        "heading_part",
        [
            "A deny from a marker-gated gate",
            "A host walk that found no host",
            "A worktree held by a live process",
            "A marker write that the sandbox denies",
            "`SESSION_ID empty`",
        ],
    )
    def test_the_section_holds_the_entries_the_adapters_own_texts_point_to(self, heading_part: str) -> None:
        _, entries = self._recovery_section()
        assert any(heading_part in heading for heading in entries), heading_part

    def test_the_host_walk_report_and_the_status_flag_name_a_recovery_entry_that_exists(self, adapter: types.ModuleType) -> None:
        """Both messages quote `HOST_WALK_NOT_FOUND_ENTRY`; the install suite's `status` row checks the flag's printed text."""
        heading = "A host walk that found no host"
        _, entries = self._recovery_section()
        assert heading == adapter.HOST_WALK_NOT_FOUND_ENTRY
        assert f'"{heading}"' in adapter.HOST_WALK_NOT_FOUND_LINE
        assert heading in entries

    def test_the_shim_fault_entrys_heading_quotes_the_fault_texts_diagnosis_clause(self, adapter: types.ModuleType) -> None:
        clause = adapter.FAULT_TEXT.split(". ", 1)[0]
        _, entries = self._recovery_section()
        assert clause in entries

    def test_the_start_failed_entrys_transient_item_names_exactly_the_shims_transient_set(self) -> None:
        _, entries = self._recovery_section()
        entry = next(part for heading, part in entries.items() if "start-failed" in heading)
        item = next(line for line in entry.splitlines() if line.startswith("- Transient causes"))
        assert set(re.findall(r"`(E[A-Z]+)`", item)) == set(load_shim().TRANSIENT_ERRNO_SYMBOLS)
