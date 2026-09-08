# UI and notification preference keys ship as shared defaults in the stow-source settings file

*2026-09-02. Formerly `docs/design-decisions.md` §43.*

Stow installs `claude/.claude/settings.json` as every consumer's user-scope
`~/.claude/settings.json`. Claude Code's settings precedence has five
levels: managed policy, command line, project-local
(`.claude/settings.local.json`), shared project (`.claude/settings.json`),
and user (`~/.claude/settings.json`). None of the five is a user-scope
*local* file.
`~/.claude/settings.local.json`, where a stow user's own
`claude/.claude/settings.local.json` lands, therefore corresponds to no
documented scope, and a direct test confirmed `tui`, `theme`, and
`agentPushNotifEnabled` are not honored there. While the user settings file
is a symlink into this repo, a personal preference and a shipped default are
the same bytes; overriding one means editing the tracked file, the tradeoff
every key in that file already carries.

All three are committed here. `tui: "fullscreen"` has the functional case —
the alternate-screen renderer offers three things the classic renderer
doesn't:

- No redraw flicker.
- Flat memory across long conversations.
- Mouse support (click-to-expand tool output, click-and-drag selection,
  copy-to-clipboard on mouse release).

`theme: "dark"` and `agentPushNotifEnabled: true` have no equivalent
justification; they are defaults, not capabilities.

**The session-keys guard is a drift gate, not a presence ban.**
`guard-settings-session-keys.sh` compares the staged file against `main` key
by key, so a guarded key holding a committed value keeps its protection: a
later `/config` theme change or `/tui classic` still denies the commit that
would ship it. `model: "sonnet"` already sits in this file under that
arrangement. The gate has no agent-side proceed path by design, so the
commit that first introduces a guarded key's value is run by the engineer
directly rather than by a Claude Code session, and the guarded key list stays
untouched. Dropping `theme` and `tui` from that list to unblock one commit
was rejected. Claude Code writes both keys itself. The file is also
reachable through the user-scope symlink, the accidental-commit path the
keys were added to catch in the first place.

**Two entries this file cannot host.** `enabledPlugins` has no per-account
expression under this layout — every Claude Code account on a machine
resolves `settings.json` through the same symlink, so a plugin enabled for
one account is enabled for all. A toggle wanted for a single account, and an
`extraKnownMarketplaces` entry naming one machine's absolute checkout path,
were both dropped rather than committed; the second would be wrong for any
other consumer, whose checkout is elsewhere. A real per-account settings file
belongs in the machine's own provisioning, outside this repo.

**Open discrepancy, not resolved here.** The settings reference lists
`enabledPlugins` and `agentPushNotifEnabled` with scope "Any file", defined
there as effective in all four settings locations including Local.
`claude/.claude/rules/settings-json-conventions.md` states the opposite for
`enabledPlugins`. Both cannot be right, and nothing in this repo tests the
question.

## Sources

- [Claude Code settings](https://code.claude.com/docs/en/settings) — the five-level precedence model and each level's file path.
- [Claude Code settings reference](https://code.claude.com/docs/en/settings-reference) — per-key scope column, including the "Any file" scope cited above.
- [Fullscreen mode](https://code.claude.com/docs/en/fullscreen) — alternate-screen renderer behavior, memory, and mouse support.
- `claude/.claude/hooks/guard-settings-session-keys.sh` and [`docs/hooks.md`](../hooks.md) — the guarded key set and the staged-vs-`main` comparison.
