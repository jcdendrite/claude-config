# Agent branch records

The `agent_branch_records` key makes a cleanup script copy a worktree's per-branch review data into an archive before it removes the worktree. The data lives in the worktree's gitignored `agent-reviews/` directory, which `git worktree remove` deletes along with the worktree.

Four names recur below:

- **Key:** `agent_branch_records`, a per-account `bool` in `claude-config.toml` that defaults to `false`.
- **Root:** `~/.local/state/agent-branch-records/`, fixed in code. You relocate it with a symlink to a larger volume (see Setup).
- **Sentinel:** a file named `.agent-branch-records-root` that you create in the root by hand. Every archive call checks it once before any write, so an unmounted volume makes the scripts refuse instead of delete.
- **Account directory:** `<root>/<account>/`, where `<account>` is the account's config directory, its canonical path with symlinks resolved, with `%` written as `%25` and `/` as `%2F`. An account's snapshots live under it.

The archive holds only a worktree's `agent-reviews/`. The ledger sweep deletes each `review-narrative-ledger/*.jsonl` file once it has gone 30 days without an append, with no copy, whether the key is on or off. Until that changes, copy such a file by hand into the archive root before then if you want to keep it.

## Setup

1. Create the target directory on the larger volume first (`mkdir <volume path>/agent-branch-records`), because a link to a missing target leaves a dangling root that reads as a missing sentinel. Run `mkdir -p ~/.local/state` as well, since a macOS home may lack it.
2. When `~/.local/state/agent-branch-records` already exists as a directory, move every entry it holds into the volume directory with `find ~/.local/state/agent-branch-records -mindepth 1 -maxdepth 1 -exec mv -- {} <volume path>/agent-branch-records/ \;`. That also moves a dot-named sentinel, which a `*` glob skips. Then remove the emptied directory with `rmdir`, and link only once that succeeds, since a failed `rmdir` means an entry was left behind. `ln -s` onto an existing directory creates the link inside it, and the sentinel would then sit on the home volume.
3. Make the root a symlink to that directory: `ln -s <volume path>/agent-branch-records ~/.local/state/agent-branch-records`. A plain directory at the root sits on the volume that cleanup is meant to relieve, where `ENOSPC` makes every archive fail.
4. Create the sentinel only after confirming the volume is mounted, and never from an agent session, because a sentinel on the bare mountpoint passes the gate while the volume is absent: `touch ~/.local/state/agent-branch-records/.agent-branch-records-root`.
5. Set `agent_branch_records = true` in the account's `claude-config.toml`, export that account's own `CLAUDE_CONFIG_DIR`, then confirm that `~/.claude/scripts/config-get.sh agent_branch_records` exits 0, which means the key is on. A malformed line reads as off. A cleanup script's `--dry-run` prints `Archive: ready (<account directory>)` once the key reads as on and the sentinel and write probes pass. That line does not mean a copy will succeed, because a directory, copy or manifest refusal can still follow. It prints `Archive: unavailable (<reason>)` when the archive cannot be written or the key's state could not be read.

Choose a volume as trusted as `$HOME`, because the archive holds finding text that can quote you, plus branch names and worktree paths.

Archived findings can contain secrets quoted from reviewed code. Rotating a flagged secret does not remove its archived copies, and deleting archived copies is manual.

### Security properties

- On a volume that ignores POSIX modes, such as FAT, exFAT, NTFS or many SMB mounts, any other local account can read, modify or replace the archive unless the volume is remounted with your own uid and gid and an owner-only umask.
- No check refuses a symlink at the account directory, `branches` or a key directory. An account that can write the root or an account directory can therefore redirect later snapshots to a directory it controls.

## When a script refuses

A cleanup script that cannot archive a worktree keeps it and exits 1. An agent that sees a refusal reports it to you. It never creates the root or the sentinel, and never changes the key.

| Reason | Cause and fix |
|---|---|
| `the config directory or the archive setting could not be resolved` | `CLAUDE_CONFIG_DIR` is relative, `HOME` is unset with no `CLAUDE_CONFIG_DIR`, the config schema is unreadable or lacks the key's row, or the config directory cannot be canonicalized. Fix the install. |
| `the archive root is missing its readiness marker` | The sentinel is missing or is not a regular file. A dangling root symlink, whose target volume is absent, is one cause. Mount the volume, then create the sentinel as Setup step 4 says. An unset `HOME` with `CLAUDE_CONFIG_DIR` set is another, because the root then resolves under `/`. Set `HOME`, since mounting the volume and creating the sentinel cannot clear that cause. |
| `a filesystem call under the archive root did not answer in time` | A call under the root hit the 5-second cap, usually a stalled mount. The cap needs `timeout` or `gtimeout` on PATH (README.md Requirements lists `timeout`). Check the volume and rerun. A stall outside the cap hangs the script instead of producing this refusal, so interrupt the run. A snapshot directory with no `manifest.json` is incomplete and safe to delete (see the last bullet of Platforms and retention). |
| `the archive root is not writable` | No account directory exists yet and the root fails a write probe. Fix the root's mode. On a volume that ignores POSIX modes, the cause is usually a read-only mount or the mount's uid, gid or umask options. |
| `this account's archive directory is not writable` | Fix its mode. On a volume that ignores POSIX modes, the cause is usually a read-only mount or the mount's uid, gid or umask options. |
| `the worktree's repository root or ledger key could not be resolved` | The worktree has a `.git`, but `git rev-parse --show-toplevel` fails or prints nothing, so repair the worktree. Hashing failed because `sha256sum` printed nothing, so check that it runs. An empty argument is a bug in the calling script, so report it. |
| `agent-reviews is not a plain directory, or holds an entry that is neither a regular file nor a directory` | List the refused entries with `find agent-reviews ! -type f ! -type d` from the worktree, and remove or replace each one. The value also covers an `agent-reviews` that is a symlink or a regular file. It also fires when the scan of `agent-reviews` or of the snapshot copy fails, for example on an unreadable subdirectory, where that `find` prints an error on stderr and no entry. It also fires when an entry appears during the copy, in which case the worktree may already be clean and a rerun is the remedy. |
| `an archive directory could not be created` | The account directory, `branches` or a key directory is missing and could not be made. Check the volume's space and permissions. A stalled volume can also cause this. |
| `the snapshot directory could not be created` | The snapshot directory could not be made, or its timestamp could not be read. Check the key directory's permissions. A stalled volume can also cause this. |
| `copying agent-reviews failed` | The copy exited nonzero, typically `ENOSPC` or an unreadable file. The partial copy is removed. |
| `the snapshot copy of agent-reviews differs from the original` | The copy finished but did not match the source. The partial copy is removed. |
| `the snapshot manifest could not be written` | The manifest could not be written or renamed into place, or its timestamp could not be read. A stalled volume can also cause this. The partial copy is removed. |

By default the Claude Code sandbox denies writes outside the session's working directory, added directories and `$TMPDIR`, so under it an archive write typically fails and the worktree is kept. Run cleanup from a terminal outside Claude Code.

## Fail direction

- A malformed or unreadable enabling line reads as off, with only `_config.sh`'s stderr warning. The scripts then behave as with the key off.
- An unavailable archive keeps every worktree. That includes an account whose key is off, when `_config.sh` cannot resolve the config directory or read the schema, until the install is fixed or the change reverted.
- A missing or unreadable config directory or `claude-config.toml` reads as off, with an absent `Archive: ready` line as the only sign.
- List mode prints no `Archive:` line, so it gives no sign that the key read as on.
- A failed stat of `agent-reviews` or `.git` for want of search permission on the worktree or an ancestor reads as `absent`. The same permissions stop `git worktree remove` deleting the entry, so the findings stay on disk. With an unsearchable ancestor, git may still unregister the worktree, which leaves a directory holding findings that the snapshot records as `absent`.
- An unreadable `agent-reviews` or an unreadable entry inside it refuses instead (see the `agent-reviews is not a plain directory` and `copying agent-reviews failed` rows).

## Removing the key

When the key leaves the schema, delete the `agent_branch_records` line from each account's `claude-config.toml`, because `_config.sh` warns on every read of a line for an unrecognized key.

## Platforms and retention

- CI checks Linux only, so macOS is unverified beyond the path name.
- Verification compares the copy through the page cache and is not a durability guarantee, so a power loss or unplugging the volume shortly after a removal can lose the snapshot.
- Nothing prunes the archive, which keeps finding text until you delete it. Deleting `<root>/<account>/` removes one account's archive. A snapshot directory with no `manifest.json` is incomplete, and deleting it is safe.
