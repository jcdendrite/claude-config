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
it against any future Claude Code version, and a rewritten case study that
states the answer in one sentence. The onward paraphrase the engineer quotes to
clients is delivered in the session reply rather than committed (see M5). Where
the measurement contradicts the current case study, the case study changes.

**Budget constraint (engineer, this session).** The experiment gets **7 live
runs** — 5 for the primary question, plus 2 the engineer added to test `Explore`
specifically. Not the ~100 an earlier draft specified. This is a hard input to
the design, not a target to approach: the matrix below extracts a full four-way
discrimination from the first 5, and everything that cannot be bought with the
7 is dropped to Out of scope rather than trimmed to fit.

**Second question, added by the engineer: should `Explore` be pinned to Haiku
rather than Sonnet?** This is downstream of the primary question and the plan
treats it that way — if plan mode ignores the pin, the pin's *value* is
irrelevant in plan mode and the Haiku change buys nothing there. Runs 6–7
therefore test whether the pin survives at all *and* whether a Haiku request is
honored exactly, in one pair of runs. See M9.

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
| G4a | This repo's hooks write session state keyed by PID or session id (`capture-session-id.sh` → `sessions/$CLAUDE_PID`; `nudge-handoff-near-context-cap.sh` → `.handoff-nudge-fired.d/<session_id>` plus a shared append-only log), and `marker.sh` is not exercised by a run that invokes no review skill | Repo-owned rather than beyond reach, but load-bearing for M1a's decision to run against the ambient config dir: it is why seven headless runs cannot clobber a concurrent interactive session's state, and why the only residue is accumulation — [verified: `claude/.claude/hooks/_lib.sh:106-120`, `capture-session-id.sh`, `nudge-handoff-near-context-cap.sh`, `claude/.claude/scripts/marker.sh`, this session] |
| G3a | Same-named subagent definitions resolve by a documented priority order — managed settings (1), the `--agents` CLI flag (2), project `.claude/agents/` (3), user `~/.claude/agents/` (4), plugin `agents/` (5) — so a definition supplied via `--agents` outranks the stowed user-scoped `Explore.md` that is present in every run | Vendor-published; load-bearing for M9, which would otherwise be measuring whichever definition happened to win — [verified: `code.claude.com/docs/en/sub-agents`, "Choose the subagent scope", read this session — "When multiple subagents share the same name, Claude Code uses the one from the higher-priority location", with the five-row priority table; and `claude --help`, v2.1.228 — `--agents <json>` "JSON object defining custom agents"] |
| G5 | `--permission-mode plan` and `--model <alias>` are both accepted on a headless `claude -p` run, and a subagent's resolved model is recoverable from `<session>/subagents/agent-<id>.jsonl` (`.message.model` on assistant records), with the *requested* model and agent type in the paired `.meta.json` | Harness-provided CLI and on-disk format — [verified: `claude --help` this session, v2.1.228 — `--permission-mode <mode>` "(choices: 'acceptEdits', 'auto', 'bypassPermissions', 'manual', 'dontAsk', 'plan')" with no print-mode restriction annotation; and direct inspection of this session's own subagent sidecars: `{"agentType":"general-purpose",...,"spawnDepth":1,"model":"sonnet"}` alongside 36 assistant records all reading `claude-sonnet-5`] |
| G6 | The engineer's current client-facing statement is "in plan mode, subagents are pinned to the parent," and they are not confident in it | Stated directly this session — [engineer-verified] |

### The four rival mechanisms

All four fit the committed corpus numbers. They differ in one cell.

| | What it claims | Run 2 (`sonnet` + plan) | Run 4 (`opus` + plan, Sonnet-pinned agent) | Which agent ran (M3) |
|---|---|---|---|---|
| **H-OPUS** | Plan mode overrides resolution steps 1–3 and forces Opus, independent of parent. *The committed case study's claim.* | **Opus** | Opus | requested |
| **H-INHERIT** | Plan mode collapses resolution to step 4 (main conversation's model), bypassing param and frontmatter. *The engineer's stated understanding.* | **Sonnet** | **Opus** | requested |
| **H-SUBSTITUTE** | Plan mode changes nothing about resolution; it routes research to the built-in `Plan` agent, which has no `model:` field and so falls through to step 4 exactly as documented (G1+G2). | **Sonnet** | **Opus** | **built-in `Plan`** |
| **H-ARTIFACT** | Plan mode does nothing to model choice. The corpus signal is the `opusplan` confound plus mode mis-attribution (G4) and/or the mid-corpus `Explore` change. | **Sonnet** | **Sonnet** | requested |

Run 2 separates H-OPUS from the rest; run 4 separates H-ARTIFACT from
H-INHERIT/H-SUBSTITUTE. **No model outcome separates H-INHERIT from
H-SUBSTITUTE** — they predict identical models in every cell. The sole
discriminator is M3's agent-identity read, which is therefore load-bearing on
every plan-mode run rather than an extra on the decisive one. This is also why
so few runs suffice: the four hypotheses disagree in only two places, and the
third signal is parsed from transcripts those same runs already produce.

H-SUBSTITUTE deserves emphasis: it is the only hypothesis that requires **no
undocumented behavior at all**, and G1+G2 together predict it. The case study
currently asserts H-OPUS — the reading that requires the most undocumented
machinery — on the strength of one datapoint.

**Scope note on H-SUBSTITUTE.** G2's sentence describes Claude *autonomously
deciding* to research the codebase; it does not say a prompt-directed dispatch
to a named agent is rerouted. Within the 5-run budget this plan measures the
**named-dispatch** case only — which is the case that matters here, because the
engineer's pipeline dispatches named agents (`Explore`, `staff-*`) rather than
relying on autonomous exploration. M3's agent-identity read on runs 2–7 answers
"was the named dispatch rerouted to `Plan`?" directly and at no extra run cost.
Whether *autonomous* exploration behaves differently is a separate question,
deferred to Out of scope.

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
- **M1a — Record the ambient environment; do not try to replace it.** The
  harness records `claude --version`, the effective `CLAUDE_CONFIG_DIR`, the
  `model:` frontmatter it read for the dispatched agent, and
  **`CLAUDE_CODE_SUBAGENT_MODEL` (set/unset, plus its value)** — and **aborts
  before run 1 if that variable is set.** It sits at resolution step 1 of G1,
  above both the per-dispatch param and the frontmatter pin, so an ambient value
  would override every other step in all seven runs and make the four
  hypotheses indistinguishable — with nothing in the output to show it happened.
  Recording it is not enough on its own: discovering it afterwards means seven
  wasted runs against a hard budget, so this is a precondition that fails loudly,
  not a field on a report. **Reproducibility is scoped to "same CLI flags, same
  frontmatter, same subagent-model env" — not "same environment."** Recording
  `CLAUDE_CONFIG_DIR` captures a *path*, and that path's gitignored
  `settings.local.json` and `plugins/` mutate independently of any commit, so a
  future re-run against the same path is not guaranteed the same effective
  settings. The write-up must claim the narrower thing.

  **Why not a dedicated config dir.** An earlier draft ran the matrix under one
  seeded from the committed `claude/.claude/` tree; at this scale that is an
  over-powered primitive and it is dropped. Its three justifications do not
  survive the smaller budget: **containment** was about ~100 sessions polluting
  the corpus and live marker state, and seven transcript directories are noise;
  **account scoping** is handled by recording which config dir was active rather
  than controlling it; and **reproducibility** mattered most for the
  `Explore`-pin cell, which M9 now covers by writing a project-scoped definition
  into its own temp project instead. Dropping it also removes the auth problem it
  created — the committed tree has no `.claude.json`, `settings.local.json`, or
  `plugins/` (all gitignored — [verified: `git check-ignore` against
  `.gitignore:20,104-112,122`, this session]), so a seeded dir would have had to
  be hand-authenticated and then *proven* not to have silently fallen back to
  the ambient `$HOME`. Recording beats controlling here: one line of output
  instead of a setup step, a credential copy, and a fail-loud fallback check.

  **Accepted residue, stated rather than left looking like an oversight.**
  Running against the ambient config dir means the seven runs leave two traces
  in the engineer's live `~/.claude/`: a `sessions/<pid>` entry each, and
  possibly a `.handoff-nudge-fired.d/<session_id>` marker. Neither collides with
  a concurrent interactive session — every such write is keyed by PID or session
  id (G4a) — but `claude -p` one-shot runs never fire `SessionEnd`, so these are
  not self-cleaning. Seven entries is accepted noise, not worth a cleanup step;
  recorded here so a later reader knows it was weighed rather than missed.
  `marker.sh` is never exercised at all, since the harness invokes no review
  skill. anchors: rows G5, G4a
- **M2 — Run seven sessions.** All seven use one **fixed prompt template**,
  varying only `--model`, `--permission-mode`, and (for runs 6–7) the agent
  dispatched, so an observed difference cannot be confounded with prompt
  wording. Runs 1–5 dispatch
  `staff-backend-engineer` (frontmatter `model: sonnet`, no per-dispatch param)
  with a cheap classification-shaped task — the experiment needs the dispatch to
  *happen*, not to produce useful work. Carry forward the reuse source's
  per-subprocess wall-clock timeout (`run_skill_evals.py`'s `SAMPLE_TIMEOUT_S`
  pattern); headless `-p` without a TTY can hang on an approval prompt, and run
  1 exercises `default` mode with a real dispatch. Run 1 first — if it fails,
  the other six are uninterpretable and no budget should be spent on them.
  anchors: row G5

  | Run | `--model` | `--permission-mode` | Purpose |
  |---|---|---|---|
  | 1 | `opus` | `default` | **Instrument self-check, and it must run first.** Parent Opus, pin Sonnet, no plan mode ⇒ must return **Sonnet**. If it returns Opus the harness is reading the parent rather than the subagent, and runs 2–7 are void. Also a controlled replication of the prior pass's 178/178 |
  | 2 | `sonnet` | `plan` | **Decisive A.** Opus ⇒ H-OPUS; Sonnet ⇒ every other hypothesis |
  | 3 | `sonnet` | `plan` | Repeat of run 2 |
  | 4 | `opus` | `plan` | **Decisive B.** Sonnet ⇒ H-ARTIFACT; Opus ⇒ H-INHERIT or H-SUBSTITUTE |
  | 5 | `opus` | `plan` | Repeat of run 4 |
  | 6 | `opus` | `plan` | **`Explore` pin test** — dispatches `Explore` against a `model: haiku` definition supplied via `--agents`, instead of `staff-backend-engineer`. See M9 |
  | 7 | `opus` | `plan` | Repeat of run 6 |

  **How so few runs still discriminate all four hypotheses.** Runs 2 and 4 are
  the only two cells the four hypotheses disagree on (see the table above), and
  M3's agent-identity read — which costs no extra sessions because it is parsed
  from the same transcripts — separates the one pair those two cells cannot
  (H-INHERIT vs H-SUBSTITUTE). Run 1 is not discriminating but is not optional:
  without it, a broken instrument produces four confident readings of nothing.
  Runs 6–7 answer the engineer's separate `Explore`/Haiku question (M9) and take
  no part in the four-way discrimination. The `opusplan` sanity cells, the
  explicit-param cell, and the autonomous-delegation cell all answered
  *secondary* questions and are deferred to Out of scope rather than squeezed in
  at n=1.

  **What two repeats buy, and what they do not.** Runs 3 and 5 exist to catch an
  unlucky draw on the two observations everything else rests on — the corpus
  showed off-rates around 3% and 0.3%, so a single decisive run could in
  principle land on an exception and invert the conclusion. Two agreeing runs
  make that much less likely. They do **not** support any rate claim: at n=2 the
  detectable floor is nowhere near those percentages. **The write-up may say
  "2 of 2 runs" and must not say "always," "deterministic," or any percentage
  derived from these runs.** It may cite the prior pass's 178/178 alongside them
  **only if the corpus cross-check in Verification confirms that figure survives
  corrected per-dispatch mode attribution** — that number is precisely what the
  cross-check exists to re-derive, and citing it unconditioned would re-import
  the suspect evidence this experiment was built to replace. If runs 2 and 3
  disagree — or 4 and 5, or 6 and 7 — the honest result is "behavior is not
  deterministic and seven runs cannot characterize it," which is a finding to
  report, not a failure to paper over by picking the majority.

  Report **attempted vs. observed** dispatches for every run. A run where the
  requested dispatch never happened is a dropped trial, not a silent omission,
  and at n=2 losing one is half the evidence — re-run it rather than reporting
  1 of 1.
- **M9 — Runs 6–7: does `Explore`'s override survive plan mode, and is a Haiku
  request honored exactly?** Dispatch `Explore` in an `opus` + `plan` session
  against a **project-scoped** `Explore.md` the harness writes into its own temp
  project, carrying `model: haiku`. Three points, in order of importance:

  1. **Why Haiku rather than the current Sonnet pin — it is a strictly sharper
     instrument, not just the config under consideration.** With the parent on
     Opus and the pin on Haiku, three outcomes are distinguishable: **Haiku** ⇒
     the pin is honored exactly; **Sonnet** ⇒ the pin is consulted but
     substituted upward, which would be *undocumented* — G3 documents a **cap**
     at Opus, not a floor at Sonnet, so a Haiku request landing on Sonnet is a
     finding in its own right; **Opus** ⇒ the pin is ignored, or `Explore` was
     replaced by `Plan`, separated by M3. A Sonnet pin against an Opus parent
     collapses the first two of those into one observation and would answer the
     engineer's actual question only by inference.
  2. **Supply the override via the `--agents` CLI flag, and do not edit the
     committed `claude/.claude/agents/Explore.md`.** That file is stowed to every
     consumer of this repo; a measurement must not mutate shipped config. The
     flag sits at priority 2 in the documented scope order, above both project
     (3) and the stowed user-scope definition (4) — and the stowed `model: sonnet`
     `Explore.md` **is present in all seven runs**, because M1a runs against the
     ambient config dir. Without an override that provably outranks it, a Sonnet
     reading in runs 6–7 would be ambiguous between "Haiku request substituted
     upward" and "the user-scoped Sonnet definition simply won" — two
     explanations producing identical model output *and* identical agent
     identity, which M3 cannot separate. `--agents` removes that fourth outcome
     by construction rather than leaving it to be reasoned about afterwards.
     anchors: row G3a
  3. **The pin's value only matters if the pin survives.** If runs 2–5 show the
     frontmatter pin losing effect in plan mode — whether because it is
     overridden on the requested agent (H-INHERIT) or because `Explore` is
     replaced by `Plan` and the pin is never consulted at all (H-SUBSTITUTE) —
     then switching `Explore` to Haiku buys **nothing for plan-mode dispatches**.
     Since the prior corpus pass put roughly three-quarters of `Explore`
     dispatches inside plan mode, that would gut most of the expected saving.
     M9's result must be read against runs 2–5 before any pin change is
     proposed, and the write-up must say so rather than presenting Haiku as an
     unconditional cost lever. If runs 2/3 or 4/5 disagree, the primary question
     is unresolved and M9 cannot be interpreted either — report both as open
     rather than reading M9 against a coin flip. anchors: row G3
  4. **M9 is hypothesis-generating for the pin decision, not authorization for
     it.** Two runs are enough to select among mechanisms that differ by ~100
     percentage points; they are not enough to justify changing a default that
     ships to every stow consumer of this public repo. A pin change is a separate
     decision with its own validation, and it also has to be squared with this
     repo's own Model Routing rule ("Haiku: narrow, deterministic skills only.
     Never for code authoring or judgment") — `Explore` decides what is relevant
     and what to report back, which is judgment feeding the planning step. Not
     resolved here; named so the decision is made deliberately. anchors: row G3
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

Which files change, and what the claim becomes, is fully determined by two runs
and one observation — none of which requires waiting for the data to work out.
Only the counts get filled in from M2.

| Reading | Run 2 (`sonnet`+plan) | Run 4 (`opus`+plan) | M3: which agent ran | Headline sentence | Engineer's clause (i) "pinned to the parent" | Engineer's clause (ii) "not documented" | Files to change |
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
- **M8 — Cap each run's spend and report the actual total.** Pass
  `--max-budget-usd` on each of the seven subprocesses — [verified:
  `claude --help`, v2.1.228 this session — "Maximum dollar amount to spend on
  API"; it bounds a *single* session, so seven runs need seven caps and the
  aggregate is the number to report]. At this scale the pre-run estimate an
  earlier draft required is disproportionate — the per-run cap bounds the worst
  case by construction, which is what the estimate was for. Report the actual
  total from `transcript-analysis.py cost` afterwards so the PR carries a real
  figure, not a projection.

  **Derive the cap; do not pick a round number.** Before run 1, take a
  representative `staff-backend-engineer` dispatch's actual cost from the
  existing corpus via `transcript-analysis.py cost` and set the cap at roughly
  10× it, recording both the derived per-dispatch figure and the multiplier in
  the harness. The headroom is the point: a cap set near the expected cost
  truncates a run mid-flight, and the runs it would truncate are the decisive
  ones that repeat only twice — converting a spend guardrail into exactly the
  dropped-trial failure M2 already guards against. A truncated run is re-run,
  never reported as a result. anchors: row G5

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
- **M3's discriminator may never observe a true positive.** H-SUBSTITUTE's row
  predicts an agent-identity read of `Plan` for a *named* dispatch, but G2
  documents substitution only for Claude's autonomous exploration. If named
  dispatches are never rerouted — plausible, and left open by this plan's own
  scope note — M3 reads "requested" in all seven runs, the experiment never
  produces a real substitution record, and the `agent-substituted-to-Plan`
  fixture stays synthetic and never validated against a genuine one. That does
  not invalidate the result, but it changes its strength: **if H-SUBSTITUTE is
  selected, the write-up must say whether it was confirmed by an observed
  substitution or only by elimination from the model pattern.** Those are not
  the same claim and must not be reported as one.

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
- **No `.github/workflows/tests.yml` change.** An earlier draft wired this test
  into CI by explicit path, which also meant touching the change-gating regexes
  that decide when the pytest steps run at all. That is disproportionate for a
  parser behind a manually-run, rarely-changed script: the wiring is more
  delicate than the thing it guards, and `evals/`'s sibling harness is already
  documented as never-CI. The test runs from the local command in Verification.
  Recorded as a deliberate gap, not an oversight — if this harness later grows
  enough to rot unnoticed, wiring it in is the follow-up.
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

- **Outcome self-check, run 1, before spending budget on runs 2–7:** `opus` +
  `default` with a Sonnet-pinned agent must return **Sonnet**. If it returns
  Opus, the harness is reading the parent rather than the subagent and every
  other run is void — stop and fix the instrument.
- **Structural self-check on runs 2–7, before reading any outcome.** Run 1 is
  `default` mode and so cannot catch a plan-mode-only parsing failure — if
  substitution changes the sidecar shape or breaks the `toolUseId` join, run 1's
  outcome check passes while every measured run is silently misread. A bare
  not-null assertion is **not enough**: a field-mapping bug that picks up a
  stale or sibling value yields something non-null and passes, which is the same
  silent misattribution the check exists to prevent. So it must corroborate, not
  merely find:
  1. Pin run 1's known-good `default`-mode record as a **schema fixture** and
     validate every plan-mode record against it — a shape divergence surfaces as
     a schema mismatch rather than as a plausible value. **Scope the fixture to
     the fields the outcome parser and M3 actually read** (`.message.model`, the
     `tool_use` entries, `agentType`), not a whole-record diff: it is built from
     a single run, so a full-record schema would encode whatever that one
     classification-shaped task happened to emit — flagging legitimate
     plan-mode-only shape variation as corruption and burning re-run budget,
     while still missing a field that run 1 never produced.
  2. Cross-validate the parsed agent identity against the **observed toolset**
     (M3's discriminator, applied redundantly). Two independent signals
     disagreeing is a parser defect and voids the run; agreeing is corroboration
     no single field read can give. At n=2 a voided run must be re-run, not
     dropped.
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

**Dropped to fit the 5-run budget.** Each answers a real question and each is a
clean follow-up once the primary question is settled; none is a prerequisite for
it. Listed rather than silently cut, so the write-up can say what it did not
measure:

- **Whether an explicit per-dispatch `model: sonnet` param is honored in plan
  mode** (resolution step 2; the prior pass's 0/70 claim).
- **Whether autonomous exploration behaves differently from a named dispatch** —
  the condition G2 literally describes, and the only form in which H-SUBSTITUTE
  is vendor-documented. This plan measures named dispatch only. Because of that,
  the write-up must disclose if M3 never observed a genuine substitution and
  H-SUBSTITUTE was selected only by elimination (see the fifth validity threat).
- **Whether a `user`-scoped `Explore` override behaves as the `--agents`-supplied
  one M9 tests.** M9 supplies the definition via the CLI flag to avoid mutating
  shipped config and to outrank the stowed file (G3a); the engineer's real setup
  is user-scoped via stow. The scope order is documented, but whether plan mode
  treats a scope-4 definition identically to a scope-2 one is not — so a result
  showing the pin honored does not by itself prove the *stowed* pin would be.
- **`opusplan` environment-sanity runs**, confirming the alias resolves Opus
  while planning and Sonnet during execution.
- **Any rate or off-rate characterization.** Five runs cannot support one; the
  corpus's ~3% and ~0.3% figures stay attributed to the prior pass.

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
