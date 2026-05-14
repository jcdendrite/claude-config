---
name: respond-pr
description: Read and respond to PR review comments on the current branch's pull request
argument-hint: "[PR number]"
---

TRIGGER when: replying to PR review comments, OR posting any comment/reply to a GitHub pull request — even as part of a larger task. Enforces required attribution prefix.

Fetch all review comments on the current branch's open pull request and address them.

## Steps

0. **Enable hook bypass.** Run:
   <!-- HOOK_TEST_FIXTURE: enable-bypass — the hook-alignment test suite reads this exact fenced block to verify it creates the marker layout require-respond-pr.sh expects. Do not duplicate elsewhere; the test re-reads it from here. -->
   ```
   ~/.claude/scripts/marker.sh activate respond-pr
   ```
   The `require-respond-pr.sh` PreToolUse hook bypasses while THIS session's marker exists and its stored PID is alive (PID liveness check via `kill -0`). Per-session keying prevents parallel respond-pr sessions from thrashing on cleanup or leaking bypass to unrelated sessions. If the chain fails (empty `SESSION_ID`, etc.), the `capture-session-id.sh` SessionStart hook didn't run — abort and report; do not proceed without the marker, since every gated `gh` call below will be blocked.

   **Marker lifecycle:** this marker must stay active for the entire skill session — step 7 is the only step that removes it. If you run other skills as intermediate steps (e.g., `/ready-for-review` before pushing in step 6), their cleanup must not touch this marker. If the marker is accidentally removed mid-session, restore it in a standalone Bash call before any subsequent `gh` command — the hook fires before the shell executes, so you cannot create the marker and use it atomically in the same call.
1. Identify the PR number for the current branch: `gh pr view --json number -q '.number'`
2. Fetch **all three** types of comments. Two failure modes Claude commonly hits: (a) fetching only the first type and missing the other two; (b) fetching without `--paginate` and silently truncating at 30 results per type. Both produce reviews that look complete but miss real feedback.
   - **Inline file comments:** `gh api repos/{owner}/{repo}/pulls/{number}/comments --paginate`
   - **Top-level review comments:** `gh api repos/{owner}/{repo}/pulls/{number}/reviews --paginate --jq '.[] | select(.body != "")'`
   - **Issue-level comments:** `gh api repos/{owner}/{repo}/issues/{number}/comments --paginate`
3. **Classify, then batch.** Before drafting any reply, assign each unresolved comment to one of the five types below. All code changes across the batch go into a single commit (step 6); all replies are posted after that commit. Required fields are not optional — if a field's value is non-obvious, that is exactly when it earns its keep.

   - **`FIXED`** — a code change was made.
     Required fields: `disposition` (one of `fixed-as-requested` | `fixed-with-modification` | `fixed-differently`), `rationale` (REQUIRED when disposition ≠ `fixed-as-requested` — explain what was changed and why), `commit_sha` (filled in after the commit in step 6).
   - **`EXPLAIN-DESIGN`** — the existing code is correct; the comment misreads intent.
     Required fields: `decision` (what the code does), `why` (the reason it does that), `why-not-alternative` (why the reviewer's apparent alternative was not taken), `where-documented` (file:line or "inline below").
   - **`OUT-OF-SCOPE`** — the comment identifies a real issue but it belongs in a separate PR.
     Required fields: `acknowledgment` (confirm the issue is real), `where-tracked` (ticket or backlog), `link-or-ticket` (URL or "will create").
   - **`DEFERRED`** — the change is valid but intentionally left for a follow-up.
     Required fields: `acknowledgment`, `deferral-reason`, `follow-up-ticket` (URL to an existing ticket, or `"will create: <one-line summary>"` if none exists yet — but the ticket must be created before the skill session ends).
   - **`AGREE-NO-CHANGE`** — the reviewer is right but the code already handles it, or the suggestion is a matter of preference and no change is warranted.
     Required fields: `acknowledgment`, `rationale`.

   **Worked examples** (illustrating required-field depth, not wording to copy verbatim):

   *`FIXED` with `fixed-with-modification`:*
   > **[Claude Code]** Fixed. Applied the reviewer's intent (validate before writing) but checked at the handler level rather than inline at the call site — the call site is shared by three paths and an inline check would need to be duplicated. `disposition: fixed-with-modification` | `rationale: moved check to handler boundary to avoid three-way duplication` | `commit_sha: <sha>`

   *`EXPLAIN-DESIGN`:*
   > **[Claude Code]** The early return is intentional — when the session token is absent we want a fast 401 with no DB round-trip. The alternative (falling through to a null-check deeper in the stack) would silently succeed for unauthenticated callers in contexts where the token field is optional. `where-documented: auth/middleware.ts:42`

4. For each unresolved comment:
   - Read the referenced file and line to understand the context
   - Apply the classification from step 3
   - If a code change is needed, make it; note it in the reply using the `FIXED` field template
   - If it's design explanation, draft using the `EXPLAIN-DESIGN` template
5. Post replies using the appropriate endpoint for each comment type — do **not** use `gh api .../pulls/comments/{id}` with `-F body=...`; that endpoint PATCHes the target comment in place and silently overwrites the author's text.

   - **Inline file comments** (fetched from `pulls/{n}/comments`) — reply via the `/replies` sub-resource:
     ```
     gh api repos/{owner}/{repo}/pulls/{pull_number}/comments/{comment_id}/replies \
       -F body='**[Claude Code]** ...reply text...'
     ```

   - **Top-level review bodies** (fetched from `pulls/{n}/reviews`) — no `/replies` primitive exists; post a new top-level comment in the conversation tab:
     ```
     gh api repos/{owner}/{repo}/issues/{number}/comments \
       -F body='**[Claude Code]** ...reply text...'
     ```

   - **Issue-level comments** (fetched from `issues/{n}/comments`) — same path; no `/replies` sub-resource:
     ```
     gh api repos/{owner}/{repo}/issues/{number}/comments \
       -F body='**[Claude Code]** ...reply text...'
     ```
6. Commit and push any code changes in a single commit
7. **Remove this session's hook bypass marker:**
   <!-- HOOK_TEST_FIXTURE: disable-bypass — the hook-alignment test suite reads this exact fenced block to verify it removes the marker the enable step created. Do not duplicate elsewhere; the test re-reads it from here. -->
   ```
   ~/.claude/scripts/marker.sh deactivate respond-pr
   ```
   Removes only this session's file. If the skill errors out before reaching this step, the gate will evict the orphan automatically once the session's process ends — the hook checks PID liveness on each gate hit.

## Attribution

**CRITICAL:** All PR comment replies are posted through the user's GitHub token and will appear as the user's account. To avoid confusion, **always** prefix every reply body with `**[Claude Code]**` followed by the response content. This makes it clear the response is AI-generated.

Example:
```
gh api repos/owner/repo/pulls/4/comments/12345678/replies \
  -F body='**[Claude Code]** Moved the utility functions to the shared module as suggested.'
```

## Guidelines

- Group related code changes into a single commit
- Be concise in replies — state what was done, not lengthy explanations
- If you disagree with a comment, explain why clearly but defer to the reviewer's judgment
- Do not resolve review threads — let the reviewer verify and resolve them
- PR comments are editable after posting. If a reply **you authored** in this session has a typo or factual error, edit it in place rather than posting a correction. **Use PATCH only against comments you authored — confusing the target ID with the user's comment ID overwrites their text irrecoverably and cannot be undone.** A `user.login` check is insufficient — Claude posts under the user's own token, so PATCH against the user's own comment will succeed. The reliable check is the attribution prefix: every reply Claude posts starts with `**[Claude Code]**`, and the user's comments do not. Before any PATCH, fetch the target body and verify it starts with that prefix; abort to the `/replies` form (Step 5) on any mismatch:
  ```
  TARGET_BODY=$(gh api repos/{owner}/{repo}/pulls/comments/{id} --jq '.body')
  case "$TARGET_BODY" in
    '**[Claude Code]**'*) gh api repos/{owner}/{repo}/pulls/comments/{id} -X PATCH \
                            -F body='**[Claude Code]** ...corrected text...' ;;
    *) echo "ABORT: target is not Claude-authored; reply via /replies instead" >&2; exit 1 ;;
  esac
  ```
  (or `/issues/comments/{id}` for issue-level comments)
- **Avoid SHA-pinned commit references.** When acknowledging a fix in a reply, prefer "addressed in the latest commit on this branch" over "fixed in commit `<sha>`". SHA references become stale if the branch is rebased or force-pushed; branch-tip language remains correct across rebases. If a prior reply in this session cited a SHA that is now stale, post a correction reply — do not leave stale SHAs uncorrected before requesting reviewer re-verification.
