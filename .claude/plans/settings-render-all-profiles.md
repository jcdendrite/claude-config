# Make per-profile rendering explicit, loud on failure, and documented

## Context

Goal: make rendering a non-default Claude Code profile (`CLAUDE_CONFIG_DIR`) a documented step that belongs to whatever stows claude-config into that profile, make its failure mode name the remedy, and record the fail-closed requirement for that tool. This change is stacked on `settings-base-render` (#744).

Ask: the engineer's task "Close the non-default-profile settings.json gap before merging claude-config #744", as cited by earlier revisions of this plan. The engineer chose Option A: "Yeah A sounds the best to me." On detecting stale profiles: "yes treat the reminder as a separate follow-up". On rendering profiles other than the inherited one: "rendering profiles that don't correspond to the one that was inherited seem like a flawed design".

Why now: #744 renames the tracked `settings.json` to `settings.base.json` and renders it locally. After the pull, a non-default profile whose `settings.json` was a stow symlink into the checkout dangles, and it silently loses `permissions.deny` and hooks. `install.sh` renders only `~/.claude`. `ensure-settings-render.sh` renders only the inherited profile, and only when that profile holds a `settings.base.json`.

## Approach

`render-settings.sh` keeps #744's behavior: base, overlay and target all come from the profile's own directory. A profile is rendered by whatever stows claude-config into it, which places `settings.base.json` in the profile along with the rest of the package. This PR does not change where base is read from.

What changes:
- **M2.** The "settings.base.json not found" refusal names the remedy: stow claude-config into that profile first.
- **M6.** The disclosure line names its target when that target is not `~/.claude`.
- **M7.** `DISABLE_FEEDBACK_COMMAND` joins the overlay `env` allowlist, in its own commit.
- **M8.** Docs and CHANGELOG state who renders a profile, when to re-run it, the first-render migration, and the fail-closed requirement for the stowing tool.
- `ensure-settings-render.sh` and `install.sh` keep their behavior.

Coverage:
- A provisioning tool that stows a profile and then runs the render command for it renders that profile (given 4).
- A new shell renders its inherited profile when that profile holds a `settings.base.json`.
- A stow user who runs no such tool gets the docs and the manual command.
- A profile that no new shell inherits goes stale after a pull that changes base, until something renders it again.
- A profile that was never stowed, whose `settings.json` dangles, or whose `settings.json` resolves to a file without base's floor (such as the stray tracked-path file a session recreated through a stow symlink), still starts Claude Code without that floor. This PR's render changes cannot prevent that for a profile nothing renders, because hooks are registered inside `settings.json` (row 9).

This narrows the gap #744 opens. It does not close it (rows 8, 9).

Alternatives set aside:
- **Render every declared profile at each shell start.** Set aside by the engineer's choice of A (rows 5, 6).
- **Read base from beside the script, so a profile needs no base copy.** Path-dependent: a file-by-file stow makes the neighbouring base the profile's own copy, and a worktree's script would apply its mid-edit base to a live profile.
- **Fall back to `~/.claude/settings.base.json` when a profile has none.** The engineer rejected it: "F could cause the wrong settings to be associated with the wrong Claude account... It's better if the settings just fail I guess then it's obvious". Base carries no `env` block and personal's environment values live in its overlay, so the fallback would not copy personal env values (row 12). The rejection stands on the account-association concern.
- **Have `install.sh` render the other profiles.** It cannot discover profiles it does not stow (row 3).
- **Ship #744 alone.** It leaves every stowed non-default profile dangling with no signal.

Stacking: this PR targets `settings-base-render`, #744's branch. The engineer selected "Both, and draft #744 (Recommended)" for what gates merging #744. `[engineer-verified: label "Both, and draft #744 (Recommended)"]` #744 therefore becomes a draft, and its merge guard names two conditions: this PR has landed, and the out-of-repo provisioning follow-up has run. #744 is reported as conflicting with main. Syncing its branch is a separate decision.

### Assumption ledger

**Root:** #744 makes a non-default profile's stow-linked `settings.json` dangle, and that profile runs with no `permissions.deny` floor and no hooks until something renders it. This affects every stow consumer who runs more than one profile. A single-profile consumer must see no change in output.

**Givens:**

1. Claude Code treats a dangling or missing `settings.json` as absent and prints no diagnostic. `[unverified: empirical in .claude/plans/settings-base-render.md given 2 (Claude Code v2.1.260); not re-run]`
2. A profile picked after the rc file runs, by direnv or a `CLAUDE_CONFIG_DIR=<dir> claude` prefix, is invisible to the render that runs at rc time. `[unverified: docs/scripts.md states it; not exercised]`
3. Stowing claude-config into a non-default profile happens outside this repo, because `install.sh` stows only into `$HOME`. CLAUDE.md says this repo depends on no other repository. Whoever stows a profile owns when it is rendered. `[verified: install.sh:598-601; docs/scripts.md render-settings.sh entry; CLAUDE.md "Working in this repo"]`
4. A provisioning tool in another repository will run `CLAUDE_CONFIG_DIR=<dir> <dir>/scripts/render-settings.sh` for each profile, between its stow and its `register-marketplace.sh` call. Another party owns it, and this plan does not change it. `[unverified: the session's description; that repository was not read]`

**Assumptions:**

5. The engineer chose Option A. `[engineer-verified: "Yeah A sounds the best to me."]`
6. Option A's contents come from the session's description, which the engineer accepted as a whole. `[unverified]` This plan reads that choice as superseding the earlier direction that claude-config owns rendering of every profile. `[unverified: the supersession is this plan's reading]`
7. Detecting a rendered profile older than base, and warning at shell start when the inherited profile's `settings.json` is dangling or missing, are separate follow-ups. `[engineer-verified: "yes treat the reminder as a separate follow-up"; "we can have the shell start warning as a follow up"]` Placing them with the provisioning tool or a later PR here is the session's proposal. `[unverified]`
8. The PR title and body say "narrows". `[engineer-verified: "Yes, say \"narrows\" (Recommended)"]` After the rewrite the engineer confirmed it again by selecting the label "Keep both (Recommended)" for the question on "narrows" and M7 staying in this PR. `[engineer-verified: label "Keep both (Recommended)"]` The residuals are accepted and documented. `[engineer-verified: "Yeah accept and document. We need a follow up PR in [the engineer's private provisioning repository] to handle this. It needs to be handled but not in this PR."]`
9. Hooks and the deny floor are registered inside `settings.json`, so no hook can fail closed when that file is missing, dangling, or lacks base's floor. Only something outside it can: managed settings, which also covers a bare `claude`, or a launch check in the tool that provisions the profile, which misses direnv and `CLAUDE_CONFIG_DIR=<dir> claude` launches. `[unverified: the session's reading of how Claude Code loads hooks. Silence is confirmed for `claude doctor` and `claude auto-mode reset`; interactive startup is untested (CHANGELOG [Unreleased])]` The engineer's requirement: "if an account is not provisioned properly it can't be the case that hooks just silently stop working and all the gates let everything through." `[engineer-verified: that quote]` Meeting it belongs to the follow-up in the engineer's private provisioning repository.
10. `render-settings.sh` takes base, overlay and target all from `$config_dir` and refuses to run without `$config_dir/settings.base.json`. `[verified: render-settings.sh:76-79, :93-96]`
11. The refusal text is asserted by substring `settings.base.json not found` in three tests. `[verified: test_install_sh_settings_render.py:247, :546, :575]` M2 keeps that substring.
12. `settings.base.json` has no `env` key, 32 `permissions.deny` rules and hooks for 9 events. Overlay-owned keys are limited to an allowlist (`autoMode`, `env`, `skillListingBudgetFraction`, plus `permissions.defaultMode`). `[verified: python read of claude/.claude/settings.base.json; render-settings.sh:83]`
13. The render's disclosure line prints only when the render changed something, because the render runs at every new shell. `[verified: render-settings.sh:321-322, :363; .claude/plans/settings-base-render.md:92]`
14. `render-settings.sh` runs under `set -euo pipefail` and reads `$HOME` only as the default of `${CLAUDE_CONFIG_DIR:-$HOME/.claude}`. An unguarded `$HOME` in M6 would abort a render that has `CLAUDE_CONFIG_DIR` set and `HOME` unset. `[verified: render-settings.sh:2, :76]`
15. `register-marketplace.sh` exits 0 and does nothing when `<config-dir>/settings.json` is not a regular file. `[verified: register-marketplace.sh:53-57]`
16. Base defines neither `enabledPlugins` nor `extraKnownMarketplaces`. A first render over a dangling symlink keeps only what base and the overlay define, and names nothing it dropped. `[verified: CHANGELOG.md [Unreleased] migration bullets]`
17. CHANGELOG's Migration steps hardcode `~/.claude`. Step 3's `git checkout` discards any edit that any profile wrote through its symlink to the tracked file. `[verified: CHANGELOG.md Migration steps]`
18. #744 has not shipped. `[verified: CHANGELOG.md under [Unreleased]]`
19. The engineer asked for the variable name to be verified. `[engineer-verified: "Uhhh just get a subagent to verify the name dude. Look up docs"]` The engineer then selected "Keep both (Recommended)" on whether M7 stays in this PR as its own commit. `[engineer-verified: label "Keep both (Recommended)"]` Keeping `DISABLE_BUG_COMMAND` and documenting no value other than `1` are the session's choices. `[unverified]`
20. `DISABLE_FEEDBACK_COMMAND` is the current documented name, and `DISABLE_BUG_COMMAND` is its accepted older name. The page gives no meaning for any other value. `[verified: https://code.claude.com/docs/en/env-vars, fetched by a subagent: "Set to `1` to disable the `/feedback` command... Also disables `/bug` and `/share`... The older name `DISABLE_BUG_COMMAND` is also accepted"]`
21. `code-writer` does not commit, so each commit boundary needs its own dispatch. `[verified: claude/.claude/agents/code-writer.md:21]`
22. Public prose cannot name the provisioning tool, its repository, or the owner's profile names or counts. `[verified: CLAUDE.md "Redact private-project-identifying content"]`

### Mechanisms

**M2: the not-found refusal names the remedy.** `anchors: root, row10, row11`
- At render-settings.sh:94 the message keeps its `<base_file> not found -- cannot render settings.json without it` prefix and appends: `; stow claude-config into <config_dir> first (a profile gets settings.base.json from the stow, not from this script; docs/scripts.md's stow-packages.sh entry lists the packages and targets)`.
- That entry is the supported no-tool route: run stow for each package `stow-packages.sh` prints, with `<config_dir>` as the target.
- Emitters: a hand run, a provisioning tool, `install.sh` (`~/.claude` only) and `ensure-settings-render.sh` (gate keeps default-profile behavior). For the default profile `install.sh`'s and ensure's own `./install.sh` hint stays the remedy, and M2's text is not the primary message there.
- Other failing exits (overlay refusals, jq failures) keep their text. `render-settings.sh:119` says an existing `settings.json` keeps its rules, which is untrue for a never-rendered profile. Aligning those messages is a follow-up (Out of scope).
- No other behavior changes. The exit status stays 1 and nothing is written.

**M6: the disclosure line names a non-default target.** `anchors: row4, row13, row14`
- **Why.** A provisioning tool renders several profiles in one run, so each disclosure line must say which profile it describes.
- **Rule.** At render-settings.sh:363 the line is unchanged when `${HOME:-}` is non-empty and `$config_dir -ef "$HOME/.claude"`. Otherwise ` in <config_dir>` follows "this render changed settings.json".
- **Unset `HOME`.** Reading `${HOME:-}` keeps a render with `CLAUDE_CONFIG_DIR` set and `HOME` unset from aborting.
- **Unchanged.** Single-profile output does not change.
- **Contract homes.** The disclosure comment at render-settings.sh:309-320 and `docs/auto-mode.md`'s bullet that lists what a render's stderr names (near :190-194) each gain one clause: the line names its profile when it is not `~/.claude`.

**M7: `DISABLE_FEEDBACK_COMMAND` joins the overlay `env` allowlist next to `DISABLE_BUG_COMMAND`.** `anchors: row19, row20`
It lands as its own commit, and both names stay. The doc line states only the documented "set to `1`" behavior.

**M8: docs and CHANGELOG.** `anchors: row3, row4, row7, row9, row15, row16, row17, row22`
- Each doc site says what each render path covers and what to run for the rest.
- Where the provisioning tool is meant, the wording is "whatever stows claude-config into the profile".
- The docs state the requirement for that tool: a profile must never start with no hooks. Because hooks live in `settings.json`, the tool needs a check outside it, such as managed settings or a launch check that refuses to start `claude` on a missing or dangling `settings.json`.
- The fail-closed statement is about content, not presence: a profile's `settings.json` must carry base's `permissions.deny` and `hooks`. A launch check in a provisioning tool misses direnv and `CLAUDE_CONFIG_DIR=<dir> claude` launches. Only managed settings covers a bare `claude`. Whether to adopt managed settings, and its cost for stow consumers, is left to the follow-up.
- The docs hedge: silence for a dangling `settings.json` is confirmed for `claude doctor` and `claude auto-mode reset`, and interactive startup is untested.
- A provisioning tool must stop on a non-zero render exit before it registers or launches, because `register-marketplace.sh` exits 0 on a missing `settings.json`.
- The docs use one render command form with an absolute `CLAUDE_CONFIG_DIR`.
- The migration steps live in CHANGELOG. docs/scripts.md points at them.
- `register-marketplace.sh`'s early exit is already stated in its own entry. Other sites point there.

### Rollback

The PR body carries this entry.
- **Revert order, when the commits are preserved.** Revert Dispatch 3 (docs), then Dispatch 2 (M2, M6, tests), then Dispatch 1 (M7). Dispatch 1 stands on its own.
- **Under a squash merge** the three commits collapse into one. Revert that one commit to undo everything. A partial revert means reverting by hand the files of the dispatch to undo, using the Critical files list.
- **After reverting Dispatch 2.** `render-settings.sh` returns to #744's message and disclosure line. Every profile keeps its last rendered `settings.json` as a regular file, so none dangles because of the revert.
- **After reverting Dispatch 3.** The docs return to #744's wording, which tells users to place a base in each profile by hand.
- **After #744 has merged,** revert the commits, not the PR.

## Critical files

Three `code-writer` dispatches, run in sequence, each one commit (row 21). Every commit leaves the suite green. After each dispatch, run `.venv/bin/python3 claude/.claude/scripts/select-tests.py`, `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck`, and `.venv/bin/ruff check claude/.claude/ claude-skills/`.

**Dispatch 1: M7 alone.**
- `claude/.claude/scripts/render-settings.sh`: add `"DISABLE_FEEDBACK_COMMAND"` to the env allowlist (near :30).
- `claude/.claude/scripts/tests/test_render_settings.py`: add the name to `ENV_ALLOWED_NAMES` (near :35-43).
- `docs/security-hardening.md`: the `DISABLE_BUG_COMMAND` line (near :569) becomes: "`DISABLE_FEEDBACK_COMMAND` — set to `1` to disable the `/feedback` command. Claude Code's [env-var reference](https://code.claude.com/docs/en/env-vars) says it also disables `/bug` and `/share`, and still accepts the older name `DISABLE_BUG_COMMAND`."

**Dispatch 2: M2 and M6 with tests.**
- `claude/.claude/scripts/render-settings.sh`: the M2 message and the M6 disclosure change.
- `claude/.claude/scripts/tests/test_render_settings.py`: the new cases in Verification 2. Anchor edits by text, not line number.
  - `_run_script` cannot unset `HOME`, and `{"HOME": ""}` is not the unset case. The unset-`HOME` case builds its environment by popping `HOME` from a copy of `os.environ` and calling `subprocess.run` directly.
  - The dispatch prompt tells `code-writer` to run the new M2 and M6 non-default cases against the unmodified script first and confirm they fail.
- `claude/.claude/scripts/render-settings.sh` also: the comment at :309-320.
- `docs/auto-mode.md`: the stderr-disclosure bullet (near :190-194), one clause.
- `claude/.claude/hooks/tests/test_install_sh_settings_render.py`: the three existing substring assertions stay valid. No edit unless one fails.

**Dispatch 3: docs (M8).**
- `docs/scripts.md`, `render-settings.sh` entry. Anchor by text:
  - Replace the bullet that says the profile's directory "must already hold `settings.base.json`" with: the profile must be stowed from claude-config first, because the stow places `settings.base.json` there; otherwise the command exits with the stow-first message.
  - Replace the "Render any other profile by hand" bullet with: `install.sh` renders `~/.claude` only, and a new shell renders only the profile it inherits; whatever stows claude-config into any other profile should run the command for it after each stow and after each pull that changes `settings.base.json`; with no such tool, run it by hand at those times.
  - Add one sentence: a profile whose `settings.json` is a dangling symlink at its first render silently loses every key base does not ship; CHANGELOG's "Migration, other profiles" bullet gives the steps.
  - Add one sentence: render a profile before registering it, because `register-marketplace.sh` does nothing until the profile has a `settings.json`.
- `docs/scripts.md`, `ensure-settings-render.sh` entry: no change.
- `CHANGELOG.md`, `[Unreleased]`:
  - Replace the bullet that says `install.sh` renders only `~/.claude` (and any hedge about other profiles) with: `install.sh` renders only `~/.claude`; a new shell renders only the profile it inherits, when that profile holds a `settings.base.json`; whatever stows claude-config into another profile renders it.
  - New **Migration, other profiles** sub-bullet. It applies to each non-default profile whose `settings.json` symlinks to the checkout's `claude/.claude/settings.json`:
    - Order: pull; run `./install.sh` for `~/.claude`; then, for each other profile, steps 1 and 2 with `<dir>` in place of `~/.claude`; then step 3; then re-stow and render that profile. Step 3's `git checkout` discards edits any profile wrote through its symlink.
    - All stow-linked profiles' `settings.json` symlinks point at the same tracked path. A session in any one of them can recreate a stray file there, and every other profile's first render then carries that file's keys forward. Check for the stray file before re-stowing (`git status` in the checkout).
    - A materialized profile keeps its old floor as a regular file until its first render.
    - A profile that was not materialized dangles until its first render. Claude Code's silence is confirmed for `claude doctor` and `claude auto-mode reset`, and interactive startup is untested, so it may run with no deny rules or hooks.
    - A profile's first render, when not materialized, drops every key base does not ship, including `enabledPlugins` and `extraKnownMarketplaces`, and names nothing it dropped.
    - Re-stow each profile so it holds `settings.base.json` (stow each package `stow-packages.sh` prints, with the profile as target), then run `CLAUDE_CONFIG_DIR=<absolute dir> ~/.claude/scripts/render-settings.sh` for it.
    - Run `CLAUDE_CONFIG_DIR=<dir> register-marketplace` after the first render. For a profile that was not materialized, that registers only the claude-config marketplace, so re-enable its other plugins yourself.
- `docs/security-hardening.md`, the Limitations bullet about non-default profiles (near :644-656), rewritten:
  - **Who renders a profile.** Stowing claude-config into a profile places `settings.base.json` there. A non-default profile is rendered only by a command run for it (a provisioning tool's or a hand render) or by a new shell that inherits it while it holds a `settings.base.json`.
  - **Before its first render.** A profile that was materialized keeps its old floor as a regular file. One that was not has no `settings.json` to enforce, and Claude Code's silence there is confirmed only for `claude doctor` and `claude auto-mode reset`.
  - **After a base change.** A profile that none of those three renders again keeps its previous floor, again with no diagnostic.
  - **Fail closed.** No hook can fail closed when `settings.json` is missing or dangling, because hooks are registered in that file. A floor that must reach every profile regardless belongs in managed settings ("Enterprise rollout: managed settings"), and a provisioning tool should also refuse to launch `claude` on a missing or dangling `settings.json`.
  - The existing `install.sh` warning sentence and the `ensure-settings-render.sh` hint sentence stay. Leave the rest of the bullet (near :652-656) as #744 wrote it unless it contradicts the points above.
  - Say a launch check misses direnv and `CLAUDE_CONFIG_DIR=<dir> claude` launches, and only managed settings covers a bare `claude`.

Reuse, don't reimplement: the `-ef` comparison at ensure-settings-render.sh:26; the tests' existing `_run_script` helper.

## Verification

1. After each dispatch, run `select-tests.py`, shellcheck and ruff as above. Every commit leaves the suite green.
2. The tests show these behaviors, one case each, in `test_render_settings.py`:
   - **M7:** the allowlist equality test and the per-name acceptance cases include the new name.
   - **M2, main use case.** A profile directory with no `settings.base.json` exits 1, stderr contains `settings.base.json not found` and `stow claude-config into {config_dir} first`, and no `settings.json` is created.
   - **M2, nothing disturbed.** The same refusal leaves an existing regular `settings.json` byte-identical, and leaves a dangling `settings.json` symlink dangling.
   - **M6, non-default target.** With `HOME` set to a tmp home and `CLAUDE_CONFIG_DIR` elsewhere, the line is `this render changed settings.json in <config_dir> -- `.
   - **M6, default target.** `<tmp home>/.claude` prints `render-settings.sh: this render changed settings.json -- ` with no `in`. Seed a disclosure and assert the full prefix. Add a case that spells the target with a trailing slash or through a symlink to `<tmp home>/.claude`, so replacing `-ef` with string equality fails.
   - **M6, unset `HOME`.** With `HOME` popped from a copy of `os.environ` and `CLAUDE_CONFIG_DIR` set, the render exits 0 and the line names `<config_dir>`. The test must fail if the `${HOME:-}` guard is changed to `$HOME`.
3. **Mutation check.** Change `${HOME:-}` to `$HOME` in M6, confirm the unset-`HOME` case fails, revert, and record the result in the PR body.
4. **Revert check.** At the final head, in a scratch worktree with its own `.venv`, revert Dispatch 3, then Dispatch 2, and run `select-tests.py` after each. Both are green. Record the result in the PR body, and say it proves the commits revert independently, not that a squash merge can be partly reverted.
5. **End-state check.** Read-only, on the author's machine, after the provisioning tool's change has run:
   - Each profile the author launches `claude` under has a regular `settings.json`.
   - Its `permissions.deny` and `hooks` equal base's, compared with `jq`.
   - Record pass or fail only. The plan, the PR and the commits carry nothing about which profiles there are or how many.
6. **PR body.**
   - Base branch `settings-base-render`. The body states the delta against #744 alone: a clearer refusal, a disclosure that names its profile, `DISABLE_FEEDBACK_COMMAND`, and documentation. It does not claim the gap is closed.
   - The title says "narrows" and not "loud on failure". The body says no rendering behavior changes: the PR changes one refusal message, one disclosure line, one allowlist entry and the docs.
   - The opening paragraph names the residuals, including the cohort of multi-profile users who run no provisioning tool and get no signal: a profile that no new shell inherits and no stowing tool renders again goes stale, and a profile that was never rendered runs with no hooks and no deny rules, which only a check outside `settings.json` can prevent.
   - Call out M7 as a separate commit that touches a security allowlist.
   - Include the Rollback entry and the results of items 3 and 4.
   - Note that a follow-up PR in a separate private repository handles provisioning and the fail-closed check, without naming it.
   - When this PR merges into #744's branch, correct any statement in #744's PR body that says a profile renders from its own `settings.base.json` without the stow-first requirement.

## Out of scope

- **Rendering profiles other than the inherited one at shell start.** Rows 5, 6.
- **Detecting a rendered profile older than base, and a shell-start warning for a dangling or missing inherited `settings.json`.** Separate follow-ups (row 7).
- **The provisioning tool's own change** to stow, render, then register each profile, and its fail-closed launch check. It lives in another repository (given 4, row 9).
- **A runtime signal in this repo for a multi-profile user who runs no provisioning tool.** `install.sh` cannot tell that user from a single-profile user (given 3), so for now the docs are the signal.
- **Changing ensure's gate, `install.sh`'s diverged-profile warning, or where base is read from.**
- **Aligning the other failing render exits' text** (overlay refusals, jq failures) with whether the profile has a floor. `render-settings.sh:119` is the clearest case.
- **`register-marketplace.sh` changes,** and `README.md`'s register-before-render ordering in its marketplace paragraph.
- **Syncing #744's branch with main.**
