# Cursor adapter

The adapter runs this repo's unchanged hooks under Cursor's local IDE. It installs user-level entries into `~/.cursor/hooks.json`, and each entry but the sessionStart check runs `hook-shim.py`, which translates Cursor's payload into the shape the hook expects and the hook's answer back into Cursor's. No file lands in a repo you open in Cursor.

The adapter lives in `claude/.claude/scripts/cursor-adapter/` and reaches `~/.claude/scripts/cursor-adapter/` through the stow link `./install.sh` makes. Generated commands name that stable path, never a checkout or a worktree.

The marker-gated gates (see `docs/hooks.md`, which describes each gate's marker release in that gate's own entry) release on a marker file. Under Cursor, the review skill run in a conversation writes that marker through `marker.sh`, where the conversation's mode lets a Shell call write the routed config dir ("Where the state lives" names the modes). Where it cannot, the gates can deny operations, and your own terminal or `uninstall` is the way out (Recovery's marker-gated entry).

## Commands

Every command is `python3 ~/.claude/scripts/cursor-adapter/cursor-adapter.py SUBCOMMAND`, run from a terminal, with the python3 you want recorded first on `PATH`. The file is not executable and not on `PATH`, so a bare `cursor-adapter.py` does not run. The commands below name the subcommand only.

- `install --config-dir DIR [--acceptance-only]` records the tools it found and writes the entries. For example, `install --config-dir "$HOME/.claude"` routes every hook to the config dir Claude Code itself uses.
  - `--acceptance-only` is for the attended acceptance run, which observes how the entries behave under Cursor. It records `acceptance_only` in the install record, and `install`, `check`, `status` and the sessionStart report then print a warning that the marker-gated gates can deny and that `uninstall` backs out. An install made without the flag carries no such key, so re-running `install` without it clears the warning.
  - `DIR` is the config dir Cursor's hooks route to: each hook runs with `CLAUDE_CONFIG_DIR` set to it. It must be an absolute path of letters, digits and `. _ / + @ = -` with no `..` or empty component, a real directory you own that is not writable by group or others, and outside any git work tree.
  - Every generated path lies under the bare `$HOME/.claude`, whatever `DIR` is, so the checkout must be stowed there. `./install.sh` does that.
  - It refuses with nothing written in cases such as these:
    - A prerequisite fails. "Prerequisites" below says what each needs.
    - The shim's self-check fails.
    - `~/.cursor/hooks.json` does not parse, cannot be read, is not a JSON object, or has a `hooks` member that is not an object of event arrays.
    - Its top-level `version` is not 1 or is absent. The refusal names the version found, and the fix is to set `version` to 1 by hand.
    - An install already routes another config dir. The refusal says to run `uninstall` first.
  - Run `uninstall` before you re-link `hooks.json`. After `install`, the adapter's entries live only in the real file, so re-stowing or `stow --adopt` drops them or commits `$HOME` paths into the dotfiles repo. `install` also refuses when `~/.cursor`, `~/.cursor/cursor-adapter`, `~/.cursor/hooks.json` or the state directory is a symlink, is owned by someone else, or is writable by group or others. `uninstall` refuses for the first three, and leaves an unsafe state directory in place and says so. A `hooks.json` that a dotfiles manager links in therefore has to be the real file during both.
  - It merges into your own `~/.cursor/hooks.json`: every entry that is not the adapter's stays where it is. Before it replaces or removes anything, it prints each foreign entry, each duplicated or differing adapter entry, and each non-owned entry the saved pre-install file does not hold, each line starting with what the step does to it. The first install replaces nothing, so it prints no non-owned entry.
  - It saves your original `hooks.json` bytes to `~/.cursor/cursor-adapter/hooks.json.pre-install` before the first change.
  - It does not test that `~/.claude/settings.json` and `DIR/settings.json` resolve to the checkout's, so `check` can report one after a clean `install`. For `~/.claude/settings.json` the fix is `./install.sh` from the checkout. `./install.sh` stows only the bare `~/.claude`, so for a `DIR` that is another directory the fix is to re-link `DIR/settings.json` to the checkout's file.
  - Its exit status is 0 on success, 1 for a refusal that wrote nothing, and 3 for a failure after the first write. A status 3 message names the step the command last finished, or says it stopped before its first step finished. The next `uninstall` completes or removes what was written.
    - An unexpected error ends with status 1 and one line naming the command, the exception class and the innermost frame (file, line and function), never the exception's message. For `install` and `uninstall` the line adds that nothing was written, because every failure after their first write is status 3. A failed write to stdout after the command's last write also ends with status 1, an accepted limit.
    - A usage error from argument parsing, such as a missing `--config-dir` or an unknown subcommand, ends with status 2.
    - An interrupt (Ctrl-C) after the first write ends with Python's own status and its traceback, and the files written so far stay. Re-run `uninstall`, or `install`, to complete it.
    - The steps of `install` are `after-pre-image`, `after-install-record`, `after-generated-files`, `after-hooks-json` and `after-manifest`.
    - The steps of `uninstall` are `after-entries`, `after-generated-files`, `after-state` and `after-record`.
    - A `hooks.json` that changed while `install` ran ends with status 3 too, because the record and the saved pre-install file are already written when `install` finds the change.
  - To find the config dir an install used, read `routed_config_dir` in `~/.cursor/cursor-adapter/install.json`, or reuse the `install` command you ran. Every fix that says to re-run `install` needs the same `--config-dir`.
  - Run one `install` or `uninstall` at a time. Two at once, or one of each, are unsupported: both sweep the `.adapter-tmp-*` files in `~/.cursor` and in the record directory, so one deletes the other's in-flight temp file and the other's rename fails or its steps interleave. The adapter takes no lock.
- `uninstall` removes the adapter's entries and restores the saved bytes when nothing else changed.
  - It then removes the runtime state, the install record, the saved pre-install file and the manifest, in that order, and the record directory last. An `uninstall` that stopped after it removed the install record leaves the saved pre-install file and the manifest, and its re-run says the runtime state may already have been removed.
  - With no adapter entry, install record, saved pre-install file or manifest, it prints `Nothing was installed`. A kept foreign entry ends the run naming it instead, never that line.
  - It ends by telling you to relaunch Cursor and to read `~/.cursor/hooks.json`, which then holds no entry that runs the adapter or does not exist. When a kept foreign entry, a kept manifest path or a left state directory remains, it ends by naming what remains instead of saying that. It does not point at `check` or `status`: both need the install record that `uninstall` has just removed, so each prints `No usable install record` and exits 1.
  - It keeps a foreign entry, one that holds an adapter path without being the adapter's own, and says so, because Cursor can still run the adapter through it. Delete that entry from `hooks.json` by hand.
  - A saved pre-install file that is not a JSON object whose `hooks` member is an object of arrays is not used. `uninstall` says so, rewrites `hooks.json` without the adapter's entries and goes on.
  - The shim supplies `uninstall`'s strict JSON parse, its error names and its comparison of the entries it removes with what `generate` emits now. When the shim does not load, `uninstall` falls back to a lenient JSON parse and to class names. When the shim or the comparison fails, it says so if `hooks.json` holds an adapter entry to compare. It goes on either way, so it still runs when the shim is missing or broken.
- `check` re-tests the recorded wrapper, the recorded python3 floor, the python3 and bash floors on the hooks' `PATH`, and that `jq` and `sha256sum` resolve there. It does not re-test `git`, `ps` or `gh`. It runs the shim's self-check under the recorded python3, compares `~/.cursor/hooks.json` with what `generate` emits now, reports a hook script an entry runs that is missing, and exits nonzero on any finding. It prints each finding as a `finding:` line and `no findings` when there is none. It also reports a `~/.claude/settings.json` or `DIR/settings.json` that does not resolve to the checkout's, which `install` does not test.
- `status` reads the shim's log and its one rotated file, bounded to their last 4 MiB and 50,000 lines each, and reports fire counts per registration, shim faults by class and Python version with a count, and the same comparison. A value in the log outside the log grammar or the known registration labels prints as `other`. When the state directory is a link, it prints one line saying so and reads no log. `status` exits 1 when there is no usable install record or `hook-shim.py` does not load, and exits 0 when it prints findings from a readable log. It flags a Shell-selecting registration label that has no log line while other Shell calls logged. The flag counts the lines `status` read, which can reach back before the last install, so a registration added by a later `install` can be flagged until it fires.
- `generate` prints the `hooks.json` it would emit and writes nothing. `generate --print-entry KEY --shim PATH` prints one registration's entries against a scratch shim.
- `check --hook-env` is the sessionStart entry's command. It prints one JSON document with the report in `additional_context` and exits 0 whatever it finds. The report carries the tamper comparison and the tool findings `check` prints, as `note:` and `finding:` lines, and `no findings` when there is none.

`status`, `check` and the sessionStart report share one comparison of `hooks.json` with what `generate` emits now. It names, by registration label, event and position and never by an entry's text:
  - a missing, changed, extra or duplicated adapter entry, with the fix of re-running `install`;
  - each foreign entry that holds an adapter path without being the adapter's own, by event and position, with its own fix: delete that entry from `hooks.json` by hand, because `install` and `uninstall` keep it;
  - the count of non-owned entries per event, as a `note:`, because your own hooks are expected there. A hook added after install shows as that count, and a planted entry differs from yours only by it. When no adapter entry remains, each non-owned entry the saved pre-install file does not hold is a finding, by event and position;
  - a top-level `version` other than 1, with the fix of setting it back to 1 by hand, and a count of top-level members that differ from the saved pre-install file, with the fix of `uninstall` followed by `install`, which saves the current members as the new baseline;
  - an install record that the shim itself refuses, such as one with a damaged tool map or no `timeout` or `gtimeout`, because every fail-closed entry then denies with the `install-record` outcome;
  - a `hooks.json` that does not exist, with the fix of re-running `install`.

When `generate` fails, for example on a registration the checkout no longer translates, the comparison says so first and ends that finding with the sentence that restores the checkout. It still reports the checks that need no generated entries: a changed `tool-map.json`, a stale contract version, a record the shim refuses, the top-level members, and each foreign or duplicated entry.

Quit Cursor and end every Cursor CLI session before `install` and `uninstall`, and relaunch afterwards, because a change to `hooks.json` while Cursor runs is untested. Once Cursor has quit, end any `cursor-agent` worker still running, because such workers have outlived the app. Whether one runs hooks is pending an unrecorded probe (see "Cursor CLI beside the adapter").

After a `git pull`, run `check`: it compares the installed entries with what `generate` emits now and reports a changed `tool-map.json`. A changed registration or contract version needs a quit, an `install` and a relaunch, which each finding says. An install made with `--acceptance-only` keeps that flag's warning until you re-run `install` without it.

## What changes in Cursor

- **Every Shell command is rewritten.** The directory pin starts each agent Shell command with two lines, an environment line and then `builtin cd -- '<dir>' || exit 1`, so a gate evaluates the directory the command runs in. The directory is the workspace's first root. Four consequences follow:
  - A `cd` no longer carries over: the next call starts in the pinned directory again. In a multi-root workspace, open as a root each tree the agent works in, and have the agent lead a command with `cd <absolute path> &&` to work under a later root.
  - Cursor's approval prompt, its allowlist matcher and Auto-review read the prefixed text.
  - An entry of your own on `beforeShellExecution` whose matcher is anchored at the start of the command, or whose script tests its first word, stops matching with no error. Such an entry is not supported beside the adapter.
  - A shell's error line numbers count the prefix's two lines.
- **Every Shell call runs under the routed config dir.** The rewrite's first line unsets `CLAUDECODE`, exports the routed `CLAUDE_CONFIG_DIR` and the call's session id, and exports or unsets the host process id the hooks resolved, all ahead of the agent's command. The call therefore runs under the routed `CLAUDE_CONFIG_DIR`, replacing a value the Shell's environment holds, such as a per-project one from direnv. A `claude` the agent starts in that call uses the routed account's context, not the project's. `marker.sh` and the other scripts an agent runs resolve the state the hooks read for the same reason.
- **A worktree lock outlives its conversation.** An IDE conversation's lock names the `Cursor` main process, which every IDE conversation shares, so it reads live while Cursor runs. Closing or archiving the conversation releases nothing. The next conversation's first write in that worktree is denied with text that names the lock's process id, until Cursor quits or you run `git worktree unlock <path>` once nothing works in that worktree (Recovery's live-holder entry). A subagent's calls take its direct parent's conversation id as the session id, so a direct subagent shares its parent's markers and lock, and a nested one does not.
- **The import's copies keep running.** Cursor's Claude Code import runs its own untranslated copies of the hooks beside the adapter's entries, so a hook can fire twice. A deny from either source wins.

## Cursor CLI beside the adapter

The adapter targets the local IDE, and the Cursor CLI (`agent`) reads the same user-level `~/.cursor/hooks.json`, so a CLI session meets the adapter's entries too. Nothing stands down for it: a CLI conversation is gated as an IDE conversation is, and the adapter keeps no CLI-only state, flag or log field. A claim marked "pending an unrecorded probe" has no acceptance probe result recorded for it.

- **Identity.** A CLI conversation resolves its own identity, and its session id is its conversation id. Where a hook's walk up its process ancestry meets no `Cursor` main process, it takes the nearest process named exactly `agent` or `cursor-agent` as the host. A CLI started in Cursor's integrated terminal, or under an IDE agent's Shell call with no `claude` between, anchors on `Cursor` as an IDE conversation does.
- **Markers and locks.** A CLI conversation's markers and worktree locks are its own, keyed on its session id. A lock another conversation holds, IDE or CLI, denies its write with the live-holder text that names the lock's process id, as a lock between two IDE conversations does. Agents on both launchers can therefore work at once in separate worktrees, and the collision guard keeps each out of the other's. Whether the process id that a conversation's Shell calls share is the one its hook events and its subagents' calls see is pending an unrecorded probe.
- **Lifetime.** A CLI conversation's state lives while its CLI process lives, so the lock of an `agent -p` run reads dead once the run exits. A CLI that runs under a running Cursor anchors on `Cursor` and lives while Cursor runs.
- **Clearing a marker-gated deny.** The review skill, run in the CLI conversation, writes the marker through `marker.sh` under the rewrite's exports, as in an IDE conversation. Each of these is pending an unrecorded probe:
  - that the CLI runs a review skill end to end, subagent dispatches included;
  - that `marker.sh` can resolve its identity and write the routed config dir there;
  - the same two under a CLI sandbox mode.

  Recovery's CLI entry gives the way out for each gate class.
- **The directory pin.** The CLI applied a `preToolUse` `updated_input` rewrite when observed. Whether its `beforeShellExecution` entry receives the rewritten command, so `pin-check` passes, is pending an unrecorded probe.

## Cursor-update regression checklist

The sessionStart report names this list when Cursor's version changes. The report also fires once per new CLI build string and when the version record cannot be read, and the same list applies. The engineer, not the agent, runs it after the update, on a new conversation. It holds these checks:

1. The report's host-walk line reads `Host walk: found.` A `not-found` result means identity no longer resolves, and Recovery's host-walk entry says what to do.
2. With the Claude Code import's copies of the hooks on, `<routed config dir>/sessions/` holds no `<pid>` file for a live `Cursor`, `agent` or `cursor-agent` process, and a later session start adds none.
3. A Shell call's command starts with the directory pin's two-line prefix (see "What changes in Cursor"), whose first line starts with `unset CLAUDECODE`, and the `pin-check` deny count `status` reports does not rise across a new conversation after the update. Read the count before and after. It covers only the log tail `status` reads, so compare the two only when `status` prints no note that its counts cover the log's last lines at most.

## Parity with Claude Code

Under the adapter, Cursor behaves as Claude Code does wherever the vendor allows. This doc and `claude/.claude/scripts/cursor-adapter/parity-exceptions.json` together list each known difference. The file holds each harness registration, permission deny, adapter gate or Cursor tool that the adapter does not carry, with its reason. This doc notes any other difference, including a newly found one.

A gate or deny that cannot reach parity under Cursor is raised to the engineer as an open decision before it ships, and an entry in the file that drops a gate or deny falls under this.

A direct `marker.sh write` in an unprompted mode matches Claude Code's main session, and the Limitations entry "Marker-gated gates" covers it.

A write that newly takes a worktree lock prints no "run `git worktree unlock <path>` if this was unintended" note under Cursor, where Claude Code prints one, because Cursor's hooks reference lists no `additional_context` output for `preToolUse` [unverified: Cursor's hooks reference was not re-read for this claim]. The lock and the collision guard still work.

## Prerequisites

- **Python:** the recorded python3 and the `python3` the hooks run both meet the toolkit's Python floor, which `README.md` § "Requirements" states.
  - `install` records `sys.executable` of the first `python3` on `PATH`, resolved from `~/.cursor/` or `$HOME` when `~/.cursor` does not exist yet, and refuses a virtual environment's interpreter.
  - The fix for a recorded python3 below the floor is to put a newer `python3` first on `PATH` and re-run `install`. The refusal names that interpreter's path and version.
  - The same fix serves a Python `SyntaxError` from an older interpreter that cannot parse `cursor-adapter.py`.
  - The hooks' `PATH` holds each recorded tool's directory in record order, python3's first, then `/usr/bin`, `/bin`, `/usr/sbin` and `/sbin`. The bare-name `python3` the hooks call is therefore the recorded interpreter whenever its directory holds it as `python3`. When that directory holds no file named `python3`, or an older one, the refusal names the directory and its own fix.
  - A tool in python3's directory shadows the recorded one for the hooks. In practice that needs python3 to share a system or package-manager `bin` directory with other tools.
- **GNU coreutils `timeout` or `gtimeout`:** stricter than the `timeout` bullet in `README.md` § "Requirements". `install` records the first of `timeout` and `gtimeout` whose `--version` first line names GNU coreutils, because BusyBox returns a TERM-trapping hook's own status, as the `_lib_capped_for` header in `claude/.claude/hooks/_lib.sh` states, and a timed-out hook could then read as an allow. On macOS, `brew install coreutils` provides `gtimeout`. Otherwise put GNU's directory first on `PATH`, then re-run `install`.
- **bash and the other tools:** bash 5.2 or later, `jq`, `git`, `ps`, `sha256sum`, and `gh` when present, found on `PATH` as absolute paths with no `..` component and none holding `:` or a newline.
- **Shells:** the launch guard around each hook call is POSIX `sh` text, tested under `/bin/sh`, bash and zsh, and a machine with no zsh runs the rows under the shells it has.
- **Run mode:** no source gives the adapter Cursor's run mode, so every report prints `run mode not readable; the run-mode precondition in docs/cursor-adapter.md is unchecked`. The precondition is your own attestation: Allowlist mode with the sandbox off and an empty allowlist, so that every Shell call prompts. Only the empty allowlist was observed, so a non-empty one is an unobserved case. The directory pin's prefix (see "What changes in Cursor") changes what an allowlist entry matches. Cursor's permissions reference calls its allowlist and auto-run settings "Not a security boundary".

## Where the state lives

- `~/.cursor/hooks.json`: the merged entries.
- `~/.cursor/cursor-adapter/`: `install.json` (the routed config dir, the recorded tool paths, the contract version and the tool map), `hooks.json.pre-install` (or `hooks.json.pre-install.none` when you had no file) and `manifest.json`.
- `<routed config dir>/cursor-adapter-state/`: the shim's log `shim.log` and its one rotated file, the first-call marks, and the rotation lock. The directory is mode 0700 and gitignored.
- `~/.cursor/sandbox.json`: the sandbox grant, which lists the routed config dir's marker and active-bypass directories under `additionalReadwritePaths`, so an agent Shell call under a sandboxed mode can write a marker. The adapter builds the grant, but `install` does not write it while `gate-settings-permissions.sh` is listed in `parity-exceptions.json`. `install` writes it once that gate is no longer listed, because that gate denies a `marker.sh` shape your permissions do not allow in a mode that gives no prompt.
  - Until then, marker-gated work uses Allowlist with the sandbox off, and `status` prints `sandbox grant: pending.` with that reason.
  - Once `install` writes it, `install` keeps each grant directory your own file already lists and prints how many it found listed. `uninstall` removes only the paths `install` added.

The session-start entry rotates the log past 16 MiB and removes first-call marks older than 7 days. It prints its findings through `additional_context`, which reaches you only if the agent repeats them, so plain `check` prints the same tamper comparison and tool findings to the terminal. It adds the Claude Code and locale environment names of the hook it runs in, which a terminal run cannot know, and `install` prints neither.

## Recovery

The engineer, not the agent, follows the Recovery section. An agent runs no command from that section, retries only where this deny's own text says to, and otherwise stops and reports this deny in chat.

This file lives in the claude-config checkout that `~/.claude`'s stowed files link into, not in the workspace. Your own terminal and `uninstall` are the way out of every entry below. The log is `<routed config dir>/cursor-adapter-state/shim.log`.

A shim deny's text carries an outcome name, as in `outcome pin-check`. Only the entries that cover outcomes name them in backticks, and the entry headed "A deny whose outcome no entry above names" covers the rest.

If `cursor-adapter.py` cannot run, restore `~/.cursor/hooks.json` by hand: copy the bytes of `~/.cursor/cursor-adapter/hooks.json.pre-install` over it, or delete it when `hooks.json.pre-install.none` is the file that exists. The copy discards every entry added to `hooks.json` since install. To keep those entries, delete by hand only the entries whose command holds `/.claude/scripts/cursor-adapter/` under your home directory. The `.none` delete applies only when `hooks.json` holds nothing but those entries. The install record, the state directory and the manifest remain after that restore, so when `cursor-adapter.py` runs again, run `uninstall` to remove them. Until then `check` reports every entry missing, or `hooks.json` as absent after the delete, and its fix sentence says to re-run `install`, which would install again what you just removed.

### The Cursor adapter's hook shim ended without deciding this call

The launch guard prints this text with exit code 2 when the interpreter call ends with no output document. The causes:

- The recorded python3 is missing, not executable or below the Python floor.
- A pull was partial or the checkout is damaged, so `hook-shim.py` is missing, unreadable or does not parse.
- A fault in the shim itself. `status` shows its class beside the interpreter's version.

The way out, in order:

1. Run `check` and read what it prints: the recorded interpreter's path and version, and whether the shim's self-check passes under it, with each failure's check name and class.
2. For an interpreter cause, install a python3 at or above the floor and re-run `install`.
3. For a checkout cause, restore the checkout.
4. Otherwise, or until then, run `uninstall`, or follow the manual restore above.

`install`, `check` and `uninstall` run `cursor-adapter.py`, which needs a python3 at the floor. Under an older one it can stop with Python's own `SyntaxError`, for example when a directory's `.python-version` pin puts a bare `python3` below the floor. Run the file under the recorded interpreter instead: its path is the `python3` entry of `~/.cursor/cursor-adapter/install.json`, and the form is `<recorded python3> -I -S ~/.claude/scripts/cursor-adapter/cursor-adapter.py uninstall`. The manual restore needs no interpreter.

### An install-record deny, or a deny that the hook script is missing or not executable

Outcomes: `install-record`, `script-missing` and `script-not-executable`.

The shim reads `~/.cursor/cursor-adapter/install.json` on every call, and it runs each hook from `~/.claude/hooks/`.

- An install-record deny means the record is missing, unreadable or malformed, as after a deleted `~/.cursor/cursor-adapter/` or a hand edit. Re-run `install`. Re-run it with the same `--config-dir` as before: with no record, nothing names the old routed directory, so a different one leaves the old `cursor-adapter-state` directory behind. A deleted `~/.cursor/cursor-adapter/` also took the saved pre-install file with it, and `install` writes no new one while `hooks.json` still holds an adapter entry, whatever `--config-dir` it gets. If `uninstall` says the record is missing, it removes the entries and leaves the runtime state in place.
- A script-missing or script-not-executable deny means a hook file an entry runs is gone or lost its mode, as after a partial pull. Restore the checkout, or run `./install.sh`, then quit Cursor, re-run `install` and relaunch. `check` names each missing script path.

### A deny from a marker-gated gate

The paragraph before `## Commands` says why they can deny. There are two ways out:

1. Where the conversation's mode lets a Shell call write the routed config dir, run the review skill in that conversation. It writes the marker through `marker.sh`, and the denied operation then goes through on a retry. That holds in Allowlist mode with the sandbox off and in a mode that runs everything unsandboxed. Under Auto-Review (with Sandbox), the write fails while the sandbox grant is dormant (see "A marker write that the sandbox denies").
2. Otherwise, make the denied operation from your own terminal, or run `uninstall`.

### A worktree held by a live process

A write into a linked worktree is denied with "this worktree is held by a live process (pid N, session S) ... ask the engineer in chat, and do not unlock it yourself". The text names the lock's process id, and its session id when the lock records one. An IDE conversation's lock names the `Cursor` main process, so it reads live until Cursor quits, even after the conversation is closed or archived.

Read the process's name with `ps -o comm= -p <pid>`. On macOS the command prints the executable's path, and the adapter compares only its last component. The way out follows that name:

- `Cursor`, the running main process: when you have closed or archived the conversation that holds the lock, run `git worktree unlock <path>` once you confirm nothing works in that worktree, or quit Cursor.
- `cursor-agent`, a worker: its lock outlives Cursor's quit, so end that worker, or run the same unlock.
- `agent`, a CLI process: a Cursor CLI conversation holds the lock while its session runs. End that session, after which the lock reads dead, or work in another worktree.
- Any other name, `claude` among them: a live Claude Code session may hold the worktree. Confirm what the process is, then wait for it to finish, or work in another worktree.

The session the text names can belong to an open ancestor of the denied call, so do not unlock under a working agent. A Claude Code session denied with "already in use by a live Claude Code session" may be meeting a Cursor holder, so read the pid's name as above.

### A host walk that found no host

The sessionStart report says `Host walk: not-found.`, `status` flags it, a `pin-no-host` outcome in the log marks a Shell call whose pin wrote no host process id, and a gated write into a linked worktree is denied with "could not resolve this session's own process identity". The adapter then has no anchor for this conversation.

Three causes are possible:

- A Cursor update renamed the main process or the CLI's launcher, or the CLI was started under another name. List the running processes and find the Cursor main process or the CLI's launcher, then read its name with `ps -o comm= -p <pid>`. On macOS the command prints the executable's path, and the adapter compares only its last component. The adapter matches `Cursor` for the main process and `agent` or `cursor-agent` for the CLI, names observed on macOS. The main process's name on Linux is unrecorded, so a different one there reads as this case.
- A `ps` read failed once, such as under a cap kill.
- The sessionStart report's own walk ran past its 1 s bound, which a loaded machine can cause. Any timeout or failure of that walk reports `not-found`.

Only a new conversation that reports `not-found` again points to a rename. The `status` flag counts the `hostwalk=not-found` lines in the tail it reads of the log and its rotated file, so a one-off failure keeps the flag raised. Compare the flag's conversation count before and after a new conversation.

Until the name is matched, run `uninstall` or make the denied operation from your own terminal, and report the name and Cursor's version.

### `marker.sh` says `SESSION_ID empty` in a Cursor or CLI conversation

Identity did not resolve for that Shell call. The text's pointer to the `capture-session-id.sh` SessionStart hook does not apply under Cursor, where `marker.sh` takes a call's session id from the directory pin's export, not from a `sessions/` registration. It finds the host process id by its own walk up the process ancestry. The pin's exported host process id serves only as the fallback when a `ps` read in that walk fails, as in a sandboxed mode. The agent stops and reports the text in chat. The causes:

- `marker.sh`'s own walk found no host, which the host-walk entry above covers.
- A `ps` read in that walk was denied, as in a sandboxed mode, and the exported host process id is unset or invalid. A call that logged `pin-no-host` exports none.
- The pin did not rewrite the Shell call, so it exports no session id. For that case see the entry headed "A pin-check deny on every Shell call" below.
- A Cursor user on the Claude Code import with no adapter installed: the import's copy of the session hook declines the untranslated Cursor payload, and nothing exports a session id. The way out is to install the adapter, or to run the gated operation from a terminal.

`review-ledger.sh` and the review-pr scripts print the same SessionStart-hook pointer, and the same causes apply.

### A marker write that the sandbox denies

Under Auto-Review (with Sandbox), an agent's `marker.sh` write fails while the sandbox grant is dormant ("Where the state lives"), so a marker-gated gate keeps denying. Switch to Allowlist with the sandbox off, or make the denied operation from your own terminal.

### A hook's own deny names its bash floor or a missing jq, or an exit-nonzero deny with status 126 or 127

Outcome: `exit-nonzero`.

The hooks need bash 5.2 or later and `jq` on the `PATH` `install` recorded, and the shim needs neither. Status 126 or 127 is what the wrapper gives when a hook's shebang interpreter is missing or not executable. Run `check`, fix the tool and re-run `install`. When `check` finds no tool fault, the hook returned that status itself: report the hook's name and status.

### A deny that names a size cap or the deadline

Outcomes: `payload-over-cap`, `hook-output-cap` and `timeout`.

These are limits by design, listed under Limitations. A retry on the same input repeats the deny. Make that Write or commit from your own terminal.

### A start-failed deny

Outcome: `start-failed`.

The shim could not start the hook. The recorded `timeout` or `gtimeout` is the process every hook runs under, so when it is missing, moved or not executable every fail-closed call denies.

- Transient causes, for which the deny invites one retry: `EAGAIN`, `EMFILE`, `ENFILE`, `ENOMEM`.
- For any other cause, `ENOENT` and `EACCES` among them, run `check`. It re-tests the recorded wrapper's path and prints it with the cause. Restore the tool at that path or re-run `install`.
- A cause of `status:125` is the wrapper failing on its own, which `check`'s self-check repeats with the same flags. A one-off 125 with a passing `check` is probably the wrapper's own fork failing, so retry once before `uninstall`. The single retry is a project choice.

### An unenterable-directory deny

Outcome: `unenterable-dir`.

A workspace root was removed, renamed or made unsearchable while the conversation stayed open, and every fail-closed call that resolves to it denies, with text naming the directory. Restore the folder or its permissions, or start a new conversation on a folder that exists.

### A pin-check deny on every Shell call

Outcome: `pin-check`.

Cursor no longer applies the directory pin's rewrite, or something altered the prefix's environment line, so a Shell command reached the check without the exact pin prefix. `status` counts these denies. Run `uninstall` and report the Cursor version.

### A directory-input refusal on every Shell call

Outcomes: `dir-input-refused` and `no-absolute-dir`.

For `dir-input-refused`, calls whose agent set no directory are refused too. A Cursor build now fills the Shell tool's directory input itself. Run `uninstall` and report the finding.

For `no-absolute-dir`, the first workspace root is not an absolute path, or there is no workspace root and `HOME` is unset or not absolute. Open a local folder as the workspace root. Run `uninstall` only if the deny persists in a plain local window.

### A missing-required-key deny on every call of one tool class

Outcome: `missing-key`.

Cursor's payload lacks a key the field table marks `doc-only`. Report the tool class and the key from the deny text.

### An unclassified-tool deny

Outcome: `unknown-tool`.

Cursor sent a tool name that neither the recorded tool map nor a `parity-exceptions.json` `cursor-tool` entry lists, such as a new or renamed tool, or WebFetch or WebSearch under a spelling the files do not hold. Classify the name in the stowed checkout:

- A name added to `parity-exceptions.json` as a `cursor-tool` entry with reason `unsupported` or `declined` attaches no gate. The shim reads that file on each call, so the deny lifts live.
- A name added to `tool-map.json`, or a remapped one, takes effect only after `install`, which records the map. That lifts the unknown-tool deny but attaches no gate: a name that should carry the Edit, Write or other gates also needs an entry in the `CURSOR_TOOL_NAMES` table in `cursor-adapter.py` before `install`. Until `install`, the shim keeps the recorded map, and `check` reports the drift.

### A stale-install deny

Outcome: `stale-install`.

A pull changed the contract version. Quit Cursor, re-run `install` and relaunch.

### A deny whose outcome no entry above names

Outcomes: `payload-malformed`, `session-id-grammar`, `unknown-event`, `stdin-read`, `signal`, `non-json`, `unknown-field` and `tool-table-gap`. A `timeout` whose text says Cursor held its payload stream open belongs here too.

Each is the shim or a hook meeting input it does not translate, or a hook killed by a signal. A retry on the same input repeats the deny. Report the outcome name from the deny text and what `status` shows for it, then make the call from your own terminal or run `uninstall`.

### A deny in a Cursor CLI session beside the installed adapter

A CLI session meets the adapter's gates as an IDE conversation does (see "Cursor CLI beside the adapter"), and the way out depends on the deny's class. Each way out that needs the CLI to run a skill, or to apply the directory pin's rewrite, is pending an unrecorded probe.

- A completion gate's deny, from `require-code-review`, `require-plan-review` or `require-ready-for-review`, has three routes. After the first or second, the CLI retries the gated operation.
  1. Run the review skill in that CLI conversation.
  2. Where the CLI cannot run the skill, end the CLI session and wait until its lock reads dead, then run the same review in an IDE conversation on the same tree and state. These gates match a marker's content under every session suffix (`docs/hooks.md` § "Marker keying and gate-release authority"). While the CLI session is open, its worktree denies the IDE conversation's first write of findings with the live-holder text that names the lock's process id.
  3. While the CLI session is open, make the gated operation from your own terminal.
- An active-bypass gate's deny, from `require-respond-pr` or `require-memory-skill`: run the skill in that CLI conversation. Where the CLI cannot run it, make the gated operation from your own terminal, where no hook runs, or have an IDE conversation make it under its own bypass. Another conversation's skill run releases nothing for the CLI, because a bypass is keyed on its own conversation's session id.
- In no case write a marker for the CLI conversation's session id from anywhere else.
- A deny no IDE call would get, such as `pin-check` or `dir-input-refused` on every CLI call: run `uninstall` and report the finding, at least until a probe records whether the CLI's `beforeShellExecution` receives the rewritten command.

### CLAUDECODE set in Cursor's hook environment

A Cursor started from a Claude Code terminal passes `CLAUDECODE` to its hooks. The import's copies of the hooks then refuse the adapter's pair, the session id and host process id the hooks resolve for a Cursor call, and deny in linked worktrees, and the sessionStart report names `CLAUDECODE` among the Claude Code environment names set in the hook. With `CLAUDECODE` set, the copies' session hooks decline an untranslated Cursor payload only where the host walk finds a host (Limitations' `sessions/` entry). Terminal `check` does not report these names, because it cannot see Cursor's environment, so this entry is reached only through the sessionStart report. Quit Cursor and relaunch it from a shell that has no `CLAUDECODE` set. On macOS, launching it from the Dock does that. On Linux, use the desktop launcher or a shell started outside Claude Code.

## Limitations

- **Locale:** under a C locale, a hook the adapter runs may see a UTF-8 `LC_CTYPE` that the import's copy of it does not. The sessionStart report says whether Cursor's hook environment carries `LANG` or an `LC_` variable, which tells you whether the case applies.
- **Tests:** CI runs the shim's tests on Linux, and macOS runs them only on a contributor's machine.
- **The sessionStart python3 report:** it stands in for `check-python-floor`'s registration, which the adapter declines. Its limits:
  - It probes from `~/.cursor/`, or `$HOME` when `~/.cursor` does not exist yet, while a hook runs in the workspace. A version manager that picks `python3` by directory can pass the probe and give a hook an older one, where `check-python-floor.sh` judges from the session's directory. The hooks' `PATH` holds only recorded directories, python3's first, and the four system directories, so that needs python3's own directory to hold no `python3` and a later recorded tool's path to lie in such a manager's shim directory.
  - It runs once per new conversation, not on each resume.
  - It reaches you only through `additional_context`, so only if the agent repeats it. A native sessionStart value reached the agent in 2 of 3 attempts when observed.
  - It shares the entry's 10 s `timeout` with the tamper comparison and the run-mode line, and Cursor's kill at that timeout costs the whole `additional_context`.
  - A launch-guard fault, such as a missing `cursor-adapter.py`, prints `{}` and no report.
  - Plain `check`, run from the terminal, is the reliable detector, and it runs only when you run it.
- **By-design denies, none a fault:**
  - A payload past 8 MiB.
  - A hook stream past 1 MiB.
- **The deadline:** any gate whose work passes its entry's deadline, 10 s by default, is denied with the timeout outcome under Cursor, and a retry on the same input repeats the deny. Claude Code's default hook timeout is 600 s, as `docs/hooks.md` § "Gate deadlock recovery" states. `require-diff-size-prompt` is the known case, because its state read can take up to about 102 s, as the `_lib_diff_size_gate_state` comment in `claude/.claude/hooks/_lib.sh` states. The way out is your own terminal.
- **What `status` shows of a shim fault:**
  - A fault the shim's handler logs shows by class.
  - A missing or unparseable shim, an unusable python3, a signal death or an unwritable state dir leaves no log line on either tail.
  - An informational entry's `{}` after such a fault leaves none either.
  - `check` is the first step for those.
- **The hook run:**
  - A child the hook leaves in its process group when it exits gets no signal, and a child outside that group gets none from the wrapper. Each runs on until it exits, or until its own cap where it has one.
  - A SIGKILL to the `timeout` wrapper itself from outside, or a stop of it that is never resumed, leaves the hook unbounded.
  - The hook runs on under the wrapper's timer until it ends, after an output-cap deny or after a timeout at the shim's bound. A wrapper that started late gives the second.
- **Your own hooks:** a hook you add to `hooks.json` after install shows in reports as a non-owned entry, counted by event. Note the count in `status` after adding one, because a planted entry under the same event differs from yours only by that count. A "foreign" entry in a report is a different thing: an entry that holds an adapter path without being the adapter's own.
- **Marker-gated gates:** the paragraph before `## Commands` says why they can deny, `docs/hooks.md` describes each gate, and the Recovery section holds the way out. Three residuals follow:
  - A direct `marker.sh write` releases a completion gate with no review in a mode that gives no prompt and needs no grant, `agent -p --force` and the IDE's Run Everything (Unsandboxed), and no adapter entry denies it. For the main agent that stands, as it does in Claude Code's main session. A restricted subagent's write also runs wherever it is unprompted, an allowlisted shape included, until a later gate covers it.
  - Under Auto-Review (with Sandbox), an agent's `marker.sh` write fails while the sandbox grant is dormant, so a marker-gated commit denies in that mode. "Where the state lives" says which mode to use meanwhile.
  - Under Auto-Review (with Sandbox), an agent's `marker.sh status` may delete the conversation's own live active-bypass file, because `_lib_active_bypass_marker_live` removes a file whose recorded process id fails `kill -0`. Whether that sandbox lets a Shell call signal-probe the exported host process id is pending an unrecorded probe.
- **Cursor CLI beside the adapter:** these residuals follow from the rule in "Cursor CLI beside the adapter".
  - A CLI run that a Claude Code session starts takes that session's identity, because the resolver takes a live registered ancestor first. It reads that session's markers and shares its locks. Its calls carry no Claude Code `agent_id`, so a CLI that a Claude Code subagent starts sits outside Claude Code's agent-keyed bars, as the header of `enforce-marker-script-shape.sh` states for a nested `claude -p`.
  - A process named exactly `agent` or `cursor-agent` becomes the host where the walk meets no `Cursor` main. The adapter's entry-command processes between a hook and the CLI carry neither name, so the match passes over them.
  - A `cursor-agent` worker that outlives the app anchors a hook it runs after Cursor quits on itself, and a lock it holds outlives the quit. Whether such a worker runs hooks is pending an unrecorded probe.
  - A lock holder's liveness is a bare `kill -0` on its recorded process id. A short-lived `agent -p` run's id, once reused, makes its lock read as live, a rare deny that clears once the id is free.
  - The entries that rest on `stop`, `beforeSubmitPrompt`, `subagentStart` or `subagentStop` get no event under `agent -p`, which fired none when observed. That is a gap, not a deny.
  - A hook `ask`, such as a `permissions.ask` twin's, may run unprompted under `agent -p --force`, which Cursor documents as "Force allow commands unless explicitly denied". Whether an `ask` prompts there or interactively is pending an unrecorded probe.
  - A call with no `parent_agent_id` meets no attribution deny, and a CLI main agent's calls are such calls if they carry none, as the IDE's never do.
  - Whether a CLI subagent's calls carry `parent_agent_id`, and whether an interactive CLI fires `subagentStart`, is pending an unrecorded probe.
  - An unattributed-subagent deny, if one is added, must handle a CLI subagent call that carries `parent_agent_id` when no `subagentStart` arrives.
  - The CLI's file-tool target forms, which the main-checkout deny's repo resolution rests on, and whether the CLI runs the import's copies of `~/.claude/settings.json`'s hooks, are pending an unrecorded probe. On Linux the CLI's process name may read `node`, which resolves no host and meets the denies Recovery's CLI entry covers. That case has not been run. The IDE's main process name on Linux is also unrecorded, and a name other than `Cursor` resolves no host there either.
- **`sessions/` registrations:** no Cursor IDE or CLI process registers in `sessions/`, because the session hooks decline an untranslated Cursor payload.
  - With `CLAUDECODE` unset, they decline whatever the host walk returns. With it set, they decline only where the walk finds a host, so a copy whose walk returns no host name or fails still writes its entry. The sessionStart report names a set `CLAUDECODE`.
  - A set `CLAUDECODE` has two possible sources. One is a Cursor started from a Claude Code terminal (Recovery's `CLAUDECODE` entry), and relaunching Cursor without it removes that source. The other is a repository whose `.claude/settings.json` `env` block sets it, which no run has observed reaching Cursor's hooks. No relaunch clears that source.
  - A written entry clears once the registered process exits, because the start-time check then rejects it, and no runtime check flags it in between.
  - The decline keys on the payload's shape, so it applies with or without the adapter installed. Without it, `marker.sh` run from an agent Shell call has no identity, because the session id that gives one is exported only under the adapter.
  - Nothing prunes `sessions/`, so an entry from an earlier checkout stays until its process exits. The relaunch after `install` covers Cursor processes only, so a CLI process that outlives the install keeps its old entry resolving until it exits.
- **`nudge-handoff-near-context-cap.sh --check`:** it reads `sessions/<pid>` through a walk of its own and finds no entry for a Cursor or CLI process, so under the adapter it prints `cannot-resolve`. That is a gap, not a deny.
- **Credential redaction:** `redact-credential-values.sh` runs but redacts no tool output under Cursor, MCP output included. Cursor accepts a replacement tool output for `MCP:` tools only, and the hook's matcher selects no `MCP:` tool.
- **Protections that do not run:** an entry that `claude/.claude/scripts/cursor-adapter/parity-exceptions.json` lists as `pending-phase`, `unsupported` or `declined` is not carried through the adapter. That holds for a registration, a `permissions.deny` entry and an adapter gate. `gate-settings-permissions.sh` carries the `settings.json` `permissions.deny` Bash and Read entries, and Cursor does not enforce them until the phase the file names. `gate-agent-tools.sh` and `gate-credential-exposure.sh` are pending the same way. The Limitations list of the `cursor-adapter/` entry in `docs/scripts.md` § "Script reference" names the shim's own limits, among them the subagent bars and the `stop` and `subagentStop` deny.
- **What the reports cannot see:**
  - `status` cannot tell a Cursor that stopped loading `hooks.json` from a Cursor you did not use.
  - The sessionStart check lives in the file it checks, so removing that file or that entry silences it.
  - `status`'s flags are known to trip without a fault. The log-rotation flag can trip during one long healthy conversation, and each flag says what to do.
