# claude.ai skill sync disabled by default

*2026-09-17.*

`claude/.claude/settings.json` now sets `syncClaudeAiSkills: false`. This repo manages skills canonically here, in `claude-skills/`, and the engineer manually copies them to claude.ai — the claude.ai→Claude Code sync direction only injects duplicates of skills this repo already holds. Sync is on by default for every account once logged in via `/login`, so leaving the key unset would keep re-introducing that duplication on every stowed account.

The flip has two separate consumer-visible effects, not one "recovery" story. First, the flip applies at **user or managed** scope, a subset of the key's full valid-scope set (user, local, managed, or `--settings`; repository scope is the one excluded). At that scope, setting it to `false` moves any already-synced skills out of `~/.claude/skills/synced/` and into `~/.claude/skills/.trash/`, rather than merely stopping future syncs. Recovery is manual: move the affected skill directories back out of `.trash/` if you want them again. Second, and independently of that relocation, there is no supported way to resume sync for a single project once the stow-source default is `false`. The key's own documentation states `true` is exactly equivalent to unset at *every* scope — this is a direct, key-specific rule, not an inference from general settings-precedence ordering. So no scope, including a normally higher-precedence one like a repository's own `.claude/settings.local.json`, can express "sync back on." The only confirmed recourse is editing the tracked stow-source `claude/.claude/settings.json` directly and removing the key, re-clobbered on the next `git pull` unless carried forward. No minimum Claude Code version is documented for this key, unlike some sibling keys in the same reference table, so none is claimed here.

The rollout is pull-gated with no push mechanism and no completion signal back to this repo. A consumer who pulls infrequently keeps sync on, and keeps accumulating synced duplicates, until their next `git pull`.

## Sources

- [Claude Code settings reference](https://code.claude.com/docs/en/settings-reference#syncclaudeaiskills) — the key's scope restriction (User, local, or managed, or `--settings`; not a repository's shared settings), the `false`-at-user-or-managed-scope `.trash/` relocation, and the `true`-is-always-equivalent-to-unset rule.
- `claude/.claude/settings.json` — the `syncClaudeAiSkills: false` entry itself.
- `.claude/plans/disable-claude-ai-skill-sync.md` — full assumption ledger, per-mechanism reasoning, and verification steps this entry summarizes.
