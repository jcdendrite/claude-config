# Update `@claude-config` plugins across every repo on the machine

## Context

`update-claude-config-plugins.sh` only reports and updates project-scope
`@claude-config` plugin installs for the repo it's run from — project-scope
entries belonging to other repos are filtered out by design. The user wants
one command that catches outdated project-scope installs across every repo
on the machine, instead of re-running the script by hand in each repo. The
intended outcome is a new `--all-projects` flag on the existing script that
extends the current single-repo report/update flow machine-wide.

## Approach

**Extend the existing script with a flag, not a variant script or a skill.**

The user proposed three shapes: a script variant, a script argument, or a
"zero-trigger" skill. A flag on the existing script is the right one:

- **Vs. a script variant:** the version-comparison, marketplace-refresh, and
  update-loop logic (~250 lines) would be duplicated wholesale for a change
  that only affects *which* project-scope entries are in scope and *where*
  the update command runs. That's a direct violation of this repo's
  single-source-of-truth rule — one script, one flag.
- **Vs. a skill:** this task is fully mechanical — group already-known data
  by a field, then loop a shell command — no model judgment is involved. A
  skill (even one with no auto-trigger description) still appears in the
  available-skills listing every session, which is the exact context cost
  the user is trying to avoid ("doesn't eat into skill context"). A plain
  script flag has zero session-context cost. This is the same
  default-suspect-over-powered-primitives judgment call as the `respond-pr`
  "should this be a hook?" case: mechanical + deterministic → script, not an
  LLM-mediated surface.

**No filesystem walk to "find all repos."** The framing in the request
("iterate through all repos on a machine") implies scanning the filesystem
for `.git` directories or `.claude/settings.json` files. That's unnecessary:
`claude plugin list --json` already returns every scope=`project` install
machine-wide, each tagged with its `projectPath`, independent of the caller's
cwd — verified live this session:

```
$ claude plugin list --json
[
  {"id": "some-plugin@claude-config", ..., "scope": "project", "projectPath": "/home/user/project-a"},
  {"id": "other-plugin@claude-config", ..., "scope": "project", "projectPath": "/home/user/project-b"},
  ...
]
```

A plugin can only be "installed at project scope in repo X" if the CLI
already recorded that centrally — `plugin list --json` reads that record
directly. A filesystem walk could not discover anything this JSON doesn't
already contain, and it would have to reinvent "what counts as a repo"
(bare `.git`, nested worktrees, nested checkouts) that the CLI's own
bookkeeping already resolved. The only thing the current script does that a
machine-wide mode must stop doing is filtering `projectPath != $PROJECT_ROOT`
out of the report.

**Applying the update needs a real `cd`, not a flag.** `claude plugin update`
and `claude plugin install` take `--scope`/`-s` but no path/`-C` option
(confirmed via `claude plugin update --help` and `claude --help` this
session — no cwd-override flag exists anywhere in the CLI). Project-scope
resolution is cwd-derived, so updating a project-scope entry that belongs to
a different repo than the script's own cwd requires literally `cd`-ing there
first: `(cd "$project_path" && claude plugin update ...)`. User-scope entries
are already cwd-independent and need no such wrapping. Confirmed live this
session, including in a directory Claude Code has never trusted and that
isn't even a git repo:

```
$ (cd /tmp/.../untrusted-dir && claude plugin update "definitely-not-installed@claude-config" --scope project)
Checking for updates for plugin "definitely-not-installed@claude-config" at project scope…
✘ Failed to update plugin "definitely-not-installed@claude-config": Plugin "definitely-not-installed" not found
EXIT: 1
```

No trust prompt, no crash — a clean stderr message and a nonzero exit,
exactly the shape the failure-isolation design below depends on.

**Failure isolation across repos.** A repo directory can have been deleted,
renamed, or made unreadable since its plugin was installed. The existing
script already treats "skip one, keep going" as normal (an interactive "no"
answer doesn't abort the remaining plugins); the machine-wide sweep extends
that shape to update failures and missing paths — warn to stderr, `continue`
to the next entry, and report a nonzero exit code only if any entry actually
failed. This is a new design call (no existing precedent in this script for
a multi-target failure mode), called out here rather than folded in
silently.

### Assumption ledger

- Root problem: one command should catch outdated `@claude-config`
  project-scope installs across every repo on the machine, without a
  separate skill or manual per-repo re-run. `anchors: root`
- Delivery mechanism is a flag on the existing script, not a script variant
  or a skill — see rejected alternatives above (duplication vs. session
  context cost vs. no judgment required). `anchors: root`
  `[verified: claude/.claude/scripts/update-claude-config-plugins.sh read this session]`
- `claude plugin list --json` already returns every scope=project entry
  machine-wide with its `projectPath`, unfiltered by cwd — no filesystem walk
  needed for repo discovery. `anchors: root`
  `[verified: claude plugin list --json output inspected live this session across ≥4 distinct project paths]`
- The `claude` CLI has no cwd-override flag on any `plugin` subcommand; a
  project-scope update must physically `cd` into that project's root.
  `anchors: row3`
  `[verified: claude --help and claude plugin update --help / claude plugin install --help output inspected live this session]`
- Continue-on-failure (skip + warn + nonzero exit) is the right shape for a
  multi-repo sweep, matching the existing script's "skip one, keep going"
  interactive precedent. `anchors: row4`
  `[unverified: no existing precedent specifically for update-command failures across multiple targets — a fresh call, not derived from prior script behavior]`
- New code keeps the file's existing `[ ... ]` test style rather than
  switching to `[[ ... ]]` (the shell-conventions rule's stated preference).
  `anchors: row2`
  `[verified: every existing conditional in update-claude-config-plugins.sh reviewed this session — all use [ ], none use [[ ]]; matching the file's established convention over the abstract style-guide default avoids a mixed-style diff]`

## Critical files

- `claude/.claude/scripts/update-claude-config-plugins.sh`
  - Add `--all-projects` to argument parsing (alongside `--dry-run`,
    `--yes`) and to `usage()`.
  - Step 4 filter: skip the `entry_project_path != $PROJECT_ROOT` exclusion
    when `--all-projects` is set — project-scope entries from every repo
    stay in `OUTDATED_*` arrays. No change to the existing per-line report
    format: it already prints `path: ${entry_project_path}` for every
    project-scope row (redundant with cwd today, informative once multiple
    repos can appear).
  - Update loop: when `--all-projects` is set and the entry's scope is
    `project`, wrap the update call as
    `(cd "$project_path" && claude plugin update "${plugin_name}@claude-config" --scope project)`
    guarded by `if [ ! -d "$project_path" ]` (warn + skip) and
    `if ! ( ... ); then` (warn + skip) — both `if`-guarded per this repo's
    `set -e`/`(( ))` shell convention, so a failing update doesn't abort the
    rest of the sweep. Track a `FAILED_COUNT`; exit 1 at the end if it's
    nonzero. User-scope entries keep the existing unwrapped call (already
    cwd-independent).
  - Update the header comment block (lines 1–23) to describe the new mode.
  - **Reuse:** everything in Steps 1–3 (marketplace refresh, version-map
    build) is untouched — the flag only changes Step 4's filter and the
    update loop.

- `claude/.claude/scripts/tests/test_update_claude_config_plugins.py`
  - New `TestAllProjects` class:
    - `--all-projects --dry-run` includes a project-scope plugin whose
      `projectPath` differs from `cwd` (inverse of the existing
      `TestProjectScopeFiltering.test_other_repos_project_plugins_excluded`,
      which stays as-is to lock in the default-mode behavior).
    - `--all-projects --yes` runs `claude plugin update` with the shim's
      cwd actually set to the entry's `projectPath` — requires extending
      `_make_claude_shim`'s `plugin update` branch to also log
      `os.getcwd()` per call, reusing the existing shim/update-log
      machinery rather than a new fixture.
    - A recorded `projectPath` that doesn't exist on disk: warning on
      stderr, no update call logged for that entry (the shim is never
      invoked for it — the script's own `[ -d "$project_path" ]` guard
      skips before the `claude` call), exit code 1.
    - One project's update fails while another succeeds: sweep continues,
      both are attempted, exit code 1 overall. Requires a third shim
      parameter, `fail_for_plugin_ids: set[str] = frozenset()` — every
      `plugin update` call is still logged (plugin id, scope, `os.getcwd()`,
      and an `outcome` of `"success"` or `"failure"`), but when the incoming
      plugin id is in `fail_for_plugin_ids` the shim additionally prints an
      error line and `sys.exit(1)` instead of `0`. The test passes one
      outdated plugin's id in `fail_for_plugin_ids` and asserts: both
      entries' log lines are present with the correct `cwd` each (proving
      the `cd` happened for both before either result was known), the
      failing entry's logged `outcome` is `"failure"`, the other's is
      `"success"`, and the script's own exit code is 1.
  - **Reuse:** `_make_claude_shim`, `_make_installed_plugin`, `_run_script`,
    `_read_update_log` — extend, don't replace.

- `docs/scripts.md`
  - Update the script's one-line description and usage block to document
    `--all-projects` (and `--all-projects --yes`).

## Verification

- `../../../.venv/bin/pytest claude/.claude/scripts/tests/test_update_claude_config_plugins.py -v`
  (three levels deep from this worktree per this repo's contributor-venv
  convention).
- `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck`
  (repo-wide shell lint; confirms the new `--all-projects` branch doesn't
  introduce quoting/`set -e` issues).
- Manual smoke test is impractical against real machine state (would need
  an actually-outdated `@claude-config` install in a second real repo right
  now) — covered instead by the new pytest fixtures, which exercise the
  cross-repo cd and failure-isolation paths directly.

## Out of scope

- `scope=local` / `scope=managed` entries — the script doesn't handle these
  today (report loop passes through whatever scope string is present); not
  touched by this change.
- Scheduling the sweep (cron, a hook, a periodic reminder) — the user asked
  for the capability to run it, not to automate when it runs.
- Grouping or sorting report output by project path — the existing per-line
  `path: ${entry_project_path}` already surfaces which repo each row belongs
  to; reordering is a formatting nicety, not something requested.
