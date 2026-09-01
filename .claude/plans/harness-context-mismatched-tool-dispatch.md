# Harness bug: `ScheduleWakeup` reflexively misapplied outside `/loop`

## Context

Record, as a durable decision, why this repo will not build a hook or
other repo-side mechanism to catch a recurring harness-level misfire —
the assistant reflexively calling the built-in `ScheduleWakeup` tool as
a "fallback heartbeat" after dispatching a subagent or backgrounding a
Bash command, even though `ScheduleWakeup` is scoped by its own tool
description to `/loop` dynamic-mode sessions only — and separately
report the pattern to Anthropic, since it is genuinely harness-owned
and not something this repo's own config can fix.

The user surfaced a concrete instance this session ("plan-architect is
running now... That tool wasn't the right fit here (it's for /loop
sessions) — cancelled it.") and asked for `transcript-analysis` to find
other examples before deciding how to respond. That search confirmed
18 instances across many different session types — `/plan-it`,
`/code-review`, `/ready-for-review`, `/plan-review`, plain chat, and an
eval harness — always the same tool, never `plan-architect`, `Monitor`,
or `CronCreate`. A follow-up Opus consult (`plan-architect`,
`MODE=consult`) then weighed three candidate repo-side mechanisms — a
`PreToolUse` deny gate, a `PostToolUse` advisory nudge, and a
`CLAUDE.md` prose line — and rejected all three: none actually reaches
a first-class harness tool whose own built-in description already
states, verbatim, the exact constraint being violated.

The intended outcome is a `docs/design-decisions.md` §41 entry that
records the decision and its rejected alternatives for future
re-derivation, plus an upstream report (a GitHub issue against
`anthropics/claude-code`, or `SendFeedback` as a fallback) carrying the
full quantitative evidence that the committed entry itself cannot
publish. Nothing in this repo's runtime behavior changes.

## Approach

Record the decision as `docs/design-decisions.md` §41 and report the
behavior upstream; build nothing. The repository change is one
appended numbered entry — no hook, no skill edit, no `CLAUDE.md` line,
no new doc page. The upstream report is a separate act the implementing
session performs directly, and it carries the precise corpus figures
that §41 itself must not.

The entry's job is to make the *declining* durable and re-decidable:
what the misuse is, which three repo-side mechanisms were rejected and
why each fails on its own terms, where the correct alternative already
lives in this repo, and the named conditions that would reopen the
question. `docs/worktree-bash-guard.md` is the shape to follow — it
documents a harness-native behavior this repo cannot intercept, pins
the harness version the observation was made at, and closes with an
explicit statement that authoring the upstream report is not that
doc's job. §41 differs on that last point only: the report is in this
plan's scope, just not as a repository file.

**Root problem.** The model reflexively calls the built-in
`ScheduleWakeup` as a fallback heartbeat after dispatching a subagent
or backgrounding a Bash command, outside the `/loop` dynamic mode the
tool's own description scopes it to; this repo owns no surface that
can prevent it, so the deliverable is a durable record plus an
upstream report rather than a mechanism.

**Givens** (conditions the design treats as fixed because they lie
beyond its reach):

- **G1.** `ScheduleWakeup` is a harness built-in — its description, its
  schema validation, and its availability are Anthropic's to change,
  not this repo's. `[verified: repo-wide grep finds ScheduleWakeup only
  in evals/fixtures/*.jsonl and .claude/plans/handoff-hard-block.md; no
  tool definition or skill file for it exists in this repo]`
- **G2.** `/loop` is harness-native and unowned here — no `SKILL.md`
  for it exists in this repo or on the local filesystem, so this repo
  can neither instrument its entry/exit nor have it write a marker.
  `[verified: Step 3 find; established by the dispatching session]`
- **G3.** The transcript corpus already gathered this session mixes
  private-project and public-repo transcripts, so publishing its
  as-gathered aggregate figures (the instance count, the wall-clock
  range) would inherit the private half under this repo's provenance
  clause. A public-only-scoped re-derivation is technically reachable
  — the same `transcript-analysis` toolkit supports scoping to this
  repo's own transcripts — but is declined as not worth the effort for
  a decision that doesn't depend on the exact numbers; see Out of
  scope. `[engineer-verified]`
- **G4.** The scope is one design-decisions entry plus an upstream
  report — not a mechanism, not ongoing monitoring.
  `[engineer-verified]`

**Assumptions:**

1. §41 is the next free number — §40 is the file's last entry.
   Numbering is append-sequential with no gaps. `[verified: heading
   scan of docs/design-decisions.md; §40 begins at line 589 and the
   file ends at line 673]`
2. A `### Sources` subsection is the current house convention for a new
   entry — 19 of 41 entries carry one, and §37, §38, §39, and §40 all
   do. `[verified: grep count + reads of §37–§40]`
3. A docs-only change of this shape ships without a `CHANGELOG.md`
   entry. §40 — the closest analogue, a decision to decline a mechanism
   with no consumer-visible behavior change — has zero mentions
   anywhere in `CHANGELOG.md`, while §31, §37, and §39 (all of which
   changed behavior) each got one. `[verified: case-insensitive grep
   for "attribution" and "§ ?40" across CHANGELOG.md returns no
   matches; lines 9, 25, 27, 28 carry the others]`
4. `select-tests.py` maps any path under `docs/` to the hooks and
   skills test directories via a deliberate blanket rule, and maps
   `.claude/plans/` to nothing. `[verified: select-tests.py:90-97
   (DOCS_DIR blanket + its rationale comment), :328 (the rule-table
   row), :79-80 (PLANS_DIR)]`
5. No existing test asserts anything a new §41 would disturb.
   `test_doc_counts.py` pins three regexes inside
   `docs/design-decisions.md`, all three scoped to §3's
   reviewer-persona counts. `[verified: test_doc_counts.py:335-361]`
6. `test_doc_has_no_state_path` parametrizes over `docs/**/*.md` and
   fails on a literal `~/.claude/`, `$HOME/.claude/`, or
   `${HOME}/.claude/` prefix on a per-account state path — so §41 must
   write `<config-dir>/...` if it names one at all. `[verified:
   test_skills.py:2827-2837 (the regex), :2939-2947 (the doc
   parametrization)]`
7. Three of the six always-on redaction detectors are live hazards for
   this specific entry: long-hex/UUID (transcript session IDs),
   home-rooted path (`/home/<user>/...` corpus paths), and the
   Slack-channel-shape detector, which also matches a hash-prefixed
   lowercase hyphen-separated markdown anchor link by design. §41 must
   cite sibling entries as plain `§NN` text, exactly as §37–§40 already
   do. `[verified: docs/private-project-redaction.md: 51-58 and
   :64-67]`
8. This repo's established form for an upstream harness citation is a
   resolvable `github.com/anthropics/claude-code/issues/NNNNN` URL —
   §40 cites two, and `docs/case-studies/worktree-enforcement.md` cites
   #34327. Nothing in the repo has ever cited a `SendFeedback`
   submission. `[verified: grep for "anthropics/claude-code/issues"
   across the tree returns 7 files; grep for "SendFeedback" returns
   only eval fixtures]`
9. The consult's "this is not an information gap" claim holds, and this
   repo supplies a second, independent instance of the same correct
   alternative: `handoff/SKILL.md:38` already ships the `ListAgents`
   collect step, `.claude/plans/handoff-hard-block.md:151` already
   rejected `ScheduleWakeup`-based active polling in a shipped
   over-powered-primitive check on the grounds that "polling
   reimplements what the notification path already delivers
   passively," and `test_skills.py:1168-1170` pins the resulting "do
   not poll and do not call TaskOutput" clause. `[verified: all three
   read directly]`
10. That second surface is *not* clean evidence of a visible constraint
    being ignored — `handoff/SKILL.md` loads only when `/handoff` runs,
    and the misfires were observed in `/plan-it`, `/code-review`,
    `/ready-for-review`, `/plan-review`, plain chat, and eval-harness
    sessions. The constraint that was demonstrably loaded at the moment
    of every misfire is `ScheduleWakeup`'s own always-present tool
    description. §41 must state both facts separately and not merge
    them. `[verified: handoff/SKILL.md trigger scope; session-type
    spread established by the dispatching session]`
11. `require-routing-read.sh` exits open on every undecidable state —
    absent `session_id` (:39), a `session_id` that is not a safe path
    component (:51), an unresolvable config dir (:56) — and enforces
    only once the marker file is present (:60), with an in-file comment
    naming this as the opposite default from the bypass-shaped gates.
    This is the exact safety property a `/loop`-absence-keyed gate
    would invert, and §41 should cite it by line rather than by
    paraphrase. `[verified: require-routing-read.sh:33-60]`
12. The misfire follows backgrounded Bash as well as subagent dispatch,
    so the trigger surface is wider than the `Agent` tool.
    `ready-for-review/SKILL.md:178` is this repo's own instance of the
    backgrounded-Bash-plus-completion-notification pattern. `[verified:
    ready-for-review/SKILL.md:178]`
13. `SendFeedback` is available to the implementing session. Not
    checkable from here — the authoring agent holds no such tool.
    `[unverified]`
14. No upstream issue already covers this behavior. Not checkable from
    here — no web access in this dispatch. `[unverified]`
15. `ScheduleWakeup`'s description text quoted in §41 matches what the
    implementing session's harness version actually ships. The
    implementing session holds that text verbatim in its own context
    and must quote from there, not from this plan. `[unverified]`

**Mechanisms:**

| Mechanism | Justification | Anchor |
|---|---|---|
| One appended `docs/design-decisions.md` §41 entry, no new file | The lightest primitive that makes the decision durable and re-decidable. A new `docs/*.md` page would fragment a decision that is one paragraph-class record, and §40 — same shape, a mechanism evaluated and declined — is an entry, not a page. | root |
| §41 pins the harness version the behavior was observed at | The subject is harness-owned text and harness-owned validation, both of which can change without notice; `docs/worktree-bash-guard.md:52` pins 2.1.238 for exactly this reason, and its later non-reproduction finding is what makes the pin load-bearing rather than decorative. | root; row 15 |
| §41 quotes `ScheduleWakeup`'s scoping line and its "Never fabricate or predict a pending agent's results" line verbatim from the implementing session's own tool description | `plan-it` Step 5's external-pattern-grounding rule: a paraphrase of the constraint would crystallize a reading the source may not support, and the second line is the whole basis for the consult's severity correction. | row 15 |
| §41 states the corpus evidence qualitatively — pattern, session-type spread, both cost sub-modes, self-caught outcome — with no counts, no durations, and no session IDs | G3's provenance clause, plus row 7's long-hex detector, which would deny the commit on a pasted session ID regardless. | G3; row 7 |
| §41 carries a **Revisit** paragraph naming the three reopening conditions from the consult | §40's own closing paragraph is the precedent, and it is what keeps a declined option recoverable instead of merely absent. | root |
| §41 names where the correct alternative already lives in this repo (`handoff/SKILL.md:38`, the `ListAgents` collect step) as a locator, kept separate from the argument that the constraint was already loaded | Rows 9 and 10: the two facts support different claims, and merging them would overstate the evidence into "a repo rule was ignored," which is not what happened. | row 9; row 10 |
| The upstream report is a direct act of the implementing session, not a repository file | It carries the figures §41 cannot (G3), and it is the only lever that reaches G1/G2. | G1; G2; G3 |
| The report is filed where it yields a citable URL, with `SendFeedback` as the fallback channel | Row 8: every prior upstream citation in this repo resolves to a URL a reader can open. A `SendFeedback` submission produces no such artifact, so §41's Sources would have to say "reported via the in-product feedback channel on `<date>`, no citable URL" — honest, but strictly weaker than what four existing citations achieve. | row 8; row 13 |

**Over-powered-primitive check.** The chosen mechanism is already the
lightest available — an appended prose record with no runtime path, no
tool-call boundary, and no execution context — so the check runs in
the informative direction: three *heavier* primitives were enumerated
and each rejected on its own grounds. A `PreToolUse` gate on
`ScheduleWakeup` inverts row 11's safety property, since no marker for
`/loop` can exist under G2 and the only candidate signals are a
sentinel shape that misses the arbitrary-prompt case by construction
and an undocumented transcript format that answers "did this session
ever run `/loop`" rather than "is this call legitimate now." A
`PostToolUse` advisory via `hookSpecificOutput.additionalContext`
re-delivers a message already present verbatim in the tool's own
description at the moment of the misfire, and would fire on every turn
of a genuine `/loop` session — noise aimed at the exact population the
tool exists for — while forfeiting the mechanical-non-negotiability
property §1 requires to justify hook complexity. A `CLAUDE.md` line is
worse than both: it restates harness-owned text this repo cannot keep
in sync, and spends the budget `check-claude-md-length.sh` polices.

**Dispatch split.** One phase, written inline by the session that holds
this plan — not delegated to `code-writer`. The entry's accuracy
depends on three things that live only in the dispatching session's
context: the corpus findings, the consult's returned text, and
`ScheduleWakeup`'s live tool description (row 15). Restating all three
in a dispatch prompt is `plan-it` Step 5's own named do-not-split
condition, and it would cost more than writing four paragraphs.

**Decided during plan-review of this plan:** the companion option of
adding a one-line anti-polling reminder naming `ScheduleWakeup` to
`subagent-delegation/SKILL.md` was raised and declined by the user in
favor of the recommendation above — see the corresponding Out of scope
bullet.

## Critical files

- **`docs/design-decisions.md`** — append `## 41. <title> (2026-09-01)`
  after §40's last Sources bullet at line 673, with a blank line
  between them. Re-run a `^## [0-9]` heading scan first and renumber if
  another branch landed §41 in the interim (assumption 1). Content
  contract, following §40's paragraph density:
  - What the misuse is, with `ScheduleWakeup`'s scoping line quoted
    verbatim, plus the harness version observed (mechanisms 2 and 3).
  - The corpus evidence, qualitative only — the session types it
    spans, that it was never seen inside an actual `/loop` session, the
    two cost sub-modes (a malformed call rejected instantly by schema
    validation, and a well-formed call that schedules and later
    fires), and that every observed instance was self-caught. No
    counts, no durations, no session IDs (mechanism 4).
  - The severity note: the tool's own "Never fabricate or predict a
    pending agent's results" line names the latent failure this misuse
    courts, which the clean-cancellation outcome does not retire.
  - The three rejected repo-side mechanisms, each with its own failure
    reason, citing `require-routing-read.sh:33-60` by line for the
    fail-open property a `/loop` gate would invert (assumption 11),
    `consume-durable-continuity-file-on-read.sh` as the advisory-nudge
    precedent that was declined here, and §1 for the
    advisory-versus-hook tier.
  - Where the correct alternative already lives —
    `handoff/SKILL.md:38`'s `ListAgents` collect step — with the
    load-scope caveat kept as its own sentence (mechanism 6).
  - A **Revisit** paragraph: a `PreToolUse` field exposing the
    harness's own computed active-mode state (or `ScheduleWakeup`
    rejecting outside `/loop` outright, which moots this); one
    observed non-self-healing instance; or evidence of a materially
    higher well-formed-call rate on large-context sessions, which would
    make each misfire a real `docs/cost-ledger.md` line item.
  - `### Sources`: the upstream report (URL if one exists, otherwise
    the channel and date per mechanism 8), `docs/worktree-bash-guard.md`
    for the harness-bug-documentation precedent,
    `claude/.claude/hooks/require-routing-read.sh` for the
    marker-as-predicate contrast, §25 for the observer-over-hook
    precedent, and this plan file for the full ledger.
  - **Reuse:** §40's Revisit-paragraph shape; §39's and §40's plain
    `§NN` cross-reference style (assumption 7 — no markdown anchor
    links); §37's and §39's "full assumption ledger lives in the plan
    file" Sources bullet.
- **`.claude/plans/harness-context-mismatched-tool-dispatch.md`** —
  this plan, committed per `plan-it` Step 7 since Critical files names
  a file. Subject to the same redaction constraints as §41: it ships
  in the same PR (repo `CLAUDE.md`, "Plans in this repo affect all
  stow users").

**Not a repository file:** the upstream report. The implementing
session searches `anthropics/claude-code` issues first (assumption
14), then either files there or submits via `SendFeedback` (assumption
13). This is the one place the precise figures belong — counts, the
wall-clock range, the session-type spread, both cost sub-modes. Even
there, send aggregates only: no session IDs, no project or repo names,
no filesystem paths, no verbatim prompt text. G3 permits the figures;
it does not permit the raw material they were derived from. Sequence
the report *before* writing §41 so its Sources bullet can carry the
resulting URL in the same commit.

## Verification

- `.venv/bin/python3 claude/.claude/scripts/select-tests.py` — the
  repo's documented scoped command. Expect it to select the hooks and
  skills test directories via the `docs/` blanket rule; the plan file
  under `.claude/plans/` contributes no additional tests (assumption
  4). Two assertions specifically confirm this change is inert:
  `test_doc_counts.py`'s three design-decisions regexes still match
  (assumption 5), and `test_doc_has_no_state_path` passes for
  `docs/design-decisions.md` (assumption 6).
- Redaction pre-flight before staging — read the added block back and
  confirm it contains no 32+ character hex run or UUID shape, no
  `/home/<user>/` or `/Users/<user>/` path, no hash-prefixed
  lowercase-hyphenated markdown anchor, and no aggregate count or
  duration from the corpus (assumptions 3 and 7, G3).
  `deny-private-project-refs.sh` enforces
  the first three at commit time; the fourth is reviewer discipline
  only and nothing will catch it.
- Confirm the quoted `ScheduleWakeup` lines are copied from the
  implementing session's own live tool description and that the
  harness version named in §41 is that session's actual version, not
  one carried over from this plan (assumption 15).
- `/code-review` before the commit, per the repo's review pipeline.
  `/plan-review` on this plan before it is presented, per `plan-it`
  Step 6.
- Confirm the upstream report was actually submitted, and that §41's
  Sources bullet describes the channel accurately — a URL if one
  exists, an explicit "no citable URL" if not (mechanism 8).

## Out of scope

- **A retrospective `transcript-analysis` detector for this pattern.**
  Declined for now, recorded here rather than built. The consult ranked
  it "below the core recommendation and not required to close this
  out," and the ask was to stop the recurrence and document plus report
  it, not to add ongoing monitoring — a retrospective detector counts
  instances, it prevents none. Building it now would also invert §25's
  own logic: §25 widened an *existing* observer to close a *confirmed*
  gap with a confirmed incident behind it, whereas here every observed
  instance self-corrected with no user intervention and no incorrect
  deliverable. The detector's value is conditional on precisely the
  reopening conditions §41's Revisit paragraph names, so recording
  those conditions is what makes it recoverable. Cost is not trivial
  either — a new subcommand in the decomposed `transcript-analysis`
  package, its tests, and a `docs/transcript-analysis.md` section, on a
  code surface the plan otherwise never touches.
- **A `PreToolUse` gate, a `PostToolUse` advisory nudge, and a
  `CLAUDE.md` line.** All three rejected in the Approach's
  over-powered-primitive check. They are named in §41 rather than
  merely omitted, because a future session will otherwise re-derive
  them.
- **Promoting the "don't poll — let the notification arrive, or call
  `ListAgents`" fact from `handoff/SKILL.md` into
  `subagent-delegation/SKILL.md`.** Considered and declined by the
  user directly: unlike `ScheduleWakeup`'s harness-owned description,
  this is a repo-owned fact this repo has already verified, and
  `subagent-delegation` triggers on "delegating implementation," so it
  would plausibly be in context at the exact decision point the
  misfire follows — but whether that skill was actually loaded in any
  observed instance was never checked (assumption 10), the misfire
  also follows backgrounded Bash where the skill's trigger conditions
  do not obviously apply (assumption 12), and a `SKILL.md` edit pulls
  hook-enforced `/skill-review` and the per-file-type review dispatch
  into what is otherwise a prose-only PR.
- **A `CHANGELOG.md` entry.** Nothing consumer-visible changes; §40
  shipped the same way (assumption 3).
- **A new `docs/*.md` page for this behavior.** §41 is the right
  altitude; `docs/worktree-bash-guard.md` earned a page because it
  carries a five-row trigger taxonomy, a ten-site sweep, and a
  re-verification procedure, none of which has an analogue here.
- **Publishing the corpus counts or durations anywhere in the repo**,
  and **re-deriving them from a public-only corpus to make them
  publishable.** The first is barred by G3; the second is real work
  with no consumer — §41's argument does not depend on the exact
  numbers, and the report that does carry them is not a repository
  file.
