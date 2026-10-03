# Spike: git-native client hooks as a replacement for PreToolUse commit gates

## Context

Goal: decide go/no-go on replacing claude-config's PreToolUse commit-text-matching gates with git-native client hooks (`pre-commit` / `pre-merge-commit` / `commit-msg` / `prepare-commit-msg`), and, if go, what the design keeps, deletes, and costs.

Ask: "I'm actually wondering if investing in pre-commit hooks would have eliminated a lot of the complexity" (the engineer's words in the prior session, relayed by the session handoff file), followed by the request "to do that spike". In this session the engineer selected "Let the spike compare both", "Probe first (Recommended)", and "Uncommitted plan file (Recommended)" for the clarifying questions, and "No, keep the freeze (Recommended)" for the coverage-goal question. After reading the no-go, the engineer widened the deliverable: "This belongs in a decision doc." On whether the plan ships too: "I meant plan and doc because that’s the convention".

Why now: the merge-aware review-gates work (PR #1215, merged) added more command-shape parsing on top of an already intricate gate layer. That made the engineer question the foundation. A standing constraint from the prior session (relayed by the handoff, not this session's words): "We shouldn't invest any more time into got commit related hooks. The machinery is already quite intricate." The outcome is a reviewed go/no-go recommendation, not an implementation. A no-go is a valid outcome.

Evidence inputs:

- Three research subagents (git source and docs, session-state reach, a measured inventory of the gate surface).
- An empirical probe on git 2.43.0: `/tmp/git-hooks-spike-probe.sh`, output `/tmp/git-hooks-spike-probe.out`, run once by the engineer via `!` and once from a Claude Bash tool call.

## Approach

**Recommendation: no-go — keep the freeze.** A git-native replacement retires only the command-shape layer (about 625 lines of gate code and 1.7k–2.3k lines of tests at best, much less in the realistic case). It keeps the novel-content base, the marker machinery, and the plugin matcher copy, and it adds a dispatcher that every repo Claude touches would depend on. The design below is recorded as the target to reopen from if one of the named triggers fires. It is not pending work.

### Why it doesn't pay: net-complexity verdict

- **What retires, best case (rows 17, 18).**
  - Code:
    - `deny-invisible-commit-content.sh` (434 lines).
    - The `_lib.sh` commit-shape predicates (~191–203 lines with comments; row 17): `_lib_commit_fragment_has_worktree_target`, `_lib_fragment_concludes_commit_shape`, `_lib_command_concludes_commit_shape`, `_lib_command_concludes_commit`, `_lib_command_concludes_marker_gated_commit`, `_lib_fragment_concludes_commit`, and the `_LIB_CONTINUE_VERBS_*` grammar (`_lib.sh:1569-1570`).
    - Each moved gate's commit-detection preamble.
    - `deny-pii-in-commits.sh`'s `-a`/pathspec `git diff HEAD` arm and its `-F` argument parsing, because a hook reads the final index and the message file directly.
    - All of that is measured against ~3.7k lines of gate code: 2,610 in the seven stowed gates and 1,104 in the three plugin gates.
  - Tests: `test_deny_invisible_commit_content.py` (1,151) and `test_lib.py:6186-6749` (564), plus possibly `test_parse_git_command.py` (342) and `test_hook_command_normalization.py` (219). The other ~19k gate-test lines would move to a real-commit harness rather than be deleted.
- **What does not retire.**
  - `_lib_gate_diff_base` and the rest of the novel-content base (`_lib.sh:972-1257`).
  - The marker hash machinery.
  - The anchor-admissibility analysis. Its baseline bypass (plumbing plus a fast-forward merge) fires no hook under either design (row 16). A clean non-fast-forward merge does fire `pre-merge-commit` (row 1), but the fast-forward variant stays available.
  - The shared fragment splitter, which eight other hooks call.
  - The skill-management plugin lib's commit-shape and splitter closure (`plugins/skill-management/hooks/_lib.sh:621-843`, about 145 non-comment lines) (rows 11, 12).
  - Looking back, a git hook would have avoided the `--continue` arming predicates from the merge-aware work. The `--continue` grammar also lives in `enforce-marker-script-shape.sh:659` and in the plugin's `_lib_chains_marker_write_before_commit`, which stay. It would not have avoided the novel-content base, because a hook still has to decide what to hash mid-merge.
- **The best case is not the realistic case.**
  - `deny-invisible-commit-content.sh` can retire only once every gate that reads the PreToolUse `git diff --cached` snapshot has moved. In stow-plus-plugin installs that set includes the `skill-management` plugin's `require-skill-review.sh` (row 13).
  - No mechanism lets a plugin attach to the single env-supplied hooks dir, and a plugin-only install has no dispatcher at all (row 11).
  - If the plugin stays on PreToolUse, `deny-invisible-commit-content.sh` stays. So do the shared predicates it calls (`_lib_command_concludes_commit`, per `docs/design-decisions/rebase-continue-marker-gate-carveout.md:39`).
  - Realistic retirement then shrinks to the per-gate preambles and the PII gate's `-a`/pathspec and `-F` arms.
- **What gets added.** This spike did not estimate the size of any of these:
  - A dispatcher with a pass-through stub for every githooks(5) event, plus chaining logic (row 5).
  - A SessionStart hook that writes `CLAUDE_ENV_FILE` (row 8).
  - A residual PreToolUse bypass gate that still needs a walk over the commit's own arguments (row 19).
  - An environment scrub across this repo's test roots (row 10).
  - New docs for each of the above.
- **Further costs found in plan review.** Each applies only to the recorded target, and each strengthens the no-go:
  - **Carrier-absent fail-open.** The moved gates fire only if the `GIT_CONFIG_*` carrier reaches the Bash call. Git treats a missing hooks dir or hook as "no hook" and proceeds. So each of the following silently turns every moved gate off, including both `irreversible` scanners, with the PreToolUse predicates already deleted:
    - git older than 2.31, which ignores `GIT_CONFIG_COUNT` (git 2.31.0 release notes). The README states no git floor.
    - A SessionStart writer that failed or didn't run.
    - Subagent or headless sessions that don't source `CLAUDE_ENV_FILE`, since row 8 is unverified.
    - A dispatcher dir that stow hasn't linked yet after `git pull` (`require-code-review.sh:60`).

    `docs/hooks.md:26` counts a new fail-open path as a regression. A PreToolUse fail-closed check against this would need the commit-shape predicate the replacement deletes.
  - **Residual-deny completeness.** Today's gates scan `sudo git commit …` (`_lib.sh:1289`). `sudo` and other launchers that reset the environment strip the carrier, and they form an open class that no token list can enumerate (`su -`, `nix-shell --pure`, `docker run`). The `-n` walk inherits `_lib_commit_fragment_has_worktree_target`'s documented mis-parse of attached short-flag values (`_lib.sh:1409`; `deny-pii-in-commits.sh:207-211`).
  - **Residual-deny scope and tier.** Unscoped, the token set over-denies ordinary shapes such as husky's `git config core.hooksPath .husky` and a consumer's own `GIT_CONFIG_COUNT` use. Scoping it to commits needs the commit-shape predicate. As the evasion backstop for two `irreversible` scanners, it would inherit `irreversible` (`docs/hooks.md:22`).
  - **Carrier writer.** `GIT_CONFIG_COUNT` is a shared, count-indexed namespace. A static write clobbers inherited pairs (`safe.directory`, `url.*.insteadOf`). An appending write must stay idempotent across SessionStart re-fires and per-call re-sourcing.
  - **Installer collisions.** Inside a Claude session, `git config core.hooksPath` returns the dispatcher dir. Then:
    - `pre-commit install` refuses when `core.hooksPath` is set (from memory, unprobed).
    - `git lfs install` may write into the dispatcher dir. Under the stow tree, that writes through symlinks into tracked files.
    - Husky's local setting is shadowed for the whole session.

    The dispatcher also cannot live in `claude/.claude/hooks/`, because `test_hook_alignment.py:85-102` sweeps every file there as a hook.
  - **Dispatcher posture and latency.** The design needs four things it doesn't yet specify:
    - exit-code propagation from the chained hook;
    - a fail posture per event class;
    - a log line when a chain is skipped;
    - a recovery route that doesn't assume row 8.

    Stubs for every event add a shell spawn on routine git operations in every repo. That includes `reference-transaction` on every ref update, against this repo's own under-100 ms budget (`skill-review-gate-disarms-on-empty-base-relative-diff.md:159-174`).
  - **Mid-operation denial.** A PreToolUse deny happens before git state changes. A failing `prepare-commit-msg` inside a rebase or cherry-pick pick leaves the operation stopped mid-sequence, which a cooperative agent then has to recover.
  - **Consumer test suites.** The row 10 collision is cheap to scrub in this repo: `conftest.py:366-375` already has an autouse fixture that scrubs a leaking variable, and there are four conftest roots. It cannot be scrubbed in consumer repos' suites or release tooling, so the second net-loss condition below effectively holds for the every-stow-consumer scope.
- **Verdict.** The replacement swaps an open-ended layer that guesses from command text for a closed-form integration layer. That is a sounder foundation for "what is being committed," but it is not a smaller one, and the realistic slice adds net code.

### Who's gated: the two options compared (rows 26, 27)

- **Claude sessions only, via an env-scoped `core.hooksPath` set through `CLAUDE_ENV_FILE`.** This is the only viable replacement mechanism.
  - It fires in whatever repo a Claude Bash call commits in. That matches today's scope (`deny-pii-in-commits.sh:12-15` fires on every repo).
  - It leaves the `!` shell and human terminals ungated (row 7), which preserves the engineer's escape hatch.
  - Cost: it shadows every repo's own hooks (husky, pre-commit framework, git-lfs), so the dispatcher must chain every event (row 5).
  - Cost: it fires inside local test suites and tooling that commit in subprocesses (row 10).
  - Cost: plugins have no way to register with it (row 11).
- **All commits, via a repo-local `core.hooksPath`.** Rejected as a replacement.
  - It reaches only repos where someone ran the install. Every stow consumer's other repos lose coverage they have today, and so do `git -C <other-repo>` commits.
  - Gating human commits on `/code-review` markers is a category error. The workflow gates would have to branch on `CLAUDECODE` anyway (row 7), which collapses back into the first option's behavior.
  - `core.hooksPath` holds one value, so this can't coexist with a consumer repo's own husky setting.
  - An env-scoped layer shadows it whenever both are active (row 5).
  - Its one real benefit is content scanning of human and IDE commits in this repo. That would be an additive per-repo feature, not a replacement, and `docs/security-hardening.md:625-629` already names it out of scope.

### Per-gate disposition (recorded target, only if reopened)

- **`require-code-review.sh`** moves to `pre-commit` + `pre-merge-commit`.
  - It keeps `_lib_gate_diff_base`.
  - It drops the commit-shape preamble and the in-chain `marker.sh write && commit` shortcut (`_lib_chains_marker_write_before_commit`), because the marker already exists by the time the hook runs.
  - Wire the clean-merge arm only if row 21 holds. Otherwise it would newly over-deny an ordinary `git merge origin/<default>`, which is ungated today.
- **`check-skill-length.sh`, `check-claude-md-length.sh`, `guard-settings-session-keys.sh`:** placement is unresolved, because no git event matches today's gated set (rows 1, 15).
  - `pre-commit` misses `rebase --continue`, which these gates cover today. That is a regression under row 14.
  - `prepare-commit-msg` fires on every clean rebase, cherry-pick, and revert pick (row 1), so a length ratchet could over-deny an ordinary rebase.
- **`deny-pii-in-commits.sh`** moves to `prepare-commit-msg`, reading the message from `$1`.
  - That hook survives `--no-verify` (row 2) and covers sequencer picks, provided row 3 holds.
  - If row 3 fails, the gate needs a residual PreToolUse `rebase --continue` arm, which keeps the `--continue` grammar alive.
- **`deny-private-project-refs.sh`** splits. The commit arm moves the same way as the PII gate. The `gh pr`/`gh issue`/`gh api` body arm (`:249-334`) stays on PreToolUse.
- **`deny-invisible-commit-content.sh`** is deleted, but only after every snapshot reader has moved, including the plugin skill-review gate (row 13).
- **The plugin gates** (`require-skill-review.sh`, `require-plugin-version-bump.sh`, `require-npm-version-bump.sh`) stay on PreToolUse (rows 11, 12). The skill-management plugin lib becomes the only home of the commit-shape predicate, and the predicate-parity coverage in `test_require_skill_review.py` is the deletion candidate. The byte-equality test at `test_lib.py:8116` stays, because it pins the gate-base and marker-recipe closure (`_SHARED_CLOSURE_FUNCTIONS`, `test_lib.py:8090-8100`), which both copies keep.
- **Unaffected:** `enforce-marker-script-shape.sh`, `require-ready-for-review.sh`, `require-worktree-for-git-writes.sh`, `advance-past-commit-stall.sh`.

### Residual PreToolUse deny, and whether it re-imports the parser

A replacement must still deny every input that the current gates deny on the merge-base (row 14), and today's PreToolUse layer stops every shape that disables hooks (row 15). The residual deny splits into two parts:

- **A substring part, lighter than today.**
  - It is a case-insensitive token match that ignores quoting and needs no fragment split.
  - Tokens:
    - the long form `--no-verify` and its unambiguous abbreviations;
    - `hookspath` (git config keys are case-insensitive);
    - `GIT_CONFIG_COUNT`, `GIT_CONFIG_KEY_`, `GIT_CONFIG_VALUE_`, `GIT_CONFIG_PARAMETERS`;
    - `env -i` and `--ignore-environment`;
    - `unset GIT_CONFIG`.
  - Accepted over-deny: a commit message or doc command that merely mentions a token. The recourse is `-F <file>`.
- **An argument part, where the parser survives.**
  - `git commit -n` (row 19) skips a marker gate placed on `pre-commit`.
  - Catching `-n` inside clustered short options (`-anm`) while skipping `-m` values means walking the commit subcommand's own arguments.
  - `_lib_commit_fragment_has_worktree_target` already does that walk, so it would be renamed, not deleted.
  - If row 3 fails, the `rebase --continue` arm survives too, and with it the `--continue` abbreviation grammar.

So yes, partially: the part that survives is the part the merge-aware work grew. A third surviving arm is the fail-closed check for an absent carrier, listed under "Further costs found in plan review".

The `-n` walk is a placement consequence, not a git limit. `prepare-commit-msg` survives `--no-verify` (row 2), so marker gates placed there would need no `-n` walk. The price is over-denying ordinary rebase picks unless the hook detects sequencer state. A reopen would put that trade to the engineer.

### Coverage deltas vs today (recorded target)

- **Newly caught.**
  - Every shape in "Known gap: the plugin matcher," for the stowed gates (`docs/design-decisions/skill-review-gate-disarms-on-empty-base-relative-diff.md:208-234`):
    - script files and aliases;
    - `$var`/`${IFS}` words;
    - `bash -c`/`eval`;
    - quoted or `$(...)` values for git's global flags;
    - `X=/a/git` prefixes.
  - Commits aimed elsewhere with `-C`, `--git-dir`, `cd`, or `GIT_*`. The hook runs in the committing repo's root (row 1, linked-worktree case).
  - `-am`/pathspec commits and staging done in the same call, provided row 20 holds.
  - If row 3 holds, scanners placed on `prepare-commit-msg` also catch `--no-verify` commits, rebase picks (including the `edit`-pause fold-in), and clean cherry-pick and revert.
  - A clean `git merge` reaches `pre-merge-commit`. Whether that counts as coverage or over-deny depends on row 21.
- **Still missed.**
  - `git am`. It fires no `prepare-commit-msg`, and `am --no-verify` suppresses `pre-applypatch`. It is ungated today as well, because `_LIB_CONTINUE_VERBS_ALL` omits it (row 16).
  - `git stash` fires no hook, but its content can only reach a branch through a later commit, which is gated.
  - `commit-tree` + `update-ref`/`branch` plumbing, and a fast-forward to such a commit.
  - Overrides of hooksPath or the environment, which the residual deny covers.
  - The marker gates on a multi-commit `cherry-pick`/`revert --continue`, if row 4 holds. That would be a regression against today.
  - `git rebase --apply` and the options that imply it, per git-rebase(1)'s behavioral-differences section (from memory, unprobed). The apply backend runs the `am` hooks, not `prepare-commit-msg`. `rebase --apply --continue` is gated today, so moving the scanners would regress it and keep the `rebase --continue` arm alive.
  - Message text edited after `prepare-commit-msg` (editor flow, `reword`, `squash`) on the `--no-verify` path, because `commit-msg` is suppressed. This is the same gap `deny-pii-in-commits.sh:103-105` documents today.
  - Scanner-logic gaps, which port unchanged: binary-classified files, added lines starting `++`, and lines with invalid encoding (`deny-pii-in-commits.sh:183-199`).
  - Committers that never run git hooks: libgit2-, dulwich- or JGit-based tools, and `git fast-import`. Likewise a script that itself runs `git -c core.hooksPath=… commit`, which neither layer sees.
- **Backstops considered.**
  - `post-commit` runs after the commit is already written. It could only enforce anything through an auto-reset, which is destructive, so it is rejected.
  - `reference-transaction` (row 30) is rejected as over-powered; see M6.
  - The candidate backstop for the two `irreversible` scanners is a `pre-push` scan of the outgoing commits. Their irreversibility comes from publication, not from the commit (`docs/hooks.md:15`), and a push-time scan sees `git push` publication regardless of which commit path produced the content. It does not see publication that bypasses `git push`, such as API or MCP content writes.
  - That scan is unsized and under-specified. It needs:
    - destination-remote scoping;
    - per-commit scanning rather than a net-range diff, so content added and then removed is still caught;
    - `git push --no-verify` coverage;
    - the same carrier and dispatcher as the full replacement.

    It should also be compared against a PreToolUse push gate and against platform-side push protection, which covers only the credential-value tier but needs no client mechanism. It is new gate investment, so it is a reopen design (trigger 1), not part of this decision.

### Chaining cost: husky, pre-commit framework, git-lfs

An env-supplied hooks dir replaces the repo's own hook dir for every event, not just the gated ones (row 5). The dispatcher therefore needs four things:

1. A stub for every githooks(5) event name.
2. A way to resolve the hook dir git would otherwise have used. It checks `core.hooksPath` at worktree, local, global, then system scope, and falls back to `$(git rev-parse --git-common-dir)/hooks`. Relative values resolve from the worktree root.
3. Loop prevention when that dir turns out to be the dispatcher's own.
4. stdin forwarding for `pre-push`, `post-rewrite`, and `reference-transaction`.

If an event is missed, nothing errors: the consumer repo's own hook just stops running inside Claude sessions. git-lfs's `pre-push` is the high-stakes case, because skipping it pushes LFS pointers without their objects. This obligation applies in every repo that every stow consumer opens, which means this repo would own a compatibility matrix across hook managers.

### Threat-model interaction

- Every moved gate is `cooperative`. `deny-pii-in-commits.sh`, `deny-private-project-refs.sh`, and `deny-invisible-commit-content.sh` also carry `irreversible`.
- The regression-only rule applies at every tier (row 14). No placement may newly allow `--no-verify`/`-n`, `-c core.hooksPath`, `env -i`, or any `--continue` form denied today.
- Inside a git hook, the gate reads git state rather than command text. That removes most of the surface where steered command text can evade detection. It is the replacement's strongest argument, but it earns no tier credit: the commit-boundary disclosure gates deliberately carry no `untrusted-input` tier (`docs/hooks.md:47`).
- The dependency invariant fixes the deletion order. `deny-invisible-commit-content.sh` is the snapshot backstop its dependents name. Deleting it before the plugin skill-review gate stops needing it would be a regression for that gate (row 13), and that needs the engineer's decision, not a waiver.

### Minimal first slice, and what would make it a net loss

No small slice retires any machinery. The smallest slice that does retire something has to include all of the following together:

- the dispatcher and its chaining;
- the `CLAUDE_ENV_FILE` hook;
- all seven stowed gates;
- the residual deny;
- the test-environment scrub;
- deleting `deny-invisible-commit-content.sh` and the stowed predicates.

It also needs one of these two:

- a way for plugins to register with the dispatcher, which doesn't exist today (row 11); or
- the engineer accepting that the skill-review gate falls back to its plugin-only behavior in stow-plus-plugin installs (row 13).

Any smaller slice adds net code, because every predicate stays. Moving only the PII gate to `prepare-commit-msg` is an example.

The retiring slice becomes a net loss if any of these happen:

- The chaining layer starts collecting per-tool special cases: husky v4 vs v9, lefthook, LFS, or worktree-scoped config.
- The test-fixture collision (row 10) needs a scoping heuristic instead of a simple scrub, such as skipping repos under `$TMPDIR`, repos with no remote, or repos other than the session's own.
- Row 3 turns out false, so the `--continue` grammar stays.
- The `-n` walk (row 19) is needed, which is the likely case.

### Reopen triggers

1. A decision that the scanners' coverage gaps (clean merge, cherry-pick, revert, rebase picks, `am`, script files) are unacceptable. No shipped control observes what the gates missed before publication (`docs/hooks.md:28`), so this is a decision-time trigger, not an observed-incident one. The first investment would then be the `pre-push` scan above, sized first, not the full replacement.
2. A decision that human and IDE commits in this repo need content scanning. That would be the repo-local option, built as an additive feature.
3. The git version this repo supports gains a way for more than one provider to register a hook (row 31). That removes the dispatcher and the plugin blocker. Newer git may already have config-defined hooks (`hook.<name>.command`; believed to be git 2.54, unverified). The README states no git floor today.
4. A commit gate needs yet another command-shape patch. Compare that patch against this target design before writing it.

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
   - Nothing: `stash`.

   `[verified: /tmp/git-hooks-spike-probe.out lines 8-91; git sequencer.c try_to_commit 1608/1730 per research subagent]`
2. `--no-verify` suppresses `pre-commit` and `commit-msg` on commit, and `pre-merge-commit` on merge. It does not suppress `prepare-commit-msg`. `[verified: /tmp/git-hooks-spike-probe.out lines 14-16, 33-34]`
3. A non-zero exit from `prepare-commit-msg` aborts the commit, including inside a sequencer pick, and `git diff --cached` at that point shows the pick's content. `[unverified]` (githooks(5) prepare-commit-msg text; no abort probe was run)
4. A multi-commit `cherry-pick` or `revert --continue` (with a sequencer todo present) skips `pre-commit`. A single-commit `revert --continue` behaves like `cherry-pick --continue`. `[unverified]` (inferred from sequencer.c 5362/5389/1155-1156 per research subagent; the probe covered only a single-commit cherry-pick)
5. An env-supplied `core.hooksPath` beats a repo-local one, so the repo's own hooks do not run unless the dispatcher chains them. With the env unset, the repo-local hooks run. `[verified: /tmp/git-hooks-spike-probe.out lines 93-104]`
6. `git -c core.hooksPath=/dev/null commit` fires no hooks. `[verified: /tmp/git-hooks-spike-probe.out lines 25-26]` `GIT_CONFIG_PARAMETERS`, a further `GIT_CONFIG_COUNT/KEY/VALUE` pair, and deleting a hook file are further ways to override an env-scoped hooksPath. `[unverified: git docs per research subagent, not probed]` `GIT_CONFIG_GLOBAL` cannot override it, because command-line scope outranks global. The probe masks global config and env hooks still fire (probe script line 31; out lines 8-12).
7. The caller's environment, including `CLAUDECODE=1`, reaches a hook run from a Claude Bash call, and `marker.sh resolve-session-id` resolves the same session id inside the hook. `[verified: /tmp/git-hooks-spike-probe.out lines 4-5, 9]` In the engineer's `!` run, `CLAUDECODE` was unset and no session id resolved. `[verified: that run's output, read this session before the session's own re-run overwrote the file; not in the retained artifact]` Whether an env-scoped carrier leaves the `!` shell ungated depends on row 8 and is not shown by either run, because the probe sets the carrier itself.
8. `export` lines that a SessionStart hook writes to `CLAUDE_ENV_FILE` reach every later Bash tool call and do not reach the `!` shell. `[unverified]` (Claude Code hooks docs via summarizer; `docs/design-decisions/sentinel-config-consolidation.md:13` describes it; not probed)
9. A settings.json `env` key reaches the Bash tool and also the Claude process's own git calls and hook subprocesses, and it may reach the `!` shell. `[unverified]`
10. 35 test files in this repo run `git commit` as a subprocess that inherits the environment (237 matches; for example, `claude/.claude/hooks/tests/conftest.py:401` passes no `env=`). An env-scoped hooks dir would therefore fire the moved gates inside local pytest runs launched from Claude. `[verified: ripgrep over **/test_*.py this session; conftest.py:401]` The claim that the gates would actually fire inherits row 8's `[unverified]`.
11. `core.hooksPath` holds a single value, and the last command-line-scope entry wins. `[verified: /tmp/git-hooks-spike-probe.out lines 25-26, where `-c` beats the env pair; config precedence per research subagent]` The consequence, that stow and each plugin cannot each supply their own hooks and a plugin-only install has no dispatcher, holds only while row 31 holds.
12. `require-skill-review.sh`, `require-plugin-version-bump.sh`, and `require-npm-version-bump.sh` ship as marketplace plugins, each with its own lib copy. `[verified: inventory subagent; docs/design-decisions/skill-review-gate-disarms-on-empty-base-relative-diff.md:208-218]`
13. `deny-invisible-commit-content.sh` exists to keep every other commit gate's PreToolUse `git diff --cached` snapshot accurate. In stow-plus-plugin installs, that includes the plugin skill-review gate and both semver gates (`require-plugin-version-bump.sh:138`, `require-npm-version-bump.sh:175`). It calls the shared `_lib_command_concludes_commit`. `[verified: deny-invisible-commit-content.sh:4-10; skill-review-gate-disarms-on-empty-base-relative-diff.md:218; rebase-continue-marker-gate-carveout.md:39-41]`
14. Under the regression-only rule, any input denied on the merge-base and allowed after a change is a regression that must be fixed or put to the engineer. `[verified: docs/hooks.md:26]`
15. Today's gated set:
    - `git commit` in any flag form, including `-n`/`--no-verify` and `-c`/`-C` prefixes.
    - `merge`/`cherry-pick`/`revert --continue`, for all seven stowed gates.
    - `rebase --continue`, for the six stowed gates that aren't marker gates: the five that read `git diff --cached`, plus `deny-invisible-commit-content.sh` (`rebase-continue-marker-gate-carveout.md:39-41`).

    The PreToolUse layer is unaffected by `--no-verify` by construction. `[verified: require-code-review.sh:71-89; rebase-continue-marker-gate-carveout.md:5; deny-pii-in-commits.sh:17-31; _lib.sh:1569-1570]`
16. Clean `merge`/`cherry-pick`/`revert` and `git am` are ungated today. Plumbing plus a clean or fast-forward merge is the baseline bypass the anchor-admissibility test rests on. `[verified: skill-review-gate-disarms-on-empty-base-relative-diff.md:236-240; rebase-continue-marker-gate-carveout.md:56-58; _lib.sh:1569]`
17. Line counts:
    - `deny-invisible-commit-content.sh`: 434.
    - `_lib.sh` commit-shape predicates: ~130 body lines. With comments, the inventory subagent counted ~191. The platform reviewer measured 203 (`_lib.sh:1374-1426` and `1564-1713`).
    - About 58 of the 564 `test_lib.py` lines (`TestSplitFragmentsPipefailContract` and a shared sha256 helper) do not retire.
    - `test_deny_invisible_commit_content.py`: 1,151.
    - `test_lib.py:6186-6749`: 564.
    - Possibly `test_parse_git_command.py` (342) and `test_hook_command_normalization.py` (219).
    - Stowed gate code: 2,610. Plugin gate code: 1,104. Gate tests: ~20,269.

    `[verified: inventory subagent, wc -l]`
18. These stay regardless:
    - the novel-content base (`_lib.sh:972-1257`, from `_lib_git_inprogress_state` through `_lib_code_review_marker_value`);
    - the marker machinery;
    - the shared splitter, which eight other hooks call;
    - the skill-management plugin lib's commit-shape closure (`_lib.sh:621-843`; the inventory subagent's "169 body lines" was not reproduced by review);
    - `enforce-marker-script-shape.sh`, `require-ready-for-review.sh`, `require-worktree-for-git-writes.sh`, `advance-past-commit-stall.sh`.

    `[verified: _lib.sh function index this session; inventory subagent]`
19. `git commit -n` is the short form of `--no-verify`, so a residual deny that matches only the long form lets it past a gate placed on `pre-commit`. `[unverified]` (git-commit(1); not probed)
20. Inside `pre-commit`, during `commit -a` or a pathspec commit, `git diff --cached` reflects the temporary index through the `GIT_INDEX_FILE` git exports. `[unverified]`
21. At `pre-merge-commit` for a clean merge, the merge state `_lib_gate_diff_base` reads (`MERGE_HEAD`) is present, so the gate can isolate the novel content. `[unverified]`
22. A repo-local `core.hooksPath` outranks a global one, so husky repos escape a global setting. Any `core.hooksPath` replaces `$GIT_DIR/hooks`. `[unverified: documented git behavior per research subagent; the probe masked global config and installed no .git/hooks script, so it tested only env-over-local, out lines 93-104]`
23. Commit detection runs on every Bash call. For the plugin hook it costs 17–23ms in typical cases and up to 1.5s on a 50 KB heredoc dense with git commands. GH-1180 tracks a lighter fix that doesn't depend on git hooks. `[verified: skill-review-gate-disarms-on-empty-base-relative-diff.md:153-206]`
24. Nothing installs git hooks today. `docs/security-hardening.md:625-629` names git-native pre-commit as out of scope for claude-config, and `docs/hooks.md:33` says "This repo does not ship one". `[verified: read this session]`
25. Standing constraint: "We shouldn't invest any more time into got commit related hooks. The machinery is already quite intricate." `[verified: session handoff relaying a prior-session quote]` These are not this session's words, so the row carries no engineer-verified override protection.
26. The engineer asked the spike to compare who gets gated. `[engineer-verified: "Let the spike compare both"]`
27. The two options compared are "Claude-sessions-only via env-scoped hooksPath" and "all commits via repo-local core.hooksPath". `[unverified]` These are the session's option descriptions, not the engineer's words.
28. The deliverable is a plan file that is not committed. `[engineer-verified: "Uncommitted plan file (Recommended)"]` Superseded by rows 34 and 35, which carry the engineer's later words.
29. Probes ran before the design. `[engineer-verified: "Probe first (Recommended)"]`
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

- For the doc, run `.venv/bin/python3 claude/.claude/scripts/select-tests.py` from the worktree, using the `../../../.venv` path. It should select `claude/.claude/hooks/tests/test_design_decision_files.py`, which checks the filename and provenance grammar, plus the citation-grammar tests in `claude-skills/skills/tests/test_skills.py`. All selected tests pass.
- Every number and cause-and-effect claim in the doc traces to a source the doc names:
  - git documentation or the git 2.31.0 release notes;
  - a repo `file:line`;
  - the hook-coverage observation, stated with its git version and date.
- What `/plan-review` should check:
  - **The verdict rests on rows 12, 13, 14, 15, 17, and 18, which are verified, plus row 11.** Row 11's mechanics are verified, but its plugin-blocker consequence depends on `[unverified]` row 31. If row 31 fails at a future git floor, the plugin blocker dissolves. The retained machinery (row 18) and the costs listed under "Further costs found in plan review" still stand. Rows 5, 10, and 22 add cost on top.
  - **The `[unverified]` rows 3, 4, 19, 20, and 21 only change how bad the go case looks.** Rows 8 and 9 are premises of regression parity, not cost estimates. If the carrier doesn't reach a session, the moved gates fail open (see "Further costs found in plan review"). Either way the no-go holds, because none of these rows touches the retained machinery.
  - **Additional probes, needed only on reopen.** Probe `git rebase --apply`, both clean and through `--continue` after a conflict. Probe the carrier writer with a pre-existing `GIT_CONFIG_*` pair across two SessionStart fires. Repeat the hook probe on the oldest git the README would support. For row 3, also record the repo state after the aborted pick.
  - **Every number traces to its cited source:** probe line ranges, `wc -l` counts from the inventory subagent, and doc line ranges.
  - **`[engineer-verified]` tags quote only the labels the engineer selected** (rows 26, 28, 29, 32). The option descriptions stay `[unverified]` (row 27).
  - **The relayed constraint (row 25) is not tagged as this session's words.**
- The open rows need closing only if a reopen trigger fires. Each probe below extends `/tmp/git-hooks-spike-probe.sh` and runs in scratch repos on git 2.43, except the last:
  - **Row 3.** Add a `prepare-commit-msg` that logs `git diff --cached --stat` and exits 1. Run it under a plain commit, `commit --no-verify`, a rebase pick, a clean cherry-pick, and a clean revert. Confirm each one aborts and shows the pick's content.
  - **Row 4.** Run a two-commit `cherry-pick` sequence and a two-commit `revert` sequence that conflict on the first pick, then `--continue`. Record whether `pre-commit` fires.
  - **Rows 19 and 20.** Run `git commit -n` and `git commit -am` against a logging `pre-commit` that records `$GIT_INDEX_FILE` and `git diff --cached --stat`.
  - **Row 21.** Run a clean `merge --no-ff` whose `pre-merge-commit` logs whether `MERGE_HEAD` exists.
  - **Chaining resolution.** With the env hooksPath set, check inside a hook that `git config --local --get core.hooksPath` returns the repo-local value. The platform reviewer already found that `git rev-parse --git-path hooks` honors `core.hooksPath`, resolves to the common-dir hooks in a linked worktree, and expands a `~/` prefix.
  - **Row 30.** Add a `reference-transaction` that exits 1 in the `prepared` state, and run it under `commit`, `update-ref`, and `merge --ff-only`.
  - **Rows 8 and 9 need a probe at the settings level.** Add a temporary SessionStart hook that writes one `export` to `CLAUDE_ENV_FILE`, then run `env` from a Bash call and from `!`. Because it edits settings, this belongs to implementation time.

## Out of scope

- Implementing any part of the recorded target design. This is a spike, and the recommendation is no-go.
- The per-Bash-call parser cost tracked by GH-1180 (row 23). It has its own lighter fix that doesn't depend on this decision.
- Content scanning of human and IDE commits through a repo-local hooks dir, and a `pre-push` content scan. Both are new gate investment beyond the Ask's complexity question. They are recorded as reopen triggers 1 and 2.
- Server-side `pre-receive` and branch protection. These are per-repo hosting settings that these hooks cannot verify (`rebase-continue-marker-gate-carveout.md:86`).
- Closing the residuals of the current gates, given the standing freeze (row 25). One residual is documented only for the marker gates, not in the two `irreversible` scanners' own Known-gaps sections: they don't fire on clean merge, cherry-pick, revert, rebase picks, or `am` (probe out lines 28-91; `deny-pii-in-commits.sh:93-219` and `deny-private-project-refs.sh:77-145` list none of these). `docs/hooks.md:20` and `:35` say accepted debt is recorded on the gate header or a tracking issue. Adding a one-line Known-gaps entry to each scanner is a doc edit outside this spike's Ask, so it is raised to the engineer rather than planned here.
- Letting marketplace plugins depend on the stow tree (former G3). Plugins standing alone is this repo's own distribution model (repo `CLAUDE.md`, "depends on no other repository"; README:201-222), so this plan could change it but declines to. Without it, a plugin hook could call the stow dispatcher when one is present. That dissolves the plugin blocker (row 11) only for stow-plus-plugin installs. Plugin-only installs still have no dispatcher, so each plugin gate would carry two code paths. The verdict is unchanged, because best-case retirement stays bounded by row 18.
- Relaxing the threat-model tier policy in `docs/hooks.md` (former G5): the cooperative default, no relaxing `irreversible` gates, the regression-only rule, and the dependency invariant. Changing that policy is a repo-wide decision beyond this spike's Ask. Without the regression-only rule, the residual deny could drop the `-n` walk and the `--continue` arms. That would newly let `git commit -n` past the marker gates, an enforcement-invariant loosening that is the engineer's call. It still retires none of the row 18 machinery, so the verdict is unchanged.
- Re-running the anchor-admissibility analysis. This decision doesn't affect it, because its baseline bypass fires no hook under either design (row 16).
