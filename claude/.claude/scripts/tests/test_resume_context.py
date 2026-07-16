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


def _install_recorder(tmp_path: Path) -> tuple[Path, Path]:
    """Write a launcher stub that records its argv, return (stub_path, recorder_path)."""
    recorder = tmp_path / "recorder.txt"
    stub = tmp_path / "fake-launcher"
    stub.write_text(_RECORDER_STUB.format(recorder=recorder))
    stub.chmod(0o755)
    return stub, recorder


def _run(args: list[str], env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
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
