"""Tests for transcript_analysis/pr_cost.py's gh-integration paths (cmd_pr_cost's gh-repo-identity
pinning end to end, cross-repo ledger isolation, and redaction of every gh-integration print
site). Local-mechanics coverage (attribution, ledger I/O, join logic, mechanical proxies) lives
in test_transcript_pr_cost.py; gh_cli.py's own unit coverage lives in test_transcript_gh_cli.py."""
import importlib.util
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ._pr_cost_helpers import (
    _argv_carries_repo_pin,
    _enable_pr_cost,
    _fake_pr_cost_subprocess_run,
    _pr_cost_args,
    _sample_pr_cost_row,
)
from .conftest import _priced, _two_declared_roots, _write_jsonl

_SCRIPT = Path(__file__).parent.parent / "transcript-analysis.py"
# "transcript_analysis" below never touches sys.modules (module_from_spec + exec_module
# alone doesn't register it), so it can't shadow the real transcript_analysis package --
# switching to the standard importlib recipe (which does register in sys.modules) would.
_spec = importlib.util.spec_from_file_location("transcript_analysis", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(_SCRIPT.parent))
_spec.loader.exec_module(_mod)
class TestPrCostPerPrEnrichmentRateLimitDegradesRowNotRun:
    def test_perpetual_gh_pr_view_rate_limit_failure_marks_row_degraded_and_still_records(
        self, fake_projects, tmp_path, monkeypatch,
    ):
        """A per-PR `gh pr view` enrichment call that never succeeds marks
        that row's own status column and the run still completes -- only
        repo-identity-resolution/discovery failures (with no row yet to
        degrade into) abort the whole run."""
        _enable_pr_cost(tmp_path)
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch="feature-a"),
        ])
        merged_prs = [{
            "number": 1, "headRefName": "feature-a", "additions": 1, "deletions": 1,
            "changedFiles": 1, "mergedAt": "2026-01-01T00:00:00Z",
        }]
        monkeypatch.setattr(
            subprocess, "run",
            _fake_pr_cost_subprocess_run(
                repo="owner/repo", merged_prs=merged_prs,
                gh_pr_view_failure_stderr="API rate limit exceeded (HTTP 403)\n",
            ),
        )
        monkeypatch.setattr(time, "sleep", lambda s: None)

        args = _pr_cost_args(record=True)
        _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])

        rows = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())
        assert len(rows) == 1
        assert rows[0]["status"] == _mod.pr_cost_ledger._PR_COST_STATUS_DEGRADED_RATE_LIMIT


class TestPrCostPerPrEnrichmentNoRetryFailuresFoldToDegradedNetwork:
    def test_gh_pr_view_auth_shaped_failure_writes_degraded_network_not_degraded_auth(
        self, fake_projects, tmp_path, monkeypatch,
    ):
        """An auth-shaped failure on the per-PR `gh pr view` enrichment call
        (distinct from the M9-equivalent identity-resolution call) folds
        into _PR_COST_STATUS_DEGRADED_NETWORK before reaching the row --
        _GH_CALL_DEGRADED_AUTH is never a valid ledger status value."""
        _enable_pr_cost(tmp_path)
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch="feature-a"),
        ])
        merged_prs = [{
            "number": 1, "headRefName": "feature-a", "additions": 1, "deletions": 1,
            "changedFiles": 1, "mergedAt": "2026-01-01T00:00:00Z",
        }]
        monkeypatch.setattr(
            subprocess, "run",
            _fake_pr_cost_subprocess_run(
                repo="owner/repo", merged_prs=merged_prs,
                gh_pr_view_failure_stderr="You are not logged into any GitHub hosts.\n",
            ),
        )

        args = _pr_cost_args(record=True)
        _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])

        rows = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())
        assert len(rows) == 1
        assert rows[0]["status"] == _mod.pr_cost_ledger._PR_COST_STATUS_DEGRADED_NETWORK

    def test_gh_pr_view_gh_host_mismatch_failure_writes_degraded_network_not_degraded_host_mismatch(
        self, fake_projects, tmp_path, monkeypatch,
    ):
        """Same fold as the auth-shaped case above, for the other
        no-retry-shaped failure kind: _GH_CALL_DEGRADED_HOST_MISMATCH is
        never a valid ledger status value either."""
        _enable_pr_cost(tmp_path)
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch="feature-a"),
        ])
        merged_prs = [{
            "number": 1, "headRefName": "feature-a", "additions": 1, "deletions": 1,
            "changedFiles": 1, "mergedAt": "2026-01-01T00:00:00Z",
        }]
        monkeypatch.setattr(
            subprocess, "run",
            _fake_pr_cost_subprocess_run(
                repo="owner/repo", merged_prs=merged_prs,
                gh_pr_view_failure_stderr=(
                    "none of the git remotes configured for this repository correspond to the"
                    " GH_HOST environment variable. Try adding a matching remote or unsetting"
                    " the variable\n"
                ),
            ),
        )

        args = _pr_cost_args(record=True)
        _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])

        rows = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())
        assert len(rows) == 1
        assert rows[0]["status"] == _mod.pr_cost_ledger._PR_COST_STATUS_DEGRADED_NETWORK


class TestPrCostGhCallsPinnedAfterRepoIdentityResolution:
    def test_every_post_resolution_gh_call_carries_repo_pin(self, fake_projects, tmp_path, monkeypatch):
        """Every `gh pr list`/`gh pr view` call this run makes after
        _resolve_pinned_gh_repo resolves the identity must carry
        --repo <pinned-value> -- the fake
        itself raises AssertionError on a call missing the pin (enforce_repo_pin),
        so a regression that drops it fails loud rather than returning a
        stale canned response; the assertions below are a second, explicit
        check against the captured call log."""
        _enable_pr_cost(tmp_path)
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        _write_jsonl(fake_projects / "sess-a.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch="feature-a"),
        ])
        _write_jsonl(fake_projects / "sess-b.jsonl", [
            _priced("claude-sonnet-5", input=500_000, branch="feature-b"),
        ])
        merged_prs = [
            {"number": 1, "headRefName": "feature-a", "additions": 1, "deletions": 1,
             "changedFiles": 1, "mergedAt": "2026-01-01T00:00:00Z"},
            {"number": 2, "headRefName": "feature-b", "additions": 1, "deletions": 1,
             "changedFiles": 1, "mergedAt": "2026-01-01T00:00:00Z"},
        ]
        call_log: list[list[str]] = []
        monkeypatch.setattr(
            subprocess, "run",
            _fake_pr_cost_subprocess_run(
                repo="owner/repo", merged_prs=merged_prs, call_log=call_log, enforce_repo_pin="owner/repo",
            ),
        )

        args = _pr_cost_args(record=True)
        _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])

        gh_pr_list_calls = [c for c in call_log if c[:3] == ["gh", "pr", "list"]]
        gh_pr_view_calls = [c for c in call_log if c[:3] == ["gh", "pr", "view"]]
        assert len(gh_pr_list_calls) == 1
        assert len(gh_pr_view_calls) == 2
        for cmd in [*gh_pr_list_calls, *gh_pr_view_calls]:
            assert _argv_carries_repo_pin(cmd, "owner/repo")
        # _argv_carries_repo_pin accepts a bare-or-host-qualified suffix match
        # (needed for the GHE case below), so this exact-equality check is the
        # one place confirming the default github.com origin is itself
        # host-qualified to "github.com/owner/repo", not left bare.
        for cmd in [*gh_pr_list_calls, *gh_pr_view_calls]:
            assert cmd[cmd.index("--repo") + 1] == "github.com/owner/repo"


class TestPrCostGheHostQualifiesGhRepoCalls:
    def test_ghe_origin_scopes_auth_preflight_and_host_qualifies_pr_list_and_view(
        self, fake_projects, tmp_path, monkeypatch,
    ):
        """A GHE origin (acme-corp.ghe.com, not github.com) must scope `gh auth
        status` to that host AND host-qualify every `gh pr list`/`gh pr
        view` --repo value the same way -- gh's bare OWNER/REPO --repo form
        always resolves against api.github.com regardless of the invoking
        directory's own git remote, so an unqualified value here would
        silently query the wrong host under the same owner/repo."""
        _enable_pr_cost(tmp_path)
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch="feature-a"),
        ])
        merged_prs = [{
            "number": 1, "headRefName": "feature-a", "additions": 1, "deletions": 1,
            "changedFiles": 1, "mergedAt": "2026-01-01T00:00:00Z",
        }]
        call_log: list[list[str]] = []
        monkeypatch.setattr(
            subprocess, "run",
            _fake_pr_cost_subprocess_run(
                repo="owner/repo", host="acme-corp.ghe.com", merged_prs=merged_prs, call_log=call_log,
            ),
        )

        args = _pr_cost_args(record=True)
        _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])

        assert ["gh", "auth", "status", "--hostname", "acme-corp.ghe.com"] in call_log

        gh_pr_list_calls = [c for c in call_log if c[:3] == ["gh", "pr", "list"]]
        gh_pr_view_calls = [c for c in call_log if c[:3] == ["gh", "pr", "view"]]
        assert len(gh_pr_list_calls) == 1
        assert len(gh_pr_view_calls) == 1
        for cmd in [*gh_pr_list_calls, *gh_pr_view_calls]:
            assert _argv_carries_repo_pin(cmd, "acme-corp.ghe.com/owner/repo")
        # _argv_carries_repo_pin's suffix branch alone can't distinguish a
        # correctly-qualified value from a double-qualified one (e.g. a
        # regression producing "acme-corp.ghe.com/acme-corp.ghe.com/owner/repo"
        # would still pass via the suffix match), so pin the exact value too --
        # mirroring the equivalent check on the default github.com path.
        for cmd in [*gh_pr_list_calls, *gh_pr_view_calls]:
            assert cmd[cmd.index("--repo") + 1] == "acme-corp.ghe.com/owner/repo"


class TestPrCostCrossRepoLedgerIsolation:
    def test_two_repos_recording_the_same_pr_number_persist_as_distinct_rows(
        self, fake_projects, tmp_path, monkeypatch,
    ):
        """(repo, pr_number, machine) is the ledger's own key -- two
        different repos both recording pr_number=42 for the same machine
        must persist as two distinct latest-per-key rows, neither
        superseding the other."""
        _enable_pr_cost(tmp_path)
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        existing_row = _sample_pr_cost_row(repo="repo-a/x", pr_number=42, machine="ci1")
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [existing_row])

        merged_prs = [{
            "number": 42, "headRefName": "ghost-branch", "additions": 1, "deletions": 1,
            "changedFiles": 1, "mergedAt": "2026-01-01T00:00:00Z",
        }]
        monkeypatch.setattr(
            subprocess, "run", _fake_pr_cost_subprocess_run(repo="repo-b/y", merged_prs=merged_prs),
        )

        args = _pr_cost_args(record=True, pr=42)
        _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])

        rows = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())
        assert len(rows) == 2
        by_repo = {r["repo"]: r for r in rows}
        assert set(by_repo) == {"repo-a/x", "repo-b/y"}
        assert by_repo["repo-a/x"]["supersedes"] == ""
        assert by_repo["repo-b/y"]["supersedes"] == ""


class TestPrCostCrossHostLedgerIsolation:
    """(host, repo, pr_number, machine) is the ledger's own key -- a
    same-named owner/repo on two different hosts (e.g. an org mid-migration
    from a GHE instance to github.com, the scenario
    TestResolvePinnedGhRepoIdentity.test_host_mismatch_with_matching_owner_repo_exits_2
    names for the identity-resolution check) must not collide under one
    key. Sibling coverage to TestPrCostCrossRepoLedgerIsolation above, which
    only varies `repo` on a single host."""

    def _existing_row_and_merged_prs(self, existing_host: str) -> tuple[dict, list[dict]]:
        existing_row = _sample_pr_cost_row(host=existing_host, repo="owner/repo", pr_number=42, machine="ci1")
        merged_prs = [{
            "number": 42, "headRefName": "ghost-branch", "additions": 1, "deletions": 1,
            "changedFiles": 1, "mergedAt": "2026-01-01T00:00:00Z",
        }]
        return existing_row, merged_prs

    def test_second_hosts_pr_is_recorded_not_skipped_as_already_captured(
        self, fake_projects, tmp_path, monkeypatch,
    ):
        _enable_pr_cost(tmp_path)
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        existing_row, merged_prs = self._existing_row_and_merged_prs("acme-corp.ghe.com")
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [existing_row])
        monkeypatch.setattr(
            subprocess, "run",
            _fake_pr_cost_subprocess_run(repo="owner/repo", host="github.com", merged_prs=merged_prs),
        )

        args = _pr_cost_args(record=True, pr=42)
        _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])

        rows = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())
        assert len(rows) == 2
        by_host = {r["host"]: r for r in rows}
        assert set(by_host) == {"acme-corp.ghe.com", "github.com"}

    def test_force_does_not_supersede_the_other_hosts_row(self, fake_projects, tmp_path, monkeypatch):
        """--force with an owner/repo match on a different host must still
        record a fresh row, not a correction: _latest_pr_cost_row correctly
        returns None across hosts, so there is nothing for `supersedes` to
        reference."""
        _enable_pr_cost(tmp_path)
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        existing_row, merged_prs = self._existing_row_and_merged_prs("acme-corp.ghe.com")
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [existing_row])
        monkeypatch.setattr(
            subprocess, "run",
            _fake_pr_cost_subprocess_run(repo="owner/repo", host="github.com", merged_prs=merged_prs),
        )

        args = _pr_cost_args(record=True, pr=42, force=True)
        _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])

        rows = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())
        assert len(rows) == 2
        by_host = {r["host"]: r for r in rows}
        assert by_host["github.com"]["supersedes"] == ""
        assert by_host["acme-corp.ghe.com"]["supersedes"] == ""

    def test_uncaptured_gap_listing_still_lists_second_hosts_pr(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        """Read mode's gap listing (_print_pr_cost_uncaptured) keys its own
        already-captured check by host too -- a row captured on one host
        must not hide the same-numbered PR as already captured on another."""
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        distinctive_branch = "feature-cross-host"
        existing_row = _sample_pr_cost_row(host="acme-corp.ghe.com", repo="owner/repo", pr_number=99, machine="ci1")
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [existing_row])
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch=distinctive_branch),
        ])
        merged_prs = [{
            "number": 99, "headRefName": distinctive_branch, "additions": 1, "deletions": 1,
            "changedFiles": 1, "mergedAt": "2026-01-01T00:00:00Z",
        }]
        monkeypatch.setattr(
            subprocess, "run",
            _fake_pr_cost_subprocess_run(repo="owner/repo", host="github.com", merged_prs=merged_prs),
        )

        _mod.pr_cost._pr_cost_report(_pr_cost_args(), datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])

        out = capsys.readouterr().out
        assert "PR #99" in out
        assert "(none)" not in out


class TestPrCostMultiRootRefusalRedaction:
    """The multi-root refusal (see local-mechanics' own
    test_more_than_one_resolved_root_refuses_with_exit_2 for its path-
    redaction coverage) fires before any repo/branch identity is resolved --
    confirmed here by proving no git/gh call happens at all, so there is
    structurally nothing repo/branch-shaped left for the message to leak."""

    def test_refusal_fires_before_any_git_or_gh_call(self, fake_projects, tmp_path, monkeypatch):
        other_root = tmp_path / "other-account" / "projects"
        other_root.mkdir(parents=True)

        def fail_on_any_call(cmd, *a, **kw):
            raise AssertionError(f"unexpected subprocess call before the multi-root refusal: {cmd}")

        monkeypatch.setattr(subprocess, "run", fail_on_any_call)

        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost._pr_cost_report(
                _pr_cost_args(), datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent, other_root],
            )
        assert exc_info.value.code == 2


class TestPrCostReadModeRedaction:
    """`_pr_cost_report` in read mode (record=False) -- every per-row print
    in _print_pr_cost_ledger_rows (existing captured rows) and
    _print_pr_cost_uncaptured (merged PRs not yet captured) routes through
    _assign_root_scoped_redact_label; no raw branch name reaches stdout/
    stderr. The --record loop's own per-branch "resolving..." progress line
    is covered by TestPrCostUnforcedReRecordRefusalRedaction below."""

    def test_no_raw_branch_name_in_existing_rows_table_or_uncaptured_listing(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        distinctive_branch = "acme-corp-secret-initiative-branch"
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [_sample_pr_cost_row(pr_number=7)])
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch=distinctive_branch),
        ])
        merged_prs = [{
            "number": 99, "headRefName": distinctive_branch, "additions": 1, "deletions": 1,
            "changedFiles": 1, "mergedAt": "2026-01-01T00:00:00Z",
        }]
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(repo="owner/repo", merged_prs=merged_prs))

        _mod.pr_cost._pr_cost_report(_pr_cost_args(), datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])

        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert distinctive_branch not in combined
        assert "account-1/branch-1" in combined


class TestPrCostUnforcedReRecordRefusalRedaction:
    def test_no_raw_branch_name_in_already_captured_refusal(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        """Also covers the --record loop's per-branch "resolving..."
        progress line, printed just before this refusal fires."""
        distinctive_branch = "acme-corp-secret-initiative-branch"
        _enable_pr_cost(tmp_path)  # seeds machine-id with "c0ffee01"
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        _mod.pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, [_sample_pr_cost_row(pr_number=42, machine="c0ffee01")])
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch=distinctive_branch),
        ])
        merged_prs = [{
            "number": 42, "headRefName": distinctive_branch, "additions": 1, "deletions": 1,
            "changedFiles": 1, "mergedAt": "2026-01-01T00:00:00Z",
        }]
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(repo="owner/repo", merged_prs=merged_prs))

        args = _pr_cost_args(record=True, pr=42)
        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])
        assert exc_info.value.code == 1

        err = capsys.readouterr().err
        assert distinctive_branch not in err
        assert "already" in err
        assert "account-1/branch-1" in err


class TestPrCostAuthPreflightFailureAbortRedaction:
    def test_gh_auth_status_failure_aborts_before_any_identity_resolution(
        self, fake_projects, monkeypatch, capsys,
    ):
        """An auth preflight failure aborts before _resolve_pinned_gh_repo is
        ever called, confirmed here by asserting no `gh repo view` call is in
        the captured call log -- there is nothing repo/branch-shaped yet to
        leak."""
        call_log: list[list[str]] = []
        monkeypatch.setattr(
            subprocess, "run",
            _fake_pr_cost_subprocess_run(gh_auth_status_failure=True, call_log=call_log),
        )

        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost._pr_cost_report(_pr_cost_args(), datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])
        assert exc_info.value.code == 1

        err = capsys.readouterr().err
        assert "gh auth login" in err
        assert not any(c[:3] == ["gh", "repo", "view"] for c in call_log)


class TestPrCostAllAccounts:
    """--all-accounts: lifts the multi-root refusal and loops the full
    report (local corpus scan, ledger read/print, and -- under --record --
    ledger write) once per resolved account, with each account's own
    ~/.claude/.pr-cost-enabled sentinel still individually gating whether
    that account's row is durably written. gh auth/identity resolution and
    merged-PR discovery are resolved once for the whole run, shared across
    every account's iteration below.

    Every --record test here uses per-account config dirs, not the file's
    usual PR_COST_LEDGER_PATH monkeypatch idiom -- that path is refused
    outright once --all-accounts sees more than one root (see
    TestPrCostAllAccountsForcedLedgerPathRefusal below).
    """

    def test_read_mode_across_two_accounts_keeps_branch_and_repo_labels_distinct(
        self, tmp_path, monkeypatch, capsys,
    ):
        roots = _two_declared_roots(tmp_path, monkeypatch)
        acct_a, acct_b = roots[0].parent, roots[1].parent
        for root in roots:
            proj = root / "-home-user-testrepo"
            proj.mkdir(parents=True)
            _write_jsonl(proj / "sess.jsonl", [
                _priced("claude-sonnet-5", input=1_000_000, branch="feature-a"),
            ])
        for account_config_dir in (acct_a, acct_b):
            _mod.pr_cost_ledger._write_pr_cost_ledger_file(
                account_config_dir / "pr-cost-ledger.tsv",
                [_sample_pr_cost_row(repo="owner/repo", pr_number=1, machine="ci1")],
            )
        merged_prs = [{
            "number": 99, "headRefName": "feature-a", "additions": 1, "deletions": 1,
            "changedFiles": 1, "mergedAt": "2026-01-01T00:00:00Z",
        }]
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(merged_prs=merged_prs))

        _mod.pr_cost._pr_cost_report(_pr_cost_args(all_accounts=True), datetime(2026, 8, 10, tzinfo=UTC), roots)

        out = capsys.readouterr().out
        assert "account-1/repo-1" in out
        assert "account-2/repo-1" in out
        assert "account-1/branch-1" in out
        assert "account-2/branch-1" in out

    def test_record_with_mixed_opted_in_and_not_opted_in_accounts(
        self, tmp_path, monkeypatch, capsys,
    ):
        roots = _two_declared_roots(tmp_path, monkeypatch)
        acct_a, acct_b = roots[0].parent, roots[1].parent
        (acct_a / ".pr-cost-enabled").touch()  # acct_b deliberately left without a sentinel
        proj_a = roots[0] / "-home-user-testrepo"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch="feature-a"),
        ])
        merged_prs = [{
            "number": 1, "headRefName": "feature-a", "additions": 1, "deletions": 1,
            "changedFiles": 1, "mergedAt": "2026-01-01T00:00:00Z",
        }]
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(merged_prs=merged_prs))

        args = _pr_cost_args(record=True, all_accounts=True)
        _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), roots)

        rows_a = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text((acct_a / "pr-cost-ledger.tsv").read_text())
        assert len(rows_a) == 1
        assert not (acct_b / "pr-cost-ledger.tsv").exists()

        captured = capsys.readouterr()
        assert "account-2 is not opted in (pr_cost_recording)" in captured.err
        assert "recorded 1 of 2 declared accounts (1 not opted in, 0 skipped)" in captured.out

    def test_record_with_zero_sentinels_present_records_nothing_and_exits_cleanly(
        self, tmp_path, monkeypatch, capsys,
    ):
        roots = _two_declared_roots(tmp_path, monkeypatch)
        acct_a, acct_b = roots[0].parent, roots[1].parent
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())

        args = _pr_cost_args(record=True, all_accounts=True)
        _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), roots)  # must not raise SystemExit

        assert not (acct_a / "pr-cost-ledger.tsv").exists()
        assert not (acct_b / "pr-cost-ledger.tsv").exists()
        out = capsys.readouterr().out
        assert "recorded 0 of 2 declared accounts (2 not opted in, 0 skipped)" in out

    def test_recorded_counter_counts_accounts_not_rows(self, tmp_path, monkeypatch, capsys):
        """One account writing two rows in one run (two branches) must still
        count as 1 toward `recorded`, not 2 -- the summary line's own
        denominator is "declared accounts", not "rows written"."""
        roots = _two_declared_roots(tmp_path, monkeypatch)
        acct_a = roots[0].parent
        (acct_a / ".pr-cost-enabled").touch()  # acct_b deliberately left without a sentinel
        proj_a = roots[0] / "-home-user-testrepo"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch="feature-a"),
            _priced("claude-sonnet-5", input=1_000_000, branch="feature-b"),
        ])
        merged_prs = [
            {"number": 1, "headRefName": "feature-a", "additions": 1, "deletions": 1,
             "changedFiles": 1, "mergedAt": "2026-01-01T00:00:00Z"},
            {"number": 2, "headRefName": "feature-b", "additions": 1, "deletions": 1,
             "changedFiles": 1, "mergedAt": "2026-01-01T00:00:00Z"},
        ]
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(merged_prs=merged_prs))

        args = _pr_cost_args(record=True, all_accounts=True)
        _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), roots)

        rows_a = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text((acct_a / "pr-cost-ledger.tsv").read_text())
        assert len(rows_a) == 2
        out = capsys.readouterr().out
        assert "recorded 1 of 2 declared accounts (1 not opted in, 0 skipped)" in out

    def test_full_sweep_account_with_no_matchable_branch_counts_as_skipped_not_omitted(
        self, tmp_path, monkeypatch, capsys,
    ):
        """Full-sweep --record --all-accounts (no --pr): an opted-in account
        whose only local branch matches no merged PR must still land in one
        of the three summary counters, not vanish from all of them -- the
        per-branch "no merged PR found" skip has no --pr target to attach a
        per-branch skipped_other increment to, so the account-level count
        must come from account_recorded_a_row staying False after the
        branch loop, not from a branch-loop increment."""
        roots = _two_declared_roots(tmp_path, monkeypatch)
        acct_a, acct_b = roots[0].parent, roots[1].parent
        (acct_a / ".pr-cost-enabled").touch()  # acct_b deliberately left without a sentinel
        proj_a = roots[0] / "-home-user-testrepo"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch="feature-a"),
        ])
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())  # no merged PRs at all

        args = _pr_cost_args(record=True, all_accounts=True)
        _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), roots)

        assert not (acct_a / "pr-cost-ledger.tsv").exists()
        assert not (acct_b / "pr-cost-ledger.tsv").exists()
        out = capsys.readouterr().out
        assert "recorded 0 of 2 declared accounts (1 not opted in, 1 skipped)" in out

    def test_targeted_pr_branch_absent_from_one_accounts_corpus_is_skipped_not_zero_recorded(
        self, tmp_path, monkeypatch, capsys,
    ):
        """See _pr_cost_report's branch-not-in-corpus skip comment for why --
        distinct from the single-account --pr N contract
        (test_target_pr_with_zero_branch_records_uses_zero_valued_agg_default),
        which keeps writing the zero-valued row when --all-accounts is absent."""
        roots = _two_declared_roots(tmp_path, monkeypatch)
        acct_a, acct_b = roots[0].parent, roots[1].parent
        (acct_a / ".pr-cost-enabled").touch()
        (acct_b / ".pr-cost-enabled").touch()
        proj_a = roots[0] / "-home-user-testrepo"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch="feature-a"),
        ])
        (roots[1] / "-home-user-testrepo").mkdir(parents=True)  # acct_b: no local activity at all
        merged_prs = [{
            "number": 5, "headRefName": "feature-a", "additions": 1, "deletions": 1,
            "changedFiles": 1, "mergedAt": "2026-01-01T00:00:00Z",
        }]
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(merged_prs=merged_prs))

        args = _pr_cost_args(record=True, pr=5, all_accounts=True)
        _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), roots)

        rows_a = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text((acct_a / "pr-cost-ledger.tsv").read_text())
        assert len(rows_a) == 1
        assert rows_a[0]["turn_count"] > 0
        assert not (acct_b / "pr-cost-ledger.tsv").exists()

        captured = capsys.readouterr()
        assert "account-2 has no local corpus activity for this branch" in captured.err
        assert "recorded 1 of 2 declared accounts (0 not opted in, 1 skipped)" in captured.out

    def test_all_accounts_renamed_branch_mismatch_hits_the_skip_not_the_new_warning(
        self, tmp_path, monkeypatch, capsys,
    ):
        """Distinct from the single-account --pr N mismatch
        (test_renamed_branch_mismatch_warns_but_still_writes_zero_valued_row):
        under --all-accounts, an account whose branch_totals is non-empty but
        missing the PR's branch hits the pre-existing branch-not-in-corpus
        skip (this test's sibling above) before the new mismatch-warning
        print is ever reached -- the skip's own `continue` fires first."""
        roots = _two_declared_roots(tmp_path, monkeypatch)
        acct_a, acct_b = roots[0].parent, roots[1].parent
        (acct_a / ".pr-cost-enabled").touch()
        (acct_b / ".pr-cost-enabled").touch()
        proj_a = roots[0] / "-home-user-testrepo"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch="new-name"),
        ])
        proj_b = roots[1] / "-home-user-testrepo"
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_b / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch="old-name"),  # renamed away from the PR's branch
        ])
        merged_prs = [{
            "number": 5, "headRefName": "new-name", "additions": 1, "deletions": 1,
            "changedFiles": 1, "mergedAt": "2026-01-01T00:00:00Z",
        }]
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(merged_prs=merged_prs))

        args = _pr_cost_args(record=True, pr=5, all_accounts=True)
        _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), roots)

        rows_a = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text((acct_a / "pr-cost-ledger.tsv").read_text())
        assert len(rows_a) == 1
        assert rows_a[0]["turn_count"] > 0
        assert not (acct_b / "pr-cost-ledger.tsv").exists()  # skipped, not zero-recorded
        err = capsys.readouterr().err
        assert "account-2 has no local corpus activity for this branch" in err  # the pre-existing skip fires
        assert "has no matching local corpus activity" not in err  # the new warning must not also fire
        assert "old-name" not in err
        assert "new-name" not in err

    def test_targeted_pr_already_captured_converts_to_per_account_skip_not_hard_abort(
        self, tmp_path, monkeypatch, capsys,
    ):
        """A second, unforced --record --pr N --all-accounts call against an
        already-captured (repo, pr_number, machine) is a per-account skip,
        not the whole run hard-aborting with sys.exit(1) the way plain
        single-account --pr N does at the same guard."""
        roots = _two_declared_roots(tmp_path, monkeypatch)
        acct_a = roots[0].parent
        (acct_a / ".pr-cost-enabled").touch()  # acct_b deliberately left without a sentinel
        proj_a = roots[0] / "-home-user-testrepo"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch="feature-a"),
        ])
        merged_prs = [{
            "number": 7, "headRefName": "feature-a", "additions": 1, "deletions": 1,
            "changedFiles": 1, "mergedAt": "2026-01-01T00:00:00Z",
        }]
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(merged_prs=merged_prs))
        args = _pr_cost_args(record=True, pr=7, all_accounts=True)

        _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), roots)  # first call: captures the row
        rows_after_first = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text((acct_a / "pr-cost-ledger.tsv").read_text())
        assert len(rows_after_first) == 1
        capsys.readouterr()  # discard first call's output

        _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), roots)  # second call: must not raise

        rows_after_second = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text((acct_a / "pr-cost-ledger.tsv").read_text())
        assert len(rows_after_second) == 1  # no correcting row appended without --force
        captured = capsys.readouterr()
        assert "is already captured" in captured.err
        assert "recorded 0 of 2 declared accounts (1 not opted in, 1 skipped)" in captured.out

    def test_targeted_pr_merged_inside_asof_window_converts_to_per_account_skip_not_hard_abort(
        self, tmp_path, monkeypatch, capsys,
    ):
        """A --pr N target merged too recently is a per-account skip under
        --all-accounts, not the whole run hard-aborting with sys.exit(1) the
        way plain single-account --pr N does at the same guard -- distinct
        from the "already captured" and "branch not in corpus" conditions
        covered by the two tests above."""
        roots = _two_declared_roots(tmp_path, monkeypatch)
        acct_a = roots[0].parent
        (acct_a / ".pr-cost-enabled").touch()  # acct_b deliberately left without a sentinel
        proj_a = roots[0] / "-home-user-testrepo"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch="feature-a"),
        ])
        merged_prs = [{
            "number": 9, "headRefName": "feature-a", "additions": 1, "deletions": 1,
            "changedFiles": 1, "mergedAt": "2026-08-09T00:00:00Z",
        }]
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(merged_prs=merged_prs))

        args = _pr_cost_args(record=True, pr=9, all_accounts=True)
        _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), roots)  # must not raise SystemExit

        assert not (acct_a / "pr-cost-ledger.tsv").exists()
        captured = capsys.readouterr()
        assert "merged too recently" in captured.err
        assert "skipped" in captured.err
        assert "recorded 0 of 2 declared accounts (1 not opted in, 1 skipped)" in captured.out

    def test_all_accounts_on_a_single_declared_account_machine_is_a_no_op(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        """--all-accounts against a machine with only one resolved root
        produces identical read-mode output to a plain call, and never
        triggers the multi-root refusal."""
        merged_prs = [{
            "number": 1, "headRefName": "feature-a", "additions": 1, "deletions": 1,
            "changedFiles": 1, "mergedAt": "2026-01-01T00:00:00Z",
        }]
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(merged_prs=merged_prs))

        _mod.pr_cost._pr_cost_report(_pr_cost_args(), datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent])
        plain_output = capsys.readouterr()

        _mod.pr_cost._pr_cost_report(
            _pr_cost_args(all_accounts=True), datetime(2026, 8, 10, tzinfo=UTC), [fake_projects.parent],
        )
        all_accounts_output = capsys.readouterr()

        assert plain_output.out == all_accounts_output.out
        assert plain_output.err == all_accounts_output.err

    def test_account_ordinal_is_resolved_path_sorted_not_scan_order(self, tmp_path, monkeypatch, capsys):
        """account-N is assigned by resolved-path sort (_redaction_ordinals),
        not by --config-dir argument order or scan order -- mirrors
        subagent-mix's own regression test of the same name."""
        monkeypatch.setattr(_mod.scope, "declared_transcript_roots", lambda: [])
        active = tmp_path / "zzz-active"
        active_proj = active / "projects" / "-home-user-active-repo"
        active_proj.mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: active)
        _write_jsonl(active_proj / "sess-active.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch="active-branch"),
        ])

        extra = tmp_path / "aaa-extra"
        extra_proj = extra / "projects" / "-home-user-extra-repo"
        extra_proj.mkdir(parents=True)
        _write_jsonl(extra_proj / "sess-extra.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch="extra-branch"),
        ])

        merged_prs = [
            {"number": 1, "headRefName": "extra-branch", "additions": 1, "deletions": 1,
             "changedFiles": 1, "mergedAt": "2026-01-01T00:00:00Z"},
            {"number": 2, "headRefName": "active-branch", "additions": 1, "deletions": 1,
             "changedFiles": 1, "mergedAt": "2026-01-01T00:00:00Z"},
        ]
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(merged_prs=merged_prs))

        _mod.pr_cost.cmd_pr_cost(_pr_cost_args(extra_config_dirs=[str(extra)], all_accounts=True))

        out = capsys.readouterr().out
        pr1_line = next(line for line in out.splitlines() if "PR #1" in line)
        pr2_line = next(line for line in out.splitlines() if "PR #2" in line)
        # "aaa-extra" (PR #1's branch) resolved-path-sorts before "zzz-active"
        # (PR #2's branch) despite being scanned second -- account-1 must be
        # the extra root's row.
        assert "account-1/branch-1" in pr1_line
        assert "account-2/branch-1" in pr2_line

    def test_symlinked_sentinel_opts_both_accounts_in_together(self, tmp_path, monkeypatch, capsys):
        """Pins docs/pr-cost.md's documented caveat: the sentinel check is a
        plain Path.exists(), which follows symlinks -- an account whose
        .pr-cost-enabled is a symlink to another account's real sentinel is
        opted in too, with no separate consent of its own."""
        roots = _two_declared_roots(tmp_path, monkeypatch)
        acct_a, acct_b = roots[0].parent, roots[1].parent
        (acct_a / ".pr-cost-enabled").touch()
        os.symlink(acct_a / ".pr-cost-enabled", acct_b / ".pr-cost-enabled")
        for root in roots:
            proj = root / "-home-user-testrepo"
            proj.mkdir(parents=True)
            _write_jsonl(proj / "sess.jsonl", [
                _priced("claude-sonnet-5", input=1_000_000, branch="feature-a"),
            ])
        merged_prs = [{
            "number": 1, "headRefName": "feature-a", "additions": 1, "deletions": 1,
            "changedFiles": 1, "mergedAt": "2026-01-01T00:00:00Z",
        }]
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(merged_prs=merged_prs))

        args = _pr_cost_args(record=True, all_accounts=True)
        _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), roots)

        assert (acct_a / "pr-cost-ledger.tsv").exists()
        assert (acct_b / "pr-cost-ledger.tsv").exists()
        out = capsys.readouterr().out
        assert "recorded 2 of 2 declared accounts (0 not opted in, 0 skipped)" in out

    def test_two_opted_in_accounts_record_distinct_machine_identities(
        self, tmp_path, monkeypatch, capsys,
    ):
        """Closes a wiring-regression gap: a bug that hoists
        account_config_dir incorrectly in the per-account loop (e.g. always
        resolving identity through the first account's config dir) would
        collapse both accounts' ledger `machine` values onto one shared
        identity, with no existing test catching it."""
        roots = _two_declared_roots(tmp_path, monkeypatch)
        acct_a, acct_b = roots[0].parent, roots[1].parent
        (acct_a / ".pr-cost-enabled").touch()
        (acct_b / ".pr-cost-enabled").touch()
        for root in roots:
            proj = root / "-home-user-testrepo"
            proj.mkdir(parents=True)
            _write_jsonl(proj / "sess.jsonl", [
                _priced("claude-sonnet-5", input=1_000_000, branch="feature-a"),
            ])
        merged_prs = [{
            "number": 1, "headRefName": "feature-a", "additions": 1, "deletions": 1,
            "changedFiles": 1, "mergedAt": "2026-01-01T00:00:00Z",
        }]
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(merged_prs=merged_prs))

        args = _pr_cost_args(record=True, all_accounts=True)
        _mod.pr_cost._pr_cost_report(args, datetime(2026, 8, 10, tzinfo=UTC), roots)

        rows_a = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text((acct_a / "pr-cost-ledger.tsv").read_text())
        rows_b = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text((acct_b / "pr-cost-ledger.tsv").read_text())
        machine_a, machine_b = rows_a[0]["machine"], rows_b[0]["machine"]
        assert _mod.ledger_common._MACHINE_IDENTITY_RE.match(machine_a)
        assert _mod.ledger_common._MACHINE_IDENTITY_RE.match(machine_b)
        assert machine_a != machine_b


class TestPrCostAllAccountsForcedLedgerPathRefusal:
    def test_all_accounts_with_forced_ledger_path_and_multi_root_refuses_before_any_git_or_gh_call(
        self, tmp_path, monkeypatch, capsys,
    ):
        roots = _two_declared_roots(tmp_path, monkeypatch)
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(tmp_path / "shared-ledger.tsv"))

        def fail_on_any_call(cmd, *a, **kw):
            raise AssertionError(f"unexpected subprocess call before the PR_COST_LEDGER_PATH refusal: {cmd}")

        monkeypatch.setattr(subprocess, "run", fail_on_any_call)

        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost._pr_cost_report(_pr_cost_args(all_accounts=True), datetime(2026, 8, 10, tzinfo=UTC), roots)

        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert str(roots[0]) not in err
        assert str(roots[1]) not in err


class TestCmdPrCostEndToEndViaRealArgparse:
    def test_all_accounts_flag_with_machine_label_and_record_refused_through_the_real_parser(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        """Exercises pr-cost through the real argparse CLI (build_parser()),
        not the _pr_cost_args() test-helper shortcut every other pr-cost
        test uses."""
        _enable_pr_cost(tmp_path)
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch="feature-a"),
        ])
        merged_prs = [{
            "number": 1, "headRefName": "feature-a", "additions": 1, "deletions": 1,
            "changedFiles": 1, "mergedAt": "2026-01-01T00:00:00Z",
        }]
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(merged_prs=merged_prs))

        parser = _mod.build_parser()
        args = parser.parse_args(["pr-cost", "--all-accounts", "--record", "--machine-label", "ci1"])
        assert args.all_accounts is True
        assert args.func == _mod.pr_cost.cmd_pr_cost

        # Parsing succeeds; the refusal is body-level, not an argparse-level
        # constraint. cmd_pr_cost itself still refuses this namespace, since
        # --machine-label is no longer accepted with --record.
        with pytest.raises(SystemExit) as exc_info:
            _mod.pr_cost.cmd_pr_cost(args)
        assert exc_info.value.code == 1

    def test_all_accounts_and_record_with_no_machine_label_drives_cmd_pr_cost_through_the_real_parser(
        self, fake_projects, tmp_path, monkeypatch, capsys,
    ):
        """The genuine --record success path through the real argparse CLI,
        now that --machine-label is refused there and machine identity is
        generated instead."""
        _enable_pr_cost(tmp_path)
        ledger_path = tmp_path / "pr-cost-ledger.tsv"
        monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch="feature-a"),
        ])
        merged_prs = [{
            "number": 1, "headRefName": "feature-a", "additions": 1, "deletions": 1,
            "changedFiles": 1, "mergedAt": "2026-01-01T00:00:00Z",
        }]
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run(merged_prs=merged_prs))

        parser = _mod.build_parser()
        args = parser.parse_args(["pr-cost", "--all-accounts", "--record"])
        assert args.all_accounts is True
        assert args.func == _mod.pr_cost.cmd_pr_cost

        _mod.pr_cost.cmd_pr_cost(args)

        rows = _mod.pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())
        assert len(rows) == 1
        out = capsys.readouterr().out
        assert "recorded 1 of 1 declared accounts (0 not opted in, 0 skipped)" in out
