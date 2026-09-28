"""Local per-week cost/efficiency ledger read/append. See docs/cost-ledger.md
for the schema and .claude/plans/cost-trend-ledger.md for the design
rationale (why each column exists, what got dropped, and the error-path
contracts implemented below).

Imports cost, ledger_common, pricing, render, review_trace, reviewer_yield,
and scope by module (attribute access, not by name) -- see scope.py's own
top-of-file comment for why. config_dir is bound by name from _config_dir,
the same way pr_cost_ledger.py binds it.
"""
from __future__ import annotations

import argparse
import contextlib
import errno
import fcntl
import math
import os
import re
import stat
import sys
import tempfile
import time
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import _config
from _config_dir import config_dir
from transcript_analysis import cost, ledger_common, pricing, render, review_trace, reviewer_yield, scope

_COST_LEDGER_COLUMNS = (
    "week", "machine", "rates", "usd", "context_pct", "opus_pct",
    "ge200k_pct", "denials", "reviewer_gap_pp", "note",
)
_COST_LEDGER_HEADER_LINE = "| " + " | ".join(_COST_LEDGER_COLUMNS) + " |"
_COST_LEDGER_SEPARATOR_LINE = "|" + "|".join(["---"] * len(_COST_LEDGER_COLUMNS)) + "|"


_COST_LEDGER_ISO_WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")


# A note is a rendered markdown table cell (docs/cost-ledger.md, viewed on
# GitHub) and a terminal string (cost-ledger's own read mode) -- printable
# ASCII only blocks both raw control/escape bytes and non-ASCII lookalikes.
_COST_LEDGER_NOTE_MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]]*\]\([^)]*\)")


class _CostLedgerParseError(Exception):
    """Raised by _parse_cost_ledger_file_text on any malformed ledger content
    -- the canonical parser fails loud rather than mis-parsing a hand-edited
    or merge-conflicted row."""


def _cost_ledger_path() -> Path:
    """Return the active cost-ledger file path: $COST_LEDGER_PATH if set
    (must be absolute), else config_dir() / "cost-ledger.md"."""
    override = os.environ.get("COST_LEDGER_PATH")
    if override:
        path = Path(override)
        if not path.is_absolute():
            raise ValueError(f"COST_LEDGER_PATH must be an absolute path, got: {override!r}")
        return path
    return config_dir() / "cost-ledger.md"


def _parse_cost_ledger_iso_week(week_str: str) -> None:
    """Raise _CostLedgerParseError unless week_str is a genuine ISO week
    label -- both the YYYY-Www shape and a week number the given year
    actually has (year 2025 has no W53, for instance)."""
    m = _COST_LEDGER_ISO_WEEK_RE.match(week_str)
    if not m:
        raise _CostLedgerParseError(f"malformed week label {week_str!r} (expected YYYY-Www)")
    year, week = int(m.group(1)), int(m.group(2))
    try:
        date.fromisocalendar(year, week, 1)
    except ValueError as exc:
        raise _CostLedgerParseError(f"malformed week label {week_str!r}: {exc}") from None


def _parse_cost_ledger_pct_cell(label: str, cell: str) -> float:
    """Parse a "N.N%"-shaped percentage cell, raising _CostLedgerParseError
    on anything else (missing '%', a non-numeric value in front of it, or a
    non-finite float -- float() itself accepts "nan"/"inf"/"-infinity" with
    no error, which a percentage column must reject the same as any other
    malformed cell)."""
    if not cell.endswith("%"):
        raise _CostLedgerParseError(f"malformed {label} {cell!r} (expected a trailing '%')")
    try:
        value = float(cell[:-1])
    except ValueError:
        raise _CostLedgerParseError(f"non-numeric {label} {cell!r}") from None
    if math.isnan(value) or math.isinf(value):
        raise _CostLedgerParseError(f"non-finite {label} {cell!r}")
    return value


def _cost_ledger_note_violation(note: str) -> str | None:
    """Return a human-readable reason `note` is rejected, or None if it's
    valid. Shared between --record-time validation and the canonical row
    parser, so a hand-edited or PR-introduced row is held to the same
    contract as one written by --record: a raw '|' or newline would corrupt
    the table's row format, a non-printable-ASCII byte (e.g. an ANSI/OSC
    terminal escape sequence) would be interpolated unescaped into
    cost-ledger's terminal read-mode output, and markdown link/image syntax
    would beacon an external server on every GitHub render of
    docs/cost-ledger.md.
    """
    if "|" in note or "\n" in note or "\r" in note:
        return "must not contain '|' or a newline -- either would corrupt the table's row format"
    if not all(32 <= ord(c) <= 126 for c in note):
        return "must contain only printable ASCII -- control and terminal-escape characters are rejected"
    if _COST_LEDGER_NOTE_MARKDOWN_LINK_RE.search(note):
        return "must not contain markdown link/image syntax ('[text](url)' or '![alt](url)')"
    return None


def _parse_cost_ledger_row_cells(cells: list[str], line_no: int) -> dict:
    """Validate and coerce one already-split, already-stripped data row's
    cells into a typed row dict. Raises _CostLedgerParseError naming the
    offending line on any field that doesn't match its column's contract.
    """
    if len(cells) != len(_COST_LEDGER_COLUMNS):
        raise _CostLedgerParseError(
            f"line {line_no}: expected {len(_COST_LEDGER_COLUMNS)} columns, got {len(cells)}"
            " (a stray '|' inside a cell produces this same error)"
        )
    week, machine, rates, usd_s, context_s, opus_s, ge200k_s, denials_s, gap_s, note = cells

    note_violation = _cost_ledger_note_violation(note)
    if note_violation is not None:
        raise _CostLedgerParseError(f"line {line_no}: malformed note {note!r} ({note_violation})")

    try:
        _parse_cost_ledger_iso_week(week)
    except _CostLedgerParseError as exc:
        raise _CostLedgerParseError(f"line {line_no}: {exc}") from None

    if not ledger_common._MACHINE_LABEL_RE.match(machine):
        raise _CostLedgerParseError(f"line {line_no}: malformed machine label {machine!r}")

    try:
        datetime.strptime(rates, "%Y-%m-%d")
    except ValueError:
        raise _CostLedgerParseError(f"line {line_no}: malformed rates date {rates!r}") from None

    try:
        usd = float(usd_s)
    except ValueError:
        raise _CostLedgerParseError(f"line {line_no}: non-numeric usd {usd_s!r}") from None
    if math.isnan(usd) or math.isinf(usd):
        raise _CostLedgerParseError(f"line {line_no}: non-finite usd {usd_s!r}")

    try:
        context_pct = _parse_cost_ledger_pct_cell("context_pct", context_s)
        opus_pct = _parse_cost_ledger_pct_cell("opus_pct", opus_s)
        ge200k_pct = _parse_cost_ledger_pct_cell("ge200k_pct", ge200k_s)
    except _CostLedgerParseError as exc:
        raise _CostLedgerParseError(f"line {line_no}: {exc}") from None

    try:
        denials = int(denials_s)
    except ValueError:
        raise _CostLedgerParseError(f"line {line_no}: non-numeric denials {denials_s!r}") from None

    reviewer_gap_pp: float | str | None = None
    if gap_s:
        if gap_s == reviewer_yield._REVIEWER_YIELD_INSUFFICIENT:
            reviewer_gap_pp = gap_s
        elif not gap_s.endswith("pp"):
            raise _CostLedgerParseError(
                f"line {line_no}: malformed reviewer_gap_pp {gap_s!r} (expected a trailing 'pp')"
            )
        else:
            try:
                reviewer_gap_pp = float(gap_s[:-2])
            except ValueError:
                raise _CostLedgerParseError(f"line {line_no}: non-numeric reviewer_gap_pp {gap_s!r}") from None
            if math.isnan(reviewer_gap_pp) or math.isinf(reviewer_gap_pp):
                raise _CostLedgerParseError(f"line {line_no}: non-finite reviewer_gap_pp {gap_s!r}")

    return {
        "week": week, "machine": machine, "rates": rates, "usd": usd,
        "context_pct": context_pct, "opus_pct": opus_pct, "ge200k_pct": ge200k_pct,
        "denials": denials, "reviewer_gap_pp": reviewer_gap_pp, "note": note,
    }


def _parse_cost_ledger_file_text(text: str) -> tuple[str, list[dict]]:
    """Canonical parser for docs/cost-ledger.md's full content.

    Returns (preamble, rows): preamble is everything up to and including the
    table's header and separator rows, verbatim, so a re-render (preamble +
    one rendered line per row) is a byte-identical round trip for an
    already-canonical file. Fails loud (_CostLedgerParseError) on an
    unresolved git merge-conflict marker anywhere in the file, a missing or
    malformed header/separator pair, a data row not wrapped in '|...|', a
    wrong column count (a stray '|' inside a cell surfaces this same error),
    a non-ISO week label, or a non-numeric numeric/percentage cell -- never
    silently misparses a malformed row.
    """
    lines = text.splitlines()
    for marker in ledger_common._COST_LEDGER_CONFLICT_MARKERS:
        for line_no, line in enumerate(lines, start=1):
            if line.startswith(marker):
                raise _CostLedgerParseError(f"line {line_no}: unresolved merge-conflict marker {marker!r}")

    header_idx = None
    for i, line in enumerate(lines):
        if line.strip() == _COST_LEDGER_HEADER_LINE:
            header_idx = i
            break
    if header_idx is None:
        raise _CostLedgerParseError("could not find the cost-ledger table header row")
    if header_idx + 1 >= len(lines) or lines[header_idx + 1].strip() != _COST_LEDGER_SEPARATOR_LINE:
        raise _CostLedgerParseError("cost-ledger table header is not followed by its separator row")

    rows: list[dict] = []
    for line_no, line in enumerate(lines[header_idx + 2:], start=header_idx + 3):
        if not line.strip():
            continue
        stripped = line.strip()
        if not (stripped.startswith("|") and stripped.endswith("|")):
            raise _CostLedgerParseError(f"line {line_no}: malformed table row {line!r}")
        cells = [c.strip() for c in stripped[1:-1].split("|")]
        rows.append(_parse_cost_ledger_row_cells(cells, line_no))

    preamble = "\n".join(lines[: header_idx + 2]) + "\n"
    return preamble, rows


def _format_reviewer_gap_cell(gap: float | str | None, *, unmeasured: str) -> str:
    """Render reviewer_gap_pp for either the markdown-pipe or read-mode
    format. unmeasured is the empty-denominator token -- "" for the
    markdown file's empty cell, "unmeasured" for the read-mode fixed-width
    line. A str value is always _REVIEWER_YIELD_INSUFFICIENT and passes
    through unchanged."""
    if gap is None:
        return unmeasured
    if isinstance(gap, str):
        return gap
    return f"{gap:+.1f}pp"


def _format_cost_ledger_row(row: dict) -> str:
    """Render one row dict as its markdown table line -- the exact inverse
    of _parse_cost_ledger_row_cells."""
    gap_s = _format_reviewer_gap_cell(row["reviewer_gap_pp"], unmeasured="")
    cells = [
        row["week"], row["machine"], row["rates"],
        f"{row['usd']:.2f}", f"{row['context_pct']:.1f}%", f"{row['opus_pct']:.1f}%",
        f"{row['ge200k_pct']:.1f}%", str(row["denials"]), gap_s, row["note"],
    ]
    return "| " + " | ".join(cells) + " |"


def _upsert_cost_ledger_row(existing_rows: list[dict], new_row: dict, force: bool) -> list[dict]:
    """Insert new_row into existing_rows, keyed by (week, machine).

    Refuses (raises ValueError) when a row for that key already exists and
    force is False -- a week's numbers change as the week fills, and
    silently rewriting history is how a ledger stops being one. With force,
    replaces that row in place; every other row's order and content is
    untouched.
    """
    key = (new_row["week"], new_row["machine"])
    for i, row in enumerate(existing_rows):
        if (row["week"], row["machine"]) == key:
            if not force:
                raise ValueError(
                    f"a row for week={new_row['week']} machine={new_row['machine']} already exists"
                    " -- pass --force to overwrite it"
                )
            return [*existing_rows[:i], new_row, *existing_rows[i + 1:]]
    return [*existing_rows, new_row]


def _write_cost_ledger_file(ledger_path: Path, preamble: str, rows: list[dict]) -> None:
    """Crash-safe write: render to a temp file in the ledger's own directory,
    read it back to confirm the write landed intact and parses on the
    canonical parser, then atomically replace the ledger file -- a killed
    process leaves either the old file intact or an orphaned temp file,
    never a half-written row the next run's duplicate check could misread.

    The read-back is a byte-equality check against the text just written,
    not a round trip through row dicts: _format_cost_ledger_row rounds usd
    to cents and percentages to one decimal, so a row's raw float and its
    formatted-then-reparsed value legitimately differ -- that is expected
    precision loss, not a serialization bug, and comparing rows would
    refuse almost every real (non-round-number) row.

    The temp file is chmod'd to the existing ledger file's permission bits
    before the replace -- tempfile.mkstemp creates it 0600, and os.replace
    swaps that mode in along with the content, silently downgrading the
    ledger file's existing permissions on every --record otherwise. If
    ledger_path does not exist yet (this --record is the first ever run
    against it), the chmod is skipped and tempfile.mkstemp's own 0600
    default is left in place.
    """
    new_text = preamble + "\n".join(_format_cost_ledger_row(r) for r in rows) + ("\n" if rows else "")
    fd, tmp_name = tempfile.mkstemp(dir=str(ledger_path.parent), prefix=".cost-ledger-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(new_text)
        written_text = Path(tmp_name).read_text()
        if written_text != new_text:
            raise _CostLedgerParseError("write verification mismatch -- refusing to publish")
        _parse_cost_ledger_file_text(written_text)  # fails loud on the canonical parser before publishing
        if ledger_path.exists():
            os.chmod(tmp_name, stat.S_IMODE(ledger_path.stat().st_mode))
        os.replace(tmp_name, ledger_path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def _reviewer_gap_pp(agg2: dict[tuple[str, str], dict[str, int]]) -> float | str | None:
    """Percentage-point gap between the findings-found and zero-finding
    cited-path edit rates, aggregated across every reviewer agent type --
    cost-ledger's reviewer_gap_pp column. None (left empty in the row) when
    either side's Active denominator is zero, rather than dividing by zero
    or silently substituting 0%. _REVIEWER_YIELD_INSUFFICIENT when either
    side's Active denominator is nonzero but below _REVIEWER_YIELD_ACTIVE_FLOOR
    -- the same low-confidence signal reviewer-yield's own per-bucket rate
    already reports, rather than an ordinary number with no distinguishing
    signal.
    """
    findings_active = sum(
        v["active"] for (_stype, bucket), v in agg2.items() if bucket == reviewer_yield._REVIEWER_VERDICT_FINDINGS_FOUND
    )
    findings_edited = sum(
        v["edited"] for (_stype, bucket), v in agg2.items() if bucket == reviewer_yield._REVIEWER_VERDICT_FINDINGS_FOUND
    )
    zero_active = sum(
        v["active"] for (_stype, bucket), v in agg2.items() if bucket == reviewer_yield._REVIEWER_VERDICT_ZERO_FINDING
    )
    zero_edited = sum(
        v["edited"] for (_stype, bucket), v in agg2.items() if bucket == reviewer_yield._REVIEWER_VERDICT_ZERO_FINDING
    )
    if findings_active == 0 or zero_active == 0:
        return None
    if (
        findings_active < reviewer_yield._REVIEWER_YIELD_ACTIVE_FLOOR
        or zero_active < reviewer_yield._REVIEWER_YIELD_ACTIVE_FLOOR
    ):
        return reviewer_yield._REVIEWER_YIELD_INSUFFICIENT
    return 100 * (findings_edited / findings_active - zero_edited / zero_active)


_COST_LEDGER_READ_HEADER = (
    f"{'Week':<10} {'Machine':<9} {'Rates':<11} {'$':>12} {'Context%':>9} "
    f"{'Opus%':>7} {'>=200k%':>8} {'Denials':>8} {'GapPP':>12}  Note"
)


def _format_cost_ledger_read_row(row: dict) -> str:
    """Render one row dict as a fixed-width terminal line for read mode --
    distinct from _format_cost_ledger_row, which renders the file's own
    markdown-pipe format. Empty reviewer_gap_pp prints as the literal token
    "unmeasured" (never a blank cell) so every column stays a single
    whitespace-delimited token."""
    gap_s = _format_reviewer_gap_cell(row["reviewer_gap_pp"], unmeasured="unmeasured")
    note = row["note"] or "-"
    return (
        f"{row['week']:<10} {row['machine']:<9} {row['rates']:<11} {row['usd']:>12,.2f} "
        f"{row['context_pct']:>8.1f}% {row['opus_pct']:>6.1f}% {row['ge200k_pct']:>7.1f}% "
        f"{row['denials']:>8} {gap_s:>12}  {note}"
    )


def _print_cost_ledger_read(existing_rows: list[dict], args: argparse.Namespace, roots: Sequence[Path]) -> None:
    """Read-mode output: every existing ledger row, then any ISO week present
    in the live corpus that no row (for any machine) has captured yet -- the
    gap between "recorded" and "still recoverable" this ledger exists to close.
    """
    session_iter, scope_label = scope._resolve_project_scope(args, "cost-ledger", include_subagents=True, roots=roots)
    scope.print_resolved_scope("cost-ledger", scope_label, roots)

    if not existing_rows:
        print("\nNo rows recorded yet.")
    else:
        print()
        print(_COST_LEDGER_READ_HEADER)
        print("-" * len(_COST_LEDGER_READ_HEADER))
        for row in existing_rows:
            print(_format_cost_ledger_read_row(row))

    cost_weeks, _unpriced_turns, _unpriced_tokens = cost.compute_cost_trend_data(session_iter)
    recorded_weeks = {row["week"] for row in existing_rows}
    missing_weeks = sorted(w for w in cost_weeks if w not in recorded_weeks)
    if missing_weeks:
        print("\nWeeks present in the live corpus with no ledger row yet:")
        for week_str in missing_weeks:
            print(f"  {week_str}")


def _acquire_cost_ledger_lock(lock_f) -> None:
    """Acquire an exclusive, non-blocking lock on lock_f, retrying at
    _COST_LEDGER_LOCK_POLL_INTERVAL_S intervals until
    _COST_LEDGER_LOCK_TIMEOUT_S elapses. Exits non-zero with a clear message
    rather than blocking indefinitely (fcntl.flock's plain LOCK_EX) on a
    wedged or long-running concurrent --record.
    """
    deadline = time.monotonic() + ledger_common._COST_LEDGER_LOCK_TIMEOUT_S
    while True:
        try:
            fcntl.flock(lock_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN):
                raise
            if time.monotonic() >= deadline:
                print(
                    "cost-ledger: another cost-ledger --record appears to be running"
                    " (lock held on the ledger's own .lock sibling file -- see"
                    " _cost_ledger_path()) -- timed out after"
                    f" {ledger_common._COST_LEDGER_LOCK_TIMEOUT_S:.0f}s",
                    file=sys.stderr,
                )
                sys.exit(1)
            time.sleep(ledger_common._COST_LEDGER_LOCK_POLL_INTERVAL_S)


def cmd_cost_ledger(args: argparse.Namespace) -> None:
    """CLI entry point for the cost-ledger subcommand.

    Reads the wall-clock date exactly once, here, then delegates to
    _cost_ledger_report, which takes `today` as an explicit parameter — the
    same split cmd_cost/cmd_cost_trend use so week-boundary logic is
    deterministic under test.
    """
    roots = scope.resolve_scan_roots(args)
    _cost_ledger_report(args, datetime.now(UTC).date(), roots)


def _default_cost_ledger_preamble() -> str:
    """Preamble for a ledger file --record creates fresh at a not-yet-existing
    path -- header/separator are byte-identical to the module constants so
    _parse_cost_ledger_file_text round-trips a freshly created file the same
    as an already-canonical one."""
    lines = [
        "# cost-ledger",
        "",
        "Weekly cost/efficiency snapshots recorded by `cost-ledger --record`.",
        _COST_LEDGER_HEADER_LINE,
        _COST_LEDGER_SEPARATOR_LINE,
    ]
    return "\n".join(lines) + "\n"


def _cost_ledger_report(args: argparse.Namespace, today: date, roots: Sequence[Path] | None = None) -> None:
    """Read (default) or append (--record) one row of the cost ledger at
    _cost_ledger_path().

    --record's row reuses cost.compute_cost_trend_data for usd/context_pct/
    opus_pct/ge200k_pct, and windows review_trace.compute_deny_summary_data/
    reviewer_yield.compute_reviewer_yield_data to the current ISO week's Monday-through-
    next-Monday UTC boundary for denials/reviewer_gap_pp — see
    docs/cost-ledger.md for why those two are per-week rather than
    corpus-lifetime figures. The corpus scan that computes the row runs
    unlocked; only the final read-check-write step (re-read the ledger,
    check for an existing (week, machine) row, write) holds an exclusive
    lock on a sibling .lock file (never the ledger file itself, so lock
    identity survives the atomic replace in _write_cost_ledger_file), so two
    racing recorders can't both pass the duplicate-row check.
    _acquire_cost_ledger_lock bounds the wait rather than blocking
    indefinitely on a wedged concurrent recorder.
    """
    record: bool = bool(getattr(args, "record", False))
    force: bool = bool(getattr(args, "force", False))
    note: str = getattr(args, "note", None) or ""

    try:
        ledger_path = _cost_ledger_path()
    except ValueError:
        # Not str(exc): _cost_ledger_path's own message embeds
        # COST_LEDGER_PATH's raw value, which can carry a home-rooted
        # engagement path. Same discipline as pr-cost-export's identical
        # catch.
        print("cost-ledger: COST_LEDGER_PATH must be an absolute path", file=sys.stderr)
        sys.exit(1)

    if roots is None:
        roots = scope.resolve_scan_roots(args)

    if not record:
        if not ledger_path.exists():
            # Omits the resolved path entirely -- it's home-rooted, and this
            # repo's own redaction convention treats home-rooted paths as
            # always-sensitive (command output here routinely gets pasted
            # into public issues).
            print(
                "cost-ledger: no ledger recorded here yet -- --record has never"
                " run against this path (or COST_LEDGER_PATH/CLAUDE_CONFIG_DIR"
                " resolves somewhere unexpected); see docs/cost-ledger.md",
                file=sys.stderr,
            )
            sys.exit(1)
        try:
            _preamble, existing_rows = _parse_cost_ledger_file_text(ledger_path.read_text())
        except _CostLedgerParseError as exc:
            print(f"cost-ledger: {exc}", file=sys.stderr)
            sys.exit(1)
        _print_cost_ledger_read(existing_rows, args, roots)
        return

    # No config_dir_override -- this is the single-account path, not the
    # --all-accounts loop below, which passes its own account_config_dir.
    try:
        cost_ledger_recording_enabled = _config.config_enabled("cost_ledger_recording")
    except _config.ConfigSchemaEmptyError:
        # config-keys.psv was read successfully but produced zero schema
        # rows (see that class's own docstring) -- distinct from the
        # unreadable-file case below, which this file was not.
        print(
            "cost-ledger: --record found config-keys.psv empty or malformed"
            " (no parseable schema rows) -- see docs/cost-ledger.md",
            file=sys.stderr,
        )
        sys.exit(1)
    except _config.ConfigSchemaRowTruncatedError:
        # cost_ledger_recording's own row is present but truncated after an
        # earlier column (see that class's own docstring) -- a torn schema
        # row, not a renamed or typo'd key literal at this call site.
        print(
            "cost-ledger: --record found cost_ledger_recording's config-keys.psv"
            " row truncated (partial stow-relink or interrupted git pull)"
            " -- see docs/cost-ledger.md",
            file=sys.stderr,
        )
        sys.exit(1)
    except KeyError as exc:
        if _config.schema():
            # config-keys.psv parsed fine (schema() returned rows), so this
            # KeyError is a real unknown-key bug -- a renamed or typo'd
            # literal at this call site -- not the infrastructure cause the
            # message below assumes.
            print(f"cost-ledger: --record: unknown config key {exc}", file=sys.stderr)
            sys.exit(1)
        # config-keys.psv itself was unreadable at the moment of this call
        # (see _config.py's module docstring) -- a partial stow-relink or
        # interrupted `git pull`, not a caller-side key-name typo.
        print(
            "cost-ledger: --record could not read config-keys.psv (partial stow-relink"
            " or interrupted git pull) -- see docs/cost-ledger.md",
            file=sys.stderr,
        )
        sys.exit(1)
    if cost_ledger_recording_enabled is None:
        # Distinguishes "config dir unresolvable" from "disabled" -- the
        # single message below would otherwise misdiagnose an unresolvable
        # CLAUDE_CONFIG_DIR as a missing opt-in, the same three-way branch
        # _cost_ledger_path() above already makes for its own ValueError.
        print(
            "cost-ledger: --record could not resolve the Claude Code config"
            " directory (CLAUDE_CONFIG_DIR is set to a relative path, or"
            " $HOME is unset/empty) -- see docs/cost-ledger.md",
            file=sys.stderr,
        )
        sys.exit(1)
    if not cost_ledger_recording_enabled:
        # Hardcodes the conventional ~/.claude path rather than the resolved
        # config dir -- same don't-print-a-resolved-home-rooted-path
        # discipline as the ledger-file-missing message above, applied to a
        # fixed docstring instead of an f-string since CLAUDE_CONFIG_DIR
        # overrides are rare enough that the canonical hint reads clearer.
        print(
            "cost-ledger: --record requires the opt-in sentinel ~/.claude/.cost-ledger-enabled"
            " -- see docs/cost-ledger.md",
            file=sys.stderr,
        )
        sys.exit(1)

    if len(roots) > 1 and ledger_common._ledger_path_is_git_tracked(ledger_path):
        # --record writes to a single resolved ledger path; unioning multiple
        # declared accounts into that one write only risks silently
        # committing/pushing one account's figures if the write actually
        # lands somewhere git could commit it -- non-record reads still
        # return the union regardless. Doesn't echo ledger_path itself,
        # matching this function's other home-rooted-path redaction above.
        print(
            "cost-ledger: --record is refused when more than one root is in"
            " scope and the ledger path is inside a git working tree; scope"
            " to a single account (--config-dir, or a roots file declaring"
            " only this account), move COST_LEDGER_PATH outside git, or drop"
            " --record",
            file=sys.stderr,
        )
        sys.exit(2)

    # Resolved only after the sentinel and git-tracked checks above, so a
    # run never creates a machine-id file inside a config dir whose owner
    # never opted in to --record.
    # See pr-cost's equivalent call for the same ordering.
    machine = ledger_common._resolve_machine_identity("cost-ledger")

    note_violation = _cost_ledger_note_violation(note)
    if note_violation is not None:
        print(f"cost-ledger: --note {note_violation}", file=sys.stderr)
        sys.exit(1)

    iso = today.isocalendar()
    week_str = f"{iso.year}-W{iso.week:02d}"
    monday = date.fromisocalendar(iso.year, iso.week, 1)
    week_start_ts = datetime(monday.year, monday.month, monday.day, tzinfo=UTC).timestamp()
    week_end_ts = week_start_ts + 7 * 86400

    cost_session_iter, scope_label = scope._resolve_project_scope(args, "cost-ledger", include_subagents=True, roots=roots)
    # All three iterators below deliberately duplicate the identical full-corpus
    # include_subagents=True scan rather than sharing one materialized pass —
    # doing so would require restructuring cost.compute_cost_trend_data,
    # review_trace.compute_deny_summary_data, and reviewer_yield.compute_reviewer_yield_data's calling
    # convention. Other commands also use that same calling convention.
    # deny_session_iter widens to match its siblings so its denials column and
    # review-trace --deny-summary (also include_subagents=True) can never
    # disagree over the same scope.
    deny_session_iter, _scope_label2 = scope._resolve_project_scope(args, "cost-ledger", include_subagents=True, roots=roots)
    reviewer_session_iter, _scope_label3 = scope._resolve_project_scope(
        args, "cost-ledger", include_subagents=True, roots=roots
    )
    scope.print_resolved_scope("cost-ledger", scope_label, roots)

    cost_weeks, _unpriced_turns, _unpriced_tokens = cost.compute_cost_trend_data(cost_session_iter)

    if week_str not in cost_weeks:
        print(
            f"cost-ledger: no priced turns found for the current week ({week_str});"
            " refusing to record a blank/zero row",
            file=sys.stderr,
        )
        sys.exit(1)

    max_observed_week = max(cost_weeks)
    if max_observed_week != week_str:
        print(
            f"cost-ledger: clock skew detected -- the corpus's most recent activity is dated"
            f" {max_observed_week}, but this machine's clock resolves the current week as"
            f" {week_str}; refusing to record under a possibly-wrong week label",
            file=sys.stderr,
        )
        sys.exit(1)

    week_data = cost_weeks[week_str]
    # Two distinct metrics: context_pct is the context-class (cache_read +
    # both cache_write tiers) dollar share of the week's total, GH-554 F1's
    # "context is ~88% of the bill" thesis; ge200k_pct is cost-trend's own
    # existing >=200k-context-bucket dollar share (_context_bucket) -- a
    # different corpus slice, not a second name for the same number.
    context_share = render._pct_value(week_data["context_class_dollars"], week_data["total"])
    ge200k_share = render._pct_value(week_data["context_over"], week_data["total"])
    opus_share = render._pct_value(week_data["opus"], week_data["total"])

    deny_data = review_trace.compute_deny_summary_data(deny_session_iter, since_ts=week_start_ts, until_ts=week_end_ts)
    denials = sum(deny_data["hook_counts"].values())

    reviewer_data = reviewer_yield.compute_reviewer_yield_data(
        reviewer_session_iter, since_ts=week_start_ts, until_ts=week_end_ts
    )
    reviewer_gap_pp = _reviewer_gap_pp(reviewer_data["agg2"])
    pricing._warn_if_subagent_format_drift(reviewer_data["subagent_spawns"], reviewer_data["sidechain_turns"])

    new_row = {
        "week": week_str,
        "machine": machine,
        "rates": pricing._PRICING_FETCH_DATE.isoformat(),
        "usd": week_data["total"],
        "context_pct": context_share,
        "opus_pct": opus_share,
        "ge200k_pct": ge200k_share,
        "denials": denials,
        "reviewer_gap_pp": reviewer_gap_pp,
        "note": note,
    }

    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = ledger_path.with_name(ledger_path.name + ".lock")
    with open(lock_path, "w") as lock_f:
        _acquire_cost_ledger_lock(lock_f)
        try:
            try:
                preamble, existing_rows = _parse_cost_ledger_file_text(ledger_path.read_text())
            except FileNotFoundError:
                # Treated uniformly as "never recorded here" -- doesn't
                # distinguish a dangling symlink or an externally-removed
                # directory from a genuinely fresh path; both are edge cases
                # requiring external tampering, not a normal --record flow.
                preamble, existing_rows = _default_cost_ledger_preamble(), []
            except _CostLedgerParseError as exc:
                print(f"cost-ledger: {exc}", file=sys.stderr)
                sys.exit(1)

            ledger_common._warn_machine_identity_absent_from_ledger("cost-ledger", machine, existing_rows)

            try:
                new_rows = _upsert_cost_ledger_row(existing_rows, new_row, force)
            except ValueError as exc:
                print(f"cost-ledger: {exc}", file=sys.stderr)
                sys.exit(1)

            try:
                _write_cost_ledger_file(ledger_path, preamble, new_rows)
            except _CostLedgerParseError as exc:
                print(f"cost-ledger: {exc}", file=sys.stderr)
                sys.exit(1)
        finally:
            fcntl.flock(lock_f, fcntl.LOCK_UN)

    print(f"cost-ledger: recorded {week_str} / {machine}")
