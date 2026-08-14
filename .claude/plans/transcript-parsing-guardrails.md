# Transcript-parsing guardrails

## Context

**Goal: make `transcript-analysis.py` findable at the moment a session is about
to hand-roll transcript parsing, and stop the assumption tag that let the
resulting wrong scope survive review.**

While investigating cache-rebuild costs, a session (this one) needed corpus-wide
transcript statistics. It wrote an ad-hoc Python script globbing
`~/.claude/projects/*/*.jsonl` and ran it, rather than invoking the
`transcript-analysis` skill. That glob covers one config root. This machine
declares six in `~/.claude/transcript-config-dirs`, and every
`transcript-analysis.py` subcommand already unions across all of them by
default. The resulting figure was understated by roughly 48% ($1,463 vs the
correct $2,162 over 30 days).

The error then survived a full `/plan-review` round. The plan recorded the
narrow scope as a ledger given tagged `[engineer-verified]`, citing a
`CLAUDE.local.md` sentence about shell access to those paths. That tag is
defined as "came from the human directly" and carries an explicit instruction
that reviewers must not override it unilaterally — so a misapplied tag converted
an inference into something the reviewer was told to defer to. The product
reviewer duly validated the given as legitimate. Only an operator correction
dislodged it.

Two distinct failures, needing two distinct fixes:

1. **The toolkit was not findable.** Its skill description is a 531-char feature
   catalogue with no `TRIGGER` clause, so "I need cache-write statistics" had
   nothing situational to match. No guard exists either:
   `deny-data-file-reads.sh` denies `.jsonl` only via the `Read` tool and only
   when armed by `~/.claude/data-file-read-guard.md`, which is absent by default
   and absent on the machine where this happened.
2. **`[engineer-verified]` has no stated source discipline.** Its definition does
   not say a file cannot be its source, so citing a doc sentence reads as
   conforming.

**Why now:** the same shape recurs silently. A wrong corpus scope produces a
plausible number, and the tag suppresses the review step that would catch it.

**Intended outcome:** a nudge that names the toolkit when a session writes a
transcript-parsing script, a trigger clause so the skill is findable before that,
and a tightened tag definition that generalizes beyond transcripts.

## Approach

Add one `PostToolUse` informational nudge, add a `TRIGGER`/`DO NOT TRIGGER`
clause to the `transcript-analysis` skill description, and tighten two bullets in
`plan-it`'s assumption-ledger grammar.

**The nudge informs; it does not deny.** An always-on `PreToolUse` deny gate was
considered and rejected. Three reviewers independently found distinct failure
modes in it, which is the design-wrong-shape signal rather than a patch list:

- It installs at user level and fires in **every repo on the machine**, so a stow
  consumer writing a legitimate personal script that touches
  `~/.claude/projects/*/*.jsonl` would be denied with an escape hatch they cannot
  use — `claude/.claude/scripts/` is this repo's tree, not theirs.
- Content-matching would be a **new mechanism class**: none of the 40 hooks in
  `claude/.claude/hooks/` match on `content`/`old_string`/`new_string`, and the
  nearest precedent (`ask-new-dependency-disclosure.sh`) never denies.
- Literal-string matching is **leaky by construction** —
  `os.path.join(home, ".claude", "projects", ...)`, f-strings, and variable
  indirection all defeat it. That is disqualifying for a gate and irrelevant for a
  nudge: a miss costs nothing and a false positive costs one line of context.
- It would have been the 11th unconditional `PreToolUse` hook on every `Bash`
  call, for correctness-class harm, copying the posture of a sibling that prevents
  credential exposure.

Downgrading to a nudge dissolves all four. The remaining value — telling a
session the toolkit exists at the exact moment it is about to reinvent it — is the
part that actually addresses the incident, since the failure was ignorance, not
evasion.

**`PostToolUse` on `Write`/`Edit`/`MultiEdit`, no `Bash` arm.** `additionalContext`
appears only on `PostToolUse` and `UserPromptSubmit` hooks in this repo; there is
no `PreToolUse` precedent for it. `PostToolUse` is also the better moment: the
write succeeds with zero friction and the nudge lands before the script is run. A
`Bash` arm is dropped entirely — it would have fired on
`grep -rn 'projects/\*/\*\.jsonl' docs/`, a command that discusses the pattern
rather than executing it, and on single-file `cat` reads that have nothing to do
with the multi-root globbing that caused the incident.

### Assumption ledger

**Root problem:** nothing makes `transcript-analysis.py` findable at the moment a
session hand-rolls single-root transcript parsing, and `[engineer-verified]` can
launder the resulting wrong scope past the review step designed to catch it.

**Givens** (fixed, beyond this plan's reach):

- **`PreToolUse` cannot carry `additionalContext`.** Its `hookSpecificOutput`
  schema is `permissionDecision`, `permissionDecisionReason`, `updatedInput`;
  `additionalContext` is `PostToolUse`'s only field. Harness contract. [verified:
  Claude Code hooks reference, raw `hooks.md` — the rendered page misreports this,
  so the raw source is the citable one. Corroborated in-repo: all 7 hooks emitting
  `additionalContext` fire on `PostToolUse`, `UserPromptSubmit`, `SessionStart`, or
  `Stop` — `consume-durable-continuity-file-on-read.sh:129`,
  `nudge-worktree-anchor.sh:172`, `check-branch-divergence.sh`,
  `session-marker-dashboard.sh`, and three other `nudge-*` hooks. None on
  `PreToolUse`.]
- **Skill descriptions are advisory.** Relevance-matching decides whether a skill
  loads; nothing guarantees it. Platform boundary — and the reason mechanism 1
  exists alongside mechanism 2 rather than being replaced by it.
- **The harness silently truncates a skill description past 1,536 chars.** The
  repo constant only mirrors that limit; raising it here would not make a longer
  description reach the model. [verified:
  `plugins/skill-management/scripts/validate_skill_structure.py:25`, whose
  violation message reads "exceeds harness cap of 1536; the tail [gets silently
  truncated by the harness]"; aggregate `SKILL_LISTING_BUDGET_CHARS` at `:47`]

**Mechanisms:**

1. **`nudge-transcript-toolkit.sh`** — anchors: root.
   Fires at the moment of the mistake, which a description cannot do.
   *Lighter primitives considered:* (a) mechanism 2 alone — rejected: a
   description is advisory and only helps if relevance-matching fires, which is
   exactly what failed; the nudge is the backstop for when it does not; (b) making
   `transcript-analysis.py` refuse single-root operation — rejected: it already
   unions by default and prints a resolved-scope header, and the failure was never
   reaching it.
2. **`TRIGGER`/`DO NOT TRIGGER` clause on `transcript-analysis`** — anchors: root.
   Addresses the primary cause: a feature catalogue with no situational trigger.
   *Lighter primitive considered:* rely on mechanism 1's nudge alone — rejected:
   that fires only after a script is already written, one step too late when the
   skill should simply have been findable.
3. **`plan-it` ledger-grammar tightening** — anchors: root.
   The generalizing arm; the tag misuse is domain-independent.
   *Lighter primitive considered:* fix only `plan-review`'s side (teach reviewers
   to challenge `[engineer-verified]`) — rejected: that inverts the tag's purpose,
   which is legitimately to stop reviewers re-litigating the human's decisions.
   The defect is authors over-claiming it, so the author-side definition is where
   it belongs.

**Assumptions:**

- The failing path was `Write` of a `.py` then `Bash` to run it, with no
  transcript path in the command text. [verified: this session's own tool calls]
- Literal-pattern matching catches the accidental case and not the evasive one.
  Accepted, because the incident was accidental. Recorded as a documented residual
  rather than claimed as coverage. [verified: reviewer analysis of
  `os.path.join`/f-string/variable-indirection constructions]
- Markdown must never be content-matched: **13** `.md` files in this repo contain
  a transcripts-glob shape under the pattern `projects/\*.*\.jsonl`. The count is
  pattern-dependent — a looser pattern returns 22 — which is itself why the nudge
  keys on file extension rather than trying to be precise about content.
  [verified: `grep -rlE 'projects/\*.*\.jsonl' --include='*.md'` this session]
- `plan-it/SKILL.md` is 92 lines against `check-skill-length.sh`'s 200-line
  default; `transcript-analysis`'s description is 531 of 1,536 chars. [verified:
  `wc -l`, regex measure] The aggregate listing budget is a second constraint not
  visible from one skill's number. [verified: `test_skills.py` passes post-edit]
- Appending `TRIGGER when:` to the existing **plain** YAML scalar produces invalid
  YAML — the `: ` sequence is not legal there. The description must convert to a
  block-fold (`description: >`), matching `branch-management`. [verified: reviewer
  reproduced `mapping values are not allowed here` before converting]

## Critical files

| Path | Change | Reuse |
|---|---|---|
| `claude/.claude/hooks/nudge-transcript-toolkit.sh` | **New.** `hook-class: informational`. `PostToolUse`, self-filters to `Write`/`Edit`/`MultiEdit`. When the target has a `.py`/`.sh` extension and the written content matches a transcripts-glob pattern, emit `additionalContext` naming `transcript-analysis.py`, its union-across-declared-roots default, and `~/.claude/transcript-config-dirs`. Never denies. The message is a fixed three-fact string, never built from scanned content, so it needs no length budget. Suppression must be **path-anchored to this toolkit's own tree** — `(^\|/)claude/\.claude/(scripts\|hooks\|tests)/` plus the stowed `~/.claude/(scripts\|hooks\|tests)/` shape — **not** a bare `scripts/`/`tests/`/`hooks/` directory-name match, which would silence the nudge for any unrelated project having a `scripts/` directory, including private test suites that hand-roll transcript parsing (precisely the case this exists to catch). Carries a `# Documented residuals:` header block listing the known misses verbatim, mirroring `deny-credential-bash-reads.sh`, so a contributor who breaks the residual tests in §Verification 6 sees why they exist before "fixing" them. | `_lib.sh` bootstrap idiom; `consume-durable-continuity-file-on-read.sh` as the structural template for a `PostToolUse` `additionalContext` emitter; `nudge-worktree-anchor.sh` for message shape |
| `claude/.claude/settings.json` | Register one `PostToolUse` entry under an `Edit\|Write\|MultiEdit` matcher. No `PreToolUse` entry, no `Bash` entry. | Existing `PostToolUse` entry shape |
| `claude/.claude/hooks/tests/test_nudge_transcript_toolkit.py` | **New.** Cases per §Verification. | `helpers.run_hook`, `run_hook_reason`, `write_input`, `edit_input`, `multiedit_input`; `isolated_home` |
| `docs/hooks.md` | Add the `- **\`nudge-transcript-toolkit.sh\`**` entry. Required by `test_hook_alignment.py:113-137`. | Existing entry format |
| `claude/.claude/skills/transcript-analysis/SKILL.md` | Convert `description` to a block-fold and append `TRIGGER when: needing any statistic derived from Claude Code transcripts — cost, cache efficiency, model mix, session history. DO NOT TRIGGER when:` the question is not about transcript data, the ask is a narrative case study rather than a statistic (names `transcript-narrative`), or the number was already produced this session. **Applied and tested.** | Clause phrasing from `branch-management`, `subagent-delegation` |
| `claude/.claude/skills/plan-it/SKILL.md` | `[engineer-verified]` gains "the human stated it as an utterance in this session. A file the human wrote is not this tag's source — that is `[verified: <file>]`, which carries no override protection." `[verified]` gains "prose describing a restriction is not evidence about behavior: when the claim is what a tool or path can reach, run it and cite the result." **Applied and tested.** | Existing bullet list at `:54-57` |

## Verification

1. **The incident regression case.** A `write_input` with `file_path` ending
   `.py` and content containing
   `glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl"))` asserts the
   nudge fires. Without this the hook could pass every other case and still miss
   what happened.
2. **Edit and MultiEdit arms.** Three cases, since MultiEdit has two distinct
   shapes: an `edit_input` whose `new_string` alone carries the glob asserts the
   nudge fires; a **single-entry** `multiedit_input` whose lone `new_string`
   carries it asserts fires (the common shape in practice, and untested in either
   direction before this round); a `multiedit_input` splitting the glob across two
   `edits[]` entries asserts it does **not** — pinned as a known miss, not left
   unclaimed.
3. **Never fires on markdown.** Assert against the **real repo's** `.md` files
   containing the pattern, not one synthetic fixture, so a future narrowing of the
   extension list cannot silently start matching them. Assert the discovered
   corpus is non-empty first — if those docs are ever edited away the assertion
   goes vacuously true and silently stops testing anything.
4. **Quiet when editing the toolkit.** A `.py` under each of the three suppressed
   directories — `scripts/`, `tests/`, **and** `hooks/`, not `scripts/` alone —
   asserts no nudge, in both the worktree-nested shape
   (`.claude/worktrees/<branch>/claude/.claude/scripts/…`) and the stowed shape
   (`~/.claude/scripts/…`, where the doubled `claude/.claude` collapses; this is
   the one a naive substring check fails). The `tests/` branch matters most: this
   hook's own test file necessarily contains the glob pattern as fixture data, so
   a regression there would fire the nudge on every run of its own suite.
5. **Message is actionable.** `run_hook_reason` asserts the text names
   `transcript-analysis.py`, the union-across-roots default, and
   `~/.claude/transcript-config-dirs`. A nudge that does not say what to use
   instead reproduces the original failure.
6. **Documented residuals, asserted as misses.** `os.path.join(...)`, f-string,
   and variable-indirection constructions assert **no** nudge, matching
   `deny-credential-bash-reads.sh`'s convention of pinning known gaps by test
   rather than letting the contract overclaim. Non-`.py`/`.sh` extensions
   likewise. These tests are meant to break when the matcher improves — that is
   the signal to update the hook's `# Documented residuals:` header, which is why
   that header is required rather than optional.
7. **Fire-side variety, not just the incident's literal string.** At least one
   construction beyond the exact incident text — string concatenation
   (`home + "/.claude/projects/*/*.jsonl"`) — asserted explicitly as either caught
   or residual. Currently it is undocumented in both directions, and the fire-side
   coverage rests on a single literal shape exercised three ways.
8. **Alignment.** `test_hook_alignment.py` passes — `hook-class: informational`
   and the `docs/hooks.md` entry.
9. **Skill validation.** `test_skills.py` passes, covering both the per-skill
   1,536 cap and the aggregate listing budget. Already confirmed green (251
   passed) for the applied mechanism 2 and 3 edits.
10. **Skill body cap.** `check-skill-length.sh` is a **commit-time** gate, not a
   pytest item — confirm via an actual `git commit` attempt or by invoking the
   script directly; running `pytest` alone does not exercise it.
11. **Lint:** `../../../.venv/bin/ruff check claude/.claude/` and
    `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck`.

## Out of scope

- **An always-on `PreToolUse` deny gate.** Considered and rejected after review:
  global blast radius across every repo, no usable escape hatch for stow consumers
  outside this repo, a new content-matching mechanism class, an Edit arm unsound by
  construction, and leaky literal matching. The nudge keeps the value and drops
  the failure modes.
- **A `Bash` arm.** Dropped: it would fire on commands that discuss the pattern
  (`grep -rn 'projects/\*/\*\.jsonl'`) and on single-file reads unrelated to the
  multi-root globbing that caused the incident.
- **Arming `deny-data-file-reads.sh`, or giving it a Bash sibling for all data
  types.** That hook's opt-in posture and repo-wide extension list are a separate
  design with a separate blast radius. Still a real gap, separately ticketable.
- **Auditing other Read-only gates for a missing Bash arm.** Offered and declined;
  `deny-env-reads.sh` is the next candidate, named alongside
  `deny-data-file-reads.sh` in `deny-credential-bash-reads.sh`'s own header.
- **Adding a `TRIGGER` clause to `transcript-narrative`.** Surfaced during review —
  it has none either, and it is `transcript-analysis`'s nearest neighbour. Real,
  but not this plan's incident.
- **Any change to `plan-review`'s handling of `[engineer-verified]`.** The tag's
  override protection is correct; only author-side claim discipline was at fault.
