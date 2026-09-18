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
    matcher_admits_tool,
    multiedit_input,
    read_input,
    registered_hook_event_name,
    registered_hook_matchers,
    run_hook_raw,
    write_input,
)

ANNOUNCE_HOOK = HOOKS_DIR / "announce-plan-review-path.sh"

EXPECTED_INSTRUCTIONAL_SUBSTRING = "State the plan path you are reviewing verbatim in the Output format closing line."
SESSION_ID = "test-session"
PLAN_PATH = "/repo/.claude/plans/example-plan.md"
# The hook rejects any path longer than this many characters.
PATH_LIMIT = 4096


def _sibling_path(config_dir: Path, session_id: str = SESSION_ID) -> Path:
    return config_dir / ".plan-review-active.d" / f"{session_id}.reviewed-plan-path"


def _declaration(sibling: str | Path, content: str = PLAN_PATH, session_id: str | None = SESSION_ID) -> dict:
    return write_input(str(sibling), content=content, session_id=session_id)


def _assert_announces_exactly(result: subprocess.CompletedProcess, plan_path: str) -> None:
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["systemMessage"] == f"Plan declared for review: {plan_path}"
    additional_context = payload["hookSpecificOutput"]["additionalContext"]
    assert f"declaration file for /plan-review: {plan_path}. " in additional_context
    assert EXPECTED_INSTRUCTIONAL_SUBSTRING in additional_context


def _assert_silent(result: subprocess.CompletedProcess) -> None:
    assert result.returncode == 0
    assert result.stdout == ""


class TestAnnouncePlanReviewPath:
    # -----------------------------------------------------------------------
    # Root-cause regression: config-dir resolution
    # -----------------------------------------------------------------------

    def test_announces_path_naming_non_default_config_dir(self, isolated_home, tmp_path):
        """A CLAUDE_CONFIG_DIR that is not $HOME/.claude must be reflected in
        the matched sibling path — a hook that resolved the wrong config dir
        would still pass every $HOME/.claude case."""
        config_dir = tmp_path / "custom-profile"
        result = run_hook_raw(
            ANNOUNCE_HOOK,
            _declaration(_sibling_path(config_dir)),
            home=isolated_home,
            extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
        )
        _assert_announces_exactly(result, PLAN_PATH)

    # -----------------------------------------------------------------------
    # Happy path and trailing newlines
    # -----------------------------------------------------------------------

    def test_well_formed_write_announces_on_both_channels(self, isolated_home):
        result = run_hook_raw(
            ANNOUNCE_HOOK, _declaration(_sibling_path(isolated_home / ".claude")), home=isolated_home
        )
        _assert_announces_exactly(result, PLAN_PATH)

    @pytest.mark.parametrize("trailing_newlines", ["\n", "\n\n"], ids=["one", "two"])
    def test_trailing_newlines_are_dropped_from_the_announced_path(self, isolated_home, trailing_newlines):
        """Command substitution strips every trailing newline, so none reaches the output."""
        result = run_hook_raw(
            ANNOUNCE_HOOK,
            _declaration(_sibling_path(isolated_home / ".claude"), content=PLAN_PATH + trailing_newlines),
            home=isolated_home,
        )
        _assert_announces_exactly(result, PLAN_PATH)

    def test_path_of_exactly_path_limit_characters_announces(self, isolated_home):
        boundary_path = "/" + "a" * (PATH_LIMIT - 1)
        result = run_hook_raw(
            ANNOUNCE_HOOK,
            _declaration(_sibling_path(isolated_home / ".claude"), content=boundary_path),
            home=isolated_home,
        )
        _assert_announces_exactly(result, boundary_path)

    # -----------------------------------------------------------------------
    # Emitted contract shape and registration
    # -----------------------------------------------------------------------

    def test_hook_event_name_matches_registration(self, isolated_home):
        result = run_hook_raw(
            ANNOUNCE_HOOK, _declaration(_sibling_path(isolated_home / ".claude")), home=isolated_home
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["hookEventName"] == registered_hook_event_name(ANNOUNCE_HOOK)

    def test_registered_matcher_admits_write(self):
        """The hook is invoked directly in these tests, so only this check
        catches a settings.json matcher that no longer admits Write."""
        matchers = registered_hook_matchers(ANNOUNCE_HOOK)
        assert matchers, f"{ANNOUNCE_HOOK.name} has no registered matcher"
        assert all(matcher_admits_tool(matcher, "Write") for matcher in matchers)

    # -----------------------------------------------------------------------
    # Tool filtering (defense-in-depth)
    # -----------------------------------------------------------------------

    @pytest.mark.parametrize(
        "make_input", [edit_input, multiedit_input, read_input], ids=["Edit", "MultiEdit", "Read"]
    )
    def test_non_write_tool_emits_nothing(self, isolated_home, make_input):
        """The payload carries the matching sibling path and valid absolute
        content, so only the hook's own tool_name filter can suppress it."""
        payload = make_input(str(_sibling_path(isolated_home / ".claude")))
        payload["session_id"] = SESSION_ID
        payload["tool_input"]["content"] = PLAN_PATH
        _assert_silent(run_hook_raw(ANNOUNCE_HOOK, payload, home=isolated_home))

    # -----------------------------------------------------------------------
    # Path filtering: each case violates exactly one part of the exact path
    # -----------------------------------------------------------------------

    @pytest.mark.parametrize(
        "build_path",
        [
            lambda home: f"{home}/.claude/.plan-review-active.d/{SESSION_ID}",
            lambda home: f"{home}/.claude/.plan-review-active.d/{SESSION_ID}.planmode-path",
            lambda home: f"{home}/other-profile/.plan-review-active.d/{SESSION_ID}.reviewed-plan-path",
            lambda home: f"{home}/.claude/.respond-pr-active.d/{SESSION_ID}.reviewed-plan-path",
            lambda home: f"{home}/.claude/.plan-review-active.d/other-session.reviewed-plan-path",
            lambda home: f"{home}/.claude/.plan-review-active.d/nested/{SESSION_ID}.reviewed-plan-path",
            lambda home: f"{home}/.claude/.plan-review-active.d/../.plan-review-active.d/{SESSION_ID}.reviewed-plan-path",
            lambda home: f"{home}/.claude/.plan-review-active.d/../../{SESSION_ID}.reviewed-plan-path",
        ],
        ids=[
            "bare-session-marker",
            "planmode-path-sibling",
            "other-config-dir",
            "other-active-dir",
            "other-sessions-id",
            "nested-segment",
            "dotdot-back-into-same-dir",
            "dotdot-out-of-config-dir",
        ],
    )
    def test_non_matching_path_emits_nothing(self, isolated_home, build_path):
        _assert_silent(
            run_hook_raw(ANNOUNCE_HOOK, _declaration(build_path(isolated_home)), home=isolated_home)
        )

    def test_missing_file_path_key_emits_nothing(self, isolated_home):
        payload = _declaration(_sibling_path(isolated_home / ".claude"))
        del payload["tool_input"]["file_path"]
        _assert_silent(run_hook_raw(ANNOUNCE_HOOK, payload, home=isolated_home))

    # -----------------------------------------------------------------------
    # Session binding
    # -----------------------------------------------------------------------

    @pytest.mark.parametrize(
        "session_id",
        [None, "", "../escape", "dotted.id", "with space"],
        ids=["missing", "empty", "traversal", "dotted", "space"],
    )
    def test_unusable_session_id_emits_nothing(self, isolated_home, session_id):
        """The sibling path is built from the payload's own (here mismatching
        or unvalidated) session id, so only the session-id check can reject it."""
        sibling_name = session_id if session_id else SESSION_ID
        sibling = isolated_home / ".claude" / ".plan-review-active.d" / f"{sibling_name}.reviewed-plan-path"
        _assert_silent(
            run_hook_raw(ANNOUNCE_HOOK, _declaration(sibling, session_id=session_id), home=isolated_home)
        )

    # -----------------------------------------------------------------------
    # Content gate
    # -----------------------------------------------------------------------

    @pytest.mark.parametrize(
        "content",
        [
            "relative/plan.md",
            "",
            "\n",
            "/repo/.claude/plans/my plan.md",
            "/repo/$(x).md",
            "/repo/`x`.md",
            "/repo/a;b.md",
            "/repo/it's.md",
            "/repo/a\x1bb.md",
            "/repo/a\rb.md",
            "/repo/a\tb.md",
            "/repo/caf\u00e9.md",
            "/repo/a\nb.md",
            "/tmp/plan\n\nSENTINEL-INJECT\n\nx.md",
            "/repo/pl\u0000an.md",
            "/" + "a" * PATH_LIMIT,
            # The bash-side cap alone rejects this; the case does not pin the jq-side guard.
            "/" + "a" * 2_000_000,
        ],
        ids=[
            "relative-path",
            "empty",
            "newline-only",
            "space",
            "command-substitution",
            "backtick",
            "semicolon",
            "single-quote",
            "escape-byte",
            "carriage-return",
            "tab",
            "non-ascii",
            "embedded-newline",
            "newline-injection",
            "nul-byte",
            "over-path-limit",
            "multi-megabyte",
        ],
    )
    def test_content_gate_rejects(self, isolated_home, content):
        result = run_hook_raw(
            ANNOUNCE_HOOK, _declaration(_sibling_path(isolated_home / ".claude"), content=content), home=isolated_home
        )
        _assert_silent(result)

    @pytest.mark.parametrize("content", ["/repo/caf\u00e9", "/repo/a\u00b2"], ids=["accented-letter", "superscript-digit"])
    def test_allowlist_stays_ascii_under_collating_locale_without_globasciiranges(self, isolated_home, content):
        """Bash < 5.0 defaults globasciiranges off, so bracket ranges follow the
        locale's collation. Only the function-scoped `local LC_ALL=C` keeps
        `[A-Za-z]` ASCII-only there; CI's bash 5.x reaches that state only via
        `bash +O globasciiranges` under a collating locale."""
        locale_name = "en_GB.utf8"
        available_locales = subprocess.run(["locale", "-a"], capture_output=True, text=True, check=False).stdout
        if locale_name not in available_locales.split():
            pytest.skip(f"{locale_name} locale is not installed")
        env = {**os.environ, "HOME": str(isolated_home), "LC_ALL": locale_name}
        # An ambient CLAUDE_CONFIG_DIR would move the hook's config dir off the declared path and make the silence vacuous.
        env.pop("CLAUDE_CONFIG_DIR", None)
        result = subprocess.run(
            ["bash", "+O", "globasciiranges", str(ANNOUNCE_HOOK)],
            input=json.dumps(_declaration(_sibling_path(isolated_home / ".claude"), content=content)),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        _assert_silent(result)

    def test_missing_content_key_emits_nothing(self, isolated_home):
        payload = _declaration(_sibling_path(isolated_home / ".claude"))
        del payload["tool_input"]["content"]
        _assert_silent(run_hook_raw(ANNOUNCE_HOOK, payload, home=isolated_home))

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
            _declaration(sibling),
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
                input=json.dumps(_declaration(sibling)),
                cwd=str(tmp_path),
                env={**os.environ, "HOME": str(isolated_home)},
                capture_output=True,
                text=True,
                check=False,
            )
        assert result.returncode == 0
        assert result.stdout.strip() == ""
