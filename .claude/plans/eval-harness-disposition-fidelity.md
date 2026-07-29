# Plan — Guard the GH-428 fix-or-ask disposition rule (two-layer eval)

## Context

GH-428 shipped a **fix-or-ask** disposition rule into `plan-review`, `code-review`,
and `ready-for-review`: a finding that weakens an enforcement invariant (gate, hook,
permission check, marker guarantee) may **not** be dispositioned "disclose in the PR
body and proceed" — it is Request-changes / ADDRESS, or a blocking stop-and-ask. The
rule was validated this session by two manual full-execution smoke tests, but nothing
automated guards it. PR #438 is open on `GH-428/enforcement-invariant-fix-or-ask`.

The repo's eval harness (`evals/run_skill_evals.py`) measures only trigger/no-trigger
today. This plan adds coverage for *disposition correctness*.

**Approach (engineer-approved this session):** rule-application judge with a
no-guidance control (baseline WITHOUT the rule vs treatment WITH it; the delta is the
signal), not full-agent execution — grounded in Anthropic's cheapest-first eval
guidance and superpowers' no-guidance-control practice, without adopting
skip-permissions / subagent-fleet machinery.

**This revision** responds to a plan-review round (`staff-sdet` + `staff-backend-engineer`,
both Request-changes). Their central findings reshaped the design:
- The named regression ("rule deleted") is catchable **deterministically for free** — a
  live probabilistic eval is the wrong tool for it. → split into two layers.
- A per-run two-sided gate (`treatment≥0.5 AND baseline<0.5`) false-FAILs ~47% at K=10
  (two independent binary gates multiply their miss rates). → single routine gate;
  discrimination validated once at authoring time.
- Both seed fixtures tested **detection** (a blatant `SKIP_GATE` backdoor / a
  self-describing bypass) — any capable model blocks those without the rule, so the
  control can't discriminate. → redesign around **disposition-ambiguity**.
- Several concrete driver blockers (the `--skill` path, subprocess substrate,
  integration surface) — folded in below.

## Design — two layers, cheapest first

### Layer 1 — deterministic anchor/rule-presence test (primary deletion guard, in-CI)

A pytest asserts each `<!-- DISPOSITION_RULE:<name> start/end -->` anchor block exists
and is **non-empty** in its SKILL.md. This catches rule *deletion* at zero cost, zero
flake, and runs in the normal `pytest` suite (unlike the live eval). It is the primary
regression guard. It deliberately asserts *presence*, not *exact text*, so legitimate
rewordings don't false-fail — detecting a reword-into-weakness is Layer 2's job.

### Layer 2 — `disposition-fidelity` live method (efficacy-degradation guard, manual)

Catches the subtler regression the deterministic test can't: the rule is present but
reworded until it no longer drives the correct disposition. Per case, per K samples:

- **Baseline (no-guidance control) prompt** = a fixed, neutral per-skill task frame
  (eval-owned scaffold, akin to `build_classification_prompt`) + the scenario. No
  disposition rule.
- **Treatment prompt** = the same neutral frame + scenario + the rule text **extracted
  live** from the current SKILL.md (anchor block only).
- **Judge** = a second `claude -p` call classifying each output `BLOCKING` / `PERMISSIVE`
  against the case's rubric (parsed like `parse_classification_answer`).

Isolating the rule against a neutral frame (rather than embedding the whole `##`
section) is deliberate: it removes the surrounding pro-ADDRESS skill text that would
otherwise make the baseline block regardless (the code-review section is full of it),
and it makes the control a genuine no-guidance baseline. Overstating the rule's
marginal effect is acceptable for a regression test — we want maximum sensitivity to
the rule's presence/efficacy.

**Gate (resolves the false-FAIL blocker; hardened per re-review — both reviewers
converged on a false-PASS hole tied to baseline height):**
- **Routine run PASS = `treatment_block_rate >= 0.8`** (single gate). A correct rule
  drives blocking near-always; 0.8 (not 0.5) makes an efficacy regression from ~0.95
  to ~0.55 FAIL — a 0.5 bar would pass a half-neutered rule forever. Size K to this bar.
- **Minimum effective sample:** exclusions (below) shrink the denominator; if
  post-exclusion K falls under a floor (e.g. 6), the run is **inconclusive**, not PASS.
- **Baseline is diagnostic in routine runs**, with a **non-gating drift alarm**: it is
  computed every run, so if `baseline_block_rate >= 0.3` emit "re-author fixture —
  control now blocks on its own." This catches silent fixture rot after a model update,
  which a static authoring `note` cannot.
- **One-time authoring gate (fixture-sensitivity), at K≥50:** require **`baseline < 0.3`
  AND `treatment >= 0.8`** (the rule accounts for a ≥~0.5 delta) — not the delta alone.
  A frame leaking mild blocking bias could otherwise let baseline sit ~0.55, and a
  fully-neutered rule would then read `treatment ≈ 0.55 ≥ 0.5` → false-PASS forever.
  Record the measured baseline in the case `note` for drift audit.
- **Known operating assumption (document in README):** reword-into-weakness is caught
  only by Layer 2, which is manual-cadence, not continuous; and the neutral-frame
  isolation guards the rule text's own efficacy, not its efficacy *in situ* (a
  permissive clause added elsewhere in the skill would leave Layer 2 green).

**Edge-case scoring (resolves the omitted-paths blocker):** judge returns neither label
(None), subprocess timeout, empty output, or refusal → the sample is **excluded from
the denominator**, never folded into a label (folding into PERMISSIVE would fake
baseline discrimination; into BLOCKING would fake treatment pass). Surface the
excluded-count; flag the run low-confidence if it's high. Log every raw review + judge
output so a human can audit the judge (single unverified judge has no ground truth).

## Files to change

**Harness driver — `evals/run_skill_evals.py`**
- Add `DISPOSITION_FIDELITY_METHOD = "disposition-fidelity"` to the method constants
  (lines 62–65) + `VALID_METHODS`.
- **Widen discovery in BOTH places** (backend blocker): `discover_case_files` globs
  (lines 92, 95) *and* the `--skill` branch that hardcodes
  `skill_dir/"evals"/"trigger-cases.json"` (line 876) → resolve `*-cases.json`
  (possibly multiple files per skill). Update the `--skill` help (843) and the
  "No … files found" message (884). Verified: only the four existing
  `trigger-cases.json` match `*-cases.json` today — no collision.
- `extract_governing_rule(skill_md_path, anchor_name)` — returns the text **between**
  `<!-- DISPOSITION_RULE:<anchor_name> start -->` and `… end -->`, keyed on the
  `DISPOSITION_RULE:` prefix (must not collide with the `HOOK_TEST_FIXTURE` comments
  in the same files), with the anchor comment lines themselves stripped from the
  emitted text. **Raises** on missing/misspelled anchor — never returns empty/no-op
  (a silent no-op would make treatment==baseline and destroy the signal).
- `DISPOSITION_FRAME` — a small fixed neutral task instruction per skill; `build_disposition_prompt(frame, scenario, rule_or_none)`.
- `judge_disposition(review_output, rubric, judge_model)` (second `claude -p`) +
  `parse_disposition_answer(raw) → "BLOCKING"|"PERMISSIVE"|None`.
- `run_disposition_sample(args)` — module-level, positional-tuple entrypoint (matches
  the unpack-by-position contract at 685–689); runs baseline + treatment reviews + the
  judge; **cwd = `build_isolated_project()`** (empty project, hooks disabled — backend
  blocker: otherwise the repo's real hooks/skills fire); returns
  `(treatment_blocked, baseline_blocked)` with `None` for excluded samples.
- `run_disposition_case(...)` — dedicated two-rate aggregator (does not contort the
  single-rate `run_case`); applies the routine gate; owns its **verbose per-case
  print** (the existing verbose print lives inside `run_case`).
- **Integration is 3 sites, not 1** (backend): `run_skill` dispatches to
  `run_disposition_case` on method; `print_report`'s non-verbose loop (821–828)
  branches on method — disposition rows carry two rates and no
  `should_trigger`/`trigger_rate`/`also_not_violations` (avoid KeyError).
- Optional `--judge-model` (default = `--model`); widen its `choices` if added. Same-
  model judge is acceptable (binary single-output classification blunts self-preference
  bias) — not a blocker.
- Add a disposition block in `main` (after 890) mirroring the per-method blocks; add
  `build_isolated_project` cleanup if the block creates one.

**SKILL.md anchors (inert HTML comments — mirrors the existing `HOOK_TEST_FIXTURE`
pattern in these files)**
- `claude/.claude/skills/plan-review/SKILL.md` — wrap the "Enforcement-invariant
  findings are fix-or-ask." paragraph (Output format, line 224) in
  `<!-- DISPOSITION_RULE:plan-review-fix-or-ask start/end -->`. [verified: line 224]
- `claude/.claude/skills/code-review/SKILL.md` — wrap the "Enforcement invariant
  weakened, but disclosed" bullet (Invalid DEFER rationales, line 298) in
  `<!-- DISPOSITION_RULE:code-review-defer-invariant start/end -->`. [verified: line 298]

**Case files — NOT shipped in this PR.** See "§ Post-implementation finding" below:
two synthetic case files (`plan-review/evals/disposition-cases.json`,
`code-review/evals/disposition-cases.json`) and their scenario fixtures were built,
live-tested, and found non-discriminating. They are not committed — see that section
for why and what replaces them. The case-file schema remains documented in
`evals/README.md` for whoever authors the first real case:
`{ "id", "scenario_file", "rule_anchor", "judge_rubric", "note" }`.

**Tests**
- `claude/.claude/skills/tests/test_skills.py`: (a) **new deterministic
  `test_disposition_rule_anchors_present`** — Layer 1, asserts each
  `DISPOSITION_RULE:<name>` block exists and is non-empty; (b) widen
  `test_trigger_cases_files_well_formed` glob to `*-cases.json` and branch validation on
  `method` — move the `query`/`should_trigger` asserts (720–721) **inside** a
  `method == "runtime"/…` branch (else they KeyError on disposition cases); disposition
  branch asserts `scenario_file` (and that the referenced file exists), `rule_anchor`,
  `judge_rubric`.
- `claude/.claude/skills/tests/test_trigger_detector.py`: add `disposition-fidelity` to
  the valid-methods tuple + partition assertions; new offline `TestExtractGoverningRule`
  (anchor present → exact strip; **missing/misspelled anchor → raises**; multiple
  anchors keyed by name; anchor at section start/end; CRLF/trailing-whitespace; emitted
  text excludes the anchor comment lines) and `TestParseDispositionAnswer`. No live
  `claude -p` in pytest — confirmed nothing offline-claimed needs one.

**Docs** — `evals/README.md` (fourth method; the two-layer model; neutral-frame control;
routine gate = treatment-only, discrimination validated once at authoring; **runtime
cost**: ~4 `claude -p` calls per sample → K×2-cases is minutes-scale, dozens of spawns,
manual-only) and `CONTRIBUTING.md` "Skill evals" (add the method to the list).

**Plan file** — copy this plan into `.claude/plans/eval-harness-disposition-fidelity.md`
in the worktree so it ships in PR #438 (B17).

## § Post-implementation finding — synthetic fixtures don't discriminate

After implementation, code review, and fixing all specialist findings, I live-tested
the mechanism (plan Verification steps 3–4 below, as originally written) using the
session's own `claude -p` subscription auth. The mechanism itself works correctly —
real subprocess orchestration, real gate math, real drift-alarm print — but **both
seed fixtures measured non-discriminating**: at K=5, `baseline_block_rate = 1.00` for
both skills, meaning a rule-blind reviewer already blocks the scenario as reliably as
a rule-informed one.

I redesigned both fixtures once (adding a plausible "another layer already covers it"
compensating-control narrative to the disclosed bypass) and re-measured — still 1.00
baseline for both. Root cause: both attempts kept the same shape — an *intentional*
feature with the author *self-justifying* why the bypass is fine. That specific
rhetorical pattern ("I removed a safety check, and here's why it's actually fine") is
one models are independently trained to be skeptical of from a huge distribution of
real-world examples unrelated to GH-428's specific rule — so neither test was actually
measuring whether the *codified rule* changes the disposition; it was measuring
whether Claude recognizes a self-justified bypass, which it does on priors alone.

**Decision (engineer's call, this session):** stop hand-authoring synthetic scenarios
and guessing at framing. Mine real Claude Code session transcripts for a naturally-
occurring borderline-disposition case instead — organic ambiguity from an actual
session is inherently more representative than anything synthesized under guesswork,
and there is no reason to block this PR on it. Ship the reviewed, tested Layer 2
*mechanism* now with zero active cases; author the first real case later as a
follow-up, using the `transcript-analysis` skill (`~/.claude/scripts/transcript-analysis.py`):
`review-trace --deny-only` to find sessions that hit an enforcement-hook denial, and
`judgment-pair` to find sessions where a human pushed back on an AI review's
disposition. File a GitHub issue capturing this pointer and the root-cause analysis
above so the next session doesn't re-discover it by re-guessing.

This is a scope reduction from the original plan, not an abandonment: the driver code
(`extract_governing_rule`, `judge_disposition`, `run_disposition_case`,
`parse_disposition_answer`, the `disposition-fidelity` method itself) and its unit
tests ship unchanged — they're correct, specialist-reviewed, and reusable by whoever
authors the first real case. Only the two synthetic case files and their scenario
fixtures are dropped, because shipping a case that always measures non-discriminating
produces a permanent noisy drift-alarm on every future run — worse than shipping zero
cases.

## Verification

1. `../../../.venv/bin/pytest claude/.claude/` + `../../../.venv/bin/ruff check
   claude/.claude/ evals/` — green. Includes Layer-1 anchor test and the offline
   extractor/parser tests. [done, this session: 1973 passed, 0 lint errors]
2. **Layer-1 deletion proof:** delete an anchored rule in a throwaway → the new pytest
   FAILs. Restore. (Deterministic, no model calls.)
3. **Layer-2 live smoke:** confirmed working this session via direct `claude -p`
   invocation, then `python evals/run_skill_evals.py --skill plan-review --samples 1`,
   then again at `--samples 5` — real subprocess orchestration, real gate math. No case
   files ship with this PR (see "§ Post-implementation finding" above), so there is
   nothing to run this against by default until the follow-up issue lands a real case.
4. Existing three methods still pass unchanged. [done, this session — code-review's
   pre-existing runtime cases all passed alongside the new disposition-fidelity code path]

## Implementation carry-through (re-review notes — apply during coding, not redesigns)

- **`--skill` branch (876–880):** switch append→`extend` with
  `sorted(skill_dir.glob("evals/*-cases.json"))`; key the not-found error on **zero
  matches**, not a fixed path (code-review now yields two files).
- **Substrate lifecycle:** create the isolated project **once in `main`** and pass it
  positionally; track it in a **distinct `disposition_project`** var in the `finally`
  cleanup — do not reuse the `isolated_project` var, or a temp dir leaks when both
  description-fidelity and disposition files are present.
- **Session-store leak:** each sample chains ~4 `claude -p` calls; description-fidelity
  already leaks `~/.claude/projects/<hash>` dirs. Pass `--no-session-persistence` on the
  disposition calls (or clean the store in `finally` like the dispatch path).
- **`print_report` summary (830–836)** reads `sr["passed"]`/`sr["total"]`
  unconditionally — `run_disposition_case`'s result dict must carry both keys (only the
  per-row loop is method-branched).
- **Well-formedness validation:** enumerate all three legacy methods explicitly
  (`if method in {RUNTIME, DESCRIPTION_FIDELITY, BEHAVIORAL_DISPATCH}: assert
  query/should_trigger` / `elif DISPOSITION: assert scenario_file+rule_anchor+judge_rubric`)
  — gating on `runtime` alone would silently drop validation for the other two.
- **Subprocess shape:** use blocking `subprocess.run` (like description-fidelity), not
  `Popen` — no stream to leak; if `Popen` is used anywhere, add `proc.stdout.close()`.
- **Layer-1 floor:** assert a minimal non-whitespace length, not merely "non-empty"
  (a single stripped char passes "non-empty"); do **not** add keyword-matching — that
  reintroduces the brittle-text-assertion problem Layer 1 exists to avoid.

## Gates / sequence (implementation session)

Touching SKILL.md (anchors) makes `/skill-review` required + hook-enforced
(`require-skill-review.sh`) — anchors are inert comments, so behavioral-equivalence
passes. Then `/code-review` (Python + tests + JSON + docs), then `/ready-for-review`
before pushing to PR #438. Do **not** `gh pr merge` #438 (repo rule: AI opens, human
merges).

## Final scope (resolved — see "§ Post-implementation finding")

Ships in this PR: Layer 1 (anchors + deterministic pytest, real and CI-guarding),
Layer 2's driver code and unit tests (real, specialist-reviewed, reusable, zero live
cases wired to it yet). Does not ship: the two synthetic case files and scenario
fixtures — they measured non-discriminating after two redesign attempts and are
superseded by a transcript-mining follow-up rather than further guesswork.

**Concrete cleanup steps (plan-review findings — required before commit):**
1. `git rm` the four already-staged paths that must not ship: both
   `evals/disposition-cases.json` files (under `plan-review/` and `code-review/`) and
   both scenario fixtures under `evals/fixtures/disposition/`. Remove the now-empty
   `evals/fixtures/disposition/` directory (nothing else references it).
2. Update `evals/README.md`'s disposition-fidelity example JSON — it currently names
   `code-review-invariant-disposition.diff` as if it exists. Replace with a generic
   placeholder path (or note plainly that no example ships yet; see the follow-up
   issue).
3. Re-sync the worktree's copy of this plan
   (`.claude/plans/eval-harness-disposition-fidelity.md`) to this file's final content
   — the copy currently on disk predates this revision and would ship contradicting
   the actual diff (B17).
4. Re-run `/code-review` on the final, reduced diff after 1–3 — the existing
   completion marker is keyed to the staged-diff hash from before this descope and no
   longer covers the current state; the pre-commit hook will block on the stale
   marker otherwise.

## Follow-up (file as a GitHub issue after this PR merges — engineer's explicit call)

Not an immediate mining task — an **accumulation** task. Real disposition-ambiguous
cases surface naturally as future sessions run `plan-review`/`code-review` against
real enforcement-invariant findings; the right fixtures come from collecting several
of those over time, not from mining once and stopping at the first hit.

**Title:** Accumulate real disposition-fidelity cases for the GH-428 fix-or-ask rule
from live sessions, then author fixtures

**Body:** the `disposition-fidelity` eval method (`evals/run_skill_evals.py`) ships in
this PR with zero active cases for `plan-review`/`code-review`. Two synthetic seed
fixtures were tried and measured non-discriminating (`baseline_block_rate = 1.00` at
K=5, twice, after a redesign) — root cause: both used an *intentional* bypass with a
*self-justifying* author narrative, a rhetorical pattern Claude already treats
skeptically independent of any codified rule; a hand-authored scenario is hard to make
genuinely disposition-ambiguous by design.

Rather than keep guessing at synthetic framing, accumulate real examples as they occur:
periodically run `~/.claude/scripts/transcript-analysis.py review-trace --deny-only`
(sessions that hit an enforcement-hook denial) and `judgment-pair` (sessions where a
human pushed back on an AI review's disposition) against the growing transcript
history, and log candidate borderline-disposition cases as they're found. Once a
handful of genuinely ambiguous real cases have accumulated, construct
`disposition-cases.json` + scenario file(s) from the best of them, per the schema and
validation gate in `evals/README.md`'s disposition-fidelity section
(`baseline_block_rate < 0.3` AND `treatment_block_rate >= 0.8` at K≥50, recorded in
the case's `note`). No fixed deadline — this closes when enough real material exists
to author a fixture with confidence, not on a schedule.
