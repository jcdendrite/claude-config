# Content-addressed review markers

*Formerly `docs/design-decisions.md` §2.*

Five kinds exist: `code-review`, `plan-review`, `ready-for-review`, `skill-review`, and `cumulative-review` ([§44](ready-for-review-cumulative-diff-cache.md)) — a fifth kind that hashes the cumulative PR-vs-base diff so `ready-for-review` step 3 can reuse a prior clean pass instead of re-running it on a byte-identical rebase.

A marker's *content* — not its filename, and not its mere existence — is what authorizes a gate to open. The sha256 is taken from the staged diff at the time `/code-review` runs; the hook recomputes the sha256 at commit time and compares. If even one line has been re-staged since the review ran, the sha256 doesn't match and the gate fires again — no manual invalidation needed, no timer to expire, no way to accidentally commit a diff that wasn't reviewed.

That content-addressing is what makes the filename a pure implementation detail. The marker lives at `<config-dir>/code-review-markers/<repo-hash>.<session-id>` (`<config-dir>` means `$CLAUDE_CONFIG_DIR` when set, else `~/.claude`), where the session-id component exists so two parallel Claude Code sessions in the same worktree don't overwrite each other's markers — a write-side concern. Reading it back as an *authorization* predicate was a mistake worth naming: it narrowed the gate to "this session reviewed this state" when the property the gate wants is "this state has been reviewed," so a session resumed under a new id was denied a review it had genuinely completed. The gate now matches on content across every session suffix under the repo-hash. The repo-hash stays part of the read, because an identical diff in an unrelated repository was reviewed against different code.

The same reasoning generalizes past `/code-review`: `/plan-review` hashes the active plan set, `/ready-for-review` stores the gated HEAD sha, `/skill-review` hashes the SKILL.md-scoped diff. For the read semantics each gate applies, and for the separate question of *who* may write a marker at all, see [`hooks.md` — Marker keying and gate-release authority](../hooks.md#marker-keying-and-gate-release-authority).
