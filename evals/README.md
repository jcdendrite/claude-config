# Skill Evals

A local harness that measures each skill's case file (`trigger-cases.json` or
`disposition-cases.json`) against its declared behavior. It runs one of four
measurement methods per skill — `runtime`, `description-fidelity`,
`behavioral-dispatch`, or `disposition-fidelity` — and reports a per-case pass
rate. See [Measurement methods](#measurement-methods) for which method fits
which skill.

## Why local only — never CI

The harness runs `claude -p` as a subprocess using your existing Claude Code
subscription auth. This means:

- **No `ANTHROPIC_API_KEY` required.** Max-plan OAuth auth is used automatically.
- **No per-token charge** beyond your subscription.
- **No CI wiring.** Both methods produce a probabilistic model classification,
  not a deterministic computation. A single-sample binary pass/fail produces a
  flaky CI signal; the harness treats output as a human-read pass-rate report
  (`triggered 7/10`), not a gate. Running it in CI would also require
  `--dangerously-skip-permissions` on a public repo — a security footgun.

This rationale applies equally to `measure_subagent_model_resolution.py` (see
[below](#subagent-model-resolution-experiment)) — same subscription-auth
subprocess launch, same never-CI posture.

## Usage

```bash
# Run all skills that have a trigger-cases.json:
python evals/run_skill_evals.py

# Run one skill:
python evals/run_skill_evals.py --skill code-review

# Run the test-conventions / test-evaluation adjacency pair:
python evals/run_skill_evals.py --skill test-conventions --skill test-evaluation

# Tune sampling:
python evals/run_skill_evals.py --skill code-review --samples 5

# Spot-compare with Opus:
python evals/run_skill_evals.py --skill code-review --model claude-opus-4-7

# Verbose (prints each case as it completes):
python evals/run_skill_evals.py --verbose

# Warm behavioral-dispatch (physically primes context window via real Read calls):
python evals/run_skill_evals.py --skill subagent-delegation --warm-dispatch --samples 30

# Write machine-readable results:
python evals/run_skill_evals.py --json /tmp/results.json
```

Default model: `claude-sonnet-4-6`. Sonnet is a stricter classifier than Opus,
making it a better stress test of whether a skill's TRIGGER prose is specific
enough. Pass rates are model-version-scoped — the report header prints the
model used.

## Reading the output

```
Skill eval   model=claude-sonnet-4-6  K=10   2026-05-15

code-review                              runtime                4/5
  code-written-commit-pending         triggers      10/10  PASS
  explicit-user-code-review-request   triggers       8/10  PASS
  cosmetic-typo-fix                   does-not-trig  0/10  PASS
  plan-only-no-code                   does-not-trig  2/10  FAIL  (plan-review fired 7/10)
  vs-skill-review-skill-md-only       does-not-trig  0/10  PASS
test-conventions                         description-fidelity   6/6
  planning-tests-new-feature          triggers      10/10  PASS
  adding-tests-to-partially-covered…  triggers       9/10  PASS

summary: 2 skills, 7 cases | pass 5/7 | review the 2 FAIL cases above
```

- The second column on each skill line is the **measurement method** —
  `runtime` or `description-fidelity`. The two are distinct signals; the
  column keeps them from being conflated.
- **PASS**: trigger rate ≥ 50% for should-trigger cases; < 50% for should-not.
  Plus no `also_not_triggered` skill fired in any sample.
- **FAIL + parenthetical**: adjacent skill stole the trigger — the actionable signal
  for tightening the DO NOT TRIGGER prose.
- Under `description-fidelity` the `triggers` / `does-not-trig` per-case label
  reads as "the classifier named this skill" / "named a different skill or
  none" — there is no live dispatch in that mode.
- Exit code is always `0` — measurement, not a gate.

**Noise floor.** Triggering is probabilistic, so the pass rate carries sampling
noise. At `K=10` a case that genuinely triggers ~60% of the time still reads
FAIL roughly 1 run in 6. Treat a borderline FAIL near the 50% line as a prompt
to re-run at higher `K` (e.g. `--samples 30`), not as a confirmed regression.

## Adding trigger cases

Co-locate a `trigger-cases.json` file in the skill's `evals/` subdirectory:

```
claude/.claude/skills/<name>/evals/trigger-cases.json
```

Schema:

```json
{
  "skill_name": "code-review",
  "method": "runtime",
  "cases": [
    {
      "id": "post-implementation-handoff",
      "query": "I just finished the rate limiter — look it over before I commit.",
      "should_trigger": true
    },
    {
      "id": "cosmetic-typo-fix",
      "query": "Fix the typo in the README heading: 'recieve' -> 'receive'.",
      "should_trigger": false
    },
    {
      "id": "adjacent-skill-confusion",
      "query": "Review the edits I made to plan-it/SKILL.md.",
      "should_trigger": false,
      "also_not_triggered": ["plan-review"]
    }
  ]
}
```

- `method`: required. Which harness measures this skill — see
  [Measurement methods](#measurement-methods).
- `id`: stable label shown in the report.
- `should_trigger`: `true` = the skill must fire; `false` = it must stay silent.
- `also_not_triggered`: optional. Adjacent skills that must **not** fire on this
  query. A FAIL names which adjacent skill stole the trigger.

## Measurement methods

Each case file (`trigger-cases.json` or `disposition-cases.json`) declares a
`method`. `run_skill_evals.py` runs all four in one invocation, routing per
case file and labelling each skill's mode in the report:

- **`runtime`** — the harness spawns `claude -p` and watches for the skill's
  `Skill` tool call to fire. It measures real auto-dispatch in a live session.
- **`description-fidelity`** — the harness asks a model, in a plain `claude -p`
  classification prompt, which one skill a query should match given the full
  skill listing. It measures whether the skill's `description` discriminates
  the query, not runtime dispatch.
- **`behavioral-dispatch`** — the harness spawns `claude -p` with a full task
  scenario and watches for the `Task` tool call to fire. It measures whether
  the model actually delegates to a subagent rather than handling the task
  inline. Used for skills like `subagent-delegation` whose effect is the
  parent's tool choice, not a `Skill` invocation.
- **`disposition-fidelity`** — the harness asks `claude -p` to review a fixed
  scenario twice (with and without the skill's governing rule text) and judges
  each review's disposition. It measures whether a rule actually drives the
  correct disposition, not whether the skill triggers at all. See
  [disposition-fidelity](#disposition-fidelity) below.

Headless `claude -p` does not reliably auto-trigger every skill — advisory
skills the model is not pushed to invoke (and `user-invocable: false` skills)
under-trigger regardless of description quality, so `runtime` measurement
returns a false zero for them. Those skills use `description-fidelity` instead.

The first three methods measure genuinely different properties and are not
interchangeable. `runtime` is strictly more faithful when available — it
observes the real dispatch decision — so a skill that *can* trigger headlessly
keeps `runtime` rather than being downgraded to classification.
`disposition-fidelity` is a different axis entirely — trigger/no-trigger vs.
disposition correctness — and is not a substitute for any of the other three.

**Write realistic queries.** On-the-nose queries (keyword-bait like "trigger
code-review now") pass trivially. The `also_not_triggered` confusion cases carry
the real signal — write queries that read like genuine user turns where the
boundary between adjacent skills is ambiguous.

## How detection works

### runtime

The harness runs `claude -p <query>` from a throwaway project whose
`.claude/skills/` is symlinked to the working-tree `claude/.claude/skills/`.
Real skills compete in real mutual context. When the model decides to invoke a
skill, it calls the `Skill` tool with the skill name in the input JSON. The
harness reads the stream-json output and looks for:

```
content_block_start → content_block.type == "tool_use" AND name == "Skill"
content_block_delta → input_json_delta.partial_json (accumulated)
content_block_stop  → parse accumulated JSON; compare input["skill"] == skill_name exactly
```

Early-terminates the subprocess once the target skill fires and no `also_not`
guards remain to observe; reads to timeout when misfire guards are active so every
block is seen.

### description-fidelity

The harness assembles a skill listing — every skill's `name` and `description`
frontmatter — and builds one `claude -p` prompt per case: the listing, the
case query, and an instruction to name the single skill that should handle the
query, or `none`. It runs from an empty project (no skills symlinked) so
`claude -p` answers the question rather than auto-dispatching. The reply is
parsed to one skill name; that name is scored against `should_trigger` and
`also_not_triggered` exactly as a runtime fire is — a reply naming a guarded
adjacent skill is an `also_not_triggered` violation.

This path is plain question-answering, not skill auto-dispatch, so it is
unaffected by the headless auto-trigger limitation that makes `runtime`
unreliable for advisory skills.

### behavioral-dispatch

The harness runs `claude -p <query> --output-format stream-json` against a
throwaway project whose `.claude/skills/` is symlinked to the working-tree
skills — so the `subagent-delegation` skill (or any other skill under test) is
present and shaping the model — and whose working tree is seeded from
`evals/fixtures/dispatch-project/`, a small multi-file Python project with a
real import graph. The detector watches for:

```
content_block_start → content_block.type == "tool_use" AND name IN ("Agent", "Task")
```

`Agent` is the dispatch-tool name on Claude Code >=2.1.191; earlier versions
used `Task`. Both are matched so the harness stays correct across a version
boundary — confirmed by capturing a live `claude -p` stream: the model emits
`"Agent"` even when the `system/init` tools list still advertises `"Task"`.
"Fired" means any `Agent` or `Task` call occurred; there is no payload field
to match. `also_not_triggered` is not used in this method.

**`should_trigger: true`** means the scenario should cause the model to delegate.
**`should_trigger: false`** means the scenario should be handled inline.

**Instrument warming — cold path (default).** Each sample injects a mid-session
handoff via `--append-system-prompt` that establishes the model as an orchestrator
partway through a multi-step job: several turns done, partial results in hand, more
steps queued, context budget growing. This restores the orchestrator stance that makes
delegation rational — without a real prior session. The handoff sets *pressure and
history* only; the per-case query drives the actual delegate-vs-inline decision.

**Instrument warming — warm path (`--warm-dispatch`).** Opt-in mode that physically
fills the context window instead of asserting it. Mechanism:

1. **Prime once** (shared across all cases and all K samples): run a single `claude -p`
   with `--session-id <uuid>` against the dispatch project, using a prompt that
   instructs the model to actually read `components.py`, `renderer.py`, `layout.py`,
   and `logs/render.log` via the `Read` tool and log anomalies. Real tool-call history
   lands in the session file.
2. **Fork per sample**: each sample runs `claude -p <case-query> --resume <uuid>
   --fork-session --output-format stream-json …`. `--fork-session` assigns each
   parallel sample its own new session ID so K concurrent resumes never corrupt the
   immutable primed base.

Cost: **1 priming invocation + K × num_cases fork invocations** — cheaper than priming
once per sample because priming is shared. The priming prompt **forbids delegation**
(`Do NOT spawn any subagents`) — a priming turn that delegates fills a subagent's
context, not the parent's, defeating the warm-up purpose.

**Session cleanup.** Session files are stored externally at `~/.claude/projects/<hash>/`
where `<hash>` is the dispatch project's absolute path with `/` replaced by `-`. The
harness cleans this directory in its `finally` block for both warm and cold runs (cold
runs also accumulate session files that `shutil.rmtree` on the tempdir does not reach).

**What behavioral-dispatch actually measures.** Native delegation propensity under
a physically-warmed context window — *not* skill-body efficacy. For a concrete task
query the model never invokes the `subagent-delegation` Skill tool, so the skill body
never loads and cannot shape the decision. A 10/10 DELEGATE rate validates the warm
priming mechanism (the harness correctly fills the context and the model reads it as
an orchestration context), not the skill rules.

**Residual limitation.** `--append-system-prompt` (cold path) *asserts* a large prior
context; the real context window stays small. If the model's delegation decision is
driven by physical token accounting rather than stance, cold warming will not move it.
Use `--warm-dispatch` to test with a physically-filled window. If DELEGATE cases still
fire < 50% at K=30 even under `--warm-dispatch`, conclude "headless behavioral-dispatch
is structurally too cold for this skill even warmed" and document it — do not record as
a skill regression.

**Queries behavioral-dispatch cannot measure.** Concrete known-target relay/lookup
queries ("Show me X", "Find Y and list it") elicit direct retrieval regardless of
context warmth — the model reads them as bounded concrete tasks, not delegation-worthy
sweeps. Do not author DELEGATE cases of this shape expecting them to fire; they produce
a permanent 0/10 that reads as a skill regression but reflects only the query framing.

**Case-authoring note.** Even with warming, INLINE cases are the easier arm
(the model still naturally inlines short-context reasoning); DELEGATE cases
are the load-bearing signal. Write DELEGATE scenarios broad enough that
delegation is clearly warranted even when context pressure is asserted rather
than physically present (multi-file sweeps, exploratory mapping,
cross-module correlation tasks).

### disposition-fidelity

**Two-layer model.** A skill's governing rule can fail in two different ways:
it can be deleted outright, or it can be reworded until it no longer drives
the correct disposition. These need different guards:

- **Layer 1 — deterministic anchor-presence test** (in the normal pytest
  suite, `claude/.claude/skills/tests/test_skills.py::test_disposition_rule_anchors_present`).
  Zero-flake, zero-cost, runs in CI. Asserts each rule's
  `<!-- DISPOSITION_RULE:<name> start/end -->` anchor block exists and encloses
  non-trivial text — catches deletion, not rewording.
- **Layer 2 — this method.** Manual-cadence, not continuous (see the runtime
  cost note below). Catches the subtler regression Layer 1 can't: the rule is
  present but no longer efficacious.

**Mechanism.** Per case, per sample:

1. **Baseline (no-guidance control)** — a neutral, skill-specific task frame
   (says nothing about the rule under test) plus the case's scenario. `claude -p`
   reviews it and states a disposition.
2. **Treatment** — the same neutral frame and scenario, plus the rule's text
   extracted *live* from the current SKILL.md via its `DISPOSITION_RULE` anchor.
3. **Judge** — a second `claude -p` call classifies each review's disposition
   as `BLOCKING` or `PERMISSIVE` against the case's `judge_rubric`.

Isolating the rule against a neutral frame (rather than the whole skill
section) removes the surrounding pro-strictness skill text that would
otherwise make the baseline block regardless, keeping baseline a genuine
no-guidance control.

**Gate.** Routine `PASS` is `treatment_block_rate >= 0.8` over non-excluded
treatment samples — a correct rule drives blocking near-always, so 0.8 (not
0.5) catches a regression from ~0.95 down to ~0.55 that a lower bar would let
pass forever. `baseline_block_rate` is diagnostic, not gating: it prints a
non-gating drift alarm at `>= 0.3` ("re-author fixture — control now blocks on
its own"), catching silent fixture rot a static `note` can't. A judge call that
times out, errors, or returns neither label excludes that sample from its
arm's denominator rather than folding it into a label.

**Fixture discrimination is validated once, at authoring time, not per
routine run** — re-run a new or edited fixture at `--samples 50` and confirm
`baseline_block_rate < 0.3` AND `treatment_block_rate >= 0.8` before trusting
it; record the measured baseline in the case's `note` field. If a fixture
doesn't separate, make its benign framing more tempting, not its detection
more subtle — disposition-fidelity fixtures assume detection is trivial and
isolate the disposition question. Watch for a specific failure mode: a
scenario where the author *self-justifies* removing a safety check ("it's
fine because X") tends to saturate the baseline arm regardless of how benign
the justification sounds — Claude treats that rhetorical pattern skeptically
on priors, independent of any codified rule, so it doesn't isolate the rule's
marginal effect. A scenario mined from a real session (where the weakening
wasn't necessarily narrated as an intentional, defended choice) is more
likely to discriminate than a hand-authored one.

**Runtime cost.** ~4 `claude -p` calls per sample (baseline review + judge,
treatment review + judge), so `K` samples × 2 cases is minutes-scale — dozens
of subprocess spawns per run. Manual-only; do not add this to any CI-like
routine.

**Prompt size assumption.** Each review/judge prompt (frame + scenario file
contents + extracted rule text, or rubric + review output) is passed as a
single `argv` element to `claude -p` — there is no length check or
truncation. Keep `scenario_file` contents in the low single-digit KB, far
under any OS `ARG_MAX`. A `scenario_file` pointing at a large real diff or
plan could hit that limit and fail with an opaque `OSError` rather than a
harness-level message — keep fixtures small, or add an explicit size check if
that changes.

**No case currently ships for this method.** Two synthetic seed fixtures were
tried and measured non-discriminating — see the tracking issue for
authoring real ones from live transcripts. The schema below is for whoever
authors the first case.

**Case-file schema** (`disposition-cases.json`):

```json
{
  "skill_name": "code-review",
  "method": "disposition-fidelity",
  "cases": [
    {
      "id": "<short-slug>",
      "scenario_file": "evals/fixtures/disposition/<your-scenario>.md",
      "rule_anchor": "code-review-defer-invariant",
      "judge_rubric": "...",
      "note": "..."
    }
  ]
}
```

- `scenario_file`: path (relative to repo root) to the fixture the model reviews.
- `rule_anchor`: the `DISPOSITION_RULE:<name>` anchor to extract from the
  skill's SKILL.md for the treatment arm.
- `judge_rubric`: the text the judge classifies each review's disposition
  against — written to match the scenario precisely.
- `note`: records the one-time authoring-time discrimination validation
  (measured baseline/treatment rates) for drift audit.

The `method` schema, the classification-answer and disposition-answer
parsers, the anchor extractor, and the stream-json detectors (runtime and
behavioral-dispatch) are unit-tested offline (synthetic inputs, no `claude -p`)
in `claude/.claude/skills/tests/test_trigger_detector.py`; the fixtures live in
`evals/fixtures/`.

## Linting

CI lints `claude/.claude/` only (`ruff check claude/.claude/`). For `evals/`:

```bash
ruff check evals/
```

Run locally before committing harness changes.

## Subagent model resolution experiment

`measure_subagent_model_resolution.py` is a separate, one-off instrument —
not a skill eval. It launches short headless `claude -p` runs across an
explicit (session model × permission mode × dispatch shape) matrix, then
reads each run's `subagents/agent-*.jsonl` + `.meta.json` sidecars to report
requested vs. observed subagent model, so the plan-mode subagent
model-resolution question can be settled by measurement instead of corpus
inference. Full design, hypotheses, and the run matrix live in
[`.claude/plans/plan-mode-model-resolution-experiment.md`](../.claude/plans/plan-mode-model-resolution-experiment.md) —
this is a pointer, not a restatement.

```bash
# Print the seven-run matrix without launching anything:
python evals/measure_subagent_model_resolution.py --list

# Run a single matrix cell (1-7):
python evals/measure_subagent_model_resolution.py --run 1

# Run the full matrix in order (stops if run 1's self-check fails):
python evals/measure_subagent_model_resolution.py --all --json results.json
```

Tests: `evals/test_measure_subagent_model_resolution.py`, fixture-based, no
live sessions.
