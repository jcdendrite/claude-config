#!/usr/bin/env python3
"""A-bench CLI: `mine-szz`, `mine-rounds`, and `confirm` for
evals/review_bench's known-defect set.

LOCAL USE ONLY -- never run in CI. `mine-rounds` reads this account's own
session transcripts; `mine-szz` and `confirm` read this repo's own git
history. Every miner writes to the gitignored evals/review_bench/.local/;
only `confirm` writes to the committed evals/review_bench/defects.json, and
only for a candidate the engineer has already approved.

See .claude/plans/measure-review-quality.md's Approach > Defect set for the
mining algorithms and Critical files' Dispatch 1a section for this CLI's spec.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EVALS_DIR = Path(__file__).resolve().parent

DEFAULT_LOCAL_DIR = EVALS_DIR / "review_bench" / ".local"
DEFAULT_DEFECTS_PATH = EVALS_DIR / "review_bench" / "defects.json"


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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
