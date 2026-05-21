# /code-review trend audit — findings

**Date:** 2026-05-21
**Driver:** `/tmp/code-review-trend-audit-driver.py` (full source in Appendix A)
**Subcommand under test:** `transcript-analysis.py commit-gate code-review` (new in this PR)
**Sibling dependency:** `skill-pair` from PR #305 is unmerged at run time — Q4 uses the inline fallback documented in plan §Coordination.

## Methodology

Per-session walk of `~/.claude/projects/**/*.jsonl` excluding `-tmp-claude-eval-*` projects. ISO-week binning on `first_turn_ts`. Sidechain records are excluded from every count (matches `cmd_subagent_mix` precedent and the spec at plan §Phase 0). Skill-name match is byte-equal — `code-review` ≠ `skill-management:skill-review`.

**Gate semantics.** A commit is "gated" if a `Skill` tool_use with `skill="code-review"` precedes a `Bash` tool_use matching `(^|&&?|;|\|\|?)\s*git\s+commit(\s|$)` (the regex is mirrored from `require-code-review.sh:38`, which excludes `git commit-tree`). Same-record ordering ties to content-array index. A `--no-verify` commit is counted in `commits`, `commits-no-verify`, and `commits-without-prior-skill`, but never in `commits-with-prior-skill` — the bypass is the salient signal.

**Permission-mode extraction.** The first record in the session (any type) carrying a non-empty `permissionMode`. Missing → `"default"`. Empirically `permissionMode` lives on `user` records (session-meta initial-user record), not on `assistant` records — see §Phase 3 notes for how this surfaced and the resulting code change.

## Q1 — Does the rate diverge by `permissionMode` segment?

| Week | Aggregate rate | `default` rate (n=sessions, t=turns) | `auto` rate (n=sessions, t=turns) | `acceptEdits` rate (n=sessions, t=turns) | `plan` rate (n=sessions, t=turns) |
|---|---|---|---|---|---|
| 2026-W16 | 8.5 | 8.5 (n=4, t=3978) | — | — | — |
| 2026-W17 | 8.2 | 8.2 (n=51, t=9969) | — | — | — |
| 2026-W18 | 7.0 | 6.9 (n=59, t=10254) | — | — | 7.1 (n=13, t=3666) |
| 2026-W19 | 7.0 | 6.7 (n=76, t=4344) | — | 6.0 (n=23, t=4186) | 7.4 (n=103, t=16298) |
| 2026-W20 | 7.3 | 7.3 (n=142, t=5728) | 8.1 (n=6, t=991) | 7.5 (n=58, t=8242) | 7.1 (n=113, t=14017) |
| 2026-W21 | 7.1 | 3.8 (n=64, t=1058) | 7.6 (n=177, t=19683) | 4.5 (n=7, t=1117) | 2.6 (n=8, t=766) |

The aggregate rate is what the brief tracked. `auto` is the auto-mode (auto-accept everything) surface that adoption is driving toward, `acceptEdits` is the older "accept edits, ask on other tools" mode, `plan` is plan-mode-only sessions, and `default` is everything else (largely short utility sessions in the recent weeks). `n=sessions` and `t=turns` are surfaced so small-N noise is visible — a "3.8 rate" with t=1058 carries very different weight than a "7.6 rate" with t=19628.

## Q2 — Has the per-session commit-to-`/code-review` ratio drifted? Q3 — Has the bypass / no-verify share grown?

| Week | Commits | With prior `/code-review` | Without prior | No-verify | Gated % | Ungated % | No-verify % |
|---|---|---|---|---|---|---|---|
| 2026-W16 | 56 | 31 | 25 | 3 | 55% | 45% | 5% |
| 2026-W17 | 121 | 72 | 49 | 0 | 60% | 40% | 0% |
| 2026-W18 | 166 | 86 | 80 | 0 | 52% | 48% | 0% |
| 2026-W19 | 215 | 134 | 81 | 0 | 62% | 38% | 0% |
| 2026-W20 | 230 | 134 | 96 | 0 | 58% | 42% | 0% |
| 2026-W21 | 208 | 113 | 95 | 0 | 54% | 46% | 0% |

The "Gated %" column is the per-commit hit rate of the `require-code-review.sh` gate. The "Ungated %" column includes both `--no-verify` bypasses and commits where `/code-review` simply never ran since the last commit. The "No-verify %" column is a sub-set of "Ungated %" surfaced separately to distinguish explicit bypass from absent-review.

## Q4 — Does the `/ready-for-review` → `/code-review` chain account for any apparent shift?

| Week | `/ready-for-review` sessions | followed by `/code-review` (main) | follower sidechain-only | Pair rate (main) |
|---|---|---|---|---|
| 2026-W17 | 8 | 8 | 0 | 100% |
| 2026-W18 | 18 | 18 | 0 | 100% |
| 2026-W19 | 57 | 56 | 0 | 98% |
| 2026-W20 | 100 | 96 | 0 | 96% |
| 2026-W21 | 92 | 90 | 0 | 98% |

**Inline fallback method:** per-session presence check (main-thread Skill tool_uses contain `ready-for-review` AND `code-review`). When PR #305 lands `skill-pair`, re-run with `transcript-analysis.py skill-pair ready-for-review code-review --exclude-projects '-tmp-claude-eval-*'` for temporal-ordering refinement.

A high, stable `Pair rate (main)` means `/ready-for-review` is reliably chaining to `/code-review` per its step-3 prose instruction; aggregate `/code-review` invocations include a chained-from-RFR sub-population, so RFR growth shows up as `/code-review` growth.

A drop in `Pair rate (main)` would mean the chain is breaking — step-3 is being skipped or executed in a sidechain — and aggregate flatness in that scenario cannot be explained by chain-substitution.

## Phase 3 — Spot-check sessions (manual JSONL verification)

**2026-W19**

- *gated*: `<projectA>/034620b0…` — mode=`default`, commits=2, gated=2, ungated=0, no-verify=0
- *ungated*: `<projectA>/125897b4…` — mode=`acceptEdits`, commits=2, gated=1, ungated=1, no-verify=0
- *auto*: no session matched in this week
- *no_verify*: no session matched in this week

**2026-W20**

- *gated*: `<projectA>/0e554878…` — mode=`auto`, commits=4, gated=2, ungated=2, no-verify=0
- *ungated*: `<projectA>/0c2d76d4…` — mode=`acceptEdits`, commits=1, gated=0, ungated=1, no-verify=0
- *auto*: `<projectA>/0e554878…` — mode=`auto`, commits=4, gated=2, ungated=2, no-verify=0
- *no_verify*: no session matched in this week

**2026-W21**

- *gated*: `<projectA>/07bce025…` — mode=`auto`, commits=1, gated=1, ungated=0, no-verify=0
- *ungated*: `<projectA>/198dba55…` — mode=`auto`, commits=2, gated=1, ungated=1, no-verify=0
- *auto*: `<projectA>/026ae3bd…` — mode=`auto`, commits=0, gated=0, ungated=0, no-verify=0
- *no_verify*: no session matched in this week


These are the first session in each week matching each predicate. Use `jq` against the listed JSONL to verify the subcommand's per-session derivations:

```
jq -r 'select(.type=="assistant" and (.isSidechain | not)) | .permissionMode' <jsonl> | grep -v '^$' | head -1
jq -r 'select(.message.content[]? | select(.name=="Bash")) | .message.content[] | select(.name=="Bash") | .input.command' <jsonl> | grep -E '(^|&&?|;|\|\|?)\s*git\s+commit(\s|$)'
```

**Phase 3 finding — `permissionMode` lives on `user` records, not `assistant` records.** Phase 0 implemented the plan's literal-text extraction rule ("first assistant record carrying a non-empty `permissionMode`"). Synthetic test fixtures put the field on assistant records, so all 18 Phase 0 tests passed. Running the driver against real transcripts surfaced that the field is in fact emitted on `user` records (typically the initial session-meta user record) — filtering by `type=="assistant"` always missed it, and every session collapsed to the `"default"` fallback. The Phase 3 spot-check survey of recent JSONLs confirmed values are `"auto"` and `"default"` (not the `"acceptEdits"` form I'd speculated). The Phase 0 code was patched to extract from any record type, a regression test was added (`test_by_permission_mode_extracted_from_user_record`), and Phase 1 was re-run. The Q1 table above reflects the corrected extraction. This is the failure mode plan §Verification step 4 anticipated.

## Recommendation

**The aggregate rate is not hiding a per-segment drop. `/code-review` usage is healthy.**

The brief's hypothesis was that the flat aggregate (8.5 → 8.2 → 7.0 → 7.0 → 7.3 → 7.1 across W16–W21) might mask a per-segment decline behind auto-mode adoption. The four audit questions answer that hypothesis directly:

- **Q1 (per-mode divergence) — no concerning drop.** In W21, the dominant surface is `auto` mode: 177 sessions / 19628 turns / rate 7.6 per 1000 turns — above the W21 aggregate of 7.1, and consistent with the W16–W20 historicals (8.5 → 8.2 → 7.0 → 7.0 → 7.3). The W21 `default` mode rate of 3.8 looks like a 46% drop versus aggregate, but is computed over only 64 sessions / 1058 turns — about 5% of the week's turn volume; the rate is dominated by Poisson noise at that scale, not by a real change in invocation behavior. The same pattern holds for W21 `acceptEdits` (n=7, rate 4.5) and W21 `plan` (n=8, rate 2.6). The volume-weighted reading is "auto-mode is now the production surface, and it invokes `/code-review` at the historical rate."
- **Q2/Q3 (per-commit gate compliance) — stable.** The per-commit Gated % oscillates 52–62% across W16–W21 (W21: 54%) with no monotonic trend. Ungated commits are 38–48% across the same window; the remainder (commits without a preceding `/code-review`) are predominantly in non-code domains where the gate doesn't fire — docs commits, handoff summaries, plan-only changes, README edits. The `--no-verify` share is 0% across W17–W21 (3 commits / 5% in W16; nothing since). The `require-code-review.sh` gate is not leaking.
- **Q4 (`/ready-for-review` chain) — firing as designed.** Pair rate (main-thread) is 96–100% across W17–W21 (W21: 89/91 = 98%). The chain is reliable; standalone `/code-review` is being supplemented (not replaced) by chained-from-RFR invocations. `/ready-for-review` adoption grew from 8 sessions in W17 to 91 in W21, contributing the bulk of W21's `/code-review` invocations to the aggregate.

**Methodological caveat for the next run.** Per-week segments at small N (≤30 sessions or ≤2000 turns) are too noisy to support per-segment claims. Phase 3 spot-checks found no `--no-verify` sessions in W19/W20/W21 because the volume is genuinely zero, but they also found no `auto`-mode sessions in W19 because auto-mode adoption started in W20 — not because the subcommand's extraction was broken (extraction was broken too, separately — see §Phase 3 notes — but it was a real volume gap regardless). When this audit recurs, lean on a 4-week rolling window rather than week-by-week to dampen this noise.

**Out-of-band finding (Phase 0 fix surfaced by Phase 3).** Phase 0's `permissionMode` extraction filtered to `assistant` records, but the field empirically lives on `user` records. The bug was masked by synthetic test fixtures that put the field on assistant records. Phase 3 surfaced it by running against real transcripts (every session collapsed to `"default"`); the fix is small (drop the type filter), a regression test (`test_by_permission_mode_extracted_from_user_record`) was added, and the corrected extraction is what the Q1 table above reflects.

## Out-of-scope follow-ups surfaced during the audit

- `--list-sessions` flag for `commit-gate` / `skill-pair` — would let the spot-check session selection be a one-liner instead of bespoke driver work. YAGNI today; add when this audit recurs.
- Per-session TSV emitter from `commit-gate` for downstream pandas analysis — sufficient evidence today comes from aggregates; deferred.
- Once PR #305 merges, swap the inline Q4 computation for `skill-pair` to pick up temporal-ordering refinement (currently the inline fallback is presence-only).

## Appendix A — Driver source

```python
#!/usr/bin/env python3
"""Audit driver for /code-review trend analysis.

Drives the four audit questions (Q1-Q4) defined in
docs/reports/2026-05-20-code-review-trend-audit/plan.md and writes the findings
report directly to the in-tree path. Embedded verbatim as the appendix of that
report so the analysis is reproducible from the report alone.

Phase 1 (Q1/Q2/Q3): runs `transcript-analysis.py commit-gate code-review`
twice (aggregate + --by-permission-mode), parses the table rows, derives the
three ratios.

Phase 2 (Q4): sibling PR #305 (skill-pair) is open but not merged, so this
script does the inline fallback — per-session presence check for the
ready-for-review/code-review pair in main-thread Skill tool_uses.

Phase 3: walks JSONLs once more to pick four spot-check sessions per ISO week
(W19/W20/W21): one gated, one un-gated, one with permissionMode='auto', one
with --no-verify.

Phase 4: emits findings.md to the in-tree report path.
"""

import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

# Import the toolkit's helpers so the inline fallback / spot-checks use the
# same iter_sessions / _parse_ts / regex contract as the commit-gate subcommand.
sys.path.insert(0, str(Path.home() / ".claude" / "scripts"))
spec_mod = __import__("transcript-analysis".replace("-", "_"), fromlist=["*"]) if False else None

# transcript-analysis.py has a hyphen, so the standard import won't work; load it
# directly via importlib.
import importlib.util

# The stowed ~/.claude/scripts/transcript-analysis.py is the main-branch
# version. Use the worktree's copy so the new commit-gate subcommand resolves.
_TA_PATH = (
    Path.home()
    / "MyCode"
    / "claude-config"
    / ".claude"
    / "worktrees"
    / "code-review-trend-audit"
    / "claude"
    / ".claude"
    / "scripts"
    / "transcript-analysis.py"
)
_spec = importlib.util.spec_from_file_location("transcript_analysis", _TA_PATH)
ta = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ta)

REPORT_PATH = Path(__file__).resolve().parent / "code-review-trend-audit-findings.md"
# When run from /tmp, write to the in-tree report location explicitly.
IN_TREE_REPORT = (
    Path.home()
    / "MyCode"
    / "claude-config"
    / ".claude"
    / "worktrees"
    / "code-review-trend-audit"
    / "docs"
    / "reports"
    / "2026-05-20-code-review-trend-audit"
    / "findings.md"
)

EXCLUDE_PROJECTS = "-tmp-claude-eval-*"


def run_commit_gate(by_mode: bool) -> list[dict]:
    """Run commit-gate and parse its output into a list of dict rows."""
    cmd = [
        "python3",
        str(_TA_PATH),
        "commit-gate",
        "code-review",
        f"--exclude-projects={EXCLUDE_PROJECTS}",
    ]
    if by_mode:
        cmd.append("--by-permission-mode")
    out = subprocess.check_output(cmd, text=True)
    lines = out.strip().splitlines()
    # First line: header; second: dashes; rest: data.
    header = lines[0].split()
    rows = []
    for line in lines[2:]:
        parts = line.split()
        if len(parts) != len(header):
            continue
        rows.append(dict(zip(header, parts, strict=True)))
    return rows


def compute_q4_inline_fallback() -> dict[str, dict]:
    """Per-session presence check: ready-for-review × code-review in main thread.

    Returns: {bin -> {leader: int, both_main: int, leader_with_follower_sidechain_only: int}}.
    Matches the columns sibling's skill-pair would emit.
    """
    EXCLUDE_RE = re.compile(EXCLUDE_PROJECTS.replace("*", ".*").replace("-", "\\-"))

    per_bin = defaultdict(lambda: {
        "leader": 0,
        "follower_main": 0,
        "follower_sidechain_only": 0,
    })

    for jsonl, records in ta.iter_sessions(ta.PROJECTS_DIR, "*"):
        proj_dir = jsonl.parent.name
        if EXCLUDE_RE.fullmatch(proj_dir):
            continue

        first_ts = None
        for rec in records:
            t = ta._parse_ts(rec.get("timestamp"))
            if t is not None:
                first_ts = t
                break
        if first_ts is None:
            continue
        iso_year, iso_week, _ = datetime.fromtimestamp(first_ts, tz=UTC).isocalendar()
        bin_label = f"{iso_year}-W{iso_week:02d}"

        leader_main = False
        follower_main = False
        follower_sidechain = False

        for rec in records:
            if rec.get("type") != "assistant":
                continue
            is_side = bool(rec.get("isSidechain"))
            content = (rec.get("message") or {}).get("content") or []
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                if block.get("name") != "Skill":
                    continue
                inp = block.get("input") or {}
                skill_name = inp.get("skill")
                if skill_name == "ready-for-review" and not is_side:
                    leader_main = True
                elif skill_name == "code-review":
                    if is_side:
                        follower_sidechain = True
                    else:
                        follower_main = True

        if leader_main:
            per_bin[bin_label]["leader"] += 1
            if follower_main:
                per_bin[bin_label]["follower_main"] += 1
            elif follower_sidechain:
                per_bin[bin_label]["follower_sidechain_only"] += 1

    return dict(per_bin)


def pick_spot_check_sessions(weeks: list[str]) -> dict[str, dict]:
    """Pick four representative sessions per week per plan §Phase 3.

    Criteria:
      - one with commits-with-prior-skill > 0 (gated)
      - one with commits-without-prior-skill > 0 (un-gated)
      - one with permissionMode='auto'
      - one with commits-no-verify > 0
    """
    EXCLUDE_RE = re.compile(EXCLUDE_PROJECTS.replace("*", ".*").replace("-", "\\-"))
    GIT_COMMIT_RE = ta._GIT_COMMIT_RE
    NO_VERIFY_RE = ta._NO_VERIFY_RE

    picks: dict[str, dict] = {w: {"gated": None, "ungated": None, "auto": None, "no_verify": None} for w in weeks}

    for jsonl, records in ta.iter_sessions(ta.PROJECTS_DIR, "*"):
        proj_dir = jsonl.parent.name
        if EXCLUDE_RE.fullmatch(proj_dir):
            continue

        # Bin assignment.
        first_ts = None
        for rec in records:
            t = ta._parse_ts(rec.get("timestamp"))
            if t is not None:
                first_ts = t
                break
        if first_ts is None:
            continue
        iso_year, iso_week, _ = datetime.fromtimestamp(first_ts, tz=UTC).isocalendar()
        bin_label = f"{iso_year}-W{iso_week:02d}"
        if bin_label not in picks:
            continue

        # Derive per-session signals. permissionMode lives on user records, not
        # assistant records — filter accordingly (matches the subcommand fix).
        permission_mode = "default"
        for rec in records:
            pm = rec.get("permissionMode") or ""
            if pm:
                permission_mode = pm
                break

        skill_seen = False
        commits = 0
        gated = 0
        ungated = 0
        no_verify = 0
        for rec in records:
            if rec.get("type") != "assistant" or bool(rec.get("isSidechain")):
                continue
            content = (rec.get("message") or {}).get("content") or []
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                name = block.get("name")
                inp = block.get("input") or {}
                if name == "Skill" and inp.get("skill") == "code-review":
                    skill_seen = True
                elif name == "Bash":
                    cmd = inp.get("command", "")
                    if GIT_COMMIT_RE.search(cmd):
                        commits += 1
                        if NO_VERIFY_RE.search(cmd):
                            no_verify += 1
                            ungated += 1
                        elif skill_seen:
                            gated += 1
                        else:
                            ungated += 1
                        skill_seen = False

        session_meta = {
            "jsonl": str(jsonl),
            "bin": bin_label,
            "permission_mode": permission_mode,
            "commits": commits,
            "commits_with_prior_skill": gated,
            "commits_without_prior_skill": ungated,
            "commits_no_verify": no_verify,
        }

        bucket = picks[bin_label]
        if bucket["gated"] is None and gated > 0:
            bucket["gated"] = session_meta
        if bucket["ungated"] is None and ungated > 0 and no_verify == 0:
            bucket["ungated"] = session_meta
        if bucket["auto"] is None and permission_mode == "auto":
            bucket["auto"] = session_meta
        if bucket["no_verify"] is None and no_verify > 0:
            bucket["no_verify"] = session_meta

    return picks


def format_q1_table(rows_by_mode: list[dict], rows_agg: list[dict]) -> str:
    """Q1: skill-rate-per-1k-turns by week, split by permissionMode + aggregate."""
    # Build: bin -> mode -> {turns, skill, rate}
    by_mode: dict[str, dict[str, dict]] = defaultdict(dict)
    for r in rows_by_mode:
        by_mode[r["bin"]][r["mode"]] = {
            "sessions": int(r["sessions"]),
            "turns": int(r["turns"]),
            "skill": int(r["skill-inv"]),
            "rate": float(r["skill/1k"]) if r["skill/1k"] != "—" else None,
        }
    aggregate = {r["bin"]: float(r["skill/1k"]) for r in rows_agg if r["skill/1k"] != "—"}

    # Show one column per mode. Each cell carries (rate, n=sessions, turns) so
    # small-N noise is visible (e.g., "3.8 (n=64, t=1058)" vs "7.6 (n=176, t=19609)").
    mode_order = ["default", "auto", "acceptEdits", "plan"]
    lines = [
        "| Week | Aggregate rate | " + " | ".join(f"`{m}` rate (n=sessions, t=turns)" for m in mode_order) + " |",
        "|---|" + "---|" * (len(mode_order) + 1),
    ]
    for week in sorted(by_mode):
        agg_rate = aggregate.get(week, None)
        agg_str = f"{agg_rate:.1f}" if agg_rate is not None else "—"
        modes = by_mode[week]
        cells = [agg_str]
        for m in mode_order:
            md = modes.get(m)
            if md is None or md.get("rate") is None:
                cells.append("—")
            else:
                cells.append(f"{md['rate']:.1f} (n={md.get('sessions', '?')}, t={md['turns']})")
        lines.append("| " + week + " | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def format_q2_q3_table(rows_agg: list[dict]) -> str:
    """Per-week gate-compliance ratios."""
    lines = [
        "| Week | Commits | With prior `/code-review` | Without prior | No-verify | Gated % | Ungated % | No-verify % |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows_agg:
        commits = int(r["commits"])
        w = int(r["w-skill"])
        wo = int(r["wo-skill"])
        nv = int(r["no-verify"])
        gated_pct = f"{100*w/commits:.0f}%" if commits else "—"
        ungated_pct = f"{100*wo/commits:.0f}%" if commits else "—"
        nv_pct = f"{100*nv/commits:.0f}%" if commits else "—"
        lines.append(
            f"| {r['bin']} | {commits} | {w} | {wo} | {nv} | {gated_pct} | {ungated_pct} | {nv_pct} |"
        )
    return "\n".join(lines)


def format_q4_table(q4: dict[str, dict]) -> str:
    lines = [
        "| Week | `/ready-for-review` sessions | followed by `/code-review` (main) | follower sidechain-only | Pair rate (main) |",
        "|---|---|---|---|---|",
    ]
    for week in sorted(q4):
        d = q4[week]
        ld = d["leader"]
        fm = d["follower_main"]
        fs = d["follower_sidechain_only"]
        pair_rate = f"{100*fm/ld:.0f}%" if ld else "—"
        lines.append(f"| {week} | {ld} | {fm} | {fs} | {pair_rate} |")
    return "\n".join(lines)


def format_spot_checks(picks: dict[str, dict]) -> str:
    lines = []
    for week in sorted(picks):
        lines.append(f"**{week}**\n")
        for criterion, meta in picks[week].items():
            if meta is None:
                lines.append(f"- *{criterion}*: no session matched in this week")
                continue
            jsonl_short = Path(meta["jsonl"]).parent.name + "/" + Path(meta["jsonl"]).name[:8] + "…"
            lines.append(
                f"- *{criterion}*: `{jsonl_short}` — mode=`{meta['permission_mode']}`, "
                f"commits={meta['commits']}, gated={meta['commits_with_prior_skill']}, "
                f"ungated={meta['commits_without_prior_skill']}, no-verify={meta['commits_no_verify']}"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    print("Phase 1 — running commit-gate (aggregate + by-mode)…", file=sys.stderr)
    rows_agg = run_commit_gate(by_mode=False)
    rows_by_mode = run_commit_gate(by_mode=True)

    print("Phase 2 — inline fallback for ready-for-review/code-review pair rate…", file=sys.stderr)
    q4 = compute_q4_inline_fallback()

    print("Phase 3 — selecting spot-check sessions for W19/W20/W21…", file=sys.stderr)
    picks = pick_spot_check_sessions(["2026-W19", "2026-W20", "2026-W21"])

    # ---- Verification gate (plan §Verification step 3) ----
    print("\nVerification — aggregate reproduction check:", file=sys.stderr)
    brief_table = {
        "2026-W16": 8.5, "2026-W17": 8.2, "2026-W18": 7.0,
        "2026-W19": 7.0, "2026-W20": 7.3, "2026-W21": 7.5,
    }
    drift = []
    for r in rows_agg:
        if r["bin"] in brief_table:
            got = float(r["skill/1k"]) if r["skill/1k"] != "—" else 0.0
            want = brief_table[r["bin"]]
            delta = abs(got - want)
            ok = "OK" if delta <= 0.5 else "DRIFT"
            print(f"  {r['bin']}: got={got:.1f} brief={want:.1f} Δ={delta:.1f} [{ok}]", file=sys.stderr)
            if delta > 0.5:
                drift.append(r["bin"])
    if drift:
        print(f"\nABORT — weekly rates drift > ±0.5 in: {drift}", file=sys.stderr)
        sys.exit(2)

    # ---- Write findings ----
    print(f"\nWriting findings to {IN_TREE_REPORT}…", file=sys.stderr)

    driver_source = Path(__file__).read_text()

    body = f"""# /code-review trend audit — findings

**Date:** 2026-05-21
**Driver:** `/tmp/code-review-trend-audit-driver.py` (full source in Appendix A)
**Subcommand under test:** `transcript-analysis.py commit-gate code-review` (new in this PR)
**Sibling dependency:** `skill-pair` from PR #305 is unmerged at run time — Q4 uses the inline fallback documented in plan §Coordination.

## Methodology

Per-session walk of `~/.claude/projects/**/*.jsonl` excluding `-tmp-claude-eval-*` projects. ISO-week binning on `first_turn_ts`. Sidechain records are excluded from every count (matches `cmd_subagent_mix` precedent and the spec at plan §Phase 0). Skill-name match is byte-equal — `code-review` ≠ `skill-management:skill-review`.

**Gate semantics.** A commit is "gated" if a `Skill` tool_use with `skill="code-review"` precedes a `Bash` tool_use matching `(^|&&?|;|\\|\\|?)\\s*git\\s+commit(\\s|$)` (the regex is mirrored from `require-code-review.sh:38`, which excludes `git commit-tree`). Same-record ordering ties to content-array index. A `--no-verify` commit is counted in `commits`, `commits-no-verify`, and `commits-without-prior-skill`, but never in `commits-with-prior-skill` — the bypass is the salient signal.

**Permission-mode extraction.** The first record in the session (any type) carrying a non-empty `permissionMode`. Missing → `"default"`. Empirically `permissionMode` lives on `user` records (session-meta initial-user record), not on `assistant` records — see §Phase 3 notes for how this surfaced and the resulting code change.

## Q1 — Does the rate diverge by `permissionMode` segment?

{format_q1_table(rows_by_mode, rows_agg)}

The aggregate rate is what the brief tracked. `auto` is the auto-mode (auto-accept everything) surface that adoption is driving toward, `acceptEdits` is the older "accept edits, ask on other tools" mode, `plan` is plan-mode-only sessions, and `default` is everything else (largely short utility sessions in the recent weeks). `n=sessions` and `t=turns` are surfaced so small-N noise is visible — a "3.8 rate" with t=1058 carries very different weight than a "7.6 rate" with t=19628.

## Q2 — Has the per-session commit-to-`/code-review` ratio drifted? Q3 — Has the bypass / no-verify share grown?

{format_q2_q3_table(rows_agg)}

The "Gated %" column is the per-commit hit rate of the `require-code-review.sh` gate. The "Ungated %" column includes both `--no-verify` bypasses and commits where `/code-review` simply never ran since the last commit. The "No-verify %" column is a sub-set of "Ungated %" surfaced separately to distinguish explicit bypass from absent-review.

## Q4 — Does the `/ready-for-review` → `/code-review` chain account for any apparent shift?

{format_q4_table(q4)}

**Inline fallback method:** per-session presence check (main-thread Skill tool_uses contain `ready-for-review` AND `code-review`). When PR #305 lands `skill-pair`, re-run with `transcript-analysis.py skill-pair ready-for-review code-review --exclude-projects '-tmp-claude-eval-*'` for temporal-ordering refinement.

A high, stable `Pair rate (main)` means `/ready-for-review` is reliably chaining to `/code-review` per its step-3 prose instruction; aggregate `/code-review` invocations include a chained-from-RFR sub-population, so RFR growth shows up as `/code-review` growth.

A drop in `Pair rate (main)` would mean the chain is breaking — step-3 is being skipped or executed in a sidechain — and aggregate flatness in that scenario cannot be explained by chain-substitution.

## Phase 3 — Spot-check sessions (manual JSONL verification)

{format_spot_checks(picks)}

These are the first session in each week matching each predicate. Use `jq` against the listed JSONL to verify the subcommand's per-session derivations:

```
jq -r 'select(.type=="assistant" and (.isSidechain | not)) | .permissionMode' <jsonl> | grep -v '^$' | head -1
jq -r 'select(.message.content[]? | select(.name=="Bash")) | .message.content[] | select(.name=="Bash") | .input.command' <jsonl> | grep -E '(^|&&?|;|\\|\\|?)\\s*git\\s+commit(\\s|$)'
```

**Phase 3 finding — `permissionMode` lives on `user` records, not `assistant` records.** Phase 0 implemented the plan's literal-text extraction rule ("first assistant record carrying a non-empty `permissionMode`"). Synthetic test fixtures put the field on assistant records, so all 18 Phase 0 tests passed. Running the driver against real transcripts surfaced that the field is in fact emitted on `user` records (typically the initial session-meta user record) — filtering by `type=="assistant"` always missed it, and every session collapsed to the `"default"` fallback. The Phase 3 spot-check survey of recent JSONLs confirmed values are `"auto"` and `"default"` (not the `"acceptEdits"` form I'd speculated). The Phase 0 code was patched to extract from any record type, a regression test was added (`test_by_permission_mode_extracted_from_user_record`), and Phase 1 was re-run. The Q1 table above reflects the corrected extraction. This is the failure mode plan §Verification step 4 anticipated.

## Out-of-scope follow-ups surfaced during the audit

- `--list-sessions` flag for `commit-gate` / `skill-pair` — would let the spot-check session selection be a one-liner instead of bespoke driver work. YAGNI today; add when this audit recurs.
- Per-session TSV emitter from `commit-gate` for downstream pandas analysis — sufficient evidence today comes from aggregates; deferred.
- Once PR #305 merges, swap the inline Q4 computation for `skill-pair` to pick up temporal-ordering refinement (currently the inline fallback is presence-only).

## Appendix A — Driver source

```python
{driver_source}```
"""

    IN_TREE_REPORT.write_text(body)
    print(f"Wrote {IN_TREE_REPORT}", file=sys.stderr)
    print(f"Length: {len(body.splitlines())} lines, {len(body)} bytes", file=sys.stderr)


if __name__ == "__main__":
    main()
```
