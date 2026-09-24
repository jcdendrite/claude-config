# Restructure global CLAUDE.md into Agent Core and Main session layers (GH-1085 phase 1)

## Context

Goal: reorganize `claude/.claude/CLAUDE.md` into an "Agent Core" layer (rules every agent must follow) and a "Main session" layer (rules only the main session, forks, and a main-like orchestrator need), without changing any rule's wording except where noted.

Why now: Claude Code v2.1.271 added subagent frontmatter `omitClaudeMd: true`, which would cut a large per-dispatch cost in this subagent-heavy harness but would also drop the safety and engineering-judgment rules every agent should follow. Separating the two layers is worth doing even if `omitClaudeMd` is never adopted (engineer, GH-1085). It also makes the later extraction of core into a preloadable skill a mechanical move (GH-1085 phase 3).

Intended outcome: one contiguous Agent Core section and one Main session section, with every bullet placed per the GH-1085 ledger, as challenged and revised in this plan. The source of truth is https://github.com/jcdendrite/claude-config/issues/1085. Its "How to treat these placements" section applies to this plan: every placement, the engineer's included, is a proposal to evaluate. Disagreements go back to the engineer as decision points, never as silent overrides.

Phase 1 scope, from the issue:
- Verbatim moves.
- In-file relocations: worktree-enforcement paths and script-first Bash into core; Code Comments folded under Prose as a "Durable text" subsection.
- One new sentence translating "confirm/ask/stop" into "report back to your dispatcher" for subagents.
- `output-preferences.md`: the issue's `@`-import is deferred to a follow-up. The current conditional-read bullet moves verbatim into Main session. See the answers below and ledger rows 44–46. The engineer confirmed the deferral in round 3 ("yeah, let's make it follow-up work to simplify things.").
- One PR-scope addition: the permission-globs bullet moves to `claude/.claude/rules/settings-json-conventions.md` in this PR rather than a separate one, to stay within the byte gate. A one-line stub stays in Agent Core § Safety (round 3 answers).
- Updating every cross-reference to renamed or merged sections.

## Engineer's answers to plan-architect's open decisions (Step 5, round 1)

Only the quoted label is the engineer's. The option descriptions were proposals from the main session or plan-architect.

- OD1, rows meant for main + authoring agents (installs, package naming, secret commits, least privilege, prove-your-change-caused-a-failing-check): "Agent Core (Recommended)"
- OD2, userEmail bullet: "Agent Core (Recommended)"
- OD3, marker clear-stale bullet: "Main session (Recommended)"
- OD4, walk-through split: "Split as proposed (Recommended)"

Step 4 answers:
- Installs, package naming, secret commits, userEmail: engineer typed "Ask plan-architect to weigh in."
- Attribution: "Core (Recommended)"
- output-preferences: "@~/.claude/ path + touch (Recommended)" (superseded by the round 1 and round 2 answers below; the engineer confirmed the deferral in round 3)
- Byte budget: "Pull glob rule relocation in (Recommended)"

Plan-review round 1 answers:
- Output-preferences import. Engineer typed: "Interesting that all these findings focus on the @ import. I think the ~ resolution problem is solveable by colocating output-preferences to the same folder as CLAUDE.md (think about how the @AGENTS.md pattern works, which is canonical). If there's concerns beyond that, I don't think it's a huge deal if we punt on this because user-provided output preferences really only matter for the main agent."
- Audience signal: "Merge into the translation line (Recommended)"
- Dispatch-denial rule (CLAUDE.md:118): "Move it to Agent Core (Recommended)"
- Glob relocation: "Keep, cite hook + add test (Recommended)"

Plan-review round 2 answers:
- Glob gap through Bash. Engineer typed: "I don't think this is as much of a gap. If there's a rule for settings.json (there is, right? that's what we're leveraging?), then that should already be in the context of the agent that would execute Bash. I'm assuming that if an agent would be mutating settings with Bash, they'd read the file first, which would bring the rule into context." The plan records the gap as accepted on that reasoning, with its two assumptions marked unverified (row 25).
- CLAUDE.md:182 (subagent returns rather than ships): "Move to Agent Core (Recommended)"
- Commit shape: "Two commits (Recommended)"
- Output-preferences. Engineer selected "Check resolution first (Recommended)" and typed: "So (b) is still an issue status quo right? Like status quo subagents still get the instruction, right (c) I thought I said we can remove this problem by having install.sh touch the file. (d) this is a fair point, that's scope creep, but we could migrate pretty easily since CLAUDE.md currently loads from a fixed path." The main session then ran the check (row 45). The rule "import if it resolves, punt if not" was the main session's option text, not the engineer's words, so the deferral needed the engineer's confirmation, which they gave in round 3 (below).

Plan-review round 3 answers:
- Opening line. Engineer typed: "Oh that seems disruptive. I agree with option 1, but check with plan-architect please." Option 1 was the main session's proposal: append "; if it gates an action, hold that action". Plan-architect's consult returned a replacement line that keeps the meaning of option 1 and costs fewer bytes. The engineer then selected "Adopt plan-architect's line (Recommended)". The wording is plan-architect's.
- Glob gaps. Engineer typed: "I'm surprised the out of project Read doesn't load it because the rule should be symlinked like other rules. Double-check that please. The Bash read of settings.json is an unusual case - I don't think agents would do that, BUT it's easy to guard against with a permission deny rule. Ask plan-architect for guidance." The double-check could not run empirically: a scratch config dir has no login, and the only workaround copies credential files. The docs describe path-scoped matching as project-relative and are silent on out-of-project reads. Plan-architect advised against a deny rule. The engineer then selected "Add the stub, no deny rule (Recommended)". The stub text came from plan-architect via the main session.
- Output-preferences. Engineer selected "Test user scope now, then decide". That test could not run either, for the same login reason. The docs say user-scope imports load without the approval dialog (except in Cowork) and that relative imports resolve "relative to the file containing the import", and are silent on whether that means the link's or the target's directory. The deferral stays undecided (Notes for the engineer).
- Forks. Engineer typed: "Yes, move to agent-core if MEMORY is also loaded for subagents. But there's one other problem. The problem is in the opening line. It should say only the main session AND forks follow it. The problem is in the opening line, not the location. And that opening line fix to say this section applies to forks as well is really important. And forks DO often run in this harness." Probes (plan-architect, general-purpose, code-writer and staff-sdet dispatches) each reported the MEMORY.md index in their own context (row 19). So `:23` moves to Agent Core and the opening line names forks.
- Marker clear-stale. Engineer typed: "Yeah definitely agree with plan-architect to keep marker clear-stale in main session."
- Orchestrator. Engineer typed: "You don't need to stub in an orchestrator agent that doesn't exist yet. That's fine. But I DO want to know about all changes you're deferring because of the byte margin." The list is in the decision record and Out of scope.
- Output-preferences check. Engineer typed: "you could set up the scratch config directory and then all I'd have to do is log in". The main session built `/tmp/scratch-import-probe.sh` and the engineer ran it, one run per arm. Symlink arm: `/context` listed the config-dir `CLAUDE.md`, `canary-target-rel.md` and `canary-abs.md`, and the model listed the target-relative and absolute canaries. Real-file control: `/context` listed `canary-link-rel.md` and `canary-abs.md`, and the model listed the link-relative and absolute canaries.
- Forks and the escalation sentence. Engineer typed: "Forks cannot talk to me directly." So the sentence applies to forks unchanged, with no carve-out.
- Byte-margin deferrals. Engineer typed: "Deferred for scope, not bytes - sounds good", then on the byte-driven candidates: "orchestrator naming - I defer based on scope. orchestrator agent doesn't exist yet."; "keeping full permission-globs bullet in CLAUDE.md - I actually think keeping it out and having the sub is better pracitce."; "'(main)' tag on each main-only bullet - I think that's excessive." So no change is deferred for the byte margin.
- Output-preferences deferral. Engineer typed: "yeah, let's make it follow-up work to simplify things." The import is follow-up work.
- Output-preferences file. Engineer typed: "I mean that the output preferences that live in the file, as opposed to the output preferences text in CLAUDE.md, are intended to reflect the user's personal preferences and therefore should be .gitignored." Plan-architect's consult (its words, not the engineer's) read this as personal calibration and recommended the file live outside the repo, at `~/.claude/output-preferences.md`, rather than beside CLAUDE.md even if gitignored. It also advised keeping the import out of phase 1.

Plan-review round 4 answers:
- Fork precedence. The main session proposed that `:182` governs any dispatched agent, forks included, recorded in the decision record and Verification 5 at no CLAUDE.md cost. Engineer typed: "Agreed, I like that."
- Wording. The main session proposed starting the translation sentence "When dispatched, a step aimed at..." (2 bytes shorter than "Inside a subagent"). Engineer typed: "Sounds good."
- The word "fork". Engineer typed: "I don't think "context: fork" skills are ever called forks themselves. they are skills that can be run in forks right? in which case the prose applies to the forks that runs those skills, so we're good here." `docs/skills.md:141-145` does call a `context: fork` run a "fork" (no conversation history, no `AskUserQuestion`). Either kind is dispatched and cannot ask the user, so "When dispatched" and `:182` cover both, and the decision record defines the word.
- PR #714. Engineer typed: "Add a comment on PR 714 indicating that it needs to be modified based on your PR, which will definitely land first." The main session posted that comment on #714.
- Out-of-project Read probe. Engineer typed: "You may, but I deleted the scratch config directory because you said I should. Yo'll need to recreated it." The probe has not been run.
- Fork shipping wording (round 5). CISO found the fork-precedence gap in loaded text. Engineer typed: "I think rewording (option 1) makes sense. What does plan-architect think?" Plan-architect recommended naming forks instead of "a dispatched agent", and the engineer selected "Plan-architect's: 'any fork or subagent' (Recommended)". This supersedes the round-4 answer above, which assumed no CLAUDE.md cost. The wording is plan-architect's.
- Fork residual (round 5). CISO found that the reworded clause leaves three readings that let a fork ship: the fork may not know it is one, the loaded text has no tiebreak against Main session's Shipping bullet, and "on its own" can read as "on its own initiative". Closing them needs about 25–30 bytes against a 5-byte margin, and no hook can tell a fork from the main session. The engineer selected "Accept, record it, rely on post-merge fork check (Recommended)". Verification 6's fork spot-check is the control, and it includes a case where the dispatcher's prompt directs shipping.

## Approach

Split `claude/.claude/CLAUDE.md` into two H1 groups:
- **`# Agent Core`** comes first, is one contiguous block, and opens with a line that states both groups' audiences and translates escalation verbs for subagents.
- **`# Main session`** follows it.

Every bullet moves verbatim. Every `##` name stays, except Code Comments, which becomes `### Durable text` under Prose and Output Format.

Moving the permission-globs bullet to the settings.json rule file, and leaving a 35-byte stub in Agent Core § Safety, pays for the new text. Net result: about −5 bytes and 196 lines, with every CLAUDE.md edit in one commit.

The output-preferences `@`-import is deferred. Line 158's conditional-read bullet moves verbatim into Main session (row 44).

### Notes for the engineer

Recorded from the engineer's round-3 selections:
- **Opening-line wording.** You selected plan-architect's line. The wording is plan-architect's. Your typed words agree with the meaning of the main session's option 1, not with this text. The main session added "and forks" at your direction (net +7 bytes over plan-architect's line, after "When dispatched" saved 2).
- **The new stub.** You selected the stub `` - No globs in `permissions.allow`. `` in Agent Core § Safety. It is new wording beyond phase 1's "one new sentence".

No decision requested:
- **Output-preferences import.** Deferred to a follow-up, as you confirmed ("yeah, let's make it follow-up work to simplify things."). Row 45 records the scratch-test result and Out of scope records the two candidate shapes.
- **Fork shipping precedence.** `:182` names forks (round 5: you selected "Plan-architect's: 'any fork or subagent' (Recommended)", which supersedes your round-4 "Agreed, I like that.").
- **Forks and `:23`.** Settled by your round-3 answer: the opening line names forks, and `:23` moves to Agent Core (row 19, P11).
- **Marker clear-stale (`:21`).** Stays in Main session (row 42, your quote). Plan-architect agrees: `clear-stale` cannot clear a subagent's own leftovers.
- **Orchestrator.** Not named in the opening line, per your answer. Its body must claim Main session (row 48). Open PR #714 adds it, and the main session posted a comment there asking for that change once this PR lands, as you directed.
- **Forks and the escalation sentence.** Forks cannot ask you directly (your round-3 answer), so the sentence applies to them and needs no carve-out.
- **Byte-margin deferrals.** None. Your answers place each candidate elsewhere (Out of scope).
- **Thin byte margin.** The margin is 5 bytes (expected 31,569 against 31,574). If the measured size exceeds 31,574, the implementer stops and reports instead of trimming rule wording (Verification 2).
- **Little line headroom.** The file ends at 196 of 200 lines.
- **A pre-existing hook gap.** Grounding the glob relocation surfaced a gap in `ask-review-permissions.sh`: it asks only for paths containing `.claude/settings`. So a settings file in a `CLAUDE_CONFIG_DIR` whose path has no `.claude/` segment gets no ask (row 25). The gap predates this change; the follow-up is in Out of scope.
- **Glob gaps.** The relocated rule loads on a Read-tool read of an in-project settings file. Gaps (a)-(f) are recorded in row 25 as found by the plan, and the always-loaded stub keeps the prohibition in context for all of them. Your round-2 quote covers only (d). Plan-architect advised against a deny rule for Bash reads of settings files, and none is added (row 58).

### Target layout

Source numbers are current `claude/.claude/CLAUDE.md` lines. Bullets keep their current relative order.

```text
# Agent Core                                  (replaces "# Global Instructions")
<opening line>
## Safety                 5, 6, 7, 8, 9-12, 13, 14-19, 20, the new stub, 23
## Engineering Judgment   28-50
## Working Style          core half of 54 + 55-60, 61, 62, 63, 64, 65, 67, 68-86,
                          116 (worktree Edit/Write paths), 120-127 (script-first),
                          184 (Stopping), 118 (dispatch denial),
                          182 (subagent returns rather than ships; dedented to a top-level bullet, reworded to name forks)
## Prose and Output Format  146, 148-157
### Durable text          (replaces "## Code Comments, Documentation, and Prose")
#### Where to put it      164
#### When to write it and what to include   168, 170-175
# Main session
## Safety                 21, 22
## Working Style          main half of 54, 66, 158
## Code Review            89
## Plan Review            93
## Pre-Handoff Review     97
## Agent Briefing         101-115, 117, 119, 128
## Model & Effort Routing 132-142
## Shipping               179-181, 183
```

Line 24 leaves the file. It is appended verbatim as the last bullet of `claude/.claude/rules/settings-json-conventions.md`. A one-line stub, `` - No globs in `permissions.allow`. ``, takes its place in Agent Core § Safety, after :20. Each heading gets one blank line before and after, including before `## Code Review`, which lacks one today.

These are the only new or changed lines:
- **Opening line**, directly under `# Agent Core` (plan-architect's wording plus "and forks" at the engineer's direction, with "When dispatched" replacing "Inside a subagent"; 264 bytes with its newline, measured with `wc -c`): `Every agent follows Agent Core; only the main session and forks follow Main session. When dispatched, a step aimed at the user or a reviewer (ask, confirm, point, name, raise, defer) means: report it in your return and take no action it gates; stop means: return.`
- **Stub**, in Agent Core § Safety directly after the marker-hand-write bullet (old :20): `` - No globs in `permissions.allow`. `` (35 bytes with its newline, measured with `wc -c`). The full bullet stays verbatim in the rule file.
- **Reworded shipping clause**, the dedented old :182: `- Merge stays human-only; any fork or subagent returns its work to its dispatcher rather than shipping on its own.` It replaces "a dispatched subagent" (21 bytes) with "any fork or subagent" (20 bytes). Round-5 review found that, once :182 leaves the Shipping bullet and the opening line grants forks Main session, nothing in loaded text says a fork is a "dispatched subagent". Naming forks makes the clause take precedence over Main session's Shipping bullets for a fork. It is the only reworded line in the move.
- **Walk-through split**, with sentences unchanged:
  - Main half: `- Walk through your proposed approach and explain tradeoffs before writing code.`
  - Core half: `- When presenting options, evaluate them — state which you'd recommend and why, rather than listing choices without a judgment. Open with the one sentence naming why it's a genuine decision, then the options. Genuine-decision shapes include:`, followed by lines 55-60 unchanged.

About the opening line:
- It merges the audience statement into the translation line, as the engineer chose (row 47).
- It names forks because a fork holds the whole conversation and MEMORY.md and acts for the main session. Engineer: "forks DO often run in this harness." "When dispatched" covers ordinary subagents and forks alike, and `:182` ("any fork or subagent returns its work to its dispatcher rather than shipping on its own") names forks, so a fork returns its work instead of following Main session's commit-and-PR duties.
- The verb list covers the escalation phrases in Agent Core aimed at the user or a reviewer, including "Point the user" (:5) and "name it for the user" (:10). "Mention it separately" (:86) is not in the list; it falls under "a step aimed at the user or a reviewer". The phrase "ask first whether the target can be discovered" (:14) isn't aimed at the user, so it doesn't trigger (row 51).
- "Report it in your return and take no action it gates" separates steps that gate an action from steps that only surface something. For :5, :6, :9-12, :31 and :118 the gated action (the install, the manifest edit, the secret read, the destructive step, continuing after a denial) is withheld. For :33, :73 and :86 the subagent keeps working and lists the item in its return. "Stop means: return" keeps :118 and :184 a real halt. This matches `code-writer.md:15-17` ("name it in your return — do not make it").
- It sits under `# Agent Core`, so it moves with core into phase 3's skill. For the main session it is inert.

### Byte and line budget

| Change | Bytes | Lines |
|---|---|---|
| Glob bullet (L24) out | −319 | −1 |
| `# Global Instructions` → `# Agent Core` | −9 | 0 |
| `## Code Comments, Documentation, and Prose` → `### Durable text` | −26 | 0 |
| Two `###` → `####` | +2 | 0 |
| Opening line | +264 | +1 |
| Stub | +35 | +1 |
| Line 182 dedented, reworded to name forks, and moved to core | −3 | 0 |
| `# Main session` | +15 | +1 |
| Second `## Safety` and second `## Working Style` | +27 | +2 |
| Walk-through split | +2 | +1 |
| Structural blank lines (36 → 43) | +7 | +7 |
| **Net** | **≈ −5** | **+12 → 196** |

Moving lines 118 and 158 changes no byte count. The totals were hand-counted. Three round-2 reviewers reproduced the earlier layout (261-byte opening line, no stub, `:182` still nested) at 31,534 bytes and 195 lines on a scratch copy. This layout adds 3 for the fork-aware opening line (264 bytes against 261) and 35 for the stub, and takes 3 off for the dedent and reword, so the expected result is 31,569 bytes and 196 lines (row 3). Moving `:23` changes no byte count. Round-3 platform, product and SDET reviewers rebuilt the layout without the stub at 31,536 bytes and 195 lines. Verification 2 re-measures it.

The gate compares each commit against HEAD (row 1), and only the glob removal is big enough to offset the opening line and the stub. So every CLAUDE.md edit lands in one commit (row 4). A separate headings-only move commit would grow the file by 18 bytes and be denied.

### Heading scheme: what survives, what must change

- **Tests:** the kept `##` names pass every structural test unchanged.
  - The first `## Working Style` is in core and holds the attribution bullet (row 5).
  - `## Prose and Output Format` appears once, with its bold lead-ins before `### Durable text` (row 6).
  - "Ground every choice" keeps its indentation (row 7).
  - Duplicate headings break no test (row 8).
- **Hooks:** no edits (row 9).
- **Cross-references:** references by name to Agent Briefing, Shipping, Safety, Working Style, Engineering Judgment, and Model & Effort Routing keep resolving. None points at line 118 (rows 10, 11).
- **Edits required:** the Code Comments rename, at the live sites in row 12, each becoming `§Durable text`. README.md:452 also changes, because the output-preferences instruction no longer sits in "Prose and Output Format" (row 53).
- **New test:** a structural test module pins the group contract, the cross-group placements the engineer decided (rows 40–42, 44, 49, and :182 in the round-2 answers), and the prose-backed Safety rules; the pins encode the engineer's core placements, not the absence of a hook.
- **Duplicate headings:** the repeated `## Safety` and `## Working Style` stay until phase 3 moves core into its own file. The decision record accepts that risk (row 52).

### Output preferences: deferred

Line 158 moves verbatim to Main session § Working Style (row 44). Phase 1 makes no import, `install.sh`, `.gitignore`, or `test_skills.py` change. README.md:452 changes to name where the instruction now lives and that it applies to the main session and forks (row 53). Out of scope records the follow-up, starting from the engineer's colocation idea and the symlink check (rows 45, 46). The engineer confirmed the deferral in round 3.

### Design-decision records

Phase 1 adds one record and one supersession line:
- **Supersession line.** `declined-sessionstart-additionalcontext-injection.md:27` still says "No per-agent CLAUDE.md mechanism exists", which is the premise #919 relied on. `omitClaudeMd` makes it false (row 37), and `.claude/rules/design-decisions.md` requires a supersession line in the record itself (row 36). The SessionStart decline itself still stands.
- **New record.** It holds what a later CLAUDE.md editor needs, which today lives only in GH-1085, an external issue:
  - the two-group contract and its opening line
  - the placement tests
  - the accepted duplicate-heading risk
  - the glob relocation, grounded in the hook source and its regression tests, with the always-loaded stub. The stub keeps the prohibition in core, which meets the relocation bar at `docs/cost-levers-considered.md:374` for the prohibition. The rationale relocates for the byte budget. The deny rule for Bash reads of settings files was considered and advised against by plan-architect (row 58)
  - a one-line note that the output-preferences import was deferred
  - forks: named in the opening line, with `:23` in Agent Core because every subagent probed holds the MEMORY.md index (row 19)
  - the byte margin: the engineer deferred the orchestrator naming on scope, preferred the stub to the full glob bullet on practice, and called per-bullet "(main)" tags excessive, so nothing is deferred for bytes (Out of scope)
  - follow-ups for the dangling phrases after verbatim moves: `:184`'s "still" (its Shipping antecedent stays in Main session), `:158`'s "the rules above" (it now sits under Working Style), `:146`'s "the section below" (it now points at a subsection), and the Durable text scope line "This section governs comments and durable docs only" (ambiguous inside Prose)
  - where the deferred follow-ups are tracked
  - the rollback procedure and its trigger (Critical files, Verification 6)

  Unverified claims appear in the record as unverified (rows 13, 25). The compliance sentence says compliance is unmeasured at commit time, and that the post-merge spot-check result goes to GH-1085.

### Assumption ledger

**Root problem:** Every subagent except Explore and Plan loads all of `claude/.claude/CLAUDE.md`. Nothing in the file separates rules every agent needs from rules only the main session needs. So neither a subagent reading it today nor a later `omitClaudeMd` extraction can tell which rules are its own.

**Givens:**
- **G1. How CLAUDE.md loads is vendor-owned.** User-scope CLAUDE.md, its `@`-imports, and `.claude/rules/` reach every subagent except Explore and Plan, unless the agent sets `omitClaudeMd`. The vendor owns loading, so this plan can only arrange content. `[verified: GH-1085 body, Background, citing the sub-agents and memory docs; docs not re-fetched]`
- **G2. `@`-import behavior is vendor-owned, and parts are undocumented.** What happens when an imported file is missing is undocumented. So is how a relative import resolves through a symlinked CLAUDE.md, though the engineer's scratch test shows it resolves against the target's directory (row 45). This plan does not depend on either, but they bound the deferred follow-up. `[verified: memory-doc quotes relayed by this session's exploration; not re-fetched]`

**Mechanisms:**

| Mechanism | Justification | anchors |
|---|---|---|
| Two H1 groups, core first and contiguous, `##` names kept | Tells each reader which rules are its own, and leaves core extractable as one block. Lighter options fail: (a) HTML-comment boundary markers give phase 3 a boundary but give no reader an audience signal, and whether Claude Code strips HTML comments from CLAUDE.md is unchecked; (b) a "(main)" tag on each main-only bullet costs about 40 tags, roughly 300 bytes the budget lacks, and leaves core interleaved | `anchors: root, row5, row6, row8, row13` |
| Opening line: audience statement plus escalation translation | States each group's audience in words, not just a heading, and gives every core escalation verb a subagent meaning | `anchors: row13, row14, row47, row51` |
| Glob bullet → `claude/.claude/rules/settings-json-conventions.md`, with a 35-byte stub kept in Agent Core § Safety | Pays for the new text. The engineer kept the relocation with the hook cited and a test added, then chose the stub. The stub keeps the prohibition always loaded, so the rule file only has to deliver the rationale. A deny rule was considered and not added | `anchors: row4, row23, row24, row25, row50, row57, row58` |
| A `MultiEdit` case in `test_ask_review_permissions.py`, a hook-wiring assertion in `test_hook_alignment.py`, plus a rule-file invariant in the group test | The hook's `MultiEdit` arm has no test today, nothing pins that `settings.json` wires the hook on a matcher spanning Edit, Write and MultiEdit, and nothing pins that the relocated rule still exists with `paths:` covering both settings filenames | `anchors: row24, row25, row50` |
| Durable-text rename and reference updates | The only renamed section | `anchors: row12` |
| `test_global_claude_md_groups.py` | Pins group order, the opening line's contract, and the cross-group placements the engineer decided (`:118`, `:182`, `:21`, `:23`, output-preferences) and the core-only rules, all of which phase 3's extraction depends on | `anchors: row13, row44, row49, row51, root` |
| Decision record plus supersession line | The contract needs an in-repo home, and a load-bearing premise is now false | `anchors: row24, row36, row37, row52` |
| Two commits: CLAUDE.md with its rule file and test, then everything else | The per-commit ratchet forces the CLAUDE.md edits into one commit. Nothing forces more than two, and `main` squash-merges, so finer commits vanish after merge and each costs a `/code-review` run | `anchors: row1, row4, row55` |

**Placement ledger.** Every GH-1085 row is listed below. The "GH-1085" column is `[verified: GH-1085 body, as saved this session]`. The issue body is a file, so it carries no override protection, except for the rows that cite a quote from the engineer.

| # | Rule (current CLAUDE.md line) | GH-1085 placement | Final | Status | anchors |
|---|---|---|---|---|---|
| P1 | No autonomous installs (5) | core. Engineer: code-writer + main | Agent Core | engineer decided (OD1) | `row13, row15, row16, row22, row40` |
| P2 | Name every package (6) | core, same split | Agent Core | engineer decided (OD1) | `row13, row15, row16, row22, row40` |
| P3 | Never commit secrets or binaries (7) | core. Engineer: code-writer + main | Agent Core | engineer decided (OD1) | `row13, row16, row22, row40` |
| P4 | `userEmail` not contact copy (8) | TBD. Engineer: code-writer, maybe a reviewer | Agent Core | engineer decided (OD2) | `row16, row18, row22, row41` |
| P5 | Never Read/cat secret-likely files (9-12) | core | Agent Core | confirmed | `row15, row51` |
| P6 | Least privilege (13) | authoring | Agent Core | changed from authoring; engineer decided (OD1) | `row13, row16, row17, row40` |
| P7 | Discover the destructive target (14-19) | core | Agent Core | confirmed | `row15` |
| P8 | Never hand-write markers (20) | core | Agent Core | confirmed | `row15` |
| P9 | Marker `clear-stale` (21) | not in the issue's tables | Main session | new row; engineer decided (OD3) | `row20, row39, row42` |
| P10 | Trust review-narrative after compaction (22) | main | Main session | confirmed | `root` |
| P11 | MEMORY.md index routes (23) | main | Agent Core § Safety, after the stub | changed; engineer decided (round 3 answers), on the condition that subagents hold MEMORY.md, which the probes confirmed | `row19` |
| P12 | No globs in `permissions.allow` (24) | relocate, in a separate PR | settings-json rule file, in this PR, with a one-line stub in Agent Core § Safety | engineer decided (Step 4); kept with hook citation and test (round 3); stub chosen by the engineer (round 3 answers) | `row23, row24, row25, row50, row57` |
| P13 | Understand intent (28) | core, plus a rewording follow-up | Agent Core; rewording in phase 2 | confirmed | `root` |
| P14 | Actual stack (29) | core | Agent Core | confirmed | `root` |
| P15 | Single source of truth (30) | core | Agent Core | confirmed | `root` |
| P16 | Flag and confirm destructive actions (31) | core, needs translation | Agent Core | confirmed | `row14, row51` |
| P17 | Verify, don't guess (32) | core, plus a rewording follow-up | Agent Core; rewording in phase 2 | confirmed | `root` |
| P18 | Worktree scope constrains writes (33) | core | Agent Core | confirmed | `row51` |
| P19 | Over-powered primitives (34) | core | Agent Core | confirmed | `root` |
| P20 | Structural siblings (35) | core | Agent Core | confirmed | `root` |
| P21 | Wrong foundation (36-41) | core | Agent Core | confirmed | `root` |
| P22 | Prove your change caused a failing check (42) | authoring | Agent Core | changed from authoring; engineer decided (OD1) | `row13, row15, row17, row40` |
| P23 | Extract functions (43) | core | Agent Core | confirmed | `root` |
| P24 | Ground every choice (44-50) | core | Agent Core | confirmed | `row7` |
| P25 | Walk through approach (54-60) | split. Engineer: authoring, maybe core | core half + Main session half | engineer decided (OD4) | `row13, row14, row43` |
| P26 | Be precise (61) | core | Agent Core | confirmed | `root` |
| P27 | Attribute to the engineer (62) | core, unresolved in the issue | Agent Core | engineer decided (Step 4) | `row21, row5` |
| P28 | Compounding defensive layers (63) | core | Agent Core | confirmed | `root` |
| P29 | Check the environment (64) | core, plus a merge follow-up | Agent Core; merge in phase 2 | confirmed | `root` |
| P30 | Descriptive names (65) | core | Agent Core | confirmed | `root` |
| P31 | Default-consider delegation (66) | main | Main session | confirmed | `row13` |
| P32 | Locate before a whole-file read (67) | core | Agent Core | confirmed | `root` |
| P33 | Scope discipline (68-86) | core, needs translation | Agent Core | confirmed | `row14, row51` |
| P34 | Code Review / Plan Review / Pre-Handoff Review (89, 93, 97) | main + orchestrator | Main session | confirmed | `row20` |
| P35 | Worktree-enforcement Edit/Write paths (116) | core | Agent Core § Working Style | confirmed | `row15` |
| P36 | Script-first Bash (120-127) | core, under Working Style | Agent Core § Working Style | confirmed | `row15` |
| P37 | Other Agent Briefing bullets (101-115, 117, 119, 128) | main | Main session | confirmed | `row13` |
| P37a | Dispatching cannot clear a denial (118) | main, as one of "all other bullets" | Agent Core § Working Style, after Stopping | changed; engineer decided (round 3) | `row10, row15, row49` |
| P38 | Model & Effort Routing (132-142) | main, plus a claude-config exception | Main session; the exception goes to a separate PR | confirmed | `row13` |
| P39 | Prose section scope line (146) | core | Agent Core | confirmed | `row6` |
| P40-44 | Lead with the answer / Shape / Cut / One idea / Active voice (148-157) | core | Agent Core | confirmed | `row6` |
| P45 | output-preferences (158) | main: `@`-import, touch, symlink | Main session § Working Style, verbatim conditional read; import deferred | deferred by the main session under the engineer's stated condition; engineer confirmed (round 3 answers) | `row44, row45, row46, row54` |
| P46 | Fold Code Comments under Prose as "Durable text" | fold | `### Durable text` | confirmed | `row12` |
| P47 | Place prose where reader and altitude match (164) | core | Agent Core | confirmed | `root` |
| P48 | Durable-text rules (168, 170-175) | core | Agent Core | confirmed | `root` |
| P49 | Autonomous shipping (179-181) | main | Main session | confirmed | `row11` |
| P49a | Subagent returns its work rather than shipping (182) | main, as a sub-bullet of autonomous shipping | Agent Core § Working Style, after :118, dedented | changed; engineer decided (round 2); reworded to name forks, engineer decided (round 5) | `row11, row56, row59` |
| P50 | Update the PR body (183) | main | Main session | confirmed | `row11` |
| P51 | Stop when genuinely blocked (184) | core, subagent escalates | Agent Core § Working Style | confirmed | `row11, row14` |

**Assumption rows:**

| # | Assumption | Tag |
|---|---|---|
| 1 | Outside merge and rebase, the length gate compares each commit's staged CLAUDE.md against HEAD. It denies growth once the file is over 25,600 bytes or 200 lines | `[verified: claude/.claude/hooks/_lib.sh:1616-1619, :1700-1762, :846-885 (the diff base is empty outside an in-progress state); limits from this session's exploration of check-claude-md-length.sh, not reopened]` |
| 2 | The branch base is 184 lines and 31,574 bytes | `[verified: 184 lines read this session; `wc -c -l` → `184 31574`, run on this worktree by the round-3 platform review]` |
| 3 | The target layout nets about −5 bytes and ends at 196 lines, expected 31,569 bytes | `[verified: three round-2 reviewers rebuilt the earlier layout on a scratch copy and measured 31,534 bytes and 195 lines, with the 261-byte opening line, no stub and :182 nested; this layout adds 3 for the 264-byte opening line and 35 for the stub (both lines measured with wc -c) and takes 3 off for the :182 dedent and reword. Round-3 platform, product and SDET reviewers rebuilt it without the stub at 31,536 bytes and 195 lines. Verification 2 re-measures]` |
| 4 | Only the glob removal (−319) offsets the opening line (+264) and the stub (+35). So every CLAUDE.md change lands in one commit, and a headings-only move commit (+18: −9 −26 +2 +15 +27 +2 +7) would be denied | `[verified: follows from row 1 and the budget table]` |
| 5 | `test_nudge_answer_provenance.py` takes the first `## Working Style` section, up to the next `## ` heading, and requires the attribution bullet there | `[verified: claude/.claude/hooks/tests/test_nudge_answer_provenance.py:63-69, :158-163]` |
| 6 | `test_output_preferences_layering.py` requires exactly one `## Prose and Output Format\n`, with at least one bold lead-in before the next heading of any level. It does not require the output-preferences line there. README's first `"…" section` quote must stay "Prose and Output Format" | `[verified: claude/.claude/hooks/tests/test_output_preferences_layering.py:32-69, :90-115]` |
| 7 | The doc-count scan runs from `- **Ground every choice.**` to the next line that starts with `- ` or `#` | `[verified: claude/.claude/hooks/tests/test_doc_counts.py:149-189]` |
| 8 | Nothing depends on "Global Instructions" or on section names being unique. The citation test resolves duplicate headings. The only citation-grammar reference into this file is branch-management's `§ "Agent Briefing"`, which survives | `[verified: grep; claude-skills/skills/tests/test_skills.py:3807-3813; claude-skills/skills/branch-management/SKILL.md:97]` |
| 9 | Hooks that quote CLAUDE.md name sections or bullets that survive: deny-no-op-dispatch.sh:54,127; nudge-answer-provenance.sh:26; advance-past-commit-stall.sh:104,225; ask-new-dependency-disclosure.sh:6,172,237 | `[verified: grep]` |
| 10 | Every "Agent Briefing" reference outside CLAUDE.md points at bullets that stay there: isolation, no-op dispatch, parallel dispatch. Nothing outside CLAUDE.md cites line 118 | `[verified: README.md:305, plan-it/SKILL.md:83, branch-management/SKILL.md:97, _lib.sh:2463, test_lib_worktree_collision_guard.py:453, test_deny_no_op_dispatch.py:185-249; grep "cannot clear a denial" matches only CLAUDE.md]` |
| 11 | "Shipping" references cite the autonomous-shipping bullet or the subagent-doesn't-ship clause. The autonomous-shipping bullet stays in Shipping; the clause (:182) moves to Agent Core (P49a), so a citation of it by section name now points at Working Style. None cites Stopping. `advance-past-commit-stall.sh:104` cites the clause as "CLAUDE.md's Shipping section" and needs the follow-up in Out of scope | `[verified: advance-past-commit-stall.sh:104,225; test_advance_past_commit_stall.py:672; docs/commit-stall-block.md:69; docs/worktree-bash-guard.md:74]` |
| 12 | Live references to "Code Comments, Documentation, and Prose", ten sites in five files: README.md:244; code-review/SKILL.md:71,130,283; plan-it/SKILL.md:71; code-writer.md:70-71,116-117; comment-discipline-reviewer.md:5,9-10,41-42 | `[verified: grep, excluding .claude/plans/, CHANGELOG.md, docs/reports/, docs/case-studies/]` |
| 13 | The opening line tells every subagent that still loads the full file (code-writer, plan-architect, general-purpose, and the staff-* reviewers) that Main session isn't its own, forks excepted | `[unverified: whether subagents comply is unmeasured; Verification 6 spot-checks it after merge and records the result in GH-1085]` |
| 14 | A dispatched subagent can't stop mid-run to ask the user. It returns once | `[unverified: GH-1085's claim; plan-architect's own tool set is consistent with it but isn't general evidence]` |
| 15 | Seven staff-* agents and ciso-reviewer hold Bash and Write. code-writer holds Bash, Edit, and Write. plan-architect holds Read, Grep, and Glob. No custom agent holds Skill, Task, or Agent | `[verified: claude/.claude/agents/*.md tools lines]` |
| 16 | Hook backing: installs have `deny-network-installs.sh`; package naming has `ask-new-dependency-disclosure.sh`, which only asks and doesn't check rationale; secret commits have `deny-pii-in-commits.sh`, but large binaries are prose-only. `userEmail`, least privilege, discover-target, and MEMORY.md are prose-only | `[verified: this session's exploration; .claude/plans/claude-md-audience-restructure.md:45 for the ask-only, no-rationale finding]` |
| 17 | `ciso-reviewer` partly covers least privilege, with no provisioning-guidance bullet. No staff-* agent covers merge-base reproduction | `[verified: exploration of ciso-reviewer.md:41,55,60 and the staff-* bodies; not reopened]` |
| 18 | Subagents receive the `userEmail` context | `[verified: present in this plan-architect dispatch's own context]` |
| 19 | Subagents receive the MEMORY.md index, not only forks | `[verified: plan-architect, general-purpose, code-writer and staff-sdet dispatches each reported the index in their own context; GH-1085's Background said non-fork subagents never receive it, which this contradicts]` |
| 20 | Only main and general-purpose run skills, and a `claude` agent type may also activate bypass markers (`_lib.sh:3163-3165`; the round-4 CISO and platform reviews read it as design intent, not observed behavior). CLAUDE.md:20 names general-purpose as the agent that carries Skill | `[verified: row 15; CLAUDE.md:20]` |
| 21 | Attribution bullet → core | `[engineer-verified: "Core (Recommended)"]` |
| 22 | The installs, package-naming, secret-commit, and `userEmail` rows come back as plan-architect recommendations | `[engineer-verified: "Ask plan-architect to weigh in"]` |
| 23 | The glob bullet leaves CLAUDE.md in phase 1 | `[engineer-verified: "Pull glob rule relocation in (Recommended)"]` |
| 24 | `ask-review-permissions.sh` asks on every Edit, Write, or MultiEdit whose `file_path` ends in `.claude/settings*.json`. It asks whether or not the file exists, and whatever the session's working directory. #919 kept the glob bullet in CLAUDE.md because a permission rule is composed before any settings file is opened | `[verified: claude/.claude/hooks/ask-review-permissions.sh:21-33; .claude/plans/claude-md-audience-restructure.md:47]` |
| 25 | After relocation, these gaps remain. The main session found them. The engineer's round-2 answer (quoted in the answers above) addressed only (d), on the premise that an agent would Read the file first, and round-3 one-trial probes did not support that premise (tags below). The always-loaded stub (row 57) keeps the prohibition itself in context for every gap: (a) advice given without any settings file being opened or created, which neither the rule nor the hook reaches; (b) a Write that creates a new settings file gets the hook's generic ask, but may not get the rule's guidance before the content is written; (c) a settings file under a config directory whose path has no `.claude/` segment gets the rule on Read but no ask; (d) a Bash-mediated write (`jq`, `sed -i`, `tee`) gets no ask, because the hook is registered for Edit, Write and MultiEdit only, and gets the rule only if the agent used the Read tool on the file first (a Bash read loaded no rule in a one-trial subagent probe); (e) the hook is fail-open when `_lib.sh` cannot be sourced, so a consumer who pulls without re-running `install.sh` after a hook-file addition loses it; (f) the rule did not load on a Read-tool read of a settings file outside the session's project, such as `~/.claude/settings.json`, in one-trial subagent probes, and the docs describe path matching as project-relative and are silent on out-of-project files. The rule loads in a subagent after a Read of an existing matching file inside the project. "Backstop" for the hook means Edit, Write and MultiEdit only | `[verified: ask-review-permissions.sh:14-17, :21-24, :29; claude/.claude/settings.json:334; a round-2 CISO probe of a /tmp copy of the hook showing Edit and Write ask while Bash writes pass; docs/design-decisions/declined-sessionstart-additionalcontext-injection.md:17 for the Read path]` `[verified: round-3 CISO and platform probes, one trial per case, run in subagents: a Read-tool read of an out-of-project settings.json and a Bash read of an in-project one loaded no rule, while in-project Read controls loaded it]` `[unverified: that Edit requires a prior Read, which the engineer assumed; whether the rule loads on a Write that creates a new file; the out-of-project result beyond one trial each, because the main session's re-check could not run without a login in a scratch config dir]` |
| 26 | CLAUDE.md imports `@~/.claude/output-preferences.md`, and `install.sh` creates the file. Not implemented in phase 1 (rows 44, 45). The engineer confirmed the deferral in round 3 | `[engineer-verified: "@~/.claude/ path + touch (Recommended)"]` |
| 27–35 | Withdrawn with the import (row 44). Their questions move to the Out-of-scope follow-up. Row 30's mechanism was also wrong: `install.sh` creates `~/.claude` as a real directory before stowing, so only a leftover top-level `~/.claude` symlink from the old `stow --adopt` setup would put the file in the repo | `[verified: the round-3 platform review reproduced this in a scratch stow tree; not re-run here]` |
| 36 | The design-decisions rule requires a supersession line in the overturned record itself, and a bare-date provenance line for new records | `[verified: .claude/rules/design-decisions.md]` |
| 37 | The declined-SessionStart record states "No per-agent CLAUDE.md mechanism exists", which `omitClaudeMd` falsifies | `[verified: docs/design-decisions/declined-sessionstart-additionalcontext-injection.md:27; GH-1085 body, Background]` |
| 38 | After phase 1, core is about 19.2 KB and main about 12.3 KB | `[verified: the round-3 platform reviewer measured 18,771 and 12,765 bytes on the layout without the stub, with a 265-byte opening line; the stub adds 35, the opening line is 1 byte shorter (264), and the :182 reword saves 1, and moving :23 (418 bytes, measured with wc -c) shifts 418 from main to core; Verification 2 measures the final split]` |
| 39 | GH-1085's Safety table has no row for CLAUDE.md:21 | `[verified: GH-1085 body, Safety table]` |
| 40 | Rules meant for main + authoring agents go to Agent Core: installs, package naming, secret commits, least privilege, and "prove your change caused a failing check" | `[engineer-verified: "Agent Core (Recommended)"]` |
| 41 | The `userEmail` bullet goes to Agent Core | `[engineer-verified: "Agent Core (Recommended)"]` |
| 42 | The marker clear-stale bullet goes to Main session. The engineer reconfirmed it in round 3 after plan-architect advised keeping it there (`clear-stale` cannot clear a subagent's own leftovers, `marker.sh:729-733`) | `[engineer-verified: "Main session (Recommended)"]` |
| 43 | The walk-through bullet splits: the interactive half goes to Main session, and the evaluate-and-recommend half goes to Agent Core | `[engineer-verified: "Split as proposed (Recommended)"]` |
| 44 | The main session defers the output-preferences import in phase 1. Line 158's conditional read moves verbatim into Main session. The engineer's statement is conditional ("if there's concerns beyond that"), and the main session, not the engineer, judged the condition met. Row 45 now shows the resolution concern is gone, so the deferral rests on the remaining concerns and on phase 1's scope (row 54). The engineer confirmed the deferral in round 3 | `[engineer-verified: "If there's concerns beyond that, I don't think it's a huge deal if we punt on this because user-provided output preferences really only matter for the main agent."]` |
| 45 | Concerns, after the engineer's round-2 corrections: (a) resolved: the engineer's scratch test at user scope showed that a symlinked CLAUDE.md follows `@`-imports, that a relative import resolves against the symlink target's directory, and that an absolute import loads, while a real-file control loaded its sibling import. The earlier project-scope non-load is consistent with the external-import approval dialog the docs describe, and the user-scope result shows the symlink is not the cause. `@~/...` is untested; (b) the engineer noted subagents already get the instruction today, so an import is no regression, and this is dropped; (c) missing-target behavior is undocumented, and the engineer's `install.sh` touch covers fresh installs, leaving only a pull without re-running `install.sh`; (d) changing the path orphans existing files, which the engineer called scope creep but easy to migrate; (e) cross-account sharing is dropped, because GH-1085 records the engineer saying per-account divergence isn't intended | `[verified: /tmp/importtest.sh, run this session with claude -p: a real CLAUDE.md with @pref.md loaded its import; a symlinked CLAUDE.md loaded its body but neither its relative nor its absolute import, one run per case, from the model's own report of its loaded context; the memory docs say relative imports "resolve relative to the file containing the import" and are silent on symlinks and on missing files]` `[verified: round-3 SDET reruns with claude -p, two per arm; the memory docs' "Import additional files" section, as quoted by the round-3 CISO review and by a subagent that fetched it]` `[verified: the engineer's scratch-config test at user scope, /tmp/scratch-import-probe.sh, one run per arm, read from /context and the model's report]` `[unverified: @~/... resolution; missing-target behavior; that the approval dialog, not something else, explains the project-scope non-load]` |
| 46 | The follow-up weighs colocating the file beside CLAUDE.md, the `@AGENTS.md` pattern (the engineer's idea), against an `@~/` import of a file outside the repo (plan-architect's recommendation, given that the file is personal and untracked) | `[engineer-verified: "I think the ~ resolution problem is solveable by colocating output-preferences to the same folder as CLAUDE.md (think about how the @AGENTS.md pattern works, which is canonical)"]` |
| 47 | The opening line states both groups' audiences as well as the subagent translation | `[engineer-verified: "Merge into the translation line (Recommended)"]` |
| 48 | The opening line names only the main session and forks. GH-1085 defines main as "the main session and the planned review-orchestrator agent". A future orchestrator agent must claim Main session in its own body, and the engineer deferred naming it on scope: "orchestrator naming - I defer based on scope. orchestrator agent doesn't exist yet." | `[unverified: the orchestrator agent exists only in open PR #714, not on `main`; the orchestrator was left out on scope, per the engineer; naming it would also cost about 25–30 bytes against a 5-byte margin]` |
| 49 | CLAUDE.md:118 goes to Agent Core | `[engineer-verified: "Move it to Agent Core (Recommended)"]` |
| 50 | The glob relocation stays. Rows 24–25 cite the hook source, and a regression test is added | `[engineer-verified: "Keep, cite hook + add test (Recommended)"]` |
| 51 | The opening line's verb list covers every escalation phrase in Agent Core: :5 "Point the user"; :6 "get explicit confirmation" and "handing the command to the user"; :9-12 "ask them" (twice) and "name it for the user"; :31 "confirm the approach"; :33 "Defer to a human"; :73 "Raise to the reviewer"; :86 "mention it separately", which is aimed at the user; :118 "and stop"; :184 "Stopping". The phrase at :14, "ask first whether the target can be discovered", isn't aimed at the user. The non-gating ones (:33, :73, :86) must leave the subagent working. :86's "mention" is covered by "a step aimed at the user or a reviewer", not by the verb list | `[verified: CLAUDE.md lines read this session; Verification 5 re-walks them]` |
| 52 | Duplicate `## Safety` and `## Working Style` names last until phase 3 moves core into its own file. Until then, a `§ "Safety"` citation could point at either, and `:118`'s "Safety's marker bullet" is ambiguous because Main Safety also holds a marker bullet (:21). Both are accepted | `[verified: the citation resolver builds a set of headings, test_skills.py:3807-3813]` |
| 53 | README.md:452 says the output-preferences instruction lives in the "Prose and Output Format" section. After :158 moves to Main session that is false. The README sentence must keep `"Prose and Output Format" section` as its first quoted section phrase, because `test_output_preferences_layering.py` reads that quote as the heading to check, and repointing it at `## Working Style` would fail on the duplicate heading | `[verified: README.md:452 as read by the round-2 platform, product and ai-instruction reviewers; test_output_preferences_layering.py:78-86, :90-115]` |
| 54 | The output-preferences deferral now rests on scope and the remaining concerns, not on resolution, because row 45's test shows imports work through the symlink. The engineer confirmed it in round 3 | `[engineer-verified: "yeah, let's make it follow-up work to simplify things."]` |
| 55 | `main` squash-merges: the last 200 subjects on `main` all end in `(#N)` and there are no merge commits. Each `git commit` needs its own `/code-review` marker, and `require-architect-consult.sh` counts distinct (HEAD, staged-diff) states per branch, capped at 2 outside a live plan-review or ready-for-review fan-out | `[verified: round-2 platform reviewer's git log check and reading of require-code-review.sh and require-architect-consult.sh:80-123]` |
| 56 | `:182` ("any fork or subagent returns its work to its dispatcher rather than shipping on its own", reworded in round 5 from "a dispatched subagent") addresses subagents and, since round 5, forks. Only `code-writer.md:21` restates it, in its own words. The review-only agents are also blocked from `git commit` and `git push` by `deny-reviewer-tree-mutation.sh`. The reviewers and `general-purpose` have no other prose backing for it. `block-gh-pr-merge.sh` blocks `gh pr merge` shapes for every agent, with documented gaps | `[verified: round-2 product and CISO reviewers reading agents/*.md and settings.json:266]` |
| 57 | The stub `` - No globs in `permissions.allow`. `` sits in Agent Core § Safety after the marker-hand-write bullet. The prohibition is then always loaded, and the rule file delivers the rationale and the exact-match alternative | `[engineer-verified: "Add the stub, no deny rule (Recommended)"]` |
| 58 | A permission deny rule for Bash reads of settings files is not added. A narrow one (`Bash(cat *settings.json)`) misses `settings.local.json`, `sed`, `jq`, `head` and `grep`. A broad one also blocks `git diff` on settings paths and any `git commit -m` that names the file. It teaches the agent nothing, and it does not reach an out-of-project `~/.claude/settings.json` | `[verified: plan-architect's round-3 consult, reading review-permissions/REFERENCES.md:78-80 and settings.json; not probed]` `[unverified: that Bash permission patterns accept a leading *; that an ask hook's permissionDecisionReason reaches the model and not only the user]` |
| 59 | The reworded `:182` clause names forks. A CISO finding (round 5) showed that after the dedent and the opening line's fork grant, no loaded text said a fork is a "dispatched subagent", so a fork could read Main session's autonomous-shipping bullet as its own. Prose cannot stop a prompt-injected fork, and no gate can tell a fork from the main session. `require-ready-for-review.sh` and `require-code-review.sh` gate `gh pr create`, `git push` and `git commit` on review state for every caller, with documented bypass shapes and a session-agnostic marker read, so they pass a fork that follows a main-session review at HEAD. Prose is the only fork control. An `agent_type`-keyed deny would cover non-fork subagents only. That gap predates this change and is a follow-up | `[engineer-verified: "Plan-architect's: 'any fork or subagent' (Recommended)"]` `[verified: CISO round-5 finding; plan-architect's round-5 consult]` `[verified: a round-5 CISO probe of a /tmp copy of require-ready-for-review.sh: gh pr create denied without a marker for every agent_type, allowed with a session-agnostic marker at HEAD]` `[unverified: whether the harness tells a fork it is a fork]` |

## Critical files

Two `code-writer` dispatches, run in parallel in the shared feature worktree, with no file in common:
- Dispatch 1 owns the CLAUDE.md change, the rule file, and the test that pins them.
- Dispatch 2 owns the renamed-reference sites, the hook regression test, and the records.

Neither reads the other's output. Dispatch 2's references name `§Durable text`, which no test resolves.

The parent runs the scoped suite, the compression-diff audit and the reference greps once, after both dispatches return (Verification 1, 4, 7, 8). While Dispatch 1 is mid-edit, the old heading still matches in CLAUDE.md and a half-applied file could fail tests, so each dispatch runs only checks on its own files.

The parent commits in this order:
- **A:** Dispatch 1's three files, as one commit, because the gate requires it (row 4). `/code-review` runs on A's staged diff.
- **B:** everything else: Dispatch 2's files (five rename files, README.md, CHANGELOG.md, the hook test, the two decision-record files) and the plan file, `.claude/plans/claude-md-agent-core.md`. B comes after A, so no commit cites `§Durable text` before the heading exists.

Two commits, not four: `main` squash-merges, so finer commits vanish after merge, and each `git commit` needs its own `/code-review` marker, with the round-3 consult gate counting distinct review states per branch (row 55). If a `/code-review` fix cycle on A moves the branch to two distinct states, expect the consult gate to bite on B.

Rollback: after merge, A and B are one squash commit on `main`. Roll back by reverting that squash commit, which removes the CLAUDE.md change, the rule-file bullet, the renames, the README and CHANGELOG edits, the hook test, and the decision records together. Do not restore only some paths: the decision record and the "Partially superseded" line would otherwise describe a contract that no longer exists. The revert grows CLAUDE.md by about 5 bytes while the file is over 25,600 bytes, so committing a hand-restored tree through `git commit` would be denied by the length ratchet. Plain `git revert <sha>` is not intercepted, because `check-claude-md-length.sh:87` and `require-code-review.sh:66` match only the `commit` subcommand. Round-3 and round-4 reviews observed that `git revert -n` followed by `git commit` passes the length gate but is denied by `require-code-review.sh` until `/code-review` records a marker on the reverse diff, and that plain `git revert <sha>` is intercepted by neither gate. Reverting the squash commit will probably conflict in `CHANGELOG.md`: resolve it and revert the whole commit, never restore paths one by one. The length gate's revert handling lives in `_lib.sh`, which this round did not reopen. Since `claude/.claude/**` goes live on `git pull`, a revert goes live the same way.

### Dispatch 1: CLAUDE.md groups

| Path | Action |
|---|---|
| `claude/.claude/CLAUDE.md` | Apply the Target layout and the new lines from Approach, including the reworded :182 clause. Reword nothing else |
| `claude/.claude/rules/settings-json-conventions.md` | Append CLAUDE.md:24 verbatim as the last bullet |
| `claude/.claude/hooks/tests/test_global_claude_md_groups.py` | **Create.** Tests, each with a one-line docstring and an explanatory assertion message (match `test_global_claude_md_email_redaction.py`). Anchor every group position on the line equal to `# Main session`, not a substring, because the opening line also contains "Main session". (1) the file's H1 lines are exactly `# Agent Core`, then `# Main session`, and the `##` headings under each match the Target layout; (2) the line containing `output-preferences.md` sits after the `# Main session` line; (3) the lead-ins of these bullets sit before `# Main session`: `**Dispatching cannot clear a denial your child inherits.**`, and the `:182` clause `any fork or subagent returns its work to its dispatcher rather than shipping on its own`; (4) the opening line appears once, between `# Agent Core` and the first `## ` heading, and contains each of `Every agent follows Agent Core`, `only the main session and forks follow Main session`, `When dispatched,`, `(ask, confirm, point, name, raise, defer)`, `report it in your return`, `take no action it gates`, and `stop means: return`; (5) table-driven: a distinctive substring of each prose-backed Agent Core rule sits before `# Main session`. The rules are no autonomous installs (:5), package naming (:6), secret commits (:7), `userEmail` (:8), the credential gate (:9-12), least privilege (:13), discover-the-target (:14-19), marker hand-writes (:20), the `No globs in` stub, the MEMORY.md guard (:23), prove-your-change-caused-a-failing-check (:42) and attribution (:62), plus the destructive-action confirm rule (:31) and Stopping (:184). Each pinned substring must occur exactly once in the file (assert `count == 1` before comparing its position) or be a bold lead-in or the bullet's first words, because short common words such as `install` or `secrets` survive a bullet move; (6) the `clear-stale` bullet (:21) sits after the `# Main session` line; (7) `claude/.claude/rules/settings-json-conventions.md` still contains the relocated glob guidance, and the parsed `paths:` list in its frontmatter contains both `**/settings.json` and `**/settings.local.json`. A substring check on the whole file would pass even after a filename left `paths:`, because the body names both files; `test_rules_frontmatter.py:340-361` is the parsing precedent |

**Reuse:** the new module copies `test_output_preferences_layering.py`'s `from helpers import CLAUDE_DIR` import and its `REPO_ROOT` setup. Parse the rule file's frontmatter with the in-directory `parse_frontmatter` (`test_agent_roster.py:16`), and resolve the `# Main session` line in a helper that asserts it exists exactly once. Cite `GH-1085` bare in the module docstring, with no phase qualifier: `test_ticket_reference_discipline.py` rejects a tracker ID followed by `Phase` or `Step` and a number, and it reads tracked files only, so the failure would appear after commit A stages the file. Name the invariant instead: core is extractable as one block.

**Self-check before returning:** run `diff <(git show HEAD:claude/.claude/CLAUDE.md | LC_ALL=C sort) <(LC_ALL=C sort claude/.claude/CLAUDE.md)` and include its full output in the return. It must match Verification 3's list.

**Verification for Dispatch 1:** steps 2, 3 and 5 below, plus the new test module on its own. The parent runs step 1.

### Dispatch 2: references, hook test, records

| Path | Action |
|---|---|
| `README.md` | At :244, change `§Code Comments, Documentation, and Prose` to `§Durable text` |
| `claude-skills/skills/code-review/SKILL.md` | Same rename at :71, :130, :283 |
| `claude-skills/skills/plan-it/SKILL.md` | Same rename at :71 |
| `claude/.claude/agents/code-writer.md` | Same rename at :70-71 and :116-117 (the name is hard-wrapped across lines) |
| `claude/.claude/agents/comment-discipline-reviewer.md` | Same rename at :5 (frontmatter), :9-10, :41-42 |
| `README.md` (second edit) | At :452, name where the output-preferences instruction now lives (Main session § Working Style) and say it applies to the main session and forks. Keep `"Prose and Output Format" section` as the first quoted section phrase in that paragraph (row 53) |
| `CHANGELOG.md` | Add an `[Unreleased]` / `Changed` entry (precedent `CHANGELOG.md:50`, `:68`) naming the consumer-visible changes: subagents other than forks are told to skip Main session; the shipping clause names forks (any fork or subagent returns its work instead of shipping); the `# Global Instructions` heading is now `# Agent Core`, `## Code Comments, Documentation, and Prose` is now `### Durable text`, and `## Safety` and `## Working Style` each appear twice; the glob rule's rationale now loads on a Read-tool read of a settings file inside the project instead of always, and a one-line stub stays always loaded; the output-preferences read instruction is main-session-only |
| `claude/.claude/hooks/tests/test_ask_review_permissions.py` | Add one `multiedit_input(...)` case (the helper is at `helpers.py:519`) for a `.claude/settings.json` path to `test_settings_edits_ask`'s parametrization, so dropping `MultiEdit` from the hook's `case` arm fails a test. Add no other case: the existing `write-settings` case already covers a nonexistent path, and the hook reads only `tool_name` and `file_path`, never content, existence or working directory. Put one wiring assertion in `test_hook_alignment.py`, beside `_pretooluse_matcher_groups_for` and reusing it (open PR #744 changes how `settings.json` is produced, so a second parser would go stale), asserting that `claude/.claude/settings.json` registers `ask-review-permissions.sh` on a PreToolUse group whose matcher spans Edit, Write and MultiEdit. The hook is `informational`, so the alignment test's gate-only registration check does not cover it |
| `docs/design-decisions/global-claude-md-agent-core-and-main-session-groups.md` | **Create.** Top: H1, a blank line, `*2026-09-23.*`. Cite rules by their bold lead-in or first words, not by line number, because the line numbers in this plan are pre-restructure. Content, in order: (1) why: `omitClaudeMd` plus `skills:` preload means core must be one block that can be extracted as-is; (2) the contract: two H1 groups, core first and contiguous, `##` names kept, Durable text; for the opening line, cite it at `claude/.claude/CLAUDE.md` rather than restating its verb list; (3) the placement tests: tool capability, not likelihood; a prose-only rule whose trigger can arise in a subagent goes to core; a rule meant for code-writer or plan-architect goes to core because the opening line tells subagents Main session isn't theirs; a rule addressed to subagents goes to core (`:118`, `:182`). Subagent compliance is unmeasured at the time of writing (row 13); the post-merge spot-check result is recorded in GH-1085. The opening line names forks, so a fork follows Main session too, and `:23` sits in Agent Core because every subagent probed holds MEMORY.md (row 19). The orchestrator is not named in the opening line (row 48); (4) the accepted duplicate-heading risk until phase 3, including `:118`'s ambiguous "Safety's marker bullet" (row 52); (5) the glob relocation: it partly reverses #919. The stub keeps the prohibition always loaded, which meets the relocation bar at `docs/cost-levers-considered.md:374` for the prohibition, and the rationale relocates for the byte budget. Record the deny rule as considered and advised against by plan-architect (row 58), not as the engineer's decision. Cover the hook matcher it relies on (`ask-review-permissions.sh:21-33`, Edit, Write and MultiEdit only, fail-open when `_lib.sh` is unreadable), the `MultiEdit` test, the rule-file invariant test, and the gaps (a)-(f) in row 25 as gaps the plan found, of which the engineer's round-2 answer covered only (d), including "advice given without any settings file being opened or created" and "Bash-mediated writes". Record that a Read-tool read outside the project and a Bash read inside it loaded no rule in one-trial probes, mark "Edit requires a prior Read" as unverified, and say the hook tests do not cover load timing; (6) the output-preferences deferral, with the symlink-import result (row 45: user scope, one run per arm, `@~/` untested) and its phase-3 consequence (a colocated relative import of core resolves inside the repo), the four dangling phrases (`:184` "still", `:158` "the rules above", `:146` "the section below", and the Durable text scope line "This section governs comments and durable docs only") as phase 2 follow-ups, and where the follow-ups are tracked (the parent supplies the tracker IDs once filed; until then write "not yet filed"); (7) forward pointer: `advance-past-commit-stall.sh:104` cites the `:182` clause as "CLAUDE.md's Shipping section", which is now Working Style; (8) the rollback procedure and its trigger, from Critical files and Verification 6; (9) the byte margin: nothing is deferred for it. The engineer deferred the orchestrator naming on scope, preferred the stub to the full glob bullet on practice, and called per-bullet "(main)" tags excessive. Any later CLAUDE.md wording growth needs an equal trim (phase 2); (10) forks: the "any fork or subagent returns its work to its dispatcher rather than shipping on its own" clause names forks, so a fork returns its work instead of following Main session's commit-and-PR duties. Quote the clause, and record that it is the one line in the move whose wording changed. Define "fork" as a dispatched run that either inherits the parent's conversation or, for a `context: fork` skill, receives none; neither can ask the user; (11) the stub is a fragment on purpose: the exact-match alternative lives in the rule file; (12) the review orchestrator in open PR #714 must claim Main session in its body. Cite GH-1085 for the full placement table instead of restating it |
| `docs/design-decisions/declined-sessionstart-additionalcontext-injection.md` | Under the provenance line, add one `**Partially superseded by [...](global-claude-md-agent-core-and-main-session-groups.md) (2026-09-23):**` line. It should say: the "No per-agent CLAUDE.md mechanism exists" paragraph no longer holds, because `omitClaudeMd` drops CLAUDE.md for a single agent; the SessionStart decline stands |

**Reuse:** for supersession precedents, run `git grep -n '^\*\*Superseded by' docs/design-decisions/`.

**Verification for Dispatch 2:** `test_ask_review_permissions.py`, `test_hook_alignment.py` and `test_design_decision_files.py` on their own. The parent runs steps 1, 4, 7 and 8.

## Verification

1. **Scoped suite.** Run `.venv/bin/python3 claude/.claude/scripts/select-tests.py`, using the main checkout's `.venv` as README's Tests section describes (from this worktree, `../../../../.venv`). Every path this PR touches maps to a test domain. Expect a domain-selected superset of `hooks/tests` and `skills/tests` (a round-2 dry run selected `skills/tests`, `hooks/tests`, `scripts/tests`, `claude/.claude/tests`, `test_transcript_analysis*.py`, `test_select_tests.py` and `test_ticket_reference_discipline.py`). That is not a widening. If the run widens to the full suite, `select-tests.py` hit an unmapped path and failed open. Report which path, and let the wider run stand; it still covers every test below. These must all pass:
   - `test_nudge_answer_provenance.py`
   - `test_output_preferences_layering.py`
   - `test_doc_counts.py`
   - `test_global_claude_md_email_redaction.py`
   - `test_deny_no_op_dispatch.py`
   - `test_check_claude_md_length.py`
   - `test_design_decision_files.py`
   - `test_rules_frontmatter.py`
   - `test_ask_review_permissions.py`
   - `test_hook_alignment.py`
   - `test_global_claude_md_groups.py`
   - `test_ticket_reference_discipline.py`, run after A stages the new module
   - the state-path, marker-bullet, MEMORY, and citation contracts in `test_skills.py`, all unchanged
2. **Size gate.** Run `wc -c -l claude/.claude/CLAUDE.md` before and after. The result must be no more than 31,574 bytes and no more than 200 lines; expect 31,569 bytes and 196 lines (row 3). If the bytes exceed 31,574, stop and report instead of trimming rule wording. Committing A must pass `check-claude-md-length.sh`. After A commits, the gate compares each later commit against A's size, so a later CLAUDE.md edit on this branch, such as a `/code-review` or `/ready-for-review` fix, must not grow the file. If one would, stop and report. In the PR body, record the before and after figures, plus the byte size of each group (the lines from `# Agent Core` up to `# Main session`, and the rest). Measure the groups with `LC_ALL=C awk '/^# Main session$/{m=1} {b[m+0]+=length($0)+1} END{print b[0], b[1]}' claude/.claude/CLAUDE.md`; the two figures sum to the file size. The group sizes answer GH-1085's open question 11.
3. **Enumerated move diff. Mandatory.** Dispatch 1 runs it on the working tree (see its self-check). The parent re-runs it right after committing A: `diff <(git show HEAD~1:claude/.claude/CLAUDE.md | LC_ALL=C sort) <(git show HEAD:claude/.claude/CLAUDE.md | LC_ALL=C sort)`. The parent pastes the output into the PR body. The output must show exactly the following:
   - **Removed:** `# Global Instructions`, old lines 24 and 54, the indented `  - Merge stays human-only; ...` line (old 182), `## Code Comments, Documentation, and Prose`, and the two `###` subsection headings.
   - **Added:** `# Agent Core`, the opening line, `# Main session`, the ``- No globs in `permissions.allow`.`` stub, a second `## Safety`, a second `## Working Style`, the two walk-through halves, the dedented, reworded `- Merge stays human-only; ...` line, `### Durable text`, the two `####` headings, and seven blank lines.

   Any other line in that diff is unplanned rewording. A clean diff proves the multiset of physical lines was kept; it doesn't prove placement, which step 5 checks.
4. **Compression-diff and trigger audit** (`ai-instruction-and-memory-files`). The parent runs this after both dispatches return.
   - Write one row per removed line that carries instruction content: the glob bullet, which survives verbatim in the rule file and as a one-line stub in Agent Core. The title has no instruction content.
   - In the PR body, state when the relocated glob rule loads: its rationale on a Read-tool read of a settings file inside the project, and the stub always.
   - Name the gaps (a)-(f) from row 25 as gaps the plan found, of which the engineer's round-2 answer covered only (d). Include "advice given without any settings file being opened or created" and "Bash-mediated writes", and say the hook covers Edit, Write and MultiEdit only and fails open when `_lib.sh` is unreadable.
   - State what the probes observed: a Read-tool read outside the project and a Bash read inside it loaded no rule, in one-trial subagent probes. State that "Edit requires a prior Read" is unverified.
   - Cite `ask-review-permissions.sh:21-33`, the new `MultiEdit` test, the wiring assertion and the rule-file invariant test.
5. **Placement and escalation check.**
   - Read the final file against the Placement ledger. Every P-row's bullet must sit under the group and section the ledger names.
   - Walk each escalation phrase listed in row 51. Confirm the opening line covers each one, and that line 14's "ask first" doesn't trigger it.
   - For `:6`, `:31`, `:118`, `:9-12` and `:184`, state which action is withheld, or which halt applies, under the final opening line. Each must be the gated action or the continuation, not the confirmation.
   - For `:33`, `:73` and `:86`, confirm the line leaves the subagent working and listing the item in its return. A halt there is an over-stop.
   - For forks, confirm `:182` names forks, so a fork returns its work instead of following Main session's commit-and-PR duties.
   - Read `# Main session` for text addressed to a subagent. `:182` was the only hit in round 2; confirm none remains.
6. **After merge: subagent spot-check.** Use this repo's own sessions only, on a single account, and cite beside the figure a scope command that refuses a wider corpus, to meet `docs/private-project-redaction.md`'s publishing bar. A wider read goes to the owner privately. If no scope-refusing instrument covers the compliance read, the result goes to the owner privately, and GH-1085 records only the decision (kept or reverted) and that the check ran, with no counts or rates.
   - The engineer picks the sample size per agent type before the check, because one transcript cannot show a skip rate. Cover a `code-writer` run, a staff-* review, a `general-purpose` dispatch made after the pull, and a fork.
   - Check both directions: did an agent skip an Agent Core rule it needed, and did it act on a Main session rule or ship on its own?
   - Record the result in GH-1085, as aggregate results and not quoted transcript text.
   - If a subagent skipped an Agent Core rule because of the new grouping, revert the squash commit (see Critical files) and reopen the placement question in GH-1085.
   - There is no pre-merge control sample, so treat a skip as caused by the grouping only when the agent's return or transcript shows it.
   - Do this before the next PR that touches CLAUDE.md. The engineer owns the check.
7. **Reference grep, both directions.**
   - `git grep -n "Code Comments, Documentation, and Prose"`, `"Global Instructions"`, and `"Don't add globs"` may hit only preserved records (`.claude/plans/`, `CHANGELOG.md`, `docs/design-decisions/prose-rules-promoted-to-global-claude-md.md`, the declined-SessionStart record, and the new decision record). The glob text may also hit the rule file.
   - `git grep -n "§Durable text" -- README.md claude-skills/skills/code-review/SKILL.md claude-skills/skills/plan-it/SKILL.md claude/.claude/agents/code-writer.md claude/.claude/agents/comment-discipline-reviewer.md` must list the ten sites in row 12: README.md (1), code-review/SKILL.md (3), plan-it/SKILL.md (1), code-writer.md (2), and comment-discipline-reviewer.md (3). The plan file and the decision record also contain the string, so the pathspec keeps them out. Two sites are hard-wrapped across lines, so also check them by reading.
   - `git grep -n "output-preferences" -- README.md docs/` and confirm any prose naming a section for a moved bullet still matches where the bullet now sits.
8. **Lint.** Run `.venv/bin/ruff check claude/.claude/ claude-skills/`.
9. **Review routing.** `/code-review` dispatches the per-file reviews:
   - `/skill-review` for the two SKILL.md edits (hook-enforced)
   - `/agent-review` for the two agent files
   - `ai-instruction-and-memory-files` for CLAUDE.md and the rule file

## Out of scope

- **The output-preferences import, deferred, as the engineer confirmed in round 3 (rows 44–46, 54).** The file holds the user's personal preferences and stays untracked (engineer, round 3 answers). The follow-up weighs two shapes. One is an `@~/.claude/output-preferences.md` import of a file outside the repo, which is plan-architect's recommendation: HOME is shared by all the engineer's accounts, the file is never in the tree, and it is not imposed on other stow consumers. The other is a relative import beside CLAUDE.md, the engineer's colocation idea. The scratch test (row 45) shows that resolves into the repo's `claude/.claude/`, where a `.gitignore` entry would be the only barrier from the public tree. The `@~/` form fails `test_global_claude_md_has_no_state_path` until `output-preferences.md` is reclassified from per-account to HOME-anchored (`test_skills.py:5622-5631`, `:5810-5816`), and it moves files for `CLAUDE_CONFIG_DIR` consumers. `@~/...` resolution is untested. Either way, a personal machine-setup repo can share one file across accounts today by symlinking each non-personal `<config-dir>/output-preferences.md` to `~/.claude/output-preferences.md`.
  - The scratch test also unblocks phase 3's `@`-import of core: a colocated relative import of core resolves inside the repo.

  The follow-up also carries these concerns:
  - Missing-target behavior is undocumented. The `install.sh` touch covers fresh installs, leaving a pull without re-running `install.sh`.
  - Changing the path orphans existing `<config-dir>/output-preferences.md` files. The engineer sees migration as easy.
  - A shared file should not hold account- or project-identifying content.
  - A `.gitignore` entry is needed because a relative import puts the file in the repo tree. It would also guard against a leftover top-level `~/.claude` symlink from the old `stow --adopt` setup (rows 27–35).
  - `.gitignore` has no `claude/.claude/output-preferences.md` insurance line, although similar user-local files have one.
  - `install.sh:773` checks `$HOME/.claude/output-preferences.md`, while the CLAUDE.md bullet reads `<config-dir>/output-preferences.md`, so its tip sends a non-personal account to the wrong path.
  - Its own tracker issue, filed after the engineer confirms.
- **`ask-review-permissions.sh` asking only for paths containing `.claude/settings`** (row 25, gap c), and its Edit, Write and MultiEdit-only scope (gap d). Both predate the change. Raise them as their own fix, with the follow-up filed as its own tracker issue.
- **`advance-past-commit-stall.sh:104`** cites the `:182` clause as "CLAUDE.md's Shipping section", which is now Working Style. Update the citation in a follow-up (row 11).
- **Two more dangling phrases after verbatim moves:** `:146`'s "the section below" (now a subsection) and the Durable text scope line "This section governs comments and durable docs only" (ambiguous inside Prose). Both go on the phase 2 follow-up list in the decision record.
- **A permission deny rule for Bash reads of settings files,** and delivering the rationale through the hook's own `permissionDecisionReason`. Plan-architect advised against the deny rule (row 58). Whether the reason string reaches the model is unverified.
- **The byte margin.** The file is 31,574 bytes and the gate forbids growth, so the margin after this PR is 5 bytes, and any later wording growth needs an equal trim, which phase 2 pays for. No change is deferred for it. The engineer's positions: naming the review-orchestrator is deferred on scope, because the agent doesn't exist yet; the stub is better practice than keeping the full permission-globs bullet in core; per-bullet "(main)" tags are excessive.
- **PR linkage.** The PR body says "Part of #1085", not "Closes", because GH-1085's phase 1 still lists the import and the glob relocation as written there. Posting any update to GH-1085 needs the engineer's confirmation first.
- **Fork shipping control beyond prose (row 59).** No commit, push or PR-creation gate keyed on caller identity covers a fork, `code-writer` or `general-purpose`. `deny-reviewer-tree-mutation.sh` keys on `agent_type` only for the closed review-only set, denies only commit and push, and does not gate `gh pr create`. A fork runs in the parent's process identity, so nothing can tell it from the main session. The existing gates are review-state gates that pass a fork after a main-session review at HEAD. A follow-up could add an `agent_type`-keyed deny on commit, push and PR creation for non-fork subagents, by extending `deny-reviewer-tree-mutation.sh` and its closed agent set. It cannot cover forks. Its own tracker issue, filed after the engineer confirms.
- **Phase 2 rewordings:** widen "understand intent", widen "walk through approach", generalize "verify, don't guess", and merge the "check, don't assume" cluster. Phase 1 moves text verbatim.
- **Phase 3:**
  - `omitClaudeMd` adoption, the agent-core skill, and the roster test.
  - Re-pointing `CLAUDE.md §Safety`, `§Working Style`, and `§Durable text` references at the skill, which also ends the duplicate headings.
  - GH-1085 open questions 1–3, 7, 8, and 10.
- **Separate PRs already named in GH-1085:** the claude-config routing exception as a project-level rule, and #1083.
- **The review-orchestrator agent.** Its body must claim Main session, because the opening line names only the main session and forks (row 48). It lives in open PR #714, not on `main`. The main session posted a comment there asking for that change.
- **Trimming agent-file restatements** of CLAUDE.md sections, such as `code-writer.md:70,116`. This PR only renames the section there.
- **An edit-time project rule, plus a placement regression test,** for which group a new CLAUDE.md rule belongs in. `test_global_claude_md_groups.py` pins only the group contract, the decided placements and the core-only rules; per-bullet placement stays a manual check (Verification 5) until that follow-up. For now the decision record carries the guidance.
- **Raising the 25,600-byte or 200-line limits.** The 200-line figure is Anthropic's CLAUDE.md guidance. The byte figure is this repo's extrapolation from MEMORY.md's load window (`check-claude-md-length.sh:43-51`; `.claude/plans/claude-md-audience-restructure.md:57`), not an Anthropic figure. Neither is raised here, and the decision record does not restate a provenance claim for the byte figure.
- **Stale references that predate this change.** Raise these with the reviewer instead of fixing them: README.md:246 cites "Model Routing", and CLAUDE.md:136 cites "Codebase discovery", which is no section. Both are outside the verbatim-move remit.
- **Records that stay untouched:** CHANGELOG.md:94, `.claude/plans/*`, `docs/design-decisions/prose-rules-promoted-to-global-claude-md.md:5,13,15`, and `declined-sessionstart-additionalcontext-injection.md:33`.
