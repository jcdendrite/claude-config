# Rules-file review coverage

## Context

`.claude/rules/*.md` path-scoped rule files have no review coverage at
code-review time. `/skill-review` is scoped to SKILL.md, `/agent-review` to
agent files, and `/ai-instruction-and-memory-files` to
CLAUDE.md/AGENTS.md/auto-memory — so a rules-file edit reaching commit is
reviewed only by generic `/code-review`. Why now: rule files carry real
behavioral authority (they auto-load into any session that opens a matching
file), the stowed `claude/.claude/rules/` set applies in *every* repo a stow
consumer opens, and a live latent defect of exactly the uncaught class already
exists in the repo (S19, below). Intended outcome: rule files get the same
review coverage at code-review time that they already have at plan time, plus
a mechanical check for the glob-correctness failure mode that no human review
reliably catches.

The asymmetry has a known origin. PR #721 (`ad1f199`, "Plan it consider
rules") added `.claude/rules/` awareness to `plan-it`, `plan-review`, and
`ai-instruction-and-memory-files` — and touched `code-review/SKILL.md`
nowhere. This is an unfinished sibling arm, not an undesigned area.

### Decisions taken this session

- **Scope** — "dispatch fix + glob test," over prose-only and over authoring a
  new `rules-review` skill. `[engineer-verified]`
- **S19 fix is in scope** — composite-action globs added to
  `claude/.claude/rules/github-actions-workflows.md`. `[engineer-verified]`
- **Stowed-glob portability is enforced** (row3d) — a stowed rule's `paths:`
  globs must carry no leading literal segment. `[engineer-verified]`
- **Both architect-added items are kept** — the `ai-instruction-and-memory-files`
  scoping note (row4a) and the `review-pipeline-dispatch.md` body bullet
  (row2). `[engineer-verified]`

## Approach

Close the rules-file review gap by adding one dispatch clause to `/code-review` (mirroring the clause `/plan-review` already carries), pointing `review-pipeline-dispatch.md`'s own `paths:` at rule files so the dispatch guidance loads when you edit one, and extending the existing `test_rules_frontmatter.py` with a **literal-prefix / portability** check on each `paths:` glob. That glob check is what makes the S19 fix safe to ship in the same PR: it validates each glob's *non-wildcard leading segments* against the corpus that glob actually targets, which catches a misspelled path root while leaving a fully-portable forward-looking pattern like `**/.github/actions/**/action.yml` untouched. No new skill, no new hook, no new test module.

**Assumption ledger**

**Root problem.** A `.claude/rules/*.md` edit reaching commit is reviewed only by generic `/code-review` — no domain dispatch and no mechanical check — while the same content reviewed at plan time gets `ai-instruction-and-memory-files` via `plan-review/SKILL.md:236`. Rule files auto-load into any session that opens a matching file, and the stowed set applies in every repo a stow consumer opens, so a defective rule ships silently to every consumer.

**Givens** (fixed beyond this plan's reach):

- **G1.** Claude Code decides *when* a path-scoped rule loads; a `paths:` glob that matches nothing simply never fires, with no error surface. Anthropic owns the loading semantics. `[verified: .claude/plans/trim-global-claude-md.md:168 quoting code.claude.com/docs/en/memory — "Path-scoped rules trigger when Claude reads files matching the pattern, not on every tool use."]`
- **G2.** The stowed rules in `claude/.claude/rules/` target *every consumer's repo*, not this one. This repo cannot enumerate that corpus, so "does this glob match a real file" is unanswerable here for a stowed glob — it is a question about other parties' trees. `[verified: repo-root Glob for `**/{Dockerfile,Dockerfile.*,*.sql,*.dockerfile}` returns zero files, yet `claude/.claude/rules/dockerfile-conventions.md` and `sql-ddl-conventions.md` are both correct rules]`
- **G3.** `/code-review` runs because CLAUDE.md §Code Review requires it before any commit, not because a hook fires it. Whatever this plan adds to `code-review/SKILL.md` inherits that invocation model; changing it is a CLAUDE.md-surface decision outside this plan. `[verified: CLAUDE.md §Code Review]`
- **G4.** Global skill bodies under `claude/.claude/skills/` install to every stack and must stay platform-agnostic, so repo-specific stow paths cannot appear in them. `[verified: .claude/rules/skill-and-agent-self-review.md, "Global skill bodies stay platform-agnostic"]`

**Rows**

**row1 — `code-review/SKILL.md:200`: add `.claude/rules/*.md` to the `ai-instruction-and-memory-files` dispatch line.** `anchors: root`. This is the literal asymmetry: `plan-review/SKILL.md:232` and `:236` name rule files, `code-review/SKILL.md` names them nowhere. `[verified: grep for `rules/` and `\.claude/rules` in code-review/SKILL.md returns zero]` `[engineer-verified: Decision 1, item 1]`

**row1a — no Change-type table edit is needed.** `anchors: row1`. Row `:277` already reads "Adds or modifies a skill, agent, instruction-file rule, or hook" and defers to the Domain section for the handler; once `:200` names rule files, the row resolves instead of dead-ending. `[verified: code-review/SKILL.md:277]`

**row1b — write `.claude/rules/*.md` only, not the stow-source path.** `anchors: G4`. `code-review/SKILL.md:198` already writes `.claude/skills/**/SKILL.md` and is understood to cover this repo's `claude/.claude/skills/` stow source; the rules clause follows the same precedent. The two literal claude-config paths belong in the project-scoped rule (row2), where the reader and altitude match. `[verified: code-review/SKILL.md:198]`

**row2 — `.claude/rules/review-pipeline-dispatch.md`: add `.claude/rules/**` and `claude/.claude/rules/**` to `paths:`, plus one body bullet describing the rules-file dispatch.** `anchors: root`. Today this rule's `paths:` is `claude/.claude/skills/**/SKILL.md`, `claude/.claude/agents/*.md`, `plugins/**/*` — editing a rule file does not load the dispatch guidance. `[verified: .claude/rules/review-pipeline-dispatch.md:2-5]` The body bullet is required for coherence, not optional polish: a `paths:` addition alone makes the rule load on rule-file edits while saying nothing about rule files. Its content mirrors the `agent-review` bullet's shape — dispatched by `/code-review`, **not hook-enforced** (see row6). This is Decision 1 item 2 completed on its own terms, not a rescope. After the change the rule is self-referential (it lives in `.claude/rules/`), which is correct — editing it loads it.

**row3 — extend `rule_frontmatter_violations()` in the existing `claude/.claude/skills/tests/test_rules_frontmatter.py` with a per-glob literal-prefix check.** `anchors: root`. Algorithm, per glob string:

1. Split on `/`; the *literal prefix* is the longest leading run of segments containing no glob metacharacter (treat `*`, `?`, `[`, `{` as metacharacters — brace semantics for `paths:` are undocumented in-repo, so exclude conservatively rather than guess). Compute the **full leading run**, not the first segment alone — a typo in a later literal segment (`claude/.claude/skils/**`) must still be caught.
2. **Empty or whitespace-only glob entry** is a violation in both classes. `paths: [""]` currently passes the existing all-strings check and would otherwise be silently exempted as "portable."
3. **Stowed rule** (file under `claude/.claude/rules/`): a non-empty literal prefix is a violation. Its referent corpus is every consumer repo (G2), so a leading literal either is a typo or wrongly assumes this repo's layout.
4. **Project rule** (file under `.claude/rules/`): a non-empty literal prefix must resolve to an existing **directory** under `_REPO_ROOT`. An empty prefix (a fully-portable `**/…` glob) is exempt — its referent is any path in this repo, which stays meaningful.
   - Use `.is_dir()`, not `.exists()`: a prefix naming an existing *file* followed by a wildcard segment (`docs/rules-references.md/**`) is nonsensical and must flag.
   - **Join safely.** `_REPO_ROOT / "/etc/passwd"` evaluates to `/etc/passwd` — pathlib's `/` discards the left operand when the right side is absolute, so a leading-`/` or `..`-walking prefix would be checked against the real filesystem root and pass or fail by accident of the host. Resolve via `_REPO_ROOT.joinpath(*prefix_segments)` and additionally assert the result `.is_relative_to(_REPO_ROOT)`; a prefix that escapes the repo root is a violation.
5. Reuse the module's existing `_discover_rule_files()`, `_REPO_ROOT`, and parametrized-test scaffolding — add no second parametrized test. **There is no accumulator to append to:** `rule_frontmatter_violations()` currently returns a single-item list from each guard branch and ends with a bare `return []`. Replace that terminal `return []` with the glob-check results. The existing early returns do not mask the new check — they are mutually-exclusive structural prerequisites (absent/invalid frontmatter, wrong `paths` type, non-string entries) that cannot co-occur with a well-formed-but-badly-globbed list.
6. **Fixture classification seam.** Row3 classifies by path ancestry, but the existing `_write_rule(tmp_path, content)` helper hardcodes `tmp_path / "rule.md"` — a path under neither rules directory, so none of the fixtures below can be built with it as-is. Grow the helper with a `stowed: bool` parameter that mirrors the real layout under `tmp_path` (`tmp_path/"claude"/".claude"/"rules"/"rule.md"` vs `tmp_path/".claude"/"rules"/"rule.md"`), and classify against those two ancestries rather than against the live repo paths.

**row3a — this formulation catches the docstring's own cited typo and clears every glob in the repo, including S19's.** `anchors: row3`. Corpus counts re-derived by script this session, superseding an earlier draft that stated 23/9/11 and did not reconcile: **21 globs across 8 rule files — 12 project, 9 stowed.** `[verified: python3 walk of both rules dirs parsing `paths:` via PyYAML and computing each literal prefix, run this session]`
- Must-flag fixture `"cluade/.claude/rules/**"`: as a project rule, prefix `cluade/.claude/rules` does not resolve → flagged; as a stowed rule, leading literal `cluade` → flagged. Caught either way.
- S19's `**/.github/actions/**/action.yml`: fully portable → exempt. Not a special case — the *same* clause exempts **all 9** existing stowed globs, every one of which is `**/`-led with an empty literal prefix. `[verified: same script — stowed globs with a non-empty prefix: 0 of 9]`
- **10 of the 12 project globs carry a non-empty literal prefix, and all 10 resolve to existing directories**: `claude/.claude/skills`, `claude/.claude/agents`, `claude/.claude`, `plugins`, `.claude/skills`. The remaining two are the `**/settings.json` and `**/settings.local.json` globs in `settings-json-conventions.md`, which are portable and exempt. `[verified: same script — every non-empty project prefix returned exists=True]`

**row3b — over-powered-primitive check on row3.** `anchors: row3`. Three lighter primitives, evaluated:
- **A prose checklist item in the new review clause, no test.** Lighter, and rejected: distinguishing `cluade/` from `claude/` is proofreading, not judgment, and the existing module docstring already records this exact shape as the one that "passes this check while still silently matching nothing at runtime." Mechanical detection is strictly better than asking a reviewer to re-read a string.
- **A new standalone `test_rules_globs.py`.** Rejected as *heavier*, not lighter: it would duplicate `_discover_rule_files()` and `_REPO_ROOT`, add a second discovery surface that can silently diverge, and gain nothing — the existing module already parametrizes over exactly the right file set.
- **Extend the existing pure function `rule_frontmatter_violations()`.** **Adopted.** No new module, no new hook, no new script, and `select-tests.py` already maps both `ROOT_RULES_DIR` and `RULES_DIR` to `SKILLS_TESTS_DIR`, so no mapping change either. `[verified: select-tests.py:123-127, :301-302]`

Also rejected — **require every glob to match ≥1 real file, with an allowlist for forward-looking patterns.** The allowlist would be the norm rather than the exception on day one: this repo has zero `.sql` files and zero Dockerfiles `[verified: find for `*.sql` and `Dockerfile*` both return nothing]`, so `sql-ddl-conventions.md` and `dockerfile-conventions.md` would each need an entry alongside S19's — 3 of 4 stowed rules exempted by hand. A check whose allowlist covers most of its inputs is bookkeeping, not enforcement. It also needs comment-to-list-item association inside YAML frontmatter, which PyYAML discards.

**row3c — residual gap, stated plainly.** `anchors: row3`. No filesystem-grounded check can catch a typo in a glob's *wildcard-interior* segments — `**/.github/wrokflows/*.yml` passes row3, because the only way to reject it is to demand that `.github/workflows` exist, which would also reject `**/.github/actions/**/action.yml`. The two requirements are strictly incompatible, so the "no formulation works" case is real but narrow: it applies only to interior segments of portable globs, not to the documented `cluade/` shape. A spelling/edit-distance heuristic would need a vocabulary of "known directories," which for stowed rules is unbounded by G2 — rejected as producing false positives on legitimately-novel segments. `[unverified — asserted from the incompatibility argument above, not from a tried implementation]`

**row3d — row3 establishes a new enforced convention: stowed globs must be portable.** `anchors: row3`. Currently true of all 11 stowed globs with zero exceptions, so it costs nothing today, and it enforces at commit time what G2 already implies. **Resolved this session: enforce it** — the engineer chose enforcement over the project-rule-only variant, accepting that it constrains every future stowed rule. `[engineer-verified]`

**row4 — `ai-instruction-and-memory-files/SKILL.md`: name rule files in the description, and add a short body section scoping what applies to them.** `anchors: row1`. `[engineer-verified: Decision 1, item 4]` The description currently names CLAUDE.md, AGENTS.md, and auto-memory only, while `plan-review:236` already dispatches it for rule-file content and its own Step 1 item 2 (`:26`) already names `.claude/rules/*.md` as a *placement destination*. `[verified: ai-instruction-and-memory-files/SKILL.md:3-8, :26]` The description edit inserts `.claude/rules/*.md` into the summary line and the TRIGGER clause. This **does** grow the file: the description block is hard-wrapped at 62–78 characters and line 4 is already 67, so both insertions rewrap into roughly 1–2 additional lines. `[verified: awk line-length measurement of the frontmatter block this session]` Combined with row4a the body lands near 204–205 against the 215 cap — comfortable, but the earlier "no line growth" claim was wrong and is corrected here.

The description also carries a second budget, separate from the line cap: `TestTotalListingBudgetUnderSonnet::test_total_within_listing_budget` in `test_skills.py` sums `description` characters across all model-invokable skills against `SKILL_LISTING_BUDGET_CHARS`. `user-invocable: false` excludes this skill from slash invocation, not from that budget, so row4 consumes headroom there and the Verification section checks it. Row1's edit is body prose, not frontmatter, and does not.

**row4a — the body scoping note is required for correctness, not polish.** `anchors: row4`. Declaring ownership of rule files without bounding it invites the skill to apply CLAUDE.md-specific machinery to a rule file — most concretely `check-claude-md-length.sh`'s 200-line cap, which has no rules-file equivalent (no `check-rules-length.sh` exists). A ~6-line section stating what carries over (behavior test §2, compression-diff audit, duplicate-vs-reference §3, anti-duplication §5, Step 1.2 placement) and what does not (§1 CLAUDE.md/AGENTS.md loading and `@AGENTS.md` import, ancestor precedence, the 200-line cap, §4 AGENTS.md-adoption rows, §5 auto-memory mechanics) fits the 215-line hook cap with room left — 197 + ~6 = ~203. `[verified: check-skill-length.sh:22-24 for the 215 cap; wc -l for the 197 current]` **Resolved this session: keep it.** `[engineer-verified]` It is not in fact optional: dropping it would leave the description claiming rule-file audit coverage with no body content scoping that claim, which is a `skill-review` item-2 overpromising-description defect. An earlier draft called it droppable; that was wrong.

**Drafted text is required, not a description of text.** Compose the section so it names no repo-specific hook script: `ai-instruction-and-memory-files/SKILL.md` is a globally-stowed body under `claude/.claude/skills/`, and `skill-review` item 12 (platform-genericness) treats a repo-specific token anchoring a rule as a hit. State "the 200-line CLAUDE.md cap does not apply to rule files" rather than naming `check-claude-md-length.sh` or a hypothetical `check-rules-length.sh`. The file already carries one such mention at `:78` on a line this diff does not touch — per item 12 that is a note for the PR reviewer, not a licence to add a second.

**row5 — `claude/.claude/rules/github-actions-workflows.md`: add composite-action globs to `paths:`, and one applicability line to the body.** `anchors: root`. `[engineer-verified: Decision 2 — S19 fix is in scope]`

**row5a — S19's recorded rationale is stale at HEAD; the glob addition is re-grounded, not inherited.** `anchors: row5`. The rule body no longer names composite or custom actions anywhere. `[verified: grep for "composite|custom action" in claude/.claude/rules/github-actions-workflows.md returns zero at HEAD; `git log -S composite` shows the phrase removed by `d6d3930` (#733, 2026-08-24), which postdates the 2026-08-22 audit that recorded S19]` The surviving bullet at `:30-33` reads "into any field the receiving action executes as code, not just `run:`". That wording still governs an `action.yml` author *if* a composite action's `runs.steps[].run` expands `${{ }}` — an inference from composite-action execution semantics, not a quote. `[unverified — not checked against GitHub's metadata-syntax reference]` The glob addition is judged still warranted, resting on two bullets that survive at HEAD rather than on the removed sentence:

1. The SHA-pin bullet at `:14-18` closes with "(Doesn't cover transitive actions the pinned action itself calls.)" — a composite action's nested `uses:` **is** that transitive surface, so the rule already names the gap the glob would close. `[verified: read of :14-18 this session]`
2. The surviving interpolation bullet at `:30-33` governs any field the receiving action executes as code, which includes a composite action's `runs.steps[].run`. `[unverified: composite-step `${{ }}` expansion semantics not checked against primary GitHub docs this session]`

Decision 2 stands; the weakened rationale is recorded so it can be reconsidered from accurate facts.

**row5b — the glob needs an applicability qualifier that names the inapplicable keys explicitly.** `anchors: row5`. Several bullets are workflow-file syntax with no `action.yml` counterpart, so matching `action.yml` auto-loads guidance that partly cannot apply — for every stow consumer, in every repo, with no error surface (G1). A vague "some bullets may not apply" **reproduces** the risk instead of closing it: the concrete failure is an author adding `permissions: {}` to an `action.yml` believing it scopes the composite action's token, when the key is not in the action metadata schema and is silently ignored — actual scoping happens only in the calling job. That is false confidence in a security control, and the downstream blast radius is a stow consumer's real CI pipeline, which *is* externally reachable via PR-triggered workflows even though claude-config itself is not.

The qualifier must therefore **enumerate**, not gesture. Draft content, to be confirmed against GitHub's Actions metadata-syntax reference before landing:

- **Does not transfer — no such key in the `action.yml` schema:** `permissions:`, `concurrency:`, `runs-on:` runner-image pinning. Composite-step `timeout-minutes` is in the same class but is the least certain of the four — confirm it specifically. `[unverified: schema-absence asserted from the metadata-syntax reference's documented top-level key set (`name`/`description`/`inputs`/`outputs`/`runs`/`branding`); not fetched this session]`
- **Transfers directly:** SHA-pinning nested third-party `uses:` inside composite steps; `persist-credentials: false` on `actions/checkout` (a `with:` input, usable identically in a composite step); untrusted-input interpolation into executed fields — but the sink inside a composite action is `inputs.*` and `env.*`, not `github.event.*` directly, and the qualifier must name that indirection or the guidance misses its actual shape.
- **Context-dependent, not authorable in `action.yml`:** `pull_request_target`/`workflow_run` privilege (composite steps inherit the caller's trigger context, set in the workflow) and OIDC subject pinning (a cloud-provider-side trust policy, unless the composite action itself wraps the auth step).

Land the confirming citation in `docs/rules-references.md`'s existing `github-actions-workflows.md` section (the rule body's `:11-13` already points there). Do not write the qualifier from memory.

**row6 — no hook gate for the rules-file review clause.** `anchors: row1`. `docs/skills.md:132`: *"Gate a review skill only when its target files carry always-loaded context budget or route dispatcher decisions (e.g. `/skill-review` via `require-skill-review.sh`); lazy-loaded targets like `/agent-review` rely on dispatcher-level invocation instead."* `[verified: docs/skills.md:132]` Rule files are lazy-loaded on matching file read (G1), so they carry no always-loaded budget — they fall squarely in the `/agent-review` class, which has no gate and no `require-agent-review.sh`. The clause's enforcement is G3: `/code-review` is already mandatory before commit. Adding a gate would additionally require a new `marker.sh write <verb>` target enumerated in `enforce-marker-script-shape.sh:607,638` and `_lib.sh:1026` — real cost the rule does not justify. **Do not add a gate.**

**row6a — named residual: `review-pipeline-dispatch.md` sits inside the gate criterion's second disjunct, and this plan accepts that gap rather than closing it.** `anchors: row6`. `docs/skills.md:132` gates on always-loaded budget **or** routing dispatcher decisions. That file's entire content *is* dispatcher routing, so it is not the edge case an earlier draft framed it as — it is squarely inside the second disjunct. The earlier rebuttal ("the dispatcher decision is owned by `code-review/SKILL.md`, which is already gated") does not hold: `require-skill-review.sh` gates `code-review/SKILL.md`'s own behavioral equivalence, not the correctness of content living in `.claude/rules/`, which that hook never reads. A regression introduced into `review-pipeline-dispatch.md` — a broken glob, a dropped bullet — therefore has no hook backstop and falls back to G3 alone.

This is accepted, not resolved. Severity is bounded by the declared surface: worst case is a stale or wrong dispatch rule shipping silently to stow consumers, which is the same residual as today's pre-PR baseline rather than a new exposure, and no privilege boundary is crossed. Gating this one file is the available alternative and is deliberately declined here as disproportionate to a single convention file. Recorded so a future reader does not mistake row6 for a demonstration that no gap exists.

**row7 — single `code-writer` dispatch, no split.** `anchors: root`. See *Critical files*.

## Critical files

**Modify**

1. `claude/.claude/skills/code-review/SKILL.md` — line 200 only: add `.claude/rules/*.md` to the `ai-instruction-and-memory-files` dispatch list, mirroring `plan-review/SKILL.md:232`. No Change-type-table edit (row1a). Currently 470 lines against a 500 cap; this adds none.

2. `.claude/rules/review-pipeline-dispatch.md` — add `.claude/rules/**` and `claude/.claude/rules/**` to `paths:` (both literal prefixes resolve, so row3 passes); add one body bullet in the existing three-bullet list, shaped like the `agent-review` bullet, naming the dispatch as **not hook-enforced**.

3. `claude/.claude/skills/tests/test_rules_frontmatter.py` — extend `rule_frontmatter_violations()` per row3; **update the module docstring**, whose current sentence ("It does NOT verify that any individual glob pattern is well-formed or matches a real target path; a syntactically-valid but wrong/typo'd glob (e.g. `"cluade/.claude/rules/**"`) passes this check") becomes false with this change; add fixtures to the existing `TestRuleFrontmatterViolations` class.
   **Reuse:** `_discover_rule_files()`, `_REPO_ROOT`, `_PROJECT_RULES_DIR`, `_STOWED_RULES_DIR`, the `_write_rule` fixture helper, and the existing parametrized test — add no new module, no new discovery, no second parametrize.

4. `claude/.claude/skills/ai-instruction-and-memory-files/SKILL.md` — description summary line and TRIGGER clause gain `.claude/rules/*.md`; add the ~6-line rule-file scoping section per row4a. Body 197 → ~203 against the 215 cap.

5. `claude/.claude/rules/github-actions-workflows.md` — add the composite-action globs to `paths:` (portable, `**/`-led, so row3-exempt by the same clause that exempts the existing 11); add the one-line applicability qualifier per row5b.

6. `docs/rules-references.md` — extend the existing `github-actions-workflows.md` section (do not create a new file) with the metadata-syntax citation that row5b's qualifier rests on. **Not conditional:** row5b requires confirming the schema-absence claims before landing, and this repo's grounding rule requires the confirming source be recorded alongside the claim, so the citation is a deliverable of row5b rather than a contingency. If the check finds the draft classification wrong, the qualifier changes and the citation records the corrected version — either way this file is edited.

**Dispatch split: one `code-writer` dispatch, not split.** The five files share a single "why" — the rules-file dispatch gap — and splitting would force restating it in every prompt, which `plan-it`'s rule names as the disqualifier. There is also a hard sequencing dependency: file 5's new glob is only legal because of file 3's portability exemption, so a parallel split would race. Within the single dispatch, implement in the order **3 → 5 → 2 → 1 → 4** so the test's exemption exists before the glob that relies on it. Total surface is ~5 files and well under 100 changed lines; there is no isolation benefit to buy.

## Verification

Run from the worktree root:

1. `.venv/bin/python3 claude/.claude/scripts/select-tests.py` — scoped to the diff, per this repo's contributor instructions. Expect it to widen beyond `SKILLS_TESTS_DIR` into `HOOKS_TESTS_DIR`, because `select-tests.py` maps `GITHUB_ACTIONS_WORKFLOWS_RULE_MD` there via `test_ci_path_filter.py`. **Resolved before implementation:** that test references the rule only as a sample path in `_PREVIOUSLY_MISSING_PATHS` (`test_ci_path_filter.py:43`) and asserts nothing about its `paths:` frontmatter, so file 5 needs no corresponding edit there. `[verified: grep for `github-actions-workflows|paths:` in test_ci_path_filter.py returns only line 43]`
2. `.venv/bin/ruff check claude/.claude/` — lint. No shell files change, so ShellCheck is not needed. Note the `.venv` is worktree-relative and may not exist in a fresh worktree; run `./install-dev.sh` from the repo root first if it is absent.
2a. Confirm `TestTotalListingBudgetUnderSonnet::test_total_within_listing_budget` in `claude/.claude/skills/tests/test_skills.py` still passes after row4's description edit — it is the corpus-wide character budget, distinct from the 215-line hook cap, and row4 is the only row that consumes it.
3. **Fixture set for `TestRuleFrontmatterViolations`.** All of these are required; the first three alone are insufficient (see the surviving mutation below).
   - `"cluade/.claude/rules/**"`, project rule → **flagged** (the must-flag case the current docstring documents as uncaught).
   - `"**/.github/actions/**/action.yml"`, stowed rule → **not flagged** (forward-looking portable glob must survive).
   - `"claude/.claude/skills/**"`, stowed rule → **flagged** as non-portable (the row3d convention).
   - `"claude/.claude/skils/**"`, project rule → **flagged**. Typo in the *third* segment. Without this, a naive first-segment-only implementation of "literal prefix" passes all three fixtures above byte-identically while never checking later segments — a named surviving mutation, not a hypothetical.
   - `"/etc/passwd/**"`, project rule → **flagged**. Guards the absolute-path join defect; without it the check silently consults the host filesystem root.
   - `""` (empty string entry), either class → **flagged**.
   - `"docs/rules-references.md/**"`, project rule → **flagged**. Prefix resolves to a file, not a directory.
   - `"**/*.{yml,yaml}"`, stowed rule → **not flagged**, pinning row3's deliberate choice to treat `{` as a metacharacter. The choice is recorded in row3 but currently unverified by any assertion; this fixture makes the decision observable so a later change to brace handling fails loudly.
4. **Hook-enforced `/skill-review`** fires at commit for files 1 and 4 (`require-skill-review.sh` matches `claude/.claude/skills/**/SKILL.md`). Editing files 2, 3, and 5 does not trigger it — that asymmetry is the gap this PR closes at the `/code-review` layer, not at the hook layer (row6).
5. **Self-check the change against itself:** after file 2 lands, editing any rule file loads `review-pipeline-dispatch.md`. Confirm the added bullet actually reads as dispatch guidance for rule files, not as a restatement of the SKILL.md bullet.

## Out of scope

- **A dedicated `rules-review` skill.** Weighed and rejected (Decision 1). It would need `docs/skills.md` notes, README.md pipeline bullets and hook-table rows, a `code-review/SKILL.md` dispatch entry, and — if gated — a new `marker.sh write` verb in `enforce-marker-script-shape.sh` and `_lib.sh`. `ai-instruction-and-memory-files` already owns the transferable checks.
- **Any hook gate for rule-file review** — `docs/skills.md:132` does not justify one (row6). Not a given: this plan *could* add one and deliberately does not.
- **A `check-rules-length.sh` line cap for rule files.** None exists today. Whether rule files should carry one is a separate question; row4a's scoping note only records that the CLAUDE.md 200-line cap does not transfer, it does not propose a replacement.
- **Catching typos in wildcard-interior glob segments** (`**/.github/wrokflows/*.yml`). Provably incompatible with permitting forward-looking portable globs (row3c). Named here so a later revision does not re-litigate it as an oversight.
- **The `enabledPlugins` scope discrepancy at `docs/design-decisions.md:803-808`**, where the upstream settings reference and `.claude/rules/settings-json-conventions.md` contradict each other and "nothing in this repo tests the question." It is good evidence *for* this PR's premise — rule-file content claims go unverified — but resolving it is content work on a different rule, in a different domain, needing an upstream-docs check this plan does not perform.
- **Backfilling `docs/rules-references.md` sections for the six rule files that lack them** (audit finding C24). File 6 extends only the existing `github-actions-workflows.md` section, and only if row5b produces a citation.
- **Structural enforcement of rule-file *content* accuracy.** Row3's test checks glob shape and portability only. Nothing in this plan mechanically checks that a rule's guidance is still true — citation freshness, or whether a bullet names a key that exists in the schema it targets. That remains a dispatch-only control: `/code-review` invoking `ai-instruction-and-memory-files`, which is advisory rather than deterministic. Stated explicitly so row3's glob test is not later mistaken for broader guidance-quality assurance. The `enabledPlugins` contradiction below and the `permissions:`-in-`action.yml` misfire in row5b are both instances of this uncovered class.
