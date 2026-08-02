# plan-review/ROUTING.md has no length cap or independent review marker

## Context

`plan-review/ROUTING.md` is currently invisible to both mechanical gates that
would otherwise catch bloat or unreviewed drift in a skill's runtime content:
`check-skill-length.sh` only greps staged paths matching `.../SKILL.md`, and
`require-skill-review.sh` scopes its early-exit and its marker hash to the
same `**/SKILL.md` pathspecs — so a `ROUTING.md`-only commit has no length
ceiling and rides along on whatever `SKILL.md` marker happens to be on disk
(or on none, if no `SKILL.md` was touched in the same commit) instead of
being independently audited. This was surfaced during GH-481's three-round
plan review (ledger row 9) and filed as
[GH-504](https://github.com/jcdendrite/claude-config/issues/504). The fix
closes both gaps: give `ROUTING.md` the same length cap its content shape
already earns, and fold it into `/skill-review`'s gated scope so an edit to
it cannot commit without a review marker computed over that edit specifically.

## Approach

**Root problem:** `plan-review/ROUTING.md` carries dispatcher-routing
content (item ownership, spawn criteria, reconciliation rules) with the same
review-worthiness as a `SKILL.md` file, but neither mechanical gate that
enforces review-worthiness for that content class currently sees it.

Extend both existing hooks to recognize `claude/.claude/skills/plan-review/ROUTING.md`
as an explicit, hardcoded third pathspec — not a generalized "any co-located
auxiliary file" mechanism. `docs/skills.md` §"Skill architecture notes" and
`docs/design-decisions.md` §4 both frame `ROUTING.md` as a **last-resort,
single-instance exception** ("last-resort exception requiring that level of
hook enforcement, not a pattern to reach for when any skill approaches the
cap" — design-decisions.md §4), and a `git grep` across
`claude/.claude/skills/**` and `plugins/*/skills/**` confirms it is the only
non-`SKILL.md`, non-`REFERENCES.md` file in any skill directory today.
Building a generic auxiliary-file-discovery mechanism for a population of one
is the over-powered-primitive case CLAUDE.md's Engineering Judgment section
warns against — two lighter alternatives were considered and rejected:

- **A naming convention** (e.g., any `*-ROUTING.md` or files matching a
  frontmatter marker) that both hooks pattern-match generically. Rejected:
  no second instance exists to validate the convention against, and
  `docs/design-decisions.md` §4 explicitly says extracting to a co-located
  auxiliary is a last resort, not a pattern — building generalized
  infrastructure now would encode a convention nobody has needed twice.
- **A manifest file** (e.g., `.claude/skills/_auxiliary-files.txt`) listing
  gated auxiliary paths, read by both hooks. Rejected: adds a second file
  that itself needs its own drift-consistency guarantee between the two
  hooks' reads of it, trading one hardcoded-path duplication (already the
  established pattern between `require-skill-review.sh` and
  `marker.sh write skill-review`, proven out by
  `test_skill_marker_write_command_covers_a_plugin_skill_diff`) for a
  different one with no reduction in coupling.

The hardcoded third pathspec, mirroring the existing two-pathspec pattern
these hooks already use for stowed vs. plugin `SKILL.md` locations, is the
lighter primitive: no new file, no new convention, same shape the codebase
already reviews and tests.

**Assumption ledger**

- Root problem: `plan-review/ROUTING.md` carries dispatcher-routing content
  with the same review-worthiness as a `SKILL.md` file, but is invisible to
  both mechanical gates that enforce that for the rest of the skill corpus.
- `claude/.claude/skills/plan-review/ROUTING.md` is the only non-`SKILL.md`,
  non-`REFERENCES.md`, non-test file in any skill directory in this repo
  today `[verified: git grep -l "co-located" -- '*.md' and find across
  claude/.claude/skills, plugins/*/skills — only ROUTING.md matches]`. This
  is why a hardcoded third pathspec is proportionate rather than a
  generalized mechanism `anchors: root`.
- `ROUTING.md` already meets `docs/skills.md`'s documented criterion for
  hook-gating a review skill — "Gate skills whose target files carry
  always-loaded context budget on every session **or route dispatcher
  decisions**" — since it is literally `plan-review`'s item-ownership and
  spawn-routing table, read at runtime via the Read tool and enforced by
  `require-routing-read.sh` before any specialist spawn `[verified:
  docs/skills.md "Skill architecture notes" bullet on gating criteria;
  claude/.claude/hooks/require-routing-read.sh]`. This grounds extending
  `require-skill-review.sh`'s scope rather than leaving it to dispatcher-
  level-only invocation (the lazy-loaded class docs/skills.md contrasts it
  with) `anchors: root`.
- The 500-line cap (not the 200-line default) is the correct ceiling for
  `ROUTING.md`, because `check-skill-length.sh`'s own header comment
  reserves the 500-line ceiling specifically for "structural-dispatcher
  skills... [that] carry item-ownership / routing tables that legitimately
  run longer" — the same content class `ROUTING.md` was extracted from
  `plan-review/SKILL.md` to hold `[verified:
  claude/.claude/hooks/check-skill-length.sh lines 10-15; ROUTING.md's own
  "Item ownership" table]` `anchors: row2` (the length-cap mechanism below).
- `require-skill-review.sh`'s structural-YAML validator
  (`validate_skill_structure.py`) must **not** receive `ROUTING.md` as
  input — it has no frontmatter, so it is not a "staged `SKILL.md`" in the
  sense that validator checks `[verified: read
  plugins/skill-management/hooks/require-skill-review.sh lines 114-169;
  ROUTING.md has no YAML frontmatter block]`. The early-exit and the marker
  hash must widen to include `ROUTING.md`; `STAGED_SKILL_PATHS` (which feeds
  the validator) must not `anchors: row3` (the review-marker mechanism
  below).
- The "corpus budget" section of `require-skill-review.sh` (aggregate
  description+`when_to_use` char budget across all `SKILL.md` files) must
  also exclude `ROUTING.md` for the same reason — it has no `description`
  field to include in that budget `[verified: same read as above, lines
  171-199]` `anchors: row3`.
- `marker.sh write skill-review`'s pathspecs must change in lockstep with
  `require-skill-review.sh`'s read-side pathspecs, or the write and read
  sides silently disagree and either over- or under-gate — this is the same
  failure class `test_skill_marker_write_command_covers_a_plugin_skill_diff`
  exists to catch for the current two pathspecs `[verified: read
  claude/.claude/scripts/marker.sh skill-review case;
  claude/.claude/hooks/tests/test_require_skill_review.py lines 304-343]`
  `anchors: row3`.
- `test_reconciliation_block_consistency.py`'s module docstring currently
  asserts `ROUTING.md` "sits outside every mechanical gate" and names
  `check-skill-length.sh`/`require-skill-review.sh`'s `SKILL.md`-only scope
  as the reason its own test exists. Both hooks change scope under this
  plan, so the docstring's factual claim about current gate coverage
  becomes stale even though the drift test's own reason for existing
  (cross-file semantic drift between two Reconciliation blocks) is
  untouched by a length cap or a review-marker requirement — neither
  mechanism diffs file content against another file `[verified: read
  claude/.claude/hooks/tests/test_reconciliation_block_consistency.py lines
  1-38]` `anchors: root`.
- `plugins/skill-management/hooks/require-skill-review.sh` and
  `plugins/skill-management/skills/skill-review/SKILL.md` both live inside
  the `skill-management` plugin
  (`plugins/skill-management/.claude-plugin/plugin.json`, currently
  `"version": "3.0.2"`), and this repo's own
  `.claude/rules/review-pipeline-dispatch.md` requires any commit touching a
  file under a plugin directory to strictly raise that plugin's `version`
  since the branch's merge-base, enforced by
  `require-plugin-version-bump.sh` — so this plan's own implementation
  commit would be denied by a pre-existing gate if it omits the bump
  `[verified: plugins/skill-management/.claude-plugin/plugin.json;
  .claude/rules/review-pipeline-dispatch.md "any file under a plugin
  directory" bullet — found via staff-platform-engineer spawn, confirmed by
  reading both files directly]` `anchors: root`.
- `claude/.claude/tests/helpers.py`'s `write_skill_review_marker` (lines
  459-469) independently re-derives the same `git diff --cached --
  'claude/.claude/skills/**/SKILL.md' 'plugins/*/skills/**/SKILL.md'`
  pathspec list in Python rather than shelling out to `marker.sh` — its
  sibling `write_plan_review_marker`'s docstring (lines 421-436) states
  this duplication is deliberate, specifically so tests using it "can catch
  drift in the shell-side recipe." Left unwidened, it becomes a stale test
  double: any test seeding a marker through this helper for a diff that
  includes `ROUTING.md` gets a hash computed over only the old two
  pathspecs, silently diverging from what the real `marker.sh` (once
  widened) would produce `[verified: read claude/.claude/tests/helpers.py
  lines 421-470 directly — found via staff-platform-engineer spawn]`
  `anchors: row3` (the review-marker mechanism).
- The `skill-management` plugin activates hook-scope changes only when a
  contributor updates the installed plugin, not merely on `git pull` (the
  same caveat `docs/skills.md` documents for `plugin-semver`, and this
  repo's own `.claude/rules/review-pipeline-dispatch.md` states explicitly
  for `require-plugin-version-bump.sh`). Between a contributor pulling this
  change and updating their plugin install, `marker.sh` (stowed, live
  immediately) would write a three-pathspec marker while the not-yet-updated
  `require-skill-review.sh` still reads two — every mixed-diff case in that
  window fails closed (a stale marker denies, never silently allows), so
  this is a transient false-deny window, not a bypass, and self-resolves on
  plugin update `[verified: docs/skills.md "Project-scoped plugins" section
  + .claude/rules/review-pipeline-dispatch.md plugin-semver bullet — found
  via staff-platform-engineer spawn]` `anchors: root`.

## Critical files

- `claude/.claude/hooks/check-skill-length.sh` — add
  `claude/.claude/skills/plan-review/ROUTING.md` to the `limit_for()` case
  arm that returns `500` (same arm as `code-review/SKILL.md` and
  `plan-review/SKILL.md`), and widen the file-selection `grep -E` at the
  bottom of the script to also match that exact path (currently
  `.../SKILL\.md` only — `ROUTING.md` will never reach `limit_for()`
  otherwise). Update the header comment (lines 10-15) to name `ROUTING.md`
  alongside the two `SKILL.md` exceptions and why.
- `plugins/skill-management/hooks/require-skill-review.sh` — widen scope to
  `ROUTING.md` in exactly two places, not three:
  1. The early-exit check (currently `SKILL_DIFF` alone decides "nothing to
     review") — OR in a `ROUTING.md`-scoped diff check so a `ROUTING.md`-only
     commit no longer short-circuits past the gate.
  2. `CURRENT_HASH`'s `git diff --cached --` pathspec list — add the
     `ROUTING.md` path as a third pathspec.

  Leave `SKILL_DIFF` (feeds `STAGED_SKILL_PATHS` → the frontmatter/YAML
  structural validator) and the corpus-budget section's pathspecs
  unchanged — both are specifically about `SKILL.md` frontmatter shape,
  which `ROUTING.md` does not have. Update the file's header comment
  (lines 11-30) describing the marker's scope to mention the third
  pathspec.
- `claude/.claude/scripts/marker.sh` — in the `write skill-review)` case,
  add the same `claude/.claude/skills/plan-review/ROUTING.md` pathspec to
  both the `_guard_staged_vs_unstaged` call and the `MARKER_VALUE` hash
  computation, so write and read side agree. **Reuse**: this is a literal
  mirror of the existing two-pathspec list already in this function — no
  new logic, just a third quoted pathspec string in both places.
- `plugins/skill-management/.claude-plugin/plugin.json` — bump `version`
  above `3.0.2`. This is an additive scope widening to an existing gate
  (more content now requires review, no existing consumer contract
  breaks), the same class of change as the `2.1.0` pyyaml
  self-provisioning bump in `CHANGELOG.md`'s `[Unreleased]` → `Added`
  section — confirm the exact level (minor vs. patch) against
  `plugin-semver`'s own review at implementation time rather than assuming
  here.
- `claude/.claude/tests/helpers.py` — add the same third pathspec
  (`claude/.claude/skills/plan-review/ROUTING.md`) to
  `write_skill_review_marker`'s `git diff --cached --` call (lines
  459-469), so this Python test double stays in sync with `marker.sh`'s
  widened recipe and continues to "catch drift in the shell-side recipe"
  per its sibling's own documented purpose.
- `plugins/skill-management/skills/skill-review/SKILL.md` — update the
  marker-pathspec comment near the `Step — Record review completion`
  section (currently names only the two `SKILL.md` pathspecs) to also name
  the `ROUTING.md` pathspec. Add a one-line scope note to the "Review
  checklist" section (§7) stating that when the staged diff is (or
  includes) `plan-review/ROUTING.md`, checklist items 1-3 (frontmatter,
  description scope, trigger specificity) do not apply — `ROUTING.md` has
  no frontmatter — and items 4-11 (length, behavior test, voice,
  cross-reference correctness, duplication justification, redaction,
  behavioral-equivalence audit) do.
- `claude/.claude/hooks/tests/test_reconciliation_block_consistency.py` —
  update the module docstring's description of current gate coverage
  (paragraph 2) to reflect that `ROUTING.md` now carries a length cap and a
  review-marker requirement, while keeping the stated reason the drift test
  itself still exists (neither new mechanism catches cross-file semantic
  drift between the two Reconciliation blocks — that is still this test's
  job alone). No change to test logic.
- `claude/.claude/hooks/tests/test_check_skill_length.py` — add a test
  mirroring the existing `plan-review/SKILL.md` 500-cap case (around line
  257) for `claude/.claude/skills/plan-review/ROUTING.md`: at/under 500
  lines and growing → allow; over 500 and growing vs. `HEAD` → deny.
  **Reuse**: copy the existing `TestCase`-style fixture helpers
  (`make_repo_with_skill`, `make_skill_content`) already in this file.
- `claude/.claude/hooks/tests/test_require_skill_review.py` — add:
  (a) a test staging a `ROUTING.md`-only change with no marker present →
  deny (the regression case GH-504 reports — today this is a silent
  allow). Assert the deny **reason** via `run_hook_reason` (already used
  elsewhere in this file, e.g. around `test_structural_validator_reads_staged_blob_not_working_tree`)
  and require it names the skill-review marker gate ("Commit blocked by
  skill-review gate...") rather than the structural validator ("Commit
  blocked by skill-management structural validator..." — the message
  `validate_skill_structure.py` would produce if `STAGED_SKILL_PATHS` were
  wrongly widened to include `ROUTING.md`). A bare `== "deny"` assertion
  cannot distinguish "correctly gated, no marker yet" from "incorrectly
  fed to the frontmatter validator," and the latter would make `ROUTING.md`
  permanently uncommittable.
  (b) a `test_skill_marker_write_command_covers_a_routing_md_diff`
  mirroring `test_skill_marker_write_command_covers_a_plugin_skill_diff`
  (lines 304-343) — stage a `ROUTING.md`-only diff, run the marker-write
  recipe extracted from `skill-review/SKILL.md`, assert the hook then
  allows.
  (c) a `test_mixed_skill_and_routing_stale_skill_only_marker_denies`
  mirroring `test_mixed_stowed_and_plugin_skill_stale_stowed_only_marker_denies`
  (lines 453-470) — stage a `SKILL.md` change, write a marker covering only
  that diff, then also stage a `ROUTING.md` change; assert the hook denies
  because the combined hash no longer matches the stored marker. Add the
  converse case too (stale `ROUTING.md`-only marker, then a `SKILL.md`
  change staged alongside it → deny). Neither existing pathspec-isolation
  test proves the three-pathspec `git diff --cached --` interpolation in
  `CURRENT_HASH` and in `marker.sh` stays byte-identical between read and
  write sides when more than one pathspec's content is actually present in
  the diff — only a combined-diff test does.
- `claude/.claude/hooks/tests/test_marker_script.py` — add `ROUTING.md`
  analogs of the existing `SKILL.md`-scoped `_guard_staged_vs_unstaged`
  tests (`test_skill_review_out_of_scope_unstaged_does_not_fire`,
  `test_skill_review_unstaged_skill_md_exits_2`, around lines 403-429):
  an unstaged `ROUTING.md`-only change must not fire the guard when nothing
  else is staged; a staged-empty/unstaged-`ROUTING.md` state must exit 2
  with the same "run `git add`" guidance. This exercises the widened guard
  pathspec directly, at the layer it actually changed, rather than only
  indirectly through the end-to-end recipe test in (b) above.
- `CHANGELOG.md` — `### Changed` entry under `[Unreleased]`, matching this
  repo's existing convention for hook-scope changes (see the
  `require-respond-pr.sh` and code-review-marker-rename entries already
  there for tone/format). Note the plugin-activation-lag caveat from the
  assumption ledger above (contributors who pull before updating the
  `skill-management` plugin see transient false-denies on mixed
  `SKILL.md`+`ROUTING.md` commits, never a silent bypass, until they
  update) so it isn't mistaken for a bug when reported.

## Verification

- `../../../.venv/bin/pytest claude/.claude/hooks/tests/test_check_skill_length.py claude/.claude/hooks/tests/test_reconciliation_block_consistency.py claude/.claude/hooks/tests/test_require_skill_review.py claude/.claude/hooks/tests/test_marker_script.py -q`
  from the worktree — even though `require-skill-review.sh` ships inside
  `plugins/skill-management/`, its tests live alongside every other hook's
  under `claude/.claude/hooks/tests/` `[verified: find -iname
  test_require_skill_review.py test_marker_script.py — both resolve to
  claude/.claude/hooks/tests/, not plugins/skill-management/]`.
- `../../../.venv/bin/ruff check claude/.claude/` (repo-wide lint; the
  `helpers.py` edit is this change's only Python source).
- `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck` to
  catch quoting/portability regressions in the two edited `.sh` files.
- Manual end-to-end check: in a scratch repo (or via the existing pytest
  hook-harness fixtures), stage a `ROUTING.md`-only edit that pushes it
  over 500 lines vs. `HEAD` and confirm `check-skill-length.sh` denies;
  then stage a small `ROUTING.md`-only edit under the cap with no
  `skill-review` marker and confirm `require-skill-review.sh` denies with
  a reason naming the marker gate, not the structural validator; then run
  the `skill-review-marker-write` recipe and confirm the same commit now
  allows; then stage a `SKILL.md` change on top of an already-marked
  `ROUTING.md`-only diff and confirm the combined commit denies again
  until re-reviewed.
- After implementation, confirm `git commit` for this change's own diff is
  denied by `require-plugin-version-bump.sh` until `plugin.json`'s
  `version` is bumped — proves the missing-bump gap the operational review
  found is actually closed, not just noted.

## Out of scope

- Generalizing gate scope to any future co-located auxiliary file beyond
  `ROUTING.md` — rejected above as premature for a population of one; the
  next instance (if any) should revisit whether a generalized mechanism is
  warranted, not this change.
- Rewriting `ROUTING.md`'s content to reduce its line count — it is
  currently 105 lines, well under both the existing 200-line default and
  the 500-line cap this plan assigns it. No content change is needed to
  satisfy the new cap today.
