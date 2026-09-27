#!/usr/bin/env python3
"""A-bench CLI: `mine-szz`, `mine-rounds`, `confirm` for evals/review_bench's
known-defect set, plus `snapshot-arms`, `smoke`, and `run` for its fixture
and runner harness (dispatch 1b).

LOCAL USE ONLY -- never run in CI. `mine-rounds` reads this account's own
session transcripts; `mine-szz` and `confirm` read this repo's own git
history. Every miner writes to the gitignored evals/review_bench/.local/;
only `confirm` writes to the committed evals/review_bench/defects.json, and
only for a candidate the engineer has already approved. `smoke` and `run`
launch real `claude -p` sessions against real Claude subscription auth.

See .claude/plans/measure-review-quality.md's Approach > Defect set for the
mining algorithms, Approach > "Fixtures and arms" and "Runs and
adjudication" for the runner design, and Critical files' Dispatch 1a/1b
sections for this CLI's spec.
"""
from __future__ import annotations

import argparse
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


def cmd_mine_szz(args: argparse.Namespace) -> int:
    from review_bench import defects, mine_szz

    candidates = mine_szz.mine(REPO_ROOT, base_ref=args.base_ref)
    out_path = Path(args.local_dir) / "szz_candidates.json"
    defects.save_candidates(out_path, candidates)
    print(f"mine-szz: wrote {len(candidates)} candidate(s) to {out_path}", file=sys.stderr)
    return 0


def cmd_mine_rounds(args: argparse.Namespace) -> int:
    # Lazy import: transcript_analysis is a large module tree with no
    # bearing on the frozen harness constants, so it must stay out of M9's
    # content-hash import closure (Critical files, Dispatch 1a).
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
    # description confirms -- the drafting session saw the whole shortlist (M12).
    excerpts_by_id = {c.id: c.excerpt for c in candidates if c.excerpt}

    defects_path = Path(args.defects_path)
    existing = defects.load_confirmed_defects(defects_path)
    existing_ids = {d.id for d in existing}

    appended: list[defects.ConfirmedDefect] = []
    rejected = 0
    for candidate in candidates:
        # A candidate with no description is not yet engineer-approved;
        # one already in defects.json is a no-op retry, not a rejection.
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
            # No other excerpt text ever reaches the terminal here (row 41) --
            # only the candidate ID, the matched run, and its source candidate ID.
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
        appended.append(defect)

    # No override: a rejected entry is never written, on this or any later run,
    # until the engineer edits its own .local/ description and reruns confirm.
    if appended:
        # existing_ids was read at load time; if it no longer matches the
        # file's current defect count, a concurrent confirm run appended in
        # the meantime -- abort rather than blindly overwrite it with a
        # stale existing + appended.
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
    from review_bench import runner

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
    # Both live checkouts the per-run validity check must contain a leak
    # into: this harness's own, and the one the ambient config resolves
    # into -- distinct under worktree isolation (Approach > "Per-run
    # validity checks").
    live_checkout_roots = runner.default_live_checkout_roots()

    def build_spec(defect_id: str):
        defect = next(d for d in selected if d.id == defect_id)
        return runner.build_defect_fixture_spec(
            defect, arm_names=(runner.arms_mod.ARM_CURRENT_RULE, runner.arms_mod.ARM_FUNCTION_CONTEXT),
            source_repo=REPO_ROOT, live_checkout_roots=live_checkout_roots,
            arms_snapshot_root=Path(args.arms_root), run_store=run_store,
        )

    result = runner.run_campaign(
        [d.id for d in selected], build_spec=build_spec,
        arms=(runner.arms_mod.ARM_CURRENT_RULE, runner.arms_mod.ARM_FUNCTION_CONTEXT),
        k=args.k, seed=args.seed, campaign_id=campaign_id, run_store=run_store,
        records_path=records_path, projects_root=runner.config_dir() / "projects",
        fault=fault, workers=args.workers,
    )
    total = sum(len(block.records) for block in result.block_results.values())
    print(
        f"{args.subcommand}: campaign {campaign_id} ran {total} run(s) across {len(result.block_results)} defect(s)",
        file=sys.stderr,
    )
    return 0


def cmd_smoke(args: argparse.Namespace) -> int:
    return _run_or_smoke(args, fault=args.inject_fault)


def cmd_run(args: argparse.Namespace) -> int:
    return _run_or_smoke(args, fault=None)


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
    # on exactly one config-dir root (Critical files, Dispatch 1a).
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
        help="Render both arms' bench-<lens>.md files from production at the freeze commit (PR 2).",
    )
    p_snapshot.add_argument("--arms-root", default=str(DEFAULT_ARMS_ROOT), help="Where to write <arm>/bench-<lens>.md.")
    p_snapshot.set_defaults(func=cmd_snapshot_arms)

    p_smoke = sub.add_parser(
        "smoke",
        help="Fault-injectable dry-run campaign against the real CLI (gate 6) -- never the baseline campaign.",
    )
    _add_campaign_args(p_smoke)
    p_smoke.add_argument(
        "--inject-fault", choices=("wrong-agent", "extra-tool-call"), default=None,
        help="Force a real launch through one named validity-check failure, exercising retry-then-missing.",
    )
    p_smoke.set_defaults(func=cmd_smoke)

    # `run` has no --inject-fault: fault injection is smoke-only. Omitting
    # the flag here is the rejection itself -- argparse exits 2 on an
    # unrecognized argument (Verification: "run rejects the smoke-only
    # fault-injection options").
    p_run = sub.add_parser("run", help="The real reviewer campaign (baseline or a later arm's rerun).")
    _add_campaign_args(p_run)
    p_run.set_defaults(func=cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
