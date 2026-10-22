# The `cumulative-review` marker is keyed on normalized diff text, not raw diff bytes

*2026-10-22.*

The marker's value used to be the sha256 of `pr-diff-against-base.sh`'s raw output (see [ready-for-review-cumulative-diff-cache.md](ready-for-review-cumulative-diff-cache.md)). That preimage includes every `@@ -k,l +n,m @@ TEXT` hunk header and every `index <hash>..<hash>` line. A conflict-free rebase onto a base that edited a file the PR touches, outside a hunk's context window, shifts the hunk's start offsets and changes the file's blob ids. The hash then missed over an unchanged change, and a full specialist review re-ran.

## The decision

The value is the sha256 of the text that `_lib_normalize_cumulative_review_text` prints, computed through `_lib_cumulative_review_value`. Two rules apply:

- A hunk header keeps its two line counts and loses its start offsets and its function-context TEXT. `@@ -12,3 +12,4 @@ TEXT` becomes `@@ -,3 +,4 @@`, and an omitted count stays omitted (`@@ -5 +5 @@` becomes `@@ - + @@`).
- An `index <hash>..<hash>[ <mode>]` line is dropped when a `--- ` line directly follows it. That is the shape of a text entry. A binary entry's `index` line is followed by `Binary files`, and an empty-file entry's by the next `diff --git` line or the end of input, so both stay.

Every other byte reaches sha256 unchanged, including every `+`, `-` and context line, path line, mode line and rename line. The recorded-subject protocol is unchanged: `write` hashes the text that `--record` captured at step-3 entry (see [cumulative-review-marker-not-recomputed.md](cumulative-review-marker-not-recomputed.md)), and `status` recomputes the live diff.

## The property: a normalized-text equivalence key

The marker is a normalized-text equivalence key, not a content hash. This file is the one canonical statement of what shares a value. The `_lib.sh` comments, the CHANGELOG entry and `docs/hooks.md` name this file and do not restate the class.

- sha256 hashes the normalized text. Binary entries are identified by git's abbreviated blob ids (at least 7 hex digits by default, more in a large repository, 4 at minimum under `core.abbrev`), so the key makes no collision-resistance claim for them. The raw preimage carried the same abbreviated `index` line, so this is not a weakening.
- NUL bytes are not hashed. Bash command substitution (`$( )`) drops them in `pr-diff-against-base.sh`'s `DIFF_TEXT` capture, in `_lib_cumulative_diff_hash` and in `marker.sh`'s recorded-subject read, so two diffs that differ only by NUL bytes share a value. The raw-byte key had the same property, so this is not a weakening.
- The rules never rewrite a body line. Two diffs share a value only when they differ solely in hunk start offsets, in function-context TEXT, in a text entry's `index` line, or in the site of a hunk with identical context.
- Hunk line counts stay in the preimage. Under `diff.suppressBlankEmpty=true`, trailing blank context lines print as empty lines that `$( )` strips, so only the counts record how many there were. Dropping the counts would make two such diffs collide. Because counts are a function of the hunk body, keeping them costs no hit on the targeted rebase.
- A header the rules do not recognize stays in the preimage, which causes a miss and never a false `live`. Recognized means the closing `@@` followed by end of line or a space. A combined-diff `@@@` header, a glued tail (`@@ -1 +1 @@x`), an `index` line with an extra field, and uppercase hex are not recognized.
- The key has no age bound and matches tree-wide and branch-agnostically, so a reapply or backport of an already-reviewed patch in the same checkout can read `live`. `docs/hooks.md` says markers match on content across every session suffix under the repo-hash, and `_lib.sh` notes that completion markers are never pruned. The compensation is the one [ready-for-review-cumulative-diff-cache.md](ready-for-review-cumulative-diff-cache.md) names: the verification cache, CI and human review.

## Why not `git patch-id`

`git patch-id` is git's first-party normalizer for diff text, and it was the first design. On git 2.56.0, a probe built two files with blank context lines and gave each an early-hunk change and a late-hunk change. Under `diff.suppressBlankEmpty=true`, `git patch-id --verbatim` gave identical ids for two diffs that differed only in the late hunk, while their raw sha256 values differed. The probe did not establish why the ids matched. A single-file diff still printed exactly one well-formed id line, and the same diff over two files printed two. The call site cannot tell from an id line whether the id covers the whole diff, and pinning that one config would close only the trigger the probe found. A filter that rewrites two named line classes and passes everything else through can only fail toward a miss.

`git patch-id --stable` was also set aside: it leaves the id unchanged for a re-indent of a changed line, so a whitespace-only edit would read as already reviewed.

## The base-side widening is a change of guarantee

[ready-for-review-cumulative-diff-cache.md](ready-for-review-cumulative-diff-cache.md) accepted, as a named residual, that a reviewer's judgment can depend on code outside the diff. This decision widens that residual, and the widening is a change of guarantee, not the same residual class at closer range. Before this change, any base edit to a file the PR touches missed, because the post-image blob id sits in that file's `index` line. After it, a base edit outside the effective ±3-line context window keeps `live`, including an edit to a callee defined in the same file. The compensation (the verification cache and CI) is unchanged.

## Residuals

Only the base-side widening above and the PR-side relocation below were accepted by the engineer as named residuals. Dropping function-context TEXT is this design's own classification, as base content outside the window, made after the engineer asked for the correct design. The unchanged mode, abbreviated binary blob ids and textconv rest on this design's own judgment. Each pin is named by test.

- **Function-context TEXT after `@@` is not hashed.** TEXT is a heuristic pick that names the enclosing function, not hunk content. In markdown or YAML it is often the line just above the context window, which a base edit can change, so keeping it would add misses on the targeted rebase. Pinned by `test_edit_relocated_by_a_hand_written_variant_keeps_the_value[different-function-context-text]` in `TestLibCumulativeReviewValue`.
- **The unchanged mode on a dropped `index` line is not hashed.** A mode change prints separate `old mode` and `new mode` lines, which pass through. Pinned by `test_normalizer_output_equals_the_hand_derived_oracle_text`, whose text entry carries a mode on its dropped `index` line.
- **PR-side relocation.** Any moved edit (an addition, a deletion or a replacement), including a move across functions, keeps its value when it lands at a site with identical context over the effective width. The worst case is a move to the wrong copy of duplicated code. This is separate from the base-side widening, and the exposure needs a repeated window. It also needs the moved edit to keep its position relative to the file's other PR hunks, because the normalized text keeps hunk order. No normalization can keep a hunk's site and still survive a line-shifting rebase.
  - A commit that makes the move is a changed staged diff, so the commit-time `/code-review` gate reviews it. That gate does not cover a move made while resolving conflicts in `git rebase --continue`, or a commit outside a gated session. The verification cache, CI and human review also apply. The "bytes nobody changed" reasoning in [ready-for-review-cumulative-diff-cache.md](ready-for-review-cumulative-diff-cache.md) does not carry over, because a relocation changes the PR's own bytes.
  - A conflict-free rebase whose automatic merge lands a hunk at a different identical-window site runs no commit-time gate, because no commit is made. Probed on git 2.56.0 with one fixture: a file of a four-line prefix, a six-line stanza, a separator, the same stanza again and a four-line suffix. The PR added one line to the first stanza, and the base edited the first stanza's second line, which is inside the added line's context window. `git rebase --apply` and `git am` applied the PR to the second stanza, and the value stayed equal to the pre-rebase value. The default merge-backend `git rebase` and `git cherry-pick` kept the line in the first stanza, so the changed context line changed the value. This is the result for that one fixture and says nothing about other merge shapes.
  - Pinned by `test_status_stays_live_after_relocated_edit_with_identical_context` in `test_marker_script.py` and by `test_edit_relocated_to_a_site_with_identical_context_keeps_the_value`, `test_edit_relocated_by_a_hand_written_variant_keeps_the_value` and `test_edit_moved_past_another_hunk_of_the_same_file_changes_the_value` in `TestLibCumulativeReviewValue`.
- **Abbreviated binary blob ids.** A binary entry is identified by git's abbreviated `index` blob id, so only grinding could produce a deliberate collision. This is unpinned by design, since only grinding could exercise it. Adding `--full-index` to the merge-base `git diff` would close it, but it changes the text reviewers read, so it is left out.
- **Textconv.** A binary rendered through a textconv filter prints a text body headed by `--- `, so its `index` line drops and its textconv output alone identifies it. The reviewer reads the same textconv text the marker covers, because `--diff-file` is the script's own output, so the marker still attests what was reviewed. Pinned by `test_textconv_binary_renders_a_text_body_headed_by_a_minus_header` in `TestGitDiffEntryShapes`.

## What is deliberately not normalized

Each of these can only cause a miss: color, `similarity index`, and diff config such as the algorithm. `pr-diff-against-base.sh` passes `--no-color --no-ext-diff` on its merge-base `git diff`, so color and external-diff config cannot reach the text. `test_merge_base_diff_ignores_color_and_external_diff_config` is the guard for those flags.

Context width is not in that list. The relocation residual grows as the effective context width (`diff.context`, `-U<n>`, `GIT_DIFF_OPTS`) shrinks below 3, and at 0 the same added line at any position in a file collides. The script passes none of them and does not pin the width, matching how `diff.suppressBlankEmpty` is handled: config-gated, named, and pinned in tests. Adding `-U3` alone would not pin it, because `GIT_DIFF_OPTS` takes precedence over `-U`.

## Portability

The filter runs on whichever `awk` comes first on `PATH` and avoids interval expressions. `awk` runs under `LC_ALL=C` so a non-UTF-8 diff cannot make a locale-sensitive `awk` exit early on a UTF-8 locale. `TestLibCumulativeReviewValue` looks up five names (`/usr/bin/awk`, `mawk`, `gawk`, `original-awk` and `nawk`), keeps one entry per real file name, and runs each against the same oracle text. An `awk` installed under any other name is not run. The `LC_ALL=C` pin is checked against a real locale-sensitive `awk` only on macOS contributor machines, and on CI through an environment-asserting shim. If `write cumulative-review` refuses with `awk`'s own error on stderr, put an `awk` that one of those five names resolves to, and that passes that test, first on `PATH` as `awk`, or revert.

## Rollback

Both arms reach the recipe through `_lib_cumulative_review_value`, the single seam. Revert the commit as a unit, or make that function hash its argument directly through `_lib_hash_diff_text`. The oracle tests that encode the normalized recipe move with that change. Never revert one arm alone, because `write` and `status` would then disagree and every marker whose diff has a hunk header or a dropped `index` line would read `historical`. Either rollback re-keys every marker written under the normalized recipe whose diff has a hunk header or a dropped `index` line, so each such in-flight branch pays a second full review. A diff that normalizes to itself is unaffected.

## Consumer premise

The weakened key is tolerable because no hook reads the `cumulative-review` marker: `docs/hooks.md` says "No hook reads the `verification` and `cumulative-review` markers. Only `marker.sh` does." Any gate that adopts this value needs a threat-model tier review first.

A false `live` skips the cumulative pass in `ready-for-review` step 3. The skill's prose enforces that skip, and no hook does. The `ready-for-review` write arm of `marker.sh` records only the HEAD sha and does not consult the `cumulative-review` marker.

`test_no_hook_references_the_cumulative_review_value_helpers` in `test_hook_alignment.py` is a tripwire on direct references: it fails if a hook references `_lib_cumulative_review_value` or `_lib_cumulative_diff_hash`. It does not detect a hook that shells out to `marker.sh status` or reads `cumulative-review-markers/`, because neither names either helper. It does not detect a future `_lib.sh` wrapper, because `_all_hook_files()` excludes `_lib.sh`.

## Weakened claims elsewhere

`ready-for-review/SKILL.md` says a fix "misses on the changed bytes" and that `live` means "this diff content already passed". Both are now approximate for the residuals above: a fix that only relocates an edit to a site with identical context leaves the marker `live`, and so does a rebase or merge-sync that changes only function-context TEXT or a text entry's blob ids. The skill's wording is unchanged. [cumulative-review-marker-not-recomputed.md](cumulative-review-marker-not-recomputed.md)'s mistimed-write check, that the recorded subject and the live diff disagree, weakens the same way. [ready-for-review-fix-loop-convergence.md](ready-for-review-fix-loop-convergence.md) says "The byte-exact cumulative guarantee stays intact"; it now describes a normalized-text key. The generic claims that a marker's content is a hash of exactly the reviewed state, in two sentences of `docs/hooks.md` and in the comment on `_lib_marker_value_present`, are qualified in place for `cumulative-review`.

## The one-time `historical`

A marker written before this change reads `historical` once when its diff has a hunk header or a text entry's `index` line, because the live value is now the normalized one and `status` matches by exact content. There is no dual read of the old value: accepting both recipes would keep the raw recipe on the cumulative path and need a later removal, all to save one review per in-flight branch. A diff with neither, such as a pure-rename PR, normalizes to itself, so its old marker stays `live`. For the diffs that do change, the one-time `historical` also ends the first disclosure in [cumulative-review-marker-not-recomputed.md](cumulative-review-marker-not-recomputed.md), that an unswept unearned marker stays `live` forever.

## Sources

- `.claude/plans/cumulative-hash-patch-id.md` — full assumption ledger, mechanism list, and out-of-scope residuals.
