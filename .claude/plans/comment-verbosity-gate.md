# Comment-discipline enforcement: fix the instruction, then add an independent reviewer

## Context

**Goal:** stop the model applying CLAUDE.md's comment-verbosity rule to the one
spot a human points at and abandoning the rest of the diff — first by fixing a
malformed instruction that delivers the rule, then by adding a reviewer that
enumerates violations from a context the authoring session never touched.

The rule has failed three times, and the shape is identical each time — not
ignorance of the rule, but **partial application** of it:

1. **#544 / PR #546** — verbose `#` blocks in shell diffs. Fixed by adding
   advisory rule text.
2. **#564** — two hooks written with 40- and 21-line headers *after* that rule
   existed and was surfaced to the session twice independently. The session
   fixed the two most obvious bullets and stopped; the engineer caught the rest.
3. **A cross-repo incident, a different Claude account** — a private
   repository carrying its own equivalent rule. Two independent human
   reviewers flagged the same defect. Earlier in that review a reviewer
   flagged one 14-line block with a single word ("Verbose."), it was fixed, and
   every comparably-verbose block elsewhere in the diff was left untouched until
   two *new* reviewers caught them.

**Why now:** #564 asked for a judgment call on whether a third occurrence tips
the balance toward mechanical enforcement. It does. What "mechanical" should mean
here is narrower than a gate, for two independent reasons: an unrelated
instruction defect turned up in `code-writer` that is worth fixing on its own
merits (Part 1, warranted by ledger row 2 — *not* by these three incidents), and
the enforcement mechanism that best fits the observed failure is an independent
reviewer rather than a predicate (Part 2).

## Approach

Two changes, ordered cheapest-first per CLAUDE.md's default-suspect-over-powered-
primitives rule. **They ship together in one PR** (see Sequencing).

### Part 1 — a malformed instruction in `code-writer`

`code-writer` receives the comment rule like this (`code-writer.md:55`):

> "As you write, let CLAUDE.md §Engineering Judgment, §Working Style, and §Code
> Comments, Documentation, and Prose actively steer choices: understand the
> intent of existing code before changing it, ground every choice (timeouts,
> suppressions, discriminator literals, new dependencies), default-suspect
> over-powered primitives, respect scope discipline (Axis 1–4), **and write any
> comment as a one-line durable fact, not PR narration** — surface them at each
> decision point, not only at self-review."

One sentence, six distinct concerns, the comment rule fifth and trailing.
`code-writer.md:81` repeats the shape for the self-review step. The instruction
that says *"one line, not a paragraph"* is itself a six-fact run-on, which is
the defect it describes.

**What this is not.** This defect is *not* established as the cause of the three
incidents above. #564's own narrative is a main-session one — "I (Claude) wrote
two new hooks," with the rule arriving via `shell-script-conventions.md`
auto-loading on `.sh` edit and via the `claude-hook-review` checklist invoked
directly. Nothing in #564, #544, or the cross-repo incident shows a dispatched `code-writer`
subagent in the causal path. The run-on is a verified defect in the text; it is
not a verified cause of the documented failures, and the plan does not claim
otherwise.

Its warrant is separate and narrower: the engineer reports observing
`code-writer` handle the comment rule poorly in practice (ledger row 2). Fixing
a real instruction defect on the strength of that observation is worth ten lines
regardless of whether it also explains the three incidents.

### Part 2 — an independent reviewer

Every mechanism tried so far runs inside the *authoring* context, which is where
the satisficing happens. A reviewer agent runs in a fresh one. This repo already
states the principle, in `skill-fidelity-reviewer`'s description: *"never seeing
the session that produced the work — an uncontaminated observer is the entire
point."*

`comment-discipline-reviewer` reviews a diff against CLAUDE.md §Code Comments,
Documentation, and Prose and returns every violating site — comment verbosity,
prose at the wrong altitude, PR-defined terminology, "used to be X" framing, and
durable-doc content failing the survives-the-PR self-test.

Its value is **exhaustive enumeration by an observer that did not write the
code**. #564's failure was "fixed the two most obvious and stopped," a scan
failure inside one context; a fresh context performing the scan attacks it
directly.

### What this does NOT do, stated plainly

**A reviewer agent detects; it does not compel.** Findings return to the same
session that satisfices, which decides how many to fix.

One real mitigation exists and is worth naming rather than overselling:
`/code-review`'s disposition step requires every finding be tagged ADDRESS or
DEFER-with-criterion, and DEFER items are persisted into the PR body. So an
un-actioned finding becomes visible to a human reviewer rather than silently
dropped — which is how the cross-repo incident was eventually caught anyway. That is
partial, not equivalent to a gate, and the design should be adopted knowing it.

Two things make the residual acceptable. It is cheap and reversible, unlike a
blocking gate. And the escalation is already designed and measured: a blocking
`PreToolUse` gate with new-unit scoping denies 73% of commits at a 2.5%
tokenizer false-positive rate, with a lossless blank-separator remedy, and
denies the actual #564 commit while naming 15 violating units.

That escalation needs a durable home rather than a commit reference; see Out of
scope.

### Sequencing

Part 1 and Part 2 are independently revertable but **ship in one PR**, and Part 2
is not gated on Part 1 failing. The earlier framing of Part 1 as "a candidate fix
that may make Part 2 unnecessary" is dropped: nothing here tests Part 1's
efficacy in isolation, there is no observation window between the two, and no
criterion for "Part 1 didn't work" is stateable. Claiming a sequential trial
while shipping both at once would be fiction.

### Alternatives set aside

- **A blocking commit-time gate.** Set aside, not disproven — measured teeth and
  demonstrably fires on #564. Its predicate flagged 24% of denials as
  reviewer-unreasonable; the fix for that (a compound sentence-count-plus-size
  predicate, which the engineer identified) was never measured. Held as the
  documented escalation above.
- **Extracting §Code Comments into a `ROUTING.md`-style file plus a hook
  enforcing it was read.** Rejected on direct evidence: it would enforce a
  precondition already satisfied in the documented failure. From #564: *"Both
  rules were surfaced to me directly in-session, independently, before I finished
  writing the hooks... I used the rule to fix two of the most obviously spliced
  bullets and stopped there."* Reading was not the missing step. The mechanism
  exists (`require-routing-read.sh`) and would guarantee the one thing that
  already happened. CLAUDE.md is also 124 lines against a 200-line cap, so
  extraction has no length-pressure justification.
- **A new `/code-review` checklist tripwire.** Rejected: #546 already declined
  this on compounding-layers grounds, and it runs in the authoring session's
  context rather than a fresh one.
- **Adding the lane to an existing `staff-*` reviewer.** Rejected: each is
  domain-scoped while comment discipline is cross-cutting, so it would be
  checked inconsistently across N reviewers or dropped by all.

### Agent design

Naming: `comment-discipline-reviewer`. Its lane is all of §Code Comments,
Documentation, and Prose, which includes durable docs, so the name undersells the
scope slightly and the description carries the full lane. The alternative,
`prose-discipline-reviewer`, reads as writing-quality review, which is not the
job.

Registration is mechanically constrained, and `test_agent_roster.py` enforces
every item:

- `tools: Read, Grep, Glob, Write` — no `Bash`; the task is closed-form, matching
  `skill-fidelity-reviewer`. Membership in `_LIB_NO_GATE_RELEASE_AGENTS`
  additionally forbids `Skill` and `Task`, asserted by `TestNoGateReleaseRosterSync`.
- `model: sonnet`, per CLAUDE.md §Model Routing and the roster test's pin.
- The `### File-based output` block must be **byte-identical** to every other
  canary agent's, modulo the H1 — `TestFileBasedOutputBlockConsistency` compares
  literally. Copy it; do not paraphrase.
- Goes in `CANARY_AGENTS`, **not** `REVIEWER_AGENTS`. The latter is the eight
  stack specialists and is locked to the "N specialist personas" doc claims by
  `test_doc_counts.py`. `skill-fidelity-reviewer` is the precedent.
- Goes in `_LIB_REVIEW_ONLY_AGENTS`, which spreads into
  `_LIB_NO_GATE_RELEASE_AGENTS`. Consequences, both correct:
  `enforce-marker-script-shape.sh` denies it from writing or activating a review
  marker, so it cannot self-certify; and `deny-reviewer-tree-mutation.sh` denies
  it any tree mutation outside `/tmp` and `agent-reviews/`. No `.gitignore` work
  is needed — `agent-reviews/` is already ignored repo-wide at `.gitignore:27`,
  and that hook's allowance is per-path, not per-agent.

**Step 1.5 keeps its check.** An earlier revision proposed downgrading
`/code-review` Step 1.5's "Non-durable comment" tripwire to a citation of the new
agent. That is wrong and would weaken an existing check: Step 1.5 runs
unconditionally in the orchestrating session on every `/code-review`, while the
new agent is dispatched conditionally off the ripple-effect-triage table. Diffs
that do not trigger dispatch would lose the check entirely. Step 1.5 continues to
perform its check inline and may cite the agent for depth. Item 12 ("Stripped
WHY comments") is untouched — it governs comment *deletion*, a different concern.

### Assumption ledger

**Root problem:** the comment-verbosity rule reaches the model and is applied
only at the site a human names, leaving the rest of the diff unswept — observed
three times across two repos and two Claude accounts.

**Givens** (fixed, outside this design's reach):

- Findings returned by a subagent are acted on by the dispatching session; no
  agent can compel its dispatcher. Only a gate compels, and the decision this
  session is to try the lighter mechanism first — the gate itself sits in Out of
  scope with its reason, not here.

**Mechanisms:**

- *Splitting the `code-writer` run-ons* — `anchors: row 2`. The lightest primitive
  in the space: one file, no new component, no dispatch, no gate. Nothing lighter
  exists.
- *A dedicated fresh-context reviewer* — `anchors: root`. Lighter primitives
  rejected: (a) a `/code-review` checklist tripwire — same authoring context, and
  the compounding-layers pattern #546 declined; (b) folding the lane into an
  existing `staff-*` reviewer — domain-scoped reviewers check a cross-cutting
  rule inconsistently or not at all.

**Assumptions:**

| # | Assumption | Tag |
|---|---|---|
| 1 | `code-writer.md:55` and `:81` deliver the comment rule as a trailing clause in a six-concern run-on | `[verified: read this session]` — verifies the text only, not that it caused any documented incident |
| 2 | `code-writer` handles the comment rule poorly in practice | `[engineer-verified: this session]` — the warrant for Part 1; no incident in #544/#564/#728 is shown to run through a `code-writer` dispatch |
| 3 | CLAUDE.md is 124 lines against a 200-line cap | `[verified: wc -l]` |
| 4 | The rule was surfaced twice in-session in #564 and still only partially applied | `[verified: #564 body]` |
| 5 | A new reviewer belongs in `CANARY_AGENTS`, not `REVIEWER_AGENTS` | `[verified: test_agent_roster.py registries and header comment]` |
| 6 | The `### File-based output` block is byte-identical-enforced across canary agents | `[verified: TestFileBasedOutputBlockConsistency]` |
| 7 | `_LIB_REVIEW_ONLY_AGENTS` spreads into `_LIB_NO_GATE_RELEASE_AGENTS` | `[verified: _lib.sh:1212-1215]` |
| 8 | `agent-reviews/` is already gitignored, so no new ignore entry is needed | `[verified: .gitignore:27]` |
| 9 | Step 1.5 runs unconditionally; the new agent dispatches conditionally | `[verified: code-review/SKILL.md Step 1.5]` — the reason Step 1.5 keeps its check |
| 10 | Prefer the agent over the blocking gate; fix `code-writer` first | `[engineer-verified: this session]` |
| 11 | Read-enforcement via a ROUTING-style extraction is rejected | `[engineer-verified: this session]`, corroborated by row 4 |
| 12 | No numeric length threshold | `[engineer-verified: PR #546]` — moot here; this design has no mechanical predicate |
| 13 | Splitting the run-ons improves `code-writer`'s comment output | `[unverified]` — not mechanically testable, and no verification step targets it; see Verification |
| 14 | A fresh-context reviewer enumerates more completely than the authoring context | `[unverified]` — the design's core bet; Verification 4 is a one-time manual acceptance check, not a regression guard |

Rows 13 and 14 are the residual risk and neither is closable by a test. That is
the honest cost of this direction against the gate, which was measurable. It buys
cheapness, reversibility, and no false-positive tax.

## Critical files

**Modify:**

- `claude/.claude/agents/code-writer.md` — split the run-on at `:55` into one
  directive per concern, with the comment rule as its own bullet carrying a
  concrete test. Same for `:81`. Restructuring only; do not add content.
- `claude/.claude/hooks/_lib.sh` — add to `_LIB_REVIEW_ONLY_AGENTS`, **and**
  update the array's own header comment at `:1147-1150`, which enumerates three
  categories ("the eight staff-*/ciso-reviewer personas ... the
  skill-fidelity-reviewer ... plus the harness built-ins Explore and Plan") that
  the new member fits none of.
- `claude/.claude/hooks/deny-reviewer-tree-mutation.sh:3-4` — the same
  categorical enumeration appears in this hook's header and goes stale the same
  way. Structural sibling of the `_lib.sh` comment above; both must change.
- `docs/hooks.md:22` — hand-maintained exhaustive roster prose, not sourced from
  `_lib.sh`.
- `claude/.claude/hooks/tests/test_agent_roster.py` — add to `CANARY_AGENTS`.
- `claude/.claude/skills/code-review/SKILL.md` — register in the
  Ripple-effect-triage **Change type** table and the **Item ownership** table.
  Step 1.5 keeps its inline check and gains a citation only.
- `README.md` "### Agents" — a standalone paragraph outside the bulleted eight,
  per the `skill-fidelity-reviewer` precedent, noting it sits outside the
  specialist-roster count and why `Bash` is omitted.
- `docs/design-decisions.md §9` — record the spawn-from-scratch decision against
  the existing Extend/Split/Spawn tree.
- `CHANGELOG.md` — an entry; no **Migration:** note needed, since no gate is
  added and no contributor workflow changes.

**Create:**

- `claude/.claude/agents/comment-discipline-reviewer.md`.
- `claude/.claude/hooks/tests/fixtures/gh564-incident.diff` — the **full pre-fix
  files** from `2f14b4b` as a diff, not the two violating headers in isolation.
  Vendored so Verification 4 is reproducible without
  `git fetch origin refs/pull/563/head`, which is unreachable from a default
  clone. Full files rather than extracted headers because the capability under
  test is exhaustive enumeration across a real diff: a fixture containing only
  known-bad blocks tests recognition of pre-isolated text, which is close to
  tautological, while the real task is signal-versus-noise discrimination among
  surrounding code and compliant comments.

**Landing and rollback are atomic across the Part-2 file set.**
`TestNoGateReleaseRosterSync` asserts every `_LIB_NO_GATE_RELEASE_AGENTS` member
has a matching agent file, so the `_lib.sh` entry and the agent file must land in
one commit. Rollback has a sharper constraint in the other direction: reverting
the `_lib.sh` line alone while leaving the agent registered in `SKILL.md`'s
dispatch tables does not merely un-register it — `deny-reviewer-tree-mutation.sh`
fast-exits for any agent not in the array, so removing the entry **widens** what
a still-dispatched agent may write. Revert the whole Part-2 set together.

**Reuse rather than reimplement:** `skill-fidelity-reviewer.md`'s complete
structure — it is the other non-stack-specialist fresh-context reviewer and the
closest template. Copy its `### File-based output` block verbatim per row 6.

## Verification

1. **Roster tests** — `pytest claude/.claude/hooks/tests/test_agent_roster.py`.
   All assertions must pass: strict frontmatter parse, description ≤1000 chars,
   `model: sonnet`, `Write` in tools, no `Skill`/`Task`, byte-identical
   file-based-output block, no uncategorized agents, and roster sync.
2. **Doc-count tests** — `pytest claude/.claude/hooks/tests/test_doc_counts.py`,
   confirming the "eight specialist personas" claims did not break. This is the
   specific failure of registering in the wrong list.
3. **Pipeline self-review** — this change touches an agent file and a SKILL.md,
   so `/agent-review` (dispatcher-invoked) and `/skill-review` (hook-enforced by
   `require-skill-review.sh`) both run per
   `.claude/rules/review-pipeline-dispatch.md`. `/skill-review` blocks the commit
   until its marker is written.
4. **One-time manual acceptance check against the real incident** — dispatch
   `comment-discipline-reviewer` against the vendored `gh564-incident.diff`
   fixture and confirm it names every violation the engineer caught by hand, not
   merely the two the original session fixed. A reviewer that reproduces the
   original partial scan should not ship.
   **This is explicitly not a suite member and not a regression guard**: agent
   output is non-deterministic, and this repo has no behavior tests for any
   reviewer agent — `ciso-reviewer`, the seven `staff-*` personas, and
   `skill-fidelity-reviewer` all ship with structural and registration tests
   only. Matching that convention is deliberate; the cost is that row 14 is
   verified once, by hand, and never again.
5. **Self-application** — run the reviewer against this change's own diff. The
   `code-writer.md` edit and the new agent body must both pass the rule they
   carry, per `.claude/rules/skill-and-agent-self-review.md`.
6. **Suite and lint** — `pytest claude/.claude/`, `ruff check claude/.claude/`,
   `scripts/list-shell-files.sh | xargs -0 shellcheck`.

**Part 1 has no efficacy test, and none is available.** Prose restructuring is
not executed by anything testable, and a synthetic "dispatch `code-writer` at a
verbose-comment-tempting task and eyeball it" check would be a single
non-deterministic sample. `/agent-review` covers its structure only. Row 13 is
named rather than papered over.

The `.venv` lives at the main worktree root only and this branch's worktree is
four levels deep (`.claude/worktrees/GH-564/comment-verbosity-gate`), so the
documented `../../../.venv` path is wrong here — use an absolute path.

## Out of scope

- **The blocking commit-time gate.** Fully designed and measured at commit
  `7dcdfa0`. File a tracking issue carrying the measured numbers (73% new-unit
  denial, 2.5% tokenizer false-positive rate, 24% reviewer-unreasonable denials,
  and the compound sentence-plus-size predicate identified as the fix for that
  last one). A squash-merge makes `7dcdfa0` unreachable and eventually
  GC-eligible, so the commit alone is not a parking place.
- **Posting the #564 evidence comment.** Drafted from the brief and held for
  explicit engineer authorization — external communication is not covered by the
  go-ahead to build.
- **Extracting §Code Comments into a separate routing file.** Rejected above on
  #564's own evidence.
- **Changing the rule's content.** Both parts change how the existing rule is
  delivered and checked, not what it says.
- **The uncommitted `claude/.claude/hooks/_lib.sh` change in the main tree** —
  pre-existing, unrelated, not this branch's to resolve. Note the conflict risk
  it creates rather than only its ownership: this branch edits `_lib.sh` in two
  places (the roster array near `:1212` and its header comment at `:1147-1150`),
  so if that uncommitted change overlaps either region, this branch conflicts on
  rebase once it lands. Check the overlap before rebasing rather than at merge
  time.
