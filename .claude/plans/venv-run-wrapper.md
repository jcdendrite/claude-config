# Plan: venv-run wrapper (GH-1252)

## Context

Goal: stop the contributor commands from reaching the shared `.venv` by counting directory levels, so they work from a linked worktree at any depth.

Ask: "Ok 1251 is merged let’s move to 1252", where GH-1252 is the issue "Contributor commands address the shared .venv by worktree depth, which breaks for ticketed branches" (https://github.com/jcdendrite/claude-config/issues/1252). When the issue was being filed, in an earlier part of the same conversation, the engineer wrote "I agree with filing one issue. Have the architect look at the draft before filing".

Problem. `README.md` says linked worktrees are "exactly three levels deep" and prescribes `../../../.venv/bin/...`. Ticketed branches (`<TICKET-ID>/<topic-slug>`) put worktrees four levels deep, and git accepts branch names with any number of `/` segments, so the depth is unbounded. From a four-level worktree the documented path does not exist. `.claude/settings.json` carries two exact-match allow rules for the select-tests command, at depth 0 and depth 3.

Why now. GH-1234 (PR #1251, merged) made `worktree-ensure.sh` the way to create worktrees, which makes four-level ticketed worktrees the common case.

Intended outcome. One repo-root, non-stowed wrapper finds the main checkout's `.venv` through git and execs the requested tool. The documented commands and the allow rule use it.

## Approach

Add one repo-root script that is not stowed: `scripts/venv-run.sh <tool> [args...]`. It finds the main checkout from its own on-disk location with `git -C <script dir> rev-parse --path-format=absolute --git-common-dir` followed by `dirname`, validates the tool name and the target, and `exec`s `<main>/.venv/bin/<tool>` without resolving symlinks. The same text then runs from the main checkout and from a linked worktree at any depth. The documented contributor commands, one exact-match select-tests allow rule, and the hooks' runner-skip lists all switch to it, and `select-tests.py` gains a rule for repo-root `scripts/`.

Wrapper algorithm, in order:
1. Check the arguments first, since that needs no I/O. No argument or an empty first argument exits 2. A tool name containing `/` exits 2.
2. Resolve the common dir inside `if ! common_dir=$(git -C "$script_dir" rev-parse ... 2>/dev/null)`, where `script_dir` is `dirname -- "${BASH_SOURCE[0]}"`. A bare assignment would abort under `set -e` with git's own 128 and print no message of ours. On failure exit 127 with a message that names `$script_dir` and says git failed or returned nothing.
3. Set `main=$(dirname -- "$common_dir")`. If `$main/.venv` is not a directory, exit 127 with `run ./install-dev.sh in <main>`.
4. If `$main/.venv/bin/$tool` is not both `-f` and `-x`, exit 127 with a message that names the tool and `<main>/.venv/bin` and says to run `./install-dev.sh` in `<main>`. `install-dev.sh` recreates an unhealthy `.venv`, so the remedy fits a dangling interpreter link as well as a missing venv.
5. `tool=$1; shift; exec "$main/.venv/bin/$tool" "$@"`.

There is no single-absolute-line guard on git's output: `README.md:97` declares git >= 2.31, and what the wrapper does on an older git is unverified (row 12). The result then is a path that does not name the main checkout, and the `.venv` checks in steps 3 and 4 are the only guard.

There is no PATH lookup, no environment scrub and no `cd`, so the tool runs in the caller's working directory. Every failure the wrapper itself produces is one stderr line starting `venv-run.sh: `. No message echoes `"$@"`. A small `fail <code> <message>` function emits it.

Exit codes: 2 for a usage error and 127 for any resolution failure. The architect review suggested a single 127 instead. I split usage out because neither `scripts/list-shell-files.sh` (it has no explicit exits) nor `shell-script-conventions.md` (no exit-code rule) sets a convention, so the repo's own usage precedent governs. That precedent is exit 2 with a `<script>: ` prefix (row 30). 127 is kept for "could not find it" because it is what the missing `../../../.venv/bin/X` form returns today (row 31). I agree with the review on sysexits: no repo script uses 64 or 69.

Durable comments the wrapper carries, one line each:
- Header: `# Run a tool from the main checkout's contributor .venv, from the main checkout or any linked worktree.`
- `# Usage: scripts/venv-run.sh <tool> [args...]`
- `# Exit 2: usage error. Exit 127: the main checkout, its .venv, or the tool was not found.`
- At the name check: `# A bare name only: a '/' could reach an executable outside .venv/bin.`
- At the exec: `# Exec the unresolved path: Python finds its venv from the path it was invoked by.`

Bash version: the wrapper needs nothing beyond `[[ ]]`, `${BASH_SOURCE[0]}`, `"$@"` and `exec`, so it stays bash-3.2-clean at no cost. No test enforces this for repo-root scripts (row 26). Widening that guard is out of scope.

Alternatives considered and set aside (the full over-powered-primitive check is at M1):
- A fixed verb list (`test`, `lint`, ...). The delegated process chose the generic form instead (row 4).
- Sourcing `_lib.sh` for `_lib_main_repo_root`. Rejected for the reasons in rows 9-10.
- Using the first entry of `git worktree list --porcelain`. It would also handle a separate-git-dir layout, but it means parsing porcelain output, and the three sites that locate the main checkout take `dirname` of the common dir, so the wrapper follows them (row 8 verifies the count; the sites are named in Out of scope).
- A single 127, or sysexits. Covered above.

### Assumption ledger

**Root problem:** From a linked worktree deeper than three levels, every documented contributor command (`../../../.venv/bin/...`) names a path that does not exist, and the allow-listed select-tests command matches only depth 0 and depth 3.

**Givens:**
- G1. `permissions.allow` Bash rules are exact-match only. Reason: the harness matches a rule with no `*` against one exact command string, and the global CLAUDE.md Safety rule ("No wildcards in `permissions.allow`") sits outside this repository.

Conditions the plan could change by editing this repository and deliberately does not (reach includes this repo's own artifacts, so these are not givens; each is repeated in **Out of scope** with what the design becomes without it):
- O1. Worktree depth is unbounded. Git accepts branch names with any number of `/` segments, and `branch-management`'s `<TICKET-ID>/<topic-slug>` convention maps onto the worktree path. Without it, a flat naming rule would fix the depth and the wrapper would be unnecessary.
- O2. There is one shared `.venv`, at the main checkout root (`install-dev.sh`, README.md:306). Without it, each worktree would run `./install-dev.sh` for its own venv and `.venv/bin/<tool>` would work at any depth; the issue weighed that and kept the shared venv for the per-worktree install cost and pin drift.
- O3. git >= 2.31 (README.md:97 declares it). Without it, the wrapper would need a fallback that canonicalises `--git-common-dir` itself.

**Mechanisms:**
- M1. Repo-root `scripts/venv-run.sh` that resolves `<main>` from its own location and execs `<main>/.venv/bin/<tool>`. anchors: root, row4, row6, row7.
  Over-powered check: a new script is heavier than editing docs. Lighter primitives, each failing:
  - (a) Per-depth relative paths, plus one allow rule per depth. Fails: depth is unbounded (O1), and each depth needs its own exact-match rule (G1). Replaces row1.
  - (b) An inline `$(git rev-parse ...)` in each documented command. Fails: it restates the resolution at about 10 doc sites (row 2) against single-source-of-truth. Global CLAUDE.md routes multi-step Bash to a script ("Script-first"). `docs/worktree-bash-guard.md` ("Trigger taxonomy" and "The fix: script-first") records that a bare `$(git ...)` substitution was refused in a worktree-anchored session and prescribes one script call per multi-step recipe. Replaces row8.
  - (c) Have `worktree-ensure.sh` symlink `.venv` into each worktree. Fails: `install-dev.sh:14-17` refuses a symlinked `.venv`. Worktrees not created by that script (`isolation: worktree`, a hand-run `git worktree add`) would lack the link. `worktree-ensure.sh` is stowed to every consumer, for a need only claude-config has. Replaces row32.
  - (d) A wildcard allow rule. Fails G1.
- M2. Reject an empty tool name or one containing `/`, require `-f` and `-x`, and do no PATH lookup, environment scrub or `cd`. anchors: row4, row15, row19.
- M3. Exec the unresolved `.venv/bin/<tool>` path, never a realpath. anchors: row13, row14.
- M4. Failure contract: one `venv-run.sh: ` stderr line; exit 2 for usage, 127 for resolution. anchors: row30, row31, row32.
- M5. Resolve the main checkout with the inline `git -C <script dir> rev-parse --path-format=absolute --git-common-dir` formula rather than sourcing `_lib.sh`, with no output guard. anchors: row9, row10, row12.
- M6. One allow rule, `Bash(scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py)`, replaces `.claude/settings.json:11-12`. The main session makes this edit, not a code-writer. anchors: row1, row5, row15, row33, row41, row42.
- M7. All documented command sites switch to the wrapper form in this PR, including README's "exactly three levels" sentence. anchors: row2, row25, row34, row40.
- M8. Add `venv-run.sh` to the runner list in `_lib.sh:1852` and to the mirrored list in `deny-repo-relocation.sh:137`, with tests in `test_lib.py`. anchors: row16, row17, row18.
- M9. New constant `ROOT_SCRIPTS_DIR = "scripts"` and a `CROSS_DOMAIN_EXCEPTIONS` row mapping it to `(HOOKS_TESTS_DIR, SCRIPTS_TESTS_DIR)`. anchors: row20, row21, row22, row23.
- M10. A subprocess test against a real repo plus a linked worktree four levels deep, with one case per failure branch. No `_<topic>-lib.sh` extraction. anchors: root, row13, row37.
- M11. A CHANGELOG `[Unreleased]` entry. No `docs/scripts.md` entry. anchors: row35.

**Rows:**
1. `[verified: .claude/settings.json:9-14; grep of claude/.claude/settings.json]` Exact-match select-tests rules exist only at `.claude/settings.json:11` (depth 0) and `:12` (depth 3). The stow-source settings file has none. The rules at `:10` and `:13` are bare PATH forms that do not depend on depth.
2. `[verified: grep \.\./\.\./\.\./\.venv|\.venv/bin/ outside .claude/plans, docs/case-studies, docs/reports]` The depth-form sites are:
   - `CLAUDE.md:12-15` and `CLAUDE.md:35-37` ("the worktree-relative `.venv` paths")
   - `README.md:515-517`, `523`, `529`, `544`, `547`
   - `install-dev.sh:113-115`
   - `install.sh:954` and `install.sh:970`

   No other hit is a user-facing command site or hint. The hits that stay as they are fall in these classes:
   - `install-dev.sh:80-111` runs `.venv/bin/` in its own cwd by design.
   - `python-environment-conventions.md:17,32-33` is generic guidance.
   - `reviewer_yield.py` and its test are row 36.
   - `require-skill-review.sh:298-299` is row 7.
   - Test fixtures and docstrings that use the strings as data (`test_deny_network_installs.py`, `test_install_dev.py`, `select-tests.py:1012`), one design-decision record, and a dated comment in `evals/`.
3. `[engineer-verified: "Ask staff backend engineer then have the architect do an adversarial review of that"]` The interface is whatever that process settles.
4. `[unverified]` The process's outcome is generic `scripts/venv-run.sh <tool> [args...]`, upheld with changes. This is the session's relay of subagent output, not the engineer's words. Rows 15 and 19 re-check its reach against source.
5. `[engineer-verified: "Remove both now (Recommended)"]` Both rules at `.claude/settings.json:11-12` are removed in this PR.
6. `[engineer-verified: "Ask fable"]` Finding the main checkout was delegated to a Fable consult. Its relayed outcome (inline, anchored on the script's own location) is checked in rows 7-12.
7. `[verified: plugins/skill-management/hooks/require-skill-review.sh:292-299]` That hook uses the same formula for the same venv, anchored on its own path (`$0`), and checks `-x .../.venv/bin/python3`.
8. `[verified: grep path-format=absolute --git-common-dir in *.sh, excluding tests]` The formula appears inline at 12 call sites across 9 shell files. The consult said "about six".
9. `[verified: install.sh:103, install.sh:432]` The consult's claim that sourcing `_lib.sh` would be "the first root-to-`claude/` dependency" is wrong: `install.sh` already sources `_stow_migration_lib.sh` and `_config.sh`. It would be the first `scripts/`-to-`claude/` dependency. The remaining ground stands: sourcing the hook library would break the test runner whenever a branch breaks `_lib.sh`, which is exactly when the runner is needed.
10. `[verified: claude/.claude/hooks/_lib.sh:580-601]` `_lib_main_repo_root` exists, but it resolves from cwd, not from a given directory. Reusing it would need a subshell `cd` on top of sourcing the library.
11. `[verified: README.md:97]` git >= 2.31 is declared, and `install.sh` does not check it.
12. `[unverified]` git older than 2.31 prints `--path-format=absolute` as an output line and exits 0. This is documented at `_lib.sh:584-585` but was not run this session. The wrapper carries no guard for it, because row 11 declares git >= 2.31 and a guard would need its own test (the architect consult recommended deleting it).
13. `[verified: claude/.claude/scripts/select-tests.py:1011-1018]` `select-tests.py` finds pytest via `Path(sys.executable).with_name("pytest")`. Exec'ing a resolved `/usr/bin/python3.X` would miss the venv's pytest.
14. `[unverified]` PEP 405, quoted from memory and not fetched: "If a pyvenv.cfg file is found either adjacent to the Python executable or one directory above it (if the executable is a symlink, it is not dereferenced), this file is scanned for lines of the form key = value." Row 13 independently requires the unresolved path.
15. `[verified: claude/.claude/scripts/tests/test_review_pr_post.py:883-920]` The new allow entry passes `_allow_entry_matches_script_invocation`. Its head, `venv-run.sh`, is not in `SHELL_WRAPPER_HEADS`; it names no gated script; it has no wildcard. Adding `venv-run.sh` to `SHELL_WRAPPER_HEADS` would fail this entry. M2's `/` rejection is what keeps the wrapper away from `~/.claude/scripts/*`.
16. `[verified: deny-reviewer-tree-mutation.sh:467-481; _lib.sh:1848-1855]` Without the runner entry, `scripts/venv-run.sh ruff check --fix` resolves to the command word `scripts/venv-run.sh`. The reviewer gate's deny for ruff `--fix`/`format` would then miss the newly documented form. `../../../.venv/bin/ruff` resolves today because matching is on the basename.
17. `[engineer-verified: "Yes, add to both lists (Recommended)"]` Add the entry to both lists, with a test in `test_lib.py`'s `_lib_fragment_command_word` block and hook review of the change.
18. `[verified: tier lines and call sites of _lib_fragment_invokes_tool / _lib_command_invokes_tool_subcmd]` Gates whose verdict goes through `_lib_fragment_command_word`:
    - `cooperative, untrusted-input, irreversible`: `block-gh-pr-merge.sh`, `enforce-marker-script-shape.sh`
    - `cooperative, irreversible`: `deny-reviewer-tree-mutation.sh`, `deny-invisible-commit-content.sh`
    - `cooperative`: `deny-repo-relocation.sh`, `deny-escaped-backticks-in-pr-body.sh`, `require-stow-reminder.sh`

    The change can only widen what these gates catch. Resolution moves past a first word only when its basename is `venv-run.sh`, so every fragment that matched a tool before still matches. One caller uses a match to skip a re-check (`deny-invisible-commit-content.sh:419-421`). The masked-text worktree-target check at `:390` still runs on that same fragment unconditionally, and `.venv/bin/git` does not exist.
19. `[verified: claude/.claude/hooks/deny-network-installs.sh:121-131, 257-264]` That gate matches `pip` as any word, not as the command word. `scripts/venv-run.sh pip install <pkg>` is still denied.
20. `[engineer-verified: "Keep it as you chose"]` Add a `select-tests.py` rule for repo-root `scripts/**`. The quote covers adding the rule. The domain choice in row 21 is mine.
21. `[verified: claude/.claude/hooks/tests/test_shellcheck.py:31; claude/.claude/hooks/tests/test_shell_lib_function_liveness.py:66; grep of claude-skills]` The hooks tests read repo-root `scripts/` (they run `list-shell-files.sh` and lint or scan every tracked shell file). The new `test_venv_run.py` is in the scripts tests. No skills test reads repo-root `scripts/`.
22. `[verified: claude/.claude/scripts/tests/test_select_tests.py:343-365]` `TestCrossDomainReadCompleteness` skips any path that falls open to the full suite. Once the row exists, it checks the module constants in `test_shellcheck.py`, `test_shell_lib_function_liveness.py` and `test_venv_run.py` against the row automatically.
23. `[verified: test_select_tests.py:1972-2031]` `_KNOWN_CROSS_DOMAIN_READS` must not gain a hooks-tests entry for a `scripts/` path. Its equality test requires every such entry to be a `SKILL_FILES_READ_BY_HOOK_TESTS` member.
24. `[verified: select-tests.py:407-411]` `select-tests.py` is a `GLOBAL_TRIGGER_PATHS` member, so this PR's scoped run is a full-suite run (CLAUDE.md, case 1).
25. `[engineer-verified: "Yes, replace it in this PR (Recommended)"]` README.md:523's "exactly three levels deep" sentence is replaced in this PR. The quote covers only that choice. The replacement wording is the session's.
26. `[verified: claude/.claude/scripts/tests/test_no_bash4_constructs.py:7-9, 23-24, 38-42]` The bash-4 guard scans only `claude/.claude/**/*.sh`.
27. `[unverified]` "Bash 3.2 support is being deprecated" is the session's relay of the engineer, not a quote. The wrapper's choice does not depend on it.
28. `[verified: .github/workflows/tests.yml:106; claude/.claude/hooks/tests/test_ci_path_filter.py:32-34]` `SHELL_REGEX` matches `\.sh$`. The pytest skip list covers only five root docs, so a `scripts/` change runs both pytest and shellcheck in CI.
29. `[verified: claude/.claude/scripts/stow-packages.sh:24-27]` Repo-root `scripts/` is not a stow package, so the directory of `BASH_SOURCE[0]` is always inside the checkout.
30. `[verified: config-get.sh:42-43; review-pr-acquire.sh:65-66; review-pr-finish.sh:33-34; relocate-claude-config.sh:502-503; grep "exit (64|69)" over *.sh found none]` Usage errors exit 2 at all four sites, two of them with a `<script>: ` prefixed message and two with a usage text. No repo script uses sysexits.
31. `[unverified]` bash exits 127 for a command path that does not exist. This is standard behaviour, not run this session, and only the rationale for picking 127.
32. `[verified: install-dev.sh:10-13, 94-97]` `install-dev.sh` creates `.venv` in its cwd and only checks for `requirements-dev.txt`. It therefore also "succeeds" from a linked worktree, creating a `.venv` the wrapper ignores. That is why the missing-venv message names `<main>`.
33. `[verified: claude/.claude/hooks/ask-review-permissions.sh:3, 18-23]` An Edit or Write to `.claude/settings*.json` triggers an ask that points at `/review-permissions`.
34. `[verified: claude/.claude/hooks/tests/test_install_sh_contributor_intent_prompt.py:11-12, 75-106]` These tests assert only on `install-dev.sh`, `private-projects.md`, the "Optional (contributors)" text, and the fixture markers.
35. `[verified: CHANGELOG.md:90, 237; docs/scripts.md:3]` CHANGELOG records `select-tests.py` rule changes. `docs/scripts.md` covers only `claude/.claude/scripts/`, so the README Tests section is the wrapper's documentation home.
36. `[verified: claude/.claude/scripts/transcript_analysis/reviewer_yield.py:283-288; claude/.claude/scripts/tests/test_transcript_reviewer_yield.py:1708-1714]` `reviewer_yield.py` handles the old `../../../.venv/bin/pytest` form. Left untouched, it keeps handling historical transcripts.
37. `[verified: claude/.claude/rules/shell-script-conventions.md, "Extract pure bash" bullet]` The extraction rule targets pure-bash logic with an input matrix. The wrapper is git, filesystem and exec I/O plus one two-shape `case`, so a subprocess test with one case per branch is the whole seam.
38. `[unverified]` `gh issue view 1252` was not run by the architect (no Bash). The plan's Context section and the dispatch relay were the issue's only representation there.
39. `[unverified]` Whether a `.claude/settings.json` change takes effect mid-session (the new rule may prompt once in this session). Not load-bearing.
40. `[verified: grep of "Run tests|Run lint|Lint shell" in **/tests/*.py]` No test asserts the text of `install-dev.sh`'s closing hint lines.
41. `[verified: claude/.claude/scripts/review-pr-checkout.sh:584 via _lib.sh:908-910; claude-skills/skills/review-pr/audit-execution-surface.py:45-105; claude-skills/skills/review-pr/SKILL.md:100]` A `/review-pr` checkout worktree is created at depth 3, the depth the old rule at `.claude/settings.json:12` matches. The audit's `_classify` stops on named `.claude/` subtrees (skills' `SKILL.md`, hooks, agents, rules, output styles, commands), `settings.json`, `CLAUDE.md` and `AGENTS.md`, but not on `claude/.claude/scripts/` or root `scripts/`, and Step 6's "always stops for confirmation" is prose that no hook enforces. So the old rules already let a PR's own `select-tests.py` run unprompted from a review worktree whose Bash cwd is inside it. The new rule adds one more PR-controlled file, the wrapper, to that chain and keeps the review worktree at depth 3, so the adversarial population is unchanged. This is an inherited non-regression and is recorded, not closed. Extending the audit is not planned: it is a pure function of a path list in a skill shipped to every stack. Whether a project allow rule applies whatever the Bash cwd is stays unverified (row 39 family).
42. `[engineer-verified: "Yes, add the replacement rule (Recommended)"]` The single replacement allow rule in M6 is added. The quote covers adding that one rule. The exposure in row 41 was raised by the CISO review and characterized by the architect consult, and the engineer's earlier "I don’t think this is a real concern. Check with the architect" covers the exposure only.
43. `[engineer-verified: "Follow the architect (Recommended)"]` The extra tests are the architect's set: one trimmed gate-level deny and allow case in `test_deny_reviewer_tree_mutation.py`, and no depth-text tripwire and no allow-entry pin. The quote covers that choice. The case wording is the session's.
44. `[unverified]` Inherited `GIT_DIR`, `GIT_COMMON_DIR` or `GIT_WORK_TREE` can steer the resolution call. The wrapper does not scope them, because the callers that set them (git hooks) do not run it, and scoping would need its own test. The limitation is recorded here and in Out of scope. Traced from git's documented behaviour, not run.

## Critical files

Dispatch split: three sequential `code-writer` phases, then one main-session step. The phases' file sets do not overlap. They run in sequence because Phase 2 and 3 verify with the wrapper Phase 1 creates, and because parallel test runs in the shared worktree would see each other's half-written `_lib.sh` and `install.sh`. Each phase verifies with targeted runs. The full-suite run happens once, under Verification.

The phase verification commands below are not allow-listed, so a code-writer's Bash call for them prompts or is denied. A code-writer returns a denied or prompted command as `not run: <command>` under Still uncertain instead of substituting another form. The main session stages the phase's new files with `git add` (as its own Bash call) and runs that phase's verification itself, where the engineer can approve a prompt. `scripts/list-shell-files.sh` and the shellcheck tests read `git ls-files`, so an unstaged file is never linted.

**Phase 1 — wrapper and test selection** (one `code-writer`)
- `scripts/venv-run.sh` — new, mode 100755, `#!/usr/bin/env bash`, `set -euo pipefail`, built to the algorithm and comments in Approach. The code-writer sets the exec bit on the real file with `chmod +x` through Bash, because the Write tool creates mode 0644. Patterns to reuse (copy inline, do not source):
  - `require-skill-review.sh:294-299`: `git -C` on its own directory, then `dirname`, then `-x`.
  - `stow-packages.sh:21`: the `dirname --` form.
- `claude/.claude/scripts/tests/test_venv_run.py` — new.
  - Module constant `_SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "venv-run.sh"`, the same shape as `test_install_dev.py:40-44`, so the completeness scan resolves it.
  - Fixture: `_init_repo` and `_commit` from `scripts/tests/conftest.py`, under a base directory whose name contains a space (as in `test_worktree_ensure.py:99-109`, including the `.git/info/exclude` entry). Pin `core.hooksPath` to an empty directory as `test_worktree_ensure.py:99-109` does. Copy the wrapper into `<main>/scripts/` with `shutil.copy`, which keeps the mode, and do not `chmod` the copy (the template at `test_pr_cost_section.py:410-433` does, which would hide a lost exec bit on the real file). `_commit` stages only `file.txt`, so the fixture runs an explicit `git add scripts/venv-run.sh` and commit. Then run `git worktree add -b T-1/topic-slug <main>/.claude/worktrees/T-1/topic-slug`, which is four levels deep. The tracker-shaped name `T-1` is chosen to pass the redaction hook; `PROJ-1` also works.
  - Environment: `_base_test_env()` minus `GIT_DIR`, `GIT_WORK_TREE`, `GIT_COMMON_DIR` and `GIT_INDEX_FILE` (the pattern from `test_worktree_ensure.py:63`).
  - Stub: a target file whose name is not `python3` prints `$0`, `pwd -P` and each argument. `<main>/.venv/bin/python3` is a symlink to it.
  - Invoke the copied wrapper directly, not through `bash`, so a lost exec bit fails the success cases.
  - Cases:
    - Success from the four-level worktree, invoked as relative `scripts/venv-run.sh python3 a "b c"`: rc 0. `Path($0).name == "python3"`, which proves no realpath. `$0`'s directory resolves to `<main>/.venv/bin`. The printed cwd equals the worktree's realpath. Both arguments arrive intact. The docstring names GH-1252.
    - Success from the main checkout, rc 0.
    - Success when the wrapper is invoked by absolute path from a cwd outside the repo: rc 0, and the stub reports that cwd. This proves script-location anchoring and no `cd`.
    - Stub exits non-zero (for example 3): the wrapper's rc equals it, so a wrapper that swallows the tool's status fails.
    - No argument and an empty first argument: rc 2 each.
    - Tool `../bin/python3` and a name with an inner `/` such as `sub/tool` (a file that exists under `.venv/bin/sub/`): rc 2 each. The first would otherwise exist, so this proves rejection is by `/` anywhere in the name, not by `-f`.
    - Wrapper copied outside any repo, with `GIT_CEILING_DIRECTORIES=<tmp_path>` and the cwd inside the main checkout: rc 127, which proves the wrapper is not cwd-anchored.
    - No `.venv` in main, with a decoy `.venv/bin/python3` inside the worktree, run from the worktree: rc 127, and stderr names the main checkout (compared against `main.resolve()`) and `install-dev.sh`.
    - Tool not an executable regular file: rc 127. Parametrized over a name that exists on PATH but not in `.venv/bin` (proves no PATH fallback), a mode-644 file (proves `-x`) and a directory (proves `-f`).
  - Every failure case asserts the exact rc (never `!= 0`), empty stdout, exactly one stderr line, `stderr.startswith("venv-run.sh: ")` (not `in`, because bash's own line `scripts/venv-run.sh: line N:` contains the substring), a fragment naming that branch, and that the argument value `b c` does not appear in stderr.
- `claude/.claude/scripts/select-tests.py`
  - Add `ROOT_SCRIPTS_DIR = "scripts"` next to `INSTALL_SH` (around line 328). Its comment names the readers: `test_shellcheck.py` and `test_shell_lib_function_liveness.py` (HOOKS_TESTS_DIR) run `scripts/list-shell-files.sh` and lint or scan every tracked shell file under the directory; `test_venv_run.py` (SCRIPTS_TESTS_DIR) runs `scripts/venv-run.sh` by path.
  - Add the row `(lambda p: _is_under(p, ROOT_SCRIPTS_DIR), (HOOKS_TESTS_DIR, SCRIPTS_TESTS_DIR))` beside the `INSTALL_SH` row (around line 780).
  - Add `ROOT_SCRIPTS_DIR` to the header comment's "see each constant's own comment" list (around lines 691-692).
  - `MAPPED_TOP_LEVEL_DIRS` and `MAPPED_ROOT_CLAUDE_DIRS` do not change.
- `claude/.claude/scripts/tests/test_select_tests.py`. Templates: `test_install_sh_change_selects_hooks_tests` (around line 1147) and `test_sibling_directory_sharing_scripts_dir_prefix_does_not_match` (around line 484). New tests:
  - `scripts/venv-run.sh` selects HOOKS_TESTS_DIR and SCRIPTS_TESTS_DIR and is not a full-suite run.
  - The nested `scripts/dev/fork-topology-probe.sh` selects the same.
  - `scripts-old/x.sh` falls open with reason `unmatched-path`.
  - In `TestRuleTablePathFidelity`, `ROOT_SCRIPTS_DIR` exists as a directory.
  - Do not touch `_KNOWN_CROSS_DOMAIN_READS` (row 23).
- Phase 1 verification, run by the main session after it stages the new files: `scripts/venv-run.sh pytest claude/.claude/scripts/tests/test_venv_run.py claude/.claude/scripts/tests/test_select_tests.py`, then `scripts/venv-run.sh shellcheck scripts/venv-run.sh`, then `git ls-files -s scripts/venv-run.sh` shows mode `100755`.

**Phase 2 — runner-skip lists** (one `code-writer`)
- `claude/.claude/hooks/_lib.sh:1852` — append `venv-run.sh` to the case list. Add one line above it: `# venv-run.sh is claude-config's contributor runner (scripts/venv-run.sh), which execs a .venv/bin tool.`
- `claude/.claude/hooks/deny-repo-relocation.sh:137` — the same word, so the two lists stay identical.
- `claude/.claude/hooks/tests/test_lib.py`, `TestFragmentCommandWord` (around line 3383), reusing the `_fragment_command_word` helper at line 3353. Two cases:
  - `scripts/venv-run.sh ruff check --fix x.py` resolves to `ruff`.
  - `scripts/venv-run.sh python3 -m black src/x.py` resolves to `black`.
- `claude/.claude/hooks/tests/test_deny_reviewer_tree_mutation.py`, `TestCommandWordResolution` (around line 795-846), one case per runner as the existing cases do. Two cases:
  - Deny `scripts/venv-run.sh ruff format x.py` for a review-only agent.
  - Allow `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/`, the documented reviewer command, so a false deny fails.
- The `deny-repo-relocation.sh` edit is parity-only: its verdict is the same with or without the entry, so no behavioral test can exist for it, and Verification step 6 is its check.
- Phase 2 verification, run by the main session: `scripts/venv-run.sh pytest claude/.claude/hooks/tests/test_lib.py claude/.claude/hooks/tests/test_deny_repo_relocation.py claude/.claude/hooks/tests/test_deny_reviewer_tree_mutation.py`. Then the session runs `claude-hook-review:claude-hook-review` on this diff with row 18's tier check.

**Phase 3 — documentation and hints** (one `code-writer`)
- `CLAUDE.md`
  - Lines 12-15 use the wrapper: `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py`, `scripts/venv-run.sh pytest claude/.claude/ claude-skills/`, `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/`, `scripts/list-shell-files.sh | xargs -0 scripts/venv-run.sh shellcheck`.
  - Line 11's comment becomes "run from the main checkout's root". This is my proposal, because the wrapper reads only `<main>/.venv` (row 32).
  - Lines 35-37: replace "the worktree-relative `.venv` paths" with "how `scripts/venv-run.sh` reaches the shared `.venv` from a linked worktree".
- `README.md`
  - Lines 515-517 and 544 use the wrapper form.
  - Line 529: "The bare `scripts/venv-run.sh pytest claude/.claude/ claude-skills/` command".
  - Line 523 becomes: "The `.venv` lives only in the main checkout's root, and linked worktrees never inherit it. `scripts/venv-run.sh <tool> [args...]` finds that `.venv` through git and runs `<tool>` from its `bin/` directory, so the commands above work unchanged when run from the root of the main checkout or of a linked worktree at any depth."
  - Line 547: drop the "Same worktree-relative substitution…" sentence and keep the rest.
- `install-dev.sh:113-115` and `install.sh:954,970` — swap the `.venv/bin/` prefix for `scripts/venv-run.sh ` and keep each line's existing arguments. Keep both `INSTALL_TEST_FIXTURE` markers.
- `CHANGELOG.md` — `## [Unreleased]` / `### Changed`, appended as new entries:
  - One entry: contributor commands now go through `scripts/venv-run.sh`, which works from a worktree at any depth. The two select-tests allow rules are replaced by one, so the `.venv/bin/python3` and `../../../.venv/bin/python3` forms now prompt. Sub-bullet: `venv-run.sh` joins the runner words the command-word gates look past, so `scripts/venv-run.sh ruff --fix` is denied to review-only agents.
  - A second entry: a change under repo-root `scripts/` now selects the hooks and scripts tests instead of falling open to the full suite.
- Phase 3 verification, run by the main session: `scripts/venv-run.sh pytest claude/.claude/hooks/tests/test_install_sh_contributor_intent_prompt.py claude/.claude/scripts/tests/test_install_dev.py`, then `scripts/venv-run.sh shellcheck install.sh install-dev.sh`.

**Main-session step** (not delegated, because the edit triggers row 33's ask)
- `.claude/settings.json` — replace lines 11-12 with the single entry `"Bash(scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py)"`. Leave lines 10 and 13 untouched. Then run `/review-permissions`.
- `.claude/plans/venv-run-wrapper.md` — this plan.

That is 15 files in total; this list is the change's file limit.

## Verification

0. Stage every new file with `git add` (its own Bash call) before steps 1, 3 and 4: the shellcheck tests and `list-shell-files.sh` read `git ls-files`, and `git ls-files -s` prints nothing for an unstaged file.
1. Final scoped test run from this worktree, which sits at `.claude/worktrees/GH-1252/venv-run-wrapper` — four levels deep, so this run is itself the end-to-end check of the fix: `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py`. This is the newly allow-listed form. From the main tree, the command documented until now is `.venv/bin/python3 claude/.claude/scripts/select-tests.py`; after this PR CLAUDE.md documents the same wrapper string there. Expect reason `global-trigger` and a full-suite run, because `select-tests.py` changed (row 24). That run covers:
   - `test_review_pr_post.py` around lines 867-935
   - `TestCrossDomainReadCompleteness`, which now checks the new row against three test files
   - `test_shellcheck.py`
   - `test_hook_alignment.py`'s settings wildcard tests
2. `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/`
3. `scripts/list-shell-files.sh | xargs -0 scripts/venv-run.sh shellcheck`
4. `git ls-files -s scripts/venv-run.sh` shows mode `100755`.
5. Grep `\.\./\.\./\.\./\.venv` outside `.claude/plans/`, `docs/case-studies/`, `docs/reports/` and `CHANGELOG.md` (whose new entry names the old form on purpose). The only hits should be `reviewer_yield.py:285` and `test_transcript_reviewer_yield.py:1708-1712`.
6. Grep the runner case lines in `_lib.sh` and `deny-repo-relocation.sh` and confirm the two lists are identical.
7. Reviews:
   - `claude-hook-review:claude-hook-review` on Phase 2
   - `/review-permissions` on the settings edit
   - `ai-instruction-and-memory-files` on the `CLAUDE.md` diff (per plan-it Step 5)
   - then `/code-review`

## Out of scope

- The conditions O1-O3 under the assumption ledger's givens: flat branch naming, per-worktree venvs, and lowering the git floor. Each is in this repo's reach and is declined here, with what the design becomes without it stated beside the condition.
- `reviewer_yield.py` and its test, which the issue already tracks as a separate fix. Its docstring's "(this repo's own CLAUDE.md idiom)" goes stale with this PR. It keeps handling the old form for historical transcripts, and the new form is not handled until that fix lands.
- `.claude/plans/*` other than this plan, `docs/case-studies/*`, `docs/reports/*`, and the dated comment at `evals/measure_subagent_model_resolution.py:82`. These are historical records.
- The `.claude/settings.json:10` and `:13` allow rules.
- Allow-listing the shellcheck pipeline. It is not allow-listed today, and an exact-match rule does not fit a pipeline.
- Extending `test_no_bash4_constructs.py` to cover repo-root scripts.
- Making `install-dev.sh` refuse to run from a linked worktree (row 32). The wrapper's missing-venv message points to the main checkout instead.
- A parity test for the two duplicated runner lists (an observation, not a decision).
- Mapping `install-dev.sh` in `select-tests.py`; it still falls open to the full suite.
- Making the pytest arguments consistent across sites: the install hints use `claude/.claude/`, while CLAUDE.md and README use `claude/.claude/ claude-skills/`.
- Adding `venv-run.sh` to `SHELL_WRAPPER_HEADS` in `test_review_pr_post.py`. Doing so would fail the new allow entry (row 15).
- A test that fails when README, CLAUDE.md, the install scripts or `.claude/settings.json` regain `../`-counted `.venv` text. A depth-counted path now fails loudly with "No such file" and the old forms prompt, so the regression is not silent (row 43).
- A test pinning every `venv-run.sh`-headed `permissions.allow` entry to the one approved string. Every settings edit already triggers `ask-review-permissions.sh` and `/review-permissions`, and the `*` ban test covers wildcards (row 43).
- Extending `audit-execution-surface.py` to treat files named by an exact allow rule as execution surface. The audit is a pure function of a path list in a skill shipped to every stack, and the exposure is inherited (row 41).
- Scoping `GIT_DIR`, `GIT_COMMON_DIR` and `GIT_WORK_TREE` for the wrapper's resolution call, and requiring the common dir's basename to be `.git` so bare and separate-git-dir layouts fail closed. Both accept the same assumption as the three sites that take `dirname` of the common dir (`_lib.sh:588-601`, `worktree-removal-status.sh:50`, `require-skill-review.sh:294-299`) and would need their own tests (row 44).
- A guard for git older than 2.31 (row 12).
- Supporting an invocation through a symlink to the wrapper. The wrapper anchors on the directory of the path it was run by, so a symlink on `PATH` resolves from the symlink's directory.
- The `install-dev.sh` hint inside a linked worktree: run there, it still creates a worktree-local `.venv`, prints its hints, and the first hint then fails with the wrapper's message naming the main checkout. This is a known behaviour change, handled by the wrapper's message (row 32).
- Allowing `scripts/venv-run.sh pip install -r requirements-dev.txt` through `deny-network-installs.sh`. That gate would deny it, because the wrapper word leaves a leftover token. No documented command uses the wrapper for pip, and `install-dev.sh` calls `.venv/bin/pip` directly.
