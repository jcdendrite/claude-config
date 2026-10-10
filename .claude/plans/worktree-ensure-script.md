# Plan: one script to create or find a branch's worktree (GH-1234 follow-on)

## Context

Goal: replace the prose worktree-creation recipe in `claude-skills/skills/branch-management/SKILL.md` and the copy-pasteable command in the worktree gate's deny hint (`claude/.claude/hooks/require-worktree-for-git-writes.sh`, line ~438) with one stowed script under `claude/.claude/scripts/` that creates the worktree at an absolute path under the main checkout and tells the agent exactly what to do next, so creating and entering a worktree is deterministic.

This follows branch `GH-1234/absolute-path-worktree-recipe` (issue jcdendrite/claude-config#1234: the cwd-relative `git worktree add` nested a new worktree inside a linked worktree). That branch already implements the prose version (unpushed, no PR). This plan is a separate file and does not edit `.claude/plans/absolute-path-worktree-recipe.md`, which stays a provenance record.

Ask:
1. [engineer-verified: "the script that creates a worktree and enables an agent to enter one should also check if they are already in the worktree. It'll save one turn."]
2. [engineer-verified: "I'm surprised you didn't add a script or two as part of the branch management skill updates to make worktree creation and entering deterministic"]
3. [engineer-verified: "But since only the plan has been reviewed I'd say we should write a new plan."]

Clarifying answers (engineer's words):
- Already-in check: "Wait I think the failed call is on EnterWorktree. Either run a small probe/test or scan the deny log or transcript analysis for this type of issue"
- Scope (recipe and hint vs also the other relative-form sites): "Ask the architect"
- Failed-add handling: "See prior response about architect. Only one spawn for all questions"

## Approach

One stowed script, `~/.claude/scripts/worktree-ensure.sh '<branch>'`, replaces the prose worktree recipe and the gate's main-tree deny hint.
- It finds the branch's worktree, or creates one under the main checkout from a freshly fetched default tip with a single `git worktree add --no-track -b`.
- It prints its state plus the one next step: an `EnterWorktree{path}` call, or `next: none` when the working directory already is that worktree.
- Every refusal it can settle without writing runs before its two writes, the fetch and the add.
- Neither write touches the main checkout's HEAD, index or tracked files (row 20).

**Script contract.**
- **Name:** `worktree-ensure.sh`. "create" would misname two of its three success states.
- **Arguments:** exactly one positional argument.
- **Where git runs:** the script never `cd`s.
  - `check-ref-format` and `collect_all_worktrees`'s listing run in the caller's working directory. Once step 1 unsets the `GIT_*` variables, that reaches the same repository as `<canon-main>` (row 51).
  - Every other git call runs as `git -C "<canon-main>"`. The shared lib's listing stays unchanged.
- **Token rule:** a refusal gets a new token only for a state that, within a standard clone, would otherwise end silently wrong, such as a nested worktree or a half-built tree reported ready. A state that would merely end less informatively reuses an existing token, with git's own message on stderr.
  - Step 5's two tokens, `ref-conflict` and `branch-without-worktree`, are the exceptions. Without them, git's own ref failure would end the add with nothing written (rows 50, 67). Both run before the fetch, so a rerun writes nothing. `ref-conflict` names the existing branch. The engineer chose to keep `ref-conflict` (row 59).

Order of operations:

1. **Environment.** Before the first git call:
   - run `unset GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR GIT_INDEX_FILE` (row 39);
   - run `export LC_ALL=C` (row 68);
   - capture `<canon-cwd>` as `pwd -P`.
2. **Arguments and name** (`worktree_ensure_name_check`, M12).
   - Not exactly one argument: exit 2, `usage`.
   - The name may hold only `A`–`Z`, `a`–`z`, digits, `.`, `_`, `/` and `-`. The bracket expression lists every character, with no ranges (row 52). The set excludes `@{`, so `@{-N}` never reaches git (row 14).
   - Then run `git check-ref-format --branch` and discard its output. Exit 0 passes. Exit 128 is git's invalid-name status. Any other status means git could not run.
   - A name outside the set, or a 128: exit 2, `bad-name`, quoting the name. Any other status: exit 1, `no-repo`, naming git's status (row 54).
3. **Main checkout** (M7). Run `collect_all_worktrees`.
   - Zero records: exit 1, `no-repo`. The message names two causes: the working directory is outside any repository, or the listing failed (row 51).
   - `<canon-main>` is the canonical path of the first record (row 8). In a standard clone that record is the main checkout. Other layouts are unchecked (row 78, Out of scope).
4. **Lookup** (`worktree_ensure_classify`, M12). Only the record holding `<branch>` counts, and no path here touches the network.
   - No record holds it: go on to step 5.
   - The main checkout holds it: exit 1, `in-main`. The message asks the engineer to switch the main checkout to another branch, then rerun (row 73).
   - Its lock reason is exactly `initializing`: exit 1, `add-unfinished`. The message says that add is still running or was killed, so the tree may be incomplete (row 64). Any other lock reason, or none, does not count (row 34).
   - Its path is not a directory holding a `.git` that resolves (`-e`), whatever its `prunable` line says: exit 1, `missing` (rows 34, 56). Step 6's stray test differs on purpose, because it also counts a dangling `.git`.
   - Its canonical path is not under `<canon-main>/.claude/worktrees/` (the comparison includes the trailing slash): exit 1, `outside` (G1). This precedes `current`, so `current` never names a path `EnterWorktree` refuses.
   - Its canonical path equals `<canon-cwd>`: state `current`. Otherwise: state `existing`.
   - A found, usable record ends the run here. The script prints `existing` or `current` and exits 0 before step 5.
5. **Local refs** (`worktree_ensure_ref_check`, M12, M20). The input is `git -C "<canon-main>" for-each-ref --format='%(objectname) %(refname)' refs/heads/`. A failed listing exits 1 with `list-failed`.
   - `refs/heads/<branch>` exists: exit 1, `branch-without-worktree`. The message names the tip SHA and three causes:
     - an earlier create that failed or was killed after git made the branch;
     - a create still in flight that has not registered its worktree yet;
     - a worktree in the middle of a rebase or bisect, which shows a detached HEAD.

     It says `git worktree list` tells these apart, and that attaching or deleting the branch is the engineer's call, with the commands in `docs/scripts.md` of the claude-config checkout (row 90). It prints no `worktree add` command, because one would skip step 6's checks.
   - An existing branch is a path prefix of `<branch>` (`GH-1` for `GH-1/new`), or lies beneath it: exit 1, `ref-conflict`, naming that branch (row 50).
6. **Target** (`worktree_ensure_target_check`, M4, M12). `<target>` is `<canon-main>/.claude/worktrees/<branch>`. The check walks each component from `<canon-main>/.claude` down to `<target>`, in that order, and the first hit refuses:
   1. The component is a symlink, tested with `[[ -L ]]`, so a dangling one counts: `symlinked-path`.
   2. A linked record's canonical path equals it: `path-in-use`. The message embeds `worktree_ensure_describe_record` (rows 15, 61).
   3. It exists and is not a directory: `path-in-use`. Git would fail there after creating the branch (row 65).
   4. It holds a `.git` entry of any type (`-e` or `-L`): `path-in-use`. The message says no registered worktree claims the directory, and that resolving it is the engineer's call (row 66). It names no move or removal. At `.claude` or `.claude/worktrees` the refusal is intended: the directory is then a repository of its own or a leftover, and the script creates no worktree inside another repository.

   After the walk, if `<target>` is a directory with any entry, dotfiles counted: `target-not-empty` (row 15).
   - Records are scanned from index 1 with an explicit length test (row 47).
   - The lib prints the token and the whole message. The script dies with the verdict's first word as its token, so no refusal needs its own dispatch arm.
7. **Default branch.** If `_lib_default_branch_from_origin_head "<canon-main>"` fails: exit 1, `no-origin-head`, telling the agent to ask the engineer.
8. **Fetch.** Run `GIT_TERMINAL_PROMPT=0 git -C "<canon-main>" fetch --quiet origin "+refs/heads/<default>:refs/remotes/origin/<default>"`.
   - The explicit refspec names its own destination, so a narrowed `remote.origin.fetch` cannot leave the ref stale (row 13). The argument starts with `+refs/`, so git cannot parse it as an option.
   - On failure: exit 1, `fetch-failed`. The message names `<default>` and four causes the agent cannot tell apart: the network, authentication, a concurrent fetch holding the ref lock, or a stale `refs/remotes/origin/<default>.lock` left by a killed fetch, which blocks the fetch once the remote's `<default>` has advanced (row 64). It gives no retry instruction.
   - `START_SHA` is `git rev-parse --verify --quiet "refs/remotes/origin/<default>^{commit}"`. Guard it with `|| die fetch-failed`, with the message "fetch reported success but `refs/remotes/origin/<default>` is absent; `git worktree add` did not run" (row 67).
9. **Create.** Run `git -C "<canon-main>" worktree add --no-track -b "<branch>" "<target>" "<START_SHA>"`. Git creates `<target>` and any missing parents itself (row 15). Then re-collect the records, with the zero-record guard, and re-run step 4's lookup. The guard's token is `list-failed`, and its message says the add may have succeeded.
   - `existing` prints `created`. `current` prints `current`.
   - No record holds the branch: exit 1, `add-failed`. The message says:
     - git creates the branch before it checks the path, so `<branch>` may remain at `<START_SHA>`;
     - or a concurrent run for this branch owns it, and `git worktree list` after a moment shows which;
     - a rerun then writes nothing, and reports `branch-without-worktree` or the winner's state.
   - Any other state exits 1 with that state's own token. Three examples:
     - `missing`, when a hook emptied the new worktree (row 56);
     - `add-unfinished`, when a concurrent run is still checking out (row 64);
     - `outside`, when an ancestor swapped for a symlink redirected the add (row 40).

**State decides, not exit status.** A failing post-checkout hook makes `worktree add` exit non-zero without undoing the checkout (row 36). So the re-read records decide success, and git's message stays on stderr. One lookup function serves steps 4 and 9.

**Output.**
- One emit function writes stdout once, after every git call.
- Every git call's stdout and stderr go to stderr. `worktree add` writes `HEAD is now at …` to stdout, so the redirect is load-bearing (row 53).
- On exit 0, stdout is exactly two lines, byte for byte:
  - `created <path>` or `existing <path>`, then `next: EnterWorktree{path: "<path>"}`;
  - `current <path>`, then `next: none; the working directory is this worktree, and EnterWorktree refuses a target equal to the working directory`.
- SKILL.md tells the agent to find the last line that starts with `next:` and read the state from the line just before it.
  - This depends on how the tool presents the two streams. If stderr appears after stdout, a stderr line starting `next:`, such as a repository hook's output, comes last.
  - Verification step 5 records the presentation. If it shows stderr after stdout, the session reports that to the engineer before merge (row 53).
- `<path>` is the record's path exactly as `git worktree list --porcelain` prints it. The state is the text before the first space, and the path is the rest of the line.
- On exit 1 or 2, stdout is empty, and when the script reaches `die`, that one function writes the last stderr line: `worktree-ensure.sh: <token>: <message>`. Any lines before it come from git and the programs git runs, such as hooks (row 36).
- **Failure rule** (SKILL.md owns it):
  - Exit 2 (`usage`, `bad-name`, or no `worktree-ensure.sh:` line): `usage` and `bad-name` refuse before any write. With no line, the script most likely never ran, though a parse error late in the file exits 2 after earlier statements ran (row 54). The agent corrects the call once and reruns, with one argument, or with the name rewritten within the set when it held a character outside it. A second exit 2, or a no-line exit 2 from a call that already had one argument and an in-set name, goes to the engineer with the exit status and stderr.
  - Exit 127 with no line: the agent tells the engineer to re-run `./install.sh`.
  - `in-main`: the agent asks the engineer to switch the main checkout's branch, then reruns.
  - Every other exit 1, any other status, and any run the tool killed: the agent reports the last `worktree-ensure.sh:` line, or the exit status when there is none, and does not rerun.
    - Commands that write are the engineer's to run, wherever they are suggested. Examples include creating or attaching a worktree, deleting a branch, unlocking, removing or pruning a record, deleting a lock file, and moving or removing a directory.
    - Read-only commands such as `git worktree list` are fine.
    - A rerun the engineer asks for writes nothing while a failed or killed run's branch or worktree remains, and reports that state.
  - The writing-command bar is prose only. The gate allows `git worktree` and `git branch` by subcommand name (row 20), so `unlock`, `remove --force`, `prune` and `branch -D` pass it from any tree, and only the default-mode permission prompt backs the bar otherwise. File 8 pins the sentence.
- **Exit codes:**
  - 0: the worktree is ready.
  - 1: a state or failure to report to the engineer.
  - 2: `usage` or `bad-name`. With no `worktree-ensure.sh:` line, a shell syntax error in the composed call, or a script file bash cannot parse, also exits 2 (row 54).
  - 127 with no `worktree-ensure.sh:` line: the script is missing.
  - A timeout kill ends with no such line (Interrupted runs).
  - Any other status: an unguarded failure. Stderr carries the failing command's message.
- **Tokens (16):** `usage`, `bad-name`, `no-repo`, `list-failed`, `in-main`, `add-unfinished`, `missing`, `outside`, `branch-without-worktree`, `ref-conflict`, `symlinked-path`, `path-in-use`, `target-not-empty`, `no-origin-head`, `fetch-failed`, `add-failed`.
- **Header**, modeled on `worktree-removal-status.sh:2-27`.
  - It lists the two writes and their targets:
    - the fetch writes `refs/remotes/origin/<default>`, `FETCH_HEAD` and objects in the shared repository;
    - the add writes the new branch, its administrative entry, and `<target>` with any missing parent directories;
    - neither write touches the main checkout's HEAD, index or tracked files.
  - It states the supported layout: "Assumes a standard clone, where git's first `worktree list` record is the main checkout. Other layouts are unchecked, and a create there can exit 0 with the worktree inside or beside the git directory." (row 78)
  - It adds three trust lines:
    - The default tip's hooks run as they would on `git pull`, under `LC_ALL=C`.
    - ssh prompts are not suppressed, and the Bash tool's timeout is the backstop (row 38).
    - Hooks and text-matched permission rules judge only the outer call, so they cannot see the inner git calls (G2, G5).

**Add-step foundation. Decision: every write-free refusal, then the fetch, then one `worktree add --no-track -b`, with no `mkdir`.** `-b` creates the branch before git checks or creates the path (row 15), so the design question is how to keep a path failure from leaving that branch behind.
- **(a) Chosen.**
  - Step 6 settles every path condition it can check without a write: a symlinked component, a component that a record or a stray `.git` already uses, a component that is not a directory, and a non-empty target.
  - Git creates the target and its parents, so the script creates no directory.
  - The create-only ref settles concurrent runs before git touches a path. It also keeps the tip of a branch that appears after step 5 (rows 45, 67).
  - Both writes run against the shared repository, which the gate allows from any tree (row 20). No git command runs inside `<target>` (row 49). A failing post-checkout hook runs once.
  - **Not re-checked before the add.** The window from step 6 to the add spans the fetch. The plan documents it rather than closing it (row 40), for two reasons:
    - A re-check narrows the window but cannot close it against a same-user writer, which the cooperative model already accepts.
    - A re-check would report an honest concurrent winner's worktree as `path-in-use`, where the ref race reports `created` or `existing`.
  - **Cost.** A failure after git creates the ref leaves a branch at the fetched tip with no commits of its own, and a later run reports it as `branch-without-worktree`. Row 65 lists the causes observed, including an unwritable ancestor, of which the sandbox boundary is one case (row 16).
- **(b) Set aside: `worktree add --detach`, then a bound `switch -c`.** It adds a second write, and concurrent runs left duplicate records (rows 45, 49).
- **(c) Set aside: `git branch`, then attach.** With (a)'s checks, it fails the same ways in two writes, and rollback needs a destructive delete (row 18).
- **(d) Set aside: `-b` with no checks.** Every path failure leaves a branch.
- **(e) Set aside: `EnterWorktree{name}` create mode.** It takes no start point and no `--no-track` (M1(b)).
- **(f) Set aside: `mkdir -p` before the add.** It is a third write, and it leaves directories behind at every failure stage.

**Already-in behavior.**
- `current` means `<canon-cwd>` equals the worktree root. That is the verified failure shape (row 1).
- Verification step 1 decides, before the dispatch, whether a subdirectory also counts (row 3).
- The script cannot see the harness anchor (row 4). So `current` states only that `EnterWorktree` would refuse.
- Compared with the prose recipe, the script saves two calls when creating, and one when the result is `current` (row 6).

**Concurrency.** "Deterministic" holds for one session: the same repository state always gives the same output.
- The script does not serialize concurrent runs for the same branch, and no lock is added. The create-only ref decides the race (row 45).
- A loser reports `branch-without-worktree`, `add-unfinished`, `existing`, `created` or `add-failed`, depending on how far the winner got. Each resolves once the winner finishes, unless the winner is killed: a killed winner's leftovers persist until the engineer clears them (Interrupted runs). The failure rule applies to the failing ones.

**Interrupted runs.** A Bash-tool timeout kill leaves the branch, an `initializing` record, or a complete worktree, depending on when it lands (row 64). A kill during the fetch leaves none of these, though it may leave a ref lock. A later run reports the three as `branch-without-worktree`, `add-unfinished` or `existing`, before any write. It reports a stale ref lock as `fetch-failed` once the remote's default branch has advanced (row 64).

**Timeouts. Decision: the script adds no cap of its own. The agent makes the call with the Bash tool's maximum `timeout`.**
- The fetch and add the script replaces already ran uncapped in the same tool. Copying the 30-second precedent would bring in a literal with no primary source (rows 24, 25).
- The one cap inside a run is inherited: step 7's reused helper caps its two local git reads at 5 seconds where `timeout` or `gtimeout` is on PATH, and runs them uncapped, with no signal, where neither is. A timed-out read reports as `no-origin-head` (row 10).
- A kill mid-checkout is the one costly kill. So SKILL.md and the hint tell the agent to set the Bash tool's `timeout` to the maximum the tool's description allows (row 75).
- No number is written into the script, the hook or SKILL.md.
- The cost of the maximum: a stalled fetch holds the call for the maximum rather than the default. An ssh prompt is one cause, because `GIT_TERMINAL_PROMPT=0` does not cover it.
- `GIT_TERMINAL_PROMPT=0` suppresses git's own password prompt. Unlike the siblings' `GIT_ASKPASS=''`, it leaves credential helpers alone (row 38).

**Branch-name grammar.**
- `check-ref-format --branch` decides path safety (row 14).
- It admits shell and output metacharacters, so the explicit ASCII set covers those (row 52). Under the step 1 export, ranges and the explicit list behave alike on the script's path. The list guards a future caller that does not export `LC_ALL=C`, and no test pins that choice (row 88).
- Both call sites single-quote the name. Both tell the agent to rewrite a name outside the set rather than quote around it, because a `'` would end the quoting before the script runs (row 54).
- The `<TICKET-ID>/<topic-slug>` convention and its 50-character slug limit stay as SKILL.md guidance. Enforcing them would break stow consumers who have no ticket system.

**D1: scope. Rule: a deny hint changes only where it restates a worktree-creation command.** Only `require-worktree-for-git-writes.sh:438` does that (row 21). These sites stay unchanged:
- `:337`, the SENTINEL arm. It fires on parse failures and offers only enter-only advice. Pointing it at a creation script would invite an agent that hit a quoting error to invent a branch.
- `require-worktree-for-file-writes.sh:149` and `:172`. Both give enter-only advice or name a write path. Neither restates a creation command.
- The README and CONTRIBUTING blocks (row 27), and the preserved records.

**Rollout window.** A consumer may have `~/.claude/scripts` as a real directory. There, `git pull` makes the hook and SKILL.md edits live before re-stow links the new script (G4, row 41). The hint, SKILL.md and the CHANGELOG bullet each say that a missing script means re-running `./install.sh`, following the hook's own `:190` precedent.

**Single source of truth.**
- The script's header owns the checks, the writes, the stdout grammar, the exit codes and the supported layout.
- SKILL.md owns the protocol and the failure rule. Hook `:438` points at the script.
- `docs/scripts.md` owns the engineer's runbook for clearing what a refusal leaves, including the attach command. `docs/` is not a stow package, so the runbook sits in the engineer's claude-config checkout, not under `~/.claude/`. The `branch-without-worktree` message names it that way, and no stowed file copies it (row 90).
- The hook test asserts the deny reason at runtime. A labeled wiring tripwire in `test_skills.py` checks SKILL.md against the script.
- No test reads a file outside its own `select-tests.py` domain (row 43).

**Supersedes and branch work.**
- `.claude/plans/absolute-path-worktree-recipe.md` stays byte-identical as a provenance record.
  - The net diff retires its Critical files 1–3: the SKILL.md prose recipe, the interpolated command at hook `:438`, and the four-route and spaced-path hook tests. It also retires the "live on `git pull` with no re-install" item of its file 6.
  - Its files 4, 5 and 7 stand (row 28). The SKILL.md heading stays byte-identical (row 26).
- New commits go on top of the branch's existing commits.
- The file 7 rewrite deletes the already-exists bullet, including the closing sentence an earlier review kept. That sentence's premise, that both forms are defined in the file, no longer applies.
- SKILL.md `:61-63` inlines the default-branch lookup, because the recipe step it pointed at is deleted.

**Beyond the Ask.** Items outside the Ask and their basis:
- the architect's rulings (row 48), its agreement on the layout boundary (row 86), and its extension of that boundary to a `.git` symlink (row 78);
- the engineer's selections (rows 59, 71, 82, 84, 85, 86, 87, and row 91 with Decision record 92);
- the orchestrator's `[unverified]` readings of row 80: dropping the locale chain and folding `stale-ancestor` into `path-in-use` (rows 88, 89);
- the items applied under row 74's general go-ahead as the dispatching session read it, including `LC_ALL=C` and the Bash `timeout` instruction (row 79);
- Verification step 5's `git worktree remove` of the session's own probe, which the session proposes and the engineer approves at the default-mode permission prompt, with `git branch -D` left to the engineer. The engineer asked why the agent could not use the permission prompt for this; the proposal is the orchestrator's reading of that question `[unverified]`.

**Dispatch split.** One `code-writer` dispatch, run after Verification step 1.
- Every file depends on the script's name, its output lines, its tokens and its exit codes. Splitting would mean restating that contract in every prompt.
- Point the dispatch at this Approach above the ledger, Critical files, and Verification steps 2–3. The ledger and Out of scope are reviewer reference, not dispatch instructions.
- The first implementation commit lands files 1–11 together, so no commit has a deny message naming a script that does not exist.

### Assumption ledger

**Root:** Today, creating and entering a worktree depends on the agent assembling the steps by hand:
- `branch-management` prescribes a multi-call prose recipe, with values the agent pastes in by hand.
- The worktree gate's main-tree deny restates its own copy of the creation command.
- Nothing checks whether the caller's working directory already is the target.

As a result, the copies drift apart, and every session whose working directory is the target pays for a failing `EnterWorktree` call. A cwd-relative path can also nest a new worktree inside a linked one (issue #1234).

**Givens:**
- G1. `EnterWorktree`'s `path` entry requires a target under `.claude/worktrees/` of the same repository that `git worktree list` lists. It works from inside another worktree. The harness owns this. [verified: the `EnterWorktree` tool description in the dispatching session's tool list]
- G2. Hooks and the harness's isolation guard both decide before a Bash call runs, so neither sees the commands a script runs inside it. The harness owns the guard. [verified: `docs/worktree-bash-guard.md:3-7,124-130`; the hook reads only the tool input]
- G3. Under the Claude Code sandbox, a linked-worktree session can write only to that worktree, the shared `.git` and `$TMPDIR`. Anthropic sets this. [verified: old plan G2, citing `docs/case-studies/claude-code-sandbox-write-boundary.md:119-129,153-155`]
- G4. A stow consumer whose `~/.claude/scripts` is a real directory links a new script only on re-stow. Stow's folding behavior owns this. [verified: `CHANGELOG.md:32`]
- G5. Allow rules match exact strings, so a script call whose argument varies cannot be pre-allowed without a wildcard. The harness owns the matcher. [verified: `claude/.claude/rules/settings-json-conventions.md`]

**Rows.** Retired numbers, not reused: 2, 9, 17, 29, 30, 32, 33, 55, 60, 62, 70, 72, 76.

1. `EnterWorktree` called with a path equal to the session's working directory returns "Cannot enter worktree: <path> is the current working directory". This confirms the engineer's clarifying answer in Context. [verified: the dispatching session's own `EnterWorktree` call on its current worktree]
3. Undetermined: whether `EnterWorktree` accepts a target that contains the working directory, that is, a cwd in a subdirectory of the worktree. Verification step 1 settles it before the dispatch. If the harness compares logical rather than canonical paths, a `current` result only skips a call that would have succeeded. [unverified]
4. A script cannot observe the harness anchor. `nudge-worktree-anchor.sh` treats a cwd in a linked worktree as anchored. [verified: `nudge-worktree-anchor.sh:141-145`]
5. SKILL.md `:110-112` claims that a prior `cd` silently leaves the session unanchored. This plan keeps the claim and neither strengthens nor verifies it. [unverified]
6. Call counts, prose vs script:
   - Creating: the prose recipe takes 4 calls (step 1, the separate default-branch lookup, the chained add, and `EnterWorktree`). The script takes 2.
   - The worktree already exists: prose takes 2. The script takes 2, or 1 when the result is `current`.

   [verified: `claude-skills/skills/branch-management/SKILL.md:71-96`]
7. `collect_all_worktrees` fills `ALL_WT_PATHS`, `ALL_WT_BRANCHES`, `ALL_WT_LOCKED`, `ALL_WT_PRUNABLE_REASONS` and the lock-detail arrays for every porcelain record, the main checkout first.
   - `ALL_WT_LOCK_REASONS` holds the `locked <reason>` text verbatim.
   - A detached record has an empty branch.
   - `worktree_canon_path` resolves symlinks and falls back to the raw path.

   [verified: `claude/.claude/scripts/_worktree-lib.sh:45-59,225-305`]
8. `git worktree list` prints the main worktree first. [verified: git-worktree(1) 2.43.0, per old plan row 5] Its path is the checkout root only in the standard layout (row 78).
10. `_lib_default_branch_or_guess` is meant for callers whose wrong answer only changes a diff or a message, "rather than the base it fetches, rebases onto". This script fetches and branches from that base, so it uses the pointer-only `_lib_default_branch_from_origin_head`. [verified: `_lib.sh:1247-1300`] That helper runs its two local git reads through `_lib_capped`, which caps each at 5 seconds when `timeout` or `gtimeout` is on PATH. A timed-out read returns 1, which step 7 reports as `no-origin-head`. With neither on PATH, the reads run uncapped. `_lib_capped_for` then prints a note on stderr (`:134`), but the helper redirects stderr on both reads (`:1261`, `:1263`), so the note is discarded and nothing signals the missing cap. `collect_all_worktrees` calls git with no cap and no note (row 51), so no step of the script prints that note. [verified: `_lib.sh:49-50,60-66,126-137,1258-1267`; `_worktree-lib.sh:303`] Round 9 corrected this row: its earlier `[verified]` text said the note reaches stderr, which the cited redirects contradict.
11. Among the non-test `.sh` files under `claude/.claude/`, the literal `refs/remotes/origin/HEAD` may appear outside a comment only in `_lib.sh`. [verified: `claude/.claude/scripts/tests/test_default_branch_resolution_is_shared.py:23-97`; staff-platform-engineer round 5 for the comment exception]
12. `worktree add --no-track -b <branch> <target> <sha>` sets no upstream.
    - With a commit SHA as the start point, no upstream was set with or without the flag, under `branch.autoSetupMerge` unset, `true`, `always` and `false`.
    - With a remote-tracking start point, git set one under unset, `true` and `always`, and `--no-track` suppressed it.

    So the flag is inert today and guards a future remote-tracking start point. [verified: staff-backend-engineer round-4 run, git 2.43.0] This repo's tooling reads an upstream as "already pushed". [verified: old plan rows 6–7]
13. `git fetch origin <name>` updates `refs/remotes/origin/<name>` only when `remote.origin.fetch` maps it. [verified: git-fetch(1) "Configured remote-tracking branches"] The explicit `+refs/heads/main:refs/remotes/origin/main` form advanced `origin/main` to a commit pushed from a second clone, while `remote.origin.fetch` mapped only another branch. [verified: staff-backend-engineer K1, git 2.43.0]
14. `git check-ref-format --branch` runs outside a repository. It exits 128 and rejects:
    - a leading `-` or `/`, `..`, whitespace, `.lock`, a trailing `/` and `//`;
    - a component starting with `.`, `\`, `HEAD`, and an embedded or trailing newline.

    It accepts `$`, `;`, a backtick, quotes, `é` and `refs/heads/foo`, with output unchanged. It expands `@{-1}` only where the repo has a prior checkout. [verified: staff-backend-engineer rounds 1 and 3, git 2.43.0] Its only documented expansion is `@{-N}`. [unverified: recalled from git-check-ref-format(1)]
15. `git worktree add -b` creates the branch before it checks the path. With a non-empty target that already existed, it exited 128 and left the branch. [verified: staff-platform-engineer round-3 probe; staff-backend-engineer round-1 trace of 2.43 `builtin/worktree.c`; git 2.43.0] Git then creates the target and any missing parent directories itself, including `.claude/` when it is absent. [verified: staff-backend-engineer round-6 probe D; staff-sdet, staff-platform-engineer and ciso-reviewer round-6 scratch runs; git 2.43.0] Both orderings are unverified on other git versions.
16. Under G3, the prose recipe run from a linked worktree fails the add with `Read-only file system`, while the branch lands in the writable shared `.git`. [verified: old plan row 11] The script's add fails the same way. It leaves a branch, which a later run reports as `branch-without-worktree`. [unverified: inferred from G3 and row 15. The tracked settings carry no `sandbox` key, so Verification step 5 exercises this only in a sandboxed session]
18. No script under `claude/.claude/scripts/` rolls back a branch.
    - `review-pr-checkout.sh` adds its worktree with `--detach` (`:446`). On failure it removes only its own `mktemp` directory (`:462`).
    - The only branch deletion there is the merged-branch cleanup at `cleanup-merged-branches.sh:964`.

    [verified: read `review-pr-checkout.sh:436-464`; Grep of `claude/.claude/scripts/*.sh`]
19. The worktree gate exits early on a command with no word-bounded `git`, so `GH-1/new` never reaches the parser.
    - The parser treats any token equal to `git`, or ending in `/git`, as a git invocation. A script call with such a branch therefore yields SENTINEL and is denied.
    - A token that only contains `git`, such as `GH-1/fix-git-hooks`, yields no record and is allowed.

    [verified: hook `:203-210`; unverified for the parser cases, which staff-sdet and ciso-reviewer traced through `parse-git-command.py:163-216` and the hook test pins]
20. The script makes two writes, `fetch` and `worktree add`, both as `git -C <canon-main>`.
    - The gate allows both from any tree. It matches `worktree` and `branch` by subcommand name alone, so their writing forms pass from any tree too. [verified: `_lib.sh:3693-3739` and hook `:302`, `:370`, re-read for this revision; ciso-reviewer round 10 for the parser taking the first non-flag token]
    - The fetch writes `refs/remotes/origin/<default>`, `FETCH_HEAD` and objects in the shared repository. [verified: ciso-reviewer round 5] It may also start git's automatic maintenance. [unverified]
    - Neither writes the main checkout's HEAD, index or tracked files. [verified: staff-backend-engineer round-4 answer 8, git 2.43.0]
    - The `GIT_*` unset (row 39) keeps inherited variables from redirecting either write. File 3's `_main_checkout_state` assertions pin the result.
21. The deny arms offer these remedies:
    - git-writes `:337` (SENTINEL): "change the session cwd into an existing worktree under .claude/worktrees/, or use the EnterWorktree tool".
    - git-writes `:438`: the interpolated chained creation command.
    - file-writes `:149` (git-state parse failure): "cd into an existing worktree under .claude/worktrees/".
    - file-writes `:172` (main-tree write): "Write the file at its worktree path instead".

    Only `:438` restates a creation command. [verified: read all four lines]
22. Existing tests assert that "untracked", "isolation" and "spawn an agent" are absent from both reasons. [verified: `test_require_worktree_for_git_writes.py:82-118`]
23. The allow list has no git entries and only fixed-argument script entries. [verified: `claude/.claude/settings.json:3-34`]
24. Neither timeout precedent cites a primary source. `review-pr-checkout.sh:320-322` says "matching the files-listing budget", and `_lib.sh:865-875` says "a chosen ceiling, not a measured or documented figure". [verified]
25. `worktree-removal-status.sh` and `findings-path-suffix.sh` call git uncapped. [verified: both files]
26. `nudge-worktree-anchor.sh:168` and `ready-for-review/SKILL.md:32` cite § "Anchor the session in the worktree" by name. [verified: nudge `:168`; old plan row 10]
27. `README.md:299-302` and `CONTRIBUTING.md:13-16` are human shell blocks, run at the main checkout and followed by `cd`. [verified: `README.md:293-303`; `CONTRIBUTING.md:9-17`]
28. On this branch, `CLAUDE.md:51-54`, `claude/.claude/CLAUDE.md:172` and `README.md:307` already point at the skill without naming a command. [verified: read and Grep]
34. git never marks a locked worktree `prunable`. The collision guard writes a lock for each worktree a session writes from, and does not clear it at session end. A deleted locked worktree therefore shows `locked` and no `prunable`. [verified: git-worktree(1) on `lock`; staff-backend-engineer round-1 read of `_lib.sh:2926`]
35. Git accepts an existing empty target directory. [verified: staff-backend-engineer and staff-platform-engineer round-2 probes, git 2.43.0]
36. A post-checkout hook that prints and exits 1 made `worktree add -b` exit 1. The worktree stayed registered, checked out and holding the branch, and the hook ran once. [verified: staff-platform-engineer round-3 probe, git 2.43.0] Git passes the hook's exit status through, and relays the hook's stdout to its own stderr. [verified: staff-backend-engineer round-4 answer 4 and K1; staff-sdet run; git 2.43.0]
37. `git worktree list --porcelain -z` arrived in git 2.36. The same release notes record that the non-`-z` form "did not c-quote pathnames and lock reasons with unsafe bytes correctly". This repo's git floor is 2.31. [verified: `/usr/share/doc/git/RelNotes/2.36.0.txt:110-112`; `README.md:97`]
38. Setting `GIT_TERMINAL_PROMPT` to false disables git's built-in password prompt. [verified: `/usr/share/doc/git/RelNotes/2.3.0.txt:65-66`] ssh's own passphrase and host-key prompts are outside it. [unverified]
39. An exported `GIT_DIR`, `GIT_WORK_TREE` or `GIT_COMMON_DIR` redirects `git -C` calls. The gate unsets `GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE` before judging a command. `cleanup-merged-branches.sh` documents an `.envrc` that exports these variables. [verified: hook `:196-199`; ciso-reviewer's read of `cleanup-merged-branches.sh:281-297`]
40. These inputs reach the script from outside its own code. Each has a control:
    - **The `<branch>` argument,** which may come from tracker text: the ASCII set, `check-ref-format --branch`, the single-quoted call sites, and the rewrite-not-quote instruction (row 54).
    - **Paths and branches read back from porcelain:** canonical comparison, the `initializing` test, and the `.git` test. The first record is taken as the main checkout unchecked (row 78).
      - A newline inside a registered path can still forge a record (row 37).
      - The accepted impact on stdout is a misleading path, which `EnterWorktree` rejects when it is not a registered worktree (G1).
      - The failure line prints interpolated text as given (row 63).
    - **The default-branch name:**
      - an explicit refspec whose only destination is under `refs/remotes/origin/`;
      - every interpolation prefixed by `refs/`;
      - ref-format rules on the existing ref.
    - **The inherited environment:** the `GIT_*` unset (row 39), `LC_ALL=C` (row 68) and `GIT_TERMINAL_PROMPT=0` (row 38).
    - **The filesystem under `<main>/.claude/`:** step 6's checks. One same-user window remains, from step 6 through the fetch to the add:
      - A writer that fills `<target>` makes git refuse after creating the branch.
      - A writer that swaps an ancestor for a symlink to an outside directory redirects the add: git followed the link, created the missing parent there, exited 0 and registered the outside path. [verified: ciso-reviewer round-6 scratch run, git 2.43.0] The post-add lookup reports that as `outside`, and file 3's redirect case pins it.

      The window is accepted under the cooperative threat model. Honest concurrent runs are settled by the ref (row 45).
    - **The repository's hooks:** trusted to the same degree as `git pull` on the default branch.
    - **Git and remote text on stderr:** passed through, and never onto stdout.

    [unverified: compiled from ciso-reviewer findings across rounds 1–6, against this design]
41. In a consumer whose `~/.claude/scripts` is a real directory, `git pull` updates the already-linked hook and SKILL.md. It leaves the new script unlinked until re-stow, so the call exits 127 until then. The hook already tells the agent to re-run `./install.sh` when its own files are missing. [verified: `CHANGELOG.md:32` (G4); hook `:190`]
42. An argument-bearing script call prompts in default permission mode, like the git chain it replaces. The saving is an agent call, not a prompt. [verified: `settings.json:3-34` (row 23); `docs/scripts.md:261`]
43. A module-level repo-path constant in a test must be covered by `select-tests.py`'s rules for the file it reads, or `TestCrossDomainReadCompleteness` fails.
    - A SKILL.md edit selects only the skills test tree.
    - `test_skills.py` already carries labeled wiring tripwires.
    - `test_skills.py` already reads `SCRIPTS_DIR` for `test_scripts_are_executable`.

    [verified: `test_select_tests.py:330-365`; `test_skills.py:1677-1714`; staff-sdet's read of `select-tests.py:517-555` and `test_skills.py:2938-2950`]
44. `test_worktree_removal_status.py` defines `_SCRIPT` at `:26` and `_run_script` at `:38-49`. The conftest helpers named in file 3 exist with usable signatures. [verified: read `:1-50`; staff-sdet for conftest]
45. Concurrent same-branch runs:
    - With `-b`, a second run's add failed at the create-only ref with exit 255 and registered nothing. The winner's worktree stayed intact. [verified: staff-platform-engineer round-3 and staff-backend-engineer round-4 synthetic runs, git 2.43.0, with `mkdir -p` before the add; trials on one machine, not proof]
    - Without `mkdir -p`, a failure at the ref creates no path, and `.claude/` stayed absent. Eight same-branch races gave one exit 0, one exit 255 and one record in every trial. [verified: staff-backend-engineer round-6 probes G and I, and round-7 probe-5, which re-ran the eight races; staff-platform-engineer round-6 races; git 2.43.0] Twenty trials, each of two concurrent adds sharing a missing parent, never failed. [verified: staff-backend-engineer round-6 probe I, git 2.43.0] staff-sdet's round-6 probe covered only the first sentence, on a git version it did not capture.
    - Under a detached add, which takes no ref first, two runs left two records at one path, and a losing run's cleanup deleted the winner's directory. [verified: staff-backend-engineer round 2, git 2.43.0]
    - Branch refs were never clobbered in either design.
46. Pure bash goes into a sourceable lib that tests source directly, with one subprocess case per branch. An existing sibling lib whose domain covers the helper comes first. [verified: `claude/.claude/rules/shell-script-conventions.md`; `.claude/rules/bash-unit-test-seams.md`]
47. Bash before 4.4 aborts when it expands an empty array under `set -u`.
    - CI runs on Linux only. `test_no_bash4_constructs.py` guards only `declare -A`, `mapfile`/`readarray` and `sort -V`. [verified: staff-platform-engineer rounds 3 and 5, citing `_config.sh:19-22`, `_worktree-lib.sh:153`, `.github/workflows/tests.yml:25` and that test's docstring]
    - The only array slice in `claude/.claude/**/*.sh` is `_lib.sh:305`, guarded by a non-empty test. New code uses an index loop with a length test, as `worktree-removal-status.sh:41,58,102` does. [verified: `_lib.sh:304-305`]
    - A `case` inside `$( )` can fail to parse in bash 3.2. [unverified: staff-platform-engineer round 2]
49. Without an explicit binding, `git -C <dir> switch -c X` moved the main checkout's HEAD to a new `X`. Here `<dir>` sat under `.claude/worktrees/` with no `.git` of its own, so git searched upward and found the main checkout. [verified: staff-backend-engineer rounds 2 and 3, git 2.43.0] The design runs no git command inside `<target>`.
50. With branch `GH-1` present, `worktree add --no-track -b GH-1/new` exited 255 and added no ref and no record. With `GH-8/new` present, `-b GH-8` behaved the same. [verified: staff-platform-engineer round 3; staff-backend-engineer round-4 answer 3; git 2.43.0] Step 5 compares names case-sensitively. [unverified for case-insensitive filesystems]
51. `collect_all_worktrees` runs a bare `git worktree list --porcelain` in the caller's working directory, through a process substitution. A failed listing, including one run outside any repository, therefore yields empty arrays and no error. The main record is always listed, so zero records means the listing failed. [verified: `_worktree-lib.sh:264-305`; staff-backend-engineer and staff-platform-engineer round 5 for `:303`]
52. In bash `[[ =~ ]]`, the range form `A-Za-z` admitted `é` under en_* UTF-8 locales and rejected it under `C` and `C.UTF-8`. The explicit list rejected `é` in every locale tried. An uninstalled locale falls back to `C`. [verified: staff-sdet and staff-platform-engineer round-3 runs, bash 5.2.21] An in-script `export LC_ALL=C` made the range form reject `é` under a widening locale. [verified: staff-sdet round-6 probe 2; staff-backend-engineer round-7 probe-4; bash 5.2.21]
53. `worktree add` writes `HEAD is now at …` to stdout and `Preparing worktree …` to stderr. [verified: staff-backend-engineer and staff-sdet runs, git 2.43.0] How the Bash tool presents the two streams to the agent is not known. [unverified; Verification step 5 records it]
54. Four exit statuses arise outside the script's own decisions:
    - An unbalanced `'` in the composed call exits 2 without running the script. [verified: staff-sdet round-3 run, bash 5.2.21]
    - A script file bash cannot parse exits 2 with no `worktree-ensure.sh:` line, which an untested bash 3.2 could hit (row 47). Bash runs top-level statements as it parses them, so a parse error late in the file exits 2 after the earlier statements have run. [verified: staff-platform-engineer round-7 runs, bash 5.2.21; staff-sdet and staff-backend-engineer round-8 runs]
    - An unbound variable inside the running script exits 1. [verified: staff-platform-engineer round-7 runs, bash 5.2.21]
    - With git missing, the shell exits 127 for `check-ref-format`, not git's 128, and step 2 reports it as `no-repo`. [unverified: staff-platform-engineer's trace; file 3 pins it with a shim]
56. A post-checkout hook run by `worktree add` starts after git writes `<target>/.git`, with the new worktree as its cwd. A hook that empties `<target>` left `worktree add -b` exiting 0, with the record registered, holding the branch, marked `prunable`, and with no `.git`. [verified: staff-platform-engineer round 3; staff-backend-engineer rounds 3 and 4; git 2.43.0] So the post-add lookup's `.git` test, not the record alone, decides `created`.
57. Python's `subprocess.run(cwd=<symlink>)` with an inherited environment gave the same result from `pwd` and `pwd -P`. Only an explicit `PWD=<symlink>` in the child environment made them differ. [verified: staff-sdet run]
58. `git grep --untracked` lists tracked and untracked matches and skips a `.gitignore`d directory. `agent-reviews/` is ignored. [verified: staff-platform-engineer round-3 probe; `.gitignore:41`]
61. A record whose gitdir file points to a missing location is listed as `prunable gitdir file points to non-existent location`, even when its directory is present.
    - `git worktree prune` removes that record, and leaves the directory and the branch.
    - A second add onto the still-registered target exits 128, with git's own "prune or remove" advice. Under `-b`, it has already created its branch.

    [verified: staff-backend-engineer rounds 3–5, git 2.43.0]
64. During `worktree add`, git registers the worktree, writes `<target>/.git`, and marks the record `locked initializing` until the checkout finishes.
    - SIGKILL mid-checkout left the branch, the record with `locked initializing`, a partial tree and an `index.lock`.
    - SIGTERM mid-checkout let git remove the record and `<target>`, leaving the branch.
    - SIGKILL during a post-checkout hook, after the checkout finished, left a complete worktree with no lock.
    - A concurrent loser sees the winner's record in the `initializing` state.

    [verified: staff-backend-engineer and staff-platform-engineer round-4 probes, git 2.43.0] Clearing such a record: `git worktree remove` refused on the lock, and after `git worktree unlock` it still refused on modified or untracked files until given `--force`. [verified: staff-backend-engineer round-7 probe-3, git 2.43.0, which approximated the state with a `--no-checkout` worktree holding the `initializing` lock and one stray file] A real SIGKILLed checkout leaves every file staged-deleted, so a plain `remove` very likely refuses there too. A kill before any file is written may leave a tree it accepts. [unverified: staff-platform-engineer's round-8 reading of its round-4 probe-2; staff-backend-engineer round 8] A kill during the fetch leaves no branch, record or target, but may leave a ref lock. [unverified: by trace; no cited probe killed a fetch] A stale `refs/remotes/origin/main.lock` made the explicit-refspec fetch exit 1 with git's "cannot lock ref … File exists" message once the remote had advanced, and the same fetch exited 0 while the remote was unchanged. [verified: staff-backend-engineer round-8 runs d and e, git 2.43.0 per its round-7 reading] The collision guard writes its own reason, `claude-code pid <N> session <id>`, never `initializing`. [verified: `_lib.sh:2926`] Which signal the Bash tool sends on timeout is not known. [unverified]
65. What a failed add leaves depends on its stage:
    - A failure at the ref leaves no ref, no record and no path, and `.claude/` stayed absent (rows 45, 50).
    - A failure after the ref leaves the branch with no record. Git removes the leaf target, even one that existed empty before the run. Parent directories git created stay. These causes were observed:
      - an existing empty target the session cannot write;
      - a required smudge filter that fails, such as Git LFS without `git-lfs`;
      - SIGTERM;
      - a regular file as a path component, which step 6 catches first;
      - an unwritable ancestor, such as a read-only `.claude/worktrees`.

    [verified: staff-backend-engineer round-4 trials A, C, H and I and round-6 probes E, F, G and K; staff-platform-engineer round-4 probe-1 and round-6 run; git 2.43.0] A full disk mid-checkout is assumed to behave the same. [unverified]
66. A directory under `.claude/worktrees/` can hold a `.git` entry that no record claims. Two causes reproduce:
    - A worktree directory is renamed or moved, and then pruned. Its `.git` file still points at the deleted administrative entry. [verified: staff-backend-engineer round-5 probe S2]
    - An administrative entry is deleted by hand. [verified: ciso-reviewer round-4 probe B, which deleted the entry by hand and did not run prune]

    `git worktree prune` itself leaves no `.git` behind. After the `.git` file or the whole directory is removed, prune drops the record and leaves a directory with no `.git`, or nothing. [verified: staff-backend-engineer round-5 probes S1 and S3] Under such a directory, `worktree add --no-track -b stale/inner` exited 0 and registered a nested worktree. [verified: probe B, git 2.43.0] Row 71 flags the approval this cause fed.
67. `-b` is create-only against the start point it is given.
    - Branch `pre` was created at an older commit after the pre-checks. `worktree add --no-track -b pre` then exited 255 with "a branch named 'pre' already exists". `pre` kept its tip, and the main checkout's HEAD was unchanged.
    - An empty start point made `worktree add -b` exit 128 with "invalid reference", leaving no branch and no record.

    [verified: ciso-reviewer round-4 probes C and D; staff-backend-engineer round-4 answer 7; git 2.43.0] A missing remote ref fails the explicit-refspec fetch first, so the `START_SHA` guard only keeps the failure-line contract. [unverified: staff-backend-engineer round 5 and the adversarial review, by trace]
68. Each review probe ran against git 2.43.0 only.
    - Git writes the `initializing` reason through gettext, so an add under a translated locale writes a translation. git's German catalog gives `initialisiere`. [unverified: relayed from the adversarial review's reading of git's `po/de.po`]
    - The script exports `LC_ALL=C`, so its own adds write the untranslated string. A hand-run add under a translated locale is not matched.
    - The tests seed the literal rather than produce it. `_base_test_env` passes the inherited locale through. [verified: `claude/.claude/scripts/tests/conftest.py:80-93`, read for this revision]
    - File 3's locale case pins the `LC_ALL` value git receives. No test observes the literal git writes.
69. `_make_repo_with_remote` adds no ignore for `.claude/worktrees/`, so `status --porcelain` shows `?? .claude/` once a worktree exists there. This repo ignores the path at `.gitignore:13`; a stow consumer's repo may not. [verified: `conftest.py:1354-1367` and `.gitignore:13`; ciso-reviewer round-4 probe A]
73. `switch` and `checkout` are absent from the gate's any-tree list. An opted-in gate therefore denies an agent's branch switch in the main checkout, so the `in-main` remedy is the engineer's. [verified: `_lib.sh:3693-3739`, read for this revision]
75. The Bash tool's `timeout` parameter takes milliseconds, with a default of 120000 and a maximum of 600000 for a foreground call. A harness version may differ. The parameter is not part of the command text, so exact-string allow matching is unaffected. [unverified by this author: relayed from staff-backend-engineer's and staff-platform-engineer's round-5 reads of the Bash tool description]
78. In these layouts, git's first porcelain record is not the checkout root the agent works in:
    - A bare repository: the bare directory, marked `bare`. A fresh `git clone --bare` sets no `remote.origin.fetch`, so a plain fetch writes only `FETCH_HEAD`, and `git remote set-head origin -a` fails. With no `origin/HEAD`, a run there stops at `no-origin-head`. [verified: staff-backend-engineer round-6 probe C and round-9 `probe-1.sh`; ciso-reviewer round-6 probe and round-7 probe 3; git 2.43.0] A bare repository given a fetch refspec and `origin/HEAD` passes step 7, and its create exited 0 at `<bare>/.claude/worktrees/<branch>`. [verified: staff-backend-engineer round-9 `probe-1.sh` section A2, git 2.43.0]
    - An absorbed submodule: `<super>/.git/modules/<name>`. The plan's fetch and add, run against it, both exited 0 and registered a worktree inside that git directory. [verified: staff-backend-engineer round-6 `probe-redesign-3.sh`; ciso-reviewer round-6 probe; git 2.43.0]
    - `--separate-git-dir` with a git directory not named `.git`, through `git init`, a re-init or `git clone`: the git directory itself, whether listed from the work tree or from the git directory. The work tree holds a `.git` file. An add rooted there registered a worktree inside the git directory. [verified: staff-backend-engineer round-6 probe B, round-7 probe-2 and round-8 run-a; staff-platform-engineer round-7 `probe-layouts-1.sh`; ciso-reviewer round-7 probes 1 and 3; git 2.43.0] An earlier adversarial-review reading, that git prints the work tree, did not reproduce.
    - A git directory literally named `.git`: `<x>`, whose `.git` is a directory. This holds for `git init --separate-git-dir <x>/.git <work>`, for a `--separate-git-dir` clone into `<x>/.git`, and for a bare repository at `<x>/.git`. A create there landed beside the git directory at `<x>/.claude/worktrees/<branch>`, and the work tree's HEAD and status were untouched. [verified: staff-platform-engineer round-7 `probe-layouts-1.sh` and round-9 `probe-9a.sh` section A; staff-backend-engineer round-9 `probe-3.sh`; git 2.43.0]
    - A checkout whose `.git` is a symlink. To a directory not named `.git`, the first record is the link's target. [verified: staff-backend-engineer round-7 probe-1 case E, git 2.43.0] To a directory named `.git`, it is the target's parent, and the fetch and add there both exited 0. [verified: ciso-reviewer round-7 probe 2, git 2.43.0; that probe created links inside its own scratch directory, against its scratch rules]
    - Git derives the first record as the canonical common directory with a trailing `/.git` removed. [unverified: the architect consult's account; consistent with every probe above]
    - Step 4 measures `outside` against that record, so a worktree correctly placed under the real checkout's `.claude/worktrees/` is refused as `outside`. [unverified: ciso-reviewer round-8 trace of step 4]
    - Undoing a fresh create: in the `--separate-git-dir`, absorbed-submodule and bare-with-`origin/HEAD` layouts, `git worktree remove` without `--force` and `git branch -D` both exited 0. The worktree list and branches returned to their state before the create, and the work tree's status stayed clean. Only the empty `.claude`, `.claude/worktrees` and `.claude/worktrees/<ticket>` directories remained, as in a standard-clone control. [verified: staff-backend-engineer round-8 run-a and round-9 `probe-1.sh` sections A2 and B; ciso-reviewer round-8 `probe1.sh`; git 2.43.0] The fetch's refs, `FETCH_HEAD` and objects stay, as after any fetch. A worktree a session has used carries the collision guard's lock, so its removal needs `git worktree unlock` first and may need `--force` (rows 34, 64). [unverified: traced from those rows, not run]

    So a create in these layouts can exit 0 with the worktree inside or beside the git directory, though a fresh bare clone stops at `no-origin-head`. No `.git` test on the first record separates them all, because the `.git`-named variants pass it. The engineer ruled the bare, absorbed-submodule and `--separate-git-dir` layouts out of scope (row 86). Treating the `.git`-symlink layouts the same way is the architect consult's extension, resting on the round-7 probes above. [unverified: relayed by the dispatching session]
79. Row 74's relayed list. The adversarial reviewer's simplifications, as the dispatching session relayed them: `LC_ALL=C`; three target tokens; the first record as `<canon-main>`; no `mkdir -p`; the fourth concurrency window; the narrowed where-git-runs claim; the `in-main` remedy; and the token rule. The Bash `timeout` instruction came in as a round-5 specialist fix. Both `LC_ALL=C` and the `timeout` instruction were applied under row 74 as the dispatching session read it. Neither was an engineer selection. The list's claim that the test conftest forces `LC_ALL=C` did not check out (row 68). [unverified]
90. `docs/` is in neither stow package, so `docs/scripts.md` is not under `~/.claude/`. Every consumer installs from a claude-config checkout, and a stowed script resolves into that checkout, so the engineer whose agent ran the script has the file there. [verified: `claude/.claude/scripts/stow-packages.sh:4-27`, which lists the packages `claude` and `claude-skills` and resolves the checkout through the stowed symlink]
91. The reviewer-tree-mutation gate denies `worktree-ensure.sh` by command word for review-only agents (Critical files item 11).
    - Round 17's staff-platform-engineer found that the gate passed the call, because it contains no git word, while the worktree gate's `:438` message advertises it. [verified: `deny-reviewer-tree-mutation.sh:410-457`; `require-worktree-for-git-writes.sh:438`]
    - The architect and a Fable consult both read the finding as an enforcement-invariant gap the cooperative-threat-model waiver does not cover, since our own deny message steers a cooperative agent into the call. [unverified: relayed by the dispatching session]
    - The engineer's words and what they cover are in Decision record 92.
    - The residual is a class: any command whose command word, after the gate's command-word resolution in `_lib.sh`, is not the script. The members are wrappers outside the runner skip list (`bash`, `sh`, `source`, `timeout N`, `exec`, `nohup`, `setsid`), syntax the fragment splitter does not unwrap (a brace group `{ <path> x; }`, `then` and `do` bodies, a bare `&`), and a glob- or variable-built command word. The `:438` hint, once item 5 lands, names the Bash tool's `timeout` parameter, so it no longer invites the `timeout N` prefix.
    - Set aside:
      - widening the shared runner list in `_lib.sh`, which also serves `deny-repo-relocation.sh`;
      - a signal inside the script that detects a review-only caller (the agent type is a hook-input field, not an environment variable `[unverified]`);
      - naming the sibling git-writing stowed scripts (no deny message advertises git-writing scripts);
      - hiding the hint from review-only callers, which duplicates who-may-write knowledge into the WHERE-gate, while the reviewer gate already denies every call that reaches `:438`;
      - closing `timeout` or `bash` in code, or widening the shared runner list for them;
      - extra pins for `./`, quote-split, uppercase and `GH-7/git` forms, and a contract test tying the two script-name literals, all deferred as gold-plating.
    - [unverified: whether the harness shows the model both blocking hooks' stderr in one turn]

**Decision record** (the engineer's words, with one line on what each covers, and the readings built on them):

31. Delegation quotes:
    - [engineer-verified: "Ask the architect"] answered the scope question in Context, plan-review's three-item question, and its nine beyond-Ask items.
    - [engineer-verified: "See prior response about architect. Only one spawn for all questions"] answered the failed-add question.

    Both cover delegation only. The D1 rule and the add-step choice are the architect's. Row 77 settles the spawn count.
48. The architect's rulings on the delegated items:
    - Kept: the name set with quoting, `symlinked-path`, the `GIT_*` unset, `GIT_TERMINAL_PROMPT=0`, the lib placement and its header edit, and the missing-script clauses.
    - Cut: `bad-default-branch`, the printable-path guard, the `EXIT` trap, the SENTINEL-arm edit and the write probe.

    [unverified: relayed by the dispatching session]
59. [engineer-verified: "Keep (Recommended)"], then, after the add step changed, [engineer-verified: "Keep"], on keeping the `ref-conflict` refusal. The labels cover keeping it. The option text and the reasons given were the session's.
63. [engineer-verified: "Cut (Recommended)"] on rewriting control bytes in interpolated failure text. The label covers cutting it. The option text was the session's.
71. [engineer-verified: "I think those make sense."] on the three refusals plan-review asked about: `add-unfinished`, `stale-ancestor` and `no-start-point`. They were offered as optional, like the beyond-Ask items.
    - [unverified, the architect's note] `stale-ancestor` was put to the engineer on the cause that `git worktree prune` leaves a `.git`-bearing directory. The round-5 probes did not reproduce that cause (row 66), so the approval may have rested on it. The refusal stands on the causes that do reproduce.
    - Folding `stale-ancestor` into `path-in-use` and dropping `no-start-point`'s token came with the design row 74 approved, as row 79 relays it. Row 82 is the cited approval for dropping `no-start-point`'s token. Row 89 is the orchestrator's reading for the fold.
    - [engineer-verified: "2 sure"] answered a question about reconfirming the refusal on the corrected cause. The option text is not recorded here. That the answer reconfirms it as `path-in-use` is the dispatching session's account. [unverified]
74. [engineer-verified: "Let’s go with fable’s simpler design and apply fixes"]. Covers adopting the adversarial reviewer's simplified design and applying the round-5 specialist fixes. What that design included is row 79's relay.
77. [engineer-verified: "I meant one spawn for the architect per AskUserQuestion block rather than spawning say 3 times because I said to ask the architect three times in a row. Do you follow?"]. Covers: row 31's "Only one spawn" means one architect spawn per `AskUserQuestion` block. That the add-step consult and the later architect passes each answered their own block is as relayed by the dispatching session. [unverified]
80. [engineer-verified: "Ok sounds like the decisions are clear, leaning with the decisions where reviewers and or fable agree."], said after the engineer was shown the reviewers' and the adversarial reviewer's positions on three decisions: the layout boundary, the redundant `LC_ALL=C` chain, and approval coverage. The quote covers following those positions where they agree. The orchestrator's readings of it are rows 88 and 89, both `[unverified]`. Row 82 confirmed the approval coverage for `no-start-point`. An earlier reading of this quote as adding a layout refusal is superseded by row 86.
81. [engineer-verified: "1. I like your recommendation"], answering which trim to apply. The recommendation was the orchestrator's: "B, plus three of Fable's items". B is staff-platform-engineer's round-6 trim list. The three Fable items were: the consumer inventory collapsed to one sentence; row 76 (now retired) with its `docs/scripts.md` bullet; and the trailing noticed-in-passing notes, on the `CHANGELOG.md` double `### Changed` header and the `README.md:522` three-levels-deep path assumption, which now sit one line each under Out of scope. The option text and its contents are the orchestrator's. [unverified]
82. [engineer-verified: "2. Yes it’s covered. Architect spawn happens once"], answering whether row 80 already covers dropping the `no-start-point` token, or whether explicit confirmation was needed. Covers: dropping that token is approved.
83. The locale chain was dropped. It held the widening-locale lib case, file 4's import from `hooks.tests.test_config_lib`, a hand entry in `select-tests.py`, and a docstring edit in `test_config_lib.py`.
    - The record of the docstring request, formerly row 72: [engineer-verified: "Why is the doc string so brittle? It shouldn’t have to be changed after an import right?"], then [engineer-verified: "I meant make it less brittle. Ask the architect"].
    - The step 1 export makes the chain redundant: under `LC_ALL=C` the explicit list and ranges cannot be told apart on the script's path (row 52). staff-sdet and staff-platform-engineer raised this in round 6.
    - Dropping the chain rests on row 88. The docstring request stands as its own change in a separate PR (rows 84, 85).
84. [engineer-verified: "Yes to this “Do you want the docstring made less brittle as its own change?”"]. Covers: the docstring of row 83's request is to be made less brittle, as its own change. The question text inside the quote was the session's.
85. [engineer-verified: "Separate PR (Recommended)"], selected over "This branch" when asked where the docstring change goes. Covers: a separate PR, not this branch. The option text was the session's.
86. [engineer-verified: "I strongly think this should be unsupported out of scope. This otherwise feels over engineered if we bring in scope. Does the architect agree or disagree"], answering whether the script should refuse bare, absorbed-submodule and `--separate-git-dir` layouts or state them as unsupported in Out of scope. Covers: those layouts are unsupported, stated in Out of scope, with no refusal in the script. That the architect consult agreed, and its edit list, are relayed by the dispatching session. [unverified]
87. [engineer-verified: "Yes"], said after the engineer was told the architect agreed and was shown the full scope of this pass. Covers: proceeding with that scope. What the scope listed is the dispatching session's account. [unverified]
88. The orchestrator reads row 80 as covering dropping the widening-locale chain (row 83). The step 1 export that remains came in under row 74 (row 79), not under this reading. staff-sdet and staff-platform-engineer took that position in round 6. The adversarial reviewer did not address it. [unverified: the orchestrator's reading]
89. The orchestrator reads row 80 as covering folding `stale-ancestor` into the stray form of `path-in-use`. Row 82's quote covers only the sibling `no-start-point` drop, and row 71's "2 sure" has no recorded option text. [unverified: the orchestrator's reading]
92. The reviewer-gate deny of `worktree-ensure.sh` (row 91, item 11) and the `:438` hint wording:
    - [engineer-verified: "I think we need to fix this in this PR… what does Fable think?"]. Covers: fixing the gap in this PR, and asking Fable.
    - [engineer-verified: "Yes, implement (Recommended)"], and [engineer-verified: "Accept both gaps (Recommended)"]. These are option labels, answers to a question that offered, as the session's own proposal, accepting `bash <path>` and `timeout N <path>` as known gaps `[unverified: the dispatching session's account]`. The labels cover implementing the deny and accepting the gaps the question offered. That "both" refers to `bash <path>` and `timeout N <path>` is the session's reading of the label. [unverified]
    - [engineer-verified: "Rewording the hint makes sense. Does the architect agree?"]. Covers: rewording the hint. That the architect agreed is relayed by the dispatching session. [unverified]
    - [engineer-verified: "Ok reword plus deny accept class and stop as you recommend"], said after the session described a residual class (wrappers outside the runner skip list in `_lib.sh`'s command-word resolution, for example `bash`, `sh`, `source` and `timeout N`) and recommended: reword the hint, keep the single deny, accept the class, add nothing else `[unverified: the dispatching session's account of what the engineer was shown]`. The traced class is wider than that description (row 91). The quote covers exactly: reword the hint at `require-worktree-for-git-writes.sh:438`, keep the one by-name deny (item 11), accept the residual as that class, and add no further layers. It does not cover more.
    - Item 6's `timeout`-parameter pin and step 3's three item-11 mutants are the author's reading of "reword", not separately quoted. The engineer may read "stop" more narrowly. [unverified: the author's reading]

**Mechanisms** (M19 and M21 are retired):
- M1. One script is the recipe's only home. anchors: root, row1, row6, G2. Lighter alternatives, each set aside:
  - (a) Keep the prose recipe. It fails the target == cwd case (row 1), and the calls add up (row 6).
  - (b) `EnterWorktree{name}` create mode. It has no start point and no `--no-track`.
  - (c) A PreToolUse hook that denies `EnterWorktree` when target == cwd. The deny costs the call it is meant to save (row 1).
  - (d) A lookup-only script, with the add left in prose. It stays multi-call and keeps a command copy.
  - (e) A git alias. It needs per-consumer git config, which stow has no channel for.
- M2. Three success states. anchors: row1, row3, row4, G1.
- M3. The usability predicate: not `initializing`-locked, and a directory containing `.git`. `LC_ALL=C` makes the script's own adds write the string it matches, and file 3 pins the export. anchors: G1, row7, row34, row56, row64, row68.
- M4. Step 6's component walk, with three tokens. anchors: root, G1, row15, row40, row61, row66, row74.
- M5. One `worktree add --no-track -b`, with no `mkdir`. anchors: row15, row16, row18, row36, row45, row49, row65, row67, row74.
- M6. The name check. anchors: row14, row40, row52, row54.
- M7. `<canon-main>` comes from the first porcelain record, unchecked. That record is the main checkout in a standard clone, and other layouts are out of scope (row 86). Set aside:
  - a refusal unless `<canon-main>/.git` is a directory: a token, a lib function and layout fixtures for layouts the engineer ruled out of scope, and a git directory named `.git` passes it anyway (row 78);
  - a `git rev-parse --is-bare-repository` check: set aside because the layout check it would have served was dropped (row 86);
  - a second derivation with a cross-check: a second source for one fact.

  anchors: row8, row51, row74, row78, row86.
- M8. Default branch, fetch and `START_SHA`. `http.lowSpeedLimit`/`http.lowSpeedTime` is set aside: it covers HTTP only and needs config changes. anchors: row10, row11, row12, row13, row24, row25, row38, row67.
- M9. Unset the inherited repository-selection variables. Refusing when they are set, as `cleanup-merged-branches.sh` does, is set aside: the gate unsets them, and the script should act on the repository the gate judged. anchors: row39.
- M10. The output, exit-code, token-rule and failure-rule contract. anchors: row10, row37, row40, row47, row51, row53, row54, row64, row90.
- M11. Source the two libs. An inline `symbolic-ref` (row 11) and copied helpers are set aside. anchors: row7, row10, row11.
- M12. The five decision functions in `_worktree-lib.sh`. anchors: row46, row47.
- M13. The hook `:438` pointer. anchors: row19, row20, row21, row22, row41, row75, row91.
- M14. The SKILL.md protocol rewrite. anchors: row5, row6, row19, row26, row41, row53, row54, row73, row75.
- M15. Tests stay in their own domains, with real git wherever git behavior is under test. anchors: row19, row22, row43, row44, row57, row68, row69, row83.
- M16. No `settings.json` change. anchors: row23, row42, G5.
- M17. The D1 scope rule. anchors: row21, row27, row28, row31.
- M18. The CHANGELOG bullet and the `docs/scripts.md` entry. anchors: G2, G4, row41, row42, row64, row65, row90.
- M20. Refuse ref-name conflicts before any write. anchors: row50, row59.
- M22. The reviewer gate denies `worktree-ensure.sh` by command word (item 11), because the `:438` hint advertises a script that has no git word. The residual is the wrapper class, accepted. (The number M21 is retired and not reused.) Set aside, as row 91 lists. anchors: row19, row20, row91.

## Critical files

All files go in one `code-writer` dispatch. Write test files 3, 4, 6, 8 and item 11's test file first (red, Verification step 2), then files 2, 1, 5, 7, 9, 10 and item 11's hook and docs. `conftest.py` does not change. Row, G and M references and `[verified]`/`[unverified]` tags, here and in the Approach, are plan annotations and appear in no shipped file.

1. **New:** `claude/.claude/scripts/worktree-ensure.sh`. Run `chmod +x` on disk, and stage it with `git add --chmod=+x`. `git add --chmod` sets only the index bit, and the tests exec the file directly.
   - The header follows the Approach's Header bullet. It also holds:
     - the purpose and Usage;
     - the byte-exact stdout templates;
     - the token list;
     - an "Exit codes:" block, including the timeout-kill line.
   - `SCRIPT_NAME` and `set -euo pipefail`.
   - Source `hooks/_lib.sh` the way `review-pr-checkout.sh:98-99` does, and `_worktree-lib.sh` the way `worktree-removal-status.sh:32-33` does. Guard both sources.
   - Reuse `_lib_default_branch_from_origin_head`, `collect_all_worktrees`, `worktree_canon_path`, and file 2's functions.
   - One re-collect helper serves steps 3 and 9, and it runs in the main shell, because `collect_all_worktrees` fills globals. Its zero-record token is `no-repo` at step 3 and `list-failed` at step 9.
   - One `die <token> <message>`. Step 6 dies generically on the verdict.
   - Guard every fallible call with `if ! x=$(…)` or `|| die`. Under `set -e`, a bare `x=$(fn)` exits silently with the helper's status. The add's status is the one deliberate exception, because the post-add lookup decides its outcome.
   - The `no-origin-head` message must not spell `refs/remotes/origin/HEAD` (row 11).
   - One-line comments only. Example at step 9: "Git creates the branch before it checks the path, so every path refusal runs first."
   - Bash 3.2-safe:
     - the guarded expansion for empty arrays;
     - no array slices;
     - no `case` inside `$( )`;
     - no `local` or `declare` of `_CONFIG_MEMO_*` names (`test_config_lib.py:2454`).
2. `claude/.claude/scripts/_worktree-lib.sh`:
   - Header lines 2–16: widen "worktree-cleanup scripts", add `worktree-ensure.sh` to "Sourced by", and add the new functions to "Provides". `collect_all_worktrees` is unchanged.
   - `worktree_ensure_name_check <name>`: the character set first, then `git check-ref-format --branch`. It prints `ok`, `bad-name`, or `git-failed <status>`. The pattern sits in a variable, expanded unquoted on the right of `=~`, because bash matches a quoted right-hand side as a literal string.
   - `worktree_ensure_classify <branch> <canon-main> <canon-cwd>`: reads `ALL_WT_PATHS`, `ALL_WT_BRANCHES` and `ALL_WT_LOCK_REASONS`. It prints `absent`, or one of `in-main`, `add-unfinished`, `missing`, `outside`, `current` or `existing`, followed by a space and the record's index, in step 4's order.
   - `worktree_ensure_ref_check <branch>`: reads `<sha> <refname>` lines on stdin, skipping empty lines. It prints `branch-without-worktree <sha>`, `ref-conflict <existing branch>`, or nothing.
   - `worktree_ensure_target_check <canon-main> <target>`: step 6's walk. It prints nothing, or one line `<token> <message>`. Each message names the component, and the record form embeds `worktree_ensure_describe_record`. The stray form's message is the same at every component and names no move or removal. All its comparisons are quoted equality tests.
   - `worktree_ensure_describe_record <index>`: prints one line naming, in order:
     - the record's path;
     - `branch <name>` or `detached`;
     - `locked` when the record is locked;
     - `directory missing` or `no .git` when the `.git` test fails;
     - when the prunable reason is non-empty, git's reason, followed by "`git worktree prune` removes this record but not its directory or branch; a run for that branch then reports `branch-without-worktree`" (row 61).
3. **New:** `claude/.claude/scripts/tests/test_worktree_ensure.py`. One case per I/O branch. The decision matrix lives in file 4.
   - Model `_SCRIPT` and `_run_script` on `test_worktree_removal_status.py:26` and `:38-49`.
   - Reuse these `conftest.py` helpers: `_make_repo_with_remote` (with `default_branch`), `_make_feature_branch`, `_make_worktree` and `_base_test_env`.
   - Define locally:
     - a helper that pushes from a second clone;
     - `_git_shim_failing_nth(subcommand, n)`. It delegates to the real git except on the n-th call of that subcommand, which it matches after any `-C <path>` pair.
     - `_git_shim_on(subcommand, snippet)`. On a matching call, matched the same way, it runs a shell snippet, which may exit to replace the call, then execs the real git by absolute path.
   - File-wide fixture defaults:
     - the main checkout sits under a directory whose name contains a space;
     - branch names have the `<ticket>/<slug>` shape;
     - the repo sets `core.hooksPath` and `branch.autoSetupMerge=always` explicitly;
     - the main checkout's `.git/info/exclude` lists `.claude/worktrees/` (row 69).
   - State-dependent cases build their worktrees with `_make_feature_branch`, `_make_worktree`, or direct git calls in the test, never through the script. Only the lifecycle chains chain script runs.
   - Shared helpers:
     - `_repo_state(main)` returns three things:
       - `for-each-ref refs/heads`;
       - `worktree list --porcelain`;
       - a sorted recursive listing of `<main>/.claude/worktrees` that names each entry's type and follows no symlink, or that path's own type when it is not a directory.
     - `_main_checkout_state(main)`: returns the main checkout's `symbolic-ref -q HEAD`, `rev-parse HEAD` and `status --porcelain`, always run with a clean environment.
     - `_assert_refused_output(result, exit_code, token)`: checks the exit code, empty stdout, and a last stderr line starting `worktree-ensure.sh: <token>:`.
     - `_assert_refused(result, exit_code, token, before, after)`: `_assert_refused_output` plus `before == after`.
     - `_assert_ready(result, state, path)`: checks exit 0, and that stdout equals the byte-exact template.
     - `_message_text(result, tmp_path, *paths)`: the last stderr line after `worktree-ensure.sh: <token>: `, with the required `tmp_path` root and each further path the test passes removed, longest first, in both its raw and `os.path.realpath` forms. `tmp_path` names come from test names, so a word asserted on the raw line could come from a path. Every assertion about a word in a message, positive or negative, runs on this text. Assertions that a message names a path, such as the decoy checks, run on the raw line.
   - Cases:
     - **Name and argument exit paths.**
       - `usage`, for zero and two arguments.
       - `bad-name` for `a;b` and for `a..b`. Both run from a `tmp_path` directory that is not a repository, which shows the name check runs before repository detection.
       - A `git` shim that exits 127 gives `no-repo` from step 2.
       - `GH-1/new` run from a directory that is not a repository gives `no-repo` from step 3.
     - **`no-origin-head`:** `git remote set-head origin -d`.
     - **Lifecycle,** two chains:
       - Chain 1: cwd at the main root, default branch `main`. It first asserts that `<main>/.claude` is absent, which pins git creating parent directories (row 15).
       - Chain 2: cwd in a linked worktree at `<main>/.claude/worktrees/<ticket>/<slug>`, default branch `trunk`. Its docstring cites GH-1234 as the nesting shape it guards.

       In each chain, a second clone pushes a commit after the last fetch. Then:
       - The run gives `created`. The path is under `<canon-main>/.claude/worktrees/` and not under the linked worktree. The tip is the pushed commit, and `_main_checkout_state` is unchanged. The byte-exact stdout check also guards the stderr redirect (row 53).
       - `<branch>@{upstream}` fails (row 12). It is a tripwire for a future start point, not evidence for `--no-track` today.
       - A rerun gives `existing`, with the identical path.
       - With the cwd at the worktree, the run gives `current`.
       - With the cwd in a subdirectory, the run gives what Verification step 1 decided.
     - **Post-add `current`:** the cwd is an existing empty `<target>`. Expect exit 0 and the byte-exact `current` template (row 35).
     - **Symlinked cwd:** run from a symlink to a worktree, with `PWD` set to the symlink path in the child's environment only. Expect `current` (row 57).
     - **No network for `existing`:** the bare remote is moved away, and the run still gives `existing`.
     - **Narrowed fetch:** a clone whose `remote.origin.fetch` maps only another branch still starts from the freshly pushed tip.
     - **Environment.** Five variants, each exported for one `created` run:
       - `GIT_DIR=<main>/.git`;
       - `GIT_WORK_TREE=<main>`;
       - `GIT_COMMON_DIR=<main>/.git`;
       - `GIT_INDEX_FILE=<main>/.git/index`;
       - `GIT_DIR` pointing at an unrelated repo.

       The `GIT_DIR=<main>/.git` variant also runs `existing` and `current`. After each run, `_main_checkout_state` is unchanged, and the result is in the cwd's repository.
     - **`GIT_TERMINAL_PROMPT`:** the child environment sets it to `1`. `_git_shim_on("fetch", …)` records the value it sees, recording unset as a distinct value. The test asserts `_assert_ready` first, then that the recorded value is `0`.
     - **Locale:** the child environment has `LC_ALL` removed and `LANG=C.UTF-8`. `_git_shim_on("worktree add", …)` records `${LC_ALL-unset}`. The test asserts `_assert_ready` first, then that the recorded value is `C`. Removing `LC_ALL`, rather than setting another value, makes an assignment without `export` fail too (row 68).
     - **Refusals.** Each runs through `_assert_refused` with the bare remote moved away, so a refusal that ran after the fetch fails on its token.
       - `missing`: a locked worktree whose directory was removed.
       - `add-unfinished`: a worktree whose own `locked` file, in the directory `git -C <wt> rev-parse --git-dir` names, holds `initializing`. The test first asserts the porcelain shows `locked initializing`.
       - `in-main`: the branch the main checkout has checked out.
       - `outside`: a real worktree on the branch at a sibling path outside `<main>/.claude/worktrees/`.
       - `branch-without-worktree`. The message text carries the tip SHA, `git worktree list` and `docs/scripts.md`, and contains no `worktree add`.
       - `ref-conflict`, two orientations. First, `GH-1` present and `GH-1/new` requested. Second, `GH-8/new` present and `GH-8` requested, where the message must contain `GH-8/new`.
       - `symlinked-path`: `.claude/worktrees` links to a directory outside the repo. Nothing appears there.
       - `path-in-use`, three forms:
         - a detached worktree at the target, whose message text contains `detached`. The test also asserts that `tmp_path.name` does not occur in that text;
         - a worktree at `.claude/worktrees/foo` that holds `bar`, with the request `foo/baz`, whose message text contains `branch bar`;
         - a stray `.git`: a worktree on branch `other` at `.claude/worktrees/old` is moved with `mv` to `.claude/worktrees/stale`, then `git worktree prune` runs, and the request is `stale/inner`. Its message text contains the "no registered worktree" claim, and, compared case-insensitively, neither the stem `mov` nor `remov`.

         The stray form's former branch must not be a path prefix of the request, because step 5's `ref-conflict` runs first. Never reorder the steps to make the case pass.
       - `target-not-empty`: a target directory holding a regular file, and a variant holding only a dotfile not named `.git`.
       - Decoys: for `missing`, `add-unfinished`, `outside` and the two record forms of `path-in-use`, a decoy linked worktree is created first. The test asserts the message contains the blocking record's path and not the decoy's.
     - **`fetch-failed`:** the origin URL points at a missing path.
     - **`fetch-failed` after a successful fetch:** `_git_shim_on("rev-parse", …)` exits 1 when its arguments contain `^{commit}`. Expect `_assert_refused` with `fetch-failed`, and a message saying the ref is absent and `git worktree add` did not run.
     - **Late failures,** through `_git_shim_on("worktree add", …)`, so real git runs the add:
       - **Target filled after the checks.** The snippet creates `<target>` with a file in it. Expect `_assert_refused_output` with `add-failed`, and a message saying the branch may remain. Then assert that `refs/heads/<branch>` exists at the fetched tip, and that no record holds it. A second run without the shim gives `branch-without-worktree <sha>`, with `_repo_state` unchanged. This re-pins rows 15 and 65, and shows the script deletes no branch.
       - **Branch appears after the checks.** The fixture has no `<main>/.claude`. The snippet runs the real git's `branch <branch> <older commit>`. Expect `add-failed`, the branch still at the older commit, `_main_checkout_state` unchanged, and `<main>/.claude` still absent (rows 45, 67).
       - **Redirected add.** The snippet replaces `<main>/.claude/worktrees` with a symlink to a directory outside the repository. Expect `_assert_refused_output` with `outside`, and a message naming the registered outside path (row 40).
     - **Hook empties the new worktree.** A post-checkout hook, installed after every fixture git command and reached through `core.hooksPath`, exits 0 unless `pwd -P` is the canonical target. Otherwise it deletes every entry there, dotfiles included.
       - Expect `_assert_refused_output` with `missing`.
       - Then assert that a record at the target holds the branch and that `<target>/.git` is absent.
       - A rerun gives `missing`, with `_repo_state` unchanged (row 56).
     - **`list-failed`,** at two call sites:
       - step 5, with `_git_shim_failing_nth("for-each-ref", 1)` through `_assert_refused`;
       - after the add, with `_git_shim_failing_nth("worktree list", 2)` through `_assert_refused_output`. The message contains "may have succeeded".
     - **State decides, not exit status:** a post-checkout hook that prints to stdout and exits 1. The run gives `created`, and stdout is byte-exact. The case cannot show the stdout redirect, because git itself relays a hook's stdout to stderr (row 36).
4. `claude/.claude/scripts/tests/test_worktree_lib.py`: direct, sourced tests for the five new functions.
   - They use synthetic `ALL_WT_*` arrays, stdin text, and `tmp_path` directories whose names contain a space, `[`, `]` and `*`.
   - Each control expected to print nothing shares a parametrization with a positive case.
   - Every assertion about a word in an output line, positive or negative, runs on the line with each `tmp_path`-derived path removed, through a local helper like file 3's `_message_text`. That covers the stray form's case-insensitive `mov` and `remov` negative, and the record-form and Describe fields. Duplicating the helper in both files is DAMP test code.
   - The new section's docstring names `worktree-ensure.sh` as the caller.
   - Name check:
     - `bad-name` for `-x`, `a..b`, `a b`, `x.lock`, `a/`, `/x` and the empty string;
     - `bad-name` for `a;b`, `a"b`, `a'b`, `a}b`, `$x`, a backtick, a no-break space, `é` and `@{-1}`;
     - `bad-name` for `a` + newline + `b`, and for `a` + trailing newline;
     - `ok` for `GH-1/new`;
     - `é` gives `bad-name` under `LC_ALL=C` and under `LC_ALL=C.UTF-8`.
   - Classify:
     - `absent`, `in-main`, `current` and `existing`;
     - `add-unfinished` for a record whose lock reason is `initializing`, with `.git` present and with it absent, and for a cwd equal to that record;
     - `existing` for a lock reason `claude-code pid 1 session x`, and for a bare `locked` with an empty reason (row 34);
     - `missing` for a locked record with no `prunable` reason, for a directory with no `.git`, and for a registered record whose `.git` is a dangling symlink (step 4's `-e` test, where step 6 counts the same entry as stray);
     - `outside` for `<main>/.claude/worktrees-old/x` and for `<main>/elsewhere`, and for a cwd equal to an outside record that holds the branch;
     - a subdirectory cwd;
     - controls that give `absent`: record `…/foo` with the query `foobar`, and a record on branch `axb` with the query `a.b`;
     - a record whose path reaches the cwd through a symlink gives `current`;
     - with the main checkout at `…/m*`, a record at `…/mX/.claude/worktrees/foo` gives `outside`.
   - Ref check:
     - an exact match gives `branch-without-worktree <sha>`;
     - `GH-1` for `GH-1/new`, `a/b` for `a/b/c`, and `GH-8/new` for `GH-8` each give `ref-conflict`;
     - controls that print nothing: `GH-10` for `GH-1/new`, `foo-bar` for `foo`, `axb` for `a.b`, and empty input.
   - Target check:
     - `symlinked-path` for `.claude` as a symlink, `.claude/worktrees` as a symlink, a symlinked intermediate component, and a dangling symlink at the leaf.
     - `path-in-use` record form for:
       - a record equal to the target, with its directory empty;
       - a record equal to the target, with its directory missing;
       - a record at an ancestor whose path is unrelated to its branch.

       With a decoy record at index 1, the message names the blocker's path.
     - `path-in-use` stray form for an unregistered ancestor holding a `.git` file, a `.git` directory, and a dangling `.git` symlink; for a target directory holding a `.git` file; and for `.claude` and for `.claude/worktrees` each holding a `.git` directory. No stray-form message contains, compared case-insensitively, the stem `mov` or `remov`.
     - `path-in-use` not-a-directory form for a regular file at `.claude/worktrees`, as a middle component, and at the target.
     - `target-not-empty` for a target directory holding a regular file, and one holding only a dotfile not named `.git`.
     - Controls that print nothing:
       - a prefix sibling `foobar` beside record `foo`;
       - a target `…/ab/x` beside a record `…/a[b]`;
       - a one-element array holding only the main record;
       - an existing empty target;
       - a plain ancestor with no `.git`;
       - an absent target.
   - Describe: a detached record; a record holding a branch; a locked record whose directory is missing; and a prunable record whose directory is present, which shows git's reason and the prune remedy text.
5. `claude/.claude/hooks/require-worktree-for-git-writes.sh:438` only. Today it restates the creation command. Afterwards it points at the script.
   - Replace the "cd into … or create one with '…' (allowed from any tree)" clause. Draft wording: "run ~/.claude/scripts/worktree-ensure.sh '<branch>' as the whole Bash command, with the Bash tool call's timeout parameter set to its maximum, typing the branch name in place of <branch> inside the quotes, and make the step it prints your next call. Branch names use only ASCII letters, digits, ., _, / and -; rewrite a name outside that set instead of quoting around it. If that script does not exist, tell the engineer to re-run ./install.sh".
   - The draft uses no backticks anywhere, so the hook's double-quoted string needs no escaping and the script call stays byte-exact, with no delimiter beside it.
   - The wording names the tool's `timeout` parameter and not a command prefix, because a bare "maximum timeout" invites `timeout N <path>`, which the reviewer gate's command-word match does not see (row 91).
   - Remove the `${REPO_GIT_COMMON_DIR%/*}` interpolation.
   - Keep the "See claude-config README" pointer and `_lib_stray_marker_hint`.
   - Avoid "untracked", "isolation" and "spawn an agent" (row 22).
   - `:337` stays untouched.
   - Gate: `claude-hook-review:claude-hook-review`.
6. `claude/.claude/hooks/tests/test_require_worktree_for_git_writes.py`:
   - Replace `_recipe_from_reason` (`:914-922`) with `_script_call_from_reason`. It extracts the `<path>/worktree-ensure.sh '<branch>'` token from the reason, whatever its path, and asserts that a match exists. The extractor's regex (`\S*worktree-ensure\.sh '[^']*'`) and the exact-equality assertion below are unchanged, because the draft hint puts no backtick or other delimiter beside the call.
   - Replace the four-route `test_main_tree_deny_suggests_main_root_path` (`:976`) with one main-tree deny test: `git commit -m foo` from the main root. The test asserts four things:
     - the extracted call equals the hard-coded `~/.claude/scripts/worktree-ensure.sh '<branch>'`;
     - the reason contains `install.sh`;
     - the reason contains the character-set phrase;
     - the reason contains no `worktree add`.
   - Pin the hint's parameter phrasing (row 91): the reason contains "timeout parameter", and does not contain the wrapper-prefix reading "with the Bash tool's maximum timeout".
   - Rewrite `test_main_tree_deny_recipe_is_allowed_on_main_tree` (`:938`) as the allow test for cwd = main root.
     - Drop its `main_checkout` parametrization, since `spaced_main_root` is deleted.
     - It takes the call from a live deny reason through the extractor and fills `<branch>` inside the single quotes with each of three names:
       - `GH-1/new`, which takes the fast path and is allowed;
       - `GH-1/fix-git-hooks`, which reaches the parser and is allowed;
       - `GH-7/git`, which reaches the parser and gets the SENTINEL deny. The test asserts the SENTINEL arm's own reason text, and its docstring labels the case a known limitation.
   - Delete `test_main_tree_deny_recipe_is_allowed_from_linked_worktree` (`:950`). The allow test's docstring says why: the script call has no `git` word, so the gate allows it before cwd matters (row 19).
   - Delete `spaced_main_root`, `_assert_hint_targets_main_checkout` and `test_main_tree_deny_quotes_spaced_main_path`. Spaced-path coverage moves to file 3.
   - Drop the docstring saying "the `cd` route is where the allowlist is load-bearing".
   - Remove the comment at `:87-88`, because the reason no longer embeds a path. Update the section comment at `:903-905`.
   - Hard-code the expected script path. Deriving it from the script file would be a cross-domain read (row 43).
7. `claude-skills/skills/branch-management/SKILL.md`. Gate: `/skill-review`, hook-enforced. Today § "Anchor the session in the worktree" holds the recipe. Afterwards it holds the script protocol. Keep the heading byte-identical, because `nudge-worktree-anchor.sh:168` and `ready-for-review/SKILL.md:32` cite it (row 26).
   - `:56-59`: "the worktree recipe below" becomes "`worktree-ensure.sh` (below)".
   - `:61-63`: replace "Step 2 … gives the lookup" with the lookup itself: `git symbolic-ref --short refs/remotes/origin/HEAD` prints the name after `origin/`.
   - `:67-106`, draft wording:
     1. Branch names use only ASCII letters, digits, `.`, `_`, `/` and `-`. Rewrite a name outside that set before composing the call; never quote around it.
     2. Run `~/.claude/scripts/worktree-ensure.sh '<branch>'`, typing the branch name in place of `<branch>` inside the single quotes. Set the Bash tool's `timeout` to the maximum its description allows, because a kill mid-checkout leaves a state only the engineer can clear.
     3. Find the last line that starts with `next:`. The line just before it starts with `created `, `existing ` or `current `. Other lines come from git and the programs it runs, such as hooks.
        - On `created` or `existing`, make the `EnterWorktree{path: …}` call on the `next:` line your very next call.
        - On `current`, skip `EnterWorktree`. The working directory already is that worktree, and `EnterWorktree` refuses a target equal to it.
     4. On failure, read the last line that starts with `worktree-ensure.sh: `. Its second field is the token.
        - `usage`: call the script with exactly one argument.
        - `bad-name`: rewrite the name within the set in step 1.
        - Exit 2 with no `worktree-ensure.sh:` line: the script most likely never ran, usually because the name held a `'`. Rewrite the name only if it held a character outside the set.
        - After any exit 2, correct the call and rerun once. A second exit 2, or a no-line exit 2 from a call that already had one argument and a name within the set, goes to the engineer with the exit status and stderr.
        - `in-main`: ask the engineer to switch the main checkout to another branch, then rerun.
        - Exit 127 with no such line: the script is missing. Tell the engineer to re-run `./install.sh`.
        - A worktree-gate denial when the name is `git` or ends in `/git`: rename the branch, because the gate reads such a name as a git command. `EnterWorktree`'s create mode is not a substitute, because it takes no start point.
        - Any other failure, including a run the tool killed with no `worktree-ensure.sh:` line: report that line, or the exit status, to the engineer, and do not rerun.
          - Commands that write are the engineer's to run, wherever they are suggested. Examples include creating or attaching a worktree, deleting a branch, unlocking, removing or pruning a record, deleting a lock file, and moving or removing a directory.
          - Read-only commands such as `git worktree list` are fine.
          - A rerun the engineer asks for writes nothing while the failed run's branch or worktree remains, and reports that state.
        - Never fall back to a hand-typed `git worktree add`.
   - `:108-112`: delete the first three sentences, which the script now covers. Keep the `cd` warning, reworded to stand alone.
   - `:135-138`: re-enter "with the steps above" before any other command or dispatch.
   - If Verification step 1 could not run, add its fallback sentence.
8. `claude-skills/skills/tests/test_skills.py`: add a `TestBranchManagementWorktreeEnsureWiring` class beside `TestPrDescriptionCostSectionWiring` (`:1677`).
   - Its docstring reads "Wiring tripwire, not a behavioral test" and points at `test_worktree_ensure.py`.
   - Every phrase assertion collapses each run of whitespace to one space, in SKILL.md and in the phrase, before the substring test, because SKILL.md is hard-wrapped.
   - It asserts that SKILL.md contains:
     - `~/.claude/scripts/worktree-ensure.sh '<branch>'`;
     - `./install.sh`;
     - the character-set phrase;
     - the protocol line: `next:`, and the backticked `created `, `existing ` and `current ` with their trailing spaces. The bare words `existing` and `current` already occur in SKILL.md, so asserting them could never fail;
     - the writing-command bar: "Commands that write are the engineer's to run, wherever they are suggested". Only the default-mode permission prompt backs it otherwise (the Approach's Failure rule);
     - "Never fall back to a hand-typed `git worktree add`". With the bar above, it is one of two agent-facing bars on the runbook's attach command, which the gate allows from any tree (row 20);
     - the exit-2 bound: "correct the call and rerun once", "A second exit 2" and "goes to the engineer".
   - It asserts that `SCRIPTS_DIR / "worktree-ensure.sh"` exists, using the constant `test_scripts_are_executable` already uses. That test already covers the executable bit.
9. `docs/scripts.md`: one entry beside `worktree-removal-status.sh`, around `:255`. It gives:
   - the script's role and its two callers;
   - that it prompts under the default permission mode, like any argument-bearing call to a script here (`:261`);
   - that hooks and text-matched permission rules cannot see the inner git calls, so a consumer's own deny rule on `git fetch` or `git worktree add` does not apply inside it (G2);
   - a short block on clearing what a refusal leaves, for the engineer. Each item says what to check first and which command clears it. Commands use placeholders such as `<main-checkout>`, never an interpolated literal path, so the reader supplies the shell quoting. The dispatch confirms each command against git-worktree(1) and git-rev-parse(1) before writing it.
     - `branch-without-worktree`: first check `git worktree list` for a worktree rebasing or bisecting the branch, or a create still in flight. Then either attach it with `git -C <main-checkout> worktree add <main-checkout>/.claude/worktrees/<branch> <branch>`, which skips the script's path checks, or delete the branch. Which one is the engineer's call.
     - `add-unfinished`: first confirm no `git worktree add` for that path is still running. Then run `git worktree unlock`, then `git worktree remove --force`, which discards the partial checkout. A plain `remove` may refuse after the unlock (row 64). The branch remains, so the next run reports `branch-without-worktree`, which the item above covers.
     - `missing` with no `prunable` reason, a locked record whose directory is gone: unlock it, then `git worktree prune`. The branch remains, so the next run reports `branch-without-worktree`.
     - The stray-`.git` form of `path-in-use`: below `.claude/worktrees/`, read what a `.git` file points at, or inspect a `.git` directory, then move the directory aside. No git command clears it. At `.claude` or `.claude/worktrees` itself, the directory is a repository of its own, which the script does not support.
     - `fetch-failed`: if the network and credentials check out, look recursively for `*.lock` under `refs/remotes/origin/` in the git common directory (`git rev-parse --git-common-dir`). If one is there, first confirm no git process is running in that repository, then remove it. A killed fetch may leave such a lock, and it blocks the fetch only once the remote's default branch has advanced (row 64; the kill half is `[unverified]`).
   - that the script's header owns the checks, the writes, the stdout grammar, the exit codes and the supported layout.
10. `CHANGELOG.md:255`: rewrite the `### Fixed` bullet to say:
    - Worktree creation and entry are now one script call, `~/.claude/scripts/worktree-ensure.sh '<branch>'`. It finds the branch's worktree, or creates one under the main checkout from a freshly fetched `origin/<default-branch>` with no upstream.
    - It reports `current` when the working directory already is the worktree, where `EnterWorktree` would refuse.
    - The cwd-relative form nested worktrees.
    - `branch-management` and the worktree gate's main-tree deny message now point at the script.
    - Path conditions the script can check without writing are refused before git creates the branch. A failure inside git's own checkout or write, such as an unwritable path or a failing filter, can still leave a branch, which the next run reports as `branch-without-worktree`.
    - Re-run `./install.sh` (or re-stow) when `~/.claude/scripts` is a real directory (G4). Until then, the deny message says so.

    Replace the bullet's "Live on `git pull` with no re-install" claim.
11. `claude/.claude/hooks/deny-reviewer-tree-mutation.sh` and `claude/.claude/hooks/tests/test_deny_reviewer_tree_mutation.py` (row 91):
    - Gate: `claude-hook-review:claude-hook-review` on `deny-reviewer-tree-mutation.sh`.
    - One `if _lib_fragment_invokes_tool "$fragment" worktree-ensure.sh` in the Bash arm, after the terraform/tofu block. It matches the command word only, so `cat`, `grep` and `bash -n` of the script stay allowed.
    - The residual is a class: any command whose command word, after the gate's command-word resolution in `_lib.sh`, is not the script. The members are wrappers outside the runner skip list (`bash`, `sh`, `source`, `timeout N`, `exec`, `nohup`, `setsid`), syntax the fragment splitter does not unwrap (a brace group `{ <path> x; }`, `then` and `do` bodies, a bare `&`), and a glob- or variable-built command word. The class is accepted (Decision record 92). The header calls it "undecidable" and makes no tracker claim for wrappers (`deny-reviewer-tree-mutation.sh:69,84`).
    - The header explains the deny in one sentence and states the residual as that class.
    - Tests, in `TestWorktreeEnsureDeniedByName` plus one parametrize row:
      - Deny, six forms: the hint's byte-exact form, an absolute path, a relative path, the bare name, a second command after `cd … &&`, and `env X=1 <path>`.
      - A deny-reason test asserts the script name, "does not apply to review-only agents" and the sanctioned-alternative sentence.
      - A `worktree-ensure-arm` row in the existing reason-sentence parametrize asserts the same reason text.
      - Allow for callers outside the review-only set: `None`, `main`, `code-writer` and `general-purpose`.
      - Allow for reads: `cat`, `grep` and `bash -n` of the script.
      - Pin `bash <path>` and `timeout N <path>` as allowed, as two examples of the accepted class.
    - `docs/hooks.md` gets the class wording in the reviewer-gate entry, including that the script "fetches refs".
    - `docs/hooks.md:150` and the docstring of `test_reviewer_worktree_ensure_behind_unlisted_wrapper_allowed` drop the GH-1103 wrapper claim, matching the header. The docstring's "flip to deny when GH-1103 closes the gap" becomes: flips only if the runner skip list changes. Neither surface names a tracker for wrappers. The `CHANGELOG.md` bullet gets one sentence that says the gate denies the script "by command word". `docs/scripts.md` gets none, because the gate's behavior is documented once, with the gate.

**Consumers checked and unchanged.** staff-backend-engineer's rounds 1–2 cited these lines, unless the ledger verifies them: `require-worktree-for-git-writes.sh:337`; `require-worktree-for-file-writes.sh:149`, `:172`; `plan-it/SKILL.md:21`; `transcript_analysis/denials.py:152`; `test_transcript_review_trace.py:1754`; `marker.sh:201`, `:204`; `nudge-worktree-anchor.sh:150`; `CLAUDE.md:51-54`; `claude/.claude/CLAUDE.md:172`; `README.md:267-307`; `CONTRIBUTING.md:13-16`; `docs/skills.md:16`; `docs/hooks.md:148`; `branch-management/REFERENCES.md`; `git-feature-branch-sync/SKILL.md:19`; `test_require_worktree_for_git_writes.py:215-221`; `_lib.sh:2697`; `evals/fixtures/*.jsonl`; `test_config_lib.py:2454`; and `docs/worktree-bash-guard.md`.

Not touched: `claude/.claude/settings.json`, `require-worktree-for-file-writes.sh`, `conftest.py`, `select-tests.py`, `test_config_lib.py` and `.claude/plans/absolute-path-worktree-recipe.md`.

## Verification

Step 1 runs in the session before the dispatch, because it can change what files 3, 4 and 7 expect.
- Every git behavior the design rests on was observed on git 2.43.0.
- The tests re-pin rows 12, 13, 14, 15, 20, 35, 36, 39, 40, 53, 56, 65, 66 and 67 on whatever git runs them. A re-pin that fails on another git version is reported to the engineer, not patched in the test.
- Rows 45, 50, 61 and 64 rest on review observations only. File 3 seeds row 64's `initializing` state rather than producing it (row 68).

1. **Pre-dispatch probe** (row 3). The dispatching session runs it with no agent in flight. If the harness refuses it, the session hands the probe to the engineer to run via `!`.
   - `cd` into an existing subdirectory of this worktree, for example `claude/`.
   - Call `EnterWorktree{path: "<this worktree's root>"}` and record the result.
   - If `pwd` is not the root afterwards, `cd` back to the root before anything else.
   - Act on the result:
     - If the call succeeds, `current` stays exact equality, and a subdirectory gets `existing`.
     - If the call refuses, `current` covers the root and every path under it, compared with the trailing slash. File 3's lifecycle case and file 4's subdirectory case then expect `current`.
     - If the probe cannot run, keep exact equality. File 7 then adds one sentence: an `EnterWorktree` refusal that names the working directory means the session is already in the worktree.
2. **Red.** The dispatch writes files 3, 4, 6, 8 and item 11's test file first, and runs them against two kinds of stub:
   - a `worktree-ensure.sh` that exits 0 with empty stdout, made executable on disk with `chmod +x`;
   - lib functions that print nothing.

   The command is `<main-checkout>/.venv/bin/pytest claude/.claude/scripts/tests/test_worktree_ensure.py claude/.claude/scripts/tests/test_worktree_lib.py claude/.claude/hooks/tests/test_require_worktree_for_git_writes.py claude/.claude/hooks/tests/test_deny_reviewer_tree_mutation.py claude-skills/skills/tests/test_skills.py::TestBranchManagementWorktreeEnsureWiring`. Expected at red:
   - Every subprocess case fails on its own exit-code or stdout assertion, which comes before any state assertion. No case fails on a missing or non-executable file.
   - Every lib case fails except the controls that expect nothing. Each control shares a parametrization with a positive case that fails.
   - The hook deny test and all three allow cases pass at red. Commit `cc0addc2` already holds the extractor and a hint that points at the script. The one new test in file 6 that fails red is the `timeout`-parameter pin, because the hint is unchanged and still reads "with the Bash tool's maximum timeout".
   - Item 11's deny cases, its deny-reason test, and its allow, read and accepted-class cases all pass at red, because the working tree already holds the reviewer hook's `worktree-ensure.sh` arm. Red-first evidence for item 11 is step 3's first item-11 mutant (the new `if` removed), which requires reverting the arm.
   - The wiring tripwire fails on every SKILL.md assertion, while its existence check passes against the stub.

   The dispatch returns that output, then removes the stubs.
3. **Green.** The dispatch applies every Critical-files edit and reruns step 2's command until every test passes. If the lifecycle case finds an upstream (row 12), it stops and reports instead of working around it.
   - **Mutant check.** The dispatch first copies files 1 and 2 to a scratch directory outside the tree. For each regression below, it reintroduces the regression, reruns, and confirms that the named test fails on the named assertion. Then it restores the code:
     - `pwd -P` replaced by `pwd`: the symlinked-cwd case;
     - a plain `fetch origin <default>`: the narrowed-fetch case;
     - `GIT_TERMINAL_PROMPT=0` removed: the prompt case;
     - `export LC_ALL=C` removed: the locale case, on its recorded value;
     - `LC_ALL=C` assigned without `export`: the locale case, on its recorded value;
     - the `GIT_*` unset removed: the environment case;
     - `>&2` dropped from the add: the lifecycle case, on its byte-exact stdout;
     - `-b` changed to `-B`: the branch-appears case, on `add-failed`;
     - the `|| die fetch-failed` guard removed from `START_SHA`: the after-a-successful-fetch case, on its last line;
     - `worktree add`'s exit status trusted, as `|| die add-failed`: the state-decides case, on `created`;
     - the post-add lookup without its `.git` test: the hook-empties case, on `missing`;
     - an `-L` arm added to step 4's lookup `.git` test: the dangling-`.git` classify row, on `missing`;
     - the post-add lookup without its `outside` test: the redirected-add case, on `outside`;
     - the stray-`.git` check removed from step 6: the stray `path-in-use` case, on its token;
     - the `-L` arm dropped from step 6's stray `.git` test: file 4's target-check stray-form case for a dangling `.git` symlink at an ancestor, on `path-in-use`;
     - the `initializing` test removed from the lookup: the `add-unfinished` case, on its token;
     - the fetch moved above step 5: the `ref-conflict` case, which then gets `fetch-failed` because its remote was moved away.
   - **Item 11 mutants,** run the same way against a scratch copy of `deny-reviewer-tree-mutation.sh` and `require-worktree-for-git-writes.sh`:
     - the new `if` removed: the `TestWorktreeEnsureDeniedByName` deny cases;
     - the command-word match switched to any-token: the `cat`, `grep` and `bash -n` read-allow cases and the two accepted-class pins;
     - the `:438` hint wording reverted to the "maximum timeout" phrasing: the parameter-phrasing pin in file 6.
   - After the loop, it diffs files 1 and 2 and the two hooks against the copies and reruns step 2's command.
   - It returns:
     - the mutant output, with the green output;
     - `git --version`;
     - the summed `--durations=0` seconds for files 3 and 4.

     After the push, the session adds the CI runner's core count from the workflow's "Report runner core count" step.
4. **Scoped suite and lint**, from this worktree:
   - `<main-checkout>/.venv/bin/python3 claude/.claude/scripts/select-tests.py`;
   - `<main-checkout>/.venv/bin/pytest claude-skills/skills/tests/test_skills.py -k test_scripts_are_executable`;
   - `<main-checkout>/.venv/bin/ruff check` on files 3, 4, 6, 8 and item 11's test file;
   - `<main-checkout>/.venv/bin/shellcheck` on the script, `_worktree-lib.sh`, `require-worktree-for-git-writes.sh` and `deny-reviewer-tree-mutation.sh`;
   - `test -x claude/.claude/scripts/worktree-ensure.sh`. After staging, `git ls-files -s claude/.claude/scripts/worktree-ensure.sh` shows `100755`.
5. **Live run through the harness**, after the dispatch, from this session with no agent in flight, and with the Bash tool's maximum `timeout`.
   - Call this worktree's own `claude/.claude/scripts/worktree-ensure.sh` by absolute path. `~/.claude/scripts/` does not carry the script until merge, or, in real-directory consumers, until re-stow.
   - Use the live repository, not a throwaway clone. The question is whether a linked-worktree session may run the add under the main checkout (G3, row 16), and a clone under `$TMPDIR` would not ask it.
   - For this session's own branch, run from the worktree root. Expect `current` with this worktree's path.
   - For `GH-1234/ensure-probe`:
     - Before the probe's first call, record read-only whether the probe path and `refs/heads/GH-1234/ensure-probe` exist. The teardown depends on it.
     - Expect `created <main-checkout>/.claude/worktrees/GH-1234/ensure-probe`.
     - Rerun it and expect `existing` with the same path.
     - Do not call `EnterWorktree`.
   - The `created` call's fetch advances `refs/remotes/origin/main` in the shared repository. That is its intended effect.
   - Record:
     - for each call, whether it prompted, and the session's permission mode (row 42);
     - whether the Claude Code sandbox was on, asking the engineer if the session cannot tell. If it was off, the PR body keeps row 16's prediction `[unverified]`;
     - the exact text the agent sees for the `created` call, both streams as the tool presents them, and which stream comes last. If stderr comes after stdout, report it to the engineer before merge (row 53);
     - whether a clean `existing` call shows only the two lines (row 53).

     A harness refusal of the script call goes to the engineer. An `add-failed` that leaves the probe branch is the sandbox result row 16 predicts.
   - Tear down at once, removing nothing but the probe. Do it only if neither the probe path nor the branch existed before the probe's first call and that call's output shows `created`, whichever stream the harness presents it on. Otherwise, including on `existing` or a non-zero exit, report the recorded pre-call state, the last `worktree-ensure.sh:` line, `git worktree list --porcelain` and `git for-each-ref refs/heads/GH-1234/ensure-probe`, say a leftover probe is the engineer's to clear, and stop.
     - Run `git worktree remove '<probe path>'` without `--force`. The session proposes it and the engineer approves it at the default-mode permission prompt. If it refuses, report the refusal and stop.
     - Prove, read-only, that the probe branch has no commits of its own: `git merge-base --is-ancestor GH-1234/ensure-probe origin/main` exits 0. A non-zero exit means report the status and hand over nothing. Otherwise hand `git branch -D GH-1234/ensure-probe` to the engineer to run via `!`. The session deletes no branch.
6. **Residual sweep.**
   - `git grep --untracked -n "worktree add" -- claude-skills/skills/branch-management/SKILL.md claude/.claude/hooks/require-worktree-for-git-writes.sh` should show the hook's generic header comment at `:12` and, from SKILL.md, only its "Never fall back to a hand-typed `git worktree add`" line. SKILL.md is hard-wrapped, so that line may split and not match: one line or two both pass.
   - `git grep --untracked -ln "worktree-ensure.sh"` should list exactly these files:
     - the script and `_worktree-lib.sh`;
     - `test_worktree_ensure.py` and `test_worktree_lib.py`;
     - the hook and the hook test;
     - SKILL.md and `test_skills.py`;
     - `docs/scripts.md` and `CHANGELOG.md`;
     - `deny-reviewer-tree-mutation.sh` and `test_deny_reviewer_tree_mutation.py`;
     - `docs/hooks.md`;
     - this plan file.

     `--untracked` covers the files not yet staged and honors `.gitignore`, so `agent-reviews/` stays out (row 58).
   - Run `grep -oE '\b[A-Z]{2,}-[0-9]+\b'` on this plan file. Every hit must start with a prefix on `deny-private-project-refs.sh`'s allowlist (`:424`).
7. **Review gates.** Run `/skill-review` on SKILL.md, then `claude-hook-review:claude-hook-review` on both hooks (`require-worktree-for-git-writes.sh` and `deny-reviewer-tree-mutation.sh`), then `/code-review` over the whole diff.
8. **Engineer-run bash 3.2 check, before merge.** CI has no bash 3.2 job (row 47).
   - The engineer uses macOS `/bin/bash`, or any bash 3.2 binary already on hand. The session fetches none.
   - Run `bash -n` on `worktree-ensure.sh` and `_worktree-lib.sh`.
   - Run a sourced smoke under `set -u`: source `_worktree-lib.sh` and call the five functions with a one-element `ALL_WT_*` array, an empty ref list and a non-empty one. `worktree_ensure_name_check` gets one valid name, `a;b`, `é`, and a name with a trailing newline. If bash 3.2 read the pattern as a literal string, it would reject every name, and the valid name catches that.
   - Run three read-only whole-script calls in the engineer's repository:
     - no argument: expect `usage`, exit 2;
     - `'a;b'`: expect `bad-name`;
     - a branch that already has a worktree: expect `existing`, with no network call and no write.
   - The PR body lists the check as pending until the engineer reports it, or as "not run" if no bash 3.2 is available.

## Out of scope

- **`README.md:300-301` and `CONTRIBUTING.md:14-15`.** They are correct human shell examples at the main checkout (row 27). They start from the main checkout's `HEAD` with no fetch, but that is a different bug shape from nesting. Noted for the reviewer.
- **Repositories whose first `git worktree list` record is not the checkout root.** The engineer ruled bare repositories, `--separate-git-dir` clones and absorbed submodules unsupported (row 86). A checkout whose `.git` is a symlink joins them on the architect consult's extension and the round-7 probes, not on the engineer's quote (row 78). All are unchecked, and the script takes that record as the main checkout (row 78):
  - A create can exit 0 with the worktree under that record's `.claude/worktrees/`, inside or beside the git directory. A fresh `git clone --bare` stops earlier, at `no-origin-head`. The shipped header states this consequence in one clause.
  - A worktree correctly placed under the real checkout's `.claude/worktrees/` is refused as `outside`.
  - `git worktree remove` and `git branch -D` undo a fresh create. For a checkout whose `.git` is a symlink, that is inference, not a run: no probe ran the undo on a symlinked-`.git` layout (round-7 probes did create the link; see row 78) `[unverified]`. The empty `.claude`, `.claude/worktrees` and `.claude/worktrees/<ticket>` directories stay, and so do the fetched refs and objects, as after any fetch.
  - A worktree a session has used carries the collision guard's lock, so `git worktree unlock` comes first (row 34). `remove` may then need `--force` (row 64), which discards uncommitted work, and `git branch -D` discards the branch's commits. Those two discard consequences are the architect's inference from git's documented behavior, not observed here `[unverified]`.

  The prose recipe and the old hint had the same limit.
- **Making the `test_config_lib.py` helper docstring less brittle.** The engineer wants it as its own change in a separate PR (rows 84, 85). This plan adds no importer of that helper.
- **Resolving what a refusal leaves behind.** These stay the engineer's call, and there is no `--attach` flag:
  - attaching or deleting a branch that has no worktree (rows 64, 65);
  - removing, unlocking or pruning the record that `missing`, `add-unfinished` or `path-in-use` names;
  - moving or removing a stray `.git` directory `path-in-use` names;
  - removing a stale ref lock behind `fetch-failed` (row 64).
- **The reviewer gate's other command shapes.** Any command whose command word, after the gate's command-word resolution in `_lib.sh`, is not the script. The members are wrappers outside the runner skip list (`bash`, `sh`, `source`, `timeout N`, `exec`, `nohup`, `setsid`), syntax the fragment splitter does not unwrap (a brace group `{ <path> x; }`, `then` and `do` bodies, a bare `&`), and a glob- or variable-built command word still reaches the script from a review-only agent. The engineer accepted the class as the session described it (Decision record 92). The non-wrapper members rest on the author's trace of `_lib.sh`, not on that description. The other git-writing stowed scripts are not named, and that rests on the quote's "add nothing else", not on the class acceptance. Whether GH-1103's scope covers wrappers is `[unverified: issue body not read]`; repo text describes it as the gate's closed enumeration (`deny-reviewer-tree-mutation.sh:69,84`).
- **Serializing concurrent same-branch runs.** No lock is added. The create-only ref settles them (row 45).
- **The same-user window.** Step 6 is not re-run before the add, and the window spans the fetch (row 40).
- **Case-insensitive filesystems.** Two branch names that differ only by case map to one path, and the comparisons are case-sensitive (row 50).
- **Git versions other than 2.43.0,** and adds run by hand under a translated locale. Every observation here is from 2.43.0. A hand-run add under a translated locale writes a lock reason `add-unfinished` does not match (row 68). Nothing exercises the 2.31 floor, and no real-git test observes the `initializing` literal.
- **Other start points and remotes.** This covers:
  - stacked branches, whose start point is not the default tip;
  - remotes other than `origin`;
  - repos without `origin/HEAD`;
  - a stale `origin/HEAD` after a remote renames its default branch. The script then fetches the old name if it still exists.
- **A branch that exists on origin but not locally.** It gets `created` from the default tip, as under the prose recipe.
- **Issue item 3**, a hook that denies nested `git worktree add` from other commands. The script refuses nesting only for its own creations.
- **Sandbox compatibility.** GH-1104 owns it. Under G3, the add fails after creating the branch, as the prose recipe's did (row 16). When GH-1104 resolves it, the grant should cover only `<main>/.claude/worktrees` and the shared repository, rather than exempting the script from the sandbox.
- **The other deny arms.** They stay unchanged under D1. Noted for the reviewer:
  - `:149`'s "cd into" advice conflicts with SKILL.md's `cd` warning;
  - `:337`'s remedy does not fit a parse failure when the session is already in a worktree.
- **The gate parser's `git`-token rule** (row 19). A branch named `git` or `*/git` is denied from the main checkout with the SENTINEL reason. The hook test pins this, and SKILL.md says to rename the branch. Fixing the parser is a separate change to an `irreversible`-tier gate.
- **NUL-delimited porcelain in `_worktree-lib.sh`.** `-z` needs git 2.36, above the 2.31 floor (row 37). A newline-forged record is accepted under the cooperative threat model (row 40).
- **No override for `symlinked-path`, or for a `.git` on `.claude` or `.claude/worktrees`.** A consumer whose `.claude` or `.claude/worktrees` is a symlink, or a repository of its own, is refused, where the prose recipe worked. Accepted for path containment.
- **`cd` into the worktree without `EnterWorktree`.** The script cannot tell this apart from an entered session, and reports `current` either way (rows 4–5).
- **A wildcard allow rule, `Bash(~/.claude/scripts/worktree-ensure.sh *)`.** Declined. CLAUDE.md Safety forbids wildcards in `permissions.allow`, and the git chain it replaces also prompts (G5, row 42).
- **Syncing this branch with `origin/main`.** It happens after plan approval, outside this plan, per the engineer's decision as the dispatching session relayed it.
- **`nudge-worktree-anchor.sh:168`, `claude/.claude/CLAUDE.md:42` and `git-state-safety/SKILL.md:36`.** The first runs only when the cwd is the main checkout, and it already defers to the skill. The other two are throwaway checkouts.
- **A `claude/.local/bin/` wrapper.** The script is for agents, not humans.
- **Post-merge measurement.** A transcript query on the "is the current working directory" error is the success metric. Any figure published from it follows `docs/private-project-redaction.md` § "Publishing a tooling measurement".
- **Noticed in passing, not fixed here:**
  - `CHANGELOG.md` has two `### Changed` headers under `## [Unreleased]` (`:7` and `:243`).
  - `README.md:522` says linked worktrees sit exactly three levels deep, which a `<ticket>/<slug>` branch name makes four.
- **Preserved records:** `docs/case-studies/*`, every earlier `.claude/plans/` file, and `docs/worktree-bash-guard.md` § "Site sweep".
