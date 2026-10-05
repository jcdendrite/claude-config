#!/usr/bin/env python3
"""Review bench CLI: `mine-szz`, `mine-rounds`, `mine-pr-comments`, `confirm` for
evals/review_bench's known-defect set; `snapshot-arms`, `smoke`, and `run` for its
fixture and runner harness; `judge`, `spot-check export|import`, `analyze`, and
`freeze` for its adjudication and analysis.

LOCAL USE ONLY -- never run in CI. `mine-rounds` reads this account's own
session transcripts; `mine-pr-comments` reads the authenticated `gh` user's
inline PR review comments; `mine-szz` and `confirm` read this repo's own git
history. Every miner writes to the gitignored evals/review_bench/.local/;
only `confirm` writes to the committed evals/review_bench/defects.json, and
only for a candidate the engineer approves at its interactive prompt, which
needs a terminal. `smoke`, `run`, and
`judge` launch real `claude -p` sessions against real Claude subscription
auth.

See evals/README.md's "Review bench" section for usage and the operational
design: frozen conditions and invalidation, building a later arm, reading
the report, interruption and cleanup, and out-of-session reads.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import stat
import subprocess
import sys
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EVALS_DIR = Path(__file__).resolve().parent

DEFAULT_LOCAL_DIR = EVALS_DIR / "review_bench" / ".local"
DEFAULT_DEFECTS_PATH = EVALS_DIR / "review_bench" / "defects.json"
DEFAULT_ARMS_ROOT = EVALS_DIR / "review_bench" / "arms"
DEFAULT_RUN_STORE_DIR = EVALS_DIR / "review_bench" / ".local" / "run-store"
DEFAULT_RECORDS_DIR = EVALS_DIR / "review_bench" / ".local" / "runs"
DEFAULT_JUDGE_RUN_STORE_DIR = EVALS_DIR / "review_bench" / ".local" / "judge-run-store"
DEFAULT_JUDGE_RECORDS_DIR = EVALS_DIR / "review_bench" / ".local" / "judge-runs"
DEFAULT_SPOT_CHECK_EXPORT_PATH = EVALS_DIR / "review_bench" / ".local" / "spot-check-export.json"
DEFAULT_CONDITIONS_PATH = EVALS_DIR / "review_bench" / "conditions.json"
DEFAULT_BASELINE_REPORT_PATH = EVALS_DIR / "review_bench" / "results" / "baseline.json"


def _positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {value}")
    return value


def _escape_control_characters(text: str) -> str:
    """text with each terminal-unsafe character shown as a backslash escape,
    so model-emitted text can never act on the operator's terminal."""
    from review_bench import defects

    return defects.escape_for_terminal(text)


def _print_out_of_session_reads(subcommand: str, records: Iterable) -> None:
    for record in records:
        for path in record.out_of_session_paths:
            print(
                f"{subcommand}: out-of-session read in {record.arm} run {record.opaque_run_id}: "
                f"{_escape_control_characters(path)}",
                file=sys.stderr,
            )


def _select_campaign(
    campaign_id: str | None, run_store_dir: str | None, *, subcommand: str, default_store_root: Path,
) -> tuple[str, Path]:
    """The campaign ID and run-store directory for `run`, `smoke`, and `judge`.
    Announced before any lock is taken, so a crash still leaves the operator
    the ID to resume with.

    Unless the caller names a store, it nests under the campaign ID:
    `RunStore.completed_block_ids` is keyed by `defect_id` alone within one
    store directory, so a store shared between campaigns would silently skip
    every defect another campaign already completed."""
    from review_bench.identifiers import validate_campaign_id

    if campaign_id is None:
        campaign_id = f"{subcommand}-{uuid.uuid4().hex[:8]}"
    validate_campaign_id(campaign_id)
    store_dir = Path(run_store_dir) if run_store_dir is not None else default_store_root / campaign_id
    print(f"{subcommand}: campaign {campaign_id} -- run store at {store_dir}", file=sys.stderr)
    return campaign_id, store_dir


def _existing_records_path(raw_path: str) -> Path:
    """`raw_path` as a Path once it names an existing file. `read_run_records`
    returns no records for a missing file, so a mistyped path would otherwise
    read as an empty campaign. Raises HarnessInvalidatedError, exit 2."""
    from review_bench import runner

    path = Path(raw_path)
    if not path.is_file():
        raise runner.HarnessInvalidatedError(f"records file {path} does not exist")
    return path


def _verify_frozen_conditions(
    conditions_path: Path, *, defects_path: Path, arms_root: Path, k: int | None, label: str,
    require_conditions: bool, seed: int | None = None,
):
    """Raises HarnessInvalidatedError, before any billable dispatch, when the
    harness files, K, the campaign seed (when `seed` is given), or environment
    differ from the frozen conditions.
    Returns the reference environment every block's readings must equal: the
    frozen environment, or, with no conditions file, one that adopts the
    campaign's first reading. A missing conditions file is an error when
    `require_conditions` is set, and otherwise noted and skipped, because the
    smoke campaign's judges run before the freeze."""
    from review_bench import analysis, runner

    if not conditions_path.is_file():
        if require_conditions:
            raise runner.HarnessInvalidatedError(
                f"{label}: no frozen conditions at {conditions_path} -- run `freeze` first"
            )
        print(f"{label}: no frozen conditions at {conditions_path} -- nothing to verify", file=sys.stderr)
        return runner.EnvironmentReference()
    frozen = analysis.load_frozen_conditions(conditions_path)
    analysis.check_against_frozen_conditions(
        frozen, analysis.compute_frozen_fields(defects_path, arms_root), k=k, seed=seed,
    )
    analysis.check_environment_reading_matches_frozen(runner.read_environment_record(), frozen)
    return runner.EnvironmentReference(frozen["environment"])


def cmd_mine_szz(args: argparse.Namespace) -> int:
    from review_bench import defects, mine_szz

    candidates = mine_szz.mine(REPO_ROOT, base_ref=args.base_ref)
    out_path = Path(args.local_dir) / "szz_candidates.json"
    defects.save_candidates(out_path, candidates)
    print(f"mine-szz: wrote {len(candidates)} candidate(s) to {out_path}", file=sys.stderr)
    return 0


def cmd_mine_rounds(args: argparse.Namespace) -> int:
    from review_bench import defects, mine_review_rounds

    candidates = mine_review_rounds.mine(REPO_ROOT)
    out_path = Path(args.local_dir) / "review_round_candidates.json"
    defects.save_candidates(out_path, candidates)
    print(f"mine-rounds: wrote {len(candidates)} candidate(s) to {out_path}", file=sys.stderr)
    return 0


def cmd_mine_pr_comments(args: argparse.Namespace) -> int:
    from review_bench import defects, mine_pr_comments

    candidates = mine_pr_comments.mine(REPO_ROOT)
    out_path = Path(args.local_dir) / "pr_comment_candidates.json"
    defects.save_candidates(out_path, candidates)
    print(f"mine-pr-comments: wrote {len(candidates)} candidate(s) to {out_path}", file=sys.stderr)
    return 0


_SHORT_SHA_LENGTH = 12  # the length mined candidate IDs already use for a commit
# Commit-authored text reaches the terminal through the approval display, so
# both its count and its length are capped.
_CONFIRM_MAX_COMMIT_SUBJECTS = 3
_CONFIRM_MAX_SUBJECT_CHARS = 120
# A mined `diff_hunk` is GitHub-authored text, so its displayed length is capped too.
_CONFIRM_MAX_DIFF_HUNK_CHARS = 600
# Sources whose miner computes lines_exist_at_introducing_head instead of defaulting it to True.
_SOURCES_CHECKING_LINES_EXIST = frozenset({"pr-comment"})
# The order `confirm` presents sources in. `review-round` comes first because its
# transcripts age out, so an early `q` must never starve it. A source not listed sorts last.
_CONFIRM_SOURCE_ORDER = ("review-round", "pr-comment", "szz")
# Sources whose evidence records whether the head is among the PR's own commits.
_SOURCES_LABELING_HEAD_BRANCH = frozenset({"pr-comment"})
_HEAD_OUTSIDE_PR_BRANCH_LABEL = " (head is outside the PR branch)"
_HEAD_BRANCH_UNKNOWN_LABEL = " (PR branch membership unknown)"
_NO_DESCRIPTION_NOTE = "(none -- a y asks for a one-line description)"
_CONFIRM_HEADER = (
    "confirm: {count} candidate(s) to review. Answer y to promote one, q or end of input to stop and keep the "
    "answers so far, and anything else to skip it. Ctrl-C discards every answer in this run, so q is the "
    "durable exit. Paste one line at a time: a multi-line paste can answer a later prompt unseen."
)

_DECISION_PROMOTE = "y"
_DECISION_SKIP = "n"
_DECISION_QUIT = "q"


def _short_sha(sha: str) -> str:
    return sha[:_SHORT_SHA_LENGTH]


def _stdin_is_terminal() -> bool:
    """Whether `confirm` may prompt. Tests patch this seam. No environment
    variable or command-line flag reaches it, so approval needs a real
    terminal."""
    if sys.stdin is None:
        return False
    try:
        return sys.stdin.isatty()
    except ValueError:  # stdin closed
        return False


def _defect_passing_confirm_checks(candidate, excerpts_by_id: dict[str, str]):
    """The ConfirmedDefect `candidate` would promote to, or None after printing
    why it is rejected. Every printed field passes through
    `_escape_control_characters`."""
    from review_bench import defects

    escaped_id = _escape_control_characters(candidate.id)
    try:
        public_texts = defects.defect_public_texts(
            REPO_ROOT, candidate.head_commit, candidate.fix_commit, source=candidate.source,
            evidence=candidate.evidence,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        # One candidate's commits being unreachable (rewritten history,
        # a stale .local/ shortlist) must not abort every other
        # candidate's confirmation in the same run.
        print(
            f"confirm: rejected {escaped_id} -- could not read its public git text "
            f"({_escape_control_characters(str(exc))})",
            file=sys.stderr,
        )
        return None

    violation = defects.check_description_provenance(candidate.description, public_texts, excerpts_by_id)
    if violation is not None:
        # No other excerpt text ever reaches the terminal here -- only
        # the candidate ID, the matched run, and its source candidate ID.
        print(
            f"confirm: rejected {escaped_id} -- description shares the word run "
            f"{_escape_control_characters(violation.shared_run)!r} with candidate "
            f"{_escape_control_characters(violation.source_candidate_id)}'s excerpt",
            file=sys.stderr,
        )
        return None

    try:
        return defects.ConfirmedDefect(
            id=candidate.id, source=candidate.source, lens=candidate.lens,
            base_commit=candidate.base_commit, head_commit=candidate.head_commit,
            fix_commit=candidate.fix_commit, fix_date=candidate.fix_date,
            description=candidate.description, path=candidate.evidence.get("path", ""),
            file_is_markdown=candidate.file_is_markdown,
        )
    except ValueError as exc:
        print(f"confirm: rejected {escaped_id} -- {_escape_control_characters(str(exc))}", file=sys.stderr)
        return None


def _indented_escaped_lines(text: str, indent: str) -> list[str]:
    """`text` as display lines, each escaped and indented. It splits on LF
    only, because the escape turns every other line separator into a visible
    backslash escape."""
    return [f"{indent}{_escape_control_characters(line)}" for line in text.split("\n")]


def _shown_diff_hunk_lines(diff_hunk) -> list[str]:
    """The display lines for a candidate's mined `diff_hunk`, each escaped. A
    hunk longer than `_CONFIRM_MAX_DIFF_HUNK_CHARS` keeps its end, because the
    commented line is the hunk's last, and a marker precedes the kept lines.
    Empty when the candidate carries no text hunk."""
    if not isinstance(diff_hunk, str) or not diff_hunk:
        return []
    cut = len(diff_hunk) > _CONFIRM_MAX_DIFF_HUNK_CHARS
    bounded = diff_hunk[-_CONFIRM_MAX_DIFF_HUNK_CHARS:] if cut else diff_hunk
    if cut and "\n" in bounded.rstrip("\n"):
        bounded = bounded.partition("\n")[2]  # drops the line the cut fell inside
    shown = ["  diff_hunk:"]
    if cut:
        shown.append("    ... (diff_hunk cut)")
    shown.extend(_indented_escaped_lines(bounded, "    "))
    return shown


def _print_candidate_for_approval(
    candidate, subjects: list[tuple[str, str]], same_location_candidates: Sequence[tuple] = (),
) -> None:
    """Show one passing candidate to the engineer, with the description first
    and in full. The four inclusion fields the engineer's answer accepts print
    last, beside the prompt. A miner that hard-codes a field as a guess it
    never checked has that field labeled. `same_location_candidates` are the
    other-source candidates that share this one's head, fix, and path, each
    with its status."""
    escape = _escape_control_characters
    path = escape(str(candidate.evidence.get("path", "(none recorded)")))
    if candidate.source in _SOURCES_LABELING_HEAD_BRANCH:
        head_on_pr_branch = candidate.evidence.get("head_on_pr_branch")
        # A missing key means the miner could not list the branch's commits, and any non-boolean is no answer either.
        if head_on_pr_branch is False:
            path += _HEAD_OUTSIDE_PR_BRANCH_LABEL
        elif head_on_pr_branch is not True:
            path += _HEAD_BRANCH_UNKNOWN_LABEL
    shown_same_location = [
        f"    {escape(other.id)} ({escape(other.source)}): {status}" for other, status in same_location_candidates
    ]
    if shown_same_location:
        shown_same_location.insert(0, "  also mined from another source at this head, fix, and path:")
    shown_subjects = [
        f"    {escape(_short_sha(sha))} {escape(subject[:_CONFIRM_MAX_SUBJECT_CHARS])}"
        for sha, subject in subjects[:_CONFIRM_MAX_COMMIT_SUBJECTS]
    ]
    shown_diff_hunk = _shown_diff_hunk_lines(candidate.evidence.get("diff_hunk"))
    # The whole description prints, with no length cut: the engineer approves the text as committed.
    shown_description = (
        ["  description:", *_indented_escaped_lines(candidate.description, "    ")]
        if candidate.description.strip()
        else [f"  description: {_NO_DESCRIPTION_NOTE}"]
    )
    lines = [
        "",
        f"candidate {escape(candidate.id)}",
        f"  source: {escape(candidate.source)}  fix_date: {escape(candidate.fix_date)}",
        f"  base {_short_sha(candidate.base_commit)}  introducing {_short_sha(candidate.head_commit)}  "
        f"fix {_short_sha(candidate.fix_commit)}",
        *shown_description,
        f"  path: {path}",
        "  commit subjects:",
        *shown_subjects,
        *shown_diff_hunk,
        *shown_same_location,
        "  inclusion fields:",
        f"    lens: {escape(candidate.lens)}",
        f"    lines_exist_at_introducing_head: {escape(str(candidate.lines_exist_at_introducing_head))}"
        f"{'' if candidate.source in _SOURCES_CHECKING_LINES_EXIST else ' (unchecked default)'}",
        f"    reviewer_could_have_caught_it: {escape(str(candidate.reviewer_could_have_caught_it))} "
        "(unchecked default)",
        f"    file_is_markdown: {escape(str(candidate.file_is_markdown))}",
    ]
    print("\n".join(lines), file=sys.stderr)


def _read_promotion_decision() -> str:
    """One answer at the `[y/N/q]` prompt. Only exactly `y` promotes, and only
    exactly `q` or end of input stops. Anything else, `Y` and `yes` included,
    skips the candidate."""
    print("promote this candidate? [y/N/q] ", end="", file=sys.stderr, flush=True)
    line = sys.stdin.readline()
    if line == "":
        return _DECISION_QUIT
    answer = line.rstrip("\r\n")
    if answer == _DECISION_PROMOTE:
        return _DECISION_PROMOTE
    if answer == _DECISION_QUIT:
        return _DECISION_QUIT
    return _DECISION_SKIP


def _confirmation_status(
    candidate_id: str, existing_ids: set[str], accepted_ids: set[str], declined_ids: set[str],
    rejected_ids: set[str],
) -> str:
    if candidate_id in existing_ids:
        return "confirmed"
    if candidate_id in accepted_ids:
        return "accepted this run"
    if candidate_id in declined_ids:
        return "skipped this run"
    if candidate_id in rejected_ids:
        return "rejected by checks"
    return "pending"


def _read_typed_description() -> str | None:
    """One typed description line, without its line feed, or None at end of
    input. A carriage return stays in the text, so the control-character check
    rejects it."""
    print("one-line description: ", end="", file=sys.stderr, flush=True)
    line = sys.stdin.readline()
    if line == "":
        return None
    return line.removesuffix("\n")


def _defect_with_typed_description(candidate, typed_description: str, excerpts_by_id: dict[str, str]):
    """The ConfirmedDefect `candidate` promotes to with `typed_description`, or
    None after printing why it is rejected. The control-character check runs
    before the surrounding whitespace is stripped, so a trailing carriage
    return is not trimmed away."""
    from review_bench import defects

    if defects.has_disallowed_control_character(typed_description):
        print(
            f"confirm: rejected {_escape_control_characters(candidate.id)} -- the description holds a control character",
            file=sys.stderr,
        )
        return None
    return _defect_passing_confirm_checks(
        replace(candidate, description=typed_description.strip()), excerpts_by_id,
    )


def _confirm_source_rank(candidate) -> int:
    if candidate.source in _CONFIRM_SOURCE_ORDER:
        return _CONFIRM_SOURCE_ORDER.index(candidate.source)
    return len(_CONFIRM_SOURCE_ORDER)


def _cross_source_location(candidate) -> tuple:
    return (candidate.head_commit, candidate.fix_commit, candidate.evidence.get("path"))


def cmd_confirm(args: argparse.Namespace) -> int:
    from review_bench import defects

    local_dir = Path(args.local_dir)
    candidates: list[defects.Candidate] = []
    for shortlist in sorted(local_dir.glob("*_candidates.json")):
        candidates.extend(defects.load_candidates(shortlist))
    defects.assert_unique_ids(candidates, miner="confirm")

    # Stable, so a source's candidates keep the order their miner wrote them in.
    candidates.sort(key=_confirm_source_rank)
    candidates_by_location: dict[tuple, list[defects.Candidate]] = {}
    for candidate in candidates:
        candidates_by_location.setdefault(_cross_source_location(candidate), []).append(candidate)

    # Checked against every candidate's excerpt, not only the one a given
    # description confirms -- the drafting session saw the whole shortlist.
    excerpts_by_id = {c.id: c.excerpt for c in candidates if c.excerpt}

    defects_path = Path(args.defects_path)
    existing = defects.load_confirmed_defects(defects_path)
    existing_ids = {d.id for d in existing}

    passing: list[tuple[defects.Candidate, defects.ConfirmedDefect]] = []
    rejected = 0
    rejected_ids: set[str] = set()
    for candidate in candidates:
        # One already in defects.json is a no-op retry, not a rejection. A
        # candidate with no description yet passes here and is described after
        # the engineer's `y`.
        if candidate.id in existing_ids:
            continue
        defect = _defect_passing_confirm_checks(candidate, excerpts_by_id)
        if defect is None:
            rejected += 1
            rejected_ids.add(candidate.id)
        else:
            passing.append((candidate, defect))

    # No override: a rejected entry is never written, on this or any later run,
    # until the engineer edits its own .local/ description and reruns confirm.
    if not _stdin_is_terminal():
        print(
            f"confirm: {len(passing)} candidate(s) awaiting the engineer at a terminal, {rejected} rejected -- "
            "nothing written or pinned; rerun confirm from an interactive terminal to approve",
            file=sys.stderr,
        )
        return 2 if passing else 0

    approved: list[defects.ConfirmedDefect] = []
    accepted_ids: set[str] = set()
    declined_ids: set[str] = set()
    skipped = 0
    print(_CONFIRM_HEADER.format(count=len(passing)), file=sys.stderr)
    try:
        for candidate, defect in passing:
            try:
                subjects = defects.commit_subjects(
                    REPO_ROOT, (candidate.head_commit, candidate.fix_commit, candidate.base_commit),
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                print(
                    f"confirm: rejected {_escape_control_characters(candidate.id)} -- "
                    f"could not read its commit subjects ({_escape_control_characters(str(exc))})",
                    file=sys.stderr,
                )
                rejected += 1
                rejected_ids.add(candidate.id)
                continue
            same_location_candidates = [
                (other, _confirmation_status(other.id, existing_ids, accepted_ids, declined_ids, rejected_ids))
                for other in candidates_by_location[_cross_source_location(candidate)]
                if other.source != candidate.source
            ]
            _print_candidate_for_approval(candidate, subjects, same_location_candidates)
            decision = _read_promotion_decision()
            if decision == _DECISION_QUIT:
                break
            if decision != _DECISION_PROMOTE:
                declined_ids.add(candidate.id)
                skipped += 1
                continue
            if not candidate.description.strip():
                typed_description = _read_typed_description()
                if typed_description is None:
                    break
                if not typed_description.strip():
                    print(
                        f"confirm: skipped {_escape_control_characters(candidate.id)} -- empty description",
                        file=sys.stderr,
                    )
                    declined_ids.add(candidate.id)
                    skipped += 1
                    continue
                defect = _defect_with_typed_description(candidate, typed_description, excerpts_by_id)
                if defect is None:
                    rejected += 1
                    rejected_ids.add(candidate.id)
                    continue
            approved.append(defect)
            accepted_ids.add(candidate.id)
    except KeyboardInterrupt as exc:
        print("confirm: interrupted -- nothing written or pinned", file=sys.stderr)
        return 128 + getattr(exc, "signum", signal.SIGINT)

    # Pinned after the prompt loop and only for approved candidates, so a
    # skipped or interrupted candidate leaves no ref. Pinning precedes the
    # write, so no committed defect names commits that only a deletable
    # branch or a moved PR-head ref holds.
    appended: list[defects.ConfirmedDefect] = []
    for defect in approved:
        try:
            defects.pin_defect_commits(REPO_ROOT, defect)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            print(
                f"confirm: rejected {_escape_control_characters(defect.id)} -- "
                f"could not pin its fix commit ({_escape_control_characters(str(exc))})",
                file=sys.stderr,
            )
            rejected += 1
            continue
        appended.append(defect)

    if appended:
        # `existing` was read at load time. A difference from the file's
        # current defects means a concurrent edit landed during the prompts,
        # so this aborts rather than overwriting with a stale
        # existing+appended set. A count comparison would miss an add paired
        # with a remove.
        current = defects.load_confirmed_defects(defects_path)
        if current != existing:
            print(
                f"confirm: aborted -- {defects_path} changed underneath this run "
                f"(had {len(existing)} defect(s) at load, now has {len(current)}); rerun confirm",
                file=sys.stderr,
            )
            return 1
        defects.save_confirmed_defects(defects_path, existing + appended)
    print(f"confirm: {len(appended)} appended, {skipped} skipped, {rejected} rejected", file=sys.stderr)
    return 0


def cmd_snapshot_arms(args: argparse.Namespace) -> int:
    from review_bench import arms

    dest_root = Path(args.arms_root)
    for arm in (arms.ARM_CURRENT_RULE, arms.ARM_FUNCTION_CONTEXT):
        arms.write_arm_snapshot(arm, dest_root / arm)
        print(
            f"snapshot-arms: wrote {len(arms.LENS_READ_CLAUSES)} lens file(s) for {arm} under {dest_root / arm}",
            file=sys.stderr,
        )
    return 0


def _load_defects_for_run(args: argparse.Namespace) -> list:
    from review_bench import defects

    all_defects = defects.load_confirmed_defects(Path(args.defects_path))
    if args.defect_id:
        requested_ids = set(args.defect_id)
        unknown_ids = sorted(requested_ids - {d.id for d in all_defects})
        if unknown_ids:
            print(f"warning: --defect-id matches no confirmed defect: {', '.join(unknown_ids)}", file=sys.stderr)
        all_defects = [d for d in all_defects if d.id in requested_ids]
    return all_defects


def _run_or_smoke(args: argparse.Namespace, *, fault: str | None, verify_frozen: bool) -> int:
    import run_skill_evals
    from review_bench import analysis, runner

    if args.k is None:
        args.k = runner.DEFAULT_K
    if args.workers is None:
        args.workers = run_skill_evals.DEFAULT_WORKERS

    selected = _load_defects_for_run(args)
    if not selected:
        print(f"{args.subcommand}: no confirmed defects selected -- nothing to run", file=sys.stderr)
        return 1

    campaign_id, run_store_dir = _select_campaign(
        args.campaign_id, args.run_store_dir, subcommand=args.subcommand, default_store_root=DEFAULT_RUN_STORE_DIR,
    )
    # Read back by main() to name the ID in an interrupt's resume hint.
    args.campaign_id = campaign_id
    run_store = runner.RunStore(run_store_dir)
    records_path = Path(args.records_dir) / f"{campaign_id}.jsonl"
    # See runner.default_live_checkout_roots's own docstring for why these
    # two roots are checked.
    live_checkout_roots = runner.default_live_checkout_roots()

    arm_names = (runner.arms_mod.ARM_CURRENT_RULE, runner.arms_mod.ARM_FUNCTION_CONTEXT)
    if verify_frozen:
        environment_reference = _verify_frozen_conditions(
            Path(args.conditions_path), defects_path=Path(args.defects_path), arms_root=Path(args.arms_root),
            k=args.k, label=args.subcommand, require_conditions=True, seed=args.seed,
        )
    else:
        environment_reference = runner.EnvironmentReference()
    completed_defect_ids = run_store.completed_block_ids()
    pending = [defect for defect in selected if defect.id not in completed_defect_ids]
    runner.preflight_defects(
        pending, arm_names=arm_names, source_repo=REPO_ROOT, arms_snapshot_root=Path(args.arms_root),
    )
    ceiling = runner.campaign_cost_ceiling(len(pending), len(arm_names), args.k)
    print(
        f"{args.subcommand}: {len(pending)} defect(s) x {len(arm_names)} arm(s) x K={args.k} = {ceiling.runs} run(s); "
        f"nominal cap product ${ceiling.single_attempt_usd:,.2f} at ${runner.REVIEWER_BUDGET_CAP_USD:.2f} per run "
        f"(${ceiling.all_retried_usd:,.2f} if every run retries once) -- unverified as a bound on spend",
        file=sys.stderr,
    )

    def build_spec(defect_id: str):
        defect = next(d for d in selected if d.id == defect_id)
        return runner.build_defect_fixture_spec(
            defect, arm_names=arm_names,
            source_repo=REPO_ROOT, live_checkout_roots=live_checkout_roots,
            arms_snapshot_root=Path(args.arms_root), run_store=run_store,
        )

    try:
        result = runner.run_campaign(
            [d.id for d in selected], build_spec=build_spec, arms=arm_names,
            k=args.k, seed=args.seed, campaign_id=campaign_id, run_store=run_store,
            records_path=records_path, projects_root=runner.config_dir() / "projects",
            fault=fault, workers=args.workers, environment_reference=environment_reference,
        )
    except (runner.EnvironmentMismatchError, runner.SystemicFailureError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    all_records = [record for block in result.block_results.values() for record in block.records]
    total = len(all_records)
    print(
        f"{args.subcommand}: campaign {campaign_id} ran {total} run(s) across {len(result.block_results)} defect(s): "
        f"{runner.format_outcome_counts(runner.count_outcomes(all_records))}",
        file=sys.stderr,
    )
    # The raw path is terminal-only, for the engineer's own review
    # (evals/README.md's "Out-of-session reads" section); the committed --out
    # report from `analyze` carries only the per-arm count, never a path.
    _print_out_of_session_reads(args.subcommand, all_records)
    print(
        f"{args.subcommand}: out-of-session read counts per arm = "
        f"{analysis.out_of_session_counts_by_arm(all_records)}",
        file=sys.stderr,
    )
    print(f"{args.subcommand}: cost per arm = {analysis.cost_totals_by_arm(all_records)}", file=sys.stderr)
    return 0


def cmd_smoke(args: argparse.Namespace) -> int:
    from review_bench import analysis

    # The closure manifest hash `freeze --last-smoke-manifest-hash` checks is taken before this process's first dispatch,
    # so an edit to the harness during the process makes `freeze` refuse it.
    # A campaign resumed after a closure edit takes its hash after the edit, which `freeze` still accepts.
    manifest_hash = analysis.closure_manifest_hash(analysis.compute_harness_closure())
    result = _run_or_smoke(args, fault=args.inject_fault, verify_frozen=False)
    if result == 0:
        # The hash prints here because args.k is resolved to its default inside _run_or_smoke.
        # The engineer passes it by hand.
        # No state file is used, because judging a smoke campaign as passing is the engineer's own manual gate.
        print(
            f"smoke: harness closure manifest hash = {manifest_hash} (K={args.k}) -- "
            "after judging this passes, pass both to `freeze --last-smoke-manifest-hash "
            "--smoke-full-k`",
            file=sys.stderr,
        )
    return result


def cmd_run(args: argparse.Namespace) -> int:
    return _run_or_smoke(args, fault=None, verify_frozen=True)


def cmd_judge(args: argparse.Namespace) -> int:
    from review_bench import adjudicate, analysis, runner

    selected = _load_defects_for_run(args)
    if not selected:
        print("judge: no confirmed defects selected -- nothing to judge", file=sys.stderr)
        return 1
    environment_reference = _verify_frozen_conditions(
        Path(args.conditions_path), defects_path=Path(args.defects_path), arms_root=Path(args.arms_root),
        k=None, label="judge", require_conditions=False,
    )

    reviewer_records = runner.read_run_records(_existing_records_path(args.reviewer_records_path))
    records_by_defect: dict[str, list] = {}
    for record in reviewer_records:
        records_by_defect.setdefault(record.defect_id, []).append(record)

    campaign_id, run_store_dir = _select_campaign(
        args.campaign_id, args.judge_run_store_dir, subcommand="judge", default_store_root=DEFAULT_JUDGE_RUN_STORE_DIR,
    )
    args.campaign_id = campaign_id
    run_store = runner.RunStore(run_store_dir)
    judge_records_path = Path(args.judge_records_dir) / f"{campaign_id}.jsonl"
    live_checkout_roots = runner.default_live_checkout_roots()

    run_store.acquire_lock()
    try:
        swept = run_store.sweep_abandoned(runner.config_dir() / "projects")
        if swept:
            print(
                f"judge: swept {len(swept)} directory/session-store pair(s) left by an abandoned attempt",
                file=sys.stderr,
            )
        # Resuming a `judge` run under the same --campaign-id must not
        # re-append a defect this campaign already judged -- judge_records_path
        # is append-only, so a retry that re-judged every defect from scratch
        # would duplicate every already-successful defect's own records.
        completed = run_store.completed_block_ids()
        # A prior invocation may have run and persisted a defect's recall
        # judge, then failed to build the precision fixture. That already-
        # paid recall run must not be redispatched on resume.
        recorded_recall_by_defect = {
            record.defect_id: record
            for record in runner.read_run_records(judge_records_path)
            if record.arm == adjudicate.JUDGE_ARM_RECALL
        }
        judged = 0
        judge_records: list = []
        for defect in selected:
            if defect.id in completed:
                continue
            records = records_by_defect.get(defect.id, [])
            if not records:
                print(f"judge: no reviewer runs recorded for {defect.id} -- skipping", file=sys.stderr)
                continue
            if not any(record.status == runner.STATUS_OK for record in records):
                print(f"judge: no completed reviewer run for {defect.id} -- skipping", file=sys.stderr)
                continue
            had_recorded_recall = defect.id in recorded_recall_by_defect
            try:
                recall_record, precision_record = adjudicate.run_defect_judges(
                    defect, records, source_repo=REPO_ROOT, campaign_id=campaign_id, seed=args.seed,
                    live_checkout_roots=live_checkout_roots, run_store=run_store,
                    judge_records_path=judge_records_path,
                    existing_recall_record=recorded_recall_by_defect.get(defect.id),
                    environment_reference=environment_reference,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                # One defect's commits being unreachable (rewritten history)
                # must not abort every other defect's judging in the same run.
                # run_defect_judges persists the recall record before it can
                # raise from building the precision fixture. Re-reading
                # judge_records_path here tells apart a defect whose recall
                # cost is already spent and recorded from one that failed
                # before any judge was dispatched.
                recall_now_recorded = had_recorded_recall or any(
                    record.defect_id == defect.id and record.arm == adjudicate.JUDGE_ARM_RECALL
                    for record in runner.read_run_records(judge_records_path)
                )
                if recall_now_recorded:
                    print(
                        f"judge: skipped {defect.id} -- recall already recorded, precision failed to build ({exc})",
                        file=sys.stderr,
                    )
                else:
                    print(f"judge: skipped {defect.id} -- could not read its git text ({exc})", file=sys.stderr)
                continue
            # Only the precision record is appended here -- run_defect_judges already
            # persisted the recall record itself, as soon as it completed.
            # A process kill between this append and mark_block_complete redispatches
            # precision once more for this defect on resume (evals/README.md's
            # "Interruption and cleanup" section).
            runner.append_run_records(judge_records_path, (precision_record,))
            judge_records.extend((recall_record, precision_record))
            run_store.mark_block_complete(defect.id)
            judged += 1
    finally:
        run_store.release_lock()

    # Terminal-only by design; see evals/README.md's "Out-of-session reads"
    # section.
    _print_out_of_session_reads("judge", judge_records)
    print(
        f"judge: out-of-session read counts per judge kind = {analysis.out_of_session_counts_by_arm(judge_records)}",
        file=sys.stderr,
    )
    print(f"judge: cost per judge kind = {analysis.cost_totals_by_arm(judge_records)}", file=sys.stderr)
    print(f"judge: campaign {campaign_id} judged {judged} defect(s), wrote {judge_records_path}", file=sys.stderr)
    return 0


def _build_spot_check_samples(args: argparse.Namespace):
    """Shared by `spot-check export` and `spot-check import`: the same
    (reviewer records, judge records, seed) always produce the same sampled
    candidate pool, so `import` re-derives it rather than round-tripping
    judge_label/arm through the human-facing export file, which must never
    carry them."""
    from review_bench import adjudicate, runner

    reviewer_records = runner.read_run_records(_existing_records_path(args.reviewer_records_path))
    judge_records = runner.read_run_records(_existing_records_path(args.judge_records_path))

    arm_by_defect: dict[str, dict[str, str]] = {}
    findings_by_defect: dict[str, dict[str, str]] = {}
    for record in reviewer_records:
        arm_by_defect.setdefault(record.defect_id, {})[record.opaque_run_id] = record.arm
        if record.status == runner.STATUS_OK:
            findings_by_defect.setdefault(record.defect_id, {})[record.opaque_run_id] = (
                adjudicate.normalize_findings_text(record.findings_text or "")
            )

    recall_candidates = []
    precision_candidates = []
    for record in judge_records:
        if record.status != runner.STATUS_OK:
            continue
        normalized = findings_by_defect.get(record.defect_id, {})
        expected_ids = tuple(normalized)
        arm_by_run = arm_by_defect.get(record.defect_id, {})
        if record.arm == adjudicate.JUDGE_ARM_RECALL:
            parsed = adjudicate.parse_recall_answer(
                record.findings_text or "", expected_ids=expected_ids, normalized_findings_by_id=normalized,
            )
            if parsed is None:
                continue
            recall_candidates.extend(
                adjudicate.build_recall_spot_check_candidates(record.defect_id, parsed, arm_by_run, normalized)
            )
        elif record.arm == adjudicate.JUDGE_ARM_PRECISION:
            parsed = adjudicate.parse_precision_answer(
                record.findings_text or "", expected_ids=expected_ids, normalized_findings_by_id=normalized,
            )
            if parsed is None:
                continue
            precision_candidates.extend(
                adjudicate.build_precision_spot_check_candidates(record.defect_id, parsed, arm_by_run, normalized)
            )

    recall_sample = adjudicate.select_spot_check_sample(
        recall_candidates, sample_size=adjudicate.SPOT_CHECK_RECALL_SAMPLE_SIZE, seed=args.seed,
    )
    precision_sample = adjudicate.select_spot_check_sample(
        precision_candidates, sample_size=adjudicate.SPOT_CHECK_PRECISION_SAMPLE_SIZE, seed=args.seed,
    )
    return recall_sample, precision_sample


def cmd_spot_check_export(args: argparse.Namespace) -> int:
    from review_bench import adjudicate

    recall_sample, precision_sample = _build_spot_check_samples(args)
    out_path = Path(args.out)
    adjudicate.export_spot_check(recall_sample + precision_sample, out_path)
    print(
        f"spot-check export: wrote {len(recall_sample)} recall item(s) and "
        f"{len(precision_sample)} precision item(s) to {out_path}",
        file=sys.stderr,
    )
    return 0


def cmd_spot_check_import(args: argparse.Namespace) -> int:
    from review_bench import adjudicate, analysis

    recall_sample, precision_sample = _build_spot_check_samples(args)
    sample = recall_sample + precision_sample
    human_labels = adjudicate.import_spot_check_labels(Path(args.labels_path))

    kappa_by_kind = adjudicate.score_spot_check(sample, human_labels)
    for kind in sorted(kappa_by_kind):
        kappa = kappa_by_kind[kind]
        validated = "validated" if kappa >= analysis.KAPPA_SUBSTANTIAL_FLOOR else "NOT validated (below KAPPA_SUBSTANTIAL_FLOOR)"
        print(f"spot-check import: {kind} kappa = {kappa:.3f} -- {validated}", file=sys.stderr)

    agreement_by_arm = adjudicate.split_agreement_by_arm(sample, human_labels)
    for arm in sorted(agreement_by_arm):
        print(f"spot-check import: {arm} split agreement = {agreement_by_arm[arm]:.3f}", file=sys.stderr)
    return 0


def _stratum_figures(
    recall_counts, precision_counts, recall_ids: list[str], precision_ids: list[str], *,
    cluster_by_defect, baseline_arm: str, other_arm: str,
) -> tuple[dict, str | None, str | None]:
    """The figures reported for one gate or stratum, over its own defects
    alone: effective N as a defect count and a fixture count beside the
    largest fixture's size, per-arm recall and pooled precision with
    paired-cluster-bootstrap intervals, and the arm difference in each, other
    arm minus baseline arm. Every figure that is undefined on this set, such
    as an interval over fewer than two fixtures, is None. Also returns the two
    non-inferiority verdicts, which a caller records only for a gate."""
    from review_bench import analysis

    recall_verdict, recall_difference_interval = analysis.recall_noninferiority_verdict(
        recall_counts, recall_ids, baseline_arm, other_arm, cluster_by_defect=cluster_by_defect,
    )
    precision_verdict, precision_difference_interval, precision_difference_dropped = (
        analysis.precision_noninferiority_verdict(
            precision_counts, precision_ids, baseline_arm, other_arm, cluster_by_defect=cluster_by_defect,
        )
    )
    pooled_precision_per_arm = {}
    for arm in (baseline_arm, other_arm):
        interval, dropped_resamples = analysis.bootstrap_interval_with_drops(
            precision_ids, lambda resample_ids, arm=arm: analysis.pooled_precision(precision_counts, resample_ids, arm),
            cluster_by_defect=cluster_by_defect,
        )
        pooled_precision_per_arm[arm] = {
            "precision": analysis.pooled_precision(precision_counts, precision_ids, arm),
            "interval": interval, "dropped_resamples": dropped_resamples,
        }
    figures = {
        "effective_n": len(recall_ids),
        "fixture_count": analysis.fixture_count(recall_ids, cluster_by_defect),
        "largest_cluster_size": analysis.largest_cluster_size(recall_ids, cluster_by_defect),
        "precision_effective_n": len(precision_ids),
        "precision_fixture_count": analysis.fixture_count(precision_ids, cluster_by_defect),
        "recall_per_arm": {
            arm: {
                "recall": analysis.arm_recall(recall_counts, recall_ids, arm),
                "interval": analysis.bootstrap_interval(
                    recall_ids, lambda resample_ids, arm=arm: analysis.arm_recall(recall_counts, resample_ids, arm),
                    cluster_by_defect=cluster_by_defect,
                ),
            }
            for arm in (baseline_arm, other_arm)
        },
        "recall_difference": {
            "arm": other_arm, "minus_arm": baseline_arm, "interval": recall_difference_interval,
            "difference": analysis.recall_difference(recall_counts, recall_ids, other_arm, baseline_arm),
        },
        "pooled_precision_per_arm": pooled_precision_per_arm,
        "precision_difference": {
            "arm": other_arm, "minus_arm": baseline_arm, "interval": precision_difference_interval,
            "dropped_resamples": precision_difference_dropped,
            "difference": analysis.precision_difference(precision_counts, precision_ids, other_arm, baseline_arm),
        },
    }
    return figures, recall_verdict, precision_verdict


def _gate_size_fields(
    recall_ids: list[str], precision_ids: list[str], confirmed_in_gate: int, *, cluster_by_defect, n_min_value: int,
) -> dict:
    """What a gate adds to its figures: whether each kept set spans N_min
    fixtures, and the confirmed defects the recall and precision analyses
    dropped."""
    from review_bench import analysis

    return {
        "effective_n_meets_n_min": analysis.effective_n_meets_n_min(
            recall_ids, cluster_by_defect, n_min_value=n_min_value,
        ),
        "precision_effective_n_meets_n_min": analysis.effective_n_meets_n_min(
            precision_ids, cluster_by_defect, n_min_value=n_min_value,
        ),
        "dropped_defects_recall": confirmed_in_gate - len(recall_ids),
        "dropped_defects_precision": len(recall_ids) - len(precision_ids),
    }


def _strata_report(
    recall_counts, precision_counts, confirmed, kept_ids: list[str], precision_kept_ids: list[str],
    kept_by_gate: dict[str, list[str]], precision_kept_by_gate: dict[str, list[str]], *, cluster_by_defect,
    baseline_arm: str, other_arm: str,
) -> dict:
    """The per-source strata and the secondary stratum: figures only. A
    stratum never gates, so none carries a verdict."""
    from review_bench import analysis

    kept_by_source = analysis.source_defect_ids(confirmed, kept_ids)
    precision_kept_by_source = analysis.source_defect_ids(confirmed, precision_kept_ids)
    figures_kwargs = {"cluster_by_defect": cluster_by_defect, "baseline_arm": baseline_arm, "other_arm": other_arm}
    return {
        "per_source": {
            source: _stratum_figures(
                recall_counts, precision_counts, kept_by_source[source], precision_kept_by_source[source],
                **figures_kwargs,
            )[0]
            for source in kept_by_source
        },
        "secondary_stratum": _stratum_figures(
            recall_counts, precision_counts, kept_by_gate[analysis.STRATUM_SECONDARY],
            precision_kept_by_gate[analysis.STRATUM_SECONDARY], **figures_kwargs,
        )[0],
    }


def _baseline_gate_sections(
    recall_counts, precision_counts, confirmed_by_gate: dict[str, list[str]], kept_by_gate: dict[str, list[str]],
    precision_kept_by_gate: dict[str, list[str]], *, cluster_by_defect, baseline_arm: str, other_arm: str,
    n_min_value: int,
) -> dict:
    """Per gate, a baseline report's figures, N against N_min, and the
    sensitivity verdict with its interval limits (None for an empty or
    one-fixture gate), computed over that gate's own defects alone."""
    from review_bench import analysis

    sections = {}
    for gate in analysis.GATES:
        figures, _recall_verdict, _precision_verdict = _stratum_figures(
            recall_counts, precision_counts, kept_by_gate[gate], precision_kept_by_gate[gate],
            cluster_by_defect=cluster_by_defect, baseline_arm=baseline_arm, other_arm=other_arm,
        )
        sensitivity_verdict, sensitivity_interval = analysis.baseline_sensitivity_verdict(
            recall_counts, kept_by_gate[gate], baseline_arm, other_arm, cluster_by_defect=cluster_by_defect,
        )
        lower_limit, upper_limit = sensitivity_interval if sensitivity_interval is not None else (None, None)
        sections[gate] = {
            **figures,
            **_gate_size_fields(
                kept_by_gate[gate], precision_kept_by_gate[gate], len(confirmed_by_gate[gate]),
                cluster_by_defect=cluster_by_defect, n_min_value=n_min_value,
            ),
            "baseline_sensitivity": {
                "verdict": sensitivity_verdict, "interval_lower_limit": lower_limit,
                "interval_upper_limit": upper_limit, "delta": analysis.DELTA,
            },
        }
    return sections


def _later_arm_gate_sections(
    recall_counts, precision_counts, confirmed_by_gate: dict[str, list[str]], kept_by_gate: dict[str, list[str]],
    precision_kept_by_gate: dict[str, list[str]], baseline_verdicts: dict[str, str | None], *, cluster_by_defect,
    baseline_arm: str, other_arm: str, n_min_value: int,
) -> tuple[dict, dict]:
    """Per gate, a later arm's figures, non-inferiority verdicts, and outcome,
    computed over that gate's own defects alone, plus the `GateResult`
    `certify_later_arm` reads. A gate is short when either its recall-kept or
    its precision-kept set spans fewer than N_min fixtures."""
    from review_bench import analysis

    sections, gate_results = {}, {}
    for gate in analysis.GATES:
        figures, recall_verdict, precision_verdict = _stratum_figures(
            recall_counts, precision_counts, kept_by_gate[gate], precision_kept_by_gate[gate],
            cluster_by_defect=cluster_by_defect, baseline_arm=baseline_arm, other_arm=other_arm,
        )
        size_fields = _gate_size_fields(
            kept_by_gate[gate], precision_kept_by_gate[gate], len(confirmed_by_gate[gate]),
            cluster_by_defect=cluster_by_defect, n_min_value=n_min_value,
        )
        gate_results[gate] = analysis.GateResult(
            recall_verdict=recall_verdict, precision_verdict=precision_verdict,
            baseline_sensitivity=baseline_verdicts[gate],
            short=not (size_fields["effective_n_meets_n_min"] and size_fields["precision_effective_n_meets_n_min"]),
        )
        sections[gate] = {
            **figures, **size_fields,
            "baseline_sensitivity": {"verdict": baseline_verdicts[gate]},
            "recall_noninferiority": {"verdict": recall_verdict},
            "precision_noninferiority": {"verdict": precision_verdict},
            "outcome": analysis.gate_outcome(gate_results[gate]),
        }
    return sections, gate_results


def cmd_analyze(args: argparse.Namespace, *, n_min_override: int | None = None) -> int:
    """`n_min_override` is test-only and no CLI flag reaches it: it replaces
    N_min(K) in the short-gate test, so a test can build a gate that meets it
    from a few fixtures."""
    from review_bench import adjudicate, analysis, arms, defects, runner

    # Caught here too, not only by main()'s own top-level handler, since
    # tests invoke cmd_analyze directly and rely on it returning 2 itself.
    baseline_verdicts: dict[str, str | None] = {}
    freeze_identity: dict | None = None
    try:
        confirmed = defects.load_confirmed_defects(Path(args.defects_path))
        reviewer_records = runner.read_run_records(_existing_records_path(args.reviewer_records_path))
        judge_records = runner.read_run_records(_existing_records_path(args.judge_records_path))
        analysis.check_campaign_environment_consistency(reviewer_records)

        if args.baseline_conditions_path is not None:
            frozen = analysis.load_frozen_conditions(Path(args.baseline_conditions_path))
            analysis.check_environment_matches_baseline(
                reviewer_records, baseline_cli_version=frozen["environment"]["cli_version"],
                baseline_ambient_config_commit=frozen["environment"]["ambient_config_commit"],
            )
            analysis.check_against_frozen_conditions(
                frozen, analysis.compute_frozen_fields(Path(args.defects_path), Path(args.arms_root)), k=args.k,
            )
            analysis.check_record_defect_ids_match(reviewer_records, frozen["defect_ids"])
            baseline_verdicts = analysis.load_baseline_gate_verdicts(Path(args.baseline_report_path), frozen)
        elif args.out is not None:
            freeze_identity = analysis.baseline_report_freeze_identity(
                Path(args.defects_path), Path(args.arms_root), reviewer_records, k=args.k,
            )
    except analysis.HarnessInvalidatedError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    recall_labels_by_defect: dict[str, dict] = {}
    precision_labels_by_defect: dict[str, dict] = {}
    findings_by_defect: dict[str, dict[str, str]] = {}
    for record in reviewer_records:
        if record.status == runner.STATUS_OK:
            findings_by_defect.setdefault(record.defect_id, {})[record.opaque_run_id] = (
                adjudicate.normalize_findings_text(record.findings_text or "")
            )
    for record in judge_records:
        if record.status != runner.STATUS_OK:
            continue
        normalized = findings_by_defect.get(record.defect_id, {})
        expected_ids = tuple(normalized)
        if record.arm == adjudicate.JUDGE_ARM_RECALL:
            parsed = adjudicate.parse_recall_answer(
                record.findings_text or "", expected_ids=expected_ids, normalized_findings_by_id=normalized,
            )
            if parsed is not None:
                recall_labels_by_defect[record.defect_id] = parsed
        elif record.arm == adjudicate.JUDGE_ARM_PRECISION:
            parsed = adjudicate.parse_precision_answer(
                record.findings_text or "", expected_ids=expected_ids, normalized_findings_by_id=normalized,
            )
            if parsed is not None:
                precision_labels_by_defect[record.defect_id] = parsed

    recall_counts = analysis.compute_recall_counts(reviewer_records, recall_labels_by_defect)
    kept_ids = analysis.kept_recall_defect_ids(
        recall_counts, (arms.ARM_CURRENT_RULE, args.arm_x or arms.ARM_FUNCTION_CONTEXT), k=args.k,
    )
    precision_counts = analysis.compute_precision_counts(reviewer_records, precision_labels_by_defect)
    precision_kept_ids = analysis.kept_precision_defect_ids(kept_ids, precision_counts)

    baseline_arm, other_arm = arms.ARM_CURRENT_RULE, (args.arm_x or arms.ARM_FUNCTION_CONTEXT)
    n_min_value = n_min_override if n_min_override is not None else analysis.n_min(args.k)
    print(
        f"analyze: {len(confirmed)} confirmed defect(s), {len(kept_ids)} kept for recall (N_min={n_min_value})",
        file=sys.stderr,
    )
    dropped_recall_defects = len({defect.id for defect in confirmed} - set(kept_ids))
    print(
        f"analyze: dropped {dropped_recall_defects} confirmed defect(s) from recall (below K/2 completed runs in "
        "either arm, or no valid recall-judge labels)",
        file=sys.stderr,
    )
    print(
        f"analyze: dropped {len(kept_ids) - len(precision_kept_ids)} defect(s) from precision (missing "
        "precision-judge run)",
        file=sys.stderr,
    )
    if not kept_ids:
        print("analyze: no defect is kept for recall -- nothing to analyze", file=sys.stderr)
        return 2

    cluster_by_defect = analysis.fixture_clusters(confirmed)
    try:
        confirmed_by_gate = analysis.gate_defect_ids(confirmed, [defect.id for defect in confirmed])
        kept_by_gate = analysis.gate_defect_ids(confirmed, kept_ids)
        precision_kept_by_gate = analysis.gate_defect_ids(confirmed, precision_kept_ids)
    except analysis.HarnessInvalidatedError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    for gate_or_stratum in (*analysis.GATES, analysis.STRATUM_SECONDARY):
        print(
            f"analyze: {gate_or_stratum}: {len(kept_by_gate[gate_or_stratum])} kept defect(s) over "
            f"{analysis.fixture_count(kept_by_gate[gate_or_stratum], cluster_by_defect)} fixture(s), largest fixture "
            f"holds {analysis.largest_cluster_size(kept_by_gate[gate_or_stratum], cluster_by_defect)}",
            file=sys.stderr,
        )
    figures_kwargs = {"cluster_by_defect": cluster_by_defect, "baseline_arm": baseline_arm, "other_arm": other_arm}
    gate_sections: dict = {}
    certification = None
    if args.baseline_conditions_path is None:
        gate_sections = _baseline_gate_sections(
            recall_counts, precision_counts, confirmed_by_gate, kept_by_gate, precision_kept_by_gate,
            n_min_value=n_min_value, **figures_kwargs,
        )
        for gate in analysis.GATES:
            sensitivity = gate_sections[gate]["baseline_sensitivity"]
            print(
                f"analyze: {gate} gate baseline sensitivity = {sensitivity['verdict']} "
                f"(M1 {sensitivity['interval_lower_limit']}, upper limit {sensitivity['interval_upper_limit']})",
                file=sys.stderr,
            )
    else:
        if not precision_kept_ids:
            print(
                "analyze: no defect is kept for precision (every precision-judge run is missing or unparseable) "
                "-- nothing to certify",
                file=sys.stderr,
            )
            return 2
        gate_sections, gate_results = _later_arm_gate_sections(
            recall_counts, precision_counts, confirmed_by_gate, kept_by_gate, precision_kept_by_gate,
            baseline_verdicts, n_min_value=n_min_value, **figures_kwargs,
        )
        certification = analysis.certify_later_arm(gate_results)
        for gate in analysis.GATES:
            section = gate_sections[gate]
            print(
                f"analyze: {gate} gate recall non-inferiority = {section['recall_noninferiority']['verdict']} "
                f"(interval {section['recall_difference']['interval']})",
                file=sys.stderr,
            )
            print(
                f"analyze: {gate} gate precision non-inferiority = {section['precision_noninferiority']['verdict']} "
                f"(interval {section['precision_difference']['interval']}, "
                f"{section['precision_difference']['dropped_resamples']} resample(s) dropped for a zero "
                "precision denominator)",
                file=sys.stderr,
            )
            print(
                f"analyze: {gate} gate baseline sensitivity = {baseline_verdicts[gate]}, "
                f"outcome = {section['outcome']}, {section['fixture_count']} fixture(s) (N_min={n_min_value})",
                file=sys.stderr,
            )
        print(f"analyze: certification = {certification}", file=sys.stderr)

    print(f"analyze: read tokens/run per arm = {analysis.read_token_stats(reviewer_records)}", file=sys.stderr)
    print(
        f"analyze: partial-view/paged-followup counts per arm = {analysis.partial_and_paged_counts(reviewer_records)}",
        file=sys.stderr,
    )
    print(f"analyze: whole-file-read adherence per arm = {analysis.whole_file_read_adherence(reviewer_records)}", file=sys.stderr)
    print(
        f"analyze: missing runs by reason per arm = {analysis.missing_run_counts_by_reason(reviewer_records)}",
        file=sys.stderr,
    )
    print(f"analyze: cost per arm = {analysis.cost_totals_by_arm(reviewer_records)}", file=sys.stderr)
    print(f"analyze: cost per judge kind = {analysis.cost_totals_by_arm(judge_records)}", file=sys.stderr)
    # Terminal-only by design; see evals/README.md's "Out-of-session reads"
    # section.
    _print_out_of_session_reads("analyze", reviewer_records)
    print(
        f"analyze: out-of-session read counts per arm = {analysis.out_of_session_counts_by_arm(reviewer_records)}",
        file=sys.stderr,
    )
    # judge_records' own `arm` field holds JUDGE_ARM_RECALL/JUDGE_ARM_PRECISION, not a reviewer
    # arm, but out_of_session_counts_by_arm keys generically off `record.arm`.
    _print_out_of_session_reads("analyze", judge_records)
    print(
        f"analyze: out-of-session read counts per judge kind = {analysis.out_of_session_counts_by_arm(judge_records)}",
        file=sys.stderr,
    )

    # recall_by_fix_date_half and recall_diff_over_read_cap_stratum both need
    # per-defect fix_date/over_read_cap flags the recall-counts join above
    # doesn't carry.
    fix_dates_by_defect = {defect.id: defect.fix_date for defect in confirmed}
    recall_by_fix_date_half_per_arm = {
        gate: {
            arm: analysis.recall_by_fix_date_half(recall_counts, kept_by_gate[gate], fix_dates_by_defect, arm)
            for arm in (baseline_arm, other_arm)
        }
        for gate in analysis.GATES
    }
    print(f"analyze: recall by fix-date half per gate and arm = {recall_by_fix_date_half_per_arm}", file=sys.stderr)

    # The over-read-cap stratum is the code gate's alone: markdown files are read whole.
    over_read_cap_defect_ids = {record.defect_id for record in reviewer_records if record.over_read_cap}
    recall_diff_over_read_cap_stratum = analysis.recall_diff_over_read_cap_stratum(
        recall_counts, kept_by_gate[analysis.GATE_CODE], over_read_cap_defect_ids, baseline_arm, other_arm,
    )
    print(f"analyze: recall diff in the code gate's over-read-cap stratum = {recall_diff_over_read_cap_stratum}", file=sys.stderr)

    observed_sigma_d = {
        gate: analysis.observed_sigma_d(
            recall_counts, kept_by_gate[gate], baseline_arm, other_arm, cluster_by_defect=cluster_by_defect,
        )
        for gate in analysis.GATES
    }
    print(f"analyze: observed sigma_d per gate = {observed_sigma_d}", file=sys.stderr)

    if args.out is not None:
        # No key holds a dollar total: the cost lines above go to stderr for
        # the engineer only, and the report is committed.
        if certification is not None:
            mode_fields = {"certification": certification}
        else:
            # Only a baseline report records the freeze identity a later arm checks it against.
            mode_fields = {
                "freeze_identity": freeze_identity,
                "dropped_defects_recall": dropped_recall_defects,
                "dropped_defects_precision": len(kept_ids) - len(precision_kept_ids),
                "detection_rate_per_defect": {
                    defect_id: {arm: recall_counts[defect_id].detection_rate(arm) for arm in (baseline_arm, other_arm)}
                    for defect_id in kept_ids
                },
            }
        report = {
            **mode_fields,
            "n_min": n_min_value,
            "gates": gate_sections,
            **_strata_report(
                recall_counts, precision_counts, confirmed, kept_ids, precision_kept_ids, kept_by_gate,
                precision_kept_by_gate, **figures_kwargs,
            ),
            "confirmed_defects": len(confirmed),
            "kept_defect_ids": kept_ids,
            "precision_kept_defect_ids": precision_kept_ids,
            "read_tokens_per_arm": analysis.read_token_stats(reviewer_records),
            "partial_and_paged_counts_per_arm": analysis.partial_and_paged_counts(reviewer_records),
            "whole_file_read_adherence_per_arm": analysis.whole_file_read_adherence(reviewer_records),
            "missing_runs_by_reason_per_arm": analysis.missing_run_counts_by_reason(reviewer_records),
            "out_of_session_read_counts_per_arm": analysis.out_of_session_counts_by_arm(reviewer_records),
            "out_of_session_read_counts_per_judge_kind": analysis.out_of_session_counts_by_arm(judge_records),
            "recall_by_fix_date_half_per_arm": recall_by_fix_date_half_per_arm,
            "recall_diff_over_read_cap_stratum": recall_diff_over_read_cap_stratum,
            "observed_sigma_d": observed_sigma_d,
        }
        out_path = Path(args.out)
        defects.atomic_write_text(out_path, json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(f"analyze: wrote {args.out}", file=sys.stderr)
    return 0


def _gate_counts(confirmed) -> dict[str, dict[str, int]]:
    """Each gate's and the secondary stratum's distinct fixture count and
    defect count over the confirmed set, the secondary cells excluded from
    each gate's."""
    from review_bench import analysis

    cluster_by_defect = analysis.fixture_clusters(confirmed)
    ids_by_gate = analysis.gate_defect_ids(confirmed, [defect.id for defect in confirmed])
    return {
        gate: {
            "fixture_count": analysis.fixture_count(defect_ids, cluster_by_defect),
            "defect_count": len(defect_ids),
        }
        for gate, defect_ids in ids_by_gate.items()
    }


def cmd_freeze(args: argparse.Namespace) -> int:
    from review_bench import analysis, defects, runner

    out_path = Path(args.out)
    if out_path.exists():
        print(
            f"freeze: {out_path} already exists -- a frozen record is never overwritten; "
            "delete it deliberately to freeze again",
            file=sys.stderr,
        )
        return 2

    defects_path = Path(args.defects_path)
    try:
        frozen_fields = analysis.compute_frozen_fields(defects_path, Path(args.arms_root))
    except analysis.HarnessInvalidatedError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    confirmed = defects.load_confirmed_defects(defects_path)
    local_dir = Path(args.local_dir)
    excerpts_by_id: dict[str, str] = {}
    evidence_by_id: dict[str, dict] = {}
    for shortlist in sorted(local_dir.glob("*_candidates.json")):
        for candidate in defects.load_candidates(shortlist):
            evidence_by_id[candidate.id] = candidate.evidence
            if candidate.excerpt.strip():
                excerpts_by_id[candidate.id] = candidate.excerpt
    review_round_defects_without_excerpt = [
        defect.id for defect in confirmed if defect.source == "review-round" and defect.id not in excerpts_by_id
    ]

    provenance_failures: list[tuple[str, str]] = []
    for defect in confirmed:
        try:
            public_texts = defects.defect_public_texts(
                REPO_ROOT, defect.head_commit, defect.fix_commit, source=defect.source,
                evidence=evidence_by_id.get(defect.id),
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            provenance_failures.append((defect.id, f"could not read public git text ({exc})"))
            continue
        violation = defects.check_description_provenance(defect.description, public_texts, excerpts_by_id)
        if violation is not None:
            provenance_failures.append((defect.id, f"shares word run {violation.shared_run!r}"))

    try:
        analysis.check_freeze_preconditions(
            current_manifest_hash=frozen_fields["harness_closure_hash"],
            last_smoke_manifest_hash=args.last_smoke_manifest_hash,
            k_to_freeze=args.k, smoke_full_k=args.smoke_full_k, provenance_failures=provenance_failures,
            review_round_defects_without_excerpt=review_round_defects_without_excerpt,
        )
    except analysis.HarnessInvalidatedError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    try:
        gating_rule = analysis.gating_rule_record()
        gate_counts = _gate_counts(confirmed)
    except analysis.HarnessInvalidatedError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    n_min_value = analysis.n_min(args.k)
    for gate_or_stratum, counts in gate_counts.items():
        below_n_min = gate_or_stratum in analysis.GATES and counts["fixture_count"] < n_min_value
        print(
            f"freeze: {gate_or_stratum}: {counts['fixture_count']} fixture(s), {counts['defect_count']} defect(s)"
            + (f" -- below N_min({args.k}) = {n_min_value}, so a later arm's verdict is capped at inconclusive"
               if below_n_min else ""),
            file=sys.stderr,
        )

    environment = runner.read_environment_record()
    conditions = {
        "reviewer_model_id": runner.REVIEWER_MODEL_ID,
        "judge_model_id": runner.JUDGE_MODEL_ID,
        "k": args.k,
        "delta": analysis.DELTA,
        "alpha_one_sided": analysis.ALPHA_ONE_SIDED,
        "n_min": analysis.n_min(args.k),
        "planning_variance": analysis.planning_variance(args.k),
        "bootstrap_resamples": analysis.BOOTSTRAP_RESAMPLES,
        "bootstrap_seed": analysis.BOOTSTRAP_SEED,
        "campaign_seed": args.campaign_seed,
        "kappa_floor": analysis.KAPPA_SUBSTANTIAL_FLOOR,
        "missing_run_retry_rule": "a failed run is retried once; a run that fails twice is recorded missing",
        "later_arm_gate": (
            "non-inferiority at delta on recall and on pooled precision, in the code gate and in the markdown "
            "gate; all four required"
        ),
        "gating_rule": gating_rule,
        "gate_counts": {gate: gate_counts[gate] for gate in analysis.GATES},
        **frozen_fields,
        "environment": {"cli_version": environment.cli_version, "ambient_config_commit": environment.ambient_config_commit},
        "main_commit_sha": args.main_commit_sha,
    }
    defects.atomic_write_text(out_path, json.dumps(conditions, indent=2, sort_keys=True) + "\n")
    print(f"freeze: wrote {out_path}", file=sys.stderr)
    return 0


def _add_conditions_path_arg(parser: argparse.ArgumentParser, *, when_absent: str) -> None:
    parser.add_argument(
        "--conditions-path", default=str(DEFAULT_CONDITIONS_PATH),
        help=f"The frozen conditions.json to verify against before any dispatch; {when_absent}.",
    )


def _add_campaign_args(parser: argparse.ArgumentParser, *, defect_id_required: bool) -> None:
    # build_parser() calls this for every invocation regardless of
    # subcommand chosen, so it stays import-free. runner.DEFAULT_K and
    # run_skill_evals.DEFAULT_WORKERS are read lazily in _run_or_smoke
    # instead, via the None defaults below.
    parser.add_argument("--defects-path", default=str(DEFAULT_DEFECTS_PATH), help="The committed defect-set file.")
    parser.add_argument(
        "--defect-id", action="append", default=[], required=defect_id_required,
        help="Restrict the campaign to this defect ID (repeatable)"
        + ("." if defect_id_required else "; default: every confirmed defect."),
    )
    parser.add_argument("--arms-root", default=str(DEFAULT_ARMS_ROOT), help="Root holding <arm>/bench-<lens>.md snapshots.")
    parser.add_argument(
        "--run-store-dir", default=None,
        help="Local run-store directory (write-ahead record + lock); "
        "default: DEFAULT_RUN_STORE_DIR nested under this campaign's own --campaign-id.",
    )
    parser.add_argument(
        "--records-dir", default=str(DEFAULT_RECORDS_DIR), help="Where this campaign's RunRecord JSONL is written.",
    )
    parser.add_argument("--campaign-id", default=None, help="Campaign ID (default: <subcommand>-<random>).")
    parser.add_argument(
        "--k", type=_positive_int, default=None,
        help="Runs per arm per defect, at least 1 (default: review_bench.runner.DEFAULT_K).",
    )
    parser.add_argument("--seed", type=int, default=0, help="Campaign seed for the per-defect block shuffle.")
    parser.add_argument(
        "--workers", type=int, default=None,
        help="Worker pool size per block (default: run_skill_evals.DEFAULT_WORKERS).",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Review bench defect-set mining and confirmation CLI.")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_szz = sub.add_parser("mine-szz", help="Mine SZZ-style blame candidates from this repo's own git history.")
    p_szz.add_argument("--base-ref", default="origin/main", help="First-parent history to scan (default: origin/main).")
    p_szz.add_argument("--local-dir", default=str(DEFAULT_LOCAL_DIR), help="Where to write the candidate shortlist.")
    p_szz.set_defaults(func=cmd_mine_szz)

    # No scope flag: this miner is always scoped to this repo's own worktrees
    # on exactly one config-dir root.
    p_rounds = sub.add_parser(
        "mine-rounds",
        help="Mine later-review-round candidates from this account's own session transcripts.",
    )
    p_rounds.add_argument("--local-dir", default=str(DEFAULT_LOCAL_DIR), help="Where to write the candidate shortlist.")
    p_rounds.set_defaults(func=cmd_mine_rounds)

    # No author or repository flag: the miner discovers the authenticated `gh`
    # login itself and reads only this checkout's github.com `origin`, when the
    # provider reports it public.
    p_pr_comments = sub.add_parser(
        "mine-pr-comments",
        help="Mine candidates from the repo owner's own inline review comments on merged PRs, where a "
        "respond-pr reply marked the comment fixed. Needs an authenticated gh.",
    )
    p_pr_comments.add_argument(
        "--local-dir", default=str(DEFAULT_LOCAL_DIR), help="Where to write the candidate shortlist.",
    )
    p_pr_comments.set_defaults(func=cmd_mine_pr_comments)

    p_confirm = sub.add_parser(
        "confirm",
        help="Check each .local/ candidate, then promote into the committed defects.json the ones the engineer "
        "approves at a [y/N/q] prompt, asking for a one-line description of any that has none. Needs a terminal.",
    )
    p_confirm.add_argument("--local-dir", default=str(DEFAULT_LOCAL_DIR), help="Where the miners' shortlists live.")
    p_confirm.add_argument("--defects-path", default=str(DEFAULT_DEFECTS_PATH), help="The committed defect-set file.")
    p_confirm.set_defaults(func=cmd_confirm)

    p_snapshot = sub.add_parser(
        "snapshot-arms",
        help="Render both arms' bench-<lens>.md files from production at the freeze commit.",
    )
    p_snapshot.add_argument("--arms-root", default=str(DEFAULT_ARMS_ROOT), help="Where to write <arm>/bench-<lens>.md.")
    p_snapshot.set_defaults(func=cmd_snapshot_arms)

    p_smoke = sub.add_parser(
        "smoke",
        help="Fault-injectable dry-run campaign against the real CLI -- never the baseline campaign. "
        "Requires --defect-id, since a default full-set sweep is a full-cost campaign.",
    )
    _add_campaign_args(p_smoke, defect_id_required=True)
    p_smoke.add_argument(
        "--inject-fault", choices=("wrong-agent", "extra-tool-call"), default=None,
        help="Force a real launch through one named validity-check failure, exercising retry-then-missing.",
    )
    p_smoke.set_defaults(func=cmd_smoke)

    # `run` has no --inject-fault: fault injection is smoke-only. Omitting
    # the flag here is the rejection itself -- argparse exits 2 on an
    # unrecognized argument.
    p_run = sub.add_parser("run", help="The real reviewer campaign (baseline or a later arm's rerun).")
    _add_campaign_args(p_run, defect_id_required=False)
    _add_conditions_path_arg(p_run, when_absent="exits 2 when the file does not exist")
    p_run.set_defaults(func=cmd_run)

    p_judge = sub.add_parser("judge", help="Run both judges once per defect over a completed reviewer campaign.")
    p_judge.add_argument("--defects-path", default=str(DEFAULT_DEFECTS_PATH), help="The committed defect-set file.")
    p_judge.add_argument(
        "--defect-id", action="append", default=[],
        help="Restrict judging to this defect ID (repeatable); default: every confirmed defect.",
    )
    p_judge.add_argument(
        "--reviewer-records-path", required=True, help="The `run`/`smoke` campaign's own RunRecord JSONL to judge.",
    )
    p_judge.add_argument("--arms-root", default=str(DEFAULT_ARMS_ROOT), help="Root holding <arm>/bench-<lens>.md snapshots.")
    _add_conditions_path_arg(p_judge, when_absent="skipped, with a note, when the file does not exist")
    p_judge.add_argument(
        "--judge-run-store-dir", default=None,
        help="Local run-store directory for judge runs (write-ahead record + lock); "
        "default: DEFAULT_JUDGE_RUN_STORE_DIR nested under this campaign's own --campaign-id.",
    )
    p_judge.add_argument(
        "--judge-records-dir", default=str(DEFAULT_JUDGE_RECORDS_DIR),
        help="Where this judge campaign's RunRecord JSONL is written.",
    )
    p_judge.add_argument("--campaign-id", default=None, help="Judge campaign ID (default: judge-<random>).")
    p_judge.add_argument("--seed", type=int, default=0, help="Blinding-order seed for every defect's judge input.")
    p_judge.set_defaults(func=cmd_judge)

    p_spot_check = sub.add_parser("spot-check", help="Human spot-check export/import for judge validation.")
    spot_check_sub = p_spot_check.add_subparsers(dest="spot_check_subcommand", required=True)

    def _add_spot_check_source_args(spot_check_parser: argparse.ArgumentParser) -> None:
        spot_check_parser.add_argument(
            "--reviewer-records-path", required=True, help="The reviewer campaign's own RunRecord JSONL.",
        )
        spot_check_parser.add_argument(
            "--judge-records-path", required=True, help="The judge campaign's own RunRecord JSONL.",
        )
        spot_check_parser.add_argument(
            "--seed", type=int, default=0, help="Sampling seed (must match between export and import).",
        )

    p_spot_check_export = spot_check_sub.add_parser("export", help="Sample and export a blind human spot-check sheet.")
    _add_spot_check_source_args(p_spot_check_export)
    p_spot_check_export.add_argument("--out", default=str(DEFAULT_SPOT_CHECK_EXPORT_PATH), help="Where to write the sheet.")
    p_spot_check_export.set_defaults(func=cmd_spot_check_export)

    p_spot_check_import = spot_check_sub.add_parser("import", help="Score a human-labeled spot-check sheet.")
    _add_spot_check_source_args(p_spot_check_import)
    p_spot_check_import.add_argument("--labels-path", required=True, help="The human's own labeled sheet.")
    p_spot_check_import.set_defaults(func=cmd_spot_check_import)

    p_analyze = sub.add_parser("analyze", help="Compute recall/precision verdicts over a judged campaign.")
    p_analyze.add_argument("--defects-path", default=str(DEFAULT_DEFECTS_PATH), help="The committed defect-set file.")
    p_analyze.add_argument(
        "--reviewer-records-path", required=True, help="The reviewer campaign's own RunRecord JSONL.",
    )
    p_analyze.add_argument("--judge-records-path", required=True, help="The judge campaign's own RunRecord JSONL.")
    p_analyze.add_argument(
        "--arms-root", default=str(DEFAULT_ARMS_ROOT),
        help="Root holding <arm>/bench-<lens>.md snapshots, recomputed against the frozen arm hashes.",
    )
    p_analyze.add_argument("--k", type=_positive_int, required=True, help="K used for this campaign (for the K/2 drop rule).")
    p_analyze.add_argument(
        "--arm-x", default=None,
        help="The non-baseline arm to report against current-rule (default: function-context).",
    )
    p_analyze.add_argument(
        "--baseline-conditions-path", default=None,
        help="A frozen baseline's conditions.json -- omit to compute the baseline's own sensitivity verdict "
        "instead of a later arm's non-inferiority verdicts.",
    )
    p_analyze.add_argument(
        "--baseline-report-path", default=str(DEFAULT_BASELINE_REPORT_PATH),
        help="The baseline-mode report (`analyze --out`, committed as results/baseline.json) whose per-gate "
        "sensitivity verdicts a later arm's analysis reads. Read only with --baseline-conditions-path.",
    )
    p_analyze.add_argument(
        "--out", default=None,
        help="Optional path to write the machine-readable report JSON. Per gate (code, markdown) and per source, "
        "and for the secondary stratum, it writes per-arm recall and pooled precision with intervals, the arm "
        "differences, and N against N_min. Baseline mode (no --baseline-conditions-path) adds each gate's "
        "sensitivity verdict; a later arm's analysis adds each gate's non-inferiority verdicts and the "
        "certification. The report carries no dollar totals; `analyze` prints them to stderr only.",
    )
    p_analyze.set_defaults(func=cmd_analyze)

    p_freeze = sub.add_parser("freeze", help="Write evals/review_bench/conditions.json (run once, before any baseline campaign).")
    p_freeze.add_argument("--defects-path", default=str(DEFAULT_DEFECTS_PATH), help="The committed defect-set file.")
    p_freeze.add_argument("--local-dir", default=str(DEFAULT_LOCAL_DIR), help="Where the miners' shortlists live.")
    p_freeze.add_argument("--arms-root", default=str(DEFAULT_ARMS_ROOT), help="Root holding <arm>/bench-<lens>.md snapshots.")
    p_freeze.add_argument("--k", type=_positive_int, required=True, help="K being frozen.")
    p_freeze.add_argument(
        "--campaign-seed", type=int, required=True,
        help="The --seed the baseline run/smoke campaign used for its per-defect block shuffle.",
    )
    p_freeze.add_argument(
        "--last-smoke-manifest-hash", required=True,
        help="The manifest hash `smoke` printed on its last passing campaign.",
    )
    p_freeze.add_argument(
        "--smoke-full-k", type=int, required=True, help="K of the smoke campaign's own full-K fixture.",
    )
    p_freeze.add_argument("--main-commit-sha", default="", help="main's own commit SHA, recorded for reference only.")
    p_freeze.add_argument("--out", default=str(DEFAULT_CONDITIONS_PATH), help="Where to write conditions.json.")
    p_freeze.set_defaults(func=cmd_freeze)

    return parser


class _SignalInterrupt(KeyboardInterrupt):
    """A KeyboardInterrupt that remembers which signal raised it, so `main()`
    can exit 128 plus that signal's number."""

    def __init__(self, signum: int) -> None:
        super().__init__()
        self.signum = signum


def _raise_signal_interrupt(signum: int, _frame) -> None:
    raise _SignalInterrupt(signum)


def _restrict_local_dir_to_owner(local_dir: Path) -> None:
    """A `.local/` directory that predates `main()`'s owner-only umask can hold
    raw reviewer output behind group or other permission bits. Tightens it to
    0700, or raises HarnessInvalidatedError when it cannot. A symlink or
    non-directory is refused untouched, so the chmod never reaches another
    directory. An absent directory is created later, under that umask."""
    from review_bench.runner import HarnessInvalidatedError

    try:
        local_dir_stat = local_dir.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise HarnessInvalidatedError(f"{local_dir} could not be inspected: {exc}") from exc
    if not stat.S_ISDIR(local_dir_stat.st_mode):
        raise HarnessInvalidatedError(f"{local_dir} is a symlink or not a directory; remove it and rerun")
    mode = stat.S_IMODE(local_dir_stat.st_mode)
    if not mode & (stat.S_IRWXG | stat.S_IRWXO):
        return
    try:
        local_dir.chmod(0o700)
    except OSError as exc:
        raise HarnessInvalidatedError(
            f"{local_dir} has mode {mode:04o} and could not be restricted to its owner: {exc}"
        ) from exc
    print(f"note: restricted {local_dir} from mode {mode:04o} to 0700", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    # Catch point for every subcommand's HarnessInvalidatedError
    # (read_run_records and every analysis.check_* precondition raise it) --
    # guarantees a malformed records file or a failed precondition always
    # exits 2 with a clean message, in every subcommand including `judge`
    # and `spot-check`.
    from review_bench.fixture_repo import UnsafeFixtureConfigError
    from review_bench.identifiers import InvalidIdentifierError
    from review_bench.runner import HarnessInvalidatedError, RunStoreLocked

    # A `claude -p` child leads its own session, so a terminal hangup never
    # reaches it. Raising KeyboardInterrupt for SIGHUP and SIGTERM sends them
    # down Ctrl-C's path: abort_launches() and the child's process-group kill.
    # `nohup` starts the process with SIGHUP ignored, and that must stand, or a
    # detached campaign would abort at logout.
    if signal.getsignal(signal.SIGHUP) is not signal.SIG_IGN:
        signal.signal(signal.SIGHUP, _raise_signal_interrupt)
    signal.signal(signal.SIGTERM, _raise_signal_interrupt)
    # Run records and .local/ hold raw reviewer output; keep them owner-only.
    os.umask(0o077)
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        _restrict_local_dir_to_owner(DEFAULT_LOCAL_DIR)
        return args.func(args)
    except (HarnessInvalidatedError, InvalidIdentifierError, UnsafeFixtureConfigError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except RunStoreLocked as exc:
        print(f"{exc} -- another run on this store is still in progress", file=sys.stderr)
        return 1
    except KeyboardInterrupt as exc:
        campaign_id = getattr(args, "campaign_id", None)
        resume_hint = f" -- resume with --campaign-id {campaign_id}" if campaign_id else ""
        print(f"{args.subcommand}: interrupted{resume_hint}", file=sys.stderr)
        return 128 + getattr(exc, "signum", signal.SIGINT)


if __name__ == "__main__":
    sys.exit(main())
