# Walkthrough: One contribution cycle with claude-config active

This transcript shows a realistic contribution cycle enforced by claude-config hooks and skills. Task: add a rate limit to the user export endpoint.

---

## 1. Plan phase

**User:** Add a rate limit to the user export endpoint — 10 requests per minute per org, return 429 with Retry-After.

**Claude:** I'll write a plan before touching any code.

*Claude writes `.claude/plans/rate-limit-export.md` with concrete steps: identify the export handler in `src/routes/exports.ts`, add a Redis-backed rate limiter using the existing `createRateLimiter` factory, wire the 429 response with `Retry-After`, add unit tests for the deny path.*

*`require-plan-review.sh` fires on the next `Edit` call.*

**Hook output:**
```
[PreToolUse: Edit denied] A plan file exists at .claude/plans/rate-limit-export.md.
Run /plan-review before editing code files.
```

**Claude runs `/plan-review`.** Plan review spawns `staff-backend-engineer` (API contract, idempotency) and `staff-sdet` (test coverage). Both return clean. Review verdict: Approve.

*Plan-review marker written. `Edit` calls now pass through.*

---

## 2. Implementation and commit attempt

**Claude** edits `src/routes/exports.ts` and `src/middleware/rate-limit.ts`. Tests in `src/routes/exports.test.ts` are added.

**Claude:** The implementation looks good. Let me commit.

*Claude calls `Bash(git commit -m "feat: rate-limit user export endpoint")`.*

**Hook output:**
```
[PreToolUse: Bash denied] git commit blocked — no code review marker found for current staged diff.
Run /code-review before committing.
```

---

## 3. Code review

**Claude runs `/code-review`.** Spawns `staff-backend-engineer` and `staff-sdet` against the staged diff.

**`staff-backend-engineer` finding:** The rate limiter increments the counter before checking the limit. Under concurrent requests, two requests can both read a count of 9, both increment to 10, and both proceed. The Redis INCR + check should be inside a Lua script or use `SET NX` + TTL to make the check atomic.

**`staff-sdet` finding:** The test for the 429 path uses a stub that returns `true` for `isRateLimited`, bypassing the Redis logic entirely. No test exercises the concurrent increment case. The error path for a Redis timeout (returns `null` from `createRateLimiter`) has no test and no code handling it.

---

## 4. Fix, re-stage, commit

**Claude** fixes the race condition using a Lua script, adds a test for the Redis timeout fallback (fail open with a log warning), and adds a concurrent-increment integration test.

*Claude re-stages all files. The sha256 of the staged diff is now different from when `/code-review` ran.*

**Claude runs `/code-review` again.** Both reviewers return clean. Marker written for new diff sha256.

**Claude calls `Bash(git commit -m "feat: rate-limit user export endpoint")`.**

Hook finds marker, sha256 matches. Commit succeeds.

---

## 5. Ready for review

**Claude runs `/ready-for-review`.** Verifies `npm test` passes, `npm run lint` clean, typecheck clean. Runs code review against the cumulative PR diff (all commits on the branch vs default branch). Syncs PR description.

*Ready-for-review marker written for HEAD SHA.*

**Claude calls `Bash(git push origin rate-limit-export)`.**

**Hook output:** `[PreToolUse: Bash — allowed]` Push succeeds.

---

## 6. Responding to reviewer comments

**Reviewer leaves comment on PR:** "Can you clarify why you chose fail-open on Redis timeout rather than fail-closed?"

**Claude calls `Bash(gh api .../pulls/47/comments)`.**

**Hook output:**
```
[PreToolUse: Bash denied] gh api PR comment access blocked.
Run /respond-pr to fetch all comment types and post replies with [Claude Code] attribution.
```

**Claude runs `/respond-pr`.** Fetches inline comments, top-level comments, and review summaries. Bypass marker set.

**Claude** drafts reply: "Fail-open on Redis timeout prevents a Redis availability issue from taking down the export endpoint entirely. The rate limit is a best-effort abuse control, not a billing or security gate — a brief window of unthrottled requests during a Redis outage is acceptable. Added a log warning so ops can observe when the fallback fires."

**Claude calls `Bash(gh pr comment 47 --body "[Claude Code] Fail-open on Redis timeout prevents...")`.**

Hook sees bypass marker, allows. Comment posted.
