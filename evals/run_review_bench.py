#!/usr/bin/env python3
"""A-bench CLI: `mine-szz`, `mine-rounds`, `confirm` for evals/review_bench's
known-defect set; `snapshot-arms`, `smoke`, and `run` for its fixture and
runner harness; `judge`, `spot-check export|import`, `analyze`, and `freeze`
for its adjudication and analysis.

LOCAL USE ONLY -- never run in CI. `mine-rounds` reads this account's own
session transcripts; `mine-szz` and `confirm` read this repo's own git
history. Every miner writes to the gitignored evals/review_bench/.local/;
only `confirm` writes to the committed evals/review_bench/defects.json, and
only for a candidate the engineer has already approved. `smoke`, `run`, and
`judge` launch real `claude -p` sessions against real Claude subscription
auth.

See evals/README.md's "Review bench" section for usage and the operational
design: frozen conditions and invalidation, building a later arm, reading
the report, interruption and cleanup, and out-of-session reads.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import uuid
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


def _atomic_write_text(path: Path, text: str) -> None:
    """Write `text` to `path` via a same-directory temp file plus
    `os.replace` (atomic on POSIX), so a crash mid-write leaves the previous
    complete file in place rather than a truncated one. Duplicated from
    review_bench.defects's/review_bench.runner's own _atomic_write_text
    rather than imported, since that helper is each module's own private
    convention, not a public export."""
    tmp_path = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp_path.write_text(text)
    os.replace(tmp_path, path)


def cmd_mine_szz(args: argparse.Namespace) -> int:
    from review_bench import defects, mine_szz

    candidates = mine_szz.mine(REPO_ROOT, base_ref=args.base_ref)
    out_path = Path(args.local_dir) / "szz_candidates.json"
    defects.save_candidates(out_path, candidates)
    print(f"mine-szz: wrote {len(candidates)} candidate(s) to {out_path}", file=sys.stderr)
    return 0


def cmd_mine_rounds(args: argparse.Namespace) -> int:
    # Lazy import: transcript_analysis is a large module tree with no
    # bearing on the frozen harness constants, so it must stay out of their
    # content-hash import closure.
    from review_bench import defects, mine_review_rounds

    candidates = mine_review_rounds.mine(REPO_ROOT)
    out_path = Path(args.local_dir) / "review_round_candidates.json"
    defects.save_candidates(out_path, candidates)
    print(f"mine-rounds: wrote {len(candidates)} candidate(s) to {out_path}", file=sys.stderr)
    return 0


def cmd_confirm(args: argparse.Namespace) -> int:
    from review_bench import defects

    local_dir = Path(args.local_dir)
    candidates: list[defects.Candidate] = []
    for shortlist in sorted(local_dir.glob("*_candidates.json")):
        candidates.extend(defects.load_candidates(shortlist))
    defects.assert_unique_ids(candidates, miner="confirm")

    # Checked against every candidate's excerpt, not only the one a given
    # description confirms -- the drafting session saw the whole shortlist.
    excerpts_by_id = {c.id: c.excerpt for c in candidates if c.excerpt}

    defects_path = Path(args.defects_path)
    existing = defects.load_confirmed_defects(defects_path)
    existing_ids = {d.id for d in existing}

    appended: list[defects.ConfirmedDefect] = []
    rejected = 0
    for candidate in candidates:
        # A candidate with no description is not yet engineer-approved. One
        # already in defects.json is a no-op retry, not a rejection.
        if not candidate.description or candidate.id in existing_ids:
            continue

        try:
            public_text = defects.public_git_text(REPO_ROOT, candidate.head_commit, candidate.fix_commit)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            # One candidate's commits being unreachable (rewritten history,
            # a stale .local/ shortlist) must not abort every other
            # candidate's confirmation in the same run.
            print(f"confirm: rejected {candidate.id} -- could not read its public git text ({exc})", file=sys.stderr)
            rejected += 1
            continue

        violation = defects.check_description_provenance(candidate.description, public_text, excerpts_by_id)
        if violation is not None:
            # No other excerpt text ever reaches the terminal here -- only
            # the candidate ID, the matched run, and its source candidate ID.
            print(
                f"confirm: rejected {candidate.id} -- description shares the word run "
                f"{violation.shared_run!r} with candidate {violation.source_candidate_id}'s excerpt",
                file=sys.stderr,
            )
            rejected += 1
            continue

        try:
            defect = defects.ConfirmedDefect(
                id=candidate.id, source=candidate.source, lens=candidate.lens,
                base_commit=candidate.base_commit, head_commit=candidate.head_commit,
                fix_commit=candidate.fix_commit, fix_date=candidate.fix_date,
                description=candidate.description,
            )
        except ValueError as exc:
            print(f"confirm: rejected {candidate.id} -- {exc}", file=sys.stderr)
            rejected += 1
            continue
        # id (machine-generated, unlike the engineer-authored description) is printed
        # alongside it since this is the only point the description's own text reaches
        # a human before commit. The rejection branches above print only the id.
        print(f"confirm: promoting {defect.id} -- {defect.description!r}", file=sys.stderr)
        appended.append(defect)

    # No override: a rejected entry is never written, on this or any later run,
    # until the engineer edits its own .local/ description and reruns confirm.
    if appended:
        # existing_ids was read at load time. A mismatch with the file's
        # current defect count means a concurrent confirm run appended in
        # the meantime, so this aborts rather than overwriting with a stale
        # existing+appended set.
        current = defects.load_confirmed_defects(defects_path)
        if len(current) != len(existing):
            print(
                f"confirm: aborted -- {defects_path} changed underneath this run "
                f"(had {len(existing)} defect(s) at load, now has {len(current)}); rerun confirm",
                file=sys.stderr,
            )
            return 1
        defects.save_confirmed_defects(defects_path, existing + appended)
    print(f"confirm: {len(appended)} appended, {rejected} rejected", file=sys.stderr)
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
        all_defects = [d for d in all_defects if d.id in set(args.defect_id)]
    return all_defects


def _run_or_smoke(args: argparse.Namespace, *, fault: str | None) -> int:
    import run_skill_evals
    from review_bench import analysis, runner

    if args.k is None:
        args.k = runner.DEFAULT_K
    if args.workers is None:
        args.workers = run_skill_evals.DEFAULT_WORKERS

    selected = _load_defects_for_run(args)
    if not selected:
        print("run: no confirmed defects selected -- nothing to run", file=sys.stderr)
        return 1

    run_store = runner.RunStore(Path(args.run_store_dir))
    campaign_id = args.campaign_id or f"{args.subcommand}-{uuid.uuid4().hex[:8]}"
    records_path = Path(args.records_dir) / f"{campaign_id}.jsonl"
    # See runner.default_live_checkout_roots's own docstring for why these
    # two roots are checked.
    live_checkout_roots = runner.default_live_checkout_roots()

    def build_spec(defect_id: str):
        defect = next(d for d in selected if d.id == defect_id)
        return runner.build_defect_fixture_spec(
            defect, arm_names=(runner.arms_mod.ARM_CURRENT_RULE, runner.arms_mod.ARM_FUNCTION_CONTEXT),
            source_repo=REPO_ROOT, live_checkout_roots=live_checkout_roots,
            arms_snapshot_root=Path(args.arms_root), run_store=run_store,
        )

    try:
        result = runner.run_campaign(
            [d.id for d in selected], build_spec=build_spec,
            arms=(runner.arms_mod.ARM_CURRENT_RULE, runner.arms_mod.ARM_FUNCTION_CONTEXT),
            k=args.k, seed=args.seed, campaign_id=campaign_id, run_store=run_store,
            records_path=records_path, projects_root=runner.config_dir() / "projects",
            fault=fault, workers=args.workers,
        )
    except runner.EnvironmentDriftExceededError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    all_records = [record for block in result.block_results.values() for record in block.records]
    total = len(all_records)
    print(
        f"{args.subcommand}: campaign {campaign_id} ran {total} run(s) across {len(result.block_results)} defect(s)",
        file=sys.stderr,
    )
    # The raw path is terminal-only, for the engineer's own review
    # (evals/README.md's "Out-of-session reads" section); the committed --out
    # report from `analyze` carries only the per-arm count, never a path.
    for record in all_records:
        for path in record.out_of_session_paths:
            print(
                f"{args.subcommand}: out-of-session read in {record.arm} run {record.opaque_run_id}: {path}",
                file=sys.stderr,
            )
    print(
        f"{args.subcommand}: out-of-session read counts per arm = "
        f"{analysis.out_of_session_counts_by_arm(all_records)}",
        file=sys.stderr,
    )
    return 0


def cmd_smoke(args: argparse.Namespace) -> int:
    result = _run_or_smoke(args, fault=args.inject_fault)
    if result == 0:
        # `freeze`'s own manifest precondition checks against the last
        # passing smoke campaign's manifest (evals/README.md's "Frozen
        # conditions and invalidation" section) -- printed here, at
        # smoke-completion, since args.k is
        # only resolved to its default inside _run_or_smoke above. Passed by
        # hand to `freeze --last-smoke-manifest-hash` rather than read back
        # from a state file, since a smoke campaign's pass/fail judgment
        # itself is the engineer's own manual gate.
        from review_bench import analysis

        manifest_hash = analysis.closure_manifest_hash(analysis.compute_harness_closure())
        print(
            f"smoke: harness closure manifest hash = {manifest_hash} (K={args.k}) -- "
            "after judging this passes, pass both to `freeze --last-smoke-manifest-hash "
            "--smoke-full-k`",
            file=sys.stderr,
        )
    return result


def cmd_run(args: argparse.Namespace) -> int:
    return _run_or_smoke(args, fault=None)


def _resolve_judge_run_store_dir(judge_run_store_dir: str | None, campaign_id: str) -> Path:
    """Nests under `campaign_id` when the caller didn't override
    `--judge-run-store-dir`. `RunStore.completed_block_ids` is keyed by
    `defect_id` alone within one store directory, so a resumed `judge
    --campaign-id B` against the shared default path would otherwise
    silently skip every defect campaign A already completed."""
    if judge_run_store_dir is not None:
        return Path(judge_run_store_dir)
    return DEFAULT_JUDGE_RUN_STORE_DIR / campaign_id


def cmd_judge(args: argparse.Namespace) -> int:
    from review_bench import adjudicate, analysis, runner

    selected = _load_defects_for_run(args)
    if not selected:
        print("judge: no confirmed defects selected -- nothing to judge", file=sys.stderr)
        return 1

    reviewer_records = runner.read_run_records(Path(args.reviewer_records_path))
    records_by_defect: dict[str, list] = {}
    for record in reviewer_records:
        records_by_defect.setdefault(record.defect_id, []).append(record)

    campaign_id = args.campaign_id or f"judge-{uuid.uuid4().hex[:8]}"
    run_store = runner.RunStore(_resolve_judge_run_store_dir(args.judge_run_store_dir, campaign_id))
    judge_records_path = Path(args.judge_records_dir) / f"{campaign_id}.jsonl"
    live_checkout_roots = runner.default_live_checkout_roots()

    # Printed before acquire_lock, not only in the final success message, so
    # an operator who omitted --campaign-id still has a stderr line naming
    # the auto-generated ID to resume with after a mid-run crash.
    print(f"judge: campaign {campaign_id} -- run store at {run_store.store_dir}", file=sys.stderr)

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
            had_recorded_recall = defect.id in recorded_recall_by_defect
            try:
                recall_record, precision_record = adjudicate.run_defect_judges(
                    defect, records, source_repo=REPO_ROOT, campaign_id=campaign_id, seed=args.seed,
                    live_checkout_roots=live_checkout_roots, run_store=run_store,
                    judge_records_path=judge_records_path,
                    existing_recall_record=recorded_recall_by_defect.get(defect.id),
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
    for record in judge_records:
        for path in record.out_of_session_paths:
            print(f"judge: out-of-session read in {record.arm} run {record.opaque_run_id}: {path}", file=sys.stderr)
    print(
        f"judge: out-of-session read counts per judge kind = {analysis.out_of_session_counts_by_arm(judge_records)}",
        file=sys.stderr,
    )
    print(f"judge: campaign {campaign_id} judged {judged} defect(s), wrote {judge_records_path}", file=sys.stderr)
    return 0


def _build_spot_check_samples(args: argparse.Namespace):
    """Shared by `spot-check export` and `spot-check import`: the same
    (reviewer records, judge records, seed) always produce the same sampled
    candidate pool, so `import` re-derives it rather than round-tripping
    judge_label/arm through the human-facing export file, which must never
    carry them."""
    from review_bench import adjudicate, runner

    reviewer_records = runner.read_run_records(Path(args.reviewer_records_path))
    judge_records = runner.read_run_records(Path(args.judge_records_path))

    arm_by_defect: dict[str, dict[str, str]] = {}
    findings_by_defect: dict[str, dict[str, str]] = {}
    for record in reviewer_records:
        arm_by_defect.setdefault(record.defect_id, {})[record.opaque_run_id] = record.arm
        if record.status == runner.STATUS_OK:
            findings_by_defect.setdefault(record.defect_id, {})[record.opaque_run_id] = (
                adjudicate.normalize_bench_paths(record.findings_text or "")
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


def cmd_analyze(args: argparse.Namespace) -> int:
    from review_bench import adjudicate, analysis, arms, defects, runner

    try:
        confirmed = defects.load_confirmed_defects(Path(args.defects_path))
        reviewer_records = runner.read_run_records(Path(args.reviewer_records_path))
        judge_records = runner.read_run_records(Path(args.judge_records_path))
        analysis.check_campaign_environment_consistency(reviewer_records)

        if args.baseline_conditions_path is not None:
            baseline_conditions_path = Path(args.baseline_conditions_path)
            try:
                baseline_conditions = json.loads(baseline_conditions_path.read_text())
                baseline_environment = baseline_conditions["environment"]
                baseline_cli_version = baseline_environment["cli_version"]
                baseline_ambient_config_commit = baseline_environment["ambient_config_commit"]
                baseline_harness_closure = baseline_conditions["harness_closure"]
                # check_manifest_matches's dict() call would otherwise raise
                # a raw ValueError/TypeError on a wrong-shaped closure,
                # outside this guarding try.
                if not isinstance(baseline_harness_closure, dict):
                    raise TypeError(
                        f"harness_closure must be an object, got {type(baseline_harness_closure).__name__}"
                    )
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise analysis.HarnessInvalidatedError(
                    f"invalidated -- rerun all arms: baseline conditions file "
                    f"({baseline_conditions_path}) is unreadable or missing an expected field: {exc!r}"
                ) from exc
            analysis.check_environment_matches_baseline(
                reviewer_records, baseline_cli_version=baseline_cli_version,
                baseline_ambient_config_commit=baseline_ambient_config_commit,
            )
            current_closure = analysis.compute_harness_closure()
            analysis.check_manifest_matches(current_closure, baseline_harness_closure)
    except analysis.HarnessInvalidatedError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    recall_labels_by_defect: dict[str, dict] = {}
    precision_labels_by_defect: dict[str, dict] = {}
    findings_by_defect: dict[str, dict[str, str]] = {}
    for record in reviewer_records:
        if record.status == runner.STATUS_OK:
            findings_by_defect.setdefault(record.defect_id, {})[record.opaque_run_id] = (
                adjudicate.normalize_bench_paths(record.findings_text or "")
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
    print(
        f"analyze: {len(confirmed)} confirmed defect(s), {len(kept_ids)} kept for recall "
        f"(N_min={analysis.n_min(args.k)})",
        file=sys.stderr,
    )
    print(
        f"analyze: dropped {len(recall_counts) - len(kept_ids)} defect(s) below K/2 completed runs in either arm",
        file=sys.stderr,
    )
    print(
        f"analyze: dropped {len(kept_ids) - len(precision_kept_ids)} defect(s) from precision (missing "
        "precision-judge run)",
        file=sys.stderr,
    )

    if args.baseline_conditions_path is None:
        verdict, interval = analysis.baseline_sensitivity_verdict(recall_counts, kept_ids, baseline_arm, other_arm)
        print(f"analyze: baseline sensitivity = {verdict} (interval {interval})", file=sys.stderr)
    else:
        recall_verdict, recall_interval = analysis.recall_noninferiority_verdict(
            recall_counts, kept_ids, baseline_arm, other_arm,
        )
        precision_verdict, precision_interval = analysis.precision_noninferiority_verdict(
            precision_counts, precision_kept_ids, baseline_arm, other_arm,
        )
        certification = analysis.certify_later_arm(recall_verdict, precision_verdict)
        print(f"analyze: recall non-inferiority = {recall_verdict} (interval {recall_interval})", file=sys.stderr)
        print(f"analyze: precision non-inferiority = {precision_verdict} (interval {precision_interval})", file=sys.stderr)
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
    # Terminal-only by design; see evals/README.md's "Out-of-session reads"
    # section.
    for record in reviewer_records:
        for path in record.out_of_session_paths:
            print(f"analyze: out-of-session read in {record.arm} run {record.opaque_run_id}: {path}", file=sys.stderr)
    print(
        f"analyze: out-of-session read counts per arm = {analysis.out_of_session_counts_by_arm(reviewer_records)}",
        file=sys.stderr,
    )
    # judge_records' own `arm` field holds JUDGE_ARM_RECALL/JUDGE_ARM_PRECISION, not a reviewer
    # arm, but out_of_session_counts_by_arm keys generically off `record.arm`.
    for record in judge_records:
        for path in record.out_of_session_paths:
            print(f"analyze: out-of-session read in {record.arm} run {record.opaque_run_id}: {path}", file=sys.stderr)
    print(
        f"analyze: out-of-session read counts per judge kind = {analysis.out_of_session_counts_by_arm(judge_records)}",
        file=sys.stderr,
    )

    # recall_by_fix_date_half and recall_diff_over_read_cap_stratum both need
    # per-defect fix_date/over_read_cap flags the recall-counts join above
    # doesn't carry.
    fix_dates_by_defect = {defect.id: defect.fix_date for defect in confirmed}
    recall_by_fix_date_half_per_arm = {
        arm: analysis.recall_by_fix_date_half(recall_counts, kept_ids, fix_dates_by_defect, arm)
        for arm in (baseline_arm, other_arm)
    }
    print(f"analyze: recall by fix-date half per arm = {recall_by_fix_date_half_per_arm}", file=sys.stderr)

    over_read_cap_defect_ids = {record.defect_id for record in reviewer_records if record.over_read_cap}
    recall_diff_over_read_cap_stratum = analysis.recall_diff_over_read_cap_stratum(
        recall_counts, kept_ids, over_read_cap_defect_ids, baseline_arm, other_arm,
    )
    print(f"analyze: recall diff in the over-read-cap stratum = {recall_diff_over_read_cap_stratum}", file=sys.stderr)

    observed_sigma_d = analysis.observed_sigma_d(recall_counts, kept_ids, baseline_arm, other_arm)
    print(f"analyze: observed sigma_d = {observed_sigma_d}", file=sys.stderr)

    if args.out is not None:
        report = {
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
        out_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(out_path, json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(f"analyze: wrote {args.out}", file=sys.stderr)
    return 0


def _hash_directory(directory: Path) -> str:
    """One combined hash over every file under directory, sorted by relative
    path -- used for the arm-directory hashes `freeze` records
    (evals/README.md's "Frozen conditions and invalidation" section)."""
    parts = [
        f"{path.relative_to(directory)}:{hashlib.sha256(path.read_bytes()).hexdigest()}"
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    ]
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def cmd_freeze(args: argparse.Namespace) -> int:
    from review_bench import adjudicate, analysis, arms, defects, runner

    current_closure = analysis.compute_harness_closure()
    current_manifest_hash = analysis.closure_manifest_hash(current_closure)

    confirmed = defects.load_confirmed_defects(Path(args.defects_path))
    local_dir = Path(args.local_dir)
    excerpts_by_id: dict[str, str] = {}
    for shortlist in sorted(local_dir.glob("*_candidates.json")):
        for candidate in defects.load_candidates(shortlist):
            if candidate.excerpt:
                excerpts_by_id[candidate.id] = candidate.excerpt
    local_excerpts_present = local_dir.is_dir() and any(local_dir.glob("*_candidates.json"))

    provenance_failures: list[tuple[str, str]] = []
    for defect in confirmed:
        try:
            public_text = defects.public_git_text(REPO_ROOT, defect.head_commit, defect.fix_commit)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            provenance_failures.append((defect.id, f"could not read public git text ({exc})"))
            continue
        violation = defects.check_description_provenance(defect.description, public_text, excerpts_by_id)
        if violation is not None:
            provenance_failures.append((defect.id, f"shares word run {violation.shared_run!r}"))

    try:
        analysis.check_freeze_preconditions(
            current_manifest_hash=current_manifest_hash, last_smoke_manifest_hash=args.last_smoke_manifest_hash,
            k_to_freeze=args.k, smoke_full_k=args.smoke_full_k, provenance_failures=provenance_failures,
            local_excerpts_present=local_excerpts_present,
        )
    except analysis.HarnessInvalidatedError as exc:
        print(str(exc), file=sys.stderr)
        return 2

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
        "later_arm_gate": "non-inferiority at delta on recall and on pooled precision; both required",
        "defect_ids": sorted(d.id for d in confirmed),
        "harness_closure_hash": current_manifest_hash,
        "harness_closure": current_closure,
        "arm_dir_hashes": {
            arm: _hash_directory(Path(args.arms_root) / arm)
            for arm in (arms.ARM_CURRENT_RULE, arms.ARM_FUNCTION_CONTEXT)
        },
        "judge_agent_hashes": {
            "bench-judge-recall": hashlib.sha256(adjudicate.RECALL_JUDGE_AGENT_FILE.read_bytes()).hexdigest(),
            "bench-judge-precision": hashlib.sha256(adjudicate.PRECISION_JUDGE_AGENT_FILE.read_bytes()).hexdigest(),
        },
        "defects_json_hash": hashlib.sha256(Path(args.defects_path).read_bytes()).hexdigest(),
        "prompt_template_hashes": {
            "review_prompt": hashlib.sha256(runner.REVIEW_PROMPT_TEMPLATE.encode()).hexdigest(),
            "dispatch_prompt": hashlib.sha256(runner.DISPATCH_PROMPT_TEMPLATE.encode()).hexdigest(),
            "judge_inner_prompt": hashlib.sha256(adjudicate.JUDGE_INNER_PROMPT_TEMPLATE.encode()).hexdigest(),
        },
        "environment": {"cli_version": environment.cli_version, "ambient_config_commit": environment.ambient_config_commit},
        "main_commit_sha": args.main_commit_sha,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(conditions, indent=2, sort_keys=True) + "\n")
    print(f"freeze: wrote {out_path}", file=sys.stderr)
    return 0


def _add_campaign_args(parser: argparse.ArgumentParser) -> None:
    # build_parser() calls this for every invocation regardless of
    # subcommand chosen, so it stays import-free. runner.DEFAULT_K and
    # run_skill_evals.DEFAULT_WORKERS are read lazily in _run_or_smoke
    # instead, via the None defaults below.
    parser.add_argument("--defects-path", default=str(DEFAULT_DEFECTS_PATH), help="The committed defect-set file.")
    parser.add_argument(
        "--defect-id", action="append", default=[],
        help="Restrict the campaign to this defect ID (repeatable); default: every confirmed defect.",
    )
    parser.add_argument("--arms-root", default=str(DEFAULT_ARMS_ROOT), help="Root holding <arm>/bench-<lens>.md snapshots.")
    parser.add_argument(
        "--run-store-dir", default=str(DEFAULT_RUN_STORE_DIR),
        help="Local run-store directory (write-ahead record + lock).",
    )
    parser.add_argument(
        "--records-dir", default=str(DEFAULT_RECORDS_DIR), help="Where this campaign's RunRecord JSONL is written.",
    )
    parser.add_argument("--campaign-id", default=None, help="Campaign ID (default: <subcommand>-<random>).")
    parser.add_argument(
        "--k", type=int, default=None, help="Runs per arm per defect (default: review_bench.runner.DEFAULT_K).",
    )
    parser.add_argument("--seed", type=int, default=0, help="Campaign seed for the per-defect block shuffle.")
    parser.add_argument(
        "--workers", type=int, default=None,
        help="Worker pool size per block (default: run_skill_evals.DEFAULT_WORKERS).",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="A-bench defect-set mining and confirmation CLI.")
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

    p_confirm = sub.add_parser(
        "confirm",
        help="Promote every engineer-approved .local/ candidate into the committed defects.json.",
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
        help="Fault-injectable dry-run campaign against the real CLI -- never the baseline campaign.",
    )
    _add_campaign_args(p_smoke)
    p_smoke.add_argument(
        "--inject-fault", choices=("wrong-agent", "extra-tool-call"), default=None,
        help="Force a real launch through one named validity-check failure, exercising retry-then-missing.",
    )
    p_smoke.set_defaults(func=cmd_smoke)

    # `run` has no --inject-fault: fault injection is smoke-only. Omitting
    # the flag here is the rejection itself -- argparse exits 2 on an
    # unrecognized argument.
    p_run = sub.add_parser("run", help="The real reviewer campaign (baseline or a later arm's rerun).")
    _add_campaign_args(p_run)
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
    p_analyze.add_argument("--k", type=int, required=True, help="K used for this campaign (for the K/2 drop rule).")
    p_analyze.add_argument(
        "--arm-x", default=None,
        help="The non-baseline arm to report against current-rule (default: function-context).",
    )
    p_analyze.add_argument(
        "--baseline-conditions-path", default=None,
        help="A frozen baseline's conditions.json -- omit to compute the baseline's own sensitivity verdict "
        "instead of a later arm's non-inferiority verdicts.",
    )
    p_analyze.add_argument("--out", default=None, help="Optional path to write the machine-readable report JSON.")
    p_analyze.set_defaults(func=cmd_analyze)

    p_freeze = sub.add_parser("freeze", help="Write evals/review_bench/conditions.json (run once, before any baseline campaign).")
    p_freeze.add_argument("--defects-path", default=str(DEFAULT_DEFECTS_PATH), help="The committed defect-set file.")
    p_freeze.add_argument("--local-dir", default=str(DEFAULT_LOCAL_DIR), help="Where the miners' shortlists live.")
    p_freeze.add_argument("--arms-root", default=str(DEFAULT_ARMS_ROOT), help="Root holding <arm>/bench-<lens>.md snapshots.")
    p_freeze.add_argument("--k", type=int, required=True, help="K being frozen.")
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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
