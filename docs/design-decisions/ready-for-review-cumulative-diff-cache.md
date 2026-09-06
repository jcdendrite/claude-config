# `ready-for-review`'s cumulative-diff review cache: a fifth content-addressed marker kind

*2026-09-02. Formerly `docs/design-decisions.md` §44.*

`ready-for-review` step 3 ran a mandatory, fully-unnarrowed `/code-review` pass over the entire PR-vs-base diff on every push to a branch with an open PR, including a push that only rebases or merge-syncs onto a moved default branch with zero conflicts and zero content change. The gating marker (`ready-for-review-markers`) is keyed on the exact HEAD SHA, not diff content, so a conflict-free rebase always re-arms it and forces a full specialist-reviewer re-run from scratch.

A `cumulative-review` marker kind closes this: its value is the sha256 of `pr-diff-against-base.sh`'s output. It is written by `marker.sh write cumulative-review` at the end of a clean step-3 pass and read back through `marker.sh status`'s existing completion-marker report, so a rebase that leaves the cumulative diff byte-identical reuses the prior clean review instead of re-running it.

The preimage is the diff bytes alone, deliberately excluding the merge-base SHA and the base-branch ref — folding either in would make the cache miss in precisely its motivating case, since a rebase onto a moved default branch always changes the merge-base.

**Named residual, not fixed here** ([§34](reviewer-responsibility-bounded-to-diff.md)'s "Named residual, not fixed here" is the precedent shape for recording this kind of gap rather than engineering it away). A byte-identical diff rebased onto a moved default branch is not strictly the same review object: a reviewer's ripple and causal-reach judgment can depend on code outside the diff, and a clean rebase surfaces textual conflicts only, not semantic ones. This residual is not closed by folding the base ref into the hash — that would defeat the cache's own motivating case, as above — so it is compensated instead: step 2's verification runs against the rebased tree on every pass and is never cached, and CI runs on every push. The cache skips reviewer judgment over bytes nobody changed, not verification of the tree.

**A second named residual: the write is a zero-evidence self-attestation.** `write cumulative-review`'s only precondition is that the diff hashes to something; nothing binds the write to proof that a review actually ran. This is not a new privilege boundary: every existing marker kind is already self-attested, with no hook correlating a write to completed review work. It does, however, convert step 3 from prose-"unskippable" into a silently skippable step. The accepted mitigation is the same one already in place for the base-move residual above: step 2's verification and CI still run unconditionally on every pass, cache hit or not. [§50](cumulative-review-marker-not-recomputed.md) closes the narrower defect this residual was later found to also cover — a write that stamped a diff it never reviewed at all — without closing this residual itself, which stays open at parity with the other four marker kinds.

## Sources

- `.claude/plans/rfr-cumulative-diff-cache.md` — full assumption ledger, mechanism list, and out-of-scope residuals.
