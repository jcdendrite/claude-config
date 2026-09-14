# Reviewer responsibility bounded to the diff under review, uniformly, with default-branch and cumulative-pass guards

*2026-08-29. Formerly `docs/design-decisions.md` §34.*

The redesign applies one uniform clause to every Change-type row: a spawn's exhaustive-enumeration duty is bounded to the diff already handed to it, but a defect outside that boundary the change causes, activates, or newly reaches stays in scope for the spawn's flagging duty. No per-row exemption list is needed to protect `ciso-reviewer`, `staff-sdet`, or any other row's cross-change reasoning.

The boundary computes no new ref: it is simply the diff a spawn is already being handed (`git diff --cached` for the commit-gate pass, the same basis `require-code-review.sh` hashes), restated as file paths and line ranges for reviewers without `Bash`.

`ready-for-review`'s cumulative PR-vs-base pass gets zero narrowing, enforced by a positive precondition rather than an opt-out flag: narrowing applies only when the diff under review is the currently-staged diff. Every context failing that precondition — the cumulative pass, a presentation-path review, an ad-hoc review — enumerates the full diff automatically, with no exclusion list to maintain. `ready-for-review/SKILL.md` carries its own mirrored applicability statement rather than relying solely on `code-review`'s precondition, because a session mid-way through several re-review rounds is exactly the case most likely to misclassify the cumulative pass as "just another round."

The precondition additionally requires `HEAD` not be the repository's default branch. A direct commit to the default branch is followed by no `ready-for-review` cumulative pass at all, so the guard forces full, unnarrowed enumeration of that one commit. Worktree enforcement makes this rare in this repo specifically, but `claude/` installs to every stow consumer and not every consumer opts into worktree enforcement. Cross-commit protection generally comes from the causal-reach clause, applied uniformly to every row, not from this guard specifically.

Responsibility-narrowing saves fix-loop churn, not reviewer reads — every non-prose reviewer still opens whole files for context, unchanged. Only the comment/prose row is additionally match-narrowed (it does not spawn at all when the boundary carries no comment/durable-doc prose), because it alone is closed-form with no cross-file reach; that is the one genuine token-read saving this design delivers.

**Named residual, not fixed here.** Two `/code-review` invocations against the same staged state with no commit between them see an identical boundary under this design — it does not distinguish "already cleared this round" from "never reviewed" within a round, because both hand the same diff. `SKILL.md`'s existing requirement to pass prior findings plus what's been applied on re-review is the standing mitigation.

## Sources

- `.claude/plans/scope-code-review-delta-rounds.md` — full assumption ledger, mechanism list, and out-of-scope residuals.
