"""The handoff-signal-response command: cmd_handoff_signal_response audits each
observed context-budget signal (the --check result, the advisory injection,
the hard-block stop) and whether a same-session /handoff followed it.
.claude/plans/handoff-nudge-rationalization-gap.md holds the design.

Imports corpus, cost, handoff_nudge, pricing, redaction, render, and scope by
module (attribute access, not by name) -- see scope.py's own top-of-file
comment for why.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import statistics
import sys
from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from transcript_analysis import corpus, cost, handoff_nudge, pricing, redaction, render, scope

# Named constants, not inline strings, so a signal's kind is never a
# copy-pasted literal. Read at three call sites: the OR-condition detector,
# the aggregate table, and the curation-card formatter.
_HANDOFF_SIGNAL_CHECK = "check"
_HANDOFF_SIGNAL_ADVISORY = "advisory"
_HANDOFF_SIGNAL_HARD_BLOCK = "hard-block"

# nudge-handoff-near-context-cap.sh's own script basename, matched
# post-basename since the real invocation is always a tilde or absolute path
# -- mirrors _DENIAL_COMMAND_MULTIPLEXERS' own convention for marker.sh.
_HANDOFF_SIGNAL_HOOK_BASENAME = "nudge-handoff-near-context-cap.sh"

# The hard-block stderr message's own stable substring
# (nudge-handoff-near-context-cap.sh's printf, around line 647), distinct
# from the advisory clause's own text so it can't cross-match.
_HANDOFF_SIGNAL_HARD_BLOCK_TEXT = "handoff-nudge hard-block point"

# A /handoff write's own file-path shape: handoff/SKILL.md writes
# "<config-dir>/handoffs/<slug>-handoff.md".
_HANDOFF_SIGNAL_WRITE_PATH_RE = re.compile(r"/handoffs/[^/]+-handoff\.md$")

# Long enough to carry a full rationalization sentence on a curation card,
# short enough to keep the card scannable -- a display truncation, not a
# protocol-grounded value.
_HANDOFF_SIGNAL_EXCERPT_MAX_CHARS = 400

# Long enough to carry a full reasoning paragraph in a --context-turns
# entry, short enough to keep --sample output bounded -- a display
# truncation, not a protocol-grounded value.
_HANDOFF_SIGNAL_FORWARD_CONTEXT_MAX_CHARS = 1000


def _handoff_signal_bash_check_call(block: dict) -> bool:
    """True iff `block` is a Bash tool_use invoking
    nudge-handoff-near-context-cap.sh --check, in any &&/;/|-chained segment."""
    if not (isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Bash"):
        return False
    command = (block.get("input") or {}).get("command", "") or ""
    for segment in corpus.split_command_segments(command):
        if segment and os.path.basename(segment[0]) == _HANDOFF_SIGNAL_HOOK_BASENAME and "--check" in segment[1:]:
            return True
    return False


def _handoff_signal_marker_transition(block: dict) -> str | None:
    """Return "activate"/"deactivate" iff `block` is a Bash tool_use invoking
    `marker.sh (activate|deactivate) ready-for-review`, else None. Best-effort,
    like every other command-shape classifier in this file: an unrecognized
    wrapping (an alias, a function) is silently missed."""
    if not (isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Bash"):
        return None
    command = (block.get("input") or {}).get("command", "") or ""
    for segment in corpus.split_command_segments(command):
        if len(segment) < 3 or os.path.basename(segment[0]) != "marker.sh":
            continue
        if segment[1] in ("activate", "deactivate") and segment[2] == "ready-for-review":
            return segment[1]
    return None


def _handoff_signal_is_handoff_event(block: dict) -> bool:
    """True iff `block` is a same-session handoff event: a `/handoff` Skill
    invocation, or a Write/Edit whose file_path is a
    `<config-dir>/handoffs/<slug>-handoff.md` write."""
    if not (isinstance(block, dict) and block.get("type") == "tool_use"):
        return False
    name = block.get("name")
    inp = block.get("input") or {}
    if name == "Skill" and inp.get("skill") == "handoff":
        return True
    if name in ("Write", "Edit"):
        return bool(_HANDOFF_SIGNAL_WRITE_PATH_RE.search(inp.get("file_path", "") or ""))
    return False


def _handoff_signal_is_eligible_main_thread_turn(rec: dict) -> bool:
    """True iff `rec` is a main-thread assistant turn: type=="assistant",
    not isSidechain. Shared by _handoff_signal_excerpt_eligible_text and
    _handoff_signal_forward_context, whose turn-walking loops both need
    the same main-thread-turn definition."""
    return rec.get("type") == "assistant" and not bool(rec.get("isSidechain"))


def _handoff_signal_excerpt_eligible_text(rec: dict) -> str:
    """Excerpt-eligible: main-thread assistant `text` blocks only, never
    tool_use/tool_result/user records or sidechain turns."""
    if not _handoff_signal_is_eligible_main_thread_turn(rec):
        return ""
    content = (rec.get("message") or {}).get("content") or []
    if not isinstance(content, list):
        return ""
    texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text" and b.get("text")]
    return " ".join(texts)


def _handoff_signal_excerpt(deduped: Sequence[dict], after_record_index: int) -> str:
    """First excerpt-eligible text strictly after `after_record_index` in
    `deduped` (see _handoff_signal_excerpt_eligible_text), truncated to
    _HANDOFF_SIGNAL_EXCERPT_MAX_CHARS; "" when no eligible record follows."""
    for rec in deduped[after_record_index + 1:]:
        text = _handoff_signal_excerpt_eligible_text(rec)
        if text:
            return text[:_HANDOFF_SIGNAL_EXCERPT_MAX_CHARS]
    return ""


def _handoff_signal_forward_context(deduped: Sequence[dict], after_record_index: int, n_turns: int) -> list[dict]:
    """Forward-context window for a --context-turns caller:
    - up to `n_turns` main-thread turns strictly after `after_record_index` in `deduped`
    - same main-thread-turn definition as _handoff_signal_response_session_rows' own
      main_thread_turns: type=="assistant", not isSidechain
    - each entry carries both text AND thinking content -- unlike
      _handoff_signal_excerpt_eligible_text (text blocks only), reading both
      content-block kinds lets a caller see reasoning an agent confined to an
      extended-thinking block, invisible to the base excerpt
    - one entry per turn visited, even when both fields are empty, keeping
      turn_offset (1-based) stable
    - stops early once `n_turns` turns have been visited or the transcript
      runs out, whichever comes first
    - never includes session_id/jsonl_path or any other identifying field
    """
    if n_turns <= 0:
        return []
    contexts: list[dict] = []
    for rec in deduped[after_record_index + 1:]:
        if not _handoff_signal_is_eligible_main_thread_turn(rec):
            continue
        content = (rec.get("message") or {}).get("content") or []
        if not isinstance(content, list):
            content = []
        texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text" and b.get("text")]
        thinkings = [
            b.get("thinking", "") for b in content
            if isinstance(b, dict) and b.get("type") == "thinking" and b.get("thinking")
        ]
        contexts.append({
            "turn_offset": len(contexts) + 1,
            "text": " ".join(texts)[:_HANDOFF_SIGNAL_FORWARD_CONTEXT_MAX_CHARS],
            "thinking": " ".join(thinkings)[:_HANDOFF_SIGNAL_FORWARD_CONTEXT_MAX_CHARS],
        })
        if len(contexts) >= n_turns:
            break
    return contexts


def _handoff_signal_response_session_rows(records: Sequence[dict]) -> tuple[list[dict], list[dict], list[int]]:
    """Detect every observed context-budget signal in one session's own
    transcript. Returns (rows, deduped, trace):
    - rows: one dict per signal (kind, record_index, position, context_at_turn,
      threshold, marker_active, handoff_followed, turns_after_signal,
      dollars_after_signal, session_total_dollars, pct_spend_after_signal)
      -- session_id is not included; the caller attaches it (this function
      has no I/O, so it never resolves jsonl.stem).
      -- exceeds_startup_burn_benchmark is not included either: it depends on
      a corpus-wide benchmark the caller alone can compute
      (_startup_burn_benchmark), not on anything local to one session.
    - deduped: this session's own _dedup_turns_by_request_id output, returned
      so a caller building curation-card excerpts can search forward from a
      row's own record_index without re-deduping the session a second time.
    - trace: one abs-token estimate (context_at_turn + output_tokens, the
      hook's own ESTIMATE unit) per main-thread turn, in
      _rearm_backtest_report's own session_traces shape -- lets a caller feed
      _operator_response_lag_from_log without a second dedup+price pass.

    A single ordered pass over `records` (post-dedup) tracks, in parallel:
    - running main-thread turn context/output/dollars (`_price_turn`, the
      same primitive every sibling subcommand in this file uses for this)
    - the `ready-for-review` active-marker state (marker.sh
      activate/deactivate Bash calls)
    - pending `--check` tool_use ids awaiting their tool_result
    - every same-session handoff event

    Each signal row captures the running marker-active state AS OF that
    point in the pass, not the state by the end of the session.
    """
    deduped = pricing.dedup_turns_by_request_id(records)

    main_thread_turns: list[tuple[int, int, float]] = []  # (context_at_turn, output_tokens, dollars)
    main_thread_models: list[str] = []
    marker_active = False
    pending_check_calls: dict[str, int] = {}  # tool_use_id -> turn_index at call time
    handoff_record_indices: list[int] = []
    signals: list[dict] = []

    for record_index, rec in enumerate(deduped):
        rec_type = rec.get("type")

        if rec_type == "attachment":
            att = rec.get("attachment") or {}
            att_type = att.get("type")
            if att_type == "hook_success" and os.path.basename(att.get("command") or "") == _HANDOFF_SIGNAL_HOOK_BASENAME:
                try:
                    payload = json.loads(att.get("stdout") or "")
                except (json.JSONDecodeError, ValueError):
                    payload = {}
                additional_context = (payload.get("hookSpecificOutput") or {}).get("additionalContext")
                if additional_context:
                    signals.append({
                        "kind": _HANDOFF_SIGNAL_ADVISORY,
                        "record_index": record_index,
                        "turn_index": len(main_thread_turns),
                        "marker_active": marker_active,
                    })
            elif att_type == "hook_stopped_continuation" and _HANDOFF_SIGNAL_HARD_BLOCK_TEXT in (att.get("message") or ""):
                signals.append({
                    "kind": _HANDOFF_SIGNAL_HARD_BLOCK,
                    "record_index": record_index,
                    "turn_index": len(main_thread_turns),
                    "marker_active": marker_active,
                })
            continue

        if rec_type == "user":
            content = (rec.get("message") or {}).get("content")
            if isinstance(content, list):
                for block in content:
                    if not (isinstance(block, dict) and block.get("type") == "tool_result"):
                        continue
                    tool_use_id = block.get("tool_use_id")
                    if tool_use_id is None or tool_use_id not in pending_check_calls:
                        continue
                    turn_index = pending_check_calls.pop(tool_use_id)
                    try:
                        payload = json.loads(render._content_text(block.get("content")))
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if payload.get("status") != "ok":
                        continue
                    if payload.get("over_threshold") or payload.get("already_fired"):
                        signals.append({
                            "kind": _HANDOFF_SIGNAL_CHECK,
                            "record_index": record_index,
                            "turn_index": turn_index,
                            "marker_active": marker_active,
                        })
            continue

        if rec_type != "assistant" or bool(rec.get("isSidechain")):
            continue

        msg = rec.get("message") or {}
        usage = msg.get("usage")
        if usage:
            model = msg.get("model", "")
            dollars_by_class, context_at_turn, _unpriced_tokens = pricing._price_turn(model, usage)
            dollars = sum(dollars_by_class.values()) if dollars_by_class is not None else 0.0
            main_thread_turns.append((context_at_turn, int(usage.get("output_tokens", 0)), dollars))
            main_thread_models.append(model)

        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            transition = _handoff_signal_marker_transition(block)
            if transition == "activate":
                marker_active = True
            elif transition == "deactivate":
                marker_active = False
            if _handoff_signal_bash_check_call(block):
                tool_use_id = block.get("id")
                if tool_use_id:
                    pending_check_calls[tool_use_id] = len(main_thread_turns)
            if _handoff_signal_is_handoff_event(block):
                handoff_record_indices.append(record_index)

    total_turns = len(main_thread_turns)
    # Suffix sums: O(total_turns) once, vs. O(signals × turns) if re-summed per signal.
    suffix_dollars = [0.0] * (total_turns + 1)
    for i in range(total_turns - 1, -1, -1):
        suffix_dollars[i] = suffix_dollars[i + 1] + main_thread_turns[i][2]

    rows: list[dict] = []
    for sig in signals:
        turn_index = sig["turn_index"]
        if turn_index > 0:
            context_at_turn = main_thread_turns[turn_index - 1][0]
            threshold = handoff_nudge._hook_effective_fire_threshold(main_thread_models[turn_index - 1])
        else:
            # Defensive edge case, not expected in practice: every real fire
            # already read a usage block before firing, so turn_index is 0
            # only for a malformed/synthetic fixture with no prior usage.
            context_at_turn, threshold = 0, None
        rows.append({
            "kind": sig["kind"],
            "record_index": sig["record_index"],
            "position": turn_index,
            "context_at_turn": context_at_turn,
            "threshold": threshold,
            "marker_active": sig["marker_active"],
            "handoff_followed": any(idx > sig["record_index"] for idx in handoff_record_indices),
            "turns_after_signal": total_turns - turn_index,
            "dollars_after_signal": suffix_dollars[turn_index],
            "session_total_dollars": suffix_dollars[0],
            "pct_spend_after_signal": (
                suffix_dollars[turn_index] / suffix_dollars[0] if suffix_dollars[0] > 0 else None
            ),
        })

    trace = [c + o for c, o, _d in main_thread_turns]
    return rows, deduped, trace


def _rank_signal_rows_by_spend(rows: list[dict], sample_n: int, seed: int | None) -> list[dict]:
    """Return the top `sample_n` rows descending by `dollars_after_signal`,
    with a seeded pre-shuffle tie-break; with no seed, ties keep input order."""
    if seed is not None:
        rng = random.Random(seed)
        rows = list(rows)
        rng.shuffle(rows)
    return sorted(rows, key=lambda row: row["dollars_after_signal"], reverse=True)[:sample_n]


def _handoff_signal_response_cards(sampled: list[dict], redact: bool, context_turns: int = 0) -> list[dict]:
    """Attach a curation-card excerpt to each sampled row, re-reading only
    the sampled rows' own sessions (not the whole scanned corpus) -- see
    _handoff_signal_excerpt's own eligibility rule. `redact` controls
    whether each card's session id is replaced by a run-scoped opaque
    label (_assign_session_redact_label/_redact_session_id, the same
    mechanism cost's own per-row redaction uses). `context_turns` > 0 adds
    a "forward_context" key (_handoff_signal_forward_context) to each card;
    0 (the default) omits the key entirely, so every existing call site's
    card shape is unchanged."""
    session_redact_map: dict[str, str] = {}
    deduped_cache: dict[Path, list[dict]] = {}
    cards: list[dict] = []
    for row in sampled:
        jsonl_path: Path = row["jsonl_path"]
        deduped = deduped_cache.get(jsonl_path)
        if deduped is None:
            deduped = pricing.dedup_turns_by_request_id(corpus.read_session_file(jsonl_path, include_subagents=False))
            deduped_cache[jsonl_path] = deduped
        excerpt = _handoff_signal_excerpt(deduped, row["record_index"])
        session_id = row["session_id"]
        if redact:
            redaction._assign_session_redact_label(session_id, session_redact_map)
            session_id = redaction._redact_session_id(session_id, session_redact_map)
        card = {
            "session_id": session_id,
            "kind": row["kind"],
            "position": row["position"],
            "context_at_turn": row["context_at_turn"],
            "threshold": row["threshold"],
            "marker_active": row["marker_active"],
            "handoff_followed": row["handoff_followed"],
            "turns_after_signal": row["turns_after_signal"],
            "dollars_after_signal": round(row["dollars_after_signal"], 2),
            "session_total_dollars": round(row["session_total_dollars"], 2),
            "pct_spend_after_signal": (
                round(row["pct_spend_after_signal"], 4) if row["pct_spend_after_signal"] is not None else None
            ),
            "exceeds_startup_burn_benchmark": row["exceeds_startup_burn_benchmark"],
            "excerpt": excerpt,
        }
        if context_turns:
            card["forward_context"] = _handoff_signal_forward_context(deduped, row["record_index"], context_turns)
        cards.append(card)
    return cards


def _startup_burn_benchmark(workstream: dict[str, dict]) -> tuple[float | None, int, int]:
    """Session-count-weighted average of startup-burn dollars
    (_compute_workstream_dollars' own startup_burn_dollars) across every
    branch in scope -- not an unweighted per-branch average, which would let
    a low-continuation branch skew the result.

    Returns (benchmark_dollars, total_continuations, branch_count), where
    branch_count is every branch with corpus activity in scope (len(workstream),
    _compute_workstream_dollars' own population), not only branches with a
    continuation session. Returns None for benchmark_dollars when no branch
    has a non-first session to sum, to avoid a ZeroDivisionError.
    """
    total_burn = sum(agg["startup_burn_dollars"] for agg in workstream.values())
    total_continuations = sum(max(agg["session_count"] - 1, 0) for agg in workstream.values())
    benchmark = total_burn / total_continuations if total_continuations > 0 else None
    return benchmark, total_continuations, len(workstream)


def _format_startup_burn_benchmark(benchmark_dollars: float | None) -> str:
    """Shared "$X.XX per continuation session" / unavailable phrasing for
    the startup-burn benchmark -- used by both the aggregate report and the
    curation-card markdown header, so the two surfaces never drift apart."""
    if benchmark_dollars is None:
        return "unavailable (no continuation sessions found in scope)"
    return f"{render._fmt_usd(benchmark_dollars)} per continuation session"


def _format_forward_context_turns_markdown(forward_context: list[dict]) -> str:
    """Render a card's own "forward_context" list (--context-turns only) as
    a markdown subsection; a turn whose text and thinking are both empty is
    skipped entirely to keep the card scannable. Caller only invokes this
    when the key is present on the card -- an empty (but present) list
    still renders a header, distinct from the key being absent."""
    if not forward_context:
        return "**Forward context:** (no eligible turns followed the signal)\n\n"
    lines = [f"**Forward context (next {len(forward_context)} turn(s)):**\n"]
    for turn in forward_context:
        text = turn.get("text") or ""
        thinking = turn.get("thinking") or ""
        if not text and not thinking:
            continue
        pieces = []
        if thinking:
            pieces.append(f"thinking: {thinking}")
        if text:
            pieces.append(f"text: {text}")
        lines.append(f"- turn +{turn['turn_offset']}: {' / '.join(pieces)}\n")
    lines.append("\n")
    return "".join(lines)


def _format_handoff_signal_cards_as_markdown(
    cards: list[dict], *, sample_n: int, seed: int | None, benchmark_dollars: float | None,
) -> str:
    """Return a full markdown document for human curation of
    handoff-signal-response --sample output, mirroring
    _format_samples_as_markdown's own curation-card shape."""
    today = date.today().isoformat()
    seed_display = str(seed) if seed is not None else "(none)"
    header = (
        f"# handoff-signal-response curation — {len(cards)} signal(s)\n"
        f"\n"
        f"Generated: {today}  ·  Filter: `--sample {sample_n}  --seed {seed_display}`\n"
        f"\n"
        f"Startup-burn benchmark (this scope): {_format_startup_burn_benchmark(benchmark_dollars)}.\n"
        f"\n"
        f"For each signal: read the excerpt (the agent's own next eligible text turn after the"
        f" signal, if any), then check ONE verdict box.\n"
    )
    sections: list[str] = []
    total = len(cards)
    for i, card in enumerate(cards):
        excerpt = card["excerpt"] or "(no eligible assistant text turn followed the signal)"
        threshold_display = f"{card['threshold']:,}" if card["threshold"] is not None else "n/a"
        pct_display = (
            f"{card['pct_spend_after_signal'] * 100:.1f}%" if card["pct_spend_after_signal"] is not None else "n/a"
        )
        exceeds_display = (
            "n/a" if card["exceeds_startup_burn_benchmark"] is None
            else ("yes" if card["exceeds_startup_burn_benchmark"] else "no")
        )
        forward_context_block = (
            _format_forward_context_turns_markdown(card["forward_context"]) if "forward_context" in card else ""
        )
        section = (
            f"## {i + 1}/{total} — session `{card['session_id']}` — {card['kind']} signal at turn {card['position']}\n"
            f"\n"
            f"- context_at_turn: {card['context_at_turn']:,}  ·  threshold: {threshold_display}\n"
            f"- ready-for-review marker active: {card['marker_active']}\n"
            f"- handoff followed (same session): {card['handoff_followed']}\n"
            f"- turns after signal: {card['turns_after_signal']:,}  ·  $ after signal: {card['dollars_after_signal']:,.2f}\n"
            f"- % of session spend after signal: {pct_display}  ·  exceeds startup-burn benchmark: {exceeds_display}\n"
            f"\n"
            f"**Excerpt:**\n"
            f"> {excerpt}\n"
            f"\n"
            f"{forward_context_block}"
            f"Verdict: [ ] cost-grounded  [ ] step-count/\"nearly-done\" (no cost reasoning)  "
            f"[ ] handed off  [ ] unclassifiable\n"
        )
        sections.append(section)
    return header + "\n" + "\n".join(sections)


def _handoff_signal_response_aggregate_report(
    rows: Sequence[dict], log_diagnostic: str | None,
    benchmark_dollars: float | None,
) -> None:
    """Print the census-mode aggregate report: signal counts, conversion
    rate, and post-signal spend distribution split by signal kind and by
    marker-active context.

    benchmark_dollars is the corpus-wide startup-burn benchmark
    (_startup_burn_benchmark), pre-computed by the caller from a second,
    independent scope pass -- this function never recomputes it from rows."""
    total = len(rows)
    print(f"\n## Handoff signal response ({total:,} signal(s) in scope)\n")
    print(f"Startup-burn benchmark (this scope): {_format_startup_burn_benchmark(benchmark_dollars)}.")
    if not total:
        print("No signals found in scope.")
        return

    sessions_with_signal = len({r["session_id"] for r in rows})
    followed = sum(1 for r in rows if r["handoff_followed"])
    print(f"Sessions with at least one signal: {sessions_with_signal:,}")
    print(
        "Conversion rate (a same-session /handoff followed the signal):"
        f" {render._pct_of(followed, total)} ({followed:,}/{total:,})"
    )
    if benchmark_dollars is not None:
        exceeding = sum(1 for r in rows if r["exceeds_startup_burn_benchmark"])
        print(
            "Signals whose post-signal spend exceeded the benchmark:"
            f" {exceeding:,} ({render._pct_of(exceeding, total)})"
        )
    if log_diagnostic:
        print(f"\n{log_diagnostic}")

    def _print_breakdown(title: str, key) -> None:
        print(f"\n### {title}\n")
        groups: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            groups[key(r)].append(r)
        header = f"{'Group':<14} {'Signals':>8} {'Handoff%':>9} {'Median $ after':>15}"
        print(header)
        print("-" * len(header))
        for label in sorted(groups):
            group_rows = groups[label]
            n = len(group_rows)
            hf = sum(1 for r in group_rows if r["handoff_followed"])
            dollars = [r["dollars_after_signal"] for r in group_rows]
            median = statistics.median(dollars) if dollars else 0.0
            print(f"{label:<14} {n:>8,} {render._pct_of(hf, n):>9} {median:>15,.2f}")

    _print_breakdown("By signal kind", lambda r: r["kind"])
    _print_breakdown(
        "By ready-for-review active-marker context",
        lambda r: "active" if r["marker_active"] else "inactive",
    )


def cmd_handoff_signal_response(args: argparse.Namespace) -> None:
    """CLI entry point for the handoff-signal-response subcommand.

    Uses the shared `_resolve_scan_roots` scope machinery, not the
    cost-family per-subcommand `--config-dir` extras.

    Resolves scope a second time to feed `_compute_workstream_dollars`,
    since `session_iter` above is a single-pass generator already consumed
    by the main loop. Every other two-pass subcommand in this file (e.g.
    cost-ledger) accepts the same tradeoff.
    """
    redact: bool = not bool(getattr(args, "no_redact", False))
    roots = scope.resolve_scan_roots(args)
    multi_root = len(roots) > 1
    sample_n: int = getattr(args, "sample", 0) or 0
    context_turns: int = getattr(args, "context_turns", 0) or 0

    if not redact and multi_root:
        print(
            "handoff-signal-response: --no-redact is refused when more than one root is in scope;"
            " drop --no-redact or scope to a single root (e.g. --this-repo with no additional"
            " declared roots)",
            file=sys.stderr,
        )
        sys.exit(2)
    if context_turns and not sample_n:
        print("handoff-signal-response: --context-turns requires --sample", file=sys.stderr)
        sys.exit(2)
    if context_turns < 0:
        print("handoff-signal-response: --context-turns must not be negative", file=sys.stderr)
        sys.exit(2)
    if not redact:
        print(scope._DO_NOT_PUBLISH_BANNER)
        print(scope._DO_NOT_PUBLISH_BANNER, file=sys.stderr)

    session_iter, scope_label = scope._resolve_project_scope(args, "handoff-signal-response", roots=roots)
    scope.print_resolved_scope("handoff-signal-response", scope_label, roots)

    seed: int | None = getattr(args, "seed", None)

    all_rows: list[dict] = []
    session_traces: dict[str, list[int]] = {}
    for jsonl, records in session_iter:
        session_id = jsonl.stem
        rows, _deduped, trace = _handoff_signal_response_session_rows(records)
        for row in rows:
            row["session_id"] = session_id
            row["jsonl_path"] = jsonl
        all_rows.extend(rows)
        if trace:
            session_traces[session_id] = trace

    benchmark_session_iter, _benchmark_scope_label = scope._resolve_project_scope(
        args, "handoff-signal-response", roots=roots
    )
    workstream = cost._compute_workstream_dollars(benchmark_session_iter)
    benchmark_dollars, _, _ = _startup_burn_benchmark(workstream)
    for row in all_rows:
        row["exceeds_startup_burn_benchmark"] = (
            row["dollars_after_signal"] > benchmark_dollars if benchmark_dollars is not None else None
        )

    # Corroborating diagnostic only -- every row above already comes from
    # this session's own transcript, never from this log. Mirrors
    # _rearm_backtest_report's own "Operator-response-lag sample" line.
    log_entries = handoff_nudge._parse_nudge_log_entries(scope.config_dir() / ".handoff-nudge.log")
    lags, excluded = handoff_nudge._operator_response_lag_from_log(session_traces, log_entries)
    log_diagnostic: str | None = None
    if lags:
        median_lag = statistics.median(lags)
        log_diagnostic = (
            f"Operator-response-lag cross-check (.handoff-nudge.log 'nudged' lines): {len(lags):,}"
            f" joined ({excluded:,} excluded -- no matching session in scope), median lag"
            f" {median_lag:,.0f} tokens past the fire point"
        )

    if sample_n:
        # Rank by post-signal spend, since that is where a wrong
        # continue-decision actually cost something -- not the whole
        # population. See _rank_signal_rows_by_spend's own docstring for the
        # tie-break contract.
        sampled = _rank_signal_rows_by_spend(all_rows, sample_n, seed)
        cards = _handoff_signal_response_cards(sampled, redact, context_turns=context_turns)
        output_format: str = getattr(args, "output_format", "json") or "json"
        if output_format == "md":
            print(_format_handoff_signal_cards_as_markdown(
                cards, sample_n=sample_n, seed=seed, benchmark_dollars=benchmark_dollars,
            ))
        else:
            print(json.dumps(cards, indent=2))
        return

    for row in all_rows:
        row.pop("jsonl_path", None)
        row.pop("record_index", None)
    _handoff_signal_response_aggregate_report(
        all_rows, log_diagnostic, benchmark_dollars=benchmark_dollars,
    )
