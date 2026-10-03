# `settings.base.json` ships no `model`, `theme`, `tui`, or `agentPushNotifEnabled`

*2026-10-02.*

`settings.base.json` is the tracked source that `render-settings.sh` merges into every consumer's `settings.json`. A key base defines always wins over the live file, so a shipped `theme` would overwrite a consumer's own `/theme` choice on every render. A key base does not define carries forward from the live file instead, which is how `/config`, `/theme`, and `/tui` choices survive a render.

The four keys therefore stay out of base. They are per-user preferences that Claude Code writes into the live file itself, and carry-forward is the only mechanism that keeps a consumer's value.

**Cost.** A consumer whose `~/.claude/settings.json` is a dangling symlink or absent at the first render has no prior values to carry. Every key base does not ship is dropped at that render, and a render with no prior file names nothing it dropped. A consumer who first materializes the symlink as a regular file, per the `CHANGELOG.md` Migration steps, keeps every key base and the overlay do not define. The four keys reset to Claude Code's defaults once. `tui: "fullscreen"` was the one shipped value with a functional case (no redraw flicker, flat memory across long conversations, mouse support), so a consumer who wants it runs `/tui` after the first render. The `CHANGELOG.md` Migration line and README's migration notes name the restore paths.

**Opt-outs of base-shipped settings.** A key base defines (`attribution`, `disableArtifact`, `disableWorkflows`, `syncClaudeAiSkills`) and the base-owned `permissions` always take base's value. At user scope, an opt-out of one, such as a removed `permissions.deny` rule, persists only as an edit to `claude/.claude/settings.base.json` in the checkout. An edit in the live `~/.claude/settings.json` is reverted by the next render. Removing a base-defined key from base also requires deleting it from `~/.claude/settings.json`, because rule 3 carries the prior render's value forward once base stops defining it. Setting a different value in base takes effect on its own. For `disableArtifact` and `disableWorkflows`, a project's `.claude/settings.local.json` is the project-scope route (see README.md's "Artifact and Workflow disabled by default"). A prior opt-out made in the old tracked file is reverted by the first render, because base's value wins.

**The `model` default.** Dropping `model` also drops the `model: sonnet` default that [`cost-levers-considered.md`](../cost-levers-considered.md) records landing on 2026-08-14 as a coherence fix. A consumer whose Claude Code default is Opus restores Sonnet by exporting `ANTHROPIC_MODEL=sonnet`.

**Alternative rejected: a default-if-absent tier in the render.** It would restore the shipped values on the first render while still letting a consumer's choice win afterwards. It adds a fifth rule to a merge that already has four: base-owned keys win, overlay-allowed keys never carry forward, other keys carry forward, and two dotted `env` paths are re-applied.

The settings-scope findings and the session-keys-guard reasoning in [ui-notification-defaults-in-stow-source.md](ui-notification-defaults-in-stow-source.md) still hold.
