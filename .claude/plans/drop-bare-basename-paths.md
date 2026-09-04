# Drop the five bare-basename `paths:` entries from `claude-md-conventions.md`

Issue: https://github.com/jcdendrite/claude-config/issues/844
Branch: `GH-844/drop-bare-basename-paths`, stacked on `origin/rules-file-review-coverage` (PR #842) — **not** on `main`.

## Context

Retire five `paths:` entries that exist only as insurance against a glob-dialect question that has now been answered. `claude/.claude/rules/claude-md-conventions.md` ships ten `paths:` entries — five `**/`-led globs each paired with a bare-basename duplicate. PR #839 shipped the duplicates defensively because Anthropic's docs never state whether a leading `**/` matches zero path segments, so `**/CLAUDE.md` might silently never have fired for a repo-root `CLAUDE.md` — the single most important case. That check has now been run and a `**/`-led glob **does** match a project-root file. The intended outcome: drop the five bare entries, update the test that pins them, replace the docs subsection recording the question as open with the empirical result, and retire the `**/`-led portability carve-out that PR #842 added specifically to accommodate those five entries.

## Approach

Drop the five bare-basename `paths:` entries from `claude-md-conventions.md`, and retire — in the validator, its unit tests, and the rule prose — the two stowed-glob portability exemptions that existed only to permit them. The stowed-rule invariant becomes uniform: every `paths:` entry is `**/`-led, with no leading literal path segment. The test module gains a small predicate that expands a `**/`-led pattern to also match its depth-zero form before `fnmatch`, so the nine representative candidates stay covered without the duplicates propping them up.

The empirical result is recorded once, in `docs/rules-references.md`'s `` ## `paths:` glob-dialect conventions `` section — the source section for `rule-authoring-conventions.md`, which is the rule that states dialect facts. The issue prescribes replacing the `### Glob decision for the rule's `paths` frontmatter` subsection under `## CLAUDE.md and AGENTS.md conventions` with the result; that subsection is instead **deleted outright**, because a `paths:`-dialect fact filed under one rule's own section is invisible to the next author who looks up `**/` semantics. Once the duplicates and the exemption are gone, nothing rule-specific about `claude-md-conventions.md`'s glob list remains to record there — its five entries are shaped identically to every other stowed rule's.

Alternatives set aside: dropping the three repo-root candidates from the test so plain `fnmatch` still passes (stops covering the exact case the investigation was about) and `PurePosixPath.full_match` (3.13+, CI pins 3.12) — both rejected by the engineer at Step 4 and re-examined below under M2. Keeping the exemptions as dormant capability was weighed and rejected under M3.

### Assumption ledger

**Root problem.** Five `paths:` entries in a stowed rule exist solely as insurance against a glob-dialect question that has now been answered, and a portability carve-out on the sibling branch exists solely to let those five entries pass — so the redundancy is now self-propagating rather than merely inert.

**Givens** (conditions this design treats as fixed, each beyond its own reach):

- **Claude Code's `paths:` matcher is closed and under-specified.** Anthropic owns the implementation and publishes four dialect behaviors on the memory docs page; every remaining question is settleable only by measurement against a running harness, never by reading a spec.
- **The base is `origin/rules-file-review-coverage` (PR #842), not `main`.** The stacked base's contents move only through that PR's own review cycle, which this plan does not control.
- **CI pins Python 3.12** (`.github/workflows/tests.yml:138`). Raising it is a repo-wide toolchain decision outside this change, so `PurePosixPath.full_match` is unavailable here.

**Assumption rows:**

1. A `**/`-led `paths:` glob matches a project-root file (zero leading directory segments). `[engineer-verified]` — the instrumented `InstructionsLoaded` run recorded in issue 844, decisive trial reading repo-root `install.sh` against `shell-script-conventions.md`'s `["**/*.sh", "**/*.bash"]`, replicated on `install-dev.sh`, with two gating positive controls.
2. That result transfers from `-p` (non-interactive) mode to ordinary interactive sessions. `[unverified]` — the engineer disclosed that the two modes were not shown to load instructions identically. This is the plan's single load-bearing risk; everything downstream inherits the flag. Closed by measurement in Verification step 5, which is a merge gate rather than a fallback layer — the ten-entry state hedged this axis as well as the zero-segment one, and this change spends that hedge.
3. `claude-md-conventions.md` is the only stowed rule using either exemption. `[verified: grep of every `paths:` entry under claude/.claude/rules/, this session]` — the other five stowed rules (`sql-ddl-conventions`, `github-actions-workflows`, `rule-authoring-conventions`, `dockerfile-conventions`, `shell-script-conventions`) are `**/`-led throughout, so the exemptions cover zero live entries once the five bare ones are dropped.
4. `fnmatch` alone cannot express zero-segment `**/`: its `*` is "any characters including `/`" with no `**` segment concept, and `fnmatch.translate('**/CLAUDE.md')` requires a literal `/`. `[verified: engineer's repo-venv run, Step 3]` — this is why dropping the bare entries breaks `'CLAUDE.md'`, `'AGENTS.md'`, and `'CLAUDE.local.md'` unless the matcher changes.
5. Removing the exemptions leaves every current stowed rule passing `test_rule_has_parseable_paths_frontmatter`. `[verified: claude/.claude/skills/tests/test_rules_frontmatter.py:89-102 read against row 3's grep]` — `_literal_prefix_segments` halts on the leading `**`, returning `()`, so the `if prefix and ...` guard never fires for a `**/`-led entry.
6. One PR, stacked on #842, carries all deliverables including the carve-out retirement; it merges after #842. `[engineer-verified]` — Step 4 answer 1.
7. The test matcher encodes zero-segment semantics via a `**/`-expansion before `fnmatch`, and all nine representative candidates are kept. `[engineer-verified]` — Step 4 answer 2.
8. `claude/.claude/rules/github-actions-workflows.md:17-23` carries the same `[unverified]` zero-segment caveat, about whether `**/action.yml` matches a root-level `action.yml`. `[verified: claude/.claude/rules/github-actions-workflows.md:17-23]` — a structural sibling the issue's Change list does not name.
9. Four sites outside the rule files restate the pairing, the entry count, or the exemption: `docs/rules-references.md`, the test module, `github-actions-workflows.md` (row 8), and `claude/.claude/skills/ai-instruction-and-memory-files/SKILL.md:174`, whose §4 "Stowed-rule portability" bullet states the exemption in prose. `[verified: `git grep claude-md-conventions`, a grep for bare-basename / zero-segment / defensive phrasing, and a direct grep for "Stowed-rule portability", this session]` — the SKILL.md site was missed by the first two greps because it names none of those terms, and was found by the plan-review dispatch that read the skill body. All four are in **Critical files**.
10. `test_skills.py`'s `TestMemorySkillSectionOrdinalCrossReferences` and `test_ci_path_filter.py`'s `_PREVIOUSLY_MISSING_PATHS` are both content-independent with respect to this change. `[verified: claude/.claude/skills/tests/test_skills.py:510-524, claude/.claude/hooks/tests/test_ci_path_filter.py:32-49]` — the first pins skill-section ordinals and names `claude-md-conventions.md` only as a relocation target; the second lists `docs/rules-references.md` and `claude/.claude/rules/github-actions-workflows.md` as literal path strings in a CI-filter allowlist, never reading their content. Neither needs an edit.
11. `**/.claude/CLAUDE.md` and `**/.claude/AGENTS.md` are themselves redundant with `**/CLAUDE.md` and `**/AGENTS.md` under the real matcher. `[unverified]` — row 1's evidence covers zero intermediate segments and three (`claude/.claude/hooks/_lib.sh`, which also proves `**/` traverses a dot-directory), but not the one-segment case these two entries occupy. Recorded in **Out of scope**, deliberately not acted on.

**Mechanisms:**

- **M1 — Delete the five bare entries from `claude-md-conventions.md`.** `anchors: row1, row2`. Row 2 is closed by measuring it (Verification step 5, a merge gate), not by hedging it in code: adding a fallback to insure against an unverified row is the compounding-defensive-layers tell that produced this issue in the first place, and the correct response to an unmeasured axis is one more trial on an instrument that already exists. Until that trial passes, the `-p` qualifier stays on the claim in both `rule-authoring-conventions.md` and `docs/rules-references.md`, so no site overstates what was checked.
- **M2 — Add a zero-segment-aware match predicate to the test module, and replace the count pin with an exact-list pin.** `anchors: row1, row4, row7`. Heavier than plain `fnmatch`, so the lighter primitives were re-derived: (a) drop the three repo-root candidates and keep bare `fnmatch` — lightest, no new code, rejected because it stops covering the depth-zero case the whole investigation was about (row 7); (b) `PurePosixPath.full_match`, which handles `**` natively — rejected as 3.13+ against a 3.12 CI pin (Given 3, row 7); (c) `PurePath.match` on 3.12 — rejected because its documented 3.12 behavior is that `**` acts as non-recursive `*`, giving no zero-segment semantics and no full-path anchoring, so it solves neither half. Three lighter options named, all failing; the ~5-line predicate stands.
- **M3 — Retire both exemptions from the validator body, its docstring, the module docstring, the violation message, four unit tests, `rule-authoring-conventions.md`'s prose bullet, and `ai-instruction-and-memory-files/SKILL.md`'s §4 restatement of it.** `anchors: row3, row5, row9`. Seven sites, not six — the SKILL.md one states the exemption as a review criterion, so leaving it would have an active review skill enforcing a rule the validator no longer implements. The exemptions do retain one legitimate shape after this change — a stowed rule wanting to match *only* the repo-root instance of a file and not nested ones, which `**/`-led cannot express. Retired anyway: nothing in the repo needs it, and re-adding the six lines later alongside the concrete rule that needs them gives the unit tests a real fixture instead of a hypothetical one. Keeping them keeps a door open onto exactly the redundancy this PR removes.
- **M4 — Delete `docs/rules-references.md`'s `### Glob decision` subsection; record the measurement in the `` ## `paths:` glob-dialect conventions `` section instead.** `anchors: root`. Both of that subsection's paragraphs die with the same change — the first justified the pairing (M1), the second justified the exemption (M3). Filing the replacement under the dialect section rather than under `## CLAUDE.md and AGENTS.md conventions` follows the file's own stated structure (one section per rule file) and the repo convention that rule bodies state the rule while `docs/rules-references.md` holds provenance.
- **M5 — Strike the now-settled `[unverified]` clause from `github-actions-workflows.md`.** `anchors: row8`. Same bug shape, same evidence, one arm over.
- **M6 — One `## [Unreleased]` → `### Changed` CHANGELOG entry.** `anchors: root`. `rule-authoring-conventions.md` is stowed, so its guidance change reaches every consumer on `git pull` — the same inclusion test the "Locate before a whole-file read" entry states explicitly at `CHANGELOG.md:17`.

### Prescribed content

**`claude/.claude/rules/rule-authoring-conventions.md`** — replace the `[unverified]` bullet (lines 24-27 at the stacked base) with two bullets, and rewrite the stowed-rule bullet (lines 28-36):

```markdown
- **A `**/`-led pattern also matches a root-level file.** `**/CLAUDE.md`
  fires on a repo-root `CLAUDE.md`, so pairing it with a bare `CLAUDE.md`
  entry adds nothing. Established by measurement in `-p` mode, not shown to
  transfer to interactive sessions and not stated by the source above
  (method and limits: `docs/rules-references.md`).
- **`?` support, leading-`/` anchoring, and trailing-`/` semantics are all
  `[unverified]`** — not stated at the primary source above; don't fill
  them in by inference.
- **A stowed rule's referent is every consumer's repo, not this one** —
  every `paths:` glob must be `**/`-led, with no leading literal path
  segment. A leading literal directory assumes some other repo's layout.
  A bare filename (`CLAUDE.md`) or a `.claude/`-anchored literal
  (`.claude/CLAUDE.md`) matches a strict subset of its `**/`-led form,
  which already covers the root-level file.
```

**`docs/rules-references.md`** — replace the closing bullet of `` ## `paths:` glob-dialect conventions `` (lines 246-248 at the stacked base) with:

```markdown
- **Zero-segment `**/` match — established by measurement, not by this
  source.** Five one-shot sessions instrumented with an
  `InstructionsLoaded` hook filtering on `load_reason: path_glob_match`.
  The decisive trial read repo-root `install.sh` against
  `shell-script-conventions.md`, whose `paths` list is
  `["**/*.sh", "**/*.bash"]` with no bare-basename entry, so nothing but a
  `**/`-led glob could have matched; the hook reported a `path_glob_match`
  load. Replicated on `install-dev.sh`. Two gating positive controls ran
  first: a depth-3 read (`claude/.claude/hooks/_lib.sh`, proving the
  instrument fires at depth > 0 and that `**/` traverses a dot-directory)
  and a repo-root `CLAUDE.md` read (proving depth-zero loading fires at all
  in this run mode) — without the second, a null result would have been
  uninterpretable. **Limit:** every trial ran in `-p` (non-interactive)
  mode, and the two run modes were not shown to load instructions
  identically. If a rule is ever observed not loading on a repo-root file
  read in an interactive session, re-run this check in that mode before
  looking elsewhere.
- **`?` support, leading-`/` anchoring, and trailing-`/` semantics are not
  stated at this source** — recorded as `[unverified]` in the rule body
  (`rule-authoring-conventions.md`) rather than restated or inferred here.
- **One intermediate segment is still unmeasured.** The trials above cover
  zero and three intermediate segments, so whether `**/CLAUDE.md` subsumes
  `**/.claude/CLAUDE.md` is open. `claude-md-conventions.md` keeps both
  forms for that reason, not by oversight. A depth-1 trial on the same
  instrument would settle it.
```

**`claude/.claude/rules/github-actions-workflows.md`** — in the paragraph at lines 17-23, delete the sentence beginning "Whether the `**/action.yml` / `**/action.yaml` frontmatter globs above actually match a root-level `action.yml` is `[unverified]`…". Keep the two surrounding sentences and re-join them so the paragraph reads: "**This rule also matches composite `action.yml` files.** Not every bullet below applies to one. Verified against GitHub's Actions documentation, fetched 2026-09-03 (see `docs/rules-references.md` for per-claim citations):".

**`claude/.claude/skills/tests/test_rules_frontmatter.py`** — the predicate, placed immediately after `_literal_prefix_segments` so the two module-private glob helpers sit together:

```python
def _matches_paths_glob(candidate_path: str, pattern: str) -> bool:
    """True if `pattern` matches `candidate_path` under Claude Code's `paths:` dialect.

    Argument order mirrors `fnmatch.fnmatch(name, pat)`. A `**/`-led pattern
    also matches a root-level file, which `fnmatch` cannot express in a
    single pattern because it has no `**` path-segment concept — so the
    leading `**/` is stripped and both forms are tried. Provenance for the
    zero-segment behavior, and its one stated limit, live in
    `docs/rules-references.md`.
    """
    forms = [pattern]
    if pattern.startswith("**/"):
        forms.append(pattern.removeprefix("**/"))
    return any(fnmatch.fnmatch(candidate_path, form) for form in forms)
```

The predicate wraps both steps rather than exposing a bare expander, so no call site can perform the `fnmatch` and forget the expansion.

**Count pin** — `assert len(paths) == 10` becomes neither `== 5` nor nothing. Add a module-level `_CLAUDE_MD_EXPECTED_PATHS` holding the five surviving patterns in file order, and a separate non-parametrized `test_claude_md_conventions_paths_list_is_unchanged` asserting `frontmatter["paths"] == _CLAUDE_MD_EXPECTED_PATHS`, with a failure message directing the reader to revisit `_CLAUDE_MD_CANDIDATE_PATHS` alongside any change. Remove the count assertion from the parametrized test entirely, leaving it only the match assertion.

Three reasons this beats `== 5`: an exact-list pin catches a *substitution* (a typo'd pattern that preserves the count), which no count pin can; the failure output names the expectation instead of printing `assert 4 == 5`; and it stops re-asserting a list-shape fact on all nine parametrized runs, splitting "the list is what we expect" from "the list covers every representative location" into two tests that fail for different reasons. List equality, not set equality — a duplicated entry is a defect worth failing on.

The count pin does still earn a successor, because `**/.claude/CLAUDE.md` and `**/.claude/AGENTS.md` can never be a sole matcher for any candidate under `fnmatch` (whose `*` crosses `/`), so silently deleting either would pass every per-candidate assertion. That is exactly what row 11 flags and what **Out of scope** records.

**Violation-message rewrite** — the current message enumerates the two exemptions and ends with a rationale that no longer holds. Replace with a statement of the actual invariant, one fact per sentence:

```python
f"{rule_file} `paths` entry {glob!r} carries a leading literal "
"path segment — a stowed rule's globs apply in every stow "
"consumer's repo, so every entry must be `**/`-led. A leading "
"literal directory assumes some other repo's layout. A bare "
"filename or a `.claude/`-anchored literal matches a strict "
"subset of its `**/`-led form, which covers the root-level file "
"as well as nested ones. Matching a root-level file and no "
"nested one has no representable form here."
```

The closing sentence matters: `**/`-led is a proper superset of the bare form, not an equivalent, so an author who genuinely wants root-only matching must be told the shape is unavailable rather than that it is "already covered." Retiring the exemption is what makes root-only unrepresentable (M3), and **Out of scope** names the condition for reintroducing it.

This drops the substring `"must be fully portable"` that three existing tests assert on (lines 399, 429, 441). Update all of them — plus the two inverted tests below — to assert on ``"must be `**/`-led"`` instead, which pins the invariant rather than a vaguer adjective.

**Four exemption unit tests** — invert two, reword two, delete none:

- `test_fully_literal_glob_in_stowed_rule_passes` → `test_bare_filename_glob_in_stowed_rule_fails`. Same `"CLAUDE.md"` fixture, now asserting the violation. Comment states that a bare filename matches a strict subset of `**/CLAUDE.md`.
- `test_dotclaude_anchored_literal_glob_in_stowed_rule_passes` → `test_dotclaude_anchored_literal_glob_in_stowed_rule_fails`. Same `".claude/CLAUDE.md"` fixture, now asserting the violation.
- `test_dotclaude_anchored_deep_literal_glob_in_stowed_rule_fails` and `test_multisegment_literal_glob_in_stowed_rule_fails` keep their fixtures and assertions unchanged; only their comments change. Both were built to pin the *boundary* of an exemption that no longer exists, so with `test_nonportable_glob_in_stowed_rule_fails` they would become three fixtures asserting one invariant. Each comment must therefore name what it still guards: these two are the shapes a future re-widening would most plausibly reach for — a `.claude/`-anchored path one segment too deep, and a multi-segment literal with no wildcard at all — and they pin that neither is exempt. A comment that only restates the general rule is the erosion this instruction exists to prevent; if the comment cannot name a distinct shape, merge the fixture into `test_nonportable_glob_in_stowed_rule_fails` instead of keeping a decorative duplicate.

Inverting rather than deleting is the point: the two shapes the exemption used to permit are the two most likely to be re-introduced, and inversion is the only thing that pins their new rejection.

**Also update in the same file:** the module docstring's third bullet (lines 9-11) — "A stowed rule's literal prefix is empty, a bare filename, or a two-segment `.claude/`-anchored path" becomes "A stowed rule carries no leading literal path segment — every entry is `**/`-led." The `_CLAUDE_MD_CANDIDATE_PATHS` comment block (lines 58-68) loses both the "ten `paths` patterns" count and the now-obsolete co-matching caveat, since no bare `.claude/`-prefixed pattern survives to co-match.

In `rule_frontmatter_violations`'s docstring, delete by sentence, not by line range — the exemption sentence ends mid-line and the next sentence begins on the same physical line, so a line-range delete clips a fragment. Keep "`is_stowed` selects which glob-portability rule applies. A stowed rule (`claude/.claude/rules/`) must carry no leading literal path segment, since its referent is every consumer's repo, not this one." Delete the following sentence in full, from "Two shapes are exempt:" through "so the exemption doesn't extend past depth 2)." Re-join so "A project rule (`.claude/rules/`) may have a literal prefix." follows directly, and re-wrap the paragraph.

**`CHANGELOG.md`** — one entry under the existing `## [Unreleased]` → `### Changed`. It must carry four facts: the zero-segment `**/` behavior is settled by measurement, stating which run modes were checked; `claude-md-conventions.md` drops five bare-basename entries with no change to which files trigger it; the stowed-glob rule is now uniformly `**/`-led, so a stowed rule whose `paths:` entry is a bare filename or a `.claude/`-anchored literal no longer validates; and it is live on `git pull` with no re-install. State that third fact in terms of the rule, not the test function's name — the validator runs only against this repo's own rule files, so a consumer reading the entry cannot act on a test identifier. Keep it well short of the surrounding entries — this is a smaller change than most rows there.

### Dispatch split

**One `code-writer` dispatch.** Not because the files are few, but because every candidate split boundary fails `plan-it`'s do-not-split test: any partition would have to restate the same shared-state background — the empirical result, its `-p` limit, and the exemption-retirement decision — in both prompts, and two agents re-reading the same evidence can settle the same wording question differently with neither self-review seeing the other. Concretely, the test module's predicate docstring points at `docs/rules-references.md`, the validator's new violation message must match `rule-authoring-conventions.md`'s prose concept-for-concept, and `_CLAUDE_MD_EXPECTED_PATHS` must match the rule file's post-edit list — so a prose/code split leaves three cross-file agreements unowned. `docs/rules-references.md` would also be touched on both sides of that split, and parallel dispatches share the parent's worktree, where overlapping edits clobber silently rather than conflict.

## Critical files

All paths repo-relative. Line numbers are as of the stacked base (`origin/rules-file-review-coverage`) and will shift as edits land.

**Modify:**

- `claude/.claude/rules/claude-md-conventions.md` — frontmatter lines 3-12. Delete the five bare entries (`CLAUDE.md`, `AGENTS.md`, `CLAUDE.local.md`, `.claude/CLAUDE.md`, `.claude/AGENTS.md`), leaving the five `**/`-led ones in their current order. Body unchanged.
- `claude/.claude/rules/rule-authoring-conventions.md` — lines 24-36. Split the `[unverified]` bullet and rewrite the stowed-rule bullet, per **Prescribed content**.
- `claude/.claude/rules/github-actions-workflows.md` — lines 17-23. Strike the zero-segment `[unverified]` sentence, per **Prescribed content**. *Not named in the issue's Change list; added under row 8.*
- `claude/.claude/skills/tests/test_rules_frontmatter.py` — module docstring lines 9-11; `_CLAUDE_MD_CANDIDATE_PATHS` comment block lines 58-68; new `_matches_paths_glob` after `_literal_prefix_segments` (line 102); new `_CLAUDE_MD_EXPECTED_PATHS` constant; `rule_frontmatter_violations` docstring — delete the exemption sentence by sentence boundary, per **Prescribed content**, not by line range; stowed branch lines 194-217; parametrized test lines 256-283; new `test_claude_md_conventions_paths_list_is_unchanged`; unit tests at lines 394-441 (five `"must be fully portable"` assertion updates, two inversions, two comment rewordings).
- `claude/.claude/skills/ai-instruction-and-memory-files/SKILL.md` — line 174, §4's "Stowed-rule portability" bullet, which restates the exemption in prose: "a stowed rule's globs carry no literal prefix beyond a bare filename or a two-segment `.claude/`-anchored path". Rewrite to the new invariant — every entry is `**/`-led, with no leading literal path segment. This file is a SKILL.md, so `/code-review` routes it to `skill-review` rather than to `ai-instruction-and-memory-files`; expect that dispatch in addition to the rule-file one.
- `docs/rules-references.md` — delete the `### Glob decision` subsection (lines 35-52) entirely; replace the closing bullet of `` ## `paths:` glob-dialect conventions `` (lines 246-248) per **Prescribed content**.
- `CHANGELOG.md` — one entry under `## [Unreleased]` → `### Changed`.

**Reuse rather than reimplement:**

- `_literal_prefix_segments` (line 89) is unchanged and still correct — it halts on the leading `**` and returns `()` for every `**/`-led entry, which is precisely why removing the exemption guard leaves all six stowed rules passing (row 5). Do not touch it.
- `parse_frontmatter` (imported from `validate_skill_structure`) already loads both the rule's `paths` list and every other frontmatter read in this module. The new exact-list test uses it, not a hand-rolled YAML read.
- `TestRuleFrontmatterViolations._write_rule` / `_make_repo_root` are the existing tmp_path fixture helpers; the inverted tests keep using them unchanged.
- `fnmatch` is already imported at line 46. No new import beyond that.

**Confirmed not implicated** (row 10 — do not edit): `claude/.claude/skills/tests/test_skills.py:510-524`, `claude/.claude/hooks/tests/test_ci_path_filter.py:32-49`, `docs/skills.md:20`, `claude/.claude/skills/ai-instruction-and-memory-files/SKILL.md:40`, `CHANGELOG.md:32`.

## Verification

Run from the worktree root. README.md's Tests section covers the worktree-relative `.venv` path if the contributor venv is not resolving.

1. **Scoped suite** — `.venv/bin/python3 claude/.claude/scripts/select-tests.py`, the repo's documented per-diff command. Verified against `select-tests.py`'s `DOMAIN_RULES` (lines 371-386) and `CROSS_DOMAIN_EXCEPTIONS` (line 298), this diff selects `claude/.claude/skills/tests/` and `claude/.claude/hooks/tests/`: `claude/.claude/rules/**` → skills tests (line 374), `github-actions-workflows.md` → also hooks tests (line 375), `docs/**` → both (line 377), the test module itself → skills tests (line 298), and `CHANGELOG.md` → nothing (line 301). Do not widen to the full suite by hand; a path `select-tests.py` cannot map would be a bug in its rule table, not a licence to widen.
2. **Lint** — `.venv/bin/ruff check claude/.claude/`, since a Python test module changed.
3. **Targeted assertions to confirm green, by name** — `test_rule_has_parseable_paths_frontmatter` passes for all six stowed rules with the exemption branch gone (row 5's prediction, now executed rather than reasoned); `test_claude_md_conventions_globs_match_representative_paths` passes for all nine candidates against five patterns, which is the assertion the new predicate exists to make possible (row 4); `test_claude_md_conventions_paths_list_is_unchanged` passes; and the two inverted unit tests fail on the pre-change validator and pass on the post-change one — check that inversion by running them once before editing `rule_frontmatter_violations`, since a test that would pass either way pins nothing.
4. **Skill/rule self-review** — `.claude/rules/review-pipeline-dispatch.md` routes any `claude/.claude/rules/*.md` change to `ai-instruction-and-memory-files` via `/code-review`'s dispatcher, and any SKILL.md change to `skill-review`. Three rule files and one SKILL.md change here, so expect both dispatches. The rule-file one is not hook-enforced and will not block a commit if skipped — run it anyway; `skill-review` is gate-enforced by `require-skill-review.sh`.
5. **Required before merge — the interactive-mode re-run that retires row 2.** Re-run the `InstructionsLoaded` zero-segment check in an ordinary interactive session rather than `-p` mode: arm the hook filtering on `load_reason: path_glob_match`, read repo-root `install.sh`, and confirm `shell-script-conventions.md` loads. This is the engineer's own step — it needs an interactive session, so no agent in the implementation dispatch can run it.

   It is a merge gate rather than an optional extra because the redundancy this PR spends was hedging two axes, not one. The ten-entry state was robust whether or not `**/` matched at depth zero *and* whether or not the two run modes load instructions identically; the measurement settles only the first. If row 2 is wrong, a repo-root `CLAUDE.md` silently stops loading its rule in every consumer's interactive sessions — the mode they actually work in, on the case the Context calls the most important. M1's "reversible by re-adding five lines" holds only once someone notices, and a silent non-load is precisely what nobody notices.

   On a pass, narrow the **Limit** sentence in `docs/rules-references.md` in the same commit: both modes were checked, and the `-p` qualifier comes out of `rule-authoring-conventions.md`'s settled bullet too. On a fail, stop — the five bare entries are load-bearing after all, and the issue's premise needs revisiting rather than the plan patching around it.

## Out of scope

- **`**/.claude/CLAUDE.md` and `**/.claude/AGENTS.md` are probably redundant too, and are deliberately left in place.** Under any standard `**` dialect, `**/CLAUDE.md` subsumes `.claude/CLAUDE.md`, and the depth-3 positive control (`claude/.claude/hooks/_lib.sh` matching `**/*.sh`) shows this dialect's `**/` traverses a dot-directory rather than skipping it — so the usual dotglob objection does not apply. But the direct evidence covers zero and three intermediate segments, not the one segment these two entries occupy (row 11, `[unverified]`). This PR exists because an unverified glob inference shipped defensively once already; making a second inference on the same axis in the same change repeats the pattern rather than fixing it. The two entries cost two lines and carry no failure mode, unlike the five bare entries that the new portability test would have flagged. Recorded durably in `docs/rules-references.md`'s dialect section, not only here — a future rule-editor looking at five `**/`-led entries needs to find that the subsumption was examined and deferred, rather than re-deriving it or re-hedging. Settling it needs its own depth-1 trial.
- **Re-verifying the dialect's remaining `[unverified]` behaviors.** `?` support, leading-`/` anchoring, and trailing-`/` semantics stay `[unverified]` in `rule-authoring-conventions.md`. The instrument that settled the zero-segment question would settle these too, but nothing in this change depends on any of them.
- **Re-adding a root-only-matching exemption.** M3 retires a check exemption that still covers one legitimate shape — a stowed rule matching only the repo-root instance of a file. No rule needs it today. If one arises, it should arrive together with the exemption and give the unit test a real fixture, rather than the exemption sitting dormant waiting for a use.
- **Any change to project rules under `.claude/rules/`.** The exemptions are `is_stowed`-only; the `is_stowed=False` branch of `rule_frontmatter_violations` and every project rule are untouched.
- **Widening the count pin's successor into a generic per-rule content pin.** `_CLAUDE_MD_EXPECTED_PATHS` covers one rule because one rule has a `paths` list whose contents a candidate-match test can silently under-constrain. Generalizing it across all six stowed rules is a separate call with its own maintenance cost.
- **Any edit to `main`.** This branch targets `origin/rules-file-review-coverage` and merges after PR #842 (row 6). Nothing here should be cherry-picked forward independently — the carve-out this change retires does not exist on `main`.
