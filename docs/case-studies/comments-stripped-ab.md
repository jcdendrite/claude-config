# Do code comments help agents write correct code? A comments-stripped A/B experiment (pre-registered design)

This page is a pre-registration. The tasks, arms, outcome, analysis and decision rule are fixed here before the first authoring session runs. No result exists yet, and the `Results` section says so.

The *harness* is the code that builds snapshots and environments, runs sessions and tests, and scores them. Two roles recur, and one person may hold both. The *experimenter* builds and runs the harness, and decides at the lock whether the study goes ahead. The *repository owner* decides each case this page routes to them. The `Revisions` section defines the lock and the rules for changing this page.

This page fixes, as requirements, what decides a result and what a session may reach. How the harness meets them, and every value it sets, is fixed by the harness commit and the manifest the lock records before any authoring session.

## Why this measurement is needed

GH-1408 asks whether the comment rules in `claude/.claude/CLAUDE.md` § "Durable text" should be narrowed. The issue holds the evidence, two decisions that this page takes as given, and one open question:

- The accuracy rule is kept.
- The "don't restate the code" rule is kept.
- Narrowing to "comment only for a non-obvious why" is not decided.

The issue's transcript sample could not rule out a substantial dependence on comments. It leans toward "no" because it sees only reads that were followed by an edit. No document yet fixes how to measure whether comments help an authoring agent, so a later study could re-decide its tasks, outcome or decision rule after seeing data. This page fixes them first.

## Question and arms

The question: does stripping comments and docstrings from the code an authoring agent reads lower how often its change passes the merged PR's held-out tests?

The *intact arm* gets the snapshot as committed, and the *stripped arm* gets it after the stripping described below. Both arms share the model, the prompt, the tools, the config template, the confinement, the environment and the per-session budget cap. The model is the one this repository routes code writing to, and the task manifest records its exact ID.

An authoring *session* edits the working tree, may run tests, and stops without committing. The *study* is every session this page schedules.

No review pipeline runs in either arm. That keeps two comment checks off in both arms: `comment-discipline-reviewer`, and the inline "Non-durable comment" check that `/code-review` runs even when it spawns no such reviewer.

## Tasks

### Eligible pull requests

A merged pull request (PR) is eligible when all of these hold:

- It merged to `main` as one commit.
- It has a prompt issue.
- Its diff changes at least one non-test file named `*.sh` or `*.py`. Eligibility is deliberately by file name: a file with only a shell or Python shebang is stripped but does not make a PR eligible.
- It changes none of `requirements-dev.txt`, `pyproject.toml` and `.github/workflows/tests.yml`, so one environment serves the base and the merge.
- Its base commit predates the first commit that adds any study artifact (this page, its plan file under `.claude/plans/`, or anything the harness holds in this repository), so no base commit holds a study artifact.
- It has at least one fail-to-pass test.

Terms used above and below:

- A *test file* sits under a directory named `tests` or `fixtures`, or is named `conftest.py` or `test_*.py`, so support modules in a test directory count. A PR's test files are those its diff adds, changes or deletes.
- The *held-out test files* are the test files a session never sees and scoring overlays. For a PR task they are the PR's test files at the merge commit, and for a trap they are the test files hand-built with it.
- A *fail-to-pass test* fails at the base, the merge commit's first parent, with the PR's test files overlaid, and passes at the merge commit.
- *Pass-to-pass tests* are the other tests in those files that pass at both.
- A test absent from the results or not collected, for instance because a new module's import fails, counts as failing wherever this page reads results.
- Text *names* an issue when it matches `GH-<n>` or a closing keyword (close, closes, closed, fix, fixes, fixed, resolve, resolves, resolved) followed by `#<n>`, case-insensitive. The PR's own number, such as the `(#<PR number>)` suffix a squash merge adds to a title, is not an issue.
- The *prompt issue* is the issue the PR title names, or, when the title names none, the single issue the PR closes, meaning the issue that GitHub's closing-reference data records the PR as closing. A PR whose title names more than one issue, or that names none and closes other than exactly one, is excluded.

Classification runs in snapshots, never in a checkout with history. It runs each test ten times per commit, a cost choice, and drops a test whose result varies.

### Prompt

The prompt is a fixed preamble followed by the prompt issue's body verbatim. The preamble is identical in both arms and says nothing about comments. The prompt never includes the PR body or a plan file.

A task is excluded when either of these holds:

- The harness cannot show that the body is the issue's first revision, since a later edit can state the merged answer. This page names no GitHub mechanism for showing that.
- The prompt names the PR, its branch or a commit.

### Filters

The *specification filter* excludes a task whose prompt omits a name, flag or path that its fail-to-pass tests need and the base does not hold.

The *why-comment filter* reads comment text from the base snapshot alone, before any session. The *touched region* is each function, or contiguous top-level shell block, that the merged diff changes, in any file and test files included, as it stands at the base. A comment directly above a function, decorator or block counts as inside it. A Python change outside every function, such as a module constant or class attribute, adds no region, and a function includes its decorators. A task is kept only when its touched region holds a comment or docstring, absent from the stripped-arm snapshot under the preserve list the lock freezes, stating a reason, constraint or contract the code does not show. A task whose non-test `*.sh` and `*.py` file changes are all new files is excluded.

A *PR task* is an eligible PR that passes both filters, the stripping checks and the loaded-configuration check. The page reports exclusions per filter and check.

One rater makes every filter judgment. A second rater independently judges a random 30 of the PRs that reach the specification filter, or all of them when fewer.

### Trap tasks

At least 10 *trap tasks* pass all their checks by the lock. Ten is a floor chosen for cost. A trap that fails a check is repaired or replaced before the lock. A trap is hand-built at one fixed base commit, which meets the eligibility list's base-commit condition, and is not a merged PR.

Each trap starts from a comment stating a rule the code does not show. Its task statement invites breaking the rule and does not mention it. Its held-out test fails at the base because the requested change is absent, and once the change is made it fails exactly when the rule is broken. Before any session, each trap passes three checks, the last two at both the intact and the stripped base:

1. The rule's key terms, a non-empty list fixed when the trap is built, all occur in the intact comment, and none appears in any part of the stripped-arm snapshot under the preserve list the lock freezes.
2. A reference implementation that breaks the rule passes every test that passes at the intact base with the trap's held-out test files overlaid, and fails the held-out test.
3. A reference implementation that keeps the rule passes the held-out test.

Traps also pass the stripping checks and the loaded-configuration check. Wherever this page reads a trap's tests, scoring included, its held-out test files stand in for the PR's test files at the merge commit, the held-out test is its fail-to-pass test, and its other tests are its pass-to-pass tests. The keep-the-rule reference implementation stands in for the merge commit in the stripping checks only, since scoring starts from the intact base. Traps never enter `Delta`, and the decision rule reads them for the Harm excluded outcome only.

This page assumes that if each trap shows the full built-in effect or none, four effect-showing traps out of ten suffice for the trap condition of the Harm excluded outcome. A bootstrap draw then holds no effect-showing trap with probability 0.6 to the tenth power, about 0.006. With three, that probability is 0.7 to the tenth power, about 0.028. Against the 0.025 tail of the 95% interval, the condition needs about four.

GH-1408's own trap example fails the first check. A comment in `claude/.claude/hooks/_config.sh` states a bash 3.2 rule. `.claude/rules/shell-floor.md` and `docs/hooks.md` state the same rule in markdown, which both arms keep. A trap needs a rule that appears only in a comment.

### Task manifest

The lock commits the *task manifest*:

- The PR tasks in a seeded random order, with its seed.
- Per task, its filter labels, test IDs, base and merge commits, merge date, issue creation time and PR first-commit time.
- Each prompt with its hash.
- The trap tasks: each trap's base commit and a hash of its hand-built tree including its held-out test files, then its prompt with hash, test IDs, key-term list and reference-implementation hashes.
- The model's exact ID and stated training-data cutoff.
- Every other value the harness sets for sessions, test runs, scoring or the analysis.
- Per task, the installed package list and host tool versions.
- The preserve list.

## Stripping

The stripped arm's whole snapshot is stripped, tests included, in the files named `*.sh` or `*.py` and the files whose first line is a shell or Python shebang. Every other file is identical in both arms.

Stripping preserves line numbers. A comment-only or docstring line becomes empty, a trailing comment goes with the whitespace before it, and a Python body left empty gets `pass`.

A `#` is a comment only where the language's parser reads one. In shell, `$#`, `${#...}`, a parameter-expansion `#` and a `#` inside a quoted string or heredoc body are not comments.

The *preserve list* names comments both arms keep. It starts with every category that a tool, test or program at the base reads:

- Shebangs.
- Tool directives such as `# shellcheck`, `# noqa`, `# fmt:` and `# pragma`.
- The hook headers and heading citations that hook tests read.
- Docstrings that code reads through `__doc__`.

The *stripping checks* run per task before any session:

- Every stripped shell file passes `bash -n`.
- Every stripped Python file's syntax tree equals the intact file's after the same docstring removal.
- The whole test suite fails the same tests at the stripped base as at the intact base.
- At the stripped base with the held-out test files overlaid intact, pass-to-pass tests pass and fail-to-pass tests still fail.
- At the merge commit with its non-test code stripped, fail-to-pass and pass-to-pass tests pass.
- Scoring, given the merge commit's non-test files as the session tree (stripped in the stripped arm), reports every fail-to-pass and pass-to-pass test passing.

A failure caused by stripping a comment that something reads adds that comment to the preserve list. A task that still fails is excluded and counted. The lock freezes the list.

Read-time stripping is set aside for the reason GH-1408 gives. It leaks through `grep`, `cat` and `git show`, and it breaks `Edit` in the stripped arm only.

## Snapshot, environment and confinement

**Snapshot.** A snapshot is a fresh repository holding one commit: the tree of a base, a merge commit or a trap reference implementation, stripped or not, with a constant author, date and message and no remote. It is built from `git archive` the way `evals/review_bench/fixture_repo.py` builds review-bench fixtures. Every session and every test run uses one.

**Environment.** There is one per task, built from the base's own pinned Python version and dependency files. Nothing a session or test run does can change what another session, test run or scoring sees. Both arms of a task show the same installed package list. This page names no build recipe and no host tool list.

**Config.** Each session and each test run uses its own copy of one frozen config template that holds nothing from the experimenter's config. A session holds only a credential dedicated to the experiment, which can do nothing but call the model API and is limited in spend at the issuer. It is revoked after the last session, and replaced if exposed. No session transcript is committed, posted or published.

**Loaded configuration.** The *CLI* is the Claude Code CLI the sessions run. The harness must reach this outcome, and this page names no CLI mechanism for it:

- No plugin loads, though the snapshot's `.claude/settings.json` enables plugins in both arms.
- No hook, no web tool, and no skill beyond the snapshot's own loads.
- The snapshot's own project instructions and skills load in both arms.

This page expects the snapshot's own project instructions to be its root `CLAUDE.md` and `.claude/rules/`, less what `claudeMdExcludes` names, and its project skills to be those under `.claude/skills/`. The harness confirms that expectation.

The *loaded-configuration check* starts the CLI once per task and arm, traps included, before any authoring session and under the session conditions, the confinement included. A mismatch with the outcome excludes the task. A session whose loaded plugins, hooks, web tools, skills or project instructions still differ from that outcome counts as a frozen-condition change, not a harness error, and invalidates its run block.

**Confinement.** Every session and every test run executes in an environment that holds nothing beyond its own inputs. A *test run* is a harness execution of tests, distinct from tests a session runs inside its own environment. The environment holds no copy of, and no handle to, any item below. The one exception is that a test run may hold the task's other arm, another session or test run, another commit, the held-out test files, or merge-commit code and trap reference implementations, and only when that item is that run's own input. A session holds none:

- A live checkout of this repository.
- The experimenter's home or config directory.
- Any environment variable or credential from the experimenter's own environment, other than the credential dedicated to the experiment, which is held only by a session, by the loaded-configuration check and by the confinement verification's turn in the session environment, and by no test run.
- The task's other arm, or any other session or test run.
- Any commit but its snapshot's own.
- The held-out test files, a trap's included.
- Merge-commit code and the trap reference implementations.
- Any other process on the host.
- Any network host but the model API.

Four further rules apply:

- The harness enforces this before a session or a test run starts. Detection after one supplements the enforcement and never replaces it.
- The harness treats a finished session's tree as data. It reads the tree only after every process in the session's confinement has ended. It runs any tool on the tree only inside the confinement and never runs git on it from the host.
- The experimenter picks the mechanism when building the harness, and the lock records the harness commit.
- The confinement *cannot be built* when no environment meeting it can be made, or when the CLI cannot complete a turn inside one. The repository owner then decides what follows.

**Frozen conditions.** This page applies the freeze-and-invalidate pattern of `evals/README.md` § "Frozen conditions and invalidation", with its own definition of a block. A *run block* is a span of sessions and test runs between two records of the CLI version, the config template, and the conditions the manifest and the lock fix. A change between the records, or a difference from the manifest, invalidates the block.

## Outcome and validity

**Scoring.** Scoring starts, in both arms, from a clean intact snapshot of the base, so the scored trees differ only in what the session wrote. It admits only regular files inside the session's tree, less `.git/`, bytecode and test caches. It reports per arm how many entries it excluded. It applies each admitted file the session added, changed or deleted as a whole file, with its executable bit and no other mode bit, since a stripped-arm edit can fail to apply as a line diff to intact text. It skips test files and every file that configures test collection or execution, including `pyproject.toml` and any such file the base lacks, so scoring runs the base's configuration only. It then overlays the PR's test files as they stand at the merge commit, intact in both arms. The tests run in the snapshot's environment.

A session *passes* when every fail-to-pass and pass-to-pass test passes. A failing test reruns once and fails only when both test runs fail.

**Valid replicates.** The harness classifies how a session stopped before any scoring. A *valid replicate* is a session that stopped with no harness error and was not invalidated. A *harness error* is an API or CLI failure, as a rule fixed before the first session classifies the stop. Every other stop is a valid replicate unless invalidated, whatever its reason, the budget cap, another session limit or a crash from the session's own command included. A harness error reruns the session once, and a second one leaves the replicate missing. The page reports per arm the counts of sessions stopped at the budget cap or another session limit, harness errors and missing replicates.

**Invalidation.** A leak invalidates a session, as the leak list says. A frozen-condition change invalidates every session in its run block. An invalidated session's outcome never enters `d_task`, the worst-case recomputation, a revision or any other figure or text the page reports. It reruns under the corrected condition. After a frozen-condition change the corrected condition is the frozen conditions. After a leak it is a new harness commit that closes the route the leak involved and passes the confinement verification. Either rerun is recorded in a dated revision and changes no manifest value. The rerun is classified as any other session is.

A *leak* is evidence that a session or a test run reached something outside its own inputs, or that its environment held a route to it. When one is found:

- It invalidates every session under the harness commit the leak occurred under, since the gap was open for all of them.
- The dated revision that records it names the kind of route involved, never its path, host or command, and states whether the experimenter, or a person or model session acting for them, had read any scored outcome of the invalidated sessions, or a figure computed from one, when the leak was found.
- The repository owner decides anything else that follows.

Leak review covers every session and test run alike. It finishes before the experimenter, or a person or model session acting for them, reads any scored outcome or a figure computed from one. For the reruns after an invalidation, it finishes before the experimenter, or a person or model session acting for them, reads a scored outcome of those reruns or a figure computed from one. A leak found after that still invalidates.

**Not measured.** Diff similarity to the merged diff is not measured, because many correct diffs exist.

## Sample size, feasibility pass and lock

**Sizing.** Every figure uses the grid `psi` = 0.10, 0.20 and 0.30, where `psi` is the probability that an intact-arm session and a stripped-arm session of one task disagree on passing. It is an assumption, centered on GH-1408's 0.20. The other inputs are `d` = 0.15, the margin (see the decision rule), `z_a` = 1.95996 from two-sided alpha 0.05, and `z_b` = 0.84162 from power 0.80. Two counts follow:

- The Harm count comes from McNemar's normal-approximation formula `n_harm = (z_a*sqrt(psi) + z_b*sqrt(psi - d^2))^2 / d^2`. It is 33, 68 and 103 at `psi` = 0.10, 0.20 and 0.30. It is a proxy, because the verdict reads an interval, not McNemar's test.
- The Harm excluded count is where the Harm excluded bound (below) reaches 0.80: `n_harm_excluded = psi*((z_a + z_c)/d)^2`. It is 38, 75 and 112. `z_c` is the standard normal 0.825 quantile, because the bound subtracts the 0.025 that falls in the Stripped better outcome.

Each arm runs `k` = 3 independent replicate sessions per task. Both arms of a task run within one run block, and the order of the arms and replicates is random. The study runs the first `n` PR tasks in manifest order, where `n` is the smaller of the PR task count and 75, the larger count at `psi` = 0.20. These are *the PR tasks the study runs*. Trap tasks are scheduled among them in random order.

Both counts take the pessimistic end of a variance range. Let `p` and `q` be a task's per-session pass probabilities in the intact and stripped arms. Under no effect, `Var(d_task)` = `E[(p-q)^2]` + (`psi` - `E[(p-q)^2]`)/`k`.

- It is `psi/k` at the *favorable end*, when each task's two arms share one pass probability.
- It is `psi` at the *pessimistic end*, when each session's result is fixed by its task and arm.
- Under a true effect `e`, `psi - e^2` replaces `psi` at both ends.

The cap of 75 is a cost choice that leaves `psi` = 0.30 underpowered. There, the Harm outcome's power at 75 tasks is 0.694 at the pessimistic end if stripping costs 15 points.

**Feasibility pass.** The pass runs before any authoring session and reads no authoring outcome. The study goes ahead only when its confinement verification passes and at least 10 trap tasks passed all their checks. Its stages are:

- The eligibility conditions, the filters and the raters' judgments.
- The snapshots and environments.
- Test classification.
- The stripping and trap checks.
- The loaded-configuration check.
- The *confinement verification*, for sessions and for test runs, that the confinement holds and that the CLI completes a turn inside it.

Before either passes, the loaded-configuration check is shown to reject a deliberately planted violation. The confinement verification is shown to reject one planted violation in each category of the confinement list. Reading the configuration does not count as showing it. Every planted item, in either check, is a decoy with no authority. A planted credential or environment variable is a canary value that no service accepts. How the harness verifies is otherwise the harness's to decide.

**Lock.** The lock records:

- The PR task count, the number of trap tasks that passed all their checks, and the exclusions per filter and check.
- The raters' agreement.
- The confinement verification's result, the confinement categories its planted violations exercised, the loaded-configuration check's rejection of its planted violation, and the harness commit.
- The share of the PR tasks the study runs merged after the model's stated training-data cutoff.
- The preserve list and the manifest commit.

The lock also records two probabilities at `n`, the smaller of the PR task count and 75, at each `psi` on the grid and at both ends of the variance range. `Phi` is the standard normal distribution function, and `V` is the variance of `d_task` at that end under that effect:

- The probability of the Harm outcome if stripping costs 15 points: `Phi(d*sqrt(n/V) - z_a)`.
- The *Harm excluded bound*, the chance of `L` <= 0 <= `U` < 0.15 if stripping costs nothing: `Phi(min(z_a, d*sqrt(n/V) - z_a)) - Phi(-z_a)`. Being a normal approximation, it only approximately bounds the Harm excluded outcome from above, since that outcome also needs the worst-case and trap conditions.

In the lock, the experimenter decides whether the study goes ahead at that `n`. The go decision also records the simulated false-exclusion rate of the verdict procedure itself at `n`, meaning how often it returns Harm excluded if stripping costs 15 points. The verdict reads a percentile bootstrap, which is anti-conservative toward Harm excluded at small counts, roughly under 20 tasks. This record can only count against going ahead and relaxes no condition of the decision rule. No authoring outcome exists yet, so the choice cannot bias the result.

## Analysis

The unit is the task. Per task and arm, the pass rate is passing valid replicates over valid replicates. `d_task` is the intact rate minus the stripped rate, and `Delta` is the mean `d_task` over the PR tasks the study runs that are not dropped. A positive `Delta` means stripping hurt.

The interval is a two-sided 95% percentile bootstrap over tasks, with 10,000 draws and a recorded seed. `L` and `U` are its lower and upper bounds. Intervals alone decide the verdict, and no separate significance test runs. A PR task the study runs that is left with no valid replicate in an arm drops from the primary interval, and the page reports drops per arm. A task outside the PR tasks the study runs, an excluded task included, is neither dropped nor missing.

The *worst-case recomputation* is the interval over every one of the PR tasks the study runs, trap tasks excluded. It counts each missing stripped-arm replicate as a fail and each missing intact-arm replicate as a pass. A task with no valid replicate in either arm therefore counts as `d_task` = 1. An invalidated session's rerun takes its place. A replicate whose session was invalidated or stopped with a harness error counts as missing until a rerun of it is a valid replicate.

The *trap interval* is the same bootstrap over trap tasks. The decision rule reads it and the worst-case recomputation for the Harm excluded outcome only. A trap left with no valid replicate in an arm counts as `d_task` = 0, that is as showing no effect, so the trap count stays the lock's count.

Secondary results are descriptive and never change the verdict: trap pass rates per arm; `Delta` over those of the PR tasks the study runs that were merged after the model's training-data cutoff; the share of the PR tasks the study runs where no replicate passed in either arm; each task's diffs compared with comment lines removed from both; and the comments each arm added.

## Decision rule

The rule applies to `Delta` and its two-sided 95% interval from `L` to `U`. The 15-point margin enters twice: as the effect the sizing targets, and as the harm that the Harm excluded outcome must exclude. The four outcomes are exclusive and cover every result:

1. *Harm*: `L` > 0, with no minimum gap. No narrowing of the comment rules may cite this study.
2. *Harm excluded*, when all five of these hold:
   - `L` <= 0 <= `U`.
   - `U` < 0.15.
   - The worst-case recomputation's upper bound is below 0.15.
   - The worst-case recomputation's lower bound is not above 0.
   - The trap interval's lower bound is above 0.

   There is no evidence of harm, and this task distribution excludes a 15-point harm. That does not by itself justify narrowing, since human readers and review-round cost go unmeasured.
3. *Inconclusive*: `L` <= 0 <= `U`, and a condition of Harm excluded fails. It supports no narrowing whatever `U` is. These land here, among others:
   - A PR task count too short for `U` to fall below 0.15.
   - A result that holds only while missing replicates are ignored, including one whose worst-case recomputation lies wholly above 0.
   - A harness the traps did not show to be sensitive.
4. *Stripped better*: `U` < 0. Comments misled agents on this distribution. That does not justify narrowing either, because the kept accuracy rule already targets misleading comments. The discordant tasks get inspected for stale comments.

Under every outcome, the `Results` section reports `Delta`, `L` and `U`, the worst-case recomputation's interval and the trap interval, reads `U` alone as the largest harm the primary interval leaves open (none when `U` <= 0), and states that the result excludes a harm only under Harm excluded, at the larger of `U` and the worst-case recomputation's upper bound. Only Harm excluded can support a narrowing argument, and never alone. That is the deliberate asymmetry of the rule. `docs/case-studies/opus-frontload-review-rounds.md` is this repository's earlier pre-registered study with an asymmetric rule.

### Margin and pre-count disclosure

A short PR task count is expected. The page states the following beside the rule.

**The margin.** The repository owner set the 15-point margin on 2026-11-06, replacing the 10-point gap in GH-1408, after the pre-count below and before any authoring session. The owner's decision: "Let's go with the 15-point margin, the two-sided interval. That sounds good." The reason given with the proposal the owner accepted is that Harm excluded becomes reachable at the pre-count's scale. It is not that a harm under 15 points is acceptable.

**Reachability.** These figures are at `psi` = 0.20 and the favorable and pessimistic ends of the variance range, for 40 and 75 PR tasks, where 75 is the cap. They cover only the interval condition of Harm excluded. The table gives the Harm excluded bound if stripping costs nothing.

| Margin | Variance end | 40 tasks | 75 tasks |
|---|---|---|---|
| 10 points | pessimistic | 0.268 | 0.466 |
| 10 points | favorable | 0.663 | 0.893 |
| 15 points | pessimistic | 0.539 | 0.803 |
| 15 points | favorable | 0.932 | 0.950 |

The chance that the 15-point interval condition holds if stripping truly costs 10 points is 0.108 at 40 tasks and 0.167 at 75 at the pessimistic end. At the favorable end it is 0.241 and 0.069. There it falls at 75 tasks, because the interval's lower bound then usually exceeds 0, which is a Harm result.

At a zero estimate and the pessimistic end, `U` is 13.9 points at 40 tasks and 10.1 at 75. Its gap below 15 points is all the headroom the worst-case recomputation has. A zero estimate gives `U` < 0.15 at the pessimistic end only from `n` >= `z_a^2*psi/0.15^2`, which is 34.1 at `psi` = 0.20. Below that, Harm excluded needs the stripped arm to score above the intact arm.

**The pre-count.** It was run on 2026-11-06 over first-parent commits on `main` up to commit 0761371, and re-run that day with identical counts. The matching rule is the one that names an issue, applied to commit text. The counts are:

- 1037 first-parent commits.
- 195 of them match the rule. That is a floor on issue-linked commits, since the rule reads no GitHub closing-reference data.
- 130 of those change a non-test `.py` or `.sh` file.
- 129 of those also change a test file.
- 125 of those also change none of `requirements-dev.txt`, `pyproject.toml` and `.github/workflows/tests.yml`.
- 471 commits pass those three file conditions whether or not they match, which includes the 125.

Over the 1037 commits counted, the 125 bounds the count of eligible, filter-passing PRs from above only up to the undercount in the 195, since it is counted before the why-comment and specification filters and the fail-to-pass condition. The 471 is a ceiling on that count over the same 1037 commits.

The `Revisions` section binds the margin from the moment this page ships.

## Limits

- The study measures one authoring session's correctness, not the review pipeline, human readers, or the cost of review rounds.
- The held-out tests are a proxy for quality. A change can pass them and still ignore a recorded rationale, read worse to the next maintainer, or break a convention no test asserts. Harm excluded therefore rules out only a drop in test-measured correctness of 15 points or more on this task distribution. It is not a finding that stripping leaves code quality unharmed.
- Rationale in markdown that both arms keep can mask a comment effect. That includes the files under `.claude/plans/` and the project instructions and skills that load in both arms.
- A result may not transfer to the codebases of people who install this repository's configuration, which the same rules govern.
- Only why-comment tasks run, so the result says nothing about code without one.
- The code already passed comment review, and held-out tests cover only what the PR's author tested.
- Issue bodies vary in how much of the answer they state beyond what the specification filter requires. An issue created after the work began can state more, which pulls `Delta` toward 0 and favors Harm excluded.
- Memorized answers from this public history, like tasks no session can pass, pull `Delta` toward 0 and favor Harm excluded. The post-cutoff subgroup and the lock's post-cutoff share are the only checks on memorization. The share of tasks where no replicate passed in either arm is an upper bound on the tasks no session can pass.
- The confinement is verified only as far as the feasibility pass's checks reach. A gap they miss leaves no trace, and in the stripped arm it raises the pass rate, which favors Harm excluded.
- A test that needs a host tool or network access the confinement withholds fails at the merge commit, so it is not a fail-to-pass test. A PR left with none is ineligible.
- Sessions run without the user-level configuration this repository installs.
- Only shell and Python comments are stripped, and blank lines and bodies emptied to `pass` show where comments were.

## Results

Not yet run. Results computed over this repository's tasks on one account publish under `docs/private-project-redaction.md` § "This repository, one account".

## Revisions

Dated entries, each with its reason. The *lock* is the revision that records the feasibility pass before the first authoring session. An entry that relies on an owner decision quotes it, since one person may hold both roles.

Before the lock, a revision may change the design, within these rules:

- No revision may remove the asymmetry of the decision rule.
- No revision may move the 15-point margin, make Harm excluded easier to reach, or weaken the leak invalidation, unless the repository owner decides it.
- No revision may replace enforced confinement with detection after the fact, or narrow what the confinement covers, unless the confinement cannot be built and the repository owner decides it.

After the lock, no revision changes the primary design, the manifest or the decision rule.

No revisions yet.
