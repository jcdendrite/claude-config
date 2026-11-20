# GH-1119 F1: offender assessment for the 1,000-line code-file limit

## Context

Produce the F1 offender assessment for GH-1119: a reviewed table of every tracked `.py` and `.sh` file over 1,000 lines, with a verdict and a proposed exception ceiling for each, so the engineer can judge the limit before F2 enforces it.

Ask: "GH-1119 F1 offender assessment. Read agent-reviews/GH-1113-status-assessment.md first: four harness-closure files must not be split between the review bench's freeze and B's acceptance campaign, and F1's PR must not close #1119." Where the assessment lives — accepted via label "Report under docs/reports/ (Recommended)". How split-pending verdicts get tracking issues that do not exist yet — accepted via label "Propose, file after review (Recommended)". Whether F1's PR touches `docs/design-decisions/code-file-line-limit.md` — accepted via label "Leave it to F2 (Recommended)".

The size limit is decided but not enforced. `docs/design-decisions/code-file-line-limit.md` holds F2's check back until F1 records every current offender. It also names F1's result as a Revisit condition: the limit is reconsidered if the assessment finds "an unreasonable number or shape of exceptions". F1 has no prerequisite. F1 gates F2. F1's split-pending verdicts give the remaining large-file splits their tracking issues.

The first-pass counts in GH-1119's body no longer describe the tree. The body expects 43 offenders, 8 of them non-test. At `ab85a627`, `git ls-files -- '*.py' '*.sh'` piped through `wc -l` shows 112 files over 1,000 lines: 88 named `test_*.py` or `conftest.py`, and 24 others. F1 re-derives every count and records the command that produced it.

For each offender GH-1119 asks F1 to record its physical and non-comment line counts, its 90-day commit count, its subject and candidate seams, and a verdict. The verdict is either split pending, with the issue that tracks the split, or structural exception, with its reason. Both verdicts get a proposed exception row whose ceiling is the file's size at assessment time. No check lands in F1.

Two constraints bind the result. First, four files in the review bench's harness closure exceed 1,000 lines: `evals/review_bench/runner.py`, `evals/run_skill_evals.py`, `evals/measure_subagent_model_resolution.py` and `evals/review_bench/analysis.py`. A change to the closure after the bench's `freeze` invalidates its baseline, so any split of those four is scheduled before `freeze` or after child B's acceptance campaign, never between. Second, GH-1119 closes on F2's PR. F1's PR cites the issue with no closing keyword.

The intended outcome is one PR holding the assessment report under `docs/reports/` and this plan. The report states the offender count and shape plainly enough for the engineer to rule on the Revisit condition. Tracking issues for split-pending files are proposed in the report and filed only after the engineer reviews it.

## Approach

F1 ships one dated report under `docs/reports/`, at the path Critical files defines, and the whole report is the output of a scratch renderer. A stdlib-only measuring program, recorded in the report's appendix, prints every number from a pinned commit. Read-only verdict dispatches write judgment files that hold no measured figure. The renderer joins both with a prose file. Two `cmp`s gate the report: one against a fresh render, and one comparing its verdict-table rows with the measured physical counts. The report proposes a tracking issue for each split-pending file that has none, and files none. Filing happens after merge, outside this plan.

**Measurement.** One program produces every number, so no figure is hand-joined across separate command outputs. `measure.py <ref>` prints tab-separated records, each led by its type:
- `offender`: path, physical lines, non-comment lines and 90-day commits;
- `near`: path and physical lines;
- `figure`: name and value;
- `meta`: name and value, for the pin, the pin's committer date, the window start, `sys.version` and `git --version`.

Its rules:
- **Pin.**
  - The pin is `git merge-base HEAD origin/main` when the first dispatch starts. That is `ab85a627` unless the branch has been synced.
  - The header records it as a short SHA, as `docs/reports/2026-08-10-repo-quality-audit/findings.md:4` does, because a full SHA matches the long-hex detector (row 39).
  - The program reads blobs at the pin, not the working tree.
  - It lists them with `git ls-tree -r -z <pin>`, keeping modes `100644` and `100755` with a `.py` or `.sh` suffix.

  Merges, rebases and other sessions' splits therefore cannot move a count. The report keeps its one pin, and "Drift after the pin" below says where later changes go.
- **Line indexing.** The program splits a blob on `\n` only, never with `str.splitlines()`, because `ast` and `tokenize` number lines by `\n` alone. `claude/.claude/scripts/tests/test_cursor_adapter_translate.py` is the control. It holds a literal U+2028, so its newline count is 1,857 while `splitlines()` gives 1,858 (row 35). Verification step 2's `wc -l` cross-check covers it for both line columns.
- **Physical lines.** One less than the length of the `\n`-split list the non-comment pass iterates, which is the newline count `wc -l` prints.
- **Non-comment lines.** A line counts unless it holds only whitespace, a comment, or a Python docstring.
  - The column feeds one thing: the summary's unit-sensitivity figures, which show the engineer whether GH-1119's choice of unit is worth revisiting. No verdict or ceiling reads it, so it gets no independent check.
  - GH-1119's body does not define the term. It already rejects non-comment counting as the check's unit, because the count needs a per-language classifier and comments cost Read tokens too (row 9).
  - Docstrings are excluded because in Python they carry the prose that comments carry in shell.
  - Python: `tokenize` finds comments, and `ast` finds docstrings. The counting rules:
    - A docstring is a string-constant first statement of a module, class, function or `async` function body.
    - A line holding both a docstring and code, as in a one-line `def` with its docstring, counts.
    - A whitespace-only line inside any multi-line string holds only whitespace, so it does not count.
    - A Python file that `tokenize` or `ast` rejects reads `unparsed`, not a number.

    Both parsers are the standard library's own, so an exact count costs no hand-written state, where a line-based `#` count would misread `#`-led lines inside multi-line strings as comments.
  - Shell: a comment line is one whose first character other than a space, tab, vertical tab, form feed or carriage return is `#`. Every comment-only line qualifies. The only false matches are `#`-led lines inside a heredoc body or a multi-line quoted string, so the count can only overstate comments. The shell cell is therefore a lower bound, printed as `≥N`.
  - Seven shell files exceed the limit. Row 31 gives their `<<` line counts.
- **90-day commits.** The window is the 90 days before the pin's committer date.
  - A counted commit is reachable from the pin. It is not a merge (`--no-merges`). `--since` places it inside the window. It touches the file's history, followed across renames with `--follow`. Without `--follow`, `claude-skills/skills/tests/test_skills.py` reads 76 commits instead of 100, because `c6fcd96e` renamed it inside the window (row 36).
  - The program prints counts only, never an author name or email (G3).
- **Ceiling-edit load.** The same per-file `git log --follow --numstat` walk feeds this figure. A commit counts toward a file when that file's added and deleted line counts differ in it, so a pure rename does not count. The load is the number of distinct commits in the union across offenders. Under exact both-direction ceilings, each such commit would have edited the exception table. The program prints the load beside the window's total non-merge commit count, so the load reads as a share (row 32). Squash merges make that roughly a PR count (row 19).
- **Versions.** Python 3.12 changed how `tokenize` splits f-strings, so a rerun on another interpreter can differ. Method records both versions on one line.
- **Test code.** A file is test code when its path has a `tests` directory component or its name is `test_*.py` or `conftest.py`. This moves test-support modules such as `claude/.claude/tests/helpers.py` out of production. That matters for the sensitivity figures, because the engineer first proposed 2,000 lines for test code (row 16). The report reconciles its split with Context's name-only 88/24 in one line.
- **Summary figures the program prints:**
  - offenders by test code and production;
  - counts over 1,000, 1,500 and 2,000 lines, in both units;
  - near-limit files at 900–1,000 physical lines, since a file at exactly 1,000 passes (row 17);
  - the `unparsed` count;
  - each shell file whose non-comment lower bound is at or under a threshold its physical count is over, by name, since its side of that threshold is undetermined;
  - the ceiling-edit load and the window's total non-merge commit count.

  These go in the Summary's Figures table, which the renderer builds from them.

The program lives in a `~~~python` block in the report's appendix, and that block is the report's only one. The renderer copies it there from the file that produced the measurements. Options set aside for the measuring program:
- **Recorded one-liners only.** These would be lighter, and the physical, churn and shell columns each have one. But 112 rows would then be hand-joined across several outputs, a transcription risk for every number. The Python column needs `tokenize` and `ast` anyway.
- **A committed script under top-level `scripts/`, with tests.** This is durable tooling, with lint and test scope, for a one-off measurement whose output the dated report freezes. F2 counts physical lines by default and needs no classifier.
- **A `.py` file beside the report.** It would be a tracked code file under `docs/` that no documented lint command covers (row 23), and that F2's own check would then scan.
- **A stateful shell scanner, shfmt's AST or tree-sitter-bash.** No decision depends on the precision these add, since verdicts and ceilings never read the column. Neither parser is a declared dependency (row 24).

**Verdict rule.** Every verdict dispatch reads this rule here, and the report's Method section restates it for the report's reader.
- **Structural exception.** Splitting is blocked by something outside the file's current state, and the reason cites a `file:line`, an issue, or the engineer's ruling on this report's PR. Three kinds qualify:
  1. A loading or shipping constraint that requires one file. The dispatch verifies the constraint by reading. For example, `_config.sh`'s recorded constraint is independence from `_lib.sh` (row 15), which does not by itself bar sibling files.
  2. A recorded decision that governs the file's shape:
     - #1117's in-file reorganization governs `_lib.sh`.
     - #1015's gates govern `helpers.py` and any `conftest.py` it covers, and GH-1119's body defers to #1015 there (row 9).
     - If #1015 and #1396 disagree on `helpers.py`, the row names both and the summary lists it for the engineer.
     - The engineer's ruling on this report's PR, which a declined row cites, is also a recorded decision of this kind.
  3. A body that is one data table or generated output.
- **Split pending.** The file has at least one named seam along which parts can move to sibling files without changing how the file is loaded, invoked or installed. A seam is a subcommand, domain, detector, lifecycle stage or test-class family. Positional seams do not count (row 18). A test file whose module also splits takes that module's seam where one fits.
- **Inputs.**
  - A verdict dispatch reads blobs at the pin through `git show <pin>:<path>`, public issue bodies through `gh issue view`, and this plan. A test-file dispatch also reads the production judgments, which D2 wrote from those same inputs.
  - It reads no other untracked local file.
  - Its prompt names this plan, the short pin, `<scratch>`, its row-set file and its output file, and carries no other prose.
- **Churn and margin do not change a verdict.** A file barely over the limit with no churn is still split pending.
- **Set aside: churn- or margin-weighted verdicts.** They would label an economic judgment "structural". They would also hide the number of files barely over the limit, which is what the Revisit condition weighs. The summary reports that number instead.

**Judgments.** Each verdict dispatch writes one tab-separated line per row in its row set: path, subject, seams, verdict, and tracking or reason. The file holds no measured figure. Its cell rules:
- Subject and Seams are never empty, and no cell holds a `|`, which would break the report's table.
- A split-pending Seams cell names the seam's symbols as they appear in the file's blob at the pin, backticked.
- Verdict reads exactly `split pending` or `structural exception`.
- A split-pending row's last cell is a tracking value: `#<digits>` for an existing issue, or `proposed (not filed)`. The existing issues are #1117 and its sub-issues #1433–#1436, #1530, #1531, #1532, #1015, and #1118 with `test_skills.py` phase 4 in flight (row 11).
- A structural exception's last cell is reason prose holding at least one citation, either a backticked `path:line` or `#<digits>`. A declined row's reason reads `` engineer's ruling on #<n>: "<approved text>" ``, where `<n>` is this report's PR.

A file far over 1,000 lines is not read whole in one call (row 22). A dispatch therefore works from an outline, `git show <pin>:<path>` piped to `grep -nE` on `class` and `def` lines for Python and on function definitions and section banners for shell, then reads targeted ranges.

**Renderer.** `render.py <pin> <scratch>` writes the whole report to standard output. Its inputs are `prose.md`, `measure.tsv`, the four judgment files, `closure.txt` and the text of `measure.py`. `prose.md` names each figure by its Figures-table row and quotes no number the program or renderer prints, though it may state whether a check passed. The renderer exits nonzero, naming each failing row, when any of these holds:
- an offender has no judgment line or more than one, or a judgment names a path that is not an offender;
- a cell breaks one of the cell rules under Judgments, or a `#<digits>` tracking value is not one of the existing issues listed there;
- a split-pending Seams cell has no backticked symbol, or a backticked Seams symbol does not appear, word-bounded, in the file's blob at the pin.

A failing row goes back to the dispatch that owns it, with only the failing paths. The session copies that judgment file before the repair and `diff`s it after, expecting only those paths' lines to change. A D2 line whose verdict or seams change after D3–D5 started reruns the rows of the test files for that module. A passing render:
- sorts rows into the Structural exceptions table and the Split pending tables by verdict, by physical count descending and then by path;
- appends `; closure-windows` to the last cell of each offender that `closure.txt` lists;
- computes the verdict counts and the proposed-issue count, which is the number of `proposed (not filed)` cells, into the Figures table;
- renders Method's closure list from `closure.txt`'s intersection with the offenders, and its versions line from the `meta` records;
- copies `measure.py` into the appendix's `~~~python` block.

Every edit to the report goes through a scratch input and a fresh render, never a hand edit of `<findings>`.
- `<scratch>` is `agent-reviews/f1-scratch/` under the worktree root. `agent-reviews/` is gitignored (`.gitignore:41`), so step 1's status listing stays clean.
- Commands name it by that relative path, because the armed redaction hook denies a home-rooted path in a gated command's text (rows 39 and 49).
- It lasts until the worktree is removed after merge, so a post-review edit re-renders from it. If the session ends first, the handoff note names the path.

Options set aside for assembly:
- **Dispatches editing the report in place.** A table parser would then have to re-check every cell after each edit, and the dispatches would run in sequence because all write one file.
- **A `join` and `awk` pipeline.** It is still a multi-step script, and its seam check needs one `git show` per row.
- **Committing the renderer.** It would be durable, tested tooling for one dated report, and a tracked code file that F2's check would scan. The report keeps only the measuring program, which a reader needs to reproduce the numbers.

**Report shape.** The report's sections run in this order:
1. **Header:** date, pin, scope, and where to find the reproduction. The 2026-08-10 report's header is the model (`findings.md:1-9`).
2. **Summary for the Revisit ruling:**
   - the Figures table, holding the program's figures, the verdict counts and how many new issues per-file filing would create;
   - the assessment's own reading, marked as the assessment's and bound by the Renderer's `prose.md` rule.
3. **Defaults and consequences:** GH-1119's "Plan defaults" and "Consequences" lists (row 9), each pointing at the Figures-table row that applies, if one does. This plan's own defaults follow: the units, the window, the test-code definition and per-file issues.
4. **Method:**
   - the units, the window and the verdict rule;
   - the cell formats and the last-cell vocabulary, and that the renderer confirms a seam name occurs in the file, not that the seam is real;
   - the issue template;
   - the closure windows and the closure list;
   - the versions line, and the result of each check in Verification steps 2 and 3, with what it proves;
   - one sentence saying each row's proposed ceiling is its Physical cell. A duplicate column would add nothing.
   - one sentence saying proposed issues are filed after merge, outside this report, and that GH-1113's task list lists each filed issue.
5. **Structural exceptions** table.
6. **Split pending:** one table per directory group, for `claude/.claude/hooks/`, `claude/.claude/scripts/`, `claude-skills/`, `evals/` and any other path, with rows by physical count descending.
7. **Near-limit files:** one table with Path and Physical columns only (row 32).
8. **Appendix:** the program and the cross-check commands.

Tables 5 and 6 share one column list: Path, Physical, Non-comment, 90-day commits, Subject, Seams, Verdict, Tracking or reason. Physical is a bare integer, which Verification step 6's row extraction reads.
- A note such as the `closure-windows` token goes in the last cell, after its tracking value or reason.
- The report names each piece by issue number and a plain description, not by the epic plan's letter labels, because the report outlives the plan that defines them. For example, it names B as #1115 and the size check as #1119's closing PR.

**Harness-closure rows.** The same rule sets their verdicts. The engineer's constraint (row 1) governs only scheduling.
- Membership comes from `compute_harness_closure()` (`evals/review_bench/analysis.py:749`), intersected with the offender set, not from the four-name list.
- D1 runs it once, before any sync, because it reads the working tree, not the pin (row 14).
- The four files the engineer named must be in its output, so an empty result cannot pass (Verification step 3).

Method states the windows once, from `.claude/plans/measure-review-quality.md` (row 29):
- **Before the smoke campaign:** splitting is free.
- **After the smoke campaign and before `freeze`:** splitting is allowed but forces a re-smoke (`:364-366`).
- **From `freeze` through #1115's acceptance campaign:** splitting is forbidden. A closure change invalidates the baseline and reruns every arm (`:368`, `:376`).
- **After #1115's acceptance campaign:** splitting is free.

A proposed issue for one of these files carries the four windows in its body. It also names #1114 and #1115 as where to read the campaign's current state.

**Proposed issues.** The report files no issue. A split-pending row with no existing issue reads `proposed (not filed)`, and Method's issue template describes the issue to file.

One issue is proposed per such file. That matches GH-1113's task-list sentence `Further files get an issue each from F1's "split pending" verdicts.` (row 10). The issue template in Method, filled from the row, gives:
- the title "Split `<path>` below 1,000 lines";
- a body naming the seam, the report path and "Part of GH-1113";
- the closing condition, which is the PR that brings the file to 1,000 lines or fewer.

Two alternatives were set aside:
- **Per-cluster issues by default.** Each would close on its last PR, so every earlier PR in the cluster would carry the closing-keyword hazard (row 28). Whoever files can still group them.
- **Filing before merge.** The engineer chose "After merge (Recommended)" (row 43).

After the engineer reviews the PR:
- A ruling that changes the limit or the unit sends this plan back to `/plan-it` revision, because it changes which rows need an exception at all.
- A row the engineer declines to split becomes a structural exception, since a split-pending row proposes a split. The session shows the last cell as the passing render prints it, in the form Judgments gives, and the engineer says yes to that text before the commit that introduces it. A later change to that cell goes back to the engineer. The reason never goes to the tracker.

**Drift after the pin.** Before each push, the session runs the measuring program at `origin/main` and diffs its offender paths and physical counts against the pin's (Verification step 10). The PR description lists each offender the diff reports as added, removed or resized.
- **Added after the pin:** the offender gets no row, because the report is pinned. The PR description marks it unassessed, and F2's plan assesses it before F2's check lands, since F2 re-derives every ceiling then (row 30).
- **Removed after the pin, or resized:** the row stays as measured.

**PR body and the closing keyword.** After every body sync, whether `/ready-for-review` or `/pr-description` alone made it, the session runs four separate Bash calls:
1. `gh pr view <n> --json body --jq .body > <scratch>/body.md`;
2. an edit deleting the `Fixes` line and re-appending, from `<scratch>/pr-records.md`, the drift list or composition-check record if the sync dropped it;
3. `grep -iE '<pattern>' <scratch>/body.md`, with Verification step 11's `<pattern>`, which must print nothing;
4. `gh pr edit <n> --body-file <scratch>/body.md`.

The sync re-adds `Fixes GH-1119` every time, and every new commit forces one (row 34). The body cites GH-1119 in prose. Commit messages stay free of closing keywords too, since a squash commit landing on `main` can close an issue (G2).

Set aside: renaming the branch to drop its `GH-1119/` segment. That would stop the derivation at its source. But it would also drop the `GH-1119:` prefix that `pr-create-preflight.py` builds into the PR title from the same segment (`:169-173`), and it would mean replacing a branch that may already be pushed.

**Publication checks.** The redaction hook's content scans do not run in this checkout by default (row 37), and the engineer chose to arm them in this worktree (row 48).
- **Arming.** Before D1, the session writes an untracked `.claude/private-projects-optin` at the worktree root. It appends `/.claude/private-projects-optin` to the exclude file `git rev-parse --git-path info/exclude` names, so the marker is never listed or staged (row 49). The marker goes with the worktree, and Verification step 7 confirms the scans are armed.
- **Coverage.** The armed hook scans every commit and every PR title and body before the call that publishes it runs. It never scans the branch name or a staged file's path, which `git push` and each commit publish, and both are fixed by this plan.
- **Composition.** Before the first commit, each new figure category is checked against the grandfathered set, as `docs/private-project-redaction.md` requires of every new figure, own-history included (row 38, Verification step 8).

**Assumption ledger**

**Root:** F2's size check for GH-1119 waits until every tracked `.py` and `.sh` file over 1,000 lines has its counts, churn, subject, seams and verdict recorded. The engineer then rules on the decision record's Revisit condition from this assessment's count and shape (row 27). This plan produces that record without changing any counted file.

**Givens:**
- G1. Other sessions change offender sizes while F1 runs, among them the `test_skills.py` phase-4 split. Reason: other sessions own those branches, and F1 cannot hold `main` still. `[unverified — peer-session state relayed by the dispatching session]`
- G2. GitHub closes an issue when a closing keyword paired with the issue's reference, `GH-` form included, appears in either place:
  - a merged PR's body;
  - a commit message landing on the default branch.

  Reason: this is platform behavior that no repository artifact controls. `[unverified — GitHub's docs not fetched; row 20's closingIssuesReferences result is the nearest evidence, and #1114's event history does not corroborate the relayed closure, per the platform reviewer]`
- G3. The report ships in a public repository. Reason: the repository's visibility is a GitHub setting, outside any repository artifact. The redaction rule that follows from it exempts "Content derived only from this repo's own history". `[verified: CLAUDE.md, opening paragraph and § "Redact private-project-identifying content"]`

The closure-invalidation rule, F2's ceiling timing and `/pr-description`'s `Fixes` default are this repository's own artifacts, so they are in reach. Rows 28–30 record them, and Out of scope says why this plan does not change them.

**Mechanisms:**
- M1 — Pin to the merge-base and read blobs at the pin, recorded as a short SHA. Drift between the pin and the `origin/main` tip goes in the PR description. `anchors: root, G1, row12, row39`
- M2 — One stdlib measuring program with a single mode that prints tab-separated records, recorded in the report's appendix. It is heavier than recorded one-liners. The Approach's "Options set aside for the measuring program" gives the lighter primitives and why each fails. `anchors: root, row8, row23, row24`
- M3 — Non-comment lines exclude whitespace-only, comment and docstring lines, indexed on `\n` only. Python counts exactly with `tokenize` and `ast`. Shell counts lines whose first non-whitespace character is `#`, printed as a lower bound, because no decision needs more precision than that bound gives. `anchors: row9, row24, row31, row35`
- M4 — A 90-day window anchored to the pin's committer date. One `--follow` walk per offender feeds its count and the ceiling-edit load. The program prints no author identity. `anchors: root, G3, row19, row32, row36`
- M5 — The verdict rule: structural exceptions only for a cited constraint, decision, or indivisible body. Everything with a named non-positional seam is split pending. `anchors: root, row9, row11, row15, row18, row26`
- M6 — Report shape: the Revisit figures first, sensitivity at 1,500 and 2,000 lines in both units, structural exceptions before split pending, and the ceiling stated once rather than as a column. `anchors: root, row12, row16, row17, row27, row32`
- M7 — Closure membership comes from `compute_harness_closure()`, run once before any sync, and the four named files must be in its output. The renderer marks each closure row with a fixed `closure-windows` token and renders the closure list into Method from the same file. The four scheduling windows go in Method and in each closure file's proposed issue. `anchors: row1, row14, row29`
- M8 — One proposed issue per file that has none, from Method's template, filed after merge outside this plan. The tracking cell reads `proposed (not filed)`, and Method names GH-1113's task list as where filed issues are listed. A declined row becomes a structural exception, in text the engineer approved. A changed limit or unit re-enters `/plan-it`. `anchors: row4, row5, row10, row12, row28, row43, row44`
- M9 — `/ready-for-review` re-adds `Fixes GH-1119` on every body sync, so the session strips the line after each sync and greps the body file for a closing reference before re-applying it. The last action after the final sync checks `closingIssuesReferences`, the PR title and body, and the commit messages. `anchors: G2, row1, row19, row20, row28, row34`
- M10 — D1 measures, D2 judges the production files, D3–D5 judge the test files in parallel, and D6 renders. A verdict dispatch reads only pinned blobs, public issue bodies, this plan and the production judgments. It writes a judgment file that holds no measured figure. `anchors: root, row22, row41, row42, row45`
- M11 — A scratch renderer builds the whole report and checks row coverage, the cell rules, tracking numbers and seam symbols. Before each commit, one `cmp` proves the report is a fresh render, and another checks its verdict-table rows against the measured physical counts. It is heavier than dispatches editing the report directly. The Approach's "Options set aside for assembly" gives the lighter primitives and why each fails. `anchors: root, row8, row41, row42, row46`
- M12 — An untracked, locally excluded opt-in marker arms the redaction hook in this worktree, and one probe confirms it before the first commit. Each new figure category gets the composition check before the first commit. `anchors: G3, row37, row38, row39, row48, row49`
- M13 — Drift against the `origin/main` tip goes in the PR description before each push. An offender added after the pin is left to F2's plan. `anchors: G1, row30`

**Assumptions:**
1. `[engineer-verified: "four harness-closure files must not be split between the review bench's freeze and B's acceptance campaign, and F1's PR must not close #1119."]` The tag covers two points: the closure scheduling constraint, and that F1's PR does not close #1119.
2. `[engineer-verified: "Report under docs/reports/ (Recommended)"]` The tag covers where the assessment lives.
3. `[unverified]` The description behind row 2's label was the dispatching session's proposal, and this plan adopts it:
   - a dated report directory beside `docs/reports/2026-08-10-repo-quality-audit/`, holding the offender table and the recorded commands;
   - this plan file stays as provenance, in the same PR;
   - the epic plan's "live in F1's plan file" (`code-file-size-splits.md:219`) is set aside.
4. `[engineer-verified: "Propose, file after review (Recommended)"]` The tag covers how split-pending verdicts get issues that do not exist yet.
5. `[unverified]` The description behind row 4's label was the dispatching session's proposal:
   - the table proposes an issue per file or per cluster, marked not yet filed;
   - approved issues are filed through `gh` after review, and the rows are updated before merge.

   This plan adopts the first item, with per file as the default (M8). Row 43 replaces the second.
6. `[engineer-verified: "Leave it to F2 (Recommended)"]` The tag covers whether F1's PR touches `docs/design-decisions/code-file-line-limit.md`.
7. `[unverified]` The description behind row 6's label, that F1 edits nothing in that file, was the dispatching session's proposal. This plan adopts it.
8. At `ab85a627`, 112 files exceed 1,000 lines: 88 named `test_*.py` or `conftest.py`, and 24 others.
   - The 88 `[verified: Grep line counts of every test_*.py and conftest.py in the worktree, this session]`.
   - The total and the 24 `[verified: the platform reviewer's git ls-files and wc -l run at the pin, which printed 112 with 24 not named test_*.py or conftest.py]`.
   - By directory, the 88 split as 35 under `claude/.claude/hooks/tests/`, 38 under `claude/.claude/scripts/tests/` (one of them `conftest.py`), 13 under `evals/` and 2 under `claude-skills/skills/tests/`. `[verified: same Grep]`
9. GH-1119's body holds four items:
   - a "Closing PR" section (row 28);
   - a "Plan defaults — not engineer decisions" list;
   - a "Consequences to flag for the engineer when F1 is presented" list;
   - the deferral to #1015 for `helpers.py`.

   It does not define "non-comment line". Its one statement on the subject rejects non-comment counting as the check's unit, for three reasons: the count needs a per-language classifier, shell heredocs and a quoted `#` defeat a line-based one, and comments cost Read tokens too. `[verified: gh issue view 1119 --json number,title,state,body, run by the reviewing session]` The first dispatch re-reads the two lists.
10. GH-1113's task list reads `Further files get an issue each from F1's "split pending" verdicts.` `[verified: gh issue view 1113 --json body, run by the reviewing session]`
11. These issues are open: #1117 (with sub-issues #1433–#1436), #1530, #1531, #1532, #1015 and #1396. `[verified: gh issue list --state open --limit 100, run by the reviewing session]` #1118 is closed while `test_skills.py` phase 4 is in flight under it. `[unverified — relayed]` #1396's relation to #1015 is `[unverified]`.
12. A report sits in a dated directory and pins its baseline commit in its header. It adds an index row, newest first. After merge, its one permitted edit is a dated `## Status` section recording which findings have since been closed and by which commit. An instruction aimed at a future actor may also be marked superseded in place. `[verified: docs/reports/README.md:3-14, :31-35, read this session; docs/reports/2026-08-10-repo-quality-audit/findings.md:1-9]`
13. A change under `docs/` selects `HOOKS_TESTS_DIR` and `SKILLS_TESTS_DIR`, and a change under `.claude/plans/` selects nothing. `[verified: claude/.claude/scripts/select-tests.py:732, :1032]` `docs/reports/**` is outside the docs state-path contract. `[verified: claude-skills/skills/tests/_skill_md_helpers.py:543-556]`
14. `compute_harness_closure()` walks imports statically from `review_bench.runner`, `.adjudicate` and `.analysis`, and returns a mapping keyed by repo-relative path. `[verified: evals/review_bench/analysis.py:700, :749-771, read this session]` It resolves each file under directories derived from its own module's location, so it reads the working tree, not the pin. `[verified: analysis.py:31-33, :703-721, read this session]` Importing `analysis.py` leaves the tree unchanged. `[verified: the platform reviewer ran Verification step 3's commands, which printed 4 and left the tree clean]`
15. `_config.sh`'s recorded constraint is staying dependency-free of `_lib.sh` for its standalone consumers. That is not a bar on its own sibling files. `[verified: code-file-size-splits.md:548, which cites _config.sh:2-12; _config.sh not reopened]`
16. The engineer first typed "2000 lines for test code" and later selected 1,000 lines for tests. `[verified: code-file-size-splits.md:549-550]`
17. A file at exactly 1,000 lines passes. The check fails only above the limit. `[verified: code-file-size-splits.md:243]`
18. Positional splits, such as a `_part2` file, are forbidden. `[verified: code-file-size-splits.md:254]`
19. `main` merges by squash, so one commit on `main` is one PR. The squash message is built from the branch's commit messages, and the squash subject is the PR title only for a PR of more than one commit. `[verified: the platform reviewer's read-only GET of the repository's merge settings: squash only, COMMIT_MESSAGES, COMMIT_OR_PR_TITLE]`
20. `closingIssuesReferences` is a valid `gh pr view --json` field, and it reports a closing reference written in the `GH-` form. `[verified: the platform reviewer's gh pr view 1521, whose body closes #1114 in that form, returned 1114]` Whether it reflects a closing keyword in a commit message is `[unverified]` and not relied on, since Verification step 11 also greps the commit messages and the PR title.
21. No repository test parses `docs/reports/README.md`'s index table. `[verified: Grep for reports/README across *.py, no match]`
22. The Read tool's per-call cap means a file far over 1,000 lines is not read whole in one call. Verdict dispatches therefore work from outlines and targeted reads. `[verified: docs/design-decisions/code-file-line-limit.md:31-34, which cites code-review/REFERENCES.md § "Read-tool per-call token cap"; REFERENCES.md not reopened]`
23. The documented `ruff` command covers `claude/.claude/`, `claude-skills/`, `plugins/` and `evals/`, not `docs/` or top-level `scripts/`. `[verified: CLAUDE.md § "Commands"]`
24. `requirements-dev.txt` declares no shell parser. `[verified: requirements-dev.txt:1-5]`
25. Every test-file offender has a nameable, non-positional seam. `[unverified]` The verdict dispatches settle this per file.
26. Whether #1015 covers `claude/.claude/scripts/tests/conftest.py` was left open by the epic plan. `[verified: code-file-size-splits.md:259]` The production-files dispatch settles it from #1015's body.
27. The decision record names the assessment's result as a Revisit condition: "an unreasonable number or shape of exceptions". `[verified: docs/design-decisions/code-file-line-limit.md:59-63]`
28. `/pr-description` writes `Fixes <TICKET-ID>` whenever it is passed a ticket ID. `[verified: claude-skills/skills/pr-description/SKILL.md:80]` GH-1119's "Closing PR" section says only F2's PR closes the issue, and tells authors to remove the `Fixes` line by hand until #1528 lands. `[verified: gh issue view 1119 --json number,title,state,body, run by the reviewing session]`
29. A harness-closure change after `freeze` invalidates the review bench's baseline and reruns every arm. A change between the smoke campaign and `freeze` forces a re-smoke. `[verified: .claude/plans/measure-review-quality.md:351, :364-368, :376]`
30. F2 sets each exception ceiling to the file's size when F2 lands, so F1's ceilings are proposals. `[verified: .claude/plans/code-file-size-splits.md:219]`
31. Seven tracked `.sh` files exceed 1,000 lines at `ab85a627`. Lines containing `<<` number 30 in `_lib.sh`, 6 in `install.sh` and 4 in `marker.sh`. `[verified: git ls-files -- '*.sh' filtered through wc -l, and grep -c, run by the reviewing session]`
32. `[engineer-verified: "Architect's set (Recommended)"]` The tag answers which report contents beyond GH-1119's list stay.
    - The question's own wording defined that set: the sensitivity figures, the near-limit table with Path and Physical columns only, and the ceiling-edit load with the window's total commit count added. The ten-largest-offenders share is dropped.
    - Before that selection the engineer typed "These all seem reasonable but check with the architect please", and a `plan-architect` consult recommended the set.
33. `[engineer-verified: "Proceed anyway"]` The tag answers `/plan-review`'s question on this plan's projected diff of about 410 changed lines against a threshold of 400. That answer was given at the about-410 projection. Row 47 answers the same question for this revision's projection.
34. `/ready-for-review` puts `Fixes GH-1119` back into the PR body on every run. `[verified: claude-skills/skills/ready-for-review/SKILL.md:42, :117; claude/.claude/scripts/pr-create-preflight.py:159-161, :216-218; claude/.claude/hooks/require-ready-for-review.sh:24-29, read this session]` The chain:
    - Its step 1 takes `<TICKET-ID>` from `pr-create-preflight.py ticket`, which returns the branch name's first segment when that segment matches the ticket pattern. This branch yields `GH-1119`.
    - Its step 5 passes that ID to `/pr-description`. With a PR open, `/pr-description` applies the body through `gh pr edit --body-file`.
    - A new commit moves HEAD, which invalidates the gate's completion marker and forces a rerun.

    The PR title derives as `GH-1119: f1 offender assessment`, which holds no closing keyword. `[verified: pr-create-preflight.py:169-184, :221-227]`
35. `claude/.claude/scripts/tests/test_cursor_adapter_translate.py` has 1,857 newline-terminated lines. `[verified: Grep line count, this session]` It holds a literal U+2028 at `:1721`, and `str.splitlines()` gives 1,858 lines. `[verified: the staff-sdet reviewer's probe at the pin]`
36. `c6fcd96e` renamed `claude-skills/skills/tests/test_skills.py` and `test_trigger_detector.py` inside the window. `test_skills.py` reads 76 window commits without `--follow` and 100 with it. `[verified: the platform reviewer's git log at the pin]`
37. `claude/.claude/hooks/deny-private-project-refs.sh` exits before its content scans unless `remote.origin.url` contains `claude-config` or `.claude/private-projects-optin` exists. `[verified: deny-private-project-refs.sh:27-28, :484-485, read this session]` Neither held when this plan was reviewed. `[verified: the reviewing session's git config --get remote.origin.url piped to grep -c claude-config, which printed 0, and its check for the opt-in file]`
38. Every new figure, own-history included, is checked against the grandfathered list before publishing. A figure that could narrow a grandfathered figure's residual goes to the owner in session. `[verified: docs/private-project-redaction.md:283-285, :304-314, :321-348]`
39. The hook's home-rooted-path detector catches a path rooted at a `Users` or `home` directory followed by a username segment, with the username drawn from `[A-Za-z0-9_.-]`. `[verified: docs/private-project-redaction.md:72, :76-77]` Its long-hex detector catches a contiguous hex run of 32 or more characters but not a short git SHA. `[verified: docs/private-project-redaction.md:73, read this session]`
40. At the pin, 22 files sit at 900–1,000 physical lines, one of them at exactly 1,000. One tracked symlink exists, which the mode filter excludes, and `git ls-files` piped through `wc -l` still yields 112 offenders. `[verified: the platform reviewer's runs at the pin]`
41. `[engineer-verified: "Report as build output (Recommended)"]` The tag answers which build shape this plan is rewritten to. Before that selection the engineer typed "Is all this extra instrumentation really necessary?", and a `plan-architect` consult answered no.
42. `[unverified]` The shape behind row 41's label is the consult's recommendation as the dispatching session summarized it, and neither text is the engineer's. This plan adopts it with two departures:
    - The recommended known-answer check of 1,857 lines for `test_cursor_adapter_translate.py` is dropped, because Verification step 2's `wc -l` cross-check already covers that file (row 35).
    - The recommended post-merge Status section pointing to GH-1113 becomes a Method sentence written before merge. A Status section records findings since closed (row 12), and filing an issue closes no finding.
43. `[engineer-verified: "After merge (Recommended)"]` The tag answers when the tracking issues for split-pending files are filed, relative to F1's PR.
44. `[unverified]` Rows 4 and 43 together place filing after merge, outside this plan. At merge, every split-pending row without an existing issue therefore reads `proposed (not filed)`. GH-1119's "the issue that tracks the split" is then met by a proposed issue until someone files it.
45. A dispatched `code-writer` or `general-purpose` agent can write under `<scratch>`. `deny-reviewer-tree-mutation.sh:24-28` exits for both `[verified: the platform reviewer's read]`, and `require-plan-review.sh:193-200` exempts `agent-reviews/` `[verified: read this session]`. D1's first write settles the rest `[unverified]`.
46. `[unverified]` The measuring program's output is identical across runs at a fixed pin, interpreter and git version, so a fresh render matches the report byte for byte. Verification step 6's first rerun settles it.
47. `[engineer-verified: "Proceed anyway"]` The tag answers `/plan-review`'s question on this plan's projected diff of about 430 changed lines against a threshold of 400.
48. `[engineer-verified: "Arm the hook locally (Recommended)"]` The tag answers the dispatching session's question whether to switch the redaction hook's content scans on in this worktree with an untracked opt-in file, or keep the plan's own path grep.
49. `[unverified]` How the hook is armed is the dispatching session's proposal, which this plan adopts. Verification step 7 settles whether the installed hook then arms here.
    - Outside `claude-config`, the hook arms while `.claude/private-projects-optin` exists under `git rev-parse --show-toplevel` at hook time, a linked worktree's own marker included. `[verified: deny-private-project-refs.sh:163-165, :470, :484-485; claude/.claude/hooks/tests/test_deny_private_project_refs.py:887-910, read this session]`
    - Armed, its tracker-ID deny names the marker, and its structural detectors scan gated command text. `[verified: deny-private-project-refs.sh:486, :866; docs/private-project-redaction.md:565, read this session]`
    - `git rev-parse --git-path info/exclude` names an exclude file every worktree of this checkout shares, so the entry outlives this worktree and hides only an untracked marker. `[verified: findings-path-suffix.sh:27-35, as precedent; the common git directory's info/exclude:7 holds that script's agent-reviews/ entry, read this session]`

## Critical files

- `docs/reports/<date>-code-file-size-offenders/findings.md` (new). This bullet is the one place `<date>` is fixed. It is the pin's committer date, which the measuring program prints as a `meta` record from `git log -1 --format=%cs <pin>`. The header's date, the README row's date and the end of the 90-day window all use it, so no later step reads the date from the clock.
- `docs/reports/README.md`: one index row, newest first (`:25-29`).
- `.claude/plans/f1-offender-assessment.md`: this plan.

Reuse:
- `compute_harness_closure()` at `evals/review_bench/analysis.py:749` for closure membership.
- Stdlib `tokenize`, `ast` and `subprocess`. The program imports nothing else.
- `scripts/venv-run.sh` to run Python from this linked worktree.
- The header shape of `docs/reports/2026-08-10-repo-quality-audit/findings.md:1-9`.

Never committed, all in `<scratch>`: `measure.py`, whose text the report's appendix carries, `render.py`, `prose.md`, the row-set files `rows-D<k>.txt`, the judgment files `judgments-D<k>.tsv`, and every intermediate file Verification names.

Dispatches. The session arms the redaction hook first (Publication checks). D1 runs next, then D2. D3, D4 and D5 then run in parallel. Each of the three writes only its own scratch file and no tracked file, so their writes never overlap. D6 runs last. Every dispatch passes `model: sonnet`.
- **D1, measurement** (`code-writer`). It writes and runs `measure.py`, runs the closure function, and writes `offenders.txt` and the four row-set files, each sorted with `LC_ALL=C sort`. It writes `prose.md` with every prose part of the report, Method's recorded results included, and a single `TBD-reading` line in place of the reading. It reads GH-1119's two lists with `gh issue view 1119`. Verification: steps 1–4.
- **D2, production verdicts** (`general-purpose`). Its row set is every offender not named `test_*.py`: production files, `helpers.py`, `conftest.py` and any test-support module. It reads #1015, #1396 and #1117 with `gh issue view`, and writes `judgments-D2.tsv`. Verification: steps 1 and 5.
- **D3, D4 and D5, test verdicts** (`general-purpose`, in parallel). Their row sets are the `test_*.py` offenders under `claude/.claude/hooks/tests/` and `claude-skills/` for D3, under `claude/.claude/scripts/tests/` for D4, and everywhere else for D5. Each reads `judgments-D2.tsv` for the modules its files test, and writes `judgments-D<k>.tsv`. Verification: steps 1 and 5, for its own `<k>`.
- **D6, render** (`code-writer`). It writes `render.py` and renders once to read the Figures table. It then replaces `TBD-reading` with the reading, renders again, copies the render to `<findings>`, and adds the README row. Verification: steps 1, 6 and 9.

Parts of `findings.md`, by estimated line count:
- 112 offender rows;
- 22 near-limit rows (row 40);
- 24 Figures-table and closure-list rows;
- 40 lines of table scaffolding, for eight tables;
- 140 lines of appendix;
- 45 lines of Method;
- 30 lines of reading, defaults and consequences;
- 15 lines of header and section headings.

Projected diff: about 430 lines, all additions. `findings.md` is 112 + 22 + 24 + 40 + 140 + 45 + 30 + 15 = 428, and `docs/reports/README.md` adds 1, for 429.

## Verification

Run from the worktree root. `<pin>` is the header's pin, `<findings>` is the report path Critical files defines, `<scratch>` is the directory the Renderer paragraph defines, `<n>` is the PR's number, and `<k>` is a verdict dispatch's number, from 2 to 5.

Steps 1–9 run before the first commit, in the dispatches Critical files names. Steps 2 and 3 also need the working tree to equal the pin, so they run only before any sync. Steps 10 and 11 check `origin/main` and the PR. Every `measure.py` run passes the Bash tool's maximum timeout.

1. **No counted file changes.** Run after every dispatch and before each commit.
   - `git diff --quiet <pin> -- '*.py' '*.sh'` exits 0. After a sync, `git diff --quiet HEAD -- '*.py' '*.sh'` replaces it.
   - `git status --porcelain --untracked-files=all` lists only the report's `findings.md`, `docs/reports/README.md` and this plan.
   - After a commit, `git diff --name-only origin/main...HEAD` lists only the three Critical files. The three-dot form diffs the merge-base against `HEAD`, so a sync does not trip it.
2. **Measurement.**
   - `scripts/venv-run.sh python3 <scratch>/measure.py <pin> > <scratch>/measure.tsv` exits 0.
   - `awk -F'\t' '$1 == "offender" {print $2 "\t" $3}' <scratch>/measure.tsv | LC_ALL=C sort > <scratch>/pin-physical.tsv` keeps each offender's path and physical count. The same command with `"near"` and `<scratch>/pin-near.tsv` keeps the near-limit records.
   - `git ls-files -z -- '*.py' '*.sh' | xargs -0 wc -l | awk '$2 != "total" && 1000 < $1 {print $2 "\t" $1}' | LC_ALL=C sort | cmp - <scratch>/pin-physical.tsv` exits 0, so the offender set and every physical count match `wc -l`. The same pipeline with the predicate `899 < $1 && $1 < 1001` and `<scratch>/pin-near.tsv` exits 0, so the near-limit set matches too.
   - `awk -F'\t' '$1 == "offender" && $2 == "claude-skills/skills/tests/test_skills.py" {print $5}' <scratch>/measure.tsv` prints 100 at `ab85a627` (row 36). At another pin, `git log <pin> --no-merges --follow --since=<window start> --format=%h -- claude-skills/skills/tests/test_skills.py | wc -l` gives the expected value, with `<window start>` taken from the `meta` records. This proves `--follow` and the window start for that one file.
   - `git rev-list --count --no-merges --since=<window start> <pin>` prints the window-total figure, and the ceiling-edit load figure is no larger.
   - Method records each result with what it proves.
3. **Closure.**
   - `scripts/venv-run.sh python3 -B -c "import sys; sys.path.insert(0, 'evals'); from review_bench.analysis import compute_harness_closure; print(chr(10).join(sorted(compute_harness_closure())))" > <scratch>/closure.txt` exits 0.
   - `grep -cxF -e evals/review_bench/runner.py -e evals/run_skill_evals.py -e evals/measure_subagent_model_resolution.py -e evals/review_bench/analysis.py <scratch>/closure.txt` prints 4.
4. **Partition.**
   - `awk -F'\t' '$1 == "offender" {print $2}' <scratch>/measure.tsv | LC_ALL=C sort > <scratch>/offenders.txt` lists the offenders.
   - `LC_ALL=C sort <scratch>/rows-D2.txt <scratch>/rows-D3.txt <scratch>/rows-D4.txt <scratch>/rows-D5.txt | cmp - <scratch>/offenders.txt` exits 0, so every offender sits in exactly one row set.
5. **Judgments**, after each verdict dispatch.
   - `awk -F'\t' 'NF != 5' <scratch>/judgments-D<k>.tsv` prints nothing.
   - `cut -f1 <scratch>/judgments-D<k>.tsv | LC_ALL=C sort | cmp - <scratch>/rows-D<k>.txt` exits 0.
6. **Render**, in D6 and before each commit.
   - Step 2's first command runs again. It reads blobs at the pin, so it stays valid after a sync.
   - `scripts/venv-run.sh python3 <scratch>/render.py <pin> <scratch> > <scratch>/findings.rendered.md` exits 0.
   - `cmp <scratch>/findings.rendered.md <findings>` exits 0. D6's copy makes the first run pass, and every later run proves the report is still a fresh render. It proves freshness, not correctness.
   - ``awk -F' *[|] *' '/^## /{t = /^## (Structural exceptions|Split pending)/} t && $3 ~ /^[0-9]+$/ {gsub(/`/, "", $2); print $2 "\t" $3}' <findings> | LC_ALL=C sort | cmp - <scratch>/pin-physical.tsv`` exits 0, so the verdict tables hold each offender once, with its measured physical count.
   - `grep -c 'TBD-' <findings>` prints 0.
7. **Hook armed**, once, before the first commit. From the worktree root, pipe `bash_input`'s JSON (`claude/.claude/tests/helpers.py:1067-1080`) into the installed `deny-private-project-refs.sh`, as `run_hook` does (`:592`). Its command is a `gh pr edit` whose title holds a tracker-shaped token off the hook's `OSS_ALLOWLIST`, typed only into this call. The hook denies, naming `.claude/private-projects-optin`.
8. **Composition**, before the first commit. For each new figure category, check whether the figure could narrow a grandfathered figure's residual, as `docs/private-project-redaction.md` § "New figures against the grandfathered set" requires. The categories are physical and non-comment line counts, per-file 90-day commit counts, the window's total non-merge commit count and the ceiling-edit load.
   - `<scratch>/pr-records.md` and the PR description record the result per category.
   - A category that could narrow one goes to the owner in session before anything publishes.
9. **Tests.** `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py`.
10. **Drift**, before each push.
    - `git fetch origin main`.
    - `scripts/venv-run.sh python3 <scratch>/measure.py origin/main > <scratch>/measure-main.tsv` exits 0.
    - `awk -F'\t' '$1 == "offender" {print $2 "\t" $3}' <scratch>/measure-main.tsv | LC_ALL=C sort > <scratch>/main-physical.tsv` keeps the same two columns.
    - `diff <scratch>/pin-physical.tsv <scratch>/main-physical.tsv` prints the drift. `<scratch>/pr-records.md` and the PR description list each path it prints as added, removed or resized.
11. **No closing reference**, as the last action after the final body sync, and again after any later sync. Here `<pattern>` is `(^|[^[:alnum:]])(close[sd]?|fix(e[sd])?|resolve[sd]?):?[[:space:]]+[^[:space:]]*1119([^0-9]|$)`.
    - `gh pr view <n> --json closingIssuesReferences --jq '.closingIssuesReferences[].number'` does not print 1119 (row 20).
    - `gh pr view <n> --json title,body --jq '.title, .body' | grep -iE '<pattern>'` prints nothing. For a PR of more than one commit, the title becomes the squash commit's subject (row 19).
    - `git log origin/main..HEAD --format=%B | grep -iE '<pattern>'` prints nothing.
    - The body still holds the drift list and the composition-check record.

## Out of scope

- **Editing `docs/design-decisions/code-file-line-limit.md`.** That covers its stale `_lib.sh` size at `:43` and `:79` and its wrong test path at `:78`. The engineer left these to F2 (row 6).
- **The size check, its `select-tests.py` exception and the exception table it reads.** These belong to F2, #1119's closing PR.
- **Splitting any offender.** Each split runs under its tracking issue, on the closure windows for the four closure files.
- **Re-measuring at F2's landing, or changing when F2 sets ceilings.** F2 sets each ceiling when it lands (row 30). That timing belongs to the epic plan's design for F2. F1 proposes ceilings into that design but does not revise it.
- **Changing the review bench's closure-invalidation rule.** The rule lives in this repository's own bench code and A's approved plan (row 29), so it is in reach. The engineer's constraint (row 1) assumes it, and changing it would reopen #1114's approved design.
- **Revising `code-file-size-splits.md:219`'s "live in F1's plan file".** Row 3 sets the wording aside for F1 rather than editing another plan's record.
- **Changing `/pr-description`'s closing-keyword default.** The skill is in this repository, so the fix is in reach (row 28). It is a skill change with its own review pipeline, already tracked as #1528. F1 strips the `Fixes` line after each body sync instead.
- **Fixing the redaction hook's scope for every contributor, or raising that as its own issue.** The hook keys its scans to an origin URL this checkout does not match, or to an opt-in marker (row 37). The untracked marker arms only this worktree (row 49).
- **Posting any declined-row reason to the tracker.** The reason lives only in the report row, whose text the engineer approves as the passing render prints it, before the commit that introduces it.
- **Settling #1015's gates, #1117's go/no-go, or how #1396 relates to #1015.** Those decisions belong to the engineer on those issues. The report only cites them.
- **Files without a `.py` or `.sh` suffix.** GH-1119 scopes the assessment to those two suffixes, so an extensionless executable over 1,000 lines goes unmeasured.
- **Filing the proposed issues, and editing GH-1113's task list.** The engineer placed filing after merge (row 43), so it happens outside this plan. In its place the report carries:
  - `proposed (not filed)` as the last cell of each split-pending row with no existing issue;
  - Method's issue template, which gives each issue's title, body and closing condition;
  - Method's sentence naming GH-1113's task list as where filed issues are listed.

  The checks that belong to filing go with it, such as a title search before each create and the choice of filing account. A dated Status section may later record findings since closed, the one edit `docs/reports/README.md:8` permits after merge (row 12).
