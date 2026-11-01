# Session config-keys log

`record-session-config-keys.sh` adds a row to `<config-dir>/.session-config-keys-log.jsonl` at each startup, resume, `/clear` and compaction. A later analysis reads the rows to tell which `config-keys.psv` keys were on for a session.

`<config-dir>` is `$CLAUDE_CONFIG_DIR`, or `~/.claude` when that is unset or empty. Each account has its own `<config-dir>`, which holds that account's log and its `projects/` transcript tree.

If the hook's no-row notice sent you here, go to [Notice](#notice).

## Rows

Each row is one line holding a JSON object. Skip blank lines, and skip a line that does not parse.

- `session_id`, `source` and `logged_at` are strings.
- `source` is the SessionStart source the harness sent, such as `startup`, `resume`, `clear` or `compact`. `unreadable` means the hook could not read it.
- `keys` maps every schema key at that event to a string value.

Rows are advisory and unauthenticated, because any process running as the account can append, forge or trim them. A reader must not use a row to excuse or allow an action. A reader re-validates key names, value tokens and the `logged_at` shape.

## Values

A value is the key's resolved value at that event. A key nobody set shows its schema default, so a row cannot tell a default from an explicit setting.

- `false` means off.
- `unreadable` means the hook could not read the key, so its state is unknown.
- Any other value means on, an enum literal such as `dollars` included.
- A key absent from a row has no state in that row.

A value can be the fail-safe value `_config.sh` substitutes for a state file it cannot read. A gate in that state gets the same value, and the row does not mark it as substituted.

## Joining a row to its transcript

Check `session_id` against `^[A-Za-z0-9_-]+$`, the grammar `_lib_valid_session_id_component` enforces, before building a path from it. Then join the row to `<config-dir>/projects/*/<session_id>.jsonl`.

A subagent's transcript records carry its parent's `sessionId`, so a subagent takes its parent's rows.

## State at a transcript record

To find the key state at one transcript record, use the rows whose `session_id` equals the record's `sessionId`:

- Take the latest row whose `logged_at` is at or before the record's `timestamp`. Compare the two as times, not as strings. `logged_at` has whole seconds, as in `2026-05-19T10:00:00Z`, and a record's `timestamp` has milliseconds, as in `2026-05-19T10:00:00.000Z`. `jq`'s `fromdateiso8601` accepts only the first shape, so drop the fraction before parsing a `timestamp` with it.
- Among rows with equal `logged_at`, take the later line in the file.
- A record earlier than every such row has unknown state.
- A record after a lost event, as Coverage defines it, has unknown state unless a row has a `logged_at` between that event's attachment `timestamp` and the record's `timestamp`.
- Do not count on one row per event. Each row is a full snapshot, so a duplicate row is harmless.

The row taken is the newest evidence of the state at that transcript record, not proof of it:

- Gates reread their keys on every call, so a key flipped mid-session governs at once.
- A row shows that flip only from the next SessionStart event. `/compact` adds that row to the same session, and `/clear` or a plain restart starts a new session with its own row.

## Coverage

Report four counts, each on its own: sessions of each of the two recorder-failure kinds below, lost events, and sessions with unknown state. Do not fold unknown state into a recorder-failure count.

Apply these rules once per session id, pooling every transcript file whose records carry that id, subagent transcripts included.

A hook attachment is a transcript attachment of type `hook_success` whose `command` basename is `record-session-config-keys.sh`. A hook attachment with a non-empty `stdout` is a lost event: that event wrote no row, even in a session that holds other rows.

The coverage start is the earliest `logged_at` among the log's rows. On an account whose log holds no row, or that has no log, it is the earliest hook attachment `timestamp` in the account's transcripts. Compare these times as State at a transcript record says.

An account with neither a row nor a hook attachment has no coverage start. Report it as an account where the hook has not run, not as one with no failures, and tell the engineer to run the `ls` check in `README.md` § "Updating after git pull" on `~/.claude/hooks/record-session-config-keys.sh`. That section says when to re-run the installer.

A session is covered when either of these is at or after the coverage start:

- The earliest record `timestamp` in its transcripts.
- The `timestamp` of a hook attachment in its transcripts.

A covered session with no row is a recorder failure of one of two kinds:

- The hook ran and wrote no row: its transcripts hold a hook attachment.
- The hook did not run: they hold none. Usual causes are a `<config-dir>/hooks` directory that leaves the hook unlinked, a session started without the user-layer settings, the engineer having removed the hook's `settings.json` entry, the harness killing the hook on a hung `<config-dir>` filesystem before it exits, an IDE or desktop session if those skip SessionStart, and a Claude Code release that stops firing SessionStart hooks.

A session that is not covered has unknown state. It predates the coverage start, or its rows were trimmed or deleted.

An event at which the hook did not run leaves no hook attachment. In a session that holds another row, such an event shows in no count.

## Notice

A notice means that event wrote no row. A missing notice does not show that a row was written, and Coverage is the check for that.

The engineer runs the checks below and files any issue. An agent that sees the notice reports it and does nothing else unless the engineer asks. In a `claude -p` run, the agent's report can land in the run's output.

To find the cause, the engineer runs these checks in the shell that launches Claude Code, on the same account, because the hook discards its own error output:

- `bash` on that shell's `PATH` meets `README.md` § "Requirements". `docs/hooks.md` § "Bash version" says what a lower `bash` stops.
- `jq` is on that `PATH`.
- `timeout`, or `gtimeout`, on that `PATH` accepts `-k`.
- `CLAUDE_CONFIG_DIR`, unless it is unset or empty, is an absolute path.
- `CLAUDE_CONFIG_DIR`, unless it is unset or empty, is not `/` or any other value made only of slashes, such as `//`.
- `HOME`, when `CLAUDE_CONFIG_DIR` is unset or empty, is non-empty and not `/`, because an empty or `/` `HOME` leaves the hook no `<config-dir>`, so it writes no row and prints the notice. `HOME=//` works.
- `<config-dir>` exists.
- The account can write `<config-dir>`.
- The filesystem holding `<config-dir>` has free space.
- That filesystem is not read-only.
- That filesystem accepts `chmod`.
- Any file already at `<config-dir>/.session-config-keys-log.jsonl` is a regular file.
- The account owns that file.
- The account can write that file.
- `bash -c '. ~/.claude/hooks/_lib.sh && _config_schema_known_keys'` prints the key list, or the error that stops the hook reading `config-keys.psv`.

If every check holds, the engineer files an issue on the claude-config repository with the notice text and the Claude Code version, and with no session id, path or key value.

The notice prints at every event until its cause is fixed. Until then, the engineer can quiet it only by removing the hook's entry from `claude/.claude/settings.json` in the claude-config checkout. `<config-dir>/settings.json` links to that file. Removing the entry changes a tracked file, so a later pull that touches the file can conflict with it. Removal stops every later row for each account whose `<config-dir>/settings.json` links to that file.

To record the session that showed the notice, the engineer gives it a new event after the fix, because the hook writes a row for the session only at its next event:

- A fix to `bash`, `jq`, `timeout` or `CLAUDE_CONFIG_DIR` changes the shell's environment, which reaches only a Claude Code process launched from that shell. The engineer restarts the session from the fixed shell with `claude --continue` or `claude --resume`.
- After any other fix, `/compact` in the running session records it.
- `/clear` and a plain restart start a new session and leave the one that showed the notice without a row.

## Retention

The log holds a session id and time for every session on the account. It outlives transcript cleanup. Nothing trims it automatically, so old rows stay available as a baseline.

After a deletion, or a trim that keeps no row, read Coverage only after the next event writes the log's first row, because until then the account has no row. With no hook attachment either, Coverage reports that the hook has not run. With one, Coverage starts at the earliest hook attachment and counts each later session whose row was removed as a recorder failure. A session from before the deletion or trim that gets no later row has unknown state.

Only the engineer runs the trim, because an agent's own session stays open while it runs. To trim:

1. Pick `<days>` no shorter than `cleanupPeriodDays`, because a shorter cutoff gives unknown state to sessions whose transcripts still exist. `cleanupPeriodDays` is the Claude Code setting for how long transcripts are kept. It is set in `<config-dir>/settings.json` and defaults to 30 days.
2. Close every Claude Code session on the account, because a row a session adds during the trim is lost.
3. In a terminal, replace `<days>` in the command below and run it. It keeps the rows that parse and fall inside the window, and drops blank lines. Run it in a shell whose `CLAUDE_CONFIG_DIR` matches what Claude Code sees for the account being trimmed, because the command resolves that account's `<config-dir>` as `${CLAUDE_CONFIG_DIR:-$HOME/.claude}`.

```bash
(cd "${CLAUDE_CONFIG_DIR:-$HOME/.claude}" && trimmed=$(mktemp .session-config-keys-log.XXXXXX) && { jq -cR --argjson days <days> 'try (fromjson | select((.logged_at | fromdateiso8601) >= now - $days * 86400))' .session-config-keys-log.jsonl >| "$trimmed" && mv "$trimmed" .session-config-keys-log.jsonl || { rm -f -- "$trimmed"; false; }; })
```

Afterwards `ls -l "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.session-config-keys-log.jsonl"` shows the log as `-rw-------`.
