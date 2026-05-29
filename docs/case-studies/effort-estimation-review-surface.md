# Estimating effort by review surface, not implementation time

*Part of the [claude-config case studies](../case-studies.md). The empirical record behind [`design-decisions.md` §13](../design-decisions.md#13-effort-estimated-by-review-surface-not-implementation-time-2026-05-29).*

**The problem.** A recurring observation from engineers working alongside coding agents: the agent's effort estimates anchor on human coding speed. "This is a 30-minute change" — "a quick win" — "a small self-contained PR" framed as easy because it would take a human developer an afternoon. The frame is wrong. For an agent, implementation time is negligible — a 500-line refactor and a one-line fix both run in minutes. The cost that gates a change is not how long it takes to write; it's how long it takes to review, and how wide the testing surface is. Anchoring on implementation time miscalibrates triage, artificially weights small tasks toward "do it now," and treats low-implementation-cost changes as low-risk even when they touch widely-shared surfaces.

**Question.** Does claude-config prevent this framing, and what does its own transcript corpus show about how often the habit actually appears?

**Short answer.** The "review-surface, not hours" rule is encoded in two planning skills — governing plan effort sections specifically — and the corpus shows the broader habit is rare. When the pattern does appear, the dominant signal is the agent *arguing against* it during plan reviews, not the agent using it. One honest limit: the skill rule governs *plan documents*, where an effort section is the named site. Conversational scoping asides ("probably 30 minutes") fall outside the plan-section scope and are caught only by general judgment culture, not a structural gate.

## How this was measured

The corpus is this repo's own Claude Code transcript store: 473 session `.jsonl` files at the project root, 426 of which contain at least one assistant turn, totaling 50,471 assistant text blocks, as of 2026-05-29. An additional 809 `.jsonl` files exist in subagent-session subdirectories alongside each session UUID; those were not included — this audit covers main-thread session files only. Counts were extracted with a streaming `jq` pass over assistant `message.content[].text` fields.

Four honest limits apply:

- **Point-in-time.** The transcript store is mutable — sessions accrue continuously, and worktrees are cleaned up after merges. The figures here were measured during this case study's authoring; the method reproduces, but a later run reports different totals.
- **Ad-hoc, not committed.** The analysis ran as a `jq` + `grep` pipeline, not a named subcommand of the repo's `transcript-analysis.py` toolkit — deliberately. The rarity finding is the whole point: committing a new subcommand to detect a one-off is over-powered for the signal.
- **Single project.** Only the `claude-config` project directory was analyzed. This repo's content skews toward meta-discussion of the anti-pattern; a typical coding project directory might show different rates.
- **`trivial` and `self-contained` were excluded as time proxies.** Both appeared frequently (85× and 89× respectively in assistant prose) but are structural signals in this corpus — "mechanically simple edit," "skill file independently readable" — not time estimates. Counting them would inflate the prevalence figure for the wrong reason.

Quotes below were scanned for credential material and private-project identifiers before inclusion.

## The numbers

Across 50,471 assistant text blocks, genuine human-time effort estimates of *proposed work* number roughly **6–8**. The single clearest instance — the canonical shape — appears once:

> "They're a small self-contained claude-config PR — probably 30 minutes of implementation. Want me to hand those off now as a separate session brief, or fold them back into this session's plan and do them here?"

The remaining instances are mild conversational asides ("worth 5 minutes to run," "~10 minutes of empirical testing first") rather than formal scoping claims.

The dominant signal in the corpus is the inverse: the agent **arguing against** the pattern. When the agent reviews a plan that has an effort section citing hours or minutes, it flags B15 (the plan-review checklist item for effort-section reality) and rewrites the section in review-surface terms. Verbatim from a plan-review output in this corpus:

> "B15 — Effort table used hours. Replaced '~5 min / ~10 min / ~1-2 hr' with review-surface terms (file count + change shape)."

And from a session where the agent caught its own prior framing in a handoff-timing proposal:

> "'~3 hours of wallclock time' — wrong measure. As you pointed out, idle overnight sessions don't burn tokens; the cache stays warm. The lever is active turns or accumulated output."

These self-correction instances — the agent flagging the anti-pattern as a defect during a review pass — outnumber the raw occurrences of the pattern itself in the corpus. That is a stronger signal than low prevalence alone: the anti-pattern is not just rare, it is treated as a recognized defect when it appears.

## The encoded fix

The rule is encoded in two planning skills, both shipping from this repo via stow to all users.

**`plan-it/SKILL.md` (Step 5, effort section guidance):**
> "Effort sections optional; if present, describe review surface (file count, domain spread, risk concentration), never hours or days."

**`plan-review/SKILL.md` (checklist item B15):**
> "If the plan has an 'Estimated Effort' section, does it describe **review surface** (file count, domain complexity, risk concentration) rather than **implementation hours**? Hour-based estimates anchored in human coding speed mislead when Claude writes the code. Flag any effort section citing hours/days; rewrite in review-surface terms."

The research justification for the rule is in `plan-it/REFERENCES.md`, from a survey of canonical PR planning templates:
> "Hour/day estimates — no single-PR template uses them; Squarespace's Timeline exists for multi-week features only."

**Why the rule is scoped to plan sections, not all prose.** Adding a general "never use time estimates" instruction to `CLAUDE.md` would spend always-loaded context budget against a pattern that appears roughly 6–8 times across 50K text blocks. The plan-section scope is where effort estimates are formally consequential — where a reviewer or implementer reads a number and updates their mental model of a change's weight. Casual conversational sizing is harder to structurally gate and lower in impact; the residual is governed by judgment culture rather than a rule. The proportionality test in `CLAUDE.md`'s "Default-suspect over-powered primitives" heuristic applies directly: an always-loaded rule is a heavier mechanism than the documented occurrence warrants.
