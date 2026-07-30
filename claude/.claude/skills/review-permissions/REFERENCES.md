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

### `cleanup-idle-open-pr-worktrees` bare-name entries

`settings.json` contains both absolute-path entries
(`Bash(~/.claude/scripts/cleanup-idle-open-pr-worktrees.sh)`) and bare-name
entries (`Bash(cleanup-idle-open-pr-worktrees)`,
`Bash(cleanup-idle-open-pr-worktrees --dry-run)`), for the same reasons
recorded in the `cleanup-merged-branches` decision above: installed to
`~/.local/bin/` by `install.sh`, calls only absolute paths, execs no
untrusted input.

`--idle-hours=N` invocations are deliberately not pre-authorized — the
no-globs rule (checklist items 1–9) rules out a wildcard entry for an
arbitrary `N`, so a non-default threshold prompts for approval on first use.
This is an accepted ergonomic tradeoff, not a gap: the two pre-authorized
shapes (bare invocation, `--dry-run`) cover the common case.

Checklist item 10 (PATH-resolved commands) applies. Justification accepted.

### `npm run lint`, `npm run typecheck`, `npm run build` — intentionally not in the global allow list

These three entries would let subagent-dispatched checks (see
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

## Allowing privileged scripts — three-layer strict-shape pattern

Claude Code permission rules are glob-only (no regex, no alternation, no
`{a,b}`). Chain segments evaluate independently (`safe-cmd && evil-cmd`
lets the first segment through silently while prompting on the second).
Deny rules cannot carve out exceptions: a deny like `Bash(<script> *)`
matches everything including the valid shapes (deny runs before allow;
allow is never reached). Without further enforcement, any shape outside
the explicit allow list **prompts** the user — prompt fatigue leads to
absent-minded approvals.

When you legitimately need to allow a privileged script, use all three
layers:

1. **`settings.json` `permissions.allow`** — exact-string `Bash(...)`
   entries for each valid invocation shape (no trailing `*`). Ships to
   all stow users. Achieves silent auto-approval for known-good forms.
2. **Dedicated `PreToolUse:Bash` hook** — fast-exit 0 if the script name
   doesn't appear; otherwise match the full command string against
   anchored regex patterns for the N valid shapes. Exit 2 + usage on
   stderr for anything else. Do not include attacker-controlled bytes
   verbatim in the error message (truncate to 80 chars).
3. **Script's own arg validation** — `case` whitelist with explicit
   mismatched-combination rejection. Third independent layer; holds if
   the hook is bypassed.

For trivial scripts that call only absolute paths and do not exec
untrusted input, layer 3 alone may be sufficient — see the
`cleanup-merged-branches` decision above for that case.

Verified against the official permissions docs at
`code.claude.com/docs/en/permissions.md`.
