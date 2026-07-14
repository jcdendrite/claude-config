"""Tests for consume-durable-continuity-file-on-read.sh."""
from __future__ import annotations

import shutil
import stat
import time
from pathlib import Path

import pytest
from helpers import (
    HOOKS_DIR,
    agent_input,
    install_resume_context_script,
    read_input,
    run_hook,
)

CONSUME_HOOK = HOOKS_DIR / "consume-durable-continuity-file-on-read.sh"


def _write_fixture(isolated_home: Path, rel_path: str) -> Path:
    path = isolated_home / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("fixture content\n")
    return path


class TestConsumeDurableContinuityFileOnRead:
    def test_read_handoff_file_consumes_it(self, isolated_home):
        install_resume_context_script(isolated_home)
        fixture = _write_fixture(isolated_home, ".claude/handoffs/example-handoff.md")
        run_hook(CONSUME_HOOK, read_input(str(fixture)), home=isolated_home)
        assert not fixture.exists()

    def test_read_brief_file_consumes_it(self, isolated_home):
        install_resume_context_script(isolated_home)
        fixture = _write_fixture(isolated_home, ".claude/briefs/example-task.md")
        run_hook(CONSUME_HOOK, read_input(str(fixture)), home=isolated_home)
        assert not fixture.exists()

    def test_read_unrelated_directory_is_noop(self, isolated_home):
        install_resume_context_script(isolated_home)
        fixture = _write_fixture(isolated_home, ".claude/skills/handoff/SKILL.md")
        run_hook(CONSUME_HOOK, read_input(str(fixture)), home=isolated_home)
        assert fixture.exists()

    def test_read_same_dir_wrong_suffix_is_noop(self, isolated_home):
        """Boundary case distinct from an unrelated directory: a future glob
        loosening that over-matches everything under handoffs/ would pass the
        unrelated-directory test but should still fail this one."""
        install_resume_context_script(isolated_home)
        fixture = _write_fixture(isolated_home, ".claude/handoffs/notes.md")
        run_hook(CONSUME_HOOK, read_input(str(fixture)), home=isolated_home)
        assert fixture.exists()

    def test_wrong_tool_name_is_noop(self, isolated_home):
        install_resume_context_script(isolated_home)
        fixture = _write_fixture(isolated_home, ".claude/handoffs/example-handoff.md")
        run_hook(CONSUME_HOOK, agent_input(), home=isolated_home)
        assert fixture.exists()

    def test_missing_resume_context_script_is_noop(self, isolated_home):
        # No install_resume_context_script call — script absent, hook must
        # fail open rather than error.
        fixture = _write_fixture(isolated_home, ".claude/handoffs/example-handoff.md")
        run_hook(CONSUME_HOOK, read_input(str(fixture)), home=isolated_home)
        assert fixture.exists()

    def test_kill_switch_disables_consumption(self, isolated_home):
        install_resume_context_script(isolated_home)
        (isolated_home / ".claude" / ".consume-durable-continuity-disabled").touch()
        fixture = _write_fixture(isolated_home, ".claude/handoffs/example-handoff.md")
        run_hook(CONSUME_HOOK, read_input(str(fixture)), home=isolated_home)
        assert fixture.exists()

    def test_double_read_of_already_consumed_file_is_noop(self, isolated_home):
        """Second firing on a path the first firing already moved away —
        distinct failure mode from 'script binary entirely missing'."""
        install_resume_context_script(isolated_home)
        fixture = _write_fixture(isolated_home, ".claude/handoffs/example-handoff.md")
        run_hook(CONSUME_HOOK, read_input(str(fixture)), home=isolated_home)
        assert not fixture.exists()
        # Second firing on the same (now-gone) path must not error out.
        assert run_hook(CONSUME_HOOK, read_input(str(fixture)), home=isolated_home) == "allow"

    def test_read_of_path_traversing_a_symlink_is_noop(self, isolated_home):
        """Documents the literal-path-only scope as an intentional, tested
        boundary: a Read whose file_path resolves into handoffs/ through a
        symlink but doesn't textually match the glob (the real path lives
        elsewhere) is left alone."""
        install_resume_context_script(isolated_home)
        real_dir = isolated_home / "elsewhere"
        real_dir.mkdir()
        real_file = real_dir / "example-handoff.md"
        real_file.write_text("fixture content\n")
        alias_dir = isolated_home / ".claude" / "handoffs"
        alias_dir.mkdir(parents=True)
        alias_path = alias_dir / "aliased-handoff.md"
        alias_path.symlink_to(real_file)
        run_hook(CONSUME_HOOK, read_input(str(real_file)), home=isolated_home)
        assert real_file.exists()

    def test_read_of_symlink_planted_at_glob_matching_path_leaves_target_untouched(self, isolated_home):
        """The reverse boundary: a symlink placed AT a glob-matching path
        (rather than merely traversed through one) does textually match, so
        the hook fires — but resume-context.sh rejects a symlink source
        outright rather than moving-then-chmodding it, so both the symlink
        and whatever it points to are left untouched."""
        install_resume_context_script(isolated_home)
        target = isolated_home / "unrelated-target.txt"
        target.write_text("sensitive content\n")
        target.chmod(0o644)
        planted = isolated_home / ".claude" / "handoffs" / "planted-handoff.md"
        planted.parent.mkdir(parents=True)
        planted.symlink_to(target)

        run_hook(CONSUME_HOOK, read_input(str(planted)), home=isolated_home)

        assert planted.is_symlink()
        assert stat.S_IMODE(target.stat().st_mode) == 0o644

    def test_read_case_differing_path_is_noop(self, isolated_home):
        """Documents the case-sensitive-glob boundary named in the hook's
        header: a path differing only in case is a no-op here, even though it
        would resolve to the same file on a case-insensitive filesystem
        (default macOS APFS)."""
        install_resume_context_script(isolated_home)
        fixture = _write_fixture(isolated_home, ".claude/Handoffs/example-handoff.md")
        run_hook(CONSUME_HOOK, read_input(str(fixture)), home=isolated_home)
        assert fixture.exists()

    def test_hook_always_exits_allow(self, isolated_home):
        install_resume_context_script(isolated_home)
        fixture = _write_fixture(isolated_home, ".claude/handoffs/example-handoff.md")
        assert run_hook(CONSUME_HOOK, read_input(str(fixture)), home=isolated_home) == "allow"

    def test_timeout_bounds_a_hung_resume_context(self, isolated_home):
        """The one property the timeout wrapper exists to guarantee — an SDET
        review round found it had no test, since there was no seam to inject
        a short timeout without a real multi-second sleep in the suite.
        Injects a 1s timeout against a stub resume-context.sh that sleeps
        10s and asserts the hook returns well before the full sleep elapses.
        """
        if not shutil.which("timeout"):
            pytest.skip("timeout(1) not available — BSD/macOS without coreutils")

        scripts_dir = isolated_home / ".claude" / "scripts"
        scripts_dir.mkdir(parents=True)
        stub = scripts_dir / "resume-context.sh"
        stub.write_text("#!/bin/bash\nsleep 10\n")
        stub.chmod(0o755)
        fixture = _write_fixture(isolated_home, ".claude/handoffs/example-handoff.md")

        start = time.monotonic()
        run_hook(
            CONSUME_HOOK,
            read_input(str(fixture)),
            home=isolated_home,
            extra_env={"RESUME_CONTEXT_HOOK_TIMEOUT_SECONDS": "1"},
        )
        elapsed = time.monotonic() - start

        assert elapsed < 5, (
            f"hook took {elapsed:.1f}s — RESUME_CONTEXT_HOOK_TIMEOUT_SECONDS "
            "did not bound the hang"
        )

    def test_timeout_absent_fallback_still_consumes(self, isolated_home, tmp_path):
        """Mirrors test_lib.py's test_timeout_absent_fallback_valid_payload_returns_ok:
        build a PATH that excludes `timeout` but keeps every other binary the
        hook and resume-context.sh need, and assert the bare fallback call
        still performs the real consume — an SDET review round found this
        branch (documented as the BSD/macOS path) is otherwise never
        exercised, since `timeout` is present on essentially every CI runner.
        """
        install_resume_context_script(isolated_home)
        fixture = _write_fixture(isolated_home, ".claude/handoffs/example-handoff.md")

        shadow_bin = tmp_path / "shadow-bin"
        shadow_bin.mkdir()
        for cmd in ["jq", "bash", "cat", "mktemp", "mv", "chmod"]:
            cmd_path = shutil.which(cmd)
            if cmd_path:
                (shadow_bin / cmd).symlink_to(cmd_path)

        run_hook(
            CONSUME_HOOK,
            read_input(str(fixture)),
            home=isolated_home,
            extra_env={"PATH": str(shadow_bin)},
        )
        assert not fixture.exists()
