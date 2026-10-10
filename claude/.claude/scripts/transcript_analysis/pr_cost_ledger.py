"""The pr-cost ledger's on-disk format: column schema, status and join-confidence enums, path resolution, canonical parser and
formatter, append-only upsert, crash-safe write, and the --record lock.

Imports ledger_common by module (attribute access, not by name) -- see scope.py's own top-of-file comment for why."""
from __future__ import annotations

import contextlib
import errno
import fcntl
import json
import math
import os
import re
import stat
import sys
import tempfile
import time
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from _config_dir import config_dir
from transcript_analysis import ledger_common

# Exact column order of pre-model_breakdown ledgers; never edit, and never revert to code that lacks it.
_PR_COST_LEDGER_PRE_MODEL_COLUMNS: tuple[str, ...] = (
    # Key.
    "host", "repo", "pr_number", "machine",
    # Identity / provenance.
    "head_branch", "merged_at", "rate_stamp", "captured_at",
    "join_confidence", "supersedes", "status",
    # Dollars and tokens by class, in _TOKEN_CLASSES order.
    "cache_read_usd", "cache_write_5m_usd", "cache_write_1h_usd", "output_usd", "input_usd",
    "cache_read_tokens", "cache_write_5m_tokens", "cache_write_1h_tokens", "output_tokens", "input_tokens",
    "unpriced_turns", "unpriced_tokens",
    "turn_count", "session_count",
    "opus_dollars", "opus_dollar_share_pct",
    "sum_context_at_turn", "mean_context_at_turn",
    # gh-sourced PR size/rework.
    "additions", "deletions", "changed_files", "commit_count", "review_comment_count",
    # Mechanical review-surface proxies -- configurable, with claude-config defaults.
    "distinct_top_level_dirs", "distinct_file_extensions",
    "tests_changed", "plan_file_added", "risk_surface_flag",
)
_PR_COST_LEDGER_COLUMNS: tuple[str, ...] = (
    *_PR_COST_LEDGER_PRE_MODEL_COLUMNS,
    # Empty cell: not recorded. `{}`: recorded, no priced turns.
    "model_breakdown",
)
_PR_COST_LEDGER_HEADER_LINE = "\t".join(_PR_COST_LEDGER_COLUMNS)

# 'Legacy' in these names means the pre-host header only; the pre-model header is _PR_COST_LEDGER_PRE_MODEL_COLUMNS.
# Legacy header (no "host" column): every row under it is implicitly
# _PR_COST_LEDGER_LEGACY_HOST_DEFAULT.
if _PR_COST_LEDGER_PRE_MODEL_COLUMNS[0] != "host":  # the slice below assumes this position; `assert` would
    raise RuntimeError("_PR_COST_LEDGER_PRE_MODEL_COLUMNS[0] must be 'host'")  # vanish under python -O
_PR_COST_LEDGER_LEGACY_COLUMNS: tuple[str, ...] = _PR_COST_LEDGER_PRE_MODEL_COLUMNS[1:]
_PR_COST_LEDGER_LEGACY_HEADER_LINE = "\t".join(_PR_COST_LEDGER_LEGACY_COLUMNS)
_PR_COST_LEDGER_LEGACY_HOST_DEFAULT = "github.com"

# Every recognized header line, mapped to that header's own column tuple.
# _parse_pr_cost_ledger_file_text reads a row under its file's header, then
# fills each current column the header lacks from _PR_COST_LEDGER_COLUMN_DEFAULTS
# -- see docs/pr-cost.md's backward-compat contract for a new column.
_PR_COST_LEDGER_COLUMNS_BY_HEADER_LINE: dict[str, tuple[str, ...]] = {
    "\t".join(columns): columns
    for columns in (_PR_COST_LEDGER_COLUMNS, _PR_COST_LEDGER_PRE_MODEL_COLUMNS, _PR_COST_LEDGER_LEGACY_COLUMNS)
}
# Cell text for each column added after the first header, read by name for a row whose header lacks it.
_PR_COST_LEDGER_COLUMN_DEFAULTS: dict[str, str] = {
    "host": _PR_COST_LEDGER_LEGACY_HOST_DEFAULT,
    "model_breakdown": "",
}

_PR_COST_FLOAT_COLUMNS = (
    "cache_read_usd", "cache_write_5m_usd", "cache_write_1h_usd", "output_usd", "input_usd",
    "opus_dollars", "opus_dollar_share_pct", "mean_context_at_turn",
)
# Excludes pr_number, part of the key and parsed separately alongside repo/machine.
_PR_COST_INT_COLUMNS = (
    "cache_read_tokens", "cache_write_5m_tokens", "cache_write_1h_tokens", "output_tokens", "input_tokens",
    "unpriced_turns", "unpriced_tokens", "turn_count", "session_count", "sum_context_at_turn",
    "additions", "deletions", "changed_files", "commit_count", "review_comment_count",
    "distinct_top_level_dirs", "distinct_file_extensions",
)
_PR_COST_BOOL_COLUMNS = ("tests_changed", "plan_file_added", "risk_surface_flag")
_PR_COST_JSON_COLUMNS = ("model_breakdown",)

# status is a fixed enum carrying no embedded gh diagnostic text --
# _GH_CALL_DEGRADED_AUTH and _GH_CALL_DEGRADED_HOST_MISMATCH from
# _gh_call_with_backoff both fold into _PR_COST_STATUS_DEGRADED_NETWORK
# here, since a mid-run auth or local-misconfiguration failure and a
# generic transient one both just mean "this row's enrichment is
# incomplete," not distinguishable data states.
_PR_COST_STATUS_OK = "ok"
_PR_COST_STATUS_DEGRADED_RATE_LIMIT = "degraded_rate_limit"
_PR_COST_STATUS_DEGRADED_NETWORK = "degraded_network"
_PR_COST_STATUS_VALUES = (_PR_COST_STATUS_OK, _PR_COST_STATUS_DEGRADED_RATE_LIMIT, _PR_COST_STATUS_DEGRADED_NETWORK)

# "high": direct headRefName match corroborated by plan-slug or SHA overlap.
# "medium": direct match, uncorroborated. "low": resolved only via the
# branch-reuse tie-break (highest commit-SHA overlap, most recent
# mergedAt), or unresolved (no row written).
_PR_COST_JOIN_CONFIDENCE_HIGH = "high"
_PR_COST_JOIN_CONFIDENCE_MEDIUM = "medium"
_PR_COST_JOIN_CONFIDENCE_LOW = "low"
_PR_COST_JOIN_CONFIDENCE_VALUES = (
    _PR_COST_JOIN_CONFIDENCE_HIGH, _PR_COST_JOIN_CONFIDENCE_MEDIUM, _PR_COST_JOIN_CONFIDENCE_LOW,
)


class _PrCostLedgerParseError(Exception):
    """Raised by _parse_pr_cost_ledger_file_text on any malformed pr-cost
    ledger content -- the canonical parser fails loud rather than mis-parsing
    a hand-edited or corrupted row."""


def _pr_cost_ledger_path(config_dir_override: Path | None = None) -> Path:
    """Active pr-cost ledger path: $PR_COST_LEDGER_PATH if set (must be
    absolute), else (config_dir_override or config_dir()) / "pr-cost-ledger.tsv".
    config_dir_override lets --all-accounts resolve each account's own
    ledger path without reassigning the process-wide CLAUDE_CONFIG_DIR."""
    override = os.environ.get("PR_COST_LEDGER_PATH")
    if override:
        path = Path(override)
        if not path.is_absolute():
            raise ValueError(f"PR_COST_LEDGER_PATH must be an absolute path, got: {override!r}")
        return path
    return (config_dir_override or config_dir()) / "pr-cost-ledger.tsv"


# Persisted contract: a cell outside this key rule, these leaf keys, or this depth makes every checkout with this codec refuse
# the ledger. Never reshape model_breakdown; a new cell shape is a new appended column under a new frozen header. New variant
# and class labels need neither, because they are checked for key shape only.
_MODEL_BREAKDOWN_KEY_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_MODEL_BREAKDOWN_LEAF_KEYS = frozenset({"tokens", "usd_micros"})


class _DuplicateJsonKeyError(Exception):
    """Raised by _reject_duplicate_json_keys; not a ValueError, so it stays
    distinguishable from json.loads's own decode errors."""


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise _DuplicateJsonKeyError
    return dict(pairs)


def _model_breakdown_cell_violation(decoded: object) -> str | None:
    """The fixed rule text for the first structural violation in a decoded
    model_breakdown value, else None. Structure only: no membership,
    class-completeness, or cross-column check runs here."""
    if not isinstance(decoded, dict):
        return "top level must be an object"
    for model, variants in decoded.items():
        if not _MODEL_BREAKDOWN_KEY_RE.fullmatch(model):
            return "key outside the allowed key shape"
        if not isinstance(variants, dict) or not variants:
            return "model value must be a non-empty object"
        for variant, classes in variants.items():
            if not _MODEL_BREAKDOWN_KEY_RE.fullmatch(variant):
                return "key outside the allowed key shape"
            if not isinstance(classes, dict) or not classes:
                return "variant value must be a non-empty object"
            for token_class, leaf in classes.items():
                if not _MODEL_BREAKDOWN_KEY_RE.fullmatch(token_class):
                    return "key outside the allowed key shape"
                if not isinstance(leaf, dict) or set(leaf) != _MODEL_BREAKDOWN_LEAF_KEYS:
                    return "class value must be a leaf with exactly the keys tokens and usd_micros"
                for leaf_value in leaf.values():
                    if isinstance(leaf_value, bool) or not isinstance(leaf_value, int) or leaf_value < 0:
                        return "leaf values must be non-negative integers"
    return None


def _encode_model_breakdown_cell(value: dict) -> str:
    """The one canonical encoding of a decoded model_breakdown value."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _decode_model_breakdown_cell(cell: str, line_no: int) -> dict | None:
    """Decode one model_breakdown cell: "" is None (not recorded), anything
    else must be structurally valid JSON (see _model_breakdown_cell_violation).
    Preserves values, accepting whitespace and key-order variants -- the
    formatter re-encodes canonically on the next write. Every message names a
    fixed rule, never cell text, and is raised outside the except clauses
    below so no cell text reaches __context__."""
    if cell == "":
        return None
    decoded: object = None
    rule: str | None = None
    try:
        decoded = json.loads(cell, object_pairs_hook=_reject_duplicate_json_keys)
    except _DuplicateJsonKeyError:
        rule = "duplicate key"
    except RecursionError:
        rule = "nesting too deep"
    except ValueError:
        rule = "not valid JSON"
    if rule is None:
        rule = _model_breakdown_cell_violation(decoded)
    if rule is not None:
        raise _PrCostLedgerParseError(f"line {line_no}: malformed model_breakdown ({rule})")
    return decoded


def _parse_pr_cost_ledger_row_cells(cells: list[str], line_no: int) -> dict:
    """Validate and coerce one already-split, already-tab-separated data
    row's cells into a typed row dict. Raises _PrCostLedgerParseError naming
    the offending line on any field that doesn't match its column's
    contract. Never embeds a cell's raw value in this message, since
    pr-cost-export echoes it to stderr where another account's concurrent
    session may be watching."""
    if len(cells) != len(_PR_COST_LEDGER_COLUMNS):
        raise _PrCostLedgerParseError(
            f"line {line_no}: expected {len(_PR_COST_LEDGER_COLUMNS)} columns, got {len(cells)}"
        )
    row = dict(zip(_PR_COST_LEDGER_COLUMNS, cells, strict=True))

    if not row["host"] or row["host"] != row["host"].lower():
        raise _PrCostLedgerParseError(f"line {line_no}: malformed host value (must be lowercase)")
    if not row["repo"] or row["repo"] != row["repo"].lower():
        raise _PrCostLedgerParseError(f"line {line_no}: malformed repo value (must be lowercase owner/name)")
    try:
        row["pr_number"] = int(row["pr_number"])
    except ValueError:
        raise _PrCostLedgerParseError(f"line {line_no}: non-numeric pr_number") from None
    if not ledger_common._MACHINE_LABEL_RE.match(row["machine"]):
        raise _PrCostLedgerParseError(f"line {line_no}: malformed machine label")
    for required_col in ("head_branch", "merged_at", "captured_at"):
        if not row[required_col]:
            raise _PrCostLedgerParseError(f"line {line_no}: {required_col} must not be empty")
    # _latest_pr_cost_row picks the "latest" row via a lexicographic string
    # max() on captured_at, which silently misresolves on a malformed ISO8601 value.
    for ts_col in ("merged_at", "captured_at"):
        try:
            datetime.fromisoformat(row[ts_col].replace("Z", "+00:00"))
        except ValueError:
            raise _PrCostLedgerParseError(f"line {line_no}: malformed {ts_col}") from None
    try:
        datetime.strptime(row["rate_stamp"], "%Y-%m-%d")
    except ValueError:
        raise _PrCostLedgerParseError(f"line {line_no}: malformed rate_stamp") from None
    if row["join_confidence"] not in _PR_COST_JOIN_CONFIDENCE_VALUES:
        raise _PrCostLedgerParseError(f"line {line_no}: unknown join_confidence")
    if row["status"] not in _PR_COST_STATUS_VALUES:
        raise _PrCostLedgerParseError(f"line {line_no}: unknown status")

    for float_col in _PR_COST_FLOAT_COLUMNS:
        try:
            row[float_col] = float(row[float_col])
        except ValueError:
            raise _PrCostLedgerParseError(f"line {line_no}: non-numeric {float_col}") from None
        if math.isnan(row[float_col]) or math.isinf(row[float_col]):
            raise _PrCostLedgerParseError(f"line {line_no}: non-finite {float_col}")

    for int_col in _PR_COST_INT_COLUMNS:
        try:
            row[int_col] = int(row[int_col])
        except ValueError:
            raise _PrCostLedgerParseError(f"line {line_no}: non-numeric {int_col}") from None

    for bool_col in _PR_COST_BOOL_COLUMNS:
        if row[bool_col] not in ("true", "false"):
            raise _PrCostLedgerParseError(f"line {line_no}: malformed {bool_col} (expected true/false)")
        row[bool_col] = row[bool_col] == "true"

    row["model_breakdown"] = _decode_model_breakdown_cell(row["model_breakdown"], line_no)
    return row


def _current_cells_from_file_row(cells: list[str], file_columns: tuple[str, ...], line_no: int) -> list[str]:
    """One data row's cells, split under its own file header `file_columns`,
    reordered by name into current column order with each column that header
    lacks filled from _PR_COST_LEDGER_COLUMN_DEFAULTS."""
    if len(cells) != len(file_columns):
        message = f"line {line_no}: expected {len(file_columns)} columns, got {len(cells)}"
        if "\t".join(file_columns) == _PR_COST_LEDGER_HEADER_LINE and len(cells) == len(file_columns) - 1:
            message += " (if an editor trimmed trailing whitespace, append a tab to this line)"
        raise _PrCostLedgerParseError(message)
    cell_by_column = dict(zip(file_columns, cells, strict=True))
    return [
        cell_by_column[column] if column in cell_by_column else _PR_COST_LEDGER_COLUMN_DEFAULTS[column]
        for column in _PR_COST_LEDGER_COLUMNS
    ]


def _parse_pr_cost_ledger_file_text(text: str) -> list[dict]:
    """Canonical parser for the pr-cost ledger's tab-separated content.

    Unlike the weekly cost-ledger's markdown table, this format has no
    preamble: line 1 must be exactly one of the headers in
    _PR_COST_LEDGER_COLUMNS_BY_HEADER_LINE (the current one or a frozen
    older one -- see that table's own comment), and every following
    non-blank line is one tab-separated data row, checked against its own
    file's header width. Fails loud
    (_PrCostLedgerParseError) on an unresolved git merge-conflict marker
    (reusing ledger_common._COST_LEDGER_CONFLICT_MARKERS -- a generic git marker, not
    specific to the weekly ledger's own format), a missing/mismatched
    header, or a row with the wrong column count or a malformed cell --
    never silently misparses a malformed or hand-edited row.
    """
    lines = text.splitlines()
    for marker in ledger_common._COST_LEDGER_CONFLICT_MARKERS:
        for line_no, line in enumerate(lines, start=1):
            if line.startswith(marker):
                raise _PrCostLedgerParseError(f"line {line_no}: unresolved merge-conflict marker {marker!r}")

    file_columns = _PR_COST_LEDGER_COLUMNS_BY_HEADER_LINE.get(lines[0]) if lines else None
    if file_columns is None:
        raise _PrCostLedgerParseError(
            "missing or mismatched pr-cost ledger header row (if a newer claude-config wrote this file,"
            " update this checkout -- see docs/pr-cost.md in the claude-config repo)"
        )

    rows: list[dict] = []
    for line_no, line in enumerate(lines[1:], start=2):
        if not line.strip():
            continue
        cells = _current_cells_from_file_row(line.split("\t"), file_columns, line_no)
        rows.append(_parse_pr_cost_ledger_row_cells(cells, line_no))
    return rows


def _format_pr_cost_ledger_row(row: dict, *, columns: Sequence[str] = _PR_COST_LEDGER_COLUMNS) -> str:
    """Render one row dict as its tab-separated line -- the exact inverse of
    _parse_pr_cost_ledger_row_cells. Refuses (raises _PrCostLedgerParseError)
    to render any cell containing a tab or newline, which would corrupt the
    row's own column structure -- every free-text-shaped cell here is
    program-generated (a redacted placeholder, or an ISO8601 timestamp this
    module itself formatted), never raw external text, so this should never
    fire in practice; it exists as a last-resort guard against writing a
    corrupt row rather than as an expected code path. `columns` defaults to
    the ledger's own column tuple. pr-cost-export passes its own wider tuple
    to reuse this same tab/newline guard and bool/float rendering."""
    cells: list[str] = []
    for col in columns:
        value = row[col]
        if col in _PR_COST_BOOL_COLUMNS:
            cell = "true" if value else "false"
        elif col in _PR_COST_FLOAT_COLUMNS:
            cell = f"{value:.6f}"
        elif col in _PR_COST_JSON_COLUMNS:
            cell = "" if value is None else _encode_model_breakdown_cell(value)
        else:
            cell = str(value)
        if "\t" in cell or "\n" in cell or "\r" in cell:
            raise _PrCostLedgerParseError(f"column {col!r} contains a tab or newline -- refusing to write a corrupt row")
        cells.append(cell)
    return "\t".join(cells)


def _latest_pr_cost_row(
    rows: Sequence[dict], host: str, repo: str, pr_number: int, machine_label: str | None,
) -> dict | None:
    """Latest row (by captured_at) matching (host, repo, pr_number[, machine_label]).

    host and repo are compared as-is (no re-lowering here): both are
    case-folded by the caller before reaching this function (host via
    _git_remote_origin_host_and_owner_repo, repo via _resolve_pinned_gh_repo),
    and every stored row's own host/repo cells are validated lowercase by
    the parser -- the same convention _pr_cost_report's other identity
    comparisons already rely on. machine_label=None matches any machine --
    read mode's default (an operator checking "has any machine captured
    this PR yet" doesn't care which one); --record always passes its own
    resolved machine_label, matching the ledger's own (host, repo,
    pr_number, machine) key.
    """
    matches = [
        r for r in rows
        if r["host"] == host and r["repo"] == repo and r["pr_number"] == pr_number
        and (machine_label is None or r["machine"] == machine_label)
    ]
    if not matches:
        return None
    # Last appended wins on an equal captured_at (the index tiebreaks max()).
    return max(enumerate(matches), key=lambda p: (p[1]["captured_at"], p[0]))[1]


def _append_pr_cost_ledger_row(existing_rows: list[dict], new_row: dict, already: dict | None, force: bool) -> list[dict]:
    """Append new_row to existing_rows, keyed by (host, repo, pr_number, machine).

    Unlike the weekly ledger's in-place replace, a duplicate key with
    --force APPENDS a new row (carrying new_row["supersedes"], already set
    by the caller to the prior latest row's own captured_at) rather than
    overwriting -- this ledger is append-only by design, so a correction
    keeps the full history instead of losing everything before the latest
    edit. Refuses (raises ValueError) on a duplicate key without --force. `already`
    is the caller's own _latest_pr_cost_row lookup, passed in rather than
    re-derived here so there is one call site for that lookup per write.
    """
    if already is not None and not force:
        raise ValueError(
            f"a row for pr_number={new_row['pr_number']} machine={new_row['machine']}"
            " already exists -- pass --force with --pr to append a correcting row"
        )
    return [*existing_rows, new_row]


def _pr_cost_ledger_value_for_comparison(column: str, value):
    """A parsed cell value as the write postcondition compares it: floats at
    the ledger's own six-decimal precision, everything else (model_breakdown
    included, as its decoded value) as-is."""
    if column in _PR_COST_FLOAT_COLUMNS:
        return round(value, 6)
    return value


def _first_differing_pr_cost_column(prior_row: dict, staged_row: dict) -> str | None:
    """First column, in current column order, where the two parsed rows differ under
    _pr_cost_ledger_value_for_comparison, else None."""
    for column in _PR_COST_LEDGER_COLUMNS:
        if _pr_cost_ledger_value_for_comparison(column, prior_row[column]) != _pr_cost_ledger_value_for_comparison(
            column, staged_row[column]
        ):
            return column
    return None


_PR_COST_WRITE_REFUSED_PREFIX = "refusing to write the ledger (ledger unchanged): "
_PR_COST_WRITE_REFUSED_SUFFIX = (
    " -- a claude-config defect, or the ledger was edited during this run;"
    " rerun, and if it repeats on a current claude-config, report it"
)


def _refuse_if_prior_rows_not_preserved(prior_rows: list[dict], staged_rows: list[dict]) -> None:
    """Raises _PrCostLedgerParseError unless staged_rows' first len(prior_rows) rows equal prior_rows
    value-for-value. Names a 1-based data-row ordinal and a column, never a value."""
    differences = (
        (ordinal, column)
        for ordinal, (prior_row, staged_row) in enumerate(zip(prior_rows, staged_rows, strict=False), start=1)
        if (column := _first_differing_pr_cost_column(prior_row, staged_row)) is not None
    )
    first_difference = next(differences, None)
    if len(staged_rows) < len(prior_rows):
        first_lost_ordinal = first_difference[0] if first_difference else len(staged_rows) + 1
        raise _PrCostLedgerParseError(
            f"{_PR_COST_WRITE_REFUSED_PREFIX}the rewrite would drop prior data rows from {first_lost_ordinal} on"
            f"{_PR_COST_WRITE_REFUSED_SUFFIX}"
        )
    if first_difference:
        changed_ordinal, changed_column = first_difference
        raise _PrCostLedgerParseError(
            f"{_PR_COST_WRITE_REFUSED_PREFIX}the rewrite would change prior data row {changed_ordinal},"
            f" column {changed_column}{_PR_COST_WRITE_REFUSED_SUFFIX}"
        )


def _read_prior_pr_cost_ledger(ledger_path: Path) -> tuple[list[dict], bool]:
    """(rows, header_is_current) of the ledger already on disk; ([], True) when there is none, so
    creation reads as not-an-upgrade. FileNotFoundError means no prior file; any other OSError propagates."""
    try:
        prior_text = ledger_path.read_text()
    except FileNotFoundError:
        return [], True
    try:
        prior_rows = _parse_pr_cost_ledger_file_text(prior_text)
    except _PrCostLedgerParseError as exc:
        raise _PrCostLedgerParseError(
            f"{_PR_COST_WRITE_REFUSED_PREFIX}the ledger on disk changed during this run and no longer parses: {exc}"
        ) from None
    return prior_rows, prior_text.splitlines()[0] == _PR_COST_LEDGER_HEADER_LINE


def _write_pr_cost_ledger_file(ledger_path: Path, rows: list[dict]) -> bool:
    """Crash-safe write mirroring _write_cost_ledger_file's temp-file/
    read-back/atomic-replace pattern, adapted for this ledger's plain-TSV
    format (no markdown preamble) and its own 0600 creation mode -- these
    rows carry branch/repo data the public weekly ledger's rows don't, so a
    freshly created file gets 0600 explicitly (an existing file's mode bits
    are preserved instead, matching _write_cost_ledger_file's own rationale)
    rather than silently depending on tempfile.mkstemp's own default.

    Always rewrites the whole file under the current header. Refuses to
    publish (raises _PrCostLedgerParseError, ledger unchanged) unless the
    rewritten file's first N parsed rows equal the N rows the file held when
    this call began, floats compared at the ledger's six decimals. Returns
    True when it replaced a file whose header was not the current one, False
    otherwise (creation included).
    """
    prior_rows, prior_header_is_current = _read_prior_pr_cost_ledger(ledger_path)
    new_text = "\n".join([_PR_COST_LEDGER_HEADER_LINE] + [_format_pr_cost_ledger_row(r) for r in rows]) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=str(ledger_path.parent), prefix=".pr-cost-ledger-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(new_text)
        written_text = Path(tmp_name).read_text()
        if written_text != new_text:
            raise _PrCostLedgerParseError("write verification mismatch -- refusing to publish")
        try:
            staged_rows = _parse_pr_cost_ledger_file_text(written_text)  # fails loud on the canonical parser before publishing
        except _PrCostLedgerParseError as exc:
            raise _PrCostLedgerParseError(
                f"{_PR_COST_WRITE_REFUSED_PREFIX}the staged rewrite failed validation: {exc}"
            ) from None
        _refuse_if_prior_rows_not_preserved(prior_rows, staged_rows)
        if ledger_path.exists():
            os.chmod(tmp_name, stat.S_IMODE(ledger_path.stat().st_mode))
        else:
            os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, ledger_path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
    return not prior_header_is_current


def _acquire_pr_cost_ledger_lock(lock_f) -> None:
    """Acquire an exclusive, non-blocking lock on lock_f, retrying at
    ledger_common._COST_LEDGER_LOCK_POLL_INTERVAL_S intervals until
    ledger_common._COST_LEDGER_LOCK_TIMEOUT_S elapses -- the same local-lock convenience
    bound the weekly ledger uses (_acquire_cost_ledger_lock), reused here
    since it guards the same kind of wait (a local read-check-write window
    against another --record), not a network call.
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
                    "pr-cost: another pr-cost --record appears to be running (lock held on the"
                    " ledger's own .lock sibling file) -- timed out after"
                    f" {ledger_common._COST_LEDGER_LOCK_TIMEOUT_S:.0f}s",
                    file=sys.stderr,
                )
                sys.exit(1)
            time.sleep(ledger_common._COST_LEDGER_LOCK_POLL_INTERVAL_S)
