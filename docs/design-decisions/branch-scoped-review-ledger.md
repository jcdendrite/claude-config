# Branch-scoped review ledger with a per-session fallback

*2026-09-30.*

The `/code-review` ledger (`claude/.claude/scripts/review-ledger.sh`) is keyed by branch, so a new session on the same branch can read the branch's earlier findings and dispositions.
A session-keyed ledger loses them at every handoff, next-day session, and `/clear`.
`show` prints the rows, and the session dashboard prints counts and a date span, never finding text.
A `SETTLED` disposition records a human's or a consult's decision to keep text as it is.
A repeat of an earlier finding at an unchanged site carries that decision without a new stop, when the script accepts the carry.
`render` builds the PR-body block from the ledger's live decisions, not from one round's chat output.

## Keying and fallbacks

The ledger file is `<repo-hash>.<branch-hash>.jsonl` under the config dir's `review-narrative-ledger/` directory.
That is the same `<repo-hash>.<branch-hash>` key that `_lib_reviewer_round_state_key` gives the round-3 gate, so "this branch" has one definition.
The branch half is a sha256 of the full branch name, so two branch names never collide on a slug.

`_lib_review_ledger_path` in `claude/.claude/hooks/_lib.sh` resolves the file.
Hooks need the resolver, which is why it lives in `_lib.sh` and not in the script.
Its header comment states the output shape and exit contract.

These cases fall back to the per-session file `<repo-hash>.<session-id>.jsonl`:
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
`show`'s header names the oldest row's date, and `docs/scripts.md` tells the engineer to delete the file `show`'s header names.

Ancestry filtering fails.
Rows record no commit SHA, so a filter would need a new field.
Default-branch sync in this repo is a rebase, which rewrites every commit a branch recorded, so each such row would fail the ancestor test.
A reflog creation filter needs a helper at every reader of the ledger plus reflog behavior that was not verified.
Branch names in practice embed an incrementing ticket number, so reuse is unlikely and easy to detect.

**Path relocation starts a fresh ledger.**
The repo half of the key is the worktree path, so the same branch checked out at another path resolves to a different file.
The worktree flow keeps one worktree per branch, which makes this rare.

**A branch rename starts a fresh ledger.**
The branch half of the key hashes the branch name, so `git branch -m` leaves the old file behind and the renamed branch resolves to a new, empty one.
A published PR block keeps its rows through the kept-row rule, and an old id reads as not in this branch's ledger for `--ref`.

**A mid-rebase append is stranded in the session file.**
While HEAD is detached, `show` reads only the session file, so the branch's rows are hidden until the rebase ends.
After the rebase, only the session that wrote the mid-rebase rows sees them.
A later session and the dashboard's branch summary never do, and the rows stay in the session file until the retention sweep removes it.
While detached, `show`'s max round comes from the session file alone, so a compaction or resume mid-rebase restarts numbering from that file's maximum.
Reading `rebase-merge/head-name` would make the ledger's key diverge from the round-3 gate's shared key.

**A change in default-branch detection moves a branch between scopes.**
Detection has inputs that can differ between calls:
- What `origin/HEAD` names.
- Whether `origin/HEAD` resolves at all. When it is unset or dangling, the `origin/main`, `origin/master`, and `origin/develop` refs decide, and with none present the name list decides.
- Whether the capped git call completes. A timeout reads as unresolved.

Any of these can move a branch between branch and session scope, and only the timeout is transient.
A transient timeout splits one branch's rows across two files with no ref changed.
Rows written before the move stay in the file the earlier resolution chose.
After a move to session scope, `show` hides the branch file, as in the mid-rebase case.
After a move to branch scope, earlier session files stay readable only by the sessions that wrote them.

The fallback has scope limits that are not moves.
A branch named like `main`, `master`, or `develop` is still branch-keyed when `origin/HEAD` names a different branch.
Long-lived non-default branches such as `release/*` are branch-keyed like any feature branch.

**A branch already in flight starts with an empty branch ledger.**
Its first new session re-decides its earlier findings once.

**A range that is too narrow reopens nothing.**
An edit inside the decided block but outside the hashed lines does not reopen the decision.
A range of short common text, such as a lone `}`, also matches identical text elsewhere, in any file, when a reviewer cites that line.
A SKILL.md paragraph is one line, so any edit anywhere in it reopens every decision anchored to it, which fails toward a stop.

**A carry binds the decided text, not its site.**
The script checks that the carry's range hashes to the decision's `site_hash` and does not compare paths.
A block-length copy of the decided text in another file therefore carries, as a lone `}` does.
Accepting a carry at a new path is what lets a renamed file keep its decisions.

**Partial staging binds unstaged edits.**
Under `git add -p`, the hash covers the block's working-tree text, including unstaged edits that the commit-gate reviewers did not see.

**A concurrent fork can leave two live successors.**
Validation runs before the append lock, so two sessions that validate at the same moment can both land.
A carry validated against a decision that another session closes before the carry lands is still appended, and `render` omits it.

**Kept rows retire only by hand.**
`render --pr-json` keeps a PR-block row whose last cell is not a ledger id, which covers rows from before ids existed and rows of a swept ledger.
Only a hand edit of the PR body retires such a row, and deleting the ledger file clears none of them.
`render` also regenerates a row planted by hand under the Settled heading as if it were generated, which takes a PR-body editor and so lies outside the cooperative-agent model.

**An idle branch loses its ledger at the retention sweep.**
Every `append` runs a sweep that removes `*.jsonl` files whose mtime is more than 30 days old, and that window is fixed.
A long-parked branch therefore loses its recorded rows and its round count.

**A decision whose block was fixed or removed stays live until retired.**
Liveness never consults `site_hash`, so `render` lists such a decision until a non-carry row names it.
The `/code-review` prose mitigates this by requiring `--ref <id>` on an `ADDRESS` or a fresh `DEFER` at a live decision's site, but a block deleted and never re-raised still renders.

**A subagent can retire an engineer decision.**
`ADDRESS --ref` retires an engineer decision, an enforcement-invariant one included, and the script cannot identify its caller.
The hook bars a subagent only from rows carrying `--engineer-quote`, so its `ADDRESS --ref` lands.
The next `render` drops the decision's row from the PR block, and only `show` still lists the `ADDRESS` row.
A `gh pr edit` from the same agent deletes the published row as directly, so gating this row would not protect it.
A retired decision never carries, and an invariant-class repeat of its finding is still never `DEFER`-eligible.

**A `DEFER` carry is the one unattended carry class.**
It applies with no engineer stop, and its guards are the unchanged hashed text and the orchestrator re-running the closed list.
The prose has the orchestrator range a `DEFER` as the whole block, and a one-line range left unwidened is the accepted exposure.

**A new PR has no block until the post-create edit lands.**
The no-PR path creates the PR from a body without the block, then publishes the block through `render` and `gh pr edit`.
Between create and edit the PR carries no Deferred or Settled tables, and a failed or denied edit leaves it that way until the next `render` run.
Whether a `gh pr view` immediately after create returns the new PR's body was not verified.

**The redaction gate is not a completeness check.**
It blocks the shapes it detects, and a structural fingerprint, private-corpus provenance or a credential value passes it (`docs/scripts.md` states the credential case), so the engineer's quote is published as typed.

## Settled decisions and carries

`SETTLED` takes `--decided-by engineer`, `plan-architect`, or `carry`.
An engineer decision stores the engineer's words verbatim in `--engineer-quote`, at most 200 characters, rejected and never truncated.
A consult decision stores no quote.
`ADDRESS` with a rationale cannot record a keep, because `author_outcome.py` counts every `ADDRESS` row as a failure and `show` cannot tell a keep from a fix.
A `DEFER` criterion cannot stand in either, because `DEFER` is a closed list for real defects and an enforcement-invariant finding is never `DEFER`-eligible.
Putting the quote inside `--rationale` would mix the orchestrator's prose with the engineer's words.

Every row gets an `id`, the first 12 hex digits of the row's sha256, and `--ref <id>` links a row to one earlier decision.
Every rule is one hop: a decision is live until a non-carry row names it, `ADDRESS` closes it, and a fresh `DEFER` or `SETTLED` supersedes it.
An engineer decision is superseded only by an `ADDRESS` or an engineer `SETTLED`, so a `DEFER` or consult row cannot retire an engineer's words from the PR body.
Twelve digits stay below the redaction gate's 32-digit hex-run detector, which is why an id can render in the PR body and a full hash cannot.

A `DEFER` or `SETTLED` row with a range-form `--source` stores a `site_hash`: the sha256 of those lines of the working-tree file, kept to 12 digits.
A carry is accepted only when its `--cited-line` lies inside the carry's own range and that range hashes to the decision's `site_hash`.
A test of "this round's diff leaves the site untouched" fails in both review timelines.
A commit-gate round diffs only the staged delta, so it cannot see an earlier commit's edit to the block.
A cumulative pass diffs the whole branch against its base, so every block the branch wrote sits inside the diff and nothing would carry.
A whole-file hash reopens every decision in a file on any edit, and a commit anchor fails because settled text is often uncommitted at decision time and a rebase rewrites the SHA.
The hash reads the working tree, which holds the same bytes before and after a commit and across a rebase, and which reviewers' cited line numbers refer to.
The orchestrator names the range at decision time and at each carry, because turning a cited line into a block takes judgment the script cannot supply.
A range that is too wide costs only later stops.

`--carry-forward` is an affirmative per-decision attribute, accepted only on an engineer `SETTLED` with a range-form source and never beside `--enforcement-invariant`.
An engineer decision without it never carries, so a forgotten flag costs a stop and never a carry.
Making decisions carryable by default fails open on a forgotten label, and a per-carry attestation flag has one valid value, so it would be ritual.
A `DEFER` decision needs no such flag, because a `DEFER` carry restates its criterion and the script rejects a mismatch.
`--enforcement-invariant` is a label with local checks and not a gate: the script accepts it only on an engineer `SETTLED`, rejects it beside `--carry-forward`, and rejects a successor that drops it.
It never classifies a finding, so the prose of `/code-review` keeps the rule that an invariant-class finding is asked again on every re-raise and never carries.
Whether a repeat names the same failure mode stays the orchestrator's judgment.
The hash and the cited-line check limit that judgment to unchanged text that contains the cited line.

A carry is logged `--decided-by carry`, never with the engineer's words, and prints one line naming its decision, date, round, and the command that reopens it.
A reopen is `ADDRESS --ref <id>`.

## Render

`review-ledger.sh render` builds the PR-body block between `<!-- code-review:deferred:start -->` and `<!-- code-review:deferred:end -->`, the delimiters that blocks already in open PRs use.
The block holds a Deferred table and a Settled table of live decisions, with each carry beneath its decision and invariant decisions under their own heading.
Building the block in skill prose could not be tested and invites paraphrasing a quote.
`--pr-json` returns a whole PR body with the block replaced and every other byte identical, and keeps a row in the old block whose last cell is not a ledger id.
`--out` receives the result only on success, and the skill runs `gh pr edit --body-file` itself, only after a `changed:` line, so the redaction and escaped-backtick gates read the body file.
A script-internal edit would bypass both gates.
A shell redirect to the body file would leave an empty file when `render` fails, and the redaction gate does not reject an empty file, so `gh pr edit` would blank the body.

## Which store is canonical

The branch ledger is canonical for dispositions.
`/ready-for-review`'s `agent-reviews/code-review-dispositions-<suffix>.md` record is canonical only for the Cap's pass accounting.
The record stays because it carries these things the ledger lacks:
- which rounds were cumulative passes;
- cap rows;
- which reviewer raised a row;
- the per-row Outcome column.

Folding the record into the ledger is a follow-up.
It would cost a schema addition for each of those and a rewrite of the Cap clause that tests pin.
It would gain ordering by a script-stamped `event_time` and an exact branch key.
Evidence that rows duplicated between the two stores drift apart in practice would change this call.

## Analysis join

`author_outcome.py` attributes a row to session S when the row's `session_id` is S, or when it has no `session_id` and sits in `*.S.jsonl`.
It groups S's rows into round blocks and maps the k-th transcript round-open to the k-th block, by rank and not by `round` value.
A value join cannot work, because a later session on a branch starts at a round number past the earlier sessions' maximum.

`docs/transcript-analysis.md` "Round-number-sequence check" states when S is excluded from the headline.
Two files claiming one round repeat a round value, so the cross-worktree race guard still holds.
A session that reviews on two branches and whose round values fall (round 5 on one branch, then round 1 on another) is excluded.
That exclusion is deliberate, and a test pins it.

Rounds of an excluded session are not classified.
The data-quality counters "rounds with a clean marker write but no round-keyed ledger rows" and "authoring_agent inconsistent with the transcript join" therefore stop counting them.
Both read the round-to-row join that the exclusion marks untrusted.

The positional join has a silent residual.
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

The fix-loop convergence and round-3 consult gate decisions reject `review-ledger.sh` as a home in part because it is session-keyed.
This decision supersedes the session-keyed statement in each and leaves the decisions themselves standing.

[The fix-loop convergence decision](ready-for-review-fix-loop-convergence.md) rejects the ledger for the disposition record because it is "keyed by session rather than branch" and has no outcome for a contradiction consult's keep verdict or a cap row.
The keying premise no longer holds, and a consult's keep verdict now has a slot: a `SETTLED` row, for which the script requires a repo-relative `--source`.
The script requires the range form only for a carry or an engineer `--carry-forward`, so the range form on a consult's keep is a prose rule and not a script check.
The ledger still has no cap row, pass kind, reviewer, or Outcome, so the persisted `agent-reviews/` record stays, canonical only for the Cap's pass accounting.

[The round-3 consult gate decision](round3-plan-architect-consult-gate.md) rejects the ledger as a round counter because it is "session-keyed, disable-able".
The keying premise no longer holds, and the sentinel that made the ledger disable-able is no longer honored.
The gate's `(HEAD, staged-diff)` counter stays, because the other grounds that decision lists do not depend on the keying.

## Rollback

A revert leaves the branch files in place.
The per-append retention sweep removes `*.jsonl` files whose mtime is more than 30 days old, and `review-ledger.sh clear-stale` removes them on a window of Claude Code's `cleanupPeriodDays` floored at 30 days.
The reverted writer never reads a branch file, so the leftover files change no behavior.
On an open PR, the next `/code-review` round then replaces the delimited block with that round's `DEFER` rows in the four-column shape, dropping the Settled table and any kept rows.
