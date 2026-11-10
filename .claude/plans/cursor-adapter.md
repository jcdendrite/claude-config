# Cursor adapter for the user-level harness

## Context

Goal: make this repo's user-level harness — hooks, skills, agents, rules, and instructions — work in Cursor's local IDE as well as Claude Code, without placing any file in a consuming repo.

Ask: "I think plan-it for the whole adapter." — following the engineer's constraint "If getting my harness to work requires landing code or markdown in the [private project]'s repo, that's not acceptable."
The harness is built on Claude Code, and its users may also work in Cursor. Cursor already imports parts of it: `~/.claude/settings.json` hooks, `~/.claude/skills/`, `~/.claude/agents/`, and a project-root `CLAUDE.md`. Its "Include Third-Party Plugins, Skills, and Other Configs" setting is on by default. The import is partial, though. Cursor's docs do not say how it translates Claude-format `tool_input` fields. Several matchers this harness depends on (`Skill`, `Agent`, `AskUserQuestion`, `ExitPlanMode`, `MultiEdit`, Notification) are either unmapped or undocumented. Cursor reads only bare `~/.claude`, which bypasses the multi-account `CLAUDE_CONFIG_DIR` routing. Run unadapted, the harness half-works: gates can deny forever or never fire.

The intended outcome is one source of truth in this repo. An empirical payload spike comes first, run in the local Cursor IDE, in scratch repos, never against a consuming project's code. A thin adapter follows: payload translation between Cursor's and Claude Code's hook formats, plus Cursor-native artifacts generated from the Claude definitions and installed only at user level (`~/.cursor/`). Cursor cloud agents are out of reach, because they cannot run user-level hooks.

Cursor primary sources consulted this session:

- https://cursor.com/docs/hooks
- https://cursor.com/docs/reference/third-party-hooks
- https://cursor.com/docs/context/rules
- https://cursor.com/help/customization/rules
- https://cursor.com/docs/context/skills
- https://cursor.com/docs/subagents

Fetched by the staff-backend-engineer round-2 review:

- https://cursor.com/docs/reference/ignore-file
- https://cursor.com/docs/cli/reference/permissions
- https://cursor.com/docs/reference/permissions
- https://cursor.com/docs/agent/security/run-modes

## Phase 1 observations

Sources: the ones the observations admission rule lists, in the paragraph after Run order step 6, which governs this section. On-screen text enters only as a paraphrase, or as Cursor's own fixed message with every variable part replaced by a placeholder.

### Run facts

- Cursor IDE 3.27.22, launched from the Dock. Claude Code v2.1.312 served as the control. The Cursor CLI ran afterwards as a second launcher, for the sub-questions the IDE run left unrun (see "Cursor CLI follow-up").
- Run Mode Allowlist, empty allowlists, sandbox off, Workspace Trust on. The import toggle was off for S1 to S4 and S6, and on for S5 and S7. Every prompt observation below was made in that mode unless a bullet says otherwise. A short later run used Auto-review (see "Auto-review (IDE)").
- Every cell reported in this section ran on This Mac.
- Models: Cursor's agent used its default, Grok 4.7 High, for most cells. A few cells ran on Grok 4.6 High Fast or the picker's Auto setting. Claude Code ran Sonnet 5.5.
- Each step was run after a Cursor quit and relaunch, per the runbook. Hot reload was not tested.
- Cursor keeps the Shell tool's working directory across calls in one conversation and resets it in a new one.

### Payload (O1, O3, O8, O15)

- Tool names seen: `Shell` (not `Bash`), `Read`, `Write`, `Delete`, `Grep`, `Task`. Cursor-only event names also seen: `afterAgentResponse`, `afterAgentThought`, `afterFileEdit`, `afterShellExecution`, `postToolUseFailure`, `workspaceOpen`.
- `preToolUse` and `postToolUse` carry `tool_name`, `tool_input`, `tool_use_id`, `conversation_id`, `generation_id`, `session_id`, `model`, `user_email` and `workspace_roots`. `postToolUse` adds `tool_output` and `duration`. `postToolUseFailure` adds `error_message`, `failure_type`, `is_interrupt`.
- `beforeShellExecution` adds `command`, `cwd`, `sandbox`. `parent_agent_id` and `parent_tool_call_id` appear there and on `preToolUse` Shell and Read. `beforeReadFile` adds `file_path`, `content`, `attachments`. `beforeSubmitPrompt` carries `prompt` and `attachments[]` (`file_path`, `type`).
- `subagentStart` carries `subagent_id`, `subagent_type` (the probe agent reports its own name), `subagent_model`, `task`, `tool_call_id`, `is_parallel_worker`, `parent_agent_id`, `parent_conversation_id`. `subagentStop` adds `status` (`completed` and `error` seen), `duration_ms`, `tool_call_count`, `message_count`. The Task call's `tool_use_id` equals the subagent's `subagent_id`. No equality class includes `parent_tool_call_id`, so per-call attribution of a subagent's tool calls is not shown (O8.b).
- `sessionEnd` carries `reason`, `final_status`, `duration_ms`. `stop` carries `status` and `loop_count`, with no `last_assistant_message`. `afterAgentResponse` carries `text`.
- `cwd` and `tool_input.cwd` are non-absolute in every Cursor record, native and imported. Only Claude Code's records carry an absolute `cwd`. A gate that resolves its repository from the payload `cwd` falls back to its own process directory under Cursor.
- `beforeShellExecution` fired for main and subagent Shell calls (O3.a). In multi-root, `preToolUse` and `beforeShellExecution` each fired twice at the same instant with the same `workspace_roots` shape. The cause is unknown (O1.d).
- Large payloads: `postToolUse` and `afterShellExecution` received about 1.4 MB and 1.2 MB without error, uncapped. A pasted 108 KB file became a file-reference attachment, not prompt text, so no cell sent a large prompt body. The `unread` question (O23) is open.
- Hook environment under Cursor: names `CLAUDE_PROJECT_DIR`, `CURSOR_PROJECT_DIR`, `CURSOR_USER_EMAIL`, `CURSOR_VERSION`, and no `CLAUDECODE`. Ancestry is extension host, then Cursor, then launchd. Under Claude Code `CLAUDECODE` is present and a shell-exported canary reaches hooks from a plain terminal (O15.b, O15.c).
- CC2, the integrated-terminal arm of O15.a: `claude` started in Cursor's integrated terminal fired `UserPromptSubmit`, `PreToolUse`, `PostToolUse` and `Stop` once each on a Shell-equivalent Bash call. The hook environment names include `CLAUDECODE`, `CLAUDE_CODE_ENTRYPOINT`, `CLAUDE_PROJECT_DIR`, and the two settings-defined canaries (project and local). The shell-exported canary is absent, cause not established. `CLAUDE_CONFIG_DIR` is absent, so it ran under the default config directory, as CC1 did. Ancestry is `claude`, `bash`, `Cursor Helper: terminal pty-host`, Cursor, launchd. Per the engineer's report, the Bash call prompted.

### Imported registrations receive Cursor's payload untranslated (O16)

- The import maps the matcher (a `Bash` matcher selects `Shell`) but not the payload. Imported hooks receive Cursor's own payload: `tool_name: "Shell"`, Cursor-only keys, and no `permission_mode`, `prompt_id` or `effort`. Stop has no `last_assistant_message`.
- Consequences for the re-plan: an imported Stop gate has no last message in its payload, while `afterAgentResponse` carries `text`. A gate that exits when `tool_name` is not `Bash` never acts on a Cursor Shell call. The harness's fail-closed posture (`onFailure: "block"`) also does not survive the import on a crash or timeout: those fail open (see the execution-contract section). Both are gaps the re-plan must decide, not only observations.

### Imported hook outputs in the IDE (O2.e, O2.f)

With the toggle on, per imported event, in the IDE:
- `PreToolUse`: a Claude-shaped deny blocked the call and its reason showed in chat, with an "Agent Looping Detected" banner. No prompt showed, which rests on the engineer's report alone, because the `reason-` nonce has no legal note token. Exit 2 blocked and its stderr nonce showed in chat. `ask` prompted and ran, which is indistinguishable from the zero-hook control under an empty allowlist (Auto-review separates them: see "Auto-review (IDE)"). A context value was not seen by the agent.
- `PostToolUse`: exit 2 and a context value both ran, and prompted per the engineer's report. No nonce reached the chat.
- `UserPromptSubmit`: exit 2 blocked the submission and its stderr nonce showed in chat, repeated three times, with a "Submission blocked by hook" banner. A context value gave a plain reply with no extra text.
- `SessionStart`: the context value did not reach the agent. The seven entries fired once each, about 32 s after the send. Cursor's hooks log says `SessionStart trigger matcher "<name>" is not supported in Cursor, hooks will fire for all triggers` for `startup` and for `compact`.
- Cursor's hooks log says `Unknown Claude Code event "<name>", skipping` for `SubagentStart` and for `PostToolBatch`, and `Claude Code event "Notification" is not supported in Cursor and will be ignored`.
- At startup the Hooks pane showed `Failed to parse project hooks configuration` beside `No project hooks configuration found`. This is assumed, not checked, to reflect an absent project `.cursor/hooks.json`. The error changed no cell result.
- O2.f: an imported Stop hook that returns a follow-up each turn got 6 continuations, 7 fires in all, and the six replies matched the hook-record counts. 6 is the probe's own cap, so Cursor's bound on an imported Stop chain is at least 6 and was not reached.

### Matchers and arguments (O12, O21)

- `preToolUse` matcher: `Shell`, `Sh`, `^Shell$` and `Write|Delete` fired. `shell` did not. The matcher is a case-sensitive, unanchored regular expression on the tool name.
- `beforeShellExecution` matcher: it runs against the command text. `touch` and `^touch` fired, `TOUCH` did not.
- `beforeReadFile`: `Read` fired, `Shell` did not.
- Subagent events: the probe agent's name fired. `generalPurpose` never fired, including for the general-purpose agent.
- `sessionStart`: `startup`, `resume` and the alternation all fired on a new conversation. The imported SessionStart `compact` entry also fired on a new conversation.
- Imported Bash-matcher entries fired on Shell calls. `Agent` alone never fired and `Agent|Task` did (O21.h). No Cursor call reaches an `Agent`-only registration.
- Arguments: a quoted `'a|b c'` arrived as one argument, quotes removed, holding a pipe and a space. The command runs through a shell. Per `summary.md`, a native project-level hook's own working directory is in the workspace, and a native user-level hook's is the Cursor config directory. An imported user-level hook's was not recorded. PATH has Homebrew, `jq`, `python3` and a bash of at least 5.2. The shebang found bash 5.3.

### Decisions: what blocks, what is seen (O2, O19)

- A `deny` from a native `preToolUse`, `beforeShellExecution` or `beforeReadFile` hook blocked the call with no approval prompt. The message appeared in chat as `Rejected: <message>`, `Rejected: Command execution was blocked by a hook: <message>` or `Error: File read was blocked by a hook: <message>`. A Claude-shaped `permissionDecision: deny` and exit 2 also blocked on `preToolUse` (exit 2 showed its stderr text). The agent never quoted the `agent_message` in its reply, and no nonce appeared in the Hooks output pane.
- O2.g: in the desktop IDE, under Run Mode Allowlist, a native `preToolUse` `deny` on a Write call that creates a file blocked it with no approval prompt, shown in chat as `Rejected: <message>`. An edit of an existing file, an imported Claude-shaped deny on a Write, and the CLI were not run.
- `ask` on `preToolUse` and `beforeShellExecution` is indistinguishable from the zero-hook control, because an empty allowlist prompts every Shell call. `ask` on `beforeReadFile` blocked the read as an invalid response, with an error toast.
- `subagentStart` never blocked a subagent: `deny`, `ask`, non-JSON and fail-closed silence all let it run.
- Output behavior across the 34 S1 cells, with `failClosed` off and on:
  - `preToolUse`, `beforeShellExecution`, `beforeReadFile`: non-JSON stdout blocks in both modes. Empty stdout blocks only with `failClosed`. `{}` never blocks.
  - `beforeSubmitPrompt`, `postToolUse`, `stop`, `subagentStart`: no cell blocked.
- Context injection: a `postToolUse` additional-context value reached the agent, appended to the tool result inside `system_reminder` tags. A `sessionStart` value reached the agent in 2 of 3 attempts, in the Agents window and the IDE side panel. The failed attempt left no record that resolved to its cell. It also differed in arming order and in the relaunch. No cause is claimed.
- O20.b (IDE): a native user-level deny beat an `allow`, an `ask`, a neutral output and an exit 1 from a native project, an imported project and an imported local source. Every one of those 12 cells blocked. Each showed the user-level hook's nonce and no prompt, and both hooks fired. The reverse direction, an imported live-gate deny against a native output, was not observable, because the live gates ran and did not block under Cursor.
- O20.b, imported project-level direction (IDE): an imported project-level `PreToolUse` deny beat a native `preToolUse` `allow`, `ask`, neutral output and exit 1 on a plain Shell call. All four cells blocked with no prompt. Each showed the deny's own `reason-` text in chat, and both hooks fired. The `reason-` text has no legal note token. The notes therefore record `blocked` and `not-prompted`, and the text rests on the engineer's on-screen observations. This direction does not cover an imported user-level source, which is where the live gates sit.
- O20.c (IDE): a Stop follow-up survived the other source's neutral in both directions:
  - User-level follow-up: 5 follow-ups, with the project Stop firing 6 times and the user Stop 5.
  - Project-level follow-up: 6 follow-ups, with the project Stop firing 7 times and the user Stop 5. 6 is the probe's own cap, so Cursor's bound was not reached.
  - The fire counts differ between the cells and are not explained.
- A `stop` follow-up ran 5 times with `loop_limit` unset and 2 times with `loop_limit: 2`. Both runs show one fewer hook fire than agent replies.
- One repeated identical Shell call after a `deny` ran with no resolved hook record. Not explained. `summary.md` counts 9 records whose cell did not resolve, which may include it. The re-plan carries a deny-then-repeat check into the acceptance run.
- The first tool call after a relaunch is hooked (O29).

### Hook execution contract (O17, O19.b, O22)

- Hooks of one event start within about 1 ms of each other. A native and an imported `preToolUse` hook, each holding 2 s, started in the same millisecond. 20 sleeping imported hooks cost about one timeout, not twenty. O22.a's across-source half is answered by that cell. The S6 merge steps were not run.
- The default timeout is about 60 s and the `timeout` setting is in seconds (`3` ran as 3 s). Both are confirmed by the hooks log's `timeout` line and by summary heartbeats.
- Without `failClosed`, a timeout (killed, exit 1), a missing command (exit 127), a non-executable script (exit 126) and an exit-1 hook all fail open: the call proceeds and prompts normally.
- With `failClosed: true`, a timeout, a missing command and a non-executable script all block, with text naming the exit code and, for 127 and 126, the shell's own stderr. An exit-0 hook with empty output also blocks.
- Exit 0 with non-JSON stdout blocks without `failClosed`. With 20 matching imported hooks, Cursor lists one rejection per offending hook and none short-circuits.
- `none` and `{}` from imported entries leave the call to the approval flow (O19.b).
- O17.a to O17.c in the IDE:
  - Exit 1 fails open: the call prompted, then ran.
  - A timeout fails open: the call prompted, then ran.
  - Exit 0 with non-JSON stdout blocks, with no prompt and an error shown.
  - These hold for an imported `PreToolUse` entry with `onFailure: "block"` and with `onFailure` removed. The two variants gave the same result in each case, so `onFailure` has no effect on an imported entry.

### Sessions, processes and environment (O4, O5, O6, O18)

- A `sessionStart` env value reached later native and imported hooks, but no agent Shell leg carried it. It did not fire, at project level, in a conversation created in Restricted Mode before trust was granted. Tool-call hooks fired once trust was granted.
- The `S7.untrusted` cell, run in a workspace untrusted again through the Workspace Trust page, gave no evidence either way. `S7.untrusted.shell` reads `not-seen`.
  - The IDE agent panel returned a "Connection Error" on two sends. The cause is not established.
  - The Agents window asked to trust the folder before it would run, so no Shell call was made.
  - A retry of the same prompt later showed a pending approval, after the Workspace Trust page read trusted again. It was not run.
  - Whether tool-call hooks fire in a still-untrusted workspace stays untested (OD4 carries it).
- O5.c was not exercised: at a 2 s hold the first `preToolUse` came 4.1 s after `sessionStart`.
- A user-level native `sessionStart` `env` reached imported hooks (O18.a). Setting `CLAUDE_CONFIG_DIR` that way still left the routed `sessions/` and `session-end-records/` folders empty (O28.c).
- A detached child outlived its `sessionStart` hook without holding it open (O6).
- Extra `sessionStart` records appeared on several cells and are unexplained.
- O4: every Cursor hook and Shell call descends from a `Cursor Helper (Plugin)` extension-host process named for the workspace folder (variants `agent-exec`, `retrieval`, `always-local`, `user`), then Cursor, then launchd. Conversations in one window share that extension host, so most conversations have no ancestor unique to them. Where one does, it is the host of a different window or folder (depth 1) and Cursor itself (depth 2). Under Claude Code the unique ancestor is the `claude` process at depth 1. No single Cursor ancestor identifies one conversation, so a lock keyed on an ancestor would key on the window, not the conversation.

### Import of Claude Code configuration (O9, O19.c, O28)

- With the toggle on, project `.claude/settings.json` and `settings.local.json` hooks both load. Cursor offers no finer import switches. Agent self-reports (model replies, not records, so unverified): user-level `CLAUDE.md` no, user-level rules no, skills and agents yes.
- Silent imported registrations never blocked a call or changed a prompt (O19.c).
- Live harness gates (O28.a):

  | Gate | Claude Code | Cursor |
  |---|---|---|
  | code-review (Bash commit) | blocked | ran |
  | plan-review (Write) | blocked | ran |
  | credential-file read (Read tool only) | blocked (names the gate) | blocked (names the gate) |
  | no-op dispatch | blocked (names the gate) | blocked (names the gate) |

  In Cursor the first two gates executed, exited 0 with empty output, and did not block. The payload `tool_name` and `cwd` facts above bear on this. Cursor also inserts a `Co-authored-by` trailer into a commit command. The Claude Code read cell declined once with no tool call and only the retry counts.
- Live-gate follow-up, added after the first commit of these observations. An imported gate can no-op silently under Cursor, for at least two reasons: it filters on the tool name `Bash`, or it finds its repo from `cwd` or `$PWD`. Neither shows an error to the user.
  - code-review: `require-code-review.sh` exits 0 unless `tool_name` is `Bash`, and Cursor names its shell tool `Shell`. The gate exits 0 without acting under Cursor. This is read from the script, not replayed.
  - plan-review:
    - Repeat: the Claude Code control blocked again in the same repo state. A Cursor re-run under the run's original conditions (toggle on, Allowlist, empty allowlist) wrote the file with no block and no prompt. The miss is not specific to Auto-review.
    - The call was matched: the probe's copy of the harness's `Edit|Write|MultiEdit` registration fired on that Write, so the matcher selects it.
    - The deny format: the credential-file read and no-op dispatch gates use the same deny output and blocked as imported entries, and O2.g shows a native Write deny is enforced. This is an inference. No cell ran an imported Claude-shaped deny on a Write.
    - Replay: the payload was built from the key list `summary.md` records for a Cursor Write `preToolUse`, with no `cwd` key, `workspace_roots` set and `file_path` read from `tool_input`. The hook blocked when its process started inside the repo, with an absolute or a relative path. It exited 0 silently at its "not in a git repo" check when its process started outside the repo.
    - The hook never reads `workspace_roots`, so it resolves the repo from its own working directory.
    - Leading candidate, unverified: Cursor documents that user hooks run from `~/.cursor/` (row 28), and the live gates are registered at user level. Per `summary.md`, native user-level hooks recorded the Cursor config directory as their working directory, and native project-level hooks recorded the workspace. No cell recorded an imported user-level hook's directory.
  - Hook audit, partial, read from each script to its first `exit` and not replayed. Thirteen gates exit 0 unless `tool_name` is `Bash`, so they exit 0 without acting under Cursor, `deny-credential-bash-reads` and `require-code-review` among them. `require-worktree-for-git-writes` has no tool-name test and finds its repo from `cwd` or `$PWD`, so it may fall under the second reason (unverified). OD4 has the re-plan re-derive the full gate list from `claude/.claude/settings.json`.
  - The `Read` credential-file gate and the no-op dispatch (`Task`) gate fired and blocked. No other `Read`- or `Task`-keyed gate was run, and no kit gate acts on a credential read through Shell under Cursor. That is read from `deny-credential-bash-reads`, not replayed, and no cell ran a Shell credential read.
- `sessions/` gained 25 entries that match a recorded Cursor ancestor. `session-end-records/` gained entries, none of which match a recorded Cursor ancestor. O28.b: archiving a conversation, closing the window with Cmd+W and quitting Cursor produced no `sessionEnd` record in the kit under the closing cells. Cursor's hooks log and the kit's records show the following:
  - A window close or a quit requests `sessionEnd` with `reason: "window_close"`, once per window. The command hook does not run (exit code N/A, 0 ms) and the step reports that no hook returned a valid response. This held for every `window_close` request in the logs of the run, including a later quit that came after the closing cells' notes.
  - Between the relaunch and the closing cells' last note the logs hold one `sessionEnd` request, a `window_close` for one window, which fits the Cmd+W close. The archive left no `sessionEnd` request in that span.
  - One request carried `reason: "user_close"`. It came about two seconds after a `sessionStart` request for a new conversation. Its hooks ran (exit 0). The request came after the last closing-cell note, so the archive filed in the closing cell is not what produced it. Its trigger is not established: the nearest event is the new conversation's start.
  - `summary.md` shows one desktop `sessionEnd` record, under the S3 observe cell `S3.observe.shell-plain`. No hooks log among those read covers it.
  - The CLI recorded `sessionEnd` under `agent -p`. A plain tab close was not reproduced, because Cmd+W with no tab open raised a close-window dialog.
- A `Write`-shaped `preToolUse` and `afterFileEdit` fired with no visible tool call in one pasted-prompt cell. A plan-review gate would act on a write nobody saw.

### Cursor CLI follow-up (O7, O20.a, O22, O8, O16)

Every result in this section is from `agent -p` in Cursor CLI 2026.10.22-27dcf5f, run with `--force --trust` under a scrubbed environment, and none of it stands for the IDE. `--force` never prompts, so the CLI cannot record `prompted` or `not-prompted`.

- Hook loading: `agent -p` loaded the scratch project's `.cursor/hooks.json`, and the hook environment names match the IDE's.
- Import: the imported `.claude/settings.json` hooks fired under the CLI. The IDE import toggle was assumed still off from the S6 run and was not rechecked, so whether the toggle governs the CLI is not established.
- Events that never fired in any `agent -p` cell: `stop`, `beforeSubmitPrompt`, the imported UserPromptSubmit entry, `subagentStart` and `subagentStop`. A subagent's own tool calls still fired `preToolUse`. The imported SessionStart context did not reach the agent. `sessionStart`, `sessionEnd` and `workspaceOpen` fired once per `agent -p` invocation in the S3 cells.
- O20.a (CLI only): within one native source a deny beat every other output, in both orders.
- O7.a and O7.b (CLI only): a `preToolUse` `updated_input` rewrite was honored. The rewritten command ran and the original did not.
- O22 under the CLI matches the IDE: the default timeout is about 60 s, `timeout: 3` ran as 3 s, and with `failClosed` a timeout, a missing command (127) and a non-executable script (126) block while without it they fail open. Non-JSON stdout blocks.
- O16 under the CLI: an imported `exit2` blocked and its stderr text showed in chat. The live code-review gate did not block `git commit --dry-run`, as in the IDE.
- S3 observe under the CLI: shell, read, write, delete and grep cells produced hook records. `S3.observe.task` and `S3.observe.message` failed on three tries each with dropped connections to Cursor's API, so they have no outcome. Several other cells also showed connection errors in their replies while their hook records still arrived, so those cells rest on the records.
- Data correction: three S7 cross-source live-gate cells were first noted `blocked` by an over-broad reply heuristic although each reply says the command ran. With the engineer's go-ahead the three stored notes were changed to `ran`.
- Stalled once: one S7 cell hung 1530 s on a dropped connection and was re-run.

### Auto-review (IDE)

Run after the Allowlist cells. Auto-review is Cursor's default run mode (row 52). The Auto-review run used the mode Cursor labels "Auto-Review (with Sandbox)", which adds a sandbox, so the sandbox and Auto-review's own approval logic both differ from the Allowlist run. A different result cannot be pinned on that logic alone. The command allowlist was empty, and Read Access, the two file protections and Auto-Run Network Access were left as found.

- Baselines ran with no prompt: the plain Shell smoke, `S1.control.control` (import toggle off) and `S7.control.control` (toggle on).
- An imported project-level `PreToolUse` deny still blocked, with its `reason-` text and no prompt, in the `allow` and `ask` cells of the project-level direction above. Its `neutral` and `exit1` cells were not re-run.
- A native `beforeShellExecution` `ask` prompted, and the prompt carried the on-screen label "Hook requested approval". It ran after the engineer approved it. The baseline in the same mode had run with no prompt, so the prompt came from the hook.
- An imported `PreToolUse` `ask` (`S7.imp-PreToolUse.claude-ask`) ran with no prompt. The hooks doc, as relayed in G2, does not enforce `ask` for `preToolUse`. So the Allowlist prompt for this cell cannot be credited to the hook.
- `S3.legs.leg`, an agent Shell call whose probe writes a record outside the workspace, ran with exit 0 and no prompt. The native `preToolUse` hook fired for it, but this run left no leg record where the Allowlist run left one. The sandbox is the cause: OD9's C3 showed an agent Shell call's write outside the workspace failing under Auto-Review (with Sandbox) and landing under Allowlist with the sandbox off (OD9 check results).
- Whether Auto-review's classifier skips `beforeReadFile` for read-only calls it approves was not isolated. No cell tests it.
- Whether a hook's `allow` changes what the sandbox or the classifier does is untested. No Auto-review cell emitted an allow.
- `summary.md` withholds the prompt notes of these cells as "not compared in this run". The prompt claims above rest on the engineer's on-screen observations and the notes.
- The Allowlist and Auto-review runs share cell ids, so a cell's note list and fire counts mix both modes, for example `S2.decisions-beforeShellExecution.ask`, `S7.imp-PreToolUse.claude-ask` and both control cells.

### Not run, or answered in part (cut when the engineer set priorities mid-run)

- The IDE half of each of O2.e, O2.f, O17.a, O17.b, O17.c, O20.b (native-deny half) and O20.c is an answer. `summary.md` still prints "no outcome recorded" for them, because the CLI half of each reads pending: O20.c's because `stop` never fired under `agent -p`, the others' because their CLI control cell has no usable note (the CLI cannot record prompting).
- The CC2 arm of O15.a is answered by the CC2 cells. O28.b is answered by Cursor's hooks log, not by the kit.
- Answered by the CLI only, so the IDE answer is open: O7.b, O20.a. O7.a's IDE half is answered by OD9's C2 (OD9 check results).
- O23.a and O23.b are partial. O25 and O27 are not observable, because their desktop session cells were not run.
- OD6's trigger reads O17, O20.b and O20.c. Those now have IDE answers, with O20.b's imported-deny half answered from the project-level source only (see its bullets above). The re-plan evaluates OD6 on that basis.
- Not observable by any cell: MCP events, Tab events, `preCompact`.
- O3(b) has no recorded result. The observations report O3(a) only, so whether a Shell call fires preToolUse(Shell) without beforeShellExecution, or the reverse, is open. Repo resolution's `pin-check` entry states its scope by it.
- Verification 3 holds only on the engineer's acceptance of this list. O20.b's imported user-level direction, O25 and O27 are the sub-questions that read "not observable".
- The S2, S3 and S4 smoke cells have records under the CLI only, so the IDE's load evidence for those scenarios is their later cells' records.

### Data corrections and run disclosures (IDE segment)

- A desktop note on `S7.stop-user-followup` was first filed `followup-10` and the stored note was then corrected to `followup-5` against the hook-record counts. The kit has no retraction, so the correction was a hand edit of `notes.json`.
- When the `imp-UserPromptSubmit` step launched, the previous cell's command (`imp-PostToolUse` `claude-context`) was re-run while `S7.smoke` was armed. The engineer then sent the smoke prompt in the same conversation. No note was filed for the stray run, and its hook records may sit under the smoke cell.
- The `imp-SessionStart` smoke prompt was first sent in the smoke conversation, where `SessionStart` cannot fire. It was resent in a new conversation, so its Stop entries fired twice.
- In the CC2 session, an account check typed with `!` drew a model reply before the smoke cell, so that turn's hook records may sit under the armed smoke cell.
- The `S7.cross-imported-deny` and `S7.untrusted` steps and the `OD4.a` sub-question were added to the kit after the run began, and `selfcheck` passed with them.
- The `S7.cross-imported-deny` `allow` and `ask` cells, the `S7.imp-PreToolUse` and S2 `ask` cells, and the control and smoke cells ran once under Allowlist and once under Auto-review. Notes were appended, not replaced. "Auto-review (IDE)" states the effect on `summary.md`.
- The `S7.untrusted` prompt was first sent in the IDE window (Connection Error), then in the Agents window (trust dialog).
- The S2 `decisions-write` step and the `O2.g` sub-question were added to the kit after the live-gate follow-up began, and `selfcheck` passed with them.
- The `S5.livegates.write` cell ran a second time under the original S5 conditions and carries two `ran` notes. The Claude Code control re-run filed `blocked` and `named-gate` on `CC1.livegates.write`, which already held a `named-gate` note. Notes were appended, not replaced.
- The plan-review replay and the hook audit were read-only subagent work, and the replay ran on a throwaway copy of the scratch repo. The session read only the hook scripts and `summary.md`. A subagent it dispatched read Cursor's raw hooks log during the run, which the admission rule bars. Which hooks-log facts in this section, if any, came from that read is not recorded.
- For O28.b the "close conversation" action was an archive, and the Cmd+W attempt closed the window. The close-conversation cell carries two `ran` notes. The quit cell carries two as well, and the `summary.md` snapshot this section reads shows one, because the second was filed after it.

### OD9 check results

OD9's checks ran before Run order step 6. C1 to C4, C6 and C7 ran in the IDE from the desktop launcher, on project-level scratch hooks with the import toggle off, and a bullet names the run mode where a result depends on it. C5 and C8 read the kit's existing records, 3096 Cursor payload records and 26 Claude Code records, with no new run. The sources are `summary.md`, the engineer's on-screen observations and, for C6's docs half, Cursor's sandbox reference page.

C1, the Shell `cwd` (row 136):
- Payload `cwd` and `tool_input.cwd` were the empty string on every Shell record, 13 of 13 and 7 of 7, across preToolUse and beforeShellExecution. No non-empty relative `cwd` occurred. The payload `cwd` therefore names no directory, and an earlier call's `cd` cannot be followed from it.
- preToolUse and beforeShellExecution agreed on the `cwd` form in 6 of 6 calls. One event fired twice for one call, with the same `cwd` form and root membership both times.
- The agent's Shell process does not persist across calls: two legs' nearest shell ancestors were different processes.
- Which workspace root a relative `cwd` is relative to in multi-root is not observable, because no relative `cwd` occurred.
- File-tool targets arrived absolute on every observed event, though the prompts gave the agent relative paths: preToolUse for Read 4 times, Write as a create once, Delete once and Grep once, with their postToolUse and beforeReadFile records.
- The one Write that edits an existing file errored inside Cursor's tool with no preToolUse record, so an edit's target form is unobserved.

C2, the IDE half of O7.a (row 137):
- In the IDE, a preToolUse `updated_input` rewrite replaced the executed Shell command. The rewritten command's sentinel was created and the original's was not, and the agent's output was empty where the original would have printed.
- An unarmed rerun, the control, ran the original command. The CLI result agrees.

C3, agent Shell writes outside the workspace (row 127):
- The IDE offers four run modes: Allowlist, Allowlist (with Sandbox), Auto-Review (with Sandbox) and Run Everything (Unsandboxed). No Auto-review mode runs without the sandbox.
- Under Auto-Review (with Sandbox), an agent Shell call's write outside the workspace failed with "Operation not permitted" and exit 1, with no prompt, and wrote no file.
- Under Allowlist, with the sandbox off, the same write prompted, ran when approved, and landed.
- Allowlist (with Sandbox) and Run Everything (Unsandboxed) were not run.

C4, beforeReadFile's `content` (row 128):
- For a 108000-byte file, `content` equalled the file's text, with the size bucket 64 KiB or more on both sides.
- Cursor's Read tool refused to give the model that file, with a message that it "exceeds maximum allowed characters (100000)".
- Larger files are untested.

C5, from the kit's records:
- Predicate keys (row 158):
  - `workspace_roots` and `cursor_version` are on every Cursor record. `conversation_id` and `generation_id` are on every Cursor event except workspaceOpen, 3067 of 3067 records. workspaceOpen carries `workspace_roots` and `cursor_version` only.
  - No Claude Code record carries any of the four.
  - `generation_id` is the empty string on 205 records, the sessionStart and desktop sessionEnd ones.
- Ids:
  - `session_id` equals `conversation_id` on every Cursor record that carries both, 3067 of 3067.
  - `session_id`, `conversation_id` and `parent_conversation_id` are 36 characters and pass the id grammar. `subagent_id` and `tool_call_id` are 85 characters and fail it. `generation_id` is 36 or 43 characters, or empty.
- Subagents (row 138):
  - `parent_agent_id` is absent from every main-agent record. It is present on subagentStart (24 records), subagentStop (10) and the subagents' tool-call records (19).
  - A subagent's tool calls carry their own `conversation_id`, which their `session_id` equals. Their `parent_agent_id` equals the parent's conversation id in 19 of 19, and never the `subagent_id`.
  - On subagentStart, `conversation_id`, `session_id`, `parent_conversation_id` and `parent_agent_id` are one equality class, the parent's, and `generation_id` equals them too.
  - `subagent_id` equals `tool_call_id` in 24 of 24.
  - subagentStop's `child_conversation_id` differs from its `conversation_id` in 10 of 10. It matches the subagents' conversation ids on 13 of the 19 child records. The other 6 sit in a cell with no subagentStop.
  - Nested spawn, one observation: the grandchild's `parent_agent_id` is the first child's conversation id, not the root's.
  - The records hold no equality class for `parent_tool_call_id`.
- Hosts (row 165):
  - The hook-side and Shell-leg-side nearest `Cursor Helper (Plugin)` ancestors are one process in 18 of 19 IDE legs and 2 of 2 desktop legs. The exception, one hook record, pairs an `agent-exec` variant with a `retrieval` one.
  - The hook-side variant changes between events in one conversation, across plain, `agent-exec`, `retrieval`, `always-local` and `user`.
- `CLAUDECODE` (row 177): no Shell-leg record carries it (0 of 29), and no Cursor hook record does (0 of 3096). All 26 Claude Code records do.
- Turn order (row 139): afterAgentResponse precedes stop with the same `generation_id` in 15 of 15 turns, all in the IDE's S3 cells.
- `transcript_path` is a string on every stop (190 records) and every afterAgentResponse (15), and null on every sessionStart (441).
- Key lists (rows 144, 156):
  - Write's input keys: `file_path` and `content` only, the same in the create and the edit cells, with no old or new strings. The records do not show which cell made a true edit.
  - afterFileEdit: `file_path`, and an `edits` array whose items carry `old_string` and `new_string`.
  - postToolUse: `tool_output` is a string on every record.
  - Delete's input key: `file_path`.
  - Grep's input keys: `pattern`, `output_mode`, and `glob` or `file_path`.
  - Task's input keys: `description`, `prompt` and `subagent_type`, and optionally `model` and `run_in_background`.

C6, sandbox-writable paths (row 250), under Auto-Review (with Sandbox):
- Docs: Cursor's sandbox reference documents a `sandbox.json` with the keys `type`, `additionalReadwritePaths`, `additionalReadonlyPaths`, `readBoundary`, `additionalReadPaths`, `disableTmpWrite`, `enableSharedBuildCache` and `networkPolicy`. A user-level file and a workspace-level file merge, with the workspace one winning. Managed and hardcoded rules cannot be weakened, protected paths stay write-protected, and temp dirs are writable by default. The page does not say whether it applies to the IDE.
- Temp dirs: one agent Shell call's writes to `/tmp` and to `$TMPDIR` both landed, exit 0, no prompt.
- Workspace `sandbox.json`: with a workspace-level file listing one directory outside the workspace under `additionalReadwritePaths`, and a new conversation, an agent Shell call's write into that directory landed, exit 0, no prompt. The same kind of write outside the workspace failed under this mode with no such file (C3). The listed directory was the kit's own state directory, wider than one file.
- User-level `sandbox.json`: with a user-level file listing the same directory, no workspace-level file, and a new conversation, the same write landed, exit 0, no prompt. The user-level file was absent before the cell, and the kit removed it on the cell's note.
- Not observed: whether an agent can itself write either file, or whether Cursor treats them as protected paths; whether the setting reaches a path inside the routed config dir (the listed directory was a kit one, a path of the same kind); the run modes C3 skipped.

C7, which `updated_input` output the IDE applies, under Allowlist with an empty allowlist:
- With no `permission` field, the rewritten command ran in place of the original (sentinel present for the rewrite, absent for the original, empty output), and the call prompted.
- With `"permission": "allow"`, the form C2 ran, the call also prompted.
- A sibling beforeShellExecution entry received the rewritten command: its record's `rewritten` boolean was true, in the IDE and the CLI. The sibling preToolUse entry's was false.

C8, from the kit's records (row 251):
- Every IDE and desktop record has a `Cursor` main process among its ancestors. Its place in the ancestry list was 1 in 2019 records, 2 in 24 and 3 in 10, so it matches by process name, not at a fixed depth. The 16 records with no `Cursor` ancestor are the Claude Code controls.
- 117 of 119 conversations stay under one main process, and 2 span two.
- The nearest plugin helper changes between events in 2 conversations.
- How long a host lives is bounded by these records, not measured.

Other findings:
- `loop_count` (row 225): across five follow-ups the stops read 0 to 4, then 0 on the first stop after a second user prompt. The records hold no beforeSubmitPrompt entry for that cell, so the prompt boundary rests on the engineer's timing.
- Deny channels (row 182): a native deny carried text in both `user_message` and `agent_message`. Asked to repeat everything it was shown about the block, the model repeated the `user_message` text. The `agent_message` text appeared nowhere on screen or in the reply. The only agent-labelled text was Cursor's own "Do not suggest workarounds to the blocked tool." This is one observation.
- `~/.cursor/permissions.json` (row 197): absent under each of the four run modes, checked after a prompt in each and after a full quit and relaunch in one.

## Approach

Phase 1 ran. No Phase 2 file changes until `/plan-review` passes this revision (Phase exit; r14).

Phase 1's uncommitted kit recorded how Cursor's local IDE runs hooks, and `## Phase 1 observations` above is the one home of every run fact this section cites. This revision plans Phases 2 to 6 from those observations (rows 54, 80). The adapter is a native shim: one generated `~/.cursor/hooks.json` entry per harness registration runs the unchanged harness hook on a payload translated to Claude Code's shape. The import's own copies of those hooks keep running beside the shim's entries, because Cursor lets any deny win regardless of source (G5) and a copy cannot observe whether its twin runs (row 148). Native twins and Cursor-only adapter gates cover what translation cannot carry. OD9's checks C1 to C8 have run, and their results are in the observations' "OD9 check results" (OD9). Each agent Shell call's directory is pinned through C2's rewrite, which C7 showed the IDE applies with no explicit allow while the call still prompts (Repo resolution; r13; r14). OD7 to OD11 are all decided (rows 145, 232, 255, 258, 264), so no open decision gates any phase (r13; r15; r16; r17 cd R1). One design item gates Phase 4's merge: the CLI-coexistence rule the Phase 4 re-plan states (Installer; r21).

The user surface is every stow consumer. Phase 1's PR ships them only this plan, so Claude Code behaves exactly as it does today (row 66). The kit lives outside this repo, and the engineer deletes it after the run (row 68). Phases 2 to 6 ship scripts under `claude/.claude/scripts/cursor-adapter/`, which stow installs for everyone but which act only after an opt-in `install` writes `~/.cursor/` files on one machine. Phase 4 also changes `_lib.sh`'s identity resolution and its allow helper, and two session hooks, whose Claude Code path requirement 14 pins unchanged. The Cursor CLI is not an adapter target (row 5). It was the probe's second launcher for the sub-questions the IDE run left unrun (row 112).

### Phase 1: the payload spike

This is the spec the run followed. It stays as the record the observations cite, and as the spec a re-run rebuilds the kit from after a Cursor update.

**Placement.** The kit registers native probe entries in a scratch repo's own `.cursor/hooks.json` by default. That project-level file is a documented hook source (G5), and it loads only in its own workspace (row 63). A step registers in the user-level `~/.cursor/hooks.json` only when its observation depends on user-level placement, and `scenarios.json` marks each such step. The user-level steps are S3's `user` step, S5's native sessionStart entry, and S7's native deny entries. The Cursor settings the run changes, and any installed user-level step, apply to every workspace, which the run accepts because the engineer does no other work in Cursor until it ends (row 100).

**Kit layout.** The kit is five files, which one `code-writer` dispatch writes into a source directory the session creates with `mktemp -d "$HOME/.cache/cursor-adapter-kit.XXXXXX"` (row 66). That directory sits outside any git work tree, which a failing `git -C <dir> rev-parse --show-toplevel` confirms, and never in this repo. Nothing in it is tracked or stowed, so shellcheck, `select-tests.py` and the repo's test bar do not apply. Its prefix keeps it findable by glob, as `sandbox-spike.md:121`'s does. `summarize` writes `summary.md` here. After the dispatch's own `selfcheck`, the session runs `/code-review` on the five files by path (row 69).

- `probe.sh` and `probe.py`, the probe (below), written to the 3.11 grammar (row 58).
- `probe-kit.py`, the kit's subcommands. It loads `probe.py`'s path constants and record schema by path rather than restating them.
- `scenarios.json`: every scenario, step, family and cell. Each cell names its sub-question ids, its declared command or path, and its legal note tokens. A sub-question that a missing record can answer names its positive control: the same call's `.probe-ran/<cell>` sentinel for a Shell cell, or a same-conversation record from another entry. Each user-level step is marked, and so is each sub-question that compares approval prompts (Scenarios). Each event no cell can trigger, and each harness matcher or event G1 says Cursor lacks, is declared not observable with its reason (Observations).
- `vocabulary.json`, the only env names, process basenames and enum values a record may hold (Sanitization), plus the documented keys and tool names `scenario` and `summarize` compare against:
  - the documented Cursor and Claude key, event, tool, enum and env names. The env names come from the hooks page's Environment Variables table plus the Claude names this plan cites (`CLAUDECODE`, `CLAUDE_CONFIG_DIR`, `CLAUDE_CODE_ENTRYPOINT`, `CLAUDE_PROJECT_DIR`);
  - an allowlist of process basenames: `bash`, `sh`, `zsh`, `env`, `python3`, `node`, `claude`, `login`, `launchd`, and any basename starting `Cursor`. The allowlist is the session's proposal [unverified];
  - the id, number and enum paths;
  - the bare config dir's top-level names, taken only from the tracked `claude/.claude/` layout and Claude Code's documented directories, never from a listing of the real `~/.claude`;
  - every kit-defined name a record is compared against.

At run time the kit owns two roots, neither inside the other, so no workspace root is an ancestor of the kit state:

- `~/cursor-adapter-probe/` holds the kit state:
  - its contents: a marker file, `kit.json`, the raw records, the rendered cell table, the active-cell file, a `routed/` config dir for O28, and `bin/`, where `init` copies `probe.sh`, `probe.py` and `vocabulary.json` from the source directory;
  - every registration runs the `bin/` copy, and `probe.py` reads nothing outside the kit state root, so a registered call lives and dies with the kit state;
  - the imported mirror and O12's tilde cells spell the command `~/cursor-adapter-probe/bin/probe.sh`, as the harness spells its own commands (`settings.json:132`);
  - every other entry uses its absolute path unquoted;
  - `init` refuses a `$HOME` holding any character outside `[A-Za-z0-9._/-]`, so quoting and the space path reach only O12's cells. `selfcheck` builds its temporary home under a fixed alphanumeric prefix that passes.
- `~/cursor-adapter-scratch/` holds the two scratch git repos, `scratch` and `scratch-b`, the only workspaces the run opens.

**The probe.** `probe.sh` is the command every registration runs. It starts through `#!/usr/bin/env bash`, as a stowed hook does, so its record shows which bash that shebang finds under Cursor. It parses and runs on bash 3.2, expands no possibly empty array under `set -u`, and sources nothing from `hooks/`. In order, it:

1. resolves its own real directory without `readlink -f`, and points stderr at `/dev/null`, keeping a duplicate descriptor that only the `exit2` nonce uses;
2. exits 0 with empty stdout when `HOME` is unset or the kit's marker is absent, and never creates the kit directory;
3. checks that `python3` resolves and reports at least 3.11, the repo's floor (`README.md:111`, enforced at `install.sh:83`), running it with stderr discarded;
4. below the floor, or when `python3` or `probe.py` is missing or unreadable, appends one below-floor record and exits 0 with empty stdout. This mirrors `_config.sh:40`'s floor check but fails quiet on purpose, because a non-zero exit would read as an O17 or O19 result, while the harness's gates keep failing loud (row 38);
5. otherwise `exec`s `python3 -I -B probe.py` with stdin attached and the bash version as an argument.

The below-floor record is written before any scope decision, so it is the one record an out-of-scope call can leave. It holds only the entry id, the bash version, `python3`'s major.minor or `missing`, and whether `command -v python3` resolves under a Homebrew directory, under `/usr/bin` or under `$HOME`.

`probe.py` is one standard-library file, and on each call it:

1. reads stdin up to an 8 MiB cap, above O23's largest payload, unless its behavior is `unread`, and notes the byte count and whether the cap cut it. It decodes the bytes as UTF-8 with `errors="replace"` before parsing, so non-UTF-8 bytes still yield a record and only invalid JSON is malformed;
2. decides whether the call is in scope (Scoping, below), and out of scope exits 0 with empty stdout whatever its behavior, moving only the per-entry counter;
3. appends one record (Sanitization, below);
4. produces the output its behavior names.

Until the scope decision, `probe.py` maps every `BaseException`, including `SystemExit` from argument parsing, to exit 0 with empty stdout. Unknown, stray or missing arguments therefore give exit 0, empty stdout and empty stderr, plus a record when the call is in scope. Only `exit1` and `exit2` exit non-zero, and only after the scope decision. The stdin read, the record lock and every subprocess share one 5-second deadline. The value is the kit's own choice: half the smallest explicit `timeout` the mirror copies (10 seconds, `settings.json:178`). The hooks doc named no default number, as the staff-backend-engineer round-6 review relays. O22(b) measures the default. The code comment labels the value as the kit's choice. A record whose lock the probe cannot take by the deadline is dropped, and a dropped-record counter increments.

**Behaviors.** Every registration passes `--scenario` and `--entry`, plus either a fixed `--emit <behavior>` or `--emit cell`, which reads the behavior at call time from the active cell's row in the kit's rendered cell table. That lets one S1 step test three outputs without a relaunch. The active cell is the one `cell <id>` writes to the kit's active-cell file and `note` on that cell clears. An id outside the closed cell set is ignored, and with no active cell `--emit cell` gives `none`. Each record carries the resolved cell id, or `unresolved`, and the behavior the probe emitted, both from closed kit-defined sets.

The behaviors form a closed set:

- `none` (exit 0, empty stdout), `empty-object` (`{}`), `text` (one non-JSON line), and `neutral` (the per-event output S1 selects, below);
- `allow`, `ask` and `deny` in Cursor's format, where a deny carries distinct `agent_message` and `user_message` nonces;
- `claude-allow`, `claude-ask`, `claude-deny` with a reason nonce, and `claude-context` with an `additionalContext` nonce, as `hookSpecificOutput`;
- `claude-block`, a Stop block in the flat `{"decision":"block","reason":...}` form the harness's own Stop gate emits (`advance-past-commit-stall.sh:234`), with a reason nonce;
- `exit1`, and `exit2` with a stderr nonce;
- `sleep`, which appends a heartbeat each second until it is killed or 600 seconds pass;
- `unread`, which exits 0 without reading stdin;
- `followup`, a stop `followup_message` nonce. The probe emits `followup` and `claude-block` at most six times per conversation and entry, and answers `none` after that:
  - six is one more than the relayed `loop_limit` default of 5 (row 28), so a chain Cursor does not bound costs at most six turns;
  - the count lives in kit state under the record lock, apart from the per-entry call counter;
  - a call that cannot take the lock answers `none`;
- `context`, an `additional_context` nonce;
- `env`, a sessionStart `env` output that sets `CURSOR_ADAPTER_PROBE_SESSION_ENV` to the conversation's pseudonym and, in O28's routed cell, `CLAUDE_CONFIG_DIR` to the kit's `routed/` dir;
- `updated-input`, an `updated_input` that rewrites the Shell command to touch a `-rewritten` sentinel;
- `detach`, which starts a heartbeating child through `subprocess.Popen(start_new_session=True, close_fds=True)` with stdio on `/dev/null`, then acts as `neutral`. The child exits after 600 seconds, or once the marker is gone.

A `--hold <seconds>` argument delays any behavior, so S4 can race a slow sessionStart `env` against the first Shell call.

An allow-class or rewrite output (`allow`, `claude-allow`, `updated-input`) is emitted only when the call's command or path fullmatches the string the active cell declares in `scenarios.json`. Otherwise it is `neutral`.

The nonces:

- `scenario` draws every nonce fresh per cell;
- a visibility note counts only when the nonce appears verbatim where the note says;
- the nonces sit only in the rendered cell table under the kit state root, outside every workspace;
- every cell prompt tells the agent to open no file outside the scratch repo, and an agent that reads the table anyway is accepted residual for an attended run.

A `--shell-leg` mode records the same facts from inside an agent Shell call, with no stdin. The probe catches every exception and, for an in-scope call, records only its class name, so no input fragment reaches Cursor's logs. Malformed or empty stdin is out of scope (Scoping) and records nothing.

**Sanitization.** A record never holds a payload string value, an env value, a path, an absolute time, a raw id or a raw PID. Payload keys and tool names come from Cursor's schema, so a record holds each one as-is when it fullmatches `^[A-Za-z][A-Za-z0-9_]{0,39}$`, and as a counted `other` otherwise. On preToolUse and postToolUse an MCP tool name arrives `MCP:`-prefixed, so the colon fails the grammar. beforeMCPExecution and afterMCPExecution carry it bare (hooks doc), so every `tool_name` on those two events is a counted `other` (row 111). Every enum value, env name or process basename that `vocabulary.json` does not list becomes a counted `other` in its place (row 103). The raw records therefore hold nothing private, and the session may read them. O4 and O15 report an undocumented env or process name only as a count.

A record holds only these:

- key paths with JSON types, array indices as `[]`;
- event and tool names, booleans, and the values at `vocabulary.json`'s enum paths;
- numbers at its number paths, such as `loop_count`, plus stdin byte length and exit statuses;
- pseudonyms: the first 8 hex digits of an unsalted SHA-256, for the values at `vocabulary.json`'s id paths, for PIDs, and, tagged `unlisted`, for any other string leaf shaped like an id (a UUID, or 16 or more characters of `[A-Za-z0-9_-]`), so equality shows within one run;
- value-shape booleans, computed for every string leaf by its content shape, whatever its key:
  - a path is absolute, under a workspace root, or under a scratch repo;
  - a command contains a newline, starts with `cd ` (read after a Shell cell's `touch .probe-ran/<cell>;` prefix), arrives wrapped in `sh -c` or `bash -lc`, or contains the kit's `-rewritten` sentinel name;
  - an id matches the session-id grammar (row 50), with the id's length;
  - each variable the kit's `env` output sets is absent, equals the value the kit set for the record's conversation, or differs;
- the probe's argument count, and whether each argument contains a space or a pipe or starts or ends with a quote character;
- whether the probe's own working directory is under `~/.cursor`, under a workspace root, or under a scratch repo;
- the `vocabulary.json` env names present, PATH booleans (a Homebrew directory, a `$HOME`-rooted entry), whether jq, python3 and a bash of at least 5.2 are on PATH, the bash version, `python3`'s major.minor, and Cursor's version when it matches `[0-9]+(\.[0-9]+){1,3}`;
- for each ancestor up to pid 1, from one `ps` call under the deadline: its depth, PID pseudonym, executable basename, and elapsed time, plus that call's exit status or timeout;
- times in milliseconds since `init`, read from the system clock at `probe.py`'s first statement.

Every grammar check uses `fullmatch`, so a trailing newline fails it. `summarize` reports equality classes by key path. Records append under `fcntl.flock`, and `bin/probe.sh` is executable so Cursor can run it.

**Scoping and file ownership.** The kit takes every target from literals in its code and never from input.

- `init` refuses, so the init time never changes mid-run, when:
  - `HOME` is unset;
  - either kit root exists, marked or not;
  - a root's parent is a symlink.

  It creates the kit state root and its marker first, then the scratch root, the `bin/` copies, and `kit.json`. `kit.json` holds the init time and the hash of each file the kit later writes outside the two roots.
  - Each scratch repo has no remote and no `.gitignore`, because Cursor's ignore list takes every `.gitignore` entry (row 64), and one commit holding only `README.md`. Each holds an untracked `.claude/worktree-optout` (row 64), a `.env` whose placeholder values are plainly not credentials, `.probe-cells/` files for Read and Write cells, an empty `.probe-ran/` for the Shell cells' sentinels, and a `.probe-bin/probe` link to the `bin/` copy.
  - The kit's own git calls run with `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1` and a placeholder identity passed by `-c`, so the engineer's global signing, hooks-path and template settings never reach them.
  - `scratch-b` also holds the live-gate state (row 64): an untracked `.claude/plans/probe.md`, a placeholder `.netrc`, and a staged placeholder file.
  - `init` also creates, under `bin/`, a directory whose name contains a space holding a link to `bin/probe.sh` (O12) and a non-executable copy of `probe.sh`, `bin/probe-noexec.sh`, which O22's spawn-failure cell runs by its absolute path, unquoted, and in `scratch`'s `.probe-cells/`, a pasted-prompt file of about 100 KiB (O23).
  - It ends by listing the bare `~/.claude` as `scenario` does (below). Its one option, `--approval-pinned`, records that the run mode, the sandbox, the allowlist or auto-run could not be set for the run (Scenarios).
- A call is in scope when one of these holds:
  - a hook call carries a non-empty `workspace_roots` or a `cwd`, and every entry of whichever are present is an absolute string path that resolves inside a scratch repo, compared by path component after `realpath`;
  - a Shell leg's working directory resolves inside a scratch repo;
  - a payload with neither field, an empty `workspace_roots` and no `cwd` included, comes from a conversation whose pseudonym an earlier in-scope payload of the same scenario registered;
  - the call is `unread`, which reads no payload and so is only ever a fixed `--emit` on a project-level entry. It is in scope by that placement (row 63) and records only that it fired.

  Malformed or empty stdin is out of scope. Out of scope, every behavior gives exit 0, empty stdout, empty stderr, no record, no child and no wait. The one record an out-of-scope call can leave is the below-floor record, which `probe.sh` writes before any scope decision. Only the per-entry call counter moves, so `summarize` can tell an entry that never fired from one whose calls all fell out of scope.
- `scenario <id>` renders the scenario's desired file set and reconciles to it.
  - The set is drawn from fixed names: each scratch repo's `.cursor/hooks.json`, `.cursor/agents/cursor-adapter-probe.md`, `.claude/settings.json` and `.claude/settings.local.json`, plus `~/.cursor/hooks.json` for a user-level step.
  - The probe agent moves to `~/.cursor/agents/cursor-adapter-probe.md` only if the fetched subagents doc documents no project-level agents directory.
  - Inside the scratch root it writes and removes the fixed names directly, with no hash, because only the kit creates that root.
  - It writes each `~/.cursor` target directly, without following links, then records the file's hash in `kit.json`.
  - It removes such a file once it falls outside the set and its hash still matches.
  - A crash between the write and the record leaves an unrecorded or hash-mismatched file. The next `scenario` refuses it (the refusal rule below), `teardown` names it, and the engineer removes it by hand (row 82).
  - It refuses, touching nothing, when a `~/.cursor` target exists and is not recorded with a hash that still matches the file, when a user-level step finds `~/.cursor` missing, or when a target or any parent is a symlink.
  - The refusal names the path and the remedy: open Cursor once, move the engineer's own `~/.cursor/hooks.json` aside, or remove a file a crashed render left.
  - It reads `kit.json` only for the fixed names' hashes, so no other entry there can name a target.
  - Before it writes, it checks every key, event name and enum value it renders against `vocabulary.json`'s documented set, and refuses on any other.
  - Rendered scratch settings carry only `hooks` and `env` keys, never `enabledPlugins`, marketplaces or permissions.
  - At each install it lists the bare `~/.claude` (row 53) into the raw records: the entry count per top-level directory, with names only where `vocabulary.json` holds them, and the pseudonyms of the all-digit names in `sessions/` and `session-end-records/`.
  - `summarize` takes one more such listing when it runs, so a scenario's closing quit counts, and draws only O28(b) from these listings.
- `teardown` runs after Cursor has quit. It removes each recorded `~/.cursor` file whose hash still matches, and each `~/.cursor` directory the kit created once empty, leaving a changed or unrecorded file in place and naming it. It deletes no directory tree and is idempotent (row 98). It ends by printing the folders the engineer deletes by hand: the kit state root, the scratch root and the source directory. It then prints, by setting name, the reverts it cannot make, whose prior values stay in the engineer's own notes:
  - the engineer's own `~/.cursor/hooks.json`, if they moved it aside;
  - the toggle, the run mode, the allowlist, auto-run, the sandbox setting and the scratch folders' workspace trust.

**The kit.** `probe-kit.py` has seven subcommands: `selfcheck`, `init`, `scenario <id> [--step <name>]`, `cell <id>`, `note <cell> <token>`, `summarize` and `teardown`. Every step name and cell id is checked against `scenarios.json` before it reaches a path.

- `scenario` prints the scenario's toggle state, the relaunch step restating the run's one launch method (row 70), a smoke prompt, and each cell's prompt preceded by the `cell <id>` command that arms it, all from `scenarios.json`. It refuses every Cursor scenario but S1 until S1's results are in, meaning every S1 cell carries a note, `not-seen` included, because the other scenarios' observation entries emit `neutral`.
- `note` accepts only the tokens `scenarios.json` declares legal for that cell's question, from this closed set: `ran`, `blocked`, `prompted`, `not-prompted`, `error-shown`, `agent-nonce`, `user-nonce`, `stderr-nonce`, `context-nonce`, `followup-<n>`, `floor-message`, `named-gate`, `other-gate`, `in-chat`, `in-hooks-output`, `not-seen`, `yes`, `no`, and `run-stopped`, which every cell accepts (Run order). A Shell cell's command starts with `touch .probe-ran/<cell>;`, joined by `;` so the sentinel shows the call ran even when the command after it fails.
- `summarize` gives each sub-question (Observations, below) one of four results: an answer; "did not fire", when the sub-question's declared positive control is present and the entry's record is absent; "no outcome recorded", when a record, sentinel, control or note it needs is missing; or "not observable", with its reason. Every state this plan names maps to one of the four. The reason is one `scenarios.json` declares, or one of these run-time reasons:
  - "control acted on ordinary calls" for a toggle-on scenario's decision cells (Scenarios);
  - "block of unknown origin" for O28(a) on a live-gate cell noted `blocked`;
  - "CC1 control read <token>" for a live-gate cell, and "S5 `Bash` cell read <token>" for an S7 cell whose deny side is the live gate (Scenarios);
  - "not compared in this run" for a marked prompt comparison under `--approval-pinned` or when S1's zero-hook control is noted `not-prompted`;
  - "no neutral form in S1" for a later scenario's data on such an event (below);
  - "run stopped before this scenario finished" for a sub-question with a cell noted `run-stopped` or left unnoted in a scenario holding a `run-stopped` note;
  - "scenario not run" for a sub-question none of whose scenarios was rendered.

  It never defaults a missing note. It groups pending sub-questions by scenario, so a finished scenario's gaps stand apart from unrun ones. Under each answer it lists the facts it rests on:
  - key paths and types per event and tool, and equality classes;
  - which entries fired for which cells, flagging an entry that never fired in a step, judged per step, unless `scenarios.json` declares it not observable, and reporting a declared one that fired anyway;
  - the outcomes, and S1's `neutral` selection, with any S1 note whose cell differs from the record's resolved cell flagged, and `unresolved` records left out of every answer;
  - below-floor records on their own line, with `python3`'s major.minor, so a below-floor interpreter does not read as "never fired";
  - the dropped-record counter and a count of unparseable record lines;
  - env-name sets and ancestry chains per scenario, cell and entry, stating whether `CLAUDE_CONFIG_DIR` was present in each, with an ancestor reported only as alive at the last record, and an empty chain from a failed `ps` read as "no outcome recorded";
  - for O28(b), only whether `sessions/` and `session-end-records/` gained entries and how many new entries a recorded Cursor ancestor's pseudonym matches, never an absolute count or any other directory. A negative reads "state may have landed elsewhere" when the imported hooks carried `CLAUDE_CONFIG_DIR`;
  - for O28(c) and O18(b), whether `routed/sessions/` and `routed/session-end-records/` hold any entry, which `summarize` lists itself, because only the kit's routed `env` output points a process there;
  - for O2(d) and O2(f), "cap reached, no Cursor bound observed" whenever a record shows `none` emitted under a `followup` or `claude-block` cell, never inferred from a chain's length;
  - the Cursor version the records carry.

For each event, `neutral` is the first of `none` and `empty-object` whose `failClosed: false` cell in S1 ran and, for a Shell cell, drew the same approval prompt as S1's zero-hook control. The rules around that selection:

- Only Shell cells compare prompts, so on the other events `neutral` rests on the call having run.
- When S1's zero-hook control is noted `not-prompted`, no comparison can show a suppressed prompt. The Shell events' `neutral` then also rests on the call having run, and `summarize` reports that selection undecided: not observable, "not compared in this run".
- An event where neither form qualifies gets no observation entry in any later scenario. `summarize` reads that event's data from them not observable, "no neutral form in S1".
- An event no S1 step covers, such as sessionStart, sessionEnd or subagentStop, takes `none` as its observation output.
- The no-entry rules above apply to native entries only. Imported observation entries, one per source registration, always render and emit `none`, Claude's own no-decision form, and S5's and S7's control cells show what Cursor makes of it.
- Only a cell that tests an allow emits one, and no observation entry ever does (row 51).

**Self-check.** `selfcheck` is the kit's only test. It must pass before `init`, and again after any later kit edit (Run order). Each case runs the kit as subprocesses whose `HOME` is a fresh temporary directory holding an empty `.cursor` and a copy of the real `~/.claude/settings.json` for the mirror, with `CURSOR_*`, `VSCODE_*` and `CLAUDE*` removed from their environment, so it never opens the real `~/.cursor` or kit roots. Every `probe.sh` case runs through `/bin/bash`, macOS's bash 3.2, except the two Direct-exec cases, which after `init` execute `bin/probe.sh` directly by path and through a scratch repo's `.probe-bin/probe` link, so its mode, shebang and real-directory resolution run as Cursor runs them. It prints one result line per case and exits non-zero if any fails. The cases cover what the run's safety and answers rest on (row 82):

- Sanitizer: hostile values inside an in-scope payload yield records whose keys and string leaves hold no payload string or fragment of one, no planted env value and no temporary-home path. The hostile values are a path as a key, a hyphenated key, a 41-character key, an email value, an `MCP:`-prefixed tool name, a bare tool name on a beforeMCPExecution payload, an id in an unlisted key, an undocumented enum value, a nested array and non-UTF-8 bytes, plus a planted env name and an unlisted process basename. In the record:
  - the path, hyphenated and 41-character keys, the `MCP:` tool name, the bare MCP-event tool name, the undocumented enum value, the planted env name and the unlisted basename each show only as a counted `other`;
  - an undocumented identifier-shaped key, and a tool name on any other event than the two MCP ones, appear as written (row 111).

  Positive partners, checked against the test's own known values, show a record holds what the observations read:
  - a doc-shaped payload's key paths and types equal a hand-written literal;
  - each value-shape boolean is true on a positive input and false on a negative one, and the `env` shape takes all three of its values across two conversations;
  - equal ids give equal pseudonyms and different ids differ, with the raw id absent;
  - the ancestry list is non-empty, holds the pseudonym of the test process's own PID, and records the `ps` status;
  - a split-argument call exits 0 with empty stdout and stderr and records its argument count and shape booleans.
- Scoping: the scope function, called directly, rejects a payload outside the scratch repos, a sibling-prefix path, a `..` segment, a symlink out of a scratch repo, a relative path, a non-string entry, a multi-root workspace with one non-scratch root, roots inside with `cwd` outside and the reverse, an empty `workspace_roots` with no `cwd`, a field-less payload from an unregistered conversation, and malformed or empty stdin. It accepts the in-scope partners and a field-less payload from a registered conversation.
  - End to end, one out-of-scope call per behavior class that acts differently (`exit1`, `exit2`, `sleep`, `detach`, `env`, `updated-input`) and one out-of-scope `--shell-leg` call each give exit 0, empty stdout and stderr, no record and no child. `unread`, in scope by placement alone, exits 0 without consuming stdin.
  - An allow-class or `updated-input` cell emits its output for its declared command only, and `neutral` for a longer command containing it and for an unrelated one.
  - Under `--emit cell` over a three-cell table, each active cell yields its own stdout, an id outside the closed set or no active cell yields `none`, and each record carries the resolved cell and the emitted behavior.
- Follow-up cap: under `followup` and under `claude-block`, seven in-scope Stop calls of one conversation and entry, each its own process, emit six times and then `none`. A second conversation and a second entry each start at zero, an out-of-scope call leaves the count unchanged, and a call that cannot take the lock emits `none`.
- Fail-quiet: with the marker absent, `probe.sh` exits 0 with empty stdout and stderr, and the temporary home's listing stays unchanged. With `probe.py` missing, a stub `python3` reporting 3.9, or no `python3` on PATH, it does the same but adds exactly one below-floor record under the kit state root. An invalid `--emit` does the same and adds one record only when the call is in scope.
- Containment, over a listing of names, modes and hashes:
  - the real kit roots' listing is the same before and after `selfcheck`, whether they exist or not, and both roots appear under the temporary home after `init`;
  - after `init` and each scenario, the temporary home differs from its pre-`init` listing only inside the two roots and at `scenario`'s fixed `~/.cursor` names, the user-level agent fallback included, and after `teardown` it differs only inside the two roots;
  - `scenario` refuses an unrecorded `~/.cursor/hooks.json` and one whose recorded hash no longer matches, and `teardown` leaves each in place.
- Scenario lint:
  - an allow-class, `updated-input`, `followup` or `env` behavior appears only on a cell that declares that test (row 51), and `unread` only as a fixed `--emit` on a project-level entry;
  - every cell prompt carries the stop rule (Scenarios) and the sentence telling the agent to open no file outside the scratch repo, and every Shell cell's command starts with `touch .probe-ran/<cell>;` and holds no absolute, `~` or `..` path;
  - only the live-gate `Agent|Task` cell's dispatch text matches the copied `NOOP_PHRASE_RE`;
  - every key, event name and enum value in each rendered `hooks.json` and `.claude/settings.json`, and in each behavior's output, is in `vocabulary.json`'s documented set, and a misspelled key refuses the render;
  - the rendered mirror, parsed beside the temporary home's `settings.json` copy, holds one entry per source registration with an equal event, matcher, `if`, `onFailure` and `timeout`, and differs only in its command;
  - no entry rendered into `~/.cursor/hooks.json` carries `failClosed: true`;
  - it prints one sample stdout per behavior, one rendered `hooks.json` entry per registration shape, and every sub-question id with the cells that name it. Before `init`, the session compares the first two with the fetched hooks-doc excerpt and the third with the Observations table.

Out of scope lists the checks cut. The kit's `selfcheck` also ran cases beyond this list, so the list is the floor the run rested on, not the kit's full case set.

**Scenarios.** `scenarios.json` defines each scenario's toggle state, steps, registrations and cells. Each scenario or step starts with Cursor quit. The engineer runs `scenario <id>`, launches Cursor by the run's one launch method (row 70), opens a scratch repo, and starts a new conversation, so no result but O25's and O29's depends on hot reload or launch timing. At a workspace-trust prompt, the engineer trusts that scratch folder only. After a step's last cell is noted, the engineer, in order:

1. quits Cursor with the toggle unchanged, so a scenario's quit-time observations, S5's closing quit and S9's open-conversation quit among them, run under its own toggle;
2. at a scenario's end, runs `summarize` while its files are still installed, so a quit-time event lands on the entries of the step that caused it;
3. runs `scenario` for the next step or scenario;
4. when the next scenario's toggle differs, sets it in a launch by the same method that starts no conversation, then quits Cursor again;
5. launches Cursor for that step, as above, and sends the scenario's smoke cell.

If a smoke cell did not fire, the engineer sends it again in the same conversation, and `summarize` reports the first miss. In every scenario that runs Shell legs, the first smoke cell also checks that a leg's record landed.

Every cell prompt says it is a probe test, names the one call to make, and tells the agent to run no skill, subagent or other command before or after it. If the call is blocked or denied, the agent reports the shown text verbatim and stops, with no retry, edit or re-stage. This stop rule matters most for the live gates, whose deny texts tell the agent to run a review skill and retry (`require-code-review.sh:233`, `require-plan-review.sh:339-341`), because a review that ran would write a marker that disarms the trigger. If an agent starts a review instead of stopping, the engineer stops it and reruns that cell (row 105). If the review got as far as writing a marker, the engineer first appends a line to the staged placeholder or to `probe.md`, and re-stages the placeholder, so the marker no longer matches.

Cursor runs in Allowlist mode with an empty allowlist and the sandbox off, so every Shell command asks and a suppressed prompt shows (row 52). If the run mode, the sandbox, the allowlist or auto-run cannot be set for the run, `init --approval-pinned` makes `summarize` report each prompt comparison `scenarios.json` marks as not observable (row 84). An allowlist or auto-run the engineer did not declare shows as S1's zero-hook control noted `not-prompted`, which also makes `summarize` report those comparisons not observable (The kit).

Each toggle-on scenario starts with a control cell under `neutral` entries only, read against S1's zero-hook control. A block or changed prompt that S1's control did not show means the live harness's imported hooks act on ordinary calls, which is itself an O16 result, and `summarize` marks that scenario's decision cells not observable (row 53). One that S1's control also showed is a Cursor-side effect, never an import result. In toggle-on scenarios, every cell but the live-gate family and the cross-source arm's live-deny steps runs in `scratch`, because `scratch-b`'s untracked plan makes `require-plan-review` deny every Write there. Payload capture never depends on a call's decision.

Most cells come from five families that `scenarios.json` declares once and `scenario` expands, which keeps the kit proportionate:

- **Matrix** (S1): event × `failClosed` × stdout, with one step per event and `failClosed` pair and one cell per stdout.
- **Matchers** (S3): per event, the matcher values O21 lists, each on its own entry.
- **Imported mirror** (S5, S7): one project-level imported registration per harness registration in the stowed `~/.claude/settings.json`, the file the import reads, which `scenario` reads at render time. Each copies the registration's event, matcher, `if`, `onFailure` and `timeout`, and swaps only its command for the probe. It emits `none` in S5, and S7's cells vary its PreToolUse entries.
- **Live gate** (S5, with CC1's first step as its Claude Code control): for each Cursor-mapped PreToolUse matcher, an inert call in `scratch-b` that reaches a named live gate's deny branch in the state `init` creates (row 64):
  - `Bash`: `git commit --dry-run -m probe` over the staged placeholder, under `require-code-review`. A dry run creates no commit, so the staged state survives a missed deny;
  - `Edit|Write|MultiEdit`: a Write creating `probe-write.txt` while the plan is untracked, under `require-plan-review`;
  - `Read`: a Read of the placeholder `.netrc`, under `deny-credential-file-reads`, because Cursor's default ignore list already covers `.env*` (G7);
  - `Agent|Task`: a Task whose dispatch text reads `Do nothing and report back immediately.`, under `deny-no-op-dispatch`. The cell prompt says that text is the exact string under test, because the harness otherwise tells Claude Code not to send a no-op dispatch (`claude/.claude/CLAUDE.md:147`).

  Each cell's legal notes are `floor-message` whenever the shown reason contains `could not source _lib.sh`, whatever label prefixes it; for any other reason, `named-gate` when it starts `Blocked by <label> gate:` with the cell's declared label, `other-gate` when it starts that way with another label, and `blocked` for any other reason or none; and `ran`. The legal-token text `scenario` prints carries that precedence. Only `floor-message`, `named-gate` and `other-gate` show that the user-level file ran. Another hook source can block the same trigger (G5), so `summarize` reads O28(a) for a `blocked` cell not observable, "block of unknown origin". A Cursor live-gate cell reads not observable unless its CC1 control reads `named-gate`. An S7 cross-source cell whose deny side is the live gate reads not observable unless CC1's control and S5's `Bash` cell both read `named-gate`. `require-routing-read`'s `Agent` registration has no cell, because it denies only during a plan-review the kit never starts (row 64).
- **Merge** (S6, S7): a deny side against an other side giving an allow form, an ask form, `neutral`, `exit1` or, within one source, a timeout.
  - Within one native project source (S6): on preToolUse in both array orders, plus a beforeShellExecution deny against `allow`.
  - Across sources (S7): the live `require-code-review` gate on the live-gate `Bash` trigger in `scratch-b`, or a user-level native `deny` on a plain Shell call in `scratch`, against a native project entry, a Claude project entry or a Claude project local entry. Only the live-deny steps use `scratch-b`. A Stop `followup` also meets a `neutral` across the native user and Claude project sources, in both directions.

The harness registers `onFailure: "block"` only on PreToolUse entries (row 65), so S1 tests `failClosed: true` only on the four permission events. S10 and CC3 are out of scope.

| Scenario | Toggle | Runs | Observations |
|---|---|---|---|
| S1 | off | A zero-hook control step, then one `--step` per pair: `failClosed: false` for preToolUse, beforeShellExecution, beforeReadFile, subagentStart, beforeSubmitPrompt, postToolUse and stop, and `failClosed: true` for the first four. Each step registers only its own entry, with `--emit cell`, and runs a `none`, an `empty-object` and a `text` cell. The preToolUse and beforeShellExecution cells are Shell calls. | O19 |
| S2 | off | First the first-prompt cell, then deny shapes, reason nonces, the visibility nonces, `ask` per event, and stop follow-ups with `loop_limit` unset and set to 2 | O2, O29 |
| S3 | off | Observation entries on every event and tool; the matcher family; the argument, space-path and tilde entries; agent Shell legs; a multi-root workspace of both scratch repos; and a `user` step repeating the observation and O12 entries at user level | O1, O3, O12, O15, O21, O23 |
| S4 | off | Two conversations in one window on `scratch` and a third in a second window on `scratch-b`; sessionStart `env` at `--hold 0` and `--hold 2`; `detach`; the probe agent and a built-in subagent in parallel, one of them spawning a nested subagent; the probe agent, with `tools: Read`, asked for a Shell call | O4, O5, O6, O8 |
| S5 | on | The control cell; the imported mirror; the live-gate family; a Task whose subagent makes one Read of `README.md`, with dispatch text holding none of `NOOP_PHRASE_RE`'s phrases, beside a project-level native subagentStart observation entry; a user-level native sessionStart `env` entry, without the routed `CLAUDE_CONFIG_DIR` in a first conversation and with it in a second; the O9 checks; and, last, closing the second conversation, then the quit that ends the scenario, so a sessionEnd at either point lands on S5's entries | O9, O12, O15, O16, O18, O19, O21, O28 |
| S6 | off | The merge family's within-source arm, `updated-input`, the default-timeout, timeout-unit and spawn-failure cells, and a native `unread` cell | O7, O20, O22, O23 |
| S7 | on | The control cell; the mirror's PreToolUse entries giving `exit1`, `sleep`, `text`, `none` and `empty-object`, with and without `onFailure: "block"`; imported `claude-deny`, `exit2`, `claude-ask` and `claude-context` per event, and a `claude-block` Stop chain; the merge family's cross-source arm; one native and one imported entry at `--hold 2`; and imported `unread` | O2, O17, O19, O20, O22, O23 |
| S9 | off | A conversation started before `scenario S9` and then continued, and one resumed after a relaunch, each with a positive-control entry on another event; a conversation closed, and one left open when Cursor quits | O25, O27 |
| CC1 | n/a | Claude Code from a plain terminal under the Claude account CC1 and CC2 use, with bare `~/.claude` (row 61). A first step in `scratch-b` sends the live-gate family's four triggers as O28(a)'s Claude Code control. Then, in `scratch`, probe hooks in `.claude/settings.json` only, `env` canaries in `.claude/settings.json` and `.claude/settings.local.json`, and a canary the launching shell exports | O15, O28 |
| CC2 | n/a | CC1, launched from Cursor's integrated terminal | O15 |

CC1's and CC2's printed steps open with the engineer confirming, in Claude Code's own status view and without reporting the value, that the Claude account CC1 and CC2 use and bare `~/.claude` are active. They stop otherwise (row 61).

**Observations.** Each row's sub-questions are lettered, and `scenarios.json` cells name them by id, such as `O20.b`. `summarize` enumerates those ids, so no sub-question hides behind its row's id. The criteria in Phases 2 to 6 name the sub-questions that decided them. The hooks doc already answers O13 and O14 (row 46), so they take no probe and move to OD4. O10 and O11 are deferred with S10, and O24 and O26 are cut with S11 and S8 (rows 75 and 77). `scenarios.json` declares the events no cell can trigger (the MCP events, beforeMCPExecution among them, the Tab events, and preCompact) not observable, with that reason. It declares the harness matchers and events G1 says Cursor lacks (`Skill`, `ExitPlanMode`, `AskUserQuestion`, Notification and PostToolBatch), and the import's unmapped SubagentStart, as selecting no Cursor call, with G1 as the reason. Neither group's silence is flagged. It also declares O21(h)'s Grep and WebFetch arms of the PostToolUse mirror (`settings.json:529`) not observable, because S5 makes no Grep or WebFetch call, and OD4 already routes both tools to the engineer.

| Obs | Sub-questions | Scenario |
|---|---|---|
| O1 | (a) Key paths and types for every event the hooks doc lists, and per tool: Shell (plain, with a leading `cd`, and multi-line), Read, Write as a create and as an edit, Delete, Grep, Task, and WebFetch of one vendor-doc URL `scenarios.json` fixes. (b) Every tool name seen. (c) The value-shape booleans for `file_path`, the Delete path, `command`, `cwd`, `workspace_roots` and each id. (d) `workspace_roots` and `cwd` in a multi-root workspace. | S3 |
| O2 | (a) Which native outputs block: a Cursor deny, a Claude `hookSpecificOutput` deny, and exit 2. (b) Where do the agent and the human each see a deny's `agent_message` and `user_message` nonces, an `exit2` stderr nonce, a sessionStart or postToolUse `additional_context` nonce, and a `followup_message` nonce? (c) What does `ask` do on preToolUse, beforeShellExecution, beforeReadFile and subagentStart? (d) How many stop follow-ups run with `loop_limit` unset and set to 2? (e) Imported: where do a `claude-deny` reason nonce, an `exit2` stderr nonce and a `claude-context` nonce land, and what does a `claude-ask` resolve to? (f) How many continuations does an imported `claude-block` Stop chain get, and is `prompt_id` one equality class across it? | S2, S7 |
| O3 | (a) Does beforeShellExecution fire, with command and cwd, for main and subagent Shell calls? (b) Does any Shell call fire preToolUse(Shell) without beforeShellExecution, or the reverse? | S3 |
| O4 | (a) The ancestry of hooks and Shell calls in three concurrent conversations across two windows, and of subagent calls. (b) Is any ancestor unique to one conversation and alive at its last record, and how many hops above the hook is it? | S4 |
| O5 | At each `--hold` delay: (a) does a sessionStart `env` value reach the same conversation's later native hooks and Shell calls, main and subagent? (b) Does it differ across conversations? (c) Does it arrive before the first Shell call? | S4 |
| O6 | Does the `detach` child, in its own session and holding none of the hook's descriptors, outlive its sessionStart hook without holding the hook open? | S4 |
| O7 | (a) Does a preToolUse `updated_input` replace the executed Shell command? (b) Do sibling hooks see the rewritten command, by the sentinel boolean? | S6 |
| O8 | (a) What does subagentStart's payload hold, and what `subagent_type` does the probe agent report? (b) Per tool call, which field, listed or `unlisted`, identifies the calling subagent, with two parallel subagents and one nested? (c) Which subagentStop `status` values occur? (d) Does the probe agent's `tools: Read` stop its Shell call (G3)? | S4 |
| O9 | With the toggle on: (a) does the import read `~/.claude/CLAUDE.md`? (b) Does it read `~/.claude/rules/` with its `paths:` scoping? (c) Do the harness skills and agents appear, showing the import follows stow links? (d) Does it load `.claude/settings.local.json` as well as `.claude/settings.json`? (e) Does Cursor offer finer import toggles than the one? | S5 |
| O12 | (a) Does Cursor run `command` through a shell or as argv, tested with a quoted `'a\|b c'` argument and the space path? (b) Does it expand a `~/` command, native and imported? (c) Which cwd and PATH booleans does a hook get? (d) Which bash and `python3` does the shebang find, for native entries at project and user level and for imported ones? | S3, S5 |
| O15 | (a) Env names and the parent chain of: a native hook at project and user level, an imported hook, an agent Shell call, a Claude Code hook from a plain terminal, and one from Cursor's integrated terminal. (b) Is `CLAUDECODE` on Claude Code hook processes? (c) Do the canaries from the scratch repo's `.claude/settings.json` `env`, its `.claude/settings.local.json` `env`, and the launching shell reach Claude Code hooks? A nested `claude -p` from an agent Shell call is inferred from (a) and (c), not run. | S3, S5, CC1, CC2 |
| O16 | (a) The Claude-shaped payload each mirror registration receives per event and tool: key paths, types, tool names and O1's booleans, set against O1. (b) Does each carry every field row 12 lists, including `hook_event_name` and Stop's `prompt_id`, `permission_mode` and `last_assistant_message`? (c) In S5's Task cell, does the subagent's imported Read payload carry a field in one equality class with an id the native subagentStart entry records? | S5 |
| O17 | Does a mirror PreToolUse registration block on (a) exit 1, (b) a timeout, and (c) exit 0 with non-JSON stdout, each with `onFailure: "block"` and without it? | S7 |
| O18 | Does a user-level native sessionStart `env` value reach (a) imported probe runs, and (b) the live harness's hooks, in the same conversation, the latter shown by O28(c)? | S5 |
| O19 | (a) The native no-decision matrix: per S1 cell, did the call run and, for a Shell cell, did its approval prompt match the zero-hook control? (b) Does a mirror PreToolUse entry with `onFailure: "block"` that gives `none` or `empty-object` leave the call to the approval flow? (c) Does any silent mirror registration, on any event the harness registers, block a call or change a prompt? | S1, S5, S7 |
| O20 | (a) Within one native source, does a preToolUse deny beat an allow form, an ask form, `neutral`, `exit1` and a timeout, in both array orders, and a beforeShellExecution deny beat `allow`? (b) In the merge family's cross-source arm, does each deny side beat each other-side output? (c) Across sources, does a Stop follow-up survive the other source's `neutral`, in both directions? Together they show which of G5's two merge statements holds. | S6, S7 |
| O21 | What each event's matcher is tested against: (a) preToolUse (`Shell`, `shell`, `Sh`, `^Shell$`, `Write\|Delete`); (b) beforeShellExecution (`touch`, `^touch`, `TOUCH`); (c) subagentStart and subagentStop (the probe agent's name, `generalPurpose`); (d) stop (`Stop`) and beforeSubmitPrompt (`UserPromptSubmit`); (e) sessionStart (`startup`, `resume`, `startup\|clear\|compact\|resume`); (f) postToolUse and beforeReadFile (`Shell`, `Read`); (g) an absent matcher. (h) For each harness matcher string, which Cursor calls does its mirror registration select? | S3, S5 |
| O22 | (a) Do matching hooks run in parallel or serially, within a source (from the start times in S6's timeout merge cells) and across sources (from S7's two `--hold 2` entries)? (b) What are Cursor's default timeout and the units of `timeout`? (c) What does a timeout do under `failClosed` true and false? (d) What do a missing command path and a non-executable one do, under both? | S6, S7 |
| O23 | Does an `unread` entry raise an error or block on a large payload, (a) natively and (b) imported? The payloads are a postToolUse for a Shell command printing about 1 MiB, and the pasted 100 KiB prompt. O23(a)'s `unread` cells sit in S6 and S7, and S3 records only the payload sizes. | S3, S6, S7 |
| O25 | For a conversation started before its scenario was installed, and one resumed after a relaunch: (a) does sessionStart fire, given that the positive control fired? (b) Do earlier `env` values persist? | S9 |
| O27 | (a) Does sessionEnd fire per agent loop or at conversation close? (b) With which `reason` values? (c) Does it fire on a Cursor quit? | S9 |
| O28 | Does the import run the harness's own user-level `~/.claude/settings.json` through its stow link? (a) Per live-gate cell, does the named gate's own reason show, or a bash-floor message, another gate's reason, or no block, set against CC1's control for the same trigger? (b) Do `sessions/` and `session-end-records/` gain entries, and how many new entries match a recorded Cursor ancestor's pseudonym? (c) With the routed `CLAUDE_CONFIG_DIR`, do the harness's `sessions/` and `session-end-records/` entries land in `routed/` instead? | S5, CC1 |
| O29 | Is the first tool call of a conversation started right after a relaunch hooked? | S2 |

**Run order and recording.** This plan is the runbook, and `scenario` prints each scenario's own steps. The run goes in this order:

1. the session creates the source directory, and the `code-writer` dispatch writes the kit there and runs `selfcheck` (Critical files). The session then runs `/code-review` on the five kit files by path, a further dispatch fixes any finding, `selfcheck` passes after the last edit (row 69), and the session makes Scenario lint's comparisons;
2. the prerequisites. The engineer makes these checks in their own terminal and in Cursor's own settings:
   - Cursor is opened once and signed in (row 71);
   - whether a managed hook source, the system-level managed hooks file the fetched hooks doc names for macOS, or a plugin hook source is present;
   - whether the import toggle, the run mode, the sandbox, the allowlist or auto-run cannot be set for the run. A toggle that cannot be set either way stops the run before `init`, and the session asks the engineer how to go on, because it moves most of what the spike can decide. A run mode, sandbox, allowlist or auto-run that cannot be set means `init --approval-pinned`;
   - whether the launch method's environment carries `CLAUDE_CONFIG_DIR`. If it does, rows 61 and 70 conflict, so the run stops before `init` and the session asks the engineer;
   - the current value of every setting the run changes.

   The engineer also sets the scenario boundary at which the run stops (row 86).

   The session then asks for the run's one launch method (row 70), the Cursor and Claude Code versions, and the default models Cursor's agent and `claude` use (row 72), which the observations record, models named per row 89. The engineer turns the import toggle off for S1 and sets Allowlist mode with an empty allowlist and the sandbox off;
3. `init`, then S1's control and first step, then `summarize`. The run goes on only when `summary.md` shows a payload record, not a below-floor one, with key paths and that step's sentinels;
4. the rest of S1, then the remaining scenarios, CC1 and CC2 in any order, with CC1 before S5, so a trigger that does not deny under Claude Code is fixed before Cursor runs it. A sub-question of a finished scenario reading "no outcome recorded" sends the engineer back to it.
   - A fix to `probe.sh`, `probe.py` or the `bin/` vocabulary after `init` restarts the run from step 3, because every registration runs the `bin/` copies. The restart goes:
     1. the fix lands in the source directory and passes `selfcheck`;
     2. a fixed `probe.sh` or `probe.py` also passes `/code-review` on the changed file (row 69);
     3. the engineer quits Cursor, runs `teardown`, and deletes the two kit roots by hand, keeping the source directory;
     4. the engineer leaves the source directory in place, makes none of the setting reverts `teardown` prints, confirms both kit roots are gone, and sets S1's toggle state;
     5. step 3's `init` runs.
   - A fix to `probe-kit.py` or `scenarios.json` takes effect at the next `scenario`, once `selfcheck` passes again.
   - At the engineer's stop point the run ends, at a scenario boundary or inside a scenario (Verification 3). A stop mid-scenario gets a `run-stopped` note on that scenario's current cell;
5. once Verification 3 holds, its rerun-or-accept decisions on the live-gate cells included, the session writes the observations section and checks it against `summary.md` before step 6 deletes the source directory;
6. the engineer quits Cursor, runs the final `teardown`, deletes the folders it prints, and completes every revert it prints.

The session writes a `## Phase 1 observations` section above `## Approach` under this admission rule, which also governs every later result this plan admits:

1. Sources. The section takes only:
   - `summary.md`;
   - step 2's launch method, the Cursor and Claude Code versions and model names (row 89), and the CLI version the engineer reports before the first CLI run;
   - the engineer's on-screen observations of each cell: block text, prompts and agent replies;
   - the structure of Cursor's per-workspace hooks log: exit codes, durations and event order;
   - this repo's tracked hook scripts as the session read them;
   - the structure of read-only replays of those scripts on a synthetic copy of a scratch repo: exit codes and the line a script exited at, with no run-derived value;
   - doc quotes and relays: G5, G7 and G8, and any other doc claim whose row the text cites.
2. Placeholders. On-screen text enters only as a paraphrase, or as Cursor's own fixed message with every variable part replaced by a placeholder.
   - The rule covers a message in the Hooks output pane and a line of Cursor's hooks log file alike.
   - Cursor's fixed wording enters as written, and so do an exit code, a duration, and an event, trigger, matcher or field name the closed set below keeps.
   - Every other variable part becomes a placeholder: a path, a workspace or folder name, an id, a time of day, a PID, command text and a nonce among them.
3. The closed set. No path, user name, email, folder name, id, hash or payload value derived from the run, the user or the machine enters from any source. What is kept of the values the run supplies is a closed set:
   - the tool, event, field, status and enum names Cursor documents;
   - the environment-variable names and process basenames the kit's vocabulary lists;
   - the model names row 89 admits;
   - the Cursor and Claude Code versions reported at step 2, and the CLI version the engineer reports before the first CLI run;
   - this repo's tracked hook-script file names and line numbers, with the gate names and counts derived from them;
   - the kit's fixed record booleans and interpreter versions;
   - a tilde-rooted config location whose every segment the vendor's docs fix, such as `~/.cursor/`, which is not derived from the run, the user or the machine.

   Names this plan, this repo's tracked files or the vendor's docs fix, such as step ids, `summary.md` and `.cursor/hooks.json`, are public text the closed set does not govern. A value carrying a path, folder, user or id fragment derived from the run, the user or the machine is forbidden whatever its category.
4. The raw hooks log. Neither the session nor any subagent it dispatches reads it. Its facts enter by one of two routes:
   - the engineer reports its structure and transcribes a fixed message under the placeholder rule;
   - a filter prints only values parsed as integers, members of the closed event-name set, and Cursor's fixed wording with every other token replaced by a placeholder, never a line passed through.
5. The commit gate. The session does not commit a diff that touches the section until the engineer confirms in the session one of two arms:
   - the read arm: they read the staged text;
   - the scan arm: they accept a clean scan of the staged lines in place of that read. The scan is a pattern match the session runs over the lines added inside the observations section, and a hit stops the commit. Its patterns:
     - the engineer's name;
     - home paths, other than a tilde-rooted location whose every segment the vendor's docs fix;
     - host names and email-shaped strings;
     - repository or organization names;
     - `.com`;
     - hex runs, with a vendor version string carrying its build id exempt;
     - UUID shapes.

     The scan does not match internal tool or product names or structural fingerprints, which the closed set keeps out. The engineer's words were "you may treat the clean scan as the check" and "I'm not looking at the staged diff myself - that's impractical", said about an earlier diff. The session reads them as standing for this section, and the engineer may withdraw them.
6. The blocklist. On either arm the engineer also confirms that the run's host name, email address, any display name, workspace folder names and any repository or organization name seen on screen are in the private-projects blocklist the hook reads. That is the `private-projects.md` in the config directory of the session that commits, which replaces the default one when it exists.
   - The engineer's own name and user name stay out of the blocklist, because an entry for them would block the engineer's own home path.
   - On the scan arm the scan's engineer's-name and home-path patterns cover them, and on the read arm the engineer's read does.
   - The hook covers tracker IDs, the structural detectors `docs/private-project-redaction.md` lists under "The five structural detectors", and blocklist entries (r21). The engineer's read covers the rest. On the scan arm no read happens, so the closed set alone covers the rest.
7. Hand-off and cutoff.
   - `summary.md` is deleted at step 6, so what the Phase exit re-plan needs from it is written into the section first.
   - Managed hook sources are out of scope and unobserved, and the section lists them among what it did not observe.
   - An observation the run cannot answer, including every sub-question of a scenario left unrun at the stop point, is recorded with its reason (row 33).
   - The follow-up run (below) and OD9's checks may add run data in commits made before step 6. No commit after step 6 adds Phase 1 run data, because the engineer deletes the raw records then.
8. Phase 6 results. The acceptance run's results enter this plan under rules 2, 3, 5 and 6, with these differences (r3 ciso R8; r4 ciso R5):
   - Destination: a `### Phase 6 acceptance` subsection, the last one inside `## Phase 1 observations`, so rule 5's scan, which covers the lines added inside that section, covers every line they add.
   - The per-phase runs from Phase 3 on (Phases) enter no plan text. The next phase's PR body records them as pass or fail per step.
   - Sources: the engineer's on-screen observations, each acceptance step's pass or fail, the adapter's own `status` output and bounded log, which hold no payload value (requirement 19), and Cursor's hooks log only through rule 4's filter. Acceptance step 12's latency enters that way, as integer milliseconds.
   - The closed set adds the adapter's registration, mode and translation-class names, and integer counts and latencies.
   - The routed config dir's path never enters; rule 3's closed set admits no path derived from the machine.
   - No MCP server name enters but the scratch fixture's fixed name, `cursor-adapter-fixture` (acceptance step 7).
   - Rule 7's cutoff covers Phase 1's run only.

**Phase exit.** No Phase 2 file is created or edited until `/plan-review` is clean on this revision and OD9's C1, C2, C3, C5 and C7 results are in the observations section, because they select Repo resolution's Shell arm and Phase 2's fixtures. All five are in "OD9 check results" (r12; r14). This rule holds whatever the observations show. `require-plan-review.sh` enforces it only while the revised plan is uncommitted or modified (row 48), so the session holds the rule itself. After the run, the session:

1. asks OD4 and, when its trigger holds, OD6, each with the evidence;
2. re-dispatches `plan-architect` with `MODE=plan-sections` to plan Phases 2 to 6 from the observations section and the sketch file, writing the surviving requirements back into this plan (row 80);
3. runs `/plan-review` on the result (row 54).

The rules around those steps:

- Commit messages and the PR body describe the plan change only, never run data or facts about the run's Cursor setup, and `deny-private-project-refs` checks them as usual (row 93).
- Phase 1's PR carries only this plan, with its observations section and the reviewed post-spike revision (row 62).
- This revision is that re-plan, with OD7 to OD10 decided (row 145).
- OD9's checks run before Run order step 6, while the kit still exists, and each result enters the observations section before the first phase it gates (OD9).

**Follow-up run: the Cursor CLI as a second launcher.** The kit's `scenario <id> --launcher cli` runs a cell as a new `agent -p` invocation, and `desktop` stays the default. This runs before step 6, against the settings the IDE run left in place, so it needs no new `init`. The launcher change edited `probe.py` after `init`, which the restart rule in Run order step 4 would answer with a restart from step 3. The engineer copied the changed file into `bin/` and `cmp` showed it byte-identical to the kit's, which stands in for that restart (row 112). Records made before the change carry no launcher and read as desktop.
- Eligibility: 18 cells are marked not-cli in `scenarios.json`, each with a reason, because they need the desktop app or would make the agent fetch remote content under the CLI's `--force` flag. Cursor's CLI parameters page documents `--force` as "Force allow commands unless explicitly denied". Only `S4.subagents.parallel` of the manual cells runs under the CLI.
- Smoke test first: Cursor's docs do not say whether `agent -p` loads `~/.cursor/hooks.json`, the project hooks file or the Claude Code import. A silent CLI shows as a missing record, which is an observation, not a failure.
- Order: O20 first, because it decides OD6. Then O2.e and O2.f, O7.a and O7.b, O23.a and O23.b, O25, O27 and O28.b, and the S2, S3 and S4 smoke cells. A sub-question the CLI cannot answer runs under the desktop launcher.
- Recording: records, notes and sentinels carry the launcher. `summarize` judges each launcher on its own. The observations section takes a CLI result only from that per-launcher breakdown, labelled with the CLI version, and never reads it as an IDE answer unless a desktop cell agrees.
- Cleanup: before the first CLI run, the engineer reports the CLI's login state and which `~/.cursor` files exist, as the baseline. The final revert list restores that baseline, and `teardown`'s printed list does not cover it.

### Phases 2 to 6

The gitignored sketch file was this section's input (row 80). Its criteria are answered below and its surviving requirements live here, so a requirement number or criterion id this plan cites now refers to this section, and nothing reads the sketch file again.

**The choice of candidate (the sketch's Q1).** The observations reject import-only (candidate A) and select the native shim (candidate B). Per criterion:

- A1 fails: the import hands each copy Cursor's own payload, with `Shell` for `Bash` and no `permission_mode`, `prompt_id` or `last_assistant_message` (O16). A9 fails on the same record.
- A2 fails: `onFailure` has no effect on an imported entry, so a crash or a timeout fails open (O17).
- A3 fails: a sessionStart `env` value reached later hooks but no agent Shell call (O5), and the routed `sessions/` and `session-end-records/` stayed empty (O28.c).
- A4 holds: no silent imported registration blocked a call or changed a prompt (O19.b, O19.c).
- A5 takes OD6's outcome: an imported project-level deny beat every native project output, and the imported user-level direction was not observable (O20.b).
- A6 fails: no field attributes a subagent's tool call to that subagent (O8.b).
- A7: the import maps `Bash` to Shell and `Agent|Task` to Task, `Agent` alone selects nothing, and Delete, Grep, WebFetch, WebSearch and MCP reach no harness PreToolUse matcher (O21.h). OD4 sorts each.
- A8 fails: an imported deny's reason shows in chat, but an imported `ask` is not enforced (Auto-review).
- B1 and B7 hold: a matcher is a case-sensitive, unanchored regular expression on the tool name (O21), the command runs through a shell with its arguments intact, and the shebang finds bash 5.3 with jq and python3 on PATH (O12).
- B2 holds: `{}` never blocks on the permission events, with `failClosed` on or off (O19.a).
- B3 takes OD6's outcome: a native user-level deny beat every project-level and imported output, and a Stop follow-up survived the other source's `neutral` (O20.b, O20.c).
- B4 holds at project level: Cursor hook processes carry `CURSOR_VERSION` and no `CLAUDECODE`, and Claude Code hook processes carry `CLAUDECODE`, from Cursor's integrated terminal too (O15). The user-level reading waits on the acceptance run (row 41), and no guard relies on it (row 148).
- B5 is unmet, because O23 is partial (row 33).
- B6 holds for parallelism: hooks of one event start within about 1 ms, across sources too (O22.a). The budget is OD5's.
- I1 fails: no Cursor ancestor is unique to one conversation (O4).
- I2's parts:
  - O6 holds;
  - O5 fails for Shell calls;
  - O7 holds under the CLI, and in the IDE per OD9's C2 (r12).
- I3 is unmet: O25 and O27 are not observable, and a window close requests `sessionEnd` without running its hooks (O28.b).

**Translation.** Every generated entry runs `hook-shim.sh` with the harness script, the Claude event and the Claude matcher as its arguments. On each call the shim:

1. reads stdin into memory, never into a file, under an 8 MiB cap and a deadline below the entry's `timeout`, and validates every payload id it uses (requirements 10, 15). The cap is the probe's, above the largest observed payload of about 1.4 MB (Payload). Past the cap a fail-closed entry denies, naming the cap, and an informational entry prints `{}`, never a truncated run (requirement 37);
2. maps Cursor's event and tool to Claude's through the tool map `install` recorded from `tool-map.json`, never the live file, re-checks the Claude matcher, and prints `{}` when it does not match (requirement 7). On a fail-closed entry, a tool name that neither the recorded map nor a `cursor-tool` entry in the live `parity-exceptions.json` lists denies until someone classifies it (requirements 27, 37; Recovery). The tool map:
   - Shell becomes `Bash`.
   - Task becomes `Agent`, the name `require-routing-read` tests for (row 125).
   - Read and Grep keep their names.
   - A Write to an existing file becomes `Edit`, with the file's current content as `old_string` and the new content as `new_string` (rows 143, 144).
   - A Write that creates a file becomes `Write`.
   - Delete becomes `Bash` running `rm -- <quoted path>` (requirement 22).
   - WebFetch and WebSearch keep their names, so the matcher-less `classify` entry lets both through, as Claude Code runs them. That is classification only: a restricted subagent's marked call to either is still checked by requirement 8's restricted-agent check (Registration). No harness PreToolUse matcher selects either. WebFetch reaches only the PostToolUse `Bash|Read|WebFetch|Grep|Task` registration (`settings.json:559`), whose output replacement is dropped and counted (OD10). Cursor's spelling of both, and whether their calls reach preToolUse at all, are [unverified]: Payload's tool names hold neither, though O1's cell list included a WebFetch call (r21).
   - An `MCP:`-prefixed tool keeps its Cursor name, which no harness matcher selects (O21.h). It reaches only the MCP arms (Twins) and requirement 8's restricted-agent check. MCP names are user-defined, so `tool-map.json` holds them as one prefix class, `MCP:`, and the `classify` entry accepts any name that starts with it (requirement 37; r21);
3. builds the Claude payload from `field-table.json` (requirement 17):
   - `hook_event_name` in Claude's casing;
   - `session_id` from the call's session id: the payload's `parent_agent_id` when it carries one, which a subagent's calls do, otherwise its `conversation_id` (OD7; r17 be R1). Identity and routing states why;
   - an absolute `cwd`, the directory Repo resolution gives;
   - `tool_response` from `tool_output`, which the hooks doc types as a JSON-stringified string (row 156);
   - the attribution fields (Twins);
   - none of `workspace_roots`, `cursor_version`, `conversation_id` and `generation_id`, the keys requirement 38's predicate tests, so the predicate never matches a translated payload.

   A field Cursor lacks is synthesized only by a rule the table names, or left absent, never filled with an empty value (requirement 6);
4. runs the hook (r5 cd R4):
   - it changes to the resolved directory, and a directory it cannot enter takes requirement 3's unenterable-directory outcome (r21);
   - it removes `CLAUDECODE`, `CLAUDE_CODE_ENTRYPOINT` and `CLAUDE_PID` from the environment (requirement 15);
   - it sets `CLAUDE_CONFIG_DIR` to the routed config dir recorded at install;
   - from Phase 4 only, it sets `CLAUDE_CONFIG_ADAPTER_SESSION_ID` to the call's session id (step 3; r17 be R1);
   - it runs the unchanged script with the payload on a pipe as its stdin (requirement 15).

   The `cd` is there because Cursor runs user hooks from `~/.cursor/` and the plan-review gate resolves its repo from its own directory (row 133);
5. maps the result through `translate-output.jq`'s table, which is total over event, exit class and stdout class (requirements 3, 17):
   - Exit 0 with empty stdout, a no-decision JSON or an allow becomes `{}` (requirements 2, 3).
   - A parsed deny becomes Cursor's `deny`, carrying the gate's reason in `agent_message` and `user_message`.
   - Exit 2 maps per event, whatever stdout holds (rows 154, 157):
     - on preToolUse, beforeShellExecution and beforeReadFile, a deny carrying the hook's stderr;
     - on subagentStart, the same deny shape, which Cursor does not enforce, because no output blocked a subagent in Phase 1 (Decisions; r21);
     - on beforeSubmitPrompt, a block (`continue: false`) carrying it;
     - on postToolUse, the stderr as `additional_context`;
     - on Stop, the Stop twin's follow-up;
     - on sessionStart, sessionEnd, subagentStop, afterAgentResponse and preCompact, the informational catch-all below.
   - A parsed `ask` becomes Cursor's `ask` on beforeShellExecution, which Cursor honors (G2). On every other event it becomes a deny that tells the agent to ask in chat (row 36).
   - A context value goes to the event's channel, or to Deferred context. Until Phase 5b builds Deferred context, a value with no channel on its event is dropped and counted in the log, and `status` lists the count (r17 plat R10).
   - A Stop block goes to its Stop twin.
   - A parsed `decision: block` on exit 0 from `send-back-skipped-self-review`'s subagentStop registration becomes subagentStop's `followup_message` carrying its `reason`, only when `status` is `completed`, and `{}` otherwise (Twins; r21).
   - An `updatedToolOutput` on an `MCP:` tool becomes `updated_mcp_tool_output`. On any other tool it is dropped and counted (OD10).
   - Any other outcome on a fail-closed entry becomes a deny. These outcomes are a non-zero exit other than 2, a timeout, non-JSON stdout, and an output field the table does not carry.
   - That deny carries requirement 3's `agent_message` and `user_message`.
   - On an informational entry the same outcomes print `{}` and are logged. This is the catch-all for every event the bullets above do not name.

**Repo resolution.** The shim resolves a directory for each call and never decides whether it holds a repo. The unchanged hook decides that from the directory, as under Claude Code, so a hook that exits 0 outside a repo still does (rows 114, 150). Per event:

- Shell events take the pinned directory. These are preToolUse(Shell), beforeShellExecution and their postToolUse twins. The directory pin below makes the command start there (rows 136, 137, 230; r13):
  - the pin is the first workspace root, or `$HOME` when there is none. The agent's command text can choose it only through the exception below, and only among the working trees the engineer opened as roots (r17 ciso R1; r19 ciso R1, cd R3);
  - a repo-resolving gate therefore starts in one of those trees, and its own `cd` and `-C` handling evaluates any relocation the command holds;
  - the one exception is a command that starts with `cd <path> &&` whose path, after quote removal, meets all of these (r19 ciso R1, cd R3):
    - it is absolute;
    - it holds no `$`, `~` or backtick;
    - it exists;
    - it is a workspace root or lies inside one, by the file tools' containment test below on its `realpath` form.

    That path is the pin. Any other leading `cd` runs after the pin prefix and leaves the pin at the first root. The test runs no `git` and no other program, so no repository configuration is read (r19 ciso R4). The engineer chose this rule (row 292), which narrows row 286's, and the cost list states what it costs;
  - the payload `cwd` plays no part, because it was the empty string on every Shell record (C1);
  - nor does `tool_input.cwd`, the Shell tool's own directory input. An absent, `null` or empty one, and an absolute one whose `realpath` form is the pinned directory, change nothing. Any other value, a non-string one included, takes the pin entry's directory-input refusal. The check tests the value's type, not its truthiness, so `false`, `true`, an empty array, an object and a whitespace-only string are each refused (r21 be R11, sdet R5). The refusal is a deny with one fixed text, which echoes no part of the value: "The Shell tool's directory input must be empty. To work in a directory inside a workspace root, start the command with `cd <absolute path> &&`, as its only leading `cd`, naming a directory whose path holds no `$`, `~` or backtick and does not resolve outside the roots. If the call is refused again with the input empty, or the directory is outside every workspace root, stop and tell the engineer in chat." The shim sends this text inside requirement 3's common deny text, which adds the hook name, the outcome class and the Recovery section (r21). So the pin never silently overrides the input (r20 be R1; r21; row 323);
  - the result goes through `realpath`.
- File tools take the workspace root that contains the target. These are Write and the `Edit` built from it, Read, beforeReadFile, Grep's search path, and Delete as `rm`:
  - containment is a path-component prefix test on the target's `realpath -m` form, as `_lib_realpath_m` computes it: `realpath -m`, then `grealpath -m`, then a walk to the nearest existing ancestor when neither returns a resolved path (row 181; r10 be R4). The shim's lib copies all three branches;
  - nested roots take the longest match;
  - a target under no root takes the first root, as Claude Code runs a hook from its session's directory, and with no workspace root at all the target's nearest existing ancestor is the directory;
  - a file tool never denies for resolution. The exceptions are requirement 39's self-protection entry, which denies a target that is not absolute or that cannot be resolved (r7 be R1; r8 ciso R4), and requirement 3's deny for a resolved directory the shim cannot enter (r21).
- Every other event takes the first workspace root, or `$HOME` when there is none: Task, sessionStart, subagentStart, the stop and afterAgentResponse twins, sessionEnd, beforeSubmitPrompt and preCompact.

**The directory pin.** Cursor keeps the Shell tool's directory across calls (Run facts), and the payload `cwd` names none (C1, row 136), so the shim makes each agent command start in the directory it gives the hooks. An adapter-only, fail-closed preToolUse `Shell` entry in `pin` mode rewrites the command through `updated_input`, which C2 showed the IDE honors (row 137; r13):

- The rewrite puts the pin prefix ahead of the agent's command, which stays unchanged. The pin prefix is the line `builtin cd -- '<dir>' || exit 1` and a newline, and from Phase 4 Identity and routing's unset-and-export line on its own line ahead of it. `<dir>` is the pinned directory, single-quoted with each `'` written as `'\''`. This form is the session's choice (r18; Alternatives set aside).
- `builtin` makes the shell's own `cd` run, so a `cd` function or alias in the engineer's shell startup files cannot turn a failed `cd` into a success. A shell with no `builtin` fails that word, so `exit 1` runs. The `exit` stays plain, because `builtin exit` would not exit on such a shell (r19 sdet R2). The residuals, which the doc names:
  - a function named `builtin` or `exit`, which bash allows outside POSIX mode;
  - a zsh `chpwd` hook, which still runs after the `cd` and adds its output to every Shell result.
- A `cd` that fails ends the call's shell before any of the agent's text runs, whatever `||`, `;`, `}` or newline the text holds, because nothing groups the text. The agent's lines parse as they would with no adapter, so a trailing backslash or a `shopt -s extglob` line behaves as without it (r18).
- Recognition (r19 sdet R1, ciso R3, cd R2, cd R8):
  - the expected pin prefix for a received command is computed from the text after any first lines shaped as a pin prefix, which is the agent's original command, together with the payload, by the rule the pin entry used. So an exception call's own directory is expected, not the first root;
  - a command that starts with exactly that expected pin prefix is left unchanged, so a double fire (row 134) prefixes it once;
  - a command whose first lines are shaped as a pin prefix but differ from the expected one is agent text, and is prefixed again like any other (r17 ciso R2, be R8);
  - from Phase 4 the expected pin prefix holds the unset-and-export line, so a Phase 3 pin prefix is agent text to a Phase 4 shim. No conversation meets both forms, because the doc has Cursor quit around `install`.
- On every Shell event the shim strips only its own pin prefix from `tool_input.command` before translation, so each hook sees the agent's command, any pin-prefix-shaped text in it included (row 245).
- An adapter-only, fail-closed, matcher-less beforeShellExecution entry in `pin-check` mode denies a command that does not start with the expected pin prefix, naming the cause. C7 showed beforeShellExecution receives the rewritten command, so a Cursor build that stops applying `updated_input`, or a foreign entry whose rewrite wins, denies every Shell call rather than letting gates evaluate the pinned directory while the command runs elsewhere. `status` counts these denies by cause (r17 ciso R2, be R6, sdet R6). Its scope is the paired case Phase 1 observed, a beforeShellExecution after the same call's preToolUse(Shell). A Shell command that reaches beforeShellExecution with no preToolUse, such as one a UI action may start, would take this deny, and whether one exists is O3(b)'s open question (r21).
- A generated beforeShellExecution matcher is never anchored at the command's start, so it still selects a prefixed command, and the shim's re-check runs on the stripped one (requirement 7; r19 cd R2).
- The output carries `updated_input` and no `permission` field, so it is no explicit allow (requirement 2). C7 showed the IDE applies that output and the call still prompts (OD9, row 244; r14).
- `updated_input` copies the payload's whole `tool_input` and changes only `command`, because C2 and C7 changed only `command` and whether Cursor merges or replaces the object is unobserved (r19 be R5).
- One entry writes the call's one `updated_input`, because G5 relays that the last response wins for fields other than the decision and the two messages (r19 cd R7).
- It lands with Phase 3's first live entries, because every Shell gate entry Phase 3 installs resolves its directory from it. A gate that resolved the root while the command ran in Cursor's carried-over directory would evaluate the wrong repository.
- Phase 2 builds and tests the pin prefix, its stripping, the directory rule and the `pin-check` mode in the shim's lib, and its OD5 benchmark counts the pin and `pin-check` entries among the adapter-only entries. Both entries land together in Phase 3 (r17 ciso R2; r19 cd R2).
- The entry is fail-closed, so a Shell call the shim cannot pin never runs (requirement 3).

The cost, which `docs/cursor-adapter.md` states:

- A `cd` no longer carries over. The next call starts in the pinned directory, so an agent that changes directory in one call and acts in the next acts in the workspace root, or in the path its own leading `cd` names.
- The Shell tool's own directory input does not choose the directory either, by Repo resolution's directory-input refusal (r20 be R1).
- A misplaced write lands in that directory. A git write that lands in a main checkout under worktree enforcement meets `require-worktree-for-git-writes`'s deny (row 246).
- In a multi-root workspace, an agent working under a later root leads each command with `cd <absolute path> &&`, and the gates start there under the exception (r19 cd R4).
- A leading `cd` into a clean repository that is itself a workspace root starts the gates in that repository. A gate that resolves its repo once, as `require-code-review.sh:97-103` does, then evaluates it whatever the command does next, as under Claude Code from that directory (r17 ciso R1; r18).
- A leading `cd` into a sibling worktree that is not itself a root, or into the main checkout when only a worktree is a root, leaves the pin at the first root. The gates then evaluate the first root's tree while the command acts in the other, so a gate that resolves its repo once, such as `require-code-review.sh` or `require-ready-for-review.sh`, can check the wrong tree. This is the cost of row 292's rule, and it matches a Claude Code session started in the first root. The doc tells the engineer to open as a root each tree the agent works in (r19 ciso R1).
- Line numbers in a shell's error messages count the pin prefix's lines: one from Phase 3, two from Phase 4 (r19 plat R3).
- Every consumer of the command text sees the prefixed form, apart from preToolUse entries (C7, row 245). The doc names each (r17 be R5, ciso R5):
  - a consuming repo's or the engineer's own beforeShellExecution entry whose matcher is anchored at the command's start, or whose script tests the first word, stops matching, with no error. Such an entry is not supported with the Cursor adapter, and the doc says so (r18);
  - Cursor's approval prompt, its allowlist matcher and Auto-review's own approval read the pin prefix, and from Phase 4 its first line of `unset` and `export`. Acceptance step 16's cells 3, 4 and 8 check the effect (r19 be R4, plat R4).
- The pin entry and the `pin-check` entry are fail-closed and match every Shell call, so one fault in them denies every Shell call. The engineer's own terminal and `uninstall` are the way out, and the Recovery section says so (r17 be R6).

The pin removes the multi-root unknown state and its deny, and the deny for a relative `cwd` with no workspace root, because every Shell call starts in a directory the shim knows. The one resolution deny for a Shell call is on a fail-closed entry with no absolute directory to pin: the first workspace root is not absolute, or there is none and `$HOME` is unset or not absolute (r9 ciso R7; r13; r18). Its text names the remedy, opening the folder as a workspace root. Requirement 39's file-tool entry, the pin entry's `tool_input.cwd` directory-input refusal and the deny for a session id that fails the grammar, which names the id field that fails it and not the folder remedy (Identity and routing), and requirement 3's deny for a resolved directory the shim cannot enter are the only other resolution denies, as requirement 36 states (r19 cd R6; r21). Two cases are not resolution denies:
- the pin prefix's `exit 1`, with `cd`'s own message naming the directory, when the pinned directory stops being enterable after the call's hooks ran. Until then, a fail-closed Shell entry's own `cd` into that directory fails first and takes requirement 3's unenterable-directory deny (Translation step 4; r21);
- an informational entry prints `{}` where a fail-closed one denies.

C7 selected the pin above: the rewrite with no `permission` applied, and the call prompted (OD9; row 252; r14; r17 cd R9).

The taint rule is row 170's default when C1 and C2 both fail. C7 did not select it, so no phase builds it unless a Cursor update's re-run of acceptance step 16 (requirement 22) does (r14):

- A Shell call whose command text holds `cd`, `pushd`, `popd`, `source`, `.` or `eval` as a word taints its conversation (row 166).
  - These are the builtins that can change the shell's own directory. The match is a plain token match in the shim's lib, whose false positives cost only a leading `cd`.
  - Every later Shell call in a tainted conversation must start with a leading `cd` that meets the directory pin's exception rule above, which gives its own directory. Any other takes the resolution deny above, so the agent's text cannot choose a directory outside every workspace root (r17 ciso R1; r19 ciso R1, cd R7).
  - The taint records the tainting call's `tool_use_id`, so that call's sibling entries still resolve it from the state before it.

**Registration.** `cursor-adapter.py generate` emits one user-level `~/.cursor/hooks.json` entry per harness registration Cursor can fire, so Cursor's parallel runner (O22.a) runs the hooks as Claude Code's does. Generating every registration, not only the gates the audit found (row 123), means no gate's coverage depends on that audit being complete. `generate`, `check` and requirement 19's tamper comparison read the registrations from one file, the tracked `claude/.claude/settings.json` in the stowed checkout. `check` verifies that bare `~/.claude/settings.json` and the routed config dir's `settings.json` both resolve to that file, and reports either one that does not (r21). Each `onFailure: "block"` entry becomes `failClosed: true` with an explicit `timeout` the generator states, which Cursor reads in seconds (O22.b). None of the 34 `onFailure` registrations carries a `timeout` of its own (`settings.json`), and `generate` asserts that the shim's inner deadline is below the `timeout` of every fail-closed entry it emits.

- PreToolUse: `Bash` to preToolUse `Shell|Delete`; `Edit|Write|MultiEdit` to `Write`; `Read` to `Read`, plus a `beforeReadFile` entry (requirement 34); `Agent` and `Agent|Task` to `Task`.
- PostToolUse: the same tool map, on postToolUse.
- SessionStart: matcher-less sessionStart entries, because Cursor's sessionStart matcher selected every value tested on a new conversation (O21.e). The shim sets `source` to `startup`, or to `compact` under the compact twin.
- SubagentStart: matcher-less subagentStart, with the shim re-checking the agent name, because the probe agent's name matched and `generalPurpose` never did (O21.c).
- UserPromptSubmit to beforeSubmitPrompt, Stop to the Stop twins, and SubagentStop `code-writer` to the send-back twin (Twins). SessionEnd's one registration, `record-session-end`, is declined (requirement 38), so no sessionEnd entry exists (r21).
- Adapter-only entries: a fail-closed preToolUse `Shell` entry in `pin` mode and a fail-closed beforeShellExecution entry in `pin-check` mode (Repo resolution; r13; r17 ciso R2), a fail-closed, matcher-less preToolUse entry in `classify` mode (requirement 37), the Asks and self-protection entries (Twins), and a sessionStart `check --hook-env` entry (Installer). From Phase 5a the `classify` entry also carries attribution's duties: it runs requirement 8's restricted-agent check on every call that carries `parent_agent_id`, whatever its tool class, gives requirement 8's unattributed-subagent deny for a marked call with no entry under its parent (every tool except Read and Grep), is the only preToolUse entry that sees an MCP, WebFetch, WebSearch or Cursor-only call, and is the one entry that runs `gate-agent-tools.sh`. From Phase 5a the adapter-only entries also include attribution's matcher-less subagentStart and subagentStop entries, which keep the active-subagent set (Twins; r21).
- `parity-exceptions.json`, which Phase 2 creates, lists the rest. It is the one home of every exemption reason: generation and the gate matrix's meta-test both read it (requirement 40; r4 sdet R2). Each entry also carries one of requirement 27's kinds: the `EnterPlanMode` and `ScheduleWakeup` deny entries are `permission-deny`, an adapter gate a later phase builds is `adapter-gate`, a Cursor tool name the engineer classifies is `cursor-tool`, and every other entry below is `registration` (r21). Each entry carries one reason from a closed set:
  - `unsupported`, where Cursor lacks the tool, the event or the channel, row 115's split (r8 plat R5): ExitPlanMode (row 37); Skill, AskUserQuestion, Notification and PostToolBatch (G1); the `SubagentHandback` PreToolUse arm of `send-back-skipped-self-review`, because Cursor has no such tool; and the `EnterPlanMode` and `ScheduleWakeup` deny entries (Twins);
  - `declined`: `capture-session-id` and `record-session-end` (requirement 38);
  - `pending-phase`, naming the phase: each twin or adapter gate a later phase builds, the two worktree gates until Phase 4's identity, and the turn-gate and batch-gate registrations requirement 40 sends to Phase 5b's twins;
  - `withheld-premise`, naming the row or Twins bullet: each twin whose premise acceptance has not confirmed (rows 140, 141 and the Twins send-back bullet).

  Generation fails on any drop the file does not list.

Each entry's command is a fixed launch guard, then the recorded bash running the stable shim path. The launch guard, a name kept apart from the directory pin's pin prefix, tests that the shim is readable and the recorded bash executable (r19 cd R1). Otherwise it writes one line naming the Recovery section of `docs/cursor-adapter.md` to stderr and exits 127, because when the shim never runs Cursor shows only the exit code and the shell's stderr (O22; requirement 3). Phase 3's run checks that Cursor shows that line (Phases). If it does not, the Recovery section's first entry after its opening sentence is headed by the exit code 127, so the engineer can find it from the code alone (r21).

**Import copies (the sketch's Q2).** The import keeps running its own copy of each harness hook on Cursor's untranslated payload, beside the shim's entry, and no hook retires itself under Cursor (row 148). A copy cannot observe whether its shim twin runs, so retiring it would rest on proxies for that fact. Cursor lets any deny win over ask and allow, regardless of source (G5, O20.b), so:

- a copy that no-ops, as the 13 `Bash`-filtered gates do (row 123), costs only its run;
- a copy that denies, as the credential-file Read and no-op dispatch gates do (O28.a), repeats the shim's deny, and G5 says Cursor concatenates the two texts, which acceptance step 3 records;
- with the adapter uninstalled, the copies enforce what they enforce today.

Four rules keep a copy from deciding on state its twin does not share:

- `capture-session-id.sh` and `record-session-end.sh` decline an untranslated Cursor payload with exit 0 and no write, and get no shim entry (requirement 38). Their copies otherwise register a Cursor process in `sessions/` (O28.b), which breaks OD7(a)'s precondition (row 165).
- A worktree gate's copy hands its own payload to the resolver, which takes the call's session id from it by the rule the shim uses, `parent_agent_id` when present and `conversation_id` otherwise (Identity and routing). The copy then resolves the same identity as its twin and reads the same lock, a subagent's call included (r17 be R1). Without it, the decline above would leave the copy no identity, so in a repo with worktree enforcement on it would deny a Write into a linked worktree that its twin allows (`_lib.sh:3463-3466`).
- From Phase 4, a worktree gate's copy gives no explicit allow on an untranslated payload under a `Cursor` host, because `_lib_emit_allow_with_context` prints nothing for one (requirement 2).
- Phase 2's gate matrix runs every registration's script on its raw Cursor payload as the import does. It fails on an allow, on a deny the shim does not give, and on an unlisted write, network call or spawn (requirement 40). A further state writer it finds goes to the engineer as a plan revision before Phase 4, with the same decline proposed for it.

The residuals, which `docs/cursor-adapter.md` discloses:

- until Phase 4 merges, the two worktree gates' copies can emit Claude Code's explicit allow, as they do with no adapter installed (row 155);
- a copy reads bare `~/.claude`, so a marker-gated copy that reached its marker check would read unrouted state (row 41). The matrix's import arm catches that as a deny the shim does not give;
- the import arm assumes a copy runs outside any repo, as Cursor documents for user hooks (row 28) and the plan-review replay suggests (row 133). Phase 3's attended run observes it with acceptance step 3's plan-review cell, cell 4 (Phases; r4 be R6; r5 ciso R3). If a copy denies where its twin allows, the doc names the import toggle as the off-switch, which also drops the consuming repo's own `.claude/` config (row 34), and the finding goes to the engineer as a plan revision;
- a consuming repo's own hooks stay row 132's and OD8's.

**Identity and routing (Phase 4).** OD7 chose option (a), host-neutral identity under the shim, with the precedence reversed so that no Claude Code process can take it (r3 ciso R1). For agent Shell calls it rests on C2 (OD9). `_lib_parse_tool_input_or_deny` stays unchanged, because `test_lib.py` pins its jq field list (row 174; r4 be R1). No new variable carries an id between functions: the payload travels as an argument (r4 ciso R1). Every environment, global or optional-argument read Phase 4 adds is written `${NAME:-}`, because a pinned row runs the guard under `set -u` with `INPUT` unset (`test_lib_worktree_collision_guard.py:1169`; r6 be R2). The `_lib.sh` functions Phase 4 adds or changes:

- `_lib_payload_is_untranslated_cursor PAYLOAD` (new) returns 0 when PAYLOAD's top level carries a key of the Cursor-only set C5 fixed, `workspace_roots` and `cursor_version`, tested by presence, not value (rows 158, 228; r12). It reads only its argument and sets nothing, so the session hooks, which read stdin themselves, call it on their own `$INPUT`. It hands PAYLOAD to jq on stdin under a capped call, never as an argument. An empty PAYLOAD is no match and runs no jq. For a non-empty one, a missing jq, a jq error or a cap kill counts as a match, which leaves the allow emitter's suppression and the session hooks' decline to the host walk (r5 ciso R6; r7 be R2).
- `_lib_cursor_host_pid` (new) prints the caller's nearest live ancestor whose executable basename is exactly `Cursor`, the main process every IDE and desktop record descends from (C8, row 251; OD11(d); r15). The exact match passes over every `Cursor Helper` variant, whose nearest instance changed between events in one conversation (row 228). It returns 2 when it finds none, or meets a process named `claude` first. The name read (r17 plat R1, sdet R2):
  - each step reads `ps -o comm= -p <pid>` under a capped call. On macOS that prints the executable's full path, which can hold spaces, and a retitled helper prints a title instead;
  - the name is the whole output line, trailing whitespace trimmed, after its last `/`. The line is never split on whitespace, because a helper's path holds a `Cursor` component followed by a space;
  - the name is compared for equality with `Cursor` and with `claude`;
  - the PPID read stays the existing `ps -o ppid=` one, `_lib_resolve_claude_pid`'s (`_lib.sh:3284`). The match pins no executable path: a fake host needs a deliberately built process tree, and OD7's option (a) already accepts requirement 29's cooperative tier for this identity (r6 ciso R4). `check` names any live registered `sessions/` PID whose process name is not `claude`, because that stop name is what keeps the walk from passing a Claude Code process (row 177; r5 be R4).
- `_lib_adapter_session_pair [PAYLOAD]` (new) prints `<session id> <host pid>`, or returns 2:
  - it returns 2 when `CLAUDECODE` is set, which every Claude Code hook carries and the shim removes (rows 177, 178);
  - the host PID is `_lib_cursor_host_pid`'s. Being the caller's ancestor already ties it to the caller, so it needs no start-time record;
  - the session id is the call's session id from PAYLOAD when PAYLOAD is given and the predicate matches it, whatever the environment holds: PAYLOAD's `parent_agent_id` when it carries one, otherwise its `conversation_id`. Otherwise it is `CLAUDE_CONFIG_ADAPTER_SESSION_ID`, which the shim and the Shell rewrite export, computed by the same rule (r17 be R1);
  - that rule gives a subagent its parent's id, as Claude Code gives a subagent its parent's `session_id`, so the twin, the import copy and the Shell rewrite resolve one id for one call from the payload alone. A child payload names only its direct parent (C5), so a nested subagent's id is its direct parent's, not the root's. Its locks and markers then differ from the root's, which fails closed: its write into a worktree the root holds denies, and its markers are not read as the root's. Phase 1 saw one nested spawn, and the doc discloses it;
  - the session id must pass `_lib_valid_session_id_component`;
  - "carries one" has one definition, which the shim, this function and the Shell export all use (r19 be R1, sdet R7):
    - an absent, `null` or empty `parent_agent_id` is not carried, and the rule takes `conversation_id`;
    - any other value must be a string that passes the grammar. One that does not, a number or an 85-character id included, is not replaced by `conversation_id`: the shim denies on a fail-closed entry, naming the id field that fails the grammar, and this function returns 2, so the guard denies (r21);
  - C5's records show `parent_agent_id` on child Shell and Read calls only, and OD9's attribution follow-up on every child call class it ran, MCP excepted. A child call without it takes the child's own `conversation_id`, so its lock is not its parent's and the next write by either side under the other's lock denies, the fail-closed direction. Acceptance step 5 records which child tools carry it, and `field-table.json` marks the field `observed` only for those (r19 be R1; r21);
  - hooks that read the raw payload's `.session_id`, such as `log-reviewer-round.sh:73-79`, see a child's own id in the import's copy. The shim's twin hands them the call's session id (r19 be R1).
- `_lib_resolve_claude_pid [PAYLOAD]` (`_lib.sh:3105-3117`) returns `_lib_adapter_session_pair`'s result only after its walk finds no live registered ancestor. A Claude Code process has one once `capture-session-id` has run, and the pair refuses under `CLAUDECODE` otherwise, so Claude Code's resolution stays the same with adapter variables set (requirement 14).
- `_lib_worktree_collision_guard` (`_lib.sh:3444`) passes `${INPUT:-}` to the resolver. Its only callers are the two worktree gates, which parse their payload first (`require-worktree-for-file-writes.sh:67`, `require-worktree-for-git-writes.sh:200`), so `$INPUT` is the call's own payload. No other consumer passes one, so an `INPUT` a script inherits never reaches identity. When `CLAUDECODE` is unset, the guard then reads the resolved PID's entry through `_lib_registered_live_sid`. When that read finds no live registered entry, or fails, the guard passes `session-keyed` to the lock rule as a fifth argument, because only the adapter branch resolves an unregistered PID (requirement 38; r5 ciso R1, be R1). It decides that once and passes the same value at both lock-rule calls: the first read (`_lib.sh:3483`) and the reread after a failed acquire (`:3513`). A four-argument reread would hand a contending conversation on the same host PID the holder's lock (r6 be R1). Under `CLAUDECODE` the pair refuses, so only the registered branch can have resolved the caller, and the guard skips the read and calls the rule as today. On the `session-keyed` branch, the live-holder deny (`:3525`) names the holder as another Cursor conversation, which may be closed or archived, and tells the agent to ask the engineer in chat, never to unlock. The Recovery section gives the engineer `git worktree unlock <path>` once they confirm that conversation is closed, or quitting Cursor. The Claude Code text stays unchanged (requirement 14; r17 plat R3).
- `_lib_worktree_lock_is_mine` (`_lib.sh:3287-3296`) gains an optional fifth argument:
  - with `session-keyed`, it skips the own-PID return at `:3291`, so a lock is the caller's only through the session-id test after it. An old-format lock, which records no session id, is then never the caller's. Every conversation under one `Cursor` process shares the host PID (row 160; OD11(d)), so the PID cannot tell them apart;
  - with four arguments it is unchanged, so the rows that call it directly with an unregistered PID and expect 0 (`test_lib_worktree_collision_guard.py:1968`, `:1973`, `:2070`) stay green and unedited (row 186);
  - the signal is an argument from the caller, never a variable (row 176).
- `_lib_emit_allow_with_context` (`_lib.sh:461-468`), the only allow emitter in `hooks/` (row 175), prints nothing when the predicate matches `$INPUT` and `_lib_cursor_host_pid` finds a host, the session hooks' two-part test (requirement 38). A Claude Code payload that ever gains a predicate key therefore keeps its allow. The walk runs only on a fresh lock, where the emitter is called (`require-worktree-for-file-writes.sh:164-166`, `require-worktree-for-git-writes.sh:457-459`). A walk that fails lets the allow through, as the copies do before Phase 4 (row 155; r7 be R2). It reads `${INPUT:-}`, so a caller with no payload, such as `test_lib_emit.py`'s, gets today's output.

`capture-session-id.sh` and `record-session-end.sh` decline on the predicate and the host walk, and the shim never sets `CLAUDE_PID`, so no Cursor process becomes a registered live ancestor (requirement 38).

The consumers that resolve identity through these functions (row 159), each driven by Phase 4's identity test:

- `marker.sh:140`, `review-ledger.sh:187`, `_review-pr-lib.sh:764` and `handoff-record-conversion.sh:13`. `_review-pr-lib.sh`'s callers `review-pr-acquire.sh`, `review-pr-checkout.sh` and `review-pr-diff.sh` write through it (r17 be R3, sdet R1);
- `pr-diff-against-base.sh:126` and `:158`, through `_lib_resolve_session_id` (`_lib.sh:3130`);
- `_lib_worktree_collision_guard` (`_lib.sh:3463`).

Routing: the shim sets `CLAUDE_CONFIG_DIR` to the routed config dir for every hook it runs. From Phase 4 the directory pin's rewrite (Repo resolution) also unsets `CLAUDECODE` and exports the routed `CLAUDE_CONFIG_DIR` and `CLAUDE_CONFIG_ADAPTER_SESSION_ID`, the call's session id, on its first line, ahead of the agent's command (r13; r17 be R1). The unset is unconditional, because a Cursor started from a Claude Code terminal passes `CLAUDECODE` to its Shell calls, and the adapter pair refuses under it (r5 be R4). `marker.sh` and the other scripts an agent runs then resolve the state the hooks read. Under a sandboxed mode an agent Shell call cannot write outside the workspace (OD9's C3), so `install` writes a user-level `sandbox.json` that makes those scripts' directories in the routed config dir writable there, once Phase 5a's permissions gate exists (Installer; OD11(b); r15; r17 be R2).

`marker-clear-stale.py` needs no change. An active marker under this identity records the live host PID, which the sweep keeps as it keeps a Claude Code one, under the 60-minute idle rule Claude Code's bypass markers already follow (row 161). Requirement 30's no-idle-timer rule governs a conversation's liveness, not that marker rule.

A Cursor relaunch makes the start-time check reject any `sessions/` entry an earlier import copy wrote (row 164). The doc therefore tells the engineer to relaunch Cursor after installing Phase 4, and `check` reports a `sessions/` entry whose PID is a live Cursor process.

**Twins and adapter gates.** These cover OD4's enforcement-loss items. A twin is a generated entry running a shim mode the item names. An adapter gate is a Cursor-only script under `claude/.claude/scripts/cursor-adapter/` that reads Cursor's payload directly, except `gate-agent-tools.sh`, which takes the shim's payload (Attribution).

- Commit stall. An `afterAgentResponse` entry builds a Claude Stop payload with `last_assistant_message` from `text` and `prompt_id` from `generation_id`, runs `advance-past-commit-stall.sh`, and stores only that hook's block reason for the conversation.
  - A `stop` entry with `loop_limit: 1` emits the stored reason as `followup_message` only when `status` is `completed` and `loop_count` is 0, then clears it (requirement 16, row 163). An aborted or errored turn gets no follow-up, as it gets no Stop continuation under Claude Code.
  - Cursor submits a follow-up as the next user message (row 46), so the text carries the hook's name, and the agent cannot read it as the engineer's.
  - The twin rests on afterAgentResponse firing before stop in the same turn (row 139, OD9's C5), which C5 showed in the IDE (r12).
- Context-cap nudge. `nudge-handoff-near-context-cap`'s Stop registration takes the same pattern with the payload's `transcript_path`, if Cursor's transcript carries the measure the hook reads (row 140). `parity-exceptions.json` withholds the twin with that reason until acceptance step 6 confirms it. If the check fails, Cursor lacks the channel, and the item becomes a documented gap.
  - The twin runs the hook from its `stop` entry, only when `status` is `completed` and `loop_count` is 0. The hook writes a fired marker when it nudges (`nudge-handoff-near-context-cap.sh:740`) and stays silent at that estimate afterwards (`:620`, `:169`), so a run at afterAgentResponse would spend the nudge on a turn that is then aborted or continued (r10 plat R2, sdet R2).
  - The gate on `loop_count` 0 rests on Cursor resetting it at each user prompt, which OD9's run showed (row 225), so the twin stays withheld on row 140 alone (r11 be R4; r12).
  - The stop payload's own `transcript_path` wins. Only where stop lacks one does the twin use the path its afterAgentResponse entry stored for that turn, which each turn overwrites and which is cleared with the held text. C5 found a string `transcript_path` on every stop and afterAgentResponse record (r11 plat R5; r12).
  - The hook's Stop output is advisory `additionalContext`, not a block reason, and Cursor's `stop` has no context field. The `stop` entry therefore holds the text in Deferred context for the next postToolUse, rather than sending a `followup_message`, which would submit a user turn and cost one turn (r9 plat R4, sdet R1).
  - An empty hook output, as from a duplicated run the hook's own marker silences, leaves the held text unchanged (requirement 35).
- Compact boundary. A `preCompact` entry writes a per-conversation flag. The next sessionStart or postToolUse entry in that conversation runs `restore-authorization-boundary-on-compact.sh` with `source: compact`, delivers its text as `additional_context` (row 131), and clears the flag. It rests on preCompact firing with the conversation id (row 141), so `parity-exceptions.json` withholds it until acceptance step 6 confirms that. If the check fails, the item becomes a documented gap.
- Code-writer send-back. A matcher-less subagentStop entry, with the shim re-checking the agent name for `code-writer`, runs `send-back-skipped-self-review.sh` only when `status` is `completed`, as the context-cap twin runs its hook, and emits `{}` without running it on any other `status` (r21). The hook exits 0 on every path and sends the subagent back by printing a `decision: block` envelope on stdout, as `advance-past-commit-stall.sh` does. The twin maps that parsed envelope to subagentStop's `followup_message` carrying its `reason`, which Cursor consumes only when `status` is `completed` (row 163). The hook's `SubagentHandback` arm is `unsupported` (Registration).
  - Three premises are [unverified], so `parity-exceptions.json` withholds the twin until acceptance step 6's trigger 7 confirms them. If the check fails, the item becomes a documented gap.
    - The hook finds its self-review evidence in what Cursor's subagentStop gives it. It needs an `agent_id` that passes `_lib_valid_session_id_component` and a Claude-format `agent_transcript_path` that is an existing file. The Payload records name no value for either. C5 records `subagent_id` as 85 characters failing the grammar. `child_conversation_id` matches the child conversation ids, which pass it, on 13 of the 19 child records. The subagentStop record holds no transcript path.
    - The hook reads the writer's report from `last_assistant_message` (`evaluate-code-writer-self-review.py`, `_read_report`) and parses its self-review ledger from it. Phase 1's subagentStop keys hold no message field (Payload), and requirement 6 bars an empty fill. An absent field reads as an empty report, which holds no ledger, so the verdict would not reflect what the writer reported. With no Cursor field that carries the report, the twin stays withheld (r21).
    - The follow-up sends the subagent back.
  - Attribution's subagentStop entry, which runs in parallel with the twin (O22.a), removes the writer from its parent's active set on the same stop. Whether Cursor fires subagentStart again for the continuation is [unverified]. If it does not, the continuation's marked calls meet requirement 8's unattributed-subagent deny while no other subagent of that parent is active, which trigger 7 records (r21).
- Deferred context (requirement 33). A context value from an event Cursor gives no context channel (beforeSubmitPrompt, subagentStart, preToolUse and stop, the last for the context-cap twin; row 47) is held for the conversation and delivered on its next postToolUse. The worktree gates' context goes to the same call's postToolUse, matched by `tool_use_id`.
- Asks (row 36).
  - Each `permissions.ask` `Bash` entry becomes a beforeShellExecution `ask`, which Cursor honors (G2), with a matcher never anchored at the command's start (requirement 7; r13).
  - `ask-review-permissions`, `ask-new-dependency-disclosure` and the `Edit(//**/.claude/settings*.json)` entry sit on the write tools (row 129), where Cursor does not enforce `ask`, so each becomes a deny that tells the agent to ask in chat.
  - `ask-new-dependency-disclosure`'s deny also tells the engineer, in both fields (requirement 3; r13), to add the dependency by editing the manifest themselves, because the deny has no approval state and repeats on every retry (r3 be R9).
  - Self-protection joins the set as an ask-in-chat deny on the write tools, with no Shell entry (requirement 39).
- Attribution (requirement 8). subagentStart and subagentStop entries keep an active-subagent set per direct parent, from `subagent_id` and `subagent_type` (row 142; r21):
  - the set keys on subagentStart's `conversation_id`, which is the direct parent's, a nested spawn's included (OD9's attribution follow-up). subagentStop removes its entry;
  - subagentStop is meant to locate the set by its `parent_agent_id` and the entry by its `subagent_id`. The records support only that `parent_agent_id` is on 10 of 10 stop records and that a stop's `conversation_id` differs from its `child_conversation_id` (Payload). That a stop's `parent_agent_id` equals the start's `conversation_id`, and that a stop carries the start's `subagent_id`, are [unverified], a nested spawn's included, because the follow-up shows only that start and stop counts matched per route. Acceptance step 8's observation 7 checks the removal;
  - a call that carries `parent_agent_id` is a subagent's, and looks up the set under that id. The shim runs each agent-keyed hook once per entry there, with that agent's `agent_type` and `agent_id`, and the call is denied if any run denies;
  - a call that carries no `parent_agent_id` is the main agent's and is never attribution-checked, because main-agent calls never carry it (row 138; OD9's attribution follow-up);
  - an entry stays until its subagentStop, or until `check` once the conversation's recorded host has exited (requirement 30). No other route removes it. A stale restricted entry only over-restricts later subagents under the same parent, never the main agent;
  - a deny from a run as an active agent names that agent's type from the allowlist table, and says that if no such subagent is running, the entry is stale and the engineer starts a new conversation (Recovery);
  - a subagentStart payload that lacks `parent_agent_id` is logged, and `status` and `check` report it, as a sign that Cursor changed the subagentStart payload. That report does not watch the field on tool calls, which the rule's lookup rests on. Acceptance step 8's marks observations watch it, and they join requirement 22's Cursor-update checklist;
  - whether a child's MCP, WebFetch, WebSearch and Cursor-only tool calls carry `parent_agent_id`, and whether its MCP, WebFetch and WebSearch calls reach preToolUse, is [unverified], because OD9's attribution follow-up ran Write, Delete, Shell, Task, Read and Grep only. Acceptance step 8 observes a restricted subagent's MCP call, WebFetch call and WebSearch call in Phase 5a's run, after its merge. A call that fails either premise goes to the engineer under the outcome the bullet of Acceptance step 8 that begins "A call in 6" states;
  - the agent-keyed hooks are `deny-reviewer-tree-mutation`, `deny-plan-writer-off-plan-writes`, `enforce-marker-script-shape` and `gate-agent-tools.sh`. Row 124 covers the other three, and Phase 5a creates `gate-agent-tools.sh`. `gate-agent-tools.sh` is handed the shim's Claude-shaped payload from Translation step 3, with the active-set entry's `agent_type` and `agent_id` added and the Cursor tool name verbatim for a name with no Claude equivalent. The other three run on their own twin entries.
- Credential exposure (requirement 32). `gate-credential-exposure.sh` blocks, before the call, each route Cursor gives a pre-call event for, and redacts MCP output through the one replacement channel the hooks doc allows. The other output channels are OD10's.
- Permission entries (requirements 4, 5). `gate-settings-permissions.sh` carries the 17 `Bash` and 13 `Read` deny entries, and denies any `marker.sh` or `review-ledger.sh` call that is not an exact `permissions.allow` shape. The two tool-name deny entries, `EnterPlanMode` and `ScheduleWakeup`, are parity exceptions, because Cursor sends neither tool (rows 129, 153). Requirement 37's unknown-tool deny covers a Cursor tool nobody has classified.

**Agents, rules and instructions.** `generate` writes these (r5 cd R11):

- one `~/.cursor/agents/<name>.md` per harness agent, which wins over the import's same-named copy (row 30);
- each agent's `tools:` list into `gate-agent-tools.sh`'s table, because Cursor's agent files have no `tools` field (G3);
- each agent's `model` from `model-map.json`, which maps each harness alias to a Cursor model name as the picker shows it (row 89), with `inherit` for an alias the map lacks;
- the user-level `CLAUDE.md` and rules, as `alwaysApply` and `globs` files under `~/.cursor/rules/` (row 32, unverified until acceptance), because the agents' self-reports say the import reads neither (O9);
- `templates/claude-vocabulary.mdc`, which maps Claude Code's tool and skill vocabulary to Cursor's.

`generate` fails, naming the file, on an agent or rule file it cannot parse, so a parse failure never reads as an agent or rule the harness dropped (r7 plat R6; r8 plat R7). `install` completes generation of its whole output set, agent and rule parsing included, before its first write, so a parse failure is a refusal with zero writes (r8 be R1). The entry comparison (requirement 19) reads the registrations only, so a parse failure is its own finding in `check` and `status` and does not stop the comparison. `uninstall` does not depend on `generate` succeeding (Installer; r9 be R1).

`install --acceptance-nonce <value>` appends to the first generated rule of each kind a line telling the agent to end its reply with `<value>` followed by the rule's kind, for acceptance step 15. The lines live only in generated files, so a plain `install` drops them and `uninstall` removes them (r5 plat R6).

**Installer.** `cursor-adapter.py` has `generate`, `install`, `uninstall`, `check` and `status`.

- `install` takes the routed config dir as a machine-local argument (rows 7, 8). It refuses a second install with a different config dir, because `~/.cursor/hooks.json` is machine-wide and one install routes every Cursor session, so `uninstall` comes first.
- It writes only the generator's fixed output set and offers a merge for the engineer's own `~/.cursor/hooks.json` (requirements 11, 18). The set spans two roots: `~/.cursor/`, and the routed config dir, where `install` creates the state dir and, from Phase 5a, the sandbox grant's listed directories (r21). The set includes three files under `~/.cursor/cursor-adapter/`:
  - `install.json`, the install record the shim reads: the routed config dir, the recorded tool paths, the contract version, and the content of `tool-map.json` at install, which the shim maps and classifies from (Translation step 2; r21);
  - `hooks.json.pre-install`, the engineer's `hooks.json` bytes before the first install, or a record that none existed;
  - `manifest.json`, the path and hash of each other file the install wrote, which `uninstall` checks before it removes one. It never names a write target, and requirement 11 bounds the delete candidates it nominates.
- From Phase 4, the generator can also emit a user-level `~/.cursor/sandbox.json`, so an agent Shell call under a sandboxed mode can write the state the gates read (OD11(b); r15). Phase 4 builds it, and Phase 5a's install first writes it (r17 be R2):
  - the grant declares `gate-settings-permissions.sh`'s entry as its prerequisite (requirement 12), because Auto-Review (with Sandbox) gives no prompt for a `marker.sh` shape that gate does not yet deny. `status` lists the grant as pending until then;
  - while it is pending, from Phase 4's merge to Phase 5a's (r19 be R2; r20):
    - an agent `marker.sh` write fails under Auto-Review (with Sandbox) with "Operation not permitted" (C3), so a marker-gated commit denies in that mode;
    - Phase 4's doc says so, and names Allowlist with the sandbox off as the mode for marker-gated work until Phase 5a;
    - the workspace `sandbox.json` report and its Recovery entry wait for Phase 5a;
    - the window before this one, from Phase 3's merge to Phase 4's, is wider, and the partial-install bullet below states it (r20 plat R1);
  - it lists under `additionalReadwritePaths` the directories in the routed config dir that the agent-run scripts write. Those scripts are the identity consumers Identity and routing lists, `marker.sh` and the `/review-pr` scripts among them. It never lists the routed config dir itself. The list is one literal in `cursor-adapter.py`, which requirement 18's behavioral test checks (r17 sdet R1, be R3);
  - `handoff-record-conversion.sh` appends to `.handoff-nudge.log` directly in the routed config dir (`:20-22`), which no directory entry covers. It is left out by name: under a sandboxed mode that telemetry append fails and the script exits non-zero, and the doc says so (r17 sdet R1, be R3);
  - the listed directories (r17 plat R6, ciso R3; r19 cd R9):
    - `install` creates one that does not exist, because whether the sandbox honors an absent path is unobserved;
    - `install` refuses one that is, or lies under, a symlink below the routed config dir;
    - `uninstall` leaves them, because they hold harness state Claude Code shares;
  - the merge keeps every key and path of the engineer's own file. A live file that does not parse, is not an object, or whose `additionalReadwritePaths` is not a list of strings makes `install` refuse with zero writes, naming the file;
  - when no file exists, `install` writes `{"additionalReadwritePaths": [...]}`, the one key C6's file held (r17 be R4, plat R6);
  - ownership: the install's record of the exact paths it added, the listed paths the live file did not already hold. `uninstall` removes those and no others, and requirement 19's comparison reads the same record. A listed path the engineer's file already held is kept, never recorded, and printed by `install` as already present (r17 plat R4, sdet R4, be R4, ciso R6);
  - the record is durable before the file changes (r19 plat R1, sdet R4):
    - an install about to add paths writes, at step 1, a pending record of them, durably by temp file, `fsync` and rename. The install that first writes the grant also writes the pre-image, `~/.cursor/cursor-adapter/sandbox.json.pre-install`, the same way;
    - step 5's manifest promotes the pending record into `manifest.json`, and a promoted record is what "a completed grant install" means;
    - while no completed grant install is recorded, a re-run `install` reads the pending record and keeps the pre-image, rather than retaking either from a file it may already have widened. `uninstall` reads the pending record as it reads the promoted one;
    - Phase 4's installs, with the grant pending, write neither the pre-image nor the record;
  - the write order: after step 4's `hooks.json`, `install` creates the listed directories, then writes the merged `sandbox.json` by the same re-read and atomic rename step 4 uses, then step 5's manifest. A crash then never leaves the grant without the adapter's entries (r17 plat R6, cd R2);
  - `uninstall`'s step 1 handles `sandbox.json` first, then `hooks.json`, for the same reason. It removes the recorded paths, pending or promoted, and restores the pre-image bytes when the rest parses equal to them. Where the pre-image records that none existed, it deletes the file when the rest equals `{"additionalReadwritePaths": []}`. Its step 4 removes that pre-image (r17 plat R6, be R4);
  - `uninstall` on a `sandbox.json` that does not parse or has the wrong shape names it, keeps its pre-image and manifest record so a later `uninstall` can finish, and runs every other step. The Recovery section's manual fallback names `sandbox.json.pre-install` beside `hooks.json.pre-install` (r17 be R4);
  - no workspace-level `sandbox.json` is ever written (row 2).
- `install` first generates its whole output set in memory, the merged `hooks.json` included (Agents, rules and instructions; r8 be R1; r9 be R1; r11 cd R9). It refuses with zero writes, naming the file, on:
  - an agent or rule file it cannot parse;
  - a live `hooks.json` that does not parse;
  - a live `hooks.json` whose `hooks` member is not an object of event arrays.

  The merge keeps every other top-level member as it stands (r10 be R1). A missing `hooks.json` is named, and `install` writes `{"version": 1, "hooks": {...}}`, the hooks doc's required form (`hooks.md:705` in the saved copy; r11 be R3). It then writes in this order (r4 plat R1):
  1. the pre-image file, written only when `hooks.json` holds no adapter-owned entry, replacing any pre-image an interrupted `uninstall` left (r5 plat R3; r6 cd R3). It is written durably, by temp file, `fsync` and rename, before any byte of `hooks.json` changes. Replacing one takes the print set below (r9 ciso R1; r11 cd R10);
  2. `install.json`, and the state dir, created 0700, so requirement 39's identity check has that protected path to compare from the first call (r11 ciso R2);
  3. the agents, rules and other generated files:
     - then it removes each prior manifest candidate whose source agent or rule is absent from the harness, by `uninstall` step 2's rule. A candidate that rule keeps stays in the new manifest (r6 plat R1; r7 cd R4, plat R6);
  4. the merged `hooks.json`, by atomic rename, after it re-reads the live file and finds it byte-equal to what the merge was computed from. A difference aborts before the rename, naming the file, in a state the next `uninstall` completes (r10 be R1);
  5. `manifest.json`, last.
- An entry is adapter-owned when its command parses in the generated command grammar: the fixed launch guard, a recorded bash path, the stable stow path of `hook-shim.sh`, and the shim's known arguments, whatever contract version or recorded bash path it carries. This bullet is the one home of the ownership rule, and requirements 13 and 18 and M51 defer to it (r9 ciso R2). A version bump's or a bash change's `install` therefore replaces the old entries, and `uninstall` removes every one. The rule needs no marker field that the vocabulary-pinned schema would reject (r5 plat R6).
  - An entry that holds the shim path but does not parse in the grammar is foreign. The comparison names it, and `install` and `uninstall` keep it (r8 ciso R1).
  - An element that is not an object with a string `command`, such as a `type: "prompt"` entry (`hooks.md:209-216` in the saved copy), is non-owned and kept (r11 be R3).
- Before they replace or remove anything, `install` and `uninstall` print to the terminal, from `hooks.json` as it stood before the merge, every entry whose evidence the step would otherwise erase. This list is the one statement of that print set, and requirement 19's print tests derive from it (r9 ciso R1, ciso R3; r10 ciso R2, sdet R3):
  - each foreign entry;
  - each duplicated owned entry;
  - each owned entry whose matcher, `failClosed`, `timeout`, key set or recorded bash path differs from what `generate` emits now, apart from the contract-version token and the prior install record's bash path;
  - each non-owned entry the existing pre-image does not hold (r11 cd R2):
    - printed by `install` when it is about to replace the pre-image;
    - printed by `uninstall` when it skips its step-1 restore, before its step 4 deletes the pre-image.
- Nothing outside that list prints: an entry that differs only by the contract-version token or the prior record's bash path, an intact entry, and a non-owned entry the pre-image holds (r11 sdet R4).
- The print shows each entry's event key, its position and each of its fields by name (r10 cd R9, ciso R3; r11 ciso R4, sdet R3):
  - the event key and each field's JSON value are bounded to 200 characters each, counted after escaping, so a differing field prints whatever the home path's length. Both numbers in this list are the plan's choice;
  - a cut value ends with a marker giving its full length;
  - every byte outside printable ASCII is shown as an escape, which covers C0 controls, DEL, C1 controls and bidi overrides;
  - at most 20 entries print, filled in the order of the four classes above, foreign entries first, and each class's remaining count follows.
- `uninstall` refuses with zero writes, naming the file, when the live `hooks.json` does not parse or its `hooks` member has an unexpected shape, so it keeps the record that holds the routed config dir. The Recovery section's manual fallback covers that case (r10 be R1). A missing `hooks.json` gives step 1 nothing to remove, and `uninstall` names it and runs steps 2 to 4 (r11 be R3).
- `uninstall` runs in reverse, so a crash at any point leaves a state the next `uninstall` completes:
  1. it removes the adapter-owned entries, and restores the pre-image bytes when the rest of `hooks.json` parses equal to them. Where the pre-image records that none existed, it deletes `hooks.json` when the rest, with empty event arrays dropped, equals `{"version": 1, "hooks": {}}` (r11 be R3);
  2. for each generated file and manifest candidate (r6 plat R1; r7 cd R6):
     - it removes one that requirement 11 accepts and whose content matches the manifest's hash or what `generate` emits now. When any source fails to parse, `generate` emits nothing, so every candidate is matched by the manifest's hash alone (r9 be R1; r10 be R1; r11 cd R3);
     - steps 3 and 4 still run in that case;
     - it names any other and keeps it;
     - it counts an already-absent candidate as removed;
  3. it removes the runtime state, found through `install.json`'s routed config dir, only when that dir passes requirement 11's config-dir checks and its `cursor-adapter-state` is a real directory, not a symlink. Otherwise it names the state and skips it (r6 plat R6);
  4. it removes `install.json`, then the pre-image and the manifest last.
- With `install.json` missing, unreadable or malformed (requirement 3; r7 cd R5):
  - `uninstall` still runs steps 1, 2 and 4, and names the runtime state it could not locate, because only the record holds the routed config dir (r5 plat R3; r6 plat R6);
  - the shim gives requirement 3's install-record outcome.
- The shim finds the record at its fixed path under `$HOME`. A test therefore runs the shim under a temporary home passed to the subprocess. No variable can point a real shim at another record (r4 sdet R9, plat R7; r5 cd R10).
- The Recovery section of `docs/cursor-adapter.md`, which every shim deny names (requirement 3), gives (r5 plat R6, ciso R6, be R3):
  - first, the sentence the deny text also carries: "The engineer, not the agent, follows the Recovery section. An agent runs no command from that section, retries only where this deny's own text says to, and otherwise stops and reports this deny in chat." (requirement 3; r21);
  - the log path and `uninstall`;
  - the manual fallback for when `cursor-adapter.py` cannot run: copy `hooks.json.pre-install`'s bytes over `~/.cursor/hooks.json`, or delete that file when the record says none existed;
  - relaunching Cursor from the Dock when `check --hook-env` reports `CLAUDECODE` set, because a Cursor started from a Claude Code terminal makes every import copy refuse the adapter pair and deny in linked worktrees;
  - what a missing-required-key deny on every call of one tool class means: Cursor's payload lacks a `doc-only` path (requirement 6);
  - requirement 37's unclassified-tool deny (r21):
    - the meaning: Cursor sent a tool name that neither the recorded tool map nor a `parity-exceptions.json` `cursor-tool` entry lists, such as a new or renamed tool, or WebFetch or WebSearch under a spelling the files do not hold;
    - the way out: the engineer classifies the name as a plan revision to those files in the stowed checkout (r21):
      - a name added to `parity-exceptions.json` as a `cursor-tool` entry, with reason `unsupported` or `declined`, attaches no gate, and the shim reads that file on each call, so the deny lifts live (requirement 27);
      - a name added to `tool-map.json`, or a remapped one, takes effect only after `install`, which records the map and regenerates the entries it feeds. Until then the shim keeps the recorded map, and `check` reports the drift;
  - requirement 3's stale-install deny, after a pull changed the contract version: the engineer quits Cursor, re-runs `install` and relaunches (r21);
  - requirement 3's unenterable-directory deny, from Phase 2 (r21):
    - the meaning: a workspace root was removed, renamed or made unsearchable while the conversation stayed open, and every fail-closed call that resolves to it denies, with text naming the directory;
    - the way out: the engineer restores the folder or its permissions, or starts a new conversation on a folder that exists. The engineer's own terminal and `uninstall` also work;
  - Repo resolution's directory-input refusal on every Shell call (r20 be R1; r20):
    - the symptom: calls whose agent set no directory are refused too;
    - the meaning: a Cursor build now fills the Shell tool's directory input itself;
    - the way out: the engineer's own terminal and `uninstall`. The finding goes to the engineer as a plan revision;
  - from Phase 3, a deny in a Cursor CLI session beside the installed adapter: expected, and `uninstall` is the way out (CLI coexistence, below). The Phase 4 re-plan replaces this entry with its rule (r21);
  - from Phase 3, when the `pin-check` entry lands, a `pin-check` deny on every Shell call, which means Cursor no longer applies the pin's rewrite: the engineer's own terminal and `uninstall` are the way out (be R6; r21);
  - from Phase 4, these further cases (r17):
    - a worktree lock held under the running `Cursor` process by a conversation the engineer has closed or archived: `git worktree unlock <path>` once they confirm it is closed, or quitting Cursor (plat R3);
    - the sessionStart entry reporting no `Cursor` host: a Cursor update changed the main process, and identity has stopped resolving (plat R2);
    - a marker write the sandbox denies under Auto-Review (with Sandbox): before Phase 5a, the grant is not yet written, so marker-gated work uses Allowlist with the sandbox off. From Phase 5a, a workspace `.cursor/sandbox.json` the sessionStart entry counted may be overriding the adapter's file. The engineer removes or narrows that workspace file, or works in Allowlist with the sandbox off (plat R7; r19 be R2; r21);
    - from Phase 5a, a subagent's deny from a check run as an active subagent when no such subagent is running: no subagentStop arrived for an earlier subagent of the same parent, so that parent's active set holds a stale entry, which only later subagents under that parent meet (requirement 8). The way out is a new conversation, whose set is empty, not a resumed one. Quitting Cursor and running `check` removes the stale set (r21);
    - the manual fallback for `sandbox.json`: copy `sandbox.json.pre-install`'s bytes over it, or delete it when the record says none existed (be R4).
- CLI coexistence, which the Phase 4 re-plan designs (r21). Row 313 records the engineer's requirement: "engineers should be able to alternate between both if they so choose, and to have agents running on both simultaneously". This block states what the design must do and what it starts from. A detailed rule was written here and withdrawn, because each review round found new seams in its added specification. The engineer chose this shape [engineer-verified: "Shrink to requirement + C9, design in Phase 4 (Recommended)"] (row 322):
  - the gate: until the Phase 4 re-plan states the rule, a Cursor CLI session beside an installed adapter meets the adapter's denies, and the way out is `uninstall`. So the requirement is unmet in that window, and Phase 4 does not merge before the re-plan states the rule;
  - C9's results (OD9), the re-plan's input. C9 was one `agent -p` run launched from an external terminal:
    - the CLI loads a user-level hooks file;
    - a CLI hook's ancestry is the hook script, then the `agent` binary, then the launching shell, with no process whose name begins `Cursor`;
    - the CLI payload has `cursor_version` (its value not compared) and `workspace_roots`, carries a `session_id` equal to `conversation_id` and an empty Shell `cwd`, and carries `is_background_agent` on sessionStart;
    - not tested: a CLI Shell session's `sessions/` registration, the CLI's file-tool target forms, hot reload, an interactive (non-`-p`) CLI session, and a CLI launched from Cursor's integrated terminal;
  - the candidate selector: the nearest ancestor's basename is `agent` or `cursor-agent`, and no `Cursor`-prefixed process is in the chain. The adapter's entry-command processes sit between the hook and `agent` [unverified], so the selector must skip them;
  - attribution under the CLI: requirement 8 never checks a call that carries no `parent_agent_id`, so a CLI main agent's calls meet no attribution deny, if they carry none as the IDE's never do [unverified]. The CLI fires no subagentStart in any `agent -p` cell (the Phase 1 follow-up), so a CLI subagent's marked calls meet requirement 8's unattributed-subagent deny. Whether a CLI subagent's calls carry `parent_agent_id` is [unverified], because the Phase 1 follow-up records only that they fired `preToolUse`, and it is an input to the Phase 4 re-plan (r21);
  - the open question the reviews raised: a stand-down, an identity-keyed entry declining to enforce a call (row 313), is per entry and origin, not per cause, so for a CLI call it would also stand down the worktree gates' main-checkout deny and their collision guard. Whether to narrow it by cause is the re-plan's call;
  - the cooperative tier applies to the Phase 4 re-plan of this rule (row 321).
- The doc tells the engineer to quit Cursor and end every Cursor CLI session before `install` and `uninstall`, and to relaunch after, because user-level hot reload is untested (Out of scope). A CLI session left open across either command meets whatever the half-changed files give (r20; r21).
- Runtime state lives in `<routed config dir>/cursor-adapter-state/`, a real directory that `.gitignore` lists, because a stow-folded parent could otherwise stage it (`CLAUDE.md`, the `>>` footgun).
- After checking each floor, `install` records the `command -v` path of bash, jq, git, `ps`, `sha256sum`, `timeout` or `gtimeout`, and `gh` when present. `sha256sum` is on the list because `README.md` and `install.sh` require it and the hash helpers in `_lib.sh` and `marker.sh` call it by bare name (r21). Generated commands name the recorded bash, and the shim's prologue sets PATH from the record (requirement 15).
- `install` records python3 as the `sys.executable` it resolves to when run from `~/.cursor/`, because a pyenv or asdf shim picks its version by directory. Generated commands name that python3 (r5 cd R10).
- The sessionStart `check --hook-env` entry is informational and runs once per conversation (r7 cd R2, plat R4; r8 cd R7). Its 10-second `timeout` is the plan's choice, matching the harness's own sessionStart precedent (`settings.json:178`). It does these in order:
  1. re-tests python3's 3.11 floor, jq, `sha256sum` and `timeout -k` under Cursor's own hook environment, and reports a failure through `additional_context` and the log (r3 plat R3);
  2. reports, by name only, whether `CLAUDECODE` or `CLAUDE_CODE_ENTRYPOINT` is set there;
  3. gives requirement 5's run-mode line (row 197; r13), whole through `additional_context`, and as its closed label in the log line's run-mode field (requirement 15). From Phase 4 it also gives two reports, each by label and count only, and each in its own field of requirement 15's log-field table, within that table's 1024-byte bound (OD11(b); r15; r17 plat R2):
     - the host walk's result, as one of the tokens `found`, `not-found` and `claude-first`, which fit the log grammar `[0-9A-Za-z._:+-]`. `status` flags `not-found`;
     - a change in the payload's `cursor_version`, reported once, which names requirement 22's Cursor-update checklist. The state dir holds one file per seen version, created exclusively, so two concurrent conversations report it once. A version outside the grammar `[0-9A-Za-z.+-]`, up to 64 characters, is recorded and reported as `other` (r19 sdet R8);
  4. rotates the log and sweeps first-call marks older than 7 days (requirement 15; r10 plat R3);
  5. runs requirement 19's tamper comparison, which requirement 39 relies on (r6 ciso R1);
  6. writes one log line, success included, in the log's field grammar, which lands in the current file because the rotation came first (requirement 19; r7 plat R1; r9 plat R2);
  7. creates the conversation's first-call mark with an exclusive create, so a mark the entry made means its comparison and log line finished. A kill before this step leaves the shim to win the create and write a first line, which requirement 19 counts (r10 sdet R1; r11 ciso R5, plat R2, sdet R5);
  8. from Phase 5a, last, the workspace `sandbox.json` report, through `additional_context` only, so a stalled read loses only this report, never the log line or the mark (r19 plat R5). Its rules (r17 ciso R4, plat R7):
     - it reads, for the payload's `workspace_roots` in order, a `.cursor/sandbox.json` that is a regular file of at most 64 KiB, under one total deadline of 3 seconds, the plan's choice, well under the entry's `timeout`. Roots it has not reached by then are reported as `unfinished`;
     - it counts the files present, those that widen the sandbox and those that hold an unrecognized key. A file widens when an `additionalReadwritePaths` entry, resolved to a canonical absolute path with ASCII case folded, lies outside every workspace root and outside `/tmp` and `$TMPDIR`, which Cursor's sandbox already leaves writable (C6). An exemption that is unset, empty, relative or an ancestor of `$HOME`, `~/.cursor` or the routed config dir is dropped, not applied. "Ancestor of" includes equality, so a root equal to `$HOME`, `~/.cursor` or the routed config dir, or containing one of them, is dropped. An exemption is also dropped when `$HOME`, `~/.cursor` or the routed config dir is itself unset, empty, relative or unresolvable, because the test cannot be run (r21). The workspace roots, `/tmp` and `$TMPDIR` are resolved by the same function as the entries, so an entry spelled through a symlink such as macOS's `/tmp` or `/var` matches. Containment is a path-component prefix test, so a sibling of a root and `/tmpx` lie outside. An entry equal to a root, `/tmp` or `$TMPDIR` itself lies inside, unless that exemption is dropped. So `~/.cursor`, the routed config dir, `$HOME` and a tool directory the hooks run from all count. When a root's `.cursor/sandbox.json` is the same file as the adapter's own user-level `~/.cursor/sandbox.json`, as for a root equal to `$HOME`, the report skips it by file identity and counts it as neither present nor widening (r19 ciso R5; r20; r21 plat R8, be R14). A file that holds any key other than `additionalReadwritePaths` counts as an `unrecognized key`, a count of its own apart from widening, because C6 lists the other keys from the docs and no cell observed what they do. `additionalReadonlyPaths`, `disableTmpWrite` and `networkPolicy` count as an `unrecognized key` and not as widening, and their effect is unobserved (r20 ciso R2; r21 be R14; r21). The residual of the case fold: on a case-sensitive volume a case variant of a root or of `/tmp` counts as inside, though it can name a different directory the grant would make writable. The report is report-only (row 323);
     - an entry that is not absolute, holds `~`, `$` or a `..` part, or cannot be resolved counts as widening, as a file past the cap or that does not parse does. So do a top-level value that is not an object, an `additionalReadwritePaths` that is not a list, and a list holding a non-string, which never count as zero (r19 ciso R5; r21);
     - the doc states that the count covers the opened roots only, and that Cursor's lookup rule, its path expansion and whether a workspace list replaces or adds to the user-level one are unobserved.
- The sessionStart entry's reports reach the engineer only if the agent repeats them. `install` and `check`, which the engineer runs, print the same tamper and run-mode findings to the terminal, and the doc says so (r7 ciso R2).
- The reports from the sessionStart entry, `status` and `check` name registration labels, event names and counts, never an entry's text. A hook the engineer adds to `hooks.json` after install shows in them as a non-owned entry, and the doc says so (r8 ciso R5).
  - Every string they print that comes from `hooks.json` is a registration label `generate` emits or an event name from requirement 20's pinned vocabulary. Any other string is counted as `other` (r9 ciso R3).
  - After the engineer's first own hook, a planted entry under the same event differs from it only by the non-owned count, so the doc tells the engineer to note that count in `status` after adding a hook (r9 ciso R9).
- The run-mode line reaches the channels requirement 5 states (r8 ciso R6; r9 ciso R5; r13).
- Phase 3's PR records the sessionStart entry's wall and CPU time once, as requirement 22's PR-record rule does for CI time (r7 plat R4).
- `install` refuses when a prerequisite of an entry it would emit is absent (requirement 12), and, in Phase 3, under the zero-write refusal below (r21 be R9). From Phase 4 on, an engineer can keep a partial install. Each twin or adapter gate a later phase builds sits in `parity-exceptions.json` as pending that phase, and `status` lists the pending ones. The import's copies keep enforcing what they enforce today, so a partial install loses nothing the import gives (row 148).
- Phase 3's install is for Phase 3's acceptance run only, an exception the engineer accepted to row 313's requirement for that window (r20 plat R1; r20):
  - Phase 3's `install` refuses with zero writes unless given `--acceptance-only`, and with it records `"acceptance_only": true` in `install.json`. Phase 4's `install` writes the record without it;
  - while the record holds it, `status`, `check` and the sessionStart entry's run-mode line each print `Phase 3 acceptance-only: marker-gated gates unsupported until Phase 4`, and the sessionStart log line's run-mode field holds `unreadable+acceptance-only` (requirement 15). Phase 3's `TestRunModeWarning` asserts the refusal, the record and each channel's line. The refusal row snapshots the routed dir, `~/.cursor` and the state dir before and after, and asserts each byte-identical, so zero writes is shown by the trees and not by the exit code. Phase 4's `TestRunModeWarning` row runs `install` over a record holding `"acceptance_only": true`, and asserts the record it writes lacks the key and that `status`, `check` and the sessionStart line then print no acceptance-only line and the log's run-mode field is back to `unreadable` (r21 sdet R7);
  - that no Cursor-side path writes a marker or an active-bypass file until Phase 4 routes `marker.sh` and gives it an identity is [unverified]. The import's `capture-session-id.sh` copy registers a Cursor process in `sessions/` (O28.b), and no Phase 1 cell ran `marker.sh` from an agent Shell call (r21):
    - if no such path exists, in every run mode the entries for `require-code-review`, `require-plan-review`, `require-ready-for-review`, `require-respond-pr` and `require-memory-skill` deny each operation their gates release only on such a file;
    - if one does, an agent's `marker.sh` can write those files in the window, so those gates can release, and `gate-settings-permissions.sh`, which checks `marker.sh` shapes, does not exist until Phase 5a;
  - a Cursor CLI session beside a Phase 3 install meets those denies too, and CLI coexistence waits on the Phase 4 re-plan (CLI coexistence, above; r21);
  - the way out of either is `uninstall`, which the engineer runs when the run ends;
  - this 3-to-4 window comes before the 4-to-5a one the sandbox grant's pending bullet states, where only Auto-Review (with Sandbox) fails.
- `generate --print-entry <registration> --shim <path>` prints that registration's entries to stdout and writes nothing, for acceptance steps 4 and 6. It renders a withheld twin's entries too, because it reads no `withheld-premise` entry (r5 plat R2).
- After a `git pull`, `check` compares the installed entries with what `generate` emits now, and README's "Updating after git pull" gains that step. `check` also compares the content `install.json` recorded for `tool-map.json` with the live file and reports a difference, because a remap that leaves the generated matchers unchanged does not show in the entries comparison (r21).
- Each conversation has one first-call mark, which the sessionStart entry and the shim both try to create with an exclusive create (r9 plat R5; r10 sdet R1, plat R3). Its path is `<state dir>/first-call/<call's session id>`, Translation step 3's id, so a subagent's calls find their parent's mark (r19 be R6):
  - when the sessionStart entry's create wins, the shim writes no first line, and the entry's full tamper comparison, which finished before the create, covers that conversation;
  - when the shim's create wins, as for a conversation with no sessionStart or a first call that beats its check, the shim writes a first line and makes the same comparison once, against a digest of the registrations `generate` read. On a mismatch it queues a warning into Deferred context (r3 be R5; r5 cd R10);
  - the sessionStart entry sweeps marks older than 7 days, the plan's bound on state growth. A live conversation whose mark it sweeps writes one shim first line at its next call, which requirement 19 names as a known trip.

**Phases.** Each phase is its own PR, from a fresh branch once the one before it merges. Phase 5 is three PRs, 5a, 5b and 5c, in that order, because each extends the shim's lib and the generator the one before it touched. Each dispatch is one `code-writer` run, in sequence. Critical files names each dispatch's files, and Verification 6 its checks. Each phase's dispatch owns its section's test rows and message texts, under that phase's code review (r21).

- Phase 2, the translation core: the tool map, the field table, `parity-exceptions.json`, the two translators, the shim and its sourceable lib, and the gate matrix (requirement 40), tested without Cursor. It writes nothing under `~/.cursor/`, and it starts only once the OD9 results Run order and recording's Phase exit paragraph names are in the observations section (r13).
- Phase 3: the generator and the installer, whose first live entries include the directory pin's (Repo resolution; r13). `docs/cursor-adapter.md` starts here, with the first user-facing command.
- Phase 4: identity and routing, and the user-level `sandbox.json`. OD11(b), OD11(d) and OD11(e) are decided (r15; r16).
- Phase 5a: the adapter gates and attribution, after OD9's C4. Phase 5b: the twins. Phase 5c: the agents, rules and instructions, and step 6's withheld-twin outcomes in `parity-exceptions.json` (r5 plat R2).
- Phase 6: the engineer's acceptance run (below) and the doc's completion.

The attended run per phase (row 171) happens after that phase merges and `git pull` updates the stowed checkout, because generated entries name stable stow paths, never a worktree (requirement 11). The next phase's branch starts only once the run passes, and a failure gets a fix PR first (r4 plat R2). Each run takes these acceptance steps:

- Phase 3: step 2's triggers for the classes it covers, step 3's cells 1, 3, 4 and 5 and cell 2's control, step 4's shim-missing case and case 7 (r21), step 10's cell 6, step 13's round trip on the engineer's own `hooks.json`, step 14's triggers 3 and 4, and step 16. Every `install` in this run takes `--acceptance-only`, because Phase 3's `install` refuses without it (Installer's Phase 3 acceptance-only rule). Step 14's "plain `install`" means an `install` without `--acceptance-nonce`, not one without `--acceptance-only` (r5 be R2, plat R1, ciso R4; r6 ciso R1, ciso R3; r9 ciso R5, sdet R2; r13);
- Phase 4: steps 1 and 5, step 3's cell 2, step 3's cell 3 again, and step 16 again, for the rewrite's first line (r17 sdet R3);
- Phase 5a: steps 7, 8 and 9, step 10's cells 1, 7 and 8, the sandbox grant's first install, and step 14's triggers 1, 2 and 5 (OD11(b); r15; r17 be R2);
- Phase 5b: step 6;
- Phase 5c: step 15.

Reverts (requirement 12):

- A phase is reverted only in reverse phase order, after `uninstall`.
- The one exception is a Claude Code regression from Phase 4. No later adapter phase edits `_lib.sh`, `capture-session-id.sh` or `record-session-end.sh`, so after `uninstall` a fix PR reverse-applies Phase 4's merge diff to those three files. Unrelated PRs edit `_lib.sh` too, so the fix PR never restores a whole file from an older base. Its diff against main touches only lines Phase 4 added or changed, and the PR lists any other changed line of the three files as a failure of the revert (r19 be R3). Phases 5a to 5c stay in place. The fix PR's other files (r17 be R7):
  - it removes `test_lib_cursor_identity.py`, whose expected-different cases test only the restored functions' Phase 4 branches;
  - it removes only Phase 4's rows from the gate-matrix file, so later phases' rows stay;
  - it sets the two worktree gates back to `pending-phase` in `parity-exceptions.json`, so `install` emits no twin whose identity the restored `_lib.sh` cannot resolve. A `generate` run over the reverted tree, in that PR, shows no worktree-gate entry. Cursor then runs the import's copies of those two gates alone, as with no adapter, which the gate matrix's import arm already tests (r4 plat R3). Phase 4's run takes acceptance step 1 only after merge, so this revert is the lever for a regression that step finds, and the doc says so (r5 plat R6).

**Acceptance (Phase 6).** The run is attended, on scratch repos placed as Phase 1 placed them, with the adapter installed from the stowed checkout, so the shim and the import's copies load the same `_lib.sh` (requirement 21). Two rules carry over from Phase 1:

- every trigger is inert when its deny is missed, as Phase 1's live-gate triggers were. A trigger drawn from the gate matrix carries the matrix's `inert` flag (requirement 40);
- every prompt carries Phase 1's stop rule (Scenarios).

Results enter this plan under the admission rule's Phase 6 clause (Run order). The per-phase runs take the steps Phases assigns them. Each step names what it observes, its pass condition and its control:

1. Claude Code regression.
   - Observe: CC1's live-gate triggers, from a plain terminal and from Cursor's integrated terminal, and the `sessions/` and `session-end-records/` entries a Claude Code session's start and end write.
   - Pass: each gate denies with its own reason, and both entries land for the Claude Code session.
   - Control: the same run with the adapter uninstalled. Once, a probe hook in the Claude Code session records its stdin's kind, a regular file, a FIFO, a socket or a character device, by bash's `-f`, `-p`, `-S` and `-c` tests on `/dev/stdin`, which settles row 207 (r9 sdet R8; r10 sdet R5).
2. Cursor gates, one trigger per translation class from the gate matrix's trigger table: Shell, Delete, Write as a create, Write as an edit, Read on both events, Grep and Task.
   - Pass: each class's gate denies with its own label, and a repeated identical denied call is denied again (the deny-then-repeat check, Decisions above).
   - Control: the same triggers with the adapter uninstalled, where the Shell-class trigger runs and the Read- and Task-class triggers deny (O28.a).
3. Import copies, with the adapter routed to a scratch config dir, not bare `~/.claude`, so a copy's state and its twin's can differ. Each cell with its pass condition:
   1. A credential-file Read trigger and a `Bash`-gate trigger. Pass: both are denied, and the shim's log line for each call records the twin's deny. The number of deny texts shown is recorded, because G5's concatenation is documented, not observed (r4 plat R4).
   2. The `sessions/` entries in bare `~/.claude` and in the routed dir. Pass: no entry names a live Cursor process.
   3. A Write into a linked worktree of a scratch repo with worktree enforcement on and an untracked plan, so plan-review's twin denies. Pass: the call is denied with plan-review's text. The file-writes copy finds its repo from the target path (`require-worktree-for-file-writes.sh:124`), so it can reach its allow from outside any repo, and the cell pits that allow against the twin's deny (r5 ciso R3).
   4. A Write in a scratch repo whose plan `/plan-review` passed in a Claude Code session run on the routed scratch config dir, so its marker exists there and not in bare `~/.claude`. No marker is written by hand. Pass: the Write is not denied. A deny there means the plan-review copy ran inside the repo and read bare `~/.claude`, which takes Import copies' off-switch route.
   5. Under Auto-review, an inert command with two scratch project-level entries: a preToolUse entry that gives an explicit allow for it, and a beforeShellExecution entry that asks for it (r5 cd R7; r6 ciso R3; r7 cd R10):
      - Pass: the result, a prompt or none, is recorded as whether an allow from one source suppresses another source's `ask` (row 183).
      - It needs no adapter, so Phase 3's run takes it.
      - That no copy's allow reaches Cursor is requirement 2's tests' to show, not this cell's.
   - Control (r5 plat R1):
     - after `uninstall`, cell 1's Read trigger shows one deny text and its `Bash` trigger none, and cell 3's Write runs;
     - cell 5's control is the same command with only the ask entry, which prompts with Cursor's "Hook requested approval" label;
     - cell 2's control is its listing in Phase 3's run, where the import copies' entries name a live Cursor process (O28.b);
     - cell 4's control is the same Write in a scratch repo whose plan has no marker, which plan-review's twin denies.
4. Failure, one forced case per class of requirement 3.
   - Setup: project-level scratch entries that `generate --print-entry` renders against a scratch copy of the shim, placed as Phase 1 placed its probes (M25).
   - Observe, one call per case:
     1. the shim missing;
     2. the shim unreadable;
     3. a hook killed by Cursor's timeout;
     4. a hook script missing;
     5. a hook exiting 1;
     6. a hook exiting 2;
     7. the shim process itself exiting 1, by a scratch entry with `failClosed: true` whose command is `exit 1` (r21).
   - Pass: each call blocks, and no output auto-approves a call. Cases 1 and 2 show the launch guard's line naming the Recovery section, and case 3 shows Cursor's own text. Case 7 blocks. A case 7 that does not block goes to the engineer as a plan revision, and the doc states the observed result. A Cursor timeout kill of a `failClosed` entry blocked in Phase 1 (Phase 1 observations, "Hook execution contract"), so the doc claims no fail-open for a signal death that no case observed (r21).
   - Control: each entry with its fault removed lets the call proceed. Case 7's control is a `failClosed: true` entry whose command prints `{}`, because an exit 0 with empty output blocks under `failClosed` (Phase 1 observations, "Hook execution contract"; r21).
5. Identity, per OD7 and OD11(d).
   - Observe: the markers and worktree locks of two conversations in one window and one in a second window, and `marker.sh` run from an agent Shell call under Allowlist with the sandbox off, once by the main agent and once by a subagent. Step 10's cell 1 runs the same write under Auto-Review (with Sandbox). Then, with one conversation's lock held, the engineer closes its window with Cursor still running, and later quits and relaunches Cursor (r15).
   - Pass: each conversation keeps its own markers and locks, and the `marker.sh` write lands under the routed dir with its conversation's session id. The closed window's lock stays held, and `check` names it, until Cursor quits. After the relaunch it reads dead (OD11(d); r15). A subagent's `marker.sh` write lands under its parent's session id (r17 be R1).
   - Also observed, because C5's records show `parent_agent_id` on child Shell and Read calls only (r19 be R1):
     - a subagent's Write into a linked worktree its parent holds. Pass: it is not denied;
     - a subagent's `git commit` in a worktree the subagent's own Write locked. Pass: it is not denied;
     - the shim's log records whether each child Write and Delete carried `parent_agent_id`. Where one did not, the call took the child's own id and denied against its parent's lock, the fail-closed outcome Identity and routing states.
   - Control: a Claude Code session in Cursor's integrated terminal, with the adapter installed, resolves its own `claude` PID.
6. Twins.
   - Setup: generation drops a withheld twin, so triggers 2, 6 and 7 run on project-level scratch entries that `generate --print-entry` renders for the compact, context-cap and send-back twins, placed as step 4 places its entries (r5 plat R2). The scratch entries apply only while a twin is withheld, and a run after Phase 5c enables one uses its generated entry (r6 plat R5).
   - Routed dir: triggers 2 and 6 run under the routed config dir `install.json` records, and each records that its hook's kill-switch file is absent there, `.authorization-boundary-disabled` for trigger 2 and `.handoff-nudge-disabled` for trigger 6 (`config-keys.psv:106`, `:99`). Either file makes its hook exit silently (r9 plat R1). Trigger 7 runs under the same dir and reads the send-back hook's own log there, `.code-writer-self-review.log` (r21).
   - Triggers, each with its pass condition:
     1. The commit-stall follow-up after a completed turn and after an aborted one. Pass: it comes after the completed turn only.
     2. The compact boundary after a compaction (row 141; r6 plat R2; r7 plat R3; r8 plat R2; r9 plat R3):
        - The engineer causes the compaction with Cursor's own manual summarize command where the IDE offers one. Otherwise each further turn asks the agent to read one scratch file whole, in one call, so the context fills toward Cursor's compaction (r10 cd R7, plat R5).
        - The run stops after at most 20 such turns, the plan's bound. The tool-calling turn after the compaction falls outside the bound. The scratch file is sized so 20 whole reads exceed the context window the engineer reads off the agent's model.
        - The scratch file ends with a sentinel line the agent must echo, so the engineer sees whether a read returned the whole file (r11 plat R6).
        - The run records "no route" and goes to the engineer at once, without the repeat, when the IDE offers no manual command and either (r11 cd R11):
          - 20 whole reads cannot exceed the window;
          - a read omits the sentinel.
        - The route taken is recorded, and so is whether Cursor showed any sign of summarizing (rows 198, 210).
        - The compaction's evidence is Cursor's own on-screen sign that it summarized the conversation, or the manual command's result, not preCompact's log line.
        - The run ends with one tool-calling turn after the compaction, because the flag is delivered on the next postToolUse (Twins).
        - Pass, recorded as one of three results:
          - yes: the boundary text arrives after that turn;
          - no: the compaction was observed, the turn ran, and the text did not arrive. A "no" on the manual route with preCompact not fired counts as undecided for the automatic route, because whether the manual command fires preCompact is unobserved (r11 plat R6);
          - undecided: no compaction was observed within the bound. The trigger repeats once. A second undecided goes back to the engineer for a decision, because OD4 sorts the compact twin as enforcement loss and row 200 covers the context-cap twin only (row 209).
        - The premise observable is the shim's log line for the preCompact call: whether preCompact fired, and whether it carried the conversation id or took requirement 6's missing-required-key outcome. `TestCompactTwin` is the positive control for the built flag, delivery and clear, so a "no" with a fired, id-carrying preCompact points at Cursor's delivery, not at the twin's code.
     3. Deferred context. Pass: the held text lands once.
     4. A `permissions.ask` command. Pass: the prompt shows.
     5. The ask-in-chat deny on a write. Pass: the deny names the hand-edit route.
     6. Whether `nudge-handoff-near-context-cap`'s reader resolves a measure from Cursor's `transcript_path` (row 140; r6 plat R5; r7 plat R2; r8 plat R3, plat R5):
        - The hook writes `<routed config dir>/.handoff-nudge.log`, not bare `~/.claude`'s (r9 cd R6).
        - The command of the twin's `stop` scratch entry, the one that runs the hook (Twins), begins `export HANDOFF_NUDGE_ABS_CAP=1000;` ahead of the generated launch guard. The value then reaches the shim and the hook rather than only the launch guard's test command (r19 cd R1). `compute_threshold` accepts it (`nudge-handoff-near-context-cap.sh:155-157`), so a resolvable measure fires in a short conversation (r9 sdet R1; r10 plat R1).
        - Pass, recorded as one of three results:
          - yes: `<routed config dir>/.handoff-nudge.log` gets a `nudged` line;
          - no: the shim's log shows the twin ran to exit 0 on a completed turn, and `<routed config dir>/.handoff-nudge.log` gets no `nudged` line (r10 cd R3; r11 cd R1):
            - a `schema-drift` line in that hook log (`:591`) is the same result, because the hook then found a usage block it could not use;
          - undecided: the shim's log shows no such run. The trigger repeats once. A second undecided leaves the entry `withheld-premise`, and the doc discloses the channel as unobserved (rows 200, 201).
        - `TestContextCapTwin`, in Phase 5b, is the positive control for the twin's payload build and delivery, so a "no" here points at Cursor's transcript (r8 plat R4; r9 sdet R1).
     7. The code-writer send-back, on a scratch entry `generate --print-entry` renders for the send-back twin, placed as step 4 places its entries: a `code-writer` subagent whose return lacks the self-review evidence the hook checks (Twins; r21):
        - The trigger reads the hook's own log, `<routed config dir>/.code-writer-self-review.log`, beside the shim's log.
        - Pass, recorded as one of three results:
          - yes: the hook's verdict is `sent-back`, the subagent is sent back once, and its second return goes through;
          - no: the hook's verdict is `sent-back` and Cursor did not re-run the subagent;
          - undecided, when the hook never reached Cursor's channel:
            - the hook log shows a verdict other than `sent-back`, such as `allowed-invalid-agent-id` or `allowed-no-transcript`;
            - the hook log holds no line, because the hook exits 0 without a line on its prefilter and on an agent-type mismatch;
            - the stop's `status` was not `completed`, so the twin never ran the hook.

            The trigger repeats once. A second undecided goes back to the engineer for a decision, because OD4 sorts the send-back twin as enforcement loss and row 200 covers the context-cap twin only, as row 209 rules for trigger 2 (r21).
        - On a "yes", the sent-back writer's first Shell call, a `git diff`, is recorded as passed or as denied as an unattributed subagent. Whether Cursor fires subagentStart again for the continuation is [unverified], and attribution's subagentStop entry has already removed the writer from its parent's active set (Twins), so a denied call goes to the engineer as a plan revision (r21).
        - Control, which must not be sent back: a `code-writer` return whose report carries the self-review ledger and whose transcript shows the reviewer Reads and the diff re-read (r21).
          - The control passes when the hook's verdict for it is not `sent-back`.
          - A control that is sent back records that the twin's premise failed, here most likely because Cursor's subagentStop carries no `last_assistant_message` and the hook read an empty report. Step 6's Outcomes bullet states what follows (r21).
        - `TestSendBackTwin`, in Phase 5b, is the positive control for the twin's mapping, so a "no" here points at Cursor's delivery (r21).
   - Control: each trigger with the adapter uninstalled and the scratch entries removed.
   - Outcomes: the row 140 and row 141 answers and trigger 7's result move each withheld twin out of `parity-exceptions.json`, or rewrite its entry as a documented gap. Phase 5c's PR carries that edit (Phases). A "no" on a withheld twin is a passing run, and its outcome is that edit (r6 plat R2). A "no" writes the reason `unsupported`, because it shows Cursor lacks the event or the channel (Registration; r8 plat R5). An `undecided` leaves the twin withheld and writes no `unsupported` (r21). A trigger 7 result, "yes" or "no", counts only when trigger 7's own control passed, the bullet that begins "Control, which must not be sent back" (r21). A failed control goes back to the engineer for a decision, because OD4 sorts the send-back twin as enforcement loss and row 200 covers the context-cap twin only, as row 209 rules for trigger 2 and as trigger 7's second undecided applies. The twin stays `withheld-premise` and no `unsupported` is written meanwhile (r21). A failed control is not a failed run, because its most likely cause is Cursor's channel, which a fix PR cannot cure, so Phase 5c still starts. If the engineer finds the twin built the payload wrong, it is a failed run and a fix PR comes first (Phases; r21). Beside an undecided main run, the failed control governs: it goes to the engineer, and the undecided main run is not repeated (r21).
7. Credential arms.
   - Observe: each pre-call arm's trigger, and MCP redaction on `cursor-adapter-fixture`, a purpose-built stdio MCP server in the scratch root that returns a fake credential-shaped string. The run calls no other MCP server.
   - Each Shell trigger of requirement 32 runs a stub, never the real tool, so a missed deny prints only a fake shape (r21):
     - stubs named `env`, `gh`, `git` and `security` sit in a scratch `bin/` directory in the scratch root, and each prints one fake credential-shaped string and nothing else, whatever its arguments;
     - each trigger names its stub by absolute path, as in `<scratch root>/bin/gh auth token`, so neither the agent Shell's PATH nor its environment, `HOME` or `GH_CONFIG_DIR` decides what runs;
     - the arm matches a path-named tool as it matches the bare name (requirement 32), so each trigger reaches the same deny. Requirement 32's tests cover the bare-name forms.
   - Pass: each pre-call arm denies, and the fixture's output reaches the agent redacted.
   - Control: a clean read and a clean fixture call pass.
8. Attribution.
   - Observe:
     1. a restricted subagent's Shell call, alone and beside a parallel subagent;
     2. a restricted reviewer's Delete of a scratch file;
     3. a built-in subagent's calls, and a restricted agent's permitted tool;
     4. a restricted subagent whose first call follows its start at once;
     5. if C5 did not settle row 138, whether `parent_agent_id` marks subagent calls only. C5 settled it, so this does not run (r12);
     6. a restricted subagent's call to step 7's `cursor-adapter-fixture` MCP tool, its WebFetch call and its WebSearch call. The record states, for each, the Cursor tool name seen, whether preToolUse fired, and whether the call carries `parent_agent_id`. The web calls' arguments are fixed inert values (a loopback address for WebFetch, a fixed non-sensitive query for WebSearch) and hold nothing read from the repository;
     7. a restricted subagent completes, then another subagent whose tool list permits Write writes under the same parent. The nested variant runs the same two subagents as children of one subagent.
   - Pass: 1, 2, 4 and 6 are denied, 6's web calls included, and 3 passes. 5 is recorded. 7 passes in both variants, which shows subagentStop removed the entry.
   - A call in 6 (the MCP fixture call, the WebFetch call or the WebSearch call) that carries no `parent_agent_id`, or that never reaches preToolUse, goes to the engineer after Phase 5a's run and before Phase 5b's branch starts, because that run follows Phase 5a's merge (Phases; Twins). The engineer chooses between the disclosed fail-open exception, after which `docs/cursor-adapter.md` lists that tool as a limit the adapter cannot enforce, and, for an unmarked call only, a plan revision that re-adds a main-conversation record, with its own creator, tests and CLI behavior. A call that never reaches preToolUse cannot be gated by such a record, so only the disclosed limit applies to it.
   - Control: the main agent's same calls pass, the fixture call and the web calls included.
   - Observations 1, 2 and 6 and the Control join requirement 22's Cursor-update checklist, so a later Cursor build is checked again for the marks the rule rests on.
9. Permission entries (requirements 4, 5).
   - Observe (r5 cd R8):
     1. `sudo -n -l`;
     2. a package-manager install of a name its registry lacks, with auto-update off;
     3. a Read of a scratch `credentials.json` placeholder;
     4. `marker.sh` with an unknown subcommand from an agent Shell call.
   - Pass: `gate-settings-permissions.sh` denies 1 to 4.
   - Control: an exact `permissions.allow` shape of `marker.sh` runs.
10. Modes. Cells 1 to 3 run under Auto-review with the sandbox on and with it off, and under any auto-run mode the IDE offers. The IDE offers no Auto-review mode with the sandbox off (OD9's C3), so that arm has no mode to run in (r12). Each cell with its pass condition:
    1. A `marker.sh` write from an agent Shell call, with the adapter's user-level `sandbox.json` installed and no workspace-level one (OD9's C3, C6; OD11(b); r15; r17 ciso R3, sdet R5, plat R5, cd R6):
       - Phase 5a's run takes it, under Auto-Review (with Sandbox) (r19 cd R10).
       - Pass: it lands in its directory inside the routed config dir, with no prompt.
       - On a miss, the record names the step that failed, the identity walk, a read or the write, by the script's own message.
       - A miss of the write itself, with the file present, takes OD11(b)'s fallback. A miss at the walk or at a read goes to the engineer with its cause.
       - Controls: with the adapter installed, a direct write to a listed directory by another command, such as `python3 -c`, lands, and writes to an unlisted name beside `sessions/` and to a prefix-sibling of a listed directory both fail. The doc states the extent this shows.
       - Baseline, not a control: with the adapter uninstalled the same `marker.sh` write fails at its identity walk (`marker.sh:141`) before it reaches the sandbox (r19 sdet R11).
       - Once more, in a scratch workspace whose own `.cursor/sandbox.json` lists an unrelated directory, the record shows whether the workspace list replaced or added to the user-level one (plat R7).
       - C6 saw a user-level entry make a kit directory writable, not one inside the routed config dir.
    2. A read-only Read against a Read gate (requirement 34). Pass: it is denied, and which of the two events fired is recorded.
    3. Step 6's trigger 4, a `permissions.ask` command. Pass: it prompts, or the mode goes into requirement 5's precondition as one where the adapter's `ask` does not hold.
    4. A workspace still in Restricted Mode (OD4). Pass: the result settles OD4's item either way.
    5. O29's first call after a relaunch. Pass: it is hooked.
    6. The run-mode line, which Phase 3's run takes (r9 ciso R5, sdet R2; r10 ciso R4, sdet R6, cd R8; r13). The engineer sets the IDE in turn to Auto-review, Allowlist with an empty list, and each auto-run mode the IDE offers. For each, the engineer records whether `~/.cursor/permissions.json` exists and the line `check` prints.
       - Pass: `check` prints requirement 5's unreadable line under every mode. That holds by construction, so the cell is a recording whose trigger is a `permissions.json` appearing (r17 sdet R10).
       - A `permissions.json` that appears is recorded and goes to requirement 5's optional search for a source (row 197).
       - Cell 6 joins requirement 22's Cursor-update checklist, so a later Cursor build is checked again.
    7. Under Auto-Review (with Sandbox), with the adapter installed, two agent Shell writes of a `sandbox.json`, which Phase 5a's run takes (OD11(b); r15; r17 ciso R4, cd R6):
       - one rewrites `~/.cursor/sandbox.json` with its own current bytes, so a landed write changes nothing;
       - one creates `.cursor/sandbox.json` holding `{}` in a scratch workspace that has none.
       - Pass: each fails or prompts.
       - A landing with no prompt, of either file, goes to the engineer with OD11(b)'s fallback as the default. Either gives the agent a `sandbox.json` of its own choosing, so the two take one disposition (ciso R4).
       - The record names, for each write, whether it landed, prompted or failed.
       - The second write's file is removed before any later cell (sdet R5).
    8. Under Auto-Review (with Sandbox), with the adapter installed, which Phase 5a's run takes (r17 plat R5; r19 sdet R9):
       - the layout: a linked worktree of a scratch repo opened as the only workspace root, with the main checkout and its `.git` outside it;
       - in that worktree, `git add` and `git commit`. Pass: no sandbox denial or prompt, and the new commit's hash is in the worktree's log;
       - one network call, the inert read the doc names once so each re-run repeats it. Pass: no sandbox denial or prompt, and it returns content;
       - otherwise the doc scopes the mode, and the result goes to the engineer.
    - Cells 1, 7 and 8 join requirement 22's Cursor-update checklist (r16; r17).
    - Control: step 2's Allowlist run. Cell 7's control is the same two writes under Allowlist with the sandbox off, which prompt.
11. Cursor's commit trailer.
    - Observe: a commit in a scratch repo made through the agent, whose command Cursor rewrites with its trailer.
    - Pass: `deny-pii-in-commits` lets it through, or the trailer's form goes to the engineer (row 135).
    - Control: the same commit from Claude Code.
12. Latency, for OD5.
    - Observe: 20 Shell, 20 Read and 20 Write calls, with the adapter installed and with it uninstalled, in a linked worktree of a scratch repo with worktree enforcement on, so the gated path and its identity walk run (r19 plat R2). A call's hook time is the sum, over its events in the hooks log's order, of each event's slowest hook duration, read through the admission rule's integer filter. Hooks of one event run in parallel (O22.a), so the slowest one bounds that event.
    - Pass: per class, the added hook time is within OD5's budget. It is the installed arm's 95th percentile minus the uninstalled arm's, each the 19th of its 20 sorted values. Step 12 applies OD5's 95th-percentile condition only, because 20 calls give no 99th percentile (r5 plat R5).
    - Control: the uninstalled arm, with the import on.
13. Install round trip on the engineer's own `~/.cursor`, holding their own `hooks.json`, with Cursor quit around each command (Installer).
    - Observe: `cmp` of `hooks.json` after `uninstall` against a copy taken before `install`.
    - Pass: install merges, and `uninstall` restores the file byte for byte (requirement 18).
    - Control: the same copy, relaunch and `cmp` sequence with `install` and `uninstall` skipped, which shows Cursor itself leaves the file unchanged.
14. Self-protection (requirement 39), under Auto-review. Each trigger with its pass condition:
    1. A Write creating a name that does not exist inside `~/.cursor/cursor-adapter/`. Pass: the ask-in-chat deny.
    2. A Write creating a name that does not exist inside the adapter state dir. Pass: the ask-in-chat deny (r5 ciso R2).
    3. The tamper check, with entries removed, changed or added (r6 ciso R1; r8 ciso R3; r9 ciso R1). Cursor is quit around each edit:
       - First variant: the engineer removes one adapter-owned entry other than the sessionStart `check --hook-env` entry, flips another's `failClosed`, and adds one foreign entry to `~/.cursor/hooks.json`, then relaunches Cursor and starts a conversation. Pass: the check's log line and `status` both name the removed, the changed and the added entry. The engineer then removes the foreign entry by hand and runs a plain `install`, which prints nothing foreign and restores the rest.
       - Second variant: the engineer removes every adapter-owned entry and adds one non-owned entry with no shim path, then runs a plain `install`. Pass: `install` prints the added entry by event and position before the pre-image changes.
    4. A scratch project-level entry that denies one inert command with a fresh nonce in `user_message` and another in `agent_message`, after which the agent is asked to repeat any text it was shown (r5 ciso R4; r6 cd R5):
       - Pass: whether each nonce reached the model is recorded yes or no, as requirement 3's second observation (row 182). OD9's run recorded yes for `user_message` and no for `agent_message`, once (r12). A result where neither reaches the model goes to the engineer as a plan revision, because the agent then sees no deny text (r13).
       - It needs no adapter, so Phase 3's run takes it.
    5. A Write that overwrites the existing install record in `~/.cursor/cursor-adapter/` with its own current bytes, so a missed deny changes nothing. Pass: the ask-in-chat deny (r11 ciso R1).
    - Control, with the adapter uninstalled, where `uninstall` has removed the install record and the state dir. Each Write trigger's control is a Write that exists there and changes nothing Cursor or the harness reads (r21):
      - for trigger 1, a Write creating a fresh name directly inside `~/.cursor/`, which `uninstall` leaves;
      - for trigger 2, a Write creating a fresh name directly inside the routed config dir, not inside any of its stow-linked directories;
      - for trigger 5, a Write overwriting trigger 1's control file with its own current bytes;
      - each runs with no deny, and the engineer removes every file a control created before the next `install`;
      - trigger 3's sequence with no entry removed names none.
15. Agents and rules (Phase 5c; r4 plat R3).
    - Setup: `install --acceptance-nonce` with a fresh inert value, so one generated rule of each kind tells the agent to end its reply with its own nonce (Agents, rules and instructions; r5 plat R6).
    - Observe (r5 cd R8):
      1. a generated agent started through Task, with its `model` from `model-map.json`;
      2. replies under the `alwaysApply` rule;
      3. replies under the `globs` rule with a matching file in context;
      4. replies under the `globs` rule with no matching file in context.
    - Pass: 1 starts on its mapped model with no error. The `alwaysApply` nonce ends every reply in 2 to 4. The `globs` nonce ends the replies in 3 and none in 4.
    - Control: the same prompts after `uninstall`, where neither nonce appears. A plain `install` follows, which drops the nonce lines.
16. Directory pin (Repo resolution; r13), which Phase 3's run takes and Phase 4's run takes again, for the rewrite's first line, apart from cell 5, which runs before Phase 3 merges. Cells 1, 2 and 6 run under Allowlist with an empty allowlist and again under Auto-Review (with Sandbox), and the other cells name their mode or need none (r17 be R6, sdet R3; r18).
    - Observe:
      1. a call that changes into a scratch subdirectory, then a second call that creates a sentinel by a relative path;
      2. a call that starts with `cd <absolute path of that subdirectory> &&` and creates a sentinel by a relative path. The command still parses after the pin prefix, and its `cd` runs there. It does not test the pin's directory rule, which `TestDirectoryPin` does (r17 sdet R9);
      3. Cursor's approval prompt in cell 1, and the Shell tool's result the agent reads (r17 ciso R5);
      4. under Auto-Review (with Sandbox), an inert read-only command, run once with the adapter uninstalled and once installed (r17 be R5, plat R5);
      5. with the adapter uninstalled, a scratch project-level preToolUse entry that rewrites a command into the pin prefix form naming a directory that does not exist (r18; r19 ciso R2, sdet R6, plat R3):
         - the command holds a sentinel on its second line, one after a lone `}`, one after a `||`, and one inside a heredoc;
         - the positive control is the same entry naming an existing directory, whose sentinels all appear, which shows Cursor applied the rewrite;
         - after the failing call, an ordinary call in the same conversation, with the entry naming an existing directory;
         - it needs no adapter, so it runs before Phase 3's PR merges, and a fail stops that merge;
      6. a multi-line command whose last line ends in a backslash, one with `shopt -s extglob` on one line and an extglob pattern on the next, and one holding a heredoc, a trailing comment and a `||` (r18; plat R8; r19 plat R3);
      7. the shell Cursor runs the command in, from a probe's own process: its basename and argument vector, whether it is interactive or a login shell, and whether it sourced the engineer's shell startup files. Cells 1 and 2 ran in that shell, so their results hold under its startup files (r18; plat R8; r19 sdet R2, plat R3);
      8. under Allowlist with one inert command on the allowlist: that command, run once uninstalled and once installed. Then the engineer allowlists a pinned command from its prompt, and the agent runs a different command (r19 be R4, plat R4);
      9. a call that sets a non-default option the Shell tool offers other than its directory input, such as a background run, installed (r19 be R5; r20 be R1);
      10. a call that sets the Shell tool's directory input to a directory inside the workspace root other than the pinned one, and one that sets it to a directory outside every root, each run once uninstalled and once installed (r20 be R1). The record names, per run, the directory the command ran in, from a sentinel a probe writes with `pwd -P`, and, installed, each deny text the call shows. A refused call runs nothing, so no gate's directory decides an effect, and which directory each hook received is `TestDirectoryPin`'s, as for cell 2. The cell also runs an installed call that sets the input to the pinned directory itself, which runs there with no refusal, so the allow side shows the key name and value form the unit rows assume (r21 sdet R9). If the Shell tool offers no directory input, the cell records "refusal unexercised", which is not a pass.
    - Pass (r17 cd R7):
      - cell 1's second sentinel lands in the workspace root;
      - cell 2's sentinel lands in the subdirectory;
      - under Allowlist, each call prompts as with the adapter uninstalled, so the pin's output suppressed no prompt (requirement 2);
      - cell 3's prompt shows the agent's command in full, with the pin prefix shown or hidden (r19 be R4);
      - cell 4 shows no prompt the uninstalled run did not, or the doc discloses the added prompt and the result goes to the engineer;
      - cell 5's failing call leaves every sentinel absent, its positive control leaves every sentinel present, and its following call runs and starts in the pinned directory;
      - cell 6's commands each give the stdout bytes and exit status they give with the adapter uninstalled. Stderr is left out, because the pin prefix shifts its line numbers. Where cell 7 records `zsh`, the `shopt` command is a bash-only check (r19 sdet R12);
      - cell 8's allowlisted command does not prompt installed, and the different command prompts;
      - cell 9's option survives the rewrite;
      - cell 10's installed calls that set a directory other than the pinned one each take the directory-input refusal, and no sentinel appears. Its installed call that sets the pinned directory runs there and meets no refusal. The uninstalled runs are a baseline that records whether Cursor honors the input (r20 be R1; r21 sdet R9).
    - A fail of cell 3 or cell 8 goes to the engineer as a decision, and the doc and requirement 5 state its consequence (r19 be R4).
    - Recorded: cell 3 shows the pin prefix, C7's prediction. Cell 5 records how Cursor reports the exit. Cell 7 records the shell, which the doc names and `TestDirectoryPin` adds, by whether it has `builtin`, if it is none of the shells it runs (r18; r20 sdet R4).
    - Control: after `uninstall`, cell 1's second sentinel lands in the subdirectory, where Cursor's carried-over directory puts it (Run facts), and cell 6's commands run as recorded there.
    - Step 16 joins requirement 22's Cursor-update checklist. A build that stops applying the rewrite now meets the `pin-check` deny on every Shell call (r17 ciso R2).
17. Design-input cells, not phase-gating (row 324). They inform a `CLAUDE.md`-delivery design in another session, and no phase's run waits on them.
    - Run each cell attended in the IDE, with a fresh nonce. Each is observation-only and builds nothing.
    - A recorded "no" or "unexercised" is a pass, not a fail. Step 17 sits outside the Phase 6 bullet that requires each acceptance step to pass or be recorded.
    - The engineer hands the record to the other session.
    - Every premise is [unverified], except two Phase 1 records in "Imported hook outputs in the IDE (O2.e, O2.f)": Cursor's hooks log skips `SubagentStart` as an unknown event, and an imported `SessionStart` context value did not reach the agent (r21).
    - `~/.claude/settings.json` is a relative stow link to the tracked `claude/.claude/settings.json` in the main checkout (Alternatives set aside), and `~/.claude/CLAUDE.md` is one to the tracked `claude/.claude/CLAUDE.md` there. No cell writes through either link. The engineer repoints a link at a scratch copy outside every git tree, and later restores the recorded link text. The engineer chose this design [engineer-verified: "Adopt all seven (Recommended)"]; its wording here is the session's.
    - Cells 2 and 3 are the one attended exception to the Alternatives-set-aside bullet that rejects a probe in `~/.claude/settings.json`, because the design under study reads the user-level path. The exception stays within three bounds, which are this plan's choice:
      - no Claude Code session is started, cleared or resumed on the account whose config dir is bare `~/.claude` during the window, because a session that starts in the window loads the scratch copy. How a running session behaves is [unverified];
      - every nonce is inert text, and each scratch hook is an inline command that runs no script file and, when `CLAUDECODE` is set, exits 0 printing and writing nothing. That every Claude Code hook carries it is [unverified], as row 40 states (r21). The cell records whether `CLAUDECODE` was set in Cursor's hook environment, so a silenced entry is told apart from an ignored output;
      - the scratch `settings.json` passes `jq empty` before its link is repointed, because how Claude Code treats an unparseable settings file is [unverified].
    - Only `~/.claude/settings.json` is repointed by default. Cell 2 runs read-only first, and `~/.claude/CLAUDE.md` is repointed only if that run shows the agent will not disclose its instructions. The cell records whether it was.
    - The record directory, `<record>`, is persistent, for example `~/.local/state/<fresh name>/`. It lies under none of `/tmp`, `$TMPDIR`, `~/Desktop`, `~/Documents` and `~/Downloads`, nor under any directory of `~/.claude/`, because `hooks`, `scripts`, `agents`, `rules` and `skills` there are stow links into the main checkout. It holds:
      - the plain `readlink` text of each link to be repointed, never `readlink -f`. The record step refuses unless that text, resolved from `~/.claude/`, names the tracked `claude/.claude/<name>` in the main checkout, so a path that already points into a `<record>` is restored first;
      - `shasum -a 256` of both tracked files, `settings.json` and `CLAUDE.md`, repointed or not, read through their links before any repoint. The record step also refuses unless `git -C <main checkout> status --porcelain -- claude/.claude/<name>` prints nothing for both files, because a write to an already-modified file leaves that output unchanged;
      - the scratch copies;
      - a listing of the user-level `~/.cursor/rules/` and `~/.cursor/agents/` directories;
      - every file the cells create.
    - The engineer runs the repoint and the restore in a terminal, not through the session's Bash. The session may hand over a script file for them, written under `<record>` and nowhere under `~/.claude/` or a git tree.
    - During the window the engineer runs no `install`, `check` or `generate`, because `check` reports a `settings.json` link that does not resolve to the tracked file (Registration; r21).
    - Repoint, per link:
      - while the path is still the stow link, `cp <path> <record>/<name>` makes the scratch copy, and the engineer edits the copy at its own path, never through the link;
      - the scratch `settings.json` is the full tracked file plus the two inert entries, and `jq empty <record>/settings.json` passes. A scratch `CLAUDE.md` names its `@`-imported file by absolute path;
      - the swap runs only when `test -L <path>` passes and the record already holds that link's non-empty `readlink` text and its `shasum`;
      - a script for the swap joins those tests to the `ln` with `&&`, so a failed test stops the swap;
      - the swap is atomic: `ln -s <record>/<name> <path>.repoint && mv -f <path>.repoint <path>`.
    - Restore, per link, is idempotent:
      - `test -L <path>.repoint && rm -f <path>.repoint` clears a left-over temporary name, and then `ln -s <recorded text> <path>.repoint && mv -f <path>.repoint <path>` swaps the recorded link back atomically;
      - the same steps hold whether the path holds the scratch link, a regular file, a different link to a file or nothing;
      - after a crash, the engineer restores `settings.json` first, because it holds the entries that run code, then `CLAUDE.md`, then the Cursor-side files;
      - if the record is lost:
        - the engineer does not remove the path, because the atomic `mv -f` replaces whatever sits there and a failed `ln -s` must not leave `~/.claude/settings.json` absent;
        - the engineer derives the link text from a sibling folded link. `readlink ~/.claude/hooks` prints `../<checkout>/claude/.claude/hooks`, where `<checkout>` is the path from `~/` to the main checkout. The derived text swaps its last component for the file name;
        - the engineer runs `test -L <path>.repoint && rm -f <path>.repoint`, then `ln -s <derived text> <path>.repoint && mv -f <path>.repoint <path>`;
        - the engineer confirms the swap with three checks:
          - `readlink -- <path>` prints the derived text;
          - `cmp -s <path> <main checkout>/claude/.claude/<name>` passes, which a dangling or wrong text fails;
          - `git -C <main checkout> status --porcelain -- claude/.claude/<name>` prints nothing for both tracked files;
        - the record's `readlink` and `shasum` checks below do not apply on this branch. The no-write witness is unavailable, so step 17's record is handed over as unwitnessed;
        - the engineer does not run `install.sh`, whose header says it mutates this repo's tracked settings.
    - After the restore:
      - `readlink -- <path>` prints the recorded text, byte for byte;
      - `shasum -a 256` through each link equals the record, which witnesses that no cell wrote the tracked file;
        - a mismatch while that file's porcelain output is empty means the checkout changed in the window, as by a pull, and the restore did not fail;
        - a mismatch where the output is non-empty means a cell may have written the tracked file. The engineer saves `git -C <main checkout> diff -- claude/.claude/<name>` into `<record>`, stops step 17, voids its record, and reverts the file only after reading that diff;
      - the engineer quits and relaunches Cursor before any other acceptance step;
      - only once these checks pass does the engineer delete the record, because a link that still points into it would then dangle;
      - listing the user-level rules and agents directories then prints what it printed before, because a left-over always-apply rule would end later replies with a stale nonce and corrupt step 15's nonce observation.
    - [unverified]: how Cursor and Claude Code read a link whose target is an absolute path, and how either behaves when the settings link dangles. If cell 3 reads "unexercised" under the absolute link, the record says so and the cell ends, which is a pass (row 324). A design that needs the tracked file edited is a separate decision outside this step.
    - Every project-level file lives in a scratch repository that is neither this repository nor a consuming repository (the Goal and row 2 bar any such file in a consuming repo). Cell 2's `@`-imported file lives in `<record>`, outside the main checkout. Cell 1's custom agent is a file the engineer writes under `~/.cursor/agents/` for the cell.
    1. Subagent inheritance of an always-apply rule:
       - Observe: with a user-level `~/.cursor/rules/*.mdc` file holding `alwaysApply: true` that tells each reply to end with the nonce, a main-agent reply, then a built-in subagent's and the custom agent's. The cell repeats with a project-root `AGENTS.md` nonce and with a project `.cursor/rules` always-apply nonce, so each source is recorded on its own;
       - Pass: the main agent's reply ends with the nonce, and the record names, per source and per subagent, whether its reply ends with it;
    2. The import of `~/.claude/CLAUDE.md`, O9 (a) made a cell:
       - Observe, read-only first: with the import toggle on and then off, the main agent and a subagent are each asked to paraphrase a named rule from the user-level instructions file. Only if the agent will not disclose its instructions is `~/.claude/CLAUDE.md` repointed at a scratch copy holding a nonce line and an absolute-path `@`-import of a file holding a second nonce. Then, whether the main agent and a subagent each act on each nonce;
       - Pass: the record names, per agent and toggle state, whether it paraphrased the rule, and whether the repoint ran. If it ran, the record names per agent and per nonce whether it was acted on, and so whether an `@`-import is followed;
    3. Imported SessionStart and SubagentStart hook output in the IDE:
       - Observe: with the toggle on and `~/.claude/settings.json` repointed at its scratch copy, a scratch entry in that copy's hooks for SessionStart and one for SubagentStart, each printing Claude-format `hookSpecificOutput.additionalContext` holding a nonce. Whether each nonce reaches the main agent or the subagent, and whether Cursor ignores the output, mis-parses it or errors. The record holds only what is new at user level beyond the two Phase 1 records above. The session never reads the hooks log; its structure reaches it only by the engineer's report or the integer and event-name filter (Run order's admission rule, rule 4);
       - Pass: the record names, per event, where the nonce arrived or that it did not, and how Cursor handled the output.
    - Control:
      - each source in cell 1 has its own nonce and is the only source file present during its run, and its main-agent reply is the positive control for its subagents;
      - each subagent dispatch text also carries a second nonce the subagent must end its reply with, and the record reads the subagent's own returned text. A subagent counts as "does not inherit" a source only when the second nonce shows and the source's nonce does not;
      - cell 2's repoint arm and cell 3 each run in a conversation started after the repoint, and each nonce line tells the agent to end its reply with it;
      - cell 2 adds a project-root `CLAUDE.md` with the same two-nonce shape as its positive control, and records the `@`-import result as "unexercised" when the base file is not loaded;
      - cell 3's two scratch entries also append a line to a scratch file named by an absolute path under `<record>`, and the inline command carries no relative path, so "ran" is recorded apart from "nonce arrived", and an entry that did not run is recorded "unexercised";
      - cells 2 and 3 run once more with the toggle off, and a nonce that arrives there voids that nonce's toggle-on result;
      - a "no" counts as "not delivered" only when the cell's positive control held.

**Requirements.** These are the sketch's, carried from rounds 1 and 2 with their numbers and changed where a revision says so, plus requirements 36 to 41 from the `r3` review. Each names the findings it comes from by reviewer tag and round prefix (r5 cd R11):

- `ciso`, `sdet`, `plat` for staff-platform-engineer, `be` for staff-backend-engineer, `chr` for claude-hook-review, and `cd` for comment-discipline-reviewer;
- no prefix for round 1, then `r2` to `r11` for the later review rounds in order, `r11` being the last plan-review round a revision answered;
- `r12` alone, with no reviewer tag, for a change made when OD9's check results entered the plan;
- `r13` alone, with no reviewer tag, for a change made on the engineer's choices after the adversarial consult on OD9's results (rows 230 to 251);
- `r14` alone, with no reviewer tag, for a change made when OD9's C6 to C8 results entered the plan (rows 252 to 254);
- `r15` alone, with no reviewer tag, for a change made on the engineer's OD11(b) and OD11(d) choices and C6's user-level result (rows 255 to 263);
- `r16` alone, with no reviewer tag, for a change made on the engineer's OD11(e) choice (rows 264, 265);
- `r17` with a reviewer tag, for a change answering that reviewer's finding in the plan-review round after r16, and `r17` alone for a change that follows from one (rows 266 to 283);
- `r18` alone, with no reviewer tag, for a change made on the engineer's answers after that round and on the session's swap of the brace wrapper for a prefix line (rows 284 to 291);
- `r19` with a reviewer tag, for a change answering that reviewer's finding in the plan-review round after r18, and `r19` alone for the engineer's choice that round raised (rows 292 to 307);
- `r20` with a reviewer tag, for a change answering that reviewer's finding in the plan-review round after r19, and `r20` alone for a change answering the round after that, or the engineer's choices it raised (rows 309 to 315);
- `r21` with a reviewer tag, for a change answering that reviewer's finding in the plan-review round after r20, and `r21` alone for a change made on the engineer's choices that round raised and on C9's results (rows 316 to 325).

**Invariant** marks an enforcement property no later design may regress. The Test map after the list names each requirement's test class.

1. **Invariant.** A deny from any source wins over every other hook's output. O20 showed it for every tested source (OD6), and the import's copies running beside the shim rest on it (row 148). Managed and plugin hook sources are out of scope and unobserved, and the imported user-level direction stays a disclosed gap (ciso R2, be R7; r2 ciso R2, be R7).
2. **Invariant.** No adapter output is an explicit allow: a hook's allow maps to `{}`.
   - The explicit allows at `require-worktree-for-file-writes.sh:165` and `require-worktree-for-git-writes.sh:458` therefore become `{}`, and their context takes Deferred context.
   - Both allows go through `_lib_emit_allow_with_context` (row 175). From Phase 4 it prints nothing on an untranslated Cursor payload, so the import's copies of those gates give no allow either, and the gate matrix grants them no exemption (Identity and routing, requirement 40). Until Phase 4 the copies can emit it, as they do with no adapter installed (row 155).
   - Acceptance step 3's cell 5 records only whether an explicit allow from one source suppresses another source's beforeShellExecution `ask` (row 183). `TestCopyAllowSuppressed` and the gate matrix's raw and import arms show that no copy's allow reaches Cursor while the emitter's host walk succeeds (ciso R3, be R3, sdet R7; r4 ciso R2; r5 ciso R3; r6 ciso R3).
   - Residual, which the doc states beside row 183's open question: a walk that fails, from a failing `ps` or a `claude` process met first, lets a copy's fresh-lock allow through, as with no adapter installed (Identity and routing; r8 ciso R8).
   - The directory pin's rewrite carries `updated_input` and no `permission` field. C7 showed the IDE applies it and the call still prompts, and acceptance step 16 checks that again on each Cursor build (Repo resolution; row 244; r13; r14).
3. **Invariant.** A registration with `onFailure: "block"` blocks under Cursor on every outcome but a parsed deny, allow or no-opinion.
   - The outcomes that block: a gate's crash, timeout, missing script or non-zero exit other than 2, which the shim maps to a deny (Translation step 5); Cursor's own timeout kill of the shim and a missing or non-executable shim command, which `failClosed` blocks (O22); and empty or non-JSON output from the shim itself. A gate's exit 2 blocks as a deny carrying its stderr (Translation step 5). Whether a shim fault on a fail-closed entry blocks is unverified until acceptance step 4's case 7 runs in Phase 3's run (Phases), and a fault that does not block is the plan revision case 7 names (r21).
   - Each generated entry carries `failClosed: true`, which Cursor honors for a timeout, a missing command and a non-executable script (O22), and an explicit `timeout` with the shim's inner deadline below it.
   - A gate's own empty stdout on exit 0, Claude Code's no-opinion, maps to `{}`, because under `failClosed` Cursor blocks empty stdout and `{}` never blocks (row 120; Phase 1 observations, "Decisions: what blocks, what is seen (O2, O19)"). Empty stdout on any other exit is not a no-opinion.
   - A shim-authored deny carries one text in both `agent_message` and `user_message`: the hook name, the outcome class, any retry or ask-in-chat instruction, and the Recovery section of `docs/cursor-adapter.md` (Installer). OD9's run saw `user_message` reach the model and the `agent_message` text reach neither the model nor the screen, on one observation (row 182). A text in `agent_message` alone could therefore reach neither the agent nor the engineer, and both fields carry it in case a later build delivers only one. Acceptance step 14's trigger 4 is the second observation (rows 238, 239; r5 ciso R4; r13).
   - No deny text names the log path or `uninstall`. The Recovery pointer in the common text reads: "The engineer, not the agent, follows the Recovery section. An agent runs no command from that section, retries only where this deny's own text says to, and otherwise stops and reports this deny in chat." The Recovery section opens with the same sentence (Installer; requirement 39; r21).
   - Where the shim never runs, because it or the recorded bash is missing or unreadable, the generated command's launch guard writes a line naming the Recovery section to stderr and exits 127, which Cursor shows with the exit code (O22). A Cursor timeout kill shows only Cursor's own text, and the doc says so.
   - The shim encodes its deny without jq, or exits 2 when jq is unavailable.
   - An `install.json` that is missing, unreadable or malformed denies on a fail-closed entry and prints `{}` on an informational one. Malformed means it does not parse, or lacks the routed config dir, the contract version or the recorded tool map (Installer).
   - A generated command whose contract version differs from the one the shim's code carries is a stale install, as after a pull changed the code but not `~/.cursor/hooks.json` (requirement 13). A fail-closed entry denies with one constant outcome text, `stale install: the engineer quits Cursor, re-runs install and relaunches`. An informational entry prints `{}`. Each logs the mismatch, and `status` counts the mismatched calls (r21).
   - A resolved directory the shim cannot enter, because it does not exist or cannot be searched, denies on a fail-closed entry, naming that cause, and prints `{}` with a logged count on an informational one. The deny text also names the action that clears it, which is the Recovery entry's way out, in both fields as the common rule above states. The shim never runs the hook from another directory, because an unchanged repo-resolving gate exits 0 outside a repo (Translation step 4; r21).
   - Tests: one case per outcome, with ids from one module-level list that a test asserts equal to this one (r5 sdet R3; r6 cd R6):
     - exit and signal outcomes: exit 1, exit 2 with stderr, 126, 127, 137 and 124, and a timeout;
     - script faults: a `set -u` abort, a missing script, and a failed source of `_config.sh`;
     - `install.json` faults, each run on a fail-closed and an informational entry: missing, unreadable, unparseable, missing the routed config dir, missing the contract version, and missing the recorded tool map;
     - a stale install, run on a fail-closed and an informational entry, with the log line and the `status` count asserted;
     - an unenterable resolved directory, one that does not exist and one of mode 000, each run on a fail-closed file-tool entry, which denies and never prints `{}` or the hook's silent exit 0, and on an informational entry, which prints `{}` and logs a count (r21);
     - output faults: empty or non-JSON stdout;
     - over every outcome, `agent_message` equals `user_message` (r13), and no deny text names `uninstall`, the adapter state dir's path or the log filename (r6 sdet R1).
   - Launch-guard tests: the missing shim, the unreadable shim and the missing recorded bash test the launch guard that Phase 3's generator emits (r19 cd R1), so they sit in Phase 3 (ciso R3, be R12, plat R14, chr 7; r2 ciso R12, be R2, chr 7; r3 be R1, be R7, plat R1, cd R2; r4 sdet R2, sdet R5, plat R7).
4. **Invariant.** The 17 `Bash` deny entries, none with a hook twin (row 19), and the 13 `Read` deny entries (row 129) enforce under Cursor through `gate-settings-permissions.sh`, because Cursor documents no IDE deny list for Shell and its ignore list does not reach the terminal (G7, row 78). The two tool-name deny entries are parity exceptions (Twins; r3 ciso R9).
   - The gate matches a pattern's words both before and after wrapper stripping, including `&&` and `;` compounds, `$(...)`, `bash -c '...'`, `xargs`, `\sudo`, `/usr/bin/sudo` and `env X=1 sudo`, because `_lib_fragment_command_word` skips `sudo`, `env`, `xargs` and `pipx` as wrappers (`_lib.sh:2132`).
   - A `Read` deny also covers Grep and the `beforeReadFile` event, and an `Edit` deny covers Write. Patterns come from the install-time snapshot, never a consuming repo's file.
   - The gate fails closed on a missing or unparseable pattern list. Its header lists the wrapper forms it misses and the operators `_lib_split_fragments` does not split, `&`, `<(` and `>(` (`_lib.sh:2089`), and it cites Claude Code's permission-rule semantics per form (row 193; r7 ciso R5).
   - Each entry is tested with wrapper, path, quoting and case variants and a near-miss allow (ciso R4, ciso R9, chr 4, sdet R12; r2 be R8, ciso R12, chr 4).
5. **Invariant.** Under Cursor, `gate-settings-permissions.sh` denies each `marker.sh` or `review-ledger.sh` call it recognizes that is not an exact `permissions.allow` shape. It covers the shapes it recognizes, and the run-mode precondition carries the rest, as `enforce-marker-script-shape.sh`'s own residual note says of its Bash arms (`:115-135`; row 193; r7 ciso R5). Under the precondition's Auto-Review (with Sandbox) mode nothing carries the rest, as under Claude Code's `auto` mode (`:131-135`), and the doc discloses the gap below (OD11(e); r16).
   - Reason: `enforce-marker-script-shape.sh` relies on Claude Code's ask-by-default prompt for command shapes outside `permissions.allow` (`:127-132`), and Cursor's default Auto-review mode ran a plain Shell call with no prompt (row 52; Phase 1 observations, Auto-review).
   - `docs/cursor-adapter.md` states the run-mode precondition, which either of two modes meets (r7 ciso R2; r8 ciso R6, cd R5; r16):
     - Allowlist with the sandbox off, where every Shell call outside the allowlist prompts, as Allowlist mode did in Phase 1 (Phase 1 observations, Decisions). The allowlist is empty, unless an acceptance cell shows a non-empty one holds. Phase 1 ran only the empty case, and the vendor calls its allowlist and auto-run settings "Not a security boundary" (G7; r10 cd R11);
     - from Phase 5a, Auto-Review (with Sandbox), with the adapter's user-level `sandbox.json` in force (OD11(b), OD11(e); r16; r17 be R2). The doc discloses this mode's gap (r17 ciso R3):
       - any Shell command that writes a directory that file lists lands with no prompt, whatever its shape, so it can forge a review marker. `marker.sh` and `review-ledger.sh` shapes the gate does not recognize are only some of those commands;
       - the listed directories include the active-bypass ones, where a file with a live PID releases its whole gate for the session (`_lib.sh:2869`, `:2913-2914`);
       - Claude Code's `auto` mode already carries the same gap (`enforce-marker-script-shape.sh:131-135`; row 261);
       - a workspace `.cursor/sandbox.json` that exists before the conversation, checked in or left by earlier work, wins over the adapter's file and can widen the sandbox with no write the adapter sees. Requirement 39's residuals state its one report channel (r20 ciso R2);
       - the sandbox is path-based (C3, C6), and no cell tested it against a hard link or symlink a Shell call plants in any directory the sandbox leaves writable (the grant's listed directories, the workspace and the temp dirs; C3, C6) to a protected file, such as `~/.cursor/hooks.json`, the install record or `~/.cursor/sandbox.json`, and then writes through. Whether such a write lands is unobserved, and the doc discloses it as untested (r21);
     - Allowlist (with Sandbox), which no cell ran, meets neither.
   - A sessionStart check prints the run-mode line (r7 cd R1). No known source holds the mode: `~/.cursor/permissions.json` was absent under every run mode, and the run-modes page gives it another schema than G7's relay (row 197).
   - So the check reads no file and prints `run mode not readable; requirement 5's precondition unchecked` on every result.
   - The doc states the precondition, either mode above, as the engineer's own attestation (r16; r17 cd R3).
   - The line's rules (ciso R6; r2 ciso R12; r3 cd R4; r4 ciso R3; r6 ciso R1; r7 ciso R2; r8 ciso R6; r9 ciso R5, sdet R2; r10 ciso R4, plat R4, sdet R6; r11 plat R1; r13):
     - the line reaches two channels: the sessionStart entry's `additional_context`, and the terminal output of `install` and `check`. The sessionStart log line holds only the line's closed label in its run-mode field (requirement 15);
     - no file content reads as meeting the precondition;
     - a search for another source, Cursor's own settings store or beforeShellExecution's `sandbox` key (Payload), is an optional add-on that no phase waits on. A source it finds comes back as a plan revision, with its own reader and fixtures.
   - Tests: `TestRunModeWarning`, in Phase 3's `test_cursor_adapter_install.py`, asserts the line on both channels with `~/.cursor/permissions.json` absent, unparseable, holding the run-modes page's `autoRun` shape, and holding G7's keys, so no file content suppresses it (r8 sdet R5; r13).
   - From Phase 5a, `install`'s user-level `sandbox.json` makes the agent-run scripts' directories writable under a sandboxed mode, which the precondition's second mode rests on (OD11(b); r15; r17 be R2). If OD11(b)'s fallback fires, the doc's precondition goes back to Allowlist with the sandbox off alone (r16).
6. **Invariant.** A translation missing a required key path fails closed on fail-closed entries, and is never an empty-field translation. The required paths live in `field-table.json`, built from the key paths OD9's C5 records, and the canary also flags missing paths (sdet R5; r3 sdet R2).
   - `field-table.json` marks each path `observed`, from C5's records, or `doc-only`, from the hooks doc alone.
   - A `doc-only` path translates like an `observed` one, so a payload that lacks it denies as a missing required key.
   - A gate-matrix row that rests on a `doc-only` path is reported unconfirmed until acceptance step 2 observes the path (r4 sdet R8). Phase 6's PR marks `observed` each path the acceptance run observed (r5 be R3).
   - A test checks each `observed` path against requirement 20's shapes-only fixtures, which come from C5's records rather than from the table (r5 sdet R5).
7. **Invariant.** No Cursor-side matcher excludes a call the Claude matcher would match. Cursor's matcher is an unanchored, case-sensitive regular expression on the tool name (O21), so a generated matcher may select more, and the shim's re-check narrows it. Registrations split per event where needed, and identical tuples dedupe once `if:` is dropped. A generated beforeShellExecution matcher is never anchored at the command's start, because the directory pin puts its pin prefix ahead of the command (r19 cd R2) (Repo resolution; r13). Requirement 41 tests it (be R2, be R7, chr 6; r3 sdet R4).
8. **Invariant.** Agent tool limits and every agent-keyed gate decide on the calling agent that active-set attribution gives (Twins).
   - Only a call that carries `parent_agent_id`, by Identity and routing's one definition, is attribution-checked. It is checked as each entry in its direct parent's active set, and denied if any check denies. A call that carries none is the main agent's and is never attribution-checked (Twins; r21).
   - A marked call with no entry under its parent, from a missed or late subagentStart, is checked as an unattributed subagent (row 138; r12; r20 ciso R1). Only Read and Grep pass. Any other tool denies, and the message tells the agent to retry once and, if the retry is denied too, to stop and tell the engineer in chat (r21 be R8).
   - The allowlist table is baked at install from `agents/*.md`. An agent with no `tools:` list gets every tool, and an agent absent from the table, such as a Cursor built-in, meets no harness tool limit, as under Claude Code (row 168).
   - Every tool the allowlist table does not permit is denied to a restricted agent, MCP, WebFetch, WebSearch and Cursor-only tools included. That holds for marked calls. For MCP, WebFetch and WebSearch it rests on acceptance step 8's observation 6 until it runs [unverified], because each needs the call to reach preToolUse and to carry `parent_agent_id`. Write maps as one class with Edit.
   - An entry stays until its subagentStop or `check` (Twins). A missed subagentStop keeps the restriction, the fail-closed direction, for later subagents under the same parent only, and Recovery names the way out (r21).
   - Tests: an unmapped tool, an MCP tool, two parallel subagents, a nested spawn, a repo-shadowed agent name, a missed subagentStop, a missed subagentStart, a child call racing the set's write, name normalization, and the allow side, where a restricted agent's permitted tool and the main agent pass (ciso R8, sdet R10, be R14, chr 3; r2 ciso R12, sdet R12, be R11, chr 3; r3 ciso R6, sdet R11, cd R2).
   - Rows in `TestAgentAttribution` (r20 ciso R1; r21 sdet R4, sdet R6):
     - a marked call with no entry under its parent: Write, Delete, Shell, an MCP tool and a nested spawn deny with the unattributed text, which tells it apart from a restricted-agent deny, and Read and Grep pass. An unmapped tool denies on requirement 37's fail-closed entry, so that row asserts requirement 37's deny text;
     - a marked call under a parent whose set holds a restricted entry is checked as that entry, and a nested child's call is checked under its direct parent's set, not the root's;
     - a regression row: an unmarked Write passes with a stale restricted entry present under its own conversation's id;
     - a subagentStart payload with no `parent_agent_id` is logged, and `status` and `check` report it;
     - the allow side: a child whose tool list permits the Write and the Delete passes both once its subagentStart has populated its parent's set.
9. **Invariant.** The adapter's own gates meet the `hooks/` gate invariants (malformed input, jq absent or hung, a below-floor bash, exit codes, the deny envelope) and run fail-closed. Their tiers are recorded in `docs/cursor-adapter.md`, because the tier-table test admits only `hooks/` gates (row 45), and the adapter-gates test checks that record. A test asserts every generated gate entry fail-closed (ciso R10, sdet R12, plat R13, chr 2; r2 sdet R12, chr 2).
10. **Invariant.** Every payload id passes the grammar of `_lib_valid_session_id_component` before any path use.
    - The shim's lib copies the grammar, because requirement 28 keeps `_lib.sh` out of the shim.
    - The copy's parity test calls the live `_lib.sh` function beside the copy over the corpus of `TestSessionIdPatternMatchesLibSh` (`test_author_outcome.py:739-766`, as the staff-sdet review cites it), plus an embedded newline, a `/` and an id of 256 or more characters.
    - The parity test sits under `claude/.claude/hooks/tests/`, so a `_lib.sh` edit selects it, and a shell file under `scripts/` selects the hooks tests too (row 25).
    - Files are created 0600 and directories 0700 without following links.
    - Payloads travel by stdin, never argv. Requirement 15 states the no-path-name rule for payload fields that carry file, tool or conversation text, and for a hook's stdout and stderr within one call (r21).
    - Tests: a behavioral test passes `TRAVERSAL_SESSION_ID` and checks `plant_traversal_canary` (`claude/.claude/tests/helpers.py:1117`, `:1127`) at every adapter path-building site. Fixture builders give `session_id` and `conversation_id` distinct values and include an id the grammar rejects, such as one ending in a newline (ciso R13, sdet R9, chr 10; r2 sdet R12; r3 sdet R2, sdet R8, cd R2).
11. **Invariant.** The installer's targets and recorded commands are fixed and checked:
    - it derives every write target from the generator's fixed output set, never from the manifest. The set spans `~/.cursor/` and the routed config dir (Installer), and this requirement's symlink, ownership and containment checks apply to targets under both (r21);
    - a delete target is in that set, or is a manifest candidate that lies inside one of the set's fixed directories, `~/.cursor/agents/` and `~/.cursor/rules/`, is a regular file and no symlink, and matches the manifest's hash. "Inside" is decided by path component on the resolved path (r7 ciso R6). That lets `uninstall` reach a generated file for an agent or rule the harness no longer has (r5 plat R3);
    - it refuses symlinks and matches `--config-dir` with `fullmatch`;
    - the config dir must be user-owned, not group- or world-writable, and outside any work tree, at `install` and again when `uninstall` reads it from the record (r6 plat R6);
    - it records stable stow paths, never a worktree path;
    - it records bash, jq, git, `ps`, `sha256sum`, `timeout` or `gtimeout`, and `gh` when present, as `command -v` reports them after checking each floor, and python3 as the `sys.executable` it resolves to from `~/.cursor/` (Installer);
    - `check` re-tests each recorded tool against its floor;
    - it quotes every command token, and generated paths have a stated character set and quoting rule (ciso R12, plat R5, be R15; r2 chr 7, chr LOW; r3 plat R3, cd R2; r4 plat R7).
12. **Invariant.** `install` refuses until every capability its generated entries need exists, and each entry declares its prerequisites. The generator emits a documented parity exception, never a gate whose prerequisite is absent, and a generate-time test checks it. Reverting a phase needs `uninstall` first and goes in reverse phase order, except for Phase 4's Claude-path files (Phases), and the doc says so (plat R3, be R6; r2 plat R10; r3 plat R6; r4 plat R3).
13. **Invariant.** Generated commands carry a contract version that the code they run checks, and a mismatch takes requirement 3's stale-install outcome (r21). Adapter ownership of an entry never depends on that version, by the Installer's command grammar (r9 ciso R2). `check` enumerates its inputs, and a missing hook reports a stale install rather than an opaque deny (plat R4, be R6; r4 plat R1).
14. **Invariant.** Phase 4's `_lib.sh` and session-hook changes leave the Claude Code path unchanged for every process that keeps `CLAUDECODE` (sdet R2; r2 sdet R12, chr C; r3 sdet R5, plat R7, ciso R1; r4 ciso R1, sdet R7; r6 ciso R4):
    - a process started with `CLAUDECODE` removed, under a fake `Cursor` host and with no live registered ancestor, takes the adapter pair, which requirement 29's cooperative tier accepts (Identity and routing);
    - the existing tests that pin it stay unedited, because Phase 4's file list leaves them out (Critical files). Those include the direct four-argument rows of `_lib_worktree_lock_is_mine`, which the new argument leaves green (row 186; r5 ciso R1, be R1);
    - `test_lib_cursor_identity.py` holds, as a literal, a snapshot of the touched functions' results over a fixed corpus, normalized to ancestor depth and session id;
    - the corpus is a fake config dir, a PATH-stubbed `ps` tree, requirement 31's decoys, and the Claude Code payloads the existing `_lib_parse_tool_input_or_deny` and `_lib_emit_allow_with_context` tests use;
    - its Claude Code cases, each with `CLAUDECODE=1`, are:
      - every adapter variable unset;
      - a live registered ancestor and caller-set adapter variables, once with `CLAUDE_CONFIG_ADAPTER_SESSION_ID` equal to the registered session id, so the lock rule stays the PID-first one;
      - adapter variables set, a fake ancestor named `Cursor` and no registry entry, which returns 2 (OD11(d); r15);
      - a payload carrying a predicate key under a fake `claude` parent, for the allow emitter, which keeps today's allow (r7 be R2);
    - its expected-different cases, listed by id, are the Cursor-shaped ones (r6 cd R1):
      - `CLAUDECODE` unset;
      - a fake `Cursor` host;
      - no registered ancestor;
      - an adapter variable set;
      - an untranslated payload;
      - the lock rule called with `session-keyed` on the three rows requirement 30 lists by id (r7 be R3);
    - it also runs each touched function once under `set -u`, with `INPUT`, `CLAUDECODE`, `CLAUDE_CONFIG_ADAPTER_SESSION_ID` and every optional argument unset, and asserts no unbound-variable abort (r6 be R2);
    - the snapshot is generated from the `_lib.sh` at Phase 4's merge-base, before any edit. No earlier phase edits `_lib.sh`, so that base is the pre-adapter file;
    - the test takes a hooks-directory parameter. Phase 4's PR runs it once against a merge-base worktree's `claude/.claude/hooks/`. That run's one pass condition is that every Claude Code case passes and exactly the listed expected-different cases differ, which shows the snapshot came from the pre-adapter file (r7 be R3);
    - the snapshot names no function list, so a later, unrelated `_lib.sh` function does not fail it. It stays in the standing suite, where a later change to a touched function updates it on purpose.
15. **Invariant.** Every native entry's prologue, and the shim's lib it runs, meet these:
    - it fixes PATH from the install record, holding `timeout` or `gtimeout`, `git`, `ps`, `sha256sum`, `python3`, and `gh` when recorded (requirement 11). PATH is each recorded tool's directory, in the record's order, then `/usr/bin`, `/bin`, `/usr/sbin` and `/sbin`, and nothing else (r21);
    - it reads that record with the `jq` its inherited PATH finds, which Cursor's observed hook PATH holds (O12). A missing `jq` or a failed read takes requirement 3's install-record outcome (r5 plat R6);
    - it checks the bash floor before its first jq by sourcing `claude/.claude/hooks/_config.sh`, so the shim defines no floor predicate of its own (row 167);
    - its lines above that source parse on bash 3.2, which `test_bash_floor.py`'s tripwire scans once Phase 2 adds the shim's pre-source region to it;
    - a failed `cd` into the resolved directory takes requirement 3's unenterable-directory outcome, never a fallback directory (r21);
    - it bounds its child and kills it on TERM;
    - it removes `CLAUDECODE`, `CLAUDE_CODE_ENTRYPOINT` and `CLAUDE_PID` from the hook's environment (row 178; r4 ciso R4);
    - its log records are single lines of at most 1024 bytes with the newline, macOS's `BUFSIZ`, so one `printf` to an append-mode descriptor stays one write (row 188). Each is appended with no lock (r5 plat R4, cd R3);
    - the log line's field grammar, stated once, is the plan's choice: a line is `name=value` fields separated by one ASCII space, ending in the newline. A value holds only characters of `[0-9A-Za-z._:+-]`, so a field splits at the next space. No field holds free text. A value outside that set is replaced by the overflow token, as a value past its maximum is, and a generate-time assertion checks every registration label `generate` emits against the set;
    - each log field has a stated maximum length, and the maxima with their separators sum to under 1024 bytes, so no line reaches the cap. A value past its field's maximum is replaced by a fixed overflow token, never cut (r6 plat R4);
    - the maxima live in one table in the shim's lib, which the sum assertion and `TestShimLogConcurrency` both read. A generate-time assertion checks that every generated registration label fits its field, and `status` names the overflow token as an anomaly rather than counting it as a registration (r7 plat R5). The run-mode field's row is in the block Phase 2 creates in `_cursor-adapter-lib.sh`, and Phase 3 first writes the field, in `cursor-adapter.py`. It holds a closed label, not the line's text: `unreadable`, or `unreadable+acceptance-only` while Phase 3's acceptance-only record holds. The full text stays on the channels that print it, `additional_context` and the terminal. The field's maximum covers the longer label, which a Phase 3 row asserts (r21);
    - the table and the overflow token are a fixed-format block of plain `NAME=value` assignments in `_cursor-adapter-lib.sh`. Bash reads them as variables with no jq, and `cursor-adapter.py` parses the block with one fixed pattern. `TestLogFieldTableParity`, in Phase 3 beside `cursor-adapter.py`, sources the lib and compares its values with Python's parse, as `TestSessionIdGrammarParity` does for the id grammar (r8 plat R6; r9 plat R6). It enumerates the names from the bash side, fails on any line in the block the pattern does not match, and asserts the two name sets equal as well as the values (r10 sdet R7). A further Phase 3 `TestLogFieldTableParity` row runs the sessionStart `check --hook-env` entry with the acceptance-only record present and reads the line with `status`'s reader. It asserts the run-mode field equals `unreadable+acceptance-only`, that value fits the field's maximum, and the line stays under 1024 bytes. The row also asserts the overflow token consists only of characters in `[0-9A-Za-z._:+-]`. A second assertion drives the Python writer, which writes the run-mode field, with the longest run-mode label and every other field at its maximum, asserts each field's value, and asserts the whole line, newline included, stays under 1024 bytes (r21). Phase 4 adds the host-walk field's and the `cursor_version` field's rows to the block and a sibling `TestLogFieldTableParity` row in `test_cursor_adapter_install.py`. That row runs the same entry once for each of `found`, `not-found` and `claude-first`, and once with a 64-character version and once with `other`. It reads each line with `status`'s reader, and asserts each field equals its value, each value fits its field's maximum, and the line stays under 1024 bytes. The Python-writer assertion then sets both fields to their maximum as well (r21 sdet R7; r21 plat R9);
    - the Python writer appends each of its lines as one `os.write` to an `O_APPEND` descriptor, as the shim's single `printf` does (r9 plat R6);
    - fire counts come from those lines, never from a read-modify-write counter (requirement 19);
    - no byte of a payload field that carries file, tool or conversation text (such as a read file's `content`, `tool_input` values, a Shell `command`, `tool_output`, `error_message`, and prompt and response text), and no byte of a hook's stdout or stderr within one call, is ever at a path name, because a beforeReadFile payload holds the read file's `content` and a postToolUse payload holds the tool's output (row 156). A named copy would be a credential at rest that no credential gate's path match covers (r21):
      - the shim holds Cursor's payload in memory and gives the hook a pipe as stdin, as `printf '%s' "$payload" | <hook> &`, so the hook stays a background child it can kill on TERM;
      - the call's outcome reads the hook's own exit status. `pipefail` is off from that pipeline's launch to its `wait`, so `printf`'s SIGPIPE, when a hook exits without reading all its stdin, never turns an exit 0 into a deny;
      - it never passes a payload through a here-string or a heredoc, because bash can write a large one to a temporary file;
      - the hook's stdout and stderr go to files the shim creates in the state dir and unlinks before the hook starts, reading them back through descriptors it opened first. A SIGKILL, as on Cursor's timeout, leaves at most an empty file;
      - requirement 33's held text and the stored commit-stall reason (Twins) are the persisted hook output. A hook composes it, and it may carry values the hook derived from the payload, such as a repo path or a skill name. Requirement 33 bounds the held text. The Twins commit-stall bullet clears the stored reason when its `stop` emits it. This rule covers neither;
    - whether Cursor itself saves hook input to disk is [unverified]. An architect review relayed that the hooks doc names a "Hooks output channel" for debugging, which this plan has not checked against the saved copy. One search of Cursor's log and state directories for a canary after a denied read would settle it, and no acceptance cell runs it (r21);
    - the sessionStart `check --hook-env` entry, once per conversation, and `check` maintain the state dir, so no shim call does:
      - they rotate the log past 16 MiB by renaming it to the one older file kept. A shim still holding the old descriptor writes its line there;
      - a rotator takes a non-blocking exclusive `fcntl.flock` on a fixed lock file in the state dir, skips rotation when another rotator holds it, and checks the size only once it holds the lock (r6 plat R4; r7 plat R4);
      - the sessionStart entry alone sweeps first-call marks older than 7 days. `check` does not sweep marks, because it writes no check line and a swept mark would then leave a live conversation's next first line unpaired (r11 plat R3);
    - every jq and ps call runs under a capped call of `_lib_capped`'s shape (`_lib.sh:50`);
    - Tests (plat R7; r2 plat R10, chr 7, chr 8; r3 plat R4, sdet R8, cd R2; r4 plat R5, cd R3; r5 cd R3):
      - `TestCappedCallParity`: behavioral, over a sleeping child's cap-fire statuses 124, 143 and 137, with neither `timeout` nor `gtimeout` on PATH, and with a `timeout` that rejects `-k`;
      - `TestShimLogConcurrency`: 40 concurrent shims, or the number of shim entries one Shell call starts if OD5's count is higher, writing lines with every field at its maximum length, lose and tear no log line. From Phase 3 the writers include one Python-written heartbeat at the maximum lengths (r9 plat R6). 40 concurrent first calls for one conversation write exactly one first line (r9 plat R5);
      - `TestShimPrologue`: a missing `jq` and an unreadable record each take requirement 3's install-record outcome;
      - `TestShimPrologue`'s canary row covers the payload's pass to the hook, and the per-call hook-output arm while the hook runs and after a SIGKILL to the shim mid-call, by two checks (r21). The no-here-string rule on the shim's own steps is reviewed, not tested, because a here-string's temporary file exists at a name only during the write, a window too short for any search to hit reliably. The shim's own steps after the hook exits are likewise reviewed, not tested. A beforeReadFile fixture's `content` and a postToolUse fixture's `tool_output` each carry a canary string, in a fixture of at least 1 MiB, larger than a pipe's capacity:
        - the probe hook saves nothing and prints the canary on both stdout and stderr. It stays alive until a live search of the test's fresh `TMPDIR`, home and config dir and a second search after a SIGKILL to the shim mid-call have both run. Neither search finds the canary at a name;
        - the probe also asserts that its stdin is a pipe (`[ -p /dev/stdin ]`), because a here-string's temporary file exists at a name only during the write, so a search for a name cannot reliably see it, and a here-string or heredoc payload of that size makes stdin a regular file;
      - `TestLogRotationAndSweep`, in Phase 3 (r6 plat R4; r7 sdet R3, sdet R4, cd R11):
        - a rotation under concurrent appends loses no line;
        - with the test itself holding the lock and the log past the threshold, a rotator skips, and the older file keeps its full content. A rotator that takes no lock fails this case;
        - with the log already rotated and small, a rotator that takes the lock renames nothing;
        - the size check's place after the lock is reviewed, not tested, because no process-level test can interleave it without a seam (r8 sdet R7);
        - a first-call mark just over 7 days old is removed, one just under is kept, and a file in that directory that is not a mark is left alone, with mtimes set relative to one captured `now`, with margins of seconds and no sleep (r8 sdet R7; r10 plat R3).
16. Stop's forced continuation stays bounded. Each Stop twin entry sets `loop_limit: 1`, which Cursor honors on a native entry (row 130), and emits only when `status` is `completed` and `loop_count` is 0, with a test (be R10; r3 be R9).
17. `field-table.json` covers every field the hooks read (rows 12, 124), including the Claude-cased `hook_event_name`, `prompt_id`, `permission_mode` and `transcript_path`, and sets SubagentStart's `session_id` by Translation step 3's one rule, the call's session id, which C5's subagentStart records put in one equality class with `parent_conversation_id` (be R9; r21).
    - Its coverage test derives the set of fields the hooks read by scanning their jq field reads, `_lib_parse_tool_input_or_deny`'s and the raw-stdin hooks' (row 12), never from a hand list (r5 sdet R5).
    - The scan joins backslash-continued lines. A self-test runs it over the hooks' known multi-line reads, such as `announce-approved-plan-path.sh:104-105`, and every hook that reads `$INPUT` must yield at least one field (r6 sdet R6).
    - The scan's output for `_lib_parse_tool_input_or_deny` must equal the field list `test_lib.py:403` and `:438-445` already pin, an independent oracle. The self-test pins, by literal, what the scan extracts from the piped `announce-approved-plan-path.sh:104-105` program and from one hook with several field reads (r7 sdet R8).
    - It carries Repo resolution's rule and the Write-to-Edit rule, and drops every key of requirement 38's predicate set (Translation).
    - `translate-output.jq` holds Translation step 5's table, which writes `agent_message` and `user_message` for every deny and defines non-JSON stdout (be R11; r3 be R1).
    - It lists as dropped a `systemMessage` on postToolUse, stop and sessionEnd, which have no `user_message` (r2 be R11).
18. Install and uninstall are atomic and idempotent, and adopt their own earlier files (Installer; plat R6, sdet R15, chr; plat R17; r2 chr LOW; r3 plat R2; r4 plat R1, sdet R9; r5 plat R3, plat R6, cd R9; r6 plat R1, plat R6; r9 ciso R1, ciso R2, ciso R6, be R1):
    - a refusal names the conflicting path and the remedy;
    - the pre-image is durable before `hooks.json` changes, is never written while an adapter-owned entry exists, and is replaced when none does;
    - adapter ownership is the Installer's command grammar, whatever the contract version or recorded bash, and an entry that holds the shim path outside it is foreign (r9 ciso R2);
    - `uninstall` removes the entries first and the state last, runtime state included;
    - with no edit since install, `uninstall` restores a merged file's pre-install bytes;
    - a second install with another config dir refuses;
    - `install` offers a merge path for the engineer's own `~/.cursor/hooks.json`;
    - from Phase 4, `~/.cursor/sandbox.json` takes this requirement's rules as `hooks.json` does: a durable pre-image, a merge that keeps the engineer's own keys and paths, and a byte-for-byte restore when the file is unedited since install (Installer; OD11(b); r15). Its ownership is the manifest's record of the paths the install added, never the current list (r17 plat R4, sdet R4, be R4, ciso R6);
    - new runtime directories are gitignored;
    - Tests:
      - a crash between each pair of install writes, then `uninstall`, leaves the original bytes;
      - a crash between each pair of `uninstall` steps, then `install` and `uninstall`, leaves the original bytes;
      - an interrupted `uninstall` that left the pre-image, then an edit to `hooks.json`, then `install` and `uninstall`, leaves the edited bytes;
      - install twice, then `uninstall`;
      - install under contract version N, then N+1, then `uninstall`;
      - frozen literal entries from each shipped phase's generator parse as owned in the current grammar, so a grammar change that rejects an earlier phase's entry fails (r9 ciso R6);
      - every owned entry removed and one non-owned entry with no shim path added, then `install`: the entry is printed by event and position before the pre-image changes (r9 ciso R1);
      - a live `hooks.json` that does not parse, or is not an object of event arrays, makes `install` refuse naming it, with `~/.cursor` byte-identical, and `status` and `check` name it as its own finding rather than an empty comparison. A missing one is named by all three (r9 ciso R1, be R1);
      - `uninstall` with an agent file unparseable, and with a rule file unparseable, still removes the entries, the runtime state and the record. A generated file that matches the manifest's hash is removed, and one that fails it is named and kept (r9 be R1; r10 be R1);
      - `uninstall` with a live `hooks.json` that does not parse, or whose `hooks` member has an unexpected shape, refuses naming it, with `~/.cursor` byte-identical (r10 be R1);
      - an edit to `hooks.json` between `install`'s merge and its step-4 rename, injected at the crash-injection seam, aborts the rename, names the file, and leaves the edit in place (r10 be R1);
      - install with one recorded bash path, then another, then `uninstall`;
      - `uninstall` removes a manifest candidate for an agent the harness no longer has, and keeps one that is a symlink, is not a regular file, lies outside the fixed directories, reaches them only through a `..` component or a sibling-prefix directory such as `agents-x/`, or fails its hash (r7 ciso R6);
      - install, remove an agent from the harness, install again: the agent's file is gone after the second install, and a later `uninstall` leaves the original bytes (r6 plat R1);
      - install, remove an agent from the harness, then edit its generated file so its hash fails, or replace it with a symlink, and install again: the file is kept, the new manifest still lists it, and `uninstall` names it without removing it (r7 sdet R9);
      - `uninstall` with `install.json` missing;
      - `uninstall` with `install.json` malformed, and with a recorded config dir whose `cursor-adapter-state` is a symlink: each names the runtime state and removes nothing there (r6 plat R6);
      - the second-install refusal;
      - `install.json` written by the installer and read by the real shim prologue;
      - in Phase 4's `TestSandboxFile`, over a `sandbox.json` that is absent, the engineer's own, and unparseable (r15):
        - install then `uninstall` leaves the original bytes, or no file where none existed;
        - a crash between each pair of install and `uninstall` writes, the listed directories' creation and the two step-1 edits included, then `uninstall`, leaves the same (r17 plat R6). The pending record makes this hold at every boundary (r19 plat R1, sdet R4);
        - a kill after the `sandbox.json` rename and before the manifest, then `uninstall`, leaves the original bytes or no file. The same kill, then `install` and `uninstall`, leaves the same, and the second `install` keeps the first's pre-image (r19 plat R1, sdet R4);
        - an install with the grant pending, an engineer's edit to `sandbox.json`, then an install that writes the grant and an `uninstall`, leaves the edited bytes (r19 plat R1);
        - an engineer's edit after install survives `uninstall`, with only the recorded paths removed;
        - the unparseable file makes `install` refuse, with `~/.cursor` byte-identical;
      - further `TestSandboxFile` rows (r17 plat R4, sdet R4, sdet R7, be R4):
        - wrong shapes refused with `~/.cursor` byte-identical: a non-object, a non-list `additionalReadwritePaths`, and a list holding a non-string;
        - the engineer's own file already lists one, and then all, of the adapter's paths: install then `uninstall` keeps their entries, and leaves the original bytes where the file was untouched;
        - install twice, then `uninstall`;
        - install under list L1, then install and `uninstall` under list L2, one directory added and one dropped, and under a build that writes no grant: nothing from L1 remains, and nothing of the engineer's is lost;
        - a populated listed directory survives `uninstall`, and an absent one is created by `install`;
        - a listed directory under a symlink is refused (ciso R3);
        - the grant is not written while its prerequisite entry is absent, and `status` lists it pending. The row builds its own `parity-exceptions.json` fixture tree through a seam and never reads the repository's file, so Phase 5a's flip leaves it green (be R2; r19 be R2, sdet R5);
        - the comparison names an added path, a removed one and a changed key by count and label, names nothing for an intact file, and shows an engineer's later own path as a count. A nonce placed in a key and in a path reaches no report;
      - in Phase 5a's `TestSandboxGrantWritten`, on the repository's own tree once the permissions gate's entry is non-pending: `install` writes the grant, `status` no longer lists it pending, and `uninstall` restores the file (r19 be R2, sdet R5);
      - in Phase 5a's `TestWorkspaceSandboxReport`, in `test_cursor_adapter_install.py`, because the report is Phase 5a's, the workspace report covers zero, one and several roots, an unreadable root, a file past the cap, an unparseable file, a benign file, and one listing each of `~/.cursor`, the routed config dir and `$HOME` (ciso R4, plat R7). An entry naming a tool directory outside `$HOME` counts as widening, and entries inside a workspace root and under `$TMPDIR` do not (r20). Further rows (r21 sdet R6):
        - a sibling-prefix of a root (`/work/repo-evil` beside `/work/repo`) and of `/tmp` (`/tmpx`) each widen;
        - a symlink inside a root that points outside every root widens;
        - the base for the `$TMPDIR`, `/tmp` and `/var` rows is `tmp_path` where its `realpath` lies outside `/tmp`'s `realpath`, and otherwise a directory the test creates under `/var/tmp` and removes at teardown. pytest's default base is under `/tmp` on Ubuntu CI, and `pyproject.toml` sets no `basetemp`, so a tree built there lies in the live `/tmp` exemption whether or not `$TMPDIR` is resolved. A row whose base cannot be made, because `/var/tmp` is absent or unwritable or its `realpath` lies under `/tmp`, skips with the reason `absent: no base outside /tmp` (r21 sdet R4, plat R2);
        - each of those rows asserts its own placement precondition before the report's outcome, and fails loudly when it does not hold. Every path the row does not place in an exemption on purpose lies outside `/tmp`'s `realpath` and outside the `realpath` of the `$TMPDIR` the row passes. For the `/var` row, that `$TMPDIR` lies under `/var`'s target (r21);
        - an entry spelled through a `$TMPDIR` that is a symlink the test creates under that base does not widen. The same entry with `$TMPDIR` unset widens. Both rows run wherever the base exists, Ubuntu CI included;
        - an entry under the real `/tmp`, spelled with no symlink, does not widen. The row runs wherever the base exists;
        - an entry spelled through the literal `/tmp` form, and one through the literal `/var` form, do not widen. These are the only macOS-only rows of the group. Each probes its own precondition and skips with its own reason, `absent: symlinked /tmp` or `absent: symlinked /var`, when the host's own `/tmp` (respectively `/var`) is not a symlink, as on Ubuntu CI;
        - an entry equal to a root itself does not widen;
        - a root's parent and `/` each widen;
        - an unset, empty or relative `$TMPDIR` adds no exemption, and neither does one that is an ancestor of `$HOME`, `~/.cursor` or the routed config dir (r21);
        - a workspace root equal to `$HOME`, to `/`, a proper ancestor of `$HOME`, equal to `~/.cursor`, equal to the routed config dir, and a root containing `~/.cursor` or the routed config dir, each gives no exemption, so an entry inside it counts as widening (r21);
        - a root nested under `$HOME` still exempts an entry inside it;
        - `/tmp` is dropped when it is an ancestor of `$HOME`, so an entry under `/tmp` widens. An unset, empty, relative or unresolvable `$HOME` or routed config dir drops each exemption the same way;
        - a root equal to `$HOME`, and a root that is a symlink to it, whose `.cursor/sandbox.json` is the adapter's own user-level file: the report skips the file by file identity, and counts it as neither present nor widening. A negative control: a root whose `.cursor/sandbox.json` is a byte-identical copy of the adapter's user-level file at a different file identity counts as present, and as widening where its content would, so a skip by content or by name fails;
        - the rows above for `$TMPDIR` and `/tmp` place the fake `$HOME`, `~/.cursor` and the routed config dir outside both `/tmp` and `$TMPDIR`, in the base above, and assert it. One further row places `$HOME` inside `$TMPDIR`, and one places it inside `/tmp`, each showing the drop;
        - a case variant of `~/.cursor` widens. A case variant of a root or of `/tmp` folds onto it under the rule's ASCII fold and does not widen, whatever the volume's case sensitivity (r21). That is the fold's stated residual, on a case-sensitive volume, for a report that is report-only (row 323);
        - a top-level array, a string `additionalReadwritePaths`, an object one, and a list holding a number or `null` each count as widening or unparseable, never zero, and the log line and the mark still land (r21).

        The report also covers entries spelled with `~`, `$HOME`, a relative path and a `..` part, each counted as widening, and a stalled first root, after which the log line and the mark still land (r19 ciso R5, plat R5). A file holding only one other key C6 lists, for each such key, and one holding an unknown key, each count as an `unrecognized key` and not as widening (r20 ciso R2; r21 be R14);
      - `TestSandboxWritableDirs`, a behavioral test, not a scan (r17 sdet R1, be R3; r19 sdet R3):
        - a temporary routed config dir is writable only in the listed directories, by mode bits. Its existing root-level files are read-only, and the test skips when run as root;
        - each identity consumer's write path runs against it under a fake `Cursor` host. For `marker.sh`, every kind in its own `WRITE_SKILLS` and `ACTIVE_BYPASS_SKILLS` arrays is driven, the list derived from those arrays, and the `.plan-review-routing-read.d` write too. `pr-diff-against-base.sh` runs with `--record` and `--diff-file`. `review-ledger.sh`, `handoff-record-conversion.sh`, `review-pr-acquire.sh` and `marker.sh clear-stale` run as well;
        - each row asserts that its artifact exists in its directory, never only the exit status, because `pr-diff-against-base.sh:104-107` keeps its exit code on a failed artifact write. The `.handoff-nudge.log` append the Installer leaves out by name is the one expected miss;
        - the consumer set is derived from which scripts call the resolver or source a library that does, and a meta-assert fails a derived consumer with no row;
        - a listed entry that is a symlink, or whose resolved path leaves the routed config dir, fails (ciso R3).
19. A bounded log with no payload values, a canary that dedupes tool names and caps their grammar and length, and a `status` subcommand. The canary and the staleness check are documented as detection only (plat R15, sdet R16, ciso R13, ciso R15).
    - The log keeps a per-call latency record for OD5's budget, and the canary keeps a reported-up-to marker so it does not repeat (r2 plat R10).
    - Each shim call logs one line naming its registration, and `status` derives each registration's fire count from those lines (requirement 15). It flags every Shell-selecting registration with no fire since the last install while the log holds a Shell call (r3 sdet R9; r4 plat R5).
    - `status` reads the log and its one rotated file, so a count since the last install covers at most those two files, and `status` says when the older one starts after the install (r5 plat R4).
    - `status` cannot tell a Cursor that stopped loading `hooks.json` from no Cursor use, because every log line comes from a shim entry. The doc states that limit, and requirement 22's Cursor-update checklist answers it (r5 plat R6).
    - The tamper comparison, which `status`, `check` and the sessionStart entry share, compares the full content of each entry `generate` emits from the registrations with `~/.cursor/hooks.json`'s, in both directions (Installer; requirement 39; r6 ciso R1; r7 ciso R4; r8 ciso R1, be R1). It names:
      - each emitted entry that is missing or changed;
      - each owned entry, by the Installer's command grammar, that equals no emitted entry, a duplicate included;
      - each foreign or non-owned entry the pre-image file does not hold. When no owned entry remains, `status` and `check` also print each non-owned entry the pre-image does not hold, by event and position, as `install` does before it replaces the pre-image (r9 ciso R1);
      - once the grant is written, any difference between the live `~/.cursor/sandbox.json` and the merge of its pre-image with the paths the manifest records, so an added path, a removed one or a changed key shows. The reports name it by count and label only, as the Installer's report rule has it (OD11(b); r15; r17 plat R4).
    - A `hooks.json` that is missing, does not parse or whose `hooks` member is not an object of event arrays is its own finding in `status`, `check` and the sessionStart entry, never an empty comparison (r9 ciso R1; r10 be R1).
    - The sessionStart check's limit: it lives in the file it checks, so removing that file or that entry silences it, and only `status` or `check`, run by the engineer, then shows the change. The doc states it.
    - Tests, on two layers (r7 sdet R6; r8 ciso R1, ciso R3, sdet R4, cd R3; r9 sdet R4, cd R3, cd R5):
      - the unit layer calls the comparison function in-process, with no subprocess, over every generated entry, the sessionStart `check --hook-env` entry included, and every case below. It carries the "every entry" claim;
      - the CLI layer runs each case once on one entry of each shape, a matcher-bearing fail-closed entry, a matcher-less one, an informational one and the check entry, once per surface;
      - the cases:
        - the entry removed;
        - its matcher altered, or added where it has none;
        - its `failClosed` flipped, or set where it has none;
        - its `timeout` altered;
        - its `command` given a `|| true` suffix;
        - its `command` given a changed argument;
        - its `command` given a changed recorded bash path;
        - the entry moved to another event;
        - an unknown key added;
      - `status` and `check` name each case;
      - `check --hook-env` names each case on another entry. Its own entry's cases are asserted through `status` and `check` only, because the entry cannot report its own removal;
      - a foreign entry that holds the shim path but not the grammar, and a duplicated owned entry, are each named by `status`, `check` and `check --hook-env`;
      - an intact file is named by none.
    - Print tests, one row per item of the Installer's print set, each printed by `install` and by `uninstall` before they act (r10 ciso R2, sdet R3):
      - a foreign entry;
      - a duplicated owned entry;
      - an owned entry with its matcher, its `failClosed`, its `timeout`, its key set or its recorded bash path changed;
      - a non-owned entry the pre-image does not hold, when `install` replaces the pre-image and when `uninstall` skips its restore;
      - with every owned entry removed and one non-owned entry added, `status` and `check` print it by event and position;
      - a `type: "prompt"` entry with no `command` is kept as non-owned and, when the pre-image does not hold it, printed (r11 be R3).
    - Print exclusions, each printing nothing from `install` and from `uninstall` (r11 sdet R4):
      - an entry that differs only by the contract-version token;
      - an entry that differs only by the prior install record's bash path;
      - an intact file;
      - a non-owned entry the pre-image holds.
    - Report-content tests (r9 ciso R3, sdet R6; r10 ciso R3, sdet R3; r11 ciso R4, sdet R3, cd R13):
      - a foreign entry holds a nonce at the start of its `command`, and an owned entry holds one at the start of its `matcher`. Each nonce is absent from `status`, `check`, `check --hook-env`'s `additional_context` and the log line. Each is present in what `install` and `uninstall` print for that entry and field, because the print set requires both entries and bounds each field separately;
      - a foreign entry and an event key each hold an ESC byte, a C1 control, a bidi override, a newline and a 64 KiB string:
        - `install` and `uninstall` print them escaped, and each cut value carries its full-length marker;
        - `status`, `check` and the sessionStart report count the key as `other`;
      - a foreign entry that copies the generated launch guard and differs only past character 200 of its `command` prints with the cut marker and its full length;
      - 21 entries of mixed classes print 20, filled in class order with foreign entries first, and the remaining class's count;
      - with 10,000 foreign entries, `install` prints 20 and a count of the rest.
    - `status` flags the sessionStart entry as silent when two or more shim first lines follow the entry's last check line in the span since the last install (r7 plat R1; r8 plat R1, cd R2; r9 plat R2, sdet R3; r10 sdet R1, plat R3, plat R6, cd R13):
      - the shim writes a first line only when its own create of the conversation's first-call mark wins (Installer). When the sessionStart entry runs first, it creates the mark, and that conversation writes no first line at all, whatever the interleaving with other conversations;
      - one first call that beats its own check writes one shim first line, which the rule tolerates;
      - each of these is a known trip the doc names, and a later conversation's check line clears it:
        - two conversations in a row with no sessionStart, such as ones open before install and resumed after;
        - two live conversations whose marks the 7-day sweep removed, each calling before a new conversation starts;
        - two first calls that both beat their checks, read before those checks land;
      - `status` also flags a log past the rotation threshold (r11 cd R13):
        - it can trip during one long healthy conversation;
        - the next conversation's check clears it by rotating the log;
        - the doc says so;
      - the rows below are log-fixture rows on `status`'s rule, so check-then-first-call orders that write the same log are one row (r11 sdet R5):
        - each conversation's check, then its first call, in any interleaving: quiet;
        - all first calls beating their checks, read before the checks land: flagged, and quiet once they land;
        - one first call beating its own check, the rest in sequence: quiet;
        - the entry's fire suppressed from one conversation on: flagged once two shim first lines follow the last check line;
        - one conversation with no sessionStart followed by a healthy one: quiet, and two in a row: flagged;
        - two swept marks of live conversations calling before a new conversation starts: flagged, and quiet after that conversation's check;
        - a check line written just after a rotation stays in the current file, so a span edge never drops the latest one;
      - integration rows run the real sessionStart entry and the shim in fixed sequential orders (r11 ciso R5, plat R2, sdet R5):
        - entry first: exactly one create succeeds, the entry's, and no first line is written;
        - shim first: exactly one create succeeds, the shim's, and exactly one first line is written;
        - the entry killed after its comparison and log line, before its create: the shim then wins and writes one first line. An entry that created the mark first would leave none, so this row fails that order.
    - `status`'s count of `pin-check` denies by cause sits in Phase 3's `TestStatusAndCanary`, with a quiet-path row where a prefixed call counts nothing (r19 sdet R10).
    - Phase 4's `TestSessionStartReports`, with a stubbed host walk (r19 sdet R8):
      - each of the walk's three results reports its own label, and `status` flags only not-found;
      - for `cursor_version`: first sight records and stays quiet, an unchanged value stays quiet, a change reports once and then stays quiet, and two concurrent conversations at a change report once;
      - an unreadable version record reports a change, the outcome that sends the engineer to the checklist;
      - a version string holding a newline, or 64 KiB long, reaches no report and is stored as `other`.
20. Generated `hooks.json` validates against a vocabulary pinned from the hooks doc's per-event schemas and from O1, O2, O12 and O21, and generation is byte-deterministic (sdet R13, sdet R5, ciso R11; r3 sdet R2; r4 sdet R3).
    - Fixtures hold shapes only, built from the key paths and types C5 records, and the test builders inject values.
    - One builder module, `claude/.claude/hooks/tests/_cursor_payloads.py`, builds every Cursor payload both test trees use, from `field-table.json`'s required paths.
    - The same module defines, once per builder spec, its Claude-shaped twin: the Claude payload the gate's own existing test sends, or a literal written beside the spec, never computed from `field-table.json`. Requirement 40's payload check and its reference run both take that one object (r8 sdet R1).
    - One test checks the builders' keys, types and distinct ids, as well as their values.
21. Each acceptance step states its observation, its pass condition and a control partner (sdet R6, sdet R7, plat R14; r2 plat R10; r3 plat R9; r4 plat R4):
    - its trigger is inert on a missed deny, and a trigger drawn from the gate matrix carries the matrix's `inert` flag;
    - acceptance includes a Claude Code regression run with the adapter installed;
    - it includes a check that no output auto-approves;
    - it includes one forced failure per requirement 3 class;
    - it uses the stowed checkout, so the imported copies and the shim load the same `_lib.sh`.
22. The Delete, Write, `if:`, selection, portability and PR-record rules hold, one theme per bullet (r3 cd R3; r5 cd R11):
    - Delete: a Delete becomes `rm` only with a quoting matrix that the production splitter checks. In Phase 2, `TestDeleteTranslation` asserts argument identity by running the translated command under a real shell parse, with `rm` stubbed to print its argv, and comparing the one argument to the original path. It asserts per case id the fragment count `_lib_split_fragments` yields and the command word `_lib_fragment_command_word` returns: `rm`, one fragment, except the `$(...)`, backtick and newline ids, which the quote-blind splitter over-splits, an accepted over-split. It feeds the shim's recorded payload, with a review-only `agent_type` added by the test, to `deny-reviewer-tree-mutation.sh` and expects allow, because that hook's Bash arm does not inspect `rm` (its header, Known gaps). The same payload with `command` replaced by `sed -i s/a/b/ x` must deny, as the control that the hook parsed the payload. The reviewer-Delete window stays disclosed until Phase 5a. Phase 5a's `TestAgentAttribution` runs the same case ids through `gate-agent-tools.sh` (r21). The case ids come from one module-level list that a test asserts equal to this set: a path holding a space, a single quote, a double quote, `$(...)`, a backtick, a newline, a leading `-`, glob characters, and non-ASCII text (sdet R11; r2 sdet R12; r4 sdet R5; r5 sdet R3).
    - Writes: multi-file writes translate every path, create and edit map distinctly, and the U+001F field-shift deny holds after translation (sdet R11; r2 sdet R12).
    - `if:` entries: all four get a non-matching-command test (row 14; chr).
    - Test selection: Phase 2 adds a reverse row so that any path under `claude/.claude/scripts/cursor-adapter/` selects the hooks tests, and rows for the builder module, each with a `test_select_tests.py` case (row 180; r4 sdet R3, sdet R4). `select-tests.py` gains the reverse rules in the phase whose scripts test first reads agents, rules, `CLAUDE.md` or docs (row 25; plat R12).
    - Portability: macOS-only assertions skip elsewhere and run locally (plat R18).
    - PR record: each phase's PR body records its CI time delta. The Phase 2 and Phase 4 PR bodies also record a local macOS run of `TestDirectoryPin`'s shell rows, with the shells run as `pytest -rA` prints them and each candidate's probe result, because CI runs those rows under bash alone unless its image has `zsh` (r20). The Phase 5a PR body also records a local macOS run of `TestWorkspaceSandboxReport`'s symlink rows, showing the literal `/tmp` and `/var` rows passed, not skipped, as `pytest -rA` prints them, because Ubuntu CI's `/tmp` is a real directory and those rows skip there. The `$TMPDIR` and non-symlink `/tmp` rows run on Ubuntu CI against the `/var/tmp` base, and the macOS run shows none skipped for `absent: no base outside /tmp`. Each PR body that adds a row skipping on Ubuntu CI records its local macOS run, and Phase 4's body names requirement 38's macOS-only host-walk row in `TestCursorPayloadDecline`. The doc carries a Cursor-update regression checklist, which includes acceptance step 8's observations 1, 2 and 6 and its Control, step 10's cells 1, 5, 6, 7 and 8, step 16, and the sessionStart entry's host-walk report (r2 plat R10; r10 ciso R4; r13; r16; r17 plat R2). This checklist is the one list, and each step that says it joins the checklist points here. The sessionStart entry names it when `cursor_version` changes (Installer).
23. Removed with the guard (row 148).
24. Removed with the guard (row 148). The manifest stays as `uninstall`'s record of the files the install wrote (Installer).
25. Removed with the guard (row 148). `docs/hooks.md` gains no adapter text.
26. Removed with the guard (row 148). Requirement 40's gate matrix covers the import's copies, and requirement 14's snapshot covers the Claude Code path.
27. Each `parity-exceptions.json` entry carries one reason from Registration's closed set. Each entry also carries one `kind` from a closed set, and its key resolves in that kind's source (r21):
    - `registration`: a hook registration, or one arm of one, that `settings.json`'s `hooks` holds;
    - `permission-deny`: a key that `settings.json`'s `permissions.deny` holds;
    - `adapter-gate`: an adapter gate (Twins and adapter gates) that a later phase builds, keyed by its script file name under `claude/.claude/scripts/cursor-adapter/` and with reason `pending-phase`. `TestParityExceptions` fails an `adapter-gate` entry whose script file exists under `claude/.claude/scripts/cursor-adapter/`, so the phase that builds the gate also removes the entry;
    - `cursor-tool`: a Cursor tool name absent from `tool-map.json`, with reason `unsupported` or `declined` (Recovery), which requirement 37's equality test checks.

    `TestParityExceptions` fails an entry with no kind or a kind outside the set, a key that does not resolve for its kind, a duplicate kind-and-key pair, and a reason outside the set. No artifact holds the import's maps, so no cross-check against them exists (row 148; r21).
28. Per-launch cost stays low: one jq program per direction and no `_lib.sh` source in the shim. A test counts the shim's exec calls and jq invocations per call rather than timing it, so it needs no budget and cannot flake on wall time (plat R9, chr 8; r3 sdet R10). OD5's fan-out benchmark measures wall time outside the suite.
29. **Invariant.** Marker and bypass writes accept an identity only from an anchor the agent cannot set, unless the engineer accepts OD7's downgrade. An adapter gate that denied a Shell command setting `CLAUDE_CONFIG_ADAPTER_*`, `CLAUDE_CONFIG_DIR` or `CLAUDE_PID` itself would be a second layer, not the control. No phase builds one, because a Shell-text matcher cannot be made safe (Alternatives set aside; r20 ciso R3, sdet R1; r20; r21 be R9, plat R6).

    So OD7's downgrade has no second layer. Hook context overrides the variable, and an index file's PID must be all digits (ciso R5; r2 ciso R11).
    - Tests, in `TestAdapterIdentity`, in `test_lib_cursor_identity.py` (r20 sdet R1):
      - with PAYLOAD given and `CLAUDE_CONFIG_ADAPTER_SESSION_ID` set to a different, grammar-valid id, `_lib_adapter_session_pair` resolves the payload's id;
      - with no PAYLOAD it resolves the variable. This row pins the accepted residual, a script-side caller under a fake `Cursor` host that sets the variable and takes that id, so closing it is a deliberate change, as requirement 39's non-ASCII fold row is.
30. **Invariant.** A conversation's liveness never rests on an idle timer (ciso R7, plat R8, sdet R8, be R13; r3 be R4; r4 cd R2):
    - under OD7's option (a), a conversation's state is live while the `Cursor` main process it runs under lives (OD11(d); r15). A closed window's or archived conversation's worktree lock therefore stays held until Cursor quits, and `check` names it. Its active-bypass file does not: the reader evicts one after 60 minutes with no gating call, whatever its PID's state (`_lib.sh:2913-2917`; r17 ciso);
    - `check` is the only remover of a conversation's adapter state, and it removes state whose recorded host has exited (r15). No sessionEnd entry exists (Registration), so a closed conversation's state stays until Cursor quits and `check` runs (r21);
    - the active-marker idle rule Claude Code already applies stays as it is (Identity and routing, row 161);
    - tests (r6 be R1, sdet R4):
      - lock ownership across two conversations in one window and across two windows, a dead host process, and PID reuse;
      - in `test_lib_cursor_identity.py`, every `TestWorktreeLockIsMine` row (`test_lib_worktree_collision_guard.py:1962-2093`) rerun with `session-keyed`, each with a stated expected value. The rows that flip to not mine are exactly `:1968` (same PID under another session), `:1973` (an old-format lock) and `:2070` (same PID with a relative config dir), the one list requirement 14 cites. Every other row keeps its four-argument value (r7 be R3, sdet R11);
      - the mirror defines its own five-argument runner and imports `_OTHER_CALLER_PID`, `_FIXED_START_TIME` and `_install_failing_ps` from the unedited module rather than copying them. The shared fixtures come from `conftest.py` (r7 sdet R11);
      - added rule rows: same PID and same session with the PID unregistered is mine, a live registered locked PID under the same session is not, and an empty or unknown fifth argument gives the four-argument result;
      - through `_lib_worktree_collision_guard`, a same-PID lock under another session id is mine with `CLAUDECODE` set and a registered ancestor, and with `CLAUDECODE` unset and a live registered ancestor. It is not mine with `CLAUDECODE` unset and no registered entry, or with the registry read failing;
      - through the guard, two conversation ids on one fake host PID with the first one's lock held: the second one's write is denied after the contended-acquire reread, with the `session-keyed` live-holder text that tells the agent to ask the engineer (r17 plat R3);
      - subagent rows, through the guard and the matrix's import arm: a lock the parent holds, then a child call's write, is mine for the twin and the copy alike; a lock a child takes first is the parent's; a child's marker is read by the parent; a nested child's write into the root's lock denies (r17 be R1);
      - a differential row, in `test_lib_cursor_identity.py`: one root, one child and one nested-child payload, and payloads whose `parent_agent_id` is `null`, `""`, a number, an 85-character id and an id ending in a newline, run through the shim's `session_id`, `_lib_adapter_session_pair` and the Shell rewrite's export. The three agree on every payload, by the one definition Identity and routing states (r19 be R1, sdet R7);
      - `check` rows, with a stubbed host (r17 sdet R8):
        - a live host's state is kept and named, and its locks print with worktree path and session id and a line saying `check` cannot tell a stale lock from a live one (plat R3);
        - a dead host's state is removed;
        - a state dir with two conversations on one host is handled per conversation;
        - nothing is removed from a conversation whose host cannot be read, as on a `ps` failure.

    History: OD7's choice dropped the sketch's keeper, heartbeat, respawn and index clauses.
31. The resolver's order under the shim is chosen with the nested-`claude` hazard named, and tested with decoys against `TestResolveClaudePid` (`test_lib_worktree_collision_guard.py:1911`) and the `_seed_session` marker tests (plat R10, be R16, sdet R4).
    - A live registered `claude` ancestor wins over any adapter-supplied id, and the adapter pair applies only when the walk finds none. The walk's per-call fork cost is measured (r3 ciso R1).
    - Rejection tests cover a wrong-id index, a dead PID, PID reuse, a non-numeric or oversized index, an invalid id component, and a traversal value.
    - Write and read sides agree through `marker.sh` and a marker-gated hook (r2 sdet R12, plat R10, chr 5).
    - Both test trees' conftests gain an autouse fixture that clears `CLAUDE_CONFIG_ADAPTER_*`, as `hooks/tests/conftest.py:461-470` clears `CLAUDE_PID`. `_run_resolve_claude_pid` and every builder that copies the parent environment then see none of them when the suite runs from a Cursor agent's Shell call (row 162; r3 sdet R7, plat R8). Nothing reads a `CURSOR_*` name, so the fixture clears none (r4 sdet R6).
    - The fixture runs before any test body, so its test is a nested `pytest` subprocess. It starts with `CLAUDE_CONFIG_ADAPTER_SESSION_ID` set, collects a probe test under the real conftest, and the probe asserts that a hook environment built through `_build_subprocess_env` lacks the variable. The outer test also runs the same probe under `--noconftest` and asserts the nested report shows the probe's variable check `failed`, naming the variable, which shows the variable reached the nested run. Any other non-zero exit, such as a setup error, fails the outer test (r6 sdet R6; r7 sdet R7).
    - Each test tree has its own file holding the nested run and the probe it collects, so an edit to either conftest selects its own check. The files are `claude/.claude/hooks/tests/test_cursor_adapter_hooks_env_scrub.py` and `claude/.claude/scripts/tests/test_cursor_adapter_scripts_env_scrub.py` (r5 sdet R4).
    - The adapter tests set or remove `CLAUDECODE` per case, because a suite run from Claude Code inherits it and the adapter branch refuses under it.
32. **Invariant.** The credential arms act before the call wherever Cursor gives a pre-call event:
    - a `beforeReadFile` entry denies a read whose `content` matches a shape `_lib_redact_credential_shaped_strings` recognizes, and the deny names the file and the shape, so a fixture holding a fake shape shows as one (row 128). The hooks doc calls `content` the full file (row 156). If C4 shows otherwise, the arm also scans the file at `file_path` up to the shim's cap and denies past it (r3 ciso R9). C4 showed the whole file at 108000 bytes, so the arm takes no fallback, and larger files stay untested (row 128; r12);
    - a preToolUse(Shell) entry denies a command whose output is a credential by construction: an environment dump with no argument, `gh auth token`, `gh auth status` with a show-token flag, `git credential fill`, and a keychain read with `-w`. It matches a tool named by path, such as `/usr/bin/env`, as it matches the bare name (r21);
    - a preToolUse(Grep) entry denies a search aimed at a path `_LIB_CREDENTIAL_PATH_REGEX` matches (row 64);
    - a postToolUse entry on MCP tools returns the redacted output through `updated_mcp_tool_output` (row 46);
    - a post-call arm on Shell and Grep output that still holds a shape adds `additional_context` telling the agent not to repeat, write or send the value, and logs a count. It is detection, not a control (OD10);
    - tests: each arm's trigger, and its allow side, where a clean read, a clean command and a clean search pass (r3 sdet R11).
33. Deferred context holds a context value per conversation, bounded in count and size, and delivers it on the conversation's next postToolUse. Held text is a hook's own output (requirement 15), and a conversation's held text is dropped by `check` (requirement 30; r21).
34. **Invariant.** Every `Read` gate registers on both preToolUse(Read) and beforeReadFile, because under Auto-review the classifier's read-only calls may skip `beforeReadFile` (observations, Auto-review). Acceptance records which of the two fires for such a call.
35. Every state write the shim or a twin makes is idempotent, because in a multi-root workspace preToolUse and beforeShellExecution each fired twice for one call (row 134). Tests duplicate and race each write (r3 sdet R11; r4 plat R3, cd R3):
    - the active-subagent set's write, in Phase 5a, which ships the set;
    - Deferred context's delivery, exactly once across two concurrent postToolUse calls;
    - Deferred context's delivery, exactly once after a duplicated preToolUse;
    - the stored commit-stall reason;
    - the context-cap twin's held nudge text: a duplicated stop run, sequential and concurrent, delivers once, because the hook's second run is silent and an empty output leaves the held text unchanged (Twins; r10 plat R2);
    - the conversation's first-call mark, which the sessionStart entry and the shim both try to make by an exclusive create, so exactly one wins. One first line is written when the shim wins, and none when the entry does (requirement 19; r8 plat R1; r10 sdet R1; r11 sdet R5);
    - the context-cap twin's stored transcript path, overwritten each turn and cleared with the held text (Twins; r11 plat R5);
    - the compact flag: `TestCompactTwin`, in Phase 5b, drives a preCompact payload carrying a conversation id through the flag write, the next sessionStart or postToolUse delivery and the clear (acceptance step 6; r7 plat R3; r8 sdet R6). Its rows:
      - a duplicated preCompact delivers once;
      - two concurrent first postToolUse calls deliver once;
      - delivery on the sessionStart channel and on the postToolUse channel, each tried first;
      - a second conversation's next postToolUse leaves the first conversation's flag in place;
      - no flag gives no delivery;
      - a preCompact lacking the conversation id takes requirement 6's missing-key outcome and writes no flag;
      - the delivered text equals what `restore-authorization-boundary-on-compact.sh` prints for `source: compact`.
36. **Invariant.** Repo resolution resolves a directory and denies only a Shell call whose directory cannot be known, apart from requirement 39's file-tool exception, the pin's directory-input refusal, the deny for a session id that fails the grammar (Identity and routing), and requirement 3's deny for a resolved directory the shim cannot enter (r21; r20 be R1; Repo resolution's file-tool rule, row 150, row 213; r9 ciso R7). The test has one row per case, each asserting the resolved directory or the specific deny (r3 sdet R3, be R2):
    - a sibling-prefix root, a `..` segment and a symlink out of a root;
    - nested roots, a multi-root join, a target under no root, and no workspace root at all;
    - a create path whose directory does not exist yet;
    - an empty, absent, relative and absolute payload `cwd` on a Shell event, none of which changes the pinned directory;
    - a leading `cd <path> &&` pins that path only under the exception rule, which Repo resolution's directory pin states (r4 sdet R8; r17 ciso R1; r18; r19 ciso R1, cd R3, cd R5):
      - pinned: the first root, a directory inside it, a later root, and a directory inside a later root, a linked worktree placed inside a root included;
      - the first root pinned instead: a sibling worktree outside every root, the main checkout when only a worktree is a root, a non-repo directory outside every root, a nonexistent path, and paths holding `$HOME`, `~`, `$(...)` or a literal backtick, the last one existing on disk (r19 sdet R1);
      - each pinned and unpinned case also with a quoted path and one holding a space;
      - a candidate inside a repository whose config sets `core.fsmonitor`, `core.hooksPath` and `include.path` to recording programs: none of them runs, because the containment test runs no program (r19 ciso R4);
    - the directory pin's rows, in `TestDirectoryPin`, in `test_cursor_adapter_shim.py` (r13; r17 plat R9):
      - a multi-root workspace pins the first root, and no workspace root pins `$HOME`;
      - a non-absolute first root, and no root with `$HOME` unset, take the resolution deny (r18);
      - a pinned directory holding a space, a single quote, a newline or a leading `-` is quoted so the pin-prefixed command starts there;
      - the round trip, for each pin source: the first root, the `$HOME` fallback, an exception directory inside the first root, one inside a later root, and one holding a space and a quote. The pin entry prefixes the command, `pin-check` allows it, stripping returns the agent's original bytes, and a double fire leaves it unchanged, so an exception call keeps one verdict (r19 sdet R1, ciso R3);
      - a command whose first lines are shaped as a pin prefix but name another directory is prefixed again, and the hooks receive it whole (r17 ciso R2, be R8; r18);
      - each Shell event's hooks receive the stripped command, from a prefixed and an unprefixed payload alike;
      - the `pin-check` entry denies an unprefixed command, a lookalike pin prefix, and a command a foreign rewrite replaced, and allows the expected pin prefix. The deny's log line carries its cause (r17 ciso R2, sdet R6; r19 sdet R10);
      - the candidate shells are the recorded bash, `/bin/sh`, `zsh`, `dash` and any shell acceptance step 16's cell 7 records. A runtime probe of each present candidate, never its platform, sorts it: one whose `builtin cd .` exits 0 runs every row below, and one where it fails runs the no-`builtin` row (r20 sdet R4; r20). On macOS `/bin/sh` is bash in POSIX mode. "Behaves as unprefixed" means equal stdout bytes and exit status, with stderr left out, because the pin prefix shifts its line numbers. A construct a shell lacks, such as `shopt` under `zsh`, must give that shell's unprefixed outcome (r18; plat R8, sdet R9; r19 sdet R12):
        - a command holding `||`, `;`, a `}`, a heredoc and a trailing comment runs none of itself when the pinned directory cannot be entered;
        - a command whose last line ends in a backslash behaves as unprefixed;
        - a `shopt -s extglob` line followed by an extglob pattern on the next line behaves as unprefixed;
        - with `cd` shadowed by an exported function that returns 0 without changing directory, and with a directory-change hook such as zsh's `chpwd`, the command runs in the pinned directory or fails, never elsewhere (r19 sdet R2);
      - the no-`builtin` row runs the pin prefix naming a valid directory and asserts the fail-closed outcome: empty stdout, exit 1, and no part of the command run. It tests the pin's `exit 1` on a shell with no `builtin` (r20 sdet R4);
      - the matrix builds its parametrization only over the candidates present and records each one's probe result. One collection-time test observes the matrix, because under the repo's default `-n auto` (`pyproject.toml:24`) a test cannot observe sibling tests' outcomes. From the classification table and the parametrize id list the matrix itself builds, it asserts that the recorded bash is classified as having `builtin` and is parametrized into every behaviour row, that its parameter ids equal the behaviour-row id list, and that the id list is non-empty for at least one shell. So a probe failure on the recorded bash fails and does not silently move it to the fail-closed row. Requirement 22's PR record carries each candidate's probe result, in the Phase 2 and Phase 4 PR bodies, so an absent `zsh` or `dash` on a local machine shows there (r20);
      - the repo's CI runner is `ubuntu-24.04` (`.github/workflows/tests.yml:25`). There `/bin/sh` is dash [unverified], so it runs the no-`builtin` row (r20 sdet R4);
      - so CI runs the `builtin` rows under bash, and under `zsh` only if the image installs it. Requirement 22's PR record carries the local macOS run (r20);
      - the output holds `updated_input` and no `permission` key, and its `tool_input` keys, over a payload with extra keys, equal the payload's apart from `command` (r19 be R5);
      - the Shell tool's directory input, by Repo resolution's directory-input refusal (r20 be R1; r20). An absent, `null` or empty `tool_input.cwd`, and an absolute one whose `realpath` form is the pinned directory, are each pinned as with none and keep `cwd` unchanged in `updated_input`. One naming a directory inside a root other than the pinned one, one outside every root, a relative one, a nonexistent one, a number, `false`, `true`, an empty array, an object and a whitespace-only string each take the refusal. The `false` row guards a default-operator reading that treats a falsy value as absent (r21 be R11, sdet R5):
        - each refusal's output has `permission` `deny` and no `updated_input` (r21);
        - one row asserts that the refusal's `agent_message` and `user_message` are byte-identical for every value above, for a 64 KiB value, and for values holding shell metacharacters, a newline and an escape, and that each contains Repo resolution's literal refusal text, so neither an echoed value nor a wrong constant passes (row 323);
        - under a leading `cd` into a later root, the exception directory is the pinned directory. An absolute `tool_input.cwd` whose `realpath` form is that directory is accepted, and one that is the first root is refused, because the first root is not the pinned directory then;
      - Phase 4's rows, for the rewrite's first line (r17 sdet R3, plat R9; r19 cd R5). Under the recorded bash with `CLAUDECODE=1` in the environment:
        - the rewritten command's probe sees `CLAUDECODE` absent;
        - it sees `CLAUDE_CONFIG_DIR` equal to the routed dir, also for one holding a space, a quote and a newline;
        - it sees `CLAUDE_CONFIG_ADAPTER_SESSION_ID` equal to the call's session id, on a root payload, on a child payload whose `parent_agent_id` differs from its `conversation_id`, and on a nested child's (r19 sdet R7);
        - an id that fails the grammar denies, naming the id field that fails the grammar (r21);
        - a Phase 4 pin prefix is recognized and stripped, and a Phase 3 pin prefix is prefixed again as agent text (r19 sdet R1);
        - a double fire leaves one first line.

    The informational arm asserts `{}` and a logged count.
37. **Invariant.** Nothing reaches a gate unclassified or truncated (r3 ciso R5, be R3, be R7):
    - `tool-map.json` and `parity-exceptions.json`'s `cursor-tool` entries (requirement 27) together list every tool name the hooks doc names (row 153), every name the observations saw, and the known tools no harness gate selects, WebFetch and WebSearch (Translation step 2), and a test checks that equality (r21). The hooks doc's MCP tools enter that equality as the one `MCP:` prefix class (Translation step 2; r21);
    - a generated, fail-closed, matcher-less preToolUse entry denies any tool name that neither the recorded tool map nor a `parity-exceptions.json` `cursor-tool` entry lists, so a new Cursor tool, such as a renamed edit tool, is denied until classified. A name that starts with `MCP:` counts as listed by the prefix class. A `cursor-tool` entry lifts the deny live, because that file attaches no gate, and no entry of another kind lifts it (Recovery; r21);
    - a `TestUnknownToolsAndCap` row runs an `MCP:` name no file lists, which passes `classify`, and an unlisted bare name, which denies (r21);
    - `install` records `tool-map.json`'s content, not a hash of it, in `install.json`, and the shim maps and classifies only from that record. A pull that edits the file therefore changes nothing until `install`, so a newly mapped name or a remapped one takes effect only then. `check` reports a recorded map that differs from the live file (Installer; r21);
    - a payload past the shim's 8 MiB cap denies on a fail-closed entry, naming the cap, and is never truncated. A test places a credential shape past the cap.
38. **Invariant.** No Cursor process becomes a registered live ancestor (row 165, Import copies; r4 ciso R1, be R2, cd R2):
    - `capture-session-id.sh` and `record-session-end.sh` exit 0 with no write when `_lib_payload_is_untranslated_cursor "$INPUT"` matches and `_lib_cursor_host_pid` finds a host, the `Cursor` main process since OD11(d) (r15). Each decline writes one stderr line naming its cause, as the hooks' other early exits do;
    - on an event whose Cursor records hold no key of the predicate's set (OD9's C5), the host walk alone would decide for that hook. C5 found the set on every sessionStart and sessionEnd record, so it leaves no such event, this arm is dead, and Phase 4 builds none of it (row 158; r6 be R5; r12);
    - a Claude Code payload that ever carries such a key still writes, because the host walk fails under Claude Code. A plain terminal session has no `Cursor` ancestor, and under Cursor's integrated terminal or an IDE extension a `claude` process sits nearer than `Cursor` (row 177; r15);
    - the decline reads no environment name, because whether a repo's `env` block reaches Cursor's hooks is unobserved (row 179);
    - the generator emits no shim entry for either hook, and the shim never sets `CLAUDE_PID`;
    - Tests (r5 cd R9, ciso R6):
      - each hook declines a Cursor-shaped payload under a fake `Cursor` host, for each event's recorded key set and for an empty one;
      - each still writes for a Claude Code payload, including one carrying `workspace_roots` under a fake `claude` parent, under the CC2 chain `Cursor`, then a shell, then `claude`, then the caller, and under no host at all. The CC2 chain also leaves the allow emitter's allow in place (r17 sdet R2);
      - the host walk's `ps` stub prints the forms macOS reports: a full bundle path for the `Cursor` main, a full path holding spaces and parentheses for a helper, a retitled helper's title, and a bare name, as on Linux. The stub also answers the walk's liveness test. Lookalikes `Cursor.bak`, `cursor` and `/x/Cursor/bin/node` never match (r17 plat R1, sdet R2);
      - a macOS-only row copies the recorded bash, a non-system binary, to `<directory holding a space>/Cursor.app/Contents/MacOS/Cursor`, starts a child of it, and runs the walk from that child under the real `ps`. Its oracle is the copy's own PID, which the test knows from spawning it, never a value read back from `ps` (r19 plat R6, sdet R12);
      - with an import copy's payload under a fake `Cursor` host, the resolver's walk returns 2;
      - under a fake `Cursor Helper (Plugin)` ancestor with no `Cursor` above it, the host walk finds none (OD11(d); r15);
      - an entry written before a relaunch fails the start-time check after it (row 164);
      - the predicate answers correctly for a 1.4 MB Cursor payload and a 1.4 MB Claude Code one, and a jq stub that hangs past the cap counts as a match for the session hooks, and for the allow emitter under a fake `Cursor` host, which then prints nothing (r8 be R4);
      - with the predicate matching and `ps` stubbed to fail, the emitter's walk fails and the allow is printed, the residual requirement 2 states (r8 be R4, ciso R8).
39. **Invariant.** The adapter protects its own enforcement root on the write tools, as the harness protects its own settings with file-edit asks and no Shell-command matcher (row 192; Twins, Asks; r3 ciso R3, be R9; r4 ciso R3, ciso R6; r5 ciso R2, cd R1; r6 ciso R1):
    - The protected paths (r7 cd R3):
      - `~/.cursor/hooks.json`;
      - `~/.cursor/cursor-adapter/`, which holds the install record, the pre-image and the manifest;
      - the adapter state dir;
      - `hook-shim.sh` at its stable stow path;
      - a workspace's `.cursor/hooks.json`, `.cursor/mcp.json` and `.cursor/sandbox.json` at any depth, as `Edit(//**/.claude/settings*.json)` matches at any depth (row 202; r8 ciso R2; r9 cd R7; r10 cd R6; r16).
    - Cursor loads the workspace files with no Shell call, so the run mode never prompts for what they run or, for `sandbox.json`, what they make writable (r9 ciso R4; r10 ciso R5, cd R6; r16).
      - The list of such workspace files comes from the fetched hooks and MCP docs and OD9's checks, not from these names alone. C6 added `sandbox.json`, which wins over the user-level file, so a Write of one could widen the sandbox for the agent's next conversation (OD11(e); r16).
      - Unobserved, and not protected (rows 214, 221; r11 cd R6):
        - whether the import loads a `.mcp.json`;
        - whether Cursor runs a `.vscode/tasks.json` or `.vscode/settings.json` with no Shell call.
    - A Write or Delete gets the ask-in-chat deny when a target is at or inside a protected path, or inside `~/.cursor` (r7 ciso R3):
      - every path of a multi-path write is checked;
      - a Delete of an ancestor of a protected path, such as `~/.cursor`, the routed config dir or `$HOME`, is also denied;
      - a Delete of a workspace's `.cursor` directory is not denied, because it removes the project's own files and adds nothing that runs (r11 ciso R3).
    - `~/.cursor/sandbox.json`, which `install` writes from Phase 5a (r17 be R2), sits inside `~/.cursor`, so a Write or Delete of it takes the ask-in-chat deny (OD11(b); r15). Whether Cursor itself protects it, or a workspace's `.cursor/sandbox.json`, from an agent Shell write is unobserved, and acceptance step 10's cell 7 records both.
    - "At or inside" is decided by file identity, not by spelling (rows 216, 217; r10 ciso R1; r11 ciso R1, ciso R3, cd R4):
      - each user-level protected path exists once installed, the state dir included, because `install` creates it (Installer);
      - the walk starts at the target's nearest existing directory: the target when it is a directory, otherwise its containing directory, or the nearest existing ancestor of a part that does not exist yet;
      - each step of the walk appends `/..` to the one before, up to the root. Each step must exist and be a directory, so a step that cannot be read, as past the system's path-length limit, is never taken for "a different file". It takes the unresolvable deny, with its cause named;
      - the walk climbs at most 64 directories, the bound `_lib_realpath_m`'s fallback also uses (`_lib.sh:169`), and a 65th takes the unresolvable deny;
      - a target matches a protected directory when a step of its walk is the same file by bash's `[ a -ef b ]`, which compares device and inode. It matches a protected file when the target itself is the same file;
      - for a Delete, the walk also runs from each user-level protected path up to the root, and the target matches when it is the same file as one of those steps. That covers the ancestor direction;
      - spellings the volume treats as one file, such as a `/System/Volumes/Data` prefix, a case or Unicode variant or a symlink, therefore match. A hard link matches only the protected file it links to, because a link to a directory's member sits in another directory (r11 sdet R1);
      - the workspace files have no fixed path, so they are matched on two legs:
        - the as-sent path, lexically normalized;
        - its `_lib_realpath_m` form;
      - on each leg, the target's parent must be the same file as the sibling spelled `.cursor` in the parent's own parent, and the target the same file as `hooks.json`, `mcp.json` or `sandbox.json` spelled in that parent;
      - a part that does not exist yet has no identity, so its name is compared with ASCII letters' case ignored, whatever the hook's locale. That only adds denies (r11 ciso R2).
    - Two target forms take a deny of their own instead (r8 be R2; r9 be R3, cd R8; r10 cd R1):
      - a target that is not absolute as sent, a `~`-led or `$HOME`-holding one included (row 206; r8 ciso R4):
        - no base is guessed until C1's follow-up shows which forms Cursor sends;
        - if it shows a relative form, the revision that enters C1's results states its base before Phase 5a;
        - C1 showed none: every observed Read, Write-as-create, Delete and Grep target arrived absolute. So no base is stated, and the deny stays for a Write-as-edit target, which C1 did not observe, and for any later form (row 206; r12);
      - a target that cannot be resolved: a dangling link, a `..` after a part that does not exist, a walk step that cannot be read, a walk past 64 directories, or the workspace leg's `_lib_realpath_m` returning 1, as on a cap kill (r7 be R1; r11 ciso R1).
    - That deny's text lists those causes. It tells the agent to retry with an absolute, `..`-free path, and to ask the engineer in chat when the path already is one.
    - These two denies and requirement 3's unenterable-directory deny are the exceptions to Repo resolution's rule that a file tool never denies for resolution (r21).
    - The entry's `timeout` is 10 seconds, the harness's explicit-timeout precedent (`settings.json:178`, `:207`, `:218`). A filesystem stalled past it takes requirement 3's timeout deny, not the unresolvable text (r9 be R3).
    - No shim deny text names `uninstall` or the log path, and the retry and ask-in-chat text above goes in both fields, by requirement 3's rule (r13).
    - Requirement 19's tamper comparison, which the sessionStart entry, `status` and `check` run, is the detection, with that requirement's stated limit and the Installer's report channels (r7 ciso R2, ciso R4).
    - `docs/cursor-adapter.md` records the tier as `untrusted-input`. It states that Cursor's run mode, under requirement 5's precondition, is the only layer in front of a Shell route, as Claude Code's permission prompts are for the harness's own root. Under Auto-Review (with Sandbox) that layer is Auto-review's own approval and its sandbox, not a prompt (OD11(e); r16). It also states that the vendor calls its allowlist and auto-run settings "Not a security boundary" (G7; r8 ciso R6). Its residuals:
      - a Shell command can remove or rewrite any protected path, and no hook prompts for it (r7 ciso R2; r8 cd R4);
      - a workspace `.cursor` directory that holds no `hooks.json`, `mcp.json` or `sandbox.json` yet, where a Write creates one under a non-ASCII spelling the volume folds onto that name, because the name compare ignores ASCII case only (row 217; r11 ciso R2; r16);
      - a hard link, which only a Shell call can make, from another directory to a file inside a protected directory or to a workspace `hooks.json`, `mcp.json` or `sandbox.json` (r11 sdet R1; r16);
      - under Auto-Review (with Sandbox), a hard link or symlink to a protected file, planted in any directory the sandbox leaves writable (the grant's listed directories, the workspace and the temp dirs) and then written through. The sandbox's containment against it is untested (requirement 5; r21);
      - the store that holds Cursor's run mode, and probably the import toggle, which nobody has located: `~/.cursor/permissions.json` was absent under every mode (row 197). No protected path is known to cover the store, so a write tool may change the mode or the toggle for a later session with no deny (r21);
      - an agent Shell write of a workspace `.cursor/sandbox.json`, if acceptance step 10's cell 7 shows one lands with no prompt and the engineer keeps the mode (OD11(e); r16; r17 ciso R4);
      - a workspace `.cursor/sandbox.json` that exists before the conversation, checked into the repository or written outside the agent's write tools. It wins over the adapter's file (C6) and loads with no Shell call, so no deny sees it. The sessionStart entry's count is its only report, and that count reaches the model only through `additional_context`, which reached the agent in 2 of 3 attempts in Phase 1 (Phase 1 observations, Decisions). `check` cannot count it, because it runs in no workspace (OD11(b); r20 ciso R2). So the count reaches the model only, never the engineer directly, and content that steers the agent can keep it from the engineer (r20);
      - a workspace `.cursor/sandbox.json` entry inside a root is judged once, at sessionStart, and the agent can swap that path afterward. Whether Cursor re-resolves entries per command is unobserved (r21);
      - the sessionStart report reads the hook's `$TMPDIR`. That it equals the agent Shell's is [unverified], though C6 observed the Shell's writable (r21);
      - `parity-exceptions.json`, the translators and the shim's lib run from the user-writable checkout, as the hook scripts do under Claude Code. `tool-map.json` reaches the shim only through the install record, which `install` copies it into (requirement 37; r21);
      - the system-level managed hooks file is outside the adapter's control (Alternatives set aside).
    - Tests (r7 ciso R1, ciso R3, be R1, sdet R5; r8 ciso R2, ciso R4, ciso R7, be R2, sdet R8, cd R6; r9 ciso R4, ciso R8, be R3, sdet R5, cd R10; r10 ciso R1, be R4, sdet R4, cd R12; r11 ciso R1, ciso R2, ciso R3, sdet R1, sdet R2, sdet R8, sdet R9, cd R5, cd R15):
      - for each class of protected path, a user-level file, a member of a protected directory and the workspace files, each denied:
        - an existing leaf, overwritten;
        - a missing leaf under an existing parent;
        - several missing levels;
      - overwrites of existing files, each denied: the install record, the pre-image and the manifest in `~/.cursor/cursor-adapter/`; an existing log and an existing first-call mark in the state dir; `~/.cursor/permissions.json`; `~/.cursor/sandbox.json` (r15); and an existing `~/.cursor/rules/x.mdc`;
      - a Write and a Delete of `.cursor/hooks.json`, of `.cursor/mcp.json` and of `.cursor/sandbox.json`, each in a workspace root and in a subdirectory, each denied (r16). The fixture workspace is not `$HOME`, where `~/.cursor` would deny everything;
      - creates of `.cursor/Hooks.json`, `.cursor/MCP.JSON` and `.cursor/Sandbox.json` where `.cursor` exists with none of the three, and of `.CURSOR/hooks.json` where no `.cursor` exists, each denied (r16);
      - for each workspace name, a symlink named as the protected file that points elsewhere, which only the as-sent leg matches, and an innocently named symlink that points at the protected file, which only the resolved leg matches, each denied;
      - the same two shapes for a symlinked `.cursor`, each denied. `-ef` follows a directory link, so both also match on the as-sent leg, and the file-symlink row above is the resolved leg's only isolating row;
      - `hook-shim.sh` built as a stow-style symlink into a checkout, with a Write through the stow name and one through the target name, both denied;
      - a Write through a symlink outside `~/.cursor` that points into it, and one through a symlink outside it that points at the install record, each denied;
      - a Write through a symlinked directory followed by `..`, denied;
      - a hard link to `~/.cursor/hooks.json` and to `hook-shim.sh`, made outside `~/.cursor`, written through, each denied;
      - a Write spelled through `/System/Volumes/Data` into `~/.cursor`, into the state dir and at `hook-shim.sh`, each denied. These rows run on macOS only, per requirement 22's portability bullet, and skip where the volume has no such prefix;
      - a case variant of each user-level protected path, denied. These rows run on macOS only and skip where `[ a -ef A ]` is false on the fixture volume;
      - a two-path Write whose protected path is second, denied;
      - a relative target, a `~/`-led target and a target holding `$HOME`, each taking the not-absolute deny;
      - a Delete of `~/.cursor`, of the routed config dir and of `$HOME`, each denied, also through a symlink alias of `$HOME` and of the routed config dir, and, on macOS only, through `/System/Volumes/Data`;
      - each unresolvable target takes the unresolvable deny:
        - a dangling link;
        - a `..` after a part that does not exist;
        - a target under a prefix padded to 1021 characters inside `~/.cursor/rules`, whose walk step passes the path-length limit;
        - a target 65 directories deep, while one 64 deep outside the protected set passes;
        - the workspace leg's `_lib_realpath_m` returning 1. The test stubs `realpath` and `grealpath` to fail so the walk runs, and stubs that walk's own `realpath --` to hang. It drives the gate script directly, because a run through the shim meets the entry's `timeout` first;
      - allow rows, each passing:
        - a Write and a Delete outside `~/.cursor` and every protected path;
        - a Write to `~/.cursor-notes/x`;
        - a Write to a name beside `cursor-adapter-state` in the routed config dir;
        - a Delete of a prefix-sibling of the routed config dir, the same name with a suffix;
        - a create under a directory that does not exist yet, outside the protected set, on each `_lib_realpath_m` branch, forced by stubbing the commands before it to fail (r8 sdet R8);
        - in a workspace, a Write to `.cursor/rules/a.mdc`, to `.cursor/hooks.json.bak`, to a `hooks.json` and an `mcp.json` with no `.cursor` parent, to `.cursor-extra/hooks.json`, and to `x.cursor/hooks.json`;
        - on macOS only, a create of a workspace hooks file under a non-ASCII spelling the volume folds onto `hooks.json`, where `.cursor` holds none: the residual, pinned so closing it is a deliberate change.
40. **Invariant.** Phase 2 adds a gate-matrix integration test, `claude/.claude/hooks/tests/test_cursor_adapter_gate_matrix.py`, that runs the shim on the unchanged scripts with no Cursor (r3 sdet R1; r4 sdet R1, sdet R9, be R3, ciso R4, plat R4; r5 sdet R1, sdet R2, sdet R6, ciso R3, plat R6, cd R2):
    - Rows: one per `settings.json` registration whose script's `# hook-class:` header is `gate`, `turn-gate` or `batch-gate`, discovered by glob, never a hard-coded count.
    - A meta-test reads `parity-exceptions.json` (Registration) and fails:
      - a registration with neither a shim arm nor a `parity-exceptions.json` entry of kind `registration` (requirement 27; r21);
      - a registration with both a shim arm and a `pending-phase` entry, so the phase that delivers a row also removes its entry;
      - from Phase 3 on, a registration with a shim arm and no `withheld-premise` entry that has no entry in the generator's output.
    - The turn-gate and batch-gate hooks set no `DENY_GATE_LABEL`. Their Stop registrations and `send-back-skipped-self-review`'s SubagentStop one enter as pending Phase 5b, whose twins carry them. Their two PostToolBatch registrations and the `SubagentHandback` one are `unsupported` (Registration).
    - Payloads come from the shared builder (requirement 20), from the key paths and types C5 records.
    - The shim, near-miss and import arms run with the process directory outside every repo, as Cursor runs user hooks (row 133). Each first asserts that `git rev-parse --git-dir` fails there under its own environment, so an inherited `GIT_DIR` cannot make it an inside arm. The repo is reachable only through the payload: `workspace_roots` with the empty `cwd` C1 records, a leading `cd` into a workspace root or a directory inside one, or a relative `file_path` (r13; r17 ciso R1; r19 ciso R1).
    - Leading-`cd` rows, for the worktree git-writes, code-review and ready-for-review gates (r17 ciso R1). Each runs through the shim and against a Claude-shaped payload whose `cwd` is the repo, and the two verdicts must be equal:
      - `cd <non-repo dir> && cd <repo> && <write>`;
      - `cd <non-repo dir> && git -C <repo> <write>`;
      - `cd <clean other repo> && cd <repo> && git commit`, with the clean repo no workspace root. With the clean repo a second workspace root instead, the shim pins it, and the row's reference is a Claude-shaped payload whose `cwd` is that clean repo (r18);
      - a leading `cd` to a nonexistent path;
      - with a single root that is a linked worktree, against a Claude-shaped payload whose `cwd` is that root (r19 ciso R1):
        - `cd <main checkout> && cd <root> && git push`, and the same with `gh pr create` and with `gh pr ready`;
        - `cd <clean sibling worktree> && cd <root> && git commit`.
    - The import arm also runs one child payload through each hook that reads the raw payload's `.session_id`, such as `log-reviewer-round.sh`. A copy that writes under the child's own id fails it as an unlisted effect, and a deny the twin does not give fails it as usual (r19 be R1).
    - Four arms per row:
      - shim: a Cursor-shaped trigger, reused from the gate's own tests, denies with the gate's `Blocked by <label> gate:` text;
      - near miss: a Cursor-shaped near-miss gives `{}`;
      - raw: the same raw Cursor payload straight to the script, from inside the repo, gives the row's recorded class (r6 cd R2):
        - `no-op`, exit 0 with empty stdout, which shows the row catches the live no-op;
        - `denies`, with the shim arm's deny, as the Read- and Task-keyed gates do (O28.a);
      - import: the raw payload from outside the repo, with Cursor's environment, gives exit 0 with empty stdout or the shim arm's deny. Where it gives exit 0, the row passes only when the shim arm denies, which shows Repo resolution reached the repo the hook needs.
    - Neither the raw nor the import arm ever gives an allow, and the import arm never gives a deny the shim arm does not give.
    - Every registration, informational ones included, also runs its import arm, and its shim arm where it has one, under a temporary home, config dir, `TMPDIR` and scratch repo, with PATH stubs that record network calls and detached spawns (r6 cd R2).
    - The temporary home's `install.json` records the stubs, so the PATH the shim fixes from it holds them (requirement 15).
    - An informational registration's trigger reaches its write path, reused from that hook's own tests as a gate row reuses its trigger.
    - Two checks show what a hook receives, with no gate run (row 195; r7 sdet R1; r8 sdet R1, sdet R2, sdet R3). They are `TestTranslatedPayload` and `TestShimEnvelope`, in the gate-matrix file, which Phase 4 lists:
      - payload: for every builder spec, the shim runs a recording probe script in place of the hook, and the stdin the probe saves equals the spec's twin (requirement 20) as parsed objects:
        - the fields the table renames or transforms, `hook_event_name`, `session_id`, `cwd`, `tool_name`, `tool_input` and `tool_response`, are compared by value;
        - the allowed key drops and additions are a literal per-event list in the test, so a `field-table.json` edit alone cannot make it pass;
      - envelope: the same probe, run once under the shim and once directly, from one fixed starting environment, reports:
        - equal cwd and umask;
        - equal stdin bytes (r9 cd R9, sdet R8):
          - under the shim, stdin is a pipe the shim writes from memory (requirement 15), which the check asserts;
          - in the direct run it is a pipe. Each kind is recorded as a regular file, a FIFO, a socket or a character device, by step 1's tests, rather than required equal, because Claude Code's own kind is unverified until acceptance step 1 records it (row 207; r10 sdet R5);
        - equal environment names and values, apart from bash's own `_`, `SHLVL`, `PWD` and `OLDPWD` and the exceptions below;
        - the exceptions (r9 cd R2):
          - the three scrubbed names are absent under the shim;
          - PATH equals the install record's PATH;
          - `CLAUDE_CONFIG_DIR` equals the routed config dir;
          - `CLAUDE_CONFIG_ADAPTER_SESSION_ID` is absent through Phase 3;
          - `CLAUDE_CONFIG_ADAPTER_SESSION_ID` equals the conversation's id from Phase 4.
    - Whole-hook effect equality, below, stays for what those two checks cannot see: the shim's own writes outside the state dir, identity from the ancestor walk, and output translation through a real run (r8 sdet R1).
    - Effects:
      - an arm fails on any effect its row does not list. The effects are a write to the config dir, `TMPDIR`, the scratch work tree or its `.git` metadata, a stubbed network call, and a spawn;
      - a shim arm's effects outside the adapter state dir, which the shim itself writes, equal those of a reference run: the same script run directly on the trigger spec's twin (requirement 20; r6 sdet R2; r8 sdet R1; r9 cd R1). That twin is the object the payload check takes, and its `session_id` equals the spec's `conversation_id`. Equality, not a subset, so a mistranslated field that makes a hook exit early fails;
      - both runs execute in the same absolute roots, each from a pristine copy, so hashed state names and printed paths agree. The compared unit is each write's root-relative path and content. Only time values and PIDs are masked, by named patterns, and no id is normalized;
      - from Phase 4, the reference run for an identity-keyed row seeds a registered `claude`-named ancestor whose `sessions/` entry carries a `session_id` equal to the shim arm's `conversation_id`, so the compared lock content differs only by the masked PID (r7 sdet R2);
      - a shim arm's output equals `translate-output.jq`'s mapping of the reference run's exit and stdout, under the same masks;
      - an import arm lists no effect, except the two session hooks' `sessions/` and `session-end-records/` writes until Phase 4. Phase 4 removes that listing, so their decline is checked here too (requirement 38).
    - `bootstrap-project-plugins.sh` (row 187):
      - its arms and reference run use a scratch copy of the hooks tree whose `../scripts/reconcile-project-plugins.sh` is a recording stub, because the hook launches it by script-relative path, which no PATH stub sees. The reference run's launch record waits on the stub's sentinel under a deadline, as `test_bootstrap_project_plugins.py:10-18` does (r7 cd R9, sdet R10);
      - its shim arm runs with `CLAUDE_CODE_ENTRYPOINT=cli`, `CLAUDECODE` and `CLAUDE_PID` set in the shim's environment and the scratch repo trusted in the temporary config dir. The shim removes the three names, so the hook takes its non-interactive exit (`bootstrap-project-plugins.sh:203`). This is the one shim arm whose effects differ from its reference run (row 178; requirement 15; r7 cd R9);
      - the shim arm passes on exactly one audit line whose decision is `m1a_non_interactive` and whose outcome is `skipped entrypoint=unset`, timestamp and PID masked, and no launch. The hook writes that line only on a path that never reaches the launch, so it is the no-launch oracle, as in `test_bootstrap_project_plugins.py:10-18` (r6 sdet R3);
      - its import arm, under the same environment, passes when the copy launches nothing and has no other effect. A launch fails it and goes to the engineer as a further state writer (Import copies).
    - Positive controls for the detectors: one scratch hook per effect class, each run through the shim arm and each expected to fail the effect check (r6 sdet R5; r7 cd R9):
      - a write under the config dir;
      - a write under `TMPDIR`;
      - a write in the scratch work tree;
      - a write in its `.git` metadata;
      - a `git fetch` through the stubs;
      - a `gh` call through the stubs;
      - a detached spawn of a command found through PATH, read after the stub's sentinel appears under a deadline (r7 sdet R10).
    - A network call a hook makes in-process, with no stubbed command, is outside the matrix.
    - Each row's trigger carries an `inert` flag, true when a missed deny leaves no effect, and a `drawn` flag, true when an acceptance step draws it. A meta-test fails a trigger whose `drawn` flag is true and whose `inert` flag is false, as Phase 1's Scenario lint did.
    - Later phases add rows, which give acceptance step 2 its trigger table:
      - Phase 3: requirement 41's arm, and the test that the Recovery section every shim deny names exists in `docs/cursor-adapter.md`;
      - Phase 4: the two worktree gates' rows, linked-worktree ones included;
      - Phase 5: the twins' and adapter gates' rows.
41. **Invariant.** A generated-matcher test, parametrized over every `settings.json` registration, checks requirement 7 (r3 sdet R4):
    - for each Claude tool name the registration's matcher selects, the mapped Cursor name matches the generated regex under `re.search`, and the shim does not print `{}` for it;
    - for each name it does not select, the shim prints `{}`;
    - each generated beforeShellExecution matcher selects a command's pin-prefixed form whenever it selects the command (r13; r19 cd R2);
    - both arms of the four `if:` entries and the dedupe run.

**Test map.** The files are those Critical files names, and the requirement numbers follow each class. Its rules (r5 cd R11):

- The map is total: every requirement and every phase has a class below, or the last bullet says why it has none (r4 sdet R5, cd R4, cd R9).
- Each class exists in its phase's PR and in every later one. Verification 6 checks that with `pytest --collect-only -q`, so a dispatch cannot pass with green tests that omit one (r3 sdet R10).
- A matrix that must hold a fixed case set takes its ids from one module-level list, and a test in its class asserts that list equals the set the requirement states, so a stub with fewer cases fails (requirements 3, 22).

The classes, by phase:

- Phase 2:
  - `TestOutputTable` (2, 3, 17), covering every event, exit class and stdout class.
    - Its properties, per cell, by Translation step 5 (r21):
      - no cell yields a Cursor allow, and no cell on an event with a block or deny channel yields `{}` when the hook asked or denied. Those events are the pre-call events and the Stop and postToolUse channels step 5 names;
      - exit 2 on an event step 5 sends to the informational catch-all yields `{}` and a log line;
      - exit 2 on a pre-call event blocks: a deny on preToolUse, beforeShellExecution and beforeReadFile, and `continue: false` on beforeSubmitPrompt;
      - exit 2 on subagentStart yields the deny shape only, not enforced by Cursor (r21);
      - `ask` on beforeShellExecution is Cursor's `ask`, neither a deny nor `{}`;
      - `ask` on every other event with a deny channel is a deny carrying the ask-in-chat text.
  - `TestFieldTableCoverage` (6, 17)
  - `TestRequiredKeyPaths` (6)
  - `TestRepoResolution` (36)
  - `TestDirectoryPin` (2, 36), in `test_cursor_adapter_shim.py` (r17 plat R9)
  - `TestUnknownToolsAndCap` (37)
  - `TestParityExceptions` (27, 37)
  - `TestDeleteTranslation` and `TestWriteTranslation` (22)
  - `TestShimFailureOutcomes` (3)
  - `TestShimPrologue`, `TestShimLogConcurrency` and `TestShimExecCount` (15, 19, 28)
  - `TestCursorPayloadBuilders` (10, 20), in the gate-matrix file
  - `TestGateMatrix` (40)
  - `TestTranslatedPayload` and `TestShimEnvelope` (17, 40), requirement 40's two checks, in the gate-matrix file
  - `TestSessionIdGrammarParity`, `TestCappedCallParity` and `TestRealpathParity` (10, 15, 36), with `TestRealpathParity` running each of `_lib_realpath_m`'s three branches
  - `TestCursorAdapterSelection` (22), in `test_select_tests.py`
- Phase 3:
  - `TestLaunchGuard` (3) (r19 cd R1)
  - `TestGeneratedMatcherCoverage` (7, 22, 41)
  - `TestPrerequisitesAndContract` (9, 12, 13)
  - `TestHooksJsonVocabulary` (20)
  - `TestReadGateBothEvents` (34)
  - `TestInstallerTargets` (11)
  - `TestInstallCrashInjection`, `TestUninstallOwnership` and `TestInstallRecordRoundTrip` (18)
  - `TestStatusAndCanary` (19, 39), the tamper check included
  - `TestRunModeWarning` (5), in `test_cursor_adapter_install.py`
  - `TestLogFieldTableParity` (15), with the run-mode field's size row, in `test_cursor_adapter_install.py` (r21)
  - `TestLogRotationAndSweep` (15, 19)
  - `TestRecoverySectionResolves` (3), in the gate-matrix file, which a `docs/` edit selects (row 180). It also asserts that the Recovery section holds the `pin-check` deny's entry and the unenterable-directory deny's entry (r21)
- Phase 4:
  - `TestClaudePathSnapshot` (14)
  - `TestAdapterIdentity` and `TestIdentityConsumers` (29, 30, 31)
  - `TestEnvScrubNestedRun` (31), in each tree's env-scrub file
  - `TestCopyAllowSuppressed` (2)
  - `TestCursorPayloadDecline` (38), in `test_lib_cursor_identity.py`
  - `TestSandboxFile` (18, 19) and `TestSandboxWritableDirs` (18), in `test_cursor_adapter_install.py` (OD11(b); r15)
  - `TestDirectoryPin`'s Phase 4 rows (36), in `test_cursor_adapter_shim.py` (r17 sdet R3, plat R9)
  - `TestSessionStartReports` (19, 22), in `test_cursor_adapter_install.py` (r19 sdet R8)
  - `TestRunModeWarning`'s Phase 4 row (5), in `test_cursor_adapter_install.py` (r21 sdet R7)
  - `TestLogFieldTableParity`'s Phase 4 row (15), the host-walk and `cursor_version` fields' size row, in `test_cursor_adapter_install.py` (r21 sdet R7; r21 plat R9)
- Phase 5a:
  - `TestSettingsPermissionsGate` (4, 5)
  - `TestAgentAttribution` and `TestActiveSetDoubleFire` (8, 35), in `test_cursor_adapter_gates.py` (r21 sdet R6). `TestAgentAttribution` also holds the Delete quoting-matrix rows through `gate-agent-tools.sh` (22; r21)
  - `TestAdapterGateInvariants` (9)
  - `TestCredentialArms` (32)
  - `TestSelfProtection` (39)
  - `TestSandboxGrantWritten` (18), in `test_cursor_adapter_install.py` (r19 be R2, sdet R5)
  - `TestWorkspaceSandboxReport` (18), in `test_cursor_adapter_install.py`, for the workspace `sandbox.json` report's rows (r21)
- Phase 5b: `TestStopTwin` (16), `TestContextCapTwin` (16), `TestDeferredContext` (33), `TestDoubleFire` (35), `TestCompactTwin` (35) and `TestSendBackTwin` (40).
  - `TestContextCapTwin` is acceptance step 6's positive control (r8 plat R4; r9 plat R4, sdet R1; r10 plat R2, sdet R2). It runs the entry text `generate --print-entry` renders for step 6, with the cap exported ahead of the `stop` entry's launch guard as step 6 does, on a Claude-shaped transcript fixture, through afterAgentResponse and then stop. Its rows:
    - a completed turn: the `nudged` line appears, and the text delivered on the next postToolUse equals the hook's `additionalContext`;
    - an aborted turn, an errored turn, and a turn with `loop_count` above 0: nothing is delivered, no `nudged` line is written, and the hook does not run, so the next completed turn at the same estimate still delivers (r11 sdet R6);
    - a duplicated stop, sequential and concurrent: one delivery;
    - `transcript_path` carried on stop, and carried only on afterAgentResponse: each delivers, and a stored path from an earlier turn is never used (r11 plat R5).
  - `TestSendBackTwin` is acceptance step 6's trigger 7 positive control (r21). It runs the real `send-back-skipped-self-review.sh`, not a stub exiting 2, through the shim on a Claude-shaped transcript fixture lacking the evidence. Its rows:
    - a stop whose `status` is `completed`: `followup_message` equals the hook's `reason`;
    - a stop whose `status` is not `completed`: the output is `{}` and the hook's log gets no line.
- Phase 5c: `TestGeneratedAgentsAndRules`, covering `model-map.json`, the agents' `tools:` table, the rules' `alwaysApply` and `globs` files and `install --acceptance-nonce` (Agents, rules and instructions; r7 plat R6; r8 be R1, plat R7). Its parse-failure rows:
  - `generate` fails, naming the file, on an agent file and on a rule file it cannot parse;
  - `install` with an unparseable agent file, and with an unparseable rule file, leaves `~/.cursor` byte-identical (r9 plat R7);
  - while an agent file or a rule file is unparseable, `status` names it and still names a removed entry.
- No automated class:
  - requirement 1, a vendor behavior, which acceptance steps 2 and 3 observe;
  - requirement 21, which governs the acceptance run itself, and Verification 6's Phase 6 bullet checks it;
  - requirement 22's portability and PR-record items, which bind every test and every PR body rather than one class;
  - requirements 23 to 26, removed;
  - Phase 6, whose checks are the acceptance steps.

### Open decisions

- **OD4: gaps and parity exceptions.** Decided after Phase 1, with the evidence (row 55).
  - The engineer's choices:
    - for the gates item, a native twin per gate (row 113);
    - for the other items, the split of row 115: an item whose gap would lose enforcement gets a twin or a blocking alternative, designed in Phases 2 to 6 above, and an item is a documented gap in `docs/cursor-adapter.md` only where Cursor lacks the tool, the event or the channel;
    - "Accept the re-sorts (Recommended)", to the session's question listing where this revision's sort differs from the session's (row 146).
  - The sort below is this revision's, re-derived from the observations. Where it differs from the dispatching session's proposed sort, the item says so (row 116).
  - Enforcement loss, so a twin or a blocking alternative:
    - Gates that silently no-op under Cursor (the live-gate follow-up above). Every harness registration Cursor can fire gets a generated native entry that runs the unchanged script on a translated payload, and the import's copy keeps running beside it (Translation, Registration, Import copies). That covers:
      - the 13 gates that exit unless the tool name is `Bash`;
      - the plan-review gate, which resolves its repo from its own working directory;
      - every gate the audit did not reach (row 123).

      The per-gate sort by payload field becomes `field-table.json`'s job, and a missing required field fails closed (requirement 6). Each twin runs its hook from the directory Repo resolution gives, and the hook decides whether that directory holds a repo, as under Claude Code. Repo resolution states its resolution denies, which requirement 36 lists (row 150).
    - `redact-credential-values`: the credential arms (Twins, requirement 32). The output channels Cursor cannot replace are OD10's.
    - `restore-authorization-boundary-on-compact`, whose key is enforcement-critical (`config-keys.psv:86-90`): the compact twin.
    - `advance-past-commit-stall`: the commit-stall twin. `nudge-handoff-near-context-cap`'s Stop registration takes the same pattern if Cursor's transcript carries its measure (row 140), and otherwise Cursor lacks the channel and it joins the documented gaps.
    - `send-back-skipped-self-review`'s SubagentStop `code-writer` registration: the send-back twin, withheld until acceptance confirms its premise (Twins).
    - The agent-keyed gates and `review-orchestrator`: O8 found no per-call attribution, so active-set attribution (Twins, requirement 8).
    - The tool classes no harness matcher selects, re-sorted per class (r5 cd R6):
      - Delete reaches the `Bash` hooks as `rm` (Translation);
      - Grep gets the `Read` deny entries (requirement 4) and the credential path arm (requirement 32);
      - MCP output gets native redaction (requirement 32);
      - WebFetch output is OD10's residual;
      - WebSearch is no item, because the harness gates no WebSearch call under Claude Code either;
      - a tool name nobody has classified is denied until classified (requirement 37).
    - `ask-new-dependency-disclosure` and `ask-review-permissions`: A8 failed, so each takes the native twin row 36 chose (Twins, Asks).
  - Re-sorted out of enforcement loss, where this revision differs from the session's sort:
    - The first-prompt window. O29 showed the first call after a relaunch hooked, so the item's trigger is not met on the run's Cursor build. The doc names G8's "Block until hooks are ready" setting for a build that shows the window, and acceptance re-checks O29.
    - Tool-call enforcement in a workspace still in Restricted Mode, which stays untested. No user-level artifact can act where Cursor loads no hooks, so no twin is possible. Acceptance step 10 tests it:
      - if tool-call hooks do not run there, Cursor lacks the channel, and the doc says to trust a workspace before agent work;
      - if they run, the item closes.
    - `require-routing-read`. The translator gives Cursor's Task the Claude name `Agent` (row 125), so its generated entry runs it, and it is no item.
    - The six hooks whose context output has no channel on its own event (row 47). They take Deferred context rather than a documented gap, because Cursor has a later channel: postToolUse context reaches the agent (row 131). Their text therefore lands with the next tool result instead of at prompt or subagent start.
  - Documented gaps, where Cursor lacks the tool or the event (G1):
    - `activate-handoff-bypass` and `nudge-answer-provenance`, on PostToolUse for `Skill` and `AskUserQuestion`.
    - The two PostToolBatch registrations.
    - `send-back-skipped-self-review`'s PreToolUse arm, on `SubagentHandback`, a tool Cursor lacks.
    - `require-review-orchestrator-routing` (`Skill`) and `track-permission-prompts` (Notification). Their keys default to off (row 18), so the gap reaches only an account that turns them on.
    - `require-plan-review`'s ExitPlanMode arm, already a parity exception (row 37).
- **OD5: the per-call budget.** Decided: 1 s at the 95th percentile of added hook time, per Shell, Read and Write call (row 184; r5 cd R5).
  - The anchor is Nielsen's response-time limits, which the staff-platform-engineer round-1 review relays as 0.1 s feeling instant and 1 s keeping the user's flow of thought. B6 and requirement 28 apply it.
  - The engineer fixed the budget before any benchmark ran, so it is not fitted to the result (r4 plat R6).
  - A class's added hook time is the installed arm's 95th percentile minus the uninstalled arm's, where the uninstalled arm runs the import's copies alone (r5 plat R5).
  - S8 is cut (row 75), so two checks apply the one budget:
    - the fan-out benchmark (r3 plat R5; r6 cd R4):
      - it runs from a scratch directory outside the repo;
      - for each class it runs one shim-and-hook process per registration that selects the class, counted from `settings.json`, and per adapter-only entry the phase has built, beside the import's copies;
      - each class runs with a small payload and with 1.4 MB, the largest observed payload (Payload), carried as postToolUse output and as Write content;
      - over 100 runs per case it records wall time, CPU time and each entry's 99th-percentile hook duration;
      - Phase 2 runs it. Phases 4, 5a and 5b re-run it, 5a and 5b with the adapter-only entries each adds (r5 plat R5). Phase 4's re-run takes its uninstalled arm's `_lib.sh` from Phase 4's merge-base, so the import copies' new per-call work shows in the difference (r6 plat R3). It also runs, per class, a gated call into a linked worktree of a scratch repo with worktree enforcement on, under a process chain as deep as C8 recorded, and reports it apart, because a call from outside every repo exits before the identity walk (r19 plat R2);
    - acceptance step 12, from Cursor's hooks log.
  - A benchmark run misses when a class's added hook time exceeds the budget, or when a 99th-percentile hook duration exceeds half the smallest inner deadline, which under contention would make a fail-closed entry deny (requirement 3). Step 12 applies the first condition only.
  - A miss on Phase 2's run stops Phase 3 from merging, and a miss on a re-run stops its own phase from merging. On any miss, step 12's included, the session takes the per-class numbers to the engineer with two options: raise the budget, or have `plan-architect` revise the plan to cut per-call work.
- **OD6: a consuming repo's hooks, or an untested source, beat the harness.** Decided: the trigger is not met (row 117). The item was to be asked when O17, O20(b) or O20(c) showed a project-level source's output beating or erasing a harness deny or follow-up, or when O20(b) showed rank precedence rather than "deny wins regardless of source".
  - No tested source beat or erased a harness deny or follow-up. A native user-level deny beat every native project, imported project and imported local output. An imported project-level deny beat every native project output. A Stop follow-up survived the other source's `neutral` in both directions (O20.b, O20.c).
  - Neither result shows rank precedence, so deny-wins extends to managed and plugin hook sources as documented (G5), not observed. A Stop follow-up stays open for them, because it is last-response-wins.
  - O17's fail-open on an imported crash or timeout is the harness's own copy failing, not another source beating it. That silent no-op belongs to OD4's gates item, which the shim's `failClosed` entries close (requirement 3).
  - The imported user-level direction, a live-gate deny against a native output, stays an open, disclosed gap. The live gates ran and did not block under Cursor, so no cell could observe it. Under this design the harness's denies come from the native user-level source, whose direction O20.b did test.
  - Every criterion and requirement that reads O20 takes this outcome (row 33). The engineer's choice that a repo's hooks run and the harness's deny wins stands (row 56).
- **OD7: Cursor-only identity (the sketch's Q3).** Decided: the engineer selected "Conversation id (Recommended)", option (a) (row 145). It rests on C2 (OD9) and gates Phase 4, whose function-level design Identity and routing states. Every marker, bypass and lock path keys on a PID and session-id pair (rows 9 to 11). Under Cursor no ancestor is unique to one conversation, a sessionStart `env` value never reaches an agent Shell call, and window-close `sessionEnd` hooks do not run (row 126). It was the engineer's decision because each option either accepts a weaker identity guarantee or changes Claude Code's own marker and lock paths. The options:
  - (a) Recommended: host-neutral identity under the shim only.
    - The session id is the payload's `conversation_id`. A subagent's call takes its parent's, the payload's `parent_agent_id`, as Claude Code gives a subagent its parent's `session_id` (Identity and routing; r17 be R1).
    - A conversation counts as live while its `Cursor` main-process ancestor, matched by exact name, is alive (OD11(d); r15).
    - For this identity, lock ownership compares session ids, not PIDs.
    - From Phase 4, the directory pin's rewrite (Repo resolution) also exports the routed `CLAUDE_CONFIG_DIR` and that session id first, so `marker.sh` and the other scripts an agent runs resolve the state the hooks read. It rests on the rewrite holding in the IDE (row 137, OD9's C2), which C2 showed with an allow-carrying output and C7 with the output with no `permission` (row 244; r13; r14). C3 failed under Auto-Review (with Sandbox), so there those scripts write that state only through the user-level `sandbox.json` OD11(b) chose (r15).
    - The `_lib.sh` adapter branch applies only when the ancestor walk finds no live registered ancestor, so a Claude Code process resolves as it does today even with the adapter variables set (requirement 14; r3 ciso R1).
    - An agent's own command can re-export those names, so choosing (a) also accepts requirement 29's downgrade to the cooperative tier this repo's hooks assume (requirement 29; r20 ciso R3, sdet R1).
    - A conversation archived, or whose window closed, keeps its worktree lock until Cursor quits, and `check` names such locks (requirement 30; OD11(d); r15).
  - (b) Window-scoped identity: the shim passes the extension-host process as the session's PID and adds no identity code. Conversations in one window then share bypasses and worktree locks, the irreversible tier (row 10).
  - (c) A keeper per conversation, and (d) host-neutral identity on Claude Code's path as well, both set aside (Alternatives set aside).
  - If C2 fails, (a) loses its carrier into Shell calls, and the choice narrows to (b) or to dropping Cursor's claim for the gates that read session-keyed state, which goes back to the engineer.
- **OD8: a consuming repo's hooks receive Cursor's payload untranslated.** Decided: the engineer selected "Document it (Recommended)", option (a). The project-level mirror showed it (row 132), so a repo hook that tests for `Bash`, or reads a field only Claude Code sends, no-ops as the harness's gates did. Rows 34 and 56 promise that a repo's hooks run, and they do. It is the engineer's decision because row 34's promise and running each repo hook once compete. The options:
  - (a) Recommended: document it, and leave a repo's hooks as Cursor's import runs them. The import cannot be split (O9.e), so a translated run would be a second run of each repo hook beside the import's, doubling its side effects.
  - (b) A native per-event dispatcher that reads the workspace's `.claude/settings.json` at call time and runs its registrations translated, beside the import's untranslated run (Alternatives set aside).
- **OD9: design-gating checks before Phase 2 code.** Decided: the engineer selected "Run them, after plan-review (Recommended)" (row 145). Two parts stayed the engineer's before the checks ran: whether C1's `probe.py` change takes Run order step 4's restart or row 112's copy-and-`cmp` stand-in, and the run's stop point. The existing kit answered the checks before Run order step 6, under the admission rule. "OD9 check results" in the observations is the one home of their results, and each check below states what its result decided and gated (r12):
  - C1, before Phase 2: whether a Shell call's payload `cwd` names the directory an earlier `cd` in the same conversation entered (Repo resolution, row 136). Decided: it fails, because the payload `cwd` was empty on every Shell record. With C2 holding, Repo resolution pins each Shell call's directory through the rewrite, on C7's result (r13). Requirement 39 states no relative base, because every observed file-tool target arrived absolute (row 206). Phase 2's gate is met (r12). It needed record booleans the kit lacked, so the follow-up also recorded:
    - the relative `cwd` forms seen: empty, `.`, or a relative path;
    - which workspace root a relative `cwd` is relative to in multi-root;
    - whether the two fires of row 134's double fire carry the same `cwd` shape;
    - whether the agent's Shell process persists across calls, from the Shell legs' ancestry pseudonyms;
    - for Write, Delete and Read targets, whether each arrives absolute, relative or `~`-led, and, for a relative one, which base the file lands under. Requirement 39 denies every non-absolute target until this answer is in (row 206; r8 ciso R4);
  - C2, before Phase 4: whether a preToolUse `updated_input` replaces the executed Shell command in the IDE, O7(a)'s IDE half (OD7, row 137). Decided: it holds, so OD7(a) keeps its carrier into Shell calls, and OD7's narrowing on a failed C2 does not apply (r12). Repo resolution's directory pin rides the same rewrite. C2's probe sent it with an explicit allow, and C7 showed the form with none applies too (r13; r14);
  - C3, before Phase 4: whether an agent Shell call can write outside the workspace under Auto-review, with the sandbox on and off (the S3 leg miss, row 127). Phase 4's marker writes rest on it. Decided: it fails under Auto-Review (with Sandbox), and the IDE offers no Auto-review mode with the sandbox off. It holds under Allowlist with the sandbox off. Phase 4's marker writes from agent Shell calls take OD11(b)'s user-level `sandbox.json` (r12; r15). The directory pin writes nothing, so it does not rest on C3 (r13);
  - C4, before Phase 5a: whether `beforeReadFile`'s `content` holds the whole file (requirement 32, row 128). Requirement 32 names the fallback. Decided: it holds for a 108000-byte file, so requirement 32 takes no fallback, and Phase 5a's gate is met (r12);
  - C5, before Phase 2, from the kit's existing records with no new Cursor run (r3 be R3, be R8, sdet R2; r4 ciso R1, be R2, be R4). Decided: its results are in "OD9 check results", so Phase 2's gate is met, and each fallback after the list names the branch the results select (r12):
    - per event and per tool, the key paths with JSON types, including the Delete path key, the Grep keys, Task's keys, Write's keys for a create and for an edit (row 144), and `tool_output`'s type;
    - per event, sessionStart and sessionEnd included, which of `workspace_roots`, `cursor_version`, `conversation_id` and `generation_id` the Cursor records carry, and whether any CC1 or CC2 record carries one. The keys on every Cursor event and on no Claude Code record form requirement 38's predicate set;
    - per conversation, whether the hook-side and the Shell-leg-side nearest `Cursor Helper (Plugin)` ancestors are one process, by PID pseudonym from the first record to the last, and which variant each is (row 165);
    - whether any Shell-leg record carries `CLAUDECODE` (Identity and routing);
    - whether `session_id` and `conversation_id` share an equality class, and the id-grammar booleans;
    - whether `parent_agent_id` appears on main-agent calls (row 138);
    - the subagent id classes, because Claude Code shares `session_id` across a subagent and its parent (r7 be R4; r8 be R3; r9 be R2; r11 cd R14):
      - the equality classes among subagentStart's `conversation_id` where present, its `parent_conversation_id`, the subagent's later tool calls' `conversation_id`, and the main calls' `conversation_id`;
      - the same classes for S4's nested spawn;
      - whether a child call's `parent_agent_id` equals its subagentStart's `subagent_id` or `parent_agent_id`;
    - whether `loop_count` is 0 on the first stop of a later user prompt after a follow-up fired, where the records hold such a pair (row 225; r11 be R4);
    - whether afterAgentResponse precedes stop in one turn with one `generation_id` (row 139);
    - which of stop and afterAgentResponse carries `transcript_path`, for the context-cap twin (Twins; r10 sdet R2).

    A fact the records do not hold either blocks the piece it feeds or takes the fallback named here (r4 be R5):
    - Write's edit keys (row 144): they come from the hooks doc as `doc-only` paths, which translate and are reported unconfirmed under requirement 6 until acceptance step 2's Write-as-edit trigger observes them. If Cursor's payload lacks one, every such Write denies as a missing required key, which the Recovery section explains (Installer; r5 be R3). Selected: the records show `file_path` and `content` on both Write cells without showing a true edit, so this fallback stands (row 144; r12);
    - C1's base root in a multi-root workspace: a relative `cwd` there had no known base. Selected: C1 saw only an empty `cwd`, so it showed no base root (r12). The directory pin made the base root moot, because each Shell call starts in the directory it pins (Repo resolution; r13);
    - the predicate's keys on sessionStart or sessionEnd: that hook declines on the host walk alone (requirement 38). Not selected: both events carry the set (r12);
    - a hook-side host that differs from the Shell-side one: `_lib_cursor_host_pid` matches, by name, the one variant both sides descend from. With no such variant, or no record to judge, Phase 4 waits for the engineer, who chooses between OD7's option (b) and dropping the session-keyed gates' Cursor claim. Selected, with no such variant: one leg's hosts differed, pairing `agent-exec` with `retrieval`, and the hook side's variant changes between events (row 228). OD11(d) anchored liveness on the `Cursor` main process instead (r12; r15);
    - rows 138 and 139, and any other twin's premise: the twin is withheld as rows 140's and 141's are. Not selected for rows 138, 139 and 225, which hold (r12);
    - a subagent's tool calls carrying their own `conversation_id` (r7 be R4; r8 be R3; r9 be R2). Selected: they do, and the join key exists. A child call's `parent_agent_id` equals its parent's conversation id, which is also subagentStart's `parent_agent_id`, and a nested spawn's points at its direct parent, not the root. No depth-one limit is needed (r12). This revision states the mapping (r13):
      - what each piece keys on (r20; r21):
        - the active-subagent set keys on the direct parent: subagentStart's `conversation_id`, and a marked call's `parent_agent_id` (Twins, requirement 8);
        - the first-call mark keys on the call's session id (Installer; r19 be R6);
        - identity and locks key on the call's session id too, `parent_agent_id` when present and `conversation_id` otherwise, because an unchanged import copy can compute only that from its payload (Identity and routing; r17 be R1);
      - main-agent calls carry no `parent_agent_id` (row 138), and the attribution follow-up below saw it on every child call class it ran. So attribution checks marked calls only, against the direct parent's set, and needs no edge store, walk or root record (r21);
      - a child call's `parent_agent_id`, where it equals a subagentStart's `subagent_id` or `parent_agent_id`, is a join key as well as a matching `conversation_id`. It equals `parent_agent_id`, never `subagent_id` (r12);
      - with no join key, Phase 4 waits for the engineer, who chooses between OD7's option (b), which settles identity and locks but not attribution, and dropping the Cursor claim of the session-keyed gates and the agent-keyed gates, which settles both by removing them. Not selected: the join key exists (r12).
  Recommended, and chosen: run them, because C1 changes the translator, C2 changes Phase 4, and C5 builds the fixtures. The alternative built on each check's fallback and let the Phase 6 acceptance run decide, at the cost of rework if a fallback is wrong.

  The r13 revision added C6 to C8 under the same rule, and the engineer's selection above covered C1 to C5 only. Each ran before Run order step 6, while the kit still existed, and its result is in "OD9 check results" (r13; r14):
  - C6, before OD11(b) is asked: whether Cursor's sandbox can be given the routed config dir, or another path outside the workspace, as writable, from the docs and the IDE's settings. It also records whether an agent Shell call's write to `$TMPDIR` lands under Auto-Review (with Sandbox), by a kit sentinel (row 250). Decided: temp-dir writes land, and a workspace-level `sandbox.json` made a directory outside the workspace writable. A later cell showed a user-level file alone do the same (row 260). A path inside the routed config dir, and whether an agent can write either file, were not observed, and acceptance step 10's cells 1 and 7 take them. OD11(b) records the engineer's choice (r14; r15);
  - C7, before Phase 2: which `updated_input` output the IDE applies to an agent Shell call, under Allowlist with an empty allowlist, from project-level scratch entries and the kit's sentinels (row 244):
    - `updated_input` with no `permission` field: whether the rewritten command runs, and whether the call prompts;
    - the same with `"permission": "allow"`, the form C2 ran: whether the call prompts;
    - O7(b)'s IDE half, by the kit's sentinel boolean: whether a sibling beforeShellExecution entry receives the rewritten command (row 245).

    Decided: the output with no `permission` applied the rewrite, and the call prompted under both outputs, so Repo resolution's pin stands and Phase 2's OD9 gate is met. A sibling beforeShellExecution entry received the rewritten command, and a sibling preToolUse entry the agent's (rows 244, 245, 252; r14). C7's first cell, the output with no `permission` field, needed a kit edit, which took Run order step 4's rules as C1's `probe.py` change did (r19 cd R11);
  - C8, before OD11(d) is asked, from the kit's existing records with no new run, as C5 read them (row 251):
    - whether one depth-2 `Cursor` process is the ancestor of every hook and Shell leg between relaunches, by PID pseudonym;
    - how long each extension host appears in the records relative to the conversations that descend from it, which bounds its lifetime but does not measure it.

    Decided: every IDE and desktop record has a `Cursor` main-process ancestor, at no fixed depth, every conversation but 2 stays under one, and host lifetimes are bounded, not measured (row 251). OD11(d) records the engineer's choice it informed (r14; r15).
  - C9, before Phase 4: the CLI-coexistence premise check, which the engineer chose (row 316; r21). The session ran it, and its results are the session's observations, not the engineer's words. The run used a temporary user-level hooks file with sessionStart, preToolUse and beforeShellExecution probe entries, then one `agent -p --force --trust` run in an empty scratch workspace making one `echo` Shell call. C9 was that one `agent -p` run, launched from an external terminal. The file did not exist before and was removed after, and the scratch log is deleted. C9's results live here, because the observations section sits above this revision's region. [verified: this session's run, 2026-11-05]
    - The CLI loads a user-level hooks file: all three probe entries fired in the one run. So the premise holds.
    - A CLI hook's ancestry: the hook script, then the `agent` binary, then the launching shell and that shell's own ancestors up to the terminal emulator. A `cursor-agent` binary is the same CLI. No process whose name begins `Cursor` appears. The launching shell sat under a `claude` process because the session started the run from Claude Code, which is incidental.
    - Payload shapes: `cursor_version` is present in the IDE's form, with the CLI's own build string as its value. Whether that value differs from the IDE's payload value was not compared [unverified], so a shape compare cannot serve as a discriminator. `workspace_roots` is present, `session_id` equals `conversation_id`, and the Shell call's `cwd` is empty, as C1's were. sessionStart carries `is_background_agent`, false here, and the tool events do not.
    - Not tested, so [unverified]: whether a CLI Shell session gets a `sessions/` registration, which needs the adapter installed; the CLI's file-tool target forms; hot reload; an interactive (non-`-p`) CLI session; and a CLI launched from Cursor's integrated terminal.

    Decided: the premise holds, and the design goes to the Phase 4 re-plan, which starts from the Installer's CLI-coexistence block. Phase 1 recorded IDE hooks under a `Cursor Helper (Plugin)` process (O4), so an `agent` or `cursor-agent` nearest the hook, with no `Cursor`-prefixed process above it, is positive evidence of a CLI, where a missing `Cursor` was not (row 322; r21).
  - The attribution follow-up, before Phase 5a: a run in a scratch repository, across foreground, parallel, nested, background and built-in spawns. Its results live here, because the observations section sits above this revision's region. [verified: the follow-up run, as the session relayed it] (r21)
    - Every subagent Write, Delete, Shell, Task, Read and Grep call carried `parent_agent_id`: Write 30 of 30, Delete 10 of 10, Shell 15 of 15, Task 2 of 2, Read 41 of 41 and Grep 10 of 10. No main-agent call carried it.
    - The nested spawn's subagentStart was keyed to its direct parent's conversation.
    - subagentStart and subagentStop counts matched on every route: 1 and 1, 2 and 2, 2 and 2, 1 and 1, and 1 and 1, in the route order above.
    - No MCP server was configured, so whether a child's MCP calls carry `parent_agent_id` is [unverified].

    Decided: requirement 8 checks marked calls only, against the direct parent's active set, with no edge store, walk, root record or clearing route (Twins).
- **OD10: credential values in tool output Cursor cannot replace.** Decided: the engineer selected "Accept and disclose (Recommended)", option (a), after stating that claude-config tooling must not regress under Cursor. The credential arms block a Read whose content holds a credential shape, a command whose output is a credential by construction, and a Grep aimed at a credential path, and they redact MCP output (requirement 32). A credential-shaped value in Shell, Grep, WebFetch or Task output from any other source still reaches model context, which is irreversible (ciso R15), because the hooks doc limits output replacement to MCP tools (row 46). It is the engineer's decision because closing more of it changes how every Shell command runs. The options:
  - (a) Recommended: accept that residual as a channel gap, disclosed in the doc, with the detection arm's warning and count.
  - (b) Also rewrite every Shell command through `updated_input` to pipe its output through the redactor. That closes the Shell share only, rests on C2, and changes each command's streams and exit status.
- **OD11: decisions OD9's results opened.** The engineer selected "Restructure OD11 (Recommended)" (row 232). Items (a) and (c) are settled in the requirements (r13). The engineer decided items (b) and (d) once their checks had run (rows 255, 258; r15), and then item (e), which (b)'s decision opened (row 264; r16):
  - (a) The run-mode source. Settled: requirement 5 applies its "no" branch, so every result prints the unreadable line, and the doc states the precondition, Allowlist with an empty allowlist, as the engineer's own attestation (row 197). Requirement 5 holds the search for another source as an optional add-on that no phase waits on.
  - (b) Agent Shell writes under a sandboxed mode. Decided: the engineer selected "User-level sandbox.json (Recommended)" once C6's user-level cell had run, after typing that requiring the unsandboxed mode "means only Allowlist. Thats a regression from Claude's auto mode" (rows 257, 258; r15). What it changes, as this revision applies it (row 259; r17 cd R4):
    - Phase 4 builds, and Phase 5a's install first writes, a user-level `~/.cursor/sandbox.json` listing under `additionalReadwritePaths` the directories in the routed config dir that the agent-run scripts write, never the whole dir (Installer; r17 be R2);
    - `install` merges into the engineer's own file, `manifest.json` records the paths it added, and `uninstall` removes those and restores the file (requirement 18). Requirement 19's tamper comparison covers it (r17 plat R4);
    - no workspace-level `sandbox.json` is written, because row 2 bars an adapter artifact in a consuming repo;
    - a consuming repo's own workspace-level file wins the merge (C6's docs half) and may drop the entries, and a marker write then fails closed. The sessionStart `check --hook-env` entry counts such files and those that widen the sandbox. The terminal `check` cannot, because it runs in no workspace (r17 ciso R4);
    - the unobserved parts, whether Cursor protects either file from an agent Shell write and whether an entry reaches a directory inside the routed config dir, are acceptance step 10's cells 1 and 7 in Phase 5a's run;
    - the fallback: if cell 1's write itself misses with the file present, a fix PR stops writing the file, and requirement 5's precondition names Allowlist with the sandbox off as the only mode, the option Alternatives set aside records. A landing on cell 7, of either file, goes to the engineer with that fallback as the default (r17 ciso R4, sdet R5). The session reports either result to the engineer before any PR.

    The reasons:
    - under Auto-Review (with Sandbox), Cursor's default mode (row 52), an agent Shell call cannot write outside the workspace, temp dirs aside (C3, C6). With no such file, `marker.sh` run by an agent cannot write the markers and bypass files the gates read in the routed config dir. Hooks are not confined that way (row 127);
    - C6 saw a user-level file alone make a directory outside the workspace writable (row 260);
    - every sandboxed conversation's Shell can then write those directories, as an agent under Claude Code already can;
    - the file sits inside `~/.cursor`, so requirement 39's ask-in-chat deny covers a Write or Delete of it.

    Item (e) admits Auto-Review (with Sandbox) into requirement 5's precondition (r16).
  - (c) Deny-text channels. Settled: requirement 3's rule puts one deny text in `agent_message` and `user_message`, on row 182's one observation, and acceptance step 14's trigger 4 is the second observation (rows 238, 239).
  - (d) The host process Phase 4's identity records. Decided: the engineer selected "Anchor on Cursor main (Recommended)" (row 255; r15). What it changes (r17 cd R4):
    - `_lib_cursor_host_pid` matches the `Cursor` main process by its exact process name, not the nearest extension host, by the name read Identity and routing states (r17 plat R1);
    - a live registered `claude` ancestor still wins first (requirement 31), which matters because CC2's ancestry holds `Cursor` above `claude`;
    - the liveness rule of OD7's option (a), which the engineer chose (row 145), as row 254 said it would: a conversation's state now lives while Cursor runs (requirement 30);
    - its cost is the opposite error. A closed window's or archived conversation's worktree lock stays held until Cursor quits, and `check` names it. Its active-bypass file does not, because the reader evicts one after 60 minutes with no gating call (`_lib.sh:2913-2917`; r17 ciso). No idle timer enters liveness, so requirement 30 holds. Acceptance step 5 observes it;
    - set aside: OD7's option (b), under which a conversation whose hook-side host changes between events would split across PIDs (row 249), and dropping the session-keyed gates' Cursor claim, which C5's differing-host branch offered.

    The reasons, from C5 and C8 (rows 228, 251, 254):
    - a conversation's nearest plugin helper changed between events, so under the nearest-host rule a helper that exits while its conversation lives would make that conversation's markers and locks read as dead. Under OD7(a) the recorded host PID enters only liveness tests, on the paths row 229 read:
      - `marker.sh:879` writes it into the session's active-bypass file, and a reader tests it with `kill -0` (`_lib.sh:2913`);
      - the collision guard tests a lock holder's PID with `kill -0` (`:3525`) and reclaims a dead holder's lock (`:3253-3268`);
      - `session-keyed` skips `_lib_worktree_lock_is_mine`'s own-PID return (`:3291`);
      - `marker-clear-stale.py` keeps an active marker while its PID lives (row 161);
    - helper lifetimes are bounded, not measured, so the anchor removes the dependence on them instead of measuring it. A main process that exits should take its helpers with it, so the anchor should read as dead no state the nearest-host rule keeps live;
    - every IDE and desktop record has a `Cursor` main-process ancestor, at no fixed depth, so the anchor matches by name, not at depth 2 as this item first stated it;
    - 2 conversations span two main processes. Under the anchor, what one of them wrote under the first reads dead under the second: a lost bypass, which fails closed, and a lock another conversation can reclaim. Whether a relaunch caused it is not established.
  - (e) Whether requirement 5's run-mode precondition admits Auto-Review (with Sandbox). Decided: the engineer selected "Admit Auto-Review (Recommended)" (row 264; r16). Once the session explained the wider gap stated below, the engineer selected "Stands (Recommended)" (row 287; r18). What it changes, as this revision applies it (row 265; r17 cd R4):
    - from Phase 5a, requirement 5's precondition names both modes, once the permissions gate the admission rests on is installed. The run-mode line still reads no file (r17 be R2);
    - requirement 5's doc text discloses the gap as any Shell write to a listed directory, whatever its shape, and names the active-bypass directories as whole-gate releases (r17 ciso R3);
    - requirement 39's doc statement names Auto-review's own approval and its sandbox as that mode's layer in front of a Shell route;
    - requirement 39's workspace names gain `.cursor/sandbox.json` beside `hooks.json` and `mcp.json` in Phase 5a, with the same match and tests. Acceptance step 10's cell 7 records whether an agent Shell call can write one;
    - Allowlist (with Sandbox), which no cell ran, stays outside the precondition;
    - if OD11(b)'s fallback fires, the precondition goes back to Allowlist with the sandbox off alone;
    - set aside: keeping Allowlist with the sandbox off as the only mode, which would leave OD11(b)'s file serving no admitted mode (Alternatives set aside).

    The reasons:
    - requirement 5's Invariant leaves a residual to the precondition. Under Allowlist with an empty allowlist, the prompt carries it;
    - Auto-review ran a plain Shell call with no prompt (row 52), so under Auto-Review (with Sandbox), with the adapter's file in force, any command that writes a listed directory lands with no prompt, so it can forge a marker or a bypass;
    - Claude Code's auto mode carries the same residual. `enforce-marker-script-shape.sh` says a session under `auto` "has no such backstop", and that "this gap is a live, unmitigated path to forging a `/code-review` or `/plan-review` completion marker" (`:131-135`; row 261). The doc discloses it as that hook's header does;
    - a workspace file wins over the user-level one (C6), so a Write of one could widen the sandbox for the next conversation, which is why requirement 39 names it.

### Alternatives set aside

For Phase 1:

- **A committed, tested probe kit under `claude/.claude/scripts/cursor-adapter/probe/`.** Stow would install it for every stow consumer, with a test suite, a doc and a fixture. Its one advantage is that a re-run after a Cursor update needs no rebuild. Here a re-run rebuilds the kit from this plan's Phase 1 spec through another `code-writer` dispatch, and the observations name the Cursor version they were measured on (rows 66 and 68).
- **A hand-edited `~/.cursor/hooks.json` and an existing scratch repo.** It clobbers an existing user file, and a user-level probe fires in every workspace, the consuming project included.
- **User-level native registration for every scenario.** It fires in every workspace, so most of its containment would exist only to undo that. Project-level placement bounds a step to its own workspace.
- **A separate Cursor profile or `HOME` for the spike.** It would isolate the run from the live harness, but whether Cursor honors either is unverified, so the spike would rest on an unobserved premise. The control cells measure the live harness's interference instead (row 53).
- **A probe registered in `~/.claude/settings.json`, or in a user-level Claude local settings file.** The first is the tracked stow file every Claude Code session on the account loads, so editing it stages to this public repo. Whether Cursor imports the second, and whether Claude Code ignores it, are both undocumented. O28 observes the user-level file through the live harness instead, and row 41 records what that leaves open.
- **`nohup cmd &`, or `&` with `disown`, for `detach`.** Both leave the child in the hook's session and process group, so a runner that signals the group kills it, and O6 would measure a start no keeper should use. `disown` also keeps every descriptor the hook inherited open.
- **A shared `_detach-lib.sh` recipe.** Its `python3 -c` needs fork control flow to avoid `setsid`'s EPERM in a process-group leader, which `shell-script-conventions.md` puts in a `.py` file instead. No keeper has been chosen to share it with, and `Popen(start_new_session=True, close_fds=True)` gives O6 the properties any later keeper must meet.
- **A Python 3.9 compatibility path.** The engineer chose to pin a supported version (row 57), and the repo already pins 3.11 (row 58).
- **A jq sanitizer.** jq may be missing from a GUI-launched PATH, which O12 records.
- **An all-Python probe entry.** It hides which bash a stowed hook's shebang finds, and a below-floor interpreter fails before it can report itself.
- **Raw output under `$TMPDIR`.** A hook's `TMPDIR` can differ from a terminal's. A fixed kit root resolves the same way from every context.
- **The control cell alone, or a by-hand count of new `sessions/` entries, as evidence that the user-level file runs.** The control cannot tell a call no hook saw from one a hook allowed. A by-hand count cannot tell a Cursor-origin entry from a concurrent Claude Code session's.
- **Reading the kit's code in place of `selfcheck`, or no check.** Reading cannot show that no payload fragment reaches a record, or that every behavior is inert out of scope. With no check, such a bug shows only during the run.
- **Spending probes on O13 and O14.** The hooks doc already answers both (row 46).
- **Cursor's Hooks view as the payload source.** Whether it shows payloads is unverified, and it would put payload values in front of the engineer to transcribe, so the observations admission rule after Run order step 6 would still apply. If the fetched hooks doc says it shows payloads, the engineer can cross-check O1 and O16 by eye.
- **An IDE cell for `~/.cursor/cli-config.json`'s `permissions.deny`.** The doc scopes that file to the CLI (G7), and an undocumented IDE behavior cannot carry requirement 4's invariant.
- **An expiry timer on the active cell.** `note` clears it, and allow-class outputs need the cell's exact command, so a stale cell can only deny inside a scratch repo.
- **Per-conversation cell resolution from payload tokens.** Every `--emit cell` step runs in one conversation, so it served no scenario, and on an event whose payload held no token a stale token outranked the engineer's explicit `cell`.
- **Per-file hashes inside the two kit roots.** Only the kit creates those roots, and the run's own writes change files in them, so a hash stop there could never complete. The hash check stays on the `~/.cursor` files, which the engineer may also own.
- **`git commit --allow-empty` as the `Bash` live-gate trigger.** `require-code-review` exits 0 on an empty staged diff (`require-code-review.sh:129-137`), so it never reaches the deny (row 64).
- **A linked worktree in `scratch-b` to keep the worktree gates out of the live-gate cells.** An untracked `.claude/worktree-optout` does the same with one file (row 64).
- **Self-expiring `failClosed: true` steps.** Project-level placement already bounds them to the scratch repos.

For the adapter:

- **Turning the import off.** It drops the consuming repo's own `.claude/` config (row 34).
- **Import off, with the shim also wrapping the consuming repo's hooks.** `~/.cursor/hooks.json` is static, so this needs a per-event dispatcher that reads each repo's registrations at call time, and the repo's skills and agents would still need the import.
- **Sharing Cursor's process PID across conversations.** It breaks lock ownership (row 10).
- **Delivering `CLAUDE.md` through sessionStart `additional_context`.** sessionStart does not block the loop (row 28), so the first turn could run without it.
- **`model: inherit` for every agent.** It drops the model routing `test_agent_roster.py` encodes. It remains the fallback for an alias `model-map.json` lacks.
- **Import-only, candidate A.** It fails A1, A2, A3, A6, A8 and A9 (row 119).
- **Translation inside `_lib.sh`'s shared parser.** About 24 hooks read their own fields from raw stdin (row 12), so they would miss it, and the import's copies would still fail open on a crash (O17).
- **Per-hook Cursor branches.** Each of 58 hook files would carry its own host logic, a second home for one translation.
- **One dispatcher entry per Cursor event.** It reimplements the parallel runner Cursor already has (O22.a), and it runs a call's `Bash` hooks one after another.
- **A `_lib.sh` guard that retires the import's copies.** An imported copy cannot observe whether its shim twin runs, so the guard stacked proxies for that fact: environment names, a manifest, a whole-file hash of `hooks.json`, and process ancestry. Each proxy opened a gap. A stale hash, or a Cursor that stopped loading the user-level file, would retire the working Read and Task gates silently, and a repo's checked-in `env` reaches hooks (O15.c), so it could forge the environment tests. Leaving the copies running costs a repeated deny text and their runs, because Cursor lets any deny win regardless of source (row 148). The two session hooks' decline (requirement 38) is not a guard: it drops only a state write, and retires no gate.
- **The system-level managed hooks file as the enforcement root.** macOS reads it from `/Library/Application Support/Cursor/hooks.json`, outside the agent's reach if root-owned, which is the ciso review's foundation concern. It is set aside for these reasons (row 152):
  - any deny wins regardless of source, so its rank buys nothing for a deny, and for `followup_message` a lower-priority source overrides a higher one;
  - it would protect only the file, while its entries still run the shim from a user-writable checkout;
  - its install and every update need root;
  - it applies to every user account on the Mac;
  - the slot may already be held by another tool;
  - it runs from the managed config directory, not `~/.cursor/`.

  The adapter does not rely on that file's owner or mode. Requirement 39 protects the user-level file instead, and the doc states that the system-level file is outside the adapter's control.
- **A Shell-command matcher for the adapter's own files.** A matcher that looks for protected names in shell text cannot be made safe. Each shape it misses, such as a variable, an encoding or a script file, needs another layer, and the layers compound. The harness guards its own settings with file-edit asks and no Shell matcher (row 192), and requirement 39 does the same. Cursor's run mode is the only layer in front of the Shell route, though the vendor does not call it a boundary (G7), and the tamper check is its detection (row 190; r8 ciso R6).
- **Carrying the identity into the import's copies through a sessionStart `env`.** O18.a saw such a value reach imported hooks, but whether it arrives before a conversation's first call (O5.c) or survives a resume (O25) is unobserved, and a copy without it would deny in a linked worktree. A copy's own payload carries its `conversation_id`, which the worktree guard hands to the resolver instead (Identity and routing).
- **Recording the conversation id inside `_lib_parse_tool_input_or_deny`.** `test_lib.py` pins that function's jq field list and read order (row 174), so a new field fails it. A global the parser set would also reach a script that never parsed, through an inherited variable of the same name. The worktree guard passes `$INPUT` to the resolver instead.
- **Telling the lock rule it runs under the adapter through a variable.** The resolver runs in a command substitution, so a variable it set never reaches the caller (row 176), and an environment value equal to a registered id could flip the rule. Whether the caller's PID is registered already marks the branch that resolved it, and the worktree guard passes that to the lock rule as an argument (Identity and routing).
- **Declining the session hooks on an environment name, such as a missing `CLAUDECODE`.** Whether a repo's `env` block reaches Cursor's hooks is unobserved (row 179), so a repo could make a copy register its host. The payload's keys and the process tree come from Cursor and the OS, not from repo content.
- **Observing whether a copy's allow suppresses an `ask`, then patching the copies.** Under Cursor, preToolUse has no context channel (row 47) and the twin's allow already maps to `{}`, so suppressing the copies' allow costs nothing. The design then rests on no answer to that question (row 183).
- **Truncating an over-cap payload, or a path-only parse of one.** A truncated JSON payload cannot be parsed for its path, and reading past the cap removes the bound it exists for. The 8 MiB cap sits above the largest observed payload, so an over-cap call denies (requirement 37).
- **A differential against `origin/main`'s `_lib.sh`.** Once a phase merges, the baseline holds the change, so the comparison is vacuous on main, and sourcing a file cannot see a changed function body. Requirement 14's committed snapshot pins the touched functions' results instead.
- **A documented gap when C1 and C2 both fail.** A gate that evaluates the wrong repository is enforcement loss, and row 115 allows a documented gap only where Cursor lacks the tool, the event or the channel. The taint rule blocks instead (row 166).
- **The taint rule while the rewrite applies.** It needs no rewrite, but it denies each later Shell call without a leading absolute `cd` in a conversation that changed directory, which Claude Code runs. The directory pin runs those calls in a known directory. The taint rule stays row 170's default for when the rewrite is unusable (Repo resolution).
- **Rewriting `tool_input.cwd` through `updated_input` instead of the command.** It would leave the command text alone, with no pin prefix, stripping or matcher rule. Whether Cursor honors a changed `cwd` is unobserved, because C2 replaced only the command, and Phase 4's routing exports rewrite the command text anyway, so the stripping and the matcher rule would still be needed (r19 cd R2).
- **C1's trailer, recording `pwd -P` after each command.** It writes under the adapter state dir, so it needs an agent Shell write outside the workspace, which C3 showed failing under Auto-Review (with Sandbox). It also leaves a call's directory unknown while its record is pending.
- **A bare `cd -- '<dir>' &&` prefix for the pin.** A failed `cd` would still run any part of the command after a `||` or `;`, in Cursor's carried-over directory, which no gate evaluated. The pin prefix's `|| exit 1` runs none of it.
- **A bare `cd` in the pin prefix, with no `builtin`.** A `cd` function or alias in the engineer's shell startup files could report success without changing directory, so the agent's command would run where no gate evaluated. `builtin` costs one word (r19 sdet R2).
- **A brace wrapper, `cd -- '<dir>' && {`, the agent's command, then `}`.** It also runs none of the command on a failed `cd`, until the command closes the group with its own `}`, which the ciso-reviewer's round-10 scratch run showed (row 267). The group also changes how the agent's lines parse: a last line ending in a backslash joins the closing `}`, and a `shopt -s extglob` or alias line no longer applies to the lines after it in the same group (the staff-platform-engineer's round-10 runs). The `|| exit 1` line groups nothing, so it needs no enterability pre-check and leaves the agent's lines parsed as with no adapter. The session chose the `|| exit 1` line (r18; r19 cd R11).
- **The leading-`cd` exception by repository identity, a path with the same git common dir as a root.** It admits every member of a root's worktree set, the main checkout and sibling worktrees included, so a two-`cd` command can start a gate that resolves once in a tree the engineer did not open (the ciso-reviewer's round-11 R1). It also runs `git` on a path the agent named. The engineer chose containment in a root instead (row 292).
- **Mapping every Cursor Write to Claude `Write`.** `deny-plan-writer-off-plan-writes.sh` denies a plan-writer agent every tool but `Edit` (row 143), so a plan-writer could not edit its plan under Cursor.
- **A keeper process per conversation (I2), OD7's option (c).** No signal marks a conversation's end (O28.b), so a keeper lives until its window closes or leaks. It still needs the `updated_input` rewrite to reach Shell calls, and it stacks a keeper, a heartbeat, respawn and an index on one mismatch.
- **Host-neutral identity on Claude Code's path too, OD7's option (d).** It changes every marker and lock path Claude Code uses, against row 114.
- **A sessionStart `env` value, or `launchctl setenv`, to route agent Shell calls.** The first reaches hooks but no agent Shell call (O5). The second sets the variable for every GUI app, and for Claude Code started in Cursor's integrated terminal.
- **Per-call subagent attribution.** No payload field gives it (O8.b).
- **Denying every subagent call that touches Shell or a write.** It also blocks `code-writer`, whose job is writing.
- **The credential detection arm alone.** It warns after the value has reached model context, so it is no control for any route a pre-call event covers.
- **Cursor's global ignore list for the credential and `Read` deny paths.** It hides files from the agent's file tools but not from its terminal (G7), so the gate stays (row 78).
- **A per-event dispatcher for a consuming repo's hooks, OD8's option (b).** `~/.cursor/hooks.json` is static, so the dispatcher reads each repo's registrations at call time, and each repo hook still runs a second time through the import.
- **Redacting Shell output by rewriting each command through a filter pipe, OD10's option (b).** It changes each command's streams and exit status, rests on C2, and leaves Grep, WebFetch and Task output uncovered.
- **A `type: "prompt"` hook as a gate.** A model evaluates it (G9), so its decision is not deterministic.
- **A `cursor` stow package for the generated files.** It would install `~/.cursor/` files for every stow consumer, and a tracked file cannot hold the machine-local routed config dir (rows 2, 8).
- **An adapter-written `~/.cursor/permissions.json` that turns `permissions.ask` into Auto-review `block_instructions`.** The dispatching session recommended against it, and the engineer set it aside (rows 227, 240). A model judges those instructions, and only under Auto-review, so the result is an advisory nudge. It adds another user-level file for `install` to merge and `uninstall` to restore, and requirement 39 already protects the path.
- **Allowlist with the sandbox off as the only mode, OD11(b)'s first option.** It adds no mechanism, but under Cursor's default Auto-Review (with Sandbox) a marker-gated operation such as a reviewed commit denies until the engineer changes mode, which the engineer called "a regression from Claude's auto mode" (row 257). It stays OD11(b)'s fallback. It was also OD11(e)'s second option, which the engineer set aside (row 264; r16).
- **A workspace-level `sandbox.json`.** C6 showed one works, but it puts an adapter artifact in a consuming repo's tree, which row 2 bars.
- **The whole routed config dir in `additionalReadwritePaths`.** It would make every state file there, `settings.json` and `sessions/` included, writable from every sandboxed conversation's Shell, where the scripts' own directories suffice.
- **A hook relay through a temp dir, OD11(b)'s third option.** Agent scripts would write a request there for the next hook event to apply. It redesigns Phase 4's routing, and makes a directory any process of the user can write an enforcement input. A gate can read before a parallel entry of the same event applies the request (O22.a). The carrier is lost where a hook's `TMPDIR` differs from the agent's, or where `disableTmpWrite`, a key the sandbox reference lists, is set.
- **The nearest `Cursor Helper (Plugin)` ancestor as identity's liveness anchor.** A conversation's nearest helper changed between events (row 228), and helper lifetimes were bounded, not measured (row 251), so a helper's exit could make a live conversation's markers and locks read as dead (OD11(d)).

### Assumption ledger

**Root.** The harness assumes Claude Code's payloads, tool names, process tree, permission engine, and config-dir routing. Under Cursor's local IDE, its gates therefore either deny forever or never fire. This plan makes every gate enforce identically under Cursor using only user-level artifacts, with no change for Claude-Code-only stow consumers, while the consuming repo's own `.claude/` config still loads and its hooks run without overriding the harness's denies.

**Givens.**

- G1. Cursor has no Skill, ExitPlanMode, AskUserQuestion, or plan-mode tool event, and no Notification or PostToolBatch hook event. The import does not map SubagentStart. Reason: Cursor defines its own tool and event set. [verified: cursor.com/docs/agent/hooks and /docs/reference/third-party-hooks, as relayed by the dispatching session; the staff-backend-engineer round-1 review fetched both pages and found it matches]
- G2. preToolUse "ask" is not enforced; only beforeShellExecution and beforeMCPExecution honor ask. Reason: vendor contract. [verified: hooks doc, as relayed; the staff-backend-engineer round-1 fetch matches] The Auto-review run agrees: an imported preToolUse `ask` ran with no prompt, and a native beforeShellExecution `ask` prompted (Phase 1 observations, Auto-review).
- G3. Cursor subagent files have no `tools` field, and subagents inherit every parent tool. Reason: vendor contract. [verified: subagents doc, as relayed]
- G4. Cursor cloud agents cannot run `~/.cursor/hooks.json`. Reason: vendor architecture. [verified: as relayed; the staff-backend-engineer round-1 fetch matches]
- G5. Every matching hook from every source runs, and the two Cursor pages state different rules for merging their responses. Reason: vendor contract. O20 records which rule holds.
  - The third-party-hooks page: "All matching hooks from every source run." and "When responses conflict, higher-priority sources take precedence during merge."
  - The hooks page: "any `deny` wins over `ask`, and `ask` wins over `allow`, regardless of source. `user_message` and `agent_message` values are concatenated. For other fields, such as `followup_message`, the last response wins ... for those fields a lower-priority source overrides a higher-priority one." The ellipsis elides "Responses merge in the priority order below, so".
  - The order those statements refer to: the hooks page ranks the managed source, then the Team source, Project `.cursor/hooks.json`, then User `~/.cursor/hooks.json`, with plugin hooks after all of them (paraphrased). The third-party-hooks page continues the list with Claude project local, Claude project, then Claude user.

  [verified: both pages, as relayed by the staff-backend-engineer round-1 review, which fetched them on 2026-10-26; the first quote was also relayed by the dispatching session; the elided words and both orders as relayed by that reviewer's round-2 fetch]
- G6. One UI setting governs the import of third-party plugins, skills, and configs, from user-level and project-level sources alike, and it is on by default. Reason: vendor UI. [verified: third-party-hooks doc, as relayed] Its all-or-nothing scope is [unverified]: the dispatching session relayed it, the staff-backend-engineer round-1 review found it not stated on the page, and O9 records whether finer toggles exist.
- G7. Cursor documents no user-level IDE deny list for Shell, Read or Write. Its user-level ignore list hides files from the agent's file tools but not from its terminal, and its deny list belongs to the CLI. Reason: vendor contract.
  - cursor.com/docs/context/ignore-files: "Set ignore patterns for all projects in user settings to exclude sensitive files without per-project configuration. The global ignore list is empty by default." and "The terminal and MCP server tools used by Agent cannot block access to code governed by .cursorignore". Its default ignore list already includes `.env*`.
  - cursor.com/docs/cli/reference/permissions puts `permissions.deny`, with forms such as `Shell(rm)` and `Read(.env*)`, in `~/.cursor/cli-config.json`, on a page about "your CLI configuration".
  - cursor.com/docs/reference/permissions puts only `mcpAllowlist`, `terminalAllowlist` and `autoRun` in `~/.cursor/permissions.json`, calls them "Not a security boundary", and says "CLI permissions are separate." The run-modes page, as the dispatching session relayed it for OD9, gives that file a different schema, and OD9's run found it absent under every mode (row 197; r12).

  [verified: those three pages, as relayed by the staff-backend-engineer round-2 review, which fetched them on 2026-10-26; that no IDE deny list exists is that review's reading of them and of the Agent Security page]
- G8. The desktop app may send the first prompt after a launch before user hooks load. The hooks doc, paraphrased: by default the desktop app waits only briefly for hooks to load before it sends a prompt, and a "Block until hooks are ready" setting in the dashboard's hook settings makes it wait with no timeout until every hook source has loaded, so hooks run on every prompt, the first after a restart included. Reason: vendor behavior, and the setting lives in Cursor's dashboard, outside the local IDE. [verified: hooks doc, as relayed by the staff-backend-engineer round-2 review] O29 measures the window.
- G9. The hooks doc lists the `postToolUseFailure`, `afterAgentThought` and `workspaceOpen` events and `type: "prompt"` hooks, which a model evaluates. Reason: vendor docs. [verified: the saved copy of cursor.com/docs/hooks this revision read, `:203`, `:936-968`, `:1294-1314`, `:1442-1463`] The Cursor pages this plan cites moved:
  - the hooks page to cursor.com/docs/hooks;
  - the subagents page to cursor.com/docs/subagents;
  - Run Modes to cursor.com/docs/agent/security/run-modes;
  - the ignore-files page to cursor.com/docs/reference/ignore-file.

  The givens above cite each page where its fetch found it, and Critical files fetches the new locations. [unverified for the moves: relayed by the dispatching session; plan-architect fetched no page]

**Rows.**

1. The scope is the whole adapter. [engineer-verified: "I think plan-it for the whole adapter."]
2. No harness config or adapter artifact lands in a consuming repo, so project-level `.cursor/` files, `.claude/` config, and project-scoped plugin installs in a consuming repo are out. Phase 1's project-level files sit only in the kit's own scratch repos. [engineer-verified: "If getting my harness to work requires landing code or markdown in the [private project]'s repo, that's not acceptable."] The harness's normal work output, such as plan files under `.claude/plans/` and worktrees under `.claude/worktrees/`, falls outside that constraint. [engineer-verified: "Plans can be committed and have been committed to repos... That's how the harness works"] That second quote answered a question about exactly those two paths. The first quote carries row 59's substitution.
3. Folded into row 91.
4. The spike runs in a scratch repo or this repo, never against a consuming project's code. [unverified: the dispatching session relays this as said earlier, with no quote] Adopted, because it only narrows where the spike runs.
5. The target is the local Cursor IDE on this Mac. [engineer-verified: "This Mac, local IDE (Recommended)"] The option's description is the session's wording. This plan puts the CLI out of scope as an adapter target. Row 112 uses it as a probe launcher, and row 313 lets a CLI session run beside the IDE with the adapter installed, still with no adapter support for the CLI (r20). Row 322 moves that requirement's design to the Phase 4 re-plan (r21).
6. Every gate must enforce under Cursor as it does under Claude Code. [engineer-verified: "Full parity"] The option description, including "some may be impossible without a Skill event", is the session's wording. The rule that a gate unable to reach parity becomes an open decision, never a silent drop, comes from the dispatch, not the engineer.
7. Cursor's harness state routes through `CLAUDE_CONFIG_DIR` to the config dir of an account the engineer chose. No account is named. [unverified: the session's wording]
8. That config dir is a machine-level value the user supplies, never a literal in a tracked file. [unverified: the session's proposal, adopted under row 42]
9. Session identity resolves in two places:
   - Hook side: `_lib_hook_claude_pid` takes `$PPID`, and accepts `CLAUDE_PID` only when it equals `$PPID` or `$PPID`'s parent (`_lib.sh:3046-3056`).
   - Script side: `_lib_resolve_claude_pid` walks the process ancestors to a start-time-validated `sessions/<pid>` file (`_lib.sh:3075-3117`).

   `capture-session-id.sh` writes that file and rewrites active-bypass PIDs (`:85-112`). `marker.sh` aborts with "SESSION_ID empty" when the walk fails (`marker.sh:140-141`). [verified]
10. A worktree lock that records the caller's own PID belongs to the caller. A lock from another PID with the same session id belongs to the caller unless that PID is a live registered session (`_lib.sh:3287-3296`). [verified] Conversations sharing one PID would therefore share locks, and the worktree gates carry the irreversible tier (Step 3 exploration).
11. `marker-clear-stale.py` judges liveness from `sessions/<pid>` files (`:97-144`). [verified]
12. The shared parser reads `tool_name`, `tool_input.command`, `cwd`, `session_id`, `tool_input.file_path`, `agent_type`, and `agent_id` in one jq call (`_lib.sh:526`), and denies on an empty `tool_name` (`:555-558`). [verified] About 24 hooks read their own fields: `tool_input.subagent_type`, `tool_input.prompt`, `skill`, `args`, `content`, `new_string`, `edits`, `planFilePath`, `tool_response`, `.prompt`, `.source`, `.transcript_path`, `.permission_mode`, `.reason`, and `.last_assistant_message`. [verified: Step 3 exploration report] Hooks also read `.prompt_id` (`advance-past-commit-stall.sh:97`) and `.hook_event_name` (`announce-branch-review-history.sh:31`, `track-permission-prompts.sh:46`). [verified]
13. Hook registrations span `settings.json:127-615`. Gate entries carry `"onFailure": "block"` (for example `:266`, `:384`, `:400`), and four `if:` entries sit at `:283-306`. [verified]
14. `require-stow-reminder.sh` and `deny-escaped-backticks-in-pr-body.sh` filter on the command text themselves. [unverified: both reference `gh pr create` or `gh pr edit`, but neither was run] Requirement 22 runs all four `if:` entries (row 13) against a non-matching command.
15. The hook headers declare 29 `gate`, 2 `turn-gate`, and 2 `batch-gate` classes; the rest are `informational`. [verified: grep `^# hook-class:` over `hooks/*.sh`]
16. A `hook-class: gate` file under `hooks/` must be wired into a PreToolUse group in `settings.json` (`test_hook_alignment.py:470-489`). [verified]
17. `config-keys.psv` values are booleans or enum tokens only (`:15-22`). [verified]
18. `review_orchestrator_routing` and `permission_prompt_tracking` both default to false (`config-keys.psv:109`, `:93`). [verified]
19. No hook twin exists for any of the 17 `Bash` deny entries: `Bash(sudo *)` and `Bash(sudo)` (`settings.json:48-49`), and the 15 package-manager entries (`:63-77`).
    - No hook names brew, gem, cargo, `go install`, `gh extension`, mas, `pipx install`, apt, yum, dnf, apk or zypper; Homebrew appears only in comments and messages.
    - `deny-network-installs.sh:195` skips `sudo` as a prefix word.
    - `_lib_fragment_command_word` also skips `sudo`, `doas`, `env`, `command` and other wrapper words (`_lib.sh:2109-2132`), so a gate that resolves the command word through it never matches a pattern whose first word is `sudo`.

    [verified: grep over `hooks/*.sh`; `_lib.sh:2132`]
20. `redact-credential-values.sh` rewrites tool output through `updatedToolOutput` (`:49`). [verified]
21. `require-plan-review.sh` gates ExitPlanMode as well as writes (`:4-9`, `:79-80`). [verified]
22. `require-routing-read.sh` exits unless the tool name is exactly `Agent` (`:37`). [verified]
23. Agent `tools:` lists (`claude/.claude/agents/*.md`). [verified]
    - The reviewers have Read, Grep, Glob, Bash, and Write.
    - `plan-architect` has Read, Grep, Glob, and Edit.
    - `Explore` has Read, Grep, and Glob.
24. `_lib_split_fragments` (`_lib.sh:2086`) and `_lib_fragment_command_word` (`:2109`) are shared command splitters. [verified]
25. `select-tests.py` selects the scripts tests for any path under `claude/.claude/scripts/` (`:611`), and also the hooks and skills tests for a shell script there (`:837`), so an adapter file selects its own tests. [verified] The reverse direction has gaps. [verified]
    - The agents (`:850-857`), rules (`:858`), `docs/` (`:862`) and global `CLAUDE.md` (`:868`) rows omit the scripts tests.
    - `TestCrossDomainReadCompleteness` (`test_select_tests.py:332-367`) fails a test that reads a path through a module-level constant when that path's rules do not select the test.
    - A later scripts test that reads agents, rules, `CLAUDE.md` or docs therefore needs new rows in `select-tests.py` and `test_select_tests.py`.
26. Hooks need bash 5.2 or later, and a lower bash makes every hook fail to source `_lib.sh` (`_config.sh:27-68`). [verified]
27. `_lib_config_dir` resolves to `$CLAUDE_CONFIG_DIR`, falling back to `$HOME/.claude` (`_config.sh:110-124`). [verified]
28. Cursor user hooks, as relayed from the hooks doc. [verified: hooks doc, as relayed]
    - They live in `~/.cursor/hooks.json` and run from `~/.cursor/`.
    - Entries support `failClosed`, per-script regex matchers, and `loop_limit`, which defaults to 5 in Cursor and is null in Claude Code.
    - sessionStart returns `env` and `additional_context` without blocking the loop.
    - `permission` takes allow or deny.
29. The import's event and tool maps are as relayed. That doc does not cover `tool_input` translation, `additionalContext`, `if`, or `onFailure`. [verified: third-party-hooks doc, as relayed] For other exit codes it says "Other exit codes: Hook failed, action proceeds (fail-open)", which O17 tests against `onFailure`. [verified: as relayed by the staff-backend-engineer round-2 review]
30. A `~/.cursor/agents` file wins over a same-named `~/.claude/agents` file, and `model` takes IDs with bracket parameters. [verified: subagents doc, as relayed]
31. Cursor scans `~/.cursor/skills`, and with the toggle on also `~/.claude/skills` and `~/.claude/agents` (Context). [verified: skills and subagents docs, as relayed] Every account dir's tracked entries link into the same checkout, so bare `~/.claude` holds the routed dir's tracked skills, agents, and `CLAUDE.md`. [unverified: the dispatching session's reading of the engineer's machine setup]
32. User-level `~/.cursor/rules/*.mdc` files apply with `alwaysApply` and with `globs`. [unverified: the relayed rules docs say only that user rule files stay local] O11 settles this once S10 runs (Out of scope).
33. Every observation in the Phase 1 table is answerable in the local Cursor IDE (row 71), with the kit's scenarios. [unverified] An observation the run cannot answer is recorded with its reason, and the post-spike revision counts every criterion that depends on it as unmet, except one that reads O20, which takes its outcome from OD6.
34. The consuming repo's own `.claude/` hooks, skills, and agents must work in Cursor. [engineer-verified: "We definitely need to include whatever hooks, skills, or agents are in a repo... I don't know why we would exclude repo-specific ones for cursor."]
35. Keeping the import on follows from row 34, because the import is the only relayed way to load repo-level `.claude/` config without a file in the repo, and it is all-or-nothing (G6). [unverified: the dispatching session's reading of row 34's quote]
36. Where Cursor cannot prompt, an ask becomes a deny that tells the agent to ask in chat. This covers `ask-review-permissions`, `ask-new-dependency-disclosure`, and the `Edit(//**/.claude/settings*.json)` ask entry. [engineer-verified: "Deny + ask in chat (Recommended)"] The option's description, which leaves the edit itself to the engineer, is the session's wording. It follows from the deny, which a retried call meets again. [unverified]
37. `require-plan-review`'s ExitPlanMode arm becomes a parity exception, and its write arm, which blocks implementation edits before review (row 21), is the boundary. [engineer-verified: "Write gate is enough (Recommended)"]
38. `_lib.sh` sources `_config.sh` before anything else (`_lib.sh:25`). `_config.sh` checks the bash floor first and returns 1 below it (`:40-68`), so every gate denies through its `_lib.sh` bootstrap (`docs/hooks.md` § "Bash version"). Code above that check must parse on bash 3.2 (`.claude/rules/shell-floor.md`). [verified]
39. All 58 hook scripts reference `_lib.sh`. [verified: grep over `hooks/*.sh`] The six checked source it through `if ! . "${0%/*}/_lib.sh"`. [verified: `require-code-review.sh:53`, `capture-session-id.sh:50`, `session-marker-dashboard.sh:47`, `bootstrap-project-plugins.sh:18`, `redact-credential-values.sh:15`, `nudge-long-turn-subagent.sh:66`] That every hook does so, with no side effect above that line, is [unverified]. No guard ships (row 148), so no design here rests on it.
40. Claude Code sets `CLAUDECODE=1` in its Bash tool's environment. [verified: `.claude/plans/git-pre-commit-hooks-spike.md` row 7] Whether its hook processes carry it is [unverified]; O15 records it.
41. A user-level hook the import runs sees the same variable names as a project-level one. [unverified]
    - The probe could register only at project level without editing the tracked `~/.claude/settings.json` (Alternatives set aside).
    - O28(a) shows whether the user-level file runs at all.
    - O28(c) observes at user level the variable candidate A depends on, `CLAUDE_CONFIG_DIR`.
    - The full name comparison stays open, because observing user-level env names needs a probe in that tracked file.
    - B4's mirror reading is therefore necessary, not sufficient. No guard relies on it (row 148).
    - An imported copy reads unrouted markers in bare `~/.claude` (row 7), so a marker-gated copy that reached its marker check could deny where its twin allows. Requirement 40's import arm tests for it.
42. Account names stay out of this public repo (`CLAUDE.md` § "Redact private-project-identifying content"). [verified] This repo's own policy is within its reach, so it is a row rather than a given.
43. The plan is revised spike-first. [engineer-verified: "Spike-first plan (Recommended)"] The label answered the session's question about how to revise the plan once round 1 showed that Phases 2 to 6 were designed before the spike that decides their shape.
44. The content adopted under that label is the session's proposal: Phase 1 fully specified, with every observation the reviewers asked for (import fidelity, merge rules, the no-decision matrix, matcher domains, native Cursor permissions, timing); Phases 2 to 6 as a design sketch, rewritten from the evidence and re-reviewed before any adapter code; and the adapter still the goal. [unverified: the option description was the session's wording, adopted under row 43]
45. Two pinned tests conflict with candidate B as round 1 drew it. [verified]
    - `test_bash_floor.py:232-234` asserts that `_lib.sh`'s first command sources `_config.sh`, so a guard above that line fails it.
    - `test_hook_alignment.py:2152-2166` requires `docs/hooks.md`'s tier table to list exactly `GATE_HOOKS`, which `:106-135` and `:162` draw from `hooks/` and the plugin hook directories only, so a table row for a gate under `scripts/` fails it.
46. The hooks doc answers O13 and O14, and both answers leave a gap. [verified: hooks doc, as relayed by the staff-backend-engineer round-1 review, which fetched it on 2026-10-26]
    - The stop input is `{status, loop_count}` plus the common fields, with no last-message field, and of a `followup_message` the doc says "Cursor will automatically submit it as the next user message", as the staff-backend-engineer round-2 review corrected the quote.
    - `updated_mcp_tool_output` is "For MCP tools only", so postToolUse cannot replace a Shell or Read result.
    - preCompact is "observational ... cannot block or modify", and its only output is `user_message`.
47. As relayed from the hooks doc, beforeSubmitPrompt outputs only `continue` and `user_message`, subagentStart only `permission` and `user_message`, and preToolUse has no `additional_context`; only postToolUse, postToolUseFailure and sessionStart carry it. [verified: hooks doc, as relayed by the staff-backend-engineer round-1 review] Six registered hooks emit `additionalContext` on an event with no such channel: `nudge-error-mode-analysis.sh:182`, `nudge-worktree-anchor.sh:181` and `nudge-unexpanded-skill-mention.sh:212` on UserPromptSubmit, `announce-branch-review-history.sh:38` on SubagentStart, and `require-worktree-for-git-writes.sh:458` and `require-worktree-for-file-writes.sh:165` on PreToolUse, whose explicit allow carries it through `_lib_emit_allow_with_context` (`_lib.sh:461-468`). [verified]
48. `require-plan-review.sh` blocks Write and Edit only while a plan file is uncommitted or modified and no review covers its exact state (`:4-15`, `:26-32`). [verified]
49. This repo's own docs record as unverified whether a repo's checked-in `env` block can set `PATH`, `HOME`, `CLAUDE_CONFIG_DIR` or `CLAUDE_CODE_ENTRYPOINT` for a hook before the trust decision (`docs/hooks.md:244`). [verified: that line] O15's canaries observe the wider question of whether a checked-in `env` reaches a Claude Code hook at all.
50. `_lib_valid_session_id_component` accepts one or more characters from `[-A-Za-z0-9_]` (`_lib.sh:916`, `:2852-2854`), and `claude/.claude/scripts/transcript_analysis/author_outcome.py:286` mirrors it in Python. [verified]
51. An explicit hook allow may bypass Cursor's own approval prompt for the call. [unverified: raised by the ciso R3 and staff-backend-engineer R3 round-1 reviews from the preToolUse schema] No cell tests it, because requirement 2 bars an adapter allow whatever the answer, and the probe binds its own allows to one cell's exact command (M22).
52. Cursor's approval setting can make every Shell command ask, so a suppressed prompt is observable. [verified: the Run Modes page, as relayed by the staff-backend-engineer round-2 review: Auto-review is the default since 3.6, "Ask Every Time" was deprecated in 3.5, the page says "Use Allowlist with an empty allowlist for the same behavior", and sandboxing is "Optional, for shell commands"] If the run's Cursor version behaves otherwise, S1's and O20's prompt comparisons are recorded as not observable.
53. In the toggle-on scenarios, the live harness's `~/.claude/settings.json` hooks also run through the import, unguarded and reading bare `~/.claude`. [unverified: inferred from G6 and rows 29 and 31, as the staff-backend-engineer round-1 review notes] O28 observes it.
54. Phases 2 to 6 are planned after the spike, from its results, through a fresh `/plan-review`. [engineer-verified: "Phase 1 now, re-plan rest (Recommended)"] The label answered whether the plan should commit to Phase 1 alone and re-plan the rest after the spike. It settles the tension between row 1 and rows 43 and 44.
55. Every parity exception but row 37's is decided after the spike. [engineer-verified: "Decide after spike (Recommended)"] The label answered how to handle the gates the plan had treated as settled exceptions when only the ExitPlanMode arm had sign-off.
56. A consuming repo's hooks run, and the harness's deny wins against them. [engineer-verified: "Run them; harness deny wins (Recommended)"] The label answered which promise row 34's quote meant, given that a repo's own allow or `.cursor/hooks.json` may override the harness's deny.
57. The probe pins a supported Python version. [engineer-verified: "I feel like we should pin a supported python version just like Claude config pinned a bash and git version. Makes it easier, less code"]
58. That version is the repo's existing floor, `python3` 3.11 or later (`README.md:111`, enforced at `install.sh:83`), with no 3.9 path. `probe.sh` checks it before it execs, and below it writes a below-floor record and exits 0. That mirrors `_config.sh:40`'s floor check but fails quiet on purpose, because a probe's non-zero exit would read as an O17 or O19 result. The harness's own gates keep failing loud below their floor (row 38, requirement 3). [unverified: the dispatching session's proposal under row 57, which the engineer has not explicitly confirmed] Adopted, because it reuses the floor the repo already enforces. The 3.11 grammar test went with the test suite (row 66), so the kit is written to the 3.11 grammar and the runtime check stays.
59. Engineer quotes in this plan write "[private project]" in place of the word the engineer used for those projects. [engineer-verified: "Replace ... with private project"] The brackets mark the substitution inside an otherwise verbatim quote, and the tag's excerpt elides that word for the same reason.
60. The option descriptions behind rows 54 to 56 are the dispatching session's wording, adopted as Phase exit, OD4 and OD6 state them. [unverified]
61. CC1 and CC2 run under the Claude account the engineer chose for them, which this plan names only as the Claude account CC1 and CC2 use. No name is given. [unverified: the session's wording]
    - With `CLAUDE_CONFIG_DIR` unset the config dir is bare `~/.claude`. [verified: `_config.sh:110-124`] That the Claude account CC1 and CC2 use runs that way is [unverified] until CC1's and CC2's account check confirms it.
    - That is the dir the import's live harness writes in toggle-on scenarios (row 53), so all of the run's Claude-side state lands there. [unverified: the dispatching session's reading] Step 2's launch-environment check and CC1's and CC2's account check test it.
62. Phase 1 ships as one PR carrying only this plan, with its observations section and the reviewed post-spike revision. [unverified: the dispatching session's default, narrowed to the plan alone by row 67's option description]
63. A scratch repo's `.cursor/hooks.json` loads only in a workspace that contains it, and may need a trust step there. [unverified: one run conversation created while Cursor's trust dialog was open never fired `sessionStart`, and tool-call hooks fired once trust was granted; the cause is untested] Each scenario's smoke cell shows whether it loads. If it also loads elsewhere, the scope rule still keeps every other workspace's calls unrecorded and inert.
64. Each live-gate trigger reaches its gate's deny branch under Claude Code, in the state `init` creates. [verified: each hook traced past its early exits]
    - `Bash`: any `git commit` form, `--dry-run` included, reaches the gate (`_lib.sh:2238`). With no in-progress state, an empty `git diff --cached` exits 0 (`require-code-review.sh:129-137`), so `scratch-b` keeps a staged placeholder. While no marker under bare `~/.claude/code-review-markers` holds that diff's hash, the gate denies (`:222-241`), and its deny text tells the agent to run `/code-review` and retry (`:233`).
    - `Edit|Write|MultiEdit`: the untracked `.claude/plans/probe.md` makes the active plan set non-empty (`require-plan-review.sh:176-183`). While no marker matches it (`:284`), a non-plan Write in the repo is denied (`:336-337`), and the deny text tells the agent to run `/plan-review` or `/plan-it` (`:339-341`).
    - `Read`: `.netrc` matches `_LIB_CREDENTIAL_PATH_REGEX` (`_lib.sh:3912`), and the gate denies (`deny-credential-file-reads.sh:80-82`). It exits 0 when the payload carries no `file_path` (`:42`), which the mirror's record of the same call shows.
    - `Agent|Task`: a prompt under 600 characters holding `report back immediately` or `do nothing` matches `NOOP_PHRASE_RE` (`deny-no-op-dispatch.sh:70`, `:108`), and the gate denies (`:127-129`) for the tool name `Agent` or `Task` (`:98-101`).
    - Every gate's deny reason starts `Blocked by <label> gate:` (`_lib.sh:426`). The labels are `code-review`, `plan-review`, `credential-file read` and `no-op-dispatch` (each hook's `DENY_GATE_LABEL`).
    - `require-worktree-for-git-writes.sh:225` and `require-worktree-for-file-writes.sh:135` exit 0 when worktree enforcement is off for the repo. An untracked `.claude/worktree-optout` turns it off whenever no `.claude/worktree-required` exists (`_lib.sh:2744-2766`), so the machine's `worktree_required` default changes no cell.
    - `require-routing-read` denies only while a plan-review active marker exists for the session (`require-routing-read.sh:61-63`), and the kit never creates one.

    Cursor's default ignore list holds `.env*`, `.git/` and the lockfiles but no `.netrc`, and the ignore list also takes every `.gitignore` entry. [verified: ignore-files page, as relayed by the staff-backend-engineer round-4 review from its 2026-10-26 fetch] The dispatch re-checks both against its own fetch.
65. The harness registers `onFailure: "block"` only on PreToolUse entries: all 34 sit within `settings.json:259-513`. [verified: grep of `settings.json`]
66. The Phase 1 kit runs as an uncommitted scratch harness, and only its findings are committed. [engineer-verified: "Uncommitted scratch kit (Recommended)"] The label answered whether the kit should be committed to this public repo, tested and shipped to every stow consumer, or run uncommitted like the sandbox and git-hooks spikes' harnesses, with only the findings committed.
67. The option description behind row 66, a kit outside the repo, deleted at the end, keeping containment rules but no shipped test suite, doc or stow surface, is the dispatching session's wording, adopted under row 66. [unverified]
68. This repo's earlier spikes kept their harnesses out of the repo and committed only findings. [verified: `.claude/plans/sandbox-spike.md:121`, "Phase 1 scratch harness (not committed; removed at teardown)", under a `mktemp -d` directory; `.claude/plans/git-pre-commit-hooks-spike.md:235`, whose `/tmp` probe files "remain session evidence and are not committed"]
69. The kit's code goes through `/code-review` before the run, although it is never committed. [engineer-verified: "yes it should go through code-review"] The quote answered whether the uncommitted kit should be reviewed before the run.
70. Every relaunch in the run uses one Cursor launch method. [engineer-verified: "The way I'll use it daily (Recommended)"] The label answered which launch method the run should use.
71. The Cursor runs use the local Cursor IDE the engineer chose, which this plan names only as the local Cursor IDE. No name is given. [unverified: the session's wording]
72. The run uses Cursor's and Claude Code's default models. [engineer-verified: "Default models"] The label answered which models the run's Cursor agent and `claude` legs use.
73. The dispatching session's proposals under rows 69 to 72 are adopted as Approach states them: `/code-review` by path before `init`, which `code-review/SKILL.md:11` allows by taking changed files "from context, git diff, or the conversation"; the launch method asked at step 2; the prerequisite checks made as step 2 states (row 84); and models named per row 89. [unverified]
74. Removed (row 103).
75. S8, O26 and their timing machinery are cut, which narrows row 44. [engineer-verified: "S8 + timing machinery"] It is one of four labels the engineer selected in a multi-select on what to cut.
76. That cut drops S8, O26, `--record timing`, `init`'s calibration, and S8's allowlist step and its revert, and OD5's budget comes from Phase 2's acceptance run. The option description also listed `--shell-leg`, on the session's mistaken premise that only OD5 used it, and row 87 keeps it. [unverified: the dispatching session's option description, adopted under row 75 except where row 87 overrides it]
77. S11 and O24 are cut. [engineer-verified: "S11 / O24 ignore-list test"] It is one of the same four labels.
78. The adapter's `Bash` deny gate then carries the 13 `Read` deny entries too, overriding the sketch file's requirement 4 clause on the ignore list. [unverified: the dispatching session's option description, adopted under row 77]
79. The Phase 2 to 6 sketch moves out of this plan until the post-spike re-plan. [engineer-verified: "Move Phase 2–6 sketch out"] It is one of the same four labels.
80. The sketch, its criteria and its requirements live verbatim in the gitignored `agent-reviews/cursor-adapter-phase2-sketch.md`, which the re-plan reads as input and whose surviving requirements it writes back into this plan before the PR opens, while the OD items and one-line mechanisms stay here. [unverified: the dispatching session's option description, adopted under row 79]
81. The kit's tests are simplified. [engineer-verified: "Simpler kit tests"] It is one of the same four labels.
82. `selfcheck` tests the scope function once over its inputs, plus one end-to-end out-of-scope call per behavior class that acts differently, and the `~/.cursor` writes drop the temp-file, planned-hash, rename and adopt protocol, so a crash leaves a file `teardown` names for the engineer to remove by hand. [unverified: the dispatching session's option description, adopted under row 81, with the behavior classes taken from the staff-platform-engineer round-5 review]
83. Removed (row 89).
84. Managed and plugin hook sources are out of scope and unobserved. [unverified: the dispatching session's option description]
    - A prompt comparison the run could not make reads "not compared in this run" (The kit).
    - Model names the run used appear as the picker shows them (row 89).
85. Before `init`, the engineer sets a stop point. [unverified: the session's wording]
86. The engineer picks a scenario boundary to stop at, and a stop mid-scenario marks that scenario not observable. The engineer may also end the run inside a scenario, which Verification 3 handles. [unverified: the dispatching session's option description, adopted under row 85]
87. `--shell-leg` stays. [engineer-verified: "Keep --shell-leg (Recommended)"] The label answered whether to keep `--shell-leg` after the session reported that row 75's option had listed it for dropping on a mistaken premise.
88. The reason it stays, that O4, O5 and O15 read agent Shell calls through it, and the option description behind row 87 are the dispatching session's wording and this plan's reading, adopted under row 87. [unverified]
89. A name of a model the run used may appear in the public plan, as the model picker, or Claude Code for its own default, shows it. [engineer-verified: "Allow vendor-published names"; "the model names are not supposed to be private"] The engineer read the Cursor names from the picker. The picker's offered list is not a result and stays out. Row 103's cut does not touch it.
90. Removed (row 103).
91. This plan names the Cursor install and the Claude account only neutrally, as the local Cursor IDE and the Claude account CC1 and CC2 use. [unverified: the session's wording]
92. Folded into row 91.
93. Commit messages and the PR body describe the plan change only, never run data or facts about the run's Cursor setup, and `deny-private-project-refs` checks them as usual, with no engineer read of each draft. [engineer-verified: "That is completely impractical"] [engineer-verified: "Use the simple rule (Recommended)"] The quote rejected that read, and the label chose the rule. The hook scans commit messages and PR bodies. [verified: `deny-private-project-refs.sh:4-6`]
94. The option description behind row 93 is the dispatching session's wording, adopted as Phase exit states the rule. [unverified]
95. The pre-`init` armed check of the guard lines was to be simplified, and row 103 then removed it with the guard. [engineer-verified: "Simplify (Recommended)"]
96. The option description behind row 95 is the dispatching session's wording, superseded by row 103. [unverified]
97. An inaccurate statement a review finds is corrected, not asked about. [engineer-verified: "if you're just asking whether you should correct something that's inaccurate you correct something that's inaccurate always."] The quote answered whether to correct an inaccurate Alternatives set aside bullet.
98. `teardown` removes only the kit's recorded `~/.cursor` files and prints the folders the engineer deletes by hand and the setting reverts, and a restart is the engineer deleting the two kit roots by hand while keeping the source directory. [engineer-verified: "Shrink teardown (Recommended)"]
99. The option description behind row 98 is the dispatching session's wording, adopted as Scoping's `teardown` bullet and Run order step 4 state it. [unverified]
100. The engineer does no other work in Cursor until the run ends, so the run carries no window rule. [engineer-verified: "You don't need to worry about the case where I'm working on something and then I am finishing up the scratch project. I'm not gonna do any real work until the scratch. Project is done"]
101. The scratch root is `~/cursor-adapter-scratch/`, a name that does not start with `.cursor`. [engineer-verified: "Rename scratch folder (Recommended)"]
102. The option description behind row 101, with that name as its example, is the dispatching session's wording. [unverified]
103. The redaction machinery is cut: the probe records only `vocabulary.json` env names, process basenames and enum values and counts every other one, so the raw records hold nothing private and the session may read them. [engineer-verified: "Cut it (Recommended)"] The cut drops the two redaction tiers, `review`, `leak-check`, the guard lines with their armed check and revert, Name review, and the salted pseudonyms.
104. The option description behind row 103, including unsalted hash pseudonyms and the removal of rows 74 and 90, is the dispatching session's wording. [unverified]
105. The per-cell and per-render nonce rewrites of the live-gate state are cut, and when an agent starts a review instead of stopping, the engineer stops it and reruns that cell. [engineer-verified: "Cut re-render (Recommended)"]
106. The option description behind row 105, which keeps the stop rule, the CC1 control and S7's `named-gate` reading, is the dispatching session's wording. [unverified]
107. The `~/.cursor` writes stay hash-recorded, with the refusal Scoping states and the symlink checks. [engineer-verified: "Keep it"] The label answered whether to keep that write safety.
108. The option description behind row 107 is the dispatching session's wording. [unverified]
109. `init --approval-pinned`, the `run-stopped` note and the pin checks stay. [engineer-verified: "Keep it"] The label answered whether to keep the `--approval-pinned` handling.
110. The option description behind row 109 is the dispatching session's wording. [unverified]
111. Payload keys and tool names are recorded as-is when they look like identifiers, because they come from Cursor's schema, and only env and process names stay restricted to `vocabulary.json`. [engineer-verified: "Record field/tool names (Recommended)"] Excluding MCP tool names, which can name a project, is the dispatching session's reading. [unverified]
112. After the IDE run left sub-questions unrun, the probe gains the Cursor CLI as a second launcher for them. [engineer-verified: "I guess let's just run those unrun scenarios - all of them. But I do think it's important to optimize by running as many as we can with Cursor CLI."]
     - That the CLI is a probe launcher only, never an adapter target, is the dispatching session's reading. [unverified]
     - That a CLI result never stands for the IDE without a desktop cell agreeing is the dispatching session's reading. [unverified]
     - The copy-and-`cmp` stand-in for the restart rule is the dispatching session's proposal, and the engineer chose the label "Record the exception (Recommended)" for recording it. The label's description is the session's wording.
113. Each harness gate that silently no-ops under Cursor gets a native twin. [engineer-verified: "Native twin per gate (Recommended)"] The label answered OD4's gates item, asked with the live-gate follow-up's evidence. The engineer had said earlier [engineer-verified: "I think we need the twins to be in place in order for Cursor to be considered fully compatible with the claude-config toolkit"].
114. The adapter must not regress the harness. [engineer-verified: "We can't incur a regression in claude-config tooling with Cursor"]
115. The remaining OD4 items follow a split: an item whose gap would lose enforcement gets a twin or a blocking alternative designed in this revision, and an item is a documented gap only where Cursor lacks the tool, the event or the channel. [engineer-verified: "Yes, use that split (Recommended)"] The label answered the dispatching session's proposal of that split, with an item sort.
116. The split's wording and the session's item sort are the dispatching session's proposal, and this revision's re-sort, which OD4 marks item by item, is its own. Generating a native entry for every registration, not only for the gates, is this revision's way of meeting rows 113 and 114. Reading row 114 to cover both Claude Code's own path and the harness's behavior under Cursor is this revision's. [unverified]
117. OD6's trigger is not met, and the silent no-op failure mode belongs to OD4's gates item. [engineer-verified: "Not triggered; silent no-op is OD4's (Recommended)"]
118. The reading behind row 117, that no tested source beat or erased a harness deny and that the imported user-level direction stays an open, disclosed gap, is the dispatching session's. [unverified]
119. Import-only fails A1, A2, A3, A6, A8 and A9, and holds A4. [verified: Phase 1 observations, the sections the candidate list in Phases 2 to 6 cites]
120. A native entry with `failClosed: true` blocks on a timeout, a missing command, a non-executable script and empty stdout, non-JSON stdout blocks either way, and `{}` never blocks. [verified: Phase 1 observations, "Hook execution contract" and "Decisions"]
121. A deny won across every tested pair of sources, and a Stop follow-up survived the other source's `neutral` in both directions. [verified: Phase 1 observations, O20.b and O20.c]
122. `_lib.sh` sources `_config.sh` as its first command (`_lib.sh:25-27`), `_config.sh` returns 1 below bash 5.2 before anything else runs (`_config.sh:26-68`), and `test_bash_floor.py:232-234` asserts only that first command. [verified] Code placed right after the source therefore runs only at or above the floor and needs no bash 3.2 parse (`.claude/rules/shell-floor.md`).
123. Thirteen gates exit 0 unless the tool name is `Bash`: `block-gh-pr-merge`, `check-claude-md-length`, `check-skill-length`, `deny-credential-bash-reads`, `deny-invisible-commit-content`, `deny-network-installs`, `deny-pii-in-commits`, `deny-private-project-refs`, `deny-repo-relocation`, `guard-settings-session-keys`, `require-code-review`, `require-ready-for-review` and `require-respond-pr`. `deny-credential-file-reads`, `deny-env-reads` and `deny-data-file-reads` act only on `Read`, and `require-routing-read` only on `Agent`. [verified: grep of the tool-name tests over `hooks/*.sh`, this revision]
124. The hooks read fields Cursor's payload lacks. `agent_type` and `agent_id` are read by the shared parser (`_lib.sh:526`) and keyed on by `deny-reviewer-tree-mutation.sh`, `enforce-marker-script-shape.sh` and `deny-plan-writer-off-plan-writes.sh`. Four hooks read `permission_mode`, and `advance-past-commit-stall.sh:97` also reads `prompt_id` and `last_assistant_message`. [verified: grep over `hooks/*.sh`, this revision; Phase 1 observations, O16]
125. `require-routing-read.sh` acts only on the tool name `Agent` (row 22), and `deny-no-op-dispatch.sh` acts on `Agent` or `Task` (row 64), so translating Cursor's Task to `Agent` reaches both. [verified]
126. No Cursor ancestor is unique to one conversation (O4). A sessionStart `env` value reaches no agent Shell call (O5). A detached child outlives its hook (O6). A preToolUse `updated_input` rewrite was honored under the CLI (O7). A window close requests `sessionEnd` without running its hooks (O28.b). [verified: Phase 1 observations]
127. Under Auto-review with the sandbox, the native hook's record landed outside the workspace while the agent Shell leg's record did not. [verified: Phase 1 observations, Auto-review] Under Auto-Review (with Sandbox) an agent Shell call's write outside the workspace failed with no prompt, under Allowlist with the sandbox off it landed, and the IDE offers no Auto-review mode without the sandbox. [verified: OD9 run, C3] So the sandbox confines agent Shell calls and not hooks. That Allowlist (with Sandbox) confines them the same way is [unverified]: no cell ran it (r12).
128. `beforeReadFile` carries `content`. [verified: Phase 1 observations, Payload] For a 108000-byte file `content` held the whole file, though Cursor's Read tool refused that file to the model. [verified: OD9 run, C4] That it holds the whole of a larger file is [unverified], though the hooks doc says it does (row 156; r12).
129. The `permissions.ask` entries are eight `Bash` scripts and one `Edit` pattern (`settings.json:37-45`). The deny entries are 17 `Bash` and 13 `Read` (`:48-77`), plus the tool names `EnterPlanMode` and `ScheduleWakeup` (`:78-79`), 32 in all. `ask-review-permissions` and `ask-new-dependency-disclosure` sit in the `Edit|Write|MultiEdit` group (`:390-435`). [verified]
130. A native stop follow-up ran 5 times with `loop_limit` unset and 2 times with `loop_limit: 2`. [verified: Phase 1 observations, Decisions]
131. A native postToolUse `additional_context` value reached the agent, a native sessionStart one reached it in 2 of 3 attempts, and an imported SessionStart one did not. [verified: Phase 1 observations, Decisions and O2.e]
132. The import hands a project-level Claude registration Cursor's payload untranslated. [verified: Phase 1 observations, O16, measured on the project-level mirror]
133. Cursor documents that user hooks run from `~/.cursor/` (row 28), and the plan-review gate exited at its "not in a git repo" check when its process started outside the repo. [verified: Phase 1 observations, the live-gate follow-up]
134. In a multi-root workspace, preToolUse and beforeShellExecution each fired twice for one call. [verified: Phase 1 observations, Payload]
135. Cursor inserts a `Co-authored-by` trailer into a commit command. [verified: Phase 1 observations, O28.a] Whether `deny-pii-in-commits` denies it is [unverified].
136. A Shell payload's `cwd` follows no `cd` an earlier call made, because it is the empty string on every Shell record. [verified: OD9 run, C1] C1 failed and C2 held, so Repo resolution pins each Shell call's directory through the rewrite, on C7's result (rows 137, 230, 244; r13).
137. A preToolUse `updated_input` rewrite is honored in the IDE: the rewritten Shell command ran and the original did not. [verified: OD9 run, C2; the CLI result agrees (O7)] OD7's option (a), Repo resolution's directory pin and OD10's option (b) rest on it (r12; r13). C2's probe sent the rewrite with an explicit allow, so whether an output with no `permission` applies is row 244's question.
138. `parent_agent_id` is present on a subagent's tool calls and absent from the main agent's, and on a child call it is the direct parent's conversation id. [verified: OD9 run, C5, over the kit's records] (r12)
139. `afterAgentResponse` fires before `stop` in the same turn, and both carry the same `generation_id`, which the commit-stall twin uses as `prompt_id`. [verified: OD9 run, C5, 15 of 15 IDE turns] (r12)
140. Cursor's transcript carries the context measure `nudge-handoff-near-context-cap` reads. [unverified] Its twin is withheld until acceptance step 6 checks it.
141. preCompact fires in the IDE and carries the conversation id. [unverified: no Phase 1 cell could trigger it] Its twin is withheld until acceptance step 6 checks it.
142. subagentStart carries `subagent_id`, `subagent_type` and `parent_conversation_id`, the Task call's `tool_use_id` equals `subagent_id`, and subagentStop carries `status`, with `completed` and `error` seen. [verified: Phase 1 observations, Payload]
143. `deny-plan-writer-off-plan-writes.sh` denies a plan-writer agent every tool but `Edit` (`:99-101`). [verified] Cursor sends no `Edit` tool (Phase 1 observations, Payload), so a Write to an existing file must reach the hooks as `Edit` for a plan-writer to work under Cursor.
144. A Cursor Write's `tool_input` keys are `file_path` and `content` only, with no old and new strings, in the create cell and the edit cell alike. [verified: OD9 run, C5] That a Write editing an existing file carries the full new content the same way is [unverified]: the records do not show which cell made a true edit, and C1's one attempted edit errored inside Cursor's tool with no preToolUse record. Acceptance step 2's Write-as-edit trigger observes it (r12).

145. OD7 to OD10 are decided: conversation-id identity, document the repo-hook gap, run OD9's checks after this review, and accept and disclose the residual credential exposure. [engineer-verified: "Conversation id (Recommended)"; "Document it (Recommended)"; "Run them, after plan-review (Recommended)"; "Accept and disclose (Recommended)"] Each label answered the session's question of that name. The session's option descriptions are its wording.
146. OD4's re-sorts are accepted. [engineer-verified: "Accept the re-sorts (Recommended)"] The label answered the session's question listing the differences from its proposed sort.
147. Generating a native entry for every registration (row 116), and editing `_lib.sh` and `marker-clear-stale.py`, stay in scope. [engineer-verified: "Keep both (Recommended)"] The label answered plan-review's scope question. The session's option descriptions are its wording.
148. The `_lib.sh` guard is deleted, and the import's copies run beside the shim's entries. [engineer-verified: "Yes, delete it and revise (Recommended)"] The label answered the session's question whether to delete the guard and let the import's copies run beside the twins, with Asks-based self-protection and the consult's other changes. The engineer's typed words on the question before it were [engineer-verified: "I think deleting the guard makes sense. If the only con is really the duplicate deny text that seems merely cosmetic. What does the architect think?"]
149. The option description behind row 148 is the dispatching session's wording, and the consult's design and reasons, which this revision adopts, are plan-architect's. [unverified]
     - The revisions' own additions: an import copy's identity taken from its own payload's `conversation_id`, the gate matrix's write check, and the partial install from Phase 3 on.
     - The cost is more than a repeated deny text. The copies also run on every call (OD5), and a copy that reached a marker or identity check could decide on state its twin does not share, which Import copies closes or tests.
150. Repo resolution resolves a directory and never denies for "no repo". [engineer-verified: "Apply it (Recommended)"] The label answered the session's question whether to apply the consult's repo-resolution rule. Its deny for a Shell call whose directory cannot be known is one of two resolution denies, and requirement 39's file-tool deny, for a target that is not absolute or cannot be resolved, is the second (row 223). [unverified] Repo resolution and requirement 36 now list the resolution denies, and requirement 36 is the one home of that list (r21).
151. The rule's per-event detail as Repo resolution states it is the consult's and this revision's. So is the reading that it settles the staff-backend-engineer review's conflict with row 114, where a deny outside every root would block writes Claude Code allows. [unverified]
152. The hooks doc, paraphrased, places the system-level managed hooks file at `/Library/Application Support/Cursor/hooks.json` on macOS, describes it as applying system-wide, and runs its hooks from the managed config directory. It also says that for fields such as `followup_message` "a lower-priority source overrides a higher-priority one". [verified: the saved copy of cursor.com/docs/hooks this revision read, `:631`, `:641-642`, `:661`, `:798`] The adapter does not rely on that file's owner or mode.
153. The hooks doc's preToolUse matcher list reads "Values include `Shell`, `Read`, `Write`, `Grep`, `Delete`, `Task`, and MCP tools using the `MCP:<tool_name>` format", and the doc does not call it closed. Neither that list nor the observations' tool names hold an `EnterPlanMode` or `ScheduleWakeup` tool. [verified: saved copy, `:753`; Phase 1 observations, Payload]
154. Exit code 2 from a command hook blocks the action, "equivalent to returning `permission: "deny"`". [verified: saved copy, `:200`, `:1496`]
155. An imported copy of `require-worktree-for-file-writes` or `require-worktree-for-git-writes` can reach its explicit allow under Cursor, which Cursor would merge with the shim's `{}` as an allow. [unverified] Whether that allow skips Cursor's prompt is row 51's open question. Claude Code's own run of the same hook makes the same allow, so it is not an adapter output (requirement 2).
156. The hooks doc types postToolUse's `tool_output` as "JSON-stringified result payload from the tool (not raw terminal text)" and beforeReadFile's `content` as "Full contents of the file". [verified: saved copy, `:929`, `:1188`] The records show `tool_output` as a string on every postToolUse, and `content` holding a 108000-byte file whole. [verified: OD9 run, C4 and C5] Whether that string holds JSON text is [unverified] (r12).
157. Claude Code's exit 2 blocks a PreToolUse call with stderr to the model, blocks a UserPromptSubmit prompt, shows a PostToolUse hook's stderr to the model, and blocks a Stop. [unverified: not checked against Claude Code's hook docs this revision] Translation step 5's exit-2 mapping rests on it.
158. The hooks doc's common input schema lists `cursor_version` and `workspace_roots` for all hooks, and sessionStart's `session_id` is "same as `conversation_id`". [verified: saved copy, `:826-841`, `:1361`] The observations record `workspace_roots` on preToolUse and postToolUse. [verified: Phase 1 observations, Payload] Every Cursor record carries both keys, sessionStart, subagentStart and sessionEnd included, and no Claude Code record carries either. [verified: OD9 run, C5] (r12)
159. `_lib_resolve_claude_pid` walks from `$PPID` and returns the first ancestor with a live, start-time-validated `sessions/<pid>` entry, or returns 2 (`_lib.sh:3075-3087`, `:3105-3117`). [verified: grep, this revision]
     - Its callers are `marker.sh:140`, `review-ledger.sh:187`, `_review-pr-lib.sh:764`, `handoff-record-conversion.sh:13`, `_lib_resolve_session_id` (`_lib.sh:3130`, called by `pr-diff-against-base.sh:126` and `:158`) and `_lib_worktree_collision_guard` (`:3463`).
     - `_lib_hook_claude_pid`'s only callers are `capture-session-id.sh:74` and `record-session-end.sh:78`.
160. `_lib_worktree_lock_is_mine` treats a lock recording the caller's own PID as the caller's (`_lib.sh:3291`), and `_lib_worktree_collision_guard` fails when it cannot resolve the caller's identity (`:3463-3466`), which its gate turns into a deny. [verified] Under a host PID several conversations share, the first rule alone would hand one conversation another's lock (row 10).
161. `marker-clear-stale.py` keeps an active marker whose PID is alive and whose mtime is within the 60-minute idle window (`:241-247`), and keeps review-pr artifacts while their provenance PID is alive (`:211-216`). [verified; line numbers re-read, r17] An entry recording a live `Cursor` host PID therefore survives a sweep as a Claude Code one does (r15).
162. `TestResolveClaudePid` lives in `claude/.claude/hooks/tests/test_lib_worktree_collision_guard.py:1911`, not in `tests/helpers.py`, where the staff-backend-engineer review placed it. `_run_resolve_claude_pid` (`:132-141`) and `_build_subprocess_env` (`helpers.py:194-216`) copy the parent environment, and `hooks/tests/conftest.py:461-470` clears `CLAUDE_PID` with an autouse fixture. [verified]
163. The stop input's `status` is `completed`, `aborted` or `error`, and subagentStop's `followup_message` is "Only consumed when `status` is `"completed"`". [verified: saved copy, `:1323`, `:1052`]
164. `sessions/` in bare `~/.claude` gained entries matching a Cursor ancestor during Phase 1 (O28.b), and `_lib_registered_live_sid` rejects an entry whose recorded start time differs from the live process's (`_lib.sh:3084-3085`). [verified] That a relaunch's new extension hosts never reuse an old host's PID within the same second, the residual that `lstart`'s resolution leaves (`_lib.sh:3072-3074`), is [unverified].
165. A Cursor hook's or Shell call's nearest `Cursor Helper (Plugin)` ancestor is its window's extension host, and once the two session hooks decline, no Cursor process is a registered live ancestor. [unverified] O4 records that ancestry, but not that a hook's `$PPID` is the host itself, which is why the adapter walks for the basename rather than reading `$PPID`. A window has more than one such host: a hook's nearest host changes between events in one conversation, across five variants, and it is the same process as its Shell leg's in all but one observed leg. [verified: OD9 run, C5] "Its window's extension host" therefore names no single process (OD11(d); r12). OD11(d) anchors the walk on the `Cursor` main process instead (row 255; r15).
166. When C1 and C2 both fail, the taint rule is this revision's choice under row 115's split, which allows a documented gap only where Cursor lacks the tool, the event or the channel. [unverified: the consult leaned to the taint rule and left the choice to the engineer]
167. `test_bash_floor.py` scans fixed regions and not a script's pre-source region (`:12-24`), and `test_no_other_shipped_file_defines_the_floor` (`:262`) fails any other shipped file that defines the floor. [verified] A shim that sources `_config.sh` defines no floor of its own.
168. Claude Code gives an agent with no `tools:` list every tool, and an agent name the harness does not list meets none of its agent-keyed gates. [unverified]

169. The hooks-log-derived O28.b bullets are checked by the clean scan, not by an engineer read. [engineer-verified: "Scan is enough (Recommended)"] The label answered the session's question on the security review's request for a read. The session's option descriptions are its wording.
170. The taint rule stays the plan's default when C1 and C2 both fail. [engineer-verified: "Keep the taint rule (Recommended)"] The label answered the session's question on row 166. The option description is the session's wording.
171. The attended install-and-check run per phase from Phase 3 on stays. [engineer-verified: "Keep it (Recommended)"] The label answered the session's question on Verification 6. The option description is the session's wording.
172. Phase 4's edits to `capture-session-id.sh`, `record-session-end.sh` and the two conftest files, the gate-matrix test and requirements 36 to 41 stay in scope. [engineer-verified: "Keep them (Recommended)"] The label answered plan-review's round-2 scope question. The option description is the session's wording.
173. This revision applies plan-review round 2's findings and goes back to `/plan-review`. [engineer-verified: "Yes, revise and re-review (Recommended)"] The label answered the session's question whether to dispatch this revision pass. The option description is the session's wording.
174. `test_lib.py` pins `_lib_parse_tool_input_or_deny`'s jq field list against a fixed map and checks its read-variable order (`test_lib.py:403`, `:438-445`), so a new field in that jq program fails it. `INPUT` is a global the parser sets (`_lib.sh:497`). [verified: read, this revision]
175. `_lib_emit_allow_with_context` (`_lib.sh:461-468`) is the only PreToolUse allow emitter in `hooks/*.sh`, its only callers are `require-worktree-for-file-writes.sh:165` and `require-worktree-for-git-writes.sh:458`, and `hooks/tests/test_lib_emit.py` tests it. [verified: grep, this revision]
176. `_lib_resolve_claude_pid`'s callers run it in a command substitution (`_lib.sh:3130`, `:3463`), so a variable it set would not reach them. `_lib_worktree_lock_is_mine` returns 0 on a PID match before it reads either session id (`:3291`). [verified: read, this revision]
177. Claude Code's hook processes carry `CLAUDECODE`, from a plain terminal and from Cursor's integrated terminal, and Cursor's hook processes carry none. [verified: Phase 1 observations, Payload] Cursor's agent Shell calls carry none either. [verified: OD9 run, C5] (r12) That a Claude Code process an IDE extension starts inside Cursor has a process named `claude` nearer than its `Cursor` ancestor, the host the walk matches since OD11(d), is [unverified] (r15), and requirement 14's corpus and requirement 38's tests pin the walk's answer for both shapes.
178. No hook reads `CLAUDECODE`. `CLAUDE_PID` is read only by `_lib_hook_claude_pid` (`_lib.sh:3048`), whose callers are the two session hooks (row 159), which get no shim entry. `bootstrap-project-plugins.sh` exits unless `CLAUDE_CODE_ENTRYPOINT` is `cli` (`:202`). [verified: grep over `hooks/*.sh`, this revision] The shim's removal of the three names therefore changes only bootstrap's twin, which it stops.
179. Whether a repo's `.claude/settings.json` `env` block reaches Cursor's native or imported hooks is [unverified]: O15(c) observed only Claude Code.
180. `select-tests.py`'s `docs/` row selects the hooks and skills tests (`:862`). `HOOKS_TESTS_MODULES_IMPORTED_BY_SCRIPTS_TESTS` (`:228`) and `SCRIPTS_TESTS_IMPORTING_HOOKS_TESTS_MODULES` (`:270`) carry the hooks-tree modules a scripts test imports. `claude/.claude/hooks/tests/_lib_sh_helpers.py` is a non-test helper module in that tree. [verified: read, this revision]
181. `_lib_realpath_m` tries `realpath -m`, then `grealpath -m`, and walks to the nearest existing ancestor only when neither returns a non-empty resolved path, whether the command is missing, rejects `-m`, is cap-killed or fails (`_lib.sh:177-188`). [verified: read, this revision]
182. A Cursor deny's `user_message` reaches the model, contrary to this row's earlier premise that it reaches the user only: asked to repeat what it was shown about a block, the model repeated the `user_message` text, and the `agent_message` text appeared nowhere on screen or in the reply. [verified: OD9 run, one observation] Whether Cursor drops `agent_message` or the model left it out is [unverified]. Requirement 3's rule, which puts one deny text in both fields, rests on this row, and acceptance step 14's trigger 4 observes it again (r12; r13).
183. Whether a preToolUse explicit allow from one source suppresses a same-call beforeShellExecution `ask` from another is [unverified]. Requirement 2's suppression of the copies' allow makes the design independent of it. Acceptance step 3's cell 5 records the answer from scratch entries. `TestCopyAllowSuppressed` and the gate matrix's raw and import arms check that no copy gives an allow (r6 ciso R3).
184. The per-call budget is 1 s at the 95th percentile for each of Shell, Read and Write calls. [engineer-verified: "1 s at p95 (Recommended)"] The label answered the session's OD5 question. The option descriptions, including the Nielsen anchor and the hook-body counts, are the session's and the architect's wording.
185. This revision applies plan-review round 3's findings and goes back to `/plan-review`. [engineer-verified: "Yes, revise and re-review (Recommended)"] The label answered the session's question whether to dispatch this revision pass. The option description is the session's wording.
186. `_lib_worktree_lock_is_mine` returns 0 on a PID match before it reads a session id or the config dir (`_lib.sh:3291`). `TestWorktreeLockIsMine` calls the four-argument form directly with the test process's own, unregistered PID and an empty registry, and expects 0 for a lock under another session id, an old-format lock and a relative config dir (`test_lib_worktree_collision_guard.py:1968`, `:1973`, `:2070`). [verified: read, this revision] A fifth argument the existing rows never pass therefore leaves them green.
187. `bootstrap-project-plugins.sh` launches the reconciler by the script-relative path `${0%/*}/../scripts/reconcile-project-plugins.sh`, in the background, only when that path is executable (`:107`, `:234-235`). [verified: read, this revision]
188. macOS's `BUFSIZ` is 1024 (the Command Line Tools SDK's `_stdio.h:203`). [verified: read, this revision] That bash's `printf` builtin emits a line no longer than that as one `write` to an append-mode descriptor is [unverified], and `TestShimLogConcurrency`'s lines at the cap check it.
189. Requirement 39 first took a bounded Shell matcher with tamper detection, a choice row 190 replaced. [engineer-verified: "Bounded matcher plus tamper detection (Recommended)"] The label answered the session's question on how requirement 39 should end.
190. Requirement 39 has no Shell ask. [engineer-verified: "Drop the Shell ask (Recommended)"] The label answered the session's next question on requirement 39. The engineer's typed words with it were [engineer-verified: "when we're doing shell matching to try to harden, we've encountered this problem already with hooks where you can't make that safe. You just end up with compounding defensive engineering. So what I'm wondering is, why are we adding more infrastructure there for Cursor?"]
191. The option descriptions behind rows 189 and 190 are the session's wording, and so is the reading this revision implements. Requirement 39 keeps the write-tool deny, requirement 3's Recovery-only `user_message` and the sessionStart tamper check, and names Cursor's run mode as the boundary. [unverified]
192. The harness guards its own settings files through the write tools. `settings.json`'s `permissions.ask` holds `Edit(//**/.claude/settings*.json)` (`:45`), and `ask-review-permissions.sh` runs on `Edit|Write|MultiEdit` (`:390-394`). [verified: read, this revision] That no hook guards those files against a Shell command is [unverified: no exhaustive audit].
193. Requirements 4 and 5 keep `gate-settings-permissions.sh`, with its limits stated. [engineer-verified: "Keep both, state limits (Recommended)"] The label answered the session's question whether row 190's reasoning reaches requirements 4 and 5.
194. The option description behind row 193 is the session's wording, and so is its reading. The reading this revision implements is that the gate's header lists the operators `_lib_split_fragments` does not split (`&`, `<(`, `>(`; `_lib.sh:2089`), and requirement 5 claims only the shapes the gate recognizes, with the run-mode precondition carrying the rest. [unverified]
195. The gate matrix adds a payload check and keeps whole-hook effect equality. [engineer-verified: "Add payload check, keep effects (Recommended)"] The label answered the session's question on the staff-sdet review's finding that effect equality is the heaviest layer.
196. The option description behind row 195 is the session's wording, and so is its reading as requirement 40's payload and envelope checks. [unverified]
197. `~/.cursor/permissions.json` does not track the IDE's run mode: it was absent under each of the four modes, checked after a prompt in each and after a full quit and relaunch in one. [verified: OD9 run] That answers acceptance step 10's cell 6 "no" before it runs. The run-modes page documents the file as a user-written Auto-review policy whose schema is `autoRun` holding `allow_instructions` and `block_instructions`, lists of plain-English instructions, and says neither file the page describes is needed to start. [verified: cursor.com/docs/agent/security/run-modes, as relayed by the dispatching session] That schema holds neither `terminalAllowlist` nor the `autoRun` value set that G7's relay of the permissions page names. Whether Cursor stores the mode anywhere else is [unverified]: nobody searched. So requirement 5's check reads no file and prints the unreadable line on every result, and the search is an optional add-on (OD11(a); r9 ciso R5; r12; r13).
198. Whether the IDE offers a manual compaction command is [unverified]. Acceptance step 6 records which route caused the compaction, and row 141 carries whether preCompact then fires with the conversation id.
199. This revision's own choices among the round's options, each the lighter fix [unverified]:
     - the allow emitter takes the session hooks' host walk, rather than a standing check on Claude Code fixtures, because a fixture check pins today's payloads and cannot see a later Claude Code release;
     - the protected-path comparison's handling of spelling variants, since replaced by file identity (row 216);
     - a `schema-drift` line reads "no" in acceptance step 6, because the hook then found a usage block it could not use;
     - the rotator's lock is non-blocking, so a stuck rotator never holds a sessionStart run until Cursor's timeout;
     - the five-argument mirror imports the unedited module's constants rather than adding a helper module, because a helper both files import would edit a file requirement 14 holds unedited.
200. A context-cap twin whose acceptance check stays undecided twice stays withheld, and the doc discloses its channel as unobserved. [engineer-verified: "Stay withheld, disclose (Recommended)"] The label answered the session's question on the staff-platform-engineer review's finding that a second undecided reached row 115's documented-gap bucket.
201. The option description behind row 200 is the session's wording, and so is its reading. The reading this revision implements [unverified]:
     - the entry keeps its `withheld-premise` reason;
     - no reason value is added;
     - row 115's split stays intact;
     - a "no" writes `unsupported`, whose definition now names the channel as row 115 does.
202. The write-tool deny covers a workspace's own Cursor hook and MCP files. [engineer-verified: "Cover them by path (Recommended)"] The label answered the session's question on the ciso review's finding that a project `.cursor/hooks.json` sits outside the protected set.
203. The option description behind row 202 is the session's wording, and so is its reading. The reading this revision implements is that `.cursor/hooks.json` and `.cursor/mcp.json` are protected at any depth by a path test on the write tools only, with no Shell matching. Its tests are a row per name in a workspace root and in a subdirectory. [unverified]
204. [engineer-verified: "Don't fight the sdlc. Do what the process says"] The engineer typed this on what happens after round 6's findings.
205. The session reads row 204 as a further `/plan-review` round after this revision, with the plan-review marker written only after it. [unverified]
206. Read, Write-as-create, Delete and Grep targets arrived absolute on every observed event, though the agent was prompted with relative paths. [verified: OD9 run, C1] A Write-as-edit target is unobserved, and no relative target occurred, so no base was observed. Requirement 39 keeps its deny for every target not absolute as sent. That the deny then never fires for Read, Write, Delete or Grep in practice is [unverified: the dispatching session's reading] (r12).
207. Claude Code's stdin kind for a hook process, a FIFO, a socket or a regular file, is [unverified]. Requirement 40's envelope check records each run's kind rather than requiring them equal, and acceptance step 1's control records Claude Code's kind once (r9 sdet R8).
208. Round 6's declines and narrowings, each the session's and this revision's choice [unverified]:
     - the ciso review's accepted-baseline file and command for non-owned entries are declined, because they would add another defensive layer on the tamper check. The report instead names labels, events and counts only, and the doc says an engineer-added hook shows in it;
     - the protected-path comparison's handling of spelling variants, since replaced by file identity (row 216);
     - `status`'s rotation-threshold prong stays, and the doc states that it can trip in one long healthy conversation, because the alternative test cannot tell that conversation from a stuck rotator;
     - the rotator's size check after the lock is reviewed, not tested, because pinning that order needs a test seam the plan does not add.
209. A compact twin whose acceptance check stays undecided twice goes back to the engineer. [engineer-verified: "Escalate to you (Recommended)"] The label answered the session's question on the staff-platform-engineer review's finding that trigger 2 imported row 200's outcome for a twin OD4 sorts as enforcement loss.
210. The option description behind row 209 is the session's wording, and so is its reading. The reading this revision implements [unverified]:
     - trigger 2 no longer takes row 200's outcome;
     - its 20-turn route reads a scratch file whole each turn;
     - it records whether Cursor shows a sign of summarizing.

     Whether that route reaches Cursor's compaction within 20 turns, and whether Cursor shows any such sign, are also unverified (r10 cd R10).
211. The tamper-detection stack stays as designed and takes round 7's fixes. [engineer-verified: "Keep the whole stack"] The label answered the session's question whether to trim that stack.
212. The option description behind row 211 is the session's wording, and so is its reading. The reading this revision implements is that the ownership grammar, the pre-image, the foreign-entry reports, the count-based `status` silent flag and the run-mode warning all stay. [unverified]
213. Settled by row 223: row 150 now names requirement 39's file-tool deny beside its Shell-call deny.
214. The set of workspace files Cursor loads and runs with no Shell call is taken from the hooks and MCP docs, and is not checked beyond `.cursor/hooks.json` and `.cursor/mcp.json`. Whether the import loads a project `.mcp.json` is [unverified].
215. Round 7's narrowings and choices, each the session's and this revision's [unverified]:
     - `status`'s silent flag counts first lines after the entry's last check line rather than totals over the span. A conversation with no sessionStart would leave a total permanently unbalanced, and chat-only conversations' extra check lines would mask a stop. One missed check among healthy runs is therefore not flagged, and the flag detects an entry that has stopped;
     - the context-cap nudge travels by Deferred context, not `followup_message`, because a follow-up submits a user turn;
     - the protected-path comparison's handling of spelling variants, since replaced by file identity (row 216);
     - the 200-character bound on a printed foreign entry and the first-call mark's 7-day sweep are the plan's own choices;
     - the cap-kill rows drive the gate script directly, because a run through the shim meets the entry's 10-second `timeout` first.
216. For protected paths that exist, requirement 39 compares file identity instead of spellings. [engineer-verified: "Use file identity (Recommended)"] The label answered the session's question on the ciso review's finding that macOS spellings of one file survive the resolve-and-fold comparison.
217. The option description behind row 216 is the session's wording, and so is its reading [unverified]. The reading this revision implements:
     - identity, by bash's `[ a -ef b ]`, replaces the case fold, the resolve-fold-compare order, the locale-varying test row and the premise that every protected name is ASCII;
     - a part that does not exist yet takes its nearest existing ancestor's identity and the exact name for the rest;
     - the workspace pair keeps its any-depth match, with identity applied on the as-sent and resolved legs;
     - the tests use symlink and hard-link aliases, and the `/System/Volumes/Data` spelling on macOS only.
218. This revision goes back to `/plan-review` before anything else. [engineer-verified: "Run round 9 first"] The label answered the session's question on the next step.
219. That `[ a -ef b ]` is a builtin test in the repo's bash floor that compares device and inode, and that it reports a `/System/Volumes/Data` spelling and its usual spelling as one file, are [unverified]. The ciso review saw `test -e` accept that prefix and Unicode-folded names on this machine's volume, and the staff-sdet review saw `-ef` report case and Unicode variants as one file on one APFS volume. On a case-sensitive volume, such as Linux CI's, `-ef` still compares device and inode, but no spelling aliases exist to test, so those rows run on macOS only.
220. Whether Cursor's Write tool passes a `/System/Volumes/Data` or Unicode-folded path through unchanged is [unverified], as the ciso review states.
221. `.vscode/tasks.json` and `.vscode/settings.json` are candidates for workspace files Cursor loads and acts on with no Shell call, because Cursor is a VS Code fork. Whether the run's Cursor build runs them without a prompt is [unverified], and requirement 39 does not protect them.
222. Round 8's narrowings and choices, each the session's and this revision's [unverified]:
     - the sessionStart entry creates the conversation's first-call mark, so a healthy start writes no shim first line in any interleaving. The swept-mark case stays a named known trip rather than gaining a refresh mechanism;
     - the context-cap twin runs its hook from the `stop` entry, rather than at afterAgentResponse with the spent nudge disclosed, so an aborted or continued turn never spends it;
     - the print bounds each value to 200 characters, shows every byte outside printable ASCII as an escape, and prints at most 20 entries. Both numbers are the plan's own, and row 226 records the per-field form round 9 gave the bound;
     - the subagent root walk stops after 8 hops, the plan's own bound. OD9's attribution follow-up retires the walk (r21);
     - `install`'s step 4 re-reads `hooks.json` before the rename and aborts on a difference, rather than accepting the lost-edit window;
     - `uninstall` refuses on an unparseable `hooks.json`, rather than continuing and deleting the only record of the routed config dir.
223. Row 150 keeps its quoted label, and its sentence on Repo resolution's denies names requirement 39's file-tool deny as the second one. [engineer-verified: "Add the exception (Recommended)"] The label answered the session's question on row 213. Repo resolution and requirement 36 now list the resolution denies, and requirement 36 is the one home of that list (r21).
224. Asked by the engineer what the process prescribes after round 9, the session answered: approve with changes, apply them, then write the plan-review marker, with no further round, because plan prose alone never adds one. That answer is the session's. [unverified]
225. Cursor resets `loop_count` to 0 at each user prompt: across five follow-ups the stops read 0 to 4, then 0 on the first stop after a second prompt. [verified: OD9 run; the prompt boundary rests on the engineer's timing, because the records hold no beforeSubmitPrompt entry for that cell] The hooks doc says the count "starts at 0" and counts a conversation's automatic follow-ups. The context-cap twin's `stop` gate and the commit-stall twin's rest on it. The context-cap twin stays withheld on row 140 alone (r12).
226. Round 9's narrowings and choices, each the session's and this revision's [unverified]:
     - `install` creates the state dir, rather than leaving it unprotected until the shim first writes there;
     - a Delete of a workspace's `.cursor` directory is not denied, because it removes the project's own files and adds nothing that runs;
     - a not-yet-existing name is compared with ASCII case ignored, which adds denies only. Non-ASCII folds stay the residual and are pinned by an allow row;
     - hard links to a protected directory's members or to the workspace pair stay a residual. Only a Shell call can make one, and a link's identity cannot be traced to its directory;
     - the print bounds the event key and each field separately, counted after escaping, so the differing field shows whatever the home path's length;
     - the identity walk climbs at most 64 directories, reusing `_lib_realpath_m`'s bound;
     - the subagent edge store has no age sweep and lives under requirement 30's liveness rule. A missing edge for a payload-marked child denies. OD9's attribution follow-up retires the edge store (r21);
     - a "no route" on step 6 trigger 2 goes to the engineer at once, beside row 209's escalation after a second undecided, and a manual-route "no" with preCompact unfired counts as undecided.
227. Setting aside an adapter-written `~/.cursor/permissions.json` that turns `permissions.ask` into Auto-review `block_instructions` was the dispatching session's recommendation. [unverified] Row 240 records the engineer's decision (r12; r13).
228. This revision's readings of OD9's results, each [unverified] (r12):
     - requirement 38's predicate set is `workspace_roots` and `cursor_version`, because OD9 defines it as the keys on every Cursor event and workspaceOpen lacks the other two. `_lib_payload_is_untranslated_cursor` matches any key of the set, so adding `conversation_id` and `generation_id` would change no answer on the observed records;
     - C5's host result selects OD9's differing-host branch with no single variant, because the one mismatch pairs `agent-exec` with `retrieval` and the hook side's variant changes between events;
     - subagentStart cannot write a child-to-parent edge, because its ids are all the parent's, and a child's `conversation_id` first appears on the child's own calls and on subagentStop. The mapping now takes each edge from a child call's own `parent_agent_id` (OD9's C5 fallback; r13). OD9's attribution follow-up retires the edges (r21);
     - a multi-root Shell call's empty `cwd` took the unknown state, under Repo resolution's rule for a base root C1 could not show. The directory pin removed that state (r13);
     - under Auto-Review (with Sandbox), `marker.sh` run from an agent Shell call cannot write the routed config dir, because C3's write outside the workspace failed there.
229. `marker.sh` names a session's active-bypass file by session id and writes the resolved PID into it (`marker.sh:879`). A reader tests that PID with `kill -0` (`_lib.sh:2913`). The collision guard tests a lock holder's PID the same way (`:3525`) and reclaims a dead holder's lock through `_lib_worktree_reclaim_dead_lock` (`:3253-3268`). `session-keyed` skips `_lib_worktree_lock_is_mine`'s own-PID return (`:3291`). [verified: read, this revision] So on these paths the host PID an identity records under OD7(a) enters only liveness tests, not the paths' names. Whether a window's extension hosts share one lifetime is [unverified]: C8 bounded host lifetimes from the kit's records without measuring them (OD11(d); r12; r13; r14). OD11(d)'s `Cursor` anchor removes identity's dependence on that answer (row 255; r15).
230. Repo resolution pins each agent Shell call's directory. [engineer-verified: "Pin the directory (Recommended)"] The label answered the session's question on how Repo resolution gets the directory when the payload `cwd` is empty. The option description is the session's wording.
231. The reading behind row 230, which this revision implements, is the consult's and this revision's [unverified]:
     - the pin is an `updated_input` rewrite from one fail-closed preToolUse `Shell` entry, which makes each agent command start in the first workspace root, in `$HOME` with no root, or in its own leading absolute `cd`. Row 266 narrows the last to a `cd` into the first root's repository (r17), and row 286 to one into any workspace root's repository (r18);
     - the taint arm Phase 2 was to build, C1's trailer and the multi-root unknown state go, and the taint rule stays only as row 170's default;
     - the pin moves into Phase 3's first live entries, and Phase 4 adds its exports to the same rewrite;
     - the brace wrapper, the unwrap, the unanchored-matcher rule, the output with no `permission` field and C7 are this revision's additions, not the label's. Row 288 replaces the brace wrapper with a prefix line (r18).
232. OD11 is restructured. [engineer-verified: "Restructure OD11 (Recommended)"] The label answered the session's question of that name. The option description is the session's wording.
233. The reading behind row 232 [unverified]:
     - item (a) applies requirement 5's "no" branch, and the search for another mode source is an optional add-on;
     - item (c) leaves OD11 as requirement 3's rule;
     - items (b) and (d) stay the engineer's and gate Phase 4 only, with C6 run before (b) is asked and C8 before (d);
     - the depth-2 `Cursor` anchor is offered under (d), not applied;
     - under requirement 5's attested precondition C3's write landed, so (b) asks whether the adapter supports a sandboxed mode.
234. Phase 2 was to build the taint arm. [engineer-verified: "Phase 2 builds taint arm"] Superseded by row 230's "Pin the directory (Recommended)". It was one of four labels the engineer selected earlier in this round from the session's options.
235. The reading behind row 234 was that Repo resolution's Shell arm is the taint rule while C1 fails, C2 holds and C3 fails [unverified]. Row 231's reading replaces it.
236. OD11's items were to gate Phases 3 and 4. [engineer-verified: "OD11 gates Phases 3 and 4"] Superseded by row 232's "Restructure OD11 (Recommended)". It was the second of the four labels row 234 names.
237. The reading behind row 236 was that Phase 3 waited on OD11(a), and Phase 4 on OD11(b) and OD11(d) [unverified]. Row 233's reading replaces it, and Phase 3 waits on no OD11 item.
238. A shim deny's text goes in `user_message` too. [engineer-verified: "Deny text in user_message too"] It was the third of the four labels row 234 names. The option description is the session's wording.
239. The reading behind row 238 [unverified]: requirement 3's shim-authored deny carries one text, the hook name, the outcome class and any retry or ask-in-chat instruction, in both `agent_message` and `user_message`, and that text names no removal command.
240. The adapter-written `~/.cursor/permissions.json` stays set aside. [engineer-verified: "permissions.json write set aside"] It was the fourth of the four labels row 234 names, and it settles row 227.
241. The reading behind row 240 [unverified]: no phase writes `~/.cursor/permissions.json`, and Alternatives set aside keeps the entry with the engineer's decision.
242. [engineer-verified: "Can you have the architect adversarially review those choices?"] The engineer typed this with the four selections rows 234 to 240 record.
243. The consult that answered row 242, and the changes it recommended, are plan-architect's, relayed by the session in its own summary. Rows 230 and 232 are the engineer's answers to the session's questions on two of those changes [unverified].
244. C2's probe sent its rewrite as `{"permission": "allow", "updated_input": ...}` (the kit's `probe.py:779`). The hooks doc's preToolUse output example pairs `"permission": "allow" | "deny"` with `updated_input`, and its table marks `user_message`, `agent_message` and `updated_input` "(optional)" but not `permission`, which it describes as "`"allow"` to proceed, `"deny"` to block" (saved copy, `:883`, `:890-895`). [verified: read, r13] In the IDE, under Allowlist with an empty allowlist, Cursor applies `updated_input` from an output with no `permission`, and neither that output nor the allow-carrying one skipped the approval prompt. [verified: OD9 run, C7] The pin's output and requirement 2 rest on the first (r13; r14).
245. After a preToolUse rewrite, a sibling beforeShellExecution entry receives the rewritten command, and a sibling preToolUse entry the agent's, in the IDE and the CLI. [verified: OD9 run, C7] So a generated ask entry whose matcher is anchored at the command's start would miss a pin-prefixed command, which is why generated beforeShellExecution matchers are unanchored and the shim strips the pin prefix before its re-check. Which command a postToolUse entry receives is [unverified]: C7 did not record it, so the shim strips it on every Shell event (r19 cd R2). The `pin-check` entry now checks the beforeShellExecution form on every call, so acceptance step 16 no longer records it (r13; r14; r18, row 285).
246. Under the pin, a git write that lands in a main checkout under worktree enforcement meets `require-worktree-for-git-writes`'s deny, because the gate finds its repo from `cwd` or `$PWD` (the live-gate follow-up's audit), which the shim sets to the pinned directory. [unverified: read from that audit, not replayed under the pin] (r13)
247. This revision counts an allow-carrying rewrite that skips the approval prompt as C2 failing for the adapter, so row 170's taint default applies. [unverified] (r13) C7 saw no such skip, so this reading selects nothing (row 252; r14).
248. subagentStop's `child_conversation_id` adds no edge a child's own calls have not written, because every child tool call reaches the matcher-less `classify` entry, which writes the edge before attribution reads it, and a stop follows its child's calls. That a stop follows every call of its child is [unverified]: C5 does not order them (r13). OD9's attribution follow-up retires the edge store (r21).
249. Under OD7(b) the host PID is the identity, so a conversation whose hook-side host changes between events would resolve to a different identity at different events. Under OD7(a), on the paths row 229 read, the host PID enters only liveness tests. The first is this revision's reading [unverified] (r13).
250. Under Auto-Review (with Sandbox), an agent Shell call's writes to the temp dirs land, and a workspace-level `sandbox.json` entry under `additionalReadwritePaths` made a directory outside the workspace writable. [verified: OD9 run, C6; Cursor's sandbox reference] Row 260 records C6's user-level cell. Whether an agent can write either file or Cursor protects them, and whether an entry reaches a path inside the routed config dir, are [unverified]: C6 did not observe them (r13; r14; r15).
251. A `Cursor` main process is an ancestor of every IDE and desktop record, at no fixed depth, so a walk finds it by name, and every conversation but 2 stays under one such process. [verified: OD9 run, C8] O4 placed Cursor at depth 2, which C8 shows is not fixed. Why the 2 conversations span two, and how long a host lives, are [unverified]: the records bound host lifetimes without measuring them (r13; r14).
252. C7 selects the first of Repo resolution's C7 arms: the pin's output, `updated_input` with no `permission` field, applies and the call still prompts. So neither the Invariant exception nor row 247's taint reading arises, and Phase 2's OD9 gate is met. [verified: OD9 check results, C7, read against Repo resolution's arms] (r14)
253. This revision's readings for OD11(b), each [unverified] (r14):
     - C6 selects OD11(b)'s second result, temp dirs writable, and its first only in a workspace-level form row 2 bars;
     - the three options' costs, including option 2's merge under requirement 18, a workspace file overriding the user one, and option 3's race between parallel entries (O22.a), are readings, not runs;
     - option 1 is recommended, because requirement 5's precondition already leaves out Auto-Review (with Sandbox), the only sandboxed mode any cell ran;
     - the choice stays the engineer's.

     Superseded by row 258, where the engineer chose option 2 (r15).
254. This revision's readings for OD11(d), each [unverified] (r14):
     - C8's result moves the proposed anchor from depth 2 to an exact-name match;
     - a main process that exits takes its plugin helpers with it, so the anchor reads as dead no state the nearest-host rule keeps live;
     - the name-matched `Cursor` anchor is recommended, and the choice stays the engineer's, because it changes OD7(a)'s liveness rule (row 145).

     Row 255 records the engineer's choice of that anchor (r15).
255. OD11(d) is decided: identity's liveness anchors on the `Cursor` main process. [engineer-verified: "Anchor on Cursor main (Recommended)"] The label answered the session's question on OD11(d). The option description is the session's wording.
256. The reading behind row 255, this revision's [unverified] (r15):
     - `_lib_cursor_host_pid` matches the exact process name `Cursor`, and the session hooks' decline and the allow emitter, which share that walk, take the same match;
     - a live registered `claude` ancestor still wins first (requirement 31);
     - it changes OD7(a)'s liveness rule (row 145), as row 254 said, so a closed window's or archived conversation's worktree lock stays held until Cursor quits, which acceptance step 5 observes. Its active-bypass file is evicted after 60 minutes with no gating call (row 281; r17).
257. On OD11(b), before C6's user-level cell ran, the engineer typed: [engineer-verified: "Interesting. Requiring unsandboxed means only Allowlist. Thats a regression from Claude's auto mode, so we'd need to figure out how to not have the agent constantly asking for approval…"] It replied to the session's OD11(b) question, which r14's options framed.
258. OD11(b) is decided: `install` writes a user-level `sandbox.json`. [engineer-verified: "User-level sandbox.json (Recommended)"] The label answered the session's question on OD11(b), asked after C6's user-level cell ran. The option description is the session's wording.
259. The reading behind row 258, this revision's [unverified] (r15):
     - the file lists the routed config dir's directories that the agent-run scripts write. `install` merges it into the engineer's own file, and `uninstall` restores that file (requirement 18). No workspace-level file is used (row 2);
     - the session's option description named `marker.sh`'s directories. This revision also lists the other identity consumers' directories, `review-ledger.sh`'s among them (row 262), because their writes would otherwise fail under a sandboxed mode;
     - a consuming repo's workspace-level file can drop the entries, which fails closed;
     - the unobserved parts are acceptance step 10's cells 1 and 7 in Phase 4's run. A miss on cell 1, or a landed write to the user-level file on cell 7, falls back to the unsandboxed-mode precondition;
     - the file keeps Auto-Review (with Sandbox) usable only if OD11(e) admits that mode. Row 264 records that it does (r16).
260. Under Auto-Review (with Sandbox), with a user-level `sandbox.json` listing one directory outside the workspace, no workspace-level file and a new conversation, an agent Shell call's write into that directory landed, exit 0, with no prompt. [verified: OD9 check results, C6] Row 250 states what C6 left unobserved [unverified]. The listed directory was a kit one (r15; r17 cd R5).
261. Claude Code's auto mode gives `enforce-marker-script-shape.sh`'s Bash arms no prompt backstop. Its header says a session under `auto`, `bypassPermissions` or `dontAsk` "has no such backstop", and calls the gap "a live, unmitigated path to forging a `/code-review` or `/plan-review` completion marker" (`:131-135`). [verified: read, this revision] (r15)
262. `marker.sh` writes its markers and bypass files in directories directly under the routed config dir, each made by `mkdir -p` (`marker.sh:541-888`), and names the active-bypass directories in `ACTIVE_BYPASS_DIRS` (`:48`). `review-ledger.sh` builds its ledger paths under `review-narrative-ledger/` (`review-ledger.sh:314`). [verified: read, this revision] That the identity consumers write no other directory there is [unverified]: `TestSandboxWritableDirs` and acceptance step 10's cell 1 check it (r15). Row 270 shows it false for four targets (r17).
263. This revision's readings for OD11(e), each [unverified] (r15):
     - row 257's words motivate admitting Auto-Review (with Sandbox), but they do not accept requirement 5's residual under it, so the choice stays the engineer's;
     - admitting it matches Claude Code's auto mode on that residual (row 261), so option 1 is recommended;
     - under option 1 a workspace `.cursor/sandbox.json` is a way an agent could widen its own sandbox for its next conversation, which is why requirement 39's workspace names would gain it.

     Row 264 records the engineer's choice of option 1 (r16).
264. OD11(e) is decided: requirement 5's precondition admits Auto-Review (with Sandbox). [engineer-verified: "Admit Auto-Review (Recommended)"] The label answered the session's question, as the session relays it, on whether the adapter admits Auto-Review (with Sandbox), given the leftover gap for unrecognized `marker.sh` and `review-ledger.sh` command shapes and that Claude Code's auto mode has the same gap. The option description is the session's wording.
265. The reading behind row 264, this revision's [unverified] (r16):
     - from Phase 4 the precondition names Allowlist with the sandbox off and Auto-Review (with Sandbox) with the adapter's `sandbox.json` in force, and the doc discloses the unrecognized-shape gap as `enforce-marker-script-shape.sh:131-135` discloses it for Claude Code's `auto` mode;
     - requirement 39's workspace names gain `.cursor/sandbox.json`, and its doc statement names Auto-review's approval and its sandbox as that mode's layer;
     - Allowlist (with Sandbox) stays outside the precondition, and OD11(b)'s fallback takes the precondition back to Allowlist with the sandbox off alone;
     - two choices under row 258 stay this revision's and are not the engineer's: the directory list's widening past `marker.sh` (row 259), and cell 7's fallback covering only the user-level file, so that a workspace-level landing is disclosed instead. Row 272 replaces the second (r17).
266. A leading `cd` no longer chooses the gates' starting directory, except into the first root's repository (r17 ciso R1). [verified: read, this revision: `require-worktree-for-git-writes.sh:207`, `:219-222` and `require-code-review.sh:97-103`, `:134-136` take the payload `cwd` and exit 0 outside a repo or on an empty staged diff, before any relocation handling] The rule, that a leading `cd` pins its path only when the path is a literal absolute one with the first root's git common dir, and that the taint rule takes the same rule, is this revision's [unverified]. Row 230's label does not fix the directory source, so this keeps that label. The multi-root cost, that a gate resolving its repo once evaluates the first root, is this revision's reading [unverified]. Row 286 relaxes the rule to any workspace root's repository (r18).
267. The pin's wrapper is recognized only as this call's computed wrapper. The pin directory must be enterable before the rewrite returns, and a `pin-check` beforeShellExecution entry denies a command that is not the shim's wrapper (r17 ciso R2, be R6, be R8, sdet R6). That an agent's own `}` closes the braces is [verified: the ciso-reviewer's round-10 scratch run under bash 3.2 and 5.3]. That beforeShellExecution receives the rewritten command is row 245's [verified]. That a deny is the right response to a missing wrapper, rather than a log line alone, is this revision's choice [unverified]. Row 288 replaces the braces, so the `}` breakout and the enterability check go, and recognition becomes a prefix match (r18).
268. The doc and the cost list name every consumer of the wrapped command text: repo and engineer beforeShellExecution entries, Cursor's prompt, its allowlist and Auto-review's approval. `status` counts anchored non-owned entries, and step 16 records the prompt, the Shell result and an Auto-review prompt baseline (r17 be R5, ciso R5). [unverified: the effects are inferred from row 245, not observed] Row 284 removes the `status` count, and row 285 step 16's anchored-entry cell (r18).
269. A call's session id is the payload's `parent_agent_id` when present, otherwise its `conversation_id`, for the twin, the import copy and the Shell export alike (r17 be R1). [verified: C5, `parent_agent_id` equals the parent's conversation id on 19 of 19 child records and is absent from main-agent records] That a nested subagent then takes its direct parent's id, failing closed against the root's locks and markers, is this revision's reading [unverified]. It replaces root keying for identity and locks only. The active set and the first-call mark keep the root walk. OD9's attribution follow-up then keys the active set on the direct parent and retires the walk (r21).
270. The writable-directory list misses four targets a source scan cannot see: `.review-pr-active.d`, written by the `/review-pr` scripts through `_review-pr-lib.sh`; `code-review-diff-markers` and `cumulative-review-diff-markers`, from a variable (`pr-diff-against-base.sh:59`, `:69`, `:160`); and `.handoff-nudge.log`, a file at the config dir's root. [verified: `handoff-record-conversion.sh:20-22` read, this revision; the others as the staff-sdet and staff-backend-engineer round-10 reviews read them] `TestSandboxWritableDirs` becomes behavioral, and the log file is left out by name (r17 sdet R1, be R3).
271. The sandbox grant waits for Phase 5a's permissions gate, so Phase 4 does not open marker writes under Auto-Review (with Sandbox) before any adapter entry checks `marker.sh` shapes (r17 be R2). [verified: the backend review read `enforce-marker-script-shape.sh:513-514`, whose copy exits on any tool name but `Bash`] That the prerequisite rule of requirement 12 is the right carrier is this revision's choice [unverified].
272. Cell 7's two writes take one disposition: a landing of either file goes to the engineer with OD11(b)'s fallback as the default. The sessionStart entry reads a workspace file's paths, bounded, and counts those that widen the sandbox (r17 ciso R4, plat R7). This is this revision's choice, replacing row 265's second, and not the engineer's [unverified].
273. `sandbox.json` ownership is the manifest's record of added paths. The created form is the one key C6's file held, and the install and uninstall order puts the grant after `hooks.json` and removes it first (r17 plat R4, plat R6, sdet R4, sdet R7, be R4, ciso R6). [unverified: the form rests on the backend review's reading of the kit's file, and the order is this revision's choice]
274. Requirement 5's gap reads as any Shell write to a listed directory, with the active-bypass directories as whole-gate releases (r17 ciso R3). [verified: `_lib.sh:2869-2871`, `:2913-2914`, read this revision] Cell 1 gains the uninstalled control, the unlisted and prefix-sibling controls, a direct non-`marker.sh` write, and the failing step's record (r17 ciso R3, sdet R5, plat R5).
275. The host walk reads `ps -o comm=` and takes the whole line's part after its last `/`, never splitting on whitespace (r17 plat R1, sdet R2). [verified: the staff-platform-engineer and staff-sdet round-10 `ps` runs on this Mac: full paths, helper paths with spaces, and retitled helpers] Its fixtures copy those forms. The staff-sdet review saw a copied system binary killed at exec, and the staff-platform-engineer's round-11 run copied a non-system bash as `Cursor` under a spaced path, ran it, and parsed `Cursor` from it, so the macOS-only row builds that process (r19 plat R6).
276. The sessionStart entry reports the host walk's result and a `cursor_version` change, and requirement 22 becomes the one Cursor-update checklist with cells 1, 5, 6, 7 and 8 (r17 plat R2). [unverified: this revision's choice]
277. Under `session-keyed`, the live-holder deny names another Cursor conversation and tells the agent to ask the engineer. The Recovery section names `git worktree unlock` for the engineer, and `check` lists live-host locks (r17 plat R3, sdet R8). That a deny naming `unlock` would teach an agent to take a live conversation's lock is this revision's reason [unverified].
278. `TestDirectoryPin` lives in `test_cursor_adapter_shim.py`, which Phase 4 now lists for the rewrite's first-line rows, and step 16 joins Phase 4's run (r17 sdet R3, plat R9). [unverified: this revision's file choice]
279. Phase 4's revert names its files: `test_lib_cursor_identity.py` goes, only Phase 4's gate-matrix rows go, and the worktree gates return to `pending-phase` (r17 be R7). [unverified: this revision's choice] Row 298 makes the three-file restore a reverse-apply of Phase 4's own diff (r19).
280. Step 10's cell 8 runs a linked-worktree commit and a network call under Auto-Review (with Sandbox) (r17 plat R5). That a worktree commit writes outside the workspace root is the platform review's reading [unverified].
281. An active-bypass file is evicted after 60 minutes with no gating call, whatever its PID's state, so under OD11(d) only a worktree lock stays held until Cursor quits (r17 ciso). [verified: `_lib.sh:2913-2917` and `marker-clear-stale.py:241-247`, read this revision] Row 161's line numbers are corrected to `:241-247` and `:211-216`.
282. The Approach lead, the Installer's pre-image item, requirement 5's run-mode lead and OD11 (b), (d) and (e) are restructured, decision first (r17 cd R1, cd R2, cd R3, cd R4). So are step 10's cells 1 and 7 and step 16's pass line, and Repo resolution's C7 sentence is cut (r17 cd R6, cd R7, cd R9). [unverified: prose only, no fact changed beyond rows 266 to 281]
283. Until Phase 5b, a context value with no channel on its event is dropped and counted, and step 10's cell 6 is called a recording (r17 plat R10, sdet R10). [unverified: this revision's choice]
284. A consuming repo's or the engineer's own beforeShellExecution entry anchored at the command's start is not supported with the Cursor adapter, and the doc and the cost list say so. [engineer-verified: "Do I have a before shell execution hook in Claude config because if I don't, let's not guard against that we can cross that bridge when we get to it you just need to document it that that isn't gonna be supported with the cursor adapter"] The engineer typed this in reply to the session's question on the open part of the backend review's round-10 R5. The harness's own generated beforeShellExecution matchers are never start-anchored (the directory pin's matcher bullet). So the `status` count of such non-owned entries goes, and the plain disclosure stays (r18).
285. Step 16's cell 3, the anchored scratch entry, goes. The `pin-check` entry denies every call whose beforeShellExecution command lacks the shim's prefix, so a per-build recording of that form repeats it. Cells 4 and 5 become 3 and 4. That reading is this revision's [unverified] (r18).
286. The leading-`cd` exception admits a directory with the same git common dir as any workspace root, not only the first. [engineer-verified: "Any workspace root's repo (Recommended)"] The label answered the session's question on r17's multi-root cost. The option description is the session's wording. It relaxes the property row 266 stated. A clean repository that is itself a workspace root can again be the starting directory of `cd <clean root> && cd <repo> && git commit`, which ciso's round-10 R1 named. The reasons, the session's relay of the option and this revision's reading [unverified]: the engineer chooses the roots, and under Claude Code `require-code-review.sh` follows no `cd` from its directory either (`:97-103`) (r18). Row 292 narrows the rule to a root or a path inside one (r19).
287. OD11(e) stands with the wider gap requirement 5 now states. [engineer-verified: "Stands (Recommended)"] The label answered the session's question after the session explained that gap, ciso's round-10 R3. No text changes beyond OD11(e)'s note (r18).
288. The pin's rewrite is the prefix line `cd -- '<dir>' || exit 1`, after Phase 4's unset-and-export line, then the agent's command unchanged, in place of the brace wrapper. This is the session's choice, on a plan-architect consult's recommendation as the session relays it, and not the engineer's [unverified]. The consult judged the pin's foundation right and the brace grammar a local mistake. `pin-check` tests for the exact prefix, and recognition stays an equality with the prefix computed for the call (r18). Row 295 adds `builtin` to the `cd` and keeps this form (r19).
289. What the braces made necessary goes: the shim's enterability pre-check, the resolution deny's "cannot be entered" conditions, the `}` and `{` test row and the text-after-brace lookalike row. A directory the shell cannot enter meets `exit 1`, and `cd`'s own message names it. That this message serves the agent as well as the deny's remedy text did is this revision's reading [unverified] (r18). Requirement 3's unenterable-directory outcome brings a shim-side deny back for that directory on every fail-closed entry, Shell's included, as a failed `cd` rather than a pre-check. So `exit 1` stops only a directory that stops being enterable after the hooks ran (Repo resolution; r21).
290. `TestDirectoryPin` runs its prefix rows under the recorded bash, `/bin/sh` and `zsh` where present, and adds a last-line backslash row and a `shopt -s extglob` row. Step 16 gains cells 5 to 7: a nonexistent-directory rewrite, the multi-line commands, and the Shell's basename (the staff-platform-engineer's round-10 R8; r18). [unverified: which shell Cursor runs the command in, until cell 7 records it]
291. The compounding pass over r17 and r18 removed the layers rows 284, 285 and 289 name. It kept `pin-check`, the prefix-equality recognition, the leading-`cd` exception's literal-path conditions and the workspace `sandbox.json` report. Each of those answers a premise or an input of its own, not a gap another adapter layer opened. That judgment is this revision's [unverified] (r18).
292. The leading-`cd` exception admits only a workspace root or a directory inside one, by the file tools' containment test. [engineer-verified: "A root or a path inside one (Recommended)"] The label answered the session's question on ciso's round-11 R1, which showed the any-root-repository rule admitting the main checkout and every sibling worktree of a root's repository. The option description is the session's wording. It supersedes row 286's widening, whose label "Any workspace root's repo (Recommended)" stays quoted there as the earlier choice. The cost, a `cd` into a sibling worktree or the main checkout that is not itself a root being pinned back to the first root, where a gate that resolves once checks the first root's tree, is this revision's statement of it [unverified]. It matches a Claude Code session started in the first root (r19).
293. The containment test runs no `git` and no other program, so a repository the agent names cannot feed the shim its configuration, and a hostile-config fixture shows nothing runs (r19 ciso R4). [verified: Repo resolution's file-tool containment rule, which the exception now reuses]
294. Recognition, stripping and `pin-check` compute the expected pin prefix from the text after any pin-prefix-shaped first lines, with the payload, by the pin entry's rule. A Phase 4 shim treats a Phase 3 pin prefix as agent text. `TestDirectoryPin` gains a round-trip row per pin source and a literal-backtick row (r19 sdet R1, ciso R3). [unverified: this revision's choice. That no conversation meets both forms rests on the doc's quit-around-install rule]
295. The pin prefix's `cd` becomes `builtin cd`, so a `cd` function or alias in the engineer's startup files cannot fake a successful `cd`, and the `exit` stays plain so a shell with no `builtin` still exits (r19 sdet R2). [verified: the staff-sdet round-11 `BASH_ENV` run, where a `cd` function returning 0 let the body run in the caller's directory] The residuals, a function named `builtin` or `exit` and a zsh `chpwd` hook, are this revision's reading [unverified]. This keeps row 288's prefix-line form.
296. Step 16's cell 5 gains sentinels after a second line, a `}`, a `||` and inside a heredoc, a positive control naming an existing directory, and a following ordinary call. It runs before Phase 3 merges, because it needs no adapter (r19 ciso R2, sdet R6, plat R3). Cell 6 gains a heredoc, a trailing comment and a `||`. Cell 7 records the shell's argument vector and whether it sourced the engineer's startup files (r19 plat R3, sdet R2). "Behaves as unprefixed" means equal stdout bytes and exit status (r19 sdet R12). [verified: the ciso round-11 probe, where `exit` ran before any later line was parsed, under bash, `sh` and `zsh`] Cursor's own runner stays [unverified] until cell 5 runs.
297. An install about to add `sandbox.json` paths records them durably at step 1 as a pending record, which step 5's manifest promotes. `uninstall` and a re-run `install` both read it, and the pre-image is taken in the install that first writes the grant (r19 plat R1, sdet R4). [unverified: this revision's mechanism] The crash rows at the rename-to-manifest boundary now have an outcome.
298. Phase 4's revert reverse-applies Phase 4's own merge diff to `_lib.sh` and the two session hooks, and never restores a whole file, because unrelated PRs edit `_lib.sh` (r19 be R3). [verified: the backend review's `git log` count of later `_lib.sh` commits]
299. Phase 4's doc states that agent marker writes fail under Auto-Review (with Sandbox) until Phase 5a writes the grant, and names Allowlist with the sandbox off for marker-gated work until then. The pending row builds its own exceptions tree, and Phase 5a's `TestSandboxGrantWritten`, in a file 5a now lists, tests the real flip (r19 be R2, sdet R5). [unverified: this revision's choice]
300. Step 16 gains a pass condition that the prompt shows the agent's command in full (cell 3), an allowlist cell (cell 8) and a non-default-option cell (cell 9). The rewrite copies the payload's whole `tool_input` and changes only `command`, and a row checks the key set (r19 be R4, be R5, plat R4). [unverified: Cursor's merge-or-replace of `updated_input` is unobserved]
301. "Carries `parent_agent_id`" has one definition: absent, `null` or empty takes `conversation_id`, and any other value must pass the grammar or the call denies. A child Write without the field takes its own id and fails closed. Acceptance step 5 records which child tools carry it, and a differential row runs the three resolvers over the edge values (r19 be R1, sdet R7). [verified: `parent_agent_id` on child Shell and Read records only, as the backend review read C5 and the Payload section] The rule's outcome for Write is this revision's reading [unverified].
302. The sessionStart entry's workspace `sandbox.json` report moves last, after the mark, through `additional_context` only, under one 3-second deadline. It resolves each entry to a canonical absolute path and counts an unresolvable or non-absolute one as widening. The host-walk and version reports stay in step 3 and gain `TestSessionStartReports` (r19 plat R5, ciso R5, sdet R8). [unverified: this revision's choice. The 3 seconds is the plan's]
303. OD5's Phase 4 re-run and acceptance step 12 run gated calls in a linked worktree with worktree enforcement on, so the identity walk's cost is measured (r19 plat R2). [verified: the platform review's round-11 timing of about 25 ms per `ps -o comm=` call on this Mac]
304. `TestSandboxWritableDirs` drives every kind in `marker.sh`'s `WRITE_SKILLS` and `ACTIVE_BYPASS_SKILLS`, runs `pr-diff-against-base.sh` with both artifact flags, asserts each artifact exists, and makes root-level files read-only. Step 10's cell 8 states its layout and a positive confirmation, and names its network call in the doc. Cell 1's uninstalled run is called a baseline. `status`'s `pin-check` count moves to `TestStatusAndCanary` (r19 sdet R3, sdet R9, sdet R10, sdet R11). [verified: `pr-diff-against-base.sh:104-107` as the staff-sdet review read it]
305. The macOS-only host-walk row runs a copied non-system bash named `Cursor` under a spaced path, with the copy's PID as its oracle, and row 275's claim is narrowed to system binaries (r19 plat R6, sdet R12). [verified: the platform review's round-11 run]
306. The first-call mark keys on the call's session id, and the edge writer and the walk land in Phase 5a (r19 be R6). [unverified: this revision's choice] OD9's attribution follow-up retires the edge writer and the walk (r21).
307. The entry command's guard is the launch guard, and the directory pin's text is the pin prefix, which the shim adds and strips, so "prefix" and "wrapper" no longer name two things. Repo resolution's exception, the multi-root cost, requirement 36's rows, the resolution-deny paragraph, the taint rule, the `sandbox.json` directory item, step 10's cell 1 and two dangling sentences are restructured (r19 cd R1 to cd R11). [unverified: prose only, no fact changed beyond rows 292 to 306]
308. Removed.
309. A call that carries no `parent_agent_id`, and whose conversation has no stored edge, is the main agent's only when that conversation has a root record, which each shim entry on a beforeSubmitPrompt call creates. Otherwise a fail-closed entry checks it as an unattributed subagent. So does a call from a conversation with a stored edge while the set is empty, the same shape's sibling arm, which the earlier text read as the main agent's. Requirement 8 gains unmarked Write and Delete rows over an empty active set with their allow side, and its case-3 deny names the action that clears it. Requirement 35 gains the record's create (r20 ciso R1; r21 be R8, sdet R4). [verified: C5 saw `parent_agent_id` on child Shell and Read records only, OD9 check results] That beforeSubmitPrompt fires with the main conversation's id before its first tool call, and never with a child's, is this revision's reading [unverified]. Acceptance step 8's control, where the main agent's calls pass, would show a missing root record. A check of C5's records before Phase 5a tests the premise, and the engineer chooses the outcome if a child's id appears. The creator skips a payload carrying `parent_agent_id`. The deny rows run with the parent's root record and an unrelated one present, and step 8 lists `subagent-roots/` after a subagent run (r20). OD9's attribution follow-up saw `parent_agent_id` on every child call class it ran, and retires the root record, the pre-Phase-5a check and step 8's listing (r21).
310. Requirement 5's disclosure and requirement 39's residuals name a workspace `.cursor/sandbox.json` that exists before the conversation. Its one report is the sessionStart count through `additional_context`, and `check` cannot give it, because it runs in no workspace. The count reports any key other than `additionalReadwritePaths` as an `unrecognized key`, and treats as widening any listed path outside the workspace roots, `/tmp` and `$TMPDIR` (r20 ciso R2; r20; r21 be R14). [verified: C6's documented key list and its temp-dir writes, the 2 of 3 sessionStart deliveries in Decisions, and OD11(b)'s statement on `check`, read this revision] Requirement 39's residual says the count reaches the model only, which content steering the agent can suppress. Answering with a disclosure, not a new engineer-facing report or a gate, is this revision's reading of the finding's scope [unverified].
311. OD7 no longer cites a second-layer gate. Requirement 29 states that no phase builds one and that the downgrade has none (r20 ciso R3, sdet R1). [verified: no phase's file list, Twins item or adapter gate builds such a gate, read this revision] Requirement 29 gains two `test_lib_cursor_identity.py` rows: the payload's id over a differing variable, and, with no payload, the variable as the pinned residual. The second row is not called expected-different, because requirement 14 uses that term for its snapshot cases [unverified: this revision's wording].
312. `tool_input.cwd` is never a pin source, by Repo resolution's directory-input refusal, which also refuses a non-string value (r20 be R1; r20). [verified: C1 saw `tool_input.cwd` empty on every Shell record] That a directory the agent sets arrives under that key stays [unverified] until step 16's cell 10 records it. The refusal is this revision's choice over taking the input as a pin source under the exception's containment test [unverified]. That source would make `pin-check` and each beforeShellExecution entry recompute the pin from that event's own `cwd`, which no record shows for a non-empty input, and the leading-`cd` exception already lets the agent choose a directory inside a root.
313. A Cursor CLI session can run with the adapter installed, beside IDE conversations. [engineer-verified: "Why would a user have to uninstall the Cursor app to use Cursor CLI? Thats a non starter… engineers should be able to alternate between both if they so choose, and to have agents running on both simultaneously"] The engineer typed this in reply to the session's relay of this revision's first answer, which had the CLI deny and named `uninstall`. The plan reads it as no adapter deny a CLI session cannot clear, and no adapter state that CLI and IDE conversations share. That reading is the plan's gloss, not the engineer's words [unverified]. For how a call is classified, the engineer then selected a label [engineer-verified: "Per-call, narrow (Recommended)"]. The option description is the session's wording. What changes (r20 be R2; r20):
     - the Installer's launcher rule stands an identity-keyed entry down in a call whose own host walk confirms no host, and keeps no record;
     - `_lib_cursor_host_pid` has three outcomes, stops at the integrated terminal's host, and reads a `Cursor`-prefixed helper with no `Cursor` main as could not tell;
     - `status` and `check` flag a helper with no main;
     - the Recovery section reads the walk's report per launcher;
     - acceptance step 17 runs a CLI session alone, beside an IDE one, nested under an IDE agent and across `install`, in Phases 4 and 5a.

     [verified: the CLI follow-up's matching hook environment names, the kit-side launcher stamp, C8's IDE-and-desktop scope, O4's `Cursor Helper (Plugin)` above every IDE hook, CC2's ancestry through `Cursor Helper: terminal pty-host` (Payload), and `_lib_hook_claude_pid`'s use of the hook's own `$PPID` (`_lib.sh:3216-3226`), read this revision] These stay [unverified] until step 17 records them:
     - whether the CLI loads `~/.cursor/hooks.json`;
     - whether a CLI's walk confirms no host from a terminal outside Cursor and from the integrated terminal;
     - the form of a CLI's file-tool targets;
     - whether the CLI reads `~/.cursor/sandbox.json`.

     The accepted costs, this revision's statement of them [unverified]: a CLI call gets no identity-keyed enforcement, and a process an IDE agent starts detached confirms no host. A rename of the main process alone now denies rather than failing open. Row 317 replaces this row's selector and its integrated-terminal stop (r21). Row 322 withdraws the rule to the Phase 4 re-plan (r21).
314. Phase 3's install is for its acceptance run only, an exception to row 313's requirement for that window. [engineer-verified: "Accept as a disclosed exception (Recommended)"] The label answered the session's question on the Phase 3 window. The option description is the session's wording. The reason given, that until Phase 4 no Cursor-side path writes a marker or an active-bypass file, is [unverified] (O28.b), and the Phase 3 window bullet now states both outcomes: those gates deny, or they may release too. [engineer-verified: "Restate: may release too (Recommended)"] That label answered the session's question on the release outcome, and it covers that outcome (r21). Phase 3's `install` needs `--acceptance-only` and stamps `install.json`, and `status`, `check` and the sessionStart line say so until Phase 4's install, under a `TestRunModeWarning` row. A CLI session beside a Phase 3 install meets the same denies, and CLI coexistence starts in Phase 4. The 3-to-4 window is named beside the 4-to-5a one (r20 plat R1; r20). [verified: a grep of the marker and active-bypass reads in `require-code-review.sh`, `require-plan-review.sh`, `require-ready-for-review.sh`, `require-respond-pr.sh` and `require-memory-skill.sh`, this revision] The statement is this revision's choice over listing those gates `pending-phase` 4 [unverified]. Their Phase 2 gate-matrix rows are shim arms, which requirement 40's meta-test fails beside a `pending-phase` entry. Row 322 moves CLI coexistence to the Phase 4 re-plan (r21).
315. `TestDirectoryPin` sorts its candidate shells by a runtime `builtin` probe, not by platform. A shell with `builtin` runs the rows, and one without it, as dash, runs the fail-closed row on a valid directory. A test asserts that every skip's reason is an absent shell. The Phase 2 and Phase 4 PR bodies record a local macOS run, because CI runs the `builtin` rows under bash alone unless its image has `zsh` (r20 sdet R4; r20). [verified: `.github/workflows/tests.yml:25` runs on `ubuntu-24.04`, read this revision, and the staff-sdet review's macOS `/bin/dash` run, where the pin prefix exited 1 with `builtin: not found` before the command ran] That `/bin/sh` is dash on that runner is the staff-sdet review's statement [unverified]. Requirement 36 replaces the skip-reason assertion with a collection-time observer of the matrix, because the matrix is parametrized only over present shells, so no skip exists.
316. The launcher rule's premise was checked before Phase 4, as OD9's C9. [engineer-verified: "Premise check first, then re-plan (Recommended)"] The label answered the session's question on how to proceed with the launcher rule. The option description and the check's scope are the session's wording. The session ran the check, and its results are the session's observations, not the engineer's words. [verified: this session's run, 2026-11-05] The CLI loads a user-level hooks file, so the rule stays. Its hooks sit under `agent` with no `Cursor`-prefixed process above them, and its payload carries `cursor_version` in the IDE's form (r21). Row 322 withdraws the rule it kept, and C9 stays as the re-plan's input (r21).
317. The launcher rule stands a deny down only on the CLI-lineage token, positive evidence of a CLI, in place of the absence of a `Cursor` host (r21 ciso R1, be R1). What changes (r21):
     - `_lib_cursor_host_pid` is the one home of the walk's output contract. It prints `cli-agent` or `cli-cursor-agent`, `claude` or `top` on outcome 2, and `unreadable` or `helper-only` on outcome 3 (be R5). It takes the recorded `ps` from the launcher rule (ciso R2). Its compares are case-sensitive on the basename (be R7, plat R5). It no longer stops at the integrated terminal's helper;
     - the session hooks decline on outcomes 0 and 3 and on 2 with `top`, and write only on 2 with `cli-agent`, `cli-cursor-agent` or `claude` (be R4; r21);
     - the walk runs only on an identity-keyed entry's deny path (be R3, plat R1);
     - a deny stands down only on status 2 with the exact token, under a deadline (ciso R2, be R2, plat R2, sdet R2);
     - `install --no-cli-stand-down` turns the stand-down off, and a tripwire checks the flagged set against the scripts that read identity state (be R10). A pinned literal is the membership oracle (sdet R1);
     - the version report skips a call with the token, and a CLI that walks to a host can still report one change (be R12, plat R4; r21);
     - the costs name restricted-subagent limits (ciso R4), a `cursor-agent` worker (be R13, plat R3), and a CLI in the integrated terminal, which now takes the IDE path. Row 319 settles that case;
     - `TestLauncherRule`, `TestSessionStartReports`, requirement 28, OD5, requirement 15's `launcher` field and step 17 follow (sdet R3, R8, R10, R11; be R6).

     [verified: C9; O4's `Cursor Helper (Plugin)` above every IDE hook; `_lib.sh:3284`'s status-dropping `ps -o ppid=` pipeline, read this revision] Two cases dissolve, because neither has `agent` nearest with no `Cursor`-prefixed process above it: a foreground CLI an IDE agent starts under a `claude`-named process, and an IDE hook whose parent exits mid-call. The premise check also ran before Phase 4 (plat R7). These stay [unverified]:
     - that the adapter's entry command adds only shells between the hook and `agent`;
     - that an identity-keyed hook's deny path writes nothing when identity cannot resolve;
     - whether an IDE agent can place a process under the integrated terminal's helper, which decides whether that chain could be admitted as evidence;
     - whether a `cursor-agent` worker loads `~/.cursor/hooks.json`.

     Row 322 withdraws every change this row lists to the Phase 4 re-plan, and restores `_lib_cursor_host_pid`'s earlier contract and the session hooks' earlier decline (r21).
318. The two launcher-rule fixes before C9 took the engineer's labels: [engineer-verified: "Apply now (Recommended)"] for reading the child bash's result only from a positive token under a deadline, and [engineer-verified: "Disclose now, positive evidence in re-plan (Recommended)"] for the agent-chosen lineage cases. The option descriptions are the session's wording. Row 317 carries both into the re-plan (r21 ciso R2, ciso R3). Row 322 withdraws both with the rule (r21).
319. A CLI in Cursor's integrated terminal stays classed as IDE, so its identity-keyed gates stay on, and sessionStart becomes a second creation point for the root record. [engineer-verified: "Root record on sessionStart too (Recommended)"] The label answered the session's question on that case. The option description is the session's wording. What changes (r21):
     - the C5 mapping, requirement 8, requirement 35, step 8 and `TestAgentAttribution` add sessionStart beside beforeSubmitPrompt. Either event's payload carrying `parent_agent_id` creates no record;
     - the pre-Phase-5a check and step 8 examine sessionStart records for a child's id too. A child's id on sessionStart drops sessionStart as a creation point and returns the case to the engineer;
     - the launcher rule's integrated-terminal cost, its foreground-nested cost, the Recovery remedy and step 17's cells 2 and 4 follow, and the advice to run CLI work outside Cursor goes.

     [verified: C9 for sessionStart; the Phase 1 follow-up's `agent -p` cells for no beforeSubmitPrompt. C9 registered no beforeSubmitPrompt entry] These stay [unverified]:
     - that sessionStart never fires for a subagent;
     - that its create lands before the conversation's first gated call.

     One deny remains, a cost this row names for the engineer: from Phase 5a, that CLI's subagents meet requirement 8's case 1, because the CLI fires no subagentStart and their marked calls meet an empty active set.

     Row 322 withdraws the sessionStart creation point to the Phase 4 re-plan as a candidate, and beforeSubmitPrompt stays the one creator (r21). OD9's attribution follow-up retires the root record (r21).

320. Round 49 of `/code-review` found the walk's start, its deadline, its log vocabulary and its tests short of the launcher rule's requirement, and the plan answers it (r21). [verified: `_lib.sh:69-84`, the header of `_lib_capped_for`, and `_lib.sh:129-140`, its body, and `pyproject.toml:31`'s `timing` marker, read this revision] What changes (r21):
     - the walk takes an optional start PID after `ps`, the shim's own PID for the launcher and the hook-process PID for the sessionStart entry, so the capped call, the child bash and the entry's interpreter are never classified. The premise list names those wrappers, and `TestLauncherRule` gains the interposed-hop chains and a real-process-tree row;
     - the CLI-lineage token is `cli-agent` or `cli-cursor-agent`, the `launcher` field gains `walk-failed` and a one-word grammar, the contract names the token's readers and the precedence between `claude` and `helper-only`, and the run-mode log field holds text;
     - the allow emitter suppresses on 0, the CLI-lineage tokens and `helper-only`, and requirement 2's residual and requirement 38's text and rows follow, the CLI-token registration included;
     - the launcher captures the child's stdout to a file, the shim computes one deadline, the walk is skipped below a floor, `generate` asserts the sum and a relation test reads it. The sessionStart walk has a total deadline, and the exec bound is a formula over two depths;
     - only the hook's own denies are eligible to stand down, `cli_stand_down` is written on every install and read by equality, `status` counts stood-down calls by hook, and the doc states a CLI conversation's log cost;
     - requirement 8's table, remedy texts and creator rows, step 8's observation 6 and the pre-Phase-5a check follow, with a child set, spawn routes and a control arm. The gate matrix gains identity-unresolved arms. OD9's attribution follow-up retires that observation and the check (r21);
     - the sandbox report drops unusable exemptions and counts wrong shapes, and requirement 39 names the in-root swap and the `$TMPDIR` residuals;
     - Recovery gains the `top` cause, requirement 22's checklist gains step 17's cell 1, and step 17 records the raw `ps` lines, the hop counts and the `cursor_version` values, and runs the restricted-subagent arm;
     - the directory-input refusal gives the `cd` remedy only for a value inside a root, and echoes only that value's `realpath` form when it is a safe path (the final text is Repo resolution's, as row 323 states; r21).

     These stay [unverified]:
     - that the start PID skips every wrapper the adapter inserts, until step 17's cell 1 records it;
     - C9's hop count, which step 17 records;
     - that the hook's `$TMPDIR` equals the agent Shell's;
     - the registration that carries the flag for requirement 8's check.

     Row 322 withdraws this row's walk, stand-down, deadline, log-field and step-17 changes, and the gate matrix's identity-unresolved arms. Its requirement 8, step 8, pre-Phase-5a check, sandbox report, run-mode field and directory-input changes stay, with the run-mode field as a closed label (row 322). Row 323 replaces the directory-input remedy with one constant text, which Repo resolution states (r21).
321. The launcher rule is held to the cooperative tier. [engineer-verified: "Cooperative for the launcher rule only (Recommended)"] The label answered the session's question on the threat tier the review should hold the rule to. The option's text is the session's wording, and so is this reading of it (r21):
     - the cooperative tier covers the launcher rule and the CLI stand-down: the walk, the CLI-lineage token, the root record's creation points and the cost list;
     - a case that needs a steered agent to shape a process lineage is a disclosed residual, in requirement 39 and the cost list, not a reason to add another layer;
     - the gates the adapter ports keep their tiers, and the stand-down relaxes the `irreversible`-tier worktree and marker-gated gates for a call whose walk prints the token, per entry and origin, as the Installer's cost list and requirement 39's residuals state (r21).

     Row 322 withdraws the rule, and the tier applies to its Phase 4 re-plan (r21).
322. The detailed CLI-coexistence launcher rule is withdrawn, and the Installer's CLI-coexistence block states what the Phase 4 re-plan must do. [engineer-verified: "Shrink to requirement + C9, design in Phase 4 (Recommended)"] The label answered the session's question after each review round from 48 to 51 found new seams in the rule's added text. The option description is the session's wording. The session reads the choice as a net reduction that adds no mechanism, and that reading is the session's (r21):
     - withdrawn: the launcher block and everything that served it alone. That is `_lib_cursor_host_pid`'s three-outcome contract and its tokens, start-PID argument and recorded `ps`; the walk's deadline, cap and exec arithmetic; `cli_stand_down`; the `launcher` field and its tokens; `--needs-identity`; `TestLauncherRule` and `TestCliStandDownRecord`; acceptance step 17; requirement 3's exception; the gate matrix's identity-unresolved arms; the walk's chain-builder module and its select-tests rows; and the stand-down's costs, residuals, Recovery and report text;
     - restored to their pre-r20 contracts: `_lib_cursor_host_pid`, the allow emitter, the session hooks' decline, requirement 38's text and rows, the sessionStart host-walk report (which keeps the field Installer step 3 gives it in requirement 15's log-field table), `TestSessionStartReports`, and requirement 28;
     - kept: the root record with beforeSubmitPrompt as its one creator, which OD9's attribution follow-up later retires (r21), the `tool_input.cwd` refusal (its text is Repo resolution's; row 323 fixes it), the sandbox report's widening and canonical-root rules, requirement 29 and OD7, the Phase 3 acceptance-only install, the capability shell matrix, the pre-Phase-5a check, C9, and requirement 15's log-field grammar with the run-mode field's closed label (`unreadable`, `unreadable+acceptance-only`), which replace row 320's "the run-mode log field holds text";
     - the gate: a CLI session beside an installed adapter meets its denies until the re-plan states the rule, and Phase 4 does not merge before then.

     Row 323 replaces the directory-input remedy this row names with one constant text (r21).
323. The directory-input refusal gives one constant text, and the sandbox report keeps its case fold with the residual stated. [engineer-verified: "Constant text, no path echo (Recommended)"] [engineer-verified: "Keep the fold, state the residual (Recommended)"] The labels answered the session's questions on code-review round 54's findings. The option descriptions are the session's wording, and so is this reading of them (r21):
     - the refusal's text echoes no part of the value, and its one home is Repo resolution, whose literal text replaces this row's first wording (row 325). The safe-character allowlist, the per-value texts, the `realpath`-form remedy and their `TestDirectoryPin` rows go, and one row asserts the text byte-identical for every value;
     - on a case-sensitive volume, a case variant of a root or of `/tmp` counts as inside, though it can name a different directory the grant would make writable. The report is report-only, so the fold stays and the residual is stated where the rule and its row are.
324. Acceptance step 17's design-input cells inform a `CLAUDE.md`-delivery design in another session and gate nothing in this plan. [engineer-verified: "You should add them. I need to run cursor tests to determine the design that that session is working on"] The engineer typed this about the cells that session asked for. The cells' wording, their repoint-and-restore text and their [unverified] premises are the session's (r21). The step reuses the number 17, which rows 313 to 322 use for the withdrawn CLI step.
325. Round 55's held items take a plan-architect consult's lighter forms. [engineer-verified: "I think take all. Consult with the architect?"] The rest of this row is the session's wording and reading (r21):
     - the engineer's earlier choice to take the recommendation for the root record's creator, a generate-time assertion, is replaced on the consult's advice. An informational, matcher-less adapter-only beforeSubmitPrompt entry in `root` mode creates the record, so the record no longer rests on harness UserPromptSubmit registrations the adapter does not own. The adapter-only list also names attribution's subagentStart and subagentStop entries. OD9's attribution follow-up retires the `root` entry (r21);
     - the directory-input refusal's literal text is ciso R6's, at Repo resolution only, and requirement 36's byte-identity row checks that literal;
     - the sandbox rows' placement precondition is one sentence, rows 320 and 322 point to Repo resolution, Recovery's CLI-deny entry applies from Phase 3, the CLI block names a CLI subagent's case 1, step 8 counts subagentStart's id fields, requirement 15 names the `cursor_version` field, and the invalid-id deny is a named resolution deny;
     - each phase's dispatch owns its section's test rows and message texts, and step 17's `CLAUDECODE` premise is [unverified], as row 40 states;
     - Phase 4's file list naming `TestAdapterIdentity`, and the further requirement 36 rows, wait for those phases' code reviews.

**Mechanisms.** M15 to M33 are Phase 1's, and M34 on are the adapter's. Where a mechanism is heavier than the lightest option, it names the lighter primitives it beat, and Alternatives set aside says why each fails.

- M15. A probe that records shapes, identifier-shaped keys and tool names, and `vocabulary.json` env and process names, never a value, counting every other name, with its kit state outside every workspace. anchors: row4, row42, row103.
- M18. A kit that creates its own scratch repos and writes only fixed-name files it has recorded. anchors: row2, row4, row107, root. Lighter primitives: a hand-edited `~/.cursor/hooks.json`, and a separate Cursor profile or `HOME`.
- M20. The `detach` child, started through `subprocess.Popen(start_new_session=True, close_fds=True)` with stdio on `/dev/null`. anchors: row9. Lighter primitives: `nohup cmd &`, and `&` with `disown`.
- M21. A bash 3.2 wrapper that checks the 3.11 floor and execs a Python sanitizer. anchors: row26, row38, row57, row58. Lighter primitives: a jq sanitizer, and an all-Python probe entry.
- M22. A `neutral` output chosen from S1, never an allow; `none` for every call out of scope; and allow-class outputs bound to the cell's exact command. anchors: row51, row52.
- M23. A control cell opening each toggle-on scenario, read against S1's zero-hook control. anchors: row53, row84.
- M24. The unconditional phase exit: the revision, then `/plan-review`, before any Phase 2 file changes. anchors: row43, row44, row48, row54, row80.
- M25. Project-level native registration, with the user-level file only for the steps that need it. anchors: row2, row63.
- M26. The imported mirror, rendered at `scenario` time from the stowed `~/.claude/settings.json`, the file the import reads, so it cannot drift from what it mirrors. anchors: row12, row13, row31, row65.
- M27. The live-gate family, with CC1's Claude Code control, the bare config dir listing and the stop rule, as evidence that the user-level file runs. anchors: row41, row53, row61, row64, row105. Lighter primitives: the control cell alone, and a by-hand count of new `sessions/` entries.
- M28. `selfcheck`, run before `init` and after any kit edit, and `summarize`, which enumerates sub-question ids after S1's first step and after each scenario. anchors: row33, row42, row66, row82. Lighter primitives: reading the kit's code, and no check.
- M30. The kit's source in a `mktemp -d` directory under `~/.cache`, outside any work tree, which the engineer deletes by hand after the run. anchors: row66, row68, row98. It is the lighter primitive to a committed kit.
- M31. `/code-review` on the kit files by path before `init`. anchors: row69, row73.
- M33. The engineer's private pre-`init` checks, and the stop point, read by `summarize` through `--approval-pinned` and the `run-stopped` note. anchors: row84, row86, row109.
- M34. One generated native entry per harness registration, running the unchanged hook through `hook-shim.sh` on a translated payload, with a Write to an existing file translated to `Edit`. anchors: row113, row116, row119, row123, row124, row125, row143. Lighter primitives: import-only, translation inside `_lib.sh`'s shared parser, and per-hook Cursor branches.
- M35. The import's copies running beside the shim's entries, with the two session hooks declining on the payload predicate and the host walk. anchors: row121, row148, row165, row177, row179. It is the lighter primitive to the `_lib.sh` guard Alternatives sets aside.
- M36. `failClosed: true` on every gate entry, with a hook's no-opinion and allow mapped to `{}`, and a total output table that maps exit 2 and `ask` per event. anchors: row120, row121, row154, row157.
- M37. Repo resolution: a directory per event, with each agent Shell call pinned by one `updated_input` rewrite, a `builtin cd -- '<dir>' || exit 1` pin prefix, to the first workspace root, or a leading `cd` into a root or a directory inside one, a `pin-check` entry denying a command that lacks the expected pin prefix, a file tool's target root, the first root otherwise, a deny only for a Shell call with no absolute directory to pin, the directory-input refusal for a Shell directory input other than the pinned directory, and row 170's taint rule as the fallback if the rewrite proves unusable. anchors: row114, row133, row136, row137, row150, row166, row230, row244, row266, row267, row286, row288, row292, row312. Lighter primitives: rewriting `tool_input.cwd` through `updated_input`, and the taint rule while the rewrite applies.
- M38. Host-neutral identity under the shim, behind any live registered ancestor and refused under `CLAUDECODE`, carried into Shell calls by the directory pin's `updated_input` rewrite and into the import's copies by their own payload, which the worktree guard passes as an argument, with the guard keying the lock rule on session id for an unregistered PID. anchors: row126, row137, row145, row244, row159, row160, row165, row174, row176, row177, row186, row255, row269. Lighter primitives: window-scoped identity, and a sessionStart `env` value.
- M39. Active-set subagent attribution from subagentStart and subagentStop, keyed on the direct parent and applied only to calls that carry `parent_agent_id`. anchors: row124, row138, row142. Lighter primitives: per-call attribution, and denying every subagent call that touches Shell or a write.
- M40. The commit-stall, context-cap and compact twins, over afterAgentResponse, stop, preCompact and postToolUse. anchors: row46, row130, row131, row139, row140, row141.
- M41. Deferred context, delivered on the next postToolUse. anchors: row47, row131.
- M42. `gate-credential-exposure.sh`'s pre-call arms, native MCP redaction, and the post-call detection arm. anchors: row20, row46, row128. Lighter primitives: the detection arm alone, and Cursor's global ignore list.
- M43. `gate-settings-permissions.sh` for the deny and ask entries and the exact marker and ledger shapes. anchors: row19, row36, row129.
- M44. An opt-in installer that writes only the generator's fixed output set, which spans `~/.cursor/` and the routed config dir, and takes the routed config dir as a machine-local argument. anchors: row2, row7, row8. Lighter primitives: a `cursor` stow package, and a hand-edited `~/.cursor/hooks.json`.
- M45. Generated agents, rules and instruction files under `~/.cursor/`. anchors: row30, row31, row32.
- M46. Self-protection through the Asks set: the ask-in-chat deny on a Write or Delete of the adapter's files, of a workspace's `.cursor/hooks.json` or `.cursor/mcp.json`, or inside `~/.cursor`, decided by file identity, with no Shell-command matcher, and the tamper comparison's report of an entry removed, changed or added as detection. anchors: row36, row148, row152, row182, row190, row192, row202, row216. It reuses the Asks twin rather than the system-level managed hooks file or a Shell matcher, both of which Alternatives sets aside.
- M47. The gate-matrix integration test between the unit tests and the attended run, with an import arm that runs each script as the import does. anchors: row114, row123, row148.
- M48. A fail-closed, matcher-less preToolUse entry for unclassified tool names, and a deny past the stdin cap. anchors: row153, root.
- M49. Twins withheld in `parity-exceptions.json` until acceptance confirms their premise. anchors: row140, row141.
- M50. `_lib_emit_allow_with_context` printing nothing on an untranslated Cursor payload under a `Cursor` host, so no import copy gives an explicit allow. anchors: row155, row175, row183. Lighter primitive: observing whether the allow suppresses an `ask` before acting, which Alternatives sets aside.
- M51. An installer that writes a durable pre-image first and its manifest last, owns entries by the Installer's command grammar, and uninstalls entries first and state last. anchors: row114, row171.
- M52. Lock-free single-line log appends under the stdio buffer, fire counts derived from the log, and rotation under a non-blocking `fcntl.flock`, with the sessionStart entry alone sweeping first-call marks. anchors: row134, row188. It is the lighter primitive to a locked append and a read-modify-write counter.
- M53. A user-level `~/.cursor/sandbox.json` that `install` merges and `uninstall` restores, listing only the agent-run scripts' directories in the routed config dir, so their writes land under a sandboxed mode. anchors: row2, row258, row260, row262. Lighter primitives: Allowlist with the sandbox off as the only mode, and a workspace-level `sandbox.json`.

## Critical files

- Modify: `.claude/plans/cursor-adapter.md`, the only tracked file Phase 1's PR creates or edits (row 66). After the run, the session wrote the observations section above the Approach heading, and this revision plans Phases 2 to 6 and writes the surviving requirements back (row 80). Phases 2 to 6 list their own files below, each phase in its own PR.

The kit is one `code-writer` dispatch, outside the repo. It takes Phase 1's spec from this plan by path, writes the five files Kit layout names into the source directory the session passes it, and verifies with `python3 -I -B <source dir>/probe-kit.py selfcheck`. The session then runs `/code-review` on those five files by path, and a further `code-writer` dispatch fixes any finding and re-runs `selfcheck` (row 69). The kit imports nothing from this repo, so it copies what it reuses:

- the session-id grammar of `_lib_valid_session_id_component` (`claude/.claude/hooks/_lib.sh:2852-2854`, row 50), for the session-id boolean;
- the floor check at `claude/.claude/hooks/_config.sh:40`, as the shape of `probe.sh`'s, which fails quiet where `_config.sh` fails loud (row 58);
- each live gate's `DENY_GATE_LABEL`, and `NOOP_PHRASE_RE` itself (`claude/.claude/hooks/deny-no-op-dispatch.sh:70`), for the live-gate cells, their `named-gate` notes and Scenario lint's no-op check (row 64).

Before the dispatch, the session fetches these pages and passes the named parts into it verbatim:

- cursor.com/docs/hooks (G9): the configuration schema, the managed, project and user file locations, every per-event input and output schema, the per-script options with `timeout`'s default as the page states it, which the probe's deadline does not rest on (O22(b) measures it), the Environment Variables table, and anything it says about a Hooks view. The kit renders `hooks.json` in this schema.
- cursor.com/docs/reference/ignore-file: the default ignore list and its use of `.gitignore`, for the `.netrc` trigger (row 64).
- cursor.com/docs/subagents: where agent files may live, for the probe agent's placement.
- cursor.com/docs/agent/security/run-modes: Allowlist mode and the sandbox setting, for the prerequisites.

If a fetch is denied, the session reports the denial. A denied hooks-doc fetch stops the dispatch, because the kit cannot render `hooks.json` without that schema. For the other pages the dependent choice takes its fallback: the probe agent goes user-level, the `Read` trigger stays `.netrc` with O28(a) recording any ignore-list confound, and the prerequisites follow row 52's relayed Run Modes text.

**Phases 2 to 6.** A new script sits under `claude/.claude/scripts/cursor-adapter/`, and a new test under `claude/.claude/scripts/tests/`, unless its line names another directory. Each dispatch verifies with Verification 6's commands, and the PR's file limit is its phase's list plus this plan where a revision changes it.

- Phase 2, one dispatch, after the OD9 results Run order and recording's Phase exit paragraph names are in the observations section (Repo resolution; r13). Create `tool-map.json`, `field-table.json`, `parity-exceptions.json`, `translate-input.jq`, `translate-output.jq`, `hook-shim.sh`, `_cursor-adapter-lib.sh`, `test_cursor_adapter_translate.py`, `test_cursor_adapter_shim.py`, `claude/.claude/hooks/tests/_cursor_payloads.py`, `claude/.claude/hooks/tests/test_cursor_adapter_gate_matrix.py` and `claude/.claude/hooks/tests/test_cursor_adapter_lib_parity.py`. Modify `claude/.claude/hooks/tests/test_bash_floor.py`, `claude/.claude/scripts/select-tests.py`, `claude/.claude/scripts/tests/test_select_tests.py` and `docs/scripts.md`.
  - `_cursor_payloads.py` is the one Cursor payload builder both test trees import, a non-test module as `_lib_sh_helpers.py` is (requirement 20, row 180).
  - The shim does not source `_lib.sh` (requirement 28). It sources `claude/.claude/hooks/_config.sh` for the bash floor (requirement 15), and `test_bash_floor.py` gains the shim's pre-source region in its tripwire. If that file's list of scripts that source `_config.sh` is not discovered by glob, the shim joins it (row 167).
  - The lib copies the session-id grammar of `claude/.claude/hooks/_lib.sh:2852-2854`, the capped-call shape of `_lib_capped` (`_lib.sh:50`) and all three branches of `_lib_realpath_m` (`_lib.sh:177-188`, row 181). Each has a behavioral parity test in `test_cursor_adapter_lib_parity.py` (requirements 10, 15, 36). The lib also holds the log-field `NAME=value` block, with the run-mode field's row, whose parity test with Python's parse lands in Phase 3, where `cursor-adapter.py` first exists (requirement 15; r9 plat R6; r21).
  - The gate matrix and the parity test sit under `claude/.claude/hooks/tests/`, so a hooks edit selects them (requirements 10, 40).
  - The two `select-tests` files take these rows, each with a `test_select_tests.py` case (requirement 22; r4 sdet R3, sdet R4):
    - a domain row so that any path under `claude/.claude/scripts/cursor-adapter/` selects the hooks tests, because the matrix and parity tests read the adapter's JSON, jq and shell files through a subprocess, which `TestCrossDomainReadCompleteness` cannot see;
    - `_cursor_payloads.py` in `HOOKS_TESTS_MODULES_IMPORTED_BY_SCRIPTS_TESTS`, and each scripts test that imports it in `SCRIPTS_TESTS_IMPORTING_HOOKS_TESTS_MODULES`;
    - any reverse row `TestCrossDomainReadCompleteness` requires for a path those tests read (row 25).
  - The dispatch fetches cursor.com/docs/hooks for the per-event input and output schemas that `field-table.json` and requirement 20's vocabulary pin, and builds fixtures from C5's key paths.
  - It also runs OD5's fan-out benchmark from a scratch directory outside the repo, and reports the per-class numbers against OD5's budget.
- Phase 3, one dispatch, whose generator emits the directory pin's entry (Repo resolution; r13). Create `cursor-adapter.py`, `test_cursor_adapter_generate.py`, `test_cursor_adapter_install.py` and `docs/cursor-adapter.md`. Modify `parity-exceptions.json`, `claude/.claude/hooks/tests/test_cursor_adapter_gate_matrix.py` (requirement 41's arm and the Recovery-section test), `docs/scripts.md`, `README.md` (the "Updating after git pull" step) and `.gitignore` (the adapter state dir). The Recovery-section test needs no `select-tests` row, because the `docs/` row and Phase 2's domain row already select the hooks tests (row 180).
- Phase 4, one dispatch. OD11(b), OD11(d) and OD11(e) are decided (r15; r16). Create `claude/.claude/hooks/tests/test_lib_cursor_identity.py`, which holds requirement 14's snapshot as a literal and `TestCursorPayloadDecline`, and requirement 31's two env-scrub files, `claude/.claude/hooks/tests/test_cursor_adapter_hooks_env_scrub.py` and `claude/.claude/scripts/tests/test_cursor_adapter_scripts_env_scrub.py`. Modify `claude/.claude/hooks/_lib.sh`, `claude/.claude/hooks/capture-session-id.sh`, `claude/.claude/hooks/record-session-end.sh`, `claude/.claude/hooks/tests/conftest.py`, `claude/.claude/scripts/tests/conftest.py`, `claude/.claude/hooks/tests/test_cursor_adapter_gate_matrix.py` (the linked-worktree rows), `hook-shim.sh`, `_cursor-adapter-lib.sh`, `cursor-adapter.py`, `test_cursor_adapter_install.py` (the `sandbox.json` merge and its behavioral test, OD11(b); `TestRunModeWarning`'s Phase 4 row; `TestLogFieldTableParity`'s Phase 4 row; `TestSessionStartReports`), `test_cursor_adapter_shim.py` (`TestDirectoryPin`'s Phase 4 rows), `parity-exceptions.json` and `docs/cursor-adapter.md` (r13; r15; r17 sdet R3, plat R9; r21 sdet R7).
  - The `_lib.sh` functions are the ones Identity and routing lists, and `_lib_parse_tool_input_or_deny` is not among them (row 174). The identity test drives every consumer that section lists, through `marker.sh clear-stale` among them, under an adapter pair and under Claude Code with adapter variables set (row 159).
  - The existing identity tests stay unedited: `test_lib.py`, `test_lib_emit.py`, `test_lib_worktree_collision_guard.py`, `test_marker_script.py`, `test_capture_session_id.py`, `test_record_session_end.py`, `test_lib_markers.py`, `test_marker_worktree_keying.py` and `test_review_ledger_script.py` under `claude/.claude/hooks/tests/`, and `test_marker_clear_stale.py`, `test_handoff_record_conversion.py`, `test_pr_diff_against_base.py` and `test_review_pr_lib.py` under `claude/.claude/scripts/tests/` (requirement 14).
  - `claude/.claude/scripts/marker-clear-stale.py` needs no edit (Identity and routing, row 161).
  - The dispatch re-runs OD5's benchmark, its uninstalled arm on the merge-base `_lib.sh` (OD5; r6 plat R3).
  - Its PR merges only after the Phase 4 re-plan states the CLI-coexistence rule (Installer's CLI-coexistence block; r21).
- Phase 5a, the first of three sequenced PRs, after OD9's C4 and its attribution follow-up (r21). Create `gate-settings-permissions.sh`, `gate-agent-tools.sh`, `gate-credential-exposure.sh` and `test_cursor_adapter_gates.py`. Modify `_cursor-adapter-lib.sh`, `hook-shim.sh`, `cursor-adapter.py`, `parity-exceptions.json`, `claude/.claude/hooks/tests/test_cursor_adapter_gate_matrix.py`, `test_cursor_adapter_install.py` (`TestSandboxGrantWritten`, the grant's first real write, and `TestWorkspaceSandboxReport`, the workspace report's rows), `claude/.claude/scripts/select-tests.py`, `claude/.claude/scripts/tests/test_select_tests.py` and `docs/cursor-adapter.md` (r5 cd R11; r19 be R2, sdet R5).
  - The gates test and Phase 5c's agents test read `claude/.claude/agents/*.md`. The two `select-tests` files therefore gain a reverse row from that directory that selects `SCRIPTS_TESTS_DIR` as a directory, so Phase 5c needs no row of its own (row 25; r5 sdet R6).
  - The credential gate sources `_lib.sh` as a gate does and reuses `_lib_redact_credential_shaped_strings` and `_LIB_CREDENTIAL_PATH_REGEX` (`_lib.sh:3912`).
  - The permissions gate reuses `_lib_split_fragments` and `_lib_fragment_command_word` (`_lib.sh:2086`, `:2109`).
  - The dispatch re-runs OD5's benchmark with the adapter-only entries this phase adds.
- Phase 5b. Create `test_cursor_adapter_twins.py`. Modify `hook-shim.sh`, `_cursor-adapter-lib.sh`, `field-table.json`, `cursor-adapter.py`, `parity-exceptions.json`, `claude/.claude/hooks/tests/test_cursor_adapter_gate_matrix.py` and `docs/cursor-adapter.md`. The dispatch re-runs OD5's benchmark with the twins' entries.
- Phase 5c. Create `model-map.json`, `templates/claude-vocabulary.mdc` and `test_cursor_adapter_agents.py`. Modify `cursor-adapter.py`, `docs/cursor-adapter.md`, and `parity-exceptions.json` with acceptance step 6's withheld-twin outcomes from Phase 5b's run (r5 plat R2).
- Phase 6. Modify `docs/cursor-adapter.md`, `field-table.json` with the paths acceptance observed (requirement 6), and this plan with the acceptance results under the admission rule's Phase 6 clause. Acceptance step 7's MCP fixture, the scratch entries of step 3's cell 5, step 4 and step 6, and step 14's scratch deny entry live in the scratch root, outside the repo, as Phase 1's kit did.

## Verification

1. Only this plan changes. Before the PR opens, `git diff --name-only origin/main...HEAD` prints only `.claude/plans/cursor-adapter.md`, and `git status --porcelain --untracked-files=all` prints nothing. `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py` passes, and its rule table maps `.claude/plans/` to no test directory (`select-tests.py:617`).
2. The kit is ready before `init`:
   - `/code-review` on the five kit files leaves no unresolved blocker (row 69);
   - `python3 -I -B <source dir>/probe-kit.py selfcheck` passes after the last kit edit, with every case listed as passing;
   - the session has compared Scenario lint's sample outputs and rendered entries with the hooks-doc excerpt, and its sub-question list with the Observations table.
3. The run is complete when every scenario has run, or the run ended at the engineer's stop point and `summarize` lists each scenario it left unrun, and all of these hold:
   - S1's first-step `summarize` showed a payload record;
   - `summarize` gives every sub-question of each scenario run an answer, "did not fire" with its control present, or "not observable" with its reason, and none reads "no outcome recorded";
   - each scenario run had its smoke cell fire, with any first miss reported;
   - each scenario run that runs Shell legs shows a leg record from its first smoke cell, or the miss is reported;
   - S1's `neutral` selection exists, an undecided Shell selection included;
   - the engineer has decided to rerun or accept each live-gate cell whose CC1 control did not read `named-gate`, and each S7 cell with the live deny side whose CC1 control or S5 `Bash` cell did not.

   A stop point may fall inside a scenario. The cells left unrun, and each sub-question still reading "no outcome recorded" for that reason, are then listed in the observations section with the reason (row 33), and the engineer accepts that list in place of the "none reads" and smoke-cell conditions above.
4. Content and cleanup:
   - The `## Phase 1 observations` section holds only the sources the observations admission rule after Run order step 6 lists, and never a path, user, host or id fragment derived from the run (admission rule 3).
   - The engineer confirmed in the session, per that rule, that they read the staged text or accepted a clean scan of it in their place, the scan the rule defines.
   - On either arm, the engineer confirmed that the blocklist holds the run's names the rule lists.
   - `deny-private-project-refs` passed each commit (row 93).
   - Phase 1's commit messages and PR body describe the plan change only, with no run data or fact about the run's Cursor setup, and `deny-private-project-refs` passed each (row 93).
   - Before the PR opens, the final `teardown` has run, the engineer has removed any `~/.cursor` file it named, deleted the folders it printed and completed every revert it printed, and `~/.cursor/hooks.json` is the engineer's original or absent.
5. Phase exit, whatever the observations show: OD4 and any triggered OD6 have been asked with the evidence, and `plan-architect` has planned Phases 2 to 6 and written the surviving requirements back into this plan. OD7 to OD10 are decided and written in (row 145). `/plan-review` has passed that revision. Only then does any Phase 2 file change, and only after the OD9 results Run order and recording's Phase exit paragraph names are in the observations section (r13).
6. Phases 2 to 6, per PR:
   - `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py` passes and runs the phase's new tests, which a path under `claude/.claude/scripts/` or `claude/.claude/hooks/` already selects (row 25). From Phase 2 on, a path under `claude/.claude/scripts/cursor-adapter/` also selects the hooks tests (requirement 22).
   - `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/` and `scripts/list-shell-files.sh | xargs -0 scripts/venv-run.sh shellcheck` pass.
   - `scripts/venv-run.sh pytest --collect-only -q` over the adapter's test files lists every class the Test map assigns to that phase and to every earlier one.
   - In Phase 4, which edits `_lib.sh`, requirement 14's snapshot test passes. The PR also runs it once against a worktree at the merge-base, made with `git worktree add <tmp> $(git merge-base HEAD origin/main)`, under requirement 14's one pass condition for that run (r7 be R3). The existing identity tests, `test_lib.py` among them, sit outside Phase 4's list, so the name-only check below keeps them unedited.
   - Phase 4's dispatch waits on no OD11 item: OD11(b), OD11(d) and OD11(e) are decided and written in (OD11; r13; r15; r16). Phase 4's PR merges only after the Phase 4 re-plan states the CLI-coexistence rule (the Installer's CLI-coexistence block; r21).
   - From Phase 3 on, after the phase merges, the engineer runs the attended steps Phases assigns it (row 171). The next phase's PR body records only pass or fail per step.
   - The Phase 2, 4, 5a and 5b dispatches report OD5's benchmark numbers per class in the session, and a miss takes OD5's miss route.
   - `git diff --name-only origin/main...HEAD` names only that phase's Critical files, plus this plan where a revision changed it.
   - Phase 6: each acceptance step passes, or is recorded with its reason and the engineer's decision to fix or accept. Step 12's record is checked against OD5's budget.

## Out of scope

- Committing the probe kit, a test suite for it, `docs/cursor-adapter.md` or a hooks-doc fixture (row 66). The doc starts in Phase 3, the first phase with a user-facing command, and until then this plan's Run order is the runbook.
- Checks a shipped kit would need, because the kit runs once, attended, on one machine (row 82):
  - crash injection and the `~/.cursor` temp-file, planned-hash, rename and adopt protocol, since a crash leaves a file `teardown` names for the engineer to remove;
  - a scoping matrix across every behavior, since the scope decision is one function ahead of behavior dispatch;
  - the closed-schema and concurrent-append tests, the fake-host dry run, the answerability table and a synthetic "did not fire" record. The sanitizer's positive partners, `fcntl.flock`, S1's first-step `summarize`, the per-scenario `summarize` and each smoke cell cover what they guarded;
  - the `neutral` case, the in-scope `sleep` and `detach` cases and the `init`-refusal cases, since the run shows any misbehavior;
  - the 3.11 grammar, index-mode, interpreter-isolation and session-id-oracle tests, which guarded shipped code.
- S8 and O26, the per-call timing scenario, with `--record timing`, `init`'s calibration and S8's allowlist entry (row 75). OD5's budget is checked by Phase 2's benchmark and by the Phase 6 acceptance run.
- S11 and O24, the ignore-list scenario (row 77). The adapter's `Bash` deny gate carries the `Read` deny entries whatever O24 would show (row 78).
- A per-scenario check that the import toggle took effect. A toggle pinned either way stops the run before `init` (Run order), and the engineer sets and sees the toggle at each step.
- Sub-questions that no A, B or I criterion, OD item or safety rule rests on:
  - O9(f), whether the toggle writes a key to Cursor's user settings JSON;
  - O20(d), one entry registered twice, which requirement 7's generator dedupes;
  - O20(e), whether a lone allow suppresses the prompt, which requirement 2 bars whatever the answer (row 51);
  - O22(e), user-level hot reload, with S6's hot-reload cell. I3 stays unmet (Phases 2 to 6, I3), and the doc discloses hot reload as untested (r21).
- The Cursor CLI as an adapter target (row 5), its `~/.cursor/cli-config.json` deny list included (G7). The follow-up run (row 112) uses the CLI only as a probe launcher, and its smoke test observes whether hooks load there. The adapter does not support the CLI. Running a CLI session beside the installed adapter is a requirement the Phase 4 re-plan designs, from the Installer's CLI-coexistence block (r20 be R2; r21).
- Cursor cloud agents (G4).
- Managed and plugin hook sources, which are out of scope and unobserved. OD6 states how an O20 result would reach them (row 84).
- A second IDE launch method. The observations name the one launch method every IDE relaunch used (row 70), and the other is a post-spike observation, not a second run. The CLI launcher (row 112) is not an IDE launch method.
- Any harness config or adapter file in a consuming repo (row 2).
- Translating the consuming repo's own `.claude/` hooks, skills, and agents, unless OD8 chooses the dispatcher. They run through the import as Cursor runs them, and the harness's denies must still win against them (row 56). O16, O17 and O20 record what that means, and `docs/cursor-adapter.md` states the limits.
- S10's cells for model IDs and user-level rules (O10, O11). This revision does not run them: `model-map.json` takes its names from the picker (row 89), the rules' `globs` scoping rests on row 32, and acceptance step 15 checks both.
- WebSearch gating. The harness gates no WebSearch call under Claude Code either (OD4).
- CC3, a rerun of CC2 after a Cursor relaunch. CC2 already shows which Cursor variables a Claude Code hook in the integrated terminal inherits.
- Invoking MCP tools during the spike. User-level MCP server names can identify a project, so no scenario calls one, and any MCP call that does occur records its tool name only as a counted `other`. Its argument key names would still record as written (row 111), an accepted residual given that no cell calls MCP.
- Porting transcript tooling (`cost-ledger`, `transcript-analysis`, `pr-cost`, `post-crash-sessions`) to Cursor's transcript format.
- Rewriting skill and agent bodies in Cursor vocabulary. The vocabulary rule covers that gap.
- Marketplace plugins and their hooks under Cursor, a documented gap, because the adapter ports the user-level harness and a plugin is a project-scoped install with its own `_lib.sh` (r21):
  - this repo's `.claude/settings.json:2-7` enables four, `skill-management`, `claude-hook-review`, `plugin-semver` and `issue-triage`. Of those, only `skill-management` and `plugin-semver` ship hooks;
  - `npm-semver` and `lovable-cloud` also ship hooks, and `README.md` installs them with `--scope project` in consuming repos;
  - the adapter generates no shim entry for any plugin hook. Where the import loads one, it runs on Cursor's untranslated payload. `require-npm-version-bump.sh` exits unless the tool name is `Bash` (`:119`), so it no-ops on Cursor's `Shell`. Whether `validate-migration-filename.sh`, on a `Write` matcher, enforces there, and whether the import loads plugin hooks at all, are [unverified];
  - `docs/cursor-adapter.md` states the gap and names both consuming-repo gates.
- Claude-Code-only UI settings with no gate behind them: `statusLine`, `skillOverrides`, and `sessionTitle`. Prompt suppression through `permissions.allow` is also out, because Cursor's own approval settings govern prompts. Its role as `enforce-marker-script-shape.sh`'s backstop is not out, and requirement 5 carries it.
- Adapting `~/CLAUDE.local.md` and other machine-private files.
- Self-regeneration of `~/.cursor` artifacts from a hook. The staleness check warns instead.
