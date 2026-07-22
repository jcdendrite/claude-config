"""Tests for harden-durable-continuity-file-mode.sh."""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

from helpers import (
    CLAUDE_DIR,
    HOOKS_DIR,
    agent_input,
    edit_input,
    multiedit_input,
    write_input,
)

HARDEN_HOOK = HOOKS_DIR / "harden-durable-continuity-file-mode.sh"


def _write_fixture(isolated_home: Path, rel_path: str, mode: int = 0o664) -> Path:
    path = isolated_home / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("fixture content\n")
    path.chmod(mode)
    return path


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _run_hook_raw(
    hook: Path, tool_input: dict, home: Path, extra_env: dict | None = None
) -> subprocess.CompletedProcess:
    """Like helpers.run_hook, but returns the raw CompletedProcess — needed
    for tests asserting on returncode and on stdout being empty (this hook
    never emits a deny envelope or any other JSON payload)."""
    env = dict(os.environ)
    env["HOME"] = str(home)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(hook)],
        input=json.dumps(tool_input),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _run_hook_raw_stdin(
    hook: Path, stdin_text: str, home: Path, extra_env: dict | None = None
) -> subprocess.CompletedProcess:
    """Like _run_hook_raw, but sends stdin_text verbatim instead of JSON-
    encoding it — needed for malformed-input tests, where the payload under
    test is itself not valid JSON."""
    env = dict(os.environ)
    env["HOME"] = str(home)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(hook)],
        input=stdin_text,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


class TestHardenDurableContinuityFileMode:
    def test_write_into_handoffs_lands_600(self, isolated_home):
        fixture = _write_fixture(isolated_home, ".claude/handoffs/example-handoff.md")
        # Pin the hard-link guard's threshold by name: a single-link file is
        # the legitimate case and must still be chmodded. Without this
        # precondition a reversed comparison in the guard (>0 rather than
        # >1) would silently skip every real continuity file and no test
        # would say why.
        assert fixture.stat().st_nlink == 1
        result = _run_hook_raw(HARDEN_HOOK, write_input(str(fixture)), home=isolated_home)
        assert result.returncode == 0
        assert _mode(fixture) == 0o600

    def test_write_into_briefs_lands_600(self, isolated_home):
        fixture = _write_fixture(isolated_home, ".claude/briefs/example-task.md")
        result = _run_hook_raw(HARDEN_HOOK, write_input(str(fixture)), home=isolated_home)
        assert result.returncode == 0
        assert _mode(fixture) == 0o600

    def test_non_suffix_filename_lands_600(self, isolated_home):
        """This hook matches any file in the two directories, not only the
        `*-handoff.md`/`*-task.md` suffixes the sibling consume-on-read hook
        narrows to — chmodding an ad-hoc file (e.g. a stray `.sql` extract)
        is idempotent and non-destructive, so the narrower risk that
        justifies the sibling's suffix glob doesn't transfer here."""
        fixture = _write_fixture(isolated_home, ".claude/handoffs/extract.sql")
        result = _run_hook_raw(HARDEN_HOOK, write_input(str(fixture)), home=isolated_home)
        assert result.returncode == 0
        assert _mode(fixture) == 0o600

    def test_path_outside_both_directories_is_untouched(self, isolated_home):
        fixture = _write_fixture(isolated_home, ".claude/skills/handoff/SKILL.md")
        result = _run_hook_raw(HARDEN_HOOK, write_input(str(fixture)), home=isolated_home)
        assert result.returncode == 0
        assert _mode(fixture) == 0o664

    def test_directory_prefix_collision_is_untouched(self, isolated_home):
        """Boundary case the trailing-slash-in-the-glob design decision
        exists for: `~/.claude/handoffs-archive/` must not collide with the
        `~/.claude/handoffs/` prefix."""
        fixture = _write_fixture(isolated_home, ".claude/handoffs-archive/example.md")
        result = _run_hook_raw(HARDEN_HOOK, write_input(str(fixture)), home=isolated_home)
        assert result.returncode == 0
        assert _mode(fixture) == 0o664

    def test_dot_dot_traversal_path_textually_prefixed_is_untouched(self, isolated_home):
        """A textual `case` glob against the raw path would treat `..` as
        ordinary characters and match this path; the realpath canonicalize
        step this hook applies before matching must resolve the traversal
        and correctly exclude the file it actually points to."""
        outside_dir = isolated_home / "elsewhere"
        outside_dir.mkdir(parents=True)
        outside_file = outside_dir / "example.md"
        outside_file.write_text("fixture content\n")
        outside_file.chmod(0o664)
        handoffs_dir = isolated_home / ".claude" / "handoffs"
        handoffs_dir.mkdir(parents=True)
        traversal_path = str(handoffs_dir / ".." / ".." / "elsewhere" / "example.md")

        result = _run_hook_raw(HARDEN_HOOK, write_input(traversal_path), home=isolated_home)

        assert result.returncode == 0
        assert _mode(outside_file) == 0o664

    def test_edit_is_matched(self, isolated_home):
        fixture = _write_fixture(isolated_home, ".claude/handoffs/example-handoff.md")
        result = _run_hook_raw(HARDEN_HOOK, edit_input(str(fixture)), home=isolated_home)
        assert result.returncode == 0
        assert _mode(fixture) == 0o600

    def test_multiedit_is_matched(self, isolated_home):
        fixture = _write_fixture(isolated_home, ".claude/briefs/example-task.md")
        result = _run_hook_raw(HARDEN_HOOK, multiedit_input(str(fixture)), home=isolated_home)
        assert result.returncode == 0
        assert _mode(fixture) == 0o600

    def test_non_matching_tool_name_is_noop(self, isolated_home):
        fixture = _write_fixture(isolated_home, ".claude/handoffs/example-handoff.md")
        result = _run_hook_raw(HARDEN_HOOK, agent_input(), home=isolated_home)
        assert result.returncode == 0
        assert _mode(fixture) == 0o664

    def test_malformed_stdin_exits_zero_silently(self, isolated_home):
        result = _run_hook_raw_stdin(HARDEN_HOOK, "not json", home=isolated_home)
        assert result.returncode == 0
        assert result.stdout == ""

    def test_empty_stdin_exits_zero_silently(self, isolated_home):
        result = _run_hook_raw_stdin(HARDEN_HOOK, "", home=isolated_home)
        assert result.returncode == 0
        assert result.stdout == ""

    def test_jq_absent_fails_open_no_chmod(self, isolated_home, tmp_path):
        """jq is required to extract tool_name/file_path at all — a PATH
        excluding jq must fail open before the hook ever reaches chmod."""
        fixture = _write_fixture(isolated_home, ".claude/handoffs/example-handoff.md")

        shadow_bin = tmp_path / "shadow-bin"
        shadow_bin.mkdir()
        for cmd in ["bash", "cat", "chmod", "realpath"]:
            cmd_path = shutil.which(cmd)
            if cmd_path:
                (shadow_bin / cmd).symlink_to(cmd_path)

        result = _run_hook_raw(
            HARDEN_HOOK,
            write_input(str(fixture)),
            home=isolated_home,
            extra_env={"PATH": str(shadow_bin)},
        )
        assert result.returncode == 0
        assert result.stdout == ""
        assert _mode(fixture) == 0o664, "jq unavailable — hook can't parse tool_input, must not chmod"

    def test_symlink_at_matching_path_is_not_chmodded_and_target_untouched(self, isolated_home):
        """The reverse boundary from the trailing-slash case: a symlink
        placed AT a glob-matching path (rather than merely traversed
        through one) resolves via realpath to a target outside both
        directories, so the prefix match excludes it — the symlink itself
        and whatever it points to are both left untouched."""
        target = isolated_home / "unrelated-target.txt"
        target.write_text("sensitive content\n")
        target.chmod(0o644)
        planted = isolated_home / ".claude" / "handoffs" / "planted-handoff.md"
        planted.parent.mkdir(parents=True)
        planted.symlink_to(target)

        result = _run_hook_raw(HARDEN_HOOK, write_input(str(planted)), home=isolated_home)

        assert result.returncode == 0
        assert planted.is_symlink()
        assert _mode(target) == 0o644

    def test_symlink_to_sibling_file_in_same_directory_is_not_chmodded(self, isolated_home):
        """Distinct from the case above: here the symlink's target also
        resolves inside handoffs/, so the prefix match alone would not
        exclude it — the [ -f ]/[ ! -L ] guard on the literal (unresolved)
        path is what skips it instead."""
        real_file = _write_fixture(isolated_home, ".claude/handoffs/real-handoff.md", mode=0o644)
        link_path = isolated_home / ".claude" / "handoffs" / "link-handoff.md"
        link_path.symlink_to(real_file)

        result = _run_hook_raw(HARDEN_HOOK, write_input(str(link_path)), home=isolated_home)

        assert result.returncode == 0
        assert link_path.is_symlink()
        assert _mode(real_file) == 0o644

    def test_hard_link_at_matching_path_is_not_chmodded_and_target_untouched(
        self, isolated_home
    ):
        """A hard link is not a symlink, so [ ! -L ] passes it, and realpath
        reports it unchanged because the link itself IS a canonical path —
        neither guard above excludes it. chmod acts on the shared inode, so
        without the link-count guard this would narrow the mode of a file
        outside both directories. Deterministic, not a race."""
        victim = isolated_home / "unrelated-target.txt"
        victim.write_text("sensitive content\n")
        victim.chmod(0o644)
        planted = isolated_home / ".claude" / "handoffs" / "planted-handoff.md"
        planted.parent.mkdir(parents=True)
        os.link(victim, planted)
        # Fixture sanity: prove the two paths really share one inode rather
        # than the link having silently degraded to a copy.
        assert planted.stat().st_nlink == 2

        result = _run_hook_raw(HARDEN_HOOK, write_input(str(planted)), home=isolated_home)

        assert result.returncode == 0
        assert result.stdout == ""
        # One inode, so either path's mode answers for both.
        assert _mode(victim) == 0o644, "hard-linked file must not be chmodded through"

    def test_stat_absent_fails_open_no_chmod(self, isolated_home, tmp_path):
        """The hard-link guard's own dependency: with no stat on PATH the
        link count is unknown, and the hook must skip the chmod rather than
        proceed or crash under `set -uo pipefail`."""
        fixture = _write_fixture(isolated_home, ".claude/handoffs/example-handoff.md")

        shadow_bin = tmp_path / "shadow-bin"
        shadow_bin.mkdir()
        for cmd in ["bash", "cat", "chmod", "realpath", "jq"]:
            cmd_path = shutil.which(cmd)
            if cmd_path:
                (shadow_bin / cmd).symlink_to(cmd_path)

        result = _run_hook_raw(
            HARDEN_HOOK,
            write_input(str(fixture)),
            home=isolated_home,
            extra_env={"PATH": str(shadow_bin)},
        )
        assert result.returncode == 0
        assert result.stdout == ""
        assert _mode(fixture) == 0o664, "stat unavailable — link count unknown, must not chmod"

    def test_missing_file_does_not_crash(self, isolated_home):
        """tool_input names a path the hook never finds on disk (e.g. it was
        removed between the tool call and this hook firing) — must exit
        cleanly rather than erroring on a chmod against a nonexistent path."""
        handoffs_dir = isolated_home / ".claude" / "handoffs"
        handoffs_dir.mkdir(parents=True)
        missing_path = str(handoffs_dir / "never-written-handoff.md")

        result = _run_hook_raw(HARDEN_HOOK, write_input(missing_path), home=isolated_home)

        assert result.returncode == 0
        assert not Path(missing_path).exists()

    def test_absent_file_path_fails_open_no_chmod(self, isolated_home):
        """A matching tool_name whose tool_input carries no file_path at all
        must fail open at the presence guard rather than proceeding with an
        empty path — an empty path would otherwise reach realpath, which
        resolves "" to the process CWD and could target a directory."""
        fixture = _write_fixture(isolated_home, ".claude/handoffs/example-handoff.md")

        result = _run_hook_raw_stdin(
            HARDEN_HOOK,
            '{"tool_name": "Write", "tool_input": {}}',
            home=isolated_home,
        )

        assert result.returncode == 0
        assert result.stdout == ""
        assert _mode(fixture) == 0o664, "no file_path in tool_input — hook must not chmod anything"

    def test_no_output_emitted_under_any_input(self, isolated_home):
        """This hook is informational and never emits a deny envelope (or
        any other JSON payload) under any input shape — success, no-op, or
        fail-open all produce silent exit 0."""
        fixture = _write_fixture(isolated_home, ".claude/handoffs/example-handoff.md")
        cases = [
            write_input(str(fixture)),
            edit_input(str(fixture)),
            multiedit_input(str(fixture)),
            agent_input(),
        ]
        for tool_input in cases:
            result = _run_hook_raw(HARDEN_HOOK, tool_input, home=isolated_home)
            assert result.stdout == "", f"unexpected stdout for {tool_input!r}: {result.stdout!r}"
        malformed = _run_hook_raw_stdin(HARDEN_HOOK, "not json", home=isolated_home)
        assert malformed.stdout == ""


def test_settings_posttooluse_matcher_exists_and_references_hook():
    """Highest-priority test in the plan: the only automated backstop
    against a matcher typo, a wrong-tool matcher, or the entry landing in
    the wrong hook-event array — none of which any other test would catch.

    Mirrors test_require_plan_review.py::test_settings_exitplanmode_matcher_exists_and_isolated.
    """
    settings_path = CLAUDE_DIR / "settings.json"
    settings = json.loads(settings_path.read_text())
    post_tool_use = settings.get("hooks", {}).get("PostToolUse", [])

    write_edit_blocks = [
        b for b in post_tool_use if b.get("matcher") == "Edit|Write|MultiEdit"
    ]
    assert len(write_edit_blocks) == 1, (
        "PostToolUse should have exactly one Edit|Write|MultiEdit matcher block"
    )
    hook_commands = [h["command"] for h in write_edit_blocks[0].get("hooks", [])]
    assert any(
        cmd.endswith("harden-durable-continuity-file-mode.sh") for cmd in hook_commands
    ), (
        "Edit|Write|MultiEdit PostToolUse block must include "
        "harden-durable-continuity-file-mode.sh"
    )
