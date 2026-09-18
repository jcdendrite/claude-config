# The official marketplace is no longer pre-registered

*2026-09-17.*

`claude/.claude/settings.json` no longer declares `extraKnownMarketplaces.claude-plugins-official` or either of its two `enabledPlugins` entries (`claude-md-management@claude-plugins-official`, `claude-code-setup@claude-plugins-official`), both already `false`. Every stow consumer paid a per-session cost to keep a github-source marketplace pre-registered for two plugins nobody currently enables: it is the only registered marketplace that does a fresh clone at session start, with no consumer currently opting either of its plugins on. That per-session tax outweighs preserving a zero-effort opt-in path nothing uses.

A consumer who wants either plugin back runs two commands, once:

```
claude plugin marketplace add anthropics/claude-plugins-official
claude plugin install <name>@claude-plugins-official --scope user
```

This commit does not un-register the marketplace on a profile where `claude plugin marketplace add` already ran. `register-marketplace.sh` decides "already registered" by reading Claude Code's own per-profile marketplace registry (`claude plugin marketplace list --json`), never `settings.json`, and nothing else in this repo ever removes a marketplace. Removing the declaration only changes what a *future* `./install.sh` run or a *fresh* profile adds; an existing registration survives the pull untouched. An alternative considered and rejected — auto-pruning undeclared marketplaces inside `register-marketplace.sh` so the repo half becomes self-realizing — turns a declarative settings file into a destructive sync over user state, silently removing any marketplace a consumer registered by hand on their own profile. It would not even help the motivating profile: `install.sh` does not run on `git pull`, so nothing would trigger the prune there either.

**[unverified — pending engineer's empirical check]** Whether this commit alone stops the per-session reclone on an already-provisioned profile, or whether manual removal is also required, has not been empirically checked as of this writing. The predicted answer is that the commit alone is insufficient: `register-marketplace.sh` reads Claude Code's own marketplace registry to decide what's already registered, never `settings.json`, so nothing in this repo's tracked state drives whatever refresh Claude Code performs at session start against a marketplace already in that registry. An already-provisioned profile is expected to need one further, engineer-run step: `claude plugin marketplace remove claude-plugins-official`, run once against that profile after pulling this commit.

**Revisit trigger:** if Claude Code gains a per-marketplace refresh or cache control that makes a registered-but-unused marketplace free to keep declared at session start, pre-registering `claude-plugins-official` again becomes worth reconsidering.

**Full revert path:** `git revert`ing this commit restores the declaration for any future or re-run `./install.sh`. A profile that had the marketplace manually removed regains the registration automatically the next time it runs `./install.sh`, since `register-marketplace.sh`'s existing add-if-absent logic already covers that case — no new one-off command is needed in the reverse direction.

## Sources

- `claude/.claude/settings.json` — the removed `enabledPlugins`/`extraKnownMarketplaces` entries.
- `.claude/plans/remove-official-plugin-marketplace.md` — full assumption ledger, per-mechanism reasoning, and verification steps this entry summarizes.
