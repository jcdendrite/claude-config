# Plans are shared by path, not by a PR opened at plan time

*2026-09-30.*

`/plan-it` opens no pull request, draft or ready, when it produces a plan. The engineer reads the plan at its absolute path, which [`announce-approved-plan-path.sh`](../../claude/.claude/hooks/announce-approved-plan-path.sh) shows when `/plan-review` records approval. The session also gives the plan file's own absolute path, because the hook's banner is not always shown and the session cannot see it. When readers outside the session need the plan before implementation starts — a new design document, or a contract other teams depend on — the session pushes the branch with no PR, only on the engineer's yes, and shares the plan file's URL.

The engineer's reasoning: "I think we have to exclude the draft PR option entirely because it forces cumulative diff reviews earlier which is cost prohibitive."

**Why no PR at plan time.** A draft PR gates pushes exactly as a ready one does, because [`require-ready-for-review.sh`](../../claude/.claude/hooks/require-ready-for-review.sh) finds a branch's PR without filtering out drafts. A PR opened at plan time therefore moves the cumulative `/code-review` of the whole branch from hand-off to every implementation push. A push to a branch with no PR is not gated, so pushing the plan without one gives other readers the same file without the repeated review. The hook's own header lists the gated commands, what a gated push needs, and the exemptions.

**Why the push is asked through `AskUserQuestion`.** Under autonomous shipping, [`advance-past-commit-stall.sh`](../../claude/.claude/hooks/advance-past-commit-stall.sh) treats a turn that ends by asking whether to push as a stall, and tells the session to continue through `/ready-for-review` to opening a PR, which is the outcome this decision rules out. A question asked through the tool is expected to be answered before the turn ends, which is not yet verified under autonomous shipping. The plan's post-merge section (`.claude/plans/plan-share-links.md`, § Verification) names it as a watch item. Update this record when it is observed.

A pushed file carries no diff, so threaded inline comments are not available on a plan shared this way. Feedback comes in-session or out-of-band.

This decision covers plan time only. When `/ready-for-review` opens the PR, and in which state, is that skill's concern.
