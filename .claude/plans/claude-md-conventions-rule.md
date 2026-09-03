# Split CLAUDE.md/AGENTS.md conventions into a path-scoped rule

## Context

Move the CLAUDE.md/AGENTS.md authoring mechanics out of
`ai-instruction-and-memory-files/SKILL.md` into a stowed path-scoped rule that
auto-loads whenever one of those files is open, and retire the skill's
215-line cap exception. That skill currently owns four file classes
(CLAUDE.md, AGENTS.md, `.claude/rules/*.md`, auto-memory) and splits into two
weakly-related halves: CLAUDE.md/AGENTS.md loading mechanics, and auto-memory
mechanics. Only the second half is what the skill's hook gates —
`require-memory-skill.sh` fires on writes under
`<config-dir>/projects/*/memory/`, never on CLAUDE.md edits — and the 215-line
cap exception's own stated rationale covers only that memory half. Why now:
the file is 197 lines against that 215 cap, and the unmerged
`rules-file-review-coverage` branch adds roughly 14 more, landing it near
211/215 with no headroom; that branch's own plan designates this split as "a
separate scope call carrying its own compression-diff audit." Intended
outcome: the CLAUDE.md/AGENTS.md authoring mechanics fire at the moment
someone edits a CLAUDE.md in any repo, the skill keeps everything its dispatch
sites promise, and the cap exception whose justification no longer matches is
removed.

### Decisions taken this session

- **Sequencing — land this PR first, standalone.** The
  `rules-file-review-coverage` branch (plan-only, no PR) rebases onto the
  smaller skill afterward. `[engineer-verified]`
- **Retire the 215-line cap override**, back to the 200 default. `[engineer-verified]`
- **Move set — authoring mechanics only; review-time content stays.** §1 and
  §4's AGENTS.md-adoption rows move. §2 (200-line cap, behavior test,
  compression-diff audit), §3, §5, and Step 1 stay, because both dispatch lines
  promise the skill owns "length cap, and behavior test" at review time and a
  path-scoped rule fires on file read, not on a diff. `[engineer-verified]`
- **No rename.** The skill keeps the name `ai-instruction-and-memory-files`. `[engineer-verified]`
- **Collapse §4's table entirely** rather than partially — 2 rows move, 1 folds
  into §2, the remaining 4 duplicate Step 1, §3, and §5. `[engineer-verified]`

## Approach

Move the CLAUDE.md/AGENTS.md *authoring mechanics* out of `ai-instruction-and-memory-files/SKILL.md` into a new stowed path-scoped rule, `claude/.claude/rules/claude-md-conventions.md`, that auto-loads whenever a CLAUDE.md, AGENTS.md, or `CLAUDE.local.md` is read in any repo. The skill keeps everything its four dispatch sites promise — placement routing, the 200-line cap, the behavior test, the compression-diff audit, duplicate-vs-reference, and auto-memory — shrinks from 197 lines to roughly 161, and gives up its 215-line cap override, whose stated rationale (the `require-memory-skill.sh` gate loads it at memory-write time) never covered the CLAUDE.md half being moved.

Two judgments beyond the engineer's Step 4 decisions carry the design, and both are called out below rather than buried: §4's decision-flow table is collapsed entirely rather than partially, because five of its seven rows duplicate Step 1, §3, or §5; and the surviving sections are renumbered, which forces edits to two cross-file pointers — one of which is **already pointing at the wrong section today**.

### Assumption ledger

**Root:** One skill owns two weakly-related file classes, but only the memory class is what its hook gates and what its 215-line cap exception was written for; the CLAUDE.md class has no trigger at the moment it is needed (someone editing a CLAUDE.md) and pays always-in-the-skill cost for a reader who may never touch memory.

**Givens** (conditions this plan treats as fixed, each with a reason it lies beyond the plan's reach):

- **G1.** Path-scoped rules fire on *read* of a matching file, not on every tool use, and reload after compaction as matching files are read. *Reason: Anthropic owns the rule-loading mechanism; no repo change alters it.* `[verified: code.claude.com/docs/en/memory.md, fetched 2026-09-03 — "Path-scoped rules trigger when Claude reads files matching the pattern, not on every tool use"; "rules with `paths:` frontmatter reload as Claude reads files they apply to"]`
- **G2.** Anthropic's published glob table documents `**/*.ts` as "All TypeScript files in any directory" and `*.md` as "Markdown files in the project root", and never states whether a leading `**/` matches zero path segments, nor whether `**` traverses dot-directories. *Reason: the primary source is silent on this specific question and the matching implementation is closed, so the semantics themselves are Anthropic's to define, not this plan's.* `[unverified]` **Observable, though not by reading docs:** the same primary source documents an `InstructionsLoaded` hook that "log[s] exactly which instruction files are loaded, when they load, and why," and names debugging path-specific rules as its use case — see row 3a. G2 is a given about the *documented semantics*, not a claim that the behavior is unobservable.
- **G3.** Rule files carry no length cap and no review-marker gate: `check-skill-length.sh`'s staged-path pattern matches only `SKILL.md` paths plus the single `plan-review/ROUTING.md` exception, and `plugins/skill-management/hooks/require-skill-review.sh` globs the same shapes. *Reason: both gates are scoped to the skill surface by design; widening either is a separate scope call.* `[verified: claude/.claude/hooks/check-skill-length.sh:86; require-skill-review.sh:108-109]`
- **G4.** `/code-review`'s dispatch line names `CLAUDE.md`, `AGENTS.md`, and `<config-dir>/projects/*/memory/` but not `.claude/rules/*.md`. *Reason: the unmerged `rules-file-review-coverage` branch's row1 owns that fix and its own plan designates this split as a separate scope call.* `[engineer-verified: Step 4 decision 1]`
- **G5.** The sibling branch will add roughly 14 lines to this SKILL.md when it rebases. *Reason: that branch's content is already drafted and not this plan's to change.* `[engineer-verified: context paragraph]`

**Mechanisms and material assumptions:**

1. **Create `claude/.claude/rules/claude-md-conventions.md`** holding §1 in full (`:39-67`) plus §4's two AGENTS.md-adoption rows (`:121`, `:124`). *Justification: this content is needed exactly when a CLAUDE.md/AGENTS.md is open, which is the definition of a path-scoped rule's trigger; the source doc names this mechanism as the remedy for a growing instruction surface.* `anchors: root` `[verified: memory.md — "If your instructions are growing large, use path-scoped rules so instructions load only when Claude works with matching files"]`

2. **File name is `claude-md-conventions.md`.** *Justification: matches the `<topic>-conventions.md` shape of three of four existing stowed rules and the engineer's own branch slug; `instruction-file-conventions.md` is marginally more accurate about AGENTS.md coverage but diverges from the slug for no behavioral gain.* `anchors: row1` `[verified: claude/.claude/rules/ listing; branch name GH-833/claude-md-conventions-rule]`

3. **Ship a defensive glob set: every target basename in both `**/`-led and bare form.** *Justification: under G2 the failure directions are asymmetric — a redundant entry costs one line against a documented 1,000-expanded-pattern budget, while a non-matching `**/CLAUDE.md` silently never fires for the repo-root file, which is the single most important case and produces no error surface.* `anchors: row1` `[verified: memory.md — "a rule's whole `paths` list shares one budget of 1,000 expanded patterns and 4 MiB"; this repo's own prior rules plan names "a malformed glob silently matches nothing" as the global-rule failure mode at .claude/plans/global-path-scoped-rules-cicd-sql.md:232]`

3a. **`InstructionsLoaded` is the vendor-documented way to observe rule loading, and Verification 6 uses it.** *Justification: an earlier draft dismissed it because "that event name appears nowhere else in the repo" — repo-absence is not evidence about a vendor mechanism, and the primary source documents it for exactly this purpose. Adopting it is a scratch-session diagnostic, not a shipped hook: nothing is added to `settings.json` on this branch.* `anchors: row3` `[verified: code.claude.com/docs/en/memory.md, fetched 2026-09-03 — "Use the `InstructionsLoaded` hook to log exactly which instruction files are loaded, when they load, and why. This is useful for debugging path-specific rules or lazy-loaded files in subdirectories."]` `[verified: not configured in this repo — grep for InstructionsLoaded in claude/.claude/settings.json and docs/hooks.md returns nothing; prior proposal at .claude/plans/global-path-scoped-rules-cicd-sql.md:278-280]`

3b. **The check stays non-gating, and that is a decision rather than an oversight.** *Justification: row 3's glob set is engineered to be correct under either answer, so an earlier check could not change the drafted frontmatter — it can only license a later simplification. Sequencing it after implementation is therefore not self-inflicted staleness. If the check does run and shows `**/`-led globs match repo-root files, drop the five bare-basename entries in a follow-up, which also retires row 4's handover to the sibling branch's portability test.* `anchors: row3` `[verified: row 3's redundancy argument; the ten entries sit against a documented 1,000-expanded-pattern budget]`

3c. **Editing an existing CLAUDE.md reliably triggers the rule, because Edit requires a prior Read of the target in-session.** *Justification: this is the mechanism the headline benefit actually rests on, and leaving it unstated makes the claim look like an assertion about globs alone. Per G1 the rule fires on that Read.* `anchors: root` `[unverified — the Edit-requires-prior-Read precondition is harness behavior stated in this session's tool contract, not something checked against a primary source this session; the claim degrades to row 7's disclosed gap if it does not hold]`

4. **The bare entries are portable despite carrying a literal first segment.** *Justification: `CLAUDE.md` and `.claude/CLAUDE.md` are the canonical locations in every repo, not one repo's layout, so the "stowed globs must be `**/`-led" heuristic the sibling branch plans to test is a proxy for portability that over-fires here.* `anchors: row3` `[unverified — the sibling branch's row3d test does not exist yet; this reasoning is handed to it, see Out of scope]`

5. **The glob set mirrors `check-claude-md-length.sh`'s already-shipped canonical shape**, extended with the nested-directory and `CLAUDE.local.md` cases the skill's own precedence list names. *Justification: reusing a regex this repo already treats as the authoritative CLAUDE.md/AGENTS.md path set beats inventing a second definition of "an instruction file."* `anchors: row3` `[verified: claude/.claude/hooks/check-claude-md-length.sh:73 regex; SKILL.md:65 names CLAUDE.local.md in the precedence list]`

6. **The read-trigger gap on cold CLAUDE.md creation is accepted, not closed with a new hook.** *Justification (over-powered-primitive check): two heavier options were considered and both fail. A `require-claude-md-skill.sh` PreToolUse gate mirroring `require-memory-skill.sh` would add a per-write blocking gate to every stow consumer's every repo for a first-authoring case, which is a more privileged mechanism than the advisory content warrants; extending `require-skill-review.sh`'s glob to rules files fails because `/skill-review` audits frontmatter, trigger lists, and description budget, none of which a rule file has, and it lives in a plugin whose version would have to bump for a gate that misfires. Two lighter primitives already in hand suffice: (a) `**/AGENTS.md` and `AGENTS.md` are in the glob set, so the near-universal precursor read in the "this repo has an AGENTS.md" case — the case where the moved content matters most — triggers the rule; (b) `/code-review`'s existing dispatch of this skill for CLAUDE.md/AGENTS.md review covers the edit-and-review path, backed by the skill's own pointer line (row 9).* `anchors: root` `[verified: code-review/SKILL.md:200; G1]`

7. **Residual gap, stated honestly:** creating a CLAUDE.md in a repo with no AGENTS.md and no prior read of either file may not load the rule. *Justification for accepting: in that repo the moved content's highest-value fact — import the existing AGENTS.md — is moot by construction, and precedence/concatenation facts matter at edit time more than at creation.* `anchors: row6` `[unverified — whether a Write to a matching path itself triggers the load is not stated in the source and is not guessed at here]`

8. **Collapse §4 (`:117-128`) entirely**, keeping only `:125`'s over-cap trim guidance, folded into §2 as a sentence. *Justification: after `:121` and `:124` move, `:122`, `:123`, `:126`, and `:127` each have a surviving equivalent, leaving one non-duplicated row and a table's worth of scaffolding — a single-source-of-truth defect under CLAUDE.md §Engineering Judgment rather than content worth preserving.* **The survivors are §5's "Where does a given rule belong?" table, not Step 1.** Step 1 is scoped to memory-write decisions (`:23`, "Before writing any file under `memory/`"), so its items 3 and 2 answer "skill vs memory," not "CLAUDE.md vs skill" — citing them would misroute the compression-diff audit. The correct survivors: `:122` → `:161` ("Rule that fires only inside a specific skill's flow | That skill's SKILL.md **(not CLAUDE.md, not auto-memory)**"), `:123` → `:162`, `:126` → §3, `:127` → §5's CLAUDE.md-vs-memory row. `anchors: root` `[verified: SKILL.md:117-127 and :23 read against :157-166 and :102-115 this session]` `[engineer-verified: approved at Step 4 after being flagged as exceeding decision 3's letter]`

9. **The skill leaves a pointer, not a copy.** *Justification: across all eight existing rule files there is zero body-content duplication with any skill and the established shape is a one-line disclaimer of overlap; the pointer must exist in the skill direction too, because `/code-review:200` dispatches the skill for CLAUDE.md review and a reviewer reading a diff may have no matching file open to trigger the rule.* `anchors: row6` `[verified: claude/.claude/rules/sql-ddl-conventions.md:8-10 precedent; zero body-content duplication across all 8 rule files]`

10. **The skill's pointer may name `~/.claude/rules/claude-md-conventions.md` literally.** *Justification: `TestPerAccountStatePathContract` forbids literal `~/.claude/` state paths in SKILL.md bodies, but its regex alternation excludes stowed directories and its own comment names `rules/` as one of them.* `anchors: row9` `[verified: claude/.claude/skills/tests/test_skills.py:2816-2838 — "unlike a stowed path (agents/, hooks/, rules/, scripts/, skills/), which resolves identically under every account"]`

11. **Renumber the survivors — §2→§1, §3→§2, §5→§3 — rather than leaving a gap at §1.** *Justification: keep-gaps costs zero edits now but leaves a file whose architecture section opens at "## 2." and sets a trap where any future renumber silently redirects a cross-repo pointer to different content rather than breaking it; renumbering now, and converting the two cross-file pointers to section names, removes the ordinal coupling permanently. This is CLAUDE.md's "a narrowly-scoped label pushes up to the canonical name."* `anchors: root` `[verified: cross-refs at SKILL.md:25, :31, :165 read this session]`

12. **`plugins/lovable-cloud/skills/lovable-cloud-knowledge/SKILL.md:97` points at the wrong section today.** It cites "`ai-instruction-and-memory-files` §3" for "the same length and behavior-test discipline as Claude Code skills" — but length targets and the behavior test are §2; §3 is duplicate-vs-reference. Renumbering would have converted a wrong-but-adjacent pointer into a silently-wrong pointer at auto-memory. *Justification for fixing it here: the plan must edit this line anyway under row 11, and repairing a pointer while editing it is cheaper than a follow-up.* `anchors: row11` `[verified: SKILL.md:69-100 vs :102-115 vs plugins/lovable-cloud/skills/lovable-cloud-knowledge/SKILL.md:93-97, all read this session]`

13. **Editing the lovable-cloud plugin costs a patch version bump and a second `/skill-review`.** `3.2.6` → `3.2.7`. *Justification: a prose pointer fix with no rule change is patch-level; the bump is hook-enforced by `require-plugin-version-bump.sh` for any file under a plugin directory.* `anchors: row12` `[verified: plugins/lovable-cloud/.claude-plugin/plugin.json version 3.2.6; .claude/rules/review-pipeline-dispatch.md]`

14. **Citation split correction — the five-move/one-shared count from exploration is wrong.** Only two REFERENCES.md entries move: the claude-code CHANGELOG (zero AGENTS.md entries) and the agents.md standard (supporting-tools list). Best Practices (hooks "guarantee the action happens"), Context Rot, and HumanLayer all ground §2's length targets and §3's structural-enforcement bullet, both of which stay. The Anthropic memory-docs URL is genuinely shared. *Justification: verified by reading REFERENCES.md against the sections that stay, not inherited.* `anchors: root` `[verified: claude/.claude/skills/ai-instruction-and-memory-files/REFERENCES.md read against SKILL.md:69-115 this session]`

15. **The shared Anthropic URL appears in both citation files; the *quotes* do not.** `docs/rules-references.md`'s new section carries the two moved verbatim quotes; REFERENCES.md drops those two and keeps the rest. *Justification: a repeated URL is a locator, not duplicated knowledge; a repeated quote is the duplication CLAUDE.md's single-source-of-truth rule targets. Neither file is loaded at runtime and their audiences are disjoint.* `anchors: row14` `[verified: docs/rules-references.md:5 — "One section per rule file; one entry per claim within a section"]`

16. **Retire the 215-line override, restoring the 200 default.** *Justification: the override's stated rationale is that gate-loaded routing content can't move to a narrower surface — false once the CLAUDE.md half has a narrower surface.* `anchors: root` `[engineer-verified: Step 4 decision 2]` `[verified: claude/.claude/hooks/check-skill-length.sh:22-24, :74-75]`

17. **Retiring the cap is gated on a hard line-count ceiling of 180 for the staged SKILL.md.** Estimate is ~166 (197 − 30 for `:39-68`, − 12 for `:117-128`, + 7 for the folded trim list, + 4 for the rewritten header). 180 leaves ≥20 lines against G5's ~14. *Justification: if the file lands above 180, the sibling rebase would push it near the 200 default with no headroom and the cap retirement is not safe — stop and report rather than keeping the override.* `anchors: row16` `[verified: line arithmetic against the file read this session; the hook counts staged content including frontmatter and blanks]`

18. **The two override tests are replaced by one default-limit test, not deleted.** `test_memory_files_skill_over_default_under_override_allows` and `test_memory_files_skill_over_override_denies` become `test_memory_files_skill_falls_to_default_limit` (init at 195, restage at 205, expect deny). *Justification: deleting both leaves nothing pinning the retirement, so a future re-add of an override passes CI; the allow-under-limit direction is already covered by the generic default cases.* `anchors: row16` `[verified: claude/.claude/hooks/tests/test_check_skill_length.py:433-483]`

19. **Enforcement loss is accepted for one PR window.** The moved content leaves both the length cap (G3) and the `/skill-review` gate (G3). *Justification (over-powered-primitive check): the two heavier closures are enumerated in row 6 and both fail; the two lighter primitives already in place are `test_rules_frontmatter.py`, which auto-discovers the new file via `rglob` and fails CI on malformed frontmatter with zero test edits, and the sibling branch's row1 addition of `.claude/rules/*.md` to `/code-review:200`, which closes the dispatch gap on a branch already scoped for it. Inventing a line cap for rule files would additionally be an ungrounded numeric literal — Anthropic publishes a 200-line threshold for CLAUDE.md, not for rules, and conditional loading is the reason rules are cheap.* `anchors: root` `[verified: claude/.claude/skills/tests/test_rules_frontmatter.py auto-discovery; G4]`

20. **`select-tests.py` widens correctly for this diff without manual intervention.** `claude/.claude/rules/` maps via `RULES_DIR` to `SKILLS_TESTS_DIR`; touching `check-skill-length.sh` pulls in `HOOKS_TESTS_DIR`. *Justification: no full-suite run is needed.* `anchors: root` `[verified: claude/.claude/scripts/select-tests.py:33, :301-302, :325]`

21. **The compression-diff audit is mandatory and its Surviving column has a load condition.** §2 requires the table for any diff that removes or shortens lines. *Justification: "the rule file now says it" is a valid Surviving citation only where the rule demonstrably loads for the reader in question — so each Y row must name the reader and the trigger, and any row whose only survivor is a glob whose zero-segment behavior is `[unverified]` (G2) cannot be scored Y on the rule alone; it needs the skill's pointer line as a co-survivor.* `anchors: root` `[verified: SKILL.md:88-94]`

22. **Rollback needs a forward version bump, not a plain revert.** After this PR merges, a revert branch's merge-base with `origin/main` already contains lovable-cloud `3.2.7`; restoring `3.2.6` fails `version_strictly_greater` and the commit is denied. *Justification: revert the content and bump to `3.2.8` — do not restore the old version. Everything else in the diff reverts cleanly in one commit, and `claude/.claude/**` returns to the prior state for consumers on `git pull` with no re-install.* `anchors: root` `[verified: plugins/plugin-semver/hooks/require-plugin-version-bump.sh:106 sets BASE from `git merge-base HEAD origin/$DEFAULT_BRANCH`; :253 requires version_strictly_greater; header comment :32-34 states the merge-base rationale]`

23. **This move is not the cap-dodge two existing rules forbid, and the plan says so in the repo, not only here.** `docs/skills.md:123` ("Shorten first, do not extract: an auxiliary adds Read-tool indirection without reducing context cost") and `.claude/rules/skill-and-agent-self-review.md:23` ("never as a way to route around a file's length cap") both target **co-located auxiliaries a skill Reads at runtime** — a mechanism that adds indirection without reducing cost for anyone. A path-scoped rule is independently triggered and does reduce cost for sessions that never open a CLAUDE.md. *Justification: without codifying the distinction, this PR becomes citable precedent for extracting to a rule purely to relieve a cap. The test is whether the content's need-moment matches a file-read trigger — not whether the file was near its limit.* `anchors: root` `[verified: docs/skills.md:123 and .claude/rules/skill-and-agent-self-review.md:23, both read this session; memory.md's own remedy sentence quoted in row 1]`

24. **The review-path pointer is a soft fallback, not a closed gap.** `/code-review:200` dispatches this skill for CLAUDE.md review; whether the dispatched context already holds a full-file Read (which would fire the rule on its own) is untested. *Justification: row 9's pointer is prose a reader must notice and follow — the instruction-vs-mechanism weakness row 14's own Best Practices citation argues against elsewhere. Stating it as soft is honest; claiming the gap is closed is not.* `anchors: row9` `[unverified — not checked whether a specialist dispatch receives the full file or only changed line ranges]`

### Drafted text

**`claude/.claude/rules/claude-md-conventions.md` (new):**

````markdown
---
paths:
  - "**/CLAUDE.md"
  - "CLAUDE.md"
  - "**/AGENTS.md"
  - "AGENTS.md"
  - "**/CLAUDE.local.md"
  - "CLAUDE.local.md"
  - "**/.claude/CLAUDE.md"
  - ".claude/CLAUDE.md"
  - "**/.claude/AGENTS.md"
  - ".claude/AGENTS.md"
---

## CLAUDE.md and AGENTS.md conventions

How Claude Code loads these files. Length targets, the per-line behavior
test, the compression-diff audit, and duplicate-vs-reference judgment live
in the `ai-instruction-and-memory-files` skill — this rule doesn't overlap
them. Full citations and verbatim quotes live in `docs/rules-references.md`
in the claude-config repo.

### Claude Code loads CLAUDE.md only — not AGENTS.md

Per Anthropic's Claude Code memory docs, Claude Code reads CLAUDE.md, not
AGENTS.md. When a repo already uses AGENTS.md for other coding agents,
create a CLAUDE.md that imports it so both tools read the same instructions
without duplicating them.

The Anthropic-documented single-source-of-truth pattern is:

```
@AGENTS.md

# Claude-specific content below this line
```

Put `@AGENTS.md` as the first line of CLAUDE.md. Claude Code imports the
referenced file's content; maintenance is single-source, no duplication.

`@path` imports resolve relative to the file containing the import, not the
current working directory. A `@docs/x.md` in `.claude/CLAUDE.md` looks for
`.claude/docs/x.md`.

### Precedence within the CLAUDE.md family

Concatenated, not overridden:

1. Managed policy (enterprise)
2. Project `./CLAUDE.md` or `./.claude/CLAUDE.md`
3. User `~/.claude/CLAUDE.md` (global)
4. `CLAUDE.local.md`

Claude Code walks from the current working directory up to `/`,
concatenating every `CLAUDE.md` it finds along the way — ancestor
instructions are additive, not overridden. In monorepos this means
root-level CLAUDE.md, team-directory CLAUDE.md, and project-level CLAUDE.md
all load together.

### Adding a guardrail, or adding AGENTS.md to a repo that has neither

- **A new cross-agent guardrail goes in AGENTS.md** (canonical). Claude Code
  gets it via the `@AGENTS.md` import; other AGENTS.md-aware agents (Codex,
  Cursor, Aider, Gemini CLI, Windsurf, Amp, Lovable) read it natively.
- **Add AGENTS.md to a CLAUDE.md-only repo only if a non-Claude
  AGENTS.md-aware agent also uses the repo.** Otherwise CLAUDE.md alone is
  fine.
````

The `(independently corroborated: zero AGENTS.md entries in the Claude Code changelog, and Claude Code is absent from agents.md's supported-tools list)` parenthetical at `SKILL.md:41` does **not** come across — it is provenance, not behavior, and it moves to `docs/rules-references.md`. The bare authority tag "Per Anthropic's Claude Code memory docs" stays in the rule body, per `docs/skills.md:126` carve-out 2: the claim is contestable mid-session and the references file is not loaded then. Score this as a Y row in the audit with that citation, not as an unexplained drop.

**Replacement for `SKILL.md:35-37` (architecture header paragraph):**

```
The facts below come from primary sources (URLs in co-located REFERENCES.md);
open REFERENCES.md only to verify a specific URL or quote.

How Claude Code loads CLAUDE.md/AGENTS.md — the `@AGENTS.md` import, `@path`
resolution, precedence, and when a repo should add an AGENTS.md — lives in
`~/.claude/rules/claude-md-conventions.md`. It auto-loads whenever one of
those files is open. Read it directly when reviewing a diff with neither open.
```

The second clause of line 1 is deliberate: the current `:36-37` carries "open REFERENCES.md only to verify a specific URL or quote," which still governs the sections that stay and would otherwise be dropped with no survivor. "When CLAUDE.md / AGENTS.md questions arise, start here" is correctly not carried over — after the split the skill is no longer where those questions start.

**Sentence folded into §2 (post-renumber §1), replacing the deleted `:125` row:**

```
**Over the cap — trim in this order:**

1. Delete content duplicating AGENTS.md; use the `@AGENTS.md` import instead.
2. Collapse narrative case studies into one-sentence principles.
3. Keep only Claude-Code-specific project context.
```

**Cross-reference edits (post-renumber §1 = Length targets, §2 = Duplicate vs. reference, §3 = Auto-memory):**

- `SKILL.md:25` → `(See §2 advisory vs deterministic, §3 anti-duplication heuristic.)`
- `SKILL.md:31` → `If step 1, 2, 3, or 5 produces a destination, write there and stop — see §3 for the full routing table.`
- `SKILL.md:165` → `**Nowhere — delete it** (§2 advisory vs deterministic)`
- `plugins/lovable-cloud/skills/lovable-cloud-knowledge/SKILL.md:97` → `discipline as Claude Code skills (see the length-targets and behavior-test guidance in ai-instruction-and-memory-files).`
- `docs/design-decisions.md:569` → ``- `claude/.claude/skills/ai-instruction-and-memory-files/SKILL.md`'s length-targets section — the 200-line cap and the per-line behavior test each promoted line was drafted against.`` — this repairs a locator inside a preserved decision record rather than rewriting the recorded fact, so CLAUDE.md Axis 3 is satisfied; leaving it stale would corrupt the record.

**`check-skill-length.sh`:** delete the `:22-24` comment block in full and the `:74-75` case arm in full. No replacement comment — the absence of an override needs no explanation.

**`docs/skills.md:20`** →

```
- **`/ai-instruction-and-memory-files`** — Claude Code auto-memory and the review discipline for AI instruction files: which surface a rule belongs in, length targets, the per-line behavior test, duplication rules, and MEMORY.md index format. Loading mechanics and the `@AGENTS.md` import pattern live in `claude/.claude/rules/claude-md-conventions.md`.
```

**`README.md:245`** → append the new domain: `...holds CI/infra, SQL/DDL, and CLAUDE.md/AGENTS.md loading conventions that apply across every repo the user opens, not just this one.`

### Alternatives set aside

**Keep the section numbers and leave a gap at §1** — zero cross-file edits, no plugin version bump. Set aside per row 11: it leaves the architecture section opening at "## 2." and preserves the ordinal coupling that row 12 shows has *already* drifted once.

**Copy §1 into the rule and keep it in the skill too** — would remove the trigger gap entirely. Set aside because it is the exact defense-in-depth shape §3 excludes ("An AGENTS.md-aware agent already reads it natively… one canonical source covers both"), and it defeats the plan's own line-count goal, which is what makes the 215-cap retirement safe.

**Widen `check-skill-length.sh`'s staged-path pattern to `claude/.claude/rules/`** — cheap, since `_lib_staged_length_gate` is already shared. Set aside per row 19: no primary source publishes a line threshold for rule files, so any number chosen would be an ungrounded literal.

**Rename the skill to reflect its narrowed scope** — set aside; `[engineer-verified: Step 4 decision 4]`, and `require-memory-skill.sh:142`'s deny text plus `transcript-analysis.py:1124` embed the current name.

### Dispatch split

**One `code-writer` dispatch, not split.** The skill diff, the rule file's content, and the cap retirement are scored by one compression-diff audit against one move set; splitting would force restating that move set in every prompt and let two agents resolve the §4-collapse boundary differently, with neither self-review seeing the other's. The dispatching session — not `code-writer` — runs `/skill-review` (twice: the skill and the lovable-cloud skill), `plugin-semver`, `/ai-instruction-and-memory-files`, and `/code-review`: `code-writer` declares no `Skill` tool and is in `_LIB_NO_GATE_RELEASE_AGENTS`, so every review gate this diff trips must be cleared by the dispatcher.

## Critical files

**Create:**
- `claude/.claude/rules/claude-md-conventions.md` — the drafted rule above. No test edit needed: `claude/.claude/skills/tests/test_rules_frontmatter.py` discovers it by `rglob` and validates the `paths` list automatically.

**Modify:**
- `claude/.claude/skills/ai-instruction-and-memory-files/SKILL.md` — delete `:39-68` (§1 + trailing blank) and `:117-128` (§4 + trailing blank); rewrite `:35-37`; renumber the three surviving `##` headings; fold the trim sentence into §2; update cross-refs at `:25`, `:31`, `:165`. Do not touch Step 0 (`:12-19`), the Final step (`:190-197`), either `HOOK_TEST_FIXTURE` comment, or the two `marker.sh` invocation strings — `test_require_memory_skill.py` reads the fixtures and `_MARKER_TRIPLE_SITES` pins the invocations. Also leave `:149`'s two pinned phrases intact (`TestMemorySkillPreservesActionPrescribingTrigger`).
- `claude/.claude/skills/ai-instruction-and-memory-files/REFERENCES.md` — remove the claude-code CHANGELOG and agents.md entries; remove the two moved quotes from the Anthropic memory-docs bullet and trim its claim list to the 200-line cap, the auto-memory role split, and the MEMORY.md load limit. Keep Best Practices, Context Rot, and HumanLayer (row 14).
- `claude/.claude/hooks/check-skill-length.sh` — delete `:22-24` and `:74-75`.
- `claude/.claude/hooks/tests/test_check_skill_length.py` — replace `:433-483`'s two tests with `test_memory_files_skill_falls_to_default_limit` (row 18), reusing `make_skill_content` and the existing repo-init boilerplate. Give it a `Regression test:` docstring naming the bug it guards — a re-added override — and why 195/205 are the boundaries: 205 denies under the 200 default but would allow under any re-added override ≥ 205, so those values are what make the test catch the reintroduction rather than arbitrary picks.
- `claude/.claude/skills/tests/test_rules_frontmatter.py` — add a self-consistency glob-match test for the new rule's ten `paths:` entries, asserting each matches at least one representative candidate path (`CLAUDE.md`, `sub/CLAUDE.md`, `.claude/CLAUDE.md`, `sub/.claude/AGENTS.md`, `CLAUDE.local.md`) via `fnmatch`/`PurePosixPath`. This cannot replicate Claude Code's closed matcher (G2) and must say so in its docstring — it catches the typo/self-inconsistency class the module's own docstring names (`"cluade/.claude/rules/**"`), which today ships with no CI signal. Five of the ten entries use a bare-basename shape no existing rule file uses.
- `claude/.claude/skills/tests/test_skills.py` — add a parametrized §-ordinal-to-heading pin over the four cross-references this change rewrites (`SKILL.md:25`, `:31`, `:165`, and the lovable-cloud pointer), asserting each cited ordinal resolves to a heading whose text still matches the content it claims. Model it on `:708-720`'s `body.index()` ordering check, which exists for exactly this failure. Row 12 proves the class is live: a pointer already drifted to the wrong section undetected, and Verification 5's manual `git grep` would not have caught it either.
- `plugins/lovable-cloud/skills/lovable-cloud-knowledge/SKILL.md:97` — de-ordinalize the pointer (row 12).
- `plugins/lovable-cloud/.claude-plugin/plugin.json` — `3.2.6` → `3.2.7`.
- `docs/rules-references.md` — new `## CLAUDE.md and AGENTS.md conventions` section, placed to match the file's existing one-section-per-rule-file layout: the `code.claude.com/docs/en/memory` URL with a 2026-09-03 fetch date, the two moved verbatim quotes (CLAUDE-not-AGENTS.md, and the import-pattern sentence), the corroboration note dropped from the rule body (row 1's note), and a subsection recording the glob decision — that zero-segment `**/` behavior is undocumented, that the bare-basename entries exist to cover repo-root files, and that a bare basename with no directory separator is portable across repos in a way a repo-specific literal prefix is not.
- `docs/skills.md:20` — drafted above.
- `docs/design-decisions.md:569` — drafted above.
- `README.md:245` — drafted above.
- `docs/skills.md` — extend the Skill architecture notes bullet at `:123`. Append exactly this, and nothing from the surrounding plan prose:

  ```
  This governs a co-located auxiliary the skill Reads at runtime, which adds
  indirection without reducing anyone's context cost. Relocating content to a
  path-scoped rule is a different mechanism: it is legitimate when the
  content's need-moment matches a file-read trigger, and cap pressure alone
  does not license it.
  ```

  (Justification: row 23. Without the addition this PR becomes citable precedent for cap-dodging.)
- `CHANGELOG.md` — one `[Unreleased]` → `### Changed` entry stating the move, the auto-load trigger, the retired 215-line cap, and — as a Migration note — that the change is live on `git pull` with no re-install, since `claude/.claude/**` is stowed. Add one clause to the Migration note, exactly this text:

  ```
  Authoring a brand-new CLAUDE.md in a repo with no AGENTS.md may not surface
  this guidance, because path-scoped rules load on file read.
  ```

  (Justification: row 7's residual gap. The clause lets a consumer who sees nothing self-diagnose instead of filing it.)

**Reuse (do not reimplement):**
- `claude/.claude/skills/tests/test_rules_frontmatter.py` — auto-discovery; adding the file is the whole integration.
- `_lib_staged_length_gate` in `claude/.claude/hooks/_lib.sh` — untouched; only `limit_for()` changes.
- `claude/.claude/rules/sql-ddl-conventions.md:8-10` and `github-actions-workflows.md:12-13` — the overlap-disclaimer and repo-qualified-citation-pointer shapes the new rule copies. Use `github-actions-workflows.md`'s qualified form ("in the claude-config repo"); `sql-ddl-conventions.md:13` omits that qualifier and is the weaker precedent.
- `make_skill_content` and the repo-init block in `test_check_skill_length.py` — for the replacement test.

## Verification

Run from the worktree. The contributor `.venv` lives only in the main worktree root; linked worktrees sit exactly three levels deep, so the documented cross-worktree form is `../../../.venv/bin/...` (README.md:512, :533).

1. **Scoped test suite:** `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py`, then run exactly what it selects. This diff touches `check-skill-length.sh`, so the selection includes `HOOKS_TESTS_DIR` alongside `SKILLS_TESTS_DIR`; no hand-widening to the full suite (CLAUDE.md's Commands section).
2. **Lint:** `../../../.venv/bin/ruff check claude/.claude/` and `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck` — a shell file is edited.
3. **Line-count gate (blocking):** after staging, count the staged SKILL.md blob with the hook's own method (`git show` of the staged path piped through `awk 'END{print NR}'`). It must print **≤ 180**, frontmatter and blank lines included. Above 180, stop and report per row 17 — do not restore the override to make the number fit.
4. **Compression-diff audit (blocking, row 21):** produce the Removed/Surviving/Behavior-preserving table for every removed or shortened line, covering §1's 29 lines, §4's seven rows, the corroboration parenthetical, the two dropped REFERENCES.md quotes, **and the `:35-37` header rewrite** — that last one drops "open REFERENCES.md only to verify a specific URL or quote," an instruction with no survivor in the drafted replacement and one that still governs the sections that stay. Score it explicitly: restore the clause, or record an N with a stated acceptance rationale. Each Y row names the surviving line *and* the reader for whom it loads. **Cite §5's table rows (`:161`, `:162`) as the survivors for `:122`/`:123`, not Step 1's items** — see row 8. Any row whose only survivor is the rule file must additionally cite the skill's pointer line, because the glob's zero-segment behavior is unverified (G2). Any N restores the instruction.
5. **Stale-ordinal sweep:** `git grep -n 'ai-instruction-and-memory-files' -- '*.md'` and a `git grep -n` for section-sign ordinals across the skill directory, `plugins`, `docs`, and `README.md` — confirm no surviving reference points at a section number that changed meaning. Expect exactly the five sites named in Critical files.
6. **Glob load check (best-effort, non-gating):** in a scratch throwaway repo containing only a root `CLAUDE.md`, open a fresh session, read that file, and check whether the new rule's content is present in context (`/context`, or by asking the session to quote the rule's overlap-disclaimer sentence without opening the rule file). A negative or inconclusive result does not block: the defensive glob set (row 3) makes the answer non-load-bearing, and a positive result only licenses a later simplification. Record the outcome in the PR body either way, and if `**/`-led globs do match repo-root files, open a follow-up to drop the five bare-basename entries (row 3b).

   **Use the `InstructionsLoaded` hook for this check.** Anthropic's memory docs document it as logging "exactly which instruction files are loaded, when they load, and why," and name debugging path-specific rules as the use case — it answers the question directly instead of inferring load from what a session can quote. Configure it in the scratch repo only; nothing is added to this repo's `settings.json`. An earlier draft rejected it because the event name appears nowhere in this repo, which is evidence about this repo, not about the mechanism (row 3a).
7. **Rollback note in the PR body (row 22):** state that reverting this PR requires bumping lovable-cloud to `3.2.8`, not restoring `3.2.6` — `require-plugin-version-bump.sh` compares against merge-base and denies a non-increasing version, so a plain `git revert` is blocked at the commit gate with no pointer back to this cause.
8. **Redaction sweep:** grep the diff for private-project tokens before staging. The rule ships to every stow consumer and the plan file commits to a public repo.
9. **Review pipeline, in order:** `/ai-instruction-and-memory-files` on its own diff *and* on the new rule file (the skill's own domain per `plan-review/SKILL.md:236`, and `.claude/rules/skill-and-agent-self-review.md` requires running the skill on its own edit); `/skill-review` on `ai-instruction-and-memory-files/SKILL.md` and again on `lovable-cloud-knowledge/SKILL.md` (hook-enforced); `plugin-semver` for the lovable-cloud bump (hook-enforced); `/code-review` on the full staged diff. Then commit, `/ready-for-review`, push, open the PR. Do not merge.

## Out of scope

- **Adding `.claude/rules/*.md` to `/code-review/SKILL.md:200`'s dispatch line.** Owned by the unmerged `rules-file-review-coverage` branch's row1, whose own plan designates this split as a separate scope call. Leaving it means one PR window in which the new rule file gets no per-file-type review dispatch — accepted per row 19.
- **The sibling branch's `**/`-led portability test (row3d).** It will fire on this rule's five bare-basename entries. The carve-out it needs, handed over rather than pre-implemented here: a bare entry with **no directory separator** (`CLAUDE.md`, `AGENTS.md`, `CLAUDE.local.md`) is a canonical filename in every repo and is portable; a bare entry with a separator is allowed only when its leading segment is `.claude/`, likewise canonical. The reasoning and the underlying zero-segment uncertainty are recorded in `docs/rules-references.md` so that branch's author finds them from the test failure.
- **Renaming the skill.** `[engineer-verified: Step 4 decision 4]`.
- **Moving §2, §3, §5, or Step 1 out of the skill.** `[engineer-verified: Step 4 decision 3]`. Renumbering is not a move.
- **A length cap or `/skill-review` gate on `.claude/rules/*.md`.** Both enumerated and rejected in rows 6 and 19.
- **Fixing `claude/.claude/rules/sql-ddl-conventions.md:13`'s unqualified `docs/rules-references.md` pointer.** Real — that file is stowed and fires in repos where the path does not resolve — but it is a different rule file, outside this ticket's file boundary. Raise to the PR reviewer.
- **`docs/skills.md:20`'s "Cursor rules, Lovable knowledge" claim.** The skill body covers neither. The prescribed edit corrects only what this change moved; the broader accuracy question is a separate call.
- **`.claude/rules/review-pipeline-dispatch.md`.** Mentions neither this skill nor CLAUDE.md/AGENTS.md, and this change adds no new per-file-type dispatch.
- **Any change to `require-memory-skill.sh`, `check-claude-md-length.sh`, or the memory half of the skill.** The gate, its deny text, and the CLAUDE.md 200-line cap are all unaffected by this move.
