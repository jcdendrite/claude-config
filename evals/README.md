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
- **No per-token charge** beyond your subscription. `claude -p` accepts an
  `ANTHROPIC_API_KEY` and would authenticate fine in CI — cost, not
  reachability, is the actual reason this stays local:
  - Every sample is a full headless session.
  - Cost scales as K samples × cases per skill.
  - `disposition-fidelity` adds about four `claude -p` calls per sample on
    top of that (see "Runtime cost" below).
  - All of it bills per token, off-subscription.
- **No CI wiring.** Every method (see [Measurement
  methods](#measurement-methods)) produces a probabilistic model
  classification, not a deterministic computation. A single-sample binary
  pass/fail produces a flaky CI signal. The harness treats output as a
  human-read pass-rate report (`triggered 7/10`), not a gate. Running it in
  CI would also require `--dangerously-skip-permissions` on a public repo —
  a security footgun.

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
claude-skills/skills/<name>/evals/trigger-cases.json
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
`.claude/skills/` is symlinked to the working-tree `claude-skills/skills/`.
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

**Session cleanup.** Session files are stored externally at `<config-dir>/projects/<hash>/` (`<config-dir>` means `$CLAUDE_CONFIG_DIR` when set, else `~/.claude`)
where `<hash>` is the dispatch project's absolute path with `/` replaced by `-`. The
harness cleans this directory in its `finally` block for both warm and cold runs (cold
runs also accumulate session files that `shutil.rmtree` on the tempdir does not reach).

**What behavioral-dispatch actually measures.** Native delegation propensity
under a warmed context window, not skill-body efficacy — the model never
calls the Skill tool for a concrete task, so the skill body never loads or
shapes the decision.

**Residual limitation.** Cold warming (`--append-system-prompt`) only asserts
prior context without filling the window; if the model tracks real token
accounting rather than stance, use `--warm-dispatch`, and if DELEGATE still
fires < 50% at K=30 there, document it as a structural cold-harness limit,
not a skill regression.

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
  suite, `claude-skills/skills/tests/test_skills.py::test_disposition_rule_anchors_present`).
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

**Gate.** Routine `PASS` is `treatment_block_rate >= 0.8` (0.8, not 0.5, so a ~0.95→~0.55
regression is caught) over non-excluded treatment samples. `baseline_block_rate`
is diagnostic, not gating: it prints a non-gating drift alarm at `>= 0.3`
(fixture rot — the control now blocks on its own). A timed-out, errored, or
unlabeled judge call excludes that sample from its arm's denominator rather
than folding it into a label.

**Fixture discrimination is validated once, at authoring time, not per
routine run.** Validate a new/edited fixture at `--samples 50`
(`baseline_block_rate < 0.3`, `treatment_block_rate >= 0.8`) and record it in
`note`; if it doesn't separate, make the benign framing more tempting rather
than the detection more subtle. Prefer scenarios mined from real sessions
over hand-authored self-justifying ones — the latter tend to saturate the
baseline arm regardless of framing, since a rule-blind reviewer already
treats an author's self-justified bypass narrative skeptically on priors,
independent of any codified rule.

**Runtime cost.** ~4 `claude -p` calls per sample (baseline review + judge,
treatment review + judge), so `K` samples × 2 cases is minutes-scale — dozens
of subprocess spawns per run. Manual-only; do not add this to any CI-like
routine.

**Prompt size assumption.** Prompts pass unchecked into a single `argv`
element to `claude -p` — keep `scenario_file` contents in the low single-digit
KB to avoid an opaque `OSError` from OS `ARG_MAX`.

**No case currently ships for this method.** Two synthetic seed fixtures were
tried and measured non-discriminating; author real cases by mining live
Claude Code session transcripts for naturally-occurring borderline-disposition
examples (via the `transcript-analysis` skill) rather than hand-authoring
more synthetic ones. The schema below is for whoever authors the first case.

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
in `claude-skills/skills/tests/test_trigger_detector.py`; the fixtures live in
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
inference — testing whether harness plan mode changes which model a
dispatched subagent actually resolves to. Full design, hypotheses, and the
run matrix live in
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

**Known limitation:** this harness's headless `--permission-mode plan` runs do
not reproduce interactive Shift+Tab plan mode's escalation to Opus — see
[`docs/case-studies/plan-mode-model-resolution.md`](../docs/case-studies/plan-mode-model-resolution.md)
for the measured discrepancy and what a valid re-verification requires.

## Review bench

The review bench (`evals/review_bench/`) measures whether a reviewer read-rule change
preserves review quality — recall and adjudicated precision on a curated set
of this repo's own known-defect PRs — before that change is allowed to
merge. This file's own "Why local only — never CI" section above applies
here too, with one addition: `smoke`, `run`, and `judge` each price out a
real reviewer or judge dispatch per sample, not one classification call, so
the cost scales faster (see "Runtime cost" below). The harness needs Python
3.12 or newer, since `review_bench/runner.py` calls `shutil.rmtree(onexc=...)`;
the repository's floor elsewhere is 3.11. It also needs Git 2.32 or newer, the
first release that honors `GIT_CONFIG_GLOBAL`, which the fixture diffs use to
ignore the engineer's git config.

### Usage

```bash
# Mine defect candidates (mine-rounds first -- transcripts age out):
python evals/run_review_bench.py mine-rounds
python evals/run_review_bench.py mine-pr-comments
python evals/run_review_bench.py mine-szz

# Triage every candidate at the [y/N/q] prompt (needs a terminal):
python evals/run_review_bench.py confirm

# Snapshot both frozen arms from production at the freeze commit:
python evals/run_review_bench.py snapshot-arms

# Dry-run one defect against the real CLI, with optional fault injection:
python evals/run_review_bench.py smoke --defect-id <id> --k 2 --inject-fault wrong-agent

# The real campaign (baseline or a later arm's rerun):
python evals/run_review_bench.py run --k 10 --records-dir evals/review_bench/.local/runs

# Judge a completed reviewer campaign:
python evals/run_review_bench.py judge --reviewer-records-path evals/review_bench/.local/runs/<campaign>.jsonl

# Export, then import, the human spot-check:
python evals/run_review_bench.py spot-check export \
  --reviewer-records-path <reviewer.jsonl> --judge-records-path <judge.jsonl>
python evals/run_review_bench.py spot-check import \
  --reviewer-records-path <reviewer.jsonl> --judge-records-path <judge.jsonl> --labels-path <labels.json>

# Freeze the harness (run once, after a passing smoke campaign):
python evals/run_review_bench.py freeze --k 10 --campaign-seed 0 \
  --last-smoke-manifest-hash <hash from smoke's own output> --smoke-full-k 10

# Compute a campaign's verdicts:
python evals/run_review_bench.py analyze \
  --reviewer-records-path <reviewer.jsonl> --judge-records-path <judge.jsonl> --k 10
```

`mine-pr-comments` needs an authenticated `gh` for github.com.

### Mining

Three miners write candidate shortlists under `evals/review_bench/.local/`.
Run them in this order:

- **`mine-rounds`** (source `review-round`) reads this account's own session
  transcripts for a finding in a later `/code-review` round about a file an
  earlier round on the same branch had read. Run it first, because
  transcripts age out.
- **`mine-pr-comments`** (source `pr-comment`) reads the inline review
  comments of the authenticated `gh` login on merged pull requests of this
  repository, and nothing else: no review bodies, no PR conversation comments,
  no other repository. A comment qualifies when the thread holds a
  `respond-pr` reply marked fixed with a commit SHA. The head is the blame of
  the commented line, and the fix is that SHA. The miner is a script with no
  prompt. It resolves `owner/repo` once from `origin` and exits 2 unless
  `origin` is exactly `github.com` and the provider reports the repository
  public, so it publishes only public text. It prints the mined login and a
  count of skipped comments by reason. A body holding a joiner, variation
  selector, or other invisible character is counted as `invisible-characters`,
  and one holding any other control character as `control-characters`. A PR
  head that could not be fetched makes the run exit 2, name the PR numbers, and
  write nothing, so a fetch failure never replaces a shortlist with a partial
  one. A rerun clears a network, auth, or ref-lock failure, not a pull ref the
  remote lacks. A comment on which a git call gave no answer is counted as
  `git-error` and its ID is printed. The run still writes its shortlist, like
  every completed run, so a run with `git-error` skips rewrites it. A rerun
  retries that comment, and one that fails again fails for that comment, not
  for the run. Edit a candidate's description in `.local/` only after the last
  `mine-pr-comments` run, because every completed run rewrites that file.
- **`mine-szz`** (source `szz`) blames the removed or modified non-markdown
  lines of each `fix|bug|regression` commit on first-parent `origin/main`.

Markdown files (skills, rules, `CLAUDE.md`, docs) are eligible through
`review-round` and `pr-comment`. `defects.is_markdown_path` is the one
markdown predicate. `mine-szz` skips markdown paths.

`evals/review_bench/.local/*_candidates.json` is gitignored: `mine-rounds`'
output can carry real session or finding-excerpt text, so never copy its
content into a committed file or another private-project-adjacent surface.
`pr_comment_candidates.json` holds public comment text only.

`confirm` is the engineer's approval step. It reads every `.local/`
candidate that is not yet in `defects.json`, ordered by source (`review-round`,
then `pr-comment`, then `szz`) and then by the miner's own order, so an early
`q` never starves the source whose transcripts age out. It suppresses no
candidate. For each one it checks the schema and, when the candidate has a
description, the description-provenance check against every `.local/`
excerpt. It then prompts `[y/N/q]`. The prompt shows:

- the candidate's ID, source, short SHAs, `fix_date`, path, and commit
  subjects;
- for a `pr-comment`, the comment's `diff_hunk`, bounded in length with the
  end kept because the commented line is its last, and the head path, labeled
  "(head is outside the PR branch)" when the head is not among the PR's own
  commits, or "(PR branch membership unknown)" when the candidate does not
  record that;
- the description, or a note that none exists yet. It prints in full with no
  length cut, one indented line per line of text, ahead of the path;
- the four inclusion fields the engineer's answer accepts: `lens`,
  `lines_exist_at_introducing_head`, `reviewer_could_have_caught_it`, and
  `file_is_markdown`. A field the miner hard-coded rather than computed is
  labeled "unchecked default".

A candidate that shares its head, fix, and path with a candidate from another
source is annotated with that candidate's ID, source, and status (confirmed,
accepted this run, skipped this run, rejected by checks, or pending). The
annotation never suppresses either candidate, and the engineer's `y` or `N`
decides.

A candidate with no description is triaged first and described after `y`: `y`
prompts for a one-line description, which runs through the same provenance
check. An empty line, a rejected character, or a provenance rejection accepts
nothing for that candidate, and the loop continues. A description may not hold
a control, format, separator, surrogate, private-use, or noncharacter code
point, a variation selector, or a blank filler (the Hangul fillers and the
Braille blank), because those render as nothing or drive the terminal. Other
unassigned code points are accepted, so the interpreter's table of unassigned
code points does not decide whether a record loads. `ConfirmedDefect` enforces
this when a record loads, so `defects.json` edited past `confirm` fails to load in
`freeze`, `run`, and `judge`. A mined description may keep LF and TAB, and a
typed one may keep neither. A `pr-comment`
candidate's mined description counts as public text, so it passes verbatim.
Words typed or edited in are still checked.

`y` promotes the candidate. Any other answer, `Y` and `yes` included, skips
it, and it stays in `.local/`. `q` or end of input stops the loop and writes
the candidates accepted so far. Ctrl-C during the prompts writes and pins
nothing. After the last prompt, `confirm` pins each accepted candidate's fix
commit under `refs/review-bench/`, then appends the candidates to
`defects.json`, each with `file_is_markdown` and the candidate's own path (the
head path for a `pr-comment`). An interrupt in that pin-and-write phase can
leave earlier pins in place and writes no defect. `confirm` aborts, exiting 1,
when `defects.json` differs from what it loaded once the prompts end. It
prints model-derived text with control characters as backslash escapes.

`confirm` needs a terminal on stdin. Without one it runs every check that
needs no typed input, prints the rejections, writes and pins nothing, prints
how many candidates await the engineer at a terminal, and exits 2 when any do.
A candidate with no description that passes the schema checks counts as
awaiting. No flag or environment variable supplies an answer in place of a
terminal.

### Fixtures and judge input

- **Markdown function context.** Arm 2 reads `.bench/change-function-context.diff`,
  which is `git diff -W`. The fixture builder gives `*.md` and `*.markdown` a
  diff driver, set in the fixture's `.git/info/attributes` and `.git/config`,
  whose heading pattern makes the function context the enclosing heading
  section. Git's default pattern anchors on any line that starts with a
  letter. The pattern matches per line, so a line-start `#` inside a fenced
  block can start a function context, and attribute patterns are
  case-sensitive, so `x.MD` keeps git's default. The diff artifacts are built
  with the engineer's global and system git config disabled, so a diff driver
  set there cannot change them. Only those two diff calls ignore that config;
  the fixture builder's other git calls read it.
- **Recall judge's fix diff.** One fix commit can hold fixes for many
  comments, so the recall judge's input shows the fix diff limited to the
  defect's recorded `path`. When the fix commit changes no line of that path,
  the section says so and lists the path and the fix commit's changed paths
  without their diffs. That limit is the head path, so a blame that followed
  the line across files, or a fix that renames the file, usually gets the
  listing. The confirmed description and that listing sit between fence lines
  in the judge input, as data the judge is told never to follow, like each
  run's findings.

### Frozen conditions and invalidation

`freeze` writes `evals/review_bench/conditions.json`: the reviewer and judge
model IDs, K, delta, alpha, N_min, the planning variance, the bootstrap's
resample count and seed, the campaign seed, the kappa floor, the retry and
missing-run rule, the later-arm certification rule, the gating rule as a
record (which `(source, file_is_markdown)` cells land in the code gate, the
markdown gate, and the secondary stratum, and the fixture cluster key
`(base_commit, head_commit)`), each gate's distinct-fixture and defect counts,
the environment (CLI version and ambient config commit at freeze), main's
commit SHA (for reference only), the confirmed defect IDs, and sha256 hashes of the
harness's own import closure, both arm directories, the judge agent files,
`defects.json`, and the prompt templates.
The import closure is walked from `runner`, `adjudicate` and `analysis`, so
edits to `run_review_bench.py` and the `mine_*.py` miners after freeze go
undetected. `analysis.GATING_RULE` is in the closure and is the authority for
gate membership, so editing it after freeze changes the manifest. `freeze`
prints each gate's fixture and defect counts, and names any gate below
N_min(K), because a later arm's verdict is then capped at `inconclusive`.

`freeze` fails, exiting 2, on a missing or empty arm directory. It refuses to
overwrite an existing `conditions.json`; delete the file deliberately to freeze
again.

Given `--baseline-conditions-path`, as a later arm's analysis is, `analyze`
recomputes every one of those hashes through the same `compute_frozen_fields`
function `freeze` records them with. A mismatch against the frozen manifest
exits 2, naming the changed, added, or removed file or field. In that mode
`analyze` also exits 2 when `--k` differs from the frozen K, or when the
reviewer records and the frozen defect IDs disagree in either direction. It
reads each gate's baseline sensitivity verdict from the baseline-mode report
at `--baseline-report-path` (default `evals/review_bench/results/baseline.json`)
and exits 2 when that file is missing or unreadable, a verdict key is missing,
a null verdict sits beside two or more fixtures, or the report's
`freeze_identity` is missing or differs from `conditions.json` in the harness
closure hash, any other frozen digest, K, or the campaign environment. The
message names the differing fields. After a re-freeze, rerun all arms under the
new freeze, then regenerate the baseline report. A null verdict is valid only for a gate of fewer than two fixtures, which is
short. No case reads a missing verdict as sensitive. Without
`--baseline-conditions-path`, the baseline's own `analyze` checks that its
records carry one environment and checks no frozen field. `analyze` and `judge`
exit 2 when a reviewer or judge records path they read does not exist; only
`judge`'s own output file may be absent. `run` and `judge` recompute the same
hashes and compare the CLI version and ambient config commit before their
first dispatch, and `run` also compares `--k` and holds `--seed` to the frozen
campaign seed. `run` exits 2 when `conditions.json` does not exist. `judge`
prints a note and proceeds, because the smoke campaign's judges run before the
freeze. `smoke` never checks, since it precedes the freeze. Two further checks
sit alongside the hash comparison, and either can exit 2 without a single hash
differing — this section is their canonical home:

- **Within one campaign, across its blocks.** Each block records the CLI
  version and the ambient config's own commit at its start and at its end.
  Every block's start and end readings must equal one reference environment:
  the environment frozen in `conditions.json` for `run` and `judge`, and the
  campaign's first reading for `smoke` and for `judge` before any freeze. Any
  mismatch halts the campaign with exit 2. Nothing reruns, and the halted
  block writes no records, except the `judge` case below. A `judge` block is
  one defect's two judge runs, read at its start, after its recall run, and
  at its end. The recall record is written after the second reading, so a
  halt at the end keeps that record, which two matching readings bracket, and
  writes no precision record. Recovery takes one of two paths:
  restore the environment and resume with the same `--campaign-id`, which
  reruns the partial block after the resume sweep; or, if the
  environment cannot be restored, as after a CLI update, re-freeze and rerun
  all arms in one campaign. The re-freeze path never sweeps the halted
  campaign. That campaign's write-ahead log, `write-ahead.jsonl` in its run
  store, lists what it left behind, so delete those `review-bench-`
  directories and session stores from it, checking each first, not by name
  prefix. `analyze` then checks the whole campaign as a
  backstop: every record must carry one CLI version and one ambient config
  commit, and a campaign whose blocks disagree makes `analyze` exit 2, naming
  each environment and the defect IDs of the blocks recorded under it.
- **Between campaigns.** A later arm's own campaign environment must match
  the frozen baseline's exactly. A mismatch exits 2 as "invalidated — rerun
  all arms", even when every hash still matches — an environment drift is a
  threat to validity a content hash cannot see.

Operating rule: the ambient config checkout stays at the frozen commit from
`freeze` through the last `judge` run, with no uncommitted edits. Merging the
PR that adds `conditions.json` does not require pulling that checkout. Add no
`git worktree add`, in either repository, while a campaign runs. Likewise
`arms/`, `judges/` and `defects.json` stay unchanged from `freeze` through the
last `judge` run and any `analyze` that follows. Each block and each judge run
re-reads `arms/` and `judges/` after the one-time check, and `analyze`
re-reads `defects.json`. `snapshot-arms`, `confirm` (which writes
`defects.json`), and a `git pull` or `git checkout` in the harness worktree
are the ways they change.

`freeze` itself refuses, exiting 2 and naming the failing precondition,
unless the current harness closure manifest matches the last smoke campaign
that passed, K matches that smoke campaign's own full-K fixture (its judge
caps were sized at that K), every `defects.json` record still passes the
description-provenance check against the `.local/` excerpts, and `.local/`
holds a non-empty excerpt for every record whose `source` is `review-round`.
A shortlist file alone does not satisfy that last check: an SZZ shortlist
carries no excerpt. A `pr-comment` record's description is checked against
those excerpts too, with its stored comment text counted as public.

### Building a later arm

A later arm (#1115's own draft rule, for example) is built by applying its
read-rule change to the frozen `arms/current-rule/` snapshot, allowlist
included — never by copying whatever the live production agent bodies have
drifted to since the freeze. `snapshot-arms` only ever writes the two frozen
arms; a later arm's own snapshot is a hand-applied diff of
`current-rule/bench-<lens>.md`.

### Reading the report

`analyze` computes every figure per gate. The code gate holds non-markdown
`szz` and `review-round` defects, and the markdown gate holds markdown
`review-round` and `pr-comment` defects. The two remaining cells, a
non-markdown `pr-comment` and a markdown `szz`, form the secondary stratum,
which is reported and never gates. `analysis.GATING_RULE` and
`analysis.certify_later_arm` are the authority for membership and for how the
gates combine into a certification. The intervals are a paired cluster
bootstrap that resamples fixtures, the `(base_commit, head_commit)` pair, so
two defects on one fixture do not narrow an interval. A gate is short when its
kept defects span fewer than N_min distinct fixtures.

The `--out` report holds:

- `gates.code` and `gates.markdown`: per-arm recall and pooled precision with
  intervals, the arm differences with intervals, the effective N as a defect
  count and a fixture count beside the largest fixture's defect count, whether
  the kept sets meet N_min, and the dropped counts. In baseline mode (no
  `--baseline-conditions-path`) each gate also holds `baseline_sensitivity`,
  the verdict with its interval limits. In a later arm's mode it holds
  `baseline_sensitivity.verdict` (read from the baseline report),
  `recall_noninferiority`, `precision_noninferiority`, and the gate's
  `outcome`.
- `per_source` and `secondary_stratum`: the same figures per `source` and for
  the secondary stratum, with no verdict key.
- `n_min`, and in a later arm's mode `certification` (`certified`,
  `not-certified`, or `inconclusive`).
- In baseline mode, `freeze_identity` (the harness closure hash, every frozen
  digest, K, and the campaign environment, which a later arm's analysis checks
  against `conditions.json`), `detection_rate_per_defect`, and the
  `dropped_defects_recall` and `dropped_defects_precision` counts. Computing
  the identity needs `--arms-root` to hold both arm snapshots.
- The secondary columns: Read tokens per run, `PARTIAL view` and paged-read
  counts, whole-file-read adherence, missing-run counts by reason,
  out-of-session read counts, recall by fix-date half per gate and arm, the
  recall difference in the code gate's over-read-cap stratum, and the observed
  standard deviation of the per-defect difference per gate.

A figure that is undefined on its set is null: an empty set, or an interval or
standard deviation over fewer than two fixtures. An arm that finds nothing in
a non-empty set has a real recall of 0.0. A gate with no kept defect reports a
null baseline verdict, and its later-arm verdict is `inconclusive`. A
bootstrap resample in which either arm has no adjudicated finding has no
precision difference, so it is dropped, and the count of drops prints beside
the interval.

Every `--out` report carries `confirmed_defects`, the size of the confirmed
set. The recall drop count is taken against that set, so a defect with no
valid recall label counts as dropped. `analyze` exits 2, printing a message and
writing no report, when no defect is kept for recall, and in a later arm's mode
also when none is kept for precision. Arm 1 is `current-rule`, arm 2 is
`function-context`, and X is any later arm. Two caveats govern how to read it:

- **Pairing protects the difference, not the absolute figures.** The
  reviewer model may have already seen this public repo's later fixes.
  Pairing within each defect cancels a shared memorization boost out of
  every arm-vs-arm difference the gates use, but it does nothing for an
  arm's own absolute recall or precision. Read the absolute per-arm numbers
  with that caveat, and read recall by fix-date half — a secondary column,
  never gating — as the closest observable proxy this harness has for that
  exposure.
- **The precision verdict assumes arm-independent judge segmentation.** A
  merge of two findings, or a split of one, moves an arm's pooled precision.
  That cancels out of `precision_X - precision_1` only if the judge's own
  segmentation error rate, and the labels the error touches, are the same
  for both arms. Split agreement per arm, printed by `spot-check import`,
  is where a violation would show; it never gates on its own.

### Interruption and cleanup

`smoke`, `run`, and `judge` each write ahead to their own local run store:
one entry per directory as soon as it exists, and one per session ID before
that run launches. Each also holds an exclusive advisory lock (`flock`) on
its run store's lock file while it runs, and writes its own PID into that
file for the refusal message. For `run` and `smoke`, end-of-block cleanup
runs once the block's runs finish, its end-of-block environment reading
matches, and its records are appended. That is before a systemic-failure stop
and before the block is marked complete. It does not run on Ctrl-C, SIGHUP or
SIGTERM, an environment halt, an error in a run or a record write, a kill, or
a crash, and those exits leave that block's fixture directories and session
stores in place. For `judge`, cleanup runs inline after each judge run, before
the environment checks, so a judge environment halt leaves nothing behind
unless a delete fails or a store is skipped. The judge's store lookup uses only
the final attempt's session ID, so a store is skipped when that attempt has no
`<id>.jsonl`. That inline delete ignores errors and reports none, and a defect
marked complete is outside a resume sweep, so a failed judge delete or a
skipped store stays silently. The judge's cleanup is not in a `finally`, so an
exception out of a judge run skips it. Only a resume under the same
`--campaign-id` sweeps what a `run`, `smoke`, or `judge` exit left: its own
sweep deletes exactly what its own abandoned attempt recorded, then reruns that
block. A resumed `judge` block reuses a kept recall record and reruns only the
precision judge.
The sweep deletes only a `review-bench-` directory directly under the system
temp dir, and a session store directly under the projects root; any other
logged path aborts the sweep with nothing deleted. Resume with the `TMPDIR`
the interrupted attempt ran under, since the sweep compares each logged
directory against the current system temp dir. While
another process holds a run store's lock, a resuming attempt refuses to start
rather than racing the run still in progress. The kernel drops the lock when
its holder dies, so no stale lock file needs clearing by hand. The lock does
not cover the killed run's `claude -p` children, which can keep running after
the lock drops. Nothing enforces the timeout on such a child once its runner is
dead, so waiting before resuming is a heuristic, not a guarantee. After any exit that skips the
child-kill path (see below), wait at least the command's own timeout before resuming: `REVIEWER_TIMEOUT_S`
in `review_bench/runner.py` for `smoke` and `run`, and the judge's own
timeout (`RECALL_JUDGE_TIMEOUT_S` or `PRECISION_JUDGE_TIMEOUT_S`) for `judge`.
A resumed sweep that runs first deletes the directories such a child is still
using, and the child's output is never recorded.

Each `claude -p` child leads its own session, so a terminal hangup never
reaches it. The CLI's `main()` therefore routes SIGHUP and SIGTERM into
`KeyboardInterrupt`, the same path Ctrl-C takes: `abort_launches()` and a kill
of each child's whole process group. Every exit that bypasses those handlers
skips this child-kill path: SIGKILL, SIGQUIT and any other unhandled signal,
and an interpreter or machine crash. A SIGHUP that was already ignored when
the command started, as under `nohup`, stays ignored, so a detached campaign
survives a logout. An interrupted `run_review_bench.py` command prints its
campaign ID with `resume with --campaign-id <id>` and exits 128 plus the
signal number: 130 for Ctrl-C, 143 for SIGTERM, and 129 for SIGHUP.
`measure_subagent_model_resolution.py` routes SIGHUP and SIGTERM the same way
and honors an ignored SIGHUP.

Before its first dispatch, `smoke` and `run` check that every pending
defect's three commits resolve in the source repo, that its head tree holds no
project config a session must not load (see "Out-of-session reads"), and that
both arms have a snapshot file for its lens, then print the run count and the
nominal cost cap product; any problem exits 2 with the full list. That preflight
reads the commit's git tree (`git ls-tree`), which does not traverse a
symlinked parent such as a symlinked `.claude` directory. The build's checks on
the extracted tree do refuse that case, so it fails closed at build time, not
before dispatch. After each block, `smoke` and `run` print its ok and
missing counts by reason. A block whose runs are all missing stops the
campaign with exit 2 and is left un-marked, so resuming under the same
`--campaign-id` reruns it; `smoke --inject-fault` never stops this way, since
it forces every run to fail. `smoke` takes its harness closure manifest hash
once per process, before its first dispatch. A smoke campaign resumed after any
edit to the harness closure therefore prints a hash for code the earlier blocks
did not run. `freeze` compares only that hash with the current manifest, so it
accepts the hash and does not detect the gap: rerun the smoke under a fresh
campaign ID.
`smoke` requires `--defect-id`, and `judge`
skips a defect that has no completed reviewer run.

Each campaign gets its own run store, nested under its `--campaign-id`, so
`smoke` followed by `run` under distinct campaign IDs never shares completion
state. A reused campaign ID shares one store, so `run` skips defects that
`smoke` already completed. A reused ID also shares the `<campaign_id>.jsonl`
records file. A resume passes the
same `--campaign-id`; each command prints the ID and store path before it
takes the lock. A campaign ID is 1–64 characters from `[A-Za-z0-9_.-]`,
starting with a letter or digit.

Five residuals remain.
- A directory can be created in the instant before its own write-ahead record
  is written. List such directories by their `review-bench-` prefix, while no
  lock is held, and inspect each one before deleting it.
- A halted or interrupted `run` or `smoke` campaign that is never resumed
  keeps its fixture directories and session stores until someone deletes them,
  and its write-ahead log names them.
- `judge` catches a `CalledProcessError` or `TimeoutExpired` from one
  defect's judging, prints `skipped`, and continues. A judge directory recorded
  before its fixture install raised stays until a later `judge` resume under the
  same `--campaign-id` sweeps it, and the skip line and exit 0 give no cue to
  resume.
- An arm's representative session that has no `<id>.jsonl` leaves the arm's
  shared session store in place, and the completed block is never swept.
- A `claude -p` child of a hard-killed runner can outlive the lock (see above).

A leftover session store holds raw dispatcher and reviewer transcripts,
including the tool results of out-of-session reads. The re-freeze recovery path
(see "Frozen conditions and invalidation") leaves a halted campaign's
leftovers by design. Nothing bounds their retention except
whatever bounds `projects/`, and `/tmp` fixture retention is unverified.

A crash between a block's records being appended and the block being marked
complete reruns that block on resume, and `read_run_records` deduplicates the
repeated records, keeping the later one. See `runner.read_run_records`.

### Out-of-session reads

A run's own Read, Grep, or Glob outside its own fixture (or judge) directory
and its own session transcript and `<session-id>/` directory is recorded in
that run's `out_of_session_paths` and does not fail the run on its own. Only a read of a live checkout's own
copy of a file the introducing commit or the fix commit changed, or of the
ambient config's `projects/` root (a run's own transcript and its own
`<session-id>/` directory excepted), fails the run. The live checkouts are the
top level of every worktree of this repository, from `git worktree list`, and
of every worktree of the repository the ambient config resolves into, read
once when the command starts. `git worktree list -z` needs Git 2.36 or newer;
an older git fails the command, and the run exits 2 before dispatch. A read
through a stowed `~/.claude` symlink resolves into one of them. A read of a
plugin cache copy under `<config-dir>/plugins/cache/` resolves into none, so
it is only recorded, and the path-list review is what catches it. The adherence diagnostic
counts whole-file reads of the introducing commit's files alone. `smoke`, `run`, `judge`, and `analyze` all print every recorded
path to the terminal, once per run it was recorded against, for the
engineer's own review — the engineer reviews the smoke campaign's list
before the go/no-go and the baseline campaign's before shipping it.
Committed results — `analyze`'s own JSON report, `conditions.json`,
`results/baseline.json` — carry only the per-arm count, never a path,
because a path can name a private project.

A Glob is checked at the directory its pattern names before any wildcard,
joined onto its `path` (an absolute pattern overrides `path`). A leading `~`
or `$VAR` in a path is expanded before the check. Printed paths show control
characters as backslash escapes, since the path is model output.

A run's transcript and subagent sidecar are checked once no file's size has
changed across two consecutive polls. If the sizes are still changing after
`SESSION_FLUSH_TIMEOUT_S` (10 seconds), the transcript as it stands on disk is
classified, so a still-growing transcript can be classified partly written and
miss a later read. A fixture tree (a reviewer arm's or the precision judge's)
is built only when its head tree's `.claude/settings.json` holds no top-level
key outside those this repository's own history has held and the tree holds
neither `.mcp.json` nor `.claude/settings.local.json`; otherwise the build
refuses. `smoke` and `run` apply the same check to every pending defect's head
commit in their preflight, so a refusal exits 2 before any dispatch. The
preflight reads git's tree while the build reads the archive extraction, so a
symlinked `.claude/settings.json` or an `export-ignore` or `export-subst`
attribute can make the two verdicts differ. The build re-checks the extracted
tree and fails closed.

`main()` runs with an owner-only umask, so run records and everything under
`evals/review_bench/.local/` are created without group or other bits. A
`.local/` directory that predates that and carries group or other bits is
restricted to 0700 at startup, and the command exits 2 if it cannot be.

### Runtime cost

- **Reviewer runs:** `2 x N x K`, where N counts every confirmed defect, the
  secondary stratum's included. A baseline that can certify a later arm needs
  each gate at N_min fixtures, so at K = 10 that is at least 252 fixtures and
  5,040 runs. One gate at N_min is 2,520 runs, the floor of a baseline that
  cannot certify. The expected cost is that count times
  `measure_subagent_model_resolution.REPRESENTATIVE_DISPATCH_COST_USD`. A
  ceiling exists only if `--max-budget-usd` bounds the subagent's spend, and not
  only the dispatcher's own overhead. If it does, the ceiling is that count times
  `measure_subagent_model_resolution.PER_RUN_BUDGET_CAP_USD`, doubled if every
  run retried once at the cap. A block that halts or is interrupted reruns whole
  on resume, and its earlier spend is not recorded. If the flag bounds only the
  dispatcher, no ceiling exists, and each run's timeout is its only spend
  bound. `smoke` and `run` print the run count and the nominal cap product
  (runs times the per-run cap, unverified as a bound on spend) before their
  first dispatch. The cost totals `smoke`, `run`, `judge`, and `analyze` print
  leave out every run with no recorded cost, a timed-out run among them, and
  may omit subagent spend, so the invoice can exceed them. This README states
  no dollar figure:
  `REPRESENTATIVE_DISPATCH_COST_USD` was derived with a command that does not
  refuse a wider corpus, so it is not a publication instrument (`docs/private-project-redaction.md` § "This repository, one account").
  `analyze` prints dollar totals, per reviewer arm and per judge kind, to
  stderr for the engineer. Its `--out` report, which is committed, carries
  none. Only the gitignored run records hold per-run costs.
- **Judge runs:** one recall and one precision judge run per defect. Their caps
  and timeouts equal the reviewer's. Change the `RECALL_JUDGE_BUDGET_CAP_USD` and
  `PRECISION_JUDGE_BUDGET_CAP_USD` constants in `review_bench/runner.py` once a
  judge has its own measured values.
- **Wall-clock:** blocks run one at a time, `ceil(2K / workers)` run-slots
  each, at `run_skill_evals.DEFAULT_WORKERS`'s default of 4 — 5 slots at
  K = 10, so 1,260 slots at 252 fixtures. Each minute of median run duration
  adds 21 hours.
- **Judge wall-clock:** `judge` dispatches one recall and one precision judge
  run per defect, fully serially with no worker pool, so wall-clock is
  `2 x N` run-slots with no division by a worker count — 504 at 252 fixtures.
  Each minute of median judge-run duration adds 8.4 hours.

### Publication

The `source` breakdown (`szz`, `review-round`, and `pr-comment`, in a published
defect-set summary) is an own-history count: its whole scope is this
repository's own git history and this account's own session transcripts, on
one account, via `mine-rounds`, which itself refuses to run against more than
one config-dir root. Publish it beside that command and its own scope
refusal, per
`docs/private-project-redaction.md` § "This repository, one account" —
never beside a wider corpus. A `pr-comment` figure is published beside
`mine-pr-comments` and its public-repository refusal.
