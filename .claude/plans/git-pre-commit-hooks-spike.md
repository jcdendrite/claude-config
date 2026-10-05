# Spike: git-native client hooks as a replacement for PreToolUse commit gates

## Context

Goal: decide go/no-go on replacing claude-config's PreToolUse commit-text-matching gates with git-native client hooks (`pre-commit` / `pre-merge-commit` / `commit-msg` / `prepare-commit-msg`), and, if go, what the design keeps, deletes, and costs.

Ask: "I'm actually wondering if investing in pre-commit hooks would have eliminated a lot of the complexity" (the engineer's words in the prior session, relayed by the session handoff file), followed by the request "to do that spike". In this session the engineer selected "Let the spike compare both", "Probe first (Recommended)", and "Uncommitted plan file (Recommended)" for the clarifying questions, and "No, keep the freeze (Recommended)" for the coverage-goal question. After reading the no-go, the engineer widened the deliverable: "This belongs in a decision doc." On whether the plan ships too: "I meant plan and doc because that’s the convention".

Why now: the merge-aware review-gates work (PR #1215, merged) added more command-shape parsing on top of an already intricate gate layer. That made the engineer question the foundation. A standing constraint from the prior session (relayed by the handoff, not this session's words): "We shouldn't invest any more time into got commit related hooks. The machinery is already quite intricate." The outcome is a reviewed go/no-go recommendation, not an implementation. A no-go is a valid outcome.

Evidence inputs:

- Three research subagents (git source and docs, session-state reach, a measured inventory of the gate surface).
- A platform reviewer pass during plan review, referred to below as "the platform reviewer".
- An empirical probe on git 2.43.0: `/tmp/git-hooks-spike-probe.sh`, output `/tmp/git-hooks-spike-probe.out`, run once by the engineer via `!` and once from a Claude Bash tool call. It instrumented nine hooks (`pre-commit`, `pre-merge-commit`, `prepare-commit-msg`, `commit-msg`, `post-commit`, `applypatch-msg`, `pre-applypatch`, `post-applypatch`, `post-rewrite`) and masked config with `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1`, and `GIT_EDITOR=true`. The decision doc records the same set and masking, because the probe files are not committed.

## Approach

**Recommendation: no-go — keep the freeze.** A git-native replacement retires only the command-shape layer. The magnitude is in `docs/design-decisions/git-native-commit-hooks-declined.md` § "What would and would not retire". The method is in `docs/design-decisions/git-native-commit-hooks-declined.md` § "Sources". In the realistic case it retires much less. It keeps the novel-content base, the marker machinery, and the plugin matcher copy. It also adds a dispatcher that every repo Claude touches would depend on. The recorded target is sketched only as far as the verdict needed and is not a design to execute. A reopen starts from the decision doc's reconsideration triggers and designs gate placement, the residual deny, rollout and rollback order, and the probes for the `[unverified]` rows from scratch.

### Why it doesn't pay: net-complexity verdict

- **What retires, best case (rows 17, 18).**
  - Code:
    - `deny-invisible-commit-content.sh`.
    - The `_lib.sh` commit-shape predicates: `_lib_fragment_concludes_commit_shape`, `_lib_command_concludes_commit_shape`, `_lib_command_concludes_commit`, `_lib_command_concludes_marker_gated_commit`, `_lib_fragment_concludes_commit`, and the `_LIB_CONTINUE_VERBS_*` grammar.
    - `_lib_commit_fragment_has_worktree_target`'s walk is kept for the residual deny, so it is renamed, not deleted.
    - Each moved gate's commit-detection preamble.
    - `deny-pii-in-commits.sh`'s `-F` argument parsing, because a hook reads the message file directly. Its `-a`/pathspec `git diff HEAD` arm retires only if row 20 holds.
  - Tests: `test_deny_invisible_commit_content.py` and the commit-shape blocks of `test_lib.py` (the decision doc's § "What would and would not retire" names the blocks), plus possibly `test_parse_git_command.py` and `test_hook_command_normalization.py`. The other gate tests would move to a real-commit harness rather than be deleted.
- **What does not retire.**
  - `_lib_gate_diff_base` and the rest of the novel-content base (`_lib_git_inprogress_state` through `_lib_code_review_marker_value`).
  - The marker hash machinery.
  - The anchor-admissibility analysis. Its baseline bypass is plumbing plus a clean merge, ungated today (row 16).
  - The shared fragment splitter, which most of its callers keep using.
  - The skill-management plugin lib's commit-shape and splitter closure (`plugins/skill-management/hooks/_lib.sh`) (rows 11, 12).
  - Looking back, a git hook would have avoided the `--continue` arming predicates from the merge-aware work, but only if row 3 holds and row 4 does not (both `[unverified]`). The `--continue` grammar also lives in `VALID_CHAINED_COMMIT_PATTERN` in `enforce-marker-script-shape.sh` and in the plugin's `_lib_chains_marker_write_before_commit`, which stay. It would not have avoided the novel-content base, because a hook still has to decide what to hash mid-merge.
- **The best case is not the realistic case.**
  - `deny-invisible-commit-content.sh` can retire only once every gate that reads the PreToolUse `git diff --cached` snapshot has moved. In stow-plus-plugin installs that set includes the three plugin gates (row 13).
  - No mechanism exists today for a plugin to attach to the single env-supplied hooks dir, and a plugin-only install has no dispatcher at all (row 11).
  - If the plugin stays on PreToolUse, `deny-invisible-commit-content.sh` stays. So do the shared predicates it calls (`_lib_command_concludes_commit`, per `docs/design-decisions/rebase-continue-marker-gate-carveout.md` § "Why `deny-invisible-commit-content.sh` takes the broad predicate").
  - Three stowed gates (`check-skill-length.sh`, `check-claude-md-length.sh`, `guard-settings-session-keys.sh`) have no clean git event (rows 1, 15). `pre-commit` misses `rebase --continue`, which is a regression, and `prepare-commit-msg` over-denies ordinary picks. Unless the engineer accepts a regression, they stay on PreToolUse, and `deny-invisible-commit-content.sh` stays with them.
  - Realistic retirement then shrinks to the per-gate preambles and the PII gate's `-F` arm, plus its `-a`/pathspec arm if row 20 holds.
- **What gets added.** This spike did not estimate the size of any of these:
  - A dispatcher with a pass-through stub for every githooks(5) event, plus chaining logic (row 5).
  - A SessionStart hook that writes `CLAUDE_ENV_FILE` (row 8).
  - A residual PreToolUse bypass gate that still needs a walk over the commit's own arguments (row 19).
  - An environment scrub across this repo's test roots (row 10).
  - New docs for each of the above.
- **Further costs found in plan review.** Each applies only to the recorded target, and each strengthens the no-go:
  - **Carrier-absent fail-open.** The moved gates fire only if the `GIT_CONFIG_*` carrier reaches the Bash call and git finds an executable hook. githooks(5) says a hook without the executable bit is ignored, and what git does with a nonexistent `core.hooksPath` directory is unprobed. Each of the following silently turns every moved gate off, including both `irreversible` scanners, with the PreToolUse predicates already deleted:
    - git older than 2.31, which ignores `GIT_CONFIG_COUNT`. The README states no git floor. The git 2.31.0 release notes' entry "Two new ways to feed configuration variable-value pairs via environment variables" does not name the variable, so the mapping rests on knowledge beyond the cited entry.
    - A SessionStart writer that failed or didn't run.
    - Subagent or headless sessions that don't source `CLAUDE_ENV_FILE`, since row 8 is unverified.
    - A dispatcher dir that stow hasn't linked yet after `git pull` (the bootstrap message in `require-code-review.sh` says stow does not relink a new file into an existing directory), or that was later unlinked or deleted. This case also depends on git ignoring a nonexistent hooks dir, which is unprobed.
    - A hook file that was deleted, or made non-executable with `chmod -x` or by renaming it. githooks(5) documents the non-executable case as ignored.

    `docs/hooks.md` § "Threat-model tiers" counts a new fail-open path as a regression. A PreToolUse fail-closed check against this would need the commit-shape predicate the replacement deletes.
  - **Residual-deny completeness.** Today's gates scan `sudo git commit …` (the `_lib_fragment_invokes_git` header in `_lib.sh` lists it as accepted).
    - `sudo` and other launchers that reset the environment strip the carrier.
    - Those launchers form an open class that no token list can enumerate (`su -`, `nix-shell --pure`, `docker run`).
    - `sudo git commit` is denied at the merge-base. Under sudo's default `env_reset` (sudoers(5); configurable, not probed), it would be allowed after the change, which is a regression under row 14.
    - The recorded target cannot avoid that regression without an explicit engineer decision.
    - Deleting a hook file or making it non-executable (`chmod -x` or renaming it) is a further override with no token in the deny (row 6).
    - Removing the dispatcher dir is such an override only if git ignores a nonexistent `core.hooksPath` directory, which is unprobed.
  - **Residual-deny scope and tier.** Unscoped, the token set over-denies ordinary shapes such as husky's `git config core.hooksPath .husky` and a consumer's own `GIT_CONFIG_COUNT` use. Scoping it to commits needs the commit-shape predicate. As the evasion backstop for two `irreversible` scanners, it would inherit `irreversible` (`docs/hooks.md` § "Threat-model tiers").
  - **Carrier writer.** `GIT_CONFIG_COUNT` is a shared, count-indexed namespace. A static write clobbers inherited pairs (`safe.directory`, `url.*.insteadOf`). An appending write must stay idempotent across SessionStart re-fires and per-call re-sourcing.
  - **Installer collisions.** Inside a Claude session, `git config core.hooksPath` returns the dispatcher dir. Then:
    - `pre-commit install` refuses when `core.hooksPath` is set (from memory, unprobed).
    - `git lfs install` may write into the dispatcher dir. Under the stow tree, that writes through symlinks into tracked files.
    - Husky's local setting is shadowed for the whole session.
  - **Dispatcher placement.** A `*.sh` dispatcher in `claude/.claude/hooks/` would be swept in as a hook by `_all_hook_files()` in `test_hook_alignment.py`, which globs `*.sh` non-recursively there. Extensionless git stubs and subdirectories do not match that glob. A recursive `**/*.sh` glob over the hooks directory in `test_config_lib.py` would still sweep a `.sh` file in a subdirectory. Other sweeps were not checked.
  - **Dispatcher posture and latency.** The design needs these things it doesn't yet specify:
    - exit-code propagation from the chained hook;
    - a fail posture per event class;
    - a log line when a chain is skipped;
    - a recovery route that doesn't assume row 8;
    - a way to see that the moved gates are not firing, because the carrier-absent fail-open is silent and no log line covers it.

    Stubs for every event add a shell spawn on routine git operations in every repo. That includes `reference-transaction` on every ref update, against this repo's own under-100 ms budget (`skill-review-gate-disarms-on-empty-base-relative-diff.md` § "Performance"). The cost is unmeasured, because no stub was timed.
  - **Mid-operation denial.** A PreToolUse deny happens before git state changes. If a failing `prepare-commit-msg` aborts a rebase or cherry-pick pick (row 3, unprobed), the operation stops mid-sequence, which a cooperative agent then has to recover. If it does not abort, every scanner on that hook is off for those picks, which is a fail-open.
  - **Consumer test suites.** The row 10 collision is cheap to scrub in this repo: `conftest.py` already has an autouse fixture (`_clear_claude_pid_env`) that scrubs a leaking variable, and there are four conftest roots. It cannot be scrubbed in consumer repos' suites or release tooling.
- **Verdict.** The replacement swaps an open-ended layer that guesses from command text for a closed-form integration layer. That is a sounder foundation for "what is being committed," but the additions were not sized, so it is not shown to be a smaller one. The no-go rests on four structural points rather than a line count: the retained machinery (row 18), the carrier-absent fail-open, the plugin blocker (row 11), and the unresolved placement of three gates.

### Who's gated: the two options compared (rows 26, 27)

- **Claude sessions only, via an env-scoped `core.hooksPath` set through `CLAUDE_ENV_FILE`.** This is the only viable replacement mechanism.
  - It fires in whatever repo a Claude Bash call commits in. That matches today's scope (the header of `deny-pii-in-commits.sh` says it "Fires on every repo").
  - A human terminal outside Claude never receives the carrier. Whether the `!` shell does depends on row 8 (see row 7). If it does not, the engineer's escape hatch is preserved.
  - Cost: it shadows every repo's own hooks (husky, pre-commit framework, git-lfs), so the dispatcher must chain every event (row 5).
  - Cost: it fires inside local test suites and tooling that commit in subprocesses (row 10).
  - Cost: no mechanism exists today for plugins to register with it (row 11).
- **All commits, via a repo-local `core.hooksPath`.** Rejected as a replacement.
  - It reaches only repos where someone ran the install. Every stow consumer's other repos lose coverage they have today, and so do `git -C <other-repo>` commits.
  - Gating human commits on `/code-review` markers is a category error. The workflow gates would have to branch on `CLAUDECODE` anyway (row 7), which collapses back into the first option's behavior.
  - `core.hooksPath` holds one value, so this can't coexist with a consumer repo's own husky setting.
  - An env-scoped layer shadows it whenever both are active (row 5).
  - Its one real benefit is content scanning of human and IDE commits in this repo. That would be an additive per-repo feature, not a replacement, and `docs/security-hardening.md` § "Limitations" already names it out of scope.

### Threat-model interaction

- Every moved gate is `cooperative`. `deny-pii-in-commits.sh`, `deny-private-project-refs.sh`, and `deny-invisible-commit-content.sh` also carry `irreversible`.
- The regression-only rule applies at every tier (row 14). No placement may newly allow `--no-verify`/`-n`, `-c core.hooksPath`, `env -i`, or any `--continue` form denied today.
- Inside a git hook, the gate reads git state rather than command text. For commit-shape detection, that removes most of the surface where steered command text can evade detection while the hook runs. Disabling the hook stays driven by command text and environment. The target adds an open launcher class and carrier-absent fail-opens. The git-state read is the replacement's strongest argument, but it earns no tier credit: the commit-boundary disclosure gates deliberately carry no `untrusted-input` tier (`docs/hooks.md` § "Threat-model tiers").
- The regression-only rule fixes the deletion order (row 14). `deny-invisible-commit-content.sh` is the snapshot backstop its dependents name. Deleting it before the three plugin gates (row 13) stop needing it would be a regression for those gates, and that needs the engineer's decision, not a waiver.

### Reopen triggers

1. A decision that the scanners' coverage gaps (clean merge, cherry-pick, revert, rebase picks, `am`, script files) are unacceptable. No shipped control observes what the gates missed before publication (`docs/hooks.md` § "Threat-model tiers"), so this is a decision-time trigger, not an observed-incident one. The first step would then be to choose the publication-boundary control, comparing a client `pre-push` scan, a PreToolUse push gate, and platform-side push protection. A client `pre-push` scan is bounded to `git push` and is skipped by `git push --no-verify`. Any chosen control is sized first.
2. A decision that human and IDE commits in this repo need content scanning. That would be the repo-local option, built as an additive feature.
3. The git version this repo supports gains a way for more than one provider to register a hook (row 31). That removes the dispatcher and the plugin blocker. Newer git may already have config-defined hooks (`hook.<name>.command`; believed to be git 2.54, unverified). The README states no git floor today.
4. A commit gate needs yet another command-shape patch. Compare that patch against the decision doc's cost inventory and the recorded target's mechanisms (M2-M5) before writing it.

### Open question for the engineer

This is a genuine decision because the Ask measures complexity, while what the git-native layer really buys is coverage. If coverage of the accidental-leak paths (commits made by script files, rebase picks, clean cherry-picks) is a goal in its own right, the proportionate investment is trigger 1's `pre-push` scan, not this replacement. Without that goal, my default is to keep the freeze and start no new work.

**Resolved:** the engineer selected "No, keep the freeze (Recommended)" (row 32). The no-go stands, and no `pre-push` scan is proposed.

### Assumption ledger

**Root:** Decide whether git-native client hooks could replace claude-config's PreToolUse commit-text-matching gates with less machinery in total, without allowing any input those gates deny today, for every stow consumer.

**Givens:**
- G1. Git decides which hook event fires for which operation, and that `--no-verify`, `-c`/env config, and `core.hooksPath` can override hooks. Reason: the git project owns that behavior (githooks(5), git-config(1)), and this plan cannot change it.
- G2. A PreToolUse hook sees only the Bash command text, not the processes that command spawns. A Bash call's environment comes from the harness, and `CLAUDE_ENV_FILE` is the documented injection point. Reason: Anthropic owns the harness.
- G4. Consumer repos may carry their own hook managers: husky, the pre-commit framework, git-lfs, lefthook. Reason: other parties own those repos.

Two conditions the analysis holds fixed are in this repo's own reach, so they sit in **Out of scope** rather than here: plugin standalone-ness (former G3) and the threat-model tier policy (former G5). That section states what the design becomes without each.

**Mechanisms:**
- M1 (chosen). Keep the current PreToolUse commit gates unchanged under the existing freeze. Their documented residuals stay accepted debt, and no git-native layer is added. — anchors: root, row13, row17, row18, row19, row25, row32
  - Over-powered check: M1 adds no mechanism, so it is the lightest candidate. Every alternative below is heavier.
- M2 (recorded target). An env-scoped hooks dir, supplied as `GIT_CONFIG_COUNT/KEY/VALUE` by a SessionStart hook that writes `CLAUDE_ENV_FILE`. — anchors: row5, row7, row8, row10, row11
  - Lighter primitive (a): installing files into `.git/hooks`. Fails: it is per-clone, it collides with the files git-lfs and the pre-commit framework write to the same paths, and any `core.hooksPath` shadows it (row 22).
  - Lighter primitive (b): a repo-local `core.hooksPath`. Fails: it reaches only repos where it was installed, which narrows the PII gate's every-repo scope, and its single value collides with husky's (row 11).
  - Lighter primitive (c): a settings.json `env` key. It is declarative and needs no hook, but its reach is wider. It reaches Claude's own git calls, hook subprocesses, and possibly the `!` shell, which would gate the engineer's escape hatch (row 9).
  - Heavier, rejected: a global `core.hooksPath`. It gates every human and IDE commit on the machine, shadows `.git/hooks`, lets husky repos escape (row 22), and requires editing every stow consumer's `~/.gitconfig`.
- M3 (recorded target). A dispatcher with a stub for every githooks(5) event, chaining to the repo's own hook dir. — anchors: row5
  - Lighter: wire only the gate events. Fails: the env value shadows every event, so any missing stub silently drops the repo's own `pre-push` or `post-checkout`.
- M4 (recorded target). Place each gate on a specific event: marker gates on `pre-commit`/`pre-merge-commit`, content scanners on `prepare-commit-msg`. — anchors: row1, row2, row3, row4, row15, row21
- M5 (recorded target). A residual PreToolUse deny for hook bypasses: a substring token set plus an argument walk for `-n`. — anchors: row6, row14, row15, row19
  - Lighter: substring matching only. Fails on `git commit -n` (row 19).
- M6 (rejected backstop). Abort ref updates from `reference-transaction` in its prepared state. — anchors: row16, row30
  - Lighter primitive (a): a `pre-push` scan of the outgoing range. It covers every commit path at the real publication boundary and fires once per push rather than on every ref update (fetch, reset, branch, stash).
  - Lighter primitive (b): the status quo, plus the documented acceptance of the baseline bypass (row 16).

**Rows:**
1. git 2.43 fires these hooks per operation:
   - `pre-commit`: plain commit; amend; a conflicted merge concluded by `commit` or by `merge --continue`; a single-commit `cherry-pick --continue`; a commit in a linked worktree, with cwd at that worktree's root.
   - `pre-merge-commit`: a clean `merge --no-ff`.
   - `prepare-commit-msg` and `post-commit` but not `pre-commit`: rebase picks (clean or after `--continue`), clean cherry-pick, clean revert.
   - `applypatch-msg`, `pre-applypatch`, and `post-applypatch` only: `am`.
   - None of the nine instrumented hooks: `stash`. The uninstrumented `reference-transaction` may have fired, because githooks(5) says it runs for any reference update.

   `[verified: the probe output's per-operation cases, as tabulated in the decision doc. Relayed by the research subagent and not checked here: the git sequencer.c pointers to try_to_commit]` The rebase-pick rows are observed behavior. git-rebase(1) calls the neighboring `post-commit` and `post-checkout` calls an accident of implementation and does not mention `prepare-commit-msg`.
2. `--no-verify` suppresses `pre-commit` and `commit-msg` on commit, and `pre-merge-commit` and `commit-msg` on merge. It does not suppress `prepare-commit-msg`. `[verified: the probe output's commit --no-verify and merge --no-verify cases]`
3. A non-zero exit from `prepare-commit-msg` aborts the commit, including inside a sequencer pick, and `git diff --cached` at that point shows the pick's content. `[unverified]` (githooks(5) prepare-commit-msg text; no abort probe was run)
4. A multi-commit `cherry-pick` or `revert --continue` (with a sequencer todo present) skips `pre-commit`. A single-commit `revert --continue` behaves like `cherry-pick --continue`. `[unverified]` (inferred from git's sequencer.c per research subagent, relayed and not checked here; the probe covered only a single-commit cherry-pick)
5. An env-supplied `core.hooksPath` beats a repo-local one, so the repo's own hooks do not run unless the dispatcher chains them. With the env unset, the repo-local hooks run. `[verified: the probe output's env-over-repo-local case and its env-unset case]`
6. `git -c core.hooksPath=/dev/null commit` fires no hooks. `[verified: the probe output's `-c core.hooksPath=/dev/null` case]`
    - `GIT_CONFIG_PARAMETERS`, a further `GIT_CONFIG_COUNT/KEY/VALUE` pair, and deleting a hook file are further ways to override an env-scoped hooksPath. `[unverified: git docs per research subagent, not probed]`
    - Making a hook non-executable with `chmod -x` or by renaming it is another such override (githooks(5) documents a non-executable hook as ignored). `[unverified: git docs per research subagent, not probed]`
    - What git does with a nonexistent `core.hooksPath` directory is also unprobed.
    - `GIT_CONFIG_GLOBAL` cannot override it, because command-line scope outranks global (git-config(1) precedence; not probed separately).
    - The probes mask global config with `GIT_CONFIG_GLOBAL=/dev/null`, and env hooks still fire in the plain-commit case. That does not test the precedence above.
7. The caller's environment, including `CLAUDECODE=1`, reaches a hook run from a Claude Bash call, and `marker.sh resolve-session-id` resolves the same session id inside the hook. `[verified: the probe output's environment and session-id lines from the Claude Bash tool run]`
    - In the engineer's `!` run, `CLAUDECODE` was unset and no session id resolved. `[observed, not retained: that run's output, read this session before the session's own re-run overwrote the file]`
    - Whether an env-scoped carrier leaves the `!` shell ungated depends on row 8.
    - Neither run shows it, because the probe sets the carrier itself.
8. `export` lines that a SessionStart hook writes to `CLAUDE_ENV_FILE` reach every later Bash tool call and do not reach the `!` shell. `[unverified]` (Claude Code hooks docs via summarizer; `docs/design-decisions/sentinel-config-consolidation.md` describes it in its paragraph beginning "Per-call resolution was kept rather than cached once per session"; not probed)
9. A settings.json `env` key reaches the Bash tool and also the Claude process's own git calls and hook subprocesses, and it may reach the `!` shell. `[unverified]`
10. About 35 test files in this repo run `git commit` as a subprocess that inherits the environment (for example, the `git_repo` fixture in `claude/.claude/hooks/tests/conftest.py` passes no `env=`). An env-scoped hooks dir would therefore fire the moved gates inside local pytest runs launched from Claude. `[approximate count, from ripgrep over **/test_*.py before the latest rebase and not re-derived; the fixture's missing env= is confirmed at the current tip]` The claim that the gates would actually fire inherits row 8's `[unverified]`.
11. `core.hooksPath` holds a single value, and the last command-line-scope entry wins. `[verified: the probe output's case where `-c` beats the env pair. Config precedence is relayed by the research subagent and not checked here]` The consequence, that stow and each plugin cannot each supply their own hooks and a plugin-only install has no dispatcher, holds only while row 31 holds.
12. `require-skill-review.sh`, `require-plugin-version-bump.sh`, and `require-npm-version-bump.sh` ship as marketplace plugins, and the skill-management plugin's lib carries its own copy of the commit-shape predicate. `[verified: docs/design-decisions/skill-review-gate-disarms-on-empty-base-relative-diff.md § "Known gap: the plugin matcher", which says the predicate is duplicated into the plugin lib (the skill-management plugin only). Relayed by the inventory subagent and not checked here: that each of the three gates ships as its own marketplace plugin, and that the two semver gates detect commits with their own narrow matcher rather than the shared predicate]`
13. `deny-invisible-commit-content.sh` exists to keep every other commit gate's PreToolUse `git diff --cached` snapshot accurate. In stow-plus-plugin installs, that includes the plugin skill-review gate and both semver gates (the `CHANGED_FILES` assignment in each reads `git diff --cached`). It calls the shared `_lib_command_concludes_commit`. `[verified: the header of deny-invisible-commit-content.sh; docs/hooks.md § "Gate hooks", which names the semver gates; skill-review-gate-disarms-on-empty-base-relative-diff.md § "Known gap: the plugin matcher"; rebase-continue-marker-gate-carveout.md § "Why `deny-invisible-commit-content.sh` takes the broad predicate"]`
14. Under the regression-only rule, any input denied on the merge-base and allowed after a change is a regression that must be fixed or put to the engineer. `[verified: docs/hooks.md § "Threat-model tiers", the regression-only rule]`
15. Today's gated set:
    - `git commit` in any flag form, including `-n`/`--no-verify` and `-c`/`-C` prefixes.
    - `merge`/`cherry-pick`/`revert --continue`, for all seven stowed gates.
    - `rebase --continue`, for the six stowed gates that aren't marker gates: the five that read `git diff --cached`, plus `deny-invisible-commit-content.sh` (`rebase-continue-marker-gate-carveout.md` § "Why `deny-invisible-commit-content.sh` takes the broad predicate").

    The PreToolUse layer is unaffected by `--no-verify` by construction. `[verified: the commit-detection preamble of require-code-review.sh; rebase-continue-marker-gate-carveout.md § "Why `git rebase --continue` is not gated by the marker gates"; the header of deny-pii-in-commits.sh; the _LIB_CONTINUE_VERBS_* definitions in _lib.sh]`
16. Clean `merge`/`cherry-pick`/`revert` and `git am` are ungated today. Plumbing plus a clean merge is the baseline bypass the anchor-admissibility test rests on. `[verified: skill-review-gate-disarms-on-empty-base-relative-diff.md § "Known gap: the ungated clean merge"; rebase-continue-marker-gate-carveout.md § "The anchor-admissibility test"; the _LIB_CONTINUE_VERBS_ALL definition in _lib.sh]` The tag covers the clean merge only. Whether a fast-forward merge fires any hook, and whether the bypass fires no hook under the recorded target, were not probed.
17. Magnitude. The figures are approximate, counted with `wc -l` on the measurement date stated in the decision doc's Sources line, and the verdict does not rest on them. `docs/design-decisions/git-native-commit-hooks-declined.md` § "What would and would not retire" is the single home for the magnitude statement. This plan restates none of it.
18. These stay regardless:
    - the novel-content base (`_lib.sh`, from `_lib_git_inprogress_state` through `_lib_code_review_marker_value`);
    - the marker machinery;
    - the shared splitter, which most of its callers keep using;
    - the skill-management plugin lib's commit-shape closure (`plugins/skill-management/hooks/_lib.sh`);
    - `enforce-marker-script-shape.sh`, `require-ready-for-review.sh`, `require-worktree-for-git-writes.sh`, `advance-past-commit-stall.sh`.

    `[verified: the _lib.sh function names, read this session. That the plugin lib's closure matches the stowed one is relayed by the inventory subagent and not checked here]`
19. `git commit -n` is the short form of `--no-verify`, so a residual deny that matches only the long form lets it past a gate placed on `pre-commit`. `[verified: git-commit(1) 2.43, "When any of --no-verify or -n is given, these are bypassed"]`
20. Inside `pre-commit` and inside `prepare-commit-msg`, during `commit -a` or a pathspec commit, `git diff --cached` reflects the content being committed, through the index that `GIT_INDEX_FILE` names. `[unverified]`
    - Whether that index is a temporary one for `commit -a`, for the pathspec form, or for both is not established.
    - Neither githooks(5) nor git-commit(1) documents `GIT_INDEX_FILE` for commit hooks.
    - The premise covers both placements.
    - The content scanners sit on `prepare-commit-msg`, so the PII gate's `-a`/pathspec arm retires only if it holds there too.
21. At `pre-merge-commit` for a clean merge, the merge state `_lib_gate_diff_base` reads (`MERGE_HEAD`) is present, so the gate can isolate the novel content. `[unverified]`
22. A repo-local `core.hooksPath` outranks a global one, so husky repos escape a global setting. Any `core.hooksPath` replaces `$GIT_DIR/hooks`. `[unverified: documented git behavior per research subagent; the probe masked global config and installed no .git/hooks script, so it tested only env-over-local]`
23. Commit detection runs on every Bash call. For the plugin hook it costs 17–23ms in typical cases and up to 1.5s on a 50 KB heredoc dense with git commands. GH-1180 tracks a lighter fix that doesn't depend on git hooks. `[verified: skill-review-gate-disarms-on-empty-base-relative-diff.md § "Performance", which reports those figures]`
24. Nothing installs git hooks today. `docs/security-hardening.md` § "Limitations" names git-native pre-commit as out of scope for claude-config, and `docs/hooks.md` § "Threat-model tiers" says "This repo does not ship one". `[verified: docs/security-hardening.md § "Limitations"; docs/hooks.md § "Threat-model tiers"]`
25. Standing constraint: "We shouldn't invest any more time into got commit related hooks. The machinery is already quite intricate." `[verified: session handoff relaying a prior-session quote]` These are not this session's words. Row 33 records the engineer confirming the quote as accurate.
26. The engineer asked the spike to compare who gets gated. `[engineer-verified: "Let the spike compare both"]`
27. The two options compared are "Claude-sessions-only via env-scoped hooksPath" and "all commits via repo-local core.hooksPath". `[unverified]` These are the session's option descriptions, not the engineer's words.
28. The deliverable is a plan file that is not committed. `[engineer-verified: "Uncommitted plan file (Recommended)"]` Superseded by rows 34 and 35, which carry the engineer's later words.
29. The engineer chose to probe before designing. `[engineer-verified: "Probe first (Recommended)"]` The tag covers the selection only. That the probes in fact ran before the design is the session's observation, not the engineer's words.
30. A non-zero `reference-transaction` exit in the `prepared` state aborts the ref update, and `--no-verify` does not suppress that hook. `[unverified]` (githooks(5); not probed)
31. git 2.43 has no way for more than one provider to attach to the same hook event without a shared dispatcher. `[unverified]` Newer git may add config-defined hooks, believed to be 2.54 (platform reviewer, unverified). That would dissolve the dispatcher and plugin blockers at that floor.
32. Closing accidental-leak coverage is not a goal in its own right for this decision. `[engineer-verified: "No, keep the freeze (Recommended)"]` The option's description ("no new work… records the git-hook design and four reopen triggers for later") was the session's wording, not the engineer's.
33. The two relayed quotes (the Ask line's quote with "to do that spike", and row 25's constraint) accurately state what the engineer asked for and decided. `[engineer-verified: "Yes, both accurate"]`
34. The no-go is recorded as a design-decision doc. `[engineer-verified: "This belongs in a decision doc."]`
35. The plan ships in the same PR as the doc. `[engineer-verified: "I meant plan and doc because that’s the convention"]`

## Critical files

- `docs/design-decisions/git-native-commit-hooks-declined.md` (new). This is the durable record of the no-go.
  - It follows `.claude/rules/design-decisions.md`: an H1 title, then the italic provenance line `*2026-10-03.*` with no `Formerly §N` clause. There is no index to update.
  - Its shape follows the sibling declined decision `docs/design-decisions/bats-core-adoption-declined.md`. In order:
    - the question and the verdict;
    - what would retire and what would not;
    - the hook-coverage facts observed on git 2.43.0;
    - the costs a replacement adds;
    - the threat-model interaction;
    - why both "who's gated" options fail;
    - reconsideration triggers;
    - sources.
  - It carries no plan-defined labels (`M2`, `row N`, `trigger 1`) and no `/tmp` paths, per the global `CLAUDE.md` § "Durable text".
  - It cites `docs/hooks.md` § "Threat-model tiers" for the tier rules rather than restating them.
- `.claude/plans/git-pre-commit-hooks-spike.md`, this plan. It ships in the same PR as the doc's provenance (row 35).
  - The decision doc is the canonical home for the rationale.
  - This plan is the record of how the decision was reached, frozen at approval and not updated after merge. A later change to the decision edits the doc, not this plan.

The dispatch is a single `code-writer` dispatch for the doc. `/tmp/git-hooks-spike-probe.sh` and `/tmp/git-hooks-spike-probe.out` remain session evidence and are not committed.

## Verification

- For the doc, run `.venv/bin/python3 claude/.claude/scripts/select-tests.py` from the worktree, using the `../../../.venv` path. All selected tests pass.
- Every number and cause-and-effect claim in the doc traces to a source the doc names:
  - git documentation or the git 2.31.0 release notes;
  - a repo file, cited by symbol, quoted text, or heading (the doc carries no line-number cites);
  - the hook-coverage observation, stated with its git version and date.
- What `/plan-review` should check:
  - **The verdict rests on rows 11, 12, 13, 14, 15, and 18. The tags on rows 11, 12, and 18 name relayed parts that were not checked here.** Row 11's mechanics are verified, but its plugin-blocker consequence depends on `[unverified]` row 31. If row 31 fails at a future git floor, the plugin blocker dissolves. The retained machinery (row 18) and the costs listed under "Further costs found in plan review" still stand. Rows 5, 10, and 22 add cost on top.
  - **The `[unverified]` rows 3, 4, 20, and 21 only change how bad the go case looks.** Rows 8 and 9 are premises of regression parity, not cost estimates. If the carrier doesn't reach a session, the moved gates fail open (see "Further costs found in plan review"). Either way the no-go holds, because none of these rows touches the retained machinery.
  - **Figures are approximate.** They were counted with `wc -l` on the measurement date stated in the decision doc's Sources line, and the verdict does not rest on them. The doc cites predicate and test blocks by name. The "about 35 test files" figure in row 10 is labeled as not re-derived.
  - **`[engineer-verified]` tags cover rows 26, 28, 29, 32, 33, 34, and 35.** Rows 26, 28, 29, and 32 quote labels the engineer selected. Rows 34 and 35 quote text the engineer typed. Row 33 quotes the engineer's reply to the quote-accuracy question, and this plan does not record whether that reply was a selected label or typed text. The option descriptions stay `[unverified]` (row 27).
  - **The relayed constraint (row 25) is not tagged as this session's words.**
- Closing the `[unverified]` rows is reopen work.

## Out of scope

- Implementing any part of the recorded target. This is a spike, and the recommendation is no-go.
- The per-Bash-call parser cost tracked by GH-1180 (row 23). It has its own lighter fix that doesn't depend on this decision.
- Content scanning of human and IDE commits through a repo-local hooks dir, and a `pre-push` content scan. Both are new gate investment beyond the Ask's complexity question. They are recorded as reopen triggers 1 and 2.
- Server-side branch protection and `pre-receive` hooks. Branch protection is a per-repo hosting setting that these hooks cannot verify (`rebase-continue-marker-gate-carveout.md` § "Direct push to the default branch"). `docs/security-hardening.md` § "Limitations" covers `pre-receive`.
- Closing the residuals of the current gates, given the standing freeze (row 25). One residual is documented only for the marker gates, not in the two `irreversible` scanners' own Known-gaps sections.
  - The scanners don't fire on clean merge, cherry-pick, revert, rebase picks, or `am`, per rows 15 and 16.
  - The Known-gaps lists in the headers of `deny-pii-in-commits.sh` and `deny-private-project-refs.sh` name none of these.
  - `docs/hooks.md` § "Threat-model tiers" says accepted debt is recorded on the gate header or a tracking issue.
  - Adding a one-line Known-gaps entry to each scanner is a doc edit outside this spike's Ask, so it is raised to the engineer rather than planned here.
- Letting marketplace plugins depend on the stow tree (former G3).
  - Plugins standing alone is this repo's own distribution model (`rebase-continue-marker-gate-carveout.md` § "The version-bump gates keep their narrow matcher", which calls the duplication the price of a plugin installable without this repo; `README.md` § "Plugins (marketplace)"). This plan could change it but declines to.
  - Without it, a plugin hook could call the stow dispatcher when one is present. That dissolves the plugin blocker (row 11) only for stow-plus-plugin installs.
  - Plugin-only installs still have no dispatcher, so each plugin gate would carry two code paths.
  - The verdict is unchanged, because best-case retirement stays bounded by row 18.
- Relaxing the threat-model tier policy in `docs/hooks.md` (former G5): the cooperative default, no relaxing `irreversible` gates, the regression-only rule, and the dependency invariant.
  - Changing that policy is a repo-wide decision beyond this spike's Ask.
  - Without the regression-only rule, the residual deny could drop the `-n` walk.
  - That would newly let `git commit -n` past the marker gates, an enforcement-invariant loosening that is the engineer's call.
  - It still retires none of the row 18 machinery, so the verdict is unchanged.
- Re-running the anchor-admissibility analysis.
  - This decision does not change what its baseline bypass rests on, which is a clean merge that is ungated today (row 16).
  - Whether a fast-forward variant fires any hook is not probed.
