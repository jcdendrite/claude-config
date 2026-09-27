# Keep `promptCacheTtl` unset after a single-direction `main` verdict

*2026-09-25.*

**What changed.** [main-bucket-prompt-cache-ttl-unset.md](main-bucket-prompt-cache-ttl-unset.md)'s Revisit clause fired. A 30-day `cache-rebuild --ttl-verdict` run on the accounting that entry corrected returns `decline` for `main`. Every consistent root in that run favors the tier it already runs — the form of `decline` that `docs/transcript-analysis.md`'s "TTL-verdict per-root analysis" paragraph describes as no switch to make, not a switch whose evidence fell short. A _consistent_ root is one whose dominant tier clears that section's own dominance-share gate; a root close to an even split between the one-hour and five-minute tiers falls below that gate and is excluded from the verdict entirely, while a root that leans toward one tier still counts, scored under that tier.

**Action.** None. `promptCacheTtl` stays unset because the verdict above names no switch to make — see `.claude/plans/cache-ttl-tuning-analysis.md`'s "Per-root reporting and the ship rule" paragraph for what a committed value requires. `subagentPromptCacheTtl` also stays unset; that bucket still returns `decline`, favoring the five-minute tier.

**Why not commit `"1h"` anyway.** A committed `"1h"` would change no write this verdict counted, since every consistent `main`-bucket root already favors the tier it runs — for the owner of this repo, that's the one-hour tier. The vendor advises API-key and cloud-provider users to set `promptCacheTtl` to `1h`. A committed value cannot follow that advice for those users alone: [main-bucket-prompt-cache-ttl-5m.md](main-bucket-prompt-cache-ttl-5m.md)'s lever and precedence-chain paragraphs apply here unchanged, as does the vendor default its billing-regime paragraph opens with. Under them, a committed `"1h"` also overrides the vendor's five-minute default under usage credits, so a subscription consumer on usage credits keeps writing at the one-hour tier's 2x price. A consumer who wants the vendor's advice, or whose own `--ttl-verdict` run favors a tier their default doesn't give, has the main-bucket-only override in that entry's "Consumer recourse, in precedence order" paragraph.

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
