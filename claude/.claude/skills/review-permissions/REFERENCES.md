# review-permissions — Reference notes

## Decisions on global allow list entries

### `cleanup-merged-branches` — destructive form moved to `permissions.ask`

`settings.json` contains both absolute-path and bare-name entries for this
script, split across `permissions.allow` and `permissions.ask`:
`Bash(cleanup-merged-branches --dry-run)` (and its absolute-path form) are
`allow`; `Bash(cleanup-merged-branches)` (and its absolute-path form) are
`ask`.

The destructive form was originally `allow`, on the reasoning that the script
calls only absolute paths and execs no untrusted input (still true, and still
why `--dry-run` is silently auto-approved). That reasoning covered *argument
injection*, not *invocation without confirmation* — the script's own Tier A
branch classification deletes without an internal per-branch prompt (see
`docs/scripts.md`), so a global silent `allow` on the destructive form meant
nothing outside the script confirmed a delete before it ran. Moving it to
`permissions.ask` restores that confirmation at the Claude Code layer, and
does so in every permission mode: per the primary source
(`code.claude.com/docs/en/permissions`), ask outranks allow, and
`bypassPermissions` "skips permission prompts, except those forced by explicit
`ask` rules." Bare-name entries stay accepted at global scope for the same
reason as before — installed to `~/.local/bin/` by `install.sh`. This is a
plausibility argument, not a guarantee of the checklist's "unshadowable" bar:
it holds only so long as `~/.local/bin` precedes any project- or
tool-injected PATH entry of the same name (direnv, asdf/nvm shims, a
project's own `./bin`) at invocation time. Accepted pre-existing exposure,
unchanged by the `ask` move — the destructive form's exposure is reduced by
this change, not eliminated by it.

Checklist item 10 (PATH-resolved commands) applies. Justification accepted.

### `cleanup-idle-open-pr-worktrees` — destructive form moved to `permissions.ask`

Same split and same rationale as `cleanup-merged-branches` above:
`Bash(cleanup-idle-open-pr-worktrees --dry-run)` (and its absolute-path form)
stay `allow`; `Bash(cleanup-idle-open-pr-worktrees)` (and its absolute-path
form) moved to `ask`. This script has no internal per-worktree prompt at all
(see `docs/scripts.md`), so the `ask` rule is the only confirmation step
before a worktree removal.

`--idle-hours=N` invocations remain not pre-authorized in either list — the
no-globs rule (checklist items 1–9) rules out a wildcard entry for an
arbitrary `N`, so a non-default threshold prompts for approval on first use.
This is an accepted ergonomic tradeoff, not a gap: the two pre-authorized
shapes (bare invocation via `ask`, `--dry-run` via `allow`) cover the common
case.

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
