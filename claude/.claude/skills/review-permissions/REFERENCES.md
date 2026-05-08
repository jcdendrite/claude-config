# review-permissions — Reference notes

## Accepted tradeoffs in the global allow list

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

### `npm run lint`, `npm run typecheck`, `npm run build` global entries

These three entries execute project-controlled `package.json` scripts and are
therefore project-code execution (checklist item 15) at global scope
(checklist item 19). The tradeoff is accepted because:
- The machine is single-user; the operator and the machine owner are the same
  person.
- The benefit (no per-project prompt for routine lint/typecheck/build) outweighs
  the marginal risk on a personal development machine.
- If this repo is ever used on a shared or multi-user machine, these entries
  should be demoted to project-scope `.claude/settings.json`.

Checklist items 10 (PATH-resolved), 15 (project code execution), and 19
(global vs project scope) all apply. Tradeoff accepted with the single-user
constraint noted above.
