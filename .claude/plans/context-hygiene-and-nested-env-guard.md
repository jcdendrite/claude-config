# Nested `env.*` guard gap, and closing Phase 4

## Context

Ship the `guard-settings-session-keys.sh` nested-`env.*` bypass fix, and
close Phase 4 of `.claude/plans/token-cost-reduction.md` as rejected
rather than shipping it.

This branch currently carries two phases of that parent plan. Phase 5a
(the guard fix) is sound and uncontested. Phase 4 — two `## Working
Style` bullets in `claude/.claude/CLAUDE.md` telling the agent to run
`/clear` at a phase boundary and `/compact` before idling — was rejected
on review. Re-evaluating it against the repo's own records finds four
independent defects, so it is dropped rather than reworded.

Intended outcome: `claude/.claude/CLAUDE.md` returns byte-identical to
`main`; the guard fix and its tests ship unchanged; Phase 4's rejection
is recorded where a later plan will find it instead of re-proposing it.

## Approach

Delete the two bullets, record Phase 4 as rejected in the parent plan and
in `docs/cost-levers-considered.md`, and ship Phase 5a alone. No
replacement guidance is written, in CLAUDE.md or anywhere else: the
canonical home for session-reset policy already exists and already says
the opposite of what the bullets said.

### Why the bullets fail — four independent reasons

**Wrong actor.** `/clear` and `/compact` are harness built-in CLI
commands with no tool binding. `claude/.claude/CLAUDE.md` is stowed to
`~/.claude/CLAUDE.md` and loaded as the agent's prefix on every API call.
Both bullets instruct a reader who cannot perform the action, and are
re-read at full cost on every call to do it — inside a phase whose stated
purpose is cost reduction.

**The `/compact` bullet overrides an `[engineer-verified]` given in its
own parent plan.** `token-cost-reduction.md:173-174` records "Compaction
is not an acceptable mechanism in this workflow; handoff is the
prescribed context-reset primitive. `[engineer-verified]`", and its
Approach (`:73-92`) and Out-of-scope (`:419-421`) say the same. Phase 4's
one-line Phases entry (`:303-304`) names `/compact` guidance anyway, so
the entry contradicts the ledger row it sits under.

**The `/clear` bullet re-proposes advice this repo already measured and
rejected.** `docs/cost-levers-considered.md:54` records "'Always hand off
before implementation' as blanket advice | Rejected as mispriced", and
`:53` records "Automating the continue-vs-handoff decision via a hook |
Rejected | A hook cannot see how much work remains, which is half of the
breakeven calculation." A CLAUDE.md bullet is the prose form of the same
blanket rule, and it inherits the same defect: neither a hook nor an
agent can see how much work remains, so neither can tell a phase boundary
worth resetting at from one worth continuing through.

**Single source of truth.** Session-reset policy has a canonical home:
`handoff/SKILL.md:15-17` states the continue-by-default position with its
reason, `brief/SKILL.md`'s frontmatter description carries the cold-start
routing, `nudge-handoff-near-context-cap.sh --check` supplies the
mechanical warrant test, and `docs/cost-levers-considered.md` holds the
measured verdicts. CLAUDE.md prose restating any of it is duplication by
this repo's own rule, and the specific restatement was wrong in both
halves.

For the `/compact` bullet this reason is weaker — no live surface states
the opposite position — so reasons one through three carry that half.

### Assumption ledger

```
Root: PR #646 ships two claude/.claude/CLAUDE.md bullets (Phase 4 of
token-cost-reduction.md) prescribing /clear and /compact — actions the
agent cannot perform, advice this repo has already measured and rejected,
and for /compact a direct override of an [engineer-verified] given in the
same parent plan.

Givens:
- Prompt-cache TTL cannot exceed 1 hour by any exposed setting; an idle
  gap past it forces a full cache-write rebuild. Beyond reach: vendor-
  imposed, no repo-side control exists.
  [verified: token-cost-reduction.md:160-161, citing
  code.claude.com/docs/en/prompt-caching]
- /clear and /compact are harness built-in CLI commands with no tool
  binding; only the human at the terminal can run them. Beyond reach:
  Anthropic owns the harness command surface.
  [verified: the Skill tool's own contract states "Built-in CLI commands
  (/help, /clear, …) aren't skills"; no other tool in the session roster
  executes a slash command]
- Compaction is not an acceptable mechanism in this workflow; handoff is
  the prescribed context-reset primitive. [engineer-verified] — carried
  forward from token-cost-reduction.md:173-174 and re-affirmed by the
  engineer's PR #646 review comment.
- When a fresh session needs context the current one holds, /brief is the
  preferred vehicle over a bare /clear. [engineer-verified] — engineer's
  PR #646 inline comment on claude/.claude/CLAUDE.md.

Row 1 [mechanism]: delete both bullets from claude/.claude/CLAUDE.md —
anchors: root — deletion is the minimal primitive here; the heavier
options are the ones being declined. Heavier alternatives enumerated and
rejected: (a) reword into one agent-actionable /brief bullet — still
duplicates brief/SKILL.md's own routing and still asserts a
reset-at-boundary rule the register measured as net-negative
(origin/main:67); (b) a PostToolUse hook firing on `gh pr create` to
nudge a reset — inert for the same wrong-actor reason the bullets are,
since no tool binds /clear for an agent to run, so a precisely-triggered
nudge still cannot cause the reset it names. The register's standing
hook rejection (origin/main:66, "a hook cannot see how much work
remains") is *not* the reason here and must not be cited as one: it was
measured against a hook deciding continue-vs-handoff in general, and a
`gh pr create` trigger needs no such judgment; (c) move the guidance
into handoff/SKILL.md or brief/SKILL.md — those bodies already state the
opposite position, so the edit would be a contradiction rather than an
addition.
Row 2 [mechanism]: close Phase 4 in .claude/plans/token-cost-reduction.md
(Phases entry, Approach, Out-of-scope) — anchors: root — this repo closes
a superseded phase by editing the parent plan in place; PR #665
(commit 2955d32) is the worked template.
Row 2a [assumption]: editing that already-merged plan file is not an
Axis-3 preserved-content violation, despite
docs/cost-levers-considered.md:13-14 calling merged plan files "read-only
historical records" [verified: PR #665 / commit 2955d32 edited exactly
this file's Phases entry, Approach passage, and ledger row to close Phase
5b, and was merged — so the operative convention is that an active plan's
phase dispositions are updated in place. Axis 3's own test agrees: a
Phases entry states intended future work, which is a description of
current intent, not a record of something that happened] — anchors: row2
Row 3 [mechanism]: add a `## From token-cost-reduction.md` section to
docs/cost-levers-considered.md with two rows (Phase 4 rejected; idle-gap
cache rebuild named, not solved) — anchors: root — the register exists so
"a seventh plan doesn't re-measure ground already covered", and CLAUDE.md
session-reset prose is precisely the shape that gets re-proposed.
Row 4 [mechanism]: rewrite this branch's own plan file to drop its Phase
4 sections — anchors: root — the plan ships in the same PR as the
implementation, so a plan still prescribing Phase 4 would contradict the
diff beside it.
Row 5 [assumption]: `main` does not contain the two bullets, so the
revert is a two-line deletion with no merge complication
[verified: `git show main:claude/.claude/CLAUDE.md` — line 32 "Locate
before a whole-file read" is immediately followed by line 33 "Scope
discipline"] — anchors: row1
Row 6 [assumption]: docs/cost-levers-considered.md already records both
rejections the /clear bullet re-proposes — blanket boundary handoff, and
hook-automated reset [verified: on origin/main these are
docs/cost-levers-considered.md:66-67; the same two rows sit at :53-54 in
this branch's stale pre-sync copy, which grew from ~92 to 267 lines
across the 39-commit gap. Cite the post-sync numbers; match on row text,
not line number] — anchors: row1, row3
Row 7 [assumption]: handoff/SKILL.md already states the
continue-by-default position canonically, so no skill body needs the
guidance added [verified: handoff/SKILL.md:15-17 — "A handoff written
*only* to shed context usually costs more than continuing until the
session is actually past its threshold"] — anchors: row1
Row 8 [assumption]: markers survive both /clear and /compact; review-
narrative continuity does not [verified: claude/.claude/CLAUDE.md:98,
docs/hooks.md:63, token-cost-reduction.md:76-84] — anchors: row3 — load-
bearing because the register row must state the correct reason rather
than the marker framing.
Row 9 [assumption]: a /compact summary can fabricate an implied
authorization a resumed session reads as pre-approved, which is a
distinct harm from lossiness [verified: hook
claude/.claude/hooks/restore-authorization-boundary-on-compact.sh exists
on `main` for exactly this; it is absent from this branch's tree because
the merge-base predates it] — anchors: row3
Row 10 [assumption]: the idle-gap cache rebuild is the one cost effect
Phase 4's /compact half targeted and nothing else in the parent plan
addresses, so dropping Phase 4 orphans it [verified:
token-cost-reduction.md:431-433 — "addressed only by the Phase 4 guidance
line, not by a mechanism"] — anchors: row3
Row 11 [assumption]: the parent plan's ~910K-token / ~$9 figure for that
idle-gap rebuild is what the register row should attribute
[verified: token-cost-reduction.md:431-432 states it; the figure is cited
to its source plan, not independently re-derived this session] —
anchors: row3
Row 12 [assumption]: Phase 5a is uncontested and unaffected — PR #646
carries exactly one inline review comment, on
claude/.claude/CLAUDE.md, and `main` has not touched the guard hook since
the merge-base [verified: `gh api .../pulls/646/comments` returns one
comment at that path; `git log HEAD..origin/main --
claude/.claude/hooks/guard-settings-session-keys.sh` is empty] —
anchors: root
Row 13 [assumption]: the branch is 39 commits behind origin/main with a
conflict-free trial merge, so the sync is routine
[verified: `git rev-list --count HEAD..origin/main` = 39;
`git merge-tree --write-tree origin/main HEAD` returned a tree with no
conflict report] — anchors: root
Row 14 [engineer-verified]: drop Phase 4 entirely rather than reword it;
keep PR #646 open shipping Phase 5a alone; record the rejection in
docs/cost-levers-considered.md — anchors: root — chosen by the engineer
this session from three presented dispositions.
```

### Phase 5a — Nested `env.*` guard (unchanged, already implemented)

`guarded_value`'s `$settings | has($key)` only ever tests a top-level key
on `$settings`. A literal dotted string like
`"env.CLAUDE_CODE_EFFORT_LEVEL"` in `GUARDED_KEYS_JSON` is itself just
another top-level key name to `has()` — it never matches a value living
at `.env.CLAUDE_CODE_EFFORT_LEVEL`, so appending it as-is is a silent
no-op. `guarded_value` therefore splits the key on `.` and walks the
resulting path via `reduce`, tracking presence separately from value at
each step, so an explicit `null`/`false` mid-path stays distinguishable
from "key absent". A single-segment path degenerates to the original
check, so no existing guarded key changes behavior.

**Alternative considered and set aside:** jq's built-in `getpath`.
`getpath($path)` on a missing segment returns `null`, indistinguishable
from a genuinely-set `null` — the same ambiguity `guarded_value` was
already written to avoid for top-level keys
(`test_guarded_key_set_to_null_against_absent_denies`).

This work is committed and reviewed; this plan revision does not change
it. Its full design record, including the `GUARDED_KEYS_JSON` addition,
the four-other-keys check against Anthropic's settings and env-var
reference docs, and the eleven test cases, is unchanged from the
implementation on this branch.

## Critical files

| Path | Change |
| --- | --- |
| `claude/.claude/CLAUDE.md` | Delete the two `## Working Style` bullets (`:33-34` on this branch). The file must end byte-identical to `main`. |
| `.claude/plans/token-cost-reduction.md` | Close Phase 4. Rewrite the Phases entry (`:303-304`) to record it as rejected-not-executed with the reason; adjust the Approach compaction passage and the Out-of-scope idle-gap line (`:431-433`) so neither still points at a Phase 4 that will not ship. **This file is the site that holds the reason in full.** |
| `docs/cost-levers-considered.md` | New `## From token-cost-reduction.md` section with two rows: Phase 4's CLAUDE.md guidance (rejected) and the idle-gap cache rebuild (named, not solved). Each row is a verdict plus a one-clause measured reason naming its source plan — the register's preamble (`origin/main:17`) states it indexes source plans rather than restating them, so the argument stays in the plan file and the register points at it. State the `/compact` verdict's reason as loss of review-narrative continuity, not as loss of marker state: per Row 8, markers survive both commands and only the narrative does not. **Edit this file only after the sync (Verification step 1), and locate every anchor by matching row text rather than by line number:** it took three upstream edits in the 39-commit gap (#669, #676, #677) and roughly tripled in length, so every line number this plan cites for it is pre-sync. Its preamble wording also changed — a section whose lever was closed without a source plan now names the session date instead — so mirror the current phrasing, not this branch's copy. |
| `.claude/plans/context-hygiene-and-nested-env-guard.md` | This file — already rewritten. |
| `claude/.claude/hooks/guard-settings-session-keys.sh`, its test file, `docs/hooks.md` | **No change.** Phase 5a ships exactly as committed. |

**Reuse:** commit `2955d32` (PR #665, "Close Phase 5b") is the worked
template for a phase closure — it edits the Phases entry, the Approach
passage, and the ledger row in one commit, and nothing else. Follow its
shape. `docs/cost-levers-considered.md`'s existing tables supply the
column layout and the verdict vocabulary (`Rejected as mispriced`,
`Named, not solved`, `Declined deliberately, kept in reach`); reuse those
terms rather than inventing new verdicts.

**Naming:** the branch and this plan file keep the
`context-hygiene-and-nested-env-guard` slug. Renaming an open PR's branch
to match a narrowed scope costs the PR's review history for a cosmetic
gain; the PR title and body carry the corrected scope instead.

## Verification

1. Sync the branch onto current `origin/main` first (39 behind, trial
   merge clean) via `git-feature-branch-sync`. This runs **before** every
   check below, so no check reads a tree a later step rewrites.
2. `git diff origin/main...HEAD -- claude/.claude/CLAUDE.md` produces
   **empty output** — the direct proof the revert restored the file
   rather than approximating it.
3. `../../../.venv/bin/pytest claude/.claude/ -k GuardSettingsSessionKeys -q`
   — the guard's 35 cases still pass untouched.
4. `../../../.venv/bin/pytest claude/.claude/ -q` — full suite.
5. `../../../.venv/bin/ruff check claude/.claude/`.
6. `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck`.
7. Manual guard check: stage a `claude/.claude/settings.json` carrying
   `{"env": {"CLAUDE_CODE_EFFORT_LEVEL": "high"}}` against a `main`
   lacking it, attempt `git commit`, confirm the deny; confirm an
   unrelated `env` key still commits.
8. `/code-review` on the cumulative PR-vs-`main` diff. The diff touches
   `claude/.claude/CLAUDE.md` and two docs, so
   `ai-instruction-and-memory-files` and `comment-discipline-reviewer`
   are in its dispatch set; confirm both actually run.
9. `/pr-description` to rewrite the PR body for the narrowed scope
   (Phase 5a only).
10. `/ready-for-review`, then `/respond-pr` to reply to the inline
    comment and the review body.

## Out of scope

- **Any replacement for Phase 4** — in CLAUDE.md, in a skill body, or as
  a hook. The engineer chose to drop it outright; the three replacement
  shapes are enumerated under ledger Row 1 with the evidence against each.
- **Solving the idle-gap cache rebuild.** It is real and now
  unaddressed. The 1-hour TTL is vendor-capped and only the human decides
  when a session idles, so nothing agent-side can close it. Recorded as
  "Named, not solved" rather than left implicit.
- **Reopening the auto-compaction threshold.** Declined by the parent
  plan on an `[engineer-verified]` given; nothing here revisits it.
- **Stating the compaction position on a live surface.** After this
  change no skill, hook, or instruction file tells an agent that handoff
  rather than compaction is this repo's reset primitive — the position
  exists only in `token-cost-reduction.md:173-174` and in the register
  row this plan adds. Named rather than left implicit, and deliberately
  not acted on: the engineer's decision was to drop Phase 4 and add no
  replacement guidance, and writing this position into CLAUDE.md or a
  skill body would be a replacement.
- **Any change to `guard-settings-session-keys.sh`, its tests, or
  `docs/hooks.md`.** Phase 5a drew no review objection and `main` has not
  moved underneath it.
- **Renaming the branch or this plan file** to match the narrowed scope.
- **`token-cost-reduction.md`'s dangling Reuse-note clause** — "reconcile
  the compaction threshold with it" (`origin/main:340`) points at a
  compaction threshold that plan's own Out-of-scope declined to change.
  Pre-existing drift, unrelated to Phase 4's manual-`/compact` bullet, so
  dropping Phase 4 neither creates nor resolves it. Raised for the
  reviewer rather than fixed here.
- **Phase 5b and Phase 6** of the parent plan. Neither is touched here.
  5b was closed by PR #665. Phase 6 is **still open** — PR #617 narrowed
  its scope (it must not recreate `docs/cost-ledger.md`) but did not
  close it, and it remains blocked on the `cost-trend-ledger` prerequisite
  decision. [verified: `git show origin/main:.claude/plans/token-cost-reduction.md`
  — `:318` still reads "**Phase 6 — Measurement.**" unmodified, against
  `:312-316` where Phase 5b was rewritten to "Superseded, not executed."]
  No artifact this change writes may describe Phase 6 as dropped.
