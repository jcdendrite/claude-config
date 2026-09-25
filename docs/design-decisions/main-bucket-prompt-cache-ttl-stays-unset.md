# Keep `promptCacheTtl` unset after a single-direction `main` verdict

*2026-09-25.*

**What changed.** [main-bucket-prompt-cache-ttl-unset.md](main-bucket-prompt-cache-ttl-unset.md)'s Revisit clause fired. A 30-day `cache-rebuild --ttl-verdict` run on the corrected accounting returns `decline` for `main`. Every consistent root already favors the one-hour tier it already runs (verdicts as defined in `docs/transcript-analysis.md`'s "TTL-verdict per-root analysis" section).

**Action.** None. `promptCacheTtl` stays unset, because the verdict rule ships a committed value only on `adopt`. The tier `main` favors is already the vendor's default for a subscription within plan usage. `subagentPromptCacheTtl` also stays unset; that bucket still returns `decline`, favoring the five-minute tier.

**Why not `"1h"`.** The vendor advises API-key and cloud-provider users to set `promptCacheTtl` to `1h`. A value in the stowed file reaches every consumer, not just those. A subscription consumer running on credits sees the vendor's own drop to five minutes — see [main-bucket-prompt-cache-ttl-5m.md](main-bucket-prompt-cache-ttl-5m.md)'s "The corpus's billing regime bounds the verdict's reach" paragraph — and a stowed `"1h"` value overrides that drop for every consumer, not just those running on credits. That keeps the 2x write tier running on metered usage, a state this repo's read cannot isolate. A consumer who wants the vendor's advice, or whose own `--ttl-verdict` run favors a tier their default doesn't give, has the main-bucket-only override that [main-bucket-prompt-cache-ttl-5m.md](main-bucket-prompt-cache-ttl-5m.md)'s "Consumer recourse, in precedence order" paragraph describes.

**No figures.** Figures, root counts, and per-root results are withheld, per `docs/private-project-redaction.md` § "A wider corpus goes to the owner, never into a public artifact".

**Revisit** if any of:

- A later `--ttl-verdict` run returns `adopt` for either bucket, the only verdict that licenses a committed value.
- The vendor changes a main-bucket default, its over-limit drop to five minutes, or the 1.25x/2x write multipliers.

## Sources

- [main-bucket-prompt-cache-ttl-unset.md](main-bucket-prompt-cache-ttl-unset.md) — the superseded entry.
- [main-bucket-prompt-cache-ttl-5m.md](main-bucket-prompt-cache-ttl-5m.md) — the lever, precedence chain, and consumer recourse.
- `docs/transcript-analysis.md`'s "TTL-verdict per-root analysis" section — the verdict definitions.
- `.claude/plans/cache-ttl-tuning-analysis.md` — the rule that ships a committed value only on `adopt`.
- [Claude Code prompt caching](https://code.claude.com/docs/en/prompt-caching) — the per-bucket defaults, the over-limit drop to five minutes, the API-key advice, and `CLAUDE_CODE_PROMPT_CACHE_TTL`'s main-bucket-only scope.
