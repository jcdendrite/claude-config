"""The pr-cost-export command family: cmd_pr_cost_export and every helper used only by it -- collapsing every declared
account's pr-cost ledger to current rows, redacting them, and publishing one TSV. Makes no gh call and scans no transcript
corpus."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import os
import sys
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import _config
from _config_dir import declared_roots_file_is_overridden
from transcript_analysis import ledger_common, pr_cost_ledger, redaction, scope

# pr_cost_ledger._PR_COST_LEDGER_COLUMNS with a leading account column.
# head_branch is renamed head_branch_label: a stored label is per-run, not a
# stable branch identity, so re-tokenizing it needs a name that makes that
# visible.
# supersedes is replaced by correction_count: supersedes is a dangling
# pointer into a row the export no longer carries, so a derived count stands
# in for it instead.
_PR_COST_EXPORT_COLUMNS: tuple[str, ...] = ("account", *(
    {"head_branch": "head_branch_label", "supersedes": "correction_count"}.get(col, col)
    for col in pr_cost_ledger._PR_COST_LEDGER_COLUMNS
))
_PR_COST_EXPORT_HEADER_LINE = "\t".join(_PR_COST_EXPORT_COLUMNS)


def _pr_cost_export_date_only(value: str) -> str:
    """Date portion of an already-validated ISO8601 timestamp
    (YYYY-MM-DDTHH:MM:SS[Z|+HH:MM]). Validated by
    _parse_pr_cost_ledger_row_cells before this is ever called. Applies to
    merged_at/captured_at only. rate_stamp is already date-only and never
    passed through this."""
    return value.split("T", 1)[0]


def _collapse_pr_cost_rows_to_current(rows: Sequence[dict]) -> list[tuple[dict, int]]:
    """One (row, correction_count) pair per distinct (host, repo, pr_number,
    machine) key in `rows`, keeping only the current (latest by
    captured_at) row for each key -- the append-only ledger's full history
    collapsed to current state. Must run before tokenization: pr_number must
    stay a typed int. Must run before date-truncation: same-day ties need
    full-precision captured_at. correction_count is the number of other
    rows sharing that key (total captures minus one).
    """
    groups: dict[tuple[str, str, int, str], list[dict]] = {}
    for row in rows:
        key = (row["host"], row["repo"], row["pr_number"], row["machine"])
        groups.setdefault(key, []).append(row)
    collapsed: list[tuple[dict, int]] = []
    for (host, repo, pr_number, machine), group in groups.items():
        latest = pr_cost_ledger._latest_pr_cost_row(group, host, repo, pr_number, machine)
        collapsed.append((latest, len(group) - 1))
    return collapsed


def _redact_pr_cost_row_for_export(
    row: dict, ordinal: int, correction_count: int,
    host_map: dict, repo_map: dict, pr_map: dict, branch_map: dict, machine_map: dict,
) -> dict:
    """One collapsed ledger row rendered into _PR_COST_EXPORT_COLUMNS' own
    shape:

    - host/repo/pr_number/head_branch/machine are tokenized through five
      separate per-kind maps.
    - merged_at/captured_at are truncated to a date.
    - supersedes is replaced by the caller-computed correction_count.

    `row["head_branch"]` is re-tokenized here even though it already holds
    a redacted placeholder from the write path. That placeholder was
    assigned under whichever run's own ordinal scheme recorded it, so
    passing it through as-is would put two disagreeing account-K numberings
    in one row.

    `machine` is tokenized uniformly, whether it holds a tool-generated hex
    identity or a legacy operator-chosen `--machine-label` value -- both are
    potentially identifying, so neither passes through raw.
    """
    exported = dict(row)
    exported["account"] = f"account-{ordinal}"
    exported["host"] = redaction._assign_root_scoped_redact_label("host", ordinal, row["host"], host_map)
    exported["repo"] = redaction._assign_root_scoped_redact_label("repo", ordinal, row["repo"], repo_map)
    exported["pr_number"] = redaction._assign_root_scoped_redact_label("pr", ordinal, str(row["pr_number"]), pr_map)
    exported["head_branch_label"] = redaction._assign_root_scoped_redact_label(
        "branch", ordinal, row["head_branch"], branch_map
    )
    exported["machine"] = redaction._assign_root_scoped_redact_label("machine", ordinal, row["machine"], machine_map)
    exported["merged_at"] = _pr_cost_export_date_only(row["merged_at"])
    exported["captured_at"] = _pr_cost_export_date_only(row["captured_at"])
    exported["correction_count"] = correction_count
    del exported["head_branch"]
    del exported["supersedes"]
    return exported


def _pr_cost_export_rows(roots: Sequence[Path]) -> tuple[list[str], int, int, int, int, int, list[str]]:
    """Read every resolved root's own pr-cost ledger. Returns
    (formatted_rows, declared, opted_in, skipped_not_opted_in,
    legacy_header_accounts, legacy_machine_value_rows, corpus_identities),
    fully materialized, with no filesystem writes of its own. See
    docs/pr-cost.md's "Redacted cross-account export" section for account
    iteration order and corpus_identities' construction.
    """
    ordinals = scope._redaction_ordinals(roots)
    root_by_resolved = {root.resolve(): root for root in roots}
    declared = len(roots)
    opted_in = skipped_not_opted_in = legacy_header_accounts = legacy_machine_value_rows = 0
    formatted_rows: list[str] = []
    corpus_identities: list[str] = []
    host_map: dict[tuple[int, str], str] = {}
    repo_map: dict[tuple[int, str], str] = {}
    pr_map: dict[tuple[int, str], str] = {}
    branch_map: dict[tuple[int, str], str] = {}
    machine_map: dict[tuple[int, str], str] = {}

    for resolved_root in sorted(ordinals):
        ordinal = ordinals[resolved_root]
        account_config_dir = root_by_resolved[resolved_root].parent

        # Same call, and the same error handling, as --record's own opt-in
        # gate in pr_cost.py (see _config.py's _location_value for how
        # claude-config.toml and the legacy sentinel resolve against
        # each other).
        try:
            pr_cost_recording_enabled = _config.config_enabled(
                "pr_cost_recording", config_dir_override=account_config_dir
            )
        except _config.ConfigSchemaEmptyError:
            print(
                f"pr-cost-export: account-{ordinal}: config-keys.psv empty or malformed"
                " (no parseable schema rows) -- see docs/pr-cost.md",
                file=sys.stderr,
            )
            sys.exit(1)
        except _config.ConfigSchemaRowTruncatedError:
            print(
                f"pr-cost-export: account-{ordinal}: pr_cost_recording's config-keys.psv row is"
                " truncated (partial stow-relink or interrupted git pull) -- see docs/pr-cost.md",
                file=sys.stderr,
            )
            sys.exit(1)
        except KeyError as exc:
            if _config.schema():
                print(f"pr-cost-export: account-{ordinal}: unknown config key {exc}", file=sys.stderr)
                sys.exit(1)
            print(
                f"pr-cost-export: account-{ordinal}: could not read config-keys.psv (partial"
                " stow-relink or interrupted git pull) -- see docs/pr-cost.md",
                file=sys.stderr,
            )
            sys.exit(1)
        if pr_cost_recording_enabled is None:
            # account_config_dir is always concrete here (root.parent), so
            # this is not expected to be reachable in practice -- see
            # --record's identical branch in pr_cost.py for why it's still handled
            # explicitly rather than left to fail silently.
            print(
                f"pr-cost-export: account-{ordinal}'s config directory could not be resolved --"
                " see docs/pr-cost.md",
                file=sys.stderr,
            )
            sys.exit(1)
        if not pr_cost_recording_enabled:
            # account-N, not account_config_dir, to avoid a resolved
            # home-rooted path in output -- same discipline as pr-cost's own
            # --all-accounts skip message.
            # Worded generically for the same reason as pr-cost's
            # --all-accounts skip message in pr_cost.py.
            print(
                f"pr-cost-export: account-{ordinal} is not opted in (pr_cost_recording) --"
                " skipped, see docs/pr-cost.md",
                file=sys.stderr,
            )
            skipped_not_opted_in += 1
            continue
        opted_in += 1

        try:
            ledger_path = pr_cost_ledger._pr_cost_ledger_path(config_dir_override=account_config_dir)
        except ValueError:
            # Not str(exc): pr_cost_ledger._pr_cost_ledger_path's own message embeds
            # PR_COST_LEDGER_PATH's raw value, which can carry a home-rooted
            # engagement path. Same discipline as this function's other
            # account-N-only diagnostics above.
            print(
                f"pr-cost-export: account-{ordinal}: PR_COST_LEDGER_PATH must be an absolute path",
                file=sys.stderr,
            )
            sys.exit(1)
        if not ledger_path.exists():
            continue

        try:
            ledger_text = ledger_path.read_text()
        except OSError:
            print(f"pr-cost-export: account-{ordinal}: ledger file could not be read", file=sys.stderr)
            sys.exit(1)
        # splitlines(), not split("\n", 1), so a CRLF-terminated first line is
        # recognized the same way pr_cost_ledger._parse_pr_cost_ledger_file_text's own
        # canonical line-1 comparison recognizes it.
        ledger_lines = ledger_text.splitlines()
        if ledger_lines and ledger_lines[0] == pr_cost_ledger._PR_COST_LEDGER_LEGACY_HEADER_LINE:
            legacy_header_accounts += 1
        try:
            raw_rows = pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_text)
        except pr_cost_ledger._PrCostLedgerParseError as exc:
            print(f"pr-cost-export: account-{ordinal}: {exc}", file=sys.stderr)
            sys.exit(1)
        if not raw_rows:
            continue

        # Threaded through this same per-account loop rather than re-read from
        # each ledger a second time, to avoid double I/O and a result that
        # could disagree with the rows actually exported.
        corpus_identities.append(f"{raw_rows[0]['captured_at']}|{raw_rows[0]['machine']}")
        for row, correction_count in _collapse_pr_cost_rows_to_current(raw_rows):
            # Runs post-collapse, so a same-key multi-capture correction under
            # one legacy machine value is counted once here, not once per raw capture.
            if not ledger_common._MACHINE_IDENTITY_RE.match(row["machine"]):
                legacy_machine_value_rows += 1
            exported = _redact_pr_cost_row_for_export(
                row, ordinal, correction_count, host_map, repo_map, pr_map, branch_map, machine_map
            )
            try:
                formatted_rows.append(pr_cost_ledger._format_pr_cost_ledger_row(exported, columns=_PR_COST_EXPORT_COLUMNS))
            except pr_cost_ledger._PrCostLedgerParseError as exc:
                print(f"pr-cost-export: account-{ordinal}: {exc}", file=sys.stderr)
                sys.exit(1)

    return (
        formatted_rows, declared, opted_in, skipped_not_opted_in,
        legacy_header_accounts, legacy_machine_value_rows, corpus_identities,
    )


def _pr_cost_export_provenance_line(
    *, exported_at: datetime, declared: int, opted_in: int, skipped_not_opted_in: int,
    legacy_header_accounts: int, legacy_machine_value_rows: int, corpus_identities: Sequence[str],
    corpus_override: bool,
) -> str:
    """Builds the provenance line. corpus= is a same-corpus indicator only,
    never a security boundary -- see docs/pr-cost.md's "Redacted
    cross-account export" section for its construction, for what
    corpus_override=1 means, and for what legacy_machine_value_rows flags.
    """
    digest = hashlib.sha256("\n".join(sorted(corpus_identities)).encode()).hexdigest()[:12]
    exported_at_str = exported_at.isoformat(timespec="seconds").replace("+00:00", "Z")
    return (
        "# pr-cost-export DO-NOT-PUBLISH-no-tooling-enforces-this"
        f" exported_at={exported_at_str} declared={declared} opted_in={opted_in}"
        f" skipped_not_opted_in={skipped_not_opted_in} legacy_header_accounts={legacy_header_accounts}"
        f" legacy_machine_value_rows={legacy_machine_value_rows}"
        f" corpus={digest} corpus_override={int(corpus_override)}"
    )


def cmd_pr_cost_export(args: argparse.Namespace) -> None:
    """CLI entry point for the pr-cost-export subcommand -- a pure local
    file transform over every declared account's own pr-cost ledger, making
    no `gh` call and scanning no transcript corpus of its own. See
    docs/pr-cost.md's "Redacted cross-account export" section for the full
    grain and redaction contract.
    """
    out = getattr(args, "out", None)
    if not out:
        print(
            "pr-cost-export: --out PATH is required -- this subcommand never writes to stdout,"
            " since stdout inside a Claude Code session is captured into that session's own"
            " transcript",
            file=sys.stderr,
        )
        sys.exit(2)

    # Resolved (following any symlink at --out itself) so the git-tree check
    # below sees where the path really points.
    # For a dangling symlink, resolve() still reports that the target
    # doesn't exist.
    # This resolved path is never echoed to a diagnostic, since it can embed
    # a home-rooted engagement directory -- see _config_dir.py's own "the
    # path identifies an engagement" discipline.
    # Only the operator's own literal `out` string is echoed below.
    resolved_out = Path(out).resolve()
    # lexists() here buys nothing over exists(), since resolve() above
    # already followed every symlink. This check is therefore a UX-only
    # fast path that fails fast on the common case. The actual symlink
    # defense is below, at the unresolved open_path + os.link publish step.
    # That step raises FileExistsError on an existing destination rather
    # than dereferencing it.
    if os.path.lexists(str(resolved_out)):
        print(
            f"pr-cost-export: --out {out!r} already exists -- refusing to overwrite; pass a new path",
            file=sys.stderr,
        )
        sys.exit(2)
    # resolved_out is re-resolved a second, independent time at the terminal
    # os.open call below (open_path). A symlink retargeted between these two
    # checks is a known, accepted TOCTOU window for this tool's
    # single-operator threat model, not one this check closes.
    if ledger_common._ledger_path_is_git_tracked(resolved_out, "pr-cost-export"):
        print(
            f"pr-cost-export: --out {out!r} is inside a git working tree -- this repo (and every"
            " repo this subcommand might be run from) is potentially public; write outside any"
            " git working tree",
            file=sys.stderr,
        )
        sys.exit(2)

    roots = scope._resolve_cost_roots(args, "pr-cost-export")
    if len(roots) > 1 and os.environ.get("PR_COST_LEDGER_PATH"):
        # Mirrors pr-cost's own --all-accounts + PR_COST_LEDGER_PATH refusal:
        # with it set, every account would resolve to the same forced file,
        # exporting one ledger's rows once per account under a different
        # account token.
        print(
            "pr-cost-export: PR_COST_LEDGER_PATH is refused when more than one root resolves --"
            " unset PR_COST_LEDGER_PATH (each account then defaults to its own ledger path), or"
            " scope to a single profile (drop --config-dir)",
            file=sys.stderr,
        )
        sys.exit(2)

    # No _resolve_project_scope call here to derive a scope_label from --
    # this subcommand has no --this-repo/--projects flags.
    # "*" is the same literal every other subcommand's scope_label defaults
    # to absent those flags, so it is the accurate, unscoped label for every
    # declared account's ledger.
    scope.print_resolved_scope("pr-cost-export", "*", roots, file=sys.stderr)

    (
        formatted_rows, declared, opted_in, skipped_not_opted_in, legacy_header_accounts,
        legacy_machine_value_rows, corpus_identities,
    ) = _pr_cost_export_rows(roots)

    provenance_line = _pr_cost_export_provenance_line(
        exported_at=datetime.now(UTC), declared=declared, opted_in=opted_in,
        skipped_not_opted_in=skipped_not_opted_in, legacy_header_accounts=legacy_header_accounts,
        legacy_machine_value_rows=legacy_machine_value_rows, corpus_identities=corpus_identities,
        corpus_override=declared_roots_file_is_overridden(),
    )
    file_text = "\n".join([provenance_line, _PR_COST_EXPORT_HEADER_LINE, *formatted_rows]) + "\n"

    # The final path component is left exactly as named, unlike resolved_out
    # above, so the publish step's own symlink refusal (below) actually
    # fires instead of silently following the link to wherever it points.
    # The parent is still resolved, so a symlinked parent directory lands
    # inside the same target the git-tree check above already validated.
    open_path = Path(out).parent.resolve() / Path(out).name

    # Materialized into a same-directory temp file first, then published into
    # --out via a hard link, so a mid-write crash never leaves a truncated,
    # non-empty file stuck at the operator-named path with no sign it's crash
    # debris. Same mkstemp+os.link idiom as _resolve_machine_identity's own
    # publish step.
    #
    # `mkstemp` creates the temp file `0600`, and `os.link`'s new name shares
    # that same inode's mode. `--out` therefore ends up `0600` with no
    # separate `chmod` needed.
    tmp_name: str | None = None
    try:
        tmp_fd, tmp_name = tempfile.mkstemp(dir=str(open_path.parent), prefix=".pr-cost-export-", suffix=".tmp")
        with os.fdopen(tmp_fd, "w") as f:
            f.write(file_text)
    except OSError:
        if tmp_name is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
        print(
            f"pr-cost-export: --out {out!r} could not be written -- pass a new path",
            file=sys.stderr,
        )
        sys.exit(2)
    try:
        try:
            os.link(tmp_name, open_path)
        except FileExistsError:
            print(
                f"pr-cost-export: --out {out!r} already exists -- refusing to overwrite; pass a new path",
                file=sys.stderr,
            )
            sys.exit(2)
        except OSError:
            # Not over-specified to "parent directory missing or
            # unwritable": os.link can also fail this way on EXDEV (--out on
            # a different filesystem than the temp file) or ENOSPC.
            print(
                f"pr-cost-export: --out {out!r} could not be published; pass a new path",
                file=sys.stderr,
            )
            sys.exit(2)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)

    print(
        f"pr-cost-export: wrote {len(formatted_rows)} row(s) from {opted_in} of {declared} declared"
        f" account(s) to {out}"
    )
    print(
        "pr-cost-export: inspect this file outside a Claude Code session (e.g. in a separate"
        " terminal) -- reading it back with the Read tool, or catting it in-session, copies its"
        " rows into that session's own transcript. See CLAUDE.md and docs/pr-cost.md before"
        " publishing anything derived from it.",
        file=sys.stderr,
    )
