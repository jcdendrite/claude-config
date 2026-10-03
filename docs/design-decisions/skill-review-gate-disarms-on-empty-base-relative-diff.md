# Skill-review gate disarms on an empty base-relative gated diff

*2026-09-23.*

`require-skill-review.sh` and `scripts/marker.sh`'s `write skill-review`/`status` arms all resolve `_lib_skill_review_diff_base` once and thread it through the trigger, the structural validator's path list, and the marker hash. Mid-merge/cherry-pick/rebase, a `SKILL.md` or `claude-skills/skills/plan-review/ROUTING.md` an already-reviewed upstream commit brought in unchanged reads as already-reviewed rather than newly staged, so the gate disarms instead of demanding a `/skill-review` pass over content the committer did not author.

## The symptom this removes

A HEAD-relative `git diff --cached` re-gates upstream-reviewed content mid-merge. During a merge that brings in an untouched `SKILL.md`/`ROUTING.md` from the default branch, every such file reads as unreviewed staged content, and the `git commit` that completes the merge cannot be released without a `/skill-review` pass over files already reviewed on their own PRs (GH-1076). The skill-review gate therefore diffs against the same novel-content base as the code-review gate (`_lib_gate_diff_base`) and the plan-review gate, narrowed to what GH-1076 needs.

Markers live under `<config-dir>/skill-review-markers/`, where `<config-dir>` resolves to `$CLAUDE_CONFIG_DIR` when set, else the default per-user Claude Code config directory.

## Why no empty-base sentinel exists here

`_lib_code_review_marker_value` binds an empty base-relative diff to `sha256("code-review-empty-base:$base")` rather than falling through to `sha256("")`. `require-code-review.sh` still consults a marker on that empty result, so without the binding a marker earned during one operation's degenerate empty-diff case would validate a different, forged base landing on the same empty result.

The skill-review path does not have this exposure, because it does not fall through to a marker comparison on an empty base-relative diff. `require-skill-review.sh`'s trigger disarms one exit earlier, on a name-only listing scoped to exactly the pathspec set the marker hash uses (`SKILL_CONTENT_PATHSPECS` + `ROUTING_PATHSPEC` = `MARKER_PATHSPECS`).

"No gated path in the base-relative diff" and "the base-relative marker diff is empty" are the same condition by construction. The degenerate case is therefore unreachable at the hash: `_lib_staged_diff_hash` is never called with an empty-but-non-disarming base-relative diff for the gated pathspecs, and a plain `sha256("")` comparison is correct in every state.

See [`plan-review-gate-disarms-on-empty-active-plan-set.md`](plan-review-gate-disarms-on-empty-active-plan-set.md) § "Why `_lib_code_review_marker_value` keeps its sentinel" for the code-review side of this comparison. Its "Pre-analyzed successor" section also covers the declined alternative here: re-arming the gate by falling back to the HEAD-relative set on an empty base-relative one reinstates GH-1076 exactly, since it re-arms on precisely the upstream content the base exists to stop gating.

## Why revert is excluded while merge, cherry-pick, and rebase are not

The general rule: the disarm fires only where the synthesized base tree is a *union* of two trees that each reached a trusted anchor (`origin/<default>` or `HEAD`). Merge, cherry-pick, and rebase all synthesize such a union. Revert's synthesized tree (`merge-tree --write-tree --merge-base=X HEAD X^`) is a *subtraction* — HEAD minus a reviewed patch — so its removals were reviewed nowhere. `_lib_skill_review_diff_base` wraps the shared `_lib_gate_diff_base` closure and excludes exactly the revert case, returning 1 (empty base) instead of the subtraction tree.

This is not a sibling-symmetry gap with the plan-review gate, which uses the shared closure directly and does not exclude revert. The two gates authorize different units, so the plan-review choice does not transfer here.

Plan-review's marker is *state-addressed*: `_lib_active_plan_hash` hashes the active plan set's current paths-plus-contents, so what is authorized is "this is the plan text a reviewer approved," independent of how the current state was reached. A subtraction base is not a materially different risk there. A plan file's active set is decided by comparing current content against the base's content, and a revert that removes plan prose simply leaves less (or no) content to review, not unreviewed content masquerading as reviewed.

Skill-review's marker is *delta-addressed*: it hashes a diff, and the skill it gates exists specifically to audit deletions. Its own deny text demands "an explicit behavioral-equivalence table for any removed or shortened lines." A subtraction base defeats a delta-addressed audit: diffing staged content against HEAD-minus-a-reviewed-patch makes the patch's own removals invisible to the diff. A resolution that simply keeps a revert's removal would then disarm with the audit this gate exists to force never having run against it.

Mid-revert the exclusion buys the GH-1076 fix nothing anyway: a revert introduces no third-party content, so the HEAD-relative staged gated diff is exactly the committer's own removals. GH-1076's over-gating complaint — reviewing content the committer did not author — does not arise there, while the cost of disarming (skipping the removal audit `/skill-review`'s behavioral-equivalence table exists for) is real. Staying HEAD-relative mid-revert is therefore free.

## The bracket: sampling the exclusion on both sides of the base computation

The wrapper's exclusion decision and `_lib_gate_diff_base`'s own formula selection read the same mutable gitdir at different points in time. Real time and several `git` spawns separate the two reads.

An exclusion sampled only *after* the base computation is a TOCTOU. A revert that starts before the wrapper's own check and ends (its `REVERT_HEAD` removed) before that trailing probe would have its subtraction tree printed as though it were an ordinary union base.

`_lib_skill_review_diff_base` therefore brackets the `_lib_gate_diff_base` call. It samples `_lib_git_inprogress_state` once before the call and once after, and excludes on either sample reading `revert`.

- A pre-sample reading no in-progress state returns 1 directly, without calling `_lib_gate_diff_base` or spawning `merge-tree --write-tree`.
- That is the same answer `_lib_gate_diff_base` itself gives on the identical resolved gitdir, without the six-git-process closure to reach it.
- A single state change across the call (a revert concluding in a second terminal mid-call) yields the conservative HEAD-relative answer.
- That answer is the safe direction, and the same one the gate gives outside any in-progress state.

The bracket is not a closed guarantee. `_lib_git_inprogress_state` is itself a short sequence of up to five file stats, not one atomic read. The pre-sample, `_lib_gate_diff_base`'s own internal probe, and the post-sample are each a sequence in time rather than an instant snapshot.

The one residual shape that still yields a subtraction tree is a revert that both starts and ends *inside* the bracket's own window, while some other trusted state (merge, cherry-pick, or rebase) was already in progress at the pre-sample. That state's own marker must disappear and `REVERT_HEAD` appear before the internal probe, which then selects the subtraction formula against a genuine OID. `REVERT_HEAD` must then disappear again before the post-sample.

Two other bypasses dominate the residual without needing any timing.

- The ungated clean merge: a conflict-free `git revert` reaches a commit with no gate firing (see "Known gap: the ungated clean merge" below).
- The forgeable anchors: a committer who can write into the gitdir can plant an anchored `MERGE_HEAD`, and one who runs `git fetch . +<ref>:refs/remotes/origin/<default>` with porcelain alone can forge the remote-tracking anchor. Neither needs a race (see `merge-tree-base-recipe-for-gate-diff-base.md`).

The residual's realistic reachability is honest concurrency in the same worktree, not an attacker racing the gate. Examples are a `git revert --abort`/`--continue` in a second terminal, or an editor's git integration, firing inside the hook's own execution window.

## Rebase is inert in the ordinary case

`REBASE_HEAD` names the pre-rebase commit being replayed, which in the ordinary case is an ancestor of neither `origin/<default>` nor mid-rebase HEAD, so `_lib_gate_diff_base`'s anchor check fails and the HEAD-relative behavior stands. The disarm is therefore live in practice for merge and cherry-pick, and inert for rebase outside the exotic topologies `_lib_gate_diff_base`'s own header already documents as falling back to the empty base (octopus merges, `cherry-pick -m 2`, `rebase --rebase-merges`, git older than 2.38/2.40).

## The structural validator skips a path only on the diff's own deletion status

The validator's path list is a separate capped listing over the SKILL.md pathspecs with `--diff-filter=d`, so staged deletions and move-outs are excluded by the diff's own status. The disarm and trigger listings stay unfiltered so a deletion still arms the gate and reaches the marker check through the base-relative hash. Every listed path is then read with a capped `git show :<path>`, and any nonzero status denies. The causes include:

- an unmerged entry
- a corrupt index
- a name git still lists C-quoted
- an NFD name under `core.precomposeunicode`
- a cap kill

Inferring deletion from an empty name-keyed `git ls-files` lookup was rejected: git normalizes argv names differently from the diff's output, so a path with content can look absent and skip the validator.

## Known residual: the structural validator's auto-merge give-up

Scoping `STAGED_SKILL_PATHS` to the base-relative set means the structural validator runs only on genuinely novel content. That gives up one case: an auto-merge that combines two independently-valid gated files into one structurally invalid tree, with the conflict-free merge itself never having been validated at all.

- **Bound:** this repository's own CI (`test_frontmatter_parses_strictly`, `corpus_budget_violations` in `claude-skills/skills/tests/test_skills.py`) validates every `SKILL.md`'s frontmatter and the corpus budget repo-wide, independent of this commit hook, so the structural shape is caught before a branch can land here.
- **Not bounded for marketplace consumers:** `plugins/skill-management` installs into arbitrary consumer repositories, and those repositories run none of this repository's test suite. For them nothing this gate does catches the residual.
- **Behavioral drift:** two independently-reviewed edits to the same gated file drift the same way, under the same bound.

A conflict-free merge of the same two trees never reached any gate to begin with (see "Known gap: the ungated clean merge" below), which dominates both.

## Known residual: aliased index entries

The validator reads each listed path's staged content by re-resolving the name string (`git show :<path>`) and mirrors it into a temp tree under that same name. A hand-built index holding an invalid entry A plus a valid twin B whose name aliases A therefore leaves the validator reading B's content, and A skips validation even with a matching marker. Three alias shapes are known: a C-quoted rendering of A's name that equals B's literal name (portable across platforms, and pinned by `TestSkillReviewGateAliasedIndexEntryResidual`), an NFD/NFC pair under `core.precomposeunicode=true` (Apple git only), and directories differing only in case (which collide on a case-insensitive temp filesystem).

This is accepted as a known residual, bounded three ways. It needs a Bash-capable actor who builds the index by hand with `git update-index --cacheinfo` or an equivalent, and that actor is already dominated by the cheaper bypasses recorded here ("Known gap: the ungated clean merge", the forged anchors). The marker hash still covers A's real blob, so the behavioral review saw the invalid content and only the deterministic structural check is skipped. No single-entry path reproduces it: an unaliased unreadable entry denies as unreadable.

The recorded alternative is reading each blob by the object ID that `git diff --cached --raw -z --diff-filter=d` reports alongside its name, and keying the temp mirror on the entry rather than the path string. That removes name re-resolution, C-quoting, and normalization mismatch together. It was not taken, because the actor it would constrain can already bypass the gate for free.

## Why the disarm trace is a stderr line, not a compliance log

The disarm exit prints one line so that a debugging session can tell "disarmed against a synthesized base" apart from "no gate fired at all" (see "Known gap: the ungated clean merge" below). Stderr from a hook that exits 0 goes to the Claude Code debug log only, visible under `claude --debug` or `--debug-file` and absent otherwise, so the line is not a durable record: a bypass question asked after the fact usually finds no trace.

A compliance-log file mirroring `require-code-review.sh`'s `.review-ledger-compliance.log` was considered and declined: it would need a config-dir resolution at the disarm exit whose failure must not change the allow, introducing a second, fail-open config-dir posture into a hook whose existing one (the marker-check config-dir resolution) is deliberately fail-closed. A single non-blocking stderr line at the disarm exit — the same shape this hook's own corpus-budget warning already uses — costs no resolution and no write, and is guarded on a non-empty base so the ordinary "nothing staged" exit stays silent.

## `merge-tree --write-tree` writes unreachable objects

`_lib_gate_diff_base`'s `merge-tree --write-tree` call writes the synthesized tree (and any blobs/trees it needs) into the consumer repository's object database, even though nothing ever references it. Mid-revert, the pre-sample short-circuit is what keeps that write from happening at all — the wrapper returns before `_lib_gate_diff_base` is ever entered.

## Conflict-marker hard deny

`_lib_gate_diff_base` builds its tree with `merge-tree --write-tree`, which writes conflict-marker blobs into the base tree for every conflicted path. A staged gated blob that equals the base blob on such a path therefore means "unresolved conflict", not "reviewed upstream". The base-relative diff hides that path at the disarm exit and, in a merge where other gated files were resolved, on the armed path as well.

Git labels conflict sides with the ref name it was given, while `merge-tree --write-tree` labels them with literal OIDs. A merge named by full OID produces byte-identical labels, so an unresolved blob staged with `git add -A` equals the base blob. A merge named by ref differs only in the label text, which arms the gate by coincidence rather than by control.

Whenever the base is non-empty, the hook therefore scans the gated pathspecs HEAD-relative, in up to two capped git calls. Both print path lists only, so no diff or grep presentation config (`color.ui`, `diff.external`, `GIT_EXTERNAL_DIFF`, a diff driver command) can change the verdict.

- Call A, `git diff --cached --name-only --diff-filter=d -G '^(<<<<<<<|=======|>>>>>>>)( |$)'`, lists the non-deleted candidates. `-G` selects a file when a removed line matches as well as an added one, so A over-selects. `--diff-filter=d` lets a commit delete a gated file whose HEAD version carries marker lines. An empty A ends the scan.
- Call B, `git grep --cached -L -E` with the same regex over the same gated pathspecs, lists the staged blobs that carry no marker line. It runs only when A is non-empty.
- The deny set is A minus B by exact path match. Because B lists files without a match, a path A and B name differently stays in the deny set, so a naming mismatch fails safe.
- Both calls pass `-a --no-textconv`, so a `-diff` or `binary` attribute, or a textconv driver, on a gated path cannot hide a marker line.
- A nonzero status from A, or a status other than 0 or 1 from B (a cap kill or a git error), denies with a scan-failure reason.
- The scan is HEAD-relative because the base-relative diff of a hidden path is empty.

The deny is hard rather than a fall-through to the marker check. The marker hash is base-relative, so it is empty for a hidden path, and `marker.sh` refuses to write a marker for an empty diff. No marker could ever release the commit.

Accepted residuals:

- A gated file whose staged change touches a column-0 line starting `<<<<<<< `, `=======`, or `>>>>>>> `, or a bare `<<<<<<<`, `=======`, or `>>>>>>>` line, also hard-denies mid-merge, even when no conflict exists. The deny reason names this case and the way out: indent every such line so none starts at column 0 and restage, which puts the file back under the marker check.
- A file whose HEAD version has two column-0 marker lines still denies when the commit indents only one, until every such line is indented. The remedy converges: each indented line shrinks the set.
- A merge in which upstream itself indents the marker lines is released, because the staged blob no longer carries a column-0 marker line.
- A bare marker line ending in a carriage return (`<<<<<<<` then CR) does not match the scan. Git's own conflict output does not emit that shape.
- A `conflict-marker-size` attribute on the path changes the marker width git writes, and the scan does not match that width.
- The code-review gate carries the same hidden conflict and has no conflict-marker scan. See `merge-tree-base-recipe-for-gate-diff-base.md` § "Accepted residual: the code-review gate can release an unresolved conflict".
- A Markdown setext-style heading (`Some Heading` followed by a bare `=======` underline) is ordinary content, but stages the same bare `=======` line the scan hard-denies on. The same indent-and-restage workaround above applies: indenting the underline so it no longer starts at column 0 puts the file back under the marker check.

## Latency

Each capped call (`_lib_capped`) is a 5s cap plus a 2s kill grace, so a cap hit costs 7s and a call that does not hit costs up to 5s. Where `timeout` is on PATH, `_lib_jq` is `timeout 5 jq` with no grace, so it costs up to 5s. Without `timeout`, it runs `jq` uncapped.

Git processes spawned, in a repository whose `origin/HEAD` is set and whose origin anchor is reached:

- Base resolution alone spawns 7 mid-merge, cherry-pick, or rebase: two gitdir resolutions, the `origin/HEAD` read and its verify, one anchor check, `merge-tree`, and the tree verify. It spawns 1 with no in-progress state and 1 mid-revert, where the pre-sample returns before `_lib_gate_diff_base` is entered.
- The hook through its three staged-path listings adds the repo-root resolution and the listings. It spawns 13 at most mid-merge, cherry-pick, or rebase (12 when call B does not run), and 5 with no state or mid-revert. The extra mid-merge spawns are the conflict-marker scan's call A, plus call B when A is non-empty. Both run only with a non-empty base. A commit whose trigger listings are empty exits before the third listing, at 11 (12 when call B ran) and 4.

Base resolution makes up to 12 sequential capped calls. A cap hit at six of them ends the chain: both gitdir resolutions, the state-ref read, the `HEAD` anchor check, `merge-tree`, and the tree verify. The reachable worst case is 5 cap hits, so 5 x 7s + 7 non-hit calls x 5s = ~70s. A fully stalled git costs one cap, 7s.

The whole-hook worst case sums each capped site: ~70s base resolution, 2 x 7s conflict-marker scan calls, 3 x 7s listings, 12s structural validator (10s cap plus grace), 12s corpus-budget scan, 7s marker-hash diff, and 2 x 5s `_lib_jq` (input parse and deny encoding). That is ~146s, plus 7s per staged `SKILL.md` for its `git show`. It is an approximate upper bound that no run reaches, because a cap hit at a listing, `git show`, or validator site denies and exits early. It excludes the uncapped calls: the repo-root and hook-own-directory resolutions, the corpus `git ls-files`, and the `cwd` extraction.

Without `timeout` or `gtimeout` on PATH, every `_lib_capped` site runs uncapped. A stalled git then holds the hook until the harness timeout below, which releases the commit rather than blocking it. The uncapped sites are the conflict-marker scan's two calls, the three staged-path listings, the per-path `git show`, the marker hash, the validator, the corpus-budget scan, and the base resolution.

`plugins/skill-management/hooks/hooks.json` sets no `timeout`, so Claude Code's default of 600 seconds for command hooks applies, and a `PreToolUse` command hook that times out does not block the call ([hooks reference](https://code.claude.com/docs/en/hooks.md), Timeouts section, fetched 2026-09-19). The sizing must therefore keep the worst case under 600s: ~146s + 7s x N stays below it for up to 64 staged `SKILL.md` files.

The 64-file bound is conservative. A cap hit at a `git show` denies and exits, so a run that reaches the later sites completes every `git show` without a hit, at up to 5s each. That gives ~146s + 5s x N, which stays below 600s up to N = 90 (596s) and reaches 601s at N = 91.

Accepted residual: the trigger's four forks (`sed` and `tr` in the quote-strip, two `sed` in the split) are uncapped, as the single `grep` fork of a regex trigger would be. A release at 600 seconds does not block the call, so a wedged fork fails open for that call. A cap would add about four execs to the allow path, would do nothing where neither `timeout` nor `gtimeout` is on PATH, and would conflict with the 600-second sizing above if it came from a tighter `hooks.json` `timeout`. These forks read a pipe, not the filesystem, so a stalled filesystem, the case the git caps above target, cannot wedge them.

`scripts/marker.sh`'s `status` arm resolves the skill-review base separately from the shared `GATE_DIFF_BASE`, so mid-merge, cherry-pick, or rebase it repeats the 7-process resolution and its `merge-tree --write-tree` object write on every call. That cost is bounded by the same caps and is accepted.

## Performance

The trigger is `_lib_command_concludes_marker_gated_commit`. The hook is dispatched on every `Bash` tool call, not only `git commit`-shaped ones, and the cost that matters is the non-matching (allow) fast path.

Measured per-fire wall-clock cost of `require-skill-review.sh` on that fast path, run directly against the hook with a synthetic `tool_input` payload from a non-repository working directory, as 1 warm-up run plus 5 timed runs on an 8-core development machine at a load average of about 3, so ranges include contention. Heredoc payloads are `cat <<'EOF'`, N 67-byte filler lines (prose, git-dense prose, or prose with `&`), then `EOF`, and the one-`&` rows open the heredoc as `cat 2>&1 <<'EOF'`, so their only `&` is on line 1.

| Command | Size | Median (range) |
|---|---|---|
| `ls -la` | 6 B | 17ms (17–18ms) |
| compound, 3 git fragments | 43 B | 23ms (22–24ms) |
| prose heredoc | 5 KB | 31ms (30–38ms) |
| prose heredoc | 50 KB | 154ms (146–186ms) |
| prose heredoc | 500 KB | 1034ms (989–1082ms) |
| chained `git log -1 &&` x360 | 5 KB | 629ms (625–655ms) |
| prose heredoc, one `&` (`2>&1` on line 1) | 5 KB | 31ms (30–32ms) |
| prose heredoc, one `&` (`2>&1` on line 1) | 50 KB | 158ms (153–166ms) |
| prose heredoc, two `&` per line | 50 KB | 239ms (227–260ms) |
| git-dense prose heredoc | 5 KB | 158ms (154–166ms) |
| git-dense prose heredoc | 50 KB | 1506ms (1459–1639ms) |
| one fragment, `git log -1 &` x360 | 4.7 KB | 688ms (647–713ms) |

Typical interactive commands cost 17–23ms, and a 5 KB prose heredoc costs 31ms. All three are under this repo's stated hook performance budget (<100ms per fire). The budget is exceeded by about 50 `git`-naming fragments or about 30 KB of prose, so every 50 KB heredoc exceeds it, and git-dense prose exceeds it at 5 KB (158ms). A sweep on the same setup gives the crossing: chained `git log -1 &&` at 50 fragments costs 98ms (97–99ms), at 55 costs 107ms (104–123ms), and at 60 costs 114ms (113–121ms). A prose heredoc at 30 KB costs 102ms (95–105ms) and at 40 KB costs 134ms (127–144ms). Interpolating the table's own rows gives the same crossings. Between the `ls -la` row (17ms, no fragments) and the chained row (629ms, 361 fragments), 100ms falls at about 49 fragments. Between the 5 KB row (31ms) and the 50 KB row (154ms), 100ms falls at about 31 KB. The 50 KB prose row measured between 131ms and 163ms across separate runs on this machine, so treat both crossings as approximate. Both are measured budget overruns, not clean passes.

The profile splits each hook total into its parts. Fixed is the same payload sent with a non-`Bash` `tool_name`, so the hook exits after input parsing and before the trigger. Quote-strip and fragment split are each timed in a shell that sources the plugin lib. Fragment loop is the whole predicate, timed the same way, minus its own quote-strip and split. Each figure is the median of 5 runs after 1 warm-up, taken in the same run as the hook totals above. Sum adds the unrounded figures.

| Input | Fixed | Quote-strip | Fragment split | Fragment loop | Sum | Hook total |
|---|---|---|---|---|---|---|
| `ls -la` | 11ms | 5ms | 2ms | 1ms | 18ms | 17ms |
| compound, 3 git fragments | 10ms | 4ms | 2ms | 6ms | 22ms | 23ms |
| prose heredoc, 5 KB | 11ms | 5ms | 2ms | 16ms | 34ms | 31ms |
| prose heredoc, 50 KB | 17ms | 13ms | 5ms | 134ms | 168ms | 154ms |
| prose heredoc, 500 KB | 100ms | 97ms | 39ms | 1021ms | 1257ms | 1034ms |
| chained `git log -1 &&` x360 | 11ms | 5ms | 2ms | 592ms | 611ms | 629ms |
| prose heredoc, one `&`, 5 KB | 11ms | 5ms | 2ms | 14ms | 33ms | 31ms |
| prose heredoc, one `&`, 50 KB | 16ms | 14ms | 6ms | 147ms | 183ms | 158ms |
| prose heredoc, two `&` per line, 50 KB | 15ms | 12ms | 6ms | 258ms | 291ms | 239ms |
| git-dense prose heredoc, 5 KB | 11ms | 5ms | 2ms | 146ms | 165ms | 158ms |
| git-dense prose heredoc, 50 KB | 18ms | 12ms | 5ms | 1455ms | 1490ms | 1506ms |
| one fragment, `git log -1 &` x360 | 12ms | 5ms | 2ms | 661ms | 680ms | 688ms |

The sum matches the hook total within 3ms on the `ls -la`, compound, 5 KB prose heredoc, and one-`&` 5 KB rows, and within 5% on the chained, git-dense, and `git log -1 &` rows. It runs 9–22% high on the 50 KB and 500 KB prose rows, the one-`&` 50 KB row, and the two-`&` row, where the in-shell predicate ran slower than the hook did; that gap is not attributed. The growth is the per-fragment loop, not the `sed` and `tr` forks. The loop costs about 1.6ms per `git`-naming fragment (592ms over 361), about 0.18ms per prose line (134ms over 764), and about 1.9ms per git-dense line (1455ms over 764).

Every fragment costs a pure-bash word walk, and every fragment that names `git` also costs command-substitution subshells for the subcommand extraction. `_lib_split_fragments` leaves a bare `&` unsplit (GH-1063), so when the split output contains a `&` the predicate makes a second pass over the `&`-separated pieces of the fragments that contain one. A piece without the substring `git` is dropped before the pass walks it. An input with no `&` skips that pass and pays only the first. Run back to back with the pass removed, a 50 KB heredoc took 154ms (146–186ms) with it against 149ms (147–163ms) without, a 5 KB heredoc 31ms against 33ms, and chained x360 629ms against 630ms. A single `&` costs about what none does: the one-`&` heredoc took 31ms against 31ms at 5 KB and 158ms against 154ms at 50 KB, because only line 1 is split. An input with many `&` pieces pays to walk each: the two-`&`-per-line heredoc took 239ms against 163ms without the pass. Without the pass, the `git log -1 &` x360 command cost 40ms only because its later `git` words were never reached; with it, all 360 are.

The pass's worst case is a payload where every line has both a standalone `git` word and several `&`, because each line is then walked once as a fragment and again as several pieces. Timed in-shell on `_lib_command_concludes_marker_gated_commit` (plugin lib, no hook fork), median of 5 runs after 1 warm-up at a load average of about 4, the synthetic heredoc `cat <<'EOF'`, N lines of `Run git status & git log & git diff & git show & done now.` (4 `&` and 4 `git` words per line), then `EOF` took:

| Payload | `&` replaced by `+` (no pass 2) | As written (pass 2 runs) | Ratio |
|---|---|---|---|
| 4,381 B, 74 lines | 152ms (141–393ms) | 658ms (604–730ms) | 4.3x |
| 50,991 B, 864 lines | 1568ms (1451–1898ms) | 8019ms (7824–8363ms) | 5.1x |

Ratio is the as-written time over the no-pass-2 time, so pass 2 alone costs about 3–4 times pass 1 on this shape. GH-1180 owns the follow-up for this cost. Its success criterion below names inputs with no `git` word, so an early exit alone does not bound this shape, which has `git` words on every line.

Follow-up owner: GH-1180. GH-1063 tracks the fragment splitter this predicate shares, and it can close without meeting this criterion. Success criterion: allow-path cost for an input containing no `git` word does not scale with fragment count. A fork-free glob test on the already quote-stripped string, ahead of the split and loop, is the lighter primitive to try first. The fix lands in the shared predicate in both `_lib.sh` copies, whose function bodies the closure-equality tests hold byte-identical. Every hook that calls either public wrapper pays this per-fragment cost on each Bash call. The measurements above cover the plugin's `require-skill-review.sh` alone.

## Known gap: the plugin matcher

The trigger is the fragment-aware predicate `_lib_command_concludes_marker_gated_commit`, duplicated into the plugin lib from the stowed one. `git -C <dir> commit`, `git -c <config> commit`, `env git commit`, absolute-path, and quoted-`git` commits reach the gate, as do commits after a bare `&` (`git add . & git commit`, `sleep 1 & (git commit)`), which `_lib_split_fragments` leaves unsplit.

Reaching the trigger is not the gate evaluating the commit. The gate evaluates the index of the repository at the payload `cwd`, as it stands before the Bash call runs. Any commit that takes content the gate does not read from that index is a residual. Each of the three classes below is named by example, not as a closed list:

- **A commit in another repository or tree.** The trigger fires, but the gate evaluates the `cwd` repository, so unreviewed gated content staged in the target passes. Examples are `git -C <other-repo> commit`, a `--git-dir` or `--work-tree` target, a `cd <tree> &&` before the commit, and a `GIT_*` environment-variable prefix.
- **Working-tree content the commit stages itself.** The commit takes tracked working-tree content that is not in the index when the hook reads it, so the gate sees no gated change and disarms. Examples are `git commit -am x` and a `-- <pathspec>`.
- **Same-call staging.** Nothing gated is staged yet when the gate reads the index, so it disarms. An example is `git add . & git commit`.

No stowed gate evaluates a redirected repository, tree, or index (`-C`, `--git-dir`, `--work-tree`, `cd`/`pushd`/subshell, `GIT_*` prefixes). The stowed `deny-invisible-commit-content.sh` backstops only two shapes: a `-a` or pathspec commit, except behind a read-only first command (GH-1063), and same-call staging when the first git subcommand mutates the index. A plugin-only install has neither backstop.

`TestSkillReviewGateEvaluationScopeResiduals` pins the `-C`, `cd`/`pushd`/subshell, `-am`, and `&`-glued same-call shapes, and is the authoritative enumeration.

The trigger also over-matches. It is a quote-blind word walk, so a command that only names a commit command as an argument (`echo git commit`, `grep "git commit" docs/x.md`, `git log --grep "fix & git commit hook"`) reaches the gate and denies while gated content is staged with no marker. Fail-toward-deny is the accepted posture, pinned by `test_accepted_over_deny_command_naming_a_commit_reaches_gate`.

`bash -c "git commit"` and `eval "git commit"` are recognized: quote-stripping leaves `git commit` as bare words. What bypasses the trigger, as it does the stowed code-review gate, is a commit the argv walk does not parse as `git` then `commit`:

- A variable or `${IFS}` standing in for a word (`c=commit; git $c`).
- A git alias (`git ci`).
- A script file (`bash ./do-commit.sh`).
- A `\`-newline continuation between `git` and `commit`.
- A value of a global flag that precedes the subcommand (the value-taking list in `_lib_git_argv_from_subcmd`) that contains whitespace, which requires quotes, or a fragment operator (`$(`, backtick, `;`, `&&`, `||`, `|`), quoted or not. The argv walk breaks on such a value, so the subcommand is not found. Examples are `git -C "dir with space" commit` and `git -C $(pwd) commit`.
- Text built by command substitution or decoded from an encoding and piped to a shell.
- An env-var assignment whose value ends in `/git`, ahead of the real `git` word (`X=/a/git git commit`). The walk takes the assignment as the `git` word, so the subcommand it reads is the real `git`.

`test_known_bypass_commit_text_assembled_outside_the_command_string_is_not_recognized` pins the first five bullets, and its parametrization is the authoritative list of shapes for the fifth.

## Known gap: the ungated clean merge

A conflict-free `git merge`, `cherry-pick`, or `revert` still reaches a commit with no gate firing at all. Each creates the commit inside the initiating command itself, with no separate `git commit` tool call for any `PreToolUse` gate to see.

This gap is load-bearing: it is the dominating bypass that makes this gate's disarm, and `_lib_gate_diff_base`'s forgeable anchors, admissible in the first place. A Bash-capable actor who wanted past `/skill-review` entirely never needed a forged base or a timing race, because a clean merge already gets there with zero gates and no marker left behind.

`git merge --continue`, `cherry-pick --continue`, and `revert --continue` complete a resolved conflicted state the same way, and they reach the gate through the trigger predicate. `git rebase --continue` does not: the predicate deliberately omits it, because REBASE_HEAD cannot reach a trusted anchor in the ordinary case, so a review marker demanded there would force a full review at every conflicted step of a rebase. The carve-out is documented in the header of `_lib_command_concludes_marker_gated_commit`, in `_lib.sh`.

## The anchor is the fully-qualified remote-tracking ref

`_lib_gate_diff_base` checks ancestry against `refs/remotes/origin/<default>`, not the short name `origin/<default>`. Git resolves a local branch or tag named `origin/<default>` ahead of the remote-tracking ref, so a local ref cannot supply the anchor.

A direct write to `refs/remotes/origin/<default>` itself remains the accepted forged-anchor residual. That write needs no gitdir access: `git fetch . +<ref>:refs/remotes/origin/<default>` performs it with porcelain alone.

`_lib_default_branch_or_guess` still probes the short name `origin/<candidate>` when `origin/HEAD` is unset. That is intentional and a known gap for a follow-up.

- It returns only a branch name.
- The gate's fully-qualified ancestry check then rejects a name whose remote-tracking ref does not exist, and falls back to the `HEAD` anchor.

## Declined alternative: per-path parent-blob comparison

A per-path comparison of each staged blob against a parent's blob could replace the synthesized base tree.

A resolution that discards upstream's gated content defeats a per-path parent-blob comparison by one of two mechanisms, both pinned by `test_resolution_discarding_upstream_gated_content_denies`. Restoring the file to HEAD's blob (`git checkout HEAD -- <path>`) stages a blob equal to HEAD's. `git rm` of an upstream-added file removes the path entirely, leaving no staged entry to compare. That file never existed at HEAD either, so there is no HEAD blob to compare it against.

A gated file that only the branch changed also stages a blob equal to HEAD's.

Telling those two shapes apart needs the merge-base blob. A comparison against a single parent does not have it. A comparison against both parents does not have it either. The synthesized `merge-tree` base does.

The code-review gate has no conflict-marker scan, so a fix confined to the skill-review gate does not reach it.

The plan-review gate's exposure to the hidden conflict is unassessed.

## Reopening criterion

Reopen the reasoning in this file if the ungated clean-merge gap above is ever closed. Until then, every residual recorded here is dominated by that gap and by `_lib_gate_diff_base`'s forgeable-anchor posture (`docs/design-decisions/merge-tree-base-recipe-for-gate-diff-base.md`), and tightening any of them individually would not change what a motivated Bash-capable actor can already do for free.

A code-level defect found in the revert bracket or the conflict-marker scan also reopens the choice of base.
