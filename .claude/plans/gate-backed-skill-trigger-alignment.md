# Align gate-backed skill descriptions with what their hooks actually enforce

## Context

**Goal:** make every `DO NOT TRIGGER` clause on a hook-gated review skill name only a condition
that skill's gate hook can actually observe, so a session that follows the description is never
denied by the hook.

A session reported that `require-code-review.sh` blocked a commit whose staged diff was a single
plan file, even though `/code-review`'s own description routes plan-only diffs to `/plan-review`
(which had already run clean). It judged the disagreement real but out of scope and flagged it.

It is real, and it is not confined to that one clause. A skill's `description` feeds
*description-based auto-triggering*: the model reads it to decide whether to fire the skill. But
this repo also promotes several review skills to **gate** status — a `require-*.sh` hook blocks a
tool call until the skill writes a marker. At that point a `DO NOT TRIGGER` clause stops being a
routing hint and starts being a promise about whether an operation will be allowed. A hook can only
decide on predicates it can compute: tool name, command shape, staged pathspec, marker hash. Every
clause that turns on something else — a judgment about triviality, a conversational waiver, a
routing hand-off the hook does not implement — is a promise the system cannot keep, and the failure
mode is always the same: the session declines to run the review, then gets denied.

**Outcome:** four descriptions rewritten to be gate-truthful, and the invariant stated once in
`docs/hooks.md` so the next skill that gets a gate does not re-acquire the defect.

## Approach

Fix the descriptions; leave every hook's behavior unchanged. The gate is the authority, and the
descriptions are what disagree with it.

Three findings settle the direction:

1. **`code-review`'s description contradicts its own body.** `claude/.claude/skills/code-review/SKILL.md:3`
   says a SKILL.md / agent / plan / CLAUDE.md-only diff routes *away* from code-review. Lines
   169–171 of the same file (§ Domain: Claude Code config) dispatch `skill-review`, `agent-review`,
   and `ai-instruction-and-memory-files` as sub-reviews *inside* code-review. The arrow direction in
   the description is backwards.
2. **Every other surface already describes specialists as additive.**
   `.claude/rules/review-pipeline-dispatch.md:13` — "`/skill-review` is **also** required";
   `plugins/skill-management/hooks/require-skill-review.sh:74-77` is path-scoped so it fires
   *alongside* the code-review gate; `docs/skills.md:50` calls `agent-review` "dispatched by
   `/code-review`, never description-auto-triggered". Only `code-review`'s description claims
   substitution.
3. **Shipped history already violates the clause.** Commits `425b670`, `ca5ada2`, and `ddf1b0b`
   each touched exactly one `SKILL.md` and therefore needed *both* a code-review marker and a
   skill-review marker, because `require-code-review.sh` has no file-type predicate anywhere in it.

Two rewrite shapes already exist in the repo and are the models to follow: `skill-review`'s
description states plainly that it is dispatched rather than auto-triggered
(`plugins/skill-management/skills/skill-review/SKILL.md:3-6`), and `agent-review` is set to
`name-only` in `skillOverrides`, which drops its description from the listing entirely.

**Alternative considered and set aside:** teaching `require-code-review.sh` to exempt a commit whose
entire staged diff is `.claude/plans/**` when a matching plan-review marker exists. Rejected on
three grounds. It creates an exemption class to maintain; it does nothing for the agent-file and
CLAUDE.md arms of the same clause, which have *no* commit gate behind them at all, so exempting
them would mean ungated commits; and `_lib_active_plan_hash` uses `--diff-filter=d`
(`claude/.claude/hooks/_lib.sh:260-261`), so a staged plan *deletion* is absent from the plan hash
and would slip through such an exemption unreviewed. Fixing the prose costs less and leaves no hole.

### Assumption ledger

**Root problem:** hook-gated review skills advertise `DO NOT TRIGGER` exemptions their gate hooks
do not implement, so following the description produces a denial.

| # | Row | Tag |
|---|---|---|
| 1 | `require-code-review.sh` gates every `git commit` with a non-empty staged diff and has no file-type or content-severity predicate. | `[verified: require-code-review.sh:49,58,70-72,84-93]` |
| 2 | `code-review`'s "cosmetic-only" and "only one specialized file type staged" clauses are both unhonored; only its marker clause is honored. | `[verified: alignment audit, this session]` |
| 3 | `plan-review`'s "trivial one-liner" and "user has explicitly said to skip review" clauses are both unhonored — the hook arms on any active plan file and cannot observe conversational intent. | `[verified: require-plan-review.sh:91-112]` |
| 4 | `ready-for-review`'s "during active iteration" is unhonored (any non-dry-run push to a branch with an open PR is gated); its other two clauses are honored. | `[verified: require-ready-for-review.sh:87-117,143-145,163-179]` |
| 5 | `respond-pr`'s three clauses are all unhonored — the hook matches on command shape alone, with no branch, PR-existence, or read-state check. | `[verified: require-respond-pr.sh:210-215,220-234]` |
| 6 | `ai-instruction-and-memory-files` is already fully aligned; every clause names a path outside the hook's predicate. No change needed. | `[verified: require-memory-skill.sh:77-88]` |
| 7 | `skill-review` and `agent-review` are already resolved, by description rewrite and by `skillOverrides: name-only` respectively. No change needed. | `[verified: skill-review/SKILL.md:3-6; settings.json skillOverrides]` |
| 8 | The corpus listing budget **passes with substantial headroom** — well under 8000 chars once the skillOverrides-filtered set is used. An earlier draft of this plan claimed 9148/8000 "already over budget"; that was a measurement artifact of running `--corpus` over a raw `git ls-files` list, which the script's own docstring (`validate_skill_structure.py:110-114`) says the caller must filter. It sums `off`/`name-only`/`disable-model-invocation` skills the real gate excludes. | `[verified: claude/.claude/skills/tests/test_skills.py::TestTotalListingBudgetUnderSonnet passes on origin/main]` |
| 9 | Per-skill cap is 1536 chars; none of the four descriptions is near it. Neither cap binds this change. | `[verified: validate_skill_structure.py:25,96-106]` |
| 11 | `claude/.claude/skills/code-review/evals/trigger-cases.json` exists and contains two cases that assert the behavior this plan deliberately reverses. `plan-review`, `ready-for-review`, and `respond-pr` have no such fixture. | `[verified: read this session]` |
| 10 | The gate is the authority and the descriptions give way; the sweep covers all gate-backed review skills, not only `code-review`. | `[engineer-verified]` |

**Mechanisms**

- **Rewrite four descriptions** (`anchors: root`). The defective text sits in the always-loaded
  skill listing, which is precisely the surface that causes a session *not* to fire the skill.
  Lighter primitives rejected: (a) note the correction in each skill's **body** — fails, the body
  loads only after the skill fires, which is the event the bad description prevents; (b)
  `skillOverrides: name-only` for `code-review`, the `agent-review` treatment — fails, `code-review`
  genuinely is description-auto-triggered on the "before presenting code" path, which has no hook
  behind it, so stripping its description destroys real routing value.
- **State the invariant once in `docs/hooks.md`** (`anchors: root`). § *Marker keying and
  gate-release authority* already carries two named properties of every gate; this is the third.
  Lighter primitives rejected: (a) fix the four descriptions and write nothing down — fails, the
  defect recurs the next time a skill acquires a gate, and four independent instances is already
  the recurrence; (b) put it in `CLAUDE.md` — fails on altitude and on the 200-line cap enforced by
  `check-claude-md-length.sh`; this is hook-system mechanics, and `docs/hooks.md` is its
  single source of truth.

**Constraint on every rewrite:** the four descriptions total 1423 chars today (code-review 434,
plan-review 330, ready-for-review 356, respond-pr 303). Given row 8, the rewrite must be
roughly net-neutral. Per row 8 this is **discipline, not a hard ceiling** — the enforced listing
budget has ample headroom, so accuracy must never be traded for characters. The drafted
replacements measure 1435 (+12).

**User surface and threat model.** This repo is stow-distributed and public: `claude/.claude/**`
installs into every downstream user's `~/.claude/`, so these four descriptions are a user-facing
surface, not personal config. There is no production reachability and no privilege boundary
crossed — the change alters no hook, no marker content, and no gate-release path. The failure mode
is a downstream session mis-deciding whether to run a review, which is the failure this plan fixes.

The rewritten descriptions assert gate facts ("Also the gate on every `git commit`"), so they are
only true for users who actually have the gates. They do: `install.sh:24` runs a single
`stow -v --adopt -t "$HOME" claude` over the whole package, and `claude/.claude/settings.json` wires
`require-code-review.sh` (:144), `require-plan-review.sh` (:249,279), `require-ready-for-review.sh`
(:207,213), and `require-respond-pr.sh` (:190) unconditionally. There is no supported path that
installs the skills without the hooks. **Accepted residual:** a user who hand-copies an individual
`SKILL.md` off GitHub without running `install.sh` gets a description whose gate claim is false for
them. That cohort is knowingly off the only documented install path, and writing for it would mean
declining to state the gate fact at all — which is the defect being fixed. Named rather than
silently assumed away.

### The invariant to record

> A gate-backed skill's `DO NOT TRIGGER` clauses are read as predictions about whether an operation
> will be allowed, not just as routing hints. A gate hook decides on what it can compute — tool
> name, command shape, staged pathspec, marker hash — so every clause must either name a condition
> under which the hook's own predicate also declines to fire, or explicitly scope itself to a
> trigger point that has no hook behind it. Clauses turning on a judgment ("trivial", "cosmetic")
> or on conversational state ("the user said to skip") cannot be honored and must not be written.

## Critical files

**Modify** — each `description` is replaced with the exact text below; no wording decisions are
left to execution time. Character counts are the measured lengths of these literal strings.

- `claude/.claude/skills/code-review/SKILL.md` — frontmatter `description` only (line 3). Body lines
  169–171 are already correct; **do not touch them.** 434 → 384 chars.

  > Principal-engineer review before presenting code. Also the gate on every \`git commit\`. TRIGGER
  > when: code is about to be presented, a commit is pending, or the user asks for a review.
  > skill-review, agent-review, and ai-instruction-and-memory-files run from inside this review,
  > never instead of it. DO NOT TRIGGER when: a fresh code-review-markers/ entry already covers the
  > staged diff.

  **Named tradeoff:** the "cosmetic-only" clause is removed outright rather than scoped to the
  no-commit path. Scoping it costs ~65 chars to preserve an exemption that is false whenever a
  commit follows — the dominant path in a repo where `/code-review` *is* the commit gate. This is
  the one behavior change here with no hook behind it: every other newly-firing case was already
  being *denied* by a gate, so aligning the description prevents a denial rather than creating work.
  The cost is that a cosmetic change presented without a commit now triggers a review. That review
  is bounded by Step 0's domain gating — "Apply the **Base checklist** always. Apply each **Domain
  checklist** only when at least one changed file matches that domain"
  (`claude/.claude/skills/code-review/SKILL.md:21`) — so a cosmetic diff matching no domain runs the
  base checklist and spawns no specialists. It is not free. Erring toward running the review is the
  safe direction; erring toward skipping it is the defect this plan exists to fix.

- `claude/.claude/skills/plan-review/SKILL.md` — `description` (lines 3–8). 330 → 333 chars.

  > Review implementation plans before presenting to the user. A plan file in .claude/plans/ gates
  > Write/Edit/ExitPlanMode until this runs; triviality and user waivers do not release it. TRIGGER
  > when: a plan is written or updated in .claude/plans/, or is about to be presented. DO NOT
  > TRIGGER when: the plan lives outside .claude/plans/ (chat-level or /tmp drafts).

  Both original clauses survive, re-scoped to where they are true: the gate arms on that directory
  only, so a chat-level or `/tmp` plan draft genuinely is exempt.

- `claude/.claude/skills/ready-for-review/SKILL.md` — `description` (lines 3–9). 356 → 384 chars.

  > Pre-handoff gate: runs verification, code review, syncs or opens a PR. TRIGGER when: handing off
  > to a human reviewer — wrapping up a branch, "ship it" intent, before a multi-persona review or
  > /ultrareview, or on any push to a branch with an open PR, mid-iteration included. DO NOT TRIGGER
  > when: no push or gh pr ready is attempted, or on the default branch.

  The `(CISO + staff-* engineers)` persona enumeration is dropped: the body already enumerates the
  personas, and per `skill-review` §1 the summary routes rather than enumerating body topics.

  "During active iteration" moves from an exemption to an explicit inclusion, which is what the
  hook does. The two honored clauses are kept, restated as the command-shape condition the hook
  actually tests.

- `claude/.claude/skills/respond-pr/SKILL.md` — `description` (line 3). 303 → 328 chars.

  > Respond to PR review comments on the current branch's PR. Enforces required attribution prefix.
  > The gate matches command shape, so it fires on any branch and with no unread comments. TRIGGER
  > when: reading or posting any PR/issue comment or review. DO NOT TRIGGER when: no comment read or
  > post is attempted, or it names another repo.

  Two of the three original clauses were not merely unhonored but circular: "no unread review
  comments" cannot be established without issuing the read the hook blocks, and "a different PR" is
  *denied* rather than exempted. The third — "default branch with no PR open" — is unhonored because
  the hook has no branch or PR-existence check at all.

  The **different-repo** exemption is new and is the one genuine exemption this gate implements:
  `require-respond-pr.sh:293-338` bypasses commands that *explicitly* name a repo other than the
  current `origin` (`gh api repos/OWNER/REPO/...`, `gh pr <cmd> -R OWNER/REPO`), because those are
  research on an external repo rather than a response in this one. Implicit commands still gate,
  since `gh` resolves those against the current repo. An earlier draft of this description claimed
  the gate "fires on any branch **or repo**", which contradicted that bypass.

- `claude/.claude/skills/code-review/evals/trigger-cases.json` — **two cases now assert the opposite
  of the shipped behavior** and must be updated in this PR, or the fixture becomes a false record of
  intent that reports a "regression" on the next eval run:
  - `cosmetic-typo-fix` (`should_trigger: false`, query "Fix the typo in the README heading") — flip
    to `true`. The Named tradeoff above removes exactly this exemption.
  - `vs-skill-review-skill-md-only` (`should_trigger: false`, query "Review the edits I made to
    plan-it/SKILL.md") — flip to `true`, and keep `skill-review` as an additionally-expected skill
    rather than a substitute. This is the *central* case of the whole change: the clause asserting
    SKILL.md-only routes away from code-review is the one finding 1 shows contradicts the skill's
    own body.
  - Add a case for the originally-reported failure: a staged plan file with a commit pending →
    `should_trigger: true`.
  - `plan-only-no-code` (query "I need to plan the implementation of a new rate limiting system")
    stays `false` — that is a request to *plan*, not a plan file pending commit. Do not change it.

- `docs/hooks.md` — add the invariant quoted above as its own `##` section, placed immediately after
  § *Marker keying and gate-release authority* (which spans lines 41–56) and before § *Gate deadlock
  recovery*. **Not** as a third bullet inside the marker-keying section: that section's two existing
  properties are both about marker mechanics — what a marker's content authorizes, and who may write
  one — whereas this invariant is about how a skill's trigger prose must be worded. Filing them
  together would misdirect an engineer auditing marker access control into description-wording
  concerns.

**Measured total: 1423 → 1435 chars (+12)** (code-review 385, plan-review 361, ready-for-review 357,
respond-pr 332). Every summary sentence is ≤80 chars per `skill-review` §1. Treat +12 as the
ceiling, not a starting allowance; re-measure with `validate_skill_structure.py --corpus` after
editing.

### Behavioral-equivalence audit of the drafted text

`skill-review` §7.11 requires this table for any diff that removes or shortens lines. Run against
the drafts above; the implementer must re-run it against the actual diff.

| Removed / shortened | Surviving line | Preserving? |
|---|---|---|
| code-review: "cosmetic-only changes (typo, formatting, CSS with no behavioral delta)" | none | **N — deliberate.** Named tradeoff above; behavior change is that a cosmetic change presented without a commit now triggers a review. Must stay named in the PR description. |
| code-review: "SKILL.md→skill-review, agent→agent-review, CLAUDE.md/AGENTS.md/memory→ai-instruction-and-memory-files" | "skill-review, agent-review, and ai-instruction-and-memory-files run from inside this review, never instead of it" | **Y** — same three skills named, relationship corrected. |
| code-review: "plan→plan-review" | none | **Y** — dropping it is the point. Both skills firing on a staged plan file is now correct, so no dual-fire risk remains to disambiguate. |
| plan-review: "a trivial one-liner (single migration, config change)" | "the plan lives outside .claude/plans/ (chat-level or /tmp drafts)" | **Y after revision.** The first draft lost *which* plans are exempt; the parenthetical restores it. |
| plan-review: "the user has explicitly said to skip review" | "triviality and user waivers do not release it" | **Y** — inverted but explicit. |
| ready-for-review: "during active iteration" | "on any push to a branch with an open PR, mid-iteration included" | **Y** — inverted to an inclusion, explicitly. |
| ready-for-review: "on diff-only requests" | "no push or gh pr ready is attempted" | **Y** — a diff-only request issues no push; covered by command shape. |
| respond-pr: "no unread review comments" | "it fires on any branch and with no unread comments" | **Y** — inverted, explicit. |
| respond-pr: "default branch with no PR open" | "it fires on any branch …" | **Y** — inverted. |
| respond-pr: "the conversation is about a different PR" | "DO NOT TRIGGER when: … it names another repo" | **partial, deliberate.** A different *repo* is genuinely exempt and is now stated. A different *PR in the same repo* is not exempt — the hook denies and its own deny text tells the session to stop and ask the user, which it reads at denial time. Restating that in always-loaded budget buys nothing. |

**Platform-genericness (§7.12).** Extracted tokens: `git commit`, `git push`, `gh pr ready`,
`[Claude Code]`. The `gh` tokens are vendor-CLI names, but both bodies are already GitHub-coupled
(`ready-for-review/SKILL.md` has 9 `gh`/`git push` hits; `respond-pr/SKILL.md` has 15, with
`[Claude Code]` literal at three sites), and the gate hooks themselves match on `gh pr ready` and
`gh pr comment`. A description that named a generic abstraction instead would fail the very
alignment this change exists to produce. Deliberate; no project layer needed.

**Deviation from §1's one-summary-sentence target, applied uniformly.** All four descriptions carry
a second declarative sentence stating the gate fact before `TRIGGER when:`. That fact cannot live
inside `DO NOT TRIGGER` — "triviality does not exempt you" strengthens the trigger rather than
narrowing it — and burying it in the body defeats the purpose, since the body loads only after the
skill fires. Four consistent shapes beat four inconsistent ones.

**Add**

- `claude/.claude/hooks/tests/test_hook_alignment.py` — a table-driven test pinning the four
  gate↔skill pairings this plan's invariant reasons about: `code-review`↔`require-code-review.sh`,
  `plan-review`↔`require-plan-review.sh`, `ready-for-review`↔`require-ready-for-review.sh`,
  `respond-pr`↔`require-respond-pr.sh`. Assert both files exist for each pair. This is *not* the
  honorability test ruled out under *Out of scope* — it checks only that the pairing does not
  silently desync on a future rename or split, which is mechanically decidable and durable.
  `test_hook_alignment.py` today verifies each gate hook's class and deny-schema shape but never
  pins which skill releases which gate.

**Reuse, do not reimplement**

- `plugins/skill-management/scripts/validate_skill_structure.py` — already provides both the
  per-skill cap check and `--corpus` budget check. Use it to verify the length constraint; do not
  hand-count.
- `plugins/skill-management/skills/skill-review/SKILL.md:3-6` — the already-correct phrasing model
  for "dispatched, not auto-triggered". Match its shape rather than inventing new wording.

**Explicitly not modified**

- Any `require-*.sh` hook. No gate behavior changes in this work.
- `.claude/plans/trim-skill-budget.md:50`, which quotes the current `code-review` description
  verbatim. It is a shipped plan recording a past state — preserved-record content under CLAUDE.md
  § Scope discipline, Axis 3.

## Verification

1. `.venv/bin/pytest claude/.claude/` — full suite. From the worktree:
   `../../../.venv/bin/pytest claude/.claude/`. The file that actually reads these four descriptions
   is `claude/.claude/skills/tests/test_skills.py` — `TestModelInvokableSkillTriggerContracts`
   (asserts both `TRIGGER when:` and `DO NOT TRIGGER when:` substrings survive),
   `TestModelInvokableDescriptionLength` (per-skill cap), and `TestTotalListingBudgetUnderSonnet`
   (filtered corpus). All three must stay green; the trigger-contract test is the one a careless
   rewrite would break. `test_doc_counts.py` and `test_hook_alignment.py` do not pin description
   text and should be unaffected.
2. `.venv/bin/ruff check claude/.claude/` and
   `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck` — should be untouched no-ops, since
   no Python or shell changes here. Run them to confirm exactly that.
3. Per-skill cap: run `validate_skill_structure.py` against each of the four edited files.
4. Corpus budget: rely on `TestTotalListingBudgetUnderSonnet` in step 1, which applies the
   skillOverrides filter. Do **not** hand-run `validate_skill_structure.py --corpus` over a raw
   `git ls-files` list — that unfiltered call is what produced the false "over budget" premise
   corrected in row 8, and it would have the implementer working against a ceiling that does not
   exist.
5. **Behavior test, per skill** — the substantive check, and the one the automated steps cannot do.
   For each scenario below, read the rewritten description *alone* — no body, no CLAUDE.md — and
   answer "do I run this skill?". The answer must match what the hook will actually do:

   | # | Scenario | Required answer |
   |---|---|---|
   | 1 | Staged diff is one plan file, commit pending | Run `/code-review` — this is the reported failure |
   | 2 | Staged diff is one `SKILL.md`, commit pending | Run `/code-review` **and** `/skill-review` |
   | 3 | Staged diff is one `CLAUDE.md`, commit pending | Run `/code-review`; `ai-instruction-and-memory-files` runs inside it |
   | 4 | Staged diff is one agent file, commit pending | Run `/code-review`; `agent-review` runs inside it |
   | 5 | Typo-only change, commit pending | Run `/code-review` |
   | 6 | Mid-iteration `git push`, PR open | Run `/ready-for-review` |
   | 7 | PR-comment read, no unread comments | Run `/respond-pr` |
   | 8 | `gh api repos/OTHER/REPO/pulls/1/comments` | Do **not** run `/respond-pr` |
   | 9 | One-line plan drafted in chat, never written to `.claude/plans/` | Do **not** run `/plan-review` |

   **The expected answers above are recorded before the rewrite is read, and the check is not
   self-graded.** The author already knows what they meant, so grading one's own description against
   one's own intent is close to tautological. Hand the table and the four rewritten descriptions —
   nothing else, no bodies, no `CLAUDE.md` — to the `/skill-review` pass in step 6 or to a fresh
   subagent, and have that reader produce the answers cold.

   Scenarios 3 and 4 specifically test `code-review`'s new "run from inside this review, never
   instead of it" claim. Scenario 3 carries the most weight: of the three named specialists,
   `ai-instruction-and-memory-files` is the only one that can still auto-trigger on its own —
   `skill-review` disclaims description-triggering in its own text
   (`plugins/skill-management/skills/skill-review/SKILL.md:3-6`) and `agent-review` is `name-only`
   in `settings.json`, so its description never reaches the listing. If a session reads
   `ai-instruction-and-memory-files`' live TRIGGER clause, fires it alone, and treats the file as
   reviewed, that reproduces the original bug shape against a different gate.
6. `/skill-review` on the diff — hook-enforced at commit by `require-skill-review.sh`, and it owns
   the behavioral-equivalence audit for the shortened description text. Four `SKILL.md` files are
   staged, so this gate will fire.
7. `python evals/run_skill_evals.py --skill code-review` — **mandated**, not optional:
   `plugins/skill-management/skills/skill-review/SKILL.md:64` requires it after any `TRIGGER`-block
   change, and all four edits are `TRIGGER`-block changes. Only `code-review` has a
   `trigger-cases.json`, so only it can be run. Run it *after* updating the two contradicted cases,
   and read the output as a pass-rate report — per `evals/README.md` the harness is local-only and
   probabilistic, never a gate. Confirm rates held; a drop on the two flipped cases means the
   rewrite did not actually communicate the new behavior.

## Out of scope

- **No test of clause honorability.** A test asserting "no gate-backed description states an
  unhonorable exemption" cannot be written mechanically — honorability is a judgment about a bash
  predicate, not a parseable property. A forbidden-vocabulary test ("trivial", "cosmetic", "the user
  said") is the tempting substitute and is a symptom test: the defect that produced this plan —
  `code-review`'s backwards routing arrows — contains none of those words and would sail past it.
  Not written.
- **`docs/hooks.md:16` understates `require-respond-pr.sh`'s scope** — the row omits the
  un-numbered comment-ID PATCH endpoint, `gh issue comment`, and the GraphQL comment/review
  mutations that `require-respond-pr.sh:211,213-215` actually gates. This one *will* be fixed in
  this PR and listed under "Incidental edits": the respond-pr description rewrite asserts the gate
  fires on command shape for any repo, and leaving the doc row narrower would ship a fresh
  contradiction between two files this change already touches.
- **`docs/hooks.md` has no rows at all for `require-plugin-version-bump.sh` or
  `require-npm-version-bump.sh`**, though the structurally identical plugin-shipped
  `require-skill-review.sh` is documented at line 9. Unrelated to trigger alignment — raising to
  the reviewer rather than bundling.
- **The corpus overage itself** (row 8, 9148 vs 8000). Pre-existing, fails at the merge-base, and
  reducing it means trimming skills this change has no other reason to open. Constraining this
  change to net-neutral is the in-scope obligation; fixing the overage is separate work.

## Review surface

Seven files: four single-frontmatter-field skill-description edits, one eval fixture, one new
documentation section, one added test. No hook behavior changes and no executable code beyond the
test. Risk is concentrated in wording precision — an imprecise rewrite reintroduces the same class
of defect rather than failing loudly — which is why verification step 5 must be graded by a cold
reader, step 6 is hook-enforced, and step 7 is mandated by the skill that governs this file type.
