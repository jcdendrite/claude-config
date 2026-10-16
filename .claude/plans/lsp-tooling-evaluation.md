# Evaluate installing a Python LSP for this repo

## Context

Enable `pyright-lsp` (Python code-intelligence) for claude-config itself, scoped to this repo only — not to fix stale `file:line` references (LSP doesn't touch that), but for its own real benefit: automatic post-edit diagnostics and precise navigation across the repo's 149 Python files (~110K lines, mostly hooks and their tests), which today get no diagnostics feedback beyond a manually-run `ruff check` / `pytest`.

Ask: enable pyright-lsp for this repo only (original request, prior session, paraphrased from Context); narrowed this session to contributors: "Pyright-lsp should only be installed for contributors to the repo so it should actually be part of install-dev.sh" — paraphrase accepted via label "Accept the Ask line".

The engineer's original framing was that installing a Python and shell LSP would make `file_path:line_number` references (as seen throughout the merged discovery-audit-remediation-plan, the PR referred to as "PR 742," which embeds 162 such references) resilient to drift during a large refactor. That specific premise doesn't hold — verified below — so this plan proceeds on the narrower, real benefit instead, per the engineer's explicit go-ahead after seeing that finding.

## Approach

Install `pyright-lsp` from `install-dev.sh`, the contributor-only setup script, at local scope. Remove it from this repo's committed `.claude/settings.json`, so `./install.sh` no longer installs it for every stow consumer who runs it from a checkout. `install-dev.sh` installs the plugin only when the `claude` CLI is present and `claude-plugins-official` is already registered on the profile. It never registers the marketplace itself, and it checks for the `pyright-langserver` binary but never installs it. `install.sh`'s already-installed matcher moves into a shared sourced library so both scripts use one copy. As before, the plan skips a shell LSP and the stow-package scope.

This revision replaces the prior draft's design: a project-scope enable, a project-level marketplace declaration with `autoUpdate: false`, and a registration check in `install.sh`. The engineer's answer to the prior open decision 1 moved the plugin to the contributor path (row 20). A declaration in a tracked settings file reaches everyone who opens this checkout, contributor or not.

**Open decisions for the engineer.** Both were answered after this draft was returned, each with its recommended option; rows 39 and 40 record the answers, and the plan below is written to them.

1. **Where the hang-risk comment lives.** This is a real decision because the engineer's "Keep it (Recommended)" (row 22) answered a question that assumed `install.sh` installs `pyright-lsp`. After this change, `install.sh`'s loop installs only `claude-config` plugins, which come from a directory-source marketplace (row 34). The comment's GitHub-sourced example then names a case the loop never reaches. The GitHub-sourced install now happens in `install-dev.sh`.
   - **(a) Move the comment to `install-dev.sh`'s `claude plugin install` call.** Recommended, and written into this plan. `install.sh` loses those three lines.
   - **(b) Keep it in `install.sh` and drop the `(e.g. pyright-lsp@claude-plugins-official)` example.** This honors "Keep it" literally, but it documents a case no current entry hits.

   (b) changes only those comment lines in both scripts, so it needs no re-dispatch.
2. **Confirm that row 20 retires the project-level marketplace declaration.** This is a real decision because the engineer selected "Project-level marketplace (Recommended)" (row 7), and this plan removes that declaration. Removing it is the plan's reading of row 20 (row 36), not the engineer's words. A declaration in the tracked `.claude/settings.json` prompts everyone who trusts this folder to register the marketplace, contributor or not.
   - **(a) Drop it.** Recommended, and written into this plan.
   - **(b) Keep it.** This brings back the prompt for non-contributors and prior rows 11-14 (`autoUpdate`). Re-dispatch if the engineer picks (b).

**Settled design points** (evidence settles these, so they are not escalated):
- **(a) Check for the marketplace and print the command; never add it.** A `--scope local` add still registers the marketplace across the whole profile (G3). It would silently undo a deliberate removal under #1033's Migration line (row 16). Profiles that never removed it already have it, because Claude Code adds it on the first interactive session (row 9). Flipping this later costs one add line and one test.
- **(b) Print the `pyright-langserver` install command; never run it.** `pipx` is not a dependency this repo declares (row 33). It installs outside `.venv`, and the `pyright` package is deliberately unpinned (row 6). The script otherwise installs only pinned dependencies, and the network-install hook cannot see commands the script runs (row 30). If the script ran the install, an agent that runs `./install-dev.sh` as a dependency restore would perform a new-package install that no gate reviews. The engineer's row 21 selects the order of the documented commands, not who runs them.
- **(c) A missing `claude` CLI skips the step; it does not fail the script.** The script's purpose is the venv. Its own test harness runs with no `claude` on PATH (row 32). `install.sh` already enforces the CLI for stow setup (README.md:96).
- **(d) The new step reports its own status lines, and `pyright-lsp` never enters `check_venv_healthy`.** A failed probe deletes and recreates `.venv` (row 31). If the probe included `pyright-lsp`, every run without the plugin would delete and rebuild the venv.
- **(e) The hang-risk comment** is open decision 1 above.
- **The repo's remaining declaration of intent:** the plugin-id constant in `install-dev.sh`, the README Tests paragraph, and the new decision file.

**Root problem:** Claude Code sessions in this repo get no automatic Python diagnostics or precise navigation. That is the engineer's original "faster/cheaper" hypothesis, narrowed per Context. The branch currently supplies the plugin through the committed `.claude/settings.json`, and that reaches every stow consumer who runs `./install.sh` from a checkout, not only contributors (row 20).

**Givens:**
- **G1.** Claude Code's LSP integration is opt-in, through official-marketplace "Code Intelligence" plugins that each wrap a separately installed language-server binary. That is how the vendor ships LSP support. [verified: code.claude.com/docs/en/discover-plugins]
- **G2.** The official plugin table has no shell/bash entry. It is Anthropic's catalog, outside this plan's reach. [verified: code.claude.com/docs/en/discover-plugins]
- **G3.** Marketplace registration is per user, so no repo file and no `--scope` can confine a registration to one project. Vendor-imposed. [verified: plugins/loading, "There is one known_marketplaces.json per user, so a marketplace you add in one project is available in every project."]
- **G4.** *Retired in this revision.* It described the trust-gated prompt for a project-declared marketplace, and no declaration remains.
- **G5.** Each contributor's `claude` CLI version is their own. The planning machine runs 2.1.283, which has no `claude plugin install --marketplace` (documented as requiring v2.1.292 or later), so the design cannot assume the one-step form. [verified: `claude --version`, `claude plugin install --help`, plugins/install]

**Mechanisms:**

- **M1: remove `"pyright-lsp@claude-plugins-official": true` from the root `.claude/settings.json`.** This reverses the prior M1. A project-scope entry makes `install.sh` install the plugin for every stow consumer (row 2). It also makes Claude Code report the plugin as not installed to anyone who has not installed it (row 18). `anchors: root, row2, row18, row20`
- **M2: `install-dev.sh` Step 7 runs `claude plugin install pyright-lsp@claude-plugins-official --scope local`.**
  - Lighter alternative (a), rejected: a README-only instruction. It fails row 20's "should actually be part of install-dev.sh".
  - Lighter alternative (b), rejected: have the script print the install command. It fails row 20 the same way.
  - Same-weight alternative (c), rejected: project scope. It fails row 20 (M1).
  - Wider alternative (d), rejected: user scope. It enables the plugin in every project on the profile. It may also write the stow-tracked settings file the way row 15's marketplace add does; that is `[unverified]` for plugin install.
  - Local scope writes only the gitignored `.claude/settings.local.json` (row 26). Whether that activates the plugin, including in worktree sessions, is rows 27-28, settled by Verification step 1.

  `anchors: root, row20, row24, row26, row27, row28`
- **M3: a marketplace check that never adds.**
  - Read registered names via `claude plugin marketplace list --json | jq -r '.[].name'` (row 17).
  - Heavier alternative, rejected: `claude plugin marketplace add anthropics/claude-plugins-official --scope local`. See settled point (a).
  - Reuse: the same check as `register-marketplace.sh:62-63` and `:134-137`, kept inline. The shared part is one `grep -qFx`, which fits CLAUDE.md's "small duplicated value beats a bad abstraction" exception.

  `anchors: row9, row10, row16, row17`
- **M4: a binary check that only prints.** `command -v pyright-langserver`, plus one printed command that leads with `pipx install pyright` and points to README for alternatives. Heavier alternative, rejected: run `pipx install pyright`. See settled point (b). `anchors: row6, row21, row25, row30, row33`
- **M5: move `_project_plugin_already_installed` out of `install.sh` into a new sourced library, `claude/.claude/scripts/_plugin-install-lib.sh`, renamed `_plugin_installed_at_path`.**
  - The directory is now its third argument, in place of the `$REPO_DIR` global.
  - Required by CLAUDE.md's "abstract into a shared helper once two or more share it", now that `install-dev.sh` needs the same id-plus-canonical-path match for local scope (row 29).
  - Lighter alternative (a), rejected: copy the 10-line function into `install-dev.sh`. That duplicates logic, not a value, including the BSD `readlink -f` partial-stdout handling that would drift.
  - Lighter alternative (b), rejected: skip the check and rely on `claude plugin install` being a no-op when the plugin is already installed. That behavior is `[unverified]`, and a failing re-install would print a false warning on every run.
  - Lighter alternative (c), rejected: match on id and scope only. Another checkout's local install would print a false `✓`, which is the failure direction that leaves the plugin disabled here.
  - Placement follows `.claude/rules/bash-unit-test-seams.md` option 3 and the sourcing precedent of `_stow_migration_lib.sh` at install.sh:97-103.

  `anchors: row29, row34`
- **M6: Step 7 is non-fatal and separate from the health probe.** See settled points (c) and (d). `anchors: row31, row32`
- **M7: README's Tests section becomes the single home for the contributor plugin steps.** The Requirements bullets this branch added (README.md:103-106) are removed with no pointer left behind. `install-dev.sh` reports a missing binary itself, and Tests is where contributors already go for `install-dev.sh`. `anchors: row19, row21, row23`
- **M8: a new decision file, retitled to `docs/design-decisions/pyright-lsp-installed-per-contributor.md`.**
  - What still needs a durable home: this repo deliberately does not enable the plugin in its committed settings. A future contributor would most likely "fix" that in the settings file, which can carry no comment.
  - It also records how this change relates to #1033's decision: an opted-in contributor's profile takes on the marketplace again.
  - `install-dev.sh` carries one-line comments pointing to the file.
  - Rejected: comments and README only. Neither reaches the reader who edits `.claude/settings.json`.

  `anchors: row16, row18, row20`
- **M9: the hang-risk comment moves to `install-dev.sh`.** Open decision 1. `anchors: row22, row34, row39`

**Assumptions:**

| # | Claim | Tag |
|---|---|---|
| 1 | LSP diagnostics and navigation apply only to code the agent is actively editing or querying in a live session. Nothing in Claude Code's LSP integration re-validates or updates `file:line` citations already embedded in previously written, merged prose (e.g., a plan doc). | [verified: code.claude.com/docs/en/discover-plugins, code.claude.com/docs/en/common-workflows; neither documents any such re-validation behavior] |
| 2 | After this change, only `install-dev.sh` Step 7 installs `pyright-lsp` automatically. `install.sh`'s project-scope loop installs what the root `.claude/settings.json` enables, and that no longer includes `pyright-lsp`. *Rewritten: previously `install.sh` installed it.* | [verified: install.sh:743-768] |
| 3 | `pyright-langserver` installs via `pip install pyright` or `npm install -g pyright`. The plugin's own README also recommends `pipx install pyright` (row 25). | [verified: code.claude.com/docs/en/plugins-reference] |
| 4 | This repo has no `PostToolUse` hook that runs `ruff`/`pytest` automatically after a Python edit. `ruff check` and `pytest` run only as manual, allowlisted Bash commands. | [verified: claude/.claude/settings.json `permissions.allow` list; its `PostToolUse` block contains no ruff/pytest entry] |
| 5 | Repo composition: 149 tracked `.py` files (~109,840 lines), 76 tracked `.sh` files (~17,263 lines), 347 tracked `.md` files (~80,871 lines). | [verified: `git ls-files` + `wc -l` in the original planning session] |
| 6 | `pyright` stays unpinned and stays out of `requirements-dev.txt`. It never runs in CI or any blocking check. | [verified: requirements-dev.txt; README.md:96 (unpinned `stow`/`git`/`gh`/`jq` precedent)] |
| 7 | The marketplace is declared at project level, not in the stowed settings. *This revision retires the declaration; see row 36, open decision 2, and row 40.* | [engineer-verified: "Project-level marketplace (Recommended)"] |
| 8 | The engineer's reason for that scope. | [engineer-verified: "shouldn't the claude code marketplace be installed at the project level rather than for all stowed users?"] |
| 9 | Claude Code adds the official marketplace the first time an interactive terminal session starts. On a machine with no such session, it is unregistered. | [verified: plugins/install, "Claude Code adds Anthropic's official marketplace for you the first time you start an interactive terminal session"] |
| 10 | It is unknown whether that auto-add fires again on a profile that ran `claude plugin marketplace remove claude-plugins-official` per #1033's Migration line. The Step 7 skip line covers that case either way. | [unverified] |
| 11-14 | *Retired:* the `autoUpdate` behavior of a project-level declaration. No declaration remains. Row 37 carries the schema evidence forward to Out of scope. | (retired) |
| 15 | Every `claude plugin marketplace add` declares the marketplace in a settings file at user (the default), project, or local scope. User scope writes `~/.claude/settings.json`, a stow symlink to the tracked `claude/.claude/settings.json`. | [verified: `claude plugin marketplace add --help` at 2.1.283; plugins/loading; the dispatching session's check of the symlink target] |
| 16 | Any add re-registers the marketplace across the whole profile, including on a profile that removed it per #1033's Migration line. CHANGELOG.md:273 warns against that silent re-add. | [verified: CHANGELOG.md:273; follows from G3 and row 15] |
| 17 | `claude plugin marketplace list --json` emits an array of objects carrying `.name`. | [verified: register-marketplace.sh:62-63] |
| 18 | Claude Code does not fetch a plugin enabled only in project settings, and it reports such a plugin as not installed. *The post-trust-prompt half of this row is retired.* | [verified: plugins/loading, "Enabled in project settings but not installed"] |
| 19 | A PEP 668 externally managed interpreter refuses `pip install --user`. README.md:100 recommends Homebrew for Python ≥ 3.11. Whether Homebrew's Python is marked externally managed on a given machine is unknown. | [verified: claude/.claude/rules/python-environment-conventions.md, README.md:100]; [unverified]: Homebrew's marking |
| 20 | `pyright-lsp` reaches contributors only, through `install-dev.sh`. | [engineer-verified: "Pyright-lsp should only be installed for contributors to the repo so it should actually be part of install-dev.sh"] |
| 21 | The documented binary install command leads with pipx and falls back to `pip --user`. The option description was the session's. This row covers only the selected label. | [engineer-verified: "pipx first, pip --user fallback (Recommended)"] |
| 22 | Keep the `install.sh` hang-risk comment. The question assumed `install.sh` installs `pyright-lsp`; see open decision 1 and row 39. | [engineer-verified: "Keep it (Recommended)"] |
| 23 | `install.sh` already asks whether the user is a contributor and points contributors at `./install-dev.sh`. | [verified: install.sh:952-986; claude/.claude/hooks/tests/test_install_sh_contributor_intent_prompt.py] |
| 24 | `install-dev.sh` is contributor-only, refuses to run without `requirements-dev.txt` in the cwd, and already installs software (a venv plus pinned dependencies). | [verified: install-dev.sh:2-4, 10-13, 94-103] |
| 25 | The plugin's marketplace entry launches a bare `pyright-langserver --stdio`, so the binary must be on the PATH of the process that launches Claude Code. A `.venv/bin` copy is not found. The plugin README recommends `pipx install pyright`. | [verified: the dispatching session's read of the local `claude-plugins-official` clone's `marketplace.json` and plugin README] |
| 26 | `--scope local` exists for `claude plugin install` and `claude plugin marketplace add` on 2.1.283, and writes `.claude/settings.local.json`, which this repo gitignores. | [verified: the dispatching session's `--help` checks; .gitignore:12] |
| 27 | Whether an `enabledPlugins` entry in `.claude/settings.local.json` activates the plugin. `claude/.claude/rules/settings-json-conventions.md:10-11` says it does not, and cites no source. | [unverified]. Verification step 1 settles it |
| 28 | Whether a local-scope install made from the main checkout reaches a session working in a linked worktree under `.claude/worktrees/`. A gitignored `settings.local.json` is not checked out into a worktree. | [unverified]. Verification step 1 settles it |
| 29 | `claude plugin list --json` reports a local-scope install with `.scope == "local"` and a `.projectPath`. For project scope, `install.sh` reads `.scope` and `.projectPath`. | [verified: install.sh:744] for project scope; [unverified] for the local-scope values. Verification step 1 settles them |
| 30 | `deny-network-installs.sh` gates only the Bash tool's command text, so it never sees what `install-dev.sh` runs internally. Typed directly, `pip install --user pyright` is denied (pip family, leftover package token), and `pipx install pyright` matches no family (only `pipx run` with `-y` does). The hook's deny text calls a named install one that "adds software from a registry rather than restoring already-declared dependencies". | [verified: claude/.claude/hooks/deny-network-installs.sh:246-293, 340-363] |
| 31 | When `check_venv_healthy` fails, the script runs `rm -rf .venv` and recreates the venv. | [verified: install-dev.sh:79-92] |
| 32 | `test_install_dev.py` runs the real script with `PATH=<stub dir>:/usr/bin:/bin`, and the stub dir holds a fake `python3`. Existing tests therefore see no `claude`, and a `#!/usr/bin/env python3` shim would resolve to the fake. | [verified: claude/.claude/scripts/tests/test_install_dev.py:68-70, 184-193] |
| 33 | `pipx` is not a declared dependency anywhere in this repo. It happens to be installed on the planning machine. | [verified: the dispatching session's search and `pipx --version`] |
| 34 | After M1, `install.sh`'s loop installs only `@claude-config` plugins. `claude-config` is a directory-source marketplace that `register-marketplace.sh` registers just before the loop runs. | [verified: register-marketplace.sh:65-94; install.sh:715; root `.claude/settings.json` lines 3-6] |
| 35 | pipx links binaries into `~/.local/bin` by default. If not, Step 7's PATH check reports the binary as missing either way. | [unverified] |
| 36 | Row 20 retires the project-level declaration that rows 7-8 selected. This is the plan's reading, not the engineer's words. *Confirmed by row 40.* | [unverified]. Open decision 2 |
| 37 | SchemaStore defines `autoUpdate` on an `extraKnownMarketplaces` entry: "Whether to automatically update this marketplace on Claude Code startup." Whether CLI 2.1.283 honors it at runtime is unknown. | [verified: schemastore.org/claude-code-settings.json, extraKnownMarketplaces entry schema]; [unverified]: runtime |
| 38 | No CHANGELOG entry has ever covered an `install-dev.sh` change. | [verified: CHANGELOG.md has zero `install-dev` matches] |
| 39 | Open decision 1: the hang-risk comment moves to `install-dev.sh`'s `claude plugin install` call, and `install.sh` loses the three lines. | [engineer-verified: "Move to install-dev.sh (Recommended)"] |
| 40 | Open decision 2: the project-level `claude-plugins-official` declaration is dropped; no committed settings file declares the marketplace. | [engineer-verified: "Drop it (Recommended)"] |
| 41 | The Context `Ask:` line, added at plan-review, paraphrases the prior-session request and quotes this session's narrowing. | [engineer-verified: "Accept the Ask line"] |
| 42 | M5, the shared plugin-match library extraction (Dispatch A), is a deliverable the ask does not call for; the engineer kept it. | [engineer-verified: "Shared plugin-match library"] |
| 43 | M8, the new design-decision file, is a deliverable the ask does not call for; the engineer kept it. | [engineer-verified: "Design-decision doc"] |
| 44 | `select-tests.py` gains an `install-dev.sh` row in Dispatch B, with a `test_select_tests.py` case. Added at plan-review on a staff-sdet finding. | [engineer-verified: "Add the row in Dispatch B (Recommended)"] |

**Changes from the prior draft:**
- Retired: G4, rows 11-14, and the second half of row 18.
- Rewritten: row 2, with the new install path.
- Extended: row 3 (pipx).
- Annotated, not rewritten: rows 7 and 22. Open decisions 2 and 1 ask about them; rows 39 and 40 record the answers.
- Added: rows 20-44.
- Mechanisms replaced:
  - Prior M1 (keep the enable) is reversed.
  - Prior M2 (project-level declaration) is dropped.
  - Prior M3 (`install.sh` registration check) is dropped and replaced by M2-M4 in `install-dev.sh`.
  - Prior M4 (README) moves to the Tests section.
  - Prior M5's decision file is retitled.
  - M5 (the shared library) is new.

**Alternatives considered (from the approved plan, unchanged):**
- **Do nothing / hold off.** Rejected by the engineer once the stale-reference premise was separated from the real Python-tooling benefit; see Context.
- **A custom LSP plugin wrapping `bash-language-server`.** Rejected: no official-marketplace precedent exists, it adds a plugin to maintain, and grep already covers the shell surface. Explicitly declined by the engineer.
- **Enable the plugin in `claude/.claude/settings.json`.** Rejected: the engineer's ask was "for this repo in particular", and rows 8 and 20 limit it further.

## Critical files

**Dispatch A (behavior-preserving extraction)**

- **`claude/.claude/scripts/_plugin-install-lib.sh`** (create)
  - Header as in `_stow_migration_lib.sh:1-13`: `# shellcheck shell=bash`, no shebang, no top-level `set`.
  - Add one header comment line naming its two callers, `install.sh` and `install-dev.sh`.
  - One function, `_plugin_installed_at_path PLUGIN_ID EXISTING_TSV DIR`. Its body is install.sh:729-740, with `$REPO_DIR` replaced by the third argument. Keep the existing one-line comment about checking the assignment's own exit status against BSD `readlink -f`.
- **`install.sh`**
  - Replace the `project-plugin-match` fixture region (lines 721-741) with `# shellcheck source=claude/.claude/scripts/_plugin-install-lib.sh` and `. "$REPO_DIR/claude/.claude/scripts/_plugin-install-lib.sh"`.
  - Line 757 becomes `_plugin_installed_at_path "$plugin" "$existing_project_plugins" "$REPO_DIR"`.
  - Remove lines 761-763, the hang-risk comment. It moves to `install-dev.sh` in dispatch B under open decision 1(a) (row 39).
- **`claude/.claude/hooks/tests/test_install_sh_project_scope_plugins.py`** (test-only)
  - Drop `_MATCH_START`/`_MATCH_END` and every `_extract_block(_MATCH_START, _MATCH_END)` call site. There are five: `_run_match`, `_run_install_block`, and the inline calls in the three tests that bypass `_run_install_block` for custom setups (`test_malformed_project_settings_json_warns_and_does_not_crash`, `test_malformed_plugin_list_json_warns_and_proceeds_as_if_none_installed`, `test_single_install_failure_does_not_abort_remaining_plugins`). Run `grep -n _MATCH_START` on the file before deleting the constants.
  - `_run_match` sources the library and passes `repo_dir` as the third argument, instead of setting `REPO_DIR`.
  - `_run_install_block` sources the library in place of the extracted match block, and still sets `REPO_DIR` for the install block.
  - Rename the section comment at line 41.
  - The six `TestProjectPluginAlreadyInstalled` cases keep their substance.

**Dispatch B (contributor install), run after A**

- **`.claude/settings.json`** (repo root)
  - Delete line 7 and line 6's trailing comma. The file then matches `main`.
  - The edit trips `ask-review-permissions.sh`'s ask. It does not touch `permissions`, so approve it.
- **`install-dev.sh`**
  - Line 3: add that the script then installs the `pyright-lsp` plugin at local scope when it can.
  - Add **Step 7** after line 119. It runs only on a healthy venv, never exits non-zero, and never touches `check_venv_healthy` (rows 31-32).
    - Set the constant `pyright_lsp_plugin="pyright-lsp@claude-plugins-official"`. Derive the marketplace name as `${pyright_lsp_plugin##*@}`.
    - Source the library relative to the script's own directory, not the cwd, because tests run the real script from a temp cwd: `"$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/claude/.claude/scripts/_plugin-install-lib.sh"`, with a `# shellcheck source=` directive.
    - Comment line: `# Local scope, not .claude/settings.json, so only contributors get the plugin (docs/design-decisions/pyright-lsp-installed-per-contributor.md).`
    - Plugin branches, in order:
      1. **`claude` not on PATH:** print a stdout `  ! ` line saying the `claude` CLI was not found, the plugin is skipped, and to re-run `./install-dev.sh` once the CLI is installed.
      2. **Marketplace list read fails**, including a missing `jq`, via `if ! names="$(claude plugin marketplace list --json 2>/dev/null | jq -r '.[].name')"`: print a stderr warning and skip.
      3. **Marketplace not an exact line of that list:** match with `grep -qFx --` fed by a here-string, following #1262's convention. Print a stdout `  ! ` line, worded like `register-marketplace.sh:135`. It names the marketplace, says it "is not registered for this profile", and says the plugin is skipped. It gives the remedy `claude plugin marketplace add anthropics/claude-plugins-official`, says that registration applies to every project on this profile, and says to re-run `./install-dev.sh`. Put one comment line above: `# Never adds the marketplace: registration is profile-wide (docs/design-decisions/pyright-lsp-installed-per-contributor.md).`
      4. **Plugin list read** (`select(.scope == "local")` into a `"\(.id)\t\(.projectPath)"` TSV, using the field values Verification step 1 recorded): on failure, print a stderr warning and proceed as if nothing is installed. This mirrors install.sh:744-747.
      5. **`_plugin_installed_at_path "$pyright_lsp_plugin" "$tsv" "$(pwd -P)"`:** print `  ✓ <plugin> (already installed)`.
      6. **Otherwise:** print `  → installing <plugin> (local scope)`, then the relocated hang-risk comment, reworded for this call as "A plugin from a GitHub-sourced marketplace can hang this line on a stalled network path, with no timeout guard" and "Ctrl-C and re-run is safe". Then run `if ! claude plugin install "$pyright_lsp_plugin" --scope local; then` print a stderr warning naming the plugin and local scope, and continue.
    - Binary check, always run, independent of the branches above. Put one comment line above it: `# Checks for the binary but never installs it: pipx is not a declared dependency and installs outside .venv.`
      - Found: print `  ✓ pyright-langserver (<path>)`.
      - Not found: print a `  ! ` line saying the plugin needs it on PATH, with "Install with: pipx install pyright" and a pointer to README.md "Tests" for alternatives.
- **`claude/.claude/scripts/tests/test_install_dev.py`** (test-only)
  - Docstring: add item 9 for the plugin step.
  - New helpers:
    - `_make_claude_shim(bin_dir, marketplaces | None, plugins, call_log, install_exit=0)`. It must be a **bash** script (row 32). It handles `plugin marketplace list --json` (non-zero exit when `marketplaces` is None), `plugin list --json`, and `plugin install`, logs every invocation's arguments, and exits 1 on anything unhandled.
    - `_link_real_jq(bin_dir)`: symlink `shutil.which("jq")` into the stub dir.
  - New class `TestPyrightLspStep`, on a healthy-venv baseline (`_make_healthy_venv_stub` plus a python3 stub whose ensurepip check passes), with one case per branch:
    - No `claude`: exit 0, the skip line appears, and the venv summary is still printed.
    - Marketplace read fails: exit 0, a stderr warning, and no `plugin install` call.
    - Unregistered marketplace: exit 0. The skip line names `claude-plugins-official`, the add command, and "every project on this profile". The log holds no `plugin install` and no `marketplace add` (the deny side).
    - Registered and not installed: the log holds exactly `plugin install pyright-lsp@claude-plugins-official --scope local`, and no `project`/`user` scope.
    - Already installed: a `local` entry whose `projectPath` is the stub root prints `✓ … (already installed)` and makes no install call.
    - Deny side of the path match: a `local` entry for another directory still triggers the install.
    - Install exits 1: the script still exits 0, with a stderr warning.
    - `pyright-langserver` stub on PATH: the `✓` line appears.
    - `pyright-langserver` absent: the hint names `pipx install pyright`, and the venv-creation sentinel is untouched, which proves the probe ignores the binary.
    - Plugin-list read fails (registered marketplace; the shim exits 1 on `plugin list --json`): exit 0, a stderr warning, and the install still runs, which proves branch 4's proceed-as-if-none-installed fallback.
    - `jq` absent (no `_link_real_jq`, registered marketplace): exit 0 and branch 2's stderr skip, reached through the real `set -o pipefail` pipe failure rather than the shim's exit code.
  - Existing tests stay unchanged. They pass through branch 1 by construction.
- **`README.md`**
  - Delete lines 103-106, the `pyright-lsp` Requirements bullet and its sub-bullets. Requirements then matches `main`.
  - Line 518: change the comment to `# creates .venv, installs requirements-dev.txt and the pyright-lsp plugin (contributor only)`.
  - Add a paragraph after line 527, the `.venv`-location paragraph. In this order, it says:
    - `./install-dev.sh` also installs `pyright-lsp@claude-plugins-official` at local scope, giving Claude Code sessions in this checkout Python diagnostics and navigation.
    - Local scope records the plugin only in this checkout's gitignored `.claude/settings.local.json`, so it reaches contributors and not every consumer who runs `./install.sh`.
    - The install runs only when `claude-plugins-official` is already registered. Otherwise the script prints the `claude plugin marketplace add anthropics/claude-plugins-official` command, and registration applies to every project on your profile.
    - One sentence stating the worktree outcome that Verification step 1 recorded, for example which launch directory works.
    - A link to the decision file.
    - The binary needs to be on the PATH of the shell that launches Claude Code, and `./install-dev.sh` checks for it but does not install it:
      - Prefer `pipx install pyright`. Without pipx, `python3 -m pip install --user pyright` works only on an interpreter that is not externally managed (PEP 668).
      - Avoid `npm install -g pyright` where the global npm prefix is root-owned (common on stock Linux/WSL2); that fails with `EACCES`.
      - Install outside `.venv`. A `.venv`-scoped binary is not on PATH for the process that launches Claude Code.
      - On WSL2, confirm that `which pyright-langserver` resolves to the Linux-side binary, not a Windows-side one on an inherited PATH.
- **`claude/.claude/scripts/select-tests.py`** [engineer-verified: "Add the row in Dispatch B (Recommended)"]
  - `install-dev.sh` has no row in `DOMAIN_RULES` or `CROSS_DOMAIN_EXCEPTIONS`, so any change to it falls open to the full suite. Add a `CROSS_DOMAIN_EXCEPTIONS` row mirroring the `INSTALL_SH` row's shape (line 754), mapping repo-root `install-dev.sh` to `SCRIPTS_TESTS_DIR`, where `test_install_dev.py` lives. Name the constant `INSTALL_DEV_SH = "install-dev.sh"` beside `INSTALL_SH` (line 319) and add it to `MAPPED_TOP_LEVEL_DIRS`'s sibling list only if that list covers root files; read the table's own header comment (line 673) first.
- **`claude/.claude/scripts/tests/test_select_tests.py`** (test-only)
  - One case mirroring the `INSTALL_SH` case at line 1150: `select_pytest_targets([_mod.INSTALL_DEV_SH])` selects `SCRIPTS_TESTS_DIR` and nothing wider. Add `INSTALL_DEV_SH` to the fidelity list at line 1925 if that list enumerates root-file constants.
- **`docs/design-decisions/pyright-lsp-installed-per-contributor.md`** (create; template `official-marketplace-not-preregistered.md`)
  - Format per `.claude/rules/design-decisions.md`: the H1 "`pyright-lsp` is installed per contributor, at local scope", a blank line, `*2026-10-15.*` on line 3 with no `Formerly` clause, the body, then `## Sources`.
  - Body facts, one sentence each:
    - `install-dev.sh` installs the plugin at local scope, and this repo's committed `.claude/settings.json` does not enable it.
    - A project-scope entry would reach every stow consumer who runs `./install.sh` from a checkout, because `install.sh` installs every plugin that file enables.
    - Local scope records the plugin only in the checkout's gitignored `.claude/settings.local.json`.
    - The script never registers `claude-plugins-official`, because registration applies to the whole profile, and [official-marketplace-not-preregistered.md](official-marketplace-not-preregistered.md)'s migration removes it from provisioned profiles.
    - A contributor who registers it for this plugin takes on, across their whole profile, the session-start clone that the sibling decision describes.
    - The script checks for `pyright-langserver` but never installs it, because the recommended installer, `pipx`, is not a declared dependency and installs outside `.venv`. Otherwise the script installs only `requirements-dev.txt`'s pinned dependencies.
    - The sibling decision stands, because the stowed `claude/.claude/settings.json` still declares nothing from this marketplace.
  - Link README's Tests section for the steps rather than restating them.
  - Sources: the code.claude.com pages fetched this session, the sibling decision, and this plan file.
  - No per-account figure, timing, log count, or account name.

**Reuse opportunities:**
- install.sh:729-740: moved, not rewritten.
- install.sh:744-747: the warn-and-proceed shape.
- `register-marketplace.sh:62-63` and `:134-137`: the registered-list check and the diagnostic wording.
- `_stow_migration_lib.sh:1-13` and install.sh:97-103: the library header and sourcing precedent.
- `test_install_dev.py`'s `_write_stub`, `_run_script`, `_make_healthy_venv_stub`, and the `_stub_healthy_python3` shape.
- `official-marketplace-not-preregistered.md` as the decision-file template.

**Dispatch split:** two sequential `code-writer` dispatches with disjoint file sets.
- **Dispatch A** is a behavior-preserving refactor that needs none of the `pyright-lsp` background. Its check: the test command scoped by `select-tests.py` and shellcheck both pass with no test-assertion change beyond the harness.
- **Dispatch B** consumes A's function signature, which this plan fixes. Its prompt carries the results of Verification step 1: the `.scope`/`.projectPath` values and the worktree outcome.
- **Revert order:** the two commits are not independently revertable. `install-dev.sh` sources A's library, so reverting A alone while B remains makes that `source` fail under `set -e` and aborts the venv path too. Revert B before A, or both together. The PR body states this.
- Splitting B further would restate the scope, marketplace, and binary background in every prompt, and the README, script messages, and decision file must word the remedy identically.

## Verification

1. **Engineer-run, before either dispatch.** This step gates the design. It installs software, so the engineer runs it.
   - a. Confirm that `pyright-langserver` resolves on PATH. If it does not, install it with `pipx install pyright`.
   - b. From the main checkout root, run `claude plugin install pyright-lsp@claude-plugins-official --scope local`. Confirm that `.claude/settings.local.json` gained the entry and that `git status --porcelain` shows no new tracked change (row 26).
   - c. Run `claude plugin list --json | jq '.[] | select(.id == "pyright-lsp@claude-plugins-official")'` and record `.scope` and `.projectPath` (row 29).
   - d. Start a session from the main root, enter a worktree the repo's usual way, and introduce an undefined name in a `.py` under `claude/.claude/hooks/` there. Confirm a diagnostic appears automatically (visible via **Ctrl+O**). Repeat with a session launched directly in a worktree directory (rows 27-28).
   - e. Outcomes:
     - **The diagnostic appears in the usual flow:** dispatch A, then B with the recorded values.
     - **The plugin never activates:** row 27's rule holds, and no remaining scope confines the plugin to contributors without writing a tracked file (M2). Stop and re-plan with the engineer.
     - **It activates only outside worktrees:** most edits here happen in worktrees, so stop and ask the engineer.
2. **After dispatch A:** run `.venv/bin/python3 claude/.claude/scripts/select-tests.py`, or `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py` from a worktree, and do not widen its selection by hand. Then run `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck`.
3. **After dispatch B:**
   - `git diff origin/main -- .claude/settings.json` prints nothing. If `main` has changed that file since the branch point, diff against the merge-base instead.
   - Run the same `select-tests.py` command. With the new `install-dev.sh` row in place, the predicted selection is the hooks, scripts, and skills test directories (hooks via `install.sh`, README, `docs/`, and root `.claude/settings.json`; scripts via `install-dev.sh` and root `.claude/settings.json`; skills via README and `docs/`), which covers `test_install_dev.py`, `test_install_sh_project_scope_plugins.py`, `test_shellcheck.py`, `test_select_tests.py`, and `test_design_decision_files.py` (which checks the new file's slug, H1, and line-3 provenance). Without that row the tool reports `install-dev.sh` as unmatched and falls open to the full suite; that is the tool widening on its own, not a by-hand widening.
   - Run `.venv/bin/ruff check claude/.claude/` (test `.py` files changed).
   - Run `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck` (`install-dev.sh`, `install.sh`, and the new library changed).
4. **Manual, engineer-run, after pulling the branch:**
   - Run `./install-dev.sh` from the main root. Expect `✓ pyright-lsp@claude-plugins-official (already installed)` (from step 1b) and `✓ pyright-langserver (…)`. Then `git status --porcelain` shows no tracked change. This settles M5's local-scope path match against a real install.
   - Ask Claude to find all references to an existing hook helper. Confirm that it uses LSP find-references rather than a grep dispatch.
   - Optional and non-destructive: re-run `./install-dev.sh` with `CLAUDE_CONFIG_DIR` pointed at a freshly created empty temp directory. Expect branch 3's skip line and no `marketplace add`. That is the state the check guards (rows 9-10).

## Out of scope

- **Fixing stale `file:line` references in already-written plan/PR prose.** That is a documentation-practice question LSP tooling does not reach. It is flagged for the engineer to decide separately if it is still a concern.
- **A shell/bash LSP.** No official plugin exists, and the engineer declined it.
- **Enabling `pyright-lsp`, or declaring `claude-plugins-official`, in either committed settings file.** Rows 8, 20, and 40.
- **Registering the marketplace from `install-dev.sh`, or running `pipx install pyright` from it.** See settled points (a) and (b).
- **The staff-platform-engineer finding that `install.sh`'s project-scope loop lacks a marketplace check.** After M1, the loop installs only `@claude-config` plugins, and `claude-config` is registered by `register-marketplace.sh`, which install.sh:715 runs just before the loop (row 34). The failure that finding describes no longer occurs. Reply to the finding with that reason.
- **`autoUpdate` and #1033's Revisit trigger.** Row 37's schema evidence suggests that official-marketplace-not-preregistered.md's Revisit trigger ("a per-marketplace refresh or cache control") may now be met. Whether 2.1.283 honors the key at runtime is unknown. Raise it to the reviewer as a separate issue.
- **`claude/.claude/rules/settings-json-conventions.md:10-11`.** If Verification step 1 shows a local-scope enable activating, that line is wrong. It is stowed to every consumer, so raise its correction to the reviewer as a separate change rather than widening this diff.
- **CLAUDE.md:11 and install.sh:956/972's `install-dev.sh` descriptions.** They still accurately describe the script's venv role. README's Tests section is the canonical home for the plugin step.
- **`_INSTALL_START`/`_INSTALL_END` source extraction in `test_install_sh_project_scope_plugins.py`.** `_run_install_block` still regex-extracts and runs the install loop's source text, the pattern `.claude/rules/shell-script-conventions.md` forbids. Dispatch A removes only the match-function half. Raise it to the reviewer as a separate change rather than widening Dispatch A.
- **Moving `TestProjectPluginAlreadyInstalled` into a library-named test file under `claude/.claude/scripts/tests/`.** It stays where it is, to keep the extraction diff minimal.
- **An opt-out flag for Step 7.** Not requested. Row 20 asks for contributors to get the plugin.
- **Editing `docs/design-decisions/official-marketplace-not-preregistered.md` or its CHANGELOG entry.** Both are dated records, and their decision stands.
- **A CHANGELOG entry.** Stow consumers see no runtime change: the new library is a behavior-preserving extraction, and `install-dev.sh` is contributor-only and has never had an entry (row 38).
- **`register-marketplace.sh`'s own `--scope user` adds** (`register-marketplace.sh:93`, `:121`). Per row 15, they may declare into the stow-tracked settings file. The issue predates this branch, so raise it to the reviewer separately.
- **A timeout wrapper around `claude plugin install`.** The engineer kept the comment-only mitigation (row 22), and no grounded timeout value exists.
- **Running any install.** `./install-dev.sh`, `claude plugin install`, `claude plugin marketplace add`, and `pipx install pyright` are software installs or registry changes. The engineer runs them personally (CLAUDE.md Safety, and Verification steps 1 and 4).
- **Pinning `pyright` in `requirements-dev.txt`** (row 6).
