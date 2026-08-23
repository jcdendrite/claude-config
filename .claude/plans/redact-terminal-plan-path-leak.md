# Redact private-project path leak in a merged plan file

## Context

Remove a private-project-identifying filesystem path that leaked into a
committed plan file on `main`. `.claude/plans/identify-terminal-by-pid.md`
(committed in `d626ee0c`, "identify terminal by pid (#732)") cited a prior
scratchpad file via a full absolute path under a session-scoped temp
directory. The path's home-directory segment was dash-encoded (the scheme
Claude Code's scratchpad tooling uses for `/Users/<user>/<dir>` paths) and,
decoded, revealed a private project's directory name. This is exactly the
content CLAUDE.md's "Redact private-project-identifying content" section
requires stripped before commit; it was missed in review for PR #732.
Fixing it now removes the leak from the default branch going forward (the
commit history still contains it — history rewrite via `filter-repo` or a
force-push to `main` is out of proportion to a diff-only redaction fix and
is explicitly out of scope; the engineer confirmed this scope choice when
this plan was authored).

This plan file itself never reproduces the leaked path, its decoded form,
or any of its component tokens — not even redacted or placeholder-shaped —
since this file ships to `main` in the same PR as the fix, and CLAUDE.md's
redaction rules bind it exactly as they bind the file being fixed.

## Approach

Delete the parenthetical path citation from `identify-terminal-by-pid.md`
and fold its load-bearing content (what the prior brief verified) into the
surrounding prose, with no path of any kind — placeholder or real. The
exact scratchpad path was illustrative evidence, never part of the plan's
design, so removing it is a smaller and cleaner diff than placeholder-ifying
it (CLAUDE.md Axis 4, minimal change) while still fully satisfying the
redaction requirement.

The edit changes one sentence in the Context section of
`identify-terminal-by-pid.md`: it drops the parenthetical path citation and
folds the surviving clause — "a prior Haiku-authored technique brief ...
verified the underlying mechanism live" — directly into the sentence it was
parenthetical to, so the sentence still reads coherently with no path of
any kind in its place. See the actual diff on that file for the precise
before/after text.

No other file needs a change. A repo-wide sweep this session
(`git grep -niE` across every token in `~/.claude/private-projects.md`,
excluding the repo owner's own commit-author name, which the redaction
policy already excludes) found no other leak of this project's name:
every other hit was a false-positive substring match against unrelated
words. The blocklist terms that matched this leak are already present in
`~/.claude/private-projects.md`, so the blocklist needs no update to catch
a future accidental re-mention of this same project name.

### Assumption ledger

**Root problem:** a private-project directory name is present in a file on
the public default branch and must be removed going forward.

- **Givens:**
  - The commit history itself is not rewritten (`filter-repo`, force-push to
    `main`) — out of proportion to a diff-only redaction fix, and a
    materially higher-blast-radius action the engineer did not authorize.
    `[engineer-verified]`
  - Non-repo cleanup (shell history, other local scratch files) is out of
    scope for this plan — the engineer chose to keep this plan repo-only.
    `[engineer-verified]`

| # | Item | Tag |
|---|------|-----|
| 1 | Leak is still present at `.claude/plans/identify-terminal-by-pid.md:13` on current `main` | `[verified: grep run this session]` |
| 2 | No other leak of this project name exists in the repo | `[verified: git grep sweep across every private-projects.md token this session]` |
| 3 | The two blocklist terms that match this leak are already in `~/.claude/private-projects.md`, so no blocklist edit is needed | `[verified: read this session]` |
| 4 | The removed path was illustrative only, not load-bearing to the plan's design | `[verified: read surrounding Context section this session]` |

## Critical files

- `.claude/plans/identify-terminal-by-pid.md` — remove the path citation
  from the Context section's parenthetical, per the Approach above. No
  other files change.

## Verification

- A grep of `.claude/plans/identify-terminal-by-pid.md` for the leaked
  path's directory-name token (see `~/.claude/private-projects.md` for the
  exact term) and its session-UUID (sourced from commit `d626ee0c`, the
  commit that introduced the leak) returns no matches for either.
- Manual read of the edited Context paragraph confirms it still reads
  coherently and preserves the original meaning.
- `/code-review` on the diff before commit (repo convention).

## Out of scope

- Rewriting git history to purge the leak from `d626ee0c` itself.
- Scrubbing local, non-repo artifacts (shell history, other scratch files) —
  left to the engineer separately, per this session's decision.
- Auditing the rest of the repo's plan files for unrelated leaks beyond the
  sweep already run above.
- `opus-plan-sonnet-review` / PR #731 — unrelated, in progress separately.
