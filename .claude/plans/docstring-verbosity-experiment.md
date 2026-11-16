# Measure what drives docstring and comment verbosity in agent-written code

## Context

Goal: build a reusable eval runner that tells us whether agent-written docstrings and comments are verbose because of the model's built-in habits or because of this toolkit's own instructions.

Ask: "I'm trying to figure out why agents are so verbose with docstrings, which I think is leading to imprecision and review round findings. I don't know if there's prose in the claude-config toolkit that influences it or if it's just built-in behavior. And if we're not certain, we could probably design an experiment using an agent that has omitClaudeMd and no allowed skills and see how it does docstrings?" Later, on task source: "Why not use replayed claude-config PRs and [other private repos' PRs] if you need? Actually I authorize you to look at every root on this machine."

Why now: review rounds keep finding comment and docstring prose that is false, stale or overstated. The toolkit's own measurement for this (`.claude/plans/prose-claim-truth-check.md`, Context) counted about 50 post-first-commit fix commits out of 819 in the 150 most recent merged PRs. A commit qualified when its headline says it corrects a false, stale or overstated prose claim. About 20 of those roughly 50 commits are in code comments or docstrings. That count is a lower bound taken from commit headlines, and it covers this repository's history only. No measurement yet separates the model's default style from the effect of the instructions in `claude/.claude/CLAUDE.md` §Durable text, `claude/.claude/agents/code-writer.md`, and `claude/.claude/agents/comment-discipline-reviewer.md`.

Intended outcome: a local runner that replays past commits as coding tasks under at least two configuration arms, counts the comment and docstring lines each arm adds, and writes per-run records to gitignored storage. A third arm that removes only the §Durable text rules is designed but gated on the open isolation questions below.

Engineer decisions this session, as the labels they selected:
- Deliverable: "Reusable evals/ runner (Recommended)".
- Arms: "A and B now, C gated (Recommended)".
- Results: "Gitignored .local/ only (Recommended)".
- Account: "Each repo's own account (Recommended)".
- A source repo that is not on this machine: "Drop it for now (Recommended)".
- How arm A runs: "Subagent for both arms (Recommended)".

## Approach

Build `evals/replay_bench/`, a local runner that replays a source repo's past commits as coding tasks. A thin headless dispatcher hands each commit's message to one subagent. Arm `toolkit`, the Context's B, dispatches the production `code-writer` under the account's full config. Arm `bare`, the Context's A, dispatches a fixture-local `bare-writer` with `omitClaudeMd: true` and a one-line body, in a fixture whose settings exclude the account's rule files. Both arms may read the account's agent and skill files through exact Read allow entries, so `code-writer` can read its reviewer files. Every launch gets an environment built from an allowlist. No sandbox contains the subagent, because steered agents are out of scope for this runner (row 47). The runner classifies every line the subagent added as code, comment, docstring, blank or prose, keeps one record and one diff per run under gitignored `evals/replay_bench/.local/`, and prints the paired arm difference over tasks to the terminal only.

The work ships as two pull requests, split at the Phase 2 to Phase 3 boundary (D4, row 124):
- The first PR, merged as #1452, holds Phases 1 and 2 and the `select-tests.py` rows for its own files (Phase 5a). Its last dispatch ran after Verification step 1c, which set the `--max-base-entries` default (Fixture). It needs no `claude -p` run.
- The second PR, on a follow-on branch from `main` after the first merges, holds Phases 3 and 4 and the rest of Phase 5 (Phase 5b). Verification step 1b ran at its start (row 164), and the live checks run before it merges.
- This file stays whole and ships with the first PR (row 127). The second PR edits it in place, so each later change to the plan ships with the code it governs. Its Phase 3 to 5 sections are the second PR's plan, and main holds that design before its code. M21 names the two alternatives set aside.

### How a replay runs

`evals/README.md` § "Replay bench" and the modules under `evals/replay_bench/` state what the runner does. This section keeps the decisions and their rationale, under the labels the ledger cites.

**Tasks.** The selection rules, refusals, brief and recorded fields are in `evals/replay_bench/tasks.py` and `evals/README.md` § "Tasks".
- The runner first drops from a commit's changed paths every path the fixture prune removes, so a commit that also edited an instruction file still yields a task.
- A prompt over `--max-prompt-chars` is skipped, never truncated (row 33).
- The `--max-base-mb` and `--max-base-entries` defaults come from the build timing under Fixture. They also bound disk, since each attempt holds about three copies of the base tree, as the fixture's work tree, its `.git` and `baseline.git`, across four worker threads (row 82).
- The `export-attributes` check matches the attribute text anywhere on a line, a comment or an unset form included. It skips more than git would apply but needs no attribute parser (row 131).

**Fixture.** `tasks.build_task_fixture` builds it from `fixture_repo.build_single_commit_repo`, which keeps the bench's archive-only extraction (rows 27, 28).
- The runner exits 2 when the fixture directory lies under `$HOME`, where ancestor instruction files would load.
- The prune removes the checkout's own instruction files, so nested discovery loads none into either arm (rows 23, 24). It then resolves every remaining symlink with `os.path.realpath` and raises when one resolves outside the fixture (rows 83, 103, 108).
- After the commit the runner writes `.claude/worktree-optout`, the `bare-writer` agent and the arm's settings file, and keeps `/.claude/` out of the diff through `.git/info/exclude`. The opt-out keeps machine-level worktree enforcement from denying the agent's writes (rows 21, 22).
- `--fixture-git-timeout-s` defaults to `FIXTURE_GIT_TIMEOUT_S` and stays out of the manifest comparison, since it changes no result. The defaults come from this rule, applied to Verification step 1a's timing of four concurrent fixture builds on synthetic text trees (rows 88, 106):
  - the `--max-base-mb` default stays 100 when a 10,000-file tree's slowest build takes 60 s or less;
  - the `--max-base-entries` default starts at 100,000 and halves until a tree of that many files builds within 60 s;
  - `FIXTURE_GIT_TIMEOUT_S` is three times the slowest build across the trees the default bounds admit, the step 1c corner included, rounded up to a whole 10 s.
- The 60 s ceiling, the 100,000 starting point and the multiplier are judgments, not measurements.
- Step 1a's relay gave no byte total for the halving trees, and nothing measured locates the jump between 12,500 and 25,000 files. So Verification step 1c timed two more trees before the first PR's last dispatch: the corner the bounds admit, 12,500 files totaling 100 MB, and 17,500 files.
  - Neither tree exceeded 60 s, so step 1c did not stop.
  - Had either exceeded it, the stop would have gone to the engineer, because the 12,500 default is the engineer's accepted figure (row 129).
- Row 106 holds the measured series. Under the rule above, `--max-base-mb` defaults to 100, `--max-base-entries` to 12,500, and `FIXTURE_GIT_TIMEOUT_S` to 110 s, three times the 33.5 s corner build at 12,500 files totaling 100 MB, rounded up.

**Arms (`replay_bench/arms.py`).**
- `bare` dispatches a fixture-local `bare-writer` that copies `code-writer`'s tools, model and effort. Its fixture settings hold only `claudeMdExcludes`, because `omitClaudeMd` still lets path-scoped rules load (rows 16, 115). The account's `CLAUDE.md` stays off the exclude list: the dispatcher's `session_start` load of it is the run's positive control, and `omitClaudeMd` already drops it from the subagent (row 15).
- `toolkit` dispatches `code-writer` by name, which resolves to the account's stowed user-scope agent. The `SubagentStop` self-review hook, whose matcher is `code-writer`, therefore fires as in production and never sends `bare-writer` back (row 13). It writes no fixture settings file.
- Both arms send the same inner prompt, `tasks.build_inner_prompt`, wrapped by `build_dispatcher_prompt`.

**Account preflight** (`replay_bench/account.py`; `evals/README.md` § "Account preflight and launch environment"). The probe and the readiness check use their own `subprocess.run`, not `_read_environment_field`, whose error text carries the child's stderr (row 58). The readiness check does not show that the account is logged in, so `smoke` is the first check that does (row 70).

**Launch environment** (`replay_bench/launch.py`). The child's environment is a named allowlist, not inherited. Inherited, it would hand the subagent's Bash the runner's own session variables, such as another account's token, and route an honest agent's command to the wrong account (rows 51, 52). Which proxy forms the CLI reads is unchecked, so the allowlist passes each form the runner's environment sets.

**Launch.** Each attempt runs the `env -i` prefix, then `review_bench.runner.build_dispatch_command(build_dispatcher_prompt(<arm agent>, <inner prompt>), ...)` with three additions:
- `--permission-mode acceptEdits`, which both arms' subagents inherit (rows 25, 26);
- a `--settings` JSON that carries one `InstructionsLoaded` command hook and nothing else (rows 17, 19);
- last in the argv, `--allowedTools` followed by exact `Read(//<path>)` entries, each its own argv element (rows 116, 117).

The Read allow entries let `code-writer` read the reviewer and skill files its body names, which `acceptEdits` denies outside the fixture (rows 26, 118).
- The set is wider than the reviewer files: every agent and skill file under the roots. Parsing the reviewer names from the evaluator or from `code-writer.md`'s table would copy the evaluator's name-to-path rule.
- The per-attempt generation catches a file added to `agents/` or `skills/` mid-campaign, which the commit-based drift check cannot see.
- A `bare` Read of any listed file still fails `arm-leak`.

**Validity.** The runner calls `evaluate_run_validity` unchanged (row 30). Its own checks in `replay_bench/checks.py` read the subagent transcript's `tool_use` entries, since a run that reached outside its fixture no longer shows what its arm alone wrote.
- The Bash check is a bounded scan of the command text, whose blind spots the module docstring of `checks.py` lists (row 161).
- The checks detect a write or a path outside the fixture after it happened. They guard the measurement, not the machine.
- Each run records how many calls were denied (rows 48, 165), since a denied write can empty one arm's diff without failing validity.

**Instruction-load check** (`replay_bench/checks.py`). The runner reads its own `<attempt dir>/instructions-loaded.jsonl` as untrusted input and classifies each load.
- `bare` fails `arm-leak` on any dispatcher load other than its expected `CLAUDE.md`. The hook logs no line for what a subagent inherits from its dispatcher (row 18), so a file the dispatcher loaded could reach the subagent unseen.
- `toolkit` has no per-run load assertion, for the same reason. `smoke`'s block calibration checks inheritance once per account instead.
- The account-hook check shows that the account's hooks ran beside the `--settings` hook (rows 19, 54).

**Retry** (`replay_bench/attempt.py`; `evals/README.md` § "Retry and missing reasons").
- One attempt can meet several reasons. All five instrument reasons sit in one constant and all five halt the campaign, so the precedence changes the recorded reason and not the outcome.
- [unverified] An auth or API error reported as a result event before the hooks fire would record `instrument-missing`, `instrument-log-invalid` and `account-hooks-missing` and halt the campaign instead of retrying. Whether the hooks fire before the first model call is unchecked.

**Diff and classifier** (`replay_bench/comment_metrics.py`, `replay_bench/diffing.py`). Every post-run git call uses the runner-owned `baseline.git`, never the fixture's own `.git` (rows 49, 50). A changed file is never read through its work-tree path (row 77). The extension table and the test-path rule are data a new language or convention extends (rows 35, 36, 78).

**Cleanup.** Every attempt ends in a `finally` (`replay_bench/attempt.py`). It leaves the attempt's `<config-dir>/sessions/` file, as every Claude Code session does (row 154).

**Campaign (`run`, `smoke`)** (`replay_bench/campaign.py`, `replay_bench/smoke.py`; `evals/README.md` § "Run" and § "Smoke"). Each task is one block. `run` needs a passing `smoke` on each account, which puts `smoke`'s checks, and the engineer's read of what it prints, ahead of every paid block on that account. The per-arm stop applies `all_runs_missing` to each arm, because over the whole block it would miss one arm failing alone, as `bare` does on a machine with a managed-policy `CLAUDE.md` (row 74).

**Block calibration.** The hook cannot show the `CLAUDE.md` a subagent inherits (row 18), and no source read says that `bare`'s exclude holds under `-p` (row 115). So the block's own two runs calibrate the instrument. They share one task, one brief and one inner prompt (rows 136, 137):
- Positive control: the `toolkit` run's log holds a subagent `path_glob_match` load whose realpath lies under the account's `rules/` directory at any depth, compared by path component, not by string prefix. Otherwise `instrument-missing`, since `bare`'s leak check would then pass blind. The smoke task must touch a rule-matching path for it to hold, and a task that does not fails rather than passing empty (row 145).
- Exclude: the `bare` run's own per-run checks fail it as `arm-leak`. That shows the exclude holding only when `bare`'s subagent read a rule-matching file (row 145). The printed exclude note and its residual sentence (`EXCLUDE_NOTE_RESIDUAL` in `checks.py`) never gate (row 143).
- Inheritance: the `toolkit` run's first-turn prompt size, minus the `bare` run's, minus the body term, is at least the margin. Otherwise, or when either size is null, `arm-config-missing`.
- The verdict is a pure function of the `toolkit` run's parsed log, both first-turn sizes, the character counts below, and the rule files the `toolkit` dispatcher loaded at `session_start`. It returns every failed condition, so one paid block shows every problem it can. The inheritance check is token arithmetic, so it stays computable when the control fails.

The terms, each a character count over 4:
- the margin's first term, `0.5 × chars(account CLAUDE.md) / 4`, half the `CLAUDE.md` estimate;
- the margin's second term, `chars(rule file) / 4` for each rule file the `toolkit` dispatcher loaded at `session_start`, summed and not halved, so an unscoped rule, which `bare`'s exclude drops, cannot stand in for `CLAUDE.md`;
- the body term, `(chars(code-writer body) − chars(bare-writer body)) / 4`. Each body is its agent file's text after the frontmatter. `code-writer`'s comes from the account's `<config-dir>/agents/code-writer.md`, the file the dispatcher resolves. It is never this checkout's copy. The term removes the one other known difference between the two subagents' first turns.

Row 138 states the body term's weak point and its fallback.

**Entry point.** The repository's Python floor is stated in the root `README.md` § "Requirements". The runner itself needs Python 3.12 or newer. `def _without_child_output[ResultT]` in `evals/replay_bench/runner.py` uses PEP 695 syntax. `evals/run_replay_bench.py` imports that module at the top, so an older interpreter fails at import before `main()` runs.

**Summary (`summarize`)** (`replay_bench/summary.py`). It prints to the terminal and writes no file (rows 4, 10). The primary outcome, fixed before any run, is comment-plus-docstring words per added non-test code line (row 63). The paired difference `toolkit − bare` over tasks uses `analysis.bootstrap_interval` over tasks as singleton clusters (row 75). What a null difference shows is stated in `evals/README.md` § "Replay bench" (row 62). Every other figure the records hold stays out (row 157).

### Why both arms dispatch a subagent

The session's earlier evidence described a headless main session doing the task itself: `claude -p --safe-mode --disable-slash-commands` for arm A, and plain `claude -p` for arm B. This plan replaces that mechanism (row 3, D1), for these reasons.
- Production comment text is written by `code-writer` subagents, per `CLAUDE.md` "Delegated code-writing dispatches to `code-writer`".
- A full-config headless main session carries the Main-session rules: it delegates, runs `/code-review`, and commits and opens a pull request under autonomous shipping. Arm B would then measure reviewed, shipped output instead of what the writing agent produced.
- Under one account's settings, both subagents run the same hooks (row 14) and neither holds the `Skill` tool (row 12). The arm contrast is then the instruction text: `CLAUDE.md`, rules, `code-writer`'s body, and the one production hook that matches `code-writer`. The session's open hook confound stops being an arm difference.
- The Ask's first message proposed this shape (row 8).

### Arm C, designed and gated

Arm C ships later as a pair of arms sharing one delivery route, so their contrast holds the route constant:
- `toolkit-copy`: a fixture-local copy of the account's `code-writer` body, named outside the `SubagentStop` matcher, plus the full `CLAUDE.md` through the route;
- `no-durable`: the same copy with the §Durable text channels removed (D3), plus `CLAUDE.md` without its `### Durable text` subsection, through the same route.

`no-durable − toolkit-copy` isolates the rules. `toolkit − toolkit-copy` measures the route and the hook together. Adding the pair takes arm entries and fixture extras, plus the `smoke` prompt-size check D2(a) names for it. `run`'s smoke gate checks the record's arms, so the pair needs its own passing `smoke` on each account before any paid block.

### Open decisions for the engineer

- **D1 (resolved).** For arm A's shape, the engineer selected "Subagent for both arms (Recommended)" (row 3).
- **D2 (arm C only).** The route that delivers an edited `CLAUDE.md`:
  - (a) `claudeMdExcludes` in the fixture's `.claude/settings.json` naming the account's `CLAUDE.md`, plus the edited copy as the fixture's root `CLAUDE.md`. Recommended, because it keeps the `CLAUDE.md` channel.
    - The pair's expected `session_start` load is that fixture copy, so `instrument-missing` fires when the dispatcher shows none. The pair excludes the toolkit, so any other dispatcher load, the account's `CLAUDE.md` included, fails the run.
    - The memory page does not say whether `claudeMdExcludes` reaches the user-scope `CLAUDE.md` (row 115). The pair's own `smoke` settles that from the dispatcher's log.
    - The pair's subagents inherit `CLAUDE.md` with no log line (row 18), so the pair's `smoke` needs a prompt-size check of its own, designed with the pair.
  - (b) `omitClaudeMd: true` with the edited text inlined into the agent body, which is a system-prompt channel.
  - (c) A separately logged-in config dir per account. Each needs the engineer's own login and a stow, and copying credentials is barred (row 11).
- **D3 (arm C only).** What "removes only the §Durable text rules" removes. Both readings are correct:
  - (i) the `### Durable text` subsection of `CLAUDE.md` alone;
  - (ii) every channel stating those rules to `code-writer`:
    - that subsection;
    - the two `code-writer` baseline sub-bullets that restate it, "Write any comment as a one-line durable fact" and "Don't restate what the code already shows";
    - the last bullet of its self-review step 6;
    - the comment-discipline row of its self-review table;
    - the comment-length bullet of `claude/.claude/rules/shell-script-conventions.md`, which reaches a subagent as a path-scoped rule (row 16).
  - Under D2(a), removing that rule bullet means excluding the rule file and shipping an edited copy as a fixture rule, which the arm then allows as a fixture-file load.
  - Recommended: (ii). `code-writer` restates the rules and reads `comment-discipline-reviewer` from disk, so (i) can read as "no effect" from redundancy alone.
- **D4 (resolved).** On the size decision, the engineer said "I accept your recommendation on splitting" (row 124), so the work ships as two PRs split at Phase 2 to Phase 3 (Approach, rows 126, 127). This supersedes the one-PR choice of rows 68, 84, 99 and 110.

### Alternatives set aside

- `--agents` JSON for `bare-writer`: `omitClaudeMd` is documented as a frontmatter field only (row 15), and the bench already installs arm agents as fixture-local files (row 29).
- `build_two_commit_repo` for the fixture: it commits the head tree, so `git show HEAD` would hand the agent the answer (row 28).
- `RunRecord` with `append_run_records` and `read_run_records`: `RunRecord` holds reviewer fields such as `findings_text` and read statistics, and per-run atomic files need neither torn-tail repair nor dedupe.
- A third-party lexer such as pygments: a new dependency for accuracy the paired contrast does not need (row 35, Out of scope).
- A `direnv exec <source-repo>` launch prefix: it overlays the runner's whole environment and the source `.envrc` onto a Bash-holding agent (rows 51, 52), and it reruns the `.envrc` on every launch.
- `-c` overrides for `core.fsmonitor`, `core.hooksPath` and filter keys on the fixture's own `.git`: a denylist of config keys misses any key it does not name, and the runner-owned copy needs no list (row 49).
- A Python diff of the work tree against the base content: it reimplements `git diff` and its symlink handling for no gain over the runner-owned copy.
- Attributing hook-log lines by order, the dispatcher's first `session_start` load of each file: a subagent-only startup load and a duplicate dispatcher load both misattribute, where `agent_id` does not (row 53).
- Claude Code's sandbox, its credential deny list and `smoke` probe, the preflight refusals that kept its write set closed, and the further containment layers review proposed: the engineer said "No worries about steered agents here." (row 47). The session proposed which layers to drop. Each layer it kept stands on grounds other than containment:
  - the environment allowlist, on account routing (rows 51, 52);
  - the path checks and `baseline.git`, on measurement (rows 49, 50);
  - the trusted-author filter, on risk reduction: it limits how much third-party text reaches an agent holding Bash (rows 45, 61);
  - the README trust warning, on disclosure of the residual risk (row 45).
- A per-arm stop at the first block for a task-specific failure, such as a fixture build that raises. That block then halts every resume at the same place. Moving past it needs a skip mechanism or a task-set edit. The manifest's task-set hash turns such an edit into a new campaign, which discards every completed paid block. The chosen rule instead marks the block complete, so a resume always has a way forward.
- Holding a failed block incomplete until the next block passes. Any halt in between leaves it incomplete, so a resume reruns and pays for it again. Two failed blocks next to each other halt every resume at the same pair.
- Charging each swept attempt that has no spend line at the cap on resume. A write-ahead entry holds only a task ID, a directory and a session ID (row 85), so the charge needs spend lines with no arm, run index or attempt number. It also needs a rule against charging one attempt twice. Both serve to close an undercount of up to `DEFAULT_WORKERS` attempts per hard kill (row 155), which the resume prints instead.
- A stated minimum Python patch level for the tar filter's link normalization, instead of the prune's realpath check: no CPython changelog was read, and the check holds on any interpreter for about ten lines (row 108).
- Raising `_LOCAL_GIT_TIMEOUT_S` itself: it widens the hang bound for every review-bench caller, none of which this change needs to alter. Keeping it and lowering `--max-base-mb` until a build fits 10 s instead would drop the larger repos from the task source.
- Reading a changed file's post-change text from its work-tree path: an agent-made symlink would point that read at a FIFO or at a file outside the fixture, where a `baseline.git` blob is exactly what `add -A` indexed (row 77).
- A spend reservation at each block's nominal ceiling: it is simpler, but it charges a block its whole ceiling until the block ends, so the stops would act on spend that never happened.
- No smoke gate on `run`, with the per-arm stop as the only guard: a broken premise would first surface in a paid block of `2 × k` runs, and nothing would tie the engineer's read of `smoke`'s printout to the environment `run` uses.
- `claudeMdExcludes` in both arms: it would strip from `toolkit` the rules production's `code-writer` receives.
- `claudeMdExcludes` in the `--settings` JSON: the memory page names the user, project, local and managed layers, not the flag layer, and the `--settings` JSON stays the same in both arms (row 115).
- Excluding the account's `CLAUDE.md` in `bare` too: the dispatcher's `session_start` load of it is the per-run positive control, and `omitClaudeMd` already drops it from the subagent.
- Read allow entries in the fixture's project settings: a never-trusted folder's `permissions.allow` is not used under `-p` (row 116).
- `--add-dir <config-dir>`: under `acceptEdits` it auto-approves edits in that directory, and it loads that directory's `CLAUDE.md`, `.claude/rules/*.md` and `CLAUDE.local.md`, per the permissions pages as relayed.
- `--bare`: it skips hooks discovery and `CLAUDE.md`, and "Hooks still don't run in bare mode when you pass them with `--settings`", per the CLI reference as relayed, so the instrument hook would not run.
- A wildcard Read rule over `agents/` and `skills/`: `claude/.claude/CLAUDE.md` §Safety says "No wildcards in `permissions.allow`.", and this plan holds `--allowedTools` entries to the same rule.
- No Read grant, accepting the send-back: every `toolkit` run would go back once, and `code-writer` would write without the reviewer files production gives it (row 118).
- Sentinel canaries in the account's `CLAUDE.md` to detect inheritance: each needs an edit to a stowed file every account loads, and reading one back is the model self-report M6(a) sets aside.
- A per-run gate on first-turn prompt size: the size moves with the brief and the agent body, and a campaign run has no paired run on the same attempt to compare with.
- A smoke-time calibration pair, two extra attempts on a synthetic fixture: it isolated `omitClaudeMd` and the exclude without a body estimate, but it needed its own agent, fixture builder, read sets, retry rules and outcomes. The block's own runs carry the same evidence except the body term (rows 136, 137, 138), and the pair stays the fallback when step 1b finds the body estimate off.
- Superseded by row 143: a required `smoke --rule-path`, the `control-unexercised` and `exclude-unexercised` outcomes, and a missing-reason field on spend-file lines with `summarize` counts. The engineer chose the lighter printed note instead. `run`'s instrument halt stops a campaign at the first `arm-leak` attempt (row 160).
- Matching each run's transcript paths against the account rules' `paths:` globs, to tell whether `bare` read a rule-matching file: it needs a frontmatter-list parser and a `**`-aware matcher whose semantics match Claude Code's, for a note the trigger path reuses for free when the payload carries one (row 146).

### Assumption ledger

**Root:** No measurement separates the comments and docstrings a dispatched coding agent writes under this toolkit's instructions from those it writes under none, on the same tasks. So nobody knows whether the toolkit's prose or the model's defaults drive the comment verbosity that review rounds keep flagging.

**Givens:**
- **G1.** Claude Code decides which instruction files, agents, hooks and settings reach a session or a subagent. The plan can only choose among the flags, frontmatter fields and settings keys it ships. Reason: the vendor owns loading. [verified: this session's fetch of code.claude.com sub-agents.md, cli-reference.md, headless.md, skills.md and memory.md, as relayed in the dispatch]
- **G2.** Which account owns a directory is set by direnv configuration outside this repository. Reason: another party owns that mapping, and this repository may depend on no other repository. [verified: repo `CLAUDE.md` "`claude-config` depends on no other repository"]
- **G3.** A model's output varies from run to run, so an arm difference is an estimate over repeated samples. Reason: the vendor's sampling. [verified: `evals/README.md:102-105`, the noise-floor note]
- **G4.** Every replay is a full `claude -p` session on the source repo's account, so the runner is local-only and never runs in CI. Reason: the vendor bills each session. [verified: `evals/README.md` "Why local only — never CI"]

**Rows:**
1. [engineer-verified: "Reusable evals/ runner (Recommended)"] The deliverable is a runner under `evals/`. Its arms, task sources and metrics are data or arguments.
2. [engineer-verified: "A and B now, C gated (Recommended)"] Arms A (`bare`) and B (`toolkit`) are built. Arm C is designed here and built later.
3. [engineer-verified: "Subagent for both arms (Recommended)"] Both arms dispatch a subagent: arm A as the fixture-local `bare-writer` with `omitClaudeMd: true`, and arm B as the production `code-writer`. "Why both arms dispatch a subagent" names the mechanism this replaced.
4. [engineer-verified: "Gitignored .local/ only (Recommended)"] Results go only under gitignored `.local/`. `summarize` prints to the terminal and writes no report.
5. [engineer-verified: "Each repo's own account (Recommended)"] Each replay runs under its source repo's account.
6. [engineer-verified: "Drop it for now (Recommended)"] A source repo that is not on this machine is not supported.
7. [engineer-verified: "Why not use replayed claude-config PRs and [other private repos' PRs] if you need? Actually I authorize you to look at every root on this machine."] Replayed pull requests are the task source, and the runner may read any local git root it is given. Row 46 holds why first-parent commits are pull requests here.
8. [engineer-verified: "we could probably design an experiment using an agent that has omitClaudeMd and no allowed skills and see how it does docstrings?"] The engineer's first message proposed an `omitClaudeMd` agent with no skills as the bare arm.
9. [verified: `docs/private-project-redaction.md` § "A wider corpus goes to the owner, never into a public artifact"] Nothing computed from a corpus wider than this repository on one account may appear in a public artifact. So no committed default, fixture or test names or mines another repository, the tests use synthetic repositories only, and the PR carries no figure.
10. [verified: `docs/private-project-redaction.md:136-137`] A subcommand with no scope refusal of its own is not a publication instrument. The runner refuses no wider corpus, so even an own-history figure from it is not publishable.
11. [verified: this session's probe, as relayed; repo `CLAUDE.md` "Secrets, tokens, credentials"] `CLAUDE_CONFIG_DIR=<empty dir>` printed "Not logged in", and copying credentials into a config dir is barred.
12. [verified: `claude/.claude/agents/code-writer.md:4`] `code-writer`'s tools are Read, Edit, Write, Bash, Grep and Glob, with no `Skill` tool, so `bare-writer` copies a list without it too.
13. [verified: `claude/.claude/settings.json`, its `SubagentStop` entry] The `SubagentStop` hook `send-back-skipped-self-review.sh` has the matcher `code-writer`.
14. [verified: sub-agents.md, as relayed: settings, managed settings and plugin hooks still apply inside subagents] Both arms' subagents run the account's hooks.
15. [verified: sub-agents.md, as relayed] `omitClaudeMd` is a subagent frontmatter field (v2.1.271 and later) that drops user, project and local `CLAUDE.md`. Managed policy still loads.
16. [verified: Verification step 1a's `bare` run, as relayed; the sub-agents page, as relayed: "For Explore, Plan, and subagents with `omitClaudeMd`, a subdirectory's `CLAUDE.md` and matching path-scoped rules still load when the subagent works with files there."] `omitClaudeMd` does not stop path-scoped rules: the `bare` subagent loaded a user-scope rule by `path_glob_match`. [verified: `claude/.claude/rules/shell-script-conventions.md:49-52`] A leaked rule is not neutral, because one user rule sets a comment-length convention. So every `bare` fixture carries `claudeMdExcludes` (row 115), and the instrument still checks every `bare` run.
17. [verified: `docs/case-studies/claude-md-glob-zero-segment.md`, its five `-p` trials and the clean trial's two logged lines] An `InstructionsLoaded` command hook fires under `claude -p` and logs `load_reason` and `file_path`.
18. [verified: Verification step 1a's `toolkit` run, as relayed] The hook logs a subagent's lazy loads, such as a `path_glob_match` rule load, but no line for the `CLAUDE.md` a subagent inherits. The `toolkit` subagent's reply quoted text found only in the account `CLAUDE.md`, and the log held no subagent `CLAUDE.md` line. So no per-run check can show a subagent's `CLAUDE.md`, and `smoke`'s block calibration checks inheritance instead.
19. [verified: Verification step 1a, as relayed] `--settings` adds the instrument hook to the account's hooks rather than replacing them: a `sessions/` file held the run's session ID, and the `toolkit` run's `SubagentStop` evaluator sent the writer back. Every run still checks it through a known account hook's side effect (row 54).
20. [verified: Verification step 1a, as relayed: `direnv exec <dir> pwd` run from `/tmp` printed `/tmp`] `direnv exec DIR CMD` runs CMD in the caller's working directory on this machine. The preflight probe still checks it for each repo and refuses otherwise, since another machine's direnv could differ.
21. [verified: `README.md` "Worktree enforcement", lines 341-347; `claude/.claude/hooks/require-worktree-for-file-writes.sh:7-8`] Machine-level worktree enforcement denies Edit and Write in a repo's main tree unless `.claude/worktree-optout` is present. [verified: `claude/.claude/hooks/require-worktree-for-file-writes.sh:6`] The opt-out loses only to a committed `.claude/worktree-required`. [verified: `claude/.claude/hooks/require-worktree-for-git-writes.sh:5-8`] The opt-out also exempts the fixture from the git-writes gate, which reads the same markers. By this plan's design, the prune removes a committed `.claude/worktree-required` with every `.claude` directory (`tasks.prune_instruction_files`).
22. [verified: `claude/.claude/hooks/require-plan-review.sh:13-15`] The plan-review gate passes a repo with no `.claude/plans/`, which the prune also removes.
23. [verified: `claude/.claude/hooks/tests/test_claude_md_excludes.py:1-21`] In a `claude-config` checkout, nested discovery loads `claude/.claude/CLAUDE.md` and the stow-source rules under `claude/.claude/rules/`. This repo suppresses them only through its root `.claude/settings.json`, which the prune removes, so the prune must remove the nested files itself.
24. [unverified] Nested `.claude/agents/` and `.claude/skills/` directories can be discovered and could shadow the account's agents. The prune removes every `.claude` directory, so the question never arises.
25. [verified: `CLAUDE.md` §Working Style "Dispatching cannot clear a denial your child inherits"] A subagent starts in its dispatcher's permission mode, so `--permission-mode acceptEdits` reaches both arms' subagents.
26. [verified: Verification step 1a, as relayed; the permission-modes page, as relayed] Under `-p`, `acceptEdits` approves Edit and Write inside the working directory. It also auto-approves `mkdir`, `touch`, `rm`, `rmdir`, `mv`, `cp` and `sed` there, plus the read-only command set. Other Bash needs an allow rule. Step 1a saw `env` denied, and `code-writer`'s Reads of reviewer files outside the fixture denied.
27. [verified: `evals/review_bench/fixture_repo.py:129-133`] Extracting with `git archive` into a fresh repository keeps every later commit unreachable from the fixture.
28. [verified: `evals/review_bench/fixture_repo.py`, `build_two_commit_repo`] `build_two_commit_repo` commits the head tree, so it cannot build a replay fixture.
29. [verified: `evals/review_bench/fixture_repo.py:8-9`; `evals/README.md` "Building a later arm"] The bench installs an arm's agent as a fixture-local `.claude/agents/` file.
30. [verified: `evals/review_bench/runner.py:852-991`] `evaluate_run_validity` checks any thin-dispatcher run, and its live-checkout check covers the changed paths passed to it. Its path checks see only Read, Grep and Glob calls. [verified: `evals/review_bench/runner.py:100`]
31. [unverified] `code-writer`'s `model: sonnet` resolves to `REVIEWER_MODEL_ID`, the `--model-id` default. If it does not, every run of either arm fails `model-mismatch` at `smoke`. Verification step 1b reads the model ID each subagent observed and stops when it differs.
32. [unverified] The full-config dispatcher relays the inner prompt verbatim often enough. `smoke` keeps no count of it.
33. [verified: `evals/README.md:353-355`] A prompt reaches `claude -p` as one argv element and should stay in the low single-digit KB.
34. [unverified] A commit message is a usable brief. The engineer reads the `tasks` output before any spend, and the brief is identical across arms.
35. [unverified] A classifier error that both arms share cancels out of the paired difference. One arm's style falling into a blind spot more often, such as trailing comments outside Python, does not cancel.
36. [verified: `evals/review_bench/mine_szz.py:112-141`] `_python_docstring_lines` returns the 1-based line numbers of module, class and function docstrings, leaving out a first or last line shared with code.
37. [verified: `evals/review_bench/runner.py:68-78`] The per-run budget cap and timeout were set for reviewer dispatches. [unverified] Whether a replay fits within them is unmeasured, so `smoke` prints each run's cost and wall clock.
38. [verified: `claude/.claude/agents/comment-discipline-reviewer.md:77-83`] That reviewer's Comment verbosity angle counts separate sentences that each carry a distinct fact as not verbosity. A word or line count therefore measures comment volume and shape, not verbosity as that lens judges it.
39. [verified: `claude/.claude/scripts/select-tests.py`, its review-bench rows; `claude/.claude/scripts/tests/test_select_tests.py`, its review-bench selection and import-closure tests] The review-bench rows' target tuples carry each sibling eval script's test, with `measure_explore_recall.py` as the precedent. The selection tests assert that set by equality, and the import-closure test's seed list needs each sibling eval script.
40. [verified: `claude/.claude/scripts/select-tests.py`, `GLOBAL_TRIGGER_PATHS`] A change to `select-tests.py` itself selects the full suite.
41. [verified: `.gitignore`] `evals/review_bench/.local/` has its own ignore entry, so `evals/replay_bench/.local/` needs one too (Phase 1).
42. [verified: `evals/run_review_bench.py:2284-2309,2327-2331`] The bench CLI sets an owner-only umask and restricts an existing `.local/` to 0700 with `_restrict_local_dir_to_owner`.
43. [verified: `evals/review_bench/runner.py:1150-1291`] `RunStore` supplies a flock, a write-ahead log, block completion and a validated sweep. The sweep deletes only `FIXTURE_DIR_PREFIX` directories directly under the system temp dir, and session stores directly under the projects root it is given.
44. [unverified] `/usr/bin/env -i` replaces itself with `claude` rather than forking it, so `msmr._run_claude_to_completion`'s group kill reaches the child. A Phase 4 test with a fake `claude` behind the prefix checks it.
45. [verified: `evals/README.md:1161-1162`] The bench runs only on repositories whose branches the engineer or someone the engineer trusts wrote. The same bar holds here, because each subagent holds Bash under the account's allowlist in a tree from the source repo. [verified: `evals/README.md:1146-1151`] Run validity fails a steered run only after its commands ran. So the residual risk is plain: a steered replay agent can read or write anything the account's permission rules allow. This plan accepts that risk under row 47.
46. [verified: `evals/review_bench/mine_szz.py:29`] This repository's `main` is squash-merged, so its first-parent commits are its merged pull requests.
47. [engineer-verified: "drop the ones under \"drop if you agree\". No worries about steered agents here."] Sandbox containment is not adopted, and steered agents are out of scope for this runner.
48. [verified: `docs/case-studies/claude-code-sandbox-write-boundary.md:284-292`] A `tool_result` carrying `tool_result_meta[].non_execution_kind` of `"permission-rule"` marks a call that never ran, whether a hook or the permission flow denied it.
49. [verified: `evals/review_bench/local_git.py:1-7,29-30`] `isolated_git_environment` leaves a repository's own `.git/config`, `.git/info/attributes` and tracked `.gitattributes` in effect.
50. [unverified] With `--git-dir` naming a runner-owned copy, a work-tree `.gitattributes` can name a filter or diff driver but cannot define one, so `add` and `diff` run no command. Phase 4's hostile-config test checks it.
51. [verified: `evals/measure_subagent_model_resolution.py:720-726`] `_run_claude_to_completion` passes the runner's environment minus git-local variables and `CLAUDECODE`, and takes no environment argument.
52. [verified: `docs/case-studies/claude-code-sandbox-write-boundary.md:230-236`] Even sandboxed Bash inherits the session's process environment by default, credentials included. So the subagent's Bash, which this plan does not sandbox, sees every variable the launch passes.
53. [verified: `claude/.claude/hooks/enforce-marker-script-shape.sh:83-87`, quoting the hooks reference's common input fields] A hook's `agent_id` field is present only when the hook fires inside a subagent call. [verified: Verification step 1a, as relayed] `InstructionsLoaded` lines carry `session_id` on every line and `agent_id` on subagent lines only.
54. [verified: `claude/.claude/hooks/capture-session-id.sh:26-27,96`; `claude/.claude/settings.json`, its `SessionStart` and `SubagentStart` entries for `capture-session-id.sh`] The account's `capture-session-id.sh` runs on SessionStart and SubagentStart. It writes `<config-dir>/sessions/<claude-pid>`, whose first line is the session ID.
55. [verified: `claude/.claude/agents/code-writer.md:109-110`] `code-writer` reads reviewer files from `~/.claude/agents/` with the Read tool.
56. [verified: `evals/README.md:1010-1017`] A killed runner's `claude -p` children can keep running. The bench's guidance is to wait at least the command's timeout before resuming.
57. [verified: `evals/review_bench/runner.py:1194-1203,1285`] `record_directory` with an empty session ID logs a directory before any launch, and the sweep then deletes that directory alone.
58. [verified: `evals/review_bench/runner.py:1048-1061`] `_read_environment_field`'s error text carries the command and the child's stderr.
59. [verified: `evals/review_bench/runner.py:1298`] `remove_tree_reporting_failure` calls `shutil.rmtree` with `onexc`. [unverified: Python docs not read this session] `onexc` first appears in Python 3.12, so an older interpreter raises at that delete. The replay runner never reaches it, because its import fails first (see Entry point).
60. [verified: `evals/review_bench/identifiers.py:8,17`] `validate_campaign_id`'s grammar admits no `/`, so it refuses a `../` name.
61. [unverified] The trusted-author filter narrows the task source and does not prove it trusted. A commit's author email is a fair proxy for row 45's trust bar within these limits:
    - an author email is a string the committer sets;
    - under a squash-merge host the author is the pull request's author, so the filter leaves out outside contributors;
    - under a rebase or merge-commit workflow, an outside branch can carry a trusted email;
    - a message can still carry third-party text, such as bot release notes.
62. [verified: `claude/.claude/agents/code-writer.md:169`; `claude/.claude/CLAUDE.md` § "Code Review"] The toolkit also shapes comments after the writer returns, through `/code-review` and `comment-discipline-reviewer`. No arm runs that loop.
63. [unverified] Words carry verbosity better than lines, and test files follow docstring conventions of their own. So the primary outcome counts words over non-test code lines.
64. [unverified] An agent tends to match the comment density of the files it edits, so the arm difference can vary with base density.
65. [verified: `evals/measure_subagent_model_resolution.py:90-91`; `evals/review_bench/runner.py:1739-1750`] The per-run cap derives from reviewer-dispatch figures, and `campaign_cost_ceiling` is a nominal product, not a proven bound. [unverified] An attempt that ends without a result event reports no cost, so the runner charges it at the cap.
66. [unverified] `git worktree remove` deletes ignored files, so a worktree's `.local/` goes with it.
67. [verified: `claude/.claude/scripts/tests/test_select_tests.py`, the `CONFIG_DIR_MODULE` test and `_EXACT_MATCH_LITERAL_PATH_CONSTANTS`] The `CONFIG_DIR_MODULE` test asserts its target set by equality, `REVIEW_BENCH_TEST_GLOB` included. `_EXACT_MATCH_LITERAL_PATH_CONSTANTS` lists the exact-match path constants, and the review bench's directory constants have an existence test.
68. [engineer-verified: "Proceed anyway"] Superseded by row 84. The engineer chose to keep an earlier revision of this plan as one PR at its `Projected diff:` of about 3,800 changed lines, over the 400-line threshold.
69. [verified: `evals/measure_subagent_model_resolution.py:703-726,763`] `_run_claude_to_completion` sends the child's stderr to the null device and returns only its stdout lines and a timed-out flag, with no exit status.
70. [unverified] The allowlist holds every variable a launch needs. An account whose `.envrc` sets a provider-selecting variable besides `CLAUDE_CONFIG_DIR` loses it at launch. That launch fails when the config dir holds no login, and otherwise authenticates through the config dir's own login instead of the `.envrc`'s route, which `smoke` cannot tell apart. `preflight` therefore names every variable an `.envrc` set. `claude --version` shows only that the CLI resolves and starts, and whether the installed CLI has a login-status command is unchecked. So `smoke` is the first check that authenticates each account.
71. [verified: `evals/README.md`, its git 2.32 requirement; `evals/review_bench/local_git.py:23-24`] `isolated_git_environment` turns off the global git config through `GIT_CONFIG_GLOBAL`, which git older than 2.32 ignores.
72. [verified: `evals/review_bench/runner.py:1041-1045`] `_ENVIRONMENT_READ_TIMEOUT_S` is 10 seconds, set for local process and git calls with no network I/O, a hung `claude --version` among them.
73. [verified: `evals/review_bench/runner.py:1090-1106`] An `EnvironmentReference` built from a frozen mapping of `cli_version` and `ambient_config_commit` checks every reading against it. Built without one, it adopts its first reading.
74. [verified: `evals/review_bench/runner.py:1592-1594`] `all_runs_missing` is true only when none of the records it is given is ok.
75. [verified: `evals/review_bench/analysis.py:347-398`, `bootstrap_interval_with_drops` and `bootstrap_interval`] `bootstrap_interval` returns no interval for fewer than two clusters.
76. [unverified] One `printf '%s\n'` of the captured payload reaches the log in one `write` call, which Phase 4's parallel test checks. [verified: Verification step 1a, as relayed] The session-start burst arrived one payload per line. Whether the harness ever runs two `InstructionsLoaded` hooks at once stays unchecked.
77. [unverified] `git add -A` stores a symlink as its link text and skips a FIFO without opening it. Phase 4's hostile-tree test checks both.
78. [unverified] `git add -A` leaves out an untracked path that the work tree's ignore rules match, and fails on a nested repository that has no commit. Phase 4's tests pin both outcomes.
79. [verified: `claude/.claude/hooks/send-back-skipped-self-review.sh:40-41`; `docs/session-config-keys-log.md:3`] A replay session adds to account files that already exist. `toolkit`'s `SubagentStop` hook writes `.code-writer-self-review.log` and `.code-writer-self-review.d` in the config dir. Every session start adds a row to `.session-config-keys-log.jsonl`.
80. Superseded by row 158. [verified: `claude/.claude/scripts/select-tests.py:812-832,876-879`; `claude/.claude/scripts/tests/test_select_tests.py:425-431,1092-1105,2138-2149`] A test that reads the real agents directory through a production function is a hand-audited `AGENTS_DIR` reader. `MEASURE_SUBAGENT_MODEL_RESOLUTION_TEST` carries the wiring one needs:
    - a target in the `AGENTS_DIR` row;
    - an entry in `_HAND_AUDITED_AGENTS_DIR_READERS`;
    - a `_FILE_TARGETS` entry;
    - a standalone row that selects `test_select_tests.py` when the reader changes.
81. [verified: `claude/.claude/scripts/tests/test_select_tests.py`, the import-closure test] The import-closure test maps every relative `ImportFrom` to a `review_bench.` module, so a relative import in the replay package would resolve to the wrong module with no failure.
82. [verified: `evals/run_skill_evals.py:64`] `DEFAULT_WORKERS` is 4.
83. [unverified: Python docs not read this session] With `strict` false, `os.path.realpath` resolves a symlink loop as far as it can and does not raise. The prune's realpath check meets loops, and Phase 2's loop case checks it.
84. [engineer-verified: "Proceed anyway"] Superseded by row 99. The engineer chose to keep an earlier revision of this plan as one PR at its `Projected diff:` of about 5,100 changed lines, over the 400-line threshold.
85. [verified: `evals/review_bench/runner.py:1141,1214-1230,1264-1291`] `sweep_abandoned` returns the whole store's pending entries, whichever projects root it is given. An entry with an empty session ID is a directory logged before any run launched against it.
86. [verified: `evals/review_bench/runner.py:280-313`] `_repair_torn_tail` truncates a JSONL file's unparseable last line back to the last newline, and adds the missing newline to a last line that parses. The bench calls it before a log's next read or append.
87. [verified: `evals/review_bench/fixture_repo.py`, `_LOCAL_GIT_TIMEOUT_S`, `_run_git`, `_extract_commit_tree` and `_commit_snapshot`] `_run_git`, `_extract_commit_tree` and `_commit_snapshot` bound every git call by the module's 10 s `_LOCAL_GIT_TIMEOUT_S`, which its comment calls an empirical guess. `_extract_commit_tree` holds the whole archive in memory, and `_commit_snapshot` runs `git add -A --force` over the whole tree.
88. [verified: Verification step 1a's timing, as relayed] The Fixture rule applied to the measured builds gives the defaults Fixture lists. [unverified] Three times the slowest build bounds a hung git without failing a slow but healthy build. The multiplier and the 60 s ceiling are judgments.
89. [verified: `pyproject.toml:31`; `.github/workflows/tests.yml:26-34,179,186`; `evals/test_review_bench_runner.py:793,810`] CI runs the tests not marked `timing` under `-n auto` and the `timing` tests serially, inside the job's `timeout-minutes: 30` cap, whose comment says to re-derive it whenever either pass grows. The marker is for a test that asserts a wall-clock bound or needs serial isolation.
90. [verified: `evals/conftest.py:30-47`] The shared autouse fixture stubs `review_bench.runner.default_live_checkout_roots` by module attribute, and only once some test has imported `run_review_bench`. A `from` import binds before the stub, and a process that never imported `run_review_bench` gets no stub.
91. [verified: `evals/test_review_bench_cli.py:4257,4280-4302`] The bench tests its CLI's owner-only umask and its tightening of a pre-existing local dir to 0700.
92. [unverified: the git-log documentation, not read this session] With `--no-use-mailmap`, the `%ae` and `%ce` placeholders print the commit's raw identities, whatever the repo's `.mailmap` or `mailmap.file` says. Phase 2's mailmap test checks it.
93. [unverified: POSIX not read this session] An empty `PATH` entry names the current directory, and a relative entry resolves against it. The launch's current directory is the fixture.
94. [unverified] The subagent's Bash sees only the launch's allowlisted variables plus those the shell and Claude Code set, with no login profile re-exporting another account's variable. Step 1a's `env` listing was denied (row 26), so Verification step 1b runs the names-only listing from a scratch script under one exact Bash allow entry.
95. [verified: `evals/review_bench/runner.py:1600-1608`] `systemic_failure_message` tells the engineer that resuming under the same campaign ID reruns the block.
96. [verified: `evals/review_bench/fixture_repo.py:134-137`] `_extract_commit_tree` runs `git archive <commit>` with the source repo as its working directory. [unverified: the git-archive documentation, not read this session] Run from a subdirectory, it archives only that subdirectory, while the task's changed paths are relative to the repo root.
97. [verified: `evals/run_review_bench.py:880,2339-2342`] The bench CLI returns 1 when a run stops short, such as on a held store lock, and 2 for a refusal.
98. [verified: `evals/measure_subagent_model_resolution.py:682-683,719,741`] `LaunchAbortedError` is raised only with fixed text.
99. [engineer-verified: "Proceed anyway"] Superseded by row 110 for the current figure. The engineer chose to keep an earlier revision of this plan as one PR at its `Projected diff:` of about 6,200 changed lines, over the 400-line threshold.
100. [engineer-verified: "Skip up front in `tasks` (Recommended)"] For a base tree holding a link the fixture's extraction refuses, the engineer chose to skip the commit up front in `tasks`.
101. [unverified] The session's proposal, not the engineer's words: `tasks` counts each refused shape under its own skip reason, so its printout shows what the skip drops.
102. [unverified] The session's proposal, not the engineer's words: `fixture_repo.py` gets no change for this skip. `tasks` reuses `fixture_repo._has_git_path_component`, `_first_path_component_casefolded`, `_BENCH_DIR_NAME` and `_PROJECT_CONFIG_DIR_NAME`, so its predicates match extraction's own.
103. [verified: `evals/review_bench/fixture_repo.py:152-200`] `_extract_archive` raises before any write on a member with a `.git` path component, a link whose target has one, a member under `.bench`, and a link whose own path starts with `.claude`. It extracts through tarfile's `data` filter, which its docstring says refuses a member that resolves outside the destination and a link with an absolute target. [unverified: the Python tarfile documentation, not read this session] The filter resolves a relative link's target with `os.path.realpath`, so it could refuse a link that `tasks`' lexical check passes. Phase 2's parity table pins where the two agree on the running interpreter. A commit they disagree on fails as `fixture-error`, a task-specific failure that costs its own block and halts the campaign only after a second such block in a row (row 153).
104. [unverified] The session's extension, not the engineer's choice: the skip also covers the non-link entries extraction refuses, an entry with a `.git` path component and one under a root `.bench`, because each fails every attempt as `fixture-error` the same way a refused link does. It also skips, under `claude-dir-file`, a base tree holding a regular file named `.claude` at the root, since the fixture build writes `.claude/agents` beneath that name (`evals/README.md` § "Tasks").
105. [unverified: the `git-ls-tree` and `git-cat-file` documentation, which two reviewers cite and this session did not read] With `-l`, `git ls-tree` prints `-` as the size of a non-blob entry such as a gitlink. Without `-z` it quotes a path that holds an unusual character, and with `-z` it prints the path verbatim. `git cat-file --batch` answers `<oid> missing` for an object the repository does not hold. Phase 2's gitlink, non-ASCII and `missing` cases check all three. The `missing` case covers an object absent for a reason other than a partial clone (Tasks, row 130).
106. [verified: Verification step 1a's timing, as relayed; step 1c's `timing.py` output, which the engineer ran and pasted this session] A fixture build's time grows with the base tree's entry count, and nothing measured explains the jump between 12,500 and 25,000 entries. The slowest of four concurrent builds took the times below. The halving trees in the first list have no byte total.
    - 21.9 s at 10,000 files, and 23.3 s on a rerun;
    - 29.5 s at 12,500;
    - 148.6 s at 25,000;
    - 179.7 s at 50,000;
    - 211.1 s at 100,000.

    Step 1c, which the engineer ran on this branch with `timing.py`, measured two synthetic single-commit text repos on their own, with the same four concurrent builds:
    - 12,500 files, 100,000,000 bytes of blobs: 33.5 s;
    - 17,500 files, 99,400,000 bytes of blobs: 42.3 s.

    The corner's 33.5 s supersedes the 29.5 s figure in the timeout rule, which had set `FIXTURE_GIT_TIMEOUT_S` at 90 s.
107. [verified: `evals/review_bench/runner.py:388-389,407-424`] `wait_for_session_flush` reads `SESSION_FLUSH_TIMEOUT_S` and `SESSION_FLUSH_POLL_INTERVAL_S` as module globals at call time. It sleeps one poll interval before it can return, and polls to the timeout when the transcript or the sidecar is missing.
108. [unverified: CPython 3.14.10 `tarfile.py:879-893`, as one reviewer read it; not read this session] The `data` filter rewrites a link's target with `os.path.normpath` before it resolves it with `os.path.realpath`. Whether every Python 3.12 patch release does the same is unconfirmed. Without that rewrite, a chain such as `a -> d/e/f/..` with `d/e/f -> ../..` passes both the filter and `tasks`' lexical check yet resolves outside the fixture, so the prune's own realpath check fails it.
109. [verified: `evals/test_review_bench_fixtures.py:941-1000,1089`] The bench's fixture tests build synthetic archives with `_tar_with_link` and its sibling helpers. They pin `.benchmark/`, `docs/.bench/` and `.claude-plugin/` as names extraction allows.
110. [engineer-verified: "Proceed anyway"] Superseded by row 124. The engineer chose to keep this plan as one PR at its revised `Projected diff:` of about 6,880 changed lines, over the 400-line threshold. This supersedes row 99.
111. [engineer-verified: "I say option 1. what does the architect think?"] For the instrument revision that Verification step 1a forced, the engineer picked the session's option 1 and asked for the architect's view.
112. [unverified] The session's wording, not the engineer's: option 1 narrows the hook check to file and rule loads, sets `claudeMdExcludes` to drop user rules, and settles `CLAUDE.md` inheritance with probes.
113. [engineer-verified: "can you fetch or have a subagent fetch the items that the architect needs under the things that the architect did not verify in case that changes the plan? And then let's go with your recommendations."] The engineer adopted the session's relay of the architect consult as the direction, once the consult's unverified items were fetched.
114. [unverified] The session's relay, not the engineer's words. The recommendations were:
    - `claudeMdExcludes` in `bare` fixtures only;
    - exact per-file Read allow entries, the same in both arms, so `code-writer` can read its reviewer files;
    - a smoke-time calibration pair in place of sentinel canaries.

    The engineer specified no mechanism, so every mechanism that carries them out is this plan's design.
115. [verified: the memory page, as relayed: "You can configure `claudeMdExcludes` at any settings layer: user, project, local, or managed policy."; it matches "against absolute file paths using glob syntax"; "A pattern that matches either path excludes the file. Before v2.1.239, only a pattern that matched the link target excluded the file."] The fixture's project settings file is a documented layer for the key. `omitClaudeMd` needs v2.1.271 (row 15), and preflight refuses an older CLI. Past v2.1.239 the link-path entry alone should match, so the realpath entry is a second match. [unverified] Verification step 1b settles four questions the page leaves open, and `smoke`'s `bare` run re-checks the first three on every account whenever it reads a rule-matching file (row 145). Whether the key reaches:
    - user-scope `rules/` files;
    - rules loaded lazily by path;
    - a subagent's loads;
    - a never-trusted folder's project settings file under `-p`, which step 1b tests apart from the other three through a probe-only exclude of a fixture `CLAUDE.md`.
116. [verified: the headless and permissions pages, as relayed] A `-p` session in a never-trusted folder does not use the project settings' `permissions.allow` or `additionalDirectories`, and does use its hooks and `env`. So the Read allow entries go on the command line.
117. [verified: the permissions page, as relayed: "Allow rules: apply only when both the requested path and the file it resolves to match."; `//path` is an absolute path; `--allowedTools` "uses the same rule syntax"] A stowed file needs one entry for its link path and one for its target. [unverified] A single-file `Read(//<path>)` rule passed through `--allowedTools`, each entry its own argv element after the flag, grants that one Read. The page shows no such entry verbatim. Verification step 1b and `smoke`'s `read-grant-denied` condition settle it, and `read-grant-unexercised` keeps a run with no such Read from passing it vacuously.
118. [verified: `claude/.claude/hooks/evaluate-code-writer-self-review.py:8-13,74-88`; Verification step 1a, as relayed] The `SubagentStop` evaluator requires a Read of each reviewer its checks name, and matches a Read by its path suffix under `agents/` or `skills/`. It counted step 1a's denied Reads as failed, so with no allow entry every `toolkit` run is sent back once. [verified: `claude/.claude/agents/code-writer.md:47-48,110-111`] `code-writer.md` names its reviewer and skill files under `~/.claude/agents/` and `~/.claude/skills/<name>/SKILL.md`.
119. [unverified] Superseded by row 138. It held the margin judgment for the smoke-time calibration pair, which row 136 removed.
120. [unverified] The subagent transcript's first assistant entry carries `message.usage` with `input_tokens`, `cache_creation_input_tokens` and `cache_read_input_tokens`, and their sum is the subagent's first-turn prompt size. Verification step 1b reads it, and `smoke` fails `arm-config-missing` when either arm's run has a null size.
121. [verified: the hooks page, as relayed: `InstructionsLoaded` "runs asynchronously for observability purposes", and cannot block] A log line can land after the session's own files stop growing. [unverified] A hook already running when `claude -p` exits still finishes its write. A load fires on a tool call, and a tool result always goes back to the model before a session ends, so a lost line would need a hook slower than one model turn. The runner reads the log only once its size holds across two polls. No probe tests the premise (rows 134, 135).
122. [verified: the permission-modes page, as relayed; Verification step 1a, as relayed] Both arms meet the same permission rules, so a Bash command outside `acceptEdits`' set and the account's allow rules, such as a test runner, is denied in either arm. How often each arm meets a denial can differ, and the permission-denial count records it. The README states the caveat.
123. [unverified] Superseded by row 139. It held the premise that a rule matches a file of the calibration pair's synthetic fixture, which row 136 removed.
124. [engineer-verified: "I accept 2 to 4, and I accept your recommendation on splitting."] The engineer accepted the session's items 2, 3 and 4, and the session's recommendation to split the work into two PRs.
125. [unverified] The session's numbering and descriptions, not the engineer's words:
    - item 1 was the projected-diff size, about 7,680 lines, as one PR or as a split at Phase 2 to Phase 3;
    - item 2 was the Read grant over every `agents/*.md` and `skills/<name>/SKILL.md` under the config dir and `~/.claude`, by link path and realpath, with no wildcards, in both arms;
    - item 3 was Verification step 1b, three paid runs before Phase 3;
    - item 4 was the stricter `bare` check, which fails on any dispatcher load other than the account's `CLAUDE.md`.
126. [unverified] The session's recommendation, not the engineer's words: split at the Phase 2 to Phase 3 boundary, with about 1,830 lines in the first PR. That figure was this plan's estimate. [verified: line counts of Phase 2's output before the first PR's last dispatch, taken by an earlier dispatch of this plan's revision] Phases 1 and 2 hold 2,592 lines in their five new files, so the first PR's `Projected diff:` is larger than the figure the recommendation carried.
127. [verified: repo `CLAUDE.md` "a plan committed under `.claude/plans/` ships in the same PR as the implementation"] A plan ships with the code it governs.
128. [verified: `claude/.claude/hooks/require-plan-review.sh:4-16`] The plan-review gate blocks Write and Edit while a plan file under `.claude/plans/` is uncommitted or modified and no review marker covers its exact state. A committed, unmodified plan passes. So each edit to this file, on either branch, needs a fresh `/plan-review` before the next code edit.
129. [engineer-verified: "Accept the new sizes (Recommended)"] Superseded by row 141 for its first, second and fourth items, the sizes and the refactor's move. Its third item stands. The engineer selected this label when asked whether to accept four items. The label covers acceptance of those sizes and that scope shift, nothing more. The option's description was the session's proposal. The items:
    - the first PR's `Projected diff:` of about 2,750 lines, where row 126 had carried about 1,830;
    - the two PRs' combined about 8,650 lines;
    - the `--max-base-entries` default change from 100,000 to 12,500;
    - the `write_fixture_extras` refactor's move to the second PR.
130. [verified: the code-writer's run of Phase 2's test on Apple Git 2.50.1, as it reported] On Apple Git 2.50.1 a real `--filter=blob:none` clone sets `remote.<name>.promisor=true` and no `extensions.partialclone`. The refusal therefore reads `git config --get-regexp '^(extensions\.partialclone|remote\..*\.promisor)$'` and refuses on any output. [unverified: the git partial-clone documentation, which a reviewer cites and this session did not read] A partial clone records `extensions.partialClone` in its config. Git fetches a missing object on demand from `cat-file`, `diff` and `archive`. Phase 2's test (`evals/test_replay_bench_tasks.py`) holds the clone recipe.
131. [verified: `evals/review_bench/local_git.py:4-5,29-30`] `isolated_git_environment` leaves a repository's own `.git/info/attributes` and tracked `.gitattributes` in effect. [unverified: the gitattributes and git-archive documentation, which a reviewer cites and this session did not read] `git archive` drops an `export-ignore` path and rewrites an `export-subst` file's bytes. Phase 2's live-git case archives a tree whose `.gitattributes` says `tests export-ignore` and checks that `tests/` is absent.
132. [unverified] `claude --version` prints a dotted `<major>.<minor>.<patch>` version first, which the readiness check parses for its floor. Verification step 1b records the printed line, and a version the check cannot parse refuses the account.
133. [verified: `evals/replay_bench/tasks.py`; `evals/review_bench/runner.py:41-42,60`; `claude/.claude/scripts/select-tests.py`, `EVALS_CONFTEST` and `_is_review_bench_change`] The first PR's `tasks.py` imports `review_bench.runner`, which imports `measure_subagent_model_resolution`, `run_skill_evals` and `_config_dir`, so the first PR's closure already reaches every row Phase 5a names. `_is_review_bench_change`'s predicate covers `EVALS_CONFTEST`, whose comment lists the tests it applies to as an exact set.
134. [engineer-verified: "Apply all six cuts (Recommended)"] The engineer accepted the adversarial architect consult's six-item cut list. The label covers acceptance of that list, nothing more.
135. [unverified] The consult's cut list, as the session relayed it, not the engineer's words:
    - delete the 30 s delayed second hook and its step 1b stop condition, and state row 121's residual instead;
    - drop `--smoke-stop-usd` and `smoke`'s `--k`, fixing `smoke` at one run per arm and keeping the printed ceiling;
    - fold `unsafe-config-path` into `fixture-error`, with the path in `missing_detail`, keeping the refusal inside the generators;
    - cut the character test matrix to one row per class against the shared check, plus one wiring case per generator;
    - delete the `.envrc` marking and the per-value hashes, keeping the full list of dropped variable names;
    - delete the realpath containment check on the Read grant and its test.
136. [engineer-verified: "Replace the pair (Recommended)"] The engineer accepted replacing the smoke-time calibration pair with the consult's proposal. The label covers acceptance of that proposal, nothing more.
137. [unverified] The consult's proposal, as the session relayed it, not the engineer's words: `smoke`'s own block calibrates, with `toolkit` and `bare` on one task. A `toolkit` subagent rule load is the positive control, and `toolkit`'s first-turn size minus `bare`'s, minus an estimate of `code-writer`'s body, must reach the margin. The detailed mechanism under Campaign is this plan's design.
138. [unverified] The block calibration's arithmetic is a judgment:
    - A character count over 4 is a rough token estimate, and halving the `CLAUDE.md` estimate leaves room for tokenizer variance. The body term is the weak point, because its error adds straight into the gap that the margin judges. Verification step 1b measures the body's real share, and when the estimate misses by more than half the margin, the plan falls back to the calibration pair.
    - Within that bound, and with the `CLAUDE.md` estimate right, a missing `CLAUDE.md` cannot pass the check. In the same conditions a present one cannot fail it.
    - The margin's rule term covers the unscoped rules `bare`'s exclude drops. [verified: `claude/.claude/rules/*.md` frontmatter] All nine user rules in this repository are path-scoped today, so the term is zero for an account whose only rules are these.
    - [verified: a reviewer's character counts of this repository's files, relayed by the dispatch] Here the account `CLAUDE.md` is about 2.6 times the `code-writer` body, so the margin is about 3,812 tokens by the estimate and the body term about 2,965.
    - The numbers age two ways: the headroom shrinks if `CLAUDE.md` shrinks toward the body's size, and step 1b's one measurement ages with edits to either file.

    The other first-turn inputs match across the two subagents:
    - [verified: `claude/.claude/settings.json`, its `SubagentStart` entries] both get the same `SubagentStart` hooks, since the one with a matcher names `plan-architect`;
    - [verified: `claude/.claude/agents/code-writer.md:1-7`] `code-writer`'s frontmatter preloads no skill, and `bare-writer` copies its tools, model and effort (Arms);
    - both carry the same brief and inner prompt.
139. [verified: `claude/.claude/rules/*.md` frontmatter] Superseded by row 145, under row 143's choice. This repository's rules match `**/*.py`, `**/*.sh`, `**/*.sql`, `**/*.tf` and `**/Dockerfile`, among other globs. [unverified] A subagent's Read of such a file loads the rule. `smoke`'s `--rule-path` names one such changed path present in the base tree.
    - A run that never reads it fails `control-unexercised` or `exclude-unexercised`, not `instrument-missing`, so a bad pick is told apart from a silent hook.
    - A `toolkit` run that read it and logged no rule load fails `instrument-missing`.
    - An account whose history keeps no task with a rule-matching changed path needs a larger `--max-count`, or a task set from another source repo on the same account.
    - A residual: the engineer's pick could match no rule while the `toolkit` subagent loads a rule from another file, and then `bare`'s clean read of the pick shows nothing.
140. [unverified] Superseded by row 145, under row 143's choice. With the rule-path read required of both runs, the `bare` smoke run shows the exclude holding for that path. In a campaign, a broken exclude voids only the `bare` runs that touch a rule-matching file, so the leak stop halts the campaign at the first `arm-leak` attempt, a retried one included, rather than letting the per-arm stop wait for a block with no valid `bare` run.
141. [engineer-verified: "Accept the trimmed sizes (Recommended)"] The engineer selected this label when asked whether to accept the trimmed plan's sizes, about 2,890 lines for the first PR, about 6,300 for the second and about 9,190 together, and the dropped `write_fixture_extras` refactor. The label covers acceptance of those figures and that drop, nothing more. The option's description was the session's proposal.
142. [verified: a reviewer's read-only check of this machine, relayed by the dispatch] Here `~/.claude/agents` and `~/.claude/skills`, and each other account's, are directory links into the one config checkout, so every granted realpath is an in-tree file. [unverified] That holds only on a folded stow install. A consumer whose `agents/` or `skills/` already existed as a real directory gets per-file links beside their own files, which the grant then covers too.
143. [engineer-verified: "Take the lighter fix (Recommended)"] Asked whether to keep the round-2 fix for the vacuous `bare` smoke check or take a lighter one, the engineer chose the lighter fix. The label covers that choice, nothing more.
144. [unverified] Superseded by row 160 for the campaign leak stop at the first `arm-leak`, which `run`'s instrument halt provides. The session's proposal, not the engineer's words: keep only a printed note at smoke time when the `bare` run read no rule-matching file, state the residual in the ledger and the README, and rely on the per-run `arm-leak` check on later `bare` runs. The round-2 fix it replaced was a required `smoke --rule-path`, the `control-unexercised` and `exclude-unexercised` outcomes, a campaign leak stop at the first `arm-leak` and a missing-reason field on spend-file lines. The note's detection is this plan's design (`checks.exclude_note_lines`).
145. [verified: `claude/.claude/rules/*.md` frontmatter] This repository's rules match `**/*.py`, `**/*.sh`, `**/*.sql`, `**/*.tf` and `**/Dockerfile`, among other globs. [unverified] A subagent's Read of such a file loads the rule. The residuals the lighter fix accepts:
    - a `smoke` task whose `toolkit` run reads no rule-matching file fails `instrument-missing`, the reason a silent hook also gives, and the engineer tells them apart from the printed loads and the `toolkit` transcript, then picks another task;
    - an account whose history keeps no task with a rule-matching changed path needs a larger `--max-count`, or a task set from another source repo on the same account;
    - a `bare` smoke run that read no rule-matching file passes without testing the exclude, and only the printed note shows it;
    - in a campaign, a broken exclude fails `arm-leak` only on the `bare` runs that read a rule-matching file. The first such attempt halts `run` at its block's end (row 160), so a broken exclude goes unseen only while no `bare` run reads a rule-matching file.
146. [unverified] A `path_glob_match` payload names the file that triggered the load. The relayed hooks-page excerpt names no such field. Verification step 1b records whether the probe's lines carry one and under what key. Without one, `smoke` prints only the residual sentence.
147. [engineer-verified: "Add file to Critical files"] Asked whether to keep the code-review round 5 move of the shared-default timeout case and the `build_single_commit_repo` cases into `evals/test_review_bench_fixtures.py`, which Critical files had not listed, the engineer selected this label. Critical files now lists the file.
148. [engineer-verified: "Fix docs and log pin (Recommended)"] Asked how to treat the code-review round 9 findings, one of which needs `evals/review_bench/local_git.py` (not in Critical files), the engineer selected this label. Critical files now lists the file for the one docstring sentence only. [unverified] The option description was the session's proposal: it also covered the comment fixes and `--no-show-signature` on the two `git log` calls.
149. [engineer-verified: "Keep it (Recommended)"] Asked whether the second PR's `tasks` CLI should exit 2, naming the flag, for zero, negative or inverted selection limits, which the Ask does not call for, the engineer selected this label. Row 150 records where the guard lives and which values it refuses.
    - [unverified] The option description was the session's proposal: a guard at the one place a person types the limits. Row 150's placement replaces it.
150. [engineer-verified: "In SelectionLimits (Recommended)"] Asked where the guard lives, the engineer selected this label. The guard and what it refuses:
    - `SelectionLimits.__post_init__` raises `TaskSourceError`, which `_run_main` already maps to exit 2.
    - [engineer-verified: "Reject: all integer limits at least 1 (Recommended)"] Asked whether `--min-code-lines 0` is valid, the engineer selected this label. Every integer limit is at least 1, and `min_code_lines` may not exceed `max_code_lines`, with equal valid.
    - [verified: a reviewer's run of `git rev-list --count -n -1 HEAD` in this repository, relayed by the dispatch, printed the whole history, 1,058 commits] A negative `max_count` removes the 500-commit bound.
    - [unverified] Both option descriptions were the session's proposals.
151. [engineer-verified: "Guard shrink + group A (Recommended)"] Asked which cuts to the second PR to apply, the engineer selected this label and also "Item 7: simpler halting (Recommended)", "Items 9 and 10 (Recommended)" and "Item 8: no cap charges for swept attempts". The labels cover those selections, nothing more. [unverified] The item numbers and option descriptions were the architect consult's and the session's proposals, not the engineer's words:
    - the guard shrink: rows 149 and 150's `__post_init__`, one parametrized refusal case, one boundary pair, one CLI case, and the limit flags passed by field name (row 152);
    - group A: `summarize` cut to the primary outcome, its bootstrap, one docstring outcome and diagnostics (row 157); each attempt's `sessions/` file left in place (row 154); the campaign's `attempts/` deleted whole on resume (row 156); fewer block-calibration arithmetic cases, with row 138's heuristic unchanged; and the trigger-path code built only when step 1b finds a trigger field (row 146);
    - item 7: the per-arm stop halting on two blocks in a row, with no recorded halts or `--skip-task` (row 153);
    - item 8: no resume cap charge for a swept attempt that has no spend line (row 155);
    - item 9: a manual listing of the account config dir in Verification step 6, in place of `smoke`'s own;
    - item 10: the arms test reading a `tmp_path` agent file, not this checkout's (row 158).
152. [unverified] The session's design, not the engineer's words: `tasks`' flags for the nine `SelectionLimits` fields, `--rev` included, default to `None`, and `cmd_tasks` passes only the flags given to `SelectionLimits`, by field name. `SelectionLimits` then holds the only defaults, and no flag can reach another field. The CLI case that gives all nine flags distinct values pins the wiring.
153. [unverified] The session's design under row 159, not the engineer's words:
    - A task-specific failed block, one where an arm has no valid run and no attempt in the block ended under an instrument reason, is marked complete and never rerun. Its task leaves the pairs, and `summarize` counts it.
    - Two such blocks in a row halt `run` with both complete. The count lives in memory for one invocation and starts at 0 on every start and resume, so a resume goes on past the pair, and those two tasks are lost from the sample. A one-block transient failure also loses its task for good.
    - A persistent failure meets the pre-block spend stops and the two-in-a-row halt, so it costs at most one block beyond the first.
    - Two residuals stay, which the README and Out of scope also name:
      - [unverified: how `claude -p` behaves at a usage limit] a usage-limit, auth or overload event that fails every run of two blocks marks two tasks that no agent really tried complete, and drops both from the sample;
      - failures on alternating tasks never make two in a row, so only the spend stops bound them: `--cumulative-stop-usd`, and `--account-stop-usd` when it is set below it.
    - An instrument failure halts as row 160 states.
154. [verified: `claude/.claude/hooks/capture-session-id.sh:96`; `claude/.claude/hooks/_lib.sh:3695-3706`; a search of `claude/.claude/` outside its tests, which found no code that deletes a `sessions/` entry] `capture-session-id.sh` rewrites `<config-dir>/sessions/<claude-pid>` at each session start. `_lib_registered_live_sid` reads an entry as absent when its recorded start time does not match the live process. Nothing deletes an interactive session's or a review-bench launch's entry. So each replay attempt leaves one file there too, as accepted residue beside row 79's files.
    - [verified: `claude/.claude/scripts/post-crash-sessions.py:18,127,158,161`] `post-crash-sessions.py` reads these files as its source D, and its crash-evidence window is 14,400 seconds.
    - [unverified: a reviewer's read of `post-crash-sessions.py:1347-1393`] A leftover replay file, whose PID is dead and whose session store the cleanup deleted, lists there as `unknown` while its mtime is inside that window. It lists as `confirmed-clean-exit` instead if the SessionEnd hook fires under `claude -p`, which is unverified.
    - So for four hours after a campaign, the engineer may see replay attempts listed in that report, as accepted residue.
    - [unverified] No other reader under `claude-skills/` or a script outside `claude/.claude/` minds a stale entry, since the search did not cover them.
155. [unverified] The plan's design, not a measurement:
    - `_run_main` routes SIGTERM, and SIGHUP unless it is ignored (`nohup`), into `KeyboardInterrupt`, and an aborted launch is charged at the cap (`attempt._charge`, row 65). A Phase 4 case checks that an interrupted in-flight attempt writes its charge.
    - A hard kill, such as SIGKILL, an out-of-memory kill or a power loss, can end a launched attempt with no spend line. So can a failed append, which halts `run` before its block is marked complete. Either way the attempt's entry stays pending, and the next resume counts it among the uncharged attempts it prints.
    - After each hard kill, up to `run_skill_evals.DEFAULT_WORKERS` (4, row 82) launched attempts, one per worker thread, may go uncharged. The undercount adds up over kills, each adding at most `DEFAULT_WORKERS` times the per-run cap, a nominal figure (row 65).
    - The README states this as accepted residue, beside row 79's files.
156. [verified: `evals/review_bench/runner.py:1128,1168-1177,1636-1638`] The store's lock is `lock.pid`, taken with a non-blocking exclusive `flock`, and the review bench takes it before its sweep. The replay resume takes it before its sweep and its `attempts/` delete (`campaign.py`), so no attempt of the campaign runs then. [verified: `evals/review_bench/runner.py:1294-1300`] `remove_tree_reporting_failure` runs `shutil.rmtree` with an `onexc` handler and prints each failure, so the resume checks for a symlinked or an absent `attempts/` itself before it calls the helper.
157. [unverified] The plan's design: each run's record keeps its metrics, first-turn prompt size and diff, each task keeps its base density, and each spend line keeps its cap-charge flag. So a later change can compute any figure `summarize` leaves out, Out of scope's list included, with no rerun.
158. [unverified] The session's design, not the engineer's words: `evals/test_replay_bench_arms.py` builds `bare-writer.md` from a `tmp_path` `code-writer.md`, so it reads no real agents directory and needs none of row 80's wiring. That file uses distinctive non-default `tools`, `model` and `effort` values and a multi-line `description`, so a hard-coded value or a reader that handles only one-line values fails. Preflight still refuses, unpaid, an account `code-writer.md` that yields no tools, model or effort (`arms.bare_writer_markdown` and `account.py`). No test catches a change to the real file's frontmatter that the generator reads into a wrong but present value.
159. [engineer-verified: "Mark complete, halt after two in a row (Recommended)"] Asked how the per-arm stop treats a failed block, in place of holding it, the engineer selected this label and "Halt at the first block (Recommended)". The labels cover those selections, nothing more. [unverified] The option descriptions were the session's proposals, not the engineer's words. By the session's relay, the first label covers task-specific failed blocks and the second covers instrument failures. Row 153 holds the design that carries them out.
160. [unverified] The design for an instrument failure under row 159's "Halt at the first block (Recommended)", from a second architect consult the session relayed, not the engineer's words:
    - It triggers on any attempt that ends under an instrument reason. Neither `run` nor `smoke` retries such an attempt, because a clean retry would hide the evidence (`evals/README.md` § "Retry and missing reasons").
    - `run` halts at the block's end, after the block's other runs finish and the block is marked complete with the failing runs recorded missing. No resume reruns it, so the halt itself costs at most one task and never blocks a resume.
    - A broken `bare` exclude shows on the first `bare` run that reads a rule-matching file (row 145), because the halt comes at the first `arm-leak` attempt.
    - A resume goes on past the block only while the CLI version and the config checkout's commit still match the manifest. The config checkout stays at that commit with no uncommitted edits (the operating rule in `evals/README.md`).
    - A fix that changes the CLI version or the config checkout's commit makes the resume exit 2. That fix needs a new `--campaign-id`, which runs every block again and pays for it again.
161. [verified: `evals/replay_bench/checks.py`, committed as `681edfb5`; 263 passing tests] Phase 3 narrowed the Bash scan to the bounded contract in the `checks.py` module docstring, after a third-round architect consult found the lexical scan accreting defensive layers. Accepted residuals, which the README states:
    - heredoc and quoted-pattern false hits, such as `grep -rn "~/.claude"`;
    - `grep -r x ~` and `bash -c` text;
    - a denied Read, Grep or Glob call, since the runner's validity step has no notion of denial;
    - a Read of `<live>/.git` and of a linked worktree's common git dir;
    - the denial marker was verified on one CLI build only.

    The run record's `out_of_fixture_tokens` and `smoke` show each failing token.
162. [unverified] The session's design: `block_calibration_verdict` reads only the `toolkit` log, so the "both logs" wording of earlier rows is vestigial. The forged-log invariant lives in Phase 4. The runner takes the attempt dir and the fixture dir. It derives the verdict from the attempt path only, so a log written into the fixture is never read. Phase 3's pure functions cannot show that.
163. [engineer-verified: "Proceed anyway (Recommended)"] Asked whether to proceed past the diff-size gate for the second PR as one PR after the architect recommended one PR, the engineer selected this label. The gate showed the measured Phase 3 size of 2,658 lines, not the 7,500 to 8,000 projection. The label covers proceeding as one PR past the gate, nothing more. [unverified] The projected final size of 7,500 to 8,000 lines is the architect's inference, and the engineer has not accepted that figure by name. Row 141's 6,300 stands as the last figure the engineer accepted by name until they do.
164. [verified: Verification step 1b, as relayed] Step 1b ran against model `claude-sonnet-5` and CLI `2.1.322`. It found a `SubagentStop` send-back shape, which `checks.read_subagent_transcript` ignores. It left eight environment names unverified, and `evals/README.md` § "Accepted residuals" states them as a residual: `AI_AGENT`, `CLAUDE_EFFORT`, `CLAUDE_PID`, `COREPACK_ENABLE_AUTO_PIN`, `GIT_EDITOR`, `NoDefaultCurrentDirectoryInExePath`, `PYTEST_ADDOPTS` and `__CF_USER_TEXT_ENCODING`. No figure from step 1b is published (rows 9, 10).
165. [verified: `evals/replay_bench/checks.py` `_marks_permission_denial`, committed as `1b8cd410`; `evals/test_replay_bench_checks_denials.py`] On CLI `2.1.323` a denied Write leaves a top-level `toolDenialKind` and a `permissionDecision` whose `decision` is `reject` on the user entry, and no `tool_result_meta` marker. A call counts as denied on any of three markers:
    - a `toolDenialKind` in the closed set `permission-rule`, `user-rejected`, `automode-blocked`, `automode-unavailable`;
    - a `permissionDecision` of `reject`;
    - the legacy `non_execution_kind` of `permission-rule`.

    An `interrupted` or unknown kind counts as executed. [unverified] The shape on a subagent sidecar is unseen, so the legacy marker stays.
166. [verified: the session's runs of the live checks, on the engineer's "I authorize you to run those scripts"] The first `smoke` failed `bare` on the denial shape in row 165. After that fix, `tasks`, `preflight` and `smoke` passed. `smoke` ended `instrument: ok` on both arms. No figure from these runs is published (rows 9, 10).

**Mechanisms:**
- **M1. Task mining from first-parent commits.** `tasks.py` states the selection rules, refusals, briefs and recorded fields (Tasks). anchors: row6, row7, row9, row33, row34, row45, row46, row61, row64, row82, row92, row96, row100, row101, row102, row103, row104, row105, row106, row109, row130, row131.
- **M2. A single-commit archive fixture.** Fixture states its prune, opt-out and git timeout, and `diffing.py` states the `.git` copy. Lighter primitives checked:
  - (a) a `git worktree add` or `git clone` of the base commit is lighter, but both share or copy the object store, so the replayed commit stays reachable (row 27);
  - (b) a bench two-commit fixture fails the same way (row 28).
  - anchors: row21, row22, row23, row24, row27, row28, row83, row87, row88, row103, row106, row108.
- **M3. A thin headless dispatcher in front of one subagent in every arm.** This is heavier than one headless session per run. Lighter primitives checked:
  - (a) a headless main session per arm fails for the reasons in "Why both arms dispatch a subagent";
  - (b) `--append-system-prompt` carrying `code-writer`'s body into a main session delivers that body through a different channel than production's agent body, and the session still carries the Main-session rules.
  - anchors: root, row3, row8, row12, row13, row14.
- **M4. `bare-writer` as a fixture-local agent file that copies `code-writer`'s tools, model and effort.** `--agents` JSON is lighter, but `omitClaudeMd` is documented only as frontmatter. anchors: row12, row15, row29, row158.
- **M5. Per-repo account preflight and an allowlisted launch environment, as `account.py` and `launch.py` state.** Lighter primitives checked:
  - (a) the runner's own account for every repo breaks row 5;
  - (b) a `direnv exec` launch prefix hands the agent the runner's whole environment with the `.envrc`'s exports on top (rows 51, 52).
  - anchors: G2, row5, row15, row20, row44, row51, row52, row58, row69, row70, row72, row93, row94, row98, row132, row134.
- **M6. The `InstructionsLoaded` hook log as each run's arm check.** `checks.py` states how the log is read and attributed, and what fails each arm. Lighter primitives checked:
  - (a) the model's self-report is not mechanical and would contaminate the run;
  - (b) the subagent's first-turn input-token size cannot name which file loaded, and rules load mid-run on file reads, so it serves only `smoke`'s block calibration (M20);
  - (c) transcript `nested_memory` attachment records: this repository has observed them only in an interactive session's transcript (`claude/.claude/hooks/tests/test_claude_md_excludes.py:15-18`), and no source read says whether a `-p` run or a subagent transcript keeps them. [verified: `docs/session-config-keys-log.md:81`] Whether a `-p` run keeps even a hook attachment is untested.
  - anchors: row16, row17, row18, row19, row53, row76, row121.
- **M7. `evaluate_run_validity` unchanged, plus the runner's write-path, Bash-path and account-hook checks, and retry-then-missing.** anchors: row30, row31, row32, row48, row54, row55.
- **M8. Whole-line classification of `baseline.git` blobs by extension table, with Python through `tokenize` and the bench's docstring finder, a test-path rule, and word counts.** anchors: row35, row36, row38, row63, row77, row78.
- **M9. `RunStore` for the lock, write-ahead and sweep, with each fixture logged before it is filled, per-run atomic record files, cleanup of fixtures and attempt dirs on every exit path, and deletion of the campaign's whole `attempts/` on resume.** anchors: row43, row54, row56, row57, row79, row85, row154, row156.
- **M10. One block per task, with a pinned environment and spend guards.** `campaign.py`, `spend.py` and `evals/README.md` § "Run" state the manifest, spend file, stops, per-arm stop, exit codes and smoke gate. anchors: G3, G4, row2, row37, row65, row73, row74, row85, row86, row95, row97, row153, row155, row159, row160.
- **M11. `summarize` to the terminal only, over complete blocks, with a pre-registered ratio-of-sums word outcome, a paired bootstrap, one docstring secondary outcome, diagnostics, and spend and block counts.** anchors: G3, row4, row9, row10, row62, row63, row75, row157.
- **M12. `--permission-mode acceptEdits` on the dispatcher, with its Bash denials recorded and stated rather than lifted.** anchors: row25, row26, row122.
- **M13. Arm C as a gated pair sharing one route.** anchors: row2, row11, row13.
- **M14. `select-tests.py` wiring that follows the `measure_explore_recall.py` precedent, plus the no-relative-import check with a scanned-set control, and a declared row that selects it after a replay edit.** anchors: row39, row40, row67, row81, row133, row158.
- **M15. Local-storage and startup guards, with the README's trust warning.** Phase 1's `.gitignore` entry, `run_replay_bench.py` and `evals/README.md` § "Run" state them. anchors: row4, row41, row42, row45, row47, row59, row61, row66, row71, row91.
- **M16. A runner-owned copy of the fixture's pre-launch `.git` for every post-run git call.** Lighter primitives checked:
  - (a) `-c` overrides on the fixture's own `.git` form a denylist that misses any key it does not name;
  - (b) a Python diff of the work tree reimplements `git diff` and its symlink handling.
  - anchors: row49, row50.
- **M17. Test isolation and timing.** Phase 4's runner-test setup and Verification step 3 state the stubs, markers and duration check. anchors: row89, row90, row107.
- **M18. `claudeMdExcludes` in `bare` fixtures only, listing each account rule file's exact link path and realpath, with the account's `CLAUDE.md` left off.** Lighter primitives checked:
  - (a) `omitClaudeMd` alone still lets matching path-scoped rules load (row 16);
  - (b) the `--settings` JSON is not a layer the memory page names for the key (row 115).
  - anchors: row15, row16, row111, row112, row113, row114, row115.
- **M19. Exact `Read` allow entries on the command line, the same in both arms, as Launch states.** Lighter primitives checked:
  - (a) entries in the fixture's project settings are not used in a never-trusted folder (row 116);
  - (b) no grant sends every `toolkit` run back once and withholds the reviewer files production's `code-writer` reads (row 118).
  - anchors: row26, row55, row113, row114, row116, row117, row118, row134, row142.
- **M20. `smoke`'s block as its own calibration, with a printed exclude note, as Campaign states.** Lighter primitives checked:
  - (a) sentinel canaries need an edit to a stowed `CLAUDE.md` and read back through the model's self-report;
  - (b) a per-run `toolkit` load assertion cannot see an inherited `CLAUDE.md` (row 18).
  - The heavier calibration pair, two extra attempts with their own agent and fixture, stays the fallback (Alternatives set aside).
  - Superseded by row 143: the spend-file attempt reasons that backed a campaign leak stop. `run`'s instrument halt stops a campaign at the first `arm-leak` attempt (row 160).
  - anchors: row18, row113, row114, row115, row117, row120, row121, row134, row135, row136, row137, row138, row141, row143, row144, row145, row146, row160.
- **M21. Two PRs split at Phase 2 to Phase 3, with this file shipping whole in the first and edited in place by the second, the lightest delivery that keeps every plan change with its code.** Alternatives set aside:
  - (a) a new plan file for the follow-on branch would split one ledger, whose later rows anchor to earlier ones, across two files, and would rerun `/plan-it` and `/plan-review` over material already reviewed;
  - (b) shipping this file only with the second PR would merge the first PR's code with no plan (row 127), and the plan would have to come out of a branch that already committed it.
  - anchors: row124, row125, row126, row127, row128, row141.

## Critical files

**Dispatch split:** `code-writer` dispatches ran in order, one per phase, because each phase's output is the next one's input:
- the classifier feeds the task miner's metrics;
- the run checks parse the log and transcript shapes that Verification steps 1a and 1b capture;
- the runner composes all three;
- the test-selection wiring needs the final file names and imports.

The two PRs (Approach) split at Phase 2 to Phase 3, with Phase 5a in the first and Phase 5b in the second. Phase 4 stays one dispatch because the arms, launch settings, runner and CLI share the record, fixture and settings contracts.

**Phase 1 — classifier and ignore rule (first PR).** Verify: `scripts/venv-run.sh pytest evals/test_replay_bench_comment_metrics.py`, then `git check-ignore -q evals/replay_bench/.local/tasks/probe.json`.
- `.gitignore`: ignore `evals/replay_bench/.local/`, under a comment that it holds task files, run records and diffs carrying other repositories' commit text and code.
- `evals/replay_bench/__init__.py` (new): the package docstring.
- `evals/replay_bench/comment_metrics.py` (new): the classifier. It reuses `mine_szz._python_docstring_lines` and stdlib `tokenize`. `tasks` runs the diff it hands to the parser with `--src-prefix=a/ --dst-prefix=b/`, so a repo-local `diff.noprefix` cannot change the path form.
- `evals/test_replay_bench_comment_metrics.py` (new), synthetic sources only. Diff inputs are real `git diff -U0` and `--raw -z` output from a `tmp_path` repository, not hand-typed hunks. The cases pin the classifier's known gaps as outcomes, such as PHP `#`, SQL `/* */`, a heredoc body and a script with no spaces between words.

**Phase 2 — tasks and fixture (first PR).** Verify: `scripts/venv-run.sh pytest evals/test_replay_bench_tasks.py evals/test_review_bench_fixtures.py`.
- `evals/review_bench/fixture_repo.py`: a keyword-only `timeout_s` on `_run_git`, `_extract_commit_tree` and `_commit_snapshot`, defaulting to `_LOCAL_GIT_TIMEOUT_S` so every existing caller keeps its bound (row 87), and `build_single_commit_repo`, with its directory kind in the module docstring's list. No other change.
- `evals/test_review_bench_fixtures.py`: the shared-default case and the `build_single_commit_repo` cases, which belong beside that module's other tests (row 147). No other change.
- `evals/review_bench/local_git.py`: correct the module docstring's sentence about the miners, which says their git calls do not use `isolated_git_environment` (row 148). No other change.
- `evals/replay_bench/tasks.py` (new) and `evals/test_replay_bench_tasks.py` (new): task mining, the prune and `build_task_fixture`. Every git call runs under `local_git.isolated_git_environment()`, and the tests build every repository under `tmp_path`.
- The cases the ledger rows lean on:
  - a `.mailmap` that maps an untrusted email onto a trusted one keeps the commit out (row 92);
  - a symlink loop inside the fixture passes the prune's realpath check without raising (row 83);
  - an extraction-parity table, run through both `tasks`' predicate and `fixture_repo._extract_archive` on the same synthetic archives, pins where the two agree on the running interpreter (rows 103, 108, 109);
  - gitlink, non-ASCII and `missing` base trees (row 105);
  - a `--filter=blob:none` clone refused and a full clone accepted (row 130);
  - a live-git `git archive` of a tree whose `.gitattributes` says `tests export-ignore` (row 131).

**Phase 3 — run checks (second PR; committed as `681edfb5`).** Verify: `scripts/venv-run.sh pytest evals/test_replay_bench_checks*.py`. The tests split across `evals/test_replay_bench_checks*.py`, each under the 1,000-line cap.
- `evals/replay_bench/checks.py` (new): the hook-log parser, the instruction-load check, the block-calibration verdict, the account-hook check, and the write-path and Bash-path checks. It reuses `review_bench.runner`'s `live_checkout_leak_targets`, `is_changed_file_leak` and `is_config_dir_leak`. The verdict is a pure function of the `toolkit` run's parsed log (row 162).
- `evals/test_replay_bench_checks*.py` (new). Their log and transcript fixtures keep the key structure of the shapes Verification steps 1a and 1b captured, with every value synthetic: paths, session and message IDs, `transcript_path`, `cwd`, CLI version and every `usage` count. The margin cases compute from the fixtures' own numbers.

**Phase 4 — arms, launch, runner, CLI (second PR).** Verify: `scripts/venv-run.sh pytest evals/test_replay_bench*.py`.
- `evals/replay_bench/tasks.py` (edit):
  - `build_task_fixture` takes `settings_text`, written to `.claude/settings.json` beside its other extras;
  - `SelectionLimits.__post_init__` raises `TaskSourceError` for a limit outside its range (rows 149, 150). The check compares the resolved values after defaults. No pair other than `min_code_lines` and `max_code_lines` is checked, because `--max-tasks` above `--max-count` just caps the task count;
  - the `settings_text` and `SelectionLimits` cases are in `evals/test_replay_bench_tasks_limits.py`.
- The new modules under `evals/replay_bench/` are `arms.py`, `launch.py`, `account.py`, `attempt.py`, `campaign.py`, `spend.py`, `smoke.py`, `smoke_pass.py`, `summary.py`, `diffing.py`, `records.py`, `render.py` and `runner.py`. The runner is split across them so each file stays under the 1,000-line cap.
- `evals/conftest.py` (edit): the shared autouse `fresh_launch_abort_flag` fixture, which resets `msmr`'s launch-abort flag for every test in `evals/`, and the `checks_world` and `world` fixtures.
- `evals/replay_bench/comment_metrics.py` and `evals/test_replay_bench_comment_metrics.py` (edit): docstring wording and the `ValueError` text, with the test's `match=` following it.
- `evals/run_replay_bench.py` (new): the CLI. `tasks` takes one flag per `SelectionLimits` field, defaulting to `None`, and `cmd_tasks` passes only the flags given, by field name, so `SelectionLimits` holds the only defaults (row 152).
- `evals/test_replay_bench_arms.py` (new) builds `bare-writer.md` from a `tmp_path` `code-writer.md` and reads no real agents directory (row 158).
- The runner, campaign, spend, CLI, summarize and smoke tests are in `evals/test_replay_bench*.py` (new). Their setup, as M17 cites:
  - They stub every `claude` call, and every `direnv` call outside the preflight cases. An injected `launch=` writes a synthetic dispatcher transcript and subagent sidecar under a `tmp_path` projects root, and fakes the fixture edits.
  - They monkeypatch `review_bench.runner.read_environment_record` to a fixed reading, or to a sequence for the drift cases.
  - A local autouse fixture points `run_replay_bench.DEFAULT_LOCAL_DIR` under `tmp_path`. It also stubs `review_bench.runner.default_live_checkout_roots` to return no roots, since the shared stub runs only once `run_review_bench` is imported (row 90). `evals/test_replay_bench_runner.py` is the only test file that calls `main()`.
  - `build_replay_world` in `evals/test_replay_bench_runner_support.py` sets `SESSION_FLUSH_POLL_INTERVAL_S` and `SESSION_FLUSH_TIMEOUT_S` on `review_bench.runner` to near zero by module attribute, so the stubbed attempts skip the real flush wait (row 107).
  - `@pytest.mark.timing` marks the hang-prone and wall-clock tests (row 89). Every fake that sleeps runs `exec sleep <n>`, so a timeout's kill reaches the sleeper itself and leaves no orphan.
- The cases the ledger rows lean on:
  - a fake `claude` behind the `env -i` prefix, whose forked child a group kill must reach (row 44);
  - twenty parallel hook invocations leave a log whose every line parses (row 76);
  - a hostile `.git/config`, `.gitattributes`, FIFO and symlink to a FIFO: the run completes and no filter or fsmonitor command runs (rows 50, 77);
  - an ignored path and a nested repository with and without a commit (row 78);
  - an interrupted in-flight attempt writes its charge (row 155);
  - a log written into the fixture is never read, in both directions (row 162);
  - a proxy URL carrying credentials appears in no refusal message, `missing_detail` or captured output (row 98).

**Phase 5a — test selection for the first PR's files (first PR).** Verify: `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py` and `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/`.
- `claude/.claude/scripts/select-tests.py`:
  - `REPLAY_BENCH_DIR` and `REPLAY_BENCH_TEST_GLOB`, with a `DOMAIN_RULES` row that selects the glob;
  - the glob in the targets of three rows (row 133):
    - `_is_review_bench_change`, whose predicate also covers `EVALS_CONFTEST`;
    - `_REVIEW_BENCH_SCRIPTS_DEPENDENCIES`;
    - `SKILL_EVALS_RUNNER`.
  - a standalone row that selects `SELECT_TESTS_TEST_PATH` for a change under `REPLAY_BENCH_DIR`, because `test_select_tests.py` reads the replay package's files from a function body, which `TestCrossDomainReadCompleteness` cannot see.
- `claude/.claude/scripts/tests/test_select_tests.py`:
  - the glob joins every set-equality assertion over the review-bench target set (row 67);
  - `replay_bench/*.py` joins the import-closure test's seed list;
  - a test finds no `ImportFrom` with `level > 0` in `evals/replay_bench/`, with a seeded relative import as its failing control and an assertion on its scanned set, because the closure test maps every relative import to `review_bench` (row 81).

**Phase 5b — the rest of test selection, and docs (second PR).** Verify: as Phase 5a.
- `select-tests.py` and its test: `REPLAY_BENCH_RUNNER` joins Phase 5a's two rows, `_EXACT_MATCH_LITERAL_PATH_CONSTANTS`, the import-closure seed list and the no-relative-import test. `evals/test_replay_bench_arms.py` reads no real agents directory, so it needs no `AGENTS_DIR` wiring (row 158).
- `evals/README.md`: a "Replay bench" section after "Review bench", which points to this plan for the design.

The second PR's files are those of Phases 3, 4 and 5b. Phase 3 measured 2,658 lines at its commit, and the architect's inference puts the second PR near 7,500 to 8,000 lines, above the 6,300 row 141 accepted. Row 163 records the diff-size gate.

Projected diff: about 3,790 lines for the first PR as merged (3,755 added, 36 removed). `git show --numstat 330a03a2` measures it over every file but this plan, after the code-review fixes. The parts:
- the new `replay_bench` files and their tests hold most of it;
- `select-tests.py` and its test, about 130;
- `fixture_repo.py`, 40;
- `evals/test_review_bench_fixtures.py`, about 66;
- `evals/review_bench/local_git.py`, 5;
- `.gitignore`, 3.

That is about 900 above the 2,890 row 141 accepted. The engineer's earlier "Proceed anyway" on this plan's size is on record in row 110.

## Verification

Each step names its PR. Steps 2, 3 and 5 run for each PR over its own files.

1. Three manual probes. Steps 1a, 1b and 1c have run (rows 106, 164).
   - **1a, already run.** It ran three things, and its results stand in rows 16, 18, 19, 20, 26, 53, 76, 88, 94 and 106 and set the Fixture defaults:
     - `claude -p` once per arm with the Launch argv, the `env -i` prefix and the `--settings` JSON, on a one-line edit task in a scratch repo;
     - `direnv exec <dir> pwd` from `/tmp`;
     - four concurrent `build_task_fixture` builds per synthetic tree.
   - Step 1a hit two stop conditions, a missing subagent `CLAUDE.md` line and a rule load in `bare`, and so forced this revision.
   - **1c**, local and unpaid, ran on the first PR's branch before that PR's last dispatch. It timed two synthetic source repos under the system temp dir, each on its own, with four concurrent `build_task_fixture` builds through `scripts/venv-run.sh python3`:
     - 12,500 text files totaling 100 MB, the corner the bounds admit;
     - 17,500 text files.
   - The harness passes `timeout_s=600`, ten times the 60 s stop line, so a slow build records its time instead of timing out. The figure is a judgment.
   - Step 1c records each tree's file count, byte total and slowest build in row 106. The Fixture rule sets `FIXTURE_GIT_TIMEOUT_S` from the corner.
   - Afterwards it deletes both source repos and every fixture and `baseline.git` copy it built.
   - Step 1c's stop conditions, either of which would have sent its timing to the engineer before the last dispatch:
     - the corner's slowest build exceeds 60 s, so the accepted 12,500 default fails the Fixture rule (row 129);
     - the 17,500-file tree's slowest build exceeds 60 s, so the default sits within 40% of the unexplained slowdown.
   - **1b**, already run (row 164), ran on the follow-on branch before its Phase 3 dispatch. It settles rows 31, 94, 115, 117, 120, 132, 138 and 146 before Phases 3 and 4 depend on them. It makes three paid runs on one one-line edit task to a `.py` file, which this repository's Python rule matches, so that file is the probe's rule-matching file (row 145). Its steps, in the order they run:
     - Build three scratch git repos under the system temp dir, each holding that `.py` file, `.claude/worktree-optout` and `bare-writer.md`.
     - In the `bare` repo, add `bare`'s generated settings file, plus a probe-only root `CLAUDE.md` that the settings file's `claudeMdExcludes` also names. That entry tests row 115's never-trusted-folder question apart from the three rule questions.
     - In the third repo, add a probe-only `.claude/agents/probe-inherit.md`: `bare-writer`'s file with `omitClaudeMd` left out and its own name. It exists only for this probe, so the runner never ships it.
     - Write `list-env-names.py` into a fresh directory under the system temp dir with mode 0700, outside every fixture. Its body is `import os; print("\n".join(sorted(os.environ)))`, so it emits names from the process environment by construction, and no value byte can reach its output.
     - The engineer reads the script. It is then run once from a terminal with a synthetic variable whose value spans three lines, and its output must hold that variable's name and none of its value lines.
     - Before the first launch, print the probe's nominal ceiling: three runs at `msmr.PER_RUN_BUDGET_CAP_USD` each.
     - Run `toolkit`, `bare` and `probe-inherit`, each in its own repo, with one inner prompt and one argv for all three. The prompt asks for the one-line edit and for running exactly the listing command. The argv is the full Launch argv, Read allow entries included, plus one exact `Bash(<absolute python3 path> -I <script path>)` allow entry. So their first turns differ only in the agent and the `bare` repo's settings.
     - Record each run's cost and wall clock beside the cap. This is the first real replay-shaped figure behind row 37.
     - Read the hook logs, each subagent transcript's first assistant `message.usage`, observed model ID and Read of the `.py` file, each `toolkit` Read of an allow-list path with its result, each run's listing, and the `claude --version` line printed behind the launch prefix.
     - Record whether the `toolkit` run's `path_glob_match` line carries a field naming the `.py` file that triggered it, and under what key (row 146). Its absence is not a stop: `smoke`'s exclude note then prints only its residual sentence.
     - Compute by hand, from Campaign's formulas, since Phase 3's verdict does not exist yet:
       - the block calibration on the `toolkit` and `bare` sizes;
       - the body's real share, `toolkit`'s first-turn size minus `probe-inherit`'s, against the body term (row 138). Both inherit the same `CLAUDE.md` and rules and differ only in the agent body;
       - the inherited instructions' real share, `probe-inherit`'s size minus `bare`'s, against the margin.
     - Save one dispatcher line, one subagent `path_glob_match` line, and one subagent transcript's first assistant entry for Phase 3's fixtures. Keep their key structure and replace every value with a synthetic one: paths, session and message IDs, `transcript_path`, `cwd`, the CLI version and every `usage` count.
     - Clean up: delete the three scratch repos, the script's directory, and the three runs' session stores under `<config-dir>/projects/`. Row 79's appended account files and the three runs' `sessions/` files stay, as accepted residue (row 154).
   - Step 1b's stop conditions, each of which calls for a re-plan with the engineer:
     - the `toolkit` run shows no Read of the `.py` file that ran, so the control went unexercised;
     - the `toolkit` run read it and logs no subagent `path_glob_match` load of an account rule;
     - the `bare` run shows no Read of the `.py` file that ran, so the exclude went unexercised;
     - the `bare` run logs a subagent load outside its fixture;
     - `bare`'s dispatcher logs a load of the probe-only `CLAUDE.md`, so a never-trusted folder's project settings do not apply `claudeMdExcludes` (row 115);
     - `bare`'s dispatcher logs any other load besides the account's `CLAUDE.md`;
     - `bare`'s dispatcher shows no `session_start` load of the account's `CLAUDE.md`;
     - a run's first assistant entry lacks one of the three usage fields;
     - `probe-inherit`'s first-turn prompt size exceeds `bare`'s by less than the margin;
     - the body term misses `toolkit`'s first-turn size minus `probe-inherit`'s by more than half the margin. The re-plan then restores the calibration pair this design replaced (rows 136, 138);
     - the hand-computed block calibration fails;
     - a subagent's observed model ID differs from `REVIEWER_MODEL_ID` (row 31);
     - the `claude --version` line does not parse as a dotted version (row 132);
     - a `toolkit` Read of an allow-list path is denied;
     - the `toolkit` run makes no Read of an allow-list path;
     - the writer is sent back for a missing reviewer Read;
     - a run's listing is denied;
     - a run's listing names a variable outside the allowlist that neither the shell nor Claude Code sets, such as a token or a cloud-profile variable;
     - a run stops at the per-run cap, so a replay does not fit it and every printed ceiling rests on a wrong cap (rows 37, 65).
2. Each phase's own command in Critical files, run by that phase's dispatch.
3. For each PR's whole change, run `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py`, then `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/`. Both PRs edit `select-tests.py`, one of its global trigger paths, so it selects the full suite on its own (row 40).
   - After each PR's first CI run, compare the `tests` job's duration with the latest `main` run's, against the `timeout-minutes` cap (row 89).
   - A duration within 5 minutes of the cap goes to the engineer before merge, because re-deriving the cap edits `.github/workflows/tests.yml`, a file outside this plan's list.
   - Record `scripts/venv-run.sh pytest --durations=20` over that PR's new and edited replay test files beside that comparison, so a slow test shows by name (row 107).
4. First PR: `git check-ignore -q evals/replay_bench/.local/tasks/probe.json` exits 0.
5. A review check covering:
   - every new test builds its repositories, transcripts and config dirs under `tmp_path`;
   - no committed log or transcript fixture carries a captured value: its paths, IDs, CLI versions and `usage` counts are all synthetic;
   - no committed file names or mines a real repository (row 9).
6. Second PR, before it merges: live checks the engineer runs from a terminal through `scripts/venv-run.sh python3`, never in CI. These checks spend subscription quota. The first PR has none, because it ships no CLI. Their output stays in the terminal and `.local/`, and the PR body may say whether they passed but cites no figure (rows 9, 10).
   - `scripts/venv-run.sh python3 evals/run_replay_bench.py tasks --source-repo <this repository's main checkout> --trusted-author <the engineer's commit email> --name own-history --max-tasks 5`, then read the briefs, the printed identities, and the largest kept tasks by size and by entry count.
   - `scripts/venv-run.sh python3 evals/run_replay_bench.py preflight --tasks own-history` resolves one account with no refusal, its readiness check included. The same check on a task set from one other local repo, whose direnv configuration sets `CLAUDE_CONFIG_DIR`, resolves that account's config dir. The engineer reads each account's list of dropped variables before the first `run`.
   - `scripts/venv-run.sh python3 evals/run_replay_bench.py smoke --tasks own-history --task-id <id>` uses one task, so no second smoke task is needed.
     - `<id>` is the task with the most base-tree entries among those that modify a `.py` or `.sh` file the base tree already holds, which this repository's rules match (row 145). The engineer reads the candidates' changed paths from the task file. A file the commit only adds may never be read.
     - The task need not be the largest by entry count overall, because step 1c already timed the bounds' corner locally.
   - That `smoke` passes when all of these hold:
     - its last line reads `instrument: ok`, and it wrote the account's smoke-pass record;
     - the engineer has read the exclude note and its residual sentence, which gate nothing;
     - each arm's valid run has a non-empty diff;
     - both arms observed the same model;
     - each run's fixture-build seconds are under a third of the `--fixture-git-timeout-s` in force. A smoke block builds two fixtures at once, so this holds at two-way concurrency, while the timeout was derived at four-way;
     - the engineer recognizes every recorded Bash command, out-of-fixture path and out-of-session path.
   - In their own terminal, the engineer lists the account config dir before and after that `smoke`. The listing goes two levels deep, outside `projects/` and `sessions/`, with each file's size. They read names only, never a file's contents. Every name that appeared and every file that grew is either one of row 79's known appenders or a file added to the cleanup before any `run`.
   - Beside that listing, the engineer counts the keys under the top-level `projects` object of the account's `.claude.json` before and after that `smoke`, and notes only whether the count grew. A growth is an open question for the engineer before any `run`, and the runner never edits `.claude.json`.
   - A `run` on any other account first needs one passing `smoke` on a task from that account, and `run` refuses otherwise.
   - On a failed smoke, stop before any `run` and bring the failing premise to the engineer: row 16, 18, 19, 20, 26, 31, 32, 37, 70, 88, 106, 115, 117, 120, 132, 138, 145 or 146.

## Out of scope

- Building arm C. It waits on D2, D3 and its own smoke (row 2).
- A judge pass that runs `comment-discipline-reviewer` over each stored diff and counts its Comment verbosity and Restating-the-code findings per arm. A word or line count measures comment volume and shape, not verbosity as that lens judges it (row 38). The Context's intended outcome names line counts, and a judge needs its own validation first. Every run keeps its diff, so a later judge pass needs no rerun.
- Counting trailing comments outside Python, and a third-party lexer such as pygments for language-accurate tokens. A lexer is a new dependency that needs the engineer's confirmation first, and the paired contrast tolerates a classifier error both arms share (row 35).
- Publishing any figure, an own-history one included (rows 4, 9, 10).
- Source repos that are not on this machine (row 6).
- A headless main-session arm, the review-and-ship loop, as a further arm. It can be added later as arm data. Until it exists, a null result cannot show the verbosity is built-in (row 62).
- Containing a replay agent, by a sandbox, a credential deny list or a tool-deny set. Steered agents are out of scope for this runner (row 47), and the README states the residual risk (row 45).
- Writing each run's record as it finishes, so that a crash loses no paid run's record. A resumed block reruns whole, so per-run records would need a drift rule of their own. The spend file already keeps the cost of a lost block's ended attempts.
- Deleting the rows that replay sessions add to the account's shared logs (row 79), or the `sessions/` file each launch leaves (row 154). `post-crash-sessions.py` lists those files for four hours after a campaign. The README lists both as accepted residue.
- Charging, at resume, a launched attempt that a hard kill left with no spend line. The resume prints the count, and the README lists this as accepted residue (row 155 states the bound).
- Telling a usage-limit, auth or overload event apart from a task-specific failure. Such an event that fails every run of two blocks marks two tasks no agent really tried complete and drops them, and how `claude -p` behaves at a usage limit is unverified (row 153).
- A stop for failures on alternating tasks, which never make two failed blocks in a row (row 153).
- `summarize` figures beyond the primary outcome, the docstring outcome and the diagnostics:
  - comment-plus-docstring lines per added code line;
  - blocks per 100 added code lines;
  - mean lines and words per block;
  - the block-length histogram;
  - the Python trailing-comment count;
  - the base-density split (row 64);
  - the mean first-turn prompt size;
  - the per-repo breakdown;
  - each account's cap-charged share of spend.

  The records keep what each needs, so a later change adds any of them with no rerun (row 157).
- A `smoke` listing of the account config dir. Verification step 6 has the engineer take it by hand.
- A login check in preflight. Whether the installed CLI has a login-status command is unchecked, so `smoke` is the first check that authenticates (row 70).
- Moving the bench's shared pieces (validity, run store, environment record) into a common module. That would end this runner's reads of names with a leading underscore in `review_bench.runner` and `measure_subagent_model_resolution`. It is a separate refactor (Axis 4). Until then, `select-tests.py` reruns the replay tests on every review-bench change (row 39).
- Changing `_LOCAL_GIT_TIMEOUT_S` for the review bench's own callers. The replay runner passes its own limit through the new `timeout_s` keyword (row 87).
- Re-deriving the CI job's `timeout-minutes` cap. Verification step 3 brings a job near the cap to the engineer instead (row 89).
- A `CHANGELOG.md` entry. The change adds contributor-side eval tooling, and the one stowed file it edits is `select-tests.py`'s rule table.
- Allowing Bash commands beyond `acceptEdits`' auto-approved set and the account's allow rules, such as a source repo's test command. Both arms meet the same rules, the permission-denial count shows the effect, and the README states it (row 122).
- Archive-time `text`, `eol`, `ident` and `filter` conversions, which can rewrite a file's bytes but keep every path. The `export-attributes` skip covers only the two attributes that drop a path or substitute text (row 131).
- Narrowing the Read grant to the reviewer files by importing the evaluator's `ACCEPTED_REVIEWERS`. The engineer accepted the wider set (rows 124, 125).
- A containment check that every granted realpath lies inside the config checkout. The engineer accepted cutting it (rows 134, 135). The grant is read-only and its entries are exact. Its files are the config checkout's own only on a folded stow install, and the README says the grant can include other files elsewhere (row 142).
- Refusing a proxy URL that carries credentials. The README states the single-user assumption instead, and the print-hygiene tests already use such a URL.
- A `smoke` spend stop and a `smoke --k`. `smoke` runs one run per arm and prints its ceiling, and the engineer accepted dropping both (rows 134, 135).
- A probe of row 121's premise, such as a delayed second hook. The engineer accepted dropping it (rows 134, 135), and row 121 states the residual.
- A scanned-set control and a declared selection row for `review_bench`'s own import-closure guard, which has the gap Phase 5a closes for the replay package. It is outside this plan's files (Axis 1).
- A per-run check that a `toolkit` subagent received the account's `CLAUDE.md`. The hook logs no line for an inherited `CLAUDE.md` (row 18), so `smoke`'s block calibration checks inheritance once per account.
