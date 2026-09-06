"""Tests for require-code-review.sh."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

import pytest
from helpers import (
    DEFAULT_TEST_SESSION_ID,
    HOOKS_DIR,
    SKILLS_DIR,
    bash_input,
    build_path_without,
    edit_input,
    extract_skill_command,
    git_toplevel,
    marker_path,
    run_hook,
    run_hook_reason,
    run_skill_command,
    staged_diff_hash,
    write_marker,
)

from .conftest import _seed_session

CODE_REVIEW_HOOK = HOOKS_DIR / "require-code-review.sh"
CODE_REVIEW_SKILL = SKILLS_DIR / "code-review" / "SKILL.md"


class TestRequireCodeReview:
    # The marker layout is ~/.claude/code-review-markers/<repo-hash>.<session_id>.
    # The hook allows when any marker under this repo-hash holds the
    # staged diff's hash, across every session suffix — the stored hash
    # is the authorization, not the filename. Tests below thread
    # session_id through `bash_input` and `write_marker` because the
    # write side still keys on it. Tests that exit early (non-bash tool,
    # non-commit command, outside-repo, empty staged diff) don't need
    # session_id — the hook returns before reaching the marker logic.

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
        _seed_session(isolated_home, sid)

        markers_dir = isolated_home / ".claude" / "code-review-markers"
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

    # ------------------------------------------------------------------ #
    # GH-783 Phase 2: quote-split and fail-closed status-2 regression      #
    # ------------------------------------------------------------------ #

    def test_quoted_form_reaches_same_verdict_as_bare_form(self, isolated_home, git_repo):
        """A quote-adjacent split (`"git" commit -m x`) must reach the same
        deny verdict as the unquoted form — the fragment matcher strips
        quote characters before word-walking, unlike a raw regex over
        unstripped $COMMAND."""
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input('"git" commit -m foo', session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
            )
            == "deny"
        )

    def test_sed_absent_from_path_denies(self, isolated_home, git_repo, tmp_path):
        """Status-2 propagation: the matcher could not determine whether
        this command invokes git commit, and this gate's own documented
        fail-closed posture means an undetermined match denies rather than
        silently falling through to allow. Asserts the distinguishing
        reason text, not just the verdict, so this test cannot be
        satisfied by an ordinary missing-review deny reaching "deny" for
        the wrong reason."""
        farm_dir = tmp_path / "path-without-sed"
        farm_dir.mkdir()
        restricted_path = build_path_without("sed", farm_dir)
        reason = run_hook_reason(
            CODE_REVIEW_HOOK,
            bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
            cwd=git_repo,
            extra_env={"PATH": restricted_path},
        )
        assert reason is not None
        assert "could not determine" in reason


class TestRequireCodeReviewHonorsConfigDir:
    """CLAUDE_CONFIG_DIR relocates the code-review marker directory the same
    way for marker.sh (write) and this hook (read) -- see marker.sh and the
    cross-account bypass this closes (ledger row 7)."""

    def test_marker_under_matching_config_dir_allows(self, isolated_home, git_repo, tmp_path):
        """CLAUDE_CONFIG_DIR-set happy path: a marker written under the
        resolved config dir satisfies the gate when the session runs under
        the same value."""
        profile = tmp_path / "profile"
        write_marker(
            isolated_home,
            git_repo,
            staged_diff_hash(git_repo),
            session_id=DEFAULT_TEST_SESSION_ID,
            config_dir=profile,
        )
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
                extra_env={"CLAUDE_CONFIG_DIR": str(profile)},
            )
            == "allow"
        )

    def test_marker_under_different_config_dir_does_not_authorize(
        self, isolated_home, git_repo, tmp_path
    ):
        """Cross-account bypass regression: a marker written under one
        CLAUDE_CONFIG_DIR value must not satisfy the gate when the session
        runs under a different one."""
        profile_a = tmp_path / "profile-a"
        profile_b = tmp_path / "profile-b"
        write_marker(
            isolated_home,
            git_repo,
            staged_diff_hash(git_repo),
            session_id=DEFAULT_TEST_SESSION_ID,
            config_dir=profile_a,
        )
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
                extra_env={"CLAUDE_CONFIG_DIR": str(profile_b)},
            )
            == "deny"
        )

    def test_unresolvable_config_dir_denies(self, isolated_home, git_repo):
        """Fail closed: a relative CLAUDE_CONFIG_DIR (unresolvable) must deny
        the gate outright, even with a valid marker at the default location."""
        write_marker(
            isolated_home,
            git_repo,
            staged_diff_hash(git_repo),
            session_id=DEFAULT_TEST_SESSION_ID,
        )
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
                extra_env={"CLAUDE_CONFIG_DIR": "relative/path"},
            )
            == "deny"
        )


class TestRequireCodeReviewComplianceLog:
    """Non-blocking `.review-ledger-compliance.log` line appended at both of
    this hook's exit paths. Never affects the gate's own decision."""

    COMPLIANCE_LOG = "review-ledger-compliance.log"

    def _log_path(self, isolated_home: Path) -> Path:
        return isolated_home / ".claude" / f".{self.COMPLIANCE_LOG}"

    def _ledger_file_path(self, isolated_home: Path, repo: Path, session_id: str) -> Path:
        repo_hash = hashlib.sha256(git_toplevel(repo).encode()).hexdigest()
        return (
            isolated_home
            / ".claude"
            / "review-narrative-ledger"
            / f"{repo_hash}.{session_id}.jsonl"
        )

    def test_log_line_appended_on_match(self, isolated_home, git_repo):
        write_marker(isolated_home, git_repo, staged_diff_hash(git_repo), session_id=DEFAULT_TEST_SESSION_ID)
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
            )
            == "allow"
        )
        lines = self._log_path(isolated_home).read_text().splitlines()
        assert len(lines) == 1
        assert "marker=matched" in lines[0]
        assert "ledger=absent" in lines[0]

    def test_log_line_appended_on_deny(self, isolated_home, git_repo):
        assert (
            run_hook(
                CODE_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
            )
            == "deny"
        )
        lines = self._log_path(isolated_home).read_text().splitlines()
        assert len(lines) == 1
        assert "marker=unmatched" in lines[0]
        assert "ledger=absent" in lines[0]

    def test_log_line_reports_ledger_present(self, isolated_home, git_repo):
        write_marker(isolated_home, git_repo, staged_diff_hash(git_repo), session_id=DEFAULT_TEST_SESSION_ID)
        ledger = self._ledger_file_path(isolated_home, git_repo, DEFAULT_TEST_SESSION_ID)
        ledger.parent.mkdir(parents=True)
        ledger.write_text('{"finding":"f","disposition":"ADDRESS","rationale":"r","source":"n/a"}\n')

        run_hook(
            CODE_REVIEW_HOOK,
            bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
            cwd=git_repo,
        )

        lines = self._log_path(isolated_home).read_text().splitlines()
        assert "ledger=present" in lines[0]

    def test_log_line_has_iso8601_timestamp(self, isolated_home, git_repo):
        run_hook(
            CODE_REVIEW_HOOK,
            bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
            cwd=git_repo,
        )
        line = self._log_path(isolated_home).read_text().splitlines()[0]
        timestamp = line.split(" ", 1)[0]
        # Raises ValueError (failing the test) if not a well-formed
        # UTC ISO-8601 timestamp of the form the hook writes.
        datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_exit_code_unaffected_by_log_write_failure(self, isolated_home, git_repo):
        """An unwritable config dir (log append fails) must not change the
        gate's own allow/deny decision."""
        write_marker(isolated_home, git_repo, staged_diff_hash(git_repo), session_id=DEFAULT_TEST_SESSION_ID)
        config_dir = isolated_home / ".claude"
        config_dir.chmod(0o555)
        try:
            decision = run_hook(
                CODE_REVIEW_HOOK,
                bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID),
                cwd=git_repo,
            )
        finally:
            config_dir.chmod(0o755)
        assert decision == "allow"

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_unwritable_log_dir_does_not_hang(self, isolated_home, git_repo):
        """_lib_capped's timeout wraps the compliance-log append; an
        unwritable directory must fail fast rather than hang the gate.
        subprocess.run's own timeout is the test's hang-guard: a regression
        that reintroduced a blocking write would raise TimeoutExpired here
        instead of hanging the test suite indefinitely."""
        config_dir = isolated_home / ".claude"
        config_dir.chmod(0o555)
        env = {**os.environ, "HOME": str(isolated_home)}
        try:
            result = subprocess.run(
                [str(CODE_REVIEW_HOOK)],
                input=json.dumps(bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID)),
                cwd=git_repo,
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )
        finally:
            config_dir.chmod(0o755)
        # _lib_emit_deny prints a JSON deny payload and returns 0 rather than
        # calling exit 2 itself (see run_hook's own docstring) — parse the
        # payload the same way run_hook does rather than asserting exit code.
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny", (
            "no marker exists, so the gate must still deny"
        )
        assert not self._log_path(isolated_home).exists()

    def test_fifo_compliance_log_does_not_hang(self, isolated_home, git_repo):
        """A compliance-log target whose `>>` open() itself blocks (no
        reader) is the genuine hang this gate must be protected against —
        distinct from test_unwritable_log_dir_does_not_hang above, which
        only proves the EACCES case fails fast and cannot tell "fails fast"
        apart from "actually timeout-protected". subprocess.run's own
        timeout is the hang-guard: a regression that reintroduced an
        unprotected `>>` redirect would raise TimeoutExpired here instead of
        hanging the test suite indefinitely."""
        write_marker(
            isolated_home, git_repo, staged_diff_hash(git_repo), session_id=DEFAULT_TEST_SESSION_ID
        )
        log_path = self._log_path(isolated_home)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        os.mkfifo(log_path)
        env = {**os.environ, "HOME": str(isolated_home)}
        try:
            result = subprocess.run(
                [str(CODE_REVIEW_HOOK)],
                input=json.dumps(bash_input("git commit -m foo", session_id=DEFAULT_TEST_SESSION_ID)),
                cwd=git_repo,
                env=env,
                capture_output=True,
                text=True,
                timeout=15,
            )
        finally:
            log_path.unlink()
        # A matched marker's allow path is silent (empty stdout, exit 0) —
        # see run_hook's own docstring for this same empty-stdout mapping.
        assert result.stdout.strip() == "" and result.returncode == 0, (
            f"a matching marker exists, so the gate must still silently "
            f"allow despite the compliance-log append being unable to "
            f"complete; got returncode={result.returncode}, "
            f"stdout={result.stdout!r}, stderr={result.stderr!r}"
        )

