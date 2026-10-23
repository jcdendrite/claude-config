"""The pr-link command: cmd_pr_link, each named branch's GitHub PR number, main-thread model
split, and per-PR comment counts from gh, and its subparser registration.

Imports gh_cli, render, and scope by module (attribute access, not by name) -- see
scope.py's own top-of-file comment for why.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict

from transcript_analysis import gh_cli, render, scope


def _pr_link_gh_failure_kind(exc: Exception) -> str:
    """This module's own label for why a pr-link gh call failed -- never gh's
    raw stderr, which can echo the queried repo verbatim."""
    if isinstance(exc, FileNotFoundError):
        return "gh not found"
    if isinstance(exc, json.JSONDecodeError):
        return "unparseable gh output"
    if isinstance(exc, subprocess.TimeoutExpired):
        return "timeout"
    kind = gh_cli._classify_gh_error(getattr(exc, "stderr", None) or "")
    # _classify_gh_error falls through to "network" for any stderr it does
    # not recognize, a wrong repo slug included.
    return "network or unrecognized" if kind == gh_cli._GH_ERROR_KIND_NETWORK else kind


def _pr_link_report_gh_failure(branch: str, step: str, exc: Exception) -> None:
    print(f"pr-link: {step} failed for branch {branch} ({_pr_link_gh_failure_kind(exc)})", file=sys.stderr)


def cmd_pr_link(args: argparse.Namespace) -> None:
    if not getattr(args, "branches", None):
        print("--branches is required for pr-link", file=sys.stderr)
        sys.exit(1)

    branches: list[str] = [b.strip() for b in args.branches.split(",") if b.strip()]
    supplied_repo: str | None = getattr(args, "repo", None)
    if supplied_repo:
        repo = pr_list_repo = supplied_repo
        api_host_args: list[str] = []
    else:
        # `gh pr list --repo` takes the host-qualified slug directly; `gh api`
        # takes `--hostname` instead, since its path carries no host.
        # Either way, a GHE origin reaches the right host regardless of the
        # ambient GH_HOST.
        origin_host, repo = gh_cli._git_remote_origin_host_and_owner_repo(
            subcommand="pr-link", failure_hint="pass --repo OWNER/REPO",
        )
        pr_list_repo = gh_cli._gh_host_qualified_repo(origin_host, repo)
        api_host_args = ["--hostname", origin_host]
    author: str = getattr(args, "author", None) or ""
    roots = scope.resolve_scan_roots(args)
    session_iter, scope_label = scope._resolve_project_scope(args, "pr-link", roots=roots)

    branch_models: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for _jsonl, records in session_iter:
        branch_context = scope._session_branch_context(records)
        for rec in records:
            branch = scope._attributed_branch(rec, branch_context) or ""
            if branch not in branches or rec.get("type") != "assistant" or bool(rec.get("isSidechain")):
                continue
            fam = render._fam((rec.get("message") or {}).get("model", ""))
            branch_models[branch][fam] += 1

    scope.print_resolved_scope("pr-link", scope_label, roots)
    print(f"{'Branch':<35} {'PR':>5} {'Opus':>6} {'Sonnet':>7} {'IssueCmt':>9} {'ReviewCmt':>10}")
    print("-" * 80)

    for branch in branches:
        model_split = branch_models.get(branch, {})
        opus_n = model_split.get("opus", 0)
        sonnet_n = model_split.get("sonnet", 0)

        try:
            pr_result = subprocess.run(
                [
                    "gh", "pr", "list", "--head", branch, "--repo", pr_list_repo,
                    "--state", "all", "--json", "number", "--limit", "1",
                ],
                capture_output=True, text=True, check=True, timeout=gh_cli._GH_CALL_TIMEOUT_S,
            )
            prs = json.loads(pr_result.stdout or "[]")
        except (
            subprocess.CalledProcessError, json.JSONDecodeError, OSError, subprocess.TimeoutExpired,
        ) as exc:
            _pr_link_report_gh_failure(branch, "gh pr list", exc)
            print(f"{branch:<35} {'?':>5} {opus_n:>6} {sonnet_n:>7} {'gh-err':>9} {'':>10}")
            continue

        if not prs:
            print(f"{branch:<35} {'none':>5} {opus_n:>6} {sonnet_n:>7} {'—':>9} {'—':>10}")
            continue

        pr_number = prs[0]["number"]
        issue_comments = review_comments = 0

        try:
            ic = subprocess.run(
                [
                    "gh", "api", *api_host_args, f"repos/{repo}/issues/{pr_number}/comments",
                    "--paginate", "--jq", ".[].user.login",
                ],
                capture_output=True, text=True, check=True, timeout=gh_cli._GH_CALL_TIMEOUT_S,
            )
            issue_logins = [ln.strip() for ln in ic.stdout.splitlines() if ln.strip()]
            issue_comments = sum(1 for ln in issue_logins if not author or ln == author)

            rc = subprocess.run(
                [
                    "gh", "api", *api_host_args, f"repos/{repo}/pulls/{pr_number}/comments",
                    "--paginate", "--jq", ".[].user.login",
                ],
                capture_output=True, text=True, check=True, timeout=gh_cli._GH_CALL_TIMEOUT_S,
            )
            review_logins = [ln.strip() for ln in rc.stdout.splitlines() if ln.strip()]
            review_comments = sum(1 for ln in review_logins if not author or ln == author)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            _pr_link_report_gh_failure(branch, "gh api comments", exc)
            issue_comments = review_comments = -1

        print(f"{branch:<35} {pr_number:>5} {opus_n:>6} {sonnet_n:>7} {issue_comments:>9} {review_comments:>10}")


def _add_pr_link_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register the pr-link subparser on build_parser()'s subparsers action,
    binding cmd_pr_link through set_defaults(func=...).
    """
    p_pr = subparsers.add_parser("pr-link", help="Map branches to GitHub PRs and pull per-PR comment counts. Requires gh.")
    p_pr.add_argument(
        "--repo", metavar="OWNER/REPO",
        help="GitHub repo to query (default: parsed from this checkout's origin remote, host-qualified)",
    )
    p_pr.add_argument("--branches", required=True, metavar="B1,B2,...")
    p_pr.add_argument("--author", metavar="LOGIN", help="Filter comments to this GitHub login")
    scope._add_project_scope_args(p_pr)
    p_pr.set_defaults(func=cmd_pr_link)
