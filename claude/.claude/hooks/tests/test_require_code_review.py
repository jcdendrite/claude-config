"""Tests for require-code-review.sh."""
from __future__ import annotations

import os
import subprocess

import pytest
from helpers import (
    DEFAULT_TEST_SESSION_ID,
    HOOKS_DIR,
    SKILLS_DIR,
    bash_input,
    edit_input,
    extract_skill_command,
    marker_path,
    run_hook,
    run_skill_command,
    staged_diff_hash,
    write_marker,
)

CODE_REVIEW_HOOK = HOOKS_DIR / "require-code-review.sh"
CODE_REVIEW_SKILL = SKILLS_DIR / "code-review" / "SKILL.md"


class TestRequireCodeReview:
    # The marker layout is ~/.claude/review-markers/<repo-hash>.<session_id>.
    # The hook reads session_id from its JSON payload and checks the
    # matching session's marker. Tests below thread session_id through
    # `bash_input` and `write_marker` for paths that exercise the marker
    # check. Tests that exit early (non-bash tool, non-commit command,
    # outside-repo, empty staged diff) don't need session_id — the hook
    # returns before reaching the marker logic.

    def test_no_marker_denies_commit(self, isolated_home, git_repo):
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_wrong_hash_marker_denies(self, isolated_home, git_repo):
        write_marker(isolated_home, git_repo, "0" * 64)
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_correct_hash_marker_allows(self, isolated_home, git_repo):
        write_marker(isolated_home, git_repo, staged_diff_hash(git_repo))
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
            )
            == "allow"
        )

    def test_chained_add_commit_allowed_when_marker_current(self, isolated_home, git_repo):
        write_marker(isolated_home, git_repo, staged_diff_hash(git_repo))
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input(
                    "git add file.txt && git commit -m foo",
                    session_id=DEFAULT_TEST_SESSION_ID,
                ),
                cwd=git_repo,
            )
            == "allow"
        )

    def test_restaging_invalidates_marker(self, isolated_home, git_repo):
        write_marker(isolated_home, git_repo, staged_diff_hash(git_repo))
        (git_repo / "file.txt").write_text("first\nsecond\nthird\n")
        subprocess.run(["git", "add", "file.txt"], cwd=git_repo, check=True)
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_chained_add_commit_denied_when_marker_stale(self, isolated_home, git_repo):
        write_marker(isolated_home, git_repo, staged_diff_hash(git_repo))
        (git_repo / "file.txt").write_text("first\nsecond\nthird\n")
        subprocess.run(["git", "add", "file.txt"], cwd=git_repo, check=True)
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input(
                    "git add file.txt && git commit -m foo",
                    session_id=DEFAULT_TEST_SESSION_ID,
                ),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_refreshed_marker_allows(self, isolated_home, git_repo):
        (git_repo / "file.txt").write_text("first\nsecond\nthird\n")
        subprocess.run(["git", "add", "file.txt"], cwd=git_repo, check=True)
        write_marker(isolated_home, git_repo, staged_diff_hash(git_repo))
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
            )
            == "allow"
        )

    def test_other_sessions_marker_authorizes_identical_staged_diff(self, isolated_home, git_repo):
        """Session A's marker authorizes session B's commit of the identical diff.

        The marker's stored hash proves a review covered exactly this staged
        state; the filename's session suffix only keeps parallel sessions from
        overwriting each other's markers. Keying the read on it denies a
        resumed session (new session_id) a review it already completed."""
        diff_hash = staged_diff_hash(git_repo)
        write_marker(isolated_home, git_repo, diff_hash, session_id="session-A")
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id="session-B"),
                cwd=git_repo,
            )
            == "allow"
        )

    def test_other_sessions_marker_does_not_authorize_a_changed_diff(self, isolated_home, git_repo):
        """The negative half: acceptance is by diff hash, not by marker existence.

        Without this, dropping the session key would degrade the gate from a
        content check to an existence check."""
        write_marker(isolated_home, git_repo, staged_diff_hash(git_repo), session_id="session-A")
        # Re-stage a different change; the reviewed hash no longer describes it.
        (git_repo / "newly_added.py").write_text("print('unreviewed')\n")
        subprocess.run(["git", "add", "newly_added.py"], cwd=git_repo, check=True)
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id="session-B"),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_no_session_id_in_input_reads_marker(self, isolated_home, git_repo):
        """A payload with no session_id still finds a marker covering this diff.

        This gate reads no session-scoped state at all, so a payload that
        cannot be session-keyed is not thereby unreviewed."""
        write_marker(isolated_home, git_repo, staged_diff_hash(git_repo))
        # bash_input() with session_id=None omits the field entirely.
        assert (
            run_hook(CODE_REVIEW_HOOK, bash_input("git commit -m foo"), cwd=git_repo)
            == "allow"
        )

    def test_no_session_id_and_no_matching_marker_denies(self, isolated_home, git_repo):
        """Fail-closed still holds: no session_id and no covering review → deny."""
        assert (
            run_hook(CODE_REVIEW_HOOK, bash_input("git commit -m foo"), cwd=git_repo)
            == "deny"
        )

    def test_marker_under_another_repo_hash_does_not_authorize(
        self, isolated_home, git_repo, tmp_path
    ):
        """The repo-hash prefix stays part of the read predicate.

        Only the session suffix is globbed. A review of an identical diff in a
        different repository reviewed different code, so its marker must not
        release this gate."""
        other_repo = tmp_path / "other-repo"
        other_repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=other_repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=other_repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=other_repo, check=True)

        # Same diff hash, filed under the other repo's repo-hash prefix.
        write_marker(isolated_home, other_repo, staged_diff_hash(git_repo), session_id="s")
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id="s"),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_skill_marker_write_command_matches_hook_path(self, isolated_home, git_repo):
        """Regression guard against the SKILL command and HOOK getting out
        of sync on path derivation.

        Reads the marker-write recipe directly from code-review SKILL.md
        via the HOOK_TEST_FIXTURE marker, executes it, and verifies the
        hook accepts the result. SKILL.md is the source of truth — if
        the recipe drifts from what the hook expects, this test fails.
        """
        sid = "test-session-skill-cmd"
        # Set up the session_id lookup file at the path the skill reads.
        # The skill computes its filename from $PPID inside the bash
        # subshell; subprocess.run spawns bash as a child of this pytest
        # process, so $PPID resolves to os.getpid().
        sessions_dir = isolated_home / ".claude" / "sessions"
        sessions_dir.mkdir(parents=True)
        (sessions_dir / str(os.getpid())).write_text(sid)

        markers_dir = isolated_home / ".claude" / "review-markers"
        if markers_dir.exists():
            for f in markers_dir.glob("*"):
                f.unlink()

        skill_command = extract_skill_command(CODE_REVIEW_SKILL, "marker-write")
        run_skill_command(skill_command, cwd=git_repo, isolated_home=isolated_home)
        # Sanity check: the recipe wrote a marker at the path the hook checks.
        assert marker_path(isolated_home, git_repo, session_id=sid).exists(), (
            "SKILL.md marker-write recipe ran but no marker landed at the "
            "path the hook computes — the skill and hook disagree on layout."
        )
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id=sid),
                cwd=git_repo,
            )
            == "allow"
        )

    def test_empty_staged_diff_allows(self, isolated_home, git_repo):
        """Amend-message, --allow-empty, or nothing-to-commit has no new content."""
        subprocess.run(["git", "commit", "-q", "-m", "tmp"], cwd=git_repo, check=True)
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input("git commit --amend -m new-message"),
                cwd=git_repo,
            )
            == "allow"
        )

    @pytest.mark.parametrize(
        "command",
        [
            "git status",
            "git log --oneline",
            "git commit-tree abc123",
        ],
    )
    def test_non_commit_git_commands_allowed(self, isolated_home, git_repo, command):
        assert run_hook(CODE_REVIEW_HOOK, bash_input(command), cwd=git_repo) == "allow"

    def test_non_bash_tool_allowed(self, isolated_home, git_repo):
        assert run_hook(CODE_REVIEW_HOOK, edit_input("/tmp/foo.txt"), cwd=git_repo) == "allow"

    def test_outside_git_repo_allowed(self, isolated_home, tmp_path):
        """Hook should bail rather than false-deny when git can't resolve a repo."""
        non_repo = tmp_path / "not-a-repo"
        non_repo.mkdir()
        assert run_hook(CODE_REVIEW_HOOK, bash_input("git commit -m foo"), cwd=non_repo) == "allow"

    def test_chained_marker_write_then_commit_allowed_without_existing_marker(
        self, isolated_home, git_repo
    ):
        """PreToolUse fires once per Bash tool call before the chain runs, so
        an on-disk marker check finds nothing for naturally-typed forms like
        `marker.sh write code-review && git commit`. The chain itself will
        write the marker before commit, and marker.sh is the only sanctioned
        writer in either case — trust the in-chain write and allow."""
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input(
                    "~/.claude/scripts/marker.sh write code-review && git commit -m foo",
                    session_id=DEFAULT_TEST_SESSION_ID,
                ),
                cwd=git_repo,
            )
            == "allow"
        )

    def test_chained_bare_marker_write_does_not_authorize(self, isolated_home, git_repo):
        """The bypass must NOT recognize a bare `marker.sh` (PATH-resolved or
        attacker-controlled path like `/home/evil/marker.sh`). Only canonical
        ~/.claude/scripts/marker.sh or absolute /.claude/scripts/marker.sh
        paths are sanctioned by permissions.allow and enforce-marker-script-shape,
        and this helper must agree to prevent a chained-form bypass via a
        non-leading bogus marker.sh path."""
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input(
                    "marker.sh write code-review && git commit -m foo",
                    session_id=DEFAULT_TEST_SESSION_ID,
                ),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_chained_non_canonical_marker_path_does_not_authorize(
        self, isolated_home, git_repo
    ):
        """A bogus marker.sh path (not under /.claude/scripts/) must not
        trigger the bypass even when chained correctly. Closes the gap where
        enforce-marker-script-shape's leading-anchor check would not fire on
        a non-leading marker.sh fragment in a chain."""
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input(
                    "git add . && /home/evil/marker.sh write code-review && git commit -m foo",
                    session_id=DEFAULT_TEST_SESSION_ID,
                ),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_echo_wrapping_marker_text_does_not_authorize(
        self, isolated_home, git_repo
    ):
        """`echo ~/.claude/scripts/marker.sh write code-review && git commit`
        looks like a chained marker write to a text-matcher, but `echo` does
        not actually invoke marker.sh — only prints the path. The helper must
        anchor at command start so wrapper commands (echo, printf, cat, sudo)
        cannot wedge the gate open via text appearance."""
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input(
                    "echo ~/.claude/scripts/marker.sh write code-review && git commit -m foo",
                    session_id=DEFAULT_TEST_SESSION_ID,
                ),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_env_var_prefix_marker_does_not_authorize(self, isolated_home, git_repo):
        """`FOO=bar ~/.claude/scripts/marker.sh write code-review && git commit`
        is intentionally not in the sanctioned chained shape — env-var prefix
        is one of the forms enforce-marker-script-shape comments call out as
        gated by permissions.allow, not by shape regex. The helper must
        agree to prevent a bypass via prefix wrapping."""
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input(
                    "FOO=bar ~/.claude/scripts/marker.sh write code-review && git commit -m foo",
                    session_id=DEFAULT_TEST_SESSION_ID,
                ),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_bash_c_wrapped_marker_does_not_authorize(self, isolated_home, git_repo):
        """`bash -c '~/.claude/scripts/marker.sh write code-review' && git commit`
        wraps marker.sh in a subshell. Whether the inner marker.sh actually
        runs depends on subshell semantics; either way the outer command does
        not match the sanctioned chained shape, so the bypass must not fire."""
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input(
                    "bash -c '~/.claude/scripts/marker.sh write code-review' && git commit -m foo",
                    session_id=DEFAULT_TEST_SESSION_ID,
                ),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_heredoc_pipe_with_marker_text_does_not_authorize(
        self, isolated_home, git_repo
    ):
        """`cat <<EOF | bash\\n~/.claude/scripts/marker.sh write code-review\\nEOF`
        piped into a chain with `git commit` must not bypass the gate. The
        heredoc body text appears inside the command string but the outer
        shape (`cat | bash && ...`) is not a sanctioned chained form.
        Without anchoring at command start, the marker text in the heredoc
        body would trick a fragment walker into seeing a marker-write
        precedes the commit."""
        cmd = (
            "cat <<EOF | bash\n"
            "~/.claude/scripts/marker.sh write code-review\n"
            "EOF\n"
            "git commit -m foo"
        )
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input(cmd, session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_chained_skill_review_marker_does_not_authorize_code_review(
        self, isolated_home, git_repo
    ):
        """Chaining `marker.sh write skill-review` (wrong skill) before
        `git commit` must NOT authorize a code-review-gated commit. Each
        gate's bypass is scoped to its own skill name."""
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input(
                    "~/.claude/scripts/marker.sh write skill-review && git commit -m foo",
                    session_id=DEFAULT_TEST_SESSION_ID,
                ),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_marker_write_after_commit_does_not_authorize(self, isolated_home, git_repo):
        """In a hypothetical `git commit && marker.sh write code-review`, the
        marker write happens AFTER commit — too late. The bypass must only
        fire when the marker-write fragment precedes the commit fragment."""
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input(
                    "git commit -m foo && ~/.claude/scripts/marker.sh write code-review",
                    session_id=DEFAULT_TEST_SESSION_ID,
                ),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_quoted_marker_text_in_commit_message_does_not_authorize(
        self, isolated_home, git_repo
    ):
        """A literal `marker.sh write code-review` appearing inside a quoted
        commit message must NOT bypass the gate — the marker-write text has
        to be in a fragment that precedes the commit fragment, not embedded
        in the commit's own arguments."""
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input(
                    'git commit -m "marker.sh write code-review"',
                    session_id=DEFAULT_TEST_SESSION_ID,
                ),
                cwd=git_repo,
            )
            == "deny"
        )

