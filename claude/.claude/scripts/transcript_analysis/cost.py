"""The cost command family: cmd_cost, cmd_cost_trend, and every helper used
only by them -- corpus-wide dollar-cost reporting by token class/model
ID/thread/account/project, and per-ISO-week cost-trend accumulation.

Imports scope, corpus, pricing, render, and redaction by module (attribute
access, not by name) -- see scope.py's own top-of-file comment for why.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

from transcript_analysis import corpus, pricing, redaction, render, scope

# The harness's ephemeral-isolation branch name for an `isolation: "worktree"`
# subagent dispatch (see claude/.claude/CLAUDE.md's Agent Briefing section) —
# not a claim about which branch the dispatched work belongs to.
_WORKTREE_AGENT_BRANCH_PREFIX = "worktree-agent-"


def _session_branch_index(records: Sequence[dict]) -> list[tuple[float, str]]:
    """Build one session's sorted (timestamp, gitBranch) index from its own
    main-thread (non-sidechain) records — the carry-forward source
    _attributed_branch resolves a worktree-agent-* record's branch against.
    Built fresh per session, from that session's records alone: this is new
    machinery, not an extension of cmd_review_trace's position-based
    carry-forward convention (see its docstring), which never crosses the
    main-file/subagents-subdirectory boundary. A record with no parseable
    timestamp cannot be placed in timestamp order and is excluded.
    """
    index: list[tuple[float, str]] = []
    for main_rec in records:
        if bool(main_rec.get("isSidechain")):
            continue
        main_branch = main_rec.get("gitBranch")
        if not main_branch:
            continue
        main_ts = corpus._parse_ts(main_rec.get("timestamp"))
        if main_ts is None:
            continue
        index.append((main_ts, main_branch))
    index.sort()
    return index


def _attributed_branch(rec: dict, branch_index: Sequence[tuple[float, str]]) -> str | None:
    """Resolve one record's branch for --branches filtering. A record whose
    own gitBranch starts with _WORKTREE_AGENT_BRANCH_PREFIX is resolved
    instead against branch_index (see _session_branch_index): the entry with
    the largest timestamp <= the record's own, falling forward to the
    index's earliest entry when none precedes it (dispatched before any
    main-thread activity in the session, or the record itself carries no
    parseable timestamp), correctly resolving through a mid-session branch
    switch. Every other record's own gitBranch is returned unchanged.
    Returns None — the "?" sentinel case (see cmd_review_trace's docstring
    for the carry-forward convention this reuses) — when branch_index is
    empty (no main-thread branch-bearing record anywhere in the session) or
    when rec itself carries no gitBranch at all.
    """
    raw_branch = rec.get("gitBranch") or ""
    if not raw_branch.startswith(_WORKTREE_AGENT_BRANCH_PREFIX):
        return raw_branch or None
    if not branch_index:
        return None
    rec_ts = corpus._parse_ts(rec.get("timestamp"))
    if rec_ts is None:
        return branch_index[0][1]
    resolved = branch_index[0][1]
    for entry_ts, entry_branch in branch_index:
        if entry_ts > rec_ts:
            break
        resolved = entry_branch
    return resolved


def cmd_cost(args: argparse.Namespace) -> None:
    """CLI entry point for the cost subcommand.

    Reads the wall-clock date once, in UTC (matching _fmt_date/_MODEL_RATE_EXPIRES),
    and passes it to _cost_report so the staleness banner never reads a live
    clock under test. Root resolution happens here so --config-dir validation
    exits before any scan work.
    """
    roots = scope._resolve_cost_roots(args)
    _cost_report(args, datetime.now(UTC).date(), roots)


def _accumulate_per_account_turn(
    account_totals: dict, dollars_by_class: dict[str, float], token_counts: dict[str, int],
    turn_total: float, model: str,
) -> None:
    """Add one priced turn's per-class dollars/tokens and per-model dollars
    into one account's per_account entry -- the identical increments
    class_totals/class_token_totals/model_totals receive globally, just
    scoped to a single redact_ordinals ordinal."""
    for cls in pricing._TOKEN_CLASSES:
        account_totals["class_totals"][cls] += dollars_by_class[cls]
        account_totals["class_token_totals"][cls] += token_counts[cls]
    account_totals["model_totals"][model] += turn_total


def _print_token_class_table(
    class_totals: dict[str, float], class_token_totals: dict[str, int], grand_total: float,
    *, markdown: bool = False,
) -> None:
    if markdown:
        print("### Cost by token class\n")
        print("| Class | $ | Share | Tokens |")
        print("|---|---|---|---|")
        for cls in pricing._TOKEN_CLASSES:
            val = class_totals[cls]
            tok = class_token_totals[cls]
            print(f"| {cls} | {val:,.2f} | {render._pct_of(val, grand_total)} | {tok:,} |")
        print(f"| **total** | **{grand_total:,.2f}** | | |")
        return
    print("## Cost by token class\n")
    print(f"{'Class':<16} {'$':>14} {'Share':>7} {'Tokens':>14}")
    for cls in pricing._TOKEN_CLASSES:
        val = class_totals[cls]
        tok = class_token_totals[cls]
        print(f"{cls:<16} {val:>14,.2f} {render._pct_of(val, grand_total):>7} {tok:>14,}")
    print(f"{'total':<16} {grand_total:>14,.2f}")


def _print_model_id_table(model_totals: dict[str, float], grand_total: float, *, markdown: bool = False) -> None:
    if markdown:
        print("\n### Cost by model ID\n")
        print("| Model | $ | Share |")
        print("|---|---|---|")
        for model, val in sorted(model_totals.items(), key=lambda kv: kv[1], reverse=True):
            print(f"| {model} | {val:,.2f} | {render._pct_of(val, grand_total)} |")
        return
    print("\n## Cost by model ID\n")
    print(f"{'Model':<28} {'$':>14} {'Share':>7}")
    for model, val in sorted(model_totals.items(), key=lambda kv: kv[1], reverse=True):
        print(f"{model:<28} {val:>14,.2f} {render._pct_of(val, grand_total):>7}")


def _print_thread_table(main_total: float, subagent_total: float, grand_total: float, *, markdown: bool = False) -> None:
    if markdown:
        print("\n### Cost by thread\n")
        print("| Thread | $ | Share |")
        print("|---|---|---|")
        print(f"| main | {main_total:,.2f} | {render._pct_of(main_total, grand_total)} |")
        print(f"| subagent | {subagent_total:,.2f} | {render._pct_of(subagent_total, grand_total)} |")
        return
    print("\n## Cost by thread\n")
    print(f"{'Thread':<10} {'$':>14} {'Share':>7}")
    print(f"{'main':<10} {main_total:>14,.2f} {render._pct_of(main_total, grand_total):>7}")
    print(f"{'subagent':<10} {subagent_total:>14,.2f} {render._pct_of(subagent_total, grand_total):>7}")


def _print_branch_exclusion_diagnostic(
    excluded_turns_by_branch: dict[str, int],
    excluded_transcript_ids: set[str],
    *,
    redact: bool,
    markdown: bool = False,
) -> None:
    """Surface --branches's record-drop path (see _attributed_branch) instead of
    leaving an excluded branch silently invisible in the report.

    No-op when nothing was excluded.
    markdown=True (--summary's always-redacted shape) prints one aggregate line only --
    no branch names, no transcript ids.
    Non-summary mode prints a full per-branch turn-count table: raw branch names under
    --no-redact, or deterministic sequential "branch-N" labels (sorted real-name order,
    mirroring project_repr_label's convention above) under the redact=True default.
    "?" (unresolved branch) is never assigned a sequential label.
    """
    if not excluded_turns_by_branch:
        return

    total_excluded_turns = sum(excluded_turns_by_branch.values())
    if markdown:
        print(
            f"\nBranch-filter exclusions: {total_excluded_turns:,} turns excluded across"
            f" {len(excluded_transcript_ids):,} transcript files (branch names redacted)"
        )
        return

    # "?" (the unresolved-branch sentinel) carries no identifying information,
    # so it is never assigned a sequential label -- only real branch names are.
    sequential_labels = (
        {
            branch: f"branch-{i}"
            for i, branch in enumerate(sorted(b for b in excluded_turns_by_branch if b != "?"), start=1)
        }
        if redact
        else {}
    )

    print("\n## Branch-filter exclusions\n")
    print(f"{'Branch':<16} {'Turns':>8}")
    for branch, count in sorted(excluded_turns_by_branch.items()):
        label = sequential_labels.get(branch, branch)
        print(f"{label:<16} {count:>8}")


# A root whose earliest in-scope turn is more than this many seconds newer
# than a requested --since window's start fires _cost_report's
# corpus-coverage warning below -- one day, not zero, so ordinary
# per-record timestamp variance right at the window boundary doesn't warn.
_CORPUS_COVERAGE_WARNING_THRESHOLD_SECONDS = 86400


def _cost_report(args: argparse.Namespace, today: date, roots: Sequence[Path] | None = None) -> None:
    """Corpus-wide dollar-cost report by token class, model ID, and context-at-turn bucket.

    Sidechain turns are included (include_subagents=True) so dispatched spend counts toward the total.
    roots=None yields the single-root report with no per-root scan-summary lines (default for all direct callers but cmd_cost).
    --summary renders an aggregate-only block; see summary_mode branches.
    --branches filters on each record's carry-forward-attributed branch (_attributed_branch), not its literal gitBranch.
    """
    top_n: int = getattr(args, "top", 20) or 20
    redact: bool = not bool(getattr(args, "no_redact", False))

    scan_roots: Sequence[Path] = roots if roots is not None else (scope.PROJECTS_DIR,)
    multi_root = len(scan_roots) > 1

    summary_mode: bool = bool(getattr(args, "summary", False))
    if summary_mode:
        # --this-repo alone is the gate: every _path_to_project_slug-derived
        # slug is "-"-prefixed, so "any --projects value other than the
        # literal default *" would still admit a machine-wide glob like "-*".
        if not getattr(args, "this_repo", False) or getattr(args, "projects", None) not in (None, "*"):
            print(
                "cost: --summary requires --this-repo and refuses any --projects scope"
                " (including the default glob) — see docs/transcript-analysis.md",
                file=sys.stderr,
            )
            sys.exit(2)
        if (
            bool(getattr(args, "by_project", False))
            or bool(getattr(args, "no_redact", False))
            or getattr(args, "extra_config_dirs", None)
        ):
            print(
                "cost: --summary refuses --by-project, --no-redact, and --config-dir in"
                " combination — it is a fixed, aggregate-only output mode",
                file=sys.stderr,
            )
            sys.exit(2)
        if multi_root:
            # Defense-in-depth: _resolve_cost_roots is the CLI-level enforcement point for --summary's
            # single-account scope, but every direct caller of _cost_report (including this module's own tests)
            # bypasses that boundary.
            print(
                "cost: --summary resolved to more than one root — refusing to"
                " report a multi-account total",
                file=sys.stderr,
            )
            sys.exit(2)

    # Defense-in-depth, same rationale as the --summary multi-root check above.
    if not redact and multi_root:
        print(
            "cost: --no-redact is refused when more than one root is in scope"
            " (--config-dir was given); drop --no-redact or scope to a single profile",
            file=sys.stderr,
        )
        sys.exit(2)

    if not redact:
        print(scope._DO_NOT_PUBLISH_BANNER)
        print(scope._DO_NOT_PUBLISH_BANNER, file=sys.stderr)

    since_ts, since_raw = scope._parse_since_nd_arg(args, "cost")
    since_label = since_raw or ""
    branch_filter = scope._branch_filter(args)

    # _resolve_project_scope's fail-closed --this-repo check runs before
    # _build_redact_map's full-corpus disk scan, so an out-of-repo failure
    # exits without paying for that scan.
    session_iter, scope_label = scope._resolve_project_scope(args, "cost", include_subagents=True, roots=roots)

    # Resolved once, outside the per-session loop below — _root_index_for_path
    # runs once per session (per-account ordinal resolution needs it even for
    # unpriced sessions), and re-resolving every root on every call would be
    # a per-element filesystem stat inside that loop.
    resolved_scan_roots = [root.resolve() for root in scan_roots] if multi_root else []
    # redact_ordinals is the resolved-path-sorted mapping _build_redact_map's
    # keys, the per-row/--by-project lookups, and the per-root scan-diagnostic
    # loop below all share, so the same physical root reads as the same
    # account-N everywhere regardless of scan order. Computed unconditionally
    # (not gated on multi_root) -- the diagnostic loop runs at single root too
    # (roots is not None whenever cmd_cost's CLI path is reached, even with
    # zero declared extras), and _redaction_ordinals is correct and cheap on a
    # single-element list. root_by_ordinal is its inverse -- used for the
    # (unreachable when multi_root, since --no-redact is refused above)
    # non-redact display path, and for the corpus-coverage warning below.
    redact_ordinals: dict[Path, int] = scope._redaction_ordinals(scan_roots)
    root_by_ordinal: dict[int, Path] = {redact_ordinals[root.resolve()]: root for root in scan_roots}
    # A single explicit root's ordinal never varies across sessions, so it's
    # resolved once here rather than per-session like resolved_scan_roots'
    # multi-root lookup below -- None when roots is None (the non-cmd_cost
    # direct-call path, which gets no per-root coverage warning either).
    single_root_ordinal: int | None = (
        redact_ordinals[scan_roots[0].resolve()] if roots is not None and not multi_root else None
    )

    total_transcripts_scanned = 0
    if roots is not None:
        glob = scope._projects_glob(args)
        # --this-repo's slugs were already resolved (and cached on args) by
        # _resolve_project_scope above; passing them keeps this diagnostic
        # scan repo-scoped instead of falling back to _projects_glob's "*".
        this_repo_slugs = getattr(args, "_this_repo_slugs", None) if args.this_repo else None
        for root in scan_roots:
            # Looked up via redact_ordinals, not enumerate()'s scan-order index —
            # the same physical root must read as the same account-N here as in
            # the report below, regardless of which order scan_roots iterates in.
            root_label = f"account-{redact_ordinals[root.resolve()]}" if redact else str(root.parent)
            try:
                scanned, skipped = scope._scan_root_transcripts(root, glob, slugs=this_repo_slugs)
            except PermissionError as exc:
                # str(exc) on a PermissionError typically embeds the offending
                # path — suppressed under default redaction so a permission
                # failure can't leak the raw config-dir path it's reporting on.
                detail = str(exc) if not redact else "permission denied"
                print(f"cost: {root_label}: cannot scan ({detail}) — treating as 0 transcripts", file=sys.stderr)
                scanned, skipped = 0, 0
            print(f"cost: {root_label}: scanned {scanned:,} transcripts, {skipped:,} skipped (unreadable)")
            total_transcripts_scanned += scanned
            if scanned == 0:
                print(
                    f"WARNING: cost: {root_label}: no transcripts found for this scope"
                    " — check the config dir and --projects/--this-repo filter."
                )

    # --summary skips the redact map and the per-project-dir-count scope
    # header ("this repo (N project dirs)") -- that count comes from `git
    # worktree list` (this repo's own local worktrees), not account
    # identity; the input that IS identity-keyed under --summary, a raw
    # --projects value, is already refused above. Its own scope line below
    # reports total_transcripts_scanned instead.
    redact_map: dict[redaction._RedactMapKey, str] = {}
    if not summary_mode:
        redact_map = redaction._build_redact_map(roots) if redact else {}
        if redact:
            print(
                f"Corpus fingerprint: {redaction._corpus_fingerprint(redact_map)}"
                "  (private-project labels are not comparable across a different fingerprint)"
            )
        scope.print_resolved_scope("cost", scope_label, scan_roots)

    session_redact_map: dict[str, str] = {}
    by_project: bool = bool(getattr(args, "by_project", False))

    class_totals: dict[str, float] = dict.fromkeys(pricing._TOKEN_CLASSES, 0.0)
    class_token_totals: dict[str, int] = dict.fromkeys(pricing._TOKEN_CLASSES, 0)
    model_totals: dict[str, float] = defaultdict(float)
    # One class_totals/class_token_totals/model_totals triple per
    # redact_ordinals ordinal, mirroring edit-format's own per_account shape.
    # Initialized up front for every ordinal so a zero-spend account still
    # renders a clean zero-state row instead of a missing key.
    per_account: dict[int, dict] = (
        {
            ordinal: {
                "class_totals": dict.fromkeys(pricing._TOKEN_CLASSES, 0.0),
                "class_token_totals": dict.fromkeys(pricing._TOKEN_CLASSES, 0),
                "model_totals": defaultdict(float),
            }
            for ordinal in redact_ordinals.values()
        }
        if multi_root
        else {}
    )
    unpriced_tokens: dict[str, int] = defaultdict(int)
    # Turn counts (not re-priced dollars) tallied per branch a --branches
    # filter dropped a record for, plus the distinct transcript files those
    # drops span -- feeds _print_branch_exclusion_diagnostic below.
    excluded_turns_by_branch: dict[str, int] = defaultdict(int)
    excluded_transcript_ids: set[str] = set()
    bucket_totals: dict[str, float] = defaultdict(float)
    # Earliest in-scope turn timestamp seen per root ordinal, regardless of
    # --since -- feeds the corpus-coverage warning after the loop below, so a
    # well-covered root can't mask a short one.
    root_earliest_ts: dict[int, float] = {}
    session_rows: list[dict] = []
    stale_models: set[str] = set()
    main_total = 0.0
    subagent_total = 0.0
    priced_session_count = 0
    priced_turn_count = 0
    # Feed pricing._warn_if_subagent_format_drift below -- unlike main_total/
    # subagent_total, total_sidechain_turns counts every isSidechain
    # assistant turn read (mirroring cmd_subagents' corpus_sidechain_turns),
    # not just priced ones, so an unpriced-model session can't mask drift.
    total_spawns = 0
    total_sidechain_turns = 0
    # Keyed on (root_index_or_None, project_family) — see _project_family.
    project_totals: dict[tuple[int | None, str], float] = defaultdict(float)
    # One representative raw scoped_label per project_totals key, for redact
    # lookup (_redact_proj_label) — several worktree-suffixed raw labels can
    # collapse to one family, so the smallest raw label is picked for a
    # deterministic (not iteration-order-dependent) display choice.
    project_repr_label: dict[tuple[int | None, str], redaction._RedactMapKey] = {}

    for jsonl, records in session_iter:
        records = pricing.dedup_turns_by_request_id(records)
        total_spawns += pricing._count_subagent_spawns(records)
        raw_proj_label = redaction._derive_proj_label(jsonl)
        session_id = jsonl.stem[:12]
        if redact and not summary_mode:
            redaction._assign_session_redact_label(session_id, session_redact_map)
        session_total = 0.0

        # Hoisted out of the by_project-only block below so the per-account
        # accumulator (turn loop, further down) has this session's ordinal
        # regardless of --by-project.
        account_ordinal: int | None = None
        if multi_root:
            root_position = scope._root_index_for_path(jsonl, resolved_scan_roots)
            account_ordinal = redact_ordinals[resolved_scan_roots[root_position]]
        elif single_root_ordinal is not None:
            account_ordinal = single_root_ordinal

        # Only needed when --branches is active — the carry-forward source
        # _attributed_branch resolves each worktree-agent-* record against.
        branch_index = _session_branch_index(records) if branch_filter is not None else None

        for rec in records:
            if rec.get("type") != "assistant":
                continue
            # Counted before the usage/since/branch filters below, mirroring
            # cmd_subagents' corpus_sidechain_turns -- the drift canary needs
            # every isSidechain turn read, not just the ones this report ends
            # up pricing or displaying.
            if bool(rec.get("isSidechain")):
                total_sidechain_turns += 1
            msg = rec.get("message") or {}
            usage = msg.get("usage")
            if not usage:
                continue

            # Parsed unconditionally (not just when since_ts is set) so
            # root_earliest_ts reflects the corpus's actual earliest turn,
            # not just the earliest turn inside an already-applied --since
            # filter.
            rec_ts = corpus._parse_ts(rec.get("timestamp"))
            if account_ordinal is not None and rec_ts is not None:
                earliest_so_far = root_earliest_ts.get(account_ordinal)
                if earliest_so_far is None or rec_ts < earliest_so_far:
                    root_earliest_ts[account_ordinal] = rec_ts

            if since_ts is not None and (rec_ts is None or rec_ts < since_ts):
                continue

            if branch_filter is not None:
                attributed_branch = _attributed_branch(rec, branch_index)
                if attributed_branch is None or attributed_branch not in branch_filter:
                    excluded_turns_by_branch[attributed_branch or "?"] += 1
                    excluded_transcript_ids.add(session_id)
                    continue

            model = msg.get("model", "")
            dollars_by_class, context_at_turn, turn_unpriced_tokens = pricing._price_turn(model, usage)

            if dollars_by_class is None:
                unpriced_tokens[model] += turn_unpriced_tokens
                continue

            if today > pricing._MODEL_RATE_EXPIRES[model]:
                stale_models.add(model)

            token_counts = pricing._token_counts(usage)
            turn_total = 0.0
            for cls in pricing._TOKEN_CLASSES:
                class_totals[cls] += dollars_by_class[cls]
                class_token_totals[cls] += token_counts[cls]
                turn_total += dollars_by_class[cls]
            model_totals[model] += turn_total
            # multi_root and summary_mode can never co-occur here -- --summary
            # refuses --config-dir above, so this accumulator is unreachable
            # (not merely unused) under --summary.
            if multi_root:
                _accumulate_per_account_turn(per_account[account_ordinal], dollars_by_class, token_counts, turn_total, model)
            bucket_totals[pricing._context_bucket(context_at_turn)] += turn_total
            session_total += turn_total
            priced_turn_count += 1
            if bool(rec.get("isSidechain")):
                subagent_total += turn_total
            else:
                main_total += turn_total

        if session_total:
            priced_session_count += 1
            if not summary_mode:
                if multi_root:
                    scoped_label: redaction._RedactMapKey = (account_ordinal, raw_proj_label)
                else:
                    scoped_label = raw_proj_label
                if redact:
                    proj_display = redaction._redact_proj_label(scoped_label, redact_map)
                    if proj_display == redaction._REDACT_MAP_MISS_TOKEN:
                        # Omits raw_proj_label deliberately — main() has no top-level handler, so this
                        # message reaches stderr uncaught; the label hash + root ordinal are enough to
                        # debug without leaking it.
                        root_ordinal = scoped_label[0] if isinstance(scoped_label, tuple) else None
                        label_hash = hashlib.sha256(raw_proj_label.encode()).hexdigest()[:12]
                        root_desc = f"root {root_ordinal}" if root_ordinal is not None else "the single scan root"
                        raise AssertionError(
                            f"cost: redact map has no entry for a project label under {root_desc}"
                            f" (label hash {label_hash}) — the redact map's roots are out of sync"
                            " with the session iterator's roots"
                        )
                else:
                    proj_display = raw_proj_label
                session_rows.append({
                    "session_id": session_id,
                    "proj_label": proj_display,
                    "total": session_total,
                })

                if by_project:
                    root_component = scoped_label[0] if multi_root else None
                    project_key = (root_component, redaction._project_family(raw_proj_label))
                    project_totals[project_key] += session_total
                    raw_part = scoped_label[1] if isinstance(scoped_label, tuple) else scoped_label
                    current_repr = project_repr_label.get(project_key)
                    current_raw_part = current_repr[1] if isinstance(current_repr, tuple) else current_repr
                    if current_repr is None or raw_part < current_raw_part:
                        project_repr_label[project_key] = scoped_label

    pricing._warn_if_subagent_format_drift(total_spawns, total_sidechain_turns)

    if since_ts is not None:
        for ordinal, earliest_ts in sorted(root_earliest_ts.items()):
            if earliest_ts - since_ts > _CORPUS_COVERAGE_WARNING_THRESHOLD_SECONDS:
                root_label = f"account-{ordinal}" if redact else str(root_by_ordinal[ordinal].parent)
                print(
                    f"WARNING: cost: {root_label}: earliest turn found is {render._fmt_date(earliest_ts)},"
                    f" more than 1 day after the requested --since window start ({render._fmt_date(since_ts)})"
                    " — this root's local corpus does not fully cover the requested window."
                )

    grand_total = sum(class_totals.values())

    # Guards the accumulator split (double-count/drop/misroute), not _price_turn's math — a wrong
    # price would move both sides together. Tolerance is float64 noise, not rounding slack.
    if abs(main_total + subagent_total - grand_total) > 1e-6:
        raise AssertionError(
            f"cost: main ({main_total:.6f}) + subagent ({subagent_total:.6f}) spend"
            f" does not equal the grand total ({grand_total:.6f}) — the isSidechain"
            " split is out of sync with the token-class totals"
        )

    if by_project:
        project_grand_total = sum(project_totals.values())
        if abs(project_grand_total - grand_total) > 1e-6:
            raise AssertionError(
                f"cost: --by-project rows sum to {project_grand_total:.6f} but the grand"
                f" total is {grand_total:.6f} — per-project aggregation is out of sync"
                " with the token-class totals"
            )

    if multi_root:
        per_account_class_total = sum(sum(acct["class_totals"].values()) for acct in per_account.values())
        per_account_model_total = sum(sum(acct["model_totals"].values()) for acct in per_account.values())
        if abs(per_account_class_total - grand_total) > 1e-6 or abs(per_account_model_total - grand_total) > 1e-6:
            raise AssertionError(
                f"cost: per-account totals (class {per_account_class_total:.6f}, model"
                f" {per_account_model_total:.6f}) do not both equal the grand total"
                f" ({grand_total:.6f}) — the per-account accumulator is out of sync with"
                " the global token-class/model totals"
            )

    title_since = f"last {since_label}" if since_label else "all time"
    if summary_mode:
        print(f"\nCost summary ({title_since})\n")
        print(
            f"Scope: this account only ({total_transcripts_scanned:,} transcripts scanned, "
            f"{priced_session_count:,} priced sessions, {priced_turn_count:,} priced turns)"
            " — dropping --summary reports every declared account too"
        )
    else:
        print(f"\n## Cost report ({title_since})\n")

    if stale_models:
        print(
            "STALE PRICING — today is past the re-verify-by date for: "
            + ", ".join(sorted(stale_models))
            + f". Re-check rates at {pricing._PRICING_SOURCE_URL} before publishing the figures below.\n"
        )

    _print_token_class_table(class_totals, class_token_totals, grand_total, markdown=summary_mode)
    _print_model_id_table(model_totals, grand_total, markdown=summary_mode)
    total_unpriced_tokens = sum(unpriced_tokens.values())
    if summary_mode:
        # A dedicated, always-present line rather than the full report's
        # per-model breakdown below — an unrecognized model ID must never
        # silently understate a published figure with no marker, even at $0.
        print(f"\nUnpriced tokens: {total_unpriced_tokens:,} tokens across {len(unpriced_tokens)} model IDs")
    else:
        for model, tok in sorted(unpriced_tokens.items()):
            print(f"{model:<28} {'unpriced':>14} {tok:>10,} tokens")
        print(f"\nUnpriced tokens (unknown model IDs): {total_unpriced_tokens:,}")

    if not summary_mode:
        print(
            f"\n## Cost by context-at-turn bucket (input_tokens + cache_read_input_tokens"
            f" + ephemeral_1h + ephemeral_5m tokens, {pricing._CONTEXT_BUCKET_THRESHOLD:,} boundary)\n"
        )
        print(f"{'Bucket':<8} {'$':>14} {'Share':>7}")
        for bucket in (pricing._CONTEXT_BUCKET_UNDER, pricing._CONTEXT_BUCKET_OVER):
            val = bucket_totals.get(bucket, 0.0)
            print(f"{bucket:<8} {val:>14,.2f} {render._pct_of(val, grand_total):>7}")

    _print_thread_table(main_total, subagent_total, grand_total, markdown=summary_mode)

    if branch_filter is not None:
        _print_branch_exclusion_diagnostic(
            excluded_turns_by_branch, excluded_transcript_ids, redact=redact, markdown=summary_mode
        )

    if summary_mode:
        return

    if multi_root:
        print("\n## Cost by account\n")
        for ordinal in sorted(per_account):
            account_totals = per_account[ordinal]
            account_grand_total = sum(account_totals["class_totals"].values())
            print(f"\n### account-{ordinal}\n")
            _print_token_class_table(
                account_totals["class_totals"], account_totals["class_token_totals"], account_grand_total
            )
            _print_model_id_table(account_totals["model_totals"], account_grand_total)

    if by_project:
        print("\n## Cost by project\n")
        if not project_totals:
            print("(no priced turns in range)")
        elif multi_root:
            print(f"{'Account':<12} {'Project':<24} {'$':>14} {'Share':>7}")
            for (ordinal, family), val in sorted(project_totals.items(), key=lambda kv: kv[1], reverse=True):
                account_col = f"account-{ordinal}" if redact else str(root_by_ordinal[ordinal].parent)
                repr_label = project_repr_label[(ordinal, family)]
                # The redact map's multi-root value is "account-K/private-
                # project-N" (see _build_redact_map) — strip the account
                # prefix here since it's already the Account column above;
                # printing both is redundant. "claude-config" carries no "/"
                # and passes through unchanged.
                proj_col = (
                    redaction._redact_proj_label(repr_label, redact_map).split("/", 1)[-1] if redact else family
                )
                print(f"{account_col:<12} {proj_col:<24} {val:>14,.2f} {render._pct_of(val, grand_total):>7}")
        else:
            print(f"{'Project':<24} {'$':>14} {'Share':>7}")
            for (root_idx, family), val in sorted(project_totals.items(), key=lambda kv: kv[1], reverse=True):
                repr_label = project_repr_label[(root_idx, family)]
                proj_col = redaction._redact_proj_label(repr_label, redact_map) if redact else family
                print(f"{proj_col:<24} {val:>14,.2f} {render._pct_of(val, grand_total):>7}")

    print(f"\n## Top {top_n} sessions by dollars\n")
    if not session_rows:
        print("(no priced turns in range)")
    else:
        print(f"{'Session':<16} {'Proj':<24} {'$':>14}")
        for row in sorted(session_rows, key=lambda r: r["total"], reverse=True)[:top_n]:
            sid = redaction._redact_session_id(row["session_id"], session_redact_map) if redact else row["session_id"]
            print(f"{sid:<16} {row['proj_label']:<24} {row['total']:>14,.2f}")


def cmd_cost_trend(args: argparse.Namespace) -> None:
    """CLI entry point for the cost-trend subcommand.

    Reads the wall-clock date exactly once, here, then delegates to
    _cost_trend_report, which takes `today` as an explicit parameter — the
    same split _cost_report uses so the trailing week's "(partial)" label
    doesn't depend on a live clock read inside a function under test.
    """
    _cost_trend_report(args, datetime.now(UTC).date())


def compute_cost_trend_data(session_iter) -> tuple[dict[str, dict[str, float]], int, int]:
    """Per-ISO-week $/opus-share/>=200k-context-share accumulation behind
    both cost-trend's own report and cost-ledger's per-week row, extracted
    so the two share one scan instead of two implementations kept in sync
    by hand. Returns (week_str -> {"total": $, "opus": $, "context_over": $,
    "context_class_dollars": $}, unpriced_turns, unpriced_tokens); a week
    with zero priced turns is simply absent as a key, not present with
    zeros — cost-ledger's own "no row for this week yet" gap detection
    relies on that absence. context_over is bucket-based dollar share
    (_context_bucket); context_class_dollars is the dollar share from
    context-class token pricing (cache_read + cache_write tiers) regardless
    of bucket — cost-ledger's only consumer of the latter.
    """
    data: dict[str, dict[str, float]] = defaultdict(
        lambda: {"total": 0.0, "opus": 0.0, "context_over": 0.0, "context_class_dollars": 0.0}
    )
    unpriced_turns = 0
    unpriced_tokens = 0

    for _jsonl, records in session_iter:
        records = pricing.dedup_turns_by_request_id(records)
        for rec in records:
            if rec.get("type") != "assistant":
                continue
            msg = rec.get("message") or {}
            usage = msg.get("usage")
            if not usage:
                continue
            rec_ts = corpus._parse_ts(rec.get("timestamp"))
            if rec_ts is None:
                continue
            model = msg.get("model", "")
            dollars_by_class, context_at_turn, turn_unpriced_tokens = pricing._price_turn(model, usage)
            if dollars_by_class is None:
                unpriced_turns += 1
                unpriced_tokens += turn_unpriced_tokens
                continue
            turn_total = sum(dollars_by_class.values())
            iso = datetime.fromtimestamp(rec_ts, tz=UTC).isocalendar()
            week_str = f"{iso.year}-W{iso.week:02d}"
            d = data[week_str]
            d["total"] += turn_total
            if render._fam(model) == "opus":
                d["opus"] += turn_total
            if pricing._context_bucket(context_at_turn) == pricing._CONTEXT_BUCKET_OVER:
                d["context_over"] += turn_total
            d["context_class_dollars"] += (
                dollars_by_class["cache_read"] + dollars_by_class["cache_write_1h"] + dollars_by_class["cache_write_5m"]
            )

    return dict(data), unpriced_turns, unpriced_tokens


def _cost_trend_report(args: argparse.Namespace, today: date) -> None:
    """Per-ISO-week dollar spend, Opus-family share, and >=200k context-bucket
    share. Reuses _price_turn's per-turn pricing (same as cost) and
    cmd_spend_over_threshold's ISO-week bucketing. Sidechain turns are
    included (include_subagents=True). The most recent bucket is labeled
    "(partial)" rather than presented as a complete week's total. Turns whose
    model ID has no _MODEL_BASE_INPUT_RATES entry are excluded from every
    week's totals and counted corpus-wide (mirrors cmd_audit_routing's
    unpriced-turns convention). Roots resolve via _resolve_cost_roots (cost's
    own --config-dir contract), not the generic _resolve_scan_roots -- the
    one funnel that understands a repeatable --config-dir/extra_config_dirs.
    """
    redact: bool = not bool(getattr(args, "no_redact", False))
    roots = scope._resolve_cost_roots(args, subcommand="cost-trend")
    session_iter, scope_label = scope._resolve_project_scope(args, "cost-trend", include_subagents=True, roots=roots)

    # Mirrors cost's/context-distribution's own per-root scan diagnostic --
    # without it, a stale or misconfigured --config-dir root silently
    # contributes nothing to the weekly trend with no signal.
    glob = scope._projects_glob(args)
    this_repo_slugs = getattr(args, "_this_repo_slugs", None) if args.this_repo else None
    redact_ordinals: dict[Path, int] = scope._redaction_ordinals(roots)
    for root in roots:
        root_label = f"account-{redact_ordinals[root.resolve()]}" if redact else str(root.parent)
        try:
            scanned, skipped = scope._scan_root_transcripts(root, glob, slugs=this_repo_slugs)
        except PermissionError as exc:
            # str(exc) on a PermissionError typically embeds the offending
            # path — suppressed under default redaction so a permission
            # failure can't leak the raw config-dir path it's reporting on.
            detail = str(exc) if not redact else "permission denied"
            print(f"cost-trend: {root_label}: cannot scan ({detail}) — treating as 0 transcripts", file=sys.stderr)
            scanned, skipped = 0, 0
        print(f"cost-trend: {root_label}: scanned {scanned:,} transcripts, {skipped:,} skipped (unreadable)")
        if scanned == 0:
            print(
                f"WARNING: cost-trend: {root_label}: no transcripts found for this scope"
                " — check the config dir and --projects/--this-repo filter."
            )

    scope.print_resolved_scope("cost-trend", scope_label, roots)

    data, unpriced_turns, unpriced_tokens = compute_cost_trend_data(session_iter)

    if not data:
        print("No priced turns found.")
        if unpriced_turns:
            print(f"  ({unpriced_turns:,} unpriced turns / {unpriced_tokens:,} tokens excluded from priced spend)")
        return

    current_iso = today.isocalendar()
    current_week_str = f"{current_iso.year}-W{current_iso.week:02d}"

    header = f"{'Week':<20} {'$':>14} {'Context%':>9} {'Opus%':>7}"
    print(header)
    print("-" * len(header))
    for week_str in sorted(data):
        d = data[week_str]
        label = f"{week_str} (partial)" if week_str == current_week_str else week_str
        print(
            f"{label:<20} {d['total']:>14,.2f} "
            f"{render._pct_of(d['context_over'], d['total']):>9} {render._pct_of(d['opus'], d['total']):>7}"
        )
    if unpriced_turns:
        print(f"\n  ({unpriced_turns:,} unpriced turns / {unpriced_tokens:,} tokens excluded from priced spend)")
