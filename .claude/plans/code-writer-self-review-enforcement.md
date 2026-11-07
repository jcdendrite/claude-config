# Enforce code-writer's self-review pass

## Context

Goal: make `code-writer`'s mandatory self-review pass something a dispatch cannot silently skip, so defects a fix introduces are caught inside the writer's own context instead of by the next `/code-review` round.

Ask: "maybe have it write a ledger for what it checked. And then if it can check itself there, that should be much cheaper for it to fix stuff. Like, gosh, if it's really not checking against the defect rules and just quote unquote forgetting, that seems like it'd be a really big lever." Then: "Yep, I totally agree." (in reply to running `/plan-it` on enforced `code-writer` self-review as the first fix).

`claude/.claude/agents/code-writer.md` § "Self-review pass — mandatory before returning" tells the writer to re-read its diff, read the reviewer agent files its diff's domains map to, check the diff against each file's review angles, and report a per-domain Self-review line. Nothing checks that any of this happened. A private transcript study on the owner's machine pointed toward fix-introduced review findings that fall under an angle `code-writer.md` already lists, and toward dispatches that never opened a reviewer file, which can still report a clean self-review. Each such defect costs a full re-review cycle (a new staged `/code-review` round, and inside `/ready-for-review` another full cumulative pass). Why now: review-loop cost on large PRs is the owner's top cost problem, and this lever reduces fix-induced defects without dropping any review round or reviewer, so coverage does not fall.

Intended outcome: a skipped self-review is blocked or made visible at the boundary where the writer returns, using evidence a hook can observe (reviewer-file reads and a diff re-read after the last edit), plus a per-angle record of what each applied angle found.

## Approach

One fail-open hook, `send-back-skipped-self-review.sh`, catches `code-writer` at both points where its run can end: the `SubagentHandback` call it makes in auto permission mode, and the plain-text stop it makes in other modes. It reads the writer's own transcript and working tree, and sends back once a return that lacks a required reviewer-file Read, a ledger line or a diff re-read, naming each gap and asking for the complete report again. The second return goes through. In auto mode the hook prefixes it with a `SELF-REVIEW INCOMPLETE (hook-verified)` line. In other modes the one send-back told the writer to lead with that line, and the hook logs whether it did.

The writer's Self-review section becomes the ledger the engineer asked for: one line per reviewer file, naming each applied angle and what it found. The hook never judges that content. It checks that every reviewer the ledger names, and every reviewer the changed paths require, was Read in this dispatch. It finds comment lines in the text the writer's own Edit and Write calls added, not in a git diff (row 59).

### Boundary: two entry points, chosen from the probe

A harness probe on Claude Code 2.1.310, cases A to L, settles where a report leaves the writer and how each arm fails (rows 15–22, 24, 64, 65).

- In auto mode the report leaves through `SubagentHandback`. A PreToolUse deny on that call sends the writer back with the reason, and the writer acts on it (rows 15, 16). A `SubagentStop` block after a handback does nothing useful (row 17).
- In other modes the writer makes no `SubagentHandback` call. The report is the plain text it stops on, and a `SubagentStop` block sends it back (rows 15, 18).

So the hook registers on both, PreToolUse matcher `SubagentHandback` and SubagentStop matcher `code-writer`, and both run one evaluator. The SubagentStop arm owns only a dispatch whose transcript holds no `SubagentHandback` call of any kind, a denied one included (rows 20, 65). That keys the split on what the writer did, not on a permission-mode name. After a denied handback and a plain-text stop, the harness itself re-prompts the writer, and the report arrives through the next handback (row 65). One marker per writer, shared by both arms, bounds the send-back at once per report cycle.

### Fail posture: `turn-gate`, no `onFailure`

This repository's `gate` class requires `"onFailure": "block"` and a deny on malformed input (row 33). That direction is right where the guarded call is the risk. Here the guarded call is the writer's only way to deliver its report, and the matcher is a tool name that no registration field narrows to one agent type (row 37). Under `onFailure: "block"` a missing script blocked every handback and the report was lost (row 22), and that would hit every subagent type, reviewers included.

The hook therefore takes the existing `turn-gate` class, whose contract is sending an agent back instead of letting its run end, silent on every other path (row 34).

- Neither registration carries `onFailure`, and the hook never exits 2. A start failure, an exit 1 or a timeout is then a non-blocking error on both events (rows 24, 64).
- Both registrations set `"timeout": 20`, in seconds. That is above the in-script worst case of 14 seconds (row 68), so the in-script cap fires and logs first. It replaces the 600-second harness default (row 67) on a machine with no `timeout` binary.
- The hook sets `set -uo pipefail` and omits only `-e`, and its header says why. Under `-e` a failing command would end the hook with that command's own status, and a status of 2 blocks on both events (row 70). `advance-past-commit-stall.sh` omits strict mode to keep its fail-silent tail intact (row 71). The hook captures each status on the line after its command.
- Every handled failure allows and logs, except three exits that come before a log path exists: a missing `_lib.sh`, a below-floor bash (row 72) and a failed `_lib_config_dir`. The only blocking outputs are the one send-back per cycle, emitted after its marker is claimed.
- Two sites define `turn-gate`, the docstring at `test_hook_alignment.py:1836-1847` and the `claude-hook-review` skill (Design item 5). Both widen from Stop to the end of an agent's run and name both output shapes. Design item 5 and the docstring's Critical files entry account for each clause that ties the class to Stop. Stop and SubagentStop return top-level `decision: "block"`. PreToolUse `SubagentHandback` returns `hookSpecificOutput.permissionDecision`, either `deny` or `allow` with `updatedInput`. No gate count moves.
- The threat-model tier framework covers `gate` hooks only (row 35), so the hook takes no tier row. Its header states that it is a workflow check for a cooperative writer, not a security boundary.
- With no off switch (row 58), a user disables the hook by removing its two `settings.json` registrations, or by reverting this change.

### Design

1. **Hook: `claude/.claude/hooks/send-back-skipped-self-review.sh`**, whose second line is `# hook-class: turn-gate`, the line the alignment suite reads (`test_hook_alignment.py:8`). It is tracked as mode 100755 like every other `.sh` hook (row 69). Its name avoids the prefixes that force the `gate` class (row 36).
   - Above the `_lib.sh` bootstrap, in bash-3.2-parseable code, it reads all of stdin once and exits 0 unless the payload holds the quoted token `"code-writer"`. Another subagent's handback then costs one bash process and one substring match.
   - It sources `_lib.sh` or exits 0, then resolves `_lib_config_dir` or exits 0. Neither exit can log (row 72). The log is `<config-dir>/.code-writer-self-review.log`, and the marker directory is `<config-dir>/.code-writer-self-review.d`.
   - One `_lib_jq` pass reads `hook_event_name`, `agent_type` and `agent_id`. Any `_lib_jq` failure, an absent `jq` included, logs `allowed-jq-failed` and exits 0.
   - It exits 0 with no log line unless `agent_type` is exactly `code-writer`. An `agent_id` that fails `_lib_valid_session_id_component` logs `allowed-invalid-agent-id` and exits 0.
   - It runs `_lib_capped python3 "${0%/*}/evaluate-code-writer-self-review.py"` with the payload on stdin. The arguments are the validated agent id, the marker directory and the log.
   - It prints the evaluator's stdout only when the evaluator exits 0. Any other status, a cap kill or a missing `python3` included, logs `allowed-evaluator-exit=<status>` and prints nothing.
   - Each line the hook writes reads `<UTC time> <event> <verdict> agent=<agent_id>`. `<event>` is `PreToolUse`, `SubagentStop` or `-`, the agent field is `-` until the id validates, and every append is best-effort.
   - It ends with a column-0 `exit 0` and contains no `exit 2`.
2. **Evaluator: `claude/.claude/hooks/evaluate-code-writer-self-review.py`**, a co-located helper, standard library only, with pure functions and a thin `main()`.
   - **Errors.** A top-level handler logs `allowed-evaluator-error=<exception class name>`, with no path and no report text, and exits 1.
   - **Entry.** PreToolUse with `tool_name` `SubagentHandback` reads the report from `tool_input.message`. SubagentStop reads it from `last_assistant_message`. Any other event exits 0 with no output.
   - **Transcript.** SubagentStop uses `agent_transcript_path`. PreToolUse derives `<transcript_path without .jsonl>/subagents/agent-<agent_id>.jsonl` (row 19), falling back to the first `agent-<agent_id>.jsonl` below that `subagents` directory. A missing file is `allowed-no-transcript`.
   - **Tool events.** One pass decodes only the lines holding `"tool_use"` or `"tool_result"` (row 66), and skips a line that fails to decode.
     - `tool_use` blocks come only from `type: assistant` records, kept in first-seen order and deduplicated by `id`, so an `api-request-blob` copy never adds or reorders a call (row 62).
     - `tool_result` blocks come from any record other than `api-request-blob` and pair by `tool_use_id` in any order. When two carry one id, the first wins.
     - A call counts only when a paired `tool_result` exists and its `is_error` is not `true`, so an absent or `false` `is_error` is a success. A pending call, such as a Read issued in the same batch as the handback, does not count. The rule is the same for Reads, edits and Bash calls (row 63).
     - A non-empty transcript that yields no `tool_use` block is `allowed-unparsed-transcript`, never `clean`.
   - **Ownership.** SubagentStop returns `not-owned`, before any path work, when the transcript holds a `SubagentHandback` call of any kind, denied or pending included (rows 20, 65).
   - **Paths.** The `file_path` of every Edit, Write and Read call is `~`-expanded and resolved with `os.path.realpath`. Edit and Write are `code-writer`'s only editing tools (row 61).
   - **Changed paths**, with no git invocation:
     - the changed set is the resolved paths of successful Edit and Write calls that exist at hook time and sit in a repository;
     - a path sits in a repository when its own directory or an ancestor holds a `.git` entry, file or directory, so a linked worktree counts by the `.git` file at its root (row 74);
     - the walk is cached per directory, and the payload `cwd` plays no part, so edits in a linked worktree count however the session was anchored;
     - an edited path that is missing or in no repository is dropped, and the log line counts it.
   - **Added text** for the comment check comes from the writer's own successful calls on changed paths (row 59). For an Edit it is each line of `new_string` that is not a line of `old_string`. For a Write it is every line of `content`.
   - **R1 floor.** Each row is one `code-writer.md:150-160` already mandates:
     - test code (basename `test_*`, `*_test.*`, `*.test.*`, `*.spec.*` or `conftest.py`, or, for a non-`.md` file, a directory segment `tests`, `test`, `__tests__` or `spec`) requires `staff-sdet`;
     - shell, CI and IaC (`.sh`, `.bash`, `.zsh`, the consecutive segments `.github/workflows/`, a basename `Dockerfile` or `Dockerfile.*`, `.tf`, `.tfvars`) requires `staff-platform-engineer`;
     - `.md` requires `comment-discipline-reviewer`, plus `ai-instruction-and-memory-files` for a basename `CLAUDE.md` or `AGENTS.md` or the consecutive segments `.claude/rules/`;
     - a changed path whose added text includes a comment line requires `comment-discipline-reviewer`.
   - **Comment lines** start, after indentation, with one of these markers. Other extensions get no detection.
     - `#` but not `#!`, for `.sh .bash .zsh .py .rb .pl .yml .yaml .toml`, `Makefile` and `Dockerfile*`;
     - `"""` or `'''` followed by text on the same line, for `.py`, so a bare closing delimiter is code;
     - `//` or `/*`, for `.js .jsx .ts .tsx .mjs .cjs .go .rs .java .kt .c .h .cc .cpp .hpp .cs .swift .php .scala`; a bare leading `*` never counts, so `*ptr = x;` is code;
     - `--`, for `.sql`.
   - **Ledger.** The ledger is the report's text after its first exact `**Self-review:**` label, up to the first exact `**Still uncertain:**` label after it or the report's end, with `\r` stripped. Text on the label's own line counts as a ledger line.
     - A ledger line is, after indentation, an optional list marker (`-`, `*`, `+`, or digits then `.` or `)`), optional `**`, one or more backticked tokens joined by `,`, `/` or ` and `, optional `**`, then a colon.
     - A token ending `/agents/<name>.md` reads as `<name>`. A token ending `/skills/ai-instruction-and-memory-files/SKILL.md` reads as that skill.
     - Accepted names are the reviewers `code-writer.md`'s domain table names, held as an evaluator constant that a contract test pins to the table. Any other name is unrecognized.
   - **Reads.** A successful Read counts for reviewer `<name>` when its `~`-expanded path or its realpath ends `/agents/<name>.md`, or `/skills/ai-instruction-and-memory-files/SKILL.md` for that skill. A ranged Read counts, because `code-writer.md:106-111` asks for the angles section only.
   - **R2 catch-all.** When any path changed, at least one accepted ledger name has a Read.
   - **R3.** Every accepted name in the ledger has a Read.
   - **R4.** Every R1 reviewer has a ledger line and a Read.
   - **R5.** After the last successful edit to a changed path comes either a Read of a changed path, compared by realpath, or a Bash call whose `command` yields a `GIT` record with subcommand `diff`. That record comes from `parse-git-command.py`'s `build_records` (row 39), loaded with `importlib`. R5 shows that a `git diff` ran or a changed path was re-read, nothing stronger: `--stat` and a diff of another path also count.
   - **Report.** A report field that is absent or not a string reads as an empty report.
   - **Verdicts.** Missing items are deduplicated and ordered by reviewer name, with the re-read item last. Removing a marker that is already gone is not an error. Where a row below would log `clean` while successful edits exist and none is in the changed set, it logs `allowed-no-repo-path` instead, with the same output, and removes the marker as `clean` does.
     - PreToolUse, nothing missing: `clean`. Remove the marker if present.
     - PreToolUse, items missing, no marker: claim the marker, and on success print a `permissionDecision: "deny"` envelope: `sent-back`.
     - PreToolUse, items missing, marker present: remove the marker. When `message` is a string, print `permissionDecision: "allow"` with `updatedInput` equal to `tool_input`, its `message` prefixed by `SELF-REVIEW INCOMPLETE (hook-verified): <items>. Treat this diff as not self-reviewed.` and a blank line (row 16): `passed-flagged`. Otherwise print nothing: `passed-unflaggable`.
     - SubagentStop, nothing missing: `clean`, as above.
     - SubagentStop, items missing, marker present: remove the marker and print nothing. The verdict is `passed-labeled` when the report's first non-blank line starts with `SELF-REVIEW INCOMPLETE`, and `passed-unlabeled` otherwise.
     - SubagentStop, items missing, no marker, `stop_hook_active` true: print nothing: `allowed-continuation`.
     - SubagentStop, items missing, no marker: claim the marker, and on success print `{"decision": "block", "reason": …}` (row 18): `sent-back`.
   - **Claim.** All transcript and path work finishes before the claim.
     - `os.makedirs` creates the marker directory with `exist_ok=True`, then `os.mkdir` claims `<marker directory>/<agent_id>`.
     - A `FileExistsError` is `allowed-claim-raced`, any other `OSError` is `allowed-claim-failed`, and neither prints anything.
     - After a successful claim the evaluator writes and flushes the envelope. If that raises, it removes the marker and exits 1.
   - **Send-back reason.** For each item it names the file to Read with the Read tool (`~/.claude/agents/<name>.md`, or `~/.claude/skills/ai-instruction-and-memory-files/SKILL.md`), why the item is required, and the ledger line to add.
     - It states how many ledger names it did not recognize, echoing none of them, and lists the accepted names.
     - It cites `code-writer.md` § "Self-review pass — mandatory before returning".
     - It ends by asking for the complete report again in the Return format, one line, Self-review and Still uncertain included, because only the final report reaches the parent. The PreToolUse reason says to call `SubagentHandback` again with it. The SubagentStop reason says to deliver it through `SubagentHandback` when the writer has that tool, and as its final message otherwise.
     - The PreToolUse reason does not say that a second return goes through.
     - The SubagentStop reason adds: if you stop without finishing these items, start your report with the line `SELF-REVIEW INCOMPLETE: <items>`.
   - **Log.** Each verdict appends one line, `<UTC time> <event> <verdict> agent=<agent_id> calls=<n> edits=<n> changed=<n> dropped=<n> reads=<n> ledger=<n> unrecognized=<n> failed=<requirements> reviewers=<names>`.
     - `calls` counts decoded `tool_use` blocks, `edits` successful Edit and Write calls, `changed` changed paths, `dropped` edited paths left out of the changed set, `reads` reviewer-file Reads, and `ledger` parsed ledger lines.
     - `failed` lists the failing requirements among R2 to R5, so a format miss (`ledger=0` with `reads` above 0) reads differently from a skipped read.
     - `reviewers` lists accepted names only. A field not yet computed reads `-`.
     - The line holds no path, no report text and no unrecognized name.
     - Each append is best-effort, and on the send-back path it comes after the envelope is flushed. A failed append never changes the output or the exit status.
   - Envelopes are built with `json.dumps`, so no report text passes through an argument list.
3. **`code-writer.md`.**
   - Step 3 becomes "Read each reviewer file with the Read tool, by its tilde path — e.g. `~/.claude/agents/staff-backend-engineer.md`, or `~/.claude/skills/ai-instruction-and-memory-files/SKILL.md` for the one reviewer that is a skill. A Bash `cat` does not count, because a hook checks your Read calls at return." (row 30).
   - Step 5 loses its first two sentences, "Scale the pass to the change." and "A one-line edit or a single config tweak needs the baseline only." It opens instead with "Every change that edits a file gets at least one reviewer file read; choose the rows your diff touches." "A change that adds or alters logic in a domain gets that domain's reviewer read." and "When in doubt, read." stay. The prose sentence loses "beyond a whitespace or typo fix" (row 13) and keeps the rest.
   - A new step 8 reads "After your last fix, re-read the hunks you changed (`git diff -- <paths>`, or `Read` for a file you created) before returning, because a hook checks for this re-read; fix what it shows, then re-read again." (row 31).
   - Steps 3 and 8 name tool verbs because the hook sees only tool calls: R3 credits a Read call, and R5 a Read of a changed path or a `git diff` Bash call (row 30).
   - The domain table gains a last row, "Any other file (non-shell scripts, libraries, CLIs, other config not covered above)", mapped to `staff-backend-engineer`. Shell scripts and CI config stay with the CI/CD row, and every edited file now has a named reviewer for R2.
   - The Return format's Self-review bullet asks for one ledger line per reviewer file Read: the reviewer's backticked name, a colon, then each applied angle and its finding. A `<!-- HOOK_TEST_FIXTURE: code-writer-self-review-ledger -->` fenced example holds the `**Self-review:**` label and one ledger line naming a concrete reviewer.
   - One sentence there reads "A hook sends back a return whose ledger names a reviewer you did not Read, or omits one your changed paths require." It gives no count of returns.
   - The Return format's lead gains "A dispatch's own return fields add to this format and never replace the `**Self-review:**` ledger.", so a prompt that lists its own fields, as `respond-pr` does, still gets the ledger.
   - The bullet's "If the change was trivial enough to skip the deep pass, say so and why." sentence goes (row 7). Its per-test sentence stays (row 32).
4. **Ignore entries.** `.gitignore` gains `claude/.claude/.code-writer-self-review.d/` and `claude/.claude/.code-writer-self-review.log` beside the other hook state (row 42).
5. **`claude-hook-review` skill.** `plugins/claude-hook-review/skills/claude-hook-review/SKILL.md` defines the class this hook takes, so it widens with the docstring (Fail posture). The edits stay in place, and the file (174 lines) stays under the 200-line limit `check-skill-length.sh:11` sets.
   - Line 116's `turn-gate` entry, clause by clause:
     - "fires on `Stop` and may block the *turn* from ending (`{"decision":"block","reason":...}`) rather than a tool call from running" is edited. It says the class fires where an agent's run ends, on `Stop`, `SubagentStop` or PreToolUse `SubagentHandback`, and sends the agent back rather than blocking a tool call, with an output shape that follows its event (see §9). The inline Stop envelope goes, because line 153 now carries both shapes.
     - "not a Stop hook mislabeled `gate`" is edited to "not an end-of-run hook mislabeled `gate`".
     - "the emission shape differs (see §9)" goes, because the PreToolUse `SubagentHandback` arm emits the same `hookSpecificOutput.permissionDecision` envelope as `gate`.
     - The `GATE_HOOKS` clause stands, and after the colon it is the entry's remaining reason. The exclusion and its hand-written jq-absent cases hold on every event.
   - Line 153's emission check keys the shape on the event: top-level `decision: "block"` on `Stop` and `SubagentStop`, and `hookSpecificOutput.permissionDecision` on PreToolUse `SubagentHandback`, either `deny` or `allow` with `updatedInput`.
     - Its "not nested under `hookSpecificOutput`" aside stays with the `Stop` and `SubagentStop` shape. Its clause on the harness routing on exact literal keys stands for both shapes.
     - Its last sentence, "These are two distinct emission contracts; do not test or review one as though it were the other.", is edited to "`gate` and `turn-gate` are distinct contracts, and `turn-gate`'s shape follows its event; do not test or review one as though it were the other." The second half stands, because the two classes still differ in fail posture.
   - Line 154's "matcher-less `Stop`" describes the `Stop` event itself, not the class, and stands.
   - Line 156's "stuck turn" becomes "a stuck turn or a lost report". Its jq-absent and missing-tool cases stay required, and the hook's tests carry both.
   - Line 120 stays as written, because it governs moving an existing hook between classes. The commit message still says why the new hook is `turn-gate` rather than `gate`.
   - `plugins/claude-hook-review/.claude-plugin/plugin.json` raises `version` from 2.5.0 to 2.6.0, a minor bump: the `turn-gate` contract widens and nothing is reversed. `require-plugin-version-bump.sh` blocks the commit without a raise, and `plugin-semver:plugin-semver` confirms the bump class. Critical files' `CHANGELOG.md` and plugin entries carry the consumer notice.
6. **Supersession note.** `docs/design-decisions/ready-for-review-fix-loop-convergence.md` § "The prose row" still describes step 5's whitespace-or-typo exemption, which row 13 removes. Per `.claude/rules/design-decisions.md`'s Supersession bullet, a `**Superseded in part by [code-writer-self-review-send-back.md](code-writer-self-review-send-back.md) (<provenance date>):**` note joins the two notes under its provenance line. `<provenance date>` is the new decision file's own provenance date, and the dispatch prompt states that one date for both. It says that every added or rewritten prose line now gets the prose-row read. The section's own text stays as written.

### Adversarial review of the Step 4 choices

**Choice 1, send it back once: held across both entry points, and settled for other modes by row 52.**

- Attack: a send-back on the writer's only exit can trap it. Held: the marker is claimed before the deny or block, which is emitted only after the claim succeeds, so the cycle's second return always passes. A failure after the claim either delivers the envelope or removes the marker. The SubagentStop arm never blocks a stop that `stop_hook_active` marks as a continuation, and every failure allows (Fail posture).
- Attack: an auto-mode writer that stops in plain text between handbacks draws one send-back from each arm. Held: the SubagentStop arm does not own a dispatch that holds any `SubagentHandback` call, the two arms share the marker, and a test crosses the arms in each direction.
- Attack: a writer answers the deny in plain text instead of handing back, and its report is lost. Held: that stop is not owned, so the marker survives. The harness re-prompts the writer, and the next handback finds the marker and passes flagged (row 65).
- Attack: the writer answers a send-back with a fragment, such as "added the ledger line, done", and the parent loses the changed-file list. Held: both reasons ask for the complete report in the Return format.
- Attack: a hasty writer re-hands-back at once to get through. Held in auto mode: the deny does not disclose the pass-through, and the hook, not the writer, writes the INCOMPLETE line. In other modes the reason offers the label as the only alternative to finishing, so a hasty writer produces the same parent-visible line.
- Attack: other modes cannot carry a hook-authored line. Accepted with a residual: a writer that ignores the reason's label instruction reaches the parent unflagged, and the log records `passed-unlabeled`. Settled: the engineer kept the send-back at once, with the writer authoring the label (row 52).
- Attack: visibility only, never blocking, saves a writer turn. Set aside: the parent then fixes or re-dispatches outside the writer's warm context, the round trip the Ask targets. Blocking until satisfied is set aside too, for its unbounded false-positive cost.
- Attack: on the `/code-review` Fix route, `review-orchestrator` returns only its listed items, so the INCOMPLETE line never reaches the main session. Accepted: that route's next round reviews the fix regardless (row 44), so no coverage is lost (Out of scope).

**Choice 2, path floor plus writer adds: held, strengthened inside the choice.**

- Attack: "writer adds" is an honor system for logic that matches no floor row. Strengthened: R2 requires at least one Read-backed ledger line whenever a path changed, and the domain table's new last row names `staff-backend-engineer` for any file no other row covers. The floor stays path-derived, and the writer still picks the reviewer.
- Attack: the writer claims a reviewer it never opened and still reports a clean self-review. Closed by R3. A Read still pending in the handback's own batch does not count (row 63).
- Attack: false-positive reads. Every R1 row is one the domain table already mandates, and one read costs 147–260 lines (row 47). Comment lines come from the text the writer's own edits added, so an Edit's unchanged context lines never count, and the detector reads neither a dereference nor a bare closing delimiter as a comment. Step 5's typo exemption goes with the no-skip answer (row 13). Scratch files in no repository, and files deleted before the hook ran, drop out of the changed-path set.
- Accepted gaps, listed in the hook header's Known gaps:
  - a Write over an existing file counts all its comment lines as added, and a comment one Edit adds and a later Edit removes still counts;
  - a cap kill between the claim and the flush spends the marker, so that cycle's second return passes flagged with no send-back;
  - trailing comments, unconventional test paths, prose in `.rst` or `.txt`, and comments in `.css`, `.scss`, `.vue`, `.svelte` or `.html` miss R1 and fall to R2;
  - edits made through Bash (`sed -i`, heredocs) are not seen as edits;
  - a Bash `cat` of a reviewer file draws one send-back, as `require-routing-read.sh:71` already does for its own file;
  - a reverted edit or an edit to an ignored path still draws the reads;
  - a writer that never returns after a send-back leaves one empty marker directory, and nothing sweeps it.

**Choice 3, declared skip: replaced by no skip on the engineer's answer (row 7).**

- No size threshold removes the risk. Review-round fixes go to `code-writer` regardless of size (row 45), and a small fix diff can introduce a defect (row 46), so a size skip exempts some of the dispatches this plan targets. The costs are lopsided: a missed defect costs a full re-review cycle (Context), and the check costs one reviewer read of 147–260 lines (row 47).
- `code-writer.md:113-114` already sends a change that adds or alters logic to its domain reviewer, and the hook cannot tell logic from config, so a skip hands the call back to the component that failed.
- The evaluator carries no size waiver. Every dispatch that changes a file re-reads its diff (R5) and Reads at least one reviewer file (R2), plus its R1 rows.

**R5, the diff re-read: held, with the order fixed.** Step 7 of `code-writer.md` fixes issues after step 6's re-read and asks for no further re-read (row 31), so a check keyed on the last edit would fail a writer that followed the file. A new step 8 asks for a re-read after the last fix. R5 keys on the last edit to a changed path, so a scratch file written after the diff does not trip it.

### Alternatives set aside

- **`gate` class with `onFailure: "block"`.** A start failure blocks every subagent's report (row 22). An alignment-suite exemption list for one fail-open gate would patch over the class contract that `turn-gate` already names.
- **A PostToolUse recorder.** It adds a process on every Read, Edit, Write and Bash call in every session, a state directory, and ordering races within a parallel batch, to supply what the transcript already holds in order (row 20).
- **A send-back count read from the transcript instead of a marker.** A transcript that lagged behind a deny would re-deny on the only exit (row 23). The marker has no freshness dependency.
- **`permission_mode` as the arm discriminator.** It ties the split to mode names seen on one version (row 27). The transcript's `SubagentHandback` calls are the behavior itself.
- **A parent-side PostToolUse `Agent` hook that injects the INCOMPLETE line in other modes** (row 25). A background dispatch's Agent call returns at launch, so it covers foreground dispatches only, and it must parse the agent id out of undocumented result text.
- **Agent-frontmatter `hooks:` in `code-writer.md`.** They scope the hook to the writer, but their `SubagentHandback` behavior is unprobed, frontmatter hooks are skipped in untrusted folders (docs summary), and they sit outside the `settings.json` wiring the alignment suite checks. Under the fail-open posture their narrower blast radius buys nothing.
- **A ledger file.** Subagents are barred from `review-ledger.sh` state (`enforce-marker-script-shape.sh:438-447`), a file in the worktree shows in `git status`, and the parent reads the return.
- **`SubagentStart` injection of reviewer angles.** The payload carries no prompt (row 49) and the diff does not exist yet.
- **Prose, an advisory nudge, or a parent-side check.** See M1.
- **Added comment lines from a `git diff` of the changed paths.** It needed a hunk parser, untracked-file reads, and encoding and binary cases, for text the writer's own Edit and Write inputs already hold (row 59).
- **git as the changed-path authority.**
  - A `git status` pass over the edited paths dropped only reverted and ignored-path edits, and cost a NUL-record parser, a git-failure verdict and an invocation-count test.
  - A toplevel taken from the payload `cwd` misses edits in a linked worktree the session is not anchored in.
  - A `git rev-parse` per distinct directory answers the walk-up's question with a subprocess per directory, a cap and a cap verdict. Where the two disagree (row 75), the walk-up draws the reads, except for a repository git finds only through an environment override.
- **`last_assistant_message` as the SubagentStop ownership signal.** Case L showed it non-empty on the first stop after a denied handback in auto mode (row 65). The arm would then own that dispatch and consume the deny's marker, and the next handback would draw a second send-back.
- **An off switch, as a sentinel file or a config key.** The engineer declined one (row 58).

### Assumption ledger

**Root.** `code-writer`'s mandatory self-review leaves no evidence any hook checks, so a writer can skip the reviewer-file reads, report a clean self-review, and hand the parent a fix-induced defect that costs a full re-review cycle (Context).

**Givens.**

- G1. Claude Code's hook events, payload fields and decision schema are imposed by the vendor; this plan can only use them.
- G2. The supported Claude Code floor is 2.1.295 [verified: README.md:113]. The repo's Requirements policy owns it, and moving it is a decision outside this plan. The probe ran on 2.1.310 only (row 27).
- G3. The study behind Context is private. `CLAUDE.md`'s "Redact private-project-identifying content" section, which the repository owner sets for every public artifact, bars its figures here, so this plan describes the evidence without quantifying it.
- G4. A session's permission mode is the user's choice, outside this plan's reach, so the design covers every return path the probe observed rather than one mode's.

**Rows.**

1. The engineer chose to send a return without the self-review back once. [engineer-verified: "Send it back once (Recommended)"]
2. That option's description (block the first return naming the unread reviewer files, let the second through carrying a visible "self-review incomplete" line the parent sees, never loop) is the session's text, not the engineer's. [unverified]
3. The engineer chose a path floor plus writer-named reviewers. [engineer-verified: "Path floor + writer adds (Recommended)"]
4. That option's description (a mechanical floor from edited paths, such as tests to `staff-sdet`, `.md` or comment edits to `comment-discipline-reviewer`, `.sh` to `staff-platform-engineer`, with further domains named by the writer and verified by the hook) is the session's text. [unverified]
5. The engineer first chose a declared, parent-visible skip for small changes. [engineer-verified: "Declared skip, shown to parent (Recommended)"] Row 7 overrides it.
6. That option's description (the skip allowed only under a small diff-size threshold, only when declared, visible to the parent, never for prose) is the session's text. [unverified]
7. After the adversarial review, the engineer chose no size-based skip, overriding row 5. [engineer-verified: "No skip (Recommended)"]
8. That option's description (every dispatch re-reads its diff and reads at least one reviewer file, plus path-floor files) is the session's text, written from this plan's recommendation, not the engineer's. [unverified]
9. The engineer asked for each choice to be attacked before adoption. [engineer-verified: "those choices sound reasonable, but have the architect adversarially review them."]
10. The engineer asked for a record of what the writer checked. [engineer-verified: "maybe have it write a ledger for what it checked"]
11. The engineer agreed to run `/plan-it` on enforced self-review as the first fix. [engineer-verified: "Yep, I totally agree."]
12. The engineer set a standing constraint that review coverage cannot drop and that human PR review is not a backstop. [unverified: relayed in the dispatching session's words, with no verbatim quote]
13. The engineer chose that row 7's no-skip answer also removes `code-writer.md` step 5's whitespace-or-typo exemption from the prose-row read. [engineer-verified: "Yes, no exemption (Recommended)"]
14. Withdrawn with the off switch (row 58).
15. In auto mode a subagent returns through `SubagentHandback`, and the parent's Agent tool_result only points at the delivered report. In `acceptEdits` mode it makes no `SubagentHandback` call and ends in plain text, `SubagentStop` carries that text as `last_assistant_message`, and the parent receives it as a "[Subagent hand-back]" tool_result. [verified: the harness probe, Claude Code 2.1.310, cases A, B and E]
16. In auto mode a PreToolUse deny on `SubagentHandback` reaches the subagent, recorded in its transcript as an `is_error` tool_result holding the reason, and the subagent did what the reason asked and handed back again. On that second call, `permissionDecision: "allow"` with `updatedInput` (the whole `tool_input`, `message` prefixed) was honored, and the parent's report carried the prefix. [verified: the probe, case C, and its transcript snapshots]
17. A `SubagentStop` block after a delivered handback fired, but the subagent did no further work and the parent got the original report. [verified: the probe, case D]
18. Outside auto mode a `SubagentStop` block made the subagent continue and re-report. The second stop carried `stop_hook_active: true`, the parent got the updated report, and the block reason appears in the agent transcript as a system message. [verified: the probe, case F, and its transcript snapshot]
19. `agent_id` is identical across PreToolUse, PostToolUse, SubagentStart and SubagentStop for a subagent's events, and `agent_type` is present on all of them. `SubagentStop` carries `agent_transcript_path` and PreToolUse does not, and `<transcript_path without .jsonl>/subagents/agent-<agent_id>.jsonl` existed in every case. The observed ids are `a` plus 16 hex characters, inside `_lib_valid_session_id_component`'s character class. [verified: the probe, all seven cases; _lib.sh:2852]
20. At the PreToolUse `SubagentHandback` hook and at `SubagentStop`, the agent transcript already held every earlier tool call. `tool_use` blocks carry `id`, `name` and `input`, `tool_result` blocks carry `tool_use_id` and `is_error`, and one `tool_use` id can recur across records. [verified: the probe's transcript snapshots, cases A, C and F]
21. `SubagentStop`'s `last_assistant_message` was absent after a delivered handback and non-empty after a plain-text ending. [verified: the probe, cases A to G]
22. A missing script under `onFailure: "block"` on PreToolUse `SubagentHandback` blocked every handback (exit 127), the harness re-stopped the subagent itself, and the parent got "no report was delivered". The matcher is a tool name, so this reaches every subagent type. [verified: the probe, case G; docs/hooks.md:409-411]
23. The transcript stays current on long dispatches: every earlier tool call and its result are written before either hook fires. [unverified: the probe's runs made four or five tool calls] Row 66 covers parse time.
24. With no `onFailure`, a PreToolUse `SubagentHandback` hook that cannot start (a missing script) or exits non-zero other than 2 (exit 1) lets the handback through on the first call, and the parent receives the report. [verified: the harness probe, Claude Code 2.1.310, cases H and I] One that times out is a non-blocking error, and the handback proceeds. [verified: _lib.sh:89-93 quotes the hooks reference's Timeouts section]
25. `onFailure` has no effect on `SubagentStop` hooks, and the hooks reference's route for injecting context into the parent after a subagent returns is a PostToolUse hook on `Agent`. [unverified: the dispatching session's reading of code.claude.com/docs/en/hooks.md] Row 64 verifies the matcher on the agent's name, which this row also claimed.
26. A background dispatch outside auto mode, a nested dispatch at spawn depth 2, and a SendMessage continuation behave like the probed cases: the `SubagentStop` block works in the first, the derived transcript path resolves in the second, and the third's first stop carries `stop_hook_active` false. [unverified: the probe ran depth-1 dispatches, and outside auto mode foreground only; the design doc lists these paths as unverified, with the outcome log as the instrument]
27. Claude Code 2.1.295 through 2.1.309 behave as 2.1.310 did in rows 15 to 22. [unverified]
28. Removed: its only source was private, and no mechanism anchored it.
29. `code-writer.md` mandates the reviewer reads, maps domains to reviewer files, and asks for a per-domain Self-review line, and no hook checks any of it. [verified: code-writer.md:86-173; no `SubagentHandback` or `SubagentStop` registration in claude/.claude/settings.json]
30. Step 3 gives only the `~/.claude/agents/<name>.md` form, while the table also names `ai-instruction-and-memory-files`, which is a skill. [verified: code-writer.md:104-105, 160; claude-skills/skills/ai-instruction-and-memory-files/SKILL.md exists]
31. Step 5 exempts a whitespace or typo fix from the prose-row read, and step 7 fixes issues after step 6's re-read without asking for another re-read. [verified: code-writer.md:112-117, 118-148]
32. The Self-review bullet carries the per-test sentence about red-before-green runs, and no test pins it. [verified: code-writer.md:168-173; round-1 staff-backend-engineer R9's consumer search]
33. A `gate` handler must sit under PreToolUse with `"onFailure": "block"` and must not be async, and a gate must deny malformed, empty and non-object input and a missing `_lib.sh`. [verified: test_hook_alignment.py:3531-3540, 2926-2987]
34. `turn-gate` is the class that blocks an agent's run from ending rather than a tool call from running, and the PreToolUse-specific gate checks do not apply to it. The suite checks only the class value, ties no event to `turn-gate`, and requires PreToolUse wiring of `gate` hooks only. The class's one hook is silent on every non-firing path, and a hook may register under two events. [verified: test_hook_alignment.py:1831-1855, 471-487, 3506-3543; advance-past-commit-stall.sh:9-14; docs/hooks.md:177]
35. The threat-model tier framework covers `gate` hooks only, and the tier table lists exactly the gate hooks. [verified: docs/hooks.md:7; test_hook_alignment.py:2152-2165]
36. File names starting `deny-`, `require-`, `enforce-`, `guard-` or `block-`, or matching `check-*-guard.sh`, force the `gate` class. [verified: test_hook_alignment.py:143-150]
37. A `SubagentStart` group can match an agent name, but a PreToolUse matcher names a tool and an `if` filter matches tool input in permission-rule syntax, so neither narrows a `SubagentHandback` registration to one agent type. [verified: settings.json:201-209, 285-306]
38. Withdrawn with the off switch (row 58).
39. `parse-git-command.py`'s `build_records` turns a command into `GIT` records carrying the subcommand, keeping a quoted argument as one word. [verified: parse-git-command.py:2-60, 167-217, 260-262]
40. Hooks hand parsing to co-located Python helpers that tests load through `importlib`, and logic with its own control flow and data structures belongs in a `.py` file. [verified: require-worktree-for-git-writes.sh:284, ask-new-dependency-disclosure.sh:204, test_ask_new_dependency_disclosure.py:42-44, claude/.claude/rules/shell-script-conventions.md]
41. A directory claimed with `mkdir` is this repository's atomic one-shot marker, with an append-only outcome log beside it. [verified: nudge-long-turn-subagent.sh:294-296, 343-346]
42. `.gitignore` lists hook runtime state under `claude/.claude/` one path at a time, and `nudge-long-turn-subagent.sh`'s log and marker directory, the precedent row 41 copies, are not listed. [verified: .gitignore:148-167; no `long-turn-nudge` entry in .gitignore]
43. `code-writer.md` re-reads a modified file with `git diff -- <paths>` and never `git diff HEAD`, which would pull in a prior round's staged work. [verified: code-writer.md:97-101]
44. `review-orchestrator` returns only its listed items, and its next round reviews any fix it dispatched. [verified: review-orchestrator.md:27-30, 46-61]
45. Review-round fixes go to `code-writer` regardless of size. [verified: subagent-delegation/SKILL.md:162-171, "Two further carve-outs, neither size-based"]
46. A fix-induced defect can sit in a small fix diff. [unverified: no defect diff sizes are cited]
47. One floor read costs one reviewer file of 147–260 lines. [verified: line counts of claude/.claude/agents/staff-*.md, ciso-reviewer.md and comment-discipline-reviewer.md]
48. `_lib_capped` caps a command at 5 seconds with a 2-second kill grace and returns non-zero when the cap fires, and `python3` 3.11 or newer is an install-time requirement. [verified: _lib.sh:43-52, 129-139; README.md:107, 111]
49. The `SubagentStart` payload carries no prompt. [verified: docs/hooks.md:197]
50. `helpers.extract_skill_command` returns the fenced block tagged `HOOK_TEST_FIXTURE` in a markdown file, and `helpers.heading_texts` lists a file's headings. [verified: claude/.claude/tests/helpers.py:120-150, 174]
51. New files under `~/.claude/hooks` land on `git pull` when that directory links into the checkout, and need `./install.sh` on the legacy real-directory layout. [verified: README.md:95-97; docs/hooks.md:411]
52. Outside auto mode, the engineer chose to keep the send-back at once, with the writer authoring the incomplete label. [engineer-verified: "Keep once, writer labels (Recommended)"]
53. That option's description (the one send-back tells the writer to start its report with `SELF-REVIEW INCOMPLETE:` if it cannot finish, the second return always goes through, and the hook logs whether the label was present) is the session's text, not the engineer's. [unverified]
54. Row 13's option description (every edit to a `.md` file or a comment gets the prose-reviewer read, typo fixes included) is the session's text, not the engineer's. [unverified]
55. In round-2 `/plan-review`'s scope question, the engineer kept the plain-logic reviewer row, the outcome log and the design-decision doc in scope. [engineer-verified: "Plain-logic reviewer row", "Outcome log", "Design-decision doc"]
56. The off-switch key is cut, inferred from its unselected option in that question. [unverified] Row 58 carries the engineer's own words.
57. That question's option descriptions are the session's text, not the engineer's. [unverified]
58. The engineer declined an opt-out for this hook, and said any opt-out would be a config key, not a sentinel file. [engineer-verified: "We’ve made other changes to the harness without opt out I don’t understand why you’re insistent on making this one opt out and if we were to make it opt out, we would use a config key, not a sentinel file."]
59. On the comment-line check, the engineer chose to read the added text from the writer's Edit inputs. [engineer-verified: "Read from Edit inputs (Recommended)"]
60. That option's description is the session's text, not the engineer's: keep the check, take the added text from the writer's own Edit and Write calls in its transcript (`new_string`, `content`) instead of git, drop the git diff, the hunk parser, the untracked-file reads and their tests, and keep the per-language comment table as a pure function. [unverified]
61. `code-writer`'s tools are Read, Edit, Write, Bash, Grep and Glob, so it cannot call MultiEdit or NotebookEdit. [verified: code-writer.md:4]
62. In the agent transcript, `tool_use` blocks sit in `type: assistant` records and recur in `api-request-blob` records with a different input key set. Edit inputs carry `file_path`, `old_string` and `new_string`, and Write inputs carry `content`. A parallel batch's results can land out of call order. `is_error` is absent on most successes, `false` on Bash and `true` on a denied handback. The in-flight `SubagentHandback` call is absent from the transcript at its own PreToolUse. [unverified here: round-2 staff-sdet R1 and R2 read it from the probe's case A and C snapshots, not reread] Blocks sit under `message.content[]` in `assistant`, `user` and `api-request-blob` records. Each assistant record holds one content block, carrying an `apiBlockIndex` key. `tool_result` blocks sit in `type: user` records. A blob record's keys are `agentId,hash,message,sessionId,type`. `is_error` is absent on some results and present on others. [verified: round-4 staff-sdet, keys-only check of a probe transcript; not reread here] The fixture is hand-authored from these shapes, and Verification step 2 runs the real harness.
63. A Read issued in the same message as the handback can still lack its result when the PreToolUse hook fires, so crediting a pending call would credit a Read that never ran. [unverified: round-2 staff-sdet R4's inference; rows 20 and 23 bound the false send-back that not crediting risks]
64. In `acceptEdits` mode, a SubagentStop group matched on the agent's name fired for that agent, and its logger saw the `agent_type`. With no `onFailure`, a hook in that group that could not start (exit 127) or exited 1 was a non-blocking error: the stop completed and the parent received the plain-text report. [verified: the harness probe, Claude Code 2.1.310, cases J and K]
65. In auto mode, after a denied handback whose reason told the writer to stop in plain text, the writer did so. That first SubagentStop carried `stop_hook_active` false and a non-empty `last_assistant_message`. With no stop hook blocking, the harness re-prompted the writer itself, a later SubagentStop carried `stop_hook_active` true, the writer called `SubagentHandback` again, and the parent received that report. Case G also showed the harness re-prompting on its own. [verified: the harness probe, Claude Code 2.1.310, cases G and L]
66. A proxy of the evaluator's transcript pass, which keeps the lines holding `"tool_use"` or `"tool_result"` and decodes each with `json.loads`, finished well under the 5-second cap on the largest local subagent transcripts, by bytes and by tool-call count. [verified: the dispatching session's proxy measurement on the owner's local transcripts, figures withheld under G3]
67. A hook registration's `timeout` is in seconds and defaults to 600, and three existing registrations set `"timeout": 10`. [verified: docs/hooks.md:410, quoting the hooks reference's "Common fields" table, `timeout` row; claude/.claude/settings.json:178, 207, 218]
68. `_lib_jq` and `_lib_capped` each cap their command at 5 seconds plus a 2-second kill grace, so the hook's two capped calls take at most 14 seconds, and with neither `timeout` nor `gtimeout` on `PATH` both run uncapped. [verified: _lib.sh:39-41, 50-52, 129-139]
69. Every tracked `.sh` hook is mode 100755, the `.py` helpers are 100644, and no alignment test pins the mode. [unverified here: round-2 staff-backend-engineer R7's `git ls-files -s` and grep, not rerun]
70. A status of 2 escaping the hook would block on both registered events, and `python3` exits 2 when it cannot open its script. [unverified here: round-2 staff-platform-engineer R1] The evaluator-absent hook case checks the second half.
71. `advance-past-commit-stall.sh` omits `set -euo pipefail` deliberately, so that expected non-zero exits cannot cut its fail-silent tail short. [verified: advance-past-commit-stall.sh:16-20]
72. A missing `_lib.sh` ends a hook at its source guard. So does a bash below the floor, because `_config.sh` and then `_lib.sh` return 1 while sourcing. Either way `_lib_config_dir` never exists, so no log line is possible. [verified: _config.sh:40-68; _lib.sh:25-27]
73. `GATE_HOOKS`, which drives the alignment suite's auto-parametrized behavior tests, holds `gate`-class hooks only, and `helpers.build_path_without` builds a `PATH` lacking one binary. [verified: test_hook_alignment.py:162, 470; claude/.claude/tests/helpers.py:2502]
74. A linked worktree's root holds a `.git` file pointing at its gitdir, where a main checkout holds a `.git` directory, and `_worktree-lib.sh` already treats a `.git` entry of either kind as marking a checkout. [verified: this plan's own worktree root; claude/.claude/scripts/_worktree-lib.sh:475]
75. git finds a repository by walking up from a directory to the nearest `.git` entry, unless an environment override such as `GIT_DIR` or `GIT_CEILING_DIRECTORIES`, or an ownership check, changes the answer. [unverified: git's discovery rules were not reread here] An override that hides a repository from git makes the walk-up count a path git would not. An override that defines a repository with no `.git` entry makes the walk-up drop its edits, a setup this plan has no evidence a `code-writer` dispatch uses.
76. `pytest.mark.timing` marks a test that asserts a wall-clock bound, and CI runs those serially after the parallel pass. [verified: pyproject.toml:31; staff-platform-engineer round 3 R2 for .github/workflows/tests.yml:201, 208, not reread]
77. The `claude-hook-review` skill defines `turn-gate` as firing on `Stop` with a top-level `decision: "block"`, its review checklist requires jq-absent and missing-tool cases that allow silently, and its plugin is at version 2.5.0. [verified: plugins/claude-hook-review/skills/claude-hook-review/SKILL.md:116, 153, 156; plugins/claude-hook-review/.claude-plugin/plugin.json:4]
78. `respond-pr` dispatches one `code-writer` for its whole `FIXED` set, with a prompt that lists its own return fields and names no Self-review. [verified: claude-skills/skills/respond-pr/SKILL.md:55-59]
79. The engineer chose to add no `docs/hooks.md` sentence on disabling the hook. [engineer-verified: "No doc sentence (Recommended)"]
80. The engineer chose to keep the below-3.11 interpreter guard and the 30-day marker sweep cut. [engineer-verified: "Keep both cut (Recommended)"]
81. The option descriptions behind rows 79 and 80 are the session's text, not the engineer's. Those descriptions say that nothing about disabling the hook goes in `docs/hooks.md`, the CHANGELOG or the design doc, that an old `python3` exits 1, which the hook logs and allows, and that a writer that never returns leaves one empty marker directory, noted in Known gaps. [unverified]

**Mechanisms.**

- M1. Two entry points cover every return path the probe observed. anchors: root, row15, row16, row17, row18. Lighter primitives checked:
  - the PreToolUse `SubagentHandback` arm alone misses every dispatch outside auto mode (row 15);
  - a `SubagentStop` arm alone does nothing useful after a handback (row 17);
  - prose in `code-writer.md` is already there and is skipped (row 29), an advisory nudge never blocks, and a parent-side check of the Self-review line cannot see Reads.
- M2. The `turn-gate` class with no `onFailure`, a registration `timeout` above the in-script caps, and no `set -e` keep a hook failure from costing any subagent its report. anchors: row22, row24, row33, row34, row37, row64, row67, row68, row70, row71. Lighter primitives checked: an in-script guard cannot run when the script is missing, and neither a matcher nor an `if` filter narrows the PreToolUse registration to one agent type (row 37).
- M3. The writer's own transcript supplies Read, edit and Bash evidence in call order, plus the text each edit added, with no per-call process. anchors: row19, row20, row23, row62, row63, row66.
- M4. The shared marker, claimed before any send-back, bounds the send-back at once per cycle, and `stop_hook_active` keeps the SubagentStop arm off another hook's continuation. anchors: row1, row2, row18, row41, row52.
- M5. The SubagentStop arm owns only dispatches with no `SubagentHandback` call of any kind. anchors: row17, row20, row21, row65.
- M6. The hook-authored prefix in auto mode, and the reason-requested label elsewhere, make the incomplete state visible to the parent. anchors: row2, row16, row21, row25, row52. Lighter primitives checked: a writer-authored label in auto mode gives up the hook authorship available there, and a parent-side `Agent` hook covers foreground dispatches only.
- M7. R1 to R4 enforce the floor, the catch-all, and the Read behind every claim. anchors: row3, row4, row29, row30, row47.
- M8. R5 and the new step 8 enforce a re-read after the last fix. anchors: root, row31, row39, row43.
- M9. The ledger lives in the return, a contract test pins its grammar to the evaluator, and the Return format says a dispatch's own fields never replace it. anchors: row10, row32, row50, row78.
- M10. The evaluator has no size waiver, so R1, R2, R4 and R5 apply to every dispatch that changes a file, a one-line change included. anchors: row7, row13, row45, row46.
- M11. The outcome log records every run that reaches the config dir, an unparsed transcript, a ledger format miss and edits in no repository included, so each unprobed path shows. A missing script, a missing `_lib.sh` and a below-floor bash leave no line. anchors: row23, row26, row27, row72.
- M12. Both class definitions, the test docstring and the `claude-hook-review` skill, widen together, so the skill's review of this hook does not reopen the class. anchors: row34, row77.
- M13. A `.git` walk-up from each edited path's own directory decides repository membership with no git invocation, so a linked worktree counts wherever the session is anchored. anchors: row74, row75. The payload `cwd`'s toplevel misses linked-worktree edits, and a `rev-parse` per directory adds subprocesses and a cap (Alternatives set aside).
- M14. The comment check reads the text the writer's own Edit and Write calls added, so it needs no diff parser, untracked-file read or encoding handling. anchors: row59, row61, row62.

## Critical files

**Dispatch split.** Verification step 0 already ran and changed no repository file. Phase 1 is one `code-writer` dispatch, verified with `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py`. It is not split: the evaluator's ledger parser and `code-writer.md`'s ledger grammar are one contract, and the hook, the evaluator and `settings.json` change together.

**Phase 1, the hook and everything it touches.**

- `claude/.claude/hooks/send-back-skipped-self-review.sh` (new): Design item 1.
  - Reuse `_lib_jq`, `_lib_config_dir`, `_lib_valid_session_id_component` and `_lib_capped`.
  - Its header carries:
    - the class line on line 2;
    - the line "# Workflow check for a cooperative writer that skips its self-review; not a security boundary.";
    - the line "# Dispatch surfaces: PreToolUse matcher SubagentHandback and SubagentStop matcher code-writer. No matcher narrows the PreToolUse arm to one agent type, so the script checks agent_type itself.";
    - the line "# Fail posture: fail-open. Every failure allows, and only the once-per-cycle send-back blocks.";
    - the line "# set -e deliberately omitted: an aborting command's own exit status could be 2, which blocks the writer's return.";
    - a pointer to the design-decision file below, and Known gaps.
  - It sources `_lib.sh` and locates the evaluator with `${0%/*}`, not `$(dirname "$0")`.
  - The dispatch prompt tells the writer to `chmod +x` the file, so it is tracked as 100755 (row 69).
- `claude/.claude/hooks/evaluate-code-writer-self-review.py` (new): Design item 2. Reuse `parse-git-command.py`'s `build_records` through `importlib`.
- `claude/.claude/settings.json`: a PreToolUse group with matcher `SubagentHandback`, and a new `SubagentStop` key with one group matching `code-writer`, each holding `~/.claude/hooks/send-back-skipped-self-review.sh` with `"timeout": 20` (Fail posture) and no `onFailure`, `async`, `if` or `statusMessage`.
- `claude/.claude/agents/code-writer.md`: Design item 3.
- `claude/.claude/hooks/tests/fixtures/code-writer-self-review/agent-transcript.jsonl` (new), one small file hand-authored from row 62's shapes, with placeholder paths and message text. No byte of a probe snapshot enters the repository, because the snapshots carry private instruction-file text, and the dispatch prompt names no snapshot path.
  - It holds `type: assistant` records with `tool_use` blocks, records with `tool_result` blocks, and an `api-request-blob` copy of a `tool_use` with a different key set. The copied call is one whose pinned key path would change if the blob's block were read.
  - Its calls include at least one Edit, one Write and one Bash `git diff`.
  - It holds a parallel batch whose results land out of call order, and `is_error` absent, `false` and `true`, the last on a denied `SubagentHandback`.
  - It holds one reviewer Read by absolute path and one by tilde path, and ends with a delivered `SubagentHandback` and its result.
  - The test derives the PreToolUse variant by dropping the final handback's records, and the plain-text variant by dropping every handback's records. The whole file is the SubagentStop-after-handback variant.
- `claude/.claude/hooks/tests/test_send_back_skipped_self_review.py` (new), tiered like `test_ask_new_dependency_disclosure.py`. Every table row states its expected outcome.
  - Pure functions, loaded through `importlib`, as parametrized tables:
    - floor rows, positive and negative, including `latest.py`, `contest.py`, `attestation.py`, `my-tests/a.py`, `x.github/workflows/a.yml`, `Dockerfile.dev`, `docs/spec/overview.md`, `docs/CLAUDE.md`, and `.claude/rules/` at the root and under `claude/`;
    - comment families, including `#!`, a C preprocessor include directive in `.c`, `*ptr = x;` in `.c`, `.go` and `.rs`, and a bare closing `"""`;
    - added text: an Edit whose `new_string` repeats a comment line from `old_string` adds no comment line, an Edit that adds one does, and a Write counts every comment line in `content`;
    - the ledger parser over these variants: indentation, `*` and `1.` markers, bold wrapping, two names on one line, a path form, a fenced block, CRLF, duplicates, an unknown name, a roster name outside the table, and the same grammar under the `**Still uncertain:**` label;
    - the ledger anchors: a first ledger line on the `**Self-review:**` label's own line, a ledger line containing the words "Still uncertain", a report with no `**Still uncertain:**` label, and a `## Self-review` heading that is not the label;
    - tool-event extraction: a recurring `tool_use` id, an `api-request-blob` copy with a different key set, one `tool_use_id` carrying results in a blob record and in another record (the other record's wins), two non-blob results sharing one id (the first wins), a result held only in a blob record (the call counts as pending, the conservative reading), results out of call order, an absent, `false` and `true` `is_error`, and an undecodable line, which is skipped;
    - the pending-call rule: a pending reviewer Read, a pending edit and a pending `git diff` each do not count, and each pairs with a control whose result lands and counts;
    - the claim: a marker that already exists is `allowed-claim-raced`;
    - the report rule: an absent, `null` or non-string report field reads as an empty report;
    - R5's command shapes: `git diff -- a.sh` and `git diff --stat` count, while `git status` and `echo "git diff"` do not. `build_records`' own shapes stay in its suite;
    - every verdict-table row, the reason's ordering and deduplication, its count of unrecognized names with the accepted names listed, and the closing complete-report request in each reason, the PreToolUse one naming `SubagentHandback`;
    - the log line for a format miss (`ledger=0`, `reads` above 0) and for a skipped read, each with its `failed` field.
  - Contract tier: the fixture and its two derived variants each extract to their expected sequence of `(tool name, key path, success)`, and a transcript in an unknown record envelope is `allowed-unparsed-transcript`.
  - The evaluator CLI against directories under `tmp_path` and transcripts built in the fixture's record shapes, with placeholder paths. Each case pairs with a control that sends back.
    - Changed paths, with a main checkout from `helpers.init_git_repo_with_commit` (`helpers.py:1259`), so a real `git worktree add` works:
      - a symlinked edited path, and a tilde-path Read of a changed file;
      - edits in a linked worktree that `git worktree add` places under the main checkout's `.claude/worktrees/`, with the payload `cwd` at the main checkout, which count; this nested case is the real layout;
      - edits in a linked worktree that `git worktree add` places as a sibling of the main checkout, with no `.git` entry in any ancestor of its root, so only that root's `.git` file can count them, which count;
      - a scratch edit in no repository beside a repository edit, which drops and is counted;
      - edits that are all in no repository or deleted before the hook ran, which log `allowed-no-repo-path`;
      - a return sent back for a ledger name with no Read, then a second return that adds the Read while every edit still sits in no repository, which logs `allowed-no-repo-path` and leaves no marker;
      - no transcript, which logs `allowed-no-transcript`.
      - The sibling-worktree case and each case with an edit in no repository first assert that no ancestor of their scratch directory holds a `.git` entry, and skip with that reason otherwise.
    - One case, marked `@pytest.mark.timing` as the file's only marked test (row 76), runs a synthetic transcript of several thousand records with a few hundred edited paths in under 2.5 seconds, half `_lib_capped`'s cap (row 68). The transcript repeats the fixture's record shapes so every call pairs with its result, the edited paths exist under a directory holding a `.git` entry, and the case asserts that the log line's `calls`, `edits` and `changed` equal the generated counts, so the bound times a run that reached a verdict.
    - The claim: a pre-created, read-only marker directory is `allowed-claim-failed`, skipped as root and first asserting that a write there fails, and a log path that is a directory still emits the send-back with exit 0.
    - Envelopes: `updatedInput` preserves every other `tool_input` field, and an absent, `null` or non-string `message` reads as an empty report whose second return ends `passed-unflaggable`.
  - The hook as a subprocess, run by direct exec through a private raw runner returning `(returncode, stdout, stderr)` with a positive control, and `CLAUDE_CONFIG_DIR` set to a temporary directory holding no hook state.
    - The hook file is executable (`os.access(path, os.X_OK)`).
    - Scripted sequences: an edit to a `.sh` file and its test sends back naming `staff-platform-engineer` and `staff-sdet`. Adding the Reads, the ledger lines and a `git diff` then passes silently and leaves no marker. An unchanged second return passes with the prefix and leaves no marker. A later cycle on the same agent id sends back again. The SubagentStop sequences end `passed-labeled` and `passed-unlabeled`. SubagentStop after a handback is `not-owned`, and `stop_hook_active` with no marker never blocks.
    - Cross-arm sequences, asserting the log verdict and the marker state after every call:
      - SubagentStop `sent-back`, then a PreToolUse handback still missing items, ends `passed-flagged`;
      - PreToolUse `sent-back`, then a plain-text SubagentStop, is `not-owned` with the marker kept, and the next handback ends `passed-flagged` (row 65).
    - Output shape per arm: every PreToolUse output has `hookSpecificOutput.hookEventName` `PreToolUse` and a `permissionDecision`, with `permissionDecisionReason` on a deny and `updatedInput` on an allow, and no top-level `decision`. Every SubagentStop output has top-level `decision` `block` and a `reason`, and no `hookSpecificOutput`.
    - Hostile report text through the PreToolUse arm, decoded with `json.loads` and compared field by field: quotes, backslashes, `\n` sequences, `$(...)`, backticks and a trailing newline.
    - One case per allow branch of the wrapper. Each payload is byte-identical to the sent-back control except the field or environment under test, and each case asserts exit 0, empty stdout, no new marker, and its log line or, for the first, no log line:
      - `agent_type` `plugin:code-writer` with `"code-writer"` in the report text, which passes the prefilter and fails the exact match;
      - one invalid `agent_id`;
      - `jq` absent, and `python3` absent, each through `helpers.build_path_without` (row 73), the two missing-tool cases the `claude-hook-review` checklist names (row 77);
      - the evaluator file absent, so `python3` exits 2 and the hook logs `allowed-evaluator-exit=2` (row 70).
    - No case exits 2.
    - `settings.json` registers the hook exactly under PreToolUse `SubagentHandback` and SubagentStop `code-writer`, each with `"timeout": 20`, no `onFailure` and no `async`.
  - Contract cases:
    - the `HOOK_TEST_FIXTURE` ledger block, extracted with `helpers.extract_skill_command`, parses to its concrete reviewer name, and a copy with the backticks or colon mangled parses to nothing;
    - the domain table maps test code to `staff-sdet`, shell scripts to `staff-platform-engineer`, prose to `comment-discipline-reviewer`, the `CLAUDE.md`, `AGENTS.md` and rule-file clause to `ai-instruction-and-memory-files`, and its last row to `staff-backend-engineer`;
    - the evaluator's accepted names equal the table's backticked names, and each resolves to an agent file or that skill's `SKILL.md`;
    - the cited heading is in `helpers.heading_texts` of `code-writer.md`.
  - The payload builder carrying `agent_id` and the raw runner are private to this file, its only user.
- `claude/.claude/hooks/tests/test_hook_alignment.py`: `test_hook_class_value_valid`'s docstring at lines 1836-1847 (Fail posture). No count changes.
  - "'turn-gate' fires on Stop and may block the *turn* from ending (decision: "block") rather than a tool call from running" is edited. It says the class fires where an agent's run ends, on Stop, SubagentStop or PreToolUse SubagentHandback, and sends the agent back rather than blocking a tool call, with top-level decision "block" on the first two and hookSpecificOutput.permissionDecision on the third.
  - "rather than a Stop hook being mislabeled 'gate'" is edited to "rather than an end-of-run hook being mislabeled 'gate'". Its parenthetical on Layer 2's PreToolUse-specific checks stands, because `GATE_HOOKS` still excludes the class (row 73).
  - The 'informational' clause stands.
  - The batch-gate sentence's "'turn-gate' (Stop block-to-force-continuation)" is edited to "'turn-gate' (end-of-run send-back)". Its 'gate' contrast and its closing clause stand.
- `plugins/claude-hook-review/skills/claude-hook-review/SKILL.md` and `plugins/claude-hook-review/.claude-plugin/plugin.json`: Design item 5. The PR body carries `claude plugin install claude-hook-review@claude-config --scope project`, item 4 of `plugin-semver`'s checklist.
- `docs/hooks.md`: a Utility hooks entry naming the two events, the class, the fail posture, the registration `timeout` and the log path. It says that `turn-gate` covers both of this hook's output shapes. It takes no tier row and no count change (row 35), and no disable-path text (row 79).
  - It gives the liveness check: after a `code-writer` dispatch, the log holds a line for its agent id. When none appears, check that both hook files exist under `"${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/"` and that `/hooks` lists the registrations.
  - It says that a missing `_lib.sh`, a below-floor bash and a failed config-dir lookup exit before any log line (row 72).
- `docs/design-decisions/code-writer-self-review-send-back.md` (new), in `.claude/rules/design-decisions.md`'s format with no `Formerly` clause. It records the rationale for the two entry points, citing the probe by its cases and Claude Code version and never by path. It also records the `turn-gate` posture, the send-back once, the floor and catch-all, repository membership by `.git` walk-up, the comment check read from the writer's own edits, the ledger in the return, the transcript over a recorder, and no skip, which removes step 5's typo exemption. It names 2.1.310 as the probed version, with 2.1.295 through 2.1.309 unprobed and failing open (row 27), and lists row 26's paths as unverified, with the outcome log as the instrument. It links `code-writer-self-review.md` for the self-review pass itself.
- `docs/design-decisions/ready-for-review-fix-loop-convergence.md`: Design item 6.
- `README.md`: a Hook transitions row beside `advance-past-commit-stall.sh`'s (line 195), whose Gates cell names PreToolUse `SubagentHandback` and SubagentStop and the `turn-gate` class, in that row's form.
- `CHANGELOG.md`: one `### Added` entry under `[Unreleased]` naming the send-back and both new hook files. On the legacy real-directory `~/.claude/hooks` layout, it says to run `./install.sh` after pulling, since until then the send-back does not run (row 51), and to confirm with `ls` on both files under `"${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/"`. It names Claude Code 2.1.310 as the probed version, and says the hook is unprobed on 2.1.295 through 2.1.309 and fails open there (row 27).
  - A `### Changed` entry, in the form of the 2.5.0 entry at `CHANGELOG.md:57`, names `claude-hook-review` 2.6.0, its bump class (minor — the `turn-gate` definition widens and nothing is reversed), and `claude plugin update claude-hook-review@claude-config --scope project`.
- `.gitignore`: Design item 4.

## Verification

0. **Probe, already run on Claude Code 2.1.310.** None of its cases changed a repository file.
   - Cases H and I registered a PreToolUse `SubagentHandback` hook with no `onFailure`, one pointing at a missing script and one at a script that exits 1. Both let the handback through on the first call, and the parent received the report (row 24).
   - Cases J and K did the same for a SubagentStop group matched on the agent's name, in `acceptEdits` mode. The group fired, each failure was non-blocking, and the parent received the plain-text report (row 64).
   - Case L denied an auto-mode handback and recorded the plain-text stop, the harness's own re-prompt and the delivered second handback (row 65).
1. **Phase 1.**
   - `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py` passes, with no edit to `select-tests.py`. If it widens to the full suite on its own for `.gitignore` or another path, that run stands.
   - `scripts/venv-run.sh pytest --collect-only -q claude/.claude/hooks/tests/test_send_back_skipped_self_review.py` lists the file's test ids.
   - `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/` and `scripts/list-shell-files.sh | xargs -0 scripts/venv-run.sh shellcheck` pass.
   - Once the parent stages the change, `git ls-files -s claude/.claude/hooks/send-back-skipped-self-review.sh` shows mode 100755.
2. **Live smoke, after Phase 1.** The engineer runs the probe harness against the worktree's hook, registered by absolute path under the same two matchers, with the same `timeout`, in a scratch repository's `.claude/settings.local.json`. The writer is a project-level stub agent named `code-writer` whose body edits one `.sh` file and its test, reports "done", and repeats that report unchanged after any send-back, so no case depends on the real writer's judgment. Record `claude --version`.
   - Gating cases:
     - auto mode foreground, with the session run from the scratch repository's main checkout and the stub's edits landing in a linked worktree under its ignored `.claude/worktrees/`;
     - `acceptEdits` foreground, with edits in the main checkout;
     - a stub variant that Reads both reviewer files, writes the ledger and runs `git diff`.
   - Their pass criteria come from hook-owned artifacts, never the writer's behavior. The log lines read `sent-back` then `passed-flagged` in auto mode, `sent-back` then `passed-unlabeled` in `acceptEdits`, and `clean` for the variant, each with `changed=2`. In auto mode a `not-owned` line from the SubagentStop arm follows each delivered handback. The marker directory is empty afterward. In auto mode the parent's raw Agent tool_result or report message starts with `SELF-REVIEW INCOMPLETE (hook-verified):`. A miss goes to the engineer with the log.
   - These recorded logs are the check that the real transcript nesting parses (row 62): `changed=2`, and `clean` for the variant, hold only when the evaluator finds the stub's edits, Reads and `git diff` in real records.
   - One observation, which records and never gates: an auto-mode stub whose report exceeds 10,000 characters. A missing `sent-back` or a missing prefix there goes into the design doc's known degradations.
3. **Two real dispatches, after Phase 1, as observations.** The engineer dispatches the real `code-writer` in a scratch repository with the hook registered as in step 2, once on a small edit task and once with a `respond-pr`-shaped prompt that lists its own return fields (row 78), then reads each log line. The expected verdict is `clean`, or `sent-back` whose `failed` field names a read or re-read gap. A format miss (`ledger=0` with `reads` above 0) goes to the engineer with the line, because it means the grammar or the Return-format text rejects a compliant writer.
4. `/code-review` runs on the staged change. Per `.claude/rules/review-pipeline-dispatch.md` it also dispatches `/agent-review` for `code-writer.md`, `/skill-review` for the `claude-hook-review` skill, and `plugin-semver:plugin-semver` for its plugin, the last two hook-enforced. `claude-hook-review:claude-hook-review` reviews the hook, per the repository `CLAUDE.md`'s "Should this be a hook?" paragraph.

## Out of scope

- **Parent-side handling of the ledger or the INCOMPLETE line** in `subagent-delegation/SKILL.md` or `review-orchestrator.md`. The line describes itself to a direct dispatcher, and on the orchestrator route the next round reviews every fix regardless (row 44).
- **Judging the ledger's content.** Whether an angle was genuinely applied is not observable to a hook; the hook checks only that each claim is backed by a Read.
- **Edits made through Bash** (`sed -i`, heredoc writes). Attributing a path to a shell command is a separate parser, and `code-writer` carries Edit and Write for its edits.
- **Gating other implementers**, such as `general-purpose`. The hook's exact `agent_type` match can widen later; this plan's evidence covers `code-writer` only.
- **Measuring the hook's effect** on fix-induced defects. The log holds per-dispatch outcomes, and the measurement is a later windowed remeasurement under `docs/private-project-redaction.md`'s rules for publishing a tooling measurement.
- **A `docs/hooks.md` "Gate deadlock recovery" entry.** No failure path of this hook blocks a return, so it has no lock-out to recover from.
- **A per-path re-read check.** R5 credits any `git diff`, because per-path coverage would need each diff command's pathspecs parsed, and a Read of a changed path already counts.
- **`README.md:266`'s link to `docs/design-decisions.md` §11**, which now lives at `docs/design-decisions/code-writer-self-review.md`. It is unrelated to this change and is a separate fix.
- **A docs sentence on disabling the hook.** The engineer chose none (row 79), so Fail posture's one sentence stays the plan's only mention.
- **A read bound on the prefilter's scan copy.** The payload's field order is undocumented, so a bounded scan could miss `agent_type` behind a long report and switch the check off for the longest reports. The hook reads the whole payload once regardless, because the evaluator needs it.
- **Probing Claude Code 2.1.295 through 2.1.309.** The design doc and the CHANGELOG name them unprobed, and every failure path fails open.
- **An interpreter-version guard in the evaluator.** `python3` 3.11 is an install-time requirement (row 48), and a below-floor interpreter already logs `allowed-evaluator-exit=<status>` and fails open.
- **A sweep of stale marker directories.** Every normal outcome removes its marker, a stale one matters only to a later run under the same 16-hex-character agent id (row 19), and the cost is one empty directory per writer that never returned (Known gaps).
- **A SIGTERM handler for a cap kill between the claim and the flush.** That window holds only a `json.dumps` and one write, after all other work, and its cost is one flagged pass with no send-back (Known gaps).
