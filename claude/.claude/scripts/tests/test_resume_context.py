"""Tests for resume-context.sh.

Two documented env-var seams keep every test away from the real `claude`
binary and the real shared /tmp:
  RESUME_CONTEXT_LAUNCHER  command to exec instead of `claude`
  RESUME_CONTEXT_TMPDIR    temp-dir root instead of ${TMPDIR:-/tmp}
"""
from __future__ import annotations

import os
import stat
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

_SCRIPT = Path(__file__).parent.parent / "resume-context.sh"

_RECORDER_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$@" > "{recorder}"
"""

_CWD_RECORDER_STUB = """#!/usr/bin/env bash
pwd > "{cwd_recorder}"
printf '%s\\n' "$@" > "{recorder}"
"""


def _install_recorder(tmp_path: Path) -> tuple[Path, Path]:
    """Write a launcher stub that records its argv, return (stub_path, recorder_path)."""
    recorder = tmp_path / "recorder.txt"
    stub = tmp_path / "fake-launcher"
    stub.write_text(_RECORDER_STUB.format(recorder=recorder))
    stub.chmod(0o755)
    return stub, recorder


def _install_cwd_recorder(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Write a launcher stub that records both its argv and its cwd (via
    `pwd`, read at exec time — the only way to observe where --cwd actually
    landed the launched process). Returns (stub_path, recorder_path,
    cwd_recorder_path)."""
    recorder = tmp_path / "recorder.txt"
    cwd_recorder = tmp_path / "cwd-recorder.txt"
    stub = tmp_path / "fake-launcher"
    stub.write_text(_CWD_RECORDER_STUB.format(recorder=recorder, cwd_recorder=cwd_recorder))
    stub.chmod(0o755)
    return stub, recorder, cwd_recorder


def _run(
    args: list[str], env_extra: dict | None = None, cwd: Path | None = None
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        check=False,
    )


class TestLaunchMode:
    def test_zero_args_errors_without_side_effects(self, tmp_path: Path) -> None:
        stub, recorder = _install_recorder(tmp_path)
        result = _run([], {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)})
        assert result.returncode != 0
        assert result.stderr.strip()
        assert not recorder.exists()

    def test_missing_source_errors_without_side_effects(self, tmp_path: Path) -> None:
        stub, recorder = _install_recorder(tmp_path)
        missing = tmp_path / "does-not-exist-handoff.md"
        result = _run(
            [str(missing)],
            {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
        )
        assert result.returncode != 0
        assert result.stderr.strip()
        assert not recorder.exists()

    def test_missing_source_hint_points_at_overridden_tmpdir(self, tmp_path: Path) -> None:
        """The not-found branch must name the actual RESUME_CONTEXT_TMPDIR in
        use (not a hardcoded /tmp), and include the reboot caveat, so a human
        who re-runs resume-context on an already-consumed path is pointed at
        where the moved copy lives. It must also name the lookup script with
        the requested file's own basename, giving that human a second,
        cross-session recovery path alongside the ls -t glob above."""
        stub, _ = _install_recorder(tmp_path)
        missing = tmp_path / "does-not-exist-handoff.md"
        result = _run(
            [str(missing)],
            {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
        )
        assert result.returncode != 0
        assert f"{tmp_path}/resume-context.*" in result.stderr
        assert "cleared on reboot" in result.stderr
        assert "unrecoverable" in result.stderr
        assert "find-consumed-continuity-file.sh does-not-exist-handoff.md" in result.stderr

    def test_missing_source_hint_channel_carries_no_raw_escape_byte(self, tmp_path: Path) -> None:
        """Channel-level guard, not a specific-stripped-string assertion: a
        raw OSC/CSI escape in the requested path must never reach stdout or
        stderr un-stripped in the not-found diagnosis -- this script's
        fresh-TTY launch path is a more dangerous injection surface than a
        chat pane."""
        stub, recorder = _install_recorder(tmp_path)
        missing = str(tmp_path / "evil") + "\x1b[31mFAKE\x1b[0m-handoff.md"
        result = _run(
            [missing],
            {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
        )
        assert result.returncode != 0
        assert "\x1b" not in result.stdout
        assert "\x1b" not in result.stderr
        assert not recorder.exists()

    def test_missing_source_hint_channel_preserves_bidi_override_as_accepted_residual(
        self, tmp_path: Path
    ) -> None:
        """Pins the accepted residual from docs/design-decisions.md §57: a
        bidi-override character in a requested (never-written) path is not
        stripped, since this branch echoes the raw argument before any file
        is read or needs to exist -- unlike the OSC/CSI strip above, this is
        not a stripping guarantee. A future change narrowing or widening
        this residual should show up as a visible diff here."""
        stub, recorder = _install_recorder(tmp_path)
        missing = str(tmp_path / "evil") + chr(0x202E) + "live-handoff.md" + chr(0x202C)
        result = _run(
            [missing],
            {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
        )
        assert result.returncode != 0
        assert chr(0x202E) in result.stderr
        assert chr(0x202C) in result.stderr
        assert not recorder.exists()

    def test_moved_announcement_channel_carries_no_raw_escape_byte(self, tmp_path: Path) -> None:
        """Channel-level guard: the launch-mode 'moved' announcement must
        never print a raw control byte from $SRC verbatim into the invoking
        terminal."""
        stub, recorder = _install_recorder(tmp_path)
        src = tmp_path / "evil\x1b[31mFAKE\x1b[0m-handoff.md"
        src.write_text("hello handoff\n")
        result = _run(
            [str(src)],
            {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
        )
        assert result.returncode == 0, result.stderr
        assert "\x1b" not in result.stdout
        assert "\x1b" not in result.stderr
        assert recorder.exists()

    def test_happy_path_moves_and_launches(self, tmp_path: Path) -> None:
        stub, recorder = _install_recorder(tmp_path)
        src = tmp_path / "foo-handoff.md"
        src.write_text("hello handoff\n")
        result = _run(
            [str(src)],
            {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
        )
        assert result.returncode == 0, result.stderr
        assert not src.exists()
        moved = [p for p in tmp_path.iterdir() if p.name.startswith("resume-context.")]
        assert len(moved) == 1
        assert recorder.exists()
        recorded_args = recorder.read_text().splitlines()
        assert recorded_args[0] == "--append-system-prompt-file"
        assert recorded_args[1] == str(moved[0])
        assert f"moved {src} -> {moved[0]}" in result.stderr
        assert f"reload with: claude --append-system-prompt-file {moved[0]}" in result.stderr
        assert result.stdout == "", "launch mode must not write the destination announcement to stdout"

    def test_launch_prompt_instructs_task_list_restoration(self, tmp_path: Path) -> None:
        """The launched first-turn prompt must point the resuming session at the
        handoff's §2.6 resume directive — without this, a resumed session finds
        no live task-list state and reconstructs work from the plan file instead
        of the prior session's captured state. Resumed sessions are typically
        non-TTY and expose no task-list tool (gated on an interactive TTY
        upstream), so the prompt must not hard-require the tool."""
        stub, recorder = _install_recorder(tmp_path)
        src = tmp_path / "foo-handoff.md"
        src.write_text("hello handoff\n")
        result = _run(
            [str(src)],
            {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
        )
        assert result.returncode == 0, result.stderr
        recorded_args = recorder.read_text().splitlines()
        prompt = recorded_args[2]
        assert (
            "If it contains a task-list resume directive, track its pending and "
            "in-progress items from the file (not from memory) as you resume — "
            "using your session's task-list tool if one is available, otherwise "
            "inline. A missing task-list tool is not a blocker."
            in prompt
        )

    def test_moved_file_is_owner_only_regardless_of_source_permissions(self, tmp_path: Path) -> None:
        """mv's same-filesystem rename(2) inherits the source's permissions, not
        mktemp's 0600 placeholder — the script must re-assert 0600 explicitly."""
        stub, _ = _install_recorder(tmp_path)
        src = tmp_path / "foo-handoff.md"
        src.write_text("hello handoff\n")
        src.chmod(0o664)  # simulate a permissive source (e.g. default umask on write)
        result = _run(
            [str(src)],
            {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
        )
        assert result.returncode == 0, result.stderr
        moved = [p for p in tmp_path.iterdir() if p.name.startswith("resume-context.")]
        assert len(moved) == 1
        mode = stat.S_IMODE(moved[0].stat().st_mode)
        assert mode == 0o600, f"expected 0600, got {oct(mode)}"

    def test_path_with_spaces_is_moved_and_launched(self, tmp_path: Path) -> None:
        stub, recorder = _install_recorder(tmp_path)
        src = tmp_path / "bar with space-handoff.md"
        src.write_text("hello\n")
        result = _run(
            [str(src)],
            {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
        )
        assert result.returncode == 0, result.stderr
        assert not src.exists()
        moved = [p for p in tmp_path.iterdir() if p.name.startswith("resume-context.")]
        assert len(moved) == 1
        recorded_args = recorder.read_text().splitlines()
        assert recorded_args[1] == str(moved[0])

    def test_launcher_not_found_errors_before_move(self, tmp_path: Path) -> None:
        src = tmp_path / "foo-handoff.md"
        src.write_text("hello\n")
        result = _run(
            [str(src)],
            {"RESUME_CONTEXT_LAUNCHER": "definitely-not-a-real-command-xyz", "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
        )
        assert result.returncode != 0
        assert result.stderr.strip()
        assert src.exists(), "source must not be moved when the launcher can't be resolved"


class TestCwdFlag:
    def test_cwd_flag_launches_from_target_directory(self, tmp_path: Path) -> None:
        stub, recorder, cwd_recorder = _install_cwd_recorder(tmp_path)
        target_dir = tmp_path / "worktree"
        target_dir.mkdir()
        src = tmp_path / "foo-handoff.md"
        src.write_text("hello handoff\n")
        result = _run(
            ["--cwd", str(target_dir), str(src)],
            {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
        )
        assert result.returncode == 0, result.stderr
        assert cwd_recorder.exists()
        assert os.path.samefile(cwd_recorder.read_text().strip(), target_dir)
        moved = [p for p in tmp_path.iterdir() if p.name.startswith("resume-context.")]
        assert len(moved) == 1
        recorded_args = recorder.read_text().splitlines()
        assert recorded_args[0] == "--append-system-prompt-file"
        assert recorded_args[1] == str(moved[0]), (
            "the launched process must still receive DEST's original absolute path — "
            "the cd into --cwd happens after DEST is resolved and must not affect it"
        )

    def test_cwd_flag_relative_path_resolves_against_invoker_cwd(self, tmp_path: Path) -> None:
        """`resume-context.sh:214-216` asserts DEST/SRC resolution is unaffected
        by the --cwd `cd` because both are already resolved before it runs — a
        relative --cwd argument, resolved against the invoker's original cwd
        (not TMPDIR_ROOT or any other directory), is the case that would catch
        a future reordering of that `cd` before DEST/SRC resolution."""
        stub, recorder, cwd_recorder = _install_cwd_recorder(tmp_path)
        target_dir = tmp_path / "worktree"
        target_dir.mkdir()
        src = tmp_path / "foo-handoff.md"
        src.write_text("hello handoff\n")
        result = _run(
            ["--cwd", "worktree", str(src)],
            {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
            cwd=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        assert os.path.samefile(cwd_recorder.read_text().strip(), target_dir)
        moved = [p for p in tmp_path.iterdir() if p.name.startswith("resume-context.")]
        assert len(moved) == 1
        recorded_args = recorder.read_text().splitlines()
        assert recorded_args[1] == str(moved[0])

    def test_cwd_flag_before_consume_only_is_rejected(self, tmp_path: Path) -> None:
        """--cwd only matters for launch mode; combined with --consume-only
        (which never launches) it would silently do nothing, so reject the
        combination explicitly rather than accept a flag that has no effect."""
        stub, recorder = _install_recorder(tmp_path)
        target_dir = tmp_path / "worktree"
        target_dir.mkdir()
        src = tmp_path / "foo-task.md"
        src.write_text("hello brief\n")
        result = _run(
            ["--cwd", str(target_dir), "--consume-only", str(src)],
            {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
        )
        assert result.returncode != 0
        assert result.stderr.strip()
        assert src.exists(), "source must not be moved when the flag combination is rejected"
        assert not recorder.exists()

    def test_consume_only_before_cwd_is_also_rejected(self, tmp_path: Path) -> None:
        """The flag loop must reject the combination regardless of the order
        the two flags are given in."""
        stub, recorder = _install_recorder(tmp_path)
        target_dir = tmp_path / "worktree"
        target_dir.mkdir()
        src = tmp_path / "foo-task.md"
        src.write_text("hello brief\n")
        result = _run(
            ["--consume-only", "--cwd", str(target_dir), str(src)],
            {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
        )
        assert result.returncode != 0
        assert result.stderr.strip()
        assert src.exists()
        assert not recorder.exists()

    def test_cwd_flag_rejects_a_file_target_before_any_move(self, tmp_path: Path) -> None:
        """`[ ! -d "$LAUNCH_CWD" ]` rejects a plain file the same way it
        rejects a missing path — pin this so a future validation change
        (e.g. `-d` swapped for `-e`) can't silently start accepting file
        targets and pass every other --cwd test."""
        stub, recorder = _install_recorder(tmp_path)
        file_target = tmp_path / "not-a-directory.txt"
        file_target.write_text("i am a file\n")
        src = tmp_path / "foo-handoff.md"
        src.write_text("hello handoff\n")
        result = _run(
            ["--cwd", str(file_target), str(src)],
            {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
        )
        assert result.returncode != 0
        assert "not a directory" in result.stderr
        assert src.exists(), "source must not be moved when --cwd fails validation"
        assert not recorder.exists()

    def test_cwd_flag_rejects_nonexistent_directory_before_any_move(self, tmp_path: Path) -> None:
        stub, recorder = _install_recorder(tmp_path)
        missing_dir = tmp_path / "does-not-exist"
        src = tmp_path / "foo-handoff.md"
        src.write_text("hello handoff\n")
        result = _run(
            ["--cwd", str(missing_dir), str(src)],
            {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
        )
        assert result.returncode != 0
        assert result.stderr.strip()
        assert src.exists(), "source must not be moved when --cwd fails validation"
        assert not recorder.exists()

    def test_cwd_flag_not_a_directory_channel_carries_no_raw_escape_byte(self, tmp_path: Path) -> None:
        """Channel-level guard: a hostile --cwd value containing a raw
        ANSI escape must never reach stdout or stderr un-stripped in the
        "not a directory" diagnosis."""
        stub, recorder = _install_recorder(tmp_path)
        hostile_cwd = str(tmp_path / "evil") + "\x1b[31mFAKE\x1b[0m"
        src = tmp_path / "foo-handoff.md"
        src.write_text("hello handoff\n")
        result = _run(
            ["--cwd", hostile_cwd, str(src)],
            {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
        )
        assert result.returncode != 0
        assert "\x1b" not in result.stdout
        assert "\x1b" not in result.stderr
        assert src.exists(), "source must not be moved when --cwd fails validation"
        assert not recorder.exists()

    def test_cwd_flag_not_a_directory_channel_preserves_bidi_override_as_accepted_residual(
        self, tmp_path: Path
    ) -> None:
        """Pins the accepted residual from docs/design-decisions.md §57: a
        bidi-override character in a hostile --cwd value is not stripped,
        since this branch echoes the raw argument before any file is read
        or needs to exist -- unlike the OSC/CSI strip above, this is not a
        stripping guarantee. A future change narrowing or widening this
        residual should show up as a visible diff here."""
        stub, recorder = _install_recorder(tmp_path)
        hostile_cwd = str(tmp_path / "evil") + chr(0x202E) + "FAKE" + chr(0x202C)
        src = tmp_path / "foo-handoff.md"
        src.write_text("hello handoff\n")
        result = _run(
            ["--cwd", hostile_cwd, str(src)],
            {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
        )
        assert result.returncode != 0
        assert chr(0x202E) in result.stderr
        assert chr(0x202C) in result.stderr
        assert src.exists(), "source must not be moved when --cwd fails validation"
        assert not recorder.exists()

    def test_cwd_flag_missing_value_errors_without_side_effects(self, tmp_path: Path) -> None:
        """`--cwd` as the last token, with no directory argument following it."""
        stub, recorder = _install_recorder(tmp_path)
        result = _run(
            ["--cwd"],
            {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
        )
        assert result.returncode != 0
        assert "--cwd requires a directory argument" in result.stderr
        assert not recorder.exists()

    def test_unrecognized_flag_errors_without_side_effects(self, tmp_path: Path) -> None:
        """An unrecognized `-*` token must hit the usage error, not fall
        through and get misparsed as SRC."""
        stub, recorder = _install_recorder(tmp_path)
        src = tmp_path / "foo-handoff.md"
        src.write_text("hello handoff\n")
        result = _run(
            ["--bogus-flag", str(src)],
            {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
        )
        assert result.returncode != 0
        assert result.stderr.strip()
        assert src.exists()
        assert not recorder.exists()

    def test_double_dash_terminator_allows_a_dash_prefixed_source_path(self, tmp_path: Path) -> None:
        """`--` stops flag parsing so a continuity file whose name happens to
        start with `-` is still accepted as SRC rather than rejected as an
        unrecognized flag. Uses a relative path so the argument string
        itself starts with `-` — an absolute path never would."""
        stub, recorder = _install_recorder(tmp_path)
        src = tmp_path / "-oddly-named-handoff.md"
        src.write_text("hello handoff\n")
        result = _run(
            ["--", "-oddly-named-handoff.md"],
            {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
            cwd=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        assert not src.exists()
        assert recorder.exists()


class TestConsumeOnlyMode:
    def test_happy_path_moves_without_launching(self, tmp_path: Path) -> None:
        stub, recorder = _install_recorder(tmp_path)
        src = tmp_path / "foo-task.md"
        src.write_text("hello brief\n")
        result = _run(
            ["--consume-only", str(src)],
            {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
        )
        assert result.returncode == 0, result.stderr
        assert not src.exists()
        moved = [p for p in tmp_path.iterdir() if p.name.startswith("resume-context.")]
        assert len(moved) == 1
        assert not recorder.exists(), "launcher must never be invoked in --consume-only mode"
        assert result.stdout.strip() == str(moved[0])
        assert result.stdout == str(moved[0]) + "\n", "stdout must be exactly the dest path, nothing else"

    def test_moved_filename_does_not_leak_source_slug(self, tmp_path: Path) -> None:
        """A CISO round found the temp destination previously embedded the
        source's own basename, leaking the slug via `ls` on a shared,
        world-traversable /tmp even though the file's 0600 mode blocks
        content reads. The destination filename must use a fixed,
        non-descriptive prefix instead."""
        stub, _ = _install_recorder(tmp_path)
        src = tmp_path / "sensitive-project-name-task.md"
        src.write_text("hello brief\n")
        result = _run(
            ["--consume-only", str(src)],
            {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
        )
        assert result.returncode == 0, result.stderr
        moved = [p for p in tmp_path.iterdir() if p.name.startswith("resume-context.")]
        assert len(moved) == 1
        assert "sensitive-project-name" not in moved[0].name

    def test_succeeds_with_no_resolvable_launcher_on_path(self, tmp_path: Path) -> None:
        """--consume-only never resolves a launcher (resume-context.sh:89 guards
        launcher resolution behind `[ "$CONSUME_ONLY" -eq 0 ]`), but every
        existing --consume-only test above happens to supply a working
        launcher stub anyway. This is the actual production condition when
        the PostToolUse hook invokes --consume-only in an environment where
        `claude` may not be on PATH — pin that consume-only mode is
        launcher-independent, so a future refactor that moves launcher
        resolution above the CONSUME_ONLY guard shows up as a failing test
        here instead of only breaking in production."""
        src = tmp_path / "foo-task.md"
        src.write_text("hello brief\n")
        result = _run(
            ["--consume-only", str(src)],
            {
                "RESUME_CONTEXT_LAUNCHER": "definitely-not-a-real-command-xyz",
                "RESUME_CONTEXT_TMPDIR": str(tmp_path),
            },
        )
        assert result.returncode == 0, result.stderr
        assert not src.exists()

    def test_double_consume_of_already_gone_source_errors_cleanly(self, tmp_path: Path) -> None:
        """Sequential double-invocation, not a concurrency/TOCTOU test: the two
        calls run one after the other, not at the same instant. This does not
        rely on concurrent resume attempts being rare — a single user running
        two sessions against the same $HOME makes a real race between the two
        trigger paths (an explicit resume-context launch and the PostToolUse
        hook's --consume-only call) a realistic scenario, not just a
        single-user/single-session edge case. What actually makes this safe
        is that the underlying `mv` is atomic: a true race degrades to
        exactly this same clean-failure case (second mover finds the source
        already gone), not a corrupted or partial state. Revisit with a true
        concurrent variant only if a future change replaces `mv` with a
        non-atomic multi-step move."""
        stub, recorder = _install_recorder(tmp_path)
        src = tmp_path / "foo-task.md"
        src.write_text("hello\n")
        first = _run(
            ["--consume-only", str(src)],
            {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
        )
        assert first.returncode == 0

        second = _run(
            ["--consume-only", str(src)],
            {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
        )
        assert second.returncode != 0
        assert second.stderr.strip()
        assert not recorder.exists()

    def test_symlink_source_is_rejected_without_touching_target(self, tmp_path: Path) -> None:
        """mv preserves a symlink's identity on a same-filesystem rename, and
        chmod (unlike mv) dereferences symlinks by default — chmodding a moved
        symlink would silently narrow permissions on whatever arbitrary file
        it points to. The script must reject a symlink source outright rather
        than moving-then-chmodding it."""
        stub, recorder = _install_recorder(tmp_path)
        target = tmp_path / "unrelated-target.txt"
        target.write_text("sensitive content\n")
        target.chmod(0o644)
        symlink_src = tmp_path / "planted-handoff.md"
        symlink_src.symlink_to(target)

        result = _run(
            ["--consume-only", str(symlink_src)],
            {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
        )
        assert result.returncode != 0
        assert result.stderr.strip()
        assert symlink_src.is_symlink(), "the symlink itself must be left in place"
        assert stat.S_IMODE(target.stat().st_mode) == 0o644, (
            "the symlink's target must not have its permissions altered"
        )
        assert not recorder.exists()


class TestLegacyConfigDirFallback:
    """A continuity file written before this account's handoff/brief recipes
    honored CLAUDE_CONFIG_DIR may still sit at the legacy $HOME/.claude
    location; resume-context.sh checks there before giving up. --consume-only
    is sufficient for all four cases below (no launcher resolution needed,
    per test_succeeds_with_no_resolvable_launcher_on_path above)."""

    def test_falls_back_to_legacy_location_when_only_there(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        config_dir = tmp_path / "profile-container"
        (home / ".claude" / "handoffs").mkdir(parents=True)
        legacy_src = home / ".claude" / "handoffs" / "foo-handoff.md"
        legacy_src.write_text("hello handoff\n")
        requested_src = config_dir / "handoffs" / "foo-handoff.md"

        result = _run(
            ["--consume-only", str(requested_src)],
            {
                "HOME": str(home),
                "CLAUDE_CONFIG_DIR": str(config_dir),
                "RESUME_CONTEXT_TMPDIR": str(tmp_path),
            },
        )
        assert result.returncode == 0, result.stderr
        assert "legacy location" in result.stderr
        assert not legacy_src.exists()
        moved = [p for p in tmp_path.iterdir() if p.name.startswith("resume-context.")]
        assert len(moved) == 1

    def test_legacy_location_hint_channel_carries_no_raw_escape_byte(self, tmp_path: Path) -> None:
        """Channel-level guard: the legacy-location fallback hint must
        never print a raw control byte from $LEGACY_SRC verbatim into the
        invoking terminal."""
        home = tmp_path / "home"
        config_dir = tmp_path / "profile-container"
        (home / ".claude" / "handoffs").mkdir(parents=True)
        legacy_src = home / ".claude" / "handoffs" / "evil\x1b[31mFAKE\x1b[0m-handoff.md"
        legacy_src.write_text("hello handoff\n")
        requested_src = config_dir / "handoffs" / legacy_src.name

        result = _run(
            ["--consume-only", str(requested_src)],
            {
                "HOME": str(home),
                "CLAUDE_CONFIG_DIR": str(config_dir),
                "RESUME_CONTEXT_TMPDIR": str(tmp_path),
            },
        )
        assert result.returncode == 0, result.stderr
        assert "legacy location" in result.stderr
        assert "\x1b" not in result.stdout
        assert "\x1b" not in result.stderr

    def test_trailing_slash_on_config_dir_does_not_disable_fallback(self, tmp_path: Path) -> None:
        """Regression test: CONFIG_DIR must strip a trailing slash before use
        in the case-pattern match, matching _lib_config_dir's ${VAR%/}
        convention (_lib.sh:114) — otherwise the pattern gains a doubled
        slash that the single-slash requested path never matches, and the
        fallback silently no-ops."""
        home = tmp_path / "home"
        config_dir = tmp_path / "profile-container"
        (home / ".claude" / "briefs").mkdir(parents=True)
        legacy_src = home / ".claude" / "briefs" / "foo-task.md"
        legacy_src.write_text("hello brief\n")
        requested_src = config_dir / "briefs" / "foo-task.md"

        result = _run(
            ["--consume-only", str(requested_src)],
            {
                "HOME": str(home),
                "CLAUDE_CONFIG_DIR": str(config_dir) + "/",
                "RESUME_CONTEXT_TMPDIR": str(tmp_path),
            },
        )
        assert result.returncode == 0, result.stderr
        assert "legacy location" in result.stderr
        assert not legacy_src.exists()

    def test_not_found_at_either_location_reports_standard_error(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        config_dir = tmp_path / "profile-container"
        home.mkdir()
        requested_src = config_dir / "handoffs" / "nowhere.md"

        result = _run(
            ["--consume-only", str(requested_src)],
            {
                "HOME": str(home),
                "CLAUDE_CONFIG_DIR": str(config_dir),
                "RESUME_CONTEXT_TMPDIR": str(tmp_path),
            },
        )
        assert result.returncode != 0
        assert "not found" in result.stderr
        assert "legacy location" not in result.stderr

    def test_no_op_when_config_dir_unset(self, tmp_path: Path, monkeypatch) -> None:
        """When CLAUDE_CONFIG_DIR resolves to the default $HOME/.claude, the
        fallback block's own guard (CONFIG_DIR != $HOME/.claude) skips it
        entirely — there is no separate "legacy" location to fall back to."""
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        home = tmp_path / "home"
        home.mkdir()
        requested_src = home / ".claude" / "handoffs" / "nowhere.md"

        result = _run(
            ["--consume-only", str(requested_src)],
            {"HOME": str(home), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
        )
        assert result.returncode != 0
        assert "not found" in result.stderr
        assert "legacy location" not in result.stderr


def _index_dir(tmpdir_root: Path) -> Path:
    return tmpdir_root / f"resume-context-index-{os.geteuid()}"


def _day_file(tmpdir_root: Path, when: datetime | None = None) -> Path:
    """Path to the day-file for `when` (default: today, UTC)."""
    day = (when or datetime.now(UTC)).strftime("%Y-%m-%d")
    return _index_dir(tmpdir_root) / f"consumed.{day}.tsv"


def _backdate(path: Path, days_old: float) -> None:
    """Set path's mtime `days_old` days in the past, for exercising the
    30-day whole-file retention sweep without waiting for real time to
    pass."""
    stamp = time.time() - (days_old * 86400)
    os.utime(path, (stamp, stamp))


# record_consumed_destination's timestamp field is always this length: `date
# -u '+%Y-%m-%dT%H:%M:%SZ'` produces a fixed-width ISO-8601 UTC stamp.
_STAMP_LEN = len("2026-01-01T00:00:00Z")
# mktemp's "resume-context.XXXXXX" template always substitutes exactly 6
# characters for the X's, so the destination basename length is fixed too.
_DEST_BASENAME_LEN = len("resume-context.") + 6


def _src_path_of_exact_length(tmpdir_root: Path, total_len: int) -> Path:
    """Builds an absolute source path with exactly total_len characters,
    using nested short directory components (each far under the 255-byte
    NAME_MAX) so the total can be tuned to an exact byte count without
    tripping ENAMETOOLONG -- the same nesting idiom the byte-cap tests above
    use to build an over-cap path, generalized to hit a precise length."""
    component_len = 100
    dir_path = tmpdir_root
    remaining = total_len - len(str(tmpdir_root))
    assert remaining > component_len + 1, "fixture base path too long for this helper's assumptions"
    while remaining > component_len + 1:
        dir_path = dir_path / ("a" * component_len)
        remaining -= component_len + 1
    dir_path.mkdir(parents=True, exist_ok=True)
    leaf_len = remaining - 1  # the "/" this final join adds
    return dir_path / ("a" * leaf_len)


class TestConsumedIndex:
    """Coverage for record_consumed_destination, the best-effort append run
    after a successful move and before the destination's mode-fixing chmod.
    Every row lands in today's UTC day-file, consumed.<YYYY-MM-DD>.tsv.
    """

    def test_consume_only_appends_one_row_naming_dest_and_source(self, tmp_path: Path) -> None:
        stub, _ = _install_recorder(tmp_path)
        src = tmp_path / "foo-task.md"
        src.write_text("hello brief\n")
        result = _run(
            ["--consume-only", str(src)],
            {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
        )
        assert result.returncode == 0, result.stderr
        dest = result.stdout.strip()
        rows = _day_file(tmp_path).read_text().splitlines()
        assert len(rows) == 1
        _stamp, row_dest, row_src = rows[0].split("\t")
        assert row_dest == dest
        assert row_src == str(src)

    def test_launch_mode_also_appends_a_row_and_still_execs_launcher(self, tmp_path: Path) -> None:
        stub, recorder = _install_recorder(tmp_path)
        src = tmp_path / "foo-handoff.md"
        src.write_text("hello handoff\n")
        result = _run(
            [str(src)],
            {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
        )
        assert result.returncode == 0, result.stderr
        assert recorder.exists()
        moved = [p for p in tmp_path.iterdir() if p.name.startswith("resume-context.")]
        assert len(moved) == 1
        rows = _day_file(tmp_path).read_text().splitlines()
        assert len(rows) == 1
        assert rows[0].split("\t")[1] == str(moved[0])

    def test_index_file_and_directory_modes_on_first_creation(self, tmp_path: Path) -> None:
        stub, _ = _install_recorder(tmp_path)
        src = tmp_path / "foo-task.md"
        src.write_text("hello brief\n")
        result = _run(
            ["--consume-only", str(src)],
            {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
        )
        assert result.returncode == 0, result.stderr
        day_file = _day_file(tmp_path)
        assert stat.S_IMODE(day_file.stat().st_mode) == 0o600
        assert stat.S_IMODE(day_file.parent.stat().st_mode) == 0o700

    def test_preexisting_loose_day_file_is_tightened_on_next_append(self, tmp_path: Path) -> None:
        stub, _ = _install_recorder(tmp_path)
        day_file = _day_file(tmp_path)
        day_file.parent.mkdir(parents=True)
        day_file.parent.chmod(0o700)
        day_file.write_text("2026-01-01T00:00:00Z\t/tmp/stale-dest\t/tmp/stale-src\n")
        day_file.chmod(0o644)
        src = tmp_path / "foo-task.md"
        src.write_text("hello brief\n")

        result = _run(
            ["--consume-only", str(src)],
            {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
        )

        assert result.returncode == 0, result.stderr
        assert stat.S_IMODE(day_file.stat().st_mode) == 0o600
        assert len(day_file.read_text().splitlines()) == 2

    def test_symlinked_index_directory_skips_append_but_consume_still_succeeds(self, tmp_path: Path) -> None:
        stub, _ = _install_recorder(tmp_path)
        real_dir = tmp_path / "elsewhere"
        real_dir.mkdir()
        _index_dir(tmp_path).symlink_to(real_dir)
        src = tmp_path / "foo-task.md"
        src.write_text("hello brief\n")

        result = _run(
            ["--consume-only", str(src)],
            {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip(), "consume-only stdout contract must be unaffected by a skipped index write"
        assert not src.exists()
        assert not any(real_dir.glob("consumed.*.tsv"))

    def test_symlinked_day_file_skips_append_but_consume_still_succeeds(self, tmp_path: Path) -> None:
        """Distinct from the directory-symlink case above: here the parent
        directory is legitimate and today's day-file itself is the
        symlink -- record_consumed_destination's own `[ -L "$day_file" ]`
        guard, not _lib_resume_context_index_dir's directory guard."""
        stub, _ = _install_recorder(tmp_path)
        day_file = _day_file(tmp_path)
        day_file.parent.mkdir(parents=True)
        day_file.parent.chmod(0o700)
        real_target = tmp_path / "elsewhere.tsv"
        real_target.write_text("not a real index\n")
        day_file.symlink_to(real_target)
        src = tmp_path / "foo-task.md"
        src.write_text("hello brief\n")

        result = _run(
            ["--consume-only", str(src)],
            {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip(), "consume-only stdout contract must be unaffected by a skipped index write"
        assert not src.exists()
        assert real_target.read_text() == "not a real index\n"

    def test_src_containing_newline_skips_append_but_consume_still_succeeds(self, tmp_path: Path) -> None:
        """resume-context.sh:130 -- an embedded newline in $src would forge
        an extra TSV row if written through; the guard must skip the append
        entirely rather than escaping or truncating it."""
        stub, _ = _install_recorder(tmp_path)
        src = tmp_path / "foo\ntask.md"
        src.write_bytes(b"hello brief\n")

        result = _run(
            ["--consume-only", str(src)],
            {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip(), "consume-only stdout contract must be unaffected by a skipped index write"
        assert not src.exists()
        assert not _day_file(tmp_path).exists()

    def test_row_exceeding_length_cap_skips_append_but_consume_still_succeeds(self, tmp_path: Path) -> None:
        """resume-context.sh's 2048-byte row cap: bash's `printf` builtin
        chunks a write into multiple
        write(2) calls past roughly 4096 bytes, which is no longer atomic
        under O_APPEND. A source path long enough to push the serialized
        row past the cap must skip the append -- fail closed, the same
        shape as the newline-forging guard above -- not fall back to a
        riskier write. Uses nested short directory components (each well
        under the 255-byte single-component limit) to build a long total
        path without tripping ENAMETOOLONG."""
        stub, _ = _install_recorder(tmp_path)
        deep = tmp_path
        for _ in range(15):
            deep = deep / ("a" * 150)
        deep.mkdir(parents=True)
        src = deep / "task.md"
        src.write_text("hello brief\n")

        result = _run(
            ["--consume-only", str(src)],
            {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip(), "consume-only stdout contract must be unaffected by a skipped index write"
        assert not src.exists()
        assert not _day_file(tmp_path).exists()

    def test_normal_length_row_still_appends(self, tmp_path: Path) -> None:
        """Regression guard against an off-by-one in the length-cap check
        above: an ordinary short src/dest pair must not be caught by it."""
        stub, _ = _install_recorder(tmp_path)
        src = tmp_path / "foo-task.md"
        src.write_text("hello brief\n")

        result = _run(
            ["--consume-only", str(src)],
            {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
        )

        assert result.returncode == 0, result.stderr
        rows = _day_file(tmp_path).read_text().splitlines()
        assert len(rows) == 1

    def test_multibyte_src_under_byte_cap_still_appends_one_row(self, tmp_path: Path) -> None:
        """A modest multi-byte source name must not be mistaken for an
        over-cap row -- regression guard alongside the byte-vs-character
        cap test below, which uses a much longer multi-byte path."""
        stub, _ = _install_recorder(tmp_path)
        src = tmp_path / "héllo-task.md"
        src.write_text("hello brief\n")

        result = _run(
            ["--consume-only", str(src)],
            {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
        )

        assert result.returncode == 0, result.stderr
        rows = _day_file(tmp_path).read_text().splitlines()
        assert len(rows) == 1
        assert rows[0].split("\t")[2] == str(src)

    def test_row_under_character_cap_but_over_byte_cap_skips_append(self, tmp_path: Path) -> None:
        """Byte-vs-character cap defect: `${#row}` counts characters under
        a multi-byte locale, so a
        non-ASCII $src can pass a 2048-character cap while the bytes
        actually written exceed it. Nine nested directory components of 78
        three-byte-each CJK characters (234 bytes/component, comfortably
        under the 255-byte NAME_MAX) push the row past 2048 bytes while its
        character count (roughly 700) stays far under 2048 -- the exact
        shape that would slip past a character-counting cap but must be
        caught by the byte-counting one."""
        stub, _ = _install_recorder(tmp_path)
        deep = tmp_path
        for _ in range(9):
            deep = deep / ("中" * 78)
        deep.mkdir(parents=True)
        src = deep / "task.md"
        src.write_text("hello brief\n")
        assert len(str(src)) < 2048, "fixture must stay under the character cap to isolate the byte-cap defect"

        result = _run(
            ["--consume-only", str(src)],
            {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip(), "consume-only stdout contract must be unaffected by a skipped index write"
        assert not src.exists()
        assert not _day_file(tmp_path).exists()

    def test_row_at_exact_2048_byte_cap_appends(self, tmp_path: Path) -> None:
        """Pins the exact boundary with the same rigor as
        test_day_file_at_exact_30_day_boundary_survives_the_sweep below: a
        row serialized to exactly 2048 bytes must still append -- `[
        "$row_bytes" -gt 2048 ]` is a strict inequality, so the cap itself is
        inclusive."""
        stub, _ = _install_recorder(tmp_path)
        dest_len = len(str(tmp_path)) + 1 + _DEST_BASENAME_LEN
        src_len = 2048 - _STAMP_LEN - 1 - dest_len - 1 - 1
        src = _src_path_of_exact_length(tmp_path, src_len)
        src.write_text("hello brief\n")

        result = _run(
            ["--consume-only", str(src)],
            {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
        )

        assert result.returncode == 0, result.stderr
        rows = _day_file(tmp_path).read_text().splitlines()
        assert len(rows) == 1
        row_bytes = len((rows[0] + "\n").encode())
        assert row_bytes == 2048, f"fixture must land exactly at the cap, got {row_bytes}"

    def test_row_at_2049_bytes_skips_append_but_consume_still_succeeds(self, tmp_path: Path) -> None:
        """One byte past the boundary pinned above must skip the append,
        confirming the cap's strict inequality cuts off immediately past
        2048, not somewhere looser."""
        stub, _ = _install_recorder(tmp_path)
        dest_len = len(str(tmp_path)) + 1 + _DEST_BASENAME_LEN
        src_len = 2049 - _STAMP_LEN - 1 - dest_len - 1 - 1
        src = _src_path_of_exact_length(tmp_path, src_len)
        src.write_text("hello brief\n")

        result = _run(
            ["--consume-only", str(src)],
            {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip(), "consume-only stdout contract must be unaffected by a skipped index write"
        assert not src.exists()
        assert not _day_file(tmp_path).exists()

    def test_stale_day_file_is_deleted_and_fresh_sibling_survives(self, tmp_path: Path) -> None:
        """30-day whole-file retention sweep, run after every append.
        Mirrors the existing sweep coverage at
        test_nudge_worktree_anchor.py's TestStateDirSweep and
        test_advance_past_commit_stall.py."""
        stub, _ = _install_recorder(tmp_path)
        index_dir = _index_dir(tmp_path)
        index_dir.mkdir(parents=True)
        index_dir.chmod(0o700)
        stale = index_dir / "consumed.2020-01-01.tsv"
        stale.write_text("2020-01-01T00:00:00Z\t/tmp/old-dest\t/tmp/old-src\n")
        _backdate(stale, days_old=31)
        fresh_sibling = index_dir / "consumed.2020-02-01.tsv"
        fresh_sibling.write_text("2020-02-01T00:00:00Z\t/tmp/recent-dest\t/tmp/recent-src\n")
        _backdate(fresh_sibling, days_old=29)
        src = tmp_path / "foo-task.md"
        src.write_text("hello brief\n")

        result = _run(
            ["--consume-only", str(src)],
            {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
        )

        assert result.returncode == 0, result.stderr
        assert not stale.exists(), "a day-file older than 30 days must be swept on the next append"
        assert fresh_sibling.exists(), "a day-file within 30 days must survive the sweep"
        assert _day_file(tmp_path).exists(), "today's day-file must never be swept"

    def test_day_file_at_exact_30_day_boundary_survives_the_sweep(self, tmp_path: Path) -> None:
        """`find -mtime +30` truncates age to whole 24-hour periods and
        matches only ages strictly greater than 30 of them, so a day-file
        backdated to exactly 30*86400 seconds is not yet in the ">30" bucket
        -- empirically confirmed (touch a file at exactly that offset, run
        `find -mtime +30`: it is not matched). Survival at this exact
        boundary is the intended contract this pins, not deletion; only a
        file older than a full 31st day (test above) is swept."""
        stub, _ = _install_recorder(tmp_path)
        index_dir = _index_dir(tmp_path)
        index_dir.mkdir(parents=True)
        index_dir.chmod(0o700)
        boundary = index_dir / "consumed.2020-01-02.tsv"
        boundary.write_text("2020-01-02T00:00:00Z\t/tmp/boundary-dest\t/tmp/boundary-src\n")
        _backdate(boundary, days_old=30)
        src = tmp_path / "foo-task.md"
        src.write_text("hello brief\n")

        result = _run(
            ["--consume-only", str(src)],
            {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
        )

        assert result.returncode == 0, result.stderr
        assert boundary.exists(), "a day-file at exactly the 30-day boundary must survive the sweep"

    def test_ten_sequential_consumes_produce_ten_well_formed_rows(self, tmp_path: Path) -> None:
        """Proves absence of corruption without concurrency -- not the
        no-lock design's actual claim, which the concurrent-writer test
        below covers. All ten land in one day-file, since they all run on
        the same UTC day."""
        stub, _ = _install_recorder(tmp_path)
        for i in range(10):
            src = tmp_path / f"foo{i}-task.md"
            src.write_text(f"hello {i}\n")
            result = _run(
                ["--consume-only", str(src)],
                {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
            )
            assert result.returncode == 0, result.stderr

        rows = _day_file(tmp_path).read_text().splitlines()
        assert len(rows) == 10
        for row in rows:
            assert len(row.split("\t")) == 3, f"malformed row: {row!r}"

    def test_concurrent_consumes_produce_no_interleaved_or_truncated_rows(self, tmp_path: Path) -> None:
        """The actual test of the no-lock design's central claim: launches
        several real --consume-only subprocesses concurrently against the
        same index, which the sequential test above cannot exercise at
        all, since sequential invocations never race on the shared append.
        Relies on Popen-launch scheduling jitter for genuine overlap rather
        than an explicit synchronization barrier -- a future flake
        investigation should not mistake incidental non-overlap for a
        passing invariant. One fixture source contains a non-ASCII path
        component, since an all-ASCII fixture set cannot exercise the
        character-vs-byte cap distinction."""
        stub, _ = _install_recorder(tmp_path)
        sources = []
        for i in range(8):
            src = tmp_path / f"concurrent{i}-task.md"
            src.write_text(f"hello {i}\n")
            sources.append(src)
        non_ascii_src = tmp_path / "héllo-wörld-task.md"
        non_ascii_src.write_text("hello non-ascii\n")
        sources.append(non_ascii_src)

        env = dict(os.environ)
        env.update({"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)})
        procs = [
            subprocess.Popen(
                [str(_SCRIPT), "--consume-only", str(src)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            for src in sources
        ]
        outputs = [proc.communicate() for proc in procs]
        for proc, (_stdout, stderr) in zip(procs, outputs, strict=True):
            assert proc.returncode == 0, stderr

        rows = _day_file(tmp_path).read_text().splitlines()
        assert len(rows) == len(sources)
        for row in rows:
            fields = row.split("\t")
            assert len(fields) == 3, f"malformed (truncated or merged) row: {row!r}"
            assert fields[1], f"row missing a destination field: {row!r}"
            assert fields[2], f"row missing a source field: {row!r}"

    def test_concurrent_appends_survive_a_racing_sweep_of_a_stale_sibling(self, tmp_path: Path) -> None:
        """Pre-seeds a day-file named for a past date, backdated 31+ days,
        then launches N=8 concurrent --consume-only subprocesses. Each
        one's own retention sweep can race the others' appends to today's
        file, but the sweep only ever unlinks the *stale* file (already
        day-boundary-separated from today's), never the live one an append
        targets. A count-only assertion can't tell N good rows from N-1
        good rows plus one corrupted line, so every row is parsed and
        checked individually."""
        stub, _ = _install_recorder(tmp_path)
        index_dir = _index_dir(tmp_path)
        index_dir.mkdir(parents=True)
        index_dir.chmod(0o700)
        stale = index_dir / "consumed.2020-01-01.tsv"
        stale.write_text("2020-01-01T00:00:00Z\t/tmp/old-dest\t/tmp/old-src\n")
        _backdate(stale, days_old=31)

        sources = []
        for i in range(8):
            src = tmp_path / f"racing{i}-task.md"
            src.write_text(f"hello {i}\n")
            sources.append(src)

        env = dict(os.environ)
        env.update({"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)})
        procs = [
            subprocess.Popen(
                [str(_SCRIPT), "--consume-only", str(src)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            for src in sources
        ]
        outputs = [proc.communicate() for proc in procs]
        for proc, (_stdout, stderr) in zip(procs, outputs, strict=True):
            assert proc.returncode == 0, stderr

        assert not stale.exists(), "the stale sibling must be swept by one of the racing appends"
        rows = _day_file(tmp_path).read_text().splitlines()
        assert len(rows) == len(sources), f"expected exactly {len(sources)} rows, got {len(rows)}"
        seen_dests = set()
        for row in rows:
            fields = row.split("\t")
            assert len(fields) == 3, f"malformed (truncated or merged) row: {row!r}"
            assert fields[1], f"row missing a destination field: {row!r}"
            assert fields[2], f"row missing a source field: {row!r}"
            assert fields[1] not in seen_dests, f"duplicate destination across rows: {row!r}"
            seen_dests.add(fields[1])

    def test_reader_finds_the_writer_row_end_to_end(self, tmp_path: Path) -> None:
        """Contract test at the writer/reader boundary: invokes the real
        resume-context.sh and the real
        find-consumed-continuity-file.sh as two separate subprocesses under
        one shared RESUME_CONTEXT_TMPDIR, so a future edit reintroducing two
        independent copies of the tmpdir-root formula fails here even
        though each script's own unit tests would still pass in isolation.
        """
        stub, _ = _install_recorder(tmp_path)
        src = tmp_path / "shared-boundary-task.md"
        src.write_text("hello brief\n")
        write_result = _run(
            ["--consume-only", str(src)],
            {"RESUME_CONTEXT_LAUNCHER": str(stub), "RESUME_CONTEXT_TMPDIR": str(tmp_path)},
        )
        assert write_result.returncode == 0, write_result.stderr
        dest = write_result.stdout.strip()

        reader_script = Path(__file__).parent.parent / "find-consumed-continuity-file.sh"
        env = dict(os.environ)
        env["RESUME_CONTEXT_TMPDIR"] = str(tmp_path)
        read_result = subprocess.run(
            [str(reader_script), "shared-boundary"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert read_result.returncode == 0, read_result.stderr
        assert dest in read_result.stdout
