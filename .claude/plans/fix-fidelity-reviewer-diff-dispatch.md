# skill-fidelity-reviewer: cost/benefit finding, and one small fix

## Context

The question: is `skill-fidelity-reviewer` worth its cost, on the hypothesis
that it is expensive "because it combs through transcripts"?

**The hypothesis is wrong on both halves. Keep the agent; change nothing about
its cadence.** The investigation is the deliverable. The only implementation
work below is a three-line prose fix it exposed.

### Finding 1 — it does not comb transcripts

The transcript scan is a Python script (`transcript-analysis.py
skill-invocation`) run by the **parent** in Bash. The agent has `tools: Read,
Grep, Glob, Write` — no `Bash` — and its body states it is handed the invocation
list precisely so it never reads transcripts. The scan parses 260 MB across 382
JSONL files (~22 s wall clock), but that is CPU, not tokens: the output reaching
the model averages **1,442 chars (~400 tokens)**, max 3,634.

### Finding 2 — it is the cheapest reviewer on the roster

94 invocations, 2026-07-24 → 08-04. Two independent measurement passes (one
keyed on Task `tool_use` ids, one on `subagents/*.meta.json` `agentType`, both
deduping usage rows by `message.id`) agree to within $0.01/run:

| Agent | runs | total $ | $/run | Mtok/run |
|---|---|---|---|---|
| code-writer | 87 | 672.07 | 7.725 | 19.282 |
| general-purpose | 179 | 420.74 | 2.351 | 1.480 |
| staff-sdet | 287 | 326.33 | 1.137 | 1.153 |
| ciso-reviewer | 237 | 218.74 | 0.923 | 0.786 |
| staff-platform-engineer | 213 | 211.09 | 0.991 | 0.780 |
| Explore | 103 | 198.39 | 1.926 | 0.540 |
| staff-backend-engineer | 107 | 120.15 | 1.123 | 1.065 |
| **skill-fidelity-reviewer** | **94** | **65.63** | **0.698** | **0.639** |

Lowest cost per run of any reviewer; $65.63 lifetime is **6.2%** of
reviewer-agent spend, against a repo baseline of $2,376 over 60 days
(`transcript-analysis.py cost --this-repo --since 60d`). List-price estimates,
not billed amounts.

### Finding 3 — skill-skipping is not rare, and it catches it

Of 94 invocations: 63 clean (67%), 23 with ≥1 finding (24%), 7 long-form
"approve with concerns" (some containing `[SILENT-SKIP]`, which would put the
rate at 32%), 1 refusal. Effective cost per invocation that surfaced something:
**$2.26**. Representative true positives — not restatements of the diff:

- `git-feature-branch-sync`: force-push done, required post-force-push PR
  communication never posted; recurs 3× in the window, once under a *fabricated
  skill citation*.
- `handoff`: claimed `.claude/plans/` was gitignored citing `.gitignore:51`;
  false — the diff commits a `.claude/plans/*.md` file.
- `verify-sources`: two-independent-source requirement silently unmet (both
  quotes from the same repo), twice.
- `claude-hook-review`: own test-coverage checklist applied to 2 of 4 nontrivial
  hook fixes, silently skipped on the other 2.
- `resolve-merge-conflicts` silently dropped a whole conflict cluster.

**Answer: it is worth it.** Cheapest reviewer on the roster, and "agent not
following skills" is its most productive finding class.

## Approach

One change, deliberately minimal.

### Fix — stop printing the diff command next to "pass this"

`ready-for-review/SKILL.md:107-108` tells the parent to pass "the **text** of
step 3's cumulative diff (`git diff $(git merge-base origin/$BASE_REF
HEAD)...HEAD` — text, not the range...)". Printing the command inline, adjacent
to the "pass this" instruction, invites pasting the *command*. On 2026-08-03 a
dispatch did exactly that; the agent correctly refused per its input contract
(`skill-fidelity-reviewer.md:21`) and the dispatch produced nothing — **1 of 94
invocations (~1%)**.

Change: step 3 already computes this exact diff in a fenced `bash` block
(`SKILL.md:72-73`). Have step 4's prose refer to that already-captured output
rather than re-printing the command, so no command string sits adjacent to the
"pass this" instruction.

*Scope note:* a second degraded run (2026-07-27) was told to run `gh api` with
no `Bash` tool. `grep` confirms **no `gh api` instruction exists anywhere in
step 4** — that came from parent-improvised dispatch prose, not from the skill
body. It is therefore not fixable by editing step 4 and is out of scope here.

*Alternative set aside:* give the agent `Bash` so it can run `git diff` itself.
Rejected — `docs/design-decisions.md:75` records the `Bash` omission as
deliberate task-shape, and adding it would let the agent read
`~/.claude/projects/**`, dissolving the uncontaminated-observer property that is
its entire rationale.

*Test deliberately not added.* A static cross-artifact contract test was
specified and rejected as disproportionate. To be non-vacuous it needs: a stable
HTML anchor comment delimiting step 4's dispatch-payload span (heading regex
would silently empty the span on renumbering), a span extractor in
`tests/helpers.py`, a named command-token frozenset, a tools-derived
applicability guard, and both-polarity assertions. That is a new test-harness
layer guarding a three-line prose edit with one observed failure. The existing
`TestSkillFidelityReviewerUndecidableDismissal` (10 tests) and
`test_agent_roster.py` already cover the agent's own contract and stay green
untouched.

## Critical files

- `claude/.claude/skills/ready-for-review/SKILL.md:107-108` — the only edit.
  Reuse step 3's existing diff computation at `:72-73`; do not add a second one.
- Not modified: `claude/.claude/agents/skill-fidelity-reviewer.md`. Its input
  contract is already correct; the dispatch is what violated it.

## Verification

Run from the implementation worktree — the contributor `.venv` exists only at
the main worktree root, so use `../../../.venv/bin/…`:

- `../../../.venv/bin/pytest claude/.claude/` — full suite; nothing should
  change, this edit touches no pinned string.
- `../../../.venv/bin/ruff check claude/.claude/` and
  `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck`.
- `/skill-review` on the SKILL.md change before staging, per
  `.claude/rules/skill-and-agent-self-review.md`.
- Behavioral: one real `/ready-for-review` dispatch. Positive criterion — the
  returned findings file cites a file path or hunk header that appears only in
  the diff text, proving text (not a range expression) arrived. "No refusal" is
  not sufficient; at a ~1% base rate a single clean run is indistinguishable
  from the unfixed state.
- Rollback: revert the single SKILL.md hunk. No other artifact changes.

**Plan-file placement:** `~/.claude/plans/` symlinks to
`claude/.claude/plans/`, which is gitignored (`.gitignore:18`). Move this plan
to the repo-root `.claude/plans/<slug>.md` on the implementation branch so it
ships in the same PR (B17).

## Out of scope — follow-up issues to file

Surfaced by this investigation; neither belongs in this change.

1. **`Explore` does not run on Haiku.** `claude/.claude/CLAUDE.md:77` asserts
   "`Explore` is pinned to Haiku" and instructs *not* to pass `model` to it.
   Measured: **103 runs, $198.39, Haiku in 1 run / 67 of 3,585 requests
   (1.9%)** — 1,372 requests on `claude-opus-5`, 1,107 on `claude-opus-4-8`.
   `Explore` is a built-in with no local agent file, so like `general-purpose`
   it inherits the parent model; the CLAUDE.md carve-out exempting it from the
   explicit-`model` rule is what causes this. Two possible outcomes — a real
   routing fix, or correcting a false CLAUDE.md line with no spend change — and
   the issue should say so rather than promising $198 of savings.
2. **`code-writer` at $672 / 19.3 Mtok per run** — the largest single cost
   center measured, 10× this agent's lifetime total.

Considered and rejected, recorded so they are not re-proposed:

- **A pre-dispatch "decidability" filter.** A 12-branch sample found 4 branches
  whose entire invocation list is out-of-scope or nominally undecidable. But the
  measured findings include real catches against `handoff` and `pr-description`
  — skills the body tells the agent to dismiss — so the filter would suppress
  true positives.
- **Narrowing the undecidable-dismissal rule to claim-level.** Rejected for
  three independent reasons: (a) the evidence was wrong —
  `git-feature-branch-sync` is *not* on the dismissal enumeration
  (`skill-fidelity-reviewer.md:51` lists only `branch-management`,
  `subagent-delegation`, `handoff`, `brief`, `pr-description`, `respond-pr`);
  (b) the measurement window opens 2026-07-24 but the dismissal rule merged
  2026-07-29 (`953e7fa`, #513), so cited catches may predate the rule they are
  offered as evidence against; (c) GH-514/#533 records **three structurally
  different wording passes on that same paragraph all failing identically**, and
  the body already reads "The test is not whether an artifact exists but whether
  you can judge that the skill was carried out" — already claim-level. A fourth
  wording pass on a paragraph proven unresponsive to wording is the
  compounding-layers tell, not a fix.
