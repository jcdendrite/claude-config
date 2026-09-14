# Cache ready-for-review's Step 2 (Verification) pass

## Context

Add a completion-marker cache to `ready-for-review` Step 2 (Verification)
so a resumed session doesn't re-run the project's full check/test suite
from scratch when nothing has changed since the last clean pass. Step 3
(code review) already gained exactly this treatment in the just-merged
`cumulative-review` marker (PR #837): a content-addressed marker lets a
session skip re-invoking `/code-review` when the cumulative diff is
byte-identical to one that already passed. Step 2 has no equivalent —
every resumed session re-executes the project's check/test commands
inline, even when HEAD (and the tree it points to) is unchanged from a
prior clean pass, including across a mid-gate handoff where the resuming
session has no memory that a prior session already ran it clean. The plan
for PR #837 named this residual explicitly (row 8): "step 2's verification
runs against the rebased tree on every pass and is not cached." A session
transcript surfaced the same gap independently, hypothesizing it as a
contributor to unusually high local check/test invocation volume.

The intended outcome: Step 2 skips re-running the project's check/test
commands when the current state has already been verified clean, recording
that fact durably (not by session judgment call) the same way Step 3 now
does for review.

## Approach

Add a sixth content-addressed marker kind, `verification`, whose value is `git rev-parse HEAD^{tree}` — the content-address of the tree `ready-for-review` step 2 actually executes. Step 2 consults it through a new `marker.sh check verification` subcommand (not through `marker.sh status`) and skips the project's check commands on a match; it writes the marker only after every selected command has run and passed. The read is bounded by a marker-file age so a tree hash — which cannot see the installed environment — can't authorize a skip indefinitely.

**Root problem:** `ready-for-review` step 2 re-executes the project's full check/lint/test suite on every pass, including passes where HEAD's tree is byte-identical to one that already passed cleanly, because no durable record of a clean verification exists for a resumed session or a mid-gate handoff to read.

**Givens** (fixed beyond this design's reach):

- **G1. Marker writes are self-attested.** No hook correlates a write to work actually performed; this holds for all five existing kinds and is named as an open residual in `docs/design-decisions/ready-for-review-cumulative-diff-cache.md`. Closing it needs a control that does not exist anywhere in the marker architecture, so it is outside this plan.
- **G2. `ready-for-review/SKILL.md` is capped at 200 lines.** `check-skill-length.sh`'s `limit_for()` default (`:74`) enforces it. `docs/skills.md:125-126` reserves cap relief for a narrow content class — a skill whose body is genuinely one-paragraph-per-line with no compliant reflow path, as `pr-description`'s 210-line override is — and this skill isn't in it: it still has a compliant reflow path (Critical files, Phase 2's Budget bullet), so raising its cap is routine relief the documented exception excludes — see Alternatives set aside below.
- **G3. A git tree addresses tracked, committed content only.** Untracked files, gitignored files, and a submodule's own working-tree state are outside any tree hash. This is git's object model, not a choice this design makes.
- **G4. CI runs on every push.** Owned by the repo's workflow config, and already the backstop §44's residuals lean on.

**Assumption rows:**

1. Step 2 executes the whole checked-out tree rather than the diff, so the tree's content-address is the correct key; a diff-hash key's only incremental benefit comes from cases where the executed tree changed underneath the cache, which is the population least entitled to a skip. `[engineer-verified]`
2. Step 3's `cumulative-review` marker stays diff-keyed, and the general rule is that each cache keys on the content-address of what its own step consumes — step 2 executes the tree, step 3 reads the diff. One rule, two consumers. `[engineer-verified]`
3. §44's compensating-control prose is stale in **two** sentences, not the one PR #837 named — line 11 ("step 2's verification runs against the rebased tree on every pass and is never cached") and line 13 ("step 2's verification and CI still run unconditionally on every pass, cache hit or not"). `[engineer-verified]` for line 11; `[verified: docs/design-decisions/ready-for-review-cumulative-diff-cache.md:13]` for line 13. This extends the engineer's stated scope by one sentence — same defect, same file, same fix shape, per CLAUDE.md's audit-structural-siblings rule.
4. `marker.sh status` is the only marker surface that makes a network round trip: its `cumulative-review` line calls `_lib_cumulative_diff_hash`, which shells to `pr-diff-against-base.sh` (→ `gh pr view`) under a 15s cap. Step 2 reading the cache via `status` would pay that round trip for a line it does not consume, one step before step 3 pays it again. `[verified: marker.sh:728-747; _lib.sh:612-632]`
5. `status`'s `live`/`historical` vocabulary means "hash matches / does not match" for all five kinds; folding an age bound into it for one kind alone would make `historical` ambiguous between a mismatched hash and an aged-out marker. `check` already owns the age-bounded skip semantics. `[verified: marker.sh:683-755, 788-797]`
6. The capped/uncapped convention is: write arms call git uncapped and abort loudly, `check`/`status` call it capped and degrade to no-match/absent. `[verified: marker.sh:466 (write, uncapped), :725 (status, capped), :770 (check, capped)]`
7. Four of the five existing write arms recompute their subject at write time; only `cumulative-review` captures first, and §50's rationale for that is a subject that drifts with **no local action at all** (the base moves on the remote). `HEAD^{tree}` has no external drift channel — it changes only when this worktree commits, rebases, or resets. `[verified: marker.sh:399-522; docs/design-decisions/cumulative-review-marker-not-recomputed.md]`
8. `_code_review_marker_fresh_age` is already fully parameterized on (markers_dir, expected_value, glob_prefix, max_age) and is applied only on `check code-review`'s skip path, never on `write` or a commit gate. Its header documents a precondition: callers must run `_lib_marker_value_present` first, so the O(n) candidate loop only runs once a hash match exists. `[verified: marker.sh:295-342, 788-797]`
9. `_resolve_code_review_check_max_age_seconds`'s malformed-input guard is a five-branch `case` pattern (`''|0|*[!0-9]*|0[0-9]*|?????????*`). A second threshold constant duplicating it is a drift defect under CLAUDE.md's single-source-of-truth rule. `[verified: marker.sh:288-293]`
10. `ready-for-review/SKILL.md` is exactly 199 lines against a 200 cap — one line of headroom. `[verified: file; check-skill-length.sh:67-74]`
11. Three count claims are already stale by one from PR #837, independent of this change:
    - `enforce-marker-script-shape.sh:75` says 19 single-command shapes; the deny list at `:658-677` has 20.
    - `enforce-marker-script-shape.sh:639` says 17 shapes in permissions.allow; settings.json has 18.
    - `docs/scripts.md:54` says 17 (same, 18).

    `test_enforce_marker_script_shape.py:27` and `:57` also say 19 against a 20-entry `TILDE_MARKER_SHAPES`. No test pins any of them. `[verified: all five sites read]`
12. `_extract_scope_anchor_region` and `_normalized_anchor_text` are already generic over the marker name and need no change; only `_EXPECTED_CACHE_ANCHORS` (exact set) and `_PINNED_CACHE_CLAUSES` (exact text, whitespace-collapsed) need entries. `[verified: test_skills.py:3211, 3395-3402, 3461-3528]`
13. `_resolve_repo_root` calls `_refuse_main_tree_under_enforcement`, so both new arms inherit the main-tree refusal automatically — including `check verification`, matching `check code-review`'s existing behavior. `[verified: marker.sh:132-144]`
14. No `.gitmodules` exists in this repo, and no `verification-markers` / `check verification` / `write verification` string collides with existing code. `[verified: Glob and Grep over the worktree]`
15. The new kind's tests need no `origin/<default-branch>` arming, unlike `cumulative-review`'s, because a tree hash needs no merge-base. The shared `git_repo` fixture suffices unmodified: it seeds one commit (`init`) before staging further edits, so `git rev-parse HEAD^{tree}` resolves against it with no `origin` arming. `[verified: claude/.claude/hooks/tests/conftest.py:272-285]`
16. `VERIFICATION_CHECK_MAX_AGE_SECONDS` defaults to 14400 (4h) — an explicit design choice, ungrounded in the same sense §62 discloses for its own 86400. Rationale is design intent, not measurement: 4h covers the motivating window the Context names (a resumed session, a mid-gate handoff, a step-3/4 fix loop back into step 2) while expiring between working sessions, where an interpreter bump or a rebuilt venv would land. Overridable by env var like the existing one. `[unverified]`

**Mechanisms:**

- **M1 — a new `verification` marker kind, tree-keyed.** *anchors: root, row1.* Over-powered-primitive check, two lighter alternatives from the existing system: (a) *reuse the existing `ready-for-review` HEAD-SHA marker as step 2's signal* — fails because it is written at step 8 after the whole gate completes, gates the push, and asserts "the gate finished," not "the checks passed," so reading it at step 2 would skip on a record written for a different claim; (b) *let the session remember it already ran step 2* — fails on the Context's own motivating case, a resumed session or handoff with no such memory, which is precisely why the record must be durable rather than in-context.
- **M2 — `check verification` as the read surface, plus a `status` report line.** *anchors: row4, row5.* `check` is the existing home for an age-bounded advisory skip and keeps step 2 fully offline; the `status` line is added separately, unbounded, because `status`'s documented contract is to report every completion marker and an omitted kind is a debugging blind spot. This is the same split `code-review` already has across the two surfaces.
- **M3 — age bound via `_marker_fresh_age` + `VERIFICATION_CHECK_MAX_AGE_SECONDS`.** *anchors: row8, row16, G3.* The tree hash cannot observe interpreter version, venv contents, gitignored files, or a submodule's dirty tree, so hash-match alone must not authorize a skip forever. Rename `_code_review_marker_fresh_age` → `_marker_fresh_age` once it has two callers (CLAUDE.md: a narrowly-scoped label pushes up to the canonical name); it is already parameterized, so only the name and five test docstrings move.
- **M4 — recompute at write time; do not adopt §50's capture-then-consume.** *anchors: row7.* §50's two-phase shape exists for a subject with an external drift channel; `HEAD^{tree}` has none, so importing it would be a heavier coordination pattern closing a gap that does not exist, and would cost a new subcommand plus a third command in a SKILL.md with no headroom. This puts `verification` at parity with the four existing local-subject arms. The residual — a false hit needs a commit landing strictly between checks passing and the write, inside one step-2 pass, which the gate's own flow never produces — is named in the decision doc rather than engineered away.
- **M5 — shared `_lib_head_tree_hash <capped|uncapped> REPO_ROOT` in `_lib.sh`.** *anchors: row6.* One helper called by write, check, and status, mirroring `_hash_staged_diff`'s existing mode argument; a hash recipe duplicated across write and read sides drifts and then never matches, which is `_lib_active_plan_hash`'s documented reason for existing.
- **M6 — tighten step 1's clean check to name untracked files.** *anchors: G3, row1.* The tree key is only honest if the working tree matches HEAD's tree; an untracked test file is executed by the checks but sits outside the tree hash, in both directions (present at the clean pass then deleted, or added after it). `git status --porcelain` already reports untracked entries by default, so this is a prose-ambiguity fix, not a new check — and it fits on the existing line. Closes the precondition rather than leaving it a residual.
- **M7 — correct §44's two compensating-control sentences and link the new decision file.** *anchors: root, row3.* The change falsifies both in the literal sense while keeping them true in substance (a rebase changes the tree, so step 2 still re-runs); §44 gets corrected clauses plus a pointer, and the new file owns the why-two-keys-differ explanation.
- **M8 — correct the three stale counts while bumping them.** *anchors: row11.* Bumping an already-wrong count to wrong-plus-one is worse than correcting it, and all five sites live in files this change already opens.

**Alternatives set aside.**

- Keying on the diff hash or the commit SHA — settled: step 2 executes the tree; commit-SHA is strictly dominated.
- Reading the cache from `marker.sh status` — rejected on M2's two grounds.
- Chaining the write to the check command as `<checks> && marker.sh write verification` — rejected: `enforce-marker-script-shape.sh` fast-exits at `:591` unless the command *starts* with the marker.sh path, so the chained form would be shape-unvalidated, and `permissions.allow`'s exact-match entry would not cover it, causing a permission prompt on every gate run.
- Adding `ready-for-review/SKILL.md` to `limit_for()`'s per-skill override table instead of reflowing — rejected on two grounds: `pr-description`'s 210-line override is justified by having *no* compliant reflow path (a file that is one-paragraph-per-line with no hard-wrap, so word-trimming can't reduce its line count), and that rationale is false here, since `:54-63`'s two paragraphs are a compliant reflow path worth 7 lines against the 5 needed; and the override is the larger diff besides, since `test_check_skill_length.py:385-465` pairs every override entry with its own under-cap/over-cap test pair and pins the absence of others, while reflow touches no test.

## Critical files

Two sequenced `code-writer` dispatches. Phase 2 is not parallelizable with phase 1: the SKILL.md text prescribes the exact commands phase 1 makes valid, and `_PINNED_CACHE_CLAUSES` pins text that must already exist.

**Phase 1 — marker plumbing and gates** (verification: `.venv/bin/python3 claude/.claude/scripts/select-tests.py`, `.venv/bin/ruff check claude/.claude/`, `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck`)

- `claude/.claude/hooks/_lib.sh` — add `_lib_head_tree_hash <capped|uncapped> REPO_ROOT` beside `_lib_cumulative_diff_hash` (`:612`), running `git -C "$repo_root" rev-parse "HEAD^{tree}"` (quote the literal). **Reuse:** `_lib_capped_for`/`_lib_capped` (`:27-48`) for the capped mode; follow `_hash_staged_diff`'s existing mode-argument shape rather than inventing one.
- `claude/.claude/scripts/marker.sh` —
  - Extract `_marker_max_age_or_default RAW DEFAULT` from `_resolve_code_review_check_max_age_seconds` (`:288-293`) so the five-branch malformed-input `case` has one copy; add `_resolve_verification_check_max_age_seconds` with default `14400`, disclosing it as ungrounded in its header the way the existing one cites §62.
  - Rename `_code_review_marker_fresh_age` → `_marker_fresh_age` (`:295-342`, call site `:793`).
  - New `write verification` arm after `cumulative-review` (`:522`): resolve session id and repo root, compute via `_lib_head_tree_hash uncapped`, abort without writing on empty, `mkdir -p "$CONFIG_DIR/verification-markers"`, write `<repo-hash>.<session-id>`. Compute before redirecting, per the convention `:404-406` states. No `_guard_staged_vs_unstaged` (the subject is the committed tree, not the staged diff).
  - New `check verification` arm beside `check code-review` (`:756-800`): `_lib_head_tree_hash capped`, no-match on empty, then `_lib_marker_value_present` first and `_marker_fresh_age` second — that ordering is the helper's documented precondition (`:299-302`). Print `match age_seconds=%s` / `no-match` with the same exit codes.
  - New `status` line for `verification` after the `ready-for-review` line (`:726`), capped and stderr-suppressed, empty-not-fatal — no age bound here, matching how `status`'s `code-review` line reports hash state while `check code-review` applies the bound.
  - Update `usage()` (`:37-53`): the `status` description's kind list, the `write` combination row, and the `check` row (`check code-review | verification`).
  - Update the `write` rejection message (`:524`) — `test_write_roster_is_closed_and_self_reported` parses this string and compares it to `WRITE_SKILLS`, so it must list `verification` last — and the `check` rejection message (`:802`).
  - **Load-bearing comment**, one line, at the new write arm's value computation: ``Each marker kind hashes what its own step consumes. `verification` hashes the tree (what step 2 executes); `cumulative-review` hashes the diff (what step 3 reads).``
- `claude/.claude/hooks/enforce-marker-script-shape.sh` — `MARKER_SHAPE` (`:598`): `check[[:space:]]+code-review` → `check[[:space:]]+(code-review|verification)`, and add `verification` to the `write` alternation. Add both new shapes to the deny-message list (`:658-677`). Leave `VALID_CHAINED_COMMIT_PATTERN` (`:629`) untouched — `verification` is written after checks pass, never before a commit, same as `cumulative-review`. **Count corrections:** header `:75` 19 → 22 (correcting row 11's pre-existing off-by-one to 20, then adding 2 new shapes — not 19 + 2); `:639` 17 → 20 (same fix: pre-existing actual is 18, plus 2 new).
- `claude/.claude/settings.json` — two exact-match entries after `:21`: `"Bash(~/.claude/scripts/marker.sh write verification)"` and `"Bash(~/.claude/scripts/marker.sh check verification)"`. No globs.
- `claude/.claude/hooks/tests/test_marker_script.py` — append `"verification"` to `WRITE_SKILLS` (`:869`); add a `TestMarkerScriptVerification` class modelled on `TestMarkerScriptCumulativeReview` (`:1981-2514`) covering: write success and directory isolation, write abort in a commit-less repo, `check` match/no-match, `check` no-match on an aged-out marker and on a moved HEAD, `check` no-match when the tree hash can't be computed, `status` live/historical/absent, and write/check recipe agreement. Use plain `git_repo` with no `origin` arming, per row 15. Update the five `_code_review_marker_fresh_age` docstring references (`:2904, 2963, 3031, 3149, 3156`). Three further cases are load-bearing, not optional additions:
  - **Same-tree HEAD move still matches.** `check` must report `match` after `git commit --amend --no-edit` (or any commit reproducing an identical tree) against a marker written before the amend — the positive-direction proof that tree-keying (row 1's whole justification over commit-SHA-keying) actually behaves differently from commit-keying. Pair this with a write-side assertion that the marker's stored value equals an independently-computed `git rev-parse HEAD^{tree}` (not just "looks like a hex digest," which would pass even if the write arm accidentally hashed `HEAD` instead of `HEAD^{tree}`).
  - **`VERIFICATION_CHECK_MAX_AGE_SECONDS` gets its own resolver tests**, mirroring the six existing `CODE_REVIEW_CHECK_MAX_AGE_SECONDS` tests (`:2883-3016`) rather than relying on those tests alone to prove `_marker_max_age_or_default`'s extraction is safe: override, malformed-value fallback to 14400, and the strict-less-than boundary, keyed to `verification`'s own env var name and default.
  - **A timing test for the new capped git call**, mirroring the three existing `@pytest.mark.timing` tests (`:3075-3193`) that prove a stalled `git diff`/`stat`/`grep` degrades to `no-match` rather than hanging — `_lib_head_tree_hash capped` is a new call on the same `check` fast-path and needs the same proof.
- `claude/.claude/hooks/tests/test_enforce_marker_script_shape.py` — append both shapes to `TILDE_MARKER_SHAPES` (`:31-52`); correct the count comments at `:27` and `:57` from 19 to 22 — `TILDE_MARKER_SHAPES` already has 20 entries (row 11's pre-existing off-by-one), plus 2 new, not 19 + 2.

**Phase 2 — skill body, anchors, docs** (verification: `.venv/bin/python3 claude/.claude/scripts/select-tests.py`, `/skill-review` hook-enforced on the SKILL.md diff, `wc -l claude-skills/skills/ready-for-review/SKILL.md` ≤ 199)

- `claude-skills/skills/ready-for-review/SKILL.md` — must end at ≤199 lines.
  - Step 2 gains a `CACHE_RULE:ready-for-review-verification-cache` anchor pair placed before the command-selection paragraph and before the scope-exception skip, enclosing exactly this text:

    > Before selecting any commands, run `~/.claude/scripts/marker.sh check verification`. `match` means this exact tree already passed a clean verification pass inside the freshness window. On a match: skip the commands below, report the cache hit in the Completion summary, and continue to step 3. On `no-match`, run the step normally, then write `~/.claude/scripts/marker.sh write verification` only after every selected command has run and passed. Do not write it after the scope-exception skip below — that path runs no commands, so nothing has passed.

    Do not copy step 3's "Content type is never a skip reason on its own" sentence: step 2 has its own content-type skip immediately below, and the two would contradict.
  - Step 1 (`:38`): `no unstaged or uncommitted changes` → `no unstaged, uncommitted, or untracked changes`. Same line, no growth.
  - Completion (`:166`): `- Verification: commands run and their results.` → `- Verification: commands run and their results, or "skipped — cache hit."`
  - **Budget:** +5 lines from the anchor block. Recover ≥7 by reflowing two step-2 paragraphs to the file's own long-line style (`:70` and `:118` are already single long lines): "Scope exceptions" (`:54-59`, 6 lines → 1) and "Pre-existing failures" (`:61-63`, 3 lines → 1), landing at 197. Preserve every word and every `**bold**` marker — this is reflow (line-joining only), not compression; verify with a whitespace-insensitive diff of the two paragraphs against `git show HEAD:claude-skills/skills/ready-for-review/SKILL.md`, not by eye. Leave the three `HOOK_TEST_FIXTURE` fenced blocks (`:27-30, 140-143, 147-150`) untouched; the hook-alignment suite reads them verbatim.
- `claude-skills/skills/tests/test_skills.py` — add `("ready-for-review", "CACHE_RULE:ready-for-review-verification-cache")` to `_EXPECTED_CACHE_ANCHORS` (`:3466`) and the exact clause above to `_PINNED_CACHE_CLAUSES` (`:3501`). **Reuse:** `_extract_scope_anchor_region` and `_normalized_anchor_text` unchanged.
- `docs/design-decisions/ready-for-review-verification-cache.md` — new file. H1, blank line, `*2026-09-09.*` (no `Formerly §N` — recorded after the split). Content: the tree-hash key and why; the one-sentence general rule (each cache keys on the content-address of what its own step actually consumes) reconciling it with `cumulative-review`'s diff key; why the read is age-bounded and that 14400 is an ungrounded default disclosed the way §62 discloses 86400; why `check` and not `status` carries the bound; and four named residuals — recompute-at-write's narrow false-hit window (with §50's rationale for why the two-phase shape does not transfer), a submodule's dirty working tree being invisible to a tree hash, G1's self-attestation at parity with the other five kinds, and the manual-invalidation path: with no operator-facing invalidation lever beyond the 4h age bound or an actual tree-changing commit, the answer to "the cache said clean but it's wrong" is deleting the stale file directly under `verification-markers/`, which is worth stating explicitly rather than leaving implicit.
- `docs/design-decisions/ready-for-review-cumulative-diff-cache.md` — correct two sentences (`:11` and `:13`) to say step 2's verification is now cached on the tree it executed, so a rebase always re-runs it, and link the new file for the two-keys reconciliation. Do not restate the reconciliation here. Everything else in the file is a preserved record — leave it.
- `docs/design-decisions/content-addressed-review-markers.md` — `:5` currently opens "Five kinds exist"; make it six and add a `verification` clause naming the tree-hash key, mirroring how the `cumulative-review` clause reads.
- `docs/hooks.md` — Marker keying section (`:103`): add `verification` to the per-kind content enumeration, naming `HEAD^{tree}` and that it is read by `ready-for-review` step 2 via `marker.sh check verification`, not by a `require-*.sh` hook.
- `docs/scripts.md` — `:54`: 17 → 20 (pre-existing actual is 18, plus 2 new — not 17 + 2), and name the new kind.

**Review surface:** 13 files across three domains (shell scripts + hooks, skill body, docs), with risk concentrated in `marker.sh`'s two new arms and the SKILL.md reflow. The doc and count edits are mechanical.

## Verification

Per this repo's CLAUDE.md, agents run the scoped selector, not the full suite:

```bash
.venv/bin/python3 claude/.claude/scripts/select-tests.py
.venv/bin/ruff check claude/.claude/ claude-skills/
scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck
```

The changed paths (`claude/.claude/scripts/marker.sh`, `claude/.claude/hooks/_lib.sh`, `claude/.claude/hooks/enforce-marker-script-shape.sh`, `claude/.claude/settings.json`, `claude-skills/skills/ready-for-review/SKILL.md`, `docs/**`) should resolve to `SCRIPTS_TESTS_DIR` + `HOOKS_TESTS_DIR` + `SKILLS_TESTS_DIR` rather than a full-suite widen — `DOCS_DIR` is a declared blanket rule (`select-tests.py:139-147`) and `CLAUDE_SETTINGS_JSON` names all three test directories (`:119-126`). If the selector widens to the full suite anyway, that is the correct run, not a reason to narrow by hand.

Tests that must be green and are specifically load-bearing here:

- `test_marker_script.py` — the new `TestMarkerScriptVerification` class, plus `test_write_roster_is_closed_and_self_reported`, which reads the write roster back off the rejection message. Within that class, the same-tree-HEAD-move `check` case and its paired write-side hash assertion are the ones actually proving tree-keying behaves differently from commit-SHA-keying — treat those two as load-bearing above the rest of the class.
- `test_enforce_marker_script_shape.py` — `test_valid_shapes_allowed` and `TestPrescriptionAllowlistAlignment::test_every_hook_accepted_tilde_shape_has_an_allow_entry_or_is_excepted`, which cross-checks each new shape against `settings.json`.
- `test_marker_script.py`'s existing max-age tests (`:2883-3012`) — the regression net proving the `_marker_max_age_or_default` extraction is behavior-preserving for `code-review`.
- `test_skills.py` — `test_cache_rule_anchors_present` and `test_pinned_cache_clause_matches_live_text`.
- `test_design_decision_files.py` — filename grammar and provenance line for the new decision file.
- `test_hook_alignment.py` — the three `HOOK_TEST_FIXTURE` blocks must still match `require-ready-for-review.sh`'s layout after the reflow.

Manual check the suite does not cover: run `~/.claude/scripts/marker.sh check verification` on a clean feature worktree, confirm `no-match`; run `write verification`; re-run `check` and confirm `match age_seconds=N`; modify a tracked file and commit it, then confirm `check` returns to `no-match` — an empty commit (`--allow-empty` or one with nothing staged) reproduces the parent's tree and must NOT trip this, so it is not a valid test of invalidation. Confirm the Phase 2 reflow lost no words: diff the reflowed "Scope exceptions" and "Pre-existing failures" paragraphs against `git show HEAD:claude-skills/skills/ready-for-review/SKILL.md`'s versions with whitespace collapsed on both sides, not by eye.

## Out of scope

- **Step 2's content-type skip carries the same base-move blind spot §44 names for step 3, and this change does not close it.** "Skip when the diff contains no executable code" decides from the diff while step 2 executes the tree, so a docs-only diff rebased onto a base that changed executable code still skips verification of code nobody ran. Closing it means re-deriving the skip's predicate from tree content rather than diff content, which would make the skip fire almost never once a base has moved — a real behavioral tradeoff deserving its own decision. Named here so the plan is not read as having fixed it.
- **`cumulative-review`'s diff-hash key is not touched.** Re-keying it on the tree would make it miss in exactly its motivating case, which is behaviorally equivalent to deleting the kind.
- **The self-attestation residual (G1) stays open**, at parity with all five existing kinds. This change adds a sixth self-attested kind; it does not widen or narrow that property.
- **`check`'s main-tree refusal is inherited, not designed here.** `check verification` refuses on the main tree under worktree enforcement because `_resolve_repo_root` calls `_refuse_main_tree_under_enforcement` — identical to `check code-review` today. Whether a read-only subcommand should refuse at all is a pre-existing question for a separate change.
- **No age bound is added to any `write` arm, to `status`, or to a `require-*.sh` commit gate.** §62's reasoning for confining the bound to the advisory `check` path holds unchanged.
- **`docs/design-decisions/cumulative-review-marker-not-recomputed.md` (§50) and `reviewer-responsibility-bounded-to-diff.md` (§34) are read-only here** — both are cited by the new decision file, neither is edited.
