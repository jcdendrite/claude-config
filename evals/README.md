# Skill Evals

A local harness that measures each skill's `trigger-cases.json` against its
declared TRIGGER / DO NOT TRIGGER conditions. It runs one of two measurement
methods per skill — `runtime` or `description-fidelity` — and reports a
per-case pass rate. See [Measurement methods](#measurement-methods) for which
method fits which skill.

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

Each `trigger-cases.json` declares a `method`. `run_skill_evals.py` runs all
three in one invocation, routing per case file and labelling each skill's mode
in the report:

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

Headless `claude -p` does not reliably auto-trigger every skill — advisory
skills the model is not pushed to invoke (and `user-invocable: false` skills)
under-trigger regardless of description quality, so `runtime` measurement
returns a false zero for them. Those skills use `description-fidelity` instead.

The three methods measure genuinely different properties and are not
interchangeable. `runtime` is strictly more faithful when available — it
observes the real dispatch decision — so a skill that *can* trigger headlessly
keeps `runtime` rather than being downgraded to classification.

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
content_block_start → content_block.type == "tool_use" AND name == "Task"
```

`Task` is the real dispatch-tool name (confirmed from the `system/init` event's
`"tools"` list in committed fixtures — the "Agent" label is an
interactive-display alias only). "Fired" means any `Task` call occurred; there
is no payload field to match. `also_not_triggered` is not used in this method.

**`should_trigger: true`** means the scenario should cause the model to delegate.
**`should_trigger: false`** means the scenario should be handled inline.

**Instrument warming.** Each sample injects a mid-session handoff via
`--append-system-prompt` that establishes the model as an orchestrator
partway through a multi-step job: several turns done, partial results in
hand, more steps queued, context budget growing. This restores the
orchestrator stance that makes delegation rational — without a real prior
session. The handoff sets *pressure and history* only; the per-case query
drives the actual delegate-vs-inline decision.

**Residual limitation.** `--append-system-prompt` *asserts* a large prior
context; the real context window stays small. If the model's delegation
decision is driven by physical token accounting rather than stance, warming
via system prompt will not move it. If DELEGATE cases fire < 50% at K=30
after warming, conclude "headless behavioral-dispatch is structurally too
cold for this skill even warmed" and document it — do not record as a skill
regression. The escalation path is `--resume <session-id>`: a real prior
turn whose tool-call history physically fills the window, at the cost of
2× invocations per sample and per-sample unique session IDs.

**Case-authoring note.** Even with warming, INLINE cases are the easier arm
(the model still naturally inlines short-context reasoning); DELEGATE cases
are the load-bearing signal. Write DELEGATE scenarios broad enough that
delegation is clearly warranted even when context pressure is asserted rather
than physically present (multi-file sweeps, exploratory mapping,
cross-module correlation tasks).

The `method` schema, the classification-answer parser, and the stream-json
detectors (both runtime and behavioral-dispatch) are unit-tested offline
(synthetic inputs, no `claude -p`) in
`claude/.claude/skills/tests/test_trigger_detector.py`; the fixtures live in
`evals/fixtures/`.

## Linting

CI lints `claude/.claude/` only (`ruff check claude/.claude/`). For `evals/`:

```bash
ruff check evals/
```

Run locally before committing harness changes.
