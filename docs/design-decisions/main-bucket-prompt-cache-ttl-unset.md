# Unset `promptCacheTtl` for the main-conversation prompt-cache bucket

*2026-09-21.*

**What changed.** [main-bucket-prompt-cache-ttl-5m.md](main-bucket-prompt-cache-ttl-5m.md)'s own Revisit clause — "a later `cache-rebuild --ttl-verdict` run inverts the `main`-bucket verdict" — fired. `cache-rebuild --ttl-verdict`'s accounting carried two biases, both pushing the 1h-to-5m verdict toward `adopt`:

- An idle-band check that used a write-tier-qualified cause value where a pure gap test was needed, undercounting the `Z` (rescued-warm-read) accumulator and the 1h-to-5m expiry-cost term.
- A margin-denominator footing that priced write volume at flat rates while the net itself already carried the fast-mode/US-inference-geo rate multipliers.

Both are fixed in this repo's own history (see Sources). A run against the same machine corpus, on the corrected accounting, no longer returns a single-direction verdict for `main`.

**Action.** `promptCacheTtl` is removed from `claude/.claude/settings.json`, restoring whichever default the vendor applies to the reader's own billing regime — the same caveat [main-bucket-prompt-cache-ttl-5m.md](main-bucket-prompt-cache-ttl-5m.md)'s "Reversing this change does not restore `1h`" section already states, unchanged by this reversal. `subagentPromptCacheTtl` was never set and stays unset. The subagent-bucket verdict (`decline`) is unaffected by either bias fix, since neither the idle-band predicate nor the margin footing changed which roots or which direction that bucket already favored.

**A consumer who already picked up the `"5m"` value sees a second flip.** The entry this supersedes merged to `main` before this one did. A subscription-billed consumer who ran `git pull` and relaunched `claude` in that window already experienced a live 1h-to-5m cache-tier change. That is the same "expected, not confirmed, next-launch" hedge that entry's "When the key takes effect is not established" section names. That consumer now sees a second silent flip, back to the vendor's billing-regime-conditional default, on their next `claude` launch after pulling this entry. Both flips are individually disclosed by their own design-decision entries; neither entry previously named the two-flip case for a consumer active in the window between them. That same section also names the way to check the currently-live tier directly: run `claude -p "hello" --output-format json` and read `usage.cache_creation`.

**No figures here either.** [main-bucket-prompt-cache-ttl-5m.md](main-bucket-prompt-cache-ttl-5m.md) withheld the underlying figures, the per-project decomposition, and the number of projects involved for the original `adopt` verdict, under this repo's CLAUDE.md § "Redact private-project-identifying content" and `docs/private-project-redaction.md` § "Publishing a tooling measurement." The corrected run is against this machine's own transcript corpus, so the same bar applies here — nothing more is disclosed.

**Revisit** if a later `cache-rebuild --ttl-verdict` run against the corrected accounting returns a consistent, single-direction verdict for `main` — in either direction.

## Sources

- [main-bucket-prompt-cache-ttl-5m.md](main-bucket-prompt-cache-ttl-5m.md) — the superseded entry; its lever/two-bucket-structure, billing-regime, recourse, version-floor, and rollout-timing sections are unaffected by this reversal and not restated here.
- `docs/transcript-analysis.md`'s "TTL-verdict per-root analysis" section — the corrected accounting this entry's verdict rests on.
- `.claude/plans/ttl-verdict-z-pure-1h-write-fix.md` — the plan and commit that fixed both accounting biases.
- `claude/.claude/settings.json` — the `promptCacheTtl` removal itself.
