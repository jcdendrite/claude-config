# Comments-stripped A/B experiment design (GH-1408)

## Context

Goal: commit a pre-registered design document for an A/B experiment that tests whether code comments help agents write correct code, so the open question in GH-1408 (should the §Durable text comment rules be narrowed) has a measurement path.

Ask: GH-1408, "Evaluate narrowing comment/docstring rules; design a comments-stripped A/B experiment", narrowed to the design document by the engineer's selected label "Experiment design doc only (Recommended)". The engineer also selected the label "Asymmetric, 10-point gap (Recommended)" for the pre-registered decision rule and the label "This repo's merged PRs (Recommended)" for the task source.

Problem and why now: GH-1408's decisions say narrowing is "not decided, and not supported by this evidence", and its sample of agent Read-then-edit sequences cannot rule out a substantial dependence on comments. The issue sketches an experiment (base-commit clones with comments stripped, held-out merged-PR tests as the outcome, McNemar sizing of about 150 pairs) but no document fixes its mechanics, tasks, or decision rule before a run. The intended outcome is a case-study page under `docs/case-studies/`, indexed in `docs/case-studies.md`, that a later session can execute without re-deciding the design. The plan changes no rule and builds no harness. The instrumentation follow-ups the issue lists are a separate plan. They are round start and end times in `review-round-cost`, and a reviewer or angle field on ledger rows.

## Approach

Write one pre-registered design page, `docs/case-studies/comments-stripped-ab.md`, and add it to the index in `docs/case-studies.md`. In the design, an authoring agent implements this repository's merged PRs from a single-commit snapshot of each PR's base. It does so once with comments and docstrings intact and once with them stripped. Every run is confined to its own inputs. The merged PR's held-out tests score both runs under an asymmetric decision rule with a 15-point margin, fixed before any session runs. A feasibility pass that reads no authoring outcome measures what only a built harness can, and a dated lock records it before the first authoring session. The page states only what a pre-registration must fix. How a harness builds and checks the confinement goes to a harness issue (rows 38-39).

**What the page must state.** D1-D10 are rules for the page's content, not drafts of its wording. The labels exist only in this plan, for the ledger. The page defines every term it uses in its own text.

- **D1. Lead, roles, revisions and motivation.**
  - The lead says the page is a pre-registration: tasks, arms, outcome, analysis and decision rule are fixed before the first session, and no result exists yet.
  - The page defines the *harness*, and two roles one person may hold. The *experimenter* builds and runs the harness and decides at the lock whether the run goes ahead (D7). The *repository owner* decides each case the page routes to them (D5, D6).
  - A closing `Revisions` section holds dated entries, each with its reason. The *lock* is the revision that records the feasibility pass (D7). The section states four rules:
    - before the lock, a revision may change the design, but none may remove the asymmetry (rows 3-4);
    - no revision moves the 15-point margin, makes outcome 2 easier to reach, or weakens D6's leak invalidation, unless the repository owner decides it (row 43);
    - no revision replaces enforced confinement with detection after the fact or narrows what the confinement covers, unless the confinement cannot be built, as D5 defines it, and the repository owner decides it (rows 36, 45, 47);
    - after the lock, no revision changes the primary design, the manifest or the decision rule.

    An entry that relies on an owner decision quotes it, since one person may hold both roles.
  - "Why this measurement is needed" cites GH-1408 for the evidence and for three decisions: the accuracy rule is kept, the "don't restate the code" rule is kept, and narrowing to "comment only for a non-obvious why" is not decided. It cites `claude/.claude/CLAUDE.md` § "Durable text" as the rules under question and restates no figure from the issue's Evidence section (rows 7, 10). It may state that the issue's transcript sample could not rule out a substantial dependence on comments, and that it leans toward "no" because it sees only reads followed by an edit.
- **D2. Question and arms.**
  - The question: does stripping comments and docstrings from the code an authoring agent reads lower how often its change passes the merged PR's held-out tests?
  - The *intact arm* gets the snapshot as committed, and the *stripped arm* gets it after D4. Both arms share the model, prompt, tools, config template, confinement, environment and per-session budget cap. The model is the one this repository routes code writing to, and the manifest records its exact ID.
  - A run is one authoring session that edits the working tree, may run tests, and stops without committing.
  - No review pipeline runs in either arm. The page names both comment checks this keeps off: `comment-discipline-reviewer`, and the inline comment check `/code-review` runs even without that reviewer (row 21).
- **D3. Tasks.**
  - A merged PR is eligible when all of these hold:
    - it merged to `main` as one commit;
    - it has a prompt issue (below);
    - its diff changes at least one non-test shell or Python file;
    - it changes none of `requirements-dev.txt`, `pyproject.toml` and `.github/workflows/tests.yml`, so one environment serves base and merge (rows 26, 32);
    - it has at least one fail-to-pass test.
  - The page defines these terms:
    - a *test file* sits under a directory named `tests` or `fixtures`, or is named `conftest.py` or `test_*.py`, so support modules in a test directory count (row 32), and a PR's test files are those its diff adds, changes or deletes;
    - a *fail-to-pass test* fails at the base, the merge commit's first parent, with the PR's test files overlaid, and passes at the merge commit;
    - *pass-to-pass tests* are the other tests in those files that pass at both.
  - Classification runs in snapshots (D5), never in a checkout with history. It runs each test ten times per commit, a cost choice, and drops a test whose result varies.
  - The *prompt issue* is the issue the PR title names, or, when the title names none, the single issue the PR closes. A PR whose title names more than one issue, or that names none and closes other than exactly one, is excluded (row 29).
  - The prompt is a fixed preamble, identical in both arms and silent about comments, followed by the prompt issue's body verbatim. It never includes the PR body or a plan file. A task is excluded when either holds (row 29):
    - the harness cannot show the body is the issue's first revision, since a later edit can state the merged answer;
    - the prompt names the PR, its branch or a commit.

    The page names no GitHub mechanism for showing a first revision.
  - The *specification filter* excludes a task whose prompt omits a name, flag or path that its fail-to-pass tests need and the base does not hold.
  - The *why-comment filter* is judged from the base snapshot alone, before any session. The *touched region* is each function, or contiguous top-level shell block, that the merged diff changes, as it stands at the base. A task is kept only when its touched region holds a comment or docstring stating a reason, constraint or contract the code does not show. A task whose code changes are all new files is excluded.
  - A *PR task* is an eligible PR that passes both filters, the stripping checks (D4) and the loaded-configuration check (D5). The page reports exclusions per filter and check.
  - Raters: one rater makes every filter judgment. A second rater independently judges a random 30 tasks, or every task when fewer.
  - *Trap tasks* number at least 10, a floor chosen for cost (row 33). They are hand-built at one fixed base commit, not merged PRs (row 6). Each starts from a comment stating a rule the code does not show. Its task statement invites breaking the rule without mentioning it. Its held-out test fails exactly when the rule is broken. Before any session, each trap passes three checks, the last two at both the intact and the stripped base:
    - the rule's key terms, a non-empty list fixed when the trap is built, all occur in the intact comment and none appears in the stripped-arm snapshot;
    - a reference implementation that breaks the rule passes every test at the base and fails the held-out test;
    - a reference implementation that keeps the rule passes the held-out test.

    Traps also pass the stripping checks and the loaded-configuration check (D5). They never enter `Delta` (D8), and outcome 2 reads them (D9).
  - The page names GH-1408's trap example as one that fails the first check (row 17). The bash 3.2 rule in a `claude/.claude/hooks/_config.sh` comment also sits in `.claude/rules/shell-floor.md` and `docs/hooks.md`, which both arms keep.
  - The lock commits the *task manifest* (rows 5-6):
    - the PR tasks in a seeded random order, with the seed;
    - per task, its filter labels, test IDs, base and merge commits, merge date, issue creation time and PR first-commit time;
    - each prompt with its hash;
    - the model's stated training-data cutoff;
    - per task, the installed package list and host tool versions (row 31);
    - the preserve list (D4).
- **D4. Stripping.**
  - The stripped arm's whole snapshot is stripped, in shell and Python files only, tests included. Every other file is identical in both arms.
  - Stripping preserves line numbers: a comment-only or docstring line becomes empty, a trailing comment goes with the whitespace before it, and a body left empty gets `pass`.
  - A `#` is a comment only where the language's parser reads one. In shell, `$#`, `${#...}`, a parameter-expansion `#` and a `#` inside a quoted string or heredoc body are not comments.
  - The *preserve list* names comments both arms keep. It starts with every category that a tool, test or program at the base reads (row 12): shebangs; tool directives such as `# shellcheck`, `# noqa`, `# fmt:` and `# pragma`; the hook headers and heading citations hook tests read; docstrings code reads through `__doc__`.
  - *Stripping checks* run per task before any session:
    - every stripped shell file passes `bash -n`;
    - every stripped Python file's syntax tree equals the intact file's after the same docstring removal;
    - the whole test suite fails the same tests at the stripped base as at the intact base;
    - at the stripped base with the held-out test files overlaid, pass-to-pass tests pass and fail-to-pass tests still fail;
    - at the merge commit with its non-test code stripped, fail-to-pass and pass-to-pass tests pass.

    A failure caused by stripping a comment that something reads adds that comment to the preserve list. A task that still fails is excluded and counted. The lock freezes the list.
  - The page sets read-time stripping aside for GH-1408's reason: it leaks through `grep`, `cat` and `git show`, and it breaks `Edit` in the stripped arm only (row 7).
- **D5. Snapshot, environment and confinement.**
  - *Snapshot*: a fresh repository holding one commit, the base tree stripped or not, with a constant author, date and message and no remote. It is built from `git archive` the way `evals/review_bench/fixture_repo.py` builds review-bench fixtures (row 16). Every session and test run uses one.
  - *Environment*: one per task, built from the base's own pinned Python version and dependency files, identical across arms and test runs (row 26). Both arms of a task show the same installed package list. The page names no build recipe and no host tool list.
  - *Config*: each run uses its own copy of one frozen config template that holds nothing from the experimenter's config (G3). A session holds only a credential issued for the experiment. No session transcript is committed, posted or published.
  - *Loaded configuration*: the page states the outcome the harness must reach and names no CLI mechanism (row 23):
    - no plugin loads, though the snapshot's `.claude/settings.json` enables plugins in both arms (row 13);
    - no hook, no web tool, and no skill beyond the snapshot's own loads;
    - the snapshot's own project instructions and skills load in both arms (row 27).

    The *loaded-configuration check* starts the CLI once per task and arm, traps included, before any session and under the session conditions. A mismatch with the outcome excludes the task. A session whose loaded lists still differ counts as a frozen-condition change, not a harness error, and invalidates its run block.
  - *Confinement*: a requirement the page states in about this form (row 36):
    - every session and every test run executes in an environment that holds nothing beyond its own inputs, with no copy of, and no handle to, any of these (G3):
      - a live checkout of this repository;
      - the experimenter's home or config directory;
      - the task's other arm or any other run;
      - any commit but its snapshot's own;
      - the PR's held-out test files and merge-commit code, until scoring;
      - the trap reference implementations;
      - any other process on the host;
      - any network host but the model API;
    - the harness enforces this before a run starts, and detection after a run supplements the enforcement and never replaces it;
    - the harness treats a finished session's tree as data: it reads file contents, runs any tool on the tree only inside the confinement, and never runs git on it from the host;
    - the experimenter picks the mechanism when building the harness, and the lock records the harness commit (D7);
    - the confinement *cannot be built* when no environment meeting it can be made or the CLI cannot complete a turn inside one, and the repository owner then decides what follows (rows 35, 37, 45, 47).
  - *Frozen conditions*: the page cites `evals/README.md` § "Frozen conditions and invalidation" as the pattern. A *run block* is a span of runs between two records of the CLI version, the config template, and the conditions the manifest and the lock fix. A change between the records, or a difference from the manifest, invalidates the block.
- **D6. Outcome and run validity.**
  - Scoring starts, in both arms, from a clean intact snapshot of the base, so the scored trees differ only in what the session wrote. It applies each file the session added, changed or deleted as a whole file, since a stripped-arm edit can fail to apply as a line diff to intact text. It skips test files and every file that configures test collection or execution, including `pyproject.toml` and any such file the base lacks, so scoring runs the base's configuration only (row 32). It then overlays the PR's test files as they stand at the merge commit, intact in both arms. The tests run in the snapshot's environment.
  - A run *passes* when every fail-to-pass and pass-to-pass test passes, and a required test absent from the results fails. A failing test reruns once and fails only when both runs fail.
  - A *valid replicate* is a session that ended by itself or at the budget cap with no harness error and was not invalidated. A harness error is a failure outside the session's control, such as an API or CLI error. A capped session is scored like any other. A harness error reruns the session once, and a second one leaves the replicate missing. The page reports per arm the counts of capped sessions, harness errors and missing replicates.
  - A session is *invalidated* by a leak or by a frozen-condition change (D5). Its outcome never enters `d_task`, the worst-case recomputation or a revision. It reruns under the corrected condition: the frozen conditions after a D5 change, and a new harness commit that passes D7's confinement verification after a leak. The rerun counts as an ordinary replicate.
  - A *leak* is evidence that a run reached something outside its own inputs. When one is found:
    - it invalidates every run under that run's harness commit, since the gap was open for all of them;
    - the dated revision that records it names the kind of route reached, never its path, host or command;
    - the repository owner decides anything else that follows.
  - Diff similarity to the merged diff is not measured, because many correct diffs exist.
- **D7. Sample size, feasibility pass and lock.**
  - Sizing: every figure uses the grid `psi` = 0.10, 0.20 and 0.30, because `psi`, the share of discordant pairs, is an assumption, centered on GH-1408's 0.20 (rows 7, 19). The other inputs are `d` = 0.15, the margin (D9), `z_a` = 1.95996 from two-sided alpha 0.05, and `z_b` = 0.84162 from power 0.80. Two counts follow (rows 18, 42):
    - outcome 1's count, from McNemar's normal-approximation formula `n = (z_a*sqrt(psi) + z_b*sqrt(psi - d^2))^2 / d^2`, is 33, 68 and 103, a proxy, because the verdict reads an interval, not McNemar's test;
    - outcome 2's count, where the outcome-2 bound (below) reaches 0.80, is `n = psi*((z_a + z_c)/d)^2`, which gives 38, 75 and 112. `z_c` is the standard normal 0.825 quantile, because the bound subtracts the 0.025 that falls in outcome 4.
  - Each arm runs `k` = 3 independent replicate sessions per task. The run takes PR tasks in manifest order, up to 75, the larger count at `psi` = 0.20.
  - Both counts take the pessimistic end of a variance range (rows 20, 28). With `p` and `q` a task's per-session pass probabilities in the intact and stripped arms, `Var(d_task)` = `E[(p-q)^2]` + (`psi` - `E[(p-q)^2]`)/`k` under no effect. It runs from `psi/k`, the *favorable end*, when each task's two arms share one pass probability, to `psi`, the *pessimistic end*, when each session's result is fixed by its task and arm. Under an effect `d`, `psi - d^2` replaces `psi` at both ends. The cap of 75 is a cost choice that leaves `psi` = 0.30 underpowered: there, outcome 1's power at 75 tasks is 0.694 at the pessimistic end.
  - The *feasibility pass* runs before any authoring session and reads no authoring outcome. The run goes ahead only when its confinement verification passes. Its stages:
    - the eligibility filters and the raters' judgments (D3);
    - the snapshots and environments (D5);
    - test classification (D3);
    - the stripping and trap checks (D3-D4);
    - the loaded-configuration check (D5);
    - the confinement verification, for sessions and for test runs, that the confinement holds and that the CLI completes a turn inside it.

    Before either passes, the loaded-configuration check is shown to reject a deliberately planted violation, and the confinement verification one in each category D5 lists. Reading the configuration does not count as showing it. How the harness verifies is otherwise the harness's to decide.
  - The lock records:
    - the PR task count, and the exclusions per filter and check;
    - the raters' agreement;
    - the confinement verification's result, the D5 categories its planted violations exercised, the loaded-configuration check's rejection of its planted violation, and the harness commit;
    - the share of PR tasks merged after the model's stated training-data cutoff;
    - the preserve list and the manifest commit.
  - The lock also records two probabilities at `n`, the smaller of the PR task count and 75, at each `psi` on the grid and at both ends of the variance range (rows 28, 42):
    - outcome 1 if stripping costs 15 points, `Phi(d*sqrt(n/V) - z_a)`, with `Phi` the standard normal distribution function and `V` the variance of `d_task` at that end under that effect;
    - the *outcome-2 bound*, the chance of `L` <= 0 <= `U` < 0.15 if stripping costs nothing, `Phi(min(z_a, d*sqrt(n/V) - z_a)) - Phi(-z_a)`. It bounds outcome 2 from above, since outcome 2 also needs the worst-case and trap conditions.
  - In the lock, the experimenter decides whether the run goes ahead at that `n`. No authoring outcome exists yet, so the choice cannot bias the result.
- **D8. Analysis.**
  - The unit is the task. Per task and arm, the pass rate is passing valid replicates over valid replicates. `d_task` = intact rate minus stripped rate, and `Delta` = mean `d_task` over PR tasks, positive when stripping hurt.
  - Interval: a two-sided 95% percentile bootstrap over tasks, with 10,000 draws and a recorded seed, from `L` to `U`. Intervals alone decide the verdict, and no separate significance test runs. A task left with no valid replicate in an arm drops from the primary interval, and the page reports drops per arm.
  - *Worst-case recomputation*: the interval over every PR task, trap tasks excluded, with each missing stripped-arm replicate counted as a fail and each missing intact-arm replicate as a pass. A task with no valid replicate in either arm therefore counts as `d_task` = 1. An invalidated session is never a missing replicate, since its rerun takes its place (D6).
  - *Trap interval*: the same bootstrap over trap tasks. D9 reads it and the worst-case recomputation for outcome 2 only.
  - Secondary results are descriptive and never change the verdict (row 7): trap pass rates per arm; `Delta` over tasks merged after the model's training-data cutoff; the share of tasks where no replicate passed in either arm; each task's diffs compared with comment lines removed from both; the comments each arm added.
- **D9. Decision rule**, applied to `Delta` and its two-sided 95% interval. The 15-point margin enters twice: as the effect D7 sizes for, and as the harm outcome 2 must exclude. The four outcomes are exclusive and cover every result:
  1. *Harm*: `L` > 0, with no minimum gap. No narrowing of the comment rules may cite this study.
  2. *15-point harm excluded*, when all four hold:
     - `L` <= 0 <= `U`;
     - `U` < 0.15;
     - the worst-case recomputation's upper bound is below 0.15;
     - the trap interval's lower bound is above 0.

     There is no evidence of harm, and this task distribution excludes a 15-point harm. That does not by itself justify narrowing, since human readers and review-round cost go unmeasured.
  3. *Inconclusive*, which supports no narrowing whatever `U` is: `L` <= 0 <= `U`, and a condition of outcome 2 fails, as with:
     - a short pool;
     - a result that holds only while missing replicates are ignored;
     - a harness the traps did not show to be sensitive.
  4. *Stripped better*: `U` < 0. Comments misled agents on this distribution. That does not justify narrowing either, because the kept accuracy rule already targets misleading comments. The discordant tasks get inspected for stale comments.

  Under every outcome, the `Results` section reports `Delta`, `L` and `U`, and reads `U` as the smallest harm the result excludes. Only outcome 2 can support a narrowing argument, and never alone. That is the asymmetry the selected rule asks for (rows 3-4, 43). The page cites `docs/case-studies/opus-frontload-review-rounds.md` as this repository's earlier pre-registered study with an asymmetric rule (row 8).

  *Margin and pool disclosure.* Beside the rule, the page states that a short pool is expected. It also states these (rows 22, 42, 44):
  - the repository owner set the 15-point margin, replacing the issue's 10-point gap, on the date the page states, after the pool pre-count and before any authoring session;
  - the owner's decision, quoted as D1 requires: "Let's go with the 15-point margin, the two-sided interval. That sounds good." (row 43);
  - the reason given with the proposal the owner accepted: outcome 2 becomes reachable at the pre-count's scale, not that a harm under 15 points is acceptable;
  - the outcome-2 bound at `psi` = 0.20 for 40 and 75 PR tasks, 75 being the cap, which covers outcome 2's interval condition only;
  - that bound at a 10-point and at the 15-point margin, each at the pessimistic and the favorable end (D7);
  - the chance that the same interval condition holds if stripping costs 10 points, at those task counts and ends;
  - `U` at a zero estimate, at the pessimistic end and those task counts, whose gap below 0.15 is all the headroom the worst-case recomputation has;
  - at the pessimistic end, a zero estimate gives `U` < 0.15 only from `n` >= `z_a^2*psi/0.15^2`, which is 34.1 at `psi` = 0.20, and below that outcome 2 needs the stripped arm to score above the intact arm;
  - the pre-count's run date and its `main` commit 0761371;
  - the 1037 first-parent commits up to that commit;
  - the matching rule, `GH-<n>` or a closing keyword followed by `#<n>`, case-insensitive, in commit text;
  - 195 matching commits, a floor on issue-linked commits, since the rule reads no GitHub closing-reference data;
  - 125 of those changing a non-test shell or Python file and a test file and none of D3's environment files. This bounds the pool from above only up to that undercount, and before the why-comment, specification, fail-to-pass and recoverability filters;
  - 471 commits passing those file conditions whether or not they match, which includes the 125, a ceiling on the pool.

  D1 binds the margin from the moment the page ships.
- **D10. Limits and results.** The page's limits:
  - It measures one authoring session's correctness, not the review pipeline, human readers, or the cost of review rounds.
  - Rationale in markdown both arms keep, including the plan files under `.claude/plans/` and the project instructions and skills that load in both arms (row 27), can mask a comment effect.
  - A result may not transfer to the codebases of people who install this repository's configuration, which the same rules govern.
  - Only why-comment tasks run, so the result says nothing about code without one.
  - The code already passed comment review, and held-out tests cover only what the PR's author tested.
  - Issue bodies vary in how much of the answer they state beyond what the specification filter requires. This line names the filter and does not restate its rule. An issue created after the work began can state more, which pulls `Delta` toward 0 and favors outcome 2.
  - Memorized answers from this public history, like tasks no run can pass, pull `Delta` toward 0 and favor outcome 2 (row 30). The post-cutoff subgroup and the lock's post-cutoff share are the only checks.
  - The confinement is verified only as far as the feasibility pass's checks reach. A gap they miss leaves no trace, and in the stripped arm it raises the pass rate, which favors outcome 2.
  - Tasks whose tests need a host tool or network access the confinement withholds drop as fail-to-pass exclusions (row 31).
  - Sessions run without the user-level configuration this repository installs.
  - Only shell and Python comments are stripped, and blank lines and bodies emptied to `pass` show where comments were.

  A `Results` section reads "Not yet run." Results from this repository's tasks on one account publish under `docs/private-project-redaction.md` § "This repository, one account" (row 24).

**Writing constraints for the page.**
- None of "pooled across", "machine-wide", "cross-machine" or "multi-account" appears in it (row 9).
- No figure from GH-1408's Evidence or round-level reading appears. The only numbers are the design parameters and derived figures of D3-D9 and the pre-count figures D9 discloses (rows 10, 22).
- No plan-local label, ledger row number, "this plan" or session narration appears.
- It cites files by path and doc sections by `§` heading, never by line number. Per-account paths are written `<config-dir>/...`.
- A statement resting on an `[unverified]` row (rows 19, 20, 23, 27, 28, 29, 30, 33, 35, 42) reads as an assumption or as a requirement on the harness, never as a fact.
- The index bullet carries no `§` citation and no per-account path, because `docs/case-studies.md` is inside the citation and state-path test corpora (row 11).

**Alternatives set aside.**
- *Page location.* A plan file is provenance, not a deliverable (`/plan-it` Step 1). An `evals/README.md` section documents a harness that does not exist yet. A `docs/design-decisions/` entry records a decision, and none is made yet. A case-study page holds the design now and the results later, in one home.
- *Apparatus specified in the page.* Before the lock, D1 lets a revision change everything its bars do not protect. Harness apparatus on the page therefore commits to nothing, yet reviewers read it as binding prose. The page keeps the pre-registration and the confinement requirement, and the apparatus goes to a harness issue (rows 38-39).
- *Isolation by named exclusion, by detection alone, or by the CLI's built-in sandbox.* A check or deny rule that names what to block leaves open what it does not name (rows 14-15). Detection prevents nothing (row 14). The sandbox confines Bash but not Read, Grep and Glob (row 34).
- *Running `/code-review` in both arms with `comment-discipline-reviewer` skipped.* The inline comment check still runs (row 21), and a review loop multiplies session cost and variance.
- *Stripping only the touched files.* Agents read callers and sibling files, so the arms would differ in only part of what the agent reads.
- *Fixing the pool size, flake rates and test environment as design values now.* Each is knowable only once the harness exists, so a fixed value would be a guess a later revision replaces. The page discloses the pre-count with its floor and ceiling (D9), and the lock records the measured values.
- *A pilot of authoring sessions that estimates replicate correlation.* It reads outcomes, and it spends tasks from a short pool (row 22). The lock reports its probabilities at both ends of the variance range instead (row 28).
- *Limiting tasks to those merged after the model's training-data cutoff.* It removes memorization but shrinks a short pool. The lock records the post-cutoff share, and the subgroup is reported.
- *McNemar over replicate pairs as the primary test.* Within-task correlation makes it overstate significance, which under an asymmetric rule turns noise into a blocking "harm" verdict (row 20). A majority vote per task followed by McNemar discards replicates. A mixed-effects logistic model adds a third-party statistics dependency.
- *A sign-flip permutation test beside the interval.* The two can disagree near the boundary, so the verdict would depend on which one D9 reads first. Inverting the test into its own interval needs a search over shifted nulls and a symmetry assumption.
- *Reading outcome 2 from complete cases only.* Harness errors can strike one arm more often, so ignoring missing replicates can hide harm. Only the outcome that can support narrowing has to survive the worst case.
- *Dropping the trap tasks.* Nothing would then show that the harness can detect a comment effect, so a null result from an insensitive harness would read as "harm excluded".
- *A one-sided bound for outcome 2.* It loosens the only outcome that can support a narrowing argument, against the asymmetry (row 44).
- *A plan file's Context as the prompt.* It would widen the pool, but about half of its 314 candidates state the solution (row 46).

### Assumption ledger

**Root:** GH-1408 leaves open whether the comment rules should narrow, and nothing fixes how to measure whether comments help authoring agents. This change commits a pre-registered design page, so a later run cannot re-decide its tasks, outcome or decision rule after seeing data.

**Givens:**
- G1. GH-1408's decisions and its Out of scope stand: keep the accuracy rule, keep the "don't restate the code" rule, leave narrowing undecided, treat PR-defined terms as not format-only, and do not remove docstrings outright. *Reason: the issue owns those decisions, and this plan designs a measurement for what the issue leaves open.*
- G2. A figure from a wider-corpus read never enters a public artifact, except under the owner's case-by-case authorization for that artifact. *Reason: the repository owner sets this policy in `docs/private-project-redaction.md` § "A wider corpus goes to the owner, never into a public artifact".*
- G3. The experimenter's normal Claude Code config is a stow of this repository, so a session that loads it reads, runs and is gated by a live checkout. *Reason: the install architecture works this way (CLAUDE.md: `claude/` is stowed into `$HOME`; `evals/README.md` § "Out-of-session reads"), and changing it is outside this plan.*
- G4. A published case study is a preserved record. *Reason: `claude/.claude/CLAUDE.md` Axis 3 governs it, and `claude-skills/skills/tests/test_skills.py:8349` names `docs/case-studies/**` as preserved records. This is what makes the page a pre-registration.*

**Mechanisms:**
- The case-study page plus one index bullet (D1). `anchors: row8`
- Pre-registration content only on the page, with the harness apparatus in a harness issue (D5-D7). `anchors: row38`
- Repository-wide, line-preserving stripping with a preserve list that the stripping checks grow and the lock freezes (D4). `anchors: row12`
- Enforced confinement of every session and test run, stated as a requirement on the environment, with the mechanism and its verification left to the harness and pinned by the harness commit the lock records (D5, D7). This is the heaviest isolation primitive. The two lighter forms, named exclusion and detection alone, fail as Alternatives states. `anchors: row36`
- One snapshot construction and one environment per task for every session and test run (D5). `anchors: row16`
- Author-only sessions with no review pipeline (D2). `anchors: row21`
- A feasibility pass and lock ahead of the first session, and a margin and pool disclosure that ships with the page (D7, D9). Fixed guesses fail as Alternatives states. `anchors: row22`
- A task-level bootstrap interval as the only inferential frame (D8). `anchors: row20`
- The four-outcome decision rule with a 15-point margin, with outcome 2 also read under the worst-case recomputation and the trap interval (D9). `anchors: row43`
- One `code-writer` dispatch, then one read-only claim-check dispatch (Critical files). `anchors: root`

**Rows:**
1. `[engineer-verified: "Experiment design doc only (Recommended)"]` The deliverable is the design page and its index bullet. `anchors: root`
2. `[unverified]` The session wrote the description behind row 1's label: a design doc that reserves an optional third arm, measurable once ledger rows carry an angle field, with no rule change and no instrumentation. The plan departs from it in one way: it reserves no third arm. The arm cannot run until ledger rows carry that field, and it would need its own pre-registration, so reserving it now fixes nothing a later plan could not add. `anchors: row1`
3. `[engineer-verified: "Asymmetric, 10-point gap (Recommended)"]` The decision rule is asymmetric and built around a 10-point pass-rate gap. `anchors: root`
4. `[unverified]` The session wrote the description behind row 3's label: a stripped arm at least 10 points worse (McNemar, p<0.05) blocks any narrowing, while no significant gap reads as "no evidence of harm" and does not by itself justify narrowing. D9 keeps the asymmetry, with row 43's margin as the effect D7 sizes for and as the harm outcome 2 must exclude, and D1 bars any revision from removing the asymmetry. D9 departs from the description in four ways:
   - A task-level interval decides, not McNemar's test (row 20).
   - Outcome 1 fires at any `L` > 0, so a gap under the margin whose interval excludes 0 counts as harm, where the description blocked narrowing only at 10 points or more.
   - D9 defines the outcome the description leaves open where the gap favors the stripped arm.
   - "Harm excluded" must also hold under the worst-case recomputation and the trap condition.

   Every outcome except "harm excluded" leaves the rules as they are, so counting a gap under the margin as harm changes a label, not an action. The last departure only makes "harm excluded" harder to reach. `anchors: row3`
5. `[engineer-verified: "This repo's merged PRs (Recommended)"]` Tasks come from this repository's merged PRs. `anchors: root`
6. `[unverified]` The session wrote the description behind row 5's label: merged PRs with a base commit and held-out tests, stratified by whether the touched code carries a why-comment, plus hand-built trap tasks, all public and reproducible with no private-project provenance. D3 departs from it in one way: it keeps only why-comment tasks, since no outcome reads the other stratum. Trap tasks are hand-built, not merged PRs, so row 5's label does not cover them, and only this session-written description does. GH-1408 sketches a trap (row 17). D3's other filters only narrow the eligible set. `anchors: row5`
7. `[verified: gh issue view 1408, run by the session after /plan-review round 1; it matches the round-1 draft of the body apart from one trailing blank line]` The live issue's Decisions, Proposed experiment, Out of scope and Follow-ups sections carry what G1 and D1-D10 rely on. That includes the reason to set read-time stripping aside, the instruction to drop diff similarity and compare diffs ignoring comments, and "about 150 pairs to detect a 10-point pass-rate gap if 20% of pairs disagree". `anchors: root`
8. `[verified: docs/case-studies.md:3-7; docs/case-studies/opus-frontload-review-rounds.md:3-5, :146]` A case study is the empirical record behind a design decision, indexed as one bold-title bullet. The repository's earlier pre-registered study fixed its gates before any scan and used a deliberately asymmetric rule. `anchors: root`
9. `[verified: claude-skills/skills/tests/test_skills.py:4205-4248]` A `docs/case-studies/*.md` file containing "pooled across", "machine-wide", "cross-machine" or "multi-account" fails CI unless it is registered. `anchors: root`
10. `[verified: docs/private-project-redaction.md:172-179; gh issue view 1408, its figure-authorization sentence, run by the session after /plan-review round 1]` A case study may not republish a wider-corpus figure. The issue's authorization covers its figures "in this issue" only. `anchors: root`
11. `[verified: claude-skills/skills/tests/test_skills.py:8345-8358, :8372-8374]` The state-path corpus excludes `docs/case-studies/**`. It includes `docs/case-studies.md`, and so does the citation-extraction corpus. `anchors: root`
12. `[verified: claude/.claude/hooks/tests/test_hook_alignment.py:8, :1825, :2107-2125; claude/.claude/hooks/tests/test_send_back_skipped_self_review.py:769-778; claude/.claude/scripts/tests/test_plan_shape.py:1308, :1314; claude/.claude/scripts/tests/test_pr_create_preflight.py:566, :798; claude/.claude/scripts/tests/test_transcript_session_pricing.py:1753; claude/.claude/scripts/tests/_move_fidelity.py:883; claude/.claude/hooks/tests/test_split_command_words.py:118, :239, :253, :453; claude/.claude/scripts/tests/test_transcript_review_rounds.py:6004; claude/.claude/hooks/tests/test_shellcheck.py exists]` Tests read the `# hook-class:` header at line 2, the `# tier-threat-model:` header at line 3 and the hook-header heading citations. Three tests read docstrings through `__doc__`, and one script builds its argparse help from its module docstring. Test files carry `# fmt:` and `# pragma` directives. `anchors: root`
13. `[verified: .claude/worktree-required exists; .claude/settings.json:1-22; README.md:284-308; a Glob of .claude/{skills,agents,commands,hooks}/** at this plan's base]` This repository opts into worktree enforcement. Its project settings enable four plugins from this repository's own marketplace, register no hook, and keep `**/claude/.claude/CLAUDE.md` out of loaded instructions through `claudeMdExcludes`. Its `.claude/` holds three project skills and no project agent, command or hook. `anchors: root`
14. `[verified: evals/README.md:1101-1106, :1146-1151; evals/review_bench/runner.py:774-807]` The review bench fails a run only on a Read, Grep or Glob of a live checkout's copy of a changed file or of the config `projects/` root, and only after the call has run. Its sessions hold Bash, which that check does not see. `anchors: root`
15. `[verified: claude/.claude/rules/settings-json-conventions.md]` "A `permissions.deny` rule blocks only the command or tool-call shape it names." `anchors: row14`
16. `[verified: evals/review_bench/fixture_repo.py:1-5, :43-54]` The bench builds each fixture as a fresh repository from `git archive` trees. It uses a fixed author and date and a constant commit message, so `git log --all` cannot reach a later commit. `anchors: root`
17. `[verified: claude/.claude/hooks/_config.sh:26; .claude/rules/shell-floor.md:14; docs/hooks.md:424; shell-floor.md loaded into plan-architect's context on a Read of install-dev.sh; staff-sdet confirmed the first-check failure in round 2]` The rule behind the issue's trap example sits in a comment and in two markdown files both arms keep. `.claude/rules/shell-floor.md` is a path-scoped rule that loads when a session reads a shell file. `anchors: row6`
18. `[verified: hand arithmetic by plan-architect, which ran no python, z(0.975) = 1.95996, z(0.80) = 0.84162; staff-sdet reproduced 154.60 in round 2]` McNemar's normal-approximation count is 154.6 at `d` = 0.10 and `psi` = 0.20, matching the issue's "about 150". At `d` = 0.15 it rounds up to 33, 68 and 103 at `psi` = 0.10, 0.20 and 0.30. Verification step 2 recomputes every figure. `anchors: row7`
19. `[unverified]` That formula is the normal-approximation sample size for McNemar's test. plan-architect read no source for it, and its match with the issue's independent "about 150" is the only corroboration. The page states the formula and its inputs rather than citing a source. `anchors: row18`
20. `[unverified]` Replicate sessions of one task are positively correlated. So McNemar over replicate pairs overstates significance, and a figure computed as if replicates add no information is the pessimistic end of a range (row 28). This is a statistical argument, not something read from a source. `anchors: row4`
21. `[verified: claude-skills/skills/code-review/SKILL.md:78, :85]` `/code-review` runs its inline "Non-durable comment" check even in rounds that do not spawn `comment-discipline-reviewer`. `anchors: root`
22. `[verified: the pre-count the session ran over git log --first-parent on main up to 0761371; staff-sdet reproduced 1037, 195, 130, 129 and 125 and counted 471 in round 5, with the matching rule below, and the session reports that the 111 staff-sdet also listed was the post-cutoff date split, not a step of this funnel]` The matching rule is `GH-<n>`, or a closing keyword followed by `#<n>`, case-insensitive, in commit text. The counts:
   - 1037 first-parent commits;
   - 195 of them match the rule;
   - 130 of those touch a non-test `.py` or `.sh` file;
   - 129 of those also touch a test file;
   - 125 of those also touch none of `requirements-dev.txt`, `pyproject.toml` and `.github/workflows/tests.yml`;
   - 471 commits pass those three file conditions whether or not they match, which includes the 125.

   The 195 is a floor on issue-linked commits, because the rule reads no GitHub closing-reference data. The 125 therefore bounds the pool from above only up to that undercount, and before the why-comment, specification, fail-to-pass and recoverability filters. The 471 is a ceiling. `anchors: row5`
23. `[unverified]` A session under a copy of a config template with no marketplace registered loads none of the plugins that the snapshot's `.claude/settings.json` enables. D5 states the required outcome, which the harness confirms per task and arm, and leaves the mechanism to the harness. `anchors: row13`
24. `[verified: docs/private-project-redaction.md:97-103]` A measurement computed over this repository's own corpus on one account is publishable. `anchors: row10`
25. `[verified: claude/.claude/scripts/select-tests.py:358-366, :885]` A change under `docs/` selects the hooks and skills test directories. `anchors: root`
26. `[verified: install-dev.sh:3, :53, :153]` `install-dev.sh` builds `.venv` from the Python version `.github/workflows/tests.yml` pins and installs `requirements-dev.txt`. Those files and `pyproject.toml`'s pytest configuration (row 32) therefore define the test environment. `anchors: root`
27. `[unverified]` A session started in a snapshot under a copy of the config template still loads the snapshot's project instructions, meaning its root `CLAUDE.md` and `.claude/rules/`, minus what `claudeMdExcludes` names, and its project skills under `.claude/skills/`. The page states this as an expectation the harness confirms. `anchors: row13`
28. `[unverified]` D7's probabilities come from a normal approximation to the interval, with `V` the variance of `d_task`. With `p` and `q` a task's per-session pass probabilities in the two arms, `psi` = `E[p(1-q) + q(1-p)]` and a true effect `e`, `V` = `E[(p-q)^2]` - `e^2` + (`psi` - `E[(p-q)^2]`)/`k`. Since `E[(p-q)^2]` lies between `e^2` and `psi`, `V` runs from (`psi` - `e^2`)/`k` to `psi` - `e^2`, so every figure D7 and D9 state is one end of a range. Under no effect, `L` <= 0 <= `U` < `d` holds when the estimate, in standard errors, lies between `-z_a` and `min(z_a, d*sqrt(n/V) - z_a)`, with `d` the margin. Under a true effect `e`, it holds with probability `Phi(min(z_a, d*sqrt(n/V) - z_a) - e*sqrt(n/V)) - Phi(-z_a - e*sqrt(n/V))`. This is a statistical argument, not something read from a source. `anchors: row18`
29. `[unverified]` GitHub exposes which issues a PR closes and an issue body's revision history, so the harness can find the prompt issue of a PR whose title names none and show that a body is the issue's first revision. D3 excludes a task when either fails, so a gap here shortens the pool and cannot admit an edited body. `anchors: row6`
30. `[unverified]` Memorized answers and tasks no run can pass both pull `Delta` toward 0, which favors outcome 2. This is a statistical argument. `anchors: row3`
31. `[verified: a Grep for shutil.which( calls across this repository's Python files]` Tests at this plan's base look up host executables beyond common POSIX tools, among them `jq`, `gh`, `stow`, `direnv`, `strace` and `zsh`. Host tool versions therefore belong to the environment the manifest and the frozen conditions record. `anchors: root`
32. `[verified: Globs for test_*.py and conftest.py under claude/.claude, claude-skills, evals and plugins; pyproject.toml:17-42]` Test code sits under directories named `tests`, except `evals/test_*.py` and `evals/conftest.py`, and support modules such as `claude/.claude/tests/helpers.py` sit there too. The root `pyproject.toml` holds pytest's `pythonpath`, markers and collection rules. `anchors: root`
33. `[unverified]` Ten traps can meet D9's trap condition when the built-in effect is large. If each trap shows the full effect or none, a bootstrap draw holds no effect-showing trap with probability 0.6^10 = 0.006 when four of ten show it, and 0.7^10 = 0.028 when three do. Against the 0.025 tail, the condition needs about four. This is a statistical argument. `anchors: row4`
34. `[verified: docs/case-studies/claude-code-sandbox-write-boundary.md:63-64, :215-226, under the Q1 and Q4 headings found by Grep at :27, :213]` Claude Code's built-in sandbox confines Bash subprocesses only, so Read, Grep and Glob stay outside it. On Linux it denied Bash egress to a host not on its allowlist, with no direct route around its proxy. `anchors: root`
35. `[unverified]` An environment meeting D5's confinement, with network limited to the model API, can be built, and the CLI completes turns inside it. The CLI may need other hosts: `docs/security-hardening.md:559-572` lists its telemetry, error reporting and other non-essential traffic, each with a variable that turns it off, and no one checked whether anything beyond the model API remains. The experimenter picks the mechanism, and D7's feasibility pass tests it. `anchors: row34`
36. `[engineer-verified: "Sure require confinement"]` The design requires enforced confinement rather than detection after the fact. The quote covers only that choice, so how the harness enforces and verifies the confinement stays open, within D1's bar. `anchors: root`
37. `[verified: docs/case-studies/claude-code-sandbox-write-boundary.md:94-98]` On Ubuntu 24.04 and later, per Claude Code's sandboxing doc as that page quotes it, "the default AppArmor policy prevents bubblewrap from creating the user namespaces it needs for isolation". A host can therefore block a namespace-based confinement until its policy changes, so D5 allows "cannot be built" as an outcome, and the repository owner decides what follows. `anchors: row35`
38. `[engineer-verified: "Reshape per architect (Recommended)"]` The engineer chose the reshape row 39 describes. This supersedes the label "Keep the current text", which the engineer selected earlier for the draft page's confinement paragraph and which now stands only as history. `anchors: root`
39. `[unverified]` The description behind row 38's label was the session's relay of a plan-architect consult, which is model output. The page keeps its pre-registration text and drops the harness apparatus, which goes to a harness issue. The consult held that the experiment design is sound. It argued that D1 lets a pre-lock revision change everything but the asymmetry, the bound and the confinement bars, so harness apparatus on the page commits to nothing yet gets reviewed as binding prose. It also named three defects that survive any page shape, which D3 and D10 fix: which issue supplies the prompt, when a body counts as recovered, and a Limits line that restated the specification filter. `anchors: row38`
40. `[engineer-verified: "Draft a harness issue (Recommended)"]` The session drafts a harness issue to hold the apparatus the page drops. The draft lives outside the repository and is not part of the diff. `anchors: row38`
41. `[engineer-verified: "Run the pre-count (Recommended)"]` The session counts candidate PRs before the page states a pool figure. Row 22 records the result. `anchors: row22`
42. `[unverified]` plan-architect computed these by hand from row 28, without running python, and staff-sdet's round-6 figures match them. At `psi` = 0.20 with no true effect, the outcome-2 bound at 40 and 75 PR tasks is:
   - 0.268 and 0.466 at a 10-point margin, pessimistic end;
   - 0.663 and 0.893 at a 10-point margin, favorable end;
   - 0.539 and 0.803 at the 15-point margin, pessimistic end;
   - 0.932 and 0.950 at the 15-point margin, favorable end.

   The bound reaches 0.80 at 38, 75 and 112 PR tasks for `psi` = 0.10, 0.20 and 0.30, and a zero estimate gives `U` < 0.15 from 34.1 tasks at `psi` = 0.20. If stripping costs 10 points, the interval condition holds with probability 0.108 and 0.167 at 40 and 75 tasks at the pessimistic end, and 0.241 and 0.069 at the favorable end. At a zero estimate, `U` is 13.9 points at 40 tasks and 10.1 at 75, at the pessimistic end. At `psi` = 0.30, outcome 1's power at 75 tasks is 0.694 at the pessimistic end. Verification step 2 recomputes them. `anchors: row28`
43. `[engineer-verified: "Let's go with the 15-point margin, the two-sided interval. That sounds good."]` The decision rule's margin is 15 points, and every outcome reads the two-sided interval. This supersedes the number in row 3's label, which now stands only as history. `anchors: root`
44. `[unverified]` The session's proposal behind row 43's quote was a one-sided bound for outcome 2 plus a 15-point margin, giving outcome 2's reachability at the pre-count's scale as the margin's reason (row 42), and a plan-architect consult rejected the one-sided part. The session reads the quote as leaving the asymmetry rows 3-4 record unchanged. `anchors: row43`
45. `[engineer-verified: "Only when it cannot be built (Recommended)"]` D1's owner carve-out from enforced confinement applies only when the confinement cannot be built, as D5 defines it (row 47). `anchors: row36`
46. `[unverified]` The session reports a funnel over first-parent commits on `main` at 0761371: 508 add a file under `.claude/plans/`, 334 of those touch a non-test `.py` or `.sh` file, 330 of those also touch a test file, and 314 of those also touch none of `requirements-dev.txt`, `pyproject.toml` and `.github/workflows/tests.yml`. It also reports that about half of those 314 plans' Contexts state the solution. Verification step 2 command 6 restates the funnel's rule, plan-architect ran neither count, and the page states neither. `anchors: root`
47. `[engineer-verified: "Yeah, the box the agent can't run in is unusable."]` A confinement the CLI cannot complete a turn inside counts as one that cannot be built. `anchors: row45`
48. `[unverified]` The session wrote the question row 47's quote answers: whether a confinement in which the CLI cannot complete a turn counts as "cannot be built" for the owner carve-out. Read with D1, widening the network hosts the confinement allows so that the CLI can run narrows what the confinement covers, so only the repository owner can decide it. `anchors: row47`

## Critical files

- `docs/case-studies/comments-stripped-ab.md` (new): the design page, written from D1-D10 under the writing constraints above, in roughly 300 lines or fewer. The H1 names the question and marks the page as a pre-registered design. The branch's current draft is reusable wherever it already meets a rule, and the rewrite drops everything D5-D7 no longer require.
- `docs/case-studies.md`: one bullet appended at the end of `## Index`, in its neighbors' shape. The bold title matches the page H1, the link is relative, and a short summary states three things: the page is a pre-registered design with results pending, the outcome is the merged PR's held-out tests, and the rule is asymmetric. The bullet has no figure, no `§` citation and no per-account path.
- `.claude/plans/comment-ab-experiment-design.md`: this plan, committed with the change per `/plan-it` Step 7.

The page cites, for reuse, the files and doc sections D1-D10 name, and the diff modifies none of them. The harness-issue draft lives outside the repository and is not part of the diff (row 40).

**Dispatch split:** two dispatches, in sequence.
1. One `code-writer` dispatch writes both files. The index bullet summarizes the page, so a second writer would have to restate the whole design. The prompt points at this plan file by path and names Verification steps 1-4, less step 2's commands 5 and 6, as the dispatch's own checks. For D9 it carries the pre-count's run date and the margin decision: its decider, the repository owner, row 43's quote, and the date.
2. One read-only `general-purpose` dispatch with `model: sonnet`, which did not write the page, then runs Verification step 5. Any failure it returns goes back to a `code-writer` fix dispatch, and step 5 reruns on the changed sentences.

## Verification

1. `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py` passes. For this diff it selects the hooks and skills test directories (row 25). Of their tests, only the pooled-marker test reads the page (row 9), and the state-path and citation tests read the index (row 11). None checks the page's claims, which step 5 covers.
2. Six commands reproduce the figures the plan states. A person runs commands 5 and 6 before the dispatch, because a hook denied command 5 to a reviewing subagent, and that run is row 22's rerun. A command that prints anything else stops the work, and its output is reported.
   - `python3 -c "import math; from statistics import NormalDist; z = NormalDist().inv_cdf; za, zb = z(0.975), z(0.80); count = lambda p, d: (za*math.sqrt(p) + zb*math.sqrt(p - d*d))**2 / d**2; print(*[math.ceil(count(p, 0.15)) for p in (0.10, 0.20, 0.30)], f'{count(0.20, 0.10):.1f}', f'{NormalDist().cdf(0.15*math.sqrt(75/(0.30 - 0.15**2)) - za):.3f}')"` prints `33 68 103 154.6 0.694`, outcome 1's counts, the 10-point check, and outcome 1's pessimistic power at 75 tasks and `psi` = 0.30 (rows 18, 42).
   - `python3 -c "import math; from statistics import NormalDist; z = NormalDist().inv_cdf; za, d = z(0.975), 0.15; print(*[math.ceil(p*((za + z(0.825))/d)**2) for p in (0.10, 0.20, 0.30)], f'{za*za*0.20/d**2:.1f}', *[f'{100*za*math.sqrt(0.20/n):.1f}' for n in (40, 75)])"` prints `38 75 112 34.1 13.9 10.1`, outcome 2's counts, the zero-estimate threshold, and `U` in points at a zero estimate for 40 and 75 tasks, pessimistic end (row 42).
   - `python3 -c "import math; from statistics import NormalDist; N = NormalDist(); za = N.inv_cdf(0.975); bound = lambda d, e, v, n: N.cdf(min(za, d*math.sqrt(n/v) - za) - e*math.sqrt(n/v)) - N.cdf(-za - e*math.sqrt(n/v)); print(*[f'{bound(d, 0, v, n):.3f}' for d in (0.10, 0.15) for v in (0.20, 0.20/3) for n in (40, 75)], '|', *[f'{bound(0.15, 0.10, v, n):.3f}' for v in (0.19, 0.19/3) for n in (40, 75)])"` prints `0.268 0.466 0.663 0.893 0.539 0.803 0.932 0.950 | 0.108 0.167 0.241 0.069`: the outcome-2 bounds D9 discloses, by margin, then variance end, then task count, and after the bar the interval condition's chance if stripping costs 10 points, by variance end, then task count (rows 28, 42).
   - `python3 -c "print(f'{0.6**10:.3f} {0.7**10:.3f}')"` prints `0.006 0.028` (row 33).
   - `python3 -c "import re, subprocess; log = subprocess.run(['git', 'log', '--first-parent', '--name-only', '--format=%x1e%B%x1f', '0761371'], capture_output=True, text=True, check=True).stdout; commits = [(text, set(filter(None, names.splitlines()))) for text, names in (record.split(chr(31)) for record in log.split(chr(30))[1:])]; is_test = lambda path: bool({'tests', 'fixtures'} & set(path.split('/')[:-1])) or path.split('/')[-1] == 'conftest.py' or (path.split('/')[-1].startswith('test_') and path.endswith('.py')); code = lambda names: any(path.endswith(('.py', '.sh')) and not is_test(path) for path in names); tested = lambda names: code(names) and any(map(is_test, names)); clean = lambda names: tested(names) and not names & {'requirements-dev.txt', 'pyproject.toml', '.github/workflows/tests.yml'}; linked = [names for text, names in commits if re.search(r'\bGH-[0-9]+|\b(close[sd]?|fix(e[sd])?|resolve[sd]?)\s+#[0-9]+', text, re.I)]; print(len(commits), len(linked), *[sum(map(check, linked)) for check in (code, tested, clean)], sum(clean(names) for text, names in commits))"` prints `1037 195 130 129 125 471`, the pre-count D9 discloses (row 22).
   - `python3 -c "import subprocess; log = subprocess.run(['git', 'log', '--first-parent', '--name-status', '--format=%x1e', '0761371'], capture_output=True, text=True, check=True).stdout; commits = [[line.split(chr(9)) for line in record.splitlines() if line] for record in log.split(chr(30))[1:]]; is_test = lambda path: bool({'tests', 'fixtures'} & set(path.split('/')[:-1])) or path.split('/')[-1] == 'conftest.py' or (path.split('/')[-1].startswith('test_') and path.endswith('.py')); code = lambda names: any(path.endswith(('.py', '.sh')) and not is_test(path) for path in names); tested = lambda names: code(names) and any(map(is_test, names)); clean = lambda names: tested(names) and not names & {'requirements-dev.txt', 'pyproject.toml', '.github/workflows/tests.yml'}; planned = [{entry[-1] for entry in entries} for entries in commits if any(entry[0] == 'A' and entry[-1].startswith('.claude/plans/') for entry in entries)]; print(len(planned), *[sum(map(check, planned)) for check in (code, tested, clean)])"` prints `508 334 330 314`, row 46's funnel.
3. Every `§` citation in the page names a heading that `grep -nF` finds in its target file. Every repository path the page names passes `git ls-files --error-unmatch <path>`.
4. A read of the page finds no breach of the writing constraints, and every term the page uses is defined in it. The read also confirms:
   - `grep -ni backstop` on the page prints nothing;
   - `grep -niE 'canar|run marker|known-target|leak probe|probe host|target class|environment class|positive control|unconfined|go criterion|\.venv|install-dev'` on the page prints nothing, so none of the apparatus D5-D7 dropped remains;
   - `wc -l` on the page prints roughly 300 or fewer.
5. An independent check, run by the read-only dispatch the dispatch split names. It gets the page's path and this plan's path, and returns one line per item, with `file:line`:
   - every sentence of the page that states how a file, test, tool, script or setting in this repository behaves, confirmed or contradicted against its source, with the ledger's `[verified]` rows as a seed, not a limit;
   - every bullet of D1-D10, as the required-statements list, present on the page or absent;
   - every count, threshold, bound and funnel number on the page, equal to its step-2 output or not;
   - whether D9's four outcomes, as the page words them, are exclusive and cover every combination of `L`, `U`, the worst-case bound and the trap bound.

   Every contradicted claim, absent statement, mismatched figure or exclusivity failure is fixed and rechecked before commit.

## Out of scope

- Building the harness (task miner, stripper, session runner, scorer, analysis), running the feasibility pass, committing the task manifest and the lock, and running the experiment. Later plans do that once this page lands (row 1). The apparatus the page drops goes to the harness issue drafted this session, which the page does not cite (row 40).
- Moving the margin again or relaxing the asymmetric rule. The engineer chose both (rows 3, 43), and once the page ships D1 routes a margin change to the repository owner.
- A binding futility look. Adding one is a pre-lock revision option that only makes outcome 2 harder to reach, so it passes D1's bar without an owner decision.
- Editing `claude/.claude/CLAUDE.md` § "Durable text", `comment-discipline-reviewer`, `/code-review` or any agent body, since GH-1408 leaves narrowing undecided (G1).
- The instrumentation follow-ups: round start and end times in `review-round-cost`, and a reviewer or angle field on ledger rows. They need a separate plan (Context).
- A format-reverted third arm, restoring comments a review rewrote only to clear a format finding. It needs a reviewer or angle field on ledger rows and its own pre-registration (row 2).
- Tasks from other repositories, including stow consumers' codebases. The engineer selected this repository's merged PRs (row 5), and other codebases carry provenance limits (G2).
