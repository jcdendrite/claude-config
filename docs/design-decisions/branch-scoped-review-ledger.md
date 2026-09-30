# Branch-scoped review ledger with a per-session fallback

*2026-09-30.*

The `/code-review` ledger (`claude/.claude/scripts/review-ledger.sh`) is keyed by branch, so a new session on the same branch can read the branch's earlier findings and dispositions.
A session-keyed ledger loses them at every handoff, next-day session, and `/clear`.
This decision ships cross-session visibility only: `show` prints the rows, and the session dashboard prints counts and a date span, never finding text.
`append` accepts only `ADDRESS`, `DEFER`, and `CLEAN`, and nothing routes a repeat finding to an earlier disposition.

## Keying and fallbacks

The ledger file is `<repo-hash>.<branch-hash>.jsonl` under the config dir's `review-narrative-ledger/` directory.
That is the same `<repo-hash>.<branch-hash>` key that `_lib_reviewer_round_state_key` gives the round-3 gate, so "this branch" has one definition.
The branch half is a sha256 of the full branch name, so two branch names never collide on a slug.

`_lib_review_ledger_path` in `claude/.claude/hooks/_lib.sh` resolves the file.
Hooks need the resolver, which is why it lives in `_lib.sh` and not in the script.
Its header comment states the output shape and exit contract.

Three cases fall back to the per-session file `<repo-hash>.<session-id>.jsonl`:
- A detached HEAD, which includes a rebase in progress.
- The branch that `_lib_default_branch_or_guess` resolves as the default.
- When no default resolves, a branch named `main`, `master`, or `develop`. This keeps a local-only `main` from growing one unbounded file.

When git cannot read HEAD, the resolver exits 1 and does not fall back to the session file.
A fallback there would split one branch's rows across two files on any git stall.
`append` then aborts and asks for a retry, and hooks print nothing.

Every row carries its `session_id`, so readers can still attribute a row to the session that wrote it.
`show` reads the resolved file plus this session's own session file, listed once when they are the same file.
It prints a stderr header naming the scope, the files, the row count, the oldest and newest `event_time`, and the max round.
Round numbers are branch-wide and stay caller-supplied: a session's first round continues from one past the header's max round.
`render` and a `--ref` check read only the resolved file, while `show` merges this session's file too, because a session-file row is not one of the branch's decisions and must not reach a PR body.

## Accepted residuals

**Branch-name reuse is not filtered.**
A branch deleted and recreated under the same name at the same path resolves to the same file.
Its old rows therefore reach `show` and the dashboard's counts.
Round numbering continues from the old branch's maximum.
`show`'s header names the oldest row's date, and `docs/hooks.md` tells the engineer to delete the file `show`'s header names.

Ancestry filtering fails for two reasons.
Rows record no commit SHA, so a filter would need a new field.
Default-branch sync in this repo is a rebase, which rewrites every commit a branch recorded, so each such row would fail the ancestor test.
A reflog creation filter needs a helper at every reader of the ledger plus reflog behavior that was not verified.
Branch names in practice embed an incrementing ticket number, so reuse is unlikely and easy to detect.

**Path relocation starts a fresh ledger.**
The repo half of the key is the worktree path, so the same branch checked out at another path resolves to a different file.
The worktree flow keeps one worktree per branch, which makes this rare.

**A mid-rebase append is stranded in the session file.**
While HEAD is detached, `show` reads only the session file, so the branch's rows are hidden until the rebase ends.
After the rebase, only the session that wrote the mid-rebase rows sees them.
A later session and the dashboard's branch summary never do, and the rows stay in the session file until the retention sweep removes it.
While detached, `show`'s max round comes from the session file alone, so a compaction or resume mid-rebase restarts numbering from that file's maximum.
Reading `rebase-merge/head-name` would make the ledger's key diverge from the round-3 gate's shared key.

**A change in default-branch detection moves a branch between scopes.**
Detection has three inputs that can differ between calls:
- What `origin/HEAD` names.
- Whether `origin/HEAD` resolves at all. When it is unset or dangling, the `origin/main`, `origin/master`, and `origin/develop` refs decide, and with none present the name list decides.
- Whether the capped git call completes. A timeout reads as unresolved.

Any of these can move a branch between branch and session scope, and only the timeout is transient.
A transient timeout splits one branch's rows across two files with no ref changed.
Rows written before the move stay in the file the earlier resolution chose.
After a move to session scope, `show` hides the branch file, as in the mid-rebase case.
After a move to branch scope, earlier session files stay readable only by the sessions that wrote them.

The fallback has two scope limits that are not moves.
A branch named like `main`, `master`, or `develop` is still branch-keyed when `origin/HEAD` names a different branch.
Long-lived non-default branches such as `release/*` are branch-keyed like any feature branch.

**A branch already in flight starts with an empty branch ledger.**
Its first new session re-decides its earlier findings once.

**An idle branch loses its ledger at the retention sweep.**
Every `append` runs a sweep that removes `*.jsonl` files whose mtime is more than 30 days old, and that window is fixed.
A long-parked branch therefore loses its recorded rows and its round count.

## Analysis join

`author_outcome.py` attributes a row to session S when the row's `session_id` is S, or when it has no `session_id` and sits in `*.S.jsonl`.
It groups S's rows into round blocks and maps the k-th transcript round-open to the k-th block, by rank and not by `round` value.
A value join cannot work, because a later session on a branch starts at a round number past the earlier sessions' maximum.

S is excluded from the headline when it has a round-keyed row and either the block count differs from the round-open count or the block round values are not strictly increasing.
Two files claiming one round repeat a round value, so the cross-worktree race guard still holds.
A session that reviews on two branches and whose round values fall (round 5 on one branch, then round 1 on another) is excluded.
That exclusion is deliberate, and a test pins it.

Rounds of an excluded session are not classified.
Two data-quality counters therefore stop counting them: rounds with a marker write but no ledger row, and rows whose `authoring_agent` disagrees with the transcript.
Both read the round-to-row join that the exclusion marks untrusted.

The positional join has one silent residual.
When one round's rows are missing and a stray block with a higher round value appears, the count and order still match, and the join misattributes where a value join would have flagged a mismatch.
`docs/transcript-analysis.md` documents this.

## Row-size check

`append` rejects a row whose final JSON line exceeds 4,095 bytes, so the line plus its newline fits the 4,096-byte bound that the atomic single-`write()` append relies on.
The check counts bytes with `wc -c`, which is locale-independent, because characters undercount bytes.
Free text is not character-filtered, and JSON writes a control character without a short escape as six bytes, so the per-field character caps do not bound the row.
The check matters more with a branch file because several sessions append to one file, and a failed lock acquisition is ignored.

## Kill-switch removal

The `.review-narrative-ledger-disabled` sentinel is not honored, and the ledger always records.
Engineer quotes logged at review stops can therefore reach PR bodies, and no replacement opt-out exists.
While the sentinel file exists, the session dashboard prints one line saying so.
Deleting the file silences the line.

## Relation to earlier decisions

Two earlier decisions reject `review-ledger.sh` as a home in part because it is session-keyed.
This decision supersedes the session-keyed statement in each and leaves the decisions themselves standing.

[The fix-loop convergence decision](ready-for-review-fix-loop-convergence.md) rejects the ledger for the disposition record because it is "keyed by session rather than branch" and has no outcome for a contradiction consult's keep verdict or a cap row.
The keying premise no longer holds.
The outcome premise still does, since `append` accepts only `ADDRESS`, `DEFER`, and `CLEAN`, so the persisted `agent-reviews/` record stays.

[The round-3 consult gate decision](round3-plan-architect-consult-gate.md) rejects the ledger as a round counter because it is "session-keyed, disable-able".
The keying premise no longer holds, and the sentinel that made the ledger disable-able is no longer honored.
The gate's `(HEAD, staged-diff)` counter stays, because the other grounds that decision lists do not depend on the keying.

## Rollback

A revert leaves the branch files in place.
The per-append retention sweep removes `*.jsonl` files whose mtime is more than 30 days old, and `review-ledger.sh clear-stale` removes them on a window of Claude Code's `cleanupPeriodDays` floored at 30 days.
The reverted writer never reads a branch file, so the leftover files change no behavior.
