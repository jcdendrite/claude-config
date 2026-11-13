# Agent branch records

The `agent_branch_records` key makes a cleanup script copy a worktree's per-branch review data into an archive before it removes the worktree. The data lives in the worktree's gitignored `agent-reviews/` directory, which `git worktree remove` deletes along with the worktree.

Four names recur below:

- **Key:** `agent_branch_records`, a per-account `bool` in `claude-config.toml` that defaults to `false`.
- **Root:** `~/.local/state/agent-branch-records/`, fixed in code. You relocate it with a symlink to a larger volume (see Setup).
- **Sentinel:** a file named `.agent-branch-records-root` that you create in the root by hand. Every archive call checks it once before any write, so an unmounted volume makes the scripts refuse instead of delete.
- **Account directory:** `<root>/<account>/`, where `<account>` is the account's config directory, its canonical path with symlinks resolved, with `%` written as `%25` and `/` as `%2F`. An account's snapshots live under it.

The archive holds only a worktree's `agent-reviews/`. The ledger sweep deletes each `review-narrative-ledger/*.jsonl` file once it has gone 30 days without an append, with no copy, whether the key is on or off. To keep a ledger file, copy it by hand from `<config dir>/review-narrative-ledger/` into the archive root before it reaches 30 days without an append, where `<config dir>` is `CLAUDE_CONFIG_DIR`, or `~/.claude` when it is unset or empty.

## Setup

The commands below take `<volume path>` as a bare word, so quote it when the path contains a space, as in `/Volumes/My Drive`. With no larger volume, create the root as a plain directory with `mkdir -p ~/.local/state/agent-branch-records`, skip steps 1 to 3 and continue at step 4.

1. Create the target directory on the larger volume first (`mkdir <volume path>/agent-branch-records`), because a link to a missing target leaves a dangling root that reads as a missing sentinel. Run `mkdir -p ~/.local/state` as well, since a macOS home may lack it.
2. When `~/.local/state/agent-branch-records` already exists as a directory, move every entry it holds into the volume directory with `find ~/.local/state/agent-branch-records -mindepth 1 -maxdepth 1 -exec mv -- {} <volume path>/agent-branch-records/ \;`. That also moves a dot-named sentinel, which a `*` glob skips. Then remove the emptied directory with `rmdir`, and link only once that succeeds, since a failed `rmdir` means an entry was left behind. `ln -s` onto an existing directory creates the link inside it, and the sentinel would then sit on the home volume.
3. Make the root a symlink to that directory: `ln -s <volume path>/agent-branch-records ~/.local/state/agent-branch-records`. A plain directory at the root sits on the volume that cleanup is meant to relieve, where `ENOSPC` makes every archive fail.
   - Skip this step once the root is already a symlink to the volume directory, because GNU `ln -s` onto a linked root creates a stray self-referential link inside the volume directory.
   - To relocate to another volume, create the new directory as step 1 says and move every entry, the sentinel included, from the old target directory into it as step 2 does. Then repoint the link with `ln -sfn <new volume path>/agent-branch-records ~/.local/state/agent-branch-records`, only after the move succeeds.
4. Create the sentinel only after confirming the volume is mounted, and never from an agent session, because a sentinel on the bare mountpoint passes the gate while the volume is absent: `touch ~/.local/state/agent-branch-records/.agent-branch-records-root`.
5. Smoke-test the copy on the target volume, from your own terminal and as your own user, because `Archive: ready` does not check that `cp -RPp`, `diff -r` and `mv` work there. The sample tree holds a mode 0644 file and a mode 0755 directory, and a failing command means the volume cannot hold the archive. Delete the scratch directory afterwards either way:

   ```bash
   scratch="$HOME/.local/state/agent-branch-records/smoke-test"
   mkdir -p "$scratch/sample/dir" && printf 'text\n' > "$scratch/sample/file"
   chmod 644 "$scratch/sample/file" && chmod 755 "$scratch/sample/dir"
   cp -RPp "$scratch/sample" "$scratch/copy" && diff -r "$scratch/sample" "$scratch/copy" && mv "$scratch/copy/file" "$scratch/copy/renamed"
   rm -r "$scratch"
   ```

6. Set `agent_branch_records = true` in the account's `claude-config.toml`, export that account's own `CLAUDE_CONFIG_DIR`, then confirm that `~/.claude/scripts/config-get.sh agent_branch_records` exits 0, which means the key is on. When the key reads as off, a malformed line naming the key or an unreadable config file makes the archive unavailable (see Fail direction). A cleanup script's `--dry-run` prints `Archive: ready (<account directory>)` once the key reads as on and the sentinel and write probes pass. That line does not mean a copy will succeed, because a directory, copy or manifest refusal can still follow. It prints `Archive: unavailable (<reason>)` when the archive cannot be written or the key's state could not be read.

Choose a volume as trusted as `$HOME`, because the archive holds finding text that can quote you, plus branch names and worktree paths.

Archived findings can contain secrets quoted from reviewed code. Rotating a flagged secret does not remove its archived copies, and deleting archived copies is manual.

### Security properties

- On a volume that ignores POSIX modes, such as FAT, exFAT, NTFS or many SMB mounts, any other local account can read, modify or replace the archive unless the volume is remounted with your own uid and gid and an owner-only umask.
- No check refuses a symlink at the account directory, `branches` or a key directory. An account that can write the root or an account directory can therefore redirect later snapshots to a directory it controls.

## Snapshots

A snapshot is a verified copy of one worktree's `agent-reviews/` plus a `manifest.json`.

- **Path:** `<account directory>/branches/<repo-hash>.<branch-hash>/<UTC stamp>.<pid>.<n>/`. The key directory's two hashes are the SHA-256 of the worktree's `git rev-parse --show-toplevel` and of the branch name, the formula the review ledger's branch-scope file names use. The directory holds `agent-reviews/` when the worktree had one, and `manifest.json`.
- **Completeness:** a snapshot is complete if and only if its `manifest.json` exists, and the manifest is written last. Readers ignore a snapshot directory without one.
- **Manifest fields:**
  - `schema_version`, which is `1`.
  - `archived_at`, in UTC as `YYYY-MM-DDTHH:MM:SSZ`.
  - `trigger`, the calling script's file name.
  - `branch`, `tip_sha` (empty when the branch does not resolve) and `worktree_path`, as `git worktree list --porcelain` reports it.
  - `main_worktree_path`.
  - `ledger_repo_root`, the string that was hashed into the key.
  - `ledger_key`.
  - `agent_reviews`: `copied`, or `absent` when the worktree had no `agent-reviews/`.
- **Finding one:** search `<account directory>/branches/*/*/manifest.json` for the `branch` field. No cleanup script restores a snapshot, so restoring is a manual `cp -R` of the snapshot's `agent-reviews/` into the worktree.
- **Reuse:** when the newest complete snapshot under the key directory has the worktree's current `tip_sha`, `ledger_key` and `agent_reviews` value, and its copy still matches the worktree's `agent-reviews/`, the script reuses it and adds none. Any other outcome makes a new snapshot, except a cap kill in the reuse check, which refuses with the stalled reason and makes none. A worktree git refuses to remove, such as a dirty one, is archived before the refusal, so repeated runs rely on this reuse to avoid adding a snapshot each time.

## The cleanup scripts

With the key on, `cleanup-merged-branches.sh` and `cleanup-idle-open-pr-worktrees.sh` archive each worktree right before they run `git worktree remove` on it. A worktree whose archive fails is kept, and the script exits 1 after its summary. `cleanup-merged-branches.sh` also keeps that worktree's local and remote branch.

| Output | Meaning |
|---|---|
| `Archive: ready (<account directory>)` | A real run or `--dry-run` found the key on and the root usable. It does not promise a copy will succeed, because a later directory, copy or manifest refusal can still keep a worktree. |
| `Archive: unavailable (<reason>); a real run removes nothing ...` | `--dry-run` found the archive unavailable. The preview continues and exits 0. |
| `archive:        <snapshot path>` (merged), `<branch>: archived to <snapshot path>` (idle) | The snapshot that holds the worktree's current state, new or reused. |
| `worktree:       kept (archive failed: <reason>)` (merged), `<branch>: kept (archive failed: <reason>)` (idle) | The archive refused. The reason is one in the table under "When a script refuses". |
| `Skipped (archive failed): <branch>` | `cleanup-merged-branches.sh` prints one per kept branch after its summary. |

- **Unavailable on a real run:** the script prints the reason on stderr and exits 1 before it classifies or removes anything in the repo it is processing (see Fail direction for the conditions). Under `--all-projects`, a repo processed earlier keeps whatever it already removed. The refusal is per repo, so the sweep continues with the next repo and the overall exit is 1. `cleanup-idle-open-pr-worktrees.sh` checks before its first `gh` call.
- **List mode:** `cleanup-merged-branches.sh --list-removable` prints no `Archive:` line, because its stdout carries list lines only. When the archive is unavailable it prints no list line for that repo, names the reason on stderr and exits 1. Under `--all-projects`, earlier repos have already printed their list lines, so discard the list on any nonzero exit. The real run can still keep a listed worktree whose archive fails. A consumer that removes a listed worktree itself gets no snapshot, because the key covers only the cleanup scripts' own `git worktree remove` calls.
- **Key off:** the output is unchanged and nothing under the root is read or written, except when a condition in Fail direction makes the archive unavailable.
- **Account under `--all-projects`:** `cleanup-merged-branches.sh` reads the key and resolves the account directory after it loads each repo's environment. `cleanup-idle-open-pr-worktrees.sh` loads no repo environment, so it always uses the invoking environment's account. For `cleanup-merged-branches.sh`:
  - Without `direnv`, every repo uses the invoking environment's account.
  - With `direnv`, a repo whose allowed `.envrc` sets `CLAUDE_CONFIG_DIR` archives into that account.
  - With `direnv`, any other repo gets the environment that `direnv`'s resync leaves. If the invoking shell's `CLAUDE_CONFIG_DIR` was itself set by `direnv`, the resync can revert it to its earlier value for a repo outside that `.envrc`'s tree, usually unset, which resolves to the default account (`$HOME/.claude`). This resync behavior is unverified.
  - **Warning:** if that default account has the key off, that repo's worktrees are removed with no snapshot. The only sign is a missing `Archive: ready` line under that repo's banner.

## When a script refuses

A cleanup script that cannot archive a worktree keeps it and exits 1. An agent that sees a refusal reports it to you. It never creates the root or the sentinel, and never changes the key.

| Reason | Cause and fix |
|---|---|
| `the config directory or the archive setting could not be resolved` | `CLAUDE_CONFIG_DIR` is relative, `HOME` is unset with no `CLAUDE_CONFIG_DIR`, the config schema is unreadable or lacks the key's row, or the config directory cannot be canonicalized. Fix the install. |
| `the archive root is missing its readiness marker` | The sentinel is missing or is not a regular file: create it as Setup step 4 says.<br>The root is a dangling symlink because its target volume is absent: mount the volume, then create the sentinel as Setup step 4 says.<br>`HOME` is unset with `CLAUDE_CONFIG_DIR` set, so the root resolves under `/`: set `HOME`. Mounting the volume and creating the sentinel cannot clear this cause. |
| `a filesystem call under the archive root did not answer in time` | Check the volume and rerun.<br>A call under the root hit the 5-second cap, usually a stalled mount. The cap needs `timeout` or `gtimeout` on PATH (README.md Requirements lists `timeout`).<br>A stall outside the cap hangs the script instead of producing this refusal, so interrupt the run.<br>A snapshot directory with no `manifest.json` is incomplete and safe to delete (see "Platforms and retention"). |
| `the archive root is not writable` | No account directory exists yet and the root fails a write probe. Fix the root's mode. On a volume that ignores POSIX modes, the cause is usually a read-only mount or the mount's uid, gid or umask options. |
| `this account's archive directory is not writable` | Fix its mode. On a volume that ignores POSIX modes, the cause is usually a read-only mount or the mount's uid, gid or umask options. |
| `the worktree's repository root or ledger key could not be resolved` | The worktree has a `.git`, but `git rev-parse --show-toplevel` fails or prints nothing, so repair the worktree. Hashing failed because `sha256sum` printed nothing, so check that it runs. An empty argument is a bug in the calling script, so report it. |
| `agent-reviews is not a plain directory, or holds an entry that is neither a regular file nor a directory` | List the refused entries with `find agent-reviews ! -type f ! -type d` from the worktree, and remove or replace each one.<br>`agent-reviews` itself is a symlink or a regular file: remove or replace it.<br>The scan of `agent-reviews` or of the snapshot copy failed, for example on an unreadable subdirectory: that `find` prints an error on stderr and no entry.<br>An entry appeared during the copy: the worktree may already be clean, so rerun. |
| `an archive directory could not be created` | The account directory, `branches` or a key directory is missing and could not be made. Check the volume's space and permissions. A stalled volume can also cause this. |
| `the snapshot directory could not be created` | The snapshot directory could not be made, or its timestamp could not be read. Check the key directory's permissions. A stalled volume can also cause this. |
| `copying agent-reviews failed` | The copy exited nonzero, typically `ENOSPC` or an unreadable file. On a volume that ignores POSIX modes, `cp -RPp` can also fail because the volume refuses a mode or ownership change, which the Setup smoke test checks. It can also fail on a file name the volume does not allow, such as one with `:` or `?` or a trailing dot, which the smoke test does not cover. The partial copy is removed. |
| `the snapshot copy of agent-reviews differs from the original` | The copy finished but did not match the source. The partial copy is removed. |
| `the snapshot manifest could not be written` | The manifest could not be written or renamed into place, or its timestamp could not be read. A stalled volume can also cause this. The partial copy is removed. |
| `the archive setting in claude-config.toml could not be read as true or false` | The engineer makes every fix below. An agent reports the printed reason and the stderr warning line, and changes nothing. A `_config.sh: warning: skipping malformed line in <path>: <line>` on stderr just above the refusal prints the line to fix. With no such warning, check `ls -ld` on `<config dir>/claude-config.toml` and each parent (`<config dir>` is defined at the top of this page). The cause is one of those below.<br>The config reader could not parse a line containing the text `agent_branch_records`, or the key's line holds a value other than bare `true` or `false`: fix the line the stderr warning prints, which may only mention the key name, then restore the value the engineer set, asking the engineer if unsure which value they set. The config reader's rules for that line are:<br>- A line it cannot parse is skipped, and a trailing comment makes a line unparseable.<br>- A value that is not bare `true` or `false` is rejected, so a quoted `"true"` makes the line malformed.<br>- Case folds in the value only, and spaces around `=` are tolerated.<br>- The key must be exactly `agent_branch_records`, and a differently cased key reads as off.<br>The file cannot be read: restore read permission on `claude-config.toml`.<br>A directory on the file's path cannot be searched: restore search permission on it. A directory that can be searched but not read does not refuse.<br>A regular file sits where a directory is expected on the path above `claude-config.toml`: replace it with a directory.<br>A dangling link or a self-looping link sits on the path above the file or at the `claude-config.toml` path: repair the link or fix `CLAUDE_CONFIG_DIR`. Do not replace a link with an empty directory, which hides the real config.<br>A directory or FIFO sits at the `claude-config.toml` path: replace it with a regular file. |

By default the Claude Code sandbox denies writes outside the session's working directory, added directories and `$TMPDIR`, so under it an archive write typically fails and the worktree is kept. Run cleanup from a terminal outside Claude Code.

## Fail direction

An unavailable archive keeps every worktree. These are the conditions that make it unavailable:

- Whatever the key is set to, `_config.sh` cannot resolve the config directory, cannot read the schema, or finds no row for the key. The worktrees stay kept until the install is fixed or the change reverted.
- With the key on, the sentinel is missing, the root is not writable or, once the account directory exists, that directory is not writable, a probe under the root does not answer in time, or the config directory cannot be canonicalized.
- With the key off, `claude-config.toml`:
  - cannot be read.
  - has a directory on its path that cannot be searched.
  - has a regular file where a directory is expected on its path.
  - has a dangling or self-looping link on its path.
  - has a directory or other non-file at its path.
  - holds a line containing the text `agent_branch_records` that the config reader cannot parse.
  - holds a line for the key whose value reads as other than `true` or `false`.
- The key-off check runs only when the key reads as off. A `= true` line followed by a malformed duplicate still reads as on and archives, because the last conforming line wins.

These cases leave the archive available:

- A misspelled key name, the hyphenated form `agent-branch-records` included, reads as off and is not detected. Only `_config.sh`'s stderr warning marks that line.
- A missing config directory, or a missing `claude-config.toml` under a searchable directory, reads as off, with an absent `Archive: ready` line as the only sign.
- An internal failure of the config reader's own resolver makes it return the key's schema default, which is false, so the key reads as off and the worktree is removed unarchived. The same absent `Archive: ready` line is the only sign.
- The config reader's own read of the file swallows a transient I/O failure, so that failure can still read as off. A stat error under a searchable parent other than EACCES, ELOOP or ENOTDIR (for example EIO, ESTALE or ETIMEDOUT) can still read as off too. This is a residual, not a guarantee.
- List mode prints no `Archive:` line, so it gives no sign that the key read as on.
- A failed stat of `agent-reviews` or `.git` for want of search permission on the worktree or an ancestor reads as `absent`. The same permissions stop `git worktree remove` deleting the entry, so the findings stay on disk. With an unsearchable ancestor, git may still unregister the worktree, which leaves a directory holding findings that the snapshot records as `absent`.
- An unreadable `agent-reviews` or an unreadable entry inside it refuses instead (see the `agent-reviews is not a plain directory` and `copying agent-reviews failed` rows).

## Removing the key

When the key leaves the schema, delete the `agent_branch_records` line from each account's `claude-config.toml`, because `_config.sh` warns on every read of a line for an unrecognized key.

## Platforms and retention

- CI checks Linux only, so macOS is unverified beyond the path name.
- Verification compares the copy through the page cache and is not a durability guarantee, so a power loss or unplugging the volume shortly after a removal can lose the snapshot.
- Nothing prunes the archive, which keeps finding text until you delete it. Deleting `<root>/<account>/` removes one account's archive. A snapshot directory with no `manifest.json` is incomplete, and deleting it is safe.
