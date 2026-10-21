# Cumulative-review cache: key on normalized diff text, not raw diff bytes

## Context

Goal: make the `cumulative-review` marker survive a rebase that only shifts hunk line numbers, by hashing the PR diff with its hunk headers and text-entry `index` lines normalized instead of hashing the raw diff bytes.

Ask: "sure, sounds good." — the engineer's reply to the session's offer: "I'd start with items 1 and 3, the reviewer admission bar and the patch-id hash. Both are small and tied to open issues, and neither adds runtime cost. Want me to plan either one?" Item 3 in the session's wording: "Use `git patch-id --stable` for the cumulative-review cache ... A rebase that only shifts line numbers therefore misses the cache." Item 1 is planned on a separate branch. When the pre-implementation probe showed the `patch-id` design hashes less than its input, the engineer said: "Ask the architect. We're straying from the original design". After the architect recommended a sha256 over normalized diff text instead, the engineer said: "got it yes that makes total sense."

Why now: `docs/design-decisions/ready-for-review-cumulative-diff-cache.md` built this cache for the rebase-onto-moved-default case. Its preimage, though, includes every `@@ -a,b +c,d @@` hunk header and every `index <old>..<new>` line. A rebase onto a default branch that added or removed lines above a hunk in a file the PR also touches shifts those numbers, so the hash misses and a full specialist review re-runs over unchanged content. This advances #1207, the umbrella issue for review re-run cost, which folds in #1196 and #1057.

Evidence behind the foundation switch (git 2.56.0). These are the dispatching session's reported probe results, relayed and not re-run here (row 7 tags the second one `[unverified]`; the implementer re-runs it in Verification 3):
- Identical change and context with only shifted hunk line numbers: the raw sha256 differs, while `git patch-id --verbatim` and `--stable` ids match. That motivated the original `patch-id` design.
- Under `diff.suppressBlankEmpty=true`, `git patch-id --verbatim` stops hashing at the first empty context line. Two diffs that differ only in a later hunk gave identical ids, and a single-file diff still printed exactly one well-formed id line. A design built on `patch-id` cannot detect that from the call site.
- A re-indent of a changed line leaves the `--stable` patch-id unchanged, so `--stable` would treat a whitespace-only edit as already reviewed. That is why the session's original `--stable` wording was already replaced by `--verbatim` before the switch.

## Approach

The `cumulative-review` marker becomes the sha256 of the diff text after two normalization rules. Each hunk header keeps its two line counts and loses its start offsets and its function-context TEXT, so `@@ -12,3 +12,4 @@ TEXT` becomes `@@ -,3 +,4 @@` and an omitted count stays omitted (`@@ -5 +5 @@` becomes `@@ - + @@`). Each `index` line that a `--- ` line directly follows is dropped. Every other byte reaches sha256 unchanged, including every `+`, `-` and context line, path line, mode line, and binary `index` line.

Counts are invariant under the pure line-shift rebase this change targets. A hunk's old count is its context-plus-deleted line total and its new count is its context-plus-added line total, so the counts differ only when the body differs. So a rebase that only shifts hunk start offsets or text files' blob ids keeps the marker `live`. What else shares a value is listed in the decision file `cumulative-review-marker-keyed-on-normalized-diff.md` (created below), which is the one canonical statement of the equivalence class.

This revision replaces the `git patch-id --verbatim` design. That design's git 2.39 floor, its `--verbatim` flag, its `_lib_capped` wrapper, and its one-line and id-shape checks are all gone. README's git floor stays 2.31 (row 29). The engineer's own words cover the foundation switch, the `--no-color --no-ext-diff` flags, the base-side widening, and the PR-side relocation residual (rows 3, 4, 5, 36). The plan classifies function-context TEXT as a base-side edit when only TEXT differs (row 5's widening) and as part of a PR-side move when the edit relocates (row 36's second quote); the architect consult this session recommended dropping it after the engineer asked for the correct design (row 41). The other residuals (the unchanged mode on a dropped `index` line, abbreviated binary blob ids, textconv) rest on this plan's own judgment, with no engineer quote. Keeping the hunk counts closes the trailing-blank-context collision that dropping them would have left (row 38).

**The handed-over design, reviewed.** It is endorsed (rows 3, 7–19), with five corrections:
- `_lib_hash_diff_text` takes its text as an argument, not on stdin (`_lib.sh:1199-1201`).
  - The value helper therefore captures the normalized text and passes it as an argument.
  - The normalizer becomes its own function, `_lib_normalize_cumulative_review_text`. Tests can then compare its output to explicit expected text instead of only to opaque digests.
- The `index` rule matches git's full `index <hash>..<hash>[ <mode>]` grammar rather than a bare prefix, which makes it as strict as the `@@` rule (M2).
- The "normalized-text equivalence key" statement and the residual list go in a new decision file, and `ready-for-review-cumulative-diff-cache.md` gets only a supersession note. This is because `.claude/rules/design-decisions.md` "Supersession" keeps an overturned decision's body in place (row 25).
- Not every existing marker reads `historical` once. A diff with no hunk header and no dropped `index` line, such as a pure-rename PR, normalizes to itself, so its old marker correctly stays `live` (row 21). The §50 note says so.
- `marker.sh`'s write-failure message (`:694`) and its could-not-verify message (`:1021`) stay unchanged. An awk failure belongs to the same tool-misbehavior class as a sha256sum failure, which neither message names today (row 18).

### Assumption ledger

**Root:** The `cumulative-review` value is the sha256 of `pr-diff-against-base.sh`'s raw text, and that text includes every `@@ -k,l +n,m @@ TEXT` hunk header and every `index <hash>..<hash>` line. So when a PR is rebased conflict-free onto a base that edited a file the PR touches, outside the hunk's context window, the cache misses over an unchanged change and a full specialist review re-runs.

**Givens** (fixed beyond this design's reach):
- **G1.** The recorded-subject protocol: `write` hashes the text that `--record` captured at step-3 entry (`cumulative-review-marker-not-recomputed.md:7`). So the value must be computed from diff text, not from a ref range. Reason: §50 is its own decision, and reopening it needs a decision outside this plan.
- **G2.** git owns its diff output grammar. The plan relies on the line classes git documents (rows 9–10) and pins the undocumented prefix and adjacency facts in tests (rows 11–13). Reason: the vendor defines it.

**Rows:**
1. `[engineer-verified: "sure, sounds good."]` The engineer accepted the session's offer to plan item 3, a cumulative-review cache that survives a line-shift rebase. Read against "Want me to plan either one?", the quote covers planning that item only. It does not cover the offer's `--stable` wording or any detail below.
2. `[engineer-verified: "Ask the architect. We're straying from the original design"]` Asked which foundation to use, the engineer handed the choice to an architect consult.
3. `[engineer-verified: "got it yes that makes total sense."]` This is the engineer's reply to the consult's recommendation: a sha256 over normalized diff text in place of patch-id. Read against that recommendation, the quote covers the foundation switch. The rule details below are this plan's own.
4. `[engineer-verified: "Include it (Recommended)"]` The engineer chose to add `--no-color --no-ext-diff` to `pr-diff-against-base.sh`'s merge-base `git diff`.
5. `[engineer-verified: "Accept the widening (Recommended)"]` The engineer accepted that a clean review also covers a rebase in which the base edited the same file outside the hunk's context window. The quote covers base-side edits only. A PR-side relocation is row 36. `[engineer-verified: "Accept the lost guarantee (Recommended)"]` Asked again with the lost-guarantee framing (before, any base edit to a PR-touched file missed; now only an edit inside the ±3-line window does, including a callee in the same file), the engineer selected this label.
6. `[unverified]` These are the session's and the architect's wording, not the engineer's:
   - the option descriptions behind rows 4–5;
   - the consult's judgment that rows 4–5 still stand under the new foundation.
7. `[unverified: the dispatching session's probe on git 2.56.0 (two files, each with blank context lines, an early-hunk change and a late-hunk change; diffed under `diff.suppressBlankEmpty=true`). The probe script was read, but its output was relayed and not re-run. The implementer re-runs it on the local git and cites that output before the commit message and decision file state the result.]` Under `diff.suppressBlankEmpty=true`, `git patch-id --verbatim` gave identical ids for two diffs that differed only in later hunks. A single-file diff still printed one well-formed id line. So patch-id can return a valid-looking id over input it hashed only in part.
8. `[verified: _lib.sh:1201, :1225; marker.sh:693]` Both arms hash the raw text through `_lib_hash_diff_text`.
9. `[verified: gitattributes.adoc:827-834]` "Each group of changes (called a "hunk") in the textual diff output is prefixed with a line of the form: `@@ -k,l +n,m @@ TEXT`". "The "TEXT" portion is by default a line that begins with an alphabet, an underscore or a dollar sign". `[verified: user-manual.adoc:3631]` `@@ -1 +1 @@` shows that git omits a count of 1, so the rule makes both counts optional.
10. `[verified: diff-generate-patch.adoc:32-45, :59-61]` `index <hash>..<hash> <mode>` is an extended header line. "The index line includes the blob object names before and after the change. The _<mode>_ is included if the file mode does not change; otherwise, separate lines indicate the old and the new mode."
11. `[verified: git-diff.html:2547-2549]` `diff.suppressBlankEmpty` is "A boolean to inhibit the standard behavior of printing a space before each empty output line", so an empty context line can print as an empty line. `[unverified]` Apart from that case, every hunk body line starts with a space, `+`, `-` or `\`. If so, no body line can match either column-0 rule. Case (q) and the shape pins test this.
12. `[unverified]` A `--- ` line directly follows an `index` line exactly when the entry has a text hunk body:
    - That holds for text edits, new and deleted text files, a mode change with a content change, and symlinks.
    - For a plain binary entry, `Binary files … differ` follows instead.
    - For an empty-file creation or deletion, the next `diff --git` line or the end of input follows.
    - Mode-only and 100%-rename entries carry no `index` line at all.

    `TestGitDiffEntryShapes` pins all of this, and it is a stop condition (Verification 1). `[verified: user-manual.adoc:3628-3631]` A gitlink entry follows the same shape: `index d266b98..261dfac 160000` and then `--- a/a`.
13. `[verified: diff-options.adoc:838-847]` "textconv filters are enabled by default only for linkgit:git-diff[1] and linkgit:git-log[1]". `[unverified]` A binary rendered through textconv prints a text body headed by `--- `. Its `index` line then drops, and its textconv output alone identifies it. The textconv shape pin settles this.
14. `[unverified]` The session proposed accepting the textconv residual, and this plan endorses it. The reviewer reads the same textconv text the marker covers, because the `--diff-file` artifact is this script's own output (`pr-diff-against-base.sh:147-163`). So the marker still attests what was reviewed.
15. `[verified: diff-generate-patch.adoc:79-80]` "Hunk headers mention the name of the function to which the hunk applies." TEXT is a heuristic pick that names the enclosing function. It is not hunk content.
    - `[unverified]` In markdown or YAML, most lines start with a letter. So TEXT is often the line just above the context window, which a base edit can change.
    - Dropping TEXT keeps that rebase a hit, and case (p) pins the resulting residual.
16. `[verified: _lib.sh:1195-1197, :3443]` `_lib_hash_diff_text` runs uncapped because "it's an in-memory pipe to sha256sum that won't hang". `_lib_mask_shell_quotes` is capped "because the scan is O(n^2)". The normalizer is a single linear pass over an in-memory pipe, so the uncapped reasoning covers it.
17. Which awk runs the normalizer:
    - `[verified: grep of _lib.sh]` `_lib.sh` already calls awk at `:1159, :1201, :1587, :1800, :1950, :1968, :2544, :2545, :2900, :3470`.
    - `[verified: grep of README.md for awk, no match]` README names no awk requirement, so the helper runs on whichever awk comes first on PATH.
    - `[verified: tests.yml:25]` CI runs only on `ubuntu-24.04`.
    - `[verified: _lib.sh:3455; docs/hooks.md:104]` BSD/macOS awk already diverges from other awks once (it treats `RS = "\0"` as paragraph mode), so portability is pinned per implementation, not assumed.
    - `[unverified: reviewers' runs, relayed and not re-run]` BSD awk 20200816 accepts the regexes and passes a 5,000,000-byte line. Without the `LC_ALL=C` pin it exits 2 with partial output on invalid UTF-8 under a UTF-8 locale. mawk and gawk are unrun.
    - `[unverified]` Which awk `ubuntu-24.04` resolves is unknown. The awk-matrix test names each case by the implementation it resolved to. It fails on CI for `/usr/bin/awk` and for any awk the workflow installs, and none is installed, so there is no ratchet on the runner's awk set beyond `/usr/bin/awk` (case (v), Verification 7).
18. `[verified: _lib.sh:1131-1137]` `marker.sh` sources `_lib.sh` under `set -u` with no `pipefail`. There, "a failed `sha256sum` there still leaves `awk` exiting 0 with empty output, which the emptiness check ... is what catches." So the value helper checks both awk's exit status and that its output is non-empty.
19. `[verified: _lib.sh:1223-1224; marker.sh:679-688]` Both arms strip trailing newlines through `$( )` and refuse empty text before hashing. So both arms give the normalizer identical bytes.
20. `[verified: git grep -n '_lib_hash_diff_text' -- claude/, code lines only: _lib.sh:649, :1170, :1634, :4127, :4198; marker.sh:954, :1078; require-code-review.sh:157; _review-ledger-lib.sh:338, :349; review-pr-post.sh:147]` `_lib_hash_diff_text` has 11 other call sites, and each keeps raw sha256 semantics. Its body stays unchanged.
21. `[verified: marker.sh:259-270; _lib.sh:695-718]` `status` matches a marker by exact content. So an old raw-sha256 marker reads `historical` when normalization changes its diff's text. It stays `live` when normalization does not, and that is correct, because equal values mean equal normalized text.
22. `[engineer-verified: "Accept the one-time miss (Recommended)"]` Asked whether existing markers whose diff has a hunk may read `historical` once, with no dual read of the old raw sha256, the engineer selected this label.
23. `[verified: cumulative-review-marker-not-recomputed.md:13]` §50's first disclosure says that an unswept unearned marker "stays `live`" forever. Row 21 ends that for every diff with a hunk header or a dropped `index` line.
24. `[verified: ready-for-review-cumulative-diff-cache.md:9, :11]` §44 accepted, as a named residual, that "a reviewer's ripple and causal-reach judgment can depend on code outside the diff", with the verification cache and CI as compensation. Row 5's widening keeps the same compensation, but it is a change of guarantee, not only the same residual class at closer range. Before this change any base edit to a file the PR touches missed, because the post-image blob id in that file's `index` line changed. After it, a base edit outside the effective ±3-line window keeps `live`, including a callee defined in the same file. A PR-side relocation (row 36) is a different class, because the code in question is the PR's own.
25. `[verified: .claude/rules/design-decisions.md "Supersession"; merge-tree-base-recipe-for-gate-diff-base.md:5; declined-sessionstart-additionalcontext-injection.md:5]` An overturned decision gets an in-place note under its provenance line. The precedent forms are "Superseded in part by" and "Partially superseded by".
26. `[verified: ready-for-review/SKILL.md:15, :68]` The skill makes two claims:
    - after a fix, "its cache marker misses on the changed bytes";
    - `live` means "this diff content already passed".

    Both stay true for a fix that changes a body, path, mode or rename line. They do not hold for a fix that only relocates an edit to a site with identical context (row 36), or that changes only function-context TEXT or only a text entry's blob ids: its marker stays `live`. §50's mistimed-write protection, that the recorded subject and the live diff disagree, weakens the same way. The skill's wording is not changed (Out of scope), and the decision file says it is approximate.
27. `[verified: pr-diff-against-base.sh:84, :60; docs/scripts.md:168]` The merge-base arm runs bare `git diff`. The `--staged` arm's `git diff --cached` is "the same invocation `marker.sh`'s `write code-review` arm hashes".
28. `[verified: diff-options.adoc:353-358, :835-836]` `--no-color` "can be used to override configuration settings. It is the same as `--color=never`." `--no-ext-diff`: "Disallow external diff drivers." `[unverified: a session probe under `--color=always`, relayed and not re-run; Verification 2's red run of `test_merge_base_diff_ignores_color_and_external_diff_config` is what shows it]` Under `--color=always` the output carries ANSI escapes. So neither rule matches a colored header, the value is still computed, and every rebase misses. Case (s) pins only the normalizer's side: hand-written colored text is not matched, the value is still computed, and every rebase would miss.
29. `[verified: README.md:97]` The git floor is 2.31, for `--path-format=absolute`. The normalizer calls no git, so the floor does not move. `[engineer-verified: "Covered by my reply"]` Asked whether "got it yes that makes total sense." covers dropping the patch-id design's 2.39 floor and `--verbatim`, the engineer selected this label.
30. `[verified: test_marker_script.py:380-390, :4298-4328]` The context-drift test inserts `L9.5` inside the context window, which changes a context line. Context lines pass through unchanged, so that test still reads `historical` with its assertions unchanged.
31. `[verified: grep for "@@" in claude/.claude/**/*.sh, no match]` No shipped shell file contains `@@` today. So a test awk shim that fires only on an argument containing `@@` targets only the normalizer.
32. `[verified: test_marker_script.py:3831-3833; _lib.sh:1201]` The value stays a 64-hex sha256, so the check at `:3831` and its message stay unchanged.
33. `[verified: .claude/rules/bash-unit-test-seams.md; marker.sh:8]` A new helper goes in the existing lib whose domain covers it, and `marker.sh` sources `_lib.sh`.
34. `[verified: CLAUDE.md "Ground every choice", hand-rolled-logic bullet]` Hand-rolled parsing in a non-trivial domain needs a commit-message justification for why no standard alternative fits.
35. `[unverified: from recollection, not checked here]` Linux caps a single exec argument at 128 KiB (MAX_ARG_STRLEN). So the value tests pass text through a file read with `$(cat ...)`, as `marker.sh:679` does, not as one `bash -c` argument.
36. `[engineer-verified: "Accept as a named residual (Recommended)"]` Asked whether a relocated line (the same added line moved to a site with identical ±3-line context) may keep a prior clean review, the engineer selected this label. It covers PR-side relocation of an added line only. It does not cover the unchanged mode, abbreviated binary blob ids, or textconv.
    - `[engineer-verified: "I think any moved edit, any TEXT, but have the architect review that decision"]` Asked which relocation scope the plan should state as accepted, the engineer typed this. It widens the acceptance to any moved edit (an addition, a deletion or a replacement) and to a move across functions. `[unverified: the architect consult's account, relayed]` The architect consult then recommended accepting it: TEXT does not track scope reliably, keeping it costs cache hits, and the same compensations cover both kinds of move. Those reasons are the architect's, not the engineer's.
37. `[unverified: a reviewer's probe on real git diff output, relayed and not re-run]` Moving one added line between two stanzas with identical ±3-line context gave equal normalized values and different raw sha256 values. The exposure needs a repeated window: identical context over the effective context width (3 by default), with TEXT irrelevant because the header rule drops it. The moved edit must also keep its position relative to the file's other PR hunks, because the normalized text keeps hunk order. No normalization can keep a hunk's site and still survive a line-shifting rebase.
    - Compensation for a relocation: the commit that makes the move is a changed staged diff, so the commit-time `/code-review` gate reviews it (`require-code-review.sh`), and that gate does not cover a move made while resolving conflicts in `git rebase --continue` or a commit outside a gated session. The verification cache re-runs in opted-in repos, CI runs on every push, and a human reviews the PR. Do not reuse §44's "bytes nobody changed" reasoning, because a relocation changes the PR's own bytes.
    - A conflict-free rebase whose automatic merge lands a hunk at a different identical-window site runs no commit-time gate, because no commit is made. `[unverified]` Whether git produces such a case is unconfirmed. The implementer's re-run of this row's probe, extended with a conflict-free rebase of the moved-edit pair, settles it. The extension tries each git integration path: the default merge-backend `rebase`, `rebase --apply`, `am` and `cherry-pick`. The decision file states the outcome per path, never a blanket "git does not produce such a case". A positive result is recorded in the decision file and is not a stop condition, because row 36's "any moved edit" already covers it.
38. `[unverified: a reviewer's probe, relayed and not re-run]` Under `diff.suppressBlankEmpty=true`, two diffs that differ only in the count of trailing blank context lines at the end of input (2 against 0) would collide if the header counts were dropped: `$( )` strips the trailing newlines and nothing else records them. This is the one place a context line does not otherwise pass through unchanged.
    - So the normalizer keeps both counts and drops only the start offsets. The pair then differs in its counts, and case (z) pins that.
    - Dropping the counts buys nothing the targeted rebase needs, because counts are a function of the body (Approach). It would only reopen this collision.
39. `[unverified: a reviewer's finding, relayed]` A binary entry is identified by git's abbreviated `index` blob id (7 hex digits by default, 4 at minimum under `core.abbrev`). So a deliberate collision is possible only by grinding, and the plan does not claim collision resistance for binary entries. Adding `--full-index` to the line already being edited would close it. It is left out of scope because it changes the text reviewers read and no engineer quote asks for it.
40. `[engineer-verified: "Keep it"]` Asked whether the one `tests.yml` log line (resolved `awk` and its version), which no earlier quote calls for, stays in scope, the engineer selected this label.
41. `[engineer-verified: "I'm not as interested in what I originally said here as opposed to what is the correct design. I want to know what the correct design is. What does the architect think"]` Asked whether "any TEXT" covers only a moved edit or also the general TEXT residual, the engineer asked for the correct design. `[unverified: the architect consult's own account, relayed; it ran no git and measured no miss rate]` The architect recommended dropping TEXT:
    - TEXT is base content outside the window, so a TEXT-only difference is a base-side edit already inside row 5's accepted widening.
    - Keeping TEXT would win back a heuristic-shaped slice of that guarantee and add misses on the targeted rebase, for example a parallel edit to the neighboring prose line in markdown.
    - The false-live exposure it would close is a repeated window under different TEXT moved during conflict resolution; moves under the same TEXT collide either way.
    - A mass miss would also follow any change to funcname selection, such as a `diff=` attribute or `core.attributesFile`.

**Mechanisms:**

**M1. Two helpers in `_lib.sh`, directly after `_lib_hash_diff_text`, called by both arms.** `anchors: root, row3, row7, row8, row9, row10, row19, row33`. `_lib_normalize_cumulative_review_text TEXT` prints the normalized text. `_lib_cumulative_review_value TEXT` hashes that text through `_lib_hash_diff_text`. A hand-written filter is more mechanism than the raw hash, so lighter options were checked:
- (a) Raw sha256, unchanged. This fails the root.
- (b) `git patch-id --verbatim`. It can hash only part of its input and still print one valid id (row 7). Pinning `diff.suppressBlankEmpty=false` on the merge-base diff would close only the one trigger the probe found. Patch-id's failure mode is a well-formed id over partially hashed input, so the call site cannot detect any trigger nobody has probed.
- (c) A `sed` filter. The `index` lookahead needs the `N`/`P`/`D` commands. `[unverified: GNU sed manual, from recollection]` GNU and POSIX sed disagree on `N` at the last line, which is exactly where an empty file's kept `index` line can sit (case (t)).
- (d) A bash `read` loop with `[[ =~ ]]`. It would work, but its per-line regex runs on every `status` call `[unverified: slower than one awk pass]`, and `_lib.sh` already filters text through awk (row 17).
- (e) Python. `.claude/rules/shell-script-conventions.md` reserves embedded Python for syscalls bash cannot express, and a `.py` file would add an interpreter start on every `status`.

**M2. Strict, column-0 regexes, and the `index` lookahead keys on `--- `.** `anchors: row9, row10, row11, row12`. Two alternatives were set aside:
- Bare-prefix matching (`^@@ `, `^index `) would also never touch a body line (row 11). But it would rewrite a header that a future git prints in another form. A strict match leaves such a line in the preimage, which errs toward a miss.
- Dropping every `index` line would make two different binary edits collide, because that line is a binary entry's only content identity (row 10, case (k)).

Keying the lookahead on `--- `, not on `Binary files`, means any line the filter does not recognize keeps its `index` line in the hash.

The header rule ends where git's grammar ends: the closing `@@` followed by end of line or a space. A combined-diff header (`@@@ ... @@@`), a glued tail (`@@ -1 +1 @@x`), an `index` line with an extra field, and uppercase hex all stay in the preimage with their digits (case (ae)). The `index` rule is already `$`-anchored. Both are written with bracket expressions and no `{n}` intervals.

**M3. Uncapped, with `LC_ALL=C` on awk only.** `anchors: row16, row17`. Capping is heavier and buys nothing, because row 16's exemption applies. If awk inherited the ambient locale instead, BSD awk exits 2 with partial output on invalid UTF-8 under a UTF-8 locale (row 17), so the pin keeps non-UTF-8 diffs working on macOS. Case (r) needs byte semantics. Its real-awk discrimination comes from a locale-sensitive awk, which is macOS BSD awk only and so runs on contributor machines, not CI. CI observes the pin through case (ad)'s environment check. An unpinned failure exits nonzero, so it fails closed: it is an availability control, not a false-live guard.

**M4. `--no-color --no-ext-diff` on `pr-diff-against-base.sh:84` only.** `anchors: row4, row27, row28`. Two lighter options were set aside:
- (a) Leaving `git diff` bare would still be safe. But `color.ui=always` users would get no rebase tolerance, and `diff.external` users would review text that is not a patch. The engineer chose to include the flags (row 4).
- (b) Stripping ANSI in the normalizer is heavier, and the reviewer would still read colored text.

The `--staged` arm stays bare (row 27).

**M5. No dual read of old raw-sha256 values.** `anchors: row21, row22, row23`. Accepting both recipes in `status` is the heavier option. It would keep the raw recipe on the cumulative path and need a later removal, all to save one review per in-flight branch.

**M6. Docs.** `anchors: row5, row14, row15, row21, row22, row23, row24, row25, row26, row36, row37, row38, row39`.
- A new decision file.
- "Superseded in part" notes on §44, §50 and `ready-for-review-fix-loop-convergence.md`.
- One phrase replacement and two appended sentences in `docs/hooks.md`.
- A CHANGELOG entry.
- `ready-for-review/SKILL.md` stays out of the diff (row 26).

## Critical files

**One `code-writer` dispatch, not split.** The helpers, both callers, the test files, and the docs all rest on one contract: the two rules and the residual list. The decision file and the CHANGELOG must state exactly what the code does. Splitting would restate that contract in every prompt and let two agents word the residuals differently. Order inside the dispatch:
1. The shape pins, run before any code change (Verification 1). `isolated_git_diff` in `claude/.claude/tests/helpers.py` is part of this step.
2. The two red tests (Verification 2).
3. The helpers and callers.
4. The value tests.
5. The row 7 and row 37 probes (row 37 extended with a conflict-free rebase of the moved-edit pair), run on the local git, with their output cited before the docs step.
6. The docs and CHANGELOG.

**Create:**
- `docs/design-decisions/cumulative-review-marker-keyed-on-normalized-diff.md`
  - H1, then `*<implementation commit date>.*`, with no `Formerly` clause. All three supersession notes below use the same date.
  - Body, using markdown links:
    - The problem: the raw preimage includes hunk numbers, function-context TEXT, and blob ids.
    - The decision:
      - The value is the sha256 of the text that `_lib_normalize_cumulative_review_text` prints, computed through `_lib_cumulative_review_value`.
      - State the two rules: a hunk header keeps its line counts and loses its start offsets and TEXT, and a text entry's `index` line is dropped.
      - State that every other byte passes through unchanged.
      - The recorded-subject protocol is unchanged; link `cumulative-review-marker-not-recomputed.md`.
    - The property: the marker is a normalized-text equivalence key. This file is the one canonical statement of the equivalence class. The `_lib.sh` comments, the commit message, the CHANGELOG entry and `docs/hooks.md` name this file and do not restate the class.
      - sha256 hashes the normalized text. Binary entries are identified by git's abbreviated blob ids (row 39), so the key makes no collision-resistance claim for them. The raw preimage carried the same abbreviated binary `index` line, so this is not a weakening.
      - The rules never rewrite a body line, so two diffs share a value only when they differ solely in hunk start offsets, in function-context TEXT, in a text entry's `index` line, or in the site of a hunk with identical context (row 37).
      - Hunk line counts stay in the preimage, so trailing blank context under `diff.suppressBlankEmpty` is not a residual (row 38).
      - A header the rules do not recognize stays in the preimage, which causes a miss, not a false `live`. Recognized means the closing `@@` followed by end of line or a space. A combined-diff `@@@` header, a glued tail, an `index` line with an extra field, and uppercase hex are not recognized.
      - The key has no age bound and matches tree-wide and branch-agnostically, so a reapply or backport of an already-reviewed patch in the same checkout can read `live`. Cite `docs/hooks.md` (markers match on content across every session suffix under the repo-hash) and `_lib.sh`'s note that completion markers are never pruned, and restate neither. The compensation is row 24's: the verification cache, CI and human review.
    - Why not patch-id: describe row 7's probe shape inline (two files with blank context lines, changes in an early and a late hunk) and its result on git 2.56.0. Do not rely on the `/tmp` path.
    - The base-side widening from row 24: link `ready-for-review-cumulative-diff-cache.md` and state it as a change of guarantee. Before this change any base edit to a file the PR touches missed, because the post-image blob id sits in that file's `index` line. After it, a base edit outside the effective ±3-line window keeps `live`, including a callee defined in the same file. It is not only the same residual class at closer range. The compensation (verification cache, CI) is unchanged.
    - Residuals, split by provenance: only base-side widening and PR-side relocation read as engineer-accepted (rows 5, 36). Function-context TEXT is the plan's own classification, as base content outside the window, made on the architect's recommendation after the engineer asked for the correct design (row 41). The unchanged mode, abbreviated binary blob ids and textconv rest on the plan's own judgment and say so. Cite each pin by its test name (scenario and outcome). The row and case references in the bullets below are anchors for the implementer and do not appear in the file, because they do not survive the PR. The abbreviated-blob-id residual is unpinned by design, since only grinding could exercise it. The relocation pin lives in `test_marker_script.py`; the others live in `TestLibCumulativeReviewValue`:
      - Function-context TEXT after `@@` is not hashed (row 15, case (p)).
      - The unchanged mode on a dropped `index` line is not hashed (row 10, case (t)).
      - A PR-side relocation: any moved edit, including a move across functions, keeps its value when it lands at a site with identical context over the effective width (rows 36–37, cases (p) and (ac)). The worst case is a move to the wrong copy of duplicated code. This is separate from the base-side widening, and the exposure needs a repeated window. The commit-time `/code-review` gate does not cover a conflict-free rebase whose automatic merge lands a hunk at a different identical-window site. State the outcome of the implementer's probe re-run (row 37) for each integration path tried (default merge-backend `rebase`, `rebase --apply`, `am`, `cherry-pick`), never as a blanket "git does not produce such a case".
      - Abbreviated binary blob ids (row 39). The raw preimage carried the same abbreviated binary `index` line, so this is not a weakening.
      - Textconv: a textconv-rendered binary is identified by its textconv output, not its blob ids (rows 13–14, textconv shape pin). If that pin showed `Binary files`, say this residual does not arise instead.
    - What is deliberately not normalized, each only able to cause a miss: color, `similarity index`, and diff config such as the algorithm. Context width is not in this list: the relocation residual grows as the effective context width (`diff.context`, `-U<n>`, `GIT_DIFF_OPTS`) shrinks below 3, and at 0 the same added line at any position in a file collides. `pr-diff-against-base.sh` passes none of them, and the plan does not pin the width, matching how `diff.suppressBlankEmpty` is handled: config-gated, named and pinned in tests. Adding `-U3` alone would not pin it, because `GIT_DIFF_OPTS` takes precedence over `-U`.
    - Portability: the filter runs on whichever awk comes first on PATH and avoids interval expressions. The matrix test runs every awk it finds. The `LC_ALL=C` pin is checked against a real locale-sensitive awk only on macOS contributor machines, and on CI through an environment-asserting shim. If `write cumulative-review` refuses with awk's own error on stderr, put an awk that passes the matrix first on `PATH`, or revert.
    - Rollback: both arms reach the recipe through `_lib_cumulative_review_value`, which is the single seam. Revert the commit as a unit, or make that function hash its argument directly through `_lib_hash_diff_text`; the oracle tests that encode the normalized recipe move with that change. Never revert one arm alone, because `write` and `status` would then disagree and every marker whose diff has a hunk header or a dropped `index` line would read `historical`. Either rollback re-keys every marker written under the normalized recipe whose diff has a hunk header or a dropped `index` line, so each such in-flight branch pays a second full review. A diff that normalizes to itself is unaffected (row 21).
    - Consumer premise: the weakened key is tolerable because no hook reads the `cumulative-review` marker; cite the sentence in `docs/hooks.md` that says so, which is the premise itself, and state that any gate adopting it needs a threat-model tier review. A permanent test in `test_hook_alignment.py` is a tripwire on direct references: it fails if any hook references `_lib_cumulative_review_value` or `_lib_cumulative_diff_hash`. It does not detect a hook that shells out to `marker.sh status` or reads `cumulative-review-markers/`, because neither names either helper, and it does not detect a future `_lib.sh` wrapper, because `_all_hook_files()` excludes `_lib.sh`.
    - Weakened claims elsewhere: `ready-for-review/SKILL.md` says a fix "misses on the changed bytes" and that `live` means "this diff content already passed". Both are now approximate for the residuals above, and §50's mistimed-write check weakens the same way (row 26). `ready-for-review-fix-loop-convergence.md` says "The byte-exact cumulative guarantee stays intact"; it gets its own supersession note (below).
    - The one-time `historical`:
      - it applies to every diff with a hunk header or a dropped `index` line;
      - it ends §50's first disclosure for those diffs.
    - Cite, rather than restate, any rule the rationale rests on (`.claude/rules/design-decisions.md` "Cite, don't restate").
  - `## Sources` names `.claude/plans/cumulative-hash-patch-id.md`.

**Modify:**
- `claude/.claude/hooks/_lib.sh`
  - Insert after `:1204`, before `_lib_cumulative_diff_hash`:
    ```bash
    _lib_normalize_cumulative_review_text() {
      printf '%s\n' "$1" | LC_ALL=C awk '
        held_index_line != "" {
          if (substr($0, 1, 4) != "--- ") print held_index_line
          held_index_line = ""
        }
        /^index [0-9a-f]+[.][.][0-9a-f]+( [0-7]+)?$/ { held_index_line = $0; next }
        match($0, /^@@ -[0-9]+(,[0-9]+)? [+][0-9]+(,[0-9]+)? @@/) &&
            (RLENGTH == length($0) || substr($0, RLENGTH + 1, 1) == " ") {
          split($0, header_fields, " ")
          old_count = header_fields[2]; sub(/^-[0-9]+/, "", old_count)
          new_count = header_fields[3]; sub(/^[+][0-9]+/, "", new_count)
          print "@@ -" old_count " +" new_count " @@"
          next
        }
        { print }
        END { if (held_index_line != "") print held_index_line }
      '
    }

    _lib_cumulative_review_value() {
      local normalized_text
      normalized_text=$(_lib_normalize_cumulative_review_text "$1") || return 1
      [ -n "$normalized_text" ] || return 1
      _lib_hash_diff_text "$normalized_text"
    }
    ```
    - Use bracket expressions (`[.]`, `[+]`), not backslash escapes, and no `{n}` intervals.
    - The header rule ends at the closing `@@` followed by end of line or a space. It checks that boundary with `RLENGTH` and `substr`, not an in-regex `( |$)` alternation. `[unverified]` The matrix test (case (v)) is what shows the construct runs on every awk found.
    - A header's counts keep their literal text, comma included, so an omitted count prints as nothing: `@@ -5 +5 @@` gives `@@ - + @@`, `-1,0 +2` gives `@@ -,0 + @@`, and a new-file `-0,0 +1,3` gives `@@ -,0 +,3 @@`.
    - No SC2016 suppression is expected. `[unverified: a reviewer ran the repo-pinned shellcheck 0.11.0 clean on verbatim copies of both helpers, relayed]` If lint flags it, add the disable with a one-line rationale per CLAUDE.md "Ground every choice".
  - Header comments, one fact per line:
    - Normalizer:
      - It reduces each hunk header to its two line counts (`@@ -,b +,d @@`) and drops each `index` line that a `--- ` line follows.
      - Every other line passes through unchanged.
      - Cite the new decision file.
      - It avoids interval expressions so BSD awk and mawk accept it.
      - It is uncapped for the same reason as `_lib_hash_diff_text`.
    - Value:
      - It prints the cumulative-review marker value, the sha256 of TEXT's normalized form.
      - `marker.sh`'s `write cumulative-review` arm and `_lib_cumulative_diff_hash` both call it.
      - Exit 1 with empty stdout means TEXT is empty, or awk or sha256sum failed.
      - The key is not content equality: `cumulative-review-marker-keyed-on-normalized-diff.md` lists what else shares a value.
      - It must not back a hook-enforced marker without a threat-model tier review.
  - `_lib_hash_diff_text`'s header (`:1182-1185`):
    - It becomes: hashes TEXT with sha256, and `_lib_cumulative_review_value` below hashes the normalized cumulative-review text through it.
    - Keep the review-ledger consumer note at `:1186-1188`.
    - Leave the body unchanged (row 20).
  - `_lib_cumulative_diff_hash`:
    - `:1209-1210`: replace the function name `_lib_hash_diff_text` with `_lib_cumulative_review_value`.
    - `:1213`: "the sha256 hex digest of the diff" becomes "the `_lib_cumulative_review_value` of the diff".
    - `:1214`: the exit-1 list gains "or its value could not be computed".
    - `:1225` becomes `_lib_cumulative_review_value "$diff_output"`.
  - **Reuse:** `_lib_hash_diff_text`.
- `claude/.claude/scripts/marker.sh`
  - `:693` becomes `MARKER_VALUE=$(_lib_cumulative_review_value "$SUBJECT_TEXT") || {`. The message and the exit-before-`rm -f` shape stay.
  - `:1002-1003`: the comment says that `_lib_cumulative_diff_hash` computes the value through `_lib_cumulative_review_value`, the same recipe the write arm uses.
  - `:1021` is unchanged (row 18).
- `.github/workflows/tests.yml`: append awk lines to the existing `Report runner core count` step's `run:`, keeping the step name. They print the resolved `awk` and its version banner (Verification 7).
- `claude/.claude/hooks/tests/test_hook_alignment.py`: add one permanent test over `_all_hook_files()` (`:104`, which already excludes `_lib.sh` and spans plugin hooks). It asserts that no hook references `_lib_cumulative_review_value` or `_lib_cumulative_diff_hash`, and it passes today. It is a tripwire on direct references to those two helpers. It does not detect a hook that shells out to `marker.sh status` or reads `cumulative-review-markers/`, because neither names either helper, and it does not detect a future `_lib.sh` wrapper, because `_all_hook_files()` excludes `_lib.sh`. The decision file's consumer premise is the `docs/hooks.md` sentence, not this test.
- `claude/.claude/scripts/pr-diff-against-base.sh`
  - `:84` becomes `git diff --no-color --no-ext-diff "$MERGE_BASE...HEAD"`.
  - Add one comment line above it: the flags keep color and external-diff output out of the patch text. Claim nothing broader.
  - `:60` is unchanged.
- `claude/.claude/tests/helpers.py`: add the shared helpers named under `test_marker_lib.py`. `[unverified: path from a reviewer; confirm the file and its import convention before adding]`
- `claude/.claude/hooks/tests/test_marker_lib.py`
  - Shared helpers live in `claude/.claude/tests/helpers.py`, one copy for all three test files. Confirm the existing import convention there before adding them.
    - `normalize_cumulative_review_text(text: bytes, tmp_path, env_overrides=None)` and `cumulative_review_value(text: bytes, tmp_path, env_overrides=None, shell_prelude="")`. Each writes TEXT to a file and runs the function on `"$(cat "$1")"` (row 35). Each returns `(exit_status, stdout)`, because cases (g), (u) and (ad) assert on both.
      - `shell_prelude` is a string of bash definitions run before the call, so cases (u), (ad) and (v) use these helpers and not a second wrapper copy.
      - `normalize_cumulative_review_text` returns raw newline-terminated stdout. `cumulative_review_value` returns stripped text.
    - `reference_normalized_text(text: bytes) -> bytes`, a pure-Python implementation of the two rules. A test asserts that it reproduces case (t)'s hand-derived expected bytes (trailing newline stripped). It returns the text with trailing newlines stripped, as `$( )` does, so no call site strips it. The end-to-end tests compute their expected value from it, so no expected value comes from the function under test.
    - `isolated_git_diff(repo, *args, config=())` runs under `GIT_CONFIG_GLOBAL=/dev/null` and `GIT_CONFIG_NOSYSTEM=1`, and sets `XDG_CONFIG_HOME` to an empty per-test temp dir in the subprocess env only, so the default `core.attributesFile` no longer reads from `$HOME`. It adds `-c` pairs from `config`, and strips trailing newlines as the callers do. Every real-git diff in `TestGitDiffEntryShapes` and `TestLibCumulativeReviewValue` is generated through it, using `config=` for the textconv and `suppressBlankEmpty` variants. Its docstring states the limits: `GIT_CONFIG_GLOBAL` needs git 2.32 and fixture commits are not isolated.
  - New class `TestGitDiffEntryShapes`. These pins check raw git output in an `init_git_repo` repo, with the diffs generated through `isolated_git_diff`, and call no `_lib.sh` helper:
    - A text edit, a new text file, a deleted text file, a mode change with a content change, and a symlink: the line after `index` starts with `--- `. The mode-change case's `index` line carries no mode.
    - A binary edit with a leading NUL byte, plus a binary creation and a binary deletion: the line after `index` starts with `Binary files `.
    - An empty-file creation and an empty-file deletion, both mid-diff and last: the line after `index` is `diff --git` or the end of input.
    - A mode-only change and a 100% rename: neither entry has a line matching `^index `.
    - A rename with an edit (`similarity index`, `rename from`, `rename to`, then `index`, `---`, `+++` and a hunk): the line after `index` starts with `--- `.
    - Textconv, set via `.git/info/attributes` `*.bin diff=hexdump` and `-c diff.hexdump.textconv='od -A n -t x1'`: the line after `index` starts with `--- `.
    - Across all these shapes: every line between a hunk header and the next `diff --git` starts with a space, `+`, `-`, `\` or `@@`, or is empty under `-c diff.suppressBlankEmpty=true` (row 11).
    - A gitlink bump: the line after `index` starts with `--- ` (row 12).
    - The body-line grammar check also runs over a fixture whose content lines start with `@@ -1 +1 @@`, `index 1..2 100644`, `--- x`, `+++ x` and `diff --git x`, each behind its `+`, `-` or space prefix.
    - These pins call no `_lib.sh` helper. The normalizer is not used until step 3.
  - New class `TestLibCumulativeReviewValue`, with diffs taken from real commits unless the case says hand-written:
    - (a) Lines inserted at least 4 lines above an appended line:
      - first assert that the raw texts, their raw sha256 values, their `index` lines and their `@@` lines all differ, and that the `@@` lines differ in start offsets only (the counts are equal);
      - then assert equal values.
    - (b) Re-indenting the added line, and separately adding trailing whitespace or changing the line ending to CRLF, each give different values.
    - (c) The same hunk in `a.txt` and in `b.txt` gives different values.
    - (d) The same added line with and without a final newline gives different values.
    - (e) Two files where the first ends with `\ No newline at end of file` and only the second varies: different values.
    - (g) Empty text: exit 1, empty stdout.
    - (i) The same content diff with and without a `chmod +x` gives different values.
    - (j) A pure rename, versus the same rename to another path: different values.
    - (k) Two binary edits to one file with different contents give different values. The same blob at another path also gives a different value.
    - (m) A text of about 100 KiB holding one line of about 80 KiB followed by a short line: exit 0, the same value on two runs, and a one-byte change at the last byte of the long line gives a different value.
    - (o) A final file with two hunks where only the last hunk's added line changes: different values, under both the default config and `diff.suppressBlankEmpty=true`. The first hunk holds a blank context line.
    - (p) Residual pin: one real-git relocation pair, the same edit relocated within one file to a site with identical ±3-line context, gives equal values. Case (ac) is the marker-level evidence that git emits such pairs. The variants are hand-derived pairs in the table below, each with equal expected normalized text: different and same function-context TEXT, three repeated stanzas, a `diff.context=0`-shaped pair (the residual grows as the effective context width shrinks), and a moved addition, a moved deletion and a move across functions. The negative pin, moving the edit past another PR hunk, is a pair whose expected normalized texts differ.
    - (q) Hand-written: body lines `+@@ -1 +1 @@`, `-@@ -1 +1 @@`, ` @@ -1 +1 @@`, `+ab@@ -1 +1 @@` (a prefix of three or more characters), `+index 1111111..2222222 100644` and `-index 1111111..2222222 100644` followed directly by a `--- ` body line. Changing a digit inside any one of them gives a different value.
    - (r) A one-byte Latin-1 change (`\xe9` to `\xe8`) on a body line gives a different value. The same change on a hunk-header TEXT line exits 0 with non-empty output and gives an equal value, because the header rule drops TEXT (this is case (p)'s residual at the byte level, and the equality is intended). The same text gives equal values under `LC_ALL=C` and under a UTF-8 locale. Take the UTF-8 locale from `_installed_utf8_locale_names` alone (`test_config_lib.py:39-60`). Do not call `_locale_that_widens_ascii_ranges`, which probes bash bracket ranges, a different property, and skips on macOS. Verification 3 requires (r) to run, not skip, on the local macOS run. Deleting `LC_ALL=C` once must make it fail there.
    - (ad) Environment pin, observable on every platform. A selective shim `awk() { case "$*" in *'@@'*) [ "${LC_ALL-}" = C ] || return 3 ;; esac; command awk "$@"; }` run with a non-`C` ambient `LC_ALL` still gives exit 0 and the expected value. Removing the pin gives exit 1 and empty stdout, so CI covers the pin without a locale-sensitive awk.
    - (s) A hand-written colored rendering of case (a)'s pair (ANSI escapes around the header and body lines):
      - each run exits 0;
      - the two values differ, so there is no tolerance;
      - each differs from its uncolored value.
    - (t) Hand-written oracle. The input holds:
      - a text entry whose `index` line carries a mode;
      - one hunk header with TEXT, one with both counts omitted (`-5 +5`), one with a zero old count and an omitted new count (`-1,0 +2`), and a new-file header (`-0,0 +1,3`);
      - a mid-diff empty-file creation;
      - a binary entry;
      - an empty-file creation as the last entry.

      The normalizer's stdout must equal an explicit expected byte string, with the four headers as `@@ -,3 +,4 @@`-style count-only lines (`@@ - + @@`, `@@ -,0 + @@` and `@@ -,0 +,3 @@` for the three omitted-count and zero-count forms). `_lib_cumulative_review_value` must equal `hashlib.sha256` of that string with its trailing newline stripped.
    - (u) A selective shim, `awk() { case "$*" in *'@@'*) <fail> ;; esac; command awk "$@"; }`, so only the normalizer is affected. Three shapes, each giving exit 1 and empty stdout: exit 3 with no output, exit 0 with no output, and exit 3 after printing the first lines of the real normalized output. Shape 1 (exit 3, no output) pins the combined behavior and is expected to survive each single-check deletion. Shape 3 is the exit-status check's kill. Shape 2 plus case (g) is the non-empty check's kill.
    - (v) The awk matrix. Parametrize over `/usr/bin/awk`, `mawk`, `gawk`, `original-awk` and `nawk`, resolved with `shutil.which` and de-duplicated by `os.path.realpath`. Skip any that is absent. Prepend a directory whose `awk` symlinks to the implementation. Each must reproduce case (t)'s expected text and cases (m), (q) and (ae). Name each case by the implementation it resolved to (the `realpath` basename). Under `GITHUB_ACTIONS`, `/usr/bin/awk` must resolve and pass, so the matrix is never all-skip, and an awk the workflow itself installs fails instead of skipping when absent (Verification 7). Any other awk that resolves is run and named, not required.
    - (w) The normalizer runs on every `TestGitDiffEntryShapes` shape, including the rename with an edit. Its stdout, with trailing newlines stripped, equals `reference_normalized_text` for each shape-pin diff. A secondary guard asserts that no hunk header carries a start offset (no line matching `^@@ -[0-9]`). The expected text drops the text-entry `index` lines and keeps the binary and empty-file ones.
    - (x) A hand-written CRLF pass-through case, with a precondition assert that the input contains `\r`: a diff whose content lines end in CR keeps the CR on every content line after normalization.
    - (y) Pass-through identity: a diff of shapes no rule touches (mode-only change, 100% rename, binary entry) normalizes to itself.
    - (z) Hand-written pair with identical `index` lines, shaped as `diff.suppressBlankEmpty=true` prints it, differing only in trailing blank context count at the end of input. They give different values, because the header keeps its counts (row 38). Assert that the two texts are equal once their `@@` lines are removed and trailing newlines are stripped, so the difference comes from the counts alone. Real commits would also differ in their `index` lines, which would break that assert.
    - (aa) Multibyte UTF-8 content and a multibyte path each pass through, and a change inside either gives a different value. Run it under both `core.quotePath` values, because the default octal-quotes a non-ASCII path to ASCII and so tests nothing multibyte alone.
    - (ab) A gitlink bump gives a different value from the same repo at another commit.
    - (ac) Residual pin for relocation at the marker level lives in `test_marker_script.py` (below).
    - (ae) Hand-written: a header or `index` form the rules do not recognize keeps its digits in the preimage. Each of these, with a digit changed inside it, gives a different value:
      - a combined-diff header `@@@ -1 -1 +1 @@@`;
      - a glued tail after the closing `@@`, as in `@@ -1 +1 @@x`;
      - an `index 1111111..2222222 100644 extra` line (an extra field) directly before a `--- ` line;
      - an `index ABCDEF1..2222222 100644` line (uppercase hex) directly before a `--- ` line.

      Also assert that the normalizer's output still contains each of the four lines verbatim.
    - Value-differs cases (b), (c), (d), (i), (j), (q) and (ae) are table-driven rows of (raw text, expected normalized text), hand-derived as (t)'s is, in place of per-case repos. Case (p)'s variants are table-driven pairs of raw texts with equal expected normalized text, except the negative pin, whose pair differs. Keep real-git cases for (a), the one (p) relocation pair, the shape pins and the end-to-end tests. Case (s) is also in the table as a hand-written colored pair, because after M4 no path feeding the cumulative-review normalizer produces colored text (the `--staged` arm stays bare but is not hashed by it). The real guard for M4 is `test_merge_base_diff_ignores_color_and_external_diff_config`.
    - The letters f, h, l and n are not used.
  - `TestLibHashDiffText`'s docstring at `:201-205`: it now says that `_lib_cumulative_review_value` hashes through this recipe.
- `claude/.claude/hooks/tests/test_marker_script.py`
  - Import `reference_normalized_text` from the shared helpers.
  - `:4091`: the expected value becomes `hashlib.sha256(reference_normalized_text(recorded_text)).hexdigest()`, so the oracle is independent of the function under test. Also assert that it is not `hashlib.sha256(recorded_text)`, which proves the normalization was applied.
  - `:4189-4231`:
    - Rename to `test_write_and_status_values_match_the_shared_recipe_on_script_output`.
    - The expected value is `hashlib.sha256(reference_normalized_text(...))` of the script's own stdout. Add an assert that the stored value is not `hashlib.sha256` of the raw stdout (no such assert exists today).
    - Fix the docstring's stale premise: the write arm reads a recorded subject, and both arms reach `_lib_cumulative_review_value`, whose output case (t) pins.
  - `:337-344`: fix `_advance_origin_main_and_rebase`'s docstring. Each `commit_fn` commits itself.
  - Add `_prepend_distant_line(repo)` beside `_insert_adjacent_line` at `:380`. It writes `L0` before `L1` in `shared.txt`, then stages and commits.
  - Add `test_status_stays_live_after_rebase_shifts_hunk_line_numbers`, the regression test:
    - On `context_diff_repo`, record and write.
    - Run `_advance_origin_main_and_rebase(repo, _prepend_distant_line)`.
    - Assert no `could not verify` in stderr first, then `cumulative-review: live`.
  - Add `test_status_historical_after_rebase_when_an_added_line_changes`:
    - The same setup and rebase as the regression test, followed by a commit that changes the PR's appended `L11` line.
    - Assert `historical`, with no `could not verify`.
  - The shim tests build one `PATH` carrying both the awk shim directory and the existing gh shim from `_env_with_gh_shim`, the awk shim resolving the real awk before either is prepended.
  - Add `_write_awk_shim_failing_on_normalizer(stub_dir, mode)`. When any argument contains `@@` (row 31) it fails in one of two modes: exit 1 with no output, or exit 1 after printing the first lines of the real output. Otherwise it execs the real awk, resolved with `shutil.which` before PATH changes.
  - Add `test_status_stays_live_after_relocated_edit_with_identical_context`, the marker-level pin for rows 36–37 and §50's weakened check: on a hand-built file with two stanzas of identical ±3-line context, record and write, then move the PR's added line to the other stanza and assert `cumulative-review: live`.
  - Add `test_write_refuses_and_keeps_subject_when_normalization_fails`, parametrized over both shim modes:
    - Record with the real awk, then write under the shim.
    - Assert exit 2, `could not hash the recorded cumulative-review subject`, the subject still present, and no marker.
  - Add `test_status_never_live_when_normalization_fails_with_marker_on_disk`:
    - Record and write with the real awk, then run `status` under the shim.
    - Assert that the line is not `live` and that stderr contains `cumulative-review: could not verify`.
  - `:4301-4307`: the docstring now says the cache tolerates line-number shifts but not changed context lines. The assertions are unchanged (row 30).
  - `:3831-3833` is unchanged (row 32).
- `claude/.claude/scripts/tests/test_pr_diff_against_base.py`
  - `:417-425`: the local wrapper is deleted and the shared `cumulative_review_value` is used. This file's texts are small.
  - `:591-621`: `.decode()` on the `recorded_text` line (`:609`) is dropped, so the call at `:611` passes bytes. The shared helper's return is unpacked as `(exit_status, stdout)`. The test asserts exit status 0, then compares the returned stdout to `from_cumulative_hash` (assigned at `:619`) in the assert at `:621`. In the docstring at `:592-596`, "through _lib_hash_diff_text" becomes `_lib_cumulative_review_value`.
  - No local wrapper around `_lib_cumulative_review_value` or `_lib_normalize_cumulative_review_text` survives in any of the three test files.
  - Two local wrappers stay. `_lib_cumulative_diff_hash` (`:428-443` here) stays because the test above still calls it at `:619` as the independent comparator, and it needs the gh-shim env. `_hash_diff_text` in `test_marker_lib.py` (`:55`) stays because `TestLibHashDiffText` keeps using it over the unchanged `_lib_hash_diff_text`.
  - Add `test_merge_base_diff_ignores_color_and_external_diff_config`:
    - Set `color.ui=always` and `diff.external=false` in the repo's local config.
    - The bare invocation exits 0, its stdout starts with `diff --git`, and it contains no `\x1b[`.
- `docs/design-decisions/ready-for-review-cumulative-diff-cache.md`: add under `:3`:

  `**Superseded in part by [cumulative-review-marker-keyed-on-normalized-diff.md](cumulative-review-marker-keyed-on-normalized-diff.md) (<date>):** the marker value is now the sha256 of the diff with hunk headers and text entries' `index` lines normalized, not of its raw bytes, so a rebase that only shifts line numbers or text files' blob ids also reuses the prior review. The base-ref exclusion and both named residuals stand; the first named residual is widened as that file describes, and that file adds a PR-side relocation residual the original did not have.`
- `docs/design-decisions/cumulative-review-marker-not-recomputed.md`: add under `:3`:

  `**Superseded in part by [cumulative-review-marker-keyed-on-normalized-diff.md](cumulative-review-marker-keyed-on-normalized-diff.md) (<date>):** the first disclosure below no longer holds for a diff with a hunk header or a text entry's `index` line, because a marker written before the switch then reads `historical`.`
- `docs/design-decisions/ready-for-review-fix-loop-convergence.md`: add, directly below its existing "Partially superseded by" note (under its provenance line), a "Superseded in part by" note in the same form as the two above. It says that the sentence "The byte-exact cumulative guarantee stays intact" now describes a normalized-text key, and that it also qualifies the earlier note's "Everything else here stands" for this guarantee. Use the same link and date. Leave the earlier dated note's text and the body untouched.
- `docs/hooks.md:245`:
  - "the hash of the diff recorded" becomes "the `_lib_cumulative_review_value` of the diff recorded".
  - Append two sentences:
    - That value is the sha256 of the diff with each hunk header reduced to its line counts and each text entry's `index` line dropped.
    - So a rebase that only shifts line numbers or text files' blob ids still matches. Link the new decision file for what else shares a value, and restate none of it.
- `CHANGELOG.md`, under `[Unreleased]` › `### Changed`, one entry:
  - **The `cumulative-review` cache now survives a rebase that changes only hunk start line numbers, function-context text, or the blob ids of text files the PR touches.** Name the decision file for what else shares a value.
  - Existing markers whose diff has a hunk read `historical` once, so the next step-3 pass on an open PR runs a full review.
  - `pr-diff-against-base.sh`'s cumulative diff now runs with `--no-color --no-ext-diff`.
- `.claude/plans/cumulative-hash-patch-id.md`: this plan, committed at plan-it Step 7.

**Commit message must carry** this justification, per CLAUDE.md's hand-rolled-logic bullet (row 34), in substance:
- git's first-party normalizer for diff text is `git patch-id`.
- On git 2.56.0, `--verbatim` silently stops hashing at the first empty context line that `diff.suppressBlankEmpty=true` prints, yet it still prints one well-formed id. Pinning that config would close only that one trigger, and the call site cannot detect an unprobed one.
- This filter rewrites only the hunk header gitattributes(5) defines as `@@ -k,l +n,m @@ TEXT`, keeping its counts `l` and `m` and dropping `k`, `n` and TEXT. It drops only the `index <hash>..<hash>` extended header documented in diff-generate-patch, and only when a `--- ` line follows it.
- Every other byte reaches sha256 unchanged. So a header form the rules do not recognize can only stay in the preimage, which causes a miss and never a false `live`.
- The key is not content equality. `docs/design-decisions/cumulative-review-marker-keyed-on-normalized-diff.md` states the equivalence class and its residuals.
- Two texts that differ only in trailing blank context hash differently, because the counts stay in the preimage (pinned by case (z)).

**Not modified, on purpose:**
- `README.md` (row 29).
- `claude-skills/skills/ready-for-review/SKILL.md` (row 26): its wording is approximate for the named residuals, and the decision file says so.
- `docs/scripts.md`: it describes the diff's range and makes no claim about the hash recipe.
- `docs/design-decisions/content-addressed-review-markers.md:5`: "on a byte-identical rebase" stays true, because a byte-identical rebase still hits.
- `_lib_hash_diff_text`'s body and its 11 other call sites (row 20).

## Verification

1. **Shape pins first (rows 11–13).** Write `TestGitDiffEntryShapes` before any code change. It calls no `_lib.sh` helper (`isolated_git_diff` in `helpers.py` is part of this step). Run it with `scripts/venv-run.sh pytest claude/.claude/hooks/tests/test_marker_lib.py -k TestGitDiffEntryShapes`.
   - If any non-textconv pin fails, stop and return the output to the session. The failing cases would be a binary `index` line followed by `--- `, a text entry without one, or a body line with an unexpected prefix. Each is a design question. Do not adjust the regexes to fit.
   - If the textconv pin shows `Binary files`, flip that assertion, record "this residual does not arise" in the decision file, and continue.
2. **Red first.** Write `test_status_stays_live_after_rebase_shifts_hunk_line_numbers` and `test_merge_base_diff_ignores_color_and_external_diff_config`, and run both against the unchanged code.
   - The first must fail with `historical` and no `could not verify`. That separates the intended red from a masked computation failure.
   - The second must fail, either on the exit code or on `\x1b[` in stdout.
   - Then implement.
3. **Stop conditions after implementation.** These must pass on the local git and awk:
   - `TestLibCumulativeReviewValue` cases (a)–(e), (g), (i)–(k), (m), (o), (p), (q), (r), (s), (t), (u), (v), (w)–(ab), (ad) and (ae), and the table-driven rows. Case (ac) lives in `test_marker_script.py` and is covered by that file's tests below;
   - each false-live pin shown failing once under its mutant, with the dispatch's return naming which pins it saw red:
     - dropping every `index` line: the binary and empty-file pins ((k), (t), (w), (y));
     - unanchored `index` regex: (q);
     - unanchored header regex: (q) via the long-prefix line;
     - truncated output and removing the `END` block: (t), (u);
     - removing the exit-status check: (u) shape 3 and the marker-level shim tests; removing the non-empty check: (u) shape 2 and (g), because the marker-level modes both exit nonzero;
     - stopping at the first empty line: (o);
     - dropping backslash lines: (d);
     - stopping at the first backslash line: (e);
     - a whitespace- or CR-collapsing rewrite (`sub(/[ \t\r]+$/, "")`): (b), (x);
     - a rule that also drops path, mode or rename lines: (c), (i), (j). Case (ab) stays a regression pin with no mutant;
     - deleting `LC_ALL=C`: (ad), and (r) on the macOS run;
     - a header rule that drops the hunk counts again (back to a bare `@@`): (z) and (t);
     - a loosened header regex (no closing-`@@` terminator check, or a combined-diff `@@@` prefix accepted), and a loosened `index` regex (uppercase hex accepted, an extra field accepted, or the `$` anchor removed): (ae);
   - both step-2 tests, `test_status_stays_live_after_rebase_shifts_hunk_line_numbers` and `test_merge_base_diff_ignores_color_and_external_diff_config`, the latter being the only real-git guard for M4;
   - the unchanged `test_status_historical_after_base_move_shifts_diff_context`;
   - the probe re-runs (rows 7 and 37, extended with a conflict-free rebase of the moved-edit pair), run on the local git at dispatch step 5, before the commit message and decision file state their results. Cite the output.
     - Stop and return to the session if row 7's or row 37's relocation result differs from the relayed one.
     - The conflict-free-rebase extension has no relayed result, so record its per-path outcome in the decision file instead of stopping (row 37).
     - Row 38's re-run is dropped, because case (z) pins that property.
   - the `test_hook_alignment.py` tripwire (no hook directly references `_lib_cumulative_review_value` or `_lib_cumulative_diff_hash`).

   If any fails, stop and return the output. Do not loosen a test, broaden a regex, or add a third rule.
4. Scoped tests, per the repo's `CLAUDE.md`:
   ```bash
   scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py
   ```
   Editing `claude/.claude/tests/helpers.py` is a `GLOBAL_TRIGGER_PATHS` entry in `select-tests.py`, so the selection is the full suite for every run on this branch. That is the script working as intended (CLAUDE.md case 1), and no hand-run is needed. A "selection must include" check would be vacuous, so none is stated.
5. Lint:
   ```bash
   scripts/venv-run.sh ruff check claude/.claude/
   scripts/list-shell-files.sh | xargs -0 scripts/venv-run.sh shellcheck
   ```
6. Run `git grep -n -e '_lib_hash_diff_text' -e '_lib_cumulative_review_value' -- claude/.claude/scripts/marker.sh claude/.claude/hooks/_lib.sh`, and read only the code lines.
   - The cumulative-review write arm and `_lib_cumulative_diff_hash` must reach only `_lib_cumulative_review_value`.
   - No comment may still name `_lib_hash_diff_text` as the cumulative-review recipe.
7. **Awk coverage across machines (row 17).** On macOS, case (v)'s `/usr/bin/awk` run covers BSD awk. The `code-writer` dispatch ends before any CI run, so it does not decide the CI awk set.
   - **Dispatch scope.** It appends permanent awk lines to the `run:` of the existing `Report runner core count` step in `tests.yml`, keeping the step name. The banner probe is specified by properties:
     - It prints `readlink -f "$(command -v awk)"`.
     - It then tries each version flag in turn (`--version`, `-W version`, `-version`), because the flag differs per awk, with stdin from `/dev/null`, inside one `{ ...; }` group so `||` sees awk's own exit status.
     - It prints the first line of the first flag that succeeds.
     - The line ends with `|| true`, so the step cannot fail under `bash -e` without `pipefail`.

     `.github/workflows/tests.yml` is therefore in the Modify list for that step.
   - **No package install.** The dispatch installs no package. CI hard-requires `/usr/bin/awk` (case (v)) and any awk the workflow installs, and none is installed here. If a second awk looks necessary, the dispatch stops and returns to the session. Naming the package, its version and its rationale, and getting the engineer's confirmation, come first.
   - **After the first push,** the session reads the log line and case (v)'s named results from the PR's CI run. If it then adds an install step, it keeps the three steps' `if:` parity, restores the default `awk` with `update-alternatives --set awk <the pre-install target>` or runs both test passes under the new default and records the outcome, and compares `readlink -f "$(command -v awk)"` before and after.
   - **PR body.** It states which awk implementations ran. It records the `Run tests (parallel)` wall time and the per-class `--durations` of `TestGitDiffEntryShapes`, `TestLibCumulativeReviewValue` and the new `test_marker_script.py` tests from one local run, because wall time alone is within runner noise and that job has hit its time cap before.

## Out of scope

- **`_lib_hash_diff_text`'s semantics and its 11 other call sites** (row 20).
- **ready-for-review's SKILL.md** (row 26). Its wording stays, and the decision file records that it is approximate for the named residuals.
- **`--full-index` on the merge-base diff** (row 39). It would close the abbreviated-blob-id residual, but it changes the text reviewers read and no engineer quote asks for it.
- **`pr-diff-against-base.sh --staged`** (row 27).
- **Rewriting the bodies of §44 or §50.** They are preserved decision records and get supersession notes only (row 25).
- **Any git floor or README change** (row 29).
- **`--no-textconv`.** It would change the text that reviewers read, so textconv stays a documented residual (rows 13–14).
- **Normalizing `similarity index` or `dissimilarity index`.** A base edit to a renamed file can shift the percentage and miss, which is the safe direction. Rename detection can also flip the entry to a delete plus an add, and no line rule recovers that.
- **Normalizing color or other porcelain diff config** (`diff.algorithm`, `diff.context`, `diff.noprefix`, `diff.suppressBlankEmpty`). Each changes lines that pass through unchanged, so it can only cause a miss, except a context width below 3, which widens the relocation residual. `diff.suppressBlankEmpty` with trailing blank context cannot collide, because the header keeps its counts (row 38). The M4 comment claims only color and external diff.
- **Applying the normalizer to the `code-review` or `skill-review` markers.** Those are hook-enforced staged-diff gates with their own threat-model tiers and base recipes. This plan's rebase-shift root is the cumulative cache's alone.
- **An awk or git version check in `install.sh`.** README says it checks none, and this plan keeps that.
- **Closing #1207.** This change advances only that issue's rebase-shift case.
