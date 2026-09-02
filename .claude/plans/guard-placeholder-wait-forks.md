# Guard against placeholder "wait" fork overspawn

## Context

A user relayed a self-observed model-behavior bug: while multiple background
`Agent` dispatches were already in flight, the model spawned an additional
`fork` subagent whose entire instruction was to do nothing and report back
immediately — apparently trying to force a wait/checkpoint instead of simply
not calling a tool that turn. Each placeholder fork inherited the full
parent context for zero work.

The user asked two things: whether this warrants a guard, and whether it has
happened in other sessions. A transcript-corpus search this session (five
account roots, several keyword phrasings — a floor, not an exhaustive count)
found exactly two real occurrences with unrelated prompt wording: one in a
private project's repository, one in this repository's own history
(`memory-content-migration` worktree, 2026-08-30). The user chose the
mechanism: a rule added to the `subagent-delegation` skill, explicitly
rejecting a hook as brittle — pattern-matching prompt text for intent is
evadable by phrasing drift (the two occurrences already differ), and risks
blocking a legitimate short dispatch.

## Approach

Add one short, self-standing rule to `subagent-delegation`'s Step 1 stating
that a dispatch must return information the parent does not already have,
and that an agent instructed to do no work waits for nothing while still
paying a full agent's context cost. The rule is stated by *shape* rather
than by prompt phrasing, so it covers sibling shapes (a status-poll dispatch,
a dispatch that restates what the parent already knows) without enumerating
them — the same collapsing form that lets prose succeed where the rejected
hook's pattern-matching would need constant upkeep.

Verbatim text to insert:

```markdown
**A dispatch must return something the parent does not already have.**
Never dispatch an agent — of any type — whose instructions are to do no
work: to report back immediately, to occupy the turn, or to hold while
other dispatches finish. A no-op agent returns at once, so it waits for
nothing. It still pays a full agent's context cost for an empty return.
Waiting is not an action a dispatch can perform: when pending dispatches
are all that remain, end the turn without a tool call and let their
completion drive the next one.
```

The core justification — a no-op agent returns immediately and therefore
fails at the very wait it was spawned to perform — is self-evidently
checkable by any model reading it and depends on no harness mechanism,
which is what makes a prose guard work here.

**Assumption ledger**

**Root:** Nothing in `subagent-delegation` tells a session that an agent
dispatched to do no work is never the right call, so a session with
background dispatches in flight can spawn a placeholder agent that pays a
full agent context and returns nothing.

**Givens:**

- The harness mechanism by which a completed background dispatch re-engages
  a session is Anthropic-owned and not documented in this repo. Consequence
  for the design: the rule's "what to do instead" clause is written so it
  holds regardless of that mechanism ("end the turn... let their completion
  drive the next one"), rather than asserting a notification behavior this
  repo cannot verify.
- `claude/` is stowed, so this skill's audience is every stow consumer, not
  this session's owner. The design cannot scope the rule to one machine, and
  the wording must stay platform-agnostic per
  `.claude/rules/skill-and-agent-self-review.md`.

**Rows:**

1. Mechanism is a prose rule in the skill body, not a hook.
   `[engineer-verified]` — the user's explicit answer this session, with the
   hook rejected as brittle (pattern-matching prompt text for intent,
   evadable by phrasing drift, false positives on legitimate short
   dispatches). `anchors: root`
2. Over-powered-primitive check on row 1. The chosen mechanism is the
   lightest of three; the two heavier ones are enumerated and rejected. (a)
   A `PreToolUse` hook on `Agent` — a privileged execution context matching
   on tool-input *content*, for which no precedent exists: the only current
   `Agent`-matching hook, `require-routing-read.sh`, gates on a crisp
   file-read fact, not prompt text. (b) A line in `claude/.claude/CLAUDE.md`
   — wider scope, not lighter, since it is re-read every turn of every
   session; and CLAUDE.md's Working Style bullet already defers
   dispatch-vs-inline judgment to this skill, so the skill is the single
   source of truth for it. `anchors: row1`
3. Placement is a new bolded-lead paragraph in Step 1, inserted after the
   "Stays inline — do not over-delegate" list and before the "**No
   permission cost.**" paragraph. `[verified:
   claude/.claude/skills/subagent-delegation/SKILL.md:40-63]` — Step 1
   already carries exactly this shape twice (`**Operational trigger:**`,
   `**No permission cost.**`), so this uses the section's existing
   structure. Placing it before "No permission cost." groups the two
   anti-over-delegation items and leaves the pro-delegation note last.
   `anchors: row1`
4. It is deliberately *not* a fifth bullet in the "Stays inline" list.
   `[verified: same file:44-58]` — every existing bullet names work the
   parent performs itself; a no-op has no work to perform inline, so that
   list's heading would mis-describe it. `anchors: row3`
5. The paragraph must stand alone rather than lean on the two-test gate.
   `[verified: same file:24-25]` — the gate's opening sentence scopes it to
   "before running a `Bash` command," so a non-Bash dispatch decision is not
   something the gate as written reaches. `anchors: row3`
6. The two observed occurrences used unrelated prompt wording. `[verified:
   transcript-corpus search this session across five config-dir roots and
   several keyword phrasings; a floor, not an exhaustive count]` — this is
   the evidence that the failure is a shape, not a phrasing, and it is why
   the rule states the shape. `anchors: root`
7. Illustrative phrasings inside the rule ("report back immediately",
   "occupy the turn") are examples a model generalizes from, not a match
   list. `[unverified]` — a claim about model behavior, load-bearing: it is
   the reason including examples does not re-import the brittleness that
   disqualified the hook. `anchors: row1`
8. The rule states the cost qualitatively and carries no token figures.
   `[verified: CLAUDE.md §Redact private-project-identifying content,
   provenance paragraph]` — the measured per-dispatch figures come from a
   private project's transcript, and publishing a computed or recalled
   figure carries that engagement's fingerprint just as a quoted one would.
   This binds the plan file and PR body as well as the skill body. `anchors:
   root`
9. The frontmatter `description` is not modified. `[verified: same
   file:1-13]` — the DO NOT TRIGGER list names cases where the skill should
   *not* load, so adding placeholder dispatches there would suppress the
   skill in precisely the situation the rule must cover. A TRIGGER-side
   addition was also weighed and rejected: the skill already triggers on
   dispatch decisions, and any description edit converts a zero-risk body
   addition into a trigger-behavior change with blast radius across every
   stow consumer. `anchors: row1`
10. Residual limitation of the chosen mechanism: the rule only fires if the
    skill is in context at the moment of the placeholder impulse.
    `[unverified]` — whether the two observed instances had the skill loaded
    is not established. Accepted rather than mitigated, because mitigating
    it means widening an always-loaded file, which the user's chosen scope
    excludes. If a third instance appears, the cheap next lever is a clause
    on the existing `subagent-delegation` pointer in
    `claude/.claude/CLAUDE.md`'s Working Style section — not a hook.
    `anchors: row1`
11. No mechanical enforcer ships with the rule, departing from this repo's
    usual practice of landing a convention's enforcing test in the same PR.
    `[engineer-verified]` — the trigger is dispatch *intent*, which is not a
    file-content fact any test or hook can assert; a test asserting the body
    contains a given string would be tautological and would break on any
    later rewording. `anchors: row1`

## Critical files

- `claude/.claude/skills/subagent-delegation/SKILL.md` — **modify.** Insert
  the verbatim paragraph from the Approach section between the end of the
  "Stays inline — do not over-delegate" bullet list (currently line 58,
  ending "...not the investigation that precedes it.") and the "**No
  permission cost.**" paragraph (currently line 60), separated by a blank
  line on each side. No other line in the file changes — frontmatter, Step
  1's gate text, the bullet list, and all of Step 2 stay byte-identical.

**Reuse opportunities:** match the existing bolded-lead-sentence paragraph
shape already used twice in Step 1 (`**Operational trigger:**`, `**No
permission cost.**`) rather than introducing a new formatting convention.
Stay inside the skill's established metaphor — a subagent as a function call
whose *return value* is what the parent keeps (file header, lines 17-20) —
instead of coining a second framing for the same idea.

**Dispatch split:** one `code-writer` dispatch, `model: sonnet`, not
splittable. One file, one paragraph, verbatim text fixed by this plan — the
decision-made test holds by construction and there is no second
non-overlapping file set to partition. The parent retains `/skill-review`,
`/code-review`, the review marker, and the commit: `code-writer` holds no
`Skill` tool and is denied marker writes, so a review gate it hits comes
back to the dispatching session to resolve.

## Verification

No Python or shell changes, so `ruff` and `shellcheck` are no-ops for this
diff — do not run them and do not claim they passed.

1. `.venv/bin/python3 claude/.claude/scripts/select-tests.py` from this
   worktree root, using the worktree's own `.venv` (README's Tests section
   covers the worktree-relative `.venv` paths). Let it select — do not
   widen to the full suite by hand; a SKILL.md-only diff is squarely inside
   its rule table, and CI runs the full suite on push.
2. `/skill-review` on the diff — hook-enforced: `require-skill-review.sh`
   blocks `git commit` until the behavioral-equivalence marker is written
   (`.claude/rules/review-pipeline-dispatch.md`). The specific claim it must
   confirm here: the frontmatter is untouched, so *when* the skill loads is
   unchanged, and the addition only changes what the skill says once
   loaded.
3. `/code-review` before the commit, per global CLAUDE.md. It dispatches
   `/skill-review` automatically for SKILL.md changes, so running it
   satisfies step 2 as well.
4. Read the rendered paragraph in place and confirm it reads as a peer of
   `**Operational trigger:**` and `**No permission cost.**`, not as an
   orphaned bullet — the placement is the design, and a diff that lands the
   text inside the bullet list defeats row 4.
5. `/plan-review` on this plan before it is presented, and
   `/ready-for-review` before push, per the hook-enforced pipeline.

## Out of scope

- **A hook.** Explicitly rejected by the user this session. Do not add one,
  and do not add a "lightweight" content check on `Agent` tool input as a
  compromise — that is the same mechanism at a smaller size.
- **A test asserting the rule's presence.** Row 11 covers the reasoning; a
  body-string assertion is tautological and brittle to rewording.
- **Any frontmatter change** — neither the TRIGGER nor the DO NOT TRIGGER
  list. Row 9 covers why the DO NOT TRIGGER side would be actively harmful.
- **Any edit to `claude/.claude/CLAUDE.md`** or to its `subagent-delegation`
  pointer. Named as the next lever if a third instance appears; not this
  change.
- **Step 1's Bash-scoped opening sentence.** The section already contains
  non-Bash items ("comprehension read", "Edit/Write sequences"), so the
  framing is mildly incoherent today. That predates this change and fixing
  it would put an unrelated edit in a skill-review-gated diff.
- **`fork`-specific handling.** The rule says "an agent — of any type"
  deliberately; do not add a `fork` carve-out or a `fork`-named example.
- **A fuller corpus count.** Two occurrences is a floor established by
  keyword search, not a proven total. Do not state or imply an exhaustive
  count anywhere in the plan, commit message, or PR body.
- **Private-project evidence in any committed artifact.** The originating
  project's name, repo path, branch identifier, and per-dispatch token
  figures stay out of the plan file, skill body, commit message, and PR
  body.
