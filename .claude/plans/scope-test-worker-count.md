# Scope pytest-xdist worker count for safe concurrent local sessions

## Context

A prior session told the engineer the full test suite ran with 16
pytest-xdist workers, and the engineer wants to (1) confirm that count
is properly scoped to their own machine, (2) make it configurable, and
(3) be able to run multiple Claude Code sessions' test suites
concurrently on that machine without oversubscribing its CPUs. This
matters now because this repo's workflow routinely runs many parallel
agent worktrees (80+ exist in this checkout today), each of which may
invoke `select-tests.py` or the full suite independently, and today
nothing documents how to keep those runs from stepping on each other.
The intended outcome is a documented, already-existing pytest-xdist
override mechanism the engineer can set as a personal default, with no
behavior change for other contributors or CI.

## Discovery findings (for plan-architect)

- `pyproject.toml`'s `[tool.pytest.ini_options]` sets
  `addopts = ["-n", "auto", "--strict-markers"]`
  (`pyproject.toml:24`).
  This governs both the documented full-suite command
  (`.venv/bin/pytest claude/.claude/`) and `select-tests.py`, since
  neither passes its own `-n` value.
- `select-tests.py` (`build_pytest_argv` at
  `claude/.claude/scripts/select-tests.py:344`)
  forwards `sys.argv[1:]` straight through to the `pytest` subprocess
  after the resolved target paths, so a CLI override such as
  `select-tests.py -n 4` already reaches pytest unmodified today — no
  code change needed for a one-off override.
- Machine facts, gathered this session: `sysctl -n hw.ncpu` → 16
  (logical), `sysctl -n hw.physicalcpu` → 8 (physical). No `psutil` is
  declared in `requirements-dev.txt` (only `pytest==8.*` and
  `pytest-xdist==3.*`), so pytest-xdist's `-n auto` on this machine
  falls back past its psutil-based physical-core provider to
  `multiprocessing.cpu_count()`, landing on 16 — the number the prior
  session reported.
- Verified against pytest-xdist's own source
  (`https://github.com/pytest-dev/pytest-xdist/blob/master/src/xdist/plugin.py`,
  fetched this session): `pytest_xdist_auto_num_workers()` tries a
  provider chain, and the *first* provider checked is
  `_auto_num_workers_envvar()`, which reads the
  `PYTEST_XDIST_AUTO_NUM_WORKERS` environment variable and returns
  `int(env_var)` if set — overriding every other provider, including
  the `multiprocessing.cpu_count()` fallback. This is pytest-xdist's
  own documented escape hatch for exactly this scenario: it requires
  no repo code change, works for both the full-suite command and
  `select-tests.py`, and is honored per-process, so each concurrently
  running session can set it independently in its own shell.
- README.md's Tests section (`README.md:513`)
  currently documents only `-n0` for serial debugging — it says
  nothing about `-n auto`'s worker count or how to override it.
- CI (`.github/workflows/tests.yml`) runs on a `ubuntu-24.04` GitHub-hosted
  runner (2 vCPUs on standard runners) and always runs as a single job
  per push/PR (`concurrency.cancel-in-progress: true`), so it never hits
  the concurrent-local-session problem this plan addresses and needs no
  change. It already sets `PYTEST_XDIST_AUTO_NUM_WORKERS` from a repo
  Variable, with its own comment explaining the fallback — confirmed
  this session by reading `.github/workflows/tests.yml`'s job-level
  `env:` block directly.

## Answers gathered (Step 4)

- **Where the safer worker count lives:** personal override only. Keep
  `pyproject.toml`'s `-n auto` unchanged — it stays correct for CI and
  any contributor running one session at a time. Document the
  existing `PYTEST_XDIST_AUTO_NUM_WORKERS` env var and `-n <N>` CLI
  override in README so the engineer can set a personal default
  outside this repo (their own shell profile or per-worktree direnv
  config, per their own workstation-setup conventions) rather than
  changing the repo-wide default for every contributor.
- **Concurrency target:** 4 concurrent full-suite sessions on this
  16-logical-core machine → a recommended per-session cap of 4 workers
  (16 / 4), stated in README as a formula (`logical cores / concurrent
  sessions you expect to run`) rather than a hardcoded number, since
  the divisor is per-engineer and per-machine.

## Approach

Document the two worker-count overrides pytest-xdist and this repo's own
test entry points already support, and change no code. `pyproject.toml`
keeps `addopts = ["-n", "auto", ...]` exactly as it is; README.md's Tests
section gains one paragraph naming (a) the `PYTEST_XDIST_AUTO_NUM_WORKERS`
environment variable, which pytest-xdist consults before any of its own
core-count detection, and (b) an explicit `-n <N>` on the pytest command
line, which `select-tests.py` forwards through unchanged. Alongside them it
states the sizing rule — *logical cores divided by the number of test runs
you expect to have in flight at once* — as a formula the reader applies to
their own machine, not as a number this repo picks for them. Wiring a
personal default into a shell profile or per-directory environment config
happens outside this repository; the repository's job is to say the knob
exists and how it behaves.

Heavier alternatives considered and set aside. Lowering `addopts` to a
fixed `-n 4` would slow every contributor's single-session run and CI to
serve one engineer's concurrency habit (anchors: row3 — the right divisor
is not a repo-wide constant). Adding `psutil` to `requirements-dev.txt` so
`-n auto` resolves to physical rather than logical cores would take a new
third-party dependency to move one machine from 16 workers to 8, still
without capping concurrent runs (anchors: row8). Teaching `select-tests.py`
a `--workers` flag or a repo-local config file would reimplement, in this
repo, a knob pytest-xdist already ships and CI already uses (anchors: row4).
A cross-worktree lock or semaphore serializing concurrent suites would add
coordination state, a stale-lock failure mode, and a new way for a run to
hang, to solve a problem a per-shell integer solves (anchors: row3). None of
the four buys anything the documented knobs do not.

Because the chosen design adds no code, no dependency, and no new execution
context, there is no mechanism here heavier than the task requires — the
enumeration above is of the heavier options rejected, not of lighter
substitutes for something heavy that was kept.

**Root problem:** concurrent local test runs — one per agent worktree, and
this checkout carries many — each default to `-n auto`, which resolves to
the machine's *logical* CPU count, so several simultaneous runs oversubscribe
the machine by roughly their own count; nothing in the repo tells a
contributor how to cap the per-run worker count.

**Givens** (conditions this design treats as fixed because they lie outside
its reach):

- **row1** — pytest-xdist owns how `-n auto` becomes a number, including the
  order of its detection providers. Vendor-imposed: the chain lives in
  xdist's own `plugin.py`, and the repo can only feed it, not replace it.
  `[verified: xdist plugin.py provider chain, fetched this session (see
  Discovery findings); requirements-dev.txt:2 pins pytest-xdist==3.*]`
- **row3** — how many test runs will be in flight at once is a per-engineer,
  per-machine, per-moment number that nothing inside this repository can
  observe. Removing the design's dependence on a human-supplied divisor
  would require a cross-process coordination mechanism, which is a decision
  outside this plan. `[engineer-verified]`

**Mechanisms** (each justified against the root problem or a row above):

- **Document `PYTEST_XDIST_AUTO_NUM_WORKERS` as the per-shell default.** It
  is the only override that both entry points honor without a code change,
  and it wins over the detection this repo cannot alter. *anchors: root,
  row1, row4, row5*
- **Document `-n <N>` as the one-off per-command override.** Covers the case
  where the reader wants a different count for a single run without changing
  their environment, and it already reaches pytest through `select-tests.py`
  unmodified. *anchors: root, row6, row7*
- **State a formula rather than a worker count.** The divisor is a human
  input the repo cannot know, so a concrete number would be wrong for every
  reader but one. *anchors: row3, row8, row9*
- **Prose only — no `pyproject.toml`, `select-tests.py`, or workflow edit.**
  Both overrides work today, so any code change here would add a second way
  to express something the toolchain already expresses. *anchors: root,
  row11, row12*

**Assumptions:**

- **row4** — `PYTEST_XDIST_AUTO_NUM_WORKERS` is read by the *first* provider
  in `pytest_xdist_auto_num_workers()`, so a set value short-circuits every
  later provider including the logical-core fallback.
  `[verified: xdist plugin.py — _auto_num_workers_envvar() checked first;
  fetched this session, recorded under Discovery findings]`
- **row5** — the variable is consulted per pytest process, so two concurrent
  runs can carry different values without interfering.
  `[verified: same source — the provider reads the environment at run time
  rather than from shared state]`
- **row6** — a `-n <N>` arriving on the pytest command line overrides the
  `-n auto` in `addopts`. `[verified: pyproject.toml's addopts comment
  states it for -n0, and .github/workflows/tests.yml's serial timing pass
  passes -n0 against the same addopts and does run serially in practice;
  Verification step 2 re-confirms it for a nonzero N, which is the form the
  README will recommend]`
- **row7** — `select-tests.py` appends its caller's arguments to the pytest
  argv verbatim, after the resolved target paths, so `select-tests.py -n 4`
  reaches pytest intact. `[verified: build_pytest_argv at
  claude/.claude/scripts/select-tests.py:344-350 returns the target paths
  followed by the passthrough args; main() at :370 sets them from
  sys.argv[1:] and passes them at :386]`
- **row8** — on the engineer's machine `-n auto` resolves to 16 (logical),
  not 8 (physical), because `psutil` is not declared in
  `requirements-dev.txt` and xdist's physical-core provider is skipped.
  `[verified: sysctl hw.ncpu=16 / hw.physicalcpu=8 this session;
  requirements-dev.txt declares only pytest and pytest-xdist]`
- **row9** — the engineer's target is up to four concurrent full-suite runs,
  giving four workers each on that machine. `[engineer-verified]`
- **row10** — an exported variable reaches a pytest run only if the process
  that launches pytest inherited it; a value set only in an interactive
  shell's rc file may not reach a non-interactive shell launched by an
  editor or agent. `[unverified]` — the README wording therefore tells the
  reader to confirm the worker count from xdist's own startup banner rather
  than asserting which file to set it in — wiring a personal default into a
  specific shell file is a scope decision (see Out of scope), not something
  this row treats as unreachable.
- **row11** — CI needs no change and is not a second place this guidance must
  land: it runs one cancel-on-supersede job on a fixed GitHub-hosted runner
  and already sets `PYTEST_XDIST_AUTO_NUM_WORKERS` from repo Variables, with
  its own comment explaining the fallback ("Unset/empty falls through to
  xdist's own auto-detection"). `[verified:
  .github/workflows/tests.yml — concurrency.cancel-in-progress: true,
  runs-on: ubuntu-24.04, and the job's env block setting
  PYTEST_XDIST_AUTO_NUM_WORKERS from vars.PYTEST_XDIST_AUTO_NUM_WORKERS,
  all re-read this session]`
- **row12** — README.md:513 is the only contributor-facing prose describing
  the default parallel behavior other than the prerequisites bullet at
  README.md:97, which mentions only `-n0`, so one paragraph at :513 is the
  whole change. `[verified: repo-wide grep for
  "n0|xdist|numprocesses|PYTEST_XDIST" against README.md this session —
  only lines 97 and 513 match, both -n0-only]`

**Dispatch split:** one phase, one `code-writer` dispatch. The change is a
single paragraph in a single file, so there is nothing to partition.

## Critical files

- `README.md` — **modify.** Insert one paragraph in the Tests section
  immediately after the existing sentence at line 513 ("The suite runs
  under `pytest-xdist` (`-n auto`) by default; pass `-n0` to run serially…")
  and before the "CI runs the same pin set…" sentence at line 515. That
  places the override guidance next to the sentence that introduces the
  default it overrides.

  The paragraph should state four things, one sentence each:
  1. `-n auto` resolves to the machine's logical CPU count (say "logical CPU
     count", not a function name — `pyproject.toml` and
     `.github/workflows/tests.yml` describe the fallback slightly
     differently, and the observable outcome is what matters).
  2. Set `PYTEST_XDIST_AUTO_NUM_WORKERS=<N>` in the environment to cap it for
     every run in that shell; pytest-xdist checks it ahead of its own
     detection, and it applies to both `.venv/bin/pytest claude/.claude/` and
     `select-tests.py`.
  3. Or pass `-n <N>` on the command line for a single run —
     `select-tests.py` forwards it through to pytest.
  4. Sizing rule for running several suites at once: logical cores divided by
     the number of concurrent runs you expect (e.g. a 16-core machine
     expecting four concurrent runs → 4). Add the confirmation cue: xdist
     prints the worker count in its startup banner, so check there if a run
     does not appear to have picked the value up.

  Two constraints on the copy. It must not create a forward dependency on
  `select-tests.py`, which the README does not introduce until line 517 —
  naming the file is fine, explaining it is not. And it must not restate
  CI's use of the same variable; `.github/workflows/tests.yml`'s env block
  already carries that in its own comment, and the Tests section's CI
  sentence at line 515 is the pointer.

**Reuse opportunities:** none apply — the change adds no code, so there is no
function or utility to call instead of reimplementing. The reuse that *is*
happening is at the design level: both documented overrides are existing
mechanisms (pytest-xdist's environment variable, `select-tests.py`'s existing
argv passthrough), which is precisely why the file list is one entry long.

Explicitly not edited: `pyproject.toml`, `claude/.claude/scripts/select-tests.py`,
`.github/workflows/tests.yml`, and README.md:97's prerequisites bullet — see
Out of scope.

## Verification

1. **Doc accuracy re-read.** Read the new paragraph back against its three
   sources and confirm each claim: `pyproject.toml:24` for the `-n auto`
   default, the provider-order fact in row4, and `select-tests.py:344-350`
   plus `:386` for the passthrough. Confirm the paragraph names no function
   the repo describes inconsistently (row12's note) and adds no CI claim.
2. **Empirical check of the `-n <N>` passthrough (settles row6 for a nonzero
   worker count and row7 end to end).** From the worktree:
   `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py -n 2 -k no_such_test`
   — confirm xdist's startup banner reports two workers rather than the
   machine's logical-core count. The `-k` filter keeps the run cheap; the
   banner prints before collection finishes.
3. **Empirical check of the environment variable.** Same command with
   `PYTEST_XDIST_AUTO_NUM_WORKERS=2` prefixed and no `-n` flag — confirm the
   banner again reports two workers. Keep it to a single statement in one
   Bash call, per this repo's Bash-guard convention.
4. **Suite.** `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py`
   from the worktree (worktree-relative `.venv` path per README.md:511). This
   is a docs-only change, so expect either a docs-domain selection or the
   full-suite fallback; either is fine. The `addopts` pin in
   `claude/.claude/tests/test_pytest_collection_config.py` stays green
   precisely because `pyproject.toml` is untouched — a failure there means
   the change went further than intended.
5. **Review pipeline.** `/code-review`, then `/ready-for-review` before
   handoff, per this repo's CLAUDE.md.

## Out of scope

- **No change to `pyproject.toml`'s `addopts`.** It would retune every
  contributor's single-session run and CI to suit one engineer's concurrency
  pattern, and the per-shell override reaches the same outcome for whoever
  actually wants it.
- **No new third-party dependency.** Adding `psutil` so `-n auto` picks
  physical cores would take a new supply-chain surface to halve one machine's
  worker count, and it still would not cap concurrent runs — the actual
  problem.
- **No `select-tests.py` change.** No `--workers` flag, no repo-local worker
  config: the script already forwards `-n <N>` unmodified, so any addition
  would be a second spelling of an existing knob.
- **No cross-run coordination.** A lock or semaphore serializing concurrent
  suites across worktrees is a real design, but it introduces shared state
  and a stale-lock failure mode to replace a number the human already knows.
- **No `.github/workflows/tests.yml` change.** CI runs one cancel-on-supersede
  job on a fixed-size runner and already exposes this variable through repo
  Variables; it never meets the concurrent-local-session problem.
- **No edit to the engineer's dotfiles repository.** It's a reachable peer
  repo, not an out-of-reach dependency, but wiring a personal default into
  it is a deliberate scope choice (the "personal override only" decision in
  Step 4) rather than something this plan needs to do — this plan ships the
  documentation the engineer wires in themselves.
- **No fix to the fallback-API wording drift.** `pyproject.toml` and
  `.github/workflows/tests.yml` describe xdist's non-psutil fallback with
  slightly different function names. Both are comments in files this change
  does not otherwise touch; the README paragraph sidesteps the question by
  naming the observable outcome. Worth raising separately if it ever matters.
- **No second documentation site.** README.md:97's prerequisites bullet keeps
  mentioning only `-n0`; the Tests section stays the single home for
  worker-count guidance.
