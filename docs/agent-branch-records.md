# Agent branch records

With the `agent_branch_records` key on, `cleanup-merged-branches.sh` and `cleanup-idle-open-pr-worktrees.sh` copy a worktree's gitignored `agent-reviews/` directory into an archive before `git worktree remove` deletes it. The key is a per-account `bool` in `claude-config.toml` and defaults to `false`. The archive root is `~/.local/state/agent-branch-records/`, fixed in code.

## Setup

The commands take `<volume path>` as a bare word, so quote it if it has a space, as in `/Volumes/My Drive`. With no larger volume, run `mkdir -p ~/.local/state/agent-branch-records`, skip steps 1 and 2 and continue at step 3. Moving an existing root to another volume is manual and not covered here.

Before step 5, exclude the root, and the step 1 target if you use a volume, from any sync or backup client that copies off the machine. Snapshots hold finding text that can quote secrets.

1. Create the target on the volume first, because a link to a missing target reads as a missing sentinel: `mkdir <volume path>/agent-branch-records`. Also run `mkdir -p ~/.local/state`, since a macOS home may lack it.
2. Link the root to it: `ln -s <volume path>/agent-branch-records ~/.local/state/agent-branch-records`. The root must not exist yet, because `ln -s` onto an existing directory links inside it.
3. Create the sentinel with `touch ~/.local/state/agent-branch-records/.agent-branch-records-root`, only after confirming the volume is mounted and never from an agent session. A sentinel on a bare mountpoint passes the check while the volume is absent.
4. Smoke-test the copy on the target volume, from your own terminal as your own user, because `Archive: ready` does not check that `cp -RPp`, `diff -r` and `mv` work there. A failing command means the volume cannot hold the archive. Delete the scratch directory either way:

   ```bash
   scratch="$HOME/.local/state/agent-branch-records/smoke-test"
   mkdir -p "$scratch/sample/dir" && printf 'text\n' > "$scratch/sample/file"
   chmod 644 "$scratch/sample/file" && chmod 755 "$scratch/sample/dir"
   cp -RPp "$scratch/sample" "$scratch/copy" && diff -r "$scratch/sample" "$scratch/copy" && mv "$scratch/copy/file" "$scratch/copy/renamed"
   rm -r "$scratch"
   ```

5. Set `agent_branch_records = true` in the account's `claude-config.toml`, export that account's own `CLAUDE_CONFIG_DIR`, and confirm that `~/.claude/scripts/config-get.sh agent_branch_records` exits 0. Then run a cleanup script with `--dry-run` and check that the `Archive: ready (<account directory>)` line names the account you enabled.

## What a run prints

- `Archive: ready (<account directory>)` means the key is on and the root passed its checks. It does not promise that a copy will succeed.
- `Archive: unavailable (<reason>)` on `--dry-run` previews the refusal and exits 0.
- A real run that finds the archive unavailable prints the reason on stderr and exits 1 before removing anything in that repo. Under `--all-projects`, the sweep continues with the next repo and the overall exit is 1.
- A worktree whose copy fails prints `kept (archive failed: <reason>)` and stays, and the script exits 1 after its summary. `cleanup-merged-branches.sh` also keeps that worktree's local and remote branch.
- `cleanup-merged-branches.sh --list-removable` prints no `Archive:` line. When the archive is unavailable, it prints no list line for that repo, names the reason on stderr and exits 1.

## When a script refuses

A cleanup script that cannot archive a worktree keeps it and exits 1. The first column is the printed reason.

| Reason | Fix |
|---|---|
| `the config directory or the archive setting could not be resolved` | Use an absolute `CLAUDE_CONFIG_DIR`, set `HOME`, or repair the install: the config schema is unreadable, lacks the key's row, or the config directory cannot be canonicalized. |
| `the archive root is missing its readiness marker` | Create the sentinel (step 3).<br>If the root is a dangling link, mount the volume first.<br>If `HOME` is unset, set it. |
| `a filesystem call under the archive root did not answer in time` | A call hit the 5-second cap, usually a stalled mount: check the volume and rerun. |
| `the archive root is not writable` | Fix the root's mode, or remount it writable. |
| `this account's archive directory is not writable` | Fix its mode, or remount the volume writable. |
| `the worktree's repository root or ledger key could not be resolved` | Repair the worktree so `git rev-parse --show-toplevel` succeeds, then rerun. |
| `agent-reviews is not a plain directory, or holds an entry that is neither a regular file nor a directory` | Remove or replace each entry that `find agent-reviews ! -type f ! -type d` lists, and make `agent-reviews` itself a plain directory. |
| `an archive directory could not be created` | Check the volume's space and permissions. |
| `the snapshot directory could not be created` | Check the key directory's permissions. |
| `copying agent-reviews failed` | Free space, fix an unreadable file, or rerun the step 4 smoke test. |
| `the snapshot copy of agent-reviews differs from the original` | Rerun, and if it repeats, run the step 4 smoke test. |
| `the snapshot manifest could not be written` | Check the volume's space and permissions, then rerun. |
| `the archive setting in claude-config.toml could not be read as true or false` | You make these fixes, and an agent only reports the refusal. A `_config.sh: warning: skipping malformed line` on stderr names the line.<br>A line that mentions the key but does not parse, or whose value is not bare `true` or `false`: fix that line and restore the value you set.<br>`claude-config.toml` is unreadable: restore read permission.<br>A directory on its path is not searchable: restore search permission.<br>A regular file sits where a directory belongs on its path: replace it with a directory.<br>A dangling or looping link sits on its path: repair the link or fix `CLAUDE_CONFIG_DIR`.<br>A directory or FIFO sits at its path: replace it with a regular file. |

## Fail direction

- Key off: the scripts print no `Archive:` line and refuse nothing, though `_config.sh` still warns on stderr about any bad line in `claude-config.toml`. A config directory or schema that cannot be resolved (relative `CLAUDE_CONFIG_DIR`, unset `HOME`, unreadable `config-keys.psv`) is the exception and refuses regardless of the key.
- A key that cannot be read as on or off refuses: a malformed or non-boolean `agent_branch_records` line, or a `claude-config.toml` that cannot be read (see the last table row).
- Some failures read as off and remove worktrees with no snapshot, and a missing `Archive: ready` line is the sign of each. The cases:
  - A misspelled key name, including `agent-branch-records`, is not detected, and only `_config.sh`'s stderr warning marks the line.
  - A transient read failure inside the config reader reads as off.
  - A stat error such as EIO, ESTALE or ETIMEDOUT under a searchable parent directory reads as off.
- The copy steps are not time-capped. A stalled volume hangs the run but never reaches removal.

## Snapshots

- Account directory: `<root>/<account>/`, where `<account>` is the config directory's canonical path with `%` written as `%25` and `/` as `%2F`.
- Snapshot: `<account directory>/branches/<repo-hash>.<branch-hash>/<UTC stamp>.<pid>.<n>/`, holding `agent-reviews/` (absent when the worktree had none) and `manifest.json`.
- A snapshot is complete only once its `manifest.json` exists, because the manifest is written last.

To find one, search `<root>/*/branches/*/*/manifest.json` for the `branch` and `main_worktree_path` fields. No script restores a snapshot, so restoring is a manual `cp -R` of its `agent-reviews/`.

Account directories are not an access boundary, because every account runs as the same OS user. Snapshots are created owner-only, which a volume that ignores POSIX modes does not enforce. The manifest is a plain JSON file that any process of the same OS user can write.

## Known limits

- The ledger sweep deletes `review-narrative-ledger/*.jsonl` files after at least 30 days without an append, and this archive does not cover them.
- The archive is not crash-consistent, because nothing flushes the copy to disk before removal. A power loss or kernel panic soon after a run can leave empty files in a snapshot that reads as complete.
- A worktree removed by other means gets no snapshot, such as a `--list-removable` consumer or a manual `git worktree remove`.
- `cleanup-merged-branches.sh` reads the key and account from each repo's own environment, so a repo on a key-off account is removed with no snapshot and no output.
