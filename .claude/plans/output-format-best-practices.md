# Output format best practices → CLAUDE.md

## Context

Promote the universal, non-personal prose-quality rules currently buried in
the optional, uncommitted `<config-dir>/output-preferences.md` into the
committed, always-loaded `claude/.claude/CLAUDE.md`, so every stow consumer
and every subagent — not just an interactive session that happens to read
the personal file — authors comments, docs, chat replies, and drafted prose
against them from the first draft, reducing (not eliminating) the volume of
after-the-fact corrections from `tighten-prose` and `comment-discipline-reviewer`.
This matters now because the user is absorbing repeat corrections from both
reviewers for prose patterns — verbosity, compound sentences, hedged
non-answers, elegant variation — that are already-known best practices
sitting on the wrong surface (a personal file with no automatic load path
for subagents), not genuinely undiscovered problems. The intended outcome is
a single canonical home for these rules in `CLAUDE.md`, with
`output-preferences.md` narrowed to genuine personal-taste calibration, plus
a durable `docs/design-decisions.md` record and a regression test tying the
two surfaces together so the split doesn't silently re-drift.

## Approach

Promote the nine prose rules that are genuinely universal — five already decided plus four mined from `tighten-prose` §4 — into a new `## Prose and Output Format` section in `claude/.claude/CLAUDE.md`, sited before `## Code Comments, Documentation, and Prose` so the general rules read first and the comment/doc section reads as a narrowing of them. That new section absorbs today's standalone `## Output Preferences` pointer as its last bullet, which recovers four lines and puts the global-rule/personal-layer boundary in one place a consumer cannot miss. The epistemic-honesty rule folds into `## Working Style`'s existing "Be precise" bullet rather than becoming a new one, because that bullet's subject already is calibrating what you assert to what you know.

The delivery mechanism, not the content, is what changes. `claude/.claude/CLAUDE.md` stows to every consumer's user-scope `CLAUDE.md` and is in context before the first turn — for the session and for every subagent except `Explore`/`Plan` (`claude/.claude/skills/plan-it/SKILL.md:55`). `<config-dir>/output-preferences.md` reaches a session only when something performs the `Read` that `claude/.claude/CLAUDE.md:155` requests, and nothing in the repo injects it: a repo-wide grep for `output-preferences` returns only that pointer, `README.md:442`, the `install.sh:686-694` setup tip, a path-shape regex in `test_skills.py:2821`, and a prior plan file — no hook, no `additionalContext` emitter.

Four rules survive the `tighten-prose` mining pass: one idea per sentence, one term per concept, active voice, and plain verbs over inflated ones and noun stacks. `tighten-prose` §2's overriding constraint — never drop or flatten a fact, number, decision, hedge, or conditional to shorten a sentence — is promoted with them, in the same bullet as the concision rule where the temptation arises. Brevity guidance stated without it teaches shortening by dropping qualifiers, which is a correctness regression, not a style one. Two rules are declined and the reasons are recorded in Out of scope: the ~20-25-word sentence target, and §3's whole-sentence-class carve-out.

`comment-discipline-reviewer` contributes no new CLAUDE.md content, and the plan says so explicitly rather than leaving it implied. All six of its review angles are already stated in `claude/.claude/CLAUDE.md`'s Code Comments section; per `docs/design-decisions.md` §9 that reviewer is a fresh-context sweep against a rule the authoring session already had loaded, not a backstop for a missing rule. No realistic CLAUDE.md line closes an adherence gap on a rule that is already present, so this change should be expected to move `tighten-prose`'s rewrite volume and not that reviewer's finding volume. That is the honest bound on "reduce corrections."

One deliberate overlap: the new section's "one idea per sentence" restates the core of §Code Comments' "Split multi-fact comments" bullet. Three resolutions were weighed. Trimming the comment bullet to defer upward is the textbook SSOT move, but it ripples: `claude/.claude/skills/code-review/SKILL.md:67` and `:126`, `claude/.claude/agents/code-writer.md:56` and `:99`, `claude/.claude/agents/comment-discipline-reviewer.md:10`, and `claude/.claude/skills/plan-it/SKILL.md:70` all cite that section by name as a self-contained rule set — four files at six sites — and one of those edits is a `SKILL.md` change that pulls hook-enforced `/skill-review` into a prose PR. Dropping the general rule instead forgoes the single highest-frequency `tighten-prose` rewrite class. So the overlap is kept, under the Engineering Judgment section's third named single-source-of-truth exception — "a small duplicated value that beats a bad abstraction" — rather than its "instructional prose that must stand alone" exception: both sections live in the same always-loaded file, reached through the identical single load path, so there is no scenario where an agent sees one without the other, which is what the stand-alone exception actually protects against. The comment-scoped bullet still carries a remedy the general rule does not — an explicit list when the facts are parallel — and the general bullet is written in compressed form to keep the shared surface to one clause rather than three sentences, which is what makes the small-duplicated-value framing hold: a cross-reference would cost more to thread through six citing sites than the one repeated clause costs in context.

**Assumption ledger**

Root: prose rules that are not personal taste live only in a per-user, uncommitted file that reaches an agent only via an optional `Read`, so agents draft without them and two reactive reviewers absorb the cost after the fact.

Givens:
- G1 — The 200-line CLAUDE.md ceiling holds. Anthropic documents it ("Longer files consume more context and reduce adherence"), quoted in `claude/.claude/skills/ai-instruction-and-memory-files/SKILL.md` §2; a vendor-imposed threshold is not this plan's to move. `[verified: claude/.claude/hooks/check-claude-md-length.sh:16-19]`
- G2 — `comment-discipline-reviewer.md` and `tighten-prose/SKILL.md` bodies stay unedited, and the promote/keep split of the seven output-preferences bullets is settled. Decided by the engineer this session. `[engineer-verified]`
- G3 — `<config-dir>/output-preferences.md` remains uncommitted and user-local. This is the engineer's own decision this session ("have a customizable section, certainly — that can be output-format file in its current state"), not merely an inference from README's existing prose; a future revision should treat it as settled rather than silently reopen it. `[engineer-verified]`

Rows:

1. `[mechanism, anchors: root]` New `## Prose and Output Format` section in `claude/.claude/CLAUDE.md`. This is the widest-scope prose mechanism available — always-loaded, every consumer, every agent — so two lighter primitives were checked first and both fail on reachability, not on content. (a) The status quo: keep the rules in `<config-dir>/output-preferences.md` and rely on the existing pointer. That is a `Read` a subagent can skip and a fresh consumer may never have created — the file is optional and `install.sh:686-694` only prints a tip. (b) A path-scoped rule under `claude/.claude/rules/*.md`, which auto-loads on `paths` frontmatter matching an open file. Prose written into a chat reply, a PR body, or a handoff note has no matching file path, so the rule would never fire for the dominant surface. A third, restating the rules in each agent body, is lighter in blast radius but makes ~12 copies of one rule set and still misses the top-level session.
2. `[mechanism, anchors: row1]` Fold the existing `## Output Preferences` section into the new section's final bullet instead of leaving it standalone. Recovers 4 lines, and states the global/personal boundary adjacent to the rules it bounds rather than two sections away.
3. `[mechanism, anchors: row1]` Extend `## Working Style`'s "Be precise" bullet in place with the don't-know clause rather than adding a bullet. Costs zero physical lines (the file is one-line-per-bullet in that section) and avoids a near-duplicate bullet on the same subject.
4. `[assumption, anchors: row1]` CLAUDE.md loads for every subagent except `Explore`/`Plan`, and nothing loads `<config-dir>/output-preferences.md` automatically for any of them. `[verified: claude/.claude/skills/plan-it/SKILL.md:55; repo-wide grep for "output-preferences" returns no hook, script, or context-injector]`
5. `[assumption, anchors: row1]` The line budget accommodates the change with margin: 164 now, +11 inserted, −4 removed → 171, and the gate denies only when the staged file is both over 200 and longer than `HEAD`. `[verified: claude/.claude/hooks/check-claude-md-length.sh:79]`
6. `[assumption, anchors: row1]` No existing test pins text in the regions being edited. The pinned regions are §Safety's marker and memory bullets, §Safety's `userEmail` line, and §Engineering Judgment's "Ground every choice" nested-category count. `[verified: claude/.claude/skills/tests/test_skills.py:926-1002; claude/.claude/hooks/tests/test_global_claude_md_email_redaction.py:23-43; claude/.claude/hooks/tests/test_doc_counts.py:143-182]`
7. `[assumption, anchors: row1, row2]` Every mention of the personal file in CLAUDE.md, README, or `docs/` must be written `<config-dir>/output-preferences.md`; the literal `~/.claude/output-preferences.md` form is a test failure, not a style preference. `[verified: claude/.claude/skills/tests/test_skills.py:2818-2946 — the path is in `_PER_ACCOUNT_STATE_PATH_RE`'s alternation and the contract covers skill bodies, agent bodies, docs, and `claude/.claude/CLAUDE.md`]`
8. `[mechanism, anchors: row1]` The general one-idea-per-sentence rule and §Code Comments' "Split multi-fact comments" bullet both stand, unmodified, under the small-duplicated-value exception (not the stand-alone-prose exception — see Approach). Recorded in the new `docs/design-decisions.md` §38 so the overlap reads as decided rather than missed.
9. `[mechanism, anchors: row2]` Prune README's template block and rename its cross-reference to the new section name. Without this a stow consumer copying today's template recreates the exact duplication this change removes, and `README.md:442`'s pointer to a `"Output Preferences"` section becomes a dangling reference.
10. `[mechanism, anchors: row9]` One new test pinning the README template's two remaining bullets and the existence of the CLAUDE.md section the README names. The invariant is silent-drift-prone in both directions and there is no other mechanical guard on it.
11. `[mechanism, anchors: root]` New `docs/design-decisions.md` §38, following §13's format (dated numbered heading, rationale, `### Sources`). §13 is the direct precedent — the same move, duplicated prose-quality content consolidated to a canonical CLAUDE.md home.
12. `[mechanism, anchors: row2]` Prune the operator's real `<config-dir>/output-preferences.md` down to the two kept-personal bullets. Out-of-repo direct edit, not part of the commit; the worktree file-write gate exempts it. `[verified: claude/.claude/hooks/require-worktree-for-file-writes.sh:93-103 — the `$HOME/.claude/*` prefix arm exits 0 before the repo walk]`
13. `[assumption, anchors: root]` Stating these rules in the always-loaded file will reduce `tighten-prose` rewrite volume. Load-bearing for the plan's purpose, asserted rather than measured; no measurement is proposed (see Out of scope). `[unverified]`

## Critical files

Single `code-writer` dispatch for the four repo files — they are one coupled edit (a section that moves, a README that points at it by name, a decision record that explains it, and a test that pins both ends), and splitting would require restating the same rationale in every prompt. The out-of-repo edit stays with the dispatching session.

**1. `claude/.claude/CLAUDE.md`** — 164 lines before, 171 after (+11 inserted, −4 removed). Three edits:

*(a)* Insert before line 136 (`## Code Comments, Documentation, and Prose`), as an 11-line block:

```markdown
## Prose and Output Format

These rules govern every text surface you author — chat replies, PR bodies, commit messages, handoff notes, plan files, ticket comments. Code comments and durable in-repo docs carry the further constraints in the section below.

- **Lead with the answer or the action taken.** Caveats and reasoning come after it. Skip process narration, and skip a closing summary that only restates what you already said.
- **Shape follows content.** A single concept gets a sentence or two of prose, several parallel items get a list, and headers earn their place only past ~15 lines. Match a code block's language tag to what is actually inside it. In terminal output, avoid markdown tables where width-wrapping would break them.
- **Cut every sentence that adds no information.** Keep the why when it is non-obvious. Never drop or flatten a fact, number, decision, hedge, or conditional to shorten a sentence — keep the content and accept the longer sentence.
- **One idea per sentence, one term per concept.** Split a compound claim instead of chaining it into a run-on. Hold the chosen term for the whole document — elegant variation reads as a second thing, not a second word for the same thing.
- **Active voice, plain verbs, no noun stacks.** Passive only when the actor is unknown or irrelevant to the reader. "Start," not "commence." A verb or prepositional phrase in place of a stacked-noun phrase.
- If `<config-dir>/output-preferences.md` exists, read it at session start and apply it. Cap at 50 lines. That file layers personal tone and style calibration on the rules above; it is not a place to restate them.
```

*(b)* Replace line 38 in place (same physical line, +0 lines) — appended clause only, existing text unchanged:

`- Be precise. Do not overstate severity, conflate distinct issues, or hand-wave. State the realistic impact and verify claims against actual code — not against what the code or a sensible design should do. When you don't know, say so and name what would resolve it, rather than offering a plausible answer at hedged confidence.`

*(c)* Delete lines 153-156 (`## Output Preferences`, its blank lines, and its one content line). Line 152's blank stays as the separator before `## Shipping`. The content is not lost — it is row 2's final bullet in the new section.

`## Code Comments, Documentation, and Prose` is untouched, including its "Split multi-fact comments" bullet and its "PR body and commit-message conciseness is `pr-description`'s concern, not this section's" scope sentence. That sentence disclaims its own section and stays accurate; the new section directly above names PR bodies in its own scope line, so a reader gets the answer without a cross-reference.

**2. `README.md`** — two prose edits in the `### Output preferences` section (line 440), plus a template prune. Net roughly −1 line.

- Line 442: replace `It is loaded via an instruction in \`claude/.claude/CLAUDE.md\`'s "Output Preferences" section.` with a sentence naming the new section and its role: `It is loaded via an instruction in \`claude/.claude/CLAUDE.md\`'s "Prose and Output Format" section, which also carries the non-personal prose rules — response shape, concision, sentence craft — that apply to every session and every subagent whether or not this file exists.`
- Line 444: keep the 50-line cap sentence; replace the anti-duplication sentence with one that says where the canonical rules are: `Keep it to personal tone and style — rules already in the global CLAUDE.md apply regardless, and a second copy here costs context budget and drifts from the original.`
- Template block (lines 448-455): reduce to the two kept-personal bullets, scaffolding intact:

```markdown
# Output preferences

- Tone: direct and calibrated — state things plainly; match certainty to evidence (no overclaiming, no hedging filler).
- Avoid emoji unless explicitly asked.
```

Do not touch the `output-preferences` TOC anchor link (line 31) or `install.sh:692` — both reference the README's own section name, which is unchanged.

**3. `docs/design-decisions.md`** — append `## 38. Universal prose rules promoted from the personal output-preferences layer into the global CLAUDE.md (2026-08-31)` after §37. Content, following §13's format:

- Paragraph 1, the mechanism gap: the personal file reaches a session only via an optional `Read` while `claude/.claude/CLAUDE.md` stows to every consumer's user-scope `CLAUDE.md` and is in context before the first turn for the session and every subagent except `Explore`/`Plan`; non-taste rules were therefore sited on the weaker surface, and the cost landed on `tighten-prose`, which rewrites drafted prose after the fact rather than shaping it.
- Paragraph 2, what moved and what stayed: the five promoted rules by name, the fold of the don't-know rule into §Working Style's "Be precise" bullet, and why tone and emoji stayed personal — both are taste, and a consumer wanting a different tone should get it by editing their own file rather than contradicting a global rule.
- Paragraph 3, the mining pass: the four `tighten-prose` §4 rules promoted, on the reasoning that a rule worth applying reactively to every drafted PR body is worth stating before the draft exists; and §2's preserve-every-fact constraint promoted alongside rather than after them, because brevity guidance without it teaches shortening by dropping qualifiers.
- Paragraph 4, the two declines with their reasons (the ~20-25-word target and the whole-sentence-class carve-out — see Out of scope for the wording).
- Paragraph 5, the bounded claim: all six `comment-discipline-reviewer` angles are already CLAUDE.md rules, the gap it closes is authoring-session satisficing on a loaded rule rather than an absent rule (§9), and no CLAUDE.md line closes that — so this change is not expected to move that reviewer's finding volume.
- Paragraph 6, the accepted overlap with §Code Comments' "Split multi-fact comments," justified under Engineering Judgment's small-duplicated-value-beats-a-bad-abstraction exception — not the stand-alone-instructional-prose exception, since both sections share one file and one load path — citing the four files at six sites that reference that section by name and the list remedy the general rule does not carry.
- `### Sources`: `claude/.claude/skills/tighten-prose/SKILL.md` §2 and §4 (the mined list); `claude/.claude/agents/comment-discipline-reviewer.md` (the six angles); `claude/.claude/skills/ai-instruction-and-memory-files/SKILL.md` §2 (the 200-line cap and the per-line behavior test each promoted line was drafted against); `claude/.claude/skills/plan-it/SKILL.md` Step 5 (subagent CLAUDE.md loading).

Write `<config-dir>/output-preferences.md`, never the `~/.claude/` form — `test_doc_has_no_state_path` covers this file. Both `README.md` and `docs/design-decisions.md` already use `<config-dir>` elsewhere, so no new defining caveat sentence is needed in either.

**4. `claude/.claude/hooks/tests/test_output_preferences_layering.py`** (new, ~40 lines). Reuse: import `CLAUDE_DIR` from `helpers` and derive `REPO_ROOT = CLAUDE_DIR.parent.parent`, exactly as `test_doc_counts.py:36-42` does — do not re-resolve the repo root. Two tests:

- `test_template_holds_only_the_personal_bullets` — locate README's `### Output preferences` section, take its first fenced block, collect the `- `-prefixed lines, and assert equality against the two expected bullets verbatim. The friction is the point: re-adding a promoted rule to the template becomes a deliberate act.
- `test_readme_names_the_global_prose_section` — assert README contains `"Prose and Output Format" section` and that `claude/.claude/CLAUDE.md` contains the `## Prose and Output Format` heading, so a future rename cannot silently orphan the README pointer.

Module docstring states the invariant and why it matters (the template is what a stow consumer copies), with no PR-defined terminology and no reference to this change's history.

`README.md` and `claude/.claude/CLAUDE.md` both already route to `HOOKS_TESTS_DIR` and `SKILLS_TESTS_DIR` in `claude/.claude/scripts/select-tests.py:322,325`, so the new test is selected without a rule-table change. Update the citation comments above `README_MD` (line 99-102) and `GLOBAL_CLAUDE_MD` (line 106-112) to name this new reader — those comments enumerate which tests read each file by path, and leaving them stale is the drift the recent rule-table work was closing.

**5. `<config-dir>/output-preferences.md`** — the operator's real, uncommitted file. Direct edit by the dispatching session after the commit lands, not part of the diff and not part of the `code-writer` dispatch. Reduce to the two kept-personal bullets, dropping the five promoted ones:

```
- Tone: direct and calibrated — state things plainly, match certainty to
  evidence, and disagree openly rather than only softening claims (no
  overclaiming, no hedging filler).
- No emoji unless directly quoting something that contains them.
```

Leaving this file unpruned is the failure case worth naming: the promoted rules would then load twice per session in slightly different wording, which is the duplication this change exists to remove.

**Authoring discipline for the CLAUDE.md edit.** Read `claude/.claude/skills/ai-instruction-and-memory-files/SKILL.md` §2 and §4 before drafting and apply the per-line behavior test to every new line. Skip that skill's Step 0 marker activation: `require-memory-skill.sh` gates writes to auto-memory files only (its path classifier returns early for anything outside `<config-dir>/projects/*/memory/`), and this change writes no memory file, so activating the bypass would claim a gate that never fires.

## Verification

1. `wc -l claude/.claude/CLAUDE.md` → `171` (was `164`). Confirms the +11/−4 math and leaves 29 lines of headroom under the 200-line cap.
2. `grep -n "## Prose and Output Format" claude/.claude/CLAUDE.md` → exactly one hit; `grep -c "## Output Preferences" claude/.claude/CLAUDE.md` → `0`.
3. `grep -n "Split multi-fact comments" claude/.claude/CLAUDE.md` → still present, and `git diff claude/.claude/CLAUDE.md` shows no change to that line or to the `## Code Comments, Documentation, and Prose` heading.
4. `git diff | grep -n "\.claude/output-preferences.md"` → no `~/`, `$HOME/`, or `${HOME}/` prefixed form anywhere in the diff; every occurrence reads `<config-dir>/output-preferences.md`.
5. `.venv/bin/python3 claude/.claude/scripts/select-tests.py` — the documented scoped test command. The changed paths (`claude/.claude/CLAUDE.md`, `README.md`, `docs/design-decisions.md`, `claude/.claude/hooks/tests/*`) route to `HOOKS_TESTS_DIR` and `SKILLS_TESTS_DIR`; `TestPerAccountStatePathContract`, `test_doc_counts.py`, and the new test file are all inside that selection.
6. `.venv/bin/ruff check claude/.claude/` — covers the new test file.
7. Fill the compression-diff audit table from `ai-instruction-and-memory-files` §2 for the one shortening in the diff: README's template block losing two bullets. Each removed bullet cites its surviving line in `claude/.claude/CLAUDE.md`'s new section. No CLAUDE.md line is removed or shortened by this change — the deleted `## Output Preferences` content survives verbatim as the new section's final bullet, which is itself a row in that table.
8. Read the finished `## Prose and Output Format` section against its own rules once, before commit: no sentence without information, one idea per sentence, one term per concept, no closing restatement. A section that violates the rules it states is the defect this step exists to catch.
9. Commit-time gate: `check-claude-md-length.sh` allows at 171 lines. `/code-review` will dispatch `comment-discipline-reviewer` (the diff modifies durable in-repo doc prose). No `SKILL.md`, agent file, or plugin-directory file is touched, so neither `/skill-review` nor `/agent-review` nor `plugin-semver` is implicated — per `.claude/rules/review-pipeline-dispatch.md`.
10. Confirm the out-of-repo prune landed: `<config-dir>/output-preferences.md` contains exactly the two kept-personal bullets and no promoted rule.

## Out of scope

- **Editing `claude/.claude/agents/comment-discipline-reviewer.md` or `claude/.claude/skills/tighten-prose/SKILL.md`.** Engineer's decision this session. Both auto-load CLAUDE.md at startup, so the promoted rules reach them without a cross-reference, and `docs/design-decisions.md` §9 documents the reviewer as deliberately closed-form for changes of this shape.
- **Adding `## Prose and Output Format` to the section lists in `claude/.claude/skills/code-review/SKILL.md:67` and `claude/.claude/agents/code-writer.md:56,99`.** Those lists focus a code-diff review over a file that is already loaded in full, so omitting the new section removes no rule from any agent's context. Adding it would pull hook-enforced `/skill-review` into a prose-only PR for an emphasis-only gain. Reconsider if the section proves under-applied in practice.
- **Promoting `tighten-prose` §4's ~20-25-word sentence target.** Safe application depends on §3's semantic carve-outs (hedges, quantifiers, negation, conditionals) sitting next to it, and those are too long for a 200-line always-loaded file. Stated alone, a numeric target pushes toward splitting precisely the sentences that must not be split — the regression §2 exists to prevent. It stays in `tighten-prose`, where its carve-outs are co-located.
- **Promoting `tighten-prose` §3's whole-sentence-class carve-out** (deploy and coordination steps, security-invariant claims, reviewer action items). It fires only inside `pr-description`'s flow and explicitly defers to that skill's own "Coordination-step preservation" section, so per `ai-instruction-and-memory-files` §4 it belongs in a skill body, not CLAUDE.md.
- **Changing the tone and emoji bullets.** Engineer's decision to keep both personal. The pre-existing partial overlap between the tone bullet's "no overclaiming, no hedging filler" and CLAUDE.md's "Be precise" is left as-is — it predates this change and the bullet's distinctly personal content ("disagree openly") is what earns its place.
- **Deduplicating `claude/.claude/rules/shell-script-conventions.md`'s local restatement of one-fact-per-sentence** ("Match CLAUDE.md's comment-length convention in every `#` block. State each non-obvious fact as one sentence"). Pre-existing, and a path-scoped rule is a separate load path that must stand alone when it fires.
- **Raising the 200-line cap or adding a per-file override in `check-claude-md-length.sh:66-71`.** The threshold is Anthropic-documented rather than a local preference, and the change fits with 29 lines to spare.
- **Measuring the change in correction volume.** Row 13 is honest about being unverified. A before/after measurement would need transcript-corpus analysis whose provenance is not publishable in this repo, and it is not what was asked for.
- **`install.sh`'s output-preferences tip (lines 686-694).** It names the README section, whose name is unchanged, and it enumerates no rule content.
