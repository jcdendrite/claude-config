# Settle plan-mode subagent model resolution by controlled experiment

## Context

**Goal:** determine, by controlled measurement rather than corpus inference,
what harness plan mode actually does to subagent model resolution — then
rewrite `docs/case-studies/plan-mode-model-resolution.md` so its claim is
unambiguous, and produce a short statement the engineer can quote to clients
with every clause traceable to either Anthropic's published docs or a
reproducible measurement.

**Why now.** The repo currently carries two *different* claims about the same
behavior, and the engineer has to explain one of them to clients:

- **The committed case study** (`docs/case-studies/plan-mode-model-resolution.md:9`)
  says the override is keyed to `permissionMode == "plan"` and forces **Opus**,
  "not to which model the parent happens to be running."
- **The engineer's own working statement** says subagents in plan mode are
  "**pinned to the parent**."

Those are not the same claim. They diverge in exactly one condition — a
plan-mode session whose parent is *not* Opus — and the entire committed
evidence base for preferring the first over the second is **one dispatch**
(`:19`, "One plan-mode `ciso-reviewer` dispatch had a parent turn running on
`claude-sonnet-5`"). Everywhere else the two are collinear, because this repo's
session default is `opusplan` (Opus while planning), so "in plan mode" and
"parent is Opus" describe nearly the same set of sessions in the corpus.

Two further facts, both established this session, say the current write-up
cannot be the basis for a confident client-facing claim:

1. **Anthropic's docs describe a mechanism that produces the observed data
   under the engineer's reading, and the case study does not engage with it.**
   The published resolution order has four steps and *no* permission-mode term
   at all. What the docs do say is that plan mode changes **which agent runs**:
   "When you're in plan mode and Claude needs to understand your codebase, it
   delegates research to the Plan subagent" — and the `Plan` subagent's
   documented model is "inherits from the main conversation." Under `opusplan`
   that inheritance yields Opus, with no permission-mode override needed
   anywhere. The case study's "What this doesn't resolve" section (`:25`) gets
   close to this but frames it as an unexplained gap rather than as a rival
   mechanism that its own data cannot exclude.
2. **The corpus measurement's exposure to a mode-attribution error was never
   ruled out.** The measurement script was ad-hoc and uncommitted (`:21`), so
   the one thing that would settle whether it attributed `permissionMode`
   correctly per dispatch cannot be inspected. The field *is* re-stamped
   through a session (verified below), so correct attribution is possible — but
   "possible" is not "was done," and the committed `commit-gate` reader in this
   repo takes the session's **first** stamp and would get it wrong.

**Intended outcome.** One measured answer, a committed harness that re-derives
it against any future Claude Code version, a rewritten case study that states
the answer in one sentence, and a quotable paragraph. Where the measurement
contradicts the current case study, the case study changes.

## Approach

**Root problem:** the repo asserts a plan-mode → Opus *override* keyed on
permission mode, while Anthropic's docs describe a plan-mode → `Plan`-agent
*substitution* whose documented model inheritance explains the same
observations; the existing corpus cannot distinguish them because `opusplan`
makes plan mode and an Opus parent nearly collinear, and the sole
discriminating datapoint is n=1. anchors: root

### Givens

| # | Given | Reason |
|---|---|---|
| G1 | Subagent model resolution order is `CLAUDE_CODE_SUBAGENT_MODEL` → per-invocation `model` param → definition frontmatter → main conversation's model, and this published order contains **no** permission-mode term | Vendor-published contract; this repo cannot change it — [verified: `code.claude.com/docs/en/sub-agents`, read this session — "Claude Code resolves the subagent's model in this order: 1. The `CLAUDE_CODE_SUBAGENT_MODEL` environment variable... 2. The per-invocation `model` parameter 3. The subagent definition's `model` frontmatter 4. The main conversation's model"] |
| G2 | Plan mode is documented as changing *which agent Claude delegates to*, and that agent inherits the main conversation's model | Vendor-published design of a built-in agent — [verified: same page — `Plan`: "A research agent used during plan mode to gather context before presenting a plan"; "**Model**: inherits from the main conversation"; "When you're in plan mode and Claude needs to understand your codebase, it delegates research to the Plan subagent so that exploration output stays in a separate context window while the main conversation remains read-only"] |
| G3 | The built-in `Explore` also inherits the main conversation's model (capped at Opus on the Claude API), but a user/project `Explore` override "keeps its own `model` field" | Vendor-published; sets the baseline this repo's `Explore.md` override relies on — [verified: same page — "**Model**: inherits from the main conversation, capped at Opus on the Claude API"; "A user or project subagent named `Explore` overrides the built-in and keeps its own `model` field"] |
| G4 | `permissionMode` is re-stamped throughout a session on a dedicated `permission-mode` record type (plus some `user` records), so per-dispatch attribution is mechanically possible — but only if the reader carries the most recent preceding stamp forward | Harness-written transcript format, outside this repo's control — [verified: direct probe of 150 recent transcripts this session: 7,623 `permission-mode`-type stamps vs 959 on `user` records; 48 of 150 sessions change mode mid-session; median last stamp sits at 98.3% of the way through the session, and 0% of sessions have their last stamp inside the first 5% of records] |
| G4a | This repo's own hooks and `marker.sh` resolve their write targets through `_lib_config_dir()` — `CLAUDE_CONFIG_DIR` when set and absolute, else `$HOME/.claude` — so a seeded config dir genuinely contains marker, handoff-nudge, and session-id state rather than leaking it into the operator's live tree | Repo-owned rather than beyond reach, but load-bearing for M1a's containment claim and so recorded as checked — [verified: `claude/.claude/hooks/_lib.sh:98-118`, plus `capture-session-id.sh`, `nudge-handoff-near-context-cap.sh`, `session-marker-dashboard.sh`, `set-session-title-from-branch.sh`, and `claude/.claude/scripts/marker.sh`, this session] |
| G5 | `--permission-mode plan` and `--model <alias>` are both accepted on a headless `claude -p` run, and a subagent's resolved model is recoverable from `<session>/subagents/agent-<id>.jsonl` (`.message.model` on assistant records), with the *requested* model and agent type in the paired `.meta.json` | Harness-provided CLI and on-disk format — [verified: `claude --help` this session, v2.1.228 — `--permission-mode <mode>` "(choices: 'acceptEdits', 'auto', 'bypassPermissions', 'manual', 'dontAsk', 'plan')" with no print-mode restriction annotation; and direct inspection of this session's own subagent sidecars: `{"agentType":"general-purpose",...,"spawnDepth":1,"model":"sonnet"}` alongside 36 assistant records all reading `claude-sonnet-5`] |
| G6 | The engineer's current client-facing statement is "in plan mode, subagents are pinned to the parent," and they are not confident in it | Stated directly this session — [engineer-verified] |

### The four rival mechanisms

All four fit the committed corpus numbers. They differ in one cell.

| | What it claims | Cell 1 (`sonnet` + plan) | Cell 3 (`opus` + plan, Sonnet-pinned agent) | Which agent ran (M3) |
|---|---|---|---|---|
| **H-OPUS** | Plan mode overrides resolution steps 1–3 and forces Opus, independent of parent. *The committed case study's claim.* | **Opus** | Opus | requested |
| **H-INHERIT** | Plan mode collapses resolution to step 4 (main conversation's model), bypassing param and frontmatter. *The engineer's stated understanding.* | **Sonnet** | **Opus** | requested |
| **H-SUBSTITUTE** | Plan mode changes nothing about resolution; it routes research to the built-in `Plan` agent, which has no `model:` field and so falls through to step 4 exactly as documented (G1+G2). | **Sonnet** | **Opus** | **built-in `Plan`** |
| **H-ARTIFACT** | Plan mode does nothing to model choice. The corpus signal is the `opusplan` confound plus mode mis-attribution (G4) and/or the mid-corpus `Explore` change. | **Sonnet** | **Sonnet** | requested |

Cell 1 separates H-OPUS from the rest; cell 3 separates H-ARTIFACT from
H-INHERIT/H-SUBSTITUTE. **No model outcome separates H-INHERIT from
H-SUBSTITUTE** — they predict identical models in every cell. The sole
discriminator is M3's agent-identity read, which is therefore load-bearing on
every plan-mode cell rather than an extra on the decisive one.

H-SUBSTITUTE deserves emphasis: it is the only hypothesis that requires **no
undocumented behavior at all**, and G1+G2 together predict it. The case study
currently asserts H-OPUS — the reading that requires the most undocumented
machinery — on the strength of one datapoint.

**Scope precondition for H-SUBSTITUTE.** G2's sentence describes Claude
*autonomously deciding* to research the codebase. It does **not** say a
prompt-directed dispatch to a named agent is rerouted. If substitution applies
only to self-initiated exploration, a matrix built entirely from explicit named
dispatches cannot test H-SUBSTITUTE at all, and no number of repetitions would
help. Cell 0 resolves this before the rest of the matrix runs.

### Mechanisms

- **M1 — Commit a measurement harness at `evals/measure_subagent_model_resolution.py`.**
  It launches short headless `claude -p` runs across an explicit
  (session model × permission mode × dispatch shape) matrix, then reads each
  run's `subagents/agent-*.jsonl` + `.meta.json` and reports, per cell:
  requested agent type, requested model param, frontmatter pin, and **observed**
  `.message.model`. `evals/` is the grounded home — `evals/run_skill_evals.py`
  is the repo's existing headless-`claude`-launching harness and already carries
  the `subprocess.Popen(["claude", "-p", ..., "--output-format", "stream-json", ...])`
  pattern, and `evals` is on `pyproject.toml`'s pytest `pythonpath`. Repo-root
  `scripts/` holds shell only; `claude/.claude/scripts/` would stow this
  one-off instrument into every consumer's `~/.claude`. **stdlib only** — the
  described work is subprocess launch plus JSONL parsing and the reuse source is
  stdlib-only; adding any package requires naming it first per this repo's rule.
  Harness output goes to a **gitignored** path under `evals/` or to stdout,
  never a working file a later `git add -A` could sweep into a public repo.
  anchors: row G5
- **M1a — Run the whole matrix under a dedicated, seeded `CLAUDE_CONFIG_DIR`,
  not the operator's ambient one.** Seed it from the repo's committed
  `claude/.claude/` tree so `Explore.md`'s pin and the session-default model are
  the *reviewed* ones rather than whatever is stowed at run time. One change
  resolves three otherwise-separate problems, which is why it is a mechanism and
  not three patches: **(i) reproducibility** — a rerun cannot silently differ
  because the operator's personal `~/.claude` drifted, which matters because the
  stated outcome is re-derivation against future versions, and cell 7's premise
  otherwise rests on whichever `Explore.md` happens to be stowed;
  **(ii) containment** — the ~100 sessions' transcript directories land in a
  disposable tree instead of accumulating in the real corpus, and the runs'
  `SessionStart`/`UserPromptSubmit` hooks stop mutating the engineer's live
  marker and handoff-nudge state, which is shared with concurrent interactive
  sessions; **(iii) account scoping** — this machine runs several Claude Code
  accounts under distinct config dirs, so an ambient-inherited value could
  attribute runs to the wrong account. Record `claude --version` and the config
  dir in the harness's own output. The seeded dir lives in a **temp location
  outside the repo** — never inside the working tree, so no credential material
  can reach a commit. anchors: row G5
- **M1b — Authenticate the seeded config dir explicitly, and verify it before
  the matrix runs.** Seeding from `claude/.claude/` alone produces an
  *unauthenticated* dir: `.claude.json` (which carries the account/auth
  segment), `settings.local.json`, and `plugins/` are all gitignored and
  therefore absent from the committed tree — [verified: `git check-ignore`
  against `.gitignore:20,104-112,122`, this session's platform review]. Left
  unhandled, `claude -p` against that dir either fails auth outright or falls
  back to the ambient `$HOME` — and the silent fallback is the worse outcome,
  because it defeats containment goal (iii) at exactly the point M1a claims to
  fix it, while appearing to work. Two parts:
  1. **Setup step:** copy the operator's active `.claude.json` into the seeded
     dir with a plain file copy. Never read its contents — this repo's own rule
     forbids pulling credential files into a session transcript, and `cp` moves
     the bytes without doing so.
  2. **Precondition check, not an assumption:** before any cell runs, execute one
     throwaway `claude -p` against the seeded dir and assert from its transcript
     that the session landed *in the seeded dir* rather than in the ambient one.
     A silent ambient fallback must fail loudly here rather than quietly
     invalidating every cell and polluting the real corpus.

  The three `enabledPlugins` entries (`skill-management`, `claude-hook-review`,
  `plugin-semver`) are also absent from a seeded dir. Confirm before running —
  do not assume — that none of the matrix's dispatches (`staff-backend-engineer`,
  `Explore`, and the no-named-agent prompts) invoke them; if any does, the cell
  is measuring a different environment than the one being reasoned about.
  anchors: row G5
- **M2 — Run the matrix.** Cell 0 first; it gates whether cells 7 and 9 are
  meaningful. Every cell uses a **single fixed prompt template**, varying only
  the named agent, so an observed difference cannot be confounded with prompt
  wording. Carry forward the reuse source's per-subprocess wall-clock timeout
  (`run_skill_evals.py`'s `SAMPLE_TIMEOUT_S` pattern) — headless `-p` without a
  TTY can hang on an approval prompt, and cells 2/4/6 exercise `default` mode
  with a real dispatch. anchors: row G5

  | # | `--model` | `--permission-mode` | Dispatch | Reps | Isolates |
  |---|---|---|---|---|---|
  | 0 | `opusplan` | `plan` | Explicit `staff-backend-engineer`, **and** separately a prompt giving only "understand this codebase" with no named agent | 5 each | **Pre-flight.** Whether an explicit named dispatch is ever rerouted to `Plan`, or substitution applies only to autonomous exploration. Determines whether H-SUBSTITUTE is testable by named dispatch at all. Branching in the table below |
  | 1 | `sonnet` | `plan` | `staff-backend-engineer`, no `model` param | **30** | **Decisive.** Opus ⇒ H-OPUS; Sonnet ⇒ every other hypothesis |
  | 2 | `sonnet` | `default` | same | 5 | Baseline for cell 1 |
  | 3 | `opus` | `plan` | same | **30** | **Decisive.** Separates H-ARTIFACT from H-INHERIT/H-SUBSTITUTE |
  | 4 | `opus` | `default` | same | 5 | Instrument self-check; controlled replication of the prior pass's 178/178 |
  | 5 | `opusplan` | `plan` | same | 5 | Environment sanity — confirms `opusplan` resolves as documented and reproduces the corpus's dominant condition. Not hypothesis-discriminating |
  | 6 | `opusplan` | `default` | same | 5 | Environment sanity — `opusplan`'s execution-phase half |
  | 7 | `opus` | `plan` | `Explore` (repo override pins `sonnet`) | 5 | Whether G3's "keeps its own `model` field" survives plan mode. Reads cleanly only under cell 0's "0 of 5" branch — if `Explore` is itself rerouted to `Plan`, the pin was never consulted and "the pin failed" is the wrong reading |
  | 8 | `opusplan` | `plan` | `staff-backend-engineer` **with** explicit `model: sonnet` param | 5 | Resolution step 2 under plan mode; the prior pass's 0/70 claim. Same cell-0 caveat as cell 7 |
  | 9 | `opus` | `plan` | No named agent — "understand this codebase" only | 5 | The condition G2 literally describes. Interpretation set by cell 0's branch, per the table below |

  **Cell 0 branching — decide this before any other cell is read.** Count how
  many of the 5 explicit-`staff-backend-engineer` reps ran as the built-in
  `Plan` rather than the requested agent (M3's read):

  | Cell 0 result | What it means | Action |
  |---|---|---|
  | **0 of 5 rerouted** | Explicit named dispatch is not subject to substitution; H-SUBSTITUTE is unreachable by named dispatch | Cells 1/3/7/8 read as designed, and their agent-identity column is expected to be constant. **Cell 9 becomes the only test of H-SUBSTITUTE** and must be read against cell 0's own no-named-agent arm |
  | **5 of 5 rerouted** | Explicit named dispatch *is* rerouted; substitution is live everywhere | H-SUBSTITUTE is in play for every named-dispatch cell, and M3's agent-identity column — not the model column — carries the discrimination in cells 1/3/7/8 |
  | **1–4 of 5 rerouted** | Rerouting is probabilistic, not a rule | No cell may be read as a clean binary. Every downstream cell must report the agent-identity split alongside the model split, and the write-up states a **rate**, not a mechanism. Do not average the two populations together — a cell mixing rerouted and non-rerouted dispatches is two experiments, not one |

  Cell 0 runs at n=5, the same floor the plan applies to every other
  non-decisive cell, rather than a lower ad hoc count — a gate that decides how
  three other cells are interpreted should not rest on weaker evidence than the
  cells it gates.

  **On repetition counts.** n=5 is ample for *hypothesis selection*, because the
  hypotheses predict opposite outcomes — roughly 100 percentage points apart,
  not a subtle effect. n=5 is **not** sufficient to characterize the residual
  off-rate the corpus showed (92/95 ≈ 3%, 340/341 ≈ 0.3%): a clean 5/5 is fully
  consistent with a real 3% deviation rate. Cells 1 and 3 therefore run n=30,
  which at 80% power detects rates down to roughly 5%. **This bounds the claim
  the write-up may make:** a clean sweep at n=30 licenses "no exceptions in 30
  trials, ruling out deviation rates above ~5%" — it does **not** license
  "always" or "deterministic," and M4 must not state either. Report **attempted
  vs. observed** dispatches per cell; a rep where the requested dispatch never
  happened is a dropped trial, and silently excluding it would inflate apparent
  determinism.
- **M3 — Record which agent actually ran, not just which was requested.** This
  is the sole discriminator between H-INHERIT and H-SUBSTITUTE, and neither the
  committed `subagent-mix` subcommand nor the prior pass's ad-hoc script
  captured it — `.meta.json`'s `agentType` records only what the parent *asked
  for*. **Concrete detection, in preference order:** (a) any explicit agent-name
  or system-prompt field on the subagent's own first record; failing that,
  (b) **observed toolset**, a strong discriminator here — this repo's
  `Explore.md` declares `tools: Read, Grep, Glob`
  (`claude/.claude/agents/Explore.md:5`) while the built-in `Plan` carries a
  much wider read-only set including `Bash` and web tools, so a `Bash` call from
  a dispatch that requested `Explore` proves substitution. **Confirm which of
  (a)/(b) is available by a manual probe of one real plan-mode dispatch before
  implementing** — do not assume a system-prompt field exists in the transcript.
  anchors: row G2
- **M4 — Rewrite `docs/case-studies/plan-mode-model-resolution.md`** around the
  measured result. Required structural changes regardless of outcome: lead with
  a one-sentence statement of the behavior; state the `opusplan` confound and
  the n=1 discriminator as *limitations of the prior pass*, not as settled
  findings; fold in G1/G2 as the documented mechanism the result either
  confirms or contradicts; and per this repo's Axis 3 preserved-content rule,
  add the new pass as a dated section rather than silently editing the prior
  pass's numbers away. Also update the index line at `docs/case-studies.md:13`,
  which currently states the prior pass's headline ("178/178 still honored the
  pin") as the case study's summary. anchors: row G6
- **M5 — Add a "Stating this precisely" section to the case study**, written in
  the same technical register as the rest of the file — *not* addressed to an
  outside audience, and not framed as copy to quote onward. The engineer's need
  is a claim whose every clause is precise and sourced; that is the case study's
  own job, and a second audience inside the same file would leave contributors
  editing outward-facing copy while fixing technical accuracy. The onward
  paraphrase is delivered in the session reply, not committed. This section must
  explicitly adjudicate all three clauses of the engineer's current statement
  (G6), including stating plainly which half of the "not documented" clause is
  correct: the built-in `Plan` agent's model inheritance **is** vendor-documented
  (G2), so what is undocumented — if anything — is narrower, namely what happens
  to a **named** subagent's own `model:` pin when it is dispatched during plan
  mode. Do not let this fall out implicitly from "fold in G1/G2." anchors: row G6
- **M6 — Correct every site whose wording the measurement falsifies**, per the
  decision table below. Scope known from the prior pass (PR #631):
  `docs/auto-mode.md:188-211`, `claude/.claude/CLAUDE.md` Model Routing,
  `subagent-delegation/SKILL.md`, `Explore.md`, `agent-review/SKILL.md`. anchors: row G6

### Outcome → consequence decision table

Which files change, and what the claim becomes, is fully determined by two
cells and one observation — none of which requires waiting for the data to work
out. Only the counts get filled in from M2.

| Reading | Cell 1 (`sonnet`+plan) | Cell 3 (`opus`+plan) | M3: which agent ran | Headline sentence | Engineer's clause (i) "pinned to the parent" | Engineer's clause (ii) "not documented" | Files to change |
|---|---|---|---|---|---|---|---|
| **H-OPUS** | Opus | Opus | requested | Plan mode forces subagents to Opus regardless of the parent's model or any pin. | **Wrong** — the target is Opus specifically, not the parent | **Correct** | Case study sharpened only; PR #631's corrections stand as written |
| **H-INHERIT** | Sonnet | Opus | requested | In plan mode, subagent model resolution collapses to the main conversation's model; the per-dispatch param and the frontmatter pin are both ignored. | **Correct** | **Half wrong** — `Plan`'s inheritance is documented; a named agent's pin losing effect is not | Case study rewritten; `docs/auto-mode.md` "forces Opus" → "collapses to the parent's model"; `CLAUDE.md`, `subagent-delegation`, `Explore.md`, `agent-review` re-scoped |
| **H-SUBSTITUTE** | Sonnet | Opus | built-in `Plan` | Plan mode does not override model resolution — it substitutes the built-in `Plan` agent for the one requested, and `Plan` has no `model:` field, so it inherits the parent. | **Right outcome, wrong mechanism** | **Wrong** — fully documented (G1+G2) | Case study rewritten around the documented mechanism; every "override"/"forces Opus" phrasing corrected repo-wide; `CLAUDE_CODE_DISABLE_EXPLORE_PLAN_AGENTS` is promoted from dead end to live mitigation |
| **H-ARTIFACT** | Sonnet | Sonnet | requested | Plan mode does not affect subagent model resolution; the prior finding was an `opusplan` confound plus mode mis-attribution. | **Wrong** | Moot | Case study **retracted and superseded**; PR #631's corrections to all five files need reverting — the largest-blast-radius outcome, and the reason the instrument self-checks below are non-optional |

A fifth possibility — cells disagreeing across repetitions rather than landing
cleanly — is a real outcome, not a failure: it means the behavior is
probabilistic and the honest headline says so with a rate. The write-up must be
able to state that; do not force a deterministic reading onto a mixed result.
- **M7 — Document the harness** by extending `evals/README.md`'s existing "Why
  local only — never CI" section rather than writing a parallel disclaimer
  (single source of truth), and add a `docs/scripts.md` entry. **No CI risk
  exists and the plan should say so plainly:** CI runs
  `pytest claude/.claude/ plugins/` (`.github/workflows/tests.yml`), so `evals/`
  is never a collection target; `pyproject.toml`'s `pythonpath` entry governs
  import resolution only. anchors: row G5
- **M8 — Estimate the cost before running M2, using this repo's own tooling**
  (`transcript-analysis.py cost`, `docs/cost-levers-considered.md`), and state
  the figure in the PR. Pass `--max-budget-usd` on each subprocess as a guard —
  [verified: `claude --help`, v2.1.228 this session — "Maximum dollar amount to
  spend on API"; it bounds a single session, so the aggregate across ~100
  sessions is the figure that must be stated, not the per-run cap].
  ~100 headless sessions, many Opus-anchored, is not a free operation, and the
  stated outcome is re-running it against future versions — so the cost recurs
  rather than being one-time. Pin each dispatched agent's task to a cheap
  classification-shaped probe: the experiment needs the dispatch to *happen*,
  not to produce useful work. anchors: row G5

**No heavier mechanism adopted.** Two lighter primitives were considered
before committing a new harness, per the over-powered-primitive check:
*(a) extend the existing `subagent-mix` subcommand with a `--by-permission-mode`
flag* — rejected because it re-derives from the same confounded corpus and
therefore cannot answer the question no matter how it slices; the decisive cell
(Sonnet parent in plan mode) barely exists in the corpus at all, which is why
the prior pass found n=1. *(b) A pure corpus reanalysis with corrected
per-dispatch mode attribution* — rejected for the same reason, though it is
retained as a **cross-check** in M2's verification, not as the primary
instrument. The new harness is justified specifically because the needed
condition must be *created*, not found.

### Validity threats to state in the write-up, not paper over

- **Headless ≠ interactive.** The engineer's question is about interactive
  Shift+Tab plan mode; the harness measures `-p --permission-mode plan`.
  `claude --help` does not annotate `--permission-mode` as print-mode-only
  (G5), but equivalence is **[unverified]**. Mitigation: one manual interactive
  spot-check of the decisive cell, run by the engineer, before the case study
  claims the interactive case.
- **Dispatch compliance may itself vary by mode.** Plan mode is read-only and
  research-oriented; the model may decline, or alter how it invokes, an
  explicitly-requested code-touching agent in plan vs. default mode for reasons
  unrelated to model resolution. This is why M2 reports attempted vs. observed
  per cell — a large compliance gap between a cell and its baseline makes the
  two non-comparable and must be disclosed rather than averaged away.
- **Version drift.** `Explore` changed from always-Haiku to inherit-from-parent
  at v2.1.198; the corpus spans that change, the harness measures v2.1.228
  only. The case study must date its claim to a version.
- **`Plan` agent availability.** If plan mode's delegation to `Plan` is what
  drives the effect, `CLAUDE_CODE_DISABLE_EXPLORE_PLAN_AGENTS=1` becomes a
  *testable mitigation* rather than the dead end the current case study records
  (`:32`).

## Critical files

- `evals/measure_subagent_model_resolution.py` — **new.** Reuse from
  `evals/run_skill_evals.py`: the `subprocess.Popen(["claude", "-p", ...])`
  launch shape (`:516`), `detect_dispatch_in_stream` (`:574`) for the
  attempted-vs-observed count, the `SAMPLE_TIMEOUT_S` timeout pattern, and its
  explicit session-store cleanup — `shutil.rmtree(project_dir)` alone does not
  remove transcripts, which land outside the temp project. Do not reimplement
  transcript discovery — the project-dir name is the cwd with `/` → `-`, and
  `_dispatch_usage_summary` in
  `claude/.claude/scripts/transcript-analysis.py:3363` already encodes the
  `toolUseId` → `subagents/agent-<id>.jsonl` join; import or mirror it rather
  than writing a third copy.
- `evals/test_measure_subagent_model_resolution.py` — **new.** Fixture-based, no
  live sessions: canned `.meta.json` + `agent-<id>.jsonl` pairs covering
  agent-ran-as-requested, agent-substituted-to-`Plan`, missing sidecar, missing
  `.message.model`, and a mixed-model dispatch. **It lives beside the harness,
  not in `claude/.claude/tests/`** — see the note below.
- `.github/workflows/tests.yml` — add this one test file by explicit path to the
  two `pytest claude/.claude/ plugins/` invocations (lines 160, 166) and the
  `ruff check` invocation (line 170), alongside the harness module itself.
  Explicit paths, not a blanket `evals/` target: broadening collection to the
  whole directory would sweep in `run_skill_evals.py`, which `evals/README.md`
  documents as never-CI. Also check the change-gating regexes above those steps
  (~lines 111–130) — the pytest steps are skipped unless a hook-relevant path
  changed, so a PR touching only `evals/` would otherwise skip its own tests.
- `docs/case-studies/plan-mode-model-resolution.md` — rewrite per M4/M5.
  Currently 34 lines; the prior pass's numbers become a dated prior section.
- `docs/case-studies.md` — line 13, the index summary (M4).
- `docs/auto-mode.md` — lines 188–211 (`## Subagent delegation under plan
  mode`). Per the decision table's row for the measured outcome (M6).
- `docs/scripts.md`, `evals/README.md` — harness documentation (M7).
- `claude/.claude/CLAUDE.md`, `claude/.claude/skills/subagent-delegation/SKILL.md`,
  `claude/.claude/agents/Explore.md`, `claude/.claude/skills/agent-review/SKILL.md`
  — per the decision table's row for the measured outcome (M6); touch only if
  the measurement falsifies their current wording.

**Why the test does not go in `claude/.claude/tests/`.** That directory is
stowed — `claude/.claude/` maps 1:1 to `~/.claude/`, so anything added there
ships to every user who clones and stows this repo, not just this engineer.
Every test currently there covers a file that *is* stowed and therefore exists
in the target. A test for `evals/measure_subagent_model_resolution.py` would be
the only one importing a module that is never stowed — dead weight with an
unresolvable import for every stow consumer. Landing it beside the harness and
naming it explicitly in CI keeps the test running where it matters and out of
everyone else's `$HOME`. (Placement there was the round-1 recommendation, made
on CI-collection grounds before the stow-distribution consequence was weighed.)

## Verification

- **Structural self-check on every plan-mode cell, before reading any outcome.**
  Cells 2 and 4 are both `default` mode and so cannot catch a plan-mode-only
  parsing failure — if substitution changes the sidecar shape or breaks the
  `toolUseId` join, the `default`-mode outcome checks below pass while every
  measured cell is silently misread. A bare not-null assertion is **not enough**:
  a field-mapping bug that picks up a stale or sibling value yields something
  non-null and passes, which is the same silent misattribution the check exists
  to prevent. So the check must corroborate, not merely find:
  1. Pin cell 4's known-good `default`-mode record as a **schema fixture**, and
     validate every plan-mode record against it — a shape divergence then
     surfaces as a schema mismatch rather than as a plausible value.
  2. Cross-validate the parsed agent identity against the **observed toolset**
     (M3's discriminator, applied redundantly). Two independent signals
     disagreeing is a parser defect and voids the cell; agreeing is corroboration
     no single field read can give.
- **Outcome self-check before trusting any cell:** cell 2 (`sonnet` +
  `default`, Sonnet-pinned agent) must return Sonnet, and cell 4 (`opus` +
  `default`) must return Sonnet. If cell 4 returns Opus, the instrument is
  reading the parent rather than the subagent and every other cell is void.
- **Corpus cross-check:** re-derive the prior pass's headline numbers with
  *correct per-dispatch* `permissionMode` attribution (carry the most recent
  preceding stamp forward, per G4). Agreement strengthens the prior pass;
  disagreement is itself a finding for the write-up.
- **Re-locate the n=1 mirror counter-example** in the corpus and determine
  whether it survives correct mode attribution, or whether it was a dispatch
  after `ExitPlanMode` in a session stamped `plan` earlier.
- **Disclosure boundary on both corpus-touching steps above.** They read the
  real `~/.claude/projects/**` tree, which carries private project paths, names,
  and code, and this repo is public. Report **counts and booleans only** in any
  committed artifact; session IDs, project paths, and per-dispatch snippets stay
  in the local scratchpad — the posture the existing case study took at `:21`
  and that `transcript-analysis.py`'s `--redact` behavior already encodes.
- `.venv/bin/pytest claude/.claude/ evals/test_measure_subagent_model_resolution.py`
  and `.venv/bin/ruff check` over both new `evals/` files. From a linked
  worktree the venv is three levels up (`../../../.venv/bin/...`).
  `shellcheck` if any shell is added.
- **Stow-consumer check** (this repo's own `plan-review` project layer): confirm
  the diff adds nothing under `claude/` that would ship to every stow user
  without belonging in their `$HOME`. As designed, the only `claude/` changes
  are the conditional doc corrections in M6 — the harness and its test both stay
  repo-local.
- Every quantitative claim in the rewritten case study re-derived from the
  harness output at the moment of writing, with the cell it came from named
  inline — per this repo's "Ground every choice" rule.
- `/code-review` before commit, run **after** M4/M5 are drafted rather than only
  after the harness lands, so `deny-private-project-refs` sees the prose the
  corpus cross-check could have contaminated. `/ready-for-review` before handoff.

## Out of scope

- **Reopening the `opusplan` session default.** The measurement may sharpen the
  cost argument for flipping it, but the flip collides with
  `guard-settings-session-keys.sh` and affects every session. Name it as a
  follow-up in the PR; do not decide it here.
- **A client-facing Artifact explainer.** Offered and not chosen; the onward
  paraphrase is delivered in the session reply rather than committed, per M5.
- **Measuring `CLAUDE_CODE_SUBAGENT_MODEL` behavior under plan mode.** Carried
  over as out of scope from the prior plan; it sits at resolution step 1 and is
  a blunt global override this repo already declines to use.
- **The separate "Opus code-read delegation discipline" cost driver** — whether
  parents should dispatch at all, as distinct from what a dispatch resolves to.
