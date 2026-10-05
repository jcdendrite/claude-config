# Git-native commit hooks declined as a replacement for the PreToolUse commit gates

*2026-10-03.*

The question is whether git-native client hooks (`pre-commit`, `pre-merge-commit`, `prepare-commit-msg`, `commit-msg`) should replace this repo's PreToolUse commit gates. Those hooks would reach git through an environment-scoped `core.hooksPath` or a repo-local one. They should not. A replacement retires only the command-shape layer, and it adds a new integration layer: a dispatcher, an environment carrier, and a residual PreToolUse deny. The added layer was not sized, so the verdict does not rest on a net line count. It rests on four structural points: the machinery that stays under either design, the carrier-absent fail-open, the plugin blocker, and the unresolved placement of three gates.

## What would and would not retire

Best case, a replacement deletes these:

- `claude/.claude/hooks/deny-invisible-commit-content.sh`.
- The commit-shape predicates in `claude/.claude/hooks/_lib.sh`: `_lib_fragment_concludes_commit_shape`, `_lib_command_concludes_commit_shape`, `_lib_command_concludes_commit`, `_lib_command_concludes_marker_gated_commit`, `_lib_fragment_concludes_commit`, and the `_LIB_CONTINUE_VERBS_*` grammar.
- The commit-detection preamble of each moved gate.
- If `git diff --cached` inside the hook reflects the final index under `commit -a` and pathspec commits, which was not probed for `pre-commit` or `prepare-commit-msg`, the `-a`/pathspec `git diff HEAD` arm in `deny-pii-in-commits.sh`. The `-F` argument parsing there would retire because a hook reads the message file directly.
- Tests of those predicates:
  - `test_deny_invisible_commit_content.py`.
  - `test_lib.py` from `TestCommandConcludesCommit` up to, but not including, `_git_supports_sha256_object_format`, except the parts that do not retire, such as `TestSplitFragmentsPipefailContract` and the sha256 probe helper `_git_supports_sha256_object_format`.
  - Possibly `test_parse_git_command.py` and `test_hook_command_normalization.py`.

`_lib_commit_fragment_has_worktree_target`'s walk is kept for the residual deny, so it is renamed, not deleted.

Best case, on the order of 600 lines of gate code and on the order of 2k lines of tests. Which test helpers retire is approximate.

The deletions edit the seven stowed gates under `claude/.claude/hooks/`: `require-code-review.sh`, `check-skill-length.sh`, `check-claude-md-length.sh`, `guard-settings-session-keys.sh`, `deny-pii-in-commits.sh`, `deny-private-project-refs.sh`, and `deny-invisible-commit-content.sh`. They edit none of the three plugin gates, `require-skill-review.sh`, `require-plugin-version-bump.sh`, and `require-npm-version-bump.sh`, which keep their own matcher. Those three read the PreToolUse `git diff --cached` snapshot that `deny-invisible-commit-content.sh` backstops.

The gate tests would move to a real-commit harness rather than disappear.

These stay under either design:

- The novel-content diff base, `_lib_git_inprogress_state` through `_lib_code_review_marker_value` in `_lib.sh`, including `_lib_gate_diff_base`. A hook still has to decide what to hash mid-merge.
- The marker hash machinery.
- The shared fragment splitter. Many hook scripts under `claude/.claude/hooks/` call `_lib_split_fragments`, and most do not move.
- The `skill-management` plugin's own copy of the commit-shape and splitter closure in `plugins/skill-management/hooks/_lib.sh`.
- The `--continue` grammar (`VALID_CHAINED_COMMIT_PATTERN`) in `enforce-marker-script-shape.sh` and in the plugin's `_lib_chains_marker_write_before_commit`.
- `require-ready-for-review.sh`, `require-worktree-for-git-writes.sh`, and `advance-past-commit-stall.sh`.

The best case is not the realistic case:

- `deny-invisible-commit-content.sh` can retire only after every gate that reads the PreToolUse `git diff --cached` snapshot has moved. In stow-plus-plugin installs, that set includes the plugin's `require-skill-review.sh` and both semver gates.
  - The header of `deny-invisible-commit-content.sh` points to `docs/hooks.md` § "Gate hooks" for the dependent gates.
  - `docs/design-decisions/skill-review-gate-disarms-on-empty-base-relative-diff.md` § "Known gap: the plugin matcher" covers the skill-review gate.
  - The `CHANGED_FILES` assignments in `require-plugin-version-bump.sh` and `require-npm-version-bump.sh` read `git diff --cached`.
- No mechanism exists today for a plugin to attach to a single environment-supplied hooks directory, and a plugin-only install has no dispatcher.
- If the plugin gates stay on PreToolUse, `deny-invisible-commit-content.sh` stays, and so do the shared predicates it calls (`docs/design-decisions/rebase-continue-marker-gate-carveout.md` § "Why `deny-invisible-commit-content.sh` takes the broad predicate").
- Three stowed gates have no clean git event today: `check-skill-length.sh`, `check-claude-md-length.sh`, and `guard-settings-session-keys.sh`.
  - `pre-commit` misses `rebase --continue`, which those gates cover today, so placing them there is a regression.
  - `prepare-commit-msg` fires on ordinary rebase, cherry-pick, and revert picks, so placing them there over-denies.
  - Unless a maintainer accepts a regression, they stay on PreToolUse, and `deny-invisible-commit-content.sh` stays with them.
- Realistic retirement then shrinks to the per-gate preambles and the PII gate's `-F` arm, plus its `-a`/pathspec arm if `git diff --cached` reflects the final index under `commit -a` and pathspec commits.

Smaller slices retire little, because the shared predicates stay for every gate that did not move. Moving only the PII gate to `prepare-commit-msg` retires at most that gate's own arms.

## Which hooks git fires

Observed on git 2.43.0 on 2026-10-03 in scratch repositories created with `git init -b main`. Each table probe set `core.hooksPath` through `GIT_CONFIG_COUNT`, `GIT_CONFIG_KEY_0`, and `GIT_CONFIG_VALUE_0`. The last two control probes unset that pair. Every probe ran with `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1`, and `GIT_EDITOR=true` so the committer's own git config could not skew results. The probe script is not committed.

The probe instrumented nine hooks as logging stubs that exit 0: `pre-commit`, `pre-merge-commit`, `prepare-commit-msg`, `commit-msg`, `post-commit`, `applypatch-msg`, `pre-applypatch`, `post-applypatch`, and `post-rewrite`. Every other githooks(5) event was uninstrumented. In the table, "none" means none of those nine.

| Operation | Instrumented hooks that fired |
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
| `stash` | none of the nine |

`stash` updates `refs/stash`, and githooks(5) says `reference-transaction` runs for any reference update, so the uninstrumented `reference-transaction` may have fired.

git-rebase(1), in its behavioral-differences section on hooks, calls the `post-commit` and `post-checkout` calls an accident of implementation and says rebase will likely stop making them. It does not mention `prepare-commit-msg`, so the rebase-pick rows are observed behavior that git does not document. A scanner that relies on `prepare-commit-msg` for rebase picks rests on that observation.

Override behavior observed in the same scratch repositories:

- `commit --no-verify` suppresses `pre-commit` and `commit-msg`. `merge --no-verify` suppresses `pre-merge-commit` and `commit-msg`. Of the nine instrumented hooks, `prepare-commit-msg` was the only one that can block a commit and still ran under `commit --no-verify` or `merge --no-verify`. `am --no-verify` was not run.
- `git -c core.hooksPath=/dev/null commit` fires no hooks, and the `-c` value beats the environment pair.
- An environment-supplied `core.hooksPath` beats a repo-local one, so the repo's own hooks do not run unless the dispatcher chains them. With the environment pair unset, the repo-local hooks run.

`GIT_CONFIG_GLOBAL` cannot override the pair either. That follows from git-config(1)'s scope precedence, where command-line scope outranks global scope, and was not probed separately.

Not probed, so not relied on:

- Whether a non-zero `prepare-commit-msg` aborts a pick inside a sequencer. githooks(5) says a non-zero exit aborts the commit. If it does not abort a pick, every scanner placed on that hook is off for rebase, cherry-pick, and revert picks, which is a fail-open.
- Whether a multi-commit `cherry-pick` or `revert --continue` skips `pre-commit`.
- Whether `$GIT_INDEX_FILE` and `git diff --cached` inside `pre-commit` and `prepare-commit-msg` reflect the final index during `commit -a` or a pathspec commit. This question concerns both hooks.
- Whether `MERGE_HEAD` is present at `pre-merge-commit` for a clean merge.
- Whether `rebase --apply` fires `prepare-commit-msg`.
- Whether git errors on a `core.hooksPath` directory that does not exist. githooks(5) states only that a hook without the executable bit is ignored.
- Whether git accepts an abbreviated `--no-verify`.

## Costs a replacement adds

Dispatcher, silent loss of consumer hooks, hook-manager matrix, and latency apply to every consumer repo Claude touches.

- **Dispatcher.** An environment-supplied `core.hooksPath` replaces the repo's own hook directory for every event, not only the gated ones. The dispatcher needs:
  - a stub for every githooks(5) event name, and a way to notice when a newer git adds one;
  - a way to resolve the directory git would have used, from `core.hooksPath` at each scope, then the common-dir `hooks` fallback;
  - loop prevention when that directory is the dispatcher's own;
  - stdin forwarding for `pre-push`, `post-rewrite`, and `reference-transaction`;
  - exit-code propagation from the chained hook, because a swallowed non-zero exit is a fail-open;
  - a fail posture per event class;
  - a log line when a chain is skipped;
  - a recovery route that does not assume `CLAUDE_ENV_FILE` reaches every Bash call;
  - a way to see that the moved gates are not firing, because the carrier-absent fail-open is silent and no log line covers it.
- **Silent loss of consumer hooks.** A missed event does not error. The consumer's own hook stops running inside Claude sessions. Husky, the pre-commit framework, and git-lfs are the known cases. A skipped git-lfs `pre-push` publishes pointers without their objects.
- **Hook-manager matrix.** This repo would own compatibility with husky versions, lefthook, git-lfs, and worktree-scoped config.
- **Latency.** A stub per event adds a shell spawn on routine git operations in every repo, including `reference-transaction` on every ref update. The cost is unmeasured, because no stub was timed. This repo's stated hook budget is under 100 ms per fire (`docs/design-decisions/skill-review-gate-disarms-on-empty-base-relative-diff.md` § "Performance").
- **Plugin blocker.** `core.hooksPath` holds one value, and the last command-line entry wins, so stow and each plugin cannot each supply a hooks directory through it. No mechanism exists today for them to share one. A dispatcher that discovers plugin-supplied hook scripts would be one, and it would enlarge the dispatcher. A plugin-only install has no dispatcher.
- **Residual PreToolUse deny.** Every shape that disables hooks must stay denied (see the threat-model section below). That deny has two parts:
  - A case-insensitive token match for `--no-verify` and its unambiguous abbreviations, `hookspath`, `GIT_CONFIG_COUNT`, `GIT_CONFIG_KEY_`, `GIT_CONFIG_VALUE_`, `GIT_CONFIG_PARAMETERS`, `env -i`, `--ignore-environment`, and `unset GIT_CONFIG`. A plain substring match on `--no-verify` does not match an abbreviation, so the abbreviation member needs more than a substring.
  - A walk over the commit's own arguments for `-n`, including clustered short options such as `-anm`. `_lib_commit_fragment_has_worktree_target` already does that walk. The walk inherits that function's mis-parse of attached short-flag values, documented in the Known gaps list in the header of `deny-pii-in-commits.sh`. In `git commit -mtext -n`, the walk reads `-n` as `-mtext`'s value and never tests it. Inherited unchanged, that fail-open is a regression under the regression-only rule.
- **Residual-deny over-deny.** Unscoped, the token set over-denies ordinary shapes such as husky's `git config core.hooksPath .husky` and a consumer's own `GIT_CONFIG_COUNT` use. Scoping it to commits needs the commit-shape predicate the replacement deletes.
- **Launchers that reset the environment.** Launchers that reset the environment by default, such as `su -`, `nix-shell --pure`, and `docker run`, would strip the carrier. They form an open class that no token list enumerates. No launcher was probed. The current gates scan `sudo git commit` (the `_lib_fragment_invokes_git` header in `_lib.sh` lists it as accepted). A `sudo git commit` that a gate denies is denied today. Under sudo's default `env_reset` (configurable, per sudoers(5)), it would be allowed after the change.
- **Hook-file and dispatcher deletion, and hook overrides.** githooks(5) supports that an absent or non-executable hook file is ignored, so deleting a hook file leaves git with nothing to run for that event.
  - `chmod -x` on a hook, or renaming it, is an uncovered override of the same kind.
  - What git does with a nonexistent `core.hooksPath` directory, as after removing the dispatcher directory or unlinking it with `stow -D`, is unprobed. If git hard-fails there, the result is an outage of hooked git operations in every repo the session touches until the directory is relinked, not a fail-open.
  - No token in the deny covers any of these.
- **Carrier-absent fail-open.** Each case below turns every moved gate off without an error, including both `irreversible` scanners, with the PreToolUse predicates already deleted:
  - git older than 2.31, which ignores `GIT_CONFIG_COUNT`. The README states no git floor.
  - A SessionStart writer that failed or did not run.
  - A subagent or headless session that does not source `CLAUDE_ENV_FILE`. Whether it does is unverified.
  - A dispatcher directory that stow has not linked yet after `git pull`, or that was later unlinked or deleted. The existing bootstrap message says stow does not relink a new file into an existing directory (`claude/.claude/hooks/require-code-review.sh`, the "could not source _lib.sh" deny). This case also depends on git ignoring a missing hooks directory, which is unprobed (see above).
- **`GIT_CONFIG_COUNT` namespace.** The count-indexed namespace is shared. A static write clobbers inherited pairs such as `safe.directory` and `url.*.insteadOf`. An appending write must stay idempotent across SessionStart re-fires and per-call re-sourcing.
- **Installer collisions.** Inside a Claude session, `git config core.hooksPath` would return the dispatcher directory.
  - The pre-commit framework's installer may refuse to run while `core.hooksPath` is set. This is from memory and unprobed.
  - `git lfs install` may write into the dispatcher directory. Under the stow tree, that writes through symlinks into tracked files.
  - Husky's local setting is shadowed for the whole session.
- **Dispatcher placement.** A `*.sh` dispatcher in `claude/.claude/hooks/` would be swept in as a hook by `_all_hook_files()` in `claude/.claude/hooks/tests/test_hook_alignment.py`, which globs `*.sh` non-recursively there. Extensionless git stubs and subdirectories do not match that glob. A recursive `**/*.sh` glob over the hooks directory in `claude/.claude/hooks/tests/test_config_lib.py` would still sweep a `.sh` file in a subdirectory. Other sweeps were not checked.
- **`prepare-commit-msg` is not a `pre-commit` substitute.** githooks(5) says it should not be used as a replacement for the `pre-commit` hook. A replacement places the content scanners there.
- **Test-suite collisions.** Local test suites commit in subprocesses that inherit the environment, for example the `git_repo` fixture in `claude/.claude/hooks/tests/conftest.py`. A carrier would fire the moved gates inside them. This repo can scrub the variable in its `conftest.py` roots, as the autouse `_clear_claude_pid_env` fixture in the same file does for a leaking variable. A consumer's test suite and release tooling cannot be scrubbed.
- **Mid-sequencer denial.** A PreToolUse deny happens before git state changes. If a failing `prepare-commit-msg` aborts a rebase or cherry-pick pick, the operation stops mid-sequence and the agent has to recover. That abort is unprobed (see above).
- **New docs and tests.** Each item above needs its own documentation and coverage.

## Threat-model interaction

`docs/hooks.md` § "Threat-model tiers" defines the tiers and the regression-only rule. These are the ways they bite here:

- Under the regression-only rule, a commit that a gate denies today must not become allowed because it carries `--no-verify`, `-n`, `-c core.hooksPath`, or `env -i`, or because it is a `--continue` form. No placement may allow one.
- The carrier-absent cases above are new fail-open paths, which that rule treats as regressions. A PreToolUse fail-closed check against them would need the commit-shape predicate the replacement deletes.
- No token list closes the launcher, hook-file, and dispatcher-deletion overrides in the Costs section above. Dispatcher deletion is such an override only if git does not hard-fail on a missing hooks directory, which is unprobed. A dispatcher-based replacement would need an explicit maintainer decision on those regressions before it shipped.
- Two of the moved gates, `deny-pii-in-commits.sh` and `deny-private-project-refs.sh`, carry `irreversible`. The residual deny would be their backstop against evasion, so under the dependency invariant it would inherit `irreversible`.
- For commit-shape detection, a hook reads git state, not command text, which removes most of the surface where steered text could evade detection while the hook runs. Disabling the hook stays driven by command text and environment (`--no-verify`, `-c core.hooksPath`, `env -i`). A dispatcher-based replacement also adds an open launcher class and carrier-absent fail-opens that today's layer lacks. It earns no tier credit, because the commit-boundary disclosure gates deliberately carry no `untrusted-input` tier (`docs/hooks.md` § "Threat-model tiers").
- Under the regression-only rule, retiring `deny-invisible-commit-content.sh` before its dependent gates move would be a regression (see "The best case is not the realistic case" above).

## Who gets gated

Both mechanisms for choosing who is gated fail as a replacement.

- **Claude sessions only, through an environment-scoped `core.hooksPath` written to `CLAUDE_ENV_FILE`.** This is the only viable replacement mechanism.
  - It fires in whatever repo a Claude Bash call commits in, which matches today's scope (the `deny-pii-in-commits.sh` header: "Fires on every repo").
  - It carries every cost in the section above.
- **All commits, through a repo-local `core.hooksPath`.** This is rejected as a replacement.
  - It reaches only repos where someone ran the install. Every stow consumer's other repos, and `git -C <other-repo>` commits, lose coverage they have today.
  - Gating human commits on `/code-review` markers is a category error. The workflow gates would have to branch on `CLAUDECODE`, which collapses back into the first mechanism.
  - A repo-local value cannot coexist with a consumer's own husky setting, and an environment-scoped layer shadows it whenever both are active.
  - Its one real benefit is content scanning of human and IDE commits in this repo. That would be an additive per-repo feature, not a replacement. `docs/security-hardening.md` § "Limitations" already names it out of scope.

## Reconsideration triggers

Reopen this decision when any of these holds:

1. A maintainer decides that the scanners' coverage gaps are unacceptable. The gaps are clean merge, cherry-pick, revert, rebase picks, `am`, and script-file commits. No shipped control inspects, before publication, the content those gates missed (`docs/hooks.md` § "Threat-model tiers").
   - The first step would be to choose the publication-boundary control, comparing a client `pre-push` scan, a PreToolUse push gate, and platform-side push protection. What the platform control covers, and on which plan tiers, must be resolved against the provider's own documentation before anything cites it.
   - A client `pre-push` scan needs the same carrier and dispatcher as this replacement.
   - It needs destination-remote scoping and per-commit scanning, so content added and then removed is still caught.
   - `git push --no-verify` skips it.
   - It sees only `git push`, not publication through API or MCP content writes.
   - Any chosen control would be sized before work starts.
2. A maintainer decides that human and IDE commits in this repo need content scanning. That would be the repo-local mechanism, built as an additive feature.
3. The git version this repo supports gains config-defined hooks that let more than one provider register for the same event. That removes the dispatcher and the plugin blocker. Git 2.54 is believed to add `hook.<name>.command`, which is unverified. The README states no git floor today.
4. A commit gate needs yet another command-shape patch. Compare that patch against the costs and constraints listed in this document before writing it.

## Sources

Observations are dated. External pages are unpinned, so re-read them to check what a claim is scoped to.

- githooks(5), for hook events, `--no-verify` scope, the non-executable-hook rule, and the `prepare-commit-msg` caveat: https://git-scm.com/docs/githooks
- git-config(1), for `GIT_CONFIG_COUNT` and config scope precedence: https://git-scm.com/docs/git-config
- git-rebase(1), for the behavioral-differences section on hooks: https://git-scm.com/docs/git-rebase
- git-commit(1), for `-n` as the short form of `--no-verify`: https://git-scm.com/docs/git-commit
- Introduction of `GIT_CONFIG_COUNT`, `GIT_CONFIG_KEY_<n>`, and `GIT_CONFIG_VALUE_<n>`: `Documentation/RelNotes/2.31.0.txt` in the git/git repository (the "Two new ways to feed configuration variable-value pairs via environment variables" entry). That entry does not name the three variables, so the version mapping rests on knowledge beyond it. git-config(1)'s ENVIRONMENT text documents the variables themselves.
- Claude Code hooks documentation, for `CLAUDE_ENV_FILE`: https://code.claude.com/docs/en/hooks
- Hook behavior table and override behavior: the probe described under "Which hooks git fires".
- Line figures are approximate, counted with `wc -l` on 2026-10-04. The verdict does not rest on them. Predicate and test blocks are cited by name because line numbers shift.
- Tier rules: `docs/hooks.md` § "Threat-model tiers"
