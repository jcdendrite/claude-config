# Declined a `SessionStart` + `additionalContext` per-subagent injection mechanism for orchestrator-only CLAUDE.md content

*2026-09-03.*

An external critique (Gemini) argued the orchestrator-only block in `claude/.claude/CLAUDE.md` (Agent Briefing, Model & Effort Routing) — 9,656 bytes as of commit `a3b74ce7` — is paid by every dispatched subagent with no reader (`sed -n '98,136p' claude/.claude/CLAUDE.md | wc -c`). It proposed injecting the block only at session start instead, before any delegation decision, sparing every subagent dispatch the block's context cost. The proposal was considered and declined, not deferred, for four reasons in descending weight:

1. **It converts a vendor-guaranteed load into a locally-scripted one whose failure is silent.** A missing, non-executable, or erroring `SessionStart` hook drops the block:
   - No denial.
   - No tool error.
   - No user-visible symptom — the session simply behaves as if Agent Briefing and Model & Effort Routing were never written.

   CLAUDE.md loading has no such failure mode. The effect would land on every stow consumer in every repo.
2. **It moves rules onto a surface of unmeasured adherence in order to improve adherence.** `additionalContext` enters as conversation-position text, not the memory block, and no measurement exists comparing the two — a self-defeating trade for a plan whose own goal is adherence.
3. **The saving lands in the smaller cost slice.** The block would stay in the main thread, which carries 71.4% of dollar cost (`docs/cost-levers-considered.md`), and be removed only from the 28.6% subagent slice. Spend is instead dominated 92.9% by idle-gap cache rebuilds, whose frequency is unrelated to byte count (`docs/cost-levers-considered.md`'s "Context cost root cause" entry).
4. **Its premise is unverified in the general case.** The mechanism depends on `SessionStart` not firing for subagents. See the empirical finding below for what is and is not established about that premise.

**Empirical finding on rule-loading inside a dispatched subagent (narrower than the declined mechanism's own premise).** `.claude/rules/*.md` path-scoped loading fires inside a dispatched subagent on the **Read** tool path for an existing file matching a rule's `paths` glob: a subagent reported no rule text in its context, then the full rule body arrived as a system-reminder immediately after a `Read` matching `**/*.sh`, and the same result reproduced for `.github/workflows/*.yml`. This finding is the plan-authoring session's own direct empirical test, not independently reproduced since.

**Not established by that test:**

- A `Write` to a file that does not yet exist.
- An `Edit`.
- A no-file-touch case where the model reasons about a path without touching it.

Separately, and left genuinely unresolved: whether `SessionStart` itself fires for subagents — the premise the declined mechanism actually rests on — is not settled by the rule-injection test above, which probes a different loading path, and no other empirical check of it has been run.

**No per-agent CLAUDE.md mechanism exists.** Every custom agent receives the full CLAUDE.md hierarchy; no frontmatter field or per-agent setting changes it, and `claudeMdExcludes` matches absolute paths/globs at the settings layer, not per-agent. This is vendor-owned; the plan cannot dissolve the dependency, only choose among the surfaces the vendor provides.

**Rule-count gating for CLAUDE.md/AGENTS.md was considered and declined alongside the above, on separate grounds.** No source gives a defensible threshold: `docs/instruction-file-sizing-evidence.md` §5 finds no peer-reviewed study and no vendor first-party publication measuring adherence against rule count for an always-loaded instruction file. The peer-reviewed instruction-count-degradation literature that does exist scores instructions that all live on the one task being judged, not dormant rules sitting in a file most of which is inactive on any given turn, so its independent variable doesn't transfer to a file-level rule count. `ai-instruction-and-memory-files/SKILL.md`'s pointer to that section states the same foreclosure. The re-opener condition: a source measuring adherence over the count of dormant rules in an always-loaded file that states a threshold or per-rule marginal cost, or a vendor publishing a rule-count figure for CLAUDE.md/AGENTS.md analogous to Anthropic's 200-line figure. Absent one of those, this decision stays closed — not re-derived by the next planning pass.

**Lighter alternatives considered instead, in the order they should be attempted if the CLAUDE.md compression pass (`docs/cost-levers-considered.md`'s "Second pass" entry) still leaves a gap:**

- **Trim the CLAUDE.md restatements out of the agent files.** Several agent files restate CLAUDE.md sections (Engineering Judgment, Scope Discipline, Code Comments) that every subagent already receives in full through the standard hierarchy — cutting the restatement reduces per-dispatch context with zero adherence risk to the main thread and no new mechanism. The correctly-shaped fix for the per-subagent-cost problem.
- **A narrow `PreToolUse`-on-`Agent` validation hook** — not a Read-gate before dispatch (rejected: some rules govern *whether* to delegate, which precedes dispatch, and a gate there would add friction where a skill has already prescribed the target correctly). A hook that inspects only the dispatch's `subagent_type` and `model` and denies a narrow violation (e.g. `general-purpose` with no explicit `model`) is silent whenever a skill has already prescribed the dispatch correctly, so the friction objection does not apply to this narrower shape.
- **Move dispatch mechanics into the dispatching skills** (`subagent-delegation`, `plan-it`, `code-review`) — covers prescribed dispatches only, not ad hoc ones, so it ranks last.

## Sources

- `.claude/plans/claude-md-audience-restructure.md`, the section recommending against the `SessionStart` deferral and assumption-ledger rows 11–14 — the four-reasons-declined argument, the empirical rule-injection test, and the unresolved `SessionStart` premise, as recorded by the plan-authoring session.
- `.claude/plans/claude-md-audience-restructure.md:63` — the rule-count-gating decline and its re-opener condition, recorded verbatim in substance above.
- [Claude Code sub-agents docs](https://code.claude.com/docs/en/sub-agents) — "Explore and Plan are the only subagents that omit CLAUDE.md and git status. There is no frontmatter field or per-agent setting to change which agents skip them," quoted at primary source by the plan-authoring session.
- `claude/.claude/hooks/capture-session-id.sh` — the only one of five `SessionStart` hooks that dual-registers on `SubagentStart` "in addition to" `SessionStart`, cited by the plan as circumstantial (not primary-source) evidence for the unresolved `SessionStart`-for-subagents premise.
