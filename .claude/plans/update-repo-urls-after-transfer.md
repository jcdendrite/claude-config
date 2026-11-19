# Update repo URLs after the transfer, with one shared origin-scope helper

## Context

Goal: make this repository's tracked references and its two origin-scoped hooks correct after the repo moved from `jcdendrite/claude-config` to `cordova-strategy/workflow-gates`.

Ask: "well don't we have to make changes in the repo to support the transfer?" and "Ok well we should continue with changing the actual code to support the rename right?", then, on the review round's helper question, the selected label "Add the helper to this PR", and on its known-gaps question, the selected label "Accept as documented gaps (Recommended)".

Why now: two hooks decide "is this the toolkit repo?" by testing whether `git config --get remote.origin.url` contains `claude-config` (`deny-private-project-refs.sh`, the public-repo redaction gate, and `require-stow-reminder.sh`). A checkout whose origin now ends in `workflow-gates.git` leaves both gates silent. The branch already widens each test to also accept `workflow-gates`, updates the tracked owner and repo URLs in live docs, the `tests.yml` comment and test fixtures, and adds tests. Historical case studies and design records keep their old URLs because GitHub redirects them. The plugin and marketplace id `claude-config` is out of scope.

Code review round 1 found two open defects in that widened check. The predicate is hand-copied into two hooks with no single owner. It also misses a URL typed in different case and a repo that carries several `remote.origin.url` values, because it reads only the last one.

Intended outcome: one shared helper in `claude/.claude/hooks/_lib.sh`, called by both hooks, that holds the only copy of the slug list. It reads every `remote.origin.url` value, matches case-insensitively, and keeps the whole-URL substring semantics the fork contract documents. A URL-matrix test covers it. The two shapes the helper deliberately does not close, an alias origin that hides the repo name and a repo with no `origin` remote, are recorded as known gaps in the helper's header comment, with each hook's Known gaps pointing there.

Decisions already made this session, from the plan-architect consult the review round ran:
- Keep the whole-URL substring match for both literals, and pin that with a superstring case in the matrix test.
- Keep the origin read uncapped, as both hooks have it today.
- Do not build on `_lib_origin_owner_repo`, because its owner/repo parse returns nothing for some URLs the substring match accepts.

## Approach

Add one helper, `_lib_origin_names_claude_config REPO_ROOT`, to `claude/.claude/hooks/_lib.sh`, and replace the hand-copied `REMOTE_URL` test in both hooks with a call to it. The helper lowercases every `remote.origin.url` value and returns 0 when any contains `claude-config` or `workflow-gates`. Its header is the one place that states the match rule and its known gaps, and both hook headers point there.

**The helper.** Insert it in `_lib.sh` directly after `_lib_case_insensitive_ne`, so the origin helpers sit together. The body runs under `local LC_ALL=C`.

**Call sites.**

- `deny-private-project-refs.sh`:
  - Replace lines 487-488 with a call that captures `$?` from `_lib_origin_names_claude_config "$REPO_ROOT"`. Status 1 takes the existing marker-arm path at 489-492 unchanged, and any other non-zero status calls `emit_deny` (ledger rows 19-20).
  - Reword the comment at 479 to "Scope: claude-config and its forks, by `_lib_origin_names_claude_config`, plus any repo with .claude/private-projects-optin at its toplevel."
  - Delete line 481, which describes a `git config --get` read this hook no longer makes.
  - In the header, replace the three Scope-and-limits bullets at lines 27-33 with the bullet below. Leave the fail-closed bullet at 34-37 as it is.

    ```text
    # - Runs its content scans only where `_lib_origin_names_claude_config` in
    #   _lib.sh accepts the origin, or in a repo with
    #   `.claude/private-projects-optin` at its toplevel.
    #   That function's header defines the origin match, which is deliberately loose.
    #   Those scans are silent in every other repo.
    ```

  - Add a Known gaps bullet after the pseudo-file pointer at 152-153: `# - The origin test's own gaps are listed in _lib_origin_names_claude_config's header in _lib.sh.`
- `require-stow-reminder.sh`:
  - Replace lines 133-134 with `if ! _lib_origin_names_claude_config "$REPO_ROOT"; then`, which stays fail-open per that hook's header, and keep the comment at 132.
  - Replace the header bullet at 25-27 with the bullet below.

    ```text
    # - Fires only when `_lib_origin_names_claude_config` in _lib.sh accepts the
    #   origin, the same test deny-private-project-refs.sh applies without its
    #   .claude/private-projects-optin marker arm.
    ```

  - Append the same Known gaps pointer bullet after line 87.

**Tests.**

- Add a `TestLibOriginNamesClaudeConfig` class to `test_lib_gh.py`, after `TestLibCaseInsensitiveNe`. Each case builds a repository with `init_git_repo`, then calls the helper with that repo's path through `_run_lib_call`. The env comes from `isolated_git_config_env` at `claude/.claude/tests/helpers.py:1652`, not a third inline copy of `test_lib_gh.py:62`. Each case asserts the exact return code.
  - Returns 0:
    - `https://github.com/someone/claude-config.git`
    - `https://github.com/someone/claude-config/`, a trailing-slash origin that `_lib_origin_owner_repo` rejects, which pins the decision not to build on it
    - `git@github.com:someone/workflow-gates.git`
    - `https://github.com/Someone/Workflow-Gates.git`
    - `git@github.com:SOMEONE/CLAUDE-CONFIG.git`
    - the superstring `git@github.com:someone/claude-config-notes.git`
    - two origin values, the matching one first and `git@github.com:someone/other-app.git` added last with `git config --add remote.origin.url`, as `test_plan_push_preflight.py:971` builds one
  - Returns 1:
    - `git@github.com:someone/other-app.git`
    - a path that is not a git repository
    - a repository with no remotes
    - a `workflow-gates` URL under a remote named `upstream` only, which pins the no-origin gap, with a positive control asserting the `upstream` remote's URL holds the slug
    - origin `wg:` with `url.git@github.com:someone/workflow-gates.git.insteadOf` set to `wg:`, which pins the alias gap, with a positive control asserting `git remote get-url origin` shows the rewritten slug
- Add a deny case in `test_deny_private_project_refs.py` where the helper returns 127, per ledger row 20.
- Add one wiring case per hook, an origin of `git@github.com:Someone/Workflow-Gates.git`:
  - `test_mixed_case_origin_gates` beside `test_workflow_gates_fork_origin_gates`, which asserts deny.
  - `test_origin_in_mixed_case_still_gates` beside `test_origin_with_the_new_slug_still_gates`, through `assert_live_deny_control`.
- Reword the `test_no_remote_suspect_token_allowed` docstring (`test_deny_private_project_refs.py:644-648`) so it no longer names `git config --get` or the two glob literals. It states only that a repo with no `origin` remote stays ungated.
- Update the module docstring at `test_lib_gh.py:1` to name the new function.

**Alternatives set aside.**

- Reading `git remote get-url --all origin` instead applies `insteadOf`, which closes the alias gap. But a rewrite whose replacement drops the name would turn today's deny into silence on an irreversible gate. Reading both forms avoids that at the cost of a second git call per gated command. The engineer's acceptance of the documented gap makes that call unnecessary.
- Matching every remote's URL closes the no-origin gap. But it arms both gates in an unrelated repository that merely carries a claude-config remote under another name.
- An optional REPO_ROOT with a cwd fallback, the form `_lib_origin_owner_repo` has, is set aside because neither caller needs the cwd form.
- Rows 5 and 9 below cover the inline-copies-plus-parity-test option and the other case-folding primitives.

**Assumption ledger.**

Root: two gates decide whether a checkout is claude-config with a predicate hand-copied into each. That predicate reads only the last `remote.origin.url` value and matches case-sensitively, so a case-variant or multi-valued origin leaves both gates silent.

Givens:

- GitHub resolves an owner/repo path in any letter case, so a working clone URL can carry any case. GitHub owns this. `[unverified]`: `_lib.sh:794-796` states it, and it was not checked against GitHub's documentation this session.
- `remote.origin.url` is a multi-valued git key. `git config --get` prints only its last value, and `--get-all` prints every value. Git owns this. `[unverified]`: the Context states it from the round-1 review, and it was not run this session. The matrix case with the matching value first pins it.
- `git config` returns a remote URL as configured, before any `url.<base>.insteadOf` rewrite. Git owns this. `[unverified]`: `test_lib_gh.py:115-117` states it, and the matrix's alias case pins it.
- Both gates fall under `docs/hooks.md` § "Threat-model tiers", and its existing-debt rule (line 20) and regression-only rule (lines 26-31) bind this change. That section owns these rules, and changing them needs a decision outside this plan. `[verified: docs/hooks.md:20, :26-31; tier lines at deny-private-project-refs.sh:3 and require-stow-reminder.sh:3]`

Rows:

1. `[engineer-verified: "Add the helper to this PR"]` The shared helper lands in this PR. The option's description was the dispatching session's own proposal, so this row covers only where the helper lands. `anchors: root`
2. `[engineer-verified: "Accept as documented gaps (Recommended)"]` The alias-origin and no-origin shapes stay open and are documented rather than closed. `anchors: root`
3. `[verified: Grep for *claude-config*, *workflow-gates* and remote.origin.url over every .sh and .py file]` Only `deny-private-project-refs.sh:487-488` and `require-stow-reminder.sh:133-134` test the origin slug. No third sibling arm needs the fix. `anchors: root`
4. `[verified: .claude/rules/bash-unit-test-seams.md]` A helper that two hooks need belongs in `claude/.claude/hooks/_lib.sh`. A new `_*-lib.sh` sidecar under `hooks/` fails `test_hook_alignment.py` today. `anchors: row3`
5. Mechanism: `_lib_origin_names_claude_config REPO_ROOT` in `_lib.sh` holds the only copy of both literals. The lighter option was two inline copies plus a parity test. It fails because it guards a duplication the helper removes, and the next rename would still need two edits. `anchors: row4`
6. `[verified: Grep "toolkit" under claude/.claude/hooks: 4 files, all the transcript-toolkit nudge hook and its tests]` The name departs from the dispatching session's proposed `_lib_origin_names_toolkit_repo`. "toolkit" already names a different thing in the hooks tree. Both hook headers, `README.md:473` and `docs/hooks.md:140` call this repository claude-config. `anchors: row5`
7. Mechanism: the helper takes a required REPO_ROOT and reads `git -C "$repo_root" config --get-all remote.origin.url`, uncapped per the Context decision. `--get-all` closes the multi-value miss. `anchors: root`
8. `[verified: deny-private-project-refs.sh:474, :487; require-stow-reminder.sh:127, :133]` Each hook's REPO_ROOT is the `git rev-parse --show-toplevel` of the directory its origin read runs in today, so `git -C "$REPO_ROOT"` reads the same repository's config. Both callers exit before the call when REPO_ROOT is empty (`deny-private-project-refs.sh:475-477`, `require-stow-reminder.sh:128-130`). `anchors: row7`
9. Mechanism: `${origin_urls,,}` against lowercase literals folds case. Each alternative fails:
   - `shopt -s nocasematch` mutates shell-global state and needs the save-and-restore that `_lib_case_insensitive_ne` carries (`_lib.sh:817-825`).
   - `grep -qi` forks a process on every gated command.
   - `_lib_case_insensitive_ne` tests equality, not a substring.

   `anchors: root`
10. `[verified: .claude/rules/shell-floor.md; _lib.sh:25 sources _config.sh, whose floor check is at _config.sh:40; _lib.sh:2145-2147 already uses ${var,,}]` The bash-4 lowercase expansion is allowed here, and it runs only after the floor check. `anchors: row9`
11. Mechanism: the whole-URL substring match stays, per the Context decision. The matrix pins it with a superstring case. `anchors: root`
12. `[verified: reading deny-private-project-refs.sh:487-488 and require-stow-reminder.sh:133-134 against the helper body]` The helper accepts every origin the staged predicate accepts. The last value is among the values `--get-all` prints, and lowercasing leaves a lowercase literal intact. Neither gate allows an input it denied at the merge-base, so the regression-only rule finds no regression. `anchors: row7`
13. `[verified: deny-private-project-refs.sh:152-153; docs/hooks.md:55]` This gate's Known gaps already points at a shared helper's header for that helper's gaps. The commit-boundary gates record their debt in their header Known gaps, so a pointer bullet in each hook keeps the helper's known gaps in that closed set. `anchors: row2`
14. `[verified: docs/hooks.md:20, :26]` Every gap in the helper's header also exists at the merge-base, which reads the same unrewritten `origin` URL, so none is a regression. The header lists four gaps:
    - An alias origin.
    - No `origin` remote.
    - A renamed fork or mirror.
    - A failed `git config` read, which counts as "not claude-config" and so leaves the deny gate silent.

    Only a missing helper function fails closed. The existing-debt rule still requires the commit message or PR body to state why the engineer-accepted gaps, the alias origin and the missing `origin`, are accepted. `anchors: row2`
15. `[verified: claude/.claude/rules/shell-script-conventions.md; test_lib_gh.py:1, :51-131]` The URL matrix belongs in direct helper tests beside the sibling `TestLibOriginOwnerRepo`. Each hook keeps one subprocess case per branch. `anchors: row5`
16. Mechanism: one mixed-case-origin test per hook proves the hook calls the helper. Each is written before the call-site swap, fails against the staged case-sensitive predicate, and passes after the swap. `anchors: row15`
17. `[verified: test_shell_lib_function_liveness.py:3-8, :12-13, :153-185; test_hook_alignment.py:105, :111-125]` The liveness test requires a column-0 `name() {` definition and one non-comment reference in tracked shell or Python files. The two hook calls satisfy that. `test_hook_alignment.py` sweeps `_lib.sh` for exactly two shapes, a GNU `\s` regex and a pipe into an early-exit grep. The helper uses neither. No test there requires anything else of a single `_lib.sh` function. `anchors: row5`
18. `[verified: README.md:473; docs/hooks.md:140, :147; docs/private-project-redaction.md:12-16, :402-407, :548-549, :678-684]` Each doc passage states that an origin URL containing either name scopes the repo in. The helper still honors that sufficient condition, so no passage needs an edit beyond the staged one. `anchors: row12`
19. `[verified: ciso-reviewer and staff-platform-engineer findings, plan-review round 1; deny-private-project-refs.sh:461 and its status checks]` With `if !`, a helper status other than 0 or 1, such as 127 from a missing function, reads as "origin does not match" and silences the irreversible deny gate. In `deny-private-project-refs.sh` only status 1 means "not claude-config" and any other non-zero status denies through `emit_deny`, as the hook does at line 461 for its other helper. `require-stow-reminder.sh` keeps the plain `if !` because its header makes it fail-open. `anchors: row7`
20. Mechanism: the deny hook's call site captures `$?` instead of using `if !`. A test runs the hook against a stubbed helper that returns 127 and expects deny, using the hook's existing helper-stub seam, or a copy of the hooks directory without the function when no seam exists. `anchors: row19`
21. `[engineer-verified: "Fix all three (Recommended)"]` Code-review round 2 found three files outside the first Critical files list that name the old owner or the old origin test: `claude-skills/skills/review-pr/REFERENCES.md`, `docs/precompact-hook-behavior.md` and `docs/cost-levers-considered.md`. The quote covers only that they are fixed in this PR. The option description naming the exact edits was the session's own proposal. `anchors: root`

## Critical files

Edited further by this plan:

- `claude/.claude/hooks/_lib.sh`: add `_lib_origin_names_claude_config` after `_lib_case_insensitive_ne`.
- `claude/.claude/hooks/deny-private-project-refs.sh` (staged): call the helper at 487-488. Edit the comments at 479 and 481, the Scope-and-limits bullets at 27-33, and the Known gaps pointer after 152-153.
- `claude/.claude/hooks/require-stow-reminder.sh` (staged): call the helper at 133-134. Edit the Scope bullet at 25-27 and add the Known gaps pointer after 87.
- `claude/.claude/hooks/tests/test_lib_gh.py`: add the `TestLibOriginNamesClaudeConfig` matrix class and update the line-1 module docstring.
- `claude/.claude/hooks/tests/test_deny_private_project_refs.py` (staged): add `test_mixed_case_origin_gates` and reword the docstring at 644-648.
- `claude/.claude/hooks/tests/test_require_stow_reminder.py` (staged): add `test_origin_in_mixed_case_still_gates`.

Added after code-review round 2, on the engineer's selected label "Fix all three (Recommended)":

- `claude-skills/skills/review-pr/REFERENCES.md`: the sentence naming the origin test now names both strings.
- `docs/precompact-hook-behavior.md`: one issue link retargeted to `cordova-strategy/workflow-gates`.
- `docs/cost-levers-considered.md`: one issue link retargeted to `cordova-strategy/workflow-gates`.

Added after the round 12 consult, on the engineer's selected label "Fix it in this PR (Recommended)":

- `evals/comment_ab_pool_count.py`: `STUDY_REPOSITORY` at line 98 names `cordova-strategy/workflow-gates`.
- `CHANGELOG.md`: one `[Unreleased]` entry with a `**Migration:**` bullet telling existing clones to repoint `origin`. Added on the engineer's selected label "Add the CHANGELOG entry (Recommended)".
- `claude/.claude/hooks/tests/_lib_sh_helpers.py`: shared `copy_hooks_tree` and `remove_lib_function` helpers for the two status-127 tests.
- `claude/.claude/tests/helpers.py`: the shared `locale_that_folds_ascii_capital_i_to_a_non_ascii_letter` finder.
- `claude/.claude/scripts/tests/test_reconcile_project_plugins.py`: imports that finder instead of a private copy.

Already staged, with no further change:

- `.github/workflows/tests.yml`
- `CODE_OF_CONDUCT.md`
- `CONTRIBUTING.md`
- `README.md`
- `SECURITY.md`
- `claude/.claude/hooks/tests/test_hook_alignment.py`
- `docs/hooks.md`
- `docs/private-project-redaction.md`

Reuse rather than reimplement:

- `_run_lib_call` from `claude/.claude/hooks/tests/_lib_sh_helpers.py`.
- `run_hook` and `bash_input` in the deny test file.
- `assert_live_deny_control` at `test_require_stow_reminder.py:187`.

Dispatch split: one `code-writer` dispatch covers every further edit. The tests depend on the helper's name and contract, so a split would restate that background in each prompt.

Projected diff: about 300 changed lines. Roughly 130 are already staged, estimated from reading the staged files because no diff stat was run. Roughly 150 come from this plan: about 15 in `_lib.sh`, 70 in `test_lib_gh.py`, 30 across the two hooks and 35 across the two hook test files.

## Verification

1. Red step: first run the matrix against a helper body written as the staged predicate (`--get` and case-sensitive) and confirm the matching-value-first row and the mixed-case rows fail. Then write the two wiring tests before swapping the call sites, then run `scripts/venv-run.sh pytest claude/.claude/hooks/tests/test_deny_private_project_refs.py claude/.claude/hooks/tests/test_require_stow_reminder.py -k mixed_case`. Both fail against the staged predicate, and both pass after the swap.
2. Focused run: `scripts/venv-run.sh pytest claude/.claude/hooks/tests/test_lib_gh.py claude/.claude/hooks/tests/test_deny_private_project_refs.py claude/.claude/hooks/tests/test_require_stow_reminder.py claude/.claude/hooks/tests/test_shell_lib_function_liveness.py claude/.claude/hooks/tests/test_hook_alignment.py`.
3. The documented scoped run: `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py`.
4. Lint: `scripts/venv-run.sh ruff check claude/.claude/hooks/tests/` and `scripts/list-shell-files.sh | xargs -0 scripts/venv-run.sh shellcheck`.
5. Single-owner check: a Grep for `\*claude-config\*` and `\*workflow-gates\*` over `claude/.claude/hooks/*.sh` matches only inside `_lib_origin_names_claude_config`.
6. The commit message or PR body states why the alias-origin and no-origin gaps are accepted and quotes the engineer's label "Accept as documented gaps (Recommended)", as `docs/hooks.md:20` requires.

## Out of scope

- A permanent tripwire test for a third inline copy of the slug list: Verification step 5's one-shot grep covers this PR, and the shell-lib liveness test already flags an unreferenced helper.
- A separate locale-pin change for the unchanged case-insensitive comparator and the blocklist grep. The helper itself carries `local LC_ALL=C`, because a Turkish locale folds `CLAUDE-CONFIG` to `claude-confıg`. The pin test in `test_lib_gh.py` runs only where a Turkic locale is installed, and CI installs one.
- `claude-skills/skills/memory-store-audit/SKILL.md:125`, whose own check requires the remote to contain `claude-config`: the engineer selected "Separate follow-up (Recommended)".
- The false-match arm's deny text is out of scope. In an unrelated repo whose origin merely contains either string, it tells an honest agent to add a prefix to the public allowlist. The engineer selected the label "Keep as-is, log your decision (Recommended)".
- Closing either documented gap, whether by reading `git remote get-url` or by matching every remote: the engineer accepted both as documented gaps.
- Rewording the docs' origin-scope summaries in `README.md`, `docs/hooks.md` and `docs/private-project-redaction.md` beyond the staged edits: each states a sufficient condition the helper still honors (ledger row 18).
- The exact-slug comparators that review-pr scripts and `require-respond-pr.sh` apply to old-slug clones: they need their own change in files this diff does not touch. The CHANGELOG entry tells existing clones to repoint `origin`.
- Owner-side GitHub repository settings, which sit outside the tree.
