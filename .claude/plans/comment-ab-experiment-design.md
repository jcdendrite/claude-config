# Comments-stripped A/B experiment design (GH-1408)

## Context

Goal: commit a pre-registered design document for an A/B experiment that tests whether code comments help agents write correct code, so the open question in GH-1408 (should the §Durable text comment rules be narrowed) has a measurement path.

Ask: GH-1408, "Evaluate narrowing comment/docstring rules; design a comments-stripped A/B experiment", narrowed to the design document by the engineer's selected label "Experiment design doc only (Recommended)". The engineer also selected the label "Asymmetric, 10-point gap (Recommended)" for the pre-registered decision rule and the label "This repo's merged PRs (Recommended)" for the task source.

Problem and why now: GH-1408's decisions say narrowing is "not decided, and not supported by this evidence", and its sample of agent Read-then-edit sequences cannot rule out a substantial dependence on comments. The issue sketches an experiment (base-commit clones with comments stripped, held-out merged-PR tests as the outcome, McNemar sizing of about 150 pairs) but no document fixes its mechanics, tasks, or decision rule before a run. The intended outcome is a case-study page under `docs/case-studies/`, indexed in `docs/case-studies.md`, that a later session can execute without re-deciding the design. The plan changes no rule and builds no harness. The instrumentation follow-ups the issue lists are a separate plan. They are round start and end times in `review-round-cost`, and a reviewer or angle field on ledger rows.

## Approach

Write one pre-registered design page, `docs/case-studies/comments-stripped-ab.md`, and add it to the index in `docs/case-studies.md`. In the design, an authoring agent implements this repository's merged PRs from a single-commit snapshot of each PR's base, once with comments and docstrings intact and once with them stripped. The merged PR's own held-out tests score both runs, under an asymmetric decision rule fixed before any session runs. A feasibility pass that reads no authoring outcome measures what only a built harness can, such as the eligible task count, the test environment, flaky tests and whether the isolation holds. A dated lock records it before the first authoring session.

**What the page fixes.** The labels D1-D10 exist only in this plan, for the ledger. They never appear in the page, which defines every term it uses in its own text.

- **D1. Lead, revisions and motivation.**
  - Lead: the page is a pre-registration. Tasks, arms, outcome, analysis and decision rule are fixed here before the first session.
  - `Revisions`: a closing section of dated entries, each with its reason. A revision before the lock may change the design, but none may remove the asymmetry or move the 10-point bound (rows 3-4). The *lock* is the revision that records the feasibility pass (D7). After it, no revision changes the primary design, the manifest or the decision rule.
  - "Why this measurement is needed" cites GH-1408 for the evidence and for three of its decisions:
    - the accuracy rule is kept;
    - the "don't restate the code" rule is kept;
    - narrowing to "comment only for a non-obvious why" is not decided.
  - That section also cites `claude/.claude/CLAUDE.md` § "Durable text" as the rules under question, and it restates no figure from the issue (rows 7, 10).
  - The page may state the direction of the issue's transcript sample. The sample could not rule out a substantial dependence on comments. It leans toward "no" because it only sees reads followed by an edit.
- **D2. Question and arms.**
  - The question: does stripping comments and docstrings from the code an authoring agent reads lower how often its change passes the merged PR's held-out tests?
  - The *intact arm* gets the snapshot as committed. The *stripped arm* gets the same snapshot after D4.
  - Shared by both arms: the model ID, prompt, tools, config directory, test environment (D5) and per-session budget cap. The model is the one this repository routes code writing to, and the manifest records its exact ID.
  - Each run is one authoring session that edits the working tree and stops without committing. It may run tests.
  - No review pipeline runs in either arm:
    - this keeps `comment-discipline-reviewer` off in both arms;
    - it also keeps off the inline comment check `/code-review` runs even when it spawns no such reviewer (row 21).
- **D3. Tasks.**
  - A merged PR is eligible when all of these hold:
    - it merged to `main` as one commit, and its title names a GH issue or it closes one (row 29);
    - its diff changes at least one non-test shell or Python file;
    - it changes none of `requirements-dev.txt`, `pyproject.toml` and `.github/workflows/tests.yml`, so one environment and one pytest configuration serve base and merge (rows 26, 32);
    - it has at least one fail-to-pass test.
  - Test files. A *test file* sits under a directory named `tests` or `fixtures`, or is named `conftest.py` or `test_*.py`, so helpers and support modules in a test directory count (row 32). Every other file is non-test code. The PR's test files are the test files its diff adds, changes or deletes.
  - Test classes. A *fail-to-pass test* fails at the base (the merge commit's first parent) with the PR's test files overlaid, and passes at the merge commit. *Pass-to-pass tests* are the other tests in those files that pass at both. Classification runs in snapshots built as D5 states, never in a checkout with history. It runs each test ten times per commit, a cost choice, and drops a test whose result varies.
  - Prompt: a fixed preamble, identical in both arms and silent about comments, then the named issue's body verbatim, as it stood at the merge. Never the PR body or plan file, which describe the implementation.
    - The manifest freezes each prompt's text and its hash.
    - A task is excluded when that version of the body cannot be recovered (row 29).
    - A task is excluded when the prompt names the PR, its branch or a commit.
  - Specification filter: a task is excluded when its prompt omits a name, flag or path that its fail-to-pass tests need and the base does not hold.
  - Why-comment filter, judged from the base snapshot alone before any session runs. The *touched region* is each function, or each contiguous top-level shell block, that the merged diff changes, as it stands at base. A task is kept only when its touched region holds at least one comment or docstring stating a reason, constraint or contract the code does not show. A task whose code changes are all new files has no touched region and is excluded.
  - A *PR task* is an eligible PR that passes both filters and the stripping checks (D4). The page reports the exclusions per filter and check.
  - Raters: one rater makes every specification-filter and why-comment judgment. A second rater independently judges a random 30 tasks, or every task when fewer. When the two agree on fewer than 90% of those judgments, the second rater judges every task, and a task is kept only when both raters keep it.
  - *Trap tasks*: at least 10, hand-built at one fixed base commit. They are not merged PRs (row 6). Each starts from a comment stating a rule the code does not show. Its task statement invites an implementation that breaks the rule without mentioning it, and its held-out test fails exactly when the rule is broken. Traps never enter `Delta` (D8). They show whether the harness detects a comment effect where one is built in, which outcome 2 requires (D9). Ten is a floor chosen for cost (row 33). Before any session, each trap passes three checks:
    - the rule's key terms, listed when the trap is built, appear nowhere in the stripped-arm snapshot;
    - a reference implementation that breaks the rule passes every test at the base and fails the held-out test;
    - a reference implementation that keeps the rule passes the held-out test.
  - The page names GH-1408's trap example as one that fails the first check (row 17). A comment in `claude/.claude/hooks/_config.sh` says the code down to the bash floor check's `return 1` must parse and run on bash 3.2. `.claude/rules/shell-floor.md` and `docs/hooks.md` state the same rule in markdown both arms keep.
  - The *task manifest* holds the following, and the lock commits it (rows 5-6, 22):
    - the PR tasks in a seeded random order, with the seed;
    - each task's filter labels, test IDs, base and merge commits, and merge date;
    - each prompt with its hash;
    - the model's stated training-data cutoff;
    - each task's installed package list, the host tool versions and the `HOME` contents (D5);
    - the preserve list (D4).
- **D4. Stripping.**
  - Scope: the whole stripped-arm snapshot, shell and Python files only, tests included. Markdown, config files and every other file stay untouched in both arms.
  - Line-preserving: a comment-only line becomes empty, and a trailing comment goes with the whitespace before it. Docstring lines become empty, and a body left empty gets `pass`. Line-number contracts therefore still hold.
  - A `#` inside a quoted string, `$#`, `${#...}` or a heredoc body is not a comment. The stripper reports every `#` it leaves inside a heredoc body or quoted string, and the page reports the count per arm.
  - The *preserve list* names comments both arms keep. It starts with every category that a tool, test or program at the base reads (row 12):
    - shebangs;
    - tool directives, such as `# shellcheck`, `# noqa`, `# fmt:` and `# pragma`;
    - the hook headers and heading citations that hook tests read;
    - docstrings that code reads through `__doc__`, in a test or at run time.
  - *Stripping checks*, per task, before any session:
    - every stripped shell file passes `bash -n`;
    - every stripped Python file's syntax tree equals the intact file's after the same docstring removal;
    - the whole test suite fails the same tests at the stripped base as at the intact base;
    - pass-to-pass tests pass, and fail-to-pass tests still fail, at the stripped base with the held-out test files overlaid;
    - fail-to-pass tests pass at the merge commit with its non-test code stripped.
  - A check failure caused by stripping a comment that something reads adds that comment to the preserve list. A task that still fails is excluded, and the page reports the count. The lock freezes the list.
  - The page sets aside read-time stripping for GH-1408's reason: it leaks through `grep`, `cat` and `git show`, and it breaks `Edit` in the stripped arm only (row 7).
- **D5. Isolation and environment.**
  - Snapshot: a fresh repository holding one commit, the base tree stripped or not, with a constant author, date and message and no remote. It is built from `git archive` the way `evals/review_bench/fixture_repo.py` builds review-bench fixtures (row 16). Classification, the stripping checks, sessions and scoring all run in snapshots built this way.
  - Environment, identical for both arms and for every test run:
    - a `.venv` at the snapshot root, on the Python minor that the base's `.github/workflows/tests.yml` pins, holding the base's `requirements-dev.txt`, so `scripts/venv-run.sh` resolves it (row 26);
    - host tools: `bash`, `git`, `jq`, `gh`, `stow`, `direnv`, `strace`, `zsh`, coreutils' `timeout`, `sha256sum` and `realpath`, and every other executable the base's tests look up, which the feasibility pass lists (row 31);
    - a `HOME` the harness builds with the same contents for every run, holding nothing from the experimenter's home.
  - The harness builds `.venv` itself, because `install-dev.sh` exits without a private-projects opt-in file (row 26). Both arms of a task must show the same installed package list.
  - Config: every session runs under a dedicated config directory that stows nothing from this repository and registers no hook, plugin or marketplace. Three facts force this:
    - the experimenter's normal config resolves into a live checkout holding every task's merged answer with its comments intact (G3);
    - this repository opts into worktree enforcement, so that config's hooks would block edits (row 13);
    - that config's plugin cache holds post-merge plugin code (row 13).
  - The page states the outcome the harness must reach: no hook, skill or plugin from this repository loads. It names no CLI mechanism (row 23). The snapshot's own project instructions still load, in both arms (row 27).
  - Network: web tools are off, and the session's environment holds no GitHub credentials.
  - Backstop: a session fails the backstop when its transcript shows a Read, Grep or Glob path, or a Bash command string, naming a path under a live checkout of this repository, the experimenter's own home or config directory, or the repository's remote host. The review bench's check sees only Read, Grep and Glob, and only a live checkout's copies of the changed files and the config `projects/` root (row 14), so this widens it. D6 states what a failure costs, and D7's leak probe tests the backstop.
  - Frozen conditions: the model ID, CLI version, manifest commit, harness commit, host tool versions, OS, installed package lists and `HOME` contents are recorded at the start and end of each run block. A change between the two, or a host or package difference from the manifest, invalidates the block. This follows `evals/README.md` § "Frozen conditions and invalidation".
- **D6. Outcome and run validity.**
  - Scoring: the harness overlays the PR's test files (D3) as they stand at the merge commit, intact in both arms, onto the session's tree. They replace any same-path file the session edited. The tests run in the snapshot's environment (D5).
  - A run *passes* when every fail-to-pass and pass-to-pass test passes. A failing test reruns once, and it fails only when both runs fail. The page reports per arm how many runs a rerun changed.
  - A *valid replicate* is a session that ended by itself or at the budget cap, with no backstop failure and no harness error. A harness error is a failure outside the session's control, such as an API or CLI error. A session that hit the cap is scored like any other.
  - A backstop failure or harness error reruns the session once, and a second one leaves the replicate missing. The page reports per arm the counts of capped sessions, backstop failures, harness errors and missing replicates.
  - Diff similarity to the merged diff is not measured, because many correct diffs exist.
  - Secondary measures, descriptive only, help read discordant tasks (row 7):
    - each task's diffs compared with comment lines removed from both;
    - the comments each arm added.
- **D7. Sample size, feasibility pass and lock.**
  - Sizing: the page states McNemar's normal-approximation formula `n = (z_a*sqrt(psi) + z_b*sqrt(psi - d^2))^2 / d^2` and its inputs (rows 18-19):
    - `d` = 0.10, the pass-rate gap;
    - `psi` = 0.20, the share of discordant pairs, an assumption carried from GH-1408;
    - `z_a` = 1.95996, from two-sided alpha 0.05;
    - `z_b` = 0.84162, from power 0.80.
  - The result is 154.6, so 155 independent pairs, consistent with the issue's "about 150". Because `psi` is assumed, every figure below uses the grid `psi` = 0.10, 0.20 and 0.30, where the count is 77, 155 and 234.
  - The verdict reads an interval, not McNemar's test, so the count is a proxy. It assumes replicates add no information, which makes it conservative (row 20).
  - Each arm runs `k` = 3 independent replicate sessions per task. Replicates of one task are correlated, so they count as fewer than `k` pairs (row 20).
  - The run takes PR tasks in manifest order, up to 155.
  - The page gives the session count, `2 * k * (PR tasks + trap tasks)` plus the leak probe, and the test runs of classification and the stripping checks, so the cost is visible before a run. It gives no dollar estimate (row 22).
  - The *feasibility pass* runs before any authoring session and reads no authoring outcome. It runs the eligibility filters, the raters' judgments, the snapshots and environments, test classification, the stripping checks (D4) and the trap checks (D3).
  - It also runs a *leak probe*: one session under D5's conditions in a stripped snapshot, told to find and copy the merged PR's change by any means. Nothing scores it. The run goes ahead only when each route the probe tried was blocked by the harness or flagged by the backstop. Otherwise the harness is fixed and the probe reruns.
  - The lock records:
    - the PR task count, and the exclusions per filter and check;
    - the raters' agreement, and the leak probe's routes and result;
    - the share of tasks merged after the model's stated training-data cutoff;
    - the preserve list and the manifest commit.
  - The lock also records two probabilities at the measured PR task count `n`, where `Phi` is the standard normal distribution function (rows 18, 28):
    - outcome 1 if stripping costs 10 points, `Phi(d*sqrt(n/(psi - d^2)) - z_a)`;
    - `L` <= 0 <= `U` < 0.10 if stripping costs nothing, `Phi(min(z_a, d*sqrt(n/psi) - z_a)) - Phi(-z_a)`. It bounds outcome 2 from above, since outcome 2 also needs the worst-case and trap conditions.

    Both are computed at each `psi` on the grid, assuming replicates add no information. At `n` = 155 and `psi` = 0.20 they are 0.815 and 0.770.
  - In the lock, the experimenter decides whether the run goes ahead at the measured count. No authoring outcome exists yet, so the choice cannot bias the result.
- **D8. Analysis.**
  - The unit is the task. Per task and arm, the pass rate is passing valid replicates over valid replicates.
  - `d_task` = intact rate minus stripped rate. `Delta` = mean `d_task` over PR tasks. A positive `Delta` means stripping hurt.
  - Interval: a 95% percentile bootstrap over tasks, with 10,000 draws and a recorded seed. Intervals alone decide the verdict, and no separate significance test runs.
  - A task left with no valid replicate in an arm is dropped from the primary interval, and the page reports drops per arm.
  - *Worst-case recomputation*: the interval is recomputed over every PR task, trap tasks excluded, with each missing stripped-arm replicate counted as a fail and each missing intact-arm replicate counted as a pass. A task with no valid replicate in either arm therefore counts as `d_task` = 1. D9 reads it for outcome 2 only.
  - *Trap interval*: the same bootstrap over trap tasks. D9 reads its lower bound for outcome 2 only.
  - Secondary results never change the verdict:
    - trap pass rates per arm;
    - `Delta` over tasks merged after the model's training-data cutoff;
    - the share of tasks where no replicate passed in either arm.
- **D9. Decision rule**, applied to `Delta` and its interval from `L` to `U`. The 10-point gap enters twice: as the effect D7 sizes for, and as the bound outcome 2 must exclude. Outcome 1 needs no minimum gap. The four outcomes are exclusive and cover every result:
  1. *Harm*: `L` > 0. Comments carry weight for authoring agents on this code, so no narrowing of the comment rules may cite this study. The page reports `Delta` and the interval against the 10-point effect of interest.
  2. *Harm excluded*: there is no evidence of harm, and this task distribution excludes a 10-point harm. This does not by itself justify narrowing, since human readers and review-round cost go unmeasured here. It needs `L` <= 0 <= `U` and all of these:
     - `U` < 0.10;
     - the worst-case recomputation's upper bound is below 0.10;
     - the trap interval's lower bound is above 0, which shows the harness can detect a built-in comment effect.
  3. *Inconclusive*: `L` <= 0 <= `U`, and a condition of outcome 2 fails. There is no reading. A short pool lands here, and so does a result that holds only while missing replicates are ignored, or that comes from a harness the traps did not show to be sensitive.
  4. *Stripped better*: `U` < 0. Comments misled agents on this distribution. That does not justify narrowing either, because the kept accuracy rule already targets misleading comments. The discordant tasks get inspected for stale comments.

  Only outcome 2 can support a narrowing argument, and never alone. That is the asymmetry the selected rule asks for (rows 3-4). The page cites `docs/case-studies/opus-frontload-review-rounds.md` as this repository's earlier pre-registered study with an asymmetric rule (row 8).
- **D10. Limits and results.**
  - Limits the page states:
    - It measures one authoring session's correctness, not the review pipeline, human readers, or the cost of review rounds.
    - Much of this repository's rationale lives in markdown that both arms keep, including the plan files under `.claude/plans/` and the project instructions that load in both arms (row 27). That can mask a comment effect.
    - A result may not transfer to stow consumers' codebases, which the same rules govern.
    - Only tasks whose touched code holds a why-comment are run, so the result says nothing about code without one.
    - The code already passed comment review.
    - Held-out tests cover only what the PR's author tested.
    - Issue bodies vary in how much of the answer they state, beyond what the specification filter catches.
    - The model may have trained on this public history. Memorized answers, like tasks no run can pass, pull `Delta` toward 0, which favors outcome 2 (row 30). The post-cutoff subgroup and the lock's post-cutoff share are the only checks.
    - A leak that the probe did not try and the backstop's string match cannot see, such as a path or host built up at run time, goes undetected. In the stripped arm it raises the pass rate, which favors outcome 2.
    - Sessions run without the user-level configuration this repository installs.
    - Only shell and Python comments are stripped.
    - Blank lines, and bodies emptied to `pass`, show where comments were.
  - A `Results` section reads "Not yet run." Results from this repository's tasks on one account publish under `docs/private-project-redaction.md` § "This repository, one account" (row 24).

**Writing constraints for the page.**
- None of "pooled across", "machine-wide", "cross-machine" or "multi-account" appears in it (row 9).
- No figure from GH-1408's Evidence or round-level reading appears. The only numbers are the design parameters and derived figures of D3, D6 and D7-D9 (row 10).
- No plan-local label, ledger row number, "this plan" or session narration appears.
- It cites files by path and doc sections by `§` heading, never by line number. Per-account paths are written `<config-dir>/...`.
- A statement resting on an `[unverified]` row (rows 19, 20, 22, 23, 27, 28, 29, 30, 33) reads as an assumption or as a requirement on the harness, never as a fact.
- The index bullet carries no `§` citation and no per-account path, because `docs/case-studies.md` is inside the citation and state-path test corpora (row 11).

**Alternatives set aside.**
- *Page location.* A plan file is provenance, not a deliverable (`/plan-it` Step 1). An `evals/README.md` section documents a harness that does not exist yet. A `docs/design-decisions/` entry records a decision, and none is made yet. A case-study page holds the design now and the results later, in one home.
- *Running `/code-review` in both arms with `comment-discipline-reviewer` skipped.* No switch exists beyond a written skip rationale, and the inline comment check still runs (row 21). A review loop also multiplies session cost and variance.
- *Stripping only the touched files.* Agents read callers and sibling files, so the arms would differ in only part of what the agent reads.
- *Lighter isolation than a dedicated config directory.* (a) The normal config plus the review bench's Read/Grep/Glob leak check misses Bash reads and leaves this repository's hooks gating edits (row 14). (b) The normal config plus `permissions.deny` on live-checkout paths blocks only the tool-call shape each rule names and leaves hooks and plugins loaded (row 15).
- *Fixing the pool size, flake rates and test environment in the page now.* None is knowable until the miner and snapshots exist, so each fixed value would be a guess that a later revision replaces. Leaving them to the harness plan with no lock is lighter still, but then the pre-registration never shows whether outcome 2 was reachable. The feasibility pass measures them without reading an outcome.
- *A pilot of authoring sessions that estimates replicate correlation to shrink the task count.* It reads outcomes, and it spends tasks from a pool that is likely already short (row 22). The lock's probabilities, which assume replicates add no information, bound the count conservatively instead.
- *Limiting tasks to those merged after the model's training-data cutoff.* It would remove memorization, but it shrinks a short pool. The lock records the post-cutoff share, and the subgroup is reported.
- *McNemar over replicate pairs as the primary test.* Within-task correlation makes it overstate significance. Under an asymmetric rule, that turns noise into a blocking "harm" verdict (row 20). A majority vote per task followed by McNemar is valid, but it discards replicates and needs at least 155 tasks. A mixed-effects logistic model adds a third-party statistics dependency for the same question.
- *A sign-flip permutation test beside the interval.* The two can disagree near the boundary, for example a p-value above 0.05 with a lower bound above 0, so the verdict would depend on which one D9 reads first. Inverting the test into its own interval keeps one frame, but it needs a search over shifted nulls and a symmetry assumption to answer the same question.
- *Reading outcome 2 from complete cases only.* A stripped-arm session that explores more can fail the backstop more often, so ignoring missing replicates can hide harm. Under the asymmetry, only the outcome that can support narrowing has to survive the worst case.
- *Dropping the trap tasks.* Nothing would then show that the harness can detect a comment effect, so a null result from an insensitive harness would read as "harm excluded".

### Assumption ledger

**Root:** GH-1408 leaves open whether the comment rules should narrow, and nothing fixes how to measure whether comments help authoring agents. This change commits a pre-registered design page, so a later run cannot re-decide its tasks, outcome or decision rule after seeing data.

**Givens:**
- G1. GH-1408's decisions and its Out of scope stand: keep the accuracy rule, keep the "don't restate the code" rule, leave narrowing undecided, treat PR-defined terms as not format-only, and do not remove docstrings outright. *Reason: the issue owns those decisions, and this plan designs a measurement for what the issue leaves open.*
- G2. A figure from a wider-corpus read never enters a public artifact, except under the owner's case-by-case authorization for that artifact. *Reason: the repository owner sets this policy in `docs/private-project-redaction.md` § "A wider corpus goes to the owner, never into a public artifact".*
- G3. The experimenter's normal Claude Code config is a stow of this repository, so a session that loads it reads, runs and is gated by a live checkout. *Reason: the install architecture works this way (CLAUDE.md: `claude/` is stowed into `$HOME`; `evals/README.md` § "Out-of-session reads"), and changing it is outside this plan.*
- G4. A published case study is a preserved record. *Reason: `claude/.claude/CLAUDE.md` Axis 3 governs it, and `claude-skills/skills/tests/test_skills.py:8349` names `docs/case-studies/**` as preserved records. This is what makes the page a pre-registration.*

**Mechanisms:**
- The case-study page plus one index bullet (D1). `anchors: row8`
- Repository-wide, line-preserving stripping with a preserve list that the stripping checks grow and the lock freezes (D4). `anchors: row12`
- A dedicated config directory, a built `HOME` and a transcript backstop, tested by a leak probe (D5, D7). This is the heaviest isolation primitive, and the two lighter ones fail as Alternatives (a) and (b) state. `anchors: row14`
- One snapshot construction and one environment for classification, the stripping checks, sessions and scoring (D5). `anchors: row26`
- Author-only sessions with no review pipeline (D2). `anchors: row21`
- A feasibility pass and lock ahead of the first session (D7). The two lighter options, fixed guesses and an unrecorded harness step, fail as Alternatives states. `anchors: row22`
- A task-level bootstrap interval as the only inferential frame (D8). `anchors: row20`
- The four-outcome decision rule, with outcome 2 also read under the worst-case recomputation and the trap interval (D9). `anchors: row4`
- One `code-writer` dispatch, then one read-only claim-check dispatch (Critical files). `anchors: root`

**Rows:**
1. `[engineer-verified: "Experiment design doc only (Recommended)"]` The deliverable is the design page and its index bullet. `anchors: root`
2. `[unverified]` The session wrote the description behind row 1's label: a design doc that reserves an optional third arm, measurable once ledger rows carry an angle field, with no rule change and no instrumentation. The plan departs from it in one way: it reserves no third arm. The arm cannot run until ledger rows carry that field, and it would need its own pre-registration, so reserving it now fixes nothing a later plan could not add. `anchors: row1`
3. `[engineer-verified: "Asymmetric, 10-point gap (Recommended)"]` The decision rule is asymmetric and built around a 10-point pass-rate gap. `anchors: root`
4. `[unverified]` The session wrote the description behind row 3's label: a stripped arm at least 10 points worse (McNemar, p<0.05) blocks any narrowing, while no significant gap reads as "no evidence of harm" and does not by itself justify narrowing. D9 keeps the asymmetry. It uses the 10-point gap as the effect D7 sizes for and as the bound outcome 2 must exclude, and D1 bars any revision from removing the asymmetry or moving the bound. D9 departs from the description in four ways:
   - A task-level interval decides, not McNemar's test (row 20).
   - Outcome 1 fires at any `L` > 0, so a gap under 10 points whose interval excludes 0 counts as harm, where the description blocked narrowing only at 10 points or more.
   - D9 defines the outcome the description leaves open where the gap favors the stripped arm.
   - "Harm excluded" must also hold under the worst-case recomputation and the trap condition.

   Every outcome except "harm excluded" leaves the rules as they are, so counting a sub-10-point gap as harm changes a label, not an action. The last departure only makes "harm excluded" harder to reach. `anchors: row3`
5. `[engineer-verified: "This repo's merged PRs (Recommended)"]` Tasks come from this repository's merged PRs. `anchors: root`
6. `[unverified]` The session wrote the description behind row 5's label: merged PRs with a base commit and held-out tests, stratified by whether the touched code carries a why-comment, plus hand-built trap tasks, all public and reproducible with no private-project provenance. D3 departs from it in one way: it keeps only why-comment tasks, since no outcome reads the other stratum. Trap tasks are hand-built, not merged PRs, so row 5's label does not cover them, and only this session-written description does. GH-1408 sketches a trap (row 17). D3's other filters only narrow the eligible set. `anchors: row5`
7. `[verified: gh issue view 1408, run by the session after /plan-review round 1; it matches the round-1 draft of the body apart from one trailing blank line]` The live issue's Decisions, Proposed experiment, Out of scope and Follow-ups sections carry what G1 and D1-D10 rely on. That includes the reason to set read-time stripping aside, the instruction to drop diff similarity and compare diffs ignoring comments, and "about 150 pairs to detect a 10-point pass-rate gap if 20% of pairs disagree". `anchors: root`
8. `[verified: docs/case-studies.md:3-7; docs/case-studies/opus-frontload-review-rounds.md:3-5, :146]` A case study is the empirical record behind a design decision, indexed as one bold-title bullet. The repository's earlier pre-registered study fixed its gates before any scan and used a deliberately asymmetric rule. `anchors: root`
9. `[verified: claude-skills/skills/tests/test_skills.py:4205-4248]` A `docs/case-studies/*.md` file containing "pooled across", "machine-wide", "cross-machine" or "multi-account" fails CI unless it is registered. `anchors: root`
10. `[verified: docs/private-project-redaction.md:172-179; gh issue view 1408, its figure-authorization sentence, run by the session after /plan-review round 1]` A case study may not republish a wider-corpus figure. The issue's authorization covers its figures "in this issue" only. `anchors: root`
11. `[verified: claude-skills/skills/tests/test_skills.py:8345-8358, :8372-8374]` The state-path corpus excludes `docs/case-studies/**`. It includes `docs/case-studies.md`, and so does the citation-extraction corpus. `anchors: root`
12. `[verified: claude/.claude/hooks/tests/test_hook_alignment.py:8, :1825, :2107-2125; claude/.claude/hooks/tests/test_send_back_skipped_self_review.py:769-778; claude/.claude/scripts/tests/test_plan_shape.py:1308, :1314; claude/.claude/scripts/tests/test_pr_create_preflight.py:566, :798; claude/.claude/scripts/tests/test_transcript_session_pricing.py:1753; claude/.claude/scripts/tests/_move_fidelity.py:883; claude/.claude/hooks/tests/test_split_command_words.py:118, :239, :253, :453; claude/.claude/scripts/tests/test_transcript_review_rounds.py:6004; claude/.claude/hooks/tests/test_shellcheck.py exists]` Tests read the `# hook-class:` header at line 2, the `# tier-threat-model:` header at line 3 and the hook-header heading citations. Three tests read docstrings through `__doc__`, and one script builds its argparse help from its module docstring. Test files carry `# fmt:` and `# pragma` directives. `anchors: root`
13. `[verified: .claude/worktree-required exists; .claude/settings.json:1-22; README.md:284-308]` This repository opts into worktree enforcement. Its project settings enable four plugins from this repository's own marketplace, register no hook, and keep `**/claude/.claude/CLAUDE.md` out of loaded instructions through `claudeMdExcludes`. `anchors: root`
14. `[verified: evals/README.md:1101-1103, :1148-1150; evals/review_bench/runner.py:774-807]` The review bench fails a run only on a Read, Grep or Glob of a live checkout's copy of a changed file or of the config `projects/` root. Its sessions hold Bash, which that check does not see. `anchors: root`
15. `[verified: claude/.claude/rules/settings-json-conventions.md]` "A `permissions.deny` rule blocks only the command or tool-call shape it names." `anchors: row14`
16. `[verified: evals/review_bench/fixture_repo.py:1-5, :43-54]` The bench builds each fixture as a fresh repository from `git archive` trees. It uses a fixed author and date and a constant commit message, so `git log --all` cannot reach a later commit. `anchors: root`
17. `[verified: claude/.claude/hooks/_config.sh:26; .claude/rules/shell-floor.md:14; docs/hooks.md:424; shell-floor.md loaded into plan-architect's context on a Read of install-dev.sh; staff-sdet confirmed the first-check failure in round 2]` The rule behind the issue's trap example sits in a comment and in two markdown files both arms keep. `.claude/rules/shell-floor.md` is a path-scoped rule that loads when a session reads a shell file. `anchors: row6`
18. `[verified: hand arithmetic by plan-architect, which ran no python, z(0.975) = 1.95996, z(0.80) = 0.84162; staff-sdet reproduced 76.09, 154.60, 233.09, 0.815 and 0.795 in round 2]` `(1.95996*sqrt(0.20) + 0.84162*sqrt(0.19))^2 / 0.01` = 154.6. The same formula gives 76.1 and 233.1 at `psi` = 0.10 and 0.30. At `n` = 155, D7's probability pairs are 0.986 and 0.950 at `psi` = 0.10, 0.815 and 0.770 at 0.20, and 0.638 and 0.598 at 0.30. The 0.770 is P(`U` < 0.10) = 0.795 less the 0.025 that falls in outcome 4. Verification step 2 recomputes every figure. `anchors: row7`
19. `[unverified]` That formula is the normal-approximation sample size for McNemar's test. plan-architect read no source for it, and its match with the issue's independent "about 150" is the only corroboration. The page states the formula and its inputs rather than citing a source. `anchors: row18`
20. `[unverified]` Replicate sessions of one task are positively correlated. So McNemar over replicate pairs overstates significance, and a count that assumes replicates add no information is conservative. This is a statistical argument, not something read from a source. `anchors: row4`
21. `[verified: claude-skills/skills/code-review/SKILL.md:78, :85]` `/code-review` runs its inline "Non-durable comment" check even in rounds that do not spawn `comment-discipline-reviewer`. `anchors: root`
22. `[unverified]` staff-sdet counted 1037 first-parent commits on `main` in round 2, of which 128 subjects start with a GH issue reference and 145 contain one. Before D3's further filters, the why-comment filter among them, that makes 155 PR tasks unlikely. plan-architect cannot run git, and the per-commit filter count was not run. The lock records the measured count, and D9 reads a short pool as inconclusive. `anchors: row5`
23. `[unverified]` A session under a config directory with no marketplace registered loads none of the plugins that the snapshot's `.claude/settings.json` enables. D5 states the required outcome and leaves the mechanism to the harness plan. `anchors: row13`
24. `[verified: docs/private-project-redaction.md:97-103]` A measurement computed over this repository's own corpus on one account is publishable. `anchors: row10`
25. `[verified: claude/.claude/scripts/select-tests.py:358-366, :885]` A change under `docs/` selects the hooks and skills test directories. `anchors: root`
26. `[verified: scripts/venv-run.sh:27-42; .gitignore:3; install-dev.sh:3, :31-48, :89-98, :153]` `scripts/venv-run.sh` looks for `.venv` in the directory that holds the git common directory, which in a single-commit snapshot is the snapshot's root. `.venv` is gitignored. `install-dev.sh` builds `.venv` from the Python version `.github/workflows/tests.yml` pins and installs `requirements-dev.txt`. It exits 1 when no private-projects opt-in file exists under `HOME` or the config directory, and when it must build `.venv` and the pinned `python<X.Y>` is not on PATH. `anchors: root`
27. `[unverified]` A session started in a snapshot under a dedicated config directory still loads the snapshot's project instructions, meaning its root `CLAUDE.md` and `.claude/rules/`, minus what `claudeMdExcludes` names. The page states this as an expectation the harness confirms. `anchors: row13`
28. `[unverified]` D7's probabilities come from a normal approximation to the interval. When replicates add no information, `d_task` has variance `psi` under no effect and `psi - d^2` under a 10-point effect. Under no effect, `L` <= 0 <= `U` < 0.10 holds when the estimate, in standard errors, lies between `-z_a` and `min(z_a, d*sqrt(n/psi) - z_a)`. This is a statistical argument, not something read from a source. `anchors: row18`
29. `[unverified]` GitHub keeps an issue body's edit history and records which issues a PR closes. The miner can then recover each body as it stood at the merge, and find issue-linked PRs whose title names no issue. D3 excludes a task whose at-merge body cannot be recovered. `anchors: row6`
30. `[unverified]` Memorized answers and tasks no run can pass both pull `Delta` toward 0, which favors outcome 2. This is a statistical argument. `anchors: row3`
31. `[verified: a Grep for shutil.which( calls across this repository's Python files; install-dev.sh:3; staff-sdet counted 109 test files calling it and 6 reading home paths in round 2]` Tests at this plan's base look up `bash`, `git`, `jq`, `gh`, `stow`, `direnv`, `strace`, `zsh`, `timeout` or `gtimeout`, `sha256sum` and `realpath`, besides common POSIX tools. `shellcheck` comes from `requirements-dev.txt`. `anchors: root`
32. `[verified: Globs for test_*.py and conftest.py under claude/.claude, claude-skills, evals and plugins; pyproject.toml:17-42]` Test code sits under directories named `tests`, except `evals/test_*.py` and `evals/conftest.py`, and support modules such as `claude/.claude/tests/helpers.py` sit there too. The root `pyproject.toml` holds pytest's `pythonpath`, markers and collection rules. `anchors: root`
33. `[unverified]` Ten traps can meet D9's trap condition when the built-in effect is large. If each trap shows the full effect or none, a bootstrap draw holds no effect-showing trap with probability 0.6^10 = 0.006 when four of ten show it, and 0.7^10 = 0.028 when three do. Against the 0.025 tail, the condition needs about four. This is a statistical argument. `anchors: row4`

## Critical files

- `docs/case-studies/comments-stripped-ab.md` (new): the design page, D1-D10, under the writing constraints above. The H1 names the question and marks the page as a pre-registered design.
- `docs/case-studies.md`: one bullet appended at the end of `## Index`, in its neighbors' shape. The bold title matches the page H1, the link is relative, and a short summary states three things: the page is a pre-registered design with results pending, the outcome is the merged PR's held-out tests, and the rule is asymmetric. The bullet has no figure, no `§` citation and no per-account path.
- `.claude/plans/comment-ab-experiment-design.md`: this plan, committed with the change per `/plan-it` Step 7.

The page cites these files for reuse and the diff does not modify them:
- `evals/review_bench/fixture_repo.py` (snapshot construction);
- `live_checkout_leak_targets`, `is_changed_file_leak` and `default_live_checkout_roots` in `evals/review_bench/runner.py` (leak backstop, widened as D5 states);
- `install-dev.sh` and `scripts/venv-run.sh` (test environment);
- `evals/README.md` § "Out-of-session reads" and § "Frozen conditions and invalidation";
- `docs/case-studies/opus-frontload-review-rounds.md`;
- `docs/private-project-redaction.md` § "This repository, one account".

**Dispatch split:** two dispatches, in sequence.
1. One `code-writer` dispatch writes both files. The index bullet summarizes the page, so a second writer would have to restate the whole design. The prompt points at this plan file by path and names Verification steps 1-4 as the dispatch's own checks.
2. One read-only `general-purpose` dispatch with `model: sonnet`, which did not write the page, then runs Verification step 5. A claim it contradicts goes back to a `code-writer` fix dispatch, and step 5 reruns on the changed sentences.

## Verification

1. `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py` passes. For this diff it selects the hooks and skills test directories (row 25). Of their tests, only the pooled-marker test reads the page (row 9), and the state-path and citation tests read the index (row 11). None checks the page's claims, which step 5 covers.
2. Two commands reproduce D7's figures:
   - `python3 -c "import math; from statistics import NormalDist; z = NormalDist().inv_cdf; za, zb, d = z(0.975), z(0.80), 0.10; print(*[math.ceil((za*math.sqrt(p) + zb*math.sqrt(p - d*d))**2 / d**2) for p in (0.10, 0.20, 0.30)])"` prints `77 155 234`.
   - `python3 -c "import math; from statistics import NormalDist; N = NormalDist(); za, d, n = N.inv_cdf(0.975), 0.10, 155; print(*[f'{N.cdf(d*math.sqrt(n/(p - d*d)) - za):.3f}/{N.cdf(min(za, d*math.sqrt(n/p) - za)) - N.cdf(-za):.3f}' for p in (0.10, 0.20, 0.30)])"` prints `0.986/0.950 0.815/0.770 0.638/0.598`, one outcome-1 and outcome-2-bound pair per `psi`.
3. Every `§` citation in the page names a heading that `grep -nF` finds in its target file. Every repository path the page names passes `git ls-files --error-unmatch <path>`.
4. A read of the page against the writing constraints finds no breach:
   - no GH-1408 figure;
   - no plan-local label;
   - every term defined in the page;
   - statements resting on rows 19, 20, 22, 23, 27, 28, 29, 30 and 33 worded as assumptions or as harness requirements.
5. An independent claim check, run by the read-only dispatch the dispatch split names. It gets the page's path and this plan's path. It lists every sentence of the page that states how a file, test, tool, script or setting in this repository behaves, using the ledger's `[verified]` rows as a seed, not a limit. It checks each sentence against its source and returns one verdict per claim, confirmed or contradicted, with `file:line`. Every contradicted claim is fixed and rechecked before commit.

## Out of scope

- Building the harness (task miner, stripper, session runner, scorer, analysis), running the feasibility pass, and committing the task manifest and the lock. That is a separate plan once this page lands (row 1).
- Running the experiment or any pilot (row 1).
- Editing `claude/.claude/CLAUDE.md` § "Durable text", `comment-discipline-reviewer`, `/code-review` or any agent body, since GH-1408 leaves narrowing undecided (G1).
- The instrumentation follow-ups: round start and end times in `review-round-cost`, and a reviewer or angle field on ledger rows. They need a separate plan (Context).
- Removing docstrings outright, which GH-1408 itself puts out of scope (G1).
- A format-reverted third arm, restoring comments a review rewrote only to clear a format finding. It needs a reviewer or angle field on ledger rows and its own pre-registration (row 2).
- A `docs/design-decisions/` entry. No decision exists until a run reports.
- Measuring comments' value to human readers or the cost of review rounds. The page names both as limits, and neither is a question about authoring correctness.
- Tasks from other repositories, including stow consumers' codebases. The engineer selected this repository's merged PRs (row 5), and other codebases carry provenance limits (G2).
