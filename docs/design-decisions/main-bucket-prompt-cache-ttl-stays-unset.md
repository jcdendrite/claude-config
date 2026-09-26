# Keep `promptCacheTtl` unset after a single-direction `main` verdict

*2026-09-25.*

**What changed.** [main-bucket-prompt-cache-ttl-unset.md](main-bucket-prompt-cache-ttl-unset.md)'s Revisit clause fired. A 30-day `cache-rebuild --ttl-verdict` run on the accounting that entry corrected returns `decline` for `main`. Per `docs/transcript-analysis.md`'s decline-definition sentence in its "TTL-verdict per-root analysis" section, that means every consistent root confirms the tier it already runs, not that a switch failed its margin.

**Action.** None. `promptCacheTtl` stays unset because the verdict above names no switch to make — see `.claude/plans/cache-ttl-tuning-analysis.md`'s "Per-root reporting and the ship rule" paragraph for what a committed value requires. The tier `main` favors is already the vendor's default for the corpus's billing regime — see [main-bucket-prompt-cache-ttl-5m.md](main-bucket-prompt-cache-ttl-5m.md)'s "The corpus's billing regime bounds the verdict's reach" paragraph. `subagentPromptCacheTtl` also stays unset; that bucket still returns `decline`, favoring the five-minute tier.

**Why not commit `"1h"` anyway.** A committed `"1h"` would change no write this verdict measured, since every consistent root already writes at the tier the verdict above confirms. It would change only writes the vendor's default puts on the five-minute tier, and the verdict measured none of those. The vendor advises API-key and cloud-provider users to set `promptCacheTtl` to `1h`. A committed value cannot follow that advice for those users alone: [main-bucket-prompt-cache-ttl-5m.md](main-bucket-prompt-cache-ttl-5m.md)'s lever, precedence-chain, and billing-regime paragraphs apply here unchanged. Under them, a committed `"1h"` also overrides the vendor's five-minute default under usage credits, so a subscription consumer on usage credits keeps writing at the one-hour tier's 2x price. A consumer who wants the vendor's advice, or whose own `--ttl-verdict` run favors a tier their default doesn't give, has the main-bucket-only override in that entry's "Consumer recourse, in precedence order" paragraph.

**No figures.** Figures, root counts, and per-root results are withheld, per `docs/private-project-redaction.md` § "A wider corpus goes to the owner, never into a public artifact".

**Revisit** if any of:

- A later `--ttl-verdict` run returns `adopt` for either bucket — see `.claude/plans/cache-ttl-tuning-analysis.md`'s "Per-root reporting and the ship rule" paragraph for what that verdict licenses.
- The vendor changes the main-bucket default.
- The vendor changes the usage-credits drop to five minutes.
- The vendor changes the 1.25x/2x write multipliers.

## Sources

- [main-bucket-prompt-cache-ttl-unset.md](main-bucket-prompt-cache-ttl-unset.md) — the superseded entry.
- [main-bucket-prompt-cache-ttl-5m.md](main-bucket-prompt-cache-ttl-5m.md) — the lever, precedence chain, and consumer recourse.
- `docs/transcript-analysis.md`'s "TTL-verdict per-root analysis" section — the verdict definitions.
- `.claude/plans/cache-ttl-tuning-analysis.md` — the per-root ship rule.
- [Claude Code prompt caching](https://code.claude.com/docs/en/prompt-caching) — the per-bucket defaults, the usage-credits drop to five minutes, the API-key advice, and `CLAUDE_CODE_PROMPT_CACHE_TTL`'s main-bucket-only scope.
- [Claude platform prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) — the published 1.25x/2x/0.1x price multipliers.
