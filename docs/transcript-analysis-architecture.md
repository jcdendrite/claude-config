# transcript-analysis.py architecture

This is the contributor-facing counterpart to [`docs/transcript-analysis.md`](transcript-analysis.md)'s
CLI reference: where each piece of `transcript-analysis.py`'s logic lives, not what each subcommand does.

`transcript-analysis.py` stays a plain top-level script at its own path — every existing caller
(the hook nudge, the `/transcript-analysis` skill, a contributor's own shell history) keeps working
unchanged. Its own body still holds `build_parser()`, `main()`, and every not-yet-moved `cmd_*`
subcommand handler. Leaf logic with no dependency on any `cmd_*` function, plus (starting with
`cost.py`) whole command groups, have moved into the `transcript_analysis/` package
(`claude/.claude/scripts/transcript_analysis/`), which the script imports from. `cli.py`
(`build_parser()`/`main()`) is a later-phase move, once every command group has followed.

Every command-group module moves in leafward first: the shim imports it, never the reverse, so no
circular import is possible while `cmd_*` functions remain split across both the shim and the
package. `cost.py`, `reviewer_yield.py`, `review_rounds.py`, `denials.py`, `review_trace.py`,
`read_scope.py`, `pr_cost.py`, `pr_cost_export.py`, and `cache_rebuild.py` are the only modules the
shim imports back into (not just from). Cost-ledger still calls `review_trace.py`'s `compute_deny_summary_data` from
the shim. The CLI's own `build_parser()` still wires up `review_trace.py`'s
`cmd_review_trace`/`REVIEW_TRACE_SKILLS` from the shim, until the `cli.py` phase migrates both. Two
still-unmigrated friction/command-shape helpers likewise call `denials.py`'s
`hook_denial_key`/`_drop_denial_command_flag_values` by name from the shim.
`build_parser()` likewise wires up `read_scope.py`'s `cmd_read_scope` from the shim.
The still-unmigrated context-composition code separately reads `read_scope.py`'s
`_READ_SCOPE_CHARS_PER_TOKEN` by name from the shim.
`review_trace.py` also imports `reviewer_yield.py`, for its own reviewer-spawn detection
(`reviewer_yield._is_reviewer_subagent_type`), and `review_rounds.py`, for its `/slash`-invocation
skill-name matching (`review_rounds._round_skill_name`, `review_rounds._SLASH_COMMAND_RE`) — the
package's first two imports from one command-group module into another.
`build_parser()` likewise wires up `pr_cost.py`'s `cmd_pr_cost` (with its two
`--asof-window-days`/`--plan-file-glob` default constants) and `pr_cost_export.py`'s
`cmd_pr_cost_export` from the shim.
`build_parser()` likewise wires up `cache_rebuild.py`'s `cmd_cache_rebuild` and its two
`--since`/`--threshold` default constants (`_CACHE_REBUILD_DEFAULT_SINCE`,
`_CACHE_REBUILD_DEFAULT_THRESHOLD`) from the shim.

## The package

### `corpus.py`

JSONL transcript read/parse and session iteration: `iter_sessions`, `read_session_file`,
`SUBAGENT_SUBDIR` (the `<session_id>/subagents/*.jsonl` split-transcript convention), `_parse_ts`,
and `_index_subagent_dispatches` (one session's toolUseId → paired subagent `.jsonl`/requested-model
join, reused recursively by `review_rounds.py`'s nested-dispatch descent since the function's own
`jsonl.parent / jsonl.stem / SUBAGENT_SUBDIR` layout resolves identically for a subagent's own
transcript file). No dependency on scope resolution, redaction, or pricing — every other module
(and the shim) builds on this one.

Also owns `split_command_segments` (tokenize a raw shell command, then split on `&&`/`||`/`;`/`|`)
as the single source of truth for two consumers: the shim's own mutating-git classifier, and
`author_outcome.py`'s clean-marker-write matcher (`_is_clean_marker_write`).

### `scope.py`

Scan-root and project-scope resolution: `PROJECTS_DIR`, `resolve_scan_roots`,
`print_resolved_scope`, the `--this-repo` project-slug machinery, and the multi-root
`--config-dir` resolution cost-family subcommands share (`_resolve_cost_roots`,
`_SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR`). Also owns `_redaction_ordinals` — kept here instead of
`redaction.py` so `redaction.py`'s dependency on it stays one-directional, not circular.

Read reassignable module globals (`scope.PROJECTS_DIR`, `scope.config_dir`,
`pricing._usage_drift_warned`) via attribute access, never `from module import NAME` — the latter
binds at import time and misses later reassignment or monkeypatching.

### `redaction.py`

Project-label pseudonymization: the redact map (`_build_redact_map`), the corpus fingerprint,
and session/branch/subagent-type label assignment. Reads `scope.PROJECTS_DIR` and
`scope._redaction_ordinals` by attribute access, per the discipline above. Also imports
`render._sanitize_table_cell` directly (not by attribute access, since it's a pure function with
no reassignable state) to strip control characters from a `--this-repo`-disclosed raw label
before it reaches a table row. No cycle: `render.py` stays a leaf with no dependency back on
`redaction.py`. The shim's `cmd_subagents`/`cmd_subagent_mix` also call `_sanitize_table_cell`
directly on their single-root labels, and on `cmd_subagents`' `tool_name` column. Every
`gitBranch`/`subagent_type`/`tool_name` value these two subcommands print is therefore
control-character-sanitized unconditionally, regardless of the `--this-repo`/multi-root
disclosure gating described above. The model-mix table's `Declared` column is a deliberate
exception: it's read from a local agent-definition file's own `model:` frontmatter, not from
transcript content, and is left unsanitized on the theory that a local file's trust boundary
differs from a remote model/subagent/MCP-tool-result's.

Also owns the `--this-repo` subagent_type disclosure allowlist:

- `_BUILT_IN_AGENT_TYPES` — the Claude Code built-in `subagent_type` values present in every
  install.
- `_REPO_AGENT_DEFINITIONS_DIR` — this repo's own tracked `agents/` directory.
- `_repo_tracked_agent_type_names` — the stems of every `agents/*.md` file that directory
  git-tracks, unioned with `_BUILT_IN_AGENT_TYPES`.

The shim's `cmd_subagent_mix` and `cmd_cost_counts` call `_repo_tracked_agent_type_names` bare to gate
which raw `subagent_type` values a `--this-repo` report may disclose versus fold into a withheld
row (this is the shim-imports-back-into-package pattern described above).

### `pricing.py`

Rate tables, per-turn dollar pricing (`_price_turn`), token counts, context-window sizing, and
`dedup_turns_by_request_id` (collapsing Claude Code's one-record-per-content-block write pattern
into one record per API call). Self-contained: no dependency on `scope.py` or `redaction.py`.

### `render.py`

Small display-formatting and text-normalization helpers with no state of their own: model-family
labels (`_fam`), markdown/table rendering, `_content_text`, `_fmt_usd`, `_pct_of`,
`_strip_task_notifications`. Self-contained.

### `cost.py`

The cost command family: `cmd_cost`, `cmd_cost_trend`, and every helper used only by them —
corpus-wide dollar-cost reporting by token class/model ID/thread/account/project
(`_cost_report`), and per-ISO-week cost-trend accumulation (`compute_cost_trend_data`,
`_cost_trend_report`). The first command-group module in the package, not a leaf: it imports
`corpus`, `scope`, `redaction`, `pricing`, and `render` all by module (attribute access), matching
the cross-module discipline every other package module already follows. `compute_cost_trend_data`
is the one public (non-underscore-prefixed) name here, reached from the still-unmigrated
cost-ledger code in the shim — see the one-directional exception noted above.

### `reviewer_yield.py`

The reviewer-yield command family: `cmd_reviewer_yield` and every helper used only by it —
joining each main-thread reviewer-agent dispatch to its own subagent transcript, classifying its
verdict (findings-found/zero-finding/unclassified), and scoring cited-path edit overlap
(`compute_reviewer_yield_data`). Imports `corpus`, `pricing`, `render`, and `scope` all by module
(attribute access), matching `cost.py`'s convention. `compute_reviewer_yield_data` is the one
public name here, reached from the still-unmigrated cost-ledger code in the shim;
`_is_reviewer_subagent_type` is read by `review_trace.py`, via
`reviewer_yield._is_reviewer_subagent_type` (attribute access) — no longer reached bare from the
shim, since review-trace's own detection moved into the package.

### `review_rounds.py`

The review-round-cost command family: `cmd_review_round_cost` and every helper used only by it —
per-branch review-round-window detection across both the `Skill` tool_use and `/slash` invocation
shapes, and recursive per-round subagent dollar attribution via `corpus._index_subagent_dispatches`'
toolUseId join (`compute_review_round_costs`). Also exports `compute_review_round_counts`, a
count-only sibling reusing the same detection helpers to produce per-skill round counts with no
pricing, no dispatch index, and no recursion — the shim's `cmd_cost_counts` calls it for the
`### Review rounds` half of its output. Imports `corpus`, `pricing`, `redaction`, `render`,
and `scope` all by module (attribute access), matching `cost.py`'s convention — deliberately no
`cost.py` import: a round's own branch is its opening record's own `gitBranch`, carried forward
when absent, and every record inside that round's window is attributed to it, never
`cost._attributed_branch`'s worktree-agent-\* resolution, which a main-thread round-opening record
never needs. `REVIEW_SKILLS`, `compute_review_round_counts`, and `detect_round_windows` are the
public names here. `REVIEW_SKILLS` is back-imported by the still-unmigrated `cmd_judgment_pair` in
the shim for its own `--skills` default, one instance of the one-directional exception noted
above. `_round_skill_name` and `_SLASH_COMMAND_RE` are two more instances, both back-imported by
the still-unmigrated review-trace code: `_round_skill_name` for its own `REVIEW_TRACE_SKILLS`
membership test and `--skill` filter comparison, `_SLASH_COMMAND_RE` to extract a `/slash`-invoked
skill name before `_round_skill_name` normalizes it.
`cmd_cost_counts` and its subagent-spawn-count aggregator have not
moved into the package alongside `compute_review_round_counts`.
`detect_round_windows` is public (no leading
underscore) for a separate reason: `author_outcome.py` is a second consumer, reading only each
window's own `open_idx`/`skill`.

### `author_outcome.py`

The author-outcome command family: `cmd_author_outcome` and every helper used only by it —
for each `--agent`-typed dispatch (default `code-writer`), joins it to the `code-review` round
that judged its diff (`compute_author_outcomes`), by completion-index ordering against
`review_rounds.detect_round_windows`' own `open_idx`, and classifies the outcome by reading that
session's own review-narrative-ledger files directly. `_ledger_files_for_session` locates every
file matching a session-id glob under `<config_dir_root>/review-narrative-ledger/`, and
`_read_ledger_row_entries_for_session` reads and merges all of them, sorted by `event_time`. See
`docs/transcript-analysis.md`'s author-outcome section ("Ledger lookup") for the merge behavior
and the residual gaps it still leaves. Ledger rows are
matched to a round by exact `round`-field equality against that round's own 1-indexed position in
the transcript's round-open sequence. The transcript is still the sole source for round-open
positions, dispatch completion ordering, and the `marker.sh write code-review` Bash `tool_use`
fallback signal used only when a round has no ledger row at all (`_is_clean_marker_write`).

See `_lib.sh`'s own `_lib_acquire_append_lock`/`_lib_append_json_line_locked`
docstrings for the append-lock mechanism `review-ledger.sh`'s schema-v2
write depends on.

Imports `corpus`, `pricing`, `render`, `review_rounds`, and `scope` all by module
(attribute access), matching `review_rounds.py`'s own convention. See
`docs/transcript-analysis.md`'s author-outcome section for the full failure definition, output
shape, and documented scope gaps.

### `denials.py`

Hook-denial detection and classification, with no dependency on any `cmd_*` function: the shared
`hook_denial_key` predicate covering both transcript shapes (a legacy `attachment` record and a
current-format `is_error` `tool_result`), the label/cause/command-shape classifiers
(`_denial_hook_label`, `_denial_cause_kind`, `_denial_command_shape`), and `toolDenialKind`
non-gate-friction classification (`_is_nongate_friction_kind`, `_friction_kind_label`). A leaf:
imports `corpus` and `render` by module (attribute access), matching `cost.py`'s convention —
`corpus._parse_ts` for the module-level `_TOOL_DENIAL_KIND_REGIME_START_TS` constant,
`render._content_text` for `hook_denial_key`'s current-shape decode. `hook_denial_key` and
`_drop_denial_command_flag_values` are the two names reached bare from still-unmigrated
friction/command-shape helpers in the shim — see the exception noted above.

### `review_trace.py`

The review-trace command family: `cmd_review_trace` and every helper used only by it —
`--deny-summary`'s grouped denial/friction accumulation and report (`compute_deny_summary_data`,
promoted from a shim-private `_compute_deny_summary_data`), and the per-session event-timeline
detector both the default output and `--deny-summary` share (`_review_trace_session_events`).
Imports `corpus`, `denials`, `render`, `reviewer_yield`, `review_rounds`, and `scope` all by module
(attribute access), matching `cost.py`'s convention; calls `reviewer_yield._is_reviewer_subagent_type`
for its own reviewer-spawn detection and `review_rounds._round_skill_name`/
`review_rounds._SLASH_COMMAND_RE` for its `/slash`-invocation skill matching — see that module's own
section above for why these are the package's first cross-command-group imports.
`_review_trace_session_events` re-expresses its own `_normalize_skill_name` (the emitted display
label's lighter directory-only strip) locally rather than back-importing the shim's copy — the same
re-expression pattern `review_rounds.py` uses for `_is_fresh_user_prompt` and `_SLASH_COMMAND_RE`.
`cmd_review_trace`, `REVIEW_TRACE_SKILLS`, and
`compute_deny_summary_data` are the three public names here, reached from the still-unmigrated
`build_parser()`/cost-ledger code in the shim — see the exception noted above.

### `read_scope.py`

The read-scope command family: `cmd_read_scope` and every helper used only by it — the Read-call
census by cohort and scope (`_scan_read_scope_session`), repeat-whole-file-read detection, and
per-file-and-sessionId prompt-token growth. Imports `corpus`, `pricing`, `render`, and `scope` all
by module (attribute access), matching `cost.py`'s convention. `cmd_read_scope` and
`_READ_SCOPE_CHARS_PER_TOKEN` are the two names reached bare from the shim — see the exception
noted above.

### `ledger_common.py`

Recording primitives shared by the cost-ledger and pr-cost ledgers: the generated per-config-dir
machine identity (`_resolve_machine_identity`, `_machine_identity_path`), the git-tracked
destination check (`_ledger_path_is_git_tracked`), the machine-label format, the merge-conflict
markers both parsers refuse, and the local-lock timing both `--record` paths use. A leaf: no
dependency on any other package module. Binds `config_dir` by name from `_config_dir`, mirroring
`scope.py`'s own binding (see the attribute-access discipline noted under `scope.py` above) — every
still-shim-resident consumer (cost-ledger) and every package consumer (`pr_cost_ledger.py`,
`pr_cost.py`, `pr_cost_export.py`) reads through this one binding. The seven names cost-ledger
reaches bare from the shim: `_MACHINE_LABEL_RE`, `_COST_LEDGER_CONFLICT_MARKERS`, both lock
constants, `_ledger_path_is_git_tracked`, `_resolve_machine_identity`, and
`_warn_machine_identity_absent_from_ledger`.

### `gh_cli.py`

gh and git-remote access shared by pr-link, pr-cost, and workstream-cost: origin
host/owner/repo parsing, gh stderr classification, rate-limit backoff, auth preflight,
effective-repo pinning, and merged/closed PR discovery. Imports `pr_cost_ledger` and `redaction` by
module — `pr_cost_ledger` for the two degraded-status constants `_gh_call_with_backoff` returns
(`_PR_COST_STATUS_DEGRADED_RATE_LIMIT`/`_PR_COST_STATUS_DEGRADED_NETWORK`), keeping the ledger's own
status enum a single source of truth rather than a duplicated pair of strings. The nine names
pr-link and workstream-cost reach bare from the shim: `_classify_gh_error`,
`_GH_ERROR_KIND_NETWORK`, `_git_remote_origin_host_and_owner_repo`, `_gh_host_qualified_repo`,
`_GH_CALL_TIMEOUT_S`, `_gh_auth_preflight_ok`, `_resolve_pinned_gh_repo`,
`_gh_discover_merged_prs`, and `_gh_discover_closed_unmerged_pr_branches`.

### `pr_cost_ledger.py`

The pr-cost ledger's on-disk format: column schema, status and join-confidence enums, path
resolution, canonical parser and formatter, append-only upsert, crash-safe write, and the
`--record` lock. Imports `ledger_common` by module, for `_ledger_path_is_git_tracked` and the
machine-identity primitives its own writers call before recording. Binds `config_dir` by name from
`_config_dir`, the same pattern `ledger_common.py` uses, for its own `_pr_cost_ledger_path`.

### `pr_cost.py`

The pr-cost command family: `cmd_pr_cost` and every helper used only by it — the branch-to-merged-PR
join, per-PR gh enrichment, mechanical review-surface proxies, and the read/`--record` report. Every
stdout/stderr path routes branch and repo values through `redaction._assign_root_scoped_redact_label`,
never raw. Imports `corpus`, `cost`, `gh_cli`, `ledger_common`, `pr_cost_ledger`, `pricing`,
`redaction`, `render`, and `scope` all by module. The `cost` import reaches `cost.py`'s own
`_compute_pr_cost_branch_totals` and `_new_pr_cost_agg` — the pr-cost command group depending on the
cost command group, not the reverse. `cmd_pr_cost`, `_PR_COST_ASOF_WINDOW_DAYS_DEFAULT`, and
`_DEFAULT_PR_COST_PLAN_FILE_GLOB` are the three names reached bare from the shim.

### `pr_cost_export.py`

The pr-cost-export command family: `cmd_pr_cost_export` and every helper used only by it —
collapsing every declared account's pr-cost ledger to current rows, redacting them, and publishing
one TSV. Makes no gh call and scans no transcript corpus. Imports `ledger_common`, `pr_cost_ledger`,
`redaction`, and `scope` all by module. `cmd_pr_cost_export` is the one name reached bare from the
shim.

### `cache_rebuild_rules.py`

A leaf: the cache-rebuild family's pure rules — per-call cause classification against the vendor's
5m/1h cache tiers, subagent idle-gap cause attribution, per-call priced excess and cacheTtl switch
deltas, and the `--ttl-verdict` per-root reducers, plus every label constant they emit. Imports
`corpus` and `pricing` by module (attribute access), matching `cost.py`'s convention. No name here
is reached bare from the shim.

### `cache_rebuild.py`

The cache-rebuild command family: `cmd_cache_rebuild` and its report. Imports `corpus`, `pricing`,
`redaction`, `render`, and `scope` all by module (attribute access), matching `cost.py`'s
convention. Unlike every other command-group module, it also imports `cache_rebuild_rules.py`'s 31
constants and pure functions by name, since none is reassigned at runtime. A test that
monkeypatches one of those 31 names must patch both `cache_rebuild_rules`'s own binding and this
module's separate imported binding to take effect. `cmd_cache_rebuild`,
`_CACHE_REBUILD_DEFAULT_SINCE`, and `_CACHE_REBUILD_DEFAULT_THRESHOLD` are the three names reached
bare from the shim.

## Sibling scripts

`token-analyzer.py` and `analyze-context.py` import these modules directly
(`from transcript_analysis.corpus import read_session_file`, `from transcript_analysis import
scope`, etc.) instead of loading the entire `transcript-analysis.py` CLI via
`importlib.util.spec_from_file_location` to reach a handful of helpers. Both also import
`render.py`'s `_fam`/`_content_text` rather than maintaining their own copies.

## Tests

Each of `corpus.py`, `scope.py`, `redaction.py`, `pricing.py`, and `render.py` is exercised only
through `transcript-analysis.py`'s existing test suite (`tests/test_transcript_analysis.py`), which
calls into the shim. Every other package module has its own per-command-group (or, for `denials.py`,
per-leaf) test file: `cost.py`'s in `tests/test_transcript_cost.py`, `denials.py`'s in
`tests/test_transcript_denials.py`, `review_trace.py`'s in
`tests/test_transcript_review_trace.py`, `read_scope.py`'s in
`tests/test_transcript_read_scope.py`, `ledger_common.py`'s (`TestMachineIdentity`) in
`tests/test_transcript_ledger_common.py`, `pr_cost_ledger.py`'s in
`tests/test_transcript_pr_cost_ledger.py`, and `pr_cost_export.py`'s per-account gating, ordinals,
and provenance in `tests/test_transcript_pr_cost_export_accounts.py`. The pr-cost family splits
further along module seams rather than one file per module: `gh_cli.py`'s own unit coverage lives
in `tests/test_transcript_gh_cli.py`; `pr_cost.py`'s local-mechanics coverage lives in
`tests/test_transcript_pr_cost.py`, its gh-integration coverage (exercised end to end through
`cmd_pr_cost`) in `tests/test_transcript_pr_cost_gh.py`; and `pr_cost_export.py`'s remaining
coverage (redaction, timestamps, refusals) lives in `tests/test_transcript_pr_cost_export.py`. All
seven pr-cost-family test files, plus `tests/_pr_cost_helpers.py` (a plain module, not a test file
itself — see `.claude/rules/test-tree-packaging.md` for why its own consumers import it as
`from ._pr_cost_helpers import ...`), share the family-only fixtures the legacy pr-cost tests used:
`_enable_pr_cost`, `_pr_cost_args`, `_pr_cost_export_args`, `_fake_pr_cost_subprocess_run`,
`_argv_carries_repo_pin`, `_sample_pr_cost_row`, and `_legacy_row_line`. Each loads its own
independent copy of `transcript-analysis.py` via the same `spec_from_file_location` boilerplate
`test_transcript_analysis.py` uses, rather than importing that file's `_mod`. Each reaches a moved
module's own private helpers as `_mod.<module>.<name>` (e.g. `_mod.denials.hook_denial_key`,
`_mod.review_trace.cmd_review_trace`) — the same channel the shim-reimport exception above relies
on. `tests/conftest.py` carries the shared fixtures that reach across the shim/package
boundary and across every test file (`fake_projects`, `fake_config_dir_factory`, `_table_cols`,
`cost_ledger_file`, `cost_ledger_enabled`, `_hook_deny`, `_hook_deny_current`, `_review_trace_args`,
`_compact_boundary_rec`, `_cost_ledger_args`, `_cost_ledger_row`, `_two_declared_roots`); see its own
docstrings for why `fake_projects` patches four `config_dir` bindings: `scope.config_dir` and the
shim's still-independent `config_dir` (for cost-ledger, spend-over-threshold, and rearm-backtest,
not yet moved into the package), plus `ledger_common.config_dir` and `pr_cost_ledger.config_dir`
(each module's own by-name binding, mirroring `scope.py`'s pattern). `author_outcome.py`'s own tests
live in `tests/test_author_outcome.py`: most exercise the package module directly
(`from transcript_analysis import author_outcome`), with a small `spec_from_file_location`-loaded
shim copy reserved for the argparse-wiring and `cmd_author_outcome` end-to-end tests.

The cache-rebuild family splits along thematic seams rather than one file per module:

- `tests/test_transcript_cache_rebuild.py` — core report mechanics
- `test_transcript_cache_rebuild_attribution.py` (`_attribution.py`) — subagent idle-gap cause attribution
- `test_transcript_cache_rebuild_switch_delta.py` (`_switch_delta.py`) — 5m-to-1h switch-delta pricing and per-dispatch dispersion
- `test_transcript_cache_rebuild_ttl_rules.py` (`_ttl_rules.py`) — `--ttl-verdict` wiring and each verdict rule's own tests
- `test_transcript_cache_rebuild_ttl_accumulation.py` (`_ttl_accumulation.py`) — per-root accumulation and dominance reduction
- `test_transcript_cache_rebuild_ttl_footing.py` (`_ttl_footing.py`) — pure-1h idle-band reads, rate-multiplier footing, and the default-path regression

All six share
`tests/_cache_rebuild_helpers.py` (a plain module, not a test file itself — see
`.claude/rules/test-tree-packaging.md` for why its own consumers import it as
`from ._cache_rebuild_helpers import ...`), plus the family-only helpers each file keeps local to
itself. `TestCacheRebuildCrossInstrumentReconciliation` stays in
`tests/test_transcript_analysis.py` rather than moving with the rest of the family: it spans both
cache-rebuild and cache-efficiency, and `.claude/plans/transcript-analysis-decomposition.md`'s rule
for a cross-group test (stated there for `_UNCONDITIONAL_HEADER_CASES`) keeps such a test in the
legacy file until every group it references has moved.
