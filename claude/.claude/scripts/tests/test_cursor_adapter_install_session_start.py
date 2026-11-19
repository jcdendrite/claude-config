"""cursor-adapter.py's sessionStart entry: its log, its host-walk and cursor_version reports, and the state it writes."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from helpers import CLAUDE_DIR

from . import _cursor_install_helpers as install_helpers
from ._cursor_install_helpers import (
    HOOK_ENV_SURFACE,
    Result,
    World,
    replace_tool,
    run_surface,
)

sys.path.insert(0, str(CLAUDE_DIR))
from hooks.tests._cursor_identity_fixtures import build_fake_process_tree, install_ps_stub  # noqa: E402
from hooks.tests._cursor_payloads import (  # noqa: E402 (the import needs the sys.path entry above)
    ADAPTER_SOURCE,
    CONVERSATION_ID,
    load_shim,
)

# The fixtures live once in the helper module. An import would trip F811 on every test parameter that names one.
world = install_helpers.world
fast_world = install_helpers.fast_world


# --- the log's field table ---


class TestLogFieldTable:
    def test_the_check_entrys_line_fits_its_field_and_the_line_stays_under_the_cap(self, fast_world: World) -> None:
        world = fast_world
        shim = world.adapter.load_shim()
        world.install()
        world.run("check", "--hook-env")

        raw = (world.state_dir / "shim.log").read_bytes()
        fields = world.adapter.read_log(str(world.state_dir)).lines[-1]

        assert fields["runmode"] == "unreadable+acceptance-only"
        assert len(fields["runmode"]) <= dict(shim.LOG_FIELDS)["runmode"]
        assert max(len(line) + 1 for line in raw.split(b"\n") if line) < shim.LOG_LINE_MAX_BYTES

    @pytest.mark.parametrize("case", ["cursor-main", "no-host", "claude-nearer-than-cli"])
    def test_the_host_walk_field_holds_each_token_within_its_maximum_and_the_line_stays_under_the_cap(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch, case: str
    ) -> None:
        world = fast_world
        tree_names, token = HOST_WALK_CASES[case]
        put_fake_process_tree_behind_ps(world, tree_names, monkeypatch)
        world.install()

        run_surface(world, HOOK_ENV_SURFACE)

        shim = world.adapter.load_shim()
        fields = world.adapter.read_log(str(world.state_dir)).lines[-1]
        assert fields["hostwalk"] == token
        assert len(fields["hostwalk"]) <= dict(shim.LOG_FIELDS)["hostwalk"]
        assert all(len(line) + 1 < shim.LOG_LINE_MAX_BYTES for line in (world.state_dir / "shim.log").read_bytes().split(b"\n"))

    @pytest.mark.parametrize(
        ("version", "expected"),
        [("v" * 64, "v" * 64), ("not a version", "other")],
        ids=["64-characters", "other"],
    )
    def test_the_cursor_version_field_holds_a_reported_change_within_its_maximum_and_the_line_stays_under_the_cap(
        self, fast_world: World, version: str, expected: str
    ) -> None:
        world = fast_world
        world.install()
        for sent in ("1.0.0", version):
            payload = {"hook_event_name": "sessionStart", "conversation_id": CONVERSATION_ID, "cursor_version": sent}
            run_hook_env_process(world, payload)

        shim = world.adapter.load_shim()
        fields = world.adapter.read_log(str(world.state_dir)).lines[-1]
        assert fields["cursorver"] == expected
        assert len(fields["cursorver"]) <= dict(shim.LOG_FIELDS)["cursorver"]
        assert all(len(line) + 1 < shim.LOG_LINE_MAX_BYTES for line in (world.state_dir / "shim.log").read_bytes().split(b"\n"))

    def test_the_overflow_token_holds_only_characters_the_log_grammar_allows(self, world: World) -> None:
        import re

        shim = world.adapter.load_shim()
        assert re.fullmatch(shim.LOG_VALUE_PATTERN, shim.LOG_OVERFLOW_TOKEN)

    def test_the_writer_keeps_a_line_with_every_field_at_its_maximum_under_the_cap(self, world: World) -> None:
        shim = world.adapter.load_shim()
        fields = {name: "a" * maximum for name, maximum in shim.LOG_FIELDS}

        line = shim.format_log_line(fields)

        assert len(line) < shim.LOG_LINE_MAX_BYTES
        assert line.endswith(b"\n")
        assert all(f"{name}={'a' * maximum}".encode() in line for name, maximum in shim.LOG_FIELDS)

    def test_both_run_mode_labels_fit_the_field(self, world: World) -> None:
        shim = world.adapter.load_shim()
        maximum = dict(shim.LOG_FIELDS)["runmode"]
        assert len(world.adapter.RUN_MODE_LABEL) <= maximum
        assert len(world.adapter.RUN_MODE_ACCEPTANCE_LABEL) <= maximum


# --- the sessionStart entry's host-walk and cursor_version reports ---

SESSION_START_DECLINE_FRAGMENT = "untranslated Cursor payload declined"
# Fake ancestors, outermost first, and the token each gives. The nearest one is the process that starts the walk's bash.
HOST_WALK_CASES = {
    "cursor-main": (("Cursor", "shell"), "found"),
    "cli-host-alone": (("agent", "shell"), "found"),
    "cli-nearer-than-claude": (("claude", "agent"), "found"),
    "claude-nearer-than-cli": (("agent", "claude"), "claude-first"),
    "no-host": (("shell",), "not-found"),
}


# A project choice: long enough that a loaded parallel run still finishes a walk through bash-script `ps` stubs.
STUBBED_WALK_BOUND_SECONDS = 30


def put_fake_process_tree_behind_ps(world: World, tree_names: tuple[str, ...], monkeypatch: pytest.MonkeyPatch) -> Path:
    """Replaces the world's `ps` with a stub that answers from a fake tree whose nearest ancestor is this process.

    Returns the stub's directory, which a hook run by hand puts first on its PATH."""
    monkeypatch.setattr(world.adapter, "HOST_WALK_BOUND_SECONDS", STUBBED_WALK_BOUND_SECONDS)
    stub = install_ps_stub(world.tmp_path, build_fake_process_tree(tree_names))
    stub_directory = stub.log_path.parent
    replace_tool(world, "ps", f"exec '{stub_directory / 'ps'}' \"$@\"\n")
    return stub_directory


def run_capture_session_id_under_fake_tree(
    world: World, stub_directory: Path, *, claudecode: bool = False
) -> subprocess.CompletedProcess:
    payload = {"hook_event_name": "SessionStart", "session_id": CONVERSATION_ID, "workspace_roots": [str(world.tmp_path)]}
    return subprocess.run(
        ["bash", str(CLAUDE_DIR / "hooks" / "capture-session-id.sh")],
        input=json.dumps(payload).encode(),
        capture_output=True,
        env={
            "HOME": str(world.home),
            "PATH": f"{stub_directory}:{world.bin_dir}:/usr/bin:/bin",
            "CLAUDE_CONFIG_DIR": str(world.config_dir),
            **({"CLAUDECODE": "1"} if claudecode else {}),
        },
        check=False,
        timeout=60,
    )


def last_log_fields(world: World) -> dict:
    return world.adapter.read_log(str(world.state_dir)).lines[-1]


class TestSessionStartReports:
    @pytest.mark.parametrize("case", list(HOST_WALK_CASES))
    def test_the_real_walk_reports_the_expected_token_and_the_session_hooks_decline_by_claudecode_and_the_walk(
        self, fast_world: World, case: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        tree_names, token = HOST_WALK_CASES[case]
        stub_directory = put_fake_process_tree_behind_ps(world, tree_names, monkeypatch)
        world.install()

        sessions_directory = world.config_dir / "sessions"

        context = run_surface(world, HOOK_ENV_SURFACE).out
        declined_without_claudecode = (
            SESSION_START_DECLINE_FRAGMENT in run_capture_session_id_under_fake_tree(world, stub_directory).stderr.decode()
        )
        entries_after_unset_run = list(sessions_directory.glob("*"))
        declined_with_claudecode = (
            SESSION_START_DECLINE_FRAGMENT
            in run_capture_session_id_under_fake_tree(world, stub_directory, claudecode=True).stderr.decode()
        )
        entries_after_set_run = list(sessions_directory.glob("*"))

        assert last_log_fields(world)["hostwalk"] == token
        assert f"Host walk: {token}." in context
        assert declined_without_claudecode
        assert entries_after_unset_run == []
        assert declined_with_claudecode is (token == "found")
        assert bool(entries_after_set_run) is (token != "found")

    @pytest.mark.parametrize("case", list(HOST_WALK_CASES))
    def test_status_flags_only_a_not_found_walk(self, fast_world: World, case: str, monkeypatch: pytest.MonkeyPatch) -> None:
        world = fast_world
        tree_names, token = HOST_WALK_CASES[case]
        put_fake_process_tree_behind_ps(world, tree_names, monkeypatch)
        world.install()
        run_surface(world, HOOK_ENV_SURFACE)

        status = world.run("status").out

        assert ("host walk found no Cursor or CLI host" in status) is (token == "not-found")
        assert ('"A host walk that found no host" entry' in status) is (token == "not-found")

    def test_a_walk_whose_ps_fails_at_its_first_read_reports_not_found(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        monkeypatch.setattr(world.adapter, "HOST_WALK_BOUND_SECONDS", STUBBED_WALK_BOUND_SECONDS)
        replace_tool(world, "ps", "exit 1\n")
        world.install()

        run_surface(world, HOOK_ENV_SURFACE)

        assert last_log_fields(world)["hostwalk"] == "not-found"

    def test_a_walk_that_outlasts_its_bound_reports_not_found(self, fast_world: World, monkeypatch: pytest.MonkeyPatch) -> None:
        world = fast_world
        world.install()
        real_run = subprocess.run
        shim = world.adapter.load_shim()

        def stalled_walk(argv, **keywords):
            if shim.HOST_WALK_PROGRAM in argv:
                raise subprocess.TimeoutExpired(argv, keywords["timeout"])
            return real_run(argv, **keywords)

        monkeypatch.setattr(world.adapter.subprocess, "run", stalled_walk)

        run_surface(world, HOOK_ENV_SURFACE)

        assert last_log_fields(world)["hostwalk"] == "not-found"

    def _report(self, world: World, version: str | None) -> str:
        payload = {"hook_event_name": "sessionStart", "conversation_id": CONVERSATION_ID}
        if version is not None:
            payload["cursor_version"] = version
        return run_hook_env_process(world, payload)["additional_context"]

    def test_the_first_sight_of_a_version_records_it_and_stays_quiet_and_an_unchanged_one_stays_quiet(
        self, fast_world: World
    ) -> None:
        world = fast_world
        world.install()

        first = self._report(world, "1.2.3")
        unchanged = self._report(world, "1.2.3")

        for context in (first, unchanged):
            assert "Cursor version changed" not in context
        assert all("cursorver" not in fields for fields in world.adapter.read_log(str(world.state_dir)).lines)
        assert os.listdir(world.state_dir / "cursor-versions") == ["1.2.3"]

    def test_a_change_reports_once_and_then_stays_quiet(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        self._report(world, "1.2.3")

        changed = self._report(world, "1.2.4")
        again = self._report(world, "1.2.4")

        assert "Cursor version changed to 1.2.4." in changed
        assert "Cursor version changed" not in again
        reported = [fields.get("cursorver") for fields in world.adapter.read_log(str(world.state_dir)).lines]
        assert reported == [None, "1.2.4", None]

    @pytest.mark.parametrize("seam", ["listdir", "create"])
    def test_two_conversations_that_both_read_the_record_before_either_creates_report_the_change_once(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch, seam: str
    ) -> None:
        world = fast_world
        world.install()
        self._report(world, "1.2.3")
        shim = world.adapter.load_shim()
        state_dir = str(world.state_dir)
        claimed_token = "2.0.0"
        real_listdir = os.listdir
        real_open = os.open
        outcomes: dict[str, bool] = {}
        racing: list[str] = []

        def the_other_claimant_runs_whole(path):
            # The first claimant has read the record and not yet created its file.
            racing.append(path)
            outcomes["other"] = world.adapter.cursor_version_changed(shim, state_dir, claimed_token)

        def listdir_then_the_other_claimant_runs(path):
            names = real_listdir(path)
            if seam == "listdir" and not racing:
                the_other_claimant_runs_whole(path)
            return names

        def the_other_claimant_runs_before_the_create(path, *arguments, **keywords):
            if seam == "create" and not racing and isinstance(path, str) and os.path.basename(path) == claimed_token:
                the_other_claimant_runs_whole(path)
            return real_open(path, *arguments, **keywords)

        monkeypatch.setattr(world.adapter.os, "listdir", listdir_then_the_other_claimant_runs)
        monkeypatch.setattr(world.adapter.os, "open", the_other_claimant_runs_before_the_create)

        outcomes["first"] = world.adapter.cursor_version_changed(shim, state_dir, claimed_token)

        assert racing, "the first claimant never reached the seam"
        # The loser's create finds the winner's file, which is the only path that returns False.
        assert outcomes == {"other": True, "first": False}

    def test_a_record_that_cannot_be_read_reports_a_change(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        (world.state_dir / "cursor-versions").write_text("not a directory")

        context = self._report(world, "1.2.3")

        assert "Cursor version changed to 1.2.3." in context

    @pytest.mark.parametrize(
        "version",
        ["bad\nversion", "1.2.3\n", "x" * 65, "x" * 64 * 1024, "has space", "", ".", ".."],
        ids=["newline", "trailing-newline", "65-characters", "64-KiB", "space", "empty", "dot", "dotdot"],
    )
    def test_a_version_outside_the_grammar_reaches_no_report_and_is_stored_as_other(
        self, fast_world: World, version: str
    ) -> None:
        world = fast_world
        world.install()
        self._report(world, "1.2.3")

        context = self._report(world, version)

        assert "Cursor version changed to other." in context
        assert len(version) < 3 or version not in context
        assert sorted(os.listdir(world.state_dir / "cursor-versions")) == ["1.2.3", "other"]
        assert last_log_fields(world)["cursorver"] == "other"

    def test_a_payload_with_no_version_or_a_non_string_one_reports_no_version_change(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        self._report(world, "1.2.3")

        absent = self._report(world, None)
        numeric = run_hook_env_process(
            world, {"hook_event_name": "sessionStart", "conversation_id": CONVERSATION_ID, "cursor_version": 7}
        )["additional_context"]

        for context in (absent, numeric):
            assert "Cursor version changed" not in context
        assert os.listdir(world.state_dir / "cursor-versions") == ["1.2.3"]


# --- rotation and the sweep ---


class TestLogRotationAndSweep:
    def _log(self, world: World, size: int) -> Path:
        world.state_dir.mkdir(exist_ok=True)
        log = world.state_dir / "shim.log"
        log.write_bytes(b"x" * size)
        return log

    def test_a_log_past_the_threshold_is_renamed_to_the_one_older_file(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(world.adapter, "LOG_ROTATE_BYTES", 100)
        self._log(world, 101)

        assert world.adapter.rotate_log(str(world.state_dir)) is True

        assert not (world.state_dir / "shim.log").exists()
        assert (world.state_dir / "shim.log.1").read_bytes() == b"x" * 101

    def test_a_rotator_skips_while_another_holds_the_lock_and_the_older_file_keeps_its_content(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import fcntl

        monkeypatch.setattr(world.adapter, "LOG_ROTATE_BYTES", 100)
        log = self._log(world, 500)
        (world.state_dir / "shim.log.1").write_bytes(b"older")
        descriptor = os.open(world.state_dir / "rotate.lock", os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)

            assert world.adapter.rotate_log(str(world.state_dir)) is False
        finally:
            os.close(descriptor)

        assert log.read_bytes() == b"x" * 500
        assert (world.state_dir / "shim.log.1").read_bytes() == b"older"

    def test_a_small_already_rotated_log_is_left_alone_by_a_rotator_that_holds_the_lock(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(world.adapter, "LOG_ROTATE_BYTES", 100)
        self._log(world, 10)
        (world.state_dir / "shim.log.1").write_bytes(b"older")

        assert world.adapter.rotate_log(str(world.state_dir)) is False

        assert (world.state_dir / "shim.log.1").read_bytes() == b"older"
        assert (world.state_dir / "shim.log").read_bytes() == b"x" * 10

    def test_a_rotation_under_concurrent_appends_loses_no_line(self, world: World, monkeypatch: pytest.MonkeyPatch) -> None:
        shim = world.adapter.load_shim()
        monkeypatch.setattr(world.adapter, "LOG_ROTATE_BYTES", 2000)
        world.state_dir.mkdir()
        total = 400
        started = threading.Event()

        def append_lines() -> None:
            for number in range(total):
                shim.write_log_line(str(world.state_dir), {"ts": number, "reg": "writer", "out": "ok"})
                started.set()

        writer = threading.Thread(target=append_lines)
        writer.start()
        started.wait()
        rotated = False
        while writer.is_alive() and not rotated:
            rotated = world.adapter.rotate_log(str(world.state_dir))
        writer.join()
        # The writer's output exceeds the threshold, so a rotation that the loop missed still lands once it has finished.
        rotated = rotated or world.adapter.rotate_log(str(world.state_dir))
        assert rotated

        names = ("shim.log.1", "shim.log")
        counted = sum(
            len((world.state_dir / name).read_bytes().splitlines()) for name in names if (world.state_dir / name).exists()
        )
        assert counted == total

    def test_a_mark_just_over_seven_days_old_is_removed_one_just_under_is_kept_and_a_non_mark_stays(self, world: World) -> None:
        marks = world.state_dir / "first-call"
        marks.mkdir(parents=True)
        now = 1_800_000_000.0
        age = world.adapter.FIRST_CALL_MARK_MAX_AGE_SECONDS
        for name, mtime in (("old-session", now - age - 5), ("fresh-session", now - age + 5), ("not.a.mark", now - age - 5)):
            (marks / name).write_text("")
            os.utime(marks / name, (mtime, mtime))

        removed = world.adapter.sweep_first_call_marks(str(world.state_dir), now)

        assert removed == 1
        assert sorted(path.name for path in marks.iterdir()) == ["fresh-session", "not.a.mark"]


# --- the sessionStart check's writes ---


def directory_tree(root: Path) -> dict[str, tuple[bool, bytes | None, int]]:
    """Every entry under `root`: whether it is a link, its bytes when a regular file, and its modification time."""
    return {
        str(path): (
            path.is_symlink(),
            path.read_bytes() if path.is_file() and not path.is_symlink() else None,
            path.lstat().st_mtime_ns,
        )
        for path in sorted(root.rglob("*"))
    }


def run_hook_env_process(world: World, payload: dict | None) -> dict:
    """The real sessionStart command under `-I -S` with the payload on stdin, as the launch guard runs it."""
    completed = subprocess.run(
        [sys.executable, "-I", "-S", str(ADAPTER_SOURCE), "check", "--hook-env"],
        input=json.dumps(payload).encode() if payload is not None else b"",
        capture_output=True,
        env={"HOME": str(world.home), "PATH": str(world.bin_dir)},
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


SESSION_START_PAYLOAD = {"hook_event_name": "sessionStart", "conversation_id": CONVERSATION_ID}


class TestHookEnvStateDirectory:
    def _link_state_dir_to(self, world: World, target: Path) -> None:
        shutil.rmtree(world.state_dir)
        world.state_dir.symlink_to(target)

    def test_a_state_dir_that_is_a_link_gets_no_log_line_mark_sweep_or_rotation_through_it(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        world.install()
        target = world.tmp_path / "elsewhere"
        (target / "first-call").mkdir(parents=True)
        old_mark = target / "first-call" / CONVERSATION_ID
        old_mark.write_text("")
        os.utime(old_mark, (1, 1))
        (target / "shim.log").write_text("existing line\n")
        monkeypatch.setattr(world.adapter, "LOG_ROTATE_BYTES", 1)
        self._link_state_dir_to(world, target)
        before = directory_tree(target)

        result = run_surface(world, HOOK_ENV_SURFACE)

        assert directory_tree(target) == before
        assert not (target / "rotate.lock").exists()
        assert "is missing or not a real directory" in result.out

    def test_a_first_call_dir_that_is_a_link_is_neither_swept_nor_given_a_mark_and_the_log_line_still_lands(
        self, fast_world: World
    ) -> None:
        world = fast_world
        world.install()
        target = world.tmp_path / "elsewhere"
        target.mkdir()
        old_mark = target / CONVERSATION_ID
        old_mark.write_text("")
        os.utime(old_mark, (1, 1))
        (world.state_dir / "first-call").symlink_to(target)
        before = directory_tree(target)

        result = run_surface(world, HOOK_ENV_SURFACE)

        assert directory_tree(target) == before
        assert "first-call directory" in result.out
        assert [fields["reg"] for fields in world.adapter.read_log(str(world.state_dir)).lines] == ["check-hook-env"]

    def test_a_state_dir_that_is_a_link_gets_no_first_call_mark_through_it_when_the_entry_runs_with_a_payload(
        self, fast_world: World
    ) -> None:
        world = fast_world
        world.install()
        target = world.tmp_path / "elsewhere"
        (target / "first-call").mkdir(parents=True)
        self._link_state_dir_to(world, target)
        before = directory_tree(target)

        run_hook_env_process(world, SESSION_START_PAYLOAD)

        assert directory_tree(target) == before
        assert not (target / "first-call" / CONVERSATION_ID).exists()

    def test_a_first_call_dir_that_is_a_link_gets_no_first_call_mark_through_it_when_the_entry_runs_with_a_payload(
        self, fast_world: World
    ) -> None:
        world = fast_world
        world.install()
        target = world.tmp_path / "elsewhere"
        target.mkdir()
        (world.state_dir / "first-call").symlink_to(target)
        before = directory_tree(target)

        run_hook_env_process(world, SESSION_START_PAYLOAD)

        assert directory_tree(target) == before
        assert not (target / CONVERSATION_ID).exists()

    def test_real_directories_get_the_log_line_the_rotation_and_the_sweep(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        world.install()
        (world.state_dir / "first-call").mkdir()
        old_mark = world.state_dir / "first-call" / CONVERSATION_ID
        old_mark.write_text("")
        os.utime(old_mark, (1, 1))
        (world.state_dir / "shim.log").write_text("existing line\n")
        monkeypatch.setattr(world.adapter, "LOG_ROTATE_BYTES", 1)

        result = run_surface(world, HOOK_ENV_SURFACE)

        assert "is missing or not a real directory" not in result.out
        assert (world.state_dir / "shim.log.1").read_text() == "existing line\n"
        assert not old_mark.exists()
        assert [fields["reg"] for fields in world.adapter.read_log(str(world.state_dir)).lines] == ["check-hook-env"]

    def test_status_with_a_linked_state_dir_prints_one_line_for_it_and_reads_no_log(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        target = world.tmp_path / "elsewhere"
        target.mkdir()
        (target / "shim.log").write_bytes(world.adapter.load_shim().format_log_line({"reg": "planted", "out": "ok"}))
        self._link_state_dir_to(world, target)

        result = world.run("status")

        assert result.out.count("is missing or not a real directory") == 1
        assert "planted" not in result.out
        assert "registration " not in result.out


class TestHookEnvAlwaysAnswers:
    def _assert_one_line_document_naming(self, result: Result, *fragments: str) -> None:
        assert result.status == 0
        assert len(result.out.splitlines()) == 1
        context = json.loads(result.out)["additional_context"]
        for fragment in fragments:
            assert fragment in context

    def test_a_comparison_that_raises_an_unexpected_error_still_gives_status_zero_and_one_document_naming_the_class(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        world.install()

        def raising_comparison(record, hooks_path, generation_failure_fix):
            raise RuntimeError("boom")

        monkeypatch.setattr(world.adapter, "compare_install", raising_comparison)

        result = world.run("check", "--hook-env")

        self._assert_one_line_document_naming(result, "check failed (RuntimeError)")

    def test_a_tool_re_test_that_raises_an_unexpected_error_still_answers_and_the_log_line_is_written(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        world.install()

        def raising_re_test(record, command_name):
            raise RuntimeError("boom")

        monkeypatch.setattr(world.adapter, "tool_findings", raising_re_test)

        result = world.run("check", "--hook-env")

        self._assert_one_line_document_naming(result, "The tool re-test failed (RuntimeError)")
        assert [fields["reg"] for fields in world.adapter.read_log(str(world.state_dir)).lines] == ["check-hook-env"]


class EntryKilled(BaseException):
    """A kill of the sessionStart entry at a chosen point. It is no Exception, because a killed process runs no handler."""


class TestFirstCallMark:
    """The sessionStart entry and the shim's first call race for one mark per conversation. These run the entry as a real
    process with a payload on stdin, and call the shim's claim in each order."""

    def _marks(self, world: World) -> list[str]:
        directory = world.state_dir / "first-call"
        return sorted(os.listdir(directory)) if directory.is_dir() else []

    def _log_regs(self, world: World) -> list[str]:
        return [fields["reg"] for fields in world.adapter.read_log(str(world.state_dir)).lines]

    def test_the_entry_first_makes_the_mark_after_its_one_log_line_and_the_shims_claim_then_loses(
        self, fast_world: World
    ) -> None:
        world = fast_world
        world.install()

        run_hook_env_process(world, SESSION_START_PAYLOAD)

        assert self._marks(world) == [CONVERSATION_ID]
        assert self._log_regs(world) == ["check-hook-env"]
        assert load_shim().claim_first_call(str(world.state_dir), CONVERSATION_ID) is False

    def test_the_shim_first_keeps_its_mark_and_the_entry_neither_replaces_it_nor_makes_a_second(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        assert load_shim().claim_first_call(str(world.state_dir), CONVERSATION_ID) is True
        mark = world.state_dir / "first-call" / CONVERSATION_ID
        inode = mark.stat().st_ino

        run_hook_env_process(world, SESSION_START_PAYLOAD)

        assert self._marks(world) == [CONVERSATION_ID]
        assert mark.stat().st_ino == inode
        assert self._log_regs(world) == ["check-hook-env"]

    def test_an_entry_killed_after_its_log_line_and_before_its_mark_leaves_the_log_line_no_mark_and_the_shims_claim_wins(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        world.install()
        shim = world.adapter.load_shim()
        real_claim_first_call = shim.claim_first_call

        def killed_before_the_mark(state_dir, session_id):
            raise EntryKilled

        monkeypatch.setattr(world.adapter, "hook_env_conversation_id", lambda shim_module, payload: CONVERSATION_ID)
        monkeypatch.setattr(shim, "claim_first_call", killed_before_the_mark)

        with contextlib.redirect_stdout(io.StringIO()), pytest.raises(EntryKilled):
            world.adapter.hook_env_check_body()

        assert self._log_regs(world) == ["check-hook-env"]
        assert self._marks(world) == []
        assert real_claim_first_call(str(world.state_dir), CONVERSATION_ID) is True

    def test_a_log_line_that_failed_to_append_leaves_no_mark(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        (world.state_dir / "shim.log").mkdir()

        document = run_hook_env_process(world, SESSION_START_PAYLOAD)

        assert "additional_context" in document
        assert self._marks(world) == []

    def test_a_run_with_no_payload_makes_a_log_line_and_no_mark(self, fast_world: World) -> None:
        world = fast_world
        world.install()

        run_hook_env_process(world, None)

        assert self._log_regs(world) == ["check-hook-env"]
        assert self._marks(world) == []


# --- a sessionStart entry whose payload stream never closes ---


def run_hook_env_with_stdin_held_open(world: World, seconds: int = 30) -> tuple[int, list[str], float]:
    """(exit status, stdout lines, seconds taken) of the real sessionStart command whose stdin never reaches EOF."""
    import time

    errors = world.tmp_path / "held-open-stderr"
    with errors.open("wb") as error_file:
        process = subprocess.Popen(
            [sys.executable, "-I", "-S", str(ADAPTER_SOURCE), "check", "--hook-env"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=error_file,
            env={"HOME": str(world.home), "PATH": str(world.bin_dir)},
        )
    outcome: dict[str, bytes] = {}
    started = time.monotonic()
    reader = threading.Thread(target=lambda: outcome.update(stdout=process.stdout.read()), daemon=True)
    reader.start()
    reader.join(seconds)
    try:
        assert not reader.is_alive(), "check --hook-env blocked on a payload stream that never closed"
        process.wait(timeout=seconds)
    finally:
        elapsed = time.monotonic() - started
        process.kill()
        process.wait()
        process.stdin.close()
        process.stdout.close()
    return process.returncode, outcome["stdout"].decode().splitlines(), elapsed


class TestHookEnvPayloadHeldOpen:
    def test_an_entry_whose_payload_stream_never_closes_still_answers_logs_itself_and_writes_no_first_call_mark(
        self, fast_world: World
    ) -> None:
        world = fast_world
        world.install()

        status, lines, _ = run_hook_env_with_stdin_held_open(world)

        assert status == 0
        assert len(lines) == 1
        assert json.loads(lines[0])["additional_context"].startswith("Cursor adapter check:")
        assert [fields["reg"] for fields in world.adapter.read_log(str(world.state_dir)).lines] == ["check-hook-env"]
        first_call_directory = world.state_dir / "first-call"
        assert not first_call_directory.exists() or not list(first_call_directory.iterdir())

    @pytest.mark.timing
    def test_an_entry_whose_payload_stream_never_closes_answers_inside_its_own_timeout(self, fast_world: World) -> None:
        world = fast_world
        world.install()

        _, _, elapsed = run_hook_env_with_stdin_held_open(world)

        assert elapsed < world.adapter.CHECK_ENTRY_TIMEOUT_SECONDS
