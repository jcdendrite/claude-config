# Multi-fact comment-structuring guidance

## Context

**Goal:** give CLAUDE.md's §Code Comments, Documentation, and Prose section, and `comment-discipline-reviewer`'s checklist, an explicit rule for how to structure a comment that must state several distinct non-obvious facts at once — today both surfaces speak only to overall length ("One line, not a paragraph"), with nothing addressing how a legitimately multi-fact comment should be organized.

The gap surfaced during review of an external PR, where a human reviewer flagged several doc comments as unreadable despite `comment-discipline-reviewer` having already reviewed them and found no violation of the existing length rule. All three flagged comments were later rewritten and confirmed as better by the same reviewer; each rewrite kept roughly the same fact count but split it across separated sentences, or an explicit list for a run of parallel items, instead of chaining every fact into one sentence-cluster via semicolons, dashes, and parentheticals.

Investigating that pattern against this repo's own history turned up stronger, in-repo corroboration than the triggering PR alone: this repo already invented and shipped the same fix twice, independently, for two narrower surfaces — `claude-hook-review`'s hook-header checklist and `claude/.claude/rules/shell-script-conventions.md` — but never generalized it to CLAUDE.md's own core comment rule or to `comment-discipline-reviewer`'s checklist, which is what the brief for this plan asked to close. One of those two prior fixes (`shell-script-conventions.md`) already reads "Match CLAUDE.md's comment-length convention" — a convention CLAUDE.md does not currently state in that form, so this plan also resolves a preexisting dangling cross-reference as a side effect.

## Approach

Add the rule as a new bullet in CLAUDE.md's §Code Comments section, sibling to "One line, not a paragraph," and give `comment-discipline-reviewer` a matching sixth checklist angle to enforce it — preserving the file's existing 1:1 angle-to-bullet mapping (five bullets in that section today, five checklist angles) rather than folding the new rule into either existing item. The two already-duplicated enumeration sites in `code-review/SKILL.md` that spell out the agent's angle list get the new angle name propagated too, so they don't go stale.

**Rule frame:** not "always split into N single-fact sentences," and not the plain "short sentences or a list" split from the initial hypothesis. A closer read of the three triggering rewrites shows they group by coupling: independent facts each get their own sentence; genuinely parallel facts (a run of gaps, conditions, or exclusions of the same kind) get an explicit one-item-per-fact list instead of being nested as asides; facts that are tightly coupled — a cause and its direct effect — may still share a sentence. This is a principle, not a numeric threshold, matching this CLAUDE.md section's existing style (no sibling bullet carries a fact-count trigger either).

### Assumption ledger

```
Root: comments/durable docs that must state several independent facts get
  crammed into one dense run-on (semicolons/dashes/parentheticals) because
  CLAUDE.md's only relevant rule ("One line, not a paragraph") and
  comment-discipline-reviewer's mirroring angle both push toward single-line
  compression, with nothing addressing the case where one line can't hold
  the facts.

Given 1: CLAUDE.md's 200-line cap is hook-enforced (check-claude-md-length.sh)
  — a fixed budget this plan works within, not something it relaxes.
Given 2: this repo is public; evidence cited here or in shipped guidance must
  not identify the private PR/repo/reviewer that surfaced the pattern —
  CLAUDE.md's own redaction rules impose this, not a choice this plan makes.

Row 1 [mechanism]: new CLAUDE.md bullet under §Code Comments > "When to write
  it and what to include," sibling to "One line, not a paragraph" — anchors:
  root. States the rule once, canonically, in the section's existing
  one-bullet-per-atomic-rule style. Lighter alternatives rejected: (a)
  extend "One line, not a paragraph" in place instead of adding a bullet —
  rejected because that bullet's fix ("trim the narration, not the fact")
  targets over-elaboration of one fact, the opposite failure from
  under-separation of several facts, and merging the two reads as
  self-contradictory without a full rewrite of the existing bullet; (b) rely
  on the two already-shipped narrower instances alone (claude-hook-review,
  shell-script-conventions.md) with no CLAUDE.md change — rejected because
  neither covers comments outside shell/hook files, the case the triggering
  examples are drawn from, and shell-script-conventions.md's bullet already
  points at a CLAUDE.md convention that doesn't yet exist in this form.
Row 2 [mechanism]: new sixth checklist angle "Multi-fact comment structure"
  in comment-discipline-reviewer.md (description, angle block, "five" →
  "six" count, violation-type enum) — anchors: row1. Needed because the
  existing "Comment verbosity" angle's prescribed fix (one-line compression)
  is the wrong move for a multi-fact comment, so without a distinct angle
  the reviewer has no correct instruction to apply at that site [finding
  from a delegated Opus analysis this session]. Lighter alternatives
  rejected: (a) fold the new rule into "Comment verbosity" instead of adding
  a sixth angle — same over-collapse conflict as Row 1(a); (b) no text
  change, relying on the existing angle to pick it up implicitly — rejected
  because this repo already measured that exact failure: this repo's own
  `.claude/plans/comment-verbosity-root-cause.md` records CLAUDE.md's "One
  line, not a paragraph" bullet producing no trigger at either review pass
  until an explicit checklist angle named it.
Row 3 [mechanism]: propagate the new angle name into code-review/SKILL.md's
  two existing enumerations of comment-discipline-reviewer's angle list
  (item 12a, the Change-type table) — anchors: row2. Both sites already
  spell out the same five-angle list the agent carries — a pre-existing
  duplication CLAUDE.md's own "Single source of truth" bullet accepts for
  lazy-loaded, standalone instructional prose — so leaving them at five once
  the agent has six turns them into an inaccurate description of scope.
  Lighter alternative rejected: leave both unedited since dispatch routing
  doesn't depend on the enumerated count — rejected because their job is to
  describe scope to a human reading SKILL.md without opening the agent
  file, and letting that silently understate scope is the "duplicated
  copies drift" failure CLAUDE.md warns against.

Row 4 [verified: read claude/.claude/CLAUDE.md and
  claude/.claude/agents/comment-discipline-reviewer.md this session]:
  neither file currently addresses multi-fact comment structuring; both
  speak only to overall length.
Row 5 [verified: git show on the commits that shipped
  claude-hook-review's header-category bullet and
  shell-script-conventions.md's comment bullet, read this session]: the
  "one fact per sentence, list for parallel facts" idea already shipped
  twice for narrower surfaces but was never generalized to CLAUDE.md's core
  rule or to comment-discipline-reviewer.
Row 6 [verified: grep shell-script-conventions.md this session]: its bullet
  reads "Match CLAUDE.md's comment-length convention," which CLAUDE.md does
  not currently state in that form — a preexisting dangling cross-reference
  this plan resolves as a side effect of Row 1.
Row 7 [unverified]: pulling additional review-comment history from the
  triggering private PR beyond the three examples already in hand would add
  more corroboration, but was judged unnecessary given the independent
  in-repo corroboration in Row 5; not pulled this session.
Row 8 [engineer-verified]: rule frame is "group by coupling" (independent
  facts separated; parallel facts listed; tightly-coupled facts may share a
  sentence), not the plain split-or-list framing from the initial
  hypothesis.
Row 9 [engineer-verified]: no numeric fact-count threshold — principle-based
  guidance only, matching this section's existing style.
Row 10 [engineer-verified, via a delegated Opus analysis this session]: the
  rule lives on both surfaces (CLAUDE.md canonical, comment-discipline-
  reviewer.md enforcement), not either alone — grounded in Row 2's finding
  that a CLAUDE.md-only version of this exact rule already measured as
  producing no review-time trigger.
```

**Dispatch split:** single `code-writer` dispatch. All three files are edits to one conceptual addition (one new rule, propagated to its two duplicate-enumeration sites) with no independent verification surface per file — splitting would mean restating the same rule text and rationale in every dispatch prompt.

## Critical files

- `claude/.claude/CLAUDE.md` — add one bullet to §Code Comments, Documentation, and Prose > "When to write it and what to include," immediately after "One line, not a paragraph":

  > **Split multi-fact comments.** State each non-obvious fact as its own sentence rather than chaining several into one run-on via semicolons, dashes, and parentheticals — a reader shouldn't have to parse a whole sentence-cluster to find where one fact ends and the next begins. When the facts are genuinely parallel (a set of gaps, conditions, or exclusions of the same kind), use an explicit list, one item per fact, instead of nesting them as asides in unrelated prose. Facts that are tightly coupled — a cause and its direct effect — may still share a sentence.

- `claude/.claude/agents/comment-discipline-reviewer.md`:
  - Frontmatter `description` (line 5): insert ", multi-fact comment structure" after "comment verbosity" so the sentence reads "Focus on comment verbosity, multi-fact comment structure, prose at the wrong altitude for its reader, PR-defined terminology, ...".
  - New angle block in "Core review angles," after "Comment verbosity":

    > **Multi-fact comment structure** — several independent, non-obvious facts chained into one sentence-cluster via semicolons, dashes, and parentheticals, so a reader must parse the whole cluster to find where one fact ends and the next begins. Flag the site and name the fix: a separate sentence per independent fact, or an explicit one-item-per-fact list when the facts are genuinely parallel (a set of gaps, conditions, or exclusions). Facts that are tightly coupled — a cause and its direct effect — staying in one sentence is not a violation.
  - "How to work" step 2: "all five angles above" → "all six angles above."
  - Output format, "Violation type" enumeration: add "/ Multi-fact comment structure."

- `claude/.claude/skills/code-review/SKILL.md` — propagate the new angle name into both existing enumerations of comment-discipline-reviewer's angle list:
  - Item 12a (~line 108): insert "multi-fact comment structure (several independent facts chained into one run-on instead of split or listed), " after "comment verbosity (a multi-paragraph rationale where one line suffices), " and before "prose at the wrong altitude for its reader".
  - Ripple-effect-triage Change-type table row (~line 248): insert "multi-fact comment structure, " after "comment verbosity, " and before "wrong-altitude prose".

Reuse: no new mechanism anywhere — this generalizes a rule this repo already proved out twice (`claude-hook-review`'s header-category bullet, `shell-script-conventions.md`'s comment bullet) into the two surfaces the brief named, and mirrors the existing five-angle/five-bullet structure exactly rather than inventing a new organizing scheme.

## Verification

1. `grep -n "Split multi-fact comments" claude/.claude/CLAUDE.md` shows the new bullet; `wc -l claude/.claude/CLAUDE.md` stays under the 200-line cap.
2. `grep -n "Multi-fact comment structure" claude/.claude/agents/comment-discipline-reviewer.md` shows it in the description, angle block, and violation-type enum; `grep -n "all six angles" claude/.claude/agents/comment-discipline-reviewer.md` confirms the count.
3. `grep -n "multi-fact comment structure" claude/.claude/skills/code-review/SKILL.md` shows both propagated sites.
4. `../../../.venv/bin/pytest claude/.claude/` and `../../../.venv/bin/ruff check claude/.claude/` stay green — no executable code changes, but the hook/agent-roster and skill-alignment tests read these files' structure.
5. `/agent-review` on `comment-discipline-reviewer.md` and `/skill-review` (hook-enforced) on `code-review/SKILL.md`, per `.claude/rules/review-pipeline-dispatch.md`; `/code-review` on the full staged diff before commit.

## Out of scope

- Editing `claude-hook-review`'s SKILL.md or `shell-script-conventions.md` — both already ship a working version of this rule for their narrower surfaces (hook headers; all shell scripts). This plan generalizes the rule to CLAUDE.md's core comment guidance and to `comment-discipline-reviewer`'s checklist, the two surfaces the brief named; it does not rewrite the two prior, already-functioning instances to match this wording exactly.
- Pulling additional review-comment history from the triggering private PR — the pattern is already corroborated by two independent in-repo prior implementations (assumption ledger Row 5); not needed to strengthen the case further.
- `comment-discipline-reviewer`'s other four checklist items (verbosity threshold, PR-defined terminology, "used to be X" framing, durable-doc self-test) and their scoping/exclusion rules — untouched.
