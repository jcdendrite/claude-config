# transcript-analysis.py architecture

This is the contributor-facing counterpart to [`docs/transcript-analysis.md`](transcript-analysis.md)'s
CLI reference: where each piece of `transcript-analysis.py`'s logic lives, not what each subcommand does.

`transcript-analysis.py` stays a plain top-level script at its own path — every existing caller
(the hook nudge, the `/transcript-analysis` skill, a contributor's own shell history) keeps working
unchanged. Its own body still holds `build_parser()`, `main()`, and every `cmd_*` subcommand handler.
Leaf logic with no dependency on any `cmd_*` function has moved into the `transcript_analysis/`
package (`claude/.claude/scripts/transcript_analysis/`), which the script imports from. Command
groups (the `cmd_*` functions themselves) and `cli.py` (`build_parser()`/`main()`) are later-phase
moves, once every command group has followed; splitting them out first would need the still-unmoved
`cmd_*` functions to import back into the shim, a circular import this decomposition avoids by
staying leafward-only for now.

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
what's tested or how. `tests/conftest.py` carries the shared fixtures that reach across the
shim/package boundary (`fake_projects`, `fake_config_dir_factory`, `_table_cols`,
`cost_ledger_file`); see its own docstrings for why `fake_projects` patches both `scope.PROJECTS_DIR`
and the shim's still-independent `config_dir` binding.
