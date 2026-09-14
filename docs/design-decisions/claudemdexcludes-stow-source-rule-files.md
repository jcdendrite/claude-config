# A second `claudeMdExcludes` entry suppresses the nested-discovery duplicate of each stow-source rule file in a linked worktree

*2026-09-03. Formerly `docs/design-decisions.md` §47.*

In a linked worktree the user-scope symlink target (`~/.claude/rules`, pointing at the main checkout) and the nested project path (`<worktree>/claude/.claude/rules/`) are different absolute paths, so each matching stow-source rule file loads twice. In the main checkout those two paths are identical, so only one copy loads there.

The main-checkout copy wins, so a worktree session editing a stow-source rule file won't see its own edit take effect there.

The `.claude/worktrees/` segment in the new pattern is what keeps it off the user-scope copy's link target. A pattern without that segment suppresses both copies, because a `claudeMdExcludes` pattern matching a rules file's link target excludes it the same as matching its own path.

The tail is `/**`, the shape the Claude Code documentation uses for a rules directory. Observation confirmed it matches a direct child. It also covers a rule file later placed in a subdirectory of `claude/.claude/rules/`, a case no rule file occupies today and which was therefore not observed directly.

The entry reaches only a worktree created under `.claude/worktrees/`. A linked worktree created elsewhere still loads both copies.

The observable symptom, if this entry ever stops matching, is a worktree session carrying each matching stow-source rule twice. Nothing reports that at runtime. A future contributor who notices a larger-than-expected context in a worktree session should re-verify the entry rather than assume it is inert. Reverting the entry restores the double-load and has no other effect.

See [§39](claudemdexcludes-nested-discovery-duplicate.md) for the sibling `CLAUDE.md` exclusion and for why the stow-source `claude/.claude/settings.json` carries no `claudeMdExcludes` entry.

## Sources

- [Claude Code memory docs](https://code.claude.com/docs/en/memory), "Exclude specific CLAUDE.md files" — the either-path symlink rule and absolute-path matching.
- [Claude Code large-codebases docs](https://code.claude.com/docs/en/large-codebases.md) — the rules-file reach of `claudeMdExcludes`.
- `.claude/plans/dedupe-nested-rules-dir.md` — full assumption ledger, mechanism-by-mechanism reasoning, and verification steps.
