# review-permissions — Reference notes

## Decisions on global allow list entries

### `cleanup-merged-branches` bare-name entries

`settings.json` contains both absolute-path entries
(`Bash(~/.claude/scripts/cleanup-merged-branches.sh)`) and bare-name entries
(`Bash(cleanup-merged-branches)`, `Bash(cleanup-merged-branches --dry-run)`).

The bare-name entries are accepted at global scope because:
- The script is installed to `~/.local/bin/` by this repo's `install.sh` and
  is not a name any project is likely to shadow.
- The script itself calls only absolute paths; it does not exec untrusted input.
- The absolute-path fallback entries remain as the preferred form; the bare-name
  entries exist for ergonomic invocation when `~/.local/bin` is in `$PATH`.

Checklist item 10 (PATH-resolved commands) applies. Justification accepted.

### `npm run lint`, `npm run typecheck`, `npm run build` — intentionally not in the global allow list

These three entries would let subagent-dispatched verify-class commands (see
CLAUDE.md "Heavy command output") inherit permissions and run without prompt.
They are deliberately absent from the global allow list because:

- `lint`/`typecheck`/`build` are project-controlled `package.json` script names.
  At global scope, a malicious project's scripts auto-fire the moment Claude Code
  opens the directory, with no prompt. This is checklist items 15 (project code
  execution) and 19 (global vs project scope) compounded.
- Subagent permission inheritance does not honor project-scope
  `.claude/settings.json`, so demoting these to project scope does not restore
  subagent dispatch. The resulting per-project re-prompt friction is the deliberate
  cost of removing global auto-trust on project-controlled scripts.
- Per-project re-allow is an explicit decision: a committed `.claude/settings.json`
  works for parent-direct invocations on a trusted project; a gitignored
  `~/.claude/settings.local.json` works for subagent dispatch on this machine only.

Do not reintroduce these to the stowed global allow list without re-evaluating
the trust boundary.
