# Plans are hidden from Grep, not deleted

*2026-11-20.*

A tracked [`.rgignore`](../../.rgignore) at the repo root lists `.claude/plans/`, so a Claude Code Grep rooted above that directory returns no plan file. No plan is deleted, moved or edited. The file's own comments state which tools honour it and which do not.

**Why this reopens a recorded non-defect.** The [2026-08-10 repo quality audit](../reports/2026-08-10-repo-quality-audit/findings.md) lists plan accumulation under its finding 8, "Not defects — recorded so they are not re-litigated", and says pruning is not the fix. GH-1535 reopened that finding for one reason: a search for a hook or script name returned plan files alongside the code that implements it, and a reader of those hits cannot tell a superseded plan from current behaviour. The issue reopened it on a stated condition, that a plan may go once no active agent and no relevant PR needs it.

**Why hide instead of prune.** Hiding plans from content search removes the problem the issue states without deciding which plans meet that condition. Plan paths still resolve for `Read`, so every citation of a plan by path keeps working. The plan gate is untouched. The change is one file to revert.

**Deletion was the fallback, and it was not taken.** The issue's second step was a script that deletes every plan cited by no tracked file, open issue, open PR or pushed unmerged branch. That step applied only if an ignore file failed to keep plans out of Grep, and the ignore file did not fail. Plans stay tracked, and the "supersede, never delete" policy the audit finding describes still holds.

**What the ignore file leaves in place.** Glob still lists plan files, so a file-name search returns them. To search plans on purpose, root the Grep at `.claude/plans`.

**The Explore recall fixture.** Its keys name plan paths, which matters to whoever next re-pins it. `evals/README.md` § "Explore recall gate" states what a re-pin must do.
