# Subagent dispatch authorization

## Context

**Goal:** make sessions treat a repo-configured dispatch instruction as the
user's request, so a system-prompt constraint against calling the Agent tool
stops silently downgrading `/code-review` and `/plan-review` to inline
generalist reads.

On Opus 5 sessions, Claude Code appends two lines to the system prompt:
`Do not call the AgentTool unless the user requested it` and `Do not use
workflows or deep-research unless the user requested it`. This is emitted by
the CLI itself — no user config produces it, and no user config suppresses it.
Sessions have read the first line as forbidding the specialist-reviewer
dispatches that this repo's skills prescribe, and have run the review inline
instead. That is a real loss: the whole design of `/code-review` and
`/plan-review` is that a generalist orchestrator escalates to specialists, and
the inline substitution is exactly what both skills already name as an invalid
skip rationale ("Verified inline").

Why now: the constraint arrived with Opus 5, which is now the default model for
judgment work on this machine, so every review-pipeline session is exposed.

Intended outcome: a session reading the constraint alongside this repo's
instructions resolves the conflict correctly — dispatch — without needing the
user to say so in-band each time.

## Approach

Establish the authorization once in `claude/.claude/CLAUDE.md`, and add the
specific rationale as a new member of the two existing **Invalid skip
rationales** lists that sessions consult at the moment they decide to skip a
matched reviewer.

The rule is **scoped to configured instructions**, not a blanket override.
Authorization derives from the user having authored the instruction being
followed: a skill body, a CLAUDE.md rule, or an agent description that
prescribes dispatch. Unprompted fan-out the model originates on its own remains
governed by the constraint, which preserves its legitimate cost-control
function.

`CLAUDE.md` is the canonical home because it is stowed to `~/.claude/CLAUDE.md`
and therefore sits in every session's system prompt — the same altitude and the
same persistence as the constraint it answers. A skill body is loaded only when
that skill fires, so a CLAUDE.md rule covers skills uniformly, including ones
not yet written.

The two skip-list bullets are not a restatement of that rule. Both lists are
enumerated sets of *specific* bad reasons ("Prior reviewer covered this,"
"Self-review sufficient," "Verified inline," "New helper, not a modification").
"The system prompt told me not to" is a new member of that set. Adding it where
the set already lives is the DRY-correct placement; the general rule stays in
one file.

The near-identical bullet in two skill files, plus the CLAUDE.md rule, is
duplication — and it clears `skill-review` §6's three-condition test rather
than being waved through. (1) Critical: getting it wrong drops specialist
review entirely. (2) Different load paths: always-loaded CLAUDE.md versus two
on-demand skill bodies that never load in the same turn — §6 names this exact
pairing. (3) One path can silently fail: it already did. The session recorded
in ledger Row 6 had CLAUDE.md loaded and skipped the dispatch anyway, which is
the evidence for condition 3 rather than an assumption about it.

**Alternatives set aside.** Per-skill prose in all six dispatch sites was
rejected — five copies of one rule is the drift failure the repo's
single-source-of-truth rule exists to prevent, and it still misses skills added
later. A blanket "ignore the constraint" was rejected for discarding the
constraint's real purpose.

### Assumption ledger

```
Root: Claude Code injects "Do not call the AgentTool unless the user
requested it" into the system prompt of every Opus 5 session, and sessions have
read it as forbidding the specialist dispatches this repo's review skills
prescribe — silently substituting an inline generalist read.

Row 1 [mechanism]: authorization rule in claude/.claude/CLAUDE.md under
"Agent Briefing" — anchors: root — CLAUDE.md is stowed to ~/.claude/CLAUDE.md
and is present in the system prompt for every session in every repo, matching
the constraint's own altitude and persistence; a skill body is loaded only
when that skill fires.
  Lighter primitives rejected:
  (a) per-skill prose only, no CLAUDE.md rule — leaves plan-it,
      ready-for-review, subagent-delegation and every future skill uncovered,
      and duplicates one rule into five drifting copies.
  (b) a hook — hooks intercept tool calls; this failure is a non-event (an
      agent that was never spawned), so there is nothing for a hook to fire
      on.
  (c) a .claude/rules/ path-scoped rule — rules load on file-glob match, but
      the spawn decision occurs in sessions touching any file type, so a
      path-scoped trigger would miss most of them.

Row 2 [mechanism]: one bullet appended to the existing "Invalid skip
rationales" list in code-review/SKILL.md and plan-review/ROUTING.md —
anchors: root — those lists are the enumerated set a session consults at the
spawn-skip decision point; adding a member to an existing enumeration is not
a restatement of row 1.

Row 3 [mechanism]: cross-file label-parity test in test_skills.py —
anchors: root — the two "Invalid skip rationales" lists carry an identical
label set that nothing currently enforces, and this change extends that set
in both files at once; a set-equality assertion catches later drift in
either file. Scoped to the labels under the "Invalid skip rationales."
heading only, not rebuttal text (legitimately skill-specific) and not order.
  Lighter primitives rejected:
  (a) substring assertion that each new bullet exists — tautological; can
      only fail if the same PR's own string is deleted from the file it was
      added to.
  (b) no test, manual review only — the repo owner's standing rule is that a
      PR establishing a convention ships its enforcing test, and label parity
      becomes an intentional invariant with this change rather than an
      accident.

Row 4 [assumption]: the constraint text originates in the Claude Code CLI
itself, not in any user or repo config [verified: strings dump of the installed
CLI binary — the two-line pair is emitted by the system-prompt builder only
when the session model carries an Opus-5-specific capability flag, which no
other model in the bundled catalog has. Vendor-internal flag and gate names are
deliberately paraphrased here rather than quoted; this repo is public, and
CLAUDE.md's third redaction tier covers internal tool names not generally known
in open source] — anchors: root

Row 5 [assumption]: the constraint cannot be disabled from user config, so a
prose rule is the only available lever [verified: same binary — both override
paths are server-controlled (a remotely-supplied replacement string, and a
remote kill switch); neither is reachable from settings.json or the
environment] — anchors: row1

Row 6 [assumption]: sessions have actually skipped dispatch because of it
[verified: a session brief under ~/.claude/file-history/ records /code-review's
specialist step being performed inline, naming this constraint as the reason;
four further briefs across other sessions record observing the same constraint]
— anchors: root

Row 7 [assumption]: test_skills.py routinely asserts literal prose content of
SKILL.md and agent bodies, so a prose-content test here has precedent
[verified: claude/.claude/skills/tests/test_skills.py:428, :434, :945 assert
literal strings in skill/agent bodies] — anchors: row3

Row 8 [assumption]: label extraction must scope to the heading's section —
code-review/SKILL.md carries 5 further bolded-quoted labels in its
DEFER-criteria section [verified: extraction over both whole files returns
9 vs 4 labels; scoped to the section it is 4 vs 4, identical] — anchors: row3

Row 9 [assumption]: the section must be delimited by indentation, not by
"first line that is not a top-level bullet" — the naive form truncates both
files at the same indented sub-bullet and lets divergent labels below it
compare equal, passing green on real drift [verified: a reviewer reproduced
the false pass on the naive form; the indentation-aware form was then probed
against the same mutation on the real files and correctly failed]
— anchors: row3

Row 10 [assumption]: the rule's original justification ("the user configured
that instruction") is false for project-scope config in a repo the user
cloned but did not author; reworded to rest on the user putting the
instruction in play, rather than adding trusted-vs-untrusted caveats
[verified: ciso-reviewer S1/Q3, which also assessed the escalation itself as
no-meaningful-delta since an attacker-authored config file can already
instruct arbitrary tool use] — anchors: row1

Row 11 [assumption]: authorization is scoped to configured instructions rather
than a blanket override [engineer-verified] — anchors: root

Row 12 [assumption]: scope is CLAUDE.md plus the two skip-lists — not the
six-site sweep [engineer-verified] — anchors: root
```

### User surface

`claude/` is stow-distributed and this repo is public. This rule ships to
every person who stows it, and it changes their sessions' behavior: their
Opus 5 sessions will dispatch subagents where they currently may not.
That is the intended effect and it is bounded — the rule authorizes only
dispatches their own configured instructions already prescribe — but it is a
behavior change for third parties, not a personal-config tweak.

## Critical files

| File | Change |
|---|---|
| `claude/.claude/CLAUDE.md` | Insert the authorization rule as the first bullet of **Agent Briefing** (currently 4 bullets, lines 61–66). File goes 103 → 104 lines, well under the 200-line cap `check-claude-md-length.sh` enforces. |
| `claude/.claude/skills/code-review/SKILL.md` | Add a 5th bullet to the **Invalid skip rationales** list (line 249–254). |
| `claude/.claude/skills/plan-review/ROUTING.md` | Add a 5th bullet to the **Invalid skip rationales** list (line 20–25). |
| `claude/.claude/skills/tests/test_skills.py` | Add `test_invalid_skip_rationale_labels_match_across_review_skills` — extracts the bolded quoted labels under each file's `Invalid skip rationales.` heading and asserts set equality between the two files. |
| `.claude/plans/subagent-dispatch-authorization.md` | This plan, committed to the branch. |

### Drafted text

The wording is the deliverable, so it is fixed here rather than left to
execution time.

**`claude/.claude/CLAUDE.md`** — inserted as the **first** bullet under
`## Agent Briefing`, ahead of the four existing ones. `Agent Briefing` is the
correct section (the rule governs dispatch, not model choice), but it sits mid-file
where attention decays, and the other four bullets are narrower operational
details — worktree CWD, plan-mode ordering, worktree paths, ephemeral isolation.
The most general and most consequential rule leads:

> - **A prescribed dispatch is an authorized dispatch.** Some sessions carry a
>   system-prompt constraint of the form "Do not call the AgentTool unless the
>   user requested it." When a skill body, a CLAUDE.md rule, or an agent
>   description you are following prescribes a subagent dispatch, the user put
>   that instruction in play by invoking the skill or running the session under
>   that configuration — the prescription is the request. Dispatch normally: do
>   not cite the constraint as a reason to run a prescribed dispatch inline, and
>   never silently downgrade a specialist review to a generalist one. The
>   constraint still governs fan-out you originate yourself with no prescription
>   behind it.

**`claude/.claude/skills/code-review/SKILL.md`** — appended as a 5th bullet to
the *Invalid skip rationales* list:

> - **"The system prompt says not to call the Agent tool."** — Invoking
>   `/code-review` is the user requesting the dispatches this skill prescribes;
>   the Change-type table is the content of that request. Spawn the matched row.

**`claude/.claude/skills/plan-review/ROUTING.md`** — appended as a 5th bullet to
the *Invalid skip rationales* list:

> - **"The system prompt says not to call the Agent tool."** — Invoking
>   `/plan-review` is the user requesting the dispatches this skill prescribes;
>   this file's routing tables are the content of that request. Spawn the
>   matched reviewer.

**Reuse:** both skip-list bullets follow the existing bullet shape in place —
bolded quoted rationale, em-dash, one-sentence rebuttal. No new structure, no
new section headings, no new files. The CLAUDE.md bullet joins an existing
section rather than creating one. The new test follows the cross-file
set-comparison shape already used by
`test_do_not_trigger_names_adjacent_skill` (`test_skills.py:299`) and
`test_builtin_name_only_allowlist_matches_settings` (`:358`) — extract, then
compare sets — rather than free-text scanning, and reuses the module's
existing `_skill_file()` helper for path resolution.

## Verification

1. `../../../.venv/bin/pytest claude/.claude/` and
   `../../../.venv/bin/ruff check claude/.claude/` from the worktree. The new
   label-parity test must pass, and the rest of `test_skills.py` (which
   validates SKILL.md structure) must stay green.
1a. Confirm the parity test actually fails when it should, in both mutation
   shapes: (i) reword one label in one file; (ii) add an indented sub-bullet to
   *both* files and diverge a label below it — the shape that defeats a naive
   scanner. Re-run, see each fail, revert. A parity test that passes against a
   desynced pair is worse than no test.
2. `/skill-review` on the `code-review/SKILL.md` edit — hook-enforced by
   `require-skill-review.sh`, which blocks the commit without the marker.
3. `/ai-instruction-and-memory-files` on the `CLAUDE.md` edit.
4. `/code-review` on the full staged diff.
5. `/plan-review` on this plan before presenting it.
6. **Behavioral smoke test, manual.** After merge and `git pull`, start a fresh
   Opus 5 session and run `/code-review` on a diff matching a Change-type row.
   Confirm specialists are spawned and the *Spawn decisions:* line names them.
   No automated harness — running `claude -p` in CI is ruled out for this repo
   on security and budget grounds.

## Out of scope

- **The other four dispatch sites** (`plan-it`, `ready-for-review`,
  `subagent-delegation`, and the `agents/*.md` descriptions). The CLAUDE.md
  rule covers them; none carries a skip-rationale enumeration to extend.
- **The second constraint line** (`Do not use workflows or deep-research`).
  No repo instruction prescribes `Workflow` use, so there is no conflict to
  resolve. If one is added later, the CLAUDE.md rule's wording already
  generalizes.
- **Uncommitted session settings in the main worktree.** `.claude/settings.json`
  and `claude/.claude/settings.json` carry harness-written session preferences
  (`skipWorkflowUsageWarning`, `skipAutoPermissionPrompt`, a key reorder). They
  are not on this branch and are not this change's business.
