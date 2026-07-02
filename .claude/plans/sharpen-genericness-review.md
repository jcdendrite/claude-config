# Close the platform-genericness review gap (GH #417)

## Context

**Goal: stop platform-genericness violations from passing skill-review/agent-review by converting the check from read-through noticing to a mandatory enumerate-then-justify procedure, and by giving agent-review the item it currently lacks.**

PR #413 shipped three violations of committed repo rules through the review
pipeline — `grep` prescribed in a reviewer-agent angle and in code-review 9d, a
vendor product name as a category anchor in 9h, and a "house conventions" bias
anchor in test-conventions §9. All were caught by human review; none by the
pipeline, despite skill-review running three times. skill-review **item 12
("Platform-genericness")** exists but failed in execution; **agent-review has no
counterpart item at all**, so the `grep`-in-agent-file violation went through
agent-review with nothing to catch it.

The issue diagnoses three reasons item 12 failed: (1) **example bias** — its
exemplars are infra tokens (`pg_cron`, `net.http_post`), so a reviewer
pattern-matching against those does not classify a ubiquitous CLI verb or a
vendor category-anchor as violations; (2) **read-through noticing** — it relies
on spotting tokens during a prose read; (3) **self-review blindness** — the
session that wrote the token grades the item. The generalizable fix is the same
one PR #413's own 9d fix used: **enumerate-then-justify**.

## Approach

Two procedural edits, both to reviewer-skill checklists. No code, no new test.

### 1. Sharpen skill-review item 12 (`plugins/skill-management/skills/skill-review/SKILL.md`)

Replace the current one-line item 12 with an enumerate-then-justify item modeled
on code-review item 9d ("Run a literal text search… do not rely on noticing
during read-through… For each hit… Flag"). The new item:

- Names the **three distinct failure classes** the current item under-specifies:
  (a) a **tool-invocation verb** prescribed inside a review/checklist
  instruction; (b) a **vendor/product name** used as a *category anchor* rather
  than an illustrative example paired with the generic capability; (c) a
  **source-material bias anchor** ("house conventions", a named team's practice).
- Makes the **procedure mechanical**: extract every hit in each class from the
  diff first, then justify each one inline as deliberate/illustrative or move it
  to a project layer. Extraction is mandatory; the **verdict stays judgment**.
- Keeps examples deliberately light (the issue blames infra-token exemplars for
  example bias); describes the anchor-vs-illustration distinction in words rather
  than seeding another concrete product token.

### 2. Add the mirrored item to agent-review (`claude/.claude/skills/agent-review/SKILL.md`)

agent-review's checklist (currently 15 items) gains a new item 16 mirroring the
sharpened skill-review item 12, adapted for agent bodies, with **one class
dropped**: agent bodies are stowed to every user with the same rationale as
skill bodies, so the tool-verb-prescription class (a) and the source-material
bias-anchor class (from skill-review's (c)) transfer and apply — the issue's
own demonstrated agent-review gap (`grep` prescribed as a mandatory step in a
reviewer-agent angle) is a class-(a) violation. The vendor/product
category-anchor class (skill-review's (b)) does **not** transfer: `staff-*`
reviewer personas (`staff-platform-engineer`, `staff-data-engineer`,
`staff-analytics-engineer`, `staff-frontend-engineer`) are domain-expert
bodies whose job is enumerating the vendor landscape they must recognize
(Terraform, Snowflake, Fivetran, Sentry, and similar) — that enumeration is
load-bearing domain knowledge, not platform lock-in to generalize away.
Porting skill-review's full three-class item onto agent-review without this
carve-out was a category error caught in review: skill bodies are generic
meta-process instructions where vendor names are lock-in to avoid; `staff-*`
bodies are domain-expert review personas where vendor names are the
domain knowledge itself. Item 16's text states the exclusion and its
rationale explicitly, rather than relying on a reviewer to infer it.

This is a **genuine gap, not duplication-for-safety** — the repo's "No shared partials
across skills — duplicate intentionally" rule sanctions the near-duplicate text
so each file stands alone.

The agent-review item points at CLAUDE.md's "Global skill bodies stay
platform-agnostic" rule for the *rationale* (stowed to all users), and states
that agents use illustrative-vs-prescriptive discipline rather than a
`<skill>-<project>` layer (agents have no project-layer mechanism). No CLAUDE.md
edit: the canonical rationale is cited, not restated, and the skill-specific
remedy (project layer) does not map onto agents.

**Pointer-wording constraint (required):** the canonical rule at `CLAUDE.md:73`
literally scopes to "Skills under `claude/.claude/skills/`" with a skill-only
remedy, so a bare "see CLAUDE.md" pointer from an agent-context item reads as
broken. Item 16 must apply the rule's rationale *by analogy* — e.g. "agent
bodies are stowed to every user for the same reason skill bodies are; the
platform-agnostic rule applies by that rationale (see repo-root `CLAUDE.md`)" —
modeled on the existing item 13 pointer shape (`(Repo-specific; see repo-root
CLAUDE.md.)`). Do not emit a bare cross-reference.

### Why the issue's Part 3 (denylist test) is dropped

The issue's third part — a curated vendor/product-name denylist test with an
allowlist — is rejected. This is a **deliberate scope reduction from the ticket**;
recorded here and to go in the PR body.

- **Wrong axis.** The defect class is "a vendor name used *prescriptively as the
  category anchor*" vs. "*illustratively alongside a generic term*" — a
  structural/contextual distinction. A denylist keys on token *identity*, which
  is silent on which case applies.
- **Precision collapses it to near-empty.** Corpus scan: bodies legitimately
  contain Terraform, Pulumi, CloudFormation, Snowflake, BigQuery, Fivetran,
  Airbyte, dbt, Sentry, Mixpanel, PostHog as illustrative enumerations
  (staff-analytics-engineer even carries an explicit "these are illustrations,
  not the required stack" disclaimer). The denylist can hold only names that
  *never* appear legitimately — a tiny arbitrary set; the one demonstrated name
  (SonarQube) is not even in the corpus.
- **Blocklist incompleteness + drift.** The next violation uses a name nobody
  added — reproducing the exact false-confidence property ("ran three times and
  still passed") that motivated the ticket.
- **Compounding-layers anti-pattern.** CLAUDE.md: "Do not keep adding hardening…
  the right primitive usually has a simple shape." A brittle token scan bolted
  beside a checklist item is that anti-pattern.
- **No mechanical verdict to test.** prescriptive-vs-illustrative is semantic
  judgment; the issue itself concedes the tool-verb class "cannot be mechanically
  banned" (plan-it prescribes `git grep`; code-review item 6 says "Check with
  grep before flagging"). A test asserting the item "contains the right words"
  is the source-scanning anti-pattern (code-review 9g). The "write-the-enforcing-
  test" convention applies to mechanically-enforceable conventions; this class is
  not one.

**Fallback if a tripwire is still wanted:** a minimal regression pin over the
exact demonstrated token(s), explicitly labeled a regression pin (not class
coverage). Not recommended — it guards an already-absent token and does not
generalize. Left as an option for the user to elect.

### Constraints honored

- **Not added to code-review's base checklist** — both edits live in the
  per-file-type reviewer skills (skill-review, agent-review) that code-review
  already dispatches; nothing new runs on `.py`/`.sh` diffs.
- **No prose proliferation** — the fix is procedural sharpening of one item plus
  the one genuinely-missing item, not more copies of the rule text.

## Critical files

- `plugins/skill-management/skills/skill-review/SKILL.md` — rewrite item 12
  (§7 checklist). File is ~195 lines; keep the item tight so the file does not
  drift materially past the 200-line target (the behavior test governs).
- `claude/.claude/skills/agent-review/SKILL.md` — add item 16 to the §7
  checklist. File is ~151 lines; ample room.

**Reuse / model to follow:** code-review item 9d
(`claude/.claude/skills/code-review/SKILL.md:89`) is the canonical
enumerate-then-justify wording to mirror ("Run a literal text search… do not
rely on noticing during read-through… For each hit… Flag").

**Canonical rule (do not restate):** repo-root `CLAUDE.md` "Global skill bodies
stay platform-agnostic" and "When a skill is surfaced by real-world work,
abstract first" — the two items operationalize these; they point at CLAUDE.md
rather than duplicating it.

## Verification

- **Dogfood each skill on its own diff** (repo rule): invoke `/skill-review`
  against the skill-review SKILL.md diff and against the agent-review SKILL.md
  diff — the sharpened items must themselves pass the behavior test, voice, and
  length checks. Both edits are SKILL.md changes, so `require-skill-review.sh`
  gates the commit on the skill-review marker; `/agent-review` is *not* triggered
  (neither edit touches a `claude/.claude/agents/*.md` file).
- **`/code-review`** dispatch on the staged diff (routes to skill-review per
  file type).
- **Regression check (no new test added):** from a worktree,
  `../../../.venv/bin/pytest claude/.claude/skills/` and
  `../../../.venv/bin/ruff check claude/.claude/` stay green — confirms the item
  edits don't break existing structural tests (e.g. frontmatter parse,
  listing-budget, trigger-block assertions).

## PR packaging

- This plan file (`.claude/plans/…fizzy-dream.md`) is committed in the **same
  PR** as the two SKILL.md edits — not a separate branch.
- The PR body carries the **denylist-rejection rationale** (Approach §"Why the
  issue's Part 3 is dropped") prominently, so the absence of the issue-requested
  test reads as a recorded decision, not an oversight. Note it deviates from the
  "add test enforcement for new conventions" convention; the justification is
  that prescriptive-vs-illustrative has no mechanical verdict and a
  source-scanning test would be the code-review 9g anti-pattern.

## Out of scope

- The denylist test (issue Part 3) — dropped, rationale above.
- Any CLAUDE.md edit — the canonical rules already cover the three classes; the
  items cite them.
- Re-editing the PR #413 sites (code-review 9d/9h, test-conventions §9) — already
  fixed in PR #413; corpus scan confirms no residual violating tokens.
