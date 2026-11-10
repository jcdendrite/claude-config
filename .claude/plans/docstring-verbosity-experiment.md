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

Build `evals/replay_bench/`, a local runner that replays a source repo's past commits as coding tasks. A thin headless dispatcher hands each commit's message to one subagent. Arm `toolkit`, the Context's B, dispatches the production `code-writer` under the account's full config. Arm `bare`, the Context's A, dispatches a fixture-local `bare-writer` with `omitClaudeMd: true` and a one-line body. Every launch gets an environment built from an allowlist. No sandbox contains the subagent, because steered agents are out of scope for this runner (row 47). The runner classifies every line the subagent added as code, comment, docstring, blank or prose, keeps one record and one diff per run under gitignored `evals/replay_bench/.local/`, and prints the paired arm difference over tasks to the terminal only.

### How a replay runs

**Tasks (`run_replay_bench.py tasks`).**
- `tasks --source-repo <path> [--source-repo <path> ...] --trusted-author <email> [--trusted-author <email> ...] --name <task-set>` walks `git rev-list --first-parent <rev>` in each repo, newest first, and writes the qualifying commits to `.local/tasks/<task-set>.json`.
  - `--rev` defaults to `HEAD`, and `--max-count` to 500.
  - `--source-repo` and `--trusted-author` have no default. An empty `--trusted-author` exits 2, since it would match a commit with an empty author email.
  - Each source repo must be the top level of a git repository on this machine, a linked worktree's included (rows 6, 7). `git -C <path> rev-parse --show-toplevel` must print the path's own `os.path.realpath`. A subdirectory, a non-repository path and a bare repository each exit 2 naming the path, because `git archive` run from a subdirectory would build a fixture rooted there (row 96).
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
    - Their defaults, 100 and 100,000, stand unless Verification step 1's build timing lowers them (Fixture).
    - Both bounds come from one `git ls-tree -r -l -z <base>` call once every other rule passes. With `-z`, git prints each path verbatim instead of quoting an unusual name.
    - A non-blob entry, such as a submodule's gitlink, prints `-` for its size, so it counts as zero bytes and one entry and is never read as a file (row 105).
    - Each attempt holds about three copies of that tree, as the fixture's work tree, its `.git` and `baseline.git`, across four worker threads (row 82).
  - every object `tasks` reads with `git cat-file --batch` is present. A `missing` reply, which a partial clone gives for an object it does not hold, skips the commit under its own reason (row 105);
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
- Reading a task file exits 2 on a task that lacks a key, whose ID breaks that grammar, or whose SHAs fail `fixture_repo._validate_sha`.

**Fixture.**
- Every run attempt gets a fresh directory from `msmr._resolved_temp_project_dir(f"{FIXTURE_DIR_PREFIX}replay-")`, using `review_bench.runner.FIXTURE_DIR_PREFIX` so that `RunStore`'s sweep accepts it.
- The runner logs that directory with `RunStore.record_directory(<task-id>, <dir>, "")` before it writes anything into it (row 57).
- It exits 2 when the directory lies under `$HOME`, where ancestor instruction files would load.
- The new `fixture_repo.build_single_commit_repo(source_repo, commit, dest_dir, *, prune, timeout_s)` runs `git init`, extracts the base tree with `_extract_commit_tree`, calls `prune(dest_dir)`, and commits once with `_commit_snapshot`. It keeps the bench's archive-only extraction, so the replayed commit is unreachable from the fixture (rows 27, 28).
- `--fixture-git-timeout-s` bounds every git call that builds the fixture or reads it after the run, in place of the bench's 10 s `_LOCAL_GIT_TIMEOUT_S` (row 87). The calls do no network I/O, so the limit only bounds a hung git.
  - It defaults to the runner's `FIXTURE_GIT_TIMEOUT_S`. The flag lets a loaded machine raise the limit without a code edit. It changes no result, so it stays out of the manifest comparison.
  - Verification step 1 times four concurrent fixture builds on each of two synthetic text trees totaling the `--max-base-mb` default: one of about 10,000 files, and one at the `--max-base-entries` default. `FIXTURE_GIT_TIMEOUT_S` is three times the slowest build across both, rounded up to a whole 10 s (rows 88, 106).
  - The `--max-base-mb` default stays 100 when the 10,000-file tree's slowest build takes 60 s or less, and the `--max-base-entries` default stays 100,000 when the larger tree's does. Otherwise the session halves the slow tree's default and times again until a build fits. The 60 s ceiling and the 100,000 starting point are judgments, not measurements (row 88).
- The replay prune walks the tree without following links. Without it, nested discovery loads a checkout's own instruction files into both arms (rows 23, 24).
  - It removes every entry named `CLAUDE.md`, `CLAUDE.local.md` or `AGENTS.md`, matched case-insensitively, at any depth.
  - It removes every directory named `.claude` at any depth, and the root `.mcp.json`, each matched case-insensitively.
  - It unlinks a matching entry that is a symlink, a nested `.claude` link included, never its target.
  - Extraction refuses a link with an absolute target, a link that resolves outside the tree, and a link at the root `.claude` path, and `tasks` skips any commit whose base tree holds one (Tasks, row 103).
  - After its removals, the prune resolves every remaining symlink with `os.path.realpath` and raises when one resolves outside the fixture. A link chain can pass both the lexical skip and a tar filter that does not normalize link targets, and an agent's write through such a link would escape the diff (rows 83, 103, 108).
  - A prune or an extraction that raises, an `UnsafeFixtureConfigError` included, fails that run as `fixture-error` before any launch (Retry).
- After that commit the runner writes `.claude/worktree-optout` and `.claude/agents/bare-writer.md` into every fixture, both arms alike, and adds `/.claude/` to `.git/info/exclude`. The opt-out keeps machine-level worktree enforcement from denying the agent's writes (rows 21, 22). The exclude keeps both files out of the diff.
- The runner then copies the fixture's `.git` to `<attempt dir>/baseline.git`. The attempt dir is `.local/campaigns/<campaign-id>/attempts/<task-id>-<arm>-<n>-<attempt>/`. Every post-run git call uses that copy, so the diff compares the work tree with the pristine base whatever the agent did to the fixture's own `.git`, a commit, a reset or an edited `.git/config` included (rows 49, 50).

**Arms (`replay_bench/arms.py`).** An arm is data: a name, the agent the dispatcher names, and the instruction loads its subagent must and must not show.
- `bare` dispatches `bare-writer`.
  - Its frontmatter copies `tools`, `model` and `effort` from the account's `<config-dir>/agents/code-writer.md`, read with `msmr.agent_frontmatter_tools`, `msmr.agent_frontmatter_model` and an `effort` reader over `msmr._frontmatter_block`.
  - It adds `name: bare-writer`, `description: Implements the change the prompt describes.` and `omitClaudeMd: true`.
  - Its body is the one line `You implement the change the prompt describes, in the current repository.`
- Each arm names the fixture-file loads it allows. `bare` and `toolkit` allow none.
- `toolkit` dispatches `code-writer` by name, which resolves to the account's stowed user-scope agent. The `SubagentStop` self-review hook, whose matcher is `code-writer`, therefore fires as it does in production and never fires for `bare-writer` (row 13).

Both arms send the same inner prompt. Its delimiters differ from the `<<<` and `>>>` lines that `build_dispatcher_prompt` wraps it in:

```text
Implement the change described between the BEGIN and END lines below in this repository. The original change touched these paths: <changed paths, comma-separated>. Leave your changes in the working tree, uncommitted. Do not install packages or run anything that needs network access. When you finish, reply with the paths you changed.

BEGIN CHANGE DESCRIPTION
<brief>
END CHANGE DESCRIPTION
```

**Account preflight.** Before any launch, the runner probes each source repo once. It refuses the repo, with exit 2, on any failure below.
- The probe is `direnv exec <source-repo> <sys.executable> -I -c <snippet>`, run from a fresh empty temp dir. The snippet prints JSON holding its working directory, the value of `CLAUDE_CONFIG_DIR`, and a sha256 of each environment variable's value, keyed by name.
- The runner compares those hashes with its own environment's. It prints, as names only, each variable the `.envrc` set or changed, other than `CLAUDE_CONFIG_DIR` and direnv's own `DIRENV_` variables, since the launch passes none of their `.envrc` values. A dropped provider-selecting variable can leave a launch authenticating through the config dir's own login instead of the route the `.envrc` selects (row 70). The runner never prints or stores a hash.
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
  - the readiness check below fails.
- The readiness check runs `claude --version` behind the exact launch prefix from Launch environment, from the probe's temp dir. It takes its limit as a `timeout_s` parameter, which `preflight` reads from `review_bench.runner._ENVIRONMENT_READ_TIMEOUT_S` through its module at call time, a constant whose stated purpose includes a hung `claude --version` (row 72). The check shows that `claude` resolves on the allowlisted `PATH` and starts. It does not show that the account is logged in, so `smoke` is the first check that does (row 70).
- The readiness check and every launch report a failure in the probe's shape: the repo, then `exit <n>`, `timed out after <limit> s`, or `_os_error_detail`'s text. This covers a readiness refusal, a launch exception's `missing_detail`, and a `LaunchAbortedError`, whose text is fixed (row 98).
  - None carries the argv, the child's output or an environment value, because the argv holds every allowlisted value, a proxy URL's credentials included.
  - The readiness check does not use `_read_environment_field`, whose error text carries the command and the child's stderr (row 58).
- It prints each repo's resolved config dir and the probe's wall clock. It stores and prints no other environment value.- `preflight --tasks <task-set>` runs only this step and launches nothing.

**Launch environment (`replay_bench/launch.py`).** The child's environment is built from a named allowlist, not inherited (rows 51, 52). Inherited, it would hand the subagent's Bash the runner's own session variables, such as another account's token, and route an honest agent's command to the wrong account.
- The allowlist, kept as data:
  - `PATH`, `HOME`, `USER`, `LOGNAME`, `SHELL`, `TMPDIR`, `LANG`, `LC_ALL` and `LC_CTYPE`;
  - `HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY`, `NODE_EXTRA_CA_CERTS` and `SSL_CERT_FILE`, which a launch behind a proxy or a private CA needs to reach the API (row 70).
- The launch argv starts with `/usr/bin/env -i`. Then comes `NAME=value` for each allowlisted variable the runner's environment sets, plus `CLAUDE_CONFIG_DIR` when the probe found it set. A variable the runner does not set is left out, never passed empty.
- Nothing from the source repo's `.envrc` reaches the child besides `CLAUDE_CONFIG_DIR`, and the `.envrc` runs once per repo, at preflight.
- `HOME` passes, so file-based credentials such as `~/.config/gh`, `~/.aws` and `~/.ssh` stay reachable from the agent's Bash and are not routed by account.
- `env` replaces itself with `claude`, so `msmr._run_claude_to_completion`'s group kill still reaches the child (row 44).

The `--settings` JSON carries one `InstructionsLoaded` command hook and nothing else (rows 17, 19). The hook is `payload=$(cat); printf '%s\n' "$payload" >> <log>`, where `<log>` is the shell-quoted `<attempt dir>/instructions-loaded.jsonl`. It emits each event in one write, so two hooks firing at once cannot split one payload from its newline (row 76).

Nothing contains a subagent beyond `acceptEdits`' working-directory scope and the account's own permission rules (rows 25, 26, 47). The path checks under Validity guard the measurement, not the machine: they find a write or a path outside the fixture after it happened.

**Launch.** Each attempt runs the `env -i` prefix, then `review_bench.runner.build_dispatch_command(build_dispatcher_prompt(<arm agent>, <inner prompt>), model_id=..., session_id=..., budget_cap_usd=...)` with two additions:
- `--permission-mode acceptEdits`, which both arms' subagents inherit (rows 25, 26);
- the `--settings` JSON above.

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

The Bash check splits each command with `shlex`, and resolves `~` against the home and relative tokens against the fixture.
- The run's record keeps every Bash command, and every absolute token outside the fixture that no rule above fails.
- A command `shlex` cannot split is recorded as unparsed.
- A `tool_use` whose command or target path is missing or not a string, and a token holding a NUL byte, are recorded as unparsed and never resolved.
- The check reads literal paths only, so it detects and does not contain.

Each run also records how many `tool_result` entries carry a permission-rule `non_execution_kind`, which a hook denial and a permission-flow denial both carry (row 48). A denied write can empty one arm's diff without failing validity, so `smoke` prints the count and `summarize` reports it by arm.

**Instruction-load check.** The runner reads its own `<attempt dir>/instructions-loaded.jsonl` as untrusted input. It uses the `file_path` values only to classify them.
- A line with no `agent_id` key is the dispatcher's, and a line whose `agent_id` is a non-empty string is the subagent's (row 53).
- The run fails as `instrument-log-invalid` on any of these:
  - a line that does not parse as a JSON object, a torn last line included;
  - an `agent_id` that is present but null, empty or not a string;
  - a `session_id`, `file_path` or `load_reason` that is absent or not a string;
  - a `file_path` holding a NUL byte, on which `os.path.realpath` would raise;
  - a `session_id` other than the run's;
  - lines carrying more than one distinct `agent_id`.
- A `file_path` reaches `os.path.realpath` only once its line passes every check above.
- The run fails as `instrument-missing` when the log is absent or empty, or holds no dispatcher `session_start` load of the account's `CLAUDE.md`. That load is the positive control that the hook fired.
- The check resolves every loaded path and every toolkit path with `os.path.realpath` before comparing them, so a stowed file reported by its link target still classifies.
- Each loaded path falls in one class: toolkit (the account's `CLAUDE.md`, or a file under its `rules/`, `agents/` or `skills/`), fixture (under the fixture), or other.
- `bare` fails as `arm-leak` on any subagent load outside the fixture (row 16).
- `bare` also fails as `arm-leak` when a Read, Grep, Glob or Bash path resolves to a toolkit file, a read `toolkit` makes by design (row 55).
- `toolkit` fails as `arm-config-missing` when no subagent load is the account's `CLAUDE.md` (row 18). That load is also the evidence that subagent loads reach the log at all, so `smoke` requires a valid `toolkit` run before it trusts any `bare` run.
- Either arm fails as `fixture-instruction-load` on a fixture-file load its arm does not allow.
- Duplicate loads of one file are counted, never failed.
- Every run records its subagent loads by class, with each path relative to the config dir or the fixture.
- The run fails as `account-hooks-missing` when `<config-dir>/sessions/` holds no non-symlink file whose first line is the run's session ID. That file shows that the account's hooks ran beside the `--settings` hook, so `toolkit` still carried its `SubagentStop` hook (rows 19, 54).

**Retry.** A failed attempt retries once on a fresh fixture, per `review_bench.runner.ATTEMPTS_PER_RUN`, and then records the run missing with its reason.
- A retried run's cost and token figures sum both attempts' `extract_result_usage`.
- An attempt with no cost figure, such as a timeout, a kill or a launch exception, is charged at `--budget-cap-usd` (row 65).
- Two failures record the run missing at once, because a retry would fail the same way on the same input:
  - `fixture-error`: building the fixture raised, before any launch. An extraction's `UnsafeFixtureConfigError` lands here too, never as a `main()` exit 2, so the campaign halts through the per-arm stop with exit 1 and `--skip-task` can pass the task;
  - `analysis-error`: the diff, the classifier or a check raised after the launch, or a post-run git call failed or timed out. The run keeps the attempt's cost.
- When either failure comes from a git timeout, its `missing_detail` reads `git timed out after <limit> s; raise --fixture-git-timeout-s`, naming the limit in force.
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

**Cleanup.** Every attempt ends in a `finally`, whether it was valid, invalid, timed out, raised or interrupted. A valid attempt captures its diff and metrics first. The `finally` deletes:
- the fixture and the attempt's session store;
- the attempt dir;
- the `<config-dir>/sessions/` file from the instruction-load check, after it checks again, at delete time, that the entry is a non-symlink file whose first line is the run's session ID.

A retry starts only after the failed attempt's `finally` has run.

**Campaign (`run`, `smoke`).** Each task is one block, run in task-file order.
- A block reads `review_bench.runner.read_environment_record` at its start and its end. Its checkout root is the parent of the account's resolved `CLAUDE.md`.
- Each account's `EnvironmentReference` is built from the reading the manifest pinned for that account, so drift halts the campaign across a resume too (row 73). A drift halt prints the block's attempt count and its spend from the spend file.
- `build_block_plan(task_id, arm_names, k, seed)` orders the block's runs, and `run_skill_evals.DEFAULT_WORKERS` threads run them.
- Once the block's end reading matches, the runner writes one `ReplayRunRecord` JSON per run to `.local/campaigns/<campaign-id>/runs/<task-id>/<arm>-<n>.json` with `defects.atomic_write_text`. It writes each diff beside its record, then calls `RunStore.mark_block_complete`.
  - A block rerun after a crash overwrites its own files, so reading needs no dedupe.
  - A crash before that point loses the block's records, at most `2 × k` runs with up to two attempts each. The spend file still holds their cost. The plan accepts the lost records so that each block's drift check stays whole.
- After each complete block, `run` prints one line: the task ID, the ok and missing counts per arm, the block's spend, and the cumulative and account totals against their stops.
- `ReplayRunRecord` keeps `status`, `missing_reason` and `missing_detail` in the bench's meanings, so `count_outcomes`, `all_runs_missing` and `systemic_failure_message` apply to it as they are.
- `RunStore(.local/campaigns/<campaign-id>/store)` supplies the lock, the write-ahead entries, and the resume sweep (row 43).

**Spend file.** `.local/campaigns/<campaign-id>/spend.jsonl` holds one JSON line per ended attempt: the task ID, arm, run index, attempt number, account key, session ID, cost, and whether that cost is a cap charge.
- The runner appends each line with one `os.write` on an `O_APPEND` descriptor, under a lock its worker threads share.
- A crash mid-append can leave a torn last line. A resume repairs it with `review_bench.runner._repair_torn_tail` before its first append, so the torn bytes never fuse with the next line (row 86).
- A line that still does not parse counts at `--budget-cap-usd` against the cumulative stop and every account stop.
- A resume's cap-charge line for a swept entry cannot know the attempt's arm, run index or attempt number, since a write-ahead entry holds only a task ID, a directory and a session ID (row 85). Those three fields hold JSON `null`, and the reader accepts `null` there only on a cap-charge line.
- That line carries the swept entry's session ID, so a later resume finds the ID carried and charges it no second time.
- `run`'s stops sum this file, never the run records.

A resume does these things, in order, before its first launch:
- it exits 2 naming the repo when the probe resolves a source repo to an account key other than the manifest's;
- it compares each account's current environment reading with the manifest's pinned one, and exits 2 naming the account and the field that differs;
- it repairs the spend file's torn tail;
- it runs `sweep_abandoned` once for each account projects root in the campaign. Each call returns the whole store's pending entries (row 85), so the resume merges them into one set and reruns each swept task's block once, unless the task is skipped. A skipped task's swept entries are still charged and cleaned below;
- it appends one cap charge for each distinct non-empty session ID in that set that no spend line carries, since that attempt ended before recording its cost. The charge goes on the account of the entry's task's source repo.
  - An entry with an empty session ID logs a fixture before its launch (row 57), so it is never charged.
  - An attempt whose torn spend line the repair removed has no spend line left, so this step charges it once.
- it prints the spend file's total for blocks not yet complete;
- it deletes each swept entry's `<config-dir>/sessions/` file, matched by first line, under that entry's own account only;
- it deletes each entry of the campaign's `attempts/` whose name parses and that is a non-symlink direct child of `attempts/`. A name parses as `<task-id>-<arm>-<n>-<attempt>` when all of these hold:
  - it starts with a task ID in the task set and a hyphen;
  - it ends with a hyphen, a run index and an attempt number, each in range;
  - what lies between is an arm name in the manifest, so an arm name may hold hyphens;
- it prints the count of `attempts/` entries deleted, and the count left because no name matched.

`run`'s inputs:
- `--k` and `--cumulative-stop-usd` are required, with no default for either.
- `--account-stop-usd` caps each account's spend, and defaults to the cumulative stop.
- `--campaign-id` must pass `identifiers.validate_campaign_id`.
- The first start pins a manifest of the task-set hash, arms, k, seed, model ID, each source repo's account key, and each account's environment reading, read before the first launch. A resume whose task-set hash, arms, k, seed or model ID differs exits 2.
- `--fixture-git-timeout-s` stays out of the manifest and its comparison, since it changes no result.
- The manifest also holds each task's recorded halts and skips (Repeated halts). Neither enters the task-set hash or the resume comparison, and the runner rewrites the manifest with `defects.atomic_write_text` when either changes.

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
- On a resume, the printed run count covers only the incomplete, non-skipped blocks. The campaign's ceiling there is the spend file's total so far plus those blocks' ceilings.
- Before each block, it stops when the block's ceiling would carry either total past its stop:
  - the spend file's total, against the cumulative stop;
  - the block's account's spend, against the account stop.
- Either stop halts the whole campaign, so a resume with a raised stop goes on in task-file order.
- After each block, it stops when any arm has no valid run in it, by `all_runs_missing` over that arm's records, and prints `systemic_failure_message` for the task and arm. The block stays incomplete, so a resume reruns it. `all_runs_missing` over the whole block would miss one arm failing alone, as `bare` does on a machine with a managed-policy `CLAUDE.md` (row 74).

`run`'s halts and exit codes:
- Every halt prints its reason: environment drift, the cumulative stop, the account stop naming its account, or the per-arm stop naming its task and arm.
- `run` then exits 1, the bench CLI's code for a run that stopped short, and keeps exit 2 for refusals (row 97).
- A run that completes every task not skipped exits 0.

**Repeated halts.** A task-specific failure, such as a fixture build that raises or a task whose every attempt times out, halts its block the same way on every resume. `systemic_failure_message` offers only that resume (row 95), and a new campaign would discard the completed paid blocks. So:
- each per-arm stop records the halt in the manifest, with each failing arm's missing reasons;
- a per-arm stop on a task that already has a recorded halt prints `halted again`, that task's missing reasons, and the `--skip-task <task-id>` flag. The first per-arm stop on a task prints neither;
- `run --skip-task <task-id>`, repeatable, records the skip in the manifest. It exits 2 for an ID that is not a task in the task set, or whose block is complete;
- `run` never reruns a skipped task, on that resume or any later one, a swept one included.

`smoke --tasks <task-set> --task-id <id> [--k 1]` runs one block under a fresh `smoke-` campaign ID. `--task-id` must match the task-ID grammar.

For each run, `smoke` prints:
- status and reason;
- subagent instruction loads by class;
- recorded Bash commands, out-of-fixture paths and out-of-session paths;
- the permission-denial count;
- the `non_file_entries`, `ignored_paths` and `undecodable_files` tallies;
- metrics, cost, fixture-build seconds and wall clock.

`smoke` also prints two lists under the account config dir, outside `projects/` and `sessions/`, and never reads a file's contents:
- every file or directory name that appeared during the smoke, two levels deep;
- every top-level file that already existed and grew, compared by `stat` alone, since a known appender adds rows to an existing file (row 79).

`smoke`'s last line reads `instrument: ok` only when all of these hold:
- each arm has a valid run;
- no run failed as `arm-leak`, `arm-config-missing`, `instrument-missing`, `instrument-log-invalid` or `account-hooks-missing`.

Otherwise the line names the first failed condition, and `smoke` exits 2. On `instrument: ok`, `smoke` writes the account's smoke-pass record with `defects.atomic_write_text` to `.local/smoke-passes/<account digest>.json`. The record holds the account key, its environment reading, the model ID and the arms `smoke` validated. The digest is the first 16 hex digits of the account key's sha256.

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
- It prints counts by arm, status and missing reason, and the counts of complete blocks, skipped blocks, and other incomplete blocks.
- It lists each skipped task with the missing reasons its halts recorded.
- It prints the spend file's spend by account, with the share of each account's spend that is cap charges.
- The primary outcome, fixed before any run, is comment-plus-docstring words per added non-test code line (row 63).
  - For each task and arm it is a ratio of sums: the words over that arm's valid runs of the task, divided by their non-test code lines.
  - `summarize` reports the paired difference `toolkit − bare` over tasks with a valid run and at least one non-test code line in both arms. Every other task is left out of the pairs and counted by why. It uses `analysis.bootstrap_interval` over tasks as singleton clusters, and prints no interval for fewer than two paired tasks (row 75).
  - It repeats that difference for paired tasks strictly below the median base density, by `statistics.median`, and for the rest, ties at the median included (row 64).
  - The median is over the non-null densities. A paired task with a null base density stays in the primary difference, falls in neither half, and is counted.
- A null primary difference shows only that the writing agent's instructions leave its first draft's comment density unchanged on these tasks. It cannot show that the verbosity is built-in, because no arm runs the review loop through which the toolkit also shapes comments (row 62).
- Secondary outcomes, each per arm beside the task's reference metrics:
  - comment-plus-docstring lines per added code line;
  - blocks per 100 added code lines;
  - mean lines and mean words per block;
  - a block-length histogram of 1, 2, 3, and 4 or more lines;
  - Python docstring lines per 100 added code lines;
  - the Python trailing-comment count, the unclassified-line share, and the count of Python files `ast.parse` rejected (row 35);
  - the count of runs with any permission-rule denial;
  - the `non_file_entries`, `ignored_paths` and `undecodable_files` tallies.
- It breaks every figure down by source repo label, the task ID's first part.
- A run with no non-test code line still enters its task's sums, and also counts in a separate tally by arm.

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

`no-durable − toolkit-copy` isolates the rules. `toolkit − toolkit-copy` measures the route and the hook together. Adding the pair takes arm entries and fixture extras only, with no runner change. `run`'s smoke gate checks the record's arms, so the pair needs its own passing `smoke` on each account before any paid block.

### Open decisions for the engineer

- **D1 (resolved).** For arm A's shape, the engineer selected "Subagent for both arms (Recommended)" (row 3).
- **D2 (arm C only).** The route that delivers an edited `CLAUDE.md`:
  - (a) `claudeMdExcludes` in the fixture's `.claude/settings.json` naming the account's `CLAUDE.md`, plus the edited copy as the fixture's root `CLAUDE.md`. Recommended, because it keeps the `CLAUDE.md` channel. Whether `claudeMdExcludes` reaches a user-scope file is unverified, and one `smoke` with the instrument settles it.
  - (b) `omitClaudeMd: true` with the edited text inlined into the agent body, which is a system-prompt channel.
  - (c) A separately logged-in config dir per account. Each needs the engineer's own login and a stow, and copying credentials is barred (row 11).
- **D3 (arm C only).** What "removes only the §Durable text rules" removes. Both readings are correct:
  - (i) the `### Durable text` subsection of `CLAUDE.md` alone;
  - (ii) every channel stating those rules to `code-writer`: that subsection, the two `code-writer` baseline sub-bullets that restate it ("Write any comment as a one-line durable fact" and "Don't restate what the code already shows"), the last bullet of its self-review step 6, and the comment-discipline row of its self-review table.
  - Recommended: (ii). `code-writer` restates the rules and reads `comment-discipline-reviewer` from disk, so (i) can read as "no effect" from redundancy alone.
- **D4 (resolved).** The engineer selected "Proceed anyway" at each projected diff, most recently about 6,880 lines (row 110), after about 6,200 (row 99) and about 5,100 (row 84).
  - (a) Keep one PR, as rows 68, 84 and 99 chose.
  - (b) Split at the Phase 2 to Phase 3 boundary. The first PR holds Phases 1 and 2 with their `select-tests.py` rows, about 1,800 lines, and needs no live probe. The second waits on Verification step 1 in any case.
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
- Dropping a repeatedly halting task by editing the task set: the manifest pins the task-set hash, so the edit forces a new campaign that discards every completed paid block.
- A stated minimum Python patch level for the tar filter's link normalization, instead of the prune's realpath check: no CPython changelog was read, and the check holds on any interpreter for about ten lines (row 108).
- Raising `_LOCAL_GIT_TIMEOUT_S` itself: it widens the hang bound for every review-bench caller, none of which this change needs to alter. Keeping it and lowering `--max-base-mb` until a build fits 10 s instead would drop the larger repos from the task source.
- Reading a changed file's post-change text from its work-tree path: an agent-made symlink would point that read at a FIFO or at a file outside the fixture, where a `baseline.git` blob is exactly what `add -A` indexed (row 77).
- A spend reservation at each block's nominal ceiling: it is simpler, but it charges a block its whole ceiling until the block ends, so the stops would act on spend that never happened.
- No smoke gate on `run`, with the per-arm stop as the only guard: a broken premise would first surface in a paid block of `2 × k` runs, and nothing would tie the engineer's read of `smoke`'s printout to the environment `run` uses.

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
16. [unverified] Whether `omitClaudeMd` also drops `~/.claude/rules` and nested `.claude/rules` loads is undocumented, so the instrument checks every `bare` run. A leaked rule is not neutral, because that rule sets a comment-length convention. [verified: `claude/.claude/rules/shell-script-conventions.md:49-52`]
17. [verified: `docs/case-studies/claude-md-glob-zero-segment.md`, its five `-p` trials and the clean trial's two logged lines] An `InstructionsLoaded` command hook fires under `claude -p` and logs `load_reason` and `file_path`.
18. [unverified] The hook also fires for loads inside a subagent. Verification step 1 checks it, and every `toolkit` run must show a subagent `CLAUDE.md` load.
19. [unverified: cli-reference.md's "additional settings" wording, as relayed; not run] `--settings` adds the instrument hook to the account's hooks rather than replacing them. A replacement would strip the `code-writer` `SubagentStop` hook from `toolkit`. Every run checks it through a known account hook's side effect (row 54), which needs no transcript attachment.
20. [unverified] `direnv exec DIR CMD` runs CMD in the caller's working directory. The preflight probe checks it for each repo and refuses otherwise.
21. [verified: `README.md` "Worktree enforcement", lines 341-347; `claude/.claude/hooks/require-worktree-for-file-writes.sh:7-8`] Machine-level worktree enforcement denies Edit and Write in a repo's main tree unless `.claude/worktree-optout` is present. [verified: `claude/.claude/hooks/require-worktree-for-file-writes.sh:6`] The opt-out loses only to a committed `.claude/worktree-required`. [verified: `claude/.claude/hooks/require-worktree-for-git-writes.sh:5-8`] The opt-out also exempts the fixture from the git-writes gate, which reads the same markers. By this plan's design, the prune removes a committed `.claude/worktree-required` with every `.claude` directory (Fixture).
22. [verified: `claude/.claude/hooks/require-plan-review.sh:13-15`] The plan-review gate passes a repo with no `.claude/plans/`, which the prune also removes.
23. [verified: `claude/.claude/hooks/tests/test_claude_md_excludes.py:1-21`] In a `claude-config` checkout, nested discovery loads `claude/.claude/CLAUDE.md` and the stow-source rules under `claude/.claude/rules/`. This repo suppresses them only through its root `.claude/settings.json`, which the prune removes, so the prune must remove the nested files itself.
24. [unverified] Nested `.claude/agents/` and `.claude/skills/` directories can be discovered and could shadow the account's agents. The prune removes every `.claude` directory, so the question never arises.
25. [verified: `CLAUDE.md` §Working Style "Dispatching cannot clear a denial your child inherits"] A subagent starts in its dispatcher's permission mode, so `--permission-mode acceptEdits` reaches both arms' subagents.
26. [unverified] Under `-p`, `acceptEdits` approves Edit and Write, and possibly some filesystem Bash commands, inside the working directory only. Every other permission prompt is denied. Verification step 1 checks both.
27. [verified: `evals/review_bench/fixture_repo.py:121-126`] Extracting with `git archive` into a fresh repository keeps every later commit unreachable from the fixture.
28. [verified: `evals/review_bench/fixture_repo.py:267-284`] `build_two_commit_repo` commits the head tree, so it cannot build a replay fixture.
29. [verified: `evals/review_bench/fixture_repo.py:8-9`; `evals/README.md` "Building a later arm"] The bench installs an arm's agent as a fixture-local `.claude/agents/` file.
30. [verified: `evals/review_bench/runner.py:852-991`] `evaluate_run_validity` checks any thin-dispatcher run, and its live-checkout check covers the changed paths passed to it. Its path checks see only Read, Grep and Glob calls. [verified: `evals/review_bench/runner.py:100`]
31. [unverified] `code-writer`'s `model: sonnet` resolves to `REVIEWER_MODEL_ID`, the `--model-id` default. If it does not, every `toolkit` run fails `model-mismatch` at `smoke`.
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
53. [verified: `claude/.claude/hooks/enforce-marker-script-shape.sh:83-87`, quoting the hooks reference's common input fields] A hook's `agent_id` field is present only when the hook fires inside a subagent call. [unverified] `InstructionsLoaded` payloads carry the common fields `session_id` and `agent_id`. Verification step 1 checks it.
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
76. [unverified] One `printf '%s\n'` of the captured payload reaches the log in one `write` call. Whether the harness ever runs two `InstructionsLoaded` hooks at once is also unchecked. Phase 4's parallel test checks the first, and Verification step 1 records the second.
77. [unverified] `git add -A` stores a symlink as its link text and skips a FIFO without opening it. Phase 4's hostile-tree test checks both.
78. [unverified] `git add -A` leaves out an untracked path that the work tree's ignore rules match, and fails on a nested repository that has no commit. Phase 4's tests pin both outcomes.
79. [verified: `claude/.claude/hooks/send-back-skipped-self-review.sh:40-41`; `docs/session-config-keys-log.md:3`] A replay session adds to account files that already exist. `toolkit`'s `SubagentStop` hook writes `.code-writer-self-review.log` and `.code-writer-self-review.d` in the config dir. Every session start adds a row to `.session-config-keys-log.jsonl`.
80. [verified: `claude/.claude/scripts/select-tests.py:812-832,876-879`; `claude/.claude/scripts/tests/test_select_tests.py:425-431,1092-1105,2138-2149`] A test that reads the real agents directory through a production function is a hand-audited `AGENTS_DIR` reader. `MEASURE_SUBAGENT_MODEL_RESOLUTION_TEST` carries the wiring one needs:
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
88. [unverified] Three times the slowest of four concurrent builds at the `--max-base-mb` default bounds a hung git without failing a slow but healthy build. Verification step 1 measures the build time. The multiplier and the 60 s ceiling are judgments.
89. [verified: `pyproject.toml:31`; `.github/workflows/tests.yml:26-34,179,186`; `evals/test_review_bench_runner.py:793,810`] CI runs the tests not marked `timing` under `-n auto` and the `timing` tests serially, inside a 25-minute job cap whose comment says to re-derive it whenever either pass grows. The marker is for a test that asserts a wall-clock bound or needs serial isolation.
90. [verified: `evals/conftest.py:30-47`] The shared autouse fixture stubs `review_bench.runner.default_live_checkout_roots` by module attribute, and only once some test has imported `run_review_bench`. A `from` import binds before the stub, and a process that never imported `run_review_bench` gets no stub.
91. [verified: `evals/test_review_bench_cli.py:4257,4280-4302`] The bench tests its CLI's owner-only umask and its tightening of a pre-existing local dir to 0700.
92. [unverified: the git-log documentation, not read this session] With `--no-use-mailmap`, the `%ae` and `%ce` placeholders print the commit's raw identities, whatever the repo's `.mailmap` or `mailmap.file` says. Phase 2's mailmap test checks it.
93. [unverified: POSIX not read this session] An empty `PATH` entry names the current directory, and a relative entry resolves against it. The launch's current directory is the fixture.
94. [unverified] The subagent's Bash sees only the launch's allowlisted variables plus those the shell and Claude Code set, with no login profile re-exporting another account's variable. Verification step 1's names-only listing checks it.
95. [verified: `evals/review_bench/runner.py:1600-1608`] `systemic_failure_message` tells the engineer that resuming under the same campaign ID reruns the block.
96. [verified: `evals/review_bench/fixture_repo.py:127-130`] `_extract_commit_tree` runs `git archive <commit>` with the source repo as its working directory. [unverified: the git-archive documentation, not read this session] Run from a subdirectory, it archives only that subdirectory, while the task's changed paths are relative to the repo root.
97. [verified: `evals/run_review_bench.py:880,2339-2342`] The bench CLI returns 1 when a run stops short, such as on a held store lock, and 2 for a refusal.
98. [verified: `evals/measure_subagent_model_resolution.py:682-683,719,741`] `LaunchAbortedError` is raised only with fixed text.
99. [engineer-verified: "Proceed anyway"] Superseded by row 110 for the current figure. The engineer chose to keep an earlier revision of this plan as one PR at its `Projected diff:` of about 6,200 changed lines, over the 400-line threshold.
100. [engineer-verified: "Skip up front in `tasks` (Recommended)"] For a base tree holding a link the fixture's extraction refuses, the engineer chose to skip the commit up front in `tasks`.
101. [unverified] The session's proposal, not the engineer's words: `tasks` counts each refused shape under its own skip reason, so its printout shows what the skip drops.
102. [unverified] The session's proposal, not the engineer's words: `fixture_repo.py` gets no change for this skip. `tasks` reuses `fixture_repo._has_git_path_component`, `_first_path_component_casefolded`, `_BENCH_DIR_NAME` and `_PROJECT_CONFIG_DIR_NAME`, so its predicates match extraction's own.
103. [verified: `evals/review_bench/fixture_repo.py:145-193`] `_extract_archive` raises before any write on a member with a `.git` path component, a link whose target has one, a member under `.bench`, and a link whose own path starts with `.claude`. It extracts through tarfile's `data` filter, which its docstring says refuses a member that resolves outside the destination and a link with an absolute target. [unverified: the Python tarfile documentation, not read this session] The filter resolves a relative link's target with `os.path.realpath`, so it could refuse a link that `tasks`' lexical check passes. Phase 2's parity table pins where the two agree on the running interpreter, and a commit they disagree on fails as `fixture-error`, which `--skip-task` covers.
104. [unverified] The session's extension, not the engineer's choice: the skip also covers the non-link entries extraction refuses, an entry with a `.git` path component and one under a root `.bench`, because each fails every attempt as `fixture-error` the same way a refused link does.
105. [unverified: the `git-ls-tree` and `git-cat-file` documentation, which two reviewers cite and this session did not read] With `-l`, `git ls-tree` prints `-` as the size of a non-blob entry such as a gitlink. Without `-z` it quotes a path that holds an unusual character, and with `-z` it prints the path verbatim. `git cat-file --batch` answers `<oid> missing` for an object the repository does not hold, as in a partial clone. Phase 2's gitlink, non-ASCII and `missing` cases check all three.
106. [unverified] A fixture build's time grows with the base tree's entry count as well as its bytes, so a tree at the byte bound with many small files builds slower than one with 10,000. Verification step 1 times both shapes.
107. [verified: `evals/review_bench/runner.py:388-389,407-424`] `wait_for_session_flush` reads `SESSION_FLUSH_TIMEOUT_S` and `SESSION_FLUSH_POLL_INTERVAL_S` as module globals at call time. It sleeps one poll interval before it can return, and polls to the timeout when the transcript or the sidecar is missing.
108. [unverified: CPython 3.14.10 `tarfile.py:879-893`, as one reviewer read it; not read this session] The `data` filter rewrites a link's target with `os.path.normpath` before it resolves it with `os.path.realpath`. Whether every Python 3.12 patch release does the same is unconfirmed. Without that rewrite, a chain such as `a -> d/e/f/..` with `d/e/f -> ../..` passes both the filter and `tasks`' lexical check yet resolves outside the fixture, so the prune's own realpath check fails it.
109. [verified: `evals/test_review_bench_fixtures.py:941-1000,1089`] The bench's fixture tests build synthetic archives with `_tar_with_link` and its sibling helpers. They pin `.benchmark/`, `docs/.bench/` and `.claude-plugin/` as names extraction allows.
110. [engineer-verified: "Proceed anyway"] The engineer chose to keep this plan as one PR at its revised `Projected diff:` of about 6,880 changed lines, over the 400-line threshold. This supersedes row 99.

**Mechanisms:**
- **M1. Task mining from first-parent commits of top-level repos, with pruned paths dropped, a trusted-author list over raw identities, delimiter skips, base-tree size and entry-count bounds read from a NUL-delimited tree listing, a `missing-object` skip, an up-front skip of base trees the fixture cannot extract, merge-commit briefs, reference metrics and a nullable base density.** anchors: row6, row7, row9, row33, row34, row45, row46, row61, row64, row82, row92, row96, row100, row101, row102, row103, row104, row105, row106, row109.
- **M2. A single-commit archive fixture with a case-insensitive, link-safe prune that realpath-checks every remaining symlink, the worktree opt-out, a pre-launch copy of its `.git`, and a git timeout measured at both base-tree bounds and overridable by flag.** Lighter primitives checked:
  - (a) a `git worktree add` or `git clone` of the base commit is lighter, but both share or copy the object store, so the replayed commit stays reachable (row 27);
  - (b) a bench two-commit fixture fails the same way (row 28).
  - anchors: row21, row22, row23, row24, row27, row28, row83, row87, row88, row103, row106, row108.
- **M3. A thin headless dispatcher in front of one subagent in every arm.** This is heavier than one headless session per run. Lighter primitives checked:
  - (a) a headless main session per arm fails for the reasons in "Why both arms dispatch a subagent";
  - (b) `--append-system-prompt` carrying `code-writer`'s body into a main session delivers that body through a different channel than production's agent body, and the session still carries the Main-session rules.
  - anchors: root, row3, row8, row12, row13, row14.
- **M4. `bare-writer` as a fixture-local agent file that copies `code-writer`'s tools, model and effort.** `--agents` JSON is lighter, but `omitClaudeMd` is documented only as frontmatter. anchors: row12, row15, row29, row80.
- **M5. Per-repo account resolution through one direnv probe that also names the variables an `.envrc` set, a readiness check with a passed limit, a launch environment built from an allowlist, a `PATH` refusal, and failure text that never carries a child's output or the argv.** Lighter primitives checked:
  - (a) the runner's own account for every repo breaks row 5;
  - (b) a `direnv exec` launch prefix hands the agent the runner's whole environment with the `.envrc`'s exports on top (rows 51, 52).
  - anchors: G2, row5, row20, row44, row51, row52, row58, row69, row70, row72, row93, row94, row98.
- **M6. The `InstructionsLoaded` hook log, one write per event, attributed by `agent_id` and checked against a dispatcher positive control, as each run's arm check.** Lighter primitives checked:
  - (a) the model's self-report is not mechanical and would contaminate the run;
  - (b) the subagent's first-turn input-token size cannot name which file loaded, and rules load mid-run on file reads;
  - (c) transcript `nested_memory` attachment records are documented only for interactive sessions, per row 23's source. [verified: `docs/session-config-keys-log.md:59,81`] Whether a `-p` run keeps a hook attachment is untested.
  - anchors: row16, row17, row18, row19, row53, row76.
- **M7. `evaluate_run_validity` unchanged, plus the runner's write-path, Bash-path and account-hook checks, and retry-then-missing.** anchors: row30, row31, row32, row48, row54, row55.
- **M8. Whole-line classification of `baseline.git` blobs by extension table, with Python through `tokenize` and the bench's docstring finder, a test-path rule, and word counts.** anchors: row35, row36, row38, row63, row77, row78.
- **M9. `RunStore` for the lock, write-ahead and sweep, with each fixture logged before it is filled, per-run atomic record files, and cleanup of attempt dirs and session-ID files on every exit path and by validated name on resume.** anchors: row43, row54, row56, row57, row79, row85.
- **M10. One block per task, environment readings and repo-to-account keys against a pinned manifest, a per-attempt spend file repaired at resume, cap charges only for launched attempts with no spend line, written so a later resume sees them, spend guards before every block and a resume ceiling over the remaining blocks, a per-arm stop after it, a recorded skip for a repeatedly halting task, per-block progress with an exit code for every halt, and an arm-keyed smoke pass per account before `run`.** anchors: G3, G4, row2, row37, row65, row73, row74, row85, row86, row95, row97.
- **M11. `summarize` to the terminal only, over complete blocks, with a pre-registered ratio-of-sums word outcome, a base-density split that sets null densities aside, a paired bootstrap, and spend, skip and incomplete-block counts.** anchors: G3, row4, row9, row10, row62, row63, row64, row75.
- **M12. `--permission-mode acceptEdits` on the dispatcher.** anchors: row25, row26.
- **M13. Arm C as a gated pair sharing one route.** anchors: row2, row11, row13.
- **M14. `select-tests.py` wiring that follows the `measure_explore_recall.py` precedent, plus the hand-audited agents-directory reader and the no-relative-import check.** anchors: row39, row40, row67, row80, row81.
- **M15. The `.gitignore` entry in Phase 1, the startup ignore check, the Python and git guards, the owner-only umask, and the README section with its trust warning and residual risk.** anchors: row4, row41, row42, row45, row47, row59, row61, row66, row71, row91.
- **M16. A runner-owned copy of the fixture's pre-launch `.git` for every post-run git call.** Lighter primitives checked:
  - (a) `-c` overrides on the fixture's own `.git` form a denylist that misses any key it does not name;
  - (b) a Python diff of the work tree reimplements `git diff` and its symlink handling.
  - anchors: row49, row50.
- **M17. Test isolation and timing: a local stub of the live-checkout roots, near-zero flush constants set by module attribute, `timing` markers and stated deadlines on the hang-prone and wall-clock tests, `exec`'d sleepers in fakes, and a CI duration check with per-test durations against the job cap.** anchors: row89, row90, row107.

## Critical files

**Dispatch split:** five `code-writer` dispatches, run in order, one per phase. Each phase's output is the next one's input:
- the classifier feeds the task miner's metrics;
- the run checks parse the log and transcript shapes that Verification step 1 captures;
- the runner composes all three;
- the test-selection wiring needs the final file names and imports.

Phase 4 stays one dispatch because the arms, launch settings, runner and CLI share the record, fixture and settings contracts. The session runs Verification step 1 before dispatching Phase 3.

**Phase 1 — classifier and ignore rule.** Verify: `scripts/venv-run.sh pytest evals/test_replay_bench_comment_metrics.py`, then `git check-ignore -q evals/replay_bench/.local/tasks/probe.json`.
- `.gitignore`: ignore `evals/replay_bench/.local/`, under a one-line comment that it holds task files, run records and diffs carrying other repositories' commit text and code.
- `evals/replay_bench/__init__.py` (new): one docstring line naming the package.
- `evals/replay_bench/comment_metrics.py` (new):
  - the extension table and the test-path rule from Approach, as data;
  - added line numbers per path from a `-U0` diff, where a hunk header with no count means one line and a count of zero adds none;
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

**Phase 2 — tasks and fixture.** Verify: `scripts/venv-run.sh pytest evals/test_replay_bench_tasks.py evals/test_review_bench_fixtures.py`.
- `evals/review_bench/fixture_repo.py`:
  - add a keyword-only `timeout_s` to `_run_git`, `_extract_commit_tree` and `_commit_snapshot`, defaulting to `_LOCAL_GIT_TIMEOUT_S` so every existing caller keeps its bound (row 87);
  - add `build_single_commit_repo(source_repo, commit, dest_dir, *, prune, timeout_s)`. It validates `commit` with `_validate_sha` and composes `_run_git(["init", "-q"], ...)`, `_extract_commit_tree`, `prune(dest_dir)` and `_commit_snapshot`, passing `timeout_s` to each;
  - add its directory kind to the module docstring's list. Make no other change.
- `evals/replay_bench/tasks.py` (new):
  - `ReplayTask` with `to_dict` and `from_dict`, and task-set read and write with `defects.atomic_write_text`, where writing validates the task-set name and reading validates each task's keys, ID grammar and SHAs;
  - `TaskSourceError`, raised naming the path for a source repo that is not a top level and for an empty trusted author, which `main()` turns into exit 2;
  - commit selection: the pruned-path drop, the trusted-author rule over raw identities, the delimiter and path-character skips, the size and entry-count bounds from one `git ls-tree -r -l -z` call, the `missing-object` skip, the skip of base trees the extraction refuses through `fixture_repo`'s own path helpers (row 102), and the brief, merge commits included;
  - the per-repo progress lines, and the skip-reason counts, identity counts and largest kept tasks that `tasks` prints;
  - the prune predicate and `prune_instruction_files(dest_dir)`, with its realpath check of every remaining symlink;
  - `build_task_fixture(..., timeout_s)`, which calls `build_single_commit_repo` with that `timeout_s`, writes the opt-out, the `bare-writer` file and the exclude line, and copies `.git` to the attempt dir's `baseline.git`;
  - `build_inner_prompt(task)`, which renders the inner-prompt text from Approach;
  - reference metrics and base density, reading a commit's post-change files and its base files in one `git cat-file --batch` call each, with a null density when the base holds no non-test code line.
  - Every git call runs under `local_git.isolated_git_environment()`.
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

**Phase 3 — run checks.** Verify: `scripts/venv-run.sh pytest evals/test_replay_bench_checks.py`.
- `evals/replay_bench/checks.py` (new):
  - the hook-log parser with its field-shape checks, and the instruction-load check from Approach;
  - the account-hook check under `<config-dir>/sessions/`;
  - the write-path and Bash-path checks over the subagent transcript's `tool_use` entries, reusing `review_bench.runner`'s `live_checkout_leak_targets`, `is_changed_file_leak` and `is_config_dir_leak`;
  - the permission-denial count.
- `evals/test_replay_bench_checks.py` (new). Its log fixtures use the payload shape Verification step 1 captured, with placeholder paths. Cases:
  - `instrument-missing`: an empty log, an absent log, and a log with no dispatcher `CLAUDE.md` load;
  - `instrument-log-invalid`: a torn last line, a foreign `session_id`, and two `agent_id` values;
  - field shapes, each one mutation of a captured line, and each failing `instrument-log-invalid` without raising:
    - `agent_id` null, empty, and a number;
    - `session_id`, `file_path` and `load_reason`, each absent and each a number;
    - a non-string `file_path`, which never reaches `os.path.realpath`;
    - a string `file_path` holding a NUL byte, which never reaches `os.path.realpath`;
  - attribution: a subagent-only `session_start` load, credited to the subagent; a duplicate dispatcher load, not credited to it;
  - a stowed `CLAUDE.md` and `rules/` file reported by link target, still classed toolkit;
  - `bare` with a toolkit rule load, and with an other-class load; `toolkit` with no subagent `CLAUDE.md` load; a fixture-file load;
  - a forged log written into the fixture, never read;
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

**Phase 4 — arms, launch, runner, CLI.** Verify: `scripts/venv-run.sh pytest evals/test_replay_bench_arms.py evals/test_replay_bench_runner.py`.
- `evals/replay_bench/arms.py` (new): the `Arm` record with its allowed fixture loads, the `bare` and `toolkit` registry, and the `bare-writer` file text built from the account's `code-writer.md` frontmatter.
- `evals/replay_bench/launch.py` (new): the environment allowlist as data, the `env -i` prefix, the readiness argv, and the `--settings` JSON with the instrument hook.
- `evals/replay_bench/runner.py` (new):
  - the account preflight, its account key, its `PATH` check and its readiness check, with failure text in the probe's shape;
  - `FIXTURE_GIT_TIMEOUT_S`, under a one-line comment naming its basis, as the `--fixture-git-timeout-s` default, with its value and the `--max-base-mb` and `--max-base-entries` defaults set from Verification step 1's timing;
  - one attempt: fixture, launch, validity, checks, diff, metrics, and cleanup in a `finally`;
  - retry-then-missing, with the cap charge, `launch-no-output`, and `fixture-error` and `analysis-error` without a retry;
  - the spend file and its torn-tail repair at resume;
  - the block and campaign loop with its pinned environment, smoke gate, spend guards, per-arm stop, per-block progress line, halt messages and exit codes, and `ReplayRunRecord`;
  - recorded halts, `halted again`, and `--skip-task`;
  - `RunStore` use, the per-account sweep, and the resume's cap charges with their `null` attempt fields and validated-name deletions;
  - the manifest's repo-to-account pin, the resume's ceiling over the incomplete, non-skipped blocks, and `--fixture-git-timeout-s` outside the manifest comparison;
  - the preflight's names-only printout of the variables an `.envrc` set;
  - `smoke`'s printout and its arm-keyed smoke-pass record, and `summarize`.
- `runner.py`'s reuse from `review_bench.runner`:
  - prompts and launch: `build_dispatcher_prompt`, `build_dispatch_command`, `wait_for_session_flush`, `extract_result_usage`;
  - validity: `evaluate_run_validity`, `own_session_paths_for`, `default_live_checkout_roots` through its module, `_worktree_top_levels`, `session_store_dir_for`, `_os_error_detail`;
  - campaign: `read_environment_record`, `EnvironmentReference`, `build_block_plan`, `RunStore`, `count_outcomes`, `all_runs_missing`, `systemic_failure_message`, `campaign_cost_ceiling`, `remove_tree_reporting_failure`, `_repair_torn_tail`;
  - constants: `FIXTURE_DIR_PREFIX`, `REVIEWER_MODEL_ID`, `REVIEWER_TIMEOUT_S`, `ATTEMPTS_PER_RUN`, `_ENVIRONMENT_READ_TIMEOUT_S`.
- `runner.py`'s reuse from `measure_subagent_model_resolution`: `_run_claude_to_completion`, `_resolved_temp_project_dir`, `abort_launches`, `LaunchAbortedError`, `PER_RUN_BUDGET_CAP_USD`, `agent_frontmatter_tools`, `agent_frontmatter_model` and `_frontmatter_block`.
- `runner.py`'s reuse from the rest of `review_bench`: `analysis.bootstrap_interval`, `identifiers.validate_campaign_id`, `defects.atomic_write_text`, `fixture_repo._validate_sha`, and `local_git`'s `isolated_git_environment` and `DIFF_TEXT_ARGS`.
- `evals/run_replay_bench.py` (new): the subcommands `tasks`, `preflight`, `smoke`, `run` and `summarize`, with `DEFAULT_LOCAL_DIR = EVALS_DIR / "replay_bench" / ".local"`, `--local-dir`, `--skip-task`, `--fixture-git-timeout-s` and `--max-base-entries`. `main()` runs the startup steps in Approach's order. An `UnsafeFixtureConfigError` never reaches it, because an attempt records it as `fixture-error` (Retry). It returns 2 on any of these:
  - `HarnessInvalidatedError` or `TaskSourceError`;
  - a preflight, spend, smoke-gate, skip or local-dir refusal;
  - an environment-pin mismatch;
  - a Python or git below its floor.
- `run` returns 1 on every halt, and 0 when it completes every task not skipped.
- `evals/test_replay_bench_arms.py` (new). Cases:
  - one test method, taking only `self`, reads this checkout's `claude/.claude/agents/code-writer.md`. It checks that the generated `bare-writer.md`, parsed with `msmr.agent_frontmatter_tools`, `msmr.agent_frontmatter_model` and the `effort` reader, has the same `tools`, `model` and `effort`, plus `name: bare-writer` and `omitClaudeMd: true` (row 80);
  - the `effort` reader returns nothing for a frontmatter without the key;
  - the registry's `bare` and `toolkit` allow no fixture loads.
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
- `evals/test_replay_bench_runner.py` launch and environment cases:
  - launch argv: the `env -i` prefix, `--permission-mode acceptEdits`, and a `--settings` JSON holding only the instrument hook, compared as parsed JSON;
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
  - a fake whose `.envrc` stand-in exports `SENTINEL_PROFILE=sentinel-value` before it `exec`s prints the name `SENTINEL_PROFILE`, and the output holds neither the value nor any hash;
  - readiness: a `PATH` with no `claude`, and a fake `claude --version` that exits 1, each refused before any launch;
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
    - in both, the per-arm stop halts the campaign with exit 1, never `main()`'s exit 2, and a resume with `--skip-task` for that task runs the next task's block.
- Retry cases:
  - an attempt that fails validity twice records missing with summed cost, and leaves no fixture, attempt dir or `sessions/` file;
  - a fail-once-then-succeed run takes its diff from the second fixture, and the first fixture and attempt dir are gone;
  - a launch timeout and a launch exception are each charged at the cap;
  - an empty stdout records `launch-no-output`;
  - an exception from the analysis after a launch records `analysis-error` with the attempt's cost, and no second launch happens;
  - a prune that raises records `fixture-error` with no launch;
  - the `analysis-error` and `fixture-error` cases each leave no fixture, attempt dir or `sessions/` file.
- Cleanup cases, each with a `tmp_path` config dir:
  - a valid run's record, diff file, metrics and cleanup, the `sessions/` file included;
  - a `sessions/` file whose first line changes between lookup and delete survives;
  - a symlink at that name survives, and so does its target;
  - an `attempts/` entry that is a symlink to a sentinel dir, and one whose name breaks the grammar, are both left, with the sentinel intact;
  - an attempt dir name with a hyphenated arm such as `toolkit-copy` round-trips through its formatter and parser, and the resume deletes it by name;
  - a swept entry from one account deletes nothing under another account's `sessions/`.
- Campaign cases:
  - environment drift at block start and at block end halts the campaign, writes no record, never calls `mark_block_complete`, and prints the block's spend;
  - a resume after an account's environment changed exits 2 before any launch, naming the account and the field;
  - a block whose `bare` runs all fail while its `toolkit` runs pass stops the campaign and stays incomplete, and a resume reruns it;
  - each halt prints its reason and exits 1, with one case each for drift, the cumulative stop, the account stop and the per-arm stop. A campaign that completes exits 0;
  - each complete block prints its progress line with the task ID, per-arm counts, the block's spend and the totals against their stops;
  - repeated halts:
    - the first halt on a task prints neither `halted again` nor the `--skip-task` flag;
    - a block whose `bare` runs fail the same way twice prints `halted again` with its missing reasons on the second halt;
    - a resume with `--skip-task` for that task passes the manifest comparison and goes on to the next task;
    - a later resume without the flag still never reruns it;
    - a skipped task with a pending entry whose session ID no spend line carries is charged once, its fixture and `sessions/` file are deleted, its block is not rerun, and `summarize` counts it as skipped, not incomplete;
    - a campaign whose last remaining task is skipped exits 0;
    - `--skip-task` for an ID outside the task set, and for a complete block, each exit 2;
  - a resume whose probe resolves a source repo to another account key than the manifest's exits 2 before any launch, naming the repo;
  - a resume with a changed `--fixture-git-timeout-s` passes the manifest comparison;
  - smoke gate: `run` exits 2 before any launch for an account in each of these cases:
    - no smoke-pass record;
    - a record holding another environment reading or model ID;
    - a record whose arms lack one of the manifest's;
    - a record that does not parse, with no traceback;
    - a record whose stored account key does not hash to its file name's digest;
  - a crash between record writes and `mark_block_complete`, then a rerun, leaves exactly one file set;
  - a two-account resume sweeps each account root, reruns each swept block once, deletes stale attempt dirs by name, and prints the count;
  - the manifest-mismatch exit, one case per field: task-set hash, arms, k, seed and model ID.
- Spend cases:
  - a block ceiling over a stop exits 2 before any launch;
  - a campaign ceiling over the cumulative stop exits 2 without `--allow-early-stop`. With it, the campaign runs until the cumulative stop halts it before the block that would pass it;
  - a resume after one complete block and one skipped task prints the run count of the remaining blocks, and compares the spend so far plus their ceilings with the cumulative stop. Its stop sits between that figure and the all-tasks ceiling, so only that reading passes without `--allow-early-stop`;
  - the cumulative stop and the account stop each fire before the next block, and the account stop halts the whole campaign;
  - two repos with one config dir share one account's spend, and two with different config dirs do not;
  - after a crash between launches and `mark_block_complete`, the resume's stops count the lost block's spend lines when deciding whether the next block launches;
  - resume cap charges, on a `tmp_path` store and spend file with two accounts:
    - a pre-launch entry with an empty session ID is charged nothing;
    - an attempt that has a spend line and still has its empty-ID entry pending gets no new line;
    - an entry with a session ID and no spend line, returned by both account roots, gets exactly one cap charge, on the account of its task's repo;
    - a halted block whose attempts all have spend lines gets no charge at all;
    - the stop arithmetic after that resume matches the sum of the spend lines;
    - the charge line holds `null` arm, run index and attempt number and the swept session ID, and the stops' own parser reads it as one charge at `--budget-cap-usd`;
    - a second resume over the same store appends no line for that entry, and the stop arithmetic is unchanged;
  - a torn last spend line is truncated at resume before the first append. The next appended line parses, and the sweep charges the truncated line's attempt once at the cap;
  - an unparseable line that is not the last counts at the cap;
  - the spend-file appender called from four threads, N appends each, leaves 4N lines that all parse, with the total equal to the sum.
- CLI cases:
  - exit 2 for `run` without `--k` or `--cumulative-stop-usd`, for a `--task-id` breaking its grammar, and for a `tasks` call that raises `TaskSourceError`;
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
  - a task with a valid run in one arm only, left out of the pairs;
  - a task with no non-test code line in one arm, left out of the pairs and counted in the zero-code-line tally;
  - the base-density split with an even task count, with a tie at the median, and with a single task;
  - a paired task with a null base density, kept in the primary difference, in neither half, and counted;
  - spend by account and each account's cap-charged share, from a synthetic spend file;
  - a partial incomplete block's records on disk, left out of every figure and counted as incomplete;
  - a skipped task, listed with its recorded missing reasons and counted;
  - one paired task, printed with no interval;
  - the per-repo breakdown by the task ID's first part;
  - the block-length histogram at blocks of 1, 2, 3, 4 and 5 lines;
  - per arm, the permission-denial count, the unclassified-line share, the `ast.parse` rejection count and the three tallies;
  - the mixed-environment exit.
- `smoke` cases:
  - `instrument: ok`, with its smoke-pass record written, naming the arms it validated;
  - each run's printout carrying its fixture-build seconds;
  - each failed condition naming itself, with no record written;
  - a config-dir file that grew, listed by name.

**Phase 5 — test selection and docs.** Verify: `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py` and `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/`.
- `claude/.claude/scripts/select-tests.py`:
  - add `REPLAY_BENCH_DIR`, `REPLAY_BENCH_RUNNER`, `REPLAY_BENCH_TEST_GLOB = "evals/test_replay_bench*.py"` and `REPLAY_BENCH_ARMS_TEST = "evals/test_replay_bench_arms.py"`;
  - add a `DOMAIN_RULES` row that selects `REPLAY_BENCH_TEST_GLOB` for a path under the directory, the runner, or a glob member;
  - add `REPLAY_BENCH_TEST_GLOB` to the targets of the `_is_review_bench_change` row and of the `_REVIEW_BENCH_SCRIPTS_DEPENDENCIES` and `SKILL_EVALS_RUNNER` cross-domain rows, because the runner imports `review_bench` and `measure_subagent_model_resolution` and, through them, `run_skill_evals` (row 39);
  - add `REPLAY_BENCH_ARMS_TEST` to the `AGENTS_DIR` cross-domain row's targets at `:876-879` (row 80);
  - add a standalone row that selects `SELECT_TESTS_TEST_PATH` for a change to `REPLAY_BENCH_ARMS_TEST`, beside `MEASURE_SUBAGENT_MODEL_RESOLUTION_TEST`'s at `:832` (row 80);
  - add a header-comment entry for that standalone row, like the one at `:812-818`.
- `claude/.claude/scripts/tests/test_select_tests.py`:
  - add `REPLAY_BENCH_TEST_GLOB` to every set-equality assertion over the review-bench target set, `test_config_dir_module_change_selects_all_four_declared_importer_sets` at `:1575-1596` included (row 67);
  - add cases for a replay package file, the replay runner, and a replay test file;
  - add a replay counterpart of the `_REVIEW_BENCH_SCRIPTS_DEPENDENCIES` membership loop at `:798-806`;
  - add `REPLAY_BENCH_RUNNER` to `_EXACT_MATCH_LITERAL_PATH_CONSTANTS`;
  - add `REPLAY_BENCH_ARMS_TEST` to `_EXACT_MATCH_LITERAL_PATH_CONSTANTS`;
  - add `REPLAY_BENCH_ARMS_TEST` to `_FILE_TARGETS`;
  - add `REPLAY_BENCH_DIR` to `test_review_bench_directory_constants_exist_on_disk`;
  - add `REPLAY_BENCH_ARMS_TEST`, with its real-file reader's test name, to `_HAND_AUDITED_AGENTS_DIR_READERS` (row 80);
  - add `replay_bench/*.py` and `run_replay_bench.py` to the import-closure test's seed list, as that test's docstring asks of a sibling eval script, and keep `_REVIEW_BENCH_SCRIPTS_DEPENDENCIES` equal to the closure;
  - add a test that parses every file in `evals/replay_bench/` and `run_replay_bench.py` and finds no `ImportFrom` with `level > 0`, with a seeded relative import as its failing control. The closure test maps every relative import to `review_bench`, so the replay package must import by absolute path (row 81).
- `evals/README.md`: a "Replay bench" section after "Review bench". It covers:
  - what the runner measures, and usage for the five subcommands;
  - `run`'s exit codes, its per-block progress line, and `--skip-task` for a task that halts again;
  - the account preflight with its readiness check, `--direnv-timeout-s` and the `direnv exec <repo> true` hint, the allowlisted launch environment, and the refusal of a `PATH` with an empty or relative entry;
  - that `preflight` names each variable an `.envrc` set that the launch drops, and that `HOME` passes, so file-based credentials such as `~/.config/gh`, `~/.aws` and `~/.ssh` are not routed by account;
  - `tasks`' base-tree bounds, `--max-base-mb` in units of 10^6 bytes and `--max-base-entries`, its printout of the largest kept tasks, and `--fixture-git-timeout-s` for a machine where healthy builds time out;
  - that `run` needs a passing `smoke` on each account under its current environment and for every arm it runs, so a CLI update, a `git pull` of the config checkout, or a new arm calls for a new `smoke`;
  - the residual risk: nothing contains a replay agent beyond `acceptEdits`' working-directory scope and the account's permission rules, so a steered agent can read or write anything those rules allow (rows 45, 47);
  - the bench's trust warning (row 45);
  - the trusted-author list's purpose, which is risk reduction and not containment: it limits how much third-party text reaches an agent holding Bash. It reads raw identities, so a `.mailmap` cannot widen it. Its limits (row 61):
    - an author email is a string the committer sets;
    - under a squash-merge host the author is the pull request's author, so the list leaves out outside contributors;
    - under a rebase or merge-commit workflow, an outside branch can carry a trusted email;
    - the delimiter skip stops no injected instruction that does not need to close its block;
  - that a machine with a managed-policy `CLAUDE.md` fails every `bare` run;
  - the account files a replay adds to and the cleanup leaves: `.code-writer-self-review.log`, `.code-writer-self-review.d` and `.session-config-keys-log.jsonl` (row 79);
  - where `.local/` lives, that removing a worktree deletes it, and `--local-dir`;
  - that a resume needs the same `TMPDIR` as the run it resumes;
  - that results are not a publication instrument, because the runner refuses no wider corpus (row 10);
  - that it needs Python 3.12 or newer and git 2.32 or newer, run through `scripts/venv-run.sh python3`;
  - a pointer to this plan for the design.

Projected diff: about 6,880 lines, the sum of these parts: about 2,750 for the new package and CLI, 3,850 for the five new test files, 150 across `select-tests.py` and its test, 96 for `evals/README.md`, 30 for `fixture_repo.py`, and 4 for `.gitignore` (row 110).

## Verification

1. Before Phase 3, the session runs one manual probe to settle rows 15, 18, 19, 20, 26, 53, 76, 88, 94 and 106 before the runner depends on them.
   - Build a scratch git repo under the system temp dir with `.claude/worktree-optout` and a `bare-writer.md`.
   - Run `claude -p` once per arm with the Launch argv, the `env -i` prefix and the `--settings` JSON, on a one-line edit task.
   - In the `toolkit` run, the task also asks the subagent to run `env | cut -d= -f1`, a names-only listing. Compare those names with the allowlist.
   - Read the hook log's full payloads, the subagent transcript, `<config-dir>/sessions/`, and `git status`.
   - Note whether the session-start burst of hook-log lines arrived one payload per line.
   - Run `direnv exec <a directory with an .envrc> pwd` from another directory.
   - Save one dispatcher line and one subagent line of the hook log, with every path replaced by a placeholder, for Phase 3's fixtures.
   - Time four concurrent Phase 2 `build_task_fixture` calls, through `scripts/venv-run.sh python3`, on each of two synthetic source repos under the system temp dir. Both base trees total the `--max-base-mb` default:
     - one holds about 10,000 text files;
     - the other holds as many files as the `--max-base-entries` default, the larger shape.
   - The slowest build across both sets Phase 4's `FIXTURE_GIT_TIMEOUT_S`, `--max-base-mb` default and `--max-base-entries` default by the rules under Fixture.
   - Stop and re-plan with the engineer before Phase 3 when any of these holds:
     - the hook log shows no subagent `CLAUDE.md` load for `toolkit`, or any `CLAUDE.md` load for `bare`;
     - a subagent load line carries no `agent_id`, or a line carries no `session_id`;
     - no `sessions/` file holds the run's session ID;
     - the session fails to start or to authenticate under the allowlisted environment;
     - a hook-log line holds two payloads, or one payload spans two lines;
     - the names-only listing shows a name outside the allowlist that neither the shell nor Claude Code sets, such as a token or a cloud-profile variable.
2. Each phase's own command in Critical files, run by that phase's dispatch.
3. For the whole change, `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py`. It selects the full suite on its own, because `select-tests.py` is one of its global trigger paths (row 40). Then run `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/`.
   - After the PR's first CI run, compare the `tests` job's duration with the latest `main` run's, against the 25-minute `timeout-minutes` cap (row 89).
   - A duration within 5 minutes of the cap goes to the engineer before merge, because re-deriving the cap edits `.github/workflows/tests.yml`, a file outside this plan's list.
   - Record `scripts/venv-run.sh pytest --durations=20` over the five new test files beside that comparison, so a slow test shows by name (row 107).
4. `git check-ignore -q evals/replay_bench/.local/tasks/probe.json` exits 0.
5. A review check covering:
   - every new test builds its repositories, transcripts and config dirs under `tmp_path`;
   - every committed log or transcript fixture carries placeholder paths only;
   - no committed file names or mines a real repository (row 9).
6. Live checks the engineer runs from a terminal through `scripts/venv-run.sh python3`, never in CI. They spend subscription quota. Their output stays in the terminal and `.local/`, and the PR body may say whether they passed but cites no figure (rows 9, 10).
   - `scripts/venv-run.sh python3 evals/run_replay_bench.py tasks --source-repo <this repository's main checkout> --trusted-author <the engineer's commit email> --name own-history --max-tasks 5`, then read the briefs, the printed identities, and the largest kept tasks by size and by entry count.
   - `scripts/venv-run.sh python3 evals/run_replay_bench.py preflight --tasks own-history` resolves one account with no refusal, its readiness check included. The same check on a task set from one other local repo, whose direnv configuration sets `CLAUDE_CONFIG_DIR`, resolves that account's config dir.
   - `scripts/venv-run.sh python3 evals/run_replay_bench.py smoke --tasks own-history --task-id <id>`, where `<id>` is the task `tasks` named as largest by entry count, passes when all of these hold:
     - its last line reads `instrument: ok`, and it wrote the account's smoke-pass record;
     - each arm's valid run has a non-empty diff;
     - both arms observed the same model;
     - each run's fixture-build seconds are under a third of the `--fixture-git-timeout-s` in force;
     - the engineer recognizes every recorded Bash command, out-of-fixture path and out-of-session path;
     - every name `smoke` reports under the config dir, and every file it reports as grown, is one of three things: a file the cleanup already removes, one of row 79's known appenders, or a file added to the cleanup before any `run`.
   - In their own terminal, the engineer counts the keys under the top-level `projects` object of the account's `.claude.json` before and after that `smoke`, and notes only whether the count grew. A growth is an open question for the engineer before any `run`, and the runner never edits `.claude.json`.
   - A `run` on any other account first needs one passing `smoke` on a task from that account, and `run` refuses otherwise.
   - On a failed smoke, stop before any `run` and bring the failing premise to the engineer: row 16, 18, 19, 20, 26, 31, 32, 37, 70, 88 or 106.

## Out of scope

- Building arm C. It waits on D2, D3 and its own smoke (row 2).
- A judge pass that runs `comment-discipline-reviewer` over each stored diff and counts its Comment verbosity and Restating-the-code findings per arm. A word or line count measures comment volume and shape, not verbosity as that lens judges it (row 38). The Context's intended outcome names line counts, and a judge needs its own validation first. Every run keeps its diff, so a later judge pass needs no rerun.
- Counting trailing comments outside Python, and a third-party lexer such as pygments for language-accurate tokens. A lexer is a new dependency that needs the engineer's confirmation first, and the paired contrast tolerates a classifier error both arms share (row 35).
- Publishing any figure, an own-history one included (rows 4, 9, 10).
- Source repos that are not on this machine (row 6).
- A headless main-session arm, the review-and-ship loop, as a further arm. It can be added later as arm data. Until it exists, a null result cannot show the verbosity is built-in (row 62).
- Containing a replay agent, by a sandbox, a credential deny list or a tool-deny set. Steered agents are out of scope for this runner (row 47), and the README states the residual risk (row 45).
- Writing each run's record as it finishes, so that a crash loses no paid run's record. A resumed block reruns whole, so per-run records would need a drift rule of their own. The spend file already keeps a lost block's cost.
- Deleting the rows that replay sessions add to the account's shared logs (row 79). The README lists them as accepted residue.
- A login check in preflight. Whether the installed CLI has a login-status command is unchecked, so `smoke` is the first check that authenticates (row 70).
- Moving the bench's shared pieces (validity, run store, environment record) into a common module. That would end this runner's reads of names with a leading underscore in `review_bench.runner` and `measure_subagent_model_resolution`. It is a separate refactor (Axis 4). Until then, `select-tests.py` reruns the replay tests on every review-bench change (row 39).
- Changing `_LOCAL_GIT_TIMEOUT_S` for the review bench's own callers. The replay runner passes its own limit through the new `timeout_s` keyword (row 87).
- Re-deriving the CI job's `timeout-minutes` cap. Verification step 3 brings a job near the cap to the engineer instead (row 89).
- A `CHANGELOG.md` entry. The change adds contributor-side eval tooling, and the one stowed file it edits is `select-tests.py`'s rule table.
