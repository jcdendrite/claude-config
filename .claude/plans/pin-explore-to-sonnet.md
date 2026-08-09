# Stop paying Opus rates for Sonnet-pinned work

## Context

**Goal:** stop the largest measured pure-waste in current spend — subagent
dispatches running on Opus despite carrying (or being documented to carry) a
cheaper model pin — with the lightest mechanism that fully closes it, and
correct every place the old resolution story is asserted as fact.

**Why now.** Six prior plans converged on one lever: reset the prefix more often
(`/handoff`, PR #593's absolute-token nudge). PR #593's own *Expected effect*
puts that at ~15% of $/merged-PR, discounted by an `[unverified]` 25% conversion
prior. Spend is `calls x prefix x rate`. Every plan so far attacked *prefix*.
Nobody checked whether the *rate* being charged was the rate intended.

Measured 2026-08-09 over a 14-day window, **default config dir only** (A1).
Whole-account absolute dollars are deliberately expressed as shares — see
*Disclosure* below.

| Fact | Value | Source |
|---|---|---|
| Prefix-priced classes (cache read + both writes) | 88.8% of spend | `transcript-analysis.py cost --since 14d` |
| Cost by thread | main 71.6% / subagent 28.4% | same |
| Tool-result **bytes** by thread | main 32.2% / sidechain 67.8% | ad-hoc corpus walk |
| Main-thread byte sources | Read 41.6%, Bash 35.5%, Agent returns 7.1%, assistant text 7.2%, Grep+Glob 0.5% | ad-hoc corpus walk |
| `staff-*` + `ciso-reviewer` sidechains observed on Opus despite `model: sonnet` | **~23–24%** (256/1,101 by timestamp window; 264/1,115 by mtime) | meta.json -> sidechain join |
| `Explore` sidechains observed on Opus | **40 of ~60**, tracking the parent session's model in every case (58/58) | same |
| Opus share of spend | 16.0% | `cost --since 14d` |
| Dispatches carrying an explicit `model` request | 12.7% (241 of ~1.9k `meta.json`) | key-set census |
| Parent model vs subagent model (`staff-*`/`ciso-reviewer`) | opus-parent -> opus-sub 151; sonnet-parent -> opus-sub 98; opus-parent -> sonnet-sub 44 | parent `tool_use` join |
| **This public repo** | **$4,755.47 / 81 merged PRs = $58.71/PR** | `cost --since 14d --this-repo` + `gh pr list` |

**The `Explore` finding traces to a documented, non-account-specific mechanism,
and this repo already has the tool to override it.** Claude Code v2.1.198's
release notes: *"The built-in Explore agent now inherits the main session's
model (capped at opus) instead of running on haiku."* The motivating cause,
per [anthropics/claude-code#45357](https://github.com/anthropics/claude-code/issues/45357)
(filed before the change, closed as the likely duplicate/root cause): with a
realistic MCP tool surface (~200 tools, 10+ servers, 19 plugins), Haiku's prompt
size limit couldn't fit the tool definitions, so Explore failed outright —
*"Prompt is too long"* — not a reasoning-quality complaint. The reporter's own
recommendation was *"Explore should default to Sonnet (or inherit)"*; Anthropic
chose inherit-capped-at-Opus. Official docs
([code.claude.com/docs/en/sub-agents](https://code.claude.com/docs/en/sub-agents)) state
the override path directly: *"A user or project subagent named `Explore`
overrides the built-in and keeps its own `model` field, so define one with
`model: haiku` to keep exploration on a lower-cost model."* The same mechanism
with `model: sonnet` is this plan's fix — a repo-owned file, not a change to the
shared session default, and not dependent on which model the parent session
happens to run.

The `staff-*`/`ciso-reviewer` finding is a different animal and this mechanism
doesn't touch it: 59% of those Opus runs trace to an Opus-anchored parent
(useful context, not actionable from this repo), but the remaining 41% do not —
frontmatter and explicit `model` requests are measured genuinely unreliable at
the platform level, which this repo cannot fix from within.

Every dollar figure came from throwaway scripts, not reviewed tooling — which is
why the instrument is Step 2, not Step 1, and why no dollar figure is written
into an always-loaded file as a live number.

## Approach

Three steps. Step 1 is the dollar-saving change and lands first: a new
repo-owned agent-override file plus the resolution-story doc corrections. It
needs no change to `claude/.claude/settings.json` — `opusplan` stays this
repo's shared default — and needs no change outside normal commit flow.

### Root problem and givens

**Root problem:** `Explore` has no frontmatter of its own and, absent an
override, silently costs whatever the parent session's model costs (capped at
Opus); separately, `staff-*`/`ciso-reviewer`'s frontmatter pins and explicit
`model` requests are resolved unreliably at the platform level. The repo's own
docs misdescribe both.

| # | Given | Why it is fixed |
|---|---|---|
| G1 | Cache read is 0.1x base input, charged on the full prefix every call | Vendor pricing mechanic. `[verified: transcript-analysis.py:3616-3617 _PRICING_SOURCE_URL / _PRICING_FETCH_DATE]` |
| G2 | **Resolution of `staff-*`/`ciso-reviewer`'s frontmatter pin and of an explicit `model` request is measured nondeterministic, and not fully explained by parent model** — only 59% of their Opus runs trace to an Opus parent; dispatches carrying an explicit `model` still resolved to Opus. | `[verified: meta.json / parent tool_use / sidechain assistant records, this session]` — Anthropic owns the resolver. Platform boundary, not a declined choice. |
| G3 | Sonnet 5 base rates rise 2026-09-01 ($2->$3 in, $10->$15 out) | Vendor-set. Confounds any before/after window spanning the date. |
| G4 | **A user/project subagent file named `Explore` overrides the built-in entirely, including its `model` field** — a documented mechanism, not a workaround. | `[verified: primary source — code.claude.com/docs/en/sub-agents, "Built-in subagents" tab and "Choose a model" section, quoted above]` |
| G5 | **The override forfeits one built-in-only optimization: "Explore and Plan are the only subagents that omit CLAUDE.md and git status. There is no frontmatter field or per-agent setting to change which agents skip them."** Once `Explore` is overridden it is a custom subagent for this purpose and loads full CLAUDE.md + git status on every dispatch. | `[partially verified: same source, "Manage subagent context / What loads at startup"]` — the CLAUDE.md/git-status forfeiture is confirmed; whether any other safety-relevant field (e.g. `permissionMode`) also resets on override is unconfirmed from primary source and has no local precedent (no agent file in this repo sets `permissionMode` today) — implementation-time `agent-review` closes this. See *Expected effect* for why the confirmed cost doesn't erase the model-rate savings. |

### Step 1 — Override `Explore`'s model via a repo-owned agent file

*(anchors: root)* Add `claude/.claude/agents/Explore.md`: `name: Explore`,
`model: sonnet`, an explicit `tools` list (see Critical files — the exact list
must be verified against the built-in's actual granted set before
implementation, not assumed), and a `description` matching the built-in's
documented role so the override doesn't change *when* Claude delegates to it,
only *what runs*.

**Why this is lighter than the settings.json route this plan carried through
four prior revisions.** An earlier draft flipped `claude/.claude/settings.json`'s
shared `model` default from `opusplan` to `sonnet` — a change that (a) collided
with `claude/.claude/hooks/guard-settings-session-keys.sh`, which hard-blocks
any Claude-Code-authored commit touching that key and has no in-session bypass,
requiring the engineer to commit it manually outside the harness; (b) touched
11 sites across 5 files asserting "opusplan is the default"; (c) needed a new
CHANGELOG entry and a `claude-opus` escalation wrapper for every consumer who
still wants Opus during planning; and (d) only fixed `Explore` for sessions that
happened to be Sonnet-anchored, not for one explicitly started with `--model
opus`. The agent-file override needs none of that: it is a plain, normal,
Claude-Code-committable file; `opusplan` stays the default for everyone;
existing consumers see zero behavior change outside `Explore` itself; and the
fix applies **regardless of the parent session's model**, which the settings.json
route never could.

*Heavier primitives rejected (from the superseded draft, kept here so this
ground isn't re-tread):* (a) `claude/.claude/settings.json`'s shared default —
superseded above. (b) `ANTHROPIC_MODEL=sonnet` as a personal env var — sits
above the `model` setting in Claude Code's own precedence and only fixes one
machine. (c) `CLAUDE_CODE_SUBAGENT_MODEL` — a global override
`docs/auto-mode.md` already advises against, forcing every subagent to one
model including ones legitimately wanting Opus. None of these three needed
re-litigating once G4 surfaced a mechanism that fixes `Explore` specifically,
at the file it actually lives in.

**The tools list is not optional.** Frontmatter's `tools` field "inherits every
tool available to subagents if omitted" — the built-in `Explore` is documented
read-only (*"Tools: read-only tools; Write and Edit are denied"*), and an
override that omits `tools` would silently grant the replacement full write
access the built-in never had, a real regression disguised as a config change.
`Explore.md` must specify `tools` explicitly.

**Family B — the resolution-outcome/pin-reliability story, corrected together.**
The `Explore` claim is now a true, durable, repo-controlled fact rather than a
description of external vendor behavior that could drift again on the next
Claude Code release — write it that way:

| Site | What it asserts | Disposition |
|---|---|---|
| `claude/.claude/CLAUDE.md:72-73` | "Enforced via `model: sonnet` frontmatter in each agent file" | **Correct** — becomes "requested via" for `staff-*`/`ciso-reviewer` (G2), stays "enforced" for `Explore` (G4 — a repo-owned override, not a request the platform can drop). |
| `claude/.claude/CLAUDE.md:76` | "Do not pass `model` to the other agents — `Explore` is pinned to Haiku…" | **Correct** — clause deleted. States: `Explore` is pinned to Sonnet via `claude/.claude/agents/Explore.md` (fact, not a request); the frontmatter pin and an explicit `model` request for `staff-*`/`ciso-reviewer`/`general-purpose` are **requests, not guarantees** (G2); pass `model: sonnet` explicitly on those dispatches anyway — it costs nothing and helps at any honor rate above zero (A4). |
| `docs/auto-mode.md:145-150` | "Subagent model is resolved in this order — the first that applies wins" | **Correct** — becomes "the requested order, not a guarantee" for the three parent-resolvable sources, citing G2; note `Explore` sits outside this order entirely once overridden, since a same-named file replaces the built-in before resolution order applies. |
| `docs/auto-mode.md:157` (table: "Model under an auto-mode parent") | `Explore` -> Haiku, "Pinned by Claude Code" | **Correct** — new cell: "Sonnet — `claude/.claude/agents/Explore.md` pin (G4), independent of parent model." |
| `docs/auto-mode.md:158-159` | `staff-*`/`ciso-reviewer`/`code-writer` -> Sonnet via frontmatter | **Correct** — citing G2, unchanged framing otherwise. |
| `docs/auto-mode.md:165-168` | "pass an explicit `model: sonnet` … step 2 overrides step 4" | **Correct** — keep the advice for `staff-*`/`ciso-reviewer`/`general-purpose`, drop "overrides"; it is a request. Not applicable to `Explore`, which no longer reaches step 4 at all. |
| `docs/auto-mode.md:169-172` | "treat this as unconditional: always dispatch `general-purpose` with an explicit `model`" | **Unchanged** — still the right advice under A4. |
| `claude/.claude/skills/subagent-delegation/SKILL.md:108-110`, `:130`, `:149`, `:169-171` | "**Always pass an explicit `model`** on `general-purpose`" (four sites) | **Unchanged as advice; modality corrected** — the imperative survives A4, but "always pass" must not imply it is honored. |
| `docs/design-decisions.md:221` | "`general-purpose` (model: sonnet)" | **Unchanged** — a record of a past decision, read-only under CLAUDE.md Axis 3. |
| `claude/.claude/skills/agent-review/SKILL.md:126` | routes reviewers to CLAUDE.md "Model Routing" | **Correct** — asserts agents *pin* `model: sonnet` uniformly; under this step `Explore`'s pin is real (G4) but `staff-*`/`ciso-reviewer`'s is a request (G2) — the reviewer checklist item needs to distinguish the two, not treat every `model:` frontmatter line as equally authoritative. |

**A sliding measured rate does not belong in an always-loaded instruction
file.** The `staff-*`/`ciso-reviewer` leak rate moved from ~10% to 40–48%
inside one measurement window, and no mechanical staleness gate covers any of
these files. The imperative goes in `claude/.claude/CLAUDE.md`; the dated
distribution goes in `docs/auto-mode.md`. `Explore`'s new line is not dated,
because it is no longer describing external behavior that drifts — it is
describing this repo's own file.

Verification re-runs the grep to prove no site was missed.
`subagent-delegation/SKILL.md` sits at 171 of its 200-line cap (29 lines
headroom); `claude/.claude/CLAUDE.md` at 114 of 200.

### Step 2 — Build the instrument

*(anchors: row A6)* Extend `cmd_subagents` (`:1030`) and `cmd_subagent_mix`
(`:2607`) rather than adding subcommands: `--since` and repeatable `--config-dir`
on both; observed / requested / declared model columns per `agentType` on
`subagent-mix`; tool-result bytes grouped by tool name on `subagents`.

*Lighter primitives rejected:* (a) a new `subagent-model` subcommand — `cmd_subagents`
already reports a model split and byte totals and `cmd_subagent_mix` already counts
dispatches per `agentType`; a third would create a third definition of "a run."
(b) a shell wrapper looping per config dir — each invocation re-parses the full
corpus, which is what made a prior 7-profile loop time out.

**Four method terms the scratchpad scripts left implicit.** Each admits several
readings that produce different numbers; Step 2 fixes them:

| Term | Definition |
|---|---|
| A "run" | One `subagents/*.meta.json` **with a readable sibling `<id>.jsonl`**. A dangling `meta.json` is excluded from the denominator and counted separately — `_index_subagent_dispatches` does not check existence today. |
| Requested model | `meta.get("model")`. Absent key = no explicit request. |
| Observed model | Modal `assistant.message.model` across the sidechain. Two real model IDs report `mixed` as its own bucket, never collapsed. `<synthetic>` collapses to `other` via `_fam` and is **not** a pin violation. |
| Parent model | Model on the assistant record carrying the `tool_use` block whose id equals `meta["toolUseId"]`. |
| Declared pin | Frontmatter `model:` in `config_dir()/agents/<agentType>.md`. After Step 1, `Explore` has one; `general-purpose`, `claude-code-guide` and `Plan` still have **no on-disk definition** — they render `built-in` and are never counted as pin violations. |

**Read the requested model from `meta["model"]`, not by joining back to the parent.**
A key-set census over ~1.9k `meta.json` files finds 241 (12.7%) carry a `model`
key (`sonnet` 208, `opus` 31, `haiku` 2, `fable` 1) — the requested alias, recorded
on the dispatch artifact itself. The parent join is still needed for *parent*
model, which `meta.json` does not record. Also tolerate: `spawnDepth` absent in 78
files, `parentAgentId` present in 52.

**Step 2's post-Step-1 acceptance target changes.** Once `Explore.md` ships,
`subagent-mix`'s declared-vs-observed join should show `Explore` at (or very
near) 0% Opus going forward — a concrete, checkable regression signal distinct
from the `staff-*`/`ciso-reviewer` residual, which stays nonzero by G2.

### Step 3 — The register and the delegation ratio

*(anchors: root)* `docs/cost-levers-considered.md` (new): one register of every
cost lever already investigated and closed across six prior plans — lever,
verdict, measured reason, source plan. Six sessions each kept their own
rejected-alternatives section and the seventh still re-tread the ground, because
those sections are scattered across six files nobody reads before starting.
Include the superseded settings.json-default-change route from this plan's own
earlier revisions as one more entry, with its guard-hook collision as the
reason it was dropped in favor of the agent-override mechanism.

*Lighter primitives rejected:* (a) a numbered section in `docs/design-decisions.md`
— ~21 rows would dominate a file whose sections are single decisions; (b) amending
`.claude/plans/absolute-token-handoff-threshold.md` in place — CLAUDE.md Axis 3
makes merged plans read-only records, and it would leave the register split.

Also add the measured main-vs-sidechain association — the main thread carries
32.2% of tool-result bytes and 71.6% of dollars — to `docs/design-decisions.md`,
**not** to `subagent-delegation/SKILL.md`. A dated, drifting ratio (A5 is
explicitly an association, not a causal price) does not belong in an
auto-triggering skill with no staleness gate — the same principle Step 1 applies
to `CLAUDE.md`. The skill's qualitative argument stays as-is. This reverses PR
#593's closure of the "delegation-discipline pilot," which was closed as
*unmeasurable* on ISO-week time-series noise — a cross-sectional ratio needs no
time series — but the number lives in docs, not in a skill body.

**Step 3 is a process deliverable, not a cost fix, and rides along honestly
labeled as such.** It solves "six plans re-tread closed ground," not "the repo
pays Opus rates" — Step 1 is the cost fix; Step 3 is out-of-scope-adjacent work
sequenced last specifically so it never blocks the dollar-saving step.

### Assumption ledger

| Row | Assumption | Tag |
|---|---|---|
| A1 | **Every figure covers the default config dir only.** The stated scope is all accounts, repos and projects. `cost` takes repeatable `--config-dir`; `subagents`, `subagent-mix`, `review-trace`, `analyze-context.py`, `token-analyzer.py` do not. | `[verified: --help and source]` — the machine-wide picture is **unknown**; Step 2 makes it knowable. Do not quote these as machine-wide. |
| A2 | The Opus-on-pinned-agent finding is real, not an artifact | `[verified: sampled Opus sidechains are all isSidechain=true with non-null parentUuid (no parent leakage); 98.8% single-model; code-writer and skill-fidelity-reviewer measure pure-Sonnet under the same method; all on-disk staff-sdet definitions carry model: sonnet with no project-scope shadow]`. Method terms were implicit — Step 2 fixes them and the headline may move a point or two. |
| A3 | `meta.json` carries `model` on 12.7% of dispatches | `[verified: key-set census this session]` — **supersedes an earlier draft that asserted the key set from a 3-file sample and concluded no model field existed.** |
| A4 | Passing an explicit `model` weakly dominates not passing it, for `staff-*`/`ciso-reviewer`/`general-purpose` | `[derived from G2]` — zero cost, non-negative benefit at any honor rate. Not applicable to `Explore`, whose pin is enforced by G4, not requested. |
| A5 | Main-thread bytes accompany several times the dollars per byte of sidechain bytes | `[derived: (71.6%/32.2%) vs (28.4%/67.8%)]` — an **observed association across an uncontrolled mix, not a causal per-byte price**; sidechain prefixes are shorter for reasons beyond byte placement. Step 3 must state it that way. |
| A6 | Recoverable dollars from the `staff-*`/`ciso-reviewer` residual (unaffected by Step 1) | `[unverified]` — needs the model x thread x agentType dollar cross-tab that does not exist. Bound only: Opus is 16.0% of spend and ~5x Sonnet per token. **No figure may be committed before Step 2 runs.** |
| A7 | Parent model is a strong in-reach determinant of `staff-*`/`ciso-reviewer` model | `[verified: 151/98/44 join, one account]` — 59% of their Opus runs had an Opus parent. Not load-bearing for Step 1 (Explore's fix doesn't depend on parent model at all); relevant only as context for the residual G2 problem, which this plan cannot close. |
| A8 | The 65% blended-reduction goal, tracked as $/merged-PR | `[engineer-verified]` — carried from PR #593 A9. **This plan does not reach it**, and says so in *Expected effect*. |
| A9 | Hook denials are not a cost lever | `[verified: ~0.6% of spend]` — real workflow friction, excluded on cost grounds only. |
| A10 | Pinning `Explore` at a fixed Sonnet ceiling, forfeiting the case where a harder parent task would benefit from Opus-level exploration, is an acceptable trade | `[engineer-verified]` — the engineer's own proposed design ("pin Explore at Sonnet... find safe and appropriate uses for Haiku separately"), confirmed this session after G4's mechanism and G5's CLAUDE.md/git-status cost were both surfaced. |

### Expected effect

**Step 1 is the dollar-saving step, and it fully closes the `Explore` slice of
the problem — not merely the parent-anchored share of it.** G4 makes the fix a
repo-owned fact rather than a session-model correlation: every `Explore`
dispatch runs Sonnet going forward, independent of what model the parent
session happens to be on, including an explicit `--model opus` session. G5's
disclosed cost (CLAUDE.md + git status now load on every `Explore` dispatch,
previously skipped) is real but small relative to the model-rate savings it
trades against: this repo's own prior measurement puts the full resident
instruction footprint at roughly 15,700 tokens against a ~177,761-token typical
per-call prefix (~8.8%), and that one-time per-dispatch addition is dwarfed by
the ~5x rate difference between Opus and Sonnet on every turn `Explore` would
otherwise have spent at the higher rate. The `staff-*`/`ciso-reviewer` residual
(G2's 41% unexplained-by-parent-model share) is addressed only indirectly, by
replacing instructions that steered consumers toward a mechanism measured
unreliable — G2 is a platform boundary this repo cannot close outright. Step 2
saves nothing directly — it is the instrument that sizes the residual and turns
G2 into a reproducible measurement rather than an anecdote; Step 3 is the
record.

**This plan does not reach A8's 65% goal and no config-side change does.** The
term that could — 81 merged PRs in 14 days, with this repo alone a large share of
account spend — is a working-cadence decision and is out of scope. Saying so
plainly is the point: six prior plans each proposed a config lever against that
target and none named the gap.

**What this plan cannot fix.** G2 is the wall for `staff-*`/`ciso-reviewer`. The
repo can reduce exposure to it, stop misdescribing it, measure the residual
(Step 2), and escalate it — it cannot make a pin authoritative for agents whose
model resolution still depends on the platform. `Explore` no longer has this
problem at all, because G4 lets this repo own the pin outright.

## Disclosure

This plan ships in a public repo. Whole-account absolute dollars are excluded:
publishing both the account total and this repo's `$4,755.47` would disclose
private-engagement spend by subtraction. `$4,755.47` and `$58.71/PR` are this
public repo's own cost against a PR count verifiable from GitHub, and are safe.
Every argument here consumes ratios, so dropping the absolutes costs nothing.

## Critical files

**Step 1:**
- `claude/.claude/agents/Explore.md` (new) — `name: Explore`, `model: sonnet`,
  a `description` matching the built-in's documented delegation trigger ("file
  discovery, code search, codebase exploration... without making changes") so
  delegation timing is unchanged, only the model and tool-loading behavior
  differ. **The `tools` list is not a citation of this repo's reviewer-agent
  convention** — every `staff-*` and `ciso-reviewer` file actually carries
  `Write` (for their own `findings_path`), so copying that frontmatter verbatim
  would grant `Explore` the write access this whole step exists to avoid. The
  built-in's documented guarantee is narrower than "read-only tools" implies:
  *"Write and Edit are denied"* — that denies two tool names, not the mutation
  capability `Bash` alone can still exercise (`>`, `sed -i`, `git commit`,
  `curl | sh`). Before implementation: verify the built-in's actual granted
  tool set from primary source; if `Bash` is not in it, omit `Bash` entirely —
  `Read, Grep, Glob` covers the documented role, and `Explore`'s harness-driven,
  attacker-influenceable-input dispatch pattern (auto-invoked on repo/branch
  content, not human-chosen per call) makes a permission-layer backstop
  (`permissions.deny`) an insufficient substitute for denying `Bash` at the
  frontmatter — that layer is void under `acceptEdits`/`bypassPermissions`
  (this repo's own recorded finding, commit `6317c0f`) and is not this repo's
  to guarantee on a consumer's own `settings.json`. If the built-in does carry
  `Bash`, state that explicitly rather than inferring parity from an
  inapplicable convention.
- **`claude/.claude/hooks/tests/test_agent_roster.py` and `_lib.sh:1148,1192-1198`
  — a real, previously-unaccounted-for dependency.** `_lib.sh`'s
  `_LIB_REVIEW_ONLY_AGENTS`/`_LIB_NO_GATE_RELEASE_AGENTS` lists `Explore` (with
  `Plan`) as a harness-builtin exemption from marker-gate release, grounded
  specifically on *no repo-owned frontmatter existing* for it — once
  `Explore.md` ships, that grounding is no longer true, and shipping the file
  without updating this breaks four tests: `test_no_uncategorized_agents`
  (needs `Explore.md` added to `NON_REVIEWER_AGENTS`, not `REVIEWER_AGENTS` —
  the latter is `test_doc_counts.py`'s specialist-persona count and `Explore`
  is not a reviewer), `test_expected_model_map_is_complete` +
  `test_model_pinned_to_expected_value[Explore.md]` (needs
  `NON_REVIEWER_MODELS["Explore.md"] = "sonnet"`), and
  `test_harness_builtin_exemptions_have_no_agent_file` — `Explore` must move
  out of `HARNESS_BUILTIN_NO_GATE_RELEASE_AGENTS`, `test_exemption_set_is_pinned`
  (hard-asserts the set is exactly `{"Explore","Plan"}`) updates accordingly,
  and `_lib.sh:1192-1198`'s comment block is rewritten to state the new
  grounding (`Plan` remains file-less and stays exempt; `Explore` no longer is).
  This is a security-boundary change, not bookkeeping — say so in the PR, not
  just in the diff.
- `claude/.claude/CLAUDE.md` — Model Routing section (Family B sites above).
- `docs/auto-mode.md` — Family B sites (`:145-172`, resolution order and table).
- `claude/.claude/skills/subagent-delegation/SKILL.md`,
  `claude/.claude/skills/agent-review/SKILL.md` — Family B sites.
- This is Claude Code config content — invoke `agent-review` against the
  drafted `Explore.md` per plan-review's own "Domain: Claude Code config"
  routing; have it enumerate the full supported-frontmatter-field set against
  the built-in's documented behavior, not just tools-list completeness (G5's
  CLAUDE.md/git-status forfeiture is confirmed from primary source, but
  whether any other safety-relevant field like `permissionMode` also resets is
  unverified in this plan — no agent file in this repo sets it today, so there
  is no local precedent to check against).

**No changes to** `claude/.claude/settings.json`, `README.md`, `docs/scripts.md`,
`claude/.claude/scripts/claude-auto.sh`, and no new wrapper scripts or CHANGELOG
entry — all superseded by the lighter mechanism; see Step 1's rationale above.

**Hazard:** each registered `Occurrence` in
`claude/.claude/hooks/tests/test_doc_counts.py` enforces exactly one regex match,
and `docs/design-decisions.md` and `claude/.claude/CLAUDE.md` both carry
registered patterns — prose restating a registered sentence shape breaks the
suite.

**Step 2** — `claude/.claude/scripts/transcript-analysis.py`.

*Reuse, do not reimplement:* `_resolve_project_scope` (`:2323`) already accepts
`roots: Sequence[Path] | None = None` and **both target functions already call
it** (`:1041`, `:2610`) — no 19-subcommand refactor is implied. `_resolve_cost_roots`
(`:3772`) is already parameterized by subcommand. `_root_index_for_path` (`:3881`)
supplies root identity for `account-<K>` labels. `_price_turn` (`:3728`), `_fam`
(`:76`), and `cmd_handoff_ratio`'s ISO-week idiom (`:4447`) exist as named.

*`include_subagents` asymmetry — resolve before writing the join.*
`cmd_subagents` (`:1041`) already passes `include_subagents=True`;
`cmd_subagent_mix` (`:2610`) does not. Observed model requires sidechain
`assistant.message.model` records that `cmd_subagent_mix`'s iterator does not
yield today. Flipping the flag is not free: subagent `.jsonl` files carry their
own `tool_use` and `Skill` records, so the existing `spawns`/`skills` counts at
`:2624-2646` would silently double-count nested dispatches if the flag is flipped
naively. Step 2 states which path supplies sidechain models — flag flip with a
nesting guard, or a separate `subagents/*.meta.json` + sibling-file index reused
from `cmd_subagents` — and pins the pre/post spawn count in a test so the change
is caught if it drifts.

*`--since` must filter the table, not the drift canary.*
`_warn_if_subagent_format_drift` (`:1007-1025`) fires when
`total_spawns > 0 and total_sidechain_turns == 0`, and `corpus_sidechain_turns` is
incremented before the branch filter specifically so a narrow branch scope cannot
trigger a false format-drift warning. A record-level `--since` applied inside the
same loop would suppress that counter under a narrow window and produce a
spurious warning. `--since` filters what is reported, never what feeds the canary.

*Row-key contract for `subagent-mix`'s new columns.*
`cmd_subagent_mix` rows are keyed by branch (or
`f"{branch} [{stem[:8]}]"` under `--per-session`, `:2641`), with agentTypes
flattened into one `Top subagent types` cell (`:2661`). Per-`agentType`
observed/requested/declared columns need either a row-key change — which would
break the documented sample in `docs/transcript-analysis.md` and the
`--per-session` key form — or a second table keyed by `agentType`. Step 2 adds a
second table; the existing branch-keyed table is unchanged.

*Do **not** reuse `_build_redact_map`* (`:3281`, `:3348`) for root labelling: it
performs a full-corpus read per root and builds a project-label map neither
subcommand prints. `_root_index_for_path` is the lighter primitive for the same
need.

*Four CLI contracts — all behavior changes, not additive flags:*
1. `main()` (`:5819-5827`) refuses the **top-level** `--config-dir` only for
   `cost` and `context-distribution`; both targets accept it today via the
   `PROJECTS_DIR` reassignment at `:5389`. Adding per-subcommand flags without
   extending that tuple reproduces the silent divergence the guard prevents, and
   invalidates `_resolve_project_scope`'s documented invariant (`:2348-2373`).
   Re-anchor that function's fail-closed zero-match `--this-repo` check on the
   subcommand-level flag, or it becomes unreachable.
2. `_resolve_cost_roots` (`:3792`) makes `--this-repo` and `--config-dir` mutually
   exclusive; both subcommands carry `--this-repo` via `_add_project_scope_args`.
3. Both key rows on `gitBranch` with no root identity in the row key, so two
   accounts each with a `main` branch collapse into one row.
4. Neither prints `_DO_NOT_PUBLISH_BANNER` (only `cost` `:3969` and
   `context-distribution` `:4290` do). Extend it to any multi-root output here.

*Three disclosure controls Step 2 must build — these are new work, not wiring:*
- **Branch-name redaction has no primitive at all.** `_redact_proj_label` covers
  project labels and `_assign_session_redact_label` covers session IDs; nothing
  covers `gitBranch` (`:1096`, `:2657`). `subagents --config-dir <other-account>`
  prints that account's branch slugs raw. Adding a `--no-redact` flag makes the
  existing multi-root guard fire but leaves the default `redact=True` path with
  nothing to apply. Build a per-root branch primitive shaped like
  `_assign_session_redact_label`, or refuse `--config-dir` on any mode printing
  raw branch text.
- `--per-session` (`:2641`) keys rows `f"{branch} [{jsonl.stem[:8]}]"`, joining a
  foreign account's session-ID prefix to its branch name. Refuse it with
  multi-root, or route the stem through the session redact map.
- Tool-name grouping emits `mcp__<server>__<tool>` labels, and an MCP server name
  is a per-account integration identifier. Bucket all `mcp__*` into one row.

*Byte grouping ships un-dollar-weighted.* `_price_turn(model, usage)` prices an
*assistant* turn's usage block; tool-result bytes are counted off *user* records
(`:1068-1079`) carrying no `usage` and no model. There is no per-byte dollar
primitive, and inventing an allocation model is out of scope — A5 stays an
aggregate association. Grouping also needs a per-session `tool_use_id -> name`
index built from assistant `tool_use` blocks, and under `include_subagents=True`
(`:1041`) the merge flattens subagent records into one list, so pairing spans
files.

`claude/.claude/scripts/tests/test_transcript_analysis.py` — all ~10
`TestSubagentMix` cases build args via `type("A", (), {...})()` with hand-listed
attributes; adding two fields breaks every one with `AttributeError` unless a
shared args-builder factory lands first. Budget that refactor.

`docs/transcript-analysis.md` — both subcommand entries; its `subagents` sample at
`:145-149` is already stale (omits the `Bytes` column) and the new top-level
`--config-dir` refusal must be documented, since it breaks existing invocations
for every stow consumer.

**Step 3** — `docs/cost-levers-considered.md` (new), `docs/design-decisions.md`
link row and delegation-ratio paragraph.

## Verification

1. `git grep -n -i -E 'pinned to haiku|Explore.*Haiku'` across `claude/`, `docs/`
   to confirm every Family B site was corrected and none still describe
   `Explore` as Haiku-pinned or resolver-dependent.
2. **The declared pin — durable, unit-testable, and this repo already has the
   suite for it.** `claude/.claude/hooks/tests/test_agent_roster.py`'s
   `TestAgentFrontmatter::test_model_pinned_to_expected_value` is exactly the
   right shape (per-file frontmatter assertion against a declared-expectation
   map): add `Explore.md` to `NON_REVIEWER_MODELS` (per Critical files) so a
   future edit that re-adds `Write`/`Edit` or drops the `model: sonnet` pin
   fails a permanent regression test, not a one-time manual check. Assert
   *no filesystem mutation by any route*, not merely `Write`/`Edit` absence —
   dispatch `Explore` against a scratch directory and confirm nothing is
   created or modified via any granted tool, including `Bash` redirection or
   `git`; a check that only confirms `Write`/`Edit` are absent passes while
   `Bash(sh -c 'echo x > probe')` still succeeds.
3. **The resolved model — a one-time dated measurement, not a regression
   test.** Dispatch a real `Explore` agent from a session anchored to Opus
   (e.g. during a plan-mode Opus phase) and confirm via the sidechain's
   `assistant.message.model` that it ran Sonnet — this is the one case the
   superseded settings.json route could never have produced, so it is the most
   direct proof the override works as G4 describes. This requires the live
   harness and cannot be a unit test; record it with its date, same discipline
   as item 7's manual measurement.
4. **Delegation-trigger drift — real, and only partially checkable.** No test
   can pin *when* Claude delegates to `Explore` versus the built-in, because
   the built-in's description isn't readable from disk to diff against —
   `agent-review` (item 5) is a judgment call here, not a gate. Record the
   built-in's description text and its source date in `Explore.md` itself so a
   paraphrase is traceable. The one available empirical proxy is Step 2's own
   instrument: compare `subagent-mix`'s per-`agentType` `Explore` dispatch
   count, pre- vs post-Step-1 at a fixed `--since` window — confounded by
   workload changes, so it catches only gross drift, not subtle narrowing.
   Note the 1000-char `AGENT_DESCRIPTION_MAX_CHARS` cap applies and frontmatter
   parsing is strict YAML (a bare `: ` inside the description breaks parsing).
5. `agent-review` against `claude/.claude/agents/Explore.md` — frontmatter
   contract, description fidelity to the built-in's delegation trigger, tools
   list completeness, and the full supported-frontmatter-field enumeration
   G5's `[partially verified]` tag calls for.
6. `../../../.venv/bin/pytest claude/.claude/` and
   `../../../.venv/bin/ruff check claude/.claude/` from the worktree (the `.venv`
   lives at the main worktree root only, three levels up), plus
   `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck` — this
   run is what surfaces the four `test_agent_roster.py` failures if the Critical
   files updates there were missed.
7. `skill-management:skill-review` on both edited SKILL.md files;
   `check-skill-length.sh` and `check-claude-md-length.sh`.
8. **Step 2 unit tests over the existing `fake_projects` / `fake_config_dir_factory`
   fixtures — not the live corpus.** Required: repeatable `--config-dir` (two roots
   yield a strictly larger dispatch count than either alone; assert on the label
   strings produced, single root flat and two roots namespaced, not by regexing the
   table); extend the parametrized
   `test_top_level_config_dir_refused_for_subcommands_with_their_own` (`:274`) to
   both subcommands; `--since` in the `Nd` convention via `_parse_since_nd_arg`
   (boundary-inclusive, missing-`timestamp` excluded, malformed exits non-zero
   naming the subcommand); and one case per method term — a 3-dispatch `staff-sdet`
   fixture reporting `2/3 opus`, a mixed sidechain reporting `mixed`, a
   `<synthetic>` landing in `other` and not a violation, a dangling `.jsonl`
   excluded from the denominator, `meta["model"]` present vs absent driving the
   requested column, and an undefined `agentType` rendering `built-in`. Plus one
   assertion per disclosure control, over the same fixtures: two roots with
   colliding branch names produce distinct redacted labels with no raw slug in
   output; `--per-session` under multi-root either exits non-zero or emits no raw
   session stem; an `mcp__<server>__<tool>` fixture collapses to one bucket;
   multi-root output carries `_DO_NOT_PUBLISH_BANNER` on both stdout and stderr
   (matching `:3969-3970`).
9. **One dated manual measurement, explicitly not a test.** After Step 2, run
   `subagent-mix --since 14d --this-repo` and record it with its date. It cannot be
   an assertion: the window slides daily, the corpus is not in the repo and does
   not exist on any other consumer's machine, and the `staff-*`/`ciso-reviewer`
   leak rate is itself moving. Constrain the published form to `--this-repo` or
   counts-only — the multi-root table carries the branch-name exposure above and
   this repo is public. Expect `Explore`'s Opus share to read at or near zero
   post-Step-1; a mismatch triggers investigation of **both** the override and
   the instrument, not an assumption the instrument is wrong.
10. **Performance envelope.** `cmd_subagents` already runs `include_subagents=True`
   over the full corpus; grouping adds a pass and multi-root multiplies it. Record
   single-root and two-root wall-clock in the PR body, and run the multi-root
   acceptance at `--since 2d` first so a timeout does not block acceptance.
11. **Success metric.** $/merged-PR on this repo, currently $58.71. G3's
   2026-09-01 repricing lands inside any post-change window, so a raw before/after
   reads as a regression regardless of effect — compare Sonnet-equivalent dollars
   at fixed model mix, or state the confound rather than reporting a misleading
   delta.

## Rollback

- **Step 1** — **a two-file revert, not a single-file one.** Deleting
  `claude/.claude/agents/Explore.md` alone leaves `Explore` correctly out of
  `NON_REVIEWER_MODELS`/`NON_REVIEWER_AGENTS` (those checks are one-directional
  and stay green) but still missing from
  `HARNESS_BUILTIN_NO_GATE_RELEASE_AGENTS`, failing
  `test_exemptions_are_all_roster_members`/`test_exemption_set_is_pinned` and
  leaving `_lib.sh`'s comment asserting a no-file grounding that the diff just
  contradicted. Revert `test_agent_roster.py` and `_lib.sh:1192-1198`'s comment
  in the same commit as the file deletion. No config-key guard is involved and
  no manual-commit step — an ordinary revert, once both files are included.
  `~/.claude/agents` is a directory symlink into this repo, so the delete lands
  for every stow consumer on `git pull` with no re-stow needed — but an
  already-running session that already resolved the override may keep running
  it until its next reload; the revert is not guaranteed to take effect
  mid-session.
- **Step 2** — behavior-compatible except the new top-level `--config-dir`
  refusal, which breaks existing `--config-dir X subagents` invocations. The
  parametrized test pins that breakage so it is deliberate; revert restores cleanly.
- **Step 3** — revert-safe. Its cited reproducing command must work both before
  and after Step 2, or Step 3 is not independently revertible.

## Out of scope

- **Reducing process volume** — 81 merged PRs in 14 days, this repo a large share
  of account spend. The one term that could reach A8's target, and a
  working-cadence decision rather than a config change. Raised, not planned.
- **Changing `claude/.claude/settings.json`'s shared `model` default.**
  Superseded by Step 1's lighter mechanism — see Step 1's own rationale. Not
  reopened here; `docs/cost-levers-considered.md` (Step 3) records why.
- **Finding "safe and appropriate uses for Haiku"** — the engineer's own
  follow-up framing (A10). Explicitly out of scope for this plan: it is a
  separate design question (which agents, if any, should run Haiku, and under
  what task shape does that not degrade output) with its own evidence
  requirements, distinct from "stop `Explore` from running Opus."
  `docs/case-studies/check-runner.md`'s retired Haiku check-runner agent is the
  one existing data point and a reasonable starting read for that follow-up.
- **Cache TTL selection** — a per-request API-caller field with no `settings.json`,
  hook, or env-var surface. Closed, not deferred.
- **Static-prefix trimming**, including the ~1,950 tokens of skill descriptions
  loading twice because `~/.claude/skills` symlinks to the tracked copy. Real and
  unclaimed, but ~8.8% of a per-call prefix.
- **Hook-denial friction** — ~0.6% of spend (A9); may deserve its own change on
  friction grounds.
- **Multi-root support for `analyze-context.py` and `token-analyzer.py`** — same
  single-root binding, left under-reporting and noted so the omission is visible.
  `analyze-context.py --top` is additionally unusable here: it requires
  `~/.claude/usage-data/session-meta/`, which does not exist.
- **A dollar-per-byte allocation model** — named and declined above; A5 stays an
  association.
- **Re-arming the handoff nudge at escalating bands** — PR #593's named first
  follow-up, untouched.
- **Escalating G2 upstream** — the engineer's call, not this repo's to plan.
