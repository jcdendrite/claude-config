"""Test helpers shared by the pr-cost family's test files (test_transcript_pr_cost*.py,
test_transcript_gh_cli.py, test_transcript_ledger_common.py)."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from transcript_analysis import ledger_common, pr_cost_export, pr_cost_ledger

# Frozen ledger header lines, oldest first, hand-written: the single test-side source for every older header.
# Never derive an entry from the live column tuple. When a column is added, append the outgoing header
# line to this tuple.
_PRE_HOST_HEADER_LINE = "\t".join((
    "repo", "pr_number", "machine",
    "head_branch", "merged_at", "rate_stamp", "captured_at", "join_confidence", "supersedes", "status",
    "cache_read_usd", "cache_write_5m_usd", "cache_write_1h_usd", "output_usd", "input_usd",
    "cache_read_tokens", "cache_write_5m_tokens", "cache_write_1h_tokens", "output_tokens", "input_tokens",
    "unpriced_turns", "unpriced_tokens", "turn_count", "session_count",
    "opus_dollars", "opus_dollar_share_pct", "sum_context_at_turn", "mean_context_at_turn",
    "additions", "deletions", "changed_files", "commit_count", "review_comment_count",
    "distinct_top_level_dirs", "distinct_file_extensions", "tests_changed", "plan_file_added", "risk_surface_flag",
))
_PRE_MODEL_HEADER_LINE = "\t".join((
    "host", "repo", "pr_number", "machine",
    "head_branch", "merged_at", "rate_stamp", "captured_at", "join_confidence", "supersedes", "status",
    "cache_read_usd", "cache_write_5m_usd", "cache_write_1h_usd", "output_usd", "input_usd",
    "cache_read_tokens", "cache_write_5m_tokens", "cache_write_1h_tokens", "output_tokens", "input_tokens",
    "unpriced_turns", "unpriced_tokens", "turn_count", "session_count",
    "opus_dollars", "opus_dollar_share_pct", "sum_context_at_turn", "mean_context_at_turn",
    "additions", "deletions", "changed_files", "commit_count", "review_comment_count",
    "distinct_top_level_dirs", "distinct_file_extensions", "tests_changed", "plan_file_added", "risk_surface_flag",
))
_FROZEN_PR_COST_HEADER_LINES: tuple[str, ...] = (_PRE_HOST_HEADER_LINE, _PRE_MODEL_HEADER_LINE)

# Hand-written data rows under each frozen header. Cells are pairwise distinct within a row and from the other row's,
# except the three bool cells, which can take only two values.
_PRE_HOST_ROW_LINE = "\t".join((
    "legacy-org/legacy-repo", "91", "ab12cd34",
    "account-1/branch-9", "2025-12-01T00:00:00Z", "2026-07-01", "2025-12-05T00:00:00Z", "medium",
    "2025-12-02T00:00:00Z", "degraded_network",
    "0.100000", "0.200000", "0.300000", "0.400000", "0.500000",
    "11", "22", "33", "44", "55",
    "6", "7", "8", "3",
    "0.123457", "12.500000", "9000", "1125.000000",
    "71", "72", "73", "74", "75",
    "4", "5", "false", "true", "true",
))
_PRE_MODEL_ROW_LINE = "\t".join((
    "ghe.example.com", "other-org/other-repo", "21", "ef56ab78",
    "account-2/branch-3", "2026-02-01T00:00:00Z", "2026-08-02", "2026-02-09T00:00:00Z", "low",
    "", "degraded_rate_limit",
    "1.100000", "1.200000", "1.300000", "1.400000", "1.500000",
    "101", "202", "303", "404", "505",
    "16", "17", "18", "13",
    "1.234568", "22.500000", "19000", "2125.000000",
    "81", "82", "83", "84", "85",
    "14", "15", "true", "false", "false",
))

# Each row line above as the typed row the parser must return, written out by column name and not derived from any tuple.
_PRE_HOST_ROW_PARSED = {
    "host": "github.com", "repo": "legacy-org/legacy-repo", "pr_number": 91, "machine": "ab12cd34",
    "head_branch": "account-1/branch-9", "merged_at": "2025-12-01T00:00:00Z", "rate_stamp": "2026-07-01",
    "captured_at": "2025-12-05T00:00:00Z", "join_confidence": "medium", "supersedes": "2025-12-02T00:00:00Z",
    "status": "degraded_network",
    "cache_read_usd": 0.1, "cache_write_5m_usd": 0.2, "cache_write_1h_usd": 0.3, "output_usd": 0.4, "input_usd": 0.5,
    "cache_read_tokens": 11, "cache_write_5m_tokens": 22, "cache_write_1h_tokens": 33, "output_tokens": 44,
    "input_tokens": 55,
    "unpriced_turns": 6, "unpriced_tokens": 7, "turn_count": 8, "session_count": 3,
    "opus_dollars": 0.123457, "opus_dollar_share_pct": 12.5, "sum_context_at_turn": 9000, "mean_context_at_turn": 1125.0,
    "additions": 71, "deletions": 72, "changed_files": 73, "commit_count": 74, "review_comment_count": 75,
    "distinct_top_level_dirs": 4, "distinct_file_extensions": 5,
    "tests_changed": False, "plan_file_added": True, "risk_surface_flag": True,
    "model_breakdown": None,
}
_PRE_MODEL_ROW_PARSED = {
    "host": "ghe.example.com", "repo": "other-org/other-repo", "pr_number": 21, "machine": "ef56ab78",
    "head_branch": "account-2/branch-3", "merged_at": "2026-02-01T00:00:00Z", "rate_stamp": "2026-08-02",
    "captured_at": "2026-02-09T00:00:00Z", "join_confidence": "low", "supersedes": "",
    "status": "degraded_rate_limit",
    "cache_read_usd": 1.1, "cache_write_5m_usd": 1.2, "cache_write_1h_usd": 1.3, "output_usd": 1.4, "input_usd": 1.5,
    "cache_read_tokens": 101, "cache_write_5m_tokens": 202, "cache_write_1h_tokens": 303, "output_tokens": 404,
    "input_tokens": 505,
    "unpriced_turns": 16, "unpriced_tokens": 17, "turn_count": 18, "session_count": 13,
    "opus_dollars": 1.234568, "opus_dollar_share_pct": 22.5, "sum_context_at_turn": 19000, "mean_context_at_turn": 2125.0,
    "additions": 81, "deletions": 82, "changed_files": 83, "commit_count": 84, "review_comment_count": 85,
    "distinct_top_level_dirs": 14, "distinct_file_extensions": 15,
    "tests_changed": True, "plan_file_added": False, "risk_surface_flag": False,
    "model_breakdown": None,
}


_LEDGER_DIR_MARKER = "zzledgerdirmarkerzz"  # names the ledger's directory; must never reach captured output
_PRIOR_ROW_MARKER = "zzpriorrowmarkerzz"  # sits in a prior row's head_branch cell; must never reach captured output

# Stems of the run-output lines the positive tests pin in full. Each positive-line builder and each
# "not printed" check starts from the same stem, so rewording a message moves both.
_PER_PR_DEGRADE_STEM = "per-model breakdown failed"
_COUNT_LINE_STEM = "row(s) recorded without a per-model breakdown"
_UPGRADE_NOTICE_STEM = "pr-cost: upgraded "
_UPGRADE_NOTICE_FRAGMENT = (
    "to the current header -- older claude-config checkouts refuse this file until they are updated,"
    " and there is no supported downgrade; see docs/pr-cost.md in the claude-config repo"
)


def _upgrade_notice_line(ledger_label: str) -> str:
    """The stderr line announcing an upgraded ledger; ledger_label is "the ledger", or "account-N's ledger"
    under --all-accounts."""
    return f"{_UPGRADE_NOTICE_STEM}{ledger_label} {_UPGRADE_NOTICE_FRAGMENT}"


def _merged_pr(number: int, branch: str) -> dict:
    return {
        "number": number, "headRefName": branch, "additions": 1, "deletions": 1,
        "changedFiles": 1, "mergedAt": "2026-01-01T00:00:00Z",
    }


def _enable_pr_cost(config_dir: Path, identity: str = "c0ffee01") -> None:
    """Opt an account into pr-cost --record and seed its machine identity.
    pr-cost has no shared opt-in fixture the way cost-ledger's
    cost_ledger_enabled does, because every test here resolves its own
    config dir(s)."""
    (config_dir / ".pr-cost-enabled").touch()
    (config_dir / ledger_common._MACHINE_IDENTITY_FILENAME).write_text(identity)


def _make_mkstemp_create_0644(monkeypatch) -> None:
    """Make tempfile.mkstemp create files 0644, so a test of the ledger's 0600 creation mode
    fails if the writer relies on mkstemp's own default instead of setting the mode itself."""
    real_mkstemp = tempfile.mkstemp

    def mkstemp_creating_0644(*args, **kwargs):
        fd, temp_name = real_mkstemp(*args, **kwargs)
        os.fchmod(fd, 0o644)
        return fd, temp_name

    monkeypatch.setattr(tempfile, "mkstemp", mkstemp_creating_0644)


def _pr_cost_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    extra_config_dirs: list[str] | None = None,
    record: bool = False,
    pr: int | None = None,
    machine_label: str | None = None,
    force: bool = False,
    asof_window_days: float | None = None,
    plan_file_glob: str | None = None,
    risk_surface_globs: list[str] | None = None,
    all_accounts: bool = False,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "extra_config_dirs": extra_config_dirs,
        "record": record,
        "pr": pr,
        "machine_label": machine_label,
        "force": force,
        "asof_window_days": asof_window_days,
        "plan_file_glob": plan_file_glob,
        "risk_surface_globs": risk_surface_globs,
        "all_accounts": all_accounts,
    })()


def _pr_cost_export_args(*, out: str | None, extra_config_dirs: list[str] | None = None) -> object:
    return type("A", (), {
        "out": out,
        "extra_config_dirs": extra_config_dirs,
    })()


def _fake_pr_cost_subprocess_run(
    *,
    repo: str = "owner/repo",
    host: str = "github.com",
    merged_prs: list[dict] | None = None,
    enrichment_by_pr: dict[int, dict] | None = None,
    local_git_shas: set[str] | None = None,
    gh_repo_name_with_owner: str | None = None,
    gh_repo_view_host: str | None = None,
    gh_auth_status_failure: bool = False,
    gh_repo_view_failure_stderr: str | None = None,
    gh_pr_view_failure_stderr: str | None = None,
    call_log: list[list[str]] | None = None,
    enforce_repo_pin: str | None = None,
    git_tracked: bool = False,
):
    """Build a subprocess.run double covering every local git/gh call
    _pr_cost_report's full orchestration makes: origin-remote resolution
    (host defaults to github.com; pass a GHE hostname to simulate a
    GHE-pinned repo), the git-tracked-ledger check (answers "not a git
    repository" unless git_tracked=True, so --record never trips the
    git-tracked refusal against a tmp_path ledger by default), gh
    auth/repo-view/pr-list/pr-view, and cat-file --batch-check (answers from
    local_git_shas). Raises AssertionError on any other command shape, so an
    untested call path fails loud instead of silently returning "".

    gh-integration extension points (all default to the local-mechanics
    behavior above -- unset, every new param is a no-op):
    gh_repo_name_with_owner decouples `gh repo view`'s own nameWithOwner from
    the git-remote-derived `repo`, and gh_repo_view_host (defaults to `host`)
    decouples its own url's host the same way (_resolve_pinned_gh_repo's
    case-fold/mismatch scenarios); a
    *_failure_stderr param makes every call of that kind fail with the given
    stderr text instead of succeeding (rate-limit/network/auth-shaped
    classification, retry-exhaustion); call_log, when given, collects every
    matched command's argv; enforce_repo_pin, when given, raises
    AssertionError on any post-resolution `gh pr list`/`gh pr view` call
    whose argv doesn't carry `--repo <enforce_repo_pin>` contiguously;
    git_tracked, when True, makes the git-tracked-ledger check answer as if
    the ledger path sits inside a tracked git working tree.
    """
    merged_prs = merged_prs if merged_prs is not None else []
    enrichment_by_pr = enrichment_by_pr or {}
    local_git_shas = local_git_shas or set()
    gh_repo_name_with_owner = gh_repo_name_with_owner if gh_repo_name_with_owner is not None else repo
    gh_repo_view_host = gh_repo_view_host if gh_repo_view_host is not None else host

    def fake_run(cmd, *args, **kwargs):
        class _Proc:
            returncode = 0
            stdout = ""
            stderr = ""

        proc = _Proc()
        if cmd[:3] == ["git", "remote", "get-url"]:
            proc.stdout = f"https://{host}/{repo}.git\n"
        elif cmd[0] == "git" and "rev-parse" in cmd and "--is-inside-work-tree" in cmd:
            if git_tracked:
                proc.stdout = "true\n"
            else:
                proc.returncode = 128
                proc.stderr = "fatal: not a git repository (or any of the parent directories): .git\n"
        elif cmd[0] == "git" and len(cmd) > 1 and cmd[1] == "cat-file":
            queried = kwargs.get("input", "").split()
            found = [sha for sha in queried if sha in local_git_shas]
            proc.stdout = "".join(f"{sha} commit\n" for sha in found)
        elif cmd[:2] == ["gh", "auth"]:
            if gh_auth_status_failure:
                proc.returncode = 1
                proc.stderr = "error: not logged into any GitHub hosts\n"
            else:
                proc.stdout = ""
        elif cmd[:3] == ["gh", "repo", "view"]:
            if gh_repo_view_failure_stderr is not None:
                proc.returncode = 1
                proc.stderr = gh_repo_view_failure_stderr
            else:
                # Real `gh --json` returns only the fields the caller asked
                # for -- gating on the actual argv here (not hardcoding both
                # keys) catches a caller that drops a required field from
                # its own --json list, which a hardcoded payload would mask.
                if "--json" not in cmd:
                    raise AssertionError(f"gh repo view call missing --json: {cmd}")
                json_flag_index = cmd.index("--json")
                requested_fields = set(cmd[json_flag_index + 1].split(","))
                full_payload = {
                    "nameWithOwner": gh_repo_name_with_owner,
                    "url": f"https://{gh_repo_view_host}/{gh_repo_name_with_owner}",
                }
                proc.stdout = json.dumps({
                    key: value for key, value in full_payload.items() if key in requested_fields
                })
        elif cmd[:3] == ["gh", "pr", "list"]:
            if enforce_repo_pin is not None and not _argv_carries_repo_pin(cmd, enforce_repo_pin):
                raise AssertionError(f"gh pr list call missing --repo {enforce_repo_pin!r} pin: {cmd}")
            proc.stdout = json.dumps(merged_prs)
        elif cmd[:3] == ["gh", "pr", "view"]:
            if enforce_repo_pin is not None and not _argv_carries_repo_pin(cmd, enforce_repo_pin):
                raise AssertionError(f"gh pr view call missing --repo {enforce_repo_pin!r} pin: {cmd}")
            if gh_pr_view_failure_stderr is not None:
                proc.returncode = 1
                proc.stderr = gh_pr_view_failure_stderr
            else:
                pr_number = int(cmd[3])
                proc.stdout = json.dumps(enrichment_by_pr.get(pr_number, {}))
        else:
            raise AssertionError(f"unexpected subprocess.run call in pr-cost test: {cmd}")
        if call_log is not None:
            call_log.append(cmd)
        return proc

    return fake_run


def _argv_carries_repo_pin(cmd: list[str], pinned_repo: str) -> bool:
    """True when `--repo <pinned_repo>` appears contiguously in cmd, where
    the argv value may be the bare pin or a host-qualified `HOST/<pinned_repo>`
    form -- _fake_pr_cost_subprocess_run's enforce_repo_pin check."""
    return any(
        cmd[i] == "--repo" and (cmd[i + 1] == pinned_repo or cmd[i + 1].endswith(f"/{pinned_repo}"))
        for i in range(len(cmd) - 1)
    )


def _sample_pr_cost_row(**overrides) -> dict:
    """A complete, valid pr-cost ledger row dict covering every column and
    type (str, int, float, bool, JSON cell -- model_breakdown defaults to None, not recorded) -- the base
    fixture for round-trip, append, and malformed-content tests."""
    row: dict = {
        "host": "github.com", "repo": "owner/repo", "pr_number": 42, "machine": "ci1",
        "head_branch": "account-1/branch-1", "merged_at": "2026-01-01T00:00:00Z",
        "rate_stamp": "2026-08-02", "captured_at": "2026-01-02T00:00:00Z",
        "join_confidence": "high", "supersedes": "", "status": "ok",
        "cache_read_usd": 1.5, "cache_write_5m_usd": 0.25, "cache_write_1h_usd": 0.1,
        "output_usd": 2.0, "input_usd": 0.5,
        "cache_read_tokens": 1000, "cache_write_5m_tokens": 200, "cache_write_1h_tokens": 100,
        "output_tokens": 500, "input_tokens": 300,
        "unpriced_turns": 0, "unpriced_tokens": 0,
        "turn_count": 5, "session_count": 2,
        "opus_dollars": 0.0, "opus_dollar_share_pct": 0.0,
        "sum_context_at_turn": 1500, "mean_context_at_turn": 300.0,
        "additions": 42, "deletions": 10, "changed_files": 3, "commit_count": 4, "review_comment_count": 1,
        "distinct_top_level_dirs": 2, "distinct_file_extensions": 3,
        "tests_changed": True, "plan_file_added": True, "risk_surface_flag": False,
        "model_breakdown": None,
    }
    row.update(overrides)
    assert set(row) == set(pr_cost_ledger._PR_COST_LEDGER_COLUMNS), "sample row must cover every ledger column exactly"
    return row


def _legacy_row_line(**overrides) -> str:
    """Returns a _sample_pr_cost_row(**overrides) rendered under the frozen pre-host
    header's columns -- no host cell, no model_breakdown cell."""
    return pr_cost_ledger._format_pr_cost_ledger_row(
        _sample_pr_cost_row(**overrides), columns=_PRE_HOST_HEADER_LINE.split("\t"),
    )


def _pre_model_row_line(**overrides) -> str:
    """Returns a _sample_pr_cost_row(**overrides) rendered under the frozen pre-model
    header's columns -- no model_breakdown cell."""
    return pr_cost_ledger._format_pr_cost_ledger_row(
        _sample_pr_cost_row(**overrides), columns=_PRE_MODEL_HEADER_LINE.split("\t"),
    )


def _sample_model_breakdown(model: str = "claude-sonnet-5") -> dict:
    """A model_breakdown cell value for one model, one standard-variant group, consistent with
    _sample_pr_cost_row's per-class scalars. Written as a literal, not computed through
    pr_cost._usd_to_micros."""
    return {
        model: {
            "standard": {
                "cache_read": {"tokens": 1000, "usd_micros": 1_500_000},
                "cache_write_5m": {"tokens": 200, "usd_micros": 250_000},
                "cache_write_1h": {"tokens": 100, "usd_micros": 100_000},
                "output": {"tokens": 500, "usd_micros": 2_000_000},
                "input": {"tokens": 300, "usd_micros": 500_000},
            },
        },
    }


def _parse_pr_cost_export_provenance_line(line: str) -> dict:
    """Parses a pr-cost-export provenance line's key=value tokens, skipping
    its first three space-separated fields ("#", the subcommand name, and
    the DO-NOT-PUBLISH marker)."""
    return dict(t.split("=", 1) for t in line.split(" ")[3:])


def _parse_pr_cost_export_row(line: str) -> dict:
    """Parses one tab-separated pr-cost-export data row into a
    {column: cell} dict keyed by _PR_COST_EXPORT_COLUMNS."""
    return dict(zip(pr_cost_export._PR_COST_EXPORT_COLUMNS, line.split("\t"), strict=True))
