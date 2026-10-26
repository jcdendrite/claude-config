# transcript-analysis.py architecture

This is the contributor-facing counterpart to [`docs/transcript-analysis.md`](transcript-analysis.md)'s
CLI reference: where each piece of `transcript-analysis.py`'s logic lives, not what each subcommand does.

`transcript-analysis.py` stays a plain top-level script at its own path — every existing caller
(the hook nudge, the `/transcript-analysis` skill, a contributor's own shell history) keeps working
unchanged. Its own body holds `build_parser()` and `main()`, and defines no `cmd_*` subcommand
handler. Leaf logic with no dependency on any `cmd_*` function, plus (starting with
`cost.py`) whole command groups, have moved into the `transcript_analysis/` package
(`claude/.claude/scripts/transcript_analysis/`), which the script imports from.

Every command-group module moves in leafward first: the shim imports it, never the reverse, so no
circular import is possible. `cost.py`, `reviewer_yield.py`, `review_rounds.py`, `denials.py`, `review_trace.py`,
`read_scope.py`, `pr_cost.py`, `pr_cost_export.py`, `cache_rebuild.py`, `audit_routing.py`,
`cost_ledger.py`, `workstream_cost.py`, `subagents.py`, `subagent_mix.py`, `rearm_backtest.py`,
`spend_over_threshold.py`, `plan_boundary.py`, `handoff_signal_response.py`, `judgment_pair.py`,
`skill_invocation.py`, `skill_pair.py`, `period_cost.py`, `buckets.py`, `fail_seq.py`, `struggle.py`,
`user_input.py`, `duration.py`, `friction_count.py`, `turn_shape.py`, `commit_gate.py`, `pr_link.py`,
`sessions.py`, `context_distribution.py`, `context_composition.py`, `cache_efficiency.py`,
`edit_format.py`, and `instrument_authoring.py` are the only
modules the shim imports back into (not just from). The CLI's own `build_parser()` still wires up `review_trace.py`'s
`cmd_review_trace`/`REVIEW_TRACE_SKILLS` from the shim, until the `cli.py` phase migrates both.
`build_parser()` likewise wires up `read_scope.py`'s `cmd_read_scope` from the shim.
A command-group module may import another by module (attribute access). Among the review modules,
`reviewer_yield.py` and `review_rounds.py` import no other command-group module. `review_rounds.py`
is imported by `subagent_mix.py` (for `REVIEW_SKILLS` and `compute_review_round_counts`),
`judgment_pair.py` (for `REVIEW_SKILLS`), `skill_invocation.py` (for `_SLASH_COMMAND_RE`), and
`author_outcome.py` (for `detect_round_windows` and `spawn_round_skill`). `review_trace.py` imports `reviewer_yield.py` (for
`_is_reviewer_subagent_type`), `review_rounds.py` (for `_round_skill_name` and
`_SLASH_COMMAND_RE`), and `skill_invocation.py` (for `_normalize_skill_name`).
`friction_count.py` imports `fail_seq.py` (for `TEST_RUNNER_RE` and `FAILED_RE`) and `struggle.py` (for
`STRUGGLE_PHRASES`), and `user_input.py` imports `struggle.py` (for `STRUGGLE_PHRASES`).
`turn_shape.py` imports `denials.py` (for `_drop_denial_command_flag_values`).
`context_composition.py` imports `read_scope.py` (for `_READ_SCOPE_CHARS_PER_TOKEN`) and `subagents.py` (for `_MCP_TOOL_BUCKET_LABEL`).
`build_parser()` likewise wires up `pr_cost.py`'s `cmd_pr_cost` (with its two
`--asof-window-days`/`--plan-file-glob` default constants) and `pr_cost_export.py`'s
`cmd_pr_cost_export` from the shim.
`build_parser()` likewise wires up `cache_rebuild.py`'s `cmd_cache_rebuild` and its two
`--since`/`--threshold` default constants (`_CACHE_REBUILD_DEFAULT_SINCE`,
`_CACHE_REBUILD_DEFAULT_THRESHOLD`) from the shim.
`build_parser()` likewise wires up `audit_routing.py`'s `cmd_audit_routing`,
`cmd_audit_routing_shape`, and `cmd_audit_routing_samples` from the shim.
`build_parser()` likewise wires up `subagents.py`'s `cmd_subagents` and `subagent_mix.py`'s
`cmd_subagent_mix`/`cmd_cost_counts` from the shim.
`build_parser()` likewise wires up `rearm_backtest.py`'s `cmd_rearm_backtest` and
`spend_over_threshold.py`'s `cmd_spend_over_threshold` from the shim.
`build_parser()` likewise wires up `plan_boundary.py`'s `cmd_plan_boundary`,
`handoff_signal_response.py`'s `cmd_handoff_signal_response`, and `period_cost.py`'s `cmd_period_cost`
from the shim.
`build_parser()` registers judgment-pair, skill-invocation, skill-pair, buckets, fail-seq, struggle,
user-input, duration, friction-count, pr-link, commit-gate, turn-shape, turn-shape-samples,
turn-shape-holdout-samples, sessions, context-distribution, context-composition, cache-efficiency, edit-format, and
instrument-authoring by calling each owning
module's `_add_<command>_subparser` from the shim at that command's place in registration order. Each
module's own registration binds `set_defaults(func=...)` to its own handler.
The shim also binds `cmd_judgment_pair`, `cmd_skill_invocation`, `cmd_skill_pair`, `cmd_buckets`,
`cmd_duration`, `cmd_fail_seq`, `cmd_struggle`, `cmd_user_input`, `cmd_commit_gate`, `cmd_pr_link`, and
`cmd_sessions`, `cmd_context_distribution`, `cmd_context_composition`, `cmd_cache_efficiency`,
`cmd_edit_format`, `cmd_instrument_authoring`, and
`cache_efficiency.py`'s `_cache_efficiency_report`, and those
bindings exist only for test files, which read them as `_mod.<name>`. Six leaf bindings are test-only
too, each annotated at its import site: `_dedup_turns_by_request_id`, `_DO_NOT_PUBLISH_BANNER`,
`_redaction_ordinals`, `_resolve_cost_roots`, `_resolve_project_scope`, and `_print_resolved_scope`.
`TestRootsThreadingSpy` patches two of them with `raising=True`, so each must stay bound. They are read-only test
affordances: a test that needs to steer a moved command patches the module whose binding the
handler reads at call time, not the shim name.

## The package

### `corpus.py`

JSONL transcript read/parse and session iteration: `iter_sessions`, `read_session_file`,
`SUBAGENT_SUBDIR` (the `<session_id>/subagents/*.jsonl` split-transcript convention), `_parse_ts`,
and `_index_subagent_dispatches` (one session's toolUseId → paired subagent `.jsonl`/requested-model
join). The harness writes every nested dispatch's `.meta.json` into the session's own flat
`subagents/` directory. One session's index therefore also resolves a dispatch spawned from inside
another subagent. `review_rounds.py`'s nested-dispatch descent reuses it. No dependency on scope
resolution, redaction, or pricing — every other module (and the shim) builds on this one.

Also owns `split_command_segments` (tokenize a raw shell command, then split on `&&`/`||`/`;`/`|`)
as the single source of truth for two consumers: `turn_shape.py`'s mutating-git classifier
(`_bash_command_is_mutating_git`), and
`author_outcome.py`'s clean-marker-write matcher (`_is_clean_marker_write`).

### `scope.py`

Scan-root and project-scope resolution: `PROJECTS_DIR`, `resolve_scan_roots`,
`print_resolved_scope`, the `--this-repo` project-slug machinery, and the multi-root
`--config-dir` resolution cost-family subcommands share (`_resolve_cost_roots`,
`_SUBCOMMANDS_REFUSING_TOP_LEVEL_CONFIG_DIR`). Also owns `_redaction_ordinals` — kept here instead of
`redaction.py` so `redaction.py`'s dependency on it stays one-directional, not circular. Also owns the
per-record branch attribution that the consumers listed under "Pre-branch default-branch attribution" in `docs/transcript-analysis.md` share: `_session_branch_context` (built once
per session), `_attributed_branch` (the timeline step for sidechain records, then the pre-branch step) and
`_attribute_carried_branch` (the pre-branch step alone, for positional consumers).
The rule itself is documented in `docs/transcript-analysis.md` under "Subagent attribution" and
"Pre-branch default-branch attribution".

`scope.py` also owns `_add_project_scope_args`, which registers the mutually exclusive
`--projects`/`--this-repo` flags on a subparser. `_resolve_project_scope` reads the `this_repo`
attribute that function defines.

`scope.py` also owns `_iso_date`, the argparse `type=` validator for every YYYY-MM-DD flag, which
`_parse_absolute_window_args` relies on.

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
`redaction.py`. `subagents.py`'s `cmd_subagents` and `subagent_mix.py`'s `cmd_subagent_mix` also
call `render._sanitize_table_cell` on their single-root labels, and `cmd_subagents` also calls it
on its `tool_name` column. Every `gitBranch`/`subagent_type`/`tool_name` value these two
subcommands print is therefore control-character-sanitized unconditionally, regardless of the
`--this-repo`/multi-root disclosure gating described above. The model-mix table's `Declared` column is a deliberate
exception: it's read from a local agent-definition file's own `model:` frontmatter, not from
transcript content, and is left unsanitized on the theory that a local file's trust boundary
differs from a remote model/subagent/MCP-tool-result's.

Also owns the `--this-repo` subagent_type disclosure allowlist:

- `_BUILT_IN_AGENT_TYPES` — the Claude Code built-in `subagent_type` values present in every
  install.
- `_REPO_AGENT_DEFINITIONS_DIR` — this repo's own tracked `agents/` directory.
- `_repo_tracked_agent_type_names` — the stems of every `agents/*.md` file that directory
  git-tracks, unioned with `_BUILT_IN_AGENT_TYPES`.

`subagent_mix.py`'s `cmd_subagent_mix` and `cmd_cost_counts` call
`redaction._repo_tracked_agent_type_names` (attribute access) to gate which raw `subagent_type`
values a `--this-repo` report may disclose versus fold into a withheld row.

### `pricing.py`

Rate tables, per-turn dollar pricing (`_price_turn`), token counts, context-window sizing, and
`dedup_turns_by_request_id` (collapsing Claude Code's one-record-per-content-block write pattern
into one record per API call). Self-contained: no dependency on `scope.py` or `redaction.py`.
`_dedup_session` is the per-session step that `cost.py`'s report loop and `session_pricing.py` share:
it deduplicates one session's records and returns its subagent-spawn count, its sidechain
assistant-turn count, and whether any merged run's usage drifted during that call. `_rate_expired` is
the re-verify-by staleness rule both apply.

### `session_pricing.py`

Per-session, per-UTC-day pricing for importers outside this repository: `price_sessions_by_utc_day`,
its frozen result dataclasses `SessionSpend` and `SessionPricingResult`, and `SessionPricingError`.
Each field's meaning is in its dataclass's docstring. It reads each session with
`corpus._read_session_file_partitioned`, deduplicates it and collects its drift-canary inputs with
`pricing._dedup_session` (the step `cost.py`'s report loop also calls), checks usage with its own
strict token-field validator, and prices each turn with `pricing._price_turn`. It imports only
`corpus` and `pricing` from the package, by module (attribute access), plus the standard library.

**Import path.** Append `<config-dir>/scripts` to the end of `sys.path`, where `<config-dir>` is the
Claude Code config directory this repo is stowed into. Then run
`from transcript_analysis import session_pricing`. Do not insert the directory at the front: it also
holds top-level modules and packages such as `tests`, `_config` and `_config_dir`, which would then
shadow an importer's own modules of the same name. The module needs only the standard library and the
Python version `README.md` requires.

**Stability promise.** This is the only module in `transcript_analysis` with a stability promise.
Every other module, and every underscore-prefixed name anywhere in the package, may change without
notice. The promise covers:

- the names in `session_pricing.__all__`;
- the parameters of `price_sessions_by_utc_day`;
- every field of `SessionSpend` and `SessionPricingResult`, with its type and documented meaning;
- the `SessionPricingResult.format_drift_detected` property;
- `SessionPricingError`, its `session_index` attribute, and its index-only message;
- the error contract and the stderr contract below.

The promise covers reading named fields. Constructing these types, relying on field order, hashing a
result, mutating its day maps, and comparing or serializing whole results (for example with
`dataclasses.asdict` or `repr`) are outside it.

A new public name, a new result field, or a new keyword-only parameter with a default is an additive
change. Any other change to the list above is a breaking change, and so is removing this module. A
breaking change updates the pins in `tests/test_transcript_session_pricing.py` and states the break in
this section.

**Breaking change: `unpriced_models` now defaults to populated.** `price_sessions_by_utc_day`'s
keyword-only `include_unpriced_model_ids` parameter changed from default `False` to default `True`.
A caller that wants `unpriced_models` withheld must now pass `include_unpriced_model_ids=False`.
The `unpriced_model_count` field reports the count either way. This default diverges from `cost.py`'s
own tiering for the identical model-ID exposure risk, a deliberate tradeoff rather than an oversight.
See `cost.py`'s `_print_excluded_spend_banner` docstring for that tiering's own rationale.

**Error contract.** `price_sessions_by_utc_day` raises `ValueError` when `start` is after `end` or a
path cannot be passed to the operating system (it contains a NUL byte or does not encode in the
filesystem encoding), and `TypeError` for a wrongly typed argument, before it reads any session.
It raises `SessionPricingError` for the first session it cannot read, deduplicate, validate or price,
and reads no later session. An assistant turn with a non-empty usage object and a truthy non-string
timestamp fails its session this way. A malformed usage object fails its session even on a turn
outside `[start, end]`. A missing or unreadable main file is not an error: it comes back
as `main_file_readable=False`. A `SessionPricingError` does not say whether the session's content is
malformed, claude-config failed while processing it, or the environment failed. `__cause__` holds the original
exception for diagnosis; its type and text are outside the promise. Any other exception signals a
claude-config defect or a failing environment.

**Stderr contract.** `price_sessions_by_utc_day` writes nothing to stdout or stderr itself. The
`pricing.py` functions it calls may write four lines to stderr:

- a usage-drift warning starting `WARNING: requestId `, at most once per process;
- a merge notice starting `NOTICE: non-contiguous requestId run merged -- `, at most once per process;
- a merge notice starting `NOTICE: non-contiguous requestId run rejected -- `, at most once per process;
- a subagent-format warning starting `WARNING: subagent spawns detected in main thread`, at most once per call.

Each of the first three names one requestId, copied from the transcript with control characters
escaped and no limit on its length. Only these prefixes are stable; the rest of each line may change.
After a process has printed the usage-drift warning it prints it no more, but each call's
`usage_drift_detected` still reports that call's own sessions.

**What the output can disclose.**

- The fixed text of the four stderr lines names no transcript path, session ID, project slug, config
  directory, or model ID.
- The requestId in the first three lines, described in the stderr contract, is the one
  transcript-supplied substring on stderr, so treat those lines as carrying untrusted text.
- `str()` of a `SessionPricingError` names only `session_index`.
- `__cause__`, `__context__` and tracebacks are outside this guarantee and may carry a path or
  transcript content. Record only `session_index` and the exception class in output you share.
- `unpriced_models` is the only result field whose strings come from transcript content, and it is empty
  only when the call passed `include_unpriced_model_ids=False`.
  - Its entries are `message.model` values, verbatim, and are untrusted transcript text of any length.
  - Pass `include_unpriced_model_ids=False` when the caller has nowhere safe to put that text. A model
    ID can carry an account-identifying pre-announcement name when the account has early access to an
    unreleased model.
  - Also escape entries before rendering.
  - `unpriced_model_count` carries no transcript content and is always populated. Prefer it over
    `include_unpriced_model_ids=True` wherever only the count is needed.

**Trust in inputs and results.** `session_paths` are not confined to a root, and symlinks are followed.
`main_file_readable` and the results reveal whether a path is readable. A FIFO blocks the read, and an
endless device file such as `/dev/zero` grows memory without bound, which a timeout does not stop. Pass only
paths whose directories nobody else can write. The call also reads each path's `<stem>/subagents/*.jsonl`, so
checking `session_paths` alone is not enough. Every result derives from unauthenticated transcript text, and its integers are
unbounded.

### `render.py`

Small display-formatting and text-normalization helpers with no state of their own: model-family
labels (`_fam`), markdown/table rendering, `_content_text`, `_fmt_usd`, `_pct_of`,
`_strip_task_notifications`, `_is_main_thread_user_text`, `_is_task_notification`,
`_is_fresh_user_prompt`. Self-contained. `_is_fresh_user_prompt` has its one definition here, built
on the other two predicates; `judgment_pair.py`, `review_rounds.py`'s round-window detection, and
`period_cost.py`'s review windows read it. `rearm_backtest.py`'s boundary detection reads
`_is_main_thread_user_text`, which also accepts a task notification.

### `cost.py`

The cost command family: `cmd_cost`, `cmd_cost_trend`, and every helper used only by them —
corpus-wide dollar-cost reporting by token class/model ID/thread/account/project
(`_cost_report`), and per-ISO-week cost-trend accumulation (`compute_cost_trend_data`,
`_cost_trend_report`). The first command-group module in the package, not a leaf: it imports
`corpus`, `scope`, `redaction`, `pricing`, and `render` all by module (attribute access), matching
the cross-module discipline every other package module already follows. `compute_cost_trend_data`
is the one public (non-underscore-prefixed) name here, read by `cost_ledger.py`'s own
`_cost_ledger_report` as `cost.compute_cost_trend_data` — a normal cross-module package read, not
the shim back-import exception, which is fully closed (see
`test_transcript_analysis_cost_import_direction.py`).

### `reviewer_yield.py`

The reviewer-yield command family: `cmd_reviewer_yield` and every helper used only by it —
joining each main-thread reviewer-agent dispatch to its own subagent transcript, classifying its
verdict (findings-found/zero-finding/unclassified), and scoring cited-path edit overlap
(`compute_reviewer_yield_data`). Imports `corpus`, `pricing`, `render`, and `scope` all by module
(attribute access), matching `cost.py`'s convention. `compute_reviewer_yield_data` is the one
public name here, read by `cost_ledger.py`'s own `_cost_ledger_report` as
`reviewer_yield.compute_reviewer_yield_data`. `_is_reviewer_subagent_type` is read by `review_trace.py`, via
`reviewer_yield._is_reviewer_subagent_type` (attribute access) — no longer reached bare from the
shim, since review-trace's own detection moved into the package.

### `review_rounds.py`

The review-round-cost command family: `cmd_review_round_cost` and every helper used only by it —
per-branch review-round-window detection across three invocation shapes (the `Skill` tool_use, the
`/slash` tag, and an `Agent`/`Task` spawn of a `review-orchestrator`), and recursive per-round
subagent dollar attribution via `corpus._index_subagent_dispatches`' toolUseId join (`compute_review_round_costs`). Also exports `compute_review_round_counts`, a
count-only sibling reusing the same detection helpers to produce per-skill round counts with no
pricing, no dispatch index, and no recursion — `subagent_mix.py`'s `cmd_cost_counts` calls it as
`review_rounds.compute_review_round_counts` for the `### Review rounds` half of its output. Imports `corpus`, `pricing`, `redaction`, `render`,
and `scope` all by module (attribute access), matching `cost.py`'s convention — deliberately no
`cost.py` import: a round's own branch is its opening record's own `gitBranch`, carried forward
when absent and passed through `scope._attribute_carried_branch`, and every record inside that round's
window is attributed to it, never `scope._attributed_branch`'s sidechain timeline step, which a
main-thread round-opening record never needs. `REVIEW_SKILLS`, `compute_review_round_counts`, `detect_round_windows`, `spawn_round_skill`,
and `round_opened_by_spawn` are the public names here. `spawn_round_skill` maps a spawn block to the
review skill it opens (`author_outcome.py` is its consumer), and `round_opened_by_spawn` reports
whether a record's round came from a spawn block rather than a `Skill` block (the review-bench
miner's round filter is its consumer). `REVIEW_SKILLS` is read by `judgment_pair.py`, for
judgment-pair's `--skills` default, and by the shim's `build_parser()`, for review-round-cost's
`--skill` choices. `_round_skill_name` and `_SLASH_COMMAND_RE` are read by `review_trace.py`:
`_round_skill_name` for its `REVIEW_TRACE_SKILLS` membership test and `--skill` filter comparison, and
`_SLASH_COMMAND_RE` to extract a `/slash`-invoked skill name before `_round_skill_name` normalizes it.
`_SLASH_COMMAND_RE` is also `skill_invocation.py`'s `/slash` pattern, and this is its one definition.
`detect_round_windows` is public (no leading
underscore) for a separate reason: `author_outcome.py` is a second consumer, reading only each
window's own `open_idx`/`skill`.

### `author_outcome.py`

The author-outcome command family: `cmd_author_outcome` and every helper used only by it —
for each `--agent`-typed dispatch (default `code-writer`), joins it to the `code-review` round
that judged its diff (`compute_author_outcomes`), by completion-index ordering against
`review_rounds.detect_round_windows`' own `open_idx`, and classifies the outcome by reading that
session's own review-narrative-ledger rows directly. `_LedgerIndex` holds one config-dir root's
parsed ledger files, refreshed on every session lookup, and attributes each row to a session by
its `session_id` field or, for a row without one, by its filename. `_read_ledger_row_entries_for_session`
returns one session's rows sorted by `event_time`. See `docs/transcript-analysis.md`'s
author-outcome section ("Ledger lookup") for the attribution rules and the residual gaps they
still leave. `_round_blocks` groups a session's rows into blocks, and the k-th round-open takes
the k-th block by rank, not by `round` value. The transcript is still the sole source for
round-open positions, dispatch completion ordering, and the `marker.sh write code-review` Bash
`tool_use` fallback signal used only when a round has no ledger row at all
(`_is_clean_marker_write`). For a main-thread `review-orchestrator` round-open,
`_orchestrator_spawns` also projects that spawn's own transcript, when it has readable records,
into a `_OrchestratorSpawn` of derived facts (the marker write and the nested `--agent`
dispatches), located through
`corpus._index_subagent_dispatches`' `toolUseId` join.

See `_lib.sh`'s own `_lib_acquire_append_lock`/`_lib_append_json_line_locked`
docstrings for the append-lock mechanism `review-ledger.sh`'s
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
`render._content_text` for `hook_denial_key`'s current-shape decode. `turn_shape.py` reads
`_drop_denial_command_flag_values` as `denials._drop_denial_command_flag_values`. `friction_count.py` reads `hook_denial_key` as `denials.hook_denial_key`.

### `review_trace.py`

The review-trace command family: `cmd_review_trace` and every helper used only by it —
`--deny-summary`'s grouped denial/friction accumulation and report (`compute_deny_summary_data`,
promoted from a shim-private `_compute_deny_summary_data`), and the per-session event-timeline
detector both the default output and `--deny-summary` share (`_review_trace_session_events`).
Imports `corpus`, `denials`, `render`, `reviewer_yield`, `review_rounds`, `scope`, and
`skill_invocation` all by module (attribute access), matching `cost.py`'s convention; calls
`reviewer_yield._is_reviewer_subagent_type` for its own reviewer-spawn detection and `review_rounds._round_skill_name`/
`review_rounds._SLASH_COMMAND_RE` for its `/slash`-invocation skill matching — see the intro for the
cross-command-group import rule.
`_review_trace_session_events` reads `skill_invocation._normalize_skill_name` for the emitted display
label's directory-only strip.
`cmd_review_trace` and `REVIEW_TRACE_SKILLS` are the two names reached bare from the shim's own
still-monolithic `build_parser()` — see the exception noted above. `compute_deny_summary_data` is
read by `cost_ledger.py`'s own `_cost_ledger_report` as `review_trace.compute_deny_summary_data`, a
normal cross-module package read.

### `read_scope.py`

The read-scope command family: `cmd_read_scope` and every helper used only by it — the Read-call
census by cohort and scope (`_scan_read_scope_session`), repeat-whole-file-read detection, and
per-file-and-sessionId prompt-token growth. Imports `corpus`, `pricing`, `render`, and `scope` all
by module (attribute access), matching `cost.py`'s convention. `cmd_read_scope` is the one name
reached bare from the shim — see the exception noted above. `context_composition.py` reads
`_READ_SCOPE_CHARS_PER_TOKEN` as `read_scope._READ_SCOPE_CHARS_PER_TOKEN`.

### `ledger_common.py`

Recording primitives shared by the cost-ledger and pr-cost ledgers: the generated per-config-dir
machine identity (`_resolve_machine_identity`, `_machine_identity_path`), the git-tracked
destination check (`_ledger_path_is_git_tracked`), the machine-label format, the merge-conflict
markers both parsers refuse, and the local-lock timing both `--record` paths use. A leaf: no
dependency on any other package module. Binds `config_dir` by name from `_config_dir`, mirroring
`scope.py`'s own binding (see the attribute-access discipline noted under `scope.py` above) — every
package consumer (`cost_ledger.py`, `pr_cost_ledger.py`, `pr_cost.py`, `pr_cost_export.py`) reads
through this one binding. `cost_ledger.py` reads the other six names by attribute access too
(`ledger_common.<name>`), matching every other package module's cross-module discipline:
`_MACHINE_LABEL_RE`, `_COST_LEDGER_CONFLICT_MARKERS`, both lock constants,
`_ledger_path_is_git_tracked`, `_resolve_machine_identity`, and
`_warn_machine_identity_absent_from_ledger`.

### `gh_cli.py`

gh and git-remote access shared by pr-link, pr-cost, workstream-cost, and period-cost: origin
host/owner/repo parsing, gh stderr classification, rate-limit backoff, auth preflight,
effective-repo pinning, and merged/closed PR discovery. Imports `pr_cost_ledger` and `redaction` by
module — `pr_cost_ledger` for the two degraded-status constants `_gh_call_with_backoff` returns
(`_PR_COST_STATUS_DEGRADED_RATE_LIMIT`/`_PR_COST_STATUS_DEGRADED_NETWORK`), keeping the ledger's own
status enum a single source of truth rather than a duplicated pair of strings. `pr_link.py` reads five of its names by attribute access
(`gh_cli.<name>`): `_classify_gh_error`, `_GH_ERROR_KIND_NETWORK`,
`_git_remote_origin_host_and_owner_repo`, `_gh_host_qualified_repo`, and `_GH_CALL_TIMEOUT_S`.
`workstream_cost.py` reads `_git_remote_origin_host_and_owner_repo`, `_gh_auth_preflight_ok`,
`_resolve_pinned_gh_repo`, `_gh_discover_merged_prs`, and `_gh_discover_closed_unmerged_pr_branches`
by attribute access (`gh_cli.<name>`), matching every other package module's cross-module
discipline.

The period-cost command shares four pieces of this module:

- **`_gh_pr_list`** is the one bulk `gh pr list` call. It returns `(payload, status)` and aborts
  nothing: an empty status is success, and `_GH_PR_LIST_UNPARSEABLE` is gh answering with stdout that
  is not JSON. It takes `max_attempts` (period-cost passes 1, so a failing call never sleeps),
  `env`, which it forwards to the call, and `command_name`, which prefixes every message
  `_gh_call_with_backoff` prints. The merged and closed
  discovery helpers go through it and keep their own abort messages. Each attempt is bounded by
  `_GH_PR_LIST_TIMEOUT_S`, not the shorter `_GH_CALL_TIMEOUT_S` that every other gh call uses. The values are in `docs/transcript-analysis.md` § "period-cost".
- **`_is_plain_owner_repo`** is the Python copy of the owner/repo grammar in `_lib_parse_pr_identity`
  (`claude/.claude/hooks/_lib.sh`), with parity tests against the bash parser.
- **`_environ_without_gh_tokens`** returns a copy of `os.environ` without the four variables gh reads as
  tokens. A child that must not send an ambient gh token gets that environment. The call sites are in
  `docs/transcript-analysis.md` § "period-cost".
- **`_origin_identity_at`** resolves a recorded directory's `origin` where the cwd lookup resolves the
  process's own, so it lives in `gh_cli` beside that lookup. It returns `None` on any failure and prints
  nothing. See docs/transcript-analysis.md, "Repository resolution", for the git call and the vetting.

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

### `audit_routing.py`

The audit-routing command family: the three `cmd_*` functions (`cmd_audit_routing`,
`cmd_audit_routing_shape`, `cmd_audit_routing_samples`) and every helper only they use. Imports
`corpus`, `pricing`, `redaction`, `render`, and `scope` all by module (attribute access), matching
`cost.py`'s convention. The three `cmd_*` functions are the only names reached bare from the shim.

### `cost_ledger.py`

The cost-ledger command family: `cmd_cost_ledger` and its real implementation,
`_cost_ledger_report` — read (default) or append (`--record`) one row of the local per-week
cost/efficiency ledger. The sentinel opt-in check and the git-tracked-destination refusal both run
inline in `_cost_ledger_report`, before the write, per the governing decomposition plan's rule that
a ledger's guard checks stay in the same function as its write; the git-tracked check itself calls
the already-shared `ledger_common._ledger_path_is_git_tracked`. Imports `cost`, `ledger_common`,
`pricing`, `render`, `review_trace`, `reviewer_yield`, and `scope` all by module (attribute access),
matching `cost.py`'s convention. Binds `config_dir` by name from `_config_dir`, the same pattern
`pr_cost_ledger.py` uses. `cmd_cost_ledger` is the one name reached bare from the shim.

### `workstream_cost.py`

The workstream-cost command: `cmd_workstream_cost` and `_print_workstream_session_stats` —
per-branch session count and continuation "startup burn" dollars, via `cost._compute_workstream_dollars`
(the cost command group's own bare-caller-retained helper), plus an optional `--check-pr-status` gh
classification pass reusing pr-cost's own repo-pinning machinery. Imports `cost`, `gh_cli`, `render`,
and `scope` all by module (attribute access), matching `cost.py`'s convention. `cmd_workstream_cost`
is the one name reached bare from the shim.

### `subagents.py`

The subagents command: `cmd_subagents` and `_MCP_TOOL_BUCKET_LABEL` — per-branch `isSidechain` turn
counts by model family, plus tool-result text bytes per thread and per producing tool, with every
`mcp__<server>__<tool>` name collapsed into the one bucket label (an MCP server name is a per-account
integration identifier). Imports `corpus`, `pricing`, `redaction`, `render`, and `scope` all by
module (attribute access), matching `cost.py`'s convention. `cmd_subagents` is the one name reached
bare from the shim, which `build_parser()` wires up. `context_composition.py` reads
`_MCP_TOOL_BUCKET_LABEL` as `subagents._MCP_TOOL_BUCKET_LABEL`.

### `subagent_mix.py`

The subagent-mix and cost-counts commands: `cmd_subagent_mix` and `cmd_cost_counts`, plus every helper
only they use — `_dispatch_usage_summary` (one dispatch's modal observed model and priced dollar
totals), the `--this-repo` disclosure partition (`_partition_spawn_counts_by_disclosure`), and
`_UNKNOWN_SUBAGENT_TYPE`, which both commands' spawn counting reads so the two never disagree on the
fallback `subagent_type`. Imports `corpus`, `pricing`, `redaction`, `render`, `review_rounds`, and
`scope` all by module (attribute access), matching `cost.py`'s convention.
`cmd_cost_counts` reads its one root, `config_dir() / "projects"`, as `scope.config_dir()` by
attribute access, per the rule under `scope.py` above, so `fake_projects`' existing
`scope.config_dir` patch isolates it with no further patch. `cmd_subagent_mix` and `cmd_cost_counts`
are the two names reached bare from the shim, from `build_parser()`.

### `handoff_nudge.py`

The handoff-nudge family's shared core, with no `cmd_*` of its own: the mirror of the hook's fire
threshold (`_hook_effective_fire_threshold`, `_HANDOFF_NUDGE_ABS_CAP`, `_HANDOFF_NUDGE_PCT_THRESHOLD`),
the turn-index ramp curve (`_ramp_curve_turn_index_bucket`, `_ramp_curve_from_corpus`), the single
dedup-and-price pass over one session (`_extract_rearm_session_turns`) with its whole-session scope
filter (`_session_matches_rearm_scope`), and the `.handoff-nudge.log` reader, parser,
operator-response lag join, and diagnostic footer (`_read_bounded_log_lines`,
`_parse_nudge_log_entries`, `_operator_response_lag_from_log`, `_print_nudge_log_diagnostic`). Its
consumers are `rearm_backtest.py`, `spend_over_threshold.py`, `plan_boundary.py`, and
`handoff_signal_response.py`. Imports `corpus`, `pricing`, and `scope` all by module (attribute
access), matching `cost.py`'s convention. `_print_nudge_log_diagnostic` reads
`config_dir() / ".handoff-nudge.log"` as `scope.config_dir()` by attribute access, per the rule
under `scope.py` above, so `fake_projects`' existing `scope.config_dir` patch isolates it with no
further patch.

### `rearm_backtest.py`

The rearm-backtest command: `cmd_rearm_backtest` and `_rearm_backtest_report`, plus every helper only
they use — hook-observable boundary detection (`_hook_observable_boundaries`), per-spacing replay
(`_simulate_rearm_spacing`), `--spacings` parsing (`_parse_rearm_spacings_arg`), the
nudge-to-handoff conversion classifier (`_nudge_conversion_from_log`), and the per-root log-size
disclosure line (`_rearm_backtest_log_size_lines`). Imports `handoff_nudge`, `render`, and `scope` all
by module (attribute access), matching `cost.py`'s convention. The multi-root `--no-redact` refusal
stays inline in `_rearm_backtest_report`, ahead of any output, in addition to
`scope._resolve_cost_roots`' own CLI-level refusal. `cmd_rearm_backtest` is the one name reached bare
from the shim, from `build_parser()`.

### `spend_over_threshold.py`

The spend-over-threshold command: `cmd_spend_over_threshold` — each ISO week's share of session spend
earned at or above the hook's fire threshold, followed by the nudge-log diagnostic footer. Reads its
per-session turns through `handoff_nudge._extract_rearm_session_turns` and prints the footer through
`handoff_nudge._print_nudge_log_diagnostic`. Imports `corpus`, `handoff_nudge`, `render`, and `scope`
all by module (attribute access), matching `cost.py`'s convention. `cmd_spend_over_threshold` is the
one name reached bare from the shim, from `build_parser()`.

### `plan_boundary.py`

The plan-boundary command: `cmd_plan_boundary` and `_plan_boundary_report`, plus every helper only
they use — boundary-turn location (`_plan_boundary_turn_index`), the three arms' repricing
(`_arm_b_boundary_plus_one_dollars`, `_arm_b_later_turn_dollars`, `_arm_c_turn_dollars`), and the
pairwise work-inflation breakeven (`_plan_boundary_work_inflation_breakeven`). Imports
`handoff_nudge`, `pricing`, `render`, and `scope` all by module (attribute access), matching
`cost.py`'s convention. The multi-root `--no-redact` refusal is inline in `_plan_boundary_report`,
ahead of any output, in addition to `scope._resolve_cost_roots`' own CLI-level refusal.
`cmd_plan_boundary` is the one name reached bare from the shim, from `build_parser()`.

### `handoff_signal_response.py`

The handoff-signal-response command: `cmd_handoff_signal_response` and every helper only it uses — the
per-record signal classifiers (`_handoff_signal_bash_check_call`, `_handoff_signal_marker_transition`,
`_handoff_signal_is_handoff_event`), the excerpt and forward-context windows, the single-pass session
scan (`_handoff_signal_response_session_rows`), spend ranking, curation cards, the startup-burn
benchmark, and the aggregate and markdown renderers. Imports `corpus`, `cost`, `handoff_nudge`,
`pricing`, `redaction`, `render`, and `scope` all by module (attribute access), matching `cost.py`'s
convention. The multi-root `--no-redact` refusal lives inline in `cmd_handoff_signal_response`, since
`scope.resolve_scan_roots` refuses nothing. Its `.handoff-nudge.log` read goes through
`scope.config_dir()` by attribute access, per the rule under `scope.py` above, so `fake_projects`'
existing `scope.config_dir` patch isolates it. `cmd_handoff_signal_response` is the one name reached
bare from the shim, from `build_parser()`.

### `judgment_pair.py`

The judgment-pair command: `cmd_judgment_pair`, which pairs each review skill's last main-thread
assistant output with the user's next fresh prompt, and the `_add_judgment_pair_subparser`
registration. Imports `corpus`, `redaction`, `render`, `review_rounds`, and `scope` all by module
(attribute access), matching `cost.py`'s convention; `review_rounds.REVIEW_SKILLS` is the default
for its `--skills` flag. `_add_judgment_pair_subparser` is the one name `build_parser()` reaches
bare.

### `skill_invocation.py`

The skill-invocation command: `cmd_skill_invocation`, the per-skill tally of top-level, routed, and
`/slash` invocations, `_normalize_skill_name`, the display-label helper that strips a directory
qualifier from a skill name, and the `_add_skill_invocation_subparser` registration. Imports
`corpus`, `render`, `review_rounds`, and `scope` all by module (attribute access), matching
`cost.py`'s convention. `_normalize_skill_name` has its one definition here, and `review_trace.py`
reads it. The `/slash` pattern is `review_rounds._SLASH_COMMAND_RE`.
`_add_skill_invocation_subparser` is the one name `build_parser()` reaches bare.

### `skill_pair.py`

The skill-pair command: `cmd_skill_pair`, the ISO-week rate at which a follower skill fires in
sessions where a leader skill fired on the main thread, and the `_add_skill_pair_subparser`
registration. Imports `corpus`, `pricing`, and `scope` all by module (attribute access), matching
`cost.py`'s convention. `_add_skill_pair_subparser` is the one name `build_parser()` reaches bare.

### `period_cost.py`

The period-cost command: `cmd_period_cost` and every helper used only by it — the inclusive UTC
window refusals, the per-session stream assignment, the anchor directories, the review windows, the
repository resolver, the per-repository PR join, the Slack report renderer, and the stderr notices.
The stream assignment runs the PR-cost attribution and pricing primitives per record
(`pricing.dedup_turns_by_request_id`, `scope._attributed_branch`, `pricing._price_turn`) instead of
calling `cost._compute_pr_cost_branch_totals`, which takes no time window. Imports `corpus`, `cost`,
`gh_cli`, `pr_cost_ledger`, `pricing`, `render`, `review_rounds`, and `scope` all by module (attribute
access), matching `cost.py`'s convention; the `cost` import reaches only `_LIST_PRICE_CAVEAT` and
`_CORPUS_COVERAGE_WARNING_THRESHOLD_SECONDS`. It reads its one root, `scope.config_dir() / "projects"`,
as `scope.config_dir` by attribute access, per the rule under `scope.py` above, and registers no
`--config-dir` flag. Its subprocess calls are listed in
`docs/transcript-analysis.md` § "period-cost".
`cmd_period_cost` is the one name reached bare from the shim, from `build_parser()`.

### `buckets.py`

The buckets command: `cmd_buckets`, the per-branch count of main-thread assistant turns by model
family with project and session counts and the date range, and the `_add_buckets_subparser`
registration. Imports `corpus`, `redaction`, `render`, and `scope` all by module (attribute access),
matching `cost.py`'s convention. `_add_buckets_subparser` is the one name `build_parser()` reaches bare.

### `fail_seq.py`

The fail-seq command: `cmd_fail_seq`, each branch's ordered test-run failed-count sequence with its
failing rate and longest failing streak (`_longest_fail_streak`), and the `_add_fail_seq_subparser`
registration. Imports `render` and `scope` both by module (attribute access), matching `cost.py`'s
convention. It is the one home of `TEST_RUNNER_RE` and `FAILED_RE`, which `friction_count.py` reads as
`fail_seq.TEST_RUNNER_RE` and `fail_seq.FAILED_RE`. `_add_fail_seq_subparser` is the one name
`build_parser()` reaches bare.

### `struggle.py`

The struggle command: `cmd_struggle`, each branch's count of user turns matching `STRUGGLE_PHRASES` by
the preceding model family, and the `_add_struggle_subparser` registration. Imports `render` and
`scope` both by module (attribute access), matching `cost.py`'s convention. It is the one home of
`STRUGGLE_PHRASES`, which `user_input.py` and `friction_count.py` read as `struggle.STRUGGLE_PHRASES`.
`_add_struggle_subparser` is the one name `build_parser()` reaches bare.

### `user_input.py`

The user-input command: `cmd_user_input`, the per-session narrative of fresh user prompts classified as
initial, follow-up, or explicit correction, the prompt-classification helpers only it uses
(`_is_fresh_user_prompt_for_narrative`, `_is_unrecognized_user_list_record`, `_classify_prompt`,
`_attribute_model_to_prompt`, `_truncate_prompt_text`), and the `_add_user_input_subparser`
registration. Imports `corpus`, `redaction`, `render`, `scope`, and `struggle` all by module
(attribute access), matching `cost.py`'s convention; `_classify_prompt` reads
`struggle.STRUGGLE_PHRASES`. It scans `scope.PROJECTS_DIR` only, ignoring declared roots. Its
`--redact` map comes from `redaction._build_redact_map`'s default root. `_add_user_input_subparser`
is the one name `build_parser()` reaches bare.

### `duration.py`

The duration command: `cmd_duration`, each branch's span, active, and idle minutes and activity-burst
count, and the `_add_duration_subparser` registration. Imports `corpus` and `scope` both by module
(attribute access), matching `cost.py`'s convention. `_add_duration_subparser` is the one name
`build_parser()` reaches bare.

### `friction_count.py`

The friction-count command: `cmd_friction_count`, one transcript's composite of hook denials, failed
test runs, and struggle-phrase user turns, its `--checkpoint` incremental byte-offset scan
(`_read_friction_checkpoint`, `_write_friction_checkpoint`, `_consume_new_transcript_lines`), and the
`_add_friction_count_subparser` registration. Imports `denials`, `fail_seq`, `render`, and `struggle`
all by module (attribute access), matching `cost.py`'s convention: it reads `denials.hook_denial_key`,
`fail_seq.TEST_RUNNER_RE`, `fail_seq.FAILED_RE`, and `struggle.STRUGGLE_PHRASES`. It reads no scope,
since it takes one transcript path rather than a corpus. `nudge-error-mode-analysis.sh` runs it
through the shim with `--checkpoint` and reads a bare integer from stdout.
`_add_friction_count_subparser` is the one name `build_parser()` reaches bare.

### `turn_shape.py`

The turn-shape commands: `cmd_turn_shape`, the per-turn tool-call-count and single-call streak-length
distributions weighted by priced dollars; `cmd_turn_shape_samples` and `cmd_turn_shape_holdout_samples`,
random samples of flagged and unflagged streaks; the mutating-git classifier the delegation-rule streak
excludes (`_bash_command_is_mutating_git`); and the `_add_turn_shape_subparser`,
`_add_turn_shape_samples_subparser`, and `_add_turn_shape_holdout_samples_subparser` registrations. The
three commands share `_turn_shape_session_turns` and `_turn_shape_streaks`. Imports `corpus`, `denials`,
`pricing`, `render`, and `scope` all by module (attribute access), matching `cost.py`'s convention: the
classifier reads `corpus.split_command_segments` and `denials._drop_denial_command_flag_values`. The three
registrars are the only names `build_parser()` reaches bare.

### `commit_gate.py`

The commit-gate command: `cmd_commit_gate`, each ISO week's main-thread git commits with and without a
prior invocation of a named skill, optionally split by permissionMode, and the
`_add_commit_gate_subparser` registration. Imports `corpus` and `scope` both by module (attribute
access), matching `cost.py`'s convention. `_add_commit_gate_subparser` is the one name `build_parser()`
reaches bare.

### `pr_link.py`

The pr-link command: `cmd_pr_link`, each named branch's GitHub PR number, main-thread model split, and
per-PR comment counts from gh, with its two failure-reporting helpers (`_pr_link_gh_failure_kind`,
`_pr_link_report_gh_failure`) and the `_add_pr_link_subparser` registration. Imports `gh_cli`, `render`,
and `scope` all by module (attribute access), matching `cost.py`'s convention. `_add_pr_link_subparser`
is the one name `build_parser()` reaches bare.

### `sessions.py`

The sessions command: `cmd_sessions`, the resolved scope's transcript paths one per line, plus split
subagent files under `--include-subagents`, and the `_add_sessions_subparser` registration. Imports
`corpus` and `scope` both by module (attribute access), matching `cost.py`'s convention: it reads
`corpus.SUBAGENT_SUBDIR`. `_add_sessions_subparser` is the one name `build_parser()` reaches bare.

### `context_distribution.py`

The context-distribution command: `cmd_context_distribution`, each session's peak main-thread
context-at-turn bucketed at candidate percentage and absolute-token thresholds with each threshold's
session and dollar shares, and the `_add_context_distribution_subparser` registration. Imports `corpus`,
`pricing`, `render`, and `scope` all by module (attribute access), matching `cost.py`'s convention. The
threshold tuples and `_session_peak_context` live in `pricing.py`, and `_context_distribution_rows` lives
in `render.py`. `_add_context_distribution_subparser` is the one name `build_parser()` reaches bare.

### `context_composition.py`

The context-composition command: `cmd_context_composition`, rate-weighted token-turns by content-item
category over each context sequence, gated by the static-prefix reconciliation residual's per-sequence
instability, and the `_add_context_composition_subparser` registration. Imports `corpus`, `pricing`,
`read_scope`, `render`, `scope`, and `subagents` all by module (attribute access), matching `cost.py`'s
convention. It makes two cross-command reads, `read_scope._READ_SCOPE_CHARS_PER_TOKEN` and
`subagents._MCP_TOOL_BUCKET_LABEL`. `_add_context_composition_subparser` is the one name `build_parser()`
reaches bare.

### `cache_efficiency.py`

The cache-efficiency command: `cmd_cache_efficiency`, the per-thread cold-cache read-collapse census of
cache read and write tokens, with a per-account breakdown above one root, and the
`_add_cache_efficiency_subparser` registration. Imports `corpus`, `pricing`, `render`, and `scope` all by
module (attribute access), matching `cost.py`'s convention. `_add_cache_efficiency_subparser` is the one
name `build_parser()` reaches bare.

### `edit_format.py`

The edit-format command: `cmd_edit_format`, the Edit/Write/MultiEdit call census with per-tool failure
classification, governance-hook re-bucketing, not_found cause attribution, and
old_string/new_string/Write token overhead, with a per-account breakdown above one root, and the
`_add_edit_format_subparser` registration. Imports `pricing`, `render`, and `scope` all by module
(attribute access), matching `cost.py`'s convention. `_add_edit_format_subparser` is the one name
`build_parser()` reaches bare.

### `instrument_authoring.py`

The instrument-authoring command: `cmd_instrument_authoring`, the aggregate-only census of Bash heredoc
and inline-program calls and Write-to-scratchpad calls, size-bucketed by main-thread and subagent scope
and split into zero-dispatch and dispatched session cohorts, and the `_add_instrument_authoring_subparser`
registration. Imports `pricing`, `render`, and `scope` all by module (attribute access), matching
`cost.py`'s convention. `_add_instrument_authoring_subparser` is the one name `build_parser()` reaches
bare.

## Sibling scripts

`token-analyzer.py` and `analyze-context.py` import these modules directly
(`from transcript_analysis.corpus import read_session_file`, `from transcript_analysis import
scope`, etc.) instead of loading the entire `transcript-analysis.py` CLI via
`importlib.util.spec_from_file_location` to reach a handful of helpers. Both also import
`render.py`'s `_fam`/`_content_text` rather than maintaining their own copies.

## Tests

Each of `corpus.py`, `scope.py`, `redaction.py`, `pricing.py`, and `render.py` is exercised through
`transcript-analysis.py`'s existing test suite (`tests/test_transcript_analysis.py`), which calls into
the shim. `pricing.py`'s shared per-session step, staleness rule, and canary return values, and
`cost.py`'s accumulation of that step's counts, also have direct tests in
`tests/test_transcript_pricing_session_helpers.py`. `session_pricing.py`'s tests, including the pins
its stability promise relies on, live in `tests/test_transcript_session_pricing.py`. Every other package module has its own per-command-group (or, for `denials.py`,
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
`_argv_carries_repo_pin`, `_sample_pr_cost_row`, and `_legacy_row_line`. A test file that reads
only package-resident names imports those modules directly, unaliased, as `from
transcript_analysis import <module>`, and reads `<module>.<name>`. A test file that reads a name
only the shim has — `build_parser` or `main` — loads
its own independent copy of `transcript-analysis.py` via `spec_from_file_location`, the same
boilerplate `test_transcript_analysis.py` uses. It reaches package modules as
`_mod.<module>.<name>` (e.g. `_mod.cost._cost_report`, `_mod.redaction._REDACT_MAP_MISS_TOKEN`)
— the same channel the shim-reimport exception above relies on. `tests/conftest.py` carries the
shared fixtures that reach across the shim/package
boundary and across every test file (`fake_projects`, `fake_config_dir_factory`, `_table_cols`,
`cost_ledger_file`, `cost_ledger_enabled`, `_hook_deny`, `_hook_deny_current`, `_review_trace_args`,
`_compact_boundary_rec`, `_cost_ledger_args`, `_cost_ledger_row`, `_two_declared_roots`,
`_attr_ts`, `_exit_plan_mode`, `_priced_opus`, `_priced_sidechain_asst`, `_read_use`, `_skill_use_rec`, `_thinking_block`); see its own
docstrings for why `fake_projects` patches four `config_dir` bindings: `scope.config_dir`, plus
`cost_ledger.config_dir`, `ledger_common.config_dir`, and
`pr_cost_ledger.config_dir` (each module's own by-name binding, mirroring `scope.py`'s pattern). `author_outcome.py`'s own tests
live in `tests/test_author_outcome.py`: most exercise the package module directly
(`from transcript_analysis import author_outcome`), with a small `spec_from_file_location`-loaded
shim copy reserved for the argparse-wiring and `cmd_author_outcome` end-to-end tests.

`fake_projects`, `cost_ledger_file`, and `cost_ledger_enabled` patch the package modules directly, so a
test file that uses them needs no `transcript-analysis.py` loader of its own.
`tests/test_transcript_shared_fixtures.py` requests them from a file that loads no shim.

`tests/_move_fidelity.py` checks a code move between files: moved-node AST equality modulo a rename
map applied to code only, what remains of each source file, byte-compared CLI captures under a pinned
environment, a per-subcommand dump of each parser's argparse configuration, and per-test outcomes
across junit XML runs. Its seeded-defect self-test is split by subject:
`tests/test_transcript_move_fidelity_test_ids.py` covers `test-ids`,
`tests/test_transcript_move_fidelity_parser.py` covers the parser dump and
`compare-captures --moved-callable`, and `tests/test_transcript_move_fidelity.py` covers the rest.
Run `tests/_move_fidelity.py` with `--help` for usage.
`tests/_move_fidelity_helpers.py`, a plain module, not a test file itself, holds the helpers
the self-test files share: `_invoke` and `_checks` (run the harness and read its output) and `_write`
and `_write_capture` (write input files and capture directories). The test-ids file imports only
`_invoke` and `_checks`. A new self-test file imports these helpers instead of defining its own
copies.

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
cache-rebuild and cache-efficiency.
`.claude/plans/transcript-analysis-decomposition.md`'s rule for a cross-group test (stated there for
`_UNCONDITIONAL_HEADER_CASES`) keeps such a test in the legacy file at least until every group it
references has moved.

The audit-routing family splits along its three subcommands rather than one file per module:
`tests/test_transcript_audit_routing.py` (`cmd_audit_routing`),
`test_transcript_audit_routing_shape.py` (`cmd_audit_routing_shape`), and
`test_transcript_audit_routing_samples.py` (`cmd_audit_routing_samples`). All three share
`tests/_audit_routing_helpers.py`, a plain module, not a test file itself. Its own consumers
import it as `from ._audit_routing_helpers import ...` — see
`.claude/rules/test-tree-packaging.md` for why. Each file keeps its own family-only helpers
local to itself. `TestMultiRootFormatOutliers` stays in `tests/test_transcript_analysis.py`
rather than moving with the rest of the family: it spans cost, audit-routing-samples, and
judgment-pair.

The cost-ledger command family splits at the `--record` refusal-gate seam rather than one file per
module: `tests/test_transcript_cost_ledger.py` covers path resolution, read mode, serialization,
parser hostility, the reviewer-gap floor, record parity, write fidelity, auto-create, idempotence,
degenerate corpora, concurrency, and CLI wiring; `tests/test_transcript_cost_ledger_record_gates.py`
covers every path where `--record` must refuse and write nothing, plus each gate's boundary success
cases (`TestCostLedgerPublishSafety`, `TestCostLedgerSentinelGate`). `workstream_cost.py`'s own
tests live in `tests/test_transcript_workstream_cost.py`, alongside `cost.py`'s
`_compute_workstream_dollars` coverage. `tests/test_transcript_package_module_prefixes.py` pins the
by-module import discipline of every module in its `PRODUCTION_MODULES` tuple. For every file in
its `TEST_FILES` tuple, it checks both the `<module>.<name>` and `_mod.<module>.<name>` read
shapes. It requires at least one such read per file. It holds the file to an unaliased
whole-module import. It also fails any test file that imports from `transcript_analysis` without
loading the shim and is missing from `TEST_FILES` — a permanent check rather than a one-time
migration-time script.

The subagent command family splits at command seams rather than one file per module:

- `tests/test_transcript_subagents.py` covers `cmd_subagents`, plus the declared-roots multi-root class
  for both `cmd_subagents` and `cmd_subagent_mix`
- `tests/test_transcript_subagent_mix.py` covers `cmd_subagent_mix`'s spawn table, model-mix columns,
  declared-pin path safety, `--since`, and multi-root disclosure
- `tests/test_transcript_subagent_mix_dollars.py` covers the `Actual $` and `Counterfactual $` columns
  and `_dispatch_usage_summary`'s dedup-before-pricing
- `tests/test_transcript_cost_counts.py` covers `cmd_cost_counts`'s refusals, rendering, nested-spawn counting and
  attribution, disclosure allowlist, and the agent-type charset pin

The first three share `tests/_subagent_helpers.py`, a plain module, not a test file itself.
`tests/test_transcript_analysis.py` imports it too. All four consumers import it relatively, as
`from ._subagent_helpers import ...` — see `.claude/rules/test-tree-packaging.md` for why. It holds
only helpers used by more than one file, or by `tests/test_transcript_analysis.py` as well:
`_sum_column_across_rows`, `_subagent_mix_args`, and `_subagents_args`. Each file keeps its other
family-only helpers local to itself.
`TestFormatDriftCanary` stays in `tests/test_transcript_analysis.py` rather than moving with the
family: it spans subagents, skill-pair, and cache-efficiency. `TestSubagentFormatContract` stays
there too: it pins `corpus.py`'s on-disk subagent-file contract and reads no name from the family.

The handoff-nudge command family splits at module seams, and handoff-signal-response's tests split
again at its detection/reporting seam:

- `tests/test_transcript_handoff_nudge.py` covers `handoff_nudge.py`: fire threshold, ramp curve,
  per-session turn extraction and scope, and nudge-log parsing, including a contract test against
  the real hook
- `tests/test_transcript_rearm_backtest.py` covers `rearm_backtest.py`'s boundary detection, spacing
  replay, `--spacings` parsing, and report
- `tests/test_transcript_rearm_backtest_nudge_log.py` covers its nudge-to-handoff conversion
  classifier and per-root log-size disclosure line
- `tests/test_transcript_spend_over_threshold.py` covers `cmd_spend_over_threshold`, including its
  diagnostic footer
- `tests/test_transcript_plan_boundary.py` covers `plan_boundary.py`: boundary-turn location, the
  three arms' repricing helpers, the work-inflation breakeven, and the report end to end
- `tests/test_transcript_handoff_signal_response_detection.py` covers `handoff_signal_response.py`'s
  per-record signal classifiers, excerpt and forward-context windows, and single-pass session-row
  detection
- `tests/test_transcript_handoff_signal_response.py` covers its reporting side: spend ranking,
  curation cards, the startup-burn benchmark, the renderers, and `cmd_handoff_signal_response` end
  to end
- `tests/test_transcript_period_cost.py` covers `period_cost.py`: the window refusals, review windows,
  anchor directories, repository resolution, PR join, report rendering, notices, and `cmd_period_cost`
  end to end against a fake `subprocess.run`. Its golden test compares `--sample` to the sample in
  `docs/transcript-analysis.md`.

`tests/_handoff_nudge_helpers.py`, a plain module, not a test file itself, holds the six helpers more
than one file reads: `_spend_over_threshold_args`, `_rearm_backtest_args`,
`_ramp_curve_from_records`, `_check_result_json`, `_handoff_advisory_attachment`, and
`_check_call_turn`. `tests/test_transcript_analysis.py` imports only `_spend_over_threshold_args` and
`_rearm_backtest_args`, for its cross-subcommand table. All consumers import it relatively, as `from ._handoff_nudge_helpers import ...`
— see `.claude/rules/test-tree-packaging.md` for why. Each file keeps its other family-only helpers
local to itself.

The skill command group splits at command seams, one test file per module:
`tests/test_transcript_skill_invocation.py` covers `cmd_skill_invocation` (invocation buckets,
`--branches` and `--include-subagents` scoping, name normalization, provenance, the repo-scoped
default, and the `/slash` `<command-args>` exclusion), `tests/test_transcript_skill_pair.py` covers
`cmd_skill_pair`, and `tests/test_transcript_judgment_pair.py` covers `cmd_judgment_pair`. None of the
three loads the shim: each imports its package module directly and relies on the loader-free
`fake_projects` fixture. `tests/_skill_helpers.py`, a plain module, not a test file itself, holds the
helpers more than one of those files, or `tests/test_transcript_analysis.py`, reads. The legacy file
imports it too, as `from ._skill_helpers import ...` — see `.claude/rules/test-tree-packaging.md` for
why. `TestIsFreshUserPrompt` stays in `tests/test_transcript_analysis.py` because it tests
`render.py`, not the judgment-pair command.

The session-signal command group splits at command seams, one test file per module:
`tests/test_transcript_buckets.py`, `tests/test_transcript_fail_seq.py`,
`tests/test_transcript_struggle.py`, `tests/test_transcript_user_input.py`,
`tests/test_transcript_duration.py`, and `tests/test_transcript_friction_count.py`. None of the six
loads the shim: each imports its package module directly and relies on the loader-free
`fake_projects` fixture. `TestInlineSidechainRecordsFollowTheDispatchingBranchInBucketsAndDuration`
covers buckets and duration from `tests/test_transcript_buckets.py`. `tests/_session_signal_helpers.py`,
a plain module, not a test file itself, holds the helpers more than one of those files, or
`tests/test_transcript_analysis.py`, reads. The legacy file imports it too, as
`from ._session_signal_helpers import ...` — see `.claude/rules/test-tree-packaging.md` for why.
`TestPerRecordConsumersCreditPrefixToTheBranchEntered` stays in `tests/test_transcript_analysis.py`
because it also spans judgment-pair and pr-link.

The turn-shape, commit-gate, and pr-link commands each have one test file:
`tests/test_transcript_turn_shape.py`, `tests/test_transcript_commit_gate.py`, and
`tests/test_transcript_pr_link.py`. None of the three loads the shim: each imports its package modules
directly and relies on the loader-free `fake_projects` fixture. `tests/_commit_gate_helpers.py`, a plain
module, not a test file itself, holds `_gate_args`, which the commit-gate file and
`tests/test_transcript_analysis.py`'s cross-subcommand header table both read. The legacy file imports it
too, as `from ._commit_gate_helpers import ...` — see `.claude/rules/test-tree-packaging.md` for why.
`sessions.py` has no test file of its own. The cross-subcommand header table covers its header.
`tests/test_transcript_cli_bootstrap.py` covers its dispatch and the `--paths --include-subagents`
emission. No test covers the `--paths` refusal. The pr-link method of
`TestPerRecordConsumersCreditPrefixToTheBranchEntered` stays with that class (see the paragraph above).

The context-distribution, context-composition, and cache-efficiency commands each have one test file:
`tests/test_transcript_context_distribution.py`, `tests/test_transcript_context_composition.py`, and
`tests/test_transcript_cache_efficiency.py`. None of the three loads the shim: each imports its package
modules directly. `tests/_cache_efficiency_helpers.py`, a plain module, not a test file itself, holds
`_cache_efficiency_args`, which the cache-efficiency file and `tests/test_transcript_analysis.py` both
read. The legacy file imports it too, as `from ._cache_efficiency_helpers import ...` — see
`.claude/rules/test-tree-packaging.md` for why. `TestCacheEfficiencyArgparseWiring` and
`TestConfigDirRouting` stay in `tests/test_transcript_analysis.py` because they read `build_parser` and
`main`, which only the shim has.

The edit-format and instrument-authoring commands each have one test file:
`tests/test_transcript_edit_format.py` and `tests/test_transcript_instrument_authoring.py`. Neither
loads the shim: each imports its package modules directly. `_UNCONDITIONAL_HEADER_CASES` in
`tests/test_transcript_analysis.py` reads `cmd_edit_format` and `cmd_instrument_authoring` through
the shim.
