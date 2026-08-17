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
package. `cost.py` is the one deliberate, temporary exception, in the opposite direction: cost-ledger
(a later, still-unmigrated phase) calls `cost.py`'s `compute_cost_trend_data` from inside the shim,
so the shim imports `cost.py` back for that one name. This resolves cleanly (`cost.py` itself has no
import of the shim), but it means `cost.py`'s public surface is reachable from monolith code this
phase left behind — expected to shrink to zero once cost-ledger's own phase moves.

## The package

### `corpus.py`

JSONL transcript read/parse and session iteration: `iter_sessions`, `read_session_file`,
`SUBAGENT_SUBDIR` (the `<session_id>/subagents/*.jsonl` split-transcript convention), and
`_parse_ts`. No dependency on scope resolution, redaction, or pricing — every other module
(and the shim) builds on this one.

### `scope.py`

Scan-root and project-scope resolution: `PROJECTS_DIR`, `resolve_scan_roots`,
`print_resolved_scope`, the `--this-repo` project-slug machinery, and the multi-root
`--config-dir` resolution cost-family subcommands share (`_resolve_cost_roots`,
`_SUBCOMMANDS_WITH_OWN_CONFIG_DIR`). Also owns `_redaction_ordinals` (assigning each scan root
a stable `account-N` number): this module's own multi-root scan functions need it for a
non-redaction "scanning root N/M..." progress diagnostic, and `redaction.py` needs it too (for
`_build_redact_map`'s account-namespaced labels) — keeping it here, rather than in `redaction.py`,
makes that second dependency one-directional (`redaction.py` -> `scope.py`) instead of circular.

`PROJECTS_DIR` is this package's one reassignable global. Every reader outside this module —
including `transcript-analysis.py` itself — reads it as `scope.PROJECTS_DIR` (attribute access on
this module), never `from transcript_analysis.scope import PROJECTS_DIR`. The latter binds a
reference at import time that a later `scope.PROJECTS_DIR = ...` reassignment (`main()`'s
`--config-dir` handling, or a test's `monkeypatch.setattr(scope, "PROJECTS_DIR", ...)`) would never
reach — the exact silent-desync failure mode this discipline exists to close off. The same
attribute-access rule applies to any other cross-module state a test patches directly (e.g.
`scope.config_dir`, `pricing._usage_drift_warned`): import the module, not a name out of it.

### `redaction.py`

Project-label pseudonymization: the redact map (`_build_redact_map`), the corpus fingerprint,
and session/branch/subagent-type label assignment. Reads `scope.PROJECTS_DIR` and
`scope._redaction_ordinals` by attribute access, per the discipline above.

### `pricing.py`

Rate tables, per-turn dollar pricing (`_price_turn`), token counts, context-window sizing, and
`dedup_turns_by_request_id` (collapsing Claude Code's one-record-per-content-block write pattern
into one record per API call). Self-contained: no dependency on `scope.py` or `redaction.py`.

### `render.py`

Small display-formatting helpers with no state of their own: model-family labels (`_fam`),
markdown/table rendering, `_content_text`, `_fmt_usd`, `_pct_of`. Self-contained.

### `cost.py`

The cost command family: `cmd_cost`, `cmd_cost_trend`, and every helper used only by them —
corpus-wide dollar-cost reporting by token class/model ID/thread/account/project
(`_cost_report`), and per-ISO-week cost-trend accumulation (`compute_cost_trend_data`,
`_cost_trend_report`). The first command-group module in the package, not a leaf: it imports
`corpus`, `scope`, `redaction`, `pricing`, and `render` all by module (attribute access), matching
the cross-module discipline every other package module already follows. `compute_cost_trend_data`
is the one public (non-underscore-prefixed) name here, reached from the still-unmigrated
cost-ledger code in the shim — see the one-directional exception noted above.

## Sibling scripts

`token-analyzer.py` and `analyze-context.py` import these modules directly
(`from transcript_analysis.corpus import read_session_file`, `from transcript_analysis import
scope`, etc.) instead of loading the entire `transcript-analysis.py` CLI via
`importlib.util.spec_from_file_location` to reach a handful of helpers. Both also import
`render.py`'s `_fam`/`_content_text` rather than maintaining their own copies.

## Tests

Each of `corpus.py`, `scope.py`, `redaction.py`, `pricing.py`, and `render.py` is exercised through
`transcript-analysis.py`'s own existing test suite (`tests/test_transcript_analysis.py`), which
calls into the shim exactly as before — the split is an internal reorganization, not a change to
what's tested or how. `cost.py`'s own tests live in `tests/test_transcript_cost.py`, the first
per-command-group test file the decomposition has produced; it loads its own independent copy of
`transcript-analysis.py` via the same `spec_from_file_location` boilerplate
`test_transcript_analysis.py` uses, rather than importing that file's `_mod`. `tests/conftest.py`
carries the shared fixtures that reach across the shim/package boundary and across both test files
(`fake_projects`, `fake_config_dir_factory`, `_table_cols`, `cost_ledger_file`); see its own
docstrings for why `fake_projects` patches both `scope.PROJECTS_DIR` and the shim's still-independent
`config_dir` binding.
