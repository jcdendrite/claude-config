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
package. `cost.py`, `reviewer_yield.py`, `review_rounds.py`, `denials.py`, and `review_trace.py`
are the only modules the shim imports back into (not just from). Cost-ledger still calls
`review_trace.py`'s `compute_deny_summary_data` from the shim. The CLI's own `build_parser()` still
wires up `review_trace.py`'s `cmd_review_trace`/`REVIEW_TRACE_SKILLS` from the shim, until the
`cli.py` phase migrates both. Two still-unmigrated friction/command-shape helpers likewise call
`denials.py`'s `hook_denial_key`/`_drop_denial_command_flag_values` by name from the shim.
`review_trace.py` also imports `reviewer_yield.py`, for its own reviewer-spawn detection
(`reviewer_yield._is_reviewer_subagent_type`), and `review_rounds.py`, for its `/slash`-invocation
skill-name matching (`review_rounds._round_skill_name`, `review_rounds._SLASH_COMMAND_RE`) — the
package's first two imports from one command-group module into another.

## The package

### `corpus.py`

JSONL transcript read/parse and session iteration: `iter_sessions`, `read_session_file`,
`SUBAGENT_SUBDIR` (the `<session_id>/subagents/*.jsonl` split-transcript convention), `_parse_ts`,
and `_index_subagent_dispatches` (one session's toolUseId → paired subagent `.jsonl`/requested-model
join, reused recursively by `review_rounds.py`'s nested-dispatch descent since the function's own
`jsonl.parent / jsonl.stem / SUBAGENT_SUBDIR` layout resolves identically for a subagent's own
transcript file). No dependency on scope resolution, redaction, or pricing — every other module
(and the shim) builds on this one.

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
never needs. `REVIEW_SKILLS` and `compute_review_round_counts` are the two public names here;
`REVIEW_SKILLS` is also back-imported by the still-unmigrated `cmd_judgment_pair` in the shim for
its own `--skills` default — a second entry in the one-directional exception noted above.
`_round_skill_name` is a third entry, back-imported by the still-unmigrated review-trace code for
its own `REVIEW_TRACE_SKILLS` membership test and `--skill` filter comparison.
`cmd_cost_counts` and its subagent-spawn-count aggregator stay in the shim rather than moving into
the package alongside `compute_review_round_counts`: the `--this-repo` subagent_type disclosure
allowlist they must honor (`_repo_tracked_agent_type_names`) lives in the shim, and the package may
not import back from the shim.

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
`tests/test_transcript_denials.py`, and `review_trace.py`'s in
`tests/test_transcript_review_trace.py`. Each loads its own independent copy of
`transcript-analysis.py` via the same `spec_from_file_location` boilerplate
`test_transcript_analysis.py` uses, rather than importing that file's `_mod`. Each reaches a moved
module's own private helpers as `_mod.<module>.<name>` (e.g. `_mod.denials.hook_denial_key`,
`_mod.review_trace.cmd_review_trace`) — the same channel the shim-reimport exception above relies
on. `tests/conftest.py` carries the shared fixtures that reach across the shim/package
boundary and across every test file (`fake_projects`, `fake_config_dir_factory`, `_table_cols`,
`cost_ledger_file`, `_hook_deny`, `_hook_deny_current`, `_review_trace_args`); see its own
docstrings for why `fake_projects` patches both `scope.PROJECTS_DIR` and the shim's still-independent
`config_dir` binding.
