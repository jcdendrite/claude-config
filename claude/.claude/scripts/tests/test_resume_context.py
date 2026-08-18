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
        where the moved copy lives."""
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
