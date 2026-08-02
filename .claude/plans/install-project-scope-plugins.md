# Auto-install claude-config's own project-scope plugins in install.sh

## Context

`./install.sh` should leave a fresh machine with `claude-hook-review`,
`skill-management`, and `plugin-semver` actually usable inside the
claude-config checkout, matching what this repo's own committed
`.claude/settings.json` already declares — but today it doesn't, so a new
contributor hits "Unknown skill" the first time they try to invoke one.

Root cause, confirmed this session on a real affected machine:
`.claude/settings.json` (repo root) declares
`enabledPlugins: {"skill-management@claude-config": true,
"claude-hook-review@claude-config": true, "plugin-semver@claude-config":
true}` (added across #181, #207/`ccab348`, #290/`eafcf29`). Actually
*installing* a project-scope plugin is a separate, machine-local action
(`claude plugin install <name>@claude-config --scope project`) recorded in
`~/.claude/plugins/installed_plugins.json`. On this machine that file has
zero `claude-config`-marketplace entries — confirmed by reading it directly
— and this session's own available-skills listing is missing all three
plugin skills, matching the user's reported errors. `install.sh`'s existing
"Installing plugins from enabledPlugins" step (lines 137–147) only reads
`$HOME/.claude/settings.json` (the global, stowed settings file) and only
ever calls `claude plugin install "$plugin" -s user` — it has no code path
that reads the project-root `.claude/settings.json` or installs at
`--scope project`. `update-claude-config-plugins.sh` (added by the prior
`update-plugins-across-repos` plan) only *updates already-installed*
`@claude-config` plugins; it explicitly reports "No @claude-config plugins
are installed" and stops when none exist yet, so it doesn't cover first-time
setup either.

The intended outcome: running `./install.sh` from a claude-config checkout
also installs, at project scope, every plugin this repo's own
`.claude/settings.json` declares `true` — no separate manual step, and no
change needed the next time a plugin is added or removed from that map.

## Approach

**Add a second install loop to `install.sh`, nested inside the existing
`if [ -f "$SETTINGS_FILE" ]; then … fi` block (lines 82–148), sourced from
the project's own `.claude/settings.json`.** It mirrors the existing
user-scope loop's shape (`enabledPlugins`-read → already-installed check →
install), generalized over whatever `enabledPlugins` currently contains —
not a hardcoded three-name list, so adding or removing a plugin from
`.claude/settings.json` (as already happened three times in this repo's
history) needs no further `install.sh` change. It only ever fires when
`install.sh` is run from inside its own containing checkout — `install.sh`
isn't copied into or distributed to other repos, so there's no risk of it
misfiring against a downstream consumer's own `.claude/settings.json`.

**Nesting inside the existing `if`, not a separate top-level block, is a
correctness requirement, not a style choice.** The project-scope install
calls `claude plugin install "$plugin@claude-config" -s project` against the
`claude-config` marketplace, which is registered a few lines earlier in the
*same* `if [ -f "$SETTINGS_FILE" ]` block (lines 90–113). If that guard is
false (stow partially failed, first-run race), the marketplace never gets
registered — a separate, independently-guarded new block would still run
and fail the whole script under `set -e` with a confusing "marketplace not
found" error instead of skipping cleanly alongside the rest of the
marketplace/plugin setup it depends on.

**Alternatives considered and set aside:**

- **Document a manual post-clone step instead of automating it.** Already
  the status quo — README:187-196 documents each plugin's
  `claude plugin install <name>@claude-config --scope project` command, and
  a commit message on `eafcf29` even called it out as a "Post-merge plugin
  refresh" step for existing consumers. This is the exact mechanism that
  just failed: a real user, on a real new machine, missed it. A design that
  already produced the reported incident isn't a fix for the reported
  incident. Rejected.
- **Ship a generic "install my repo's own project-scope plugins" helper that
  any consumer repo could adopt.** Over-scoped: no consumer repo has asked
  for this, and it would mean distributing/documenting a second reusable
  entry point beyond what this task needs (Axis 4, minimal targeted change).
  claude-config only needs its own `install.sh`, run from its own checkout,
  to install its own declared plugins. Rejected.

### Assumption ledger

- Root problem: a new contributor's `./install.sh` run leaves this repo's
  own declared project-scope plugins uninstalled, so `Skill(claude-hook-review...)`
  and friends fail with "Unknown skill" the first time they're invoked.
  `anchors: root`
- Fix is a second loop in `install.sh`, generalized over `enabledPlugins`
  rather than a hardcoded plugin list, nested inside the existing
  marketplace-registration `if` block rather than a sibling block — lighter
  than either rejected alternative above, and closes the ordering hazard a
  sibling-block design would reopen. `anchors: root`
  `[verified: install.sh:82-148 and README:187-196 read this session]`
- `claude plugin list --json` includes `scope` and `projectPath` fields per
  entry, and a project-scope entry's `id` is only unique per `(id,
  projectPath)` pair, not globally. `anchors: row1`
  `[verified: this session, live — installed `skill-management@claude-config`
  at `--scope project` from a scratch directory and confirmed the exact
  returned shape:
  `{"id": "skill-management@claude-config", "version": "3.0.2", "scope":
  "project", "enabled": true, "installPath": "...",
  "projectPath": "/private/tmp/.../plugin-scope-check"}`; then uninstalled it
  and confirmed `claude plugin list --json` returns no project-scope entries
  again — this is a from-scratch verification in this session, not a
  citation of the `update-plugins-across-repos` plan's illustrative
  (placeholder-path) example]`
- `claude plugin install --help` lists only `--config` and `-s/--scope
  <user|project|local>` — no cwd-override flag exists, so project-scope
  resolution is cwd-derived and the install command must run with `$PWD` at
  `$REPO_DIR`. `anchors: row2`
  `[verified: this session, live — ran `claude plugin install --help`
  directly, full output captured, confirming no path/`-C`-style flag]`.
  `install.sh` already does `cd "$REPO_DIR"` near the top (line ~15), so no
  additional `cd` is needed for the new loop.
- The new loop's "already installed" match (plugin id + canonicalized
  `projectPath` compared to `$REPO_DIR`) must use the *fixed* canonicalization
  idiom already established in this file
  (`if ! resolved="$(readlink -f -- "$path" 2>/dev/null)"; then resolved="$path"; fi`,
  from `ensure_local_bin_on_path`, lines 208-210) — not the `cmd1 2>/dev/null
  || cmd2` form used in the marketplace-registration block above it (line
  101). The `||`-inside-`$()` form can concatenate BSD `readlink -f`'s
  partial stdout with the fallback `echo` when `readlink` writes output and
  then still exits non-zero on a dangling target — the two would land in one
  captured string, silently corrupting the comparison. `anchors: row3`
  `[verified: install.sh:202-210's own comment documents this exact BSD
  hazard, and lines 208-210 show the fixed idiom already used elsewhere in
  the same file — a staff-platform-engineer plan-review pass on an earlier
  draft caught that this plan's first draft cited the *broken* pattern
  (lines 90-101) as its precedent instead]`
- The pre-existing marketplace-registration block (line 101) has the same
  broken `||`-inside-`$()` idiom this plan's new code must avoid — a
  genuinely dangling-target case in that exact call site (a moved/renamed
  checkout, which is the whole scenario `relocate-claude-config` exists to
  handle), not just this new code's equivalent check. `anchors: row3`
  `[verified: same plan-review pass; treated as a small, same-file,
  same-bug-shape incidental fix per this repo's Axis-1 "audit structural
  siblings" guidance — see Critical files]`
- A transient `claude plugin list --json` failure is silently swallowed:
  `install.sh` uses `set -e` but not `pipefail`, so a failing `claude`
  invocation piped into `jq` reports `jq`'s exit status (0, on empty stdin),
  and `existing_project_plugins` becomes an empty string — every plugin then
  reports `→ installing` instead of surfacing the underlying failure. This
  is inherited from the identical pattern in the existing user-scope loop
  (line 139), not introduced by this plan, and is left as-is here for the
  same reason the sibling loop leaves it as-is: fixing it would mean adding
  `pipefail`-sensitive error handling to a shared script pattern used by two
  loops, which is a broader behavior change than this incident requires.
  `anchors: row4` `[verified: install.sh's `set -euo` header — actually just
  `set -e` (line 2) — confirmed to lack `pipefail` this session]`
- Malformed JSON in the project's own `.claude/settings.json` makes the
  `enabledPlugins`-parsing `jq` call fail to stderr with zero stdout lines;
  because it runs inside `< <(...)` process substitution, `set -e` does not
  see that failure, and the `while read` loop simply never iterates — the
  block prints its two header lines and looks like a normal no-plugins-to-install
  run. This is the same silent-failure *shape* as the incident this plan
  fixes (a broken settings file quietly disabling plugin installation), so
  unlike row4 this one gets a one-line explicit stderr warning in the new
  code rather than being left inherited-and-unaddressed — see Critical
  files. `anchors: row5`
  `[verified: this session, by tracing process-substitution + `set -e`
  interaction against Bash's documented behavior — `set -e` does not apply
  to commands whose exit status is never read by the parent shell, which is
  exactly what `< <(...)` does]`
- A single `claude plugin install ... -s project` failure (network blip,
  registry hiccup) would, under `set -e`, abort the entire `install.sh` run
  before it reaches later, purely-local, unrelated steps —
  `check_private_projects_file`, `ensure_local_bin_on_path` (PATH setup),
  and the `python3`/`ensurepip` check. The existing user-scope loop (line
  145) has this identical fragility already; this plan adds three more
  network-dependent calls in front of those same unrelated steps, which
  measurably widens the blast radius even though it doesn't introduce the
  failure mode. `anchors: row6` `[verified: install.sh's overall structure
  read this session — the PATH-setup and Python-ensurepip checks run only
  after both plugin-install blocks]`. Resolution: make only the **new**
  block's install calls non-fatal (log a warning, continue to the next
  plugin) — see Critical files. The existing user-scope loop's identical,
  pre-existing fragility is not touched (Axis 1: it's not this plan's file
  boundary to change error-handling semantics on already-shipped,
  unmodified code) and is named explicitly in Out of scope instead of
  silently left alone.
- Test coverage: the pure bash/jq matching logic (does plugin id + a
  canonicalized `projectPath` match `$REPO_DIR`) is extracted into a named
  function and unit-tested directly with hardcoded TSV fixtures — no live
  `claude` CLI needed. The full block's control flow (iterating
  `enabledPlugins`, printing ✓/→, invoking `claude plugin install`) is
  tested with a stub `claude` executable placed on `PATH`, mirroring the
  `_bin_dir_with_failing_syntax_check` stubbing technique already used in
  `test_install_sh_local_bin_path.py`. Only the live, real
  `claude plugin install` network call itself stays untested, matching the
  sibling user-scope loop's precedent. `anchors: row7`
  `[verified: this session — grepped claude/.claude/hooks/tests/ and
  claude/.claude/scripts/tests/ for `enabledPlugins`/`plugin install`
  (zero matches, confirming no prior coverage to conflict with), and read
  `test_install_sh_local_bin_path.py`'s `_bin_dir_with_failing_syntax_check`
  and `_file_has_active_reference` to confirm both the CLI-stub and
  named-function-extraction patterns are already established in this repo]`

## Critical files

- **`install.sh`**:
  1. Insert a new block immediately after the existing "Installing plugins
     from enabledPlugins" `while` loop (currently ends line 147), *before*
     the `fi` on line 148 that closes `if [ -f "$SETTINGS_FILE" ]` — keeping
     it nested, per the ordering rationale above. Wrap the pure-matching
     function in its own `INSTALL_TEST_FIXTURE: project-plugin-match`
     markers (mirroring `_file_has_active_reference`'s markers at lines
     165-168) so it can be extracted and unit-tested without a `claude`
     stub, and wrap the surrounding install loop in a second
     `INSTALL_TEST_FIXTURE: project-scope-plugin-install` marker pair for
     the stubbed-CLI test:

     ```bash
     echo ""
     echo "=== Installing this repo's own project-scope plugins ==="
     PROJECT_SETTINGS_FILE="$REPO_DIR/.claude/settings.json"
     if [ -f "$PROJECT_SETTINGS_FILE" ]; then
       # INSTALL_TEST_FIXTURE: project-plugin-match — start
       # Whether $1 (a "name@marketplace" plugin id) is already installed at
       # project scope for $REPO_DIR, given $2 as a "$id\t$projectPath" TSV of
       # existing scope=="project" entries. Canonicalizes each entry's path
       # the same way ensure_local_bin_on_path does (lines 208-210), not the
       # `cmd1 || cmd2`-inside-`$()` form used above for marketplace
       # registration — that form can concatenate BSD readlink's partial
       # stdout with the fallback on a dangling target.
       _project_plugin_already_installed() {
         local plugin_id="$1" existing_tsv="$2" entry_id entry_path entry_path_real
         while IFS=$'\t' read -r entry_id entry_path; do
           [ -z "$entry_id" ] && continue
           [ "$entry_id" != "$plugin_id" ] && continue
           if ! entry_path_real="$(readlink -f -- "$entry_path" 2>/dev/null)"; then
             entry_path_real="$entry_path"
           fi
           [ "$entry_path_real" = "$REPO_DIR" ] && return 0
         done <<< "$existing_tsv"
         return 1
       }
       # INSTALL_TEST_FIXTURE: project-plugin-match — end

       # INSTALL_TEST_FIXTURE: project-scope-plugin-install — start
       existing_project_plugins="$(claude plugin list --json 2>/dev/null | jq -r '.[] | select(.scope == "project") | "\(.id)\t\(.projectPath)"')"
       enabled_project_plugins="$(jq -r '.enabledPlugins // {} | to_entries[] | select(.value == true) | .key' "$PROJECT_SETTINGS_FILE")" || \
         echo "[install] warning: could not parse enabledPlugins from $PROJECT_SETTINGS_FILE — skipping project-scope plugin install" >&2
       while read -r plugin; do
         [ -z "$plugin" ] && continue
         if _project_plugin_already_installed "$plugin" "$existing_project_plugins"; then
           echo "  ✓ $plugin (already installed)"
         else
           echo "  → installing $plugin"
           claude plugin install "$plugin" -s project || \
             echo "[install] warning: failed to install $plugin at project scope" >&2
         fi
       done <<< "$enabled_project_plugins"
       # INSTALL_TEST_FIXTURE: project-scope-plugin-install — end
     fi
     ```

     (Switched the `enabledPlugins` read from `< <(jq ...)` process
     substitution to a captured variable + `<<<` here-string specifically so
     the `|| echo warning` on the `jq` call itself is reachable — a failure
     inside `< <(...)` is invisible to the surrounding command's exit
     status, which is exactly row5's finding.)

  2. **Incidental fix, called out explicitly**: apply the same
     `if ! resolved=...; then resolved=...; fi` idiom fix to the
     pre-existing marketplace-registration block's `claude_config_recorded_real`
     assignment (line 101), which has the identical broken
     `cmd1 2>/dev/null || cmd2`-inside-`$()` shape this plan's new code must
     avoid repeating. Small, same-file, same-bug-shape, no behavior change
     on the non-dangling-target path — the only path this changes is the
     one where the recorded marketplace path no longer exists, which today
     silently risks a corrupted comparison instead of a clean fallback.

- **`README.md`** (lines 187-196) — reuse opportunity, not new content: the
  per-plugin "install at project scope" commands stay as-is (still correct
  for a *downstream* consumer repo choosing to adopt a plugin), but the
  lead-in sentence ("`./install.sh` registers the `claude-config`
  marketplace automatically... Then install any of the plugins below at
  project scope") gains one clause noting that `install.sh`, run from within
  claude-config's own checkout, now installs this repo's own declared set
  automatically.

- **`claude/.claude/hooks/tests/test_install_sh_project_scope_plugins.py`**
  (new file) — reuse opportunity: copy the extraction-by-marker technique
  from `test_install_sh_local_bin_path.py` (`_extract_local_bin_block`) and
  the stub-binary-on-`PATH` technique from its
  `_bin_dir_with_failing_syntax_check`. Two test classes:
  - Pure-function tests against the `project-plugin-match` fixture: match on
    exact id+path; no match on right id/wrong path; no match on wrong id;
    canonicalization normalizes a `..`-containing path to match; a dangling
    `readlink -f` target falls back to raw-string comparison instead of
    raising.
  - Stubbed-CLI tests against the `project-scope-plugin-install` fixture,
    using a fake `claude` script on `PATH` that reads a canned JSON fixture
    for `plugin list --json` and appends its args to a log file for `plugin
    install`: fresh machine (empty existing list) installs all declared
    plugins; already-installed machine (existing list matches `$REPO_DIR`)
    installs none and prints `✓`; empty `enabledPlugins: {}` is a no-op;
    malformed `PROJECT_SETTINGS_FILE` JSON prints the new warning and
    doesn't crash the block.

## Verification

1. On this exact machine (the one with the confirmed-missing installs), run
   the updated `./install.sh` from the claude-config checkout and confirm
   the new "=== Installing this repo's own project-scope plugins ==="
   section reports `→ installing` for all three plugins (not `✓ already
   installed`, since `~/.claude/plugins/installed_plugins.json` currently
   has zero `claude-config` entries — confirmed this session).
2. Re-run `./install.sh` a second time immediately after; confirm all three
   now report `✓ already installed` — proves the already-installed check
   works and the step is idempotent.
3. Start a new Claude Code session in the claude-config repo and confirm
   `Skill(claude-hook-review:claude-hook-review)` (and the other two) now
   resolve instead of "Unknown skill" — this directly re-tests the reported
   entity from this incident, per root-cause-analysis Stage F.
4. `git diff .claude/settings.json` before and after step 1 — confirm the
   local `enabledPlugins: {}` drift either resolves back to the 3-entry map
   or is otherwise explained; if it doesn't self-resolve, note that as a
   follow-up.
5. `../../../.venv/bin/pytest claude/.claude/` (including the new test file)
   and `../../../.venv/bin/shellcheck install.sh` to confirm the new block
   passes and doesn't introduce shellcheck findings.

## Out of scope

- Reconciling the pre-existing uncommitted `claude/.claude/settings.json`
  diff on this machine (the `clangd-lsp`/`swift-lsp`/`theme`/`tui`
  additions) — unrelated local drift in the global stowed settings file,
  not caused by and not fixed by this change.
- The existing **user-scope** plugin-install loop (line 145) has the same
  `set -e`-aborts-the-whole-script fragility on a single plugin's install
  failure as the new project-scope loop would without this plan's `||
  echo warning` fix. That loop is pre-existing, unmodified code outside this
  plan's file boundary — flagged here for a future follow-up, not silently
  fixed and not silently ignored.
- The existing user-scope loop's swallowed-`claude plugin list --json`-failure
  behavior (row4 above) — inherited by the new loop, not introduced, and not
  fixed for either loop in this plan.
- Extending `update-claude-config-plugins.sh` to also handle first-time
  installs (only handles updates to already-installed plugins today) — a
  reasonable follow-up, but a different script with its own review surface.
