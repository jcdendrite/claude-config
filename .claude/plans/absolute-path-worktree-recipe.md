# Plan: absolute-path worktree recipe (GH-1234)

## Context

Goal: make `branch-management`'s worktree-creation recipe safe to run from a session already anchored in a linked worktree, so the new worktree no longer nests inside the current one.

Ask: GitHub issue jcdendrite/claude-config#1234, "branch-management worktree recipe nests the new worktree when run from inside a worktree". Items 1 (fix the skill recipe) and 2 (fix the hook's suggested command) are the ask; item 3 (a guard) is optional in the issue and not planned. Base-ref question: engineer said "I think so, but as the architect and give it the context of the issue please". Sandbox question: engineer said "Ask the architect".

Why now: the issue reports a session that followed the recipe from inside a worktree, got a nested worktree at `<current-worktree>/.claude/worktrees/<branch>`, passed `EnterWorktree` a path that did not exist, and stalled for two calls with no useful error. Intended outcome: the recipe, and the hook message that teaches it, work identically from the main checkout and from a linked worktree.

## Approach

Rewrite `branch-management`'s worktree recipe so it creates the worktree at an absolute path under the main checkout. The agent reads that path off `git worktree list`, starts the branch from a freshly fetched `origin/<default-branch>` with `--no-track`, confirms placement in the same call, then anchors. The worktree gate's main-tree deny message offers the same command with the main root filled in. Both CLAUDE.md sites that repeat the old command now point at the skill instead. Under the Claude Code sandbox the new recipe fails loudly instead of nesting silently, and that compatibility gap stays with GH-1104.

**The recipe** (`branch-management/SKILL.md` § "Anchor the session in the worktree"). It works the same from the main checkout or from a linked worktree:

1. Run `git worktree list --porcelain`. The path on its first `worktree` line is the main checkout; call it `<main-checkout>`. `--porcelain` is unambiguous when the path contains spaces.
2. In one call, run `git fetch origin <default-branch> && git worktree add --no-track -b <branch> "<main-checkout>/.claude/worktrees/<branch>" origin/<default-branch> && git worktree list`. Confirm the new entry's path starts with `<main-checkout>/.claude/worktrees/`. `<default-branch>` is the name `git symbolic-ref --short refs/remotes/origin/HEAD` prints after its `origin/` prefix; if `origin/HEAD` is unset, ask the engineer.
3. Run `EnterWorktree{path: "<main-checkout>/.claude/worktrees/<branch>"}`, passing the same literal path, as the very next call.

Rules that go with the recipe, one sentence each:
- `origin/<default-branch>` starts the branch at the fresh default tip, not at the current `HEAD`. Inside a linked worktree, `HEAD` is that worktree's branch.
- `--no-track` keeps `origin/<default-branch>` from becoming the new branch's upstream.
- Never pass a cwd-relative path. Run from inside a linked worktree, it nests the new worktree inside that one.
- Type `<main-checkout>` as literal text, with no `$(...)`. The harness refuses command substitution in an anchored session (G1).
- A refused call, a failed `fetch`, or an "already exists" error is reported or resolved by entering the existing worktree from `git worktree list`; it never licenses the relative form or the old no-start-point form.

If the harness refuses the chained call, split it into the same statements as separate literal calls: `git fetch`, `git worktree add`, `git worktree list`. Each statement is free of Triggers A–E, so the split needs no new mechanism.

**Draft of the lead-in and pointer text.** The lead-in at `SKILL.md:64-66` becomes a sentence that introduces the three steps and attaches "very next call" to step 3. The paragraph at `SKILL.md:72-73` keeps only what step 3 does not already say. "Branch from a fresh default" gets one clause folded into its existing sentence at `:55-56`: a branch that will live in a linked worktree skips that section's `checkout`/`pull` commands, because the recipe below fetches and starts from the default tip itself.

**Why not `EnterWorktree` in create mode.** Its documented parameters are only `name` and `path`, and its base ref comes from the `worktree.baseRef` setting. It cannot pass `--no-track` or pick the start point per call (row 26).

**Base ref.** The engineer leaned toward yes and delegated the call (row 15), and this plan includes `origin/<default-branch>`. Without a start point, the add bases the branch on `HEAD`. Run from a linked worktree, which is the case this issue fixes, `HEAD` is the current feature branch. That carries in exactly the baggage "Branch from a fresh default" forbids. That section's own `git checkout main && git pull --ff-only` cannot serve a worktree flow either, because the gate denies both on the main tree (row 2). `--no-track` and the chained `fetch` are the architect's additions. A remote-tracking start point would otherwise become the branch's upstream (row 6). This repo's own tooling reads "has an upstream" as "has been pushed" (row 7).

**The hook message** (`require-worktree-for-git-writes.sh:438`). It interpolates the main root as `${REPO_GIT_COMMON_DIR%/*}` (no fork; same value as the dirname of the common dir) into the full recipe command: `git fetch origin <default-branch> && git worktree add --no-track -b <branch> "<main-root>/.claude/worktrees/<branch>" origin/<default-branch>`, with `<branch>` and `<default-branch>` left as placeholders and the path double-quoted. It applies the same interpolation to the message's "cd into an existing worktree under .claude/worktrees/" clause. "(that specific command is allowed on the main tree)" becomes "(allowed from any tree)". The replacement stays on one physical line and avoids the words "untracked", "isolation", and "spawn an agent", which existing tests assert absent from this reason. The gate already allows `git worktree` from any cwd, which answers the issue's open question (row 1). A worktree session reaches this deny through `cd <main> && git <write>` (row 3). In that case `$REPO_ROOT` names the linked worktree, so interpolating it would reproduce the nesting (row 4). The interpolation also fixes the relative suggestion for a session whose cwd is a subdirectory of the main tree.

**CLAUDE.md sites.** `claude/.claude/CLAUDE.md:172` and `CLAUDE.md:52-54` prescribe the same relative command from whatever cwd the session holds (row 17). This is the same bug shape the skill fix addresses. Both sites already name `branch-management`, so each one's literal command becomes a pointer to the skill. That leaves the skill as the recipe's only prose home.

**Sandbox (delegated, row 16).** The plan carries no sandbox-specific text and no sandbox-specific step.
- Trial C4 ran exactly the issue's proposed shape and failed with `Read-only file system` (row 11). The old relative form most likely succeeds under the sandbox by nesting (row 12, inferred). So a sandboxed consumer trades silent nesting for a loud failure, never the reverse.
- M5's rule bars the one dangerous recovery: falling back to a relative path.
- The remedy is sandbox-adoption design that GH-1104 owns (row 14). It could be a write-set grant for `<main-checkout>/.claude/worktrees/`, or creating worktrees from the main checkout.
- No settings file on this machine enables the sandbox (row 13). Nothing in this plan depends on whether the engineer's sessions run sandboxed, so this leaves no open decision.

**Reconciling the case study.** `claude-code-sandbox-write-boundary.md:130-133` blames the cwd-relative convention for the C4 failure. C4's actual command was the main-anchored absolute form (`sandbox-spike.md:169`). Read charitably, the sentence says the convention's intended sibling target is unreachable from inside a worktree. The issue and C4 agree: relative nests, and absolute fails under the sandbox.

**Dispatch split.** One `code-writer` dispatch covers all seven Critical files, after Verification steps 1–3 pass, and the dispatch prompt orders the work tests-first (step 4). The hook message must carry the skill's command shape, and the CHANGELOG bullet describes it. The CLAUDE.md pointers are one-sentence edits. Splitting would mean restating the shape in every prompt.

### Assumption ledger

**Root:** `branch-management`'s worktree recipe (`SKILL.md:68`) prescribes `git worktree add .claude/worktrees/<branch> -b <branch>`. So do two always-loaded instruction sites (`claude/.claude/CLAUDE.md:172`, `CLAUDE.md:52-54`) and the worktree gate's main-tree deny message (`require-worktree-for-git-writes.sh:438`). When the session sits in a linked worktree, the cwd-relative path nests the new worktree inside the current one. From that cwd, the missing start point also bases the new branch on the current worktree's branch.

**Givens:**
- G1. Claude Code's worktree-isolation Bash guard refuses a bare `$(git …)` substitution in a worktree-anchored session, whether or not it is assigned (Trigger E). The harness owns this guard, and nothing in this repo can intercept it. [verified: `docs/worktree-bash-guard.md:3-9,21-27`]
- G2. With the Claude Code sandbox enabled, writes from a linked worktree reach only that worktree, the shared `.git`, and `$TMPDIR`. Anthropic's sandbox sets this write set. [verified: `docs/case-studies/claude-code-sandbox-write-boundary.md:119-129,153-155`]
- G3. git resolves a relative `git worktree add` path against the process cwd. With `-b` and no start point, the branch starts at that cwd's `HEAD`. Git defines both behaviors. [unverified: the issue's reported symptom; Verification step 2 observes both]

**Rows:**
1. The worktree gate allows every `git worktree` subcommand from any cwd and to any target path, because its allowlist match runs before any cwd or `-C` check. It allows the nested form and the absolute form alike. [verified: `claude/.claude/hooks/require-worktree-for-git-writes.sh:367-372`; `claude/.claude/hooks/_lib.sh:3738`]
2. `fetch` is on the same allowlist; `checkout` and `pull` are not. [verified: `_lib.sh:3693-3739`]
3. The `:438` deny fires only when a write's effective cwd is the main working tree. A worktree session reaches it through `cd <main> && git <write>`, because any `cd` routes the command past the fast path into full parsing. [verified: hook `:243-261,386-438`]
4. `$REPO_ROOT` is `--show-toplevel` of the session cwd, so in row 3's case it names the linked worktree. `$REPO_GIT_COMMON_DIR` is the absolute shared `.git`, the same from any tree. Its dirname is the main root, the formula `_lib_main_repo_root` documents. [verified: hook `:213,230-233`; `_lib.sh:580-601`]
5. `git worktree list` prints the main worktree first. [verified: git-worktree(1) 2.43.0 "list" ("The main worktree is listed first"), read by the `staff-platform-engineer` review this session]
6. When the start point is a remote-tracking branch, `git worktree add -b` makes it the new branch's upstream unless `--no-track` is passed. [verified: git-worktree(1) 2.43.0 "--[no-]track", read by the `staff-platform-engineer` review this session]
7. An upstream of `origin/<default-branch>` would mislead this repo's tooling. `ready-for-review` step 8 halts on "no remote tracking" to catch an unpushed branch. `advance-past-commit-stall.sh` reads `@{u}..HEAD`, and its design assumes no upstream before the first push. [verified: `claude-skills/skills/ready-for-review/SKILL.md:146`; `claude/.claude/hooks/advance-past-commit-stall.sh:190-212`]
8. `.claude/worktrees/` is gitignored, so the outer worktree's `git status` never shows a nested one. In this repo the nesting is silent. [verified: `.gitignore:13`]
9. The skill requires `EnterWorktree` as the very next call after the add, before any other command, read-only ones included. [verified: `claude-skills/skills/branch-management/SKILL.md:64-76`]
10. `claude/.claude/hooks/nudge-worktree-anchor.sh:168` and `claude-skills/skills/ready-for-review/SKILL.md:32` both cite the heading "Anchor the session in the worktree", so its text stays byte-identical. [verified: Grep this session; `.claude/rules/citation-grammar.md`]
11. Sandbox trial C4 ran `git -C <clone> worktree add <clone>/.claude/worktrees/spike-compat-2 -b spike-compat-2` from inside a linked worktree, which is the issue's proposed shape. It failed with `Read-only file system`. [verified: `.claude/plans/sandbox-spike.md:169`; case study `:125-129,417`]
12. Under the sandbox, the cwd-relative form run from a linked worktree likely succeeds by nesting. Its target lies under the working directory and its admin entry under the shared `.git`, and G2 allows writes to both. [unverified: inferred from G2; never trialed]
13. No settings file on this machine sets a `sandbox` key at user, per-account, or repo scope, and no managed-settings directory exists. The shipped `claude/.claude/` settings carry none either. Another stow consumer can still enable it. [verified: Grep this session]
14. GH-1104 owns sandbox adoption. The case study lists C4 among adoption's compatibility costs and makes no recommendation. [verified: case study `:530-541`]
15. Asked whether to include `origin/<default-branch>`, the engineer answered: [engineer-verified: "I think so, but as the architect and give it the context of the issue please"]. The quote covers only a lean toward including it and delegation of the final call.
16. On the sandbox case, the engineer answered [engineer-verified: "Ask the architect"]. The quote covers delegation only.
17. Two always-loaded sites prescribe the same cwd-relative command, and both already name `branch-management`. `claude/.claude/CLAUDE.md:172` loads in every stow consumer's every session and tells a parent to create a worktree before a dispatch. `CLAUDE.md:52-54` loads in this repo, where enforcement keeps sessions anchored in worktrees. [verified: read this session]
18. No test pins the `:438` text or either CLAUDE.md sentence; three tests assert only that "untracked", "isolation", and "spawn an agent" stay absent from the reason. "allowed on the main tree" appears only at hook `:438` and `claude/.claude/CLAUDE.md:172`. [verified: Grep across test files and the repo outside `.claude/plans/`]
19. The harness's worktree-isolation guard does not refuse a literal `git worktree add` whose target lies outside the anchored worktree. [unverified: `docs/worktree-bash-guard.md:40-45` records a 2.1.222 widening of isolation to "Bash in every session type" against "destructive git commands against the main checkout", and no trial covers a non-destructive out-of-anchor target; Verification step 1 settles it]
20. `README.md:300` and `CONTRIBUTING.md:14` are fenced shell examples for a human at the main checkout. Each sits in a section that starts work from the main tree and is followed by `cd .claude/worktrees/<name>`. [verified: `README.md:293-307`; `CONTRIBUTING.md:9-17`]
24. `README.md:307` is a prose paragraph with no `cd` after it. Its third sentence restates the "create the worktree yourself, then dispatch" instruction that `claude/.claude/CLAUDE.md:172` carries, with the same relative command. [verified: `README.md:307`, read by the architect consult this session]
26. `EnterWorktree`'s documented parameters are `name` (creates a new worktree and branch under `.claude/worktrees/`) and `path` (enters an existing one). The base ref comes from the `worktree.baseRef` setting. It has no start-point or `--no-track` argument. [verified: the tool's schema description, loaded this session]
25. Asked whether to add `README.md:307` as Critical file 7, the engineer selected [engineer-verified: "Add README.md:307 (Recommended)"]. The quote covers adding that file; the one-sentence replacement wording is the architect's.
21. This change is consumer-visible in both stow packages. `CHANGELOG.md`'s single `[Unreleased]` release carries a `### Fixed` section. [verified: `CHANGELOG.md:5,253`]
23. Asked whether Critical files 4 (`claude/.claude/CLAUDE.md:172`), 5 (`CLAUDE.md:52-54`), and 6 (`CHANGELOG.md`), which go beyond the Ask, stay in this change, the engineer answered: [engineer-verified: "I think these all make sense but ask the architect"]. The quote covers a lean toward keeping all three and delegation of the final call. The architect's keep verdict and its changes to items 4 and 6 are the architect's own, not the engineer's.
22. The offered label "Include origin/<default> (Recommended)" and its description were the session's proposal, and the engineer did not select them. They carry no engineer weight. `--no-track` and the chained `git fetch` are the architect's additions. [unverified]

**Mechanisms:**
- M1. The recipe: discover `<main-checkout>` from `git worktree list`; make one chained add call; then run `EnterWorktree` on the same literal absolute path. anchors: root, G3, row5, row9. Lighter primitives, each set aside:
  - (a) A prose rule to run the old command from the main checkout. An anchored session can reach the main checkout only by leaving its anchor, which this same section says breaks the anchor. anchors: row9.
  - (b) `git -C <main-checkout> worktree add .claude/worktrees/<branch> …`. It needs the same discovered path, plus a second, separately composed absolute path for `EnterWorktree`. The absolute form passes one string to both calls. anchors: row9.
  - (c) A path derived inline from `$(git rev-parse --path-format=absolute --git-common-dir)`. Trigger E refuses it in exactly the anchored session this fix targets. anchors: G1.
  - Heavier option, held as a fallback: a `~/.claude/scripts/` wrapper, which needs a script, a test file, and a `docs/scripts.md` entry. Each call above is a literal statement with none of Triggers A–E. The only value carried between calls is one path the agent reads and pastes, which the skill already does for `EnterWorktree{path}`. Adopt the wrapper only if Verification step 1 shows a refusal. anchors: G1, row19.
- M2. The start point `origin/<default-branch>`, refreshed by the chained `git fetch`, plus the one-sentence pointer in "Branch from a fresh default". anchors: root, G3, row2, row15.
- M3. `--no-track`. anchors: row6, row7.
- M4. `git worktree list` chained after the add in the same call. A separate verification call would break row 9's rule, and verifying after `EnterWorktree` would anchor the session in the wrong tree first. anchors: root, row8, row9.
- M5. The one-sentence failure-mode rule against cwd-relative paths. It also blocks the natural fallback when the absolute form fails under a sandbox. anchors: root, row8, row12.
- M6. The hook message interpolates `$(dirname "$REPO_GIT_COMMON_DIR")`. anchors: root, row1, row3, row4. Alternatives set aside:
  - `$REPO_ROOT` reproduces the nesting (row4).
  - `_lib_main_repo_root` computes the same value with a second identical `rev-parse` the hook already ran at `:233` (row4).
  - Resolving `<default-branch>` inside the hook adds a subprocess on the deny path for a value the agent already resolves.
  The one command line the message shares with the skill falls under CLAUDE.md's Single-source-of-truth exception (3), because a deny message cannot load a skill.
- M7. Hook tests: allow-route and deny-message cases for the main tree. The deny-message tests pin the shape the skill and the hook share, since a deny message cannot load the skill. anchors: row1, row3, row4.
- M8. Both CLAUDE.md sites and `README.md:307` become pointers to `branch-management`. Fixing the literal in place would keep four prose homes for one recipe. That duplication is how these sites came to prescribe the defect alongside the skill. anchors: root, row17, row18, row24.
- M9. No sandbox-specific text or step. anchors: G2, row11, row12, row13, row14, row16.
- M10. A CHANGELOG `### Fixed` bullet, including the sandbox failure disclosure. anchors: row11, row14, row21.

## Critical files

1. `claude-skills/skills/branch-management/SKILL.md`
   - § "Anchor the session in the worktree" (`:62-70`): replace the `:68` command and the `:70` `EnterWorktree` line with M1–M5. Keep the heading byte-identical (row 10) and leave `:72-102` unchanged.
   - § "Branch from a fresh default" (`:45-60`): add M2's one-sentence pointer.
   - Gate: `/skill-review` (hook-enforced).
2. `claude/.claude/hooks/require-worktree-for-git-writes.sh:438`: apply M6. Reuse `$REPO_GIT_COMMON_DIR` (`:230-233`), which needs no new git call and no `_lib.sh` change. Gate: `claude-hook-review:claude-hook-review`.
3. `claude/.claude/hooks/tests/test_require_worktree_for_git_writes.py`: add M7's two tests beside `test_worktree_cwd_chained_cd_to_main_merge_denied` (`:808-819`), the precedent for a worktree session that `cd`s to main. Reuse `opted_in_with_worktree` (`claude/.claude/hooks/tests/conftest.py:540-554`), `run_hook`, and `run_hook_reason` (`claude/.claude/tests/helpers.py:235-264`). Use `git_toplevel` (`helpers.py:790-799`) as an oracle independent of the hook's formula. Leave the relative fixture at `:216` unchanged.
   - Add `git_toplevel` to the file's `from helpers import (...)` block.
   - `test_recipe_add_command_allowed_on_main_tree_routes`, parametrized over two routes where the allowlist is load-bearing: cwd = main root, and a worktree-anchored session running `cd <main> && <chain>`. `<chain>` is M1's full chained call with an absolute target under the main root. Both return `"allow"`. The fixture's worktree holds no lock, so the second route takes the parser path.
   - `test_main_tree_deny_suggests_main_root_path`, parametrized over four routes: cwd = main root, cwd = a subdirectory of main, a worktree session via `cd <main> && git commit -m foo`, and a worktree nested at `<main>/.claude/worktrees/GH-1/x`. Each reason:
     - contains `f"{git_toplevel(main)}/.claude/worktrees/<branch>"`;
     - contains the tokens `--no-track` and `origin/`;
     - does not contain `worktree add .claude/worktrees/`;
     - for the worktree routes, does not contain `f"{git_toplevel(worktree)}/.claude/worktrees/"`.
   - `test_main_tree_deny_quotes_spaced_main_path`: a main repo under a directory whose name contains a space. The add command extracted from the reason survives `shlex.split` with the path as a single argument.
   - Dispatch order: the writer adds these tests, runs them, and returns the failing output for the deny-message tests before editing the hook (Verification step 4).
4. `claude/.claude/CLAUDE.md:172`: replace "pick a slug per the `branch-management` skill, run `git worktree add .claude/worktrees/<slug> -b <slug>` (allowed on the main tree even under worktree enforcement)" with "pick a branch name and create its worktree per the `branch-management` skill (`git worktree` is exempt from the enforcement gate)". The kept clause carries the dropped parenthetical's exemption in the always-loaded file, since the hook's `:438` text only appears after a different write is denied. Gate: `ai-instruction-and-memory-files`.
5. `CLAUDE.md:52-54`: replace the sentence with "A worktree created per the `branch-management` skill (or an agent with `isolation: worktree`) satisfies it." Gate: `ai-instruction-and-memory-files`.
6. `CHANGELOG.md`: add one bullet under `### Fixed` (`:253`). It says:
   - the `branch-management` recipe and the worktree gate's deny message now target an absolute path under the main checkout, from a fresh `origin/<default-branch>` with no upstream;
   - the cwd-relative form nested worktrees when run from a linked worktree;
   - the `claude/.claude/CLAUDE.md` Agent Briefing line now points at the skill;
   - a sandboxed consumer running the recipe from a linked worktree gets `Read-only file system` (row 11), tracked in GH-1104 (row 14). The bullet does not claim the old form worked under the sandbox (row 12 is inferred);
   - the change is live on `git pull` with no re-install.

   The root `CLAUDE.md` edit stays out of the bullet, since stow consumers never see it.

7. `README.md:307`, last sentence only: replace "create the worktree yourself with `git worktree add .claude/worktrees/<slug> -b <slug>` first, then dispatch the agent into that path" with "create the worktree yourself first per the `branch-management` skill, then dispatch the agent into the new worktree". It restates the sentence file 4 fixes, in prose with no `cd` after it (row 24). No test pins it. Gate: `/code-review`.

Run `/code-review` over the whole diff.

## Verification

Steps 1–3 run in the session before the `code-writer` dispatch, because a failure in either probe changes the design.

1. **Git-semantics probe (G3; rows 5–6 are already verified against git-worktree(1) 2.43.0).** It has no live-repo side effects, so it runs first. Write one throwaway script in a `mktemp -d` scratch directory and save its output to a file there. If the harness refuses to run it, hand it to the engineer to run via `!`.
   - Pin the environment: `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_SYSTEM=/dev/null`, `git init -b main`, commit identity via `-c user.name=… -c user.email=…`, and every path inside the scratch directory.
   - Setup: a bare `origin.git`. A clone whose directory name contains a space, as `main-checkout`, with one commit pushed to `main`. A linked worktree `main-checkout/.claude/worktrees/feat` on branch `feat`, with one extra commit. A second commit pushed to `origin`'s `main` from another clone.
   - From inside `feat`, assert only what the man page does not state:
     - the recipe with `<branch>` = `GH-1/new` (slash-named) lists a path starting with `main-checkout/.claude/worktrees/`, not a path under `feat/`, and the first `worktree` line of `git worktree list --porcelain` is `main-checkout`;
     - `GH-1/new` starts at the second pushed commit, not at `feat`'s tip, which shows the chained `fetch` refreshed `origin/main`;
     - `git rev-parse --abbrev-ref GH-1/new@{upstream}` exits non-zero;
     - control: the same add without `--no-track` gives an upstream of `origin/main`;
     - negative control: the old `git worktree add .claude/worktrees/old -b old` lands under `feat/.claude/worktrees/old`, and an add without a start point bases the branch on `feat`'s tip (G3).
2. **Harness probe (row 19), against the final recipe shape.**
   - From this worktree-anchored session, run `git worktree list --porcelain`, then `git fetch origin main && git worktree add --no-track -b GH-1234/recipe-probe "<main-checkout>/.claude/worktrees/GH-1234/recipe-probe" origin/main && git worktree list`.
   - Run the second call twice, on two probe names (`GH-1234/recipe-probe` and `GH-1234/recipe-probe-2`), because `docs/worktree-bash-guard.md` says one non-refusal is weak evidence.
   - Pass: neither call is refused, and each probe path starts with `<main-checkout>/.claude/worktrees/`.
   - Do not run `EnterWorktree` on a probe.
   - If the harness refuses, switch the skill text to the split-call form named in the Approach, rerun the probe in that form, and report the refusal to the engineer.
3. **Probe teardown.** `git worktree remove` (no `--force`) each probe path, then `git branch -d` each probe branch (each has zero commits of its own), then `git worktree list` shows neither. Both commands refuse rather than destroy a dirty or unmerged probe. Run it immediately after step 2, so no probe outlives the probe it belongs to.
4. **Red.** The `code-writer` dispatch adds the Critical file 3 tests first and runs `<main-checkout>/.venv/bin/pytest claude/.claude/hooks/tests/test_require_worktree_for_git_writes.py -k "recipe_add_command or main_tree_deny"` before editing the hook. The deny-message tests fail against the current `:438`. The allow tests pass already, since they pin existing behavior, and the return includes the failing output.
5. **Green.** The dispatch then applies all Critical-files edits and reruns step 4. Every test passes.
6. **Scoped suite and lint.** From this worktree, run:
   - `<main-checkout>/.venv/bin/python3 claude/.claude/scripts/select-tests.py`. README.md § Tests covers the cross-worktree `.venv` path.
   - `<main-checkout>/.venv/bin/ruff check claude/.claude/hooks/tests/test_require_worktree_for_git_writes.py`.
   - `<main-checkout>/.venv/bin/shellcheck claude/.claude/hooks/require-worktree-for-git-writes.sh`.
7. **Residual sweep.** `git grep -n "worktree add .claude/worktrees" -- ':!.claude/plans'` returns only `README.md:300`, `CONTRIBUTING.md:14`, `test_require_worktree_for_git_writes.py:216`, and `test_parse_git_command.py:301`. This is a literal-text check, not a complete detector: read `CLAUDE.md:51-55` directly, because a single-line grep misses its wrapped command. Existing tests assert "untracked", "isolation", and "spawn an agent" absent from the main-tree deny reason (`test_require_worktree_for_git_writes.py:84-86,93,102`); the new wording must keep them absent.
8. **Review gates.** Run `/skill-review` (SKILL.md), `claude-hook-review:claude-hook-review` (the hook), `ai-instruction-and-memory-files` (both CLAUDE.md files), then `/code-review`.

## Out of scope

- **Issue item 3**, a guard that denies or warns on `git worktree add` into another linked worktree. The issue's author asked for items 1 and 2 first, revisiting 3 only if nesting recurs.
- **Sandbox compatibility for sibling-worktree creation.** GH-1104 owns it (row 14), and M9 covers this plan's side.
- **`README.md:300` and `CONTRIBUTING.md:14`.** These are human shell examples at the main checkout, followed by `cd` (row 20). The issue named both as correct.
- **`test_require_worktree_for_git_writes.py:216` and `test_parse_git_command.py:301`.** They are main-tree and parser fixtures, and path shape is irrelevant to what each pins.
- **`docs/case-studies/claude-code-sandbox-write-boundary.md:130-133`'s attribution.** A case study records a finished trial, so it stays as written. The reconciliation lives in this plan's Approach instead.
- **Bare-repo and `--separate-git-dir` layouts.** There the common dir's parent is not a working tree. The recipe and the hook degrade the same way `_lib_main_repo_root` already does.
- **Observed in passing, for the reviewer:**
  - `claude/.claude/CLAUDE.md:42`'s `$(git merge-base HEAD origin/main)` matches Trigger E in a worktree-anchored session.
  - `README.md:522` says linked worktrees sit "exactly three levels deep", which a branch name containing `/` breaks. This branch is one such name.
