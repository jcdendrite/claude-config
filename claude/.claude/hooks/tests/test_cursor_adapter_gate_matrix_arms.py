"""Gate matrix: the unchanged harness hooks, run through the Cursor adapter's shim with no Cursor.

What the matrix does not cover: an informational hook listed in NO_WRITE_TRIGGER_YET runs a benign call only, so a state write
reached only by a trigger is not seen. A registration with a parity exception has no shim arm, only an import arm.

The process directory of the shim, near-miss and import arms is outside every repository, as Cursor runs user hooks. Each arm
first asserts that `git rev-parse --git-dir` fails there under its own environment, which carries no GIT_* name."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
from helpers import HOOKS_DIR, SCRIPTS_DIR, _hook_class, symlink_hooks_lib_chain

from . import _cursor_gate_matrix_helpers as gate_matrix_helpers
from ._cursor_gate_matrix_helpers import (
    ALL_REGISTRATIONS,
    DIRECT_HOOK_TIMEOUT_SECONDS,
    EXCEPTIONS,
    GATE_CLASSES,
    NO_TRIGGER_TOOL_CLASSES,
    NO_TRIGGER_YET,
    TRIGGER_TOOL_CLASSES,
    TRIGGERS,
    Registration,
    Trigger,
    World,
    _arm_args,
    _decision,
    _direct,
    _memory_path,
    _row_matcher,
    _run_outside,
    _write_memory_index,
)
from ._cursor_payloads import (
    CHILD_CONVERSATION_ID,
    CONVERSATION_ID,
    DELIVERED_PHASE_NUMBER,
    PARENT_CONVERSATION_ID,
    TOOL_MAP_JSON,
    assert_outside_any_repository,
    build_spec,
    file_tool_payload,
    hook_args,
    load_json,
    parse_registration_key,
    phase_number,
    read_log_lines,
    recording_probe_script,
    shell_pre_payload,
    shim_argv,
    without_git_environment,
    write_hook_script,
)

# The fixture lives once in the helper module. An import would trip F811 on every test parameter that names it.
world = gate_matrix_helpers.world


# --- the matrix ---


GATE_REGISTRATIONS = [r for r in ALL_REGISTRATIONS if r.hook_class in GATE_CLASSES]
INFORMATIONAL_REGISTRATIONS = [r for r in ALL_REGISTRATIONS if r.hook_class == "informational"]


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
        "PreToolUse|Bash|require-worktree-for-git-writes.sh",
        "PreToolUse|Edit|Write|MultiEdit|require-worktree-for-file-writes.sh",
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


# --- triggers ---


TRIGGER_CASES = [(r, t) for r in SHIM_ARM_REGISTRATIONS for t in TRIGGERS.get(r.script, ())]


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
# The two session writers whose registrations the adapter declines, each with an empty write allowance. The import arm runs a
# Cursor-shaped payload with CLAUDECODE unset, so neither writes whatever the host walk returns. `TestCursorPayloadDecline` in
# test_lib_cursor_identity.py checks the decline for each CLAUDECODE state.
DECLINED_SESSION_WRITES: dict[str, tuple[str, ...]] = {
    "capture-session-id.sh": (),
    "record-session-end.sh": (),
}
# Rows whose hook may call a stubbed network command.
LISTED_NETWORK: frozenset[str] = frozenset()


# Events with no tool name. Cursor has no Notification or PostToolBatch event, so those borrow the closest payload.
# The `trigger` field of `preCompact` is `doc-only` in field-table.json (documented, not seen in a recorded payload), so
# PreCompact borrows a payload too.
# The borrowed payload twins to Stop and the steering hook's self-filter silences it, so the import arm proves silence only.
EVENT_SPEC_IDS = {
    "SessionStart": "session_start",
    "SubagentStart": "subagent_start",
    "SubagentStop": "subagent_stop",
    "UserPromptSubmit": "prompt_submit",
    "Stop": "stop",
    "SessionEnd": "after_agent_response",
    "Notification": "after_agent_response",
    "PostToolBatch": "after_agent_response",
    "PreCompact": "after_agent_response",
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


def _shim_arm(world: World, registration: Registration, payload: dict) -> subprocess.CompletedProcess:
    world.reset()
    return _run_outside(world, shim_argv(*_arm_args(registration)), payload)


def _import_arm(world: World, registration: Registration, payload: dict) -> subprocess.CompletedProcess:
    """The raw Cursor payload straight to the script."""
    return _run_outside(world, [str(registration.path)], payload)


def _import_decisions(registration: Registration) -> tuple[str, ...]:
    """What an import arm may give: no output or a deny, and for an informational hook its context output too."""
    if registration.hook_class == "informational":
        return ("none", "deny", "ask", "other")
    return ("none", "deny", "ask")


def _reference_reason(result: subprocess.CompletedProcess) -> str:
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

    def test_every_registration_parity_exception_names_a_current_registration(self) -> None:
        current_keys = {r.key for r in ALL_REGISTRATIONS}
        assert EXCEPTIONS and current_keys
        assert sorted(set(EXCEPTIONS) - current_keys) == []

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

    def test_only_the_two_declined_session_writers_are_listed_for_the_import_arm_and_the_declined_set_adds_the_floor_check(
        self,
    ) -> None:
        assert set(DECLINED_SESSION_WRITES) == {"capture-session-id.sh", "record-session-end.sh"}
        assert {e["key"].rsplit("|", 1)[-1] for e in EXCEPTIONS.values() if e["reason"] == "declined"} == {
            *DECLINED_SESSION_WRITES,
            "check-python-floor.sh",
        }

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


# The stderr line each declined registration prints in the import arm. Cursor's recorded sessionEnd keys name no `session_id`, so
# record-session-end.sh stops at its own session-id check and never reaches the decline.
# A sessionEnd payload that carries `session_id` is declined, as `TestCursorPayloadDecline` in test_lib_cursor_identity.py pins.
DECLINED_SESSION_IMPORT_STDERR = {
    "SessionStart": "[capture-session-id] untranslated Cursor payload declined; lookup file not written",
    "SubagentStart": "[capture-session-id] untranslated Cursor payload declined; lookup file not written",
    "SessionEnd": "[record-session-end] no session_id in payload; no SessionEnd record written",
}
DECLINED_SESSION_REGISTRATIONS = [r for r in ALL_REGISTRATIONS if r.script in DECLINED_SESSION_WRITES]


class TestDeclinedSessionHooksImportArm:
    def test_the_declined_registrations_are_the_three_session_events(self) -> None:
        assert {r.event for r in DECLINED_SESSION_REGISTRATIONS} == set(DECLINED_SESSION_IMPORT_STDERR)

    @pytest.mark.parametrize("registration", DECLINED_SESSION_REGISTRATIONS, ids=lambda r: r.key)
    def test_the_import_arm_on_the_events_recorded_shape_writes_nothing_and_prints_its_one_line(
        self, world: World, registration: Registration
    ) -> None:
        payload = _benign_payload(registration, world)
        world.reset()
        before = world.snapshot(ignore_state=False)
        result = _import_arm(world, registration, payload)
        assert result.returncode == 0, result.stderr
        assert result.stdout == b""
        assert result.stderr.decode().strip() == DECLINED_SESSION_IMPORT_STDERR[registration.event]
        assert_no_effects(before, world.snapshot(ignore_state=False), world, *_listed(registration, import_arm=True))


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
        subprocess.run(
            [str(script)],
            input=b"{}",
            capture_output=True,
            check=False,
            cwd=world.outside,
            env=world.cursor_env(),
            timeout=DIRECT_HOOK_TIMEOUT_SECONDS,
        )
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
            [str(hook)],
            input=json.dumps(payload).encode(),
            capture_output=True,
            check=False,
            cwd=world.outside,
            env=environment,
            timeout=DIRECT_HOOK_TIMEOUT_SECONDS,
        )
        deadline = time.monotonic() + LAUNCH_DEADLINE_SECONDS
        while not launched.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert launched.exists(), "the positive control never launched the stub, so the shim row proves nothing"
