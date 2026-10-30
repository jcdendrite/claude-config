"""The friction-count command: cmd_friction_count, one transcript's composite of
hook denials, failed test runs, and struggle-phrase user turns, its --checkpoint
incremental scan, and its subparser registration.

Imports denials, fail_seq, render, and struggle by module (attribute access, not by
name) -- see scope.py's own top-of-file comment for why.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path

from transcript_analysis import denials, fail_seq, render, struggle

# All three friction signals are weighted equally (1) in the composite —
# stated explicitly here rather than left implicit in the addition below.
_FRICTION_SIGNAL_WEIGHT = 1


def _friction_denial_events(records: list[dict]) -> int:
    """Count hook-denial events in `records`, deduped by tool_use_id.

    Flat, single-file count reusing hook_denial_key for both denial shapes
    (mirrors cmd_review_trace's detection and seen-id dedup exactly), but
    additionally skips isSidechain records — a friction-count-only filter;
    cmd_review_trace's denial detection is not itself isSidechain-filtered.
    """
    seen_denial_ids: set[str] = set()
    count = 0
    for rec in records:
        if bool(rec.get("isSidechain")):
            continue
        rec_type = rec.get("type", "")
        if rec_type == "attachment":
            denial = denials.hook_denial_key(rec)
            if denial is None:
                continue
            tool_use_id, _ = denial
            if tool_use_id and tool_use_id in seen_denial_ids:
                continue
            if tool_use_id:
                seen_denial_ids.add(tool_use_id)
            count += 1
        elif rec_type == "user":
            for block in ((rec.get("message") or {}).get("content") or []):
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                denial = denials.hook_denial_key(block)
                if denial is None:
                    continue
                tool_use_id, _ = denial
                if tool_use_id and tool_use_id in seen_denial_ids:
                    continue
                if tool_use_id:
                    seen_denial_ids.add(tool_use_id)
                count += 1
    return count


def _friction_failed_test_run_events(records: list[dict]) -> int:
    """Count failed test-run events: a Bash tool_use matching TEST_RUNNER_RE
    paired (by tool_use_id) with a tool_result whose max FAILED_RE count > 0.

    Flat, single-file pairing — re-implements the tool_use_id pairing state
    machine independently of cmd_fail_seq (which is branch-grouped and not
    modified), sharing only the TEST_RUNNER_RE/FAILED_RE constants. Counts
    only failing runs (FAILED_RE count > 0), not every matched run — this is
    cmd_fail_seq's "failing" subtotal, not its total run count. isSidechain
    records are skipped.
    """
    pending: set[str] = set()
    count = 0
    for rec in records:
        if bool(rec.get("isSidechain")):
            continue
        rec_type = rec.get("type", "")
        msg = rec.get("message") or {}
        if rec_type == "assistant":
            for block in (msg.get("content") or []):
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("name") == "Bash"
                ):
                    cmd = (block.get("input") or {}).get("command", "")
                    if fail_seq.TEST_RUNNER_RE.search(cmd):
                        pending.add(block["id"])
        elif rec_type in ("user", "human"):
            content = msg.get("content") or []
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                tid = block.get("tool_use_id", "")
                if block.get("type") == "tool_result" and tid in pending:
                    pending.discard(tid)
                    result_text = render._content_text(block.get("content", ""))
                    counts = [int(m) for m in fail_seq.FAILED_RE.findall(result_text)]
                    if counts and max(counts) > 0:
                        count += 1
    return count


def _friction_struggle_turn_events(records: list[dict]) -> int:
    """Count user turns whose lowercased text contains a STRUGGLE_PHRASES entry.

    Flat, single-file count — no branch or model-family attribution.
    isSidechain records are skipped.
    """
    count = 0
    for rec in records:
        if bool(rec.get("isSidechain")):
            continue
        if rec.get("type") not in ("user", "human"):
            continue
        msg = rec.get("message") or {}
        text = render._strip_task_notifications(render._content_text(msg.get("content", ""))).lower()
        if any(phrase in text for phrase in struggle.STRUGGLE_PHRASES):
            count += 1
    return count


def _friction_signals(records: list[dict]) -> dict[str, int]:
    """Return the per-signal friction breakdown plus the all-1-weighted composite."""
    # Shadows the module-level `denials` import (transcript_analysis.denials) -- this
    # function needs no denials.<name> access, only the count computed below.
    denials = _friction_denial_events(records)  # noqa: F811
    failed_test_runs = _friction_failed_test_run_events(records)
    struggle_turns = _friction_struggle_turn_events(records)
    composite = (
        _FRICTION_SIGNAL_WEIGHT * denials
        + _FRICTION_SIGNAL_WEIGHT * failed_test_runs
        + _FRICTION_SIGNAL_WEIGHT * struggle_turns
    )
    signals = {
        "denials": denials,
        "failed_test_runs": failed_test_runs,
        "struggle_turns": struggle_turns,
        "composite": composite,
    }
    # Pinned invariant: composite must equal the sum of the three signals.
    # An explicit raise (not `assert`) so the check survives python -O /
    # PYTHONOPTIMIZE, which strips bare asserts.
    if signals["composite"] != denials + failed_test_runs + struggle_turns:
        raise AssertionError(
            "friction-count: composite must equal the sum of denials + failed_test_runs + struggle_turns"
        )
    return signals


# friction-count --checkpoint: incremental byte-offset scan. Avoids reparsing
# the whole transcript on every hook fire — see the hook's own comment for
# why this exists. The checkpoint is a small JSON blob (offset + the three
# running per-signal totals); no cross-call dedup state is needed beyond the
# offset, because each call starts exactly where the previous one stopped, so
# every transcript line is read at most once across the checkpoint's lifetime.
_FRICTION_CHECKPOINT_SIGNAL_KEYS = ("denials", "failed_test_runs", "struggle_turns")


def _is_valid_checkpoint_int(value: object) -> bool:
    """True if `value` is a non-negative int — explicitly excluding bool,
    which subclasses int in Python (`isinstance(True, int)` is True), so an
    unvalidated check would silently accept `{"offset": true}` as offset 1."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _read_friction_checkpoint(checkpoint_path: Path, transcript_path: Path) -> tuple[int, dict[str, int]]:
    """Read a friction-count checkpoint: (byte_offset, running per-signal totals).

    Fails open to a full rescan (offset 0, zero totals) on any absent or
    malformed checkpoint — unreadable file, invalid JSON, wrong shape, a
    non-int/negative/bool offset or totals value, or a stored offset beyond
    the transcript's current size (a stale checkpoint from a transcript that
    was truncated or rewritten while the session_id-keyed checkpoint
    persisted — without this check, seeking past EOF would freeze friction
    counting for that session permanently rather than resetting it). Never
    raises.
    """
    zero_totals = {key: 0 for key in _FRICTION_CHECKPOINT_SIGNAL_KEYS}
    try:
        data = json.loads(checkpoint_path.read_text())
    except (OSError, json.JSONDecodeError):
        return 0, dict(zero_totals)
    if not isinstance(data, dict):
        return 0, dict(zero_totals)
    offset = data.get("offset")
    totals = data.get("totals")
    if not _is_valid_checkpoint_int(offset) or not isinstance(totals, dict):
        return 0, dict(zero_totals)
    running_totals: dict[str, int] = {}
    for key in _FRICTION_CHECKPOINT_SIGNAL_KEYS:
        value = totals.get(key)
        if not _is_valid_checkpoint_int(value):
            return 0, dict(zero_totals)
        running_totals[key] = value
    try:
        transcript_size = transcript_path.stat().st_size
    except OSError:
        # Transcript unreadable — let the caller's own transcript-read
        # attempt raise/report; the checkpoint content isn't the problem.
        return offset, running_totals
    if offset > transcript_size:
        return 0, dict(zero_totals)
    return offset, running_totals


def _write_friction_checkpoint(checkpoint_path: Path, offset: int, totals: dict[str, int]) -> None:
    """Best-effort persist of the new byte offset + running per-signal totals.

    Writes to a temp file in the checkpoint's own directory and atomically
    renames it into place (`os.replace`), rather than truncating the
    checkpoint file in place — an interrupted-and-resubmitted prompt can
    leave two hook invocations for the same session_id alive within the
    hook's 10s timeout, and an in-place write is a lost-update race between
    them. A failed write (unwritable directory, disk full) is swallowed
    rather than raised: the next call re-reads from the old offset and
    rescans those bytes again — correct, just not incremental for that one
    call — matching this subcommand's fail-open posture everywhere else.
    """
    with contextlib.suppress(OSError):
        fd, tmp_name = tempfile.mkstemp(
            dir=checkpoint_path.parent, prefix=f".{checkpoint_path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(json.dumps({"offset": offset, "totals": totals}))
            os.replace(tmp_name, checkpoint_path)
        except OSError:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise


def _consume_new_transcript_lines(transcript_path: Path, offset: int) -> tuple[list[str], int]:
    """Read complete JSONL lines appended to `transcript_path` since `offset`.

    Returns (new_lines, new_offset). A trailing line with no terminating
    newline — the transcript may be mid-write by the harness while the hook
    reads it — is left unconsumed: new_offset stops at the end of the last
    complete line, so the next call picks up the partial line's bytes whole
    once it's terminated, rather than double-reading or losing them.
    """
    with open(transcript_path, "rb") as fh:
        fh.seek(offset)
        chunk = fh.read()
    if not chunk:
        return [], offset
    pieces = chunk.split(b"\n")
    complete_pieces = pieces[:-1]  # last piece is "" (chunk ended in \n) or a partial line
    new_offset = offset + sum(len(piece) + 1 for piece in complete_pieces)
    new_lines = [piece.decode("utf-8", errors="replace") for piece in complete_pieces]
    return new_lines, new_offset


def _emit_friction_result(signals: dict[str, int], *, as_json: bool) -> None:
    """Print either the --json per-signal breakdown or the bare composite integer."""
    if as_json:
        print(json.dumps(signals))
    else:
        print(signals["composite"])


def cmd_friction_count(args: argparse.Namespace) -> None:
    """Composite friction-signal count for a single transcript file.

    Reads exactly one JSONL file (no iter_sessions, no gh) and prints the
    composite integer to stdout. --json prints the per-signal breakdown
    instead of the composite.

    --checkpoint <path> makes the scan incremental: only the bytes appended
    to the transcript since the checkpoint's stored byte offset are parsed,
    the resulting per-signal deltas are added to the checkpoint's running
    totals, the new offset + totals are written back, and the *cumulative*
    totals (not just this call's delta) are what gets printed. Without
    --checkpoint, behavior is unchanged: a full scan every call, no state
    read or written.
    """
    transcript_path = Path(args.transcript)
    checkpoint_arg = getattr(args, "checkpoint", None)

    if checkpoint_arg:
        checkpoint_path = Path(checkpoint_arg)
        offset, running_totals = _read_friction_checkpoint(checkpoint_path, transcript_path)
        try:
            new_lines, new_offset = _consume_new_transcript_lines(transcript_path, offset)
        except OSError:
            print(f"friction-count: cannot read transcript file: {transcript_path}", file=sys.stderr)
            sys.exit(1)

        records: list[dict] = []
        for raw in new_lines:
            try:
                records.append(json.loads(raw))
            except json.JSONDecodeError:
                continue

        deltas = _friction_signals(records)
        for key in _FRICTION_CHECKPOINT_SIGNAL_KEYS:
            running_totals[key] += deltas[key]
        _write_friction_checkpoint(checkpoint_path, new_offset, running_totals)

        composite = sum(running_totals[key] for key in _FRICTION_CHECKPOINT_SIGNAL_KEYS)
        signals = {**running_totals, "composite": composite}
        _emit_friction_result(signals, as_json=getattr(args, "json", False))
        return

    records = []
    try:
        # errors="replace" mirrors the --checkpoint branch's binary-read +
        # decode tolerance: a transcript with invalid Unicode bytes must
        # degrade to lossy decoding, not raise an uncaught
        # UnicodeDecodeError (a ValueError subclass the `except OSError`
        # below does not catch).
        with open(transcript_path, encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                records.append(rec)
    except OSError:
        print(f"friction-count: cannot read transcript file: {transcript_path}", file=sys.stderr)
        sys.exit(1)

    signals = _friction_signals(records)
    _emit_friction_result(signals, as_json=getattr(args, "json", False))


def _add_friction_count_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register the friction-count subparser on build_parser()'s subparsers action,
    binding cmd_friction_count through set_defaults(func=...).
    """
    p_friction = subparsers.add_parser(
        "friction-count",
        help=(
            "Composite friction-signal count (hook denials + failed test runs +"
            " user-correction phrases) for a single transcript file."
        ),
    )
    p_friction.add_argument(
        "--transcript", required=True, metavar="PATH",
        help="Path to one transcript .jsonl file.",
    )
    p_friction.add_argument(
        "--checkpoint", metavar="PATH", default=None,
        help=(
            "Path to a checkpoint file for incremental scanning: only bytes"
            " appended to the transcript since the checkpoint's stored offset"
            " are parsed, and the cumulative composite (not just this call's"
            " delta) is printed. An absent or malformed checkpoint fails open"
            " to a full scan from offset 0. Without this flag, every call"
            " does a full scan with no state read or written."
        ),
    )
    p_friction.add_argument(
        "--json", action="store_true",
        help="Emit the per-signal breakdown as JSON instead of the composite integer.",
    )
    p_friction.set_defaults(func=cmd_friction_count)
