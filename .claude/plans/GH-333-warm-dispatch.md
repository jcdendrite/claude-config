# GH-333 — Implement `--resume` warming for behavioral-dispatch evals

## Context

**Goal:** make the `subagent-delegation` behavioral-dispatch eval actually
measure delegation behavior by giving the model a *physically* filled context
window, not an asserted one.

Issue #333 asked for adversarial `claude -p` cases proving `subagent-delegation`
does not over-trigger. PR #407 (merged) built the `behavioral-dispatch`
measurement method, 5 cases, offline detector unit tests, and README docs — but
the instrument is documented as structurally too cold to deliver the issue's
goal. The current warming uses `--append-system-prompt` (`dispatch-session-handoff.md`),
which *asserts* a large prior context while the real window stays small. The
result: DELEGATE cases fire 0–1/10 ("bounded-retrieval framing bias") and INLINE
cases "do not self-validate." The README names the fix — `--resume <session-id>`
against a real prior session that physically fills the window — but it was never
implemented. This plan implements it as an opt-in warm mode.

This is harness-only work under `evals/`; the `subagent-delegation` SKILL.md is
not modified. Note `evals/` files are distributed to all stow users, so keep the
new mode opt-in and documented.

## Approach

Add an opt-in **warm mode** to behavioral-dispatch, gated by a new
`--warm-dispatch` flag (default off → existing cold `--append-system-prompt`
path is unchanged). Warm mode works in two stages:

1. **Prime once per run** (shared across all cases and all K samples): run a
   single `claude -p` in the dispatch-project with a self-generated session UUID
   (`--session-id <uuid>`) and an *executable* priming prompt that instructs the
   model to actually read `components.py`, `renderer.py`, `layout.py`, and
   `logs/render.log` and log anomalies. The real Read/Bash tool calls land the
   tokens physically in the session's window, leaving the same "steps still
   queued" orchestrator stance as the current asserted handoff — but real. The
   priming prompt **must forbid delegation** (no Agent/Task) — priming that
   dispatches fills a subagent's context, not the parent's, defeating the
   warming purpose.
2. **Sample K times in parallel**: each sample runs
   `claude -p <case-query> --resume <uuid> --fork-session --output-format stream-json …`.
   `--fork-session` gives every parallel sample its own forked session ID so K
   concurrent resumes never corrupt the immutable primed base. The existing
   Agent/Task detector (`detect_dispatch_in_stream`) is reused unchanged.

Cost: **1 priming invocation + (K × num_cases) sample invocations** per run —
cheaper than the README's pessimistic "2× per sample" estimate, because priming
is shared, not per-sample.

Why opt-in rather than replacing the cold path: warming adds priming latency and
forked-session disk footprint; the cold path stays the cheap default for quick
runs, and the report must label which mode produced the numbers so the two
signals are never conflated.

Rationale for the chosen flags is confirmed against `claude --help`:
`--session-id <uuid>` ("Use a specific session ID"), `-r/--resume [value]`
("Resume a conversation by session ID"), and `--fork-session` ("When resuming,
create a new session ID … only works with --print"). All three coexist with
`-p` + `--output-format stream-json`.

### Lighter alternatives considered

- **`--append-system-prompt` (current cold path):** asserts context without
  filling it — the exact failure this issue exists to fix. Kept as the default
  fallback, not the warm mechanism.
- **`-c/--continue`:** resumes "the most recent conversation" — no explicit ID,
  so it cannot isolate K parallel samples and races under the worker pool.
- **`--resume` *without* `--fork-session`, serialized:** correct but K× slower
  and it mutates the shared base session each sample. `--fork-session` is the
  lighter isolation primitive — prime once, fork per sample — versus priming K
  separate full sessions.
- **Padding the query with filler tokens:** fills the window but creates no real
  tool-call history; the model reads it as noise, not as its own prior work.

### Verification spikes (do first — these are the real unknowns)

Two CLI behaviors must be confirmed by a throwaway 2–3 command spike *before*
wiring the harness (plan-mode prevented running `claude -p` here):

1. **Resume handshake:** `claude -p "X" --session-id <uuid> …` then
   `claude -p "Y" --resume <uuid> --fork-session --output-format stream-json …`
   resumes with prior context intact and emits a parseable stream.
2. **Session storage + cleanup:** locate where sessions persist for a given cwd
   (likely `~/.claude/projects/<hashed-cwd>/`). If sessions live *inside* the
   tempdir project, existing `shutil.rmtree(dispatch_project)` cleans them; if
   not, capture forked session IDs from each sample's `system/init` event and
   delete the base + forks in the `finally` block. Confirm `--fork-session`
   under concurrent workers does not corrupt the base session file; if it does,
   fall back to bounded/serial resume.

## Critical files

- **`evals/run_skill_evals.py`** — the only code file changed:
  - `_build_dispatch_command(query, handoff, model, *, warm, session_id)` —
    branch: warm → `--resume <session_id> --fork-session` (no
    `--append-system-prompt`); cold → existing `--append-system-prompt handoff`
    (unchanged). **Reuse** the existing builder, don't fork a parallel one.
  - New `prime_dispatch_session(dispatch_project, priming_prompt, model) -> str`
    — runs the single priming invocation with a `uuid.uuid4()` session ID,
    waits for the `result` event, returns the ID. (Spawns `claude -p`; only its
    command-builder is offline-testable.)
  - `run_dispatch_sample` — extend the positional arg tuple with
    `(warm, primed_session_id)`; update both the unpack and the `run_case`
    construction together (the docstring already flags this coupling).
  - `run_case` / `run_context` — carry `warm` + `primed_session_id` inside the
    `dispatch_context` dict (the `run_context` already passed through), so only
    the `BEHAVIORAL_DISPATCH_METHOD` branch reads them. Do **not** widen
    `run_skill`'s signature — that would needlessly touch the runtime and
    description-fidelity call paths.
  - `main()` — add `--warm-dispatch` arg (default `False`); in the
    `behavioral_dispatch_files` block, when warm, call `prime_dispatch_session`
    once, store `primed_session_id` in `dispatch_context`, and clean up sessions
    in `finally`. Pass `warm` down to `run_skill`/`run_case`.
  - `print_report` — label warm vs cold in the report header/method column so
    runs are not conflated.
- **`evals/fixtures/dispatch-priming-prompt.md`** (new) — the *executable* audit
  instruction for warm priming. Keep `dispatch-session-handoff.md` as-is for the
  cold path; the two modes coexist. **Reuse** the existing audit narrative
  (R-01/R-02 anomalies, the 5 queued steps) so warm and cold present the same
  orchestrator stance — the only difference is real vs asserted.
- **`claude/.claude/skills/tests/test_trigger_detector.py`** — add offline cases
  for `_build_dispatch_command` warm shape (asserts `--resume` + `--fork-session`
  present, `--append-system-prompt` absent) and confirm the existing cold-shape
  test still passes. **Reuse** the existing `TestBuildDispatchCommand`-style
  structure.
- **`evals/README.md`** — update "behavioral-dispatch", "Instrument warming",
  and "Residual limitation" sections to document `--warm-dispatch`: the priming
  mechanism, the 1-prime + K-fork cost model, session cleanup, and the
  warm-vs-cold report labelling. Keep the "Why local only — never CI" framing.
- **`claude/.claude/skills/subagent-delegation/evals/trigger-cases.json`** —
  update the DELEGATE case notes (currently "0/10 reflects framing bias") to
  state the warm-mode expectation (DELEGATE cases should now fire ≥50% under
  `--warm-dispatch`; cold default still under-fires). Do not re-author cases —
  full re-scoping to post-#408/#410 prose is out of scope (see below).

## Verification

1. Offline unit tests + lint (fast, deterministic):
   `.venv/bin/pytest claude/.claude/skills/tests/test_trigger_detector.py`
   and `.venv/bin/ruff check evals/`.
2. Cold path regression (must be unchanged):
   `python evals/run_skill_evals.py --skill subagent-delegation --samples 10`.
3. Warm path (the payoff):
   `python evals/run_skill_evals.py --skill subagent-delegation --warm-dispatch --samples 30 --verbose`.
   Success = DELEGATE cases (`locate-importers-sweep`, `explore-unfamiliar-area`,
   `relay-lookup`) fire materially higher warm than cold (target ≥50%). If they
   still fire <50% warmed at K=30, the README's conclusion ("structurally too
   cold even warmed") is confirmed — document that as the finding rather than a
   skill regression, and the issue closes on a now-validated negative result.
4. Confirm no session files leak after a warm run (spike #2's cleanup path).

## Out of scope

- Re-authoring trigger-cases.json against post-#408 (relay-lookup carve-out) and
  post-#410 (debug-probe) skill prose — note-level updates only here; full case
  re-scoping is a separate follow-up.
- CI wiring — behavioral-dispatch stays local-only per the README's "never CI"
  rationale (probabilistic, needs `--dangerously-skip-permissions`).
- Applying warming to the `runtime` or `description-fidelity` methods.
