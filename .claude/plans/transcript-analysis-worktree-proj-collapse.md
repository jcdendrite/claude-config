# Fix worktree-driven Proj over-count in transcript-analysis's `cmd_buckets`

## Context

`cmd_buckets` in `claude/.claude/scripts/transcript-analysis.py` computes its `Proj` column by adding the raw, uncollapsed project-directory slug to a per-branch set (`d["projects"].add(jsonl.parent.name)`, line 290). It does not resolve a git-worktree checkout's slug back to its parent repository's canonical identity before counting distinct projects, so a repo's main checkout and its own linked worktree (`repo` vs. `repo--claude-worktrees-<branch>`) count as two distinct "projects" on a branch that is genuinely one repo's one branch.

This over-count directly breaks any analysis that gates on `Proj == 1` as an admissibility test — starting with `.claude/plans/opus-session-cost-ab-test.md`'s admission rule 1 ("It appears under exactly one declared root and shows `Proj == 1` in that root's `buckets` output"). That study's own Feasibility-and-freeze dispatch already surfaced an underweighted version of this signal (roughly 26 more branches would have classified as Opus-anchored, with the dominant admissibility failure reason being this same worktree-driven inflation rather than genuine scarcity of Opus-anchored work), filed at the time as a diagnostic footnote rather than escalated. A later session's live re-scans confirmed it as a real, quantified, worsening-over-time admission-rate decline, on two separate machines — reported by that session, not independently reproduced in this plan's own research. Because every downstream figure in that study's delivered report (never committed; delivered in-session only) was computed over this same biased, shrinking-over-time admissible population, that report is treated as retracted rather than merely caveated.

Intended outcome: `cmd_buckets`'s `Proj` column correctly collapses a worktree-suffixed project-dir slug to its parent repo's identity before counting, reusing the existing `_project_family` helper that `cost.py`'s `--by-project` path already uses to solve the identical problem, with test coverage that pins both the fix and the over-collapse guard rail it must not cross. Re-running the `opus-session-cost-ab-test` analysis against the corrected classifier is separate, later work, blocked on this landing, and is out of scope for this plan.

## Approach

Collapse the worktree suffix off the raw project-directory slug before it enters `cmd_buckets`' per-branch `projects` set: replace `d["projects"].add(jsonl.parent.name)` (`transcript-analysis.py:290`) with `d["projects"].add(_project_family(jsonl.parent.name))`, reusing the existing pure-string helper at `redaction.py:32-45` rather than adding any new collapsing logic. `_project_family` is applied to the **raw slug**, not to `_derive_proj_label(jsonl)`'s output — `cmd_buckets` never displays the value, it only counts distinct ones, so the display-shortening transform that `cost.py:753` performs first buys nothing here and carries a real undercount risk (below). Three tests pin the new behavior and the two boundaries it must not cross, and the one prose sentence in `docs/transcript-analysis.md` that defines the `Proj` column is corrected in the same change.

Alternatives set aside. Reusing `scope._repo_scoped_project_slugs` (live `git worktree list` against the current process's cwd) was rejected: it `sys.exit(1)`s whenever cwd isn't a recognized worktree, and `cmd_buckets` scans historical transcripts from repos and worktrees that may no longer exist on disk or belong to a different account — a scanning tool must tolerate that, so a fail-closed live-git probe is the wrong shape. Writing a new suffix-stripping helper inside `transcript-analysis.py` was rejected as a second authority for one rule; `_WORKTREE_SUFFIX_RE` already is that authority and `redaction.py` is already imported by the shim.

Mirroring `cost.py`'s exact `_derive_proj_label` → `_project_family` pipeline was the closest competing option and is rejected on a verified behavioral difference. `_derive_proj_label` is `jsonl.parent.name.lstrip("-").replace("-", "/", 2).split("/", 2)[-1]` (`redaction.py:23`) — it discards the first two hyphen-delimited tokens, i.e. the `home`/`<user>` path prefix. Two genuinely distinct project dirs that differ only in those tokens (`-home-alice-repo` and `-home-bob-repo`; `-Users-alice-repo` and `-home-alice-repo`) reduce to the same label. In `cost.py`'s `--by-project` table that is an accepted display grouping. In `cmd_buckets`' `Proj` **count** it would be an undercount, which is the opposite error class from the one this plan fixes and strictly the worse of the two: over-counting excludes a real branch visibly, under-counting admits a genuinely multi-repo branch silently into a `Proj == 1` admission rule. Operating on the raw slug preserves every distinguishing character except the worktree suffix.

**Assumption ledger**

Root: `cmd_buckets`' `Proj` column counts raw project-directory slugs, so one repo's main checkout and its own linked worktree count as two projects on the same branch — an over-count that makes `Proj == 1` a stricter test than "one repo."

Givens:
- G1. Claude Code's project-directory naming (`/` and `.` both mapped to `-`, so a linked worktree's slug is the main slug plus `--claude-worktrees-<branch>`) is the harness's, not this repo's, to change. `[verified: claude/.claude/scripts/transcript_analysis/scope.py:36-44, "Verified against real dirs: /home/<user>/repo -> -home-<user>-repo; /home/<user>/repo/.claude/worktrees/b -> -home-<user>-repo--claude-worktrees-b"]`

Rows:
1. `_project_family`'s trailing-anchored regex (`--claude-worktrees-.+$`) matches a raw slug exactly as it matches a `_derive_proj_label` output, because `_derive_proj_label` strips only a prefix and returns the tail. `anchors: root` `[verified: redaction.py:23 and redaction.py:29 — read both bodies; lstrip("-") plus replace("-", "/", 2) plus split("/", 2)[-1] alters only the leading two tokens]`
2. Passing the raw slug rather than `_derive_proj_label`'s output avoids a new undercount, since that function discards the `home`/`<user>` prefix and two distinct home directories holding the same repo name would otherwise collapse. `anchors: row1` `[verified: redaction.py:23]`
3. `cmd_buckets` is the only site in the codebase that counts distinct project-directory slugs, so the fix stays single-site. `cmd_commit_gate` (transcript-analysis.py:3207-3208) and `cmd_skill_pair` (transcript-analysis.py:3041) also read `jsonl.parent.name`, but only to filter against an `--exclude-projects` glob, where collapsing would be wrong because a glob may deliberately target worktree-suffixed dirs. `cmd_subagents`, `cmd_subagent_mix`, `cmd_plan_boundary`, `cmd_pr_cost`, and `cmd_review_trace` group by branch or session ID and never count project dirs. `anchors: root` `[verified: exhaustive git grep for every ".parent.name" call site in transcript-analysis.py and transcript_analysis/*.py this review round — exactly four sites total: redaction.py:23 (_derive_proj_label), transcript-analysis.py:290/3041/3207]`

   Given `_project_family` (row 1) applies to any string ending in the worktree suffix regardless of what precedes it, and `_derive_proj_label` (row 2's citation) is confirmed by its own preceding comment to carry that suffix through unchanged onto its output, no distinct-project-counting site was missed by relying on `.parent.name` call sites alone: every place that reads a project-dir slug at all does so either via the raw path directly or via `_derive_proj_label`'s output, and both preserve the suffix identically.
4. `transcript-analysis.py`'s own convention is a name-import from `redaction`, not the qualified `redaction.` access `cost.py` uses — the shim already name-imports eight redaction symbols including `_derive_proj_label`. `redaction.py`'s top-of-file qualified-access rule governs how *it* reads `scope`, not how consumers import it. `anchors: row1` `[verified: transcript-analysis.py:95-106; redaction.py:5-7; docs/transcript-analysis-architecture.md:79 scopes the attribute-access discipline to package modules importing each other]`
5. `_project_family` gains a second consumer whose input shape (raw slug) its docstring does not currently admit, and whose failure surface (`buckets`' `Proj` count) its caveat does not name — both need updating in the same change or the helper's stated contract goes stale on landing. `anchors: row1` `[verified: redaction.py:32-45]`
6. `docs/transcript-analysis.md:73` defines `Proj` as "the count of distinct project directories contributing to that row." That sentence becomes false on landing. It describes current behavior rather than recording an event, so it is in-scope prose, not preserved content. `anchors: root` `[verified: docs/transcript-analysis.md:73]`
7. The sample-output block at `docs/transcript-analysis.md:62-71` is not regenerated. Its `Proj` values (1, 1, 6) remain plausible under the corrected semantics, and re-running the command against a live corpus to refresh them would put private-corpus-derived figures into a public repo. `anchors: row6` `[verified: docs/transcript-analysis.md:62-71; CLAUDE.md § "Also redact structural fingerprints and provenance"]`
8. No existing test exercises `_project_family` on a raw slug at any level — the two cost tests reach it only through `_derive_proj_label` first, and there is no redaction-owned test module. The new input contract therefore needs its own direct assertions. `anchors: row5` `[verified: git grep _project_family returns only redaction.py, cost.py:753, and test_transcript_cost.py:1438/1445; no test_transcript_redaction.py exists in claude/.claude/scripts/tests/]`
9. A direct unit test belongs in `test_transcript_analysis.py`, which keeps `docs/transcript-analysis-architecture.md:119-121` ("`redaction.py` … exercised only through … `tests/test_transcript_analysis.py`") accurate and needs no architecture-doc edit. `anchors: row8` `[verified: docs/transcript-analysis-architecture.md:119-121]`
10. The existing regression guard `test_proj_column_counts_two_project_dirs_sharing_a_branch` uses `-home-u-repo-a` / `-home-u-repo-b`, which differ in the **tail** — it would still pass under the rejected `_derive_proj_label` variant, so it does not guard row 2's reasoning. A second guard whose slugs differ only in the user segment is needed to make that rationale executable. `anchors: row2` `[verified: test_transcript_analysis.py:669-684]`
11. Live re-scans found this over-count producing a worsening-over-time admission-rate decline in the `opus-session-cost-ab-test` corpus. `[unverified]` — reported from a separate completed analysis session; not reproduced here. Nothing in this plan's design depends on it, only its priority.

Mechanism justification. The chosen mechanism is a single call to an existing pure function with no I/O — the lightest primitive available, and lighter than either alternative weighed above (`anchors: root`). No over-powered-primitive enumeration is owed, since the two heavier candidates (live `git worktree list` resolution; a new parallel helper) are the ones being rejected, not adopted.

## Critical files

Single `code-writer` dispatch. The four files are one coherent edit — the source change, the helper's contract, its tests, and the sentence that documents the column — and splitting them would force the shared `_project_family`-input-shape context to be restated in every prompt.

- **`claude/.claude/scripts/transcript-analysis.py`** — two edits.
  1. Add `_project_family` to the existing `from transcript_analysis.redaction import (...)` block (lines 95-106), positioned between `_derive_proj_label` and `_redact_proj_label` to hold the block's existing sort order.
  2. Line 290: `d["projects"].add(jsonl.parent.name)` → `d["projects"].add(_project_family(jsonl.parent.name))`.

  **Reuse:** `_project_family` and `_WORKTREE_SUFFIX_RE` already exist; write no new collapsing logic. Do **not** route through `_derive_proj_label` — see Approach and ledger row 2.

- **`claude/.claude/scripts/transcript_analysis/redaction.py`** — docstring only, no behavior change. Broaden `_project_family`'s opening line to admit both input shapes and name the second consumer in its caveat. Prescribed content (durable facts only):
  - First line: `Collapse a project label or a raw project-dir slug to its base-repo "family" key.`
  - Add one sentence stating that both forms carry the trailing `--claude-worktrees-<branch>` suffix unchanged, so the same trailing-anchored match applies to either.
  - Caveat's closing line: `re-evaluate if cost's --by-project rows or buckets' Proj count ever shows an unexpected merge.`

- **`claude/.claude/scripts/tests/test_transcript_analysis.py`** — three additions, all following `TestBuckets`' existing fixture idiom (`tmp_path` + `monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)` + `_write_jsonl` + `_asst` + `_table_cols(..., header_contains="Branch", max_labels=8)`).
  1. In `TestBuckets`, `test_proj_column_collapses_a_worktree_dir_into_its_main_repo`: `-home-u-repo-a` and `-home-u-repo-a--claude-worktrees-branch-x`, one session each on branch `feat` → `Proj == 1`, `Sess == 2`. This is the fix's pinning test.
  2. In `TestBuckets`, `test_proj_column_counts_same_repo_name_under_two_home_dirs_as_two_projects`: `-home-alice-repo` and `-home-bob-repo`, one session each on branch `feat` → `Proj == 2`. Guards ledger row 2 — fails under the rejected `_derive_proj_label` variant, passes under the chosen one.
  3. New `TestProjectFamily` class adjacent to `TestBuckets`, two assertions on `_mod._project_family` directly: a raw slug with the worktree suffix returns the base slug; a raw slug without the suffix returns unchanged.

  **Reuse:** `_write_jsonl`, `_asst`, and `_table_cols` all come from `claude/.claude/scripts/tests/conftest.py` (lines 32, 194, 315) — do not redefine any of them locally. The existing `TestBuckets` tests at lines 651-684 are the shape to copy; both must keep passing unmodified.

- **`docs/transcript-analysis.md`** — line 73 only. Replace the `Proj` definition sentence so it defines the column as distinct **repositories**, states that a repo's linked-worktree directories collapse into its main checkout's slug first (so a branch worked in both still shows `Proj == 1`), and keeps the existing pooled-row guidance about `--this-repo` / a narrower `--projects` glob. Leave the sample-output block at lines 62-71 untouched (ledger row 7). This file sits outside the source/test triad, but the sentence is a description of current behavior that this change falsifies, so leaving it is a defect, not scope discipline.

**Omission found after implementation.** Row 3's exhaustive-grep claim missed `cmd_user_input`
(`transcript-analysis.py:572`), which counted distinct projects via the same rejected
`_derive_proj_label`-first pipeline row 2 argues against. `cmd_user_input` now carries the
identical `_project_family(jsonl.parent.name)` fix, pinned by
`test_scope_project_count_counts_same_repo_name_under_two_home_dirs_as_two_projects`.

## Verification

`.venv/bin/python3 claude/.claude/scripts/select-tests.py` — the repo's documented scoped test command (`CLAUDE.md` § Commands). It maps the source and test paths above to the scripts domain and picks up `docs/transcript-analysis.md` through its `DOCS_DIR` blanket rule, so no hand-widening to the full suite is warranted.

Beyond the selected suite passing, confirm all four specifically:

1. The two new `TestBuckets` tests and the new `TestProjectFamily` class pass.
2. `test_proj_column_counts_distinct_project_dirs_not_sessions` and `test_proj_column_counts_two_project_dirs_sharing_a_branch` pass **unmodified** — the second is the over-collapse guard.
3. `test_worktree_suffixed_siblings_collapse_into_one_family_row` and `test_non_worktree_label_colliding_with_suffix_shape_merges_into_existing_family` (`test_transcript_cost.py`) pass — `cost.py`'s use of `_project_family` is unchanged and must stay so.
4. `test_transcript_analysis_cost_import_direction.py` passes — a new name-import lands in `transcript-analysis.py`'s import block, and that test governs import direction there.

Also run `.venv/bin/ruff check claude/.claude/ claude-skills/`: the new import must not trip `F401` (it is used at line 290, so no `noqa` is warranted — do not add one).

## Out of scope

- Re-running the `opus-session-cost-ab-test` analysis against the corrected classifier. Separate, later work, blocked on this merging. That later plan will also need to design explicit cross-machine pooling of results (the engineer has confirmed the corpus should combine data from multiple machines, not just multiple accounts on one machine) — the original `opus-session-cost-ab-test` plan only designed for multi-account pooling on a single machine, so this is new design territory for that future plan, not an extension of this one.
- `.claude/plans/opus-session-cost-ab-test.md` — merged as PR #912; not edited here, including its admission rule 1.
- `cmd_commit_gate`'s and `cmd_skill_pair`'s `jsonl.parent.name` uses, and the five sibling subcommands (`cmd_subagents`, `cmd_subagent_mix`, `cmd_plan_boundary`, `cmd_pr_cost`, `cmd_review_trace`). Verified unaffected (ledger row 3); collapsing in either glob-filter site would be actively wrong.
- Narrowing `_project_family`'s literal-substring match to require genuine worktree provenance. A change the plan *could* make but deliberately won't: the tradeoff is already accepted and test-pinned for `cost.py`'s `--by-project` path, and tightening it would need a decision about what provenance evidence a historical-scan tool may demand of a directory that no longer exists.
- Regenerating the `buckets` sample-output block in `docs/transcript-analysis.md` (ledger row 7).
- Any cross-machine or cross-account tooling productionalization.
- `docs/transcript-analysis-architecture.md` — its `redaction.py` and Tests sections stay accurate under this change (ledger row 9).
