# Improve claude-config from Artifact B engagement lessons

## Context

**Goal:** close the specific gaps identified in `~/Downloads/artifact-b-lessons.md` — a de-identified error-mode report from a multi-session AI-assisted engagement — so claude-config-enhanced agents don't repeat them.

The report identifies 8 error modes bucketed by detection layer: cross-session process failures (B1), bot findings reviewer agents missed pre-commit (B2), and human-unique findings (B3). Exploration confirmed each maps to a concrete claude-config surface, with two important corrections to the report's candidate fixes:

- **B1-L1's candidate fix is wrong.** It recommends `git -C <path>` as the substitute for `cd X && git Y` chains — but `require-worktree-for-git-writes.sh` explicitly denies `git -C` as its own failure mode (the hook reads Claude Code's session-persisted cwd, set only by standalone `cd` calls). The durable rule must teach standalone-`cd`-first.
- **B1-L2's check already exists.** `ready-for-review` Step 4 ("Sync PR description") is a substantive description-accuracy check. The gap is cadence, not existence — so the fix is extraction into a cheap standalone skill, not writing a new check.

User decisions (confirmed): extract a standalone skill registered `skillOverrides: name-only`; file the Section 2 GitHub issue as part of implementation; handle Jest-specific lessons as generic public-base principles plus a private-project layer snippet delivered separately.

## Approach

One PR in a linked worktree, edits grouped by review gate. Per-lesson mapping:

| Lesson | Fix | Surface |
|---|---|---|
| B1-L1 recurring `cd X && git Y` | Durable CLAUDE.md rule (standalone-cd-first; `git -C` also denied) | `claude/.claude/CLAUDE.md` §Agent Briefing |
| B1-L2 + B3-L4 PR-description drift | Extract Step 4 → new `sync-pr-description` skill; dispatch from ready-for-review + handoff checklist; two-tier push gate | new skill + 2 skills + gate hook rework + marker plumbing + settings + docs |
| B2-L1 async contract across components | New review angle in frontend reviewer (code-writer inherits via its read-the-reviewer-file mechanism) | `staff-frontend-engineer.md` |
| B2-L2 suppression-construct itself flagged | New code-review sub-item 9h (construct-substitution suppression) | `code-review/SKILL.md` |
| B3-L3 suppression rationale not systematically checked | Make 9d mechanical: explicit grep of diff for suppression tokens | `code-review/SKILL.md` |
| B3-L1/L2 mock accessor + spy import idioms | Generic principles in test-conventions §8; Jest spellings → private-project layer snippet (appendix) | `test-conventions/SKILL.md` |
| B3-L5 handoff claims treated as ground truth | Reader-side preamble line + writer-side evidence checklist item | `handoff/SKILL.md` |
| B3-L6 config change without rationale | New code-review base item 14a under Scope discipline | `code-review/SKILL.md` |
| Section 2 methodology template | File GitHub issue verbatim | `gh issue create` |

**Cadence closure (B1-L2/B3-L4):** extraction fixes the *cost* half of the lesson (the check no longer requires the full test-suite gate); the *cadence* half — "no independent pre-push trigger" — is closed by reworking the existing push gate into a **two-tier gate** (section 9, user-approved during review): iteration pushes to a draft PR are satisfied by a cheap HEAD-fresh `sync-pr-description` marker; the draft→ready transition and pushes to a ready PR still demand the full `/ready-for-review` gate. Firing points for the check: `/ready-for-review` Step 4, the two-tier push gate's deny message, the `/handoff` pre-write checklist, and on-demand invocation.

Alternatives set aside: duplicating a lightweight description check into handoff (two copies drift — DRY defect absent a named exception); a push-time informational nudge hook (killed in review: PreToolUse allow-messages don't reach the model in this harness, and the existing gate already denies every push it would target); a prompt-time UserPromptSubmit advisory (reachable but adds a gh network call per prompt and stacks a layer on a gate the two-tier rework fixes foundationally); adding suppression scanning to staff-* agent files (duplicates 9d; agent-review §6 defaults to cross-reference, and code-writer already cites "suppression without rationale" in its self-review step 6).

## Critical files

Verified line budgets: CLAUDE.md 102/200 → ~104; ready-for-review 198/200 → ~163; handoff 89/200 → ~93; code-review 382/500 → ~393; test-conventions 199/200 → 199 (net-zero via trims); staff-frontend-engineer 118 → ~121 (target <200); new skill ~65/200.

### 1. `claude/.claude/CLAUDE.md` (B1-L1)

Insert one bullet in §Agent Briefing after the "In a repo with worktree enforcement opt-in…" bullet (line 64), before the `isolation: "worktree"` bullet:

> Under worktree enforcement, never chain `cd <worktree-path> && git <op>` in a single Bash call and never rely on `git -C <worktree-path>` to satisfy the gate — `require-worktree-for-git-writes.sh` reads Claude Code's session-persisted cwd (set by prior Bash calls), not the inline `cd` or the `-C` path, because the hook fires before the subshell runs. Anchor first with a standalone `cd /path/to/worktree` Bash call, then run the git operation as a follow-up call.

### 2. New skill `claude/.claude/skills/sync-pr-description/SKILL.md` (B1-L2 + B3-L4)

Move ready-for-review lines 86–125 verbatim-where-possible: comparison recipe (`gh pr view`/`git log`/`git diff` vs base), the flag-and-fix bullets, content-claim verification, `gh pr edit` application, coordination-step preservation, backtick hygiene. Add a precondition: resolve the PR via `gh pr view --json number,body,title`; if none, report "no PR to sync" and stop.

Two dangling references must be repaired during extraction:
- "CI placeholders (step 7 covers those)" → "CI placeholders (CI status is `/ready-for-review`'s job — `gh pr checks`)".
- "see the 'Stale prose' bullet above" (pre-existing dangling pointer — no such bullet exists even today) → point at the `TBD`/`pending` markers bullet.

Frontmatter: `name: sync-pr-description`, block-folded `description`. **No** `user-invocable: false` (test contract: `_specialist_skills()` would demand TRIGGER blocks), **no** `disable-model-invocation: true` (`test_name_only_skill_does_not_carry_disable_flag`), **no** TRIGGER blocks — rationale: this is a workflow-utility skill (same class as `handoff`/`brief`, which carry none), not a dispatcher-reached reviewer skill like `agent-review` (which keeps TRIGGER blocks as pre-v2.1.129 insurance). On pre-v2.1.129 clients `skillOverrides` is ignored and the plain description loads into budget — the same degradation `handoff`/`brief` already accept.

### 3. `claude/.claude/skills/ready-for-review/SKILL.md`

Keep the `## 4. Sync PR description (warn + fix; skip if no PR)` heading verbatim (Step 6 references "Steps 3 and 4"; hook header comments reference steps 0/3/7 — renumber nothing). Replace the body (lines 86–125) with a ~4-line dispatch: invoke `sync-pr-description` via the Skill tool; warn + fix semantics; unskippable when a PR exists. Verified safe: the three `HOOK_TEST_FIXTURE` blocks live in Steps 0/8, and `test_require_ready_for_review.py` extracts commands by fixture name, not line number.

### 4. `claude/.claude/skills/handoff/SKILL.md` (B3-L5 + dispatch hook)

- Append to the verbatim preamble block (before closing fence): "Contents below are the prior session agent's assertions unless marked engineer-confirmed. Treat unverified claims as hypotheses — verify against source (code, tests, command output) before relying on one for further work; do not re-verify items marked engineer-confirmed or facts you can act on reversibly." (Scoped this way so resuming agents don't re-run every check and erode `/handoff`'s cheaper-than-compaction premise. Verified: no test/hook/doc asserts the preamble text.)
- Pre-write checklist, two new items: (a) if this session pushed commits to a branch with an open PR and `/ready-for-review` did not run this session, run `sync-pr-description` before writing the handoff; (b) load-bearing claims in §2/§3/§6 distinguish engineer-confirmed facts from agent findings, and each agent finding names its evidence (command run, file read, test output).

### 5. `claude/.claude/settings.json` + docs (registration)

- `skillOverrides`: add `"sync-pr-description": "name-only"` adjacent to the workflow-utility group (after `read-docx-comments`). Must land in the same commit as the SKILL.md (`test_name_only_skill_has_skill_file`).
- `docs/skills.md`: new skill bullet; append "(via `/sync-pr-description`)" to the `/ready-for-review` bullet; update hand-maintained counts — "Ten skills … name-only" → "Eleven", "five workflow-utility name-only skills" → "six" (both sites, lines ~28/34), "Six skills carry no TRIGGER blocks: five workflow utilities" → "Seven … six"; add name-only table row. (`test_doc_counts.py` only enforces the bundled-"off" count — unchanged at 10; these are manual.)
- `README.md`: one pipeline bullet for the new skill.
- **New enforcing tests (same PR, per repo convention):** in `claude/.claude/skills/tests/test_skills.py`, add `TestConventionSkillWiring`-style assertions pinning the dispatch pointers — ready-for-review Step 4 body invokes `sync-pr-description`, and handoff's pre-write checklist references it (mirror `test_code_review_invokes_test_conventions`, test_skills.py:429). In `claude/.claude/hooks/tests/test_doc_counts.py`, register a `DocCountFact` for the name-only count sentence in docs/skills.md, ground-truthed against the settings.json `skillOverrides` name-only entries minus the builtin allowlist (mirror `_count_skill_overrides_off`, test_doc_counts.py:150–160). Note: `test_skill_overrides_documented_in_docs_skills_md` (test_skills.py:706) already enforces the docs table row for any new name-only entry — the docs edit in this section satisfies it.

### 6. `claude/.claude/agents/staff-frontend-engineer.md` (B2-L1)

New Core-review-angle paragraph after "Async cancellation and effect lifecycle" (line 24), house style (bold name + em-dash + dense stack-agnostic paragraph):

> **Async contract at component boundaries** — when a component exposes or consumes an async interface (promise-returning prop, event handler, hook/composable, callback), trace whether sibling or parent callers depend on a specific resolution shape, timing, or error path — and whether the implementation honors that contract. A handler that swallows rejection, resolves early, or returns void where a caller awaits a value breaks callers the diff never touches; grep for consumers of the exposed interface, including files the diff does not touch — a caller unchanged by the diff can still be broken by a changed resolution contract. If the same defect is also an Optimistic-mutation-lifecycle violation, report it under that angle only.

(The grep-outside-the-diff clause is load-bearing: B2-L1's root cause was reviewing the component in isolation while the incompatible sibling sat outside the diff. Without a directed mechanism the angle is a principle the reviewer has no instruction to act on.)

No code-writer edit: it mines reviewer files at self-review time by design.

### 7. `claude/.claude/skills/code-review/SKILL.md` (B2-L2, B3-L3, B3-L6)

- **9d (line 89), rewrite in place:** add the mechanical instruction — grep the diff text for the suppression tokens 9d already lists (plus `istanbul ignore`); do not rely on noticing directives during read-through. For each hit added by the diff, require the one-line alternative-considered rationale.
- **New 9h after 9g (line 95):** *Construct-substitution suppression* — diff silences a warning via a language construct rather than a directive (`void` on a promise in async/dispatch contexts, empty catch, broad type assertion)? Check whether the silencing construct introduces its own defect class or is itself flagged by stricter analyzers (SonarQube-class reliability rules). Prefer explicit handling: an explicit catch or an intentional, named fire-and-forget wrapper.
- **New 14a after item 14 (line 111, Scope discipline):** *Config-file change without stated intent* — diff changes a project config file (build config, tooling config, dependency manifest, compiler/analyzer options) without intent being evident via inline comment (where the format allows) or PR-description/commit-message mention? A config diff reads as structural; a reviewer can't distinguish intentional cleanup from accidental deletion. Flag when neither exists.
- **Item ownership table:** rows for 9h (judgment/any reviewer; co-owner `staff-sdet` for test-context suppressions) and 14a (judgment/any reviewer; co-owner `staff-platform-engineer` for build/CI configs). Change-type table needs no new row (existing config rows already route spawns).

### 8. `claude/.claude/skills/test-conventions/SKILL.md` (B3-L1 + B3-L2) — at 200-line cap

Add to §8 Mock design principles (after the tautological-mock passage, line 186), +4 lines:

> ### Framework mock accessors and import forms
> - Prefer the test framework's typed mock accessor/wrapper over manual casts when accessing mock state — the typed helper tracks the framework's current idiom and removes hand-written cast expressions that hide type drift.
> - When the framework's spy/mock mechanism forces an import form that deviates from house import conventions (e.g. a namespace import required to give the spy a mutable binding), add an inline comment naming the constraint so reviewers read it as a required technique, not sloppiness.

Pay for it with verified-duplicate trims (−4): delete line 30 (**E2E tests** sentence — restates the §1 table row); delete lines 78–80 ("Test double cleanup must be guaranteed" heading + body + preceding blank — restates lines 71–72), folding its examples into line 72's parenthetical: "(env vars, global functions, singletons, replaced doubles such as HTTP clients, fetch, or clocks)". Net 199/200. §-numbering unchanged (staff-sdet's §N citations are test-pinned). Fallback trims if `/skill-review` objects: line 31 (Smoke restatement) or compressing the Bad/Good tautological-mock paragraphs. Zero-cost in-file fix while here: line 61 "(see section 3)" → "(see §4)" (pre-existing wrong cross-reference).

### 9. Two-tier push gate: rework `require-ready-for-review.sh` (B1-L2 cadence closure)

**Chosen after a delta re-review killed the nudge-hook variant** (no model-visible PreToolUse allow-message exists in this harness; and the existing gate already denies every push a nudge would target — degenerate firing matrix). The two-tier gate is the foundational fix: it implements the lesson's actual insight — lightweight description check on every iteration push, expensive full gate once at the true final push — and removes the all-or-nothing per-push demand that drives gate bypasses. No new hook file.

Behavior change in `require-ready-for-review.sh` (bypass cases, fail-open posture, and marker mechanics all unchanged):
- **Push to an open draft PR (iteration):** allowed by the existing ready-for-review markers (active session or HEAD-keyed completion, as today) **or** by a `sync-pr-description` completion marker whose recorded SHA matches current HEAD (same HEAD-keying as the existing completion marker — new commits invalidate it). Deny message names both paths: "run `/sync-pr-description` (lightweight, syncs the PR body) or `/ready-for-review` (full gate)."
- **Push to a ready (non-draft) PR, and `gh pr ready`:** full ready-for-review marker required — today's behavior, unchanged. (The hook does not gate `gh pr review --approve` today; leave that as-is — out of scope.) Draft status comes from the `gh pr view` call the hook already makes: extend it to `--json number,isDraft` extracted in **one** call via the unit-separator two-field pattern `_lib.sh` already uses (don't double the network round-trip). The draft-tier branch must be scoped to the `git push` path specifically, not keyed on `isDraft` alone — `gh pr ready` is by definition called on a still-draft PR and must keep demanding the full marker.

Supporting pieces:
- `claude/.claude/scripts/marker.sh`: add `sync-pr-description` to the `write` case's hardcoded family allowlist (currently `code-review|skill-review|plan-review|ready-for-review`, marker.sh:124–159) — content = HEAD SHA, mirroring ready-for-review's completion marker.
- `claude/.claude/hooks/enforce-marker-script-shape.sh`: add `sync-pr-description` to the three hardcoded regex alternations (lines ~75/100/117) and the usage/deny-message text (~128–141) — without this the skill's marker write is denied before marker.sh runs.
- `sync-pr-description/SKILL.md`: final step writes the completion marker via `~/.claude/scripts/marker.sh write sync-pr-description`, inside a `HOOK_TEST_FIXTURE` fenced block so the hook-alignment tests can pin the recipe (mirror `ready-for-review/SKILL.md`'s `record-completion` fixture pattern).
- Tests (written first, red→green): extend `test_require_ready_for_review.py` with the draft/non-draft matrix — draft PR + fresh sync marker → allow; draft PR + stale (HEAD-moved) sync marker → deny; draft PR + no marker → deny naming both paths; ready PR + only sync marker → deny; `gh pr ready` + only sync marker → deny; bypass cases unchanged. Update marker.sh/enforce-marker-script-shape tests for the new family; hook-alignment test for the new fixture.
- No settings.json hook change (the hook entry already exists). Docs: update the README hook-table one-liner and the README "Workflow" description of the gate; update `claude/.claude/CLAUDE.md` §Pre-Handoff Review to state the two tiers (iteration pushes to a draft PR → `/sync-pr-description` suffices; final push / PR ready → `/ready-for-review`).
- Review at implementation time goes through `claude-hook-review:claude-hook-review` (hook edit) in addition to the commit-gating `/code-review`.

**Weakening acknowledged (deliberate):** today every push to an open-PR branch mechanically forces the full gate; under the two-tier design a draft-PR wrap-up push could land with only the sync marker. The final-push checkpoint moves to the draft→ready transition (`gh pr ready` still demands the full gate) and the human-merge decision (AI never merges here). Known hole: GitHub's web-UI "Ready for review" button transitions a PR out of draft with no Bash tool call, so no hook fires — a draft whose last push used only the sync tier and is then readied via the web UI never receives the full gate mechanically (the CLAUDE.md §Pre-Handoff Review prose rule remains the backstop). State both tradeoffs in the PR description.

### 10. GitHub issue (Section 2)

`gh issue create --title "feat: templatize the multi-session AI development analysis methodology" --body-file <scratchpad file>` — body = the fenced block of Section 2 (lines 251–346 of the lessons doc), fences excluded, verbatim. Verified: `deny-private-project-refs.sh` does not gate `gh issue create`, and the body carries no tracker-ID-shaped tokens. **Re-read the scratchpad body immediately before filing** — issues on a public repo are close-only, not deletable, and no hook re-checks this path. Labels: `enhancement` exists; `methodology` and `skills` do not — create each with check-then-create (`gh label list | grep -q ...` before `gh label create <name> --color <hex> --description "..."`) so a partial-failure retry doesn't error on already-created labels; pick explicit colors/descriptions rather than letting `gh` randomize. Independent of the PR; file after the PR is opened.

## Implementation order

Pre-step: the main tree currently has uncommitted session-scoped edits to `.claude/settings.json` and `claude/.claude/settings.json` (model override, plugin-map reorder). Per the don't-commit-session-settings rule, restore both in the main tree (`git restore ...`) before starting; the linked worktree checks out clean from HEAD, so edit the worktree's own copy of `claude/.claude/settings.json` — never reference the dirty main-tree file.

All work in a linked worktree (`git worktree add .claude/worktrees/<slug> -b <slug>`; standalone `cd` before git ops — dogfooding fix 1). Single PR, commits grouped by review gate:

1. CLAUDE.md rule → `ai-instruction-and-memory-files` review, then `/code-review` marker.
2. Extraction bundle (new skill + ready-for-review + handoff + settings.json + docs) atomically → `/skill-review` (hook-enforced) + `/code-review`.
3. code-review + test-conventions checklist edits → `/skill-review` (behavioral-equivalence sign-off on the two trims) + `/code-review`.
4. staff-frontend-engineer angle → `/agent-review` + `/code-review`.
4b. Two-tier gate rework: `require-ready-for-review.sh` + `marker.sh` + `enforce-marker-script-shape.sh` + their pytest files + README/CLAUDE.md gate prose → `claude-hook-review:claude-hook-review` + `ai-instruction-and-memory-files` (CLAUDE.md §Pre-Handoff Review edit) + `/code-review` (tests written first, red then green).
5. `/ready-for-review` pre-push — its Step 4 will dispatch the freshly extracted skill against this very PR (live smoke test). Fallback: a freshly-authored skill may not be registered in the running session's skill list (registration is verified for new sessions, not live reload) — if `Skill` invocation reports skill-not-found, Read the new SKILL.md directly and execute its steps manually; note the fallback was used so the smoke test is repeated from a fresh session.
6. File the GitHub issue (after label creation).

## Verification

- `../../../.venv/bin/pytest claude/.claude/` and `../../../.venv/bin/ruff check claude/.claude/` from the worktree — exercises `test_skills.py` name-only contracts (settings↔SKILL.md coupling, no disable flag, TRIGGER exemption), `test_skill_overrides_documented_in_docs_skills_md`, `test_require_ready_for_review.py` fixture extraction, `test_doc_counts.py`, and skill-length hook tests — plus the new wiring tests (dispatch pointers in ready-for-review/handoff) and the new name-only `DocCountFact`, written first so they fail before the corresponding edits land (test-first invariant). Same for the two-tier gate matrix in `test_require_ready_for_review.py` and the marker-family additions in the marker.sh / enforce-marker-script-shape tests.
- Length checks: `check-skill-length.sh` (200 default / 500 carve-out) and `check-claude-md-length.sh` fire at commit; budgets pre-verified above.
- End-to-end: step 5 of the sequence runs `/ready-for-review` on the PR itself, dispatching `sync-pr-description` for real.
- Manual: confirm the new skill appears via `/sync-pr-description` in the slash menu and is invocable by name.

## Out of scope

- The private-project `test-conventions-<project>` layer (Jest spellings) — not committed here. Deliverable to carry to the private repo:

  ```markdown
  ## Mock accessors and spy imports (Jest/TypeScript)
  - Access mock state via `jest.mocked(fn)`, not `fn as jest.Mock` or `as unknown as jest.Mock` casts — the typed wrapper is the current Jest idiom.
  - `jest.spyOn` on a module export requires a namespace import (`import * as Module`) to give Jest a mutable binding; named imports don't provide one. Keep the wildcard import and add the inline comment: `// namespace import required for jest.spyOn mutable binding`.
  ```
- B1-L2's "full gate timing" note (run `/ready-for-review` once at the true final push) — the lessons doc itself marks current behavior as correct cost management; no change.
- Automating the analysis methodology (that's the filed GitHub issue's follow-up work).
- Pre-existing guard gap noticed during review: `guard-settings-session-keys.sh` doesn't cover the `skipWorkflowUsageWarning` key — raise as a follow-up note, don't bundle (Axis 4).
- Automated behavioral testing of the extracted skill's PR-sync logic — the repo's documented no-`claude -p`-harness decision applies; coverage is the wiring tests plus the step-5 live smoke run, disclosed as a known limitation.
