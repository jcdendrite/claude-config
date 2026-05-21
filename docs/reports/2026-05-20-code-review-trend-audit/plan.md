# /code-review trend audit

## Context

**Goal:** Produce `/tmp/code-review-trend-audit-findings.md` (plus a committed snapshot at `.claude/plans/code-review-trend-audit-findings.md`) that either confirms `/code-review` usage is healthy across recent workflow shifts (auto-mode adoption, `/brief` introduction, `/plan-review` gate loosening) or quantifies a per-segment drop that the stable aggregate rate hides.

The aggregate `/code-review` per-1000-assistant-turn rate has been flat (8.5 → 8.2 → 7.0 → 7.0 → 7.3 → 7.5 across 2026-W16…W21). Three workflow shifts in the last five days could conceal a per-segment drop:

- **Auto-mode adoption:** `permissionMode: "auto"` jumped from ~0% to ~80% of sessions on 2026-05-19.
- **`/brief` introduction:** 5 invocations on 2026-05-20…21.
- **`/plan-review` gate loosening:** 2026-05-17; `/plan-review` per-1000-turn rate fell from ~4.1 to ~2.2.

A stable aggregate over a fast-mixing denominator is a known confounder. The audit answers four questions: (1) does the rate diverge by `permissionMode` segment? (2) has the per-session commit-to-`/code-review` ratio drifted (i.e., is the `require-code-review.sh` gate leaking or being bypassed)? (3) has the fraction of sessions-that-committed-but-skipped-`/code-review` grown? (4) does `/ready-for-review`'s internal chain to `/code-review` account for any apparent shift, or does it run parallel rather than substituting?

The user expects pairing-rate / gate-compliance analyses to recur (next likely candidates: `code-writer` → `code-review`, future review-chain skills). Per user direction on plan-review, this audit lands the analysis as a reusable **`commit-gate` subcommand in `transcript-analysis.py`** rather than a one-shot script, so the next gate-compliance question is a one-liner. (`/ready-for-review` chain is covered by the sibling audit's `skill-pair` subcommand — see Coordination below.) **User surface:** the auditor (the user) and any future contributor reading the report or using the new subcommand; `transcript-analysis.py` is stowed to `~/.claude/scripts/` for every clone-and-stow user, so the new subcommand is platform-wide, not personal.

The PR for this branch ships three things: this plan, the new `commit-gate` subcommand + tests, and the committed findings snapshot (which embeds the audit driver script verbatim in an appendix).

## Coordination with sibling audit

A concurrent session is landing a `skill-pair <leader> <follower>` subcommand in the same `transcript-analysis.py` file on branch `plan-it-chain-audit`. Coordination rules:

- **Different subcommand name** — mine is `commit-gate`, theirs is `skill-pair`. No collision.
- **Symbol-relative references only** — this plan cites `cmd_subagent_mix`, `iter_sessions`, `_branch_filter`, `_projects_glob`, `_parse_ts`, and `REVIEW_SKILLS` by name, never by line number, so a sibling-first or self-first merge doesn't invalidate either plan.
- **Additive-only edits** — both subcommands add a new `cmd_*` function and a new `subparsers.add_parser(...)` block at the end of the existing parsers in `main()`. Both tests get a new `TestCommitGate` / `TestSkillPair` class appended to the test file. A standard git auto-merge handles two appends.
- **`permissionMode` extraction convention** — sibling asserts the field appears once per session (on the session-meta record, not on every assistant turn) and missing → `"default"`. I adopt the same convention for `commit-gate`'s `--by-permission-mode` flag, and test it against a real session in Verification.
- **Binning hardcoded, no `--bin` flag** — sibling explicitly skips a single-choice `--bin` flag as YAGNI (sibling plan, Phase 0 CLI shape). `commit-gate` matches: ISO-week is hardcoded internally; add a `--bin` flag in a follow-up only when a second binning mode is genuinely needed.
- **`--exclude-projects` flag** — sibling adds it to `skill-pair`; I add the same flag to `commit-gate` with the same semantics (project-dir basename glob, skipped before walking). Same default (empty) and same documented form (`--exclude-projects '-tmp-claude-eval-*'`) at the audit invocation. If sibling later extracts to a shared helper, both subcommands can be refactored at that time.
- **Q4 depends on `skill-pair`** — phase 2 of this audit calls `transcript-analysis.py skill-pair ready-for-review code-review`. If sibling's PR hasn't merged by my execution time, my driver does a one-off equivalent computation inline (per-session presence check, no temporal-ordering refinement) and the report calls out the dependency; once sibling merges, the driver swaps to the real subcommand on the rebased branch.

## Approach

Five phases. Phase 0 lands the reusable subcommand; phase 1 uses it for Q1/Q2/Q3; phase 2 uses sibling's `skill-pair` for Q4; phases 3–4 spot-check and write the report.

### Phase 0 — Land `commit-gate <skill>` subcommand + tests

CLI shape:

```
transcript-analysis.py commit-gate <skill> [--by-permission-mode] [--projects GLOB] [--exclude-projects GLOB] [--branches B1,B2,...]
```

Argparse `help=` string (matches the file's existing one-line convention at lines 496–540): `"Per-commit gate-compliance: did <skill> precede each commit in the same session? Optionally split by permissionMode."`

- Positional: `skill` — exact-match against the `skill:` field on `Skill` `tool_use` blocks; matches the sibling's "no plugin-prefix normalization" rule. The match is byte-equal — `code-review` does NOT match `skill-management:skill-review`.
- `--by-permission-mode` — adds a per-mode split column. Without it, rows are bucketed by `bin` only.
- ISO-week binning is hardcoded internally; no `--bin` flag (matches sibling's YAGNI decision).
- `--projects` / `--exclude-projects` / `--branches` — same semantics as sibling's `skill-pair`. Defaults: `*` projects, empty exclude, no branch filter.

Output columns (default, no `--by-permission-mode`):

```
bin | sessions | turns | skill-invocations | skill-rate-per-1k-turns | commits | commits-with-prior-skill | commits-without-prior-skill | commits-no-verify
```

With `--by-permission-mode`, an additional `mode` column appears between `bin` and `sessions`, and the row count multiplies by the number of distinct modes per bin.

**Per-session aggregation logic** (model on `cmd_subagent_mix`'s per-session bucket pattern):

For each session in `iter_sessions(...)`:

1. **Sidechain exclusion** — only main-thread records (`isSidechain != true`) count; matches sibling and existing `cmd_subagent_mix` precedent.
2. **`permissionMode` extraction** — first assistant record in the session that carries a non-empty `permissionMode` field. Missing → `"default"`. (Sibling claims the field is on the session-meta record; my tests verify this against a real session in Verification, and the derivation gets switched to turn-span-weighted if the assumption fails.)
3. **Bin assignment** — ISO week derived from the session's `first_turn_ts` via `_parse_ts(...)` + `datetime.fromtimestamp(...).isocalendar()`. Sessions that span two weeks are bucketed by `first_turn_ts` (assigned to a single bin) — matches sibling's session-per-bin convention; per-week splitting was considered but adds complexity for limited analytical benefit at this scale.
4. **Skill-invocation count** — `Skill` `tool_use` blocks where `input.skill == <skill>`. The full skill name match is literal — `code-review` matches `code-review` only, not `skill-management:skill-review`.
5. **Commit count** — `Bash` `tool_use` blocks where the command matches `(^|&&?|;|\|\|?)\s*git\s+commit(\s|$)` (the same regex `require-code-review.sh:38` uses, which already excludes `git commit-tree`). `--amend` is included; `--no-verify` is included in the `commits` total AND counted separately into `commits-no-verify`.
6. **`commits-with-prior-skill`** — for each commit in the session, was there a `Skill` `tool_use` of `<skill>` **between the previous commit (or session start) and this commit**? Per-commit attribution (not per-session presence). A session with two commits and one early `/code-review` (before commit 1) counts the first commit as gated and the second as un-gated — the `/code-review` "consumes" the first commit and doesn't carry forward. This matches the hook's runtime semantics: the marker is invalidated by re-staging, so a single `/code-review` doesn't gate multiple downstream commits without re-running.

   **Same-record tiebreaker.** Tool_use blocks share their parent assistant record's timestamp. When a single assistant record emits Skill and Bash `git commit` blocks (common in auto-mode when the model chains tool calls in one turn), order by `content[]` array index within the record. A Skill block earlier in the array than a `git commit` block in the same record counts as "before" the commit.
7. **`commits-without-prior-skill`** — commits in the session that had no preceding `<skill>` invocation in the gating window defined in step 6.

Aggregate per bin (and per mode if `--by-permission-mode`):

- `sessions` — distinct sessions in the bin.
- `turns` — total assistant turns in the bin (main thread only).
- `skill-invocations` — total `<skill>` invocations.
- `skill-rate-per-1k-turns` — `1000 * skill-invocations / turns`, formatted to one decimal.
- `commits` / `commits-with-prior-skill` / `commits-without-prior-skill` / `commits-no-verify` — summed.

Tests live in `claude/.claude/scripts/tests/test_transcript_analysis.py`, using the file's existing synthetic-JSONL fixture helpers (`_asst`, `_skill_use`, `_write_jsonl`). Coverage:

- Empty JSONL → `No data found.`, exit 0 (matches `cmd_subagent_mix` line 408 convention).
- Single session, no commits, one `/code-review` → 1 session, 0 commits, 1 invocation, rate computed against turn count.
- Single session, one commit AFTER one `/code-review` → `commits-with-prior-skill = 1`, `commits-without-prior-skill = 0`.
- Single session, one commit BEFORE one `/code-review` → `commits-with-prior-skill = 0`, `commits-without-prior-skill = 1` (ordering matters).
- Single session, two commits with one `/code-review` between them → `commits-with-prior-skill = 1`, `commits-without-prior-skill = 1`.
- Single session, `git commit --no-verify` → counted in both `commits` and `commits-no-verify`; not in `commits-with-prior-skill` regardless of preceding `<skill>` calls (the bypass is the salient signal — we want to surface it as a distinct column, not absorb it into the "gated" bucket).
- Single session, `git commit --amend` → counted in `commits` (each amend is a new review surface).
- Single session, `git commit-tree …` → NOT counted (regex anchor `(\s|$)` excludes it; explicit test).
- Sidechain `/code-review` `tool_use` → NOT counted toward `skill-invocations` or `commits-with-prior-skill`.
- **Skill-name exact-match contract**: a session with one `_skill_use(... "skill-management:skill-review")` invoked via `commit-gate skill-review` → NOT counted (literal byte-equal match, no plugin-prefix normalization).
- **Same-record ordering**: a session with one assistant record containing `[_skill_use("code-review"), _bash_use("git commit -m 'x'")]` → commit is `commits-with-prior-skill`. Same record with reversed order `[_bash_use, _skill_use]` → commit is `commits-without-prior-skill`.
- `--by-permission-mode` split: two sessions, one with `permissionMode: "auto"` on session-meta record, one without `permissionMode` (→ `"default"`) → two rows per bin.
- `--by-permission-mode` + sparse `permissionMode` (only on a mid-session switch record, not session-meta) → currently picks the first carrying record; this case tests our extraction matches the documented contract.
- ISO-week boundary: commit on Sunday 23:59:59 UTC vs Monday 00:00:01 UTC.
- `--projects` glob filter.
- `--exclude-projects` glob: a project dir matching the exclude pattern is omitted even when also matched by `--projects`.
- `--branches` filter reuses `_branch_filter`.

### Phase 1 — Q1/Q2/Q3 from `commit-gate`

Run from the audit driver script:

```
transcript-analysis.py commit-gate code-review --by-permission-mode --exclude-projects '-tmp-claude-eval-*'
```

This emits per (week, mode) rows. From this single invocation the driver derives:

- **Q1** — `skill-rate-per-1k-turns` column, split by `mode`, for W19/W20/W21. Flag any segment >30% off the week's aggregate (computed by re-running the subcommand without `--by-permission-mode` and comparing).
- **Q2** — `commits-with-prior-skill / commits` ratio per week. Compare W19 baseline to W21 post-shift.
- **Q3** — `commits-without-prior-skill / commits` ratio per week, plus `commits-no-verify / commits` per week. Both are gate-violation signals.

### Phase 2 — Q4 from sibling's `skill-pair`

```
transcript-analysis.py skill-pair ready-for-review code-review --exclude-projects '-tmp-claude-eval-*'
```

Sibling's output columns: `bin | leader-sessions | follower-main | follower-sidechain-only | pair-rate-main`. For Q4, `pair-rate-main` answers: "of sessions that invoked `/ready-for-review`, what fraction also invoked `/code-review` in the main thread?" If `pair-rate-main` is high (≥80%) and stable across W19–W21, the chain is firing as designed and RFR growth is contributing chained `/code-review` to the aggregate (substitution-ish for any declining standalone use). If `pair-rate-main` is low or dropping, the chain is broken (RFR's step-3 prose instruction is being skipped); aggregate-flat in that case must come from elsewhere.

**Dependency note:** if sibling's PR hasn't merged at execution time, the driver script does a one-off inline computation (per-session presence: `ready-for-review` in main-thread Skill tool_uses ∧ `code-review` in main-thread Skill tool_uses) and the findings call out the temporary fallback. Post-rebase the driver swaps to the real subcommand.

### Phase 3 — Spot-check sessions (bespoke driver)

Candidate-session selection is bespoke driver work — `commit-gate` emits aggregates only, so the driver re-walks `~/.claude/projects/*/*.jsonl` with the same `iter_sessions` pattern the subcommand uses, filters for the four criteria below, and picks the first match per criterion per week.

For each of W19/W20/W21, pick four sessions for manual JSONL verification:

- One with `commits-with-prior-skill > 0` (confirmed gated).
- One with `commits-without-prior-skill > 0` (confirmed bypass — investigate why; sometimes legitimate, e.g., docs-only commits).
- One with `permissionMode: "auto"` (verify the `--by-permission-mode` derivation matches the session's actual mode).
- One with `commits-no-verify > 0` (confirm `--no-verify` is literally present in the Bash command).

If `permissionMode` turns out to be sparse (emitted only on switches, not on the session-meta record), the subcommand's "first carrying record" rule is wrong — switch to a turn-span-weighted derivation between switches, update tests, re-run phase 1.

### Phase 4 — Write the report

Driver script writes directly to `docs/reports/2026-05-20-code-review-trend-audit/findings.md` (the committed snapshot). No `/tmp/` working copy — the file is committed in the same PR, so the in-tree path is canonical from first write.

**Alternatives considered.**

- **Keep the bespoke one-shot script (original plan).** Set aside per user direction: pairing-rate / gate-compliance questions recur, and an audit landed as a reusable subcommand pays off the next time the question gets asked.
- **Combine `commit-gate` and sibling's `skill-pair` into one subcommand.** Set aside: the two questions have different shapes (commit-vs-skill ordering vs leader/follower-skill pairing). Forcing them into one CLI surface would bury one behind flags the other doesn't need.
- **Add a `--list-sessions` flag to `commit-gate` for the phase-3 spot-checks.** Set aside as YAGNI: spot-checks are bespoke driver work, not a recurring subcommand need. Sibling reached the same conclusion for their `--list-sessions` question.
- **Emit a per-session TSV from `commit-gate` for downstream pandas analysis.** Set aside: aggregates per (bin, mode) are sufficient for the audit's tables. A future row-emitter subcommand is a follow-up if needed.

## Critical files

**Read (in-tree, reference only):**

- `claude/.claude/scripts/transcript-analysis.py` — model `cmd_commit_gate` on the per-session bucket pattern in `cmd_subagent_mix`. Re-use `iter_sessions`, `_fam`, `_parse_ts`, `_branch_filter`, `_projects_glob`. The sidechain-exclusion rule is the same `isSidechain != true` check `cmd_subagent_mix` uses. The `REVIEW_SKILLS` tuple is unrelated to my subcommand (mine takes any skill name as a positional argument).
- `claude/.claude/scripts/tests/test_transcript_analysis.py` — re-use `_asst`, `_skill_use`, `_write_jsonl` fixture helpers. Append a new `TestCommitGate` class at the end (so sibling's `TestSkillPair` doesn't conflict).
- `claude/.claude/hooks/require-code-review.sh` — confirm the commit-regex (`require-code-review.sh:38`) my subcommand replicates, and the marker-bypass-on-chain check; cite both in the findings methodology.
- `claude/.claude/hooks/require-ready-for-review.sh` — confirm `--no-verify` does not bypass `require-code-review.sh` (push-gate `--no-verify` semantics are unrelated; the commit gate does not honor `--no-verify`). Cite for the `commits-no-verify` column's interpretation in the findings.
- `claude/.claude/skills/code-review/SKILL.md` — for any rule citations in the findings.
- `claude/.claude/skills/ready-for-review/SKILL.md` — confirm step-3 chain language is a Skill-call prose instruction, not silent invocation. Document the answer in findings.

**Read (transcripts, scripted):**

- `~/.claude/projects/*/*.jsonl` — excluding `-tmp-claude-eval-*` subdirs at invocation time, via `--exclude-projects`.
- Per-record fields used: `type` (gate to `"assistant"`), `isSidechain`, `gitBranch`, `timestamp`, `permissionMode`, `sessionId`, `message.content[].name == "Skill"`, `message.content[].input.skill`, `message.content[].name == "Bash"`, `message.content[].input.command`.

**Edit (in-tree, committed):**

- `claude/.claude/scripts/transcript-analysis.py` — add `cmd_commit_gate` function (model on `cmd_subagent_mix`); append a `subparsers.add_parser("commit-gate", ...)` block at the END of the existing parser registrations in `main()` so sibling's `skill-pair` insertion doesn't conflict.
- `claude/.claude/scripts/tests/test_transcript_analysis.py` — append `class TestCommitGate(...)` with the test cases listed in Phase 0.

**Write (transient):**

- `/tmp/code-review-trend-audit-driver.py` — the bespoke driver script that calls `commit-gate`, calls `skill-pair`, does spot-check loads, and writes the report. Embedded verbatim as a fenced-code-block appendix in the committed findings snapshot so the analysis is reproducible from the report alone.

**Write (committed):**

- `docs/reports/2026-05-20-code-review-trend-audit/findings.md` — durable snapshot of the findings, committed alongside the plan. Written at the end of the audit so plan + subcommand + tests + findings ship together.
- `docs/reports/2026-05-20-code-review-trend-audit/plan.md` — this plan (already in place). `.claude/plans/` is gitignored in this repo, so audit artifacts that ship with a PR live under `docs/reports/<YYYY-MM-DD>-<slug>/` per public-repo convention (Argo CD `docs/proposals/`, Chia post-mortems, Kubernetes design docs); see PR description for the path-decision rationale.

## Verification

This is both a code change (subcommand + tests) and an analytical deliverable. Verification covers both surfaces.

1. **Phase 0 — subcommand tests + lint pass.**
   - `pytest claude/.claude/scripts/tests/test_transcript_analysis.py` is green — confirms the new `commit-gate` subcommand doesn't regress existing tests AND every new `TestCommitGate` case listed in Phase 0 passes.
   - `ruff check claude/.claude/scripts/transcript-analysis.py claude/.claude/scripts/tests/test_transcript_analysis.py` is clean.
2. **Phase 0 — subcommand smoke test.** Run `claude/.claude/scripts/transcript-analysis.py commit-gate code-review` against real `~/.claude/projects/` (no `--exclude-projects` filter). Output is a table with the 9 declared columns; rows sorted by ISO week; not blank.
3. **Phase 1 — aggregate reproduction gate.** Sum `commits-with-prior-skill + commits-without-prior-skill` per week and divide `skill-invocations` by `turns / 1000`. The resulting weekly per-1k-turn rates must match the brief's reference table (8.5 → 8.2 → 7.0 → 7.0 → 7.3 → 7.5) within ±0.5 per 1000 turns. Outside that band → subcommand bug (most likely sidechain inclusion, wrong denominator, or wrong skill name match); halt and fix before continuing.
4. **Phase 3 — spot-check verification.** Confirm the four sessions' `permission_mode_dominant`, `commits-with-prior-skill`, `commits-without-prior-skill`, and `commits-no-verify` derivations against manual greps of the source JSONLs. If `permissionMode` is sparse (only on switches), update the subcommand's extraction to turn-span-weighted, update tests, re-run phases 0–1.
5. **Phase 4 — findings file committed.** `docs/reports/2026-05-20-code-review-trend-audit/findings.md` exists, is tracked, and is non-empty. (No `/tmp/` working copy under the revised path convention, so the original durable-copy-equivalence diff step is dropped.)
6. **Phase 4 — recommendation grounding.** The recommendation paragraph cites the specific metric and the specific week that triggered it (e.g., "W21 `auto` segment shows skill-rate-per-1k-turns of 4.1 vs week aggregate of 7.5 — 45% below"). A recommendation that doesn't cite the table is unsupported and must be revised.

## Out of scope

Per the brief's §7 (with one user-reversed item, matching the sibling audit's pattern):

- Redesign `/code-review`, `/ready-for-review`, or any related hook.
- Modify `require-code-review.sh` or `require-ready-for-review.sh` (their behavior is a finding, not a target).
- Investigate `/plan-review` or `/plan-it` trends (sibling brief covers those; treat fully independent).
- ~~Bundle a refactor of `transcript-analysis.py`~~ **Reversed by user on plan-review:** the `commit-gate` subcommand is now in scope and lands in this PR. Other generalizations that emerge during the audit (per-session TSV emitter, `--list-sessions` flag, alternate binning) are still out — name them as follow-ups in the report.
- Extend the audit to `/handoff`, `/brief`, `/skill-review`, or other skills.

**Branch / handoff note.** This branch now opens a PR (the subcommand + tests + plan + findings snapshot ship together). The PR description should call out the sibling-audit coordination (no merge-order requirement; both subcommands are additive) and the committed findings snapshot.
