# Action plan — transcript-analysis findings (2026-06-02)

## Context

The monthly transcript analysis (`~/.claude/research/transcript-analysis-2026-06-02.md`)
surfaced four findings across six projects. Fixes land in **claude-config**, since
that is where the shared hooks, skills, and reviewer agents live; the other repos
consume them on `git pull`. The goal of this plan is to act on the findings that
are genuine claude-config *mechanism* fixes, and to record an explicit disposition
for the ones that are not — so nothing is silently dropped and no intentional design
gets reversed without a decision.

After interrogating the data (including an empirical re-scan of the reviewer
transcripts), the findings sort into:

- **Primary, fully grounded:** extend the reviewer file-output canary to all
  reviewers — but *only* if it ships with a plan-review-gate exemption and a
  contract tightening, both of which the data shows are load-bearing, not polish.
- **Secondary, small, separate PR:** close the residual marker.sh chain friction
  left after commit `0784a0e`.
- **Open decision, do not auto-resolve:** the Opus read-then-edit delegation gap
  reverses a deliberate design boundary; it needs an explicit call before any edit.
- **Out of scope here:** the Item 2 behavioral corrections are project-specific and
  not claude-config mechanism fixes.

---

## Workstream A (primary) — Extend the reviewer file-output canary to all reviewers

### Why

`staff-backend-engineer` is the only reviewer with `Write`. When dispatched with
`findings_path:`, it writes findings to `agent-reviews/<file>.md` and returns a
~677 B pointer line instead of 4,500–7,800 B of inline findings — a 6×–11× context
reduction in the **Opus orchestrator's** window, which re-reads that context every
turn. The report recommends extending it to the other 7 reviewers (est. 32–52 KB
saved per `/ready-for-review` cycle).

### What the empirical re-scan changed about the plan

A direct scan of all 1,496 transcripts (33 `findings_path` dispatches, May 6 – Jun 2,
deduped by `tool_use_id`) found compliance is **64%, not the ~92% the report implied**:

| Category | Count | Bytes | Disposition |
|---|---|---|---|
| Compliant (clean pointer) | 21/33 (64%) | 495–795 B (median 653) | Validates the ~677 B claim |
| Verbose-pointer | 7/33 (21%) | 809–1,073 B | Mild; still ~6× under inline. Fixable in prose |
| Fallback (full inline) | 5/33 (15%) | 820–8,014 B | Savings lost. **Caused by the plan-review gate blocking Write** |

Two consequences this plan is built around:

1. **The fallbacks cluster on branches with a staged plan file**
   (`migration-cleanup-proof-ci`, `day-279-schema-migration`, `day-459-s12-mitigation`,
   `main`). They are `require-plan-review.sh` blocking the reviewer's `Write`. Extending
   the canary to 7 reviewers *without* a gate exemption multiplies this fallback 7× —
   and it fires exactly when a plan is in flight, i.e. when context is already under
   pressure. **The gate exemption is therefore not optional; it ships with the
   extension or the extension under-delivers in its worst case.**

2. **The verbose-pointer drift correlates with dispatch prompts that pose specific
   enumerated questions** — the agent answers them in the inline summary slot rather
   than burying answers in a file the orchestrator may not open. It is mild (~150–400 B
   over target) and arguably useful, but it will replicate across 7 more agents. The
   fix is a one-sentence contract change, not a hard pointer-only mandate (crushing it
   to 100 B just defers a file read the orchestrator may need anyway).

### Approach

Edit the **template agent first**, then propagate, then wire the dispatcher, then
exempt the gate. Ordering matters: the dispatcher's own "Canary scope" guard
(`code-review/SKILL.md:250`) warns that passing `findings_path` to an agent lacking
the Write tool **and** the file-output prose section re-arms the heredoc-abort-on-
large-findings bug. So agents get `Write` + the section **before** the dispatcher is
generalized.

1. **Tighten the contract in the template** — `claude/.claude/agents/staff-backend-engineer.md`,
   the `### File-based output` block (~lines 80–107). Add one sentence: *if the
   dispatch prompt poses specific questions, answer them inside the findings file
   (e.g. under an `## Answers` heading), not in the inline return; the inline summary
   stays one sentence.* Keep a soft ~1 KB expectation, not a bare-pointer mandate.

2. **Propagate to the 7 remaining reviewers** —
   `staff-frontend-engineer`, `staff-sdet`, `staff-data-engineer`,
   `staff-analytics-engineer`, `staff-product-engineer`, `staff-platform-engineer`,
   `ciso-reviewer` (all in `claude/.claude/agents/`):
   - add `Write` to the `tools:` line (line 5 in each);
   - copy the updated `### File-based output` + `### Inline output` sections, swapping
     only the H1 agent-name token (`# staff-backend-engineer` → `# <agent-name>`).

3. **Generalize the dispatcher** — `claude/.claude/skills/code-review/SKILL.md:250`.
   Replace the hardcoded `staff-backend-engineer` findings_path construction with a
   per-agent path `agent-reviews/<agent-name>-<epoch>-<slug>.md`, and relax the
   "Canary scope" sentence from "today that is `staff-backend-engineer` alone" to "all
   reviewers, each of which now carries `Write` + the file-output section." Keep the
   idempotent `agent-reviews/` add to `$(git rev-parse --git-dir)/info/exclude`.
   `/ready-for-review` needs no change — it delegates the constellation to `/code-review`.
   **`/plan-review`'s reviewer dispatch stays inline this PR** — it routes to the same
   reviewers (via `plan-review/ROUTING.md`) but does not construct `findings_path`, and
   giving the 7 agents `Write` does not change that (the file-output section only
   activates when `findings_path` is in the prompt). Wiring plan-review's dispatch to
   `findings_path` is a deliberate non-goal here; capture it as a possible follow-up,
   not silent scope.

4. **Exempt `agent-reviews/` in the plan-review gate** —
   `claude/.claude/hooks/require-plan-review.sh`, immediately before the repo-boundary
   check (~line 114). Add `agent-reviews/` to an allowlist so a reviewer's findings
   write is never blocked by an armed plan-review gate. **Match precisely**: anchor on
   the resolved path with an exact prefix (`"$REAL_REPO"/agent-reviews/*`, reusing the
   existing `realpath -m` of target and repo root), never a substring — otherwise a
   sibling like `foo-agent-reviews/` would slip through the gate. Per the "audit
   structural siblings" rule, check the other Write-gating hook
   (`require-worktree-for-file-writes.sh`): in-worktree writes are already allowed
   (line 109), so the constellation-in-worktree case is fine, but evaluate whether
   `agent-reviews/` should also be exempted there for the main-tree-under-enforcement
   case, for consistency. `require-code-review.sh` gates `git commit` (Bash), and
   `agent-reviews/` is gitignored and never staged — so it needs **no** change.

5. **Test enforcement** (per the "add enforcement for new conventions" rule): add a
   case to the `require-plan-review.sh` pytest file asserting a `Write` to
   `agent-reviews/...` is allowed while a plan file is armed. Check for any existing
   test asserting "only staff-backend-engineer has Write" / the canary-scope invariant
   and update it to the new roster-wide expectation.

6. **Docs** — update `README.md` reviewer-roster note (~lines 11, 193–204) to state
   the file-output canary now applies to all reviewers; document the mechanism in its
   canonical home `docs/design-decisions.md` (§3 roster, §9 roster-ops). Today the only
   authoritative description is the `code-review/SKILL.md:250` sentence.

### Lighter alternatives considered

- **Keep the canary backend-only, just fix compliance.** Rejected: leaves the
  32–52 KB/cycle savings on the table for the other 7 reviewers, which is the whole
  finding.
- **Give agents `Write` but skip the gate exemption.** Rejected: the data shows 15%
  of dispatches already fall back because of the gate; this is the cheaper-looking
  option that silently fails in the exact high-pressure sessions the canary is meant
  to help.
- **Hard pointer-only (~100 B) contract.** Rejected: the verbose-pointer form is mild
  and front-loads the verdict the orchestrator asked for; a strict pointer just defers
  a file read. A soft ~1 KB cap + "answers go in the file" is the better equilibrium.

### Critical files

- `claude/.claude/agents/staff-backend-engineer.md` (template, lines ~80–124) — contract tighten
- `claude/.claude/agents/{staff-frontend-engineer,staff-sdet,staff-data-engineer,staff-analytics-engineer,staff-product-engineer,staff-platform-engineer,ciso-reviewer}.md` — add `Write` + sections
- `claude/.claude/skills/code-review/SKILL.md` (~line 250) — per-agent findings_path + scope sentence
- `claude/.claude/hooks/require-plan-review.sh` (~line 114) — `agent-reviews/` exemption
- `claude/.claude/hooks/require-plan-review` pytest file — new allow-case
- `README.md`, `docs/design-decisions.md` — roster + mechanism docs
- **Reuse, do not reinvent:** copy the existing `### File-based output` block verbatim
  (only the H1 token changes); reuse the existing `.gitignore` + `.git/info/exclude`
  handling for `agent-reviews/` (already in place).

### Review-pipeline obligations (this repo)

- Each agent edit → `/agent-review` (dispatcher-invoked).
- `code-review/SKILL.md` edit → `/skill-review` (**hook-enforced** by `require-skill-review.sh`).
- Hook edits → `claude-hook-review` skill; the `agent-reviews/` allowlist also warrants
  a `review-permissions` pass since it widens a gate's pass set.
- `/code-review` over the whole diff before presenting.

---

## Workstream B (secondary, separate PR) — Residual marker.sh chain friction

### Why

Commit `0784a0e` already blessed the dominant case — `&&`-joined write↔deactivate for
plan-review/ready-for-review. The residual the report names is the `2>/dev/null`-appended
variant (and `;` separators), which still hit the deny, plus a deny message that gives
no hint a chain is permitted.

### Constraint discovered during plan-review

The "collapse each skill's two adjacent marker calls into one blessed `write && deactivate`
shape" idea is **unworkable as a simple layout edit.** Those calls sit inside
`HOOK_TEST_FIXTURE` blocks (`ready-for-review/SKILL.md:168–178`; the
activate/deactivate/record-completion blocks in `plan-review/SKILL.md`) that the
hook-alignment test suite reads as *exact fenced blocks* to verify they match the
hooks' marker layout. They are also Axis-3 preserved content (stable anchor fixtures the
test harness re-reads). Collapsing them would break those tests and edit preserved
record content — so the cheap-looking layout fix carries real coupling cost.

### Approach (recommended: defer; do not widen the hook)

- **Recommended — defer / monitor.** Commit `0784a0e` already blessed the dominant
  `&&` case. The residual (`2>/dev/null`, `;`) is small, and both remaining fix paths
  have real cost (below). Re-measure the marker.sh denial rate in the next monthly
  analysis; act only if the residual is still material post-`0784a0e`.
- **Decline — widening `enforce-marker-script-shape.sh` to accept a trailing
  `2>/dev/null`.** Redirect tolerance is exactly what the gate's allowlist posture
  exists to forbid; this trades a one-turn recovery cost for a real surface widening.
- **If pursued anyway** (only on explicit user direction): the layout-collapse path
  must update the corresponding hook-alignment tests in the same PR and treat the
  `HOOK_TEST_FIXTURE` blocks as explicitly in-scope (Axis-3 exception requires the
  ticket to name them); the hook-widening path must go through `claude-hook-review`
  + `review-permissions`. Either way, a **separate PR** from Workstream A.
- **Leave the deny message as-is** — it was *deliberately* reverted to not list allowed
  chains; re-adding a hint reverses that decision.

---

## Open decision (do not auto-resolve) — Item 3, Opus read-then-edit delegation

The report wants the ~1,532 inline read-then-edit turns routed to `code-writer`. But
the current guidance keeps read-then-edit **inline by design**:
`subagent-delegation/SKILL.md:48-50` and `:165-168` classify read-to-edit as
"read-and-reason = not delegable," and `CLAUDE.md` states the code-writer rule
"does not change when the parent delegates versus writes inline." Acting on this
**reverses a deliberate boundary**, and the report's own verdict was "mixed."

**Recommendation: defer / decline the foundation flip.** The reviewer-constellation and
check-runner delegation already work well; the read-then-edit pattern is judgment-dense.
At most, add narrow criteria for when an inline read-then-edit clearly warrants
`code-writer` (multi-file edits, or edits that require exploratory reads first) — without
softening the default. **No edit will be made here without an explicit user decision**
among: (a) keep inline + refine edge, (b) flip to delegate-by-default, (c) leave entirely.

---

## Out of scope — Item 2 behavioral corrections

The Item 2 corrections (env/secret isolation on Stripe keys, scope creep, false
completions, missed protocol steps) are largely project-specific behavioral signals, not
claude-config mechanism fixes:

- **False completions** ("claimed it wrote/ran something it hadn't") — already addressed
  by `ba41d91`, which added content-claim verification to `/ready-for-review`.
- **Env/secret isolation** — the `config-environments` skill already covers this domain.
- The rest are per-project behavioral and have no shared-mechanism fix in this repo.

Recorded here for completeness; no action.

---

## Verification

- **Hook tests:** `.venv/bin/pytest claude/.claude/` (from a worktree:
  `../../../.venv/bin/pytest claude/.claude/`) — green, incl. the new `require-plan-review`
  allow-case.
- **Lint:** `.venv/bin/ruff check claude/.claude/`.
- **Functional smoke (manual — no CI eval harness in this repo):** on a branch with a
  staged `.claude/plans/` file, run `/code-review` so the constellation spawns; confirm
  each reviewer writes to `agent-reviews/` and returns a pointer line (no full-inline
  fallback), and that the plan-review gate does not block the writes.
- **Empirical re-check:** after some real dispatches, re-run the compliance scan
  (the read-only `/tmp` script approach used during planning — iterate over
  `~/.claude/projects/*/*.jsonl`, match `staff-*`/`ciso-reviewer` `findings_path`
  dispatches to their `tool_result` byte sizes) and confirm the fallback rate drops and
  the verbose-pointer rate does not balloon across the newly-extended agents.

## Out-of-scope guardrails for implementation

- Workstreams A and B are **separate PRs**.
- No edit to delegation guidance (`subagent-delegation/SKILL.md`, `CLAUDE.md`) until the
  Item 3 decision is made.
- Reviewer agent bodies are otherwise read-only — touch only the `tools:` line and the
  output sections; do not refactor surrounding review-criteria prose.
