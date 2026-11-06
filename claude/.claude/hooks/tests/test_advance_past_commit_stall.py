"""Tests for advance-past-commit-stall.sh (GH-526).

The hook fires only when ALL hold: no per-session/repo kill switch, main
session (not a subagent), valid session_id, not plan mode, a fresh prompt_id,
the final sentence of the last assistant message asks permission to
commit/push/open a PR without also carrying an exclusion phrase,
_lib_autonomous_shipping_active holds for the resolved repo, and work is
pending (dirty tree or HEAD ahead of its upstream). Every other path must
exit 0 with empty stdout — this is a fail-silent Stop hook, never a gate.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest
from helpers import (
    CANARY_CONTENT,
    HOOKS_DIR,
    TRAVERSAL_SESSION_ID,
    build_path_without,
    config_schema_text_without_key,
    hook_beside_degraded_schema,
    init_git_repo_with_commit,
    plant_traversal_canary,
    run_hook_stop,
    stop_input,
)

ADVANCE_HOOK = HOOKS_DIR / "advance-past-commit-stall.sh"

# Verbatim from .claude/plans/commit-stall-block.md's Context section — the
# issue's own quotes. The first is exactly 64 characters, which pins the
# tail-slice-floor defect the plan's Assumption ledger Row 13 documents:
# a byte-count tail slice (${msg: -600}) returns EMPTY for any message
# shorter than 600 chars, so this exact quote is the regression pin for the
# final-sentence-extraction replacement.
ISSUE_QUOTE_QUESTION = (
    "Want me to commit this, or do you want to review the diff first?"
)
ISSUE_QUOTE_NONQUESTION = "Per your standing instruction, I haven't committed."
assert len(ISSUE_QUOTE_QUESTION) == 64, "pin drifted from the plan's own 64-char quote"

# The plan's own design-history examples (Part 2), also verbatim.
MODAL_CASE = (
    "Fixed the failing test in the parser. "
    "Want me to commit this, or do you want to review the diff first?"
)
DOCUMENTED_MISS = (
    "The push failed with a non-fast-forward error. "
    "Want me to push again after rebasing?"
)


def _init_repo(repo: Path) -> None:
    init_git_repo_with_commit(repo, branch="main", file_name="f", content="a\n")


@pytest.fixture
def dirty_repo(tmp_path) -> Path:
    """Committed history plus an uncommitted, unstaged edit — the primary
    'want me to commit' trigger shape."""
    repo = tmp_path / "dirty-repo"
    _init_repo(repo)
    (repo / "f").write_text("a\nb\n")
    return repo


@pytest.fixture
def clean_pushed_no_pending_repo(tmp_path) -> Path:
    """Clean tree, upstream tracking configured and matching HEAD exactly —
    the post-PR-open state where no work is pending."""
    repo = tmp_path / "clean-pushed"
    _init_repo(repo)
    # A fake, unreachable remote: `--set-upstream-to` requires refs/remotes/*
    # to belong to a configured remote, not just exist as a bare ref.
    subprocess.run(
        ["git", "remote", "add", "origin", "https://example.invalid/repo.git"],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "checkout", "-q", "-b", "feature"], cwd=repo, check=True)
    (repo / "f").write_text("a\nb\n")
    subprocess.run(["git", "add", "f"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "work"], cwd=repo, check=True)
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/feature", "HEAD"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "branch", "--set-upstream-to=origin/feature", "feature"],
        cwd=repo,
        check=True,
    )
    return repo


@pytest.fixture
def ahead_of_upstream_repo(clean_pushed_no_pending_repo) -> Path:
    """Clean tree, upstream configured, but HEAD has a new commit not yet
    reflected on the (faked) upstream ref."""
    repo = clean_pushed_no_pending_repo
    (repo / "f").write_text("a\nb\nc\n")
    subprocess.run(["git", "add", "f"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "more work"], cwd=repo, check=True)
    return repo


@pytest.fixture
def clean_no_upstream_repo(tmp_path) -> Path:
    """Committed history, clean tree, no remote and no upstream tracking
    configured at all — the state of most feature branches before their
    first push. `@{u}` fails to resolve entirely (not '0 commits ahead')."""
    repo = tmp_path / "no-upstream"
    _init_repo(repo)
    return repo


@pytest.fixture
def repo_with_committed_optin(tmp_path) -> Path:
    """A repo that commits .claude/autonomous-shipping-required itself — the
    exact case this design's machine-anchored precedence exists to defeat.
    Paired with an isolated_home that has NOT set the machine sentinel, this
    must stay silent."""
    repo = tmp_path / "hostile-optin"
    _init_repo(repo)
    (repo / ".claude").mkdir()
    (repo / ".claude" / "autonomous-shipping-required").write_text("# hostile\n")
    subprocess.run(["git", "add", ".claude"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "hostile optin"], cwd=repo, check=True)
    (repo / "f").write_text("a\nb\n")  # dirty, so work-pending would be true
    return repo


def _fire(payload: dict, cwd, home, extra_env: dict | None = None) -> dict | None:
    return run_hook_stop(ADVANCE_HOOK, payload, cwd=cwd, home=home, extra_env=extra_env)


# ------------------------------------------------------------------ #
# Fire-predicate corpus                                               #
# ------------------------------------------------------------------ #

CORPUS: list[tuple[str, bool]] = [
    (ISSUE_QUOTE_QUESTION, True),
    (ISSUE_QUOTE_NONQUESTION, False),  # Row 19: non-question stops are uncaught
    (MODAL_CASE, True),  # "must FIRE — the modal case" per the plan
    (DOCUMENTED_MISS, True),  # accepted miss: exclusion window is final-sentence only
    ("I committed and pushed the change. Want me to profile it?", False),
    (
        'This repo\'s Stop hook fires on phrasing like "want me to commit '
        'this, or review the diff first?" — I mention it here for context. '
        "Anyway, the change is up.",
        False,
    ),  # self-referential mid-message quote; final sentence carries no ask
    ("Should I use approach A or approach B for the caching layer?", False),
    ("Want me to merge this PR now?", False),  # merge excluded
    (
        "I ran the tests and they are failing. Want me to commit anyway?",
        False,
    ),  # "anyway" exclusion
    ("Here is a summary of the changes I made to the parser module.", False),
    # Multi-line final messages: the window is the last sentence of the last non-blank line.
    (
        "Fixed the failing test\nWant me to commit this?",
        True,
    ),  # unpunctuated earlier line carrying an exclusion word; only the last-line step passes it
    (
        "Fixed the failing test.\n\nWant me to commit this?",
        True,
    ),  # capture reads past the first line; the earlier paragraph's exclusion word is outside the window
    (
        "## Summary\n- Fixed the parser\n- Added a regression test\n\nShould I push and open a PR?",
        True,
    ),  # heading and bullet lines before the ask
    ("Want me to commit this?\n", True),  # trailing newline
    (
        "The tests are failing.\nWant me to commit anyway?",
        False,
    ),  # "anyway" in the final sentence
    (
        "Want me to commit this?\n\nHere is the diff summary.",
        False,
    ),  # the last line carries no ask
    (
        "Want me to commit this?\r\n\r\n",
        True,
    ),  # CRLF: the trailing carriage returns are stripped before the line split
    (
        "Want me to commit this?\n  ",
        True,
    ),  # trailing spaces after the last newline: only the whitespace strip exposes the ask line
    ("", False),  # empty final message
    ("  \n \r\n ", False),  # whitespace-only final message
    (
        "Want me to:\n- commit\n- push",
        False,
    ),  # accepted miss: a bulleted ask leaves no verb on the last line ("A line break ends a sentence", commit-stall-block.md)
    (
        "Want me to\ncommit this?",
        False,
    ),  # accepted miss: an ask wrapped across lines leaves no verb on the last line
    (
        "Want me to commit " + "the change and " * 100 + "now?",
        True,
    ),  # a 1-2 KB final sentence with the verb at its head sits inside the scan window
    (
        "Want me to commit " + "the change and " * 200 + "now?",
        False,
    ),  # accepted miss: a final sentence longer than the scan window loses its head verb (commit-stall-block.md)
]


@pytest.mark.parametrize("message,expected_fire", CORPUS)
def test_fire_predicate_corpus(
    armed_home, dirty_repo, message, expected_fire
):
    result = _fire(
        stop_input(
            message,
            session_id="s",
            prompt_id="p1",
            permission_mode="default",
            cwd=str(dirty_repo),
        ),
        cwd=dirty_repo,
        home=armed_home,
    )
    if expected_fire:
        assert result is not None, f"expected a block for: {message!r}"
    else:
        assert result is None, f"expected silence for: {message!r}, got {result!r}"


@pytest.mark.parametrize(
    "message",
    [
        "Fixed one more parser case.\n" * 8000 + "Want me to commit this?",
        "lorem ipsum dolor sit amet " * 4000 + "Want me to commit this?",
        "The build is failing " + "and so on " * 400 + "so want me to commit this?",
    ],
    ids=["200KB-multi-line", "100KB-single-line", "exclusion-word-beyond-the-scan-window"],
)
def test_large_final_message_still_fires(armed_home, dirty_repo, message):
    """Only the message's tail is scanned (the hook's ASK_WINDOW_CHARS), so the third case's early
    exclusion word is out of view. No wall-clock assertion, per flake risk."""
    assert len(message) >= 4_000
    result = _fire(
        stop_input(message, session_id="s", prompt_id="p1", cwd=str(dirty_repo)),
        cwd=dirty_repo,
        home=armed_home,
    )
    assert result is not None


def test_case_folding_lowercase_verb_fires(armed_home, dirty_repo):
    """nocasematch must be active — a lowercase rephrasing of the verb half
    still fires."""
    result = _fire(
        stop_input(
            "ok, want me to COMMIT this now?",
            session_id="s",
            prompt_id="p1",
            cwd=str(dirty_repo),
        ),
        cwd=dirty_repo,
        home=armed_home,
    )
    assert result is not None


# ------------------------------------------------------------------ #
# Loop guard                                                          #
# ------------------------------------------------------------------ #


def test_absent_prompt_id_silent(armed_home, dirty_repo):
    payload = stop_input(
        ISSUE_QUOTE_QUESTION, session_id="s", cwd=str(dirty_repo)
    )
    assert "prompt_id" not in payload
    assert _fire(payload, cwd=dirty_repo, home=armed_home) is None


def test_empty_prompt_id_silent(armed_home, dirty_repo):
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=armed_home,
    )
    assert result is None


def test_bounded_iteration_fires_exactly_once(armed_home, dirty_repo):
    payload = stop_input(
        ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
    )
    results = [_fire(payload, cwd=dirty_repo, home=armed_home) for _ in range(5)]
    fired = [r for r in results if r is not None]
    assert len(fired) == 1, f"expected exactly one fire across 5 identical calls, got {len(fired)}"
    assert results[0] is not None, "the first call must be the one that fires"


def test_new_prompt_id_rearms(armed_home, dirty_repo):
    first = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=armed_home,
    )
    second = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p2", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=armed_home,
    )
    assert first is not None
    assert second is not None, "a new prompt_id must re-arm the guard"


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root bypasses directory permission checks",
)
def test_state_file_write_failure_stays_silent(armed_home, dirty_repo):
    """Write failure (read-only $HOME/.claude) must not block — the hook
    reads its own write back and only emits when it matches."""
    claude_dir = armed_home / ".claude"
    original_mode = claude_dir.stat().st_mode
    claude_dir.chmod(0o555)
    try:
        result = _fire(
            stop_input(
                ISSUE_QUOTE_QUESTION,
                session_id="s",
                prompt_id="p1",
                cwd=str(dirty_repo),
            ),
            cwd=dirty_repo,
            home=armed_home,
        )
    finally:
        claude_dir.chmod(original_mode)
    assert result is None


# ------------------------------------------------------------------ #
# Opt-in matrix                                                       #
# ------------------------------------------------------------------ #


def test_machine_file_absent_silent(isolated_home, dirty_repo):
    """No autonomous-shipping-required sentinel anywhere — must stay silent
    regardless of an otherwise-firing message."""
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=isolated_home,
    )
    assert result is None


def test_machine_file_present_no_optout_fires(armed_home, dirty_repo):
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=armed_home,
    )
    assert result is not None


def test_machine_file_present_repo_optout_silent(armed_home, dirty_repo):
    (dirty_repo / ".claude").mkdir(exist_ok=True)
    (dirty_repo / ".claude" / "autonomous-shipping-optout").write_text("# optout\n")
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=armed_home,
    )
    assert result is None


def test_repo_committed_optin_with_machine_file_absent_stays_silent(
    isolated_home, repo_with_committed_optin
):
    """The redesign's central guarantee: a repo cannot grant this by
    committing anything. Machine sentinel absent, repo commits the
    identically-named file — must stay silent."""
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION,
            session_id="s",
            prompt_id="p1",
            cwd=str(repo_with_committed_optin),
        ),
        cwd=repo_with_committed_optin,
        home=isolated_home,
    )
    assert result is None


def test_config_dir_sentinel_fires(isolated_home, dirty_repo, tmp_path):
    """CLAUDE_CONFIG_DIR relocates the machine sentinel, kill switch, log
    file, and state dir together — all five hardcodes route through the
    same resolved config dir."""
    config_dir = tmp_path / "profile"
    config_dir.mkdir()
    (config_dir / "autonomous-shipping-required").write_text("# machine sentinel\n")
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=isolated_home,
        extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
    )
    assert result is not None


def test_config_dir_kill_switch_disables(isolated_home, dirty_repo, tmp_path):
    config_dir = tmp_path / "profile"
    config_dir.mkdir()
    (config_dir / "autonomous-shipping-required").write_text("# machine sentinel\n")
    (config_dir / ".commit-stall-block-disabled").write_text("")
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=isolated_home,
        extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
    )
    assert result is None


def test_unreadable_config_keys_psv_does_not_fire(armed_home, dirty_repo, tmp_path):
    """A fully-unreadable config-keys.psv still makes this hook silent end
    to end -- but via autonomous_shipping's own sentinel-presence check
    (step 3), not commit_stall_block's own exit-3 arm (step 2), which this
    end-to-end path can never observe. See config-schema-audit.md's
    commit_stall_block section for why, and
    test_commit_stall_block_case_statement_treats_exit_3_as_stays_armed
    below for the isolated regression test of that exit-3 arm itself.
    Mirrors test_lib_config_gates.py's _lib_sh_with_unreadable_schema technique and
    test_restore_authorization_boundary_on_compact.py's own exit-3 test:
    symlink the hook plus _lib.sh/_config.sh into a directory with no
    config-keys.psv sibling, so _config_schema_field sees an absent
    (unreadable) schema file."""
    isolated_hook = hook_beside_degraded_schema(tmp_path, ADVANCE_HOOK)
    # Distinct prompt_ids: STATE_DIR dedup is keyed on session_id+prompt_id
    # and shares the same armed_home across both calls below, so reusing one
    # prompt_id would make the second call's silence ambiguous between
    # "schema unreadable" and "already-seen prompt_id."
    result = _fire(
        stop_input(ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)),
        cwd=dirty_repo,
        home=armed_home,
    )
    isolated_result = run_hook_stop(
        isolated_hook,
        stop_input(ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p2", cwd=str(dirty_repo)),
        cwd=dirty_repo,
        home=armed_home,
    )
    assert result is not None, "sanity check: the unmodified hook must fire under these conditions"
    assert isolated_result is None


def test_commit_stall_block_case_statement_treats_exit_3_as_stays_armed(
    isolated_home, dirty_repo, tmp_path
):
    """Direct regression test for the case "$?" statement itself (advance-
    past-commit-stall.sh:66-70), isolated from autonomous_shipping's own
    gate -- see config-schema-audit.md's commit_stall_block section for why
    an end-to-end reproduction can never observe this key's own exit-3
    direction inside the real hook, and
    test_unreadable_config_keys_psv_does_not_fire above for that end-to-end
    path.

    Drives the real hook script itself, not a hand-copied case statement --
    a reintroduced bug in the hook's own case arm (e.g. folding exit 3 into
    the same branch as exit 1) must fail this test. config-keys.psv is
    absent (commit_stall_block's own _config_enabled call returns exit 3),
    and _lib_autonomous_shipping_sentinel_present is stubbed, after the real
    _lib.sh is sourced, to unconditionally return 0 -- bypassing that
    function's own independent exit-3 masking against the same absent
    schema (see test_unreadable_config_keys_psv_does_not_fire's docstring),
    so the hook's own commit_stall_block case arm is what actually gates
    whether the hook proceeds to fire."""
    isolated_hooks_dir = tmp_path / "isolated-hooks"
    isolated_hooks_dir.mkdir()
    (isolated_hooks_dir / ADVANCE_HOOK.name).symlink_to(ADVANCE_HOOK)
    (isolated_hooks_dir / "_lib.sh.real").symlink_to(HOOKS_DIR / "_lib.sh")
    (isolated_hooks_dir / "_config.sh").symlink_to(HOOKS_DIR / "_config.sh")
    # config-keys.psv deliberately absent, matching the isolation technique
    # used throughout this file and test_config_lib.py.
    (isolated_hooks_dir / "_lib.sh").write_text(
        "#!/bin/bash\n"
        '. "$(dirname "${BASH_SOURCE[0]}")/_lib.sh.real"\n'
        "_lib_autonomous_shipping_sentinel_present() { return 0; }\n"
    )
    result = run_hook_stop(
        isolated_hooks_dir / ADVANCE_HOOK.name,
        stop_input(ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)),
        cwd=dirty_repo,
        home=isolated_home,
    )
    assert result is not None, (
        "exit 3 (config-keys.psv unreadable) must leave commit_stall_block "
        "armed, not fold into exit 1's disabled branch"
    )


def test_commit_stall_block_case_statement_treats_exit_4_as_stays_armed(
    isolated_home, dirty_repo, tmp_path
):
    """Direct regression test for the case "$?" statement itself
    (the one after the commit_stall_block check in advance-past-commit-stall.sh), isolated the same way the exit-3
    test above is: config-keys.psv is readable and non-empty but missing
    commit_stall_block's own row (the interrupted stow-relink/git-pull
    shape, distinct from the wholly-unreadable file above), and
    _lib_autonomous_shipping_sentinel_present is stubbed to unconditionally
    return 0 so the hook's own commit_stall_block case arm is what actually
    gates whether the hook proceeds to fire."""
    isolated_hooks_dir = tmp_path / "isolated-hooks"
    isolated_hooks_dir.mkdir()
    (isolated_hooks_dir / ADVANCE_HOOK.name).symlink_to(ADVANCE_HOOK)
    (isolated_hooks_dir / "_lib.sh.real").symlink_to(HOOKS_DIR / "_lib.sh")
    (isolated_hooks_dir / "_config.sh").symlink_to(HOOKS_DIR / "_config.sh")
    (isolated_hooks_dir / "config-keys.psv").write_text(config_schema_text_without_key("commit_stall_block"))
    (isolated_hooks_dir / "_lib.sh").write_text(
        "#!/bin/bash\n"
        '. "$(dirname "${BASH_SOURCE[0]}")/_lib.sh.real"\n'
        "_lib_autonomous_shipping_sentinel_present() { return 0; }\n"
    )
    result = run_hook_stop(
        isolated_hooks_dir / ADVANCE_HOOK.name,
        stop_input(ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)),
        cwd=dirty_repo,
        home=isolated_home,
    )
    assert result is not None, (
        "exit 4 (config-keys.psv readable but missing commit_stall_block's "
        "own row) must leave commit_stall_block armed, not fold into exit "
        "1's disabled branch"
    )


def test_legacy_home_claude_sentinel_fires_via_fast_path_union(
    armed_home, dirty_repo, tmp_path
):
    """GH-793: the fast-path sentinel check (step 3) must union the
    resolved config dir with the legacy $HOME/.claude location, the same
    way the full _lib_autonomous_shipping_active check at step 9 already
    does. armed_home provides the sentinel only at the legacy
    $HOME/.claude/autonomous-shipping-required location, while
    CLAUDE_CONFIG_DIR points at a directory holding no copy of it, and the
    fast path still fires."""
    empty_config_dir = tmp_path / "empty-profile"
    empty_config_dir.mkdir()
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=armed_home,
        extra_env={"CLAUDE_CONFIG_DIR": str(empty_config_dir)},
    )
    assert result is not None


# ------------------------------------------------------------------ #
# Work-pending predicate                                              #
# ------------------------------------------------------------------ #


def test_dirty_tree_is_pending(armed_home, dirty_repo):
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=armed_home,
    )
    assert result is not None


def test_ahead_of_upstream_is_pending(armed_home, ahead_of_upstream_repo):
    result = _fire(
        stop_input(
            "Want me to push this now, or do you want to review it first?",
            session_id="s",
            prompt_id="p1",
            cwd=str(ahead_of_upstream_repo),
        ),
        cwd=ahead_of_upstream_repo,
        home=armed_home,
    )
    assert result is not None


def test_clean_pushed_no_pr_is_not_pending(armed_home, clean_pushed_no_pending_repo):
    """Post-PR-open state: clean tree, HEAD matches upstream exactly — no
    work pending, must stay silent even though the message would fire."""
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION,
            session_id="s",
            prompt_id="p1",
            cwd=str(clean_pushed_no_pending_repo),
        ),
        cwd=clean_pushed_no_pending_repo,
        home=armed_home,
    )
    assert result is None


def test_no_upstream_configured_clean_tree_is_pending(armed_home, clean_no_upstream_repo):
    """The common pre-first-push state: `@{u}` fails to resolve entirely
    (no upstream ref to diff against), not '0 commits ahead'. A naive
    empty-output-means-not-pending read would silently stay quiet on
    exactly the most common real trigger for this hook — 'I finished the
    change, want me to push it?' on a branch that's never been pushed."""
    result = _fire(
        stop_input(
            "Want me to push this now, or do you want to review it first?",
            session_id="s",
            prompt_id="p1",
            cwd=str(clean_no_upstream_repo),
        ),
        cwd=clean_no_upstream_repo,
        home=armed_home,
    )
    assert result is not None


# ------------------------------------------------------------------ #
# Structural gates                                                    #
# ------------------------------------------------------------------ #


def test_home_unset_silent(dirty_repo, monkeypatch):
    monkeypatch.delenv("HOME", raising=False)
    result = subprocess.run(
        [str(ADVANCE_HOOK)],
        input="{}",
        capture_output=True,
        text=True,
        cwd=dirty_repo,
        env={k: v for k, v in os.environ.items() if k != "HOME"},
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_home_claude_dir_absent_silent(tmp_path, dirty_repo, monkeypatch):
    """Pins the $HOME-only resolution path — CLAUDE_CONFIG_DIR must be unset
    here (unlike isolated_home, this fixture-less $HOME override doesn't
    clear it), or an ambient CLAUDE_CONFIG_DIR on the test machine would
    resolve CONFIG_DIR to a real, populated directory instead of the bare
    sandboxed $HOME this test means to exercise."""
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    bare_home = tmp_path / "bare-home"
    bare_home.mkdir()
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=bare_home,
    )
    assert result is None


def test_relative_config_dir_fails_open(isolated_home, dirty_repo):
    """config-schema-audit.md's commit_stall_block fail-direction claim: a
    resolution failure (relative CLAUDE_CONFIG_DIR, unresolvable per
    _lib_config_dir) must not block a Stop turn, matching the other four
    enforcement-critical keys' equivalent tests (e.g.
    test_restore_authorization_boundary_on_compact.py's
    test_unresolvable_config_dir_exits_0_with_no_output). Hits the hook's
    own `CONFIG_DIR=$(_lib_config_dir) || exit 0` short-circuit before
    commit_stall_block's own schema row is ever read."""
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=isolated_home,
        extra_env={"CLAUDE_CONFIG_DIR": "relative/path"},
    )
    assert result is None


def test_kill_switch_disables(armed_home, dirty_repo):
    (armed_home / ".claude" / ".commit-stall-block-disabled").write_text("")
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=armed_home,
    )
    assert result is None


def test_config_row_true_still_fires_when_the_legacy_kill_switch_file_exists(armed_home, dirty_repo):
    """A commit_stall_block row in claude-config.toml is authoritative, so the legacy file does not disable the hook."""
    (armed_home / ".claude" / "claude-config.toml").write_text("commit_stall_block = true\n")
    (armed_home / ".claude" / ".commit-stall-block-disabled").write_text("")
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=armed_home,
    )
    assert result is not None


def test_config_row_false_disables_when_no_legacy_file_exists(armed_home, dirty_repo):
    (armed_home / ".claude" / "claude-config.toml").write_text("commit_stall_block = false\n")
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=armed_home,
    )
    assert result is None


def test_reason_disable_instruction_disables_the_hook_when_run_beside_a_scaffolded_config_row(
    armed_home, dirty_repo
):
    """The instruction the agent relays must take effect: the config row, not the legacy file."""
    config_toml = armed_home / ".claude" / "claude-config.toml"
    config_toml.write_text("commit_stall_block = true\n")
    first = _fire(
        stop_input(ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)),
        cwd=dirty_repo,
        home=armed_home,
    )
    assert first is not None, "sanity check: the scaffolded `= true` row alone must not disable the hook"
    reason = first["reason"]
    assert ".commit-stall-block-disabled" not in reason
    disable_command = reason.split("To disable this on this machine: ", 1)[1].split(". To disable it for this repo", 1)[0]

    subprocess.run(["bash", "-c", disable_command], check=True)

    assert config_toml.read_text().splitlines()[-1] == "commit_stall_block = false"
    second = _fire(
        stop_input(ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p2", cwd=str(dirty_repo)),
        cwd=dirty_repo,
        home=armed_home,
    )
    assert second is None


def test_denied_root_logs_a_root_denied_line(armed_home, non_repo_dir):
    """A fire-pattern match whose .cwd resolves to no eligible root leaves a log line instead of vanishing silently."""
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(non_repo_dir)
        ),
        cwd=non_repo_dir,
        home=armed_home,
    )
    assert result is None
    log_text = (armed_home / ".claude" / ".commit-stall-block.log").read_text()
    assert "root-denied session=s prompt=p1\n" in log_text


def test_non_matching_turn_with_a_non_repo_cwd_writes_no_root_denied_line(armed_home, non_repo_dir):
    """The root is resolved only after the fire pattern matches, so ordinary turns leave no log line."""
    result = _fire(
        stop_input(
            ISSUE_QUOTE_NONQUESTION, session_id="s", prompt_id="p1", cwd=str(non_repo_dir)
        ),
        cwd=non_repo_dir,
        home=armed_home,
    )
    assert result is None
    log_file = armed_home / ".claude" / ".commit-stall-block.log"
    assert not log_file.exists() or "root-denied" not in log_file.read_text()


def test_cwd_containing_a_newline_fails_closed(armed_home, dirty_repo, tmp_path):
    """The line-oriented read would split the path at the newline and resolve its first line,
    here a symlink to an eligible repo, so a newline in .cwd must read as no cwd at all."""
    (tmp_path / "link").symlink_to(dirty_repo, target_is_directory=True)
    newline_cwd = tmp_path / "link\ninside"
    newline_cwd.mkdir()
    control = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(tmp_path / "link")
        ),
        cwd=dirty_repo,
        home=armed_home,
    )
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p2", cwd=str(newline_cwd)
        ),
        cwd=dirty_repo,
        home=armed_home,
    )
    assert control is not None, "sanity check: the symlinked path alone must fire"
    assert result is None
    log_text = (armed_home / ".claude" / ".commit-stall-block.log").read_text()
    assert "root-denied session=s prompt=p2\n" in log_text


@pytest.mark.parametrize("bad_cwd", [5, {"path": "/tmp"}, ["/tmp"]], ids=["number", "object", "array"])
def test_non_string_cwd_fails_closed(armed_home, dirty_repo, bad_cwd):
    """A non-string .cwd is blanked while the other fields stay readable, so the hook still reaches
    the root check and logs root-denied instead of dropping the whole payload before it."""
    payload = stop_input(ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1")
    payload["cwd"] = bad_cwd
    result = _fire(payload, cwd=dirty_repo, home=armed_home)
    assert result is None
    log_text = (armed_home / ".claude" / ".commit-stall-block.log").read_text()
    assert "root-denied session=s prompt=p1\n" in log_text


@pytest.mark.parametrize("line_break", ["\n", "\u0000\n"], ids=["newline", "nul-then-newline"])
@pytest.mark.parametrize("field", ["session_id", "agent_type", "permission_mode", "prompt_id"])
def test_header_field_containing_a_newline_does_not_shift_the_later_fields(armed_home, dirty_repo, field, line_break):
    """Each value carries the later fields' own values after its line break, so a line-oriented read
    that split it would reassemble a payload that fires. The hook must treat the payload as unreadable.
    The nul-then-newline break pins that a NUL directly before the newline does not hide it from the check."""
    headers = {"session_id": "s", "agent_type": "", "permission_mode": "default", "prompt_id": "p1"}
    header_names = list(headers)
    later_lines = [headers[name] for name in header_names[header_names.index(field) + 1 :]]
    later_lines.append(str(dirty_repo))
    payload = stop_input(ISSUE_QUOTE_QUESTION, cwd=str(dirty_repo), **headers)
    control = _fire({**payload, "prompt_id": "p0"}, cwd=dirty_repo, home=armed_home)
    payload[field] = headers[field] + line_break + "\n".join(later_lines)
    result = _fire(payload, cwd=dirty_repo, home=armed_home)
    assert control is not None, "sanity check: the clean payload must fire"
    assert result is None


@pytest.mark.parametrize("field", ["session_id", "agent_type", "permission_mode", "prompt_id"])
@pytest.mark.parametrize("bad_value", [5, {"id": "s"}], ids=["number", "object"])
def test_non_string_header_field_does_not_fire(armed_home, dirty_repo, field, bad_value):
    """A header field that is not a string empties every field, which gate 5 denies."""
    payload = stop_input(
        ISSUE_QUOTE_QUESTION,
        session_id="s",
        agent_type="",
        permission_mode="default",
        prompt_id="p1",
        cwd=str(dirty_repo),
    )
    control = _fire({**payload, "prompt_id": "p0"}, cwd=dirty_repo, home=armed_home)
    payload[field] = bad_value
    result = _fire(payload, cwd=dirty_repo, home=armed_home)
    assert control is not None, "sanity check: the clean payload must fire"
    assert result is None


def test_agent_type_present_silent(armed_home, dirty_repo):
    """A Stop payload with agent_type set is never force-continued: CLAUDE.md's
    Agent Core tells a subagent to return its work to its dispatcher rather
    than ship."""
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION,
            session_id="s",
            prompt_id="p1",
            agent_type="general-purpose",
            cwd=str(dirty_repo),
        ),
        cwd=dirty_repo,
        home=armed_home,
    )
    assert result is None


def test_plan_mode_silent(armed_home, dirty_repo):
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION,
            session_id="s",
            prompt_id="p1",
            permission_mode="plan",
            cwd=str(dirty_repo),
        ),
        cwd=dirty_repo,
        home=armed_home,
    )
    assert result is None


def test_missing_session_id_silent(armed_home, dirty_repo):
    payload = stop_input(ISSUE_QUOTE_QUESTION, prompt_id="p1", cwd=str(dirty_repo))
    assert "session_id" not in payload
    assert _fire(payload, cwd=dirty_repo, home=armed_home) is None


def test_missing_last_assistant_message_silent(armed_home, dirty_repo):
    payload = {
        "hook_event_name": "Stop",
        "session_id": "s",
        "prompt_id": "p1",
        "cwd": str(dirty_repo),
    }
    assert _fire(payload, cwd=dirty_repo, home=armed_home) is None


def test_null_last_assistant_message_silent(armed_home, dirty_repo):
    payload = {
        "hook_event_name": "Stop",
        "session_id": "s",
        "prompt_id": "p1",
        "cwd": str(dirty_repo),
        "last_assistant_message": None,
    }
    assert _fire(payload, cwd=dirty_repo, home=armed_home) is None


def test_traversal_session_id_denies_and_does_not_touch_marker_dir(armed_home, dirty_repo):
    canary = plant_traversal_canary(armed_home)
    payload = stop_input(
        ISSUE_QUOTE_QUESTION,
        session_id=TRAVERSAL_SESSION_ID,
        prompt_id="p1",
        cwd=str(dirty_repo),
    )
    result = _fire(payload, cwd=dirty_repo, home=armed_home)
    assert result is None, "a traversing session_id must not be treated as valid"
    assert canary.read_text() == CANARY_CONTENT, (
        "hook must not have written through the traversal path"
    )


# ------------------------------------------------------------------ #
# Layer-2 equivalents (hand-written — turn-gate hooks inherit none of        #
# test_hook_alignment.py's GATE_HOOKS auto-parametrization)                  #
# ------------------------------------------------------------------ #


def _path_without(binary: str, tmp_path: Path) -> str:
    """A PATH string mirroring the real PATH via a symlink farm, with
    `binary` omitted. Farm construction (full-mirror rationale, dedup,
    unreadable-dir handling) lives in `helpers.build_path_without`, shared
    with `test_hook_alignment.py`'s own session-memoized fixture."""
    farm_dir = tmp_path / f"path-without-{binary}"
    farm_dir.mkdir()
    return build_path_without(binary, farm_dir)


def test_jq_absent_exits_zero_empty_stdout(armed_home, dirty_repo, tmp_path):
    result = subprocess.run(
        [str(ADVANCE_HOOK)],
        input=stop_input_json(ISSUE_QUOTE_QUESTION, "s", "p1", str(dirty_repo)),
        capture_output=True,
        text=True,
        cwd=dirty_repo,
        env={**os.environ, "HOME": str(armed_home), "PATH": _path_without("jq", tmp_path)},
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def _fake_jq_failing_on_flag(fake_bin: Path, flag: str) -> str:
    """A `jq` shim that delegates to the real binary except when invoked
    with `flag`, where it exits 1 with no output — isolates a failure to one
    specific call shape rather than removing jq from PATH entirely."""
    real_jq = shutil.which("jq")
    if not real_jq:
        pytest.skip("jq not found in PATH")
    shim = fake_bin / "jq"
    shim.write_text(
        "#!/bin/bash\n"
        f'for arg in "$@"; do [ "$arg" = "{flag}" ] && exit 1; done\n'
        f'exec "{real_jq}" "$@"\n'
    )
    shim.chmod(0o755)
    return f"{fake_bin}:{os.environ['PATH']}"


def test_reason_encode_failure_degrades_to_silent_exit(armed_home, dirty_repo, tmp_path):
    """The six-field read (:87, `_lib_jq -r`) succeeds normally; the
    reason-encode call (:220, `_lib_jq -Rs .`) fails. This pins that the
    guard around that printf degrades to silent-allow instead of emitting
    the malformed `{"decision":"block","reason":}` a bare printf would
    produce. test_jq_absent_exits_zero_empty_stdout above removes jq from
    PATH entirely, which exits at the earlier six-field-read gate and never
    reaches this line at all.
    """
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    path = _fake_jq_failing_on_flag(fake_bin, "-Rs")
    result = subprocess.run(
        [str(ADVANCE_HOOK)],
        input=stop_input_json(ISSUE_QUOTE_QUESTION, "s", "p1", str(dirty_repo)),
        capture_output=True,
        text=True,
        cwd=dirty_repo,
        env={**os.environ, "HOME": str(armed_home), "PATH": path},
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_git_absent_silent(armed_home, dirty_repo, tmp_path):
    result = subprocess.run(
        [str(ADVANCE_HOOK)],
        input=stop_input_json(ISSUE_QUOTE_QUESTION, "s", "p1", str(dirty_repo)),
        capture_output=True,
        text=True,
        cwd=dirty_repo,
        env={**os.environ, "HOME": str(armed_home), "PATH": _path_without("git", tmp_path)},
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_missing_lib_sh_silent(armed_home, dirty_repo):
    """Turn-gate inversion of GATE_HOOKS's test_missing_lib_sh_denied: a
    gate hook denies when _lib.sh is absent, but this fail-open hook must
    exit 0 with no output instead — a hard failure here would turn an
    infrastructure gap into a stuck turn.

    The hook is COPIED (not symlinked) into a temp directory so that
    dirname($0) resolves to the temp dir, where _lib.sh is genuinely absent.

    Same caveat test_missing_lib_sh_denied documents for itself: this cannot
    structurally distinguish "exited silently because _lib.sh's own guard
    fired" from "exited silently because a downstream `_lib_*` call, now
    undefined, failed and an unrelated `|| exit 0` caught it." Both hooks'
    fail-open-everywhere design makes every downstream gate an equally valid
    exit path for this input shape — this test pins the observable behavior
    (silent exit under a missing _lib.sh), not the specific guard line.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_hook = Path(tmpdir) / ADVANCE_HOOK.name
        shutil.copy2(ADVANCE_HOOK, tmp_hook)
        tmp_hook.chmod(0o755)
        result = subprocess.run(
            [str(tmp_hook)],
            input=stop_input_json(ISSUE_QUOTE_QUESTION, "s", "p1", str(dirty_repo)),
            capture_output=True,
            text=True,
            cwd=dirty_repo,
            env={**os.environ, "HOME": str(armed_home)},
            check=False,
        )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_malformed_json_stdin_silent(armed_home, dirty_repo):
    """Non-JSON stdin must exit 0 silently, not just a well-formed payload
    missing one field (test_missing_session_id_silent exercises that
    narrower case). The six-field _lib_jq read swallows a jq parse failure
    via 2>/dev/null into empty reads — this pins that the empty-read path
    still reaches a silent exit rather than tripping on unset variables."""
    result = subprocess.run(
        [str(ADVANCE_HOOK)],
        input="not json",
        capture_output=True,
        text=True,
        cwd=dirty_repo,
        env={**os.environ, "HOME": str(armed_home)},
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def stop_input_json(message: str, session_id: str, prompt_id: str, cwd: str) -> str:
    return json.dumps(
        stop_input(message, session_id=session_id, prompt_id=prompt_id, cwd=cwd)
    )


# ------------------------------------------------------------------ #
# Emitted JSON shape                                                  #
# ------------------------------------------------------------------ #


def test_emitted_payload_is_exactly_decision_and_reason(armed_home, dirty_repo):
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=armed_home,
    )
    assert result is not None
    # run_hook_stop already asserts the exact {"decision", "reason"} key
    # pair and decision == "block"; this pins the reason's content contract.
    assert isinstance(result["reason"], str)
    assert "/code-review" in result["reason"]
    assert "/ready-for-review" in result["reason"]


def test_reason_opens_with_the_shared_shipping_statement(armed_home, dirty_repo, shipping_statement):
    """Single-source pin: the reason holds the helper's output byte for byte, so text drifting from the helper's fails."""
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=armed_home,
    )
    assert result is not None
    assert result["reason"].startswith(shipping_statement(dirty_repo) + " ")
    assert "gh pr merge" not in result["reason"]
    assert "/code-review" in result["reason"]
    assert "/ready-for-review" in result["reason"]


def test_reason_tail_conditions_the_continue_directive_on_the_statement(armed_home, dirty_repo):
    """A closing question can itself be the hold check, so the directive must not be unconditional.
    The statement owns the conditions, so the tail defers to it."""
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=armed_home,
    )
    assert result is not None
    assert "If the statement above lets you ship this work, continue now:" in result["reason"]


# ------------------------------------------------------------------ #
# State-dir sweep (30-day eviction, run on every fire)                #
# ------------------------------------------------------------------ #


def _touch_with_age(path: Path, days_old: int) -> None:
    path.write_text("p1\n")
    stamp = time.time() - (days_old * 86400)
    os.utime(path, (stamp, stamp))


def test_sweep_deletes_entries_older_than_30_days(armed_home, dirty_repo):
    state_dir = armed_home / ".claude" / ".commit-stall-block.d"
    state_dir.mkdir(parents=True)
    stale = state_dir / "old-session"
    _touch_with_age(stale, days_old=31)

    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=armed_home,
    )

    assert result is not None
    assert not stale.exists(), "entries older than 30 days must be swept"


def test_sweep_preserves_entries_within_30_days(armed_home, dirty_repo):
    state_dir = armed_home / ".claude" / ".commit-stall-block.d"
    state_dir.mkdir(parents=True)
    fresh = state_dir / "recent-session"
    _touch_with_age(fresh, days_old=1)

    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=armed_home,
    )

    assert result is not None, (
        "the sweep only runs on an actual fire — without this assertion the "
        "test stays green even if the hook stopped firing altogether"
    )
    assert fresh.exists(), "entries within 30 days must survive the sweep"


def test_sweep_skipped_when_state_dir_is_a_symlink(armed_home, dirty_repo, tmp_path):
    """A symlinked state dir must not be followed by the sweep — the
    `[ ! -L ]` guard skips the sweep entirely rather than deleting the
    symlink itself once its own mtime ages past 30 days."""
    real_target = tmp_path / "elsewhere"
    real_target.mkdir()

    state_dir = armed_home / ".claude" / ".commit-stall-block.d"
    state_dir.parent.mkdir(parents=True, exist_ok=True)
    state_dir.symlink_to(real_target)
    stale_mtime = time.time() - (31 * 86400)
    os.utime(state_dir, (stale_mtime, stale_mtime), follow_symlinks=False)

    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=armed_home,
    )

    assert result is not None
    assert state_dir.is_symlink(), (
        "the [ ! -L ] guard must skip a symlinked state dir with an aged "
        "mtime, not delete the symlink itself"
    )
