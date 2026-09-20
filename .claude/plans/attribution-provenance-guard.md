# Attribution provenance guard

## Context

Goal: stop agents from attributing model- or subagent-authored content to the engineer, so a claim reaches the `[engineer-verified]` tag (and its override protection) only when the engineer actually said it.

Why now: a recent `/plan-it` session asked a clarifying question via `AskUserQuestion`. The model-authored description of the chosen option named specifics ("fix / defer to issue / reject") the engineer never saw as a decision. The tool result carried only the selected label. In the next turn the model restated its own description in a `plan-architect` dispatch under "Engineer's answers … [engineer-verified], do not override". It also rendered unchosen options as "they explicitly rejected …". The subagent echoed the tag back, and the model relayed the fabrication to the engineer as their decision. Because `[engineer-verified]` forbids silent revision, the grammar protected the fabrication from correction. Filing upstream is unlikely to get traction, so the fix belongs in this harness.

Intended outcome: the harness (a) makes the boundary between what the engineer selected or typed and what the model authored explicit at the point the answer arrives, and (b) narrows `[engineer-verified]` so a menu selection covers only the assented label or the engineer's own typed text — never model-authored option prose or inferences from unchosen options — and requires that provenance to survive relay into subagent prompts.

## Approach

The fix is prose plus one advisory hook. The general attribution rule gets its canonical home as a new Working Style bullet in `claude/.claude/CLAUDE.md`. plan-it's tag becomes `[engineer-verified: "<quote>"]` and its definition points back to that bullet. plan-review's ledger checks learn the narrower scope. A PostToolUse `AskUserQuestion` hook adds a one-line provenance reminder when an answer arrives, but only if a live probe first confirms that PostToolUse fires for that tool. I recommend against the PreToolUse-on-`Agent`/`SendMessage` layer and for deferring the handoff-tag unification. Both recommendations match the leans the engineer typed, so neither is left open.

**Enforcement layers (Step 4 Q1): prose plus the AskUserQuestion hook.** The conflation happens in the turn right after the answer arrives. At that point the model's own option descriptions sit in context next to a bare label. A reminder placed at that point adds to the always-loaded rule instead of replacing it. That matters because `docs/design-decisions/declined-sessionstart-additionalcontext-injection.md:13` counts `additionalContext` adherence as unmeasured. The hook costs little:
- It fires only on human-paced events.
- It reads nothing but `tool_name`, so the undocumented `tool_response` shape is not load-bearing.
- It keeps no state.
- It adds one line of context per question.

No measurement shows it changes behavior beyond what the prose does. Treat it as cheap insurance at the exact boundary, not a proven control.

**Why the PreToolUse-on-Agent layer doesn't earn its cost.** It could take two forms, and neither pays off:
1. **Keyword match on dispatch prompts** (`engineer-verified`, "engineer's answers"). This can't tell a faithful relay from a fabricated one. Every plan-it Step 5 dispatch legitimately carries Step 4 answers, so it would fire on every correct dispatch — a nudge with no signal.
2. **A version that can tell them apart.** It would have to check each quoted string against the user turns and `AskUserQuestion` answers in the session transcript. That means JSONL parsing on the `Agent` hot path against an undocumented transcript schema. PreToolUse `additionalContext` delivery is also contested inside this repo: `nudge-transcript-toolkit.sh:11` calls it unsupported, while `_lib.sh:233` emits it on an allow.

The quote requirement this plan adds is what would make the second form possible later. Out of scope records when to reopen it.

**Canonical home (Q2): global `CLAUDE.md` Working Style, right after "Be precise" (line 61).** It is the only surface loaded in every session and every subagent. So it covers:
- ad hoc `AskUserQuestion` use,
- replies to the engineer,
- `plan-architect` in consult mode, which never reads plan-it.

Two alternatives fall short:
- plan-it `SKILL.md` loads only during `/plan-it`. It misses the asks at `plan-review/SKILL.md:270`, `review-loop-cost-audit/SKILL.md:43`, and any ad hoc ask.
- `subagent-delegation` loads on demand and covers dispatch, not replies.

Each bullet in that file is one physical line, so the change adds one line (183 → 184, under the 200-line gate). Two other sites carry parts of the rule, each for its own reason:
- plan-it's definition states only the scope that is specific to the tag, then points to the bullet.
- The hook message restates the rule's core in one sentence, because injected text must stand alone at runtime. It names the bullet by its bold lead.

**Tag form (Q3).** The syntax `[engineer-verified: "<quote>"]` matches the quoted form Step 3 already found, unprescribed, in a few plans. The tag covers only what the quote states, read against the question it answered. Bare tags in committed plans stay as they are, because those plans are provenance for merged changes. The quoted form is required on any row a revision adds or changes. No script parses the tag, so no code consumer changes.

**Consumers.** plan-review changes in two places:
- B5 flags a row whose claim goes beyond its quote.
- `ROUTING.md:55` limits the escalate-don't-resolve rule to the quoted content.

`plan-architect.md` needs no edit:
- In plan-sections mode it reads plan-it `SKILL.md` at runtime (`plan-architect.md:35`), so the narrowed definition reaches it.
- In consult mode the `CLAUDE.md` bullet reaches it.
- It cannot check a relayed quote, because it is not handed the dispatching session's conversation. Its only duty is not to widen a tag past its quote, and the definition now says so.

**Handoff (Q4): defer.** Changing `[engineer-confirmed]` to a quoted form means changing three things together:
- a preamble that `check-handoff.py` checks verbatim at runtime (`handoff/SKILL.md:63`),
- the literal tag match in `CONFIDENCE_TAGS` (`check-handoff.py:48`) — a quoted form would not match it,
- that match's parametrized test.

The new `CLAUDE.md` bullet already limits what either tag may attribute to the engineer, so the underlying risk is covered now without the rename.

**Prescribed text.** The bold lead is cited by plan-it, the hook message, and the hook's drift test, so keep it exactly as written. Keep every fact in the rest; the wording may tighten.

- `CLAUDE.md` bullet (new, after line 61):
  > - **Attribute to the engineer only what they said.** What they said is text they typed or a label they selected, read against the question it answers — never prose you or another agent wrote (an `AskUserQuestion` option description, an inference from options they didn't pick, a subagent's report, or your own earlier turn) recast as their words or decision. Carry that split into every relay (a dispatch prompt, a plan, a reply to the engineer) by quoting their words and marking your own content as yours.
- `plan-it/SKILL.md:97` (replaces the current tag bullet):
  > - `[engineer-verified: "<quote>"]` — the quote is the engineer's own words from this session or the literal option label they selected, verbatim or a verbatim excerpt; never a file the human wrote (that is `[verified: <file>]`, which carries no override protection). The tag covers only what the quote states, read against the question it answered — an option description you wrote, or an inference from options they didn't pick, goes on its own `[unverified]` row (CLAUDE.md §Working Style). Never silently revise or override the quoted content from your own investigation — a contradiction pauses and asks instead. A bare `[engineer-verified]` in an already-committed plan is legacy; use the quoted form on every row you add or change.
- Hook `additionalContext` (static):
  > Answer provenance: the engineer selected only the option label(s) in this result, plus any text they typed. The option descriptions, and anything inferred from options they did not pick, are yours — relay them as your proposal, never as the engineer's decision or under a tag like [engineer-verified] (CLAUDE.md §Working Style, "Attribute to the engineer only what they said").

The hook message restates the bullet for salience at the moment the answer arrives, not for load-path redundancy, since `CLAUDE.md` is already loaded in that session. The PR description names that as the justification.

The `CLAUDE.md §Working Style` pointer copies the uncited shape that `plan-it/SKILL.md:71` already uses. It avoids the backticked `§ "Heading"` grammar, whose target would be ambiguous between the two `CLAUDE.md` files. The cost is that `test_skills.py` never validates these pointers, so renaming Working Style would break them silently. The hook's drift test covers only the bold lead.

### Assumption ledger

```text
Root: an AskUserQuestion answer carries only the selected label, but the asking
model's own option descriptions sit beside it in context, so a relay can
restate model-authored prose as the engineer's decision under
[engineer-verified] — whose override protection then shields the fabrication
from correction.
Givens: AskUserQuestion's model-visible result is a flattened
"<question>"="<label>" string with no provenance marking, and the asking model
authors every option description — beyond reach: the tool's shape is
vendor-owned harness behavior [verified: Step 3 transcript inspection].

Row 1 [mechanism]: general attribution bullet in claude/.claude/CLAUDE.md
Working Style — anchors: root — the only surface loaded in every session and
every subagent, so it covers ad hoc asks, replies, and dispatches; the single
canonical home the other sites point to.
Row 2 [assumption]: every custom agent, plan-architect included, receives the
full CLAUDE.md hierarchy [verified:
docs/design-decisions/declined-sessionstart-additionalcontext-injection.md:27,
quoting the sub-agents docs at :41] — anchors: row1
Row 3 [assumption, corrected]: claude/.claude/CLAUDE.md is 183 lines, and
the new bullet itself is one physical line — but the file also carries a
separate 25,600-byte cap, already exceeded pre-session (31,598 bytes at
commit time), and not every existing bullet is a single physical line (some
are hard-wrapped across multiple lines). check-claude-md-length.sh blocks
past 200 lines OR past the byte cap, each dimension gated on growth vs HEAD
independently [verified: claude/.claude/hooks/check-claude-md-length.sh;
docs/hooks.md:51, corrected same session] — anchors: row1. The byte cap
being already exceeded meant the CLAUDE.md edit had to land
byte-non-increasing against HEAD in the same commit; see the trim list on
the CLAUDE.md bullet below.
Row 4 [assumption]: the plan ships both a general attribution rule and a
plan-it tag change [engineer-verified: "Both (Recommended)"] — anchors: root
Row 5 [mechanism]: narrow the plan-it tag definition (SKILL.md:92, :97) to
[engineer-verified: "<quote>"], covering only the quote read against its
question — anchors: root — makes the tag's scope a comparison a reviewer can
make, rather than a paraphrase nobody can check.
Row 6 [assumption]: [engineer-verified] requires a verbatim quote of what the
engineer said or selected [engineer-verified: "Require quote (Recommended)"]
— anchors: row5
Row 7 [assumption]: no hook, script, or test parses [engineer-verified]; the
only non-plan sites are plan-it SKILL.md/REFERENCES.md and plan-review
SKILL.md/ROUTING.md [verified: rg 'engineer-verified' excluding .claude/plans/
this session] — anchors: row5
Row 8 [mechanism]: committed plans keep their bare tags; the quoted form binds
rows a revision adds or changes — anchors: row5 — committed plans are
provenance for merged changes, and plan-review gates only uncommitted or
modified plans [verified: docs/hooks.md:7].
Row 9 [mechanism]: plan-it Step 5 dispatch bullet (SKILL.md:53) relays each
answer as the selected label or typed text, marking relayed option
descriptions as the session's own — anchors: row1 — a point-of-use pointer
inside the one prescribed relay where the failure occurred.
Row 10 [mechanism]: plan-review consumer update — B5 flags a new or changed
row whose claim goes beyond its quote, or a bare tag; ROUTING.md:55 limits
escalate-don't-resolve to the quoted content — anchors: row5 — plan-review
consumes the tag's semantics, and spawned reviewers never read plan-it.
Row 11 [assumption]: plan-review SKILL.md (289 lines) and ROUTING.md both sit
under their 500-line cap [verified: check-skill-length.sh:10-15; line count
this session] — anchors: row10
Row 12 [assumption]: plan-architect.md needs no edit — plan-sections mode reads
plan-it SKILL.md at runtime [verified: claude/.claude/agents/plan-architect.md:35],
and consult mode inherits row1 via row2 — anchors: row5
Row 13 [mechanism]: PostToolUse AskUserQuestion hook
nudge-answer-provenance.sh — informational, static additionalContext, reads
only tool_name — anchors: root — the reminder lands next to the answer, in
the turn the conflation happens, for every stow consumer in every repo.
Lighter primitives, each insufficient: (a) row1's prose alone — always
loaded, but not placed at the moment the bare label arrives beside the
model's own descriptions; the hook adds to it rather than replacing it;
(b) per-skill guidance in plan-it Steps 4/5 — covers only prescribed asks,
not plan-review:270, review-loop-cost-audit:43, or ad hoc ones;
(c) PostToolUse systemMessage — reaches the user only, not the model
[verified: docs/hooks.md:78]. Heavier alternative rejected: rewriting the
result string via updatedToolOutput depends on the undocumented tool_response
shape and alters vendor tool output.
Row 14 [assumption]: the engineer leans toward the first offered layer
option, "Prose + AskUserQuestion hook (Recommended)", and asked for
plan-architect's view before settling [engineer-verified: "I think the first
option. What does plan-architect think?"] — anchors: row13
Row 15 [assumption]: PostToolUse fires for AskUserQuestion and delivers its
additionalContext to the model [unverified] — anchors: row13 — the hooks doc
does not name AskUserQuestion; the probe before Phase 2 settles it, and a
negative result drops Phase 2.
Row 16 [assumption]: PostToolUse additionalContext reaches the model for
built-in tools [verified: docs/hooks.md:78; nudge-transcript-toolkit.sh:129-131]
— anchors: row13
Row 17 [assumption]: a new hook must carry a line-2 hook-class, source _lib.sh
via ${0%/*}, use _lib_jq instead of bare jq, and have a docs/hooks.md entry
[verified: test_hook_alignment.py:168, :793, :1048, :1177] — anchors: row13
Row 18 [assumption]: the engineer leans toward deferring the handoff-tag
unification and asked for plan-architect's view [engineer-verified: "Probably
defer for later, but what does plan-architect think?"] — anchors: root
Row 19 [assumption]: a quoted [engineer-confirmed: "…"] would fail
check-handoff.py's literal tag match, and the handoff preamble is checked
verbatim from its fenced block [verified: claude/.claude/scripts/check-handoff.py:48;
claude-skills/skills/handoff/SKILL.md:63] — anchors: row18
```

## Critical files

**Phase 1: prose.** One `code-writer` dispatch. All five files share the rule's wording, so splitting would mean restating it in every dispatch prompt.
- `claude/.claude/CLAUDE.md`: add the Working Style bullet after line 61 (text in Approach). The file was already over its 25,600-byte cap before this change (31,598 bytes at HEAD), so the edit must be byte-non-increasing against HEAD in the same commit (Row 3). Offsetting trims applied: tightened the new bullet's own wording; dropped the timeout-literal example sentence and the discriminator-literals closing sentence from "Ground every choice"; trimmed the "Single source of truth," "Place prose where its reader and altitude match," "Walk through your proposed approach," and PR-body-conciseness (Code Comments) sentences; dropped the inline-suppression "No rationale = no suppression" sentence; dropped the "Start," not "commence" example; dropped the Self-test bullet's "move the rationale to the commit message" sentence as redundant with the neighboring "No 'used to be X'" bullet's same point.
- `claude-skills/skills/plan-it/SKILL.md`:
  - `:53`: an addition, not a replacement. Keep "The Context paragraph from Step 2 and the answers gathered in Step 4, verbatim." and append: "Relay each answer as the selected label or the engineer's typed text; mark any option description you relay as your own proposal (CLAUDE.md §Working Style)."
  - `:92`: the tag name becomes the quoted form.
  - `:97`: replace with the prescribed definition.
- `claude-skills/skills/plan-it/REFERENCES.md`:
  - `:108`: the grammar row uses `[engineer-verified: "<quote>"]`.
  - `:133-135`: worked-example Row 4 gets a synthetic quote whose content covers the row's claim.
  - After `:147`: new `### Why the tag carries a quote` subsection, kept abstract with no incident specifics. It says:
    - A menu answer returns only the label, while the asking session's description sits beside it.
    - Without a quote, a relay can restate that description under the tag, and the tag's protection then shields the restatement.
    - The quote limits protection to text a reviewer can compare the claim against.
    - A quote cannot catch a fabricated quote; only a transcript check could.
- `claude-skills/skills/plan-review/SKILL.md` `:130` (B5): append one sentence. An `[engineer-verified: "<quote>"]` row is evidence only for what its quote states. On a row this revision adds or changes, flag a claim that goes beyond its quote, or a bare tag, and move the excess to its own `[unverified]` row.
- `claude-skills/skills/plan-review/ROUTING.md` `:55`: limit "do not resolve a contradiction … unilaterally" to the row's quoted content.
- Verification for this dispatch: `.venv/bin/python3 claude/.claude/scripts/select-tests.py`.

**Probe: parent session plus the engineer, no `code-writer`.** It needs a live `AskUserQuestion` answer.
1. Write a throwaway hook at `/tmp/askuserquestion-probe/probe.sh`. It saves its stdin to `/tmp/askuserquestion-probe/payload.json` and prints `{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"PROBE-SENTINEL-7Q"}}`.
2. Register it under matcher `AskUserQuestion` in `/tmp/askuserquestion-probe/.claude/settings.json`, with command `bash /tmp/askuserquestion-probe/probe.sh`. The `bash` prefix removes any dependence on the execute bit, so a permission failure can't pass for "the hook never fired". Use this scratch project rather than the worktree, so no existing settings file needs editing or restoring.
3. The engineer starts a fresh session in that directory, accepting the folder-trust prompt if shown, and types exactly: `Call AskUserQuestion once with the question "Probe?" and options "A" and "B". Do not read any file.` The engineer selects "A". The typed prompt never contains the sentinel, so the string can reach the transcript only by hook delivery.
4. Read the result from artifacts, not from the probe session's narration:
   - Locate the probe session's transcript: the newest `*.jsonl` under the account's `projects/` directory whose name encodes `/tmp/askuserquestion-probe`.
   - Pass: `payload.json` exists with `tool_name` `AskUserQuestion`, and `PROBE-SENTINEL-7Q` appears in that transcript in an entry after the `AskUserQuestion` tool result.
   - Payload file but no sentinel in the transcript: the hook fired but `additionalContext` was not delivered. Fail.
   - No payload file: the hook never fired. Fail.
   - Record the matcher string exactly as registered; Phase 2 reuses it byte-for-byte.
5. On failure, skip Phase 2 and note it in the PR body. Either way, delete `/tmp/askuserquestion-probe/`. Do not commit the payload; it holds session text.

**Phase 2: hook.** One `code-writer` dispatch, after Phase 1, because the drift test reads Phase 1's bullet.
- `claude/.claude/hooks/nudge-answer-provenance.sh` (new):
  - Line 2 is `# hook-class: informational`.
  - Header facts, one sentence each:
    - It emits a static `additionalContext` line that separates the engineer's selected label and typed text from the model's own option descriptions.
    - It reads only `tool_name`, so `tool_response`'s shape is not load-bearing.
    - It never denies, keeps no state, and has no kill switch other than its `settings.json` entry.
    - It filters `tool_name` itself.
    - It fires on every `AskUserQuestion` call with no per-session dedup; if repetition proves noisy, add a fired-marker like `nudge-long-turn-subagent.sh`'s.
    - Known gaps: none beyond unmeasured adherence, since it fires unconditionally.
  - Reuse `nudge-transcript-toolkit.sh:58-65` for the `_lib.sh` source and the `_lib_jq` `tool_name` read, and `:129-131` for the output. Use `hookEventName: "PostToolUse"` and exit 0 on every path.
- `claude/.claude/hooks/tests/test_nudge_answer_provenance.py` (new): follow `test_nudge_transcript_toolkit.py`'s file layout, but extract output with `helpers.run_hook_context` (`claude/.claude/tests/helpers.py:209`) rather than that file's local `_additional_context`. Also reuse `build_path_without` and `HOOKS_DIR`/`REPO_ROOT`. Every "fires" case runs the hook as a subprocess, parses its stdout as JSON, and asserts `hookSpecificOutput.hookEventName == "PostToolUse"` plus a non-empty `additionalContext`. Cases:
  - fires on an `AskUserQuestion` payload,
  - fires with `tool_response` absent,
  - silent on another `tool_name`,
  - silent with exit 0 on empty stdin, malformed JSON, and missing `jq`,
  - drift guard: the phrase "Attribute to the engineer only what they said" appears both in the parsed runtime `additionalContext` (not the `.sh` source text) and in `claude/.claude/CLAUDE.md`,
  - wiring: `claude/.claude/settings.json` has a `PostToolUse` entry whose matcher is exactly the string the probe validated (`AskUserQuestion`) and whose command is `~/.claude/hooks/nudge-answer-provenance.sh`, a file that exists under `HOOKS_DIR`. No existing wiring check covers `hook-class: informational` hooks.
- `claude/.claude/settings.json`: add a new PostToolUse object `{"matcher": "AskUserQuestion", "hooks": [{"type": "command", "command": "~/.claude/hooks/nudge-answer-provenance.sh"}]}` after the `Edit|Write|MultiEdit` entry (`:461-469`).
- `docs/hooks.md`: add a Utility hooks entry after `:66`, in the same shape as the `nudge-transcript-toolkit.sh` entry. It covers the trigger, static message, `tool_name`-only read, no state, and no kill switch.
- Verification for this dispatch: `.venv/bin/python3 claude/.claude/scripts/select-tests.py`, `.venv/bin/ruff check claude/.claude/ claude-skills/`, `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck`.

## Verification

- `.venv/bin/python3 claude/.claude/scripts/select-tests.py`. It should select `test_hook_alignment.py` (docs entry, hook-class, `_lib.sh` sourcing, no bare `jq`), the new `test_nudge_answer_provenance.py`, and `claude-skills/skills/tests/test_skills.py`. The last one confirms that no Phase 1 edit introduced an unresolvable `§` citation.
- `.venv/bin/ruff check claude/.claude/ claude-skills/` and `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck`.
- `git grep -n 'engineer-verified' -- ':!.claude/plans/'`: every hit uses the quoted form, or names the tag family in rationale prose.
- `/skill-review` on the plan-it and plan-review `SKILL.md` diffs (hook-enforced at commit). `/code-review` also routes the `CLAUDE.md` edit to `ai-instruction-and-memory-files`.
- The probe outcome, pass or fail and which signal was observed, goes in the PR body. It is the only evidence for Row 15.

## Out of scope

- **PreToolUse-on-`Agent`/`SendMessage` provenance hook.** A keyword version carries no signal, and a discriminating version needs transcript parsing on the dispatch hot path (see Approach). Reopen if, after this ships, someone observes a `[engineer-verified: "…"]` quote that traces to no engineer turn. The follow-up shape is a quote verifier: a script `plan-review` calls, not a hook, that checks each quoted string against the session transcript's user turns and `AskUserQuestion` answers.
- **Unifying handoff's `[engineer-confirmed]` with `[engineer-verified]`, or quoting it.** It needs changes to `check-handoff.py:48`, its parametrized test (`test_check_handoff.py:233`), and the verbatim-checked preamble (`handoff/SKILL.md:63-79`). The `CLAUDE.md` bullet already governs what either tag may attribute.
- **Rewriting bare tags in committed plans.** They are provenance for merged changes (Row 8).
- **Edits to `plan-architect.md` and `subagent-delegation/SKILL.md`.** Both inherit the rule through runtime reads of plan-it `SKILL.md` and `CLAUDE.md` (Rows 2, 12).

