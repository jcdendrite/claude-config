"""The pr-cost-export command family: cmd_pr_cost_export and every helper used only by it -- collapsing every declared
account's pr-cost ledger to current rows, redacting them, and publishing one TSV. Makes no gh call and scans no transcript
corpus."""
from __future__ import annotations

import argparse
import hashlib
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import _config
from _config_dir import declared_roots_file_is_overridden
from transcript_analysis import ledger_common, pr_cost_ledger, pricing, redaction, review_rounds, scope

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


def _tokens_for_export(token_object: dict) -> tuple[dict, int]:
    """A model or variant object that removal empties is dropped without its own count, because the keys that emptied
    it were counted."""
    kept_models: dict = {}
    dropped = 0
    for model, variants in token_object.items():
        if model not in pricing._MODEL_BASE_INPUT_RATES:
            dropped += 1
            continue
        kept_variants: dict = {}
        for variant, classes in variants.items():
            if variant not in pricing._PRICING_VARIANTS:
                dropped += 1
                continue
            kept_classes = {token_class: count for token_class, count in classes.items() if token_class in pricing._TOKEN_CLASSES}
            dropped += len(classes) - len(kept_classes)
            if kept_classes:
                kept_variants[variant] = kept_classes
        if kept_variants:
            kept_models[model] = kept_variants
    return kept_models, dropped


def _review_rounds_cell_for_export(cell: dict | None) -> tuple[dict | None, int]:
    """The cell with every key this checkout's vocabulary does not know removed, and the count removed; see
    docs/pr-cost.md for how the count is taken."""
    if cell is None:
        return None, 0
    known_round_keys = {
        *pr_cost_ledger._REVIEW_ROUND_COUNT_FIELDS, *pr_cost_ledger._REVIEW_ROUND_TOKEN_FIELDS,
        *pr_cost_ledger._REVIEW_ROUND_FLAG_FIELDS,
    }
    kept_rounds_by_skill: dict = {}
    dropped = 0
    for skill, rounds in cell["rounds"].items():
        if skill not in review_rounds.REVIEW_SKILLS:
            dropped += 1
            continue
        kept_rounds = []
        for review_round in rounds:
            kept_round = {}
            for key, value in review_round.items():
                if key not in known_round_keys:
                    dropped += 1
                elif key in pr_cost_ledger._REVIEW_ROUND_TOKEN_FIELDS:
                    kept_round[key], dropped_in_tokens = _tokens_for_export(value)
                    dropped += dropped_in_tokens
                else:
                    kept_round[key] = value
            kept_rounds.append(kept_round)
        kept_rounds_by_skill[skill] = kept_rounds
    return {"rule": cell["rule"], "rounds": kept_rounds_by_skill}, dropped


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


def _pr_cost_export_rows(roots: Sequence[Path]) -> tuple[list[str], int, int, int, int, int, int, list[str]]:
    """Read every resolved root's own pr-cost ledger. Returns
    (formatted_rows, declared, opted_in, skipped_not_opted_in,
    legacy_header_accounts, legacy_machine_value_rows,
    review_rounds_dropped_keys, corpus_identities),
    fully materialized, with no filesystem writes of its own. See
    docs/pr-cost.md's "Redacted cross-account export" section for account
    iteration order and corpus_identities' construction.
    """
    ordinals = scope._redaction_ordinals(roots)
    root_by_resolved = {root.resolve(): root for root in roots}
    declared = len(roots)
    opted_in = skipped_not_opted_in = legacy_header_accounts = legacy_machine_value_rows = 0
    review_rounds_dropped_keys = 0
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

        ledger_path = pr_cost_ledger._pr_cost_ledger_path_or_exit("pr-cost-export", ordinal, account_config_dir)
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
        for row, correction_count in pr_cost_ledger._collapse_pr_cost_rows_to_current(raw_rows):
            # Runs post-collapse, so a same-key multi-capture correction under
            # one legacy machine value is counted once here, not once per raw capture.
            if not ledger_common._MACHINE_IDENTITY_RE.match(row["machine"]):
                legacy_machine_value_rows += 1
            filtered_review_rounds, dropped_in_row = _review_rounds_cell_for_export(row["review_rounds"])
            review_rounds_dropped_keys += dropped_in_row
            exported = _redact_pr_cost_row_for_export(
                {**row, "review_rounds": filtered_review_rounds}, ordinal, correction_count,
                host_map, repo_map, pr_map, branch_map, machine_map,
            )
            try:
                formatted_rows.append(pr_cost_ledger._format_pr_cost_ledger_row(exported, columns=_PR_COST_EXPORT_COLUMNS))
            except pr_cost_ledger._PrCostLedgerParseError as exc:
                print(f"pr-cost-export: account-{ordinal}: {exc}", file=sys.stderr)
                sys.exit(1)

    return (
        formatted_rows, declared, opted_in, skipped_not_opted_in,
        legacy_header_accounts, legacy_machine_value_rows, review_rounds_dropped_keys, corpus_identities,
    )


def _pr_cost_export_provenance_line(
    *, exported_at: datetime, declared: int, opted_in: int, skipped_not_opted_in: int,
    legacy_header_accounts: int, legacy_machine_value_rows: int, review_rounds_dropped_keys: int,
    corpus_identities: Sequence[str], corpus_override: bool,
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
        f" review_rounds_dropped_keys={review_rounds_dropped_keys}"
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

    ledger_common._refuse_unusable_out_path(out, "pr-cost-export")

    roots = scope._resolve_cost_roots(args, "pr-cost-export")
    if len(roots) > 1 and pr_cost_ledger.pr_cost_ledger_path_overridden():
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
        legacy_machine_value_rows, review_rounds_dropped_keys, corpus_identities,
    ) = _pr_cost_export_rows(roots)

    provenance_line = _pr_cost_export_provenance_line(
        exported_at=datetime.now(UTC), declared=declared, opted_in=opted_in,
        skipped_not_opted_in=skipped_not_opted_in, legacy_header_accounts=legacy_header_accounts,
        legacy_machine_value_rows=legacy_machine_value_rows, review_rounds_dropped_keys=review_rounds_dropped_keys,
        corpus_identities=corpus_identities, corpus_override=declared_roots_file_is_overridden(),
    )
    file_text = "\n".join([provenance_line, _PR_COST_EXPORT_HEADER_LINE, *formatted_rows]) + "\n"

    ledger_common._publish_new_owner_file(out, file_text, "pr-cost-export")

    print(
        f"pr-cost-export: wrote {len(formatted_rows)} row(s) from {opted_in} of {declared} declared"
        f" account(s) to {out}"
    )
    if review_rounds_dropped_keys:
        print(
            f"pr-cost-export: dropped {review_rounds_dropped_keys} review_rounds key(s) this checkout does not know --"
            " some rows lack fields, skills or models a newer checkout recorded, so their token splits can under-sum"
            " their dollars; re-export from an updated checkout before summing rounds (see docs/pr-cost.md)",
            file=sys.stderr,
        )
    print(
        "pr-cost-export: inspect this file outside a Claude Code session (e.g. in a separate"
        " terminal) -- reading it back with the Read tool, or catting it in-session, copies its"
        " rows into that session's own transcript. See CLAUDE.md and docs/pr-cost.md before"
        " publishing anything derived from it.",
        file=sys.stderr,
    )


def _add_pr_cost_export_subparser(subparsers: argparse._SubParsersAction) -> None:
    p_pr_cost_export = subparsers.add_parser(
        "pr-cost-export",
        help=(
            "Export every declared account's current pr-cost ledger rows -- redacted,"
            " collapsed to one row per PR -- to a single operator-named TSV. Never writes to"
            " stdout. Makes no gh call and scans no transcript corpus. See docs/pr-cost.md."
        ),
    )
    p_pr_cost_export.add_argument(
        "--out", metavar="PATH",
        help=(
            "Required: destination TSV path, refused if it already exists (never"
            " overwritten). No stdout fallback -- stdout inside a Claude Code session is"
            " captured into that session's own transcript."
        ),
    )
    p_pr_cost_export.add_argument(
        "--config-dir", action="append", dest="extra_config_dirs", metavar="DIR",
        help="Additional Claude Code config directory to scan (repeatable).",
    )
    p_pr_cost_export.set_defaults(func=cmd_pr_cost_export)
