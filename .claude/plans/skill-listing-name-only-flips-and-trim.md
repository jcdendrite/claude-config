# Critical evaluation: skill-budget name-only flips (toucan plan)

## Context

The prior session left a plan (`read-tmp-skill-budget-optimization-hando-twinkly-toucan.md`)
that recommends flipping `agent-review` and `skill-review` to
`skillOverrides: name-only` to "reclaim skill-listing budget," plus a large
documentation apparatus (new design-decisions §17, a new case study, cross-tracker
follow-up tickets). The user asked me to **evaluate critically before executing.**

My verdict: **do not execute as written.** The flips are mechanically harmless, but the
plan is mis-ordered, internally contradictory in its evidence logic, disproportionate
(5 files + 2 trackers + a case study), and — most importantly — **wrong about the lever.**
It frames the flips as "reclaim claude-config budget" when claude-config has no overflow;
the actual pressure is in *stow-consumer* projects, driven by claude-config's globally-stowed
skill descriptions. The corrected action below is small and real: flip both skills to
name-only, and drop the disproportionate apparatus.

## What I verified (so the critique is grounded, not theoretical)

1. **Current state.** `agent-review`/`skill-review` are NOT currently flipped.
   `skillOverrides` today holds 7 name-only skills (brief, handoff, read-docx-comments,
   transcript-analysis, error-handling, test-conventions, sql-query-conventions) — and
   this session's skill listing shows all 7 with no description, so the mechanism is real
   and observable.
2. **Access path of the flip candidates.** `code-review/SKILL.md:241` invokes both
   skills **via the Skill tool, by name**; the `require-skill-review` hook only emits a
   *deny message* ("Run /skill-review…") — it does not auto-invoke. `name-only`
   preserves Skill-tool-by-name and slash invocation (skills.md override table, L52).
   → **The 2 flips cannot break code-review dispatch or the hook. The flip is safe.**
3. **Size of the prize.** `agent-review` description = 378 chars, `skill-review` = 354.
   Total reclaim ≈ **732 chars** off the *live* listing budget.
4. **Wrong budget for the toucan headline.** The repo's pytest budget
   (`validate_skill_structure.py::corpus_budget_violations`, hard 8,000) reads
   frontmatter *from files* and respects the `name-only` exclusion in settings — the
   `_name_only_skills()` function excludes settings-overridden skills from the test's
   budget corpus. Both budgets are relieved by these flips.

## The three substantive problems with the toucan plan

### A. The evidence logic is internally contradictory (the central flaw)

The plan's safe-flip rule is "≥1 invocation AND **zero** description-match
auto-triggers." Its own "Leading hypothesis" section then says: *if the listing budget
is overflowing, Claude Code silently drops descriptions, killing auto-trigger for the
dropped skills.* If that hypothesis is true, "zero observed auto-triggers" is an
**artifact of the budget problem**, not evidence of by-design dispatch-only behavior.

The plan tries to exempt the 2 flip candidates as "dispatch-only by design" — but it
also admits both **carry full TRIGGER/DO-NOT-TRIGGER blocks** (they are *designed* to be
auto-selectable too). The confound applies to them equally. One of the two positions
has to give — and the resolution is a measurement, not more prose.

### B. The ordering is backwards — the cheap diagnostic gates everything

`/doctor` (or `/skills`) answers the one question the whole plan hinges on: **is the
live listing budget actually overflowing, and are descriptions being dropped?** The plan
buries it in step 3 of Verification, *after* a 5-file change set. Run it first — both
outcomes deflate the plan without the diagnostic:

- **No overflow:** there is no budget pressure to relieve. Flipping 2 skills removes
  their auto-trigger fallback (and degrades them on pre-v2.1.129 clients) to reclaim
  headroom nobody is short of. Net negative or net zero.
- **Overflow + descriptions dropped:** the leading hypothesis is live → the
  "never/under-fires" evidence is confounded for *every* skill in the study.

### C. The change is wildly disproportionate to the prize

For a ~471-char settings flip that merely **extends an existing precedent (§15)**, the
plan proposes: edit `settings.json`, multi-edit `docs/skills.md`, a new design-decisions
**§17**, a **new case study file**, a `docs/case-studies.md` index line, and cross-tracker
follow-up tickets. The research was large; the durable change is tiny. A new case study
to record "this was a lot of research" inverts the scope test — document the *decision*,
not the effort.

### D. Unresolved blocking unknown (acknowledged by the plan, unaddressed)

`skill-review` is a plugin skill (`skill-management@claude-config`). The plan flags that
the `skillOverrides` key form for plugin skills (bare `skill-review` vs
`skill-management:skill-review`) is **unverified, and a wrong key silently no-ops.**
That cannot be resolved from disk or in plan mode — it needs a live `/skills` check after
applying the key. So even the minimal flip has a verify-in-session gate for the plugin
half.

## Diagnostic result (`/doctor`) + the stow mechanism — what actually drives this

`/doctor` (run before executing):
- **claude-config: no descriptions dropped.** Its own listing fits under 1% locally.
- **A stow-consumer project: 2 descriptions dropped** (listing wants 1.1% vs 1% budget):
  `git-state-safety` and `lovable-cloud:lovable-cloud-edge-functions`.

**The mechanism that ties them together (and that the toucan plan got backwards):**
`claude-config` is stowed to `~/.claude`, so **every globally-stowed claude-config skill's
description loads in every project's listing.** `git-state-safety` is itself a
claude-config global skill, and it's being dropped in a stow consumer. So the lever that
relieves stow consumers is **trimming claude-config's global listing footprint.**
"claude-config has no overflow" does not mean "no action in claude-config" — claude-config
is the source of the bytes overflowing downstream.

This confirms **problem A**: `git-state-safety` and `lovable-cloud-edge-functions`
"never fired" because their descriptions are dropped under overflow — an artifact, not a
trigger-scoping bug. The fix for them *is* this trim, not a ticket.

**Scoping correction that reshapes the toucan flips** (verified in repo-root
`.claude/settings.json:11-15`): `skill-management` / `claude-hook-review` / `plugin-semver`
are enabled at **project scope in claude-config only**, not globally. Therefore:
- **`agent-review`** (globally stowed) — its description loads in every stow consumer →
  flipping it to `name-only` **immediately** relieves all stow consumers (~378 chars each).
- **`skill-review`** (in the claude-config-scoped `skill-management` plugin) — not in any
  stow consumer's listing *today*. But the user intends to **enable `skill-management` in
  other project repos**; once enabled there, `skill-review`'s description (~354 chars)
  would load and add to each project's listing. Flipping it to `name-only` **now** is
  forward-looking budget hygiene — it makes the plugin budget-friendly *before* the
  rollout. Both flips are justified.

## Recommended action — trim the global footprint, verify

**1. `agent-review` → `name-only`** in the **global** stowed `claude/.claude/settings.json`.
Safe (it's Skill-tool-/slash-reached, never description-auto-triggers; verified SKILL.md:241
+ hook), relieves all stow consumers immediately.

**2. [ALREADY DONE] The sibling `trim-skill-budget.md` compressions are already applied.**
`code-review` and `git-feature-branch-sync` were compressed in commit `40bab54` (#356).
`subagent-delegation` was compressed in the same PR, then grew again in `951dc89` (#371)
when the "where scope or content is still forming" qualifier was deliberately added to the
Edit/Write DO NOT TRIGGER clause — that addition is a routing discriminator and should not
be removed. No SKILL.md edits needed.

**3. `skill-review` → `name-only` in the global stowed `settings.json`.** Justified as
forward-looking hygiene for the planned `skill-management` rollout to other repos
(see Diagnostic section). The plugin-override key form (bare `skill-review` vs
`skill-management:skill-review`) is unverified and a wrong key silently no-ops — confirm
live via `/skills` in a session where `skill-management` is enabled (claude-config now;
consumer repos after rollout).

**4. Re-measure after applying.** Re-run `/doctor` in a fresh session on any stow-consumer
project. Honest arithmetic: the safe global trims (~732 chars total from both flips) may
un-drop the two affected skills. If a gap remains, the choices are:
   - **Accept the residual drop** for lower-value skills. Lightest.
   - **Raise `skillListingBudgetFraction`** (global or local) — definitive, but
     `/doctor` flags the cost: ~2k extra tokens every session, faster rate-limit burn.
     Reserve for if the dropped skills' auto-trigger proves genuinely valuable.
   - Do **not** chase more name-only flips — the toucan study found no other safe global
     candidates (everything else either genuinely auto-triggers or is overflow-confounded),
     so further flips would trade away working auto-triggers.

## Drop from the toucan plan (surviving critique)

- **New §17 + new case study + `docs/case-studies.md` index line** — disproportionate to a
  one-line settings flip. A 2–3 line amendment to **§15** (which already owns "wired by
  dispatch, not description auto-trigger") is the single-source home.
- **Cross-tracker "investigate" tickets** — confounded; they're the overflow artifact this
  trim addresses, not a trigger bug.

## Critical files

- `claude/.claude/settings.json` — add `"agent-review": "name-only"` and `"skill-review":
  "name-only"`. Global stowed settings → affects all projects.
- `docs/skills.md` — add both skills to the name-only inventory, bump count Seven → Nine,
  extend category paragraph.
- `docs/design-decisions.md` — 2–3 line amendment to **§15** (not a new §17).

## Verification

- `/skills` in claude-config confirms both descriptions left the listing; `code-review`
  still Skill-invokes both by name; both stay in the `/` menu.
- `/doctor` in a fresh session on any stow-consumer project — confirm the
  dropped-description count fell. This is the success metric.
- `settings.json` valid JSON; `pytest claude/.claude/skills/tests/test_skills.py` green.
  (Run and confirmed green: 1822 passed, 22 skipped.)

## Out of scope

- New §17, the case study, and the cross-tracker tickets — dropped per above.
- Heavy budget-fraction raise — deferred to the post-measurement decision in step 4.
- **Enabling `skill-management` in other repos** — a separate per-repo action
  (`claude plugin install skill-management@claude-config --scope project`), not a
  claude-config file edit. This plan's `skill-review` flip *prepares* for that rollout;
  it does not perform it.
