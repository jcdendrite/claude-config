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
- The first PR, merged as #1452, holds Phases 1 and 2 and the `select-tests.py` rows for its own files (Phase 5a). Its last dispatch ran after Verification step 1c. It set the `--max-base-entries` default, adds the `export-attributes` skip, and refuses a partial clone and a repo whose `info/attributes` sets either attribute (Tasks). It needs no `claude -p` run.
- The second PR, on a follow-on branch from `main` after the first merges, holds Phases 3 and 4 and the rest of Phase 5 (Phase 5b). Verification step 1b runs at its start, before its Phase 3 dispatch, and the live checks run before it merges.
- This file stays whole and ships with the first PR, where it is already committed (row 127). The second PR edits it in place, so each later change to the plan ships with the code it governs. Its Phase 3 to 5 sections are the second PR's plan, and main holds that design before its code. M21 names the two alternatives set aside.
- A step 1b stop re-plans Phases 3 to 5 on top of the merged first PR, whose miner, classifier and fixture builder stand alone. Only abandoning the runner reverts the first PR. That revert stays clean while no file outside the first PR's own files imports its code.

### How a replay runs

**Tasks (`run_replay_bench.py tasks`).**
- `tasks --source-repo <path> [--source-repo <path> ...] --trusted-author <email> [--trusted-author <email> ...] --name <task-set>` walks `git rev-list --first-parent <rev>` in each repo, newest first, and writes the qualifying commits to `.local/tasks/<task-set>.json`.
  - `--rev` defaults to `HEAD`, and `--max-count` to 500.
  - `--source-repo` and `--trusted-author` have no default. An empty `--trusted-author` exits 2, since it would match a commit with an empty author email.
  - Each source repo must be the top level of a git repository on this machine, a linked worktree's included (rows 6, 7). `git -C <path> rev-parse --show-toplevel` must print the path's own `os.path.realpath`. A subdirectory, a non-repository path and a bare repository each exit 2 naming the path, because `git archive` run from a subdirectory would build a fixture rooted there (row 96).
  - `tasks` exits 2 naming the path for a partial clone.
    - A partial clone is a repo where `git config --get-regexp` for `extensions.partialclone` or any `remote.<name>.promisor` prints a line (row 130).
    - Git fetches a missing object on demand there, which would put network I/O with no timeout into `tasks` and into a fixture build.
  - A source repo whose own `info/attributes` file, found with `git rev-parse --git-path info/attributes`, holds a line with the text `export-ignore` or `export-subst` exits 2 naming the path, since the attribute would reach every commit's archive (row 131).
  - `--name` must pass `identifiers.validate_campaign_id` (row 60).
- Author and committer identities come from `git log --no-use-mailmap` with the `%ae` and `%ce` placeholders, for both the trust rule and the printout, and each is compared exactly. A source repo's `.mailmap` therefore cannot map an untrusted email onto a trusted one (row 92).
- The runner first drops from a commit's changed paths every path the fixture prune removes. The rules below apply to the remaining paths, so a commit that also edited an instruction file still yields a task.
- A commit qualifies when all of these hold:
  - it has a parent, and its remaining diff against its first parent touches at most `--max-files` (default 10) paths, none binary;
  - that diff adds between `--min-code-lines` (default 10) and `--max-code-lines` (default 300) non-test code lines, both bounds inclusive;
  - its author email is a `--trusted-author`, and so is every merged commit's author email for a merge commit (row 61);
  - no brief line, stripped of surrounding whitespace, equals `BEGIN CHANGE DESCRIPTION`, `END CHANGE DESCRIPTION`, `<<<` or `>>>`, so a brief cannot close its own block early;
  - no remaining path holds a comma, a control character or a byte git quotes, since the inner prompt lists paths comma-separated;
  - its inner prompt is at most `--max-prompt-chars` (default 4000) characters. A longer one is skipped, never truncated (row 33);
  - its base tree's blobs total at most `--max-base-mb` megabytes, a megabyte being 10^6 bytes, and the tree holds at most `--max-base-entries` entries. Each bound is its own skip reason.
    - Their defaults come from the build timing under Fixture.
    - Both bounds come from one `git ls-tree -r -l -z <base>` call once every other rule passes. With `-z`, git prints each path verbatim instead of quoting an unusual name.
    - A non-blob entry, such as a submodule's gitlink, prints `-` for its size, so it counts as zero bytes and one entry and is never read as a file (row 105).
    - Each attempt holds about three copies of that tree, as the fixture's work tree, its `.git` and `baseline.git`, across four worker threads (row 82).
  - every object `tasks` reads with `git cat-file --batch` is present. A `missing` reply skips the commit under its own reason (row 105). A partial clone is refused before the walk, so a reply here means an object absent for another reason;
  - no `.gitattributes` blob in its base tree, at any depth, holds a line with the text `export-ignore` or `export-subst`, under the skip reason `export-attributes`.
    - `git archive` honors both attributes, so the fixture would lack a path or hold rewritten text that `tasks` sized, counted and measured from the real tree (row 131).
    - The check finds those blobs in the same `git ls-tree` listing and reads them in the same `git cat-file --batch` call.
    - It matches the text anywhere on a line, a comment or an unset form included, which skips more than git would apply but needs no attribute parser;
  - its base tree holds no entry the fixture's extraction refuses (rows 100, 103, 104). The check reads the `120000` entries of that same `git ls-tree` call, and their link targets with one `git cat-file --batch` call. Each shape counts under its own skip reason (row 101):
    - a symlink whose own path's first component is `.claude`, matched case-insensitively;
    - a symlink with an absolute target;
    - a symlink whose target, joined to the link's directory, normalizes to a path outside the tree;
    - an entry or a symlink target with a `.git` path component;
    - an entry whose first path component is `.bench`, matched case-insensitively.
- `--max-tasks` (default 30) caps the tasks kept per repo, newest first, counted separately for each repo.
- While it walks, `tasks` prints a progress line for each repo every 50 commits examined and when the repo finishes, with the commits examined and kept so far.
- `tasks`' own git calls on a source repo carry no timeout. The command is interactive and spends nothing, so Ctrl-C ends a hung call, and one fixed bound would fail a slow but healthy walk of a large repo.
- At the end, `tasks` prints the kept and skipped counts by skip reason and the count of kept tasks with a null base density. It names the largest kept task by base-tree size and the largest by entry count, each with its ID, size and entry count. It also prints every distinct raw author and committer identity among the candidates with its commit count, so the engineer can see whom the trust list leaves out.
- The brief is the commit message. A merge commit's brief is its message plus the subjects of the commits it merged, so a repo that merges pull requests with merge commits still yields one task per merged pull request.
- Each task records:
  - the source repo's absolute path, and the base (first-parent) and commit SHAs;
  - the brief, the remaining changed paths, and the author and committer identities;
  - reference metrics, the classifier run over the commit's own diff, so `summarize` can show what actually merged beside each arm with no extra launch;
  - base density, the comment-plus-docstring words per non-test code line in the base versions of the changed non-test files (row 64). A task whose changed non-test files hold no code line in the base, such as one that only adds new files, keeps a null density and stays in the task set;
  - its base-tree size in bytes and its entry count.
- A task ID is the first 8 hex digits of the source path's sha256, a hyphen, and the commit's first 12 hex digits.
- Reading a task file exits 2 on a task that lacks a key, whose ID breaks that grammar, or whose SHAs fail `fixture_repo._validate_sha`. `tasks.py` raises `TaskFileError` for those, and `identifiers.InvalidIdentifierError` for a task-set name that breaks its grammar.

**Fixture.**
- Every run attempt gets a fresh directory from `msmr._resolved_temp_project_dir(f"{FIXTURE_DIR_PREFIX}replay-")`, using `review_bench.runner.FIXTURE_DIR_PREFIX` so that `RunStore`'s sweep accepts it.
- The runner logs that directory with `RunStore.record_directory(<task-id>, <dir>, "")` before it writes anything into it (row 57).
- It exits 2 when the directory lies under `$HOME`, where ancestor instruction files would load.
- The new `fixture_repo.build_single_commit_repo(source_repo, commit, dest_dir, *, prune, timeout_s)` runs `git init`, extracts the base tree with `_extract_commit_tree`, calls `prune(dest_dir)`, and commits once with `_commit_snapshot`. It keeps the bench's archive-only extraction, so the replayed commit is unreachable from the fixture (rows 27, 28).
- `--fixture-git-timeout-s` bounds every git call that builds the fixture or reads it after the run, in place of the bench's 10 s `_LOCAL_GIT_TIMEOUT_S` (row 87). The calls do no network I/O, since `tasks` refuses a partial clone, so the limit only bounds a hung git.
  - It defaults to the runner's `FIXTURE_GIT_TIMEOUT_S`. The flag lets a loaded machine raise the limit without a code edit. It changes no result, so it stays out of the manifest comparison.
  - The defaults come from this rule, applied to Verification step 1a's timing of four concurrent fixture builds on synthetic text trees (rows 88, 106):
    - the `--max-base-mb` default stays 100 when a 10,000-file tree's slowest build takes 60 s or less;
    - the `--max-base-entries` default starts at 100,000 and halves until a tree of that many files builds within 60 s;
    - `FIXTURE_GIT_TIMEOUT_S` is three times the slowest build across the fitting trees, the step 1c corner included, rounded up to a whole 10 s.
  - The 60 s ceiling, the 100,000 starting point and the multiplier are judgments, not measurements.
  - Step 1a's relay gave no byte total for the halving trees, and nothing measured locates the jump between 12,500 and 25,000 files. So Verification step 1c timed two more trees before the first PR's last dispatch: the corner the bounds admit, 12,500 files totaling 100 MB, and 17,500 files totaling 100 MB.
    - Neither tree exceeded 60 s, so step 1c did not stop.
    - Had either exceeded it, the stop would have gone to the engineer, because the 12,500 default is the engineer's accepted figure (row 129).
  - Row 106 holds the measured series. Under the rule above:
    - `--max-base-mb` defaults to 100;
    - `--max-base-entries` defaults to 12,500;
    - `FIXTURE_GIT_TIMEOUT_S` is 110 s, three times the 33.5 s corner build at 12,500 files totaling 100 MB, rounded up.
- The replay prune walks the tree without following links. Without it, nested discovery loads a checkout's own instruction files into both arms (rows 23, 24).
  - It removes every entry named `CLAUDE.md`, `CLAUDE.local.md` or `AGENTS.md`, matched case-insensitively, at any depth.
  - It removes every directory named `.claude` at any depth, and the root `.mcp.json`, each matched case-insensitively.
  - It unlinks a matching entry that is a symlink, a nested `.claude` link included, never its target.
  - Extraction refuses a link with an absolute target, a link that resolves outside the tree, and a link at the root `.claude` path, and `tasks` skips any commit whose base tree holds one (Tasks, row 103).
  - After its removals, the prune resolves every remaining symlink with `os.path.realpath` and raises when one resolves outside the fixture. A link chain can pass both the lexical skip and a tar filter that does not normalize link targets, and an agent's write through such a link would escape the diff (rows 83, 103, 108).
  - A prune or an extraction that raises, an `UnsafeFixtureConfigError` included, fails that run as `fixture-error` before any launch (Retry).
- After that commit the runner writes `.claude/worktree-optout` and `.claude/agents/bare-writer.md` into every fixture, both arms alike, plus the arm's fixture settings file when the arm names one (Arms). It adds `/.claude/` to `.git/info/exclude`. The opt-out keeps machine-level worktree enforcement from denying the agent's writes (rows 21, 22). The exclude keeps these files out of the diff.
- The runner then copies the fixture's `.git` to `<attempt dir>/baseline.git`. The attempt dir is `.local/campaigns/<campaign-id>/attempts/<task-id>-<arm>-<n>-<attempt>/`. Every post-run git call uses that copy, so the diff compares the work tree with the pristine base whatever the agent did to the fixture's own `.git`, a commit, a reset or an edited `.git/config` included (rows 49, 50).

**Arms (`replay_bench/arms.py`).** An arm is data:
- a name, and the agent the dispatcher names;
- the `CLAUDE.md` its dispatcher must load at `session_start`, and the dispatcher loads that fail it;
- the fixture settings file it writes, if any;
- the fixture-file loads its subagent may show.

The two arms:
- `bare` dispatches `bare-writer`.
  - Its frontmatter copies `tools`, `model` and `effort` from the account's `<config-dir>/agents/code-writer.md`, read with `msmr.agent_frontmatter_tools`, `msmr.agent_frontmatter_model` and an `effort` reader over `msmr._frontmatter_block`.
  - It adds `name: bare-writer`, `description: Implements the change the prompt describes.` and `omitClaudeMd: true`.
  - Its body is the one line `You implement the change the prompt describes, in the current repository.`
  - Its fixture settings file, `.claude/settings.json`, holds only `claudeMdExcludes`. `omitClaudeMd` still lets matching path-scoped rules load into the subagent, and one did in Verification step 1a (rows 16, 115).
  - The exclude list is generated per attempt. For every `.md` file under the account's `rules/` at any depth, it holds the path as the walk reached it and that path's `os.path.realpath`, each an exact absolute path.
  - The walk follows directory links and skips a directory whose realpath it has already visited.
  - The generator raises `UnsafeConfigPathError`, naming the path, on a path holding any character the Read allow list refuses (Launch).
  - The settings file's whole text is the JSON object `{"claudeMdExcludes": [...]}`, with no other key.
  - The account's `CLAUDE.md` stays off the list. The dispatcher's `session_start` load of it stays the run's positive control, and `omitClaudeMd` already drops it from the subagent (row 15).
  - Any dispatcher load other than the account's `CLAUDE.md` fails it (Instruction-load check). That covers a managed-policy `CLAUDE.md`, an unexcluded rule and a file the account's `CLAUDE.md` includes, since whether `omitClaudeMd` drops an included file is unverified.
- `toolkit` dispatches `code-writer` by name, which resolves to the account's stowed user-scope agent. The `SubagentStop` self-review hook, whose matcher is `code-writer`, therefore fires as it does in production and never fires for `bare-writer` (row 13).
  - It writes no fixture settings file, so its subagent gets the account's rules as production's does.
  - No dispatcher load fails it.
- Both arms expect the account's `CLAUDE.md` as their dispatcher's `session_start` load, and both allow no fixture-file load.

Both arms send the same inner prompt. Its delimiters differ from the `<<<` and `>>>` lines that `build_dispatcher_prompt` wraps it in:

```text
Implement the change described between the BEGIN and END lines below in this repository. The original change touched these paths: <changed paths, comma-separated>. Leave your changes in the working tree, uncommitted. Do not install packages or run anything that needs network access. When you finish, reply with the paths you changed.

BEGIN CHANGE DESCRIPTION
<brief>
END CHANGE DESCRIPTION
```

**Account preflight.** Before any launch, the runner probes each source repo once. It refuses the repo, with exit 2, on any failure below.
- The probe is `direnv exec <source-repo> <sys.executable> -I -c <snippet>`, run from a fresh empty temp dir. The snippet prints JSON holding its working directory, the value of `CLAUDE_CONFIG_DIR`, and the names of the variables in its environment, with no other value. It takes those names as the keys of `os.environ`, never by parsing `env` text, so no line of a multi-line value can reach the output.
- The runner prints, as names only, every variable in the probe's environment that the launch drops: each name outside the launch allowlist, other than `CLAUDE_CONFIG_DIR` and direnv's own `DIRENV_` variables (row 134).
  - The list covers variables the runner's own shell set too, so a runner started where direnv already loaded the repo still sees every dropped name.
  - A dropped provider-selecting variable can leave a launch authenticating through the config dir's own login instead of the route the engineer meant (row 70).
  - The runner never prints or stores a value.
- The runner makes that call with its own `subprocess.run`, bounded by `--direnv-timeout-s`. It does not use `_read_environment_field`, whose error text carries the child's stderr (row 58).
  - `--direnv-timeout-s` defaults to `review_bench.runner._ENVIRONMENT_READ_TIMEOUT_S`, which was sized for local calls with no network I/O. An `.envrc` that fetches a secret can need longer (row 72).
- A probe failure prints the repo path and one of these, and never the probe's stdout or stderr:
  - `exit <n>`, with a fixed hint to run `direnv exec <repo> true` in a terminal to see direnv's own message;
  - `timed out after <limit> s; raise --direnv-timeout-s`;
  - `_os_error_detail`'s text.
- The resolved config dir is `CLAUDE_CONFIG_DIR` when set, else `~/.claude`.
- An account is keyed by its resolved config dir's `os.path.realpath`. Two repos that resolve to one config dir are one account, with one spend pool and one smoke pass.
- The runner refuses the repo when:
  - the runner's own `PATH` holds an empty or relative entry, which would let `env` resolve `claude`, or the agent resolve a command, from the fixture (row 93);
  - `direnv` is not on `PATH`;
  - the probe fails or times out, for example on a blocked `.envrc`;
  - the probe's stdout does not parse as the snippet's JSON;
  - the printed working directory is not the probe's launch directory (row 20);
  - the resolved config dir lacks `CLAUDE.md` or `agents/code-writer.md`;
  - the account's `code-writer.md` yields no tools, model or effort, which would otherwise hand `bare-writer` an empty field;
  - the account's `settings.json` registers no `SessionStart` command ending in `capture-session-id.sh` (row 54);
  - the exclude-list or Read-allow-list generator raises `UnsafeConfigPathError` (Arms, Launch). Preflight calls the same generators an attempt calls, so its check and an attempt's cannot drift apart. The message names the path, and a checkout under a path holding a space makes every account refuse;
  - the readiness check below fails.
- The readiness check runs `claude --version` behind the exact launch prefix from Launch environment, from the probe's temp dir.
  - Its limit is a `timeout_s` parameter, which `preflight` fills from `review_bench.runner._ENVIRONMENT_READ_TIMEOUT_S` (row 72).
  - `preflight` reads that constant through its module at call time, so a test's stub reaches it (row 90).
  - The check shows that `claude` resolves on the allowlisted `PATH` and starts. It does not show that the account is logged in, so `smoke` is the first check that does (row 70).
  - It refuses a printed version below 2.1.271, the floor `omitClaudeMd` needs (row 15), and a version it cannot parse (row 132).
- The readiness check and every launch report a failure in the probe's shape: the repo, then `exit <n>`, `timed out after <limit> s`, or `_os_error_detail`'s text. This covers a readiness refusal, a launch exception's `missing_detail`, and a `LaunchAbortedError`, whose text is fixed (row 98).
  - None carries the argv, the child's output or an environment value, because the argv holds every allowlisted value, a proxy URL's credentials included.
  - The readiness check does not use `_read_environment_field`, whose error text carries the command and the child's stderr (row 58).
- It prints each repo's resolved config dir and the probe's wall clock. It stores and prints no other environment value.
- `preflight --tasks <task-set>` runs only this step and launches nothing.

**Launch environment (`replay_bench/launch.py`).** The child's environment is built from a named allowlist, not inherited (rows 51, 52). Inherited, it would hand the subagent's Bash the runner's own session variables, such as another account's token, and route an honest agent's command to the wrong account.
- The allowlist, kept as data:
  - `PATH`, `HOME`, `USER`, `LOGNAME`, `SHELL`, `TMPDIR`, `LANG`, `LC_ALL` and `LC_CTYPE`;
  - `HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY`, `ALL_PROXY`, their lowercase forms, `NODE_EXTRA_CA_CERTS` and `SSL_CERT_FILE`, which a launch behind a proxy or a private CA needs to reach the API (row 70). Which proxy forms the CLI reads is unchecked, so the allowlist passes each form the runner's environment sets.
- The launch argv starts with `/usr/bin/env -i`. Then comes `NAME=value` for each allowlisted variable the runner's environment sets, plus `CLAUDE_CONFIG_DIR` when the probe found it set. A variable the runner does not set is left out, never passed empty.
- Nothing from the source repo's `.envrc` reaches the child besides `CLAUDE_CONFIG_DIR`, and the `.envrc` runs once per repo, at preflight.
- `HOME` passes, so file-based credentials such as `~/.config/gh`, `~/.aws` and `~/.ssh` stay reachable from the agent's Bash and are not routed by account.
- `env` replaces itself with `claude`, so `msmr._run_claude_to_completion`'s group kill still reaches the child (row 44).
- Each allowlisted value sits in `env`'s argv until `env` execs `claude`, so on a shared machine another user's process listing could read a proxy URL's credentials. The runner assumes a single-user machine, and the README says so.

The `--settings` JSON carries one `InstructionsLoaded` command hook and nothing else (rows 17, 19). The hook is `payload=$(cat); printf '%s\n' "$payload" >> <log>`, where `<log>` is the shell-quoted `<attempt dir>/instructions-loaded.jsonl`. It emits each event in one write, so two hooks firing at once cannot split one payload from its newline (row 76).

Nothing contains a subagent beyond `acceptEdits`' working-directory scope, the account's own permission rules, and the read-only grants of the Read allow entries under Launch (rows 25, 26, 47). The path checks under Validity guard the measurement, not the machine: they find a write or a path outside the fixture after it happened.

**Launch.** Each attempt runs the `env -i` prefix, then `review_bench.runner.build_dispatch_command(build_dispatcher_prompt(<arm agent>, <inner prompt>), model_id=..., session_id=..., budget_cap_usd=...)` with three additions:
- `--permission-mode acceptEdits`, which both arms' subagents inherit (rows 25, 26);
- the `--settings` JSON above;
- last in the argv, `--allowedTools` followed by the Read allow entries below, each its own argv element (rows 116, 117).

The Read allow entries let `code-writer` read the reviewer and skill files its body names, which `acceptEdits` denies outside the fixture (rows 26, 118). The list is the same in both arms and is generated per attempt:
- Its roots are the resolved config dir and `$HOME/.claude`, deduplicated by realpath, each only when it exists. `code-writer.md` names its files under `~/.claude`, and the `SubagentStop` evaluator matches a reviewer Read by path suffix (row 118).
- Under each root it lists every `*.md` file directly under `agents/` and every `skills/<name>/SKILL.md`, skipping a dangling link.
- Each file gives one entry for the path as listed and one for its `os.path.realpath`, deduplicated, since an allow rule applies only when both paths match (row 117). An entry is `Read(/<absolute path>)`, so it starts with the `//` absolute-path prefix.
- No entry holds a wildcard. Before it emits any entry, the generator raises `UnsafeConfigPathError`, naming the path, when a path it would emit holds whitespace, a control character, or one of `*`, `?`, `[`, `]`, `{`, `}`, `(`, `)`, `,` or `\`. An exact entry holding one could parse as a pattern or split in two (rows 115, 117).
- That character check is one function, which both generators call and preflight reaches through them.
- An attempt whose generator raises records the run missing as `fixture-error`, with the path in `missing_detail`, before launch and with no spend (Retry, row 134).
- The per-attempt check catches a file added to `agents/` or `skills/` mid-campaign, which the commit-based drift check cannot see.
- The set is wider than the reviewer files: every agent and skill file under the roots. Parsing the reviewer names from the evaluator or from `code-writer.md`'s table would copy the evaluator's name-to-path rule.
- A `bare` Read of any listed file still fails `arm-leak`.
- On a folded stow install every listed realpath lies in the one config checkout. Where stow linked `agents/` or `skills/` file by file into a directory that already existed, the list also grants that directory's own files (row 142).
- Every refusal or printout that names a path from a config tree, or a variable name from the probe, prints it in Python's `!r` form, as `tasks.py:814-816` prints identities, so a control character or an escape byte in a name cannot reach the terminal raw.

Before launch the runner logs the fixture again with its session ID (row 43). `msmr._run_claude_to_completion` launches the command with the fixture as its working directory. Defaults:
- `--model-id`: `review_bench.runner.REVIEWER_MODEL_ID`;
- `--budget-cap-usd`: `msmr.PER_RUN_BUDGET_CAP_USD`;
- `--timeout-s`: `review_bench.runner.REVIEWER_TIMEOUT_S` (row 37).

An attempt whose stdout holds no line fails as `launch-no-output` before `evaluate_run_validity` runs. Every other launch failure arrives under one of validity's own reasons, such as `no-result-event`, `result-error` or `session-store-not-found`. `_run_claude_to_completion` discards the child's stderr and returns no exit status, so no failure reason or detail carries either (row 69).

**Validity.** After `wait_for_session_flush(<config-dir>/projects, session_id, timed_out=...)`, the runner calls `evaluate_run_validity` unchanged (row 30) with:
- `dispatcher_session_jsonl`, `stream_lines` and `timed_out` from the launch;
- `expected_agent_name` and `expected_inner_prompt` from the arm and the task;
- `expected_model_id`: `--model-id`;
- `agent_declared_tools`: `code-writer`'s declared tools;
- `fixture_dir`: the fixture;
- `own_dirs`: the fixture plus the run's own session paths;
- `projects_root`: `<config-dir>/projects`;
- `own_session_paths`: from `own_session_paths_for`;
- `live_checkout_roots`: `review_bench.runner.default_live_checkout_roots()`, called through its module so a test's stub reaches it (row 90), plus `_worktree_top_levels(<source-repo>)`;
- `changed_relpaths`: the task's remaining changed paths.

`evaluate_run_validity` checks only Read, Grep and Glob paths (row 30). The runner's own checks in `replay_bench/checks.py` then read the subagent transcript's `tool_use` entries. They keep the measurement honest, since a run that reached outside its fixture no longer shows what its arm alone wrote. They fail the run on:
- `out-of-fixture-write`: an Edit, MultiEdit, Write or NotebookEdit target that resolves outside the fixture;
- `out-of-fixture-path`: a Bash command token that resolves to any of these:
  - a path under the projects root, other than the run's own session paths;
  - a live checkout's changed path;
  - a path under the local dir;
  - a path in another `FIXTURE_DIR_PREFIX` directory.

The module docstring of `evals/replay_bench/checks.py` holds the full contract. In summary, the Bash check is a bounded scan of the command text (row 161). The summary leaves out two rules the docstring states: a directory that holds a guarded path while inside its tree is a hit, and a command `shlex` cannot split is also scanned as raw text. `<live>/.git` is among the guarded roots.
- It removes backslash-newline, splits the command per line, then splits each line with `shlex`. Each word and its shell-separator pieces is a candidate.
- A candidate over `MAX_PATH_CHARS` is skipped and recorded as unparsed.
- Only `~`, `$HOME`, `${HOME}`, `$PWD` and `${PWD}` expand, and `$PWD` is the fixture dir. Relative tokens resolve against the fixture.
- Containment is bounded to the config dir, the live checkout roots and `.git`, the local dir, and the `FIXTURE_DIR_PREFIX` directories. A word at or below a target is a hit.
- A command `shlex` cannot split is recorded as unparsed.

Both checks, and `check_tool_call_paths` as a whole:
- The run's record keeps every Bash command, and every absolute token outside the fixture that no rule above fails.
- A `tool_use` whose command or target path is missing or not a string, and a token holding a NUL byte, are recorded as unparsed and never resolved.
- A denied Bash command or write tool reached nothing, so it fails neither `out-of-fixture-path` nor `out-of-fixture-write`. It still counts toward a `bare` run's toolkit reach.
- `check_tool_call_paths` also returns `transcript-unreadable` (`runner.VALIDITY_FAIL_TRANSCRIPT_UNREADABLE`) when the transcript is unreadable, its last line is torn or lines were skipped. Phase 4 calls it on the subagent sidecar of every attempt.
- The check reads literal paths only, so it detects and does not contain.

Each run also records how many `tool_result` entries carry a permission-rule `non_execution_kind`, which a hook denial and a permission-flow denial both carry (row 48). A denied write can empty one arm's diff without failing validity, so `smoke` prints the count and `summarize` reports it by arm. Each run also records its Reads of Read-allow-list paths, each with whether it ran or was denied, and the paths of its denied Reads of other toolkit files.

**Instruction-load check.** The runner reads its own `<attempt dir>/instructions-loaded.jsonl` as untrusted input. It uses the `file_path` values only to classify them.
- The hook runs asynchronously (row 121). So the runner reads the log only after `wait_for_session_flush` returns and the log's size holds across two polls `SESSION_FLUSH_POLL_INTERVAL_S` apart, bounded by `SESSION_FLUSH_TIMEOUT_S`. It reads both constants from `review_bench.runner` at call time (row 107).
  - A log absent at one poll and present at the next counts as growth, and a missing file never raises.
  - At the timeout the runner reads the log as it stands, so a torn last line fails `instrument-log-invalid` and no run hangs.
  - A `timed_out` attempt skips the wait, as `wait_for_session_flush` does.
  - This loop mirrors `wait_for_session_flush`'s two-poll rule but watches a file that function does not, so it carries its own tests (Phase 4).
- A line with no `agent_id` key is the dispatcher's, and a line whose `agent_id` is a non-empty string is the subagent's (row 53).
- The run fails as `instrument-log-invalid` on any of these:
  - a line that does not parse as a JSON object, a torn last line included;
  - an `agent_id` that is present but null, empty or not a string;
  - a `session_id`, `file_path` or `load_reason` that is absent or not a string;
  - a `file_path` holding a NUL byte, on which `os.path.realpath` would raise;
  - a `session_id` other than the run's;
  - lines carrying more than one distinct `agent_id`.
- A `file_path` reaches `os.path.realpath` only once its line passes every check above.
- A `path_glob_match` line's trigger-path field, under the key step 1b finds (row 146), is kept only when it is a string holding no NUL byte, and is never resolved. Any other value leaves it absent and fails nothing, since only `smoke`'s printed note reads it.
- The run fails as `instrument-missing` when the log is absent or empty, or holds no dispatcher `session_start` load of the arm's expected `CLAUDE.md`. That load is the positive control that the hook fired.
- The check resolves every loaded path and every toolkit path with `os.path.realpath` before comparing them, so a stowed file reported by its link target still classifies.
- Each loaded path falls in one class: toolkit (the account's `CLAUDE.md`, a file under its `rules/`, `agents/` or `skills/`, or a file on the Read allow list), fixture (under the fixture), or other.
- `bare` fails as `arm-leak` on any subagent load outside the fixture (row 16).
- `bare` also fails as `arm-leak` on a dispatcher load its arm names as failing, which is any load other than the account's `CLAUDE.md`. The hook logs no line for what a subagent inherits from its dispatcher (row 18), so a file the dispatcher loaded could reach the subagent unseen.
- `bare` also fails as `arm-leak` when a Read, Grep, Glob or Bash path resolves to a toolkit file, a read `toolkit` makes by design (row 55).
- `toolkit` has no per-run load assertion, because the hook logs no subagent line for an inherited `CLAUDE.md` (row 18). `smoke`'s block checks that inheritance once per account instead (Campaign).
- Either arm fails as `fixture-instruction-load` on a fixture-file load its arm does not allow.
- Duplicate loads of one file are counted, never failed.
- Every run records its subagent loads by class, with each path relative to the config dir or the fixture.
- Every run also records its subagent's first-turn prompt size, which no check gates (row 120). It is the sum of `input_tokens`, `cache_creation_input_tokens` and `cache_read_input_tokens` in the `message.usage` of the subagent transcript's first assistant entry, or null when any of the three is absent.
- The run fails as `account-hooks-missing` when `<config-dir>/sessions/` holds no non-symlink file whose first line is the run's session ID. That file shows that the account's hooks ran beside the `--settings` hook, so `toolkit` still carried its `SubagentStop` hook (rows 19, 54).

**Retry.** A failed attempt retries once on a fresh fixture, per `review_bench.runner.ATTEMPTS_PER_RUN`, and then records the run missing with its reason.
- A retried run's cost and token figures sum both attempts' `extract_result_usage`.
- An attempt with no cost figure, such as a timeout, a kill or a launch exception, is charged at `--budget-cap-usd` (row 65).
- Two failures record the run missing at once, because a retry would fail the same way on the same input:
  - `fixture-error`: building the fixture or generating its exclude or Read allow list raised, before any launch. An extraction's `UnsafeFixtureConfigError` and a generator's `UnsafeConfigPathError` both land here, and `missing_detail` names a generator's refused path;
  - `analysis-error`: the diff, the classifier or a check raised after the launch, or a post-run git call failed or timed out. The run keeps the attempt's cost.
- Neither reaches `main()` as an exit 2. A task-specific one costs its own block, and the per-arm stop (Campaign) halts the campaign with exit 1 only after two such blocks in a row.
- When `fixture-error` or `analysis-error` comes from a git timeout, its `missing_detail` reads `git timed out after <limit> s; raise --fixture-git-timeout-s`, naming the limit in force.
- One attempt can meet several reasons. One attempt can meet several reasons. The instrument reasons are every reason the instruction-load check or the account-hook check returns: `arm-leak`, `instrument-missing`, `instrument-log-invalid`, `fixture-instruction-load` and `account-hooks-missing`. All five sit in one constant, and the precedence between them is:
  - `arm-leak` and `fixture-instruction-load` outrank every other reason, so a timed-out attempt whose log shows a `bare` leak records `arm-leak`;
  - `instrument-missing`, `instrument-log-invalid` and `account-hooks-missing` outrank only when the run reached its end;
  - a timeout, `launch-no-output`, `launch-error`, `no-result-event` or a missing session store keeps its own reason, because a run that never finished leaves a log that is often absent or torn;
  - `result-error`, `budget` and `api-error-final-record` count as reaching the end.

  [unverified] An auth or API error reported as a result event before the hooks fire would then record the absence reasons and halt the campaign instead of retrying. Whether the hooks fire before the first model call is unchecked.
- A launch call that raises `OSError` records `launch-error`, charged at the cap and retried once. It is not an instrument reason. Its detail carries no argv or environment value.
- Neither `run` nor `smoke` retries an instrument reason. The attempt's run ends missing under that reason. A clean second attempt would hide it, because such a reason is evidence about the instrument. `run` then halts at the block's end (Campaign).
- Each attempt's cost reaches the spend file as the attempt ends (Campaign).

**Diff and classifier (`replay_bench/comment_metrics.py`).** After the session ends, the runner never runs git with the fixture's own `.git`, which the agent could have rewritten. It never reads a changed file through its work-tree path either (rows 49, 50).
- It runs `git --git-dir=<attempt dir>/baseline.git --work-tree=<fixture> add -A`.
- It then runs `git -c core.quotePath=false diff --cached -U0 --no-renames <fixture commit>` with `local_git.DIFF_TEXT_ARGS` and the same two location flags, and stores that diff beside the run's record.
- `git diff --cached --raw -z --no-abbrev --no-renames <fixture commit>`, with the same location flags, names each changed entry's new mode and blob.
- One `git cat-file --batch` call reads the blobs of the regular-file entries, modes `100644` and `100755`. Those bytes are each file's post-change text. An agent-made symlink is a `120000` entry holding its link text, so no read follows it (row 77).
- Every git call runs under `local_git.isolated_git_environment()`, bounded by `--fixture-git-timeout-s` (Fixture).
- An `add -A` failure, such as on a nested repository with no commit, is an `analysis-error` (row 78).

The run records three tallies of what it does not classify:
- `non_file_entries`: changed symlink and gitlink entries;
- `ignored_paths`: untracked paths other than `/.claude/` that the work tree's ignore rules keep out of `add -A`, from one `ls-files -z --others --ignored --exclude-standard --directory` call, kept as a count and the first 100 paths (row 78);
- `undecodable_files`: blobs that do not decode as UTF-8 once a BOM is stripped. Their added lines count as unclassified.

Each added line of a changed regular file is classified from that file's post-change text:
- blank: whitespace only;
- docstring: Python only, from `mine_szz._python_docstring_lines` (row 36);
- comment: a whole line holding only comment text. Python uses `tokenize`, falling back to the `#` prefix rule when tokenizing fails. Other languages use an extension table of line prefixes and of block delimiters that open at line start;
- code: any other line in a classified language;
- prose: any non-blank line in a `.md`, `.markdown`, `.rst` or `.txt` file;
- unclassified: a line in a file with an unknown extension, or at a path git still quotes.

The extension table starts with three families, kept as data a new language extends:
- `#`: `sh`, `bash`, `zsh`, `rb`, `pl`, `r`, `yml`, `yaml`, `toml`, `tf`, `ps1`, and files named `Dockerfile` or `Makefile`;
- `//` plus `/* */`: `js`, `jsx`, `mjs`, `cjs`, `ts`, `tsx`, `go`, `rs`, `java`, `kt`, `kts`, `swift`, `c`, `h`, `cc`, `cpp`, `hpp`, `cs`, `scala`, `dart`, `php`, `scss`, with `css` taking `/* */` alone;
- `--`: `sql`, `lua`, `hs`.

A file is a test file when a path segment is `test`, `tests`, `__tests__` or `spec`, or when its basename matches `test_*`, `*_test.*`, `*.test.*`, `*.spec.*` or `conftest.py`. The rule is data a new convention extends.

A comment or docstring line's words are its whitespace-separated tokens that hold a letter or digit, once its comment delimiters are removed. A script that puts no spaces between words, such as Chinese or Japanese, yields one word per whitespace-separated run, a known gap.

A comment block is a maximal run of consecutive added lines of one class, comment or docstring, in one file. Each run records:
- its line counts per class, and its comment-plus-docstring words, each split into test and non-test files;
- each block's line and word counts;
- its Python trailing-comment count, as a diagnostic (row 35);
- its count of Python files that `ast.parse` rejects, since `_python_docstring_lines` then finds no docstring and those lines count as code;
- the three tallies above;
- the paths it changed.

**Cleanup.** Every attempt ends in a `finally`, whether it was valid, invalid, timed out, raised or interrupted. A valid attempt captures its diff and metrics first. The `finally` deletes the fixture, the attempt's session store and the attempt dir. It leaves the attempt's `<config-dir>/sessions/` file, as every Claude Code session does (row 154).

A retry starts only after the failed attempt's `finally` has run.

**Campaign (`run`, `smoke`).** Each task is one block, run in task-file order.
- A block reads `review_bench.runner.read_environment_record` at its start and its end. Its checkout root is the parent of the account's resolved `CLAUDE.md`.
- Each account's `EnvironmentReference` is built from the reading the manifest pinned for that account, so drift halts the campaign across a resume too (row 73). A drift halt prints the block's attempt count and its spend from the spend file.
- `build_block_plan(task_id, arm_names, k, seed)` orders the block's runs, and `run_skill_evals.DEFAULT_WORKERS` threads run them.
- Once the block's end reading matches, the runner writes one `ReplayRunRecord` JSON per run to `.local/campaigns/<campaign-id>/runs/<task-id>/<arm>-<n>.json` with `defects.atomic_write_text`. It writes each diff beside its record, then calls `RunStore.mark_block_complete`, before the per-arm stop below decides whether to halt.
  - A block rerun after a crash overwrites its own files, so reading needs no dedupe.
  - A crash before that point loses the block's records, at most `2 × k` runs with up to two attempts each. The spend file still holds the cost of their ended attempts. The plan accepts the lost records so that each block's drift check stays whole.
- After each block, `run` prints one line: the task ID, the ok and missing counts per arm, each arm with no valid run and its missing reasons, the running count per arm, since this invocation started, of tasks dropped for having no valid run in that arm, the block's spend, and the cumulative and account totals against their stops.
- `ReplayRunRecord` keeps `status`, `missing_reason` and `missing_detail` in the bench's meanings, so `count_outcomes` and `all_runs_missing` apply to it as they are. No halt prints `systemic_failure_message`. Its fixed text says the block is not marked complete and a resume reruns it (row 95), and a block that ends in a per-arm halt is always complete.
- `RunStore(.local/campaigns/<campaign-id>/store)` supplies the lock, the write-ahead entries, and the resume sweep (row 43).

**Spend file.** `.local/campaigns/<campaign-id>/spend.jsonl` holds one JSON line per ended attempt: the task ID, arm, run index, attempt number, account key, session ID, cost, and whether that cost is a cap charge.
- The runner appends each line with one `os.write` on an `O_APPEND` descriptor, under a lock its worker threads share.
- An append that fails, such as on a full disk or an I/O error, fails closed: `run` halts with exit 1, printing the error, before the block is marked complete.
- A crash mid-append can leave a torn last line. A resume repairs it with `review_bench.runner._repair_torn_tail` before its first append, so the torn bytes never fuse with the next line (row 86).
- A line that still does not parse counts at `--budget-cap-usd` against the cumulative stop and every account stop.
- `run`'s stops sum this file, never the run records.
- A launched attempt writes no line when a hard kill, such as SIGKILL, an out-of-memory kill or a power loss, ends the runner before the attempt ends (row 155).
  - Each hard kill can leave up to one launched attempt per worker thread uncharged, `run_skill_evals.DEFAULT_WORKERS` in all.
  - The undercount adds up over kills: each hard kill can add up to `DEFAULT_WORKERS` times the per-run cap.
  - The cap is a nominal figure, not a proven bound on one attempt's spend (row 65).

A resume does these things, in order, before its first launch:
- it exits 2 naming the repo when the probe resolves a source repo to an account key other than the manifest's;
- it compares each account's current environment reading with the manifest's pinned one, and exits 2 naming the account and the field that differs;
- it takes the store's lock with `RunStore.acquire_lock`, which every later step runs under (row 156);
- it repairs the spend file's torn tail;
- it runs `sweep_abandoned` once for each account projects root in the campaign. Each call returns the whole store's pending entries (row 85), so the resume merges them into one set and reruns each swept task's block once. It charges no swept entry (Spend file);
- it prints the count of swept entries that carry a session ID and have no spend line, as `<N> logged attempts with a session ID have no spend line and were not charged`, since an attempt killed before its launch counts too. When N is above zero, the resume's total line below and its ceiling line (spend guards) each say they leave those attempts out;
- it prints the spend file's total for blocks not yet complete;
- it deletes the campaign's `attempts/` directory whole with `remove_tree_reporting_failure`:
  - an `attempts/` that is a symlink makes `run` refuse with exit 2, naming the path, before any delete;
  - an absent `attempts/` is no failure, so the resume checks that it exists first and prints nothing.

The attempt-dir creator creates `attempts/` when it is absent. It refuses a same-named attempt dir that already exists, and that attempt records `fixture-error` with the path in `missing_detail`, before any launch.

`run`'s inputs:
- `--k` and `--cumulative-stop-usd` are required, with no default for either.
- `--account-stop-usd` caps each account's spend, and defaults to the cumulative stop.
- `--campaign-id` must pass `identifiers.validate_campaign_id`.
- The first start pins a manifest of the task-set hash, arms, k, seed, model ID, each source repo's account key, and each account's environment reading, read before the first launch. A resume whose task-set hash, arms, k, seed or model ID differs exits 2.
- `--fixture-git-timeout-s` stays out of the manifest and its comparison, since it changes no result.

`run` exits 2 before its first launch for any account in the task set that has no smoke pass. An account has none in each of these cases:
- no smoke-pass record exists for it;
- its record holds another environment reading than the account's current one;
- its record holds another model ID than `run`'s;
- its record lacks one of the manifest's arms;
- its record does not parse or lacks a key, which reads as absent with no traceback;
- its record holds an account key whose digest differs from its file name, which also reads as absent.

The gate puts `smoke`'s checks, and the engineer's read of what it prints, ahead of every paid block on each account. A CLI update or a `git pull` of the account's config checkout changes the reading, so either calls for a new `smoke`.

`run`'s spend guards (row 65):
- A block's ceiling is `campaign_cost_ceiling(1, <arm count>, k, budget_cap_usd=...).all_retried_usd`.
- Before its first launch, `run` prints the run count and the campaign's nominal ceiling.
- It exits 2 before its first launch when one block's ceiling exceeds either stop.
- It also exits 2 before its first launch when the campaign's ceiling exceeds the cumulative stop, unless `--allow-early-stop` acknowledges that the campaign will stop before its last task.
- On a resume, the printed run count covers only the incomplete blocks. The campaign's ceiling there is the spend file's total so far plus those blocks' ceilings.
- Before each block, it stops when the block's ceiling would carry either total past its stop:
  - the spend file's total, against the cumulative stop;
  - the block's account's spend, against the account stop.
- Either stop halts the whole campaign, so a resume with a raised stop goes on in task-file order.
- After each block, the per-arm stop checks each arm by `all_runs_missing` over that arm's records, so an arm fails only when none of its runs is valid. `all_runs_missing` over the whole block would miss one arm failing alone, as `bare` does on a machine with a managed-policy `CLAUDE.md` (row 74). A block where any arm fails is a failed block. Every block that reaches this check, failed or not, is already complete, with its missing runs recorded as missing, so no resume reruns it:
  - An instrument failure halts at the first block. It triggers on any attempt that ends under an instrument reason (Retry), whatever the arm's other runs did. The block's other runs finish, and `run` halts at the block's end with exit 1, naming the task, the arm and the reason. So each instrument halt costs at most one task, and a resume after the cause is fixed goes on to the next task (row 160).
  - Every other failed block is task-specific. `summarize` leaves its task out of the pairs and counts it.
  - After two task-specific failed blocks in a row, `run` halts with exit 1. Its message names each failing arm with its missing reasons and `missing_detail` text, names the two tasks now lost from the sample, and says a resume goes on past them.
  - The count of failed blocks in a row lives in memory for one invocation. It starts at 0 on every start and resume, and any block with a valid run in every arm resets it (rows 153, 159).
- A persistent failure meets the pre-block spend stops and the two-in-a-row halt. Failures on alternating tasks never make two in a row, so only `--cumulative-stop-usd` bounds them (row 153).

`run`'s halts and exit codes:
- Every halt prints its reason: environment drift, the cumulative stop, the account stop naming its account, an instrument failure naming its task, arm and reason, or two task-specific failed blocks naming both tasks and their failing arms.
- `run` then exits 1, the bench CLI's code for a run that stopped short, and keeps exit 2 for refusals (row 97).
- A run that completes every task exits 0, even when its last block failed alone. When its last two blocks fail so that the two-in-a-row halt fires, exit 1 wins.

`smoke --tasks <task-set> --task-id <id>` runs one block under a fresh `smoke-` campaign ID, with one run per arm on that task (row 134). `--task-id` must match the task-ID grammar.
- Before its first launch, `smoke` prints its nominal ceiling, `campaign_cost_ceiling(1, <arm count>, 1, budget_cap_usd=...).all_retried_usd`.
- A `smoke` attempt that fails under an instrument reason never retries, as in `run` (Retry). Any other failure retries once, as in `run`.

For each run, `smoke` prints:
- status and reason;
- subagent instruction loads by class, and the first-turn prompt size;
- recorded Bash commands, out-of-fixture paths and out-of-session paths;
- the permission-denial count, each denied Read of a path on the Read allow list, and each denied Read of another toolkit file, by name;
- for a `toolkit` run, the count of its Reads of allow-list paths that ran;
- the `non_file_entries`, `ignored_paths` and `undecodable_files` tallies;
- metrics, cost, fixture-build seconds and wall clock.

**Block calibration.** The hook cannot show the `CLAUDE.md` a subagent inherits (row 18), and no source read says that `bare`'s exclude holds under `-p` (row 115). So the block's own two runs calibrate the instrument. They share one task, one brief and one inner prompt (rows 136, 137):
- Positive control: the `toolkit` run's log holds a subagent `path_glob_match` load whose realpath lies under the account's `rules/` directory at any depth, compared by path component, not by string prefix. Otherwise `instrument-missing`, since `bare`'s leak check would then pass blind. These do not count:
  - a `path_glob_match` load on a dispatcher line;
  - a subagent load with another `load_reason`;
  - a `path_glob_match` load of a fixture-class or other-class file;
  - a `path_glob_match` load of another toolkit file, such as one under `agents/` or `skills/`, or the account's `CLAUDE.md`.
- The smoke task must touch a rule-matching path for the positive control to hold, and a task that does not fails rather than passing empty (row 145).
- Exclude: the `bare` run's own per-run checks fail it as `arm-leak` on any subagent load outside the fixture and on any dispatcher load besides the account's `CLAUDE.md`. That shows the exclude holding only when `bare`'s subagent read a rule-matching file (row 145).
- Exclude note, printed and never gated (row 143):
  - When the `toolkit` control fired and its `path_glob_match` lines name the file that triggered each load (row 146), `smoke` prints whether the `bare` transcript shows a Read that ran of any such file, compared by path relative to each run's fixture. A payload with no trigger field leaves this line out.
  - `smoke` always prints one residual sentence: "A `bare` run that read no rule-matching file passes without testing the exclude, and a later `bare` run that reads one fails `arm-leak`."
- Inheritance: the `toolkit` run's first-turn prompt size, minus the `bare` run's, minus the body term, is at least the margin. The comparison is `>=`. Otherwise, or when either size is null, `arm-config-missing`.
- The verdict is a pure function of both runs' parsed logs and first-turn sizes, the character counts below, and the rule files the `toolkit` dispatcher loaded at `session_start`. It reaches the verdict only when each arm has a valid run, so a missing run reports its own missing reason instead.
- The verdict returns every failed condition, in the order above, and `smoke` prints them all. The inheritance check is token arithmetic, so it stays computable when the control fails, and one paid block shows every problem it can.

The terms, each a character count over 4:
- the margin's first term, `0.5 × chars(account CLAUDE.md) / 4`, half the `CLAUDE.md` estimate;
- the margin's second term, `chars(rule file) / 4` for each rule file the `toolkit` dispatcher loaded at `session_start`, summed and not halved, so an unscoped rule, which `bare`'s exclude drops, cannot stand in for `CLAUDE.md`;
- the body term, `(chars(code-writer body) − chars(bare-writer body)) / 4`. Each body is its agent file's text after the frontmatter, and `code-writer`'s comes from the account's `<config-dir>/agents/code-writer.md`, the file the dispatcher resolves, never this checkout's copy. It removes the one other known difference between the two subagents' first turns.

`smoke` prints both first-turn sizes, the body term and the margin. Row 138 states the body term's weak point and its fallback.

`smoke`'s last line reads `instrument: ok` only when all of these hold, checked in this order:
- each arm has a valid run. Otherwise the line names the arm and its run's missing reason;
- no run failed under an instrument reason;
- no `toolkit` Read of a path on the Read allow list carries a permission-rule `non_execution_kind`. Otherwise `read-grant-denied` (row 117);
- the `toolkit` run made at least one Read of an allow-list path that ran. Otherwise `read-grant-unexercised`, since a grant no Read used shows nothing (row 117);
- the block calibration passed.

A denied Read of a toolkit file off the list prints by name and fails nothing. A `bare` Read of a listed path fails `arm-leak`, never `read-grant-denied`.

Otherwise the line names the first failed condition, `smoke` prints every failed condition it computed, and `smoke` exits 2. On `instrument: ok`, `smoke` writes the account's smoke-pass record with `defects.atomic_write_text` to `.local/smoke-passes/<account digest>.json`. The record holds the account key, its environment reading, the model ID and the arms `smoke` validated. The digest is the first 16 hex digits of the account key's sha256.

`main()` starts in this order:
- it exits 2 on a Python older than 3.12 (row 59);
- it exits 2 on a git older than 2.32, read from `git --version` (row 71);
- it sets an owner-only umask;
- it exits 2 unless the local dir passes the check below;
- it calls `run_review_bench._restrict_local_dir_to_owner` on the local dir (row 42);
- it routes SIGHUP and SIGTERM into `KeyboardInterrupt`, as `msmr.main()` does.

On an interrupt, `main()` calls `msmr.abort_launches()` and prints the resume command. The message says two things:
- wait at least `--timeout-s` after a hard kill before resuming, since a surviving `claude` child can still be writing (row 56);
- resume with the same `TMPDIR`, since the sweep matches logged fixtures only directly under the current system temp dir (row 43).

**Local dir.** `--local-dir` defaults to `evals/replay_bench/.local/` in the checkout the runner runs from. A linked worktree's `.local/` goes when the worktree is removed (row 66), so `--local-dir` can name another checkout's. Before creating it, `main()` requires all of these:
- an absolute path, equal to its own resolved path;
- a final component named `.local`;
- `git check-ignore -q` reporting it ignored in the work tree that holds it (row 4).

**Summary (`summarize --campaign-id <id>`).** It prints to the terminal and writes no file (rows 4, 10).
- It exits 2 when one account's records span more than one CLI version or ambient config commit.
- It reads only the records of blocks that `RunStore.completed_block_ids()` lists. A crash between record writes and `mark_block_complete` can leave an incomplete block's records on disk, and `summarize` leaves them out and counts that block as incomplete.
- It prints counts by arm, status and missing reason, and the counts of complete and incomplete blocks.
- It prints the spend file's spend by account. A line that does not parse, and so names no readable account, counts at `--budget-cap-usd` against every account, as the stops count it, and `summarize` says so beside the figures.
- The primary outcome, fixed before any run, is comment-plus-docstring words per added non-test code line (row 63).
  - For each task and arm it is a ratio of sums: the words over that arm's valid runs of the task, divided by their non-test code lines.
  - `summarize` reports the paired difference `toolkit − bare` over tasks with a valid run and at least one non-test code line in both arms. It uses `analysis.bootstrap_interval` over tasks as singleton clusters, and prints no interval for fewer than two paired tasks (row 75).
  - Directly under that difference it prints `paired <n> of <M> complete tasks`, and then the excluded tasks in four counts, each with its missing reasons: `toolkit` with no valid run, `bare` with no valid run, both arms with no valid run, and no non-test code line. It prints no threshold warning.
- A null primary difference shows only that the writing agent's instructions leave its first draft's comment density unchanged on these tasks. It cannot show that the verbosity is built-in, because no arm runs the review loop through which the toolkit also shapes comments (row 62).
- Secondary outcome, per arm beside the task's reference metrics: Python docstring lines per 100 added code lines.
- Diagnostics, per arm:
  - the unclassified-line share, and the count of Python files `ast.parse` rejected (row 35);
  - the count of runs with any permission-rule denial;
  - the `non_file_entries`, `ignored_paths` and `undecodable_files` tallies.
- A run with no non-test code line still enters its task's sums, and also counts in a separate tally by arm.
- Every other figure the records hold, such as the base-density split, stays out of `summarize` (row 157).

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
    - The pair's arm data names that fixture copy as the dispatcher's expected `session_start` load, so `instrument-missing` fires when the dispatcher shows none. It names the account's `CLAUDE.md` as a dispatcher load that fails the run.
    - The memory page does not say whether `claudeMdExcludes` reaches the user-scope `CLAUDE.md` (row 115). The pair's own `smoke` settles that from the dispatcher's log.
    - The pair's subagents inherit `CLAUDE.md` with no log line (row 18), so the pair's `smoke` needs a prompt-size check of its own, designed with the pair.
  - (b) `omitClaudeMd: true` with the edited text inlined into the agent body, which is a system-prompt channel.
  - (c) A separately logged-in config dir per account. Each needs the engineer's own login and a stow, and copying credentials is barred (row 11).
- **D3 (arm C only).** What "removes only the §Durable text rules" removes. Both readings are correct:
  - (i) the `### Durable text` subsection of `CLAUDE.md` alone;
  - (ii) every channel stating those rules to `code-writer`: that subsection, the two `code-writer` baseline sub-bullets that restate it ("Write any comment as a one-line durable fact" and "Don't restate what the code already shows"), the last bullet of its self-review step 6, the comment-discipline row of its self-review table, and the comment-length bullet of `claude/.claude/rules/shell-script-conventions.md`, which reaches a subagent as a path-scoped rule (row 16).
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
13. [verified: `claude/.claude/settings.json:232-242`] The `SubagentStop` hook `send-back-skipped-self-review.sh` has the matcher `code-writer`.
14. [verified: sub-agents.md, as relayed: settings, managed settings and plugin hooks still apply inside subagents] Both arms' subagents run the account's hooks.
15. [verified: sub-agents.md, as relayed] `omitClaudeMd` is a subagent frontmatter field (v2.1.271 and later) that drops user, project and local `CLAUDE.md`. Managed policy still loads.
16. [verified: Verification step 1a's `bare` run, as relayed; the sub-agents page, as relayed: "For Explore, Plan, and subagents with `omitClaudeMd`, a subdirectory's `CLAUDE.md` and matching path-scoped rules still load when the subagent works with files there."] `omitClaudeMd` does not stop path-scoped rules: the `bare` subagent loaded a user-scope rule by `path_glob_match`. [verified: `claude/.claude/rules/shell-script-conventions.md:49-52`] A leaked rule is not neutral, because one user rule sets a comment-length convention. So every `bare` fixture carries `claudeMdExcludes` (row 115), and the instrument still checks every `bare` run.
17. [verified: `docs/case-studies/claude-md-glob-zero-segment.md`, its five `-p` trials and the clean trial's two logged lines] An `InstructionsLoaded` command hook fires under `claude -p` and logs `load_reason` and `file_path`.
18. [verified: Verification step 1a's `toolkit` run, as relayed] The hook logs a subagent's lazy loads, such as a `path_glob_match` rule load, but no line for the `CLAUDE.md` a subagent inherits. The `toolkit` subagent's reply quoted text found only in the account `CLAUDE.md`, and the log held no subagent `CLAUDE.md` line. So no per-run check can show a subagent's `CLAUDE.md`, and `smoke`'s block calibration checks inheritance instead.
19. [verified: Verification step 1a, as relayed] `--settings` adds the instrument hook to the account's hooks rather than replacing them: a `sessions/` file held the run's session ID, and the `toolkit` run's `SubagentStop` evaluator sent the writer back. Every run still checks it through a known account hook's side effect (row 54).
20. [verified: Verification step 1a, as relayed: `direnv exec <dir> pwd` run from `/tmp` printed `/tmp`] `direnv exec DIR CMD` runs CMD in the caller's working directory on this machine. The preflight probe still checks it for each repo and refuses otherwise, since another machine's direnv could differ.
21. [verified: `README.md` "Worktree enforcement", lines 341-347; `claude/.claude/hooks/require-worktree-for-file-writes.sh:7-8`] Machine-level worktree enforcement denies Edit and Write in a repo's main tree unless `.claude/worktree-optout` is present. [verified: `claude/.claude/hooks/require-worktree-for-file-writes.sh:6`] The opt-out loses only to a committed `.claude/worktree-required`. [verified: `claude/.claude/hooks/require-worktree-for-git-writes.sh:5-8`] The opt-out also exempts the fixture from the git-writes gate, which reads the same markers. By this plan's design, the prune removes a committed `.claude/worktree-required` with every `.claude` directory (Fixture).
22. [verified: `claude/.claude/hooks/require-plan-review.sh:13-15`] The plan-review gate passes a repo with no `.claude/plans/`, which the prune also removes.
23. [verified: `claude/.claude/hooks/tests/test_claude_md_excludes.py:1-21`] In a `claude-config` checkout, nested discovery loads `claude/.claude/CLAUDE.md` and the stow-source rules under `claude/.claude/rules/`. This repo suppresses them only through its root `.claude/settings.json`, which the prune removes, so the prune must remove the nested files itself.
24. [unverified] Nested `.claude/agents/` and `.claude/skills/` directories can be discovered and could shadow the account's agents. The prune removes every `.claude` directory, so the question never arises.
25. [verified: `CLAUDE.md` §Working Style "Dispatching cannot clear a denial your child inherits"] A subagent starts in its dispatcher's permission mode, so `--permission-mode acceptEdits` reaches both arms' subagents.
26. [verified: Verification step 1a, as relayed; the permission-modes page, as relayed] Under `-p`, `acceptEdits` approves Edit and Write inside the working directory. It also auto-approves `mkdir`, `touch`, `rm`, `rmdir`, `mv`, `cp` and `sed` there, plus the read-only command set. Other Bash needs an allow rule. Step 1a saw `env` denied, and `code-writer`'s Reads of reviewer files outside the fixture denied.
27. [verified: `evals/review_bench/fixture_repo.py:129-133`] Extracting with `git archive` into a fresh repository keeps every later commit unreachable from the fixture.
28. [verified: `evals/review_bench/fixture_repo.py:267-284`] `build_two_commit_repo` commits the head tree, so it cannot build a replay fixture.
29. [verified: `evals/review_bench/fixture_repo.py:8-9`; `evals/README.md` "Building a later arm"] The bench installs an arm's agent as a fixture-local `.claude/agents/` file.
30. [verified: `evals/review_bench/runner.py:852-991`] `evaluate_run_validity` checks any thin-dispatcher run, and its live-checkout check covers the changed paths passed to it. Its path checks see only Read, Grep and Glob calls. [verified: `evals/review_bench/runner.py:100`]
31. [unverified] `code-writer`'s `model: sonnet` resolves to `REVIEWER_MODEL_ID`, the `--model-id` default. If it does not, every run of either arm fails `model-mismatch` at `smoke`. Verification step 1b reads the model ID each subagent observed and stops when it differs.
32. [unverified] The full-config dispatcher relays the inner prompt verbatim often enough. `smoke`'s `prompt-mismatch` count measures it.
33. [verified: `evals/README.md:353-355`] A prompt reaches `claude -p` as one argv element and should stay in the low single-digit KB.
34. [unverified] A commit message is a usable brief. The engineer reads the `tasks` output before any spend, and the brief is identical across arms.
35. [unverified] A classifier error that both arms share cancels out of the paired difference. One arm's style falling into a blind spot more often, such as trailing comments outside Python, does not cancel, so `summarize` shows the Python trailing-comment count as a check.
36. [verified: `evals/review_bench/mine_szz.py:112-141`] `_python_docstring_lines` returns the 1-based line numbers of module, class and function docstrings, leaving out a first or last line shared with code.
37. [verified: `evals/review_bench/runner.py:68-78`] The per-run budget cap and timeout were set for reviewer dispatches. [unverified] Whether a replay fits within them is unmeasured, so `smoke` prints each run's cost and wall clock.
38. [verified: `claude/.claude/agents/comment-discipline-reviewer.md:77-83`] That reviewer's Comment verbosity angle counts separate sentences that each carry a distinct fact as not verbosity. A word or line count therefore measures comment volume and shape, not verbosity as that lens judges it.
39. [verified: `claude/.claude/scripts/select-tests.py:608-617,634,825,829`; `claude/.claude/scripts/tests/test_select_tests.py:690-796,817-856`] The review-bench rows' target tuples carry each sibling eval script's test, with `measure_explore_recall.py` as the precedent. The selection tests assert that set by equality, and the import-closure test's seed list needs each sibling eval script.
40. [verified: `claude/.claude/scripts/select-tests.py:470-474`] A change to `select-tests.py` itself selects the full suite.
41. [verified: `.gitignore:43-48`] Only `evals/review_bench/.local/` is ignored today.
42. [verified: `evals/run_review_bench.py:2284-2309,2327-2331`] The bench CLI sets an owner-only umask and restricts an existing `.local/` to 0700 with `_restrict_local_dir_to_owner`.
43. [verified: `evals/review_bench/runner.py:1150-1291`] `RunStore` supplies a flock, a write-ahead log, block completion and a validated sweep. The sweep deletes only `FIXTURE_DIR_PREFIX` directories directly under the system temp dir, and session stores directly under the projects root it is given.
44. [unverified] `/usr/bin/env -i` replaces itself with `claude` rather than forking it, so `msmr._run_claude_to_completion`'s group kill reaches the child. A Phase 4 test with a fake `claude` behind the prefix checks it.
45. [verified: `evals/README.md:1163-1164`] The bench runs only on repositories whose branches the engineer or someone the engineer trusts wrote. The same bar holds here, because each subagent holds Bash under the account's allowlist in a tree from the source repo. [verified: `evals/README.md:1146-1151`] Run validity fails a steered run only after its commands ran. So the residual risk is plain: a steered replay agent can read or write anything the account's permission rules allow. This plan accepts that risk under row 47.
46. [verified: `evals/review_bench/mine_szz.py:29`] This repository's `main` is squash-merged, so its first-parent commits are its merged pull requests.
47. [engineer-verified: "drop the ones under \"drop if you agree\". No worries about steered agents here."] Sandbox containment is not adopted, and steered agents are out of scope for this runner.
48. [verified: `docs/case-studies/claude-code-sandbox-write-boundary.md:284-292`] A `tool_result` carrying `tool_result_meta[].non_execution_kind` of `"permission-rule"` marks a call that never ran, whether a hook or the permission flow denied it.
49. [verified: `evals/review_bench/local_git.py:1-7,29-30`] `isolated_git_environment` leaves a repository's own `.git/config`, `.git/info/attributes` and tracked `.gitattributes` in effect.
50. [unverified] With `--git-dir` naming a runner-owned copy, a work-tree `.gitattributes` can name a filter or diff driver but cannot define one, so `add` and `diff` run no command. Phase 4's hostile-config test checks it.
51. [verified: `evals/measure_subagent_model_resolution.py:720-726`] `_run_claude_to_completion` passes the runner's environment minus git-local variables and `CLAUDECODE`, and takes no environment argument.
52. [verified: `docs/case-studies/claude-code-sandbox-write-boundary.md:230-236`] Even sandboxed Bash inherits the session's process environment by default, credentials included. So the subagent's Bash, which this plan does not sandbox, sees every variable the launch passes.
53. [verified: `claude/.claude/hooks/enforce-marker-script-shape.sh:83-87`, quoting the hooks reference's common input fields] A hook's `agent_id` field is present only when the hook fires inside a subagent call. [verified: Verification step 1a, as relayed] `InstructionsLoaded` lines carry `session_id` on every line and `agent_id` on subagent lines only.
54. [verified: `claude/.claude/hooks/capture-session-id.sh:26-27,96`; `claude/.claude/settings.json:128-133,212-217`] The account's `capture-session-id.sh` runs on SessionStart and SubagentStart. It writes `<config-dir>/sessions/<claude-pid>`, whose first line is the session ID.
55. [verified: `claude/.claude/agents/code-writer.md:109-110`] `code-writer` reads reviewer files from `~/.claude/agents/` with the Read tool.
56. [verified: `evals/README.md:1010-1017`] A killed runner's `claude -p` children can keep running. The bench's guidance is to wait at least the command's timeout before resuming.
57. [verified: `evals/review_bench/runner.py:1194-1203,1285`] `record_directory` with an empty session ID logs a directory before any launch, and the sweep then deletes that directory alone.
58. [verified: `evals/review_bench/runner.py:1048-1061`] `_read_environment_field`'s error text carries the command and the child's stderr.
59. [verified: `evals/review_bench/runner.py:1298`] `remove_tree_reporting_failure` calls `shutil.rmtree` with `onexc`. [unverified: Python docs not read this session] `onexc` first appears in Python 3.12, so an older interpreter raises at the first post-capture delete, after a paid run.
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
67. [verified: `claude/.claude/scripts/tests/test_select_tests.py:1575-1596,2097,2416-2418`] The `CONFIG_DIR_MODULE` test asserts its target set by equality, `REVIEW_BENCH_TEST_GLOB` included. `_EXACT_MATCH_LITERAL_PATH_CONSTANTS` lists the exact-match path constants, and the review bench's directory constants have an existence test.
68. [engineer-verified: "Proceed anyway"] Superseded by row 84. The engineer chose to keep an earlier revision of this plan as one PR at its `Projected diff:` of about 3,800 changed lines, over the 400-line threshold.
69. [verified: `evals/measure_subagent_model_resolution.py:703-726,763`] `_run_claude_to_completion` sends the child's stderr to the null device and returns only its stdout lines and a timed-out flag, with no exit status.
70. [unverified] The allowlist holds every variable a launch needs. An account whose `.envrc` sets a provider-selecting variable besides `CLAUDE_CONFIG_DIR` loses it at launch. That launch fails when the config dir holds no login, and otherwise authenticates through the config dir's own login instead of the `.envrc`'s route, which `smoke` cannot tell apart. `preflight` therefore names every variable an `.envrc` set. `claude --version` shows only that the CLI resolves and starts, and whether the installed CLI has a login-status command is unchecked. So `smoke` is the first check that authenticates each account.
71. [verified: `evals/README.md:499`; `evals/review_bench/local_git.py:23-24`] `isolated_git_environment` turns off the global git config through `GIT_CONFIG_GLOBAL`, which git older than 2.32 ignores.
72. [verified: `evals/review_bench/runner.py:1041-1045`] `_ENVIRONMENT_READ_TIMEOUT_S` is 10 seconds, set for local process and git calls with no network I/O, a hung `claude --version` among them.
73. [verified: `evals/review_bench/runner.py:1090-1106`] An `EnvironmentReference` built from a frozen mapping of `cli_version` and `ambient_config_commit` checks every reading against it. Built without one, it adopts its first reading.
74. [verified: `evals/review_bench/runner.py:1592-1594`] `all_runs_missing` is true only when none of the records it is given is ok.
75. [verified: `evals/review_bench/analysis.py:361-370`] `bootstrap_interval` returns no interval for fewer than two clusters.
76. [unverified] One `printf '%s\n'` of the captured payload reaches the log in one `write` call, which Phase 4's parallel test checks. [verified: Verification step 1a, as relayed] The session-start burst arrived one payload per line. Whether the harness ever runs two `InstructionsLoaded` hooks at once stays unchecked.
77. [unverified] `git add -A` stores a symlink as its link text and skips a FIFO without opening it. Phase 4's hostile-tree test checks both.
78. [unverified] `git add -A` leaves out an untracked path that the work tree's ignore rules match, and fails on a nested repository that has no commit. Phase 4's tests pin both outcomes.
79. [verified: `claude/.claude/hooks/send-back-skipped-self-review.sh:40-41`; `docs/session-config-keys-log.md:3`] A replay session adds to account files that already exist. `toolkit`'s `SubagentStop` hook writes `.code-writer-self-review.log` and `.code-writer-self-review.d` in the config dir. Every session start adds a row to `.session-config-keys-log.jsonl`.
80. Superseded by row 158. [verified: `claude/.claude/scripts/select-tests.py:812-832,876-879`; `claude/.claude/scripts/tests/test_select_tests.py:425-431,1092-1105,2138-2149`] A test that reads the real agents directory through a production function is a hand-audited `AGENTS_DIR` reader. `MEASURE_SUBAGENT_MODEL_RESOLUTION_TEST` carries the wiring one needs:
    - a target in the `AGENTS_DIR` row;
    - an entry in `_HAND_AUDITED_AGENTS_DIR_READERS`;
    - a `_FILE_TARGETS` entry;
    - a standalone row that selects `test_select_tests.py` when the reader changes.
81. [verified: `claude/.claude/scripts/tests/test_select_tests.py:842-845`] The import-closure test maps every relative `ImportFrom` to a `review_bench.` module, so a relative import in the replay package would resolve to the wrong module with no failure.
82. [verified: `evals/run_skill_evals.py:64`] `DEFAULT_WORKERS` is 4.
83. [unverified: Python docs not read this session] With `strict` false, `os.path.realpath` resolves a symlink loop as far as it can and does not raise. The prune's realpath check meets loops, and Phase 2's loop case checks it.
84. [engineer-verified: "Proceed anyway"] Superseded by row 99. The engineer chose to keep an earlier revision of this plan as one PR at its `Projected diff:` of about 5,100 changed lines, over the 400-line threshold.
85. [verified: `evals/review_bench/runner.py:1141,1214-1230,1264-1291`] `sweep_abandoned` returns the whole store's pending entries, whichever projects root it is given. An entry with an empty session ID is a directory logged before any run launched against it.
86. [verified: `evals/review_bench/runner.py:280-313`] `_repair_torn_tail` truncates a JSONL file's unparseable last line back to the last newline, and adds the missing newline to a last line that parses. The bench calls it before a log's next read or append.
87. [verified: `evals/review_bench/fixture_repo.py:37-41,88-95,121-131,249-259`] `_run_git`, `_extract_commit_tree` and `_commit_snapshot` bound every git call by the module's 10 s `_LOCAL_GIT_TIMEOUT_S`, which its comment calls an empirical guess. `_extract_commit_tree` holds the whole archive in memory, and `_commit_snapshot` runs `git add -A --force` over the whole tree.
88. [verified: Verification step 1a's timing, as relayed] The Fixture rule applied to the measured builds gives the defaults Fixture lists. [unverified] Three times the slowest build bounds a hung git without failing a slow but healthy build. The multiplier and the 60 s ceiling are judgments.
89. [verified: `pyproject.toml:31`; `.github/workflows/tests.yml:26-34,179,186`; `evals/test_review_bench_runner.py:793,810`] CI runs the tests not marked `timing` under `-n auto` and the `timing` tests serially, inside a 25-minute job cap whose comment says to re-derive it whenever either pass grows. The marker is for a test that asserts a wall-clock bound or needs serial isolation.
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
104. [unverified] The session's extension, not the engineer's choice: the skip also covers the non-link entries extraction refuses, an entry with a `.git` path component and one under a root `.bench`, because each fails every attempt as `fixture-error` the same way a refused link does.
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
121. [verified: the hooks page, as relayed: `InstructionsLoaded` "runs asynchronously for observability purposes", and cannot block] A log line can land after the session's own files stop growing. [unverified] A hook already running when `claude -p` exits still finishes its write. A load fires on a tool call, and a tool result always goes back to the model before a session ends, so a lost line would need a hook slower than one model turn. The runner reads the log only once its size holds across two polls. No probe tests the premise (row 134). The plan and the README state the residual: losing a hook line needs an append slower than the model turn that follows its load.
122. [verified: the permission-modes page, as relayed; Verification step 1a, as relayed] Both arms meet the same permission rules, so a Bash command outside `acceptEdits`' set and the account's allow rules, such as a test runner, is denied in either arm. How often each arm meets a denial can differ, and the permission-denial count records it. The README states the caveat.
123. [unverified] Superseded by row 139. It held the premise that a rule matches a file of the calibration pair's synthetic fixture, which row 136 removed.
124. [engineer-verified: "I accept 2 to 4, and I accept your recommendation on splitting."] The engineer accepted the session's items 2, 3 and 4, and the session's recommendation to split the work into two PRs.
125. [unverified] The session's numbering and descriptions, not the engineer's words:
    - item 1 was the projected-diff size, about 7,680 lines, as one PR or as a split at Phase 2 to Phase 3;
    - item 2 was the Read grant over every `agents/*.md` and `skills/<name>/SKILL.md` under the config dir and `~/.claude`, by link path and realpath, with no wildcards, in both arms;
    - item 3 was Verification step 1b, three paid runs before Phase 3;
    - item 4 was the stricter `bare` check, which fails on any dispatcher load other than the account's `CLAUDE.md`.
126. [unverified] The session's recommendation, not the engineer's words: split at the Phase 2 to Phase 3 boundary, with about 1,830 lines in the first PR. That figure was this plan's estimate. [verified: line counts of Phase 2's output before the first PR's last dispatch, taken by an earlier dispatch of this plan's revision] Phases 1 and 2 hold 2,592 lines in their five new files, so the first PR's `Projected diff:` is larger than the figure the recommendation carried.
127. [verified: repo `CLAUDE.md` "a plan committed under `.claude/plans/` ships in the same PR as the implementation"; this branch's commit 9239b6f1 "Plan replay_bench docstring-verbosity experiment", from the dispatch's git status] A plan ships with the code it governs, and this file is already committed on the first PR's branch.
128. [verified: `claude/.claude/hooks/require-plan-review.sh:4-16`] The plan-review gate blocks Write and Edit while a plan file under `.claude/plans/` is uncommitted or modified and no review marker covers its exact state. A committed, unmodified plan passes. So each edit to this file, on either branch, needs a fresh `/plan-review` before the next code edit.
129. [engineer-verified: "Accept the new sizes (Recommended)"] Superseded by row 141 for its first, second and fourth items, the sizes and the refactor's move. Its third item stands. The engineer selected this label when asked whether to accept four items. The label covers acceptance of those sizes and that scope shift, nothing more. The option's description was the session's proposal. The items:
    - the first PR's `Projected diff:` of about 2,750 lines, where row 126 had carried about 1,830;
    - the two PRs' combined about 8,650 lines;
    - the `--max-base-entries` default change from 100,000 to 12,500;
    - the `write_fixture_extras` refactor's move to the second PR.
130. [verified: the code-writer's run of Phase 2's test on Apple Git 2.50.1, as it reported] On Apple Git 2.50.1 a real `--filter=blob:none` clone sets `remote.<name>.promisor=true` and no `extensions.partialclone`. The refusal therefore reads `git config --get-regexp '^(extensions\.partialclone|remote\..*\.promisor)$'` and refuses on any output. [unverified: the git partial-clone documentation, which a reviewer cites and this session did not read] A partial clone records `extensions.partialClone` in its config. Git fetches a missing object on demand from `cat-file`, `diff` and `archive`. Phase 2's test (Phase 2) holds the clone recipe.
131. [verified: `evals/review_bench/local_git.py:4-5,29-30`] `isolated_git_environment` leaves a repository's own `.git/info/attributes` and tracked `.gitattributes` in effect. [unverified: the gitattributes and git-archive documentation, which a reviewer cites and this session did not read] `git archive` drops an `export-ignore` path and rewrites an `export-subst` file's bytes. Phase 2's live-git case archives a tree whose `.gitattributes` says `tests export-ignore` and checks that `tests/` is absent.
132. [unverified] `claude --version` prints a dotted `<major>.<minor>.<patch>` version first, which the readiness check parses for its floor. Verification step 1b records the printed line, and a version the check cannot parse refuses the account.
133. [verified: `evals/replay_bench/tasks.py:26`; `evals/review_bench/runner.py:41-42,60`; `claude/.claude/scripts/select-tests.py:80-82,608-617`] The first PR's `tasks.py` imports `review_bench.runner`, which imports `measure_subagent_model_resolution`, `run_skill_evals` and `_config_dir`, so the first PR's closure already reaches every row Phase 5a names. `_is_review_bench_change`'s predicate covers `EVALS_CONFTEST`, whose comment lists the tests it applies to as an exact set.
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
    - [verified: `claude/.claude/settings.json:212-231`] both get the same `SubagentStart` hooks, since the one with a matcher names `plan-architect`;
    - [verified: `claude/.claude/agents/code-writer.md:1-7`] `code-writer`'s frontmatter preloads no skill, and `bare-writer` copies its tools, model and effort (Arms);
    - both carry the same brief and inner prompt.
139. [verified: `claude/.claude/rules/*.md` frontmatter] Superseded by row 145, under row 143's choice. This repository's rules match `**/*.py`, `**/*.sh`, `**/*.sql`, `**/*.tf` and `**/Dockerfile`, among other globs. [unverified] A subagent's Read of such a file loads the rule. `smoke`'s `--rule-path` names one such changed path present in the base tree.
    - A run that never reads it fails `control-unexercised` or `exclude-unexercised`, not `instrument-missing`, so a bad pick is told apart from a silent hook.
    - A `toolkit` run that read it and logged no rule load fails `instrument-missing`.
    - An account whose history keeps no task with a rule-matching changed path needs a larger `--max-count`, or a task set from another source repo on the same account.
    - A residual: the engineer's pick could match no rule while the `toolkit` subagent loads a rule from another file, and then `bare`'s clean read of the pick shows nothing.
140. [unverified] Superseded by row 145, under row 143's choice. With the rule-path read required of both runs, the `bare` smoke run shows the exclude holding for that path. In a campaign, a broken exclude voids only the `bare` runs that touch a rule-matching file, so the leak stop halts the campaign at the first `arm-leak` attempt, a retried one included, rather than letting the per-arm stop wait for a block with no valid `bare` run.
141. [engineer-verified: "Accept the trimmed sizes (Recommended)"] Superseded by row 163 for the second PR's figure. The engineer selected this label when asked whether to accept the trimmed plan's sizes, about 2,890 lines for the first PR, about 6,300 for the second and about 9,190 together, and the dropped `write_fixture_extras` refactor. The label covers acceptance of those figures and that drop, nothing more. The option's description was the session's proposal.
142. [verified: a reviewer's read-only check of this machine, relayed by the dispatch] Here `~/.claude/agents` and `~/.claude/skills`, and each other account's, are directory links into the one config checkout, so every granted realpath is an in-tree file. [unverified] That holds only on a folded stow install. A consumer whose `agents/` or `skills/` already existed as a real directory gets per-file links beside their own files, which the grant then covers too.
143. [engineer-verified: "Take the lighter fix (Recommended)"] Asked whether to keep the round-2 fix for the vacuous `bare` smoke check or take a lighter one, the engineer chose the lighter fix. The label covers that choice, nothing more.
144. [unverified] Superseded by row 160 for the campaign leak stop at the first `arm-leak`, which `run`'s instrument halt provides. The session's proposal, not the engineer's words: keep only a printed note at smoke time when the `bare` run read no rule-matching file, state the residual in the ledger and the README, and rely on the per-run `arm-leak` check on later `bare` runs. The round-2 fix it replaced was a required `smoke --rule-path`, the `control-unexercised` and `exclude-unexercised` outcomes, a campaign leak stop at the first `arm-leak` and a missing-reason field on spend-file lines. The note's detection is this plan's design (Campaign).
145. [verified: `claude/.claude/rules/*.md` frontmatter] This repository's rules match `**/*.py`, `**/*.sh`, `**/*.sql`, `**/*.tf` and `**/Dockerfile`, among other globs. [unverified] A subagent's Read of such a file loads the rule. The residuals the lighter fix accepts:
    - a `smoke` task whose `toolkit` run reads no rule-matching file fails `instrument-missing`, the reason a silent hook also gives, and the engineer tells them apart from the printed loads and the `toolkit` transcript, then picks another task;
    - an account whose history keeps no task with a rule-matching changed path needs a larger `--max-count`, or a task set from another source repo on the same account;
    - a `bare` smoke run that read no rule-matching file passes without testing the exclude, and only the printed note shows it;
    - in a campaign, a broken exclude fails `arm-leak` only on the `bare` runs that read a rule-matching file. The first such attempt halts `run` at its block's end (row 160), so a broken exclude goes unseen only while no `bare` run reads a rule-matching file.
146. [unverified] A `path_glob_match` payload names the file that triggered the load. The relayed hooks-page excerpt names no such field. Verification step 1b records whether the probe's lines carry one and under what key. Without one, `smoke` prints only the residual sentence.
147. [engineer-verified: "Add file to Critical files"] Asked whether to keep the code-review round 5 move of the shared-default timeout case and the `build_single_commit_repo` cases into `evals/test_review_bench_fixtures.py`, which Critical files had not listed, the engineer selected this label. Critical files now lists the file.
148. [engineer-verified: "Fix docs and log pin (Recommended)"] Asked how to treat the code-review round 9 findings, one of which needs `evals/review_bench/local_git.py` (not in Critical files), the engineer selected this label. Critical files now lists the file for the one docstring sentence only. [unverified] The option description was the session's proposal: it also covered the comment fixes and `--no-show-signature` on the two `git log` calls.
149. [engineer-verified: "Keep it (Recommended)"] Asked whether the second PR's `tasks` CLI should exit 2, naming the flag, for zero, negative or inverted selection limits, which the Ask does not call for, the engineer selected this label. Row 150 records where the guard lives and which values it refuses.
    - [verified: `evals/replay_bench/tasks.py:108-120`] `SelectionLimits` is a plain frozen dataclass with no validation, so it accepts those values today.
    - [unverified] The option description was the session's proposal: a guard at the one place a person types the limits. Row 150's placement replaces it.
150. [engineer-verified: "In SelectionLimits (Recommended)"] Asked where the guard lives, the engineer selected this label. The guard and what it refuses:
    - `SelectionLimits.__post_init__` raises `TaskSourceError`, which `main()` already maps to exit 2.
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
152. [unverified] The session's design, not the engineer's words: `tasks`' flags for the nine `SelectionLimits` fields, `--rev` included, default to `None`, and `main()` passes only the flags given to `SelectionLimits`, by field name. `SelectionLimits` then holds the only defaults, and no flag can reach another field. The CLI case that gives all nine flags distinct values pins the wiring.
153. [unverified] The session's design under row 159, not the engineer's words:
    - A task-specific failed block, one where an arm has no valid run and no attempt in the block ended under an instrument reason, is marked complete and never rerun. Its task leaves the pairs, and `summarize` counts it.
    - Two such blocks in a row halt `run` with both complete. The count lives in memory for one invocation and starts at 0 on every start and resume, so a resume goes on past the pair, and those two tasks are lost from the sample. A one-block transient failure also loses its task for good.
    - A persistent failure meets the pre-block spend stops and the two-in-a-row halt, so it costs at most one block beyond the first.
    - Two residuals stay, which the README and Out of scope also name:
      - [unverified: how `claude -p` behaves at a usage limit] a usage-limit, auth or overload event that fails every run of two blocks marks two tasks that no agent really tried complete, and drops both from the sample;
      - failures on alternating tasks never make two in a row, so only `--cumulative-stop-usd` bounds them.
    - An instrument failure halts at its block's end with the block complete (row 160), so it never blocks a resume.
154. [verified: `claude/.claude/hooks/capture-session-id.sh:96`; `claude/.claude/hooks/_lib.sh:3695-3706`; a search of `evals/` Python files, which name no `sessions/` path, and of `claude/.claude/` outside its tests, which found no code that deletes a `sessions/` entry] `capture-session-id.sh` rewrites `<config-dir>/sessions/<claude-pid>` at each session start. `_lib_registered_live_sid` reads an entry as absent when its recorded start time does not match the live process. Nothing deletes an interactive session's or a review-bench launch's entry. So each replay attempt leaves one file there too, as accepted residue beside row 79's files.
    - [verified: `claude/.claude/scripts/post-crash-sessions.py:18,127,158,161`] `post-crash-sessions.py` reads these files as its source D, and its crash-evidence window is 14,400 seconds.
    - [unverified: a reviewer's read of `post-crash-sessions.py:1347-1393`] A leftover replay file, whose PID is dead and whose session store the cleanup deleted, lists there as `unknown` while its mtime is inside that window. It lists as `confirmed-clean-exit` instead if the SessionEnd hook fires under `claude -p`, which is unverified.
    - So for four hours after a campaign, the engineer may see replay attempts listed in that report, as accepted residue.
    - [unverified] No other reader under `claude-skills/` or a script outside `claude/.claude/` minds a stale entry, since the search did not cover them.
155. [unverified] The plan's design, not a measurement:
    - `main()` routes SIGHUP and SIGTERM into `KeyboardInterrupt`, and an aborted launch is charged at the cap (Retry). A Phase 4 case checks that an interrupted in-flight attempt writes its charge.
    - A hard kill, such as SIGKILL, an out-of-memory kill or a power loss, can end a launched attempt with no spend line. So can a failed append, which halts `run` before its block is marked complete. Either way the attempt's entry stays pending, and the next resume counts it among the uncharged attempts it prints.
    - After each hard kill, up to `run_skill_evals.DEFAULT_WORKERS` (4, row 82) launched attempts, one per worker thread, may go uncharged. The undercount adds up over kills, each adding at most `DEFAULT_WORKERS` times the per-run cap, a nominal figure (row 65).
    - The README states this as accepted residue, beside row 79's files.
156. [verified: `evals/review_bench/runner.py:1128,1168-1177,1636-1638`] The store's lock is `lock.pid`, taken with a non-blocking exclusive `flock`, and the review bench takes it before its sweep. The replay resume takes it before its sweep and its `attempts/` delete (Campaign), so no attempt of the campaign runs then. [verified: `evals/review_bench/runner.py:1294-1300`] `remove_tree_reporting_failure` runs `shutil.rmtree` with an `onexc` handler and prints each failure, so the resume checks for a symlinked or an absent `attempts/` itself before it calls the helper.
157. [unverified] The plan's design: each run's record keeps its metrics, first-turn prompt size and diff, each task keeps its base density, and each spend line keeps its cap-charge flag. So a later change can compute any figure `summarize` leaves out, Out of scope's list included, with no rerun.
158. [unverified] The session's design, not the engineer's words: `evals/test_replay_bench_arms.py` builds `bare-writer.md` from a `tmp_path` `code-writer.md`, so it reads no real agents directory and needs none of row 80's wiring. That file uses distinctive non-default `tools`, `model` and `effort` values and a multi-line `description`, so a hard-coded value or a reader that handles only one-line values fails. Preflight still refuses, unpaid, an account `code-writer.md` that yields no tools, model or effort (Account preflight). No test catches a change to the real file's frontmatter that the generator reads into a wrong but present value.
159. [engineer-verified: "Mark complete, halt after two in a row (Recommended)"] Asked how the per-arm stop treats a failed block, in place of holding it, the engineer selected this label and "Halt at the first block (Recommended)". The labels cover those selections, nothing more. [unverified] The option descriptions were the session's proposals, not the engineer's words. By the session's relay, the first label covers task-specific failed blocks and the second covers instrument failures. Row 153 holds the design that carries them out.
160. [unverified] The design for an instrument failure under row 159's "Halt at the first block (Recommended)", from a second architect consult the session relayed, not the engineer's words:
    - It triggers on any attempt that ends under an instrument reason. Neither `run` nor `smoke` retries such an attempt, because a clean retry would hide the evidence (Retry).
    - `run` halts at the block's end, after the block's other runs finish and the block is marked complete with the failing runs recorded missing. No resume reruns it, so each instrument halt costs at most one task and never blocks a resume.
    - The way forward is to fix the cause and resume. Because the halt comes at the first `arm-leak` attempt, a broken `bare` exclude shows on the first `bare` run that reads a rule-matching file (row 145).
161. [verified: `evals/replay_bench/checks.py`, committed as `681edfb5`; 263 passing tests] Phase 3 narrowed the Bash scan to the bounded contract under Approach, after a third-round architect consult found the lexical scan accreting defensive layers. Accepted residuals, which the README states:
    - heredoc and quoted-pattern false hits, such as `grep -rn "~/.claude"`;
    - `grep -r x ~` and `bash -c` text;
    - a denied Read, Grep or Glob call, since the runner's validity step has no notion of denial;
    - a Read of `<live>/.git` and of a linked worktree's common git dir;
    - the denial marker was verified on one CLI build only.

    Phase 4 shows each failing token in the run record or `smoke`.
162. [unverified] The session's design: `block_calibration_verdict` reads only the `toolkit` log, so the "both logs" wording of earlier rows is vestigial. The forged-log invariant lives in Phase 4. The runner takes the attempt dir and the fixture dir. It derives the verdict from the attempt path only, so a log written into the fixture is never read. Phase 3's pure functions cannot show that.
163. [engineer-verified: "Proceed anyway (Recommended)"] Asked whether to proceed past the diff-size gate for the second PR as one PR after the architect recommended one PR, the engineer selected this label. The gate showed the measured Phase 3 size of 2,658 lines, not the 7,500 to 8,000 projection. The label covers proceeding as one PR past the gate, nothing more. [unverified] The projected final size of 7,500 to 8,000 lines is the architect's inference, and the engineer has not accepted that figure by name. Row 141's 6,300 stands as the last figure the engineer accepted by name until they do.
164. [verified: Verification step 1b, as relayed] Step 1b ran against model `claude-sonnet-5` and CLI `2.1.322`. It found a `SubagentStop` send-back shape, which Phase 4 reads. It left eight environment names unverified, and Phase 4 treats them as a residual the README states: `AI_AGENT`, `CLAUDE_EFFORT`, `CLAUDE_PID`, `COREPACK_ENABLE_AUTO_PIN`, `GIT_EDITOR`, `NoDefaultCurrentDirectoryInExePath`, `PYTEST_ADDOPTS` and `__CF_USER_TEXT_ENCODING`. No figure from step 1b is published (rows 9, 10).

**Mechanisms:**
- **M1. Task mining from first-parent commits.** Tasks states the selection rules, refusals, briefs and recorded fields. anchors: row6, row7, row9, row33, row34, row45, row46, row61, row64, row82, row92, row96, row100, row101, row102, row103, row104, row105, row106, row109, row130, row131.
- **M2. A single-commit archive fixture.** Fixture states its prune, opt-out, `.git` copy and git timeout. Lighter primitives checked:
  - (a) a `git worktree add` or `git clone` of the base commit is lighter, but both share or copy the object store, so the replayed commit stays reachable (row 27);
  - (b) a bench two-commit fixture fails the same way (row 28).
  - anchors: row21, row22, row23, row24, row27, row28, row83, row87, row88, row103, row106, row108.
- **M3. A thin headless dispatcher in front of one subagent in every arm.** This is heavier than one headless session per run. Lighter primitives checked:
  - (a) a headless main session per arm fails for the reasons in "Why both arms dispatch a subagent";
  - (b) `--append-system-prompt` carrying `code-writer`'s body into a main session delivers that body through a different channel than production's agent body, and the session still carries the Main-session rules.
  - anchors: root, row3, row8, row12, row13, row14.
- **M4. `bare-writer` as a fixture-local agent file that copies `code-writer`'s tools, model and effort.** `--agents` JSON is lighter, but `omitClaudeMd` is documented only as frontmatter. anchors: row12, row15, row29, row158.
- **M5. Per-repo account preflight and an allowlisted launch environment, as Account preflight and Launch environment state.** Lighter primitives checked:
  - (a) the runner's own account for every repo breaks row 5;
  - (b) a `direnv exec` launch prefix hands the agent the runner's whole environment with the `.envrc`'s exports on top (rows 51, 52).
  - anchors: G2, row5, row15, row20, row44, row51, row52, row58, row69, row70, row72, row93, row94, row98, row132, row134.
- **M6. The `InstructionsLoaded` hook log as each run's arm check.** Instruction-load check states how the log is written, read and attributed, and what fails each arm. Lighter primitives checked:
  - (a) the model's self-report is not mechanical and would contaminate the run;
  - (b) the subagent's first-turn input-token size cannot name which file loaded, and rules load mid-run on file reads, so it serves only `smoke`'s block calibration (M20);
  - (c) transcript `nested_memory` attachment records: this repository has observed them only in an interactive session's transcript (`claude/.claude/hooks/tests/test_claude_md_excludes.py:15-18`), and no source read says whether a `-p` run or a subagent transcript keeps them. [verified: `docs/session-config-keys-log.md:81`] Whether a `-p` run keeps even a hook attachment is untested.
  - anchors: row16, row17, row18, row19, row53, row76, row121.
- **M7. `evaluate_run_validity` unchanged, plus the runner's write-path, Bash-path and account-hook checks, and retry-then-missing.** anchors: row30, row31, row32, row48, row54, row55.
- **M8. Whole-line classification of `baseline.git` blobs by extension table, with Python through `tokenize` and the bench's docstring finder, a test-path rule, and word counts.** anchors: row35, row36, row38, row63, row77, row78.
- **M9. `RunStore` for the lock, write-ahead and sweep, with each fixture logged before it is filled, per-run atomic record files, cleanup of fixtures and attempt dirs on every exit path, and deletion of the campaign's whole `attempts/` on resume.** anchors: row43, row54, row56, row57, row79, row85, row154, row156.
- **M10. One block per task, with a pinned environment and spend guards.** Campaign and Spend file state the manifest, spend file, stops, per-arm stop, exit codes and smoke gate. anchors: G3, G4, row2, row37, row65, row73, row74, row85, row86, row95, row97, row153, row155, row159, row160.
- **M11. `summarize` to the terminal only, over complete blocks, with a pre-registered ratio-of-sums word outcome, a paired bootstrap, one docstring secondary outcome, diagnostics, and spend and block counts.** anchors: G3, row4, row9, row10, row62, row63, row75, row157.
- **M12. `--permission-mode acceptEdits` on the dispatcher, with its Bash denials recorded and stated rather than lifted.** anchors: row25, row26, row122.
- **M13. Arm C as a gated pair sharing one route.** anchors: row2, row11, row13.
- **M14. `select-tests.py` wiring that follows the `measure_explore_recall.py` precedent, plus the no-relative-import check with a scanned-set control, and a declared row that selects it after a replay edit.** anchors: row39, row40, row67, row81, row133, row158.
- **M15. Local-storage and startup guards, with the README's trust warning.** Phase 1's `.gitignore` entry, `main()`'s startup order and Local dir state them. anchors: row4, row41, row42, row45, row47, row59, row61, row66, row71, row91.
- **M16. A runner-owned copy of the fixture's pre-launch `.git` for every post-run git call.** Lighter primitives checked:
  - (a) `-c` overrides on the fixture's own `.git` form a denylist that misses any key it does not name;
  - (b) a Python diff of the work tree reimplements `git diff` and its symlink handling.
  - anchors: row49, row50.
- **M17. Test isolation and timing.** Phase 4's runner-test setup and Verification step 3 state the stubs, markers, deadlines and duration check. anchors: row89, row90, row107.
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

**Dispatch split:** `code-writer` dispatches run in order, one per phase. Each phase's output is the next one's input:
- the classifier feeds the task miner's metrics;
- the run checks parse the log and transcript shapes that Verification steps 1a and 1b capture;
- the runner composes all three;
- the test-selection wiring needs the final file names and imports.

The two PRs (Approach) divide the phases:
- **First PR, merged as #1452.** Phases 1 and 2 needed one more dispatch, after Verification step 1c. It holds Phase 2's amendments and Phase 5a, two disjoint file sets.
- **Second PR, the follow-on branch.** Verification step 1b runs first. Then come one dispatch each for Phase 3, Phase 4 and Phase 5b. Phase 4 stays one dispatch because the arms, launch settings, runner and CLI share the record, fixture and settings contracts.

The revisions since step 1a add no new file. They edit `evals/replay_bench/tasks.py` and `evals/test_replay_bench_tasks.py` in the second PR (Phase 4).

**Phase 1 — classifier and ignore rule (first PR).** Verify: `scripts/venv-run.sh pytest evals/test_replay_bench_comment_metrics.py`, then `git check-ignore -q evals/replay_bench/.local/tasks/probe.json`.
- `.gitignore`: ignore `evals/replay_bench/.local/`, under a one-line comment that it holds task files, run records and diffs carrying other repositories' commit text and code.
- `evals/replay_bench/__init__.py` (new): one docstring line naming the package.
- `evals/replay_bench/comment_metrics.py` (new):
  - the extension table and the test-path rule from Approach, as data;
  - added line numbers per path from a `-U0` diff:
    - a hunk header with no count means one line, and a count of zero adds none;
    - `tasks` runs the diff it hands to this parser with `--src-prefix=a/ --dst-prefix=b/`, so a repo-local `diff.noprefix` cannot change the path form;
  - each changed entry's new mode and blob from a `--raw -z` listing, with symlink and gitlink entries counted as non-file entries;
  - per-file classification of added lines from post-change bytes the caller supplies, decoded as UTF-8 with a BOM stripped, with word counts and the undecodable tally;
  - block grouping, and a run-level aggregate with `to_dict` and `from_dict`, the `ast.parse` rejection tally included.
  - It reuses `mine_szz._python_docstring_lines` and stdlib `tokenize`.
  - It imports by absolute `replay_bench.` and `review_bench.` module paths, as `review_bench` does.
- `evals/test_replay_bench_comment_metrics.py` (new), synthetic sources only. Diff inputs are real `git diff -U0` and `--raw -z` output from a `tmp_path` repository, not hand-typed hunks. Cases:
  - a table-driven test with one row per extension, whose rows name the known gaps (PHP `#`, SQL `/* */`, `tf` `//`) as pinned outcomes;
  - Python:
    - a docstring;
    - a one-line docstring on its own line, and one after `def f():` on the same line;
    - a whole-line comment;
    - a code line with a trailing comment, counted as code plus one trailing count;
    - a `#` inside a string;
    - a non-docstring multi-line string with `#`-leading interior lines, counted as code;
    - an added line inside an existing docstring whose opener is not in the diff;
    - a file that fails to tokenize;
    - a file `ast.parse` rejects while `tokenize` succeeds, tallied;
  - shell:
    - a shebang, counted as code;
    - a heredoc body with `#` lines, pinned as a known gap;
    - a `#` at line start inside a multi-line quoted string, pinned as a known gap;
    - `$#` and `${x#p}` mid-line, counted as code;
  - C family:
    - `//` and `///` lines;
    - a `/** */` block opened at line start across lines;
    - a `*/ code` closing line, counted as code;
    - a `/*` inside a string mid-line, counted as code;
    - a JS template literal with `/*` at line start, pinned as a known gap;
    - `//` in `.css`, counted as code;
  - other languages:
    - SQL `--` lines;
    - Markdown lines as prose, a fenced block with `#` lines included;
    - an unknown extension, unclassified;
  - decoding:
    - bytes that do not decode as UTF-8, whose added lines count as unclassified and set the undecodable tally;
    - a UTF-8 file with a BOM, classified as it would be without one;
    - a Chinese comment with no spaces, pinned as one word, a known gap;
  - words: delimiter removal, and tokens with no letter or digit;
  - the test-path rule: each segment and basename pattern, and the lookalikes `contest.py` and `latest/`;
  - consecutive added comment lines forming one block, and a gap splitting two blocks;
  - diff parsing:
    - an omitted count;
    - a zero count;
    - a pure deletion hunk;
    - `+++ /dev/null`;
    - a binary file;
    - `\ No newline at end of file`;
    - a path with spaces;
    - section text after `@@`;
    - a path git still quotes;
  - a `--raw -z` listing with a regular file, a symlink and a gitlink, the last two counted as non-file entries.

**Phase 2 — tasks and fixture (first PR).** Verify: `scripts/venv-run.sh pytest evals/test_replay_bench_tasks.py evals/test_review_bench_fixtures.py`.
- `evals/review_bench/fixture_repo.py`:
  - add a keyword-only `timeout_s` to `_run_git`, `_extract_commit_tree` and `_commit_snapshot`, defaulting to `_LOCAL_GIT_TIMEOUT_S` so every existing caller keeps its bound (row 87);
  - add `build_single_commit_repo(source_repo, commit, dest_dir, *, prune, timeout_s)`. It validates `commit` with `_validate_sha` and composes `_run_git(["init", "-q"], ...)`, `_extract_commit_tree`, `prune(dest_dir)` and `_commit_snapshot`, passing `timeout_s` to each;
  - add its directory kind to the module docstring's list. Make no other change.
- `evals/test_review_bench_fixtures.py`: add cases for `fixture_repo.py`, which belong beside that module's other tests (row 147). Make no other change. The cases:
  - the shared default, through a `subprocess.run` spy: `fixture_repo._run_git`, `_extract_commit_tree` and `_commit_snapshot`, each called with no `timeout_s`, pass `timeout=_LOCAL_GIT_TIMEOUT_S`, so every review-bench caller keeps its bound (row 87);
  - the `build_single_commit_repo` cases.
- `evals/review_bench/local_git.py`: correct the module docstring's sentence about the miners, which says their git calls do not use `isolated_git_environment`. Say that the review bench's `mine_szz`, `mine_pr_comments` and `mine_review_rounds` do not use it and that the replay miner's git calls do (row 148). The reason for the three is that a fetch there may need the engineer's credential configuration. Make no other change.
- `evals/replay_bench/tasks.py` (new):
  - `ReplayTask` with `to_dict` and `from_dict`;
  - task-set read and write through `defects.atomic_write_text`:
    - writing validates the task-set name;
    - reading validates each task's keys;
    - reading validates the type of each string, count and density field;
    - reading validates the ID grammar and SHAs;
    - reading validates that the ID equals the one `make_task_id` derives from `source_repo` and `commit`;
    - writing and reading each reject a duplicate task ID;
  - `TaskSourceError`, which `main()` turns into exit 2, raised naming the path or the repo and commit for:
    - a source repo that is not a top level, and an empty trusted author;
    - two `--source-repo` arguments resolving to one repository, the only source of duplicate task IDs;
    - one commit examined from more than one source repo, such as a linked worktree and its main checkout, or a clone and its origin. Their paths differ, so their task IDs differ and the duplicate-task-ID rejection above cannot see the repeat;
    - a per-commit git or parse failure during mining;
    - an author or committer identity holding a control character, which no honest email does;
  - a subject is never rejected for a control character. The `git log` formats are NUL-framed and parsed in field-count groups, so a subject cannot shift a field;
  - commit selection: the pruned-path drop, the trusted-author rule over raw identities, the delimiter and path-character skips, the size and entry-count bounds from one `git ls-tree -r -l -z` call, the `missing-object` skip, the skip of base trees the extraction refuses through `fixture_repo`'s own path helpers (row 102), and the brief, merge commits included;
  - the per-repo progress lines, and the skip-reason counts, identity counts and largest kept tasks that `tasks` prints;
  - the prune predicate and `prune_instruction_files(dest_dir)`, with its realpath check of every remaining symlink:
    - `is_pruned_path` applies the prune's per-entry rule to each path component;
    - `is_pruned_path` and the prune each count a last component named `.claude` as pruned only for a directory or link;
    - `is_pruned_path` counts a child under a pruned root directory as pruned, for example `.claude/x`, and `.mcp.json/x` when `.mcp.json` is a directory;
    - a parity test runs one shared row list through both;
    - a base tree holding a regular file named `.claude` at its root is not pruned, so `tasks` does not skip it, and `build_task_fixture` fails closed on it as a `fixture-error`;
  - `build_task_fixture(task, dest_dir, baseline_git_dir, *, bare_writer_markdown, timeout_s) -> str`, which runs in this order:
    - re-run the partial-clone and `info/attributes` refusals on `task.source_repo`, because a repo may have gained either property after mining, and raise `TaskSourceError`;
    - call `build_single_commit_repo` with that `timeout_s`;
    - write the opt-out, the `bare-writer` file and the exclude line;
    - copy `.git` to `baseline_git_dir`;
    - return the fixture commit's SHA from a `rev-parse HEAD` under `timeout_s`;
  - `build_inner_prompt(task)`, which renders the inner-prompt text from Approach;
  - reference metrics and base density, reading a commit's post-change files and its base files in one `git cat-file --batch` call each, with a null density when the base holds no non-test code line.
  - Every git call runs under `local_git.isolated_git_environment()`.
- `evals/replay_bench/tasks.py` amendments, in the first PR's last dispatch:
  - `SelectionLimits.max_base_entries` defaults to 12,500 (Fixture), under a one-line comment citing this plan's Fixture section as its basis. Any test that pins 100,000 changes with it;
  - the partial-clone refusal and the `info/attributes` refusal, each a `TaskSourceError` naming the path (Tasks);
  - the `export-attributes` skip, over the `.gitattributes` blobs the existing `git ls-tree` listing finds, read in the existing `git cat-file --batch` call (Tasks).
- `evals/test_replay_bench_tasks.py` (new). It builds every repository under `tmp_path`. Cases:
  - a qualifying commit;
  - a commit that also edits a nested `CLAUDE.md`, kept with that path out of its changed list and its metrics;
  - skipped commits, each counted under its own skip reason:
    - markdown-only;
    - binary;
    - oversized;
    - over the prompt cap;
    - the root commit;
    - an untrusted author;
    - a merge with an untrusted merged-commit author;
    - a brief holding an `END CHANGE DESCRIPTION` line;
    - a brief holding a `>>>` line;
    - a path holding a newline;
    - a path holding a comma;
    - a base tree over `--max-base-mb`;
    - a base tree over `--max-base-entries`;
    - a base tree holding a root `.claude` symlink;
    - a base tree holding a root `.Claude` symlink, matched case-insensitively;
    - a base tree holding a symlink at `.claude/` plus a non-ASCII name, read verbatim through `-z`;
    - a base tree holding a symlink with an absolute target;
    - a base tree holding a symlink whose target leaves the tree through `..`;
    - a base tree holding a symlink whose target has a `.git` path component;
    - a base tree holding a non-link entry with a `.git` path component, built with `git mktree` because git's index refuses that name;
    - a base tree holding an entry under a root `.bench/`;
    - a base tree holding an entry under a root `.Bench/`, matched case-insensitively;
    - a `git cat-file --batch` reply of `<oid> missing`, skipped as `missing-object`;
  - kept base trees:
    - one holding a nested `.claude` symlink and an in-tree relative symlink;
    - one holding a submodule gitlink, added with `git update-index --add --cacheinfo 160000,<sha>,vendor/sub`, whose recorded size excludes the gitlink and whose entry count includes it;
    - one holding a symlink in a non-ASCII directory, classified by the same rule as the same symlink in an ASCII one;
  - thresholds, each with one commit at the limit, kept, and one just past it, skipped:
    - `--min-code-lines`;
    - `--max-code-lines`;
    - `--max-files`;
    - `--max-prompt-chars`;
    - `--max-base-mb 1`, at exactly 1,000,000 blob bytes and at 1,000,001, which pins a megabyte as 10^6 bytes;
    - `--max-base-entries`;
  - a two-repo walk prints a progress line per repo, and names the largest kept task by size and the largest by entry count, each with its ID;
  - extraction parity: one table, run through both `tasks`' predicate and `fixture_repo._extract_archive` on the same synthetic archives, built with `test_review_bench_fixtures.py`'s `_tar_with_link` helpers by import. The two must agree on every row (row 109):
    - each of the five refused shapes, plus the `.Claude` and `.Bench` case variants;
    - the lookalikes `.github/`, `.gitignore`, `.gitattributes`, `.claude-plugin/`, `.benchmark/` and `docs/.bench/`, each kept;
    - a root-level link to `..`, refused;
    - a depth-one link to `..`, which is the tree root, kept;
    - a depth-one link to `../..`, refused;
    - the chain `a -> d/e/f/..` with `d/e/f -> ../..`, pinned in whichever direction the running interpreter's filter takes (rows 103, 108);
  - a kept base tree holding a nested `.claude` symlink and an in-tree relative symlink goes through `build_task_fixture`. It extracts, the nested link is unlinked, the relative link stays, and `git status --porcelain` is empty after the extras are written;
  - two source repos with `--max-tasks 2`, each keeping its own two newest qualifying commits;
  - source repos: a subdirectory of a repository, a non-repository path and a bare repository each raise `TaskSourceError` naming the path, and a linked worktree's top level is accepted;
  - trusted authors:
    - an empty trusted author raises `TaskSourceError`;
    - a source repo whose committed `.mailmap` maps an untrusted email onto a trusted one keeps that commit out, and the identity printout shows the raw email (row 92);
  - reference metrics and base density, each asserted as numbers:
    - a modified file with known comment words and code lines;
    - a base test file, counted in neither the density's numerator nor its denominator;
    - a commit of new files only, and one whose base files hold no code line, each kept with a null density and counted in the printout;
  - a `timeout_s` given to `build_task_fixture` reaches every git call it makes, through a `subprocess.run` spy;
  - a merge commit whose brief carries the merged subjects;
  - a task file with a non-hex SHA, and one missing a key, each refused;
  - `--name` holding `../`, refused;
  - prune matching, each name in its own case through `build_task_fixture`, and each asserted absent from the work tree and from `git ls-tree -r` of the fixture commit:
    - `CLAUDE.md`, and the variant `claude.md`;
    - `CLAUDE.local.md`, and the variant `claude.LOCAL.md`;
    - `AGENTS.md`, and the variant `agents.md`;
    - a root `.mcp.json`, and the variant `.MCP.JSON`;
    - `.Claude/`;
    - a nested `.claude/`, removed whole;
  - prune lookalikes kept: `src/rules/`, an `agents/` or `skills/` outside `.claude/`, and a nested `sub/.mcp.json`;
  - prune link safety:
    - a symlinked `CLAUDE.md` and a symlinked `.claude/skills`, each pointing at a `tmp_path` sentinel, lose the link and keep the sentinel;
    - a nested `.claude` that is itself a symlink to a sentinel dir is unlinked, and the sentinel stays;
    - the chain `a -> d/e/f/..` with `d/e/f -> ../..`, built with `os.symlink` in a `tmp_path` fixture rather than extracted, makes the prune raise, since `a` resolves to the fixture's parent;
    - a symlink loop inside the fixture passes the realpath check without raising (row 83);
    - a fixture-internal link to a directory holding a `CLAUDE.md` keeps the link and loses the file;
  - a fixture whose `git log --all` holds one commit and every pruned path is absent, whose `git status --porcelain` is empty after the extras are written, and whose `baseline.git` copy resolves the same commit.
- `evals/test_replay_bench_tasks.py` amendments, in the first PR's last dispatch. Cases:
  - a `--filter=blob:none` clone of a `tmp_path` repository over `file://`, with `uploadpack.allowFilter` set on the source, raises `TaskSourceError` naming the path, and a full clone of the same repository is accepted (row 130);
  - a source repo whose `info/attributes` holds `* export-ignore` raises `TaskSourceError` naming the path;
  - skipped as `export-attributes`: a root `.gitattributes` holding `tests export-ignore`, a nested `sub/.gitattributes` holding `*.txt export-subst`, and one holding only a commented `# export-ignore` line, pinned as the deliberate over-skip;
  - kept: a `.gitattributes` holding only `*.py text eol=lf`;
  - live git, pinning row 131: `git archive` of a tree whose `.gitattributes` says `tests export-ignore` lists no `tests/` path.

**Phase 3 — run checks (second PR; committed as `681edfb5`).** Verify: `scripts/venv-run.sh pytest evals/test_replay_bench_checks.py evals/test_replay_bench_checks_transcript.py`. The tests split across `evals/test_replay_bench_checks.py`, `evals/test_replay_bench_checks_transcript.py` and the helper-only `evals/test_replay_bench_checks_support.py`, each under the 1,000-line cap.
- `evals/replay_bench/checks.py` (new):
  - the hook-log parser with its field-shape checks, and the instruction-load check from Approach, driven by the arm's expected and failing dispatcher loads;
  - the subagent's first-turn prompt size from its transcript;
  - the exclude note from Campaign. Only when Verification step 1b finds a trigger field (row 146) does this file read the optional trigger-path field on a `path_glob_match` line and check whether a transcript shows a Read that ran of a trigger path, compared relative to each run's fixture. Without one, the note is the residual sentence alone, and neither that code nor its exclude-note cases below exist;
  - the block-calibration verdict from Campaign, a pure function of the `toolkit` run's parsed log (row 162), both runs' first-turn sizes, the account `CLAUDE.md`'s character count, the two agent bodies' character counts, and the character counts of the rule files the `toolkit` dispatcher loaded at `session_start`. It returns every failed condition in order;
  - the instrument reasons as one constant, which the runner's retry rule reads (Retry);
  - the account-hook check under `<config-dir>/sessions/`;
  - the write-path and Bash-path checks over the subagent transcript's `tool_use` entries, reusing `review_bench.runner`'s `live_checkout_leak_targets`, `is_changed_file_leak` and `is_config_dir_leak`;
  - the permission-denial count, and the record of Reads of Read-allow-list paths that ran or were denied, with denied Reads of other toolkit files by path.
- `evals/test_replay_bench_checks.py` (new). Its log and transcript fixtures keep the key structure of the shapes Verification steps 1a and 1b captured, with every value synthetic: paths, session and message IDs, `transcript_path`, `cwd`, CLI version and every `usage` count. The margin cases compute from the fixtures' own numbers. Cases:
  - `instrument-missing`: an empty log, an absent log, and a log with no dispatcher `CLAUDE.md` load;
  - `instrument-log-invalid`: a torn last line, a foreign `session_id`, and two `agent_id` values;
  - field shapes, each one mutation of a captured line, and each failing `instrument-log-invalid` without raising:
    - `agent_id` null, empty, and a number;
    - `session_id`, `file_path` and `load_reason`, each absent and each a number;
    - a non-string `file_path`, which never reaches `os.path.realpath`;
    - a string `file_path` holding a NUL byte, which never reaches `os.path.realpath`;
  - attribution: a subagent-only `session_start` load, credited to the subagent; a duplicate dispatcher load, not credited to it;
  - a stowed `CLAUDE.md` and `rules/` file reported by link target, still classed toolkit;
  - arm outcomes:
    - `bare` failing `arm-leak`, one case each:
      - a subagent toolkit rule load;
      - a subagent other-class load;
      - a dispatcher rule load;
      - a dispatcher load of an other-class `CLAUDE.md`;
    - `bare` whose only loads are the dispatcher's `CLAUDE.md`, passing;
    - `toolkit` with subagent rule loads and with no subagent load at all, each passing with its loads recorded by class;
    - a fixture-file load failing `fixture-instruction-load`;
    - a load of a file on the Read allow list classed toolkit;
  - first-turn prompt size: the sum of the three usage fields, null when one is absent, and null when the transcript holds no assistant entry;
  - the block-calibration verdict, each case asserting the returned list of reasons:
    - a passing block;
    - a `toolkit` log with no subagent `path_glob_match` rule load, failing `instrument-missing`;
    - a `toolkit` log whose only rule-shaped loads fail `instrument-missing`, one case each:
      - a `path_glob_match` load on a dispatcher line;
      - a subagent rule load with `load_reason` `session_start`;
      - one with `include`;
      - a subagent `path_glob_match` load of a fixture-class file;
      - one of an other-class file;
      - one of a file under `agents/`, one under `skills/`, and one of the account's `CLAUDE.md`;
      - one of `<config-dir>/rules-extra/x.md`, which a string-prefix match would pass;
    - a `toolkit` rule load reported by its link target, and one of a rule nested below `rules/`, each passing;
    - an account `CLAUDE.md` of 8,000 characters, equal bodies and no dispatcher-loaded rule, so the margin is 1,000 tokens: a gap of 1,000 passing, and one of 999 failing `arm-config-missing`;
    - a `code-writer` body 4,000 characters longer than `bare-writer`'s, which raises the gap the check needs by exactly 1,000;
    - a dispatcher-loaded rule in the `toolkit` run raising the margin by its character count over 4, not halved;
    - a null size in either run failing `arm-config-missing`;
    - order, the list asserted whole:
      - a control failure with a null size returns `instrument-missing` then `arm-config-missing`;
      - a control failure with a short gap returns the same two;
  - the exclude note. Phase 4's `smoke` cases show that it never changes the verdict:
    - without the trigger-path code, one case: the note is the residual sentence alone;
    - with it, a trigger path the `bare` transcript shows a Read of that ran prints the exercised line, compared relative to each fixture;
    - with it, a `bare` transcript with no such Read, and one whose only such Read was denied, print the not-exercised line;
    - with it, a trigger field that is absent, not a string, or holds a NUL byte leaves the line out without raising, and each of these cases also prints the residual sentence;
  - Read-allow-list records: a listed Read that ran, a listed Read denied by a hook and one denied by the permission flow, and a denied Read of an unlisted toolkit file recorded by path;
  - the forged-log case moves to Phase 4's runner tests (row 162);
  - `bare` reading a toolkit agent file through Read and through Bash fails `arm-leak`, and the same reads pass `toolkit`;
  - write paths: an Edit inside the fixture passes, and a Write outside it fails;
  - Bash paths:
    - a token under the projects root, one onto a live checkout's changed path, one under the local dir, and one into another fixture, each failing;
    - an absolute token into the run's own fixture, which sits under a `FIXTURE_DIR_PREFIX` directory, passing;
    - a `~/` token resolving under a `tmp_path` home's projects root, and a `../..` token resolving from the fixture into another fixture, each resolved and failing;
    - an unrelated absolute path, recorded;
    - an unsplittable command, recorded as unparsed;
  - malformed entries: a Bash `command` that is not a string, an Edit with no `file_path`, and a token holding a NUL byte, each recorded as unparsed without raising;
  - permission-rule denials from the permission flow and from a hook, each counted;
  - the account-hook check: a found file passes, an absent file fails `account-hooks-missing`, and a symlink and a file for another session each count as absent.

**Phase 4 — arms, launch, runner, CLI (second PR).** Verify: `scripts/venv-run.sh pytest evals/test_replay_bench_arms.py evals/test_replay_bench_runner.py evals/test_replay_bench_tasks.py`.
- `evals/replay_bench/tasks.py` (edit):
  - make no `write_fixture_extras` split. `build_task_fixture` has been the extras' only caller since the calibration fixture was removed (row 136), so row 129's move of the split to this PR is moot, and row 141 records the drop;
  - `build_task_fixture(task, dest_dir, baseline_git_dir, *, bare_writer_markdown, timeout_s, settings_text=None) -> str` writes `.claude/settings.json` from `settings_text` when one is given, beside its other extras;
  - `SelectionLimits.__post_init__` raises `TaskSourceError` for a limit outside its range (row 149), and `TaskSourceError`'s docstring names a selection limit among the arguments it refuses:
    - each of the eight integer fields (`max_count`, `max_tasks`, `max_files`, `min_code_lines`, `max_code_lines`, `max_prompt_chars`, `max_base_mb`, `max_base_entries`) must be at least 1. The message names the flag, which is the field name with underscores as hyphens;
    - `min_code_lines` above `max_code_lines` is refused, naming both flags. Equal values are valid, since Tasks makes both bounds inclusive. The check compares the resolved values after defaults, so `--max-code-lines 5` alone is refused against the default minimum of 10;
    - no other pair is checked, because `--max-tasks` above `--max-count` just caps the task count;
    - `max_count` below 1 is refused because `git log -n -1` walks the whole history, so a negative value would silently remove the 500-commit bound.
- `evals/test_replay_bench_tasks.py` (edit):
  - its `_build_fixture` helper passes the new keyword where a case needs it, and every first-PR case still passes unchanged;
  - a `settings_text` given to `build_task_fixture` lands at `.claude/settings.json` byte for byte with `git status --porcelain` still empty, and no settings file exists without one;
  - `SelectionLimits` cases:
    - each of the eight integer fields at 0 and at -1 is refused, naming its flag, with `max_count=-1` among them;
    - `min_code_lines == max_code_lines` is accepted, and a minimum one above the maximum is refused with both flag names in the message.
- `evals/replay_bench/arms.py` (new): the `Arm` record with its expected and failing dispatcher loads, its fixture settings and its allowed fixture loads; the `bare` and `toolkit` registry; `bare`'s `claudeMdExcludes` generator with its call of the shared character check and its settings text; the `bare-writer` file text built from the account's `code-writer.md` frontmatter; and both agents' body character counts for the block calibration, `code-writer`'s read from the account's `<config-dir>/agents/code-writer.md`.
- `evals/replay_bench/launch.py` (new): the environment allowlist as data, the `env -i` prefix, the readiness argv, the `--settings` JSON with the instrument hook, `UnsafeConfigPathError` with the one character check both generators share, and the Read allow-list generator with the `--allowedTools` argv tail.
- The runner entry below is split across modules so each file stays under the 1,000-line cap, by this map:
  - `account.py`: the account preflight and its readiness check;
  - `attempt.py`: one attempt, its retry and its reason precedence;
  - `campaign.py`: the block and campaign loop, resume and the sweep;
  - `spend.py`: the spend file;
  - `smoke.py` and `smoke_pass.py`: `smoke` and the smoke-pass record;
  - `summary.py`: `summarize`;
  - `diffing.py`, `records.py` and `render.py`: the attempt diff, the run record, and the escaping every printed refusal and `missing_detail` goes through;
  - `runner.py`: `FIXTURE_GIT_TIMEOUT_S`, `run_block` and the environment read.
- The runner behavior, under the modules above (new):
  - the account preflight, its account key, its `PATH` check, its call of both generators, and its readiness check with the CLI version floor, with failure text in the probe's shape;
  - the preflight's names-only list of every variable the launch drops;
  - `FIXTURE_GIT_TIMEOUT_S`:
    - its value is 110 (Fixture);
    - it is the `--fixture-git-timeout-s` default;
    - a one-line comment cites this plan's Fixture section as its basis;
  - the hook-log read that waits for the log's size to hold across two polls, as Instruction-load check states;
  - one attempt: fixture, launch, validity, checks, diff, metrics, and cleanup in a `finally`. The checks step calls `check_tool_call_paths` with `config_dir` on the subagent sidecar. The dispatcher transcript stays `evaluate_run_validity`'s. The step fails the run on `transcript-unreadable` (Validity, the Bash-check contract);
  - retry-then-missing, with the cap charge, `launch-no-output`, and `fixture-error`, a generator's `UnsafeConfigPathError` included with its path in `missing_detail`, and `analysis-error` and every instrument reason without a retry;
  - the instrument reason outranking every other reason on one attempt;
  - every config-tree path and variable name it prints, in `!r` form;
  - the spend file and its torn-tail repair at resume;
  - the block and campaign loop with its pinned environment, smoke gate, spend guards, per-arm stop with its instrument halt and in-memory count of failed blocks in a row, per-block progress line, halt messages and exit codes, and `ReplayRunRecord`;
  - `RunStore` use with the lock taken before the sweep, the per-account sweep, the printed count of uncharged swept attempts, the resume's deletion of the whole `attempts/`, and the attempt-dir creator;
  - the manifest's repo-to-account pin, the resume's ceiling over the incomplete blocks, and `--fixture-git-timeout-s` outside the manifest comparison;
  - `smoke`'s one-run-per-arm block, printed ceiling, no-retry rule for instrument reasons, printout with the exclude note, block calibration, `read-grant-denied` and `read-grant-unexercised` conditions, and arm-keyed smoke-pass record;
  - `summarize`.
- `runner.py`'s reuse from `review_bench.runner`:
  - prompts and launch: `build_dispatcher_prompt`, `build_dispatch_command`, `wait_for_session_flush`, `extract_result_usage`;
  - validity: `evaluate_run_validity`, `own_session_paths_for`, `default_live_checkout_roots` through its module, `_worktree_top_levels`, `session_store_dir_for`, `_os_error_detail`;
  - campaign: `read_environment_record`, `EnvironmentReference`, `build_block_plan`, `RunStore`, `count_outcomes`, `all_runs_missing`, `campaign_cost_ceiling`, `remove_tree_reporting_failure`, `_repair_torn_tail`;
  - constants: `FIXTURE_DIR_PREFIX`, `REVIEWER_MODEL_ID`, `REVIEWER_TIMEOUT_S`, `ATTEMPTS_PER_RUN`, `_ENVIRONMENT_READ_TIMEOUT_S`.
- `runner.py`'s reuse from `measure_subagent_model_resolution`: `_run_claude_to_completion`, `_resolved_temp_project_dir`, `abort_launches`, `LaunchAbortedError`, `PER_RUN_BUDGET_CAP_USD`, `agent_frontmatter_tools`, `agent_frontmatter_model` and `_frontmatter_block`.
- `runner.py`'s reuse from the rest of `review_bench`: `analysis.bootstrap_interval`, `identifiers.validate_campaign_id`, `defects.atomic_write_text`, `fixture_repo._validate_sha`, and `local_git`'s `isolated_git_environment` and `DIFF_TEXT_ARGS`.
- `evals/run_replay_bench.py` (new): the subcommands `tasks`, `preflight`, `smoke`, `run` and `summarize`, with `DEFAULT_LOCAL_DIR = EVALS_DIR / "replay_bench" / ".local"`, `--local-dir` and `--fixture-git-timeout-s`. `tasks` takes one flag per `SelectionLimits` field, `--rev` included, named as the field with underscores as hyphens and defaulting to `None`, and `main()` passes only the flags given, by field name (row 152). `smoke` takes no `--k`. `main()` runs the startup steps in Approach's order. Neither an `UnsafeFixtureConfigError` nor an attempt's `UnsafeConfigPathError` reaches it, because an attempt records each as `fixture-error` (Retry). It returns 2, with the offending file path or name in the message, on any of these:
  - `HarnessInvalidatedError`, `TaskSourceError` (an out-of-range selection limit included), `TaskFileError`, or `identifiers.InvalidIdentifierError`, which `tasks.py` raises for a task-set name that breaks its grammar;
  - a preflight, spend, smoke-gate or local-dir refusal, or a resume's refusal of a symlinked `attempts/`;
  - an environment-pin mismatch;
  - a Python or git below its floor.
- `run` returns 1 on every halt, a failed spend append included, and 0 when it completes every task.
- `evals/test_replay_bench_arms.py` (new). Cases:
  - a `bare-writer.md` generated from a `tmp_path` `code-writer.md` and parsed with `msmr.agent_frontmatter_tools`, `msmr.agent_frontmatter_model` and the `effort` reader has that file's `tools`, `model` and `effort`, plus `name: bare-writer` and `omitClaudeMd: true`. That `code-writer.md` mirrors the real file's keys, with a multi-line `description` and distinctive non-default `tools`, `model` and `effort` values. No case reads this checkout's agents directory (row 158);
  - the `effort` reader returns nothing for a frontmatter without the key;
  - the registry's `bare` and `toolkit` allow no fixture loads, and both expect the account's `CLAUDE.md`;
  - the body character counts: `code-writer`'s body and `bare-writer`'s one-line body, each counted after the frontmatter of a `tmp_path` agent file, with a frontmatter-only file counting zero;
  - with two different `code-writer.md` files, one under a `tmp_path` account config dir and one under a `tmp_path` checkout, the count comes from the account's;
  - `bare`'s exclude list, for a `tmp_path` config dir in each stow shape, a `rules` directory link to a tree holding a nested rule and a real `rules` directory of per-file links, lists each rule's link path and realpath, holds no glob character, and leaves out the account's `CLAUDE.md`;
  - the exclude list leaves out a `.txt` file and a `.md.bak` file under `rules/`;
  - a `rules` tree holding a directory-link loop yields a finite list;
  - `bare`'s settings text parses as JSON equal to `{"claudeMdExcludes": [...]}`, with no other key;
  - `toolkit` writes no settings file.
- `evals/test_replay_bench_runner.py` (new).
  - It stubs every `claude` call, and every `direnv` call outside the preflight cases below. An injected `launch=` writes a synthetic dispatcher transcript and subagent sidecar under a `tmp_path` projects root, and fakes the fixture edits.
  - It builds transcripts with `test_review_bench_runner.py`'s scenario helpers by import, not by copy.
  - It monkeypatches `review_bench.runner.read_environment_record` to a fixed reading, or to a sequence for the drift cases.
  - A local autouse fixture points `run_replay_bench.DEFAULT_LOCAL_DIR` under `tmp_path`. It also stubs `review_bench.runner.default_live_checkout_roots` to return no roots, since the shared stub runs only once `run_review_bench` is imported (row 90). This is the only test file that calls `main()`.
  - The same fixture sets `SESSION_FLUSH_POLL_INTERVAL_S` and `SESSION_FLUSH_TIMEOUT_S` on `review_bench.runner` to near zero by module attribute, so the stubbed attempts skip the real flush wait (row 107).
- Hang-prone and wall-clock tests (row 89):
  - `@pytest.mark.timing` marks the twenty-hook parallel test, the group-kill test, the direnv-timeout test, the readiness-timeout test, the FIFO case, and one flush test that restores the real flush constants and asserts the wait ends once the transcript and sidecar stop growing.
  - Every fake that sleeps runs `exec sleep <n>`, so a timeout's kill reaches the sleeper itself and leaves no orphan.
  - Group kill: a launch timeout of 1 s, a poll deadline of 10 s, and a forked child that sleeps 30 s, so only the kill ends it before the deadline.
  - Direnv and readiness timeouts: a limit of 1 s, passed to the readiness check as its `timeout_s` parameter, a fake that sleeps 10 s, and the refusal asserted within 5 s.
  - Twenty parallel hooks: every invocation joined within 30 s.
  - The FIFO case runs the post-run analysis in a daemon thread joined with a 30 s deadline, and fails with a message naming the FIFO when the deadline passes.
- `evals/test_replay_bench_runner.py` attempt-validity cases, each with a stub `launch=`, run for both arms:
  - forged log (row 162). The stub writes a real log into the attempt dir and a differing log under the same file name at the fixture root. Both directions are asserted on the run's verdict:
    - a real log with no dispatcher `CLAUDE.md` load plus a forged log with one gives `instrument-missing`;
    - a clean real log plus a poisoned forged log passes;
  - `transcript-unreadable` from `check_tool_call_paths` on the subagent sidecar. The damaged sidecar passes `evaluate_run_validity` first, with valid findings text and the damage in a non-final line, so only the new call can produce the reason:
    - a skipped line and a torn tail each fail the run `transcript-unreadable`;
    - an OSError on the sidecar is a control that does not count toward the wiring, since `evaluate_run_validity` returns the same reason for it;
  - `config_dir` and the other reasons reach the record: a Bash command `ls <config_dir>` fails `out-of-fixture-path`, and a Write outside the fixture fails `out-of-fixture-write`. Each asserts the reason, the failing token or command in the run record and in `smoke`'s printout;
  - these three reasons are not in `INSTRUMENT_REASONS`, so each retries once, and a Retry case pins that;
  - a no-false-hit control: the scan over command shapes taken from the Verification step 1a and 1b transcripts, such as heredocs with paths in prose, leaves a `bare` run clean. Values stay synthetic.
- `evals/test_replay_bench_runner.py` launch and environment cases:
  - launch argv: the `env -i` prefix, `--permission-mode acceptEdits`, a `--settings` JSON holding only the instrument hook, compared as parsed JSON, and `--allowedTools` with its entries last;
  - Read allow list, for a `tmp_path` home whose `.claude` and a separate `tmp_path` config dir each hold stowed-style links into a third directory:
    - each agent and skill file appears once by link path and once by realpath, as `Read(//...)`;
    - one root that is the other's realpath yields no duplicate;
    - a nested `agents/sub/x.md`, a non-`SKILL.md` file under `skills/<name>/` and a dangling link under `agents/` are each left out;
    - no entry holds `*`, and the list is identical for both arms;
  - the shared character check, one parametrized row per class (row 134):
    - each refused class raises `UnsafeConfigPathError`: a space, a tab, a newline, another control character, `*`, `?`, `[`, `]`, `{`, `}`, `(`, `)`, `,` and `\`;
    - each kept lookalike passes: `-`, `_`, `.`, `+`, `@`, `~`, `#`, `!`, `:`, `=` and a non-ASCII letter;
  - one wiring case per emitted form of each generator, so a check of one form alone fails:
    - the exclude generator on a rule file whose link path holds `*`, and on one whose realpath alone holds `[`;
    - the Read-allow-list generator on a `skills/<name>/SKILL.md` whose link path holds `(`, and on one whose realpath alone holds a space, each naming the offending path;
  - escaped printing: a rule file name and a probe variable name each holding an ESC byte and a newline, refused or listed, print in `!r` form, and neither byte reaches the captured terminal output raw;
  - preflight calls the same generators: a refused path exits 2 naming the path, after the probe and before the readiness check and any launch;
  - an attempt whose generator raises records `fixture-error` with the path in `missing_detail`, no launch, no retry and no spend line;
  - the hook-log read, on a real log file in `tmp_path` with `time.sleep` patched as `TestWaitForSessionFlushSettlesOnlyUpToTheTimeout` at `evals/test_review_bench_runner.py:826-900` patches it. Each case sets the constants it needs and asserts the sleep count:
    - a line appended during the first poll interval is read, and the sleep count shows the loop went on past one sample;
    - a log whose size holds across two polls returns after the fewest sleeps;
    - a log that grows on every poll stops at the timeout and is read as it stands, so a torn last line fails `instrument-log-invalid`;
    - a log absent at the first poll and present at the second counts as growth, with no raise on the first `stat`;
    - a `timed_out` attempt makes no sleep;
  - the allowlist passes `https_proxy`, `http_proxy`, `no_proxy`, `ALL_PROXY` and `all_proxy` when the runner sets them;
  - an allowlisted variable the runner does not set is absent from the argv, never `NAME=`, and `CLAUDE_CONFIG_DIR` is absent when the probe found it unset;
  - the instrument hook, run with `sh -c`, appends a JSON line to a log whose path holds a space and a single quote;
  - twenty parallel hook invocations on distinct JSON lines leave a log whose every line parses (row 76);
  - a fake `claude` on a `tmp_path` `PATH`, behind the real prefix, dumps its environment. A sentinel variable set in the runner is absent, a proxy variable set in the runner is present, and `CLAUDE_CONFIG_DIR` is the probed value;
  - group kill: a fake `claude` behind the prefix forks a child that writes its pid to a file and sleeps for a bounded time. After `_run_claude_to_completion` with a short timeout, the test polls to a deadline until that pid is gone, rather than checking once after a fixed sleep;
  - print hygiene, with `HTTPS_PROXY=http://user:sentinel@proxy.invalid` set in the runner's environment (row 98):
    - a fake `claude --version` that prints the sentinel to stderr and exits 1;
    - a fake `claude --version` that sleeps past its limit;
    - a stubbed launch that raises `OSError`;
    - in each, the refusal message, the record's `missing_detail`, and the captured stdout and stderr hold no sentinel;
  - a `PATH` holding an empty entry, and one holding a relative entry, each refused before any probe.
- Preflight cases, through a fake `direnv` on a `tmp_path` `PATH` that `exec`s its remaining argv, so the real snippet runs:
  - a fake that keeps the caller's working directory passes;
  - a fake that changes into the source repo first is refused (row 20);
  - `CLAUDE_CONFIG_DIR` set resolves to its value, and unset resolves to `~/.claude` under a `tmp_path` home;
  - stdout that is not JSON and holds a sentinel is refused, and the message holds no sentinel;
  - a fake that sleeps past `--direnv-timeout-s` is refused with a message naming the limit and the flag;
  - a probe failure whose message holds none of the probe's stderr, and whose `exit <n>` carries the `direnv exec <repo> true` hint;
  - each other refusal, a `code-writer.md` with no `effort` included;
  - a fake whose `.envrc` stand-in exports `SENTINEL_PROFILE` with a value of three lines, one of them shaped `LEAKED_NAME=sentinel-value`, before it `exec`s, prints the name `SENTINEL_PROFILE`. Neither the output nor the probe's JSON holds any line of the value, `LEAKED_NAME` included, so a text filter over `env` output would fail the case;
  - a runner environment already holding `AMBIENT_PROFILE=sentinel-value`, behind a fake that changes nothing, prints `AMBIENT_PROFILE`, and prints no allowlisted name;
  - readiness: a `PATH` with no `claude`, and a fake `claude --version` that exits 1, each refused before any launch;
  - version floor: a fake `claude --version` printing `2.1.270`, and one printing text with no version, each refused; one printing `2.1.271` passes;
  - a fixture under a monkeypatched `$HOME`, refused.
- Git and diff cases, with real `git` and a stub launch that acts after `build_task_fixture` returns:
  - the agent rewrites its own history, and the diff equals the uncommitted-edit diff after each of these:
    - it commits its edits;
    - it runs a mixed `git reset`;
    - it runs `git checkout -b`;
  - a hostile config and tree (rows 50, 77), where the stub writes:
    - a `.git/config` setting `core.fsmonitor` and a `filter.<name>.clean` command;
    - a `.git/info/attributes` and a work-tree `.gitattributes` naming that filter;
    - a FIFO;
    - a symlink to a FIFO under `tmp_path`;
  - after that hostile case:
    - the run completes;
    - neither command's sentinel file exists;
    - the symlink counts as a non-file entry;
  - untracked trees (row 78):
    - a file under a path the base tree's `.gitignore` ignores lands in `ignored_paths`;
    - a nested repository with a commit counts as a non-file entry;
    - a nested repository with no commit records `analysis-error`;
  - agent edits that reach the diff only as intended:
    - deleting a file;
    - adding a binary;
    - writing under the excluded `/.claude/`;
  - a `TimeoutExpired` from the fixture build records `fixture-error`, and one from a post-run git call records `analysis-error`, each with `git timed out after <limit> s; raise --fixture-git-timeout-s` as its detail;
  - a `subprocess.run` spy over the whole analysis step, with `--fixture-git-timeout-s 37.5`, sees each of the five post-run git calls carry `timeout=37.5`: `add -A`, `diff --cached -U0`, `diff --cached --raw -z`, `cat-file --batch` and `ls-files --others`;
  - extraction refusals, each recording `fixture-error` on every run with no launch, and leaving no fixture or attempt dir:
    - a base tree whose `_extract_archive` pre-check raises `UnsafeFixtureConfigError`;
    - a base tree whose tar filter refuses a member after earlier members reached disk;
    - in both, the campaign goes on to the next task's block, and `main()` never exits 2.
- Retry cases:
  - an attempt that fails validity twice records missing with summed cost, and leaves no fixture or attempt dir;
  - in `run`, a `bare` attempt failing `arm-leak` launches no retry, and its run ends missing as `arm-leak`;
  - on one attempt, a timeout whose log also shows a `bare` subagent rule load records `arm-leak`;
  - a fail-once-then-succeed run takes its diff from the second fixture, and the first fixture and attempt dir are gone;
  - a launch timeout and a launch exception are each charged at the cap;
  - an empty stdout records `launch-no-output`;
  - an exception from the analysis after a launch records `analysis-error` with the attempt's cost, and no second launch happens;
  - a prune that raises records `fixture-error` with no launch;
  - the `analysis-error` and `fixture-error` cases each leave no fixture or attempt dir.
- Cleanup cases, each with a `tmp_path` config dir:
  - a valid run's record, diff file, metrics and cleanup, with its `sessions/` file left in place;
  - `attempts/` on resume:
    - an `attempts/` holding a nested attempt dir is gone after the resume, and the next attempt creates `attempts/` and its own dir;
    - an absent `attempts/` prints nothing to the captured stderr;
    - an `attempts/` that is a symlink to a sentinel dir makes `run` exit 2 naming the path, with the sentinel intact;
    - an attempt whose dir already exists records `fixture-error` with the path in `missing_detail`, and launches nothing.
- Campaign cases:
  - environment drift at block start and at block end halts the campaign, writes no record, never calls `mark_block_complete`, and prints the block's spend;
  - a resume after an account's environment changed exits 2 before any launch, naming the account and the field;
  - the per-arm stop, each through a stub launch whose task-specific failures carry no instrument reason:
    - four tasks that fail, pass, fail and pass complete all four, and `run` exits 0, since a passing block resets the count;
    - two failing tasks followed by a third halt `run` with exit 1 and both failing blocks complete, and the stub asserts the third task never launches. The printout names each failing arm with its missing reasons and `missing_detail`;
    - two failing tasks where the second is the task set's last also halt with exit 1, not the exit 0 a lone failed last block gets;
    - one failing task that is the task set's last, after a passing one, ends with exit 0;
    - the failing arm is `toolkit` in one case and both arms in another;
    - a block with k of 2 or more, in which one arm has exactly one valid run among failures, is not a failed block;
    - a resume after a two-in-a-row halt reruns neither failed block and goes on to the next task;
    - with k of 2, a block where one `bare` attempt ends under `arm-leak` and the other `bare` run is valid launches no retry, lets the block's other runs finish, marks the block complete and halts `run` at its end with exit 1, naming the task, arm and reason. A resume does not rerun that block and goes on to the next task;
    - a failing block followed by one that drift or a spend stop halts leaves the failing block complete;
  - each halt prints its reason and exits 1, with one case each for drift, the cumulative stop, the account stop, an instrument failure and two task-specific failed blocks in a row. A campaign that completes exits 0;
  - each block prints its progress line with the task ID, per-arm counts, each arm with no valid run and its missing reasons, the running per-arm count of dropped tasks, the block's spend and the totals against their stops;
  - a resume whose probe resolves a source repo to another account key than the manifest's exits 2 before any launch, naming the repo;
  - a resume with a changed `--fixture-git-timeout-s` passes the manifest comparison;
  - smoke gate: `run` exits 2 before any launch for an account in each of these cases:
    - no smoke-pass record;
    - a record holding another environment reading or model ID;
    - a record whose arms lack one of the manifest's;
    - a record that does not parse, with no traceback;
    - a record whose stored account key does not hash to its file name's digest;
  - a crash between record writes and `mark_block_complete`, then a rerun, leaves exactly one file set;
  - a two-account resume sweeps each account root and reruns each swept block once;
  - the manifest-mismatch exit, one case per field: task-set hash, arms, k, seed and model ID.
- Spend cases:
  - a block ceiling over a stop exits 2 before any launch;
  - a campaign ceiling over the cumulative stop exits 2 without `--allow-early-stop`. With it, the campaign runs until the cumulative stop halts it before the block that would pass it;
  - a resume after one complete block prints the run count of the remaining blocks, and compares the spend so far plus their ceilings with the cumulative stop. Its stop sits between that figure and the all-tasks ceiling, so only that reading passes without `--allow-early-stop`;
  - the cumulative stop and the account stop each fire before the next block, and the account stop halts the whole campaign;
  - two repos with one config dir share one account's spend, and two with different config dirs do not;
  - after a crash between launches and `mark_block_complete`, the resume's stops count the lost block's spend lines when deciding whether the next block launches;
  - a swept entry with a session ID and no spend line is swept and its block reruns, it gets no charge, the resume's stop arithmetic equals the sum of the spend lines, and the resume prints `1 logged attempts with a session ID have no spend line and were not charged` with its total and ceiling lines saying they leave it out;
  - a spend append that raises `OSError` halts `run` with exit 1 and leaves its block incomplete;
  - an interrupt during a live launch leaves the aborted attempt's cap-charge line in the spend file when `main()` returns;
  - a torn last spend line is truncated at resume before the first append, and the next appended line parses;
  - an unparseable line that is not the last counts at the cap;
  - the spend-file appender called from four threads, N appends each, leaves 4N lines that all parse, with the total equal to the sum.
- CLI cases:
  - exit 2 for `run` without `--k` or `--cumulative-stop-usd`, and for a `--task-id` breaking its grammar;
  - exit 2, with the flag, path or name in the message and no traceback, for each of these:
    - `tasks --max-count 0`, whose `SelectionLimits` raises `TaskSourceError`. The message holds `--max-count`, and a stubbed `mine_tasks` that fails when called is never called;
    - a `run` whose task file raises `TaskFileError`;
    - a `tasks --name` that raises `identifiers.InvalidIdentifierError`;
  - limit wiring, with `mine_tasks` stubbed to capture its `limits`:
    - `tasks` with no limit flag passes limits equal to `tasks.DEFAULT_LIMITS`;
    - `tasks` giving all nine flags, the eight integer limits and `--rev`, distinct non-default values lands each value in its own field;
  - a `--local-dir` refusal for each condition, each exiting 2 with the target path still absent:
    - a relative path;
    - a path through a symlink;
    - a final component other than `.local`;
    - a path the work tree does not ignore;
  - owner-only controls (row 91):
    - after `main()`, the local dir is 0700;
    - a pre-existing 0755 local dir is restricted to 0700;
    - a file the runner writes under it is 0600;
  - this checkout's `git check-ignore` reports the default local dir ignored;
  - Python below 3.12, through a monkeypatched `sys.version_info`, and git below 2.32, through a fake `git` on `PATH`, each exit 2 before preflight;
  - an interrupt calls `abort_launches` and prints the resume command with its wait and its `TMPDIR` line.
- `summarize` cases, on synthetic records, each asserting printed values parsed back as numbers:
  - the ratio-of-sums primary;
  - the line under the paired difference, on records holding one paired task, one task with no valid `toolkit` run, two with no valid `bare` run, one with neither and one with no non-test code line in one arm: `paired 1 of 6 complete tasks`, and the four exclusion counts 1, 2, 1 and 1, each parsed back as a number and each with its missing reasons;
  - a task with no non-test code line in one arm, left out of the pairs and counted in the zero-code-line tally;
  - spend by account, from a synthetic spend file, with an unparseable line counted at the cap against every account;
  - a partial incomplete block's records on disk, left out of every figure and counted as incomplete;
  - one paired task, printed with no interval;
  - per arm, the Python docstring lines per 100 added code lines, the permission-denial count, the unclassified-line share, the `ast.parse` rejection count, and the three tallies;
  - the mixed-environment exit.
- `smoke` cases:
  - `instrument: ok`, with its smoke-pass record written, naming the arms it validated;
  - each run's printout carrying its fixture-build seconds and first-turn prompt size, and the block's body term and margin;
  - each failed condition naming itself, with no record written, `read-grant-denied`, `read-grant-unexercised` and each block-calibration outcome included;
  - one run per arm: a block launches exactly one `toolkit` run and one `bare` run on the given task, and `smoke --k 2` is a usage error;
  - no retry on instrument reasons, through a stub launch, one case per row:
    - `bare` attempts of `arm-leak` then clean end the run missing as `arm-leak` with one attempt launched, and no smoke-pass record is written;
    - `toolkit` attempts of `instrument-missing` then clean end the same way;
    - an attempt of `launch-no-output`, a timeout or `result-error`, then clean, retries once and lets the block calibration run;
    - one parametrized case over the shared instrument-reason constant: each of its five reasons, `fixture-instruction-load` included, ends a `smoke` run missing with one attempt launched;
    - a timed-out attempt whose log carries a `bare` subagent rule load records `arm-leak` and launches no retry;
    - `launch-no-output` then `arm-leak` ends the run missing as `arm-leak` with two attempts launched;
  - the exclude note prints after the block's runs, and a `bare` run that read no trigger path still writes the smoke-pass record when every condition holds;
  - order across the whole list: a block where `bare` fails `arm-leak`, the `toolkit` run makes no listed Read, and the calibration gap is short names `arm-leak` on its last line and prints all three;
  - a `toolkit` run that ended missing on a timeout reports `toolkit` and `timeout`, and the verdict never runs;
  - Read grant, through a stub launch, one case per row:
    - a `toolkit` run with one listed Read that ran passes;
    - a `toolkit` run with no listed Read fails `read-grant-unexercised`;
    - a `toolkit` run whose listed Read was denied fails `read-grant-denied`, with a hook denial and a permission-flow denial each counted (row 48);
    - a denied Read of an unlisted toolkit file prints by path and fails nothing;
    - a `bare` run's denied Read of a listed path fails `arm-leak`, never `read-grant-denied`;
  - the printed ceiling equals `campaign_cost_ceiling(1, 2, 1, ...)`'s `all_retried_usd`, printed before any launch.

**Phase 5a — test selection for the first PR's files (first PR).** Verify: `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py` and `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/`.
- `claude/.claude/scripts/select-tests.py`:
  - add `REPLAY_BENCH_DIR` and `REPLAY_BENCH_TEST_GLOB = "evals/test_replay_bench*.py"`;
  - add a `DOMAIN_RULES` row that selects `REPLAY_BENCH_TEST_GLOB` for a path under the directory or a glob member;
  - add `REPLAY_BENCH_TEST_GLOB` to the targets of all three rows now: the `_is_review_bench_change` row, whose predicate also covers `EVALS_CONFTEST`, and the `_REVIEW_BENCH_SCRIPTS_DEPENDENCIES` and `SKILL_EVALS_RUNNER` cross-domain rows (row 133);
  - add a standalone row that selects `SELECT_TESTS_TEST_PATH` for a change under `REPLAY_BENCH_DIR`, beside `MEASURE_SUBAGENT_MODEL_RESOLUTION_TEST`'s at `:832`, with a header-comment entry like the one at `:812-818`. `test_select_tests.py` reads the replay package's files from a function body, which `TestCrossDomainReadCompleteness` cannot see, so without the row a scoped run after a replay edit skips the guards below;
  - update the comments that name these rows' target sets, `:80-81`'s "exactly" list for `EVALS_CONFTEST` among them, and those at `:603-607` and `:787-808`.
- `claude/.claude/scripts/tests/test_select_tests.py`:
  - add `REPLAY_BENCH_TEST_GLOB` to every set-equality assertion over the review-bench target set, `test_config_dir_module_change_selects_all_four_declared_importer_sets` at `:1575-1596` and the `SKILL_EVALS_RUNNER` equality case near `:690` included (row 67);
  - add cases for a replay package file and a replay test file, and one where a change under `evals/replay_bench/` selects `SELECT_TESTS_TEST_PATH`;
  - add a replay counterpart of the `_REVIEW_BENCH_SCRIPTS_DEPENDENCIES` membership loop at `:798-806`;
  - add `REPLAY_BENCH_DIR` to `test_review_bench_directory_constants_exist_on_disk`;
  - add `replay_bench/*.py` to the import-closure test's seed list, as that test's docstring asks of a sibling eval script, and keep `_REVIEW_BENCH_SCRIPTS_DEPENDENCIES` equal to the closure. The seeds guard the second PR's later imports. The row targets above rest on row 133, not on this test;
  - add a test that parses every file in `evals/replay_bench/` and finds no `ImportFrom` with `level > 0`, with a seeded relative import as its failing control. It also asserts that its scanned set holds `tasks.py` and `comment_metrics.py`, so a glob that resolves to nothing fails. The closure test maps every relative import to `review_bench`, so the replay package must import by absolute path (row 81).

**Phase 5b — the rest of test selection, and docs (second PR).** Verify: as Phase 5a.
- `claude/.claude/scripts/select-tests.py`:
  - add `REPLAY_BENCH_RUNNER`;
  - add the runner to Phase 5a's `DOMAIN_RULES` row, and to Phase 5a's standalone row, since `run_replay_bench.py` joins the guards' scanned set. `evals/test_replay_bench_arms.py` reads no real agents directory, so it needs no `AGENTS_DIR` wiring (row 158).
- `claude/.claude/scripts/tests/test_select_tests.py`:
  - add a case for the replay runner;
  - add `REPLAY_BENCH_RUNNER` to `_EXACT_MATCH_LITERAL_PATH_CONSTANTS`;
  - add `run_replay_bench.py` to the import-closure test's seed list, to the no-relative-import test, and to that test's scanned-set assertion;
  - add a case where a change to `run_replay_bench.py` selects `SELECT_TESTS_TEST_PATH`.
- `evals/README.md`: a "Replay bench" section after "Review bench". It covers:
  - what the runner measures, and usage for the five subcommands;
  - `run`'s exit codes, its per-block progress line, and the per-arm stop:
    - the first attempt that ends under an instrument reason halts `run` at its block's end, with no retry and the block complete, so at most that task is lost and the way forward is to fix the cause and resume;
    - a task-specific failed block is marked complete and its task is left out of the pairs;
    - two task-specific failed blocks in a row halt `run`, and a resume is the way forward: it goes on past both, and those two tasks are lost from the sample. Exit 1 then wins over the exit 0 a lone failed last block gets;
    - a persistent failure meets the pre-block spend stops and the two-in-a-row halt;
    - two residuals: a usage-limit, auth or overload event that fails every run of two blocks drops two tasks no agent really tried, and failures on alternating tasks never make two in a row, so only `--cumulative-stop-usd` bounds them;
  - the account preflight with its readiness check, `--direnv-timeout-s` and the `direnv exec <repo> true` hint, the allowlisted launch environment, and the refusal of a `PATH` with an empty or relative entry;
  - that `preflight` names every variable the launch drops, ambient ones included, so the engineer reads that list per account before the first `run`;
  - that `HOME` passes, so file-based credentials such as `~/.config/gh`, `~/.aws` and `~/.ssh` are not routed by account;
  - that the runner assumes a single-user machine, since a proxy URL's credentials sit in `env`'s argv until it execs `claude`;
  - `tasks`' base-tree bounds:
    - `--max-base-mb`, in units of 10^6 bytes, and `--max-base-entries`;
    - the printout of the largest kept tasks;
    - `--fixture-git-timeout-s` for a machine where healthy builds time out;
    - `--fixture-git-timeout-s` again for a `--max-base-entries` raised past its measured default;
  - that `tasks` refuses a partial clone and a repo whose `info/attributes` sets `export-ignore` or `export-subst`, and skips a commit whose tree's `.gitattributes` does;
  - that `run` needs a passing `smoke` on each account under its current environment and for every arm it runs, so a CLI update, a `git pull` of the config checkout, or a new arm calls for a new `smoke`;
  - that `bare` fixtures exclude the account's rule files through `claudeMdExcludes`;
  - that both arms may read every agent and skill file under the config dir and under `$HOME/.claude`, the second a root under every account, through exact Read allow entries. On an install where stow linked `agents/` or `skills/` file by file into a directory that already existed, that includes files the config checkout does not hold;
  - that preflight and each attempt refuse a generated path holding whitespace, a control character or a pattern character, so a config checkout under such a path refuses every account;
  - that `smoke` runs one block of one run per arm and prints its ceiling before launch;
  - that the `smoke` task must modify a file the base tree holds that one of the account's rules matches, and that an account with no such task needs a larger `--max-count` or another source repo;
  - what `instrument-missing`, `arm-leak`, `arm-config-missing`, `read-grant-denied` and `read-grant-unexercised` each mean in `smoke`, and its printed exclude note;
  - the residual, in one sentence: a `bare` run that reads no rule-matching file passes without testing the exclude, and a broken exclude fails `arm-leak` only on later `bare` runs that read one (row 145);
  - that the block calibration's body term is a character-count estimate, so a gap close to the margin is weak evidence either way;
  - the residual that a hook line can be lost only when its append takes longer than the model turn that follows its load (row 121);
  - that Bash commands outside `acceptEdits`' auto-approved set and the account's allow rules are denied in both arms, so a replay agent cannot run, for example, a test suite the account does not allow, and the permission-denial count shows how often (row 122);
  - the reason `launch-error`: a launch call that raises `OSError` is charged at the cap, retried once, and not an instrument reason;
  - the Retry section's two-tier precedence rule, with the reached-the-end exception for the three absence reasons;
  - accepted residuals, which the README states:
    - the run-check residuals of row 161: heredoc and quoted-pattern false hits, which fail a `bare` run `arm-leak` and halt `run` at the block's end (row 160); `grep -r x ~` and `bash -c` text; denied Read, Grep and Glob calls; a Read of `<live>/.git` and of a linked worktree's common git dir; and the single CLI build the denial marker was verified on;
    - the eight environment names step 1b left unverified (row 164);
    - that the charged cost is the dispatcher's `total_cost_usd`, and whether it covers the subagent's spend is unverified, so the spend stops may undercount (row 65);
    - a descendant an agent leaves running after a normal end of stream, or a grandchild of a hung `direnv` probe, can outlive its attempt;
    - an interrupt can take as long as one fixture build;
    - that the instrument log's absolute path appears in the child's `--settings` argv (row 47);
  - the residual risk: nothing contains a replay agent beyond `acceptEdits`' working-directory scope, the account's permission rules and the runner's Read grants, so a steered agent can read or write anything those allow (rows 45, 47);
  - the bench's trust warning (row 45);
  - the trusted-author list's purpose, which is risk reduction and not containment: it limits how much third-party text reaches an agent holding Bash. It reads raw identities, so a `.mailmap` cannot widen it. Its limits (row 61):
    - an author email is a string the committer sets;
    - under a squash-merge host the author is the pull request's author, so the list leaves out outside contributors;
    - under a rebase or merge-commit workflow, an outside branch can carry a trusted email;
    - the delimiter skip stops no injected instruction that does not need to close its block;
  - that a machine with a managed-policy `CLAUDE.md`, or an account whose `CLAUDE.md` includes another file, fails every `bare` run;
  - the account files a replay adds to and the cleanup leaves: `.code-writer-self-review.log`, `.code-writer-self-review.d` and `.session-config-keys-log.jsonl` (row 79), and one `sessions/<pid>` file per launch, as every Claude Code session leaves (row 154). For four hours after a campaign, `post-crash-sessions.py` may list those replay attempts;
  - that after each hard kill, such as SIGKILL, an out-of-memory kill or a power loss, up to one launched attempt per worker thread (`run_skill_evals.DEFAULT_WORKERS`) may go uncharged. The undercount adds up over kills, each adding at most that many times the per-run cap, a nominal figure, and a resume prints how many swept attempts it did not charge (row 155);
  - where `.local/` lives, that removing a worktree deletes it, and `--local-dir`;
  - that a resume needs the same `TMPDIR` as the run it resumes;
  - that results are not a publication instrument, because the runner refuses no wider corpus (row 10);
  - that it needs Python 3.12 or newer, git 2.32 or newer and Claude Code 2.1.271 or newer, run through `scripts/venv-run.sh python3`;
  - a pointer to this plan for the design.

The second PR's files are those of Phases 3, 4 and 5b. Phase 3 measures 2,658 lines (895 in `checks.py`, 1,763 in three test files). The architect's inference from that is a final size of about 7,500 to 8,000 lines, which is above the 6,300 row 141 accepted. Phases 4 and 5b are still estimates, not counts, and the second PR restates the figure again when it edits this file. The engineer's "Proceed anyway" on the diff-size gate is on record in row 163, which says what that label does and does not cover.

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

1. Three manual probes. Steps 1a and 1c have run, and step 1b gates the second PR's Phase 3.
   - **1a, already run.** It ran three things, and its results stand in rows 16, 18, 19, 20, 26, 53, 76, 88, 94 and 106 and set the Fixture defaults:
     - `claude -p` once per arm with the Launch argv, the `env -i` prefix and the `--settings` JSON, on a one-line edit task in a scratch repo;
     - `direnv exec <dir> pwd` from `/tmp`;
     - four concurrent `build_task_fixture` builds per synthetic tree.
   - Step 1a hit two stop conditions, a missing subagent `CLAUDE.md` line and a rule load in `bare`, and so forced this revision.
   - **1c**, local and unpaid, ran on the first PR's branch before that PR's last dispatch. It timed two synthetic source repos under the system temp dir, each on its own, with four concurrent `build_task_fixture` builds through `scripts/venv-run.sh python3`:
     - 12,500 text files totaling 100 MB, the corner the bounds admit;
     - 17,500 text files totaling 100 MB.
   - The harness passes `timeout_s=600`, ten times the 60 s stop line, so a slow build records its time instead of timing out. The figure is a judgment.
   - Step 1c records each tree's file count, byte total and slowest build in row 106. The Fixture rule sets `FIXTURE_GIT_TIMEOUT_S` from the corner.
   - Afterwards it deletes both source repos and every fixture and `baseline.git` copy it built.
   - Stop and bring step 1c's timing to the engineer before the last dispatch when either holds:
     - the corner's slowest build exceeds 60 s, so the accepted 12,500 default fails the Fixture rule (row 129);
     - the 17,500-file tree's slowest build exceeds 60 s, so the default sits within 40% of the unexplained slowdown.
   - **1b** runs on the follow-on branch before its Phase 3 dispatch. It settles rows 31, 94, 115, 117, 120, 132, 138 and 146 before Phases 3 and 4 depend on them. It makes three paid runs on one one-line edit task to a `.py` file, which this repository's Python rule matches, so that file is the probe's rule-matching file (row 145). Its steps, in the order they run:
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
   - Stop and re-plan with the engineer before Phase 3 when any of these holds:
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
3. For each PR's whole change, `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py`. Both PRs edit `select-tests.py`, one of its global trigger paths, so it selects the full suite on its own (row 40). Then run `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/`.
   - After each PR's first CI run, compare the `tests` job's duration with the latest `main` run's, against the 25-minute `timeout-minutes` cap (row 89).
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
   - In their own terminal, before and after that `smoke`, the engineer lists the account config dir two levels deep, outside `projects/` and `sessions/`, with each file's size, and reads names only, never a file's contents. Every name that appeared and every file that grew is either one of row 79's known appenders or a file added to the cleanup before any `run`.
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
- Charging, at resume, a launched attempt that a hard kill left with no spend line. After each hard kill, up to one such attempt per worker thread (`run_skill_evals.DEFAULT_WORKERS`) stays uncharged. The undercount adds up over kills, each adding at most that many times the nominal per-run cap (row 65). The resume prints the count, and the README lists this as accepted residue (row 155).
- Telling a usage-limit, auth or overload event apart from a task-specific failure. Such an event that fails every run of two blocks marks two tasks no agent really tried complete and drops them, and how `claude -p` behaves at a usage limit is unverified (row 153).
- A stop for failures on alternating tasks, which never make two failed blocks in a row. Only `--cumulative-stop-usd` bounds them (row 153).
- `summarize` figures beyond the primary outcome, the docstring outcome and the diagnostics: comment-plus-docstring lines per added code line, blocks per 100 added code lines, mean lines and words per block, the block-length histogram, the Python trailing-comment count, the base-density split (row 64), the mean first-turn prompt size, the per-repo breakdown, and each account's cap-charged share of spend. The records keep what each needs, so a later change adds any of them with no rerun (row 157).
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
- A probe of row 121's premise, such as a delayed second hook. The engineer accepted dropping it (rows 134, 135), and the README states the residual.
- A scanned-set control and a declared selection row for `review_bench`'s own import-closure guard, which has the gap Phase 5a closes for the replay package. It is outside this plan's files (Axis 1).
- A per-run check that a `toolkit` subagent received the account's `CLAUDE.md`. The hook logs no line for an inherited `CLAUDE.md` (row 18), so `smoke`'s block calibration checks inheritance once per account.
