# Pin `effort:` frontmatter per subagent

## Context

Pin each of this repo's 12 subagent definitions to an explicit `effort:`
frontmatter value, chosen for that agent's actual task shape, instead of
silently inheriting whatever effort level the invoking session happens to
run at. This surfaced from a session-effort investigation: every session in
this account's entire transcript history runs at `high` (the untouched
Claude Code default), and Claude Code's subagent frontmatter turns out to
support an `effort:` key — confirmed against `code.claude.com/docs/en/sub-agents`
— that pins effort per agent independently of the session, the same lever
this repo already uses for `model:`. None of the 12 agent files use it yet.
The outcome is a documented, tested effort policy that mirrors how the repo
already governs `model:` routing, so effort choice is deliberate per agent
rather than an accident of whatever session dispatched it.

## Approach

### Assumption ledger

```
Root: subagents in this repo inherit whatever effort level the invoking
session happens to run at, even though Claude Code's subagent frontmatter
can pin effort per agent independently of the session — and this repo
already pins `model:` per agent for the analogous "task fit over
inheritance" reason.
Givens: the `effort:` frontmatter mechanism itself (key name, precedence
order, value semantics) is fixed Claude Code platform behavior — beyond
reach: defined by Anthropic's harness, not this repo.

Row 1 [mechanism]: per-agent `effort:` frontmatter — anchors: root — gives
each agent a task-fit effort floor independent of the invoking session.
Two lighter primitives considered and rejected: (a) a global
`effortLevel` in `claude/.claude/settings.json` — applies uniformly to the
whole session, so it can't differentiate `Explore`'s fast lookups from
`ciso-reviewer`'s exhaustive sweep, which is the exact problem this plan
exists to solve; also already blocked from being committed there by
`guard-settings-session-keys.sh` (session-scoped, not a repo policy field).
(b) `Workflow` tool's per-call `agent(prompt, {effort})` — only fires for
agents dispatched through `Workflow` orchestration, not the ordinary
`Agent` tool this repo's skills use for every dispatch (`/code-review`,
`/plan-review`, `code-writer`'s self-review, etc.) — frontmatter is the one
mechanism that reaches every dispatch path.
Row 2 [mechanism]: `claude/.claude/CLAUDE.md` "Model Routing" section gains
a parallel effort-tier policy bullet — anchors: root — single-source-of-truth
requires one canonical home for "why does agent X run at effort Y";
Model Routing is already that home for the analogous model-choice question.
Row 3 [mechanism]: `agent-review/SKILL.md` gets a new checklist item
cross-referencing CLAUDE.md's effort policy — anchors: row2 — mirrors
existing checklist item 7, which does the same thing for `model:` field
discipline, so effort gets identical enforcement shape rather than a
bespoke one.
Row 4 [mechanism]: new pytest tests (`test_effort_pinned_to_expected_value`,
`test_expected_effort_map_is_complete`) mirroring the existing
`test_model_pinned_to_expected_value` / `test_expected_model_map_is_complete`
pair — anchors: row1 — this repo already guards model-pin drift with a
parametrized test over every agent file; effort drift gets the identical
mechanical guard rather than relying on the advisory (non-hook-enforced)
agent-review checklist alone.

Row 5 [assumption]: none of the 12 agent files under `claude/.claude/agents/`
currently set `effort:` frontmatter
[verified: read all 12 files' frontmatter this session] — anchors: root
Row 6 [assumption]: `agent-review/SKILL.md` already documents `effort` as a
valid optional frontmatter key
[verified: claude/.claude/skills/agent-review/SKILL.md:39] — anchors: row1
Row 7 [assumption]: `effort:` frontmatter overrides session effort, but is
itself overridden by the `CLAUDE_CODE_EFFORT_LEVEL` env var when set
[verified: code.claude.com/docs/en/sub-agents "Set the effort level" section,
fetched this session] — anchors: row1
Row 8 [assumption]: `test_agent_roster.py` has no closed allowlist of
permitted frontmatter keys, so adding `effort:` requires no change to the
existing frontmatter-parsing tests
[verified: claude/.claude/hooks/tests/test_agent_roster.py lines 268-311]
— anchors: row4
Row 9 [assumption]: `comment-discipline-reviewer` and `skill-fidelity-reviewer`
are explicitly framed in this repo's own design docs as closed-form/cheap
reviewers, structurally distinct from the 8 stack-specialist personas
[verified: docs/design-decisions.md §9, lines 77 and 79] — anchors: row1
Row 10 [assumption]: the 8 stack-specialist personas plus `code-writer` are
framed everywhere in this repo's docs as correctness/thoroughness-prioritized,
single-pass agents with no cross-checking pass to catch a shallow miss
[verified: docs/design-decisions.md §3 ("reasoning contamination... is
genuinely broken" — no second pass reconciles a shallow first pass) and §11
(code-writer's self-review exists because a missed defect costs a full
parent round-trip)] — anchors: row1
Row 11 [assumption]: Claude Code's own general effort guidance (not
model-specific) flags `max` as prone to diminishing returns and directs
"test before adopting broadly" — softer than a ban, so the plan defaults
every reviewer to `xhigh` as a deliberate, untested-beyond starting point
rather than prohibiting `max` outright
[verified: code.claude.com/docs/en/model-config "Choose an effort level"
table, fetched this session] — anchors: row1 — corrected during this
plan's Opus review round: the first draft cited
platform.claude.com/docs/en/build-with-claude/effort's "structured-output...
overthinking" caveat, which is scoped to the Opus 4.7 guidance subsection
only and does not appear in that page's Sonnet 5 section — every agent in
this roster is pinned `model: sonnet`, so the original citation didn't
apply to what it was justifying. (The `xhigh` tier's own citation —
"repeated tool calling and detailed search," "long-running... token
budgets in the millions" — was re-checked against the same pass and does
hold: both phrases are in the doc's general effort-level guidance, which
explicitly lists Sonnet 5 among the supported models.)
Row 12 [assumption]: all 12 agents are pinned `model: sonnet`, and Sonnet 5
supports every tier this plan assigns (`low`, `medium`, `xhigh`) — no
agent/effort mismatch that would trigger the harness's silent
fallback-to-highest-supported-level behavior
[verified: code.claude.com/docs/en/model-config "Adjust effort level" table,
fetched this session; all 12 agents' `model:` values read this session]
— anchors: row1
Row 13 [assumption]: `effort:` frontmatter is a two-way override, not a
floor — it "overrides the session effort level" in both directions, so a
session run at `max` gets its `ciso-reviewer` dispatch clamped down to
`xhigh`, the same mechanism that raises a `low`-effort session's dispatch
up to `xhigh`
[verified: code.claude.com/docs/en/sub-agents frontmatter table, "Overrides
the session effort level," fetched this session] — anchors: row1 —
corrects the first draft's "pinning gives these agents a floor" framing,
which had the mechanism half-backwards.
Row 14 [assumption]: the clamp-down direction in Row 13 (a `max`-effort
session's `ciso-reviewer` dispatch capped to `xhigh`) is an accepted
tradeoff, not a defect to design around [engineer-verified] — anchors: row13
```

### Tier assignment

Three tiers, no agent left at the implicit `high` default and none at `max`
(Row 11):

| Tier | Agents | Why |
|---|---|---|
| `low` | `Explore` | Anthropic's own effort docs name "subagents" doing fast lookups as the canonical `low` use case. `Explore`'s description and body are explicitly built around speed ("fast, read-only... locate symbols, map an unfamiliar area") with no exhaustiveness requirement — the one agent in the roster where that's the stated design goal. |
| `medium` | `comment-discipline-reviewer`, `skill-fidelity-reviewer` | Both are framed by this repo's own design docs as closed-form, cheap-by-design reviewers (Row 9) — `skill-fidelity-reviewer` decides a bounded classification over a fixed input list; `comment-discipline-reviewer` sweeps a diff against 4 named rules, no shell needed. Narrower and more bounded than the stack-specialist reviewers, but still judgment-based (not a pure lookup), so `low` would risk exactly the kind of shortcut ("fewer tool calls, proceed directly to action") the effort docs warn produces at low effort — wrong for a reviewer whose entire job is exhaustive enumeration. |
| `xhigh` | `ciso-reviewer`, `code-writer`, `staff-analytics-engineer`, `staff-backend-engineer`, `staff-data-engineer`, `staff-frontend-engineer`, `staff-platform-engineer`, `staff-product-engineer`, `staff-sdet` | All 8 stack-specialist reviewers run a single, intentionally exhaustive pass with no cross-checking pass to catch a shallow miss (Row 10) — Anthropic's own guidance names `xhigh` for "advanced coding and complex agentic work requiring extended exploration, such as repeated tool calling and detailed search," which is exactly how each of these agents' own body describes its job (full-file reads, multi-hop consumer tracing, exhaustive angle sweeps). `code-writer` gets the same tier: its self-review loop exists specifically because a missed defect costs a parent round-trip (Row 10), the same asymmetry that justifies thoroughness for the reviewers it checks itself against. |

Explicitly pinning `high`-shaped agents to `xhigh` rather than leaving them
unset makes this deliberate policy — matching the existing `model:` pin
discipline — instead of an accident of whatever effort the *parent
session* happens to be at. `effort:` frontmatter is a two-way override,
not a floor (Row 13): a session run at `low` gets these agents raised to
`xhigh`, and — the tradeoff this plan deliberately accepts (Row 14) — a
session someone runs at `max` for a hairy security review gets
`ciso-reviewer` clamped back down to `xhigh` too. This account has never
actually run a session below `high` (0 of 528 sessions checked earlier
this conversation), so this isn't fixing an observed incident; it's
setting the floor *and* ceiling deliberately rather than leaving either to
whatever the invoking session happens to be.

## Critical files

**Agent frontmatter** (add one `effort:` line to each; no existing key
reordered) — reuse: none needed, this is a pure frontmatter addition:

- `claude/.claude/agents/Explore.md` — `effort: low`
- `claude/.claude/agents/comment-discipline-reviewer.md` — `effort: medium`
- `claude/.claude/agents/skill-fidelity-reviewer.md` — `effort: medium`
- `claude/.claude/agents/ciso-reviewer.md` — `effort: xhigh`
- `claude/.claude/agents/code-writer.md` — `effort: xhigh`
- `claude/.claude/agents/staff-analytics-engineer.md` — `effort: xhigh`
- `claude/.claude/agents/staff-backend-engineer.md` — `effort: xhigh`
- `claude/.claude/agents/staff-data-engineer.md` — `effort: xhigh`
- `claude/.claude/agents/staff-frontend-engineer.md` — `effort: xhigh`
- `claude/.claude/agents/staff-platform-engineer.md` — `effort: xhigh`
- `claude/.claude/agents/staff-product-engineer.md` — `effort: xhigh`
- `claude/.claude/agents/staff-sdet.md` — `effort: xhigh`

**Policy documentation:**

- `claude/.claude/CLAUDE.md` — retitle the "Model Routing" section header to
  "Model & Effort Routing" (it now governs two distinct per-agent dials),
  and add a new bullet after the `general-purpose` bullet:

  > - **Effort:** pin `effort:` frontmatter per agent to the task's shape,
  > not the invoking session's — the same task-fit-over-inheritance
  > reasoning as `model:` above. `effort:` is a two-way override, not a
  > floor: it also clamps a higher-effort session down to the pin, not
  > only raises a lower one — e.g. a `max`-effort session's `ciso-reviewer`
  > dispatch still runs at `xhigh`, accepted deliberately here. `low`:
  > fast, narrow, high-frequency lookups with no exhaustiveness
  > requirement (e.g. `Explore`). `medium`: closed-form or bounded-input
  > reviewers documented as cheap by design (see
  > `docs/design-decisions.md` §9). `xhigh`: single-pass reviewers and
  > self-review loops where thoroughness is explicit and there is no
  > second pass to catch a shallow miss (e.g. `ciso-reviewer`,
  > `code-writer`) — not `max`; Claude Code's own guidance flags `max` as
  > prone to diminishing returns and untested here, so `xhigh` is the
  > deliberate starting point. Current per-agent assignments live in
  > `EXPECTED_EFFORT` (`claude/.claude/hooks/tests/test_agent_roster.py`)
  > — that test is the source of truth, not this bullet.

  (Kept to tier criteria plus one illustrative example per tier, not a full
  roster enumeration — see the "Prior findings applied" note below on why.)

- `claude/.claude/skills/agent-review/SKILL.md` — new checklist **entry
  17** (the file already has an entry 16, "Platform-genericness" — this
  plan's addition goes after it, not after entry 15), mirroring entry 7's
  "defer to CLAUDE.md, not this line" shape:

  > 17. **`effort` field discipline** — check against `~/.claude/CLAUDE.md`
  > "Model & Effort Routing" for the current per-agent effort-tier policy,
  > not this line — that policy can change independently of this
  > checklist. `Explore`'s `low` pin is the canonical fast-lookup case; an
  > agent with no `effort:` pin at all silently inherits whatever effort
  > the invoking session runs at, so every agent in the roster should
  > carry an explicit pin unless a documented reason exists to leave it
  > unset.

  No change needed to `agent-review/SKILL.md:39` (already documents the
  field) or `REFERENCES.md:24` (already cites the source).

**Prior findings applied (from this plan's own `/plan-review` round):** the
first draft of the CLAUDE.md bullet enumerated all 12 agents by name,
duplicating in advisory prose what `EXPECTED_EFFORT` enforces structurally
— a real duplication finding (`agent-review/SKILL.md` §6's three-condition
test: fails condition 1, since a structurally-enforced fact adds no
adherence from prose restatement). Fixed by keeping only the tier criteria
and pointing to `EXPECTED_EFFORT` as the single source of truth. The
checklist entry's numbering (16 → 17) was also corrected against the live
file rather than assumed from the earlier exploration pass.

**Prior findings applied (from an Opus review round, requested to cover the
judgment-heavy tier calls this plan's own Sonnet-run `/plan-review` pass
wasn't routed for):** two real corrections. (1) The plan's "pinning gives
these agents a floor" framing had the `effort:` mechanism half-backwards —
it's a two-way override (Row 13), and the engineer explicitly accepted the
resulting clamp-down tradeoff (Row 14) rather than having it designed
around. (2) The "never `max`" citation was scoped to Opus 4.7-only
guidance and didn't actually apply to this Sonnet-only roster (Row 11) —
replaced with the correctly-scoped Claude Code general guidance, which
recommends testing before adopting `max` rather than banning it outright;
the practical outcome (no agent set to `max` in this plan) is unchanged,
only the justification. One Opus claim didn't survive its own review's
scrutiny and was rejected: that the `xhigh` tier's citations were
similarly Opus-4.7-scoped — re-checked and confirmed general, with Sonnet
5 explicitly listed as a supported model for both cited phrases.

**Drift guard test** — `claude/.claude/hooks/tests/test_agent_roster.py`:
reuse the existing `parse_frontmatter` import. Add, mirroring
`NON_REVIEWER_MODELS` / `test_model_pinned_to_expected_value` /
`test_expected_model_map_is_complete` (lines 68-76, 324-352):

```python
# Expected effort tier per agent. Mirrors NON_REVIEWER_MODELS's role for
# model: values — see CLAUDE.md "Model Routing" (Effort) for the tier
# rationale. Not derived from CANARY_AGENTS: the tier split cuts across
# that grouping (comment-discipline-reviewer and skill-fidelity-reviewer
# are CANARY_AGENTS but get "medium", not the "xhigh" the other six get).
EXPECTED_EFFORT = {
    "Explore.md": "low",
    "comment-discipline-reviewer.md": "medium",
    "skill-fidelity-reviewer.md": "medium",
    "ciso-reviewer.md": "xhigh",
    "code-writer.md": "xhigh",
    "staff-analytics-engineer.md": "xhigh",
    "staff-backend-engineer.md": "xhigh",
    "staff-data-engineer.md": "xhigh",
    "staff-frontend-engineer.md": "xhigh",
    "staff-platform-engineer.md": "xhigh",
    "staff-product-engineer.md": "xhigh",
    "staff-sdet.md": "xhigh",
}
```

```python
# Effort levels Claude Code recognizes.
# https://code.claude.com/docs/en/model-config#adjust-effort-level
VALID_EFFORT_LEVELS = {"low", "medium", "high", "xhigh", "max"}
```

```python
    def test_expected_effort_values_are_valid(self):
        """Every value in EXPECTED_EFFORT must be a recognized effort level.

        Guards against a typo (e.g. "xigh") landing in both EXPECTED_EFFORT
        and an agent's frontmatter, which would otherwise pass
        test_effort_pinned_to_expected_value silently since both sides agree
        with each other but not with reality.
        """
        invalid = {t for t in EXPECTED_EFFORT.values() if t not in VALID_EFFORT_LEVELS}
        assert not invalid, (
            f"EXPECTED_EFFORT contains invalid effort level(s): {sorted(invalid)}. "
            f"Valid levels: {sorted(VALID_EFFORT_LEVELS)}."
        )

    @pytest.mark.parametrize("agent_path", _AGENT_FILES, ids=lambda p: p.name)
    def test_effort_pinned_to_expected_value(self, agent_path):
        """Each agent must declare the exact effort tier its task shape requires.

        See EXPECTED_EFFORT above and CLAUDE.md "Model Routing" (Effort) for
        the tier rationale.
        """
        fm = parse_frontmatter(agent_path)
        actual_effort = fm.get("effort")
        expected_effort = EXPECTED_EFFORT.get(agent_path.name)
        assert actual_effort == expected_effort, (
            f"{agent_path.name}: effort is '{actual_effort}', expected '{expected_effort}'. "
            f"Update the agent's frontmatter or, if the policy has changed, update "
            f"EXPECTED_EFFORT in this file."
        )

    def test_expected_effort_map_is_complete(self):
        """EXPECTED_EFFORT must cover every agent file.

        Adding an agent without updating EXPECTED_EFFORT will fail here.
        This mirrors test_expected_model_map_is_complete.
        """
        all_agent_names = {p.name for p in AGENTS_DIR.glob("*.md")}
        uncategorized = all_agent_names - set(EXPECTED_EFFORT)
        assert not uncategorized, (
            f"Agents missing from EXPECTED_EFFORT: {sorted(uncategorized)}. "
            f"Add each with its expected effort tier."
        )
```

## Verification

1. `../../../.venv/bin/pytest claude/.claude/hooks/tests/test_agent_roster.py -q`
   from the worktree — new tests pass, no existing test regresses.
2. `../../../.venv/bin/ruff check claude/.claude/` — lints the modified test file.
3. `/agent-review` on the diff to the 12 agent files (required by
   `.claude/rules/skill-and-agent-self-review.md`).
4. `/skill-review` on the diff to `agent-review/SKILL.md` — it is itself a
   `SKILL.md`, so the same self-review rule applies and this is additionally
   hook-enforced (`require-skill-review.sh`) before commit.
5. The standard `/code-review` pipeline (Step 6/7 of this skill) dispatches
   `ai-instruction-and-memory-files` automatically for the `CLAUDE.md` diff —
   no separate manual step needed.
6. Optional manual spot-check post-merge: dispatch `Explore` once and one
   `xhigh`-tier reviewer once via the `Agent` tool and confirm both load
   without a frontmatter parse error (the harness would surface a malformed
   `effort:` value as a dispatch-time error, which pytest's YAML-parse test
   also already covers).

## Out of scope

- `plugins/*/agents/*.md` (marketplace-plugin agents, e.g.
  `dev-toolkit/agents/implementor.md`) — different governance
  (`plugin-semver`), not this repo's core agent roster.
- Built-in agent types with no file in this repo (`claude`, `claude-code-guide`,
  `statusline-setup`, `Plan`, `general-purpose`) — nothing to pin frontmatter
  on. `general-purpose` already has its own model-routing rule in CLAUDE.md;
  an analogous per-call effort convention for it (via `Workflow`'s
  `opts.effort`) is a separate, larger initiative.
- `SKILL.md`-level `effort:` frontmatter — skills support the same field
  (per the sub-agents doc's "Skill and subagent frontmatter" note), but a
  skill-wide effort policy covers a much larger surface (every skill in the
  repo) and wasn't part of this request.
- Changing the session-level default itself, or the `CLAUDE_EFFORT` /
  `claude-auto` launcher env var discovered earlier this session — that's
  personal-machine tooling (`workstation-setup`), outside this repo.
- Cross-checking each agent's pinned `model:` against a per-model valid-
  effort-levels table, so a future `model:` change can't silently desync
  from an incompatible `effort:` tier (flagged during this plan's own
  `/plan-review` round). Real gap, but one the existing `model:`-pin test
  already shares — nothing in this repo cross-validates `model:`/`effort:`
  compatibility today, and encoding Anthropic's per-model effort-support
  table into this suite is its own maintenance surface (that table can
  change upstream). Worth a small follow-up covering both fields, not
  bundled into this plan.
