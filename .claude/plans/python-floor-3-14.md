# Raise the Python floor from 3.11 to 3.14, the one minor CI tests

## Context

**Goal: support one Python minor, 3.14 (the CI pin in `.github/workflows/tests.yml`), as a floor (`>= 3.14`), so every other statement of the requirement reads the pin or is held to it by a test, and code and tests that exist only for older minors are deleted.**

Today the repo states three minors and tests one: 3.11 for stow users (README, `install.sh`, two hook preflights), 3.12 (ruff `target-version`, the eval bench minimum), and 3.14 (the CI pin). Code needing anything newer than 3.11 passes CI and can fail on a stow user's Python. Keeping 3.11 working also costs compatibility code and tests (issue #1445 lists them with line numbers).

Ask: GH-1445, "Raise the Python floor from 3.11 to 3.14, the one minor CI tests". On the floor-versus-exact question the engineer selected the label "Floor >= 3.14 (Recommended)". On the hook preflights the engineer asked "Why are we supporting hooks below the floor? Should sessionStart just fail loudly if the dependencies aren't met?" and selected the label "SessionStart check, drop hook preflights (Recommended)", then asked for an adversarial review of that selection. The issue proposed keeping one shared `_lib.sh` preflight instead.

## Approach

Python 3.14, the minor CI pins, becomes the one supported Python, as a floor that `_config.sh` holds and a test ties to CI's pin. Around that floor:
- `install.sh` refuses a lower `python3` before changing anything (M1).
- A new informational SessionStart hook warns at session start and resume (M3).
- The two hook version compares go, and the three per-hook `python3` presence checks stay (M4).
- The code serving only 3.11 through 3.13 is deleted (M7).
- ruff moves to a series that lints for 3.14 (M6).

This departs from the issue in two places:
- No runtime version preflight stays in any hook (rows 3-4), so the issue's acceptance line "makes the shared hook preflight exit 0 silently" is superseded.
- `install.sh` reads `_config.sh`'s floor instead of calling `read_ci_python_pin` itself (M1).

Paths are relative to the repository root.

**Alternatives weighed and set aside:**
- **`install.sh` reads the pin through `read_ci_python_pin`, as the issue says.** The hook runs from the stowed hooks directory and can reach `tests.yml` only by resolving its own link back into the checkout, so it would need a constant anyway. Two runtime sources for one floor would then need the drift test regardless. One constant that both read, tied to the pin by a test that calls `read_ci_python_pin`, keeps one runtime value (rows 7, 9).
- **The hook reads `tests.yml` at runtime.** That needs the same link resolution plus a `sed` parse of a CI file at each session start, and a moved or reformatted workflow would silence the warning.
- **A Python check inside `_config.sh` on every hook call, as the bash floor does.** Bash's check reads `BASH_VERSINFO` for free. A Python check spawns an interpreter on every hook fire, against the 100 ms per-fire budget (row 35).
- **One shared `_lib.sh` preflight, the issue's proposal.** The engineer selected dropping the preflights (row 3).
- **Refusing the session from SessionStart.** SessionStart has no decision control, and exit 2 only shows stderr (G3).
- **No matcher, so the notice fires on every SessionStart source.** The engineer selected startup and resume only (row 39).
- **Today's shape, a Python-side comparison with the floor interpolated into `-c`.** It names no found version without a second call, and its boundary is testable only by patching `sys.version_info` (row 11).
- **One predicate shared with `_bash_version_meets_floor`.** That function must parse on bash 3.2 and is pinned identical across `_config.sh` and four plugin `_lib.sh` copies (row 10), so sharing it would bring four plugins and their version bumps into scope.
- **The floor constants, or a bare `python3-venv`, in the ensurepip hint.** `python<floor>-venv` names the wrong minor for a newer `python3`, and `python3-venv` names no minor. The found `X.Y` names the package for the interpreter the check just measured, the form `install-dev.sh:127` uses (row 29).
- **Deleting `nudge-error-mode-analysis.sh`'s step 7 whole.** The output stays silent either way, but without the builtin check a host with no `python3` pays a `mkdir`, a `find` spawn and a failing `timeout` spawn on every prompt, and gets a checkpoint directory it can never use (row 20).
- **Deleting `ask-new-dependency-disclosure.sh`'s step-6 probe.** Its silent allow on an absent or unusable `python3` is a documented contract (row 19).
- **Deleting `require-worktree-for-git-writes.sh`'s `command -v python3` check (`:285-288`).** The outcome is a deny either way, but that check's text names the recovery routes, and the parser-failure deny at `:298` does not (row 23).
- **A floor below the CI minor, or a CI matrix of the floor plus the current minor.** The floor follows the single CI pin by design, because a floor below the CI minor is a floor no job exercises. `test_python_floor.py` ties the pin, the floor constants, ruff's `target-version` and the README together. Consequences of the alternatives:
  - A matrix changes the three sites that hold the single-pin rule: `_ci-python-pin-lib.sh`, `test_ci_python_matches_dev_interpreter.py` and `install-dev.sh`.
  - Each CI minor bump is a stow-user-visible floor raise that needs a README edit and a `### Removed` entry.

  A `python3` older than the pin gets the SessionStart notice, and `install.sh` refuses to install. Whether the repo's code would still run on an older minor was not checked (row 24). The floor value is the engineer's selection (row 1); the session's reasoning is that the bump cost above follows from it.
- **A general dependency check at SessionStart, which the engineer raised (row 40).** The session deferred it to a follow-up (row 41, Out of scope).
- **A Python-floor clause in the deny texts at `require-worktree-for-git-writes.sh:298` and `_lib.sh:2591`, from the adversarial review.** No one selected it (row 5). The SessionStart notice names the floor at each session start and resume, and `:2591` already names `python3`.
- **A dedicated `CROSS_DOMAIN_EXCEPTIONS` row for `_ci-python-pin-lib.sh`.** `_is_scripts_dir_shell_script_change` already selects `HOOKS_TESTS_DIR` for that path, so a second row would state one selection twice (row 43). One reader line in that row's comment declares the edge, and `TestCrossDomainReadCompleteness` fails if the selection is later narrowed.
- **The oldest ruff series that accepts `py314`.** It is not known (row 14), and a newer series parses 3.14 syntax at least as fully. The pre-Phase-1 check bounds the lint fallout instead.
- **A test that blocks `tomllib` and asserts the hoisted helper fails to load, from `ciso-reviewer`'s round-1 review.** CPython ships `tomllib` from 3.11 on (row 25), so at a 3.14 floor the test guards only a sub-3.11 interpreter. Its premise, that a deferred import could turn a TOML edit into a silent allow, needs an edit that swallows the `ImportError`, because today `main()` catches it and exits 1, which step 7 degrades to ask (row 49).
- **The peer's `printf` of fixed text for the notice JSON.** The notice cites `README.md` § "Requirements", whose quotes would break a `printf`-built line. `_lib_jq -n --arg` escapes any text, as `announce-branch-review-history.sh:37-40` does.
- **The probe under `_lib_capped`'s 5 s cap.** A hung `python3` killed only at 7 s leaves 3 s of the registration's 10 s for the two `_lib_jq` calls, against 6 s under `_lib_capped_for 2` (row 54).

### Assumption ledger

**Root:** The repo states three Python minors and tests one (Context), so this plan makes CI's pin, 3.14, the one supported minor as a floor that `_config.sh` holds and a test ties to the pin, and deletes the hook version preflights and the older-minor code (M1, M2, M4, M7).

**Givens:**
- G1. `git pull` puts hook, script and `settings.json` changes live with no install step, and `install.sh` runs only when a user runs it. *Reason: stow links the checkout into the config dir (`CLAUDE.md`: edits under `claude/.claude/**` "go live on `git pull`"). Changing that is an install-architecture decision outside this plan.*
- G2. Hooks resolve `python3` on the PATH Claude Code started with, which can differ from the terminal PATH `install.sh` checks. *Reason: Claude Code runs hooks in its own environment, a vendor behavior `docs/hooks.md:439` and `:464` record for bash.*
- G3. On SessionStart, `systemMessage` is shown to the user and `hookSpecificOutput.additionalContext` is added to the agent's context. SessionStart has no decision control, and exit 2 only shows stderr while the session proceeds. Its sources are startup, resume, clear, compact and fork, and an omitted matcher matches all of them. The docs say nothing about `continue: false` on SessionStart, about firing for subagents, or about rendering in `-p` mode. *Reason: Anthropic defines the hook protocol (code.claude.com/docs/en/hooks.md, read in part by a session subagent; not re-read by the architect).*
- G4. CI's Python is what `actions/setup-python` installs for the quoted `python-version` pin. *Reason: GitHub and the action own the runner and the build.*
- G5. Which `target-version` values a ruff release accepts is Astral's call. *Reason: Astral publishes ruff.*

**Rows:**
1. `[engineer-verified: "Floor >= 3.14 (Recommended)"]` This is the selected label on floor versus exact. `anchors: root, M1, M2`
2. `[engineer-verified: "Why are we supporting hooks below the floor? Should sessionStart just fail loudly if the dependencies aren't met?"]` `anchors: root, M3`
3. `[engineer-verified: "SessionStart check, drop hook preflights (Recommended)"]` This is the selected label on the hook preflights. `anchors: M3, M4`
4. `[unverified]` The session wrote row 3's option description. It proposed one new SessionStart hook that warns loudly when `python3` is below 3.14. It proposed deleting the two per-hook preflights and their tests, departing from the issue's "a runtime preflight stays". The plan implements both (M3, M4). It reads "the dependencies" in row 2 as `python3` itself, so a missing `python3`, or one that reports no version, also warns. "Fail loudly" becomes a screen notice plus agent context, because SessionStart cannot refuse a session (G3). All three per-hook `python3` presence checks stay: the ask hook's step 6, the nudge's step 7 and `require-worktree-for-git-writes.sh`'s (rows 19, 20, 23). `anchors: row2, row3, row20, M3, M4`
5. `[unverified]` Context's `Ask:` line records that the engineer then asked for an adversarial review of row 3. A session-commissioned architect returned "modify", and its points are a subagent's views, not the engineer's. The plan declines its deny-text clause (Alternatives) and adopts these points: `anchors: M1, M3, M4, M8`
   - keep the ask hook's presence probe (M4);
   - delete the nudge's version check safely (M4);
   - a `_config.sh` home for the floor (M1);
   - the SessionStart output shape (M3);
   - `install.sh` as the fresh-install gate (M1);
   - the CHANGELOG and stale-rationale edits (M8, M4).
6. `[verified: .github/workflows/tests.yml:100-106]` CI has one `setup-python` step, pinned `'3.14'`. Its comment calls the pin "the single home for the Python minor", names `install-dev.sh` and `test_ci_python_matches_dev_interpreter.py` as its readers, and mentions "the 3.11 floor". `anchors: root, M2`
7. `[verified: claude/.claude/scripts/_ci-python-pin-lib.sh:1-28]` `read_ci_python_pin FILE` prints the single quoted X.Y pin, and otherwise returns 1 with an `ERROR:` line. Its header says "Sourced by install-dev.sh". `anchors: M1, M2`
8. `[verified: install.sh:1-6,20-32,76-127]` `install.sh` runs under `set -e` (`:6`) and sources `_config.sh` at `:30`, before the presence check. The python block between its `INSTALL_TEST_FIXTURE: python-floor-check` markers (`:78-89`) runs `python3 -c` against `(3, 11)` and echoes its message to stdout, and `:79` points to `parse-manifest-dependencies.py` for the reason. The git block (`:94-125`) points to `README.md` § "Requirements" instead (`:95`). It captures its probe with `|| git_version_status=$?` (`:100`) and names the `git` that `command -v` resolves (`:101`, `:117`). The first change to HOME is `:127`'s `mkdir`. `:5` names `test_install_sh_python_floor.py` as the extraction pattern. `anchors: M1`
9. `[verified: claude/.claude/hooks/_config.sh:1-12,26-68]` `_config.sh` is sourced by every hook through `_lib.sh` and by `install.sh` alone, and stays free of `_lib.sh` dependencies. The bash floor's constants and predicate sit at `:27-38`, and its bash-3.2 region ends at `:68`. `anchors: M1`
10. `[verified: grep of claude/.claude/hooks/tests/test_bash_floor.py for _BASH_FLOOR_MAJOR and _bash_version_meets_floor]` The bash floor tests pin `_BASH_FLOOR_MAJOR=` as `_config.sh`'s first command, compare `declare -f _bash_version_meets_floor` across five copies, and check the region up to the bash floor's call. A block after `_config.sh:68` touches none of these. `anchors: M1`
11. `[verified: claude/.claude/hooks/tests/test_install_sh_python_floor.py:1-162]` The current test extracts the marker block, keys a stub `python3` on `version_info` in the `-c` text, asserts "3.11" in stdout, and patches `sys.version_info` to test the literal tuple. It also holds the presence-check test for a missing `python3`. `anchors: M1`
12. `[verified: claude/.claude/hooks/tests/test_git_floor.py:49-50,235-270]` The git floor's whole-script run is written inline in one test (`:236-270`). It stubs `python3` as a bare `exit 0`, expects `install.sh` to stop at the git block, and asserts an empty HOME. A python block that needs a printed version would stop it earlier. `anchors: M1`
13. `[verified: README.md:103-113; claude/.claude/hooks/tests/test_git_floor.py:273-286]` README's Python bullet (`:111`) says `>= 3.11`, names stock macOS 3.9.6, Ubuntu 22.04's 3.10 and Debian 11's 3.9 as below the floor, and says `install.sh` checks it. The git bullet opens "git 2.43 or newer", and `TestMinimumVersionMatchesTheDocumentedRequirement` parses that opening. `anchors: M2`
14. `[verified: pyproject.toml:1-3; requirements-dev.txt:3; claude/.claude/scripts/select-tests.py:475-479]` ruff has `target-version = "py312"` and `ruff==0.6.*`. `pyproject.toml` and `select-tests.py` are global triggers, so a diff touching either selects the full suite. `[unverified]` Per a session explorer, the shared `.venv`'s ruff is 0.6.9 and rejects `py314`, and PyPI's newest ruff is 0.17.4. Which series first accepts `py314` is not known. `anchors: M6`
15. `[verified: install-dev.sh:149-153; README.md:538]` `install-dev.sh` syncs `requirements-dev.txt` into `.venv` on every run, and it runs from the main checkout's root. So until this PR merges, any `./install-dev.sh` run puts main's `ruff==0.6.*` back in the shared `.venv`, and `scripts/venv-run.sh ruff` in this worktree then cannot lint for `py314`. `anchors: M6`
16. `[verified: claude/.claude/hooks/record-session-config-keys.sh:2,24-48; claude/.claude/settings.json:203-209; claude/.claude/hooks/tests/test_record_session_config_keys.py:478-499; docs/hooks.md:204]` This is the informational SessionStart pattern to copy. It has `# hook-class: informational`, `set -uo pipefail`, `exec 2>/dev/null`, a `_lib.sh` bootstrap that exits 0 on failure (`:42`), a `hook_event_name` filter, and one `printf` of `{"systemMessage":…,"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":…}}` built from fixed text. It is registered with no matcher and no `timeout`, and its `TestRegistration` pins that registration. `anchors: M3`
17. `[verified: claude/.claude/hooks/tests/test_hook_alignment.py:1-16,104,144-159,196-213]` Every hook needs a line-2 `hook-class` and a `- **`name`**` bullet in `docs/hooks.md`. Only a name matching a gate prefix must be a gate, and `check-python-floor.sh` matches none, because the `check-` pattern is `check-.*-guard\.sh`. `anchors: M3`
18. `[verified: claude/.claude/hooks/_lib.sh:50-52,129-140]` `_lib_capped` runs a command under `timeout -k 2 5`, or under `gtimeout`, or uncapped with a stderr note. `anchors: M1, M3`
19. `[verified: claude/.claude/hooks/ask-new-dependency-disclosure.sh:49-62,194-215; docs/hooks.md:247; docs/security-hardening.md:332-336,345-355]` In step 6, an absent `python3`, or a failing `python3 -c ''`, allows silently (`:195-196`), and the version compare degrades to ask (`:197-201`). Step 7 degrades to ask on a nonzero helper exit or empty output (`:210-215`). Both docs state the 3.11 floor and the below-floor degraded ask beside the documented silent allow. `anchors: M4, M5`
20. `[verified: claude/.claude/hooks/nudge-error-mode-analysis.sh:3,41-44,53-169]` The hook fires on UserPromptSubmit, so on every prompt. Step 7 (`:120-130`) is a builtin `command -v python3` check (`:125-127`) and a version compare (`:128-130`), and both exit 0. Past step 7, `:149-150` create and sweep the checkpoint directory, and `:151-152` spawn `_lib_capped_for 10 python3`. `:153-155` exits 0 on any non-integer output, so a missing or failing `python3` stays silent with or without the compare. Per `claude-hook-review`'s round-2 review, without the builtin check a host with no `python3` would pay `:149-152` on every prompt and create a checkpoint directory it can never use. The step comments are numbered 1 through 9. `anchors: M4`
21. `[verified: claude/.claude/hooks/tests/test_ask_new_dependency_disclosure.py:507-510,571-574,684-717,939-971,1376-1421; grep of both helper names]` These are the below-floor tests: two `skipif(sys.version_info < (3, 11))` decorators, `TestParseManifestDependenciesPythonFloor` (3.11 grammar and the deferred `tomllib` import), and two helpers whose only callers are the below-floor and at-floor pair and `TestVersionCheckArgumentRealBoundary`. `anchors: M4`
22. `[verified: claude/.claude/hooks/tests/test_nudge_error_mode_analysis.py:53,160-179,191-197,406-428,443,451,492-505,520,542; Glob of /usr/bin on this macOS]` `test_python3_missing_is_silent` (`:406-414`) asserts only exit 0 and empty stdout. Its PATH comes from `_restricted_path_without_python3` (`:160-169`), which links a hand-picked tool set from `/usr/bin` and skips any tool that is absent. This macOS's `/usr/bin` has no `cat`, `mkdir` or `timeout`, so the hook reads empty stdin at `:53` and exits at step 5, before step 7, and the test passes vacuously there (`staff-sdet`'s round-2 trace). That helper has one caller, and `:179` names it in another helper's docstring. `test_python3_too_old_is_silent` (`:416-428`) uses a shim that exits 1 on every call, so without the compare it covers a present `python3` that fails every call. Four other shims answer `-c` with `exit 0` only to pass the compare, and `:493-495`'s docstring names the compare. The checkpoint directory sits at `.claude/.error-mode-nudge-checkpoint.d` under the sandboxed home (`:451`). `anchors: M4`
23. `[verified: claude/.claude/hooks/require-worktree-for-git-writes.sh:209-300; claude/.claude/hooks/_lib.sh:2551-2592; claude/.claude/hooks/send-back-skipped-self-review.sh:11,78-83; grep of python3 in claude/.claude/hooks/*.sh]` These five are every direct `python3` run in `claude/.claude/hooks/*.sh` today, each with what it does when its helper exits nonzero: `anchors: M5`
    - `require-worktree-for-git-writes.sh` denies every command that reaches its parser, reads included (`:295-300`). Where worktree enforcement is active (`:225`), a command naming `git` reaches the parser from a main-tree session. From a linked worktree it does so when the command holds `cd`, `-C`, `(` or a backtick, when the worktree lock is absent, or when the collision guard denies (`:214`, `:263-283`). Without `python3` it denies with text naming the recovery routes (`:285-288`).
    - Callers of `_lib_command_words` deny with a clause naming `python3` among the failed tools.
    - `send-back-skipped-self-review.sh` allows and logs.
    - `ask-new-dependency-disclosure.sh` degrades to ask at step 7, after step 6 allows silently for an absent `python3` or one that fails `-c ''` (row 19).
    - `nudge-error-mode-analysis.sh` stays silent (row 20).
24. `[unverified]` Per `staff-product-engineer`'s round-1 plan review, `parse-git-command.py`, `split-command-words.py`, `evaluate-code-writer-self-review.py` and `parse-manifest-dependencies.py` parse under `ast.parse` at `feature_version=(3, 11)`. Whether their stdlib use runs on 3.11 through 3.13 was not checked. So the notice says these hooks are unsupported, not that they fail, and the docs state each outcome for a helper that fails. After this change, nothing keeps the helpers running on 3.11 through 3.13. `anchors: M3, M5`
25. `[verified: claude/.claude/hooks/parse-manifest-dependencies.py:7-13,297-308]` The module docstring states the 3.11 floor and the gap between ruff's target and the runtime floor. `tomllib` is imported inside `_parse_toml_manifest` so that a sub-3.11 interpreter still parses the other formats. `anchors: M4`
26. `[verified: claude/.claude/scripts/transcript_analysis/review_cost_by_size.py:151-176; grep of _average_ranks; claude/.claude/scripts/tests/test_transcript_review_cost_by_size.py:164-179]` `_average_ranks` has one caller, `_spearman_rho`, whose docstring defers `statistics.correlation(method="ranked")` to the floor. A test pins hand-worked rhos for tied input. `[unverified]` `method="ranked"` averages tied ranks and raises `StatisticsError` on fewer than two points or a constant series, as the current code assumes. The pinned test is the check. `[unverified]` Per `staff-product-engineer`, on 3.11 `method="ranked"` raises `TypeError`, which the kept arm does not catch, so `review-cost-by-size` fails with a traceback on an older `python3`. `anchors: M7, M8`
27. `[verified: claude/.claude/scripts/tests/_move_fidelity.py:16,285-287; claude/.claude/scripts/tests/test_transcript_move_fidelity.py:280-288]` The `--rename` guard and its test exist only for an interpreter without `tokenize.FSTRING_MIDDLE`. `anchors: M7`
28. `[verified: claude/.claude/scripts/tests/test_plan_push_preflight.py:8-9,780-808; test_pr_create_preflight.py:10,30,807-817; test_plan_shape.py:25,1335-1345; test_transcript_session_pricing.py:1840-1854]` These are four 3.11-grammar guards. `_fstring_offenders` is defined once, in `test_plan_push_preflight.py`, and imported by two others. `anchors: M7`
29. `[verified: install.sh:1054-1058; install-dev.sh:109-127; evals/README.md:497-500; docs/scripts.md:37; claude/.claude/scripts/tests/test_reconcile_project_plugins.py:1508]` These state a minor below the floor: the ensurepip hint's `python3.12-venv`, the eval bench's "3.12 or newer" beside "the repository's floor elsewhere is 3.11", `post-crash-sessions.py`'s "requires Python 3.10+", and a docstring's "the repo floor is 3.11". `install-dev.sh:127` names `python${ci_python_pin}-venv`, the package for the interpreter it builds from. `anchors: M1, M7`
30. `[verified: grep of the tree for 3\.11, 3\.12, py31x, version_info, feature_version and tomllib, excluding .claude/plans/]` The floor statements and older-minor code are those named in Critical files. The other hits state a CPython or GNU grep fact the floor satisfies, use a version as test data, or are dated records (Out of scope). The issue's own scope list was not read (Out of scope, "Not verified"). `anchors: root, M7`
31. `[verified: CHANGELOG.md:5-7,51-56,529-537]` `[Unreleased]` holds `### Changed` and `### Removed`. `:56` states the 3.11 floor as live `[Unreleased]` text, so M8 rewrites it. The git floor's `### Removed` entry, with its **Migration:** line, is the model. `anchors: M8`
32. `[verified: docs/hooks.md:437-448,460-468]` § "Bash version" is the precedent for a per-interpreter section. It marks an unverified delivery as unverified (`:442`), and says "A headless run needs this done beforehand" (`:462`). `:464` says a terminal pass of `install.sh` does not prove Claude Code's PATH. `:468` cites the harness's default hook timeout of 600 s. `anchors: M3, M5`
33. `[verified: grep of plugins/*/hooks/* for python3 and of plugins/ for 3.11]` Two `skill-management` hooks run `python3`, and no plugin states a Python floor. `anchors: root`
34. `[unverified]` `python3 -I -S -c 'import sys; print("%d %d" % sys.version_info[:2])'` prints two integers on every CPython 3. A Python 2, which lacks `-I`, fails it and reads as reporting no version. `anchors: M1`
35. `[verified: claude/.claude/hooks/record-session-config-keys.sh:24-25]` That hook cites a 100 ms per-fire budget in `claude-hook-review`'s SKILL.md § "7. Performance budget" and documents knowingly exceeding it, because it fires once per session event. It also documents that a registration with no `timeout` leaves a stall bounded only by the harness timeout. `anchors: M3`
36. `[unverified]` A newer ruff with `target-version = "py314"` may raise findings in files this plan does not name. The pre-Phase-1 check counts them before any dispatch. `anchors: M6`
37. `[engineer-verified: "Include it"]` This answers `/plan-review`'s question on whether Phase 3 edits `docs/scripts.md:37`, which is not in the issue's scope list. The edit stays in Phase 3. `anchors: M7`
38. `[engineer-verified: "Proceed anyway"]` This answers `/plan-review`'s question on the `Projected diff:` of about 1,030 lines, over the 400-line threshold. The plan ships as one PR with the three phases as commits. `anchors: root`
39. `[engineer-verified: "startup and resume only (Recommended)"]` This is the selected label on which SessionStart sources fire the notice. `anchors: M3`
40. `[engineer-verified: "So this is a potential scope creep but worth considering either in this PR a follow up, but I think a session start hook is probably a better place for a python three check at all and just any dependency check assuming it's fast enough because then we have less code to maintain"]` This answers whether to keep the per-hook `python3` presence checks and whether a general dependency check belongs at SessionStart. The engineer then asked for the session's thoughts and for an adversarial architect review of its recommendation. `anchors: M4, row41`
41. `[unverified]` The session's disposition, not the engineer's: the general dependency check is deferred to a follow-up, because row 40 leaves its timing open and the review in row 42 found it removes no code. `anchors: row40, row42`
42. `[unverified]` A session-commissioned architect's adversarial review of the session's recommendation on row 40 is a subagent's view, not the engineer's. The plan adopts three of its four points, and the session reversed the first after `claude-hook-review`'s round-2 review: `anchors: M4, row41`
    - reversed: delete the nudge's step 7 whole. Only its version compare goes, because the builtin presence check keeps a per-prompt spawn and a checkpoint directory off a host without `python3` (row 20);
    - keep the ask hook's step-6 probe as a documented contract (row 19);
    - keep `require-worktree-for-git-writes.sh`'s presence check for its recovery text, not for a distinct outcome (row 23);
    - defer the general dependency check, because it replaces none of `install.sh:36`'s presence check, `_config.sh`'s bash floor or `_lib_capped_for`'s uncapped fallback (rows 9, 18).
43. `[verified: claude/.claude/scripts/select-tests.py:544-546,694-700,867; claude/.claude/scripts/tests/test_select_tests.py:118-141,345-367]` `_is_scripts_dir_shell_script_change` matches every `.sh` under `claude/.claude/scripts/` and selects `HOOKS_TESTS_DIR` and `SKILLS_TESTS_DIR`. Its comment names `test_shellcheck.py` and `test_skills.py` as the readers. `TestCrossDomainReadCompleteness` resolves each test file's module-level repo paths and fails when a path's selection does not cover that test. So a hooks test holding `_ci-python-pin-lib.sh`'s path at module level passes today, and fails if that row stops selecting `HOOKS_TESTS_DIR`. `anchors: M2`
44. `[verified: grep of \.source in claude/.claude/hooks/*.sh; claude/.claude/settings.json:164-201; claude/.claude/hooks/announce-branch-review-history.sh:33]` Five SessionStart hooks filter `.source` themselves, per `CLAUDE.md`'s hook defense-in-depth rule (`announce-autonomous-shipping.sh:34`, `nudge-memory-store-audit.sh:70`, `bootstrap-project-plugins.sh:23`, `set-session-title-from-branch.sh:78`, `restore-authorization-boundary-on-compact.sh:44`). `announce-autonomous-shipping.sh` registers `"matcher": "startup|resume|clear"` (`settings.json:184`). Three SessionStart registrations set `"timeout": 10` (`settings.json:179,189,199`). `announce-branch-review-history.sh:33` filters the event and a second field in one `_lib_jq` `select`. `anchors: M3`
45. `[verified: claude/.claude/scripts/marker.sh:108-110,949-954]` `marker.sh clear-stale`, the recovery the global `CLAUDE.md` names for orphaned active markers, runs `python3 -I marker-clear-stale.py` and exits 2 or 127 when `python3` is missing. Whether `marker-clear-stale.py` runs on 3.11 through 3.13 was not checked. Row 23 covers hooks only. `anchors: M5`
46. `[engineer-verified: "Take that out of scope entirely."]` `README.md:111` no longer lists which OS releases ship which `python3` version. The engineer said of that list: "That can be easily found out on the web." The bullet states the floor and how to meet it, and names no vendor release. `anchors: M2`
47. `[verified: claude/.claude/tests/helpers.py:2925-2937,3071-3083,3189-3200; claude/.claude/hooks/tests/test_nudge_error_mode_analysis.py:507-525; claude/.claude/hooks/tests/test_install_sh_claude_code_version_check.py:63-64,245]` These are the test helpers to reuse: `anchors: M1, M3`
    - `build_path_without` mirrors the real PATH minus one binary through a symlink farm, because a hand-picked tool set is a silent false pass;
    - `write_scaled_timeout_shim` and `assert_cap_engaged` read a cap kill from a scaled `timeout` shim, and the nudge's timeout test skips when neither `timeout` nor `gtimeout` exists;
    - `_extract_presence_check_block` extracts `install.sh`'s presence check, and `:245` tests a missing `claude` with it.
48. `[verified: claude/.claude/hooks/tests/test_bash_floor.py:54-67,378-399,426-434]` `Citation` names a cited heading, and `TestFloorMessageCitationsResolve` checks that each one exists. `test_an_inherited_floor_value_does_not_raise_the_floor` pins unconditional assignment by exporting 99 before sourcing. `anchors: M1, M2`
49. `[verified: claude/.claude/hooks/tests/test_ask_new_dependency_disclosure.py:694-717; claude/.claude/hooks/ask-new-dependency-disclosure.sh:210-215,231; claude/.claude/hooks/parse-manifest-dependencies.py:465-496]` `test_tomllib_import_stays_deferred_not_top_level` blocks `tomllib` through `sys.modules["tomllib"] = None` and asserts the module still loads and parses `package.json`. M4's hoist inverts that property, so the test goes with its class. With the deferred import, a missing `tomllib` raises inside `main()`'s `try` (`:471-491`), which catches it at `:492` and exits 1. A helper that exits nonzero, or fails to load, degrades to ask at `:213-215`. A helper that loads but returns no records reaches `:231` and allows silently. `anchors: M4`
50. `[verified: claude/.claude/hooks/deny-network-installs.sh:96]` The install gate's deny text tells the agent to name the package, its exact version constraint and why, then ask the user to run the install in a separate terminal. `[unverified]` Per `ciso-reviewer`, the gate matches the pre-Phase-1 `pip install` commands. `anchors: M6`
51. `[verified: claude/.claude/scripts/reconcile-project-plugins.sh:168-169]` A version-manager shim for `python3` reads `.python-version` from the working directory, and `-I` keeps the cwd entry and PYTHONPATH out of Python startup. `[unverified]` Claude Code starts hooks in the session's working directory, so the notice reports what that directory resolves. `anchors: M3, M5`
52. `[unverified]` Per `staff-product-engineer`'s round-2 review, pyenv resolves a project's own `.python-version` ahead of `pyenv global`. So in a project that pins a version below the floor, the hooks run that pinned `python3`, and the notice repeats at each startup and resume until the pin resolves to the floor or newer. `anchors: M3, M5`
53. `[unverified]` Per `claude-hook-review`'s round-2 review, on a stock macOS without the Command Line Tools, running `/usr/bin/python3` may open the tools' install dialog, so the notice's probe may open it at each startup and resume. `ask-new-dependency-disclosure.sh:49-52` describes that `python3` as a shim that sits on PATH and fails until the tools are installed. `anchors: M5`
54. `[unverified]` A cold `python3 -I -S` start, a version-manager shim included, finishes well inside 2 s. A slower start reads as no version and shows the no-version notice. `[verified: grep of _lib_capped_for 2 in claude/.claude/hooks/*.sh]` 2 s caps are an existing pattern, for example in `nudge-long-turn-subagent.sh` and `nudge-handoff-near-context-cap.sh`. `_lib_capped_for` sends TERM at the cap and KILL 2 s later (`_lib.sh:129-140`), so a hung `python3` ends within 4 s. `anchors: M1, M3`
55. `[unverified]` No test reaches the ensurepip hint at `install.sh:1054-1058`. It reads `python3_found_version`, which only the python block assigns, so a rename at one site would print `python-venv` unnoticed. `anchors: M1`
56. `[verified: gh api repos/astral-sh/ruff/releases/tags/0.17.4, assets list]` Astral's GitHub release for 0.17.4 attaches `ruff-aarch64-apple-darwin.tar.gz` and its `.sha256`, `ruff-x86_64-apple-darwin.tar.gz` and its `.sha256`, and `dist-manifest.json`, and no wheel. The engineer asked "Why not pip?" when offered the GitHub archive. `staff-platform-engineer` advised pip, with the trust basis stated as same-origin integrity only: the PyPI digest guards corruption or a swap between download and install, not a compromised publisher. Row 58 records that the shared `.venv` now holds ruff 0.17.4. No ledger row records an engineer answer on the trust basis. `anchors: M6`
57. `[unverified]` The session's disposition, not the engineer's: row 38's answer stands for this revision's projected diff of about 1,340 lines (Critical files), up from the about 1,030 it answered, because the one-PR, three-commit shape is unchanged. The engineer is not asked again. `anchors: row38`
59. `[engineer-verified: "Add test_bash_floor.py to the plan (Recommended)"]` The Phase 1 review's SDET finding on duplicated helpers is fixed by one shared `readme_requirements_bullet` in `test_bash_floor.py`, a file the approved Critical files did not list. The engineer chose to add the file. `anchors: M2`
58. `[engineer-verified: "Fix the findings under 0.17.4"]` Ruff 0.17.4, linted from a scratch venv at `py312` and `py314`, reports `SIM115` (2), `UP031` (3) and `UP037` (2) in the three test files named under Phase 1, none of which is otherwise in this plan. The engineer chose to fix them over trying an older series. The engineer then confirmed the shared `.venv` upgrade from 0.6.9 to 0.17.4 and ran it; `scripts/venv-run.sh ruff --version` reports 0.17.4. `anchors: M6`
60. `[engineer-verified: "Include it (Recommended)"]` `claude/.claude/hooks/tests/test_deny_reviewer_tree_mutation.py` joins Critical files. `anchors: M6`
61. `[engineer-verified: "Document no mute (Recommended)"]` `docs/hooks.md` states that the floor notice has no mute. `anchors: M3`
62. `[unverified]` The session's reading, not the engineer's words: the PR body puts the missing kill switch to the reviewer as a question (row 61), written by the `/ready-for-review` PR-description step. `anchors: M3`
63. `[unverified]` Ruff 0.17.4 lists `--add-noqa[=<REASON>]` in `check --help`, and it warns, without failing, on the two prose `# noqa` mentions named under Critical files. `anchors: M6`

**Mechanisms:**
- **M1 — the floor and its install-time check.** `_config.sh` holds the floor directly after the bash floor's `fi` (`:68`), outside the bash-3.2 region, in three parts: `anchors: row1, row8, row9, row10, row11, row12, row18, row29, row34, row48, row54, row55`
  - `_PYTHON_FLOOR_MAJOR=3` and `_PYTHON_FLOOR_MINOR=14`, assigned unconditionally and not `readonly`, so an inherited value cannot move the floor and a second source does not fail.
  - `_python_version_meets_floor MAJOR MINOR`, a pure predicate. It returns 0 at or above the floor, 1 below it, and 2 when either field is not a string of 1 to 9 digits. The bound matches the git floor's `{1,9}` (`install.sh:98`) and keeps a long digit run out of the notice. It compares base-10 numbers, so 3.100 passes and a leading zero is not read as octal.
  - `_python3_floor_status [RUNNER...]`, the probe:
    - it runs `"$@" python3 -I -S -c 'import sys; print("%d %d" % sys.version_info[:2])'` with stderr discarded, and `-I -S` keeps site code and PYTHONPATH out, as `_lib.sh:2560-2562` does (row 34);
    - a nonzero probe exit counts as no output;
    - only the first line counts, read through a quoted `read`, and it must hold exactly two fields;
    - it prints the validated `X.Y` on status 0 and 1, and nothing on status 2;
    - it returns the predicate's status, so a missing, failing or banner-printing `python3`, or one the runner kills, returns 2;
    - it guards its own captures, so a `set -e` caller and an `if` caller see the same status.

  `install.sh` calls the probe bare and refuses status 1 or 2 before its first change to HOME. Its ensurepip hint names the venv package for the found `X.Y` (Alternatives), and no test reaches it (row 55). The hook passes `_lib_capped_for 2` as the runner (rows 18, 54).
- **M2 — one floor, held to the pin.** `test_python_floor.py` reads the floor by sourcing `_config.sh` and ties it to three statements: `anchors: row1, row6, row7, row13, row14, row43, row46`
  - CI's pin, read by `read_ci_python_pin` from `_ci-python-pin-lib.sh`;
  - `pyproject.toml`'s `[tool.ruff] target-version`, as `py<major><minor>`;
  - README's one `- **Python:**` bullet in § "Requirements", which opens "`python3` X.Y or newer".

  Each failure message names the four places a floor change touches: `_config.sh`, `tests.yml`'s pin, `pyproject.toml` and `README.md` § "Requirements". The test's by-path read of `_ci-python-pin-lib.sh` is declared in `select-tests.py` (row 43).
- **M3 — SessionStart notice.** `check-python-floor.sh`, an informational hook in row 16's shape, warns when the harness PATH's `python3` is missing, reports no version, or is below the floor. `anchors: row2, row3, row4, row16, row17, row18, row24, row32, row35, row39, row44, row51, row52, row53, row54, row61, row62`
  - It acts only on a SessionStart payload whose source is startup or resume, and it filters `.source` itself, as five peers do (row 44).
  - It prints nothing on status 0. Otherwise it prints one JSON line with `systemMessage` for the screen and `hookSpecificOutput.additionalContext` for the agent. It always exits 0.
  - The only interpolated values are the floor constants and, on status 1, the validated `X.Y`, so no path or free text reaches the screen or the model.
  - The notice says the hooks are unsupported, not that they will fail (row 24), and its screen text carries the fix.
  - It reports what the session's working directory resolves, because a version-manager shim resolves `python3` per directory (rows 51-52).
  - The registration's `"timeout": 10` matches three peers (row 44). It is the only bound where neither `timeout` nor `gtimeout` is on PATH (row 18), against the harness's 600 s default (row 32).
  - Elsewhere the probe's `_lib_capped_for 2` ends a hung `python3` within 4 s, so the no-version notice still prints inside the 10 s (row 54). A stalled `jq` as well could use up the 10 s, and the harness then kills the hook with no notice, which fails open.

  Alternatives covers why `install.sh` alone, per-hook preflights, a check in `_config.sh` and refusing the session each fall short.
- **M4 — preflight removal.** The two version compares go, and the three per-hook `python3` presence checks stay, each for what it adds (rows 19, 20, 23, 42). `anchors: row3, row4, row19, row20, row21, row22, row25, row40, row41, row42, row49`
  - In the ask hook, a `python3` that passes `-c ''` but cannot run the helper fails step 7 and degrades to ask, the outcome the compare gave (row 19).
  - The nudge keeps its builtin check, so a host without `python3` spawns nothing and creates no checkpoint directory on each prompt (row 20).
  - `parse-manifest-dependencies.py` imports `tomllib` at module top, because the deferral served only a sub-3.11 interpreter (row 25). Every interpreter at 3.11 or newer has `tomllib`, so the hoist changes no outcome a reachable interpreter sees, and no test pins it (Alternatives, row 49).
- **M5 — docs.** A new `docs/hooks.md` § "Python version", after § "Bash version", is the one home for the runtime story, with the content Phase 2 lists. `docs/hooks.md:247` and `docs/security-hardening.md` lose their 3.11 statements, and the documented silent allow stays. `anchors: row19, row23, row24, row32, row45, row51, row52, row53`
- **M6 — ruff lints for 3.14.** `requirements-dev.txt` pins the series the pre-Phase-1 check confirms, and `pyproject.toml` sets `target-version = "py314"`. This upgrades an already-declared package. The engineer runs every fetch in a separate terminal (row 50). Patch-floating `ruff==X.Y.*` stays, the convention `requirements-dev.txt:3` already follows. `anchors: row14, row15, row36, row50, row56, row58, row60, row63`
- **M7 — older-minor removal.** Delete the code, guards and tests that serve only 3.11 through 3.13 (rows 26-28), and the statements of a lower minor (row 29). Comments that state a fact the floor satisfies stay (row 30). `anchors: row26, row27, row28, row29, row30`
- **M8 — CHANGELOG.** One `### Removed` entry first under `CHANGELOG.md:529`, modeled on the git floor's at `:531-537`. Each phase adds the bullets for what its own commit ships. The `[Unreleased]` line at `:56` that states the old floor is rewritten, because it would be false once this ships. `anchors: root, row5, row15, row26, row31`

## Critical files

There are three `code-writer` dispatches, run in order in the shared feature worktree, one commit each, all in one PR. Phase 2 calls Phase 1's `_python3_floor_status`, and Phase 3's code is linted at Phase 1's `py314`, so none runs in parallel. Every phase adds to `CHANGELOG.md`.

**Before Phase 1, one session step** (not a dispatch).

*The ruff check* (rows 14-15, 50, 56). The engineer runs every command that fetches or installs, in a separate terminal. `<dir>` below is the literal path `mktemp -d` printed. Every ruff version is checked against `^[0-9]+\.[0-9]+\.[0-9]+$` before it appears in a command:
1. Read the newest ruff release from `github.com/astral-sh/ruff/releases` and `pypi.org/project/ruff/` only, and name its exact version to the engineer before anything is fetched.
2. Read the wheel's SHA-256 from `pypi.org/pypi/ruff/<X.Y.Z>/json`. GitHub publishes no wheel hash (row 56), so the trust basis is same-origin integrity only: the digest guards corruption or a swap, not a compromised publisher.
3. The engineer runs, in order:
   - `mktemp -d`;
   - `python3.14 -m venv <dir>/venv`;
   - `<dir>/venv/bin/pip download --isolated --only-binary=:all: --no-deps 'ruff==<X.Y.Z>' -d <dir>/wheels`.
4. Check the wheel before anything installs or runs it:
   - `<dir>/wheels` holds one file, and its name matches `^ruff-<X.Y.Z>-[A-Za-z0-9_.-]+\.whl$`, which also confirms the version without executing the wheel;
   - its `shasum -a 256` equals the hash from step 2. Stop and tell the engineer on a mismatch.
5. The engineer runs `<dir>/venv/bin/pip install --isolated --no-index <dir>/wheels/<wheel file>`.
6. From the worktree root, run `<dir>/venv/bin/ruff check claude/.claude/ claude-skills/ plugins/ evals/` twice, with the target version overridden to `py312` (main's) and to `py314`. Record `ruff --version` and each run's findings by file.
7. Stop and ask the engineer in any of these cases:
   - ruff rejects `py314`;
   - the `py314` run has findings. Name the files, and ask whether to add them and their fixes to this list or to try an older series;
   - the `py312` run has findings, because upgrading the shared `.venv` would change other worktrees' lint.
8. Otherwise ask one confirmation to upgrade the shared `.venv`. The question names:
   - the version, and the version `<main checkout>/.venv/bin/ruff --version` reports today (0.6.9 per row 14);
   - that the upgrade reaches every worktree on the machine until this PR merges (`CLAUDE.md` § "Engineering Judgment", blast radius), and that any `./install-dev.sh` run from the main checkout before then reverts it (row 15);
   - the upgrade, which the engineer runs: `<main checkout>/.venv/bin/pip install --isolated --no-index <dir>/wheels/<wheel file>`;
   - the revert: `<main checkout>/.venv/bin/pip install --isolated --only-binary=:all: 'ruff==<prior version>'`.
9. On a yes, delete `<dir>` by its literal path after the upgrade. On a no, keep `<dir>`, run `<dir>/venv/bin/ruff` by path for this PR's lint runs with CI as the authority, and delete `<dir>` once Phase 3's CI passes.

After every dispatch, run the commands from `CLAUDE.md` § "Commands" in the worktree:
- `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py`;
- `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/`, or `<dir>/venv/bin/ruff check …` after a no in the ruff check's step 8;
- `scripts/list-shell-files.sh | xargs -0 scripts/venv-run.sh shellcheck`;
- that same ruff's `--version`. The dispatch fails when it is not in the series `requirements-dev.txt` pins.

**Phase 1 — the floor, its install check, and lint at 3.14** (M1, M2, M6).
- `claude/.claude/hooks/_config.sh`: add the M1 block after `:68`. Its comments are one-line facts:
  - above the block: `install.sh` and `check-python-floor.sh` compare `python3` with this floor, and `test_python_floor.py` ties it to CI's pin;
  - above each function: its return codes, and for `_python3_floor_status`, what it prints.
- `install.sh`:
  - `:5`: point to `claude/.claude/hooks/tests/test_python_floor.py`;
  - `:79-88`: replace with a block that:
    - opens with `# README.md § "Requirements" gives the reason for this floor.`, as `:95` reads;
    - captures `python3_found_version=$(_python3_floor_status) || python3_floor_status=$?`, with the status first set to 0, as `:99-100` does;
    - on status 1, writes the found version and the floor to stderr;
    - on status 2, writes to stderr that `python3` reported no version it could read, and the floor;
    - on either, then writes the path `command -v python3` resolves, or "none found", as `:117` does, then the fix lines, then exits 1;
  - the fix lines, all to stderr, with the floor built from the constants:
    - install Python at the floor or newer so its `python3` resolves first on PATH, for example through Homebrew or with `pyenv global`;
    - re-run `./install.sh`, and restart Claude Code so its hooks resolve the same `python3`;
    - see claude-config's `README.md` § "Requirements";
  - `:1057`: `sudo apt install python${python3_found_version}-venv`. No test reaches it (row 55), so the Phase 1 return quotes this line beside the assignment it reads.
- `claude/.claude/scripts/select-tests.py:694-696`: add one line to `_is_scripts_dir_shell_script_change`'s comment, naming `test_python_floor.py` (`HOOKS_TESTS_DIR`) as a by-path reader of `_ci-python-pin-lib.sh`. It adds no row, so `test_select_tests.py` is unchanged (Alternatives).
- `claude/.claude/hooks/tests/test_python_floor.py` (new), named after `test_git_floor.py` and `test_bash_floor.py`. Write it first and run it red against the unchanged tree. It holds `_ci-python-pin-lib.sh`'s path as the module-level constant `SCRIPTS_DIR / "_ci-python-pin-lib.sh"`, with `SCRIPTS_DIR` imported by name from `helpers` as `test_bash_floor.py:37` does, so `TestCrossDomainReadCompleteness` sees the read (row 43). Its resolver cannot follow an attribute such as `helpers.SCRIPTS_DIR`. Cases:
  - the predicate, sourcing `_config.sh`:
    - status 0 for `3 14`, `3 15`, `3 100` and `4 0`;
    - status 1 for `3 13`, `3 9`, `3 08` and `2 99`;
    - status 2 for `"" ""`, `3 ""`, `3 14a`, `x y`, and a 10-, 20- and 1000-digit minor;
  - `_python3_floor_status`, with a stub `python3` first on PATH, asserting stdout and status:
    - `3 14`, `3 15` and `3 100`: status 0, and stdout is the dotted version;
    - `3 13`: status 1, and stdout is `3.13`;
    - `3 14` on the first line and `3 9` on the second: status 0;
    - `3 13` on the first line and `ZQXJ line two` on the second: status 1, and stdout is `3.13` alone;
    - status 2 and empty stdout for each of: empty output, `garbage`, one field, three fields, exit 1 with no output, `3 14` then exit 1, a banner line before `3 14`, a 1000-digit minor, `ZQXJ 14`, and `3 *` run from a working directory whose only file is named `14`;
    - a stub that also writes to stderr: nothing reaches stderr;
  - the real interpreter, as a `python3` link to `sys.executable`: status 0 with its own `X.Y`. The result is the same with `PYTHONPATH` naming a directory whose `sitecustomize.py` prints `2 7`;
  - the constants:
    - sourcing `_config.sh` with `_PYTHON_FLOOR_MINOR=99` exported yields the same floor as without it (row 48);
    - sourcing it twice in one shell succeeds, and `declare -p` shows neither constant as `readonly`;
  - the install block, extracted between its markers and run under `set -e` after sourcing `_config.sh` by absolute path, with PATH then set to a stub directory, followed by a sentinel line that also echoes `python3_found_version`:
    - `3 14`, `3 15` or `3 100`: reaches the sentinel with empty stderr, and the variable holds the dotted version;
    - `3 13` or `3 9`: exits 1 with no sentinel, and stderr names the found version, the floor, the resolved path and `README.md` § "Requirements";
    - empty output, `garbage`, one field, or a stub that exits 1: exits 1 with no sentinel, and stderr names the floor and says no version was read;
    - a `python3` link to `sys.executable`: reaches the sentinel;
  - the whole script, through `test_git_floor.py`'s whole-script helper, with a `3 13` `python3` and a passing `git`: exit 1, the floor in stderr, and an empty HOME;
  - the presence check with no `python3`, through `_extract_presence_check_block` imported from `test_install_sh_claude_code_version_check.py` (row 47);
  - the three M2 ties, each failure message naming the four places;
  - the cited `README.md` § "Requirements" heading exists, through `test_bash_floor.py`'s `Citation` (row 48).
- `claude/.claude/hooks/tests/test_install_sh_python_floor.py`: delete it.
- `claude/.claude/hooks/tests/test_git_floor.py`:
  - `:50`: drop `python3` from `_TOOLS_THAT_JUST_EXIT_ZERO`;
  - `:236-263`: move the whole-script setup and run into a module-level helper that takes the `python3` and `git` stub scripts. The git test passes a `python3` stub printing the floor's two fields, read by sourcing `_config.sh`, and `test_python_floor.py` imports the helper.
- `claude/.claude/hooks/tests/test_bash_floor.py`: add `readme_requirements_bullet(opener)`, the one README Requirements-bullet lookup that `test_git_floor.py` and `test_python_floor.py` both use (row 59).
- `README.md:111`: replace the bullet per M2. Required content, Phase 1 facts only:
  - opens "`python3` 3.14 or newer, first on the PATH Claude Code starts with";
  - the floor is the one minor CI tests, the `python-version` pin in `.github/workflows/tests.yml`;
  - no list of which OS releases ship which `python3`, and the bullet drops the existing list of stock interpreters (row 46);
  - install Python 3.14 or newer so its `python3` resolves first on PATH, for example through Homebrew, or through pyenv with `pyenv global`, because a version-manager shim resolves `python3` per directory (row 51);
  - a project's own `.python-version` overrides `pyenv global` in that project, so its hooks run the `python3` it pins (row 52);
  - `install.sh` exits before changing anything when `python3` reports a lower version or none it can read.
- `.github/workflows/tests.yml:103-105`: three comment lines. The pin sets the Python minor. `install-dev.sh`, `test_ci_python_matches_dev_interpreter.py` and `test_python_floor.py` read it. `test_python_floor.py` fails when `_config.sh`'s floor, `README.md`'s floor or ruff's `target-version` differs from it.
- `pyproject.toml:3`: `target-version = "py314"`.
- `requirements-dev.txt:3`: `ruff==<the confirmed series>.*`. The confirmed series is 0.17 (row 58).
- `claude/.claude/hooks/tests/test_deny_reviewer_tree_mutation.py:507-509,1050`: correct the comment that says the pinned ruff rejects `--add-noqa=REASON`, and reword the two prose mentions of a `# noqa` directive. ruff 0.17.4's `check --help` lists that flag, and it warns about those mentions (row 63). No behavior change. This edit lands in the commit that follows this plan revision (rows 60 and 63).
- Lint fixes that ruff 0.17.4 requires (row 58). Each is a mechanical change with no behavior change:
  - `claude/.claude/hooks/tests/test_log_reviewer_round.py:167,197`: `SIM115`, two `tempfile.TemporaryFile("w+")` calls. The fix is the `with` form or an `ExitStack`, keeping the file open for the same span the test needs;
  - `claude/.claude/hooks/tests/test_send_back_skipped_self_review.py:799,1974,1975`: `UP031`, three `%` formats. The fix is an f-string with the same padding;
  - `claude/.claude/scripts/tests/test_transcript_period_cost.py:1901,1905`: `UP037`, two quoted return annotations. The fix is `ruff check --fix` on those two lines.
- `CHANGELOG.md`: a `### Removed` entry first under `:529` (M8). Required content:
  - lead: Python older than 3.14 is no longer supported. Install Python 3.14 or newer so its `python3` is first on the PATH Claude Code starts with;
  - 3.14 is the one minor CI tests. `_config.sh` holds the floor, and a test fails when it, `README.md`'s floor or ruff's `target-version` differs from CI's pin;
  - `install.sh` exits before changing anything below the floor or when `python3` reports no version;
  - ruff now lints for 3.14 from a newer series. After pulling, contributors re-run `./install-dev.sh` from the main checkout, and until then `ruff check` rejects `target-version = "py314"`;
  - **Migration:** from the shell that launches Claude Code, run `python3 --version`. If it is below 3.14, install Python 3.14 or newer so its `python3` resolves first on PATH (`pyenv global` for pyenv), restart Claude Code from that shell, and re-run `./install.sh`.

**Phase 2 — the SessionStart notice and the preflight removal** (M3, M4, M5, M8).
- `claude/.claude/hooks/check-python-floor.sh` (new, mode 755), per M3, in row 16's shape. Code above its `_lib.sh` bootstrap stays bash-3.2-clean (`.claude/rules/shell-floor.md`). Required parts, in order:
  - header facts after the `hook-class` line, one line each:
    - it warns on screen and to the agent when the harness PATH's `python3` is missing, reports no version, or is below `_config.sh`'s Python floor;
    - SessionStart cannot refuse a session, so it only warns;
    - every path exits 0, and it prints nothing unless the probe returns 1 or 2;
    - the registration's `startup|resume` matcher and the in-script `.source` filter must stay in sync;
    - only the floor constants and a digit-validated version reach its output;
    - `_lib_jq` builds the notice, so a quote in its text cannot break the JSON line;
    - the agent text limits the agent to reporting;
    - a bash below `_config.sh`'s bash floor fails the `_lib.sh` source, so the hook then exits 0 with no notice;
    - `docs/hooks.md` § "Python version" records that delivery in `-p` mode and to subagents is unverified;
    - it may exceed the 100 ms per-fire budget in `plugins/claude-hook-review/skills/claude-hook-review/SKILL.md` § "7. Performance budget", because it starts `jq` and a `python3` interpreter, and it fires only at session start and resume;
  - `set -uo pipefail` and `exec 2>/dev/null`, with no EXIT trap;
  - `INPUT=$(cat) || exit 0`, then the bootstrap line copied from `record-session-config-keys.sh:42`;
  - one `_lib_jq -r` filter, in the shape of `announce-branch-review-history.sh:32-35`. It prints `.source` only for a payload whose `hook_event_name` is `SessionStart` and whose `source` is `startup` or `resume`, and prints nothing otherwise. Its capture ends `|| exit 0`, and `[ -n "$payload_source" ] || exit 0` follows it;
  - `python3_found_version=$(_python3_floor_status _lib_capped_for 2) || python3_floor_status=$?`, with the status first set to 0, as in `install.sh`, then an exit 0 on status 0;
  - the notice, printed by one `_lib_jq -n --arg screen … --arg agent …` call, in the shape of `announce-branch-review-history.sh:37-40`;
  - an unconditional final `exit 0`.

  Required content of the notice, not final prose:
  - screen: `python3` on the PATH Claude Code started with, in this directory, is `<X.Y>`, or is missing, reported no version, or did not answer within 2 seconds, and claude-config's hooks support Python `<floor>` or newer. Run `env python3 --version` in this directory before installing, because a slow answer is not a missing Python. If it is below the floor, install Python `<floor>` or newer, put its `python3` first on that PATH, and restart Claude Code. See `README.md` § "Requirements" in the claude-config repository;
  - agent: the same, plus: hooks that run `python3` are unsupported until then, and `docs/hooks.md` § "Python version" lists what each does if its helper fails. In your next reply, tell the engineer and point them to `README.md` § "Requirements". Do nothing else about it unless the engineer asks.
- `claude/.claude/hooks/tests/test_check_python_floor.py` (new). PATH is a stub directory followed by a `build_path_without("python3", …)` farm of the real PATH (row 47). Stubs log each call. Every case asserts empty stderr. Payloads carry `hook_event_name` `SessionStart` and `source` `startup` unless a case says otherwise. Cases:
  - a stub printing `3 14` or `3 15`, and a `python3` link to `sys.executable`: exit 0, no stdout;
  - a stub printing `3 13`, once with `source` `startup` and once with `resume`: exit 0, and `json.loads` of stdout gives one object whose only keys are `systemMessage` and `hookSpecificOutput`. `hookEventName` is `SessionStart`, and both texts name `3.13`, the floor and `README.md` § "Requirements";
  - a stub printing `3 13` and then `ZQXJ line two`: the `3.13` notice, and neither text holds `ZQXJ`;
  - no `python3` (the farm alone), a stub that exits 1, and stubs printing `garbage`, `ZQXJ 14` or a 1000-digit minor: `json.loads` of stdout succeeds, the notice says `python3` is missing or reported no version, and neither text holds the stub's output;
  - a stub that sleeps past the cap, under `write_scaled_timeout_shim` and `assert_cap_engaged(…, production_cap=2)`: the no-version notice. It skips when neither `timeout` nor `gtimeout` exists, and carries the `timing` mark, as `test_friction_count_timeout_is_silent` does;
  - exit 0, no stdout, and no stub call for each of:
    - `hook_event_name` other than `SessionStart`;
    - `source` `clear`, `compact` or `fork`, an absent `source`, and `source` `["startup"]`;
    - non-JSON stdin and empty stdin;
    - a `3 13` stub with the farm built by `build_path_without("jq", …)` instead;
  - the registration, modeled on `test_record_session_config_keys.py:478-499`: exactly one SessionStart group registers `~/.claude/hooks/check-python-floor.sh`, and no other event does. Its matcher is `startup|resume`, it has no `if`, its `timeout` is 10, and the hook file exists and is executable.
- `claude/.claude/settings.json`: a SessionStart entry with `"matcher": "startup|resume"` and `"timeout": 10`, after the `record-session-config-keys.sh` entry (`:203-209`).
- `claude/.claude/hooks/tests/test_bash_floor.py`: add `docs/hooks.md` § "Python version" to `TestFloorMessageCitationsResolve`'s parametrization (rows 48, 59).
- `docs/hooks.md`:
  - a `- **`check-python-floor.sh`** (SessionStart, `matcher: startup|resume`, informational)` bullet beside the `record-session-config-keys.sh` one (`:204`). Required facts:
    - what the hook warns about;
    - the two channels, screen and agent context;
    - it prints nothing otherwise;
    - it cannot stop the session;
    - a pointer to § "Python version";
  - a new `## Python version` section directly after § "Bash version". Required content:
    - hooks resolve `python3` on the harness PATH from the session's working directory, so a version-manager shim can resolve a different `python3` per project (row 51);
    - `README.md`'s Requirements section states the floor, and `_config.sh` holds it;
    - at session start and resume, `check-python-floor.sh` warns, and nothing else checks the version at runtime;
    - whether the notice renders in `-p` mode, and whether SessionStart fires for subagents, is unverified, so a headless run needs `python3` fixed beforehand (row 32);
    - on a stock macOS without the Command Line Tools, the probe may open their install dialog at each startup and resume (row 53);
    - row 23's list, each outcome stated for a helper that exits nonzero, with `require-worktree-for-git-writes.sh`'s full scope;
    - scripts run `python3` too, for example `marker.sh clear-stale` (row 45), and the floor covers them;
    - the recovery, as a numbered list: install Python at the floor or newer from a separate terminal; put its `python3` first on the PATH Claude Code starts with (`pyenv global` for pyenv); check with `env python3 --version` from that shell; re-run `./install.sh`; restart Claude Code;
    - after the list: a project's own `.python-version` overrides `pyenv global`, so in a project that pins a version below the floor the notice repeats at each startup and resume until that pin resolves to the floor or newer (row 52);
  - `:247`: delete "(Python >= 3.11 floor, since `Cargo.toml`/`pyproject.toml` parsing needs stdlib `tomllib`)" and "or a present-but-usable `python3` falls below the 3.11 floor (a broken install, not an expected runtime state)".
- `README.md`: add to the Python bullet, one sentence each:
  - at session start and resume, `check-python-floor.sh` warns when the hooks' `python3` is missing or below the floor;
  - it cannot stop the session;
  - `docs/hooks.md` § "Python version" covers headless runs, per-project version pins and the rest.
- `claude/.claude/hooks/ask-new-dependency-disclosure.sh`: delete `:197-201`. Replace the header's `:55-59` sentence with one line: a `python3` below README.md's Python floor gets no check of its own, and a helper it cannot run fails step 7, which degrades to ask.
- `claude/.claude/hooks/tests/test_ask_new_dependency_disclosure.py`:
  - delete the `skipif` decorators at `:507-510` and `:571-574`;
  - delete `TestParseManifestDependenciesPythonFloor` (`:684-717`);
  - delete `_python3_version_floor_shim` and `_extract_version_check_argument` (`:939-971`);
  - delete the two tests at `:1376-1401` and `TestVersionCheckArgumentRealBoundary` (`:1404-1421`);
  - add a characterization test to `TestHookDegradedAskDistinguishedFromOrdinaryAsk`. A `python3` stub that exits 0 on `-c ''` and 1 on anything else degrades a manifest edit that adds `lodash` to ask, with no `@` in the reason;
  - let ruff flag the imports left unused.
- `claude/.claude/hooks/nudge-error-mode-analysis.sh`:
  - drop "python3 older than 3.11," from `:41-42`;
  - delete the version compare (`:128-130`) and keep the `command -v python3` check (`:125-127`);
  - replace step 7's comment (`:120-124`) with one line: a host without `python3` exits here, before the checkpoint directory and the friction-count spawn. The step numbers stay 1 through 9.
- `claude/.claude/hooks/tests/test_nudge_error_mode_analysis.py`:
  - `test_python3_missing_is_silent` builds its PATH with `build_path_without("python3", …)` from `helpers` (row 47). It keeps its assertions, adds that no `.claude/.error-mode-nudge-checkpoint.d` exists under the sandboxed home, and its docstring names the presence check, not a preflight;
  - delete `_restricted_path_without_python3` (`:160-169`), its only caller gone, and drop the clause naming it from `:179`'s docstring;
  - rename `test_python3_too_old_is_silent` to `test_python3_that_fails_every_call_is_silent`. Its shim becomes `exit 1` alone, and its docstring says it covers a present `python3` that fails every call;
  - delete the `[ "$1" = "-c" ] && exit …` line from the shims at `:443`, `:501`, `:520` and `:542`, and the version-preflight mention from `:493-495`'s docstring.
- `claude/.claude/hooks/parse-manifest-dependencies.py`: delete the docstring paragraph at `:7-13`, move `import tomllib` to the module's imports, and drop `:304-306`'s deferral sentence from `_parse_toml_manifest`'s docstring.
- `docs/security-hardening.md`: delete the `:332-334` requirement sentence, so the paragraph goes on from "It covers six manifest formats". Delete "or a present-but-usable `python3` below the 3.11 floor (a broken install, not an expected runtime state)" at `:351-353`.
- `CHANGELOG.md`: add to Phase 1's entry:
  - `check-python-floor.sh` warns on screen and to the agent at session start and resume when the hooks' `python3` is missing or below the floor, and it cannot stop the session;
  - the version checks in `ask-new-dependency-disclosure.sh` and `nudge-error-mode-analysis.sh` are gone. `docs/hooks.md` § "Python version" lists what each Python-running hook does when its helper fails;
  - the hook changes are live on `git pull`, and `install.sh` checks on its next run.

**Phase 3 — older-minor code** (M7).
- `claude/.claude/scripts/transcript_analysis/review_cost_by_size.py`: delete `_average_ranks` (`:151-164`). `_spearman_rho` returns `statistics.correlation(xs, ys, method="ranked")` and keeps its `StatisticsError` arm, and its docstring loses `:170-171`.
- `claude/.claude/scripts/tests/_move_fidelity.py`: delete `:16` and `:285-287`.
- `claude/.claude/scripts/tests/test_transcript_move_fidelity.py`: delete `test_nodes_rename_exits_2_when_tokenize_reports_no_f_string_tokens` (`:280-288`).
- `claude/.claude/scripts/tests/test_plan_push_preflight.py`: delete the docstring's `:8-9`, `_fstring_offenders` (`:780-795`) and `TestPythonFloor` (`:798-808`).
- `claude/.claude/scripts/tests/test_pr_create_preflight.py`: delete `:10`, the `_fstring_offenders` import (`:30`) and `TestPythonFloor` (`:807-817`).
- `claude/.claude/scripts/tests/test_plan_shape.py`: delete the `_fstring_offenders` import (`:25`) and `TestPythonFloor` (`:1335-1345`).
- `claude/.claude/scripts/tests/test_transcript_session_pricing.py`: delete `test_changed_modules_parse_at_the_python_3_11_grammar` (`:1840-1854`).
- `claude/.claude/scripts/tests/test_reconcile_project_plugins.py:1508`: delete the `sys.orig_argv` sentence.
- `evals/README.md:497-500`: drop the Python sentence and its "floor elsewhere is 3.11" clause, so the text says the harness needs Git 2.32 or newer, citing `review_bench/local_git.py`.
- `docs/scripts.md:37`: drop "; requires Python 3.10+ (uses `X | Y` union-type syntax)".
- `CHANGELOG.md`: add to the entry that the code and tests that served Python 3.11 through 3.13 are gone. Add to its **Migration:** line that user-run scripts need the floor too, naming `transcript-analysis.py review-cost-by-size`, which fails on an older `python3` (row 26).
- In every Phase 3 file, let ruff flag the imports left unused (for example `ast`, `io`, `sys`, `tokenize`).

Projected diff: about 1,340 changed lines across 31 files, `select-tests.py` and every test file above included (Phase 1 about 630, Phase 2 about 585, Phase 3 about 125), plus any lint fixes the pre-Phase-1 check makes the engineer add. This revision adds about 70 lines over the previous projection: new probe and hook test cases, the hook's longer header and `_lib_jq` notice, and the nudge test's new PATH, less the dropped `tomllib` test and the smaller nudge deletion.

## Verification

1. **After each dispatch:** run the commands under Critical files. Phase 1 touches `pyproject.toml` and `select-tests.py`, so `select-tests.py` selects the full suite (row 14). Every new or changed test passes, and the return records which domains `select-tests.py` selected and the ruff version.
2. **Red, then green.** Each dispatch return records both runs:
   - Phase 1: `test_python_floor.py` against the unchanged `_config.sh`, `install.sh`, `README.md` and `pyproject.toml`;
   - Phase 2: `test_check_python_floor.py`'s below-floor case against an empty `check-python-floor.sh` stub, and its registration case before the `settings.json` entry exists;
   - Phase 2: the new ask-hook test is a characterization test, under the refactoring exception in `claude-skills/skills/test-conventions/SKILL.md` § "2. Test-first invariant". It passes against the unchanged hook through the version compare (`:200-201`), and after the deletion through step 7. Step 3's `HELPER_EXIT` mutation is the substitute proof for step 7;
   - Phase 2: `test_python3_missing_is_silent`, now on the real-PATH farm, passes before and after the nudge's compare goes, because the presence check it covers stays. Step 3's presence-check mutation is its red run. The return also records that the test now reaches step 7 on macOS, which its hand-picked PATH did not (row 22).
3. **Mutation runs.** Each is one line, reverted after its run, and recorded in the return:
   - Phase 1:
     - `_PYTHON_FLOOR_MINOR=13` turns the `3 13` cases red;
     - the CI pin set to `'3.13'`, `pyproject.toml` set to `py313`, and README's floor set to 3.13 each turn exactly one tie red;
     - a string comparison of the minor turns the `3 100` cases red;
     - dropping the 1-to-9-digit bound turns the 10- and 1000-digit cases red;
     - dropping `-I -S` turns the planted-`sitecustomize` case red;
     - keeping the probe's output despite a nonzero exit turns the `3 14`-then-exit-1 case red;
     - printing `X.Y` on status 2 turns the `ZQXJ 14` case red;
     - reading the first line's fields through an unquoted `set -- $first_line` turns the `3 *` case red;
     - reading every line instead of the first turns the `3 13`-then-`ZQXJ line two` case red;
     - marking `_PYTHON_FLOOR_MINOR` `readonly` turns the second-source case red;
     - moving the python block below `:127`'s `mkdir` turns the whole-script case red;
     - dropping `HOOKS_TESTS_DIR` from the `_is_scripts_dir_shell_script_change` row (`select-tests.py:867`) turns `test_select_tests.py`'s `test_every_resolved_path_selects_a_target_covering_its_reading_test` red;
   - Phase 2:
     - dropping the event-and-source filter turns the other-event, `clear`, `compact`, `fork`, absent-`source` and array-`source` cases red;
     - dropping `[ -n "$payload_source" ] || exit 0` turns the empty-stdin case red;
     - printing nothing on status 2 turns the missing-`python3` case red;
     - building the notice with the peer's `printf` shape turns the `json.loads` cases red, because the text cites `README.md` § "Requirements";
     - running the probe under `_lib_capped` instead of `_lib_capped_for 2` turns the timed case red;
     - in the ask hook's `HELPER_EXIT` arm (`:213-215`), replacing `_emit_degraded_ask` with `exit 0` turns the characterization test red;
     - deleting the nudge's `command -v python3` check turns `test_python3_missing_is_silent` red through its checkpoint-directory assertion;
     - removing the registration's matcher or its `timeout` turns the registration case red;
   - Phase 3: `method="linear"` in `_spearman_rho` turns `test_tied_series_use_average_ranks_and_each_measure_has_its_own_rho` red.
4. **The issue's acceptance bullets:**
   - the drift test across ruff's `target-version`, the floor constant and README's minimum against the CI pin is `test_python_floor.py`'s three ties, which pass;
   - "install.sh message naming 3.14": the install-block cases assert the floor from `_config.sh` in stderr, and the pin tie fixes that floor at 3.14;
   - `select-tests.py` and lint: step 1, after each phase;
   - the grep: after Phase 3, run the issue's three patterns with `([^0-9]|$)` in place of `\b`, because `staff-product-engineer` found `\b` matches nothing in `git grep -E` on macOS. The command is `git grep -nE '3\.1[0-3]([^0-9]|$)|\(3, ?1[0-3]\)|py31[0-3]' -- ':!.claude/plans/'`, which leaves out this plan because it quotes the patterns. The Phase 3 return lists every hit with the entry it maps to in the issue's "Not floor references" list or this plan's Out of scope, and an unmapped hit fails the step;
   - as a supplement, `git grep -nE '3\.11 floor|floor (elsewhere )?is 3\.11|below the 3\.11|>= ?\(?3[.,] ?11|< ?\(?3[.,] ?1[12]([^0-9]|$)|older than 3\.11|feature_version|python_31[12]|Python 3\.12 or later|3\.12 or newer|Python 3\.10\+|python3\.12-venv|"Python 3\.12"|ruff==0\.6' -- ':!.claude/plans/' ':!docs/reports/' ':!CHANGELOG.md'` has no hits, and `git grep -n version_info -- 'claude/.claude/hooks/*.sh' install.sh` hits only `_config.sh`'s probe;
   - `README.md` names no OS release's `python3` version (row 46);
   - superseded: "makes the shared hook preflight exit 0 silently" (row 3), in whose place `test_check_python_floor.py`'s warning cases pass. Also superseded: `install.sh` reading the pin itself through `read_ci_python_pin` (M1), in whose place the pin tie passes.
5. **CI.** CI runs the full suite on the PR. The lint step installs the pinned ruff, lints at `py314` and passes, and `test_ci_python_matches_dev_interpreter.py` and `test_python_floor.py` pass on 3.14.
6. **Engineer-run, before merge.** On each machine and account where Claude Code runs, `python3 --version` in the shell that launches Claude Code reports 3.14 or newer. Once, start an interactive session with a stub `python3` that prints `3 13` first on PATH:
   - confirm the screen notice appears and the agent reports it in its first reply;
   - run `/clear` and confirm no second notice appears;
   - end that session, because the stub breaks every other hook that runs `python3`.

   The PR body records one pooled pass or fail, never per-machine rows.
7. **Reviews.** `/code-review` runs on each commit. `claude-hook-review:claude-hook-review` runs on Phase 2's new hook, its `settings.json` registration and the two edited hooks. No SKILL.md, agent or plugin file changes, so no skill-review, agent-review or plugin-semver gate applies.

## Out of scope

- **Comments and test data that state a fact the floor satisfies, or use a version as data** (row 30): `post-crash-sessions.py:304`, `test_post_crash_sessions.py:290`, `transcript_analysis/scope.py:219`, `test_log_reviewer_round.py:164`, `evals/test_review_bench_mining.py:1244,1249`, `transcript_analysis/instrument_authoring.py:42` and its test at `:345-349`, the GNU grep 3.11 notes in `deny-private-project-refs.sh` and `deny-pii-in-commits.sh`, `install-dev.sh:61`, and the version fixtures in `test_install_dev.py`, `test_ci_python_matches_dev_interpreter.py` and `test_ci_python_pin_lib.py`.
- **`_ci-python-pin-lib.sh`'s header and messages.** `install.sh` does not call it (M1), and a test sourcing it is not a shipped caller.
- **`docs/config-file.md:40`.** It says where `tomllib` reads `claude-config.toml`, not a floor.
- **Dated records:** `CHANGELOG.md:51-55` and older entries, and `docs/reports/**`.
- **A Python-floor clause, or a stop-and-report instruction, in the deny texts at `require-worktree-for-git-writes.sh:298` and `_lib.sh:2591`** (Alternatives, row 5).
- **A general dependency check at SessionStart** (rows 40-42). The session defers it to a follow-up issue, and until then the three per-hook `python3` presence checks stay (M4). It replaces none of `install.sh:36`'s presence check, `_config.sh`'s bash floor or `_lib_capped_for`'s uncapped fallback, so it adds code rather than removing it. It would also need:
  - a tool list shared with `install.sh`;
  - a policy for tools whose absence is supported, such as `timeout`;
  - an event filter that does not go through `_lib_jq`, since `jq` is one of the tools it would check.
- **A kill switch for `check-python-floor.sh`, including for a project whose own version pin resolves below the floor** (row 52). The warning goes away when the `python3` the hooks run meets the floor. In such a project the hooks do run the pinned `python3`, so the repeated notice is accurate there. The engineer chose to document the absence rather than add one (row 61).
- **A Python floor for the plugins** (row 33). No plugin states one, plugins install separately, and stating one is a plugin version change outside the Ask.
- **A test that no other file defines the Python floor.** The bash floor has one because five copies exist, and the Python floor has one home.
- **A `.claude/rules/` line for the Python floor.** Ruff's target and the M2 ties enforce it.
- **Re-checking which helpers and scripts run on 3.11 through 3.13 today** (rows 24, 45).
- **`test_ci_python_matches_dev_interpreter.py:9`'s note about a future floor job.** With the floor equal to the pin, it stays accurate.
- **A ruff-series check in `install-dev.sh`'s `check_venv_healthy` or in `venv-run.sh`.** The CHANGELOG names the stale-venv symptom instead.
- **Hash-pinning ruff in `requirements-dev.txt`.** Patch-floating `ruff==X.Y.*` is the existing convention, and the ruff check's hash check covers only the wheel this machine installs.

**Not verified by the architect:** the issue body, and every `[unverified]` claim above (G3; rows 4, 5, 14, 24, 26, 34, 36, 41, 42, 50, 51, 52, 53, 54, 55, 57, 62, 63).
