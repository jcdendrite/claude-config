# Prevent out-of-`/loop` `ScheduleWakeup` calls with a bare tool-name deny

## Context

Remove the assistant's ability to call `ScheduleWakeup` outside `/loop`, by
denying the tool by bare name in the stow-source settings file. The
assistant reaches for `ScheduleWakeup` as a wait/poll mechanism in sessions
that are not `/loop` sessions — most often while a backgrounded `Agent`
dispatch runs — because it reads the tool's `noop` field as a "check back
later, do nothing" mode the tool does not have. Two sub-modes follow:

- A malformed call rejected with `prompt is required when stop is not
  true`, costing a turn.
- A well-formed call that silently schedules a real wakeup, later fires,
  and re-invokes a large-context session.

Why now: the engineer raised the recurrence directly, and a fresh
transcript measurement this session established that the *well-formed*
sub-mode — the expensive one — is the majority, not the minority that
`docs/design-decisions.md` §41 assumed when it declined to act on
2026-09-01. Separately, a mechanism §41 never evaluated turned out to
exist: `permissions.deny` already carries a bare `EnterPlanMode` entry, and
the permissions documentation states that a bare tool name removes the tool
from context entirely rather than rejecting the call. That is a stronger
guarantee than any mechanism §41 weighed.

The intended outcome is that the call cannot be formed, plus a durable
record of why §41 reversed — so a future session neither re-derives §41's
three correct rejections nor mistakes its conclusion for current.

Corpus figures behind the measurement are deliberately absent from this
file and from §46 beyond the narrow exceptions G5 names. The corpus mixes
private-project and public transcripts, so its counts and medians inherit
the private half. This plan ships in the same PR as the implementation.

## Evidence

Evidence-label citations (`E2`–`E6`) below refer to this session's own
exploration, summarized here for auditability. `E1` does not appear in this
file's citations.

- **E2** — Claude Code's own documentation: `permissions.deny` bare-tool-name
  semantics (`code.claude.com/docs/en/permissions`), `ScheduleWakeup`'s
  canonical built-in status in `tools-reference`, its always-loaded
  (non-deferred) status in the affected sessions, the scheduled-tasks docs
  behind the self-paced-`/loop` iteration inference, and confirmation that
  `skillOverrides` and `disableModelInvocation` have no built-in-tool analog.
- **E3** — a structural classifier run over this session's own transcript
  corpus (four transcript roots on this machine), resolving each
  `tool_result` to its `tool_use_id` and originating `tool_use.name` to
  disposition every `ScheduleWakeup` call by argument shape and by whether
  it followed a backgrounded `Agent` dispatch; also the corpus-wide
  `CronCreate` count.
- **E4** — a filesystem search for a `/loop` `SKILL.md` (install directory,
  plugin cache, npm root — none found) plus research into whether this
  repo's own pipeline has a genuine recurring-workflow use for `/loop`;
  found none, and found `ci-watch.sh` deliberately avoids polling via
  `Bash run_in_background` instead.
- **E5** — direct confirmation against the public `anthropics/claude-code`
  issue tracker that #80350, #88260, and #88205 are still open, and against
  the CHANGELOG through version 2.1.258 that no fix has shipped.
- **E6** — a direct read of `claude/.claude/scripts/select-tests.py`'s
  domain-mapping table, confirming `claude/.claude/settings.json` maps to
  both `HOOKS_TESTS_DIR` and `SKILLS_TESTS_DIR`.

## Approach

Ship `"ScheduleWakeup"` as a second bare tool-name entry in `claude/.claude/settings.json`'s `permissions.deny`, which removes the tool from every stow consumer's session context so the misfire cannot be formed rather than being rejected after the fact. Record the reversal as a new `docs/design-decisions.md` §46, and annotate §41 with a one-line superseded pointer without rewriting its body. Pin the entry with a sibling of the existing `EnterPlanMode` declaration test, and add no second mechanism.

**Why §41 reopens, and on what.** §41's operative claim was mechanism exhaustion — "no repo-owned surface reaches all of them." That claim is now false, and not because its three named Revisit conditions fired. Its survey went from prose to hooks and never reached the settings layer, where a documented primitive exists that no hook can match: a bare tool-name deny removes the tool from context, so it *prevents* the call instead of *rejecting* it. §41 was correct about every mechanism it evaluated and wrong about the set being complete. Of its three Revisit conditions, only the third is met (row 6); conditions one and two are not (rows 7, 8). The lesson worth recording in §46 is that a mechanism-exhaustion claim needs a sweep of every configuration layer — settings keys, CLI flags, hooks, prose — not only the layer the problem surfaced in.

**Why a new entry plus an annotation, not a rewrite.** §42 set the house precedent of extending §30 and §37 "without editing either entry," and §41's three rejections remain individually correct and worth keeping so a future session does not re-derive them. But §41 is a *reversed* decision, not a narrowed one: a reader arriving at the lower number would act on "documented, not guarded" as current. One added status line at the top of §41 fixes that without touching the record of what was considered on 2026-09-01.

**Why §17 is left standing.** §17's rationale is recurring-interval automation, which routes through `CronCreate` and is untouched by this deny; the entry's conclusion still holds, only its scope narrows. Current-behavior description is `docs/skills.md`'s job, and that is where the caveat goes — `docs/skills.md`'s `/loop` row is a description of how things behave now (fair game under the repo's in-file scope rules), while §17 is a dated record of an override decision that did not change.

**Why nothing addresses `noop` directly.** The misconception has no expression surface once the tool is absent from context — the model cannot construct a call to a tool it cannot see. A `CLAUDE.md` line or skill clause warning against a tool the deny already removed is the compounding-defensive-layer shape §41 and §42 both name as a wrong-foundation tell, and it would spend budget `check-claude-md-length.sh` polices for zero behavior change. The `noop` finding earns exactly two homes: §46's narrative (so the mechanism is recoverable if the deny is ever removed) and the upstream report (where a schema field the model reads as a mode is actionable). Neither is a runtime mechanism.

**Why no `CLAUDE.md` line for *communication* either — a separate question from prevention.** The paragraph above rejects a `CLAUDE.md` line as a prevention mechanism; whether to tell the *user* that self-paced `/loop` no longer works is a different question, addressed here so a future session does not mistake the prevention argument for having answered it too.

- `CLAUDE.md` is the only surface here that loads into every session — the four shipped mitigations (`docs/skills.md`, `docs/auto-mode.md`, §46, `CHANGELOG.md`) are all pull-based, and none reaches a consumer who types `/loop <prompt>` and watches it stop.
- Decided against on frequency and budget: an always-loaded line is paid in every session to explain an event that occurs in almost none — the same "noise aimed at the one population the tool exists for" objection §41 raised against the `PostToolUse` nudge — and `check-claude-md-length.sh` polices a 200-line cap that a permanent line would spend against.
- `docs/skills.md`'s `/loop` row is where a consumer checks what `/loop` currently does; §46's Revisit list (below) carries the reopening condition if a consumer reports the silent truncation.

**Why the prose pairing is a docs row, not a `CLAUDE.md` line.** `review-permissions` checklist item 23 requires a bare deny entry be paired with prose naming the alternative. §41 rejected a `CLAUDE.md` line as a repo-side copy of harness-owned text — but that objection dissolves precisely because the deny succeeds: once the tool is removed, its description is gone from context, so there is no harness-owned text left to duplicate. What remains needed is a *reader-facing* reason for the entry's presence, which is what `docs/auto-mode.md`'s Hard-floor deny rules table already provides for every other entry including `EnterPlanMode`. This deliberately departs from the `EnterPlanMode` precedent's second surface: that deny is partial (human `Shift+Tab`, `/plan`, and `defaultMode` paths stay open), so the model retains a routing decision that prose must govern. This deny is total, and the model retains no decision.

**Root problem.** The model reaches for `ScheduleWakeup` as a wait/poll mechanism in sessions that are not `/loop` sessions — most often while a backgrounded `Agent` dispatch runs — and the repo now has a documented settings-layer primitive that prevents the call rather than absorbing its cost, at the price of the self-paced `/loop` mode for every stow consumer.

**Givens** (conditions the design treats as fixed because they lie beyond its reach):

- **G1.** `ScheduleWakeup`'s schema, description, and validation are Anthropic's to change, not this repo's. `[verified: docs/design-decisions.md §41 and .claude/plans/harness-context-mismatched-tool-dispatch.md G1]`
- **G2.** `/loop` is harness-native with no `SKILL.md` anywhere on this filesystem, so this repo cannot instrument its entry or exit. `[verified: §41 G2, independently re-confirmed by E4's install-dir, plugin-cache, and npm-root search]`
- **G3.** `permissions.deny` carries no mode-conditional or active-skill-conditional key, so a deny cannot be scoped to "outside `/loop`." Anthropic owns the settings schema. `[verified: E2, against code.claude.com/docs/en/permissions and the settings schema]`
- **G4.** `~/.claude/settings.local.json` corresponds to no documented Claude Code settings scope, so there is no user-scope personal override file for this machine. `[verified: docs/design-decisions.md §43]`
- **G5.** The transcript corpus behind E3 and E4 mixes private-project and public transcripts, so any count, ratio, median, or duration that would reveal the private half's composition or magnitude cannot be published in this repo. A null count (zero corpus-wide instances of something) and the corpus's own root-count (already public at `docs/case-studies/targeted-read-discipline.md:24`, "four config dirs") carry no such risk and are used where they appear below. Dissolving this would require re-deriving from a public-only corpus, which the prior plan already declined as a decision outside its scope. `[engineer-verified]`

**Assumptions:**

1. A bare tool name in `permissions.deny` removes the tool from context entirely; a scoped `Tool(specifier)` rule leaves it visible and blocks on call. `[verified: E2, quoting code.claude.com/docs/en/permissions — "A bare tool name like `Bash` removes the tool from Claude's context entirely, so Claude never sees it"]`
2. `ScheduleWakeup` is a canonical built-in in `tools-reference`, so the entry produces no startup typo warning. `[verified: E2]`
3. `ScheduleWakeup` is always-loaded rather than deferred/ToolSearch-surfaced in the sessions where the misfire occurs, so the documented bare-name behavior applies without relying on the deferred-tool case. Whether bare-name deny behaves identically for a deferred tool is not established and is not load-bearing here. `[verified: E2 for the always-loaded status; the deferred-tool question is unverified and out of the design's path]`
4. Nothing in this repo — no hook, test, skill, or script — depends on `ScheduleWakeup` being present. `[verified: plan-review round 1, ciso-reviewer]` A `git grep -n ScheduleWakeup` across `claude/ docs/ README.md scripts/`, plus a case-insensitive `wakeup` sweep of `claude/.claude/hooks/` and `claude/.claude/scripts/`, returned zero hits outside pre-existing prose in §41. No gate (`require-code-review.sh`, `require-plan-review.sh`, `ask-review-permissions.sh`) references the tool or depends on wakeup-driven re-invocation. Verification step 3 re-runs this sweep at implementation time to catch drift.
5. The self-paced dynamic `/loop` mode does not silently truncate under the deny. Across 4 independent pre-implementation gate runs, `/loop`'s dynamic mode detected the missing tool via `ToolSearch` on its first turn rather than attempting a call that could fail. In 3 of 4 runs it disclosed the gap honestly and fell back to `Monitor`-only (event-driven, no periodic heartbeat) without fabricating a workaround. In 1 of 4 it substituted `CronCreate` as a fallback heartbeat (~20-minute cadence) without asking first — a real observed instance of the substitute-mechanism risk named in §46's Revisit list, not merely a hypothetical one. Fixed-interval `/loop <interval> <prompt>` is unaffected, confirmed directly: a `CronCreate` job scheduled and fired successfully under the deny. `[verified: pre-implementation gate checks 3 and 4, four independent runs]`
6. §41's third Revisit condition is met: the well-formed non-loop call is the *majority* sub-mode rather than the minority §41 assumed, and the population where a wakeup plausibly fired sits at large context sizes where a re-invocation is a real cost. `[verified: E3's structural classifier, resolving tool_result → tool_use_id → tool_use.name]` — the supporting counts and context medians exist but are unpublishable under G5, so §46 states this ordinally and reproduces no figure.
7. §41's first Revisit condition is not met: `ScheduleWakeup` still accepts out-of-`/loop` calls, and no `PreToolUse` field exposes harness-computed active-mode state. `[verified: E5 — #80350, #88260, #88205 all open; no changelog entry through 2.1.258]`
8. §41's second Revisit condition is not met: E3 classified call disposition, not outcome quality, and surfaced no instance of a fabricated or predicted pending-agent result. `[verified: E3's stated scope]`
9. The attribution of the plausibly-fired subset is a magnitude, not a logged fact — no harness log distinguishes a fired wakeup from a task-completion notification. `[unverified]` — §46 must not present it as a count either way, which G5 independently forces.
10. The failure is driven by the model reading `noop` as a "check back later, do nothing" mode the tool does not have; the errored calls carry a uniform `{delaySeconds, noop, reason}` shape with `prompt` and `stop` never present. `[verified: E3's argument analysis]`
11. `permissions` is not in `guard-settings-session-keys.sh`'s `GUARDED_KEYS_JSON` (`model`, `effortLevel`, `skipAutoPermissionPrompt`, `skipWorkflowUsageWarning`, `theme`, `tui`, …), so this edit commits without the engineer-only path §43 describes. `[verified: guard-settings-session-keys.sh:87-93]`
12. `ask-review-permissions.sh` fires an `ask` decision on any `PreToolUse` `Edit`/`Write`/`MultiEdit` touching `.claude/settings*.json`, and its reason names `permissions.deny`. `[verified: claude/.claude/settings.json:313-317 registration; test_ask_review_permissions.py:45-51 pins the wording]`
13. `test_plan_mode_entry_paths_stay_closed_in_settings` (`test_hook_alignment.py:283-304`) is the exact precedent for the new pin: `json.loads(_SETTINGS_PATH.read_text())` plus a membership assertion, with a docstring scoping the claim to *declared* config state rather than harness enforcement. `[verified: read directly]`
14. A membership check on the exact bare string also catches a later weakening into `"ScheduleWakeup(*)"`, so one assertion suffices and a second shape-check would be redundant. `[verified: `"ScheduleWakeup" in [...]` fails when the list holds only the parenthesized form]`
15. `select-tests.py` maps `claude/.claude/settings.json` to both `HOOKS_TESTS_DIR` and `SKILLS_TESTS_DIR` (the `CLAUDE_SETTINGS_JSON` cross-domain-exception mapping row), `docs/` to the same two via the blanket rule, and no longer falls open to the full suite for `CHANGELOG.md`. `[verified: E6 for the settings mapping; CHANGELOG.md:13 for the CHANGELOG rule]`
16. §46 is the next free number — §45 is the file's last entry. `[verified: `^## [0-9]+\.` heading scan against the current branch tip, re-run after this branch's sync with `main` picked up two more entries (§44, §45) that landed on `main` after this plan was first drafted]`
17. This is a consumer-visible behavior change, so it takes a `CHANGELOG.md` entry under `[Unreleased] / Changed` with a **Migration** clause — unlike §41, which shipped without one because nothing consumer-visible changed. `[verified: CHANGELOG.md:15 and :27 both carry Migration clauses for settings-behavior flips; the prior plan's assumption 3 established the docs-only exemption that no longer applies]`
18. Deny rules across settings scopes union rather than override, so no lower- or higher-precedence file un-denies a user-scope entry. The opt-out is therefore editing the tracked file — the tradeoff §43 says every key in that file already carries. `[verified: pre-implementation gate check 7]` A project-scope `permissions.allow` entry, a project-local `permissions.allow` entry, and plain omission were each tried in turn against a live user-scope deny; `ScheduleWakeup` stayed absent in all three. This claim also sets the precedent future deny entries in that table are read against.
19. Other stow consumers' `/loop` usage is not observable from this corpus. `[verified: E3's corpus is four transcript roots on this machine]` — the blast-radius argument rests on the deny being reversible in one line plus a documented migration note, not on a claim about other consumers' behavior.
20. Ship only if the deny fully prevents the call rather than merely changing the error. `[engineer-verified]`
21. The engineer has been told E4's verdict and the gate's observed `/loop` behavior (row 5), and has accepted the loss of self-paced `/loop` — including the one observed `CronCreate`-substitution caveat — for every stow consumer. `[engineer-verified: accepted]` — see **Open confirmation** above.

**Mechanisms:**

| Mechanism | Justification | Anchor |
|---|---|---|
| `"ScheduleWakeup"` appended to `permissions.deny` in `claude/.claude/settings.json` | The only documented way to hide a built-in tool, and the only mechanism meeting row 20's prevent-not-reject bar; G3 rules out scoping it to non-`/loop` sessions, so a total deny is the shape available. | root; row 1; row 20; G3 |
| The stow-source settings file rather than a personal or project-scoped one | G4 leaves no user-scope personal file, and E3's misfires span four transcript roots, so a project-scoped entry in this repo would reach only `claude-config` sessions while the failure population is every repo. | root; G4; row 19 |
| A one-line status pointer on §41, with its body untouched | §42's extend-without-editing precedent applies to a *narrowed* decision; a *reversed* one needs a marker so the lower-numbered entry isn't acted on as current, and §41's three rejections stay individually correct and worth preserving. | root; row 6 |
| A new §46 recording the reversal, the mechanism-exhaustion lesson, and the `noop` account | Makes the reversal re-decidable and preserves the reason the deny exists if it is ever removed; states E3's bearing ordinally with no figure reproduced. | root; row 6; row 10; G5 |
| No second mechanism for the `noop` misconception — no `CLAUDE.md` line, no skill clause, no advisory nudge | With the tool absent from context the misconception has no expression surface; a layer closing a gap the layer below already closed is the compounding-defensive-layers tell §41 and §42 both name. | root; row 1 |
| `docs/auto-mode.md`'s Hard-floor deny rules table gains a row, and no `CLAUDE.md` line is added | Satisfies `review-permissions` item 23's pair-with-prose requirement at the surface that already hosts every other entry's rationale; a model-facing line would govern a decision the model no longer has, unlike `EnterPlanMode` whose deny is partial. | root; row 1 |
| A sibling declaration pin in `test_hook_alignment.py`, docstring-scoped to declared config state | The repo's same-PR convention for a new convention, and row 13's precedent already established the shape and the honest scoping; row 14 makes one assertion sufficient. | root; row 13; row 14 |
| A live pre-implementation gate before the settings edit | Row 5's cost claim is an inference, and it is the fact the engineer's open confirmation turns on — running the gate first means the engineer decides against an observation, not a prediction. Reuses `.claude/plans/plan-mode-workflow-discipline.md`'s already-passed procedure rather than inventing one. | row 5; row 21 |
| `docs/skills.md`'s `/loop` row carries the caveat; §17 is left unedited | Current-capability description belongs where a reader looks for it; §17 records a `skillOverrides` decision whose value and rationale are both unchanged. | root; row 5 |

**Over-powered-primitive check.** The deny is wider in one dimension than the observed problem — global across every consumer and repo, against one owner's measured misfires. Five lighter primitives were enumerated from the settings and CLI surface and each fails:

- **A scoped `ScheduleWakeup(<specifier>)` deny.** Leaves the tool in context and rejects on call, failing row 20's bar; and G3 means no specifier grammar could distinguish a legitimate `/loop` call anyway.
- **A project-scoped deny in this repo's `.claude/settings.json`.** Genuinely lighter — zero effect on other consumers — but reaches only `claude-config` sessions, while E3's misfires span four roots (row 19).
- **`skillOverrides: {"loop": "off"}`.** Has no effect on built-in tools, so it cannot reach `ScheduleWakeup` at all (E2).
- **`disableModelInvocation`.** Exists only for skills; E2 confirms no built-in-tool analog.
- **`--disallowedTools ScheduleWakeup` in a wrapper script.** Lightest of all, but reaches only wrapper-started sessions — the same defect that sank it for `EnterPlanMode` at `.claude/plans/plan-mode-workflow-discipline.md:118-120`.

The two *heavier* primitives are already-rejected: a `PreToolUse` gate costs a script, a pytest module, a settings registration, and a `docs/hooks.md` entry against one JSON line, and still only rejects rather than prevents (row 20) with no `/loop` predicate available (G2); a `CLAUDE.md` line is instruction-tier only, and this repo's own 0/70 plan-mode measurement is the evidence against relying on stated intent to bind harness behavior.

**Open confirmation — the plan cannot proceed past the pre-implementation gate without it.** The pre-implementation gate has run (Verification step 1, all 7 checks), so this replaces inference with the engineer's own direct observation. Presented, in one turn:

- E4's finding that no realistic recurring-workflow use case for `/loop` exists in this repo's pipeline (`ci-watch.sh`, the strongest candidate, is built to avoid polling).
- The gate's observed `/loop` behavior under the deny (row 5): 3 of 4 runs disclosed the missing tool honestly with no fabricated workaround; 1 of 4 substituted `CronCreate` as a ~20-minute-cadence fallback heartbeat without asking first.
- That fixed-interval `/loop <interval> <prompt>` survives intact, confirmed directly.
- That the entry ships to every stow consumer with a tracked-file edit as the only opt-out (row 18), confirmed directly against three override attempts.

Do not settle this by inference from the corpus's zero `/loop` invocations — that measures this owner's four roots, not the consumer population (row 19).

**Dispatch split.** One phase, written by the session holding this plan rather than dispatched to `code-writer`. Two reasons:

- `ask-review-permissions.sh` fires an `ask` on the settings write (row 12), and a subagent starts in its dispatcher's permission mode with no way to clear an inherited prompt.
- §46's accuracy depends on E3's disposition analysis and E4's verdict, both of which live only in the dispatching session's context — restating them in a dispatch prompt is `plan-it` Step 5's own named do-not-split condition.

## Critical files

- **`claude/.claude/settings.json`** — append `"ScheduleWakeup"` to `permissions.deny`, immediately after `"EnterPlanMode"`, keeping the two bare tool-name entries adjacent and last in the list. Change nothing else in this file; in particular do not touch `skillOverrides.loop` (still `"name-only"`) or `model`. **Reuse:** the `EnterPlanMode` entry's placement and bare-name form.
- **`claude/.claude/hooks/tests/test_hook_alignment.py`** — add `test_schedulewakeup_stays_denied_in_settings` immediately after `test_plan_mode_entry_paths_stay_closed_in_settings` (ends line 304). Assertion shape: `settings = json.loads(_SETTINGS_PATH.read_text())`, then `assert "ScheduleWakeup" in settings.get("permissions", {}).get("deny", [])` with a failure message naming the file and stating that out-of-`/loop` wakeup scheduling is no longer prevented. Docstring must scope the claim to *declared* config state, not harness enforcement, and point at this plan's pre-implementation gate for the live verification — mirroring the sibling's docstring at lines 284-292. **Reuse:** `_SETTINGS_PATH`, already module-level; the sibling's docstring scoping language.
- **`docs/auto-mode.md`** — one row in the Hard-floor deny rules table (after the `EnterPlanMode` row, line 91). Content contract, matching that row's density: what it closes (out-of-`/loop` wakeup scheduling used as a wait/poll while backgrounded work runs), that it removes the tool from the session rather than blocking a call pattern, and what stays unaffected (fixed-interval `/loop <interval> <prompt>` via `CronCreate`). **Reuse:** the `EnterPlanMode` row's clause order.
- **`docs/design-decisions.md`** — two edits:
  - Insert one status line directly under the §41 heading: that §46 supersedes its conclusion because its mechanism survey did not reach the settings layer, and that its three rejected mechanisms stand as recorded. Do not rewrite §41's body, its Revisit list, or its Sources.
  - Append `## 46. <title> (2026-09-03)` after §43's last Sources bullet. Content contract — one element each:
    - The mechanism and the doc line it rests on, quoted verbatim (row 1).
    - Which of §41's three Revisit conditions are met and which are not, each with its reason (rows 6-8).
    - The mechanism-exhaustion lesson.
    - The `noop` account (row 10), with an explicit note that no second mechanism addresses it and why.
    - The blast-radius statement: every stow consumer, self-paced `/loop` degraded but not silently, fixed-interval unaffected, opt-out is a tracked-file edit — all three verified directly by the pre-implementation gate, not inferred (rows 5, 18, 19).
    - The gate's observed `/loop` behavior, per row 5's exact split — first-party test data generated during this plan's own preparation, not corpus-derived, so it carries none of G5's redaction constraint and can be stated in full.
    - A **Revisit** paragraph naming the falsifiable conditions (below).
    - Corpus evidence stated ordinally with no count, ratio, median, or duration (G5).
    - `### Sources`: the permissions doc, §41, §17, §43, `claude/.claude/settings.json`, and this plan file.

    **Reuse:** §42's Revisit-paragraph shape and its cite-a-sibling-entry-as-plain-`§NN` style.
  - §46's **Revisit** conditions to write: the model substitutes a worse wait mechanism for the removed tool *in production* (repeated `ListAgents`/`TaskOutput` polling, a `Bash sleep`, or a recurring `CronCreate` call — E3 measured zero `CronCreate` corpus-wide, and row 5's gate already observed exactly this substitution once, so a production recurrence is corroborating signal, not a first instance); a genuine `/loop` need arises in this repo's pipeline, which E4 found none of; a stow consumer reports a silently-truncated self-paced `/loop` — no longer the *expected* failure mode per row 5's split, but still worth watching since row 19 concedes consumer usage is unobservable; or `ScheduleWakeup` gains its own out-of-`/loop` validation upstream, which moots the entry.
  - §46 must also state that **reversal is not friction-free**: removing the line re-triggers `ask-review-permissions.sh`'s `ask` decision and a `review-permissions` re-review, exactly like the forward edit (row 12). "One line to revert" is the diff size, not the process cost.
- **`README.md`** — widen the `permissions.deny` description at the settings.json bullet ("a `permissions.deny` hard floor for `sudo` and secret-file reads") and the Hard-floor rules bullet ("hard-blocking `sudo` and well-known secret-file reads") to also name tool-availability entries, pointing at `docs/auto-mode.md` for the list. Both phrases predate `EnterPlanMode` and are already inaccurate for it, so this closes existing drift rather than only describing the new entry; it is a one-line accuracy fix in a file this PR's own table edit summarizes, not scope creep. **Reuse:** the settings.json bullet's existing parenthetical cross-reference to the Auto mode section.
- **`docs/skills.md`** — amend the `/loop` row in the name-only table (line 82) with the capability caveat: fixed-interval invocation works, the self-paced no-interval mode is degraded because `ScheduleWakeup` is denied, with a pointer to §46. Leave §17 and the surrounding budget prose unedited.
- **`CHANGELOG.md`** — one entry under `[Unreleased] / Changed`, carrying a **Migration** clause with three required elements:
  - The opt-out, stated at consumer altitude rather than repo altitude. For this repo a revert is one tracked line; for a *consumer* the equivalent is a local edit to a stow-symlinked tracked file that `git pull` will conflict with or re-clobber, and G4 establishes no user-scope `settings.local.json` exists to hold it instead. Say that plainly rather than implying parity with a one-line revert. Gate the wording on check 7's result (row 18).
  - **Launch timing:** the change is live on `git pull` with no re-install, but takes effect from the next `claude` launch, not the current session. Without this, "live on `git pull`" reads as immediate to a consumer mid-session — the same ambiguity the `disableArtifact` entry already resolved with this clause.
  - What survives: fixed-interval `/loop <interval> <prompt>` is unaffected; only the self-paced no-interval mode degrades.

  **Reuse:** the `EnterPlanMode` entry at line 15 for structure; the `disableArtifact` entry at line 27 for both the opt-out phrasing and the launch-timing clause.
- **`.claude/plans/prevent-non-loop-schedulewakeup-calls.md`** — this plan, committed per `plan-it` Step 7. Subject to the same G5 redaction constraints as §46: it ships in the same PR.

## Verification

1. **Pre-implementation gate — run before the settings edit, and before asking the engineer to confirm.** Follow `.claude/plans/plan-mode-workflow-discipline.md` § "Pre-implementation gate — run, passed" for setup shape: a **full copy** of the stowed settings file (not a minimal one), a **control tool** added to the same deny list so a negative result is attributable, and **tool calls demanded rather than a tool inventory** since self-reported tool lists are unreliable.

   **Run the gate at user scope as primary.** The precedent gate ran in a scratch *project*-scope `.claude/settings.json`, but stow lands this entry at *user* scope (`~/.claude/settings.json`) — that is the scope it actually ships to, so it is the scope checks 1–5 must be observed at. Run the project-scope copy afterwards as a comparison only. Do not invert this: spot-checking one behavior at the deployment scope and four at a scope the entry never occupies leaves the four unverified where it counts.

   1. The control tool must be uncallable — failing this invalidates everything below.
   2. `ScheduleWakeup` must be uncallable, and the failure must read as an absent tool rather than a permission denial (row 20's bar).
   3. `/loop 2m <prompt>` must still schedule — confirms the `CronCreate` path is untouched.
   4. A bare `/loop <prompt>` (self-paced) must be run **at least 4 times** and its actual iteration behavior observed and recorded on each run — this is what converts row 5 from inference to fact. A single pass is not enough: the first pre-implementation gate observed an unprompted `CronCreate`-substitution outcome in roughly 1 of 4 runs, so a re-run following this check as a single pass has a real chance of missing that behavior and reporting the gate clean when it isn't. If a re-run's split diverges from row 5's 3-honest/1-substitution baseline, update row 5 — the sole canonical site — and the Open Confirmation bullet, the only other site that restates the figure rather than pointing at row 5.
   5. `Agent`, `ListAgents`, and `TaskOutput` must all remain available.
   6. Repeat checks 2–5 at project scope and record any divergence from the user-scope run. Justification is direct, not cited: the two scopes are distinct precedence levels in Claude Code's five-level order (§43), and this entry ships to only one of them. Do **not** cite `review-permissions/REFERENCES.md` for a scope-parity precedent — its lines 62-65 record a different and narrower fact (subagent permission inheritance does not honor project-scope `.claude/settings.json` for *allow* entries), and the plan-mode precedent's identical citation is wrong for the same reason.
   7. **Cross-scope override test, resolving row 18.** With the deny at user scope, attempt to re-enable `ScheduleWakeup` from each lower-precedence settings file in turn — a project `.claude/settings.json` and a project-local `.claude/settings.local.json` — via an `allow` entry and via omission. Confirm the tool stays absent. This is what licenses the "editing the tracked file is the only opt-out" claim in `CHANGELOG.md` and `docs/auto-mode.md`; without it that claim ships unverified. If any lower-precedence file *does* re-enable the tool, row 18 is false — say so in the CHANGELOG and name the working per-scope opt-out instead.

   If check 2 fails, drop the deny entirely and re-open the design — do not fall back to a scoped rule or a hook, both of which fail row 20.
2. **Engineer confirmation** on the Open confirmation above, carrying check 4's observed result. Blocking.
3. `git grep -n ScheduleWakeup -- claude/ docs/ README.md scripts/` — resolves row 4; expect matches only in prose, plan files, and eval fixtures.
4. `.venv/bin/python3 claude/.claude/scripts/select-tests.py` — the repo's documented scoped command; expect the hooks and skills test directories via the settings-file and `docs/` mappings (row 15).
5. `.venv/bin/ruff check claude/.claude/` — the new test is the only Python touched.
6. **Negative check on the new pin:** temporarily remove `"ScheduleWakeup"` from `permissions.deny`, confirm `test_schedulewakeup_stays_denied_in_settings` fails with its intended message, then restore. A membership pin that never fails is not enforcement.
7. **Redaction pre-flight before staging** — read the added §46 block and the `CHANGELOG.md` entry back and confirm no count, ratio, median, range, or duration from the corpus survives (G5), alongside the standard structural checks `deny-private-project-refs.sh` enforces. The G5 constraint is reviewer discipline only; nothing mechanical catches it.
8. `/code-review` before the commit. The settings edit triggers `ask-review-permissions.sh` (row 12) and the repo `CLAUDE.md`'s bare-deny rule, so `review-permissions` runs — expect checklist item 23 to be raised and answer it with the `docs/auto-mode.md` row plus the reasoning above for why no `CLAUDE.md` line accompanies it.
9. `/plan-review` on this plan before it is presented, per `plan-it` Step 6.

## Out of scope

- **Flipping `skillOverrides.loop` from `"name-only"` to `"off"`.** Tempting given E4's zero-use finding, but fixed-interval `/loop` still works fully, §17 deliberately preserved it, and `name-only` already costs zero description budget — so the flip buys nothing and re-opens a decision this plan does not need to touch.
- **Denying `CronCreate`.** It is the fixed-interval `/loop` path with zero observed misuse corpus-wide; denying it would remove `/loop` entirely rather than removing the misfire.
- **A `PreToolUse` gate, a `PostToolUse` advisory nudge, and a `CLAUDE.md` line.** All three rejected in §41 on grounds that still hold, and two of the three now also fail row 20's prevent-not-reject bar. Named in §46 rather than merely omitted, so a future session does not re-derive them.
- **A `subagent-delegation/SKILL.md` anti-polling line.** Declined by the engineer directly during the prior plan's review, and the deny removes the surface it would have guarded.
- **A `docs/cost-ledger.md` row for the wasted re-invocations.** Two independent reasons, and the second is the stronger one: the figure would be corpus-derived and unpublishable under G5, *and* that file is schema-fixed and mechanically populated by `transcript-analysis.py cost-ledger --record` on a weekly cadence, so a one-off qualitative row does not fit its shape regardless of provenance. Its free-text `note` column on the next `--record` run is the right vehicle if a marker is ever wanted; §46's dated entry is the durable baseline in the meantime.
- **An enumeration test pairing every bare tool-name `permissions.deny` entry to a `docs/auto-mode.md` Hard-floor table row.** The repo already enforces the analogous `skillOverrides` ↔ `docs/skills.md` pairing automatically (`test_skill_overrides_documented_in_docs_skills_md`, `claude/.claude/skills/tests/test_skills.py:1770`), while the deny ↔ table pairing rests on manual diligence plus `/code-review`'s item-23 check. Deferred rather than built here: this PR is the second instance of the pairing, not the one that establishes it — `EnterPlanMode` did — so the same-PR test-enforcement convention does not fire on this diff. Worth a follow-up issue.
- **Re-deriving E3's figures from a public-only corpus to make them publishable.** Real work with no consumer here — §46 states its case ordinally.
- **The upstream follow-up.** E3 is materially stronger evidence than what sits on #80350, #88260, and #88205, and the `noop`-read-as-a-mode mechanism is genuinely actionable for Anthropic. But this plan authorizes no post. Two things separate cleanly and the engineer decides both: the **mechanism** (the tool's `noop` field being read as a do-nothing mode, the uniform `{delaySeconds, noop, reason}` argument shape, the backgrounded-dispatch trigger context) is derivable from the tool schema plus a single reproduction and carries no corpus provenance; the **counts, ratios, and context medians** are corpus-derived and inherit the private half under G5, notwithstanding that the prior plan's own G3 permitted aggregates upstream. That tension between the prior plan's permission and the repo `CLAUDE.md`'s provenance rule is unresolved and is not this plan's to settle — but it blocks any future upstream post regardless: a session picking up this follow-up must not read the prior plan's G3 as license to post corpus-derived counts, ratios, or medians until `docs/design-decisions.md` resolves whether G3's "not publishable in this repo" reading is repo-scoped or disclosure-scoped.
- **A `transcript-analysis` detector for the pattern.** The prior plan declined it as monitoring rather than prevention; the deny makes the thing it would count unformable.
