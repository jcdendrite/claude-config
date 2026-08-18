# Delegate the instrument along with the objective

## Context

**Goal.** Close the gap that lets a parent session author an analysis
instrument inline while delegating only that instrument's execution — and
decide, from this repo's measured transcript corpus rather than from a single
trace, whether that gap warrants a rule in `subagent-delegation`.

**Problem.** A parent session resuming an approved plan judged that it needed a
~210-line Python utility to reconstruct a hook's threshold logic across session
transcripts. It announced the split explicitly: write the script itself, then
delegate the broad multi-account execution and per-session inspection to a
subagent, on the stated ground that a subagent would "re-derive" the
threshold/window logic instead of faithfully porting it. It then ran the script
itself and followed with four further inline heredoc probes and several
`git log` bursts toward the same question.

Two distinct reasoning gaps produced that:

1. **A single objective was split, and the parent kept the expensive half.**
   "Answer question X" and "build the tooling X needs" are one objective.
   `subagent-delegation` Step 1 already says to dispatch the objective rather
   than the individual command, but every branch of the skill is framed around
   *reading* — commands, discovery sweeps, probes — or around *editing code
   that already exists*. Nothing addresses the prior decision of who *writes* a
   new instrument, so the split reads as permitted.
2. **A fidelity argument served as an unrebutted escape hatch.** "A subagent
   would re-derive it independently" sounds like engineering judgment and
   dissolves on contact: naming the source path in the dispatch makes the
   subagent read the original rather than reinvent it. Because no rule names
   this class of reasoning, any new routing rule can be argued around the same
   way.

**Why now.** `subagent-delegation` is the file `CLAUDE.md` §Working Style
routes every delegation decision to. The gap is in the one place a session
looks when deciding this question.

**Intended outcome.** A measurement subcommand that makes inline
instrument-authoring visible in the corpus, and — conditional on what it shows
— either a rule in `subagent-delegation` or a recorded rejection in the
cost-lever register.

## Approach

**The concluded design.** Add one `transcript-analysis.py` subcommand that
censuses main-thread script-authoring (substantial `Bash` heredoc/`-c` payloads
and `Write` calls to scratchpad paths), size-bucketed, correlated against the
same session's subagent-dispatch count. Then act on what it measures: if
inline authoring at meaningful size is common, add a short rule to
`subagent-delegation` stating that the instrument an objective requires is part
of that objective, routing it to `general-purpose`, and rebutting the fidelity
excuse — and narrow the frontmatter carve-out that currently licenses the
behavior. If it is rare, add no prose and record the lever as
measured-and-rejected instead.

This follows `read-scope` (#609) exactly: instrument and resulting rule in one
PR, with the measurement sizing the rule rather than decorating a decision
already made. That precedent also shows the null-ish branch is real — its
census found 45.6% of reads already targeted, which shrank the rule to one line
and killed a proposed hook.

### Assumption ledger

**Root problem.** A parent session can author a disposable instrument inline
because no rule treats instrument-authoring as part of the objective the
instrument serves, and the fidelity-of-port argument for keeping it inline is
never rebutted.

**Givens** — conditions this design treats as fixed:

- **The transcript JSONL schema (`isSidechain`, `tool_use.input`) is Claude
  Code's, not this repo's.** The census reads what the harness emits; the
  emitted shape is a platform boundary no change inside this repo reaches.

Two conditions that might read as givens are not, because this repo owns the
artifacts that set them — both are declined deliberately and recorded under
**Out of scope**: hook-based enforcement of delegation
(`claude/.claude/hooks/`) and the 200-line skill cap
(`claude/.claude/hooks/check-skill-length.sh`).

**Mechanisms:**

- **M1 — one new subcommand, not an extension of an existing one**
  (`anchors: root`). Lighter primitives rejected: (a) *extend `edit-format`* —
  it already counts `Write` characters, but its charter is edit-call
  mechanics and failure classification; adding `Bash`-content classification
  and spawn correlation changes what the subcommand *is*, and its existing
  output contract would have to grow two unrelated axes. (b) *extend
  `subagent-mix`* — it already counts `Agent`/`Task` spawns per session, but it
  measures dispatches that happened and has no notion of work that should have
  been dispatched; folding in a negative-space metric would make its numbers
  mean two different things. A new subcommand matches how `read-scope` and
  `context-composition` were each added for a new question.
- **M2 — size-bucketed histogram, not a count or a fixed threshold**
  (`anchors: row A8`). A one-line `python3 -c` date conversion and a 210-line
  reconstruction script are the same event under a count metric. Reporting a
  size distribution lets the data set the boundary instead of this plan
  inventing a numeric literal.
- **M3 — a single per-session scan accumulating both axes** (`anchors: row A4`).
  The main-thread predicate, the `tool_use.input` unwrap, and the spawn loop
  all read the same `records` iterable from one `session_iter` pass.
- **M4 — route instrument-authoring to `general-purpose`, not `code-writer`**
  (`anchors: row A2`). Prose-only mechanism; the lighter option (say
  nothing and rely on the existing "dispatch the objective" sentence) is what
  already failed in the observed trace.

  **Reconciles with `CLAUDE.md` §Model & Effort Routing.** That bullet routes
  "feature code, fixes, refactors, migrations, schema, **scripts**" to
  `code-writer` — "scripts" unqualified — which a literal reading puts in
  conflict with M4. The resolution, which Phase 2A states rather than leaves
  implicit: every list-mate of "scripts" there is a diff-bound deliverable, so
  "scripts" means shipped scripts, and a disposable instrument that never
  enters a diff falls outside that bullet.
- **M5 — narrow the frontmatter carve-out rather than only appending a section**
  (`anchors: row A1`). Appending alone leaves the description contradicting the
  body.
- **M6 — a non-blocking `PreToolUse` nudge reusing the same classifier**
  (`anchors: root`, `anchors: row A10`). Lighter primitives rejected:
  (a) *prose alone* — the trace shows two existing delegation rules failing to
  bind in one session, so prose is demonstrably insufficient on its own here;
  (b) *`PostToolUse` instead* — cheaper to write, but fires after the payload
  is already in context, which is after the decision it exists to inform. The
  heavier primitive in this family — a **blocking** gate — is declined in Out
  of scope, because denial is what would force an intent judgment the shape
  classifier cannot make.

**Assumption rows:**

| # | Assumption | Tag |
|---|---|---|
| A1 | `subagent-delegation` has no rule on authoring a new instrument, and its `DO NOT TRIGGER` clause ("Edit/Write sequences where scope or content is still forming") plus body line 57-59 arguably license it. | `[verified: claude/.claude/skills/subagent-delegation/SKILL.md:8-11, 56-59]` |
| A2 | `code-writer` is diff-shaped throughout — "self-reviews its own diff", "the parent owns git", a reviewer table keyed on repo domains — so a disposable scratchpad artifact fits none of its machinery. | `[verified: claude/.claude/agents/code-writer.md:3, 21-22, 76, 105-114]` |
| A3 | `docs/cost-levers-considered.md:86` rejects a write-capable *debug-and-fix* agent, on the narrower ground that an agent editing files while looking at a failing check will try to fix it. That is an agency-during-debugging failure, **not** a general claim against write-capable agents for one-off authoring — it is adjacent context, and M4 does not rest on it. A2 carries M4 alone. | `[verified: docs/cost-levers-considered.md:86]` |
| A4 | `Bash` command text, `Write` inputs, the main-thread predicate, and `Agent`/`Task` spawn counts are all reachable from one per-session pass — **provided the scope call passes `include_subagents=True`**. `cmd_subagent_mix` is not the precedent to copy here: its `_resolve_project_scope` call at `:3045` omits that flag, so its `isSidechain` check at `:3096` never sees a subagent record. Copy `read-scope` (`:7032`) or `edit-format` (`:6502`) instead, both of which pass it. | `[verified: transcript-analysis.py:155-158, 1264, 3045, 3095-3120, 6502, 6405-6420, 7032]` |
| A5 | `subagent-delegation/SKILL.md` is 174 lines against a hook-enforced 200-line cap, leaving ~25 lines of headroom. | `[verified: claude/.claude/hooks/check-skill-length.sh:67-70; wc -l on the skill]` |
| A6 | The engineer diagnosed the trace as a main agent doing work it should have delegated, chose to fix the split-objective and fidelity-excuse gaps as one rule, chose measure-before-ruling, and chose to record a rejected lever if the rate is low. | `[engineer-verified]` |
| A7 | Inline instrument-authoring is frequent enough, or costly enough, to justify prose in a skill already carrying four delegation rules. | `[unverified]` — this is exactly what M1 resolves; the rule ships only if the measurement supports it. |
| A8 | Script-authoring shapes are separable from benign one-liners by authored-content size. | `[unverified]` — M2's histogram tests this. If the distribution has no knee, the metric is weak and that outcome is reportable rather than fatal. |
| A10 | A non-blocking `PreToolUse` nudge is worth its fire-rate cost at an instrument-scale size threshold. | `[unverified]` — Phase 1's histogram both sets the threshold and can falsify this; Phase 2C does not ship if the population is large enough that a per-fire advisory line is itself a material context cost. |
| A9 | No existing subcommand can see this failure mode: a session that authored inline with zero dispatches is indistinguishable, under `subagent-mix`, from one that never needed a dispatch. | `[verified: survey of all 29 registered subcommands; no `Bash`-content classification exists — `transcript-analysis.py:155, 250, 4298, 4370, 9504` classify only whether a call was `Bash`]` |

### Detection design

Classify a main-thread `tool_use` block as **inline authoring** when either:

- `name == "Bash"` and `input.command` matches a script-authoring shape, with
  the authored payload measured as the heredoc body or the inline-program
  argument, **not** the whole command line. The shape set is explicit:
  - Heredocs: `<<EOF`, `<<'EOF'`, and `<<-EOF` / `<<-'EOF'` (tab-stripped).
    The extractor must match the *actual* delimiter token and scan to its
    matching terminator, so a body containing the delimiter word as data does
    not truncate the payload early.
  - Inline programs: `python3 -c`, `python -c`, `node -e`, `perl -e`,
    `ruby -e`, `sh -c`, `bash -c`. `sh -c` / `bash -c` are included
    deliberately — they are the most common inline-script shape and omitting
    them would be a systematic false negative.
  - **`-c` is overloaded** (`curl -c`, `tar -c`, `ssh -c`, `mysql -c`), so the
    flag is only meaningful bound to a known interpreter argv[0]. Match
    `<interpreter> -c`, never a bare `-c` anywhere in the string.
  - A command chaining several heredocs or inline programs (`&&`, `;`, `|`)
    **sums** their payloads; that is one authoring act split across
    invocations, which is the behavior being measured.
- `name == "Write"` and `input.file_path` is under a scratchpad/temp path, with
  `len(input.content)` as the payload size. The matcher must accept the
  symlink-resolved form as well as the literal one — on macOS `/tmp` resolves
  through a symlink to `/private/tmp`, and agent scratchpad paths appear in
  transcripts in the resolved form, so a literal `/tmp/` prefix check would
  silently miss real calls. The predicate is: the path's first component is a
  temp root (`/tmp` or `/private/tmp`) **or** the path contains a `scratchpad`
  component. Session-UUID path segments are never matched on, so the rule does
  not depend on the harness's current directory layout.

**Unparseable tool input gets its own reported cohort.** A `tool_use.input` can
arrive as `{"__unparsedToolInput": "..."}` with none of the expected fields, so
`input.command` and `input.file_path` are not unconditionally present.
`_classify_read_call` (`:6655-6672`) already handles this for `read-scope` by
giving it a named cohort rather than letting it vanish. This subcommand does the
same: an unparsed-input block is counted and reported separately, never silently
filed as "not authoring." Left unreported it would shrink the denominator of a
census feeding a go/no-go decision.

**Scope call.** `_resolve_project_scope(..., include_subagents=True)`, matching
`read-scope` (`:7032`) and `edit-format` (`:6502`). Without the flag there are
no subagent records to attribute and the main-vs-subagent split is vacuous.

Bucket payload size and report the census split by main-thread vs subagent
scope. Sessions are then aggregated into two cohorts — **zero main-thread
dispatches** and **at least one** — and the report emits authored-payload mass
per cohort. It does **not** emit per-session rows: the go/no-go rule needs only
the two cohort totals, and aggregate-only output keeps this subcommand's
disclosure posture identical to its two siblings rather than introducing a new
one.

**Output invariant, stated as a contract.** The report's content never includes
raw command text, file content, file paths, or session identifiers — only size
buckets, counts, and cohort totals. This matches the docstring invariant
`_edit_format_report` (`:6477-6481`) and `_read_scope_report` (`:7003-7008`)
each already carry, and it must be written into the new handler's docstring in
the same form. Because output carries no project-identifying content, the
subcommand needs no session-redact map and no `--no-redact` /
`_DO_NOT_PUBLISH_BANNER` gate — the same reasoning by which its two siblings
carry the invariant instead of the gate.

**Known limitation, reported rather than hidden:** the classifier sees shape and
size, never whether delegation was *appropriate*. A large authored payload in a
session that also dispatched heavily may be entirely correct. The census
therefore bounds the population; it does not adjudicate individual sessions.

### The go/no-go rule, fixed before the run

The decision rule is set here, not after seeing the numbers, and it invents no
threshold: **compare authored-payload mass in zero-dispatch main-thread
sessions against the same figure in sessions that dispatched at least once.**

- If inline authoring mass is concentrated in zero-dispatch sessions, the
  behavior is a delegation failure and **Phase 2A** ships the rule.
- If authoring mass is distributed roughly evenly across both cohorts, inline
  authoring is not tracking delegation behavior at all — it is just how
  sessions work — and **Phase 2B** records the rejected lever instead.

This is falsifiable from the corpus itself and needs no invented percentage,
which is why it replaces a bare "large enough to have warranted a dispatch"
judgement call made at execution time.

### Phase 2A — the rule, drafted here so it is not authored at execution time

Appended to `subagent-delegation/SKILL.md` after the `code-writer` section:

```markdown
### Instruments the objective needs → dispatch with the objective

A question you need tooling to answer is still one objective. When about to
build a script, query, or harness whose only purpose is to produce an answer,
dispatch the whole thing — "answer X; build whatever tooling you need" — to
`general-purpose`. Authoring the instrument inline and delegating only its
execution keeps the expensive half. Not `code-writer`: the artifact is
disposable and never enters a diff, so its diff-shaped self-review has
nothing to act on.

Fidelity is not a reason to keep it inline. If the instrument must reproduce
existing logic exactly, name the source path in the dispatch — a subagent
reading the original is not re-deriving it.

Instruments, not deliverables: a script you promote into the repo becomes a
deliverable and follows **Implementation work → `code-writer`** above.
```

Three coherence edits so the file stops contradicting itself (M5):

- Frontmatter `TRIGGER`: add `about to write a script or query to answer a
  question`. Without it the body would cover instrument-authoring while the
  always-loaded description never names the situation — and the existing
  `delegating implementation` clause is precisely the framing the observed
  session did not apply to a scratchpad script.
- Frontmatter `DO NOT TRIGGER`: `Edit/Write sequences where scope or content
  is still forming` → `Edit/Write of deliverable code where scope or content
  is still forming`. This is a deliberate narrowing, not a copy edit — the
  blanket "still-forming edits stay inline" instruction is replaced by the
  body section above, and the PR body must say so.
- Body carve-out (lines 56-59): append `— an instrument built only to answer a
  question follows the instrument rule below.`

**Line budget:** the block above is 16 lines including its leading blank
separator; the carve-out edit is net-zero and the frontmatter gains one line.
174 + 17 = 191, under the 200-line cap with 9 lines of headroom. Verify with
`wc -l` after the edit — if it lands over 200, trim this block rather than
extracting an auxiliary file (`docs/skills.md` §Skill architecture notes:
"Shorten first, do not extract").

### Phase 2C — warn-only hook, reusing the Phase 1 classifier

Prose teaches the judgment; it does not fire at the moment the judgment is
made. The observed trace shows two delegation rules already on the books
failing to bind in one session, so a rule alone is not expected to suffice.
Phase 1 builds a classifier for exactly this shape — Phase 2C reuses it at the
decision point rather than leaving it measurement-only.

**Design:**

- **Event:** `PreToolUse`, matcher on `Bash` and `Write` only. Not
  `PostToolUse`: after the fact the payload is already in context and the
  nudge cannot change the decision it exists to inform.
- **Non-blocking.** The hook emits advisory context and permits the call. It
  never denies. This is why it is separable from the blocking gate declined in
  Out of scope: no intent judgment is load-bearing, because a false positive
  costs one advisory line rather than a denied tool call.
- **Fire condition:** the Phase 1 classifier's authoring shape, above a
  payload-size threshold **taken from Phase 1's measured distribution**, not
  invented here — whichever bucket boundary the histogram shows separates
  one-liners from instruments.
- **Defense in depth:** the script filters its own input by tool name and
  matcher rather than relying on the `settings.json` `if` condition, per the
  repo `CLAUDE.md`.
- **`# hook-class: informational`.** The label tracks hardening posture —
  cannot deny — not the event it fires on, so a never-denying `PreToolUse`
  hook takes it even though the class description enumerates `PostToolUse` /
  `SessionStart`. Consequence to plan for: `GATE_HOOKS` drives the
  auto-parametrized behavior tests in `test_hook_alignment.py` and excludes
  non-gate hooks, so this hook needs its own hand-written coverage rather than
  inheriting that suite.
- **Fail-open, which means *not* the canonical skeleton.** Gate hooks use
  `_lib_parse_tool_input_or_deny`, which denies on parse failure. This hook
  must not: it parses defensively and falls through to silence on any failure.
  Deviating from the canonical helper is deliberate here and the script header
  must say so, since the helper is otherwise the required pattern.
- **Emits no command content.** The advisory text names the shape and the size
  bucket, never any fragment of the command or payload — the same output
  invariant the subcommand carries, for the same reason.
- **`settings.json` path prefix:** `~/.claude/hooks/...`, matching every other
  entry in the stowed user-scope settings file.

**Load-bearing unknown — verify before building.** The documented `PreToolUse`
output contract in the hook-review reference covers `permissionDecision: deny`.
It does not establish a channel by which a *permitting* `PreToolUse` hook
surfaces advisory text to the model, and the one adjacent fact it does give
cuts the wrong way: unparseable stdout on exit 0 is read as *no decision*, which
allows the call but says nothing about the text reaching the model. **Phase 2C's
entire mechanism rests on that channel existing.** Before any implementation,
confirm against the primary Anthropic hooks reference (per `verify-sources`)
whether a non-blocking `PreToolUse` hook can inject context. If it cannot,
Phase 2C does not ship in this form — the fallback is to re-evaluate
`PostToolUse` accepting that it fires after the payload has landed, which is a
materially weaker mechanism and a decision to bring back rather than assume.

No hook in this repo currently injects advisory context from `PreToolUse` —
the nearest analogs (`nudge-worktree-anchor.sh`, `check-branch-divergence.sh`)
fire on `UserPromptSubmit`, a different event with a different contract, so
neither establishes the channel. Verify the same pre-implementation pass a
second, structurally identical unknown: **what the harness does when the hook
process itself times out** — whether that path silently allows (consistent with
this hook's intent) or surfaces an error. Both answers must come from the
primary reference, not from inference.

**The fire-rate objection, answered.** `context-composition-analyzer.md`
rejected a `PostToolUse` ledger hook partly because advisory context on tens of
thousands of calls spends context to save context. That objection binds on fire
*rate*, which is what the size threshold controls: it fires only on
instrument-scale payloads, a population Phase 1 will have already counted. **If
Phase 1 measures that population as large enough that a per-fire advisory line
is itself a material context cost, Phase 2C does not ship** — the same
measurement that sets the threshold can also falsify the mechanism.

**Scan cap — the hook answers a boolean, the census measures a size.** Phase 1's
classifier deliberately scans a heredoc body to its true terminator without
truncating, because the histogram needs real sizes. The hook must not inherit
that: a heredoc body is user- and model-controlled and unbounded, an in-process
bash scan of it has no complexity bound, and `_lib`'s 5s `timeout` wraps only
*external* commands — it cannot bound an in-process scan. The hook needs only
"is this payload above the fire threshold," so it **caps its scan at threshold
plus a small margin**: reaching the cap already proves the payload is above
threshold, so the boolean is exact even though the size is not. Below the cap
both implementations agree exactly, which is the range the shared fixture
covers; above it they differ by design and the fixture records that.

**Shape matching is pure bash** — `[[ =~ ]]` / `case`, no `grep` subprocess per
candidate shape. With seven interpreter forms, shelling out per shape would add
roughly eight process spawns per fire on top of the ~2-spawn floor every hook
already pays.

**Keeping the two classifiers in agreement.** The hook is shell and the
subcommand is Python, so a shared import is not available, and making the hook
shell out to Python on every `Bash` call would put an interpreter start in
front of every command — not acceptable for a nudge. The shape list is
therefore implemented twice, which the repo `CLAUDE.md` treats as a defect
absent a named exception. The exception is named here, and paid for
mechanically: **one fixture file of example command strings, each labelled with
its expected verdict, read by both test suites.** A shape added to one
implementation and not the other fails the other's test. Duplication of the
matching code is accepted; divergence of the matching *behavior* is not, and
the fixture is what makes that distinction enforceable rather than aspirational.

### The second failure in the trace, named rather than absorbed

The trace contains two failures and Phase 2A addresses one. Its fidelity
paragraph directly rebuts the session's stated reason for the split. It says
nothing about what followed: four more inline heredoc probes and several
`git log` bursts toward the same question, after the instrument had already
run. That behavior is already governed by Step 1's existing "second or third
`Bash` command toward the same question" operational trigger — a rule more
directly on point than anything this plan adds, which also failed to bind in
the same session.

This plan does not claim to fix it. Two consequences, which the case study
carries rather than reporting a clean win:

- Phase 2C covers it partially by accident of mechanism, not by design: each
  subsequent large authoring payload re-fires the nudge. Repeat probing below
  the size threshold is untouched.
- Phase 1's census counts authoring calls, not repeat-probing toward a single
  question. It cannot measure this failure mode, and the case study must say so
  rather than let a reader infer coverage from an adjacent number.

### Phase 2B — the rejected-lever entry, if the measurement says no

A new section in `docs/cost-levers-considered.md`, matching the existing
`## From <plan>.md — "<title>"` + `| Lever | Verdict | Measured reason |`
shape, recording the lever as measured-and-rejected with the two cohort
figures as the reason. No skill edit ships in this branch.

## Critical files

**Create:**

- `docs/case-studies/delegate-instrument-authoring.md` — measurement, honest
  limits, and revisit triggers. Modeled on
  `docs/case-studies/targeted-read-discipline.md`.

  **Sourcing constraint:** every figure and example in this case study comes
  from the subcommand's own stdout, which carries no raw content by the output
  invariant above. Do not open transcript JSONL directly to pull an
  illustrative snippet. `deny-private-project-refs.sh` is a structural and
  blocklist matcher that explicitly disclaims general secret-scanning, so it
  would not reliably catch a credential or internal hostname lifted out of a
  raw payload into a public commit — the minimized report is the real control,
  and reading around it defeats it.

- `claude/.claude/hooks/nudge-inline-instrument-authoring.sh` — **Phase 2C
  only, and Phase 2C is gated on three conditions all resolving first:** the
  size threshold from Phase 1's histogram, Phase 1's population verdict (§A10
  can falsify the mechanism), and the unverified `PreToolUse` advisory-channel
  question below. Do not create or register this file until all three clear.
- `claude/.claude/hooks/tests/test_nudge_inline_instrument_authoring.py` —
  same gating. Fires above threshold, silent below it, silent on
  non-`Bash`/`Write` tools, permits (never denies) in every case, fails open on
  malformed input, and terminates within the scan cap on a pathologically large
  payload.
- `claude/.claude/hooks/tests/fixtures/instrument_authoring_shapes.*` — the
  labelled example commands both test suites read.

**Modify:**

- `claude/.claude/settings.json` — one `PreToolUse` hook entry, same Phase 2C
  gating. **Disclosed cost:** the detection shape is not expressible as the
  prefix-glob the existing `if` fields use, so this entry is unconditional on
  both matchers — taking `Bash` from 10 unconditional hooks to 11 and
  `Edit|Write|MultiEdit` from 7 to 8, for every stow user. The spawn-and-scan
  cost is paid on every ordinary `git log`, `pytest`, and routine edit, whether
  or not the nudge ever fires, and Phase 1's histogram measures the *emit*
  population only — it cannot falsify this always-on axis. Before registering
  the entry, measure actual per-fire wall time and confirm it holds the
  sub-100ms hook budget; if it does not, Phase 2C does not ship.
- `claude/.claude/scripts/transcript-analysis.py` — new `cmd_*` handler,
  `_*_report`, `_scan_*_session`, `_print_*_report`, plus argparse
  registration near the existing `edit-format` / `read-scope` block
  (10854-10915).
- `claude/.claude/scripts/tests/test_transcript_analysis.py` — new `Test*`
  class beside `TestReadScope` (8842) and a `TestScan*Session` class beside
  `TestScanReadScopeSession` (9048).
- `claude/.claude/skills/subagent-delegation/SKILL.md` — Phase 2A only. The
  exact text and the two coherence edits are drafted above.
- `docs/cost-levers-considered.md` — a new `## From ...` section in both
  branches; the verdict row records adoption (2A) or rejection (2B).
- `docs/case-studies.md` — one index line.
- `CHANGELOG.md` — one `[Unreleased]` entry.

**Reuse — do not reimplement:**

| Need | Existing helper |
|---|---|
| Scan roots | `_resolve_cost_roots(args, subcommand)` — `:5272` |
| Session iterator from `--projects`/`--this-repo` | `_resolve_project_scope(...)` — `:2629` |
| Shared scope flags on a subparser | `_add_project_scope_args(parser)` — `:10432` |
| Scope header | `_print_resolved_scope(...)` — `:2758` |
| Percentages | `_pct_of` / `_pct_value` — `:5218`, `:5223` |
| `--since Nd` parsing | `_parse_since_nd_arg(args, subcommand)` — `:348` |
| Spawn tool names | `_SPAWN_TOOL_NAMES` — `:1264` |
| Test record builders | `_bash_use`, `_asst`, `_agent_use`, `_write_jsonl` — `tests/conftest.py:60, 31, 68, 24` (`_bash_use` already exists) |
| Test isolation | `fake_projects` fixture — `tests/conftest.py:254-265` |

## Verification

Run from this worktree (the `.venv` lives only at the main worktree root):

```bash
../../../.venv/bin/pytest claude/.claude/scripts/tests/test_transcript_analysis.py
../../../.venv/bin/ruff check claude/.claude/
```

Then the full suite and shell lint per the repo `CLAUDE.md`:

```bash
../../../.venv/bin/pytest claude/.claude/
scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck
```

**Unit coverage** (synthetic JSONL, matching the `TestReadScope` convention):

1. A heredoc `Bash` call on the main thread is counted, with payload size
   measured as the heredoc body, not the full command string.
2. A `python3 -c` one-liner is counted but lands in the smallest size bucket —
   pins M2's size-weighting.
3. The same authoring shapes on a **subagent** record (`isSidechain: true`) are
   attributed to the subagent cohort, not the main thread. Requires the fixture
   to exercise the merged scope, so this test also pins `include_subagents=True`.
4. A `Write` to a scratchpad path is counted; a `Write` to a repo path is not.
   Covers both `/tmp/...` and `/private/tmp/...` forms.
5. Per-session spawn counts come from both `Agent` and `Task` tool names.
6. **Cohort arithmetic across multiple sessions** — the load-bearing test. A
   fixture of two zero-dispatch sessions carrying large payloads plus two
   dispatched sessions carrying small ones must produce the two cohort totals
   the go/no-go rule reads. Summation across sessions is the one computation
   the subcommand exists to produce and is not exercised by any single-session
   case.
7. An ordinary `Bash` call (`git log`, `pytest`) is not classified as authoring.
8. **Adversarial `-c` false positives** — `curl -c cookies.txt`, `tar -cf`,
   `ssh -c aes256`. None may classify as authoring; only `-c` bound to a known
   interpreter counts. Mirrors `TestEditFormat`'s existing
   `test_non_edit_tool_error_with_matching_text_not_counted` (`:8353`).
9. **Heredoc extractor edge cases** — `<<-'EOF'`, a non-`EOF` delimiter, a body
   containing the delimiter word as data, and two heredocs chained with `&&`
   (payloads sum).
10. **No verbatim payload in output** — plant a distinctive sentinel inside an
    authored payload and assert it is absent from report stdout. Matches the
    existing convention at `:1954` and `:2755-2756` and is the executable form
    of the output invariant.
11. **Zero-corpus guard** — an empty corpus prints zeroes without a
    division-by-zero error, matching
    `test_zero_read_calls_prints_zeroes_without_division_error` (`:8843`).
12. **Unparsed tool input** — a block carrying only `__unparsedToolInput`
    lands in its own reported cohort rather than being silently dropped.
13. **Parallel `tool_use` blocks in one assistant turn** — two authoring calls
    in a single record are both counted.
14. **Bucket boundary** — a payload at the largest bucket's edge lands in the
    expected bucket; test 2 pins only the smallest, leaving an off-by-one able
    to falsify A8 undetected.

**End-to-end:** run the subcommand over the default corpus scope and read the
size histogram. The go/no-go for the skill edit is whether authoring mass
concentrates in low-dispatch sessions at payload sizes large enough to have
warranted a dispatch.

**Hook verification (Phase 2C):** `claude-hook-review` on the drafted hook and
its `settings.json` entry — required by the repo `CLAUDE.md`, which routes hook
design and review there. `shellcheck` via
`scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck`. Confirm
by inspection of the hook's own output contract that it can only ever permit,
never deny — the non-blocking property is what the Out-of-scope decline rests
on, so a test asserting it is the load-bearing one.

**Shared-fixture verification:** both the Python and shell test suites read the
same labelled example file; add a shape to one implementation only and confirm
the other suite fails.

**Skill-edit verification, if it ships:** `wc -l` on the skill must stay ≤ 200
(A5); `/skill-review` per `.claude/rules/skill-and-agent-self-review.md`; and
the frontmatter narrowing must be checked against the new body section so the
description and body agree.

## Out of scope

- **A *blocking* hook that denies inline instrument authoring.** Inside this
  repo's reach and declined deliberately: a gate that denies would have to
  judge intent from a command string, and its false-positive cost — a denied
  tool call — lands on every stow user. The non-blocking variant is Phase 2C
  and is in scope precisely because it carries none of that: it permits
  every call, so a false positive costs one advisory line.
- **Raising the 200-line skill cap** in `check-skill-length.sh`. Also inside
  reach, also declined: the cap is doing its job here, and the drafted rule
  fits under it. Changing a repo-wide policy to fit one skill is the wrong
  order of operations.
- **Backfilling `docs/transcript-analysis.md`.** That reference doc has no
  section for `read-scope`, `edit-format`, or `context-composition`; recent
  subcommands document into `docs/case-studies/` plus `CHANGELOG.md`. This plan
  follows the recent precedent and does not close the reference doc's drift.
- **Changing `code-writer`'s charter** to accommodate disposable artifacts. The
  plan routes around it (M4) rather than reshaping it.
- **Re-litigating the four delegation rules already in the skill.** Only the
  carve-out at lines 56-59 and the frontmatter clause are touched, and only
  because the new rule would otherwise contradict them.
