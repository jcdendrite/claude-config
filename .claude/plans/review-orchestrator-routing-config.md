# Route /code-review rounds through review-orchestrator by config key

## Context

Goal: add a machine-level `review_orchestrator_routing` config key so that a `/code-review` round runs through the `review-orchestrator` agent without the engineer having to ask for it each time.

Ask: "Yes definitely want the review orchestrator routing config key because the existing setup with reminding the agent to use it hasn't been working for me (because I forget to ask)". Mechanism: "Wait sessionstart seems like the wrong call. It's not a pattern we've used much, and that reminder applies to all main sessions even ones that don't run code review. Why would we not do this at invocation time?" Typed `/code-review`: "By “typed” code review you mean user-invoked? I don't care about that. If a user really wants to do a one off code review they really should use the review-pr skill instead. And if they want to use the one off code review skill nonetheless then the orchestrator should NOT be used because the user probably wants it done in the main thread. So this is by design". Default: selected label "Default false, you set it true (Recommended)". Dogfood counting: selected label "Count gate-routed rounds (Recommended)". Editing `/ready-for-review` and `/review-pr` to pass the `unrouted` escape: selected label "Edit both skills (Recommended)".

Why now: PR #1257 made orchestrator rounds visible to the transcript analyses, which the dogfood needed. The agent's `TRIGGER` and the `subagent-delegation` exception both say it runs only when the user explicitly asks, so routing depends on the engineer remembering to ask.

Prior decision to reconcile: `docs/design-decisions/review-orchestrator-gate-release-decoupling.md` (line 54) names a boolean `review_orchestrator_routing` key, default `false`, with a SessionStart hook emitting one `additionalContext` line. The engineer rejected SessionStart as the delivery point (quote above). The design decision (lines 15 and 24) also rejects an identity-based marker deny and a dispatch-target hook, so the invocation-time mechanism must be weighed against those.

## Approach

Add a per-account config key, `review_orchestrator_routing`, off by default, and a new `PreToolUse` gate on the `Skill` tool, `require-review-orchestrator-routing.sh`. While the key is on, the gate denies a main-session `Skill(code-review)` call unless its args begin with the word `unrouted`. The deny text sends the session through the existing `review-orchestrator` protocol in `subagent-delegation` for that round.

The gate lets three kinds of call through:
- every subagent's call, including the agent's own;
- every call whose args begin with the word `unrouted`;
- every call from an account whose key is off or cannot be read.

A key-off consumer's routine rounds run as they do today. Three things change on every account: the args text of the diff-file passes, with `code-review` reading the path that follows the new word (rows 35, 50); the HALT step, which now carries `unrouted` through the rest of its loop; and the request parenthetical at `subagent-delegation/SKILL.md:195`, which names the routing gate.

**Why deny the `Skill` call.** The routine round is the session's own `Skill(code-review)` call (row 4). That call is the last point where a hook can act before the skill body loads (row 8). Denying it keeps the body out of the main session. The record names the skill body as one of the three things the agent exists to keep out (`review-orchestrator-gate-release-decoupling.md:5`). Any delivery that allows the call loads the body first. The deny text carries only the routing instruction. The protocol stays in its one home, `subagent-delegation/SKILL.md` § "Exception: gate/review loops stay orchestrator-driven" (record:17).

**Alternatives set aside.**
- **SessionStart `additionalContext`, the record's line-54 plan.** The engineer rejected it because it reaches every main session, including ones that never run a review (row 2).
- **A routing step at the top of `code-review/SKILL.md`.** It would read the key through `config-get.sh`, the way the fix-plan pilot region does at `code-review/SKILL.md:379`, and it would also catch a typed `/code-review` (row 5). It fails on two counts:
  - The body loads before the step can route.
  - It spends headroom in a file at 496 of its 500-line cap (row 18).
- **`PreToolUse` allow with `additionalContext`.** The body still loads, and evidence in this repo disagrees on whether the harness delivers that field (row 19).
- **`UserPromptSubmit`.** It sees only typed prompts, never the routine `Skill` call (rows 4, 5).
- **A line in `TRIGGER`, the agent description, or CLAUDE.md.** Static text is unenforced, and the skill body still loads. CLAUDE.md § "Shipping" has a session read a machine key through `autonomous-shipping-active.sh`, so text can read a key, but nothing makes the session do it. That is the remembering the engineer reported failing (row 1). The record also holds back widening `TRIGGER` (row 20).
- **`permissionDecision: "ask"`.** It prompts the engineer every round.
- **Routing a typed `/code-review`.** It stays unrouted by design (row 48). A one-off review belongs to `/review-pr`, and a user who types `/code-review` anyway probably wants it in the main thread. A `plan-architect` consult had recommended leaving typed rounds inline (row 49). The harness expands a typed command without a `Skill` call (G1), so the gate never sees one, and the plan adds no prompt hook.

**This is not a hook the record rejects.** The record rejects an allowlist hook on the agent's own `Agent` calls, because it would deny the `plan-architect` dispatch that `/code-review` makes (record:24, 46). It also rejects a deny on `marker.sh write` keyed on the agent's identity (record:15). This gate acts on the main session's `Skill` calls instead. It allows every call that carries an `agent_id`, and it constrains nothing the agent can call, read or write. Neither objection applies to it (row 24).

**The escape is a leading `unrouted` word, not any args.** Routine rounds commonly carry args, as descriptive prose that often embeds a diff-file path (row 14), so the presence of args cannot be the escape. A path in args does not mark a cumulative pass either, because `code-review/SKILL.md:290` tells the skill to reuse its invoker's diff-file path. The escape must be a signal that no routine round emits by habit. Options weighed:
- **(a) A leading `unrouted` word in args, passed by every flow that must stay inline. Chosen.** A flow passes args "unrouted", or args "unrouted <path>" with the path last when the round has a diff-file path (row 50). It needs no state read. A caller emits it only on purpose, and no routine round leads its args with it (row 34, checked before implementation). The word is `unrouted`, not `inline`, because `code-review/SKILL.md` already uses `inline` as a fixed value (row 61).
- **(b) Allow while this session holds a live `ready-for-review` active marker.** It would reuse `_lib_active_bypass_marker_live`, with no new marker surface. Rejected for three reasons (row 36):
  - The marker is live from step 0 through step 9. Every per-commit fix round inside the loop would therefore run inline too, and those are routine rounds that write the code-review marker.
  - `/review-pr` has no active marker.
  - The HALT fallback has no marker, so (b) would still need a token.
- **(c) Lighter signals, each of which fails:**
  - Any non-empty args: routine rounds carry them (row 14).
  - An absolute path in args: routine rounds embed one too (`code-review/SKILL.md:290`).
  - This session's recorded cumulative-review or review-pr artifact: it outlives the pass, so later routine rounds would escape too, and reading it adds a repo-hash computation to every call.
  - A re-allow within a time window: no source grounds the timeout, and a retry with no args would get past the gate.
  - A marker the HALT step writes: it adds a new `marker.sh` surface for a rare path.

Four main-session rounds must stay inline, and each carries `unrouted` (row 15):
- a `/ready-for-review` cumulative pass, which must not write the code-review marker;
- a `/review-pr` pass, which runs under a no-fix, no-marker override;
- the HALT fallback in `subagent-delegation`;
- an inline round the user asks for.

The HALT case covers the rest of that review loop until a clean round writes the marker (row 54). The user-asked case covers only the round it names.

The HALT step in `subagent-delegation` gains args "unrouted". `/ready-for-review` step 3 and `/review-pr` step 5 pass args "unrouted <path>" themselves, for their diff-file passes only (rows 44, 46, 50). A pass that follows its edited step therefore meets no deny, though whether a session follows it is observable only after merge (row 13). The deny text still names both passes, so a pass that loses its `unrouted` costs one deny and one retry, provided the session follows the deny text's inline exception for these passes. Only that prose keeps the pass out of the agent, which would dispatch `code-writer` and write the marker against the pass's override (row 15), so the dogfood counts a denied block inside either pass as a lost escape, not a routed success. Both edits reach every stow consumer, key on or off (G4).

**The word never reaches a reviewer as the path.** `code-review` defines no args. Its diff-artifact note tells the skill to reuse its invoker's diff-file path (`code-review/SKILL.md:290`). Without a rule for the new word, a session could hand `comment-discipline-reviewer` the word instead of the path. That would hit every consumer's cumulative pass and `/review-pr` pass, key on or off, and that reviewer carries no `Bash` to recover the diff. The plan adds one in-line clause to that note, which the architect consult recommended (item 25): in args that begin with `unrouted`, the path is what follows the word (row 35, M14). Two options were set aside:
- A pre-merge probe of the unchanged skill with args "unrouted <path>" observes one session's reading, not every session's. It leaves the risk in place even when the probe passes.
- Landing the four `unrouted` edits before the gate still ships the risk to key-off accounts. The edited text would also name a gate that does not exist yet.

If Verification step 6 still shows a session handing over the word, the fallback is the **revert set**, stated here once. It has three parts:
- item 18's HALT args edit (:201). The request parenthetical stays;
- items 23 and 24, each a one-line change;
- their pins: item 19's protocol-section parameter for the HALT edit, the two `test_code_review_args_carry_unrouted_before_path` parameters for items 23 and 24, and item 11's span-coupling assertion, which turns red once those sentences lose the span.

Item 25 and its pin stay. The revert also adds a new CHANGELOG line and a dated line in the new record recording it, because both are history. The revert restores the HALT and diff-file-pass text key-off consumers read today. The parenthetical is new text on every account and stays. A key-on account also sets the key to `false`.

**One deny each time the session enters routing.** The gate denies a call only when its args do not begin with `unrouted`. After a deny, the session either dispatches the agent, whose own call carries `agent_id`, or re-invokes the skill with args "unrouted". Each later round of a routed loop is a dispatch, so it meets no deny. The deny text ends with the sentence `deny-no-op-dispatch.sh:128` ends with, verbatim, which tells a subagent to report a denial to its dispatcher (row 55). The count:
- A loop the gate routes from its first round pays one deny, at that round.
- A loop led by a typed `/code-review` pays exactly one deny, at its first re-run, because the typed round never passes the gate.
- A HALT fallback costs no further deny for its loop, because the rest of the loop passes `unrouted` (row 54).

Four further cases need stating:
- **A fix re-run after a user-asked inline round or a typed `/code-review` is routed on purpose (row 54).** Either round can lead to a fix. The session's re-run then reviews the fix as a routine staged-diff round, which is the round the key exists to route. A user's request for an inline round covers the rounds it names, and row 48's quote governs the typed round itself. Such a loop ends as any review loop does, when a clean round writes the marker.
- **After a HALT fallback, the rest of that review loop runs inline with args "unrouted" until a clean round writes the marker (row 54).** The cause of a HALT recurs from round to round, so routing each re-run would pay one deny and one failing dispatch every time. An agent call without `agent_id` is one such cause: the agent reports the deny as a `HALT:` entry rather than dispatching itself (row 39). The loop pays one deny and one failing dispatch, then reviews every later round inline. The next review loop meets the same `HALT:` once more. The exit is setting the key to `false`, effective on the next `Skill` call.
- **A deny that repeats on the `unrouted` retry has two possible causes.** The gate cannot read the args (row 33), which takes a change to the harness payload. Or the session copied the quoted spelling from this prose, so its args begin with a quote or a backtick, and the gate denies that by design. The same key flip recovers both, and so does a typed `/code-review`, which expands without a `Skill` call.
- **A dispatch the harness refuses (an unlinked or unknown agent, or a session with no `Agent` tool) keeps the protocol's existing stop (row 38).** The session relays why, and nothing runs until the engineer acts. The refusal lasts for the session (unverified), so each new review loop pays one deny plus one refused dispatch. The exits are re-running the installer for an unlinked agent, setting the key to `false`, or asking for an inline round. The plan adds no fallback for this case: it is rare, one command recovers it, and it fails visibly. A fallback would need a stop-clause exception, a carve-out for hook denials and rejected dispatches, a cause clause in the deny text, and their pins. `./install.sh` stows into `$HOME` only, so for a non-personal account re-run the installer that stows the packages `stow-packages.sh` lists into that account's config dir.

**Reading the key and the payload.** The gate calls `_config_enabled review_orchestrator_routing`, the form every hook uses (row 10). Only exit 0 routes. The tool-name and `agent_id` checks read globals the shared parse already set (row 37), and the key read follows them. All three run before any further `jq` read, so a key-off `Skill` call costs the parse every `_lib.sh` gate pays, plus one config read. A failed skill or args read after a successful parse allows the call, and item 11 tests both (row 60). A malformed payload still denies, because every gate shares that contract (row 11).

**The deny label comes from `DENY_GATE_LABEL`.** The gate declares `DENY_GATE_LABEL="review-orchestrator routing"`, and `_lib_emit_deny` writes `Blocked by review-orchestrator routing gate: ` ahead of the reason. The reason carries no label of its own. The label joins `_DENIAL_HOOK_LABELS` and `_BOOTSTRAP_FALLBACK_HOOKS`, so the transcript analyses attribute the deny to this gate rather than filing it as `unmatched` (row 17).

**`TRIGGER` and the protocol sentence stay explicit-request.** The record holds back widening `TRIGGER`. Its plan for a key-on message made no `TRIGGER` change (row 20). The global CLAUDE.md already treats a machine-level key as the engineer's standing request (row 21). So the agent description stays as is, and so do the three `test_agent_roster.py` checks on it. The `subagent-delegation` sentence keeps its pinned phrase `only when the user explicitly asks` and gains a parenthetical naming the gate whose denial is that request. That makes the skill the one home for the claim.

**Skill text names the gate by its deny label, not its file.** Where a skill edit names the gate, it says "review-orchestrator routing", the words a session reads in the deny prefix. The hook's filename would change nothing a session does, and it would add three more homes for the gate's name. Verification step 5 checks that no edited sentence names the file.

**Dogfood.** The key ships off by default, and the record's dogfood still gates flipping the default (row 20). A round the gate routes counts toward the dogfood, and the deny text joins the sources line 61 names (rows 22, 31). The engineer confirmed two more criteria by selecting "Include both (Recommended)" (row 51):
- Every key-on main-thread `Skill(code-review)` block the gate did not deny must have args that begin with `unrouted` and must follow one of the four inline cases. A HALT case covers every later round of its loop until a clean round writes the marker (row 54), so those later rounds are not leaks. The user-asked case is scored per round. An un-denied block with no leading `unrouted` means the gate did not run. One that leads with `unrouted` but follows none of the four cases means the escape was misused, including carry past the clean round that ended a loop. The same check counts a denied block inside a `/ready-for-review` cumulative pass or a `/review-pr` pass as a lost escape, subtracted from the routed-round count instead of counted as a routed success. A lost escape followed by a `review-orchestrator` dispatch, or any lost escape inside a `/review-pr` pass, fails the dogfood; a lost escape followed by an `unrouted` retry is informational.
- Before the default flips, the per-branch `round $` total over `code-review` rounds only (`review-round-cost --skill code-review`) is compared between key-on branches and comparable key-off branches on the same account, meaning a comparable review-loop count or diff size. The all-skill total is not used: it also sums plan-review and ready-for-review spend the key does not change, and including it would dilute the signal. The comparison bounds the added review spend, and any record states only its direction (adopted or declined): no dollar value, ratio, bound or percentage, and not how many accounts enable the key. It does not measure the main-thread context the agent saves. The denied round-open (row 16) also makes the key-on per-round mean read low and rounds-per-branch read high.

The shipped analyses cannot run the first check alone, because `review_rounds.py` never extracts args (`review_rounds.py:140-143`). The record therefore names a manual local instrument and its recipe (Critical files 20). The dogfood publishes only booleans. The new record states the change, and a partial-supersession line on the old record points to it.

**Before turning the key on, the engineer should know:**
- Re-run `./install.sh` unless the account's `hooks/` and `agents/` directories are folded symlinks. `./install.sh` stows into `$HOME` only (`docs/scripts.md`, the `review-ledger.sh` Install bullet), so for a non-personal account re-run the installer that stows the packages `claude/.claude/scripts/stow-packages.sh` lists into that account's config dir. Then use `readlink -f` on each path (`ls -l` shows no arrow through a folded directory) to confirm that `~/.claude/hooks/require-review-orchestrator-routing.sh`, `<config-dir>/hooks/require-review-orchestrator-routing.sh` and `<config-dir>/agents/review-orchestrator.md` each resolve into the checkout. `settings.json` names the `~/.claude` path, which differs from `<config-dir>` on a non-personal account (rows 38, 42).
- Start a new session after the pull and after `./install.sh`. A session started earlier may not load the gate's registration (row 52).
- Every routine round on that account then runs through the agent before the dogfood has observed two things (row 23):
  - the four claims the record lists as unverified (record:68-72);
  - round numbering across consecutive dispatches.

  A failure there surfaces as a `HALT:` entry or a protocol stop.
- To enable the key, set the exact bare line `review_orchestrator_routing = true` in each account's `<config-dir>/claude-config.toml`. Change the scaffolded `review_orchestrator_routing = false` row to `true` if one exists, or place the new line after it, because the last row for a key wins (`_config.sh:238`). Then confirm that `~/.claude/scripts/config-get.sh review_orchestrator_routing` exits 0, from a shell or session whose `CLAUDE_CONFIG_DIR` is the account being enabled. Each hook run is a new process that rereads the key, so a key flip in either direction takes effect on the next `Skill` call, with no restart. Setting the key to `false` restores inline rounds.
- `migrate-legacy-config.sh` writes an explicit `false` row for a key with no legacy file. Flipping the default after the dogfood therefore needs a row migration (`docs/config-file.md` § "Migration"). That migration cannot tell a scaffolded `false` from a deliberate one, so it must be a consented rewrite or a new key.
- A typed `/code-review` runs in the main thread by design. Use `/review-pr` for a one-off review (row 48).
- After a HALT fallback, the rest of that review loop runs inline with args "unrouted" until a clean round writes the marker. A fix re-run after a user-asked inline round or a typed `/code-review` is still routed (row 54). If the agent's own call ever arrives without `agent_id`, each new review loop costs one deny and one `HALT:` dispatch before its inline fallback. Setting the key to `false` stops that.
- `/ready-for-review` step 4 should not halt on a gate-denied block, because `skill-fidelity-reviewer`'s spawn check is branch-wide and covers only completed passes (row 53). If a reviewer misapplies that check (row 62), step 4's stated escape clears it: state the deviation with a rationale, namely that the block is a routing-gate denial and the round ran in the dispatch that followed. The follow-up in Out of scope tracks the consumers.
- Each time a review loop starts through the gate, the transcript analyses gain one extra `code-review` round-open with no ledger block (row 16). Until the follow-up in Out of scope lands, check round numbering with `review-ledger.sh show`. On that account, the round-number-mismatch counter in `author-outcome` overcounts.
- A plugin skill whose final name component is `code-review` is routed too. `activate-handoff-bypass.sh` accepts the same name collision.

### Assumption ledger

**Root problem:** An engineer who wants every main-session `/code-review` round to run through `review-orchestrator` must ask for it every round. Both the agent's `TRIGGER` and the `subagent-delegation` protocol require an explicit request, and asking every round fails. The plan gives that engineer a per-account key, off by default. Its routing reaches the session at the moment it invokes `/code-review`, and a key-off consumer's rounds run as they do today.

**Givens:**
- **G1 — A typed `/code-review` expands without a `Skill` tool call (row 5).** The harness owns slash-command expansion.
- **G2 — A hook payload carries `agent_id` only on a subagent's tool call (row 7).** The vendor's hooks contract imposes this.
- **G4 — The stowed skills and the user-scope `settings.json` resolve into the main checkout.** So this branch's skill-text edits and its gate registration reach a live session only after merge and pull. Facts about the hook payload can still be captured before merge (Verification step 0). The stow distribution model owns this.

**Mechanisms:**
- **M1 — Config key `review_orchestrator_routing`.** It takes the same shape as `architect_fix_plan_pilot`: `bool`, default `false`, `config-dir` resolution (per account), no legacy columns, not offered by the install prompt. `anchors: row1, row3, row25, row26`.
- **M2 — New gate `require-review-orchestrator-routing.sh` on the `PreToolUse` matcher `Skill`.** It is heavier than the task's minimum: it is a new gate on a tool no `PreToolUse` hook watches today, and it denies a legitimate call. `anchors: root, row2, row4, row8, row11, row24`. Lighter primitives rejected:
  - A routing step in `code-review/SKILL.md`: the body loads first, and the file is near its cap (rows 8, 18).
  - `PreToolUse` allow with `additionalContext`: the body loads first, and delivery is contested (rows 8, 19).
  - `UserPromptSubmit`: it never sees the routine `Skill` call (rows 4, 5).
  - Static text in `TRIGGER`, the agent description or CLAUDE.md: it is unenforced, the skill body still loads, and it needs the remembering that fails (rows 1, 20).
  - SessionStart: the engineer rejected it (row 2).
  - `ask`: it prompts every round (row 1).
- **M3 — Allow rules, checked in this order.** The gate denies otherwise. `anchors: row6, row7, row10, row14, row15, row33, row34, row36, row37, row50, row59, row60, row61`. It allows a call when:
  1. The tool is not `Skill`.
  2. `agent_id` is non-empty.
  3. The key is not enabled.
  4. The skill's final name component, compared case-sensitively, is not exactly `code-review`, including an empty or unreadable name.
  5. The args, with leading whitespace stripped, are `unrouted` or begin with `unrouted` followed by whitespace, or the args read fails.

  The escape is the lightest signal that separates the inline flows. Signals rejected:
  - Any non-empty args, or a path in args: routine rounds carry both (row 14).
  - A live `ready-for-review` active marker: it exempts the per-commit fix rounds inside the loop, and it covers neither `/review-pr` nor the HALT fallback (row 36).
  - The session's recorded cumulative-review or review-pr artifact: it outlives the pass, and reading it costs a repo-hash computation on every call.
  - A re-allow within a time window: it needs a timeout value no source grounds, and a retry with no args would get past the gate.
  - A marker the HALT step writes: it adds a new `marker.sh` surface for a rare path.
- **M4 — The key is read through `_config_enabled`.** Only exit 0 routes. A failed field read after the parse allows the call. `anchors: row10, row12, row37`.
- **M5 — The deny label comes from `DENY_GATE_LABEL="review-orchestrator routing"`, and the reason carries no label.** The reason names the four inline cases and both args forms, and ends with the sibling gates' subagent sentence verbatim. `anchors: row9, row16, row17, row38, row39, row50, row55, row61`.
- **M6 — Two in-line edits to `subagent-delegation`:** the request parenthetical naming the gate by its deny label, and args "unrouted" in the HALT step, carried through the rest of that loop. Neither adds a line. `anchors: row15, row18, row20, row21, row50, row54, row61, row63`.
- **M7 — No change to the agent description or its test checks.** `anchors: row20, row21`.
- **M8 — Move the skill-name parsing from `activate-handoff-bypass.sh` into `_lib.sh` as `_lib_skill_basename`.** `anchors: row6, row29`.
- **M9 — A round the gate routes counts toward the dogfood.** The plan also adds an un-denied-block check and a key-on versus key-off cost comparison, which the engineer confirmed (row 51). A manual local read measures both and publishes only booleans. `anchors: row22, row31, row51`.
- **M10 — A new decision record, plus a partial-supersession line on the old one.** The record carries the enable line, the known gaps, the dogfood recipe, and a withdrawal form that lists every surface by item number. `anchors: row20, row22, row23, row24, row27, row35, row43, row48, row51, row52, row53, row54, row62`.
- **M11 — The `docs/hooks.md` bullet, tier row and count; a `settings.json` entry with no `statusMessage`; a CHANGELOG entry that names the unlinked-hook symptom and says a missing `jq` now blocks every `Skill` call.** `anchors: row11, row12, row26, row28, row30, row42, row52, row58`.
- **M12 — Register the gate's label, prove that its behavioral deny is attributed to it, and declare the new test's read of `subagent-delegation/SKILL.md`.** This touches `denials.py`, `test_transcript_denials.py`, `select-tests.py` and `test_select_tests.py`, plus the `test_skills.py` pins. `anchors: row17, row40`.
- **M13 — `/ready-for-review` step 3 and `/review-pr` step 5 pass args "unrouted <path>" for their diff-file passes.** Each is an edit inside one line, so its file stays at its 200-line cap. The fix-commit rounds in `/ready-for-review` stay routed. `anchors: row13, row15, row35, row44, row46, row47, row50, row61`.
- **M14 — One in-line clause in `code-review/SKILL.md`'s diff-artifact note says the path follows a leading `unrouted` word.** It closes the misread risk at merge for every consumer instead of observing it afterwards. `anchors: row18, row35, row61`.

**Material assumptions:**

| # | Assumption | Tag |
|---|---|---|
| 1 | Ship the key now, because asking for the agent every round fails | `[engineer-verified: "Yes definitely want the review orchestrator routing config key because the existing setup with reminding the agent to use it hasn't been working for me (because I forget to ask)"]` |
| 2 | Deliver routing when `/code-review` is invoked, not at SessionStart | `[engineer-verified: "Wait sessionstart seems like the wrong call. It's not a pattern we've used much, and that reminder applies to all main sessions even ones that don't run code review. Why would we not do this at invocation time?"]` |
| 3 | The default is `false`, and the engineer sets it to `true` | `[engineer-verified: "Default false, you set it true (Recommended)"]` |
| 4 | The routine round is the session's own `Skill(code-review)` call. CLAUDE.md tells the session to run `/code-review` after writing code, and `require-code-review.sh` denies `git commit` until a review covers the staged state | `[verified: claude/.claude/CLAUDE.md § "Code Review" (read as the loaded global CLAUDE.md); README.md:165]` |
| 5 | A typed `/code-review` reaches the transcript as a `<command-name>` user record with no `Skill` tool_use, so no `PreToolUse` `Skill` hook sees it | `[verified: docs/transcript-analysis.md:409; claude/.claude/scripts/transcript_analysis/review_rounds.py:132-133, 154-161]` |
| 6 | A `Skill` call names its skill in `tool_input.skill`, which can arrive qualified as `plugin:name` or `<dir>:name`. The call's arguments are row 33 | `[verified: activate-handoff-bypass.sh:33, 75-80; review_rounds.py:140-143; claude/.claude/tests/helpers.py:699-711]` |
| 7 | A non-empty `agent_id` marks a subagent's tool call. A main-session call has none, including a `claude --agent` session, which carries `agent_type` alone. Verification step 0 captures whether the `review-orchestrator` agent's own `Skill` call carries `agent_id` | `[verified: _lib.sh:512-513]` (on the agent's `Skill` call: `[verified: Verification step 0 capture, the agent's call carried a non-empty agent_id and agent_type review-orchestrator]`; on the main session's calls: `[verified: Verification step 0 capture, the main session's calls carried no agent_id]`) |
| 8 | A `PreToolUse` deny on `Skill` stops the call before the harness adds the skill body. Verification step 0 captures this against a positive control: an allowed `Skill` call whose body record shows which field links a body to its call | `[verified: Verification step 0 transcript read]` (the control's body record links through `sourceToolUseID`; neither denied call has a body record). The capture hook denied through the exit-0 JSON `permissionDecision: deny` envelope, the channel `_lib_emit_deny` uses, so rows 8, 9 and 16 describe the shipped gate's channel |
| 9 | A denied `Skill` call still leaves its `tool_use` block in the transcript. CHANGELOG.md:11 records this for a denied `Agent` dispatch, not a denied `Skill` call. Verification step 0's transcript read confirms it for `Skill` | `[verified: Verification step 0 transcript read]` |
| 10 | Hooks read keys with `_config_enabled <key>`, which is available once `_lib.sh` is sourced. There are 13 call sites, in 7 hooks plus `_lib.sh`, and no hook runs `config-get.sh`. It returns 0 (enabled), 1 (disabled), 2 (config dir unresolvable), 3 (schema unreadable) or 4 (row missing) | `[verified: grep -nE "^[^#]*_config_enabled [a-z_]+" over claude/.claude/hooks/*.sh this session; _lib.sh:24; _config.sh:962-966]` |
| 11 | A hook that denies is `hook-class: gate`. It needs a tier line on line 3, a tier-table row, a `docs/hooks.md` bullet and `PreToolUse` wiring. It must deny on a malformed or empty payload, a non-object `tool_input`, a missing `_lib.sh` and an absent `jq` | `[verified: test_hook_alignment.py:196-213, 502-517, 1581-1605, 1862-1884, 1902, 2653-2680]` |
| 12 | Gates on `Bash`, `Edit`/`Write`/`MultiEdit` and `Read` already deny on those failures. The new gate therefore adds `Skill` to the tools a machine in that state already cannot use | `[verified: test_hook_alignment.py:2653 (parametrized over every gate); claude/.claude/settings.json:237-441]` |
| 13 | `/ready-for-review` step 3 says "passing it the path" and `/review-pr` step 5 says "over the diff file". Neither names the `Skill` args channel, so neither pass leads its args with `unrouted` today. Whether a session that follows an edited step passes `unrouted` is observable only post-merge, because the session loads skill text from the stowed main checkout (G4) | `[verified: ready-for-review/SKILL.md:82; review-pr/SKILL.md:86]` (an edited step's effect: `[unverified]`) |
| 14 | Routine main-session rounds commonly carry args, as descriptive prose that often embeds a diff-file path. The presence of args therefore does not mark a round that must stay inline | `[verified: a read-only pass over this repo's own local transcripts (no figure carried here); code-review/SKILL.md:290]` |
| 15 | Neither diff-file pass may run through the agent: `/review-pr` runs under a no-fix, no-marker override, and the `/ready-for-review` cumulative pass must not write the code-review marker | `[verified: review-pr/SKILL.md:3, 9; ready-for-review/SKILL.md:82]` |
| 16 | Round detection opens a `code-review` round on any `Skill(code-review)` block, whatever its result. So each review loop the gate routes adds one round-open with no ledger block, provided a denied `Skill` call leaves its block (row 9) | `[verified: review_rounds.py:104-116, 178-217]` (detection ignores the result); the denied-`Skill`-leaves-a-block half rests on row 9: `[verified: Verification step 0 transcript read]`. CHANGELOG.md:11-13 covers a denied `Agent` dispatch only |
| 17 | `_lib_emit_deny` writes `Blocked by <DENY_GATE_LABEL> gate: ` ahead of every reason. `denials._DENIAL_HOOK_NAME_RE` reads the label from that prefix and trusts it only if it is a member of `_DENIAL_HOOK_LABELS`. The tests require every hook that declares a label to have a `_BOOTSTRAP_FALLBACK_HOOKS` row and a member label. They also forbid a hand-written "blocked by" in any deny literal | `[verified: _lib.sh:411-423; denials.py:98-100, 116-128; test_transcript_denials.py:330-344, 617-622, 706-715]` |
| 18 | `code-review/SKILL.md` is 496 lines against a 500-line cap. `subagent-delegation/SKILL.md` is 202 lines against the 200-line default. The length gate denies a commit that grows a file already over its cap, and an in-line edit adds no line | `[verified: line counts this session; check-skill-length.sh:6-13, 111-118]` |
| 19 | Evidence in this repo disagrees on whether the harness delivers `PreToolUse` `additionalContext` | `[verified: nudge-transcript-toolkit.sh:11; _lib.sh:444-465 and its callers require-worktree-for-git-writes.sh:453, require-worktree-for-file-writes.sh:165]` |
| 20 | The record lets the dogfood gate flipping the default. It holds back any `TRIGGER` widening, CLAUDE.md rewiring and README rows. Its planned key-on delivery was a message with no `TRIGGER` change | `[verified: review-orchestrator-gate-release-decoupling.md:54]` |
| 21 | The global CLAUDE.md already treats a machine-level key as the engineer's standing request | `[verified: claude/.claude/CLAUDE.md § "Shipping" (read as the loaded global CLAUDE.md)]` |
| 22 | The dogfood requires rounds "on explicit request" and a session action that "followed from the agent description and the subagent-delegation section alone". A round the gate routes also follows the deny text | `[verified: review-orchestrator-gate-release-decoupling.md:56, 61]` |
| 23 | The record lists four claims as unverified until the dogfood. Each dispatch takes its round number from the Step 0.1 resume path. Whether this numbers consecutive dispatches correctly from inside the agent is unobserved | `[verified: review-orchestrator-gate-release-decoupling.md:68-72; review-orchestrator.md:16-19]` (consecutive numbering: `[unverified]`) |
| 24 | The hooks the record rejects are a dispatch-target allowlist on the agent's `Agent` calls and an identity-keyed deny on `marker.sh write` | `[verified: review-orchestrator-gate-release-decoupling.md:15, 24, 46]` |
| 25 | The new key takes the schema shape of `architect_fix_plan_pilot`. `config-dir-or-home` is reserved for two keys that predate `CLAUDE_CONFIG_DIR` | `[verified: config-keys.psv:28-31, 108; test_config_py.py:77-103]` |
| 26 | Tests enforce registering the key in five places: `config-keys.psv`; the table and three counts in `docs/config-file.md`; the section and heading count of `config-schema-audit.md`; and the count comments in `install.sh` and `migrate-legacy-config.sh`. `config-schema-audit.md:3` also states the key count, and no test pins it. The scaffold tests in `test_config_lib.py` derive from the schema and need no edit | `[verified: test_doc_counts.py:613-655, 724-774; test_config_py.py:60-61, 77-88; test_config_lib.py:1320-1353; config-schema-audit.md:3]` |
| 27 | An overturned record gets an in-place `**Partially superseded by …**` line under its provenance line | `[verified: .claude/rules/design-decisions.md "Supersession"; round3-plan-architect-consult-gate.md:5]` |
| 28 | The tier table in `docs/hooks.md` has 17 plain `cooperative` rows | `[verified: docs/hooks.md:59-93, counted this session]` |
| 29 | Only `activate-handoff-bypass.sh` parses a qualified skill name today. The new gate would be a second user, so the helper moves to `_lib.sh` | `[verified: activate-handoff-bypass.sh:75-80; .claude/rules/bash-unit-test-seams.md]` |
| 30 | A new hook file reaches a consumer whose hooks directory is not a folded symlink only after `./install.sh` re-runs | `[verified: deny-no-op-dispatch.sh:86; CHANGELOG.md:21, 134-135; review-orchestrator-gate-release-decoupling.md:72]` |
| 31 | A round the gate routes counts toward the dogfood, and the deny text joins the sources of the last criterion | `[engineer-verified: "Count gate-routed rounds (Recommended)"]` |
| 32 | The shared skill-name helper and its edit to `activate-handoff-bypass.sh` (Critical files 7-9) stay in this plan, though the Ask does not name them | `[engineer-verified: "Keep the shared helper (Recommended)"]` |
| 33 | A `PreToolUse` `Skill` payload carries a call's arguments as the string `tool_input.args`. Transcript `tool_use` records carry `input.args`. Verification step 0 captured a hook payload. Had the field been absent, the `unrouted` escape cannot be read, and the plan stops | `[verified: Verification step 0 capture]` (a call with args carried a string `tool_input.args`; a call without args had no `args` key; transcript side: `review_rounds.py:142`) |
| 34 | No routine round leads its args with the bare word `unrouted`. If wrong, that round runs inline, which is today's behavior. Verification step 0 checks this repo's own local transcripts with a count-only `Grep` | `[verified: Verification step 0 count-only check]` (no match in the searched directories, which are this account's local transcripts only, and the positive control matched) |
| 35 | `code-review/SKILL.md` has no args handling, and its diff-artifact note tells the skill to reuse its invoker's diff-file path. Item 25's in-line clause says that in args beginning with `unrouted`, the path is what follows the word, so no consumer's diff-file pass hands `comment-discipline-reviewer` the word. Whether every session follows the clause is a post-merge observation. The fallback is the revert set in Approach, plus setting the key to `false` on a key-on account | `[verified: grep of code-review/SKILL.md finds no args or ARGUMENTS handling; :290; line count 496 this session]` (sessions following the clause: `[unverified]`) |
| 36 | The `ready-for-review` active marker is live from step 0 to step 9, so the per-commit fix rounds inside the loop run under it. `_lib_active_bypass_marker_live` can read it with no new marker surface. `/review-pr` keeps per-session artifacts under `.review-pr-active.d/` but has no `marker.sh` active marker, so the marker signal still does not cover it | `[verified: ready-for-review/SKILL.md:25, 82, 158; _lib.sh:813-821, 2577-2602; marker.sh:41-43; review-pr/SKILL.md:23]` |
| 37 | `_lib_parse_tool_input_or_deny` sets `TOOL_NAME` and `AGENT_ID` as globals. A later `jq` read of a payload the parse accepted fails only on a missing, failing or slow `jq` | `[verified: _lib.sh:467-468, 543; plugins/claude-hook-review/skills/claude-hook-review/SKILL.md:137]` |
| 38 | A key-on account can hold an agent that cannot be dispatched: its file stays unlinked in an `agents/` directory that is not a folded symlink until `./install.sh` re-runs. A session can also lack the `Agent` tool | `[verified: CHANGELOG.md:296; review-orchestrator-gate-release-decoupling.md:72]` (no `Agent` tool: `[unverified]`) |
| 39 | A gate's deny text can tell a subagent to report the denial to its dispatcher. The agent turns a hook denial it cannot satisfy into a `HALT:` entry | `[verified: deny-no-op-dispatch.sh:128; review-orchestrator.md:31-36]` (live: `[verified: Verification step 0, the agent's return held a HALT entry quoting the denial]`) |
| 40 | `helpers.py` and `select-tests.py` are global triggers that select the full suite. A hooks-domain test that reads a SKILL.md by a module-level path must be declared in `SKILL_FILES_READ_BY_HOOK_TESTS` and `_KNOWN_CROSS_DOMAIN_READS` | `[verified: select-tests.py:260-276, 405-416; test_select_tests.py:1982-2033; test-conventions/SKILL.md:64]` |
| 41 | `.claude/settings.local.json` is gitignored, so a scratch registration there never stages. A session started from the worktree root loads its hooks | `[verified: .gitignore:12]` (hook loading: `[verified: Verification step 0, the capture file held lines from the scratch registration]`) |
| 42 | A `settings.json` registration that names a missing hook script makes the harness report a non-blocking error and run the call. The gate is then inactive until `./install.sh` links it | `[verified: Verification step 0]` for the harness reporting a non-blocking error for the missing script, and for the call still running: an allowed `Skill` call ran, with a body record, while the missing-script entry was registered beside a passing hook. The first probe session's block came from the capture script's own failure to open its file, which the harness reported apart from the missing-script entry's non-blocking report. The unlinked-state consequence on a real non-folded account stays `[unverified]`, and the CHANGELOG hedge is its only control. (CHANGELOG.md:134-135 records the unlinked state for an earlier hook, not its exit-code consequence) |
| 43 | `install.sh` or the migration scaffolds an explicit `review_orchestrator_routing = false` row. If the key is later removed, that row becomes unrecognized, and every read of the state file warns | `[verified: _config.sh:294; docs/config-file.md § "Migration"]` |
| 44 | Edit both `/ready-for-review` and `/review-pr` to pass the escape themselves (Critical files 23-24) | `[engineer-verified: "Edit both skills (Recommended)"]` |
| 45 | The engineer left the typed-`/code-review` question to a `plan-architect` consult | `[engineer-verified: "Ask the architect"]` |
| 46 | In these two skills, only `/ready-for-review` step 3's cumulative pass and `/review-pr` step 5's line-level pass must stay inline. The fix-commit rounds in `/ready-for-review` steps 3 and 4 are routine staged-diff rounds that write the code-review marker, so they stay routed. `/review-pr`'s other `/code-review` mentions cite its Step 1 or its findings and invoke nothing | `[verified: ready-for-review/SKILL.md:82, 104; review-pr/SKILL.md:7, 82, 86, 108, 112]` |
| 47 | `ready-for-review/SKILL.md` and `review-pr/SKILL.md` are each 200 lines against the 200-line default cap. The length gate denies only a staged file that is over its cap and longer than its base, so an edit inside one line passes and any added line denies the commit. Each edited paragraph is a single line | `[verified: line counts this session; check-skill-length.sh:111-120; _lib.sh:2240-2243; ready-for-review/SKILL.md:82; review-pr/SKILL.md:86]` |
| 48 | A typed (user-invoked) `/code-review` stays unrouted by design. A one-off review should use `/review-pr`, and a user who types `/code-review` anyway probably wants it in the main thread | `[engineer-verified: "By “typed” code review you mean user-invoked? I don't care about that. If a user really wants to do a one off code review they really should use the review-pr skill instead. And if they want to use the one off code review skill nonetheless then the orchestrator should NOT be used because the user probably wants it done in the main thread. So this is by design"]` |
| 49 | A `plan-architect` consult (row 45) recommended leaving a typed `/code-review` inline | `[unverified]` (the consult's return was relayed this session and not persisted; row 48 is the decision) |
| 50 | The escape is the word `unrouted`, passed as args "unrouted" or as args "unrouted <path>" with the path last, and plain, with no case token. The two diff-file passes and the `subagent-delegation` HALT step pass it, and `code-review`'s diff-artifact note reads it. Only these four skill-text sites carry it; the deny text carries it for a user-asked inline round | `[engineer-verified: "Accept `unrouted` (Recommended)"; "Keep plain `unrouted` (Recommended)"]` (the labels cover the spelling and the plain args form, with no case token; row 44's quote covers editing both skills) |
| 51 | Two dogfood criteria beyond counting gate-routed rounds. First, every un-denied key-on main-thread `Skill(code-review)` block begins its args with `unrouted` and follows one of the four inline cases. Second, a key-on versus key-off review-round cost comparison before the default flips, defined in Approach, Dogfood. The instrument is a manual local read, and only booleans publish. The engineer selected the label "Include both (Recommended)" for the question offering the un-denied-block check and the key-on versus key-off cost comparison | `[engineer-verified: "Include both (Recommended)"]` |
| 52 | A new `settings.json` hook registration reaches only a session started after the pull and after `./install.sh`. A key flip reaches the next `Skill` call, because each hook run is a new process that rereads the key | key half: `[verified: _config.sh:34-36]` (the memo is per-process); that the vendor runs each hook as a new process on every call: `[unverified]`; registration half: `[unverified]` (no repo source; the vendor hooks reference would settle it, or a session that adds a `Skill` hook to a settings file mid-session and then calls a skill) |
| 53 | On a key-on account, `/ready-for-review` step 4's `skill-fidelity-reviewer` checks `code-review`'s spawn-dispatch obligation against the `review-trace` timeline. Under its contract the check is branch-wide (a reviewer-spawn of the required specialist anywhere on the branch) and only a completed pass is checked. A routed round's spawns reach the timeline. So a gate-denied block should not draw a silent-abbreviation finding | `[verified: ready-for-review/SKILL.md:102, 104; skill-fidelity-reviewer.md:54, 97, 116-121 for the branch-wide, completed-pass-only check; review_trace.py:673, scope.py:1037-1051, corpus.py:27-30 for subagent records entering the timeline]` (the conclusion that a gate-denied block draws no finding, and that the denial text matches nothing the agent acts on: `[unverified]`, an architect consult not persisted) |
| 54 | A fix re-run after a user-asked inline round or a typed `/code-review` is a routine staged-diff round, so the gate routes it. A user's request for an inline round covers the rounds it names, and row 48's quote governs the typed round itself: the engineer did not select the option that keeps typed loops inline too. After a HALT fallback, the rest of that review loop runs inline with args "unrouted" until a clean round writes the marker. The cause of a HALT recurs each round, so routing each re-run would pay one deny and one failing dispatch every time. If the agent's own call lacks `agent_id`, each new review loop pays one deny and one HALTing dispatch until the key is off | `[verified: subagent-delegation/SKILL.md:162-171, 201; review-orchestrator.md:31-36]` (HALT arm: `[engineer-verified: "Loop-scoped fallback (Recommended)"]`; the refused-dispatch arm is dropped, row 63; user-asked and typed arm: `[engineer-verified: "Route the re-run (Recommended)"]`) |
| 55 | `deny-no-op-dispatch.sh:128` and `require-architect-consult.sh:124` end their deny text with the same subagent sentence. `test_deny_no_op_dispatch.py:253` pins only `report this denial to your dispatcher` | `[verified: grep of those three files this session]` |
| 56 | `code-writer` cannot write a gate-release marker, it leaves its changes unstaged, and it is barred from the full suite | `[verified: _lib.sh:3840-3843; claude/.claude/agents/code-writer.md:21-22, 23-28]` |
| 57 | `scripts/list-shell-files.sh` enumerates `git ls-files`, so it lints no unstaged new hook. The contributor `.venv` lives only at the main-tree root, three levels above a linked worktree | `[verified: scripts/list-shell-files.sh:25; README.md:306, 523, 547]` |
| 58 | The existing `Skill` hook entry in `settings.json` (`PostToolUse`, `activate-handoff-bypass.sh`) carries no `statusMessage` | `[verified: claude/.claude/settings.json:507-513]` |
| 59 | `_config_trim` returns through the global `_CONFIG_TRIM_RESULT` and prints nothing | `[verified: _config.sh:106-115]` |
| 60 | On a key-on main-session `code-review` call, the gate makes its `jq` calls in this order: the parse, the skill read, the args read, then the deny-reason encode. `_lib_parse_tool_input_or_deny` and `_lib_emit_deny` each make one call, and `_config.sh` makes none | `[verified: _lib.sh:425, 523; grep of _config.sh finds no jq]` (no `jq` call while `_lib.sh` is being sourced: `[unverified]`; item 11's no-failure case counts the total) |
| 61 | The escape word is `unrouted`, not `inline`, because `code-review/SKILL.md` already uses `inline` as a fixed value: `--authoring-agent code-writer\|inline\|mixed\|unknown` at :27, :409 and :411, and Fix route `inline — <reason>` at :343. A session primed by args `inline` could log the wrong authoring agent and skew author-outcome figures on every account. `unrouted` appears in no file under `claude-skills/` or `claude/.claude/`, which is the whole grep scope. Files under `docs/` and `.claude/plans/` other than this plan do contain the word, in other senses (for example "left unrouted") | `[verified: grep of code-review/SKILL.md for 'inline' this session by the architect consult; grep of claude-skills/ and claude/.claude/ for 'unrouted' this session by the plan editor, zero matching files; grep of docs/ and .claude/plans/ and a read of the matched lines, which use the word in other senses]` |
| 62 | A live reviewer may misapply its branch-wide spawn check to a gate-denied block and raise a silent-abbreviation finding at `/ready-for-review` step 4. Step 4's stated escape clears it | `[unverified]` (the escape: `[verified: ready-for-review/SKILL.md:104]`) |
| 63 | A dispatch the harness refuses (an unlinked or unknown agent, or no `Agent` tool) keeps the protocol's existing stop. The plan adds no inline fallback for it: an engineer whose agent file is unlinked is stopped until they re-run the installer, set the key to `false` or ask for an inline round. This replaces the refused-dispatch arm that row 54's earlier selection covered | `[engineer-verified: "Oh my god I didn’t realize that was the scenario we were guarding against. Absolutely not worth the heavier fix. Simpler design."]`. The same decision removes Verification step 0's probe fallback: `[engineer-verified: "Ok so those cases are not worth guarding against. Those would apply to any new agent I add, and there was no refusal plan for other agents I added. Drop it"]` |

## Critical files

Run Verification step 0 first, in the main session with the engineer. It pins the payload shape the gate and its test use, and its stop conditions can change the design. Implementation is then one `code-writer` dispatch, carrying step 0's results in the prompt (field names and types, plus each row's outcome). The prompt also says that a plan-review denial stops the dispatch and is reported, that the dispatch does not edit `settings.json` (item 12) and reports it, and that it runs no full suite. The gate's deny text, the four skill edits, the registry rows and the new record share one contract: the key name, the gate label, the cited heading, and the `unrouted` spelling and args form. Splitting the work would restate that contract in every prompt. The dispatch runs scoped checks only (Verification steps 1-3: the test files step 1 lists, `ruff check`, a direct `shellcheck` of the hook, and step 3's timing script) and the step-5 read. The main session then applies item 12, stages and runs `select-tests.py` (step 4). Staging, the `/skill-review` marker and the commit gates belong to the parent, because `code-writer` leaves its changes unstaged, cannot write a gate-release marker and may not run the full suite (row 56).

Config key:
1. `claude/.claude/hooks/config-keys.psv`: append `review_orchestrator_routing|bool|false|config-dir|false|config-dir|||Review-orchestrator routing|docs/design-decisions/review-orchestrator-routing-gate.md|`.
2. `claude/.claude/hooks/tests/config-schema-audit.md`:
   - At :3, change "every one of the 18 keys" to 19.
   - Add a ``### `review_orchestrator_routing` `` section after the `architect_fix_plan_pilot` section, in the same shape.
   - Call site: `require-review-orchestrator-routing.sh`, through `_config_enabled`.
   - Fail direction: only exit 0 routes, and any other exit allows the call.
   - Rename "Remaining thirteen keys" to "Remaining fourteen keys".
3. `docs/config-file.md`:
   - Add the per-key table row, linking the new record.
   - In § Migration, change "the 18 keys" to 19.
   - Change "today's 18 keys" to 19, and name the new key beside `test_selection_tracking` and `architect_fix_plan_pilot`. "both keys' default rows" becomes "all three keys' default rows".
   - In § Per-account isolation, change "*all 18 keys*" to 19. That string wraps across a line break at :260, so match it across the wrap.
   - Leave "the eleven non-enforcement-critical keys" as is, because a key with no legacy file imports nothing.
4. `install.sh`: in the comment at :612, change "reports all eighteen" to "nineteen".
5. `claude/.claude/scripts/migrate-legacy-config.sh`: in the header comment at :13, change "eighteen keys" to "nineteen".
6. `claude/.claude/scripts/tests/test_config_py.py`:
   - Add `"review_orchestrator_routing": "false"` to the expected set at :85-88.
   - Parametrize the three `architect_fix_plan_pilot` checks at :90-103 over both keys: per-account resolution, `bool` type, and no prompt description.

Shared helper:
7. `claude/.claude/hooks/_lib.sh`: add `_lib_skill_basename`. It prints the final component on stdout, taken after the last `:` and then after the last `/`, and returns 0. Its one-line comment names the two qualified forms. A caller captures it with `$(...)`. Each hook calls it once per run, so the fork is cheap.
8. `claude/.claude/hooks/activate-handoff-bypass.sh`: replace :75-80 with `LABEL=$(_lib_skill_basename "$SKILL_NAME")`. `test_activate_handoff_bypass.py` must pass without edits.
9. `claude/.claude/hooks/tests/test_lib.py`: test `_lib_skill_basename` by sourcing `_lib.sh` and asserting stdout, the shape `_parse_pr_identity` uses at `test_lib.py:1555-1559`. Use six inputs:
   - a bare name;
   - `plugin:name`;
   - `.claude/worktrees/<branch>/claude:name`;
   - `/code-review`, which gives `code-review`;
   - `plugin:`, which gives an empty name;
   - an empty string.

Gate:
10. `claude/.claude/hooks/require-review-orchestrator-routing.sh`: new file. It is bash 3.2-safe (no `${var,,}`, no `mapfile`; `test_no_bash4_constructs`). Its mode is 755, so `chmod 755` it, because the `Write` tool creates 644. If the dispatch cannot run `chmod`, the parent runs it and re-runs Verification step 1's scoped tests.
    - Line 2 is `# hook-class: gate`, and line 3 is `# tier-threat-model: cooperative`.
    - `set -uo pipefail`, with no `-e`, as at `deny-no-op-dispatch.sh:40`.
    - Declare `DENY_GATE_LABEL="review-orchestrator routing"` before the bootstrap stub. Copy the stub, the source-failure deny and the re-point from `deny-no-op-dispatch.sh:76-88`:
      - the exit-2 stderr stub;
      - the `./install.sh` re-run hint;
      - the re-point to `emit_deny() { _lib_emit_deny "$1"; }`.
    - Call `_lib_parse_tool_input_or_deny "could not parse tool-input JSON."`.
    - Apply the M3 allow rules in order. Each allow exits 0 with no output. The `jq` call order is part of this contract, because item 11's fail-open cases count calls: the parse, then the skill read, then the args read, then the deny-reason encode (row 60).
      - Rule 1 tests `$TOOL_NAME`.
      - Rule 2 tests `$AGENT_ID`, never `AGENT_TYPE`.
      - Rule 3 allows on any non-zero `_config_enabled review_orchestrator_routing` exit.
      - Rule 4 reads `_lib_jq -r '.tool_input.skill // empty | strings'`. It allows on a non-zero exit, or when `$(_lib_skill_basename "$SKILL")` is not exactly `code-review`.
      - Rule 5 reads `_lib_jq -r '.tool_input.args // empty | strings'` as one value, so a non-string reads as empty. It allows on a non-zero exit. Otherwise it strips leading whitespace with the expansion `_config.sh:112` uses, `ARGS="${ARGS#"${ARGS%%[![:space:]]*}"}"`, and allows when a `case` matches `unrouted` or `unrouted[[:space:]]*`. Do not call `_config_trim`. It is internal to `_config.sh` and returns through `_CONFIG_TRIM_RESULT` (row 59), so a `$(...)` capture of it reads empty and would deny every `unrouted` call. A locale whose `[:space:]` admits non-ASCII whitespace can only widen the allow.
    - Otherwise, call `emit_deny` with the reason below, then `exit 0`.
    - The header states one fact per line:
      - its purpose, pointing to the new record;
      - the allow rules;
      - the fail directions: a payload failure denies, a failed key read allows, and a failed skill or args read after the parse allows;
      - the settings entry has no `if`, so the tool-name check is the only filter;
      - the key-off cost: a key-off `Skill` call pays the shared parse and one config read;
      - the field reads: the two field reads run only on a key-on main-session call;
      - the known gaps:
        - A typed `/code-review` expands without a `Skill` call, so it runs in the main thread by design; `/review-pr` is the one-off review.
        - A plugin skill named `code-review` also routes.
        - A fork or nested session whose payload lacks `agent_id` reads as main (the hooks-reference note in `enforce-marker-script-shape.sh`).
        - If the harness stops sending `tool_input.args` as a string, every call denies, including the `unrouted` retry, and setting the key to `false` recovers on the next `Skill` call. A session that passes the quoted spelling `"unrouted"` is denied by design and recovers the same way.
        - A main session run as `claude --agent review-orchestrator` carries `agent_type` alone (row 7), so the gate denies it with text that points at itself. Recovery is args "unrouted" or setting the key to `false`.
    - The reason is prescribed below. The label comes from `DENY_GATE_LABEL`, so the literal carries none. In the bash double-quoted literal, escape each backtick as `` \` `` and each inner double quote as `\"`. Keep the literal free of every `_DENIAL_CAUSE_MARKERS` substring (`denials.py:395-400`). Its last sentence is the one `deny-no-op-dispatch.sh:128` and `require-architect-consult.sh:124` end with, verbatim (row 55):
      > review_orchestrator_routing is on for this account, so this /code-review round runs through the review-orchestrator agent, not in this session. Load the subagent-delegation skill and follow `subagent-delegation/SKILL.md` § "Exception: gate/review loops stay orchestrator-driven". Run the round here instead only for that section's HALT fallback, a /ready-for-review cumulative pass, a /review-pr pass, or an inline round the user asks for: re-invoke the code-review skill with args "unrouted", or args "unrouted <path>" when the round has a diff-file path. Pass the word unrouted without the quotation marks, and keep the path after it when there is one. If you are a subagent, report this denial to your dispatcher rather than attempting to resolve it yourself.
11. `claude/.claude/hooks/tests/test_require_review_orchestrator_routing.py`: new file.
    - Build each payload locally, as `_with_agent_id` in `test_enforce_marker_script_shape.py:57-77` does, with field names and types pinned from Verification step 0. Do not edit `helpers.skill_input`.
    - Use a local runner that asserts the exit code and stdout. `helpers.run_hook` reads any empty-stdout exit other than 2 as "allow". "Allow" means exit 0 and empty stdout.
    - Set `HOME` to a temp dir, and set `CLAUDE_CONFIG_DIR` explicitly in every case, because the ambient value is inherited otherwise. Seed every key-true state file with the exact documented enable line, `review_orchestrator_routing = true`. Keep the key-true cases on the real `config-keys.psv`, which is the tripwire for key-name drift.
    - Baseline deny: key `true`, tool `Skill`, skill `code-review`, no args, no `agent_id`. Assert exit 0, `hookEventName` `PreToolUse` and `permissionDecision` `deny`, and that the reason:
      - starts with `Blocked by review-orchestrator routing gate: `;
      - contains the exact spans `args "unrouted"` and `args "unrouted <path>"`;
      - contains `review-orchestrator`;
      - contains `report this denial to your dispatcher`, the substring `test_deny_no_op_dispatch.py:253` pins for the sibling gate;
      - contains one substring per inline case, each asserted on its own: `/ready-for-review`, `/review-pr`, `HALT fallback` and `inline round the user asks for`;
      - contains the two body-only spans `review-orchestrator agent` and `subagent-delegation`, because `review-orchestrator` alone is satisfied by the label prefix;
      - contains `without the quotation marks`, which pins the delimiter clause.

      Do not assert whole-string equality.
    - Deny, each case a one-factor change from the baseline:
      - skill `plugin-x:code-review`, skill `.claude/worktrees/<branch>/claude:code-review`, and skill `/code-review`, which a gate that inlines its own `:` strip instead of calling `_lib_skill_basename` would let through;
      - args of descriptive prose that embeds an absolute diff path;
      - args `Unrouted review` and `unroutedly`;
      - args `unrouted:` and `unrouted,`, a non-whitespace boundary;
      - args with prose before a complete `unrouted` word: `please run unrouted`, and `please run unrouted /abs/x.diff`;
      - args `"unrouted"` and `` `unrouted` ``, delimiter-wrapped as copied from the prose and denied by design;
      - args of only spaces, a tab and a newline;
      - non-string args;
      - `agent_type` set with no `agent_id`, once to a non-roster name and once to `review-orchestrator`;
      - `agent_id: ""`.
    - Allow:
      - args `unrouted`, `unrouted /abs/x.diff`, `unrouted` + newline + `/abs/x.diff`, `unrouted` + tab + `/abs/x.diff`, and leading whitespace before `unrouted`, once each for a space, a tab and a newline;
      - `agent_id` set with `agent_type` `review-orchestrator`, with `agent_type` absent, and with a different roster name;
      - skill `code-review-proj`, `pre-code-review` and `Code-Review`. The match is case-sensitive on purpose. The gate compares the name the payload carries, and whether the harness resolves skill names case-insensitively is unverified; a cooperative caller spells the installed name;
      - a missing or null skill, and skill `plugin:`;
      - tool `Bash` with `tool_input.skill` `code-review` and no args;
      - key `false`;
      - each failed key read. This case and the `key false` case above are one-factor changes from the baseline-deny payload, so the only thing that lets the call through is the key state:
        - exit 2: a relative `CLAUDE_CONFIG_DIR`, with a `true` row seeded at `$HOME/.claude/claude-config.toml`;
        - exit 3: an isolated hooks dir with no psv. Copy and adapt `_isolated_hooks_dir` from `test_nudge_memory_store_audit.py:94`; do not import it;
        - exit 4: an isolated psv that lacks the row;
      - each failed field read after the parse, on the baseline-deny payload with key `true`. Put a `jq` stand-in first on `PATH` that runs the real `jq` by absolute path except on one numbered call, where it exits 1, and count calls through a file in the temp dir. Model the stand-in on `test_hook_alignment.py`'s `test_blocks_when_timeout_rejects_dash_k_flag` (:3058): prepend the stand-in directory to the real `PATH`, and exec the real `jq` for every call except the numbered one. That test stubs `timeout`, not `jq`, so only its `PATH`-prepend and exec-real shape carries over. Do not model it on `test_blocks_when_jq_hangs`. Fail call 2, the skill read, in one case, and call 3, the args read, in the other. The call-2 case only guards a flip from allow to deny; it cannot tell a skill read from a different read, so state that and do not claim it tests both.
    - Control for the failed-read cases: run the same stand-in with no failing call on the baseline-deny payload, and assert a deny and exactly four counted calls. A shift in the call order then fails loudly instead of silently failing the wrong read.
    - Key-off cost contract: with the same counting stand-in and no failing call, assert exactly one counted `jq` call (the parse) for key `false`, for `agent_id` set, and for tool `Bash`. Each is a one-factor change from the key-`true` baseline, so the `agent_id` and `Bash` cases run with key `true`, skill `code-review` and no args. This pins the Approach's claim that those paths skip the field reads.
    - Heading coupling: extract the `§ "..."` heading from the emitted reason. Then assert a full-line `###` match in `claude-skills/skills/subagent-delegation/SKILL.md`, after `test_hook_alignment.py:459-491`. Read the file through a module-level path constant that this test file defines, rooted at `helpers`' repo root. Items 16-17 declare that read to `select-tests.py`, and they define no constant to import.
    - Span coupling: assert the literal `args "unrouted"` (not the `<path>` form) appears in the protocol section, bounded from the `###` heading the heading check found to the next `## ` or end of file, as `_heading_section_text` bounds it. That helper lives in `test_skills.py` and cannot be imported here, so reimplement the bound locally: from the heading line to the next line that starts with `## `.
    - Settings wiring: assert that the gate's `PreToolUse` group matcher in `claude/.claude/settings.json` is exactly `Skill`, through `_pretooluse_matcher_groups_for` (`test_hook_alignment.py:522`), after the shape of the test at :562. Copy that helper into this file rather than importing it, with its path constants `_MAIN_HOOKS_DIR` and `_SETTINGS_PATH` defined locally. This pin fails until the parent applies item 12.
    - `test_hook_alignment.py` already covers the shared fail-closed cases for every gate.
12. `claude/.claude/settings.json`: the parent applies this edit in the main session after the dispatch returns, because `permissions.ask` holds `Edit(//**/.claude/settings*.json)` (`claude/.claude/settings.json:44`). The dispatch prompt says not to edit this file and to report it. Add a new `PreToolUse` entry, `{"matcher": "Skill", "hooks": [{"type": "command", "command": "~/.claude/hooks/require-review-orchestrator-routing.sh"}]}`. It has no `statusMessage`. The existing `Skill` entry carries none (row 58), and this one would show on every `Skill` call on every account, key off included. Verification step 4 re-reads the group to confirm it names the hook path.
13. `docs/hooks.md`:
    - Add a Gate hooks bullet. It names:
      - the matcher;
      - the `unrouted` escape and the four inline cases;
      - the fail directions;
      - the known gaps, including that a typed `/code-review` runs in the main thread by design, and that a main session run as `claude --agent review-orchestrator` is denied with text that points at itself (recovery: args "unrouted" or the key set to `false`);
      - the key as the off switch, with the exact enable line and the `config-get.sh` check;
      - that a key flip takes effect on the next `Skill` call, and that a new session should start after the pull and `./install.sh` before the gate is expected to act;
      - the recovery from a deny that repeats on every call, including the `unrouted` retry, from either cause (the harness no longer sends `tool_input.args` as a string, or the session copied the quoted spelling): set the key to `false`, effective on the next `Skill` call, or type `/code-review`, which expands without a `Skill` call.

      It points to the hook header for the exact allow rules and to the new record.
    - Add the tier-table row `` | `require-review-orchestrator-routing.sh` | `cooperative` | A bypass runs the round inline, the path `require-code-review.sh` already gates; nothing irreversible. | ``.
    - Change "17 plain `cooperative`" to 18.

Denial registry and test selection:
14. `claude/.claude/scripts/transcript_analysis/denials.py`: add `"review-orchestrator routing",  # require-review-orchestrator-routing.sh` to `_DENIAL_HOOK_LABELS`, in the active-hook block above the legacy-only comment.
15. `claude/.claude/scripts/tests/test_transcript_denials.py`:
    - Append `("require-review-orchestrator-routing.sh", "review-orchestrator routing")` to `_BOOTSTRAP_FALLBACK_HOOKS`, after the `deny-no-op-dispatch.sh` row. Leave `_BOOTSTRAP_FALLBACK_HOOKS_ORIGINAL_24` unedited. `TestDenyGateLabelConformance` and the bootstrap case of `TestDenialHookLabelEnumerationRealHooks` then cover the bootstrap wording and the literals.
    - Add a real-hook behavioral-deny case to `TestDenialHookLabelEnumerationRealHooks`, modelled on `test_marker_sh_path_traversal_produces_enumerated_label` (:402-413). It runs the real gate through `run_hook_reason` with the payload `helpers.skill_input("code-review")` builds (no edit to the helper), `home=` a temp dir, and `extra_env` setting `CLAUDE_CONFIG_DIR` to a temp config dir seeded with `review_orchestrator_routing = true`. It asserts `_denial_hook_label("", reason) == "review-orchestrator routing"` and `_denial_cause_kind(reason) == "behavioral"`. Putting it in this scripts-domain file keeps a `transcript_analysis` import out of the hooks-domain test.
16. `claude/.claude/scripts/select-tests.py`: add `SUBAGENT_DELEGATION_SKILL_MD = "claude-skills/skills/subagent-delegation/SKILL.md"` and make it a member of `SKILL_FILES_READ_BY_HOOK_TESTS`.
17. `claude/.claude/scripts/tests/test_select_tests.py`: add `("claude/.claude/hooks/tests/test_require_review_orchestrator_routing.py", "claude-skills/skills/subagent-delegation/SKILL.md")` to `_KNOWN_CROSS_DOMAIN_READS`.

Contract text and records:
18. `claude-skills/skills/subagent-delegation/SKILL.md`: make two in-line edits, in :195 and :201, and add no line (row 18).
    - At :195, change "used only when the user explicitly asks for it:" to "used only when the user explicitly asks for it (a denial from the review-orchestrator routing gate is that request):".
    - The stop clause at :195 ("A return that lacks a `Fix paths:` or `HALT:` item, or no return at all, stops:") stays unchanged, so a refused, hook-denied or user-rejected dispatch keeps the stop (row 63).
    - At :201, change "then run `/code-review` inline in this session at once, which asks, logs and carries by the skill's own rules." to "then run `/code-review` inline in this session at once, invoking the skill with args "unrouted", and run the rest of this review loop the same way until a clean round writes the marker. Each such round asks, logs and carries by the skill's own rules."
19. `claude-skills/skills/tests/test_skills.py`:
    - In `test_review_orchestrator_protocol_section_carries_phrase` (:7519-7555), which reads only the `subagent-delegation` section:
      - Add a parameter for `at once, invoking the skill with args "unrouted", and run the rest of this review loop the same way until a clean round writes the marker`. Its invariant: the HALT fallback must pass the escape word the routing gate lets through, and must carry it through the rest of that loop, or a key-on fallback's fix re-run is denied and routed to the agent that just HALTed. The span also pins the bound, so a later trim cannot leave the carry without an end, which would run every later loop inline.
      - Add a parameter for `a denial from the review-orchestrator routing gate is that request`. Its invariant: the explicit-request sentence names the gate whose denial counts as that request.
      - Change the `agent-used-only-on-explicit-request` invariant text to say that a routing-gate denial on a key-on account counts as that request.
    - Add a separate test, `test_code_review_args_carry_unrouted_before_path`, parametrized over (skill, heading, phrase, invariant). It reads each section through `_heading_section_text` and leaves the protocol-section test's four existing parameters alone. Each phrase is the shortest span that carries the invariant:
      - `ready-for-review`, `_READY_FOR_REVIEW_STEP3_HEADING` (`test_skills.py:5180`), `args "unrouted <path>"`;
      - `review-pr`, a new `_REVIEW_PR_STEP5_HEADING = "## Step 5 — Line-level pass"`, `args "unrouted <path>"`;
      - `code-review`, `"## Ripple effect triage"`, `the path is what follows that word`.

      The first two share one invariant: a pass that drops the word is denied once and retried, and the pin keeps that cost from becoming routine. The third's invariant: the skill must take the path after the word, or every diff-file pass hands `comment-discipline-reviewer` the word. A section-level pin cannot tell the cumulative-pass sentence from step 3's fix-commit sentence. Verification step 5's read covers that half, and a leak into a fix-commit round fails safe, because that round then runs inline as it does today.
20. `docs/design-decisions/review-orchestrator-routing-gate.md`: new record, with provenance `*YYYY-MM-DD.*` set to the day it is written. It covers:
    - the decision, and why it denies the `Skill` call;
    - the `unrouted` escape and why args presence fails: routine rounds commonly carry args. Give no figure. Cover the active-marker and recorded-artifact alternatives;
    - which steps pass `unrouted`, each cited by file and heading rather than quoted: `subagent-delegation`'s HALT step (for the rest of that review loop), `/ready-for-review` step 3's cumulative pass and `/review-pr` step 5. The deny text still names both diff-file passes, so a pass that loses the word is retried inline rather than routed. These edits reach key-off accounts too;
    - the `code-review` diff-artifact clause that reads the path after the word (row 35), and its fallback: the revert set, as Approach states it, and the key set to `false` on a key-on account;
    - the allow rules and fail directions, cited to the hook header rather than restated;
    - why the gate is not one of the hooks the old record rejects;
    - why the key counts as the engineer's standing request, citing CLAUDE.md § "Shipping" and line 54 of the old record;
    - the enable path: the exact bare line, per account; the `config-get.sh` check; a new session after the pull and `./install.sh`; and that a key flip takes effect on the next `Skill` call;
    - the rejected alternatives from Approach, including that a typed `/code-review` runs in the main thread by design and `/review-pr` is the one-off review;
    - the known gaps:
      - `/ready-for-review` step 4 should not halt on a gate-denied block, because the reviewer's spawn check is branch-wide and covers completed passes only. If a reviewer misapplies it, step 4's stated escape, stating the deviation with a rationale, clears it. The follow-up issue tracks the consumers;
      - each routed loop adds one `code-review` round-open with no ledger block (row 16);
      - a fix re-run after a user-asked or typed inline round is routed, and an agent call that lacks `agent_id` costs each new review loop one denied, HALTing dispatch until the key is off;
      - a routed round follows the protocol's "Stage the review target" step, so on a key-on account a review of unstaged work stages it first. A round with no git diff behind it is routed, as an explicitly requested round is today;
      - a plugin skill named `code-review` routes too;
      - with the key on, the old record's accepted residuals (prompt injection, the agent's `Bash` and `Agent` reach, the marker gaps) apply to every routine round, not only explicit requests. The old record's dogfood still requires one round with a fix and a `git commit` that passes on the agent's own marker, which exercises the no-fix marker condition and the commit gate; no criterion inspects each marker write;
      - a main session run as `claude --agent review-orchestrator` is denied with text that points at itself, and recovers with args "unrouted" or the key set to `false`;
    - the dogfood change (M9): gate-routed rounds count, with the deny text added to the last criterion's sources (row 31). The engineer confirmed row 51, so the record states the two further criteria, the instrument and this recipe. The instrument is a manual read of this repo's own local transcripts that publishes only booleans. The record states claims in words, not by plan row number. The cost comparison uses the `code-review` rows only. The recipe:
      1. The engineer notes the branches worked while the key was on, because no transcript records the key's state.
      2. `transcript-analysis.py skill-invocation --branches <list>` gives the main-thread `code-review` blocks, in its top-level and routed buckets. "Routed" there is the bucket sense: a `Skill` call fired while another skill's body was active (`docs/transcript-analysis.md:409`). A `/ready-for-review` cumulative pass is therefore `routed` in the bucket sense and `unrouted` in the args sense, and neither means review-orchestrator routing. Write "bucket" or "args" wherever the word could be read either way.
      3. `transcript-analysis.py review-trace --this-repo --branches <list> --deny-summary` gives the denies under the `review-orchestrator routing` label.
      4. Any excess of step 2's count over step 3's is the un-denied blocks. First subtract every deny that fell inside a `/ready-for-review` cumulative pass or a `/review-pr` pass: it is a lost escape, not a routed round, and a local read of the denied block's parent skill identifies it when `--deny-summary` cannot join a deny to its block's bucket. Read only the un-denied blocks' args, locally, for whether each begins with `unrouted` (the args sense) and follows one of the four inline cases. A HALT case covers the rest of its review loop until a clean round writes the marker (row 54), so a post-HALT re-run with `unrouted` is not a leak. The user-asked case is scored per round.
      5. Before the default flips, compare the per-branch `round $` totals from `transcript-analysis.py review-round-cost --this-repo --skill code-review` over the key-on branches against comparable key-off branches, under the comparability rule and caveats in Approach, Dogfood. The figures stay local, and the flip records only the conclusion;
    - the withdrawal form. Withdrawing the agent after a failed dogfood touches every surface this change adds. The record lists files and surfaces, not plan item numbers (the numbers below only locate them in this plan):
      - items 10-13: delete the gate, its test and its `settings.json` entry, and remove the `docs/hooks.md` bullet and tier row, restoring the count;
      - item 14: move the label into `_DENIAL_HOOK_LABELS`' legacy-only block, so older transcripts still attribute their denies;
      - item 15: remove the `_BOOTSTRAP_FALLBACK_HOOKS` row and the behavioral-deny case, which fail once the hook is gone;
      - items 16-17: remove both select-tests declarations;
      - items 18-19 and 23-25: revert the skill sentences that pass `unrouted` or name the routing gate (including the request parenthetical), and their pins, including item 11's span-coupling assertion;
      - items 7-9 stay, because `activate-handoff-bypass.sh` still calls the helper;
      - items 1-6 stay: the `config-keys.psv` row and its count sites remain, with the docs marking the key retired. Removing the row would leave each account's scaffolded `review_orchestrator_routing = false` row printing "skipping line for unrecognized key" on every config read (`_config.sh:294`), and nothing prunes those rows. A later change may prune them and then drop the schema row;
      - items 20-22 stay as history, and the withdrawal adds its own record line and CHANGELOG entry.

      A plain revert of this change's PR must keep items 1-6 too, for the same reason, once any consumer has re-run `./install.sh` or the migration;
    - what stays unverified until observed: rows 13, 35 (sessions following the clause) and 52, plus the old record's list, cited. It also records each of rows 7, 8, 9, 33, 34 and 42 with the status step 0 gave it (captured or left unverified), and states the extra round-open per routed loop unconditionally (row 9 is verified), stated without payload content;
    - Sources.
21. `docs/design-decisions/review-orchestrator-gate-release-decoupling.md`: add one line under the provenance line, separated by a blank line, and change nothing else:
    > **Partially superseded by [review-orchestrator-routing-gate.md](review-orchestrator-routing-gate.md) (YYYY-MM-DD, the day it is written):** a `PreToolUse` `Skill` gate replaces the SessionStart hook that "Staged rollout" names as the key's delivery, and a round that gate routes counts toward the dogfood, with its deny text added to the last criterion's sources. The gate names the agent only in that text and constrains nothing the agent does. Everything else here stands, including the held-back `TRIGGER` widening and the dogfood that gates flipping the key's default.
22. `CHANGELOG.md`: add an `### Added` entry under `[Unreleased]`. It covers:
    - the key and the gate;
    - which calls are routed (main-session `code-review` calls whose args do not begin with `unrouted`) and which pass;
    - that on every account, `/ready-for-review`'s cumulative pass and `/review-pr`'s line-level pass now invoke the code-review skill with args "unrouted <path>", `subagent-delegation`'s HALT step passes args "unrouted" for the rest of that review loop, its request sentence now names the routing gate, and `code-review` reads the path after the word. A refused dispatch keeps the protocol's existing stop;
    - that a typed `/code-review` runs in the main thread by design, and `/review-pr` is the one-off review;
    - that a missing or failing `jq` now blocks every `Skill` call on every account, key off included, as it already blocks `Bash`, `Edit`/`Write` and `Read` (`docs/hooks.md` § "Gate deadlock recovery");
    - the enable line, its `config-get.sh` check, and starting a new session after the pull;
    - the re-run note, naming the symptom: "If `~/.claude/hooks` is a real directory rather than a symlink into this checkout, re-run `./install.sh` so the new hook is linked. Until then the routing gate does not run, and the harness may report a hook error or block the call."

Diff-file passes that pass `unrouted`, and the note that reads it (rows 35, 44, 46, 47, 50). For each of the three files below:
- The edit changes text inside one existing line, so the file keeps its line count. `ready-for-review/SKILL.md` and `review-pr/SKILL.md` stay at 200, their cap, and `code-review/SKILL.md` stays at 496 of 500. `check-skill-length.sh` passes, and any added line in the first two would deny the commit.
- The edit goes through `/skill-review` before the commit, in the same pass as item 18 (Verification step 4). The skill-review gate enforces this for every staged SKILL.md (`.claude/rules/review-pipeline-dispatch.md`), and `.claude/rules/skill-and-agent-self-review.md` requires it.
- Where the edit says why `unrouted` is there, it names routing, not the hook's filename (Approach, "Skill text names the gate by its deny label, not its file").

23. `claude-skills/skills/ready-for-review/SKILL.md`: at :82, change "Run `/code-review` against that diff, passing it the path the `DIFF_FILE:` line named." to "Run `/code-review` against that diff, invoking the skill with args "unrouted <path>", where `<path>` is the path the `DIFF_FILE:` line named (`unrouted` keeps this pass in this session)." Leave `unrouted` out of the same paragraph's fix-commit sentence and out of step 4's fix-commit round at :104. Both are routine staged-diff rounds the gate routes (row 46).
24. `claude-skills/skills/review-pr/SKILL.md`: at :86, change "Invoke `/code-review` over the diff file step 2's script printed, under the standing override above." to "Invoke `/code-review` over the diff file step 2's script printed, under the standing override above, with args "unrouted <path>" naming that file (`unrouted` keeps this pass in this session)."

25. `claude-skills/skills/code-review/SKILL.md`: at :290, change "Reuse your invoker's diff-file path if it handed you one." to "Reuse your invoker's diff-file path if it handed you one; in args that begin with the word `unrouted`, with or without quotation marks around it, the path is what follows that word, and `unrouted` alone hands you none." The literals `test_skills.py:5299-5330` pins in that note stay intact. The engineer selected the label "Keep the clause (Recommended)" for keeping this clause, after the architect consult recommended it (row 49, `[unverified]`).

**Checked and left unchanged** (replaces the list; `code-review/SKILL.md` is now item 25):
- `claude/.claude/agents/review-orchestrator.md`
- `claude/.claude/hooks/tests/test_agent_roster.py`
- `claude/.claude/tests/helpers.py` (payloads are built locally)
- `claude/.claude/hooks/tests/test_config_lib.py`
- `README.md`

## Verification

From the worktree root:

0. **Before the implementation dispatch, capture rows 7, 8, 9, 33, 34 and 42.** The main session does this with the engineer, and it cannot be delegated.
   - Write a scratch capture hook into a new temp directory outside the repo. For each `Skill` call, it appends only these facts to a temp file:
     - the skill name;
     - the key names of `tool_input`;
     - the JSON type of `tool_input.args`;
     - whether the args equal `capture-probe`;
     - whether `agent_id` is present and non-empty;
     - the `agent_type` value, or that it is absent.

     It denies every call whose skill's final name component is `code-review`, so no review body runs. It allows every other call with no output. Its deny reason ends with the sentence from row 55, verbatim, so the agent's HALT path (row 39) sees the text the real gate would send.
   - First check for a leftover capture registration in the worktree's `.claude/settings.local.json` and remove it. Then save a byte copy of that file (gitignored, row 41) to the temp directory, or note that it does not exist. Then register the capture hook there as a `PreToolUse` `Skill` hook, and in the same hooks array register a second hook entry naming a script that does not exist. Both are present before the session starts, so the capture line is the load control for both.
   - By then the plan is committed, so nothing is staged. The engineer starts a fresh session from the worktree root. The session relays each result, including anything the harness reported, and acts on none of them. It does five things, in this order:
     1. invoke `verify-sources` through the `Skill` tool, then stop without following it. This is row 8's positive control, and it runs first so a capture line proves the registration loaded before any `code-review` call runs. It is also row 42's observation: relay whether the call ran and what the harness reported about the missing script. A silent run counts as evidence only if the capture file has this call's line. If the harness blocked the call, remove the missing-script entry, restart the session, and run the remaining calls;
     2. invoke the code-review skill through the `Skill` tool with args `capture-probe`;
     3. invoke it again with no args;
     4. dispatch `review-orchestrator` with `model: sonnet`, relay its return verbatim, and run none of the protocol steps after it. The capture hook denies the agent's own `Skill(code-review)` call, so it should return a `HALT:` entry (row 39). If the harness refuses the dispatch (row 38), stop and relay why (row 63).
     5. optionally, add a second capture hook mid-session that writes a separate file, then invoke `verify-sources` again. A line in the second file settles row 52's registration half.

     Remove both registrations together when restoring the settings file.
   - Read the capture file. Then restore `.claude/settings.local.json` to its saved content, or remove it if it did not exist, before reporting any outcome, stop conditions included. Read the file back, or confirm it is absent, to confirm it matches the saved content before the dispatch.
   - Row 8 method: `Read` the probe session's transcript, the newest `.jsonl` in the worktree root's project directory under `<config-dir>/projects/`. Find the record that carries the `verify-sources` body, and the field that ties it to that call's `tool_use` id. `sourceToolUseID` is the expected field (`transcript-analysis.py:546`). Then check that no record other than a denied call's own tool_result carries a denied `code-review` call's id in that field with skill-body text. The same read confirms row 9: each denied `Skill` call has its `tool_use` block in the transcript.
   - Row 34 method: one `Grep` with `output_mode: "count"` over this repo's own project directories under `<config-dir>/projects/`, named from this checkout's worktree slugs and never a glob. It looks for a `Skill` input whose `skill` ends in `code-review` and whose `args`, after any leading whitespace or escaped `\n` or `\t`, begin with `unrouted` followed by whitespace, an escaped `\n` or `\t`, or the closing quote. Match both field orders. Record only whether the count is zero. Run a positive control first: the same pattern shape with `capture-probe` substituted for `unrouted`, over the probe session's own transcript, which must count at least one. Record only that boolean. A control that counts zero means the pattern cannot match, and row 34 stays `[unverified]`.
   - Hold the results for the dispatch prompt: field names and types, plus each row's outcome (captured or left `[unverified]`), so item 20's record states each row's status correctly. The plan file is an active plan-set member and `require-plan-review.sh` hashes the whole set, so a retag before a dispatch would deny its first non-plan edit until `/plan-review` ran again. Rows 7, 8, 9, 16, 33, 34 and 42 were retagged after the dispatch returned, and that `/plan-review` re-run covered them. Never paste a payload, transcript line or count into the plan, PR, commit or record.
   - Outcomes:
     - An empty capture file means the registration did not load. Report that rather than reading it as a missing field.
     - Stop and re-plan when:
       - the args-bearing call (item 2) shows no string `tool_input.args` (row 33);
       - the agent's call reaches the hook without `agent_id` (row 7);
       - a skill-body record follows a denied call (row 8).
     - Stop and ask the engineer whether to re-plan or proceed with the row left `[unverified]`, and do not proceed on your own, when:
       - the row 8 check cannot run, because the transcript cannot be read or the control shows no linked body record and so the linking field is unknown. Row 8 stays `[unverified]`;
       - the agent's call never reaches the hook, meaning no capture line carries its `agent_type`, or the harness refuses the dispatch (item 4). Re-dispatch once before stopping on a missing capture line. Row 7 stays `[unverified]`, and the settings restore below runs before the report. Proceeding moves it to step 6, where a failure costs one denied call and one HALTing dispatch per routed round until the key is off.
     - A non-zero row 34 count goes to the engineer, who decides whether to choose another escape word. A row 34 check that cannot run leaves the row `[unverified]` and does not stop the plan, because a wrong row 34 only runs that round inline, as today.
     - Row 42: a harness-reported hook error confirms the report half only, and a call that runs beside the missing-script entry confirms the other half. Either states the CHANGELOG re-run note's wording. A harness block of the call goes to the engineer first, because a pull alone would then disable every `Skill` call, the review skills included, on a non-folded account until `./install.sh` re-runs; a silent run with a capture line leaves the CHANGELOG hedge as written; a silent run with no capture line is inconclusive and row 42 stays `[unverified]`.
     - A denied `Skill` call with no `tool_use` block goes to the engineer, because row 16's round detection rests on that block. A row 9 read that cannot run leaves the row `[unverified]`.
   - Row 13 and the edited skills' behavior cannot be captured here, because the session loads skill text from the stowed main checkout (G4). They stay on step 6.
1. In the dispatch, `chmod 755` the new hook. Then run these scoped checks with `../../../.venv/bin/pytest`, because the `.venv` lives only at the main-tree root (row 57):
   - `claude/.claude/hooks/tests/test_require_review_orchestrator_routing.py`
   - `claude/.claude/hooks/tests/test_lib.py`
   - `claude/.claude/hooks/tests/test_activate_handoff_bypass.py`
   - `claude/.claude/scripts/tests/test_transcript_denials.py`
   - `claude/.claude/scripts/tests/test_select_tests.py`
   - `claude/.claude/scripts/tests/test_config_py.py`
   - `claude/.claude/hooks/tests/test_doc_counts.py`
   - `claude/.claude/hooks/tests/test_design_decision_files.py`, which checks the new record's provenance line, single H1 and relative links, including item 21's supersession link
   - `claude/.claude/scripts/tests/test_no_bash4_constructs.py`, which globs the unstaged new hook
   - `claude-skills/skills/tests/test_skills.py`, the whole file: the neighbor pins on the edited paragraphs have names no `-k` filter reliably selects, and a filter that selects nothing exits 5, which must count as a failure
   - `claude/.claude/hooks/tests/test_hook_alignment.py`, filtered with `-k` to the new hook's name

   The ticket-reference scan reads `git ls-files`, so it sees these files only after step 4 stages them. Each must pass, except two expected failures that clear when the parent applies item 12: the settings-wiring pin in the first file, and the new hook's registration case in `test_hook_alignment.py`. The dispatch reports both by name. It runs no full suite (row 56); step 4 does.
2. In the dispatch, run `../../../.venv/bin/ruff check claude/.claude/ claude-skills/`.
3. In the dispatch, lint and time the new hook:
   - Run `../../../.venv/bin/shellcheck claude/.claude/hooks/require-review-orchestrator-routing.sh` directly. `scripts/list-shell-files.sh` enumerates `git ls-files`, and the dispatch leaves the new file unstaged, so the listing covers it only in step 4 (rows 56, 57).
   - Time the key-off cost from a scratch script in a new temp directory outside the repo. Pipe one `Skill` payload for a non-`code-review` skill into the gate ten times, with `HOME` and `CLAUDE_CONFIG_DIR` pointing at a temp config dir whose `claude-config.toml` has the scaffold's shape (every key present, each `false`), so the key reads as off and the state-file walk costs what a real account's does. An empty config dir skips that walk and understates the cost. Time `deny-no-op-dispatch.sh` on the same payload the same way, in the same run: it sources `_lib.sh`, parses, and exits at its tool-name check. Use a timing method that works in any contributor's bash, such as the `time` keyword with `TIMEFORMAT=%R` around the ten-run loop, not GNU-only `date +%s%N`. In the same run, time the `_config_enabled` read in a fresh process per sample (at least ten samples, against a source-only baseline, so the per-process memo is cold) and repeat the sibling's ten-run loop to get its run-to-run spread. Report all means and the spread. Pass means the gate's mean exceeds the sibling's by no more than the measured config-read time plus that spread, and the script states the margin it applied, derived from those measurements rather than chosen in advance. The 100 ms budget in `plugins/claude-hook-review/skills/claude-hook-review/SKILL.md` § "7. Performance budget" is a sanity bound, applied only when the sibling's own mean is under it. Report pass or fail in the dispatch's return.
4. In the main session, after the dispatch returns:
   - Apply item 12's `settings.json` edit. Then `Read` the file and confirm that the `PreToolUse` group with matcher `Skill` names `~/.claude/hooks/require-review-orchestrator-routing.sh`, and re-run the new test file so the settings-wiring pin passes. If the dispatch could not `chmod 755` the hook, run it now and re-run step 1's scoped tests.
   - Before staging, walk item 25's edited note against four inputs in a scratchpad: args `unrouted`, args `unrouted <path>`, no args, and prose args with an embedded path. Record only pass or fail (the fixture pair `.claude/rules/skill-and-agent-self-review.md` requires). The walk writes no repo file.
   - Rows 7, 8, 9, 16, 33, 34, 39, 41 and 42 carry step 0's results already, and the `/plan-review` re-run covered them. No further retag runs here.
   - Stage every file Critical files names. Confirm that `git ls-files -s claude/.claude/hooks/require-review-orchestrator-routing.sh` shows mode `100755`. Then run `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck` over the staged set.
   - Run `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py`, in the background if it runs long. This diff edits `select-tests.py`, a global trigger, so the script selects the full suite on its own (row 40). That selection is expected, not a widening by hand. The run must pass. The new contract lives mostly in these files:
     - `test_require_review_orchestrator_routing.py`
     - `test_hook_alignment.py`
     - `test_lib.py`
     - `test_activate_handoff_bypass.py`
     - `test_transcript_denials.py`
     - `test_select_tests.py`
     - `test_doc_counts.py`
     - `test_design_decision_files.py`
     - `test_config_py.py`
     - `test_config_lib.py`
     - `test_skills.py`
   - The commit must pass these gates:
     - `check-skill-length.sh`: `ready-for-review/SKILL.md` and `review-pr/SKILL.md` stay at 200 lines, `subagent-delegation/SKILL.md` at 202 and `code-review/SKILL.md` at 496.
     - the `/skill-review` gate. One pass covers all four edited SKILL.md files when all four are staged before `marker.sh write skill-review`, because the marker hashes the staged diff across every skill pathspec (`marker.sh:31-33`). A later edit to any of them needs a fresh pass.
   - `/code-review` dispatches `claude-hook-review` for the new hook and the `config-keys.psv` change, and `/skill-review` for the four SKILL.md files.
5. Before returning, the dispatch reads the edited text to confirm each `unrouted` site (rows 44, 46, 50):
   - In `ready-for-review/SKILL.md` § "3. Code review (halt on findings)", the sentence that runs the cumulative `/code-review` names args "unrouted <path>". Neither fix-commit round in steps 3 and 4 names `unrouted`.
   - In `review-pr/SKILL.md` § "Step 5 — Line-level pass", the sentence that invokes `/code-review` names args "unrouted <path>".
   - In `code-review/SKILL.md` § "Ripple effect triage", the diff-artifact note carries item 25's clause.
   - In `subagent-delegation/SKILL.md` § "Exception: gate/review loops stay orchestrator-driven", the HALT step names args "unrouted" and the stop clause is unchanged, and the request parenthetical names the review-orchestrator routing gate.
   - No edited skill sentence names the hook's filename.
6. After merge, observe the following on the engineer's account, from a session started after the pull and after `./install.sh` (row 52). These are observations, not CI (G4):
   - `readlink -f ~/.claude/hooks/require-review-orchestrator-routing.sh <config-dir>/hooks/require-review-orchestrator-routing.sh <config-dir>/agents/review-orchestrator.md` resolves each into the checkout, and `[ -x "$(readlink -f <path>)" ]` confirms each file exists (`readlink -f` prints a path for a missing file). Check this before reading a missing deny as a design failure.
   - Run each `config-get.sh` check in a shell or session whose `CLAUDE_CONFIG_DIR` is the account being enabled.
   - With no key row, `~/.claude/scripts/config-get.sh review_orchestrator_routing` exits 1, and a routine `/code-review` runs inline with no denial.
   - After adding `review_orchestrator_routing = true` to that account's `<config-dir>/claude-config.toml`, the same command exits 0.
   - On the next routine round, the gate denies a `Skill(code-review)` call that carries no args or descriptive-prose args. The reason starts with `Blocked by review-orchestrator routing gate:`, and a `review-orchestrator` dispatch follows.
   - Inside that dispatch, the agent's own `Skill(code-review)` call passes the gate: the subagent transcript shows no routing-gate denial.
   - A `/ready-for-review` cumulative pass and a `/review-pr` line-level pass each reach `code-review` with no routing-gate denial, and each pass's args begin with `unrouted` (rows 13, 44). Each pass hands `comment-discipline-reviewer` the real diff-file path, not `unrouted` (row 35). If one hands over the word, apply row 35's fallback.
   - On a branch with a gate-routed round, `/ready-for-review` step 4 either passes or reports a silent-abbreviation finding for the denied block. A finding of that kind clears through step 4's stated escape (rows 53, 62), and it confirms the follow-up issue's case.
   - A return with a `HALT:` entry and a `no-match` check leads to an inline round that passes the gate with args "unrouted". That round never treats `unrouted` as a diff-file path (row 35). The rest of that review loop then runs inline with args "unrouted" until a clean round writes the marker, so a fix it dispatches is re-reviewed without a further deny (row 54). A fix re-run after a user-asked or typed inline round is routed.
   - A typed `/code-review` runs in the main thread (row 48).
   - Setting the key to `false` restores inline rounds on the next call, with no restart.

## Out of scope

- **Routing a typed `/code-review`.** It stays unrouted by design (row 48), and `/review-pr` is the one-off review.
- **Telling a gate-denied `Skill(code-review)` block apart from a real invocation in every consumer that reads one.** It becomes a follow-up issue naming these consumers:
  - `detect_round_windows` in `review_rounds.py`;
  - the `Skill`-only matcher in `cmd_judgment_pair`;
  - the tally in `cmd_skill_invocation`;
  - the per-branch skill tally in `cmd_subagent_mix` (`subagent_mix.py:229-231`);
  - `skill-fidelity-report.sh`, through `skill-invocation --include-subagents`, which lists one extra main-thread `code-review` row for a gate-denied block. That row is not a finding source (rows 53, 62);
  - the `review-trace` timeline's `skill` event;
  - `evals/run_skill_evals.py` runtime cases, where detection should still hold, because a denied call still emits its block.

  Each consumer tells the two apart by the registered `review-orchestrator routing` label. The fix must also decide how it relates to the rule that a denied dispatch still counts as a round (CHANGELOG.md:11). The key ships off by default, so only key-on accounts' figures change.

  Owner step: at hand-off, the main session drafts the issue, with its title and the consumer list above, and asks the engineer whether to file it. No issue is filed without the engineer's confirmation. If it is filed before merge, the record's known-gaps entry cites its number. Otherwise the PR body lists it as pending.
- **The rewiring the record holds back (row 20).** That means widening `TRIGGER`, reconciling the Model & Effort Routing section of CLAUDE.md (record:42), adding the README agent paragraph and hook-transitions row, and flipping the default.
- **Exempting this gate from the shared fail-closed payload contract (row 11).** The plan could change it by editing `test_hook_alignment.py`, but exempting one gate needs a decision about the gate taxonomy.
- **An `if` filter on the settings entry.** Whether `if` accepts a `Skill(...)` pattern is unverified. An exact-name pattern would also miss a qualified `plugin:code-review` or `<dir>:code-review` call. The script's own filter stays the only one, and Verification step 3 times the unfiltered key-off cost instead.
- **A "Gate deadlock recovery" entry for this gate in `docs/hooks.md`.** The gate's off switch and its repeat-deny recovery live in its Gate hooks bullet (item 13). A second entry would restate them.
- **A `test_config_get.py` parametrization for the new key.** `config-get.sh` is schema-driven, so the new `config-keys.psv` row needs no per-key case there. Verification step 6 observes its exit codes.
- **A prune step for retired config keys in `migrate-legacy-config.sh`.** The record states the withdrawal form instead (row 43).
- **A `DocCountFact` for the `docs/hooks.md` count of `cooperative` rows.** No test pins that count today, and pinning it is a separate convention change.
