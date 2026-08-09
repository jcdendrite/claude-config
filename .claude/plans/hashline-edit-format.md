# Hashline edit format: evaluate, decline, and document

## Context

**Goal:** record a measured, reproducible decision on whether to adopt Stencil's
"hashline" edit format in place of Claude Code's built-in `Edit`/`Write`, so the
question does not get re-litigated from the blog post's headline numbers.

The [Stencil harness post](https://stencil.so/blog/the-harness-problem) reports
large coding-benchmark gains from changing only the edit tool — content-hashed
line tags (`2:f1|  return "world";`) that the model references instead of
reproducing exact old text. The headline is "+15pts avg over patch, 16 models."

Two things make that headline inapplicable here, and two measurements make the
whole change unattractive — including the mechanism a colleague of the
engineer's raised independently: even successful `str_replace` edits pay a
uniqueness-anchoring tax that hashline's tags would eliminate.

1. The gains are measured **against `patch`** (Codex's `apply_patch` format).
   Claude Code uses `str_replace`. The relevant column is Δ REPLACE: Sonnet 4.5
   **+3.3**, Haiku 4.5 +11.3, GPT 5.2 Codex −0.4, DeepSeek V3.2 −8.3. Opus is
   not benchmarked at all — and this machine runs `"model": "opusplan"`.
2. Hashline's second claimed benefit, rejecting edits against a stale file, is
   already implemented. The live `Edit` tool description reads: *"You must Read
   the file in this conversation before editing, or the call will fail,"* and
   `Read`: *"the harness tracks file state for you."*
3. Measured against this machine's own history, str_replace-mechanical `Edit`
   failures are **0.77% of `Edit` calls** (governance-hook denials excluded —
   see Measurement).
4. The token-overhead mechanism is real and measured — `old_string` averages
   135 tokens per edit, ~33% of Edit-call payload — but the magnitude the
   article reports doesn't transfer: eliminating it entirely saves **0.67% of
   total assistant output tokens** here, because edit calls are a small slice
   of a mixed session's output, unlike the article's edit-only benchmark.

**Intended outcome:** a case study capturing the measurement and the mechanism
analysis, plus a decision on the one real defect the measurement surfaced.

## Measurement

Snapshot taken 2026-08-08 across all four config dirs — `~/.claude/projects`
plus the three account dirs under `~/.config/claude-accounts/`. Every `.jsonl`
under each (session transcripts at depth 1, subagent transcripts at depth 3,
workflow-agent transcripts at depth 5). The corpus grows every session
(including this one), so two scans minutes apart return different absolute
counts — **7,428 `Edit` + 2,700 `Write` calls** is the final atomic pass; every
percentage below held stable to two decimal places across three separate scans
at slightly different corpus sizes, which is the property that matters.
`MultiEdit` returned 0 — the tool no longer exists and must not appear in the
recognized-tool set.

Every failure is paired to its originating call by `tool_use_id`. Rates are
stated against the **per-tool** denominator, because `not_found` is
structurally impossible for `Write`. Errors also include governance-hook
denials (worktree enforcement, plan-review gate, reviewer-tree-mutation, path
gates) — those are excluded from the table below since they reflect this
repo's own review pipeline, not the edit format; see "What actually blocks
edits in this corpus" for that count:

| Tool | Failure | count | % of that tool's calls | Fixed by hashline? |
|---|---|---|---|---|
| `Write` | `file has not been read yet` | 141 | 5.22% | **No** — hashline needs a prior read too |
| `Edit` | `String to replace not found` | 47 | 0.63% | Partly — see below |
| `Edit` | `file has not been read yet` | 6 | 0.08% | **No** |
| `Edit` | multiple-match rejection | 4 | 0.05% | Yes |
| `Edit` | `old_string`/`new_string` identical (no-op) | 6 | 0.08% | No — not a format defect |

**"str_replace-mechanical" totals 57 of 7,428 (0.77%)** — the sum of
`not_found` + `unread` + multi-match (47+6+4), deliberately excluding the 6
no-ops: a no-op is the model asking for a change that does nothing, which
succeeds or fails identically regardless of edit format, not a uniqueness or
staleness failure. Including no-ops gives **63 of 7,428 (0.85%)** as the total
count of every non-governance `Edit`-tool error; cite 0.85% wherever "every
Edit error, full stop" is the intended claim, and 0.77% wherever the claim is
specifically about str_replace's anchoring mechanism.

The `not_found` failures, attributed by pairing each failure with the **next
`Edit` on the same `file_path`** and diffing the two `old_string`s under
whitespace normalization — a rule decided by transcript evidence rather than by
inspecting the failed string alone (attribution run against the 44-failure
sub-scan, since the 3 additional failures the final atomic pass picked up
arrived from this session's own edit activity after that sub-scan ran):

| cause | count | share |
|---|---|---|
| content genuinely differs (changed or misremembered) | 33 | 75.0% |
| `[REDACTED-CREDENTIAL]` placeholder from this repo's own hook | 6 | 13.6% |
| **whitespace-only difference — the genuine hashline target** | **2** | **4.5%** |
| abandoned, no retry | 2 | 4.5% |
| identical retry | 1 | 2.3% |

**Ceiling on benefit: 2 whitespace cases + 4 multi-match = 6 of 7,428 `Edit`
calls = 0.08%.** The whitespace slice alone is 0.03%.

### Why an earlier draft of this plan said 0.24%

A first pass attributed cause with the regex `\t| {2,}\S` applied to the failed
`old_string`, yielding 24 whitespace cases. That classifier is not
discriminative: measured against the corpus it fires on **60.1% of *successful***
`Edit` calls versus 63.6% of failed ones — a 3.5pp gap. It detects "this string
contains indentation," which is true of most code, not "this edit failed because
of whitespace." The 12× over-attribution it produced is recorded here because
the case study must not repeat it, and because any cause classifier the
subcommand ships needs a test asserting it does **not** fire on an
indented-but-correct `old_string`.

The conclusion is unchanged and strengthened: the failure class hashline fixes
is roughly one edit in 3,700 (2/7,428).

### Anchoring token cost — the stronger objection, and the answer

Failure rate is not the main cost of `str_replace`. To guarantee a unique
match the model must emit enough surrounding context on **every successful
edit**, not just failed ones. That overhead is real and measurable:

| | chars | ~tokens @4 c/t | |
|---|---|---|---|
| `old_string` — pure anchoring | 4,001,316 | ~1.00M | **32.9% of Edit payload** |
| `new_string` — the actual change | 8,161,194 | ~2.04M | |
| `Write` content | 20,325,944 | ~5.08M | hashline does not reduce this |
| all assistant output, all sessions | — | **149.7M** | |

Mean `old_string` is **539 chars (~135 tokens) per edit**; 21.8% of edits carry
one over 700 chars. So roughly a third of every `Edit` call is tokens spent
solely proving uniqueness — the mechanism is confirmed.

The magnitude is what settles it. `old_string` is 0.67% of total assistant
output tokens (0.89% at 3 chars/token, a more realistic ratio for code) — this
is a **floor**, not a ceiling: `new_string` re-emits most of the same
surrounding context under `str_replace`'s "reproduce the whole edited region"
convention, so the true addressable ceiling is closer to all `Edit` payload —
~2.03% of total output tokens (`old_string` + `new_string` combined, computed
below). Even at that upper bound the article's headline "−24% output tokens"
does not transfer, for two reasons visible in its own text:

- Its savings are **failure-driven**: *"Grok 4 Fast's output tokens dropped 61%
  because it stopped burning tokens on retry loops."* At a 0.77% failure rate
  there are almost no retry loops here to eliminate.
- Its **denominator is a pure-editing benchmark** — fresh session, four tools,
  one bug fix per task — where edit calls are essentially all the output. In
  real mixed work (reasoning, search, reads, new-file writes, prose) `Edit`
  payload is ~2% of emitted tokens.

### What actually blocks edits in this corpus

Re-bucketing every error paired to an edit-family call: **252 are this repo's
own governance hooks** (78 worktree-enforcement, 67 plan-review gate, 53
reviewer-tree-mutation, 21 worktree-isolation, 17 path-spelling, 16 permission
denials), versus 57 str_replace mechanical failures. Self-imposed governance
blocks roughly 4.4× more edits than the edit format fails. That is not an argument against the hooks —
each denial is the gate doing its job — but it does locate where edit friction
in this setup actually comes from, and it is not the edit format.

## Approach

Write one case study under `docs/case-studies/`, matching the existing
sibling shape (`worktree-enforcement.md`, `review-vs-babysitting.md`) — those
record a decision plus the evidence behind it, which is exactly this artifact.
Do **not** implement hashline in any form.

The measurement also surfaced one real defect — this repo's own redaction hook
causing 6 edit failures. Both available fixes were evaluated and both rejected;
the recommendation is to record it rather than patch it. See "Redaction defect"
below, and **Open decision** for the confirmation that needs to happen first.

### Why not implement hashline

Full adoption is mechanically possible and disproportionately expensive.

The mechanism would have to be: ship an MCP server exposing hashline
`read`/`edit`/`write`, then delete the built-ins so the model uses it. Both
halves are forced:

- MCP tools are hard-prefixed. *"The full form is
  `mcp__plugin_<plugin-name>_<server-name>__<tool-name>`"* — an MCP tool can
  never be named `Edit`, so it coexists with the built-in rather than replacing
  it, and the system prompt keeps steering to the built-in.
- Removing the built-in requires a bare-name deny. *"A bare tool name like
  `Bash` removes the tool from Claude's context entirely, so Claude never sees
  it"* (permissions docs). Scoped rules like `Edit(path)` only block calls.

That combination breaks three things, all of which ship to every stow consumer:

1. **Three read guards and the credential-value redactor silently stop
   applying.** `deny-env-reads.sh`, `deny-credential-file-reads.sh`, and
   `deny-data-file-reads.sh` are registered on the `Read` matcher, and the 13
   `Read(**/...)` deny rules in `settings.json` match the `Read` tool. So does
   `redact-credential-values.sh`, on its own `Bash|Read|WebFetch|Grep|Task`
   matcher group. None match `mcp__hashline__read`. The MCP path therefore
   opens *exactly* the credential residual the "Redaction defect" section below
   refuses to open, and additionally loses the `deny-data-file-reads.sh` size
   cap that `redact-credential-values.sh:5` depends on. Every one would need
   re-keying before the built-in could be removed — a security-critical
   migration in service of a 0.08% edit-failure improvement.
2. **~13 hook registrations lose their matcher, two of them integrity gates.**
   Six on `Edit|Write|MultiEdit` (`ask-review-permissions`,
   `require-plan-review`, `require-worktree-for-file-writes`,
   `require-memory-skill`, `deny-reviewer-tree-mutation`,
   `enforce-marker-script-shape`), five on `Read`, plus
   `plugins/lovable-cloud`'s PreToolUse `Write` pair. The last two of the six
   are `hook-class: gate`, not capability: `enforce-marker-script-shape`
   guards the review-marker state CLAUDE.md treats as forge-resistant. Losing
   those is an integrity regression, not a missing convenience.
3. **`Read` loses non-text capability.** The built-in reads PDFs by page range,
   images, and Jupyter notebooks with outputs. A line-oriented hashline reader
   cannot carry those, so denying `Read` is a strict capability loss.

Lighter primitives considered and set aside:

- **PostToolUse `Read` hook adding hash tags, built-ins untouched** — strictly
  harmful. The model would quote the tagged text back in `old_string` and every
  edit would miss. This is not speculative: it is precisely the 6 measured
  `[REDACTED-CREDENTIAL]` failures, which are the same mechanism at ~0% line
  coverage. Hashline would apply it to every line.
- **PreToolUse `Edit` `updatedInput` whitespace repair** (re-anchor a
  whitespace-only mismatch to the file's exact text). Once the attribution is
  corrected this targets **2 measured cases**, against a new always-on mutation
  layer on the edit path where a wrong re-anchor silently edits the wrong
  region. Recorded in the case study as the option to revisit if the rate ever
  moves, not adopted now.

### Redaction defect — recommended fix differs from the option preview

The selected option previewed "skip mutation on Read tool_response." **I do not
recommend that**, and want the disagreement on record before implementation.

The argument *for* it is genuinely strong: the hook's own header says it exists
to backstop *"credentials that reach context through a path
`deny-credential-bash-reads.sh`/`deny-credential-file-reads.sh` don't cover
(WebFetch body, Grep match, subagent output)"* — `Read` is not in that list, and
line 5 notes Read already has a size cap via `deny-data-file-reads.sh`. By its
own documentation, `Read` is the one channel in the matcher that is separately
covered.

The argument against is the trade: removing `Read` opens a real residual — a
credential-shaped value hardcoded in a **non**-credential-path file (a token in
`src/config.ts`) would reach context unredacted, since the path gates never fire
on it. That buys back 6 failures in 7,428 `Edit` calls (**0.08%**), and those failures
are self-correcting — the model re-reads and retries — whereas a leaked
credential is not recoverable. The asymmetry does not favor the trade.

**Recommended instead: change no code.** Record the defect in the case study as
a measured, accepted cost.

The intermediate option — a `PostToolUse` `Edit` hook emitting
`additionalContext` when a failed edit's `old_string` contained
`[REDACTED-CREDENTIAL]` — was drafted and rejected at the design-fitness gate.
It is a layer whose only purpose is to explain a failure the *previous* layer
creates, which is the compounding-defensive-layers tell CLAUDE.md §Working Style
names ("Do not keep adding hardening"). At 6 occurrences in 7,428 `Edit` calls
(**0.08%**), narrating a self-inflicted failure does not earn a permanent hook
on the edit path in every stow consumer's session.

This narrowed what the engineer originally selected ("Case study + fix
redaction defect") to case-study-only. Confirmed by the engineer directly —
case-study-only, no code change to `redact-credential-values.sh`.

### Assumption ledger

**Root problem:** the hashline adoption question needs a grounded answer, and
the blog post's headline number does not answer it for this harness.

**Givens** (fixed, outside this plan's reach):
- Claude Code's built-in tool schemas and system-prompt tool descriptions are
  vendor-controlled; a config repo cannot edit them. *(Anthropic owns the
  closed harness.)*
- MCP tool naming is prefix-forced by the harness. *(Vendor-imposed.)*

| # | Assumption | Tag |
|---|---|---|
| 1 | 7,428 `Edit` + 2,700 `Write` calls; every error paired to its call by `tool_use_id`; three separate scans agree on every percentage to 2 decimals despite the corpus growing between them | `[verified: full-corpus scan, 4 config dirs, 2026-08-08]` |
| 1a | 2 whitespace-only + 4 multi-match = 0.08% of `Edit` calls is the hashline ceiling | `[verified: next-edit-same-file diff attribution, re-derived after the first classifier was shown non-discriminative]` |
| 1b | The original `\t\| {2,}\S` classifier fires on 60.1% of successful edits vs 63.6% of failed — not discriminative | `[verified: base-rate test over 7,365 successful Edit old_strings (7,428 total − 63 all-error Edit calls)]` |
| 1c | str_replace-mechanical `Edit` failures (`not_found`+`unread`+multi-match, no-ops and governance denials excluded) total 57/7,428 = 0.77%; including no-ops, all non-governance `Edit` errors total 63/7,428 = 0.85% | `[verified: re-bucketed all non-governance errors against 6 governance-hook message patterns; a scripting bug in the first pass mismatched the no-op message text ("old_string and new_string are exactly the same," not "replacement are exactly the same") and mis-sorted those 6 into an unclassified bucket — caught and corrected this round]` |
| 1d | `old_string` is 32.9% of Edit-call payload (4,001,316 of 12,162,510 chars) and 0.67% of all assistant output tokens (~1.00M tok of 149.7M) | `[verified: char sums over every Edit tool_use input, 4 chars/token estimate; total from message.usage.output_tokens; recomputed independently by ciso-reviewer round 2, all figures confirmed]` |
| 1e | 252 of ~291 non-mechanical edit-family errors are this repo's own governance hooks (worktree/plan-review/reviewer-tree/worktree-isolation/path/permission) | `[verified: same re-bucketing as 1c]` |
| 2 | Bare-name deny removes a tool from context entirely | `[verified: code.claude.com/docs/en/permissions, quoted above]` |
| 3 | MCP tools are hard-prefixed `mcp__…` and cannot be named `Edit` | `[verified: code.claude.com/docs/en/mcp]` |
| 4 | PreToolUse supports `updatedInput`; PostToolUse supports `updatedToolOutput` | `[verified: code.claude.com/docs/en/hooks; updatedToolOutput also in use at redact-credential-values.sh:45]` |
| 5 | Claude Code already enforces read-before-edit and tracks file state | `[verified: live Edit/Read tool descriptions in this session]` |
| 6 | Three read guards + `Read(**/.env)` deny rules key on the `Read` tool name and would not match an MCP replacement | `[verified: claude/.claude/settings.json PreToolUse Read matcher]` |
| 7 | Δ REPLACE for Sonnet 4.5 is +3.3 and Opus is unbenchmarked | `[verified: article table as pasted by the engineer]` |
| 8 | The 6 redaction-caused failures are attributable to `[REDACTED-CREDENTIAL]` appearing in `old_string` | `[verified: error-text match against the Edit call's own tool_use_id]` |
| 9 | Whether 2 whitespace failures justifies any edit-path change | `[resolved: no]` — settled by row 10's case-study-only disposition; retained only because the same 2-case/0.77%/0.67% figures set the numeric revisit trigger (Critical files, below) |
| 10 | Deliverable is the case study alone; no code change to `redact-credential-values.sh` | `[engineer-verified]` — initially selected "Case study + fix redaction defect", then confirmed the narrowing to case-study-only after both candidate fixes were rejected at the design-fitness gate |
| 11 | The transcript scan window is whatever history these four config dirs retain — not a fixed period | `[unverified]` — no retention policy checked; the rate is per-call, not per-unit-time, so this does not affect the percentages |

## Critical files

**Create:**
- `docs/case-studies/hashline-edit-format.md` — the decision record. Follow the
  shape of `docs/case-studies/worktree-enforcement.md` (problem → what was
  measured → mechanism → decision → revisit trigger). Must state the revisit
  trigger numerically: reconsider if str_replace-mechanical `Edit` failures
  exceed ~3% of `Edit` calls (4× today's measured 0.77%), if `old_string`
  overhead exceeds ~2.7% of total assistant output tokens (4× today's 0.67%),
  or if a Claude model ships with a benchmarked Δ REPLACE gain materially above
  Sonnet 4.5's +3.3.
- A `transcript-analysis.py` subcommand consolidating the logic from seven
  scratchpad scripts this measurement produced — port the logic, not the
  files, since none are fit to ship as-is (each answered one question during
  the session, and `final_snapshot.py` in particular carries dead scaffolding
  from an abandoned attribution attempt): `scan_edits2.py` (call/failure
  counts), `scan_notfound.py` (cause attribution — ship the corrected
  next-edit-diff method, not the discarded regex), `verify_attribution.py`
  (classifier base-rate check), `verify_pertool.py` (per-tool denominator
  pairing), `measure_overhead.py` + `classify_unknown.py` (token cost,
  governance-vs-mechanical error split), and `final_snapshot.py` (the atomic
  single-pass version that produced every headline figure in this plan —
  reproduce its single-pass property, since running separate scripts at
  different moments is what caused this plan's own numbers to drift between
  round 1 and round 2 of review). `evals/` is skill-scoped by convention
  (`*-cases.json` under a skill dir), so this belongs in
  `claude/.claude/scripts/` — **prefer a `transcript-analysis.py` subcommand**
  over a new script, since that file already owns multi-project transcript
  scanning and has the `--projects`/`--this-repo` scope flags these scripts
  hand-rolled.

**Modify:**
- `docs/design-decisions.md` — one-line entry pointing at the new case study.
- `claude/.claude/scripts/tests/` — tests for the new subcommand.

Under the recommended disposition no hook is added, so `claude/.claude/hooks/`,
`claude/.claude/settings.json`, and `docs/hooks.md` are untouched.

**Reuse rather than reimplement:**
- `claude/.claude/scripts/transcript-analysis.py` — existing subcommand
  scaffolding, `_add_project_scope_args` (line ~5047) for the scope flags, its
  established multi-config-dir handling, and **`_build_redact_map` / `--redact`
  (~line 3016–3060)** for `account-{N}` labelling. `_read_session_file`
  (~line 394) globs only `subagents/*.jsonl`, so the depth-5
  `subagents/workflows/wf_*/agent-*.jsonl` shape the scratchpad scripts picked
  up via `**` is silently dropped by every existing subcommand today, not just
  the new one — **do not change that glob in this PR.** It is shared code that
  every other subcommand's counts depend on; widening it is a behavior change
  to already-shipped output for every stow consumer and needs its own
  regression test pinning current counts, which is out of scope here. The new
  subcommand documents the exclusion (depth-5 transcripts undercounted) rather
  than silently inheriting it — see Out of scope.
- `claude/.claude/hooks/_lib.sh` — `_lib_jq`, the fail-open idioms, and
  `_lib_config_dir`; a new hook must not hand-roll JSON extraction.
- `docs/design-decisions.md` — add a one-line entry pointing at the new case
  study rather than restating it (single source of truth).

**Do not modify:** `redact-credential-values.sh`'s redaction *behavior*, per the
reasoning above.

One comment-only exception, in scope because the case study cites this header as
evidence and must not leave the code contradicting the doc:
`redact-credential-values.sh:3`'s parenthetical "(WebFetch body, Grep match,
subagent output)" reads as a coverage spec but is only the motivating list —
`Bash` and `Read` are both in the matcher and absent from it. That phrasing is
what made "remove `Read`, the header says it isn't covered" look correct. Add
one line stating the parenthetical enumerates motivating channels, not the
matcher. No behavior change; no test change.

## Verification

1. **Pin, don't reproduce.** The corpus grows every session, so "re-run and
   confirm the numbers match" can never pass. The case study states each figure
   as a snapshot: date, corpus size, and the exact command that produced it.
   The only reproducible assertion is the fixture test in step 3. Every
   quantitative claim must still be re-derived at write time rather than copied
   from this plan.
2. **Classifier honesty.** Any cause-attribution rule the subcommand ships must
   report its own base rate on *successful* edits alongside its hit count. A
   classifier whose positive rate on successes approaches its rate on failures
   measures nothing — this is the specific defect that produced the discarded
   0.24% figure.
3. **Sibling audit.** Confirm no other hook mutates a tool output that a later
   tool consumes as an exact-match contract — `grep -rn "updatedToolOutput"
   claude/.claude/hooks/ plugins/` should return only
   `redact-credential-values.sh`. If a second one exists it has the same latent
   defect and belongs in scope.
4. **Scan subcommand:** `.venv/bin/pytest claude/.claude/` and
   `.venv/bin/ruff check claude/.claude/`. Fixtures are helper-built synthetic
   records using the established `_write_jsonl` / `_write_subagent_jsonl` /
   `_tool_result` helpers and the `fake_projects` fixture in
   `claude/.claude/scripts/tests/test_transcript_analysis.py` — not files on
   disk, and not `evals/fixtures/` (skill-eval scoped, do not reuse). Required
   cases, none of which the earlier draft covered:
   - the cause classifier does **not** fire on an indented-but-correct
     `old_string` (the negative test that would have caught the 0.24% error);
   - per-tool denominators stay separate — a `Write` failure must not be
     divided by `Edit` calls;
   - a non-edit tool's error containing the literal text "String to replace not
     found" is **not** counted (mirrors
     `test_current_format_denial_text_without_is_error_ignored`); this session's
     own transcript now contains that string, so substring-only matching is
     actively contaminating;
   - failures pair by `tool_use_id`, with an explicit **unpaired counter**
     that is asserted, not silently dropped — the source scripts `continue`
     past an orphaned `tool_use_id` with no visible count, which would
     under-count silently if a subagent transcript boundary ever separates a
     tool call from its own result;
   - the recognized-edit-tool set is asserted, so a future rename cannot
     silently zero the denominator — `MultiEdit` is already dead;
   - each governance-hook denial pattern (worktree, plan-review,
     reviewer-tree, worktree-isolation, path-spelling, permissions) has its
     own fixture asserting it lands in the *named* bucket, plus one fixture
     asserting an unrecognized denial message lands in a reported
     `unclassified` count rather than vanishing — six case-insensitive
     substring matches drift silently if a hook's message text changes;
   - a fixture for the `[REDACTED-CREDENTIAL]` cause bucket, since it is both
     a named bucket the subcommand ships and the case study's one real
     defect;
   - the no-op vs. mechanical-total distinction (57 excluding no-ops, 63
     including them) is asserted as two separate counts, not collapsed into
     one — this exact miscount (a no-op pattern that didn't match the real
     error text) is what round 2 of this plan's own review caught.
5. **Public-repo redaction — mechanized, and scoped to every artifact.**
   - **Use the existing primitive, not prose discipline.**
     `transcript-analysis.py` already emits `account-{N}` labels via
     `_build_redact_map` / `--redact`, whose docstring states the intent
     ("never the config-dir path or its basename, which would leak the
     account/client identifier the directory name encodes"). The new subcommand
     must emit per-account figures through it, and the case study quotes that
     output verbatim. Hand-anonymizing at writing time is the defect; the
     mechanized label is the fix.
   - **The hook is not a backstop here.** `~/.claude/private-projects.md` has an
     entry for one of the three account names but **not** the other two. An
     implementer who tests the gate with the covered name will see it deny and
     wrongly conclude the tier is armed. Either populate the missing entries
     before implementation, or treat this tier as absent for this PR.
   - **Scope is the whole PR, not just the doc.** `.claude/plans/` is tracked,
     so this plan file ships alongside the implementation. Account directory
     names, `~/.config/claude-accounts/<name>` in **both** tilde and absolute
     form, and the scratchpad paths under the operator's home directory must
     be scrubbed from the plan file, commit message, and PR body as well.
     Note the structural detector `_LIB_HOME_ROOTED_PATH_REGEX` matches only
     `/Users/`|`/home/` — a tilde-form path does **not** fire it.
   - **Scope also includes the new subcommand's source and its test
     fixtures.** The governance-hook denial messages this measurement quotes
     (`require-worktree-for-file-writes.sh` and others) embed absolute paths
     verbatim in their own output. Test fixtures for the governance-classifier
     cases above must be **synthetic** — a constant pattern string plus a
     placeholder path — never copied from a real transcript line, which could
     carry a private-repo path in a non-home-rooted position the structural
     detector doesn't catch. This is the same synthetic-fixture rule
     Verification step 4 already requires for consistency, applied here for a
     redaction reason as well as a testing-convention one.
   - Carry no `private-project-N` ordinals into the public doc; the ordinal
     itself discloses how many other private projects sort before it.
6. Route through `/code-review`; the doc-only path still needs it, and the
   security-posture reasoning in the case study warrants `ciso-reviewer`.

## Out of scope

- Implementing hashline in any form, including the opt-in MCP prototype.
- The `PreToolUse` `Edit` whitespace-repair hook — recorded as a revisit option.
- The `PostToolUse` `Edit` diagnostic hook — rejected at the design-fitness
  gate as a compounding layer disproportionate to a 0.08% failure class.
- The 141 `Write`-side and 6 `Edit`-side `file has not been read yet` failures
  (the largest class by far — 5.22% of `Write` calls). They self-correct in one
  round trip; no mechanism in the harness lets a hook satisfy the read on the
  model's behalf. Worth its own investigation, not this one.
- Any change to `redact-credential-values.sh`'s redaction behavior or matcher.
