# `ready-for-review`'s verification cache: a sixth content-addressed marker kind, keyed on the tree

*2026-09-11.*

`ready-for-review` step 2 re-executes the project's full check/lint/test suite on every pass, including a resumed session or a mid-gate handoff where nothing has changed since a prior clean pass. No durable record of a clean verification existed for a resuming session to read, so every pass re-ran the commands from scratch regardless of whether the tree they ran against had already been verified.

A `verification` marker kind closes this: its value is `git rev-parse HEAD^{tree}`, the content-address of the tree step 2 actually executes. Step 2 runs the project's commands against the whole checked-out tree, not against a diff, so the tree's hash is the key that describes what step 2 consumed. `marker.sh write verification` stores it only after every selected command has run and passed; `marker.sh check verification` reads it back before step 2 selects any commands, and a hash match skips straight to step 3.

This sits deliberately beside `cumulative-review`'s diff-keyed marker rather than reusing its key. The general rule spanning both: each cache keys on the content-address of what its own step actually consumes — step 2 executes the tree, so it hashes the tree; step 3 reads the PR-vs-base diff, so it hashes the diff. One rule, two different preimages, because the two steps consume different things.

**The read is age-bounded.** A tree hash cannot see the installed environment — the interpreter version, a rebuilt venv, gitignored config, a dependency resolved against a moved registry — so a hash match alone cannot authorize a skip indefinitely; something outside the tree can make yesterday's clean pass no longer mean today's pass would also be clean. `marker.sh check verification` therefore treats a hash-matching marker as `no-match` once the marker file is older than `VERIFICATION_CHECK_MAX_AGE_SECONDS` (default 14400, 4h). 14400s (4h) is a deliberately chosen, round default, like [§62](markersh-check-code-review-gains-an.md)'s 86400s for `check code-review` — not derived from measured staleness data. It is sized to expire between working sessions, which is where an interpreter bump or a rebuilt venv would land, while covering the motivating window:

- a resumed session
- a mid-gate handoff
- a step-3/4 fix loop back into step 2

A wrong default here degrades to "step 2 runs once more than strictly necessary," not to a missed check.

**The bound lives on `check`, not on `write` or `status`.** `write` and the shared tree-hash recipe stay unbounded, matching every other marker kind's write arm. `status`'s `verification` line reports raw hash-match state with no age bound, the same way its `code-review` line does while `check code-review` applies its own bound — `status`'s documented contract is to report every completion marker's current state, and folding an age bound into it for one kind alone would make its `historical`/`live` vocabulary ambiguous between "hash doesn't match" and "hash matches but aged out." `check` already owns age-bounded advisory-skip semantics for `code-review`, so `verification` reuses that home instead of inventing a second one.

**Four residuals, named rather than engineered away:**

1. **A narrow false-hit window at write time.** The write arm recomputes `HEAD^{tree}` at write time rather than capturing it earlier and consuming it later, the way `cumulative-review`'s write arm consumes a subject captured at step 3's entry ([§50](cumulative-review-marker-not-recomputed.md)). That two-phase shape exists because `cumulative-review`'s diff subject drifts with no local action at all — the PR's base can move on the remote between a review and a later write with zero commits in this worktree. `HEAD^{tree}` has no such external drift channel: it changes only when this worktree commits, rebases, or resets. Importing the two-phase shape would defend against a gap that doesn't exist here. The residual it would have closed is narrower still — a commit landing strictly between the checks passing and the write, inside one step-2 pass. The gate's own flow never produces that ordering.
2. **A submodule's dirty working tree is invisible to a tree hash.** A git tree addresses tracked, committed content only; an uncommitted change inside a submodule's own working tree sits outside the superproject's tree hash even though step 2's commands may execute it. This is git's object model, not a gap this design can close from the superproject side.
3. **Self-attestation, at parity with the other five kinds.** `write verification`'s only precondition is that the tree hashes to something; nothing correlates the write to proof that the checks it claims to cover actually ran and passed. This is not a new privilege boundary — every existing marker kind already carries this exposure, and closing it needs a control that doesn't exist anywhere in the marker architecture.
4. **No operator-facing invalidation lever beyond the age bound or an actual commit.** There is no `marker.sh` subcommand that revokes a `verification` marker early. If a session determines the cache said clean but that's wrong — an environment change the tree hash can't see — the only way to force a re-run before the 4h window expires is to delete the stale file directly under `verification-markers/`.

## Sources

- `.claude/plans/rfr-verification-cache.md` — full assumption ledger, mechanism list, and out-of-scope residuals.
