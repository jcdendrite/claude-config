"""Tests for transcript_analysis/gh_cli.py (gh stderr classification, rate-limit backoff, auth
preflight, effective-repo pinning, and merged/closed PR discovery)."""
import importlib.util
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ._pr_cost_helpers import _enable_pr_cost, _fake_pr_cost_subprocess_run, _pr_cost_args

_SCRIPT = Path(__file__).parent.parent / "transcript-analysis.py"
# "transcript_analysis" below never touches sys.modules (module_from_spec + exec_module
# alone doesn't register it), so it can't shadow the real transcript_analysis package --
# switching to the standard importlib recipe (which does register in sys.modules) would.
_spec = importlib.util.spec_from_file_location("transcript_analysis", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(_SCRIPT.parent))
_spec.loader.exec_module(_mod)


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
        _mod.gh_cli._gh_discover_merged_prs("github.com", "owner/repo")

        argv = captured["cmd"]
        assert "--limit" in argv
        limit_value = int(argv[argv.index("--limit") + 1])
        assert limit_value == _mod.gh_cli._PR_COST_GH_PR_LIST_LIMIT
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
        _mod.gh_cli._gh_discover_closed_unmerged_pr_branches("github.com", "owner/repo")

        argv = captured["cmd"]
        assert argv[argv.index("--state") + 1] == "closed"
        assert "--limit" in argv
        assert int(argv[argv.index("--limit") + 1]) == _mod.gh_cli._PR_COST_GH_PR_LIST_LIMIT

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
        result = _mod.gh_cli._gh_discover_closed_unmerged_pr_branches("github.com", "owner/repo")
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
        result = _mod.gh_cli._gh_discover_closed_unmerged_pr_branches("github.com", "owner/repo")
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
            _mod.gh_cli._gh_discover_closed_unmerged_pr_branches("github.com", "owner/repo")

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
            _mod.gh_cli._gh_discover_closed_unmerged_pr_branches("github.com", "owner/repo")

        assert exc_info.value.code == 1
        assert "unparseable JSON" in capsys.readouterr().err


class TestClassifyGhError:
    @pytest.mark.parametrize("stderr", [
        "You are not logged into any GitHub hosts. Run gh auth login to authenticate.\n",
        "authentication failed for repository 'https://github.com/owner/repo/'\n",
        "HTTP 401: Bad credentials\n",
    ])
    def test_auth_shaped_stderr_classified_as_auth(self, stderr):
        assert _mod.gh_cli._classify_gh_error(stderr) == _mod.gh_cli._GH_ERROR_KIND_AUTH

    @pytest.mark.parametrize("stderr", [
        # Verified against gh 2.97.0's own stderr for an ambient GH_HOST that
        # doesn't match any configured git remote.
        "none of the git remotes configured for this repository correspond to the"
        " GH_HOST environment variable. Try adding a matching remote or unsetting"
        " the variable\n",
    ])
    def test_gh_host_mismatch_stderr_classified_as_host_mismatch(self, stderr):
        assert _mod.gh_cli._classify_gh_error(stderr) == _mod.gh_cli._GH_ERROR_KIND_HOST_MISMATCH

    @pytest.mark.parametrize("stderr", [
        "API rate limit exceeded for user ID 123.\n",
        "HTTP 429: Too Many Requests\n",
        "HTTP 403: Forbidden\n",
    ])
    def test_rate_limit_shaped_stderr_classified_as_rate_limit(self, stderr):
        assert _mod.gh_cli._classify_gh_error(stderr) == _mod.gh_cli._GH_ERROR_KIND_RATE_LIMIT

    @pytest.mark.parametrize("stderr", [
        "curl: (6) Could not resolve host: api.github.com\n",
        "connection reset by peer\n",
        "\n",  # unrecognized/empty stderr falls back to network, not a crash
    ])
    def test_unrecognized_stderr_falls_back_to_network(self, stderr):
        assert _mod.gh_cli._classify_gh_error(stderr) == _mod.gh_cli._GH_ERROR_KIND_NETWORK


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
        assert _mod.gh_cli._git_remote_origin_host_and_owner_repo() == expected

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
            _mod.gh_cli._git_remote_origin_host_and_owner_repo()
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
            _mod.gh_cli._git_remote_origin_host_and_owner_repo()
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
            _mod.gh_cli._git_remote_origin_host_and_owner_repo()
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
            _mod.gh_cli._git_remote_origin_host_and_owner_repo()
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
            _mod.gh_cli._git_remote_origin_host_and_owner_repo()
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
            _mod.gh_cli._git_remote_origin_host_and_owner_repo()
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
            _mod.gh_cli._git_remote_origin_host_and_owner_repo()
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
        assert _mod.gh_cli._gh_auth_preflight_ok("acme-corp.ghe.com") is True
        assert call_log == [["gh", "auth", "status", "--hostname", "acme-corp.ghe.com"]]

    def test_nonzero_exit_returns_false(self, monkeypatch):
        monkeypatch.setattr(
            subprocess, "run",
            lambda cmd, *a, **kw: type("R", (), {"returncode": 1, "stdout": "", "stderr": "error\n"})(),
        )
        assert _mod.gh_cli._gh_auth_preflight_ok("github.com") is False

    @pytest.mark.parametrize("raised", [
        subprocess.TimeoutExpired(cmd=["gh", "auth", "status"], timeout=1),
        OSError("gh not found"),
    ])
    def test_timeout_or_os_error_returns_false(self, monkeypatch, raised):
        def fake_run(cmd, *a, **kw):
            raise raised

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert _mod.gh_cli._gh_auth_preflight_ok("github.com") is False


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

        proc, degraded = _mod.gh_cli._gh_call_with_backoff(["gh", "repo", "view"], label="repo view")

        assert proc is None
        assert degraded == _mod.gh_cli._GH_CALL_DEGRADED_AUTH
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

        proc, degraded = _mod.gh_cli._gh_call_with_backoff(["gh", "repo", "view"], label="repo view")

        assert proc is None
        assert degraded == _mod.gh_cli._GH_CALL_DEGRADED_HOST_MISMATCH
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

        proc, degraded = _mod.gh_cli._gh_call_with_backoff(["gh", "repo", "view"], label="repo view")

        assert proc is None
        assert degraded == _mod.pr_cost_ledger._PR_COST_STATUS_DEGRADED_RATE_LIMIT
        assert call_count == _mod.gh_cli._PR_COST_RATE_LIMIT_MAX_ATTEMPTS
        assert sleep_calls  # retried at least once before exhausting

    def test_network_shaped_exception_retries_before_returning_degraded(self, monkeypatch):
        """A raised TimeoutExpired (no stderr text at all, unlike the two
        cases above) is still classified network-kind and retried under the
        same budget."""
        call_count = 0

        def fake_run(cmd, *a, **kw):
            nonlocal call_count
            call_count += 1
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=_mod.gh_cli._GH_CALL_TIMEOUT_S)

        sleep_calls: list[float] = []
        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(time, "sleep", lambda s: sleep_calls.append(s))

        proc, degraded = _mod.gh_cli._gh_call_with_backoff(["gh", "repo", "view"], label="repo view")

        assert proc is None
        assert degraded == _mod.pr_cost_ledger._PR_COST_STATUS_DEGRADED_NETWORK
        assert call_count == _mod.gh_cli._PR_COST_RATE_LIMIT_MAX_ATTEMPTS
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
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=_mod.gh_cli._GH_CALL_TIMEOUT_S)
            return type("R", (), {"returncode": 0, "stdout": '{"ok": true}', "stderr": ""})()

        sleep_calls: list[float] = []
        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(time, "sleep", lambda s: sleep_calls.append(s))

        proc, degraded = _mod.gh_cli._gh_call_with_backoff(["gh", "repo", "view"], label="repo view")

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

        proc, degraded = _mod.gh_cli._gh_call_with_backoff(["gh", "pr", "view", "1"], label="pr view 1")

        assert proc is None
        assert degraded == _mod.pr_cost_ledger._PR_COST_STATUS_DEGRADED_RATE_LIMIT
        assert sleep_calls == [60.0, 120.0, 240.0, 480.0]
        assert sum(sleep_calls) == _mod.gh_cli._PR_COST_RATE_LIMIT_MAX_ELAPSED_S

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

        proc, degraded = _mod.gh_cli._gh_call_with_backoff(["gh", "pr", "view", "1"], label="pr view 1")

        assert proc is None
        assert degraded == _mod.pr_cost_ledger._PR_COST_STATUS_DEGRADED_RATE_LIMIT
        assert call_count == 2
        assert sleep_calls == [_mod.gh_cli._PR_COST_RATE_LIMIT_MAX_ELAPSED_S]  # capped, not the raw 999999 value


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
            _mod.gh_cli._resolve_pinned_gh_repo(corpus_host, corpus_repo, ordinal=1)

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
            _mod.gh_cli._resolve_pinned_gh_repo("github.com", corpus_repo, ordinal=1)

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
            _mod.gh_cli._resolve_pinned_gh_repo("acme-corp.ghe.com", same_owner_repo, ordinal=1)

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
            _mod.gh_cli._resolve_pinned_gh_repo("github.com", "owner/repo", ordinal=1)

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
            _mod.gh_cli._resolve_pinned_gh_repo("github.com", "owner/repo", ordinal=1)

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
            _mod.gh_cli._resolve_pinned_gh_repo("github.com", "owner/repo", ordinal=1)

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

        gh_repo, _ = _mod.gh_cli._resolve_pinned_gh_repo("acme-corp.ghe.com", "owner/repo", ordinal=1)

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
            _mod.gh_cli._resolve_pinned_gh_repo("github.com", corpus_repo, ordinal=2)

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
        monkeypatch.setattr(_mod.scope, "declared_transcript_roots", lambda: [])
        active = tmp_path / "zzz-active"
        (active / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: active)
        extra = tmp_path / "aaa-extra"  # resolved-path-sorts before "zzz-active" despite being scanned second
        (extra / "projects").mkdir(parents=True)
        monkeypatch.setattr(
            subprocess, "run",
            _fake_pr_cost_subprocess_run(repo="git-side-owner/git-side-repo",
                                          gh_repo_name_with_owner="gh-side-owner/gh-side-repo"),
        )

        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost.cmd_pr_cost(_pr_cost_args(extra_config_dirs=[str(extra)], all_accounts=True))

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
        _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])

        rows = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())
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
            _mod.pr_cost._pr_cost_report(_pr_cost_args(), datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])
        assert exc_info.value.code == 1  # exhaustion, not the mismatch path's exit(2)

        gh_repo_view_calls = [c for c in call_log if c[:3] == ["gh", "repo", "view"]]
        assert len(gh_repo_view_calls) == _mod.gh_cli._PR_COST_RATE_LIMIT_MAX_ATTEMPTS
        assert len(sleep_calls) == _mod.gh_cli._PR_COST_RATE_LIMIT_MAX_ATTEMPTS - 1

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
            _mod.pr_cost._pr_cost_report(_pr_cost_args(), datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])
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
        assert _mod.gh_cli._gh_host_qualified_repo("acme-corp.ghe.com", "owner/repo") == "acme-corp.ghe.com/owner/repo"
        assert _mod.gh_cli._gh_host_qualified_repo("github.com", "owner/repo") == "github.com/owner/repo"
