"""Tests for advance-past-commit-stall.sh (GH-526).

The hook fires only when ALL hold: no kill switch, main session (not a
subagent), valid session_id, not plan mode, a fresh prompt_id, the final
sentence of the last assistant message asks permission to commit/push/open a
PR without also carrying an exclusion phrase, the payload's cwd resolves to a
root the root guard (_lib_autonomous_shipping_root_for_cwd) accepts, work is
pending (dirty tree or HEAD ahead of its upstream), and HEAD is on a
non-default branch. Every other path must
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
    AUTONOMOUS_SHIPPING_STATEMENT_LEAD,
    CANARY_CONTENT,
    HOOKS_DIR,
    TRAVERSAL_SESSION_ID,
    assert_ignored_by_repo_gitignore,
    build_path_without,
    check_ignore,
    config_schema_text_without_key,
    git_isolation_env,
    init_git_repo_with_commit,
    install_recording_shims,
    plant_traversal_canary,
    recorded_git_argvs,
    recorded_git_subcommands,
    run_hook_stop,
    set_origin_head,
    stop_input,
)

ADVANCE_HOOK = HOOKS_DIR / "advance-past-commit-stall.sh"

# Verbatim from issue #526 — the issue's own quotes. The first is exactly 64
# characters. A byte-count tail slice (${msg: -600}) returns EMPTY for any
# message shorter than 600 chars, so this exact quote pins final-sentence
# extraction.
ISSUE_QUOTE_QUESTION = (
    "Want me to commit this, or do you want to review the diff first?"
)
ISSUE_QUOTE_NONQUESTION = "Per your standing instruction, I haven't committed."
assert len(ISSUE_QUOTE_QUESTION) == 64, "pin drifted from the issue's own 64-char quote"

MODAL_CASE = (
    "Fixed the failing test in the parser. "
    "Want me to commit this, or do you want to review the diff first?"
)
DOCUMENTED_MISS = (
    "The push failed with a non-fast-forward error. "
    "Want me to push again after rebasing?"
)


# Not a default-branch name, and not "feature", which clean_pushed_no_pending_repo checks out.
# The hook stays silent on the default branch, so the fixtures build on a branch where it can fire.
NON_DEFAULT_BRANCH = "shipping-work"


def _init_repo(repo: Path, branch: str = NON_DEFAULT_BRANCH) -> None:
    init_git_repo_with_commit(repo, branch=branch, file_name="f", content="a\n")


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


def _fire(payload: dict, cwd, home, extra_env: dict | None = None) -> dict | None:
    return run_hook_stop(ADVANCE_HOOK, payload, cwd=cwd, home=home, extra_env=extra_env)


# ------------------------------------------------------------------ #
# Fire-predicate corpus                                               #
# ------------------------------------------------------------------ #

CORPUS: list[tuple[str, bool]] = [
    (ISSUE_QUOTE_QUESTION, True),
    (ISSUE_QUOTE_NONQUESTION, False),  # non-question stops are uncaught
    (MODAL_CASE, True),
    (DOCUMENTED_MISS, True),  # accepted miss: exclusion window is final-sentence only
    ("I committed and pushed the change. Want me to profile it?", False),
    (
        'This repo\'s Stop hook fires on phrasing like "want me to commit '
        'this, or review the diff first?" — I mention it here for context. '
        "Anyway, the change is up.",
        False,
    ),  # self-referential mid-message quote; final sentence carries no ask
    ("Should I use approach A or approach B for the caching layer?", False),
    ("Want me to merge this PR now?", False),  # no commit or push verb, so it never matches FIRE_RE
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
    # Each row matches FIRE_RE through a commit or push verb and carries exactly one EXCLUDE_RE alternative,
    # so it fails once that alternative is dropped.
    ("Want me to commit this and merge it?", False),  # merge
    ("Want me to push this with --force?", False),  # --force
    ("Want me to commit this and force-push it?", False),  # force-push
    ("Want me to commit this after a reset --hard?", False),  # reset --hard
    ("Want me to push this and close the PR?", False),  # close the PR
    ("Want me to push this and delete the branch?", False),  # delete the branch
    ("Want me to commit the fix for the failing test?", False),  # failing
    ("Want me to commit this even though the build failed?", False),  # failed
    ("Want me to commit the fix for this error?", False),  # error
    ("Want me to push this while the release is blocked?", False),  # blocked
    # Fire-side twin of each exclusion row above, in the same order: the same ask without the excluded token
    # blocks, so an exclusion row cannot pass through a FIRE_RE non-match alone.
    ("Want me to commit this and review it?", True),  # merge twin
    ("Want me to push this?", True),  # --force twin
    ("Want me to commit this and push it?", True),  # force-push twin
    ("Want me to commit this after a rebase?", True),  # reset --hard twin
    ("Want me to push this and review it?", True),  # close the PR twin
    ("Want me to push this and keep the branch?", True),  # delete the branch twin
    ("Want me to commit the fix for the passing test?", True),  # failing twin
    ("Want me to commit this now that the build passed?", True),  # failed twin
    ("Want me to commit the fix for this typo?", True),  # error twin
    ("Want me to push this while the release is pending?", True),  # blocked twin
    # Known gap: OBJECT_RE has no word boundaries, so an object token inside another word or phrase fires when
    # the final sentence also carries a permission verb (commit-stall-block.md).
    ("Should I push back on the spec change?", True),  # known gap: "push back", phrase-level
    ("Want me to raise the commitment question with the team?", True),  # known gap: "commitment", word-boundary
    ("Should I add push notification support next?", True),  # known gap: "push notification", phrase-level
    ("Let me know if the committee wants a different layout.", True),  # known gap: "committee", word-boundary
    ("Want me to open a project?", True),  # known gap: "open a PR" matches inside "open a project", word-boundary
    # Known gap: "create a PR" matches inside "create a profile page", word-boundary.
    ("Want me to create a profile page?", True),
    # Known gap: VERB_RE "should I" has no trailing word boundary, so "should include" matches.
    ("The fix should include a commit.", True),
    # Real stalls: the inflected stems "committing" and "pushing" fire only through the bare-substring match, so a
    # word-boundary fix to OBJECT_RE must stay stem-aware.
    ("Want me to start committing this?", True),
    ("Shall I start pushing this?", True),
    # Real stalls whose only object is a PR alternative, so a word-boundary fix to OBJECT_RE must keep them firing.
    ("Should I open a PR?", True),
    ("Want me to open the PR?", True),
    ("Want me to create a PR?", True),
    # Known gap: EXCLUDE_RE has no word boundaries either, so an exclusion token inside another word, or a whole
    # word in an unrelated sense, silences a real stall (commit-stall-block.md).
    # Known gap: real stall silenced by a whole-word, unrelated-sense exclusion match (error).
    ("Want me to commit the error handling changes?", False),
    ("Want me to push the emergency fix?", False),  # known gap: real stall silenced by in-word exclusion match (merge)
    # Known gap: real stall silenced by an in-word exclusion match (blocked in "unblocked").
    ("Want me to commit this now that CI is unblocked?", False),
]


@pytest.mark.parametrize("message,expected_fire", CORPUS)
def test_fire_predicate_corpus(
    isolated_home, dirty_repo, message, expected_fire
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
        home=isolated_home,
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
def test_large_final_message_still_fires(isolated_home, dirty_repo, message):
    """Only the message's tail is scanned (the hook's ASK_WINDOW_CHARS), so the third case's early
    exclusion word is out of view. No wall-clock assertion, per flake risk."""
    assert len(message) >= 4_000
    result = _fire(
        stop_input(message, session_id="s", prompt_id="p1", cwd=str(dirty_repo)),
        cwd=dirty_repo,
        home=isolated_home,
    )
    assert result is not None


def test_case_folding_lowercase_verb_fires(isolated_home, dirty_repo):
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
        home=isolated_home,
    )
    assert result is not None


# ------------------------------------------------------------------ #
# Loop guard                                                          #
# ------------------------------------------------------------------ #


def test_absent_prompt_id_silent(isolated_home, dirty_repo):
    payload = stop_input(
        ISSUE_QUOTE_QUESTION, session_id="s", cwd=str(dirty_repo)
    )
    assert "prompt_id" not in payload
    assert _fire(payload, cwd=dirty_repo, home=isolated_home) is None


def test_empty_prompt_id_silent(isolated_home, dirty_repo):
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=isolated_home,
    )
    assert result is None


def test_bounded_iteration_fires_exactly_once(isolated_home, dirty_repo):
    payload = stop_input(
        ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
    )
    results = [_fire(payload, cwd=dirty_repo, home=isolated_home) for _ in range(5)]
    fired = [r for r in results if r is not None]
    assert len(fired) == 1, f"expected exactly one fire across 5 identical calls, got {len(fired)}"
    assert results[0] is not None, "the first call must be the one that fires"


def test_new_prompt_id_rearms(isolated_home, dirty_repo):
    first = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=isolated_home,
    )
    second = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p2", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=isolated_home,
    )
    assert first is not None
    assert second is not None, "a new prompt_id must re-arm the guard"


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root bypasses directory permission checks",
)
def test_state_file_write_failure_stays_silent(isolated_home, dirty_repo):
    """Write failure (read-only $HOME/.claude) must not block — the hook
    reads its own write back and only emits when it matches."""
    claude_dir = isolated_home / ".claude"
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
            home=isolated_home,
        )
    finally:
        claude_dir.chmod(original_mode)
    assert result is None


# ------------------------------------------------------------------ #
# Configuration: no shipping switch exists                            #
# ------------------------------------------------------------------ #


def test_fires_with_no_shipping_configuration_at_all(isolated_home, dirty_repo):
    """The hook consults no shipping switch, so a bare home with no config file fires."""
    assert not (isolated_home / ".claude" / "claude-config.toml").exists()
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=isolated_home,
    )
    assert result is not None


def test_fires_under_a_relocated_config_dir(isolated_home, dirty_repo, tmp_path):
    """CLAUDE_CONFIG_DIR relocates the kill switch, log file, and state dir
    together — all route through the same resolved config dir."""
    config_dir = tmp_path / "profile"
    config_dir.mkdir()
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=isolated_home,
        extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
    )
    assert result is not None
    assert (config_dir / ".commit-stall-block.log").exists()
    assert (config_dir / ".commit-stall-block.d" / "s").exists()
    default_config_dir = isolated_home / ".claude"
    assert not (default_config_dir / ".commit-stall-block.log").exists()
    assert not (default_config_dir / ".commit-stall-block.d").exists()


def test_config_dir_kill_switch_disables(isolated_home, dirty_repo, tmp_path):
    config_dir = tmp_path / "profile"
    config_dir.mkdir()
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


def test_known_gap_explicit_false_is_ignored_when_schema_unreadable_exit_3(
    isolated_home, dirty_repo, tmp_path
):
    """Known-gap characterization: an explicit `commit_stall_block = false` is not
    honored on a degraded schema, a limitation docs/commit-stall-block.md lists.
    Honoring it there is a deliberate behavior change that flips this test.

    Direct regression test for the case "$?" statement itself (advance-
    past-commit-stall.sh step 2). See config-schema-audit.md's
    commit_stall_block section for the fail-direction rationale.

    Drives the real hook script itself, not a hand-copied case statement --
    a reintroduced bug in the hook's own case arm (e.g. folding exit 3 into
    the same branch as exit 1) must fail this test. config-keys.psv is
    absent, so commit_stall_block's own _config_enabled call returns exit 3
    and the explicit `false` row planted below is never read."""
    (isolated_home / ".claude" / "claude-config.toml").write_text("commit_stall_block = false\n")
    isolated_hooks_dir = tmp_path / "isolated-hooks"
    isolated_hooks_dir.mkdir()
    (isolated_hooks_dir / ADVANCE_HOOK.name).symlink_to(ADVANCE_HOOK)
    (isolated_hooks_dir / "_lib.sh").symlink_to(HOOKS_DIR / "_lib.sh")
    (isolated_hooks_dir / "_config.sh").symlink_to(HOOKS_DIR / "_config.sh")
    # config-keys.psv deliberately absent, matching the isolation technique
    # used throughout this file and test_config_lib.py.
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


def test_known_gap_explicit_false_is_ignored_when_schema_row_missing_exit_4(
    isolated_home, dirty_repo, tmp_path
):
    """Known-gap characterization: an explicit `commit_stall_block = false` is not
    honored on a degraded schema, a limitation docs/commit-stall-block.md lists.
    Honoring it there is a deliberate behavior change that flips this test.

    Direct regression test for the case "$?" statement itself
    (the one after the commit_stall_block check in advance-past-commit-stall.sh), isolated the same way the exit-3
    test above is: config-keys.psv is readable and non-empty but missing
    commit_stall_block's own row (the interrupted stow-relink/git-pull
    shape, distinct from the wholly-unreadable file above), so the explicit
    `false` row planted below is not honored."""
    (isolated_home / ".claude" / "claude-config.toml").write_text("commit_stall_block = false\n")
    isolated_hooks_dir = tmp_path / "isolated-hooks"
    isolated_hooks_dir.mkdir()
    (isolated_hooks_dir / ADVANCE_HOOK.name).symlink_to(ADVANCE_HOOK)
    (isolated_hooks_dir / "_lib.sh").symlink_to(HOOKS_DIR / "_lib.sh")
    (isolated_hooks_dir / "_config.sh").symlink_to(HOOKS_DIR / "_config.sh")
    (isolated_hooks_dir / "config-keys.psv").write_text(config_schema_text_without_key("commit_stall_block"))
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


# ------------------------------------------------------------------ #
# Work-pending predicate                                              #
# ------------------------------------------------------------------ #


def test_dirty_tree_is_pending(isolated_home, dirty_repo):
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=isolated_home,
    )
    assert result is not None


def test_ahead_of_upstream_is_pending(isolated_home, ahead_of_upstream_repo):
    result = _fire(
        stop_input(
            "Want me to push this now, or do you want to review it first?",
            session_id="s",
            prompt_id="p1",
            cwd=str(ahead_of_upstream_repo),
        ),
        cwd=ahead_of_upstream_repo,
        home=isolated_home,
    )
    assert result is not None


def test_clean_pushed_no_pr_is_not_pending(isolated_home, clean_pushed_no_pending_repo):
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
        home=isolated_home,
    )
    assert result is None


def test_no_upstream_configured_clean_tree_is_pending(isolated_home, clean_no_upstream_repo):
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
        home=isolated_home,
    )
    assert result is not None


def test_pruned_upstream_ref_clean_tree_is_pending(isolated_home, clean_pushed_no_pending_repo):
    """The configured upstream's remote-tracking ref is gone (after a merge plus
    `fetch --prune`), so `@{u}` fails to resolve although tracking config remains."""
    subprocess.run(
        ["git", "update-ref", "-d", "refs/remotes/origin/feature"],
        cwd=clean_pushed_no_pending_repo,
        check=True,
    )
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION,
            session_id="s",
            prompt_id="p1",
            cwd=str(clean_pushed_no_pending_repo),
        ),
        cwd=clean_pushed_no_pending_repo,
        home=isolated_home,
    )
    assert result is not None


def test_killed_upstream_comparison_clean_tree_is_pending(
    isolated_home, clean_pushed_no_pending_repo, git_timeout_shim
):
    """A `rev-list` that fails outright (status 124, the `timeout` kill status)
    on an otherwise clean, fully pushed branch counts as pending work."""
    env = git_timeout_shim('[ "$3" = "rev-list" ]', exit_status=124)
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION,
            session_id="s",
            prompt_id="p1",
            cwd=str(clean_pushed_no_pending_repo),
        ),
        cwd=clean_pushed_no_pending_repo,
        home=isolated_home,
        extra_env=env,
    )
    assert result is not None


# ------------------------------------------------------------------ #
# Branch predicate: the default branch and a detached HEAD stay silent #
# ------------------------------------------------------------------ #


def _fire_on_dirty_repo(isolated_home, repo: Path, **extra) -> dict | None:
    """The repo is dirty, so work is pending and only the branch decides."""
    (repo / "f").write_text("a\nb\n")
    return _fire(
        stop_input(ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(repo)),
        cwd=repo,
        home=isolated_home,
        **extra,
    )


def test_default_branch_main_with_no_origin_is_silent(isolated_home, tmp_path):
    """No origin/HEAD resolves, so the fallback names apply and `main` is the default."""
    repo = tmp_path / "main-no-origin"
    _init_repo(repo, branch="main")

    assert _fire_on_dirty_repo(isolated_home, repo) is None


def test_branch_named_by_origin_head_is_silent(isolated_home, tmp_path):
    repo = tmp_path / "trunk-default"
    _init_repo(repo, branch="trunk")
    set_origin_head(repo, "trunk")

    assert _fire_on_dirty_repo(isolated_home, repo) is None


def test_detached_head_is_silent(isolated_home, tmp_path):
    repo = tmp_path / "detached"
    _init_repo(repo)
    subprocess.run(["git", "checkout", "-q", "--detach"], cwd=repo, check=True)

    assert _fire_on_dirty_repo(isolated_home, repo) is None


def test_head_read_failure_on_a_non_default_branch_stays_silent(isolated_home, tmp_path, git_timeout_shim):
    """Only the `symbolic-ref -q` read fails (status 128), so a git failure must not read as a
    non-default branch and force a continuation."""
    repo = tmp_path / "head-read-fails"
    _init_repo(repo)
    env = git_timeout_shim('[ "$3" = "symbolic-ref" ] && [ "$4" = "-q" ]', exit_status=128)

    assert _fire_on_dirty_repo(isolated_home, repo, extra_env=env) is None


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
    _lib_config_dir) must not block a Stop turn, matching the other three
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


def test_kill_switch_disables(isolated_home, dirty_repo):
    (isolated_home / ".claude" / ".commit-stall-block-disabled").write_text("")
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=isolated_home,
    )
    assert result is None


def test_config_row_true_still_fires_when_the_legacy_kill_switch_file_exists(isolated_home, dirty_repo):
    """A commit_stall_block row in claude-config.toml is authoritative, so the legacy file does not disable the hook."""
    (isolated_home / ".claude" / "claude-config.toml").write_text("commit_stall_block = true\n")
    (isolated_home / ".claude" / ".commit-stall-block-disabled").write_text("")
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=isolated_home,
    )
    assert result is not None


def test_config_row_false_disables_when_no_legacy_file_exists(isolated_home, dirty_repo):
    (isolated_home / ".claude" / "claude-config.toml").write_text("commit_stall_block = false\n")
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=isolated_home,
    )
    assert result is None


def test_reason_disable_instruction_disables_the_hook_when_run_beside_a_scaffolded_config_row(
    isolated_home, dirty_repo
):
    """The instruction the agent relays must take effect: the config row, not the legacy file."""
    config_toml = isolated_home / ".claude" / "claude-config.toml"
    config_toml.write_text("commit_stall_block = true\n")
    first = _fire(
        stop_input(ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)),
        cwd=dirty_repo,
        home=isolated_home,
    )
    assert first is not None, "sanity check: the scaffolded `= true` row alone must not disable the hook"
    reason = first["reason"]
    assert ".commit-stall-block-disabled" not in reason
    disable_command = reason.split("To disable this on this machine: ", 1)[1]

    subprocess.run(["bash", "-c", disable_command], check=True)

    assert config_toml.read_text().splitlines()[-1] == "commit_stall_block = false"
    second = _fire(
        stop_input(ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p2", cwd=str(dirty_repo)),
        cwd=dirty_repo,
        home=isolated_home,
    )
    assert second is None


def test_denied_root_logs_a_root_denied_line(isolated_home, non_repo_dir):
    """A fire-pattern match whose .cwd resolves to no eligible root leaves a log line instead of vanishing silently."""
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(non_repo_dir)
        ),
        cwd=non_repo_dir,
        home=isolated_home,
    )
    assert result is None
    log_text = (isolated_home / ".claude" / ".commit-stall-block.log").read_text()
    assert "root-denied session=s prompt=p1\n" in log_text


def test_non_matching_turn_with_a_non_repo_cwd_writes_no_root_denied_line(isolated_home, non_repo_dir):
    """The root is resolved only after the fire pattern matches, so ordinary turns leave no log line."""
    result = _fire(
        stop_input(
            ISSUE_QUOTE_NONQUESTION, session_id="s", prompt_id="p1", cwd=str(non_repo_dir)
        ),
        cwd=non_repo_dir,
        home=isolated_home,
    )
    assert result is None
    log_file = isolated_home / ".claude" / ".commit-stall-block.log"
    assert not log_file.exists() or "root-denied" not in log_file.read_text()


def test_cwd_containing_a_newline_fails_closed(isolated_home, dirty_repo, tmp_path):
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
        home=isolated_home,
    )
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p2", cwd=str(newline_cwd)
        ),
        cwd=dirty_repo,
        home=isolated_home,
    )
    assert control is not None, "sanity check: the symlinked path alone must fire"
    assert result is None
    log_text = (isolated_home / ".claude" / ".commit-stall-block.log").read_text()
    assert "root-denied session=s prompt=p2\n" in log_text


@pytest.mark.parametrize("bad_cwd", [5, {"path": "/tmp"}, ["/tmp"]], ids=["number", "object", "array"])
def test_non_string_cwd_fails_closed(isolated_home, dirty_repo, bad_cwd):
    """A non-string .cwd is blanked while the other fields stay readable, so the hook still reaches
    the root check and logs root-denied instead of dropping the whole payload before it."""
    payload = stop_input(ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1")
    payload["cwd"] = bad_cwd
    result = _fire(payload, cwd=dirty_repo, home=isolated_home)
    assert result is None
    log_text = (isolated_home / ".claude" / ".commit-stall-block.log").read_text()
    assert "root-denied session=s prompt=p1\n" in log_text


@pytest.mark.parametrize("line_break", ["\n", "\u0000\n"], ids=["newline", "nul-then-newline"])
@pytest.mark.parametrize("field", ["session_id", "agent_type", "permission_mode", "prompt_id"])
def test_header_field_containing_a_newline_does_not_shift_the_later_fields(isolated_home, dirty_repo, field, line_break):
    """Each value carries the later fields' own values after its line break, so a line-oriented read
    that split it would reassemble a payload that fires. The hook must treat the payload as unreadable.
    The nul-then-newline break pins that a NUL directly before the newline does not hide it from the check."""
    headers = {"session_id": "s", "agent_type": "", "permission_mode": "default", "prompt_id": "p1"}
    header_names = list(headers)
    later_lines = [headers[name] for name in header_names[header_names.index(field) + 1 :]]
    later_lines.append(str(dirty_repo))
    payload = stop_input(ISSUE_QUOTE_QUESTION, cwd=str(dirty_repo), **headers)
    control = _fire({**payload, "prompt_id": "p0"}, cwd=dirty_repo, home=isolated_home)
    payload[field] = headers[field] + line_break + "\n".join(later_lines)
    result = _fire(payload, cwd=dirty_repo, home=isolated_home)
    assert control is not None, "sanity check: the clean payload must fire"
    assert result is None


@pytest.mark.parametrize("field", ["session_id", "agent_type", "permission_mode", "prompt_id"])
@pytest.mark.parametrize("bad_value", [5, {"id": "s"}], ids=["number", "object"])
def test_non_string_header_field_does_not_fire(isolated_home, dirty_repo, field, bad_value):
    """A header field that is not a string empties every field, which gate 4 denies."""
    payload = stop_input(
        ISSUE_QUOTE_QUESTION,
        session_id="s",
        agent_type="",
        permission_mode="default",
        prompt_id="p1",
        cwd=str(dirty_repo),
    )
    control = _fire({**payload, "prompt_id": "p0"}, cwd=dirty_repo, home=isolated_home)
    payload[field] = bad_value
    result = _fire(payload, cwd=dirty_repo, home=isolated_home)
    assert control is not None, "sanity check: the clean payload must fire"
    assert result is None


def test_agent_type_present_silent(isolated_home, dirty_repo):
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
        home=isolated_home,
    )
    assert result is None


def test_plan_mode_silent(isolated_home, dirty_repo):
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION,
            session_id="s",
            prompt_id="p1",
            permission_mode="plan",
            cwd=str(dirty_repo),
        ),
        cwd=dirty_repo,
        home=isolated_home,
    )
    assert result is None


@pytest.mark.parametrize("permission_mode", ["acceptEdits", "bypassPermissions", "dontAsk", "auto"])
def test_non_plan_permission_modes_fire(isolated_home, dirty_repo, permission_mode):
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION,
            session_id="s",
            prompt_id="p1",
            permission_mode=permission_mode,
            cwd=str(dirty_repo),
        ),
        cwd=dirty_repo,
        home=isolated_home,
    )
    assert result is not None


def test_missing_session_id_silent(isolated_home, dirty_repo):
    payload = stop_input(ISSUE_QUOTE_QUESTION, prompt_id="p1", cwd=str(dirty_repo))
    assert "session_id" not in payload
    assert _fire(payload, cwd=dirty_repo, home=isolated_home) is None


def test_missing_last_assistant_message_silent(isolated_home, dirty_repo):
    payload = {
        "hook_event_name": "Stop",
        "session_id": "s",
        "prompt_id": "p1",
        "cwd": str(dirty_repo),
    }
    assert _fire(payload, cwd=dirty_repo, home=isolated_home) is None


def test_null_last_assistant_message_silent(isolated_home, dirty_repo):
    payload = {
        "hook_event_name": "Stop",
        "session_id": "s",
        "prompt_id": "p1",
        "cwd": str(dirty_repo),
        "last_assistant_message": None,
    }
    assert _fire(payload, cwd=dirty_repo, home=isolated_home) is None


def test_traversal_session_id_denies_and_does_not_touch_marker_dir(isolated_home, dirty_repo):
    canary = plant_traversal_canary(isolated_home)
    payload = stop_input(
        ISSUE_QUOTE_QUESTION,
        session_id=TRAVERSAL_SESSION_ID,
        prompt_id="p1",
        cwd=str(dirty_repo),
    )
    result = _fire(payload, cwd=dirty_repo, home=isolated_home)
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


def test_jq_absent_exits_zero_empty_stdout(isolated_home, dirty_repo, tmp_path):
    result = subprocess.run(
        [str(ADVANCE_HOOK)],
        input=stop_input_json(ISSUE_QUOTE_QUESTION, "s", "p1", str(dirty_repo)),
        capture_output=True,
        text=True,
        cwd=dirty_repo,
        env={**os.environ, "HOME": str(isolated_home), "PATH": _path_without("jq", tmp_path)},
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


def test_reason_encode_failure_degrades_to_silent_exit(isolated_home, dirty_repo, tmp_path):
    """The six-field read (`_lib_jq -r`) succeeds normally; the
    reason-encode call (`_lib_jq -Rs .`) fails. This pins that the
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
        env={**os.environ, "HOME": str(isolated_home), "PATH": path},
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_git_absent_silent(isolated_home, dirty_repo, tmp_path):
    result = subprocess.run(
        [str(ADVANCE_HOOK)],
        input=stop_input_json(ISSUE_QUOTE_QUESTION, "s", "p1", str(dirty_repo)),
        capture_output=True,
        text=True,
        cwd=dirty_repo,
        env={**os.environ, "HOME": str(isolated_home), "PATH": _path_without("git", tmp_path)},
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_missing_lib_sh_silent(isolated_home, dirty_repo):
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
            env={**os.environ, "HOME": str(isolated_home)},
            check=False,
        )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_malformed_json_stdin_silent(isolated_home, dirty_repo):
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
        env={**os.environ, "HOME": str(isolated_home)},
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


def test_emitted_payload_is_exactly_decision_and_reason(isolated_home, dirty_repo):
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=isolated_home,
    )
    assert result is not None
    # run_hook_stop already asserts the exact {"decision", "reason"} key
    # pair and decision == "block"; this pins the reason's content contract.
    assert isinstance(result["reason"], str)
    assert result["reason"].startswith(
        "Do not ask the engineer for permission to continue at a natural stopping point, "
        "unless CLAUDE.md, the skill you are following, or a hold the engineer stated requires the question."
    )


def test_reason_carries_no_shipping_statement(isolated_home, dirty_repo):
    """The shipping statement reaches the agent through the compact hook, so the reason does not restate it."""
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=isolated_home,
    )
    assert result is not None
    assert AUTONOMOUS_SHIPPING_STATEMENT_LEAD not in result["reason"]
    assert "gh pr merge" not in result["reason"]


def test_reason_ends_the_turn_when_a_required_question_applies(isolated_home, dirty_repo):
    """A closing question can itself be the hold check, so the directive must not be unconditional."""
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=isolated_home,
    )
    assert result is not None
    assert "If one does, end your turn." in result["reason"]
    assert (
        "Otherwise continue with your task's next step, say the task is complete, or say what is blocked."
        in result["reason"]
    )


@pytest.mark.parametrize(
    "question",
    [
        "Want me to commit this and deploy it?",
        "Want me to commit this and push it to main?",
        "Want me to force push this branch?",
        "Want me to push this with -f?",
    ],
    ids=["deploy", "push-to-main", "force-push-spelled-with-a-space", "force-push-spelled-with-dash-f"],
)
def test_known_gap_question_targeting_an_action_outside_the_grant_still_fires_with_the_carve_out(
    isolated_home, dirty_repo, question
):
    """Known-gap characterization: a fix that extends EXCLUDE_RE or reads the question's target
    flips these cases on purpose (docs/commit-stall-block.md lists each as a limitation).

    The fire predicate does not read what the question targets or how it spells a force push
    (EXCLUDE_RE holds `--force` and `force-push` only), so the reason's carve-out is its only
    instruction routing such a question back, and it defers to CLAUDE.md for the routing.
    Whether the agent follows it is not measured."""
    result = _fire(
        stop_input(question, session_id="s", prompt_id="p1", cwd=str(dirty_repo)),
        cwd=dirty_repo,
        home=isolated_home,
    )
    assert result is not None
    assert (
        "unless CLAUDE.md, the skill you are following, or a hold the engineer stated requires the question."
        in result["reason"]
    )


# ------------------------------------------------------------------ #
# Process-spawn budget                                                #
# ------------------------------------------------------------------ #


def test_final_sentence_matching_no_fire_pattern_spawns_one_jq_and_no_git(isolated_home, dirty_repo, tmp_path):
    """The six-field read is the only spawn on the common no-fire path, so git runs only after the fire regex matches."""
    shimmed_path, invocation_log = install_recording_shims(tmp_path)
    result = _fire(
        stop_input("Done.", session_id="s", prompt_id="p1", cwd=str(dirty_repo)),
        cwd=dirty_repo,
        home=isolated_home,
        extra_env={"PATH": shimmed_path},
    )
    assert result is None
    assert invocation_log.read_text().split() == ["jq"]


def test_shims_record_git_and_jq_spawns_when_the_hook_fires(isolated_home, dirty_repo, tmp_path):
    """Positive control: proves the shims detect spawns, so the no-fire run's lone jq entry means no git ran."""
    shimmed_path, invocation_log = install_recording_shims(tmp_path)
    result = _fire(
        stop_input(ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)),
        cwd=dirty_repo,
        home=isolated_home,
        extra_env={"PATH": shimmed_path},
    )
    assert result is not None
    assert set(invocation_log.read_text().split()) == {"git", "jq"}


def _head_on_default_branch(repo: Path) -> None:
    _init_repo(repo, branch="main")


def _head_detached(repo: Path) -> None:
    _init_repo(repo)
    subprocess.run(["git", "checkout", "-q", "--detach"], cwd=repo, check=True)


@pytest.mark.parametrize(
    "prepare_repo", [_head_on_default_branch, _head_detached], ids=["default-branch", "detached-head"]
)
def test_head_on_the_default_branch_or_detached_runs_no_git_status(
    isolated_home, tmp_path, prepare_repo
):
    """The HEAD read precedes the work-pending check, so a silent HEAD state never pays for `git status`."""
    repo = tmp_path / "silent-head"
    prepare_repo(repo)
    shimmed_path, invocation_log = install_recording_shims(tmp_path)

    result = _fire_on_dirty_repo(isolated_home, repo, extra_env={"PATH": shimmed_path})

    assert result is None
    git_subcommands = recorded_git_subcommands(invocation_log)
    assert "symbolic-ref" in git_subcommands, "sanity check: the HEAD read must reach the git shim"
    assert "status" not in git_subcommands


def test_firing_run_on_a_non_default_branch_with_work_pending_runs_git_status(
    isolated_home, dirty_repo, tmp_path
):
    """Positive control: proves the shims record the `status` subcommand, so its absence above means it never ran."""
    shimmed_path, invocation_log = install_recording_shims(tmp_path)
    result = _fire(
        stop_input(ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)),
        cwd=dirty_repo,
        home=isolated_home,
        extra_env={"PATH": shimmed_path},
    )
    assert result is not None
    assert "status" in recorded_git_subcommands(invocation_log)


def test_work_pending_status_call_skips_optional_locks(isolated_home, dirty_repo, tmp_path):
    """`--no-optional-locks` keeps the hook's `git status` from taking the index lock a concurrent git command may need."""
    shimmed_path, invocation_log = install_recording_shims(tmp_path)
    result = _fire(
        stop_input(ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)),
        cwd=dirty_repo,
        home=isolated_home,
        extra_env={"PATH": shimmed_path},
    )
    assert result is not None
    status_argvs = [argv for argv in recorded_git_argvs(invocation_log) if "status" in argv]
    assert status_argvs, "sanity check: the firing run must reach the git shim with a status call"
    assert all("--no-optional-locks" in argv for argv in status_argvs)


# ------------------------------------------------------------------ #
# State stays out of git                                              #
# ------------------------------------------------------------------ #


def _files_under(directory: Path) -> set[Path]:
    return {path.relative_to(directory) for path in directory.rglob("*") if path.is_file()}


def test_state_written_by_a_firing_run_is_gitignored(isolated_home, dirty_repo, tmp_path):
    """Under stow directory-fold the config dir is the checkout's claude/.claude, so every file the hook
    writes there must be ignored. git check-ignore matches a directory-only rule against a file inside it."""
    config_dir = isolated_home / ".claude"
    files_before = _files_under(config_dir)
    result = _fire(
        stop_input(ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)),
        cwd=dirty_repo,
        home=isolated_home,
    )
    assert result is not None
    created = _files_under(config_dir) - files_before
    assert {Path(".commit-stall-block.log"), Path(".commit-stall-block.d") / "s"} <= created
    git_env = {**os.environ, **git_isolation_env(tmp_path)}
    for created_file in created:
        assert_ignored_by_repo_gitignore(check_ignore(git_env, f"claude/.claude/{created_file}"))


def test_the_ignore_rules_do_not_reach_a_bare_name(tmp_path):
    git_env = {**os.environ, **git_isolation_env(tmp_path)}
    assert check_ignore(git_env, "claude/.claude/.commit-stall-block").returncode == 1


# ------------------------------------------------------------------ #
# State-dir sweep (30-day eviction, run on every fire)                #
# ------------------------------------------------------------------ #


def _touch_with_age(path: Path, days_old: int) -> None:
    path.write_text("p1\n")
    stamp = time.time() - (days_old * 86400)
    os.utime(path, (stamp, stamp))


def test_sweep_deletes_entries_older_than_30_days(isolated_home, dirty_repo):
    state_dir = isolated_home / ".claude" / ".commit-stall-block.d"
    state_dir.mkdir(parents=True)
    stale = state_dir / "old-session"
    _touch_with_age(stale, days_old=31)

    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=isolated_home,
    )

    assert result is not None
    assert not stale.exists(), "entries older than 30 days must be swept"


def test_sweep_preserves_entries_within_30_days(isolated_home, dirty_repo):
    state_dir = isolated_home / ".claude" / ".commit-stall-block.d"
    state_dir.mkdir(parents=True)
    fresh = state_dir / "recent-session"
    _touch_with_age(fresh, days_old=1)

    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=isolated_home,
    )

    assert result is not None, (
        "the sweep only runs on an actual fire — without this assertion the "
        "test stays green even if the hook stopped firing altogether"
    )
    assert fresh.exists(), "entries within 30 days must survive the sweep"


def test_sweep_skipped_when_state_dir_is_a_symlink(isolated_home, dirty_repo, tmp_path):
    """A symlinked state dir must not be followed by the sweep — the
    `[ ! -L ]` guard skips the sweep entirely rather than deleting the
    symlink itself once its own mtime ages past 30 days."""
    real_target = tmp_path / "elsewhere"
    real_target.mkdir()

    state_dir = isolated_home / ".claude" / ".commit-stall-block.d"
    state_dir.parent.mkdir(parents=True, exist_ok=True)
    state_dir.symlink_to(real_target)
    stale_mtime = time.time() - (31 * 86400)
    os.utime(state_dir, (stale_mtime, stale_mtime), follow_symlinks=False)

    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=isolated_home,
    )

    assert result is not None
    assert state_dir.is_symlink(), (
        "the [ ! -L ] guard must skip a symlinked state dir with an aged "
        "mtime, not delete the symlink itself"
    )
