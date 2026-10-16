# Evaluate installing a Python LSP for this repo

## Context

Enable `pyright-lsp` (Python code-intelligence) for claude-config itself, scoped to this repo only — not to fix stale `file:line` references (LSP doesn't touch that), but for its own real benefit: automatic post-edit diagnostics and precise navigation across the repo's 149 Python files (~110K lines, mostly hooks and their tests), which today get no diagnostics feedback beyond a manually-run `ruff check` / `pytest`.

Ask: enable pyright-lsp for this repo only (original request, prior session, paraphrased from Context); narrowed this session to contributors: "Pyright-lsp should only be installed for contributors to the repo so it should actually be part of install-dev.sh" — paraphrase accepted via label "Accept the Ask line".

The engineer's original framing was that installing a Python and shell LSP would make `file_path:line_number` references (as seen throughout the merged discovery-audit-remediation-plan, the PR referred to as "PR 742," which embeds 162 such references) resilient to drift during a large refactor. That specific premise doesn't hold — verified below — so this plan proceeds on the narrower, real benefit instead, per the engineer's explicit go-ahead after seeing that finding.

## Approach

Install `pyright-lsp` from `install-dev.sh`, the contributor-only setup script, at local scope. The install runs only after a contributor at an interactive terminal answers `y` to a prompt. A non-interactive run prints a `cd <checkout> && claude plugin install … --scope local` command and skips the install. That covers any agent's run through the Bash tool. This repo's committed `.claude/settings.json` does not enable the plugin, so `./install.sh` never installs it for stow consumers. `install-dev.sh` reaches the prompt only when the `claude` CLI is present, `claude-plugins-official` is already registered, and the plugin is not yet installed in this checkout. It never registers the marketplace, and it checks for the `pyright-langserver` binary but never installs it. `install.sh` and `install-dev.sh` share one sourced library for the already-installed match. The prompt's answer handling lives in that library too. As before, the plan skips a shell LSP and the stow-package scope.

**This revision.** Dispatches A (9df56eea) and B (bd51441e) have landed. Round 7's ciso-reviewer found that B's Step 7 runs `claude plugin install` with no confirmation. An agent that runs the documented `./install-dev.sh` therefore installs software no one named. The engineer chose the orchestrator's option 1, a prompt in an interactive terminal only (rows 45-46). Dispatch C carries that gate and six round-7 review fixes (row 57).

**Earlier revision.** It replaced the first draft's design: a project-scope enable, a project-level marketplace declaration with `autoUpdate: false`, and a registration check in `install.sh`. The engineer's answer to that draft's open decision 1 moved the plugin to the contributor path (row 20). A declaration in a tracked settings file reaches everyone who opens this checkout, contributor or not.

**Open decisions, now answered.** No decision remains open. Each one below went to the engineer, and the plan is written to the answer.

1. **Where the hang-risk comment lives.** It moved to `install-dev.sh`'s `claude plugin install` call (row 39). The alternative kept it in `install.sh` without the `(e.g. pyright-lsp@claude-plugins-official)` example.
2. **Whether row 20 retires the project-level marketplace declaration.** It was dropped (row 40). The alternative kept it, which would bring back the trust prompt for non-contributors and prior rows 11-14.
3. **How Step 7 authorizes its install.** Round 7's ciso-reviewer finding raised it. The engineer chose option 1 (rows 45-46). The text of the other two options was not relayed to this revision.

**Settled design points** (evidence settles these, so they are not escalated):
- **(a) Check for the marketplace and print the command; never add it.** A `--scope local` add still registers the marketplace across the whole profile (G3). It would silently undo a deliberate removal under #1033's Migration line (row 16). Profiles that never removed it already have it, because Claude Code adds it on the first interactive session (row 9). Flipping this later costs one add line and one test.
- **(b) Print the `pyright-langserver` install command; never run it.** `pipx` is not a dependency this repo declares (row 33). It installs outside `.venv`, and the `pyright` package is deliberately unpinned (row 6). The script otherwise installs only pinned dependencies, and the network-install hook cannot see commands the script runs (row 30). If the script ran the install, an agent that runs `./install-dev.sh` as a dependency restore would perform a new-package install that no gate reviews. The engineer's row 21 selects the order of the documented commands, not who runs them.
- **(c) A missing `claude` CLI skips the step; it does not fail the script.** The script's purpose is the venv. Its own test harness runs with no `claude` on PATH (row 32). `install.sh` already enforces the CLI for stow setup (README.md:96).
- **(d) The new step reports its own status lines, and `pyright-lsp` never enters `check_venv_healthy`.** A failed probe deletes and recreates `.venv` (row 31). If the probe included `pyright-lsp`, every run without the plugin would delete and rebuild the venv.
- **(e) The hang-risk comment:** answered decision 1 (row 39).
- **(f) Step 7 installs the plugin only on a confirmed answer at a terminal.** Settled point (b)'s reasoning applies to the plugin as well. The plugin is not a declared dependency either, and an agent that runs `./install-dev.sh` as setup must not fetch it as a side effect. The engineer chose this gate (row 45), and M10 gives the mechanism.
- **The repo's remaining declaration of intent:** the plugin-id constant in `install-dev.sh`, the README Tests paragraph, and the decision file.

**Root problem:** Claude Code sessions in this repo get no automatic Python diagnostics or precise navigation. That is the engineer's original "faster/cheaper" hypothesis, narrowed per Context. The plugin must reach contributors only (row 20). As committed after Dispatch B, Step 7 installs it with no confirmation, so an agent running the documented `./install-dev.sh` installs software no one named (round 7's ciso-reviewer finding).

**Givens:**
- **G1.** Claude Code's LSP integration is opt-in, through official-marketplace "Code Intelligence" plugins that each wrap a separately installed language-server binary. That is how the vendor ships LSP support. [verified: code.claude.com/docs/en/discover-plugins]
- **G2.** The official plugin table has no shell/bash entry. It is Anthropic's catalog, outside this plan's reach. [verified: code.claude.com/docs/en/discover-plugins]
- **G3.** Marketplace registration is per user, so no repo file and no `--scope` can confine a registration to one project. Vendor-imposed. [verified: plugins/loading, "There is one known_marketplaces.json per user, so a marketplace you add in one project is available in every project."]
- **G4.** *Retired in an earlier revision.* It described the trust-gated prompt for a project-declared marketplace, and no declaration remains.
- **G5.** Each contributor's `claude` CLI version is their own. The planning machine runs 2.1.283, which has no `claude plugin install --marketplace` (documented as requiring v2.1.292 or later), so the design cannot assume the one-step form. [verified: `claude --version`, `claude plugin install --help`, plugins/install]

**Mechanisms:**

- **M1: remove `"pyright-lsp@claude-plugins-official": true` from the root `.claude/settings.json`.** This reverses the first draft's M1. A project-scope entry makes `install.sh` install the plugin for every stow consumer (row 2). It also makes Claude Code report the plugin as not installed to anyone who has not installed it (row 18). `anchors: root, row2, row18, row20`
- **M2: `install-dev.sh` Step 7 offers `claude plugin install pyright-lsp@claude-plugins-official --scope local`, and runs it only on a confirmed answer at a terminal (M10).**
  - Lighter alternative (a), rejected: a README-only instruction. It fails row 20's "should actually be part of install-dev.sh".
  - Lighter alternative (b), printing the install command only, is now Step 7's non-interactive arm (M10). As the only behavior, it fails row 20 for a contributor at a terminal, and the engineer chose option 1 (row 45).
  - Same-weight alternative (c), rejected: project scope. It fails row 20 (M1).
  - Wider alternative (d), rejected: user scope. It enables the plugin in every project on the profile. It may also write the stow-tracked settings file the way row 15's marketplace add does; that is `[unverified]` for plugin install.
  - Local scope writes only the gitignored `.claude/settings.local.json` (row 26). Whether that activates the plugin, including in worktree sessions, is rows 27-28. Verification step 1 gated Dispatch B on that question.

  `anchors: root, row20, row24, row26, row27, row28, row45`
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
- **M6: Step 7 is non-fatal and separate from the health probe.** See settled points (c) and (d). A decline or a non-interactive skip exits 0, like every other Step 7 skip. `anchors: row31, row32, row45`
- **M7: README's Tests section becomes the single home for the contributor plugin steps.** The Requirements bullets an earlier commit on this branch added (README.md:103-106) are removed, with no pointer left behind. `install-dev.sh` reports a missing binary itself, and Tests is where contributors already go for `install-dev.sh`. `anchors: row19, row21, row23`
- **M8: a new decision file, `docs/design-decisions/pyright-lsp-installed-per-contributor.md`.**
  - What still needs a durable home: this repo deliberately does not enable the plugin in its committed settings. A future contributor would most likely "fix" that in the settings file, which can carry no comment.
  - It also records how this change relates to #1033's decision: an opted-in contributor's profile takes on the marketplace again.
  - It records that a non-interactive run never installs the plugin. It cites `claude/.claude/CLAUDE.md` § "Safety" for why, without restating the rule. It also states that the terminal check is not a security boundary (row 49), and it makes no claim about a direct `claude plugin install` (row 50).
  - `install-dev.sh` carries one-line comments pointing to the file.
  - Rejected: comments and README only. Neither reaches the reader who edits `.claude/settings.json`.

  `anchors: row16, row18, row20, row45, row49, row50`
- **M9: the hang-risk comment moves to `install-dev.sh`.** Answered decision 1. `anchors: row22, row34, row39`
- **M10: a terminal gate on Step 7's install arm only.** The arm reached after `_plugin_installed_at_path` returns false becomes three arms:
  - With no terminal on stdin, it prints a `cd <checkout> && claude plugin install … --scope local` command and skips.
  - With a terminal, it asks through `_confirm_plugin_install` (M11) and installs only on a `y` answer.
  - Any other answer prints a decline line.

  Design notes:
  - Placement: only that arm. A non-interactive run still reports the already-installed, unregistered-marketplace, and binary states, because those branches only read.
  - The printed command names the directory. A local-scope install records a project path (row 29), and Step 7 matches that path against `pwd -P` (row 54).
  - Alternative (a), rejected: an opt-in flag or environment variable. An agent types either one as command text, so the gate fails open. `update-claude-config-plugins.sh:277`'s `ASSUME_YES` bypass has that shape.
  - Alternative (b), rejected: a prompt with no terminal check. A bare `read` blocks forever on an open, never-closed stdin (row 53).
  - Alternative (c), rejected: a test-only override of the terminal check, shaped like `_INSTALL_DEV_IS_DEBIAN`. An agent can type that too. The tests attach a real pty instead (M12).
  - Alternative (d), rejected here: a hook on `claude plugin install`. It is heavier, and a hook cannot see what a script runs internally (row 30). See Out of scope.
  - The gate is not a security boundary (row 49), and it covers only what `./install-dev.sh` runs (row 50). The docs claim no more than that.

  `anchors: root, row45, row46, row47, row48, row49, row50, row53, row54`
- **M11: `_confirm_plugin_install PLUGIN_ID SCOPE` joins `_plugin-install-lib.sh`.** It prompts on stderr. It returns 0 only for an answer starting with `y` or `Y`, and it treats EOF as a decline. It has no terminal check of its own. The caller checks, which matches the contract at install.sh:438-440 and :938-940.
  - Placement follows `.claude/rules/bash-unit-test-seams.md` option 1, because the library's domain is plugin install.
  - Lighter alternative (a), rejected: an inline `read` in `install-dev.sh`. The answer matrix would then need subprocess tests. `.claude/rules/shell-script-conventions.md` forbids that: "Keep the subprocess test to one case per branch; move the input matrix to the direct tests instead."
  - Alternative (b), rejected: a new `_confirm-lib.sh`. Option 1 of the seams rule prefers the existing sibling library.
  - Not adopted: one y/n helper for every prompt in the repo. Five `read -r -p` prompts already inline the same shape (row 48), and consolidating them is a separate change.

  `anchors: row48, row53`
- **M12: test seams for the gate.**
  - `_run_script` gives the script `/dev/null` as stdin (row 52).
  - A new pty runner covers the interactive arms. It is shaped like test_migrate_legacy_config.py:464-496, which already runs a real pty in this suite.
  - Direct tests source the library to cover the answer matrix.
  - Rejected: an environment-variable override of the terminal check (M10 (c)).
  - The pty `y` case asserts the prompt text on stderr, which settles row 51.

  `anchors: row51, row52`

**Assumptions:**

| # | Claim | Tag |
|---|---|---|
| 1 | LSP diagnostics and navigation apply only to code the agent is actively editing or querying in a live session. Nothing in Claude Code's LSP integration re-validates or updates `file:line` citations already embedded in previously written, merged prose (e.g., a plan doc). | [verified: code.claude.com/docs/en/discover-plugins, code.claude.com/docs/en/common-workflows; neither documents any such re-validation behavior] |
| 2 | After this change, only `install-dev.sh` Step 7 installs `pyright-lsp`, and only after a `y` answer at a terminal (M10). `install.sh`'s project-scope loop installs what the root `.claude/settings.json` enables, and that no longer includes `pyright-lsp`. *Rewritten twice: the first draft had `install.sh` install it, and the prior revision had Step 7 install it with no prompt.* | [verified: install.sh:729-745] |
| 3 | `pyright-langserver` installs via `pip install pyright` or `npm install -g pyright`. The plugin's own README also recommends `pipx install pyright` (row 25). | [verified: code.claude.com/docs/en/plugins-reference] |
| 4 | This repo has no `PostToolUse` hook that runs `ruff`/`pytest` automatically after a Python edit. `ruff check` and `pytest` run only as manual, allowlisted Bash commands. | [verified: claude/.claude/settings.json `permissions.allow` list; its `PostToolUse` block contains no ruff/pytest entry] |
| 5 | Repo composition: 149 tracked `.py` files (~109,840 lines), 76 tracked `.sh` files (~17,263 lines), 347 tracked `.md` files (~80,871 lines). | [verified: `git ls-files` + `wc -l` in the original planning session] |
| 6 | `pyright` stays unpinned and stays out of `requirements-dev.txt`. It never runs in CI or any blocking check. | [verified: requirements-dev.txt; README.md:96 (unpinned `stow`/`git`/`gh`/`jq` precedent)] |
| 7 | The marketplace is declared at project level, not in the stowed settings. *An earlier revision retired the declaration; see row 36, answered decision 2, and row 40.* | [engineer-verified: "Project-level marketplace (Recommended)"] |
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
| 22 | Keep the `install.sh` hang-risk comment. The question assumed `install.sh` installs `pyright-lsp`; see answered decision 1 and row 39. | [engineer-verified: "Keep it (Recommended)"] |
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
| 36 | Row 20 retires the project-level declaration that rows 7-8 selected. This is the plan's reading, not the engineer's words. *Confirmed by row 40.* | [unverified]. Answered decision 2 |
| 37 | SchemaStore defines `autoUpdate` on an `extraKnownMarketplaces` entry: "Whether to automatically update this marketplace on Claude Code startup." Whether CLI 2.1.283 honors it at runtime is unknown. | [verified: schemastore.org/claude-code-settings.json, extraKnownMarketplaces entry schema]; [unverified]: runtime |
| 38 | No CHANGELOG entry has ever covered an `install-dev.sh` change. | [verified: CHANGELOG.md has zero `install-dev` matches] |
| 39 | Open decision 1: the hang-risk comment moves to `install-dev.sh`'s `claude plugin install` call, and `install.sh` loses the three lines. | [engineer-verified: "Move to install-dev.sh (Recommended)"] |
| 40 | Open decision 2: the project-level `claude-plugins-official` declaration is dropped; no committed settings file declares the marketplace. | [engineer-verified: "Drop it (Recommended)"] |
| 41 | The Context `Ask:` line, added at plan-review, paraphrases the prior-session request and quotes this session's narrowing. | [engineer-verified: "Accept the Ask line"] |
| 42 | M5, the shared plugin-match library extraction (Dispatch A), is a deliverable the ask does not call for; the engineer kept it. | [engineer-verified: "Shared plugin-match library"] |
| 43 | M8, the new design-decision file, is a deliverable the ask does not call for; the engineer kept it. | [engineer-verified: "Design-decision doc"] |
| 44 | `select-tests.py` gains an `install-dev.sh` row in Dispatch B, with a `test_select_tests.py` case. Added at plan-review on a staff-sdet finding. | [engineer-verified: "Add the row in Dispatch B (Recommended)"] |
| 45 | The engineer selected option 1 of the three options the orchestrator offered for gating Step 7's install. | [engineer-verified: "yes let's go with option 1"] |
| 46 | Option 1, in the orchestrator's words: "Prompt only in an interactive terminal. If stdin is a terminal, ask 'Install pyright-lsp at local scope? [y/N]'. Otherwise, print the install command and skip." The engineer did not write this text, and row 45 covers only the selection. The shipped prompt names the full id `pyright-lsp@claude-plugins-official`, and the printed command adds a `cd` to the checkout (M10). | [unverified] |
| 47 | Claude Code's Bash tool gives commands a stdin that is not a terminal. An agent that runs `./install-dev.sh` through it therefore reaches M10's non-interactive arm. | [verified: the dispatching session's Bash-tool probe `test -t 0 && echo stdin-is-a-tty \|\| echo stdin-not-a-tty` printed `stdin-not-a-tty`] |
| 48 | `[ -t 0 ]` is the repo's gate for a prompt meant for a human. It appears at six gate sites in four scripts: install.sh:167, :518, :958; cleanup-merged-branches.sh:842; migrate-legacy-config.sh:381; and update-claude-config-plugins.sh:277, the one site that also accepts an `ASSUME_YES` bypass. Five of the six `read -r -p` sites are y/n prompts written as `read -r -p "… " answer \|\| answer=""` (install.sh:141, :457, :495, :943; migrate-legacy-config.sh:142). The exception, scripts/dev/fork-topology-probe.sh:83, is a press-Enter pause. | [verified: grep of `-t 0` and `read -r -p` across tracked `*.sh`, this revision] |
| 49 | `[ -t 0 ]` is not a security boundary, because a self-allocated pseudo-terminal satisfies it. The repo accepts and discloses that residual for install.sh's prompts. | [verified: docs/design-decisions/sentinel-config-consolidation.md:29, :32] |
| 50 | No hook inspects a `claude plugin install` typed into the Bash tool. M10 stops `./install-dev.sh` from installing the plugin as a side effect, and does nothing more. | [verified: no `plugin install` or `claude plugin` match in `claude/.claude/hooks/` outside `tests/`, this revision] |
| 51 | `read -p` displays its prompt on stderr only when input comes from a terminal. The piped direct tests therefore cannot see the prompt text, but the pty tests can. | [unverified]. The pty `y` case settles it (M12) |
| 52 | A `subprocess.run` with no `stdin` argument inherits the test runner's stdin, which is a terminal when `pytest -n0` runs from one. Without an explicit `/dev/null`, Step 7 would prompt and wait in that run. | [verified: test_cleanup_merged_branches.py:280-301 defaults `stdin=subprocess.DEVNULL` because its callers rely on non-TTY behavior] |
| 53 | A `read` with no terminal check before it blocks forever on an open, never-closed stdin. | [verified: install.sh:438-440, :938-940] |
| 54 | Whether a local-scope install binds to the cwd or to the git root is unknown. Step 7 runs only from the main checkout root (row 24), where the two coincide, and the printed command `cd`s to `pwd -P` either way. | [unverified]: the binding. Not load-bearing |
| 55 | Whether Claude Code's `!` bash mode gives a command a terminal stdin is unknown. Without one, an engineer's `! ./install-dev.sh` prints the command and skips. With a terminal the session cannot type into, the prompt waits until Ctrl-C. | [unverified]. Verification step 6a settles it |
| 56 | `install.sh`'s project-scope loop also runs `claude plugin install` with no prompt (install.sh:741-743). That is not the defect M10 fixes. Every plugin the loop installs is enabled in the committed root `.claude/settings.json`, so the loop restores a declared dependency (row 34). | [verified: install.sh:729-745; row 34] |
| 57 | Dispatch C also carries six round-7 fixes that the orchestrator marked ADDRESS: five comment-discipline-reviewer findings and one staff-sdet finding. These are the orchestrator's dispositions, not the engineer's words. Round 7's ciso-reviewer finding that no test pins the committed `.claude/settings.json` was marked DEFER (Out of scope). | [verified: the round-7 comment-discipline-reviewer and staff-sdet findings files] |

**Changes in this revision:**
- Rewritten: row 2.
- Added: rows 45-57, settled point (f), and mechanisms M10-M12.
- Revised: M2 (alternative (b) is now the non-interactive arm), M6, and M8.
- The open-decision prose is condensed into "Open decisions, now answered", with a third entry.
- Critical files: A and B are marked implemented, and Dispatch C is new. Verification gains steps 5-6. Out of scope replaces the opt-out-flag bullet and adds five bullets.
- Not re-tagged: rows 27-29. Verification step 1's recorded values were not relayed to this revision. README.md:525's worktree sentence states the worktree outcome.

**Changes in the earlier revision:**
- Retired: G4, rows 11-14, and the second half of row 18.
- Rewritten: row 2, with the new install path.
- Extended: row 3 (pipx).
- Annotated, not rewritten: rows 7 and 22.
- Added: rows 20-44.
- Mechanisms replaced: the first draft's M1 (keep the enable) was reversed. Its M2 (project-level declaration) was dropped. Its M3 (`install.sh` registration check) was dropped and replaced by M2-M4 in `install-dev.sh`. Its M4 (README) moved to the Tests section, and its M5 decision file was retitled. M5 (the shared library) was new.

**Alternatives considered (from the approved plan, unchanged):**
- **Do nothing / hold off.** Rejected by the engineer once the stale-reference premise was separated from the real Python-tooling benefit; see Context.
- **A custom LSP plugin wrapping `bash-language-server`.** Rejected: no official-marketplace precedent exists, it adds a plugin to maintain, and grep already covers the shell surface. Explicitly declined by the engineer.
- **Enable the plugin in `claude/.claude/settings.json`.** Rejected: the engineer's ask was "for this repo in particular", and rows 8 and 20 limit it further.

## Critical files

**Dispatch A (behavior-preserving extraction): implemented in 9df56eea. Kept here as the record.**

- **`claude/.claude/scripts/_plugin-install-lib.sh`** (create)
  - Header as in `_stow_migration_lib.sh:1-13`: `# shellcheck shell=bash`, no shebang, no top-level `set`.
  - Add one header comment line naming its two callers, `install.sh` and `install-dev.sh`.
  - One function, `_plugin_installed_at_path PLUGIN_ID EXISTING_TSV DIR`. Its body is install.sh:729-740, with `$REPO_DIR` replaced by the third argument. Keep the existing one-line comment about checking the assignment's own exit status against BSD `readlink -f`.
- **`install.sh`**
  - Replace the `project-plugin-match` fixture region (lines 721-741) with `# shellcheck source=claude/.claude/scripts/_plugin-install-lib.sh` and `. "$REPO_DIR/claude/.claude/scripts/_plugin-install-lib.sh"`.
  - Line 757 becomes `_plugin_installed_at_path "$plugin" "$existing_project_plugins" "$REPO_DIR"`.
  - Remove lines 761-763, the hang-risk comment. It moves to `install-dev.sh` in dispatch B under answered decision 1 (row 39).
- **`claude/.claude/hooks/tests/test_install_sh_project_scope_plugins.py`** (test-only)
  - Drop `_MATCH_START`/`_MATCH_END` and every `_extract_block(_MATCH_START, _MATCH_END)` call site. There are five: `_run_match`, `_run_install_block`, and the inline calls in the three tests that bypass `_run_install_block` for custom setups (`test_malformed_project_settings_json_warns_and_does_not_crash`, `test_malformed_plugin_list_json_warns_and_proceeds_as_if_none_installed`, `test_single_install_failure_does_not_abort_remaining_plugins`). Run `grep -n _MATCH_START` on the file before deleting the constants.
  - `_run_match` sources the library and passes `repo_dir` as the third argument, instead of setting `REPO_DIR`.
  - `_run_install_block` sources the library in place of the extracted match block, and still sets `REPO_DIR` for the install block.
  - Rename the section comment at line 41.
  - The six `TestProjectPluginAlreadyInstalled` cases keep their substance.

**Dispatch B (contributor install): implemented in bd51441e. Kept here as the record. Dispatch C supersedes the B items it names.**

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
      6. **Otherwise:** print `  → installing <plugin> (local scope)`, then the relocated hang-risk comment, reworded for this call as "A plugin from a GitHub-sourced marketplace can hang this line on a stalled network path, with no timeout guard" and "Ctrl-C and re-run is safe". Then run `if ! claude plugin install "$pyright_lsp_plugin" --scope local; then` print a stderr warning naming the plugin and local scope, and continue. *Superseded by Dispatch C (M10).*
    - Binary check, always run, independent of the branches above. Put one comment line above it: `# Checks for the binary but never installs it: pipx is not a declared dependency and installs outside .venv.` *Comment superseded by Dispatch C.*
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
    - Registered and not installed: the log holds exactly `plugin install pyright-lsp@claude-plugins-official --scope local`, and no `project`/`user` scope. *Superseded by Dispatch C.*
    - Already installed: a `local` entry whose `projectPath` is the stub root prints `✓ … (already installed)` and makes no install call.
    - Deny side of the path match: a `local` entry for another directory still triggers the install. *Retargeted by Dispatch C.*
    - Install exits 1: the script still exits 0, with a stderr warning. *Superseded by Dispatch C.*
    - `pyright-langserver` stub on PATH: the `✓` line appears.
    - `pyright-langserver` absent: the hint names `pipx install pyright`, and the venv-creation sentinel is untouched, which proves the probe ignores the binary.
    - Plugin-list read fails (registered marketplace; the shim exits 1 on `plugin list --json`): exit 0, a stderr warning, and the install still runs, which proves branch 4's proceed-as-if-none-installed fallback. *Retargeted by Dispatch C.*
    - `jq` absent (no `_link_real_jq`, registered marketplace): exit 0 and branch 2's stderr skip, reached through the real `set -o pipefail` pipe failure rather than the shim's exit code.
  - Existing tests stay unchanged. They pass through branch 1 by construction.
- **`README.md`**
  - Delete lines 103-106, the `pyright-lsp` Requirements bullet and its sub-bullets. Requirements then matches `main`.
  - Line 518: change the comment to `# creates .venv, installs requirements-dev.txt and the pyright-lsp plugin (contributor only)`. *Superseded by Dispatch C.*
  - Add a paragraph after line 527, the `.venv`-location paragraph. *Its first three sentences and its link sentence are superseded by Dispatch C.* In this order, it says:
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
- **`docs/design-decisions/pyright-lsp-installed-per-contributor.md`** (create; template `official-marketplace-not-preregistered.md`) *Its body is revised by Dispatch C.*
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

**Dispatch C (interactive-only plugin install, plus round-7 fixes), run after B.** One `code-writer` dispatch against the committed code. It touches exactly these five files.

- **`claude/.claude/scripts/_plugin-install-lib.sh`** (M11)
  - Append `_confirm_plugin_install` after `_plugin_installed_at_path`. Leave the header line "Sourced by install.sh and install-dev.sh." as it is, since it stays accurate.
  - Comment lines above the function, one sentence each. Wrap at the file's existing width.
    - `# Asks whether to install $1 at $2 scope, reading the answer from stdin.`
    - `# Caller must check [ -t 0 ] first: with no TTY guard of its own, read blocks forever on an open, never-closed stdin.`
    - `# Returns 0 only for an answer starting with y or Y.`
    - `# Enter, any other answer, and EOF all return 1.`
  - Body:
    ```bash
    _confirm_plugin_install() {
      local plugin_id="$1" scope="$2" answer
      read -r -p "  Install $plugin_id at $scope scope? [y/N] " answer || answer=""
      case "$answer" in
        [Yy]*) return 0 ;;
        *) return 1 ;;
      esac
    }
    ```
- **`install-dev.sh`** (M10, plus ADDRESS fixes 2 and 5)
  - Lines 3-4 become `# Creates .venv and installs requirements-dev.txt (pytest, pytest-xdist, ruff, pyyaml, shellcheck-py),` and `# then offers to install the pyright-lsp plugin at local scope from an interactive terminal.`
  - Lines 122-123 (ADDRESS 2) become `# Step 7 — Offer the pyright-lsp plugin at local scope (docs/design-decisions/pyright-lsp-installed-per-contributor.md).`
  - Lines 124-125 have the same multi-fact shape, so split them too: `# Every plugin check below is non-fatal, because a skipped plugin step must not fail the venv setup.` and `# None of them touches check_venv_healthy.`
  - Line 133 becomes `echo "=== pyright-lsp plugin (local scope) ==="`.
  - Lines 148-158 become one chain. Keep the three hang-risk comment lines and the warning line byte-for-byte:
    ```bash
      if _plugin_installed_at_path "$pyright_lsp_plugin" "$pyright_lsp_installed_tsv" "$(pwd -P)"; then
        echo "  ✓ $pyright_lsp_plugin (already installed)"
      # Only an interactive run may install the plugin, because it is not a declared dependency (docs/design-decisions/pyright-lsp-installed-per-contributor.md).
      elif [ ! -t 0 ]; then
        echo "  ! not an interactive terminal — skipping $pyright_lsp_plugin."
        printf '    Install it with: cd %q && claude plugin install %s --scope local\n' "$(pwd -P)" "$pyright_lsp_plugin"
        echo "    Or re-run ./install-dev.sh from a terminal to be prompted."
      elif _confirm_plugin_install "$pyright_lsp_plugin" local; then
        echo "  → installing $pyright_lsp_plugin (local scope)"
        # (existing three hang-risk comment lines)
        if ! claude plugin install "$pyright_lsp_plugin" --scope local; then
          echo "[install-dev] warning: failed to install $pyright_lsp_plugin at local scope" >&2
        fi
      else
        echo "  ✓ leaving $pyright_lsp_plugin uninstalled"
      fi
    ```
  - Line 161 (ADDRESS 5) becomes two lines: `# Checks for the binary but never installs it, because pipx is not a declared dependency.` and `# pipx also installs outside .venv.`
  - Nothing else changes: Steps 1-6, branches 1-4, and the binary check's code stay as they are.
- **`claude/.claude/scripts/tests/test_install_dev.py`** (M12, plus ADDRESS fixes 4 and 6)
  - Imports: add `os`, `pty`, and `shlex`. Add `_PLUGIN_INSTALL_LIB = REPO_ROOT / "claude" / ".claude" / "scripts" / "_plugin-install-lib.sh"`.
  - Module docstring (ADDRESS 4):
    - Item 9 becomes "Step 7, the pyright-lsp local-scope install, one case per branch:", followed by a nested list with one bullet per branch:
      - no `claude` CLI
      - a marketplace-list read failure
      - `jq` genuinely absent from PATH
      - an unregistered marketplace
      - already installed
      - a plugin-list read failure, which still reaches the install arm
      - a local entry for another directory, which still reaches the install arm
      - a project-scope entry for this directory, which still reaches the install arm
      - not installed, with no terminal: the script prints the install command and skips
      - not installed, at a pty, answered `y`: the script installs at local scope
      - not installed, at a pty, answered Enter: the plugin stays uninstalled
      - not installed, at a pty, sent EOF (Ctrl-D): the plugin stays uninstalled
      - a confirmed install that fails: the script still exits 0
      - the `pyright-langserver` binary present or absent
    - Add item 10: "`_confirm_plugin_install`, sourced directly. An answer starting with y or Y confirms. Any other answer, Enter, and EOF decline."
    - Append two sentences to the closing paragraph: "`_run_script` gives the script /dev/null as stdin, so Step 7's `[ -t 0 ]` check is false." and "The interactive cases attach a real pseudo-terminal through `_run_script_with_pty_stdin`."
  - Extract `_script_env(fake_home, stub_bin=None, extra_env=None, path_tail=None) -> dict` from `_run_script`'s PATH/HOME construction. Move the `path_tail` docstring paragraph to it. `_run_script` calls the helper and always passes `stdin=subprocess.DEVNULL`, following test_cleanup_merged_branches.py:280-301.
  - Add `_PTY_RUN_TIMEOUT_SECONDS = 10` with the comment `# Bounds a run whose prompt never gets a reply; matches test_migrate_legacy_config.py's pty runner.`
  - Add `_run_script_with_pty_stdin(cwd, fake_home, stub_bin, reply) -> subprocess.CompletedProcess`, shaped like test_migrate_legacy_config.py:464-496:
    - `pty.openpty()`, then `Popen([str(SCRIPT)], cwd=str(cwd), env=_script_env(fake_home, stub_bin), stdin=slave_fd, stdout=PIPE, stderr=PIPE, text=True)`.
    - Close the slave, `os.write(master_fd, reply.encode())`, then `communicate(timeout=_PTY_RUN_TIMEOUT_SECONDS)`.
    - On `subprocess.TimeoutExpired`, call `proc.kill()` and `proc.communicate()`, then re-raise.
    - In `finally`, close the master, and the slave too if it is still open.
    - Docstring, one fact per sentence. It must say that every caller passes a reply, because otherwise the prompt waits until the timeout.
  - `TestPyrightLspStep` class docstring (ADDRESS 4), one fact per sentence. Step 7 runs only after a healthy .venv is confirmed. It is never fatal. It never touches check_venv_healthy. Each case isolates one branch. `_pyright_lsp_baseline` supplies the shared healthy-venv/python3 setup. `_make_claude_shim` supplies a `claude` stub that varies only what the branch needs. Cases that reach the prompt run under a real pty.
  - `_make_claude_shim` docstring (ADDRESS 4). The first paragraph follows the reviewer's split: the bash-not-python reason; the three handled subcommands; logging each invocation's `$*` to call_log, one line per call; exiting 1 on anything unhandled. The second paragraph's two `X=None simulates …; otherwise …` sentences split into four.
  - `test_jq_absent_skips_via_pipefail_not_shim_exit_code` docstring (ADDRESS 4), as separate sentences: "jq absent must be caught by the real `set -o pipefail` pipe failure on `claude ... | jq -r ...`, not by a shim exit code." "The shim's own `plugin marketplace list` call succeeds here." "jq itself is what's missing." "No _link_real_jq call: that's the point."
  - Case changes in `TestPyrightLspStep`. Every non-pty case asserts exit 0. "Skip line" means `"not an interactive terminal" in result.stdout`.
    - Rename `test_registered_and_not_installed_runs_install` to `test_registered_and_not_installed_without_terminal_prints_command_and_skips`. It asserts the skip line, `f"cd {os.path.realpath(repo_root_stub)} && claude plugin install {_PYRIGHT_LSP_PLUGIN} --scope local"` in stdout, and no `plugin install` line in the call log.
    - Rename `test_local_entry_for_other_directory_still_triggers_install` to `..._still_reaches_install_arm`. It asserts the skip line and no `(already installed)`. Keep its docstring.
    - `test_plugin_list_read_failure_proceeds_as_if_none_installed` keeps its warning assertion. It replaces the install-call assertion with the skip line and no `plugin install` call. Its docstring ends "so the install arm is still reached."
    - Add `test_project_scope_entry_at_matching_path_still_reaches_install_arm` (ADDRESS 6). It runs with no terminal and `plugins=[(_PYRIGHT_LSP_PLUGIN, "project", str(repo_root_stub))]`. It asserts the skip line and no `(already installed)`. Its docstring mirrors test_install_sh_project_scope_plugins.py:268-270: the `.scope == "local"` filter must be load-bearing, so a same-id, same-path entry at another scope must not read as installed here.
    - Add `test_interactive_yes_installs_at_local_scope`. It runs under the pty with reply `"y\n"`. It asserts exit 0, that the install calls equal `[f"plugin install {_PYRIGHT_LSP_PLUGIN} --scope local"]`, and `f"Install {_PYRIGHT_LSP_PLUGIN} at local scope? [y/N]"` in stderr (row 51).
    - Add `test_interactive_enter_declines_and_leaves_plugin_uninstalled`. It runs under the pty with reply `"\n"`. It asserts exit 0, no `plugin install` call, and `f"leaving {_PYRIGHT_LSP_PLUGIN} uninstalled"` in stdout.
    - Add `test_interactive_eof_declines_and_leaves_plugin_uninstalled`. It runs under the pty with reply `"\x04"`, the terminal's EOF character (Ctrl-D), which gives `read` a clean EOF without closing the master. It asserts exit 0, no `plugin install` call, and `f"leaving {_PYRIGHT_LSP_PLUGIN} uninstalled"` in stdout. This pins the `|| answer=""` guard inside the real script, not only in the direct tests (plan-review staff-sdet finding).
    - Replace `test_install_failure_does_not_abort_script` with `test_confirmed_install_failure_does_not_abort_script`. It runs under the pty with reply `"y\n"` and `install_exit=1`. It asserts exit 0 and `"failed to install"` in stderr.
    - Every other `TestPyrightLspStep` case stays unchanged, because none of them reaches the prompt.
  - New class `TestConfirmPluginInstall`, after `TestPyrightLspStep` under its own section banner. Its helper runs `[shutil.which("bash") or "/bin/bash", "-c", script]` with `input=answer`. The script is `set -euo pipefail`, then `. <shlex-quoted _PLUGIN_INSTALL_LIB>`, then `if _confirm_plugin_install pyright-lsp@claude-plugins-official local; then echo confirmed; else echo declined; fi`. The shape follows test_install_sh_project_scope_plugins.py:45-56. Parametrize two tests:
    - `"y\n"`, `"Y\n"`, and `"yes\n"` print `confirmed`.
    - `"n\n"`, `"\n"`, `"x\n"`, and `""` (EOF) print `declined`.
    - Each case asserts exit 0, which proves EOF does not trip `set -e`.
- **`README.md`** (ADDRESS 1)
  - Line 514's comment becomes `# creates .venv, installs requirements-dev.txt, offers the pyright-lsp plugin (contributor only)`.
  - Line 525's paragraph, in this order:
    1. `./install-dev.sh` also offers to install `pyright-lsp@claude-plugins-official` at local scope, giving Claude Code sessions in this checkout Python diagnostics and navigation.
    2. Local scope records the plugin only in this checkout's gitignored `.claude/settings.local.json`. The "so it reaches contributors…" clause is dropped.
    3. The install runs only when `claude-plugins-official` is already registered and you answer `y` at the prompt.
    4. If the marketplace is not registered, the script prints the `claude plugin marketplace add anthropics/claude-plugins-official` command instead.
    5. That registration applies to every project on your profile.
    6. A non-interactive run prints the `claude plugin install` command to run from this checkout instead of prompting.
    7. The existing worktree sentence, unchanged.
    8. The existing link sentence, ending "for why the plugin reaches contributors only, and only from an interactive terminal."
    9. The existing binary sentence and its four bullets, unchanged.
- **`docs/design-decisions/pyright-lsp-installed-per-contributor.md`** (M8, plus ADDRESS fix 3)
  - The H1, line 3, and the README-pointer line stay unchanged. Sources gains `claude/.claude/CLAUDE.md` and `docs/design-decisions/sentinel-config-consolidation.md`.
  - The body stays one paragraph, one fact per sentence, in this order:
    1. `install-dev.sh` offers to install the plugin at local scope from an interactive terminal.
    2. This repo's committed `.claude/settings.json` does not enable it.
    3. A non-interactive run, such as an agent's, prints the install command instead of running it, because the plugin is not a declared dependency (`` `claude/.claude/CLAUDE.md` § "Safety" ``). Keep the citation on one line.
    4. That terminal check is not a security boundary, because a self-allocated pseudo-terminal satisfies it ([sentinel-config-consolidation.md](sentinel-config-consolidation.md)).
    5. The project-scope sentence, unchanged.
    6. The local-scope sentence, unchanged.
    7. The never-registers sentence, unchanged.
    8. The sibling decision [official-marketplace-not-preregistered.md](official-marketplace-not-preregistered.md) stopped this repo from adding that registration.
    9. A profile that already registered it keeps the registration until its owner removes it by hand.
    10. The session-start-clone sentence, unchanged.
    11. The script checks for `pyright-langserver` but never installs it, because the recommended installer, `pipx`, is not a declared dependency.
    12. `pipx` also installs outside `.venv`. This splits the same two-reason shape as ADDRESS 5.
    13. Apart from a plugin install the contributor confirms, the script installs only `requirements-dev.txt`'s pinned dependencies.
    14. The sibling-decision-stands sentence, unchanged.
  - Do not claim the check blocks a direct `claude plugin install` (row 50).
- **Its check:** Verification step 5.

**Reuse opportunities:**
- install.sh:729-740: moved, not rewritten (A).
- install.sh:744-747: the warn-and-proceed shape.
- `register-marketplace.sh:62-63` and `:134-137`: the registered-list check and the diagnostic wording.
- `_stow_migration_lib.sh:1-13` and install.sh:97-103: the library header and sourcing precedent.
- install.sh:438-440 and :938-940: the caller-must-check-TTY contract comment (C).
- install.sh:495-505 and :943-954: `read -r -p … || answer=""`, `case [Yy]*`, and the `✓ leaving … disabled` decline line (C).
- install.sh:518-521: the "not an interactive terminal" skip wording (C).
- test_migrate_legacy_config.py:464-496: the pty runner (C).
- test_cleanup_merged_branches.py:280-301: the `stdin=subprocess.DEVNULL` default (C).
- test_install_sh_project_scope_plugins.py:45-56 and :265-285: the sourced-library direct-test shape and the scope deny-side test (C).
- `test_install_dev.py`'s `_write_stub`, `_run_script`, `_make_healthy_venv_stub`, `_make_claude_shim`, `_link_real_jq`, and `_pyright_lsp_baseline`.
- `official-marketplace-not-preregistered.md` as the decision-file template.

**Dispatch split:**
- A and B have landed.
- **Dispatch C** is one dispatch. The prompt text, the skip-line wording, and the README and decision-doc sentences that describe them must agree. Splitting would restate the gate's background in every prompt.
- **Revert order:** revert C, then B, then A, or all together. `install-dev.sh` sources A's library, and C's prompt helper lives in it. Reverting C alone leaves B's working but unprompted install, which brings back round 7's finding. The PR body states this.

## Verification

1. **Engineer-run, before Dispatches A and B.** This step gated the design. It installs software, so the engineer runs it. Dispatch B has landed, and B required the first outcome in 1e.
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
   - Run the same `select-tests.py` command, and do not widen its selection by hand. Expect the full suite, with reason `global-trigger`: the branch diff against `origin/main` changes `claude/.claude/scripts/select-tests.py` itself, which is in its own `GLOBAL_TRIGGER_PATHS`. That is the tool widening on its own, not a by-hand widening.
   - Run `.venv/bin/ruff check claude/.claude/` (test `.py` files changed).
   - Run `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck` (`install-dev.sh`, `install.sh`, and the new library changed).
4. **Manual, engineer-run, after pulling the branch:**
   - Run `./install-dev.sh` from the main root. Expect `✓ pyright-lsp@claude-plugins-official (already installed)` (from step 1b) and `✓ pyright-langserver (…)`. Then `git status --porcelain` shows no tracked change. This settles M5's local-scope path match against a real install. The already-installed branch comes before M10's gate, so no prompt appears.
   - Ask Claude to find all references to an existing hook helper. Confirm that it uses LSP find-references rather than a grep dispatch.
   - Optional and non-destructive: re-run `./install-dev.sh` with `CLAUDE_CONFIG_DIR` pointed at a freshly created empty temp directory. Expect branch 3's skip line and no `marketplace add`. That is the state the check guards (rows 9-10).
5. **After Dispatch C:**
   - Run the same `select-tests.py` command, and do not widen its selection by hand. Expect the full suite, with reason `global-trigger`: the branch diff against `origin/main` still changes `claude/.claude/scripts/select-tests.py` itself (Dispatch B), which is in its own `GLOBAL_TRIGGER_PATHS`. That is the tool widening on its own, not a by-hand widening.
   - Run `../../../.venv/bin/ruff check claude/.claude/` from the worktree, because the test file changed.
   - Run `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck`, because `install-dev.sh` and the library changed.
   - Run `git grep -n 'pyright-lsp' -- README.md install-dev.sh docs/design-decisions/pyright-lsp-installed-per-contributor.md`. Check every hit: none may say the script installs the plugin without a prompt.
   - The Bash-tool terminal probe already ran this session (row 47), so it needs no re-run.
6. **Manual, engineer-run, after Dispatch C:**
   - a. In a Claude Code session's `!` mode, run `test -t 0 && echo stdin-is-a-tty || echo stdin-not-a-tty`, and record the result in row 55.
   - b. Optional, from the main root in a terminal. It reinstalls software, so the engineer runs it.
     - Check `claude plugin uninstall --help` for the scope flag, then uninstall the local-scope plugin.
     - Run `./install-dev.sh < /dev/null`. Expect the skip line and `cd <main root> && claude plugin install pyright-lsp@claude-plugins-official --scope local`.
     - Run `./install-dev.sh`, answer `y`, and expect `→ installing`.
     - Finish with `git status --porcelain`, which shows no tracked change.

## Out of scope

- **Fixing stale `file:line` references in already-written plan/PR prose.** That is a documentation-practice question LSP tooling does not reach. It is flagged for the engineer to decide separately if it is still a concern.
- **A shell/bash LSP.** No official plugin exists, and the engineer declined it.
- **Enabling `pyright-lsp`, or declaring `claude-plugins-official`, in either committed settings file.** Rows 8, 20, and 40.
- **Registering the marketplace from `install-dev.sh`, or running `pipx install pyright` from it.** See settled points (a) and (b).
- **The staff-platform-engineer finding that `install.sh`'s project-scope loop lacks a marketplace check.** After M1, the loop installs only `@claude-config` plugins, and `claude-config` is registered by `register-marketplace.sh`, which install.sh:715 runs just before the loop (row 34). The failure that finding describes no longer occurs. Reply to the finding with that reason.
- **`install.sh`'s project-scope loop installing with no prompt.** It restores declared dependencies, so M10's reasoning does not apply (row 56).
- **`autoUpdate` and #1033's Revisit trigger.** Row 37's schema evidence suggests that official-marketplace-not-preregistered.md's Revisit trigger ("a per-marketplace refresh or cache control") may now be met. Whether 2.1.283 honors the key at runtime is unknown. Raise it to the reviewer as a separate issue.
- **`claude/.claude/rules/settings-json-conventions.md:10-11`.** If Verification step 1 showed a local-scope enable activating, that line is wrong. It is stowed to every consumer, so raise its correction to the reviewer as a separate change rather than widening this diff.
- **CLAUDE.md:11 and install.sh:956/972's `install-dev.sh` descriptions.** They still accurately describe the script's venv role. README's Tests section is the canonical home for the plugin step.
- **`_INSTALL_START`/`_INSTALL_END` source extraction in `test_install_sh_project_scope_plugins.py`.** `_run_install_block` still regex-extracts and runs the install loop's source text, the pattern `.claude/rules/shell-script-conventions.md` forbids. Dispatch A removes only the match-function half. Raise it to the reviewer as a separate change rather than widening Dispatch A.
- **Moving the library's direct tests into a library-named test file under `claude/.claude/scripts/tests/`.** `TestProjectPluginAlreadyInstalled` stays where it is, to keep the extraction diff minimal. `TestConfirmPluginInstall` lives in `test_install_dev.py`, next to the tests of its only caller.
- **A flag or environment variable that installs the plugin without a terminal.** It was not requested, and an agent could type it, which would reopen the gap M10 closes (M10 (a)).
- **A test pinning that the committed `.claude/settings.json` excludes `pyright-lsp`.** Round 7's ciso-reviewer raised it, and the orchestrator marked it DEFER this round (row 57).
- **A hook gating an agent's direct `claude plugin install`.** No hook inspects that command today (row 50). Raise it to the reviewer separately.
- **Hardening the terminal check against a self-allocated pty.** The repo accepts that residual (row 49). If a later review raises a finding against the gate itself, treat it first as the compounding-defensive-layers tell (`claude/.claude/CLAUDE.md` § "Working Style"), not as a reason to add a layer.
- **One shared y/n prompt helper for `install.sh` and `migrate-legacy-config.sh`.** Five prompts inline the same shape (row 48). Consolidating them is a separate change.
- **Editing `docs/design-decisions/official-marketplace-not-preregistered.md` or its CHANGELOG entry.** Both are dated records, and their decision stands.
- **A CHANGELOG entry.** Stow consumers see no runtime change. The new library is a behavior-preserving extraction plus a helper that only `install-dev.sh` calls, and `install-dev.sh` is contributor-only and has never had an entry (row 38).
- **`register-marketplace.sh`'s own `--scope user` adds** (`register-marketplace.sh:93`, `:121`). Per row 15, they may declare into the stow-tracked settings file. The issue predates this branch, so raise it to the reviewer separately.
- **A timeout wrapper around `claude plugin install`.** The engineer kept the comment-only mitigation (row 22), and no grounded timeout value exists.
- **Running any install.** `./install-dev.sh` (which restores pinned dependencies and now prompts before its plugin install), `claude plugin install`, `claude plugin marketplace add`, and `pipx install pyright` are software installs or registry changes. The engineer runs them personally (CLAUDE.md Safety, and Verification steps 1, 4, and 6).
- **Pinning `pyright` in `requirements-dev.txt`** (row 6).