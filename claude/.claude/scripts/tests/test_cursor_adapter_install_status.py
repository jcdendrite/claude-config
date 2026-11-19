"""cursor-adapter.py status: the tamper comparison, what it reads from the log, its exit status and its Shell canary."""

from __future__ import annotations

import json
import re
import sys

import pytest

from . import _cursor_install_helpers as install_helpers
from ._cursor_install_helpers import (
    HOOK_ENV_SURFACE,
    ORIGINAL_HOOKS,
    SURFACES,
    World,
    find_entry,
    hooks_document,
    load_adapter_copy_with,
    mutate_remove,
    run_surface,
    save_hooks,
)

# The fixtures live once in the helper module. An import would trip F811 on every test parameter that names one.
world = install_helpers.world
fast_world = install_helpers.fast_world


TAMPER_PHRASES = (
    "missing or altered entry",
    "owned entry differs",
    "owned entry with no emitted counterpart",
    "duplicated owned entry",
    "foreign entry that holds",
    "non-owned entr",
    "top-level",
)
# An event key with terminal escapes, a bidi override, a line break and 64 KiB of padding, none of which may reach a report.
HOSTILE_EVENT_KEY = "\x1b[31m\x85‮\nHOSTILEKEY" + "A" * 65_536
HOSTILE_EVENT_KEY_FRAGMENTS = ("HOSTILEKEY", "\x1b", "\x85", "‮", "A" * 100)


def expected_tamper_lines(case: str, expectation: str, label: str, event: str, index: int) -> list[str]:
    """The lines a report holds for a tamper case on the entry `label` at `event` [`index`], per the case's expectation kind."""
    if expectation == "argument":
        expectation = "foreign" if label == "check" else "changed"
    missing = f"missing or altered entry: {label} on {event}"
    return {
        "missing": [missing],
        "changed": [f"owned entry differs from what generate emits now: {label} on {event}"],
        "foreign": [missing, f"foreign entry that holds an adapter path: {event} [{index}]"],
        "moved": [missing, f"owned entry with no emitted counterpart: {label} on stop"],
        "duplicated": [f"duplicated owned entry: {label} on {event}"],
    }[expectation]


# --- the tamper comparison and status ---


def mutate_matcher(document, event, index):
    entry = document["hooks"][event][index]
    entry["matcher"] = "Altered" if "matcher" not in entry else entry["matcher"] + "|X"


def mutate_fail_closed(document, event, index):
    entry = document["hooks"][event][index]
    if "failClosed" in entry:
        del entry["failClosed"]
    else:
        entry["failClosed"] = True


def mutate_timeout(document, event, index):
    document["hooks"][event][index]["timeout"] += 1


def mutate_or_true(document, event, index):
    document["hooks"][event][index]["command"] += " || true"


def mutate_argument(document, event, index):
    entry = document["hooks"][event][index]
    entry["command"] = entry["command"].replace("--deadline 10", "--deadline 11").replace("--hook-env", "--hook-env --x")


def mutate_python(document, event, index):
    entry = document["hooks"][event][index]
    guard_head_and_python = entry["command"].split(" -I -S ", 1)[0]
    entry["command"] = entry["command"].replace(guard_head_and_python, "{ out=$(/other/python3", 1)


def mutate_tail_document(document, event, index):
    entry = document["hooks"][event][index]
    entry["command"] = entry["command"].replace('"permission":"deny"', '"permission":"allow"')


def mutate_tail_exit(document, event, index):
    document["hooks"][event][index]["command"] = document["hooks"][event][index]["command"].replace("exit 2", "exit 0")


def mutate_move(document, event, index):
    document["hooks"].setdefault("stop", []).append(document["hooks"][event].pop(index))


def mutate_unknown_key(document, event, index):
    document["hooks"][event][index]["unknown"] = 1


def mutate_duplicate(document, event, index):
    document["hooks"][event].append(json.loads(json.dumps(document["hooks"][event][index])))


# case name -> (mutation, applies to fail-closed only, expectation kind)
TAMPER_CASES = {
    "removed": (mutate_remove, False, "missing"),
    "matcher": (mutate_matcher, False, "changed"),
    "fail-closed-flipped": (mutate_fail_closed, False, "changed"),
    "timeout": (mutate_timeout, False, "changed"),
    "or-true-suffix": (mutate_or_true, False, "foreign"),
    "changed-argument": (mutate_argument, False, "argument"),
    "python-path": (mutate_python, False, "changed"),
    "tail-document": (mutate_tail_document, True, "changed"),
    "tail-exit": (mutate_tail_exit, True, "foreign"),
    "moved": (mutate_move, False, "moved"),
    "unknown-key": (mutate_unknown_key, False, "changed"),
    "duplicate": (mutate_duplicate, False, "duplicated"),
}
TAMPER_SHAPES = {
    "matcher-bearing-fail-closed": "require-code-review",
    "matcher-less-fail-closed": "classify",
    "informational": "nudge-worktree-anchor",
    "check-entry": "check",
}
# A tail-rewriting case needs a fail-closed entry: an informational tail already prints `{}` and exits 0, so it has no such case.
SHAPE_CASE_PAIRS = [
    pytest.param(shape, case, id=f"{shape}-{case}")
    for shape in TAMPER_SHAPES
    for case, (_, closed_only, _) in TAMPER_CASES.items()
    if not (closed_only and shape in ("informational", "check-entry"))
]


class TestStatusAndCanary:
    def _emitted(self, world: World):
        return world.adapter.generate_entries(python_path=sys.executable)

    def _live(self, emitted) -> dict:
        document = {"version": 1, "hooks": {}}
        for item in emitted:
            document["hooks"].setdefault(item.event, []).append(json.loads(json.dumps(item.entry)))
        return document

    def test_an_intact_generated_file_compares_clean(self, world: World) -> None:
        emitted = self._emitted(world)
        report = world.adapter.classify_hooks(emitted, self._live(emitted)["hooks"], tolerant=False)
        assert report.clean
        assert report.non_owned == []

    @pytest.mark.parametrize("case", list(TAMPER_CASES))
    def test_the_unit_layer_names_each_case_on_every_generated_entry_it_applies_to(self, world: World, case: str) -> None:
        mutate, closed_only, expectation = TAMPER_CASES[case]
        emitted = self._emitted(world)
        applied = 0
        for target in emitted:
            if closed_only and not target.fail_closed:
                continue
            document = self._live(emitted)
            event = target.event
            index = next(i for i, item in enumerate(document["hooks"][event]) if item == target.entry)
            before = json.dumps(document, sort_keys=True)
            mutate(document, event, index)
            assert json.dumps(document, sort_keys=True) != before, (case, target.label)
            report = world.adapter.classify_hooks(emitted, document["hooks"], tolerant=False)
            applied += 1
            assert not report.clean, (case, target.label)
            expected_kind = expectation
            if expectation == "argument":
                expected_kind = "foreign" if target.label == "check" else "changed"
            if expected_kind == "duplicated":
                assert any(label == target.label for _, _, label in report.duplicated), (case, target.label)
            elif expected_kind == "missing":
                assert (event, target.label) in report.missing, (case, target.label)
            elif expected_kind == "changed":
                assert any(label == target.label for _, _, label in report.changed), (case, target.label)
            elif expected_kind == "foreign":
                assert len(report.foreign) == 1 and (event, target.label) in report.missing, (case, target.label)
            elif expected_kind == "moved":
                assert (event, target.label) in report.missing, (case, target.label)
                assert any(label == target.label for _, _, label in report.extra_owned), (case, target.label)
        assert applied

    @pytest.mark.parametrize("surface", [surface for surface in SURFACES if surface != HOOK_ENV_SURFACE])
    @pytest.mark.parametrize(("shape", "case"), SHAPE_CASE_PAIRS)
    def test_status_and_check_name_each_case_on_one_entry_of_each_shape(
        self, fast_world: World, surface: str, shape: str, case: str
    ) -> None:
        world = fast_world
        mutate, _, expectation = TAMPER_CASES[case]
        label = TAMPER_SHAPES[shape]
        world.install()
        document = hooks_document(world)
        event, index = find_entry(world, document, label)
        mutate(document, event, index)
        save_hooks(world, document)

        result = run_surface(world, surface)

        assert result.status == (1 if surface == "check" else 0), (surface, shape, case)
        for line in expected_tamper_lines(case, expectation, label, event, index):
            assert f"finding: {line}" in result.out, (surface, shape, case, line)

    @pytest.mark.parametrize("case", [case for case in TAMPER_CASES])
    def test_the_hook_env_check_names_each_case_on_another_entry(self, fast_world: World, case: str) -> None:
        world = fast_world
        mutate, _, expectation = TAMPER_CASES[case]
        label = "require-code-review"
        world.install()
        document = hooks_document(world)
        event, index = find_entry(world, document, label)
        mutate(document, event, index)
        save_hooks(world, document)

        result = run_surface(world, HOOK_ENV_SURFACE)

        assert result.status == 0
        for line in expected_tamper_lines(case, expectation, label, event, index):
            assert f"finding: {line}" in result.out.splitlines(), (case, line)

    @pytest.mark.parametrize("surface", SURFACES)
    def test_an_intact_install_reports_no_tamper_finding_on_any_surface(self, fast_world: World, surface: str) -> None:
        world = fast_world
        world.install()

        result = run_surface(world, surface)

        assert result.status == 0, result.out
        for phrase in TAMPER_PHRASES:
            assert phrase not in result.out, (surface, phrase)
        assert "finding:" not in result.out

    @pytest.mark.parametrize("surface", SURFACES)
    def test_a_foreign_hostile_entry_and_a_hostile_matcher_are_absent_from_every_report_and_the_log(
        self, fast_world: World, surface: str
    ) -> None:
        world = fast_world
        nonce = "NONCE-" + "7f3a"
        world.install()
        document = hooks_document(world)
        document["hooks"].setdefault("stop", []).append({"command": f"{nonce} {world.adapter.stow_shim_path()} --x"})
        foreign_line = f"foreign entry that holds an adapter path: stop [{len(document['hooks']['stop']) - 1}]"
        event, index = find_entry(world, document, "require-code-review")
        document["hooks"][event][index]["matcher"] = f"{nonce}-matcher"
        save_hooks(world, document)
        shown_by_install_data = world.adapter.print_set(
            world.adapter.classify_hooks(
                world.adapter.generate_entries(python_path=sys.executable), document["hooks"], tolerant=True
            ),
            document["hooks"],
            {},
            replacing_pre_image=False,
            comparable=True,
        )

        report = run_surface(world, surface).out
        log = (world.state_dir / "shim.log").read_text() if (world.state_dir / "shim.log").exists() else ""

        assert any(nonce in line for line in shown_by_install_data)
        assert nonce not in report
        assert nonce not in log
        assert foreign_line in report

    def test_an_event_key_outside_the_vocabulary_is_counted_as_other_in_the_reports(self, world: World) -> None:
        assert world.adapter.safe_event_name("stop") == "stop"
        assert world.adapter.safe_event_name("\x1b[31mevil") == "other"
        assert world.adapter.safe_label("require-code-review", {"require-code-review"}) == "require-code-review"
        assert world.adapter.safe_label("\x1b[31m", {"require-code-review"}) == "other"

    @pytest.mark.parametrize("surface", SURFACES)
    def test_a_hostile_event_key_reaches_no_report_and_is_counted_as_other(self, fast_world: World, surface: str) -> None:
        world = fast_world
        world.install()
        document = hooks_document(world)
        hostile_key = HOSTILE_EVENT_KEY
        event, index = find_entry(world, document, "require-code-review")
        document["hooks"][hostile_key] = [{"command": "echo planted"}, dict(document["hooks"][event][index])]
        save_hooks(world, document)

        report = run_surface(world, surface).out

        assert "non-owned entries by event: other 1" in report
        assert "owned entry with no emitted counterpart: require-code-review on other" in report
        for fragment in HOSTILE_EVENT_KEY_FRAGMENTS:
            assert fragment not in report, (surface, fragment)

    @pytest.mark.parametrize("surface", SURFACES)
    def test_a_foreign_entry_under_a_hostile_event_key_is_named_by_other_and_position_without_the_key(
        self, fast_world: World, surface: str
    ) -> None:
        world = fast_world
        world.install()
        document = hooks_document(world)
        document["hooks"][HOSTILE_EVENT_KEY] = [{"command": f"{world.adapter.stow_shim_path()} --x"}]
        save_hooks(world, document)

        report = run_surface(world, surface).out

        assert "foreign entry that holds an adapter path: other [0]" in report
        for fragment in HOSTILE_EVENT_KEY_FRAGMENTS:
            assert fragment not in report, (surface, fragment)

    @pytest.mark.parametrize("surface", SURFACES)
    def test_an_unsaved_non_owned_entry_under_a_hostile_event_key_is_named_by_other_and_position_without_the_key(
        self, fast_world: World, surface: str
    ) -> None:
        world = fast_world
        world.hooks_path.write_bytes(ORIGINAL_HOOKS)
        world.install()
        document = hooks_document(world)
        document["hooks"] = {HOSTILE_EVENT_KEY: [{"command": "echo planted"}]}
        save_hooks(world, document)

        report = run_surface(world, surface).out

        assert "non-owned entry the pre-install file does not hold: other [0]" in report
        for fragment in HOSTILE_EVENT_KEY_FRAGMENTS:
            assert fragment not in report, (surface, fragment)

    @pytest.mark.parametrize("command", ["install", "uninstall"])
    def test_install_and_uninstall_print_a_hostile_event_key_escaped_and_cut_with_its_full_length(
        self, fast_world: World, command: str
    ) -> None:
        world = fast_world
        world.install()
        document = hooks_document(world)
        hostile_key = HOSTILE_EVENT_KEY
        event, index = find_entry(world, document, "require-code-review")
        document["hooks"][hostile_key] = [dict(document["hooks"][event][index])]
        save_hooks(world, document)

        result = world.install() if command == "install" else world.run("uninstall")

        assert result.status == 0, result.err
        for fragment in ("\x1b", "\x85", "‮", "\nHOSTILEKEY"):
            assert fragment not in result.out
        assert "\\x1b" in result.out
        assert "\\u202e" in result.out
        assert "characters in all]" in result.out
        assert max(len(line) for line in result.out.splitlines()) < 2000

    def _write_log(self, world: World, fields_list: list[dict]) -> None:
        shim = world.adapter.load_shim()
        world.state_dir.mkdir(exist_ok=True)
        (world.state_dir / "shim.log").write_bytes(b"".join(shim.format_log_line(fields) for fields in fields_list))

    def test_status_counts_pin_check_denies_by_cause_and_a_quiet_path_counts_nothing(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        self._write_log(
            world,
            [
                {"reg": "pin-check", "out": "ok"},
                {"reg": "pin-check", "out": "pin-check"},
                {"reg": "pin-check", "out": "pin-check"},
            ],
        )
        assert "pin-check denies: 2" in world.run("status").out
        self._write_log(world, [{"reg": "pin-check", "out": "pin-check-ok"}])
        assert "pin-check denies" not in world.run("status").out

    def test_status_counts_stale_install_calls_and_shows_a_shim_fault_class_beside_its_python(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        self._write_log(
            world,
            [
                {"reg": "x", "out": "stale-install"},
                {"reg": "x", "out": "shim-fault", "fault": "RuntimeError", "py": "3.14"},
            ],
        )

        out = world.run("status").out

        assert "stale-install calls: 1" in out
        assert "shim fault: class RuntimeError on python 3.14" in out

    def test_status_names_the_overflow_token_as_an_anomaly_not_a_registration(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        self._write_log(world, [{"reg": "x" * 200, "out": "ok"}])
        out = world.run("status").out
        assert "anomaly:" in out
        assert "registration overflow" not in out

    @pytest.mark.parametrize(
        ("lines", "silent"),
        [
            (
                [{"reg": "check-hook-env"}, {"reg": "a", "py": "3.14"}, {"reg": "check-hook-env"}, {"reg": "b", "py": "3.14"}],
                False,
            ),
            ([{"reg": "check-hook-env"}, {"reg": "a", "py": "3.14"}, {"reg": "b", "py": "3.14"}], True),
            ([{"reg": "a", "py": "3.14"}, {"reg": "check-hook-env"}, {"reg": "b", "py": "3.14"}], False),
            ([{"reg": "a", "py": "3.14"}, {"reg": "b", "py": "3.14"}], True),
        ],
        ids=["interleaved-quiet", "two-first-lines-after-the-last-check", "one-first-line-after-a-check", "two-with-no-check"],
    )
    def test_status_flags_a_silent_session_start_entry_only_after_two_shim_first_lines_follow_its_last_check(
        self, fast_world: World, lines: list[dict], silent: bool
    ) -> None:
        world = fast_world
        world.install()
        self._write_log(world, lines)
        assert ("looks silent" in world.run("status").out) is silent

    def test_status_flags_a_log_past_the_rotation_threshold(self, fast_world: World, monkeypatch: pytest.MonkeyPatch) -> None:
        world = fast_world
        world.install()
        self._write_log(world, [{"reg": "a", "out": "ok"}])
        monkeypatch.setattr(world.adapter, "LOG_ROTATE_BYTES", 1)
        assert "past the rotation threshold" in world.run("status").out

    def test_a_hook_env_check_with_no_payload_makes_exactly_one_log_line(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        world.run("check", "--hook-env")
        assert [f["reg"] for f in world.adapter.read_log(str(world.state_dir)).lines] == ["check-hook-env"]


# --- what `status` reads from the log ---


def write_raw_log(world: World, data: bytes) -> None:
    world.state_dir.mkdir(exist_ok=True)
    (world.state_dir / "shim.log").write_bytes(data)


class TestStatusLogReading:
    def test_a_value_outside_the_log_grammar_or_the_known_labels_is_counted_as_other_and_never_printed(
        self, fast_world: World
    ) -> None:
        world = fast_world
        world.install()
        write_raw_log(
            world,
            b"ts=1 reg=\x1b]52;c;QUJD out=ok\nts=2 reg=planted-label out=ok\n"
            b"ts=3 reg=check-hook-env out=shim-fault fault=\x1b[31m py=\x9b\n",
        )

        out = world.run("status").out

        assert "\x1b" not in out
        assert "planted-label" not in out
        assert "registration other: 2 call(s)" in out
        assert "shim fault: class other on python other: 1 call(s)" in out

    def test_a_log_of_a_hundred_thousand_distinct_labels_is_read_under_the_caps_and_prints_one_other_line(
        self, fast_world: World
    ) -> None:
        world = fast_world
        world.install()
        limit = world.adapter.LOG_STATUS_MAX_LINES
        write_raw_log(world, b"".join(f"reg=label{index} out=ok\n".encode() for index in range(2 * limit)))

        out = world.run("status").out

        assert f"registration other: {limit} call(s)" in out
        assert "the log is larger than `status` reads" in out
        assert len(out.splitlines()) < 40

    def test_a_log_file_past_the_byte_cap_is_read_from_its_end(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        padding = b"reg=padding out=ok\n" * (world.adapter.LOG_STATUS_READ_BYTES // 19 + 10)
        write_raw_log(world, padding + b"reg=check-hook-env out=ok\n")

        lines, cut = world.adapter.read_log_tail(str(world.state_dir / "shim.log"))

        assert cut is True
        assert lines[-1] == b"reg=check-hook-env out=ok"
        assert sum(len(line) + 1 for line in lines) <= world.adapter.LOG_STATUS_READ_BYTES

    def test_faults_are_aggregated_by_class_and_python_with_a_count_and_capped_with_a_count_of_the_rest(
        self, fast_world: World
    ) -> None:
        world = fast_world
        world.install()
        shim = world.adapter.load_shim()
        kinds = world.adapter.PRINT_SET_MAX_ENTRIES + 3
        lines = [{"reg": "x", "out": "shim-fault", "fault": f"Fault{index}", "py": "3.14"} for index in range(kinds)]
        lines += [{"reg": "x", "out": "shim-fault", "fault": "Fault0", "py": "3.14"}] * 4
        write_raw_log(world, b"".join(shim.format_log_line(fields) for fields in lines))

        out = world.run("status").out

        assert "shim fault: class Fault0 on python 3.14: 5 call(s)" in out
        assert out.count("shim fault: class ") == world.adapter.PRINT_SET_MAX_ENTRIES
        assert "3 more shim fault kind(s) not shown" in out


# --- the exit status of status ---


class TestStatusExitStatus:
    def test_status_exits_1_without_a_record_or_with_an_unloadable_shim_and_0_with_findings(self, fast_world: World) -> None:
        world = fast_world
        assert world.run("status").status == 1
        world.install()
        document = hooks_document(world)
        del document["hooks"]["preToolUse"][0]
        save_hooks(world, document)
        with_findings = world.run("status")
        assert with_findings.status == 0
        assert "finding: " in with_findings.out
        load_adapter_copy_with(world, "hook-shim.py", "def broken(:\n")
        unloadable = world.run("status")
        assert unloadable.status == 1
        assert "hook-shim.py did not load" in unloadable.out


# --- status's canary for a Shell-selecting registration that never fires ---


# The preToolUse registrations that select Shell: the `Bash`-matcher gates of settings.json, written here so the function under
# test is not its own oracle, plus the shim's `pin` and `classify` entries.
LITERAL_SHELL_SELECTING_LABELS = (
    "block-gh-pr-merge",
    "check-claude-md-length",
    "check-skill-length",
    "classify",
    "deny-credential-bash-reads",
    "deny-default-branch-push",
    "deny-escaped-backticks-in-pr-body",
    "deny-invisible-commit-content",
    "deny-network-installs",
    "deny-pii-in-commits",
    "deny-private-project-refs",
    "deny-repo-relocation",
    "deny-reviewer-tree-mutation",
    "enforce-marker-script-shape",
    "guard-settings-session-keys",
    "pin",
    "require-code-review",
    "require-diff-size-prompt",
    "require-ready-for-review",
    "require-respond-pr",
    "require-stow-reminder",
    "require-worktree-for-git-writes",
)


class TestShellCanaryFlag:
    FLAG_PREFIX = "flag: the Shell-selecting registration "

    def _silent_shell_selecting_labels(self, world: World) -> set[str]:
        emitted = world.adapter.generate_entries(python_path=sys.executable)
        return set(world.adapter.shell_selecting_registrations_without_a_fire(emitted, {}))

    def test_a_gate_whose_settings_matcher_is_bash_is_named_as_selecting_shell(self, fast_world: World) -> None:
        assert "deny-network-installs" in self._silent_shell_selecting_labels(fast_world)

    @pytest.mark.parametrize("label", ["ask-review-permissions", "deny-env-reads"])
    def test_a_gate_whose_settings_matcher_names_no_shell_tool_is_never_named(self, fast_world: World, label: str) -> None:
        assert label not in self._silent_shell_selecting_labels(fast_world)

    def test_the_named_registrations_are_exactly_the_literal_shell_selecting_ones(self, fast_world: World) -> None:
        assert self._silent_shell_selecting_labels(fast_world) == set(LITERAL_SHELL_SELECTING_LABELS)

    def _selecting_labels(self, world: World) -> list[str]:
        emitted = world.adapter.generate_entries(python_path=sys.executable)
        labels = sorted(set(world.adapter.shell_selecting_registrations_without_a_fire(emitted, {})))
        assert "pin" in labels
        assert "check" not in labels
        assert "pin-check" not in labels
        return labels

    def _flagged(self, world: World) -> list[str]:
        out = world.run("status").out
        matches = (re.match(rf"{self.FLAG_PREFIX}(\S+) has not fired", line) for line in out.splitlines())
        return [match.group(1) for match in matches if match]

    def _log_shell_calls(self, world: World, labels: list[str]) -> None:
        write_raw_log(world, "".join(f"reg={label} tool=Shell out=ok\n" for label in labels).encode())

    def test_one_silent_shell_selecting_registration_among_firing_ones_is_the_only_one_flagged(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        labels = self._selecting_labels(world)
        silent = labels[0]
        self._log_shell_calls(world, [label for label in labels if label != silent])

        assert sorted(set(self._flagged(world))) == [silent]

    def test_no_flag_when_only_the_literal_shell_selecting_registrations_fired(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        self._log_shell_calls(world, list(LITERAL_SHELL_SELECTING_LABELS))

        assert self._flagged(world) == []

    def test_no_flag_when_every_shell_selecting_registration_has_fired(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        self._log_shell_calls(world, self._selecting_labels(world))

        assert self._flagged(world) == []

    def test_no_flag_when_no_log_line_names_the_shell_tool(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        write_raw_log(world, b"reg=require-code-review tool=Write out=ok\n")

        assert self._flagged(world) == []

    def test_the_check_entry_and_the_pin_check_entry_are_never_named_when_every_selecting_registration_is_silent(
        self, fast_world: World
    ) -> None:
        world = fast_world
        world.install()
        labels = self._selecting_labels(world)
        write_raw_log(world, b"reg=check-hook-env tool=Shell out=ok\n")

        flagged = self._flagged(world)

        assert sorted(set(flagged)) == labels
        assert "check" not in flagged
        assert "pin-check" not in flagged
