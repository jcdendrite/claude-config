# `marker.sh check code-review` gains an age bound; `write`/commit-gate stay hash-only

*2026-08-24. Formerly `docs/design-decisions.md` §62.*

`marker.sh check code-review` — the short-circuit `/code-review`'s Step 0.1 consults before dispatching its specialist panel — treats a hash-matching marker as `no-match` when the marker file's mtime is older than `CODE_REVIEW_CHECK_MAX_AGE_SECONDS` (default 86400, 24h); a hash match alone is not sufficient. A marker written weeks or months ago for a diff that happens to recur (a revert-then-reapply, or two branches independently producing byte-identical staged content) would otherwise let `check` skip the specialist panel indefinitely.

The bound applies only to `check`, not to `write`, the shared repo-hash recipe (`_marker_lib_repo_hash` in `_lib.sh`), or `require-code-review.sh`'s commit-time enforcement gate — all three stay hash-only and unbounded, deliberately. `check` is an advisory cache-skip inside `/code-review`'s own Step 0.1: its only job is deciding whether to re-run a review, and erring toward re-running an unnecessary review costs a few minutes, not correctness. `require-code-review.sh` is the actual enforcement gate on `git commit`; an age bound there would need its own justification (a hash match plus a time window is a weaker authorization story than a hash match alone, not a stronger one) and is out of this fix's scope.

86400 (24h) is a deliberately conservative, round default, not derived from measured marker-staleness data. Blocking this fix on a corpus measurement of how often a hash-matching marker recurs long after it was written was considered and rejected as disproportionate: the fix is a freshness check on an advisory short-circuit, not a load-bearing security boundary, and a wrong default here degrades to "the panel runs once more than strictly necessary," not to a missed review.

## Sources

- `claude/.claude/scripts/marker.sh` — the `check code-review` arm.
- `claude/.claude/skills/code-review/SKILL.md` Step 0.1 — the caller that trusts a `check` match as a skip-review signal.
- `claude/.claude/hooks/require-code-review.sh` — the commit-time enforcement gate, unchanged.
