# A — Measure review quality (A-bench)

## Context

Goal: build A-bench, a local-only replay harness under `evals/`, that measures whether a reviewer read-rule change preserves review quality — recall and adjudicated precision on a curated set of this repo's own known-defect PRs — before that change (child issue #1115, "B") is allowed to merge.

This matters now because epic #1113 wants to replace nine reviewer agents' "read every changed file fully" instruction with a capacity-aware rule, but today nothing in this repo measures the defects a review misses, so that change would ship with no quality gate. #1114 ("A — Measure review quality") is epic #1113's designated fix for that gap; the epic's own governing plan (`.claude/plans/code-file-size-splits.md`) explicitly defers implementing A to A's own `/plan-it`.

Coordination already resolved two scope questions before this plan was written: (1) A-field (extracting `read-scope` and adding reviewer-read telemetry) is owned by a sibling session working issue #1116, so this plan covers A-bench only — A-field gets its own follow-on `/plan-it` once #1116's extraction merges; (2) since child B (#1115) hasn't started and has no draft rule to serve as A-bench's originally-specified third arm, epic #1113 and #1115 were amended (already committed) so that A-bench now delivers a frozen 2-arm baseline (today's rule vs. a function-context-only comparator) plus a frozen set of run conditions, and #1115 itself later runs its draft rule as a third arm against that frozen instrument, as its own acceptance criterion.

The intended outcome of this plan is a working, tested A-bench harness under `evals/`, a committed and engineer-confirmed known-defect set, frozen run conditions with a written invalidation rule, and a recorded 2-arm baseline result answering: can this curated defect set even distinguish today's rule from a low-context comparator (assay sensitivity), and what are recall, adjudicated precision, and Read-token cost per arm?

## Approach

A-bench replays each confirmed known defect through the one reviewer lens that owns that defect class. Each replay runs 10 times per arm in a synthetic two-commit repository, and the reviewer's agent body is the only thing that differs between arms. A blind Opus judge labels whether each run found the defect and whether each finding is valid. A human spot-checks those labels. A paired, defect-level bootstrap then turns the labels into recall and precision per arm.

The baseline freezes two arms, today's rule and function context for every file, together with every run condition. It also records whether the defect set can tell those two arms apart. #1115 later adds its draft rule as a third arm under the same frozen conditions. That arm must show non-inferiority at a 5-percentage-point margin on both recall and adjudicated precision.

The work ships as three PRs:
1. Tooling.
2. The frozen defect set and run conditions.
3. The baseline result.

### Defect set

Two miners write ranked candidate shortlists to the gitignored `evals/review_bench/.local/`. The engineer confirms or rejects each candidate (row 2). Only confirmed records enter the committed `evals/review_bench/defects.json`, and each one is described from public git history.

The miner pre-fills four inclusion fields and the engineer rules on each:
- the defect's lines exist at the introducing head;
- a reviewer of that diff could have caught it from the repository as it stood then;
- the file is not markdown, since B's scope excludes markdown instruction files;
- one owning lens, which is the primary owner of the defect's checklist item in `code-review/SKILL.md`'s Item-ownership table (row 13).

**Source 1: SZZ-style blame (durable).** `mine_szz.py`:
1. Takes first-parent `origin/main` commits whose subject matches `fix|bug|regression`, case-insensitive.
2. For each fix commit, reads its `-U0` diff against the first parent. It keeps removed or modified lines in non-markdown files and drops blank and comment-only lines (row 22).
3. Runs `git blame -w -M -C --porcelain <fix>^ -L <range> -- <path>` on those lines to find the introducing commits. `-w` ignores whitespace-only changes. `-M -C` follow moved or copied lines, so this repo's own line-range decomposition moves are not blamed as introductions (row 22).
4. For a hunk that only adds lines, blames the hunk's adjacent context lines instead. SZZ cannot attribute an omission (row 22), so these candidates are flagged low-confidence.
5. Maps the introducing commit to its PR through the `(#N)` subject suffix (row 20). The fixture base is the introducing commit's first parent, and the fixture head is the introducing commit.
6. Ranks candidates in this order:
   - modified-line hits before adjacent-line hits;
   - a single introducer before several;
   - files over one Read call first (G3). Those files are where B's rule changes behavior, so enriching them makes the benchmark sensitive where it matters.

   Age is deliberately not a ranking key. The pool may already fall short of N_min (row 25), and weighting toward recent fixes would trade defects for a correction to a bias nobody can measure (row 28). Each candidate instead records its fix commit's date, and the analysis reports recall by fix-date half as a secondary column.

**Source 2: later review round (closing window, run first).** `mine_review_rounds.py` must run before anything else, because transcripts age out (G1):
1. **Sessions.** `scope._iter_scoped_sessions(scope._repo_scoped_project_slugs(...), include_subagents=False, roots=None)` supplies them: the active account's projects directory alone, filtered to this repo's worktrees by identity (row 16). That gives a single root by construction.
2. **Round windows.** `review_rounds._detect_round_windows` finds the windows in each session's main-thread records, and `_session_record_branches` gives each window's branch (row 14). Windows are grouped by branch across sessions and ordered by timestamp.
3. **Round scope (new logic).** A round's scope is the set of join keys for every Read `file_path` issued inside its window. That covers Reads by the main thread and Reads by any reviewer-typed subagent (`reviewer_yield._is_reviewer_subagent_type`) dispatched inside the window. Subagent transcripts are resolved through `corpus._index_subagent_dispatches`. Keys come from `reviewer_yield._normalize_cited_path`. It is lexical, strips the worktree prefix, and yields the same key for an absolute Read path and a cited path (row 15). Scope is file-level: today's rule makes reviewers read every changed file, so a round's Reads approximate its diff (row 11).
4. **Later-round citations.** `_extract_cited_paths` runs over each later-round reviewer's final text and Write blobs (`_scan_reviewer_transcripts`), and the results are normalized the same way.
5. **Candidates.** A candidate is a later-round citation whose key is in an earlier round's scope on the same branch. Its evidence fields are:
   - the round timestamps;
   - whether the main thread edited that path between the two rounds, which hints the cited code is new rather than missed;
   - the finding excerpt, kept local only.
6. **Fixture.** When the PR number is known (row 20), the miner fetches the PR head explicitly with `git fetch --no-tags origin refs/pull/<N>/head:refs/review-bench/pr/<N>`. This repo fetches only `refs/heads/*` by default (row 38). The named destination ref keeps the resolved SHA independent of `FETCH_HEAD`. A kept local branch is used instead when one exists (row 27). Each candidate records `ref_status` as `local-branch`, `fetched`, `fetch-failed`, or `pr-unknown`, so a ref that cannot be fetched is never mistaken for missing history. The miner then lists the PR's branch commits that touch the path, with their times relative to the rounds, and runs source 1's blame helper on the commit that fixed the finding. The engineer picks the head commit that contains the defect. The base is the PR's merge-base.

### Fixtures and arms

**Fixture.** For each confirmed defect, and separately for each arm, `fixture_repo.py` builds a fresh repository with two commits:
- the `git archive` tree of `base_commit`;
- then the tree of `head_commit`, using the introducing commit's subject and a fixed author and date.

It also writes a `.bench/` directory, listed in `.git/info/exclude`:
- `change.diff`: `git diff HEAD~1 HEAD`;
- `change-function-context.diff`: `git diff -W HEAD~1 HEAD` (row 30);
- `changed-files.tsv`: for each changed file, its path, line count, estimated tokens (characters ÷ 4, row 18), and an over-read-cap flag (estimated tokens above 25,000, G3).

This fixture design costs three things, all accepted:
- The reviewer sees no earlier history.
- The fixture's own historical project `.claude/` config loads as it did at the time.
- The model may already have seen this public repo's later fixes (row 28). Pairing within each defect puts that bias on both arms equally.

**Arm.** An arm is a directory holding one `bench-<lens>.md` agent file per lens that has a read clause (row 11). The harness installs it into the fixture's `.claude/agents/`, where it is excluded from git.
- **Arm 1, `current-rule`.** The production agent bodies at the freeze commit, copied verbatim with three frontmatter changes:
  - `name: bench-<lens>`;
  - `model: inherit`, so that `--model <frozen reviewer ID>` governs (row 29). If the CLI spike (Verification gate 3) shows that `inherit` does not resolve but a full model ID does, the arm pins the frozen ID itself;
  - `tools: Read, Grep, Glob`: production's list with `Bash` and `Write` removed (row 11). `snapshot_arm` fails loudly unless production's list holds all three read tools, so an arm never gains a tool its lens lacks.

  `effort`, the description, and the body stay unchanged.

  Removing `Bash` and `Write` at snapshot time takes containment out of the hands of ambient CLI behavior. A `tools:` allowlist withholds a tool from the subagent outright (row 32). The default headless mode only denies a call at runtime, and the stowed user settings pre-approve 25 Bash commands it would let through (row 33). Neither tool bears on what the arms compare. Every lens uses Write only for a `findings_path` file, which the review prompt never supplies (row 11). Both arms lose Bash equally, so the arm-vs-arm difference is unchanged. Without Bash, a reviewer cannot read a file whole through `cat` or `git show`. Arm 2's rule therefore cannot be bypassed that way, and the adherence diagnostic sees every whole-file read.
- **Arm 2, `function-context`.** Arm 1 with each lens's read clause replaced by the sentence below. Any other duty in the same sentence, such as sdet's "AND the code they test", stays after it. The replacement text:

  "Read the change through its function-context diff (`.bench/change-function-context.diff`), not by reading changed files whole; for any other context you need, locate it with Grep and read only that range. This read rule overrides any general instruction to read a whole file when reviewing it."

  Its last sentence neutralizes `claude/.claude/CLAUDE.md:65`, which reviewer subagents load (row 12).
- **Later arms.** A later arm is built by applying its read-rule change to the frozen arm 1 snapshot, allowlist included. It is never built by copying live agent bodies, because those drift after the freeze.

**Review prompt.** Every arm gets the same review prompt:

"Review the change from HEAD~1 to HEAD in this repository. Its commit subject is: {subject}. The diff is at `.bench/change.diff`, a function-context version is at `.bench/change-function-context.diff`, and each changed file's line count is in `.bench/changed-files.tsv`. Report every finding with its file:line, in your inline output format."

The prompt names no concern. Production dispatches do name one (row 13), but naming it here would leak the defect.

**Dispatcher.** A thin dispatcher session (`claude -p --model <frozen model ID> --session-id <uuid> --max-budget-usd <cap> --output-format stream-json`) receives this prompt. `{agent}` is `bench-<lens>` for a reviewer run and a judge agent's name for a judge run:

"Use the Agent tool once to dispatch the `{agent}` agent with exactly the prompt between the markers below, unchanged. Do not read files or do anything else. When it returns, output its result verbatim."

The review prompt, or the judge prompt, follows between `<<<` and `>>>` markers. As a main session, the dispatcher holds the CLI's default tools. What contains it is the validity check that its only tool call is the one Agent dispatch.

**Permissions.** Runs use the default headless permission mode, with no `--permission-mode` flag, matching `run_skill_evals.py` (row 9). They never use `bypassPermissions`, because fixture trees contain scripts that write under `$HOME`. Containment does not rest on the permission mode. Every agent that does work, reviewer or judge, holds only read tools through its `tools:` allowlist (rows 32, 33). The per-run validity checks confirm on every run that no other tool was used. Reviewers therefore read the `.bench/` files, since they cannot run git.

### Runs and adjudication

**Terms.**
- A *run* is one reviewer session, and K is the number of runs per arm per defect.
- A *block* is every run for one defect, K per arm, in a seeded shuffled order.
- A *campaign* runs its blocks one at a time, and each block's runs go through a worker pool (default `run_skill_evals.DEFAULT_WORKERS`). `run` resumes an interrupted campaign: it skips complete blocks, sweeps what the abandoned attempt left of a partial block (Cleanup), and reruns that block whole.

Blocking by defect means that a harness or environment change during a campaign lands between defects, never between the two arms of one defect.

**Per-run validity checks.** These apply to every reviewer run and every judge run. A run fails if any of these does not hold:
- the dispatcher's Agent `prompt` equals the intended prompt byte for byte;
- the dispatcher's only tool call is one Agent dispatch, to the expected agent;
- the subagent transcript's observed `message.model` equals the frozen ID for that agent, reviewer or judge;
- every tool the subagent used is in its agent file's `tools:` list, read from `parse_subagent_dispatches`'s `observed_tools` (row 36);
- no Read, Grep, or Glob path resolves, through symlinks, into either of two places. One path-resolution function serves a Read's `file_path` and a Grep's or Glob's `path`.
  - The live checkout's copy of a file the defect's introducing or fix commit changed, or a directory containing one (row 42). The live checkouts are the harness's own and the one the ambient config resolves into.
  - The active config dir's `projects/` root, or a directory containing it, except the run's own session store (rows 46, 47).
- the dispatcher's final result event reports success, so a run the budget cap stops never counts as a review with no findings;
- the process finished within the timeout.

A failed run is retried once. A run that fails twice is recorded as missing, with a `missing_reason` that names the check its second attempt failed, such as `budget` or `timeout`.

**Out-of-session reads.** A run's own directories are its fixture or judge directory and its own session store. A Read, Grep, or Glob path outside them that neither failing class covers is recorded in `out_of_session_paths` and does not fail the run.
- A validity check runs after the tool call. Failing the run discards its output but cannot un-read a file, so failing suits validity leaks. Both failing classes are validity leaks: the live checkout holds the later fix (row 42), and `projects/` holds the later review rounds that Source 2 mines, which can carry a defect's own later finding. `projects/` also holds other projects' sessions.
- Credential-shaped paths are stopped before the read instead. The ambient Read gates deny them before execution (row 45), and the CLI spike confirms that they fire inside a bench subagent. Those gates sit under a Read matcher, so a Grep or Glob of a credential directory is recorded, not prevented. This plan accepts that residual rather than closing it: a reviewer of this repo's own credential-gate hooks could plausibly search such a directory, and the list review below is what would surface it.
- `smoke` and `analyze` print every recorded path, with its run count per arm, to the terminal only. The engineer reviews the smoke campaign's list before the go/no-go (gate 7) and the baseline campaign's before PR 3 (gate 9). Committed results carry only per-arm counts, because a path can name a private project.

**Environment record.** Each block records `claude --version` and the ambient config commit at its start and end, plus a dirty flag. The ambient config commit is the `HEAD` of the checkout that `<config-dir>/CLAUDE.md` resolves into. A block whose two readings differ is rerun whole, and the rerun's records replace the first attempt's rather than joining them.

`analyze` also checks the whole campaign: every record must carry one CLI version and one ambient config commit. A CLI update that lands between two blocks leaves each block consistent on its own, so the per-block check cannot see it. A campaign with more than one environment makes `analyze` exit 2, naming each environment and its blocks. The remedy is to rerun every block not recorded under the current environment. The baseline campaign's single environment is recorded in `results/baseline.json`, and the later-arm drift rule compares against it.

**Caps.**
- `--max-budget-usd` reuses `measure_subagent_model_resolution.PER_RUN_BUDGET_CAP_USD`, which is a measured staff-backend-engineer dispatch cost × 10 (row 9). The CLI spike's budget probe (gate 3) settles whether the flag bounds the subagent's spend or only the dispatcher's (row 39). If it bounds only the dispatcher, the timeout is each run's only spend bound, and the go/no-go (gate 7) says so.
- The reviewer wall-clock timeout is derived the same way: 10 × the p95 of this repo's own staff-reviewer dispatch durations. The constant's comment names the `--this-repo`-scoped command that measured it, so the cap is non-binding by design.
- The timeout's reference class ran with Bash available, and bench reviewers have none. No denial can stall a run, because the tool is absent rather than refused. A no-Bash run may still take longer, for example through arm 2's Greps and paged Reads.
  - The smoke campaign (gate 6) compares every run's wall-clock with the production p95. If any run exceeds it, the timeout is re-derived as 10 × the longest smoke run before the freeze.
  - Every timeout or budget stop is recorded as `missing_reason: timeout` or `budget` and reported per arm. A mis-sized cap therefore shows up as a count of missing runs, not as a shift in recall.
- Each judge gets its own budget cap and timeout: 10 × that judge's cost and duration on the smoke campaign's full-K fixture (gate 6). Their constants' comments name the smoke run. A judge's input holds all 2K of a defect's runs, so only a run at the K to be frozen sizes a baseline judge run.
  - Until that measurement exists, the judge constants hold bootstrap values equal to the reviewer cap and timeout, the only per-run bounds this repo has measured, and their comments say so. These bound the smoke campaign's own judge runs, the first one included.
  - A smoke judge run that ends at a bootstrap bound yields no measurement, so the smoke campaign does not pass. The engineer raises the bootstrap values and reruns it.
  - Writing the derived values into the harness changes the manifest, so the smoke campaign reruns under them before `freeze` (Freeze preconditions).

**Per-run statistics.** Each run records these from its subagent transcript:
- Read calls;
- Read tokens (result characters ÷ 4, row 18);
- Reads carrying a `PARTIAL view` notice, and paged follow-up Reads (G3);
- whole-file Reads of changed files, used for the adherence diagnostic.

**Cleanup.** Each block deletes its own temp fixtures and their session stores, and only after every run and retry in the block has finished.
- A run's session store is the one directory under the config dir's `projects/` root that holds `<session-id>.jsonl` for the run's own `--session-id`. The runner finds it by that ID. It does not compute it from the fixture path, because `compute_session_store_dir()`'s name rule is incomplete (row 47).
- `run_skill_evals` is the precedent for deleting a store, and its per-sample `finally` is safe because each sample has its own temp project (row 34). Here, K concurrent runs share one fixture per arm, so the block-end ordering is what keeps a cleanup from deleting the store of a run still in progress.
- Every fixture and judge directory comes from `_resolved_temp_project_dir` with a `review-bench-` prefix, so no two share a store. Judge directories are cleaned the same way after their run.

A hard interruption, such as a killed process or a machine crash, skips block-end cleanup, because it never reaches the runner's `finally` blocks (row 48). So `run` writes ahead to the local run store: each directory it creates, as soon as it exists, and each run's session ID, before that run launches. On resume, `run` deletes every directory and session store the abandoned attempt recorded for its partial block, then reruns the block. It deletes nothing it did not record. `smoke` and `judge` record and sweep the same way. Each of the three holds a lock file in the run store naming its PID and refuses to start while that PID is alive, so a sweep never deletes the directories of an attempt still running. The one residual is a directory created in the instant before its record is written. `evals/README.md` says how to find such directories by their `review-bench-` prefix.

**Judge runs.** Both judges run the way reviewers do. The thin dispatcher, under `--model <frozen judge ID>`, dispatches a project-scope judge agent with `model: inherit` and `effort: high` (row 43). The judge agent files, `bench-judge-recall.md` and `bench-judge-precision.md`, hold the judge prompts and rubric. The per-run validity checks and retry rule apply to judge runs, with the judge ID in place of the reviewer ID, under the judge caps (Caps). A judge run that fails twice leaves its defect's labels of that type missing.

**Normalization.** Before either judge or the spot-check sheet sees a run's findings, `adjudicate.py` replaces every `.bench/` artifact path in the text with one neutral token, `[bench file]`, identically for both arms. Arm 2's read clause names `.bench/change-function-context.diff`, so a finding that cites that file would otherwise hint at its arm. Stylistic differences that survive normalization, such as narrower line ranges, are an accepted residual.

**Recall judge.** One judge run per defect, dispatching `bench-judge-recall` with `tools: Read`. Its working directory is a fresh `_resolved_temp_project_dir` that holds only the judge agent file and `.bench/judge-recall.md`: no fixture tree and no arm file. The input goes in a file because a `claude -p` prompt passes as a single argv element (row 40). `judge-recall.md` holds:
- the confirmed description;
- the defect's lines;
- the fix diff;
- every run's normalized findings, under opaque IDs, in the order described under Blinding.

The judge returns FOUND or NOT_FOUND for each ID. For a FOUND, it also quotes the opening words of the matching finding verbatim. `adjudicate.py` accepts the answer only when three things hold:
- it labels every input ID exactly once;
- it names no other ID;
- each quoted opening occurs in that ID's normalized findings.

The parser tolerates formatting variation, following `run_skill_evals.parse_disposition_answer`'s pattern, but it never infers a missing or ambiguous label, such as "partially found". An answer that fails a condition is invalid. An invalid answer from either judge, including a precision answer that fails the split check, fails the judge run under the retry rule, with `missing_reason: invalid-answer`.

**Precision judge.** One judge run per defect, dispatching `bench-judge-precision` with `tools: Read, Grep, Glob`. Its working directory is an arm-neutral judge fixture. That fixture holds `fixture_repo.py`'s two-commit tree and `.bench/` diffs for the defect, the judge agent file, and `.bench/judge-precision.md`, and no `bench-<lens>` file. The judge never runs in an arm fixture, because `.claude/agents/bench-<lens>.md` there differs by arm and a Grep would reveal which arm it is. `judge-precision.md` lists every run's normalized findings under the same opaque IDs, in the order described under Blinding. For each run, the judge splits the output into distinct findings, quoting each finding's opening words verbatim. It labels each finding against this rubric:
- VALID: "the finding names a real problem in the code at HEAD that the change causes, activates, or newly reaches, stated specifically enough to act on" (row 13's `:36` contract);
- INVALID: otherwise.

`adjudicate.py` then looks for each quoted opening in the run's normalized output, in order, each one after the previous one. If an opening cannot be found in that order, the judge's answer is invalid. A made-up or double-counted finding therefore cannot enter the pooled-precision denominator. The judge splits rather than a code parser because the lenses' inline formats name fields per finding but have no machine delimiter, and the field lists differ by lens (row 11).

**Blinding.** Neither judge and not the human ever sees an arm name. Both judges and the spot-check sheet see runs only by opaque ID, in an order computed from the opaque IDs and a frozen seed alone. The ordering function takes no arm input, so a run's position carries no arm signal. The ID-to-arm mapping lives only in the local run store.

**Human spot-check.** This is a manual step. A seeded sample of 100 recall labels and 100 precision labels (all of them if fewer), stratified by the judge's label, is exported without arm or judge label. Each precision item shows its finding inside the whole normalized run output, with the judge's split marked. The human labels each item. For precision items, they also mark whether the marked span is exactly one finding. Cohen's κ is computed per label type.
- κ ≥ 0.61, "substantial" in Landis & Koch (row 23): the labels are validated.
- Below that: the result is recorded as "adjudication not validated" and cannot certify a later arm.

Split agreement is reported per arm and never gates. The precision gate rests instead on the argument row 28 makes for memorization, applied to the judge's segmentation. A merge of two findings or a split of one moves an arm's pooled precision, and moves of equal expected size in both arms cancel in precision_X − precision₁. That holds only if segmentation error is arm-independent, in both its rate and the labels of the findings it touches (row 44). Blinding stops the judge from treating an arm differently on purpose, but not from segmenting one arm's text worse, for example if arm 2 writes vaguer findings. Split agreement per arm is where a violation would show, so the report prints it beside the precision figures (gate 10). A floor on it, like κ's, was rejected: no floor that the spot-check's roughly 50 precision items per arm can certify is tight enough to bound the gated difference within δ (row 44).

### Analysis, margin, and minimum set size

**Recall.** The unit is the confirmed defect. A run is *completed* when the recall judge labeled it FOUND or NOT_FOUND. A missing run counts in neither the numerator nor the denominator. For each defect and arm, the detection rate is FOUND runs divided by completed runs. An arm's recall is the mean of those rates across defects. A defect with fewer than K/2 completed runs in either arm is dropped from both arms, and one with exactly K/2 is kept. The drop count, and the effective N it leaves, are reported against N_min.

**Intervals.** Every interval is a paired cluster bootstrap: 10,000 resamples of defects, each defect carrying both arms' runs, with a frozen seed and a percentile interval (row 23).

**Sensitivity verdict for the baseline.** This is ICH E10's assay sensitivity. M1 is the lower limit of the two-sided 95% interval for recall₁ − recall₂.
- If M1 > δ, the result is "sensitive".
- Otherwise the result is "not sensitive". A later arm cannot then be certified at δ, because the margin would exceed the effect of whole-file context that the set can reliably show.

The verdict is recorded as it comes out, and it is never grounds to revise the defect set (row 6).

**Non-inferiority for a later arm X.** Arm X passes when the lower limit of the two-sided 95% interval for recall_X − recall₁ exceeds −δ. That is one-sided α = 0.025, the ICH E9 convention (row 23).

**Precision non-inferiority for a later arm X.** Each arm's precision is pooled: VALID findings divided by all adjudicated findings, across every completed run of every defect the recall analysis keeps. Arm X passes when the lower limit of the two-sided 95% interval for precision_X − precision₁ exceeds −δ. That interval comes from the same paired cluster bootstrap as recall, and δ is the same. This rule replaces the governing plan's point-estimate rule (row 7). Arm X is certified only when both the recall gate and the precision gate pass. Because both must pass, each gate keeps one-sided α = 0.025 with no multiplicity adjustment (row 23).

A defect whose precision-judge run is missing is dropped from the precision analysis for both arms, and that count is reported.

**Secondary columns, never gating:**
- recall difference in the over-read-cap stratum;
- recall per arm in each fix-date half, split at the median fix date of the kept defects, as an observable proxy for memorization exposure (row 28);
- Read tokens per run;
- `PARTIAL view` reads and paged follow-ups;
- adherence: arm 2's whole-file Reads of changed files, and arm 1's coverage of changed files;
- missing runs per arm by `missing_reason`, and out-of-session read counts per arm (never the paths);
- split agreement per arm, from the human spot-check, which the report also prints beside the precision figures (row 44);
- the observed σ_d, reported for the record and never used to revise N_min or δ.

**The margin: δ = 5 percentage points, absolute, on both recall and precision.** Row 1 delegated the recall margin to this plan. Applying the same δ to precision is also this plan's choice (row 31). Rows 3 and 4 put quality first, so neither quality measure gets a looser margin than the other. No data exists yet to derive a separate precision margin. The minimum set size is sized on recall alone and uses the standard formula for non-inferiority on a paired mean difference (row 23):

N_min = ⌈(z₀.₉₇₅ + z₀.₈₀)² σ_d² / δ²⌉ = ⌈7.849 σ_d² / δ²⌉

The planning variance is σ_d²(K) = 0.30/K + 0.01 (row 24), which gives 0.04 at K = 10.

- **3pp needs N_min = 349 and is rejected.** A hand-confirmed set from one repository is unlikely to reach that (row 25). Below N_min the result is "inconclusive" by the governing rule, so 3pp would make the gate a standing block rather than a quality judgment. That block would also protect the current rule, which already fails silently on files larger than one Read call (G3). 3pp was the session's proposal, not the engineer's (row 5).
- **10pp needs N_min = 32 and is rejected.** It tolerates losing one known defect in ten. FDA frames a margin as the largest loss that is acceptable given the new treatment's other advantages (row 23). Here the only other advantage, token savings, is explicitly not the objective (rows 3, 4). So the margin should be the smallest the instrument can resolve. A 10pp margin is also likely at or above the whole effect of whole-file context, and ICH E10 rules out a margin larger than the effect being protected.
- **5pp needs N_min = 126 at K = 10.** It allows at most one known defect in twenty to be lost, and it is the smallest round margin whose N_min at 80% power is on the order of a hundred defects rather than several hundred.
- **K, not δ, is the pre-freeze lever.** N_min is 95 at K = 15 and 79 at K = 20. If the confirmed set is short, the engineer may raise K before the freeze. No arm output exists at that point, so this is still pre-specification. δ does not move.
- **A short set still gets a baseline.** If the confirmed set is below N_min at freeze, the baseline still runs, and its sensitivity result is still informative. A later arm's verdict is then capped at "inconclusive".
- **Precision power is unplanned.** N_min is sized on recall alone. Pooled precision's variance depends on how many findings each run produces, and nothing measures that yet. The baseline reports the arm 2 − arm 1 precision interval, so #1115 sees the precision gate's resolution before it runs arm 3.
- **Cost of the baseline:** 2 × N × K reviewer runs, which is 2,520 at N = 126, plus N recall-judge runs and N precision-judge runs (row 39).
  - At row 9's $1.15 per staff-reviewer dispatch, the reviewer runs cost about $2,900.
  - If the budget cap bounds subagent spend (gate 3), it bounds them at about $29,000, or about $58,000 if every run retried once.
  - Judge runs are uncosted until the smoke campaign's full-K fixture measures them.
  - Wall-clock: blocks run one at a time, with ⌈2K ÷ workers⌉ = 5 run-slots each at the default 4 workers. That is 630 slots at N = 126, so each minute of median run duration adds 10.5 hours.

  Verification gate 7 turns these into one figure for the engineer's go/no-go.

### Freeze and invalidation

The `freeze` subcommand writes `evals/review_bench/conditions.json`. It records:
- the reviewer model ID and the judge model ID, copied from the harness constants `REVIEWER_MODEL_ID` and `JUDGE_MODEL_ID` (row 29);
- K, δ, α, N_min, the planning variance, bootstrap resamples and seed, the campaign seed, the κ floor, the retry and missing-run rule, and the later-arm gate: non-inferiority at δ on recall and on pooled precision, with both required;
- the defect IDs;
- sha256 hashes of the harness closure, both arm directories, the judge agent files (which hold the judge prompts and rubric), `defects.json`, and the prompt templates;
- the environment at freeze: CLI version and ambient config commit;
- main's commit SHA, for reference only.

Content hashes stand in for the "harness commit" in the consult's list, because squash merge rewrites branch SHAs (row 20).

**Harness closure.** `freeze` computes the set of hashed files rather than reading a hand-kept list. The set is every first-party source file that is loaded once `run_review_bench.py`'s `run`, `judge`, and `analyze` paths are imported, limited to files inside the repository. The closure therefore covers the three files the runner reaches outside `evals/review_bench/`: `evals/measure_subagent_model_resolution.py`, `evals/run_skill_evals.py`, and `claude/.claude/scripts/_config_dir.py` (row 36). The miners import `transcript_analysis` only inside their own subcommands, so it stays out of the closure, for three reasons:
- those modules shape candidates, not runs;
- `defects.json`'s hash freezes their output;
- including them would invalidate the baseline for changes that cannot touch a run.

**Freeze preconditions.** `freeze` exits 2, naming the cause, unless all of these hold:
- the current manifest equals the one the last passing smoke campaign recorded, so the frozen harness is the harness the smoke campaign exercised. The model IDs and the caps are constants inside the closure, so this also means `freeze` records the model IDs the smoke campaign dispatched under and the caps it ran with;
- the K being frozen equals the K of the smoke campaign's full-K fixture, because the judge caps were sized at that K;
- every `defects.json` record passes the description provenance check against the `.local/` excerpts, which must be present (M12).

**Drift between PR 1 and PR 2 is accepted, not locked.** Mining and confirmation have no time bound, so an unrelated PR may change a closure file in that window. No measurement depends on the harness before the baseline campaign. The smoke-manifest precondition turns any such change into a re-smoke rather than a silent absorption.

`analyze` recomputes every hash. If any frozen field or hash differs, it refuses to compute a later arm's verdict, exits 2, and names the field: "invalidated — rerun all arms".

Environment drift triggers the same rule. If the CLI version or ambient config commit differs from the environment the baseline campaign recorded (Environment record), the later arm runs in one campaign together with fresh arm 1 and arm 2 blocks. This extends the consult's rule (row 6) to the environment, because the Read cap is harness behavior (G3) and `CLAUDE.md:65` reaches reviewers (row 12). In practice arm 3's campaign reruns all three arms, so #1115 should budget 3 × N × K reviewer runs. The canonical text of the invalidation rule lives in `evals/README.md`'s review-bench section.

### Delivery

- **PR 1: tooling, with no defect data and no runs.** It can be reviewed freely, because nothing is frozen yet.
- **PR 2:** `defects.json`, the two arm snapshots, and `conditions.json`. It is reviewed and merged before any baseline run, so reviewers can still challenge the defect set before any arm output exists.
- **PR 3:** the baseline result.

### Assumption ledger

**Root:** Nothing in this repo measures the defects a review misses, so the reviewer read-rule change (#1115) has no quality gate. This plan builds A-bench: a local replay of confirmed known defects through reviewer agents under controlled arms. It records a frozen 2-arm baseline whose run conditions #1115 reuses for its own arm.

**Givens:**
- G1. Transcripts age out on a rolling window (`cleanupPeriodDays`, default 30 days). Review-round candidates older than the window are unrecoverable, while git history is durable. Reason: the harness owns retention. `[verified: docs/pr-cost.md:3 via .claude/plans/code-file-size-splits.md G1; docs/cost-ledger.md:5-7 relayed by the dispatching session]`
- G2. A model ID pins weights and configuration, not serving infrastructure: "Anthropic does not update the weights or configuration of an existing model ID". Request routing, safety classifiers, and sampling logic can still change. Reason: the vendor owns serving. `[verified: platform.claude.com/docs/en/about-claude/models/model-ids-and-versions, fetched and quoted by the dispatching session; not re-fetched]`
- G3. A default Read returns at most 25,000 tokens and marks a truncated read with a `PARTIAL view` notice. Reason: the harness owns tool behavior. `[verified: .claude/plans/code-file-size-splits.md G3 and row 56]`
- G4. pytest's `prepend` import mode makes test basenames global, so the new tests take the `test_review_bench` prefix. Reason: pytest owns this behavior. `[verified: .claude/plans/code-file-size-splits.md G2]`
- G5. Every headless run is a full session on the owner's own Claude account, and cost scales with the number of runs. Reason: the vendor owns billing and usage limits. `[verified: evals/README.md:12-23]`

**Mechanisms:**
- M1. A controlled replay is the primary instrument. The agent body is the only varying input, paired within each defect. `anchors: root, row3, row4, row8, row15, row21, G1`
  - Rejected: reviewer-yield's verdict mix and cited-path edit overlap. They cannot see a defect no one reported. `anchors: row15`
  - Rejected: review-ledger dispositions. They have no PR linkage and no recall. `anchors: row21`
  - Rejected: an observational before/after window. Model, prompt, and PR-mix changes confound it, and retention bounds it. `anchors: G1`
- M2. The defect set comes from two miners plus per-candidate engineer confirmation, with one owning lens per defect. `anchors: row2, row5, row13, row17, G1`
  - Rejected: a multi-lens panel that reproduces `/code-review` routing. That routing is judgment over the Change-type table, not a deterministic table (row 13). A panel would also multiply runs without isolating the read rule any better. `anchors: row13, G5`
- M2a. The SZZ miner uses a git-blame loop through stdlib `subprocess`. `anchors: row20, row22`
  - Rejected: PyDriller's SZZ. It is a new third-party dependency for a loop git already provides. `anchors: row22`
  - Rejected: SZZ Unleashed. It needs a Java toolchain. `anchors: row22`
- M2b. The review-round miner combines file-level round scope with later-round citations and runs first. `anchors: row11, row14, row15, row16, row17, row27, G1`
  - Rejected: line-level scope from Edit tool calls. Mapping `old_string` edits to line numbers across rounds is unreliable, and engineer confirmation already filters false candidates. `anchors: row2`
- M3. Fixtures are synthetic two-commit repositories built from `git archive`. `anchors: row18, row19, row28, row30, G3`
  - Rejected: `git worktree add --detach` of the real repository. It shares the object store, so a reviewer's `git log --all` reaches the later fix. `anchors: row26`
  - Rejected: `pr-diff-against-base.sh`. It cannot diff a historical commit pair. `anchors: row19`
- M4. Arms are project-scope `bench-<lens>` agent files with a `Read, Grep, Glob` allowlist, dispatched by a thin dispatcher session. Arm 2 is arm 1 with a one-clause substitution, and later arms derive from the frozen arm 1. `anchors: row6, row11, row12, row13, row26, row29, row32, row33`
  - Rejected, lighter: one session carrying the body through `--system-prompt` or `--append-system-prompt`. The harness would have to re-implement `model`, `effort`, and `tools`, which is exactly the drift a paired comparison cannot absorb, and it is not the subagent context reviewers run in. `anchors: row11`
  - Rejected, lighter: a main-thread-as-agent CLI flag. Its existence is unverified, and a main-thread context differs from a subagent context. `anchors: row26`
  - Rejected: keeping production's `tools:` and relying on the default headless mode to deny Bash and Write. That denial is ambient CLI behavior, and stowed allow rules pre-approve Bash commands it would let through. `anchors: row26, row33`
  - Rejected, heavier: OS-level containment beneath the permission gate, such as a container or a separate user. Setting one up is an install the harness may not perform, and the allowlist already withholds every tool that executes or writes. `anchors: row32`
- M5. Campaigns run in defect blocks, one block at a time. Every reviewer and judge run gets per-run validity checks. Each block gets an environment check, and so does the whole campaign. Caps are derived by the repo's own measure-×-10 precedent, and judge caps start at the reviewer's values until the smoke campaign measures judges at full K. A resumed campaign sweeps what its abandoned attempt recorded, under a run-store lock. `anchors: row9, row26, row29, row34, row36, row39, row42, row45, row46, row47, row48, G2, G5`
  - Rejected: sizing judge caps from K = 1 smoke runs. A judge's input holds all 2K of a defect's runs, so at K = 10, 10 × a K = 1 measurement leaves roughly no headroom if judge cost scales with the runs it reads. `anchors: row39`
  - Rejected: failing a run after the fact for reading a credential-shaped path. The check runs after the read, so it cannot keep the content out of the model's context, and the ambient Read gates already act before it. `anchors: row45`
  - Rejected: leaving a hard-interrupted block's directories to manual cleanup. The engineer would pick targets by name glob, which can match a live attempt's directories, while the write-ahead record names exactly the abandoned ones. `anchors: row34, row48`
- M5a. A CLI spike runs before the arm and runner code is written. It confirms the allowlist against a pre-approved Bash command, with a positive control; `model: inherit` under both model IDs; the ambient Read gates inside a subagent; and the budget cap's scope. A smoke campaign with injected failures then drives each validity check through retry-then-missing against the real CLI, before any baseline run. `anchors: row26, row29, row32, row33, row39, row45`
  - Rejected: deferring every CLI check to the smoke campaign. The arm and runner design rests on the allowlist and on `model: inherit`, so a wrong assumption would surface only after dispatch 1b is built. `anchors: row26`
- M6. Adjudication uses blind Opus judges plus a blind human spot-check with a κ floor. Each judge is a project-scope agent with a read-only allowlist, dispatched like a reviewer. Both judges see runs only by opaque ID, in an order computed without arm input, and with `.bench/` paths normalized out of the findings text. `adjudicate.py` checks each judge's answer mechanically: the recall answer's completeness and quoted openings, and the precision split. The precision gate assumes arm-independent segmentation, which split agreement per arm checks. `anchors: row6, row7, row13, row23, row29, row32, row40, row43, row44`
  - Rejected as primary: path-and-line overlap for recall. A line hit does not show that the reviewer named the defect. `anchors: row15`
  - Rejected: human-only adjudication. It means 2,520 recall labels at N = 126. `anchors: row24`
  - Rejected: judges as unrestricted main sessions. The precision judge has a task reason to execute code, namely confirming a finding, and stowed allow rules pre-approve some Bash. `anchors: row33`
  - Rejected: judges as main sessions restricted by CLI tool flags. That adds a second containment mechanism beside the allowlist the spike already verifies, and this plan has not verified those flags' semantics. `anchors: row26, row32`
  - Rejected: splitting precision findings with a code parser. The lenses' inline formats have no machine delimiter and differ by lens, so the judge splits and `adjudicate.py` checks the split. `anchors: row11`
  - Rejected: a floor on split agreement like κ's. No floor the roughly 50 precision items per arm can certify bounds the gated precision difference within δ. `anchors: row44`
- M7. The analysis is a defect-level paired cluster bootstrap. The baseline gets an assay-sensitivity verdict on recall. A later arm gets non-inferiority verdicts at δ on recall and on pooled precision, and it is certified only when both pass. `analysis.py` and `adjudicate.py` use the standard library only (`random`, `statistics`, `math`), and `statistics.NormalDist` supplies the z-values. `anchors: row6, row7, row18, row23, row35, G3`
  - Rejected as primary: majority-vote binarization with Tango's paired-proportion test. It discards within-defect information and needed a larger N under the same planning assumptions. `anchors: row23, row24`
  - Rejected: the point-estimate precision rule. It fails a later arm with truly equal precision about half the time. `anchors: row7`
  - Rejected: numpy or scipy. Either is a new third-party dependency for arithmetic the standard library covers at this N. `anchors: row35`
- M8. δ = 5pp on both the recall gate and the precision gate, α = 0.025 one-sided, K = 10, and N_min = 126 sized on recall, with K as the only pre-freeze lever. `anchors: row1, row3, row4, row5, row7, row23, row24, row25, row31`
- M9. Freeze by a content-hash manifest over the harness's computed import closure, which holds the model IDs and caps as constants. `freeze` requires the manifest and the K that the last passing smoke campaign recorded. `analyze` enforces the invalidation rule, extended to environment drift within a block, across a campaign's blocks, and between campaigns. `anchors: row6, row12, row20, row29, row36, row39, G2, G3`
  - Rejected: a hand-kept list of hashed files. It misses a new import, and the runner already reaches three files outside `evals/review_bench/`. `anchors: row36`
  - Rejected: recording the closure's hashes when PR 1 merges. The state that matters is the one the smoke campaign exercised, and harness fixes made at the smoke gate change the closure after that merge anyway. `anchors: row6`
  - Rejected: a separate check that `freeze`'s model IDs equal the smoke campaign's. Pinning the IDs as constants inside the closure lets the existing manifest check cover them. `anchors: row29`
- M10. Three PRs: tooling, freeze, result. `anchors: row6`
- M11. Offline, deterministic tests, plus a select-tests rule verified by its own unit test. CI scope stays unchanged. `anchors: row9, row10, G4`
- M12. Transcript-derived material stays in the gitignored `.local/`. Committed artifacts carry only public-git content and fixture-run aggregates. `confirm` is the only writer of `defects.json`. Both `confirm` and `freeze` reject a description that shares a six-word run with any `.local/` finding excerpt, unless that run also appears in the defect's public git text. The `source` breakdown is published as an own-history count, beside the scope-fixed miner. `anchors: row17, row37, row41`
  - Rejected: a process instruction alone ("draft from public git only"). Nothing would check it, and a lifted phrase in `defects.json` is irreversible once merged. `anchors: row41`

**Rows:**

1. `[engineer-verified: "What does plan-architect suggest?"]` This answered the session's question on the recall non-inferiority margin. The tag covers one point: the margin choice is delegated. The value (M8) is this plan's.
2. `[engineer-verified: "Delegate initial mining to this session, I confirm each candidate"]` The tag covers two points: the session mines candidates, and the engineer confirms each. The confirmed set, not the miner's output, is the defect set.
3. `[engineer-verified: "first and foremost optimizing diffs agents look at to maximize quality and thoroughness of findings according to best practices, not to minimize tokens"]`
4. `[engineer-verified: "the blanket rule of reading the entire file with the change was a signal that we should reevaluate the methodology, not a sign to put token optimization at the top of the list. Quality of code an repo over all."]`
5. `[unverified]` These were the session's proposals, not the engineer's words: "3pp (Recommended)", with its quality-first rationale, and the "Recommended" label on the mining option. This plan does not adopt 3pp (M8).
6. `[unverified — relayed by the dispatching session from the amended #1113/#1115 text and a prior consult; not read by plan-architect]`
   - A delivers a frozen 2-arm baseline plus the frozen run conditions.
   - #1115 runs arm 3 against them as its own acceptance criterion.
   - The consult's freeze list: pinned model snapshot ID, K, the defect set, the harness commit, the arm 1–2 prompts, the margin, and the minimum defect-set size.
   - If any of these changes before arm 3 runs, all three arms rerun.

   M9 adds the judge model, the prompts, the analysis parameters, and the environment to that list.
7. `[engineer-verified: "Non-inferiority on pooled precision (Recommended by plan-architect)"]` This answered the session's question on which rule gates a later arm's adjudicated precision against arm 1. The tag covers two points: precision is pooled across findings, and a non-inferiority test gates it. The selected rule replaces the governing plan's point-estimate wording, "its adjudicated precision is not lower". `[verified: .claude/plans/code-file-size-splits.md:163]` The same plan's "non-inferior to the current rule on recall and precision" already matches the selected rule. `[verified: .claude/plans/code-file-size-splits.md:383]` The point-estimate rule fails a later arm whose true precision equals arm 1's about half the time, because the estimated difference is then centered on zero and roughly symmetric. `[unverified — derived, not measured]` Whether amended #1115 still carries the point-estimate wording is `[unverified]`. If it does, #1115's text needs the same kind of amendment row 6 describes, because arm 3 is judged by this plan's frozen gate.
8. `evals/` is the never-CI home for `claude -p` measurements, because of cost and single-sample flakiness. `[verified: evals/README.md:10-34]`
9. Existing harness facts. `[verified: run_skill_evals.py:62-64 and grep of its flags; measure_subagent_model_resolution.py:15-24, :53-62, :205-219, def listing at :231, :417, :421, :559, :613, :631; test_measure_subagent_model_resolution.py:374, :401]`
   - `run_skill_evals.py` sets `SAMPLE_TIMEOUT_S = 90`, `DEFAULT_SAMPLES = 10`, and `DEFAULT_WORKERS = 4`, and launches with no `--permission-mode`.
   - `measure_subagent_model_resolution.py` reuses its launch shape, `SAMPLE_TIMEOUT_S`, `config_dir()`, and `compute_session_store_dir()`.
   - That harness derives `PER_RUN_BUDGET_CAP_USD` as a measured staff-backend-engineer dispatch cost × 10, and launches with `--session-id`, `--max-budget-usd`, and `--agents`.
   - It provides `_run_claude_to_completion`, `_wait_for_subagent_sidecar`, `subagent_dir_for_session`, `parse_subagent_dispatches`, `_resolved_temp_project_dir`, and `_frontmatter_block`.
   - Its tests fake the launcher.
10. Test-selection facts. `[verified: .github/workflows/tests.yml:165; pyproject.toml:18; select-tests.py:48, :250, :285-294, :546-562, :648-653]`
    - CI runs pytest over `claude/.claude/ claude-skills/ plugins/` only.
    - `pyproject.toml`'s pythonpath includes `evals` and `claude/.claude/scripts`.
    - select-tests maps only `evals/run_skill_evals.py`. Any other `evals/` path is unmatched and falls to a full suite that excludes `evals/`.
    - Glob targets expand.
11. Reviewer read clauses and frontmatter. `[verified: grep of claude/.claude/agents/; line 6 of each lens file; a grep for "findings_path` is absent" matches all seven lenses; staff-backend-engineer.md:9, :100-144, staff-sdet.md:93-103, and comment-discipline-reviewer.md:129-139 read this session]`
    - `staff-backend-engineer.md:63`: "Read every changed file fully."
    - `ciso-reviewer.md:51` and `comment-discipline-reviewer.md:106` carry the same clause.
    - `staff-platform-engineer.md:63`: "every changed pipeline/IaC/script file fully".
    - `staff-sdet.md:55`: "changed tests fully AND the code they test".
    - `staff-frontend-engineer.md:57` and `staff-analytics-engineer.md:80` carry variants.
    - All seven declare `model: sonnet`, an alias.
    - Effort is `xhigh`, except comment-discipline at `medium`.
    - Six lenses declare `tools: Read, Grep, Glob, Bash, Write`. comment-discipline declares `Read, Grep, Glob, Write`.
    - Each lens switches to its Inline output format when no `findings_path` is given. staff-backend-engineer's body states that its only write into the tree is the `findings_path` file (`:9`).
    - The three Inline output formats read name fields per finding, with no machine delimiter, and their field lists differ.
12. `claude/.claude/CLAUDE.md:65` says to read a file whole when "reviewing it", and subagents load CLAUDE.md. `[verified: grep; plan-it/SKILL.md:56]`
13. `/code-review` facts. `[verified: code-review/SKILL.md:272-300, :421-423; :36 via .claude/plans/code-file-size-splits.md row 9]`
    - Routing is judgment over the Change-type table.
    - The Item-ownership table names a primary owner per checklist item.
    - Real dispatches name a specific concern (`:300`).
    - The in-scope contract is "causes, activates, or newly reaches".
14. `review_rounds` gives main-thread round windows and per-record branches but no per-round scope. `[verified: review_rounds.py:83-181]`
15. `reviewer_yield` facts. `[verified: reviewer_yield.py:93-187, :242-331]`
    - `_scan_reviewer_transcripts` and `_extract_cited_paths` extract reviewer citations.
    - `_normalize_cited_path` is lexical, strips the worktree prefix to fixpoint, and returns a sha256 key for absolute and relative inputs alike.
    - `_is_reviewer_subagent_type` classifies reviewer dispatches.
    - The command cannot see an unreported defect. `[verified: .claude/plans/code-file-size-splits.md Context and row 17]`
16. Corpus and scope helpers. `[verified: corpus.py:15-62; scope.py:85-106, :240-265]`
    - `corpus._index_subagent_dispatches` maps a dispatch's tool_use ID to its subagent transcript.
    - `scope._repo_scoped_project_slugs` returns exact worktree slugs from `git worktree list` and fails closed.
    - `scope._iter_scoped_sessions(roots=None)` reads the active config dir alone.
    - Sessions from removed worktrees fall outside this scope.
17. A transcript-derived figure is publishable only beside a command that refuses a wider corpus than this repo on one account. `[verified: docs/private-project-redaction.md:100-123]`
18. read-scope estimates tokens as characters ÷ 4 (`_READ_SCOPE_CHARS_PER_TOKEN = 4`). `[verified: transcript-analysis.py:3356]`
19. `pr-diff-against-base.sh` resolves its base via `gh pr view` or origin and diffs `merge-base...HEAD`. Its output must stay byte-identical to `git diff`, and it takes no commit-pair argument. `[verified: pr-diff-against-base.sh:1-92]`
20. `main` is squash-merged with a `(#N)` subject suffix. `[verified: gitStatus snapshot, five of five recent commits]` For older history this is `[unverified]`, and the miner records the PR as unknown when the suffix is absent.
21. Review-ledger files are keyed per (repo, session). They carry no branch or PR field and are swept after 30 days, so they have no PR linkage. `[verified: review-ledger.sh, read by the dispatching session; not reopened]` This resolves the storage and retention part of `.claude/plans/code-file-size-splits.md` row 52.
22. `[verified: git-blame(1) man page, git 2.43.0; Śliwerski, Zimmermann & Zeller, "When Do Changes Induce Fixes?", MSR 2005, https://dl.acm.org/doi/10.1145/1082983.1083147; Kim, Zimmermann, Pan & Whitehead, "Automatic Identification of Bug-Introducing Changes", ASE 2006, https://dl.acm.org/doi/10.1109/ASE.2006.23]`
    - git-blame(1), `git --version` 2.43.0: `-w` "Ignore[s] whitespace when comparing the parent's version and the child's to find where the lines came from." `-M[<num>]` "Detect[s] moved or copied lines within a file." `-C[<num>]`, "[i]n addition to -M, detect[s] lines moved or copied from other files that were modified in the same commit" — repeating the flag widens the search to the file's creating commit, then to any commit. `-p, --porcelain`: "Show in a format designed for machine consumption."
    - Śliwerski et al. 2005 §4 locates the lines a fix touches in the pre-fix revision, then blames (CVS `annotate`, git's `blame` analog) those lines to find the introducing revision: "We scan the output and take for each line l ∈ L the revision r0 that annotates line l. These revisions are candidates for fix-inducing changes." This is the origin of fix-commit blame.
    - Kim et al. 2006 §4.2–4.3 give the ignore-rule this plan's miner follows: "To remove such false positives, we ignore blank lines and comment changes in the bug-fix hunks," and a source-format change "should be ignored when we identify bug-introducing changes."
    - SZZ's inability to attribute an addition-only fix is not stated verbatim in either paper. It follows from Śliwerski et al. 2005's own mechanism — blame covers only lines present in the pre-fix revision, so a hunk that only adds lines touches none — and is named directly in later literature: Rezk, Kamei & McIntosh, "The Ghost Commit Problem When Identifying Fix-Inducing Changes: An Empirical Study of Apache Projects," IEEE Transactions on Software Engineering, 2021, define this as "Mapping Ghost type 1," where "bug-fixing commit[s]... do not remove any lines and only add[] lines" (quoted via a later survey's restatement, Lyu, Kang, Widyasari, Lawall & Lo, "Evaluating SZZ Implementations: An Empirical Study on the Linux Kernel," arXiv:2308.05060, which renames the case "Remove Mapping Ghost"). `[derived from Śliwerski et al. 2005's mechanism, named in Rezk et al. 2021 (peer-reviewed) rather than stated in either paper row 22 originally cited]`

    `.claude/plans/code-file-size-splits.md` row 54 is now resolved: git-blame(1) semantics and both cited papers' claims are confirmed against primary sources.
23. `[unverified — recalled, not fetched; verify-sources quotes or drops each]`
    - ICH E9 (1998): a two-sided 95% interval, one-sided α = 0.025, for non-inferiority.
    - ICH E10 (2000): assay sensitivity, and a margin no larger than the effect the control reliably shows.
    - FDA, "Non-Inferiority Clinical Trials to Establish Effectiveness" (2016): the M1/M2 framing of the margin as the largest acceptable loss.
    - Chow, Shao & Wang, *Sample Size Calculations in Clinical Research*: the paired-mean non-inferiority sample-size formula.
    - Efron & Tibshirani (1993): percentile bootstrap.
    - Davison & Hinkley (1997): resampling clustered data at the top level.
    - Landis & Koch (1977): κ 0.61–0.80 means "substantial".
    - Tango (1998): the paired-proportion non-inferiority test.
    - Berger (1982), "Multiparameter hypothesis testing and acceptance sampling", *Technometrics*: an intersection-union test, which passes only when every component test passes at level α, has overall level α.
24. `[unverified — planning assumption]` σ_d²(K) = 2v/K + τ², with mean per-defect detection variance v = p(1−p) ≈ 0.15 and between-defect true-difference variance τ² = 0.01. The baseline reports the observed σ_d but never revises N_min or δ from it.
25. `[unverified — the first miner run answers it]` The number of candidates the engineer can confirm from this repo's history, and whether it reaches N_min.
26. `[unverified — the CLI spike (gate 3) checks the first three before dispatch 1b, the smoke campaign (gate 6) rechecks them, and the per-run validity checks recheck every observable one on every run]` Claude Code CLI behaviors the design depends on:
    - `claude -p` loads `<cwd>/.claude/agents/*.md` and applies their frontmatter, including `tools` as an allowlist (row 32) and `effort`. No transcript field is known to show `effort`, so that part stays unverified. It is identical in both arms.
    - `model: inherit` resolves to the session's `--model`.
    - Default headless mode lets the dispatcher call Agent, and lets a subagent call its allowlisted tools, without a prompt that would hang the run.
    - A subagent's Read, Grep, or Glob may or may not reach a path outside its session directory, and the spike records which. The runner does not depend on the answer: it records such paths and fails a run only for a read of a live copy (row 42).
27. `[unverified — the first mine-rounds run answers it, through its ref_status counts]` Pre-squash branch commits are reachable through GitHub's `refs/pull/<N>/head`, or through a kept local branch. Reaching `refs/pull/<N>/head` needs an explicit fetch (row 38).
28. `[unverified — not measurable]` The reviewer model may have trained on this public repo's later fixes. Pairing within each defect cancels a shared memorization boost in every arm-vs-arm difference the gates use. It does not protect the absolute per-arm figures, so PR 3 reports them with that caveat, and with recall by fix-date half as an observable proxy.
29. The reviewer model ID `claude-sonnet-5` is a pinned snapshot. `[verified: models page, relayed by the dispatching session]` The judge's Opus model ID is `[unverified]` until the CLI spike (gate 3) reads it from that page and confirms it resolves. Dispatch 1b pins both IDs as harness constants, so the freeze manifest covers them (M9).
30. `git diff -W`: "Show whole function as context lines for each change". `[verified: .claude/plans/code-file-size-splits.md row 10]`
31. `[unverified]` The option description shown beside row 7's label was plan-architect's prose written for the question, not the engineer's words. That description applied recall's δ and bootstrap to precision. Row 7's tag therefore does not cover those parameters. This plan adopts them as its own choice (M7, M8).
32. An agent's `tools:` field is a restrictive allowlist: "Tools the subagent can use. Inherits all tools if omitted." `[verified: claude-skills/skills/agent-review/REFERENCES.md:12, quoting the Claude Code subagent docs; claude-skills/skills/agent-review/SKILL.md:27]` An arm therefore lists its tools rather than omitting the field. Whether `claude -p` honors the field for a project-scope agent is row 26.
33. The ambient user settings that every run loads pre-approve 25 exact-match Bash commands in `permissions.allow`, including `~/.claude/scripts/marker.sh write code-review` and `~/.claude/scripts/marker.sh status`. The `status` subcommand writes nothing: its own comments call it "a report, not a write". Project settings can add more: the current `.claude/settings.json` allows four, and each fixture loads its own historical copy. `[verified: claude/.claude/settings.json:3-29, :4, :20; claude/.claude/scripts/marker.sh:719-808, :767, :790; .claude/settings.json:9-14]` That an allow rule also pre-approves its command inside a headless subagent is `[unverified — inferred from what an allow rule is for; the CLI spike's control agent (gate 3) settles it]`.
34. Session-store and cleanup facts. `[verified: run_skill_evals.py:642-654, :1405-1418; evals/README.md:269-272; measure_subagent_model_resolution.py:631-639, :676]`
    - `compute_session_store_dir` maps a project path to `<config_dir>/projects/<path with "/" replaced by "-">`. Row 47 shows that rule is incomplete.
    - `run_skill_evals` gives each sample its own temp project and removes that project's session store in the sample's own `finally` block.
    - `_resolved_temp_project_dir` wraps `mkdtemp`, so every fixture path is unique. It takes a caller-chosen name prefix, as `:676` does with `"subagent-model-resolution-"`.
35. `requirements-dev.txt` declares pytest, pytest-xdist, ruff, pyyaml, and shellcheck-py, and no numerical library. `[verified: requirements-dev.txt:1-5]`
36. Cross-module import facts. `[verified: run_skill_evals.py:55; glob for _config_dir.py; measure_subagent_model_resolution.py:15-24, :268-275, :344-353, :421]`
    - `run_skill_evals.py` re-exports `config_dir` from `claude/.claude/scripts/_config_dir.py`.
    - `measure_subagent_model_resolution.py`'s docstring records its own upstream reuse under "Reuses from evals/run_skill_evals.py rather than re-deriving".
    - `parse_subagent_dispatches` returns each dispatch's `observed_tools` and `observed_model_ids`, and `agent_frontmatter_tools` parses a `tools:` line.
37. The redaction class of the `source` breakdown. `[verified: docs/private-project-redaction.md:100-141, :242-263, :282-297]`
    - `docs/private-project-redaction.md` § "Own-history counts were never inside this class" places "A count whose scope holds no private-engagement record anywhere — this repo's own history" outside the wider-corpus bar.
    - § "New figures against the grandfathered set" still requires a one-time composition check for every new figure.
    - § "This repository, one account" accepts as a publication instrument a command that "always resolves to a single hardcoded root regardless of any flag".

    Reading the `source` breakdown as such a count, and `mine-rounds` as such an instrument, is this plan's classification. It stays `[unverified]` until the composition check at freeze (gate 8).
38. This repo's fetch refspec is `+refs/heads/*:refs/remotes/origin/*` only. `[verified: .git/config, as read by the staff-backend-engineer plan reviewer; not reopened]`
39. Baseline cost arithmetic from row 9's figures. `[unverified — derived]`
    - Reviewer runs: 2 × 126 × 10 = 2,520 runs × $1.15 ≈ $2,900.
    - Ceiling: 2,520 × $11.50 ≈ $29,000, or ≈ $58,000 if every run retried once at the cap.
    - $1.15 is staff-backend-engineer's production mean, so other lenses and arm 2 may cost more or less.
    - Whether `--max-budget-usd` bounds a subagent's spend or only the dispatcher's is unknown until the CLI spike's budget probe (gate 3).
    - No judge run has been measured. The smoke campaign's full-K fixture measures both judges at the K to be frozen, and adds 2K reviewer runs, 20 at K = 10, to each smoke pass.
    - Wall-clock: ⌈2 × 10 ÷ 4⌉ = 5 run-slots per block, so 630 slots at N = 126. Each minute of median run duration adds 10.5 hours.
40. A `claude -p` prompt passes as a single argv element, and the evals README keeps prompt content in the low single-digit KB for that reason. `[verified: evals/README.md:350-352]`
41. `[unverified — this plan's choice]` The provenance check's unit is a shared six-word run.
    - It targets lifted phrasing. That is the leak a cooperative drafting session makes, as opposed to deliberate paraphrase (repo-root `CLAUDE.md` § "Hook threat model").
    - Subtracting the defect's public git text keeps shared identifiers and code lines from tripping the check.
    - A false positive costs a rewording, while a false negative costs an irreversible public leak, so the unit errs short.
42. `~/.claude/` entries are stow symlinks into a live checkout of this repo, and that checkout holds code after later fixes. Reviewer bodies direct reads there, such as `~/.claude/skills/error-handling/SKILL.md`. `[verified: repo-root CLAUDE.md, "Changes under claude/.claude/** go live on git pull"; staff-backend-engineer.md:93]`
43. `effort: high` fits work "especially when a separate downstream pass already backstops it". For the judges, that pass is the human spot-check. `[verified: claude/.claude/CLAUDE.md, Model & Effort Routing, effort bullet]`
44. `[unverified — this plan's assumption; the arithmetic is derived, not measured]` The precision judge's segmentation error is arm-independent: both arms see the same rate of merges and over-splits, with the same labels among the findings they touch.
    - One segmentation error moves an arm's pooled precision by at most about 2/T, where T is that arm's adjudicated finding count. The extremes are two VALID findings merged into one INVALID unit, and one INVALID finding split into two VALID units.
    - Error rates e_X and e₁ per finding can therefore move the gated difference by up to 2(e_X + e₁). Holding that under δ = 5pp needs each rate below about 1.25%.
    - The baseline spot-check holds about 50 precision items per arm. With no split error observed, 50 items bound the rate only near 6% at one-sided 95% confidence (1 − 0.05^(1/50) ≈ 0.058). Bounding it at 1.25% takes about 240 error-free items per arm (ln 0.05 ÷ ln 0.9875 ≈ 238).
    - The worst case needs every error to push the same way within an arm and opposite ways across arms, so the realistic shift is smaller. Nothing measures how much smaller before the baseline.
45. The ambient config stops a Read of a credential-shaped path before it executes. `[verified: claude/.claude/settings.json:41-56, :410-427; claude/.claude/hooks/deny-credential-file-reads.sh:4-7]`
    - `permissions.deny` lists Read rules for `.env` variants and `credentials.json`.
    - `PreToolUse` hooks under a `Read` matcher include `deny-env-reads.sh` and `deny-credential-file-reads.sh`. The latter covers SSH private keys, `.netrc`, `.git-credentials`, cloud credential stores, `.env` variants, and `credentials.json`, and its header scopes it to the Read tool.

    That these gates fire inside a headless bench subagent is `[unverified — the CLI spike (gate 3) checks it]`. Whether the `permissions.deny` Read rules also reach Grep or Glob is `[unverified]`, so this plan treats Grep and Glob as ungated.
46. A tool result too large to return inline is saved as a file in the session's store, under `<session-id>/tool-results/`, and the model gets a pointer to Read. `[verified: this plan-architect session's own oversized Grep result was saved under <config-dir>/projects/<slug>/<session-id>/tool-results/]` That a bench subagent's oversized results land in its own session store is `[unverified — inferred; the smoke campaign's recorded paths show it]`.
47. `run_skill_evals.compute_session_store_dir` names a store by replacing only `/` with `-` (`:653`). Claude Code also replaces `.`: a session whose working directory contains `/.claude/` gets a store name containing `--claude`. `mkdtemp`'s random names can contain `_`. `[verified: run_skill_evals.py:642-654; this plan-architect session's own store name; CPython 3.12 tempfile.py:279]` Whether Claude Code also replaces `_` is `[unverified]`. Finding a store by session ID makes the harness independent of the answer.
48. `[unverified — standard process semantics, not exercised]` A SIGKILL or a machine crash ends the runner without running its `finally` blocks, so no in-process cleanup covers a hard interruption.

## Critical files

**Phase 1, PR 1: tooling.** Three `code-writer` dispatches, run in sequence because each consumes the previous one's schema. Three manual gates sit between them, each before the dispatch that depends on its answer:
- verify-sources over row 22 before dispatch 1a (gate 1), because 1a's miner encodes git-blame semantics;
- the CLI spike before dispatch 1b (gate 3), because 1b's arms and runner rest on the allowlist and on `model: inherit`, and 1b pins the model IDs the spike exercised;
- verify-sources over row 23 before dispatch 1c (gate 4), because 1c writes the statistics and the README's method text.

**Dispatch 1a: defect schema, miners, confirmation.** Run the source-2 miner the moment this dispatch lands (G1).
- Create:
  - `evals/review_bench/__init__.py`
  - `evals/review_bench/defects.py`: `Candidate` and `ConfirmedDefect` records with JSON load/save and validation:
    - lens in the known lens set;
    - 40-hex SHAs for the base, head, and fix commits;
    - `source` in {`szz`, `review-round`};
    - an ISO `fix_date`;
    - no field outside the schema, so `description` is the only free text.

    `Candidate` also carries `ref_status`. The module also holds `check_description_provenance` (row 41):
    - it tokenizes text into lowercase `\w+` runs;
    - it rejects a description that contains any word 6-gram found in a `.local/` finding excerpt but not in the defect's public git text, which is `git show` of its introducing and fix commits;
    - it compares against every review-round excerpt in `.local/`, not only the candidate's own, because the drafting session saw the whole shortlist.
    - it returns the first offending six-word run and the ID of the candidate whose excerpt holds it, so `confirm` can name them.
  - `evals/review_bench/mine_szz.py`: stdlib `subprocess` only (M2a).
  - `evals/review_bench/mine_review_rounds.py`, including the explicit PR-head fetch and `ref_status` (Source 2, step 6).
  - `evals/run_review_bench.py`: the CLI, with `mine-szz`, `mine-rounds`, and `confirm` subcommands.
    - The miners write to `evals/review_bench/.local/` by default.
    - `mine-rounds` takes no scope flag and exits 2 if its sessions ever come from more than one config-dir root (row 37).
    - `confirm` reads engineer-approved entries from `.local/`, runs `check_description_provenance` on each, and appends only passing records to `defects.json`. It writes nothing for a rejected entry and has no override. A rejection prints, to the terminal only, the entry's candidate ID, the matched six-word run, and the ID of the candidate whose excerpt holds that run, and no other excerpt text.
    - `transcript_analysis` is imported only inside the mining subcommands, so it stays out of the freeze closure.
  - `evals/test_review_bench_mining.py`: tmp-path git repos, a local bare repo standing in for `origin`, and synthetic transcript JSONL.
- Modify:
  - `.gitignore`: add `evals/review_bench/.local/`.
  - `claude/.claude/scripts/select-tests.py`: add `REVIEW_BENCH_TEST_GLOB = "evals/test_review_bench*.py"`, and a `DOMAIN_RULES` predicate covering `evals/review_bench/`, `evals/run_review_bench.py`, `evals/test_review_bench*.py`, and `evals/measure_subagent_model_resolution.py`. That last file also maps to its own existing test, `evals/test_measure_subagent_model_resolution.py`; it is imported here and is currently unmatched. Also add:
    - a `CROSS_DOMAIN_EXCEPTIONS` entry from `transcript_analysis/{corpus,scope,review_rounds,reviewer_yield}.py` to the new glob, following the undeclared-dependency precedent at `:78`, `:93`, and `:316`;
    - `REVIEW_BENCH_TEST_GLOB` added to `evals/run_skill_evals.py`'s targets.
  - `claude/.claude/scripts/tests/test_select_tests.py`: expected sets for the new rules.
- Reuse:
  - `scope._iter_scoped_sessions`, `scope._repo_scoped_project_slugs`;
  - `review_rounds._detect_round_windows`, `review_rounds._session_record_branches`;
  - `corpus._index_subagent_dispatches`;
  - `reviewer_yield._is_reviewer_subagent_type`, `_scan_reviewer_transcripts`, `_extract_cited_paths`, `_normalize_cited_path`.

  Do not re-derive any of them.

**Dispatch 1b: fixtures, arms, runner.** Written only after the CLI spike (gate 3) passes, using the `model:` form and the model IDs the spike confirmed.
- Create:
  - `evals/review_bench/fixture_repo.py`: arm fixtures, the arm-neutral precision-judge fixture (the same tree, with no `bench-<lens>` file), and the recall-judge directory.
  - `evals/review_bench/arms.py`:
    - `LENS_READ_CLAUSES`: each lens mapped to its exact current clause (row 11);
    - `FUNCTION_CONTEXT_CLAUSE`: verbatim, see Approach;
    - `ARM_TOOLS = ("Read", "Grep", "Glob")`;
    - `snapshot_arm` / `install_arm`.

    Snapshotting fails loudly unless each clause matches exactly once and production's `tools:` holds every `ARM_TOOLS` entry.
  - `evals/review_bench/runner.py`, which holds:
    - `REVIEWER_MODEL_ID` and `JUDGE_MODEL_ID`, the IDs the CLI spike exercised (row 29);
    - the reviewer cap and timeout constants, and each judge's, with the judge ones at their bootstrap values until gate 6 (Caps);
    - `REVIEW_PROMPT_TEMPLATE` and `DISPATCH_PROMPT_TEMPLATE`, verbatim (see Approach);
    - the `RunRecord` JSONL schema;
    - campaign planning: blocks one at a time, runs in the pool, and resume;
    - the write-ahead record of each block's directories and session IDs, the resume sweep, and the run-store lock (Cleanup);
    - session-store lookup by session ID (row 47);
    - the per-run validity checks and retry-then-missing;
    - per-run statistics;
    - environment readings with the within-block rerun;
    - block-end cleanup.

    Only `smoke` accepts fault-injection options (gate 6); `run` rejects them. `RunRecord` fields: campaign_id, defect_id, arm, run_index, opaque_run_id, status, missing_reason, observed_model, observed_tools, out_of_session_paths, findings_text, wall_clock_s, read_calls, read_tokens_est, partial_view_reads, paged_followups, whole_file_reads_of_changed_files, dispatch_prompt_verbatim, cli_version, ambient_config_commit.
  - `evals/test_review_bench_runner.py`
  - Synthetic subagent transcripts under `evals/fixtures/review-bench/`.
- Modify:
  - `evals/run_review_bench.py`: add `snapshot-arms`, `smoke`, and `run`.
  - `evals/measure_subagent_model_resolution.py`: add one paragraph to the module docstring, beside its existing "Reuses from" paragraph: "Consumed by evals/review_bench/runner.py, which imports _run_claude_to_completion, _wait_for_subagent_sidecar, _resolved_temp_project_dir, _frontmatter_block, agent_frontmatter_tools, subagent_dir_for_session, parse_subagent_dispatches, and PER_RUN_BUDGET_CAP_USD."
    - This plan documents the consumer rather than promoting the names to public ones. Promotion would rename every call site in that file and its tests for no behavior change.
    - The docstring already carries this file's reuse record (row 36).
    - The select-tests rule (1a) and the freeze closure already cover edits to this file mechanically.
- Reuse:
  - from `measure_subagent_model_resolution`: the eight names above;
  - from `run_skill_evals`: `DEFAULT_WORKERS`. The runner finds session stores by session ID, not through `compute_session_store_dir()` (row 47);
  - `config_dir` from `_config_dir` directly, not through `run_skill_evals`'s re-export (row 36).
  - Define a local characters-per-token constant equal to read-scope's (row 18), under the small-duplicated-value exception. `read_scope.py` is mid-extraction by #1116.

**Dispatch 1c: judges, adjudication, analysis, freeze, docs.** Written only after the verify-sources pass over row 23 (gate 4).
- Create:
  - `evals/review_bench/judges/bench-judge-recall.md` (`tools: Read`) and `evals/review_bench/judges/bench-judge-precision.md` (`tools: Read, Grep, Glob`). Each has `model: inherit`, or the judge ID itself if the CLI spike found `inherit` unresolved under it, and `effort: high` (row 43), and holds its judge's prompt and rubric.
  - `evals/review_bench/adjudicate.py`, standard library only (M7). It holds:
    - the judge input builders;
    - `.bench/` path normalization;
    - the arm-free seeded ordering;
    - tolerant answer parsers, following `run_skill_evals.parse_disposition_answer`'s pattern;
    - the precision split check;
    - spot-check export and import, with κ and split agreement.
  - `evals/review_bench/analysis.py`, standard library only (M7). It holds:
    - design constants: δ, α, planning variance, resamples, κ floor;
    - `n_min(k)`, using `statistics.NormalDist`;
    - per-defect detection rates over completed runs, and the sub-K/2 drop;
    - pooled precision and the paired bootstrap;
    - the baseline sensitivity verdict, and the later-arm verdicts with the both-must-pass rule;
    - secondary columns;
    - the import-closure manifest compute and verify step, and the freeze preconditions;
    - the within-campaign and between-campaign environment checks.

    Each check refuses with exit 2.
  - `evals/test_review_bench_analysis.py`
  - `evals/test_review_bench_adjudicate.py`
- Modify:
  - `evals/run_review_bench.py`: add `judge`, `spot-check export|import`, `analyze`, and `freeze`.
  - `evals/README.md`: a new "Review bench" section. It covers:
    - purpose, and a pointer to `evals/README.md` § "Why local only — never CI";
    - usage;
    - the frozen conditions and the invalidation rule, including the cross-block environment check (this section is the rule's canonical home);
    - how a later arm is built from the frozen arm 1;
    - how to read the report, including that pairing does not protect the absolute per-arm figures (row 28), and that the precision verdict assumes arm-independent judge segmentation, which split agreement per arm checks (row 44);
    - interruption and cleanup: the write-ahead record, the resume sweep, and the run-store lock. It names the residual, a directory created just before its record is written, and says to list such directories by their `review-bench-` prefix, while no lock is held, and inspect them before deleting any;
    - out-of-session reads: which paths fail a run, that the engineer reviews the printed list after each smoke and baseline campaign, and that committed results carry counts only;
    - a "Runtime cost" paragraph in the style of the file's existing one, with row 39's formula and figures, each beside its source;
    - publication: the `source` breakdown is an own-history count, published beside `run_review_bench.py mine-rounds` and its scope refusal (row 37).

**Phase 2, PR 2: freeze.** Produced by running PR 1's merged harness. It changes no code.
- Create:
  - `evals/review_bench/defects.json`: the engineer-confirmed set, written only by `confirm`.
  - `evals/review_bench/arms/current-rule/bench-<lens>.md` and `evals/review_bench/arms/function-context/bench-<lens>.md`: via `snapshot-arms`.
  - `evals/review_bench/conditions.json`: via `freeze`.

**Phase 3, PR 3: baseline result.**
- Create:
  - `evals/review_bench/results/baseline.json`: aggregates and per-defect rates only.
  - `evals/review_bench/results/baseline.md`
- Not repository files:
  - the epic checkbox "A-bench baseline recorded (link)";
  - a result comment on #1114.

## Verification

**Automated, per dispatch.**
- **1a:** `.venv/bin/python3 claude/.claude/scripts/select-tests.py`. `select-tests.py` itself changes, and it is a global-trigger path, so this runs the CI-scope full suite. That suite includes `test_select_tests.py`, whose expected sets are the real check on the new domain rule. It excludes `evals/` (row 10), so also run `.venv/bin/pytest evals/test_review_bench_mining.py evals/test_measure_subagent_model_resolution.py`.
- **1b and 1c:** PR 1 carries 1a's `select-tests.py` edit until it merges. Until then, `select-tests.py` keeps selecting the CI-scope full suite with reason `global-trigger`, never the new domain selection. Run it for the suite it selects, then run the review-bench tests directly:
  - 1b: `.venv/bin/pytest evals/test_review_bench_mining.py evals/test_review_bench_runner.py evals/test_measure_subagent_model_resolution.py`;
  - 1c: the same, plus `evals/test_review_bench_analysis.py evals/test_review_bench_adjudicate.py`.
- **Every dispatch:** `.venv/bin/ruff check evals/ claude/.claude/scripts/`.

The tests must show these behaviors, all offline. No test launches `claude`.
- **Mining:**
  - A fix commit's removed line blames to its introducer.
  - Comment-only, blank, and markdown lines are ignored.
  - An addition-only hunk is flagged low-confidence.
  - A later-round citation of a path read in an earlier round on the same branch yields a candidate. A path outside that scope, or a citation on a different branch, yields none.
  - `mine-rounds` exits 2 when its sessions come from more than one config-dir root.
  - A PR head fetched from a local bare `origin` records `ref_status: fetched`. A missing `refs/pull/<N>/head` records `fetch-failed`, distinct from `pr-unknown`.
  - When the round's branch still exists locally, the miner lists that branch's commits, records `ref_status: local-branch`, and fetches nothing. The stand-in `origin`'s `refs/pull/<N>/head` points at a different commit, the candidate's commits come from the local branch, and no `refs/review-bench/pr/<N>` ref exists afterward.
- **Description provenance (`defects.py`, `confirm`):**
  - A description that copies a six-word run from any `.local/` excerpt is rejected, and `confirm` writes nothing.
  - A six-word run that also appears in the defect's public git text passes.
  - A description that shares only identifiers and code lines with an excerpt passes.
  - A `ConfirmedDefect` with a field outside the schema is rejected.
  - A rejection names the matched six-word run and the ID of the candidate whose excerpt holds it, and prints no other excerpt text.
  - A six-word run of non-ASCII words, such as accented Latin text, is rejected the same way as an ASCII one.
- **Fixtures:**
  - Exactly two commits exist, with no later commit reachable.
  - `.bench/` is excluded from git.
  - The `-W` diff is present.
  - The over-read-cap flag follows the characters ÷ 4 threshold.
  - The precision-judge fixture and the recall-judge directory hold no `bench-<lens>` file.
- **Arms:**
  - Clause substitution happens exactly once per lens and fails on a missing clause.
  - Arm files differ from production only in `name`, `model`, `tools`, and the substituted clause.
  - Every arm file's `tools:` is exactly `Read, Grep, Glob`. Snapshotting fails when a production lens lacks one of the three.
- **Runner:**
  - Command construction.
  - Dispatch-prompt verbatim check.
  - Observed-model check, for reviewer and judge runs.
  - A dispatcher tool call beyond its one Agent dispatch fails the run. So does a subagent tool outside its `tools:` list.
  - A Read of a symlink that resolves to a stand-in live checkout's copy of a changed file fails the run, and so do a Grep and a Glob over a directory that contains one. A Read under a stand-in config dir's `projects/` root fails the run, while a Read of a persisted tool result in the run's own session store passes. A Read of an unchanged live file is recorded in `out_of_session_paths` and passes.
  - A run whose final result event reports an error fails. A budget stop, in the shape the CLI spike captured, records `missing_reason: budget`.
  - A run's session store is found by its session ID, whatever the store directory's name. A run with no `<session-id>.jsonl` under `projects/` fails.
  - Retry, then missing, with the `missing_reason` of each failure mode.
  - Seeded block order is deterministic.
  - Read, `PARTIAL view`, and paged-read counting from the synthetic transcripts.
  - A block whose start and end environment readings differ reruns whole. Its first-attempt records are replaced, never mixed with the rerun's.
  - A block's cleanup runs only after every run and retry in it has finished.
  - `run` resumes by skipping complete blocks and rerunning a partial block whole. Before the rerun, it deletes every directory and session store the abandoned attempt recorded for that block, and leaves an unrecorded `review-bench-` directory untouched.
  - `smoke`, `run`, and `judge` refuse to start while the run store's lock names a live PID, and start over a lock whose PID is dead.
  - `run` rejects the smoke-only fault-injection options.
- **Adjudication:**
  - No arm name and no `.bench/` path appears in either judge's input or in the spot-check sheet.
  - Permuting the arm fields of a defect's runs leaves both judges' input order unchanged.
  - The precision answer parser handles multi-finding, single-finding, zero-finding, and malformed answers.
  - The recall answer parser accepts a multi-ID answer and a single-ID answer, including with formatting variation. It rejects an answer that omits an ID, labels one twice, names an ID not in the input, gives any label but FOUND or NOT_FOUND (such as "partially found"), or quotes a FOUND opening absent from that ID's findings.
  - The split check accepts openings found in order. It rejects an opening that is absent, out of order, or a repeat of an opening the run's text contains only once. Inputs include a multi-finding run, a single-finding run, and one run-on paragraph.
  - κ and split agreement on known tables.
- **Analysis:**
  - Identical arms give an interval around 0.
  - A known shift flips the recall verdict at δ. A known shift in pooled precision flips the precision verdict at δ. A later arm that passes only one gate is not certified.
  - `n_min(10) == 126`, `n_min(15) == 95`, and `n_min(20) == 79`.
  - For a defect-arm with FOUND, NOT_FOUND, and missing runs, the detection rate is FOUND ÷ (FOUND + NOT_FOUND).
  - At K = 10, a defect with 4 completed runs in one arm and 9 in the other is dropped from both arms. The drop count rises by one, and the effective N reported against N_min falls by one. A defect with exactly 5 completed runs in an arm is kept.
  - A manifest mismatch exits 2 and names the field.
  - The computed closure includes `evals/measure_subagent_model_resolution.py`, `evals/run_skill_evals.py`, and `claude/.claude/scripts/_config_dir.py`, and excludes `transcript_analysis/`.
  - A later arm's campaign whose CLI version or ambient config commit differs from the baseline's exits 2 as "invalidated — rerun all arms", even with every hash matching.
  - A campaign whose blocks carry two environments exits 2, naming each environment's blocks.
  - `freeze` exits 2 when the manifest differs from the last passing smoke campaign's, when the K being frozen differs from the smoke campaign's full-K fixture, when a record fails the provenance check, or when the `.local/` excerpts are absent. Editing `JUDGE_MODEL_ID` after a passing smoke campaign makes `freeze` exit 2 on the manifest.
  - The results `analyze` writes carry out-of-session read counts per arm and no path.

**Manual gates, in order. None of these is automated.** Gate 3 may run alongside dispatch 1a, and gate 5 alongside dispatches 1b and 1c.

1. **verify-sources over row 22, before dispatch 1a.** Run the `verify-sources` skill over row 22.
   - Quote git-blame(1)'s `-w`, `-M`, `-C`, and `--porcelain` from the installed git's own documentation, and record `git --version`, because this repo pins no git version.
   - Quote or drop each SZZ paper claim, and update row 22's tags.
   - If a documented flag behaves differently from row 22's recollection, 1a's miner follows the quoted text.
2. **Mining, the moment dispatch 1a lands.** Run `mine-rounds` first (G1), then `mine-szz`. Any figure from `mine-rounds`, the `source` breakdown included, is published only beside that command (rows 17, 37).
3. **CLI spike, before dispatch 1b.**
   - **Setup.** A throwaway script under `/tmp`, never committed, builds a project with `_resolved_temp_project_dir`. The project holds:
     - `probe.txt`;
     - a dummy `.env` holding one unique marker token and nothing secret;
     - `chain-01.txt` through `chain-20.txt`, each naming the next file to Read;
     - three agent files, each with `model: inherit` and `effort: xhigh`: `bench-spike.md` with `tools: Read, Grep, Glob`, `bench-spike-control.md` with `tools: Read, Bash`, and `bench-spike-budget.md` with `tools: Read`.
   - **Judge ID.** Read the Opus snapshot ID from the models page (row 29). The launches below use it as the judge ID.
   - **Probes.**
     - `bench-spike` Reads `probe.txt`, runs `~/.claude/scripts/marker.sh status` with Bash, Reads one harmless absolute path outside the project, such as `/etc/hostname`, and Reads `.env`. That command is one of the 25 pre-approved exact-match strings, and it writes nothing (row 33).
     - `bench-spike-control` runs the same command with Bash, then `echo spike-probe`. It is the positive control: it shows whether the allow rule pre-approves its command inside a headless subagent, and that the command would run if Bash were offered.
     - `bench-spike-budget` follows the chain from `chain-01.txt` to the end, one Read per file.
   - **Launches.** Each uses the arm dispatcher template with `claude -p --model <ID> --session-id <uuid> --output-format stream-json` and no `--permission-mode`, under `run_skill_evals.SAMPLE_TIMEOUT_S`, the timeout `measure_subagent_model_resolution.py` already dispatches subagents under (row 9).
     - `bench-spike` runs twice, once under the reviewer ID and once under the judge ID, each with `--max-budget-usd` at `PER_RUN_BUDGET_CAP_USD`.
     - `bench-spike-control` runs once under the reviewer ID, with the same cap.
     - `bench-spike-budget` runs once under the reviewer ID, with `--max-budget-usd` at twice the first `bench-spike` launch's reported `total_cost_usd`, and a timeout of 10 × `SAMPLE_TIMEOUT_S`, so that the timeout cannot end it before the chain does.
   - **Checks.** From `parse_subagent_dispatches` and the transcripts, confirm that:
     - each observed `message.model` equals the ID its dispatcher ran under;
     - each dispatch reached its agent from project scope;
     - `bench-spike`'s `observed_tools` holds no Bash, and no `marker.sh status` output appears;
     - `bench-spike`'s Read of `.env` was denied, and the marker token appears nowhere in its transcript (row 45);
     - each dispatcher's only tool call is its one Agent dispatch.
   - **Record.**
     - Whether the outside-project Read succeeded (row 26).
     - Whether `bench-spike-control`'s pre-approved command ran and `echo spike-probe` was denied (row 33).
     - The budget probe's result (row 39). If the run ended before its timeout with the chain unfinished, the cap bounds the subagent's spend. If the subagent finished the chain, or the timeout ended the run, treat the cap as bounding only the dispatcher.
     - Whether the budget probe's reported `total_cost_usd` counts the subagent, by comparing it with the subagent transcript's token usage. If it does not, gate 6 prices its cost figures from the subagent transcripts, the way `transcript-analysis.py subagent-mix` does (row 9).
     - A budget-stopped result event: the budget probe's, or else one from a rerun with the cap below the first launch's cost. Dispatch 1b's synthetic transcripts copy its shape.
   - **Outcomes.**
     - If `inherit` does not resolve under an ID, rerun once with that ID itself as `model:`. If that resolves, every agent dispatched under that ID pins it, and rows 26 and 29 record it.
     - Stop and take the result to the engineer before dispatch 1b if neither form resolves, if Bash is offered to or runs in `bench-spike`, or if the `.env` marker token reaches its transcript. The arm and runner design rests on the first two, and the out-of-session design on the third.
     - The control's result settles row 33 and changes no design, because no arm or judge holds Bash.
4. **verify-sources over row 23, before dispatch 1c.** Run the `verify-sources` skill over row 23, quoting each source or dropping it. Update the row's tags, and keep any dropped source's claim out of the README text that 1c writes.
5. **Engineer confirmation.** The engineer confirms or rejects every candidate. For each confirmed one, they approve its description, owning lens, and fixture commits. The session drafts descriptions from public git only, and `confirm` enforces that mechanically (M12). Before any arm runs, compare the confirmed N with `n_min(K)`, and the engineer may raise K.
6. **Smoke campaign.** Run it on at least two held-out fixtures: real candidates the engineer sets aside, never entered in `defects.json`. Run both arms and both judges. Use K = 1, except on the largest held-out fixture by changed-file tokens, the *full-K fixture*, which runs at the K gate 5 settled so that its judges see a full-size input. Judge runs start under the bootstrap caps (Caps). It must confirm:
   - each row-26 behavior;
   - observed model equals the frozen ID, for reviewer and judge runs;
   - the dispatch prompt is verbatim;
   - no run used a tool outside its allowlist;
   - Read statistics parse;
   - both judges' answers pass their checks: the recall answer's completeness and quoted openings, and the precision split check;
   - session-store directories are removed;
   - the CLI version is stable across a block;
   - every reviewer run's wall-clock is under the production p95. If one is not, re-derive the timeout (Caps).

   The engineer reviews `smoke`'s printed out-of-session path list (Out-of-session reads).

   Then use `smoke`'s fault-injection options to run one injected failure per failure mode, on the smallest held-out fixture. Each injection runs the real CLI.
   - Three injections constrain the run itself:
     - a timeout far below any run's duration;
     - a budget cap far below any run's cost;
     - a dispatcher `--model` other than the frozen ID.
   - The other six tighten the expectation, so that a real transcript or answer fails the real check:
     - a one-byte change to the expected prompt;
     - a different expected agent name;
     - an allowlist narrowed to `Read`;
     - the fixture itself treated as the live checkout;
     - the fixture itself treated as the config dir's `projects/` root;
     - one extra ID in the recall judge's expected ID set.

   Each injection must end `missing` after exactly one retry, with the matching `missing_reason`.

   Derive each judge's cap and timeout from its runs on the full-K fixture (Caps). Record the manifest and the full-K fixture's K in `.local/`, which `freeze` requires. Harness fixes happen here, before the freeze, and the smoke campaign reruns after any fix. Writing the derived judge caps, or a re-derived reviewer timeout, is such a fix, so the smoke campaign passes once more under the final constants before `freeze`.
7. **Cost and duration go/no-go.** Before the baseline campaign, compute its expected cost and wall-clock from the confirmed N and K, row 39's formula, and the smoke campaign's measured reviewer runs and full-K judge runs. At N = 126 and K = 10, that is:
   - about $2,900 for reviewer runs;
   - a ceiling of about $29,000, or about $58,000 if every run retried, if the budget probe (gate 3) showed that the cap bounds subagent spend. Otherwise no per-run spend cap applies. The figure says so, and gives the smoke campaign's highest per-run cost beside the timeout, which is then each run's only bound;
   - plus the judge runs;
   - 630 run-slots of wall-clock.

   The engineer approves the figure before any baseline run, because every run spends their own account (G5).
8. **Freeze (PR 2).** Run `snapshot-arms` and `freeze`. `freeze` refuses unless its preconditions hold (Freeze preconditions). Run the composition check in `docs/private-project-redaction.md` § "New figures against the grandfathered set" once for the `source` breakdown, which `defects.json` publishes first. PR 2's body labels that breakdown an own-history count and cites `run_review_bench.py mine-rounds` beside it (row 37). Review and merge before any baseline run.
9. **Baseline campaign.** Run it from a checkout whose `analyze` manifest check passes against `conditions.json`. Then run `judge`, then the human spot-check (a manual gate, blind to arm and to judge label), then `analyze`. If `analyze` reports a mixed-environment campaign, rerun the blocks it names and analyze again. Review `analyze`'s printed out-of-session path list before PR 3.
10. **Record (PR 3).** Commit the results, tick the epic checkbox, and comment on #1114. Report:
    - the sensitivity verdict with M1;
    - recall and pooled precision per arm, with intervals, and split agreement per arm beside the precision figures (row 44);
    - the arm 2 − arm 1 pooled-precision difference with its interval, reported as the precision gate's resolution and never as a verdict;
    - N and effective N against N_min;
    - κ for each label type;
    - dropped-defect counts, and missing runs per arm by reason;
    - the secondary columns, including Read tokens and the fix-date halves;
    - the `source` breakdown, labeled an own-history count and cited beside `run_review_bench.py mine-rounds` (row 37).

    Beside the absolute per-arm figures, state that pairing protects only arm-vs-arm differences, not those figures (row 28). The figures also reflect two departures from production that apply to both arms: the review prompt names no concern (row 13), and reviewers run without Bash. They describe the instrument, not production reviewer performance.

## Out of scope

- **A-field:** A1, which extracts `read-scope` into `transcript_analysis/read_scope.py`, and A2, reviewer-read telemetry. The sibling session working #1116 owns A1, and A2 is blocked until A1 merges. Both get a follow-on `/plan-it` once A1 merges.
- **Arm 3 (B's draft rule) and B's merge decision.** #1115 runs arm 3 against this frozen instrument. What B does after a "not sensitive", "inconclusive", or "adjudication not validated" result is for the epic and #1115 to decide.
- **A function-context flag on `pr-diff-against-base.sh`.** B owns it (row 19). A-bench diffs its own synthetic fixtures.
- **Adding `evals/` tests to CI or to `FULL_SUITE_TARGETS`.** The review-bench tests are offline and CI-safe. Widening CI's collection scope is a separate decision that affects every contributor and changes both CI and select-tests in lockstep.
- **Replaying the `/code-review` main-thread base checklist** (`code-review/SKILL.md:82`), or multi-lens panels. A-bench runs one owning lens per defect.
- **Defects in markdown instruction files.** B's rule excludes them.
- **Review-ledger dispositions as a defect source.** They have no PR linkage (row 21).
- **Pinning or installing a specific Claude Code version.** Installing software is a user-only action. The harness records the version, and the invalidation rule handles drift.
