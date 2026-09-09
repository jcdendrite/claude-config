# Ground the passive-notification mechanism (Phase 1 of a 3-phase effort)

## Status

Phase 1 is implemented and committed on this branch (`369fa30`, following the
plan commit `85eac0b`). This plan file — and the PR built from this branch —
now cover **Phase 1 only**. Phases 2 (the machine-wide sleep-poll measurement)
and 3 (the case study) move to a follow-on branch; see "Deferred: Phase 2 and
3" below for why and what carries forward.

The no-polling norm for a session's own backgrounded Bash work is grounded
independently of the sub-agents-page mechanism, via Anthropic's Week 15
(2026-04-06–10) release digest and `CHANGELOG.md` v2.1.246.
`docs/design-decisions/passive-notification-over-polling.md` cites both
alongside the sub-agents-page mechanism. No new CLAUDE.md/SKILL.md rule text
is added — the vendor-owned tool schema already enforces the own-Bash case
uniformly every turn, so a repo-level rule restating it would duplicate an
already-enforced mechanism rather than close a real gap.

## Context

The repo's rule that a session waits on background work by passive
notification rather than polling was stated on three surfaces with no
external citation, and its one quantitative support (PR #923, merged as
`4693784`) is documented as non-generalizing. This phase grounds the
passive-notification **mechanism** in primary sources so the rule stops
resting on self-reference.

## Approach

Ground the passive-notification mechanism in one new design-decision entry
that cites two first-party sources — the public sub-agents page for a
dispatched subagent, and the Bash tool description's own `run_in_background`
sentence for a session's own backgrounded command — and that states in as
many words that the sub-agents page prohibits no polling. It asserts nothing
about what the Bash tool description prohibits — row 29 establishes that
description varies by agent type, so only its mechanism sentence is citable.
Point at that entry from `subagent-delegation/SKILL.md` alone, leaving
`claude/.claude/CLAUDE.md` and `handoff/SKILL.md` untouched.

Rows 31 and 33 in the Mechanisms table below are this section's canonical
record of which source grounds which claim and how the entry's disclaimer is
worded — not restated here.

**What this plan deliberately does not ground.** A plan-review finding asked
this plan to ground a repo *norm* against Bash sleep-polling. No such norm
exists to ground: `claude/.claude/CLAUDE.md` contains neither the token
`sleep` nor `poll` anywhere in its 185 lines, and `handoff/SKILL.md:43`'s "do
not poll and do not call `TaskOutput`" is scoped to subagent dispatches and
pinned there by `test_skills.py:1440-1442`. The finding's residue is taken:
the Bash tool description was genuinely an unchecked candidate, and its
mechanism half is adopted as row 28.

**On the duplication and the citation's audience.** The Context section's
goal reads reader-agnostically, and it is not:

- Don't relitigate the duplication — `no-op-dispatch-guard.md:20` names the
  exception correctly.
- This citation's reader is a maintainer auditing why the rule exists, not a
  mid-dispatch session.
- That follows from `no-op-dispatch-guard.md:5`'s own account of why the
  CLAUDE.md copy exists: to reach a session at a moment that does not look
  like a delegation decision, so it stops the behavior rather than
  justifying it.
- `subagent-delegation` already owns the delegation-cost-reasoning half of
  the documented split, and grounding *why* passive notification works is
  reasoning.
- One pointer lands on that surface, below the identical run, touching
  neither copy.

**Root problem.** The rule was uncited on all three surfaces that carry it,
and its one quantitative support is documented as non-generalizing, so both
rest on self-reference.

## Deferred: Phase 2 and 3

Phase 2 (the machine-wide sleep-poll measurement) and Phase 3 (the case study
it might justify) move to a follow-on branch, started only once
`narrow-provenance-redaction-rule` merges to `origin/main` or is abandoned —
confirmed still open and unmerged as of this session (`origin/main:CLAUDE.md`
still contains "inherits the private half"; that branch is separately in
progress under session `narrow-provenance-redaction-rule-f4`). Phase 2's
publishable content depends entirely on which redaction rule ends up in
force, so its disclosure policy is designed once, against whichever rule is
actually in force, when its own follow-on branch starts.

Two things from the discarded apparatus carry forward rather than needing
re-derivation when that follow-on plan is written:

- **Whatever Phase 2's design turns out to be, a withhold-path artifact
  records the outcome label and scope metadata only — no count, ratio, or
  percentage derived from transcript content.** A threshold chosen by looking
  at the data is such a number; so is a bare `n`; so is the share itself.
- **A per-account, per-project, or per-engagement breakdown is never
  published, under any rule.** Not in an amendment, a case study, a commit
  message, a PR body, an issue, or a plan file, and not offered as an option.
  This is an absolute, not a factor to weigh — a decomposition is invertible
  toward a specific engagement in a way a pooled ratio is not, and no version
  of the redaction rule under discussion touches it.

### Givens

| # | Given | Why it is beyond this plan's reach | Tag |
|---|---|---|---|
| 1 | `code.claude.com/docs/en/sub-agents` describes the completion-notification mechanism and contains no instruction not to poll. | Vendor-owned page; this repo cannot make it say more. | `[verified: WebFetch against that URL, this session]` |
| 2 | `ScheduleWakeup`'s prohibition lives only in a tool description pinned to Claude Code 2.1.258, with no citable URL. | Vendor-owned and version-bound. | `[verified: docs/design-decisions/schedulewakeup-misapplied-documented.md:7]` |
| 9 | The overlapping paragraph in `claude/.claude/CLAUDE.md:78-83` and `subagent-delegation/SKILL.md:56-66` is deliberately identical so `git grep` exposes drift. Its enforcement is that manual grep, not a test — the entry itself records that no mechanical enforcer exists. | A shipped design decision with its own record; overturning it is a separate plan. | `[verified: docs/design-decisions/no-op-dispatch-guard.md:20,22]` |
| 10 | `claude/.claude/CLAUDE.md` is 185 lines against `check-claude-md-length.sh`'s 200-line limit. | Vendor-grounded threshold encoded in a hook-enforced gate. | `[verified: line count and `limit_for()`, this session]` |
| 11 | `docs/design-decisions/` holds exactly 63 files, each carrying an italic provenance line on line 3. Their `Formerly §N` values form a closed, contiguous {1..63}; the monolith is retired, so that set can never grow. 48 of the 63 lines carry a leading ISO-8601 date; 15 carry none — §1–§12, §15, §18, §19. | Historical fact of the completed split. | `[verified: line-3 provenance line grepped in all 63 files, §N values extracted and counted, dateless count confirmed at 15, this session]` |
| 28 | The Bash tool's own description states that a command launched with `run_in_background` "keeps running across turns and re-invokes you when it exits." That sentence already has exactly one in-repo copy, recorded verbatim under its own `[verified]` tag. | Vendor-owned and version-bound, with no citable URL — the same class as row 2. | `[verified: .claude/plans/background-slow-bash-calls.md:95-97]` |
| 29 | The Bash tool description is **not uniform across agent types**, so no claim about what it does or does not prohibit is safe to publish. The dispatching session's schema says foreground `sleep` is blocked and directs a condition-wait to `Monitor` with an until-loop, and contains no no-polling instruction; a dispatched reviewer reported its own schema carrying an explicit one. Only row 28's `run_in_background` re-invocation sentence — the part both observed schemas agree on — is citable. | Vendor-owned, version-bound, and now known to vary by agent type; only a session holding a live Bash schema can read its own, and no session can read another's. | `[verified: this session's own Bash tool schema, plus a dispatched `staff-product-engineer`'s contradicting report of its own, both this session]` |

### Mechanisms

| # | Mechanism | Justification | Anchor |
|---|---|---|---|
| 13 | A new entry `docs/design-decisions/passive-notification-over-polling.md`, carrying the verbatim sub-agents-page mechanism quote, the disclaimer sentences drafted in row 33, a `[§41]` citation for the `ScheduleWakeup` prohibition, a citation to row 28's in-repo copy for the own-Bash mechanism, and a `## Sources` block. | The repo's documented home for a non-obvious choice plus external citation; 17 of 63 entries carry an external URL, and this exact URL already appears in four. It is the only surface whose scope spans CLAUDE.md, `subagent-delegation`, and `handoff`. | `anchors: root` |
| 14 | **Over-powered-primitive check for row 13.** Four lighter primitives fail: (a) a `## Sources` bullet appended to `[§41]` or `[§49]` — rejected by `citation-genre-mismatch.md:5`, which holds that citing an entry containing no discussion of the reasoning is a misattribution, and both are dated records (one superseded) whose substance is read-only under CLAUDE.md Axis 3; (b) an entry in `subagent-delegation/REFERENCES.md` — skill-scoped and edit-time-only, leaving two of three rule surfaces uncited and inviting a second copy; (c) an inline URL on the CLAUDE.md bullet — it breaks row 9's grep, and all six citation-carrying bullets there cite `docs/<file>.md` rather than external URLs; (d) a pointer sentence appended *after* the CLAUDE.md span, the shape row 15 uses for SKILL.md — the two spans are not the same shape. `SKILL.md:56-66` is a standalone paragraph block closed by a blank line at `:67` before a new bolded paragraph at `:68`, so a following sentence is a sibling paragraph. `CLAUDE.md:78-83` is one bullet in the Agent Briefing rule list, followed immediately by another rule bullet at `:84`, so "after the span" is either a new list item — a rule-shaped line stating no rule, in a budget every session pays every turn — or an append inside `:83`, which is part of the identical text. | Rejecting (a) also dissolves the `test_hook_alignment.py:430,451,456` hazard by construction rather than defending against it. (d)'s rejection is structural and checked, not a headroom preference. | `anchors: row13` |
| 15 | One pointer sentence in `subagent-delegation/SKILL.md`, placed after the identical run at :56-66, not inside it. | Without a pointer the entry is reachable only by `git grep` (the directory has no index by design), so the rule would still read as self-referential to its reader. One pointer on the reasoning surface is the minimum that meets the goal without touching row 9's grep or row 10's budget. | `anchors: row13` |
| 16 | Fix `test_design_decision_files.py`'s provenance check before creating the entry, in two separable parts. **(a) Per-file shape:** line 3 is an italic provenance line — a regex matching `*…*` and not `**…**` — carrying an ISO-8601 date, a `Formerly` clause, or both; a file with no `Formerly` clause must carry a date; any date present must parse via `datetime.date.fromisoformat`. **(b) Corpus closure:** the recorded `Formerly §N` values equal exactly `frozenset(range(1, 64))`, exposed as a keyword parameter defaulting to that constant. Absence of a `Formerly` clause stops being a violation on its own — (b) is what catches a dropped or fabricated clause. Also update `.claude/rules/design-decisions.md` (closed-set clause only) and `docs/design-decisions.md:5`. | A live, unfixed conflict, not scope creep: the rule file says a post-split file carries no `Formerly §N` clause, while `_legacy_number_range_violations` flags any file lacking one. Required to make the ticket's change correct (CLAUDE.md Axis 1). Part (a) is corpus-true as written — all 63 line-3 lines are italic, and every dateless file carries a `Formerly` clause (row 11) — so it grandfathers the 15 dateless files by construction rather than by carve-out, and forces no fabricated historical dates. A blanket date-led requirement would have failed against those 15 on day one. Part (b) is strictly stronger than the derived-max form, which would silently accept losing §63. | `anchors: row13`, `anchors: row11` |
| 27 | The parent session — not `code-writer` — runs `/skill-review` for `subagent-delegation/SKILL.md`'s edit. | `require-skill-review.sh` blocks `git commit` on a SKILL.md change until the marker is written, and `code-writer` cannot run review skills or write markers; it would report the denial and stall. | `anchors: row15` |
| 31 | The entry grounds the own-Bash mechanism by citing row 28's existing in-repo copy — `.claude/plans/background-slow-bash-calls.md:95-97` — not by re-quoting the sentence. | Row 2's reasoning bars creating a *second* copy of a version-bound string with no URL to re-check it against; the Bash sentence has exactly one copy today, so citing keeps the count at one. The genre check passes: that plan file discusses the mechanism in its own rows, so `citation-genre-mismatch.md:5` is not triggered. Citing a committed plan file as a source is established — `schedulewakeup-misapplied-documented.md:45` does exactly this. | `anchors: row13`, `anchors: row28`, `anchors: row2` |
| 32 | The citation's audience is maintainer-only by design, stated as such in the entry's own opening and in the PR body. No pointer lands in `claude/.claude/CLAUDE.md`. | `no-op-dispatch-guard.md:5` says the CLAUDE.md copy exists to reach a session at a moment that does not look like a delegation decision — its job is to stop the behavior, not to justify it. A citation serves an auditor, who reads the entry. Row 14(d) shows the after-the-span pointer is not structurally available on that surface anyway. Stating the audience is what keeps the Context section's reader-agnostic goal from over-promising. | `anchors: row9`, `anchors: row10`, `anchors: row15` |
| 33 | The entry's disclaimer is drafted here, verbatim, not deferred to execution. After the sub-agents quote: **"This page documents the notification mechanism only. It states no rule against polling. The no-polling rule below is this repo's own, grounded in its cost register rather than in this source."** After the own-Bash citation: **"The Bash tool description documents the same re-invocation mechanism for a session's own backgrounded command."** That sentence stops there: row 29 establishes the description varies by agent type, so the entry asserts nothing about what it prohibits. | The entry may state what a source says and not what it omits, unless the omission is checkable in a stable artifact. A URL-backed page is checkable; a per-agent-type tool description is not. Drafting the sentence here rather than at execution time is what puts it inside `/plan-review`'s reach. | `anchors: row13`, `anchors: row29` |
| 35 | Phase 1's commit message states that the provenance-check fix is a precondition for the new post-split file rather than a drive-by test change. The PR body carries row 32's audience statement. | A reviewer seeing hook-test changes in a "ground a citation" commit has no signposting toward row 16 unless they read this plan file. Row 32 promises the audience statement reaches the PR body, and nothing else instructs `/pr-description` to carry it — an unenforced promise is not a disclosure. | `anchors: row16`, `anchors: row32` |
| 37 | `.claude/rules/design-decisions.md` gains one clause naming the closed legacy set and its upper bound (63), and a test pins that stated number to `max(_CLOSED_LEGACY_NUMBERS)`, mirroring `test_rule_file_filename_grammar_matches_enforced_regex`. No date grammar is stated in rule prose and none is pinned. | The frozen constant is the one thing a future editor would restate in prose and let drift, and the existing filename-grammar pin is the proven shape for that. The date grammar has no prose statement to drift from — the Format bullet gives an example, not a regex — so pinning it would mean adding prose solely to have something to pin. The test's own docstring carries the grammar instead. | `anchors: row16`, `anchors: row11` |

## Critical files

**Dispatch A (`code-writer`, `model: sonnet`), ran first.** Provenance-grammar
fix; had to land before the new entry could exist.

- `claude/.claude/hooks/tests/test_design_decision_files.py` — modified. Split
  the provenance invariant per row 16 into two functions: a per-file shape
  check (line-3 italic provenance line; ISO-8601 date, `Formerly` clause, or
  both; a file with no `Formerly` clause must carry a date; any date validated
  with `datetime.date.fromisoformat`) and a corpus-closure check taking
  `expected_legacy_numbers: frozenset[int] = _CLOSED_LEGACY_NUMBERS` as a
  keyword parameter. Updated module-docstring invariant 2. The italic-line
  regex rejects `**bold**` — `ui-notification-defaults-in-stow-source.md:31`
  and `attribution-in-skill-prose-and-hook.md:14,32,38` are live body lines
  that a loose `^\*.*\*$` would match.
  Seven `TestFaultInjection` cases, each a one-fixture `tmp_path` corpus in
  the file's existing idiom: (1) a post-split file with a bare date line and
  no `Formerly` clause is accepted; (2) a file with a `Formerly` clause and no
  date is accepted — the shape 15 real files have; (3) a file with no
  provenance line at all is flagged; (4) a malformed date (`2026-13-45`) is
  flagged; (5) dropping the highest legacy number is flagged; (6) two files
  claiming the same `§N` is flagged; (7) a file stamped `§64` is flagged.
  `test_legacy_numbers_detects_gap` changed with the signature: it passes
  `expected_legacy_numbers=frozenset({1, 2, 3})` against its existing `{1, 3}`
  corpus and asserts the message names the missing `2`.
- `.claude/rules/design-decisions.md` — modified. Added one clause to the
  **Format** bullet naming the legacy set as closed at §63.
- `docs/design-decisions.md` — modified line 5, which previously asserted
  every file records a migrated section number.
- Added the rule-prose pin from row 37 alongside
  `test_rule_file_filename_grammar_matches_enforced_regex`.

*Commit message:* per row 35, states that the provenance-check fix is a
precondition for the new post-split file.

**Dispatch B (`code-writer`, `model: sonnet`), ran sequential after A.**

- `docs/design-decisions/passive-notification-over-polling.md` — created.
  Provenance line `*2026-09-07.*`. Body carries: the audience statement from
  row 32; the verbatim sub-agents-page sentences; the first disclaimer trio
  from row 33 immediately after them; a
  `[§41](schedulewakeup-misapplied-documented.md)` citation for the
  `ScheduleWakeup` prohibition rather than a second copy of that quote; a
  citation to `.claude/plans/background-slow-bash-calls.md:95-97` for the
  own-Bash `run_in_background` sentence, with row 31's one-clause reason for
  citing rather than re-quoting; the second disclaimer pair from row 33 after
  it; and a **Revisit** condition firing if the sub-agents page stops stating
  that a completion notification reaches Claude in a later turn.
- `claude-skills/skills/subagent-delegation/SKILL.md` — modified. One pointer
  sentence inserted at line 67, between the identical paragraph ending at :66
  and `**No permission cost.**` at :68 — never inside the identical run.

*Parent-only step:* `/skill-review` on the `subagent-delegation/SKILL.md`
diff (hook-enforced; `code-writer` cannot run it).

## Verification

Per repo `CLAUDE.md`: run `select-tests.py`, not the full suite.

```
.venv/bin/python3 claude/.claude/scripts/select-tests.py
```

This one command is the whole verification step. It derives its own
changed-path set from `compute_changed_paths` (HEAD versus the `origin/main`
divergence point, plus every dirty and untracked working-tree path), prints
one `select-tests: running <targets>` line to stderr, and then invokes pytest
on those targets itself. It takes no path arguments and needs none.

**What it selects, verified.** Run against dispatch A's three changed paths,
`select_pytest_targets()` returns `is_full_suite: False`, `reason:
domain-selected`, and six resolved targets: `claude-skills/skills/tests`,
`claude/.claude/hooks/tests`, `claude/.claude/scripts/tests/test_select_tests.py`,
and the three `claude/.claude/scripts/tests/test_transcript_analysis*.py`
files. The domain directory `claude/.claude/hooks/tests` is correctly
selected — `resolve_target_paths`'s containment filter drops the *contained
file* in favor of the directory, which is the safe direction. The GH-882
under-collection shape does not manifest for this diff.

**Do not hand-reconstruct a fallback.** Most of those targets arrive through
`CROSS_DOMAIN_EXCEPTIONS` rather than the domain rule: `_is_test_source_change`
selects `test_select_tests.py`, and `_is_hooks_or_skills_change` selects the
transcript-analysis tests — both firing precisely because the touched file is
itself a test file under `claude/.claude/hooks/`. A hand-written `pytest
claude/.claude/hooks/tests claude-skills/skills/tests` misses every one of
them. Read the selection the tool prints; do not reconstruct it. If the
printed selection ever omits a domain the diff genuinely touches, that is a
bug in `select-tests.py`'s rule table to report, not a licence to widen the
run by hand (repo `CLAUDE.md`, Commands).

**Dispatch A's real-corpus precondition is met by that command, not by an
extra step.** `test_legacy_numbers_form_contiguous_range` and the new shape
check both read `DESIGN_DECISIONS_DIR` directly, and both live under
`claude/.claude/hooks/tests`, which the selection includes. So the standard
run exercises the new invariant against all 63 live files before dispatch B
starts.

**Lint:**

```
.venv/bin/ruff check claude/.claude/ claude-skills/
```

No shell files are touched, so `scripts/list-shell-files.sh | xargs -0
.venv/bin/shellcheck` is not required.

**Gates:** `/code-review` before each commit; `/skill-review` on dispatch B
(hook-enforced); `ai-instruction-and-memory-files` on dispatch A's rule-file
edit (dispatched by `/code-review`); `/ready-for-review` before push;
`/pr-description` carries row 35's audience-statement disclosure. No plugin
directory is touched, so `plugin-semver` is not implicated.

## Out of scope

- **Editing `schedulewakeup-misapplied-documented.md` or
  `schedulewakeup-denied-by-bare-tool-name.md`.** Both are dated records, one
  superseded, and `citation-genre-mismatch.md:5` rejects retrofitting
  reasoning into an entry that does not discuss it. Leaving them untouched
  also avoids silently orphaning the §49 prose quoted in
  `test_hook_alignment.py:430,451,456` assertion messages, which would fail
  no test.
- **Re-quoting the Bash tool description in the new entry.** Row 31 cites its
  single existing in-repo copy instead. Two copies of a string with no URL to
  re-check them against is the drift pair row 2 exists to prevent.
- **Adding a repo rule against Bash sleep-polling, on any surface.** No such
  rule exists today — `claude/.claude/CLAUDE.md` contains neither `sleep` nor
  `poll`, and `handoff/SKILL.md:43`'s prohibition is scoped to subagent
  dispatches. Writing one is a separate decision, not a precondition for
  grounding the mechanism.
- **Relitigating the CLAUDE.md / `subagent-delegation` duplication, or adding
  any citation to `claude/.claude/CLAUDE.md`.** Could be changed; deliberately
  is not. Reasons in rows 9, 10, 14(d), and 32 — and row 32 states the
  audience narrowing explicitly rather than leaving it implied.
- **Editing `handoff/SKILL.md:43`.** It already states both the mechanism and
  the norm correctly for subagent dispatches, pinned by
  `test_skills.py:1440-1442`. A third pointer would be the compounding-layers
  shape rather than added grounding.
- **A `subagent-delegation/REFERENCES.md` entry for this citation.** The
  verbatim quote and the citations live in the decision entry's body; a
  second home is duplication.
- **Adding an index to `docs/design-decisions/`.** Deliberately absent per
  `.claude/rules/design-decisions.md`.
- **Narrowing `CLAUDE.md:157` in this branch.** That rule change is the
  separate `narrow-provenance-redaction-rule` branch, with its own review.
  This branch neither depends on nor blocks its outcome.
- **Designing Phase 2's measurement or disclosure mechanics in this plan.**
  See "Deferred: Phase 2 and 3" above — that design work starts on a
  follow-on branch once the redaction rule's fate is known, not before.
