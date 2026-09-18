"""Tests for announce-plan-review-path.sh."""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from helpers import (
    HOOKS_DIR,
    edit_input,
    multiedit_input,
    read_input,
    registered_hook_event_name,
    run_hook_raw,
    write_input,
)

ANNOUNCE_HOOK = HOOKS_DIR / "announce-plan-review-path.sh"

EXPECTED_INSTRUCTIONAL_SUBSTRING = "state it verbatim in the Output format closing line"


def _sibling_path(config_dir: Path, session_id: str = "test-session") -> Path:
    return config_dir / ".plan-review-active.d" / f"{session_id}.reviewed-plan-path"


class TestAnnouncePlanReviewPath:
    # -----------------------------------------------------------------------
    # Root-cause regression: config-dir resolution
    # -----------------------------------------------------------------------

    def test_announces_path_naming_non_default_config_dir(self, isolated_home, tmp_path):
        """A CLAUDE_CONFIG_DIR that is not $HOME/.claude must be reflected in
        the matched sibling path — a hook that resolved the wrong config dir
        would still pass every $HOME/.claude case."""
        config_dir = tmp_path / "custom-profile"
        sibling = _sibling_path(config_dir)
        plan_path = "/repo/.claude/plans/example-plan.md"
        result = run_hook_raw(
            ANNOUNCE_HOOK,
            write_input(str(sibling), content=plan_path),
            home=isolated_home,
            extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert plan_path in payload["systemMessage"]

    # -----------------------------------------------------------------------
    # Happy path
    # -----------------------------------------------------------------------

    def test_well_formed_write_announces_on_both_channels(self, isolated_home):
        sibling = _sibling_path(isolated_home / ".claude")
        plan_path = "/repo/.claude/plans/example-plan.md"
        result = run_hook_raw(
            ANNOUNCE_HOOK, write_input(str(sibling), content=plan_path), home=isolated_home
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert plan_path in payload["systemMessage"]
        additional_context = payload["hookSpecificOutput"]["additionalContext"]
        assert plan_path in additional_context
        # A future edit that keeps the path but weakens or drops the
        # instruction must fail this assertion, not just a generic
        # presence check on the path alone.
        assert EXPECTED_INSTRUCTIONAL_SUBSTRING in additional_context

    # -----------------------------------------------------------------------
    # Emitted contract shape
    # -----------------------------------------------------------------------

    def test_hook_event_name_matches_registration(self, isolated_home):
        sibling = _sibling_path(isolated_home / ".claude")
        result = run_hook_raw(
            ANNOUNCE_HOOK,
            write_input(str(sibling), content="/repo/.claude/plans/example-plan.md"),
            home=isolated_home,
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["hookEventName"] == registered_hook_event_name(ANNOUNCE_HOOK)

    # -----------------------------------------------------------------------
    # Tool filtering (defense-in-depth)
    # -----------------------------------------------------------------------

    @pytest.mark.parametrize(
        "make_input", [edit_input, multiedit_input, read_input], ids=["Edit", "MultiEdit", "Read"]
    )
    def test_non_write_tool_emits_nothing(self, isolated_home, make_input):
        """Defense-in-depth: filters tool_name itself, independent of the
        settings.json matcher condition, which registers Write only."""
        sibling = _sibling_path(isolated_home / ".claude")
        result = run_hook_raw(ANNOUNCE_HOOK, make_input(str(sibling)), home=isolated_home)
        assert result.returncode == 0
        assert result.stdout == ""

    # -----------------------------------------------------------------------
    # Path filtering
    # -----------------------------------------------------------------------

    @pytest.mark.parametrize(
        "build_path",
        [
            lambda config_dir: config_dir / ".plan-review-active.d" / "test-session",
            lambda config_dir: config_dir / ".plan-review-active.d" / "test-session.planmode-path",
            lambda config_dir: config_dir.parent / "elsewhere" / "test-session.reviewed-plan-path",
        ],
        ids=[
            "bare-session-id-pid-marker",
            "planmode-path-sibling",
            "reviewed-plan-path-outside-config-dir",
        ],
    )
    def test_non_matching_path_emits_nothing(self, isolated_home, build_path):
        """Three shapes that must not fire this hook: the plan-review
        active-session PID marker itself (carries a PID, not a plan path);
        the Step 0 .planmode-path sibling (a different sibling with a
        different contract — this hook exists only for the Step 1
        .reviewed-plan-path declaration); and a *.reviewed-plan-path suffix
        match outside the resolved config dir's .plan-review-active.d/ (the
        whole path, not just the suffix, is the glob)."""
        config_dir = isolated_home / ".claude"
        path = build_path(config_dir)
        result = run_hook_raw(
            ANNOUNCE_HOOK,
            write_input(str(path), content="/repo/.claude/plans/example-plan.md"),
            home=isolated_home,
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_nested_path_under_active_dir_still_matches(self, isolated_home):
        """Bash case patterns let `*` span `/` (unlike pathname expansion), so
        the glob also matches a suffix hit with an extra path segment inserted
        under .plan-review-active.d/ -- pins this permissive behavior rather
        than leaving it unpinned. Content still passes through the same
        allowlist gate as any other match, so this is not a new bypass."""
        config_dir = isolated_home / ".claude"
        path = config_dir / ".plan-review-active.d" / "subdir" / "test-session.reviewed-plan-path"
        result = run_hook_raw(
            ANNOUNCE_HOOK,
            write_input(str(path), content="/repo/.claude/plans/example-plan.md"),
            home=isolated_home,
        )
        assert result.returncode == 0
        assert "/repo/.claude/plans/example-plan.md" in result.stdout

    # -----------------------------------------------------------------------
    # Content gate
    # -----------------------------------------------------------------------

    @pytest.mark.parametrize(
        "content",
        ["relative/plan.md", "/repo/.claude/plans/my plan.md", "", None],
        ids=["relative-path", "embedded-space", "empty-content", "missing-content-key"],
    )
    def test_content_gate_rejects(self, isolated_home, content):
        sibling = _sibling_path(isolated_home / ".claude")
        if content is None:
            payload = write_input(str(sibling))
            del payload["tool_input"]["content"]
        else:
            payload = write_input(str(sibling), content=content)
        result = run_hook_raw(ANNOUNCE_HOOK, payload, home=isolated_home)
        assert result.returncode == 0
        assert result.stdout == ""

    def test_one_trailing_newline_still_announces(self, isolated_home):
        """Exactly one trailing newline is stripped before the allowlist
        check, so a Write tool_input.content carrying it still announces —
        with the newline gone from the emitted path."""
        sibling = _sibling_path(isolated_home / ".claude")
        plan_path = "/repo/.claude/plans/example-plan.md"
        result = run_hook_raw(
            ANNOUNCE_HOOK, write_input(str(sibling), content=plan_path + "\n"), home=isolated_home
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert plan_path in payload["systemMessage"]

    # -----------------------------------------------------------------------
    # Injection
    # -----------------------------------------------------------------------

    def test_embedded_newline_injection_emits_nothing(self, isolated_home):
        """Bash `case` globs match across embedded newlines, so this content
        clears the case-glob-adjacent checks and must be caught by the
        allowlist gate instead — the adversarial case that would otherwise
        leak an injected sentinel into model-visible additionalContext."""
        sibling = _sibling_path(isolated_home / ".claude")
        malicious_content = "/tmp/plan\n\nSENTINEL-INJECT\n\nx.md"
        result = run_hook_raw(
            ANNOUNCE_HOOK, write_input(str(sibling), content=malicious_content), home=isolated_home
        )
        assert result.returncode == 0
        assert result.stdout == ""
        assert "SENTINEL-INJECT" not in result.stdout

    # -----------------------------------------------------------------------
    # No subprocess reach
    # -----------------------------------------------------------------------

    def test_never_invokes_git(self, isolated_home, tmp_path):
        """Pins that the sibling's git rev-parse worktree block was not
        copied into this hook — a stub git with a side effect must never
        run, even on a well-formed, matching Write."""
        stub_bin = tmp_path / "stub-bin"
        stub_bin.mkdir()
        marker = stub_bin / "git-invoked"
        fake_git = stub_bin / "git"
        fake_git.write_text(f"#!/bin/bash\ntouch {shlex.quote(str(marker))}\nexit 1\n")
        fake_git.chmod(0o755)
        sibling = _sibling_path(isolated_home / ".claude")
        result = run_hook_raw(
            ANNOUNCE_HOOK,
            write_input(str(sibling), content="/repo/.claude/plans/example-plan.md"),
            home=isolated_home,
            extra_env={"PATH": f"{stub_bin}:{os.environ['PATH']}"},
        )
        assert result.returncode == 0
        assert not marker.exists(), "git must not be invoked by this hook at all"

    # -----------------------------------------------------------------------
    # Fail-open paths
    # -----------------------------------------------------------------------

    def test_empty_stdin_emits_nothing(self, isolated_home):
        env = dict(os.environ)
        env["HOME"] = str(isolated_home)
        result = subprocess.run(
            [str(ANNOUNCE_HOOK)], input="", capture_output=True, text=True, env=env, check=False
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_malformed_json_emits_nothing(self, isolated_home):
        env = dict(os.environ)
        env["HOME"] = str(isolated_home)
        result = subprocess.run(
            [str(ANNOUNCE_HOOK)],
            input="not valid json {{",
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_unreadable_lib_sh_is_silent(self, isolated_home, tmp_path):
        """dirname($0) resolves to HOOKS_DIR only when the hook runs from its
        real location; running a copy with no adjacent _lib.sh exercises the
        "could not source _lib.sh" exit-0 path directly."""
        sibling = _sibling_path(isolated_home / ".claude")
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_hook = Path(tmpdir) / ANNOUNCE_HOOK.name
            shutil.copy2(ANNOUNCE_HOOK, tmp_hook)
            tmp_hook.chmod(0o755)
            result = subprocess.run(
                ["bash", str(tmp_hook)],
                input=json.dumps(write_input(str(sibling), content="/repo/.claude/plans/example-plan.md")),
                cwd=str(tmp_path),
                env={**os.environ, "HOME": str(isolated_home)},
                capture_output=True,
                text=True,
                check=False,
            )
        assert result.returncode == 0
        assert result.stdout.strip() == ""
