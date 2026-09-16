# `comment-discipline-reviewer` deferred to `ready-for-review`'s cumulative pass

*2026-09-15.*

`code-review/SKILL.md`'s Step 0.6 governs the Change-type table's comment/durable-doc-prose row. A fix round's own diff is disproportionately comments, so a feature branch's iterative fix loop re-spawned the row every round against prose the previous round's fix had just rewritten — spending tokens and review cycles to produce the same merged-state verdict N times instead of once.

Step 0.6 defers that row instead of narrowing it: whenever the staged-diff-only boundary applies (the diff under review is exactly `git diff --cached` and `HEAD` is not the default branch), the row does not spawn at all, whatever prose the diff carries. Its exhaustive pass runs from `ready-for-review/SKILL.md`'s step 3 cumulative review instead, which already reviews the full PR-vs-base diff unnarrowed. This is not exactly once per branch: a fix round that reopens step 3 re-spawns the row again against the new cumulative diff. The cumulative-review marker is content-addressed on the reviewed subject, so it misses — and stops suppressing the re-spawn — once HEAD moves. The row's own charter, tools, and effort level are untouched — only when it is dispatched changes.

The deferral's safety rests on two guarantees:

- `gh pr create` is gated on a fresh cumulative-pass marker (the `gh`-subcommand detection is a plain-text regex that an unusual invocation can evade).
- `git push`'s no-open-PR bypass stops applying once a PR is open, so every later push re-requires a fresh marker at that HEAD.

A branch therefore cannot advance past its first open PR without a cumulative unnarrowed `/code-review` at each pushed HEAD, and that pass enumerates the deferred row the same as every other row. The row also stays in the Change-type table rather than being removed from it, so it still reaches every context that fails the boundary's precondition:

- the cumulative PR-vs-base pass
- a presentation-path review
- an ad-hoc review
- a commit to the default branch

A branch that never reaches `gh pr create` (merged locally to the default branch, or opened through a gap in `require-ready-for-review.sh`'s bypass or detection set) loses the exhaustive prose pass entirely. Accepted rather than defended: such a branch already bypasses the rest of the review pipeline the same way.

## Sources

- `claude-skills/skills/code-review/SKILL.md` Step 0.6 — the deferral clause and the Change-type row's pointer to it.
- `claude-skills/skills/ready-for-review/SKILL.md` § "3. Code review (halt on findings)" — the cumulative unnarrowed pass the deferred dispatch now runs from.
- `claude/.claude/hooks/require-ready-for-review.sh` — `gh pr create`'s unconditional cumulative-pass gate, the mechanism the deferral's safety rests on.
- `.claude/plans/comment-discipline-once.md` — full assumption ledger and the mechanisms considered and rejected.
