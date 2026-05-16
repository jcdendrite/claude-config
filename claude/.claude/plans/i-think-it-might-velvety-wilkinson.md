# Skill Trigger-Fidelity Test Harness

## Context

`claude-config` has grown to ~23 model-invokable skills, used across many
client engagements and shipped to every stow user on `git pull`. The only
automated coverage today is `claude/.claude/skills/tests/test_skills.py` —
**static** frontmatter contract tests that assert `TRIGGER when:` /
`DO NOT TRIGGER when:` substrings *exist*. Nothing verifies those triggers
actually *fire*: whether a skill auto-invokes when it should, stays silent
when it shouldn't, and doesn't lose contention to an adjacent skill.

A prior decision (memory `feedback_ci_eval_harness_tradeoffs.md`,
`plugins/skill-review/skills/skill-review/REFERENCES.md`) rejected an eval
harness on three grounds: CI security, CI budget, and probabilistic
flakiness. The user is **deliberately revisiting** that decision. The
REFERENCES.md rejection rested on "this repo is a single-user skill catalog
— mis-fires are observed in [your own] sessions"; that premise no longer
holds (many skills, many client engagements, mis-fires happen in sessions
the owner never sees). REFERENCES.md itself anticipated this: *"If a future
contribution requires quantified trigger-accuracy work…"*.

The reframe that resolves all three objections: the harness runs **locally
and manually, never in CI, never a required check**. Locally, `claude -p`
uses the user's existing Claude subscription auth — no `ANTHROPIC_API_KEY`,
no per-token bill. Output is a **human-read pass-rate report**, not a
red/green gate — so probabilistic flakiness becomes *data* ("triggered
2/3"), not a broken signal.

## Standard practice (researched, not guessed)

Anthropic ships the canonical implementation: `skill-creator`'s
`scripts/run_eval.py` (repo `anthropics/skills`, `skills/skill-creator/`).
This plan **adapts `run_eval.py`'s proven mechanism** rather than inventing
one. Verified facts from that source:

- **Invocation:** `claude -p <query> --output-format stream-json --verbose
  --include-partial-messages`, as a subprocess. Uses the session's Claude
  Code auth — no separate API key. The `CLAUDECODE` env var is stripped to
  permit nesting `claude -p` inside a Claude Code session.
- **Detection:** parse the JSON-lines stream for a `stream_event` →
  `content_block_start` whose `content_block.type == "tool_use"` and
  `name` in `("Skill", "Read")`; accumulate `content_block_delta` →
  `input_json_delta.partial_json`; the target skill name appearing in the
  accumulated input means it triggered. Early-return on the first decisive
  event — no need to let the skill execute.
- **Synthetic command files:** `run_eval.py` does **not** depend on skills
  being installed. It writes a temp file into `<project>/.claude/commands/`
  carrying the `description` under test, so it appears in `available_skills`.
  Triggering is decided "based solely on the title and description" — the
  SKILL.md body is irrelevant to triggering — so a command file faithfully
  reproduces the trigger surface.
- **Sampling:** each query runs `runs_per_query` times (default **3**);
  pass = `trigger_rate >= trigger_threshold` (default **0.5**) for
  should-trigger cases, inverse for should-not.
- **Case shape:** the eval set is a list of `{"query", "should_trigger"}`.
- **Co-located cases:** skill-creator stores evals at `evals/evals.json`
  *within the skill directory* (`references/schemas.md`). Co-location next
  to `SKILL.md` is the documented standard.

Note: this is `run_eval.py` (the trigger test), **not** `run_loop.py` (the
description *optimizer* that mutates `description` over 5 iterations with a
60/40 train/test split). The optimizer is an authoring tool, out of scope —
see "Out of scope".

## What we build

A standalone, repo-native batch harness that, for a set of co-located
per-skill trigger-case files, runs `claude -p` and reports per-skill,
per-case trigger fidelity. It tests the **real working-tree skill
descriptions** in **real cross-skill competition**.

### 1. Directory & files (harness code)

Repo-root **`evals/`** — outside the `claude/` stow package (so it never
symlinks into `~/.claude/`). The harness script itself is never collected by
`pytest claude/.claude/` (collection is driven solely by the path argument;
`pyproject.toml` sets no `testpaths`) — so CI never *runs* the harness. Its
pure detector logic is unit-tested separately and CI-collected (see §7).

```
evals/
  README.md                  # what it is, the local-only/no-CI reframe, how to run/read
  run_trigger_evals.py       # the harness — adapted from skill-creator run_eval.py
  fixtures/                  # captured stream-json transcripts for the detector unit test
```

Single script (~250 lines, mirroring `run_eval.py`'s size) — not a package.
Python 3.12, matching the repo. **Dependency:** the harness parses SKILL.md
YAML frontmatter (`name`, `description` — which uses both inline and folded
`>` styles across this repo's skills — `user-invocable`,
`disable-model-invocation`); regex extraction of folded scalars is unsafe,
so it uses **PyYAML**. PyYAML is a local-only dev dependency for `evals/`,
documented in `evals/README.md`; since CI never runs the harness, CI's
dependency set (`pytest`, `ruff`) is unchanged. The repo-root `evals/` is not
linted by CI's `ruff check claude/.claude/`; `evals/README.md` notes
contributors run `ruff check evals/` locally.

### 2. Trigger-case file format & location

**Co-located:** `claude/.claude/skills/<name>/evals/trigger-cases.json`.

The `evals/`-subdir-within-the-skill *directory* is skill-creator's
documented convention (`references/schemas.md`: evals live at `evals/…`
inside the skill dir). The *filename* is this plan's choice: skill-creator's
`evals/evals.json` holds its **output-quality** eval (with an `expectations`
schema); its **trigger** eval set is a separate file with no canonical name
(passed via `run_eval.py --eval-set`). `trigger-cases.json` names this
harness's trigger set distinctly so it is not mistaken for the
output-quality schema. These files sit inside the `claude/` stow package and
will symlink into `~/.claude/skills/<name>/evals/` — accepted as the cost of
the co-location convention (they are small; the `HOOK_TEST_FIXTURE` blocks
embedded in `SKILL.md` already stow test fixtures).

Schema (skill-creator's `{query, should_trigger}` plus two additive fields):

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
      "id": "vs-skill-review-md-only",
      "query": "Review the edits I made to plan-it/SKILL.md.",
      "should_trigger": false,
      "also_not_triggered": ["plan-review"]
    }
  ]
}
```

- `id` — stable case identifier for the report (additive vs skill-creator).
- `should_trigger` — standard.
- `also_not_triggered` — optional; names adjacent skills that must **not**
  fire on this query (additive — skill-creator has no cross-skill notion).
  A case with this field passes only if the target behaves as expected
  **and** none of the listed skills fired in the same run. This is the
  cross-skill-confusion test, justified by the repo's many adjacent-skill
  pairs (`test-conventions`↔`test-evaluation`, `code-review`↔`skill-review`,
  `claude-hook-review`↔`review-permissions`).

### 3. Runner mechanics

**Skill-loading approach — resolve first, as a spike.** There are two ways
to put the skills under test in front of `claude -p`, and the plan does not
default to skill-creator's blindly:

- **(A) Real skills (preferred).** Run `claude -p` from a throwaway temp
  project whose `.claude/skills/` is symlinked to the working-tree
  `claude/.claude/skills/` (plugin skills likewise). The skills load as
  *real skills* in *real mutual competition*; the detector reads a genuine
  `Skill` invocation. No fidelity question. Requires confirming that
  `claude -p` loads skills from a project's `.claude/skills/` (or via
  `--add-dir`).
- **(B) Synthetic command files (fallback).** skill-creator's `run_eval.py`
  mechanism — write each skill's `description` into
  `<temp-project>/.claude/commands/<name>-<runid>.md`. Works with no
  symlink, but measures a *command* entry, not an installed skill.

**Why (A) over (B):** `run_eval.py` synthesizes because it is a description
*optimizer* — it must test candidate descriptions that are *not* the
installed ones. This harness only ever tests the working-tree descriptions
as-is, so it does not need synthesis; (A) gives real competition for free
and eliminates the command-vs-skill fidelity gap (former Risk #2) entirely.
**First implementation step:** spike both — confirm which mechanism makes
`claude -p` load the working-tree skills and emit a detectable `Skill`
event — adopt (A) if it works, fall back to (B) if not. The steps below are
written for (A); under (B), substitute synthesized command files for the
symlinked skills directory.

Per harness run:

1. **Build the temp project.** A throwaway dir with `.claude/skills/`
   symlinked to working-tree `claude/.claude/skills/` (plugin skills
   likewise) and a minimal `.claude/settings.json` carrying none of this
   repo's workflow hooks. Skills with `disable-model-invocation: true`
   (`handoff`, `read-docx-comments`) cannot auto-trigger — the harness does
   not *score* them, but they stay present as real competition. Removed in
   a `finally` block.
2. **Run each case × K samples.** `claude -p <query> --output-format
   stream-json --verbose --include-partial-messages --model sonnet`, with
   `cwd` = the temp project (so this repo's *project-level* `.claude`
   workflow hooks never load), `CLAUDECODE` stripped (permits nesting
   inside a Claude Code session), real `HOME` (subscription auth, no API
   key). Default `K=3`.
3. **Detect** with `run_eval.py`'s stream-json algorithm
   (`content_block_start` → `tool_use` with `name` in `("Skill","Read")`,
   accumulate `input_json_delta`), extended to record *which* skill name
   fired (not just a boolean) so `also_not_triggered` is checkable.
   Early-terminate the subprocess on the first decisive event; per-sample
   wall-clock timeout (~30 s, skill-creator default).
4. **Aggregate** per case: `trigger_rate = triggers / K`. Pass =
   `should_trigger ? rate >= 0.5 : rate < 0.5`, **and** no
   `also_not_triggered` skill fired in any sample.
5. Samples run with bounded parallelism (`ProcessPoolExecutor`, `--workers`
   default 4 — lower than skill-creator's 10, to stay gentle on local
   subscription rate limits).

Needs **no `--dangerously-skip-permissions`**: only the *attempted* `Skill`
tool_use is read, never executed.

### 4. Reporting

Terminal report (no file written unless `--json <path>`); side-effect-free.

```
Skill trigger-fidelity   model=sonnet  K=3   2026-05-15

code-review                                              5/5
  post-implementation-handoff   triggers      3/3   PASS
  cosmetic-typo-fix             does-not-trig 0/3   PASS
  vs-skill-review-md-only       does-not-trig 1/3   FAIL  (plan-review fired 2/3)
...
summary: 3 skills, 11 cases | pass 9/11 | review the 2 FAIL cases above
```

Exit code is `0` regardless of pass rates (it is a measurement, not a gate);
non-zero is reserved for harness errors (missing `claude` binary, malformed
case JSON, subprocess crash). A FAIL on an `also_not_triggered` case names
which adjacent skill stole the trigger — the actionable signal.

### 5. Invocation UX

```
python evals/run_trigger_evals.py                 # all skills with a trigger-cases.json
python evals/run_trigger_evals.py --skill code-review   # one skill (repeatable)
python evals/run_trigger_evals.py --skill code-review --samples 5 --model opus
```

Flags: `--skill` (repeatable), `--samples K`, `--model {sonnet,opus}`,
`--workers N`, `--json <path>`, `--verbose`. **Default model: Sonnet** —
chosen not only for lower subscription usage but because Sonnet is a
stricter classifier than Opus, making it a better stress test of whether a
skill's *prose* triggers reliably. `--model opus` available for spot
comparison.

### 6. Pilot scope (this PR)

Per the user's decision — **harness + pilot skills**, not full coverage:

- Build `evals/run_trigger_evals.py` + `evals/README.md`.
- Author `trigger-cases.json` for **3 pilot skills** that exercise every
  case kind: `code-review` (rich positive + negative), and the
  `test-conventions` ↔ `test-evaluation` adjacency pair (the repo already
  has a parametrized adjacency contract for this exact pair) — each pilot
  skill's file carrying at least one cross-skill `also_not_triggered` case.
- Prove stream-json detection works end-to-end on these 3 before scaling.
- Remaining ~20 skills get `trigger-cases.json` in follow-up PRs.

### 7. Workflow integration & docs to update

- **`test_skills.py`** — add one *static*, **discovery-based** test
  `test_trigger_cases_files_well_formed`: glob
  `claude/.claude/skills/*/evals/trigger-cases.json` and
  `plugins/*/skills/*/evals/trigger-cases.json`; for each file *found*,
  assert it parses as JSON, `skill_name` equals the parent skill directory
  name, `cases` is non-empty, and every case has a string `query` and a
  boolean `should_trigger`. No hardcoded skill list — this avoids a second
  maintenance registry alongside the existing `COMMAND_SKILLS`, and the
  test auto-extends as `trigger-cases.json` files are added in follow-ups.
  It validates shape only, never invokes a model — free and CI-safe.
- **Detector unit test** — `claude/.claude/skills/tests/test_trigger_detector.py`
  (collected by CI). Add `evals` to `pyproject.toml`'s pytest `pythonpath`
  so the test can `import run_trigger_evals`. Feed the detector committed
  captured `stream-json` transcripts from `evals/fixtures/` (one
  skill-fired, one no-trigger, one cross-skill mis-fire) and assert it
  reports the correct fired-skill name. Deterministic, no `claude -p` call.
  This CI-tests the harness's highest-risk component (per Risks §) without
  CI ever running the harness — and satisfies the "add enforcement with the
  convention" preference.
- **`plugins/skill-review/skills/skill-review/REFERENCES.md`** — the
  "Why skill-creator is disabled" section currently argues the eval-harness
  conditions "don't apply here." Update it: trigger-fidelity evals **are**
  now adopted, as a standalone local harness adapting `run_eval.py`'s
  mechanism; the `skill-creator` *plugin* stays disabled for the unchanged
  voice/length-conflict reasons (its 500-line bodies, first-person voice).
  Distinguish "adopted the mechanism" from "enabled the plugin."
- **`CONTRIBUTING.md`** — short "Trigger-fidelity evals" subsection: what
  the harness is, that it is local/manual/never-CI, how to run it per-skill
  when editing a skill's `TRIGGER` block.
- **`docs/skills.md`** — one sentence near the architecture notes pointing
  at `evals/`.
- **`skill-review/SKILL.md`** — one line in the checklist/trigger section:
  after a `TRIGGER`-block change, run
  `python evals/run_trigger_evals.py --skill <name>` and confirm rates
  held. A documentation cross-reference, **not a new hook** — a hook running
  `claude -p` on every skill commit would reintroduce exactly the
  cost/flakiness the local-only reframe avoids. Note: editing
  `skill-review/SKILL.md` stages a `SKILL.md`, so `require-skill-review.sh`
  will gate the commit — running `/skill-review` on the diff is an expected
  step, not a surprise.
- **PR packaging** — the plan file (`.claude/plans/…`) and all
  implementation ship in the **same PR**, so reviewers see the plan and
  what it produced together.
- **Memory** — after execution, update
  `feedback_ci_eval_harness_tradeoffs.md`: the *CI* objections still hold,
  but the blanket "don't propose a `claude -p` harness" is superseded by
  the adopted local harness. (Memory write is deferred until execution —
  not editable from plan mode.)

### 8. Out of scope

- **CI wiring of any kind — rejected.** Not a required check, not an
  optional job, not `workflow_dispatch`. All three original objections
  (security of permission bypass on a public repo, per-token API-key
  billing with no subscription auth in CI, flaky model-classification as a
  red/green signal) apply only to CI. Local runs have none of them. The
  harness stays 100% out of `.github/workflows/`.
- **The description optimizer (`run_loop.py` equivalent)** — auto-mutating
  `description` text. That is an authoring tool; this is a test harness.
- **Output-quality / benchmark evals** (skill-creator's `expectations`
  schema, with-skill vs without-skill) — this harness measures *triggering*
  only.
- **No skill execution, no `--dangerously-skip-permissions`** anywhere.
- **Enabling the `skill-creator` plugin** — its voice/length conventions
  conflict with this repo; only its `run_eval.py` *mechanism* is adopted.

## Verification

End-to-end, run locally after stowing is irrelevant (the harness reads
working-tree `SKILL.md` directly, not `~/.claude/`):

1. `python evals/run_trigger_evals.py --skill code-review --verbose` —
   confirm it spawns `claude -p`, that stream-json detection fires, and the
   report shows sensible `triggers/K` for each `code-review` case.
2. Confirm a deliberately-mismatched case fails: temporarily add a case
   whose `query` is obviously unrelated with `should_trigger: true`, verify
   it reports FAIL, then remove it.
3. Confirm cross-skill detection: run the `test-conventions` /
   `test-evaluation` pair; verify an `also_not_triggered` case reports which
   sibling fired when it mis-fires.
4. `pytest claude/.claude/` — confirm the new static
   `test_pilot_skills_have_trigger_cases_file` passes and nothing else
   regressed; confirm `evals/` is **not** collected.
5. `ruff check claude/.claude/` (CI scope) unaffected; `ruff check evals/`
   clean locally.
6. Sanity-check run cost: a 3-skill pilot run is ~11 cases × 3 samples ≈ 33
   `claude -p` calls — single-digit minutes, zero marginal dollars on
   subscription auth.

## Critical files

- `evals/run_trigger_evals.py` *(new)* — harness; adapts `run_eval.py`'s
  stream-json detection (the highest-risk component — build it against a
  real captured stream first, do not write from assumption).
- `evals/README.md` *(new)* — usage + the local-only/no-CI rationale.
- `claude/.claude/skills/code-review/evals/trigger-cases.json` *(new)*
- `claude/.claude/skills/test-conventions/evals/trigger-cases.json` *(new)*
- `claude/.claude/skills/test-evaluation/evals/trigger-cases.json` *(new)*
- `evals/fixtures/*.jsonl` *(new)* — captured `stream-json` transcripts for
  the detector unit test.
- `claude/.claude/skills/tests/test_trigger_detector.py` *(new)* —
  CI-collected detector unit test (pure, deterministic, no `claude -p`).
- `claude/.claude/skills/tests/test_skills.py` *(modify)* — add the
  discovery-based `trigger-cases.json` shape test.
- `pyproject.toml` *(modify)* — add `evals` to pytest `pythonpath` so the
  detector test can import the harness module.
- `plugins/skill-review/skills/skill-review/REFERENCES.md` *(modify)* —
  reflect the adopted harness.
- `CONTRIBUTING.md`, `docs/skills.md`, `skill-review/SKILL.md` *(modify)* —
  short pointers.

## Risks / least-certain parts

1. **Skill-loading mechanism (highest risk)** — whether `claude -p` loads
   skills from a temp project's symlinked `.claude/skills/` (approach A) is
   unverified. The §3 spike resolves it before any case files are authored;
   approach B is the documented fallback. The detector unit test (§7) pins
   the parsing half regardless of which loading approach wins.
2. **stream-json event shape** — the detector depends on the exact
   `content_block_start` / `input_json_delta` structure. Mitigated by
   copying `run_eval.py`'s verified algorithm verbatim and by the
   fixture-backed detector unit test; capture one real transcript first.
3. **Synthetic-prompt realism** — on-the-nose queries make positives pass
   trivially. The `also_not_triggered` confusion cases carry the real
   signal; `README.md` must instruct that queries read like genuine user
   turns, not keyword bait.
4. **Classifier drift** — pass rates compare only within the same `--model`
   and model version; the report header prints the model and `README.md`
   says so.
