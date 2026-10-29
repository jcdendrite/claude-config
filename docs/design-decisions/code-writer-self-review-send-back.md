# A hook sends back a `code-writer` return that skips its self-review

*2026-10-26.*

`code-writer.md` § "Self-review pass — mandatory before returning" tells the writer to read the reviewer files its diff maps to, check the diff against their angles, and report a Self-review line. Nothing checked that any of it happened, so a dispatch could skip the reads, report a clean self-review, and hand the parent a defect that costs a full re-review cycle. `send-back-skipped-self-review.sh` now checks the evidence a hook can observe at the point the writer returns, and sends the writer back once when it is missing. [code-writer-self-review.md](code-writer-self-review.md) records the self-review pass itself.

## Decision

One fail-open hook, `turn-gate` class, runs one evaluator (`evaluate-code-writer-self-review.py`) from two registrations. It reads the writer's own transcript and checks:

- every reviewer file the ledger names was Read in this dispatch;
- every reviewer the changed paths require has a ledger line and a Read;
- at least one Read-backed ledger line exists whenever a path changed;
- a `git diff` or a Read of a changed path follows the last edit.

A return that lacks any of these is sent back once per report cycle. The second return goes through. The hook never judges whether an angle was genuinely applied, because that is not observable.

## Two entry points, chosen from a probe

A harness probe on Claude Code 2.1.310 showed that the report leaves the writer differently by permission mode.

- In auto mode the report leaves through the `SubagentHandback` tool call. A PreToolUse deny on that call sends the writer back with the reason, and the writer acts on it. A `SubagentStop` block after a delivered handback fires but changes nothing.
- In other modes the writer makes no `SubagentHandback` call, and the report is the plain text it stops on. A `SubagentStop` block sends it back.

So the hook registers on PreToolUse matcher `SubagentHandback` and on SubagentStop matcher `code-writer`. The SubagentStop arm owns only a dispatch whose transcript holds no `SubagentHandback` call of any kind, a denied one included. That keys the split on what the writer did, not on a permission-mode name. After a denied handback and a plain-text stop, the harness itself re-prompts the writer, and the report then arrives through the next handback. So the SubagentStop arm must not consume that cycle's marker.

## `turn-gate`, no `onFailure`

The hook takes the `turn-gate` class, whose contract is sending an agent back instead of letting its run end.

- The `gate` class requires `"onFailure": "block"`, which is right where the guarded call is the risk.
- Here the guarded call is the writer's only way to deliver its report, and a PreToolUse matcher names a tool, so no registration field narrows it to one agent type.
- In the probe, a PreToolUse `SubagentHandback` hook with no `onFailure` that could not start, or that exited 1, let the handback through.
- In the probe, the same held for a SubagentStop group in `acceptEdits` mode.
- In the probe, a missing script under `onFailure: "block"` blocked every handback of every subagent type and lost the report.

The registrations and the script therefore behave as follows.

- Neither registration carries `onFailure`, and the hook never exits 2.
- Both registrations set `"timeout": 20` seconds.
- The in-script worst case is 14 seconds: two capped calls, each 5 seconds plus a 2-second kill grace. So the in-script cap fires and logs first.
- The hook omits `set -e`, because an aborting command's own status could be 2, which blocks on both events.
- Every handled failure allows. The only blocking output is the once-per-cycle send-back, emitted after its marker is claimed.
- The threat-model tier framework covers `gate` hooks only (see `docs/hooks.md` § "Threat-model tiers"), so the hook takes no tier row. It is a workflow check for a cooperative writer, not a security boundary.

## Send it back once

A marker directory `<config-dir>/.code-writer-self-review.d/<agent-id>`, claimed with `mkdir` before the deny or block is emitted, bounds the send-back at once per cycle. Both arms share it. A failed envelope write removes the marker, so that agent id's next return is sent back.

- An evaluator exit after the envelope flush, such as a cap kill, discards the envelope and leaves the marker. The incomplete return passes without a send-back, and the same agent id's next return passes too.
- A writer that never returns after a send-back leaves its marker, and nothing sweeps marker directories. That marker suppresses the send-back if the same agent id is resumed.
- In auto mode a second return that is still incomplete passes with a `SELF-REVIEW INCOMPLETE (hook-verified)` prefix the hook writes into the report through `updatedInput`.
- In other modes a hook cannot rewrite the report. The one send-back tells the writer to start its report with `SELF-REVIEW INCOMPLETE: <items>` if it stops without finishing, and the log records `passed-labeled` or `passed-unlabeled`.
- A send-back count read from the transcript was set aside, because a transcript that lagged behind a deny would re-deny the only exit. The marker has no freshness dependency.

## The `allow` on a spent marker

The PreToolUse arm answers `allow` with `updatedInput` on a spent marker, because an `ask` would prompt in auto mode on the writer's only exit. The hook does not read `permission_mode`. The arm fires only when a `SubagentHandback` call exists, and the Claude Code documentation does not state which `permission_mode` value a subagent's payload carries.

- The hooks reference (https://code.claude.com/docs/en/hooks) says `updatedInput` combines with `allow` to auto-approve, or with `ask` to show the modified input to the user, and that a hook's `ask` forces a permission prompt in auto mode.
- The permissions reference (https://code.claude.com/docs/en/permissions) says a PreToolUse hook decision does not bypass permission rules: a matching deny rule blocks the call, and a matching ask rule still prompts when the hook returned `allow`.
- Neither page says whether a hook `allow` skips the auto-mode classifier. That stays undocumented, and no code gate was added for it.

## The floor, the catch-all, and no skip

The path floor is the evaluator's `path_floor_reviewers`, which follows the domain table in `code-writer.md`. The writer still picks further reviewers. A catch-all requires at least one Read-backed ledger line whenever a path changed, and the domain table gained a last row mapping any other file to `staff-backend-engineer`, so every edited file has a named reviewer.

No size threshold removes the risk of a fix-induced defect, because review-round fixes go to `code-writer` regardless of size and a small diff can carry a defect. A declared skip for small changes was set aside for that reason, so step 5's whitespace-or-typo exemption went too: every added or rewritten prose line now gets the prose-row read, and every dispatch that changes a file re-reads its diff and Reads at least one reviewer file.

## Repository membership by `.git` walk-up

A path counts as changed when it is the resolved path of a successful Edit or Write, still exists at hook time, and sits in a repository. A path sits in a repository when its own directory or an ancestor holds a `.git` entry, file or directory, so a linked worktree counts by the `.git` file at its root. The payload `cwd` plays no part, so edits in a linked worktree count however the session was anchored. git itself was set aside as the authority: a toplevel taken from the `cwd` misses edits in a worktree the session is not anchored in, and a `git rev-parse` per distinct directory costs a subprocess each.

## The comment check reads the writer's own edits

Added text for the comment check comes from the writer's own successful calls on changed paths: each line of an Edit's `new_string` that is not a line of its `old_string`, and every line of a Write's `content`. That needs no diff parser, untracked-file read or encoding handling. For `.py` it counts every added line that starts with `"""` or `'''`, including a line that only closes a string, such as `""")`. That over-counts on purpose, for three reasons.

- `code-writer.md`'s domain table maps docstrings to `comment-discipline-reviewer`.
- `code-writer.md` § "Self-review pass — mandatory before returning" step 5 says "When in doubt, read."
- A false positive costs the writer a little extra work, and a miss costs a full re-review cycle.

## The ledger lives in the return

The Return format's Self-review bullet asks for one ledger line per reviewer file Read: the reviewer's backticked name, a colon, then each applied angle and its finding. The evaluator parses that grammar, a contract test pins it to a `HOOK_TEST_FIXTURE` example in `code-writer.md`, and the Return format says a dispatch's own return fields add to the format and never replace the ledger. A file ledger was set aside because subagents are barred from the review-ledger state, a file in the worktree shows in `git status`, and the parent reads the return.

## The transcript over a recorder

The writer's own transcript already holds every earlier Read, Edit, Write and Bash call in order, plus the text each edit added, so no per-call process is needed. A PostToolUse recorder would add a process to every tool call in every session, a state directory, and ordering races within a parallel batch. A call counts only when a paired `tool_result` exists and is not an error, so a Read issued in the same batch as the handback, whose result may not have landed, does not count. Reviewer-Read credit is a path-suffix match (`reviewer_name_for_read_path`) on a path the writer chooses, so a successful Read of any path ending in `/agents/<name>.md`, or in `/skills/ai-instruction-and-memory-files/SKILL.md` for that skill, credits that reviewer.

## Log verdicts

This section is the single home for the log vocabulary. The hook appends to `<config-dir>/.code-writer-self-review.log`, and each line reads `<UTC time> <event> <verdict> agent=<agent id>`, followed by count fields when the evaluator wrote it. The evaluator writes every verdict except `allowed-jq-failed`, `allowed-invalid-agent-id` and `allowed-evaluator-exit=N`, which the hook script writes.

| Verdict | Meaning | Degradation | Operator action |
|---|---|---|---|
| `clean` | No item is missing. A leftover marker is removed. | No | None. |
| `sent-back` | An item is missing and the marker was claimed, so the deny or block went out. | No | None. One per incomplete cycle is expected. |
| `passed-flagged` | PreToolUse arm, marker already spent, items still missing. The report went through with the `SELF-REVIEW INCOMPLETE (hook-verified)` prefix. | No | Treat the diff as not self-reviewed. |
| `passed-labeled` | SubagentStop arm, marker already spent, items still missing. The report starts with the `SELF-REVIEW INCOMPLETE` label the send-back asked for. | No | Treat the diff as not self-reviewed. |
| `passed-unlabeled` | Same as `passed-labeled`, but the report has no label. | Yes: no hook-authored line reaches the parent. | Treat the diff as not self-reviewed. |
| `passed-unflaggable` | PreToolUse arm, marker already spent, items still missing, and `message` is not a string. The call went through unchanged. | Yes: the prefix could not be written. | Treat the diff as not self-reviewed. |
| `not-owned` | SubagentStop arm, and the transcript holds a `SubagentHandback` call of any outcome. The PreToolUse arm owns that cycle. | No | None. |
| `allowed-continuation` | SubagentStop arm with `stop_hook_active` true, no marker, items missing. The hook never blocks a continuation. | Yes: an incomplete return is not sent back. | Treat the diff as not self-reviewed. |
| `allowed-no-repo-path` | The writer made edits, none of which sits in a repository or still exists. | No | None. |
| `allowed-no-transcript` | The writer's transcript file was not found. | Yes | Check that the payload still carries `agent_transcript_path` (SubagentStop) or `transcript_path` (PreToolUse) after a Claude Code upgrade, and that the writer's transcript still sits at `subagents/agent-<agent id>.jsonl` beside the session transcript. |
| `allowed-unparsed-transcript` | The transcript did not decode as expected. See the conditions below the table. | Yes | The transcript record shape changed. Compare it with the fixture under `claude/.claude/hooks/tests/fixtures/code-writer-self-review/`. |
| `allowed-claim-failed` | The marker directory could not be created or claimed. | Yes | Check that `<config-dir>` is writable and `<config-dir>/.code-writer-self-review.d` is a writable directory. |
| `allowed-claim-raced` | Another invocation claimed the same cycle first. Nothing was printed. | Yes: this report passes unflagged. | None. Two `SubagentHandback` calls in one batch produce it. |
| `allowed-jq-failed` | `jq` is missing, failed or was cap-killed. | Yes | Install `jq` on the harness PATH. |
| `allowed-invalid-agent-id` | `agent_id` is empty or holds a character outside letters, digits, `_` and `-`. | Yes | Report a payload-shape change. |
| `allowed-evaluator-exit=N` | The evaluator ended with status N, and anything it printed was discarded. | Yes | Read N as below. |
| `allowed-evaluator-error=<class>` | The evaluator raised an exception of that class and exited 1. | Yes | Find where the evaluator raises that class. |

`allowed-unparsed-transcript` is logged when any of these holds:

- The transcript has content but no line decoded into a record carrying a `message.content` list.
- At least one `tool_result` block decoded and none carries the id of a decoded tool call (including when no call decoded).
- A tool call decoded, no `tool_result` block decoded at all, and a `user` record follows the first call.

These notes apply to the log as a whole.

- The log gets one line per `code-writer` hook invocation that reaches a verdict, except an evaluator error, which logs the pair below. A normal auto-mode run logs `clean` from the PreToolUse arm, then `not-owned` from the SubagentStop arm.
- An evaluator error logs `allowed-evaluator-error=<class>` from the evaluator, then `allowed-evaluator-exit=1` from the hook script.
- `allowed-evaluator-exit=1` with no preceding `allowed-evaluator-error=` line means the evaluator failed before its handler ran. The hook discards the evaluator's stderr, so run the evaluator by hand to see the error.
- `allowed-evaluator-exit=2` means the evaluator file is missing or unlinked. A real-directory hooks layout needs `./install.sh`.
- `allowed-evaluator-exit=127` means `python3` is not on the harness PATH.
- A cap-kill status is one `_lib_capped_for`'s header lists under "Exit statuses", which also says which of them a child's own signal death can produce.
- The silent cases, where no line is written, fall in three closed classes.
  - Exits before the config directory resolves: a missing `_lib.sh`, a bash below the floor, a failed config-dir lookup (a relative `CLAUDE_CONFIG_DIR`), and invocation by bare name.
  - Payloads the hook does not own. The script exits for another agent type. The evaluator returns for a `hook_event_name` that is neither `PreToolUse` nor `SubagentStop`, or for a PreToolUse call to a tool other than `SubagentHandback`. Both cases are silent only once the `jq` pass and the agent-id check have passed.
  - A log append that fails.
- A harness kill at the 20-second timeout is also silent when no `timeout` or `gtimeout` is on PATH, because the in-script cap then does not run.
- The log is advisory and not tamper-evident, because the writer's Bash tool can reach the config directory.
- The `failed=` field lists the R-codes defined in the module docstring of `evaluate-code-writer-self-review.py`. The other count fields come from the evaluator's `Assessment` fields.

## Known degradations and unverified paths

- The probe ran on Claude Code 2.1.310. Versions 2.1.295 through 2.1.309 are unprobed and fail open.
- A background dispatch outside auto mode, a nested dispatch at spawn depth 2, and a SendMessage continuation are unverified. The outcome log (`<config-dir>/.code-writer-self-review.log`) is the instrument for each.
- A dispatch whose report is very long in auto mode is an open observation. A missing send-back or prefix there belongs here once seen.
- An agent defined elsewhere that is also named `code-writer` inherits the send-back, with one extra round trip in both modes. The send-back reason cites `code-writer.md` and its ledger format, which that agent may lack.
- A finding that the code misses a case never becomes a new prose line. It gets one of three outcomes:
  - A fix in code.
  - A `test_known_gap_*` case in `test_send_back_skipped_self_review.py`, when a reader could mistake the behavior for a bug.
  - No action, when the hook header's security-boundary line or this record already implies it.
