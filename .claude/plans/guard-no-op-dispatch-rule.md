# Guard no-op dispatches from an always-loaded surface

## Context

A recurring model-behavior bug — a session spawning a subagent whose only job
is to wait, occupy the turn, or report back immediately while other background
dispatches are already pending — recurred one day after the advisory-only fix
for it shipped. PR #819 (merged 2026-09-02, commit
`b467b574`) added a prose rule to
`claude/.claude/skills/subagent-delegation/SKILL.md` Step 1 stating that a
dispatch must return something the parent does not already have. On 2026-09-03
a session in this repo spawned exactly that shape while waiting on a
backgrounded pytest run, and when asked afterward confirmed it had never loaded
the skill body — only the one-line trigger description from the
available-skills listing. The intended outcome is to move the prohibition to a
surface that is in context by construction, so the guard no longer depends on
the skill being consulted at the decision point, and to record the next
escalation step durably so the decision is not re-litigated a third time.

## Approach

Move the no-op-dispatch prohibition into `claude/.claude/CLAUDE.md`'s Agent Briefing section as a self-contained bullet, so the guard is in context on every turn rather than only when `subagent-delegation` happens to load, and record the mechanism choice plus its escalation trigger as `docs/design-decisions.md` §44. The `subagent-delegation` skill body is not touched: it keeps the return-value framing and delegation-cost reasoning that the CLAUDE.md prohibition is a special case of. No hook ships, and no test asserts the rule's text.

**Verbatim text — the CLAUDE.md bullet.** Insert as a single physical line, matching the bolded-lead single-line bullet shape Agent Briefing already uses at lines 77, 88, and 89:

```markdown
- **Never dispatch an agent — of any type — whose instructions are to do no work** — to report back immediately, to occupy the turn, or to hold while other dispatches finish. A dispatch must return something the parent does not already have. A no-op agent returns at once, so it waits for nothing — waiting isn't an action a dispatch can perform — yet still pays a full agent's context cost for an empty return. When pending dispatches are all that remain, end the turn without a tool call and let their completion drive the next one.
```

**Verbatim text — the `docs/design-decisions.md` §44 entry.** Append at the end of the file, after §43:

```markdown
## 44. The no-op-dispatch guard is a CLAUDE.md rule, not a hook (2026-09-03)

A session that spawns a subagent whose only instruction is to wait, occupy the turn, or report back immediately pays a full agent's context for an empty return. The prohibition first shipped as prose in `subagent-delegation/SKILL.md` Step 1 on 2026-09-02. It recurred on 2026-09-03 in a session that never loaded the skill body — only the one-line trigger description from the available-skills listing. That is the whole failure: a skill-body rule reaches a session only when the skill is in context, and this impulse arrives at moments that do not look like delegation decisions. Two occurrences are confirmed, both in this repo's own history:

- `memory-content-migration`, 2026-08-30
- `discovery-audit-remediation-plan`, 2026-09-03

That is a floor from keyword search and one direct admission, not an exhaustive count: transcripts do not record which skill bodies were in a session's context, so this repo's own history cannot be counted as exhaustive. The rule now also sits in `claude/.claude/CLAUDE.md`'s Agent Briefing section, which every session loads on every turn.

**§1 prefers a hook, and no hook is available here.** A `PreToolUse` hook on the `Agent` tool receives `tool_name`, `tool_input` (`subagent_type`, `prompt`, `description`, `model`), `session_id`, `cwd`, and `transcript_path`. None of those carries a non-content signal for "this dispatch does no work." The one non-content fact reachable — that another `Agent` dispatch is already in flight, via transcript parsing — is not the defect: `plan-it` Step 3 encourages parallel fan-out, so gating on it would deny legitimate dispatches. A hook would therefore have to match on prompt text. No hook in `claude/.claude/hooks/` reads `.tool_input.prompt` or `.subagent_type` today, and `require-routing-read.sh` — the repo's only `Agent`-matching hook — gates on a file-read marker instead. §1's preference holds where a mechanical predicate exists. Here there is none, so the real choice is between two advisory surfaces, and the always-loaded one wins.

**Partial duplication, accepted under a named exception.** CLAUDE.md carries the prohibition, the shapes it covers, and what to do instead. `subagent-delegation` keeps the return-value framing and the delegation-cost reasoning. Both surfaces must stand alone, because a session that loaded the skill and a session that did not are different readers — and the second reader is why this entry exists. That is CLAUDE.md §Engineering Judgment's "instructional prose that must stand alone" exception, not an unexamined copy. The overlapping sentences are worded identically on both surfaces so that `git grep` exposes drift.

**No mechanical enforcer.** The trigger is dispatch intent, not a file-content fact. A test asserting the bullet's literal string would be tautological and would break on any rewording.

**Revisit** if the behavior recurs after this rule ships. The next lever is a `PreToolUse` hook on `Agent`, accepting prompt-content matching and its false-positive cost. At that point two advisory surfaces will have failed, which is itself the evidence that no advisory surface reaches this impulse.

### Sources

- `claude/.claude/CLAUDE.md` §Agent Briefing — the rule's text.
- `claude/.claude/skills/subagent-delegation/SKILL.md` Step 1 — the same rule with its delegation-cost reasoning.
- `claude/.claude/hooks/require-routing-read.sh` — the repo's only `Agent`-matching hook, gating on a marker rather than on tool-input content.
- `.claude/plans/guard-placeholder-wait-forks.md` — the first mechanism choice, including the ledger rows that rejected both a hook and CLAUDE.md at that time.
- `.claude/plans/guard-no-op-dispatch-rule.md` — this change's assumption ledger.
```

**Assumption ledger**

**Root:** The no-op-dispatch prohibition lives only in a skill body, so it reaches a session only when that skill loads — and the impulse it guards against arrives during work that does not look like a delegation decision, so the skill is routinely not loaded at that moment.

**Givens:**

- A skill body enters a session's context only when the harness decides to load the skill; that decision is Anthropic-owned and not configurable from this repo. Consequence for the design: any guard placed in a skill body carries the root failure by construction, so closing it means moving to a surface loaded unconditionally.
- The `PreToolUse` `Agent` payload's field set is harness-owned. This repo cannot add a field, so no non-content predicate can be manufactured for a hook.
- Transcripts do not record which skill bodies were in a session's context. The occurrence count can only ever be a floor, and no repo-side instrument changes that.
- `claude/` is stowed, so `claude/.claude/CLAUDE.md` installs to every consumer who runs `./install.sh`. The audience is every stow consumer, not this session's owner, and the wording must stay platform-agnostic.

**Rows:**

1. Mechanism is a rule in `claude/.claude/CLAUDE.md` now, with a `PreToolUse` hook on `Agent` recorded as the escalation if the behavior recurs after this rule ships. `[engineer-verified]` — the engineer's explicit answer this session, chosen to keep the escalation ladder explicit rather than exhausting it. `anchors: root`
2. Over-powered-primitive check on row 1. CLAUDE.md is *wider* scope than the failed surface — every session pays for it on every turn — so the check runs against four lighter primitives, each rejected. (a) The skill body alone: already shipped, and the 2026-09-03 recurrence is the proof it does not reach the impulse. (b) The `subagent-delegation` frontmatter `description`, the one skill surface always present via the available-skills listing: rejected because that field's job is to decide *whether* to load, and a `DO NOT TRIGGER` addition would suppress the skill in exactly the situation the rule must cover (prior plan row 9, carried forward). (c) A path-scoped `.claude/rules/` file: this repo's four rules files (`review-pipeline-dispatch.md`, `settings-json-conventions.md`, `skill-and-agent-self-review.md`, `test-tree-packaging.md`) auto-load by matching a `paths` frontmatter pattern against open files, and a no-op dispatch has no characteristic file path to match on. (d) The `Agent` tool's own description, the surface actually in context at the decision point: harness-owned and not editable from this repo. The heavier primitive — a `PreToolUse` hook — is rejected in row 6. `[verified: Glob of **/.claude/rules/*.md returned 4 files — review-pipeline-dispatch.md, settings-json-conventions.md, skill-and-agent-self-review.md, test-tree-packaging.md — each gated by a `paths` frontmatter pattern]` `anchors: row1`
3. Section is **Agent Briefing**, not Working Style. `[verified: claude/.claude/CLAUDE.md — dispatch-*shape* rules cluster in Agent Briefing (75–99) and Model & Effort Routing (100–113); incidental mentions at 131 (Safety) and 176 (Shipping); none in Working Style]` — CLAUDE.md loads whole, so placement does not affect whether the rule is seen; it affects coherence for a reader and a future maintainer. Working Style's line 42 governs delegate-vs-inline cost; this rule governs which dispatch shapes are legitimate, which is Agent Briefing's subject. `anchors: row1`
4. Placement is the second bullet in Agent Briefing, immediately after the bullet beginning `- **A prescribed dispatch is an authorized dispatch.**` (currently line 77). `[verified: same file:77]` — that bullet establishes when a dispatch is *authorized*; this one establishes the shape that is never legitimate regardless of authorization, so adjacency blocks the misread that a prescribed dispatch needs no further test. `anchors: row3`
5. The clause is a self-contained rule, not a pointer clause hung on line 42's existing `subagent-delegation` reference. `[engineer-verified]` — a pointer still requires loading the skill, the exact step that failed. The engineer accepted the partial-duplication cost with the mitigation "bare prohibition in CLAUDE.md, reasoning in the skill." `anchors: row1`
6. Prior plan row 2(b)'s rejection of CLAUDE.md no longer controls, on two independent grounds. First, its "the skill is the single source of truth for dispatch-vs-inline judgment" premise addressed a *different* piece of knowledge: line 42 defers the delegate-vs-inline cost call to the skill, and this rule is not that call. Second, "wider scope, not lighter" was the right objection when the skill body had not yet been shown to miss; the 2026-09-03 recurrence converts that width from a cost into the requirement. `[verified: .claude/plans/guard-placeholder-wait-forks.md:88-93; claude/.claude/CLAUDE.md:42]` `anchors: row5`
7. The DRY split is by knowledge, not by sentence: CLAUDE.md is canonical for the prohibition and its self-check ("returns at once, so it waits for nothing"); `subagent-delegation` Step 1 is canonical for the return-value framing and the delegation-cost economics the prohibition is a case of. The residual sentence-level overlap falls under CLAUDE.md §Engineering Judgment's named exception (2), "instructional prose that must stand alone" — both readers exist, and the second one's existence is the defect. `[verified: claude/.claude/CLAUDE.md:7]` `anchors: row5`
8. Overlapping sentences are worded **identically** on both surfaces rather than paraphrased. `[verified: claude/.claude/skills/subagent-delegation/SKILL.md Step 1, quoted in full in the dispatch]` — identical wording satisfies §Prose's "one term per concept" and makes future drift greppable; a paraphrase would read as a second, subtly different rule. `anchors: row7`
9. `claude/.claude/skills/subagent-delegation/SKILL.md` is not modified at all — not the paragraph, not the frontmatter. `[verified: file is exactly 200 lines, `check-skill-length.sh`'s hard cap; `.claude/rules/review-pipeline-dispatch.md` makes `/skill-review` hook-enforced on any SKILL.md change]` — four reasons, each sufficient: the paragraph must stand alone for a reader who opened the skill directly (row 7); any insertion forces an offsetting trim at the cap; any edit arms `require-skill-review.sh` for a hook-enforced review round that buys no behavior change; and replacing the paragraph with a pointer up to CLAUDE.md inverts the reference direction, sending a reader out of the deeper surface for the rule they came for. `anchors: row7`
10. `claude/.claude/CLAUDE.md` line 42 is not modified. `[verified: same file:42]` — it points at the skill for the two-test gate, distinct knowledge from this rule; editing it would re-import the pointer shape row 5 rejected. `anchors: row7`
11. The deferral's home is a new `docs/design-decisions.md` §44, not the plan file alone. `[engineer-verified]` — the prior rejection lived only in a plan file and was nearly lost this session; 274 committed plan files are not a discoverable surface. `anchors: root`
12. §44 must reconcile §1 ("Hook-enforced gates over advisory instructions") head-on, since this plan chooses the opposite. `[verified: docs/design-decisions.md:5-7]` — the reconciliation is that §1 presupposes a mechanical predicate exists; here none does, so the comparison is advisory-vs-advisory and §1 is not in play. `anchors: row11`
13. No non-content signal exists to ground an `Agent` hook. `[verified: grep across claude/.claude/hooks/*.sh — no hook reads `.tool_input.prompt` or `.subagent_type`; `require-routing-read.sh:33` is the only `TOOL_NAME = "Agent"` match and gates on a marker; `plan-it/SKILL.md:37` encourages parallel fan-out, so "another dispatch is in flight" would deny legitimate calls]` — this is the substantive reason the CLAUDE.md route wins now, independent of the prior plan's prescription. `anchors: row1`
14. §44 follows the §41–43 shape: `## N. <title> (<date>)`, free-form prose with bolded-lead subsections, a `**Revisit** if …` clause, and a closing `### Sources` list. `[verified: docs/design-decisions.md:706-752 (§41, §42 revisit clauses and Sources), :753 (§43 heading with date)]` `anchors: row11`
15. §44 narrating prior state ("first shipped as prose… recurred the next day") does not violate CLAUDE.md §Code Comments' "No 'used to be X'" bar. `[verified: docs/design-decisions.md:690-752]` — §§41–43 each narrate considered-and-rejected alternatives and prior behavior; a decision log's genre is the record of the decision, which the rule's own Axis 3 decision test classifies as preserved record rather than current-behavior description. Flagging this here so a reviewer reads it as a genre call, not an oversight. `anchors: row14`
16. §44 is the correct number and no collision exists. `[verified: docs/design-decisions.md:753 is `## 43.`, the highest]` — a parallel branch landing its own §44 first would require renumbering, so this is re-checked against the file at commit time (Verification step 3). `anchors: row11`
17. The CLAUDE.md bullet does **not** carry a `docs/design-decisions.md` §44 pointer, nor a `subagent-delegation` pointer — both were drafted and cut. `[verified: claude/.claude/skills/ai-instruction-and-memory-files/SKILL.md:80-86 (the behavior test), :96-100 (don't embed PR/ticket refs — "state the rule, not the precedent")]` — two reasons. First, the behavior test: a session that has already read the prohibition and its self-check behaves no differently for knowing where the rationale is filed, so the pointer earns no per-turn cost. Second, "state the rule, not the precedent" puts a decision-log section citation in the commit message or PR body, not in an always-loaded file. The `subagent-delegation` half is separately redundant with line 42's existing pointer to the same skill. §44 stays discoverable without the forward pointer: its own `### Sources` list names CLAUDE.md §Agent Briefing, so the cross-reference survives when a reader runs docs→CLAUDE.md, and `git blame` on the bullet reaches the commit message. `anchors: row11`
18. `claude/.claude/CLAUDE.md` has 22 lines of headroom and the change adds one. `[verified: file is 178 lines; check-claude-md-length.sh:63-74 sets the limit at 200 and `_lib_staged_length_gate` denies only when the staged file both exceeds the limit and grew]` — 179 lines clears both limbs. `anchors: row3`
19. No test asserts the rule's literal text, and none is added. `[engineer-verified]` — carried forward from the prior plan's row 11; the trigger is dispatch intent, not a file-content fact, so a body-string assertion is tautological and brittle to rewording. `anchors: row1`
20. Neither committed artifact carries token figures, the private project's name, its repo path, or a branch identifier from it, and neither states or implies an exhaustive occurrence count. `[verified: CLAUDE.md §Redact private-project-identifying content, provenance paragraph; .claude/plans/guard-placeholder-wait-forks.md:121-127]` — a recalled or recomputed figure carries that engagement's fingerprint. Binds the plan file, the §44 entry, the CLAUDE.md bullet, the commit message, and the PR body. `anchors: root`
21. Both changed paths route to the same two test directories under `select-tests.py`. `[verified: select-tests.py:115 `GLOBAL_CLAUDE_MD`, :332 exact-match `CROSS_DOMAIN_EXCEPTIONS` entry → (`HOOKS_TESTS_DIR`, `SKILLS_TESTS_DIR`); :97 `DOCS_DIR`, :328 blanket `_is_under(p, DOCS_DIR)` → the same pair]` — the diff is squarely inside the rule table, so no hand-widening to the full suite is warranted. `anchors: root`
22. `test_doc_counts.py`'s three `docs/design-decisions.md` occurrences are unaffected by appending §44. `[verified: test_doc_counts.py:345-359 — all three patterns are §3-specific (`## 3\. Specialist reviewer roster \((\d+) personas\)`) or reviewer-count prose]` — no pinned count tracks the number of sections. `anchors: row11`
23. The rule says "an agent" with no `fork` carve-out and no `fork`-named example. `[verified: the shipped skill text quoted in the dispatch uses "an agent — of any type"]` — the observed occurrences used unrelated prompt wording, so the rule must state the shape, not a phrasing. `anchors: row1`

## Critical files

- `claude/.claude/CLAUDE.md` — **modify.** Insert the verbatim bullet from the Approach section as a new single-physical-line bullet immediately after the existing `- **A prescribed dispatch is an authorized dispatch.**` bullet (currently line 77), inside the `## Agent Briefing` section. Nothing else in the file changes — line 42's `subagent-delegation` pointer stays exactly as it is (row 10), and the missing blank line before `## Code Review` at line 63 stays as it is (Out of scope).
- `docs/design-decisions.md` — **modify.** Append the verbatim `## 44.` entry from the Approach section at the end of the file, after §43's `### Sources` block ending at line 816. No existing section is edited.

**Not modified:** `claude/.claude/skills/subagent-delegation/SKILL.md`. Row 9 carries the four reasons. If an implementation impulse arises to add a cross-reference there, it is out of scope, and the file's 200-line cap makes it non-trivial besides.

**Reuse opportunities:**

- Reuse the skill's exact operative wording where the two surfaces overlap ("returns at once", "pays a full agent's context cost for an empty return", "end the turn without a tool call and let their completion drive the next one") rather than paraphrasing — row 8.
- Match Agent Briefing's existing bolded-lead single-physical-line bullet shape (lines 77, 88, 89) rather than the wrapped multi-line shape (lines 80–85, 90–98).
- Match §41's and §42's `**Revisit** if …` clause form and their `### Sources` list shape rather than inventing a new subsection template.

**Dispatch split:** one `code-writer` dispatch, `model: sonnet`, not splittable. Two files, both prose, both with verbatim text fixed by this plan — the decision-made test holds by construction. The §44 entry describes the CLAUDE.md bullet's placement and reasoning, so the two edits share one background that would have to be restated in full if split; `plan-it` Step 5 bars splitting on exactly that condition. The parent retains `/code-review`, the review marker, and the commit: `code-writer` holds no `Skill` tool and is denied marker writes, so any review gate it hits returns to the dispatching session.

## Verification

`ruff` and `shellcheck` are no-ops for this diff — it changes two Markdown files and no Python or shell. Do not run them and do not claim they passed.

1. `.venv/bin/python3 claude/.claude/scripts/select-tests.py` from this worktree root, using the worktree's own `.venv` (README's Tests section covers the worktree-relative `.venv` paths). Both changed paths map to `claude/.claude/hooks/tests` and `claude/.claude/skills/tests` (row 21). Let it select — do not widen to the full suite by hand; CI runs the full suite on push.
2. `git show :claude/.claude/CLAUDE.md | wc -l` against the **staged** content, confirming ≤ 200. This mirrors `check-claude-md-length.sh`'s own count so the gate does not fire at commit time. Expected: 179.
3. `grep -n '^## 4[0-9]\.' docs/design-decisions.md` on the staged file, confirming exactly one `## 44.` heading and that 44 is the highest. A parallel branch that lands its own §44 first forces a renumber here (row 16).
4. Two falsifiable checks — the drafted bullet carries no `subagent-delegation` reference, so it contributes no hit to this grep. First, `grep -n 'subagent-delegation' claude/.claude/CLAUDE.md`, confirming exactly two hits: line 42 and line 106 (the Model & Effort Routing pointer, shifted down one line by the inserted bullet) — the negative check for row 10. Second, `diff <(git show HEAD:claude/.claude/CLAUDE.md | sed -n '42p') <(sed -n '42p' claude/.claude/CLAUDE.md)`, expecting empty output, confirming line 42 is byte-identical to its committed form.
5. `git diff --stat` confirming exactly two files changed and that `claude/.claude/skills/subagent-delegation/SKILL.md` is absent from the diff (row 9). Its absence is also what keeps `require-skill-review.sh` unarmed, so `/skill-review` is not required for this diff; `plugin-semver` is likewise not triggered, since no file under a plugin directory changes.
6. Read the rendered bullet in place and confirm it sits between the `**A prescribed dispatch is an authorized dispatch.**` bullet and the `isolation: "worktree"` bullet, as a peer of both — the placement is the design (row 4).
7. Read the §44 entry against `docs/design-decisions.md` §1 and confirm the reconciliation paragraph names §1 explicitly rather than ignoring it (row 12), and that no sentence in it carries a token figure, a private project's name, a repo path, a branch identifier from it, or an exhaustive occurrence count (row 20).
8. `/plan-review` on this plan before it is presented, `/code-review` before the commit, and `/ready-for-review` before push — the hook-enforced pipeline.

## Out of scope

- **A `PreToolUse` hook on the `Agent` tool.** Deferred, not rejected: §44's revisit clause records it as the next lever if the behavior recurs after this rule ships. Do not add one now, and do not add a "lightweight" content check on `Agent` tool input as a compromise — that is the same mechanism at a smaller size.
- **Any edit to `claude/.claude/skills/subagent-delegation/SKILL.md`**, including its frontmatter, including trimming its Step 1 paragraph now that CLAUDE.md restates part of it, and including adding a cross-reference from it up to CLAUDE.md. Row 9 carries the reasoning; the file is at its hard cap and any edit arms a hook-enforced `/skill-review` round for zero behavior change.
- **Any edit to `claude/.claude/CLAUDE.md` line 42**, the existing `subagent-delegation` pointer. Row 10.
- **The missing blank line between line 62 and line 63** of `claude/.claude/CLAUDE.md` (Working Style's last bullet and the `## Code Review` heading). A real, pre-existing Markdown defect in a section this change does not touch — Axis 1's revert-by-default bucket. Raise it to the reviewer as a separate one-line fix rather than bundling it here.
- **A test asserting the rule's literal text**, in either file. Row 19.
- **A `fork`-specific carve-out or a `fork`-named example.** Row 23.
- **Any occurrence count stated or implied as exhaustive**, in the plan file, the §44 entry, the CLAUDE.md bullet, the commit message, or the PR body. Two in-repo occurrences are confirmed, established by keyword search and one direct admission, and that is a floor rather than an exhaustive total, because transcripts do not record which skill bodies were in a session's context.
- **Private-project-identifying evidence and per-dispatch token figures** in any committed artifact. Row 20.
- **Migrating other plan-file-only deferrals into `docs/design-decisions.md`.** At least 17 plan files carry a "revisit if X recurs" clause with no `docs/` mirror, and `.claude/plans/code-writer-precondition-reads.md:62` is a near-exact structural twin of this one. Whether that pattern should generalize is a real question and a separate change; this plan moves one deferral because the engineer named a specific loss it already nearly caused.
- **A generic "model-behavior guardrails" or "escalation ladders" home under `docs/`.** No such file exists, and `docs/hooks.md` documents shipped hooks only, with no considered-and-rejected section. Creating one to host a single entry would be a heavier structure than §44 needs.
- **A durable test asserting `docs/design-decisions.md`'s `## N.` section headings are unique and strictly increasing.** Nothing in the repo checks this today — `test_doc_counts.py`'s three `design-decisions.md` patterns are all §3-specific reviewer-count assertions, not section-number tracking. Verification step 3 and ledger row 16 both handle the collision risk by hand, once, which protects nothing after this PR merges: two parallel branches could each append their own `## 44.`. Recorded as a real observation deferred, not a gap in this plan — it protects the whole file rather than this change, and adding it would turn a two-file prose PR into a test-bearing one.
