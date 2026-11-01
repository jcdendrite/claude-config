# Review cost by PR size

## Context

Goal: add a re-runnable `transcript-analysis.py` subcommand that reports how code-review round count and round cost vary with merged-PR size, so GH-1364's diff-size threshold can cite a measurement instead of an ad hoc script.

Ask: "in terms of plan files, I think planning the subcommand separately makes sense to keep the PR small." The master plan is issue #1364 (piece 1 of 4). An earlier publication intent ("I want to authorize publishing cross-account figures for this one") is superseded for this command: after review round 2, the engineer selected "Private view only (Recommended)" for `--all-accounts`, so only default-mode output is ever published.

Why now: an exploratory run over a wider corpus found the review cost rising with PR size. It used throwaway scripts that joined `review-round-cost --this-repo` output to `gh pr list` sizes by branch name. Its figures stay private under `docs/private-project-redaction.md` § "A wider corpus goes to the owner, never into a public artifact", except the one the owner authorized in issue #1364, and piece 4's threshold has no citable source. Piece 4 cites this command's default-mode output only.

Lessons from that run that the design must handle:
- Join to PRs by PR number, not by branch-name text matching on rendered output.
- When one PR's sessions span more than one transcript root or machine, sum their rounds; don't keep only the largest copy.
- Project directories can be symlinks into a directory outside every declared root.
- Truncating the long per-branch report (e.g. `| head`) silently hides most branches, so aggregates should come from the tool itself, not a hand pipeline.

Intended outcome: one command whose output carries per-PR rows and the aggregates (round count, mean cost per round, and total code-review cost against lines; rank correlations; log-log size elasticities; size-bucket medians; within-branch round-index trend), scoped so a figure from it meets the publication rules.

## Approach

Add a `review-cost-by-size` subcommand in a new `transcript_analysis/review_cost_by_size.py`. It runs `review-round-cost`'s own round detection and pricing for `code-review` rounds, joins each branch to its merged PR by exact head-branch name, and prints the aggregates, then join diagnostics, then per-PR rows. It requires `--this-repo` in both modes. By default it reads only the active account, so its figures meet the single-account publication bar, and every published figure comes from this mode. `--all-accounts` reads every declared root as one merged scan and is a private view for the owner. It prints DO NOT PUBLISH, and its output is never proposed for publication (row 42), so it carries no publication rules of its own.

**The symlink abort.** This PR does not change `scope._root_index_for_path`. Both modes sum rounds by PR and need no per-root attribution, so they pass `resolved_roots=None` and never reach that function (M3). The session's hypothesis holds up as the right follow-up fix. Every multi-root iterator yields a transcript under the root it listed it from (row 8). The redact map already keys projects by that same root (row 10). Attributing by the listing root instead of the resolved path would therefore remove the abort for all nine callers (row 9). It stays out of this PR because it changes shared, documented, test-pinned behavior this PR does not need (Out of scope).

Alternatives weighed:
- **A `--by-pr-size` flag on `review-round-cost`.** Rejected: `review_rounds.py` imports no other command-group module (row 24) and makes no `gh` call (`review_rounds.py:38`), and this needs `gh_cli`. Its pooled and multi-root redaction branches would also multiply the flag's cases.
- **Joining rendered `review-round-cost` output to `gh pr list`**, the exploratory method. Rejected: it text-matches branch names on rendered rows, the failure Context names. This command joins in-process on raw branch names.
- **Taking sizes from the pr-cost ledger.** Rejected: the ledger has no round columns yet and holds only captured PRs, while one `gh pr list` call sizes every merged PR (row 11).
- **Dropping rounds opened after the PR's `mergedAt`.** Rejected: pr-cost counts a branch's post-merge activity as the PR's (row 14), and matching that keeps both commands' per-PR grain the same.
- **Omitting per-PR rows under `--all-accounts`.** Set aside; see M4.
- **Making `--all-accounts` output proposable**, through an omission list, a small-bucket floor and a test pinning that list to the printed lines. Set aside by the engineer's choice of a private view (row 42). Each review round had found another line that composes against a public single-account figure (rows 17-18).

### Assumption ledger

**Root:** No re-runnable command reports how `code-review` round count and cost vary with merged-PR size, so piece 4's threshold has nothing citable; the exploratory figures read a wider corpus and stay private, except the one the owner authorized in issue #1364. A replacement must join rounds to PRs without text-matching rendered output. It must sum a PR's rounds across transcript roots and survive project directories symlinked outside every root. It must compute its aggregates itself, scoped so a figure from it meets the publication rules.

**Givens:**
- G1. Transcript retention decides which PRs still have sessions to measure. Reason: Claude Code deletes transcripts older than each user's `cleanupPeriodDays` setting (default 30 days, `docs/pr-cost.md:3`), and that setting is each stow user's own choice, not this command's.
- G2. A merged PR's size and head branch come from GitHub's PR metadata (`additions`, `deletions`, `headRefName`). Reason: GitHub owns that data, and `gh` is this toolkit's only source for it (`gh_cli.py:471-490`).
- G3. The repo's Python floor is 3.11. Reason: `install.sh:83` enforces it and `README.md:111` states it; raising it is a repo-wide decision outside this plan.
- G4. `docs/private-project-redaction.md` § "Publishing a tooling measurement" governs every figure this command prints. Reason: the repository owner sets that policy, and this plan conforms to it rather than changing it.

**Mechanisms:**
- **M1. Module, registration and test seams.** `transcript_analysis/review_cost_by_size.py` holds `cmd_review_cost_by_size` and `_add_review_cost_by_size_subparser(sub)`. The helper registers `--projects`/`--this-repo` through `scope._add_project_scope_args` and adds `--all-accounts`. The shim imports the helper and calls it right after `review-round-cost`'s registration block (`transcript-analysis.py:704-738`). The module imports `gh_cli`, `render`, `review_rounds` and `scope` by module, as the guard tests require. Three functions carry the logic, so most tests feed them literals instead of transcripts and a `gh` fake:
  - `_scan_code_review_rounds(args, roots, scan_gaps)` runs M3's scan. It returns the filtered round dicts and the scope label `_resolve_project_scope` returned beside its iterator, which M5's header prints.
  - `_join_rounds_to_prs(rounds, merged_prs)` applies M7-M8 and returns per-PR records plus the join counts.
  - `_compute_aggregates(records)` applies M9 and returns numbers, with `None` wherever a value is undefined.

  The cmd keeps the refusals, the root choice, the `gh` calls and the rendering. Testing every rule through the cmd alone was set aside: each stats case would need a transcript per round, a `gh` fake with explicit sizes, and parsing of rendered floats.

  anchors: row1, row23, row24, row25
- **M2. Single-account default.** In both modes, exit 2 before any `gh` call or scan unless `--this-repo` is set and `--projects` is unset or `*`, mirroring `cost-counts` (`subagent_mix.py:505-513`). Without `--all-accounts`, the roots are `[scope.config_dir() / "projects"]` alone, so declared roots contribute nothing. In the default mode, the cmd catches a `ValueError` from its own `scope.config_dir()` call and exits 2 under this subcommand's name, as `_resolve_cost_roots` does (row 35). Under `--all-accounts`, root resolution reaches `config_dir()` only through `_projects_dir()`, which already exits 2 under its own `_projects_dir:` prefix (row 45), so the cmd adds no handler there. `main()`'s shared top-level `--config-dir` refusal clause for members with no `--config-dir` of their own must hold in both modes, so it says only that the subcommand resolves its scan roots itself. This narrowing plus the scope-flag refusals is what row 3's "refuse" means (row 31). `cost --summary`'s in-function `len(roots) > 1` guard (`cost.py:576-585`) is not copied. It protects `_cost_report` from roots its callers build, while this cmd builds its one-element list itself, so the guard could never fire.

  Add `"review-cost-by-size"` to `scope._SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR`, so `main()` refuses a top-level `--config-dir` in both modes. Without `--all-accounts`, `config_dir()` would silently ignore the flag. With it, `resolve_scan_roots` would silently narrow the scan to that one directory (row 15). The comment above the tuple becomes the one place naming the members with no `--config-dir` flag of their own:
  - `cost-counts` and `period-cost` build their own single root.
  - This command builds its own root by default and calls `resolve_scan_roots` under `--all-accounts`.

  Three sites point to that comment instead of restating "the two exceptions": `_resolve_cost_roots`'s docstring (`scope.py:743-745`), `main()`'s comments (`transcript-analysis.py:979-986`), and the docstring of `test_transcript_cost_counts.py`'s refusal-message test (`:865-868`). That test's parametrization also gains this command (row 36). The tuple comment drops its pointer back to the `_resolve_cost_roots` docstring.

  anchors: row3, row15, row16, row31, row35, row36, row45
- **M3. One merged scan, never root-attributed.** Under `--all-accounts` the roots come from `scope.resolve_scan_roots(args)`. In both modes, `_scan_code_review_rounds` builds one `scope._resolve_project_scope(args, "review-cost-by-size", roots=roots, scan_gaps=scan_gaps)` iterator over every root. It keeps the default `include_subagents=False`, as `compute_review_round_costs` requires (row 7). With `True`, each session's subagent records would be priced a second time, into that session's last window (row 48). `cost-counts` passes `True` (`subagent_mix.py:527-528`) and is not the model here. It then calls `review_rounds.compute_review_round_costs(session_iter, skill_filter={_SIZED_REVIEW_SKILL}, resolved_roots=None)`. Every round keys `(None, branch)`, so one branch's rounds from every root merge before the PR join, and `_root_index_for_path` is never called. The iterator's shared dedup still reads a project directory aliased across roots once. The `scan_gaps` counter turns an unreadable root, project directory or session file into a count instead of a crash or a silent drop (row 34). Alternatives checked:
  - A per-root loop over `roots=[root]`, the shape `pr-cost --all-accounts` uses (`pr_cost.py:525-533`). It also avoids the abort, but it gives up the shared `visited_dirs` set, so a project directory aliased across two roots counts twice (row 8).
  - Making `_root_index_for_path` attribute by the listing root. This is not lighter: it changes a shared function with nine callers and rewrites a test-pinned, documented behavior (row 9), all for attribution this command discards.
  - Refusing or skipping a session whose project directory resolves outside every root. Refusing makes the mode unusable wherever such a directory exists (row 6). Skipping silently drops every session read through such a directory.

  anchors: row4, row6, row7, row8, row9, row34, row48
- **M4. `--all-accounts` is a private view.** Its output is never proposed for publication (row 42), so it needs no omission rule, small-bucket floor or docs-list test (row 43). It prints a module-owned DO NOT PUBLISH line to stdout and stderr that cites `docs/private-project-redaction.md` § "A wider corpus goes to the owner, never into a public artifact". `scope._DO_NOT_PUBLISH_BANNER` is not reused because its stated reason, real project names and session IDs, is false for this output.
  - Every line prints as in the default mode, per-PR rows included. The rows were to be omitted only because a cross-account row minus its public single-account twin isolates the other accounts' share of that PR (rows 17-18), which matters for publication alone. Shown privately, they give the owner the PRs behind each aggregate, and the renderer loses a mode branch.
  - stderr keeps the `scanning root K/N...` lines (row 19) and every other multi-root message unchanged.

  anchors: row4, row17, row18, row19, row42, row43
- **M5. Scope header.** Both modes print a local `REVIEW COST BY SIZE SOURCES (<scope label>; active account only)` line, or `...; every declared account)` under `--all-accounts`, instead of calling `scope.print_resolved_scope`. The label comes from `_scan_code_review_rounds` (M1). The header names no repository, so a run from another checkout cannot print a private repo name (row 49). In the default mode, `print_resolved_scope` would print "declared but contributed no additional root" when the declared-roots file does contribute roots that this mode deliberately ignores (`scope.py:650-657`). Under `--all-accounts`, the same local line keeps both modes on one header shape. Neither line states a root count, unlike `print_resolved_scope` (row 19), so the docs list this command beside `cost --summary`.

  anchors: row15, row19
- **M6. `gh` calls.** Run workstream-cost's sequence (`workstream_cost.py:77-90`) after root resolution and before the corpus scan, so a `gh` failure costs no scan:
  1. `_git_remote_origin_host_and_owner_repo(subcommand="review-cost-by-size")`, so its failure messages name this command (row 11).
  2. `_gh_auth_preflight_ok`, exiting 1 with this subcommand's own message on failure.
  3. `_resolve_pinned_gh_repo`, with the ordinal taken from `scope._redaction_ordinals(roots)`.
  4. `_gh_discover_merged_prs`.

  The diagnostics' merged-PR line notes when the list's length is at least `gh_cli._PR_COST_GH_PR_LIST_LIMIT`, the comparison period-cost makes (row 12). The note warns of both missed and wrong joins: older merged PRs may count as unmatched branches, and a branch name reused by a PR beyond the limit can join the wrong PR. That wrong join is possible because the ambiguity check sees only the listed PRs. It goes to stdout, so the cited report carries it.

  anchors: row11, row12
- **M7. The PR join.** Index the merged list by `headRefName` once, and group code-review rounds by their raw branch name (`branch_key[1]`). A branch in `scope._DEFAULT_BRANCH_CANDIDATES`, or one `scope._names_no_pr_branch` accepts, never joins, as in period-cost's `_pr_for_branch` (row 33). That keeps a merged fork PR opened from a fork's `main` off the local `main` rounds. Every other branch has one of three outcomes:
  - **One match:** that PR's number keys the record, and lines = `additions + deletions`.
  - **No match:** the branch counts as unmatched. This covers unmerged branches and PRs past the list limit. Never-joining branches count here too.
  - **More than one match:** the branch counts as ambiguous and is excluded, because resolving it needs `_resolve_branch_pr`'s per-PR enrichment (row 13).

  A merged PR with no code-review round in scope is not a record, since its sessions may have aged out (G1), run on another machine (row 2), or sat in a since-removed worktree (row 44). The join reads every entry's `headRefName`, and the matched entry's `number`, `additions` and `deletions`, by direct indexing, never `.get(..., 0)`. A malformed `gh` entry therefore stops the run instead of entering the smallest bucket (Out of scope). Alternatives checked:
  - `pr_cost._direct_headref_matches`. It is a one-line comprehension, and importing `pr_cost` would make this the first command module to depend on that command group and its ledger imports (row 32). The equality test is a small duplicated value, and workstream-cost already inlines it.
  - period-cost's `_pr_for_branch`. It takes period-cost's own validated PR type, which carries `isCrossRepository`, and it resolves a reused branch name to the highest-numbered PR, where this command excludes the branch.

  anchors: root, row2, row7, row13, row14, row32, row33, row44
- **M8. What a record measures.** `_SIZED_REVIEW_SKILL = "code-review"` is a module constant (row 26). A record holds a PR's number, its lines, and its per-round dollars (`main_dollars + agent_dollars`) in `sort_key` order. Three values come from it:
  - its round count;
  - its total dollars;
  - its mean dollars per round, which is total over count.

  The join counts also sum `unpriced_turns` and `dangling` over the joined rounds only, since both make dollars understate. No test asserts the constant's `REVIEW_SKILLS` membership: the scan test's `/code-review` round stops counting if that name leaves the registry.

  anchors: root, row7, row26
- **M9. Aggregates, stdlib only.** `_compute_aggregates` returns `None` for any value it cannot compute, and rendering prints `None` as `n/a`.
  - Spearman's rho of lines against each of the three values, printed beside the PR count it used (row 54). A small module helper assigns average ranks, giving tied values the mean of their positions, and `statistics.correlation` runs on those ranks. Spearman ranks every record, so its count is `len(records)` for all three values and can exceed the elasticity count. `aggregates["spearman"]` becomes `measure -> (rho, count)`, the shape `elasticity` already uses, and each line prints `rho (n=N)`, or `n/a (n=N)` when rho is `None`. The block's header reads `Spearman rho of lines against (rho; PRs used):`, naming its count the way the elasticity header does.
  - Log-log elasticity of each value against lines: the slope of `statistics.linear_regression` on natural logs, over PRs with lines > 0 and that value > 0. Each value prints its own PR count, since a zero-dollar PR drops out of the two dollar values only. Equal sizes and a constant value are both decided on the pre-log inputs, among the PRs counted for that value (row 40). Equal sizes give `n/a`, and that check runs first. Otherwise a constant value gives exactly 0.0, which prints as is.
  - Half-open size buckets with lower edges 0, 150, 400 and 800 lines, the bucket bounds issue #1364 names (row 53). Each bucket prints its PR count and the `statistics.median` of the three values. An empty bucket prints 0 and `n/a`.
  - The within-PR trend, over PRs with at least 4 code-review rounds: Spearman's rho of round index against round dollars. A PR whose rho raises, such as one whose rounds all cost the same, is skipped. The line prints how many PRs yielded a rho, their median rho and the share above zero, and it reads `n/a` when none did.
  - A `statistics.StatisticsError`, from too few points or a constant series, yields `None` for that one value. An empty set yields `None` before any division.

  The edges and the 4 are named module constants. The edges are the bucket bounds named in issue #1364's published figure. They are a grid, not a result. Neither this plan nor the code restates a measured result from that figure, such as a median or a count. Context records only its direction (row 50). The 4 is the smallest round count whose rank correlation can take more than a few values. The lighter primitive, `statistics.correlation(..., method="ranked")`, needs Python 3.12 (rows 20-21).

  anchors: root, row20, row21, row22, row40, row53, row54
- **M10. Output order.** The output prints in this order:
  1. The DO NOT PUBLISH line, under `--all-accounts` only.
  2. The header.
  3. The aggregates.
  4. The join diagnostics: merged PRs listed, PRs joined, unmatched and ambiguous branches, unpriced turns, dangling dispatches, and unreadable sources skipped. They are counts only, never a branch name.
  5. Per-PR rows, in both modes (M4), sorted by lines and then PR number. Each row prints the PR number, lines, round count, total dollars and mean dollars.

  A truncated read therefore still shows every aggregate. An empty scope needs no special line: it prints the full report with zero counts and `n/a` aggregates, and returns normally. Per-PR rows stay on in the default mode, as Context's intended outcome states. Run from this repository's checkout, their PR numbers and sizes are already public on GitHub. Run from any other checkout, the whole report falls outside the single-account bar, rows or not (row 41), and the docs say so.

  anchors: root, row16, row41
- **M11. Docs.** Four docs change: `docs/transcript-analysis.md` (a new section, the `gh` list, the corpus-scope exceptions, the attribution consumer list), `docs/transcript-analysis-architecture.md` (a module section and the lists it joins), `docs/private-project-redaction.md` (one publication-instrument bullet for the default mode), and `CHANGELOG.md`. `compute_review_round_costs`' docstring gains one sentence for its new multi-root caller. Details are under Critical files.

  anchors: row16, row23, row25, row27, row42, row44, row46, row47

**Rows:**
1. [engineer-verified: "in terms of plan files, I think planning the subcommand separately makes sense to keep the PR small."] This plan covers piece 1 of #1364 alone, and the PR stays small.
2. [engineer-verified: "One machine per run (Recommended)"] A run reads one machine's transcripts. This PR does not combine machines.
3. [engineer-verified: "Refuse multi-account (Recommended)"] The default run refuses a corpus wider than one account.
4. [engineer-verified: "Yeah, we can include it."] Include the opt-in `--all-accounts` mode the session proposed. It reads every declared root, prints DO NOT PUBLISH and sums rounds by PR, and its figures publish only through the owner's per-figure authorization. These details are the session's proposal, and the quote accepted it. Row 42 supersedes the publication clause: `--all-accounts` figures are never proposed for publication.
5. [engineer-verified: "I want to authorize publishing cross-account figures for this one"] The owner means to propose `--all-accounts` figures for publication, one authorization at a time. Row 42 supersedes this row's effect: its publication intent no longer applies to this command.
6. [verified: `test_transcript_cost.py:1220-1241`, `scope.py:261-273`] A project directory can be a symlink that resolves outside every declared root; the repo's own test fixture pins that trigger. M3 holds either way.
7. [verified: `review_rounds.py:330-468`, `:1364`, `:1385`] `compute_review_round_costs` is callable directly. It needs a main-thread-only iterator (`:339-345`) and applies `skill_filter` after window detection (`:371-377`, `:464`). It calls `scope._root_index_for_path` only when `resolved_roots` has more than one entry (`:386`, `:392`); otherwise every round keys `(None, branch)` (`:400`). A round's dollars are `main_dollars + agent_dollars`. Each round dict also carries `skill`, `ts`, `unpriced_turns`, `dangling` and `sort_key` (`:379-384`, `:449-452`). `branch_totals` covers every branch with priced activity, round or not (`:346-350`).
8. [verified: `scope.py:234-273`, `:339-371`, `:374-431`] Across several roots, `_iter_scoped_sessions` shares one resolved-path `visited_dirs` set, so a project directory aliased across roots is read once. It yields each transcript under the root it listed it from (`root.iterdir()` at `:418`, then `_list_dir_recording_gaps(project_dir, ...)` at `:359`). It still yields a project-directory symlink that resolves outside every root (`:261-273`).
9. [verified: `scope.py:857-891`; grep of `_root_index_for_path(`] `_root_index_for_path` resolves the transcript path and raises `AssertionError` when no resolved root contains it. Nine multi-root callers reach it: `cost.py:806`, `read_scope.py:449`, `cache_efficiency.py:252`, `edit_format.py:327`, `cache_rebuild.py:250`, `subagent_mix.py:175`, `subagents.py:108`, `audit_routing.py:121` and `review_rounds.py:392`. `docs/transcript-analysis.md:49` documents the abort, and the symlink case is the trigger fixture of `test_transcript_cost.py:1220-1241`.
10. [verified: `redaction.py:153-165`] `_build_redact_map` keys each project label by the root it was listed under, never by its resolved location.
11. [verified: `gh_cli.py:199`, `:207-208`, `:358`, `:362-432`, `:471-490`; `workstream_cost.py:77-90`] workstream-cost reuses the preflight, repo-pin and merged-PR discovery sequence as-is. The pin and discovery helpers' abort messages name `pr-cost` (`:414`, `:425`, `:486`). `_git_remote_origin_host_and_owner_repo` takes a `subcommand` that prefixes its own messages.
12. [verified: `gh_cli.py:55`, `:447-469`; `period_cost.py:738-739`, `:1713`] Discovery passes `--limit 1000` and warns on nothing when the list reaches it. period-cost warns by comparing the listed count to `_PR_COST_GH_PR_LIST_LIMIT`.
13. [verified: `pr_cost.py:139-145`, `:163-192`] `_direct_headref_matches` is pr-cost's exact `headRefName` join. Resolving a reused branch name takes per-PR `gh pr view` enrichment.
14. [verified: `pr_cost.py:26-32`] pr-cost treats a branch's activity after merge as the PR's, and its as-of window waits for that activity.
15. [verified: `subagent_mix.py:505-525`; `cost.py:554-585`; `scope.py:493-495`, `:694-707`, `:743-745`, `:757-758`; `transcript-analysis.py:974-1007`; `test_transcript_analysis.py:249-259`] The single-account precedents work as follows:
    - `cost-counts` and `cost --summary` require `--this-repo`, refuse a non-default `--projects`, and read `config_dir()/projects` alone.
    - `main()` reassigns only `scope.PROJECTS_DIR` for a top-level `--config-dir`, so `config_dir()` never sees it.
    - `resolve_scan_roots` returns only the one directory a top-level `--config-dir` names.
    - `cost-counts` and `period-cost` are tuple members with no `--config-dir` of their own.
    - Four comments enumerate those two.
16. [verified: `docs/private-project-redaction.md:110-119`, `:156-161`] A figure is publishable when a command that refuses a wider corpus computes it over this repository on one account, and the figure is cited beside that command. Within that scope nothing is withheld.
17. [verified: `docs/private-project-redaction.md:186-204`, `:219-231`, `:242-261`] A wider-corpus figure publishes only under the owner's in-session yes, one figure at a time. A report approved as a whole must name in its citation every category it withheld. A per-account dimension and an account or root count stay barred even under authorization.
18. [verified: `docs/private-project-redaction.md:138-149`] With the opt-in sentinel on, every merged PR's body embeds that PR's single-account `cost-counts` round counts.
19. [verified: `scope.py:409-414`, `:650-657`, `:676`; `review_rounds.py:945-952`] `print_resolved_scope`'s header always states the root count, and `review-round-cost --pooled` builds a local header to avoid it. `_iter_scoped_sessions` prints `scanning root K/N...` to stderr whenever there is more than one root.
20. [verified: `install.sh:83`; `README.md:111`; `pyproject.toml:3`; `.github/workflows/tests.yml:103`] The floor is Python 3.11, while CI and ruff target 3.12.
21. [verified: https://docs.python.org/3/library/statistics.html] `statistics.correlation` and `statistics.linear_regression` were "Added in version 3.10." `correlation`'s Spearman option arrived later: "Changed in version 3.12: Added support for Spearman's rank correlation coefficient." `correlation` raises `StatisticsError` on fewer than two points or a constant input. The docs say `linear_regression` raises it on fewer than two points or a constant x, though a constant x after `math.log` can return 0.0 instead (row 40); the doc says nothing about a constant y.
22. [verified: grep of `spearman|_average_rank|linear_regression|statistics\.correlation` over `*.py`] No rank, correlation or regression helper exists in the repo.
23. [verified: `transcript-analysis.py:210-214`, `:319-322`, `:354`, `:444-485`, `:961-966`; `docs/transcript-analysis-architecture.md:51-56`] 20 subcommands register through their module's `_add_<command>_subparser(sub)`, the path the architecture doc names. `review-round-cost` still registers inline (`:704-738`).
24. [verified: `docs/transcript-analysis-architecture.md:25-31`] `review_rounds.py` imports no other command-group module.
25. [verified: `test_transcript_analysis_architecture_doc.py:35-49`; `test_transcript_package_module_prefixes.py:27-117`, `:498-509`; `test_transcript_analysis.py:245-283`, `:2018-2043`] Four guards apply:
    - Every package module needs a `### <module>.py` architecture section.
    - A new production module and a loader-free test file must join `PRODUCTION_MODULES` and `TEST_FILES`.
    - The top-level `--config-dir` refusal test parametrizes off the tuple.
    - The `--projects`/`--this-repo` exclusivity test walks the real registrar.
26. [verified: `review_rounds.py:41`; `author_outcome.py:76`] `"code-review"` has no shared named constant. `REVIEW_SKILLS` is the registry, and `author_outcome.py` defines its own `_CODE_REVIEW_SKILL`.
27. [engineer-verified: "Keep redaction-doc bullet"] The `docs/private-project-redaction.md` bullet (M11) stays in this PR.
28. [engineer-verified: "Keep comment consolidation"] M2's rewrite of the tuple comment, the `_resolve_cost_roots` docstring and `main()`'s comments stays in this PR.
29. [engineer-verified: "Keep counts, check at ask"] Under `--all-accounts`, the command prints its count lines and stderr as planned, and composition is checked when a figure is proposed. Row 42 supersedes its publication effect. The count lines and stderr still print, but this row's publication intent no longer applies to this command, since no figure from it is proposed.
30. [unverified] The label in row 29 accepted the session's own option description: "Print everything as planned. The docs require each publication proposal to name the subtraction risk and leave out the count lines. Nothing in the command enforces this." The rule's "and all stderr" is the session's later addition, not the engineer's. Row 42 supersedes this row with row 29.
31. [unverified] Row 3's label accepted the session's own option description: "My proposal: like `cost --summary` and `cost-counts`, the command scans only the active account and refuses any wider scope. Every figure it prints, dollars included, is then publishable under the single-account rule, with no per-figure ask." Read with that description, "refuse" means the `cost-counts` narrowing plus the scope-flag refusals. The default run reads the active account's root alone and exits 2 on a wider scope flag; it does not exit because declared roots exist.
32. [verified: `pr_cost.py:23-24`, `:139-145`; grep of `pr_cost` imports over `claude/.claude/scripts/`; `workstream_cost.py:88-90`] `_direct_headref_matches` is a one-line comprehension. `pr_cost` imports `cost`, `pr_cost_ledger`, `ledger_common` and `_config`, and no other command module imports `pr_cost`. workstream-cost builds its own `headRefName` set from the same merged list.
33. [verified: `period_cost.py:781-787`; `scope.py:929-930`, `:995-998`; `gh_cli.py:482`] period-cost's `_pr_for_branch` never joins a default-branch candidate (`main`, `master`, `develop`), a detached head or a `worktree-agent-*` branch, and it skips cross-repository PRs. `_gh_discover_merged_prs` requests no `isCrossRepository` field.
34. [verified: `scope.py:252-269`, `:299-305`, `:418-427`, `:584-588`, `:608`] `_resolve_project_scope`'s `--this-repo` branch passes a `scan_gaps` counter through. With one, an unreadable root, project directory or session file is counted instead of raising or vanishing. Its keys are level tags, never a path or an ordinal. Under several roots, a root-level skip still prints `cannot scan account-K` to stderr.
35. [verified: `scope.py:747-755`; `subagent_mix.py:525`] `_resolve_cost_roots` turns a `ValueError` from `config_dir()` into exit 2; `cmd_cost_counts` does not catch it.
36. [verified: `test_transcript_cost_counts.py:863-879`; `gh_cli.py:1-2`] Two more sites name the members or consumers this PR extends. The cost-counts refusal-message test parametrizes over `cost-counts` and `period-cost`, and its docstring says those two alone lack a `--config-dir` flag. `gh_cli.py`'s module docstring lists four consumers.
37. [verified: `test_transcript_review_rounds.py:36`, `:3342-3361`] The pooled pointers' cited heading is pinned against `docs/private-project-redaction.md` through `heading_texts` and `normalize_heading` over a module-level doc path.
38. [verified: `test_transcript_review_rounds.py:458-472`, `:1211-1216`, `:1299-1300`] `review_rounds`' own tests pin `sort_key` ordering across sessions and a review-orchestrator dispatch opening a `code-review` round.
39. [verified: `_pr_cost_helpers.py:149-153`; `conftest.py:1254-1273`, `:1458-1471`; `test_transcript_review_rounds.py:78-91`] `_merged_pr` fixes `additions` and `deletions` at 1. `conftest._two_declared_roots` points both `scope.PROJECTS_DIR` and `CLAUDE_CONFIG_DIR` at one active root, and `test_transcript_review_rounds.py` carries a private copy of it. `fake_projects` patches the `config_dir` bindings directly, so combined with `_two_declared_roots` the two modes would see different active roots.
40. [verified on Python 3.15.0 with `python3 -I -c "import statistics, math; print(...)"`; the literals are synthetic inputs a reviewer's random search found, not sizes from any run] `statistics.linear_regression([math.log(x) for x in (1847,147,3255)], [math.log(5.901146528953873)]*3)` printed `slope=-7.250093809777642e-32`. `statistics.linear_regression([math.log(7)]*5, [1.0,2.0,3.0,4.0,5.0])` printed `slope=0.0` and did not raise. On `math.log` inputs, `statistics.linear_regression` can return a tiny nonzero slope when every y is equal. It can also return 0.0 without raising when every x is equal, though the docs say it raises on a constant x (row 21). So `_log_log_slope` decides both cases itself, on the pre-log inputs: equal sizes give `None`, and otherwise a constant value gives exactly 0.0. The aggregates tests pin both. Python 3.11 was not run.
41. [verified: `scope.py:125-128`, `:155-165`; `gh_cli.py:212-216`, `:423`] `--this-repo` takes its slugs from `git worktree list --porcelain`, run with no `cwd=` and so in the working directory, and exits 1 when the working directory sits outside every listed worktree. The `gh` pin reads `git remote get-url origin` the same way and checks only that `gh`'s repo matches it.
42. [engineer-verified: "Private view only (Recommended)"] `--all-accounts` is a private view, never proposed for publication. This supersedes the publication effect of rows 4, 5, 29 and 30.
43. [unverified] The label in row 42 accepted the session's own option description: "--all-accounts is never proposed for publication and keeps DO NOT PUBLISH. Its outputs need no omission rules, small-bucket floor or docs-list test. Published figures come only from the single-account default mode."
44. [verified: `scope.py:125-128`, `:142-145`; `docs/transcript-analysis.md:65`] `--this-repo` matches project directories against the worktrees `git worktree list --porcelain` lists at run time, main checkout included. A removed worktree's project directory stays on disk and is silently excluded.
45. [verified: `scope.py:71-82`, `:493-497`] Under `--all-accounts`, `resolve_scan_roots` reaches `config_dir()` only through `_projects_dir()`, and only while `PROJECTS_DIR` is unset. `_projects_dir()` catches the `ValueError` itself and exits 2 with a `_projects_dir:` prefix. A test that patches `PROJECTS_DIR` never reaches `config_dir()` on that path.
46. [verified: `docs/transcript-analysis.md:3`, `:76`, `:713-718`; `review_rounds.py:394`] The intro at `:3` names only `cost --summary` and `period-cost` as active-account-only, while `:76` also names `cost-counts` and `user-input`. The pre-branch attribution block's consumer list calls itself "the only list". Its main-thread-only and positional bullets name `review-round-cost`, whose `compute_review_round_costs` attributes branches through `_session_record_branches` (`:394`).
47. [verified: `review_rounds.py:362-369`; `docs/transcript-analysis-architecture.md:272-295`] `compute_review_round_costs`' docstring says a caller passes `resolved_roots=None` "under a single root". The `### review_rounds.py` section mentions the function but lists it neither among its public names nor with a caller outside the module.
48. [verified: `corpus.py:141-170`; `render.py:35-46`; `review_rounds.py:198-213`, `:409-429`] With `include_subagents=True`, a session's subagent records are appended after its main records. A round window ends at the next main-thread fresh user prompt or round opener, else at the list's end, and every assistant record inside a window is priced into `main_dollars`. Appended subagent records therefore land in the session's last window only.
49. [engineer-verified: "Drop repo from header (Recommended)"] The scope header names no repository; it carries the scope label only.
50. [engineer-verified: "Cut to direction (Recommended)"] Context records the exploratory run as direction only: review cost rose with PR size.
51. [engineer-verified: "Keep documented limit (Recommended)"] A default-mode project directory symlinked outside the active `projects/` stays a documented limit, with no diagnostic count.
52. [engineer-verified: "Keep the banner (Recommended)"] `--all-accounts` output is marked by the DO NOT PUBLISH line on both streams only, with no per-line tag and no separate subcommand.
53. [engineer-verified: "Use 150/400/800 (Recommended)"] The size-bucket lower edges are 0, 150, 400 and 800. The same question also offered the verbatim labels "Add a --bucket-edges flag too" and "Keep current edges", and the engineer selected neither. The edges replace the earlier log-spaced edges.
54. [engineer-verified: "Yes, print the count (Recommended)"] Each Spearman rho prints beside the PR count it used.
55. [engineer-verified: "Yes, add it (Recommended)"] `transcript_analysis/__init__.py`'s package docstring lists `review_cost_by_size`.

## Critical files

**Create:**
- `claude/.claude/scripts/transcript_analysis/review_cost_by_size.py`: M1-M10. The comment on `_SIZE_BUCKET_LOWER_EDGES` states only that the edges follow the bucket bounds named in issue #1364, and states no result. `_compute_aggregates`' `spearman` shape and its docstring, and `_print_aggregates`' Spearman lines, follow M9.
- `claude/.claude/scripts/tests/test_transcript_review_cost_by_size.py`: tests pinning the behaviors under Verification step 3 and the further cases the file adds. The file is the single home for the full test list, and step 3 names behaviors, not every test.

**Modify:**
- `claude/.claude/scripts/transcript-analysis.py`:
  - Add the import, sorted ahead of `review_rounds` and in the shim's "Read bare by this file's own build_parser" comment shape.
  - Call `_add_review_cost_by_size_subparser(sub)` after `:738`.
  - Make `main()`'s comments at `:979-986` defer to the tuple comment (M2).
  - Make the shared refusal's clause for members with no `--config-dir` of their own read "this subcommand resolves its scan roots itself" (M2).
- `claude/.claude/scripts/transcript_analysis/scope.py`: add the tuple member and rewrite its comment (`:694-707`), and fix the `_resolve_cost_roots` docstring sentence (`:743-745`) (M2).
- `claude/.claude/scripts/transcript_analysis/gh_cli.py`: add `review-cost-by-size` to the module docstring's consumer list (`:1`) (row 36).
- `claude/.claude/scripts/transcript_analysis/review_rounds.py`: one sentence in `compute_review_round_costs`' docstring (`:362-369`) stating that `resolved_roots=None` over a multi-root iterator keys every round `(None, branch)`, so a branch's rounds merge across those roots (row 47). No code changes.
- `claude/.claude/scripts/transcript_analysis/__init__.py`: add `review_cost_by_size` to the package docstring's module list (row 55).
- `claude/.claude/scripts/tests/test_transcript_package_module_prefixes.py`: add the new module to `PRODUCTION_MODULES` and the new test file to `TEST_FILES`.
- `claude/.claude/scripts/tests/test_transcript_analysis.py`: the wiring test in Verification step 4.
- `claude/.claude/scripts/tests/test_transcript_cost_counts.py`: add `review-cost-by-size` to the refusal-message test's parametrization (`:863`), and point its docstring at the tuple comment (M2).
- `claude/.claude/scripts/tests/test_transcript_cli_bootstrap.py`: one fresh-interpreter test that `review-cost-by-size` without `--this-repo` exits 2 with an empty stdout and `requires --this-repo` on stderr (the stderr assertion separates the command's own refusal from argparse's invalid-choice exit 2), mirroring the `period-cost` refusal test there. It covers the sys.path[0]-only bootstrap path that no in-process test reaches.
- `docs/transcript-analysis.md`:
  - Add `review-cost-by-size` to the gh-calling list (`:7-12`).
  - At `:3`, replace the intro's partial list of active-account-only exceptions with a pointer to "Corpus scope: the declared-roots file", whose `:76` list stays the complete one (row 46).
  - At `:76`, add it without `--all-accounts` to the active-account-only exceptions, and add it beside `cost --summary` as a header that states no root count (M5).
  - At `:717-718`, add it beside `review-round-cost` in the main-thread-only bullet and in the positional round-attribution bullet (row 46).
  - Add a `## review-cost-by-size` section between `review-round-cost` and `## author-outcome` (`:1400`), on the house spine: **Purpose.**, "Requires `gh`", **Exit codes.**, **Flags.**, bold-lead blocks, **Sample output.** (default-mode stdout only, so no `scanning root` line or root count appears) and **When to reach for it.**
    - The bold-lead blocks cover the join, what a record measures, the aggregate definitions, the diagnostics, known limits, and publication. The join block says attribution follows the "Pre-branch default-branch attribution" block under `cost`, in the wording other sections use.
    - Known limits: the section's **Known limits.** list is the single home for their wording. It puts the measurement limits first, including that bucket medians resolve only at the bucket edges, and that a fork PR whose head matches a local branch joins to it with no diagnostic counting it (row 33). The causes of the gap between merged PRs listed and PRs joined follow, among them the round floor, aged-out sessions (G1) and removed worktrees (row 44). It ends with the sentence that the gap mixes these causes with reused branch names and unreadable sources, which only the ambiguous-branch and unreadable-sources lines count.
    - The aggregate bullets and the sample follow M9: the bucket bullet lists 0-149, 150-399, 400-799 and 800 or more, and the Spearman bullet and the sample's three Spearman lines carry `(n=N)`. The sample's bucket block has four rows whose counts agree with its own per-PR rows and sum to its PRs joined. The sample's per-PR records are invented, and its medians come from running `_print_aggregates(_compute_aggregates(...))` over them, not from hand arithmetic.
    - Publication, default mode: it publishes under `docs/private-project-redaction.md` § "This repository, one account" only when run from this repository's own checkout (row 41). It reads the active config directory's `projects/` alone, and a project directory symlinked from there is read wherever it points. A new figure from it still takes the check in `docs/private-project-redaction.md` § "New figures against the grandfathered set".
    - Publication, `--all-accounts`: a private view that prints DO NOT PUBLISH, and its output is never proposed for publication (row 42).
    - The sample output uses invented values, never a real run's. Its header is the M5 line, with the scope label and account scope and no repository name.
- `docs/transcript-analysis-architecture.md`:
  - Add the module to the shim-imported list (`:14-21`) and the registration list (`:51-54`).
  - Add it to the `review_rounds.py` importers sentence (`:26-29`) for `compute_review_round_costs`.
  - In `### review_rounds.py` (`:283-288`), add `compute_review_round_costs` to the public names, with `review_cost_by_size.py` as its consumer (row 47).
  - Add it to `gh_cli.py`'s consumer sentence (`:383`), with a sentence in the `:391-394` shape naming the helpers it reads.
  - Add a `### review_cost_by_size.py` section after `### review_rounds.py`. It names the module's `scope._DEFAULT_BRANCH_CANDIDATES` and `scope._names_no_pr_branch` reads and its one bare name, `_add_review_cost_by_size_subparser`.
- `docs/private-project-redaction.md`: one bullet in § "This repository, one account" (`:121-136`), covering the default mode. It says that `review-cost-by-size --this-repo`, run from this repository's checkout, refuses `--projects` and a top-level `--config-dir` and reads the active config directory alone. It adds that a new figure from it still takes the check under "New figures against the grandfathered set". One clause says its `--all-accounts` output is never proposed for publication and points to the next section, as the `pr-cost --record` bullet does (row 42).
- `CHANGELOG.md`: one bullet under `[Unreleased]` → `### Added` (`:455`) naming the subcommand, its single-account default, `--all-accounts`, and its `gh` requirement. The bullet carries no figures.

**Reuse, don't reimplement:**
- `review_rounds.compute_review_round_costs`.
- `scope`: `_add_project_scope_args`, `_resolve_project_scope`, `resolve_scan_roots`, `config_dir`, `_redaction_ordinals`, `_DEFAULT_BRANCH_CANDIDATES` and `_names_no_pr_branch`.
- `gh_cli`: `_git_remote_origin_host_and_owner_repo`, `_gh_auth_preflight_ok`, `_resolve_pinned_gh_repo`, `_gh_discover_merged_prs` and `_PR_COST_GH_PR_LIST_LIMIT`.
- `render._fmt_usd`, `collections.Counter` for `scan_gaps`, and `statistics`' `correlation`, `linear_regression` and `median`.

Test helpers to reuse:
- `_pr_cost_helpers._fake_pr_cost_subprocess_run(merged_prs=..., call_log=..., gh_auth_status_failure=...)` for `gh`. Merged-PR dicts come from a new-file helper that takes explicit, unequal `additions` and `deletions`, since `_merged_pr` fixes both at 1 (row 39).
- The `_this_repo_slugs` pre-seed on `args` (`test_transcript_cost_counts.py:44-50`), so no `git worktree list` runs. Hand-built `args` also carry `projects`, `config_dir=None` and `all_accounts`.
- `conftest._two_declared_roots` (`conftest.py:1254`) for every multi-root test, never the private copy in `test_transcript_review_rounds.py` and never combined with `fake_projects` (row 39).
- The conftest record builders `TestComputeReviewRoundCosts` uses (`test_transcript_review_rounds.py:179`), behind one new-file helper that writes a branch's rounds from per-round input tokens.
- `helpers.heading_texts` and `normalize_heading`, over a module-level doc path (row 37).
- The mode-`0o000` session file and `skipif(os.geteuid() == 0)` guard of `test_transcript_analysis.py:3268-3284`, for the unreadable-source test.

**Dispatch split:** one `code-writer` dispatch covers every file above. Code, tests and docs all rest on the publication reasoning in M2-M5, which any split would have to restate.

## Verification

1. From the worktree root, run `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py`. Changes under `transcript_analysis/` and to `docs/transcript-analysis*.md` select at least `claude/.claude/scripts/tests/`.
2. Run `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/`.
3. The new test file pins each behavior at the cheapest layer that can see it:
   - **Aggregates:** `_compute_aggregates` over literal records.
     - One bucket fixture with PRs at 0, 149, 150, 399, 400, 799, 800 and 10000 lines puts two PRs in each bucket, written as literal numbers. Each bucket's medians fall between two distinct values, which separates `median` from `median_low` and `median_high`.
     - A tied fixture pins a hand-computed Spearman rho. On it, average ranks, min ranks, ordinal ranks and raw Pearson give four different values. Its round, total and mean series order the PRs three different ways, so a swapped series fails.
     - On PRs at 1, 4, 9 and 16 lines with 1, 2, 3 and 4 rounds, each round costing the PR's line count in dollars, the round, total and mean elasticities are 0.5, 1.5 and 1.0 under `pytest.approx`, so a swapped series fails. A zero-line PR drops out of all three values. A zero-dollar PR drops out of the two dollar values only, and each value's count shows it. A constant round count gives slope exactly 0.0 and rho `None` (row 40). Equal sizes with a constant round count give slope `None`, so swapping the two pre-log checks fails. Five same-size PRs beside one zero-line PR of another size give slope `None` for every value, so running the equal-sizes check on the unfiltered sizes fails. Every round there costs the same, so the mean-dollar value is constant and that mutation returns 0.0 for it at any PR count, whatever the standard library does on a constant x. A zero-dollar PR beside PRs of distinct sizes and identical dollars gives a total-dollars slope of exactly 0.0, so running the pre-log checks on the unfiltered series fails.
     - One trend fixture holds four 4-round PRs whose per-round dollars are `[1, 2, 3, 4]`, `[4, 3, 2, 1]`, `[1, 3, 2, 4]` and `[3, 1, 4, 2]`, with rhos 1.0, -1.0, 0.8 and 0.0. It also holds a 3-round PR with falling dollars and a 4-round PR with constant dollars. The trend reads 4 PRs, median rho 0.4 and share above zero 0.5 under `pytest.approx`. Counting the 3-round or constant PR, counting 0.0 as above zero, flipping the sign, or ranking dollars against themselves each changes a value. A corpus with every PR under 4 rounds gives `None`.
     - An empty corpus gives `None` for every value and 0 for every count.
     - A one-PR corpus of 2 rounds gives `None` for every rho and every elasticity, with each elasticity count at 1, and its trend counts 0 PRs. Its bucket has count 1 and medians equal to that PR's own values. Every other bucket gives count 0 and `None`. Its 150-line PR sits on the 150 edge, so the test looks its bucket up by `lower == 150` and expects three empty buckets.
     - **Spearman count, at the print layer** (`_print_aggregates(_compute_aggregates(...))`): on the zero-line fixture, each Spearman line reads `(n=5)` while each elasticity line reads `(n=4)`, so a count copied from the elasticity pairs fails. The constant-round fixture prints `rounds per PR: n/a (n=3)`, and the empty corpus prints `n/a (n=0)`.
     - Existing assertions rewritten for M9's new edges and Spearman shape, never weakened: the bucket test's docstring, bounds and median rows (four, not five); the scalar `spearman[...]` assertions, which read the `(rho, count)` pair; the rendered `0-149 lines` and `800+ lines` rows; and `rounds per PR: n/a (n=3)`. A command-level aggregate assertion reads the first bucket as `0-149 lines`.
   - **Join:** `_join_rounds_to_prs` over literal round dicts and merged-PR dicts, in one fixture covering every outcome.
     - A branch whose rounds arrive out of `sort_key` order joins a PR with additions 100 and deletions 49. The record has 149 lines, per-round dollars in `sort_key` order, total and mean over `main_dollars + agent_dollars`, and the branch's unpriced and dangling counts in the sums.
     - An unmerged branch with its own unpriced turn counts as unmatched, and its turn stays out of the sums.
     - A branch matching two merged PRs counts as ambiguous.
     - A `main` round and a `worktree-agent-*` round count as unmatched, though the list holds two merged PRs headed by each of those two names. With one PR per name, checking the never-join names after the ambiguity check would still pass. `test_a_default_branch_round_stays_unmatched_beside_a_merged_pr_headed_by_that_name` repeats the default-branch case for `main`, `master` and `develop`.
     - A round on the detached head (`HEAD`) counts as unmatched beside a merged PR headed `HEAD`.
     - An unmatched branch, a `main` branch, a `worktree-agent-*` branch and an ambiguous branch with two or more rounds each add 1 to their count, and their rounds add nothing to the unpriced and dangling sums, so a per-round count fails.
     - A merged PR with no round makes no record.
     - Scan plus join: rounds timestamped after a PR's explicit `mergedAt` still count toward it.
     - `test_a_branch_joins_only_the_pr_whose_head_name_equals_it_exactly`: near-miss heads (`feat` beside `feat-2`, `feat/x` and `Feat`, and a longer round branch beside a shorter head) never join, so the join is exact-name.
     - `test_a_malformed_merged_pr_stops_the_join_instead_of_defaulting_to_zero_lines`: a merged PR that is the only match for a joinable round branch and is missing any of `additions`, `deletions` or `number` stops the join (M7). A merged PR missing `headRefName` stops it whether or not it would match.
   - **Scan:** `_scan_code_review_rounds` plus the join, on transcript files.
     - Under `conftest._two_declared_roots`, one branch has 2 code-review rounds in root A and 3 in root B, and B's slug-named project directory is a symlink to `tmp_path / "external"`. The joined PR has 5 rounds. That one fixture pins the cross-root sum and M3's symlink case.
     - When root B's slug-named directory is a symlink to root A's, the PR's round count equals A's alone.
     - In one root, a code-review round that dispatches a subagent sits alone in its session file, so it is that session's last window (row 48). Its dollars count once, at the exact `_priced` amount. A `plan-review` round and a `ready-for-review` round on the same branch sit in a second session file and never count.
   - **Command:** `cmd_review_cost_by_size` with `gh` faked and `call_log` recorded.
     - No `--this-repo`, `--all-accounts` without `--this-repo`, and `--projects x` with or without it each exit 2 with an empty `call_log`. The case with `--this-repo` is the only test of the `--projects` refusal itself. In the default mode, so does a `scope.config_dir` patched to raise `ValueError`, and stderr names this subcommand (M2).
     - Default mode under `_two_declared_roots`: the declared root's project directory carries the seeded slug, and its session sits on a branch with a merged PR. The PRs-joined count and the per-PR rows show only the active root's PRs. The header equals the exact expected line, and stderr has no `scanning root` line. Rows that tie on size, whose session files sort opposite to their PR numbers, sort by lines, then PR number, and print after every aggregate line. An unmatched sentinel branch name appears nowhere in stdout.
     - `--all-accounts` on the default-mode test's fixture: PRs joined reads one more than the default mode's, and the declared root's PR has a row. stderr carries `scanning root` lines. The DO NOT PUBLISH line is on both streams, and the header equals the exact expected line.
     - The faked pin's owner/repo appears nowhere in stdout.
     - In the default mode, two session files in the active root's slug directory are mode `0o000` beside a readable session with a joined round. The unreadable-sources line reads 2, so a count of distinct gap kinds fails, PRs joined reads 1, and the cmd returns normally.
     - `test_failing_gh_auth_exits_1_before_any_report_or_scan`: a failing `gh auth`, in the default mode and under `--all-accounts`, exits 1 with this command's message, with no report, no `scanning root` line and no scan. Under `--all-accounts` the DO NOT PUBLISH line still leads both streams.
     - `test_failing_origin_remote_lookup_exits_1_under_this_commands_name`: a failing `git remote get-url origin` exits 1 with a message naming this subcommand, in the default mode and under `--all-accounts`. Under `--all-accounts` the DO NOT PUBLISH line still leads both streams.
     - `test_all_accounts_over_a_single_root_still_marks_both_streams_do_not_publish`: with a single resolved root and no declared-roots file, `--all-accounts` prints no `scanning root` line and still prints the DO NOT PUBLISH line on both streams.
     - With `gh_cli._PR_COST_GH_PR_LIST_LIMIT` patched small and no sessions, a list one short of the limit prints no limit note, and a list at the limit prints it in the stdout diagnostics. Both runs print the full report with `n/a` aggregates and return normally, which pins the empty scope.
     - The DO NOT PUBLISH line's cited heading exists in `docs/private-project-redaction.md` (row 37).
4. In `test_transcript_analysis.py`, `build_parser()` parses `review-cost-by-size --this-repo --all-accounts` to `cmd_review_cost_by_size` with `all_accounts` true, and the form without the flag to false. `test_transcript_cost_counts.py`'s refusal-message test covers the new tuple member (M2).
5. Before finishing, confirm each of five mutations turns its test red:
   - The default mode calls `scope.resolve_scan_roots(args)`: the default-mode test fails.
   - `--all-accounts` builds the single active root: the `--all-accounts` test fails.
   - `_scan_code_review_rounds` passes the resolved roots as `resolved_roots`: the symlink scan test raises `AssertionError`.
   - The iterator is built with `include_subagents=True`: the subagent scan test fails.
   - `_resolve_project_scope` gets no `scan_gaps`: the unreadable-source test fails.
6. A real single-account run exits 0 and prints the header, aggregates, diagnostics and per-PR rows in that order. Run it from this worktree as `scripts/venv-run.sh python3 claude/.claude/scripts/transcript-analysis.py review-cost-by-size --this-repo`. No figure from it goes into the commit message or PR body; piece 4 cites the command later.
7. On a machine whose declared roots include a project directory symlinked outside every root, `review-cost-by-size --this-repo --all-accounts` exits 0 with no `AssertionError` and prints the DO NOT PUBLISH line on both streams. Run it with stdout and stderr redirected to two separate scratch files outside the repo, with paths from `mktemp`, so the banner can be counted per stream. Report only the exit status, whether the string `AssertionError` is absent from both files, and the count of lines matching the DO NOT PUBLISH banner on each stream. Delete both files afterwards, and never read their contents into an agent's context. No stdout or stderr text, root count, count line or aggregate goes into an agent's return, the commit message or the PR body (row 42).

## Out of scope

- **Fixing `scope._root_index_for_path` for its nine callers** (row 9). This is recommended as its own PR. It would attribute a transcript to the root it was listed under: `jsonl.parent.parent.resolve()` matched against `resolved_roots` (rows 8, 10). That PR also needs a new trigger for the path-omission check at `test_transcript_cost.py:1220-1241` and a rewrite of `docs/transcript-analysis.md:49`. This PR does not need it (M3), and the fix changes shared, documented behavior, which row 1 argues against.
- **Combining machines.** See row 2.
- **Configurable bucket edges, or extra edges inside 400-800** (row 53). Fixed edges cannot place a threshold inside any bucket, so a threshold there reads from the per-PR rows. That covers the 400-800 range issue #1364's open question asks about, which is the single bucket 400-799. A `--bucket-edges` flag would add a CLI mechanism this piece does not need to reproduce the issue's figure.
- **Raising or paginating the 1000-PR discovery limit.** The constant is shared with pr-cost, workstream-cost and period-cost; M6 prints the condition in the diagnostics instead.
- **The pin and discovery helpers' `pr-cost` messages.** workstream-cost already accepts them (row 11), and parameterizing them is a `gh_cli` change with its own tests. M6 step 1 passes this command's name to the one helper that already takes it.
- **A glob fallback for removed worktrees.** `--projects` stays refused (M2), because a path glob can admit a foreign repository's sessions (`scope.py:107-110`), which `--this-repo`'s identity match keeps out. The docs name the gap (row 44).
- **Validating `gh pr list` entries.** `_gh_pr_list` applies no shape check (`gh_cli.py:453`). M7's direct indexing stops the run on a malformed entry rather than default a field to 0, as pr-cost does (`pr_cost.py:357`). period-cost's `_validated_pr_list` (`period_cost.py:748`) would make this module import `period_cost`, and an excluded-entry count is new machinery for third-party payload drift, which the cooperative threat model does not cover.
- **Resolving a reused branch name by SHA overlap.** It needs per-PR `gh pr view` calls (row 13); M7 counts the branch and excludes it.
- **Joining on `isCrossRepository`.** A fork PR whose head branch matches a local feature branch still joins. Requesting the field changes `_gh_discover_merged_prs`' shared field list (row 33). M7 already keeps default-branch rounds out, and the docs name the residual.
- **Promoting `review_rounds._POOLED_REFUSAL_DOC_POINTER` to `scope.py`.** Its trigger (`review_rounds.py:521-523`) is a second pooled-shares mode. `--all-accounts` prints no shares block and has no refusals or floors.
- **Refusing a default-mode project directory symlinked outside the active root.** `cost-counts` and `cost --summary` read such a directory too. The docs state the guarantee as one configured config directory instead (Critical files).
- **Selection-effect diagnostics.** The docs name the round-count floor, the retention edge and the removed-worktree gap as known limits, with no per-cause count. `branch_totals` (row 7) could later count merged PRs with activity but no code-review round.
- **A shared `code-review` constant.** Five package modules already carry the literal or their own constant (row 26).
- **A `/transcript-analysis` skill routing row.** A `SKILL.md` edit would add `/skill-review` to this PR.
- **Per-PR exclusion flags, `--since`/`--until`, and other review skills.** Sizing `code-review` needs none of them.
- **Tests of `review_rounds`' own contracts.** Its tests already pin `sort_key` ordering across sessions and a review-orchestrator dispatch opening a `code-review` round (row 38). A missing timestamp's `+inf` placement is built into that same `sort_key` (row 7). This command only sorts by `sort_key` and consumes the filtered rounds.
- **A stale-pricing-rate line.** Only `review-round-cost --pooled` prints one (`review_rounds.py:567-572`, `:1310`). This command follows the unpooled report, which prints none, so its tests need no injected date.
- **Enforcing the Python 3.11 floor in CI.** CI and ruff target 3.12 (row 20), so nothing stops a later switch to `correlation(method="ranked")`. A 3.11 CI cell or a ruff `target-version` at the floor is a repo-wide decision.
- **`README.md:79`'s "states the root count" sentence.** `review-round-cost --pooled` already prints a header without one, so the sentence is wrong before this PR, and `review-cost-by-size` adds a second header without one. `docs/transcript-analysis.md:77` is the home for the header exceptions and does not call its list complete. README's sentence stays a known residual for its own fix.
- **Pieces 2-4 of #1364.** Each ships as its own PR.
