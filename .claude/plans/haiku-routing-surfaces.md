# Haiku routing surfaces: measure Haiku's locate recall, then pin and route the locate lane

## Context

**Ask:** the engineer, 2026-10-15, after a survey of every model-selection
surface in the harness: *"Yeah this study is worth doing, but I also am
interested in taking several steps back and seeing if there's anything else
that can be safely delegated to haiku."* Then, after the survey's result and
the cost bound were presented: *"I'd like to run the study anyway."*

**Goal: determine empirically whether locate-shaped subagent work runs
acceptably on Haiku in this harness, by piloting `Explore` on Haiku behind a
recall gate whose pass condition is fixed before any number is seen.** The
pilot's product is one decision: whether the locate-and-report task shape may
run on Haiku. It closes the follow-up `pin-explore-to-sonnet.md` deferred
verbatim in its Out of scope (*"which agents, if any, should run Haiku, and
under what task shape does that not degrade output"*).

The Ask's broader question — whether anything *else* can safely move to Haiku
— is answered by the survey below, independently of the gate's outcome: no
other lane clears the safety bar. That answer stands whether the pilot ships
or refuses, and the refuse branch records it either way.

**This is a knowledge investment, not a cost play.** The engineer chose to run
the study knowing the saving it can unlock is bounded at roughly $10 per 60
days at today's dispatch mix (A14). The study is worth its one-time ~$150–200
because it answers A3 with evidence rather than assumption, and because the
answer transfers: it decides whether a whole task *shape* may run on Haiku,
not whether one agent may.

### What the survey found

All figures are scoped to **this repository's sessions** (`--this-repo`),
with the declared-roots union suppressed via `TRANSCRIPT_CONFIG_DIRS_FILE` so
`subagent_type` labels are unredacted. Re-derive from the repo root:

```bash
TRANSCRIPT_CONFIG_DIRS_FILE=/dev/null python3 \
  claude/.claude/scripts/transcript-analysis.py subagent-mix \
  --this-repo --since 60d --reprice-as claude-haiku-4-5-20251001
```

The window is 60 days because `Explore`'s repo-owned Sonnet pin landed
2026-08-09 (`d9793dd`), so a 60-day window ending 2026-10-15 is entirely
post-pin. No figure wider than this repository is used here.

**Every live model-selection surface is Sonnet or Opus.** All fourteen agents
under `claude/.claude/agents/` pin `sonnet` or `opus`. No skill, rule, hook, or
script routes any work to Haiku. `settings.json`'s session default is
`sonnet`. The only historical Haiku agent, check-runner, was retired for
inventing structured data from free-form text
(`docs/case-studies/check-runner.md`), and `docs/cost-levers-considered.md:68`
already closes "Sonnet→Haiku routing for verbose-output absorption" on that
evidence. This plan does not reopen that lever; locate-and-report is the
opposite shape (bounded output the parent can check with `grep`, no absorption
of verbose text).

**Where this repository's 60-day spend sits, and whether the safety bar admits
it.** The bar is A7's: the subagent's output must be parent-verifiable.
Dollar figures in this table are actual spend.

| Lane | Runs | Actual | Haiku-eligible under A7 |
| --- | --- | --- | --- |
| `plan-architect` (Opus) | withheld | $278.67 | No — judgment |
| `code-writer` | withheld | $208.97 | No — authoring |
| `general-purpose` + `fork` | 103 | $138.88 | Only the locate slice |
| `staff-*` / `ciso-reviewer` | withheld | $174.21 | No — rubric judgment |
| `comment-discipline-reviewer` | withheld | $9.35 | Marginal, not pursued (see Out of scope) |
| `Explore` | 0 | $0 | Yes — but no volume |

**What `general-purpose` and `fork` dispatches actually are.** 144 dispatch
sidecars (`subagents/*.meta.json` under this repository's project
directories, last 60 days by file mtime) classified by task shape from each
dispatch's description and prompt head:

| Shape | Count | Share of token volume |
| --- | --- | --- |
| JUDGMENT (review skills run via `general-purpose`, assessments) | withheld | ~53% |
| RESEARCH-SYNTHESIS | withheld | ~23% |
| LOCATE (grep-shaped, answer is a verifiable path list) | 25 | ~15% |
| IMPLEMENT | withheld | withheld |
| READ-SUMMARIZE | withheld | withheld |
| OTHER (mechanical ops, placeholders) | withheld | withheld |

The classification is one judgment pass over descriptions, not a mechanical
measurement; 9 of 144 had no recoverable prompt and were bucketed from the
description alone. JUDGMENT is inflated by two repeated-dispatch clusters, an
eval-bench replay harness and one branch's plan-review loop of 23 numbered
rounds. It is sized to locate the eligible slice, not to support a
precise figure.

**The locate lane is mis-routed, and that is the larger finding.**
`subagent-delegation/SKILL.md` § "Codebase discovery → `Explore` or
`general-purpose`" assigns locate-style search to `Explore`, yet 24 of the 25
LOCATE dispatches went to `general-purpose` or `fork` and only 1 to `Explore`. Two
shipped instructions push that way: `claude/.claude/CLAUDE.md:163` frames
`general-purpose`'s routine use as "discovery and research dispatches", and
`claude/.claude/CLAUDE.md:160` tells every dispatcher to pass `model: sonnet`
on every dispatch including pinned ones. A Haiku pin on `Explore` that nothing
dispatches to saves nothing; the pin and the routing fix ship together or not at
all.

**The rule this replaces predates its own evidence.** CLAUDE.md's *"Haiku:
narrow, deterministic skills only. Never for code authoring or judgment"*
landed 2026-05-02 (`16530ce`); the check-runner incidents ran through May and
the retirement measurement landed 2026-06-23. It also names a routing target no
mechanism implements — skills execute in whatever context invokes them.

## Approach

**Pin `Explore` to `model: haiku` in its repo-owned agent file, amend the three
standing instructions that would either override that pin at dispatch time or
steer locate work away from `Explore`, and gate the whole change on a
stratified recall eval.** Verification runs first; no shipped file is edited
until the gate passes.

Anthropic's sub-agents documentation names this exact override: *"A user or
project subagent named `Explore` overrides the built-in and keeps its own
`model` field, so define one with `model: haiku` to keep exploration on a
lower-cost model."* Anthropic also shipped `Explore` on Haiku by default until
v2.1.198 — *"The built-in Explore agent now inherits the main session's model
(capped at opus) instead of running on haiku."* The motivating defect
(`anthropics/claude-code#45357`) was prompt-size under a ~200-tool MCP surface
(*"Prompt is too long"*), **not reasoning quality**.

**The pin does not work without the instruction edits.** Dispatch-time `model`
params outrank frontmatter (`docs/auto-mode.md:173-175`), and two stow-shipped
files instruct passing `model: sonnet` on `Explore` by name:
`claude/.claude/CLAUDE.md:160` and
`claude-skills/skills/subagent-delegation/SKILL.md:123-125` (*"even on
`Explore`, whose `Explore.md` pin is a request, not a guarantee"*). Any
compliant session would silently restore Sonnet. A third site,
`claude/.claude/CLAUDE.md:163`, steers locate work to `general-purpose` in the
first place. These edits are **functional prerequisites**, not stale-assertion
cleanup.

**Where the design strains the safety bar.** `Explore` clears it for
*precision* — a fabricated path dies to one `grep` — but **not for recall**: an
omitted site is silent. Recall is therefore the gate's primary term, and the
oracle is stratified so the gate cannot be passed on the easy half alone.

**Why not the agents holding the money.** `code-writer`, `plan-architect`, and
the `staff-*`/`ciso` reviewers are code authoring and rubric judgment —
excluded by the safety bar, not deferred for capacity.

**Why re-pin rather than build a new agent.** `subagent-delegation` already
assigns locate-style search to `Explore`; a new agent would duplicate a charter
that exists. The defect is that the charter is not followed, which M4 fixes.

### Assumption ledger

**Root problem:** this harness has never measured whether any of its work runs
acceptably on Haiku; the one task shape that is structurally verifiable is
governed by a rule written seven weeks before the only evidence bearing on it,
and the agent chartered for that shape receives almost none of it.

**Givens** (fixed, beyond this design's reach):

- **G1 — Subagent model resolution follows a fixed four-step vendor order:
  per-invocation `model` param, then the definition's `model:` frontmatter,
  then `CLAUDE_CODE_SUBAGENT_MODEL`, then the main conversation's model.**
  *Vendor-owned; sub-agents docs § "Choose a model", current since v2.1.251.
  `docs/auto-mode.md:173-176` still lists the pre-v2.1.251 order with the env
  var first — corrected in the durable commit (Critical files).*
- **G2 — Harness plan mode forces dispatches to Opus regardless of any pin.**
  *Vendor-owned, gated on `permissionMode`; `docs/cost-levers-considered.md:126`
  records 92/95 dispatches and that no instruction-layer mitigation is known.*
- **G3 — Haiku 4.5 is $1.00/MTok base input vs Sonnet 5's $2.00 and Opus 5's
  $5.00.** *`transcript_analysis/pricing.py:63-68`.*

**Mechanisms:**

- **M1 — `model: haiku` in `claude/.claude/agents/Explore.md`.** `anchors:
  root`. Lighter primitives rejected: (a) `CLAUDE_CODE_SUBAGENT_MODEL` — since
  v2.1.251 it ranks below frontmatter (G1), so it would not touch pinned
  reviewers, but it forces every *unpinned* built-in (`general-purpose`,
  `fork`) onto Haiku — the wrong scope; `docs/auto-mode.md:207` advises
  against it by name. (b) dispatch-time `model:
  haiku` param — requires every caller to remember it and contradicts the
  standing rule this plan is already amending. (c) `--agents` CLI JSON — an eval
  mechanism, not shipped config.
- **M2 — Stratified recall eval under `evals/`.** `anchors: row A3`. Lighter
  primitives rejected: `run_skill_evals.py` measures skill *triggering*, not
  retrieval, and its `--model` choices exclude Haiku; `subagent-mix` prices
  dispatches and cannot score correctness; `review_bench` scores reviewer
  findings against a defect set, not retrieval against a path oracle.
- **M3 — Non-plan-mode rows in `measure_subagent_model_resolution.py`.**
  `anchors: row A5`. Run 8 (Haiku-valued override, default permission mode)
  exists in this branch's working tree. Step 2b adds run 9, the Sonnet-valued
  twin, and re-runs both against the rebased script before any Step 3 spend.
- **M4 — Instruction edits that route locate work to `Explore` and stop
  overriding its pin.** `anchors: row A13`. Lighter primitives rejected: (a) a
  hook that rewrites `subagent_type` on dispatch — it would silently change
  what the engineer asked for, and the routing rule already exists in prose;
  the fix is to stop two other prose sites contradicting it. (b) adding "use
  `Explore` for locate" to each dispatching skill (`plan-it`, `plan-review`,
  `ready-for-review`) — the contradiction lives in `CLAUDE.md`, which loads
  every session, and patching each skill restates one rule at N sites. (c)
  pinning without touching routing — rejected by A13's measurement: a pin on
  an agent that receives 1 of 25 locate dispatches changes nothing.
- **M5 — Answer extraction and site matching in the scorer.** `anchors: row
  A3`. The reused `measure_subagent_model_resolution.py` reads only model IDs
  and tool names from a dispatch's transcript, never its answer text, so
  scoring needs new code: parse `Explore`'s final answer into a site set and
  match it against the case's key. Lighter primitives rejected: (a) free-text
  parsing of a prose answer — the most bug-prone piece of the eval, and a
  path-format mismatch would silently mis-measure the gate; instead the
  dispatch prompt requires a structured answer (below) so extraction is a
  JSON parse. (b) an LLM judge for matching — reintroduces judgment into a
  measurement whose whole point is mechanical verifiability.

**Assumptions:**

| # | Assumption | Tag |
| --- | --- | --- |
| A1 | `Explore`'s 60-day figure for this repository (0 priced runs) is post-pin, and the `general-purpose`+`fork` lane is 103 runs / $138.88 | `[verified: TRANSCRIPT_CONFIG_DIRS_FILE=/dev/null subagent-mix --this-repo --since 60d --reprice-as claude-haiku-4-5-20251001, 2026-10-15; pin date from git log --follow on Explore.md]` — published under the owner authorization in A22, since `subagent-mix` has no scope refusal of its own. — supersedes the prior round's 8-day figure, re-measured on a wider post-pin window. |
| A2 | The honored-Sonnet arm, measured | `[verified: 2026-08-18 session, TRANSCRIPT_CONFIG_DIRS_FILE=/dev/null subagent-mix --this-repo --since 8d --reprice-as claude-sonnet-5; Actual$ identical with and without --reprice-as]` — historical; the figures are withheld because they predate the publication authorization in A22, and `Explore` volume has since fallen to A1's figure, so this arm no longer bears on the decision. |
| A3 | **Haiku's locate recall is adequate for `Explore`'s charter** | `[verified: FALSE — Step 3 result table, 2026-10-15]` — recall ties on grep-shaped work and trails by ~4 points on semantic work, but precision (0.931 vs 0.986) and one all-unusable sample fail the gate. The refuse branch records it. |
| A4 | `Explore`'s pin is not honored in plan mode | `[verified: docs/cost-levers-considered.md:126 (92/95)]` |
| A5 | A `model:` pin is honored *outside* plan mode, and the `--agents` override resolves symmetrically for both arms | `[verified: measure_subagent_model_resolution.py --run 8 and --run 9, 2026-10-15, against the rebased script on Claude Code 2.1.283 — opus parent, default permission mode; run 8 observed claude-haiku-4-5-20251001 ($0.22), run 9 observed claude-sonnet-5 ($0.24); requested_model_param=none, agent_identity=requested, sidecars present]` — reproduces the 2026-08-18 pre-rebase run-8 result ($0.26). |
| A6 | Anthropic ran `Explore` on Haiku until v2.1.198, moving off for prompt-size not reasoning quality | `[verified: .claude/plans/pin-explore-to-sonnet.md:33-44]` |
| A7 | Pilot `Explore` specifically; safety bar is "output must be parent-verifiable" | `[engineer-verified]` — chosen 2026-08-18. The 2026-10-15 survey (this plan's Context) found no other lane that clears the bar; that is the author's finding, not a further engineer decision. |
| A8 | **Superseding `pin-explore-to-sonnet.md`'s A10** (Sonnet ceiling is an acceptable trade) is deliberate | `[engineer-verified]` — the engineer, 2026-08-18, after the contradiction was surfaced: try Explore as the candidate, since what matters is quality of work and cost. |
| A9 | The vendor's MCP prompt-size ceiling does not bind this harness | `[engineer-verified]` — the engineer's judgment that the ~200-tool failure condition does not apply to their context-light harness. Not falsified by run 8 (no "Prompt is too long" failure), a corroborating opportunity, not the source of the tag. Residual: this ships to all stow users, whose surfaces differ — disclosed in Step 6. |
| A10 | Adding a Haiku pin requires editing the roster test, not just the agent file | `[verified: test_agent_roster.py:99-101 (NON_REVIEWER_MODELS), :543 (test_model_pinned_to_expected_value), :559 (test_expected_model_map_is_complete)]` |
| A11 | Recall, not precision, is the binding risk | `[verified: FALSE — Step 3 result table]` — precision was the term that failed as fixed (0.931) and under the single-sample exclusion (0.962); recall failed only through one catastrophic sample. The safety-bar reasoning ("a fabricated path dies to one grep") held for the parent's ability to *check* output, not for the rate at which Haiku produces output that needs checking. |
| A12 | The verbose-output-absorption Haiku lever stays closed | `[verified: docs/cost-levers-considered.md:68]` — a locate agent absorbs no command output. |
| A13 | Locate-shaped dispatches reach `general-purpose`, not `Explore` (24 of 25 in the window) | `[verified: 2026-10-15 sidecar classification of this repository's project directories, 144 sidecars]` — a manual read with no command behind it, published under the owner authorization in A22. — the *count* is a judgment-pass sample; the *direction* (near-zero `Explore` volume) is independently confirmed by A1's `subagent-mix` figure. The cause — which instruction steers dispatchers — is **`[unverified]`**; the two CLAUDE.md sites named above are the only shipped text that could, and M4 edits both. |
| A14 | The saving the pin can unlock is bounded at ~$10 / 60 days (LOCATE ≈ 15% of a $138.88 lane, halved at Haiku) | `[verified: arithmetic over A1's lane total and A13's token share, 2026-10-15]` — derived from A1 and A13, so covered by A22's authorization. — a token-share proxy, not a repriced run; sized to bound the return, not state it. The engineer chose to proceed after this bound was presented: `[engineer-verified: "I'd like to run the study anyway"]` (2026-10-15). |
| A15 | `claude/.claude/CLAUDE.md` is over its byte ratchet, so the M4 edit must not grow the file | `[verified: check-claude-md-length.sh:55 sets GLOBAL_CLAUDE_MD_BYTE_LIMIT=25600 and :6-7 denies a staged file that is over the limit and longer than the committed version; the file is 29,569 bytes on 2026-10-15; the drafted replacements in Critical files net −12 bytes, computed by applying them to an in-memory copy]` |
| A16 | `select-tests.py` maps the sibling measure script by folding it into the review-bench predicate plus two cross-domain exception entries, not by a standalone row | `[verified: claude/.claude/scripts/select-tests.py:67-72 (constants), :535-544 (_is_review_bench_change fold), :559 (DOMAIN_RULES), :710-716 (CROSS_DOMAIN_EXCEPTIONS)]` — the new scorer imports `measure_subagent_model_resolution` directly, so it takes the same fold-plus-exceptions shape; `TestCrossDomainReadCompleteness` fails loudly if the mapping is incomplete. |
| A17 | M4 (the routing edits, coupled to the pin) is in scope, beyond the Ask's "run the study" | `[engineer-verified: "Yes, ship M4 with the pin"]` — label selected 2026-10-15 at plan review; the option's rationale text is the author's. |
| A18 | The refuse branch records A13's routing finding whatever the gate's outcome | `[engineer-verified: "Yes, record it either way"]` — label selected 2026-10-15 at plan review. |
| A19 | `effort: low` is safe under `model: haiku` | `[verified: sub-agents docs "Supported frontmatter fields" — levels depend on the model; model-config docs "Adjust effort level" — Haiku 5.5 supports low/medium/high/xhigh/max, Haiku 4.5 is unlisted and ignores the field; an unsupported level falls back rather than erroring]` — the line stays. |
| A20 | The $300 cumulative stop is new driver logic, not a reuse | `[verified: measure_subagent_model_resolution.py:81-90 derives a per-process cap passed as --max-budget-usd at :255; main()'s loop at :951-968 has no running total]` — the sidecar cost data the scorer already reads is enough to build one. |
| A21 | The `haiku` alias is provider- and version-dependent: the vendor docs list Haiku 5.5 for the Anthropic API and Haiku 4.5 for Bedrock/Vertex/Foundry, yet this machine observed **`claude-haiku-4-5-20251001`** on Claude Code 2.1.283 | `[verified: model-config docs alias-resolution table; run 8 sidecar observed_model_ids on 2026-10-15; transcript_analysis/pricing.py:68 already prices claude-haiku-4-5-20251001]` — the eval records the observed full model ID per sample and reports which Haiku it measured. No `pricing.py` row is needed for the ID observed here; the durable commit adds one only if a later run observes an unpriced ID. A1's and A14's Haiku 4.5 repricing matches the model actually resolved on this machine. |
| A22 | The transcript-derived figures may be published | `[engineer-verified: "Authorize both A and B as stated"]` — the owner, 2026-10-16 (UTC), answering a question that named (A) the lane figures from `TRANSCRIPT_CONFIG_DIRS_FILE=/dev/null python3 claude/.claude/scripts/transcript-analysis.py subagent-mix --this-repo --since 60d --reprice-as claude-haiku-4-5-20251001` (plan-architect $278.67, code-writer $208.97, general-purpose+fork 103 runs / $138.88, staff/ciso reviewers $174.21, comment-discipline-reviewer $9.35, Explore 0 runs; derived ~$21 actual / ~$10 saved per 60 days) and (B) the manual classification of this repository's 144 dispatch sidecars (24 of 25 locate-shaped to general-purpose/fork, 1 to Explore; bucket shares ~53/23/15%). The option text and this restatement are the author's; the quoted label is the engineer's. |

## Verification — runs first, gates the edits

**Nothing under "Ship branch" is edited until Step 3 returns `PASS`.** The
durable-commit files under "Create — both branches" are the instrument, so
Step 2b's run-9 edit and the scorer itself are written before Step 3 runs —
that is the carve-out, and it is the only one. Steps 1 and 2 are complete and
recorded in the ledger (A2, A5); they are kept here so a reader sees the
gate's prerequisites were met, not assumed.

**Step 1 — Cost picture. Done.** A1 and A14 hold the current figures; publish
only this-repository, post-pin numbers, and state the `TRANSCRIPT_CONFIG_DIRS_FILE`
override alongside any number that ships.

**Step 2 — A5 and A9 kill-check. Done.** Run 8 observed `haiku` outside plan
mode with no "Prompt is too long" failure on 2026-08-18, and again on
2026-10-15 against the rebased script (A5).

**Step 2b — Smoke both arms against the current script, before any Step 3
spend. Done 2026-10-15: run 8 → `haiku`, run 9 → `sonnet`.** Two single
dispatches, each under the existing per-run cap:

- Re-run `--run 8` (Haiku-valued `--agents` override, default permission mode)
  and restamp A5 with the date and observed family.
- Add run 9 to `RUN_MATRIX`: identical to run 8 except the override's `model`
  value is `sonnet`. Nothing in runs 1–8 ever exercises a Sonnet-valued
  override, yet Step 3's control arm depends on one resolving symmetrically.
  Shape: add a `DISPATCH_EXPLORE_SONNET` dispatch label and an
  `EXPLORE_SONNET_OVERRIDE_MODEL = "sonnet"` constant beside the Haiku pair;
  `declared_model_pin_for_dispatch(dispatch)` and `build_run_command` map each
  label to its own override value, so the existing `dispatch: str` signatures
  do not change. Tests: `DISPATCH_EXPLORE_SONNET` exercised through
  `declared_model_pin_for_dispatch` directly (not via the
  staff-backend-engineer fallback, which would return the right answer for
  the wrong reason), the matrix test extended to nine runs, and the
  "only runs 6–8 dispatch explore-haiku" assertion kept true by giving run 9
  the new label.

Outcomes: run 8 → `haiku` and run 9 → `sonnet` → proceed. Either arm
misresolving → the `--agents` mechanism is not symmetric and the plan stops
before spending. A "Prompt is too long" failure → A9 is falsified on this
machine and the plan stops.

**Step 3 — Measure recall (the gate).** Two fixture strata, scored and reported
separately, never pooled. **Every case targets this repository** — answer keys
are derived from and checked against public, committed content, so the fixture
file carries no private-project provenance. Case shapes mirror the LOCATE
bucket's observed shapes (enumerate call sites of a helper; inventory which
tests cover a mechanism; find every site a given rule is cited), since that is
the lane the pin will serve.

- **Stratum A — grep-derivable.** Site unit is the **file path** (repo-relative,
  normalized). Answer key is the set of files `git grep -l` returns for the
  case's pattern at the pinned commit. At least 8 cases, of which **at least 5
  have ≥5 ground-truth files** — recall is only exercised when a model can
  return a subset.
- **Stratum B — semantic.** Site unit is the enclosing **function or
  top-level block**, keyed `path:symbol`. Open-scope questions ("where is X
  handled") with a hand-curated answer key, because no mechanical oracle
  exists for them. At least 5 cases, of which **at least 3 have ≥5
  ground-truth sites** — a single-site semantic case degenerates recall to a
  coin flip. A gate passing only on Stratum A would measure the half where
  literal search already suffices, which is where the two models least
  diverge.

**Answer contract (M5).** The dispatch prompt instructs `Explore` to end its
reply with one fenced ```` ```json ```` block containing a JSON array of
strings — file paths for Stratum A, `path:symbol` for Stratum B — and nothing
else in that block. For Stratum B the prompt defines `symbol` the way the
curators did: the name of the function that *contains* the matching line,
not the function called there; the literal `top-level-main` for a line
outside any function; one entry per containing block, not per line. Without
that sentence a model returns the callee's name, a line number, or a bare
path, and scores as a miss plus a false positive — which is exactly what the
first live run measured (below). A closed alias set (`main`, `top-level`,
and eight similar spellings) canonicalizes to `top-level-main`; numeric
symbols and bare paths are deliberately left to score as false positives. The scorer takes the dispatched `Explore` agent's final
assistant text from its own `subagents/*.jsonl` sidecar (a new text-block
reader beside `_scan_subagent_jsonl`, which reads only `tool_use` blocks):
the **last** assistant record in the file, its `text` content blocks
concatenated in order, `tool_use` blocks ignored. It parses the **last**
fenced JSON block in that text and normalizes each entry (strip a leading
`./`, collapse `//`, no trailing whitespace), then deduplicates the list so a
repeated item counts once for both recall and precision. A sample with no
parseable block, or whose block parses to anything other than a JSON array of
strings, scores recall 0 and precision undefined, and is counted in a reported
`format_failures` tally per model so a formatting gap is visible rather than
folded into recall.

**Matching.** Stratum A: exact normalized path equality. Stratum B: exact
normalized `path` and exact `symbol` string; a returned `path:symbol` whose
symbol exists in the file but is not in the key is a **false positive only
if** it contains nothing related to the question — that call is made once per
case at curation time, by listing the known near-miss symbols in the case's
`accepted_near_misses` field, so scoring stays mechanical. A case needing more
than one such call is rejected as under-specified rather than scored.

**Stratum B answer-key validation.** Each key is built by one curator and
independently confirmed by a second pass that must find zero additional sites
before the case is admitted. An under-curated key inflates recall for both
models identically, so the gate cannot detect it — validation is the only
defense. The fixture schema carries a `second_pass_confirmed: true` field on
every Stratum B case, and the fixture-schema test rejects a Stratum B case
without it.

**Arithmetic, fixed here so two implementers converge:**
- **Per-sample recall** = matched key sites / key sites. **Per-sample
  precision** = matched returned items / returned items.
- **Per-case recall** = mean over the K samples. Reported spread = min and max
  over the K samples.
- **Per-stratum mean recall** = macro-average over cases (each case weighted
  equally), for **both** strata.
- **Gate term 2's per-case gap** uses per-case mean recall (Haiku minus
  Sonnet). Min-of-K is reported alongside so a single-sample cliff is visible,
  but does not drive the gate.
- **Precision CI** is the Wilson score interval at 95% (z = 1.96), hand-rolled
  in ~10 lines — no stats dependency is added. Source: Wilson (1927), as
  presented in Brown, Cai & DasGupta, *Interval Estimation for a Binomial
  Proportion*, Statistical Science 16(2), 2001, §4.1. The **unit of analysis
  is the pooled set of returned items across all cases in both strata and all
  K samples for the model under test**: n = returned items, x = false
  positives. Precision is not split per stratum; only recall is. With ≥13
  cases × K=5 and ≥5-site cases, n is in the hundreds; at n = 65 the zero-FP
  Wilson lower bound is 0.944 and the floor of 0.90 needs n ≥ 35, so the
  pooled-items unit is the only one of the three candidate units under which
  the gate is callable at all.

Run **K = 5 samples per case per model**, Haiku and Sonnet on identical cases.
Report per-case recall and precision with observed spread, not just means.

**Dispatch mechanics.** Each sample launches a headless parent in an isolated
temp project directory (the environment runs 8 and 9 validated) with an
`--agents` override that supplies `Explore` with `model: haiku` or `model:
sonnet`, and a dispatch prompt carrying run 8's "dispatch by name only, pass
no model param" clause so the parent cannot restore Sonnet from ambient
CLAUDE.md. **The repository Explore searches is a separate clean checkout at
the fixture's `pinned_commit`**, passed as `--repo-dir` and validated (HEAD
equals the pinned SHA, `git status --porcelain` empty) before any launch —
the working tree that holds the scorer differs from that commit, so searching
it would turn the eval's own files into false positives. The parent is
launched with `--add-dir <repo-dir>` so the subagent's Read/Grep/Glob are
permitted outside its cwd; without that grant every read is silently denied
in headless mode, which the K=1 smoke run below demonstrated. The prompt
names that directory and asks for paths relative to it, and normalization
strips the absolute prefix if a model returns it anyway. The directory is
resolved through symlinks before use, so on macOS a `/tmp/...` argument and
a model-reported `/private/tmp/...` path strip to the same relative path. `--keep-artifacts
DIR` retains each sample's session and subagent transcripts for diagnosis
only — `DIR` lives outside the repository (under `/tmp` here) and is never
staged or committed, since those transcripts carry account-local session
paths — and the report carries each sample's extracted text and parsed
items; `--cases` restricts a
run to named cases and `--max-k-raises 0` disables the `UNCALLABLE` K-raise
for smoke runs. The observed model is
read by reusing `parse_subagent_dispatches` — its
`DispatchObservation.observed_model_family` comes from the paired
`subagents/*.jsonl` transcript, while `*.meta.json` carries only the requested
param — and **a sample whose observed model family differs from its arm is
discarded and re-run, not scored**; otherwise a silently-Sonnet "Haiku" sample
inflates the Haiku arm.

**Verification spend.** ≥13 cases × 2 models × K=5 = **≥130 live
dispatches**, one-time. The pre-measurement estimate was $150–200; runs 8 and
9 observed ~$0.22–0.24 per dispatch, so the K=5 batch is nearer **$35**.
Realized before the batch: a K=1 smoke run spent **$32.75** scoring nothing,
because the parent lacked the `--add-dir` grant and the `UNCALLABLE` K-raise
then fired on a 1-sample run — the two scorer flags above exist because of
it. Diagnosis spent a further ~$1.50 on six single samples.

**First full K=5 run (2026-10-15, $63.52 including one K-raise to 8) was an
instrument calibration, not a verdict.** Stratum A came back at recall 1.00
for every case on both models with 2 false positives in 653 Haiku items and 0
in 639 Sonnet items. Stratum B came back at ~0.30 recall for both models,
with Stratum-B-only precision of 0.31 (Haiku) and 0.39 (Sonnet) — the
pooled figures of ~0.8 were carried by Stratum A. Identical degradation on
both arms is the signature of
an instrument defect, and the retained per-sample items showed three: the
prompt never defined `symbol` (fixed in the answer contract above); models
label top-level code `main` (the alias set above); and the 90-second
per-dispatch timeout killed the parent mid-search on the heavier Stratum B
cases (subagent spans of 64–82 s), producing empty final answers that scored
as format failures. The gate is re-run in full with those three fixes and
`--timeout-s 300`; the first run's figures are not pooled with it.
Two distinct caps: **reuse `PER_RUN_BUDGET_CAP_USD`** as the per-dispatch
`--max-budget-usd` ceiling, and **add a cumulative stop** — the scorer sums
each sample's sidecar-reported cost as it goes and aborts the whole batch,
not just the next dispatch, once the running total crosses $300, then writes
the partial report. The cumulative stop is new driver logic (A20) and is
unit-tested like the gate function.

**Adoption gate, fixed here before any number is seen.** The gate function
returns one of three values, `PASS`, `FAIL`, `UNCALLABLE`:
1. Haiku's mean recall within **5 points** of Sonnet's **in each stratum
   separately** — else `FAIL`.
2. **No individual case** shows a Haiku-minus-Sonnet per-case mean recall gap
   exceeding **15 points** — else `FAIL`. This stops a per-case cliff hiding
   inside a passing mean.
3. Haiku precision **≥ 0.98** with the Wilson lower bound **≥ 0.90** — a
   point estimate below 0.98 is `FAIL`; a lower bound below 0.90 is
   `UNCALLABLE` regardless of the point estimate, because N is too small to
   decide either way.

**Result (2026-10-15, corrected instrument, K=5, 130 scored samples, 0
format failures, 0 discards, $47.81): `FAIL`.** Observed models
`claude-haiku-4-5-20251001` and `claude-sonnet-5`.

| Term | Haiku | Sonnet | Verdict |
| --- | --- | --- | --- |
| 1. Stratum A mean recall | 0.995 | 1.000 | within 5 points |
| 1. Stratum B mean recall | 0.917 | 0.953 | within 5 points |
| 2. Largest per-case gap | B3: −20 points (samples 1.0, 1.0, 1.0, **0.0**, 1.0) | — | **fails** (>15) — see note |
| 3. Pooled precision | 0.931 (46 FP / 670), Wilson lower 0.910 | 0.986 (9 / 653), Wilson lower 0.974 | **fails** (<0.98) |

Term-2 note: the scorer as run reduced the signed per-case gaps with
`max()`, which selects Haiku's *best* case (B2, +3 points) rather than its
worst, so term 2 did not fire during the run and the recorded `FAIL` came
from term 3 alone. Review caught it before the commit; the fix (reduce with
`min()`, compare against −15) and a corrected test land in the durable
commit, and under the fixed reduction the same data fails term 2 on B3 as
the table states. The verdict is unchanged either way.

Per-case Haiku recall: A1–A4, A6, A7 at 1.00; A5 0.97; A8 0.99; B1 0.98;
B2 1.00; B3 0.80; B4 0.80 (Sonnet also 0.80 — the key's fifth site is one
neither model lists); B5 1.00.

Sensitivity, stated so the record is honest rather than to rescue the gate.
The gate is applied as fixed; none of the exclusions below is made.
- The B3 cliff and 21 of Haiku's 46 false positives come from one sample
  that reported every path without its leading `claude/` segment; 20 of its
  21 entries are otherwise-correct sites, and one
  (`_lib_review_pr_select_session_worktrees`) is a site the second-pass
  curator had judged out of scope and the review judged on-topic — it moves
  to B3's `accepted_near_misses` for future runs (3 Haiku and 1 Sonnet
  occurrences in this run, no verdict effect). Excluding that one sample,
  Haiku is 25 false positives in 660 items (0.962, Wilson lower 0.945):
  still below 0.98.
- B4's 13 Haiku and 8 Sonnet false positives are largely the same adjacent
  functions on both arms (Haiku adds one more), which points at the key's
  empty `accepted_near_misses` rather than at either model. Excluding those
  as well, Haiku reaches roughly 12 in ~640 (≈0.98, at the threshold, with
  the Wilson lower bound below it) against Sonnet's 1 in 653 (0.998).
- So term 3 fails as fixed and under the single-sample exclusion, and is
  marginal only under a reading that also amends B4's key. Term 2 fails only
  through the one catastrophic sample, which is itself a finding: on
  semantic locate work Haiku produced one sample in 25 whose entire output
  was unusable, and Sonnet none in 25.

The plan therefore takes the **refuse branch**. A3 is answered: Haiku's
locate recall matches Sonnet's on grep-shaped work and trails it by ~4 points
on semantic work, but its precision and per-sample robustness do not clear
the bar fixed above.

`PASS` → ship branch. `FAIL` → refuse branch. `UNCALLABLE` → raise K by 3 for
every case on both models (another ~78 dispatches) and re-evaluate, still
under the $300 cumulative stop; `UNCALLABLE` a second time, or the stop firing
first, → refuse branch, recorded as "not decidable within budget", which is a
different verdict from "Haiku failed" and the record must say so. The
`evals/test_measure_explore_recall.py` suite pins all three return values and
the branch each drives.

**Step 4 — Suite and lint.** Runs *after* the Critical-files edits — it checks
the edited tree. From this worktree:
`../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py` (A16's
mapping makes it pick up the scorer's tests) and
`../../../.venv/bin/ruff check claude/.claude/ claude-skills/ evals/`. `evals/`
stays absent from `.github/workflows/tests.yml`, preserving `evals/README.md`
§ "Why local only — never CI".

**Step 5 — Confirm pin and routing post-merge.** Fourteen days after merge,
check two things from per-dispatch sidecars, not from `subagent-mix`'s
aggregate `Observed` column (which has no permission-mode breakdown, so it
cannot separate G2's plan-mode Opus dispatches from a genuine pin failure):

- **Pin.** List every `Explore` dispatch in the window with its observed
  model family (via `parse_subagent_dispatches`). No per-dispatch permission
  mode exists anywhere in the sidecars — `permissionMode` is a per-session
  value read from the session's own init record, the way `commit-gate
  --by-permission-mode` reads it (`transcript-analysis.py:1524-1532`) — so
  attribute each non-`haiku` dispatch to its parent session's mode at that
  granularity, and disclose that a session which toggles plan mode mid-way is
  attributed one value for all its dispatches. Any non-`haiku` dispatch from a
  session whose mode is not `plan` is a pin failure: **revert the ship-branch
  commit** — the change would be delivering the quality tradeoff without the
  saving. This is a one-off read, not a new tool.
- **Routing.** `subagent-mix --this-repo --since 14d`: `Explore`'s run count
  is no longer near zero while `general-purpose`'s has not risen to compensate.
  If the pin holds but routing has not moved, M4's edits did not reach the
  cause A13 names `[unverified]`; record that in
  `docs/cost-levers-considered.md` as a follow-up rather than reverting a
  working pin.

**Step 6 — Disclose what is not monitored.** Five residuals, all recorded in
the CHANGELOG entry:

- **Provider-dependent Haiku (A21).** `model: haiku` resolves to Haiku 5.5 on
  the Anthropic API and Haiku 4.5 on Bedrock, Vertex, and Foundry. The gate
  measures whichever this machine resolves, and the report names it; a stow
  user on another provider runs an unmeasured Haiku under the same pin. The
  project-level override below is their remedy too.

- **Prompt-size overflow (A9).** Detection for a stow user whose MCP surface
  exceeds Haiku's prompt budget is **user-report-only**; there is no
  cross-consumer telemetry and `subagent-mix` sees only this machine. Include a
  tool-count figure for this machine **as an explicitly dated snapshot** that
  will drift as plugins change — a bare integer only, never the names of
  individual tools or MCP servers. **Name the remedy alongside the gap:** a
  project-level `.claude/agents/Explore.md` with `model: sonnet` overrides the
  stowed one for that repo only, per the same agent-definition precedence the
  pin itself relies on.
- **CLI-version drift.** A stow consumer on an older Claude Code release whose
  handling of a same-named `Explore` override differs from the cited behavior
  gets either a silent no-op pin or a mis-resolved one; detection is
  user-report-only in the same way as A9.
- **Public-repo familiarity (A3).** Every fixture case targets this public
  repository, which both models may have seen in training. Measured recall
  may partly reflect familiarity with this codebase rather than locate skill
  on an unseen one; the gate cannot separate the two, and the production
  population is mostly private repos.
- **Recall drift (A3).** Step 3's gate is a one-time pre-merge measurement.
  Nothing re-runs it as the codebase, the query mix, or the Haiku model itself
  changes, and `evals/` is never-CI by design. There is no cadence and no alert;
  a later regression surfaces only if someone re-runs the eval deliberately.

**Commit shape.** With the gate at `FAIL`, nothing under "Ship branch" is
edited and no Step 5 revert can arise, so the refuse branch lands as **one
commit**: the durable instrument plus the refuse-branch records and the
CHANGELOG entry. The two-commit split below is retained for the record as
the shape a `PASS` would have required:

1. **Durable commit** — everything under "Create — both branches" (scorer,
   tests, fixtures, `select-tests.py` mapping, `evals/README.md` pointer, the
   run-9 matrix row) plus `CHANGELOG.md`. This commit records the attempt and
   outlives any revert.
2. **Ship commit** — the functional edits under "Ship branch" only. Rolling
   back the pin is `git revert` of this one commit; the eval stays runnable
   and the changelog record stays in the tree.

`require-code-review.sh` keys its marker on the hash of the staged diff at
each `git commit`, so the two commits need two stage-then-`/code-review`
cycles: stage the durable set, review, commit; then stage the ship set,
review again, commit. One review over the combined working tree matches
neither staged diff.

## Critical files

### Ship branch — only if Step 3's gate returns `PASS`

- `claude/.claude/agents/Explore.md` — `model: sonnet` → `model: haiku`.
  `effort: low` stays unless A19 resolves against it.
- `claude/.claude/CLAUDE.md` — **functional prerequisite (M4).** Four exact
  replacements in Model & Effort Routing, net −12 bytes against the committed
  file (A15), so the ratchet admits the commit:
  - `:160`, the Sonnet bullet. Replace "Pass an explicit `model: sonnet` on
    every dispatch, even ones with a `model:` pin — both are requests, not
    guarantees, and resolution doesn't always follow them; it costs nothing
    either way (see `docs/auto-mode.md` in the claude-config repo for the
    current measurement)." with "Pass an explicit `model: sonnet` on every
    dispatch except `Explore`, even ones with a `model:` pin — a pin is a
    request, not a guarantee (see `docs/auto-mode.md` in the claude-config
    repo)." The dropped "costs nothing either way" clause would be false for
    `Explore`, where the param is exactly what overrides the pin.
  - `:161`, the Haiku bullet. Replace the whole bullet with: "**Haiku:**
    `Explore` only — locate-and-report, output checked with `grep`. Dispatch
    it by name, no `model` param. Never for authoring, synthesis, or
    judgment."
  - `:163`, the `general-purpose` bullet. Replace "Its routine use is discovery
    and research dispatches (whole-file exploration per Codebase discovery)."
    with "Its routine use is research and whole-file exploration;
    locate-shaped lookups go to `Explore`." and replace "`Explore` is pinned to
    Sonnet via" with "`Explore` is pinned to Haiku via". Rest of the bullet
    unchanged.
- `claude-skills/skills/subagent-delegation/SKILL.md:123-125` — **functional
  prerequisite (M4).** In § "Debug-investigation probe → `general-purpose` or
  `Explore`", replace "Pass an explicit `model: sonnet` per `CLAUDE.md`'s Model
  & Effort Routing rule on both — even on `Explore`, whose `Explore.md` pin is
  a request, not a guarantee." with "Pass an explicit `model: sonnet` on
  `general-purpose` per `CLAUDE.md`'s Model & Effort Routing rule; dispatch
  `Explore` by name with no `model` param, which would override its
  `Explore.md` pin." Same line count, so the skill's line ratchet is
  untouched. The Codebase-discovery section's existing `Explore`-for-locate
  rule is already correct and does not move.
- `claude/.claude/hooks/tests/test_agent_roster.py:101` — `NON_REVIEWER_MODELS`
  entry for `Explore.md`: `"sonnet"` → `"haiku"`. **Reuse** the existing map and
  its completeness guard at `:559`.
- `docs/auto-mode.md:189` — the requested-model table's `Explore` row.
- `docs/transcript-analysis.md:273` — the illustrative "`Explore` … `sonnet`
  `Declared` pin" example; generalize it to not name a pin value.

Not touched: `claude-skills/skills/plan-review/SKILL.md:58` sends plan-review's
own exploration to `general-purpose` because it must read whole files; that is
the whole-file lane, correctly placed, and stays.

### Refuse branch — if the gate returns `FAIL`, or `UNCALLABLE` within budget

- `docs/cost-levers-considered.md` — new section recording the lever, the
  verdict (`FAIL` versus "not decidable within budget", kept distinct), the
  measured reason, and the fixture set that produced it. **Reuse** the existing
  Lever/Verdict/Measured-reason table shape. The record must carry, for a
  reader who never sees this plan: the two concrete Haiku failure shapes
  (precision 0.931 against 0.986, 46 versus 9 false positives in ~660 items;
  one semantic-case sample whose every path dropped its leading directory,
  producing the only per-case cliff); the Stratum A tie and the ~4-point
  Stratum B recall gap, so a future reader knows what *did* hold; the total
  study spend (~$146 across the smoke, calibration, and corrected runs); the
  fact that the instrument changed between the calibration and corrected
  runs (symbol definition, alias canonicalization, 300 s timeout) and that
  only the corrected run is scored; the this-repository `--since 60d` cost bound
  (A14) that sized the lever; and the re-run command with `--repo-dir`,
  `--timeout-s 300`, and the fixture's pinned commit. Also record A13's
  routing finding (A18), since it stands regardless of the gate's outcome.
- `docs/design-decisions/explore-haiku-pin-declined-on-measurement.md` — new
  per-decision file, citing `docs/case-studies/check-runner.md` as the prior
  Haiku data point. No index line: `.claude/rules/design-decisions.md` retired
  the hand-maintained index, and `docs/design-decisions.md` is now a pointer
  to the directory.

Both branches keep the `evals/` additions and runs 8–9; a measurement that
produced a refusal is still worth keeping runnable.

### Create — both branches (the durable commit)

- `evals/measure_subagent_model_resolution.py` — run 9 (Step 2b): a
  per-run override-model field replacing the `EXPLORE_HAIKU_OVERRIDE_MODEL`
  constant, a `DISPATCH_EXPLORE_SONNET` label, and the "eight"→"nine" bound
  strings; `evals/test_measure_subagent_model_resolution.py` extends the matrix
  tests the way run 8 did.
- `evals/measure_explore_recall.py` — the stratified scorer. **Reuse** from
  `measure_subagent_model_resolution` (imported as a module, not copied):
  the subprocess-launch shape, the `--agents` override pattern,
  `parse_subagent_dispatches` for the arm-mismatch check, and
  `RunResult`/`observation_to_dict` for the per-dispatch record. **New** (M5,
  A20): the final-answer text reader for `subagents/*.jsonl`, fenced-JSON
  extraction and normalization, Stratum A/B matching with
  `accepted_near_misses`, per-sample/per-case/per-stratum aggregation, the
  Wilson interval, the tri-state gate function, the bounded `UNCALLABLE`
  K-raise loop (`--max-k-raises`), the cumulative $300 stop, the validated
  `--repo-dir` plus `--add-dir` launch grant, `--keep-artifacts`, `--cases`,
  and the report writer (per-case recall, precision, min/max spread,
  `format_failures`, realized spend, per-sample extracted text and parsed
  items, `repo_dir` and `pinned_commit`).
- `evals/test_measure_explore_recall.py` — unit tests mirroring
  `test_measure_subagent_model_resolution.py`'s fixture style
  (`_write_subagent_jsonl`). **Must cover:** the answer reader and fenced-JSON
  extraction against synthetic transcripts, including a transcript with no
  block, one with prose after the block, and one whose block is valid JSON
  but not an array of strings; path normalization and deduplication; Stratum A and
  B matching including `accepted_near_misses`; the aggregation arithmetic
  above; the Wilson interval at known (x, n) pairs; the gate function's three
  return values and which branch each drives; the arm-mismatch discard; the
  cumulative stop firing mid-batch; and the fixture-schema check that rejects
  a Stratum B case lacking `second_pass_confirmed`. That function decides ship
  versus refuse; an `or`-for-`and` slip or an uncallable branch defaulting to
  pass would mis-decide the whole plan with nothing defending against it.
- `evals/fixtures/explore-recall-cases.json` — the two strata, every case
  targeting this repository's committed content, with `stratum`, `question`,
  `key`, `accepted_near_misses`, and (Stratum B) `second_pass_confirmed`.
- `claude/.claude/scripts/select-tests.py` — map the new scorer and test the
  way the sibling pair is mapped (A16): fold both into the review-bench
  predicate and add the matching `CROSS_DOMAIN_EXCEPTIONS` entries, so an edit
  to `measure_subagent_model_resolution.py` re-selects the scorer's tests and
  vice versa; cover it in `TestSelectPytestTargets` as the existing pair is.
- `evals/README.md` — a pointer section for the new scorer, matching the
  existing measure-script section's shape (pointer, not restatement), and the
  "eight"→"nine" run-count edits.
- `claude/.claude/scripts/transcript_analysis/pricing.py` — **no edit on this
  machine**: the observed ID `claude-haiku-4-5-20251001` is already priced
  (A21). Add a row only if a run observes an unpriced ID, citing the vendor
  pricing page in the commit message.
- `docs/auto-mode.md:173-176` — correct the resolution order to the current
  vendor order (G1) and cite v2.1.251. Lines `:199` ("resolution step 2 …
  competes with step 4") and `:207-208` ("sits at resolution step 1 and
  overrides *every* subagent's model") cite the old step numbers. Rewrite
  both to name the mechanism ("the per-dispatch param", "the env var, which
  now ranks below frontmatter") instead of a step number. This keeps the
  file from contradicting itself after the list changes.
- `CHANGELOG.md` — entry. Under the refuse branch it records the eval's
  arrival, the measured verdict in one line, and only the two Step 6
  residuals that attach to the eval itself: recall drift (A3) and public-repo
  familiarity. The three pin-contingent disclosures (provider-dependent Haiku,
  prompt-size overflow and its project-level remedy, CLI-version drift) apply
  only to a `PASS` landing and are omitted here.

## Out of scope

- **Moving any reviewer, `code-writer`, `plan-architect`, or non-locate
  `general-purpose` dispatch to Haiku.** Excluded by A7's safety bar; the
  survey found no other lane that clears it.
- **`comment-discipline-reviewer` on Haiku.** Its case would rest on low
  stakes (a missed comment-prose finding costs little), not on verifiability —
  a different bar than A7 — and the Haiku-repriced saving is ~$4.68 / 60 days
  against $9.35 actual (half of A22's authorized comment-discipline-reviewer
  figure). Not pursued here; if someone wants it, it needs its own
  gate.
- **Mechanical-execution shapes (run a tool, return its output).** The OTHER
  bucket is the closed lever at `docs/cost-levers-considered.md:68`; nothing in
  this survey reopens it.
- **Review skills executing as ad hoc `general-purpose` dispatches**, including
  one branch's `/plan-review` loop of 23 numbered rounds. The JUDGMENT
  bucket holds ~53% of the lane's token volume and is the largest
  avoidable spend the survey surfaced. It is a routing and gate-design question,
  not a Haiku question — raise it as its own ticket.
- **Fixing the residual Opus dispatches on `Explore` in plan mode.** G2;
  reopening it needs its own evidence.
- **Extending `run_skill_evals.py`'s `--model` choices to Haiku.** Its subject is
  skill-triggering behavior of models this harness actually runs on.
- **Weakening `subagent-mix`'s declared-roots agent-type redaction** so
  figures could be drawn more widely. In reach (a repo-owned rule) and deliberately
  left alone; the `TRANSCRIPT_CONFIG_DIRS_FILE` override is the
  documented path and every figure here uses it.
