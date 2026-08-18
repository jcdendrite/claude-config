# Close the self-review gap in code-review's spawn-dispatch step

## Context

`skill-fidelity-reviewer` — the observer built specifically to catch a session
that invokes a skill, loads its procedure, and then quietly delivers work
that skips it — categorically excludes `code-review` from its own audit
scope. One incident showed the resulting hole: during `/ready-for-review`
step 3's cumulative-diff `/code-review` pass, the orchestrating session
skipped a matched Change-type-table row and reviewed it itself, citing two
rationales the skill's own "Invalid skip rationales" list explicitly
disallows ("Verified inline," "Self-review sufficient"). No downstream gate
caught it — the one mechanism built for exactly this failure class was
excluded from the one skill it happened inside. The goal is to close that
scope gap so a genuine spawn-dispatch omission gets caught before a branch
reaches a human reviewer, without inventing new detection machinery to do it.

**Frequency, and what it does and doesn't tell us.** Two transcript scans
this session (the default account, plus all 5 other Claude Code accounts
configured on this machine, ~98 additional `.jsonl` files) found exactly
**1 confirmed instance total**: the incident above. That count is a
floor on literal/near-literal reuse of the two named banned phrases, not a
ceiling on the failure class — the design below (see Approach) concludes the
more likely failure shape is a row silently *omitted* from the Spawn-decisions
enumeration entirely, which leaves no banned phrase for any phrase-based scan
to find. Low observed frequency is a fact about what the current
all-prose mechanism can detect, not necessarily about true incidence.

Three draft options (a bash+jq hook doing invalid-rationale-substring
detection; the same plus actual-spawn cross-referencing; a prose-only
strengthening) were put to the user first and rejected as "not great," with
an explicit ask for independent design judgment before committing to an
approach. An Opus-model design review was dispatched with full context
(the incident, the existing tooling gaps, `docs/design-decisions.md`'s
philosophy sections, and the three rejected drafts) and asked to question the
framing rather than referee the options. Its recommendation — extend
`skill-fidelity-reviewer`'s scope narrowly rather than build new detection
surface — is what this plan implements, after direct verification of every
technical claim it made (see Assumption ledger).

## Approach

**Root problem:** two attestations that gate a branch's handoff — "which
Change-type rows this diff matches" and "were the required specialists
actually dispatched" — are both currently self-reported by the same session
that has an incentive to shortcut either one, and nothing independent checks
either.

**Givens:**
- Change-type row-matching is semantic ("what the change does for an
  operator or consumer," not file-type-derivable) by explicit design
  [verified: `code-review/SKILL.md:233`]. A fully mechanical hook/regex
  classifier for the whole table isn't achievable without an
  uncharacterizable false-negative rate — the table's own design intent
  rules this out, not a choice this plan makes differently.

**Per-mechanism justification (anchors: root):**

1. **Extend `skill-fidelity-reviewer`'s scope** (the `docs/design-decisions.md`
   §9 "extend" arm — "cheap, default move") **rather than build a new hook or
   spawn a new reviewer persona.** [anchors: root] Two heavier alternatives
   considered and rejected:
   - *A bash+jq hook* tailing the transcript for a "Spawn decisions:" block
     and substring-matching the 5 known-invalid rationale phrases — technically
     reachable (this repo's hooks can already see `.transcript_path` in their
     JSON payload; nothing prevents a hook from parsing prose out of it), but
     none of the existing ~20 hooks does so today and `_lib.sh` has no shared
     transcript-reading helper [verified: grep across
     `claude/.claude/hooks/*.sh` and `_lib.sh`'s function list, this
     session] — reaching past the payload-boundary precedent this repo has
     actually built is the heavier primitive, set aside for the concrete
     reasons below, not because it's structurally out of reach. Rejected
     on three independent grounds: (a) it trains its own evasion — the
     incident is visible *only* because the model confessed using the
     skill's own banned vocabulary; a matcher keyed to those literals selects
     for paraphrase, converting a loud, self-reported failure into a silent
     one while creating a false coverage claim — the exact "compounding
     defensive layers" wrong-foundation tell CLAUDE.md names, since it
     doesn't dissolve the self-report problem, it just relocates where the
     omission has to be worded; (b) it's structurally blind to the more
     likely failure shape (a row never enumerated produces no banned phrase
     to match); (c) no usable tool-call boundary even exists for it — the
     actual incident happened during `/ready-for-review` step 3's
     cumulative-diff pass, which explicitly does **not** call `marker.sh
     write code-review` [verified: `ready-for-review/SKILL.md:78-80`, "do
     NOT write the review-completion marker"], so gating that marker-write
     would not have caught the reported incident at all.
   - *A new reviewer persona* dedicated to this check. Rejected against
     `docs/design-decisions.md` §9's own bar for spawn-from-scratch — "chronic
     gap... AND extending an existing persona would dilute it." Neither leg
     holds: the gap is not chronic (1 confirmed instance across 6 accounts),
     and `skill-fidelity-reviewer` is already the correctly-shaped persona
     for this exact failure class by its own design citation ("a session can
     invoke a skill by name, load its procedure, then deliver work that skips
     it... The fix is an observer that never shares that context") — it is
     simply mis-scoped to exclude the one skill this incident happened
     inside.

2. **Feed `skill-fidelity-reviewer` a second, narration-free input**
   (`transcript-analysis.py review-trace --this-repo --branches`) for the
   "was it actually dispatched" half, instead of trusting code-review's own
   self-reported "Spawn decisions:" text for that half. [anchors: row 1] This
   is mechanically decidable today with zero new detection code:
   `review-trace` already emits an ordered per-session timeline of `skill`
   and `reviewer-spawn` events derived purely from `Skill`/`Agent`/`Task`
   `tool_use` records [verified: `transcript-analysis.py:1274-1289`
   (`REVIEW_TRACE_SKILLS`, `_REVIEWER_PREFIX`/`_REVIEWER_EXACT`),
   `:2029-2045` (`cmd_review_trace` docstring), `:2126-2141` (event-line
   output format) — all read directly this session]. Handing this to
   `skill-fidelity-reviewer` preserves its blindness property exactly: the
   timeline carries `subagent_type`/timestamp/branch metadata only, never
   assistant prose.

3. **For "which rows matched," keep `skill-fidelity-reviewer`'s existing
   independent-judgment model** — it already reads a skill's body fresh from
   disk and compares it to the diff for every other skill it covers.
   [anchors: row 2] No change to *how* it judges, only to *what* it's now
   permitted to judge (code-review's Ripple-effect-triage table
   specifically) — including applying the table's own built-in qualifiers
   (e.g., "declared dev-only... no production-reachable surface" for
   high-stakes rows) when deciding whether a row genuinely matches, so a
   diff that's legitimately out of a row's scope doesn't false-positive.

**Alternative considered and declined:** persisting code-review's
Spawn-decisions text (or just its disclosed-skip lines) to the PR body,
mirroring the existing `## Deferred review findings` DEFER-persistence
mechanism, to give `skill-fidelity-reviewer` an explicit disclosure channel.
Declined: it's still self-report, merely published, so it doesn't change
*who* is trusted; it adds body noise to every PR; and item 3's
independent-row-matching already has a mechanism for avoiding false
positives on legitimate skips (applying the table's own qualifiers) —
a second mechanism isn't warranted unless false positives are actually
observed in practice. Named here so it isn't silently re-proposed later.

**Assumption ledger:**
- `[verified: this session, direct grep]` `review-trace`'s `--projects`
  default is machine-wide (`"*"`), the opposite polarity from
  `skill-invocation`'s repo-scoped default
  [`transcript-analysis.py:9540` vs. `:2793`]. `--this-repo` must be passed
  explicitly or the dispatch leaks other repos' same-named branches in as
  false spawn evidence — branch names aren't unique across repos.
- `[verified: this session, direct grep]` `comment-discipline-reviewer` (a
  real Change-type-table dispatch target,
  `code-review/SKILL.md:248`) is absent from both
  `_REVIEWER_PREFIX`/`_REVIEWER_EXACT` (review-trace's detection set,
  `:1288-89`, consumed at `:1824`) and `_REVIEWER_YIELD_EXTRA_EXACT`
  (reviewer-yield's separate extended set, `:3224`, consumed by
  `_is_reviewer_subagent_type` at `:3663-68`). Left unfixed, a genuine
  `comment-discipline-reviewer` dispatch is invisible to `review-trace` and
  the new check would false-positive a silent-skip finding whenever that row
  is the one that matched.
- `[verified: two subagent scans this session]` 1 confirmed instance total
  across all 6 accounts on this machine — see Context's frequency caveat.
- `[engineer-verified]` The user rejected the three draft
  options and asked for independent (Opus) design judgment before committing
  to an approach; this plan follows that judgment after direct verification
  of its technical claims (all citations above were re-derived from source
  this session, not passed through unverified).
- `[unverified]` Whether independent row-matching, applying the table's own
  qualifiers, reliably avoids false-positiving on a legitimately-disclosed
  skip in practice — no such case has been observed because the check
  doesn't exist yet. The edited prose (Critical file 3) states the qualifier
  requirement explicitly; if false positives appear in practice, the
  declined PR-body-persistence alternative above is the next thing to
  reconsider, not a reason to hold this plan.

## Critical files

1. **`claude/.claude/scripts/transcript-analysis.py`** — consolidate
   `_REVIEWER_EXACT` (`:1289`) and `_REVIEWER_YIELD_EXTRA_EXACT` (`:3224`)
   into one shared exact-name set containing `ciso-reviewer`,
   `skill-fidelity-reviewer`, and `comment-discipline-reviewer`; update both
   consumers (`review-trace`'s check at `:1824` and
   `_is_reviewer_subagent_type` at `:3663-68`) to reference it. Two
   structural siblings currently duplicate a near-identical set and both
   have the same gap (per audit-structural-siblings — CLAUDE.md) — fix both,
   not just the one `review-trace` depends on.
2. **`claude/.claude/skills/ready-for-review/SKILL.md`** step 4 — add a
   `review-trace --this-repo --branches "$BRANCH"` extraction alongside the
   existing `skill-invocation` call; document the `--this-repo` polarity
   inline (opposite default from `skill-invocation`) so a future edit
   doesn't "harmonize" the two flags incorrectly; pass the new output to
   `skill-fidelity-reviewer`'s dispatch prompt as a named third input,
   alongside the existing skill-invocation list, diff text, and plan path.
3. **`claude/.claude/agents/skill-fidelity-reviewer.md`** — narrow the
   `code-review` scope exclusion (`:27-34`) to a named exception:
   `code-review`'s spawn-dispatch obligation comes into scope only for a
   **completed** pass and only when a review-trace timeline is present in
   the dispatch prompt; everything else about `code-review` stays excluded
   (avoids the agent generating undecidable noise across the skill's other
   ~30 steps). Add the timeline as a third input-contract item with the
   admissibility rationale (tool-call metadata only, preserves the
   blindness property `:25` already states). Add the comparison step: read
   the Ripple-effect-triage Change-type table fresh from disk, form an
   independent judgment of which rows the diff matches (applying the
   table's own qualifiers, per Approach item 3), cross-reference matched
   rows against the timeline's `reviewer-spawn` events at **branch**
   granularity (a spawn from an earlier per-commit pass on the same branch
   satisfies a row — this is a last-gate-before-handoff check, not a
   re-litigation of every iteration), and flag only rows matched-but-never-
   satisfied as `[SILENT-SKIP]` (reuses the existing verdict vocabulary —
   no new verdict category needed). **Scope this check to rows whose
   dispatch target is a specialist Agent/Task spawn** (`staff-*`,
   `ciso-reviewer`, `comment-discipline-reviewer`) **and explicitly exclude
   the "Adds or modifies a skill, agent, instruction-file rule, or hook"
   row** — its required action is a `Skill`-tool invocation
   (`skill-review`/`agent-review`/`ai-instruction-and-memory-files`/
   `claude-hook-review`), not an Agent/Task spawn, and `review-trace`'s
   `reviewer-spawn` event kind only observes `Agent`/`Task` `tool_use`
   records; its `skill` event kind would catch `skill-review`/`agent-review`
   but `REVIEW_TRACE_SKILLS` [verified: `transcript-analysis.py:1274-1276`]
   omits `ai-instruction-and-memory-files` and `claude-hook-review` entirely,
   so checking that row today would false-positive a `[SILENT-SKIP]` on a
   correctly-handled case. State this exclusion in the agent's prose so it
   reads as a deliberate cut, not a gap a future editor "completes." Requires
   `/agent-review` per `.claude/rules/skill-and-agent-self-review.md`.
4. **`claude/.claude/skills/code-review/SKILL.md`** — state the *Invalid
   skip rationales* list's underlying class ("no rationale asserting that
   non-specialist scrutiny substitutes for the dispatch") above the five
   named instances, so the rule reads correctly against a paraphrase no
   mechanical check downstream will ever catch. Requires `/skill-review`
   (hook-enforced on `SKILL.md` diffs).
5. **`docs/design-decisions.md`** — new numbered section recording: the
   mechanizable subset of the Change-type table is already hook-enforced
   elsewhere (`require-skill-review.sh` on SKILL.md paths) and the residual
   is inherently judgment; why a rationale-substring detector was rejected
   (trains its own evasion); why §16's prose-only precedent doesn't transfer
   here (DEFER has a persisted, human-visible PR-body artifact; spawn
   decisions currently have none); the PR-body-persistence alternative
   considered and declined.

## Verification

- `../../../.venv/bin/pytest claude/.claude/` — confirm no existing test
  asserts the old blanket `code-review` exclusion or the pre-consolidation
  reviewer-detection sets in a way this change breaks; add/update coverage
  for the consolidated reviewer-name set and for `review-trace`'s
  `--this-repo` scoping.
- `../../../.venv/bin/ruff check claude/.claude/`.
- `/skill-review` on the `ready-for-review/SKILL.md` and `code-review/SKILL.md`
  diffs (hook-enforced at commit).
- `/agent-review` on the `skill-fidelity-reviewer.md` diff.
- Manual dry run: construct or reuse a small branch with one matched-but-
  undispatched Change-type row (e.g., a `.claude/agents/*.md` edit with no
  `agent-review` invocation on the branch) and confirm the widened
  `skill-fidelity-reviewer` flags it; confirm a branch with a genuine
  dispatch does not flag.
- `/code-review` on this change's own cumulative diff, then `/ready-for-review`
  before handoff — this change is itself subject to the gate it modifies,
  running against the pre-change `skill-fidelity-reviewer` body until this
  PR's own review completes.

## Out of scope

- Verifying spawn *quality* (a dispatch with a vacuous question) — would
  require reading the dispatch prompt, i.e. the deviating session's own
  narration, defeating the blindness property the mechanism depends on.
- Any per-commit (non-`/ready-for-review`) `/code-review` pass — coverage
  stays at branch-level, last-gate-before-handoff granularity, matching
  where `skill-fidelity-reviewer` is dispatched today (only from
  `/ready-for-review`).
- Persisting Spawn-decisions text or skip rationale to the PR body
  (considered and declined in Approach).
- Mechanizing Change-type row-matching itself into a hook/script classifier
  — the table is semantic by design; only the already-mechanizable subset
  (SKILL.md/agent/plugin-path changes) is hook-enforced today, unchanged by
  this plan.
- Checking the "Adds or modifies a skill, agent, instruction-file rule, or
  hook" Change-type row — its dispatch target is a `Skill`-tool invocation,
  not an Agent/Task spawn, and `review-trace` cannot observe two of its four
  possible targets today (see Critical file 3). Closing this would mean
  widening `REVIEW_TRACE_SKILLS` and extending the comparison logic to check
  `skill` events too — a second, structurally distinct check, not a
  same-shape extension of this one.
