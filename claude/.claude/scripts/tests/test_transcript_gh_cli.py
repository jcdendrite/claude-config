"""Tests for transcript_analysis/gh_cli.py (gh stderr classification, rate-limit backoff, auth
preflight, effective-repo pinning, and merged/closed PR discovery)."""
import json
import os
import re
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from helpers import HOOKS_DIR
from transcript_analysis import (
    gh_cli,
    pr_cost,
    pr_cost_ledger,
    scope,
)

from ._pr_cost_helpers import _enable_pr_cost, _fake_pr_cost_subprocess_run, _pr_cost_args
from .conftest import _commit, _init_repo


# ---------------------------------------------------------------------------
# pr-cost -- gh-integration coverage: gh failure classification and backoff,
# gh's effective-repo-identity pinning (_resolve_pinned_gh_repo), cross-repo
# ledger isolation, and redaction of every gh-integration print site.
# ---------------------------------------------------------------------------
class TestGhDiscoverMergedPrsPagination:
    def test_passes_explicit_limit_well_above_ghs_own_default(self, monkeypatch):
        """gh pr list's own default (30) silently truncates a larger
        population with no error -- _gh_discover_merged_prs must always pass
        an explicit --limit."""
        captured: dict = {}

        def fake_run(cmd, *a, **kw):
            captured["cmd"] = cmd
            return type("R", (), {"returncode": 0, "stdout": "[]", "stderr": ""})()

        monkeypatch.setattr(subprocess, "run", fake_run)
        gh_cli._gh_discover_merged_prs("github.com", "owner/repo")

        argv = captured["cmd"]
        assert "--limit" in argv
        limit_value = int(argv[argv.index("--limit") + 1])
        assert limit_value == gh_cli._PR_COST_GH_PR_LIST_LIMIT
        assert limit_value > 30  # gh pr list's own truncating default


class TestGhDiscoverClosedUnmergedPrBranches:
    """_gh_discover_closed_unmerged_pr_branches: same gh pr list call shape
    as _gh_discover_merged_prs, --state closed instead of --state merged --
    workstream-cost's own sibling discovery call."""

    def test_passes_state_closed_and_explicit_limit(self, monkeypatch):
        captured: dict = {}

        def fake_run(cmd, *a, **kw):
            captured["cmd"] = cmd
            return type("R", (), {"returncode": 0, "stdout": "[]", "stderr": ""})()

        monkeypatch.setattr(subprocess, "run", fake_run)
        gh_cli._gh_discover_closed_unmerged_pr_branches("github.com", "owner/repo")

        argv = captured["cmd"]
        assert argv[argv.index("--state") + 1] == "closed"
        assert "--limit" in argv
        assert int(argv[argv.index("--limit") + 1]) == gh_cli._PR_COST_GH_PR_LIST_LIMIT

    def test_returns_headref_name_set_from_closed_prs(self, monkeypatch):
        """Returns a set of branch names (headRefName), not the raw PR dict
        list _gh_discover_merged_prs returns -- workstream-cost only needs
        set membership for its merged/closed-unmerged/no-match classification."""
        payload = [
            {"number": 1, "headRefName": "abandoned-a"},
            {"number": 2, "headRefName": "abandoned-b"},
        ]
        monkeypatch.setattr(
            subprocess, "run",
            lambda cmd, *a, **kw: type("R", (), {"returncode": 0, "stdout": json.dumps(payload), "stderr": ""})(),
        )
        result = gh_cli._gh_discover_closed_unmerged_pr_branches("github.com", "owner/repo")
        assert result == {"abandoned-a", "abandoned-b"}

    def test_entry_with_no_headref_name_filtered_out_of_result(self, monkeypatch):
        """An entry with no headRefName key (or an empty one) is dropped
        from the returned set -- mirrors the `if pr.get("headRefName")`
        truthy filter guarding each entry."""
        payload = [
            {"number": 1, "headRefName": "abandoned-a"},
            {"number": 2},
            {"number": 3, "headRefName": ""},
        ]
        monkeypatch.setattr(
            subprocess, "run",
            lambda cmd, *a, **kw: type("R", (), {"returncode": 0, "stdout": json.dumps(payload), "stderr": ""})(),
        )
        result = gh_cli._gh_discover_closed_unmerged_pr_branches("github.com", "owner/repo")
        assert result == {"abandoned-a"}

    def test_gh_call_failure_aborts_with_exit_1_not_a_partial_result(self, monkeypatch, capsys):
        """A failed gh pr list (closed) call aborts the whole run via
        _pr_cost_abort_on_gh_failure. Discovery has no per-row granularity
        to degrade into, so it does not return a partial or empty set
        silently."""
        def fake_run(cmd, *a, **kw):
            return type("R", (), {
                "returncode": 1, "stdout": "", "stderr": "not logged into any GitHub hosts\n",
            })()

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(SystemExit) as exc_info:
            gh_cli._gh_discover_closed_unmerged_pr_branches("github.com", "owner/repo")

        assert exc_info.value.code == 1
        assert "gh pr list (closed) failed" in capsys.readouterr().err

    def test_malformed_json_stdout_aborts_with_exit_1_not_a_partial_result(self, monkeypatch, capsys):
        """A successful gh call (returncode 0) whose stdout is not valid
        JSON aborts via sys.exit(1) rather than raising JSONDecodeError
        uncaught or returning a partial/empty set silently."""
        def fake_run(cmd, *a, **kw):
            return type("R", (), {"returncode": 0, "stdout": "not json", "stderr": ""})()

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(SystemExit) as exc_info:
            gh_cli._gh_discover_closed_unmerged_pr_branches("github.com", "owner/repo")

        assert exc_info.value.code == 1
        assert "unparseable JSON" in capsys.readouterr().err


class TestClassifyGhError:
    @pytest.mark.parametrize("stderr", [
        "You are not logged into any GitHub hosts. Run gh auth login to authenticate.\n",
        "authentication failed for repository 'https://github.com/owner/repo/'\n",
        "HTTP 401: Bad credentials\n",
    ])
    def test_auth_shaped_stderr_classified_as_auth(self, stderr):
        assert gh_cli._classify_gh_error(stderr) == gh_cli._GH_ERROR_KIND_AUTH

    @pytest.mark.parametrize("stderr", [
        # Verified against gh 2.97.0's own stderr for an ambient GH_HOST that
        # doesn't match any configured git remote.
        "none of the git remotes configured for this repository correspond to the"
        " GH_HOST environment variable. Try adding a matching remote or unsetting"
        " the variable\n",
    ])
    def test_gh_host_mismatch_stderr_classified_as_host_mismatch(self, stderr):
        assert gh_cli._classify_gh_error(stderr) == gh_cli._GH_ERROR_KIND_HOST_MISMATCH

    @pytest.mark.parametrize("stderr", [
        "API rate limit exceeded for user ID 123.\n",
        "HTTP 429: Too Many Requests\n",
        "HTTP 403: Forbidden\n",
    ])
    def test_rate_limit_shaped_stderr_classified_as_rate_limit(self, stderr):
        assert gh_cli._classify_gh_error(stderr) == gh_cli._GH_ERROR_KIND_RATE_LIMIT

    @pytest.mark.parametrize("stderr", [
        "curl: (6) Could not resolve host: api.github.com\n",
        "connection reset by peer\n",
        "\n",  # unrecognized/empty stderr falls back to network, not a crash
    ])
    def test_unrecognized_stderr_falls_back_to_network(self, stderr):
        assert gh_cli._classify_gh_error(stderr) == gh_cli._GH_ERROR_KIND_NETWORK


class TestGitRemoteOriginHostAndOwnerRepoRegex:
    """_git_remote_origin_host_and_owner_repo / _GIT_REMOTE_HOST_OWNER_REPO_RE:
    every github.com and GitHub Enterprise remote URL shape git/gh support,
    plus the substring-spoofing attack named in the regex's own comment."""

    @pytest.mark.parametrize("remote_url,expected", [
        ("https://github.com/owner/repo.git", ("github.com", "owner/repo")),
        ("https://github.com/owner/repo", ("github.com", "owner/repo")),
        ("git@github.com:owner/repo.git", ("github.com", "owner/repo")),
        ("ssh://git@github.com/owner/repo.git", ("github.com", "owner/repo")),
        ("https://acme-corp.ghe.com/owner/repo.git", ("acme-corp.ghe.com", "owner/repo")),
        ("git@acme-corp.ghe.com:owner/repo.git", ("acme-corp.ghe.com", "owner/repo")),
        ("https://Acme-Corp.GHE.com/owner/repo.git", ("acme-corp.ghe.com", "owner/repo")),
    ])
    def test_recognized_remote_shapes_resolve_to_host_and_owner_repo(self, monkeypatch, remote_url, expected):
        monkeypatch.setattr(
            subprocess, "run",
            lambda cmd, *a, **kw: type("R", (), {"returncode": 0, "stdout": remote_url + "\n", "stderr": ""})(),
        )
        assert gh_cli._git_remote_origin_host_and_owner_repo() == expected

    def test_mixed_case_owner_and_repo_come_back_lowercased(self, monkeypatch):
        """Owner and repo are lowercased along with the host; the parametrized test above only covers host lowercasing."""
        monkeypatch.setattr(
            subprocess, "run",
            lambda cmd, *a, **kw: type("R", (), {
                "returncode": 0, "stdout": "https://GitHub.com/Owner-Name/Repo-Name.git\n", "stderr": "",
            })(),
        )

        assert gh_cli._git_remote_origin_host_and_owner_repo() == ("github.com", "owner-name/repo-name")

    def test_default_args_prefix_failure_with_pr_cost_and_no_repo_hint(self, monkeypatch, capsys):
        """Calls _git_remote_origin_host_and_owner_repo with no arguments --
        pr-cost's own call shape -- against an unparseable origin. Confirms
        the new subcommand/failure_hint params don't change this default
        path: the message stays prefixed "pr-cost:" with no --repo hint."""
        monkeypatch.setattr(
            subprocess, "run",
            lambda cmd, *a, **kw: type("R", (), {"returncode": 0, "stdout": "not a remote\n", "stderr": ""})(),
        )
        with pytest.raises(SystemExit) as exc_info:
            gh_cli._git_remote_origin_host_and_owner_repo()
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert err.startswith("pr-cost:")
        assert "--repo" not in err

    def test_default_args_prefix_failure_when_origin_remote_unresolvable(self, monkeypatch, capsys):
        """Calls _git_remote_origin_host_and_owner_repo with no arguments --
        pr-cost's own call shape -- where `git remote get-url origin` itself
        fails. Confirms the "could not resolve this repo's own remote"
        branch, distinct from the regex-mismatch branch covered above, also
        stays prefixed "pr-cost:" with no --repo hint."""

        def fake_run(cmd, *a, **kw):
            raise subprocess.CalledProcessError(128, cmd)

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(SystemExit) as exc_info:
            gh_cli._git_remote_origin_host_and_owner_repo()
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert err.startswith("pr-cost:")
        assert "--repo" not in err

    def test_attacker_substring_shape_does_not_resolve(self, monkeypatch, capsys):
        """A malicious/misconfigured remote embedding "github.com/owner/repo"
        as a path segment on a different host must not spoof the real
        identity -- the exact shape named in _GIT_REMOTE_HOST_OWNER_REPO_RE's
        own comment. The 4-segment shape (host/github.com/owner/repo) stays
        unrecognized regardless of which host name appears in the spoofed
        segment."""
        monkeypatch.setattr(
            subprocess, "run",
            lambda cmd, *a, **kw: type("R", (), {
                "returncode": 0, "stdout": "https://attacker.example/github.com/owner/repo\n", "stderr": "",
            })(),
        )
        with pytest.raises(SystemExit) as exc_info:
            gh_cli._git_remote_origin_host_and_owner_repo()
        assert exc_info.value.code == 1
        assert "not a recognizable host/owner/repo URL" in capsys.readouterr().err

    def test_attacker_substring_shape_on_ghe_host_does_not_resolve(self, monkeypatch, capsys):
        """The same 4-segment spoofing shape, but with a GHE host as the
        spoofed segment instead of github.com. The host capture is a
        character class with no host-specific branching, so this doesn't
        guard a distinct failure mode from
        test_attacker_substring_shape_does_not_resolve -- it's here to
        confirm the anchoring invariant isn't accidentally github.com-specific."""
        monkeypatch.setattr(
            subprocess, "run",
            lambda cmd, *a, **kw: type("R", (), {
                "returncode": 0, "stdout": "https://attacker.example/acme-corp.ghe.com/owner/repo\n", "stderr": "",
            })(),
        )
        with pytest.raises(SystemExit) as exc_info:
            gh_cli._git_remote_origin_host_and_owner_repo()
        assert exc_info.value.code == 1
        assert "not a recognizable host/owner/repo URL" in capsys.readouterr().err

    def test_host_with_disallowed_character_does_not_resolve(self, monkeypatch, capsys):
        """A host segment containing a character outside the host capture's
        `[A-Za-z0-9.-]+` class (e.g. an underscore) must not silently
        mis-capture into a shorter, valid-looking host/owner/repo split."""
        monkeypatch.setattr(
            subprocess, "run",
            lambda cmd, *a, **kw: type("R", (), {
                "returncode": 0, "stdout": "https://internal_host/owner/repo\n", "stderr": "",
            })(),
        )
        with pytest.raises(SystemExit) as exc_info:
            gh_cli._git_remote_origin_host_and_owner_repo()
        assert exc_info.value.code == 1
        assert "not a recognizable host/owner/repo URL" in capsys.readouterr().err

    def test_host_with_port_does_not_resolve(self, monkeypatch, capsys):
        """The host capture's `[A-Za-z0-9.-]+` class has no port syntax, so a
        port-bearing remote (a real GHE deployment shape, e.g. behind a
        reverse proxy) fails to parse and aborts rather than mis-splitting
        the port into the owner/repo capture groups."""
        monkeypatch.setattr(
            subprocess, "run",
            lambda cmd, *a, **kw: type("R", (), {
                "returncode": 0, "stdout": "ssh://git@acme-corp.ghe.com:2222/owner/repo\n", "stderr": "",
            })(),
        )
        with pytest.raises(SystemExit) as exc_info:
            gh_cli._git_remote_origin_host_and_owner_repo()
        assert exc_info.value.code == 1
        assert "not a recognizable host/owner/repo URL" in capsys.readouterr().err

    def test_ipv6_literal_host_does_not_resolve(self, monkeypatch, capsys):
        """The host capture's `[A-Za-z0-9.-]+` class also excludes the
        bracket/colon syntax of a bracketed IPv6-literal remote, so it fails
        to parse and aborts rather than mis-splitting the literal into the
        owner/repo capture groups -- same fail-closed shape as the port gap
        above, via an independently-necessary exclusion (brackets/colons),
        not a restatement of the port test's coverage."""
        monkeypatch.setattr(
            subprocess, "run",
            lambda cmd, *a, **kw: type("R", (), {
                "returncode": 0, "stdout": "https://[::1]/owner/repo\n", "stderr": "",
            })(),
        )
        with pytest.raises(SystemExit) as exc_info:
            gh_cli._git_remote_origin_host_and_owner_repo()
        assert exc_info.value.code == 1
        assert "not a recognizable host/owner/repo URL" in capsys.readouterr().err


class TestGhAuthPreflightOkHostnameScoping:
    """_gh_auth_preflight_ok: scopes `gh auth status` to the given hostname
    instead of running a bare aggregate-host check -- a bare check treats
    every host gh has ever held credentials for as relevant and fails on a
    GHE-only token merely because GH_TOKEN triggers a speculative
    github.com check too."""

    def test_passes_hostname_flag_to_gh_auth_status(self, monkeypatch):
        call_log: list[list[str]] = []

        def fake_run(cmd, *a, **kw):
            call_log.append(cmd)
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert gh_cli._gh_auth_preflight_ok("acme-corp.ghe.com") is True
        assert call_log == [["gh", "auth", "status", "--hostname", "acme-corp.ghe.com"]]

    def test_nonzero_exit_returns_false(self, monkeypatch):
        monkeypatch.setattr(
            subprocess, "run",
            lambda cmd, *a, **kw: type("R", (), {"returncode": 1, "stdout": "", "stderr": "error\n"})(),
        )
        assert gh_cli._gh_auth_preflight_ok("github.com") is False

    @pytest.mark.parametrize("raised", [
        subprocess.TimeoutExpired(cmd=["gh", "auth", "status"], timeout=1),
        OSError("gh not found"),
    ])
    def test_timeout_or_os_error_returns_false(self, monkeypatch, raised):
        def fake_run(cmd, *a, **kw):
            raise raised

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert gh_cli._gh_auth_preflight_ok("github.com") is False


class TestGhCallWithBackoffFailureClassBehavior:
    """_gh_call_with_backoff's own retry/no-retry split by _classify_gh_error
    kind -- auth never retries, rate-limit/network do (whether the failure
    is stderr-text-shaped or a raised exception)."""

    def test_auth_shaped_failure_returns_immediately_with_no_retry_or_sleep(self, monkeypatch):
        call_count = 0

        def fake_run(cmd, *a, **kw):
            nonlocal call_count
            call_count += 1
            return type("R", (), {
                "returncode": 1, "stdout": "", "stderr": "not logged into any GitHub hosts\n",
            })()

        sleep_calls: list[float] = []
        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(time, "sleep", lambda s: sleep_calls.append(s))

        proc, degraded = gh_cli._gh_call_with_backoff(["gh", "repo", "view"], label="repo view")

        assert proc is None
        assert degraded == gh_cli._GH_CALL_DEGRADED_AUTH
        assert call_count == 1
        assert sleep_calls == []

    def test_gh_host_mismatch_failure_returns_immediately_with_actionable_message(self, monkeypatch, capsys):
        """Distinct from the generic network-failure path (which this
        stderr shape would otherwise be misclassified into, burning the
        full retry budget on a failure that can't self-resolve): no retry,
        and the abort message names the actual fix (unset/correct GH_HOST)
        without echoing gh's own raw stderr."""
        call_count = 0
        gh_stderr = (
            "none of the git remotes configured for this repository correspond to the"
            " GH_HOST environment variable. Try adding a matching remote or unsetting"
            " the variable\n"
        )

        def fake_run(cmd, *a, **kw):
            nonlocal call_count
            call_count += 1
            return type("R", (), {"returncode": 1, "stdout": "", "stderr": gh_stderr})()

        sleep_calls: list[float] = []
        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(time, "sleep", lambda s: sleep_calls.append(s))

        proc, degraded = gh_cli._gh_call_with_backoff(["gh", "repo", "view"], label="repo view")

        assert proc is None
        assert degraded == gh_cli._GH_CALL_DEGRADED_HOST_MISMATCH
        assert call_count == 1
        assert sleep_calls == []
        err = capsys.readouterr().err
        assert "GH_HOST" in err
        assert "unset" in err
        assert gh_stderr.strip() not in err  # gh's own raw stderr text is never surfaced

    def test_rate_limit_shaped_failure_retries_before_returning_degraded(self, monkeypatch):
        call_count = 0

        def fake_run(cmd, *a, **kw):
            nonlocal call_count
            call_count += 1
            return type("R", (), {
                "returncode": 1, "stdout": "", "stderr": "API rate limit exceeded (HTTP 403)\n",
            })()

        sleep_calls: list[float] = []
        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(time, "sleep", lambda s: sleep_calls.append(s))

        proc, degraded = gh_cli._gh_call_with_backoff(["gh", "repo", "view"], label="repo view")

        assert proc is None
        assert degraded == pr_cost_ledger._PR_COST_STATUS_DEGRADED_RATE_LIMIT
        assert call_count == gh_cli._PR_COST_RATE_LIMIT_MAX_ATTEMPTS
        assert sleep_calls  # retried at least once before exhausting

    def test_network_shaped_exception_retries_before_returning_degraded(self, monkeypatch):
        """A raised TimeoutExpired (no stderr text at all, unlike the two
        cases above) is still classified network-kind and retried under the
        same budget."""
        call_count = 0

        def fake_run(cmd, *a, **kw):
            nonlocal call_count
            call_count += 1
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=gh_cli._GH_CALL_TIMEOUT_S)

        sleep_calls: list[float] = []
        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(time, "sleep", lambda s: sleep_calls.append(s))

        proc, degraded = gh_cli._gh_call_with_backoff(["gh", "repo", "view"], label="repo view")

        assert proc is None
        assert degraded == pr_cost_ledger._PR_COST_STATUS_DEGRADED_NETWORK
        assert call_count == gh_cli._PR_COST_RATE_LIMIT_MAX_ATTEMPTS
        assert sleep_calls

    def test_network_shaped_failure_succeeds_after_two_retries(self, monkeypatch):
        """Neither of the two paths above proves the success-after-retry
        path itself works -- only zero-retry success (elsewhere) and full
        exhaustion (above) are covered without this test."""
        call_count = 0

        def fake_run(cmd, *a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=gh_cli._GH_CALL_TIMEOUT_S)
            return type("R", (), {"returncode": 0, "stdout": '{"ok": true}', "stderr": ""})()

        sleep_calls: list[float] = []
        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(time, "sleep", lambda s: sleep_calls.append(s))

        proc, degraded = gh_cli._gh_call_with_backoff(["gh", "repo", "view"], label="repo view")

        assert degraded == ""
        assert proc is not None
        assert proc.stdout == '{"ok": true}'
        assert call_count == 3
        assert len(sleep_calls) == 2


class TestGhCallWithBackoffElapsedBudgetCap:
    """_gh_call_with_backoff exhausts on whichever of its two bounds
    (_PR_COST_RATE_LIMIT_MAX_ATTEMPTS, _PR_COST_RATE_LIMIT_MAX_ELAPSED_S) is
    hit first -- the default doubling sequence hits the attempts bound
    first; a huge "retry after" hint (capped per-sleep) hits the elapsed
    bound first instead."""

    def test_default_backoff_exhausts_at_max_attempts_with_expected_sleep_sequence(self, monkeypatch):
        def fake_run(cmd, *a, **kw):
            return type("R", (), {
                "returncode": 1, "stdout": "", "stderr": "API rate limit exceeded (HTTP 403)\n",
            })()

        sleep_calls: list[float] = []
        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(time, "sleep", lambda s: sleep_calls.append(s))

        proc, degraded = gh_cli._gh_call_with_backoff(["gh", "pr", "view", "1"], label="pr view 1")

        assert proc is None
        assert degraded == pr_cost_ledger._PR_COST_STATUS_DEGRADED_RATE_LIMIT
        assert sleep_calls == [60.0, 120.0, 240.0, 480.0]
        assert sum(sleep_calls) == gh_cli._PR_COST_RATE_LIMIT_MAX_ELAPSED_S

    def test_malformed_huge_retry_after_hint_caps_sleep_to_remaining_elapsed_budget(self, monkeypatch):
        """A huge "retry after" hint from gh's own stderr is capped to the
        remaining elapsed budget, not passed through raw; exhaustion here
        fires via the elapsed bound, not the attempts bound."""
        call_count = 0

        def fake_run(cmd, *a, **kw):
            nonlocal call_count
            call_count += 1
            return type("R", (), {
                "returncode": 1, "stdout": "",
                "stderr": "secondary rate limit hit, retry after: 999999 seconds\n",
            })()

        sleep_calls: list[float] = []
        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(time, "sleep", lambda s: sleep_calls.append(s))

        proc, degraded = gh_cli._gh_call_with_backoff(["gh", "pr", "view", "1"], label="pr view 1")

        assert proc is None
        assert degraded == pr_cost_ledger._PR_COST_STATUS_DEGRADED_RATE_LIMIT
        assert call_count == 2
        assert sleep_calls == [gh_cli._PR_COST_RATE_LIMIT_MAX_ELAPSED_S]  # capped, not the raw 999999 value


class TestResolvePinnedGhRepoIdentity:
    """_resolve_pinned_gh_repo: refuses a genuine identity mismatch between
    gh's own effective repo and this repo's git remote -- on host or on
    owner/name -- case-folds a matching identity, and never prints a raw
    repo value."""

    @pytest.mark.parametrize("corpus_host,corpus_repo", [
        ("", "owner/repo"), ("github.com", ""), ("", ""),
    ])
    def test_empty_corpus_host_or_repo_raises_rather_than_silently_matching(
        self, monkeypatch, corpus_host, corpus_repo,
    ):
        """Pins the invariant the mismatch check's gh_host="" fail-closed
        default relies on: an empty corpus_host/corpus_repo must never
        reach the comparison, where it could coincidentally equal an
        unparseable gh_url's own empty-string fallback and skip the
        refusal. No gh call should even be attempted -- subprocess.run is
        stubbed to raise if called, rather than left unmocked, since a real
        `gh` binary on PATH would otherwise run to completion and enter
        _gh_call_with_backoff's real retry loop instead of raising."""
        def boom(cmd, *a, **k):
            raise AssertionError("gh must not be called when corpus_host/corpus_repo is empty")
        monkeypatch.setattr(subprocess, "run", boom)

        with pytest.raises(ValueError, match="non-empty corpus_host and corpus_repo"):
            gh_cli._resolve_pinned_gh_repo(corpus_host, corpus_repo, ordinal=1)

    def test_mismatch_exits_2_with_neither_raw_value_in_output(self, monkeypatch, capsys):
        """The two capsys assertions below confirm the refusal message
        itself never leaks either raw repo identity."""
        gh_repo = "gh-side-owner/gh-side-repo"
        corpus_repo = "git-side-owner/git-side-repo"
        monkeypatch.setattr(
            subprocess, "run",
            _fake_pr_cost_subprocess_run(repo=corpus_repo, gh_repo_name_with_owner=gh_repo),
        )

        with pytest.raises(SystemExit) as exc_info:
            gh_cli._resolve_pinned_gh_repo("github.com", corpus_repo, ordinal=1)

        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert gh_repo not in err
        assert corpus_repo not in err
        assert "account-1/repo-1" in err
        assert "account-1/repo-2" in err

    def test_host_mismatch_with_matching_owner_repo_exits_2(self, monkeypatch, capsys):
        """A same-named repo on a different host (e.g. an org mid-migration
        from a GHE instance to github.com) must not false-positive as a
        match -- gh's `nameWithOwner` alone can't tell the two apart, so the
        check must also compare the host `gh repo view`'s own `url` field
        resolves to."""
        same_owner_repo = "owner/repo"
        # _resolve_pinned_gh_repo is called directly below (not via the full
        # _pr_cost_report orchestration), so only the `gh repo view` response
        # this fake builds matters -- repo/host (which drive the unused
        # `git remote get-url origin` branch) are left at their defaults.
        monkeypatch.setattr(
            subprocess, "run",
            _fake_pr_cost_subprocess_run(
                gh_repo_name_with_owner=same_owner_repo, gh_repo_view_host="github.com",
            ),
        )

        with pytest.raises(SystemExit) as exc_info:
            gh_cli._resolve_pinned_gh_repo("acme-corp.ghe.com", same_owner_repo, ordinal=1)

        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert same_owner_repo not in err
        assert "acme-corp.ghe.com" not in err
        assert "github.com" not in err  # the gh-side host, symmetric with the corpus-side check above

    def test_gh_repo_view_url_not_matching_regex_refuses_rather_than_false_matching(self, monkeypatch, capsys):
        """`gh repo view`'s `url` field failing to parse (a future `gh`
        output-shape change, or a URL form _GIT_REMOTE_HOST_OWNER_REPO_RE
        doesn't anticipate) must fail closed -- refuse the identity check --
        rather than silently treat the unparseable host as matching."""
        monkeypatch.setattr(
            subprocess, "run",
            lambda cmd, *a, **kw: type("R", (), {
                "returncode": 0,
                "stdout": json.dumps({"nameWithOwner": "owner/repo", "url": "not-a-parseable-url"}),
                "stderr": "",
            })(),
        )

        with pytest.raises(SystemExit) as exc_info:
            gh_cli._resolve_pinned_gh_repo("github.com", "owner/repo", ordinal=1)

        assert exc_info.value.code == 2
        assert "owner/repo" not in capsys.readouterr().err

    def test_gh_repo_view_url_substring_spoof_shape_refuses_rather_than_false_matching(self, monkeypatch, capsys):
        """Mirrors TestGitRemoteOriginHostAndOwnerRepoRegex's substring-spoof
        cases, but at the `gh repo view` `url`-field parse site instead of
        the local git remote parse site -- both share one compiled regex
        object today, but nothing pinned that this site resists the same
        attack shape until now. A malicious/misconfigured `url` embedding
        "github.com/owner/repo" as a path segment on a different host must
        not spoof the real identity."""
        monkeypatch.setattr(
            subprocess, "run",
            lambda cmd, *a, **kw: type("R", (), {
                "returncode": 0,
                "stdout": json.dumps({
                    "nameWithOwner": "owner/repo",
                    "url": "https://attacker.example/github.com/owner/repo",
                }),
                "stderr": "",
            })(),
        )

        with pytest.raises(SystemExit) as exc_info:
            gh_cli._resolve_pinned_gh_repo("github.com", "owner/repo", ordinal=1)

        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert "owner/repo" not in err
        assert "attacker.example" not in err

    @pytest.mark.parametrize("missing_key", ["nameWithOwner", "url"])
    def test_gh_repo_view_payload_missing_required_key_exits_1(self, monkeypatch, capsys, missing_key):
        """The except (JSONDecodeError, KeyError, TypeError) branch aborts
        the whole run (exit 1, distinct from the identity-mismatch exit 2)
        when `gh repo view`'s JSON is missing either key it now requires --
        never letting an unhandled KeyError propagate as a raw traceback."""
        payload = {"nameWithOwner": "owner/repo", "url": "https://github.com/owner/repo"}
        del payload[missing_key]
        monkeypatch.setattr(
            subprocess, "run",
            lambda cmd, *a, **kw: type("R", (), {
                "returncode": 0, "stdout": json.dumps(payload), "stderr": "",
            })(),
        )

        with pytest.raises(SystemExit) as exc_info:
            gh_cli._resolve_pinned_gh_repo("github.com", "owner/repo", ordinal=1)

        assert exc_info.value.code == 1
        assert "unparseable JSON" in capsys.readouterr().err

    def test_case_differing_host_still_matches(self, monkeypatch):
        """Mirrors test_case_differing_match_proceeds_and_persists_the_pinned_lowercased_identity
        below, but on the host axis instead of the repo axis -- the
        docstring's "host or owner/name (case-folded)" claim is only
        verified for repo casing without this test.

        _resolve_pinned_gh_repo is called directly below (not via the full
        _pr_cost_report orchestration), so only the `gh repo view` response
        this fake builds matters -- repo/host (which drive the unused `git
        remote get-url origin` branch) are left at their defaults."""
        monkeypatch.setattr(
            subprocess, "run",
            _fake_pr_cost_subprocess_run(
                gh_repo_view_host="ACME-Corp.GHE.com", gh_repo_name_with_owner="owner/repo",
            ),
        )

        gh_repo, _ = gh_cli._resolve_pinned_gh_repo("acme-corp.ghe.com", "owner/repo", ordinal=1)

        assert gh_repo == "owner/repo"

    def test_mismatch_with_non_default_ordinal_labels_output_account_two(self, monkeypatch, capsys):
        """No caller in the new --all-accounts design passes a literal
        ordinal=1 by coincidence -- every call site passes
        redact_ordinals[roots[0].resolve()], which happens to be 1 only for
        a single/first root. This closes the gap that no other test in this
        file exercises _resolve_pinned_gh_repo with a non-default ordinal."""
        gh_repo = "gh-side-owner/gh-side-repo"
        corpus_repo = "git-side-owner/git-side-repo"
        monkeypatch.setattr(
            subprocess, "run",
            _fake_pr_cost_subprocess_run(repo=corpus_repo, gh_repo_name_with_owner=gh_repo),
        )

        with pytest.raises(SystemExit) as exc_info:
            gh_cli._resolve_pinned_gh_repo("github.com", corpus_repo, ordinal=2)

        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert gh_repo not in err
        assert corpus_repo not in err
        assert "account-1/repo-1" not in err
        assert "account-2/repo-1" in err
        assert "account-2/repo-2" in err

    def test_pr_cost_report_wires_the_scan_order_first_roots_resolved_ordinal_not_a_literal(
        self, tmp_path, monkeypatch, capsys,
    ):
        """_pr_cost_report computes _resolve_pinned_gh_repo's ordinal as
        redact_ordinals[roots[0].resolve()] -- roots[0] is scan-order-first,
        but _redaction_ordinals numbers by resolved-path sort, so the two
        can diverge for a root whose scan-order position doesn't match its
        resolved-path sort position. Drives that divergent root pair through
        a gh-identity mismatch end-to-end via cmd_pr_cost, closing the gap that
        test_mismatch_with_non_default_ordinal_labels_output_account_two
        only proves the literal ordinal=2 case, not _pr_cost_report's own
        computation of which ordinal to pass."""
        monkeypatch.setattr(scope, "declared_transcript_roots", lambda: [])
        active = tmp_path / "zzz-active"
        (active / "projects").mkdir(parents=True)
        monkeypatch.setattr(scope, "config_dir", lambda: active)
        extra = tmp_path / "aaa-extra"  # resolved-path-sorts before "zzz-active" despite being scanned second
        (extra / "projects").mkdir(parents=True)
        monkeypatch.setattr(
            subprocess, "run",
            _fake_pr_cost_subprocess_run(repo="git-side-owner/git-side-repo",
                                          gh_repo_name_with_owner="gh-side-owner/gh-side-repo"),
        )

        with pytest.raises(SystemExit) as exc_info:
            pr_cost.cmd_pr_cost(_pr_cost_args(extra_config_dirs=[str(extra)], all_accounts=True))

        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert "gh-side-owner/gh-side-repo" not in err
        assert "git-side-owner/git-side-repo" not in err
        assert "account-1/repo-1" not in err  # zzz-active is scan-order-first but resolved-sort SECOND
        assert "account-2/repo-1" in err
        assert "account-2/repo-2" in err

    def test_case_differing_match_proceeds_and_persists_the_pinned_lowercased_identity(
        self, fake_projects, tmp_path, monkeypatch,
    ):
        """A case-differing but otherwise-equal identity ('Owner/Repo' from
        the git remote vs. 'owner/REPO' from `gh repo view`) proceeds
        without exit(2); the row's persisted repo column is the single
        lowercased identity _resolve_pinned_gh_repo itself resolved and
        returned (its own `gh_repo`), confirmed by checking it is fully
        lowercase -- a bug that persisted the raw, differently-cased
        corpus-side value instead would leave mixed case behind."""
        _enable_pr_cost(tmp_path)
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        merged_prs = [{
            "number": 42, "headRefName": "ghost-branch", "additions": 1, "deletions": 1,
            "changedFiles": 1, "mergedAt": "2026-01-01T00:00:00Z",
        }]
        monkeypatch.setattr(
            subprocess, "run",
            _fake_pr_cost_subprocess_run(
                repo="Owner/Repo", gh_repo_name_with_owner="owner/REPO", merged_prs=merged_prs,
            ),
        )

        args = _pr_cost_args(record=True, pr=42)
        pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])

        rows = pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())
        assert len(rows) == 1
        assert rows[0]["repo"] == "owner/repo"


class TestResolvePinnedGhRepoRetryExhaustion:
    def test_perpetual_rate_limit_failure_on_gh_repo_view_exits_1_after_max_attempts_no_row_written(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        """Distinct from a genuine identity mismatch (exit 2): exhausting
        the shared retry budget on `gh repo view` itself aborts the whole
        run (exit 1, since no row exists yet to degrade into) before ever
        reaching the comparison that could disagree. The abort message must
        never surface gh's own raw stderr text either, only this module's
        generic one."""
        gh_repo = "gh-side-owner/gh-side-repo"
        corpus_repo = "git-side-owner/git-side-repo"
        rate_limit_stderr = "API rate limit exceeded (HTTP 403)\n"
        call_log: list[list[str]] = []
        sleep_calls: list[float] = []
        monkeypatch.setattr(
            subprocess, "run",
            _fake_pr_cost_subprocess_run(
                repo=corpus_repo, gh_repo_name_with_owner=gh_repo,
                gh_repo_view_failure_stderr=rate_limit_stderr, call_log=call_log,
            ),
        )
        monkeypatch.setattr(time, "sleep", lambda s: sleep_calls.append(s))

        with pytest.raises(SystemExit) as exc_info:
            pr_cost._pr_cost_report(_pr_cost_args(), datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])
        assert exc_info.value.code == 1  # exhaustion, not the mismatch path's exit(2)

        gh_repo_view_calls = [c for c in call_log if c[:3] == ["gh", "repo", "view"]]
        assert len(gh_repo_view_calls) == gh_cli._PR_COST_RATE_LIMIT_MAX_ATTEMPTS
        assert len(sleep_calls) == gh_cli._PR_COST_RATE_LIMIT_MAX_ATTEMPTS - 1

        err = capsys.readouterr().err
        assert gh_repo not in err
        assert corpus_repo not in err
        assert rate_limit_stderr.strip() not in err  # gh's own raw stderr text is never surfaced
        assert not (tmp_path / "pr-cost-ledger.tsv").exists()


class TestResolvePinnedGhRepoHostMismatchAbort:
    def test_gh_host_mismatch_failure_on_gh_repo_view_exits_1_with_no_retry_no_row_written(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        """Distinct from a genuine identity mismatch (exit 2): a GH_HOST-
        mismatch-shaped failure on `gh repo view` itself -- the new failure
        kind this PR introduces at this call site -- aborts the whole run
        (exit 1, since no row exists yet to degrade into) before ever
        reaching the identity comparison, and without retrying (a local
        shell-config mismatch doesn't self-resolve by waiting). Mirrors
        TestResolvePinnedGhRepoRetryExhaustion's rate-limit-shaped case
        above for this call site's other no-retry failure kind."""
        gh_repo = "gh-side-owner/gh-side-repo"
        corpus_repo = "git-side-owner/git-side-repo"
        host_mismatch_stderr = (
            "none of the git remotes configured for this repository correspond to the"
            " GH_HOST environment variable. Try adding a matching remote or unsetting"
            " the variable\n"
        )
        call_log: list[list[str]] = []
        sleep_calls: list[float] = []
        monkeypatch.setattr(
            subprocess, "run",
            _fake_pr_cost_subprocess_run(
                repo=corpus_repo, gh_repo_name_with_owner=gh_repo,
                gh_repo_view_failure_stderr=host_mismatch_stderr, call_log=call_log,
            ),
        )
        monkeypatch.setattr(time, "sleep", lambda s: sleep_calls.append(s))

        with pytest.raises(SystemExit) as exc_info:
            pr_cost._pr_cost_report(_pr_cost_args(), datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])
        assert exc_info.value.code == 1  # abort, not the identity-mismatch path's exit(2)

        gh_repo_view_calls = [c for c in call_log if c[:3] == ["gh", "repo", "view"]]
        assert len(gh_repo_view_calls) == 1  # no retry -- a host mismatch doesn't self-resolve
        assert sleep_calls == []

        err = capsys.readouterr().err
        assert gh_repo not in err
        assert corpus_repo not in err
        assert host_mismatch_stderr.strip() not in err  # gh's own raw stderr text is never surfaced
        assert not (tmp_path / "pr-cost-ledger.tsv").exists()


class TestGhHostQualifiedRepo:
    def test_returns_host_slash_owner_repo(self):
        assert gh_cli._gh_host_qualified_repo("acme-corp.ghe.com", "owner/repo") == "acme-corp.ghe.com/owner/repo"
        assert gh_cli._gh_host_qualified_repo("github.com", "owner/repo") == "github.com/owner/repo"


# ---------------------------------------------------------------------------
# Exact-output characterization of gh_cli.py: full gh argv, every stderr message site, and
# empty/non-list stdout handling.
# ---------------------------------------------------------------------------
_MERGED_PR_LIST_FIELDS = "number,headRefName,additions,deletions,changedFiles,mergedAt"
_AUTH_FAILURE_STDERR = "not logged into any GitHub hosts\n"
_HOST_MISMATCH_FAILURE_STDERR = (
    "none of the git remotes configured for this repository correspond to the"
    " GH_HOST environment variable. Try adding a matching remote or unsetting"
    " the variable\n"
)


def _gh_result(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return type("R", (), {"returncode": returncode, "stdout": stdout, "stderr": stderr})()


@pytest.fixture
def recorded_sleeps(monkeypatch) -> list[float]:
    """Replaces time.sleep so no test waits out a backoff, and returns the list of requested durations."""
    sleep_calls: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda seconds: sleep_calls.append(seconds))
    return sleep_calls


class TestGhDiscoveryArgv:
    def test_merged_discovery_issues_exactly_this_gh_pr_list_argv(self, monkeypatch, recorded_sleeps):
        call_log: list[list[str]] = []

        def fake_run(cmd, *a, **kw):
            call_log.append(cmd)
            return _gh_result(stdout="[]")

        monkeypatch.setattr(subprocess, "run", fake_run)
        gh_cli._gh_discover_merged_prs("acme-corp.ghe.com", "owner/repo")

        assert call_log == [[
            "gh", "pr", "list", "--repo", "acme-corp.ghe.com/owner/repo", "--state", "merged",
            "--limit", str(gh_cli._PR_COST_GH_PR_LIST_LIMIT), "--json", _MERGED_PR_LIST_FIELDS,
        ]]
        assert recorded_sleeps == []

    def test_closed_discovery_issues_exactly_this_gh_pr_list_argv(self, monkeypatch, recorded_sleeps):
        call_log: list[list[str]] = []

        def fake_run(cmd, *a, **kw):
            call_log.append(cmd)
            return _gh_result(stdout="[]")

        monkeypatch.setattr(subprocess, "run", fake_run)
        gh_cli._gh_discover_closed_unmerged_pr_branches("acme-corp.ghe.com", "owner/repo")

        assert call_log == [[
            "gh", "pr", "list", "--repo", "acme-corp.ghe.com/owner/repo", "--state", "closed",
            "--limit", str(gh_cli._PR_COST_GH_PR_LIST_LIMIT), "--json", "headRefName",
        ]]
        assert recorded_sleeps == []


class TestGhDiscoveryStderrAndEmptyOutput:
    """Exact stderr of each discovery helper's abort and unparseable-JSON paths, and what each returns for empty
    or non-list stdout. The abort path prints the backoff helper's own message first, then the abort line."""

    def test_merged_discovery_auth_failure_prints_backoff_then_abort_line_and_exits_1(
        self, monkeypatch, capsys, recorded_sleeps,
    ):
        monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **kw: _gh_result(1, stderr=_AUTH_FAILURE_STDERR))

        with pytest.raises(SystemExit) as exc_info:
            gh_cli._gh_discover_merged_prs("github.com", "owner/repo")

        assert exc_info.value.code == 1
        assert capsys.readouterr().err == (
            "pr-cost: pr list failed (auth), not retrying (auth failures don't self-resolve)\n"
            "pr-cost: gh pr list failed (degraded_auth) before any row could be captured\n"
        )
        assert recorded_sleeps == []

    def test_closed_discovery_auth_failure_prints_backoff_then_abort_line_and_exits_1(
        self, monkeypatch, capsys, recorded_sleeps,
    ):
        monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **kw: _gh_result(1, stderr=_AUTH_FAILURE_STDERR))

        with pytest.raises(SystemExit) as exc_info:
            gh_cli._gh_discover_closed_unmerged_pr_branches("github.com", "owner/repo")

        assert exc_info.value.code == 1
        assert capsys.readouterr().err == (
            "pr-cost: pr list (closed) failed (auth), not retrying (auth failures don't self-resolve)\n"
            "pr-cost: gh pr list (closed) failed (degraded_auth) before any row could be captured\n"
        )
        assert recorded_sleeps == []

    def test_merged_discovery_unparseable_json_prints_exact_message_and_exits_1(
        self, monkeypatch, capsys, recorded_sleeps,
    ):
        monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **kw: _gh_result(stdout="not json"))

        with pytest.raises(SystemExit) as exc_info:
            gh_cli._gh_discover_merged_prs("github.com", "owner/repo")

        assert exc_info.value.code == 1
        assert capsys.readouterr().err == "pr-cost: gh pr list returned unparseable JSON -- no row was captured\n"

    def test_closed_discovery_unparseable_json_prints_exact_message_and_exits_1(
        self, monkeypatch, capsys, recorded_sleeps,
    ):
        monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **kw: _gh_result(stdout="not json"))

        with pytest.raises(SystemExit) as exc_info:
            gh_cli._gh_discover_closed_unmerged_pr_branches("github.com", "owner/repo")

        assert exc_info.value.code == 1
        assert capsys.readouterr().err == "pr-cost: gh pr list (closed) returned unparseable JSON\n"

    @pytest.mark.parametrize("empty_stdout", ["", None], ids=["empty_string", "none"])
    def test_merged_discovery_empty_stdout_returns_empty_list(self, monkeypatch, recorded_sleeps, empty_stdout):
        monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **kw: _gh_result(stdout=empty_stdout))

        assert gh_cli._gh_discover_merged_prs("github.com", "owner/repo") == []

    @pytest.mark.parametrize("empty_stdout", ["", None], ids=["empty_string", "none"])
    def test_closed_discovery_empty_stdout_returns_empty_set(self, monkeypatch, recorded_sleeps, empty_stdout):
        monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **kw: _gh_result(stdout=empty_stdout))

        assert gh_cli._gh_discover_closed_unmerged_pr_branches("github.com", "owner/repo") == set()

    def test_merged_discovery_returns_a_dict_payload_unchanged(self, monkeypatch, recorded_sleeps):
        """No shape check: a dict payload reaches the caller as-is."""
        monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **kw: _gh_result(stdout='{"message": "unexpected"}'))

        assert gh_cli._gh_discover_merged_prs("github.com", "owner/repo") == {"message": "unexpected"}

    def test_merged_discovery_returns_a_null_payload_as_none(self, monkeypatch, recorded_sleeps):
        """No shape check: a null payload reaches the caller as-is."""
        monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **kw: _gh_result(stdout="null"))

        assert gh_cli._gh_discover_merged_prs("github.com", "owner/repo") is None

    # Real `gh --json` never emits a non-list payload. These three tests guard that the closed helper does no
    # shape check, so the exception types below are incidental. Flip them deliberately if a shape check is added.
    def test_closed_discovery_empty_dict_payload_returns_empty_set(self, monkeypatch, recorded_sleeps):
        monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **kw: _gh_result(stdout="{}"))

        assert gh_cli._gh_discover_closed_unmerged_pr_branches("github.com", "owner/repo") == set()

    def test_closed_discovery_non_empty_dict_payload_raises_attribute_error(self, monkeypatch, capsys, recorded_sleeps):
        call_log: list[list[str]] = []

        def fake_run(cmd, *a, **kw):
            call_log.append(cmd)
            return _gh_result(stdout='{"headRefName": "x"}')

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(AttributeError):
            gh_cli._gh_discover_closed_unmerged_pr_branches("github.com", "owner/repo")

        assert len(call_log) == 1
        assert capsys.readouterr().err == ""

    def test_closed_discovery_null_payload_raises_type_error(self, monkeypatch, capsys, recorded_sleeps):
        call_log: list[list[str]] = []

        def fake_run(cmd, *a, **kw):
            call_log.append(cmd)
            return _gh_result(stdout="null")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(TypeError):
            gh_cli._gh_discover_closed_unmerged_pr_branches("github.com", "owner/repo")

        assert len(call_log) == 1
        assert capsys.readouterr().err == ""


_DISCOVERY_HELPER_CASES = [
    pytest.param("_gh_discover_merged_prs", "pr list", "gh pr list", id="merged"),
    pytest.param("_gh_discover_closed_unmerged_pr_branches", "pr list (closed)", "gh pr list (closed)", id="closed"),
]


class TestGhDiscoveryBackoffWiring:
    """Each discovery helper hands the shared backoff its own label and default budget, and aborts with the
    backoff's final status in the abort line."""

    @pytest.mark.parametrize("helper_name,retry_label,abort_label", _DISCOVERY_HELPER_CASES)
    def test_rate_limit_exhaustion_retries_four_times_then_aborts_with_degraded_rate_limit(
        self, monkeypatch, capsys, recorded_sleeps, helper_name, retry_label, abort_label,
    ):
        call_log: list[list[str]] = []

        def fake_run(cmd, *a, **kw):
            call_log.append(cmd)
            return _gh_result(1, stderr="API rate limit exceeded (HTTP 403)\n")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            getattr(gh_cli, helper_name)("github.com", "owner/repo")

        assert exc_info.value.code == 1
        assert len(call_log) == 5
        assert recorded_sleeps == [60.0, 120.0, 240.0, 480.0]
        assert capsys.readouterr().err == (
            f"pr-cost: {retry_label} failed (rate_limit), retrying in 60s (attempt 1)...\n"
            f"pr-cost: {retry_label} failed (rate_limit), retrying in 120s (attempt 2)...\n"
            f"pr-cost: {retry_label} failed (rate_limit), retrying in 240s (attempt 3)...\n"
            f"pr-cost: {retry_label} failed (rate_limit), retrying in 480s (attempt 4)...\n"
            f"pr-cost: {retry_label} failed (rate_limit), giving up after 5 attempt(s)\n"
            f"pr-cost: {abort_label} failed (degraded_rate_limit) before any row could be captured\n"
        )

    @pytest.mark.parametrize("helper_name,retry_label,abort_label", _DISCOVERY_HELPER_CASES)
    def test_host_mismatch_failure_aborts_with_degraded_host_mismatch_without_retrying(
        self, monkeypatch, capsys, recorded_sleeps, helper_name, retry_label, abort_label,
    ):
        call_log: list[list[str]] = []

        def fake_run(cmd, *a, **kw):
            call_log.append(cmd)
            return _gh_result(1, stderr=_HOST_MISMATCH_FAILURE_STDERR)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            getattr(gh_cli, helper_name)("github.com", "owner/repo")

        assert exc_info.value.code == 1
        assert len(call_log) == 1
        assert recorded_sleeps == []
        assert capsys.readouterr().err == (
            f"pr-cost: {retry_label} failed (host_mismatch), not retrying -- gh's ambient GH_HOST"
            " environment variable does not match this repo's own git remote host;"
            " unset GH_HOST or point it at the correct host\n"
            f"pr-cost: {abort_label} failed (degraded_host_mismatch) before any row could be captured\n"
        )


class TestGhCallWithBackoffStderrMessages:
    """Exact stderr text at every message site in _gh_call_with_backoff."""

    def test_success_prints_nothing(self, monkeypatch, capsys, recorded_sleeps):
        monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **kw: _gh_result(stdout="ok"))

        proc, degraded = gh_cli._gh_call_with_backoff(["gh", "repo", "view"], label="repo view")

        assert degraded == ""
        assert proc.stdout == "ok"
        assert capsys.readouterr().err == ""

    def test_auth_failure_prints_not_retrying_message(self, monkeypatch, capsys, recorded_sleeps):
        monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **kw: _gh_result(1, stderr=_AUTH_FAILURE_STDERR))

        gh_cli._gh_call_with_backoff(["gh", "repo", "view"], label="repo view")

        assert capsys.readouterr().err == (
            "pr-cost: repo view failed (auth), not retrying (auth failures don't self-resolve)\n"
        )

    def test_host_mismatch_failure_prints_actionable_message(self, monkeypatch, capsys, recorded_sleeps):
        monkeypatch.setattr(
            subprocess, "run", lambda cmd, *a, **kw: _gh_result(1, stderr=_HOST_MISMATCH_FAILURE_STDERR),
        )

        gh_cli._gh_call_with_backoff(["gh", "repo", "view"], label="repo view")

        assert capsys.readouterr().err == (
            "pr-cost: repo view failed (host_mismatch), not retrying -- gh's ambient GH_HOST"
            " environment variable does not match this repo's own git remote host;"
            " unset GH_HOST or point it at the correct host\n"
        )

    def test_rate_limit_exhaustion_prints_each_retry_line_then_giving_up(self, monkeypatch, capsys, recorded_sleeps):
        monkeypatch.setattr(
            subprocess, "run", lambda cmd, *a, **kw: _gh_result(1, stderr="API rate limit exceeded (HTTP 403)\n"),
        )

        gh_cli._gh_call_with_backoff(["gh", "pr", "view", "1"], label="pr view 1")

        assert capsys.readouterr().err == (
            "pr-cost: pr view 1 failed (rate_limit), retrying in 60s (attempt 1)...\n"
            "pr-cost: pr view 1 failed (rate_limit), retrying in 120s (attempt 2)...\n"
            "pr-cost: pr view 1 failed (rate_limit), retrying in 240s (attempt 3)...\n"
            "pr-cost: pr view 1 failed (rate_limit), retrying in 480s (attempt 4)...\n"
            "pr-cost: pr view 1 failed (rate_limit), giving up after 5 attempt(s)\n"
        )
        assert recorded_sleeps == [60.0, 120.0, 240.0, 480.0]

    def test_network_exception_exhaustion_prints_network_kind_in_every_line(self, monkeypatch, capsys, recorded_sleeps):
        def fake_run(cmd, *a, **kw):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=gh_cli._GH_CALL_TIMEOUT_S)

        monkeypatch.setattr(subprocess, "run", fake_run)

        gh_cli._gh_call_with_backoff(["gh", "pr", "view", "1"], label="pr view 1")

        assert capsys.readouterr().err == (
            "pr-cost: pr view 1 failed (network), retrying in 60s (attempt 1)...\n"
            "pr-cost: pr view 1 failed (network), retrying in 120s (attempt 2)...\n"
            "pr-cost: pr view 1 failed (network), retrying in 240s (attempt 3)...\n"
            "pr-cost: pr view 1 failed (network), retrying in 480s (attempt 4)...\n"
            "pr-cost: pr view 1 failed (network), giving up after 5 attempt(s)\n"
        )

    def test_retry_after_hint_replaces_the_backoff_duration_in_the_retry_line(self, monkeypatch, capsys, recorded_sleeps):
        outcomes = iter([_gh_result(1, stderr="API rate limit exceeded, retry after: 7\n"), _gh_result(stdout="ok")])
        monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **kw: next(outcomes))

        proc, degraded = gh_cli._gh_call_with_backoff(["gh", "pr", "view", "1"], label="pr view 1")

        assert degraded == ""
        assert proc.stdout == "ok"
        assert capsys.readouterr().err == "pr-cost: pr view 1 failed (rate_limit), retrying in 7s (attempt 1)...\n"
        assert recorded_sleeps == [7.0]

    def test_elapsed_budget_exhaustion_prints_capped_retry_line_then_giving_up_after_two_attempts(
        self, monkeypatch, capsys, recorded_sleeps,
    ):
        monkeypatch.setattr(
            subprocess, "run",
            lambda cmd, *a, **kw: _gh_result(1, stderr="secondary rate limit hit, retry after: 999999 seconds\n"),
        )

        gh_cli._gh_call_with_backoff(["gh", "pr", "view", "1"], label="pr view 1")

        assert capsys.readouterr().err == (
            "pr-cost: pr view 1 failed (rate_limit), retrying in 900s (attempt 1)...\n"
            "pr-cost: pr view 1 failed (rate_limit), giving up after 2 attempt(s)\n"
        )


_ORIGIN_FAILURE_MESSAGE_CASES = [
    pytest.param({}, "pr-cost", "", id="defaults"),
    pytest.param({"subcommand": "period-cost"}, "period-cost", "", id="subcommand_only"),
    pytest.param({"failure_hint": "pass --repo to name it"}, "pr-cost", " -- pass --repo to name it", id="hint_only"),
    pytest.param(
        {"subcommand": "period-cost", "failure_hint": "pass --repo to name it"}, "period-cost", " -- pass --repo to name it",
        id="subcommand_and_hint",
    ),
]


class TestGitRemoteOriginFailureMessages:
    """Exact failure text of _git_remote_origin_host_and_owner_repo, by subcommand prefix and failure_hint."""

    @pytest.mark.parametrize("call_kwargs,expected_prefix,expected_hint_suffix", _ORIGIN_FAILURE_MESSAGE_CASES)
    @pytest.mark.parametrize("raised", [
        pytest.param(subprocess.CalledProcessError(128, ["git", "remote", "get-url", "origin"]), id="called_process_error"),
        pytest.param(subprocess.TimeoutExpired(cmd=["git", "remote", "get-url", "origin"], timeout=1), id="timeout_expired"),
        pytest.param(OSError("git not found"), id="os_error"),
    ])
    def test_unresolvable_origin_prints_exact_message_and_exits_1(
        self, monkeypatch, capsys, call_kwargs, expected_prefix, expected_hint_suffix, raised,
    ):
        def fake_run(cmd, *a, **kw):
            raise raised

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(SystemExit) as exc_info:
            gh_cli._git_remote_origin_host_and_owner_repo(**call_kwargs)

        assert exc_info.value.code == 1
        assert capsys.readouterr().err == (
            f"{expected_prefix}: could not resolve this repo's own remote"
            f" (git remote get-url origin failed){expected_hint_suffix}\n"
        )

    @pytest.mark.parametrize("call_kwargs,expected_prefix,expected_hint_suffix", _ORIGIN_FAILURE_MESSAGE_CASES)
    def test_unparseable_origin_prints_exact_message_and_exits_1(
        self, monkeypatch, capsys, call_kwargs, expected_prefix, expected_hint_suffix,
    ):
        monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **kw: _gh_result(stdout="not a remote\n"))

        with pytest.raises(SystemExit) as exc_info:
            gh_cli._git_remote_origin_host_and_owner_repo(**call_kwargs)

        assert exc_info.value.code == 1
        assert capsys.readouterr().err == (
            f"{expected_prefix}: this repo's origin remote is not a recognizable host/owner/repo URL{expected_hint_suffix}\n"
        )

    @pytest.mark.parametrize("call_kwargs,expected_prefix,expected_hint_suffix", _ORIGIN_FAILURE_MESSAGE_CASES)
    def test_nonzero_git_exit_is_unresolvable_only_because_the_call_passes_check_true(
        self, monkeypatch, capsys, call_kwargs, expected_prefix, expected_hint_suffix,
    ):
        """The fake raises CalledProcessError only when the caller asks for check=True, as real subprocess.run
        does. Without check=True, exit 128 with empty stdout would fall through to the unparseable-URL message."""

        def fake_run_honoring_check(cmd, *a, **kw):
            result = _gh_result(returncode=128, stderr="fatal: No such remote 'origin'\n")
            if kw.get("check") and result.returncode != 0:
                raise subprocess.CalledProcessError(result.returncode, cmd, output=result.stdout, stderr=result.stderr)
            return result

        monkeypatch.setattr(subprocess, "run", fake_run_honoring_check)
        with pytest.raises(SystemExit) as exc_info:
            gh_cli._git_remote_origin_host_and_owner_repo(**call_kwargs)

        assert exc_info.value.code == 1
        assert capsys.readouterr().err == (
            f"{expected_prefix}: could not resolve this repo's own remote"
            f" (git remote get-url origin failed){expected_hint_suffix}\n"
        )


class TestGitRemoteOriginLenientParseKept:
    """The cwd lookup parses leniently the URL shapes that _vetted_origin_identity_from_url rejects. pr-cost,
    workstream-cost, and pr-link read it, and their gh preflight and pin stop on these shapes."""

    @pytest.mark.parametrize("remote_url,expected", [
        pytest.param(
            "https://user:pw@host/project.git", ("user", "pw@host/project"), id="password_userinfo_reads_user_as_host",
        ),
        pytest.param("somedir/o/r", ("somedir", "o/r"), id="relative_path_reads_first_segment_as_host"),
        pytest.param("../o/r.git", ("..", "o/r"), id="parent_relative_path_reads_dotdot_as_host"),
        pytest.param("ssh://git@host:2222/r.git", ("host", "2222/r"), id="ssh_port_reads_port_as_owner"),
    ])
    def test_shape_the_vetting_rejects_keeps_its_current_result(self, monkeypatch, remote_url, expected):
        monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **kw: _gh_result(stdout=remote_url + "\n"))

        assert gh_cli._git_remote_origin_host_and_owner_repo() == expected


class TestGhCallWithBackoffMaxAttempts:
    def test_max_attempts_one_makes_exactly_one_call_and_never_sleeps(self, monkeypatch, capsys, recorded_sleeps):
        call_log: list[list[str]] = []

        def fake_run(cmd, *a, **kw):
            call_log.append(cmd)
            return _gh_result(1, stderr="API rate limit exceeded (HTTP 403)\n")

        monkeypatch.setattr(subprocess, "run", fake_run)

        proc, degraded = gh_cli._gh_call_with_backoff(["gh", "pr", "list"], label="pr list", max_attempts=1)

        assert proc is None
        assert degraded == pr_cost_ledger._PR_COST_STATUS_DEGRADED_RATE_LIMIT
        assert len(call_log) == 1
        assert recorded_sleeps == []
        assert capsys.readouterr().err == "pr-cost: pr list failed (rate_limit), giving up after 1 attempt(s)\n"

    def test_max_attempts_three_retries_twice_then_gives_up(self, monkeypatch, capsys, recorded_sleeps):
        monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **kw: _gh_result(1, stderr="connection reset\n"))

        gh_cli._gh_call_with_backoff(["gh", "pr", "list"], label="pr list", max_attempts=3)

        assert recorded_sleeps == [60.0, 120.0]
        assert capsys.readouterr().err.splitlines()[-1] == "pr-cost: pr list failed (network), giving up after 3 attempt(s)"

    @pytest.mark.parametrize("max_attempts", [0, -1])
    def test_max_attempts_below_one_raises_before_any_call(self, monkeypatch, recorded_sleeps, max_attempts):
        call_log: list[list[str]] = []

        def fake_run(cmd, *a, **kw):
            call_log.append(cmd)
            return _gh_result()

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(ValueError, match="max_attempts"):
            gh_cli._gh_call_with_backoff(["gh", "pr", "list"], label="pr list", max_attempts=max_attempts)

        assert call_log == []
        assert recorded_sleeps == []

    def test_default_attempts_are_read_from_the_module_constant_at_call_time(self, monkeypatch, recorded_sleeps):
        call_log: list[list[str]] = []

        def fake_run(cmd, *a, **kw):
            call_log.append(cmd)
            return _gh_result(1, stderr="connection reset\n")

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(gh_cli, "_PR_COST_RATE_LIMIT_MAX_ATTEMPTS", 2)

        gh_cli._gh_call_with_backoff(["gh", "pr", "list"], label="pr list")

        assert len(call_log) == 2
        assert recorded_sleeps == [60.0]


class TestGhCallWithBackoffTimeout:
    """`timeout_s` bounds each attempt's gh process; omitting it keeps _GH_CALL_TIMEOUT_S."""

    def test_default_timeout_is_read_from_the_module_constant_at_call_time(self, monkeypatch):
        recorded_kwargs: list[dict] = []
        sentinel_timeout_s = 17.0

        def fake_run(cmd, *a, **kw):
            recorded_kwargs.append(kw)
            return _gh_result()

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(gh_cli, "_GH_CALL_TIMEOUT_S", sentinel_timeout_s)

        gh_cli._gh_call_with_backoff(["gh", "repo", "view"], label="repo view")

        assert [kw["timeout"] for kw in recorded_kwargs] == [sentinel_timeout_s]

    def test_timeout_argument_reaches_subprocess_run_on_every_attempt(self, monkeypatch, recorded_sleeps):
        recorded_kwargs: list[dict] = []

        def fake_run(cmd, *a, **kw):
            recorded_kwargs.append(kw)
            return _gh_result(1, stderr="connection reset\n")

        monkeypatch.setattr(subprocess, "run", fake_run)

        gh_cli._gh_call_with_backoff(["gh", "pr", "list"], label="pr list", max_attempts=2, timeout_s=75.0)

        assert [kw["timeout"] for kw in recorded_kwargs] == [75.0, 75.0]


class TestGhCallWithBackoffEnvironment:
    """`env` reaches the gh child verbatim; None passes no environment, so the child inherits this process's."""

    def test_env_argument_reaches_subprocess_run(self, monkeypatch):
        recorded_kwargs: list[dict] = []

        def fake_run(cmd, *a, **kw):
            recorded_kwargs.append(kw)
            return _gh_result()

        monkeypatch.setattr(subprocess, "run", fake_run)
        child_environment = {"PATH": "/usr/bin"}

        gh_cli._gh_call_with_backoff(["gh", "pr", "list"], label="pr list", env=child_environment)

        assert [kw["env"] for kw in recorded_kwargs] == [child_environment]

    def test_default_passes_no_environment_so_the_child_inherits(self, monkeypatch):
        recorded_kwargs: list[dict] = []

        def fake_run(cmd, *a, **kw):
            recorded_kwargs.append(kw)
            return _gh_result()

        monkeypatch.setattr(subprocess, "run", fake_run)

        gh_cli._gh_call_with_backoff(["gh", "pr", "list"], label="pr list")

        assert [kw["env"] for kw in recorded_kwargs] == [None]

    def test_environment_without_gh_tokens_drops_exactly_the_four_token_variables(self, monkeypatch):
        for name in gh_cli._GH_TOKEN_ENV_VARS:
            monkeypatch.setenv(name, "ambient-value")
        monkeypatch.setenv("PATH_MARKER_FOR_TEST", "kept")
        monkeypatch.setenv("GH_CONFIG_DIR", "/ambient/gh-config")

        child_environment = gh_cli._environ_without_gh_tokens()

        assert not set(child_environment) & set(gh_cli._GH_TOKEN_ENV_VARS)
        assert child_environment["PATH_MARKER_FOR_TEST"] == "kept"
        assert child_environment["GH_CONFIG_DIR"] == "/ambient/gh-config"


class TestGhCallWithBackoffCommandName:
    """command_name replaces the "pr-cost" prefix at every message site."""

    def test_auth_failure_message_uses_command_name(self, monkeypatch, capsys, recorded_sleeps):
        monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **kw: _gh_result(1, stderr=_AUTH_FAILURE_STDERR))

        gh_cli._gh_call_with_backoff(["gh", "pr", "list"], label="pr list", command_name="period-cost")

        assert capsys.readouterr().err == (
            "period-cost: pr list failed (auth), not retrying (auth failures don't self-resolve)\n"
        )

    def test_host_mismatch_message_uses_command_name(self, monkeypatch, capsys, recorded_sleeps):
        monkeypatch.setattr(
            subprocess, "run", lambda cmd, *a, **kw: _gh_result(1, stderr=_HOST_MISMATCH_FAILURE_STDERR),
        )

        gh_cli._gh_call_with_backoff(["gh", "pr", "list"], label="pr list", command_name="period-cost")

        assert capsys.readouterr().err == (
            "period-cost: pr list failed (host_mismatch), not retrying -- gh's ambient GH_HOST"
            " environment variable does not match this repo's own git remote host;"
            " unset GH_HOST or point it at the correct host\n"
        )

    def test_retry_and_giving_up_messages_use_command_name(self, monkeypatch, capsys, recorded_sleeps):
        monkeypatch.setattr(
            subprocess, "run", lambda cmd, *a, **kw: _gh_result(1, stderr="API rate limit exceeded (HTTP 403)\n"),
        )

        gh_cli._gh_call_with_backoff(
            ["gh", "pr", "list"], label="pr list", command_name="period-cost", max_attempts=2,
        )

        assert capsys.readouterr().err == (
            "period-cost: pr list failed (rate_limit), retrying in 60s (attempt 1)...\n"
            "period-cost: pr list failed (rate_limit), giving up after 2 attempt(s)\n"
        )


_PR_LIST_FIELDS = "number,mergedAt"


def _gh_pr_list_all(**overrides):
    kwargs = {"state": "all", "json_fields": _PR_LIST_FIELDS, "label": "pr list", **overrides}
    return gh_cli._gh_pr_list("github.com", "owner/repo", **kwargs)


class TestGhPrList:
    def test_issues_exactly_this_argv_for_state_all(self, monkeypatch, recorded_sleeps):
        call_log: list[list[str]] = []

        def fake_run(cmd, *a, **kw):
            call_log.append(cmd)
            return _gh_result(stdout="[]")

        monkeypatch.setattr(subprocess, "run", fake_run)
        gh_cli._gh_pr_list("acme-corp.ghe.com", "owner/repo", state="all", json_fields=_PR_LIST_FIELDS, label="pr list")

        assert call_log == [[
            "gh", "pr", "list", "--repo", "acme-corp.ghe.com/owner/repo", "--state", "all",
            "--limit", str(gh_cli._PR_COST_GH_PR_LIST_LIMIT), "--json", _PR_LIST_FIELDS,
        ]]

    def test_each_attempt_is_bounded_by_the_bulk_list_timeout_not_the_single_call_timeout(
        self, monkeypatch, recorded_sleeps
    ):
        recorded_kwargs: list[dict] = []

        def fake_run(cmd, *a, **kw):
            recorded_kwargs.append(kw)
            return _gh_result(1, stderr="connection reset\n")

        monkeypatch.setattr(subprocess, "run", fake_run)
        sentinel_timeout_s = 123.0
        monkeypatch.setattr(gh_cli, "_GH_PR_LIST_TIMEOUT_S", sentinel_timeout_s)

        _gh_pr_list_all(max_attempts=2)

        assert [kw["timeout"] for kw in recorded_kwargs] == [sentinel_timeout_s] * 2

    def test_the_bulk_list_timeout_exceeds_the_single_call_timeout(self):
        assert gh_cli._GH_PR_LIST_TIMEOUT_S > gh_cli._GH_CALL_TIMEOUT_S

    def test_success_returns_parsed_payload_and_empty_status_without_printing(self, monkeypatch, capsys, recorded_sleeps):
        payload = [{"number": 7, "mergedAt": "2026-09-01T00:00:00Z"}]
        monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **kw: _gh_result(stdout=json.dumps(payload)))

        assert _gh_pr_list_all() == (payload, "")
        assert capsys.readouterr().err == ""

    @pytest.mark.parametrize("empty_stdout", ["", None], ids=["empty_string", "none"])
    def test_empty_stdout_returns_empty_list_and_empty_status(self, monkeypatch, recorded_sleeps, empty_stdout):
        monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **kw: _gh_result(stdout=empty_stdout))

        assert _gh_pr_list_all() == ([], "")

    @pytest.mark.parametrize("stdout,expected_payload", [
        pytest.param('{"message": "unexpected"}', {"message": "unexpected"}, id="dict"),
        pytest.param("null", None, id="null"),
    ])
    def test_non_list_payload_is_returned_unchanged_with_empty_status(
        self, monkeypatch, recorded_sleeps, stdout, expected_payload,
    ):
        monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **kw: _gh_result(stdout=stdout))

        assert _gh_pr_list_all() == (expected_payload, "")

    def test_unparseable_stdout_returns_unparseable_status_and_prints_nothing(self, monkeypatch, capsys, recorded_sleeps):
        monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **kw: _gh_result(stdout="not json"))

        assert _gh_pr_list_all() == (None, gh_cli._GH_PR_LIST_UNPARSEABLE)
        assert capsys.readouterr().err == ""

    def test_gh_failure_returns_the_backoff_status_without_aborting(self, monkeypatch, capsys, recorded_sleeps):
        monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **kw: _gh_result(1, stderr=_AUTH_FAILURE_STDERR))

        assert _gh_pr_list_all() == (None, gh_cli._GH_CALL_DEGRADED_AUTH)
        assert capsys.readouterr().err == (
            "pr-cost: pr list failed (auth), not retrying (auth failures don't self-resolve)\n"
        )

    def test_max_attempts_one_makes_exactly_one_call_and_never_sleeps(self, monkeypatch, recorded_sleeps):
        call_log: list[list[str]] = []

        def fake_run(cmd, *a, **kw):
            call_log.append(cmd)
            return _gh_result(1, stderr="API rate limit exceeded (HTTP 403)\n")

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert _gh_pr_list_all(max_attempts=1) == (None, pr_cost_ledger._PR_COST_STATUS_DEGRADED_RATE_LIMIT)
        assert len(call_log) == 1
        assert recorded_sleeps == []

    @pytest.mark.parametrize("max_attempts", [0, -1])
    def test_max_attempts_below_one_raises_before_any_call(self, monkeypatch, recorded_sleeps, max_attempts):
        call_log: list[list[str]] = []

        def fake_run(cmd, *a, **kw):
            call_log.append(cmd)
            return _gh_result(stdout="[]")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(ValueError, match="max_attempts"):
            _gh_pr_list_all(max_attempts=max_attempts)

        assert call_log == []
        assert recorded_sleeps == []

    def test_default_attempts_are_read_from_the_module_constant_at_call_time(self, monkeypatch, recorded_sleeps):
        call_log: list[list[str]] = []

        def fake_run(cmd, *a, **kw):
            call_log.append(cmd)
            return _gh_result(1, stderr="connection reset\n")

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(gh_cli, "_PR_COST_RATE_LIMIT_MAX_ATTEMPTS", 2)

        assert _gh_pr_list_all() == (None, pr_cost_ledger._PR_COST_STATUS_DEGRADED_NETWORK)
        assert len(call_log) == 2

    def test_command_name_prefixes_the_backoff_messages(self, monkeypatch, capsys, recorded_sleeps):
        monkeypatch.setattr(
            subprocess, "run", lambda cmd, *a, **kw: _gh_result(1, stderr="API rate limit exceeded (HTTP 403)\n"),
        )

        _gh_pr_list_all(command_name="period-cost", max_attempts=2)

        assert capsys.readouterr().err == (
            "period-cost: pr list failed (rate_limit), retrying in 60s (attempt 1)...\n"
            "period-cost: pr list failed (rate_limit), giving up after 2 attempt(s)\n"
        )


class TestGhPrListUnparseableStatus:
    def test_status_is_a_non_empty_string_distinct_from_every_other_status(self):
        status = gh_cli._GH_PR_LIST_UNPARSEABLE

        assert isinstance(status, str)
        assert status
        other_statuses = {
            gh_cli._GH_CALL_DEGRADED_AUTH,
            gh_cli._GH_CALL_DEGRADED_HOST_MISMATCH,
            *pr_cost_ledger._PR_COST_STATUS_VALUES,
        }
        assert status not in other_statuses


class TestOriginUrlSchemeGroup:
    def test_scheme_group_takes_part_in_a_match_with_no_prefix_and_is_empty(self):
        match = gh_cli._GIT_REMOTE_HOST_OWNER_REPO_RE.search("ghe.example:o/r")

        assert match is not None
        assert match.group("scheme") == ""
        assert match.group("sep") == ":"


class TestOriginIdentityFromUrl:
    @pytest.mark.parametrize("url,expected", [
        pytest.param("https://github.com/owner/repo.git", ("github.com", "owner/repo"), id="https_dot_git"),
        pytest.param("https://github.com/owner/repo", ("github.com", "owner/repo"), id="https_no_suffix"),
        pytest.param("git@github.com:owner/repo.git", ("github.com", "owner/repo"), id="scp_style"),
        pytest.param("https://github.com/owner/repo/", ("github.com", "owner/repo"), id="trailing_slash"),
        pytest.param("https://github.com/owner/repo.git/", ("github.com", "owner/repo"), id="dot_git_and_trailing_slash"),
        pytest.param("https://GitHub.COM/Owner-Name/Repo-Name.git", ("github.com", "owner-name/repo-name"), id="uppercase"),
        pytest.param("https://github.com/owner/repo.git\n", ("github.com", "owner/repo"), id="trailing_newline"),
        pytest.param(
            "https://user:pw@host/project.git", ("user", "pw@host/project"), id="password_userinfo_reads_user_as_host",
        ),
    ])
    def test_recognized_shapes_resolve_lowercased(self, url, expected):
        assert gh_cli._origin_identity_from_url(url) == expected

    @pytest.mark.parametrize("url", [
        pytest.param("https://user@host/o/r.git", id="userinfo_without_password"),
        pytest.param("https://user:token@host/o/r.git", id="userinfo_with_token"),
        pytest.param("file:///abs/o/r.git", id="file_scheme"),
        pytest.param("/abs/path/r.git", id="absolute_path"),
        pytest.param("ssh://git@host:2222/o/r.git", id="ssh_with_port"),
        pytest.param("not a remote", id="not_a_url"),
        pytest.param("", id="empty"),
    ])
    def test_unrecognized_shapes_return_none(self, url):
        assert gh_cli._origin_identity_from_url(url) is None


# A quadratic match at this length takes several seconds, growing with the square of the length; a linear one takes milliseconds.
_PATHOLOGICAL_LENGTH = 50_000
_LINEAR_TIME_BOUND_S = 1.0


class TestIsPlainOwnerRepo:
    @pytest.mark.parametrize("owner_repo", [
        pytest.param("acme/widgets", id="plain"),
        pytest.param("a.b/c_d-e", id="dot_underscore_hyphen"),
        pytest.param("acme/.github", id="leading_dot_name_with_a_letter"),
    ])
    def test_accepts(self, owner_repo):
        assert gh_cli._is_plain_owner_repo(owner_repo) is True

    @pytest.mark.parametrize("owner_repo", [
        pytest.param("./r", id="dot_owner"),
        pytest.param("o/..", id="dot_dot_name"),
        pytest.param("o/r*", id="glob_character"),
        pytest.param("pw@host/project", id="at_sign"),
        pytest.param("o/r/x", id="third_segment"),
        pytest.param("o", id="missing_name"),
        pytest.param("o/", id="empty_name"),
        pytest.param("/r", id="empty_owner"),
        pytest.param("o/r\n", id="trailing_newline"),
        pytest.param("o/r\u0661", id="non_ascii_digit_arabic_indic_one"),
        pytest.param("o/r:x", id="colon"),
        pytest.param("o /r", id="space"),
    ])
    def test_rejects(self, owner_repo):
        assert gh_cli._is_plain_owner_repo(owner_repo) is False

    @pytest.mark.parametrize("owner_repo", [
        pytest.param("a" * _PATHOLOGICAL_LENGTH + "*/r", id="bad_character_after_a_long_owner"),
        pytest.param("o/" + "a" * _PATHOLOGICAL_LENGTH + "*", id="bad_character_after_a_long_name"),
        pytest.param("." * _PATHOLOGICAL_LENGTH + "/" + "." * _PATHOLOGICAL_LENGTH, id="long_segments_without_a_letter_or_digit"),
        pytest.param("/".join(["a"] * _PATHOLOGICAL_LENGTH), id="many_segments"),
    ])
    @pytest.mark.timing
    def test_rejects_a_very_long_failing_input_within_a_generous_bound(self, owner_repo):
        started = time.perf_counter()

        assert gh_cli._is_plain_owner_repo(owner_repo) is False

        assert time.perf_counter() - started < _LINEAR_TIME_BOUND_S


# Reads hex-encoded owner/repo arguments so control characters survive argv; prints one 1 or 0 verdict per line.
# The trailing "." keeps command substitution from stripping a final newline.
_BASH_OWNER_REPO_VERDICTS_SCRIPT = (
    '. "$1/_lib.sh" || { echo "_lib.sh did not load" >&2; exit 1; }; shift; '
    'declare -F _lib_parse_pr_identity >/dev/null || { echo "_lib_parse_pr_identity undefined" >&2; exit 1; }; '
    'for encoded in "$@"; do '
    'decoded=$(printf "%b." "$encoded"); owner_repo=${decoded%.}; '
    'if _lib_parse_pr_identity "$owner_repo#1" >/dev/null; then echo 1; else echo 0; fi; '
    "done"
)


def _bash_owner_repo_verdicts(owner_repos: list[str]) -> tuple[list[bool], str]:
    """Per owner/repo, whether _lib_parse_pr_identity in _lib.sh accepts it with a valid PR number, plus bash's stderr.

    Runs one bash process under LC_ALL=C so bracket ranges are ASCII-only whatever locale the host bash widens them in.
    The child environment holds only PATH and the locale pin, so BASH_ENV and exported functions cannot reach it."""
    encoded = ["".join(f"\\x{byte:02x}" for byte in owner_repo.encode("utf-8")) for owner_repo in owner_repos]
    result = subprocess.run(
        ["bash", "-c", _BASH_OWNER_REPO_VERDICTS_SCRIPT, "_", str(HOOKS_DIR), *encoded],
        env={"PATH": os.environ.get("PATH", os.defpath), "LC_ALL": "C"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"bash exited {result.returncode}; stderr: {result.stderr!r}"
    verdicts = [verdict == "1" for verdict in result.stdout.splitlines()]
    assert len(verdicts) == len(owner_repos), (
        f"bash printed {len(verdicts)} verdicts for {len(owner_repos)} inputs; stderr: {result.stderr!r}"
    )
    return verdicts, result.stderr


def _bash_accepts_owner_repo(owner_repo: str) -> bool:
    return _bash_owner_repo_verdicts([owner_repo])[0][0]


class TestIsPlainOwnerRepoParityWithLibParsePrIdentity:
    @pytest.mark.parametrize("owner_repo", [
        pytest.param("acme/widgets", id="plain"),
        pytest.param("a.b/c_d-e", id="dot_underscore_hyphen"),
        pytest.param("acme/.github", id="leading_dot_name_with_a_letter"),
        pytest.param("acme/repo.", id="trailing_dot_name_with_a_letter"),
        pytest.param(".o./.r.", id="dot_wrapped_segments_with_a_letter"),
        pytest.param("0/0", id="single_digit_segments"),
        pytest.param("./r", id="dot_owner"),
        pytest.param("o/..", id="dot_dot_name"),
        pytest.param("../r", id="dot_dot_owner"),
        pytest.param("o/_", id="underscore_only_name"),
        pytest.param("-/r", id="hyphen_only_owner"),
        pytest.param("o/r*", id="glob_character"),
        pytest.param("pw@host/project", id="at_sign"),
        pytest.param("o/r/x", id="third_segment"),
        pytest.param("o", id="missing_name"),
        pytest.param("o/", id="empty_name"),
        pytest.param("/r", id="empty_owner"),
        pytest.param("/", id="both_segments_empty"),
        pytest.param("", id="empty"),
        pytest.param("o/r\n", id="trailing_newline"),
        pytest.param("o\n/r", id="newline_in_owner"),
        pytest.param("o/r\u0661", id="non_ascii_digit_arabic_indic_one"),
        pytest.param("o/r\u00e9", id="non_ascii_letter"),
        pytest.param("o/r\u212a", id="non_ascii_kelvin_sign_beside_an_ascii_letter"),
        pytest.param("o/a\u017f", id="non_ascii_long_s_beside_an_ascii_letter"),
        pytest.param("o/r:x", id="colon"),
        pytest.param("o /r", id="space"),
        pytest.param("-o/r", id="leading_hyphen_owner"),
        pytest.param("o-/r", id="trailing_hyphen_owner"),
        pytest.param("o/-r", id="leading_hyphen_name"),
        pytest.param("o/r-", id="trailing_hyphen_name"),
        pytest.param("_o/r", id="leading_underscore_owner"),
        pytest.param("o/r_", id="trailing_underscore_name"),
        pytest.param(".o/r", id="leading_dot_owner_with_a_letter"),
        pytest.param(" o/r", id="leading_space"),
        pytest.param("o/r ", id="trailing_space"),
        pytest.param("\no/r", id="leading_newline"),
    ])
    def test_agrees_with_the_bash_parser(self, owner_repo):
        assert gh_cli._is_plain_owner_repo(owner_repo) is _bash_accepts_owner_repo(owner_repo)

    def test_agrees_with_the_bash_parser_for_every_ascii_character_in_owner_and_name_position(self):
        owner_repos = [
            owner_repo
            for code_point in range(1, 128)
            for owner_repo in (f"a{chr(code_point)}b/repo", f"owner/a{chr(code_point)}b")
        ]

        bash_verdicts, bash_stderr = _bash_owner_repo_verdicts(owner_repos)

        disagreements = [
            (owner_repo, python_verdict, bash_verdict)
            for owner_repo, bash_verdict in zip(owner_repos, bash_verdicts, strict=True)
            if (python_verdict := gh_cli._is_plain_owner_repo(owner_repo)) is not bash_verdict
        ]
        assert not disagreements, f"(owner_repo, python, bash) disagreements: {disagreements!r}; bash stderr: {bash_stderr!r}"


class TestVettedOriginIdentityFromUrl:
    @pytest.mark.parametrize("url,expected", [
        pytest.param("https://github.com/Owner/Repo.git", ("github.com", "owner/repo"), id="https_dot_git"),
        pytest.param("git@github.com:o/r.git", ("github.com", "o/r"), id="scp_with_git_user"),
        pytest.param("ssh://git@ghe.example/o/r.git", ("ghe.example", "o/r"), id="ssh_scheme"),
        pytest.param("ghe.example:o/r", ("ghe.example", "o/r"), id="scp_without_user"),
        pytest.param("git@ssh-alias:o/r", ("ssh-alias", "o/r"), id="single_label_alias_host"),
        pytest.param("https://GitHub.COM/O/R.git", ("github.com", "o/r"), id="uppercase_host_and_owner_repo"),
    ])
    def test_accepts_and_lowercases(self, url, expected):
        assert gh_cli._vetted_origin_identity_from_url(url) == expected

    @pytest.mark.parametrize("url", [
        pytest.param("https://user:pw@host/project.git", id="password_userinfo"),
        pytest.param("ssh://git@host:2222/r.git", id="port"),
        pytest.param("somedir/o/r", id="relative_path"),
        pytest.param("git@host/o/r", id="scp_prefix_with_slash_separator"),
        pytest.param("../o/r.git", id="parent_relative_path"),
        pytest.param("./o/r", id="current_relative_path"),
        pytest.param("https://TOKEN@host/o/r", id="token_only_userinfo"),
        pytest.param("user:TOKEN@host/r", id="scp_style_user_and_token_userinfo"),
        pytest.param("user:TOKEN@host/o/r", id="scp_style_user_and_token_userinfo_with_three_segments"),
        pytest.param("https://ghe.example:8443/r", id="https_with_port"),
        pytest.param("https://ghe.example:8443/o/r", id="https_with_port_and_owner"),
        pytest.param("alice@host:o/r", id="scp_user_other_than_git"),
    ])
    def test_rejects_by_form(self, url):
        assert gh_cli._vetted_origin_identity_from_url(url) is None

    @pytest.mark.parametrize("url", [
        pytest.param("https://../o/r", id="dot_dot_host"),
        pytest.param("https://./o/r", id="dot_host"),
        pytest.param("https://-x.example/o/r", id="leading_hyphen_label"),
        pytest.param("https://github.com./o/r", id="trailing_dot"),
        pytest.param("https://a..b/o/r", id="empty_label"),
    ])
    def test_rejects_by_host(self, url):
        assert gh_cli._origin_identity_from_url(url) is not None  # form and name are plain, so only the host rule rejects it
        assert gh_cli._vetted_origin_identity_from_url(url) is None

    @pytest.mark.parametrize("url", [
        pytest.param("ext::sh -c id/r", id="transport_helper"),
        pytest.param("https://github.com/o/r*", id="glob_in_name"),
        pytest.param("https://github.com/./r", id="dot_owner"),
        pytest.param("https://github.com/o/..", id="dot_dot_name"),
        pytest.param("https://github.com/o p/r", id="space_in_owner"),
    ])
    def test_rejects_by_owner_and_name(self, url):
        assert gh_cli._vetted_origin_identity_from_url(url) is None

    @pytest.mark.parametrize("url", [
        pytest.param("https://github.com/" + "a" * _PATHOLOGICAL_LENGTH + "*/r", id="bad_character_after_a_long_owner"),
        pytest.param("https://github.com/o/" + "a" * _PATHOLOGICAL_LENGTH + "*", id="bad_character_after_a_long_name"),
        pytest.param("https://" + "a-" * (_PATHOLOGICAL_LENGTH // 2) + "/o/r", id="long_host_ending_in_a_hyphen"),
        pytest.param("https://" + "a." * (_PATHOLOGICAL_LENGTH // 2) + "-/o/r", id="long_dotted_host_with_a_bad_label"),
    ])
    @pytest.mark.timing
    def test_rejects_a_very_long_failing_input_within_a_generous_bound(self, url):
        started = time.perf_counter()

        assert gh_cli._vetted_origin_identity_from_url(url) is None

        assert time.perf_counter() - started < _LINEAR_TIME_BOUND_S


_GIT_REPOSITORY_LOCAL_NAMES_UNDER_TEST = (
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
    "GIT_OBJECT_DIRECTORY", "GIT_DIR", "GIT_WORK_TREE", "GIT_IMPLICIT_WORK_TREE", "GIT_GRAFT_FILE",
    "GIT_INDEX_FILE", "GIT_NO_REPLACE_OBJECTS", "GIT_REPLACE_REF_BASE", "GIT_PREFIX", "GIT_SHALLOW_FILE",
    "GIT_COMMON_DIR",
)
_GH_TOKEN_NAMES_UNDER_TEST = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")
# Names that share a prefix with a scrubbed name but are not in either scrub list, plus one unrelated name.
_PASSTHROUGH_NAMES_UNDER_TEST = (
    "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "GIT_SSH_COMMAND", "GH_HOST", "GITHUB_ACTIONS", "GITHUB_WORKSPACE",
    "UNRELATED_VARIABLE_FOR_PASSTHROUGH",
)
_EXPECTED_ORIGIN_GIT_ARGV_PREFIX = ["git", "--no-pager", "-c", "safe.bareRepository=explicit", "-C"]
_EXPECTED_ORIGIN_GIT_ARGV_SUFFIX = ["remote", "get-url", "origin"]


class TestGhTokenEnvVars:
    # Exact pin: every child process that scrubs gh credentials reads this constant, and this file exercises only the
    # _origin_identity_at child, so a dropped name would otherwise go unnoticed for the others.
    def test_names_the_four_variables_gh_reads_as_tokens(self):
        assert gh_cli._GH_TOKEN_ENV_VARS == _GH_TOKEN_NAMES_UNDER_TEST


# git splits GIT_CEILING_DIRECTORIES on colons and skips relative entries, so none of these can be one ceiling entry.
_HOMES_THAT_CANNOT_BE_A_CEILING = [
    pytest.param(None, id="unset"),
    pytest.param("", id="empty"),
    pytest.param("/srv/someone:else", id="contains_colon"),
    pytest.param("relative/home", id="relative"),
]


class TestOriginIdentityAtFakedRun:
    @staticmethod
    def _record_run(monkeypatch, result) -> dict:
        captured: dict = {"calls": 0}

        def fake_run(cmd, *a, **kw):
            captured["calls"] += 1
            captured["cmd"] = cmd
            captured["kwargs"] = kw
            return result

        monkeypatch.setattr(subprocess, "run", fake_run)
        return captured

    def test_issues_exactly_this_git_argv_with_capture_decoding_and_timeout(self, monkeypatch, capfd):
        captured = self._record_run(monkeypatch, _gh_result(stdout="https://github.com/Owner/Repo.git\n"))

        identity = gh_cli._origin_identity_at("/some/dir")
        output = capfd.readouterr()

        assert identity == ("github.com", "owner/repo")
        assert captured["cmd"] == [*_EXPECTED_ORIGIN_GIT_ARGV_PREFIX, "/some/dir", *_EXPECTED_ORIGIN_GIT_ARGV_SUFFIX]
        kwargs = captured["kwargs"]
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["errors"] == "replace"
        assert kwargs["timeout"] == gh_cli._GIT_REMOTE_ORIGIN_TIMEOUT_S
        assert (output.out, output.err) == ("", "")

    def test_uppercase_host_in_the_origin_url_comes_back_lowercased(self, monkeypatch):
        self._record_run(monkeypatch, _gh_result(stdout="https://GitHub.COM/Owner/Repo.git\n"))

        assert gh_cli._origin_identity_at("/some/dir") == ("github.com", "owner/repo")

    def test_environment_lacks_every_repository_local_and_token_variable_even_when_exported(self, monkeypatch):
        for name in (*_GIT_REPOSITORY_LOCAL_NAMES_UNDER_TEST, *_GH_TOKEN_NAMES_UNDER_TEST):
            monkeypatch.setenv(name, "exported-value")
        for name in _PASSTHROUGH_NAMES_UNDER_TEST:
            monkeypatch.setenv(name, f"kept-{name}")
        captured = self._record_run(monkeypatch, _gh_result(stdout="https://github.com/o/r.git\n"))

        gh_cli._origin_identity_at("/some/dir")

        child_env = captured["kwargs"]["env"]
        scrubbed_names = (*_GIT_REPOSITORY_LOCAL_NAMES_UNDER_TEST, *_GH_TOKEN_NAMES_UNDER_TEST)
        assert [name for name in scrubbed_names if name in child_env] == []
        assert {name: child_env.get(name) for name in _PASSTHROUGH_NAMES_UNDER_TEST} == {
            name: f"kept-{name}" for name in _PASSTHROUGH_NAMES_UNDER_TEST
        }

    def test_ceiling_directories_equals_home_when_nothing_was_set(self, monkeypatch):
        monkeypatch.setenv("HOME", "/srv/someone")
        monkeypatch.delenv("GIT_CEILING_DIRECTORIES", raising=False)
        captured = self._record_run(monkeypatch, _gh_result(stdout="https://github.com/o/r.git\n"))

        gh_cli._origin_identity_at("/some/dir")

        assert captured["kwargs"]["env"]["GIT_CEILING_DIRECTORIES"] == "/srv/someone"

    def test_ceiling_directories_puts_home_before_an_already_set_value(self, monkeypatch):
        monkeypatch.setenv("HOME", "/srv/someone")
        monkeypatch.setenv("GIT_CEILING_DIRECTORIES", "/slow/mount")
        captured = self._record_run(monkeypatch, _gh_result(stdout="https://github.com/o/r.git\n"))

        gh_cli._origin_identity_at("/some/dir")

        assert captured["kwargs"]["env"]["GIT_CEILING_DIRECTORIES"] == "/srv/someone:/slow/mount"

    @staticmethod
    def _set_home(monkeypatch, home_value: str | None) -> None:
        if home_value is None:
            monkeypatch.delenv("HOME", raising=False)
        else:
            monkeypatch.setenv("HOME", home_value)

    @pytest.mark.parametrize("home_value", _HOMES_THAT_CANNOT_BE_A_CEILING)
    def test_a_set_ceiling_passes_through_unchanged_without_a_usable_home(self, monkeypatch, home_value):
        self._set_home(monkeypatch, home_value)
        monkeypatch.setenv("GIT_CEILING_DIRECTORIES", "/slow/mount")
        captured = self._record_run(monkeypatch, _gh_result(stdout="https://github.com/o/r.git\n"))

        gh_cli._origin_identity_at("/some/dir")

        assert captured["kwargs"]["env"]["GIT_CEILING_DIRECTORIES"] == "/slow/mount"

    @pytest.mark.parametrize("home_value", _HOMES_THAT_CANNOT_BE_A_CEILING)
    def test_an_unset_ceiling_stays_absent_without_a_usable_home(self, monkeypatch, home_value):
        self._set_home(monkeypatch, home_value)
        monkeypatch.delenv("GIT_CEILING_DIRECTORIES", raising=False)
        captured = self._record_run(monkeypatch, _gh_result(stdout="https://github.com/o/r.git\n"))

        gh_cli._origin_identity_at("/some/dir")

        assert "GIT_CEILING_DIRECTORIES" not in captured["kwargs"]["env"]

    @pytest.mark.parametrize("outcome", [
        pytest.param(_gh_result(2, stderr="fatal: not a git repository\n"), id="exit_2"),
        pytest.param(subprocess.TimeoutExpired(cmd=["git"], timeout=1), id="timeout_expired"),
        pytest.param(OSError("git not found"), id="os_error"),
        pytest.param(ValueError("embedded null byte"), id="value_error"),
        pytest.param(_gh_result(stdout="not a remote\n"), id="unparseable_url"),
        pytest.param(_gh_result(stdout="https://user:SENTINEL-SECRET@host/project.git\n"), id="credentialed_url"),
        pytest.param(_gh_result(stdout="../o/r.git\n"), id="relative_path_url"),
        pytest.param(_gh_result(stdout="ext::sh -c id/r\n"), id="transport_helper_url"),
    ])
    def test_unresolvable_directory_returns_none_and_prints_nothing(self, monkeypatch, capfd, outcome):
        def fake_run(cmd, *a, **kw):
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert gh_cli._origin_identity_at("/some/dir") is None

        captured = capfd.readouterr()
        assert captured.out == ""
        assert captured.err == ""

    def test_path_like_directory_passes_its_fspath_string_to_git(self, monkeypatch):
        captured = self._record_run(monkeypatch, _gh_result(stdout="https://github.com/o/r.git\n"))

        identity = gh_cli._origin_identity_at(Path("/abs/dir"))

        assert identity == ("github.com", "o/r")
        assert captured["calls"] == 1
        assert captured["cmd"] == [*_EXPECTED_ORIGIN_GIT_ARGV_PREFIX, "/abs/dir", *_EXPECTED_ORIGIN_GIT_ARGV_SUFFIX]
        directory_argument = captured["cmd"][len(_EXPECTED_ORIGIN_GIT_ARGV_PREFIX)]
        assert type(directory_argument) is str

    def test_path_like_whose_fspath_is_bytes_returns_none_without_a_git_call(self, monkeypatch):
        class BytesPath:
            def __fspath__(self):
                return b"/abs"

        captured = self._record_run(monkeypatch, _gh_result(stdout="https://github.com/o/r.git\n"))

        assert gh_cli._origin_identity_at(BytesPath()) is None
        assert captured["calls"] == 0

    @pytest.mark.parametrize("directory", [
        pytest.param("", id="empty_string"),
        pytest.param(".", id="dot"),
        pytest.param("relative/dir", id="relative"),
        pytest.param("-C", id="leading_hyphen"),
        pytest.param("a\x00b", id="embedded_nul_relative"),
        pytest.param(None, id="none"),
        pytest.param(42, id="int"),
        pytest.param(b"/abs", id="bytes"),
    ])
    def test_invalid_directory_returns_none_without_a_git_call(self, monkeypatch, directory):
        captured = self._record_run(monkeypatch, _gh_result(stdout="https://github.com/o/r.git\n"))

        assert gh_cli._origin_identity_at(directory) is None
        assert captured["calls"] == 0

    def test_embedded_nul_in_an_absolute_path_is_rejected_by_the_real_subprocess_run(self, capfd):
        assert gh_cli._origin_identity_at("/nul\x00path") is None

        captured = capfd.readouterr()
        assert captured.out == ""
        assert captured.err == ""


def _git_version_at_least(minimum: tuple[int, int]) -> bool:
    if shutil.which("git") is None:
        return False
    try:
        version_text = subprocess.run(["git", "--version"], capture_output=True, text=True, check=True).stdout
    except (subprocess.CalledProcessError, OSError):
        return False
    version_match = re.search(r"(\d+)\.(\d+)", version_text)
    return version_match is not None and (int(version_match.group(1)), int(version_match.group(2))) >= minimum


_ORIGIN_URL = "https://github.com/Acme/Widget.git"
_ORIGIN_IDENTITY = ("github.com", "acme/widget")
_OTHER_ORIGIN_URL = "https://github.com/Other/Elsewhere.git"
_requires_safe_bare_repository = pytest.mark.skipif(
    not _git_version_at_least((2, 38)),
    reason="needs git 2.38 or later for safe.bareRepository; skipped here means the bare-layout deny path went unchecked",
)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


def _write_sentinel_script(path: Path, log_path: Path, marker: str, *, exit_code: int = 0) -> Path:
    """An executable that appends `marker` to `log_path` whatever its environment holds."""
    path.write_text(f"#!/bin/sh\necho {marker} >> '{log_path}'\nexit {exit_code}\n")
    path.chmod(0o755)
    return path


def _escape_for_ext_transport(command_path: Path) -> str:
    """Escapes a command path for an `ext::<command>` remote URL: a percent sign doubles, a space follows a percent sign."""
    return str(command_path).replace("%", "%%").replace(" ", "% ")


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git on PATH")
class TestOriginIdentityAtRealGit:
    """_origin_identity_at against real repositories. HOME and every repository the class builds sit under
    tmp_path, so the ceiling keeps discovery out of whatever encloses it and the machine's own git config never
    takes part."""

    @pytest.fixture(autouse=True)
    def isolated_git_environment(self, monkeypatch, tmp_path):
        for name in [name for name in os.environ if name.startswith("GIT_")]:
            monkeypatch.delenv(name)
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
        monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
        home = tmp_path / "home"
        (home / "xdg").mkdir(parents=True)
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "xdg"))
        self.home = home
        self.sentinel_log = tmp_path / "sentinel.log"

    def _clone_with_origin(self, name: str = "clone", origin_url: str = _ORIGIN_URL) -> Path:
        clone = self.home / name
        _init_repo(clone)
        _commit(clone, "init")
        _git(clone, "remote", "add", "origin", origin_url).check_returncode()
        return clone

    def _bare_repo_with_origin(self, name: str = "bare.git") -> Path:
        seed = self.home / f"{name}-seed"
        _init_repo(seed)
        _commit(seed, "init")
        bare = self.home / name
        subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(bare)], check=True)
        _git(bare, "config", "remote.origin.url", _ORIGIN_URL).check_returncode()
        return bare

    @staticmethod
    def _identity_at_silently(capfd, directory: Path):
        capfd.readouterr()  # drop output from fixture setup
        identity = gh_cli._origin_identity_at(directory)
        captured = capfd.readouterr()
        assert captured.out == ""
        assert captured.err == ""
        return identity

    @staticmethod
    def _direct_origin_url(directory: Path) -> str:
        """Prints what a bare `git -C <directory> remote get-url origin` says, with none of the production call's hardening."""
        result = subprocess.run(
            ["git", "-C", str(directory), "remote", "get-url", "origin"],
            capture_output=True, text=True, check=False,
        )
        return result.stdout.strip()

    def test_clone_root_and_subdirectory_resolve_to_the_identity(self, capfd):
        clone = self._clone_with_origin()
        subdirectory = clone / "nested" / "deeper"
        subdirectory.mkdir(parents=True)

        assert self._identity_at_silently(capfd, clone) == _ORIGIN_IDENTITY
        assert self._identity_at_silently(capfd, subdirectory) == _ORIGIN_IDENTITY

    def test_linked_worktree_of_a_clone_resolves_to_the_identity(self, capfd):
        clone = self._clone_with_origin()
        linked = self.home / "linked"
        _git(clone, "worktree", "add", "-q", "-b", "feature", str(linked)).check_returncode()

        assert self._identity_at_silently(capfd, linked) == _ORIGIN_IDENTITY

    def test_linked_worktree_of_a_bare_repository_resolves_to_the_identity(self, capfd):
        bare = self._bare_repo_with_origin()
        linked = self.home / "bare-linked"
        _git(bare, "worktree", "add", "-q", str(linked), "main").check_returncode()

        assert self._identity_at_silently(capfd, linked) == _ORIGIN_IDENTITY

    def test_plain_directory_nonexistent_path_and_regular_file_return_none(self, capfd):
        plain = self.home / "plain"
        plain.mkdir()
        regular_file = self.home / "file.txt"
        regular_file.write_text("x\n")

        assert self._identity_at_silently(capfd, plain) is None
        assert self._identity_at_silently(capfd, self.home / "does-not-exist") is None
        assert self._identity_at_silently(capfd, regular_file) is None

    def test_clone_whose_origin_remote_was_removed_returns_none(self, capfd):
        clone = self._clone_with_origin()
        _git(clone, "remote", "remove", "origin").check_returncode()

        assert self._identity_at_silently(capfd, clone) is None

    def test_home_that_is_a_repository_resolves_itself_but_not_plain_folders_under_it(self, monkeypatch, capfd):
        home_repo = self._clone_with_origin(name="home-repo")
        folder_under_home = home_repo / "untracked-folder"
        folder_under_home.mkdir()
        monkeypatch.setenv("HOME", str(home_repo))

        control_url = self._direct_origin_url(folder_under_home)
        assert control_url == _ORIGIN_URL, "control: with no ceiling the folder finds HOME's repository"
        assert self._identity_at_silently(capfd, home_repo) == _ORIGIN_IDENTITY
        assert self._identity_at_silently(capfd, folder_under_home) is None

    def test_exported_git_dir_pointing_at_another_repository_leaves_results_unchanged(self, monkeypatch, capfd):
        clone = self._clone_with_origin()
        other_repo = self._clone_with_origin(name="other", origin_url=_OTHER_ORIGIN_URL)
        plain = self.home / "plain"
        plain.mkdir()
        monkeypatch.setenv("GIT_DIR", str(other_repo / ".git"))

        assert self._direct_origin_url(clone) == _OTHER_ORIGIN_URL, "control: with no scrub the exported GIT_DIR wins"
        assert self._direct_origin_url(plain) == _OTHER_ORIGIN_URL, "control: with no scrub the exported GIT_DIR wins"
        assert self._identity_at_silently(capfd, clone) == _ORIGIN_IDENTITY
        assert self._identity_at_silently(capfd, plain) is None

    @_requires_safe_bare_repository
    def test_bare_repository_directory_itself_returns_none(self, capfd):
        bare = self._bare_repo_with_origin()

        assert self._direct_origin_url(bare) == _ORIGIN_URL, "control: with no -c pair the bare layout is read as a repository"
        assert self._identity_at_silently(capfd, bare) is None

    @_requires_safe_bare_repository
    def test_bare_layout_committed_inside_a_clone_returns_none_from_itself_and_from_a_folder_inside_it(self, capfd):
        clone = self._clone_with_origin()
        embedded = clone / "embedded"
        (embedded / "objects").mkdir(parents=True)
        (embedded / "refs").mkdir()
        (embedded / "objects" / ".keep").write_text("")
        (embedded / "refs" / ".keep").write_text("")
        (embedded / "HEAD").write_text("ref: refs/heads/main\n")
        (embedded / "config").write_text(
            f'[core]\n\tbare = true\n[remote "origin"]\n\turl = {_OTHER_ORIGIN_URL}\n'
        )
        _git(clone, "add", "embedded").check_returncode()
        _git(clone, "commit", "-q", "-m", "commit a bare layout").check_returncode()
        inside_layout = embedded / "refs"

        for layout_directory in (embedded, inside_layout):
            control_url = self._direct_origin_url(layout_directory)
            assert control_url == _OTHER_ORIGIN_URL, "control: with no -c pair the layout is read as a repository"
        assert self._identity_at_silently(capfd, embedded) is None
        assert self._identity_at_silently(capfd, inside_layout) is None

    def test_hostile_clone_config_runs_no_configured_program(self, capfd):
        """Pins that production runs a command executing no configured program (the command class), not that the hardening
        flags defend against hostile config."""
        clone = self._clone_with_origin()
        hooks_dir = self.home / "hostile-hooks"
        hooks_dir.mkdir()
        fsmonitor_script = _write_sentinel_script(self.home / "fsmonitor.sh", self.sentinel_log, "core.fsmonitor")
        _write_sentinel_script(hooks_dir / "post-index-change", self.sentinel_log, "post-index-change")
        _write_sentinel_script(hooks_dir / "reference-transaction", self.sentinel_log, "reference-transaction")
        _git(clone, "config", "core.fsmonitor", str(fsmonitor_script)).check_returncode()
        _git(clone, "config", "core.hooksPath", str(hooks_dir)).check_returncode()

        # Each control starts from an empty log, so its marker can only come from the command it ran.
        _git(clone, "status")
        assert "core.fsmonitor" in self.sentinel_log.read_text(), "control: git status refreshes the index through core.fsmonitor"
        self.sentinel_log.unlink()
        (clone / "new-file.txt").write_text("x\n")
        _git(clone, "add", "new-file.txt")
        assert "post-index-change" in self.sentinel_log.read_text(), "control: git add fires post-index-change"
        self.sentinel_log.unlink()
        _git(clone, "update-ref", "refs/heads/probe", "HEAD")
        assert "reference-transaction" in self.sentinel_log.read_text(), "control: update-ref fires reference-transaction"
        self.sentinel_log.unlink()

        assert self._identity_at_silently(capfd, clone) == _ORIGIN_IDENTITY
        assert not self.sentinel_log.exists()

    def test_transport_helper_origin_runs_no_program_and_returns_none(self, capfd):
        """Pins that production runs a command executing no configured program (the command class), not that the hardening
        flags defend against hostile config."""
        clone = self._clone_with_origin(name="ext-clone")
        ext_script = _write_sentinel_script(self.home / "ext helper.sh", self.sentinel_log, "ext", exit_code=1)
        _git(clone, "config", "remote.origin.url", f"ext::{_escape_for_ext_transport(ext_script)}").check_returncode()
        _git(clone, "config", "protocol.ext.allow", "always").check_returncode()

        _git(clone, "ls-remote", "origin")
        assert "ext" in self.sentinel_log.read_text(), "control: ls-remote runs the transport helper"
        self.sentinel_log.unlink()

        assert self._identity_at_silently(capfd, clone) is None
        assert not self.sentinel_log.exists()

    def test_repository_local_variable_list_names_only_scrubbed_variables(self):
        plain = self.home / "plain"
        plain.mkdir()

        printed = _git(plain, "rev-parse", "--local-env-vars")

        assert printed.returncode == 0
        assert set(printed.stdout.split()) <= set(gh_cli._GIT_REPOSITORY_LOCAL_ENV_VARS)
