# Measure whether cumulative-pass findings land on lines the previous fix wrote

## Context

Goal: measure, with exact line overlap, whether `/ready-for-review`'s cumulative-pass findings after a fix land inside the lines that fix wrote, so the engineer can decide whether fix-written prose is a real source of review-loop churn worth a rule change.

Ask: "Ok how do we do the mesasurement?", then "yeah sounds good go for it" in reply to the session's proposal (the session's words, not the engineer's) to plan the cumulative-pass half as phase 1, with a staged-round half only if phase 1 is ambiguous or points at staged rounds.

Why now: the engineer asked how to reduce review churn coming from prose written in the previous round. A session proposal to make `code-writer` fixes prefer deleting or rewording prose over adding it went to an adversarial `plan-architect` consult, which recommended rejecting it pending a measurement. The consult's ground was that the premise is unmeasured: GH-1207's scans (`docs/cost-levers-considered.md` § "From `review-loop-cost-levers.md`") found placeable findings on dirty cumulative re-passes outside the fix delta outnumbering those inside it, using heuristic token placement with no reviewer breakdown. This plan replaces that placement with exact line overlap, broken down by reviewer and by prose vs code.

Intended outcome: readings, computed by a method fixed before the run, from which the engineer decides whether in-delta prose findings (excluding `comment-discipline-reviewer`, whose in-delta findings are a first read of new prose rather than churn) land inside the fix more often than the fix's share of the branch's prose predicts. The engineer's decision chooses between stopping, a follow-up plan, or more data, such as the staged-round phase. Which reviewer accounts for them reaches the engineer in session.

## Approach

A local, uncommitted script pairs consecutive `/ready-for-review` cumulative passes on each branch and takes the commits between them as the fix. It reads only R-format findings files. A later pass's finding is inside the fix when `git blame` at that pass's branch tip attributes a cited line to one of those commits. The script measures whether prose findings land inside the fix more often than the fix's share of the branch's added prose lines predicts. Before any result is opened, the engineer hand-checks a short seeded sample in their own viewer, so no dispatched agent reads corpus text and the session reads only items the engineer rejects. This plan fixes the method, not a decision rule, and is committed before the run. The engineer then decides to stop, to act, or to gather more data, choosing among labels this plan fixes, from the readings, a fixed list of known leans, and the session's own recommendation marked as its own (rows 77, 78, 84). A later, separate commit records that decision in a new `docs/cost-levers-considered.md` section, with which way each reading pointed and the engineer's decision. No code, skill, hook, or test ships.

**Pairing passes.**
- **Corpus.** Every `agent-reviews/` directory at the root of a worktree that `git worktree list --porcelain` lists, nested worktrees included (row 39). Files group into rounds by their shared `<epoch>-<slug>` suffix (rows 12, 13). Reviewer files are those whose agent's definition carries a `### File-based output` section (row 13).
  - A reviewer file's name parses as a reviewer name from those definitions, matched longest first, then the digits after it as the epoch, then the rest as the slug. Disposition records and ledger digests parse after their literal `code-review-dispositions-` and `review-ledger-` prefixes.
  - A file counts only when `lstat` finds a regular file. A worktree drops its whole directory when its `agent-reviews/` is a link, or when `ls-tree` finds an `agent-reviews/` path in its HEAD commit's tree (row 70).
- **Branch.** A slug resolves to the one branch `for-each-ref refs/heads/` lists whose name, under `findings-path-suffix.sh`'s sanitizing rule and length cut, equals it. The script re-implements that rule rather than running the script (row 34). A slug matching one branch attaches its rounds to it, even where a deleted branch once shared that slug (rows 65, 66). A slug matching no branch, or several, drops its rounds (row 18).
- **Base.** A tip's base is its merge-base with `refs/remotes/origin/main`, the ref the reviewed diff was taken against (M10). A branch drops as stacked when its reflog has no creation entry, or when the commit that entry names is not an ancestor of `refs/remotes/origin/main` (row 59).
- **Cumulative pass.** A round with a `code-review-dispositions-<suffix>.md` record whose H1 line and `scope:` lines (case-insensitive) name no staged or hunks-only scope (rows 14, 17, 39). A round with no record is never a cumulative pass.
- **Staged round.** A round with a `review-ledger-<suffix>.md` digest and no disposition record (M11). A round with neither file is of neither kind and counts toward nothing.
- **Dirty, in this plan.** A cumulative pass is dirty when its record holds an ADDRESS row (rows 15, 62). A staged round is dirty when one of its reviewer files holds a finding heading: an R heading, or, in an older file, an H2 other than `## Answers` and row 20's non-finding titles.
- **Tip at a round.** The newest entry at or before the suffix epoch in `reflog show --date=unix refs/heads/<branch>`, never in HEAD's reflog. Every worktree shares that reflog under the common git dir's `logs/refs/heads/<branch>` (M2). Of two entries in one second, the one listed later wins. A round whose record names a `HEAD <sha8>` other than that entry's commit is unresolved (row 76). With no reflog entry, a named sha8 is used. With neither, the round is unresolved.
- **Measured pair.** Two cumulative passes on one branch with no cumulative pass between them, where all of these hold (rows 15, 24, 32, 39):
  - the earlier pass is dirty, so a fix was dispatched;
  - both tips resolve, and the earlier tip is an ancestor of the later;
  - the window, `git rev-list --first-parent --no-merges <earlier tip>..<later tip>`, is non-empty;
  - every window commit's author date falls between the two epochs, per `.claude/plans/clean-pass-criteria.md`'s window rule;
  - every commit the earlier record's Outcome cells name is a window commit;
  - every reviewer file of the later pass is R-format (M3).

  The script counts what each slug, directory, stacked, and pair condition drops, each kind apart.

**Findings and citations (M3).** The unit is a finding (row 37). A file is R-format when it holds at least one `## R<n> [<severity>] <angle>` heading, or no H2 other than `## Answers` (rows 5, 16). Each R heading opens a finding, and any other H2 closes it. An H2 that starts with `R` and a digit but breaks that grammar makes its file not R-format.
- A finding's citations are each `path:N`, `path:N-M`, and `path:N,M` in its section, and a range cites every line in it. A path-less `:N` or `:N-M` takes the finding's previous path.
- Path resolution happens at the later tip, first match wins:
  1. the path as written;
  2. `~/.claude/skills/` mapped onto `claude-skills/skills/`, and any other `~/.claude/` path onto `claude/.claude/`;
  3. a basename unique among files the branch changed since the later tip's base;
  4. a basename unique in the tree.
- Unplaceable citations (row 21):
  - an approximate line (`~`);
  - a `HEAD:path:N` citation of pre-change text;
  - a diff position;
  - a prose "lines N-M";
  - a path resolving to no tracked file, or to several;
  - a line past the file's end.
- **Placement (M1).** A placeable citation is inside when any cited line's blamed commit at the later tip is a window commit.
  - **Inside:** the finding has an inside citation.
  - **Outside:** it has none, and every citation is either placeable or resolves to a file no window commit touched. The script reports the untouched-file cases apart from the placed ones.
  - **Unplaceable:** anything else, including a finding with no citation.
- **Re-raise candidate (rows 61, 68).** An inside finding with an inside citation in a hunk of `git diff -U0 <earlier tip> <later tip>` whose old side holds a line some earlier-pass finding cites, or, for a pure addition, directly follows one. Earlier-pass citations resolve at the earlier tip. Where an earlier-pass reviewer file is not R-format, every inside finding on the later pass is a candidate. The script reports candidates apart.
- **Prose,** fixed before the run (row 37): lines of `.md` and `.txt` files, Python docstrings, and `#` comment lines in Python, shell, YAML, and TOML files other than a shebang. Everything else is code. A finding that resolves no file, one with no citation included, is unclassifiable. Otherwise it is prose when every line it places is prose, or, where it places no line, when every file it resolves is a `.md` or `.txt` file. It is code under the same rules for code, and unclassifiable otherwise. Skill and agent bodies count as prose, though they are behavioral instructions.

**Readings (M5).** They compute in exact fractions (`fractions.Fraction`), so a tie is exact. They carry no sampling bound, so each carries a direction only, whatever the number of pairs behind it (row 44). No rule turns them into a decision (row 78).
- **Population:** findings on the later pass of each measured pair that are prose or unclassifiable, from the gate reviewers: every reviewer except `comment-discipline-reviewer` (row 35) and `skill-fidelity-reviewer` (row 21).
- **Base rate per pass:** among the prose lines the cumulative diff adds, from the later tip's base to the later tip, the share whose blamed commit is a window commit. A pass adding no prose line has a base rate of zero. Its **expected count** is its population size times its base rate.
- **Three readings,** each enriched, not enriched, or undefined (row 67):
  - finding level: inside findings, summed over passes, exceed the summed expected count;
  - pass level: over passes with a non-empty population, the mean of inside share minus base rate is positive;
  - branch level: over branches with a non-empty population, the mean of inside findings minus expected count, divided by population size, is positive.

  A pass with an empty population, such as a clean later pass or one holding only excluded reviewers' findings, adds nothing to any reading. A reading with no pass or branch to average is undefined. A tie, or a mean of exactly zero, is not enriched.
- **Ceiling:** later passes that are dirty, hold a parsed finding, and on which every finding, of any class and from any reviewer, `comment-discipline-reviewer` included, is inside. Each is a round a fix-hygiene rule could have saved (rows 35, 62, 81).
- **Two assignments (row 37).** Each uncertain finding takes the value that hurts enrichment under the adverse assignment, and the value that helps it under the favorable one.
  - Adverse: an unplaceable prose finding is outside. An unclassifiable finding is prose and outside when it is outside or unplaceable, and is left out when it is inside. A re-raise candidate is left out. For the ceiling, every unplaceable finding and every re-raise candidate is outside.
  - Favorable: an unplaceable prose finding is inside. An unclassifiable finding is prose and inside when it is inside or unplaceable, and is left out when it is outside. A re-raise candidate stays inside. For the ceiling, every unplaceable finding is inside.
- **Raw reading** (rows 53, 54): inside against outside over every placeable finding from the gate reviewers, of any class, with the untouched-file outside cases included and no assignment applied. Re-raise candidates count as inside. It also feeds the section's cross-reference to the narrowed-pass row.
- **Reviewer ranking,** for the session report only (rows 49, 50): each gate reviewer's inside findings minus its expected count, under the adverse assignment. A reviewer's expected count is its population size on each pass times that pass's base rate, summed. Reviewers tied for first are named together.
- **Staged-round trigger** (rows 9, 10, 45). A repeat staged round is a dirty staged round whose branch's next round is also a staged round at the same tip. The trigger fires when repeat staged rounds outnumber measured pairs.

**Report and decision.**
- **Report.** In a single reply, the session gives the engineer `aggregate.txt`'s readings under both assignments, ceiling, raw reading, trigger, and ranking, the hand-check outcome with its "other line" tally, and the known leans. Beside them, it prints from `aggregate.txt` (rows 44, 67):
  - for each reading under each assignment, the measured pairs and the passes or branches it summed or averaged over, and at the finding level the inside and expected sums;
  - every drop count, and the findings each assignment left out;
  - beside the ceiling, the count of dirty later passes holding a parsed finding, and each one's finding count (row 86);
  - beside each reviewer's excess, its population size, inside count, unplaceable count, and citations per finding (row 87);
  - which hand-check calls the drawn items exercised, such as whether the draw held a re-raise candidate or an unclassifiable finding.

  These figures stay in session, and none enters `verdict.txt`. The session says which way the readings and leans point. After the readings and leans, it states which option it would pick and why, labelled as its own recommendation (rows 84, 85). That recommendation enters neither `verdict.txt` nor the register.
- **Known leans,** fixed before the run, each reported with the direction its row states:
  - toward stop: rows 42 and 67;
  - toward act: rows 68 and 80;
  - toward an all-inside pass: rows 81 and 86;
  - in the ranking, by reviewer: citation width (row 80), and the adverse charge on unplaceable findings and re-raise candidates and volume under a difference (row 87);
  - not bracketed by the two assignments: the branch-level reading (row 68);
  - the trigger, both ways: row 64;
  - either way or unknown: rows 24, 66, 69, and 76.
- **Decision.** Through `AskUserQuestion`, the engineer chooses among these fixed decision labels (rows 5, 9):
  - `Stop: no change pursued`, for `stop`;
  - `Act: plan a follow-up`, for `act`;
  - `More data: <source>`, for `more-data`, offered once for each source on a fixed list: `the staged-round measurement` and `older findings files`.

  No label carries a recommended mark, and a typed answer gets the same question again, so the register quotes a fixed label and nothing the session composed. The session then appends `verdict.txt`'s `decision` line. On act, the follow-up plan may name the reviewer the ranking puts first (row 79), and says that whose suggested fix wrote those lines was not measured (row 26). That plan carries the reviewer's name and no figure, count, range, rank, or share-like descriptor, and Verification 6's rank-word screen, name check, and known-answer checks run over it before its commit, since row 79's tag covers the name only. The name is publishable under `docs/private-project-redaction.md`'s allowance to record "which lever was adopted", because the lever is a change to that reviewer's wording (rows 93, 94). The work dir stays until that plan is committed, because `rows.tsv` is the only home of the per-reviewer data it needs (row 55).

**`verdict.txt` (M12).** One `<field>: <phrase>` line per field, from this vocabulary alone. `run` writes every field but `decision`, which the session appends once the engineer decides:
- `finding-level`, `pass-level`, and `branch-level`, each suffixed `-adverse` and `-favorable`: `enriched`, `not-enriched`, or `undefined`;
- `raw-reading`: `outside-outnumbers`, `inside-outnumbers`, `tie`, or `undefined`;
- `staged-trigger`: `fired` or `not-fired`;
- `decision`: `stop`, `act`, or `more-data`.

It never holds a figure, a reviewer name, the ranking, or the ceiling. Whether any later pass was all-inside goes to `aggregate.txt` and the session report only (row 91). No mapped text consumes `staged-trigger`, so the session keeps it and does not hand it to dispatch 2.

**Procedure.** Dispatches follow Critical files.

1. The session runs Verification 6's known-answer check, then screens this whole plan and fixes any failing hit. It then commits this plan, which fixes the method, before the script reads any corpus file (M9).
2. The session creates the work dir with one literal call, `mktemp -d ~/.local/state/fix-delta-churn.XXXXXX`, and spells out the printed path in every later command (row 30). It first writes that path to `workdir.txt` there, and the plan commit's SHA to `plan-sha.txt`. Every study output stays there until the decision is acted on, or, on act, until the follow-up plan is committed (rows 39, 55), except as step 10's retention rule sets.
3. **Build.**
   - The session writes `fixture-answers.txt`, describing the end-to-end fixture corpus file by file and commit by commit. It holds the hand-derived expected values for that fixture and for every readings, base-rate, and null fixture.
   - Dispatch 1 writes `measure.py`, and runs no subcommand but `selftest`. A denied write stops the study and goes to the engineer, with no retry by another agent type. On its return, the session lists the work dir and confirms that no `rounds.tsv`, `rows.tsv`, `sample.txt`, `aggregate.txt`, or `verdict.txt` exists. A listed output goes to the engineer.
   - The session, not the script's author, audits `measure.py` with `Grep`, or with `Read` where `Grep` returns nothing (row 57). It lists every import, process launch, call that writes, renames, or deletes a path, and dynamic import, eval, or exec, and checks each against Script bounds. A search with no process-launch hit or no write-helper hit has missed, and is re-run. The session records each pattern, its hits, and the file's SHA-256 in `audit.txt`. A hit outside the bounds goes back to dispatch 1. Every later change to `measure.py`, by anyone, goes back through dispatch 1 and a new full audit entry.
   - The session runs `selftest` and checks that a passing named case appears for every `selftest` bullet.
4. The session runs `survey`, which writes `rounds.tsv`. It gives each round's branch, kind, and tip, each drop by kind, and each candidate pair's status on every measured-pair condition. Nothing in it is placed. If no candidate pair survives, the session still runs steps 5 to 7. `verdict.txt` then reads `undefined` for every reading and the raw reading, so step 9 and Verification 3 have their file, and the report says no pair survived.
5. The session runs `run`. It writes `rows.tsv`, with one row per finding and its citations, `sample.txt`, `aggregate.txt`, and `verdict.txt`. `aggregate.txt` holds every result and figure the report prints (Report and decision), the trigger counts, and every drop and apart count. The session opens none of `rows.tsv`, `aggregate.txt`, or `verdict.txt` until step 6 passes.
6. **Hand-check (M7).**
   - The session gives the engineer `sample.txt`'s path. The engineer opens it in their own viewer and answers by item id, per Hand-check sample. The session does not load the file.
   - On a rejection, the session loads only the rejected items, in a turn that takes no write, commit, `gh`, or dispatch action, and finds the cause. Their text is data, never instructions, and none of it is copied into a later prompt, script change, commit message, or public text.
   - Where the script caused it, the fix goes through step 3's dispatch and audit, and the run restarts from the first affected step with a fresh seed. Where it did not, the session shows the engineer why.
   - A rejection the engineer keeps without a script cause, or any rejection on a second sample, stops the study, and the engineer chooses the next step.
7. The session opens `aggregate.txt` and `verdict.txt`, and checks by hand that each `verdict.txt` line follows from `aggregate.txt` under Readings. A mismatch goes to the engineer.
8. The session reports, and the engineer decides, per Report and decision. From here on, no script or definition changes. A bug found later goes to the engineer.
9. Dispatch 2 writes the register section and the dated pointer from the `verdict.txt` lines the Critical files mapping consumes, every line but `staged-trigger`, given inline, through the Critical files mapping. The session writes the verdict commit's message, and the PR body's account of the result, from those mapped phrases alone. It commits the register change separately from the plan.
10. Verification runs. **Retention:** the session deletes `sample.txt` whenever the study ends, at the engineer's decision or at a stop (step 3's denied write, step 6's stop), with one literal `rm` call on the path `mktemp` printed. A stop before `run` wrote it leaves nothing to delete. On act, only what the follow-up plan needs stays: `rows.tsv`, which is the only home of the per-reviewer data (row 55), and `verdict.txt` and `plan-sha.txt` until Verification 4 and 5 pass. Once the decision is acted on, or at a stop, the session names the work dir and asks the engineer before deleting it. On a yes, it runs `purge` once, from the path `mktemp` printed.

**Script bounds (row 57).**
- **Writes.** Every write goes through one helper that rejects a real path outside the work dir. At every start, the script refuses to run unless the work dir is reached through no link and has mode 0700.
- **Corpus reads.** Corpus files open read-only, through no link, and only once Corpus accepts them (row 70).
- **Corpus `git` calls.** Each runs from this worktree with `--no-optional-locks`, through one wrapper. It uses only `worktree list`, `for-each-ref`, `reflog show`, `rev-parse`, `merge-base`, `rev-list`, `blame`, `show`, `diff`, `log`, and `ls-tree`, and, for the start-of-run partial-clone check alone, `config --local --get-regexp` with a fixed key pattern. It never uses `-C`, never changes into another worktree, and never fetches (rows 30, 57). It runs with `GIT_NO_LAZY_FETCH` set to `1`, and the script refuses to run at every start unless that config call exits 1 with empty stdout and empty stderr. Exit 0 with a key printed, any other exit code, any stderr output, and a timeout each refuse (row 89). Each argument holds to this grammar:
  - a revision is a full SHA that `git` itself printed, or a record's sha8 that is lowercase hex and that `rev-parse --verify` resolves to a commit;
  - a branch is a `refs/heads/` name that `for-each-ref` printed;
  - a path is one that `ls-tree -r` at that revision lists, and it always follows `--`;
  - any argument starting with `-` must be on the wrapper's literal flag list, which excludes `--output`, and a `-L` range is `<n>,<m>` in decimal integers.
- **Environment.** Every `git` call, the fixture builder's included, runs with every inherited `GIT_*` variable removed, and with global and system config nulled (row 71).
- **Timeouts.** Every `git` call runs under a timeout whose value and basis dispatch 1's prompt states, as a local bound for a one-off scan rather than a protocol value (row 57). A timeout stops the subcommand with a non-zero exit.
- **Fixture builder.** It is a separate path from the wrapper, and the only one that runs a mutating `git` command. It runs with its working directory set to its fixture repo, with hooks and commit signing disabled. Before every mutating call, it asserts that `rev-parse --show-toplevel` resolves inside the work dir.
- **Invocation.** The session runs each subcommand as one literal `python3 -I -B <work dir>/measure.py <subcommand>` call, in the background when it may outlast a foreground call. `survey` and `run` refuse to start unless `measure.py`'s SHA-256 matches the newest hash in `audit.txt` (row 57).
- **`purge`** takes no argument. Its target is the directory holding the running `measure.py`. It refuses unless that directory is reached through no link, has mode 0700, and sits directly under the fixed `~/.local/state/` base, with a name starting `fix-delta-churn.`. It takes the expected parent from that base, never from a file inside the directory. It never globs.
- **Agents.** No dispatched agent reads a corpus file, and corpus text reaches the session only as rejected `sample.txt` items (M7).

**`selftest` known answers.** `selftest` reads no corpus file. It prints one named case per bullet below, and exits non-zero on any failure. The readings, base-rate, null, and end-to-end fixtures take their expected values from the session's `fixture-answers.txt`, never from the script.
- **Placement and pairing,** in a fixture repo the fixture builder makes inside the work dir:
  - a line one fix commit added and a second fix commit shifted is inside;
  - an untouched line, a line a merge of `main` brought in, and the line beside a deletion are outside;
  - a range touching one fix-written line is inside;
  - an approximate line in a file no window commit touched is outside, reported apart;
  - an amended pass pair drops as rewritten, and a pair with no commit between its passes drops as an empty window;
  - a rebased fix commit keeps its author date and stays in the window, and a window holding a commit authored before the earlier epoch drops;
  - a record naming a commit outside the window drops its pair, and so does an earlier record with no ADDRESS row;
  - a hand-computed base rate on a pass mixing Markdown, comment, docstring, and code lines;
  - a base rate where local `main` trails a merged `origin/main` excludes main's lines, and a stacked branch drops;
  - a re-raise candidate on a hunk replacing an earlier-cited line, another on a hunk rewriting the middle line of an earlier-cited range, another on a pure addition directly after one, and an inside finding elsewhere that is not one.
- **Reflog,** over the fixture repo's own branch reflog, which holds real commit, amend, rebase, reset, and merge entries, and two entries in one second:
  - an epoch between two entries takes the earlier, and an epoch before the first takes the record's sha8 where one resolves, and is otherwise unresolved;
  - of two entries in the epoch's second, the one listed later wins;
  - where HEAD's reflog differs from the branch's, the branch's decides;
  - a record's `HEAD <sha8>` naming another commit than the branch reflog's entry leaves the round unresolved.
- **Corpus and names:**
  - discovery finds a nested fixture worktree, and drops a linked findings file and a worktree whose HEAD tree tracks an `agent-reviews/` path;
  - names with a digit-bearing slug and hyphenated reviewer names parse to the right epoch and slug;
  - sanitizing handles `/`, a character outside `[A-Za-z0-9-]`, and the length cut;
  - a no-match slug and a several-match slug each drop, counted apart from pair drops, and a surviving branch takes a deleted same-prefix branch's rounds.
- **Parser:** an R-format file with `## Answers`, a file with no H2, a file with R headings and a non-R H2, a file with a malformed R heading, each unplaceable shape, a path-less continuation, a `~/.claude/` path, and an ambiguous basename. The malformed file drops its round's pair, and the R-format test applies to every reviewer file in a round.
- **Kinds:** each H1 shape row 17 names, a record naming a staged scope, an ignored body line mentioning "staged", and a round with no record. A round with a digest and no record is staged, and is dirty by its finding headings. A plan-review-shaped round and a skill-fidelity round, each with no record and no digest and repeated at one tip, count toward nothing.
- **Prose:** a Markdown line, a Python comment, a Python docstring, a shell comment, a shebang, a JSON line, a YAML comment, and a finding mixing prose and code lines and a finding with no citation, both unclassifiable.
- **Readings:**
  - a tie at the finding level, and a mean of exactly zero at the pass and branch levels, are not enriched;
  - a null fixture, with inside findings at their expected count at small base rates, gives each reading the value `fixture-answers.txt` holds;
  - an empty-population pass, and a pass holding only excluded reviewers' findings, add nothing to any reading, and a reading with nothing to average is undefined;
  - an unclassifiable finding and a re-raise candidate under each assignment;
  - a later pass with every finding inside counts toward the ceiling only when its record holds an ADDRESS row, and a dirty pass with no reviewer file does not count;
  - the raw reading on a case where it and the finding level point different ways;
  - a reviewer ranking where raw counts rank the other way, and a tie for first.
- **End to end:** `survey` and `run` over one fixture corpus of repo, records, and findings files write the `aggregate.txt` and `verdict.txt` that `fixture-answers.txt` holds, and a `sample.txt` holding the items `fixture-answers.txt` lists. The corpus holds a dirty later pass with every finding inside, which the sample shows whole, a finding of each placement outcome, a `comment-discipline-reviewer` finding, an unplaceable `skill-fidelity-reviewer` finding, an unplaceable prose finding, an unclassifiable finding, a pass adding no prose line, a pass with an empty population, a re-raise candidate, a finding-level reading at an exact tie, and a second branch whose branch-level term has the other sign. The excluded reviewers' findings move the ceiling, but not the population or the expected count.
- **`verdict.txt`,** as `run` writes it, holds exactly one line per field but `decision`, each with an allowed phrase. Over a fixture corpus with no surviving pair, `run` still writes it, with `undefined` for every reading and the raw reading.
- **`sample.txt` escaping:** each of a planted escape byte, a bidi override, and a C1 byte, in a fixture commit subject and in a finding's section text, appears in `sample.txt` as a visible escape, while newline, tab, and a printable non-ASCII letter pass through.
- **`sample.txt` prefixing:** a line shaped like an evidence field, such as an item id or a blamed sha8 with its placement, planted in a finding's section text and in a fixture commit subject, appears in `sample.txt` behind the fixed prefix, and no line the script wrote starts with it.
- **Deny cases:**
  - the wrapper refuses a subcommand off its list, `-C`, `--output`, and another off-list argument starting with `-`;
  - a malformed sha8, an unlisted path, and a non-integer `-L` range are refused before `git` runs;
  - the corpus open refuses a link and a write mode;
  - the fixture builder refuses a toplevel outside the work dir;
  - with `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, and `GIT_CONFIG_GLOBAL` set to a decoy repo inside the work dir, the fixture builder still builds its fixture, and neither the decoy's nor this repository's HEAD, refs, or index changes;
  - the corpus `git` wrapper, not only the fixture builder, run in a fixture repo with a decoy `GIT_DIR` and a planted `GIT_EXTERNAL_DIFF` inherited, ignores both: its output is the fixture repo's, and the planted program never runs;
  - the start-of-run check refuses a fixture repo whose config sets `extensions.partialClone` or `remote.<name>.promisor` (row 89), and refuses when the config call itself errors, such as with an invalid option, since only exit 1 with empty output passes;
  - the write helper refuses an outside path, and a link inside the work dir that points out of it;
  - `survey` and `run` refuse a `measure.py` whose hash is not the newest in `audit.txt`;
  - the start-of-run check refuses a linked work dir and a mode other than 0700, and `purge`'s check refuses both and a name without its prefix.
- The trigger count over a fixture round list.

**Hand-check sample (M7).** `run` draws with a seed it records and writes `sample.txt`, which gives each item an id and holds no aggregate. It writes every character that is not printable, other than newline and tab, as a visible escape. A printable character has a Unicode general category of letter, mark, number, punctuation, or symbol, or is the ASCII space. The escape set therefore includes format characters such as bidi overrides and zero-width characters, line and paragraph separators, C1 controls, and DEL (row 88). Every line of corpus text (section text, a cited line's text, a commit subject) goes on its own line behind the fixed prefix `| `, and no line the script writes starts with it. The engineer opens `sample.txt` as plain text, so the escape keeps corpus characters from altering the display, and the prefix keeps a corpus line from reading as one of the script's own evidence lines. Where the corpus has them, it draws:
- two findings from the gate reviewers for each placement outcome: inside, placed outside, untouched-file outside, and unplaceable;
- two dirty later passes whole, taking adverse-ceiling passes first, so a reported all-inside pass rests on a checked one. For each, `sample.txt` first lists every H2 line of each reviewer file, marked as opening a finding or not, with the file's line count.

The engineer runs no `git`, so for each drawn finding `sample.txt` shows the evidence itself:
- its findings file and heading line number, and its section text up to the next H2, since an R heading names only an angle;
- each citation as written, then its resolved path and line at the later tip, that line's text, and the sha8 and subject of the commit `blame` attributes it to, or else the unplaceable shape it matched;
- its pair's window commits, each as sha8 and subject, marked where the earlier record's Outcome cells name it, and, for an untouched-file outside finding, every path those commits touched;
- the later pass's record H1 and `scope:` lines, whether the earlier record holds an ADDRESS row, and the source of both tips: the reflog alone, the reflog matching the record's sha8, or the sha8 alone;
- the script's placement, class, and re-raise flag, and for a candidate the earlier-pass citation it rests on.

The engineer confirms an item when all of these hold, and rejects it otherwise (row 75):
- the resolved path is the file the citation names, and the shown line is the one the finding describes or quotes. Where that text sits on another line of the same file, the engineer marks the item "other line" instead of rejecting it on that ground (row 69);
- the placement follows from whether a blamed sha8 appears in the window list, an untouched-file citation's path is absent from the window's paths, and an unplaceable citation has the shape shown;
- the class follows from each cited line's file type and whether it is a comment, docstring, or document line;
- the kind call follows from the record lines shown, the earlier pass is dirty by an ADDRESS row, and a re-raise flag follows from the earlier-pass citation shown;
- for a whole pass, the H2 lines marked as findings are exactly its findings.

**Record and publication.** The record is the new register section and dated pointer Critical files specifies, as the register's own convention provides, with no design record (rows 7, 8, 27, 40; M8). The scan is ad hoc, so under row 46 nothing published carries a count, ratio, range, or share-like descriptor (row 29). Beyond direction, the record states the engineer's decision, quoting the fixed decision label they selected. The reviewer ranking, `comment-discipline-reviewer`'s own direction, and whether any later pass had every finding inside its fix stay in session (rows 49, 51, 91). The bar binds this plan, every dispatch prompt, and every public write Verification 6 lists. Figures reach the engineer in session only.

### Assumption ledger

- **Root:** No line-exact measurement says whether findings on `/ready-for-review`'s cumulative re-passes after a fix land on lines that fix wrote more often than the fix's share of the diff predicts. GH-1207's scan placed them by token match, with no base rate and no reviewer split. This plan measures it once from retained findings files and git history, by a method committed before the run, and the engineer decides from the readings. It records that decision and which way each reading pointed in the register, and changes no mechanism.
- **Givens:**
  - **G2.** The corpus is whatever findings files survive. They are gitignored and live only in worktrees not yet removed, and a cumulative pass its cache skipped wrote none (`claude-skills/skills/ready-for-review/SKILL.md:68`). *Reason:* files already deleted, or never written, cannot be recovered.
  - **G3.** Findings files already written keep the heading and citation shapes their reviewers chose, and older files follow no fixed grammar. *Reason:* they are historical output, which no change can reshape.
- **Rows:**
  1. `[engineer-verified: "Enrichment gate (Recommended)"]` The gate the engineer selected, replacing their earlier selection, "Inside outnumbers outside (Recommended)". It covers the choice of enrichment as the comparison and nothing more. Row 77 later softens how it is applied, as row 82 records.
  2. `[unverified]` That option's description is the session's own. It read: act only if, among placeable prose findings from reviewers other than `comment-discipline-reviewer` on later cumulative passes, the share inside the fix delta exceeds the delta's share of the cumulative diff's added prose lines; the direction must agree at finding, pass, and branch pooling and survive the adverse placement bound; any reviewer named is picked by enrichment, not raw count, and the ranking is descriptive. The session relayed the ceiling check as the consult's (row 37), with its branch floor called ungrounded (row 43). This plan adds the favorable assignment. The stop and inconclusive outcomes and the ceiling's place in act, which it also added, are superseded by row 78. A named reviewer raised the inside findings, which row 26 separates from whoever wrote those lines.
  3. `[engineer-verified: "Local, ordinal only (Recommended)"]` The script stays local, and the published record is ordinal.
  4. `[unverified]` The session's description of row 3's option: an ad hoc script in a work dir outside the repo, as in GH-1207 and the clean-pass-criteria study, with the PR shipping only the plan and a recorded verdict.
  5. `[engineer-verified: "R-format first (Recommended)"]` The corpus choice the engineer selected, replacing their earlier selection, "All, with R-format check (Recommended)".
  6. `[unverified]` The session's description of row 5's option: parse only `## R<n> [severity]` files first; build the heuristic parser for older files only if the R-format result is inconclusive under the bounds; on widening, hand-check a spread of older files per reviewer, with a halt on mismatch. Row 74 drops that widening from this plan, and the engineer can choose the older-file parser as more data (row 78).
  7. `[engineer-verified: "New cost-levers section (Recommended)"]` The verdict goes in a new register section.
  8. `[unverified]` The session's description of row 7's option: a new section in the GH-1207 section's shape, cross-referencing its narrowed-pass row. The heading quotes this plan's H1, as the register's other sections quote theirs (row 27). The dated pointer comes from the consult's points (row 37), and row 78 supersedes their inconclusive verdict. Row 74 trims that shape: the could-not-settle paragraph folds into the reopening list, and the limits list keeps only what bears on reading the verdict.
  9. `[engineer-verified: "yeah sounds good go for it"]` The engineer's reply to the session's proposal: plan the cumulative-pass half as phase 1, with a staged-round half only if phase 1 is ambiguous or points at staged rounds. It covers that framing. Both conditions are the session's wording.
  10. `[engineer-verified: "Repeat staged rounds dominate (Recommended)"]` The reading of row 9's "points at staged rounds" that the engineer selected, a phrase no one had defined. It covers the choice of that reading and nothing more.
  11. `[unverified]` Relayed by the session: a prior `plan-architect` consult rejected a `code-writer` rule to shrink fix prose pending this measurement, and suggested this join.
  12. `[verified: claude/.claude/scripts/findings-path-suffix.sh:37-43]` The suffix is `date +%s` at round start, then a hyphen, then the branch name with `/` turned to `-`, filtered to `[A-Za-z0-9-]` and cut to a fixed length. A detached HEAD yields `HEAD`.
  13. `[verified: claude-skills/skills/code-review/SKILL.md:300,304]` One suffix serves every reviewer a round spawns, as `agent-reviews/<agent-name>-<suffix>.md`, beside `review-ledger-<suffix>.md`. Every reviewer carries a `### File-based output` section.
  14. `[verified: claude-skills/skills/ready-for-review/SKILL.md:84]` Each cumulative pass writes `code-review-dispositions-<suffix>.md` under its round's suffix. A clean pass records a `none` row.
  15. `[verified: claude-skills/skills/ready-for-review/SKILL.md:68,82]` A live cumulative-review marker skips the pass. A dirty pass dispatches one `code-writer` covering every ADDRESS row. Its fix commit passes the staged-diff gate, and the loop returns to a full pass. The commits between two cumulative passes therefore carry the fix.
  16. `[verified: claude/.claude/agents/staff-sdet.md:118-139]` The R-format is one `## R<n> [<severity>] <angle-name>` H2 per finding, an optional `## Answers`, and a one-line note of prior decisions.
  17. `[verified: the session's Read of dispositions records in sibling worktrees]` Titles name a cumulative pass in varied wording, and some carry `HEAD <sha8>`. A `scope:` clause can sit on a body line. A body line can also mention a staged review in passing, so the staged-scope test reads only the H1 and `scope:` lines.
  18. `[verified: Glob over the main checkout and its worktrees directory]` The main checkout has no root `agent-reviews/`. At least one worktree's directory name differs from its files' slug, so branches resolve from the slug, not the directory name.
  19. Superseded by row 74.
  20. `[unverified: Step 3 exploration report]` R-format findings files exist beside older ones that mix free-form H2 titles with recurring non-finding sections: `Recommendations`, `Answers`, `Foundation concern`, `Previously-settled, now reopened`, `Ledger cross-check`, `Checked, no finding`, and `Not run`. Some older files number findings as `### N.` headings.
  21. `[unverified: Step 3 exploration report]` Citation shapes include `path:N`, `path:N-M`, `path:N,M`, path-less `:N-M`, approximate `~:N`, `HEAD:path:N`, and prose "lines N-M". Basename-only paths occur. Some `comment-discipline-reviewer` findings cite diff positions, and `skill-fidelity-reviewer` cites no lines.
  22. `[unverified: Step 3 exploration report]` The worktree branch refs checked exist locally. Some PRs are squash-merged, so their fix commits survive only on branch refs. Some branches merge `origin/main`, and some reflogs show rebases and amends.
  23. `[unverified: git documentation, not run this session]` `git blame` without `-M` or `-C` attributes each line of a revision to the commit that introduced it in that form. `rev-list --first-parent --no-merges A..B` lists a branch's own commits after A. `merge-base --is-ancestor` tests ancestry. `reflog show --date=unix` prints each entry's time. `log --format=%at` prints a commit's author date. `selftest`'s fixture repo checks each of these before any corpus run, the reflog included, because the fixture's own reflog is real `git` output.
  24. `[unverified: inference]` A cumulative pass that left no record merges two windows into one, which biases toward inside. A staged round that left a record without naming its scope splits a window, which biases toward outside. The ADDRESS, author-date, and Outcome-commit conditions catch some such pairs, not all.
  25. `[verified: docs/cost-levers-considered.md:700]` `comment-discipline-reviewer`'s findings inside a fix delta are a first read of that prose. Row 35 keeps them out of the enrichment comparison.
  26. `[unverified: inference from row 15]` One `code-writer` addresses every ADDRESS row of a pass. The reviewer who flags a fix-written line therefore need not be the one whose suggested fix wrote it. The session report names the flagger, and the source is left to the follow-up.
  27. `[verified: docs/cost-levers-considered.md:3-17,676-710]` The register keeps a verdict and its measured reason, and each section's heading names its source plan and quotes its title. The GH-1207 section's shape is method, limits list, table, what could not be settled, reopening list, and closing line.
  28. `[verified: docs/cost-levers-considered.md:695-696]` The incremental-credit row holds its verdict "even if every unclassified verdict were zero-finding", the shape the two assignments generalize. The narrowed-pass row found outside outnumbering inside by token match, with no base rate.
  29. `[unverified]` Share-like words such as "most", "majority", "minority", "many", and "few" stay out of the section and this plan's own sections, following the GH-1207 plan's reading (`.claude/plans/review-loop-cost-levers.md:36,106`). The engineer has ruled on no share-like descriptor, and the existence bit stays in session (row 91).
  30. `[verified: .claude/plans/clean-pass-criteria.md:61,97-99]` Precedent for a `mktemp -d ~/.local/state/...` work dir, one literal `python3 -I -B` call per subcommand, and corpus `git` calls that are read-only, never use `-C`, and never fetch.
  31. `[verified: CLAUDE.md "Commands"; claude/.claude/scripts/select-tests.py:336-341; claude-skills/skills/tests/test_skills.py:4248,7530,7668]` `select-tests.py` maps every `docs/` path through one blanket rule. The citation-extraction, state-path, and templated-stowed-path tests cover register prose.
  32. `[unverified: inference from Context]` A pair with no commit between its passes measures a re-run on unchanged commits, not a fix, so it drops.
  33. `[verified: claude-skills/skills/plan-it/SKILL.md:93-95]` An approved plan's implementation goes to `code-writer` per phase by default.
  34. `[verified: claude/.claude/scripts/findings-path-suffix.sh:27-35]` The suffix script appends to the repo's ignore list, so the measurement re-implements its slug rule instead of running it.
  35. `[engineer-verified: "Count it in the ceiling only (Recommended)"]` The engineer's choice for `comment-discipline-reviewer`.
  36. `[unverified]` The session's description of row 35's option: exclude its findings from the enrichment comparison, and count them toward the ceiling check, as rounds a fix-hygiene rule could save.
  37. `[unverified: an adversarial Sonnet consult, as summarized by the session]` Method points this plan adopts, except the straddle rule and the inconclusive verdict, which row 78 supersedes, and the lower-bound reading of inside, which the register's Reason replaces with what inside counts (rows 24, 68, 80):
      - the unit is findings, and inside is a lower bound on fix-induced churn;
      - the record never calls outside "variance" (row 38);
      - the gate is computed with every uncertain item adverse and again with every one favorable, a straddle is inconclusive and routes to the engineer, and certain-outside cases sit apart from ambiguous ones;
      - a pass is cumulative only with a disposition record, and worktrees come from `git worktree list --porcelain`;
      - windows use reflog tips and author dates, drop non-ancestor tips, and keep only pairs whose earlier pass was dirty with a `code-writer` fix;
      - prose is defined before the run;
      - the gate is committed before the run and the verdict after, and the work dir stays until the verdict is acted on;
      - the record carries an inconclusive verdict, a "Reopening would need" list, and a dated pointer in the GH-1207 section.
  38. `[verified: claude-skills/skills/code-review/SKILL.md:38-45]` A cumulative pass enumerates the full diff unnarrowed. A staged spawn's causal-reach clause, and the duty to flag a noticed pre-existing issue, show that findings can sit on lines a change did not write. Outside therefore holds more than variance.
  39. `[verified: .claude/plans/clean-pass-criteria.md:129-130,472,528,590]` That plan's window holds commits reachable from a recorded tip but not from the merge-base, with author dates between two records' epochs. It records that staged commit-gate rounds wrote no disposition records, that a rebase keeps author dates (its own row, unverified there), and that `git worktree list --porcelain` finds nested and out-of-tree worktrees a one-level glob misses. Its work dir stays under `~/.local/state/` across phases (`:447-449`).
  40. `[verified: docs/cost-levers-considered.md:13-15,333]` The register lets a section carry a dated follow-up paragraph, and read to the section's end. Its shape is `**YYYY-MM-DD follow-up:**` and then the text.
  41. `[verified: claude-skills/skills/tests/test_skills.py:3750-3776]` `test_tooling_measurement_citation_resolves_to_real_heading` parametrizes `docs/cost-levers-considered.md` and resolves its redaction-heading citation.
  42. `[unverified: inference]` The finding share counts findings anchored on lines the branch did not add, while the base rate counts only added lines. The comparison therefore leans toward stop.
  43. `[unverified: the session's instruction]` The consult's ceiling floor of passes on more than one branch is ungrounded, and this plan's earlier branch floor on the gate is dropped for the same reason. That the ceiling gates act at existence is superseded by row 78.
  44. `[unverified: inference]` The two assignments bound placement and class uncertainty, not sampling error. Nothing here sizes a sample.
  45. `[unverified]` The session's description of row 10's option, in the session's own wording: "fires when repeat staged rounds on one commit outnumber the cumulative pass pairs measured, i.e. re-review after a fix happens more where git can't see fix lines than where this scan can. Independent of the verdict." The other reading this plan had weighed is that inside findings implicate the staged gate each fix commit passed, under which act would fire the trigger too.
  46. `[verified: docs/private-project-redaction.md:135-136,177-184]` A read from an instrument with no scope refusal publishes nothing computed from it, and records only what it decided and which way it pointed. Row 3's choice holds publication to that bar independently of this rule.
  47. `[engineer-verified: "Keep the pointer (Recommended)"]` The engineer kept Critical files item A, the dated pointer, in scope. The tag covers that choice only, not the pointer's wording.
  48. `[engineer-verified: "Session only (Recommended)"]` The engineer's first answer when asked whether the public register row may name the reviewer ranked first by enrichment, or say whether any later pass had every finding inside its fix. Row 49 replaces it.
  49. `[engineer-verified: "Existence bit only (Recommended)"]` The engineer's answer to that question, re-chosen after an architect consult, replacing row 48. The tag covers that choice only. Row 91 later moves the existence bit out of the register.
  50. `[unverified]` The session's description of row 49's option: publish whether any later pass had every finding inside its fix, under the adverse count; keep the ranked reviewer out of the register and report it in session; on act, the follow-up plan names it publicly. Row 91 supersedes its publication of the existence bit.
  51. `[engineer-verified: "Ceiling only, strictly (Recommended)"]` The engineer's answer when asked whether publishing `comment-discipline-reviewer`'s own inside-against-outside direction goes beyond row 35's choice.
  52. `[unverified]` The session's description of row 51's option: drop its published direction, and keep sampling its findings in the hand-check only so the placement behind the ceiling is verified. The whole-pass stratum samples them that way (row 55).
  53. `[engineer-verified: "Add the raw reading (Recommended)"]` The engineer's answer when asked how to back the pointer's promise that the section states whether the exact reading points the same way as the narrowed-pass row.
  54. `[unverified]` The session's description of row 53's option: pre-commit one more reading, inside against outside over placeable findings from gate reviewers, and keep the pointer; the section notes it is a close re-read, not an exact one, because `comment-discipline-reviewer` is excluded.
  55. `[unverified: a plan-architect consult, as summarized by the session]` Points this plan adopts:
      - the published section drops the ranked reviewer and `comment-discipline-reviewer`'s direction, the session report carries the ranking, and `verdict.txt` never holds it;
      - the existence bit is read under the adverse count;
      - on act, the work dir stays until the follow-up plan is committed, because `rows.tsv` is the only home of the per-reviewer data;
      - the session report uses one ranking metric;
      - the raw reading covers placeable findings of every class from gate reviewers, with no assignment, and the section calls it a close re-read;
      - two seeded whole later passes replace the `comment-discipline-reviewer` stratum, adverse-ceiling passes first, with every finding checked. Its no-label point is superseded by row 74, since the engineer sees each placement.
  56. `[unverified: the session's instruction]` The session's dispositions of the first `/plan-review` round that carry its own judgment. Its hand-checker and snapshot dispositions are superseded by row 74, and these still apply:
      - `code-writer` reads no corpus file;
      - the pass-level reading should be symmetric under the no-enrichment null, or its lean recorded;
      - the base ref is pinned, and a stacked branch drops as ambiguous.
  57. `[verified: .claude/plans/clean-pass-criteria.md:61,82,96,98,99]` Precedent for a mode-0700 check at every start, a `purge` that refuses a link or another mode, a static audit by the session rather than the script's author with a re-run on a missing hit, a timeout whose value and basis the build prompt states, a fixture builder on its own path, and corpus calls that never change into another worktree.
  58. Superseded by row 74.
  59. `[verified: plan-architect's Read of this branch's reflog file under the main checkout's .git/logs/refs/heads/]` A branch reflog's creation entry carries an all-zero old value and names the commit the branch was created from.
  60. `[verified: claude/.claude/scripts/pr-diff-against-base.sh:70-79]` The cumulative pass's diff starts at `merge-base origin/<base> HEAD`, with the base branch from `gh pr view`. Local `main` is not that base.
  61. `[verified: claude-skills/skills/code-review/SKILL.md:268]` A later reviewer confirms that each earlier ADDRESS row's fix landed as the finding required. A missing or partial fix is a new finding for any reviewer that sees it.
  62. `[verified: claude-skills/skills/ready-for-review/SKILL.md:84]` A pass is clean when every record row is resolved, and dirty otherwise, so the skill's status turns on resolution rather than on whether findings were raised. This plan's dirty needs an ADDRESS row instead, the row class that dispatches a fix (row 15).
  63. `[verified: claude-skills/skills/code-review/SKILL.md:300; claude-skills/skills/ready-for-review/SKILL.md:100; Grep for review-ledger over plan-review/SKILL.md and review-pr/SKILL.md, no match]` Each `/code-review` round renders `review-ledger-<suffix>.md`, and writes none when no decision is live. A skill-fidelity round runs the suffix script and spawns `skill-fidelity-reviewer` alone. The `plan-review` and `review-pr` bodies name no digest file.
  64. `[unverified: inference from row 63]` A staged round with no live decision leaves no digest and goes uncounted, so the trigger leans toward not firing. A `/code-review` round outside the commit gate that leaves a digest and no record counts as staged. A non-finding H2 outside row 20's titles marks a staged round dirty, which leans the trigger toward firing.
  65. `[verified: claude/.claude/scripts/findings-path-suffix.sh:13-15,42]` The slug is cut to a fixed length after sanitizing. The script's own comment says sibling branches sharing a longer prefix can produce identical slugs.
  66. `[unverified: inference from row 65]` Where a branch sharing a slug was deleted, its rounds attach to the surviving branch. The tip lookup leaves unresolved any round older than the survivor's reflog, and the pair conditions drop some others. Which way the rest bias the readings is unknown.
  67. `[unverified: inference]` Each unit's term at the pass and branch levels has zero expectation when every finding sits inside at its pass's base rate, the null the expected count assumes.
      - At a small base rate, a unit with no inside finding is the likelier outcome and adds a small negative, while a unit with one adds a large positive.
      - So in a small sample, the sign of each mean leans toward not enriched, as the finding level's sum does at a small expected count. That lean favors stop, as row 42's does.
      - The lean shrinks as units accumulate. A count of units above their baseline against units below it, the earlier definition, leans more as units accumulate.
  68. `[unverified: inference from row 61]` A re-raise of an incomplete fix sits on fix-written lines, but a rule about fix prose would not prevent it, so re-raises lean the readings toward act. Re-raise candidates over-approximate them, so leaving candidates out under the adverse assignment errs toward stop at the finding level, and in sign at the pass level. At the branch level, leaving out an inside finding, candidate or unclassifiable, also shrinks its branch's population and can raise the branch's value, so the two assignments need not bound that reading.
  69. `[unverified: inference]` A finding is placed by the line it cites. Where the text it describes sits on another line of the cited file at the later tip, the cause can be the reviewer's citation rather than the script, so an "other line" mark is counted apart rather than as a rejection. The engineer rejects instead where they judge the script caused it. A wrong tip shows the same symptom in files the window edited, so `sample.txt` shows an item's tip source, and the tally's direction is unknown.
  70. `[unverified: inference from row 57]` A findings file can arrive through a checkout only as a path the checked-out tree tracks, so testing each worktree's HEAD tree covers files planted that way. A file staged but never committed in another worktree goes undetected. Testing ignored status directly needs a `git` call run inside that worktree, which the precedent bars.
  71. `[unverified: git documentation, not run this session]` `GIT_CONFIG_GLOBAL` and `GIT_CONFIG_NOSYSTEM` null global and system config. HEAD's reflog is per worktree, while `refs/heads/` reflogs sit in the common git dir. `blame --porcelain -L <n>,<m>` limits output to those lines and prints each line's text. A `diff -U0` hunk header gives the hunk's old and new line ranges. `selftest`'s fixture repo exercises each before any corpus run.
  72. Superseded by row 74.
  73. `[engineer-verified: "Simplify, then re-review (Recommended)"]` The engineer's answer when asked how to proceed after this plan's revision for the first `/plan-review` round added layers that patched earlier layers. The tag covers that choice only.
  74. `[unverified]` The session's description of row 73's option, in the session's own words: "Have the architect cut to the minimum that keeps the verdict trustworthy: R-format only (no Stage B), no published section beyond the verdict row, and the hand-check done by you on a short printed sample instead of an agent. That removes the agent-reads-corpus threat and its layers. Then run round 2." This plan follows it, with two readings of its own:
      - "no published section beyond the verdict row" trims the section's prose, and keeps everything rows 7, 47, 49, 51, and 53 require;
      - leaving the older-file read to the engineer's decision is consistent with row 5, whose label orders the R-format read first and names no older-file stage.
  75. `[unverified: inference]` The engineer's check confirms the finding split, each citation's resolution and cited text, and each placement, class, kind, dirtiness, and re-raise call against the evidence `sample.txt` prints. It cannot confirm that `git` attributed a line to the right commit, or that a tip is the commit a pass reviewed, though it shows each tip's source. Those rest on `selftest`'s fixture repo (rows 23, 71). The engineer wrote neither `measure.py` nor `fixture-answers.txt`, so the check is independent of both.
  76. `[unverified: inference]` Where a record's `HEAD <sha8>` and the branch reflog name different commits, nothing printed lets the engineer tell which one the pass reviewed, so the round is unresolved and its pairs drop. Which way that biases the readings is unknown.
  77. `[engineer-verified: "Descriptive, you decide (Recommended)"]` The engineer's answer on the gate's shape, after the second `/plan-review` round. The tag covers that choice only, and row 78 records how it changes row 1's application.
  78. `[unverified]` The session's description of row 77's option, in the session's own words: "Commit the method before the run: what is computed, the base rate, and both assignments. Drop the mechanical act/stop/inconclusive rule. The session shows you the readings in session and you choose act, stop or more data; the register records your decision and which way the readings pointed. The multi-cite null, the non-monotone bracket and the vacuous ceiling become visible caveats you weigh, not silent rule failures. This changes how your 'Enrichment gate' selection is applied: enrichment stays the comparison, but it no longer decides on its own." This plan follows it, and also fixes the vacuous ceiling in its definition, by requiring a parsed finding (row 81).
  79. `[engineer-verified: "Yes, follow-up may name it (Recommended)"]` The engineer's answer when asked, in the session's wording, whether on an act decision a follow-up plan, committed publicly, may name the reviewer ranked first by enrichment. The tag covers that choice only.
  80. `[unverified: inference]` A finding is inside when any line it cites is, while its expected count uses the base rate, the chance for a single line. Under no enrichment, a finding citing a range, or lines in separate places, has a higher chance of being inside than that, so the readings lean toward act.
  81. `[unverified: inference]` Requiring a parsed finding keeps a dirty pass with no reviewer file out of the ceiling. A reviewer file holding findings in a shape the parser does not read, such as `### N.` headings under no H2, still leaves findings unread, so the all-inside pass leans toward existing. The sample shows the line counts of a whole pass's files, so the engineer can see such a file.
  82. `[engineer-verified: "Softened: comparison stays (Recommended)"]` The engineer's answer when asked whether row 77's selection replaced row 1's or only softened how it is applied. They first typed "What is the right one to pick here and why? Ask the architect", and selected this after a `plan-architect` consult. The tag covers that choice only.
  83. `[unverified]` The session's description of row 82's option, in the session's own words: "Row 1 keeps its quoted label; a new row records your confirmation that enrichment stays the comparison and row 77 removed only the deciding rule." Row 82 is that row.
  84. `[engineer-verified: "Add a marked recommendation"]` The engineer's answer when asked, in the session's wording, whether the session's report should also give its own recommendation, clearly marked as the session's. The tag covers that choice only.
  85. `[unverified]` The session's description of row 84's option, in the session's own words: "After the readings, the session states which option it would pick and why, labelled as its own." This plan places it after the leans as well, and keeps it out of `verdict.txt`, the register, and the decision labels.
  86. `[unverified: inference]` The ceiling has no baseline. Under no enrichment, a pass holding a single finding is all-inside whenever that finding lands inside, which happens at about its pass's base rate, and above it under row 80's multi-cite effect. A corpus of small dirty later passes can therefore hold an all-inside pass with no fix effect, so the ceiling leans toward an all-inside pass. The finding counts printed beside the ceiling let the engineer weigh that.
  87. `[unverified: inference from rows 68 and 80]` Effects that move a reviewer's ranking apart from where its findings land, which the counts printed beside each reviewer's excess let the engineer tell apart:
      - row 80's any-cited-line rule raises the excess of a reviewer whose findings cite ranges or separate sites;
      - the adverse assignment counts a reviewer's unplaceable findings in its population as outside and leaves out its re-raise candidates, so a reviewer whose citations take an unplaceable shape, or who confirms earlier fixes, ranks lower for that;
      - a difference, unlike a ratio, scales with population size, so at the same positive enrichment a reviewer with a larger population ranks higher.
  88. `[verified: unicodedata.category, run on sample characters]` Bidi overrides and isolates, zero-width characters, and the byte-order mark are category Cf, the line and paragraph separators are Zl and Zp, C1 controls and DEL are Cc, and a no-break space is Zs. None is a letter, mark, number, punctuation, or symbol, and a letter such as an accented Latin one is Ll. The escape set by category therefore covers the cases a control-character test leaves live.
  89. `[verified: man git, GIT_NO_LAZY_FETCH and --no-lazy-fetch; man git-config, extensions.partialClone, remote.<name>.promisor, and --get-regexp]` `GIT_NO_LAZY_FETCH` set to true tells `git` not to fetch missing objects from the promisor remote on demand. `extensions.partialClone` marks a repository created as a partial clone, and `remote.<name>.promisor` marks a remote used to fetch promisor objects. The page lists `--get-regexp` as replaced by `git config get --all --show-names --regexp`, which matches case-sensitively against keys with section and variable names lowercased. This plan names the older form for compatibility, and which `git` versions accept each form was not checked. The check fails closed, so a `git` that rejects the form refuses to run instead of reading as no partial clone. The partial-clone set in Script bounds is those two keys. A repository whose markers were removed by hand is not detected.
  90. `[verified: the two listing statements run this session; a brace group joining them was refused by the worktree guard]` `ls claude/.claude/agents | sed -E 's/[.]md$//'` lists the agent names, and `git for-each-ref --format="%(refname:short)" refs/heads/` lists local branch names. Branch names can hold `/`, and `main` is among them, so the name check's `-w` match flags the word `main` in prose, which Verification 6 lists as justified.
  91. `[engineer-verified: "Session only (Recommended)"]` The engineer's answer, during the plan commit's `/code-review`, when asked how the register should handle whether any later pass had every finding inside its fix, given that `docs/private-project-redaction.md` § "A wider corpus goes to the owner, never into a public artifact" bars any count. It replaces row 49's publication of the existence bit.
  92. `[unverified]` The session's description of row 91's option: the existence bit stays in the session report for the decision, and the register publishes only the directions and the decision.
  93. `[engineer-verified: "Lever named (Recommended)"]` The engineer's answer when asked which reading of the redaction doc covers a follow-up plan naming the reviewer the ranking put first.
  94. `[unverified]` The session's description of row 93's option: the doc lets a public artifact record "which lever was adopted", retuning one named reviewer's wording is that lever, and the name may appear with no figure, count, or rank beside it.
- **Mechanisms:**
  - **M1. Placement by `git blame` at the later tip.** It stays exact across multi-commit fixes, line shifts, and lines a merge of `main` brought in. Set aside:
    - GH-1207's token match, which fails the root's exactness;
    - a `git diff -U0` of the two tips, which counts lines a merge of `main` brought in;
    - per-commit `git show -U0` hunks, whose line numbers drift across a multi-commit fix.

    anchors: root, row22, row23.
  - **M2. Tips from the shared branch reflog, with an author-date window check.** The reflog gives the exact commit each pass reviewed, which `blame` needs, and author dates survive a rebase (row 39). It reads `refs/heads/<branch>`'s reflog, never HEAD's, which is per worktree (row 71). A record sha8 that disagrees with the reflog leaves its round unresolved (row 76). Set aside:
    - a committer-date window, because rebases and amends reset committer dates (row 22);
    - the named sha8 alone, which only some records carry and a model typed;
    - an author-date window from one recorded tip alone, which names no per-pass commit for `blame`.

    anchors: row12, row17, row22, row39, row59, row71, row76.
  - **M3. R-format files only.** A pair whose later pass holds a reviewer file that is not R-format drops. Set aside: a heuristic parser for older files, which needed its own title triage and its own hand-check of older files (row 74). The engineer can choose that parser as more data instead (rows 5, 78). anchors: row5, row6, row16, row74, row78.
  - **M4. A disposition record required for a cumulative pass, plus the fix-dispatch conditions.** Set aside: classing rounds by title keywords alone, because a round with no record has no Outcome column to tie its window to a fix. anchors: row14, row15, row17, row24, row39.
  - **M5. Enrichment readings against a `blame`-derived base rate, under two assignments, with a ceiling, reported for the engineer to decide on.** The pass and branch levels average each unit's excess over its baseline, which has zero expectation under no enrichment (row 67). Set aside:
    - a mechanical act, stop, and inconclusive rule, whose definition drew new defects round after round, including a defect an earlier round's fix created (rows 77, 78);
    - the raw inside-against-outside count as the gate, the engineer's earlier gate, which ignores how much of the diff the fix wrote. It stays only as the non-gating raw reading behind the cross-reference (row 53);
    - a base rate from `git diff --numstat` over the window, which cannot split prose lines inside code files and counts lines a later commit rewrote;
    - a count of units above their baseline against units below it, whose lean toward not enriched grows with the sample at small base rates (row 67);
    - a mid-p value per pass, which needs a model of findings as independent draws and still leans in a small sample.

    anchors: row1, row2, row35, row37, row42, row43, row53, row67, row77, row78, row82, row84.
  - **M6. A standard-library script with `selftest`,** heavier than an inline command. Set aside: an inline Bash pipeline, which CLAUDE.md's script-first rule bars for a multi-step recipe and which cannot parse row 21's shapes; and extending `reviewer_yield.py`, which is committed code (row 3) and reads transcripts at file level. anchors: row3, row4, row21.
  - **M7. A hand-check by the engineer, on a short seeded sample they open in their own viewer, before any result is opened.** The engineer wrote neither the script nor its expected values, no dispatched agent reads corpus text, and the session loads only rejected items, in a turn that takes no write or publishing action (rows 74, 75). Set aside:
    - a blind `Explore` check from raw `git` output, whose effort pin needed a blind key to offset and whose read-only tools needed the session to run one literal command per raw output, each layer closing a gap the last opened (row 73);
    - `general-purpose`, which holds `Bash` and `Write` while reading corpus text;
    - the session checking alone, or printing the sample itself, since it is the party that routes a script fix and holds commit, `gh`, and public-writing tools;
    - no hand-check, because fixtures cannot anticipate the corpus's real heading and citation shapes (rows 20, 21).

    anchors: row20, row21, row73, row74, row75.
  - **M8. The register section and a dated pointer, with no design record,** as the GH-1207 plan chose, since nothing in the system changes. anchors: row7, row8, row27, row40.
  - **M9. Two commits: the plan before the run, the decision after.** A reader can check that the method predates the result. anchors: row37, row78.
  - **M10. Bases from `refs/remotes/origin/main`, with stacked branches dropped.** It matches the base the reviewed diff used (row 60). A branch created from another branch's unmerged commit records that commit in its creation entry, which is then no ancestor of `refs/remotes/origin/main` (row 59). Its parent's lines would count as its own added lines, so it drops. Set aside: local `main`, which can trail a merged `origin/main` and inflate the base rate's denominator; and `gh pr view`'s base branch, which needs the network that row 30 rules out. anchors: row56, row59, row60.
  - **M11. Staged rounds by their ledger digest.** The digest is the one artifact found that `/code-review` writes and the other record-less rounds do not (row 63). Set aside: a missing record alone, which counts plan-review and skill-fidelity rounds; and the reviewer roster, which no documented rule ties to a round's kind. anchors: row10, row63, row64.
  - **M12. `verdict.txt` in a fixed vocabulary, written by the script except for the `decision` line the session appends,** so the section, the commit message, and Verification 5 rest on a mapping rather than a prose reading. The decision labels the register quotes are fixed in this plan for the same reason. Set aside:
    - the session writing the reading lines from `aggregate.txt`, which lets a figure through by hand;
    - decision labels the session composes after the run, which could carry a figure or the first-ranked reviewer's name past the screen;
    - a separate publishing context started from `verdict.txt` alone, because an act decision's follow-up plan needs `rows.tsv` (rows 55, 79) and the session publishes only the mapped phrases. On act, the follow-up plan is the one exception: it carries one reviewer name taken from the session's figures (rows 79, 93). A rejected item loaded at the hand-check step stays in the session's context, and the turn rule in step 6 keeps only that turn from acting on it.

    anchors: row3, row49, row55, row77, row79.

## Critical files

**`docs/cost-levers-considered.md`** (modify), in two places.

**A. Dated pointer.** Append one paragraph after the `review-loop-cost-levers.md` section's closing line, as that section's last paragraph (row 40). With the verdict commit's date, it reads: ``**YYYY-MM-DD follow-up:** the narrowed-pass row's question of where findings sit relative to a fix is re-read with exact line overlap in the `fix-delta-churn-measure.md` section below, which states whether that reading points the same way. The narrowed-pass row's verdict stands.`` It changes no existing line.

**B. New section,** after the pointer, as the file's last section. In order:

1. **Heading, exact string, with no date:** ``## From `fix-delta-churn-measure.md` — "Measure whether cumulative-pass findings land on lines the previous fix wrote"``.
2. **Intro paragraph**, carrying:
   - **The question:** whether findings on `ready-for-review`'s cumulative re-passes after a fix land on lines that fix wrote more often than the fix's share of the branch's added prose lines predicts, and so whether steering fixes away from adding prose would cut review churn.
   - **Method:** a one-off scan of locally retained `agent-reviews/` findings files and each branch's git history, committed before it ran and not kept as a script. Two consecutive cumulative passes bracket a fix, and the later pass's cited line is inside it when `git blame` attributes the line to a commit between them. A reading's baseline is the fix's share of the branch's added prose lines. Prose means documentation, comments, and docstrings, skill and agent bodies included. Each reading was computed with every uncertain finding counted against inside findings exceeding their baseline, and again with every one counted for it. The engineer decided from these readings and their known leans, and the section quotes the label they selected from those fixed before the run. The section names no account, branch, or time window.
   - **Publication:** per `docs/private-project-redaction.md` § "Publishing a tooling measurement", no figure and no share-like descriptor, only which way each reading pointed (row 91). Write the citation on one line, with ASCII quotes (row 41).
   - **Cross-reference:** the `review-loop-cost-levers.md` section above placed findings by token match for its narrowed-pass row. One sentence, from `raw-reading`, states that this exact reading points the same way (`outside-outnumbers`), points the other way (`inside-outnumbers`), points neither way (`tie`), or could not be compared (`undefined`). It calls the reading a close re-read rather than an exact one, because it leaves `comment-discipline-reviewer` out, and says this section does not re-decide that row (rows 53, 54).
3. **Limits, as a list:**
   - The sample is non-random. Findings files survive only in worktrees not yet removed, only files in the current heading format were read, and a pass counted as cumulative only when it left a disposition record.
   - Findings on lines the branch did not add count against the fix, while the baseline counts only added lines. That leans the comparison toward not pursuing, and a small sample leans it the same way even where findings land inside at exactly the baseline. A finding counts as inside when any line it cites is, while the baseline is the chance for a single line, which leans the other way.
   - Commits between two passes can include work beyond the fix, which counts as inside.
   - Counting uncertain findings both ways bounds placement uncertainty at the finding and pass level, not at the branch level, and not sampling error.
   - `comment-discipline-reviewer` sits outside the comparison, because its findings inside a fix are a first read of that text.
   - `skill-fidelity-reviewer`'s findings are excluded from the comparison, because the scan could not place them.
   - Any further limit the run surfaces, in the same form.
4. **Table** `| Lever | Verdict | Measured reason |`, exactly one row. Each Reason cell lists its items with `<br>- `, as the register's existing rows do.
   - **Lever:** steering review fixes away from fix-written churn, by a `code-writer` rule to prefer deleting or rewording prose over adding it, or by a change to one reviewer's suggested-fix wording.
   - **Verdict,** from `decision`: "Not pursued (measured)" on `stop`, "Follow-up planned (measured)" on `act`, and "Deferred for more data (measured)" on `more-data`.
   - **Reason,** from `verdict.txt` through this mapping, in direction words only:
     - the direction at the finding, pass, and branch level, from the level's `-adverse` and `-favorable` fields. Where both read `enriched`, the item reads "inside findings exceeded their baseline at the <level> level, however uncertain findings were counted", and where both read `not-enriched`, the same with "did not exceed". Where either reads `undefined`, it reads "the <level> level had nothing to compare", and otherwise "at the <level> level, the direction turned on how uncertain findings were counted";
     - that inside counts findings on lines a commit between the passes wrote, and that outside mixes variance, ripple findings, and omissions anchored on older lines;
     - the next step on `act` or `more-data`: a follow-up plan, or the source the selected more-data label names.
5. **"Reopening would need" list:**
   - a record of which finding's suggested fix wrote each line, since one `code-writer` addresses every finding a pass sends it;
   - a staged-round measurement that reads fix-written lines from `code-writer` edit calls, since repeated staged rounds on one commit are never committed apart;
   - a per-pass record of the commit each cumulative pass reviewed, and citations that carry repo-relative paths.
6. **Closing line:** no code, skill, hook, or test change ships from this investigation.

**Neither the pointer nor the section may contain:**
- a plan-only label, such as a stage or phase name, "gate", "measured pair", "adverse", "favorable", "ceiling", a row number, or an M number;
- any figure or share-like descriptor from the scan;
- "variance" anywhere but the sentence saying outside mixes variance, ripple findings, and omissions (row 38);
- a branch name;
- a reviewer name other than `comment-discipline-reviewer` and `skill-fidelity-reviewer`;
- a `verdict.txt` field name or phrase in place of its mapped text;
- a home-rooted path, the work dir's included, because `test_doc_has_no_state_path` bans those;
- a line-number citation into the register.

**`.claude/plans/fix-delta-churn-measure.md`** is this plan. It is committed per `plan-it` Step 7 before the run, and it does not change afterward (M9).

**Not repository files:** `measure.py` and every study output stay in the work dir and are never committed (row 3).

**Dispatches, in sequence,** because each one's input is the previous one's output (row 33):
1. **`code-writer` (`model: sonnet`)** writes `<work dir>/measure.py`, and touches no repository file.
   - The prompt names this plan's path, the Approach paragraphs from "Pairing passes" through "Hand-check sample", and `fixture-answers.txt`. It states each `git` timeout's value and basis.
   - It reads no file under any `agent-reviews/` directory, and no reflog outside its own fixture repo. It holds `Bash` (`claude/.claude/agents/code-writer.md:4`), so the prompt names `selftest` as the only subcommand it may run. Rows 16, 17, 20, and 21 give the shapes it needs.
   - Verify with `python3 -I -B <work dir>/measure.py selftest`, which must exit 0 with a passing named case for every `selftest` bullet.
2. **`code-writer` (`model: sonnet`)** writes the pointer and the new section, and nothing else.
   - The prompt carries the `verdict.txt` lines the mapping above consumes, every line but `staged-trigger`, and the engineer's selected fixed decision label inline, and names this plan's path and Verification 1 and 5 to 8. It names the work dir as off limits, and carries no figure (M12).

**Reuse rather than restate:**
- the `review-loop-cost-levers.md` section's shape, trimmed (row 74);
- `.claude/plans/clean-pass-criteria.md`'s window rule (row 39), and its mode check, audit, timeout, fixture-builder, and `purge` bounds (row 57), cited;
- `pr-diff-against-base.sh`'s base, `origin/<base>`, cited (row 60);
- `findings-path-suffix.sh`'s slug rule, re-implemented (row 34);
- the GH-1207 plan's redaction screen (Verification 6).

## Verification

1. From the worktree root, run `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py`. `docs/` maps through the blanket rule (row 31). The citation-extraction, state-path, and templated-stowed-path tests guard the new text. `test_tooling_measurement_citation_resolves_to_real_heading` covers this file (row 41), but it passes on the existing section's citation alone, so Verification 7 checks the new section's citation by hand. Ruff and ShellCheck do not run, because no `.py` or `.sh` file in the repository changes.
2. `python3 -I -B <work dir>/measure.py selftest` exits 0, with a passing named case for every `selftest` bullet, each time the session runs it, after every dispatch 1 return.
3. **Before results open.**
   - `audit.txt` holds a full audit entry, with its SHA-256, for every version of `measure.py` that `survey` or `run` ran, and the listing before the first audit found no corpus output.
   - The engineer has confirmed every item of the current `sample.txt`, or marked it "other line", before `aggregate.txt` is opened.
   - Before dispatch 2, `grep -vE '^[a-z-]+: [a-z-]+$' <work dir>/verdict.txt` prints nothing, and every M12 field, `decision` included, appears once with an allowed phrase.
4. **The method predates the result.** The SHA in `plan-sha.txt` is an ancestor of the verdict commit. `git diff <that SHA> HEAD -- .claude/plans/fix-delta-churn-measure.md` prints nothing. The verdict commit touches only `docs/cost-levers-considered.md`.
5. Read the section beside `verdict.txt`. Its verdict, every Reason item, and the cross-reference sentence each carry the text the Critical files mapping gives for its field's phrase. The quoted label is the fixed decision label for the `decision` line's phrase, and on `more-data` the Reason item names that label's source.
6. **Redaction screen.**
   - **Known answer, first.** Pipe planted lines into the pattern's last `grep` with `printf '%s\n' <lines> | grep -niE '<pattern>'`. A planted line for each word class, a digit, a number word, a frequency word, a comparative phrase, and a rank word (`the reviewer was ranked first`), must print. A planted ordinal sentence using none of them must not. A wrong answer either way means the pattern is fixed before anything is screened.
   - **Where.** This whole plan, at Procedure step 1 before the plan commit. Every added line of the pointer and the new section, before the verdict commit and again before the PR. Every commit message on the branch (`git log --format=%B origin/main..HEAD`), and the PR body before `gh pr create`. Every later public write about this study before it posts: `gh pr edit`, PR comments, review replies, and issue comments. On act, the follow-up plan, before its commit.
   - **Pattern,** shown for the register's added lines:
     `git diff origin/main -- docs/cost-levers-considered.md | grep -E '^\+' | grep -niE '[0-9]|\b(one|two|three|four|five|six|seven|eight|nine|ten|dozen|half|third|quarter|percent|ratio|most|mostly|many|few|fewer|several|minority|majority|substantial|uncommon|common|commonly|rare|rarely|often|usual|usually|frequent|frequently|roughly|twice|double|dominant|all|every|none|only|each|any|never|outnumber[a-z]*|exceed[a-z]*|first|second|top|highest|lowest|rank[a-z]*|leading|worst|best)\b|out of|more than|less than'`
   - On this plan, drop the `[0-9]|` alternative, because row numbers and line cites match every line.
   - **Names.** Every commit message and the PR body, and each later public write the Where bullet lists, the follow-up plan included, also pass a name check (row 90). Only the one reviewer name the follow-up plan carries (row 79) is exempt there, and any other name hit in it is justified one by one. The session first writes two names files into the work dir, each by one redirect statement: `ls claude/.claude/agents | sed -E 's/[.]md$//' | grep -vxE 'comment-discipline-reviewer|skill-fidelity-reviewer' > <work dir>/agent-names.txt`, and `git for-each-ref --format="%(refname:short)" refs/heads/ > <work dir>/branch-names.txt`. It rebuilds both before each screen. The check is `git log --format=%B origin/main..HEAD | grep -niwF -f <work dir>/agent-names.txt -f <work dir>/branch-names.txt`, and for the PR body or the follow-up plan the same `grep` over its text. Known answer, first: a planted line holding one agent name from the first file must print, and so must one holding one branch name from the second. A planted line holding `comment-discipline-reviewer`, and one holding no name, must not. A planted follow-up line holding two agent names must print, and the session exempts only the one the plan is meant to carry. The harness refuses a brace group that joins the two listings into one file, so the names stay in two files. If it refuses a redirect, the session writes that file with the Write tool from the plain listing.
   - A hit is justified when it is one of these:
     - a ticket ID, or the pointer's date;
     - a plan-structure word, such as a row number, a dispatch count, a hand-check draw size, or a `verdict.txt` phrase;
     - a rank word used as plan structure, such as "first" in a procedure step or "second" for a second sample, or naming the reviewer ranking as a report item, and never one that orders a named reviewer or a finding;
     - a fixed decision label, or an engineer label this plan's ledger quotes;
     - on the name check, `main`, this branch's own name, or this plan's file name;
     - a word naming a reading's definition or the question before the run;
     - existence-only wording, such as "some" X could not be Y;
     - a verdict-bearing "exceeded" or "outnumbered" ordinal stating which way a reading pointed;
     - the screen's own pattern, or a word list that the bar or row 29 names.
   - A hit fails when it states a magnitude, or orders how much of the data was covered against how much was not. A hit fitting no justified class fails.
   - The pattern is a floor, not the rule. Read every comparative or ordinal sentence in each screened artifact against the rule, matched or not.
7. **Citations and paths by hand.** Run `ls` on every repo-relative path the section cites. Each `` `path` § "Heading" `` must match a heading line in its target (`grep -n '^#' <target>`), on one line with ASCII quotes, per `.claude/rules/citation-grammar.md`. That includes the section's own citation of `docs/private-project-redaction.md`.
8. **Read the pointer and the new section.**
   - The heading is exactly Critical files item B1, and the table has exactly one row.
   - Nothing from the must-not-contain list appears. Run `grep -n variance` over the added lines, and each hit must sit in the sentence on what outside mixes.
   - The pointer is the `review-loop-cost-levers.md` section's last paragraph, and `git diff origin/main -- docs/cost-levers-considered.md` shows no removed line.
9. Run `/code-review` on the staged diff before each commit. No `/skill-review` runs, because no `SKILL.md` changes.
10. **Work dir.** `sample.txt` no longer exists once the study ends, by the engineer's decision or by a stop, and the `purge` question has been asked in either case. On act, the work dir still exists when the follow-up plan is committed, holding `rows.tsv` and no `sample.txt`. On deletion, `purge` names its target and refuses on any failed check.

## Out of scope

- **More data: the staged-round measurement, or reading older findings files.** Each gets its own plan if the engineer chooses it (rows 5, 9, 10, 74).
- **The follow-up itself:** a reviewer's suggested-fix wording change, or a `code-writer` prose rule. Either gets its own plan if the engineer decides to act (row 26).
- **Attributing a flagged line to the reviewer whose suggested fix wrote it.** The section's reopening list names it (row 26).
- **Publishing the reviewer ranking, `comment-discipline-reviewer`'s own direction, or whether any later pass had every finding inside its fix, in the register** (rows 49, 51, 91). The ranking goes to the engineer in session, and on act a follow-up plan may name the reviewer ranked first (row 79).
- **Testing a corpus file's ignored status directly.** That needs a `git` call run inside another worktree, so the HEAD-tree test stands in for it (row 70).
- **Re-deciding the narrowed-pass row, or changing any existing line of the `review-loop-cost-levers.md` section.** The pointer only appends, as the register provides (row 40), and this plan measures a different lever.
- **A branch floor on any reading or the ceiling, and any sampling bound** (rows 43, 44).
- **OS or harness confinement of `measure.py`, a before-and-after compare of repository state, and known answers for the `git` timeout and the builder's hook and signing settings.** The `GIT_*` known answers check the scrub that keeps the fixture builder, the only mutating path, off this repository, and the corpus wrapper's scrub of an inherited `GIT_DIR` and `GIT_EXTERNAL_DIFF`. A planted slow call or hook needs fixture machinery heavier than the bound it checks warrants.
- **Committing the script, or adding a `transcript-analysis.py` subcommand** (row 3).
- **Publishing any figure, or changing the publication rule.** A figure needs the owner's per-figure authorization, and the engineer chose ordinal-only publication (rows 3, 46).
- **New instrumentation,** such as recording each pass's reviewed commit, retaining findings files longer, or fixing a citation grammar. The reopening list names these without building them.
- **Clean re-passes.** They hold no findings to place.
- **Transcripts as a source for this measurement.** The engineer chose findings files (row 5), and transcripts belong to the staged-round measurement.
