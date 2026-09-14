# `ready-for-review`'s verification cache: a sixth content-addressed marker kind, keyed on the tree

*2026-09-11.*

`ready-for-review` step 2 re-executes the project's full check/lint/test suite on every pass, including a resumed session or a mid-gate handoff where nothing has changed since a prior clean pass. No durable record of a clean verification existed for a resuming session to read, so every pass re-ran the commands from scratch regardless of whether the tree they ran against had already been verified.

A `verification` marker kind closes this: its value is `git rev-parse HEAD^{tree}`, the content-address of the tree step 2 actually executes. Step 2 runs the project's commands against the whole checked-out tree, not against a diff, so the tree's hash is the key that describes what step 2 consumed. `marker.sh write verification` stores it only after every selected command has run and passed; `marker.sh check verification` reads it back before step 2 selects any commands, and a hash match skips straight to step 3.

This sits deliberately beside `cumulative-review`'s diff-keyed marker rather than reusing its key. The general rule spanning both: each cache keys on the content-address of what its own step actually consumes — step 2 executes the tree, so it hashes the tree; step 3 reads the PR-vs-base diff, so it hashes the diff. One rule, two different preimages, because the two steps consume different things.

**The read is age-bounded.** A tree hash cannot see the installed environment, so a hash match alone cannot authorize a skip indefinitely; something outside the tree can make yesterday's clean pass no longer mean today's pass would also be clean:

- the interpreter version
- a rebuilt venv
- gitignored config
- a dependency resolved against a moved registry

`marker.sh check verification` therefore treats a hash-matching marker as `no-match` once the marker file is older than `VERIFICATION_CHECK_MAX_AGE_SECONDS` (default 14400, 4h). 14400s (4h) is a deliberately chosen, round default, like [§62](markersh-check-code-review-gains-an.md)'s 86400s for `check code-review` — not derived from measured staleness data. It is sized to expire between working sessions, which is where an interpreter bump or a rebuilt venv would land, while covering the motivating window:

- a resumed session
- a mid-gate handoff
- a step-3/4 fix loop back into step 2

A TTL that is too short only costs a redundant re-run; a TTL that is too long widens the window in which a false-clean marker can affect other sessions (residual 6, below). That asymmetry is why this default is deliberately narrower than `check code-review`'s 24h.

**The bound lives on `check`, not on `write` or `status`.** `write` and the shared tree-hash recipe stay unbounded, matching every other marker kind's write arm. `status`'s `verification` line reports raw hash-match state with no age bound, the same way its `code-review` line does while `check code-review` applies its own bound — `status`'s documented contract is to report every completion marker's current state, and folding an age bound into it for one kind alone would make its `historical`/`live` vocabulary ambiguous between "hash doesn't match" and "hash matches but aged out." `check` already owns age-bounded advisory-skip semantics for `code-review`, so `verification` reuses that home instead of inventing a second one.

**Seven residuals, named rather than engineered away:**

1. **A narrow false-hit window at write time.** `HEAD^{tree}` changes only via a local commit, rebase, or reset, so the false-hit window is limited to a commit landing between checks passing and the write within one step-2 pass, which the gate's own flow never produces. A narrower instance of this same TOCTOU sits inside `write verification`'s own two-git-call sequence: the `git status --porcelain` clean check and the subsequent `git rev-parse HEAD^{tree}` are two independent, unlocked calls with no atomicity between them. A commit, amend, or reset by a concurrent process landing in that window therefore records a marker for a tree state the guard never actually validated.
2. **A submodule's dirty working tree is invisible to a tree hash.** A git tree addresses tracked, committed content only; an uncommitted change inside a submodule's own working tree sits outside the superproject's tree hash even though step 2's commands may execute it. This is git's object model, not a gap this design can close from the superproject side.
3. **Self-attestation, at parity with the other five kinds.** `write verification`'s only precondition is that the tree hashes to something; nothing correlates the write to proof that the checks it claims to cover actually ran and passed. This is not a new privilege boundary — every existing marker kind already carries this exposure, and closing it needs a control that doesn't exist anywhere in the marker architecture.
4. **No operator-facing invalidation lever beyond the age bound or an actual commit.** There is no `marker.sh` subcommand that revokes a `verification` marker early. If a session determines the cache said clean but that's wrong — an environment change the tree hash can't see — the only way to force a re-run before the 4h window expires is to delete the stale file directly under `verification-markers/`.
5. **The write guard is blind to gitignored content.** `write verification`'s guard reads `git status --porcelain`, which never surfaces a gitignored path, so untracked-but-ignored content stays invisible to the cache key the same way a submodule's dirty tree does. This repo's own `.venv/` is gitignored and is exactly where `select-tests.py`, `pytest`, and `ruff` — the tools step 2 invokes — live, so an uncommitted edit inside `.venv/` that flips a check from failing to passing is invisible to both the guard and the tree hash.
6. **A false-clean marker isn't confined to the session that wrote it.** `check verification` matches on the `$REPO_HASH.` prefix alone, not `$REPO_HASH.$SESSION_ID`, so a false-clean marker written from residual 5's gap (or any other gap in the write guard) is not confined to the session that wrote it — it can short-circuit step 2 for any other session sharing the same tree state. `VERIFICATION_CHECK_MAX_AGE_SECONDS` is the sole bound on how long such a marker can affect other sessions before it ages out.
7. **The guard is a write-time snapshot, not a checks-time one.** The guard covers uncommitted content — staged, unstaged, or untracked — present at `write verification` time, but content present and influential during step 2's checks and then removed before the write runs leaves no trace for the guard to catch. A marker can be written claiming a clean pass for a tree state that, absent that now-gone content, may not have actually passed.

## Sources

- `.claude/plans/rfr-verification-cache.md` — full assumption ledger, mechanism list, and out-of-scope residuals.
