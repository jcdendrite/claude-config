# Plans are shared by path, not by a PR opened at plan time

*2026-09-30.*

`/plan-it` opens no pull request, draft or ready, when it produces a plan. The engineer reads the plan at its absolute path, which [`announce-approved-plan-path.sh`](../../claude/.claude/hooks/announce-approved-plan-path.sh) shows when `/plan-review` records approval. When readers outside the session need the plan before implementation starts — a new design document, or a contract other teams depend on — the session pushes the branch with no PR, only on the engineer's yes, and shares the plan file's URL.

The engineer's reasoning: "I think we have to exclude the draft PR option entirely because it forces cumulative diff reviews earlier which is cost prohibitive."

**Why no PR at plan time.** [`require-ready-for-review.sh`](../../claude/.claude/hooks/require-ready-for-review.sh) gates every `git push` to a branch that has a PR, and it finds that PR with `gh pr view` without filtering out drafts, so a draft PR gates pushes exactly as a ready one does. A gated push needs a `/ready-for-review` run at the pushed HEAD, and that run includes a cumulative `/code-review` of the whole branch against the default branch. A PR opened at plan time therefore moves that review from hand-off to every implementation push. A push to a branch with no PR is not gated, so pushing the plan without one gives other readers the same file without the repeated review. The hook's own header lists the gated commands and their exemptions.

**Why the push is asked through `AskUserQuestion`.** Under autonomous shipping, [`advance-past-commit-stall.sh`](../../claude/.claude/hooks/advance-past-commit-stall.sh) treats a turn that ends by asking whether to push as a stall, and tells the session to continue through `/ready-for-review` to opening a PR, which is the outcome this decision rules out. A question asked through the tool is answered before the turn ends.

Threaded inline review comments on a plan are deliberately given up: a pushed file has no diff to comment on, so readers' feedback comes in-session or out-of-band.

This decision covers plan time only. When `/ready-for-review` opens the PR, and in which state, is that skill's concern.
