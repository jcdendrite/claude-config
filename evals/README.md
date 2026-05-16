# Skill Trigger-Fidelity Evals

A local harness that measures how reliably each skill auto-triggers (or stays
silent) when given a test query. Answers: "does this skill's TRIGGER prose
actually cause the model to invoke it?"

## Why local only — never CI

The harness runs `claude -p` as a subprocess using your existing Claude Code
subscription auth. This means:

- **No `ANTHROPIC_API_KEY` required.** Max-plan OAuth auth is used automatically.
- **No per-token charge** beyond your subscription.
- **No CI wiring.** Trigger-fidelity is a probabilistic model classification, not
  a deterministic computation. A single-sample binary pass/fail produces a
  flaky CI signal; the harness treats output as a human-read pass-rate report
  (`triggered 2/3`), not a gate. Running it in CI would also require
  `--dangerously-skip-permissions` on a public repo — a security footgun.

## Usage

```bash
# Install the one local dev dependency (not needed by CI):
pip install pyyaml

# Run all skills that have a trigger-cases.json:
python evals/run_trigger_evals.py

# Run one skill:
python evals/run_trigger_evals.py --skill code-review

# Run the test-conventions / test-evaluation adjacency pair:
python evals/run_trigger_evals.py --skill test-conventions --skill test-evaluation

# Tune sampling:
python evals/run_trigger_evals.py --skill code-review --samples 5

# Spot-compare with Opus:
python evals/run_trigger_evals.py --skill code-review --model claude-opus-4-7

# Verbose (prints each case as it completes):
python evals/run_trigger_evals.py --verbose

# Write machine-readable results:
python evals/run_trigger_evals.py --json /tmp/results.json
```

Default model: `claude-sonnet-4-6`. Sonnet is a stricter classifier than Opus,
making it a better stress test of whether a skill's TRIGGER prose is specific
enough. Pass rates are model-version-scoped — the report header prints the
model used.

## Reading the output

```
Skill trigger-fidelity   model=claude-sonnet-4-6  K=3   2026-05-15

code-review                                              4/5
  code-written-commit-pending         triggers      3/3   PASS
  explicit-user-code-review-request   triggers      2/3   PASS
  cosmetic-typo-fix                   does-not-trig 0/3   PASS
  plan-only-no-code                   does-not-trig 1/3   FAIL  (plan-review fired 2/3)
  vs-skill-review-skill-md-only       does-not-trig 0/3   PASS

summary: 1 skills, 5 cases | pass 4/5 | review the 1 FAIL cases above
```

- **PASS**: trigger rate ≥ 50% for should-trigger cases; < 50% for should-not.
  Plus no `also_not_triggered` skill fired in any sample.
- **FAIL + parenthetical**: adjacent skill stole the trigger — the actionable signal
  for tightening the DO NOT TRIGGER prose.
- Exit code is always `0` — measurement, not a gate.

## Adding trigger cases

Co-locate a `trigger-cases.json` file in the skill's `evals/` subdirectory:

```
claude/.claude/skills/<name>/evals/trigger-cases.json
```

Schema:

```json
{
  "skill_name": "code-review",
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

- `id`: stable label shown in the report.
- `should_trigger`: `true` = the skill must fire; `false` = it must stay silent.
- `also_not_triggered`: optional. Adjacent skills that must **not** fire on this
  query. A FAIL names which adjacent skill stole the trigger.

**Write realistic queries.** On-the-nose queries (keyword-bait like "trigger
code-review now") pass trivially. The `also_not_triggered` confusion cases carry
the real signal — write queries that read like genuine user turns where the
boundary between adjacent skills is ambiguous.

## How detection works

The harness runs `claude -p <query>` from a throwaway project whose
`.claude/skills/` is symlinked to the working-tree `claude/.claude/skills/`.
Real skills compete in real mutual context. When the model decides to invoke a
skill, it calls the `Skill` tool with the skill name in the input JSON. The
harness reads the stream-json output and looks for:

```
content_block_start → content_block.type == "tool_use" AND name in ("Skill", "Read")
content_block_delta → input_json_delta.partial_json (accumulated)
content_block_stop  → check if skill_name appears in accumulated JSON
```

Early-terminates on the first decisive event. Detection logic is unit-tested
via committed stream-json fixtures in `evals/fixtures/` — see
`claude/.claude/skills/tests/test_trigger_detector.py`.

## Linting

CI lints `claude/.claude/` only (`ruff check claude/.claude/`). For `evals/`:

```bash
ruff check evals/
```

Run locally before committing harness changes.
