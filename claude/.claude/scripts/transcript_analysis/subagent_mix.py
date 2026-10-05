"""The subagent-mix and cost-counts commands: cmd_subagent_mix's per-branch
subagent_type spawn counts and per-agentType model-mix and dollar table, plus
the public-PR-body review-round and spawn counts of cmd_cost_counts, which
share the _UNKNOWN_SUBAGENT_TYPE fallback and the agent-type disclosure
allowlist.

Imports corpus, pricing, redaction, render, review_rounds, and scope by
module (attribute access, not by name) -- see scope.py's own top-of-file
comment for why.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path

from transcript_analysis import corpus, pricing, redaction, render, review_rounds, scope

# subagent-mix's model-mix table bucket for a dispatch whose meta.json carries
# no "model" key at all (no explicit model was requested).
_UNREQUESTED_MODEL_LABEL = "(none)"


def cmd_subagent_mix(args: argparse.Namespace) -> None:
    """Subagent_type spawn counts per branch, plus a second, agentType-keyed
    table of each type's model mix: Runs (dispatches with a readable
    meta.json + sibling .jsonl — a dangling pair is excluded from this count
    and reported separately under Dangling), Declared (frontmatter `model:`
    from the dispatch's own root's agents/<agentType>.md, or "built-in" with
    no on-disk file), Requested (meta.json's own "model" key, "(none)" when
    absent), and Observed (the modal real model ID across the dispatch's own
    sidechain, via _fam; "mixed" when two distinct real IDs appear), plus
    Actual $ (and, when --reprice-as is given, Counterfactual $ and Delta).

    --since limits both tables to records timestamped on or after the window
    start. --since-date/--until-date instead bound only the Actual $ /
    Counterfactual $ columns, and do so per sidechain assistant record (not
    per dispatch) — a dispatch straddling the window edge must not attribute
    its whole sidechain's dollars to the window just because it started
    inside it. --reprice-as re-prices that same in-window usage at an
    alternate model ID (validated against _MODEL_BASE_INPUT_RATES's keys),
    adding the Counterfactual $ and Delta (Actual − Counterfactual) columns.
    --config-dir (repeatable) scans additional Claude Code config
    directories the same way cost does.
    Under more than one root, both branch names and subagent_type values
    are redacted (_root_scoped_display_label) — subagent_type can name a
    project-scoped custom agent definition, the same disclosure risk
    gitBranch carries.
    Both tables aggregate on the raw (root, branch) / (root, subagent_type)
    pair, not the printed label — the redacted or disclosed label is
    computed lazily at print time (idempotently, so a value requested by
    both tables renders the same label each time), so two accounts'
    same-named agentType (or two raw values differing only in stripped
    control bytes) never merge into one row.
    --per-session is refused outright under multi-root, since it would
    otherwise join a foreign account's own session-id prefix to its branch
    name.
    Under --this-repo, every branch prints raw (account-<K>/<branch>) with
    no attestation gate: this function excludes isSidechain records before
    ever reading gitBranch, unlike cmd_subagents, so a subagent's own
    gitBranch never reaches this table.
    A subagent_type prints raw only when it is tracked in this repo's own
    agents/ directory or is a Claude Code built-in
    (_repo_tracked_agent_type_names); every other value stays opaque.
    --this-repo's disclosure is repo-agnostic: it applies to whichever repo
    --this-repo resolves to for the invoking CWD, not specifically to
    claude-config.
    """
    roots = scope._resolve_cost_roots(args, "subagent-mix")
    multi_root = len(roots) > 1
    this_repo = args.this_repo
    branch_filter = scope._branch_filter(args)
    per_session: bool = bool(getattr(args, "per_session", False))

    if multi_root and per_session:
        print(
            "subagent-mix: --per-session is refused when more than one root is in"
            " scope (--config-dir was given) — a per-session row would join a"
            " foreign account's own session-id prefix to its branch name; drop"
            " --per-session or scope to a single profile",
            file=sys.stderr,
        )
        sys.exit(2)

    reprice_as: str | None = getattr(args, "reprice_as", None) or None
    if reprice_as is not None and reprice_as not in pricing._MODEL_BASE_INPUT_RATES:
        valid = ", ".join(sorted(pricing._MODEL_BASE_INPUT_RATES))
        print(
            f"subagent-mix: --reprice-as: unknown model ID {reprice_as!r}; valid values: {valid}",
            file=sys.stderr,
        )
        sys.exit(1)

    if multi_root:
        print(scope._DO_NOT_PUBLISH_BANNER)
        print(scope._DO_NOT_PUBLISH_BANNER, file=sys.stderr)

    since_ts, _since_raw = scope._parse_since_nd_arg(args, "subagent-mix")

    # Bounds the Actual $ / Counterfactual $ columns only, per sidechain
    # assistant record (see _dispatch_usage_summary) -- independent of
    # since_ts above, which keeps its existing dispatch-level scope over
    # every other column in this table.
    dollar_since_ts, dollar_until_ts = scope._parse_absolute_window_args(
        args, "subagent-mix", since_attr="since_date", until_attr="until_date"
    )

    # Read once, matching cost's own "never read the clock inside the
    # per-record loop" rationale -- kept as a plain wall-clock read here
    # (rather than cost's separate entry/report split) since no
    # test asserts on stale-pricing output for subagent-mix.
    today = datetime.now(UTC).date()
    total_unpriced_turns = 0
    total_unpriced_tokens = 0
    all_stale_models: set[str] = set()

    session_iter, scope_label = scope._resolve_project_scope(args, "subagent-mix", roots=roots)
    scope.print_resolved_scope("subagent-mix", scope_label, roots)

    resolved_roots = [root.resolve() for root in roots] if multi_root else []
    # Resolved-path-sorted, not _root_index_for_path's raw scan-order position
    # — the same physical root must read as the same account-N here as in
    # every other multi-root diagnostic (redaction._build_redact_map, cost's
    # per-row key), regardless of which profile is currently active.
    redact_ordinals: dict[Path, int] = scope._redaction_ordinals(roots) if multi_root else {}
    branch_redact_map: dict[tuple[int, str], str] = {}
    # subagent_type redact map is separate from branch_redact_map so the two
    # kinds' per-account counters (account-<K>/branch-<N> vs.
    # account-<K>/agent-type-<N>) never share a numbering sequence.
    subagent_type_redact_map: dict[tuple[int, str], str] = {}
    # Each root's own agents/ directory, so a dispatch's Declared pin is read
    # from the account it actually came from, not this process's own
    # config_dir() — index 0 is always this process's own root (roots[0]),
    # matching root_idx's None-under-single-root convention below.
    agent_dirs = [root.parent / "agents" for root in roots]

    # Keyed on (root_index_or_None, raw gitBranch, session_suffix_or_None) —
    # raw, never the (possibly-sanitized) display label, so two raw branch
    # values that differ only in stripped control bytes stay distinct rows
    # instead of silently merging their spawn/session counts. session_suffix
    # is jsonl.stem[:8] under --per-session (always root_idx=None, since
    # multi-root refuses --per-session), and None when sessions on the same
    # branch aggregate into one row. The printed label (_mix_branch_label,
    # below) translates root_idx through redact_ordinals only at print time.
    data: dict[tuple[int | None, str, str | None], dict] = defaultdict(
        lambda: {"sessions": 0, "spawns": defaultdict(int), "skills": defaultdict(int)}
    )
    # (root_index_or_None, raw subagent_type) -> model-mix row. Only created
    # for a type that has at least one meta.json match (even a dangling
    # one) — a dispatch with no matching meta.json at all is excluded
    # entirely, matching cmd_reviewer_yield's own precedent for the same
    # join. Keyed on the raw tuple (not the display label) for the same
    # reason as `data` above: root_idx alone already root-scopes two
    # accounts' same-named agentType apart, with no dependency on the label
    # string encoding that uniqueness.
    model_mix: dict[tuple[int | None, str], dict] = defaultdict(lambda: {
        "runs": 0,
        "dangling": 0,
        "requested": defaultdict(int),
        "observed": defaultdict(int),
        "declared_seen": set(),
        "actual_dollars": 0.0,
        "counterfactual_dollars": 0.0,
    })
    declared_pin_cache: dict[tuple[Path, str], str] = {}
    total_meta_read_errors = 0

    for jsonl, records in session_iter:
        root_idx = scope._root_index_for_path(jsonl, resolved_roots) if multi_root else None
        agents_dir = agent_dirs[root_idx if root_idx is not None else 0]
        dispatch_index, session_meta_read_errors = corpus._index_subagent_dispatches(jsonl)
        total_meta_read_errors += session_meta_read_errors
        session_data: dict[str, dict] = defaultdict(
            lambda: {"spawns": defaultdict(int), "skills": defaultdict(int)}
        )
        for rec in records:
            if rec.get("type") != "assistant" or bool(rec.get("isSidechain")):
                continue
            branch = rec.get("gitBranch") or ""
            if not branch or (branch_filter and branch not in branch_filter):
                continue
            if since_ts is not None:
                rec_ts = corpus._parse_ts(rec.get("timestamp"))
                if rec_ts is None or rec_ts < since_ts:
                    continue
            for block in ((rec.get("message") or {}).get("content") or []):
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                name = block.get("name")
                inp = block.get("input") or {}
                if name in pricing._SPAWN_TOOL_NAMES:
                    stype = inp.get("subagent_type") or _UNKNOWN_SUBAGENT_TYPE
                    session_data[branch]["spawns"][stype] += 1

                    paired = dispatch_index.get(block.get("id") or "")
                    if paired is not None:
                        paired_jsonl, requested_model = paired
                        row = model_mix[(root_idx, stype)]
                        # _declared_pin reads from the on-disk agent file, so it
                        # needs the real subagent_type (stype), never the
                        # (possibly-redacted) display label built at print time.
                        row["declared_seen"].add(_declared_pin(stype, agents_dir, declared_pin_cache))
                        (
                            observed, actual_dollars, _dollars_by_class, counterfactual_dollars,
                            dispatch_unpriced_turns, dispatch_unpriced_tokens, dispatch_stale_models,
                        ) = _dispatch_usage_summary(
                            paired_jsonl, dollar_since_ts, dollar_until_ts, reprice_as, today
                        )
                        total_unpriced_turns += dispatch_unpriced_turns
                        total_unpriced_tokens += dispatch_unpriced_tokens
                        all_stale_models |= dispatch_stale_models
                        if observed is None:
                            row["dangling"] += 1
                        else:
                            row["runs"] += 1
                            row["requested"][requested_model or _UNREQUESTED_MODEL_LABEL] += 1
                            row["observed"][observed] += 1
                            row["actual_dollars"] += actual_dollars
                            if reprice_as:
                                row["counterfactual_dollars"] += counterfactual_dollars or 0.0
                elif name == "Skill":
                    skill = inp.get("skill") or ""
                    if skill in review_rounds.REVIEW_SKILLS:
                        session_data[branch]["skills"][skill] += 1

        for branch, sd in session_data.items():
            key = (root_idx, branch, jsonl.stem[:8] if per_session else None)
            d = data[key]
            d["sessions"] += 1
            for stype, cnt in sd["spawns"].items():
                d["spawns"][stype] += cnt
            for skill, cnt in sd["skills"].items():
                d["skills"][skill] += cnt

    if not data:
        print("No data found.")
        return

    def _mix_branch_label(key: tuple[int | None, str, str | None]) -> str:
        root_idx, branch, session_suffix = key
        label = (
            redaction._root_scoped_display_label(
                "branch", redact_ordinals[resolved_roots[root_idx]], branch, branch_redact_map,
                disclose=this_repo,
            )
            if root_idx is not None
            else render._sanitize_table_cell(branch)
        )
        return f"{label} [{session_suffix}]" if session_suffix is not None else label

    def _stype_label(key: tuple[int | None, str]) -> str:
        root_idx, stype = key
        return (
            redaction._root_scoped_display_label(
                "agent-type", redact_ordinals[resolved_roots[root_idx]], stype, subagent_type_redact_map,
                disclose=this_repo and stype in redaction._repo_tracked_agent_type_names(),
            )
            if root_idx is not None
            else render._sanitize_table_cell(stype)
        )

    print(f"{'Branch':<45} {'Sess':>5} {'Spawns':>7} {'CR':>3} {'PR':>3} {'RR':>3}  Top subagent types")
    print("-" * 120)
    for key in sorted(data):
        d = data[key]
        root_idx = key[0]
        branch_label = _mix_branch_label(key)
        spawns_total = sum(d["spawns"].values())
        top = sorted(d["spawns"].items(), key=lambda kv: (-kv[1], kv[0]))
        top_str = ", ".join(f"{_stype_label((root_idx, t))}({n})" for t, n in top[:5]) or "—"
        print(
            f"{branch_label:<45} {d['sessions']:>5} {spawns_total:>7} "
            f"{d['skills'].get('code-review', 0):>3} {d['skills'].get('plan-review', 0):>3} "
            f"{d['skills'].get('ready-for-review', 0):>3}  {top_str}"
        )

    if model_mix:
        header = f"{'AgentType':<28} {'Runs':>5} {'Dangling':>9}  {'Declared':<10} {'Actual$':>12}"
        if reprice_as:
            header += f" {'Counterfactual$':>18} {'Delta':>12}"
        header += f" {'Requested':<30} Observed"
        print(f"\n{header}")
        print("-" * len(header))
        for mix_key in sorted(model_mix):
            row = model_mix[mix_key]
            stype_label = _stype_label(mix_key)
            declared = "/".join(sorted(row["declared_seen"])) or _DECLARED_PIN_BUILT_IN
            requested_str = ", ".join(
                f"{k}({v})" for k, v in sorted(row["requested"].items(), key=lambda kv: (-kv[1], kv[0]))
            ) or "—"
            observed_str = ", ".join(
                f"{k}({v})" for k, v in sorted(row["observed"].items(), key=lambda kv: (-kv[1], kv[0]))
            ) or "—"
            line = (
                f"{stype_label:<28} {row['runs']:>5} {row['dangling']:>9}  {declared:<10} "
                f"{render._fmt_usd(row['actual_dollars']):>12}"
            )
            if reprice_as:
                delta = row["actual_dollars"] - row["counterfactual_dollars"]
                line += f" {render._fmt_usd(row['counterfactual_dollars']):>18} {render._fmt_usd(delta):>12}"
            line += f" {requested_str:<30} {observed_str}"
            print(line)
        # Matches cost's own "(N unpriced turns / M tokens excluded from
        # priced spend)" convention verbatim -- an unknown model ID would
        # otherwise silently read as a genuinely zero-cost dispatch.
        if total_unpriced_turns:
            print(
                f"  ({total_unpriced_turns:,} unpriced turns / {total_unpriced_tokens:,}"
                " tokens excluded from priced spend)"
            )
        # Matches cost's own STALE PRICING banner (_MODEL_RATE_EXPIRES),
        # simplified to the model list -- this table has no single "the
        # figures below" scope to point a successor-rate hint at.
        if all_stale_models:
            print(
                "STALE PRICING — today is past the re-verify-by date for: "
                + ", ".join(sorted(all_stale_models))
                + f". Re-check rates at {pricing._PRICING_SOURCE_URL} before publishing this table's dollar figures."
            )
    # Printed even when model_mix is empty (every dispatch's meta.json was
    # malformed) -- mirrors cmd_reviewer_yield's identical diagnostic, which
    # prints on its own early-return path for the same reason.
    if total_meta_read_errors:
        print(f"\n  ({total_meta_read_errors:,} meta.json files failed to parse, excluded)")


def _spawn_counts_by_agent_type(
    session_iter, branch_filter: set[str] | None,
) -> dict[str, int]:
    """Raw (undisclosed) subagent_type spawn counts across session_iter,
    for cost-counts.

    Main-thread dispatches only: excludes isSidechain records before ever
    reading gitBranch, matching cmd_subagent_mix's own exclusion order. So a
    subagent's own gitBranch never reaches this count. branch_filter matches
    a record's own literal gitBranch, not review_rounds' carry-forward
    attribution. For a main-thread record the two agree in every case that
    matters here, since cost._attributed_branch's own carry-forward logic
    exists only to resolve a worktree-agent-* sidechain record.

    No disclosure gate applied here -- see _partition_spawn_counts_by_disclosure
    for the allowlist partition this raw count feeds.
    """
    counts: dict[str, int] = defaultdict(int)
    for _jsonl, records in session_iter:
        for rec in records:
            if rec.get("type") != "assistant" or bool(rec.get("isSidechain")):
                continue
            branch = rec.get("gitBranch") or ""
            if branch_filter is not None and branch not in branch_filter:
                continue
            for block in (rec.get("message") or {}).get("content") or []:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                if block.get("name") not in pricing._SPAWN_TOOL_NAMES:
                    continue
                stype = (block.get("input") or {}).get("subagent_type") or _UNKNOWN_SUBAGENT_TYPE
                counts[stype] += 1
    return dict(counts)


_AGENT_FRONTMATTER_MODEL_RE = re.compile(r"(?m)^model:\s*(\S+)\s*$")


def _agent_frontmatter_model(agent_file_text: str) -> str | None:
    """Extract the `model:` frontmatter value from one agent file's raw text.

    Scoped to the leading YAML block (between the first pair of `---` lines)
    so a `model:` mention in the agent's prose body is never matched. Returns
    None when the text has no frontmatter block or the block has no `model:`
    key — the caller renders that as "built-in".
    """
    if not agent_file_text.startswith("---"):
        return None
    end = agent_file_text.find("\n---", 3)
    if end == -1:
        return None
    match = _AGENT_FRONTMATTER_MODEL_RE.search(agent_file_text[3:end])
    return match.group(1) if match else None


_DECLARED_PIN_BUILT_IN = "built-in"

# Fallback subagent_type for a spawn tool_use whose input carries no
# subagent_type field -- shared between cmd_subagent_mix and
# _spawn_counts_by_agent_type so the two never disagree on which raw string
# means "input missing this field."
_UNKNOWN_SUBAGENT_TYPE = "unknown"

# subagent_type values are harness-generated identifiers (e.g. "staff-sdet",
# "general-purpose") -- never containing "/" or "..". _declared_pin enforces
# this shape before building a filesystem path from one, since under
# --config-dir that value can originate from a scanned foreign root's own
# transcript data, not just this process's own dispatches.
_AGENT_TYPE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _declared_pin(
    agent_type: str, agents_dir: Path, declared_pin_cache: dict[tuple[Path, str], str]
) -> str:
    """Declared model pin for one subagent_type, from agents_dir/<agent_type>.md
    frontmatter — agents_dir is the *dispatch's own* root's agents/ directory
    (not necessarily this process's own config_dir()), since under
    --config-dir a dispatch's declared pin must be read from the account it
    actually came from. Cached per (agents_dir, agent_type), since the same
    agent_type name can resolve to a different on-disk file under a
    different root. "built-in" when no on-disk agent file exists (see
    _BUILT_IN_AGENT_TYPES — none of those three carry one), the file has no
    `model:` frontmatter — Claude Code's own default, not a pin this repo
    can assert on — or agent_type fails the on-disk agent-file naming
    allowlist (agent_type is transcript-sourced data; without this guard, an
    absolute-path or `../`-laden value would build a path outside
    agents_dir via Path.__truediv__'s os.path.join semantics).
    """
    key = (agents_dir, agent_type)
    if key in declared_pin_cache:
        return declared_pin_cache[key]
    if not _AGENT_TYPE_NAME_RE.fullmatch(agent_type):
        pin = _DECLARED_PIN_BUILT_IN
    else:
        agent_file = agents_dir / f"{agent_type}.md"
        try:
            text = agent_file.read_text()
        except OSError:
            pin = _DECLARED_PIN_BUILT_IN
        else:
            pin = _agent_frontmatter_model(text) or _DECLARED_PIN_BUILT_IN
    declared_pin_cache[key] = pin
    return pin


# The one folded row a subagent_type this repo's own agents/ tree does not
# track (and is not a Claude Code built-in) collapses into -- an em dash,
# not a hyphen, and parenthesized so it reads unambiguously as a caption,
# never as an agent name.
_WITHHELD_AGENT_TYPE_LABEL = "(withheld — untracked agent type)"


def _partition_spawn_counts_by_disclosure(raw_counts: dict[str, int]) -> list[tuple[str, int]]:
    """Partition raw subagent_type spawn counts into disclosed rows plus one
    folded withheld row, for cost-counts.

    The _repo_tracked_agent_type_names() allowlist gate is applied here
    unconditionally, unlike _stype_label's `disclose=this_repo and stype in
    _repo_tracked_agent_type_names()` (reached only under multi-root):
    _repo_tracked_agent_type_names() resolves its allowlist from
    _REPO_AGENT_DEFINITIONS_DIR, this installed toolkit's own agents/ tree --
    never a consumer repo's own tracked agents/ directory (see that
    function's own docstring). So a stow consumer's own real,
    project-tracked agent types are exactly as unresolvable here as a
    genuinely ad hoc dispatch. Both fold into the same withheld row
    regardless of whose branch cost-counts is scoring.

    Disclosed rows sort by (-count, name), matching cmd_subagent_mix's own
    `top` ordering. The withheld row -- when its folded total is nonzero --
    is always appended last, never merged into that sort, so its count can
    never place it ahead of a named row and be mistaken for one.
    """
    tracked = redaction._repo_tracked_agent_type_names()
    disclosed = sorted(
        ((stype, count) for stype, count in raw_counts.items() if stype in tracked),
        key=lambda kv: (-kv[1], kv[0]),
    )
    withheld_total = sum(count for stype, count in raw_counts.items() if stype not in tracked)
    rows = list(disclosed)
    if withheld_total:
        rows.append((_WITHHELD_AGENT_TYPE_LABEL, withheld_total))
    return rows


_COST_COUNTS_ROUNDS_CAPTION = (
    "Each invocation of a review skill is one round, whether or not it produced findings."
    " Counts reflect this section's last render; a review round that ran afterward may not"
    " be included yet."
)

_COST_COUNTS_SPAWNS_CAPTION = (
    "Counts main-thread dispatches only; an agent spawned from inside another agent is not"
    " counted."
)


def cmd_cost_counts(args: argparse.Namespace) -> None:
    """Per-branch review-round and subagent-spawn counts, for embedding in a
    public PR body -- counts only, no dollar attribution anywhere.

    Requires --this-repo and --branches; always resolves to a single root,
    `[config_dir() / "projects"]`, the active account alone. Prints exactly
    two GFM subsections, `### Review rounds` and `### Subagent spawns`, and
    nothing else. See docs/transcript-analysis.md's `cost-counts` section
    for the branch-attribution-model distinction, the rounds/spawns
    zero-count rendering asymmetry, and the disclosure-allowlist assertion
    backstop.
    """
    this_repo = bool(getattr(args, "this_repo", False))
    projects_arg = getattr(args, "projects", None)
    if not this_repo or projects_arg not in (None, "*"):
        print(
            "cost-counts: requires --this-repo and refuses any --projects scope"
            " (including the default glob) — see docs/transcript-analysis.md",
            file=sys.stderr,
        )
        sys.exit(2)

    branches_arg: str | None = getattr(args, "branches", None) or None
    if not branches_arg:
        print(
            "cost-counts: --branches is required — a corpus-wide count is never a"
            " legitimate PR-body figure",
            file=sys.stderr,
        )
        sys.exit(2)
    branch_filter = scope._branch_filter(args)

    roots = [scope.config_dir() / "projects"]

    session_iter, _scope_label = scope._resolve_project_scope(args, "cost-counts", roots=roots)
    # Fully materialized (unlike cmd_subagent_mix, which streams one session at a
    # time): cost-counts is --this-repo-only, bounding this to one account's one-repo
    # session history, small enough to hold in memory at once.
    sessions = list(session_iter)

    round_counts = review_rounds.compute_review_round_counts(sessions, branch_filter=branch_filter)
    spawn_rows = _partition_spawn_counts_by_disclosure(
        _spawn_counts_by_agent_type(sessions, branch_filter)
    )

    print("### Review rounds\n")
    print(f"{_COST_COUNTS_ROUNDS_CAPTION}\n")
    print("| Skill | Rounds |")
    print("|---|---|")
    total_rounds = 0
    for skill in review_rounds.REVIEW_SKILLS:
        n = round_counts[skill]
        total_rounds += n
        print(f"| {render._sanitize_table_cell(skill)} | {n} |")
    print(f"| **total** | **{total_rounds}** |")

    print("\n### Subagent spawns\n")
    print(f"{_COST_COUNTS_SPAWNS_CAPTION}\n")
    if not spawn_rows:
        print("No subagent spawns found in scope.")
    else:
        tracked = redaction._repo_tracked_agent_type_names()
        print("| Agent type | Spawns |")
        print("|---|---|")
        total_spawns = 0
        for row_index, (label, count) in enumerate(spawn_rows):
            if not (label == _WITHHELD_AGENT_TYPE_LABEL or label in tracked):
                raise AssertionError(
                    f"cost-counts: spawn row {row_index} is neither the withheld label nor a"
                    " repo-tracked agent type. Refusing to print the value; it is withheld"
                    " from this message by design."
                )
            total_spawns += count
            print(f"| {render._sanitize_table_cell(label)} | {count} |")
        print(f"| **total** | **{total_spawns}** |")


def _dispatch_usage_summary(
    jsonl_path: Path,
    since_ts: float | None,
    until_ts: float | None,
    reprice_as: str | None,
    today: date,
) -> tuple[str | None, float, dict[str, float], float | None, int, int, set[str]]:
    """Modal observed-model family plus priced dollar totals for one subagent
    dispatch's own transcript.

    Reads every assistant record's message.model in jsonl_path. Two or more
    distinct real (non-"<synthetic>") model IDs report the literal bucket
    "mixed", never collapsed into one family — an unstable dispatch should be
    visible, not silently assigned one of its models. A single distinct real
    model ID (regardless of how many turns used it) resolves via _fam. No
    real model ID at all (only "<synthetic>" turns) resolves via
    _fam("<synthetic>") -> "other". This bucket is computed over every
    assistant record in the file, regardless of since_ts/until_ts — a
    dispatch's model identity isn't scoped to a reporting window.

    actual_dollars/dollars_by_class price (via _price_turn) only the deduped
    turns whose first-block timestamp falls in [since_ts, until_ts). Per
    _merge_assistant_run's convention, a merged turn takes run[0]'s
    timestamp. The filter is per turn, not by the dispatch's own start time,
    since a dispatch's sidechain can straddle a window edge and a
    start-time-only filter would attribute post-cutoff spend to an
    "in-window" total.
    counterfactual_dollars re-prices that same in-window usage at
    reprice_as, or is None when reprice_as is not given.

    unpriced_turns/unpriced_tokens count in-window deduped turns _price_turn
    couldn't price (unknown model ID) — matches cost's own convention of
    surfacing this rather than letting it silently read as zero-cost spend.
    stale_models collects any priced model past its _MODEL_RATE_EXPIRES
    re-verify-by date, evaluated against the caller-supplied today (never
    read from the wall clock here, so a caller can hold this deterministic
    for tests) — mirrors cost's own staleness check.

    Records are buffered and passed through dedup_turns_by_request_id before
    pricing, since one API call writes one JSONL record per content block, all
    sharing one requestId, and pricing each block separately would overcount
    every token class. This is the same dedup step cost.py's pricing paths use.

    Returns (observed_bucket, actual_dollars, dollars_by_class,
    counterfactual_dollars, unpriced_turns, unpriced_tokens, stale_models).
    observed_bucket is None, and every other value is 0/0.0/{}/None/empty,
    when jsonl_path doesn't exist or can't be read — the caller's own
    "dangling meta.json" exclusion path (a run requires a readable sibling
    .jsonl, not just a valid meta.json).
    """
    if not jsonl_path.is_file():
        return None, 0.0, {}, None, 0, 0, set()
    real_model_ids: set[str] = set()
    saw_any_model = False
    dollars_by_class: dict[str, float] = defaultdict(float)
    counterfactual_total = 0.0
    unpriced_turns = 0
    unpriced_tokens = 0
    stale_models: set[str] = set()
    records: list[dict] = []
    try:
        with open(jsonl_path) as fh:
            for raw in fh:
                try:
                    records.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return None, 0.0, {}, None, 0, 0, set()

    for rec in pricing.dedup_turns_by_request_id(records):
        if rec.get("type") != "assistant":
            continue
        msg = rec.get("message") or {}
        model = msg.get("model")
        if not model:
            continue
        saw_any_model = True
        if model != "<synthetic>":
            real_model_ids.add(model)

        usage = msg.get("usage")
        if not usage:
            continue
        if since_ts is not None or until_ts is not None:
            rec_ts = corpus._parse_ts(rec.get("timestamp"))
            if rec_ts is None:
                continue
            if since_ts is not None and rec_ts < since_ts:
                continue
            if until_ts is not None and rec_ts >= until_ts:
                continue
        turn_dollars, _ctx, turn_unpriced_tokens = pricing._price_turn(model, usage)
        if turn_dollars is None:
            unpriced_turns += 1
            unpriced_tokens += turn_unpriced_tokens
        else:
            for cls, amount in turn_dollars.items():
                dollars_by_class[cls] += amount
            if today > pricing._MODEL_RATE_EXPIRES[model]:
                stale_models.add(model)
        if reprice_as:
            cf_dollars, _cf_ctx, _cf_unpriced = pricing._price_turn(reprice_as, usage)
            if cf_dollars is not None:
                counterfactual_total += sum(cf_dollars.values())

    if len(real_model_ids) >= 2:
        observed = "mixed"
    elif real_model_ids:
        observed = render._fam(next(iter(real_model_ids)))
    else:
        observed = render._fam("<synthetic>") if saw_any_model else "other"

    actual_dollars = sum(dollars_by_class.values())
    counterfactual_dollars = counterfactual_total if reprice_as else None
    return (
        observed, actual_dollars, dict(dollars_by_class), counterfactual_dollars,
        unpriced_turns, unpriced_tokens, stale_models,
    )
