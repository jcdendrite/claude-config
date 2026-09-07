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
package. `cost.py`, `reviewer_yield.py`, and `review_rounds.py` are the only modules the shim
imports back into (not just from) — cost-ledger and review-trace still call their public functions
from the shim until those phases migrate too.

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
`_SUBCOMMANDS_WITH_OWN_CONFIG_DIR`). Also owns `_redaction_ordinals` — kept here instead of
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
`_is_reviewer_subagent_type` is also reached bare from still-unmigrated review-trace code — see
the exception noted above.

### `review_rounds.py`

The review-round-cost command family: `cmd_review_round_cost` and every helper used only by it —
per-branch review-round-window detection across both the `Skill` tool_use and `/slash` invocation
shapes, and recursive per-round subagent dollar attribution via `corpus._index_subagent_dispatches`'
toolUseId join (`compute_review_round_costs`). Imports `corpus`, `pricing`, `redaction`, `render`,
and `scope` all by module (attribute access), matching `cost.py`'s convention — deliberately no
`cost.py` import: a round's own branch is its opening record's own `gitBranch`, carried forward
when absent, and every record inside that round's window is attributed to it, never
`cost._attributed_branch`'s worktree-agent-\* resolution, which a main-thread round-opening record
never needs. `REVIEW_SKILLS` is the one public name here, back-imported by
the still-unmigrated `cmd_judgment_pair` in the shim for its own `--skills` default — a second
entry in the one-directional exception noted above.

## Sibling scripts

`token-analyzer.py` and `analyze-context.py` import these modules directly
(`from transcript_analysis.corpus import read_session_file`, `from transcript_analysis import
scope`, etc.) instead of loading the entire `transcript-analysis.py` CLI via
`importlib.util.spec_from_file_location` to reach a handful of helpers. Both also import
`render.py`'s `_fam`/`_content_text` rather than maintaining their own copies.

## Tests

Each of `corpus.py`, `scope.py`, `redaction.py`, `pricing.py`, and `render.py` is exercised only
through `transcript-analysis.py`'s existing test suite (`tests/test_transcript_analysis.py`), which
calls into the shim. `cost.py`'s own tests live in `tests/test_transcript_cost.py`, the first
per-command-group test file the decomposition has produced; it loads its own independent copy of
`transcript-analysis.py` via the same `spec_from_file_location` boilerplate
`test_transcript_analysis.py` uses, rather than importing that file's `_mod`. `tests/conftest.py`
carries the shared fixtures that reach across the shim/package boundary and across both test files
(`fake_projects`, `fake_config_dir_factory`, `_table_cols`, `cost_ledger_file`); see its own
docstrings for why `fake_projects` patches both `scope.PROJECTS_DIR` and the shim's still-independent
`config_dir` binding.
