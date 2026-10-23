"""The user-input command: cmd_user_input, the per-session narrative of fresh user
prompts classified as initial, follow-up, or explicit correction, its
prompt-classification helpers, and its subparser registration.

Imports corpus, redaction, render, scope, and struggle by module (attribute access,
not by name) -- see scope.py's own top-of-file comment for why.
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from transcript_analysis import corpus, redaction, render, scope, struggle


def _is_fresh_user_prompt_for_narrative(rec: dict) -> bool:
    """Return True when rec is a genuine user keystroke — not a tool result or system injection.

    Distinct from `_is_fresh_user_prompt` (judgment-pair's discriminator): this one excludes
    on toolUseResult/sourceToolUseID/sourceToolAssistantUUID keys instead of tool_result content
    blocks, and accepts promptId-bearing list content instead of requiring a bare string.

    Accepts two content shapes:
    - Plain string (the common case).
    - List-of-blocks with extractable text and a promptId (text+image pastes, or list-shape
      prompts from older Claude Code versions). promptId is the positive signal that
      distinguishes these from isMeta injections that also use list-of-blocks content.
    """
    if rec.get("type") != "user":
        return False
    if rec.get("isMeta") or rec.get("isSidechain"):
        return False
    if "toolUseResult" in rec or "sourceToolUseID" in rec or "sourceToolAssistantUUID" in rec:
        return False
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str):
        return True
    if isinstance(content, list) and "promptId" in rec:
        return bool(render._content_text(content).strip())
    return False


def _is_unrecognized_user_list_record(rec: dict) -> bool:
    """Return True for user records with list-of-blocks content not handled by any known discriminator.

    Known shapes explicitly excluded:
    - isMeta=True: skill/system injections (correctly excluded from prompts).
    - promptId present: accepted as fresh prompts by _is_fresh_user_prompt_for_narrative (list-content variant).
    - toolUseResult / sourceToolUseID / sourceToolAssistantUUID: tool-result records.

    A non-zero count from this function indicates a genuinely unexpected schema variant
    that the fresh-prompt discriminator may be silently missing.
    """
    return (
        rec.get("type") == "user"
        and isinstance(rec.get("message", {}).get("content"), list)
        and not rec.get("isMeta")
        and "promptId" not in rec
        and "toolUseResult" not in rec
        and "sourceToolUseID" not in rec
        and "sourceToolAssistantUUID" not in rec
    )


def _classify_prompt(text: str, is_initial: bool) -> tuple[str, str]:
    """Classify a fresh prompt as INITIAL, FOLLOWUP, or EXPLICIT_CORRECTION.

    Returns (classification, matched_phrase). matched_phrase is non-empty only
    for EXPLICIT_CORRECTION. Phrase-matching runs on text with any forwarded
    `<task-notification>` envelope stripped, so a STRUGGLE_PHRASES entry inside
    one does not register.
    """
    if is_initial:
        return "INITIAL", ""
    lowered = render._strip_task_notifications(text).lower()
    for phrase in struggle.STRUGGLE_PHRASES:
        if phrase in lowered:
            return "EXPLICIT_CORRECTION", phrase
    return "FOLLOWUP", ""


def _attribute_model_to_prompt(records: list[dict], prompt_index: int, session_id: str) -> str:
    """Scan forward from prompt_index for the next assistant record sharing the session's ID.

    Returns the model family string, or 'unknown' when no attribution is found.
    The session ID is read from the prompt record itself so cross-session attribution
    is not possible.
    """
    prompt_rec = records[prompt_index]
    prompt_session_id = prompt_rec.get("sessionId") or ""
    for rec in records[prompt_index + 1 :]:
        if rec.get("type") != "assistant":
            continue
        if prompt_session_id and rec.get("sessionId") != prompt_session_id:
            continue
        model = (rec.get("message") or {}).get("model", "")
        if model:
            return render._fam(model)
    return "unknown"


def _truncate_prompt_text(text: str, limit: int) -> str:
    """Truncate text to limit chars, appending an ellipsis annotation when truncated.

    limit=0 disables truncation entirely.
    """
    if limit == 0 or len(text) <= limit:
        return text
    return text[:limit] + f"… (truncated, {len(text)} chars total)"


def _fence_for_text(text: str) -> str:
    """A `~`-fence longer than any run of `~` in text, so an embedded run (e.g. a prompt quoting
    someone else's fenced code block) can't close the fence early. CommonMark closes a fence with
    any same-character run at least as long as the opening one, so the fence must exceed the
    longest run already in the text, not just match it."""
    longest_run = current_run = 0
    for char in text:
        current_run = current_run + 1 if char == "~" else 0
        longest_run = max(longest_run, current_run)
    return "~" * max(3, longest_run + 1)


def cmd_user_input(args: argparse.Namespace) -> None:
    """Per-session fresh user prompts, classified as INITIAL / FOLLOWUP / EXPLICIT_CORRECTION."""
    projects_glob = scope._projects_glob(args)
    branch_filter = scope._branch_filter(args)
    corrections_only: bool = bool(getattr(args, "corrections_only", False))
    _truncate_raw = getattr(args, "truncate_chars", None)
    truncate_chars: int = _truncate_raw if _truncate_raw is not None else 500
    out_path: str | None = getattr(args, "out", None) or None
    redact: bool = bool(getattr(args, "redact", False))

    since_ts, until_epoch = scope._parse_absolute_window_args(args, "user-input")

    redact_map: dict[str, str] = redaction._build_redact_map() if redact else {}
    session_redact_map: dict[str, str] = {}

    # Shape-drift counter: user records with list content missing all tool-result keys.
    unrecognized_shape_count = 0

    # Collected session data for rendering, sorted by first-prompt timestamp.
    # Each entry: {proj_label, branch, date, session_id_prefix, prompts: [...], first_ts}
    session_entries: list[dict] = []

    # Corpus-level counters.
    total_projects_seen: set[str] = set()
    total_session_count = 0
    total_fresh_prompts = 0
    initial_count = 0
    followup_count = 0
    correction_count = 0
    phrase_hits: dict[str, int] = defaultdict(int)
    earliest_ts: float | None = None
    latest_ts: float | None = None

    for jsonl, records in corpus.iter_sessions(scope.PROJECTS_DIR, projects_glob):
        proj_label = redaction._derive_proj_label(jsonl)
        total_projects_seen.add(redaction._project_family(jsonl.parent.name))

        # Count unrecognized shapes regardless of other filters.
        for rec in records:
            if _is_unrecognized_user_list_record(rec):
                unrecognized_shape_count += 1

        # Collect fresh prompts for this session, applying all filters.
        session_prompts: list[dict] = []
        is_first_in_session = True
        branch_context = scope._session_branch_context(records)

        for idx, rec in enumerate(records):
            if not _is_fresh_user_prompt_for_narrative(rec):
                continue

            # Branch filter
            branch = scope._attributed_branch(rec, branch_context) or ""
            if branch_filter and branch not in branch_filter:
                continue

            # Date filter
            rec_ts = corpus._parse_ts(rec.get("timestamp"))
            if since_ts is not None or until_epoch is not None:
                if rec_ts is None:
                    continue
                if since_ts is not None and rec_ts < since_ts:
                    continue
                if until_epoch is not None and rec_ts >= until_epoch:
                    continue

            text = render._content_text(rec["message"]["content"])
            classification, matched_phrase = _classify_prompt(text, is_first_in_session)
            is_first_in_session = False

            model_fam = _attribute_model_to_prompt(records, idx, rec.get("sessionId") or "")

            date_str = render._fmt_date(rec_ts) if rec_ts is not None else "?"
            time_str = (
                datetime.fromtimestamp(rec_ts, tz=UTC).strftime("%H:%M")
                if rec_ts is not None
                else "?"
            )

            session_prompts.append({
                "text": text,
                "date": date_str,
                "time": time_str,
                "ts": rec_ts,
                "branch": branch,
                "session_id": rec.get("sessionId") or jsonl.stem,
                "classification": classification,
                "matched_phrase": matched_phrase,
                "model_fam": model_fam,
            })

        if not session_prompts:
            continue

        total_session_count += 1
        first_ts = session_prompts[0]["ts"]
        if first_ts is not None:
            if earliest_ts is None or first_ts < earliest_ts:
                earliest_ts = first_ts
            last_ts = session_prompts[-1]["ts"]
            if last_ts is not None and (latest_ts is None or last_ts > latest_ts):
                latest_ts = last_ts

        # Accumulate corpus counters.
        for p in session_prompts:
            total_fresh_prompts += 1
            if p["classification"] == "INITIAL":
                initial_count += 1
            elif p["classification"] == "FOLLOWUP":
                followup_count += 1
            elif p["classification"] == "EXPLICIT_CORRECTION":
                correction_count += 1
                if p["matched_phrase"]:
                    phrase_hits[p["matched_phrase"]] += 1

        # Derive session-level metadata.
        session_branch = session_prompts[0]["branch"]
        session_date = session_prompts[0]["date"]
        raw_session_id = session_prompts[0]["session_id"]
        if redact:
            redaction._assign_session_redact_label(raw_session_id, session_redact_map)
            session_id_prefix = redaction._redact_session_id(raw_session_id, session_redact_map)
        else:
            session_id_prefix = raw_session_id[:8]
        session_models = sorted({p["model_fam"] for p in session_prompts})
        session_correction_count = sum(1 for p in session_prompts if p["classification"] == "EXPLICIT_CORRECTION")
        session_followup_count = sum(1 for p in session_prompts if p["classification"] == "FOLLOWUP")
        display_label = redaction._redact_proj_label(proj_label, redact_map) if redact else proj_label

        session_entries.append({
            "proj_label": display_label,
            "branch": session_branch,
            "date": session_date,
            "first_ts": first_ts,
            "session_id_prefix": session_id_prefix,
            "session_models": session_models,
            "prompts": session_prompts,
            "correction_count": session_correction_count,
            "followup_count": session_followup_count,
        })

    # Sort sessions by first-prompt timestamp ascending.
    session_entries.sort(key=lambda e: (e["first_ts"] or 0.0))

    # Build top-5 struggle-phrase list.
    top_phrases = sorted(phrase_hits.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    top_phrases_str = ", ".join(f'"{ph}" ({n})' for ph, n in top_phrases) if top_phrases else "none"

    date_range_str = (
        f"{render._fmt_date(earliest_ts)} → {render._fmt_date(latest_ts)}"
        if earliest_ts is not None and latest_ts is not None
        else "no data"
    )

    lines: list[str] = []
    lines.append("# User Input — Conversation Narrative")
    lines.append("")
    lines.append(f"Generated: {render._fmt_date(time.time())}")
    lines.append(
        f"Scope: {len(total_projects_seen)} projects, {total_session_count} sessions, "
        f"{total_fresh_prompts} fresh prompts"
    )
    lines.append(f"Date range: {date_range_str}")
    lines.append("")
    lines.append("## Summary")
    lines.append(f"- Fresh prompts: {total_fresh_prompts}")
    lines.append(f"- Initial: {initial_count}")
    lines.append(f"- Followups (quiet redirects): {followup_count}")
    lines.append(f"- Explicit corrections (struggle-phrase match): {correction_count}")
    lines.append(f"- Top struggle phrases: {top_phrases_str}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Sessions")

    for entry in session_entries:
        lines.append("")
        lines.append(f"### {entry['proj_label']} · {entry['branch']} · {entry['date']}")
        models_str = ", ".join(entry["session_models"])
        prompt_count = len(entry["prompts"])
        lines.append(
            f"Session `{entry['session_id_prefix']}` · models: {models_str} · "
            f"{prompt_count} prompt{'s' if prompt_count != 1 else ''} "
            f"({entry['correction_count']} explicit correction{'s' if entry['correction_count'] != 1 else ''}, "
            f"{entry['followup_count']} followup{'s' if entry['followup_count'] != 1 else ''})"
        )

        for prompt in entry["prompts"]:
            classification = prompt["classification"]
            if corrections_only and classification == "INITIAL":
                continue

            lines.append("")
            if classification == "EXPLICIT_CORRECTION":
                lines.append(
                    f"**[{prompt['time']} · {classification} · {prompt['model_fam']}]**"
                    f" (matched: \"{prompt['matched_phrase']}\")"
                )
            else:
                lines.append(f"**[{prompt['time']} · {classification} · {prompt['model_fam']}]**")
            prompt_text = _truncate_prompt_text(prompt["text"], truncate_chars)
            fence = _fence_for_text(prompt_text)
            lines.append(f"{fence}text")
            lines.append(prompt_text)
            lines.append(fence)

    output = "\n".join(lines) + "\n"

    # Shape-audit line always printed to stderr so it doesn't pollute --out file content.
    print(f"Shape audit: {unrecognized_shape_count} unrecognized user records skipped", file=sys.stderr)

    if out_path:
        try:
            Path(out_path).write_text(output, encoding="utf-8")
            print(f"Wrote output to {out_path}")
        except OSError as exc:
            print(f"user-input: failed to write {out_path}: {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        print(output, end="")


def _add_user_input_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register the user-input subparser on build_parser()'s subparsers action,
    binding cmd_user_input through set_defaults(func=...).
    """
    p_user_input = subparsers.add_parser(
        "user-input",
        help="All fresh user prompts per session, classified as initial / followup / explicit-correction.",
    )
    p_user_input.add_argument("--projects", default="*", metavar="GLOB")
    p_user_input.add_argument("--branches", metavar="B1,B2,...")
    p_user_input.add_argument("--since", metavar="DATE", type=scope._iso_date, help="Inclusive start date (YYYY-MM-DD)")
    p_user_input.add_argument("--until", metavar="DATE", type=scope._iso_date, help="Inclusive end date (YYYY-MM-DD)")
    p_user_input.add_argument(
        "--corrections-only", action="store_true",
        help="Show only non-initial prompts.",
    )
    p_user_input.add_argument(
        "--truncate-chars", type=int, default=500, metavar="N",
        help="Truncate prompt text at N chars (0 = no truncation).",
    )
    p_user_input.add_argument("--out", metavar="PATH", help="Write output to a file instead of stdout.")
    p_user_input.add_argument(
        "--redact", action="store_true",
        help=(
            "Anonymize project labels and session IDs for public reporting "
            "(prompt text is not redacted — review before sharing)."
        ),
    )
    p_user_input.set_defaults(func=cmd_user_input)
