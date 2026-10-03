# Git-native commit hooks declined as a replacement for the PreToolUse commit gates

*2026-10-03.*

Counts as of 2026-10-03, measured with `wc -l` unless a line range is given. The question is whether git-native client hooks (`pre-commit`, `pre-merge-commit`, `prepare-commit-msg`, `commit-msg`) should replace this repo's PreToolUse commit gates. Those hooks would reach git through an environment-scoped `core.hooksPath` or a repo-local one. They should not. A replacement retires only the command-shape layer, and it adds a new integration layer: a dispatcher, an environment carrier, and a residual PreToolUse deny. Net complexity does not fall.

## What would and would not retire

Best case, a replacement deletes these:

- `claude/.claude/hooks/deny-invisible-commit-content.sh` (434 lines).
- The commit-shape predicates in `claude/.claude/hooks/_lib.sh` (203 lines with comments, across two ranges):
  - `_lib_commit_fragment_has_worktree_target` (lines 1374-1426).
  - `_lib_fragment_concludes_commit_shape`, `_lib_command_concludes_commit_shape`, `_lib_command_concludes_commit`, `_lib_command_concludes_marker_gated_commit`, `_lib_fragment_concludes_commit`, and the `_LIB_CONTINUE_VERBS_*` grammar (lines 1564-1713).
- The commit-detection preamble of each moved gate.
- The `-a`/pathspec `git diff HEAD` arm and the `-F` argument parsing in `deny-pii-in-commits.sh`, because a hook reads the final index and the message file directly.
- Tests of those predicates, about 1.7k to 2.3k lines:
  - `test_deny_invisible_commit_content.py` (1,151 lines).
  - `test_lib.py` lines 6186-6749 (564 lines), of which 58 do not retire: `TestSplitFragmentsPipefailContract` (lines 6528-6572) and the sha256 probe helper (lines 6737-6749).
  - Possibly `test_parse_git_command.py` (342 lines) and `test_hook_command_normalization.py` (219 lines).
  - The low end is 1,151 + 564 - 58. The high end adds the last two files.

Those deletions are measured against 3,714 lines of gate code:

- 2,610 lines in the seven stowed gates under `claude/.claude/hooks/`: `require-code-review.sh` (218), `check-skill-length.sh` (137), `check-claude-md-length.sh` (127), `guard-settings-session-keys.sh` (164), `deny-pii-in-commits.sh` (635), `deny-private-project-refs.sh` (895), and `deny-invisible-commit-content.sh` (434).
- 1,104 lines in the three plugin gates: `require-skill-review.sh` (435), `require-plugin-version-bump.sh` (269), and `require-npm-version-bump.sh` (400).

The gate tests would move to a real-commit harness rather than disappear.

These stay under either design:

- The novel-content diff base, `_lib_git_inprogress_state` through `_lib_code_review_marker_value` (`_lib.sh` lines 972-1257), including `_lib_gate_diff_base`. A hook still has to decide what to hash mid-merge.
- The marker hash machinery.
- The shared fragment splitter. Nine hook scripts under `claude/.claude/hooks/` call `_lib_split_fragments`, and most do not move.
- The `skill-management` plugin's own copy of the commit-shape and splitter closure (`plugins/skill-management/hooks/_lib.sh` lines 621-843).
- The `--continue` grammar in `enforce-marker-script-shape.sh` (line 659) and in the plugin's `_lib_chains_marker_write_before_commit`.
- `require-ready-for-review.sh`, `require-worktree-for-git-writes.sh`, and `advance-past-commit-stall.sh`.

The best case is not the realistic case:

- `deny-invisible-commit-content.sh` can retire only after every gate that reads the PreToolUse `git diff --cached` snapshot has moved. In stow-plus-plugin installs, that set includes the plugin's `require-skill-review.sh` and both semver gates (`docs/design-decisions/skill-review-gate-disarms-on-empty-base-relative-diff.md` line 218; `deny-invisible-commit-content.sh` lines 4-10).
- Plugins cannot attach to a single environment-supplied hooks directory, and a plugin-only install has no dispatcher.
- If the plugin gates stay on PreToolUse, `deny-invisible-commit-content.sh` stays, and so do the shared predicates it calls (`docs/design-decisions/rebase-continue-marker-gate-carveout.md` lines 36-42).
- Realistic retirement then shrinks to the per-gate preambles and the PII gate's `-a`/pathspec and `-F` arms.

Smaller slices add net code, because every predicate stays. Moving only the PII gate to `prepare-commit-msg` is an example.

## Which hooks git fires

Observed on git 2.43.0 on 2026-10-03 in scratch repositories, with `core.hooksPath` supplied through `GIT_CONFIG_COUNT`, `GIT_CONFIG_KEY_0`, and `GIT_CONFIG_VALUE_0`:

| Operation | Hooks that fired |
|---|---|
| Plain commit | `pre-commit`, `prepare-commit-msg`, `commit-msg`, `post-commit` |
| `commit --amend` | the plain-commit set, plus `post-rewrite` |
| Commit in a linked worktree | the plain-commit set, with `pre-commit`'s working directory at that worktree's root |
| Conflicted merge concluded by a commit or `merge --continue` | the plain-commit set |
| Single-commit `cherry-pick --continue` | the plain-commit set |
| Clean `merge --no-ff` | `pre-merge-commit`, `prepare-commit-msg`, `commit-msg` |
| Rebase picks, clean or after `--continue` | `prepare-commit-msg`, `post-commit`, `post-rewrite` |
| Clean `cherry-pick`; clean `revert` | `prepare-commit-msg`, `post-commit` |
| `am` | `applypatch-msg`, `pre-applypatch`, `post-applypatch` |
| `am --continue` | `pre-applypatch`, `post-applypatch` |
| `stash` | none |

Override behavior observed in the same scratch repositories:

- `commit --no-verify` suppresses `pre-commit` and `commit-msg`. `merge --no-verify` suppresses `pre-merge-commit`. Neither suppresses `prepare-commit-msg`.
- `git -c core.hooksPath=/dev/null commit` fires no hooks, and the `-c` value beats the environment pair.
- An environment-supplied `core.hooksPath` beats a repo-local one, so the repo's own hooks do not run unless the dispatcher chains them. With the environment pair unset, the repo-local hooks run.

`GIT_CONFIG_GLOBAL` cannot override the pair either. That follows from git-config(1)'s scope precedence, where command-line scope outranks global scope, and was not probed separately.

Not probed, so not relied on:

- Whether a non-zero `prepare-commit-msg` aborts a pick inside a sequencer.
- Whether a multi-commit `cherry-pick` or `revert --continue` skips `pre-commit`.
- What `git diff --cached` shows during `commit -a` or a pathspec commit.
- Whether `MERGE_HEAD` is present at `pre-merge-commit` for a clean merge.
- Whether `rebase --apply`, which runs the `am` hooks, fires `prepare-commit-msg`.

## Costs a replacement adds

The first four bullets apply to every consumer repo Claude touches.

- **Dispatcher.** An environment-supplied `core.hooksPath` replaces the repo's own hook directory for every event, not only the gated ones. The dispatcher needs:
  - a stub for every githooks(5) event name;
  - a way to resolve the directory git would have used, from `core.hooksPath` at each scope, then the common-dir `hooks` fallback;
  - loop prevention when that directory is the dispatcher's own;
  - stdin forwarding for `pre-push`, `post-rewrite`, and `reference-transaction`.
- **Silent loss of consumer hooks.** A missed event does not error. The consumer's own hook stops running inside Claude sessions. Husky, the pre-commit framework, and git-lfs are the known cases. A skipped git-lfs `pre-push` publishes pointers without their objects.
- **Hook-manager matrix.** This repo would own compatibility with husky versions, lefthook, git-lfs, and worktree-scoped config.
- **Latency.** A stub per event adds a shell spawn on routine git operations in every repo, including `reference-transaction` on every ref update. This repo's stated hook budget is under 100 ms per fire (`docs/design-decisions/skill-review-gate-disarms-on-empty-base-relative-diff.md` line 174).
- **Plugin blocker.** `core.hooksPath` holds one value, and the last command-line entry wins. Stow and each plugin cannot each supply a hooks directory. A plugin-only install has no dispatcher.
- **Residual PreToolUse deny.** Every shape that disables hooks must stay denied (see the threat-model section below). That deny needs two parts:
  - A case-insensitive token match for `--no-verify`, `hookspath`, `GIT_CONFIG_COUNT`, `GIT_CONFIG_KEY_`, `GIT_CONFIG_VALUE_`, `GIT_CONFIG_PARAMETERS`, `env -i`, and `unset GIT_CONFIG`.
  - A walk over the commit's own arguments for `-n`, including clustered short options such as `-anm`. `_lib_commit_fragment_has_worktree_target` already does that walk and inherits its documented mis-parse of attached short-flag values (`_lib.sh` line 1409).
  - Unscoped, the token set over-denies ordinary shapes such as husky's `git config core.hooksPath .husky` and a consumer's own `GIT_CONFIG_COUNT` use. Scoping it to commits needs the commit-shape predicate the replacement deletes.
- **Launchers that reset the environment.** `sudo`, `su -`, `nix-shell --pure`, and `docker run` strip the carrier. They form an open class that no token list enumerates. The current gates scan `sudo git commit` (`_lib.sh` line 1289).
- **Carrier-absent fail-open.** Git treats a missing hooks directory or hook as "no hook" and proceeds (githooks(5)). Each case below silently turns every moved gate off, including both `irreversible` scanners, with the PreToolUse predicates already deleted:
  - git older than 2.31, which ignores `GIT_CONFIG_COUNT`. The README states no git floor.
  - A SessionStart writer that failed or did not run.
  - A subagent or headless session that does not source `CLAUDE_ENV_FILE`. Whether it does is unverified.
  - A dispatcher directory that stow has not linked yet after `git pull`. The existing bootstrap message says stow does not relink a new file into an existing directory (`claude/.claude/hooks/require-code-review.sh` line 60).
- **`GIT_CONFIG_COUNT` namespace.** The count-indexed namespace is shared. A static write clobbers inherited pairs such as `safe.directory` and `url.*.insteadOf`. An appending write must stay idempotent across SessionStart re-fires and per-call re-sourcing.
- **Installer collisions.** Inside a Claude session, `git config core.hooksPath` would return the dispatcher directory.
  - The pre-commit framework's installer may refuse to run while `core.hooksPath` is set. This is from memory and unprobed.
  - `git lfs install` may write into the dispatcher directory. Under the stow tree, that writes through symlinks into tracked files.
  - Husky's local setting is shadowed for the whole session.
  - The dispatcher cannot live in `claude/.claude/hooks/`, because `claude/.claude/hooks/tests/test_hook_alignment.py` (lines 85-102) sweeps every file there as a hook.
- **Test-suite collisions.** Local test suites commit in subprocesses that inherit the environment (for example, the `git_repo` fixture at `claude/.claude/hooks/tests/conftest.py` line 401). A carrier would fire the moved gates inside them. This repo can scrub the variable in its four `conftest.py` roots, as the autouse fixture at `claude/.claude/hooks/tests/conftest.py` lines 366-375 does for a leaking variable. A consumer's test suite and release tooling cannot be scrubbed.
- **Mid-sequencer denial.** A PreToolUse deny happens before git state changes. A failing `prepare-commit-msg` inside a rebase or cherry-pick pick leaves the operation stopped mid-sequence, and the agent then has to recover.
- **New docs and tests.** Each item above needs its own documentation and coverage.

## Threat-model interaction

`docs/hooks.md` § "Threat-model tiers" defines the tiers and the regression-only rule. These are the ways they bite here:

- The regression-only rule makes the residual deny non-negotiable. A change may not newly allow `--no-verify`, `-n`, `-c core.hooksPath`, `env -i`, or any `--continue` form that the merge-base denies.
- That same rule counts a new fail-open path as a regression. The carrier-absent cases above are exactly that. A PreToolUse fail-closed check against them would need the commit-shape predicate the replacement deletes.
- Two of the moved gates, `deny-pii-in-commits.sh` and `deny-private-project-refs.sh`, carry `irreversible`. The residual deny would be their backstop against evasion, so under the dependency invariant it would inherit `irreversible`.
- A hook reads git state, not command text. That removes most of the surface where steered text could evade detection. It earns no tier credit, because the commit-boundary disclosure gates deliberately carry no `untrusted-input` tier (`docs/hooks.md` line 47).
- The dependency invariant fixes the deletion order. `deny-invisible-commit-content.sh` stays until the plugin gates stop needing its snapshot backstop.

## Who gets gated

Both mechanisms for choosing who is gated fail as a replacement.

- **Claude sessions only, through an environment-scoped `core.hooksPath` written to `CLAUDE_ENV_FILE`.** This is the only viable replacement mechanism.
  - It fires in whatever repo a Claude Bash call commits in, which matches today's scope (`deny-pii-in-commits.sh` lines 12-15: the gate fires on every repo).
  - It carries every cost in the section above.
- **All commits, through a repo-local `core.hooksPath`.** This is rejected as a replacement.
  - It reaches only repos where someone ran the install. Every stow consumer's other repos, and `git -C <other-repo>` commits, lose coverage they have today.
  - Gating human commits on `/code-review` markers is a category error. The workflow gates would have to branch on `CLAUDECODE`, which collapses back into the first mechanism.
  - A repo-local value cannot coexist with a consumer's own husky setting, and an environment-scoped layer shadows it whenever both are active.
  - Its one real benefit is content scanning of human and IDE commits in this repo. That would be an additive per-repo feature, not a replacement. `docs/security-hardening.md` (lines 625-629) already names it out of scope.

## Reconsideration triggers

Reopen this decision when any of these holds:

1. A maintainer decides that the scanners' coverage gaps are unacceptable. The gaps are clean merge, cherry-pick, revert, rebase picks, `am`, and script-file commits. No shipped control inspects, before publication, the content those gates missed (`docs/hooks.md` line 28). The first investment would be a `pre-push` scan of the outgoing commits, sized before any work starts. It would not be this replacement.
2. A maintainer decides that human and IDE commits in this repo need content scanning. That would be the repo-local mechanism, built as an additive feature.
3. The git version this repo supports gains config-defined hooks that let more than one provider register for the same event. That removes the dispatcher and the plugin blocker. Git 2.54 is believed to add `hook.<name>.command`, which is unverified. The README states no git floor today.
4. A commit gate needs yet another command-shape patch. Compare that patch against this design before writing it.

## Sources

Observations are dated. External pages are unpinned, so re-read them to check what a claim is scoped to.

- githooks(5), for hook events, `--no-verify` scope, and missing-hook behavior: https://git-scm.com/docs/githooks
- git-config(1), for `GIT_CONFIG_COUNT` and config scope precedence: https://git-scm.com/docs/git-config
- git-rebase(1), for the apply and merge backends: https://git-scm.com/docs/git-rebase
- Introduction of `GIT_CONFIG_COUNT`, `GIT_CONFIG_KEY_<n>`, and `GIT_CONFIG_VALUE_<n>`: `Documentation/RelNotes/2.31.0.txt` in the git/git repository (the "Two new ways to feed configuration variable-value pairs via environment variables" entry).
- Claude Code hooks documentation, for `CLAUDE_ENV_FILE`: https://code.claude.com/docs/en/hooks
- Hook behavior table: observed on git 2.43.0 on 2026-10-03 in scratch repositories.
- Line counts and ranges: the repo files named inline, as of 2026-10-03.
- Tier rules: `docs/hooks.md` § "Threat-model tiers"
