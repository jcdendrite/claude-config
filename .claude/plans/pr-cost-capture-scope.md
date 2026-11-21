# pr-cost ledger: record the capture scope and guard the branch join

## Context

Make every new pr-cost ledger row say what transcript scope it was captured from, and stop the writer from joining a fork PR or a head branch no single PR can own (a default-branch candidate, a detached `HEAD` or a `worktree-agent-*` name), so a ledger reader can tell a row captured from this checkout's repo scope from one captured from an explicit glob. A `repo` row narrows the same-named-branch exposure without removing it.

Ask: GH-1426, fix shapes 1 and 2 and "Writer guarantees" 1 to 4.

Problem. `pr-cost --record` joins a row's dollar columns and its `review_rounds` cell to a PR by branch name alone, scans every project directory in the account by default, and records nothing about the scope it read. Only the PR side is tied to the pinned repository. The writer also knows why a `review_rounds` cell is empty and discards the reason, and no field marks a row whose transcripts retention had already truncated.

Why now. `review-cost-by-size` reads these rows (GH-1421) and can only label its figures "scope unverified", so it stays outside the publication instruments in `docs/private-project-redaction.md` § "Publishing a tooling measurement". The ledger is append-only and a row's scope cannot be reconstructed after its transcripts age out, so each unscoped capture is a row the report can never separate. Recording the scope lets a reader separate rows by capture scope. It does not by itself make any figure meet that section's bar.

Intended outcome. After this change:

- A new row carries a scope class derived from the scope the capture pass resolved, an absence reason for an empty `review_rounds` cell, and an earliest-surviving-activity marker, added in one header bump. A legacy row reads as unscoped.
- A bare `pr-cost --record` scans this checkout's worktrees and its removed-worktree prefix only, and exits 1 before any write when git cannot resolve the worktrees.
- The writer writes no row for a PR whose head branch is a default-branch candidate, a detached `HEAD`, a `worktree-agent-*` name, or cross-repository.
- `--force` can replace a non-repo-class row with a shorter repo-class one.

Decisions the engineer made this session, each as the option label selected in answer to the question shown:

- Question: once rows record their capture scope and `review-cost-by-size` excludes rows not captured repo-scoped, should that report become publishable under the existing rule, with the rule itself left unchanged? Selected: "Rule unchanged (Recommended)".
- Question: should `pr-cost --record` write no ledger row at all for a PR whose head branch is `main`, `master`, `develop`, a detached `HEAD` or `worktree-agent-*`? Selected: "No row at all (Recommended)".
- Question: should `pr-cost --record` skip every cross-repository PR? Selected: "Skip fork PRs (Recommended)".
- Question: what should a bare `pr-cost --record` do after this change, and what happens to an explicit `--projects` glob? Selected: "Repo default, glob allowed (Recommended)".
- Question: `--this-repo` today reads only the project directories of worktrees that still exist; should the repo scope of a repo-scoped `pr-cost --record` also include this repo's removed-worktree directories? Selected: "Include removed worktrees (Recommended)".

The session's own proposals, not the engineer's words:

- "Glob allowed" means an explicit `--projects` glob still records and its row carries a non-repo class.
- The reader rule (GH-1426 item 5: `review-cost-by-size` excludes a row whose recorded scope is not the repo class) and any edit to the publication text in `docs/private-project-redaction.md` or `CLAUDE.md` are a separate later PR. This plan is the writer side only.
- "Include removed worktrees" means moving routing-guard's existing widening (project directories whose name starts with `<main-slug>--claude-worktrees-`, `routing_guard.py:956-959`) into a shared `scope.py` helper, and counting a scope resolved that way as the repo class.

The engineer asked for a `plan-architect` consult on the session's proposals. It endorsed both and overturned none of the selections. Its additions, which are the consult's words in summary and not the engineer's:

- Required: refuse a `--force` re-capture that would replace a repo-class row with a non-repo-class one.
- Required: `pr-cost` tells a bare run from an explicit `--projects '*'` without changing the shared `--projects` default.
- Required: the earliest-surviving-activity marker is date-truncated in `pr-cost-export` or left out of it, and the scope cell is a closed enum.
- Required: every statement this change makes false is corrected in the same PR, including the `review-cost-by-size` notice and its test pin, `docs/transcript-analysis.md` § "review-cost-by-size", and the reason clause at `docs/private-project-redaction.md:139-141`.
- Required: the scope-class literals live in `pr_cost_ledger.py`, and the PR body does not close GH-1426 unless the reader rule is first split into its own issue.
- Recommended: one stderr line on a glob `--record` saying its rows carry the non-repo class and that only a repo-scoped `--force --pr N` upgrades one.

Decisions the engineer made during plan review, each as the option label selected in answer to the question shown:

- Question: which behaviours the plan adds beyond the issue should stay, among "Read mode repo default", "--force downgrade refusal" and "Glob stderr notice"? Selected all three, and typed: "Some of these seem overengineered to be honest, check with the architect, adversarial review".
- Question: which files the issue does not name should stay in the plan? Selected: "Shared scope helpers", "Report notice rewording" and "Redaction doc sentence".
- Question: this plan projects about 1,080 changed lines; split into smaller PRs now, or proceed? Selected: "Proceed anyway".
- Question, asked after the adversarial review returned: should `pr-cost --record --force --pr N --projects <glob>` be refused when the PR's existing row is repo-scoped? Selected: "Defer to reader PR (Recommended)".
- Question, asked after the adversarial review returned: should a `pr-cost --record --projects <glob>` run print one stderr line saying its rows are marked glob? Selected: "Cut, use help text (Recommended)".

The adversarial `plan-architect` review the engineer asked for, in summary. These are the review's words, not the engineer's, and they supersede the first consult's list above where the two differ:

- Read mode's repo default and its joinable filter stay: read and record share one subparser, and a machine-wide read mode would list forever PRs a bare `--record` never captures.
- The `--force` scope-downgrade refusal leaves this plan. The ledger is append-only, so a glob re-capture leaves the repo row in place, and only a reader that collapses to the current row is harmed. The refusal would also block the documented prefix-glob remedy for a session started in a repository subdirectory (`docs/transcript-analysis.md:60-66`). The reader PR decides it.
- The glob stderr line leaves this plan. The `--projects` help says a glob's rows record `capture_scope=glob`.
- Cut `_project_scope_is_this_repo` and `_capture_scope`, and inline `_apply_repo_scope_default` into `_pr_cost_report`, keeping the `repo_scope_default` keyword and `_resolve_pr_cost_scope`.
- Docs shrink with the two cuts: the re-record reasons at `docs/pr-cost.md:302,305` keep their scope-flag advice where it is still true, and the `:72` clarifier goes.
- Row 31's reason for the duplicated absence literals should be the import direction (`pr_cost` imports `pr_cost_ledger`), not that the alternative reads worse.

The session's own ruling on one review suggestion: the legacy `capture_scope` default stays empty, not an `unrecorded` sentinel, because GH-1426 guarantee 1 says the default "is empty, meaning unknown" and its test checks for an empty cell. The short-row hint rework therefore stays.

Plan review round 1 ended Request changes. Three reviewers flagged that a scan widened by the removed-worktree prefix records `repo`, because a foreign repository cloned under `.claude/worktrees/` would match. Asked what `capture_scope` should say for such a scan, the engineer typed: "That's not a real case though. Like no one would clone a repo under a worktree. What am I missing?"

The session's dispositions for the revision, not the engineer's words:

- The class stays one value, `repo`, for the identity scope and the prefix-widened scope. The prefix admits any directory whose slug starts with it: the directories under this checkout's `.claude/worktrees/`, plus a path whose lossy slug collides with the prefix. The failure needs another repository's sessions in such a directory, on a branch named like a merged PR here. The docs and PR body say what `repo` covers and that it departs from the wording of GH-1426 guarantee 2.
- A bare run whose repo scope matches no project directory prints one stderr line. The default change creates that silent case, so it is in scope.
- A merged-PR entry whose `isCrossRepository` is not a boolean is a payload failure with a non-zero exit. `period_cost.py:764-769` classifies the same condition as an unexpected payload but degrades and exits 0; the exit-1 precedent is the unparseable-JSON exit at `gh_cli.py:486-488`.
- `review_rounds_absence` is parsed by shape, not against a closed set, and `pr-cost-export` replaces a value outside the known set with one fixed token. The writer keeps its closed set.
- No comment is posted on GH-1426 for the fork-PR skip: the issue asks for a record only if the implementer decides against its default, and the engineer selected the default.

After revision 3 the session asked two questions. To "This plan projects about 1,350 changed lines (an estimate; threshold 400). Split into smaller PRs now, or proceed?" the engineer selected "Proceed anyway". To "Round 1 added four things the issue's four writer guarantees do not ask for. Which should stay in this PR? Anything left unticked is cut from the plan." the engineer selected "Empty-scope warning", "Bad fork flag fails" and "Test helper move", left "Export date fix" unticked, and typed "Ask the architect to review those choices".

A second architect consult reviewed those four choices and agreed with all four of the engineer's answers. The session's dispositions from it, not the engineer's words:

- The export date fix is cut. `_pr_cost_export_date_only` keeps its current truncation, and the export gains one line applying it to `earliest_activity_at`, whose strict parser check guarantees a `T`. The gap for a space-separated `merged_at` or `captured_at` is pre-existing, reachable only by a hand edit, and goes under Out of scope.
- The empty-scope stderr line stays but names no causes. It points at `docs/pr-cost.md`, which is the one home for the list of sessions the repo scope does not read.
- The helper move stays with a corrected list: `_NOW`, `_arrange_record_run`, `_ledger_rows`, `_run_record` and `_cell_of`. `_arrange_round_run`, `_write_four_round_corpus` and `_fail_the_pass` stay where they are, and the `pass-failed` and captured-cell checks become assertions added to the existing tests.
- Three tests are cut: the verbatim help-string pins, the `select_ledger_prs` pin in `tests/test_transcript_ledger_rounds.py`, and the `_pr_is_joinable` unit cases for a missing, `None` or string flag, which the payload check makes unreachable.
- The reader PR's three residuals have one home, `docs/pr-cost.md`, and the PR body links to it. The plan prescribes no edit to any GitHub issue.

Plan review round 2 raised no blocker. The session accepted every finding in its smaller form, as its own dispositions:

- The `review-cost-by-size` notice, its test pin and its docs sample line change in one dispatch.
- No text outside `docs/private-project-redaction.md` describes a way for a ledger-row figure to meet the publication bar. Every ledger-row figure stays in the "does not meet" list until the reader PR changes the rule.
- A zero-valued `repo` row from a scope that read nothing is still written. The docs name it as a residual and add the scope axis to the `--force` re-capture advice.
- The payload check also rejects a payload that is not a list of objects.

One round-2 finding went to the engineer. Asked "should a pr-cost row captured with the removed-worktree prefix say `repo`, the same value as a row captured from live worktrees only?", the engineer selected "One value, repo (Recommended)".

The commit gate's review then changed the plan, and plan review round 3 followed with no blocker. The session's dispositions from both, not the engineer's words:

- A structural test pinning pr-cost as the only caller of the removed-worktree keyword is declined. The guard against a later caller is review of that caller's diff, under the P1 clause at `docs/private-project-redaction.md:166-167`.
- This plan makes no claim about whether `review-cost-by-size` can meet the publication bar after the reader PR. Four reviewers noted that a stored scope label is not itself the re-runnable refusal the rule asks for. Whether a reader that refuses rows not captured `repo` is such a refusal is for the reader PR and the rule's owner to settle. The engineer's "Rule unchanged (Recommended)" selection stands, so the reader PR does not change the rule without a fresh question to the engineer.
- The two new test files are registered in `TEST_FILES`, in dispatch 2.
- The CHANGELOG gives the docs' remedy for the empty-scope line: the prefix glob first, `--projects '*'` as the broadest fallback, and either one records `glob`.

The commit gate's third review found that filtering fork PRs out before the join would have raised a same-repository PR that shares a head name with a merged fork PR from `low` to `high` or `medium`. Asked whether to run one more review cycle or pause, the engineer selected "One more cycle (Recommended)". The session's dispositions from that cycle, not the engineer's words:

- Such a PR keeps today's `low` grade, set at the one call site of `_resolve_branch_pr`, whose signature does not change. `review-cost-by-size` keeps dropping the row.
- Every doc sentence and docstring that defines `low` or the read-mode gap listing is corrected for that case, with `docs/pr-cost.md` § "Join confidence" as the one home.
- Two more `repo` residuals are documented, not closed in code: a path whose slug collides with a live worktree's, and a local branch of a never-merged fork PR whose head name a merged same-repository PR also used.

The commit gate's fourth review raised five more wording findings and no design problem. The engineer asked for an outside consult ("Consult fable please") and then typed: "Let's go with Fable's recommendation". That recommendation, in the session's words: apply the pending fixes in their smallest form, mark the plan's verbatim doc text as draft, stop the residual list claiming completeness, and run one last cycle in which only a finding that changes production code or the persisted ledger format earns another plan edit. Any other finding goes to the engineer to keep as settled.

## Approach

`pr-cost` records each new row's capture scope, defaults to a repo scope, and refuses branch joins that name no single PR. The behaviors:
- Three trailing ledger columns arrive in one header bump:
  - `review_rounds_absence` stores the reason code the writer already computes for an empty `review_rounds` cell;
  - `earliest_activity_at` stores the branch's earliest surviving session start;
  - `capture_scope` stores `repo` or `glob`, set from `args.this_repo`, the field `_resolve_project_scope` picks its arm by.
- The whole `pr-cost` subcommand defaults to a repo scope: this checkout's worktrees plus every project directory under its main worktree's `.claude/worktrees/` slug prefix, matched by a rule moved out of routing-guard into `scope.py`.
- `repo` therefore covers more than the identity scope, a departure from GH-1426 guarantee 2's wording that the plan and the PR body name (row 48).
- A repo-scoped run that finds no priced turn says so on stderr.
- An explicit `--projects` glob still records, as `glob`, which the `--projects` help states.
- On both entry paths the writer refuses a PR that is cross-repository or whose head branch is a default-branch candidate, a detached `HEAD` or a `worktree-agent-*` name.
- A merged-PR payload that is not a list of objects with a boolean `isCrossRepository` exits 1.
- `--force` may replace a non-`repo` row with a `repo` row holding fewer rounds. Whether it may replace a `repo` row with a `glob` one is left to the reader PR (row 40).

This is one PR, writer side only; the reader rule follows in its own PR (row 6).

### Open decisions

None. The engineer settled size at about 1,350 lines (row 49), and the removed-worktree widening covers pr-cost's repo scope only (row 36).

### Design

**Scope primitives: `claude/.claude/scripts/transcript_analysis/scope.py` (dispatch 1).**
- `_add_project_scope_args` gains two keywords (row 11):
  - `repo_scope_default: bool = False`: when true, `--projects` defaults to None, so the caller can tell no flag from an explicit `--projects '*'`;
  - `projects_help: str | None = None`: the `--projects` help, mirroring the existing `this_repo_help`.

  A caller that omits both keeps today's `"*"` default and no help, so no other subcommand changes. In the docstring, the sentence on the `"*"` default gains "unless repo_scope_default is set". The sentence calling `--this-repo` "an identity-based minimization control" gains "except where a caller passes include_removed_worktrees to _resolve_project_scope" (row 25).
- `_repo_scoped_project_slugs`'s docstring, which rejects a `<slug>*` match by design (`scope.py:107-113`), gains one sentence: "_with_removed_worktree_slugs is the one deliberate prefix match, used only where a caller opts in." (row 25)
- `_branch_never_joins_a_pr(branch: str) -> bool` returns `branch in _DEFAULT_BRANCH_CANDIDATES or _names_no_pr_branch(branch)` (row 15). Docstring, verbatim: "True for a head branch name that no branch-to-PR join may use."
- Removed-worktree widening (rows 32 to 36):
  - `_WORKTREE_SLUG_INFIX = "--claude-worktrees-"` moves here from `routing_guard.py:84`.
  - `_with_removed_worktree_slugs(live_slugs: Sequence[str], project_dir_names: Iterable[str]) -> list[str]` returns `live_slugs`, then every name that starts with `live_slugs[0] + _WORKTREE_SLUG_INFIX` and is not that bare prefix, deduplicated in order. This is the rule at `routing_guard.py:956-959`, moved unchanged. Docstring, verbatim: "This checkout's worktree slugs plus every name under its main worktree's .claude/worktrees/ slug prefix, so a removed worktree's sessions stay in scope. A foreign checkout under that directory matches too, and so does a path whose slug collides with the prefix."
  - `_resolve_project_scope` gains a keyword `include_removed_worktrees: bool = False`:
    - when it is true in the repo arm, the arm lists each root with `_list_dir_recording_gaps(root, None, _SCAN_GAP_ROOT)` and passes `_with_removed_worktree_slugs(slugs, <their names>)` to `_iter_scoped_sessions`;
    - that listing passes no `scan_gaps`, because `_iter_scoped_sessions` lists the root again and reports an unreadable one itself;
    - the label becomes `this repo, removed worktrees included (N project dirs found)`, where N counts the widened names present under the roots, not candidate slugs;
    - the cached `args._this_repo_slugs` keeps the live list;
    - every caller that omits the keyword is unchanged.

**`claude/.claude/scripts/transcript_analysis/period_cost.py` (dispatch 1).** The compound guard at `:784` becomes `scope._branch_never_joins_a_pr(branch)`. At `:497` only the two-term tail moves: the line reads `branch is None or scope._branch_never_joins_a_pr(branch)`, because the helper takes a `str`. Behavior is unchanged. pr-cost's guard is the third use of the compound, so it becomes a shared helper.

**`claude/.claude/scripts/transcript_analysis/routing_guard.py` (dispatch 1).** `_capture_scope` builds `scoped_slugs` with `scope._with_removed_worktree_slugs(slugs, (entry.name for entry in on_root))` in place of the `orphan_prefix`, `orphan_slugs` and `scoped_slugs` lines (`:956`, `:958-959`), keeping the `on_root` listing at `:957`. Its matched-directory count comes from that same listing, as today. The refusal at `:1353` reads `scope._WORKTREE_SLUG_INFIX`. Behavior is unchanged (row 34).

**`claude/.claude/scripts/transcript_analysis/gh_cli.py` (dispatch 1).** `_gh_discover_merged_prs` appends `isCrossRepository` to its `json_fields` (row 16). workstream-cost, the other caller, reads only `headRefName` and does not change.

**Ledger format: `claude/.claude/scripts/transcript_analysis/pr_cost_ledger.py` (dispatch 2).**
- Today's `_PR_COST_LEDGER_COLUMNS` becomes the frozen `_PR_COST_LEDGER_PRE_CAPTURE_SCOPE_COLUMNS`, with the "never edit" comment its siblings carry. The current tuple is that one plus `review_rounds_absence`, `earliest_activity_at` and `capture_scope`, in that order. `capture_scope` goes last because a row this checkout writes never leaves it empty, so such a row never ends in a tab. Each new column carries a one-line comment:
  - `review_rounds_absence`: "Why review_rounds is empty; empty when the cell is present or the row predates this column. Checked for shape only, so a newer checkout's value still parses."
  - `earliest_activity_at`: "The branch's earliest surviving session start at capture; empty when the scan found none."
  - `capture_scope`: "Empty cell: recorded before this column, so the scope is unknown."
- `_PR_COST_LEDGER_COLUMNS_BY_HEADER_LINE` adds the frozen tuple. `_PR_COST_LEDGER_COLUMN_DEFAULTS` maps each new column to `""`, which is guarantee 1 (row 22).
- `capture_scope` is a closed enum beside `status` and `join_confidence` (row 23): `_PR_COST_CAPTURE_SCOPE_REPO = "repo"`, `_PR_COST_CAPTURE_SCOPE_GLOB = "glob"` and `_PR_COST_CAPTURE_SCOPE_VALUES`.
- `_parse_pr_cost_ledger_row_cells` adds three checks, and none echoes the cell:
  - `review_rounds_absence` is empty or fully matches `_MODEL_BREAKDOWN_KEY_RE`, the label shape both JSON cells already use, else `line N: malformed review_rounds_absence` (row 51);
  - `earliest_activity_at` is empty, or it fully matches `\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z` and parses with `datetime.fromisoformat`, else `line N: malformed earliest_activity_at`. That is the one form the writer emits, and the column has no legacy values to tolerate (row 52);
  - `capture_scope` is empty or in `_PR_COST_CAPTURE_SCOPE_VALUES`, else `line N: unknown capture_scope`.
- `_current_cells_from_file_row`'s short-row hint keys on defaults, not on JSON columns (row 21). With K the shortfall and the missing columns the file header's last K:
  - every missing column is a key of `_PR_COST_LEDGER_COLUMN_DEFAULTS` whose value is `""`: today's `(if an editor trimmed trailing whitespace, append a tab to this line)` at K of 1, and `append K tabs` in its place above 1;
  - K is 1 and the missing column's default is non-empty: today's restore hint;
  - otherwise no hint. A column absent from the defaults map, such as `risk_surface_flag`, has no empty default.

  Every existing header keeps today's hint byte for byte, pre-host and pre-model included. A legacy row rewritten under the new header ends in four empty cells, so a trimmed one is four short and still gets a hint.
- The module docstring's enum list names the `capture_scope` enum.
- The join-confidence comment at `:118-121` reads, verbatim: `# "high": direct headRefName match corroborated by plan-slug or SHA overlap. "medium": direct match, uncorroborated. "low": the head name matched more than one merged PR, a cross-repository one included; resolved by the branch-reuse tie-break (highest commit-SHA overlap, most recent mergedAt) among the same-repository matches, or unresolved (no row written).` (row 25)

**Writer: `claude/.claude/scripts/transcript_analysis/pr_cost.py` (dispatch 2).**
1. Scope (rows 4, 13, 28, 32, 36, 41, 47).
   - The subparser's own `help=` (`pr_cost.py:1207-1210`) replaces "list uncaptured merged PRs still in the local transcript window" with "list uncaptured merged PRs with activity in this repo's worktrees, removed ones included".
   - `_add_pr_cost_subparser` passes `repo_scope_default=True` and two help strings, verbatim:
     - `projects_help="Project-dir glob. Default: this repo's own worktrees, removed ones included. An explicit glob, * included, reads every project directory it matches, and the rows it records carry capture_scope=glob (see docs/pr-cost.md)."`;
     - `this_repo_help="Scope to this repo's own worktrees, removed ones included -- pr-cost's default (see docs/pr-cost.md)."`.
   - `_pr_cost_report` opens with two steps:
     - the default, inline: when `args.this_repo` is false and `args.projects` is None, set `args.this_repo = True`;
     - the class, once for the run: `capture_scope` is `pr_cost_ledger._PR_COST_CAPTURE_SCOPE_REPO` when `args.this_repo`, else `_PR_COST_CAPTURE_SCOPE_GLOB`. That line carries a one-line comment, verbatim: "# _resolve_project_scope picks its repo arm by this same field."

     Nothing writes `args.this_repo` after it, so the class names the arm both passes take (guarantee 2, row 47).
   - `_resolve_pr_cost_scope(args, root: Path, *, include_subagents: bool)` calls `scope._resolve_project_scope(args, "pr-cost", include_subagents=include_subagents, roots=[root], include_removed_worktrees=True)`. The dollar pass (`:793`) and `_scan_review_rounds_by_branch` (`:493`) both call it, so the two passes resolve by the same scope rule and the one cell describes both (row 9). Docstring, verbatim: "The one scope resolution both transcript passes use, so a row's capture_scope describes each."
   - Read mode and `--all-accounts` take the same default and the same widening. The repo arm's existing exits are the fail-closed behavior, and all of them come before any write.
   - Empty repo scope (rows 13, 14). After each account's dollar pass, when `capture_scope` is `repo` and the pass found no usage-bearing turn (`branch_totals` empty and the unbranched aggregate's `turn_count` 0), print this to stderr, verbatim: `pr-cost: the repo scope found no priced turn{for_account} -- see docs/pr-cost.md`.
     - `{for_account}` is empty, or ` in account-K` under `--all-accounts`.
     - It prints in read mode and under `--record`.
     - It covers a repo scope that matched no directory, and needs no new `scope.py` surface.
     - It names no cause, because the docs subsection on capture scope is the one home for the sessions the repo scope does not read (row 56).
     - The read-mode listing header does not change, because this line precedes its `(none)` in the one case where `(none)` would mislead.
   - The rename warning at `:1081-1088` replaces "if the branch was renamed" with "if the branch was renamed or its sessions sit outside this run's scope". It fires only when `branch_totals` is non-empty (`:1076`).
2. Joinability (guarantee 3; rows 2, 3, 7, 53, 63).
   - Payload check. Right after discovery (`:783`), before any account work, `_require_well_formed_merged_prs(merged_prs)` exits 1 when the payload is not a list, when an entry is not a dict, or when an entry's `isCrossRepository` is not a `bool`. It prints one line for all three, verbatim: `pr-cost: gh pr list returned merged PRs this checkout cannot join (not a list of objects, each with a boolean isCrossRepository) -- refusing to join any PR; update gh, and report it if it repeats (see docs/pr-cost.md)`.
     - The line claims no flag was read, because the first two failures stop before any flag is.
     - Exit 1 follows the unparseable-JSON exit at `gh_cli.py:486-488`, because discovery has no per-row result to degrade into (row 53).
     - It runs in read mode too, because both modes join.
   - `_pr_is_joinable(pr) -> bool` is true only when `pr.get("isCrossRepository") is False` and `not scope._branch_never_joins_a_pr(pr["headRefName"])`. It stays fail-closed behind the payload check. Docstring, verbatim: "False for a cross-repository PR, a PR whose isCrossRepository is not a boolean false, and a head branch no join may use."
   - Branch walk, with one line per cause:
     - before matching, a branch for which `scope._branch_never_joins_a_pr` holds prints `pr-cost:   this branch is a default-branch candidate, a detached HEAD or a worktree-agent branch, which the ledger never joins -- skipped`;
     - after today's no-match skip (`:984-986`), keep only joinable matches. When none remains, every match was cross-repository: print `pr-cost:   every merged PR for this branch is cross-repository, which the ledger never records -- skipped`;
     - the enrichment loop (`:990`) runs over the joinable list, so no fork PR is enriched;
     - `_resolve_branch_pr` resolves among joinable PRs only, so a fork PR never decides which PR the row joins. Its signature and docstring do not change, so its seven existing unit calls need no edit;
     - at its one call site (`:1002`, shared by the walk and `--pr N`), after the ambiguous skip, `join_confidence` becomes `pr_cost_ledger._PR_COST_JOIN_CONFIDENCE_LOW` when the unfiltered match count exceeds 1;
     - a same-repository PR whose head name a merged fork PR also used therefore keeps today's `low` grade (`pr_cost.py:200-204`), and `review-cost-by-size` keeps dropping it (`ledger_rounds.py:89-90`). A fork PR that was never merged is not in the discovery list, so it changes no grade.
   - `--pr N`, after the not-found exit (`:971-973`), exits 1 under `--all-accounts` too, as not-found does. The check runs on N itself, because the head-name match can resolve to another PR (row 17):
     - a guarded head prints `pr-cost: PR #N's head branch is a default-branch candidate, a detached HEAD or a worktree-agent branch, which the ledger never joins -- refusing; see docs/pr-cost.md`;
     - otherwise a non-joinable N prints `pr-cost: PR #N is cross-repository, which the ledger never records -- refusing; see docs/pr-cost.md`.
   - Read mode: `_print_pr_cost_uncaptured` keeps only joinable matches before its `len(matches) != 1` test, so it lists only PRs `--record` can capture. Its docstring's "an unambiguous direct headRefName match (a branch with zero or more-than-one match" becomes "exactly one joinable direct headRefName match (a branch with none, or more than one," (`pr_cost.py:637-644`, row 25).
   - The filter sits at each call site, not inside `_direct_headref_matches`, because the walk needs the unfiltered matches to tell "no merged PR" from "only fork PRs", and `--pr N` looks PRs up by number, not through that function (row 54).
3. Row cells (rows 18, 19, 29).
   - `_new_pr_cost_row` gains keyword-only `capture_scope: str` and `review_rounds_absence: str | None`. It writes `review_rounds_absence or ""`, and `earliest_activity_at` from `_earliest_activity_at(agg)`.
   - `_earliest_activity_at(agg: dict) -> str` takes the smallest epoch in `agg["session_starts"]`, whose entries are `(float_epoch, session_id)` (`cost.py:117-121`). It returns `_iso_utc_seconds(datetime.fromtimestamp(epoch, UTC))`, or `""` when that list is empty.
   - `_iso_utc_seconds(moment: datetime) -> str` returns `moment.isoformat(timespec="seconds").replace("+00:00", "Z")` for a UTC datetime, which truncates a fractional second. `captured_at` at `:1090` calls it too.
   - The production caller (`:1101`) passes the run's `capture_scope` and the `rounds_absence` that `_review_rounds_cell_outcome` returned.
   - The single-account `--pr N` zero-valued row stays. It now carries the run's `capture_scope`, `no-local-activity` and an empty `earliest_activity_at`.
4. Absence vocabulary (row 51). `_ROUNDS_ABSENCES` stays in pr_cost.py as the writer's closed set, and `_review_rounds_cell_outcome` keeps refusing a value outside it. The export reads that same set (Export below), so the vocabulary has one home. `not-built` stays in the set, though this checkout's writer cannot reach it (row 50).
5. `--force` re-scope (guarantee 4; rows 26, 27). `_review_rounds_force_refusal` gains keyword-only `prior_capture_scope: str` and `new_capture_scope: str`. Its first matching case decides:
   1. prior cell empty: allowed;
   2. new cell empty: refused, with today's reasons;
   3. new `rule` lower than prior: refused;
   4. prior scope not `repo` (empty included) and new scope `repo`: allowed;
   5. equal `rule`, and some skill with fewer rounds or a smaller summed `agents`: refused;
   6. otherwise: allowed.

   - Case 4 carries a comment, verbatim: "# A move to the repo class may shrink the series, because the prior row's rounds can hold another repository's sessions. This also passes a shrink from aged-out transcripts."
   - The call site (`:1118`) passes `already["capture_scope"]` and the new row's.
   - The refusal line and the exit handling do not change.
   - A `glob` re-capture over a `repo` row falls through to today's same-rule guard; refusing it is the reader PR's question (row 40).

**Export: `claude/.claude/scripts/transcript_analysis/pr_cost_export.py` (dispatch 2).**
- `_redact_pr_cost_row_for_export` applies the unchanged `_pr_cost_export_date_only` to `earliest_activity_at`, beside `merged_at` and `captured_at`. The parser's strict check guarantees that cell a `T`, so today's truncation reduces it to a date (row 52).
- The docstring at `pr_cost_export.py:32-36` reads, verbatim: "Date portion of a writer-form ISO8601 timestamp (YYYY-MM-DDTHH:MM:SS[Z|+HH:MM]). Applies to merged_at, captured_at and earliest_activity_at. The parser holds earliest_activity_at to the writer's form. merged_at and captured_at accept any fromisoformat value, so a hand-edited one without a T passes through whole." Its `rate_stamp` sentence stays. It extends no "validated" claim to the new column.
- The `_redact_pr_cost_row_for_export` docstring at `:103` reads "merged_at/captured_at/earliest_activity_at are truncated to a date" (row 25).
- `capture_scope` passes through as a closed enum (row 24).
- `review_rounds_absence` passes through when it is empty or in `pr_cost._ROUNDS_ABSENCES`, and any other value exports as `unrecognized` (row 51). This mirrors the vocabulary filter the export applies to `review_rounds`. The import adds no cycle, because only `cli.py` imports `pr_cost_export`.

**`claude/.claude/scripts/transcript_analysis/review_cost_by_size.py` (dispatch 2).** `_SCOPE_UNVERIFIED_NOTICE` reads, verbatim: `Scope unverified -- this report selects pr-cost ledger rows by this checkout's origin repository and does not filter on the capture scope a row records. A PR's rounds can include another repository's session on a same-named branch; see docs/transcript-analysis.md § "review-cost-by-size".` Only the wording changes. The report still selects rows by origin host and repository (`ledger_rounds.py:96-104`), then applies its own exclusions (`ledger_rounds.py:82-93`), and it reads no capture scope (rows 6, 25).

**Docs (dispatch 3, except the one sample line named below).** Each edit states what the code now does (row 25).
- `docs/pr-cost.md`:
  - "Schema": a table row per new column.
  - A new subsection under "Schema", "Capture scope, rounds absence and earliest activity", is the three columns' canonical home, and the other sites cite it. It states:
    - `repo`: this checkout's worktrees plus the removed-worktree prefix. Both passes read only the directories of this checkout's `git worktree list` entries, plus every directory whose name starts with the main worktree's slug followed by `--claude-worktrees-`. The class is therefore not identity-scoped, and pr-cost's `--this-repo` reads more than every other subcommand's (row 48).
    - The `repo` residuals, each with its location:
      - read as this repository's: a foreign repository cloned under this checkout's `.claude/worktrees/`, a path whose slug collides with that prefix, and a checkout elsewhere whose path maps to the same project-directory slug as one of this checkout's live worktrees (`docs/transcript-analysis.md`'s `--this-repo` section, first gap);
      - read: a session started in a subdirectory of any worktree under `.claude/worktrees/`, live or removed, because its slug carries the prefix;
      - not read: a session started in a subdirectory of the main checkout or of a worktree elsewhere, a worktree removed from outside `.claude/worktrees/`, and another clone (cite `docs/transcript-analysis.md`'s `--this-repo` section, `:47-70`);
      - silent: a scope that reads some sessions but none of one PR's prints nothing, because the walk never visits that PR;
      - counted: a subagent file under an in-repo session, whatever repository the subagent worked in;
      - scoped to the checkout that ran `--record`: `capture_scope` names that checkout's scope;
      - the row's `repo` column comes from that checkout's `origin`, so a reader also matches the row's repository to the publishing one;
      - zero-valued: a `repo` row whose `review_rounds_absence` is `no-local-activity` and whose `earliest_activity_at` is empty holds zero because the scope read nothing for the branch. That is either a PR built without Claude Code or work the scope does not read, and the row cannot tell the two apart;
      - joined by name to the wrong PR: sessions on a local branch of a fork PR that was never merged, whose head name a merged same-repository PR also used, because the discovery list holds merged PRs only, up to the cap `docs/transcript-analysis.md:1510` documents. The row keeps its `high` or `medium` grade;
      - asserted, not authenticated: `capture_scope` records the scope of the run that wrote it, and the ledger file has no checksum, so a hand edit to the cell leaves no trace.

      The subsection lists the known residuals of a `repo` row and does not claim to be complete. It does not say how a figure from such a row could meet the publication bar, which stays `docs/private-project-redaction.md`'s to say. The PR body links to the subsection (Shipping).
    - `glob`: an explicit `--projects` glob, `*` included, whatever it matched. A glob that matches only this repository's directories still records `glob`.
    - An empty `capture_scope` means the row predates the column, and a new class arrives under a new header.
    - `review_rounds_absence` holds a reason code when `review_rounds` is empty on a row whose `capture_scope` is not empty, each mapped to its stderr reason. A newer checkout may add a code, and readers tolerate one they do not know.
    - `earliest_activity_at` is the earliest first-valid-usage-turn timestamp among the sessions the dollar pass attributed to the branch, in UTC to the second, as `YYYY-MM-DDTHH:MM:SSZ`. A span from it to `captured_at` near the capturing account's `cleanupPeriodDays` means older sessions may have aged out. A shorter span makes truncation unlikely but does not rule it out.
  - The header paragraph (`:37`): six headers and the three empty defaults.
  - The hint bullets (`:41-44`): the rule by default and shortfall, with a column outside the defaults map giving no hint.
  - "Trailing cell" (`:89`): `capture_scope` is last and never empty on a row this checkout writes, and a row recorded earlier ends in four empty cells.
  - "Data" (`:244`) reads, verbatim: "Two edits are exempt: restoring the trailing tabs an editor stripped, and blanking a `model_breakdown` or `review_rounds` cell that fails to parse (keep its tab; the blanked cell is no longer the line's last cell, because three columns follow `review_rounds`). Only the tab restore leaves every value unchanged."
  - "Write-time check failures" (`:158`) reads, verbatim: "To find the cause after an exit 1 for a failed review-round pass, run `review-round-cost` with the failed run's scope flags minus `--all-accounts`, and with the top-level `--config-dir PATH` (placed before the subcommand name) set to the failing account's config directory. A bare pr-cost run has no scope flag to copy: it read this checkout's repo scope, so pass `--projects` with the prefix glob from `docs/transcript-analysis.md`'s `--this-repo` section, which reads those directories and more."
  - "Join confidence" (`:222`), the one home for the `low` grade: the `low` bullet reads, verbatim: "`low` — the branch name matched more than one merged PR (branch-name reuse), a cross-repository PR included. The writer resolves among the same-repository matches by highest commit-SHA overlap, ties broken by most recent `mergedAt`, and a remaining tie leaves the row unresolved (no row written). A same-repository PR whose head name a merged cross-repository PR also used records at `low` for that reason alone, its row can hold the cross-repository PR's local review spend, and a `--force` re-capture grades it `low` again. `review-cost-by-size` excludes every `low` row."
  - "Absence" (`:76-87`): `review_rounds_absence` names which state applies, and the retention bullet points at `earliest_activity_at` in place of "No field marks that case".
  - "Upgrading an existing ledger" (`:198`): the three empty defaults.
  - "Default (read) output" (`:248`):
    - the uncaptured-PR listing reads the run's scope, the repo scope by default, while the ledger listing prints every row;
    - the listing names only PRs `--record` can capture, so it no longer shows a fork PR or a PR with a guarded head;
    - the unambiguous-match clause reads, verbatim: "The gap listing is restricted to branches with exactly one same-repository merged PR matching directly on `headRefName` (a branch matching none, or more than one same-repository PR, needs the manual audit `join_confidence: low` rows point at, not this quick check). A cross-repository PR does not count toward that match, so a same-repository PR whose head name a merged fork PR also used is listed, and `--record` writes its row at `low`."
  - "`--record`'s capture" (`:250-254`):
    - the repo default and its removed-worktree reach, the fail-closed exits, and the `glob` class;
    - the empty-scope line, quoted verbatim, followed by one sentence: "When it prints, a session started in a subdirectory is read by the prefix glob in docs/transcript-analysis.md's `--this-repo` section, which records glob, and an account with no work on this repository inside the retention window needs nothing.";
    - fork PRs and PRs whose head is a default-branch candidate, a detached `HEAD` or a `worktree-agent-*` name record nothing. The review spend of a local checkout of a merged fork PR stays out of the ledger unless a same-repository PR used the same head name, and then it lands in that PR's row at `low` (cite "Join confidence"):
      - the walk skips each with its own line;
      - `--pr N` exits 1 for one;
      - there is no override;
      - `cost --branches <branch>` gives an ad hoc figure;
    - a merged-PR payload that is not a list of objects with a boolean `isCrossRepository` exits 1, in read mode too.
  - "The re-record contract", the opening caveat (`:270`) and the `model_breakdown` re-capture paragraph (`:272`): a bare `--force` re-capture of a row whose `capture_scope` is empty reads the repo scope only, and `--projects '*'` reproduces the original scan.
  - "The re-record contract", the guard (`:282-305`):
    - the six-case table above;
    - the reasons at `:302` and `:305`, for a row whose `capture_scope` is empty: a bare run is now narrower than the bare run that recorded it, and `--projects '*'` reproduces that original scan and records `glob`;
    - the same reasons, for a row recorded with explicit flags: repeating them stays correct;
    - at case 4: the waiver also passes a shrink from aged-out transcripts, and the new row's `earliest_activity_at` is the operator's check for it;
    - a `glob` re-capture over a `repo` row appends a `glob` row, which becomes the current row.
  - Export, the opening paragraph (`:396`): "the two remaining timestamps" becomes three, truncated to a date for a writer-form value.
  - Export, "Parsing" (`:402`): the new last column.
  - Export, "Columns" (`:410`): `earliest_activity_at` joins the date-truncated set, the truncation is qualified to writer-form values for `merged_at` and `captured_at`, and an unknown `review_rounds_absence` exports as `unrecognized`.
  - Export, the byte-identical sentence (`:413`): `review_rounds_absence` joins its exceptions, because an unknown value exports as `unrecognized`.
  - Export, the correlation bullet (`:445`): three truncated dates.
  - Export, the publication bullet (`:455`): it keeps every ledger-row figure in its "does not meet" list. It reads "A figure read from any ledger row, `review-cost-by-size`'s included: a row whose `capture_scope` is not `repo` can hold another repository's session on a same-named branch, and a `repo` row carries the residuals in "Capture scope, rounds absence and earliest activity" above." It names the subsection the way this file's other self-references do, with no `§`, because the citation grammar takes a backticked target.
  - "Combining exports" (`:483`, `:491`): the three columns are optional, and the retention bullet points at `earliest_activity_at`.
- `docs/transcript-analysis.md`:
  - `:49`: the sentence saying `--this-repo` resolves worktrees "by identity ... matched as exact project-directory names" already carves out `review-cost-by-size`. It gains the same kind of exception for pr-cost's `--this-repo`, which matches by name prefix and includes removed worktrees, with a pointer to `docs/pr-cost.md` § "Capture scope, rounds absence and earliest activity" (row 25).
  - `:53`: pr-cost joins the repo-scoped defaults.
  - The orphaned-directory bullet (`:67`): pr-cost's repo scope and routing-guard's capture both read these directories by the name prefix, with the residuals pr-cost.md states.
  - The pr-cost flags bullet (`:1195`): the default, and that pr-cost's `--this-repo` includes removed worktrees.
  - The pr-cost "Default (read) output" paragraph (`:1210`): the same clause as the `docs/pr-cost.md:248` edit. The uncaptured-PR listing reads the run's scope, the repo scope by default, and names only PRs `--record` can capture.
  - review-cost-by-size's "Known limits" (`:1511-1515`): the report does not filter on `capture_scope` or read `earliest_activity_at`. Each exposure names the rows it holds for, and each sentence cites the `docs/pr-cost.md` section that states it:
    - the fork and default-branch exposure holds only for rows recorded before this change, because the writer now records nothing for such a PR. It cites `docs/pr-cost.md` § "`--record`'s capture". This fork sub-bullet (`:1513`) gains, verbatim: "A same-repository PR whose head name a merged fork PR also used records as `low`, which the report excludes (`docs/pr-cost.md` § "Join confidence").";
    - the same-named-branch exposure holds for `glob` rows and for rows recorded before this change, and can hold for `repo` rows. It cites `docs/pr-cost.md` § "Capture scope, rounds absence and earliest activity".
  - review-cost-by-size's "Publication" (`:1522`) reads, verbatim, backticks included: ``The report is not a publication instrument. It applies no filter on the capture scope a row records and is not a command that refuses a wider project-directory scope for the reads behind its rows. See `docs/pr-cost.md` § "Capture scope, rounds absence and earliest activity" for what a `repo` row can still hold. A figure from the report publishes only under `docs/private-project-redaction.md` § "The owner can authorize one figure, case by case".``
  - The sample (`:1530`): the new notice. This one line is dispatch 2's, because `tests/test_transcript_review_cost_by_size.py` compares the sample with the report's output.
- `docs/transcript-analysis-architecture.md:443`: the `pr_cost_export.py` import list gains `pr_cost` (row 25).
- `docs/private-project-redaction.md:139-141`: the reason becomes one sentence of facts about the report, with nothing linking a recorded scope to refusal: "The reason: it selects ledger rows by this checkout's origin repository, applies no filter on the capture scope a row records, and is not a command that refuses a wider project-directory scope for the reads behind its rows." The instrument list and every rule stay as they are (row 45).
- `docs/transcript-analysis-architecture.md:827-837`: the pr-cost test-family paragraph counts ten test files, not eight, and names the capture-scope seam of the two new ones (row 25).
- `CHANGELOG.md`:
  - `:25`, the Unreleased `review-cost-by-size` entry, gains one sentence: "Only a row recorded before this release's pr-cost join guard can hold such a PR." Both entries ship in one release, so the release notes stay accurate.
  - One new entry under "Changed". Its **Migration** bullets cover:
    - the header bump: older checkouts refuse the upgraded ledger, there is no downgrade, and the way out is to roll forward;
    - the repo default for read mode and `--record`, its removed-worktree reach and residuals, and `--projects '*'` as the old scan;
    - the empty-scope line, and the action when it prints: the prefix glob from `docs/transcript-analysis.md`'s `--this-repo` section first, and `--projects '*'` only as the broadest fallback. Either one records `capture_scope=glob`;
    - an explicit `pr-cost --this-repo` also reads removed worktrees now;
    - fork PRs and default-branch, detached-`HEAD` and `worktree-agent-*` heads record nothing. A merged fork PR's local review spend stays out of the ledger unless a same-repository PR used the same head name, and then it lands in that PR's row at `low`, which `review-cost-by-size` excludes. `--pr N` exits 1 for one, and read mode's uncaptured listing no longer names one;
    - a merged-PR payload that is not a list of objects with a boolean `isCrossRepository` exits 1;
    - the `--force` re-scope, and that it also waives a retention shrink;
    - the three new export columns, with `earliest_activity_at` truncated to a date as `merged_at` and `captured_at` are;
    - the review-cost-by-size notice wording.

### Alternatives set aside
- Two PRs, the join guard and the scope helpers first: the only split guarantee 3 permits. The engineer chose to proceed as one PR, twice (rows 42, 49).
- An export date fix that parses `merged_at` and `captured_at` before truncating: left unticked by the engineer and cut (row 59). Only a hand edit reaches the gap it closes (Out of scope).
- One scope resolution shared by both passes: session iterators are single-use, the round pass runs lazily, and the arm is already a function of the shared `args` (row 9).
- A resolver variant that reports each pass's arm, combined per row: heavier, and it can only ever combine two equal values (row 9).
- Parsing the scope label: the glob arm's label is the raw glob (row 10).
- Moving `--projects`'s default for every caller of `_add_project_scope_args`: it silently changes 40 call sites (row 11).
- `set_defaults(this_repo=True)` on pr-cost: an explicit `--projects` would still take the repo arm.
- A shared `_apply_repo_scope_default` helper and a `_project_scope_is_this_repo` predicate: pr-cost is their only caller, and the class reads the same field the arm does (rows 46, 47).
- A pr-cost-only scope group, as skill-invocation has (row 12): a third copy of the group, where a keyword keeps one.
- A shape-only check on `capture_scope`: it gates the reader, so a closed set suits it, and `status` and `join_confidence` are this ledger's scalar-enum precedent (row 23).
- A closed set for `review_rounds_absence` at parse, with a test tying any change of the set to a header bump: a new write-time check is the likeliest change to the cell, and each one would lock out every older checkout (row 51).
- A ledger-owned copy of the absence set for the export: the export can read `pr_cost._ROUNDS_ABSENCES` directly, which keeps one home (row 51).
- A content check on prefix-matched directories, such as reading each session's `cwd`: excluded by the session's disposition (row 48).
- A stderr line naming the scope's matched-directory count from `scope.py`: the resolver cannot label an account, and the priced-turn check in pr-cost needs no new `scope.py` surface.
- A read-mode listing header naming its scope: the empty-scope line already precedes `(none)` in the case that misleads.
- The absence reason inside the `review_rounds` cell: that cell's shape is a persisted contract (`docs/pr-cost.md:104`).
- A retention marker built from the scanned scope's oldest session: in a repo scope that says little about one branch.
- A retention marker built from `cleanupPeriodDays` read from settings: nothing reads that setting today (row 20).
- Refusing the zero-valued `--pr N` row: it drops a PR built without Claude Code, which a cost-by-size reader needs (row 29).
- Joining a PR whose `isCrossRepository` is absent, to spare test edits: the tests' gh fake supplies the field when the argv requests it instead.
- Validating `isCrossRepository` inside `gh_cli._gh_discover_merged_prs`: workstream-cost shares that call and never reads the field.
- Widening `--this-repo` in every subcommand: outside row 32's question, and it would change listed publication instruments' corpus (row 36).
- A third class for the widened scope: the contamination it would mark needs an improbable precondition (row 48).
- A second copy of routing-guard's prefix rule in pr_cost.py: two homes for one rule.
- Widening inside `_iter_scoped_sessions`: that iterator serves every `--this-repo` caller.
- A `--force` refusal for a `glob` re-capture over a `repo` row: deferred to the reader PR (row 40).
- A stderr line on a glob `--record`: replaced by the `--projects` help (row 41).
- A `--record` that refuses a wider-than-repo capture outright, as `cost --this-repo --summary` refuses (`docs/private-project-redaction.md:105-113`): every row would be repo-scoped by construction. The engineer's selection "Repo default, glob allowed (Recommended)" rules it out (row 4).
- A `_resolve_branch_pr` keyword carrying the unfiltered match count: it changes a signature seven unit calls use, where one line at the call site sets the same grade (row 63).
- Counting a never-merged fork PR's head name toward the unfiltered match count, through `gh_cli._gh_discover_closed_unmerged_pr_branches`: it adds a second discovery call for a case the docs can name as a residual.

### Shipping
- One PR, with three sequential `code-writer` dispatches (Critical files).
- The PR body references GH-1426 without a closing keyword, because item 5, the reader rule, stays open. It may close GH-1426 only if item 5 already sits in an issue of its own (row 37).
- The PR body states the departure from GH-1426 guarantee 2's "identity-resolved" wording, and why (row 48).
- The PR body records the fork-PR skip as the issue's default, taken by the engineer's selection (row 3).
- The plan prescribes no edit to any GitHub issue.
- A revert does not restore an upgraded ledger: once a `--record` from this PR rewrites it, a checkout from before the PR refuses it. Roll forward, as `CHANGELOG.md:194` says of the previous column.
- The later reader PR (row 6) starts from the `repo` residuals in `docs/pr-cost.md`'s "Capture scope, rounds absence and earliest activity" subsection, their one home. The PR body links there instead of restating them.

### Assumption ledger

**Root:** `pr-cost --record` joins each row's dollars and `review_rounds` cell to a PR by head-branch name alone. It reads every project directory in the account by default and records nothing about the scope it read. No reader can therefore tell a row that may hold another repository's same-named-branch sessions from one that cannot.

**Givens:**
- G1. The ledger header is a persisted contract. A new column arrives only under a new header, the outgoing header is frozen, an older checkout refuses the upgraded file, and there is no supported downgrade. Ledgers already on every stow consumer's disk carry this contract, so changing it is outside this plan. [verified: `pr_cost_ledger.py:25-92,480-493`; `docs/pr-cost.md:37,47,206-208`]
- G2. Claude Code deletes transcripts older than `cleanupPeriodDays`. A legacy row's scope therefore cannot be reconstructed, and a capture cannot see sessions already deleted. The vendor owns this. [verified: `docs/pr-cost.md:3,87`]
- G3. A session's transcript sits in a project directory slugged from its starting directory. `--this-repo` therefore finds this repository's sessions only through `git worktree list`, and a removed worktree's directory is no longer listed. Claude Code's layout and git own this. [verified: `scope.py:96-118,203`; `docs/transcript-analysis.md:67`]
- G4. `isCrossRepository` is a boolean field of `gh pr list --json`, and gh owns it. [verified: `period_cost.py:85,764-769` requests it and classifies a non-boolean as an unexpected payload; `claude/.claude/scripts/_review-pr-lib.sh:170,193` does the same] That gh always returns a field the argv requests is [unverified].

**Rows.**
1. [engineer-verified: "Rule unchanged (Recommended)"] The publication rule stays as it is. The engineer selected this label when asked whether review-cost-by-size should become publishable under the existing rule, unchanged, once rows record scope and the report excludes rows not captured repo-scoped. Whether it then can is row 61's.
2. [engineer-verified: "No row at all (Recommended)"] `--record` writes no row for a PR whose head branch is `main`, `master`, `develop`, a detached `HEAD` or a `worktree-agent-*` name.
3. [engineer-verified: "Skip fork PRs (Recommended)"] `--record` skips every cross-repository PR.
4. [engineer-verified: "Repo default, glob allowed (Recommended)"] A bare `pr-cost --record` defaults to a repo scope, and an explicit `--projects` glob stays allowed. What that repo scope reads is row 62's.
5. [unverified] The session's proposal: "glob allowed" means an explicit `--projects` glob still records, and its row carries a non-repo class. Adopted as `glob`.
6. [unverified] The session's proposal: the reader rule (GH-1426 item 5) and publication text in `docs/private-project-redaction.md` and `CLAUDE.md` belong to a later PR. Adopted for the rule, the instrument list and `CLAUDE.md`. Narrowed for one sentence: the reason at `docs/private-project-redaction.md:139-141` says rows do not record scope, which this PR makes false, so its wording changes here (rows 25, 45). `docs/pr-cost.md:455` is corrected for the same reason and keeps a pointer to the `repo` residuals.
7. [unverified] The session's proposal: the walk prints a per-branch skip line, and `--pr N` exits 1 for a guarded PR. Adopted.
8. [unverified] The issue's writer guarantees 1 to 4 and its shape-2 asks, as the dispatch summarized them. This plan's author did not read the issue body. A round-1 reviewer did, and quoted two sentences this plan relies on: guarantee 2's "Only an identity-resolved repo scope earns the repo class." and guarantee 4's "Rewrite the advice at `docs/pr-cost.md:297`".
9. Both transcript passes call `_resolve_project_scope(args, "pr-cost", ...)` with the run's one `args`, and the repo arm caches its slug list on `args`, so both resolve by the same scope rule. [verified: `pr_cost.py:491-498,793-795,979`; `scope.py:592-610`]
10. `_resolve_project_scope` picks its arm by `args.this_repo` alone, and the glob arm's label is the raw glob. [verified: `scope.py:592,611-632`]
11. `--projects` defaults to `"*"` through `_add_project_scope_args`, which 40 call sites in 34 modules use, so pr-cost cannot tell an explicit `--projects '*'` from no flag. [verified: `scope.py:509-521`; `pr_cost.py:1213`; Grep count of `_add_project_scope_args(` under `transcript_analysis/`, less scope.py's definition]
12. skill-invocation is the one subcommand that already defaults to repo scope, through its own group with `--projects default=None`. [verified: `skill_invocation.py:95-110,265-277`; `docs/transcript-analysis.md:53`]
13. Facts of today's repo arm:
    - it exits 1 when git fails, when no worktree is listed, and when the working directory is outside this checkout's worktrees [verified: `scope.py:120-201`];
    - its zero-match exit needs a top-level `--config-dir`, which pr-cost refuses, so a resolved scope with no matching directory records nothing, silently, as an unmatched glob does [verified: `scope.py:597-607,702-708`];
    - its slug list does not depend on the root, so `--all-accounts` reuses it for every account [verified: `scope.py:734-737`].
14. Row 34's prefix keeps a removed worktree's sessions in scope only for a worktree under `.claude/worktrees/` (G3, row 32). [verified: `routing_guard.py:956-958`] For a worktree removed from anywhere else, under the new default:
    - the walk never visits a PR whose sessions all ran there [verified: `pr_cost.py:976`];
    - `--pr N` writes a zero-valued row [verified: `pr_cost.py:1062-1089`];
    - the rename warning precedes that row only when the scan attributed activity to some other branch, and the empty-scope line precedes it when the scan found no priced turn [verified: `pr_cost.py:1076`];
    - `--force --pr N --projects '<glob>'` re-captures the PR as `glob`, appending a row that becomes the current one (row 40) [verified: `pr_cost_ledger.py:558-582`].

    How often a stow consumer adds a worktree outside `.claude/worktrees/` and removes it before capture is [unverified].
15. The writer applies neither the branch-name guard nor the fork guard, and `period_cost.py` repeats the compound branch predicate at two sites. [verified: Grep of `pr_cost.py` for `_DEFAULT_BRANCH_CANDIDATES` and `_names_no_pr_branch`, no match; `period_cost.py:497,784-787`]
16. Merged-PR discovery requests no `isCrossRepository`, `--pr N` looks N up in that list, and workstream-cost, the other caller, reads only `headRefName`. [verified: `gh_cli.py:472-491`; `pr_cost.py:969-974`; `workstream_cost.py:88-90`]
17. `--pr N` joins by N's head name and records whichever PR `_resolve_branch_pr` picks, which can be another PR sharing that name. [verified: `pr_cost.py:974,983,1002-1009`]
18. After `_review_rounds_cell_outcome`, `rounds_absence` is one of eight values, or None beside a cell. The writer uses it for the force refusal and the stderr reason and stores it nowhere. [verified: `pr_cost.py:357-377,418-435,1091-1100,1118,1164`]
19. The dollar pass records each session's first timestamped turn with valid usage on a branch in `session_starts`, and workstream-cost sorts that list for the branch's earliest session start. [verified: `cost.py:44-56,74-80,117-121,249`]
20. [unverified: the dispatch's exploration report, not re-grepped] Nothing in pr-cost reads `cleanupPeriodDays`.
21. The short-row hint today tests whether the header's last column is a JSON column, then whether its default is non-empty, and both JSON columns default to `""`. `risk_surface_flag`, the last column of the pre-host and pre-model headers, has no entry in the defaults map, and those headers give no hint. Keying on membership in the map with an empty value therefore gives every existing header today's hint. [verified: `pr_cost_ledger.py:84-92,464-471`; `tests/_pr_cost_helpers.py:529-538`]
22. A legacy row reads each missing column from `_PR_COST_LEDGER_COLUMN_DEFAULTS`, and the write refuses unless every prior row's parsed values survive, so empty defaults keep legacy rows unchanged. [verified: `pr_cost_ledger.py:460-477,648-668,708-729`]
23. `status` and `join_confidence` are closed enums named in `pr_cost_ledger.py`, the parser refuses an unknown value of either, and pr_cost.py references them by name. [verified: `pr_cost_ledger.py:113-127,431-434`; `pr_cost.py:997,1012`]
24. The export derives its columns from the ledger tuple, a test fails until every ledger column is triaged, and `_pr_cost_export_date_only("")` returns `""`. [verified: `pr_cost_export.py:24-37,125-126`; `tests/test_transcript_pr_cost_export.py:198-225`]
25. The statement sites this PR touches, by disposition. [verified: the lines cited]
    - Made false for new rows, because each says rows record no scope, no field marks retention loss, or the writer has no fork or branch-name guard:
      - `review_cost_by_size.py:40-44`;
      - `docs/transcript-analysis.md:1511-1515,1522,1530`;
      - `docs/pr-cost.md:87,455,491`;
      - `docs/private-project-redaction.md:139-141`;
      - `CHANGELOG.md:25`.
    - Made false by the new default: the subparser help at `pr_cost.py:1207-1210`, and the rename warning at `pr_cost.py:1085`, which names one cause of two.
    - Made false by the new columns or the export's handling of them:
      - `docs/pr-cost.md:396`, which counts two remaining timestamps;
      - `docs/pr-cost.md:413`, whose byte-identical exceptions omit `review_rounds_absence`;
      - `pr_cost_export.py:32-36` and `:103`, which name two date columns;
      - `docs/transcript-analysis-architecture.md:443`, whose `pr_cost_export.py` import list lacks `pr_cost`.
    - Made false by the two new test files: `docs/transcript-analysis-architecture.md:827-837`, which counts "All eight pr-cost-family test files" and names each one's seam.
    - Made false by the join filter, for a same-repository PR whose head name a merged fork PR also used:
      - `docs/pr-cost.md:222`, whose `low` bullet names only the tie-break;
      - `docs/pr-cost.md:248`, whose gap listing excludes a branch matching more than one merged PR;
      - `pr_cost_ledger.py:118-121`, whose comment says the same of `low`;
      - the `_print_pr_cost_uncaptured` docstring at `pr_cost.py:637-644`;
      - `docs/transcript-analysis.md:1513` and the planned fork sentences in `docs/pr-cost.md`'s "`--record`'s capture" and the CHANGELOG, which say a fork PR's local review spend never reaches the ledger.

      The `_resolve_branch_pr` docstring stays true, because the grade is set at its call site (row 63).
    - Made false by the header bump and the default: `docs/pr-cost.md:244`, whose blanked-cell parenthetical puts the tab at the line's end, and `docs/pr-cost.md:158`, whose advice to copy the failed run's scope flags has nothing to copy after a bare run.
    - Made false by the widening:
      - the `_add_project_scope_args` docstring at `scope.py:513-515`, which calls `--this-repo` identity-based;
      - `docs/transcript-analysis.md:49`, which says `--this-repo` resolves worktrees "by identity" and "as exact project-directory names".

      The `_repo_scoped_project_slugs` docstring at `scope.py:107-113` stays true and gains a cross-reference to the new prefix helper.
    - Changed in meaning: `docs/pr-cost.md:248` and `docs/transcript-analysis.md:1210`, whose "local-corpus activity" now means the run's scope.
    - Still true: `docs/pr-cost.md:69`; `docs/transcript-analysis.md:1516-1521,1523-1525`.
26. `_review_rounds_force_refusal`'s first matching case decides: an empty prior cell allows, an empty new cell refuses, a lower rule refuses, a same-rule shrink refuses, and a higher rule allows. [verified: `pr_cost.py:453-475`; `docs/pr-cost.md:282-287`]
27. [unverified] This plan's reading of guarantee 4: the lower-rule refusal stays ahead of the re-scope allowance, because a lower rule means an older checkout, not a scope change.
28. [engineer-verified: "Read mode repo default"] The repo default and its widening cover the whole `pr-cost` subparser, read mode included.
    - With the selection the engineer typed: [engineer-verified: "Some of these seem overengineered to be honest, check with the architect, adversarial review"]. The adversarial review then recommended keeping it, which is the review's judgment.
    - The design reason is this plan's: read mode's uncaptured listing then names only PRs a bare `--record` would capture.
    - Under a machine-wide read scope, a PR whose only matching activity is another repository's same-named branch would stay listed as uncaptured after every `--record`.
    - Row 38 does not change this: the default changes which PRs the gap listing names and the scope the unbranched-dollars stderr line sums over.
29. [unverified] This plan's choice: the single-account `--pr N` zero-valued row stays, because a PR built without Claude Code is real data. Its `review_rounds_absence` and empty `earliest_activity_at` mark it.
30. `pr_cost.py` is 1,272 lines, and `test_transcript_pr_cost.py` and `test_transcript_pr_cost_ledger.py` are 3,563 and 1,985. All three exceed the limit in `docs/design-decisions/code-file-line-limit.md`, which nothing enforces yet, so new tests go in seam files. [verified: Read of `pr_cost.py`, whose last line is 1272; Grep line counts; `code-file-line-limit.md:19,50-57`]
31. [retired] Superseded by row 51: the absence set has one home, and the parser checks the column by shape.
32. [engineer-verified: "Include removed worktrees (Recommended)"] The repo scope of a repo-scoped `pr-cost --record` also includes this repository's removed-worktree directories. The question named pr-cost's `--record`, not `--this-repo` in other subcommands.
33. [unverified] The session's proposal: move routing-guard's widening into a shared `scope.py` helper used by both callers, and count a scope resolved that way as the repo class. Adopted. The repo class therefore no longer means identity-resolved only, because it admits row 35's prefix matches. Row 48 records why the class stays one value after round 1.
34. routing-guard's `--capture` lists the root once and keeps every name that starts with `<main slug>--claude-worktrees-` and is not that bare prefix, taking `slugs[0]` as the main worktree's slug. A test pins that a removed worktree's directory is read and a sibling fork's directory is not. [verified: `routing_guard.py:84,945-961,1353`; `tests/test_transcript_routing_guard_capture.py:2386-2392`]
35. The prefix reads any directory whose slug starts with it:
    - that includes a foreign repository cloned under this checkout's `.claude/worktrees/` and a path whose lossy slug collides with the prefix [verified: `routing_guard.py:956-958`];
    - `scope.py` names the foreign-clone case as the reason its identity scope avoids path patterns [verified: `scope.py:107-113`];
    - the identity scope already over-includes a slug collision with a live worktree [verified: `docs/transcript-analysis.md:59`];
    - a worktree removed from outside `.claude/worktrees/` stays out [verified: `routing_guard.py:956-958`].
36. [unverified] This plan's choice, settled rather than left open: the widening covers pr-cost's repo scope only, in read mode and under `--record`, bare or with `--this-repo`.
    - `--this-repo` in the other subcommands stays live-worktree only.
    - Row 32's question named pr-cost.
    - Widening everywhere would change the corpus of the `--this-repo` instruments `docs/private-project-redaction.md` lists, and would extend row 35's hole to them.
37. [unverified] The first consult's additions, which Context lists, and the status of each in that order:
    - not built; the engineer deferred it to the reader PR (row 40);
    - built (M1);
    - built (M3, M9);
    - built (row 25);
    - built (M3);
    - adopted (Shipping);
    - not built; the engineer chose the `--projects` help instead (row 41).
38. Read mode prints one dollar figure from its scan: the stderr line of unbranched dollars, which sums over the run's scope. Its ledger listing prints repo, PR, machine, status, join confidence, capture time and round counts, and its gap listing prints a PR number, a branch label and a merge date. [verified: `pr_cost.py:607-658,798-804`]
39. [retired] Superseded by row 40: the scope-downgrade refusal left this plan.
40. [engineer-verified: "Defer to reader PR (Recommended)"] This plan builds no refusal for `pr-cost --record --force --pr N --projects <glob>` over a repo-scoped row; the reader PR decides it.
    - With no refusal, such a re-capture falls through to today's same-rule guard (rows 26, 27).
    - When that guard passes, the append leaves the `repo` row in the history and makes the `glob` row current. [verified: `pr_cost_ledger.py:558-582,601-618`]
    - The review's reasons, which Context records, are not the engineer's words.
41. [engineer-verified: "Cut, use help text (Recommended)"] No stderr line prints on a glob `--record`. The `--projects` help says the rows a glob records carry `capture_scope=glob`.
42. [engineer-verified: "Proceed anyway"] One PR. The question was asked at a projection of about 1,080 lines. Row 49 records the second time it was asked.
43. [engineer-verified: "Shared scope helpers"] The shared `scope.py` helpers stay, with the files they touch that the issue does not name: `period_cost.py` and `routing_guard.py`.
44. [engineer-verified: "Report notice rewording"] The rewording of review-cost-by-size's notice stays, with its test pin and its docs sample.
45. [engineer-verified: "Redaction doc sentence"] The one-sentence correction at `docs/private-project-redaction.md:139-141` stays.
46. [unverified] The adversarial review's recommendations, which Context lists, are applied. The one that changed code: `_project_scope_is_this_repo` and `_capture_scope` are cut, and the default is inlined into `_pr_cost_report`.
47. Guarantee 2 still holds without the predicate helper:
    - `_resolve_project_scope` takes its repo arm exactly when `args.this_repo` is true (row 10) [verified: `scope.py:592`];
    - `_pr_cost_report` sets `capture_scope` from that field after its inline default and before either pass resolves;
    - no code in `pr_cost.py` writes `args.this_repo` today [verified: Grep of `pr_cost.py` for `this_repo`, no match];
    - a test pins that both passes resolved with the field the row's class names.
48. [engineer-verified: "One value, repo (Recommended)"] `capture_scope` keeps one `repo` value for the identity scope and the prefix-widened scope. The label answers the round-2 question quoted in Context. The label covers that sentence only. Dropping the content check, and the Reason, Departure and Cost sub-bullets below, are the session's own text and are not covered by it.
    - Reason: the prefix admits any directory whose slug starts with it, which are the directories under this checkout's own `.claude/worktrees/` and a path whose lossy slug collides (row 35). Contamination needs another repository cloned under that directory, or a colliding path, with a branch named like a merged PR here.
    - Departure: `repo` no longer matches guarantee 2's "Only an identity-resolved repo scope earns the repo class" (row 8). The docs, the CHANGELOG and the PR body say what `repo` covers. Its residuals live in one place, `docs/pr-cost.md`, which the PR body links for the reader PR (Shipping).
    - Cost: a `repo` row cannot be re-labelled later, because the ledger is append-only and its scope cannot be reconstructed once transcripts age out (G1, G2).
49. [engineer-verified: "Proceed anyway"] One PR. The session asked again when the projection reached about 1,350 lines, and the engineer selected the same label. A later review round need not ask again unless the projection grows past about 1,350. This revision projects less (Critical files).
50. `_build_review_rounds_cell` always returns a dict, so `_review_rounds_cell_outcome`'s `not-built` branch is unreachable from this checkout's writer. The code stays in `_ROUNDS_ABSENCES` for a checkout that does not record rounds. [verified: `pr_cost.py:345-354,427-429`]
51. [unverified] This plan's choice, following the session's disposition, for the absence column:
    - the parser checks `review_rounds_absence` by shape, and the writer keeps its closed `_ROUNDS_ABSENCES`;
    - the export reads `pr_cost._ROUNDS_ABSENCES` and replaces any other value with `unrecognized`;
    - a new write-time check then needs no header bump, and the set keeps one home;
    - a test pin tying the set to the header was set aside, because every such change would lock out older checkouts;
    - `_MODEL_BREAKDOWN_KEY_RE` already checks every key of both JSON cells [verified: `pr_cost_ledger.py:176-179,203-213,310-361`];
    - only `cli.py` imports `pr_cost_export`, so the export importing `pr_cost` adds no cycle [verified: Grep of `transcript_analysis/` for importers of `pr_cost_export`].
52. The export date gap and the new column:
    - the parser accepts any `datetime.fromisoformat` form in `merged_at` and `captured_at` [verified: `pr_cost_ledger.py:422-426`];
    - the export's `split("T", 1)[0]` leaves a space-separated value whole, time included [verified: `pr_cost_export.py:31-37`];
    - that `fromisoformat` accepts a space-separated value is [unverified] here: two reviewers ran it and reported so;
    - this plan leaves that pre-existing gap open, because the writer never emits that form and only a hand edit reaches it (row 59, Out of scope). The docs qualify the date-only wording to writer-form values;
    - `earliest_activity_at` does not inherit the gap: its parser check accepts only the writer's `T`-separated form, so the unchanged truncation always reduces it to a date.
53. The payload check's precedent:
    - period-cost classifies a merged-PR entry whose `isCrossRepository` is not a boolean as an unexpected payload, then degrades and exits 0 [verified: `period_cost.py:764-769`];
    - discovery has no per-row result to degrade into, so pr-cost exits 1, as its unparseable-JSON exit does [verified: `gh_cli.py:486-488`];
    - `_gh_discover_merged_prs` returns the decoded payload unchecked [verified: `gh_cli.py:482-491`];
    - a test pins that it returns a dict for a JSON object and None for JSON `null` [unverified: a round-2 reviewer's citation of `tests/test_transcript_gh_cli.py:1012,1018`];
    - so the check also refuses a payload that is not a list of objects;
    - gh returning a requested field is unverified (G4).
54. `--record`'s walk and read mode both reach matches through `_direct_headref_matches`, and `--pr N` looks PRs up by number. [verified: `pr_cost.py:151-157,648,969-974,983`]
55. [unverified] This plan's choice: the empty-scope line keys on the dollar pass's result, not on a directory count from `scope.py`.
    - It fires only when the dollar pass saw no usage-bearing turn on any branch and no valid unbranched turn.
    - A scope holding only unpriced-model or malformed-usage turns on some branch does not fire it, because `turn_count` counts valid-usage turns whether or not the model is priced [verified: `cost.py:125-132`], and a branch with only malformed records still gets a `branch_totals` entry [verified: `cost.py:108-112`].
56. [engineer-verified: "Empty-scope warning"] The empty-scope stderr line stays. Context records the question and its later wording.
57. [engineer-verified: "Bad fork flag fails"] The payload check stays. Context records the question and its round-2 extension to a payload that is not a list of objects.
58. [engineer-verified: "Test helper move"] The record-run helper move stays. Context records the question and the corrected list.
59. [unverified] "Export date fix" was left unticked, so it is cut: an inference from an option the engineer did not pick, under the question's wording in Context. `_pr_cost_export_date_only` keeps today's truncation.
60. [engineer-verified: "Ask the architect to review those choices"] The engineer asked for the second consult, whose dispositions Context lists.
61. [unverified] This plan makes no claim about whether review-cost-by-size can meet the publication bar after the reader PR.
    - `docs/pr-cost.md:455` keeps every ledger-row figure in its "does not meet" list.
    - A stored `capture_scope` label is not by itself the re-runnable refusal that `docs/private-project-redaction.md` § "Publishing a tooling measurement" asks for [verified: `docs/private-project-redaction.md:105-108`].
    - Whether a reader that refuses rows not captured `repo` is such a refusal is for the reader PR and the rule's owner to settle.
    - The reader PR does not change the rule without a fresh question to the engineer, because row 1's selection stands.
62. [unverified] What the repo scope reads is this plan's design (rows 32 to 36, 48), not row 4's label: this checkout's worktrees plus every directory under its main worktree's `.claude/worktrees/` prefix, which is more than this repository's live worktrees.
63. The join grade's call site:
    - `_resolve_branch_pr` is called only at `pr_cost.py:1002`, inside the branch loop that `--pr N` also feeds through `target_branches` [verified: `pr_cost.py:974,980,1002`];
    - for two or more matches it already returns `low`, so a call-site override changes only the one-joinable-match case [verified: `pr_cost.py:189-204`];
    - seven unit calls pass it four positional arguments [verified: Grep of `tests/test_transcript_pr_cost.py` for `_resolve_branch_pr(`, `:412-527`];
    - discovery lists merged PRs only, so a never-merged fork PR never enters the count [verified: `gh_cli.py:483`].

**Mechanisms.**
- M1. Repo default through the `repo_scope_default` and `projects_help` keywords, with the default applied inline in `_pr_cost_report`. anchors: row4, row11, row12, row28, row41, row46. Lighter options set aside: (a) a pr-cost-only scope group, as skill-invocation has, which copies the group a third time; (b) `set_defaults(this_repo=True)`, which keeps the repo arm under an explicit `--projects`.
- M2. `capture_scope` set once per run from `args.this_repo`, with both passes resolving through `_resolve_pr_cost_scope`. anchors: root, row9, row10, row47. Heavier options set aside: a resolver variant that reports each pass's arm, and a shared predicate helper with pr-cost as its only caller (row 46). Lighter option set aside: parsing the label, which carries the raw glob.
- M3. Three columns in one header bump, `capture_scope` last: `capture_scope` a closed enum, `review_rounds_absence` checked by shape, and `earliest_activity_at` accepting only the writer's form. anchors: root, G1, G2, row18, row22, row23, row51, row52.
- M4. Short-row hint keyed on defaults and shortfall. anchors: row21, row22.
- M5. Joinability: the payload check, `_branch_never_joins_a_pr` in scope.py and `_pr_is_joinable` in pr_cost.py, applied at each call site in the walk, the `--pr N` arm and read mode, with one line per skip cause. anchors: row2, row3, row7, row15, row16, row17, row53, row54, row57, row62, row63, G4. Lighter option set aside: the filter inside `_direct_headref_matches`, which would merge the fork skip into the no-match skip (row 54).
- M6. `earliest_activity_at` from `session_starts`. anchors: G2, row19, row20.
- M7. The force table's re-scope case. anchors: row26, row27, row40.
- M8. The wording edits this PR makes necessary. anchors: row1, row6, row25, row61.
- M9. Export triage: `earliest_activity_at` truncated by the unchanged `_pr_cost_export_date_only`, `capture_scope` passed through, and `review_rounds_absence` filtered to the writer's set. anchors: row24, row51, row52, row59.
- M10. The zero-valued `--pr N` row, kept and marked. anchors: row14, row29.
- M11. Removed-worktree widening: `_WORKTREE_SLUG_INFIX` and `_with_removed_worktree_slugs` in scope.py, used by routing-guard and by `_resolve_project_scope(include_removed_worktrees=True)`, which pr-cost reaches only through `_resolve_pr_cost_scope`. anchors: row32, row33, row34, row35, row36, row48, row62. Lighter options set aside: (a) a second copy of routing-guard's rule in pr_cost.py, which gives one rule two homes; (b) widening inside `_iter_scoped_sessions`, which every `--this-repo` caller shares.
- M12. [retired] The scope-downgrade refusal left this plan (row 40).
- M13. [retired] The glob stderr line became the `--projects` help in M1 (row 41).
- M14. The empty-scope line, keyed on the dollar pass's result, and the rename warning naming the scope as a second cause. anchors: row13, row14, row55, row56. Heavier option set aside: a matched-directory count exported from `scope.py`, which cannot name the account.

## Critical files

Paths under `transcript_analysis/` and `tests/` are under `claude/.claude/scripts/`.

**Dispatch 1 (`code-writer`): the scope primitives.** It shares no file with dispatch 2, and dispatch 2 calls what it adds, so it runs first.
- `transcript_analysis/scope.py`: the `repo_scope_default` and `projects_help` keywords, `_branch_never_joins_a_pr`, `_WORKTREE_SLUG_INFIX`, `_with_removed_worktree_slugs`, the `include_removed_worktrees` keyword, and the two docstring edits in Design.
- `transcript_analysis/period_cost.py`: two call sites repointed to the new helper.
- `transcript_analysis/routing_guard.py`: `_capture_scope` and the `:1353` refusal call the moved helper and constant. `tests/test_transcript_routing_guard_capture.py:2386` already pins the behavior and does not change.
- `transcript_analysis/gh_cli.py`: one requested field.
- `tests/test_transcript_scope_scan.py`. Each slug below is built with `scope._path_to_project_slug`, never a hand-typed infix, and the live slugs include one for a worktree outside `.claude/worktrees/`:
  - `_with_removed_worktree_slugs` keeps both live slugs first, adds the slug of `<main>/.claude/worktrees/gone`, and drops the bare prefix, a sibling `<main slug>-fork` name and a duplicate;
  - `_resolve_project_scope(..., include_removed_worktrees=True)`, over two roots resolved one at a time with one repo-arm namespace, as pr-cost's `--all-accounts` does:
    - the first root's scope yields the sessions of both live directories and of the removed one;
    - a removed-worktree directory under the second root only is absent from the first root's scope;
    - `args._this_repo_slugs` still holds the live list after the first resolution;
    - each label's N counts its own root's directories found;
    - without the keyword, the first root yields only the live directories' sessions.
- `tests/test_transcript_scope.py`, beside `TestAddProjectScopeArgsThisRepoHelp`:
  - a caller that omits both keywords keeps `"*"` and no `--projects` help; `repo_scope_default=True` gives a None default; `projects_help` sets the help;
  - `_branch_never_joins_a_pr` is true for `main`, `master`, `develop`, `HEAD` and `worktree-agent-x`, and false for an ordinary branch.
- `tests/test_transcript_gh_cli.py`: `_MERGED_PR_LIST_FIELDS` (`:885`).
- Verification: steps 1 and 2 below.

**Dispatch 2 (`code-writer`): the ledger columns and the writer, after dispatch 1 lands.** It cannot split further, because the new columns break every `--record` test until `_new_pr_cost_row` writes them, and both halves share `_pr_cost_helpers.py`.
- `transcript_analysis/pr_cost_ledger.py`, `transcript_analysis/pr_cost.py`, `transcript_analysis/pr_cost_export.py` and `transcript_analysis/review_cost_by_size.py`, as Design states.
- `tests/_pr_cost_helpers.py`, the new frozen header:
  - `_PRE_CAPTURE_SCOPE_HEADER_LINE = _PRE_REVIEW_ROUNDS_HEADER_LINE + "\treview_rounds"`, appended to `_FROZEN_PR_COST_HEADER_LINES`;
  - a `_pre_capture_scope_row_line` helper;
  - a hand-written row line whose `review_rounds` cell is `_CANONICAL_SAMPLE_REVIEW_ROUNDS_CELL`, and its parsed dict;
  - a `_FROZEN_HEADER_CASES` entry with the append-a-tab hint and the tail `"\t\t\t"`.
- `tests/_pr_cost_helpers.py`, existing helpers:
  - every existing case's parsed dict gains the three empty cells, and its tail gains `"\t\t\t"`;
  - `_sample_pr_cost_row` gains the three keys as `""`;
  - `_merged_pr` gains `"isCrossRepository": False`;
  - `_fake_pr_cost_subprocess_run` returns copies of the PR dicts, never the caller's;
  - its `gh pr list` branch adds `"isCrossRepository": False` to a copy lacking the key when the argv's `--json` list requests it, as its `gh repo view` branch gates on requested fields;
  - a new `withheld_fields: frozenset[str] = frozenset()` parameter drops those fields from every returned PR;
  - `_ScanSpy` also records each resolution's `args.this_repo`; its `kwargs` already carry `include_removed_worktrees`;
  - `_pr_cost_args` annotates `projects: str | None`, and its docstring says, verbatim: "projects defaults to "*", the glob arm; pass None for the parser's bare default.".
- `tests/_pr_cost_helpers.py`, the move: five helpers move here unchanged from `tests/test_transcript_pr_cost.py` (row 58).
  - Moved, with any test-local helper they call: `_NOW` (`:1069`), `_arrange_record_run` (`:1620`), `_ledger_rows` (`:1672`), `_run_record` (`:2326`) and `_cell_of` (`:3115`).
  - New imports they need: `pr_cost`, `subprocess`, `datetime` and `UTC`, and `_write_jsonl` and `_priced` from `.conftest`, as `_review_round_corpus_helpers.py` imports them.
  - Staying in `test_transcript_pr_cost.py`: `_arrange_round_run`, `_write_four_round_corpus`, `_fail_the_pass`, `_write_account_session` and the `_REASON_*` constants.
- `tests/test_transcript_pr_cost.py`:
  - imports the moved helpers from `_pr_cost_helpers.py`;
  - the pass-failure test at `:2718` also asserts that each row's `review_rounds_absence` is `pass-failed`, beside its `review_rounds` check at `:2735`;
  - the capture test at `:2332` also asserts that the row's `review_rounds_absence` is `""`, beside its cell check at `:2340`;
  - `TestReviewRoundsForceRefusal`'s calls, and `TestReviewRoundsAbsenceClosedSet`'s at `:3188` and `:3205`, pass `prior_capture_scope="glob", new_capture_scope="glob"`, so they pin today's same-class behavior unchanged;
  - the `_new_pr_cost_row` calls at `:538` and `:1106` (`_row_from_agg`) pass `capture_scope` and `review_rounds_absence`;
  - `:3167` and `:3197` stay as they are, because `_ROUNDS_ABSENCES` stays;
  - the rename-warning test at `:653` also asserts the new "or its sessions sit outside this run's scope" clause;
  - the mode-level test at `:2104` strips all four trailing tabs of a legacy-shaped current-header row, the realistic editor trim, and expects `append 4 tabs`. Its comment names the four trailing columns;
  - the same hand-edit table (`TestPrCostPrescribedHandEdits`, `:2078`) gains one param: an `earliest_activity_at` that `datetime.fromisoformat` accepts and that carries a time but is not in writer form, such as a space separator, with no `_REJECTED_MARKER`. Read mode, `--record` and export each refuse it and write no export file, and blanking the cell fixes it.
- `tests/test_transcript_pr_cost_ledger.py`:
  - `TestPrCostLedgerFrozenHeaders` covers five frozen lines, and the current header is the newest frozen line plus the three columns in order;
  - the test at `:408` compares each case's parsed `review_rounds` with its parsed dict's, in place of `is None`;
  - the integration test at `:340-360` also asserts the new row's `capture_scope` is `glob`, since its args pass `projects="*"`. With the updated tails, it is guarantee 1's test for every frozen header;
  - the goldens at `:695`, `:727`, `:1544` and `:1958`;
  - the test at `:1918-1926` is renamed for, and its comment repointed at, `capture_scope`, the blank trailing cell its one-tab strip now removes;
  - `TestReviewRoundsColumnRegistration`'s last-column test becomes a pin on the three trailing columns and their empty defaults.
- `tests/test_transcript_pr_cost_ledger_capture_scope.py` (new; seam: the capture-scope columns of `pr_cost_ledger.py`):
  - `capture_scope`: an unknown value refuses. The record tests, the new orchestration and every frozen-header case read back `glob`, `repo` and `""`;
  - `review_rounds_absence`: every `pr_cost._ROUNDS_ABSENCES` value, an unknown shape-valid word and `""` parse, and a shape-invalid value refuses;
  - `earliest_activity_at`: the writer's form and `""` parse, and a space separator, a missing `Z`, a fractional second and a `+00:00` offset each refuse;
  - each refusal is its fixed message with no cell text;
  - hints: a pre-`invalid_usage_records` row two short gets none;
  - hints: a pre-`review_rounds` row two short gets none.

  The one-short hint is covered by the renamed test at `test_transcript_pr_cost_ledger.py:1918-1926`, and four short by the mode-level test at `test_transcript_pr_cost.py:2104`.
- `tests/test_transcript_pr_cost_capture_scope.py` (new; seam: pr-cost's capture scope and join guard). Units:
  - `_review_rounds_force_refusal`, parametrized over the scope pairs that tell case 4 from cases 2, 3 and 5. A refusal is asserted as non-`None` and an allowance as `None`, so no reason constant moves; the existing `TestReviewRoundsForceRefusal` table keeps the reason wording. Each row is built so only one case can fire:
    - new cell empty under `""` to `repo`, refused;
    - a lower rule under `glob` to `repo`, refused;
    - a shrink from `""` to `repo` and from `glob` to `repo`, allowed;
    - a shrink from `repo` to `repo`, `repo` to `glob` and `""` to `glob`, refused;
    - a non-shrinking `glob` re-capture over a `repo` row, allowed (row 40);
  - `_earliest_activity_at` on hand-built `session_starts`: the smaller of two epochs, an epoch ending `.999` truncated down, and `""` for an empty list;
  - `_pr_is_joinable`: false for a `True` flag and for a guarded head, true for `False` with an ordinary head. The payload check makes a missing or non-boolean flag unreachable here, so no case covers one;
  - `_require_well_formed_merged_prs` exits 1 with its verbatim line for each bad input, every bad entry placed after a good one so a first-entry-only check fails:
    - a missing, a `None`, a string and an integer flag;
    - a payload that is not a list (`None` and a dict);
    - an entry that is not a dict;
  - `_require_well_formed_merged_prs` returns for a list of dicts with boolean flags;
  - pr-cost's subparser, through `build_parser()`: `--projects` defaults to None. The help strings are not pinned verbatim; the bootstrap `--help` test checks the `capture_scope=glob` clause.
- The same file, orchestration through `_pr_cost_report` with `_fake_pr_cost_subprocess_run`, and with `scope._repo_scoped_project_slugs` patched to two live slugs, one under `.claude/worktrees/` and one elsewhere. Multi-account and rounds-bearing fixtures are built inline from conftest helpers (`_two_declared_roots`, `_priced`, `_write_jsonl`, `_enable_pr_cost`), as `TestReviewRoundsCrossAccountCapture` (`test_transcript_pr_cost.py:3406`) does:
  - (1) parametrized over a bare run (`projects=None`) and `this_repo=True`, one `--record`:
    - it reads both live directories and a removed worktree's, built as in dispatch 1;
    - it skips a foreign directory holding a session on the same branch name, so the row's dollars and rounds exclude that session;
    - the row records `repo`;
    - its `earliest_activity_at` is the UTC-second form of the earliest turn with valid usage across the in-repo directories, where the foreign session's turn is the earliest of all;
    - both scans resolved with `this_repo` true and `include_removed_worktrees=True` (guarantee 2);
    - the removed-worktree directory stands in for row 35's foreign clone too, because the prefix cannot tell them apart;
  - (2) parametrized over `--projects '*'` and a narrower glob that matches only this repository's directory: the row records `glob`, and both scans resolved with `this_repo` false;
  - (3) a bare `--record` whose `["git", "worktree", "list", "--porcelain"]` call raises `CalledProcessError`, through a wrapper over the fake, exits 1 and leaves a seeded ledger byte-identical;
  - (4) a single-account `--pr N` whose only session sits outside the patched slugs, parametrized:
    - bare: it prints the empty-scope line and no rename warning, and writes a zero-valued row recording `repo`, `no-local-activity` and an empty `earliest_activity_at`;
    - under a `--projects` glob that matches nothing: it prints no empty-scope line and writes a zero-valued row recording `glob`;
  - (5) bare read mode:
    - it lists the repo-scoped PR as uncaptured;
    - it leaves a fork PR out;
    - it lists the same-repository PR of a fork and same-repository pair that share a head;
  - (6) the walk skips `main` with the guarded line and a branch whose only merged PR is cross-repository with the fork line, writing no row for either;
  - (7) the walk, over a same-repository PR sharing a head with a fork PR that has the later `mergedAt` and the higher SHA overlap:
    - it records the same-repository PR;
    - at `low` join confidence, because the unfiltered match count is 2;
    - with no "ambiguous" line;
  - (8) `--pr N`, parametrized over single-account and `--all-accounts`:
    - a fork N and a guarded-head N each exit 1 with their verbatim line and leave the ledger unchanged;
    - a fork N whose head a same-repository PR M shares records no row for M;
    - under `--all-accounts`, with no session in scope, the empty-scope line carries ` in account-K` and prints before the `--pr` refusal;
  - (9) a merged-PR entry with `isCrossRepository` withheld (`withheld_fields`), parametrized over read mode and `--record`, exits 1 with the payload line and writes no ledger;
  - (10) one `--force --pr 1` re-capture under the repo scope appends its row over a seeded row with an empty `capture_scope` that holds more rounds, which proves the call site passes both scopes;
  - (10)'s shape is the refusal test at `test_transcript_pr_cost.py:3310`: `_arrange_record_run` over one plain priced session, and a row seeded through `_sample_pr_cost_row` with a `_cell_of` cell, keeping that helper's own `captured_at`.
- `tests/test_transcript_pr_cost_gh.py`: the real-parser test at `:998` patches `scope._repo_scoped_project_slugs` and asserts the recorded row's `capture_scope` is `repo`. It stays the one parser-to-report composition test.
- `tests/test_transcript_pr_cost_export.py`:
  - the triage test adds `earliest_activity_at` to the date-truncated set, `capture_scope` to the passthrough set, and `review_rounds_absence` to a vocabulary-filtered set beside `review_rounds`;
  - an exported row carries the date of `earliest_activity_at`, `""` for an empty cell and `capture_scope` unchanged;
  - a known `review_rounds_absence` exports unchanged, and an unknown shape-valid one exports as `unrecognized`, its raw text absent from the whole exported file.
- `tests/test_transcript_review_cost_by_size.py`: `_NOTICE_TEXT` (`:37`).
- `docs/transcript-analysis.md:1530` only: the sample's notice line, because that test file compares the sample with the report's output.
- `tests/test_transcript_cli_bootstrap.py`: the `pr-cost --help` test at `:248-252` also asserts the `capture_scope=glob` clause.
- `tests/test_transcript_package_module_prefixes.py`: `TEST_FILES` (`:62`) registers both new test files. Each new file keeps an unaliased absolute `from transcript_analysis import <module>` and at least one `<module>.<name>` read, which that file's other pins need.
- Verification: steps 1 to 3 below.

**Dispatch 3 (`code-writer`): the docs, after dispatch 2 lands.** It shares no line with dispatch 2, Design's Docs section specifies every edit, and the text must describe the behavior dispatch 2 shipped. The verbatim doc sentences, docstrings and comments in Design are drafts: where one disagrees with what dispatch 2 shipped, the code wins, and the docs diff's own review is the check.
- `docs/pr-cost.md`, `docs/transcript-analysis.md` (every edit but `:1530`), `docs/transcript-analysis-architecture.md` (`:443` and `:827-837`), `docs/private-project-redaction.md` and `CHANGELOG.md`, as Design's Docs section states.
- Verification: steps 1 to 3 below.

**Reuse.** `scope._DEFAULT_BRANCH_CANDIDATES` and `scope._names_no_pr_branch`, through the new helper; routing-guard's prefix rule, moved; `scope._list_dir_recording_gaps`; `scope._path_to_project_slug`; `_MODEL_BREAKDOWN_KEY_RE`; `pr_cost._ROUNDS_ABSENCES`; `_repo_scoped_project_slugs`'s existing fail-closed exits; `_pr_cost_export_date_only`; `_CANONICAL_SAMPLE_REVIEW_ROUNDS_CELL`, `_FROZEN_HEADER_CASES`, `_spy_on_scans` and `_fake_pr_cost_subprocess_run`.

**Flagged by exploration, left untouched.** `tests/test_transcript_cli_bootstrap.py:258-264` seeds a pre-`model_breakdown` header, which stays recognized. `tests/test_transcript_ledger_rounds.py:461` builds rows through `_current_line_with_cell`, which follows the live tuple. [verified: Read of both]

Projected diff: about 1,295 lines: about 205 production, 910 test, and 180 docs and changelog. Moving the five helpers accounts for about 150 of the test lines, each moved line counted once removed and once added. Dispatch 1 is about 145 of them, dispatch 2 about 970 and dispatch 3 about 180.

## Verification

Run each step from the worktree root.
1. `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py`, after each dispatch. This is the repo's documented scoped suite. It widens on its own when the diff calls for it, and it collects untracked files, so the two new test files run. Every test Critical files names passes, the parametrized frozen-header cases included. The `pr-cost --help` check is automated in `test_transcript_cli_bootstrap.py`, so no manual run repeats it.
2. `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/`, the documented lint, after each dispatch.
3. Before each dispatch, record the tree's state with `git status --short` and `git diff --stat`. After the dispatch, run both again and compare against that baseline, untracked files included. Confirm the files that changed are all in that dispatch's list in Critical files. For dispatch 2, also read `git diff -U0 docs/transcript-analysis.md` and confirm its one hunk is the `:1530` sample line.

No live `--record` smoke runs, because it needs `gh` auth and writes a permanent ledger row.

## Out of scope

- The reader rule and the publication text (row 6), except the one reason sentence at `docs/private-project-redaction.md:139-141`.
- A structural test pinning pr-cost as the only caller that passes `include_removed_worktrees=True`.
  - The residual: a later caller's opt-in is guarded by no test.
  - The guard is review of that caller's diff, under the P1 clause at `docs/private-project-redaction.md:166-167`.
  - The resolver-level test pins only the keyword's default: without it, a removed-worktree directory is not read.
- The bullet at `docs/private-project-redaction.md:114-120`, which lists `pr-cost --record` among the instruments that refuse.
  - It names only the multi-root exit 2 (`pr_cost.py:716-730`), which refuses more than one account, not more than one repository.
  - An explicit `--projects '*'` run therefore still reads every repository on the account.
  - The reader PR corrects it with the publication text, and this PR's `:139-141` sentence does not touch it.
- Re-scoping legacy rows in bulk. Each needs its own `--force --pr N` inside retention (G2).
- Whether `--force` may replace a `repo` row with a `glob` one (row 40).
  - That is the reader PR's question.
  - A refusal would also block the documented prefix-glob remedy for a session started in a subdirectory of the main checkout (`docs/transcript-analysis.md:60-66`), which the repo scope does not read.
- Widening `--this-repo` in subcommands other than pr-cost (row 36).
- Reaching a worktree removed from outside `.claude/worktrees/` (rows 14, 35). Nothing records such a worktree's path once git forgets it.
- Excluding prefix-admitted directories from the repo scope, by a third class or a content check (row 48).
- A GH-1426 comment on the fork-PR skip (Shipping).
- The export date gap for `merged_at` and `captured_at`: the parser accepts a space-separated timestamp there, and today's truncation exports it whole (row 52). The writer never emits that form, so only a hand edit reaches it. The new `earliest_activity_at` column does not share the gap (row 59).
- An absence reason for `model_breakdown`.
- Read mode printing `capture_scope`. The reader PR's exclusion counts are the intended surface for unscoped rows, and read mode's table already widened once in this release (`CHANGELOG.md:26-28`).
- workstream-cost's merged-branch set, which still includes fork PRs' head names. It has the same join shape, but it is a diagnostic listing with no durable write.
- `--pr N` recording a different PR that shares N's head name (row 17), which predates this change. This PR refuses only a non-joinable N.
- skill-invocation adopting `repo_scope_default`.
- Splitting `pr_cost.py` under the line limit (row 30).
- Recording `cleanupPeriodDays` in the row (row 20).
