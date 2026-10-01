"""The review-trace command family: cmd_review_trace and every helper used
only by it -- --deny-summary's grouped denial/friction accumulation and
report, and the per-session event-timeline detector shared by both.

Imports corpus, denials, render, reviewer_yield, and scope by module
(attribute access, not by name) -- see scope.py's own top-of-file comment
for why. Calls reviewer_yield._is_reviewer_subagent_type and
review_rounds._round_skill_name -- the package's first two imports from one
command-group module into another.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path

from transcript_analysis import corpus, denials, render, review_rounds, reviewer_yield, scope

# Skills counted as review invocations in review-trace.
REVIEW_TRACE_SKILLS: frozenset[str] = frozenset(
    {"code-review", "plan-review", "ready-for-review", "skill-review", "agent-review", "plan-it"}
)

# A plan-architect Agent/Task dispatch whose prompt's first line is anything
# other than this literal is a consult. This re-expresses
# log-reviewer-round.sh's _maybe_write_consult_latch in a second runtime —
# see docs/design-decisions.md §48 for the cross-runtime duplication rationale.
_ARCHITECT_CONSULT_SUBAGENT_TYPE = "plan-architect"
_ARCHITECT_CONSULT_PLAN_SECTIONS_MODE_LINE = "MODE=plan-sections"

# Shared by review-trace's two zero-match termini (default timeline and
# --deny-summary) so both read identically under the scope header.
_REVIEW_TRACE_NO_SESSIONS_MSG = "No sessions matched in scope."


def _normalize_skill_name(raw: str) -> str:
    """Strip a directory qualifier from a transcript skill name, keeping any
    plugin:/dir: prefix as a display label.

    Mirrors transcript-analysis.py's own _normalize_skill_name -- re-expressed
    here since the package may not import back from the shim. Used only for
    the emitted "skill" event field; REVIEW_TRACE_SKILLS membership and
    --skill filtering use review_rounds._round_skill_name's fully-bare strip
    instead.
    """
    return raw.rsplit("/", 1)[-1]


def _print_deny_summary(
    hook_counts: dict[str, int],
    command_shape_counts: dict[str, int],
    hook_shape_counts: Counter[tuple[str, str]],
    hook_cause_counts: Counter[tuple[str, str]],
    friction_counts: dict[str, int],
    pre_regime_tool_result_count: int,
    corpus_min_ts: float | None,
    corpus_max_ts: float | None,
) -> None:
    """Print --deny-summary's grouped denial-count tables plus the friction breakout.

    hook_shape_counts cross-tabs the hook/gate axis against the command-shape
    axis — the two marginal tables alone can't say which hook denied which
    command shape, which is the whole point of the census this feeds.
    hook_cause_counts cross-tabs the same hook/gate axis against the
    orthogonal denial-cause axis (denials._DENIAL_CAUSE_KINDS).
    """
    if corpus_min_ts is not None and corpus_max_ts is not None:
        print(f"\nCorpus window: {render._fmt_date(corpus_min_ts)} to {render._fmt_date(corpus_max_ts)}")

    total = sum(hook_counts.values())
    print(f"\n## Denials by hook/gate ({total} total)\n")
    print(f"{'Hook/gate':<40} {'Count':>6}")
    print("-" * 47)
    for label, count in sorted(hook_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"{render._sanitize_table_cell(label):<40} {count:>6}")

    print(f"\n## Denials by attempted command shape ({total} total)\n")
    print(f"{'Shape':<16} {'Count':>6}")
    print("-" * 23)
    for label, count in sorted(command_shape_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"{render._sanitize_table_cell(label):<16} {count:>6}")

    # Column set is the observed shapes only (already restricted to A3's
    # classifier output plus "other"), not a fixed enumeration — the
    # multiplexer+subcommand shape space is open-ended by construction.
    # Skipped entirely (rather than rendering a header-only, zero-row table)
    # when scope has zero denials — a friction-only report has nothing to
    # cross-tab.
    if hook_counts or command_shape_counts:
        shapes = sorted(command_shape_counts.keys())
        hooks = sorted(hook_counts.keys())
        col_width = max((len(s) for s in shapes), default=5) + 2
        # Rows/header are indented two spaces — unlike the marginal tables above,
        # deliberately, so a hook-label row here never collides with a
        # column-0 row-label match against the hook/gate marginal table.
        print(f"\n## Denials by hook/gate x command shape ({total} total)\n")
        header = f"  {'Hook':<40}" + "".join(
            f"{render._sanitize_table_cell(shape):>{col_width}}" for shape in shapes
        )
        print(header)
        print("  " + "-" * (len(header) - 2))
        for hook in hooks:
            row = f"  {render._sanitize_table_cell(hook):<40}" + "".join(
                f"{hook_shape_counts.get((hook, shape), 0):>{col_width}}" for shape in shapes
            )
            print(row)

    # Column set is the fixed denials._DENIAL_CAUSE_KINDS enumeration rather than
    # sorted-observed — a zero in the lib-source column is itself the
    # signal, so a fixed column set keeps two runs comparable. Row order
    # matches the hook/gate marginal table above.
    if hook_counts:
        cause_col_width = max((len(k) for k in denials._DENIAL_CAUSE_KINDS), default=5) + 2
        print(f"\n## Denials by hook/gate x cause ({total} total)\n")
        header = f"  {'Hook':<40}" + "".join(
            f"{render._sanitize_table_cell(kind):>{cause_col_width}}" for kind in denials._DENIAL_CAUSE_KINDS
        )
        print(header)
        print("  " + "-" * (len(header) - 2))
        for hook, _count in sorted(hook_counts.items(), key=lambda kv: (-kv[1], kv[0])):
            row = f"  {render._sanitize_table_cell(hook):<40}" + "".join(
                f"{hook_cause_counts.get((hook, kind), 0):>{cause_col_width}}" for kind in denials._DENIAL_CAUSE_KINDS
            )
            print(row)

    friction_total = sum(friction_counts.values())
    print(f"\n## Friction events by kind ({friction_total} total)\n")
    print(f"{'Kind':<24} {'Count':>6}")
    print("-" * 31)
    for label, count in sorted(friction_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"{render._sanitize_table_cell(label):<24} {count:>6}")
    print(
        f"\n{pre_regime_tool_result_count} errored, non-gate tool result(s) predate the"
        f" per-record denial-kind field's introduction ({denials._TOOL_DENIAL_KIND_REGIME_START})"
        " and are excluded from the breakdown above — kind is structurally unmeasurable"
        " before that date, not zero."
    )


def _is_architect_consult_dispatch(tool_input: dict) -> bool:
    """True for a plan-architect Agent/Task dispatch whose prompt is a
    consult rather than a MODE=plan-sections call — fail-safe toward
    consult, so a missing `prompt` key or an empty first line both classify
    as a consult. See docs/design-decisions.md §48 for why this duplicates
    log-reviewer-round.sh's _maybe_write_consult_latch instead of sharing it."""
    prompt = tool_input.get("prompt") or ""
    first_line = prompt.split("\n", 1)[0]
    return first_line != _ARCHITECT_CONSULT_PLAN_SECTIONS_MODE_LINE


def _group_start_indices(groups: list[list[dict]]) -> frozenset[int]:
    """0-based positions in the flattened record list where a new source
    group (the main transcript, or one subagent file) begins.

    Matches read_session_file's own flatten order — the main transcript's
    records first, then each subagent file's in filename-sorted order — so a
    caller that already has the flat, session_iter-yielded records can align
    this function's output against them by plain list index. See
    _review_trace_session_events's group_boundaries parameter for why the
    boundary matters."""
    starts = []
    cursor = 0
    for group in groups:
        starts.append(cursor)
        cursor += len(group)
    return frozenset(starts)


def _review_trace_session_events(
    records: list[dict],
    since_ts: float | None,
    until_epoch: float | None,
    branch_filter: set[str] | None,
    skill_filter: str | None = None,
    group_boundaries: frozenset[int] | None = None,
) -> tuple[list[dict], dict[str, str], int]:
    """Detect cmd_review_trace's five per-session event kinds (skill, denial,
    friction, reviewer-spawn, architect-consult) from one session's records.

    Signal 1 (skill invocations) detects both invocation shapes: a `Skill`
    tool_use block on an assistant record, and a `/slash`-command
    <command-name> tag on a user record. This mirrors _round_open_skill's
    (review_rounds.py) own two-shape detection for review-round-cost.

    Shared by cmd_review_trace's timeline printer and compute_deny_summary_data
    so the denial/friction detection and dedup rules exist in one place rather
    than two copies kept in sync by hand. The third return value, a count of
    errored tool results predating toolDenialKind's introduction, is always
    computed (cheap) even though only --deny-summary reports it — see
    _print_deny_summary's own explanation of what it means.

    records may interleave main-thread and subagent (isSidechain) records
    when the caller resolved scope with include_subagents=True. Detection
    runs over those records merged into one chronological stream, sorted on
    a three-part key: effective_ts (the record's own corpus._parse_ts result,
    forward-filled from the immediately preceding record when unparseable,
    or float("-inf") at the start of a group), thread_rank (0 for a
    main-thread record, 1 for a sidechain one), and pre_sort_index (the
    record's original position, the final tie-break). group_boundaries (the
    0-based records indices where a new source file starts, from
    _group_start_indices; None from a caller that hasn't partitioned the
    corpus read, meaning the whole list is treated as one group) resets
    effective_ts's forward-fill at each boundary. See
    docs/design-decisions.md §58 for why the sort key and the per-group
    reset are shaped this way.

    effective_ts governs ordering only — the --since/--until filter below
    still tests each record's own, unfilled corpus._parse_ts result. Each event
    dict carries this same pre_sort_index as line_no, plus a thread field
    ("main" or "sidechain"): the main transcript's own 1-based file line for
    thread=main, a merged-stream offset indexing no file for thread=sidechain
    (see docs/design-decisions.md §58 for why).
    """
    events: list[dict] = []  # ordered, tagged with type/ts/line_no/branch/model
    # Tracks tool_use_ids already emitted as a denial. A legacy denial
    # appears as both an attachment record and an is_error tool_result
    # sharing one tool_use_id; this set collapses the pair to one event.
    seen_denial_ids: set[str] = set()

    # Friction events dedup against their own set, never seen_denial_ids
    # above — sharing it would let a friction event suppress a later
    # legitimate denial sharing a tool_use_id.
    seen_friction_ids: set[str] = set()

    # tool_use_id -> attempted command, for --deny-summary's by-command-shape
    # grouping. Indexed from every assistant tool_use block, main-thread or
    # sidechain — this loop carries no isSidechain guard of its own, so a
    # denial raised inside a subagent's own transcript still resolves to the
    # command that triggered it. Independent of the --since/--until window,
    # since a denial's own event already applies it.
    tool_use_commands: dict[str, str] = {}

    # Carry-forward trackers, updated on every main-thread record, never a
    # sidechain one. Detection itself no longer gates on isSidechain, but a
    # sidechain event still inherits whatever branch/model was live on the
    # dispatching main-thread record, not its own. Applied before the date
    # filter below, so the branch/model attributed to an event is whatever a
    # prior main-thread record last set, including one outside the
    # --since/--until window.
    last_branch = ""
    last_model = ""
    pre_regime_tool_result_count = 0

    # Merge main-thread and subagent records into one chronological stream
    # before detection (see the docstring's three-part key) — sorting
    # instead of leaving records in main-then-subagent concatenation order is
    # what lets the carry-forward trackers above see a sidechain event's
    # dispatching record before the event itself, rather than after every
    # main-thread record has already run.
    merged: list[tuple[float, int, int, dict]] = []
    prev_effective_ts = float("-inf")
    for index, rec in enumerate(records):
        if group_boundaries is not None and index in group_boundaries:
            prev_effective_ts = float("-inf")
        ts = corpus._parse_ts(rec.get("timestamp"))
        effective_ts = prev_effective_ts if ts is None else ts
        prev_effective_ts = effective_ts
        thread_rank = 1 if bool(rec.get("isSidechain")) else 0
        merged.append((effective_ts, thread_rank, index + 1, rec))
    merged.sort(key=lambda item: item[:3])

    for _effective_ts, _thread_rank, line_no, rec in merged:
        thread = "sidechain" if bool(rec.get("isSidechain")) else "main"

        if not bool(rec.get("isSidechain")):
            b = rec.get("gitBranch") or ""
            if b:
                last_branch = b
            if rec.get("type") == "assistant":
                m = (rec.get("message") or {}).get("model") or ""
                if m:
                    last_model = m

        if rec.get("type") == "assistant":
            for block in ((rec.get("message") or {}).get("content") or []):
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                tid = block.get("id")
                if tid:
                    tool_use_commands[tid] = (block.get("input") or {}).get("command", "")

        rec_ts_str: str | None = rec.get("timestamp")
        rec_ts: float | None = corpus._parse_ts(rec_ts_str)

        # Apply date filter: records with no parseable timestamp are excluded when
        # a date boundary is active.
        if (since_ts is not None or until_epoch is not None):
            if rec_ts is None:
                continue
            if since_ts is not None and rec_ts < since_ts:
                continue
            if until_epoch is not None and rec_ts >= until_epoch:
                continue

        rec_type = rec.get("type", "")
        evt_branch = last_branch or "?"
        evt_model = render._fam(last_model) if last_model else "?"

        # --- Signals 1 + 3: skill invocations and reviewer-agent spawns ---
        # Both are assistant tool_use blocks, main-thread or sidechain; a
        # single pass over content dispatches on tool name to avoid
        # iterating the list twice.
        if rec_type == "assistant":
            for block in ((rec.get("message") or {}).get("content") or []):
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                block_name = block.get("name")
                if block_name == "Skill":
                    raw_skill_name = (block.get("input") or {}).get("skill") or ""
                    matched_skill_name = review_rounds._round_skill_name(raw_skill_name)
                    if matched_skill_name not in REVIEW_TRACE_SKILLS:
                        continue
                    if skill_filter and matched_skill_name != skill_filter:
                        continue
                    events.append({
                        "kind": "skill",
                        "skill": _normalize_skill_name(raw_skill_name),
                        "ts": rec_ts_str,
                        "line_no": line_no,
                        "branch": evt_branch,
                        "model": evt_model,
                        "thread": thread,
                    })
                elif block_name in ("Agent", "Task"):
                    tool_input = block.get("input") or {}
                    stype = tool_input.get("subagent_type") or ""
                    if reviewer_yield._is_reviewer_subagent_type(stype):
                        events.append({
                            "kind": "reviewer-spawn",
                            "subagent_type": stype,
                            "ts": rec_ts_str,
                            "line_no": line_no,
                            "branch": evt_branch,
                            "model": evt_model,
                            "thread": thread,
                        })
                    elif stype == _ARCHITECT_CONSULT_SUBAGENT_TYPE and _is_architect_consult_dispatch(tool_input):
                        # A consult dispatch was initiated -- no dependence on
                        # a tool_result, unlike log-reviewer-round.sh's
                        # PostToolUse latch. The prompt itself never lands on
                        # the event dict, only this classification result.
                        events.append({
                            "kind": "architect-consult",
                            "ts": rec_ts_str,
                            "line_no": line_no,
                            "branch": evt_branch,
                            "model": evt_model,
                            "thread": thread,
                        })
        elif rec_type == "user":
            # --- Signal 1 continued: skill invocations, /slash shape ---
            # A /slash-invoked skill injects its body directly with no Skill
            # tool_use block, so it never reaches the assistant branch above.
            # Mirrors cmd_skill_invocation's own user-record branch
            # (transcript-analysis.py's own cmd_skill_invocation).
            content_raw = (rec.get("message") or {}).get("content", "")
            content_str = content_raw if isinstance(content_raw, str) else render._content_text(content_raw)
            for m in review_rounds._SLASH_COMMAND_RE.finditer(content_str):
                raw_skill_name = m.group(1)
                matched_skill_name = review_rounds._round_skill_name(raw_skill_name)
                if matched_skill_name not in REVIEW_TRACE_SKILLS:
                    continue
                if skill_filter and matched_skill_name != skill_filter:
                    continue
                events.append({
                    "kind": "skill",
                    "skill": _normalize_skill_name(raw_skill_name),
                    "ts": rec_ts_str,
                    "line_no": line_no,
                    "branch": evt_branch,
                    "model": evt_model,
                    "thread": thread,
                })

        # --- Signal 2a: hook denials, legacy shape (attachment record) ---
        if rec_type == "attachment":
            denial = denials.hook_denial_key(rec)
            if denial is None:
                continue
            tool_use_id, att = denial
            if tool_use_id and tool_use_id in seen_denial_ids:
                continue
            raw_error = att.get("blockingError")
            normalized = denials._normalize_blocking_error(raw_error)
            hook_name = att.get("hookName") or ""
            if isinstance(normalized, dict):
                # Real transcripts nest the human-readable text in a "blockingError"
                # key alongside a "command" key; fall back to "message" then repr.
                message = (
                    normalized.get("blockingError")
                    or normalized.get("message")
                    or str(normalized)
                )
            else:
                message = str(normalized) if normalized else ""
            if tool_use_id:
                seen_denial_ids.add(tool_use_id)
            events.append({
                "kind": "denial",
                "hook_name": hook_name,
                "tool_use_id": tool_use_id,
                "message": message,
                "ts": rec_ts_str,
                "line_no": line_no,
                "branch": evt_branch,
                "model": evt_model,
                "thread": thread,
            })

        # --- Signal 2b: hook denials, current shape (is_error tool_result) ---
        # Claude Code stopped emitting the hook_blocking_error attachment
        # record; current transcripts surface a denial only as an is_error
        # tool_result, identified by the hook-denial message signature.
        #
        # --- Signal 2c: non-gate friction, current shape (toolDenialKind) ---
        # toolDenialKind lives on this same `user` record, not on the
        # tool_result block — read once, but classification below still
        # requires the individual block's own is_error, since a parallel
        # tool call can carry an unrelated successful block alongside it.
        if rec_type == "user":
            tool_denial_kind = rec.get("toolDenialKind") or ""
            # A falsy tool_denial_kind this far before the regime start
            # means the field structurally could not exist yet, not that
            # this record measured zero friction. Scoped to the same
            # is_error-and-non-gate-signature population
            # denials._is_nongate_friction_kind would classify below, so the
            # count reflects records that could plausibly have been
            # friction, not every tool_result in the era (which would
            # count ordinary successful tool calls too) — tallied
            # separately and reported apart from the friction-kind
            # breakdown.
            pre_regime = (
                not tool_denial_kind
                and rec_ts is not None
                and denials._TOOL_DENIAL_KIND_REGIME_START_TS is not None
                and rec_ts < denials._TOOL_DENIAL_KIND_REGIME_START_TS
                and (not branch_filter or evt_branch in branch_filter)
            )
            for block in ((rec.get("message") or {}).get("content") or []):
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                denial = denials.hook_denial_key(block)
                already_gate_denied = denial is not None
                if denial is not None:
                    tool_use_id, message = denial
                    if not (tool_use_id and tool_use_id in seen_denial_ids):
                        if tool_use_id:
                            seen_denial_ids.add(tool_use_id)
                        events.append({
                            "kind": "denial",
                            "hook_name": "",
                            "tool_use_id": tool_use_id,
                            "message": message,
                            "ts": rec_ts_str,
                            "line_no": line_no,
                            "branch": evt_branch,
                            "model": evt_model,
                            "thread": thread,
                        })

                if block.get("is_error") and denials._is_nongate_friction_kind(tool_denial_kind, already_gate_denied):
                    friction_tool_use_id = block.get("tool_use_id") or ""
                    if not (friction_tool_use_id and friction_tool_use_id in seen_friction_ids):
                        if friction_tool_use_id:
                            seen_friction_ids.add(friction_tool_use_id)
                        events.append({
                            "kind": "friction",
                            "friction_kind": tool_denial_kind,
                            "tool_use_id": friction_tool_use_id,
                            "message": render._content_text(block.get("content")),
                            "ts": rec_ts_str,
                            "line_no": line_no,
                            "branch": evt_branch,
                            "model": evt_model,
                            "thread": thread,
                        })
                elif pre_regime and not already_gate_denied and block.get("is_error"):
                    pre_regime_tool_result_count += 1

    # Branch filtering happens after dedup (seen_denial_ids was populated
    # above over every event, unconditionally) so a duplicate-id denial on
    # a differently-branched record is suppressed, not re-emitted as a
    # distinct in-scope event.
    if branch_filter:
        events = [e for e in events if e["branch"] in branch_filter]

    return events, tool_use_commands, pre_regime_tool_result_count


def _fresh_records_and_group_boundaries(
    jsonl: Path, records: list[dict], *, include_subagents: bool,
) -> tuple[list[dict], frozenset[int] | None]:
    """Re-read jsonl as one corpus._read_session_file_partitioned call, so the
    records and group_boundaries handed to _review_trace_session_events
    always come from the same snapshot of the file -- pairing session_iter's
    own records (an earlier read) with an independently re-read
    group_boundaries would let the two desync whenever the file grows in
    between (e.g. review-trace scanning its own in-progress session).
    include_subagents is a required keyword argument, not a default, so a
    future caller must state its own session_iter's scope explicitly rather
    than silently inheriting whatever this function's last caller needed.

    Falls back to the given records with no group boundaries when jsonl is
    unreadable right now, rather than discarding a session's already-observed
    events. Two cases trigger the fallback: the file was deleted mid-scan, or
    the path is a synthetic one used in a test. Shared by
    compute_deny_summary_data and cmd_review_trace, this function's two
    identically-shaped callers.
    """
    groups = corpus._read_session_file_partitioned(jsonl, include_subagents=include_subagents)
    if not groups:
        return records, None
    return [rec for group in groups for rec in group], _group_start_indices(groups)


def compute_deny_summary_data(
    session_iter,
    since_ts: float | None = None,
    until_ts: float | None = None,
    branch_filter: set[str] | None = None,
    deny_only: bool = False,
) -> dict:
    """Corpus-wide --deny-summary accumulation, extracted so cost-ledger's
    per-week denial count and cmd_review_trace's own report share one pass
    over session_iter instead of two implementations kept in sync by hand.

    since_ts/until_ts are explicit epoch-second boundaries (until_ts
    exclusive, the same convention as cmd_review_trace's own until_epoch)
    rather than the CLI's date-string args, so a caller can pass exact week
    boundaries the CLI itself has no flag to reach.
    """
    hook_counts: dict[str, int] = defaultdict(int)
    command_shape_counts: dict[str, int] = defaultdict(int)
    hook_shape_counts: Counter[tuple[str, str]] = Counter()
    # No separate corpus-wide cause accumulator — the per-cause total is a
    # column sum of this cross-tab, and denials._DENIAL_CAUSE_KINDS is closed so
    # every column always prints.
    hook_cause_counts: Counter[tuple[str, str]] = Counter()
    friction_counts: dict[str, int] = defaultdict(int)
    corpus_min_ts: float | None = None
    corpus_max_ts: float | None = None
    pre_regime_tool_result_count = 0
    any_session_matched = False

    for jsonl, records in session_iter:
        # Both of this function's callers resolve scope with
        # include_subagents=True -- see _fresh_records_and_group_boundaries
        # for why records and group_boundaries must come from one read.
        records, group_boundaries = _fresh_records_and_group_boundaries(
            jsonl, records, include_subagents=True,
        )
        events, tool_use_commands, session_pre_regime = _review_trace_session_events(
            records, since_ts, until_ts, branch_filter, group_boundaries=group_boundaries
        )
        if not events:
            continue
        any_session_matched = True
        pre_regime_tool_result_count += session_pre_regime

        # Corpus window reads the full branch-filtered per-session events list
        # before the deny_only skip below, same as the friction tally below —
        # so the reported window matches whatever --branches/--since/--until
        # actually put in scope, not the pre-branch-filter raw record range.
        for evt in events:
            evt_ts = corpus._parse_ts(evt.get("ts"))
            if evt_ts is None:
                continue
            if corpus_min_ts is None or evt_ts < corpus_min_ts:
                corpus_min_ts = evt_ts
            if corpus_max_ts is None or evt_ts > corpus_max_ts:
                corpus_max_ts = evt_ts

        has_denial = any(e["kind"] == "denial" for e in events)

        # Friction tally reads the full per-session events list before the
        # deny_only skip below, so a friction-only session (has_denial False)
        # still contributes when --deny-only and --deny-summary run together.
        # deny_only's own session-selection stays denial-kind-only, unchanged.
        for evt in events:
            if evt["kind"] == "friction":
                friction_counts[denials._friction_kind_label(evt["friction_kind"])] += 1

        if deny_only and not has_denial:
            continue

        for evt in events:
            if evt["kind"] != "denial":
                continue
            hook_label = denials._denial_hook_label(evt["hook_name"], evt["message"])
            command = tool_use_commands.get(evt["tool_use_id"], "")
            command_shape = denials._denial_command_shape(command)
            cause_kind = denials._denial_cause_kind(evt["message"])
            hook_counts[hook_label] += 1
            command_shape_counts[command_shape] += 1
            hook_shape_counts[(hook_label, command_shape)] += 1
            hook_cause_counts[(hook_label, cause_kind)] += 1

    return {
        "hook_counts": hook_counts,
        "command_shape_counts": command_shape_counts,
        "hook_shape_counts": hook_shape_counts,
        "hook_cause_counts": hook_cause_counts,
        "friction_counts": friction_counts,
        "corpus_min_ts": corpus_min_ts,
        "corpus_max_ts": corpus_max_ts,
        "pre_regime_tool_result_count": pre_regime_tool_result_count,
        "any_session_matched": any_session_matched,
    }


def cmd_review_trace(args: argparse.Namespace) -> None:
    """Emit an ordered review-event timeline per session.

    Scans both the main thread and every dispatched subagent's own
    transcript file (include_subagents=True), merged into one chronological
    stream by _review_trace_session_events. Five event types are detected
    per session, on either thread:
    - skill: a Skill tool_use where input.skill is in REVIEW_TRACE_SKILLS
    - denial: a hook-blocking denial in either transcript shape — a legacy
      `attachment` record (type==hook_blocking_error) or a current-format
      `tool_result` block with is_error and a hook-denial message signature.
      A denial recorded as both shapes is collapsed to one event by tool_use_id.
    - friction: a current-format `user` record whose own toolDenialKind field
      marks non-gate friction (user-rejected, automode-blocked,
      automode-unavailable, interrupted) — see denials._is_nongate_friction_kind.
      Deduped by tool_use_id in its own set, independent of denial dedup.
    - reviewer: Agent/Task spawn where subagent_type is a reviewer type per
      reviewer_yield._is_reviewer_subagent_type
    - architect-consult: Agent/Task spawn where subagent_type is
      plan-architect and the prompt's first line is not the literal
      MODE=plan-sections, per _is_architect_consult_dispatch. Signals that a
      consult dispatch was initiated, not that it completed — including one
      dispatched from inside a subagent.

    denial and friction are deliberately separate event kinds: has_denial,
    denials=N, and --deny-only's session-selection all stay denial-kind-only,
    so a non-gate toolDenialKind value never broadens what those three
    surfaces report — only the default timeline and --deny-summary's own
    friction breakout render friction events.

    Branch and model are resolved per event from the record that produced it,
    not from the session's first record: each is the last non-empty value
    carried forward up to that point, so a session that moves from one branch
    (or model) to another attributes each event correctly instead of labelling
    every event with whatever the session started on. An event whose branch or
    model cannot be resolved renders '?'. --branches filters the emitted event
    list by this per-event value, not by a single session-wide branch. A
    sidechain event's thread field prints as `thread=sidechain` in the
    timeline; a main-thread event's `thread=main` is the default and stays
    unprinted. A sidechain event's line_no is a merged-stream offset, not a
    real file line (see _review_trace_session_events's docstring), so it
    prints as `line   n/a` instead of a numeral.

    --deny-summary delegates its entire accumulation to
    compute_deny_summary_data instead of running its own pass over
    session_iter, so the corpus-wide grouped-count report and cost-ledger's
    per-week denial count can never drift apart.
    """
    branch_filter = scope._branch_filter(args)
    deny_only: bool = bool(getattr(args, "deny_only", False))
    deny_summary: bool = bool(getattr(args, "deny_summary", False))
    skill_filter: str | None = getattr(args, "skill", None) or None
    roots = scope.resolve_scan_roots(args)
    session_iter, scope_label = scope._resolve_project_scope(args, "review-trace", include_subagents=True, roots=roots)

    since_ts, until_epoch = scope._parse_absolute_window_args(args, "review-trace")

    if deny_summary:
        # Ahead of the scan, matching the default arm below: a crash partway
        # through the corpus still leaves the scanned scope on stdout.
        scope.print_resolved_scope("review-trace", scope_label, roots)
        data = compute_deny_summary_data(
            session_iter, since_ts=since_ts, until_ts=until_epoch,
            branch_filter=branch_filter, deny_only=deny_only,
        )
        if sum(data["hook_counts"].values()) or sum(data["friction_counts"].values()):
            _print_deny_summary(
                data["hook_counts"], data["command_shape_counts"], data["hook_shape_counts"],
                data["hook_cause_counts"], data["friction_counts"], data["pre_regime_tool_result_count"],
                data["corpus_min_ts"], data["corpus_max_ts"],
            )
        elif data["any_session_matched"]:
            # Sessions matched but none carried a denial — distinct from the
            # scope matching no sessions at all, which the else covers.
            print("\nNo denials found in scope.")
        else:
            print(f"\n{_REVIEW_TRACE_NO_SESSIONS_MSG}")
        return

    # Printed before the scan, not on the first emitted block: a run matching
    # no session must still state the corpus it read, or a wrongly-scoped scan
    # is indistinguishable from a correctly-scoped empty one.
    scope.print_resolved_scope("review-trace", scope_label, roots)
    emitted_any_session = False

    for jsonl, records in session_iter:
        # session_iter is resolved with include_subagents=True above -- see
        # _fresh_records_and_group_boundaries for why records and
        # group_boundaries must come from one read at the same scope.
        records, group_boundaries = _fresh_records_and_group_boundaries(
            jsonl, records, include_subagents=True,
        )
        events, tool_use_commands, _pre_regime = _review_trace_session_events(
            records, since_ts, until_epoch, branch_filter,
            skill_filter=skill_filter, group_boundaries=group_boundaries,
        )
        if not events:
            continue

        has_denial = any(e["kind"] == "denial" for e in events)
        if deny_only and not has_denial:
            continue

        skill_count = sum(1 for e in events if e["kind"] == "skill")
        denial_count = sum(1 for e in events if e["kind"] == "denial")
        spawn_count = sum(1 for e in events if e["kind"] == "reviewer-spawn")
        consult_count = sum(1 for e in events if e["kind"] == "architect-consult")
        branches_seen = ",".join(sorted({e["branch"] for e in events}))
        models_seen = ",".join(sorted({e["model"] for e in events}))

        emitted_any_session = True

        print(f"\n### {jsonl}")
        print(
            f"branches={branches_seen}  models={models_seen}  skills={skill_count}"
            f"  denials={denial_count}  reviewer-spawns={spawn_count}"
            f"  architect-consults={consult_count}"
        )
        for evt in events:
            ts_label = evt.get("ts") or "?"
            # line_no is a real, seekable main-transcript line only for a
            # thread=main event; for thread=sidechain it's a merged-stream
            # offset indexing no file (see _review_trace_session_events's
            # docstring), so it prints as n/a rather than under the same
            # "line" label as a real one.
            lno = "n/a" if evt["thread"] == "sidechain" else evt["line_no"]
            kind = evt["kind"]
            thread_suffix = "" if evt["thread"] == "main" else f" thread={evt['thread']}"
            suffix = f"  (branch={evt['branch']} model={evt['model']}{thread_suffix})"
            if kind == "skill":
                print(f"  [{ts_label}] line {lno:>5}  skill        {evt['skill']}{suffix}")
            elif kind == "denial":
                hook = evt['hook_name']
                uid = evt['tool_use_id']
                msg = evt['message']
                cause = denials._denial_cause_kind(msg)
                print(
                    f"  [{ts_label}] line {lno:>5}  denial       hook={hook}  cause={cause}"
                    f"  id={uid}  msg={msg!r}{suffix}"
                )
            elif kind == "friction":
                fkind = denials._friction_kind_label(evt['friction_kind'])
                uid = evt['tool_use_id']
                msg = evt['message']
                print(f"  [{ts_label}] line {lno:>5}  friction     kind={fkind}  id={uid}  msg={msg!r}{suffix}")
            elif kind == "reviewer-spawn":
                print(f"  [{ts_label}] line {lno:>5}  reviewer     {evt['subagent_type']}{suffix}")
            elif kind == "architect-consult":
                print(f"  [{ts_label}] line {lno:>5}  consult      plan-architect{suffix}")

    if not emitted_any_session:
        print(f"\n{_REVIEW_TRACE_NO_SESSIONS_MSG}")


