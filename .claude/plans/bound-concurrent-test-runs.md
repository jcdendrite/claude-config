# GH-978 — Bound concurrent test runs

## Context

Bound how much CPU each `select-tests.py` invocation claims when other
invocations are already running, so concurrent agent-driven test runs on
one machine stop oversubscribing its cores. This is the next item in
GH-978, after hook-chain consolidation (PR #985) and injectable timeout
caps (PR #1014); a measurement of this repo's own test suite found 8
concurrent copies of one 116-test file each taking 35.6s versus 16.2s
running alone (2.2x). CPU was 0% idle with 39% of busy time in the kernel
— the signature of scheduler contention, not real test work. After this
unit, each
`select-tests.py` invocation independently sizes its own pytest-xdist
worker count from the machine's current load average (no cross-process
coordination), and every selection outcome is optionally logged so
fallback-to-full-suite frequency becomes measurable instead of a stderr
line that scrolls away.

A cross-process semaphore (the epic's original proposal) is rejected for
the same stale-lock risk `.claude/plans/scope-test-worker-count.md`
(from issue #758) already found; this unit uses load-average-informed
per-process sizing instead, with no cross-process coordination.

A related idea surfaced in the same conversation — decomposing
`claude/.claude/tests/helpers.py` and/or `conftest.py` files to reduce
how often selection falls back to `FULL_SUITE_TARGETS` in the first
place — is explicitly out of scope for this unit and has been filed
separately as issue #1015.

## Approach

Each `select-tests.py` invocation sizes its own pytest-xdist worker count
from the machine's current 1-minute load average and injects that number
into the pytest subprocess via `PYTEST_XDIST_AUTO_NUM_WORKERS`, so a run
that starts on an already-busy machine takes only the idle headroom
instead of a full machine's worth. Nothing is shared between invocations
— no lock, no counter, no persistent state. The two mechanisms are fully
decoupled: logging happens independently of the sizing computation, with
no shared state between them. Every selection outcome — reason code,
full-suite fallback flag, and (when a sizing computation ran) worker
count and load average — is appended as one JSON line to a per-machine
log under the resolved config directory. Logging is gated by a new
off-by-default config key, so fallback frequency becomes a countable
number rather than a stderr line that scrolls away.

**The formula.** With `cpu_budget` = the worker count xdist's own `-n
auto` would have chosen here, and `load_one_minute` = `os.getloadavg()[0]`:

```
workers = max(min(_MIN_LOAD_AWARE_WORKERS, cpu_budget),
              min(cpu_budget, round(cpu_budget - load_one_minute)))
```

`round` keeps a near-idle machine at exactly today's worker count; the
two `min` clamps make "never more than `-n auto` would pick" and "never
below the 2-worker floor" both invariants rather than emergent
properties.

**Alternatives set aside.** A cross-process counting semaphore is
rejected (row3) and not revisited here. Two *lighter* primitives were
checked against mechanism M1 before adopting it:

- `--maxprocesses` (`xdist/plugin.py:83-91`): a static ceiling with no
  reading of current load; also collides with `build_pytest_argv`'s
  contract that only the caller's own arguments follow the resolved
  targets.
- A fixed `-n <N>` in `pyproject.toml`'s `addopts`: already rejected by
  `.claude/plans/scope-test-worker-count.md` for penalizing every
  single-session run and CI to serve one machine's concurrency pattern.

Heavier options — `psutil` for a physical-core count, a lock file, a
coordinating daemon — buy nothing the stdlib two-call reading does not,
and each adds a failure mode (a new dependency, a stale lock, a process
to supervise) that this design has none of.

**Root problem:** concurrent `select-tests.py` invocations each
independently request a full machine's worth of xdist workers, because
`pyproject.toml:24`'s `-n auto` is resolved per-process against the
machine's core count with no awareness of sibling runs. See Context's
measurement above. **Second root problem (root2):** the four full-suite
fallback reasons are printed to stderr and then lost, so "how often does
selection fall back, and to which trigger" cannot be answered without
re-instrumenting by hand.

**Givens** (conditions this design treats as fixed because they lie
beyond its reach):

- **row1** — pytest-xdist owns how `-n auto` becomes a number and the
  order of its detection providers. Vendor-imposed: the chain lives in
  xdist's own `plugin.py`; this repo can feed it, not replace it.
  `[verified: .venv/lib/python3.12/site-packages/xdist/plugin.py:16-53;
  requirements-dev.txt pins pytest-xdist==3.*]`
- **row2** — `os.getloadavg()` is the only load signal in the Python
  standard library. It returns exponentially-smoothed 1/5/15-minute
  averages; no portable instantaneous run-queue reading exists.
  Platform/vendor-imposed: the kernel exposes the smoothed figures. The
  one instantaneous alternative, `/proc/loadavg`'s fourth field, is
  Linux-only, and this repo supports Linux, macOS, and WSL2. `[verified:
  Python `os` module docs]`
- **row3** — no cross-process coordination mechanism (semaphore, lock
  file, shared counter, any persistent inter-invocation state) is
  available to this design. `[engineer-verified]` — rejected on the same
  stale-state grounds `.claude/plans/scope-test-worker-count.md` §
  "Out of scope" already recorded ("introduces shared state and a
  stale-lock failure mode").
- **row4** — how many test runs will be in flight at once, and when each
  arrives, is a per-machine, per-moment fact no single invocation can
  observe. Dissolving the design's dependence on it needs exactly the
  cross-process mechanism row3 closed off.

**Mechanisms** (each anchored to the root problem or a row):

- **M1 — Compute the worker count from `os.getloadavg()[0]` against an
  xdist-mirrored CPU budget, and inject it as
  `PYTEST_XDIST_AUTO_NUM_WORKERS` into the pytest subprocess at
  `run_pytest`'s single launch point.** This is the one override xdist
  honors without a code change on its side, it is read per-process so two
  concurrent runs never interfere, and `run_pytest`
  (`select-tests.py:696-699`) is the sole place a pytest process is
  created. *anchors: root, row1, row3, row12*
- **M2 — Defer entirely when `PYTEST_XDIST_AUTO_NUM_WORKERS` is already
  set to a non-empty value in the inherited environment.** An operator
  who exported a value has already answered this question (README.md:527
  documents the override as supported guidance). The emptiness test
  mirrors xdist's own `if env_var:`, so "set but empty" resolves
  identically in both places. *anchors: row1, row6, row19*
- **M3 — On `OSError` from `os.getloadavg()`, set nothing and launch
  pytest with an unmodified environment.** Today's behavior is the
  correct degradation target: xdist then resolves `-n auto` exactly as it
  does now. A load reading that cannot be taken is not a reason to fail a
  test run. *anchors: row2, row11*
- **M4 — Floor the result at `min(2, cpu_budget)`.** Below two workers
  there is no parallelism left to buy, only xdist's per-worker spawn and
  IPC cost, and a saturated machine is exactly where a run is already
  slowest. *anchors: root, row7, row8*
- **M5 — Append one JSON line per invocation — reason, full-suite flag,
  triggering paths, resolved-target count — to
  `<config-dir>/.test-selection-log.jsonl`, gated by a new
  `test_selection_tracking` config key defaulting to false.** Logging
  every outcome, not just fallbacks, makes the frequency question
  answerable from one file. A config key is used instead of an env var
  because agent-launched runs — the population this measures — inherit
  `claude`'s launch-time environment (README.md:530), so a shell export
  set afterward never reaches them. A config-dir file resolves
  identically regardless of how the process started. *anchors: root2,
  row15, row17, row19*
- **M6 — The log append is best-effort: `OSError` is swallowed with one
  stderr warning and the run proceeds.** `select-tests.py` is the
  test-selection entrypoint for every agent and for `/ready-for-review`;
  a full disk or a read-only config dir must not turn into a failed test
  run. *anchors: root2*

**Assumptions:**

- **row5** — an explicit `-n <N>` on the command line makes xdist ignore
  `PYTEST_XDIST_AUTO_NUM_WORKERS` entirely, so M1 needs no special-casing
  to respect a caller's own override. `[verified: xdist/plugin.py:310-316
  calls `pytest_xdist_auto_num_workers` only when
  `config.option.numprocesses in ("auto", "logical")`;
  `parse_numprocesses` at :56-60 turns any other `-n` value into an
  `int`, which never matches that branch. `-n 0` likewise short-circuits
  to a serial run at :326-328.]`
- **row6** — xdist treats an empty-string `PYTEST_XDIST_AUTO_NUM_WORKERS`
  as unset. `[verified: xdist/plugin.py:17-18, `if env_var:`]`
- **row7** — at `numprocesses == 1`, xdist still spawns one `popen` worker
  subprocess and routes every test through it. `[verified:
  xdist/plugin.py:318-324, `config.option.tx = ["popen"] * numprocesses`]`
- **row8** — that single-worker mode is therefore slower in wall-clock
  terms than a serial `-n 0` run, which is why the floor is 2 rather than
  1. `[unverified]` — inferred from row7, not measured; the floor lives
  in a named module-level constant so it is a one-line change if field
  data contradicts it.
- **row9** — the proportional formula itself is a derived choice, not a
  cited one. GNU Make's `-l` is the precedent for *conditioning
  parallelism on existing load*, and it specifies a binary
  start/don't-start gate, not a proportional worker count; no primary
  source specifies the subtraction, the `round`, or the floor.
  `[unverified — derived]`
- **row10** — GNU Make's `-l`/`--load-average` establishes the
  gate-on-existing-load precedent: "no new jobs should be started if… the
  load average is at least *load*." `[verified: `make.1` man page]`
- **row11** — `os.getloadavg()` returns the 1/5/15-minute run-queue
  averages. It raises `OSError` when the load average is unobtainable.
  It is Unix-only, consistent with this repo's documented
  Linux/macOS/WSL2 support, so no Windows branch is needed. `[verified:
  Python `os` module docs]`
- **row12** — with `psutil` absent, xdist's `-n auto` resolves to
  `len(os.sched_getaffinity(0))` where that import succeeds and
  `os.cpu_count()` otherwise, both falling back to 1. `_cpu_budget()`
  must mirror that non-psutil branch, using the same `from os import
  sched_getaffinity` inside `try/except ImportError` idiom. A bare
  `os.sched_getaffinity(...)` raises `AttributeError`, not `ImportError`,
  on macOS, which is why the import-based idiom is required. `[verified:
  xdist/plugin.py:26-53; requirements-dev.txt declares only pytest,
  pytest-xdist, ruff, pyyaml, shellcheck-py]`
- **row13** — CI never invokes `select-tests.py`; both passes call
  `pytest` directly, so M1 through M4 cannot reach CI at all. `[verified:
  .github/workflows/tests.yml:160 and :166]`
- **row14** — the mechanism's mitigation strength scales with
  arrival-closeness to `os.getloadavg()`'s 1-minute decay constant, not
  with simultaneity per se:
  - A fully simultaneous burst gets none of it: every member reads the
    same pre-burst load average and sizes itself as if alone.
  - Arrivals within roughly that time constant of each other get partial,
    not full, mitigation, since the earlier invocation's added load has
    only partially decayed out of the later invocation's read.
  - The Context's 8-concurrent measurement (35.6s per run) is the
    simultaneous-arrival shape; the arrival pattern this unit actually
    targets — independent agent sessions launching runs at unrelated
    moments — can still land inside that partial-mitigation window rather
    than fully outside it.

  `[unverified]` — derived from row2's smoothing window against a run
  lifetime of tens of seconds; no post-implementation A/B has been run.
  Bounding either shape fully requires reservation-at-start, which is
  row3's closed door.
- **row15** — under stow directory-fold, `~/.claude/...` writes land
  physically inside this repository, so any new config-dir file needs its
  own `.gitignore` entry or it shows up in `git status` for every stow
  consumer. `[verified: .gitignore:93-109 comment block and :136's
  existing `claude/.claude/.permission-prompt-log.jsonl` entry]`
- **row16** — adding a row to `config-keys.psv` changes a count asserted
  in four places, all mechanically enforced: `install.sh`'s "so one
  function reports all N", `migrate-legacy-config.sh`'s "non-interactive
  import for all N keys", and two separate claims in
  `docs/config-file.md`. `[verified:
  claude/.claude/hooks/tests/test_doc_counts.py:508-533]`
- **row17** — `select-tests.py` can import `config_dir()` and
  `config_enabled()` as bare sibling modules: both live in
  `claude/.claude/scripts/`, which is `sys.path[0]` when the script is
  run by path and is also on `pyproject.toml:18`'s `pythonpath` for the
  test suite. `[verified: _config.py:23 does exactly that bare `from
  _config_dir import config_dir`; pyproject.toml:18]`
- **row18** — a config key with no legacy predecessor is a supported
  schema shape: an empty `legacy-polarity` falls through to the schema
  default with no warning. Leave `legacy-filename` and `legacy-polarity`
  empty, `resolution` = `config-dir`,
  `legacy-probe-on-resolution-failure` = `false`,
  `legacy-import-locations` = `config-dir`. `[verified: _config.py:313-316;
  the `legacy-import-locations` value is inert because there is no legacy
  file to import]`
- **row19** — an environment-variable gate would not reliably reach
  agent-launched runs, which is the population the instrumentation exists
  to measure. `[verified: README.md:530 — "Agents' Bash-tool subprocesses
  inherit the environment `claude` had at launch rather than reading the
  shell live"]`
- **row20** — cgroup/container CPU quotas are not reflected in either
  `sched_getaffinity` or the host load average. This is inherited from
  xdist's own detection, not introduced here: M1's computed value never
  exceeds what `-n auto` would have picked on the same box. `[verified:
  row12's provider chain]`
- **row21** — no test requires every `config-keys.psv` key to be
  promptable; six existing keys carry an empty `prompt-description`.
  `[verified: grep of `claude/.claude/hooks/tests/` for
  `prompt_description` returns only
  `test_install_sh_machine_level_opt_ins.py`, which names specific keys
  rather than iterating the schema]`

**Promptability decision:** `test_selection_tracking` is **not**
machine-promptable. `[engineer-verified]`, matching `plan-architect`'s
recommendation: a first-time installer has no basis to answer "do you
want test-selection fallback telemetry?", since the key's only audience
is whoever is tuning `select-tests.py`'s domain rules. Documented in
README's Tests section instead of wired into `install.sh`'s prompt loop.

**Rollback asymmetry, named explicitly.** Phase 1 ships no config gate
because it is fully stateless (no config row, no persisted data) — a
plain code revert undoes it completely, unlike Phase 2's persisted log,
which keeps the `test_selection_tracking` gate. A Phase-1-only revert also
removes `record_selection`'s two conditional `worker_count`/`load_average`
fields, since they are populated from Phase 1's `WorkerSizingResult` — the
log then reverts to Phase 2's original five-field shape, not to an
error, because `size_result` is an optional parameter. An individual
developer can also self-override at any time via `-n <N>` or an exported
`PYTEST_XDIST_AUTO_NUM_WORKERS` (row5, M2).

**Dispatch split:** two phases, one `code-writer` dispatch each, strictly
sequential and never parallel — both phases edit
`claude/.claude/scripts/select-tests.py` and
`claude/.claude/scripts/tests/test_select_tests.py`, and parallel
dispatches share this worktree, where overlapping edits clobber silently
rather than conflict. Phase 1 is the worker sizing (three files); Phase 2
is the instrumentation (the config-key surface). Phase 1 first, because
it is the unit's actual goal and stands alone if Phase 2 is deferred.

## Critical files

**Phase 1 — load-aware worker sizing**

- `claude/.claude/scripts/select-tests.py` — **modify.** Add `import os`.
  Add a module-level `XDIST_WORKER_ENV_VAR = "PYTEST_XDIST_AUTO_NUM_WORKERS"`
  and `_MIN_LOAD_AWARE_WORKERS = 2`. Add three functions, each a separate
  seam so the formula is testable without a load average or a subprocess:
  - `_cpu_budget() -> int` — mirrors xdist's non-psutil provider branch
    per row12, with a one-line comment stating the durable fact ("mirrors
    pytest-xdist's own `-n auto` count on the non-psutil path, so an idle
    machine gets today's worker count unchanged").
  - `compute_worker_count(*, cpu_budget: int, load_one_minute: float) ->
    int` — pure, no I/O, the formula above verbatim.
  - `pytest_subprocess_env(base_env, *, getloadavg) -> WorkerSizingResult`
    — a `NamedTuple` of `env`, `outcome`, `worker_count: int | None`,
    `load_average: float | None`, so the caller receives the sizing
    decision itself rather than inferring it from the returned env dict.
    `outcome` is one of three module-level string constants
    (`WORKER_SIZING_ALREADY_SET`, `WORKER_SIZING_COMPUTED`,
    `WORKER_SIZING_UNAVAILABLE`), matching `SelectionResult.reason`'s
    existing bare-string convention in this file. `getloadavg` has no
    default — the caller looks it up fresh at call time
    (`getloadavg=os.getloadavg`) and passes it down explicitly, so a test
    can monkeypatch `os.getloadavg` directly instead of stubbing this
    function wholesale.

  Then give `run_pytest` an `env` keyword and pass it through to
  `run(...)`; have `main()` compute the `WorkerSizingResult` once (skipped
  entirely, as `None`, for the nothing-to-run early return, since pytest
  never runs there and a `getloadavg()` syscall would be wasted) and print
  exactly one stderr line in the same style as the existing reason lines
  — `select-tests: PYTEST_XDIST_AUTO_NUM_WORKERS=<N> (1-minute load
  average <L>)`, or the corresponding one-liner for the already-set and
  load-unavailable branches, branching on `outcome` directly rather than
  re-inspecting the env dict. Naming what was set, rather than
  interpreting it, keeps the line accurate even when a caller's own
  `-n <N>` wins per row5, with no argv parsing.

  **Reuse:** `run_pytest`'s existing `run=` parameter is already the test
  seam — do not add a second one. Do not touch `build_pytest_argv`; the
  env var is the injection point precisely so passthrough argv semantics
  stay untouched.

- `claude/.claude/scripts/tests/test_select_tests.py` — **modify.**
  Follow `test_invokes_resolved_pytest_executable_with_constructed_argv`
  (around :1248): inject a fake `run` and assert on the constructed call,
  never shelling out to real pytest. Cover, at minimum: the table of
  `(cpu_budget, load) -> workers` cases including an idle machine
  returning exactly `cpu_budget`; the never-exceeds-`cpu_budget`
  invariant; `cpu_budget == 1` returning 1, not the floor of 2;
  `cpu_budget == 2, load == 0` (the point where the floor arm
  `min(2, cpu_budget)` and the cap arm coincide exactly); a
  moderate-contention case with `load < cpu_budget` where the
  proportional term still lands under 2 (e.g. `cpu_budget=8, load=6.5`,
  the shape M4's floor exists to bound — not just the extreme-overload
  case of a load above `cpu_budget`); a pre-set non-empty env var
  passing through untouched; an empty-string env var *not* counting as
  set (row6); `getloadavg` raising `OSError` producing an env with no
  `PYTEST_XDIST_AUTO_NUM_WORKERS` key added; and `run_pytest` forwarding
  the env dict to `run`. Add `capsys`-based assertions on the exact
  stderr line text for each of the three branches (M1 default, M2
  deferral, M3 OSError-degradation), matching the existing convention at
  `test_unmatched_path_prints_offending_paths_to_stderr` and
  `test_global_trigger_prints_offending_path_to_stderr`
  (`test_select_tests.py:1817,1833`) — a copy-paste bug that prints the
  M1 message in the M3 branch must fail a test, not just pass silently
  because "some stderr line" was checked. Add one guard test asserting
  `psutil` is not importable
  (`importlib.util.find_spec("psutil") is None`), failing with a message
  pointing at `_cpu_budget` — xdist's own runtime check
  (`xdist/plugin.py:26-53`) is `try: import psutil`, an importability
  check, not a manifest scan; a manifest-text guard on
  `requirements-dev.txt` would stay green while psutil is importable via
  a transitive dependency or a contributor's local `pip install`, letting
  `_cpu_budget()` silently diverge from xdist's actual (physical-core)
  budget. Add one case pinning `round()`'s half-to-even behavior at a
  `.5` boundary (e.g. `cpu_budget=8, load=6.5` → `round(1.5) == 2`) so a
  later change to `_MIN_LOAD_AWARE_WORKERS` or the formula shape can't
  silently invert that tie-breaking rule unnoticed.

- `README.md` — **modify** the bullet list at :525-530. It currently
  tells the reader to compute `logical cores / concurrent runs`
  themselves; after Phase 1 that is only true for the bare
  `.venv/bin/pytest` command, since `select-tests.py` now does it
  automatically. Rewrite so there is one statement of who sizes what:
  `select-tests.py` sizes from the 1-minute load average and never
  exceeds what `-n auto` would pick; an exported
  `PYTEST_XDIST_AUTO_NUM_WORKERS` or an explicit `-n <N>` still wins; the
  manual formula remains the guidance for the bare `pytest` command. Keep
  :530's agent-environment-inheritance sentence — it is still correct and
  is now also the reason the Phase 2 gate is a config key, not an env
  var.

**Phase 2 — fallback-reason instrumentation**

- `claude/.claude/scripts/select-tests.py` — **modify.** Add `import
  json`, a UTC timestamp source, and `from _config import config_enabled`
  / `from _config_dir import config_dir` (row17). Add
  `record_selection(selection, resolved_targets, size_result=None)`,
  where `size_result` is Phase 1's `WorkerSizingResult | None` (`None`
  for the nothing-to-run outcome, where no sizing decision was made):
  return immediately
  unless `config_enabled("test_selection_tracking")` is true; resolve the
  path as `config_dir() / ".test-selection-log.jsonl"`; append one JSON
  object with `logged_at` (ISO 8601 UTC, the field name
  `.permission-prompt-log.jsonl` already uses), `reason`, `is_full_suite`,
  `triggering_paths` (capped at the first 20 entries, with a
  `triggering_paths_truncated: true` field added when the real list is
  longer — an unbounded list can push a single JSON line past one
  `write()` syscall's worth of bytes, and interleaving is exactly the
  failure mode concurrent invocations of this same log would otherwise
  risk), `target_count`, and `worker_count` / `load_average` (added only
  when a worker-sizing computation actually ran — omitted, not `null`,
  for the already-set-by-caller, load-average-unavailable, and
  nothing-to-run outcomes, matching `triggering_paths_truncated`'s own
  conditional-field style); wrap the whole body in one `except` clause
  catching `OSError`, `ValueError` (`config_dir()` raises `ValueError`
  when unresolvable), `ConfigSchemaEmptyError`, and
  `ConfigSchemaRowTruncatedError` (both `KeyError` subtypes `_config.py`'s
  `config_enabled` raises for a torn or missing `config-keys.psv` row) and
  emit one stderr warning. Call it from `main()`
  immediately after `resolved_targets` is computed and **before**
  `run_pytest`, so an interrupted or failing pytest run still records its
  selection.

  **Reuse:** `SelectionResult` already carries `reason`, `is_full_suite`,
  and `triggering_paths` (`select-tests.py:500-507`) — the log needs no
  new plumbing through the selection path. `config_dir()`/
  `config_enabled()` are the existing single source of truth for both
  resolutions; do not re-derive either.

- `claude/.claude/hooks/config-keys.psv` — **modify.** One row:
  `test_selection_tracking|bool|false|config-dir|false|config-dir|||Test-selection
  fallback tracking|README.md § Tests|` (per row18; the trailing empty
  field is the non-promptable decision below — confirm the README
  heading text exactly, since `.claude/rules/citation-grammar.md` is
  mechanically enforced). This file triggers a `claude-hook-review`
  dispatch per `.claude/rules/review-pipeline-dispatch.md`; the new row
  touches none of the five enforcement-critical keys.

- `.gitignore` — **modify.** Add
  `claude/.claude/.test-selection-log.jsonl` beside :136's
  `.permission-prompt-log.jsonl`, in the same "hook/skill-written
  per-session and per-machine state" block (row15).

- `docs/config-file.md` — **modify.** Add the per-key table entry, and
  update the two count claims `test_doc_counts.py:522-531` matches ("For
  each of the N keys", "none exist among today's N keys").

- `install.sh` and `claude/.claude/scripts/migrate-legacy-config.sh` —
  **modify.** Comment-only count updates for the same `test_doc_counts.py`
  fact (row16): "so one function reports all N." and "non-interactive
  import for all N keys". Nothing else in either file changes, since the
  key is non-promptable.

- `claude/.claude/hooks/tests/config-schema-audit.md` — **check and
  likely modify.** It carries a `### <key>` section per key recording
  resolution, legacy-probe, legacy-import, and fail direction. Confirm
  whether it enumerates every key; if so, add the matching section for
  `test_selection_tracking` (fail direction on resolution failure:
  logging is skipped, matching this key's off-by-default polarity).

- `claude/.claude/scripts/tests/test_select_tests.py` — **modify.** Tests
  for: nothing written when the key resolves false; one well-formed JSON
  line appended when true, with every one of the seven documented fields
  (`logged_at`, `reason`, `is_full_suite`, `triggering_paths`,
  `target_count`, `worker_count`, `load_average`) asserted on the parsed
  object, not just JSON-validity plus a `reason` match, and `worker_count`
  / `load_average` specifically asserted present when a sizing
  computation ran and absent otherwise — at two layers: `record_selection`'s
  own unit layer (a hand-built `WorkerSizingResult` passed directly is
  sufficient to pin the field-gating logic) *and* a `main()`-level
  integration test asserting the persisted log's `worker_count`/
  `load_average` equal the values that same invocation's stderr line
  reports, since the two are populated from one `size_result` computed
  once in `main()` and a wiring regression at that call site would leave
  the unit-layer test green; the line's `reason` matching each of the five
  outcomes `select-tests.py` actually produces —
  `"empty-diff"`, `"global-trigger"`, `"unmatched-path"`,
  `"domain-selected"`, `"git-unavailable"` — including
  `"git-unavailable"` specifically, since it is constructed in `main()`'s
  `except GitDiffUnavailable` branch rather than inside
  `select_pytest_targets` like the other four and is the outcome easiest
  to leave untested by accident; the `triggering_paths_truncated` flag
  appearing when the list exceeds 20 entries; an `OSError` on append not
  propagating, with a `capsys` assertion that the one stderr warning
  line actually printed (not just that the call didn't raise); and
  `record_selection` being called before `run_pytest`. Use a
  `CLAUDE_CONFIG_DIR` pointed at `tmp_path` rather than touching the real
  config dir.

**Explicitly not edited:** `pyproject.toml` (its `-n auto` stays — the
sizing is per-invocation, not repo-wide), `.github/workflows/tests.yml`
(row13), `build_pytest_argv`, and the `DOMAIN_RULES` /
`GLOBAL_TRIGGER_PATHS` tables.

## Verification

Run every command from the worktree root, one double-quoted statement per
Bash call with no nested `$(...)`, per CLAUDE.md's Bash-guard convention.
A fresh worktree ships no `.venv` of its own; run `./install-dev.sh` from
the worktree root first, or point at the main checkout's `.venv` — needed
before re-verifying row12 against the installed `pytest-xdist` source, not
only for running the commands below.

1. **Formula and degradation, at the unit seam.**
   `.venv/bin/python3 claude/.claude/scripts/select-tests.py` — this is
   the repo's documented scoped command, and it selects the full suite by
   construction, since `claude/.claude/scripts/select-tests.py` is a
   `GLOBAL_TRIGGER_PATHS` member (`select-tests.py:252-256`). No
   hand-widening is needed or permitted.
2. **Sizing, empirically (one-time manual sanity check — this step has no
   lasting regression value; the automated invariant coverage lives in
   step 1's unit tests).** Run
   `.venv/bin/python3 claude/.claude/scripts/select-tests.py -k <a filter
   matching exactly one cheap test, not zero>` and confirm the new stderr
   line's worker count is reflected in xdist's own final status line,
   matched with a version-tolerant pattern
   (`grep -E '[0-9]+ workers?'`) rather than an exact string comparison —
   that line's wording (`xdist/dsession.py`'s `get_workers_status_line`)
   is internal to `pytest-xdist`, not a contractual format, and its
   singular/plural noun differs by count. Matching at least one test
   avoids relying on unverified behavior around whether that line prints
   at all when zero tests are collected.
3. **Deferral to an already-set value (settles M2).** Same command with
   `PYTEST_XDIST_AUTO_NUM_WORKERS=3` prefixed — confirm the deferral
   stderr line fires and the banner reports 3.
4. **Explicit `-n` still wins (settles row5 empirically against the
   source read).** Same command with `-n 2` added and no env var —
   confirm the banner reports 2 regardless of what the stderr line says
   was set.
5. **Log gating.** With `CLAUDE_CONFIG_DIR` pointed at an empty temp
   directory and no `claude-config.toml`, confirm no
   `.test-selection-log.jsonl` is created. Then with
   `test_selection_tracking = true` written into that directory's
   `claude-config.toml`, confirm exactly one JSON line appears carrying
   the expected `reason`.
6. **Lint.** `.venv/bin/ruff check claude/.claude/`.
7. **Review pipeline.** `/code-review` (which dispatches
   `claude-hook-review` for the `config-keys.psv` change per
   `.claude/rules/review-pipeline-dispatch.md`), then `/ready-for-review`
   before push.

Step 1 is the pass/fail gate for `test_doc_counts.py`'s four count
assertions and for `test_config_parser_parity.py` — both are in
`claude/.claude/hooks/tests/`, which the `config-keys.psv` cross-domain
exception (`select-tests.py:494`) already routes to.

## Out of scope

- **No cross-process coordination of any kind** — no semaphore, lock
  file, shared counter, or persistent inter-invocation state. Closed
  (row3) on the same stale-state grounds
  `.claude/plans/scope-test-worker-count.md` recorded.
- **No bounding of a perfectly simultaneous cold-start burst.**
- **No bounding of arrivals landing within the load average's smoothing-lag
  window of each other.** Row14's limitation is accepted, not patched:
  closing either requires reservation-at-start, which is the door row3
  closed.
- **No decomposition of `claude/.claude/tests/helpers.py` or any
  `conftest.py`** to reduce fallback frequency — filed as issue #1015 and
  deliberately separate, since this unit's job is to *measure* fallback
  frequency, not to act on it.
- **No change to `pyproject.toml`'s `addopts`.** A repo-wide fixed `-n
  <N>` would retune every single-session run and CI to serve one
  machine's concurrency pattern; the per-invocation computation reaches
  the same outcome only where it is needed.
- **No new third-party dependency.** `psutil` would move `-n auto` from
  logical to physical cores and still not adapt to load; the Phase 1
  guard test exists precisely to keep that decision explicit if it is
  ever revisited.
- **Worker-count and load-average fields are in the selection log**
  because Phase 1's success criterion is otherwise unfalsifiable in the
  field: row14 already concedes the mechanism may not measurably help the
  arrival pattern it targets, with no way to tell from the log alone.
  `select-tests.py`'s `main` holds both values in hand before the log
  write — the sizing function returns them rather than `main`
  re-deriving them — so the addition is two conditional JSON keys, not
  new coupling between the sizing and logging mechanisms.
- **No log rotation or size cap.** Matches
  `.permission-prompt-log.jsonl`'s documented posture
  (`docs/permission-prompt-tracking.md` § "Known limitations"):
  append-only, manual trim, one documented home for the retention
  tradeoff rather than two divergent policies.
- **No per-repo opt-out for the log**, for the same reason
  `docs/permission-prompt-tracking.md` gives: a per-repo opt-out
  fragments the cross-repo aggregate the mechanism exists to produce, for
  no privacy benefit the machine-level key does not already provide.
- **No `.github/workflows/tests.yml` change.** CI invokes `pytest`
  directly and never reaches `select-tests.py` (row13).
- **No tuning of `_MIN_LOAD_AWARE_WORKERS` from measurement in this
  unit.** Row8 flags it as unverified and names the constant so a later
  change is one line.
- **No second documentation home.** README's Tests section stays the
  single place worker-count and test-selection behavior are described;
  `docs/config-file.md` gets only the schema row and count updates it
  mechanically owns.
