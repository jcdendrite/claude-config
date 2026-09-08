# `claudeMdExcludes` suppresses the nested-discovery duplicate of `claude/.claude/CLAUDE.md`

*2026-08-31. Formerly `docs/design-decisions.md` §39.*

Nested-CLAUDE.md discovery walks the physical filesystem tree rather than deduplicating against files already loaded through a symlink. A session working in this repo therefore loaded the global-instructions file twice: once at user scope through the `~/.claude/CLAUDE.md` stow symlink, and again as a fresh system-reminder block the first time anything under `claude/.claude/**` was read.

The exclusion is added to this repo's own project-scope `.claude/settings.json` rather than to any per-machine `settings.local.json`, because the duplicate only occurs for a session whose working directory is inside this repo — every contributor hits it, so the fix belongs where every contributor's `git pull` picks it up.

The stow-source `claude/.claude/settings.json` deliberately carries no matching entry. That file installs to every consumer's user-scope `~/.claude/settings.json`, so an entry there would apply to every project on every consumer's machine — wider than the condition it addresses, which is specific to sessions working inside this repo.

Renaming or relocating `claude/.claude/CLAUDE.md` to dodge the duplicate was rejected: Stow links each immediate child of `claude/.claude/` individually ([§5](stow-over-plugin-marketplace.md)), so moving the file breaks the 1:1 install mapping every consumer runs.

Whether `claudeMdExcludes` also matches a symlink's target path is undocumented for CLAUDE.md files (only for `.claude/rules/`), so this pattern's correctness was unverified until a fresh session confirmed it empirically. That fresh session confirmed both that the user-scope load survives and that the pattern does not over-suppress. See the plan's assumption ledger (`.claude/plans/exclude-nested-claude-md-duplicate.md`) for the full ledger row.

## Sources

- [Claude Code memory docs](https://code.claude.com/docs/en/memory), "Exclude specific CLAUDE.md files" — `claudeMdExcludes` setting, path-matching semantics, and the either-path symlink rule for `.claude/rules/` files.
- `.claude/plans/exclude-nested-claude-md-duplicate.md` — full assumption ledger and verification steps.
