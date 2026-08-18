# Make `review-trace` and `skill-invocation` disclose their scope on a zero-match run

## Context

**Goal:** make a zero-match `review-trace` or `skill-invocation` run state what
corpus it actually scanned, so "scoped to the wrong account" stops being
indistinguishable from "correctly scoped, nothing there."

An error-mode analysis of this repo's session history (2026-08-02–08-14) found
6+ sessions where an agent scanned only its active Claude Code account and
shipped incomplete results before a human corrected it. Four remediation
branches landed (#603, #604, #615, #635) making the default scan a union across
every root in `~/.claude/transcript-config-dirs`. Two residual items survive
that work, and both are cases where the tooling is *silent about scope in
exactly the situation where scope is wrong*:

1. `review-trace` and `skill-invocation` withhold their resolved-scope
   `SOURCES` header until a match is found, so a zero-match run does not say
   what it read.
2. `~/CLAUDE.local.md` still instructs a manual per-account `--config-dir` loop
   that today *narrows* both tools below their own default.

Why now: the header deferral is the same failure shape that let the original
scope-blindness pattern run unnoticed across six sessions, and the stale note is
actively harmful rather than merely out of date.

## Approach

**Chosen design:** print the resolved-scope header unconditionally to stdout in
both subcommands — matching what `buckets`, `duration`, `pr-link` and the large
majority of other subcommands already do — and give `review-trace` an explicit
"no sessions matched" line on each of its two zero-match termini, so such a run
is loud rather than empty. Then update the three prose sites documenting the
deferral as a known exception, extend the skill directive that tells an agent
to surface the header, and refresh the publish-safety warning.

### The measurement that sets the priority

Run against the live 6-root corpus with a branch filter that cannot match
(`--branches zzz-no-such-branch-zzz`):

| Run | stdout | stderr |
|---|---|---|
| `review-trace`, 6 roots | 0 bytes | `scanning root N/6` ×6 |
| `review-trace`, 1 root (`--config-dir ~/.claude`) | 0 bytes | **0 bytes** |
| `skill-invocation`, 6 roots | `No skill invocations found.` | `scanning root N/6` ×6 |

The per-root progress line only prints above one root
(`docs/transcript-analysis.md:44`; pinned by the existing assertion
`"scanning root" not in combined` at `test_transcript_analysis.py:15288`). So a
single-root `review-trace` zero-match run emits **zero bytes on both streams** —
the tool discloses least about its corpus precisely when that corpus is
narrowest, which is the dangerous direction.

### Correction to the task brief

The brief states both subcommands "produce *identical* output: nothing."
That holds for `review-trace` only. `cmd_skill_invocation` prints
`"No skill invocations found."` and returns at `transcript-analysis.py:2854-2856`,
*before* reaching its header call at `:2873` — verified by running it and by
`test_no_invocations_prints_not_found`. So on the zero-match *scan* path
`skill-invocation` has no byte-for-byte-empty stdout to protect; its defect is
only that the message omits scope. (It does assert empty stdout on its
fail-closed error path — see assumption 9 — but this change never reaches
that path.) The brief's three-option tradeoff therefore applies to
`review-trace` alone.

### Why unconditional-to-stdout, and not the three alternatives

The decisive point is that `cmd_review_trace` **already contains this decision**,
five lines from the one that contradicts it. Its `--deny-summary` arm at
`:2093-2098` prints the header plus an explicit message when sessions matched
but carried no denials, with this rationale in-code:

> `# Scoped resolved and had matching sessions, but none carried a`
> `# denial -- printed explicitly so this reads distinctly from a`
> `# broken --branches/scope flag matching no sessions at all.`

Five lines later, `:2101-2103` asserts the opposite for the default arm:

> `# The scope header prints lazily, on the first emitted block — not`
> `# unconditionally up front — so a run that matches no session still`
> `# produces byte-for-byte empty output, as it always has.`

The function is internally inconsistent, and the newer arm's stated reason is
exactly the problem this plan is solving. The fix is to extend that decision to
the remaining arms, not to introduce a third behavior.

- **(b) header to stderr on zero-match** — rejected. It preserves the 7
  empty-stdout test assertions, and `_print_resolved_scope` already accepts a
  `file=` kwarg (`:2746`), so it is cheap. But it would leave one subcommand
  with three header behaviors: stdout when matched, stderr when not, stdout on
  the `--deny-summary` empty-denial path. That is added surface, not less —
  the compounding-layers tell. Kept as the fallback if review objects to the
  stdout change.
- **(c-doc-only) document it more prominently** — rejected. The asymmetry is
  *already* documented at all three prose sites, and it still produced six
  sessions of incomplete results. Re-wording a warning that was already there
  is not a fix.
- **(d) unguard the existing per-root progress line so it prints at one root**
  — rejected. Smallest possible diff (delete an `if multi_root:`), and it
  would disclose the root count on today's silent run. But that print lives in
  the two shared session-iterator helpers, `:2522-2527` and `:2567-2572`, which
  every session-iterating subcommand routes through — unguarding it adds a
  redundant stderr line to ~20 subcommands that already disclose scope
  correctly, to fix two that don't. It also discloses only a count, not the
  scope label or project-dir count, and `skill-invocation`'s scope-less
  "No skill invocations found." would still be scope-less.

Eager printing does change one ordering: an exception raised while consuming
`session_iter` now surfaces *after* the header rather than instead of it. This
is not a new shape — `cmd_buckets` (`:492-496`) and the other eager-header
subcommands already print before consuming the iterator.

`skill-invocation` needs no tradeoff: moving its existing header call above the
early return reproduces `cmd_buckets`' shape exactly.

### Assumption ledger

**Root problem:** a zero-match run of these two subcommands does not disclose the
corpus it scanned, so an under-scoped run and a genuinely-empty result are
indistinguishable to both a human and an agent.

**Givens** — exactly one condition is genuinely outside this plan's reach:

| Given | Why it is fixed here |
|---|---|
| The active profile's config dir plus `~/.claude/transcript-config-dirs` defines the corpus | Shipped by #603/#604/#615/#635 and consumed unchanged here; revising the corpus contract is a different problem from disclosing it |

Two conditions this plan treats as fixed are **inside** its reach and declined
deliberately, so they belong here rather than above:

| Declined change | Reason |
|---|---|
| Unguard the per-root progress line at one root | See rejected alternative (d): the gate lives in two shared iterator helpers, so removing it adds a redundant line to ~20 already-correct subcommands to fix two |
| Return a nonzero exit status on zero matches | It cannot separate the two cases — "wrong account, zero match" and "correctly scoped, genuinely empty" are both zero-match and would return the same code. It fails on its own terms, before the CLI-contract-break objection is reached |

**Mechanisms:**

| Mechanism | Justification | Anchor |
|---|---|---|
| Move `_print_resolved_scope` above `cmd_skill_invocation`'s early return | Lightest possible change; call already exists, only its position moves | `anchors: root` |
| Replace `cmd_review_trace`'s `scope_header_printed` flag with an eager unconditional print | Removes state rather than adding it — one fewer local variable and one fewer branch | `anchors: root` |
| Add "No sessions matched in scope." on **both** zero-match termini — the default arm's post-loop path and `--deny-summary`'s implicit third state (`any_session_matched` false) | Mirrors the `--deny-summary` arm's existing no-denials message; without it a header alone still reads as a truncated run. The `--deny-summary` state is the same bug in a sibling arm of the same function, so it is in scope by the audit-structural-siblings rule | `anchors: root` |

Three lighter primitives were considered and rejected for the `review-trace`
mechanism — `file=sys.stderr` on the existing call, doc-only, and unguarding
the per-root progress line — each argued above. All are smaller in diff, and
each leaves either a three-way channel split, the defect itself, or a
redundant line on ~20 correct subcommands.

**Assumptions:**

| # | Assumption | Tag |
|---|---|---|
| 1 | No non-test consumer depends on empty stdout from either subcommand | `[verified: repo-wide search of hooks, skills, docs, `.github/workflows/tests.yml`, and `~/MyCode/workstation-setup`; sole automated caller is `ready-for-review/SKILL.md:95`, whose "Empty list →" instruction is read by an agent, not shell-tested]` |
| 2 | Exactly 7 assertions pin empty `review-trace` stdout | `[verified: grep — test_transcript_analysis.py:4121, 4218, 4238, 4247, 4343, 4420, 4451, all inside `class TestReviewTrace` (:4022)]` |
| 3 | Those 7 assert empty stdout as a *proxy* for "no event blocks emitted", not as an output contract | `[verified: read of each test's name and message, e.g. :4343 "the session's first-record branch must return zero events"]` |
| 4 | The deferral is documented at exactly 3 prose sites | `[verified: grep "until a match is found" → docs/transcript-analysis.md:40 and SKILL.md:69 ("defer their header print until a match is found", verbatim twins); README.md:75 words it differently ("defer the header until a match is found") and does NOT match the narrower "defer their header print" string — use the wider grep or README is missed]` |
| 5 | `~/CLAUDE.local.md` is a symlink to the private `workstation-setup` repo | `[verified: ls -la — /Users/jared/CLAUDE.local.md → MyCode/workstation-setup/claude-local/CLAUDE.local.md]` |
| 6 | Following the stale note narrows rather than widens both tools | `[verified: transcript-analysis top-level --config-dir overrides the union to one root (docs/transcript-analysis.md:44); post-crash-sessions --config-dir yields active profile + named dirs, dropping the declared roots (post-crash-sessions.py:1230-1258)]` |
| 7 | Editing `CLAUDE.local.md` needs the engineer's say-so before a PR is opened | `[engineer-verified: task brief §5, §6.5]` |
| 8 | Adding a header line ahead of `skill-invocation`'s table will not confuse `skill-fidelity-reviewer` | `[unverified]` — it is an LLM reading rendered text and every non-empty run already carries this header, so the zero-match case simply becomes uniform; not mechanically provable |
| 9 | `skill-invocation`'s two fail-closed tests are unaffected by hoisting the header | `[verified: test_fail_closed_when_git_unavailable (:12965) and test_fail_closed_when_cwd_not_in_worktrees (:12976) both assert `captured.out == ""`, but their `SystemExit` is raised inside `_resolve_project_scope`/`_repo_scoped_project_slugs` (`:2683-2691`, `:2810`) — before the scan and far before the hoisted call site]`. Note this narrows the claim above: `skill-invocation` *does* have empty-stdout assertions, just on an error path this change never reaches |
| 10 | The header's `scope_label` echoes a user-supplied `--projects GLOB` verbatim | `[verified: _resolve_project_scope returns `glob` unmodified as the label, transcript-analysis.py:2696-2699]` — so a zero-match header can now surface a glob where nothing printed before. Judged acceptable: identical disclosure already occurs on any matched run, and the string is one the operator typed |

## Critical files

**Modify:**

- `claude/.claude/scripts/transcript-analysis.py`
  - `cmd_review_trace` `:2081-2131` — in the `--deny-summary` arm, hoist the
    header above the `if`/`elif` at `:2086`/`:2093` so it prints once
    unconditionally, and **add the missing `else`**: today that arm is an
    `if`/`elif` with an implicit third state (`any_session_matched` false —
    scope matched no sessions at all) that falls through to `return` printing
    nothing. Give it the same "No sessions matched in scope." line as the
    default arm; without the `else` this state would print a bare header with
    no explanation, and it is reachable by the plan's own repro command with
    `--deny-summary` appended. In the default arm delete `scope_header_printed`
    and its `if not scope_header_printed:` guard, print eagerly before the
    session loop, and emit the no-match line after it. Replace the `:2101-2103`
    rationale comment — do not leave it describing deleted logic.
  - `cmd_skill_invocation` `:2854-2873` — hoist the `_print_resolved_scope`
    call and its `scope_parts` construction above the `if not all_skills:`
    early return.
  - **Reuse:** `_print_resolved_scope` (`:2746`) and `_resolved_scope_header`
    (`:2727`) unchanged — this plan adds no new helper. The `file=` kwarg stays
    available for the fallback design.

- `claude/.claude/scripts/tests/test_transcript_analysis.py`
  - Rewrite the 7 assertions to assert no event block was emitted rather than
    empty stdout — this asserts the tests' actual intent per assumption 3, and
    is stronger than the empty-string form, which also passes if the function
    early-returns for an unrelated reason. `### ` is the correct marker: it is
    the sole event-block header, emitted at `transcript-analysis.py:2127`, and
    no path prints the branches/event lines without it.
    **Six sites are `out.strip() == ""` → `"### " not in out`
    (`:4121, 4218, 4238, 4247, 4343, 4420`). The seventh, `:4451`, is
    `out_branch_b_only == ""` — no `.strip()`, different variable — and becomes
    `"### " not in out_branch_b_only`.** A find-replace keyed on the uniform
    template silently skips it and leaves it failing.
  - Update the stale docstrings on
    `test_review_trace_header_states_one_root_once_a_session_matches` (`:15290`)
    and `test_skill_invocation_header_states_one_root_once_a_skill_matches`
    (`:15304`). Their bodies assert on `out + err` and need no change.
  - **Add** three zero-match tests: `review-trace` default arm,
    `skill-invocation`, and **`review-trace --deny-summary` with zero sessions
    matched** — the last covers the new `else` and is the one state no existing
    test reaches (`test_deny_summary_with_matching_session_but_zero_denials_prints_explicit_message`
    at `:4777` covers only "matched but no denials"). Each asserts both the
    `SOURCES (` header and the no-match line. **Reuse** the existing
    `fake_projects` fixture, `_review_trace_args()`, `_skill_inv_args()`,
    `_write_jsonl`, and the `_HEADER_SUFFIX` constant.

- `docs/transcript-analysis.md:40`, `README.md:75`,
  `claude/.claude/skills/transcript-analysis/SKILL.md:69` — drop
  `review-trace`/`skill-invocation` from the exception clause, leaving
  `cost --summary` as the only exception. The three are deliberate duplicates
  (per repo CLAUDE.md, skills carry standalone prose), so all three change
  together or the set drifts. Note `README.md:75` words it differently — see
  assumption 4; a grep on the narrower phrasing misses it.

- `claude/.claude/skills/transcript-analysis/SKILL.md:8-12` — **the change that
  makes the rest load-bearing.** Line 10 currently reads: "Before quoting a
  corpus-wide statistic from this toolkit's output, include the resolved-scope
  header line verbatim in what you report." Its trigger is *reporting a
  number*, so it does not obviously bind when the finding is "nothing found" —
  which is exactly the case this plan fixes, and exactly the case where the
  header previously did not exist to quote. Extend the directive so a
  zero-result report is explicitly covered by the same rule. Without this the
  header prints and no instruction tells an agent to surface it, and the
  stated failure (agent behavior across 6+ sessions, not stdout bytes) stays
  open. This is drafted skill-body prose, so `/code-review` will route it to
  `skill-management:skill-review`.

- `docs/transcript-analysis.md:17` — the existing "not publish-safe" warning
  for `review-trace` should note it now applies to zero-match output too,
  since a zero-match header echoes an explicit `--projects` glob verbatim
  (assumption 10). Keeps the canonical warning from going stale by omission.

**Deferred, pending engineer approval (assumption 7):**

- `~/MyCode/workstation-setup/claude-local/CLAUDE.local.md:17-22` — replace the
  stale paragraph. Separate repo, separate worktree, separate PR. Machine-level
  `~/.claude/worktree-required` applies there too.

**Do not touch:** any other subcommand's header behavior. `cmd_user_input` and
`cmd_friction_count` never print a header, and `cost --summary` suppresses it
deliberately — all three are correct as-is and outside this scope.

## Verification

1. From the worktree: `../../../.venv/bin/pytest claude/.claude/` and
   `../../../.venv/bin/ruff check claude/.claude/`.
2. Re-run the commands from the measurement table above and confirm each now
   prints a `SOURCES (...)` line naming the root count plus the no-match line —
   in particular the single-root `review-trace` case, which currently emits
   zero bytes, and the `--deny-summary` variant covering the new `else`:
   ```bash
   python3 claude/.claude/scripts/transcript-analysis.py --config-dir "$HOME/.claude" \
     review-trace --branches zzz-no-such-branch-zzz
   python3 claude/.claude/scripts/transcript-analysis.py --config-dir "$HOME/.claude" \
     review-trace --deny-summary --branches zzz-no-such-branch-zzz
   python3 claude/.claude/scripts/transcript-analysis.py skill-invocation \
     --branches zzz-no-such-branch-zzz
   ```
3. Confirm a *matching* run's stdout is unchanged apart from header position —
   diff `review-trace --this-repo` output against the same command run from the
   merge-base worktree.
4. `/code-review` (which will dispatch `skill-management:skill-review` for the
   SKILL.md edit), then `/ready-for-review`.

## Out of scope

- The four merged remediation branches (#603, #604, #615, #635).
- The open-ended "audit all private tooling for single-account-default shape"
  investigation — file separately if wanted.
- **Raising to the reviewer, not fixing here:** `docs/scripts.md:40-41`'s
  example comments describe `post-crash-sessions` as scanning "the default
  config dir" and `--config-dir` as scanning "an additional profile." Both
  read as wrong against `post-crash-sessions.py:1250-1252`, where an explicit
  `--config-dir` drops the declared-roots union entirely — the same
  widening-vs-narrowing inversion this plan is fixing in `CLAUDE.local.md`.
  Different file, different tool, not required for this change to be correct.
