---
name: pr-description-claude-config
description: Project-specific layer for /pr-description, loaded only when authoring or syncing PR bodies in the claude-config repo itself.
disable-model-invocation: true
---

## Closing-keyword issue linking

This repo tracks work in GitHub issues. A branch or ticket-ID prefix of the
form `GH-<N>` (see `branch-management`'s ticket-ID convention) names GitHub
issue `N` directly.

When the current branch carries a `GH-<N>` prefix, or the draft or existing
body already mentions an issue by number in prose ("issue 544", "resolves
issue 550"), the body must reference it with a GitHub closing keyword —
`Fixes #N`, `Closes #N`, or `Resolves #N` — not plain prose. GitHub only
creates the Development-sidebar linked-PR relationship and auto-closes the
issue on merge when it sees that exact syntax; a prose mention never
triggers it, regardless of accuracy. Rewrite a prose mention into
closing-keyword form rather than leaving both.

If the branch carries no ticket prefix and the body mentions no issue
number, this check has nothing to do — most PRs legitimately close nothing.
