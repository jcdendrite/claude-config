"""Tests for plugins/issue-triage/hooks/deny-gh-mutation-during-triage.sh."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from helpers import HOOKS_DIR, bash_input, run_hook, run_hook_reason, write_input

_PLUGINS_DIR = HOOKS_DIR.parent.parent.parent / "plugins"
DENY_GH_MUTATION_HOOK = (
    _PLUGINS_DIR / "issue-triage" / "hooks" / "deny-gh-mutation-during-triage.sh"
)

TARGET_REPO = "foo/bar"


def _activate_marker(
    home: Path, session_id: str, repo_target: str | None = TARGET_REPO, pid: int | None = None
) -> Path:
    """Seed <config-dir>/.issue-triage-active.d/<session_id> (PID) plus its
    <session_id>.repo-target sibling — the exact layout marker.sh's
    `activate issue-triage <owner>/<repo>` writes. repo_target=None omits
    the sibling file entirely, for the missing-target edge case."""
    marker_dir = home / ".claude" / ".issue-triage-active.d"
    marker_dir.mkdir(parents=True, exist_ok=True)
    (marker_dir / session_id).write_text(str(pid if pid is not None else os.getpid()))
    if repo_target is not None:
        (marker_dir / f"{session_id}.repo-target").write_text(repo_target)
    return marker_dir


class TestMarkerNotLiveAllowsEverything:
    """Restriction polarity (unlike every other active-bypass marker in this
    repo): no live marker means this hook has nothing to check, so even a
    gh write subcommand passes through untouched."""

    def test_no_marker_allows_write_command(self, isolated_home):
        assert (
            run_hook(
                DENY_GH_MUTATION_HOOK,
                bash_input("gh issue close 5", session_id="no-marker-session"),
                home=isolated_home,
            )
            == "allow"
        )

    def test_dead_pid_marker_evicts_and_allows_write_command(self, isolated_home):
        """A dead-PID marker is evicted (orphan cleanup) and — because the
        polarity is inverted from every other active marker — the write
        command is then ALLOWED, not denied: an evicted marker means no
        triage run is in flight for this session, so nothing restricts it."""
        sid = "dead-pid-session"
        marker_dir = _activate_marker(isolated_home, sid, pid=99999999)
        assert (
            run_hook(
                DENY_GH_MUTATION_HOOK,
                bash_input("gh issue close 5", session_id=sid),
                home=isolated_home,
            )
            == "allow"
        )
        assert not (marker_dir / sid).exists(), "dead-PID marker must be evicted"

    def test_missing_session_id_allows_write_command(self, isolated_home):
        """No session_id in the payload means liveness can never be proven,
        so — again, inverted from require-respond-pr.sh's fail-closed-deny
        default — this hook allows rather than denies."""
        _activate_marker(isolated_home, "some-other-session")
        assert (
            run_hook(
                DENY_GH_MUTATION_HOOK,
                bash_input("gh issue close 5"),
                home=isolated_home,
            )
            == "allow"
        )

    def test_other_sessions_marker_does_not_restrict_this_session(self, isolated_home):
        """Session-scoped keying: session A's live marker must not leak its
        restriction onto session B's unrelated commands."""
        _activate_marker(isolated_home, "session-A")
        assert (
            run_hook(
                DENY_GH_MUTATION_HOOK,
                bash_input("gh issue close 5", session_id="session-B"),
                home=isolated_home,
            )
            == "allow"
        )


class TestNonGhCommandsAlwaysAllowed:
    """Disclosed scope limit: this hook pattern-matches only gh/gh
    api-shaped commands, live marker or not."""

    def test_non_gh_command_allowed_while_marker_live(self, isolated_home):
        sid = "non-gh-session"
        _activate_marker(isolated_home, sid)
        assert (
            run_hook(
                DENY_GH_MUTATION_HOOK,
                bash_input("ls -la", session_id=sid),
                home=isolated_home,
            )
            == "allow"
        )

    def test_non_gh_bash_write_to_marker_path_allowed_while_marker_live(self, isolated_home):
        """Accepted residual (see header comment's Scope section): this
        gate pattern-matches only gh-shaped Bash commands, so a non-gh Bash
        write to the marker path itself — e.g. printf redirected via `>` —
        is not denied. Only the first-class Write tool is gated for these
        two paths (see TestWriteGateOnMarkerFiles)."""
        sid = "non-gh-write-residual-session"
        marker_dir = _activate_marker(isolated_home, sid)
        marker_path = marker_dir / sid
        assert (
            run_hook(
                DENY_GH_MUTATION_HOOK,
                bash_input(f"printf x > {marker_path}", session_id=sid),
                home=isolated_home,
            )
            == "allow"
        )


class TestWriteSubcommandsDenied:
    """gh issue/label write verbs, and their REST/GraphQL equivalents, deny
    while the marker is live — regardless of on-target or off-target."""

    SID = "write-session"

    @pytest.mark.parametrize(
        "command",
        [
            "gh issue close 5",
            "gh issue edit 5 --add-label bug",
            "gh issue comment 5 --body hi",
            "gh issue reopen 5",
            "gh issue delete 5",
            "gh issue lock 5",
            "gh issue unlock 5",
            "gh issue transfer 5 other/repo",
            "gh issue pin 5",
            "gh issue unpin 5",
            "gh label create bug",
            "gh label edit bug --description updated",
            "gh label delete bug",
            "gh label clone other/repo",
            "gh --repo foo/bar issue close 5",
            "gh issue --repo foo/bar close 5",
            "gh issue close 5 -Rfoo/bar",
            "gh api repos/foo/bar/issues/5 -X PATCH -f state=closed",
            "gh api repos/foo/bar/issues/5/labels -f labels[]=bug",
            "gh api graphql -f query=mutation_addComment",
            "gh api graphql --input body.json",
        ],
    )
    def test_write_shaped_command_denied(self, isolated_home, command):
        _activate_marker(isolated_home, self.SID)
        assert (
            run_hook(
                DENY_GH_MUTATION_HOOK,
                bash_input(command, session_id=self.SID),
                home=isolated_home,
            )
            == "deny"
        )

    def test_deny_reason_names_report_only_scope(self, isolated_home):
        _activate_marker(isolated_home, self.SID)
        reason = run_hook_reason(
            DENY_GH_MUTATION_HOOK,
            bash_input("gh issue close 5", session_id=self.SID),
            home=isolated_home,
        )
        assert reason is not None
        assert "report-only" in reason


class TestMutatingMethodDeniedRegardlessOfCase:
    """Case-insensitive method fold: `gh` itself normalizes -X/--method
    before sending, so PATTERN_REST_ISSUE_PATH's else-branch wraps its
    match in `shopt -s nocasematch`, denying a lowercase or mixed-case
    spelling too. Every write-shaped command in TestWriteSubcommandsDenied
    also carries a field flag, so PATTERN_FIELD_FLAG trips GATED_WRITE on
    the if-arm before this fold ever runs — these cases isolate it: -X/
    --method present, no field flag, in each case spelling. Mirrors
    test_require_respond_pr.py's TestMutatingMethodDeniedRegardlessOfCase
    precedent for the sibling hook's identical fold."""

    SID = "method-fold-session"

    @pytest.mark.parametrize(
        "command",
        [
            "gh api repos/foo/bar/issues/5 -X PATCH",
            "gh api repos/foo/bar/issues/5 -x patch",
            "gh api repos/foo/bar/issues/5 --Method Delete",
        ],
    )
    def test_mutating_method_denied_without_field_flag(self, isolated_home, command):
        _activate_marker(isolated_home, self.SID)
        assert (
            run_hook(
                DENY_GH_MUTATION_HOOK,
                bash_input(command, session_id=self.SID),
                home=isolated_home,
            )
            == "deny"
        )


class TestBackslashContinuationFlattening:
    """COMMAND_FLAT collapses a backslash-newline continuation (joins with
    nothing) so a gh invocation wrapped across lines is still recognized —
    without it, grep/[[ =~ ]] matching is per-line and a wrapped command
    would slip every arm below (see the hook's own header comment). Mirrors
    test_require_respond_pr.py:283-320's precedent classes for the sibling
    hook's identical flatten step."""

    SID = "continuation-session"

    @pytest.mark.parametrize(
        "command",
        [
            # Continuation at an argument boundary.
            "gh issue \\\nclose 5",
            # Continuation mid-verb, early.
            "gh iss\\\nue close 5",
            # Continuation mid-verb, late.
            "gh issue clos\\\ne 5",
        ],
    )
    def test_wrapped_write_shape_denied(self, isolated_home, command):
        _activate_marker(isolated_home, self.SID)
        assert (
            run_hook(
                DENY_GH_MUTATION_HOOK,
                bash_input(command, session_id=self.SID),
                home=isolated_home,
            )
            == "deny"
        )

    @pytest.mark.parametrize(
        "command",
        [
            # Continuation at an argument boundary.
            "gh issue view 5 \\\n-R other/repo",
            # Continuation mid-path, early.
            "gh api \\\nrepos/other/repo/issues/5",
            # Continuation mid-path, late.
            "gh api repos/other/repo/is\\\nsues/5",
        ],
    )
    def test_wrapped_off_target_repo_denied(self, isolated_home, command):
        _activate_marker(isolated_home, self.SID)
        assert (
            run_hook(
                DENY_GH_MUTATION_HOOK,
                bash_input(command, session_id=self.SID),
                home=isolated_home,
            )
            == "deny"
        )

    def test_unrelated_commands_on_separate_lines_both_allowed(self, isolated_home):
        """Flattening must not over-fuse: two genuinely unrelated commands
        that each touch nothing gated stay allowed even joined by a bare
        newline."""
        _activate_marker(isolated_home, self.SID)
        assert (
            run_hook(
                DENY_GH_MUTATION_HOOK,
                bash_input("echo hello\necho world", session_id=self.SID),
                home=isolated_home,
            )
            == "allow"
        )


class TestReadsAllowedOnTarget:
    """A read against the run's own resolved repo — implicit or explicit —
    stays allowed while the marker is live."""

    SID = "read-session"

    @pytest.mark.parametrize(
        "command",
        [
            "gh issue view 5",
            "gh issue list",
            "gh api repos/foo/bar/issues/5",
            "gh issue view 5 -R foo/bar",
            "gh api repos/{owner}/{repo}/issues/5",
        ],
    )
    def test_on_target_read_allowed(self, isolated_home, command):
        _activate_marker(isolated_home, self.SID)
        assert (
            run_hook(
                DENY_GH_MUTATION_HOOK,
                bash_input(command, session_id=self.SID),
                home=isolated_home,
            )
            == "allow"
        )


class TestOffTargetDenied:
    """Repo-target confinement applies to reads as well as writes — the
    batch-evidence dispatch is confined to the run's own resolved repo."""

    SID = "off-target-session"

    @pytest.mark.parametrize(
        "command",
        [
            "gh issue view 5 -R other/repo",
            "gh api repos/other/repo/issues/5",
            "gh --repo other/repo issue list",
        ],
    )
    def test_off_target_command_denied(self, isolated_home, command):
        _activate_marker(isolated_home, self.SID)
        assert (
            run_hook(
                DENY_GH_MUTATION_HOOK,
                bash_input(command, session_id=self.SID),
                home=isolated_home,
            )
            == "deny"
        )

    def test_deny_reason_names_off_target_repo(self, isolated_home):
        _activate_marker(isolated_home, self.SID)
        reason = run_hook_reason(
            DENY_GH_MUTATION_HOOK,
            bash_input("gh issue view 5 -R other/repo", session_id=self.SID),
            home=isolated_home,
        )
        assert reason is not None
        assert "other/repo" in reason


class TestMissingRepoTargetFailsClosed:
    """If the .repo-target sibling is missing (a corrupted or incomplete
    activate), an explicit-repo command must still deny — comparing against
    an empty target can never match, so this fails closed rather than
    silently treating every repo as on-target."""

    SID = "missing-target-session"

    def test_explicit_repo_denied_when_target_file_absent(self, isolated_home):
        _activate_marker(isolated_home, self.SID, repo_target=None)
        assert (
            run_hook(
                DENY_GH_MUTATION_HOOK,
                bash_input("gh issue view 5 -R foo/bar", session_id=self.SID),
                home=isolated_home,
            )
            == "deny"
        )

    def test_write_command_still_denied_when_target_file_absent(self, isolated_home):
        """The write-shape check does not depend on the repo target at
        all, so it must still fire even with no .repo-target file."""
        _activate_marker(isolated_home, self.SID, repo_target=None)
        assert (
            run_hook(
                DENY_GH_MUTATION_HOOK,
                bash_input("gh issue close 5", session_id=self.SID),
                home=isolated_home,
            )
            == "deny"
        )

    def test_implicit_read_allowed_when_target_file_absent(self, isolated_home):
        """No explicit repo reference and no write signal: nothing to
        compare against and nothing gated, so this stays allowed."""
        _activate_marker(isolated_home, self.SID, repo_target=None)
        assert (
            run_hook(
                DENY_GH_MUTATION_HOOK,
                bash_input("gh issue view 5", session_id=self.SID),
                home=isolated_home,
            )
            == "allow"
        )


class TestWriteGateOnMarkerFiles:
    """S1 fix: a Write call targeting this run's own marker files is
    denied while the marker is live, closing the self-tampering path that
    would otherwise silently disarm this whole gate —
    _lib_active_bypass_marker_live (_lib.sh) evicts any marker whose
    content isn't a live PID, so an ungated Write to either file could
    fail-open the write prohibition and the repo-target confinement check
    alike for the rest of the session."""

    SID = "write-gate-session"

    def test_write_to_pid_marker_denied(self, isolated_home):
        marker_dir = _activate_marker(isolated_home, self.SID)
        marker_path = marker_dir / self.SID
        assert (
            run_hook(
                DENY_GH_MUTATION_HOOK,
                write_input(str(marker_path), session_id=self.SID),
                home=isolated_home,
            )
            == "deny"
        )

    def test_write_to_repo_target_sibling_denied(self, isolated_home):
        marker_dir = _activate_marker(isolated_home, self.SID)
        repo_target_path = marker_dir / f"{self.SID}.repo-target"
        assert (
            run_hook(
                DENY_GH_MUTATION_HOOK,
                write_input(str(repo_target_path), session_id=self.SID),
                home=isolated_home,
            )
            == "deny"
        )

    def test_write_to_unrelated_path_allowed_while_marker_live(self, isolated_home):
        _activate_marker(isolated_home, self.SID)
        assert (
            run_hook(
                DENY_GH_MUTATION_HOOK,
                write_input("/tmp/some/other/file.md", session_id=self.SID),
                home=isolated_home,
            )
            == "allow"
        )

    def test_write_to_marker_path_allowed_when_no_marker_live(self, isolated_home):
        sid = "write-gate-no-marker-session"
        marker_path = isolated_home / ".claude" / ".issue-triage-active.d" / sid
        assert (
            run_hook(
                DENY_GH_MUTATION_HOOK,
                write_input(str(marker_path), session_id=sid),
                home=isolated_home,
            )
            == "allow"
        )


class TestRepoTargetReadDoesNotHang:
    """A FIFO planted at the .repo-target path (via a non-gh Bash write,
    unrestricted by this hook's own gh-shaped matcher) must not hang the
    hook: the `[ -f ... ]` guard before the read rejects it as not a
    regular file, and _lib_capped backstops any read that does reach cat."""

    def test_fifo_at_repo_target_path_does_not_hang(self, isolated_home):
        sid = "fifo-session"
        marker_dir = _activate_marker(isolated_home, sid, repo_target=None)
        fifo_path = marker_dir / f"{sid}.repo-target"
        os.mkfifo(fifo_path)
        env = {**os.environ, "HOME": str(isolated_home)}
        try:
            # subprocess.run's own timeout is the hang-guard: a regression
            # that dropped the `[ -f ... ]` guard or the _lib_capped wrap
            # would raise TimeoutExpired here instead of hanging the test
            # suite indefinitely.
            result = subprocess.run(
                [str(DENY_GH_MUTATION_HOOK)],
                input=json.dumps(bash_input("gh issue view 5 -R foo/bar", session_id=sid)),
                env=env,
                capture_output=True,
                text=True,
                timeout=15,
            )
        finally:
            fifo_path.unlink()
        # Matches TestMissingRepoTargetFailsClosed's fail-closed behavior
        # for a missing .repo-target: an unreadable target never equals an
        # explicit -R value, so the explicit-repo command still denies.
        assert result.stdout.strip() != ""
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
