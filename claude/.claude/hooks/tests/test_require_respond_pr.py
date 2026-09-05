"""Tests for require-respond-pr.sh."""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import time

import pytest
from helpers import (
    HOOKS_DIR,
    SKILLS_DIR,
    assert_gate_handles_traversal_session_id,
    bash_input,
    build_path_without,
    edit_input,
    extract_skill_command,
    head_sha,
    run_hook,
    run_hook_reason,
    run_skill_command,
    write_review_pr_completion_marker,
)

from .conftest import _seed_session

RESPOND_PR_HOOK = HOOKS_DIR / "require-respond-pr.sh"
RESPOND_PR_SKILL = SKILLS_DIR / "respond-pr" / "SKILL.md"
ERROR_MODE_SKILL = SKILLS_DIR / "error-mode-analysis" / "SKILL.md"

# GH-483's fenced-code-block reply commands all use this exact `-F body=`
# shape (see respond-pr/SKILL.md steps 7 and the Attribution/Guidelines
# sections).
_FENCED_BLOCK_RE = re.compile(r"```(?:bash)?\n(.*?)```", re.DOTALL)


@pytest.fixture
def current_repo_foo_bar(tmp_path):
    """Git repo whose origin is https://github.com/foo/bar.git.

    Most respond-pr tests target `foo/bar` in the command URL. The
    cross-repo bypass compares COMMAND_REPO against the current git origin,
    so we need the current repo to also be `foo/bar` for the gate to fire
    as expected on same-repo commands.
    """
    repo = tmp_path / "foo-bar-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/foo/bar.git"],
        cwd=repo,
        check=True,
    )
    return repo


@pytest.fixture
def current_repo_no_origin(tmp_path):
    """Git repo with no `origin` remote configured.

    `git config --get remote.origin.url` returns empty for this repo, so
    CURRENT_URL is empty. The cross-repo release block is guarded on
    CURRENT_URL being non-empty, so it is skipped entirely here -- every
    gated shape denies unconditionally, including reads that explicitly
    target a different repo.
    """
    repo = tmp_path / "no-origin-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    return repo


class TestRequireRespondPr:
    @pytest.mark.parametrize(
        "command",
        [
            "gh api repos/foo/bar/pulls/5/comments",
            "gh api repos/foo/bar/pulls/5/reviews",
            "gh api repos/foo/bar/issues/5/comments",
            "gh pr comment 5 --body test",
            "gh pr review 5 --approve",
            "gh api repos/foo/bar/pulls/5/comments -F body=hi",
            "gh api repos/foo/bar/pulls/comments/12345 -X PATCH -F body=oops",
            "gh api repos/foo/bar/issues/comments/12345 -X PATCH -F body=oops",
            "gh api repos/foo/bar/pulls/comments/12345",
            "gh api repos/foo/bar/pulls/comments/12345 -X DELETE",
        ],
    )
    def test_matching_commands_denied(self, isolated_home, current_repo_foo_bar, command):
        assert run_hook(RESPOND_PR_HOOK, bash_input(command), cwd=current_repo_foo_bar) == "deny"

    @pytest.mark.parametrize(
        "command",
        [
            "gh pr view 5",
            "gh pr list",
            "gh api user",
            "gh pr checkout 5",
            "echo foo",
            "git status",
            "gh api repos/foo/bar/pulls/5",
            "gh api repos/foo/bar/pulls/comments",
            "gh api repos/foo/bar/contents/comments/12345",
        ],
    )
    def test_non_matching_commands_allowed(self, isolated_home, current_repo_foo_bar, command):
        assert run_hook(RESPOND_PR_HOOK, bash_input(command), cwd=current_repo_foo_bar) == "allow"

    def test_awk_absent_from_path_denies(self, isolated_home, current_repo_foo_bar, tmp_path):
        """GH-801: status-2 propagation. COMMAND_FLAT's awk fork failing
        must not silently fall through to "no arm matched, allow" -- this
        gate's own documented posture is fail-closed, and the prior
        parameter-expansion form's "this form cannot fail" guarantee is
        exactly what the checked awk fork narrows. Asserts the
        distinguishing reason text, not just the verdict, so this test
        cannot be satisfied by an ordinary gated-write deny reaching
        "deny" for the wrong reason."""
        farm_dir = tmp_path / "path-without-awk"
        farm_dir.mkdir()
        restricted_path = build_path_without("awk", farm_dir)
        reason = run_hook_reason(
            RESPOND_PR_HOOK,
            bash_input("gh pr view 5"),
            cwd=current_repo_foo_bar,
            extra_env={"PATH": restricted_path},
        )
        assert reason is not None
        assert "could not flatten" in reason

    # -- `gh issue comment` gating -------------------------------------------
    # `gh issue comment` posts through the same POST /repos/{o}/{r}/issues/{n}/
    # comments endpoint `gh pr comment` reaches, and --edit-last/--delete-last
    # rewrite or remove an already-posted body -- all writes, so all deny.

    @pytest.mark.parametrize(
        "command",
        [
            "gh issue comment 5 --body hi",
            "gh issue comment 5 --edit-last --body hi",
            "gh issue comment 5 --delete-last",
            # -R doesn't matter: writes are denied for every repo, not only
            # the current one, so the cross-repo bypass (reads only) never
            # applies here.
            "gh issue comment -R other/repo 5 --body hi",
        ],
    )
    def test_gh_issue_comment_write_forms_denied(
        self, isolated_home, current_repo_foo_bar, command
    ):
        assert run_hook(RESPOND_PR_HOOK, bash_input(command), cwd=current_repo_foo_bar) == "deny"

    @pytest.mark.parametrize(
        "command",
        [
            "gh issue list",
            "gh issue view 5",
            "gh issue create --title x --body y",
        ],
    )
    def test_gh_issue_non_comment_subcommands_allowed(
        self, isolated_home, current_repo_foo_bar, command
    ):
        """Pins the `gh issue` arm to `comment` specifically -- the other
        `gh issue` subcommands never touch a comment body and must stay
        allowed, bounding PATTERN_ISSUE_WRITE_CMD from the other side."""
        assert run_hook(RESPOND_PR_HOOK, bash_input(command), cwd=current_repo_foo_bar) == "allow"

    # -- Flags interposed between the subcommand root and the write verb -----
    # `-R`/`--repo` is an inherited flag on `gh pr` and `gh issue`, so it is
    # valid syntax to place it before the write verb rather than after it:
    # `gh pr --repo o/r comment 5` resolves to the same command as
    # `gh pr comment 5 --repo o/r`. Both orderings must gate identically.
    #
    # These deny for every repo, same or cross: the cross-repo release is
    # reads-only, so naming another repo never turns a write into a read.

    @pytest.mark.parametrize(
        "command",
        [
            "gh pr --repo other/repo comment 5 --body hi",
            "gh pr -R other/repo comment 5 --body hi",
            "gh pr --repo=other/repo comment 5 --body hi",
            "gh pr --repo other/repo review 5 --approve",
            "gh issue --repo other/repo comment 5 --body hi",
            "gh issue -R other/repo comment 5 --body hi",
            # Same repo as origin -- nothing about the flag's position may
            # release the gate's own current-repo case.
            "gh pr -R foo/bar comment 5 --body hi",
            "gh issue -R foo/bar comment 5 --body hi",
            # Root position: ahead of the `pr`/`issue` token, not just ahead
            # of the verb. gh dispatches the write from here too.
            "gh --repo other/repo pr comment 5 --body hi",
            "gh -R other/repo pr comment 5 --body hi",
            "gh --repo other/repo issue comment 5 --body hi",
            "gh -R foo/bar pr review 5 --approve",
            # Glued short-flag value, no separator at all.
            "gh pr -Rother/repo comment 5 --body hi",
            "gh -Rother/repo pr comment 5 --body hi",
            "gh issue -Rfoo/bar comment 5 --body hi",
        ],
    )
    def test_interposed_flag_before_write_verb_denied(
        self, isolated_home, current_repo_foo_bar, command
    ):
        assert run_hook(RESPOND_PR_HOOK, bash_input(command), cwd=current_repo_foo_bar) == "deny"

    @pytest.mark.parametrize(
        "command",
        [
            "gh pr --repo other/repo view 5",
            "gh pr -R other/repo list",
            "gh issue --repo other/repo list",
        ],
    )
    def test_interposed_flag_before_read_verb_allowed(
        self, isolated_home, current_repo_foo_bar, command
    ):
        """Interposing the flag must not by itself trip the write arm.

        Holds at any tolerance width, including none -- these carry no
        `comment`/`review` token to reach. Kept as the read-side companion to
        the deny group above, not as a bound on how wide the span is; the two
        groups below are what pin the width.
        """
        assert run_hook(RESPOND_PR_HOOK, bash_input(command), cwd=current_repo_foo_bar) == "allow"

    @pytest.mark.parametrize(
        "command",
        [
            "gh pr list --search comment is:open",
            "gh pr list --search review",
            "gh issue list --search comment",
        ],
    )
    def test_flag_tolerance_does_not_span_a_positional(
        self, isolated_home, current_repo_foo_bar, command
    ):
        """Bounds the tolerated span from above.

        Widening it to arbitrary text -- e.g. the `[^|&;]*` the `api` arms use
        -- flips every one of these to deny, since each carries the bare word
        `comment` or `review` after a non-flag positional.
        """
        assert run_hook(RESPOND_PR_HOOK, bash_input(command), cwd=current_repo_foo_bar) == "allow"

    @pytest.mark.parametrize(
        "command",
        [
            "gh pr -R review view 5",
            "gh pr --repo comment view 5",
            "gh issue -R comment view 5",
        ],
    )
    def test_flag_value_equal_to_write_verb_allowed(
        self, isolated_home, current_repo_foo_bar, command
    ):
        """Bounds the tolerated span from below -- the flag's value is required.

        With the value optional, the engine may decline to consume it and match
        the value itself as the write verb, denying these reads. Nothing
        security-relevant escapes either way (an over-deny is the safe
        direction), but a gate that blocks ordinary reads gets worked around.
        """
        assert run_hook(RESPOND_PR_HOOK, bash_input(command), cwd=current_repo_foo_bar) == "allow"

    # -- Case-insensitive mutating-method matching ---------------------------
    # `gh` normalizes -X/--method before sending, so a lowercase or
    # mixed-case spelling issues the same real write as the uppercase form
    # test_matching_commands_denied already covers (-X PATCH, -X DELETE).
    #
    # Same-repo commands below deny via the unconditional same-repo fallback
    # at the bottom of the script regardless of whether the case fold works
    # -- the cross-repo release block only fires when COMMAND_REPO differs
    # from the current repo, never true here. They exercise the shape but do
    # not turn on the fold; keep them as a readable sanity check.

    @pytest.mark.parametrize(
        "command",
        [
            "gh api repos/foo/bar/pulls/comments/12345 -X delete",
            "gh api repos/foo/bar/pulls/comments/12345 --method patch -F body=x",
            "gh api repos/foo/bar/pulls/comments/12345 -X DeLeTe",
        ],
    )
    def test_mutating_method_same_repo_denied_regardless_of_case(
        self, isolated_home, current_repo_foo_bar, command
    ):
        assert run_hook(RESPOND_PR_HOOK, bash_input(command), cwd=current_repo_foo_bar) == "deny"

    @pytest.mark.parametrize(
        "command",
        [
            "gh api repos/other/repo/pulls/comments/12345 -X delete",
            "gh api repos/other/repo/pulls/comments/12345 --method patch",
            "gh api repos/other/repo/pulls/comments/12345 -X DeLeTe",
        ],
    )
    def test_mutating_method_cross_repo_denied_regardless_of_case(
        self, isolated_home, current_repo_foo_bar, command
    ):
        """Load-bearing case for the case fold: a cross-repo target only
        denies here because `shopt -s nocasematch` makes the lowercase or
        mixed-case -X/--method value trip GATED_WRITE before the cross-repo
        release block runs. Without the fold, PATTERN_MUTATING_METHOD misses
        the lowercase spelling, GATED_WRITE never trips, and the command
        reads as a plain cross-repo GET -- which the release block allows.
        The same-repo sibling above denies either way, via the unconditional
        same-repo fallback, so it does not exercise the fold at all. No
        other write signal (no -f/-F/--field flag) is present, so the
        fold is what the deny turns on -- ablation-verified against a
        scratch copy of the hook with `nocasematch` stripped: that copy
        allows all three of these while the shipped hook denies them."""
        assert run_hook(RESPOND_PR_HOOK, bash_input(command), cwd=current_repo_foo_bar) == "deny"

    # -- Line-continuation flattening (REST) ---------------------------------
    # grep/`[[ =~ ]]` never cross a line boundary, so before COMMAND_FLAT
    # existed, a REST URL wrapped mid-path slipped every arm. The existing
    # multi-line coverage elsewhere in this file uses two *separate*
    # commands on adjacent lines, which grep handles regardless of
    # flattening -- it doesn't pin this fix. These do, by wrapping a single
    # command's URL itself.

    @pytest.mark.parametrize(
        "command",
        [
            # Continuation at an argument boundary.
            "gh api \\\n  repos/foo/bar/pulls/1/comments",
            # Continuation mid-path, late.
            "gh api repos/foo/bar/pulls/1/\\\ncomments",
            # Continuation mid-path, earlier.
            "gh api repos/foo/bar/\\\npulls/1/comments",
        ],
    )
    def test_wrapped_rest_url_across_backslash_continuation_denied(
        self, isolated_home, current_repo_foo_bar, command
    ):
        assert run_hook(RESPOND_PR_HOOK, bash_input(command), cwd=current_repo_foo_bar) == "deny"

    def test_unrelated_commands_on_separate_lines_both_allowed(
        self, isolated_home, current_repo_foo_bar
    ):
        """Flattening must not over-fuse: two genuinely unrelated commands
        that each touch nothing gated stay allowed even joined by a bare
        newline."""
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input("echo hello\necho world"),
                cwd=current_repo_foo_bar,
            )
            == "allow"
        )

    # -- Bypass-marker behavior --------------------------------------------
    # Marker layout: ~/.claude/.respond-pr-active.d/<session_id>. The skill
    # writes one file per session at entry; the hook checks the file matching
    # THIS request's session_id (from JSON payload). Per-session keying
    # prevents two failure modes the prior singleton path had:
    #   (1) Cleanup thrash — session A's `rm -f` deleting B's marker.
    #   (2) Bypass leak — session A's marker silently authorizing B's
    #       gh calls even though B never ran /respond-pr.

    @pytest.mark.parametrize(
        "command",
        [
            "gh api repos/foo/bar/pulls/5/comments",
            "gh pr comment 5 --body test",
            "gh api repos/foo/bar/pulls/comments/12345 -X PATCH -F body=oops",
            "gh api repos/foo/bar/issues/comments/12345 -X PATCH -F body=oops",
        ],
    )
    def test_fresh_bypass_marker_allows(self, isolated_home, current_repo_foo_bar, command):
        sid = "test-session-fresh"
        marker_dir = isolated_home / ".claude" / ".respond-pr-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).write_text(str(os.getpid()))
        assert (
            run_hook(RESPOND_PR_HOOK, bash_input(command, session_id=sid), cwd=current_repo_foo_bar)
            == "allow"
        )

    def test_active_marker_hit_advances_mtime(self, isolated_home, current_repo_foo_bar):
        """The hook is wired to the touch-refreshing wrapper, not the bare
        liveness predicate -- a live-but-idle-window-aged marker's mtime must
        advance on a gate hit, or a reverted call site would pass every
        allow/deny assertion in this file silently."""
        sid = "session-active-touch"
        marker_dir = isolated_home / ".claude" / ".respond-pr-active.d"
        marker_dir.mkdir(parents=True)
        marker = marker_dir / sid
        marker.write_text(str(os.getpid()))
        old_time = time.time() - 300  # in-window, but old enough to detect a refresh
        os.utime(marker, (old_time, old_time))
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input("gh api repos/foo/bar/pulls/5/comments", session_id=sid),
                cwd=current_repo_foo_bar,
            )
            == "allow"
        )
        assert marker.stat().st_mtime > old_time + 1, (
            "a gate hit against a live marker must refresh its mtime"
        )

    def test_dead_pid_bypass_marker_evicts_and_denies(self, isolated_home, current_repo_foo_bar):
        """Orphaned marker with a dead PID is evicted and the gate denies."""
        sid = "test-session-dead-pid"
        marker_dir = isolated_home / ".claude" / ".respond-pr-active.d"
        marker_dir.mkdir(parents=True)
        marker = marker_dir / sid
        marker.write_text("99999999")  # PID outside Linux/macOS max range → always dead
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input("gh api repos/foo/bar/pulls/5/comments", session_id=sid),
                cwd=current_repo_foo_bar,
            )
            == "deny"
        )
        assert not marker.exists(), "hook must evict the orphan marker on dead PID"

    def test_other_sessions_marker_does_not_leak_bypass(self, isolated_home, current_repo_foo_bar):
        """Regression: a marker for session A must NOT bypass session B's
        gated calls. This is the silent failure mode of the prior singleton
        design — while any session held the marker, every other session's
        matching gh calls bypassed the gate."""
        marker_dir = isolated_home / ".claude" / ".respond-pr-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / "session-A").touch()
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input("gh api repos/foo/bar/pulls/5/comments", session_id="session-B"),
                cwd=current_repo_foo_bar,
            )
            == "deny"
        )

    def test_no_session_id_in_input_denies(self, isolated_home, current_repo_foo_bar):
        """Without session_id in the hook payload, bypass is impossible —
        deny even if a marker file exists in the dir."""
        marker_dir = isolated_home / ".claude" / ".respond-pr-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / "any-session").touch()
        # bash_input() with session_id=None omits the field entirely.
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input("gh api repos/foo/bar/pulls/5/comments"),
                cwd=current_repo_foo_bar,
            )
            == "deny"
        )

    def test_marker_dir_missing_denies(self, isolated_home, current_repo_foo_bar):
        """Sessions dir entirely absent (e.g., capture-session-id.sh never
        ran) → deny. The skill is responsible for failing loudly in that
        case; the hook just denies safely."""
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input("gh api repos/foo/bar/pulls/5/comments", session_id="some-session"),
                cwd=current_repo_foo_bar,
            )
            == "deny"
        )

    def test_traversal_session_id_denies_and_does_not_touch_marker_dir(
        self, isolated_home, current_repo_foo_bar
    ):
        """A session_id of '../canary' must not read through the traversal:
        MARKER concatenates it into .respond-pr-active.d/../canary, which
        resolves to a file one level up ($HOME/.claude/canary). The invalid
        id must skip the bypass entirely and fall through to the normal
        arm-matching gate, which denies this command on its own merits."""
        assert_gate_handles_traversal_session_id(
            RESPOND_PR_HOOK,
            lambda sid: bash_input("gh api repos/foo/bar/pulls/5/comments", session_id=sid),
            isolated_home,
            expected_decision="deny",
            cwd=current_repo_foo_bar,
        )

    def test_alive_pid_bypass_marker_allows(self, isolated_home, current_repo_foo_bar):
        """Active marker with a live PID bypasses the gate for any session duration."""
        sid = "long-run-session"
        marker_dir = isolated_home / ".claude" / ".respond-pr-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).write_text(str(os.getpid()))
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input("gh api repos/foo/bar/pulls/5/comments", session_id=sid),
                cwd=current_repo_foo_bar,
            )
            == "allow"
        )

    def test_non_bash_tool_allowed(self, isolated_home):
        assert run_hook(RESPOND_PR_HOOK, edit_input("/tmp/foo.txt")) == "allow"

    # -- Cross-repo bypass --------------------------------------------------
    # Regression: the gate originally fired on any `(pulls|issues)/N/...`
    # URL regardless of repo, which false-positived on cross-repo research
    # reads like `gh api repos/anthropics/claude-code/issues/12962/comments`
    # from inside an unrelated project.

    @pytest.mark.parametrize(
        "command",
        [
            "gh api repos/other/repo/pulls/5/comments",
            "gh api repos/other/repo/pulls/5/reviews",
            "gh api repos/other/repo/issues/5/comments",
            # Single-comment URL shape, the arm the numbered-URL cases above
            # do not reach. Its deny cases all carry a write flag, so without
            # this the arm has no read-side assertion and a future change to
            # the cross-repo extraction could start denying external reads
            # through it unnoticed.
            "gh api repos/other/repo/pulls/comments/12345",
            "gh api repos/other/repo/issues/comments/12345",
        ],
    )
    def test_cross_repo_reads_allowed(self, isolated_home, current_repo_foo_bar, command):
        assert run_hook(RESPOND_PR_HOOK, bash_input(command), cwd=current_repo_foo_bar) == "allow"

    @pytest.mark.parametrize(
        "command",
        [
            "gh api repos/other/repo/issues/5/comments --input reply.json",
            "gh api repos/other/repo/pulls/5/comments --input=reply.json",
            "gh api repos/other/repo/issues/comments/12345 --input reply.json",
        ],
    )
    def test_cross_repo_file_sourced_rest_body_denied(
        self, isolated_home, current_repo_foo_bar, command
    ):
        """Isolates the file-sourced-body write signal on a REST endpoint.

        A body read from a file is a body whatever the endpoint, but there is
        no `-X` and no field flag here, so this shape reads as a fieldless GET
        to every other signal. Re-scoping the signal to `graphql` alone flips
        all three of these to allow -- verified by ablation, not assumed.
        """
        assert run_hook(RESPOND_PR_HOOK, bash_input(command), cwd=current_repo_foo_bar) == "deny"

    # The bypass releases reads only. Research on an external repo is a read; a
    # write to one still owes the [Claude Code] attribution prefix, because that
    # disclosure is owed to readers of any public PR rather than only to readers
    # of this repo's.
    @pytest.mark.parametrize(
        "command",
        [
            "gh pr comment 5 -R other/repo --body test",
            "gh pr review 5 --repo other/repo --approve",
            "gh pr comment 5 --repo=other/repo --body test",
            "gh api repos/other/repo/pulls/comments/12345 -X PATCH -F body=hi",
            "gh api repos/other/repo/issues/comments/12345 -X PATCH -F body=hi",
            # Long-form method and the `=` field form, neither exercised above.
            "gh api repos/other/repo/issues/5/comments --method POST -f body=hi",
            "gh api repos/other/repo/issues/5/comments --field=body=hi",
            # `--raw-field`, the fourth PATTERN_FIELD_FLAG spelling -- -f, -F,
            # and --field are each covered above, --raw-field was not.
            "gh api repos/other/repo/issues/5/comments --raw-field body=hi",
        ],
    )
    def test_cross_repo_writes_denied(self, isolated_home, current_repo_foo_bar, command):
        assert run_hook(RESPOND_PR_HOOK, bash_input(command), cwd=current_repo_foo_bar) == "deny"

    # These cases were originally written to prove a decoy repo token does not
    # release a write. It doesn't -- but not because the decoy is neutralized:
    # every case below trips a write signal (GATED_WRITE), which denies and
    # `exit 0`s before COMMAND_REPO is ever extracted. Write detection and the
    # cross-repo bypass are mutually exclusive in the hook's control flow, so
    # no single command can exercise both, and a test genuinely pinning
    # "a decoy repo token does not release a write" is not constructible. The
    # decoy tokens (-R other/repo, repos/other/repo/...) are left in place
    # deliberately -- they cannot be what produces the deny, since GATED_WRITE
    # trips regardless of whether they're present. What this test actually
    # pins is the invariant that does hold: write detection runs before, and
    # takes precedence over, the cross-repo bypass.
    @pytest.mark.parametrize(
        "command",
        [
            # Cross-repo read on one line, same-repo write on the next.
            "gh api repos/other/repo/pulls/1/comments\n"
            'gh pr comment 5 --body "unattributed"',
            "gh api repos/other/repo/issues/9/comments\n"
            "gh api repos/foo/bar/pulls/comments/999 -X PATCH -F body=over",
            # Decoy inside a GraphQL mutation's own comment body.
            "gh api graphql -f query='mutation { addComment(input: "
            '{subjectId: "PR_kwABC", body: "see -R other/repo for context"}) '
            "{ clientMutationId } }'",
            "gh api graphql -f query='mutation { addComment(input: "
            '{subjectId: "PR_kwABC", body: "cf repos/other/repo/pulls/3/comments"}) '
            "{ clientMutationId } }'",
            # A REST body sourced from a file carries no field flag, so it reads
            # as a GET unless --input itself counts as a write; the trailing
            # shell comment is the decoy that then releases the bypass.
            "gh api repos/foo/bar/issues/5/comments --input reply.json "
            "# repos/other/repo/pulls/1/comments",
        ],
    )
    def test_write_signal_denies_before_cross_repo_bypass_runs(
        self, isolated_home, current_repo_foo_bar, command
    ):
        assert run_hook(RESPOND_PR_HOOK, bash_input(command), cwd=current_repo_foo_bar) == "deny"

    def test_owner_repo_placeholder_is_not_cross_repo(
        self, isolated_home, current_repo_foo_bar
    ):
        """`repos/{owner}/{repo}/...` is gh's own substitution for the current repo."""
        command = "gh api repos/{owner}/{repo}/pulls/1/comments"
        assert run_hook(RESPOND_PR_HOOK, bash_input(command), cwd=current_repo_foo_bar) == "deny"

    @pytest.mark.parametrize(
        "command",
        [
            "gh api repos/{owner}/actual-repo/pulls/1/comments",
            "gh api repos/real-owner/{repo}/pulls/1/comments",
        ],
    )
    def test_partial_owner_or_repo_placeholder_is_not_cross_repo(
        self, isolated_home, current_repo_foo_bar, command
    ):
        """A brace anywhere in the owner/repo pair blanks the whole extracted
        token, not only a fully-substituted `repos/{owner}/{repo}/...` path --
        `repos/{owner}/actual-repo/...` and `repos/real-owner/{repo}/...` are
        each a mix of gh's placeholder and a literal segment, and both must
        still fail to read as an explicit cross-repo target."""
        assert run_hook(RESPOND_PR_HOOK, bash_input(command), cwd=current_repo_foo_bar) == "deny"

    @pytest.mark.parametrize(
        "command",
        [
            "gh api repos/foo/bar/issues/5/comments "
            "--jq '.[] | select(.body | contains(\"other/repo\"))'",
            "gh api repos/foo/bar/issues/5/comments  # see also other/repo for context",
        ],
    )
    def test_decoy_repo_token_in_quoted_body_does_not_release_current_repo_read(
        self, isolated_home, current_repo_foo_bar, command
    ):
        """A read of the current repo (foo/bar) whose --jq expression or a
        trailing shell comment mentions another repo must still deny.

        COMMAND_REPO's extraction only recognizes an explicit cross-repo
        target: `repos/OWNER/REPO/(pulls|issues)/N/...` or an `-R`/`--repo`
        flag. The bare token `other/repo` embedded in a quoted body matches
        neither shape, so extraction still resolves to this command's own
        `repos/foo/bar/...` URL -- which equals the current repo, so the
        bypass never releases it. These commands carry no write signal
        (unlike the sibling test above), so this is the mechanism the old
        vacuous test name gestured at.
        """
        assert run_hook(RESPOND_PR_HOOK, bash_input(command), cwd=current_repo_foo_bar) == "deny"

    def test_full_repo_path_decoy_in_quoted_body_releases_current_repo_read_accepted_gap(
        self, isolated_home, current_repo_foo_bar
    ):
        """Pins a deliberate, documented tradeoff -- NOT a demonstration that
        this behavior is desirable, safe, or something to preserve on
        purpose. It is a gap the hook's own comments accept and this test
        exists so a change to the extraction shows up here instead of
        silently.

        Unlike the bare `other/repo` token in the sibling test above (which
        does NOT release), a *full* `repos/OWNER/REPO/(pulls|issues)/N/...`
        path shape embedded anywhere in the command -- including inside a
        quoted --jq expression on an otherwise current-repo (foo/bar) READ --
        does match COMMAND_REPO's extraction and does release the bypass.
        The read carries no write signal, so the worst this releases is a
        read; a write is still caught by GATED_WRITE before extraction ever
        runs, per the write-detection-precedes-cross-repo-bypass tests
        above.
        """
        command = (
            "gh api repos/foo/bar/issues/5/comments "
            '--jq \'.[] | select(.body | contains("repos/other/repo/pulls/3/comments"))\''
        )
        assert run_hook(RESPOND_PR_HOOK, bash_input(command), cwd=current_repo_foo_bar) == "allow"

    def test_ssh_origin_cross_repo_allowed(self, isolated_home, tmp_path):
        """SSH-form origin (git@github.com:owner/repo.git) must parse too."""
        repo = tmp_path / "ssh-repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "git@github.com:foo/bar.git"],
            cwd=repo,
            check=True,
        )
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input("gh api repos/other/repo/issues/5/comments"),
                cwd=repo,
            )
            == "allow"
        )

    def test_ssh_origin_same_repo_denied(self, isolated_home, tmp_path):
        repo = tmp_path / "ssh-repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "git@github.com:foo/bar.git"],
            cwd=repo,
            check=True,
        )
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input("gh api repos/foo/bar/issues/5/comments"),
                cwd=repo,
            )
            == "deny"
        )

    def test_cross_repo_read_denies_when_repo_has_no_origin_remote(
        self, isolated_home, current_repo_no_origin
    ):
        """Deliberate fail-closed behavior: with no `origin` remote,
        CURRENT_URL is empty, so the cross-repo release block never runs --
        even a read that explicitly targets a different repo denies here,
        rather than being recognized as legitimate cross-repo research."""
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input("gh api repos/other/repo/pulls/1/comments"),
                cwd=current_repo_no_origin,
            )
            == "deny"
        )

    # -- Skill ↔ hook alignment -------------------------------------------
    # These execute the enable/disable bypass recipes verbatim from
    # respond-pr SKILL.md. If the skill body drifts from the marker layout
    # require-respond-pr.sh expects, these fail.

    def test_skill_enable_command_creates_bypass_marker(
        self, isolated_home, current_repo_foo_bar
    ):
        """Run the SKILL.md enable-bypass recipe and verify the resulting
        marker authorizes a previously-gated gh command."""
        sid = "test-session-respond-pr-enable"
        _seed_session(isolated_home, sid)

        gated_command = "gh api repos/foo/bar/pulls/5/comments"
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input(gated_command, session_id=sid),
                cwd=current_repo_foo_bar,
            )
            == "deny"
        ), "precondition: gh comment fetch must be gated before bypass is enabled"

        enable_command = extract_skill_command(RESPOND_PR_SKILL, "enable-bypass")
        run_skill_command(enable_command, cwd=current_repo_foo_bar, isolated_home=isolated_home)

        marker = isolated_home / ".claude" / ".respond-pr-active.d" / sid
        assert marker.exists(), (
            "SKILL.md enable-bypass recipe ran but no marker landed at the "
            "path the hook checks — the skill and hook disagree on layout."
        )
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input(gated_command, session_id=sid),
                cwd=current_repo_foo_bar,
            )
            == "allow"
        )

    # -- GraphQL surface ----------------------------------------------------
    # The gate reads the GraphQL command's write verbs, so the two tests below
    # pin both halves of one boundary: a read query reaches the API, and a
    # comment mutation is denied. Keep them as a pair — the read assertion
    # alone would pass just as well against a hook with no GraphQL arm at all,
    # which is what the deny half exists to rule out.

    def _graphql_command_with_placeholders_filled(self) -> str:
        """Return Step 3's GraphQL fetch with its placeholders substituted.

        The skill writes the command for a human to adapt, so it ships with
        OWNER/REPO/NUMBER placeholders. Every deny regex requires a numeric
        PR number, which means a literal `NUMBER` falls through no matter
        what URL surrounds it — an unsubstituted command would sit green
        against exactly the REST regression these tests exist to catch.
        Substituting makes the assertion turn on the command's shape rather
        than on its placeholders being unmatchable.

        The substitution is plain text, not a template engine: it is safe
        only while no other uppercase OWNER/REPO/NUMBER substring appears in
        the fenced block. Adding one (an env var named `GH_REPO_TOKEN`, say)
        would corrupt it silently.
        """
        command = extract_skill_command(ERROR_MODE_SKILL, "fetch-pr-comments")
        runnable = command.replace("OWNER", "foo").replace("REPO", "bar").replace("NUMBER", "5")
        assert runnable != command, (
            "the Step 3 fixture no longer contains the OWNER/REPO/NUMBER "
            "placeholders this substitution targets — re-check that the "
            "command still reaches the hook in a denyable shape."
        )
        return runnable

    def test_error_mode_analysis_graphql_fetch_is_allowed(
        self, isolated_home, current_repo_foo_bar
    ):
        """The error-mode-analysis comment fetch must reach the GitHub API.

        That skill's Step 3 correlates human PR review comments against the
        session transcript, so it has to read all three comment kinds. The
        equivalent REST calls are denied by this hook, which made the skill's
        own documented procedure self-blocking; Step 3 now issues one
        read-only GraphQL query instead. Reading the command from SKILL.md
        rather than hardcoding it means a regression back to a denied REST
        form fails here instead of silently re-breaking Step 3.

        This is a real carve-out, not a fallthrough: the gate's GraphQL arm
        matches write verbs, and a query carries none. Pair it with the
        mutation test below, which is what proves the arm exists at all.
        """
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input(self._graphql_command_with_placeholders_filled()),
                cwd=current_repo_foo_bar,
            )
            == "allow"
        )

    @pytest.mark.parametrize(
        "mutation_body",
        [
            'addComment(input: {subjectId: "PR_kwABC", body: "hi"})',
            'addPullRequestReview(input: {pullRequestId: "PR_kwABC"})',
            'addPullRequestReviewComment(input: {body: "hi"})',
            'addPullRequestReviewThread(input: {body: "hi"})',
            'addPullRequestReviewThreadReply(input: {body: "hi"})',
            'addDiscussionComment(input: {body: "hi"})',
            'updateIssueComment(input: {id: "IC_kwABC", body: "edited"})',
            'updatePullRequestReviewComment(input: {body: "edited"})',
            'deleteIssueComment(input: {id: "IC_kwABC"})',
            'deletePullRequestReview(input: {pullRequestReviewId: "PRR_kwABC"})',
            # Publishes a pending review body — the GraphQL twin of
            # `gh pr review`. Carries no add/update/delete verb, so it is the
            # one a verb-only pattern misses.
            'submitPullRequestReview(input: {event: COMMENT, body: "hi"})',
        ],
    )
    def test_graphql_comment_mutations_denied(
        self, isolated_home, current_repo_foo_bar, mutation_body
    ):
        """GraphQL comment writes are gated exactly as their REST twins are.

        These post, edit, and delete PR comments the same way `gh pr comment`
        and the REST endpoints above do; only the transport differs. Routing
        them around /respond-pr would skip the [Claude Code] attribution
        prefix that discloses AI authorship to outside readers of a public
        PR, so transport must not decide whether the gate applies.

        A GraphQL mutation addresses its target by node ID, so the
        command carries no `repos/OWNER/REPO` path and no `-R` flag for the
        cross-repo bypass to read. It therefore denies rather than falling
        through — the safe direction for an unidentifiable target.
        """
        command = f"gh api graphql -f query='mutation {{ {mutation_body} {{ clientMutationId }} }}'"
        assert (
            run_hook(RESPOND_PR_HOOK, bash_input(command), cwd=current_repo_foo_bar)
            == "deny"
        )

    @pytest.mark.parametrize(
        "mutation_body",
        [
            'minimizeComment(input: {subjectId: "IC_kwABC"})',
            'unminimizeComment(input: {subjectId: "IC_kwABC"})',
            'resolveReviewThread(input: {threadId: "PRRT_kwABC"})',
            'unresolveReviewThread(input: {threadId: "PRRT_kwABC"})',
            'addReaction(input: {subjectId: "IC_kwABC", content: THUMBS_UP})',
        ],
    )
    def test_graphql_non_comment_mutations_allowed(
        self, isolated_home, current_repo_foo_bar, mutation_body
    ):
        """Mutations that write no comment body stay out of the gate.

        The gate exists to keep AI-authored comment *content* attributed.
        These change a comment's visibility, a thread's resolved state, or a
        reaction — none author text, so none need the prefix. Pinning them
        bounds the deny pattern from the other side: it is deliberately loose
        about the middle of a mutation name, and these are what stops that
        looseness from quietly swallowing the whole GraphQL surface.
        """
        command = f"gh api graphql -f query='mutation {{ {mutation_body} {{ clientMutationId }} }}'"
        assert (
            run_hook(RESPOND_PR_HOOK, bash_input(command), cwd=current_repo_foo_bar)
            == "allow"
        )

    def test_multi_line_graphql_mutation_denied(self, isolated_home, current_repo_foo_bar):
        """A mutation split across lines must be gated like a one-liner.

        grep matches within a line, so before the hook flattened the command
        every arm — REST included — could be walked around simply by wrapping
        the text. GraphQL bodies are conventionally written multi-line, which
        made this the likeliest form of the real thing rather than an edge
        case.
        """
        command = (
            "gh api graphql -f query='\n"
            "mutation {\n"
            '  addComment(input: {subjectId: "PR_kwABC", body: "hi"}) {\n'
            "    clientMutationId\n"
            "  }\n"
            "}'"
        )
        assert (
            run_hook(RESPOND_PR_HOOK, bash_input(command), cwd=current_repo_foo_bar)
            == "deny"
        )

    def test_graphql_mutation_with_interposed_flag_before_graphql_positional_denied(
        self, isolated_home, current_repo_foo_bar
    ):
        """`api` and `graphql` need not be lexically adjacent for the mutation
        arm to catch a write.

        A `gh api` flag (`-H`, `--cache`, etc.) placed before the `graphql`
        positional argument is ordinary, valid `gh api` usage. Requiring the
        two tokens to sit next to each other would miss this exact shape and
        fall through to allow, since no other arm matches a bare `graphql`
        positional.
        """
        command = (
            'gh api -H "Accept: application/vnd.github+json" graphql '
            "-f query='mutation{addComment(input:{}){id}}'"
        )
        assert (
            run_hook(RESPOND_PR_HOOK, bash_input(command), cwd=current_repo_foo_bar)
            == "deny"
        )

    @pytest.mark.parametrize(
        "command",
        [
            "gh api graphql -F query=@mutation.graphql -F pr=5",
            "gh api graphql -f query=@post-comment.graphql",
            "gh api graphql --input payload.json",
            "gh api graphql --input=payload.json",
        ],
    )
    def test_graphql_query_sourced_from_file_denied(
        self, isolated_home, current_repo_foo_bar, command
    ):
        """A query the gate cannot read is a query it cannot clear.

        `-f/-F query=@file` and `--input file` are ordinary documented `gh
        api` usage, and either can carry a mutation whose text never appears
        in the command line. Denying costs a false positive on a file-sourced
        read; allowing would reopen the whole write path.
        """
        assert (
            run_hook(RESPOND_PR_HOOK, bash_input(command), cwd=current_repo_foo_bar)
            == "deny"
        )

    def test_skill_disable_command_removes_bypass_marker(
        self, isolated_home, current_repo_foo_bar
    ):
        """Run enable then disable from SKILL.md; verify the disable recipe
        removes the marker and the hook re-gates."""
        sid = "test-session-respond-pr-disable"
        _seed_session(isolated_home, sid)

        enable_command = extract_skill_command(RESPOND_PR_SKILL, "enable-bypass")
        run_skill_command(enable_command, cwd=current_repo_foo_bar, isolated_home=isolated_home)
        marker = isolated_home / ".claude" / ".respond-pr-active.d" / sid
        assert marker.exists(), "enable-bypass setup did not create the marker"

        disable_command = extract_skill_command(RESPOND_PR_SKILL, "disable-bypass")
        run_skill_command(disable_command, cwd=current_repo_foo_bar, isolated_home=isolated_home)

        assert not marker.exists(), (
            "SKILL.md disable-bypass recipe ran but the marker is still "
            "present — the skill and hook disagree on the marker path."
        )
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input("gh api repos/foo/bar/pulls/5/comments", session_id=sid),
                cwd=current_repo_foo_bar,
            )
            == "deny"
        )


class TestRespondPrStructuralInvariants:
    """Two structural-invariant tests. Neither is a behavioral
    hook-gate test like the classes above: both are source/doc scans,
    because the two invariants they pin have no runtime code path this
    hook (or any script) executes — see each test's own docstring."""

    def test_documented_reply_commands_all_carry_claude_code_attribution(self):
        """The requirement pinned by this test: 'a comment posted through
        /respond-pr carries the [Claude Code] prefix'. The whole gate
        exists to route writes into /respond-pr for that guarantee, but
        nothing enforces it in code — the attribution is a prose
        instruction the skill's own worked examples model, not a script
        the hook (or this test) can execute against a real write. Pinned
        at the only enforceable layer available instead: every documented
        `-F body=` reply-posting command in SKILL.md itself carries both
        the required prefix and the AI-disclosure trailer, so a worked
        example silently losing the prefix fails here rather than
        shipping unnoticed."""
        skill_text = RESPOND_PR_SKILL.read_text()
        reply_blocks = [
            block for block in _FENCED_BLOCK_RE.findall(skill_text) if "-F body=" in block
        ]
        assert reply_blocks, "no `-F body=` reply-posting command blocks found in respond-pr/SKILL.md"
        for block in reply_blocks:
            assert "**[Claude Code]**" in block, (
                f"reply command missing the [Claude Code] prefix:\n{block}"
            )
            assert "🤖 Generated with [Claude Code](https://claude.com/claude-code)" in block, (
                f"reply command missing the AI-disclosure attribution trailer:\n{block}"
            )

    def test_every_pattern_is_accounted_for_in_a_gate_bucket(self):
        """GH-483's requirement that no PATTERN_* constant reach production
        wired into zero gate arms, inverted: a PATTERN_* constant can be
        defined and never wired into a gate arm, which is how one command
        reached production gated in one arm only. This test scans
        the hook's own source: every PATTERN_* it assigns must appear in
        one of three accounted-for buckets --
          - the if/elif arm chain that decides whether to keep evaluating
            a command at all (a literal `[[ "$COMMAND_FLAT" =~ $PATTERN_X
            ]]` conditional), which also covers PATTERN_MUTATING_METHOD's
            own standalone check;
          - the gated_write_patterns array literal;
          - an explicit allowlist of sub-pattern-only helpers that are
            never matched directly against COMMAND_FLAT themselves
            (PATTERN_REPO_FLAG_RUN, a repo-flag-consuming fragment
            interpolated into PATTERN_PR_WRITE_CMD and
            PATTERN_ISSUE_WRITE_CMD's own definitions, not a top-level
            write-detection pattern in its own right).
        A pattern in none of the three is real, unaccounted-for dead code
        or a forgotten gate wiring -- either way this test should catch
        it."""
        text = RESPOND_PR_HOOK.read_text()
        pattern_names = re.findall(r"^(PATTERN_[A-Z_]+)=", text, re.MULTILINE)
        assert pattern_names, "no PATTERN_* assignments found — did the naming convention change?"

        conditional_matches = set(
            re.findall(r'\[\[\s*"\$COMMAND_FLAT"\s*=~\s*\$(PATTERN_[A-Z_]+)\s*\]\]', text)
        )

        array_match = re.search(r"gated_write_patterns=\((.*?)\)", text, re.DOTALL)
        assert array_match, "gated_write_patterns array literal not found in require-respond-pr.sh"
        array_names = set(re.findall(r'"\$(PATTERN_[A-Z_]+)"', array_match.group(1)))

        sub_pattern_only_allowlist = {"PATTERN_REPO_FLAG_RUN"}

        accounted_for = conditional_matches | array_names | sub_pattern_only_allowlist
        missing = [name for name in pattern_names if name not in accounted_for]
        assert not missing, (
            f"PATTERN_* not accounted for in the arm chain, "
            f"gated_write_patterns, or sub_pattern_only_allowlist: {missing} "
            "— wire it into a gate, or add it to sub_pattern_only_allowlist "
            "with a reason."
        )


# -- review-pr's active-bypass marker (reads) and unconditional write deny -
# ----------------------------------------------------------------------------
#
# Uses the shared `git_repo`/`isolated_home` fixtures from conftest.py
# rather than `current_repo_foo_bar` above: these tests need a resolvable
# HEAD (git_repo has a real commit; current_repo_foo_bar does not) but no
# particular origin.

REVIEW_PR_PR_NUMBER = 42
REVIEW_PR_PR_IDENTITY = f"foo/bar#{REVIEW_PR_PR_NUMBER}"


def _write_review_pr_active_marker(home, session_id, pid=None):
    marker_dir = home / ".claude" / ".review-pr-active.d"
    marker_dir.mkdir(parents=True, exist_ok=True)
    (marker_dir / session_id).write_text(str(pid if pid is not None else os.getpid()))


def _write_findings_body(tmp_path, content="# findings\n", name="findings.md"):
    body_file = tmp_path / name
    body_file.write_text(content)
    body_hash = hashlib.sha256(body_file.read_bytes()).hexdigest()
    return body_file, body_hash


def _review_command(pr_number, body_file, flag="--comment"):
    return f"gh pr review {pr_number} {flag} -F {body_file}"


@pytest.fixture
def git_repo_foo_bar_origin(git_repo):
    """`git_repo` with an `origin` remote resolving to foo/bar -- matching
    REVIEW_PR_PR_IDENTITY's stored owner/repo, so a completion marker built
    from that identity describes a plausible review of this repo. Not
    load-bearing for the unconditional-deny tests below, which deny
    regardless of any repo match, but keeps the fixture data internally
    consistent."""
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/foo/bar.git"],
        cwd=git_repo,
        check=True,
    )
    return git_repo


class TestReviewPrActiveMarkerReadBypass:
    def test_active_marker_releases_a_matched_read(self, isolated_home, git_repo):
        sid = "test-session-review-pr-read"
        _write_review_pr_active_marker(isolated_home, sid)
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input(
                    "gh api repos/foo/bar/pulls/5/reviews --paginate", session_id=sid
                ),
                cwd=git_repo,
                home=isolated_home,
            )
            == "allow"
        )

    def test_absence_of_marker_still_denies_a_read(self, isolated_home, git_repo):
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input(
                    "gh api repos/foo/bar/pulls/5/reviews --paginate",
                    session_id="no-such-session",
                ),
                cwd=git_repo,
                home=isolated_home,
            )
            == "deny"
        )

    def test_dead_pid_marker_is_evicted_and_denies(self, isolated_home, git_repo):
        sid = "test-session-review-pr-dead"
        _write_review_pr_active_marker(isolated_home, sid, pid=99999999)
        marker = isolated_home / ".claude" / ".review-pr-active.d" / sid
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input(
                    "gh api repos/foo/bar/pulls/5/reviews --paginate", session_id=sid
                ),
                cwd=git_repo,
                home=isolated_home,
            )
            == "deny"
        )
        assert not marker.exists(), "hook must evict the orphan marker on dead PID"


class TestGhPrEditBodyMutatingFormsDenied:
    """The 'never edit someone else's PR body' invariant, folded into this
    hook's gated-write patterns independent of either bypass marker."""

    @pytest.mark.parametrize(
        "command",
        [
            'gh pr edit 5 --body "new body text"',
            "gh pr edit 5 --body-file /tmp/new-body.md",
        ],
    )
    def test_denied_with_no_marker(self, isolated_home, git_repo, command):
        assert run_hook(RESPOND_PR_HOOK, bash_input(command), cwd=git_repo, home=isolated_home) == "deny"

    def test_denied_even_with_a_live_review_pr_active_marker(
        self, isolated_home, git_repo
    ):
        """A live review-pr marker only releases a matched READ -- every
        matched WRITE is denied unconditionally regardless of marker state,
        and `gh pr edit`'s body-mutating form is a matched write."""
        sid = "test-session-review-pr-edit"
        _write_review_pr_active_marker(isolated_home, sid)
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input('gh pr edit 5 --body "new body text"', session_id=sid),
                cwd=git_repo,
                home=isolated_home,
            )
            == "deny"
        )

    def test_title_only_edit_is_not_gated(self, isolated_home, git_repo):
        """Bounds the pattern from the other side: only body-mutating forms
        fold into this gate -- `gh pr edit` covers title, labels, and more,
        and gating every use would be broader than the invariant this closes."""
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input('gh pr edit 5 --title "new title"'),
                cwd=git_repo,
                home=isolated_home,
            )
            == "allow"
        )


class TestReviewPrWriteDeniedUnconditionally:
    """Every `gh pr review`/`reviews` write during an active /review-pr
    session is denied unconditionally -- posting must go through
    ~/.claude/scripts/review-pr-post.sh instead, which independently
    re-verifies the completion marker before ever calling gh. Neither a
    non-approving verdict flag nor a completion marker whose HEAD, PR
    identity, and body hash all match the gated command changes that: this
    hook grants no write bypass of its own, for any verdict."""

    SID = "test-session-review-pr-write"

    @pytest.mark.parametrize(
        "command",
        [
            f"gh pr review {REVIEW_PR_PR_NUMBER} --comment -F body.md",
            f"gh pr review {REVIEW_PR_PR_NUMBER} --request-changes -F body.md",
            f"gh pr review {REVIEW_PR_PR_NUMBER} --approve -F body.md",
            f"gh api repos/foo/bar/pulls/{REVIEW_PR_PR_NUMBER}/reviews -f event=COMMENT -f body=@body.md",
            f"gh api repos/foo/bar/pulls/{REVIEW_PR_PR_NUMBER}/reviews -f event=APPROVE -f body=@body.md",
            (
                "gh api graphql -f query='mutation { submitPullRequestReview(input: "
                '{reviewId: "PRR_abc", event: COMMENT}) { clientMutationId } }\''
            ),
        ],
    )
    def test_write_denied_with_no_marker_at_all(
        self, isolated_home, git_repo_foo_bar_origin, command
    ):
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input(command),
                cwd=git_repo_foo_bar_origin,
                home=isolated_home,
            )
            == "deny"
        )

    @pytest.mark.parametrize(
        "flag", ["--comment", "--request-changes", "--approve", "-c", "-r", "-a"]
    )
    def test_write_denied_with_a_live_active_marker_and_no_completion_marker(
        self, isolated_home, git_repo_foo_bar_origin, tmp_path, flag
    ):
        sid = self.SID
        _write_review_pr_active_marker(isolated_home, sid)
        body_file, _ = _write_findings_body(tmp_path)
        command = _review_command(REVIEW_PR_PR_NUMBER, body_file, flag=flag)
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input(command, session_id=sid),
                cwd=git_repo_foo_bar_origin,
                home=isolated_home,
            )
            == "deny"
        )

    def test_write_denied_even_with_a_fully_matching_completion_marker(
        self, isolated_home, git_repo_foo_bar_origin, tmp_path
    ):
        """The write-authorization cross-check this hook used to run is
        gone: a completion marker whose stored HEAD, PR identity, and body
        hash all match the gated command exactly no longer releases a write
        here -- that verification now happens inside review-pr-post.sh,
        never in this hook."""
        sid = self.SID
        _write_review_pr_active_marker(isolated_home, sid)
        body_file, body_hash = _write_findings_body(tmp_path)
        write_review_pr_completion_marker(
            isolated_home,
            git_repo_foo_bar_origin,
            REVIEW_PR_PR_IDENTITY,
            head_sha(git_repo_foo_bar_origin),
            body_hash,
            sid,
        )
        command = _review_command(REVIEW_PR_PR_NUMBER, body_file)
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input(command, session_id=sid),
                cwd=git_repo_foo_bar_origin,
                home=isolated_home,
            )
            == "deny"
        )

    def test_deny_message_redirects_to_review_pr_post_script(
        self, isolated_home, git_repo_foo_bar_origin, tmp_path
    ):
        sid = self.SID
        _write_review_pr_active_marker(isolated_home, sid)
        body_file, _ = _write_findings_body(tmp_path)
        command = _review_command(REVIEW_PR_PR_NUMBER, body_file)
        reason = run_hook_reason(
            RESPOND_PR_HOOK,
            bash_input(command, session_id=sid),
            cwd=git_repo_foo_bar_origin,
            home=isolated_home,
        )
        assert "review-pr-post.sh" in reason

    @pytest.mark.parametrize(
        "command",
        [
            "~/.claude/scripts/review-pr-post.sh comment",
            "~/.claude/scripts/review-pr-post.sh request-changes",
            "~/.claude/scripts/review-pr-post.sh comment && echo posted",
        ],
    )
    def test_wrapper_script_invocation_is_allowed_through(
        self, isolated_home, git_repo_foo_bar_origin, command
    ):
        """The redirect design depends on this hook never matching the
        wrapper script's own invocation -- the `gh pr review` call it makes
        happens inside the wrapper, invisible to this hook. Neither the
        filename nor the chained form trips PATTERN_PR_WRITE_CMD, which
        requires the literal `gh` `pr` `comment|review` token sequence."""
        sid = self.SID
        _write_review_pr_active_marker(isolated_home, sid)
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input(command, session_id=sid),
                cwd=git_repo_foo_bar_origin,
                home=isolated_home,
            )
            == "allow"
        )
