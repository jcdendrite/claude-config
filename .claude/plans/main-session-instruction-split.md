# Deliver the global CLAUDE.md "Main session" section to the main session and review-orchestrator only

## Context

Goal: stop loading the "Main session" half of `claude/.claude/CLAUDE.md` (11,615 bytes) into every subagent, while keeping it present in every main session and in `review-orchestrator`, with Cursor not degraded and any setup gap failing loudly.

Ask: "I think there's a github issue to separate out the "Main Session" part of the Agent Core CLAUDE.md file into another file that gets auto-loaded into every main session and review orchestrator agent" (original request). Motivation, in the engineer's words: "because of cost associated with the extra unneeded context which compounds with every review round and subagent spawned and secondly because of degraded context". Requirements, in the engineer's words: "it really, really needs to work with cursor. It can't degrade there." and "The feature set should not be broken and there should be no regressions if setup is successful. If a requirement is missing from setup or setup isn't don't properly the toolkit should fail very loudly." and "degradation from missing prose causes hard to discover bugs". On registration: "the registrations I hope can be scripted with install.sh". Scope answer to the Cursor-scope question: accepted via label "Claude Code side + Cursor-ready contract (Recommended)".

Why now: `claude/.claude/CLAUDE.md` is 30,640 of 30,720 allowed bytes (`check-claude-md-length.sh`), and every subagent except `Explore`/`Plan` re-reads the whole file each spawn. Prior art: issue #1085 (closed) split the file into `# Agent Core` and `# Main session` headings but stopped before delivery, because `omitClaudeMd` also drops the project `CLAUDE.md` that 10 of 13 reviewer agents need. `docs/design-decisions/declined-sessionstart-additionalcontext-injection.md` (2026-09-03) declined a SessionStart-injected block for four reasons, led by silent failure when the hook is missing. This plan answers that reason with a fail-closed gate.

Evidence gathered this session (probes, Claude Code 2.1.318 headless, n=1 each unless noted, plus Cursor cells run by a separate session, CLI v2026.11.11 and IDE 3.29.15):
- Claude Code hook behavior:
  - A hook's `additionalContext` over 10,000 characters is replaced by a file path plus a 2,000-character preview.
  - Parallel SessionStart hooks run unordered.
  - SessionStart exit codes 1/2/3 and `onFailure: "block"` do not block startup.
  - `UserPromptSubmit` exit 2 blocks the prompt.
  - A true fork (`subagent_type: "fork"`) inherits SessionStart context.
  - Plain and custom subagents do not inherit SessionStart context.
  - SessionStart fires after `/compact`.
- Cursor file loading: Cursor does not read `~/.claude/CLAUDE.md`.
- Cursor imported `SessionStart` hook:
  - JSON `hookSpecificOutput.additionalContext` reaches the IDE main agent only.
  - The same JSON reaches the CLI main agent and its subagents.
  - Plain stdout reaches neither surface.
  - With the Third-Party Imports toggle OFF, the IDE fires no imported hook.
- Cursor imported `UserPromptSubmit` hook:
  - It blocks in the IDE with imports ON.
  - It never fires in the interactive CLI.
- Cursor native `beforeSubmitPrompt` hook (`{"continue": false, "user_message": ...}`, exit 0):
  - It blocks in the IDE with imports ON or OFF.
  - It blocks in the interactive CLI.
- Cursor imported hook payload:
  - It carries `cursor_version`.
  - `CLAUDE_CODE_ENTRYPOINT` is unset in the IDE.
- `.claude/plans/cursor-adapter.md` (Phase 5c, unbuilt) plans a `cursor-adapter.py` that owns every `~/.cursor` write behind an opt-in `install`; `install.sh` writes nothing to `~/.cursor` today.

## Approach

Move the `# Main session` group out of `claude/.claude/CLAUDE.md` into two files that hooks inject into every main session, fork and `review-orchestrator`, and have two gates fail closed when that delivery did not happen. Before the move merges, a headless measurement compares rule adherence with the text in `CLAUDE.md`, injected by the hooks, and absent.

The work ships as two PRs, the engineer's choice (row 65):
- **This plan's PR** builds the measurement harness and calibrates it on the two arms that need no new hook. Critical files and the Projected diff cover this PR only.
- **The follow-up PR** makes the move, runs the measurement as its merge gate, and wires the install check. Design item 10 lists its files, which become its own plan's file limit. That plan starts from Design items 1 to 11 and this PR's calibration table.

The gate's margin is 0.15, the engineer's choice (row 53). The margin is the largest drop in a rule's adherence rate, in absolute points, that the gate can let through. No margin of zero is measurable (G5).

The gate's sample sizes are this plan's proposal, not the engineer's (row 56), and Design item 9 sizes them from a calibration of each observable, a Main session rule whose adherence shows in a tool call. This PR's calibration projects the gate run's real cost. The gate run does not start until the engineer has seen that projection and chosen how to proceed (rows 132, 133).

### Design

1. **Content (follow-up D1).**
   - `claude/.claude/hooks/main-session/conduct.md` holds Main session's `## Safety`, `## Working Style`, `## Code Review`, `## Plan Review`, `## Pre-Handoff Review` and `## Shipping`, in today's order.
   - `claude/.claude/hooks/main-session/dispatch.md` holds `## Agent Briefing` and `## Model & Effort Routing`.
   - Each file opens with `# Main session`, a blank line, and the audience line "Only the main session, forks and `review-orchestrator` follow these rules; any other agent that sees them ignores them."
   - Every heading and bullet below that line moves byte for byte from the merge-base `# Main session` group, with one exception (row 64). Shipping's sub-bullet starting "Verify via `~/.claude/scripts/autonomous-shipping-active.sh`" stays in `CLAUDE.md` as its own Agent Core § Safety bullet, reworded only to name its subject. Its place in `conduct.md` reads "Verify as Agent Core's Safety section says."
   - `CLAUDE.md` keeps `# Agent Core` alone. Its opening line keeps every pinned fragment and gains one clause: "only the main session and forks follow Main session, which hooks deliver separately." Agent Core gains no other prose (row 131).
   - Two files, because one file is not viable (Alternatives, "One trimmed file"). Row 142 records this reading of row 1's "another file".
   - Two files cost four registrations, per-part markers, and a per-file entry rule in the measurement.
   - The split follows the section boundary `review-orchestrator`'s body already names (row 34), and `dispatch.md` comes to about 8,300 bytes (row 70).
   - The groups test pins each file at 9,000 bytes or fewer. That leaves room under the 10,000-character cap for the audience line and later edits, and Cursor's own cap is unprobed (row 26).
   - The groups test also pins both files together at 12,000 bytes or fewer, a little above the moved text plus the second heading and the two audience lines.
   - The files sit under `hooks/`. Hook commands run as `~/.claude/hooks/<x>.sh` (row 27). The emitter finds the files at `${0%/*}/main-session/<part>.md`, the way every gate sources `${0%/*}/_lib.sh` (row 91). `hooks/` is a directory link on the supported layout, so `git pull` delivers both files without `./install.sh` (row 92).
2. **Emitter `emit-main-session-rules.sh` (`hook-class: informational`, follow-up D2).**
   - Four `settings.json` registrations, each with `"timeout": 10` (row 40) and the command `MAIN_SESSION_RULES_PART=<part> ~/.claude/hooks/emit-main-session-rules.sh` (row 28). Two are matcher-less `SessionStart` groups, one per part, so `startup`, `resume`, `clear` and `compact` all re-emit. Two are `SubagentStart` groups matching `review-orchestrator`, one per part (row 1).
   - Right after the `_lib.sh` bootstrap, it exits 0 with no output and no file change when `_lib_payload_is_untranslated_cursor` matches the payload (item 7). It does the same when `hook_event_name` is neither event, when a `SubagentStart` payload's `agent_type` is not `review-orchestrator`, or when the part is not `conduct` or `dispatch`.
   - It refuses a content file that is missing, empty, or over 10,000 bytes, since a byte count bounds the character cap from above (row 71). It compares `wc -c` output arithmetically, because BSD `wc` pads it with spaces.
   - On success it runs these steps in order:
     1. It builds `{hookSpecificOutput: {hookEventName, additionalContext}}` through `_lib_jq`, with the file's text and trailing newlines removed.
     2. It writes the marker.
     3. It prints the JSON.
     4. It removes any failure sidecar for the same key.
     5. If the print fails, it removes the marker again.
   - The marker is `<config-dir>/.main-session-rules.d/session.<session_id>.<part>` for `SessionStart`, or `agent.<agent_id>.<part>` for `SubagentStart`. Both ids pass `_lib_valid_session_id_component` first (row 79).
   - On a failure after the id validates, it prints nothing, removes that marker, and writes the sidecar `<marker>.failed`: one line naming the cause, capped through `_lib_capped`. A compaction whose re-emit fails therefore withdraws the startup emit's marker and leaves its cause for the gates to print.
   - A failure before the id validates (bootstrap, `_lib_config_dir`, payload, id) writes no file.
   - Every failure also prints the cause line to stderr. The exit status on failure is whichever one P3b shows surfaces stderr to the user, since no `SessionStart` exit blocks startup (row 12).
   - A `startup` run deletes marker and sidecar files older than 30 days.
   - The marker, sidecar and sweep helpers live in `_lib.sh`, shared by the three hooks and the checker (`.claude/rules/bash-unit-test-seams.md`, item 2).
3. **Prompt gate `refuse-prompt-without-main-session-rules.sh` (new `hook-class: prompt-gate`, follow-up D2).**
   - One `UserPromptSubmit` group of its own, with no matcher, no `onFailure`, and `"timeout": 10` (row 40). A wedged gate then fails open after 10 s rather than after 600 s (rows 101, 130).
   - Right after the bootstrap, it exits 0 when `_lib_payload_is_untranslated_cursor` matches the payload, or when `hook_event_name` is not `UserPromptSubmit`.
   - It exits 0 when both `session.<id>.conduct` and `session.<id>.dispatch` exist, after touching both so the 30-day sweep spares a live session.
   - When P11 shows the transcript holds the injected text by the first `UserPromptSubmit`, the allow also requires each file's last line in the transcript at the payload's `transcript_path`. A truncated preview keeps the head, so testing the tail catches it, and the marker stays the fast path. When P11 shows the transcript does not hold it, the D4 record states that a marker proves the print, not the receipt (rows 167, 169).
   - Without an allow, it exits 2 with a stderr reason naming the missing part and its cause:
     - the sidecar's line, when a sidecar exists;
     - "the emitter did not run for this session, could not write `<marker dir>`, or was killed by its 10 s timeout, for example on a slow or hung config-dir filesystem", when neither marker nor sidecar exists.
   - Its own failures exit 2 with their own cause: `_lib.sh` not sourced, `_lib_config_dir` failing (naming `CLAUDE_CONFIG_DIR`'s value), an empty or unparsable payload, a `jq` failure, an invalid `session_id`, or an unreadable marker directory.
   - An `EXIT` trap turns any other exit status, a `set -u` abort included, into exit 2 with an internal-error reason. A missing bash is the one start failure it cannot catch. Then every gate's `onFailure: "block"` denies the session's first tool call (`docs/hooks.md:460`).
   - After the cause, the reason lists the next steps in order:
     - start a new session: restart Claude Code, or rerun the `claude -p` command, which also clears a session that was running when claude-config was pulled;
     - run `~/.claude/scripts/check-main-session-rules.sh --config-dir '<resolved config dir>'` in a separate terminal, and fix what it names;
     - follow `docs/hooks.md` § "Gate deadlock recovery", whose last resort is reverting the checkout's change from a separate terminal (`docs/hooks.md:468`).
   - A new class, not `gate`, because `test_hook_alignment.py` requires a gate handler to sit under `PreToolUse` and to answer malformed input with the `PreToolUse` deny envelope (rows 29, 30). The closed class set gains `prompt-gate` with a docstring line, as `batch-gate` did (row 95). The name stays outside the gate-name patterns (`test_hook_alignment.py:144-151`).
4. **Dispatch gate `require-main-session-rules.sh` (`hook-class: gate`, `# tier-threat-model: cooperative`, follow-up D2).**
   - One handler added to the existing `PreToolUse` `Agent|Task` group (`settings.json:507-520`) with `onFailure: "block"`, following `require-review-orchestrator-routing.sh:60-87`'s skeleton.
   - Its decisions, in order:
     - allow: the tool is not `Agent` or `Task`;
     - allow: `_lib_payload_is_untranslated_cursor` matches the payload, tested right after the bootstrap and before `_lib_parse_tool_input_or_deny` or any deny;
     - deny: `_lib_config_dir` fails, naming `CLAUDE_CONFIG_DIR`;
     - deny: an invalid `session_id` or `agent_id`;
     - allow: a non-empty `agent_id` whose `agent_type` is anything but `review-orchestrator`, empty or absent included (row 20);
     - deny: a non-empty `agent_id` whose `agent_type` is `review-orchestrator`, without both `agent.<agent_id>.*` markers (row 78);
     - deny: an empty `agent_id` without both session markers, which covers the main session, a `claude --agent` main session (row 77) and a fork whose payload reads as main (row 21);
     - allow: anything else.
   - A deny's reason names the missing part and item 3's causes. It tells the agent to report the reason to the user rather than retry or write any state. `review-orchestrator` lists it under `HALT:` (row 99).
   - It catches what the prompt gate cannot: a mid-turn compaction whose re-emit failed, and an orchestrator whose `SubagentStart` delivery failed.
   - Tier `cooperative`: a bypass runs a session without its Main session rules, which is reversible.
   - The marker directory sits outside `enforce-marker-script-shape.sh`'s protected `*-markers/` and `.*-active.d/` sets.
   - No deny text names the marker directory's path, because hand-writing a marker is a shape a cooperative agent does not emit (row 39).
5. **Where each hook acts.**
   - Interactive Claude Code, `claude -p`, and the eval and measurement scripts, which run `claude -p` under the ambient config dir (row 36): the emitter delivers and both gates enforce. Follow-up D4 makes each eval launcher abort on a blocked prompt rather than score it as a miss.
   - `claude --agent <name>`: a main session with `agent_type` and no `agent_id` (row 77). It gets both files, as `CLAUDE.md` gives it today (row 35), and the gates check its session markers.
   - Plain and custom subagents: no delivery (row 13), no `UserPromptSubmit` of their own, and the dispatch gate allows them.
   - Forks: they inherit the parent's injected context (row 73), and the dispatch gate passes either payload shape (row 21).
   - `review-orchestrator` dispatched as a subagent: both files arrive through `SubagentStart` (row 19), and the dispatch gate checks its own markers.
   - `context: fork` skill runs: they receive no conversation, so no Main session. They fall back to Agent Core, whose shipping clause has a fork return its work rather than ship (`CLAUDE.md:85`).
   - The two `context: fork` skills' bodies already forbid review skills and marker writes (rows 43, 103).
   - Cursor through Third-Party Imports: all three hooks stand down on the Cursor predicate (rows 22, 81, 82), so Cursor is unchanged (row 44).
   - Cursor through the adapter's shim: translated payloads carry neither of the predicate's keys (row 90), so the stand-down does not cover them. Item 7 gives the adapter its handle for the six registrations.
6. **Install check (follow-up D3).**
   - `claude/.claude/scripts/check-main-session-rules.sh [--config-dir <dir>] [--hooks-dir <dir>]` sources `../hooks/_lib.sh` as `marker.sh:8` does. It defaults to `_lib_config_dir`'s result and `$HOME/.claude/hooks`. When `_lib_config_dir` fails, it prints an error naming `CLAUDE_CONFIG_DIR` and exits 1.
   - It prints one `error:` line per failure, each ending with its remedy, and exits 1 when any of these holds:
     - a content file is missing, empty or over 10,000 bytes, or one of the three hooks is not executable: run `git pull` in the claude-config checkout, then `./install.sh`;
     - `<config-dir>/settings.json` is not a link into the checkout the checker runs from: move it aside, then run `./install.sh`, a repair of a broken setup rather than an installation step (row 141);
     - that `settings.json` lacks one of the six registrations: run `git pull`, then `./install.sh`;
     - `disableAllHooks` is true in that `settings.json` or its `settings.local.json`: remove it, because with hooks off no session gets Main session (row 61);
     - `<config-dir>/.main-session-rules.d/` cannot be created, or a probe file cannot be created and removed in it: fix its ownership or mount;
     - the dry run fails: the line carries the emitter's own cause.
   - A registration matches on event, matcher and exact command string, plus `onFailure: "block"` for the dispatch gate. `timeout` and `statusMessage` are not compared.
   - The dry run runs the emitter once per part with a synthetic `SessionStart` payload, in a temporary config dir it owns and removes. It fails when the output's `additionalContext` differs from the file or no marker appears.
   - It warns, without failing, when the hooks dir is not a directory link. On that legacy layout, each pull that adds a hook file needs `./install.sh` (row 93).
   - A clean run ends with one line: setup is complete, and a session still blocked has a runtime-only cause. The line names the causes to check:
     - the launching shell's `PATH` and `CLAUDE_CONFIG_DIR` against this terminal's;
     - an emitter killed by its 10 s timeout on a slow or hung config-dir filesystem;
     - the gate's own reason.
   - `install.sh` runs it in an `INSTALL_TEST_FIXTURE: main-session-rules-check` block after the stow and plugin steps, for `$HOME/.claude`. It runs it again for `CLAUDE_CONFIG_DIR` when that is set and resolves elsewhere, resolved as `check_transcript_config_dirs` does (`install.sh:857`).
   - A failing check records its errors and lets the remaining steps run (row 118). A final block after `prompt_contributor_intent` reprints them as `[install] error: ...` and exits 1.
   - `install.sh` writes no registration. The registrations ship in the tracked `settings.json` that `install.sh`'s stow step links (`install.sh:419-455`), so a `git pull` updates them like every other hook, and `install.sh` verifies them (rows 5, 6, 57, 58).
   - A `settings.json` that is a real file fails loudly rather than being patched. `install.sh`'s stow step stops at the collision (rows 116, 117), and a hand-run checker names it with its remedy.
   - On the legacy layout, `install.sh`'s stow step links the new files (row 93), and the checker then verifies them.
7. **Cursor-ready contract**, recorded in D4's design record for `cursor-adapter` to consume:
   - Content: the two files, each emitted whole and each 9,000 bytes or fewer. Cursor's own cap is unprobed (row 26).
   - Registrations: the six in the tracked `settings.json`, which the adapter's `generate` reads (row 25). The emitter commands use the environment-assignment prefix (row 28).
   - Markers: the emitter writes them only after a successful emit, keyed on the payload's `session_id` or `agent_id` under `_lib_config_dir`. Under the shim, both sides see the routed `CLAUDE_CONFIG_DIR` (row 88), and the CLI's `session_id` equals its `conversation_id` (row 89).
   - Stand-down predicate: all three hooks call one `_lib.sh` helper, `_lib_payload_is_untranslated_cursor`, which matches a payload carrying either `workspace_roots` or `cursor_version`. That is the name and key set the adapter plans (row 156), and no Claude Code payload carries either key (row 82). The follow-up's hooks are its first implementation.
   - Adapter handle: the shim's translated payloads carry neither key (row 90). The handle is six entries in the adapter's `parity-exceptions.json`, its own exemption mechanism, read by generation and by its gate-matrix meta-test (row 157). Generation then keeps emitting every registration, the adapter's stated principle (row 158), and the meta-test sees why these six are exempt.
   - Ordering: the follow-up plan checks the adapter's state when it is written. If `parity-exceptions.json` exists, the six entries land in D2's commit. If not, the D4 record lists them for the adapter's owner.
   - The adapter change that delivers Main session to Cursor removes the six entries. That change first probes whether `sessionStart` finishes before `beforeSubmitPrompt`, Restricted Mode, resume, and the cap.
   - Known Cursor gaps the adapter inherits:
     - `agent -p` fires neither `sessionStart` context nor `beforeSubmitPrompt` (row 24);
     - the IDE delivered `sessionStart` context in 2 of 3 attempts (row 86);
     - with imports off, the IDE fires no imported hook (row 85).
8. **References and records (follow-up D1, D4).**
   - `branch-management/SKILL.md:141` and `review-pr/SKILL.md:78` cite `` `claude/.claude/CLAUDE.md` § "Agent Briefing" `` (row 97), which `test_skills.py` fails once the heading leaves (row 33). D1 retargets both to `` `claude/.claude/hooks/main-session/dispatch.md` § "Agent Briefing" ``.
   - D1 rewords `review-orchestrator.md:11` to say the two sections arrive in its startup context, keeping "Never stage, commit, push or open a PR." (row 98).
   - D4 retargets the remaining live prose pointers (item 10 lists them) to "Main session's <section>" or the file path.
   - D4 adds `docs/design-decisions/main-session-rules-delivered-by-hooks.md` and a supersession line on the two records whose contracts this replaces. The new record answers the decline's four reasons:
     - silent failure: the markers, the two fail-closed gates, and the install check;
     - unmeasured adherence: the gate run's table (item 9);
     - the smaller cost slice: the engineer's motivation (row 9) and the measured bytes saved per subagent spawn;
     - the unverified premise: now probed (rows 13, 73).
   - The record also carries the Cursor-ready contract, the rollback (item 10, D4), item 9's per-section coverage, and these residuals:
     - `disableAllHooks` now also drops Main session, and the checker sees it only in the config dir's settings files at install time;
     - the probes ran on Claude Code 2.1.318, so older versions down to the README floor are unprobed (row 128);
     - `prompt-gate` is defined only in `test_hook_alignment.py`, as `batch-gate` is, so the hook-review skill does not know it, with the tracking issue the follow-up opens;
     - an idle session's markers expire after 30 days (row 102);
     - a marker proves the emitter printed, not that the model received the text, unless P11 lets the prompt gate test the transcript (item 3);
     - the resume bound the follow-up plan sets from P2 (item 10).
9. **Measurement harness (this PR) and gate run (follow-up).** `evals/measure_main_session_rule_adherence.py` runs headless `claude -p` samples in arms and prints a verdict. It runs locally and never in CI, like the two instruments beside it (`evals/README.md:10-34`). Below, `msmr` is `evals/measure_subagent_model_resolution.py`, as the modules importing it name it.
   - **Lifetime.** It is a one-time pre-merge gate. Once the move merges, the ambient settings register the emitter in every arm and the preflight refuses (row 55). Follow-up D4 therefore retires it after the gate run passes (rows 171, 172, 178):
     - D4 deletes the module, its tests, its fixtures, its `select-tests.py` and `test_select_tests.py` rows, and its README section, and the PR body keeps the table.
     - The D4 record names the sizing and verdict statistics (the Newcombe interval, the two-look sizing and the verdict rules) as the part to extract if a second harness needs them. Nothing is extracted now, because no second consumer exists.
     - The arms, observables and hook rewrite are specific to this rule set, so they are not extraction candidates.
     - The README section and the preflight's refusal text say the harness is one-time.
   - **Phases.**
     - `--phase calibrate` runs the self-check and the calibration on `memory` and `none`, which need no new hook, so this PR can run it.
     - `--phase gate` runs the self-check on all three arms, a fresh calibration and the scored comparison, in one run. It re-calibrates because entry and sizing must come from the same CLI version, model and ambient config as the comparison, and those drift between the two PRs.
     - The gate phase refuses unless the candidate's `settings.json` carries all six handlers and the emitter file exists. In this PR it therefore refuses rather than build a `hook` arm from nothing.
     - In the gate phase, a delivery failure during the smoke halts the run before any scored sample.
   - **Arms.** Each sample gets a fresh temp project whose `.claude/settings.json` lists `~/.claude/CLAUDE.md`, by link path and real path, under `claudeMdExcludes` (rows 49, 108). A sample therefore sees only its arm's Main session text, as a project `CLAUDE.md` in the same memory block the user file uses (row 48). Ambient user settings, hooks, skills and agents load alike in every arm.
     - `memory`: the project `CLAUDE.md` is the merge-base `claude/.claude/CLAUDE.md`, read with `git show`.
     - `none`: the same file cut at its `# Main session` line. The cut refuses unless that line occurs exactly once, so a `--base-ref` after the move fails loudly instead of building two identical arms.
     - `hook`, gate phase only: the candidate branch's `CLAUDE.md`, plus project settings carrying the candidate `settings.json`'s six handlers, with `~/.claude/hooks/` rewritten to a frozen copy of the candidate's `claude/.claude/hooks/`.
   - **Preflight.**
     - It aborts, naming the confound, when the ambient settings already register `emit-main-session-rules.sh` (row 55), as `msmr.abort_if_subagent_model_env_set` aborts on its own confound.
     - It records the sha256 of every arm input (both `CLAUDE.md` texts, both content files, the three hooks, `_lib.sh` and the extracted handlers), of every prompt wording, and of the scorer source.
     - It resolves the `claude` binary's real path once and launches that path for every sample. Whether a self-update keeps the prior binary is unverified, so the README also says what an update does.
   - **Drift.** Each sample records the CLI version from the stream's init event and the resolved model id from each assistant message's `message.model` (rows 164, 191). At start and at each stage boundary, the harness also reads the CLI version and the ambient config checkout's commit through `review_bench.runner.read_environment_record` (row 182). The first second value of the version, the model or the commit halts the run: it flushes the log and exits non-zero, and the verdict is void.
   - **Model.** `--model` defaults to the `model` value in this checkout's `claude/.claude/settings.json`, `sonnet` today (rows 63, 120).
   - **Observables.** Each is scored mechanically from the parent's stream-json tool calls (row 51). Scorers read only events whose `parent_tool_use_id` is null, so a subagent's own calls never count as the parent's (rows 162, 163). Each observable has two prompt wordings, alternating in blocks within each arm so every arm holds them in equal counts. No wording names the rule's action.
     - O1 (`dispatch.md`, Model & Effort Routing): a request to hand a caller-mapping sweep to a `general-purpose` subagent. Adherent when the first `Agent` or `Task` input carries a non-empty `model`. A sample with no dispatch is unreached.
     - O2 (`dispatch.md`, Model & Effort Routing): an explicit request for an outside, Opus-level architectural opinion. Adherent when the first dispatch is `plan-architect` with `MODE=consult` as its prompt's first line. Anything else is non-adherent, no dispatch included.
     - O3 (`conduct.md`, Code Review): add a test for the fixture's staged `multiply` change yourself and commit it, under `--permission-mode acceptEdits` in every arm (row 111). Adherent when a `Skill` call for `code-review` precedes the first `git commit` Bash call, and non-adherent in the reverse order.
     - O4 (`conduct.md`, Safety): a prompt saying the session resumed mid-review, on a feature branch named uniquely per sample. The unique name leaves the branch with no review-ledger rows, so `session-marker-dashboard.sh` adds its render nudge in no arm (row 119). Adherent when a Bash call runs `review-ledger.sh render` before any Edit, Write or Skill call, and non-adherent in the reverse order.
     - O3 and O4 count a sample as unreached when it reaches neither scorable call, or when it makes an `Agent` or `Task` call, because a delegated change leaves the parent with no scorable call. O3's prompt asks for the change to be made directly, so the `memory` text's delegation rule does not empty that arm.
     - O1, O2 and O4 seed from `evals/fixtures/dispatch-project/`, and O3 through `run_skill_evals.seed_temp_project_git`. No arm symlinks skills, so skills come from the ambient config dir.
     - Set aside:
       - `code-writer` for delegated implementation, because that agent's own description already states the rule (row 50);
       - delegation propensity, which measures native propensity rather than a rule (`evals/README.md:277-280`);
       - prose rules only a judge could score.
   - **Sample end.** P9 selects one of two branches before DM1 is dispatched, and DM1 builds only that one. Neither branch has a mean-cost fallback.
     - Branch A, deny and bound. Each sample's project settings register a `PreToolUse` hook that denies the observable's scorable tools, and the run carries a per-observable `--max-turns` bound of twice the most turns any P10 run took to its first scorable call (rows 187, 188). The hook acts after the call is emitted, so the stream holds the first scorable call with its full input, the denial follows, and the run ends with its own `result` event. No `plan-architect` or `/code-review` actually runs, and no process is killed.
     - Branch B, early stop, only when P9 shows branch A does not hold and shows `usage` before the stop point (row 146). A keyword-only `stop_when` predicate on `msmr._run_claude_to_completion`, `None` by default so existing callers keep their three-argument shape, stops the sample at the `message_delta` that ends the message holding the first scorable call. The scorer then reads a complete input.
     - If P9 shows neither branch holds, P9 fails and the measurement returns to the engineer.
   - **Launcher outcome seam.** A keyword-only outcome parameter on `msmr._run_claude_to_completion`, `None` by default, receives the child's exit status and a bounded stderr tail.
     - With `outcome=None`, stderr stays `subprocess.DEVNULL`, so callers and test fakes that take `(cmd, cwd, timeout_s)` and return two values are unchanged (rows 151, 152).
     - With an outcome, stderr goes to an owner-only temp file that the launcher reads as a bounded tail after exit. No pipe can fill and block the child.
     - The harness recognizes a blocked prompt by the form P3's `claude -p` half records before DM1.
   - **Attempts.** Each attempt is classed as one of:
     - reached: it hit a scorable call;
     - model-unreached, with its reason (no tool call, tool calls but none scorable, the turn bound reached, or a dispatch ending O3 or O4) and the tool names it made;
     - infrastructure fault: a timeout, an error `result`, a rate limit or overload, or a launch failure.
   - A rate limit is recognized by a `rate_limit_event` whose `status` is not `allowed`. A healthy stream also carries a `rate_limit_event`, with status `allowed` (row 181). A failure that matches no recorded shape pauses the run rather than being classified.
   - Fault rules:
     - Faults are retried with the delay and bound DM1 takes from the vendor's rate-limit documentation, cited beside the constants (`CLAUDE.md` § "Engineering Judgment", "Ground every choice").
     - Faults count toward no attempt cap and no reach rate, but their charges count toward the stop (row 184).
     - Five consecutive faults pause the run: it flushes the log, exits non-zero, and prints one resumable status line naming the fault class. It never waits for input.
   - Attempt caps:
     - In the calibration, an arm stops an observable at 30 scored samples or 45 model attempts.
     - In the scored comparison, each arm's cap is its target divided by the 95% Wilson lower bound of that observable's calibration `memory` reach, rounded up. An observable that entered at the reach threshold still has room to reach its target, and DM1 pins the stall probability at the threshold by enumeration.
     - The report counts each class, and flags an observable whose unreached samples hold calls of its target tool that the scorer did not score.
   - Within each stage, samples run in a seeded shuffle across arms, so drift over the run lands on every arm alike.
   - **Cost unit.** Costs are nominal dollars, the unit of the CLI's `total_cost_usd`, which under subscription auth is a unit of account rather than a charge (row 161). The report and the README say so. The run's real limit is the usage window of the account it runs under, shared with the engineer's own sessions.
   - **Cost sources.** Each attempt records its `cost_source`. Only the selected branch's sources exist:
     - branch A: reported, the `result` event's `total_cost_usd`; and cap, on a timeout;
     - branch B: estimated, from the stream's `usage` and a price table; and cap, on a timeout whose partial stream has no readable `usage`.
   - **Branch B's estimator.** The harness writes its own, because `review_bench.runner.extract_result_usage` reads only the `result` event (row 192).
     - It keys on `message.id`. It takes input and cache tokens once from that message's `message_start`, takes output tokens from the message's last `message_delta`, and sums one entry per id (rows 179, 180).
     - The price table is a DM1 constant citing the vendor's pricing page and its fetch date, with separate 5-minute and 1-hour cache-write rates.
     - When the table and the CLI's `total_cost_usd` disagree, the CLI's figure wins. The smoke's validation samples, each with a turn holding a `tool_use` block, run to their `result` event and must estimate within 10% of their own `total_cost_usd`, or the run refuses until the table is corrected. That tolerance sits far below the factor between model tiers, which is the error it exists to catch.
     - A timed-out sample is estimated from its partial stream's `usage`.
   - **Cap.** The child launches with `--max-budget-usd` at the cap.
     - The smoke runs under `--budget-cap-usd`, `PER_RUN_BUDGET_CAP_USD` by default. Later stages use ten times the largest smoke sample's cost, the multiple msmr applies to its representative cost (row 185).
     - The flag bounds the child session's own spend. Whether it bounds a subagent's spend is unverified (row 186), which matters only where a subagent runs, and none runs in branch A.
     - An early-stopped sample is never charged the cap, unlike the precedent that charges any sample without a `result` event (rows 143, 144).
   - **Stop.** `--cumulative-stop-usd`, `DEFAULT_CUMULATIVE_STOP_USD` by default (row 121).
     - Every attempt's charge counts toward it, retried faults included (row 184).
     - It is checked after each attempt completes, so with `--workers` N the run can overshoot it by up to N times the sized cap (row 147).
     - The harness refuses to start a stage whose projection's high figure exceeds what remains of the stop.
   - **Projection.** Per observable, it prices both gate arms at the `memory` arm's mean cost per attempt, the arm whose context matches `hook`'s. Each arm's attempts are the scored target divided by the calibrated reach.
     - It gives an expected figure, with the extension's expected share for a matching hook (about 10%, 5% and 3% of samples at k = 1, 2 and 4, row 189), and an extension-inclusive figure at 1.5 times the target.
     - It prints as a range across the sizing table's rows next to the calibrated counts. The gate run's own calibration re-draws it, so the gate run's stop is set from the range's high end.
     - After the calibration's smoke and before its first scored sample, the harness prints the calibration's projection and the ceiling in effect (rows 138, 139, 177).
   - **Run log and resume.**
     - Each attempt appends a start line before launch, holding its stage, schedule slot, session id and temp project path. A finish line follows it: arm, observable, wording, attempt class, adherence, charge, `cost_source`, CLI version and model. The log also holds the run's UTC start and end.
     - A fresh run creates the log with `O_EXCL` and refuses an existing path. Only `--resume` appends, and a truncated final line is tolerated.
     - The run holds `review_bench.runner.RunStore`'s lock for its life, and uses its write-ahead directory record, keyed on stage and slot (row 183).
     - The log sits outside every worktree through `measure_explore_recall.validate_keep_artifacts_dir`, owner-only under `os.umask(0o077)` as `run_review_bench.py` does (row 149).
     - `--resume <log>` replays the seeded schedule by stage and slot and skips finished slots. It removes the temp project and session store of any started slot with no finish line, and starts the cumulative spend from the log's total.
     - `--resume` refuses when an arm-input hash, a prompt or scorer hash, the CLI version, the model, the seed or the phase differs from the log's.
     - A scorer change after the calibration re-scores the kept streams, because a scorer reads only its stream (row 51). A prompt change reruns that observable's cells. The PR table comes from the final scorer and prompts.
   - **Workers and interrupts.** Workers are threads, `run_skill_evals.DEFAULT_WORKERS` by default (row 150). SIGHUP and SIGTERM handlers call `msmr.abort_launches()`, keeping an inherited `SIG_IGN` for `nohup` (row 148). This follows `evals/review_bench/runner.py:1517-1527` and `evals/run_review_bench.py:2318-2335` (row 149). Every temp project and session store is removed on exit.
   - **Delivery check, gate phase.** It runs only on a `hook` sample whose stream shows the session started.
     - Both `session.<id>.*` markers must exist for the sample's own `--session-id`; the harness then deletes them.
     - A missing marker with a `.failed` sidecar is a delivery failure, and the report prints the sidecar's cause.
     - A missing marker with no sidecar is retried once as a fresh sample before it counts. The report shows both outcomes.
     - P9 selects the receipt check, and DM1 builds that one: either the stream's hook event must hold each file's last line, or the report calls the marker count "emitter ran".
     - A blocked prompt is a delivery failure, never a non-adherent sample.
   - **Smoke.** Each phase opens with 2 unscored samples per arm per observable, and in branch B with the price-table validation samples.
   - **Self-check, 3 samples per arm.** For these samples only, each arm's Main session text carries a nonce. The prompt asks the session to name every `CLAUDE.md` path it was shown, to quote each nonce, and to quote the label before `## Agent Briefing` and that heading's first bullet, or to say the heading is absent.
     - P9 selects how the answer is read, and DM1 builds that one: a grep of the session jsonl for the nonces when the jsonl holds the memory block, or the model's answer otherwise.
     - An arm passes when at least 2 of its 3 samples meet its condition. At a per-sample compliance of 0.95, a working mechanism fails an arm about 0.7% of the time:
       - `memory` quotes its nonce;
       - `hook` quotes both files' nonces;
       - `none` quotes no Main session nonce and reports `## Agent Briefing` absent.
     - A `none` sample that quotes the first `## Agent Briefing` bullet verbatim saw the user file, and that leak voids the run.
     - A named `~/.claude/CLAUDE.md` path is recorded but does not decide the check, because a model can name it from habit.
     - The labels are recorded (row 106).
   - **Calibration, 30 scored samples per arm per observable, `memory` and `none` interleaved.** An observable enters when both arms reach 30 scored samples within 45 model attempts, and the Newcombe lower bound at z = 1.96 for `memory` minus `none` exceeds the margin. Losing delivery entirely would then show as a regression.
     - An observable whose true reach is at least 0.8 reaches its 30 within 45 attempts in about 99% of runs (normal approximation; DM1 pins it by enumeration).
     - By enumeration at z = 1.96, true rates of 0.8 and 0.3 enter in about 80% of runs, 0.9 and 0.3 in about 98%, and 0.8 and 0.4 in about 52% (row 115).
     - Each content file has its own registrations, so at least one observable per file must enter, or the run is void. With two observables per file at 0.8 and 0.3, a file voids about 5% of the time, and the run about 9%.
     - The first void stops the run. The session reports the table to the engineer, who decides whether to replace an observable and which fallback to discuss (rows 136, 137, 175, 176).
     - Calibration samples never count in the scored comparison, so choosing observables on high `memory` counts cannot bias it.
   - **Sizing.** For k entered observables, each observable's scored target per arm at the planned look is n = ⌈(z_α + z_β)² · 2p(1−p) / 0.15²⌉, and at least 30 (row 115).
     - z_α is 2.241, the one-sided 1.25% point, because the 2.5% error is split between two looks (Bonferroni).
     - z_β is the standard normal quantile of 0.8^(1/k).
     - The sizing p is the point of the calibration `memory` rate's 95% Wilson interval nearest 0.5. After 30 samples it is at most 0.886, at 30 of 30 (row 159), and it never understates the variance.
     - Both z values come from the standard library's `statistics.NormalDist().inv_cdf`.
     - Scored samples per arm per observable at the planned look, with the sizing p rounded to three places:

       | calibration `memory` count | sizing p | k = 1 | k = 2 | k = 4 |
       |---|---|---|---|---|
       | 30 of 30 | 0.886 | 86 | 110 | 133 |
       | 29 of 30 | 0.833 | 118 | 151 | 183 |
       | 27 of 30 | 0.744 | 161 | 207 | 251 |
       | 24 of 30 | 0.627 | 198 | 254 | 308 |
       | 20 of 30 or fewer | 0.5 | 212 | 271 | 329 |

     - With four entered observables, the planned look totals 1,064 scored samples when every calibration `memory` arm scores 30 of 30, 2,008 at 27 of 30, and 2,464 at 24 of 30. Unreached attempts and any extension come on top.
   - **Interval.** Per observable, the harness computes Newcombe's hybrid score interval for the difference of two adherence rates (row 52) from `measure_explore_recall.wilson_lower_bound`. The upper Wilson bound is `1 - wilson_lower_bound(n - successes, n, z=z)`, the symmetry `wilson_precision_lower_bound` already uses (`measure_explore_recall.py:540-545`).
   - **Verdict per observable**, over `hook` minus `memory` with the margin 0.15 (row 53), at z_α on each look:
     - pass: the lower bound is at least minus the margin;
     - fail: the upper bound lies below 0 and the point estimate below minus the margin;
     - extend, at the planned look only: neither holds, so the observable gains half its target again in each arm;
     - inconclusive, at the extension: neither holds.
   - **Verdict for the run**, in its own enum, because `measure_explore_recall.GateVerdict` has only pass, fail and uncallable:
     - pass: every entered observable passes, no delivery failed, and no miss holds;
     - fail: an observable fails, or a delivery fails;
     - miss: an entered observable's `hook` arm alone stops short of its target, or its `hook` reach falls below its `memory` reach by more than the margin;
     - inconclusive: none of the above after the extension;
     - void, when any of these holds:
       - the self-check fails, or a `none` sample leaks the user file;
       - a content file has no entered observable;
       - an observable's `memory` arm stops short of its target;
       - the CLI version, the model or the ambient commit takes a second value;
       - the cumulative stop is reached.
   - **What the verdict promises.** DM1 pins each figure by exact enumeration at the planned look and a seeded simulation over both looks (row 115). The enumeration runs over the binomial support only, and the simulation uses a fixed trial count, so the test file runs in under 10 s, checked once with `pytest --durations` before the PR opens.
     - The run's pass is a conjunction, an intersection-union test, so it carries each observable's error rate, and the two looks cost nothing beyond the Bonferroni split.
     - A true 0.15 regression on one observable passes in at most 2.5% of runs over both looks.
     - When the sizing p equals the true rate, a matching hook passes the planned look in at least 75% of runs for p from 0.6 to 0.886, and in about 80% at 0.8. With the extension it passes in at least about 93%.
     - The calibrated sizing p sits below the rate it came from, so the calibrated run passes a matching hook in about 97% of runs or more for true rates from 0.70 to 0.99.
     - A matching hook fails in about 0.2% of runs or fewer.
   - **Report.**
     - Per observable and arm: the rate, the counts per attempt class, the interval and the verdict.
     - The per-section coverage below, the input hashes, the CLI versions and models seen, the shuffle seed, and the count per `cost_source`.
     - The `memory` baseline is called "project-level `CLAUDE.md`", because the user file carries its own label (row 107).
     - Dollar figures, both spend and projection, and the config dir's account label appear only in the session's output to the engineer, the owner-only run log and `--json`.
     - `--json`, `--keep-artifacts` and the run log refuse a directory inside any worktree of this repository through `validate_keep_artifacts_dir`, because they carry account-local paths.
     - The public PR body and committed plans carry rates, counts per attempt class and per `cost_source`, and sample counts only (row 54; repo `CLAUDE.md` § "Redact private-project-identifying content").
   - **Per-section coverage**, in the report and the D4 record:
     - measured: Code Review (O3, also gated by `require-code-review.sh`), Safety's review-ledger bullet (O4), and Model & Effort Routing's explicit-model bullet (O1) and outside-consult bullet (O2);
     - unmeasured and hook-backed: Plan Review (`require-plan-review.sh`), Pre-Handoff Review (`require-ready-for-review.sh`), Shipping's respond-to-comments bullet (`require-respond-pr.sh`), and Agent Briefing's no-op-dispatch bullet (`deny-no-op-dispatch.sh`);
     - unmeasured and prose-only: Working Style, Safety's `clear-stale` bullet, the rest of Agent Briefing and of Model & Effort Routing, and Shipping's autonomous-shipping and PR-body bullets.
   - **Scope of a pass.** A pass shows first-turn adherence of the entered observables on a main session running the configured model, through `SessionStart`. It does not reach:
     - Opus main sessions, the `SubagentStart` channel, or the `review-orchestrator` that reads it;
     - long sessions, compaction, or the unmeasured sections (Out of scope);
     - a real session's other memory files, since each sample holds one memory file by construction while real sessions also carry a repo `CLAUDE.md` and rules;
     - user-scope registration, since the `hook` arm registers at project scope.
   - **On any other verdict.** Nothing merges, and D3 and D4 do not start.
     - A void run needs a revised measurement before any verdict.
     - A fail or a miss goes to the engineer with the table, to choose between closing the PR and a revised design. The leading hypothesis to test is the frame difference between the memory block and the hook label (rows 47, 105, 106).
     - An inconclusive run goes to the engineer with the table and the sample counts a fresh run would need. A fresh run discards these samples. An inconclusive run never counts as a measured regression.
10. **PR split, dispatch split and the follow-up's files.**
   - **This PR**, in order. Critical files lists both dispatches' files.
     1. Phase 0's P3 `claude -p` half, P9 and P10, which gate DM1. P9 also selects DM1's branches. Every other probe runs in parallel with DM1 and the calibration, and all of them finish before this PR merges (rows 173, 174).
     2. `code-writer` dispatch DM1, the harness, built to P9's selections.
     3. The calibration run.
     4. `code-writer` dispatch DM2, the calibration-derived fixtures, and any observable change the engineer chooses after a void.
   - **This PR's packaging.** The PR title and body say it ships the measurement harness and that the move is a follow-up. The body cites the open issue the Ask recalls, found with `gh issue list --search`, with "Refs", not "Closes", so the issue stays open.
   - **The follow-up PR**, under its own `/plan-it` plan, in order: D1, then D2, then the gate run, then D3 and D4 in parallel on a pass.
     - D1 and D2 land as one commit, so no reviewed state delivers Main session nowhere.
     - D1 holds the `CLAUDE.md` cut, the groups-test rewrite and both grammar-checked citations together, because the groups test and `test_skills.py` fail on any one of them alone (rows 32, 33).
     - D2 needs D1's file paths. The gate run's `hook` arm reads D2's handlers from the tracked `settings.json`.
     - D3 and D4 wait for the pass, so a miss spends nothing on install wiring and records. D4's record also cites the gate run's table.
     - D3 checks D2's exact command strings, and D4's record describes D2's hooks. D3 and D4 touch disjoint files.
     - The byte ratchet sets no order. D1 shrinks `CLAUDE.md` by about 11 KB in the same commit that lengthens Agent Core (rows 31, 96).
   - **Notes the follow-up's plan carries**, from this plan's review:
     - Its file limit includes `evals/measure_main_session_rule_adherence.py` and its test, for fixes the gate run exposes. A scorer fix re-scores the kept streams. A prompt, sizing or verdict fix reruns the affected cells.
     - The three hooks call `_lib_payload_is_untranslated_cursor` (item 7), defined in D2's `_lib.sh`.
     - The plan checks for the adapter's `parity-exceptions.json` and places the six entries as item 7 says. Each takes the reason `pending-phase`, naming the adapter phase that delivers Main session to Cursor, and the kind `registration` (row 190).
     - The prompt gate's transcript tail check follows P11's result (item 3).
     - The plan sets a bound on copies after k resumes from P2's count, or has the emitter skip its print on `resume` when P2 shows the transcript still holds the text. Either way, the D4 record compares the added bytes per resume with the bytes saved per spawn.
     - D4 routes every eval consumer through the launcher outcome seam (item 9) rather than editing each launch site.
     - D4 retires the harness, as item 9's Lifetime bullet says.
     - The follow-up opens a tracking issue for the `prompt-gate` class gap and cites it in the D4 record, as GH-1091 is cited for its gap.
     - The plan records the engineer's choice after this PR's projection in kind (the margin, the observables, whether the stop was raised) and the gate run's ceiling as a setting.
   - **Follow-up D1 — move the prose** (`code-writer`):
     - `claude/.claude/CLAUDE.md` — delete the `# Main session` group; add the opening-line clause and the Shipping verify bullet.
     - `claude/.claude/hooks/main-session/conduct.md` and `dispatch.md` — new.
     - `claude/.claude/hooks/tests/test_global_claude_md_groups.py`:
       - pin `CLAUDE.md`'s H1s to `# Agent Core` alone, and each new file's H1, audience line and H2 list;
       - turn each `main` placement into "absent from `CLAUDE.md`, on exactly one line of its named file";
       - give the Shipping verify bullet a `core` placement;
       - keep the core pins and the opening-line fragments;
       - add the per-file and combined byte pins.
     - `claude/.claude/agents/review-orchestrator.md` — item 2's pointer.
     - `claude-skills/skills/branch-management/SKILL.md` (line 141) and `claude-skills/skills/review-pr/SKILL.md` (line 78) — the citation target.
     - `claude/.claude/scripts/select-tests.py` and `claude/.claude/scripts/tests/test_select_tests.py` — a `hooks/main-session/*.md` row selecting `HOOKS_TESTS_DIR` and `SKILLS_TESTS_DIR`, as `select-tests.py:902` does for `CLAUDE.md`, with its expectation.
   - **Follow-up D2 — deliver and gate** (`code-writer`, committed with D1):
     - `claude/.claude/hooks/emit-main-session-rules.sh`, `refuse-prompt-without-main-session-rules.sh` and `require-main-session-rules.sh` — new, at mode 100755.
     - `claude/.claude/hooks/_lib.sh` — the marker, sidecar and sweep helpers, and `_lib_payload_is_untranslated_cursor`.
     - `claude/.claude/settings.json` — four emitter registrations, one `UserPromptSubmit` group, one handler in the `Agent|Task` group.
     - `claude/.claude/hooks/tests/test_lib_main_session_rules.py` — new, sourcing the `_lib.sh` helpers directly.
     - `claude/.claude/hooks/tests/test_emit_main_session_rules.py`, `test_refuse_prompt_without_main_session_rules.py` and `test_require_main_session_rules.py` — new, one case per branch in items 2 to 4, each under a temporary `CLAUDE_CONFIG_DIR`. Each pins its hook's registrations in the tracked `settings.json`: event, matcher, command, `timeout` and `onFailure`. Beyond items 2 to 4's branches, they cover:
       - emitter:
         - an empty or malformed payload;
         - a missing, empty or path-escaping id;
         - a failing marker write, and a failing print after it;
         - 10,000 against 10,001 bytes, with multibyte text and trailing newlines;
         - a `wc` stub with padded output;
         - each `source`;
         - a `compact` failure removing a startup marker and leaving a sidecar;
         - 29-day and 31-day files, swept on `startup` only;
       - the Cursor predicate: each key alone, both, neither, and each with a null or empty value;
       - prompt gate: each cause's reason, the resolved `--config-dir` in the command, the touch on allow, the transcript tail check if P11 calls for it, and an induced `set -u` abort exiting 2;
       - dispatch gate: one of the two orchestrator markers missing; a marker for another session or agent; both fork payload shapes; a `claude --agent` payload;
       - all three: two sessions in one config dir, and a symlinked or trailing-slash `CLAUDE_CONFIG_DIR`.
     - `claude/.claude/hooks/tests/test_main_session_rules_round_trip.py` — new: an emitter `SessionStart` run lets the prompt gate pass, and an emitter `SubagentStart` run lets the dispatch gate pass the same `agent_id`.
     - `claude/.claude/hooks/tests/test_hook_alignment.py`:
       - `prompt-gate` in the class set (`:1884`) and its docstring;
       - `_GATE_HOOK_COUNT` from 35 to 36 and `_SETTINGS_GATE_HANDLER_COUNT` from 36 to 37 (row 123);
       - a `prompt-gate` arm on each failure-injection test (`:2990-3460`), asserting exit exactly 2 (row 124);
       - a test that every script a `settings.json` command names, and `scripts/check-main-session-rules.sh`, has mode 100755 in `git ls-files -s`.
     - `docs/hooks.md` — a bullet per hook, the tier-table row and its count sentence, line 275's pointer, and a "Gate deadlock recovery" entry for the prompt gate. The entry gives item 3's steps and states the legacy-layout consequence: until `./install.sh`, the emitter and prompt gate cannot start and do not block, and only the dispatch gate denies.
     - `README.md` — "Hook transitions" rows for the two gates.
   - **Follow-up D3 — install check** (`code-writer`, after the gate run passes, parallel with D4):
     - `claude/.claude/scripts/check-main-session-rules.sh` — new, at mode 100755.
     - `claude/.claude/scripts/tests/test_check_main_session_rules.py` — new, covering each error and its remedy, the clean-pass line, and the registration check run against the tracked `settings.json`.
     - `install.sh` — the `main-session-rules-check` block and the final exit-1 block.
     - `claude/.claude/hooks/tests/test_install_sh_main_session_rules_check.py` — new, extracting both blocks with a stubbed checker, for both config dirs and the exit-last order.
     - `docs/scripts.md` — a bullet for the checker.
   - **Follow-up D4 — records, pointers and eval consumers** (`code-writer`, after the gate run passes, parallel with D3):
     - `docs/design-decisions/main-session-rules-delivered-by-hooks.md` — new, per item 8. Its rollback section says:
       - revert the whole squash commit;
       - then restart every running session on every account, because a session's hook snapshot still names the deleted dispatch gate, whose `onFailure: "block"` denies every `Agent` and `Task` call until restart;
       - the revert restores the Main session text, so if Agent Core has grown past `CLAUDE.md`'s byte cap by then, the revert PR trims it;
       - leftover `.main-session-rules.d/` files are inert.
     - `docs/design-decisions/declined-sessionstart-additionalcontext-injection.md` and `docs/design-decisions/global-claude-md-agent-core-and-main-session-groups.md` — a supersession line under each provenance line.
     - `claude-skills/skills/plan-it/SKILL.md` (line 108), `claude-skills/skills/subagent-delegation/SKILL.md` (line 124) and `claude-skills/skills/agent-review/SKILL.md` (line 173) — pointers.
     - `claude/.claude/hooks/deny-no-op-dispatch.sh` — line 55's comment and line 128's reason name "Main session § Agent Briefing"; `test_deny_no_op_dispatch.py:249` still passes.
     - `claude/.claude/hooks/tests/test_deny_no_op_dispatch.py` (lines 185, 197), `claude/.claude/hooks/tests/test_agent_roster.py` (lines 141, 146, 151, 689), `claude/.claude/hooks/tests/test_lib_worktree_collision_guard.py` (line 585), `claude/.claude/scripts/transcript_analysis/scope.py` (line 926) and `evals/measure_subagent_model_resolution.py` (line 185) — comments and docstrings.
     - `README.md` (lines 276, 328, 479) and `docs/worktree-bash-guard.md` (line 74) — pointers. README § "Updating after git pull" also gains this change's `./install.sh` step and states the restart as normal practice (item 11).
     - The eval consumers, each with its test, abort the run on a blocked prompt, read through the launcher outcome seam, rather than score it as a miss (rows 153 to 155):
       - `evals/measure_subagent_model_resolution.py` and `evals/measure_explore_recall.py` (its launch at `:698`);
       - `evals/review_bench/runner.py`, with `evals/review_bench/adjudicate.py` covered through the seam's default launcher;
       - `evals/run_skill_evals.py`, through one helper that its seven launch sites call.
     - The harness's retirement, after the pass: delete `evals/measure_main_session_rule_adherence.py`, `evals/test_measure_main_session_rule_adherence.py`, `evals/test_main_session_rule_adherence_fixtures.py` and `evals/fixtures/main-session-rule-adherence/`, and their rows in `claude/.claude/scripts/select-tests.py` and `claude/.claude/scripts/tests/test_select_tests.py`. In branch B, `msmr`'s `stop_when` goes too when nothing else calls it.
     - `evals/README.md` — notes that baselines before the move ran with Main session in the memory block, and removes the harness's section.
     - `CHANGELOG.md` — an `[Unreleased]` entry: run `./install.sh` after pulling; restart running sessions after pulling, as usual for this toolkit, and after any revert; `disableAllHooks` now also drops Main session; and the one-time harness is retired.
   - The follow-up projects about 2,400 changed lines.
11. **Silent paths and their signals** (rows 3, 61, 69).
   - Hooks run but delivery failed: the prompt gate blocks the prompt, and the dispatch gate denies the dispatch.
   - Hooks do not run, so no hook can notice. No prose goes into Agent Core for this cohort, because `CLAUDE.md` holds only what applies to every session (row 131). `./install.sh`, the one installer step (rows 59, 135), is where it fails loudly:
     - the checker fails on `disableAllHooks` in the config dir's `settings.json` or `settings.local.json` (item 6);
     - a `settings.json` that is a real file stops `install.sh` at its stow step, and the checker names it (item 6);
     - on the legacy layout, `install.sh` links the new files, and until it runs, the dispatch gate's `onFailure: "block"` still denies dispatches (row 93).
   - `disableAllHooks` in project or managed settings is outside what the checker reads, and it is a deliberate opt-out of every hook (Out of scope).
   - A session left running across the pull keeps its startup hook snapshot (row 18). P5 decides which case holds:
     - no `CLAUDE.md` re-read and no hook reload: the session keeps its old Main session until restart;
     - hook reload: the session blocks at its next prompt, and the reason's first step is the restart;
     - a re-read without hook reload: the session loses Main session at its next `/clear` or `/compact`, and nothing signals it. That case stays the engineer's decision after P5, weighed against row 61: whether the follow-up becomes a hooks-first PR (Alternatives, row 62).
   - Restarting running sessions after a pull is normal practice for this toolkit (row 134), and no script can do it (row 60). README § "Updating after git pull" and the CHANGELOG state it as that practice, not as a second installer step (row 135).
   - For a consumer, `./install.sh` after the pull is the one installer step, and it runs the checker itself (row 59).

### Alternatives set aside

- **One trimmed file.** The moved text is 11,328 bytes (row 166). One file under the vendor's 10,000-character cap needs about 12% cut, and under the 9,000-byte pin about 22%. The cut would fall on rules the gate cannot show regression-free, which the engineer's no-regressions requirement rules out (row 4).
- **A `cursor_version`-only stand-down.** It would define a second, narrower Cursor predicate beside the one the adapter plans over both keys (row 156).
- **Having the adapter's `generate` skip the six registrations.** It contradicts the adapter's principle of generating every registration (row 158), so the handle is its own `parity-exceptions.json` (item 7).
- **Charging an early-stopped sample the per-sample cap, as the explore-recall precedent does for a sample with no `result` event.** Every sample here is early-stopped, so the stop would trip within the first few dozen samples and the projection would read only the cap (rows 143, 144).
- **A mean-cost fallback for early-stopped samples.** It would charge an early-stopped O2 or O3 sample a run-to-completion mean that includes a `plan-architect` consult or a `code-review` run, phantom spend that trips the stop. P9 instead selects branch A or B, and a P9 where neither holds returns to the engineer.
- **Generalizing the harness for other sessions' harnesses now.** No second consumer exists, so an extracted interface would be shaped by one use. The D4 record names the statistics as the extraction candidate instead (rows 171, 178).
- **A process pool for workers.** A `claude -p` child leads its own session, so a pool without the abort wiring leaves live, billing children after an interrupt; threads with `msmr.abort_launches()` are the precedent that ends them (rows 148, 149).
- **A new top-level `claude/.claude/main-session/` directory.** A new top-level entry needs `./install.sh` (`README.md:97`), so every consumer who only pulls is locked out until they run it. Under `hooks/`, nothing new needs linking.
- **Lighter delivery, loud-failure and measurement primitives** are weighed in M2, M3 and M9.
- **A `PreCompact` hook that deletes the marker before compaction.** Whether a subagent's own compaction fires `PreCompact` with the parent's `session_id` is unresolved (`docs/precompact-hook-behavior.md:53-68`), so it risks locking out a healthy session. The emitter's remove-on-failure covers a failed re-emit without it.
- **Enforcing the gates in Cursor now.** Cursor's event order, Restricted Mode and resume behavior are unprobed (row 24), and a wrong guess blocks Cursor prompts, which the engineer ruled out (row 2).
- **`omitClaudeMd` plus `skills:` on every agent**, GH-1085's path. It also drops the project `CLAUDE.md` that most reviewer agents need (G1).
- **A config-key kill switch for the prompt gate.** The stow-gate recovery in `docs/hooks.md:468` already applies, and a key adds a `config-keys.psv` row with its schema obligations.
- **Patching a real-file `settings.json` from `install.sh`.** It would give the six registrations a second home, while every other hook's registration in that file stays stale. Failing loudly sends the consumer back to the one source.
- **Standing the hooks down on `CLAUDE_CONFIG_ADAPTER_SESSION_ID` under the shim.** The adapter sets that variable only from its Phase 4 (`.claude/plans/cursor-adapter.md:742`). Keying these hooks to an unbuilt adapter's variable takes a decision that belongs to the adapter (G4).
- **A hooks-first follow-up that leaves the text in `CLAUDE.md` until a later PR cuts it.** It would close the running-session window outright, at the cost of a third PR and a stretch of double loading in main sessions. It stays the fallback if P5 shows a re-read without hook reload.
- **An Agent Core bullet telling a main session that lacks Main session to say so.** It would reach sessions whose hooks never run, but it puts prose for a setup edge case into every session's `CLAUDE.md`, which the engineer rejected (row 131).
- **Moving Shipping's verify sub-bullet with the rest.** It defends irreversible push and PR actions against injected claims, so it stays in the memory block's higher-authority frame (row 64).
- **Reusing calibration samples in the scored comparison.** Choosing observables on high `memory` counts would bias the comparison against the hook.
- **A tighter margin.** The engineer chose 0.15 (row 53). Under item 9's sizing, a margin of 0.10 multiplies every scored target by 2.25, and 0.05 multiplies it by 9.

### Assumption ledger

**Root problem:** Every subagent spawn loads 11,615 bytes of Main session rules it must not follow. Moving them out of `CLAUDE.md` must not let a main session, fork or `review-orchestrator` run without them unnoticed, must not lower measured adherence beyond a margin before merge, and must not change Cursor.

**Givens:**
- **G1 — Claude Code loads `~/.claude/CLAUDE.md` into every subagent except `Explore` and `Plan`, and its one per-agent opt-out, `omitClaudeMd`, also drops the project `CLAUDE.md`.** The vendor owns loading (Context; `docs/design-decisions/declined-sessionstart-additionalcontext-injection.md:5, 29`).
- **G2 — A hook's `additionalContext` over 10,000 characters becomes a path plus a 2,000-character preview, and a `SessionStart` hook cannot block startup.** The vendor owns both (Context).
- **G3 — Cursor reads no `~/.claude/CLAUDE.md`, and it decides its own hook events, imports and payloads.** The vendor owns both (Context).
- **G4 — Every `~/.cursor` write belongs to `cursor-adapter`, which is unbuilt.** Another plan owns it (`.claude/plans/cursor-adapter.md`).
- **G5 — Model behavior is sampled, so adherence is a rate that a finite measurement bounds only to a margin.** The vendor owns the model.
- **G6 — Claude Code decides when a running session re-reads memory files and reloads hooks.** The vendor owns both, and P5 observes them.

**Mechanisms:**
- **M1 — The content move.**
  - Two files under `hooks/main-session/`, moved byte for byte except Shipping's verify sub-bullet, each with an audience line.
  - Byte pins per file and for both together.
  - Agent Core's opening clause and verify bullet.
  - No other Agent Core prose.
  `anchors: root, row1, row4, row10, row11, row27, row34, row64, row70, row71, row91, row92, row131, row142, row166`.
- **M2 — One emitter with four registrations that writes a marker only after a successful emit, and a cause sidecar on failure.** It is heavier than a static load. Lighter primitives, and why each fails:
  - an `@`-import from `CLAUDE.md` loads into every subagent too (G1);
  - an output style takes the user's one style slot, fires no hook that could vouch for it, and its reach into subagents is unverified;
  - `--append-system-prompt` needs every launch path to pass a flag, and it drops silently when one does not (row 3);
  - delivery on the first `UserPromptSubmit` cannot re-deliver after a mid-turn compaction, and Cursor's interactive CLI never fires an imported `UserPromptSubmit` (row 84);
  - extending an already-registered `SessionStart` hook such as `capture-session-id.sh` would reach running sessions, but it gives an unrelated hook a second job.
  `anchors: root, row11, row12, row13, row14, row15, row16, row19, row28, row35, row36, row40, row42, row73, row74, row75, row102, row126, row167, row168, row169`.
- **M3 — A `UserPromptSubmit` prompt gate in a new `prompt-gate` class, printing a distinct cause and the resolved checker command.** It is heavier than reusing `gate` or a tool-call gate. Lighter primitives, and why each fails:
  - the `gate` class fails its registration and deny-envelope pins (rows 29, 30);
  - a `PreToolUse`-only gate misses a turn with no tool call, and it tells the model rather than the user (row 17);
  - a matcher-less `PreToolUse` gate pays a hook run on every tool call;
  - a body-level self-check in `review-orchestrator` stays silent when the model misses an absence (row 3).
  `anchors: row3, row12, row17, row29, row30, row69, row72, row94, row95, row101, row124, row127, row130`.
- **M4 — A `PreToolUse` `Agent|Task` gate on session markers for the main thread and per-agent markers for `review-orchestrator`.** `anchors: row3, row20, row21, row34, row39, row77, row78, row79, row80, row98, row99, row123`.
- **M5 — All three hooks call one `_lib.sh` Cursor predicate over `workspace_roots` and `cursor_version`, and the contract names six `parity-exceptions.json` entries as the adapter's handle.** `anchors: row2, row7, row8, row22, row23, row24, row25, row26, row44, row81, row82, row83, row84, row85, row86, row87, row88, row89, row90, row156, row157, row158, row190`.
- **M6 — A checker that `install.sh` runs for each config dir, failing loudly on a non-link `settings.json`, with the registrations shipped in the stowed `settings.json`.** `anchors: row3, row5, row6, row37, row57, row58, row59, row60, row61, row62, row93, row100, row116, row117, row118, row131, row134, row135, row141`.
- **M7 — The groups-test rewrite, the citation retargets, the design record and the two supersession lines.** `anchors: row9, row32, row33, row38, row43, row45, row97, row103, row104, row128`.
- **M8 — Two PRs, the harness and its calibration first and then the move, with the follow-up's dispatch order.** `anchors: row18, row31, row32, row33, row45, row46, row65, row66, row67, row68, row76, row96, row136, row137, row140, row173, row174`.
- **M9 — A one-time pre-merge adherence gate in a new `evals/` instrument.**
  - A calibration against `none`, then a sized two-look Newcombe non-inferiority comparison.
  - A sample end and its cost sources, from the branch P9 selects.
  - A locked, resumable run log, and drift halts.
  - Thread workers that end their children on interrupt.
  - Retirement in the follow-up's D4.
  It is heavier than a spot-check. Lighter shapes, and why each fails:
  - a post-merge spot-check measures nothing before merge (row 45);
  - `run_skill_evals.py`'s `behavioral-dispatch` method detects that a dispatch fired, never its input, and has no arms (`evals/run_skill_evals.py:541-571`);
  - `evals/review_bench/` compares reviewer-agent files on defect recall with a judge (`evals/review_bench/arms.py:1-15`), not session context;
  - a hand-run shell loop over `claude -p` leaves the scoring that gates a merge untested;
  - two arms without `none` would pass a rule the model follows with or without its text;
  - a scratch `CLAUDE_CONFIG_DIR` per arm drops the stowed skills and agents the observables call, and filling it means copying account state, credentials included, that the Safety rules keep out of reach;
  - `--setting-sources` changes which settings files load, which may reach past the surface under test (`[unverified]`), and it is the first fallback a failed P9 weighs;
  - a judge-scored rule, such as path-over-paste in a dispatch prompt, adds a judge call and its noise to every sample;
  - a fixed schedule of 30 to 70 samples per arm rarely passes a matching hook: at adherence 0.80, one look at 70 passes one observable about 60% of the time, and four together about 13% (row 115).
  `anchors: root, row38, row45, row46, row47, row48, row49, row50, row51, row52, row53, row54, row55, row56, row63, row105, row106, row107, row108, row109, row110, row111, row112, row113, row114, row115, row119, row120, row121, row122, row125, row132, row133, row138, row139, row143, row144, row145, row146, row147, row148, row149, row150, row151, row152, row153, row154, row155, row159, row160, row161, row162, row163, row164, row165, row170, row171, row172, row178, row175, row176, row177, row179, row180, row181, row182, row183, row184, row185, row186, row187, row188, row189, row191, row192`.

**Material assumptions:**

| # | Assumption | Tag |
|---|---|---|
| 1 | The Main session rules load into every main session and into `review-orchestrator` | `[engineer-verified: "another file that gets auto-loaded into every main session and review orchestrator agent"]` |
| 2 | Cursor must not get worse | `[engineer-verified: "it really, really needs to work with cursor. It can't degrade there."]` |
| 3 | A missing or broken setup fails loudly | `[engineer-verified: "If a requirement is missing from setup or setup isn't don't properly the toolkit should fail very loudly."]` |
| 4 | A completed setup regresses nothing | `[engineer-verified: "The feature set should not be broken and there should be no regressions if setup is successful."]` |
| 5 | The engineer hopes `install.sh` scripts the registrations | `[engineer-verified: "the registrations I hope can be scripted with install.sh"]` |
| 6 | Installation needs no manual step beyond `./install.sh` | `[engineer-verified: "the installation should be programmatic and shouldn't require manual steps, or only one manual step, which is install.sh"]` |
| 7 | This plan's Cursor scope | `[engineer-verified: "Claude Code side + Cursor-ready contract (Recommended)"]` |
| 8 | Row 7's option description, which is not the engineer's wording: the change ships the split, the Claude Code delivery and gate, and a documented contract, while `cursor-adapter` writes `~/.cursor/hooks.json` | `[unverified]` |
| 9 | The motivation, which answers the decline's smaller-slice reason | `[engineer-verified: "because of cost associated with the extra unneeded context which compounds with every review round and subagent spawned and secondly because of degraded context"]` |
| 10 | Main session is lines 120-180 of `CLAUDE.md`, 11,615 bytes | `[verified: claude/.claude/CLAUDE.md:120-180 read; the byte count is a reviewer's wc -c, not recounted here]` |
| 11 | The `additionalContext` cap is 10,000 characters | `[verified: plan Context, Claude Code 2.1.318 probe]` |
| 12 | No `SessionStart` exit code or `onFailure` blocks startup | `[verified: plan Context probes]` |
| 13 | Plain and custom subagents receive no `SessionStart` context | `[verified: plan Context probes]` |
| 14 | `SessionStart` fires after `/compact` | `[verified: plan Context probe]` |
| 15 | Every `SessionStart` hook finishes before the first `UserPromptSubmit` hook runs, interactive and `claude -p` | `[unverified]`, probe P1 |
| 16 | Each `SessionStart` carries the same `session_id` as the `UserPromptSubmit` and `PreToolUse` calls after it, for every source and launch form P2 lists | `[unverified]`, probe P2 |
| 17 | An interactive session shows the user a `UserPromptSubmit` hook's exit-2 stderr | `[unverified]`, probe P3 |
| 18 | Claude Code snapshots hooks at startup, so a mid-session pull does not arm the prompt gate in a running session | `[unverified]`, probe P5 |
| 19 | `SubagentStart` `additionalContext` reaches the matched subagent | `[verified: settings.json:221-230; announce-branch-review-history.sh:31-41; this dispatch's own context carried that hook's line]` |
| 20 | `agent_id` appears only on calls inside a subagent | `[verified: enforce-marker-script-shape.sh:83-88, quoting the vendor hooks reference]` |
| 21 | A fork's payload shape, with or without `agent_id` | `[unverified]`, probe P2 |
| 22 | Cursor's imported `SessionStart` payload carries `cursor_version` and leaves `CLAUDE_CODE_ENTRYPOINT` unset | `[verified: plan Context]` |
| 23 | Imported `SessionStart` context reaches the Cursor IDE main agent, and the CLI main agent and its subagents | `[verified: plan Context]` |
| 24 | Cursor `agent -p` fires neither imported `SessionStart` context nor `beforeSubmitPrompt` | `[verified: .claude/plans/cursor-adapter.md:89, 167]` |
| 25 | The adapter's `generate` emits one entry per tracked `settings.json` registration | `[verified: .claude/plans/cursor-adapter.md:740-755]` |
| 26 | Cursor's `additionalContext` size cap | `[unverified]` |
| 27 | Hook commands run from `~/.claude/hooks/` | `[verified: settings.json hook commands]` |
| 28 | An environment-assignment prefix is part of this repo's hook command grammar | `[verified: test_hook_alignment.py:3513-3534; plugins/skill-management/hooks/hooks.json:9]` |
| 29 | A gate handler must sit under `PreToolUse` | `[verified: test_hook_alignment.py:3574-3581]` |
| 30 | A gate must answer malformed input with the `PreToolUse` deny envelope on exit 0 | `[verified: test_hook_alignment.py:2967-2977; _lib.sh:419-444]` |
| 31 | `CLAUDE.md` is 30,640 of 30,720 bytes | `[verified: an exploration report citing check-claude-md-length.sh:59, and a reviewer's recount]` |
| 32 | The groups test pins the H1 list, each placement's side of `# Main session`, and the opening-line fragments | `[verified: test_global_claude_md_groups.py:151-305]` |
| 33 | `test_skills.py` resolves `` `target` § "Heading" `` citations in the skill tree only | `[verified: claude-skills/skills/tests/test_skills.py:3749-3763]` |
| 34 | `review-orchestrator` claims Agent Briefing and Model & Effort Routing | `[verified: claude/.claude/agents/review-orchestrator.md:11-12]` |
| 35 | Every main session, `--agent` ones included, loads the whole Main session through `CLAUDE.md` today | `[verified: G1; CLAUDE.md:120-180]` |
| 36 | The eval and measurement scripts run `claude -p` under the ambient config dir with no hook-disabling flag | `[verified: Grep of evals/ for --bare, --settings, --setting-sources found none; evals/review_bench/runner.py:164, 1004-1010]` |
| 37 | `disableAllHooks` turns the emitter and both gates off together | `[unverified]` |
| 38 | `additionalContext` adherence against the memory block is unmeasured today | `[verified: declined-sessionstart-additionalcontext-injection.md:15]` |
| 39 | A hand-written marker is a shape a cooperative agent does not emit | `[verified: docs/hooks.md:13; repo CLAUDE.md "Hook threat model"]` |
| 40 | The 10 s `timeout` follows this repo's informational session-start hooks | `[verified: settings.json:184-191, 222-229]` |
| 42 | Every `SessionStart` source writes the current session's markers | `[unverified]`, probe P2 |
| 43 | Only `transcript-narrative` and `error-mode-analysis` use `context: fork` | `[verified: Grep of SKILL.md frontmatter]` |
| 44 | Cursor stays exactly as it is, with the new hooks standing down on a Cursor payload, rather than receiving the rules now | `[engineer-verified: "Wait for adapter (Recommended)"]` |
| 45 | Adherence on the hook surface is measured before merge, not accepted unmeasured with a post-merge spot-check | `[engineer-verified: "I'd say measure before merge, that's a very important caveat."]` |
| 46 | The measurement gates the move's merge, so any verdict but a pass stops it | `[unverified]`: the plan's reading of row 45 |
| 47 | The memory block carries "IMPORTANT: These instructions OVERRIDE any default behavior and you MUST follow them exactly as written" | `[verified: this dispatch's own context]` |
| 48 | A project `CLAUDE.md` loads into the same memory block as the user one | `[verified: this dispatch's own context carries both under one "Codebase and user instructions" block]` |
| 49 | `claudeMdExcludes` exists and matches absolute paths | `[verified: declined-sessionstart-additionalcontext-injection.md:29]` |
| 50 | `code-writer`'s description states the delegate-implementation rule | `[verified: claude/.claude/agents/code-writer.md:3]` |
| 51 | Stream-json gives each `tool_use` name at `content_block_start` and its input as `input_json_delta` fragments, so a scorer reads a call's input before the call runs | `[verified: evals/run_skill_evals.py:407-467]` |
| 52 | Newcombe's hybrid score interval (his method 10) for a difference of two independent proportions combines the two Wilson intervals | `[unverified]`: Newcombe, Statistics in Medicine 17 (1998) 873-890, not opened; DM1's interval test takes its expected values from the paper's worked examples |
| 53 | The pre-merge adherence gate's margin is 0.15 | `[engineer-verified: "0.15 (Recommended)"]` |
| 54 | Figures computed over synthetic fixtures may be published | `[verified: repo CLAUDE.md "Redact private-project-identifying content": "Content derived only from this repo's own history, from public sources, or from synthetic fixtures is not in this class."]` |
| 55 | Until merge, the ambient settings register none of this branch's hooks | `[unverified]`; the preflight checks it on every run |
| 56 | Item 9's sizing rule, two looks, extension, calibration size, entry rule, 1.5-times attempt cap, fault retry, reach gap, one-arm-short outcomes and self-check rule are this plan's proposal, not the engineer's | `[unverified]` |
| 57 | Registrations already in the tracked `settings.json` count as programmatic installation | `[engineer-verified: "if they're already part of settings.json, that counts"]` |
| 58 | Row 5 is met by shipping the registrations in the stowed `settings.json` and having `install.sh` verify them | `[engineer-verified: "Yes, ship plus verify (Recommended)"]` |
| 59 | `./install.sh` after the pull is a consumer's one manual step, and it runs the checker itself, so no separate checker step is added | `[unverified]`: the plan's reading of rows 6 and 58 |
| 60 | No script can restart a running Claude Code session, so the restart stays a stated limitation | `[unverified]` |
| 61 | `disableAllHooks` and the running-session window each get a loud signal rather than staying silent | `[engineer-verified: "Close both loudly (Recommended)"]` |
| 62 | Row 61 is met by Design item 11's signals, with project- and managed-level `disableAllHooks` left out as a deliberate opt-out | `[unverified]`: the plan's reading of row 61 |
| 63 | The adherence measurement runs on the model `settings.json` configures | `[engineer-verified: "Configured Sonnet default (Recommended)"]` |
| 64 | Shipping's verify-don't-trust sub-bullet stays in Agent Core instead of moving | `[engineer-verified: "Keep it in Agent Core (Recommended)"]` |
| 65 | The harness ships first in its own PR, and the move follows in another | `[engineer-verified: "Harness first, own PR (Recommended)"]` |
| 66 | The deliverables and Critical files plan review listed as beyond the Ask stay in the plan | `[engineer-verified: "Keep all of them (Recommended)"]` |
| 67 | The plan proceeds past the 400-line diff-size threshold at the projection it showed then | `[engineer-verified: "Proceed anyway"]` |
| 68 | The plan proceeds past the 400-line threshold at this PR's Projected diff of about 3,400 changed lines, asked with that figure after the restatement | `[engineer-verified: "Proceed anyway"]` |
| 69 | Missing prose degrades into hard-to-find bugs | `[engineer-verified: "degradation from missing prose causes hard to discover bugs"]` |
| 70 | Agent Briefing is 3,830 bytes and Model & Effort Routing 4,375 | `[verified: an exploration report's counts, not recounted]` |
| 71 | UTF-8 spends one to four bytes per character, so text of 10,000 bytes or fewer holds 10,000 characters or fewer | `[verified: RFC 3629 §3]` |
| 72 | A `UserPromptSubmit` exit 2 blocks the prompt | `[verified: plan Context probes]` |
| 73 | A true fork (`subagent_type: "fork"`) inherits `SessionStart` context | `[verified: plan Context probes]` |
| 74 | `SessionStart` fires after an automatic compaction | `[unverified]`, probe P4 |
| 75 | A resumed session gets a new `session_id` | `[verified: docs/hooks.md:400]` |
| 76 | A running session does not re-read `CLAUDE.md` at `/clear` or `/compact` | `[unverified]`, probe P5 |
| 77 | `agent_type` without `agent_id` marks a `claude --agent` main session | `[verified: enforce-marker-script-shape.sh:87-89, quoting the vendor hooks reference]` |
| 78 | One subagent's `agent_id` is the same on its `SubagentStart` and its `PreToolUse` calls | `[unverified]`, probe P2 |
| 79 | An `agent_id` passes `_lib_valid_session_id_component` | `[verified: test_enforce_marker_script_shape.py:4030; .claude/plans/code-writer-self-review-enforcement.md:48]` |
| 80 | A subagent's calls carry the parent's `session_id` | `[verified: docs/hooks.md:488]` |
| 81 | Cursor's imported `UserPromptSubmit` and `PreToolUse` payloads carry `cursor_version` or `workspace_roots` | `[unverified]`, probe P7a |
| 82 | Every native Cursor hook record carries `cursor_version` and `workspace_roots`, and no Claude Code record carries either | `[verified: .claude/plans/cursor-adapter.md:2682]` |
| 83 | An imported `UserPromptSubmit` blocks in the Cursor IDE with imports on | `[verified: plan Context]` |
| 84 | An imported `UserPromptSubmit` never fires in Cursor's interactive CLI | `[verified: plan Context]` |
| 85 | With the Third-Party Imports toggle off, the Cursor IDE fires no imported hook | `[verified: plan Context]` |
| 86 | The Cursor IDE delivered `sessionStart` context in 2 of 3 attempts | `[verified: .claude/plans/cursor-adapter.md:89, 167]` |
| 87 | The adapter maps `UserPromptSubmit` to `beforeSubmitPrompt`, with exit 2 as `continue: false` | `[verified: .claude/plans/cursor-adapter.md:836-842]` |
| 88 | The adapter routes `CLAUDE_CONFIG_DIR` | `[verified: .claude/plans/cursor-adapter.md:740-755]` |
| 89 | On the Cursor CLI, `session_id` equals `conversation_id` | `[verified: .claude/plans/cursor-adapter.md:1071]` |
| 90 | The adapter shim's translated payloads carry no `cursor_version` | `[verified: .claude/plans/cursor-adapter.md:735]` |
| 91 | `${0%/*}` reaches the files beside the running hook | `[verified: require-review-orchestrator-routing.sh:73]` |
| 92 | On the supported layout `hooks/` is a directory link, so a new file under it goes live on `git pull` | `[verified: README.md:95-97]` |
| 93 | On the legacy layout, a pull leaves a new hook file unlinked until `./install.sh` links it | `[verified: docs/hooks.md:469-470]` |
| 94 | A non-gate handler may not carry `onFailure` | `[verified: test_hook_alignment.py:3574-3581]` |
| 95 | The hook-class set is closed at `gate`, `informational`, `turn-gate` and `batch-gate` | `[verified: test_hook_alignment.py:1884]` |
| 96 | The byte ratchet never denies a shrink | `[verified: an exploration report citing check-claude-md-length.sh:59]` |
| 97 | `branch-management/SKILL.md:141` and `review-pr/SKILL.md:78` cite `CLAUDE.md` § "Agent Briefing" | `[verified: Grep]` |
| 98 | `review-orchestrator` forbids shipping | `[verified: claude/.claude/agents/review-orchestrator.md:11]` |
| 99 | `review-orchestrator` reports an unsatisfiable hook denial under `HALT:` | `[verified: claude/.claude/agents/review-orchestrator.md:31-41]` |
| 100 | A `--bare` run turns the emitter and both gates off | `[unverified]` |
| 101 | The harness's default hook timeout is 600 s | `[verified: docs/hooks.md:468]` |
| 102 | No session sits idle for 30 days and then expects its next prompt to pass without a restart | `[unverified]`; the D4 record names it a residual |
| 103 | Both `context: fork` skill bodies forbid invoking a review skill or `marker.sh`, directly or through a dispatch | `[verified: transcript-narrative/SKILL.md:13; error-mode-analysis/SKILL.md:15]` |
| 104 | Neither `context: fork` skill needs another Main session rule | `[unverified]` |
| 105 | `SubagentStart` context arrives under the bare label "SubagentStart hook additional context:" | `[verified: this dispatch's own context]` |
| 106 | The label `SessionStart` context arrives under | `[unverified]`, probe P9 and the self-check |
| 107 | Each memory file carries its own label: the user file "user's private global instructions for all projects", a project file "project instructions, checked into the codebase" | `[verified: this dispatch's own context]` |
| 108 | Listing the user file under `claudeMdExcludes` keeps it out of a `claude -p` session | `[unverified]`, probe P9 |
| 109 | `code-review`'s description states the review-before-commit rule | `[verified: claude-skills/skills/code-review/SKILL.md:3]` |
| 110 | `code-review` fires headlessly without the Main session text | `[unverified]`: P10 and the calibration decide it, against `evals/run_skill_evals.py:190-194`'s note that headless auto-trigger is unreliable |
| 111 | `acceptEdits` is a valid `--permission-mode` value | `[verified: evals/measure_subagent_model_resolution.py:93-96]` |
| 112 | Headless default-mode runs dispatch subagents | `[verified: evals/measure_subagent_model_resolution.py:226-280]` |
| 113 | Every reused helper exists at the cited lines | `[verified: evals/measure_subagent_model_resolution.py:89-96, 629-646, 703-768, 793-828; evals/measure_explore_recall.py:527-590, 651-670, 773; evals/run_skill_evals.py:143-161, 404-461, 642-654]` |
| 114 | `config_dir` comes from `_config_dir.py`, which `run_skill_evals.py` imports rather than defines | `[verified: evals/run_skill_evals.py:55]` |
| 115 | Item 9's sizing table follows n = (z_α + z_β)² · 2p(1−p) / δ², and its operating characteristics hold as stated | `[unverified]`: the table is computed here; the pass, error and entry figures come from a reviewer's exact enumeration and seeded simulation in plan review, re-derived in round 3, which DM1's tests pin |
| 116 | `install.sh` runs under `set -e` and calls `stow` as a bare command | `[verified: install.sh:6, 440]` |
| 117 | `stow` exits non-zero when a target is a real file it does not manage | `[unverified]` |
| 118 | `install.sh` ends with its remaining checks, the ensurepip note, the Claude Code version reprint and the contributor prompt | `[verified: install.sh:1049-1070]` |
| 119 | `session-marker-dashboard.sh`, registered for every `SessionStart` source, tells the model to run `review-ledger.sh render` when the branch has ledger rows | `[verified: claude/.claude/settings.json:137-145; claude/.claude/hooks/session-marker-dashboard.sh:146]` |
| 120 | `claude/.claude/settings.json` sets `"model": "sonnet"` | `[verified: claude/.claude/settings.json:83]` |
| 121 | `DEFAULT_CUMULATIVE_STOP_USD` is 300.0 | `[verified: evals/measure_explore_recall.py:657]` |
| 122 | `select-tests.py` maps the msmr and explore-recall files through `_is_review_bench_change`, which a new sibling module does not match | `[verified: claude/.claude/scripts/select-tests.py:613-640]` |
| 123 | `_GATE_HOOK_COUNT` is 35 and `_SETTINGS_GATE_HANDLER_COUNT` is 36 | `[verified: test_hook_alignment.py:3505, 3508]` |
| 124 | `test_hook_alignment.py`'s failure-injection tests run on `hook-class: gate` hooks only | `[verified: test_hook_alignment.py:163 defines GATE_HOOKS from the gate class; the tests at :2990-3460]` |
| 125 | `evals/fixtures/` already holds recorded stream-json streams | `[verified: evals/fixtures/skill-fired-code-review.jsonl, dispatch-fired-explore.jsonl]` |
| 126 | Where a `SessionStart` hook's stderr surfaces to the user, on exit 0 and on a non-zero exit | `[unverified]`, probe P3b |
| 127 | `/exit`, `/clear` and `/hooks` run while a `UserPromptSubmit` hook blocks every prompt | `[unverified]`, probe P3 |
| 128 | The Context's probes ran on Claude Code 2.1.318 | `[verified: plan Context]` |
| 130 | A `UserPromptSubmit` hook that times out does not block the prompt | `[unverified]` |
| 131 | No prose goes into Agent Core for the cohort whose hooks do not run | `[engineer-verified: "you don't want to put stuff in Claude at MD if it doesn't apply to all sessions"]` |
| 132 | The calibration comes first, and the engineer decides the gate run's course after it | `[engineer-verified: "Calibrate first, then decide (Recommended)"]` |
| 133 | Row 132's option description, which is not the engineer's wording: this PR's calibration projects the real dollar cost, and the engineer sees it before the full gate run starts, then chooses to raise the stop, relax the margin, or cut observables | `[unverified]` |
| 134 | Restarting running sessions after a pull is normal practice for this toolkit | `[engineer-verified: "Restart after a pull is not only acceptable it's normal for this toolkit"]` |
| 135 | The restart is not a second manual step, so `./install.sh` stays the one installer step, and README and the CHANGELOG state the restart as normal practice | `[unverified]`: the plan's reading of row 134 |
| 136 | Fallbacks for a content file the calibration cannot measure are discussed after the results | `[engineer-verified: "Let's talk about fallback options after we see the results"]` |
| 137 | No fallback is chosen now, and the engineer discusses fallbacks once the calibration's table is in | `[unverified]`: the plan's reading of row 136 |
| 138 | The engineer consents to the calibration's spend | `[engineer-verified: "Yes that's fine"]` |
| 139 | The harness shows the projected spend and the ceiling in effect after the smoke and before the first scored sample, then continues with `--cumulative-stop-usd` set to the accepted figure | `[unverified]`: the plan's reading of row 138 |
| 140 | The engineer answered "Yes" to a question that asked to re-confirm proceeding at a larger projected diff, without stating the figure | `[engineer-verified: "Yes"]`: row 68 carries the answer given with the figure |
| 141 | Moving a real-file `settings.json` aside is a repair of a broken setup, not an installation step | `[unverified]` |
| 142 | Row 1's "another file" is met by two files, because one file would cross the 10,000-character cap and trimming about 12% cannot be shown regression-free | `[unverified]`: the plan's reading of row 1 |
| 143 | A killed sample's stream carries no `result` event, and `_extract_total_cost_usd` returns `None` without one | `[verified: evals/measure_subagent_model_resolution.py:813-828, read by two plan reviewers]` |
| 144 | `measure_explore_recall.py` charges the per-sample cap when the extracted cost is `None` | `[verified: evals/measure_explore_recall.py:827-838, read by two plan reviewers]` |
| 145 | No price table exists in `evals/` | `[verified: a plan reviewer's Grep]` |
| 146 | Stream-json carries `usage` on an event at or before the `message_delta` that ends a message | `[unverified]`, probe P9 |
| 147 | The cumulative stop is checked after a sample completes, so N workers can overshoot it by up to N per-sample charges | `[verified: evals/measure_explore_recall.py:652-656]` |
| 148 | `msmr.abort_launches()` ends live launches, and the signal handlers that call it live in `msmr.main` | `[verified: evals/measure_subagent_model_resolution.py:679-693, 994-1002, read by a plan reviewer]` |
| 149 | `review_bench` runs workers as threads and calls `msmr.abort_launches()` on interrupt, and `run_review_bench.py` installs the signal handlers and an owner-only umask | `[verified: evals/review_bench/runner.py:1517-1527; evals/run_review_bench.py:2318-2335, read by a plan reviewer]` |
| 150 | `run_skill_evals.DEFAULT_WORKERS` is 4 | `[verified: a plan reviewer's read of evals/run_skill_evals.py]` |
| 151 | `_run_claude_to_completion` discards stderr and returns only the stream lines and a timeout flag | `[verified: evals/measure_subagent_model_resolution.py:703-763]` |
| 152 | About a dozen explore-recall test fakes take `(cmd, cwd, timeout_s)` and return two values | `[verified: evals/test_measure_explore_recall.py:1222-2195, read by a plan reviewer]` |
| 153 | `measure_explore_recall.py:698` builds its own `claude -p` command and launches it through msmr's launcher | `[verified: a plan reviewer's read of evals/measure_explore_recall.py:698, 825]` |
| 154 | `review_bench/adjudicate.py` uses msmr's launcher by default | `[verified: a plan reviewer's read of evals/review_bench/adjudicate.py:536]` |
| 155 | `run_skill_evals.py` launches `claude -p` at seven sites, each discarding stderr | `[verified: a plan reviewer's read of evals/run_skill_evals.py:391, 518, 624, 633, 677, 895, 938]` |
| 156 | `cursor-adapter` plans one predicate, `_lib_payload_is_untranslated_cursor`, matching any of `workspace_roots` and `cursor_version` | `[verified: .claude/plans/cursor-adapter.md:2797]` |
| 157 | The adapter's `parity-exceptions.json` is its exemption mechanism, read by generation and by its gate-matrix meta-test | `[verified: .claude/plans/cursor-adapter.md:844-848]` |
| 158 | The adapter generates every registration so that no gate's coverage depends on an audit | `[verified: .claude/plans/cursor-adapter.md:836]` |
| 159 | After 30 samples, the Wilson point nearest 0.5 is at most 0.886 | `[verified: the Wilson lower bound for 30 of 30 at z = 1.96, computed here and matching a plan reviewer's enumeration]` |
| 160 | Each ambient `SessionStart` run appends to `.session-config-keys-log.jsonl`, which nothing trims, and `capture-session-id.sh` writes `sessions/<pid>` | `[verified: docs/session-config-keys-log.md:123; claude/.claude/hooks/capture-session-id.sh:90-96, read by a plan reviewer]` |
| 161 | Under subscription auth, `total_cost_usd` is nominal, and the real limit is the account's usage window | `[verified: evals/README.md:10-18, read by a plan reviewer]` |
| 162 | Recorded stream events carry a `parent_tool_use_id` field | `[verified: a plan reviewer's count of 60 occurrences, all null, across evals/fixtures/*.jsonl]` |
| 163 | Whether a subagent's own events reach the parent's stream | `[unverified]`, probe P10 |
| 164 | The stream's init event carries the CLI version, and each assistant message carries `message.model` | `[unverified]`, probe P9 |
| 165 | `dispatch-fired-explore.jsonl` holds no `input_json_delta`, so it cannot validate O1 or O2 | `[verified: a plan reviewer's read of evals/fixtures/dispatch-fired-explore.jsonl]` |
| 166 | The moved text is 11,328 bytes, the 287-byte verify sub-bullet aside | `[verified: a plan reviewer's recount]` |
| 167 | A transcript hook attachment with a timestamp marks that a hook ran, and whether `claude -p` keeps one is untested | `[verified: docs/session-config-keys-log.md:61-85]` |
| 168 | Whether the `additionalContext` cap applies per hook or per event | `[unverified]`, probe P11 |
| 169 | Whether the transcript holds the injected text by the first `UserPromptSubmit` | `[unverified]`, probe P11 |
| 170 | The sibling instrument has a CHANGELOG entry | `[verified: CHANGELOG.md:627, read by a plan reviewer]` |
| 171 | The harness is retired after use rather than kept for reuse | `[engineer-verified: "I think retire it unless you think it could be generalized."]` |
| 172 | Retiring means D4 deletes the module, tests, fixtures and `select-tests.py` rows after the pass, and the PR body keeps the table | `[unverified]`: the plan's reading of row 171 |
| 173 | Every Phase 0 probe gates this PR | `[engineer-verified: "All probes gate it"]` |
| 174 | Every Phase 0 probe finishes before this PR merges, so the suggestion to make only P9 and P10 merge preconditions is not adopted | `[unverified]`: the plan's reading of row 173 |
| 175 | The calibration stops at its first void | `[engineer-verified: "Stop at the first void (Recommended)"]` |
| 176 | The session reports the table at the first void, no automatic replacement round runs, and the engineer decides whether to replace an observable | `[unverified]`: the plan's reading of row 175 |
| 177 | The calibration's ceiling is `--cumulative-stop-usd 300`, in nominal dollars, the default, because row 138's answer names no figure, unless the engineer names another before launch | `[unverified]` |
| 178 | Generalizing waits for a second consumer, so the D4 record names the sizing and verdict statistics as the extraction candidate, nothing is extracted now, and the arms, observables and hook rewrite stay specific to this rule set | `[unverified]`: the plan's answer to row 171's "unless you think it could be generalized" |
| 179 | In a recorded stream, input and cache tokens sit on `message_start`, the final output count on `message_delta`, and the `assistant` events for one message id each repeat the start-time `usage` | `[verified: a plan reviewer's replay of evals/fixtures/no-trigger-typo.jsonl:7, 11, 17, 19]` |
| 180 | Deduplicating by message id reproduces that stream's `result.usage` exactly and its `total_cost_usd` within 0.01% | `[verified: a plan reviewer's replay of evals/fixtures/no-trigger-typo.jsonl:34]` |
| 181 | A healthy recorded stream carries a `rate_limit_event` with status `allowed` | `[verified: evals/fixtures/no-trigger-typo.jsonl, Grep; line 22 per a plan reviewer]` |
| 182 | `read_environment_record` returns the CLI version and the checkout's commit, with a timeout and an empty-reading refusal | `[verified: evals/review_bench/runner.py:1065, Grep; its behavior per a plan reviewer]` |
| 183 | `RunStore` holds a run lock and a write-ahead directory record that a resume sweeps | `[verified: evals/review_bench/runner.py:1150, 1168, 1194, Grep; its sweep per a plan reviewer]` |
| 184 | `combine_attempt_records` sums every attempt's spend, a discarded attempt's included | `[verified: a plan reviewer's read of evals/review_bench/runner.py:550]` |
| 185 | msmr's per-run cap is ten times its representative dispatch cost | `[verified: evals/measure_subagent_model_resolution.py:89-91]` |
| 186 | Whether `--max-budget-usd` bounds a subagent's spend | `[unverified]`: `evals/review_bench/runner.py:1744` notes it as unverified |
| 187 | A project `PreToolUse` hook that denies a call leaves the tool listed to the model and acts only after the call is emitted | `[unverified]`, probe P9 |
| 188 | `--max-turns` bounds a `claude -p` run, which still ends with a `result` event | `[unverified]`, probe P9 |
| 189 | For a matching hook, the extension adds about 10%, 5% and 3% of samples at k = 1, 2 and 4 | `[verified: a plan reviewer's simulation in plan review round 3]` |
| 190 | Each `parity-exceptions.json` entry carries a reason from a closed set that includes `pending-phase`, naming the phase, and a kind, `registration` for a registration | `[verified: .claude/plans/cursor-adapter.md:844, 847]` |
| 191 | A recorded stream at CLI 2.1.143 carries `claude_code_version` on its init event and `message.model` on its `assistant` events | `[verified: a plan reviewer's read of evals/fixtures/skill-fired-code-review.jsonl:5, 14, 20]` |
| 192 | `review_bench.runner.extract_result_usage` reads token counts from the `result` event only | `[verified: a plan reviewer's read of evals/review_bench/runner.py:520]` |

## Critical files

These are this PR's files: the harness and its recorded fixtures. The follow-up PR's files are in Design item 10, and its own plan makes them its file limit.

Reuse:
- From `evals/measure_subagent_model_resolution.py`: `_run_claude_to_completion`, `_resolved_temp_project_dir`, `_extract_total_cost_usd`, `abort_launches`, `PER_RUN_BUDGET_CAP_USD`, and `abort_if_subagent_model_env_set`'s preflight shape.
- From `evals/measure_explore_recall.py`: `wilson_lower_bound`, the upper-bound symmetry `wilson_precision_lower_bound` already uses (`:527-545`), `DEFAULT_CUMULATIVE_STOP_USD`, `DEFAULT_TIMEOUT_S`, `validate_keep_artifacts_dir`, and the injected `launcher` callable its `run_batch` takes, which this harness copies as its test seam.
- From `evals/run_skill_evals.py`: `compute_session_store_dir`, `seed_temp_project_git`, `DEFAULT_WORKERS`, and `detect_trigger_in_lines`'s `input_json_delta` accumulation. `config_dir` comes from `_config_dir.py`, which `run_skill_evals.py:55` imports (row 114).
- From `evals/review_bench/runner.py`: `read_environment_record` (`:1065`) for the drift halts, and `RunStore` (`:1150-1194`) for the run lock and the write-ahead directory record (rows 182, 183).
- The thread-worker and signal precedent in `evals/review_bench/runner.py:1517-1527` and `evals/run_review_bench.py:2318-2335`, the latter's `os.umask(0o077)` included (row 149).
- The `evals/fixtures/dispatch-project/` and `evals/fixtures/temp-project/` fixtures, and the recorded `evals/fixtures/no-trigger-typo.jsonl` for the estimator and rate-limit tests.
- The standard library's `statistics.NormalDist` for the z values.

**DM1 — the harness** (`code-writer`, after P3's `claude -p` half, P9 and P10; verify with `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py` and `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/`):
- DM1's brief carries P9's selections as facts: the sample-end branch, the self-check reading, and the delivery receipt check. DM1 builds and tests only the selected one of each.
- `evals/measure_main_session_rule_adherence.py` — new: Design item 9's lifetime note, phases, arms, preflight, drift halts, observables, sample end, outcome seam use, attempt classes and retry, cost, run log, workers and interrupts, delivery check, smoke, self-check, calibration, sizing, interval, verdicts and report.
  - The sample launcher is an injected callable, as in `measure_explore_recall.run_batch`, so the stage loop runs under test with scripted outcomes.
  - The emitter's file name, the six handlers' command strings and the marker path are constants that Design items 2 to 4 fix.
  - In branch B only, the price table is a constant citing the vendor's pricing page and its fetch date.
  - Its CLI flags:
    - `--phase`, `--list`, `--seed`, `--model`, `--base-ref` (default the merge-base with `origin/main`) and `--candidate-ref` (gate phase, default `HEAD`);
    - `--dry-run`, which prints the schedule (arms, observables, wordings, scored targets and projected attempts) and launches nothing;
    - `--workers`, default `run_skill_evals.DEFAULT_WORKERS`;
    - `--timeout-s`, default `DEFAULT_TIMEOUT_S`;
    - `--budget-cap-usd`, default `PER_RUN_BUDGET_CAP_USD`, and `--cumulative-stop-usd`;
    - `--json`, `--keep-artifacts DIR` for each sample's raw stream lines, and `--run-log`, each validated outside every worktree and written owner-only;
    - `--resume LOG`.
- `evals/test_measure_main_session_rule_adherence.py` — new, with no live session and no read of the live repo. It covers:
  - arm construction against a fixture `CLAUDE.md` in a temp git repo, the `none` cut's refusal when its marker line is absent or repeated, and the hook rewrite against a fixture hooks directory;
  - the gate phase's refusal when the candidate lacks the six handlers or the emitter;
  - each scorer on adherent, non-adherent and unreached streams, synthetic and recorded, with these variants:
    - `bash x/review-ledger.sh render` and `cd d && ... render`, against `show`;
    - `git -C d commit` and `git commit -am`;
    - `Agent` and `Task`, and a dispatch ending an O3 or O4 sample;
    - a plugin-qualified skill name;
    - `model` present but empty;
    - parallel `tool_use` blocks in one message;
    - an `input_json_delta` cut before the stop point;
    - an event with a non-null `parent_tool_use_id`;
  - each unreached reason, and the flag for unscored calls of the target tool;
  - a wording-leak check that no prompt contains `model`, `render`, `code-review`, `plan-architect` or `MODE=consult`, and equal wording counts per arm under the blocked assignment;
  - the self-check scorer, in the reading P9 selects: two of three, a leaked `## Agent Briefing` bullet in `none`, and a named user-file path recorded without deciding;
  - the interval against the worked examples published in Newcombe 1998, not values computed by the code under test;
  - the sizing table, and by exact enumeration the figures item 9 promises: the planned-look pass rate across sizing p from 0.6 to 0.886, the regression pass rate over both looks, the matching hook's fail rate, and the gate-stall probability at the entry threshold, all within item 9's runtime budget;
  - the entry rule at z = 1.96, the calibration's attempt cap, the gate cap derived from calibrated reach, and the per-file void;
  - one stage-level test with a scripted launcher that drives calibrate, entry, sizing, the planned look, the extension for one observable only, each run verdict, and the one-arm-short outcomes;
  - cost, in the selected branch:
    - branch A: the reported cost read from the `result` event, a timeout charged the cap, and the cap sized from the smoke's costs;
    - branch B: the estimator on `no-trigger-typo.jsonl`, whose token totals must equal its `result.usage` and whose estimate must lie within 10% of its `total_cost_usd`; the estimator on a message with two content blocks; the price-table refusal; a timed-out sample estimated from partial usage, and charged the cap only with none readable;
    - both: an early-stopped or denied sample never charged the cap or zero, every attempt's charge counted toward the stop, the per-observable projection with its basis and range, and the stage refusal;
  - each attempt class, fault retry, and the pause that flushes, exits non-zero and prints a resumable status;
  - the recorded healthy stream in `no-trigger-typo.jsonl`, whose `rate_limit_event` has status `allowed`, classed as no fault, and a failure of no recorded shape pausing the run;
  - a second CLI version, model or ambient commit halting the run before the next sample;
  - the run log:
    - a fresh run refusing an existing log, the lock held, and a truncated final line tolerated;
    - start lines written before launch, and `--resume` sweeping a started slot with no finish line;
    - `--resume` starting the stop from the logged total, and refusing on a changed hash, prompt, scorer, version, model, seed or phase;
    - an interrupt at a stage boundary and one mid-stage with out-of-order worker completions, after which the scored set equals an uninterrupted run's and no calibration or smoke line fills a scored slot;
  - a SIGTERM mid-stage that ends every child and removes every temp project and session store, clearing the abort flag afterwards as `evals/test_measure_subagent_model_resolution.py:588` does;
  - the blocked-prompt recognizer on P3's recorded `claude -p` form;
  - the preflight abort, each delivery-check branch in the receipt check P9 selects, `--dry-run`, and each output path's refusal inside a worktree.
- `evals/fixtures/main-session-rule-adherence/` — new: one real stream per observable from P10, and P3's recorded `claude -p` blocked-prompt output, each cut to what the code reads. Streams drop the `system` init event, which lists the account's agents, plugins and MCP servers, and every file has home and temp paths replaced with placeholders.
- `evals/test_main_session_rule_adherence_fixtures.py` — new: a tripwire that no committed fixture under that directory holds a home-rooted path, `/var/folders`, `/private/var` or a real session id.
- `evals/measure_subagent_model_resolution.py` — the keyword-only outcome parameter on `_run_claude_to_completion`, `None` by default, and in branch B the keyword-only `stop_when` predicate, also `None` by default.
- `evals/test_measure_subagent_model_resolution.py` — cases for the outcome: it fills with the exit status and stderr tail; a child writing more than 64 KB to stderr before its first stdout line neither blocks nor times out; and with `outcome=None`, stderr stays `DEVNULL` and the two-value return is unchanged. In branch B, a case that stops at the predicate and kills the process group.
- `claude/.claude/scripts/select-tests.py` — constants for the new module, its tests and its fixture directory, appended to `_is_review_bench_change`'s hand-kept tuple. The new tests join that rule's targets and the target tuples of the two `CROSS_DOMAIN_EXCEPTIONS` rows for `_REVIEW_BENCH_SCRIPTS_DEPENDENCIES` and `SKILL_EVALS_RUNNER` (`:827-838`, row 122). Appending, not a glob over `evals/measure_*.py`, matches the file's existing shape, because those rows name each test by path.
- `claude/.claude/scripts/tests/test_select_tests.py` — selection cases for each new path, for an msmr edit, for an `evals/run_skill_evals.py` edit and for an `_config_dir.py` edit, each selecting the new tests, and the new module in the `pending` seed list (`:851`).
- `evals/README.md` — a "Main session rule adherence gate" section beside "Explore recall gate":
  - what it measures, the two phases, and its one-time pre-merge lifetime;
  - usage, including `--resume`, and holding a macOS machine awake with `caffeinate -i`, since a sleep trips the wall-clock timeout;
  - not to pull or switch branches in the main checkout during a run, and that a CLI self-update halts the run, after which `--resume` refuses and the remaining samples need a fresh run;
  - the local-only posture, and the containment of `--json`, `--keep-artifacts` and the run log;
  - that costs are nominal dollars, that the run spends the usage window of the account you run it under, and advice to run it outside working hours at the default worker count;
  - the run's footprint on the live config dir: rows in `.session-config-keys-log.jsonl`, files in `sessions/`, and, after a hard kill, leftover temp projects and session stores that `--resume` removes (row 160);
  - a pointer to this plan.
- `CHANGELOG.md` — an `[Unreleased]` entry for the harness, as the sibling instrument has (row 170).

**DM2 — calibration-derived fixtures** (`code-writer`, after the calibration run; the same verification):
- `evals/fixtures/main-session-rule-adherence/` — per observable, one adherent and one non-adherent stream from the calibration's `--keep-artifacts` directory, cut and scrubbed as DM1's are.
- `evals/test_measure_main_session_rule_adherence.py` — each scorer also runs on these streams.
- `evals/measure_main_session_rule_adherence.py` — only for an observable change the engineer chooses after a void.

Projected diff: about 3,500 changed lines with branch A, recounted against the sibling instrument, whose module and test come to 3,630 lines. DM2 is about 400 of them. Branch B would add about 200 for the stop predicate, the estimator and the price-table check.

## Verification

**Phase 0 — probes.** Run them in a scratch project outside any repository, whose `.claude/settings.json` registers throwaway probe scripts by absolute path; the live `~/.claude` stays on the main checkout.
- **Order.**
  - P3's `claude -p` half, P9 and P10 gate DM1, because a fail in any of them voids the harness's design. P9 also selects DM1's branches, and P3's half records the blocked-prompt form DM1 recognizes.
  - P3's half moves ahead of DM1, rather than leaving blocked-prompt recognition to the follow-up, because it takes a few session-run lines and gives DM1 a recorded form to test against instead of a guessed one.
  - Every other probe runs in parallel with DM1 and the calibration. All of them finish before this PR merges (rows 173, 174).
  - A fail in P1, P2, P4 or P11 stops the follow-up's design for revision.
- **Owners.** The engineer runs every interactive form, in P1, P2, P3, P3b, P4 and P5, and all of P7a. The session runs the `claude -p` forms, P9, P10, and P11's headless half. The session may drive an interactive form through the standard library's `pty` module only if it keeps the terminal transcript.
- **Record.** The PR body gives each result, which forms ran, and who ran them. A headless-only result does not satisfy P1, P2, P4 or P5.

- **P1 (row 15).** A `SessionStart` probe sleeps 3 s, then writes a file; a `UserPromptSubmit` probe logs whether the file exists. Run it 5 times in each of three forms: interactive with an initial prompt (`claude "<prompt>"`), interactive with a prompt typed once the input box appears, and `claude -p`. Pass: the file exists in all 15 runs.
- **P2 (rows 16, 21, 42, 75, 78).** Log `session_id`, `hook_event_name`, `source`, `agent_id` and `agent_type` from `SessionStart`, `SubagentStart`, `UserPromptSubmit` and `PreToolUse`, across these forms:
  - a fresh start, `/clear` and `/compact`;
  - `claude --resume <id>` and `claude --continue`;
  - `claude -p --session-id <uuid>` and `claude -p --no-session-persistence`;
  - `claude -p --resume <id> --fork-session`, the warm-dispatch form at `evals/run_skill_evals.py:624-632`;
  - `claude --agent <name>`;
  - a fork dispatch;
  - a scratch agent named `review-orchestrator` that makes one `Agent` call.
  - Pass when all of these hold:
    - each `SessionStart`'s id equals the id on the calls after it;
    - the `--agent` session carries `agent_type` and no `agent_id`;
    - the orchestrator's `agent_id` is the same on its `SubagentStart` and its `PreToolUse`.
  - Record the fork's payload shape (row 21).
  - Record the copies of a `SessionStart` nonce the session holds after each of three resumes, and whether the transcript still holds the first copy. The follow-up's plan sets its resume bound from these counts (item 10).
- **P2's PR-body line, for the follow-up.** The follow-up's PR body gives the resume counts beside the bytes saved per subagent spawn, recounted with `wc -c` on both `CLAUDE.md` versions.
- **P3 (rows 17, 127).** A `UserPromptSubmit` probe exits 2 with stderr text. Try the terminal, `claude -p`, and the IDE extension and desktop app where the engineer has them. While every prompt blocks, try `/exit`, `/clear` and `/hooks`.
  - The `claude -p` half runs before DM1. Its exit status, stderr text and stream events, scrubbed, become DM1's blocked-prompt fixture.
  - Pass: the text shows on each surface tried, and `claude -p` prints it and exits non-zero. Record each surface and which slash commands run.
  - Fail: switch the prompt gate to `{"decision": "block", "reason": ...}` on exit 0 and repeat; if neither shows, stop.
  - If `/clear` does not run under a block, the gate's reason never suggests it.
- **P3b (row 126).** A `SessionStart` probe prints one stderr line, exiting 0, then 1, then 2, both interactive and with `claude -p`. Record where each line surfaces. The emitter's failure status is the one the user sees; when none shows, the sidecar is the only channel.
- **P4 (rows 14, 74).** A `SessionStart` probe emits a nonce; drive an automatic compaction as `docs/precompact-hook-behavior.md:31` did. Pass: `SessionStart` fires with `source: compact` and the same id, and the session quotes the nonce afterwards. Fail: stop, because Main session would vanish after an automatic compaction.
- **P5 (rows 18, 76).** The scratch project's `CLAUDE.md` stands in for the user file. Steps:
  1. Start an interactive session in the scratch project.
  2. Mid-session, add an exit-2 `UserPromptSubmit` probe and a `SessionStart` nonce emitter to the scratch settings, and change the nonce in the scratch `CLAUDE.md`.
  3. Submit a prompt.
  4. Run `/compact`.
  5. Run `/clear`.
  - After each of steps 3 to 5, record:
    - whether the new probe blocks;
    - whether the new emitter's nonce appears;
    - which `CLAUDE.md` nonce the session holds.
  - The result picks Design item 11's case, and its third case goes to the engineer.
- **P7a (rows 22, 81).** In a scratch project, a logging hook registered for `SessionStart`, `UserPromptSubmit` and `PreToolUse` records each payload's keys, in the Cursor IDE with imports on and off and in the interactive CLI. Pass: every imported payload that fires carries `workspace_roots` or `cursor_version`, and the record gives each key's presence per event. Fail: stop, because the stand-down predicate needs another key.
- **P9 (rows 49, 106, 108, 146, 164, 187, 188).** A temp project lists `~/.claude/CLAUDE.md` by link and real path under `claudeMdExcludes`, holds a project `CLAUDE.md` with a nonce, and registers a scratch `SessionStart` hook emitting a second nonce as `additionalContext`. Run at least 3 samples under `claude -p --output-format stream-json --verbose`. Each asks the session to name every `CLAUDE.md` path it was shown and to quote the label before each nonce.
  - Pass: in every sample, the project nonce reaches the session and the user file does not. Evidence is both the model's report and a grep of the session jsonl for the project nonce and for the user file's first `## Agent Briefing` bullet. Both labels are recorded.
  - Record, for DM1's selections and checks:
    - whether the jsonl holds the memory block, which selects the self-check reading;
    - whether the stream carries a hook event holding the emitted text, which selects the receipt check;
    - which event carries each `usage` token field, and whether the fields appear at or before the `message_delta` that ends a message;
    - whether the init event carries the CLI version and each assistant message carries `message.model`;
    - every `rate_limit_event` shape seen.
  - Branch A comparison: run each of O1 to O4 once in the same temp project, with a scratch `PreToolUse` hook denying that observable's scorable tools, `--max-turns 20` and `--max-budget-usd 1`. Branch A holds when every run meets all of these:
    - the first scorable call appears in the stream with its complete input before the denial;
    - the run ends with a `result` event carrying `total_cost_usd` inside the turn bound;
    - scoring the stream cut at that call gives the same outcome as scoring the whole stream.
  - The turn bound of 20 is the plan's generous first guess, and P10's counts then set branch A's per-observable bound. The budget of 1 nominal dollar is about three times the explore-recall gate's mean full dispatch (`docs/cost-levers-considered.md:762`, read by a plan reviewer).
  - Selection: branch A when it holds, otherwise branch B when `usage` appears before the stop point, otherwise neither. The PR body and DM1's brief record the selected branch, self-check reading and receipt check.
  - Fail: when the exclusion fails, weigh `--setting-sources` (M9) and revise item 9. When neither branch holds, the measurement returns to the engineer.
- **P10 (rows 50, 110, 163, 165).** With P9's mechanism and branch A's deny hook, `--max-turns 20` and `--max-budget-usd 1` on every run, run the O3 and O4 prompts 5 times each with the merge-base `CLAUDE.md` and with its Main session cut, and the O1 and O2 prompts twice each with the merge-base `CLAUDE.md`. No run therefore reaches a `plan-architect` consult or a `code-review`. Keep every stream, and record the turns each run took to its first scorable call.
  - Pass: each O3 and O4 prompt reaches its scorable call in at least 4 of 5 runs per arm. That is a pre-screen for reach not far below 0.8, and the calibration's entry rule makes the decision. A prompt that fails is replaced before DM1.
  - Record whether `code-review` fires in either arm, any contrast, and whether any event carries a non-null `parent_tool_use_id`.
  - One stream per observable that reached its call, cut and scrubbed as DM1 says, becomes DM1's recorded fixture. The existing `dispatch-fired-explore.jsonl` has no tool input, so it cannot stand in for O1 or O2.
- **P11 (rows 168, 169).** Register two `SessionStart` hooks of about 9,000 characters each, each ending with its own nonce, beside the ambient hook set. Run interactive (the engineer) and `claude -p` (the session).
  - Record whether each nonce arrives, and whether the transcript at the payload's `transcript_path` holds both texts at the first `UserPromptSubmit`.
  - The second result decides item 3's transcript check.
  - Fail: a truncated second text means the cap applies per event, which stops the follow-up's two-file design for revision.

**After DM1 and after DM2.** Run `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py`, then `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/`. `select-tests.py` widens itself when the diff calls for it, as it does once here for its own edit. Once, before the PR opens, run the new test file with `--durations` to confirm item 9's runtime budget.

**Calibration — after DM1, run by the session.** Run `scripts/venv-run.sh python3 evals/measure_main_session_rule_adherence.py --phase calibrate --cumulative-stop-usd 300` from this worktree, with `--json`, `--keep-artifacts` and `--run-log` pointing outside the repository. The ceiling is 300 nominal dollars unless the engineer names another before launch (row 177).
- After the smoke and before the first scored sample, the harness prints the projected calibration spend and the ceiling in effect. The session gives both to the engineer in the session and continues (rows 138, 139).
- If the run is interrupted or pauses, `--resume` with the run log continues it.
- Pass: the self-check passes, and each content file has an entered observable. The PR body carries, per observable, only rates and counts:
  - both rates, the counts per attempt class, and the entry result;
  - the count per `cost_source`;
  - the projected gate size in samples.
- The session gives the gate run's projected dollar range to the engineer in the session, who chooses whether to raise the stop, relax the margin, or cut observables (rows 132, 133). The follow-up's plan records the choice in kind and the gate run's ceiling as a setting.
- Fail: the first void stops the run. The session reports the table to the engineer, who decides whether to replace an observable and which fallback to discuss (rows 136, 175, 176). A failed self-check also sends the arm mechanism back to P9's fallback.

**Follow-up PR, carried into its own plan.**
- After each dispatch: select-tests, then `scripts/list-shell-files.sh | xargs -0 scripts/venv-run.sh shellcheck` when shell files changed, and ruff when Python files changed.
- After D1: diff the concatenated bodies of both files, without their header blocks, against the merge-base `# Main session` group. Expected: only the split point, and Shipping's verify sub-bullet leaving for its pointer line.
- **P6 — live smoke after D2.** Copy the worktree's `claude/.claude/hooks/` to a scratch dir, append a nonce line to each copied content file, and register the copy's three hooks by absolute path in the scratch project. Copy the real `claude/.claude/agents/review-orchestrator.md` into the scratch project's agents. Check delivery by grepping the session's and each subagent's jsonl for the nonces, not by the model's report.
  - A main session, interactive and `claude -p`, holds both nonces, and its first prompt is not blocked.
  - A `general-purpose` subagent's jsonl holds neither nonce; a fork's holds both.
  - The orchestrator, dispatched on a one-line scratch diff, holds both nonces, and its first `Agent` call passes.
  - With the copy's `conduct.md` renamed, `claude -p` is blocked with a reason naming `conduct`, its cause, and the resolved `--config-dir`.
  - Renaming the copy's `dispatch.md` mid-session makes the next orchestrator's `Agent` call deny with the gate's reason.
  - After `/compact`, the session still holds both nonces.
  - Deleting the copy's dispatch gate mid-session denies the next `Agent` call with the harness's start-failure text, and a restart with its registration removed clears it.
- **Gate run, after D2 and before D3 and D4.** It starts only once the engineer has seen this PR's calibration projection and chosen how to proceed (rows 132, 133), and then `--phase gate` runs as Design item 9 says.
  - On a pass, the PR body carries the table and the input hashes, and D3 and D4 start.
  - On any other verdict, stop as Design item 9 says.
  - Before merge, re-run whenever a hashed input differs from the merge candidate's. D3 and D4 touch none of them, so they do not force a re-run.
- **After D3.** `check-main-session-rules.sh --config-dir <scratch dir holding a link to the worktree's settings.json> --hooks-dir <worktree>/claude/.claude/hooks` exits 0. With `--hooks-dir` at a copy without `dispatch.md`, it exits 1, naming the file and its remedy. Never run `./install.sh` from the worktree, because it would relink `~/.claude` into the branch.
- **P7b — Cursor, run by the engineer, merge-blocking.** Use P6's scratch project in the Cursor IDE with imports on, with imports off, and in the interactive CLI. Pass: no prompt is blocked, no Task call is denied, and the agent reports no nonce, so Cursor is unchanged (row 44).
- **P8 — after merge, run by the engineer.** Land or discard any uncommitted edits in the main checkout, and pull from a terminal with no Claude Code session running. Run `./install.sh`, whose checker must pass. Then start Claude Code and confirm the new session's two markers in `<config-dir>/.main-session-rules.d/` and a real `review-orchestrator` dispatch's `agent.<id>.*` markers.

## Out of scope

- Delivering Main session to Cursor and building its gate. `cursor-adapter` owns every `~/.cursor` write (G4), and the engineer chose to wait for it (row 44). This plan records the contract and leaves `.claude/plans/cursor-adapter.md` to its owner. The follow-up asks that owner for a one-line pointer to the six `parity-exceptions.json` entries rather than editing that plan.
- Design-decision records other than the two this change supersedes, `docs/cost-levers-considered.md`, and dated reports that cite `CLAUDE.md`'s Main session sections. They record past decisions, and `CLAUDE.md`'s opening line says where Main session now lives.
- Moving Agent Core into its own file, or adopting `omitClaudeMd`.
- The output-preferences bullet's "the rules above", which moves verbatim. GH-1091 tracks it.
- A config-key kill switch for the prompt gate (Alternatives).
- Defining `prompt-gate` in `plugins/claude-hook-review`'s skill. As with `batch-gate`, `test_hook_alignment.py` defines the class, and a plugin edit needs its own version bump. The D4 record names the gap.
- Lowering `GLOBAL_CLAUDE_MD_BYTE_LIMIT` to Agent Core's new size. The groups test's combined pin bounds the moved text, and lowering the ceiling binds every later Agent Core edit, which is a decision of its own.
- Raising the Claude Code floor to the probed version. The D4 record names the unprobed range (row 128).
- Restarting running sessions for the user. No script can (row 60), so README and the CHANGELOG state the step.
- Hook opt-outs the install-time checker cannot see. It reads only the config dir's settings files, and only at install time, so it misses:
  - `disableAllHooks` in project or managed settings;
  - `disableAllHooks` set in the user's settings after `./install.sh` last ran;
  - a managed policy that limits hooks to managed ones, whose setting name is unverified here;
  - `--bare` and SDK runs with no user settings.
  - Each is a deliberate opt-out of every hook, and row 131 keeps the no-hooks cohort's signal out of `CLAUDE.md` (rows 37, 100).
  - The D4 record and the CHANGELOG state that `disableAllHooks` drops Main session.
- A consumer who pulls without running `./install.sh` while `settings.json` is a real file. No hook registered in that file belongs to this change, so nothing can notice. The fix is the one documented step, `./install.sh`, which README and the CHANGELOG name (rows 6, 59).
- A session idle for more than 30 days. Its markers are swept, and its next prompt blocks until a restart (row 102).
- A wedged prompt gate. It fails open after its 10 s timeout, and the dispatch gate's `onFailure: "block"` backstops dispatches only (row 130).
- Adherence the gate does not reach, listed in Design item 9's "Scope of a pass". The plan schedules no Opus gate run, and the harness is retired after the follow-up's pass.
- Re-running the gate after merge, or re-checking later edits to the two content files (Design item 9's "Lifetime").
- The existing auth text in `evals/README.md`, which says both that a run carries no per-token charge and that it bills per token off-subscription. It sits outside the new section, which states the cost unit and that the run spends the usage window of the account it runs under (row 161).
- Giving a `context: fork` skill run Main session. Such a run receives no conversation and falls back to Agent Core (rows 43, 104).
