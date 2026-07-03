# Relax and reframe the handoff/brief length guidance

## Context

**Goal:** replace the `Target under 200 lines` guidance in the `handoff` and `brief`
skills with a ~500-line soft anchor reframed as a bloat-detector, so a continuity file
never drops load-bearing state to hit a line count.

The concern that prompted this: the 200-line figure on handoff/brief incentivizes
truncating substantive cross-session context, and 200 lines is too tight for a complex
multi-phase handoff. Investigation confirmed the premise and sharpened it:

- **It was never a hard cap.** `Target under 200 lines` is soft prose in two SKILL.md
  bodies (`handoff/SKILL.md:80`, `brief/SKILL.md:88`). No hook counts lines on the
  produced `/tmp/*-handoff.md` / `*-task.md` artifacts — those are never committed. The
  enforced 200-line hooks (`check-skill-length.sh`, `check-claude-md-length.sh`) gate
  only committed CLAUDE.md/SKILL.md/agent files at `git commit`.
- **The number came from the wrong cost model.** Primary source
  ([Claude Code — Memory](https://code.claude.com/docs/en/memory)) scopes the 200-line
  recommendation to files loaded *every session*: *"CLAUDE.md files are loaded into the
  context window at the start of every session... target under 200 lines per CLAUDE.md
  file. Longer files consume more context and reduce adherence."* The same doc leaves
  read-on-demand topic files (`debugging.md`, `patterns.md`) **uncapped**. A handoff/brief
  file is read once by one resuming session — the read-on-demand category Anthropic does
  not cap. The 200-line figure was inherited as boilerplate in the #221 skill migration.
- **The repo's own history shows the tension.** Required handoff content kept growing —
  §2.5 incomplete-prerequisites (#398, itself a fix for lost prerequisite context),
  confidence tags (#413), authorization categorization (#420) — while the 200-line target
  stayed frozen. An agent optimizing for the number drops exactly this load-bearing content.
- **The correct principle for continuity files is signal density, not line count.**
  [Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents):
  *"the smallest set of high-signal tokens that maximize the likelihood of your desired
  outcome."* Compaction guidance ([best-practices](https://code.claude.com/docs/en/best-practices))
  says to *preserve the full list of modified files and test commands* — completeness of
  state is what to protect.

**Intended outcome:** both continuity skills instruct writers to capture complete state
with no redundant inlining, using ~500 lines as a "you're probably inlining — check"
trigger rather than a truncation mandate.

**User surface:** `claude/` is stow-distributed — these two skill bodies ship to every user
who clones and stows this repo, going live on `git pull` with no re-install. The change is
writer-facing guidance prose with no runtime or enforcement surface; blast radius is that
all stow users get the reframed continuity-file guidance the next time `/handoff` or
`/brief` runs. Reversible by a one-line revert.

**Scope note (Fable assessment):** the findings in `/tmp/claude-config-assessment/`
(F1–F8) are all PR-review-comment failures and surface nothing about handoff length.
That is expected — handoff-loss manifests in session transcripts, not PR comments — so
that corpus neither supports nor refutes this change. The change rests on the documented
cost model above, not a mined incident.

## Approach

Edit the single guidance line in each of the two SKILL.md files, replacing the
line-budget phrasing with a reframed block that (a) states signal-density as the goal,
(b) keeps ~500 as a bloat-detector anchor, (c) forbids dropping any populated section or
load-bearing claim to hit a number. New text for both files:

```
Reference files by path; do not inline contents. Aim for the smallest set of
high-signal tokens that fully capture state — not a line budget. If the file runs
past ~500 lines, that is a signal to check for content recoverable from disk
(inlined diffs, tool output, file bodies) and cut that — not a mandate to cut
continuity. Never drop a populated section or a load-bearing claim to hit a line
count; completeness of state beats brevity here.
```

(In `handoff`, "populated section" refers to §1–§7 / §2.5; in `brief`, §1–§7 — both
defined earlier in the same SKILL.md, so "populated section" reads unambiguously without
re-enumerating them inline. The base line currently sits under `## Slug naming`, just
before `## Pre-write checklist`, in both files; neither Pre-write checklist references a
line count, so no checklist item needs updating.)

**Why ~500 and this framing** (chosen via AskUserQuestion): ~500 gives 2.5× headroom for
a complex multi-phase handoff while still firing the inlining self-check; a ~1000 anchor
rarely triggers before genuine bloat accumulates; dropping the number entirely loses the
self-check trigger some writers regulate against. The reframe (anchor = bloat-detector,
not budget) is the load-bearing part — it removes the truncation incentive that motivated
the change.

**Structural siblings.** `handoff/SKILL.md:80` and `brief/SKILL.md:88` carry the identical
line and identical read-once cost model — both change identically. Deliberately **not**
touched:
- `agent-review/SKILL.md:72` (`Target under 200 lines per file`) — agent files load when
  the agent runs (repeatable per-invocation cost), closer to the SKILL.md model; its cap
  stays.
- `check-skill-length.sh` / `check-claude-md-length.sh` and the CLAUDE.md/SKILL.md/agent
  200-line discipline — these gate load-every-turn committed files where the cap is
  correctly justified. Leaving them untouched is the scope boundary.

## Critical files

- `claude/.claude/skills/handoff/SKILL.md` — replace the `Target under 200 lines...` line
  (currently line 80).
- `claude/.claude/skills/brief/SKILL.md` — replace the identical line (currently line 88).

No new files, no hook changes, no test changes. The two edits are the entire change.

**Reuse / do-not-duplicate:** the phrasing must be duplicated verbatim into both files by
design — per the repo's "No shared partials across skills" rule, continuity-file guidance
is intentionally duplicated, not extracted to a shared partial. This is the named DRY
exception, not a violation.

## Verification

- **Content check:** re-read both edited SKILL.md bodies end-to-end; confirm the new block
  reads coherently in place (it follows the section list and the "Slug naming" block in
  each) and that no other reference to "200 lines" remains in either file
  (`grep -n "200 lines" claude/.claude/skills/{handoff,brief}/SKILL.md` → no hits).
- **No enforcement regression:** confirm no hook references a handoff/brief artifact line
  count (already verified — none does), so nothing mechanical needs updating.
- **Skill self-review:** run `/skill-review` on each edited SKILL.md (hook-enforced at
  commit via `require-skill-review.sh`) and check the diff against its output — an edit
  that *adds* prose to skills must survive the skill's own brevity/duplication lens. The
  net line delta per file is small (one line → ~five), well under the 200-line skill cap,
  and grows vs HEAD only marginally, so `check-skill-length.sh` will pass.
- **Pipeline:** `/code-review` dispatches `/skill-review` per the SKILL.md file type;
  address findings before presenting. No agent file or plugin file touched, so
  `/agent-review` and `plugin-semver` do not apply.
- **Lint/tests:** `.venv/bin/ruff check claude/.claude/` and `.venv/bin/pytest claude/.claude/`
  — expected no-ops (prose-only change, no hook/test touched), run as a guard.

## Out of scope

- **The `agent-review` 200-line target** (`agent-review/SKILL.md:72`) — different cost
  model; leave it alone even though it shares the "200 lines" string.
- **The committed-file length hooks** (`check-skill-length.sh`, `check-claude-md-length.sh`)
  and their tests — the load-every-turn cap they enforce is correctly justified; do not
  relax them while "we're in the neighborhood of line-count rules."
- **The `nudge-handoff-near-context-cap.sh` threshold** (120k tokens) — that governs *when*
  to write a handoff, not *how long* it may be; unrelated.
- **A transcript-analysis sweep for handoff-loss incidents** — considered and declined;
  low-yield (faint transcript traces) and the primary-source grounding already settles the
  design. Can be run later if the reframe proves insufficient.

## Revisit note

The ~500 figure is a judgment default (labeled as such in the new prose: "not a line
budget"), not a grounded constant — no external source specifies a number for read-once
continuity files because none caps them. It is safe to tune later without re-deriving the
design.
