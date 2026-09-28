# Stop re-dispatching comment-discipline-reviewer every /code-review round

## Context

Cut token cost and review churn by making `comment-discipline-reviewer` stop
running on every `/code-review` round during a feature branch's iterative
fix loop, deferring its exhaustive comment/prose audit to a single pass —
mirroring how `skill-fidelity-reviewer` runs exactly once, from
`ready-for-review`, rather than once per round.

The immediate trigger is the engineer's observation that agent-authored
comments tend toward verbose, thinking-out-loud prose.
`comment-discipline-reviewer`'s current every-round dispatch produces a
fresh batch of findings each round against that prose, spending tokens and
review cycles on the job. That job's actual deliverable — readable comments
in the merged state — only needs to be correct once, at the end.

This follows an already-merged, engineer-approved design: PR #771, closing
GH-763. PR #771 was itself a follow-up to incident GH-752, where
`comment-discipline-reviewer` findings round-tripped for 4 review rounds
and round 4 literally reverted round 2's own fix. That design made every
reviewer's *exhaustive-enumeration* duty diff-scoped, and gave
`comment-discipline-reviewer` one extra narrowing on top: it skips its
per-round spawn entirely when that round's diff carries no comment/prose
content. It did not eliminate the every-round dispatch when the round *does*
touch prose — which is most fix rounds, since a fix round's diff is
disproportionately comments and tests. That gap is the churn the engineer
is now seeing.

## Approach

Keep `comment-discipline-reviewer` in `/code-review`'s Change-type table, but change the row's extra narrowing from *match-narrowing* to *deferral*. Step 0.6's staged-diff responsibility boundary applies when the diff under review is exactly `git diff --cached` and `HEAD` is not the default branch. Whenever it applies, that row does not spawn at all, whatever prose the diff carries — its one exhaustive pass per branch lands in `ready-for-review` step 3's cumulative review, which already runs unnarrowed and needs no edit to do so.

**Why this over a literal mirror of `skill-fidelity-reviewer`.** Removing the row from the Change-type table and dispatching it from `ready-for-review` step 4 instead is the heavier mechanism and loses coverage the surgical change keeps:

- Coverage. `/code-review` runs in four contexts the boundary never covers, already enumerated in Step 0.6: the cumulative pass, a presentation-path review, an ad-hoc review, and a commit to the default branch. Table removal drops the row in all four; the deferral drops it in exactly one, the commit-gate round that a `gh pr create` gate guarantees will be followed by an unnarrowed pass.
- Line budget. `ready-for-review/SKILL.md` is exactly 200 lines against its 200-line cap, with zero headroom. `check-skill-length.sh` denies a staged file that is both over-limit and longer than the committed version, so a new dispatch step there is hook-blocked without trimming something else first. `code-review/SKILL.md` is ~483 against its 500-line override, and this change is net-zero lines.
- Charter fit. `skill-fidelity-reviewer` is dispatched once because its question ("was this skill executed over the branch's history") is structurally whole-branch. `comment-discipline-reviewer`'s question is stateless per diff. The engineer's analogy is about dispatch *frequency*, and frequency is all the deferral changes — so the frequency result is obtained without borrowing a dispatch topology that the charter does not justify.
- Mechanism weight. The deferral reuses a precondition that is already computed, already anchored, and already test-enforced. It adds no new state, no new ref computation, and no new cross-skill coupling.

**Why not leave the narrowing PR #771 introduced as-is.** The residual churn is structurally self-feeding rather than legitimate signal, repeating the GH-752 shape within a single branch's fix loop:

1. A fix round's diff is disproportionately comments.
2. The reviewer flags the freshly-written prose.
3. The fix rewrites that prose.
4. The next round audits the rewrite — the same shape where round 4 reverted round 2's own fix.

One pass over the final state cannot oscillate against itself. `/code-review` Step 1.5's inline "Non-durable comment" tripwire still fires every round unconditionally, so the worst class (comments narrating PR history) is still caught at authoring time; what defers is the exhaustive enumeration.

**Two lighter primitives considered and set aside** (over-powered-primitive check on M1):

- Narrowing by prose *location* — skip when the round's prose sits in files already reviewed on this branch. Rejected: needs a new per-branch record of reviewed paths, new persisted state for a smaller win.
- Handing the spawn a delta-of-the-delta diff instead of the full staged diff. Rejected: needs a new diff computation and still pays a full reviewer dispatch every round, which is the cost the engineer named.

**What the design deliberately does not touch.** `ready-for-review/SKILL.md` gets no edit. Its step 3 pass is already unnarrowed, and `test_ready_for_review_step3_never_produces_a_staged_diff` already enforces structurally that step 3 can never satisfy the narrowing precondition's staged-diff half — so the landing site is mechanically guaranteed before this change lands. Putting the deferral rule in `code-review`'s Step 0.6 keeps one home for the spawn decision.

### Assumption ledger

**Root:** `/code-review` dispatches `comment-discipline-reviewer` on every mid-iteration feature-branch round whose diff touches prose, so each fix round re-audits prose the previous round's fix just wrote, and the same final state is reviewed N times to produce one merged result.

**Givens:**

- **G1 — `require-ready-for-review.sh`'s bypass set is fixed.** Its documented bypasses (default-branch pushes, pushes to a branch with no open PR, and the `gh pr ready`/`gh pr create` plain-regex detection gaps tracked by GH-897) stay as they are; that hook is a separate gate whose push-path blast radius reaches every stow consumer, so changing it is outside a dispatch-cadence plan. `[verified: claude/.claude/hooks/require-ready-for-review.sh:35-78]`
- **G2 — `ready-for-review/SKILL.md` has no line headroom.** It is exactly 200 lines (`wc -l` and `awk 'END{print NR}'` agree, trailing newline present) against `check-skill-length.sh`'s plain 200-line default cap, so any net growth would push it over-limit and longer than the committed version — the exact condition that denies the commit — leaving no mechanism that adds lines there available. `[verified: wc -l and awk 'END{print NR}' against claude-skills/skills/ready-for-review/SKILL.md, this session; claude/.claude/hooks/check-skill-length.sh:100-107 `limit_for` override list, which names code-review/plan-review/ROUTING.md/pr-description only]`

**Rows:**

1. `gh pr create` is gated on an RFR marker unconditionally — the "branch has no open PR" bypass is explicitly not applied to it — so a branch cannot open a PR without a cumulative unnarrowed pass having run at the current HEAD. `[verified: require-ready-for-review.sh:313-328 and its header note at :46-47]` `anchors: root`
2. `ready-for-review` step 3's `/code-review` runs against a cumulative diff-file path, never a staged diff, so Step 0.6's narrowing precondition fails there and every Change-type row spawns unnarrowed — including the deferred one. `[verified: ready-for-review/SKILL.md:73-84 and its `SCOPE_RULE:ready-for-review-cumulative-unnarrowed` region; structurally enforced by test_skills.py's `test_ready_for_review_step3_never_produces_a_staged_diff`]` `anchors: root, M1`
3. `/code-review` Step 0.1's marker short-circuit cannot silently skip that cumulative pass on a clean tree: `marker.sh`'s `_hash_staged_diff` returns a distinguished empty-diff code and prints no hash, so an empty staged diff matches no marker. `[verified: claude/.claude/scripts/marker.sh:158-192]` `anchors: row2`
4. `ready-for-review` step 3's cache-hit branch does not open a hole: the `cumulative-review` marker is content-addressed and written only from a clean pass of that step's own cumulative `/code-review`, so a `live` reading means the identical content already got the unnarrowed pass. `[verified: ready-for-review/SKILL.md:69-71, 84]` `anchors: row2`
5. `skill-fidelity-reviewer` will not flag the deferred round as `[SILENT-SKIP]`: it accepts a `reviewer-spawn` event for a matched row anywhere on the branch, and RFR step 3 runs before step 4 in the same gate, producing that spawn. `[verified: claude/.claude/agents/skill-fidelity-reviewer.md:96-124]` `anchors: M3`
6. That reviewer re-derives matched rows from the Change-type table read fresh, applying "the table's own qualifiers" — it is not instructed to read Step 0.6 — so the deferral needs a pointer in the row cell itself, not only in Step 0.6. `[verified: skill-fidelity-reviewer.md:99-107]` `anchors: M3`
7. The engineer judges the per-round prose dispatch to be waste rather than signal, and delegated the mechanism choice to this design rather than settling it. `[engineer-verified]` `anchors: root`
8. `code-review/SKILL.md`'s Change-type row, its Step 0.6 region, and `docs/design-decisions/reviewer-responsibility-bounded-to-diff.md` are the only sites in the repo that state this reviewer's dispatch condition; every other hit is a roster membership, a charter description, or a preserved plan record. `[verified: repo-wide grep for `comment-discipline`, with `claude/.claude/hooks/_lib.sh:2923-2935`, `docs/hooks.md:46,54`, `README.md:243`, `docs/design-decisions/reviewer-persona-roster-operations.md:21`, and `docs/cost-levers-considered.md:154-189` each read and confirmed cadence-free]` `anchors: M5, out-of-scope`
9. The `SCOPE_EXEMPT_ROW` anchor's contract survives unchanged: both tests on it read only the row's left-column shorthand, which this design does not touch. `[verified: test_skills.py:4048-4076]` `anchors: M1`
10. Editing the body of the "Prior reviewer covered this." bullet is safe against the cross-file label test, which compares only the bolded labels between `code-review/SKILL.md` and `plan-review/ROUTING.md`. `[verified: test_skills.py:3640-3654; the same call was made and cited in `.claude/plans/scope-code-review-delta-rounds.md:89`]` `anchors: M4`
11. The `Spawn decisions:` output format needs no change: it already requires `skipped: <row> — <reason>` for every matched-but-skipped row, and the `scope:` tag already records whether narrowing applied. `[verified: code-review/SKILL.md:227-234]` `anchors: M1`
12. Checklist item 12a's ownership row already reads `comment-discipline-reviewer (when spawned)`, which accommodates a deferred round without an edit. `[verified: code-review/SKILL.md:427]` `anchors: out-of-scope`
13. `require-architect-consult.sh` counts distinct `(HEAD sha, staged-diff sha256)` states at reviewer-spawn time, so a round whose only matched row is the deferred one now records no state and the round-3 consult trigger advances one round later on prose-only churn. Judged acceptable and arguably correct — a round with no reviewer spawn is a round with no specialist review to escalate from, and the multi-round churn the gate targets is the churn this plan removes at source. `[verified: docs/hooks.md:54]` `anchors: out-of-scope`
14. Deferral trades feedback latency for one batched pass: prose findings arrive at the RFR gate rather than per round, and RFR step 3 already routes them through `code-writer` → fix commit → staged-diff gate → back to step 2. `[verified: ready-for-review/SKILL.md:84]` `anchors: root`
15. A branch that never reaches `gh pr create` — merged locally to the default branch, or opened through one of G1's detection gaps — loses the exhaustive prose pass entirely, where today it would get partial per-round coverage. Accepted rather than defended: such a path already bypasses the whole review pipeline, and this repo's workflow is PR-based with self-merge blocked. `[unverified]` — the local-merge path was reasoned about, not exercised. `anchors: G1, out-of-scope`

**Mechanisms:**

- **M1 — Replace Step 0.6's match-narrowing sentence with a deferral clause wrapped in a new nested anchor.** Reuses the already-computed boundary precondition as the skip condition, so no new state, ref, or computation enters the design. `anchors: root`
- **M2 — Delete the now-inapplicable clause "`comment-discipline-reviewer` gets a diff artifact instead of ranges; its Change-type table row points to the resolution note below the table for why and how." from the boundary paragraph.** Under M1 that row never spawns while the boundary applies, so a sentence describing what it receives there describes nothing; the row's own pointer and the resolution note remain the live path for unnarrowed contexts. `anchors: M1`
- **M3 — Add a one-sentence deferral pointer to the Change-type row's right-hand cell.** The table is the surface `skill-fidelity-reviewer` re-derives matched rows from, and a rule living only in Step 0.6 is invisible to it (row 6); a pointer rather than a restatement keeps Step 0.6 the single home. `anchors: row5, row6`
- **M4 — Rewrite the last sentence of the "Prior reviewer covered this." invalid-skip bullet.** That sentence currently asserts the boundary never decides whether a row is spawned, which M1 makes false; leaving it would let a session read the deferral as a forbidden skip rationale. `anchors: M1`
- **M5 — Record the decision: a new `docs/design-decisions/` file, plus a partial-supersession clause in `reviewer-responsibility-bounded-to-diff.md`.** That file's line 13 claims the comment/prose row is match-narrowed on prose presence, which M1 overturns, and the directory rule requires an overturned decision to be edited in place to say so rather than left to contradict silently. `anchors: M1, row8`
- **M6 — Extend `test_skills.py`'s existing anchor machinery to cover the new region, and add one test tying the row cell to Step 0.6.** The repo's convention is that a new convention ships with its enforcing test in the same PR, and the pinned-clause pattern is exactly how the sibling `code-review-causal-reach` guarantee is held. `anchors: M1, M3`

### Prescribed text

**`code-review/SKILL.md` Step 0.6, replacing the final paragraph of the `SCOPE_RULE:code-review-staged-diff-only` region (currently line 50):**

```
The row <!-- SCOPE_EXEMPT_ROW start -->Adds or modifies a comment or durable-doc prose beyond a hygiene tweak (code comments, docstrings, `REFERENCES.md`, doc files, README sections, skill/agent bodies)<!-- SCOPE_EXEMPT_ROW end --> is deferred rather than narrowed: <!-- SCOPE_RULE:code-review-comment-row-deferred start -->whenever this boundary applies, that row does not spawn, whatever prose the boundary carries. Its one exhaustive pass per branch runs in the cumulative unnarrowed review at `ready-for-review/SKILL.md` § "3. Code review (halt on findings)". Still enumerate the row in this step and report it on the `Spawn decisions:` line as `skipped: <row> — deferred to ready-for-review's cumulative pass`, per the Output format section's own convention. Every context where this boundary does not apply spawns the row as it spawns any other.<!-- SCOPE_RULE:code-review-comment-row-deferred end -->
```

**Change-type row, appended as the last sentence of the right-hand cell:** `Step 0.6 defers this row out of staged-diff commit-gate rounds.`

**"Prior reviewer covered this." bullet, replacing its final sentence:** `The staged-diff responsibility boundary above changes what a spawned row is responsible for flagging. It stops only the one row Step 0.6 names from spawning at all, and no other row may be skipped on that basis.`

**`reviewer-responsibility-bounded-to-diff.md`, inserted as a new line 5 (blank line, then the clause, immediately under the provenance line):**

```
**Superseded in part by [deferring the comment/prose row to the cumulative pass](comment-discipline-reviewer-deferred-to-cumulative-pass.md) (2026-09-15):** the comment/prose row's additional match-narrowing described below — spawning it whenever the boundary carries prose — is replaced by deferring that row out of staged-diff rounds entirely. Every other clause here stands, including the uniform responsibility boundary and its default-branch and cumulative-pass guards.
```

"Superseded in part by" rather than the rule file's bare `**Superseded by**` example: one clause is overturned and the rest stands, which is the shape `schedulewakeup-misapplied-documented.md:5` already records with its own what-stands sentence.

## Critical files

One phase, one `code-writer` dispatch — do not split. The pinned test constant must reproduce the Step 0.6 clause character-for-character, so the skill edit and the test edit cannot be specified independently.

- **`claude-skills/skills/code-review/SKILL.md`** — four edits, all in place, net zero lines (current ~483 against the 500-line override in `check-skill-length.sh`'s `limit_for`): M1 at line 50, M2 inside line 36, M3 in the Change-type row at line ~280, M4 in the invalid-skip bullet at line ~289. Reuse: the existing `SCOPE_RULE:` anchor namespace and the existing `Spawn decisions:` `skipped: <row> — <reason>` format — add no new output field. Before deleting M2's clause, grep `claude-skills/skills/tests/test_skills.py` for `diff artifact` and `instead of ranges` to confirm no pin depends on that wording.
- **`claude-skills/skills/tests/test_skills.py`** — four changes, all extending existing structures rather than new machinery: add `("code-review", "SCOPE_RULE:code-review-comment-row-deferred")` to `_EXPECTED_SCOPE_ANCHORS` (~3663); add `("code-review", "SCOPE_RULE:code-review-comment-row-deferred", "SCOPE_RULE:code-review-staged-diff-only")` to `test_nested_anchor_fully_contained_within_outer_rule`'s parametrize list (~3800); add the new region's exact clause text to `_PINNED_SCOPE_CLAUSES` (~3841); add one test asserting the Change-type row whose left column equals `SCOPE_EXEMPT_ROW`'s shorthand carries the literal `Step 0.6 defers this row`, locating the row with `_change_type_table_rows` + `_extract_scope_anchor_region` exactly as `test_code_review_staged_diff_instruction_lives_in_its_own_note_only` (~4210) does. That last test asserts on skill-file text by design, which is this module's whole subject — it is not the source-scanning anti-pattern `/code-review` item 9g targets.
- **`docs/design-decisions/comment-discipline-reviewer-deferred-to-cumulative-pass.md`** (new) — H1, blank line, then `*2026-09-15.*` as line 3 (an ISO date and no `Formerly` clause, the shape `test_design_decision_files.py` requires of a post-split decision), the decision, and a `## Sources` section citing `code-review/SKILL.md` Step 0.6, `ready-for-review/SKILL.md` step 3, `require-ready-for-review.sh`'s `gh pr create` gating, and `.claude/plans/comment-discipline-once.md`. Record what a reader cannot re-derive: that the deferral's safety rests on `gh pr create` being unconditionally RFR-gated, that the four unnarrowed contexts keep full coverage, and that the row stays in the Change-type table so those contexts still reach it.
- **`docs/design-decisions/reviewer-responsibility-bounded-to-diff.md`** — insert the partial-supersession clause only. Do not rewrite line 13; the record of what was decided in August stays as written.
- **`CHANGELOG.md`** — one bullet under `## [Unreleased]` → `### Changed`. Consumer-visible: `claude-skills/` stows to `~/.claude/skills/`, so every stow consumer's `/code-review` changes behavior on `git pull` with no re-install. State the new condition, name `ready-for-review` step 3 as where the pass now runs, and name the four contexts that keep unnarrowed per-invocation coverage.
- **No edit:** `claude-skills/skills/ready-for-review/SKILL.md` (G2, and row 2 — already correct), `claude/.claude/agents/comment-discipline-reviewer.md` (charter unchanged; this is a dispatcher-side decision, the same call `.claude/plans/scope-code-review-delta-rounds.md`'s Critical files item 6 made), `claude/.claude/agents/skill-fidelity-reviewer.md` (M3 closes the gap from the table side), `README.md:243`, `docs/design-decisions/reviewer-persona-roster-operations.md`, `docs/hooks.md`, `claude/.claude/hooks/_lib.sh` (row 8).

## Verification

Run from the repo root (or the branch's linked worktree):

1. `.venv/bin/python3 claude/.claude/scripts/select-tests.py` — the repo's scoped selector, per its CLAUDE.md's "Agents: run `select-tests.py`, not the full suite." The diff touches `claude-skills/skills/**` and `docs/design-decisions/**`, so this must select `claude-skills/skills/tests/test_skills.py` and `claude/.claude/hooks/tests/test_design_decision_files.py` at minimum; if either is absent from the printed scope, that is a `select-tests.py` rule-table bug to report, not a licence to widen the run by hand.
2. `.venv/bin/ruff check claude/.claude/ claude-skills/` — the test-file edit is Python.
3. `/skill-review` on the `code-review/SKILL.md` diff — hook-enforced by `require-skill-review.sh`, which blocks `git commit` until its marker is written (`.claude/rules/review-pipeline-dispatch.md`). No agent file, rule file, hook, or plugin-directory file is touched, so `/agent-review`, `ai-instruction-and-memory-files`, `claude-hook-review`, and `plugin-semver` are not implicated.
4. `/code-review` on the staged diff before commit. Expect the comment/prose row to be enumerated and reported as `skipped: ... — deferred` on the `Spawn decisions:` line — this change governs its own commit-gate round, and the exhaustive pass over this diff's own prose comes from `/ready-for-review` step 3. That is the intended behavior, not a gap to work around.
5. Acceptance check, by observation rather than a test: confirm the Step 0.6 clause and the `_PINNED_SCOPE_CLAUSES` entry are character-identical after whitespace collapsing — `test_pinned_scope_clause_matches_live_text` is the mechanical form of this and will fail loudly if they drift.
6. `check-skill-length.sh` clears on commit: `code-review/SKILL.md` must stay at or under 500 lines, and the design is net zero.

## Out of scope

- **Editing `ready-for-review/SKILL.md`.** It has no line headroom (G2), its step 3 pass is already unnarrowed (row 2), and the spawn decision's single home is `code-review`'s Step 0.6.
- **Changing `comment-discipline-reviewer`'s charter, tools, or effort level.** Nothing about what it reviews changes — only when it is dispatched.
- **Closing `require-ready-for-review.sh`'s bypass and detection gaps** (GH-897's flag-before-subcommand form, a full-path `gh` invocation, the local-merge-to-default-branch path). Pre-existing, tracked, and not widened by a design that relies on the gate's normal path; row 15 records the consequence this design inherits from them.
- **Changing `require-architect-consult.sh`'s round-state counting** to account for rounds that now spawn no reviewer (row 13). The interaction is small and arguably correct.
- **The residual PR #771 named — two `/code-review` invocations against the same staged state with no commit between them.** Still open, untouched, and not this plan's problem; the deferral reduces its blast radius incidentally by removing the one reviewer whose re-run was most expensive.
- **A general deferral rule for other Change-type rows.** The comment/prose row is the only one that is closed-form with no cross-file reach, which is why PR #771 singled it out for extra narrowing and why it alone is deferrable without losing causal-reach coverage. Do not generalize this to `staff-*` or `ciso-reviewer` rows.
