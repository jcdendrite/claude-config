# Archive branch records before removal

## Context

Goal: copy a branch's per-branch review data (the worktree's `agent-reviews/` directory) to a relocatable archive root before a cleanup script removes the worktree, instead of destroying it. Archiving the narrative-ledger files the sweep deletes is the follow-up plan's part.

Ask: from the prior session's handoff, relaying the engineer's words from the session before it: "I don't want that path hard-coded in claude-config"; "That should be a config key I think, you're right. And if that's a config key then we probably don't need the tty"; "we should bring that in scope" (the idle-worktree cleanup script); the ledger sweep "should be a config key". This session the engineer revised the first of these: "We can just hard code the path and use something reasonable. I think that's fine as long as the path is valid on both Linux and macOS". On size, first "we don't have to split. It's okay to keep it as is", then at the larger projection "Ask the architect if it should be split or if some of the test should be cut", and the architect recommended splitting D3 out and cutting tests and design pieces, which the engineer accepted via label "All of it (Recommended)": this plan covers D1 and D2, and D3 (the ledger sweep) moves to a follow-up plan. Root directory name: the engineer selected the option label "agent-branch-records (Recommended)" this session.

Why now: the existing cleanup scripts reclaim disk space, but they run `git worktree remove` on worktrees whose gitignored `agent-reviews/` data then disappears. Losing this data blocks later analysis of review yield.

Intended outcome: with one per-account config key enabled, both cleanup scripts first write a verified copy of a worktree's `agent-reviews/` under a fixed root (`$HOME/.local/state/agent-branch-records/`). The engineer relocates that root with a symlink to a larger volume, and a sentinel file at the root gates writes so an unmounted volume makes the scripts refuse rather than delete. Transcript archiving, de-provisioning paths in other repos, and the machine notes file are out of scope for this phase.

## Approach

With the per-account `agent_branch_records` key on, both cleanup scripts copy a worktree's `agent-reviews/` and a manifest into a snapshot directory under `$HOME/.local/state/agent-branch-records/<account>/` before every `git worktree remove`. A worktree whose state matches its newest complete snapshot reuses that snapshot instead of adding one. A sentinel file the engineer creates at the root gates every write. The script keeps a worktree and exits nonzero when any of these holds:
- the sentinel is missing;
- the account directory is not writable (the root, while that directory does not exist yet);
- the root does not answer within the cap;
- the config dir or schema cannot be resolved;
- the key reads as off and `claude-config.toml` cannot be proven absent or readable, or holds a line naming the key that the config reader skips or reads as other than true or false (`KEY_UNREADABLE`);
- any archive step fails, a copy that fails verification included.

When the key reads as off, a `claude-config.toml` whose state cannot be determined, or a line naming the key that the config reader skips or reads as other than true or false, makes the archive `unavailable` (row 105). Every enabled real run and dry run prints an `Archive: ready` line to show the key read as on. List mode prints none, because its stdout carries list lines only. Every snapshot lands at a new path that its own `mkdir` creates, failing on any existing entry, and is never rewritten, so no lock is needed. The archive holds only `agent-reviews/`. Archiving the narrative-ledger files before the sweep deletes them moves to a follow-up plan (row 54).

**Archive layout.** `<root>` is `$HOME/.local/state/agent-branch-records`. It is fixed in code (row 30), and the engineer relocates it with a symlink (row 31).
- `<root>/.agent-branch-records-root` is the sentinel. The engineer creates it once by hand. No script ever creates it or `<root>`, and no script output names a command that would.
- `<root>/<account>/`: `<account>` is the canonical (`cd -P`) config dir from `_lib_config_dir`, percent-encoded into one path component (`%` to `%25`, then `/` to `%2F`). The scripts never use `mkdir -p`, so a missing `<root>` is never created. M3 states how the account directory is created.
- `<account>/branches/<repo-hash>.<branch-hash>/<UTC stamp>.<pid>.<n>/` is one snapshot per distinct worktree state, made by step 6's `mkdir`. It holds `agent-reviews/` when copied, and `manifest.json`. A snapshot is complete if and only if `manifest.json` exists, and the manifest is written last.
- `<UTC stamp>` is `date -u +%Y%m%dT%H%M%SZ`. `<pid>` is `$$`, which a subshell shares with its parent script. `<n>` is a counter the lib keeps per shell. So names are distinct within one shell and one second, but not guaranteed across subshells. A collision between subshells fails closed, because `mkdir` fails on the existing path and the worktree is kept.
- A snapshot directory with no `manifest.json` is incomplete, and readers ignore it. When one outlives its run, as after a kill, the engineer may delete it.

**Manifest fields.**
- `schema_version`: 1.
- `archived_at`: UTC, as `date -u +%Y-%m-%dT%H:%M:%SZ` prints it. `review-ledger.sh:547` already writes that form.
- `trigger`: the calling script's file name.
- `branch`.
- `tip_sha`: empty when unresolvable.
- `worktree_path`: the porcelain path.
- `main_worktree_path`.
- `ledger_repo_root`: the string that was hashed.
- `ledger_key`.
- `agent_reviews`: `copied` or `absent`.

The manifest is built with `_lib_jq -n --arg`, written to `manifest.json.tmp`, then renamed with `mv`.

**New library `claude/.claude/scripts/_branch-records-lib.sh`.** The caller sources `hooks/_lib.sh` first.
- Every fallible step checks its own status with `if` or `||`. The scripts call `branch_records_status` bare under `set -euo pipefail` (row 49), and test `branch_records_archive_worktree` with `if`, where errexit is off inside the function (row 59).
- Neither function depends on the caller's `pipefail`, because `review-ledger.sh`, which the D3 follow-up will make source this lib, runs without it (row 33).
  - Every status a step acts on comes from one command's own capture, as `out=$(cmd) || status=$?`, never from a pipeline.
  - Each capture is assigned apart from its `local` declaration, because `local x=$(...)` masks the status.
- Every external command that touches `<root>` runs under `_lib_capped`, which caps it at 5 seconds (row 42). A cap kill counts as a failure. Three exceptions run uncapped:
  - the `agent-reviews/` copy, step 6's rescan of it, and the `diff -r` comparisons, for the reason under Alternatives;
  - step 8's `rm -rf` of this call's partial copy, whose size is unbounded like the copy it undoes, for the same reason;
  - the shell builtins that touch `<root>`, the `[ -d ]` tests and the manifest's `>` redirect. Each runs only after the capped sentinel probe has answered in the same status call, which every archive call re-runs at step 1.
- Every `chmod` is best-effort. Its status, a cap kill included, is ignored, and no refusal depends on the mode it leaves (row 58).
- Neither function changes the caller's cwd, umask or `CDPATH` (M16).
  - Each `cd` runs in a `$(...)` subshell as `CDPATH='' cd -P -- <dir> && pwd -P`. That is the form `worktree_canon_path` uses, except that a failure or an empty result fails the call instead of falling back to the raw path (row 65).
  - Each `umask 077` runs in a subshell around one `mkdir`, as `_lib.sh:5738` does.
- No `BRANCH_RECORDS_REASON` value names the root path, the sentinel's file name, or a command that creates either. The doc's reason table is the one place that names a fix.
- Each `BRANCH_RECORDS_REASON` value is a fixed one-line string held in one lib variable per cause, and the functions set the reason only from these variables. Each variable is named `_BRANCH_RECORDS_REASON_<CAUSE>`, for these thirteen causes:
  - `CONFIG`: `_config_enabled` returns 2, 3 or 4, `_lib_config_dir` fails, or `cd -P` cannot canonicalize the config dir;
  - `SENTINEL`: the sentinel is missing or not a regular file;
  - `STALLED`: a capped call in the status probes or the reuse check was killed at the cap;
  - `ROOT_NOT_WRITABLE`: no account directory exists and the root fails `test -w`;
  - `ACCOUNT_NOT_WRITABLE`: the account directory fails `test -w`;
  - `LEDGER_KEY`: step 1 finds an empty argument, or step 3 cannot resolve `ledger_repo_root` or `ledger_key`;
  - `UNSUPPORTED_ENTRY`: step 2 refuses `agent-reviews` or an entry in it, or step 6's rescan refuses an entry in the copy;
  - `DIRECTORY`: step 4 leaves a directory missing;
  - `SNAPSHOT_MKDIR`: step 6's stamp capture fails or prints nothing, or its `mkdir` exits nonzero or is killed at the cap;
  - `COPY_FAILED`: `cp` exits nonzero;
  - `COPY_MISMATCH`: step 6's `diff -r` exits nonzero;
  - `MANIFEST`: step 7's `archived_at` capture fails or prints nothing, or writing or renaming the manifest fails;
  - `KEY_UNREADABLE`: `_config_enabled` returns 1 and `_branch_records_toml_confirms_off` returns 1. That is any state of `claude-config.toml` it cannot prove absent or readable: an unsearchable config dir or ancestor, a dangling or looping symlink, a non-file at the path, or a read failure. It is also a read file with a line naming the key that `_config.sh` skips or reads as other than true or false.
- `branch_records_status` always returns 0. It sets `BRANCH_RECORDS_STATE` (`disabled`, `ready` or `unavailable`), `BRANCH_RECORDS_REASON` and `BRANCH_RECORDS_ACCOUNT_DIR`. It creates nothing.
  - It runs `local key_status=0`, then `_config_enabled agent_branch_records || key_status=$?`. Each status then maps to one outcome:
    - Status 0 goes on to the root check.
    - Status 1 is `disabled`, unless `_branch_records_toml_confirms_off` returns 1, which is `unavailable` with `KEY_UNREADABLE`.
    - Status 2, 3 or 4 is `unavailable` with `CONFIG`.
  - `_lib_config_dir` has one call site, after `_config_enabled`. A failure there is `unavailable` with `CONFIG`, as its call-site contract requires (`_config.sh:146-151`).
  - `_config.sh` skips a malformed line, so the key resolves to `false` and `_config_enabled` returns 1. The helper reads `claude-config.toml` again with `cat` and reuses `_config_line_key_value` to find that line. It does not use `_config_file_lines`, which cannot report a read failure.
  - `_branch_records_toml_confirms_off` returns 0 only in two cases:
    - the toml provably does not exist, because the nearest existing ancestor of its path is a searchable directory;
    - the file was read and holds no line naming the key that `_config.sh` skips or reads as other than `true` or `false`, with case folded and spaces around `=` optional.
  - It returns 1 for everything else: an unsearchable config dir or ancestor, a dangling or looping symlink, a non-file at the path, a read failure, or such a line. A skipped line holding `agent_branch_records` as a substring also returns 1.
  - A `= true` line with a malformed duplicate reaches status 0, so it stays `ready`, because the last conforming line wins.
  - A misspelled key name is not detected, and neither is a hyphenated `agent-branch-records`, which this plan judges the likeliest typo. It is a known residual to fix in a separate change, not an accepted one: the engineer said so, and the PR body names it as a follow-up (row 108). [unverified: the likelihood is the plan's own judgment, with no source]
  - The `disabled` outcome rests on the config reader's own read of the file, which the helper can veto but not contradict. A transient I/O failure on `_config_enabled`'s own read yields an empty file, because the config reader swallows read failures. The helper's second read can then succeed and find no skipped line. A stat error under a searchable parent other than EACCES, ELOOP or ENOTDIR (EIO, ESTALE, ETIMEDOUT) can likewise read as an absent file. Both need an unreliable home volume. The plan documents this as a residual and adds no code for it. [unverified: no deterministic trigger found]
  - A missing config dir or `claude-config.toml` under a searchable directory stays `disabled` (row 61 and `_config_location_value`'s `[ -f ]` fallback, `_config.sh:626-629`). Row 38 covers only an absent or unreadable state file.
  - An internal resolver failure of the config reader falls back to the schema default `false`, so the key reads as off and removal proceeds unarchived (row 39).
  - Two arms get no test, because nothing but a race reaches them after `_config_enabled` returns:
    - the failure of the `cd -P` canonicalization (row 61);
    - the failure of the `_lib_config_dir` call, because `_config_resolve` returns 1 only after a non-empty `primary_dir` from the same call (`_config.sh:897`).
  - `unavailable` also covers a sentinel that is not a regular file, a failed `test -w` probe, and a cap kill on any probe. The `test -w` probe tests the account directory when it is already a directory, and the root only when it is not, because once the account directory exists no step writes the root (row 76).
  - The probes run in this order: the capped sentinel test, then the builtin `[ -d <account dir> ]` that picks the `test -w` target, then the capped `test -w`. So no builtin touches `<root>` before a capped probe has shown the volume answers.
  - It reads `HOME` as `${HOME:-}`, so an unset `HOME` under `set -u` yields a root with no sentinel, which reads as `unavailable`, instead of aborting.
- `branch_records_archive_worktree WORKTREE BRANCH MAIN_WORKTREE TRIGGER` empties `BRANCH_RECORDS_SNAPSHOT` and `BRANCH_RECORDS_REASON` on entry. It returns 0 when the state is `disabled`, leaving `BRANCH_RECORDS_SNAPSHOT` empty, or when a complete snapshot holds the worktree's current state, setting `BRANCH_RECORDS_SNAPSHOT` to it. Otherwise it returns 1 and sets `BRANCH_RECORDS_REASON`.
  1. Re-run `branch_records_status`. `unavailable` returns 1. Then an empty WORKTREE, BRANCH, MAIN_WORKTREE or TRIGGER returns 1 as `LEDGER_KEY`, creating nothing. The check follows the status re-run, so a disabled key still returns 0 and an unavailable archive reports its own reason first.
  2. Classify `WORKTREE/agent-reviews`.
     - A directory that is not a symlink is scanned first with `find`, without `-L`. Any entry that is neither a regular file nor a directory returns 1, whatever `cp` or `diff` would do with it (row 25). So does a `find` that exits nonzero, as on an unreadable subdirectory.
     - Nothing at that path is `absent`. A symlink there, whatever its target, returns 1, and so does any other file type.
     - A missing worktree directory reads as `absent`. So does a stat that fails for want of search permission on the worktree or an ancestor. The same permission stops `git worktree remove` deleting the entry, so the findings stay on disk. With an unsearchable ancestor, git may still unregister the worktree (rows 99, 100).
  3. Resolve `ledger_repo_root`, `ledger_key` and `tip_sha`. Both `git rev-parse` calls run under `_lib_capped`.
     - `ledger_repo_root` is `git -C WORKTREE rev-parse --show-toplevel` when `[ -e WORKTREE/.git ]` holds, and WORKTREE otherwise.
       - Without a `.git`, git walks up to the enclosing repo (row 43). So a dangling `.git` symlink, or a `.git` hidden by missing search permission, makes WORKTREE the ledger root.
       - When `.git` exists and `rev-parse` fails, prints nothing or is killed at the cap, return 1 as `LEDGER_KEY`, creating nothing. An empty string would still hash to a valid-looking key.
     - `ledger_key` comes from `_lib_reviewer_round_state_key_for_branch`, and its failure returns 1 as `LEDGER_KEY`.
     - `tip_sha` is `git -C MAIN_WORKTREE rev-parse --verify -q "refs/heads/BRANCH^{commit}"`. A failure, a cap kill included, records it empty, as for a branch that does not resolve.
  4. Prepare the account directory (M3). Take the account directory, `branches` and the key directory in turn, and skip one that is already a directory. Otherwise run `( umask 077; _lib_capped mkdir -- <dir> )`. When that fails, re-test `[ -d ]`, because a concurrent first run may have just made it, and return 1 only when the directory is still missing. Then `chmod -- 700` the account directory.
  5. Reuse check. Every miss below falls to step 6, which makes and verifies a fresh copy, so a removal never rests on a doubtful snapshot. Only a cap kill, which says the volume is stalling, returns 1 instead of going on to the uncapped copy.
     - Capture `_lib_capped find <key dir> -mindepth 2 -maxdepth 2 -name manifest.json -type f` with its own status. A status consistent with a cap kill returns 1, and any other nonzero status is a miss.
     - The candidate is the last line of the captured text, sorted with `LC_ALL=C sort`. A directory without `manifest.json` is never a candidate, so an incomplete newest directory gives way to the newest complete one. No candidate is a miss.
     - One `_lib_jq -e` call compares the candidate's `tip_sha`, `ledger_key` and `agent_reviews` with this call's values.
       - Every nonzero status not consistent with a cap kill is a miss, an unparsable manifest included.
       - A candidate that does not match gives the `select` no output, so `jq -e` exits 4 for it, not 1.
       - `worktree_path` is recorded but not compared, for the reason under Alternatives (row 87).
     - When the candidate's stored `agent_reviews` is `copied`, a `diff -r` of the snapshot's `agent-reviews/` against the worktree's that exits nonzero is a miss. A missing or short copy therefore never matches.
     - A match sets `BRANCH_RECORDS_SNAPSHOT` to that snapshot and returns 0.
  6. Make and verify the snapshot.
     - Build the path `<key dir>/<UTC stamp>.<pid>.<n>` in a local variable. Capture the stamp with its own status, and a failed or empty capture returns 1 as `SNAPSHOT_MKDIR`. `<n>` is a lib global incremented on every step-6 entry, named outside the `_BRANCH_RECORDS_REASON_` prefix so M17's enumeration does not read it.
     - Run `( umask 077; _lib_capped mkdir -- <path> )`, without `-p`, as step 4 does. A nonzero status, a cap kill included, returns 1 as `SNAPSHOT_MKDIR` and removes nothing.
       - Exit 0 shows this call created an empty directory at exactly that path, because `mkdir` fails on any existing entry, a symlink included (row 92), so the path needs no further check.
       - Unlike step 4, step 6 never re-tests `[ -d ]` after a failed `mkdir`, and decides on `mkdir`'s exit status alone.
     - Copy with `cp -RPp WORKTREE/agent-reviews <snapshot>/agent-reviews`, naming the source without a trailing slash and a destination that does not exist yet.
     - Rescan the copy with step 2's `find` predicate. A refused entry in the copy returns 1 as `UNSUPPORTED_ENTRY`, because an entry added to the worktree between the pre-scan and the copy is copied as itself and `diff -r` follows links (row 101).
     - Compare the two with `diff -r`.
  7. Capture `archived_at` with its own status. Then write the manifest temp file, run `chmod -R -- go-rwx` on the snapshot so the chmod covers the manifest too, then `mv` the manifest into place.
  8. On any failure after step 6's `mkdir` returned 0, `rm -rf --` the path step 6 built. This call's `mkdir` created it, so the removal reaches only this call's partial copy, as after `ENOSPC`. The removal runs uncapped and its own status is ignored, so the original reason stands. Then return 1. A leftover is the incomplete snapshot directory described in the Archive layout, a directory with no `manifest.json` that readers ignore.

**`cleanup-merged-branches.sh`.**
- Source the lib after the existing lib sources at :172-182, unguarded like them, because `set -euo pipefail` (:170) stops the script on a failed source (row 33).
- In `run_repo_cleanup`, right after the environment re-check at :373-378, call `branch_records_status`. The check runs per repo because `load_repo_environment` can change `CLAUDE_CONFIG_DIR` (row 9). The account it selects owns that repo's snapshots (row 46).
  - `unavailable` under `--list-removable`: one stderr line naming the reason, no stdout line, exit 1.
  - `unavailable` on a real run: one stderr line naming the reason and `docs/agent-branch-records.md` and saying to ask the engineer, then exit 1 before any classification or deletion.
  - No refusal line names the sentinel path, a command that creates it, or the key's off setting.
  - `ready` on a real run or under `--dry-run` prints `Archive: ready (<account dir>)`, the signal that the key read as on. Under `--list-removable` it prints nothing, because that stdout carries list lines only.
  - `--dry-run` with `unavailable` prints `Archive: unavailable (<reason>); a real run removes nothing in this repo and exits 1`, then previews as today and exits 0.
  - `disabled` prints nothing, so default output does not change.
- In the real run's `*` arm, call `branch_records_archive_worktree` before the unlock at :1128-1134. That arm reaches `dirty` and `submodules` too, by design (row 48), and the reuse check stops a repeated refusal from adding snapshots.
  - On failure, print `    worktree:       kept (archive failed: <reason>)`, record the branch, and `continue`. That also keeps the local and remote branch.
  - On success with a snapshot, print `    archive:        <snapshot path>`.
- After the end-of-run summary, print `Skipped (archive failed): <branch>` per kept branch, and exit 1 when there is one. Under `--all-projects` the sweep then prints its `WARNING: cleanup failed in <repo>` line (:1302) and moves to the next repo.
- `worktree_removal_verdict` stays unchanged. Archive availability is account state, not worktree state, and the real run, dry run and list each need their own arm either way.
- Header:
  - The exit-code table gains the `unavailable` refusal and the kept-archive exit 1. It names the kept archive as an exception to the clause that a handled per-branch failure does not trip exit 1 (:151-152).
  - The list-mode notes gain the `unavailable` exit 1. The known-divergence paragraph (:98-103) gains that a listed worktree can still be kept by a failed archive.
  - One line cites `docs/agent-branch-records.md`.

**`cleanup-idle-open-pr-worktrees.sh`.**
- Source `hooks/_lib.sh` (:83) in place of `hooks/_config.sh`. `_lib.sh` sources `_config.sh` first (`_lib.sh:25`), so the floor check still runs before anything else. Then source the lib after `_worktree-lib.sh` (:87), unguarded like it (row 33).
- After argument validation (:101-119) and before the `gh` prerequisite checks (:144-153), call `branch_records_status`.
  - `unavailable` on a real run prints the refusal line described above and exits 1, before any `gh` call.
  - `--dry-run` with `unavailable` prints the `Archive: unavailable` line and continues.
  - `ready` prints the `Archive: ready` line on a real run and under `--dry-run`.
- In the removal loop, archive before each `git worktree remove`.
  - On failure, print `  <branch>: kept (archive failed: <reason>)` and skip the removal. After `print_summary`, exit 1 when any was kept.
  - On success with a snapshot, print `  <branch>: archived to <snapshot path>`.
- Header:
  - The exit-code table (:70-77) gains both exit-1 cases, plus one line citing `docs/agent-branch-records.md`.
  - The live-worktree window paragraph (:42-50) gains one sentence. With the key on, each earlier candidate's archive runs between the process snapshot and a later candidate's removal, which lengthens the window, and `agent-reviews/` data written after a candidate's copy is lost with its worktree.
  - No fresh `worktree_in_use` check is added (Out of scope, row 66).

**Config key.** Append `agent_branch_records|bool|false|config-dir|false|config-dir|||Agent branch-records archive|docs/agent-branch-records.md|`. The empty prompt-description keeps it out of `install.sh`'s prompt (row 17), so no prompt or install logic changes. Only count comments change.

**The four design questions.**
1. **How a worktree's ledger file is keyed.**
   - Branch scope is `<repo-hash>.<branch-hash>.jsonl`. Both halves are sha256 of raw strings: the worktree's `git rev-parse --show-toplevel`, and the branch name (row 10).
   - Session scope is `<repo-hash>.<session-id>.jsonl`, written while the worktree is detached or on the default branch (row 11).
   - The branch half can be recomputed at removal time; the session half cannot. So the snapshot directory takes the branch key, and the manifest records `ledger_key` and `ledger_repo_root`, whose sha256 is the repo half.
   - Nothing known at removal joins a session-scope file to a branch, because ledger rows carry no branch (row 13), so the manifest lists no session files (row 71).
   - Removal deletes no ledger file (row 14), so removal copies no ledger content. The manifest is the only record joining a hashed name to a branch (row 13), which the D3 follow-up's archived ledger files will rely on.
2. **Locking: none.** No snapshot is ever rewritten. Each lands at a path that step 6's `mkdir` either creates or fails on, so two writers cannot clobber each other: a colliding name makes the later `mkdir` fail, which keeps that worktree. Each is complete only once its manifest is renamed into place. Two runs archiving one unchanged worktree at once can each miss the other's snapshot in the reuse check. At worst they leave two complete snapshots of one state, which a reader can drop as duplicates. M4 records the lock primitives checked.
3. **Per file, not batch.** Moved to the D3 follow-up plan, since it concerns only the ledger sweep.
4. **`--list-removable` and the dry run when the root is unavailable.**
   - List mode prints no list line and one stderr line, then exits 1. Its stdout claims what a real run removes, which is nothing, and the nonzero exit tells a consumer to discard the list instead of reading "nothing to reclaim".
   - The dry run prints the `Archive: unavailable` line, previews as usual and exits 0, because a preview carries no completeness contract.
   - With `ready`, the list is unchanged.
   - The real run can still keep a listed worktree whose archive fails, and the header says so.

   Neither list mode nor the dry run writes under `<root>`.

**Alternatives set aside.**
- A path-valued config key: the engineer chose a hard-coded path, "We can just hard code the path and use something reasonable" (row 30). It would also need a path type in `config-keys.psv`'s grammar, which admits only `bool` and `enum:` today (`claude/.claude/hooks/config-keys.psv:15-20`).
- `${XDG_STATE_HOME:-$HOME/.local/state}`: the engineer's selection named a root under `$HOME/.local/state/` (row 1). Nothing in the repo reads `XDG_STATE_HOME`, and a value that differs between shells would split one account's archive across two roots.
- An environment variable naming the root: it carries the same split-root risk as `XDG_STATE_HOME`, and row 30 already settles a fixed path.
- A root inside each config dir: it would drop the `<account>` encoding, but moving the archive to a larger volume would then take one symlink and one sentinel per account instead of one of each.
- `[ -d <root> ]` or `mountpoint(1)` in place of a sentinel: an unmounted mountpoint is still a directory, and `mountpoint` is Linux-only and wrong for a root that is a subdirectory or symlink on the volume.
- Staging a snapshot directory and renaming it into place: `mv` onto an existing directory moves into it. Writing the manifest last needs only a file rename.
- Copying the ledger file at removal time: removal deletes no ledger (row 14), and archiving what the sweep deletes is the D3 follow-up's job.
- Refusing an account directory that is a symlink, is not owned by the caller, or is not mode 0700 after `chmod`, as `_lib.sh:5738-5744` does for its private directory (row 45). A target volume that ignores POSIX modes (row 57) reports an owner and mode the caller cannot change, so the owner and mode checks would refuse every archive there. No code can make files owner-only on a volume that ignores modes (row 58). The symlink refusal does not depend on modes, and the engineer chose to leave it out too (row 63).
- A runtime notice when the account directory keeps group or other bits after `chmod`: the engineer declined it (row 62).
- Sandbox grant text in the doc. The doc ships none (row 93) and carries no sandbox note. A sandboxed agent's own way to a real run is an unsandboxed retry (row 83). That retry passes every archive gate, so it weakens the sandbox, not the archive. Two grants are set aside:
  - A `sandbox.filesystem.allowWrite` entry, for each account directory or for the whole root. Three reasons:
    - The engineer does not use sandboxed sessions today (row 94), so a grant's spelling, path form and settings scope would ship untested in a public doc.
    - A sibling worktree's removal may fail under the sandbox with the archive granted (row 96).
    - A whole-root entry would put the root and the sentinel inside every sandboxed command's write set.

    Out of scope defers a verified setup.
  - `permissions.additionalDirectories`: it is the wider grant, opening the root and the sentinel to Claude's file tools as well (row 64).
- Comparing `worktree_path` in the reuse check. Both scripts pass the porcelain path, which is stable for one worktree, and the key directory is already named from `--show-toplevel`, so no production input makes `worktree_path` the only difference. Where one worktree is named under two spellings, reuse is still correct, because `tip_sha` matches and `diff -r` verifies the content. The comparison would only add a duplicate snapshot. Its case also rested on an unverified premise about `--show-toplevel` through a symlink (rows 79, 87).
- Reason literals written only in the tests: a reworded or added lib reason would then drift from the doc's table with nothing to catch it. Reading the lib's variables back through `${!_BRANCH_RECORDS_REASON_@}` ties the lib, the tests and the table together, without the source scan `claude-skills/skills/test-conventions/SKILL.md` § "9. Common authoring mistakes" allows only as a tripwire.
- Probing the root's writability on every status call: once the account directory exists no step writes the root (row 76), so that probe would test a directory the run never writes. It would refuse a run whose account directory is writable under a read-only root, and would pass an unwritable account directory through to a later `mkdir` failure with a less specific reason.
- A `ledger_files` manifest field naming every `<repo-hash>.*.jsonl` file: it would name every branch's and session's file in the repo, so it joins no session file to a branch. As a reuse field it would also change with unrelated sessions and with the sweep (row 71).
- Naming the first refused entry in the pre-scan's reason: a file name can hold a newline or control character that breaks the one-line output. The doc's reason table names the `find` command that lists every such entry.
- A finer snapshot stamp: within one second the newest pick can land on an older snapshot, and the reuse check returns it only when it matches, so the cost is one duplicate snapshot (row 69).
- Re-checking the sentinel before the manifest `mv`: a volume lost mid-run takes with it the snapshot directory step 6's `mkdir` made there, so the manifest write already fails. Each call's step-1 status re-run catches a root lost between worktrees.
- Archiving a symlinked `agent-reviews` as an uncopied link: a link there is rare. Refusing it keeps the worktree for the engineer to resolve, and saves one arm, one test and one manifest value.
- Refusing an `absent` reading unless a listing of the worktree's parent proves the entry is missing. That layer was built during code review and then trimmed (row 99). A stat that fails for want of search permission leaves the entry on disk, because the same permission stops `git worktree remove` deleting it (row 100), so in the shapes probed the layer guarded no loss. The doc's Fail direction states the residue: with an unsearchable ancestor git may still unregister the worktree, which leaves findings on disk that the snapshot records as `absent`.
- Explicit guards for an empty, unset or relative `$HOME`: the root such a `$HOME` yields has no sentinel, so the status already reads `unavailable`, though its reason then reports a missing sentinel rather than the bad `$HOME`.
- A `branches/index.jsonl`: it would be a second copy of every manifest, and `find <account>/branches -name manifest.json` rebuilds it.
- A basename or a hash for `<account>`: a basename can collide, and a hash is unreadable.
- Putting the helpers in `_worktree-lib.sh` or `_lib.sh`. `_worktree-lib.sh`'s domain is worktree inspection, and `review-ledger.sh`, which the D3 follow-up will make source this lib, does not source `_worktree-lib.sh`. `.claude/rules/bash-unit-test-seams.md` reserves `_lib.sh` for helpers two or more hooks need, and no hook needs these. A new `_<topic>-lib.sh` is that rule's third option.
- Only an up-front refusal, or only per-worktree checks: the per-repo check gives one clear refusal before any work, and the per-call re-check catches a root lost mid-run.
- Detecting a skipped enabling line inside `_config.sh`: it would change, for one key, the resolver every hook sources and its stated warn-and-skip rule (`_config.sh:368-371`). The lib helper detects the skipped line instead, and the archive reads `unavailable` with `Archive: ready` absent. For a misspelled key name, a missing toml and a missing config dir, the `Archive: ready` line and the setup doc's `config-get.sh` check remain the signal.
- Skipping the archive when the verdict predicts git's refusal (`dirty`, `submodules`): the `*` arm leaves that call to git by design (row 48), and a wrong prediction would remove a worktree with no snapshot.
- A documented manual prune in place of the reuse check: every refused run would still add a snapshot until someone prunes, on the volume the change exists to relieve.
- A free-space floor before each copy: a copy that hits `ENOSPC` already fails and its partial snapshot is removed, and no source grounds a threshold.
- Capping the `agent-reviews/` copy, its rescan, its comparisons and the removal of a partial copy: they run only in the manually invoked cleanup scripts, right after a capped probe shows the volume answers. No source grounds a budget for a tree of unknown size, and the removal of a partial copy shares that size with the copy it undoes. No cap interrupts uninterruptible kernel I/O (row 42).
- A caller-side manifest check before each removal: it would restate the lib's completeness rule in both callers, and the lib tests pin the return contract.
- `mktemp -d` for the snapshot directory: the path it prints is step 8's deletion target, so the lib would first have to prove that path is an empty directory it just created under the key directory. A lib-built name passed to `mkdir` needs no such check, because `mkdir`'s exit 0 already shows it (rows 91, 92).

**Dispatch split.** Critical files names two `code-writer` dispatches and each one's files.

### Assumption ledger

**Root:** With archiving off, today's only behavior, `git worktree remove` in either cleanup script deletes the worktree's gitignored `agent-reviews/` directory. Nothing keeps a copy, so later analysis of review yield loses that data. The fix copies first and never removes a worktree whose copy it cannot make and verify. The verification is a `diff -r` comparison read through the page cache. Nothing flushes the copy before the removal, a limit `docs/agent-branch-records.md` § "Platforms and retention" states (Out of scope, `fsync`). The ledger sweep's deletion of `review-narrative-ledger/*.jsonl` files after 30 days without an append is the same problem, left to the D3 follow-up plan (row 54).

**Givens:**
- G1. `git worktree remove` without `--force` deletes ignored files along with the worktree. Git owns this. [unverified: git-worktree(1) not read this session. `_worktree-lib.sh:284-295` models the refusal on `git status --porcelain`, which lists no ignored file, and `.gitignore:41` ignores `agent-reviews/`] The permission-hidden archive case's control worktree asserts it on Linux: its ignored `agent-reviews/finding.md` is gone after `git worktree remove` (`claude/.claude/scripts/tests/test_branch_records_archive.py:262-270`). [verified: the assertion read; unverified: not run by the architect]
- G3. Under the Claude Code sandbox, writes outside the working directory, added directories and `$TMPDIR` are denied. Anthropic owns this. [verified: `docs/case-studies/claude-code-sandbox-write-boundary.md:149-158`]

**Rows:**
1. [engineer-verified: "agent-branch-records (Recommended)"], the option label selected for the root's directory name under `$HOME/.local/state/`. Covers: that directory name. The "(Recommended)" description was the dispatching session's own. [unverified: relayed]
2. "I don't want that path hard-coded in claude-config"; "That should be a config key I think, you're right. And if that's a config key then we probably don't need the tty"; "Your original design was better"; "we should bring that in scope" (the idle-worktree script); the ledger sweep "should be a config key". [engineer-verified: "Yes, I said them"], the selected label answering /plan-review's question whether the engineer said these five quotes in those words. Covers: that these are the engineer's wording from the earlier session. Row 30 supersedes the first quote.
3. Reading of row 2: "that path" is the larger volume's location. The symlink keeps that location out of claude-config, so a fixed default root agrees with rows 1 and 2. No step prompts on a TTY. [unverified: the architect's reading] Row 30, the engineer's answer this session, settles the fixed root whichever path row 2 meant.
4. The sentinel gate, the refusal (keep the item, exit nonzero) when the root is unavailable, a bool config-dir key defaulting to false, and the phase-one scope were the prior session's proposals. The engineer did not object to them. [unverified: not an engineer decision]
5. The key is named `agent_branch_records`, after row 1's term, not the proposed `branch_metadata_archive`. [unverified: the architect's choice]
6. The decision on which accounts enable the key is still open. The default is false everywhere. [unverified: relayed as undecided]
7. `cleanup-merged-branches.sh`'s only `git worktree remove` is at :1136, in the real run's `*` arm, after the unlock at :1128-1134. A failed remove `continue`s past both branch deletions (:1161). [verified: `claude/.claude/scripts/cleanup-merged-branches.sh:1114-1164`]
8. `cleanup-idle-open-pr-worktrees.sh`'s only remove is at :415. It has no list mode. It sources `hooks/_lib.sh` at :83, which sources `hooks/_config.sh` first (`_lib.sh:25`). [verified]
9. `run_repo_cleanup` evals the repo's `direnv export bash` before any branch work (`load_repo_environment` at :336-340, called at :366), so a repo's `.envrc` can change `CLAUDE_CONFIG_DIR` per repo. [verified for the eval; unverified that any `.envrc` sets it]
10. A branch-scope ledger file is `<repo-hash>.<branch-hash>.jsonl` (`_lib.sh:5662-5679`, `:5233-5240`). Both halves are sha256 of the raw string (`_lib.sh:647-651`, `:1209-1215`), and the repo string is `git rev-parse --show-toplevel` (`review-ledger.sh:230-238`). [verified]
11. A session-scope ledger file is `<repo-hash>.<session-id>.jsonl`, used on a detached HEAD or the default branch (`_lib.sh:5627-5679`). Session ids hold only letters, digits, `_` and `-` (`_lib.sh:3464-3474`), so no stem followed by `.` prefixes another stem. [verified]
12. Whether `--show-toplevel` and the porcelain path can spell one worktree differently is unknown. [unverified] The design hashes the `--show-toplevel` string whenever the worktree holds a `.git`, so the key matches what `review-ledger.sh` wrote. Without a `.git` it hashes the porcelain path, which may not match, and the manifest records both strings.
13. Ledger rows carry no branch or repo field, and hashed names cannot be reversed. [verified: Step 3 exploration report, not reopened]
14. Only `_sweep_stale_ledger_files` deletes ledger files. It runs after each `append code-review` (`review-ledger.sh:670-671`) and from `clear-stale` (:849-850). [verified for the two call sites; unverified that nothing else deletes them, per the exploration report]
15. Moved to the D3 follow-up plan.
16. `_lib_acquire_append_lock` must run at most once per process, and it installs a bare EXIT trap (`_lib.sh:5356-5363`). `collect_process_cwds` can install an EXIT trap in both cleanup scripts (`_worktree-lib.sh:33-34`). [verified]
17. A row with empty legacy columns and an empty prompt-description is not promptable, and `migrate-legacy-config.sh` scaffolds its default row (`config-keys.psv:82-84`, `docs/config-file.md:178-185`). [verified]
18. `test_doc_counts.py` pins the key count in `install.sh:684`, `migrate-legacy-config.sh:13` and `docs/config-file.md:148`, `:180` and `:265`. It pins the non-critical count in `config-schema-audit.md`'s "Remaining sixteen keys" heading (:175). It also requires, per key, a config-file table row, an audit section and an existing docs-anchor path. [verified: `claude/.claude/hooks/tests/test_doc_counts.py:672-713`, `:784-832`]
19. `claude/.claude/scripts/tests/conftest.py:1630-1657`'s autouse `_isolate_transcript_corpus_lookups` already pins `CLAUDE_CONFIG_DIR` to a tmp directory for every test there, the two cleanup test files included. `_base_test_env()` (:69-82) reads `os.environ` at call time, so the pin reaches each subprocess. Neither pins `HOME`, so a test that enables the key and forgets `HOME` resolves the engineer's real root. [verified]
20. Moved to the D3 follow-up plan.
21. Whether `cp -RPp` and `rename` work on a target volume that ignores POSIX modes (row 57) is unknown. [unverified] A failure of either fails a copy or its verification, which keeps the worktree. On a volume that ignores POSIX modes no code can make the archive owner-only, so no refusal depends on a `chmod` result (row 58).
22. The tracked settings carry no `sandbox` key; machine-level settings were not read. [verified: grep of `claude/.claude/settings.json`; unverified: machine settings] Under G3, an archive write fails and nothing is removed. The case study names `permissions.additionalDirectories`, or `--add-dir`, as the documented setting that restores write access (`docs/case-studies/claude-code-sandbox-write-boundary.md:149-158`). [verified] Row 64 and Alternatives hold the reach of that setting, and the plan ships no grant of either kind (row 93).
    - Whether `test -w` sees a sandbox denial is unknown. On Linux a read-only bind mount would make it fail, as the case study's `Read-only file system` errors suggest (:29-34), and on macOS it may pass ahead of a denied write. [unverified: not run]
    - Either way a denied write fails the archive call and keeps the worktree. Which reason surfaces is unknown: a not-writable reason when `test -w` sees the denial, otherwise `DIRECTORY`, `SNAPSHOT_MKDIR` or `COPY_FAILED` from the first write step 4 or 6 reaches. [unverified: inferred from steps 4-6, not run under the sandbox]
    - The doc carries no sandbox note, so it makes no claim about which reason surfaces.
23. No in-repo consumer of `--list-removable` exists beyond the script, its tests, `docs/scripts.md` and `CHANGELOG.md`. [verified: grep]
24. `jq` is a required tool (`README.md:107`). [verified]
25. Whether `cp -RPp` and `diff -r` fail on a symlink or special file nested in `agent-reviews/` is unknown. [unverified: manuals not read; three reviewers expect both to succeed, with GNU `diff -r` following the link] The design does not depend on it: the `find` pre-scan refuses any entry that is neither a regular file nor a directory, which keeps the worktree. That `find` without `-L` reports a symlink as itself is pinned by a test on Linux only. [unverified on BSD]
26. GNU and BSD `cp -RPp` (a source without a trailing slash, copied to a destination that does not exist yet), `diff -r`, and `find` without `-L` behave alike for these uses. [unverified: manuals not read, and CI runs Linux only (`.github/workflows/tests.yml:25`, per review)]
27. `TestCallersStopWhenConfigShFails` copies `_lib.sh` only for scripts named in `_SCRIPTS_SOURCING_LIB_SH_FIRST`. [verified: `claude/.claude/hooks/tests/test_bash_floor.py:444-465`]
28. `review-pr-finish.sh` force-removes review-pr worktrees and their findings directory (:42-63). Those findings live in a session directory, not `agent-reviews/`, and review other authors' PRs. [verified: `claude-skills/skills/review-pr/REFERENCES.md:613`]
29. `scripts/list-shell-files.sh` lists tracked files only. [verified: `scripts/list-shell-files.sh:26`]
30. [engineer-verified: "We can just hard code the path and use something reasonable. I think that's fine as long as the path is valid on both Linux and macOS"], the engineer's answer to /plan-review's finding that the root path was not a given. Covers: a root path fixed in claude-config's code, in place of a path-valued key, on condition that the path is valid on Linux and macOS.
31. `$HOME/.local/state/agent-branch-records/` is valid on Linux and macOS, and the relocation is a symlink the engineer makes by hand. [unverified: the dispatching session's observation, relayed, not checked by the architect]
32. A new file under `claude/.claude/scripts/` reaches a stow consumer on `git pull`, because `~/.claude/scripts` is one folded symlink into the checkout. A real directory of per-file links there is the "unsupported legacy layout" (`docs/hooks.md:395`, `:520`). [verified: `CLAUDE.md:56`, `docs/hooks.md:395`, `:520`] That `scripts`, `hooks`, `agents` and `skills` under `~/.claude` are folded symlinks is the dispatching session's check. [unverified: relayed, not checked by the architect]
33. Both cleanup scripts run under `set -euo pipefail` and source their sibling libs unguarded (`cleanup-merged-branches.sh:170-186`, `cleanup-idle-open-pr-worktrees.sh:79-91`). `review-ledger.sh` runs under `set -u` only and guards its lib source, because there "an unchecked failed `.` would continue" (`review-ledger.sh:10-17`). [verified] That `set -e` stops a script on a failed `.` is inferred from that comment and the unguarded sibling sources. [unverified: bash manual not read]
34. [engineer-verified: "we don't have to split. It's okay to keep it as is"], the engineer's answer to /plan-review's size question. Covers: shipping D1, D2 and D3 as one PR at the projected size.
35. Superseded by row 54, which moves D3 to a follow-up plan. Nothing in D1 or D2 depends on D3.
36. [engineer-verified: "Keep the rename"], the selected label answering /plan-review's question whether item 8's rename of `_OPT_IN_PILOT_KEYS` to `_PER_ACCOUNT_OPT_IN_KEYS` stays in this PR. Covers: keeping that rename. The option description was /plan-review's own.
37. [engineer-verified: "I thought when the key is on we are moving all branch records including ledger files and agent reviews"], the engineer's words this session, relayed by the dispatching session, settling that D3 stays. Covers: the intent that both the worktree's `agent-reviews/` and the narrative-ledger files are archived when the key is on.
38. `_config.sh` warns on stderr and skips a malformed or wrong-type row for a bool key (`_config.sh:368-371`, `:385-391`). It reads an unreadable state file as an absent one (`:308`, `:620-630`). Either way the key resolves to its schema default, `false`, and `_config_enabled` returns 1. [verified]
39. `_config_resolve_fail_safe` returns a key's default on an internal resolver failure. Its comment rests that on every key but `worktree_required` defaulting to its fail-closed direction (`_config.sh:795-817`). For this key the default is the unarchived direction. [verified]
40. Moved to the D3 follow-up plan.
41. Moved to the D3 follow-up plan.
42. `_lib_capped` is the repo's 5-second cap for "any other command that reads the filesystem and can stall on it" (`_lib.sh:43-52`). It runs uncapped with a stderr note when neither `timeout` nor `gtimeout` is on PATH (:132-139), and no cap bounds uninterruptible kernel I/O (:85-86). `_lib_status_consistent_with_cap_kill` classifies a cap kill (:142-154). [verified]
43. In a directory without `.git`, git walks up and reports an enclosing repo (`cleanup-merged-branches.sh:1145-1148`), and the script's header lists a worktree without a `.git` as a real case (:110). [verified]
44. Both scripts list branches with `%(refname:short)` (`cleanup-merged-branches.sh:792`, `cleanup-idle-open-pr-worktrees.sh:288`). `resolve_worktree_for_branch` matches `branch refs/heads/<name>` exactly (`_worktree-lib.sh:227`). [verified] So a name shortened to `heads/<name>`, because a tag shares it, resolves no worktree and reaches no archive call. [unverified: that `for-each-ref` shortens as `symbolic-ref --short` does, per `_lib.sh:5613`]
45. `_lib.sh:5738-5744` makes a private directory under `umask 077`, refuses a symlink, a non-directory or another owner (`[ -O ]`), then runs `chmod -- 700`. `_lib.sh:5731` already uses `find -maxdepth 0 -perm`. [verified]
46. A worktree's snapshots belong to the account live after `load_repo_environment`. A session in that repo resolves its account through the same `.envrc`, and a repo with no override inherits the invoking account, as its sessions would. [unverified: the architect's reading of who owns the data]
47. Moved to the D3 follow-up plan.
48. `cleanup-merged-branches.sh`'s `*` arm reaches `git worktree remove` for `dirty` and `submodules` too, because "git decides, so a refusal is reported, not predicted" (:1118-1119). [verified]
49. `run_repo_cleanup` runs under `set -euo pipefail` (:170), directly or in a backgrounded subshell that keeps errexit (:1288-1300), so a bare call there runs with errexit on. [verified]
50. Moved to the D3 follow-up plan.
51. `config-get.sh <key>` exits 0 when the key is enabled and prints the effective value (`config-get.sh:5-18`). [verified]
52. Moved to the D3 follow-up plan.
53. The repo caps code and test files at 1,000 lines (`docs/design-decisions/code-file-line-limit.md:19`), and nothing enforces it yet (:50-57). `test_cleanup_merged_branches.py` already exceeds it, at 4,882 lines. `test_cleanup_idle_open_pr_worktrees.py` is at 996 lines, four under the limit. [verified: line counts by Grep]
54. [engineer-verified: "All of it (Recommended)"], the option label the engineer selected after the architect's consult on whether to split this plan or cut tests at its ~2,000-line projection. Covers: splitting D3 out of this plan, cutting tests and design pieces per that consult, and dropping M3's owner and mode refusals.
55. The description attached to row 54's label, "split D3 out, cut tests, apply the design drops, and fix the volume problem", was the dispatching session's own wording. The consult's recommendations reached this revision as that session's summary, not the consult's text. [unverified: not the engineer's words]
56. Row 54 supersedes row 34 for size. The PR ships D1 and D2, and D3 moves to a follow-up plan. [verified: rows 34 and 54]
57. A target volume that ignores POSIX modes is possible. [unverified: relayed by the dispatching session, not checked by the architect]
58. No `chmod` can make files owner-only on a volume that ignores POSIX modes. The engineer's two choices there are to remount it with their own uid and gid and an owner-only umask, or to accept that other local accounts can read the archive. [unverified: the volume's driver and its mount options not read] On such a volume `chmod` may succeed without effect, or it may fail. If it fails, `cp -RPp` likely fails too, because `-p` preserves the mode, so every copy fails and every worktree is kept. [unverified: the driver's manual and cp(1) not read]
59. Errexit is off inside a function that an `if`, `&&` or `||` tests, so each step there must check its own status. [verified: `claude/.claude/rules/shell-script-conventions.md`, "`-e` is also suppressed for commands inside an `if`/`&&`/`||` test"; unverified: bash manual not read]
60. With D3 moved out, the ledger sweep keeps deleting stale `.jsonl` files with no copy until the follow-up lands, key on or off. So row 37's intent holds only once both plans ship. [verified: row 14, and this plan edits no `review-ledger.sh`]
61. For this key, status 0 from `_config_enabled` means `_lib_config_dir` printed a non-empty path and `claude-config.toml` there set the key `true`. A relative, empty or `/` config dir returns 2, and a missing or untraversable one reads as the default `false`, which is status 1. [verified: `claude/.claude/hooks/_config.sh:155-169`, `:860-993`, `:620-739`. The `/` config dir arm traces as follows: `_lib_config_dir` prints an empty line for `CLAUDE_CONFIG_DIR=/` (:163), `primary_dir` is then empty (:897), and a key outside the union branch falls through to `return 2` (:992, under the comment at :973-975)]
62. [engineer-verified: "No notice."], the engineer's typed answer this session to whether the archive being readable by other local accounts on a volume that ignores POSIX modes is acceptable, asked with options that included a runtime notice. Covers: adding no runtime notice about group or other bits. The option descriptions were the dispatching session's own proposals.
63. [engineer-verified: "Leave it out"], the option label the engineer selected this session when asked whether the account-directory symlink refusal, dropped along with M3's owner and mode refusals, should come back. Covers: leaving that refusal out. The option descriptions were the dispatching session's own proposals.
64. The Claude Code sandboxing page documents `sandbox.filesystem.allowWrite`, an array of paths, to let sandboxed subprocess commands write outside the default set. The page says those paths are enforced at the OS level (https://code.claude.com/docs/en/sandboxing.md, "Configure sandboxing"). The same page lists `permissions.additionalDirectories` among the default write locations. The permissions page says files in additional directories get the same permission rules as the working directory, so Claude's file tools can read and edit them (https://code.claude.com/docs/en/permissions.md, "Working directories"). `additionalDirectories` is therefore the wider grant. [verified: a research subagent read both pages, the first 100,000 of the sandboxing page's 111,225 characters only; unverified: that `allowWrite` leaves Claude's file tools alone, because neither page states it]
65. `worktree_canon_path` canonicalizes in a subshell, as `(CDPATH='' cd -- "$p" 2>/dev/null && pwd -P)`, and falls back to the raw input on failure (`claude/.claude/scripts/_worktree-lib.sh:57-70`). `_lib.sh:5738` runs `( umask 077; mkdir -- "$dir" )`. [verified] The new lib's functions run in the caller's shell, because both scripts source it.
66. `collect_process_cwds` takes one process snapshot, and `worktree_in_use` reads only that snapshot (`_worktree-lib.sh:99-173`). Its `lsof` branch, the one a host without procfs takes, installs an EXIT trap and then clears it (`:118-146`). The idle script's header accepts the window between the snapshot and each removal (`cleanup-idle-open-pr-worktrees.sh:42-50`). `cleanup-merged-branches.sh` also snapshots once (:883), but its header states no rationale the archive makes wrong. [verified]
67. `_config.sh` prints `skipping line for unrecognized key` to stderr when it reads a line whose key has no schema row (`_config.sh:373-380`). [verified] `migrate-legacy-config.sh` scaffolds this key's default line into an account's file (row 17), so the line can outlive the key's removal from the schema.
68. `_lib_config_dir` reads `CLAUDE_CONFIG_DIR` and `HOME` on every call (`_config.sh:155-169`), and `_config_memo_lookup` resets its memo when either changes (`:748-759`). [verified] So a status call after `load_repo_environment` changes `CLAUDE_CONFIG_DIR` reads the new account, and the plan adds no lib test of that.
69. `<UTC stamp>` has one-second resolution, so two snapshots made within one second order by the `<pid>` and `<n>` text in their names, not by time, and the newest pick can land on the older one. The reuse check returns a snapshot only when it matches the worktree, so the cost is one duplicate snapshot, not a wrong one. [verified: the `date -u +%Y%m%dT%H%M%SZ` format, step 5, and step 6's name]
70. The dispatching session made these decisions this round. They are not the engineer's. [unverified: the session's decisions]
    - The reuse check compares only fields that describe this worktree, orders its candidates under `LC_ALL=C`, and states an outcome for each miss.
    - `cd -P` and `umask 077` run in subshells, with a test that cwd, umask and `CDPATH` survive.
    - The doc's opening paragraph states the interim scope, and the doc carries the removal note.
    - Superseded by row 93.
    - The setup steps create the volume directory first and remove an empty root directory before linking.
    - The idle header gains a note, with no fresh `worktree_in_use` check.
    - A failed step-4 `mkdir` re-tests `-d`.
    - This round's test additions and fixes.
71. Dropping `ledger_files` from the manifest is the architect's choice. [unverified: the architect's choice] The field named every `<repo-hash>.*.jsonl` file, every branch's and session's included, so it joined no session-scope file to a branch. `ledger_key` and `branch` still join the branch-scope name, and `ledger_repo_root` hashes to the repo half (row 10).
72. `helpers.write_scaled_timeout_shim` (:3071) returns `False`, writing nothing, when neither `timeout` nor `gtimeout` is on PATH (`claude/.claude/tests/helpers.py:3083-3085`). [verified]
73. [engineer-verified: "no, this is fine. PRs can ship separately. Totally fine."] Covers only: shipping D1 and D2 before the ledger follow-up, with the key archiving `agent-reviews/` only in between, is acceptable, and the PRs can ship separately.
74. Row 73's question and context, as the dispatching session relays them. The engineer asked whether running cleanup after this PR would stop ledger files being archived later. The session answered that removal deletes no ledger file (row 14), that the D3 sweep archives each stale file at sweep time with no worktree needed, and that a ledger file going 30 days without an append between the two PRs is deleted with no copy (row 60). The engineer then said "You're just changing the sweeper for the ledger". [unverified: relayed; the explanation is the session's own]
75. Every `append code-review` sweeps the whole ledger directory with the fixed floor `_LEDGER_SWEEP_FLOOR_DAYS=30` (`_lib.sh:5534`, `review-ledger.sh:670-671`), through `find "$ledger_dir" -maxdepth 1 ... -mtime "+$window_days"` (`review-ledger.sh:314`). `clear-stale` uses Claude Code's `cleanupPeriodDays`, floored at 30 (`review-ledger.sh:179-181`). [verified] So a ledger file that goes 30 days without an append is deleted at the next append anywhere.
76. Once the account directory exists, no step writes the root itself. `branch_records_status` creates nothing, step 4 skips an existing directory, and every later write lands under the account directory. [verified: this plan's steps 1-8] So the account directory, not the root, is what a `test -w` probe must test. The read-only-root success case pins it.
77. Moved out: the sandbox grant text is not shipped.
78. Moved out: the sandbox grant text is not shipped.
79. Superseded by row 87. The reuse check no longer compares `worktree_path`, so the symlinked-path case that rested on this row's premise is dropped.
80. Bash sets `HOME` from the password database at startup when its environment lacks it, so dropping `HOME` from a subprocess's environment would resolve the real home. [unverified: recollection; the bash manual not read] The unset-`HOME` case runs `unset HOME` inside its command instead, which is safe whether or not this holds.
81. The dispatching session made these decisions this round. They are not the engineer's. [unverified: the session's decisions]
    - Superseded by row 93.
    - The doc states the write side of a mode-ignoring volume and the unrefused symlinked account directory, with no claim about other stow users.
    - Only the doc's opening paragraph states the ledger sweep's window and the manual copy, and the CHANGELOG says nothing about it.
    - This round's test additions and fixes, and the guard-deletion check in place of a fail-first run.
82. Moved out: the sandbox grant text is not shipped. The account-directory probe this row also chose stays, on row 76's reason alone.
83. A Bash call the sandbox denies can be retried with `dangerouslyDisableSandbox: true`. Under a permission mode that grants bare `Bash`, with `sandbox.allowUnsandboxedCommands` at its default `true`, that retry ran unsandboxed with no prompt and no visible signal. [verified: `docs/case-studies/claude-code-sandbox-write-boundary.md:196-211`, `:350`, `:490-493`] Whether the engineer's sessions set that key was not checked (row 22). [unverified]
84. Moved out: the sandbox grant text is not shipped.
85. Moved out: the sandbox grant text is not shipped.
86. The idle script's `gh auth status` check prints `ERROR: gh is not authenticated` and exits 1 (`cleanup-idle-open-pr-worktrees.sh:150-153`), after argument validation (:101-119). The test shim's `{"__auth__": "unauth"}` mode makes that check fail (`claude/.claude/scripts/tests/test_cleanup_idle_open_pr_worktrees.py:83`, `:100-103`). [verified] So under that mode a refusal placed after the check prints the `gh` error instead of the archive reason.
87. The dispatching session made these decisions on the round-5 review findings. They are not the engineer's. [unverified: the session's decisions]
    - Superseded by row 93.
    - Drop `worktree_path` from the reuse comparison, together with the symlinked-path case and its guard-deletion line, and keep it as a manifest field.
    - Accept these other findings:
      - the dotfile-safe move;
      - the test fixes;
      - the reason variables and their tie to the doc's table;
      - the stated probe order;
      - the two prose splits.
88. The idle script skips a branch with no removable worktree before any removal step (`cleanup-idle-open-pr-worktrees.sh:335-340`). In `cleanup-merged-branches.sh`, a merged branch with no worktree takes the verdict's `none` arm (:1077-1079), which prints `worktree: not found` and never reaches the `*` arm's archive call. [verified]
89. Bash's `${!prefix@}` expansion lists the names of every set variable whose name starts with `prefix`, so the enumeration case can read the reason variables without scanning the lib's source. [unverified: recollection; the bash manual not read] A wrong premise fails the enumeration case when it is written.
90. The dispatching session made this decision. It is not the engineer's. [unverified: the session's decision]
    - Leave the symlink refusal out for `branches` and the key directory as well as the account directory, extending row 63.
91. The dispatching session and a `plan-architect` consult on the third review round made this decision. It is not the engineer's. [unverified: the session's and the consult's decision]
    - Step 6 builds the snapshot name in the lib and creates it with `mkdir`, in place of `mktemp -d` and the check of its output, so step 8 deletes only a path this call's `mkdir` created.
    - It drops `MKTEMP_REJECTED`, renames `MKTEMP_FAILED` to `SNAPSHOT_MKDIR`, and replaces the seven-arm `mktemp` shim case with one read-only key-directory case plus one collision arm.
    - Folding a failed or empty stamp capture into `SNAPSHOT_MKDIR` is the architect's addition in this revision, because without the old output check's basename test nothing else catches it.
92. `mkdir` without `-p` either creates its path or fails, so its exit 0 shows the calling process created that directory. The repo already relies on `mkdir(2)` being atomic (`claude/.claude/hooks/_lib.sh:5707`; `claude/.claude/hooks/nudge-long-turn-subagent.sh:340`). [verified: those comments] That it fails on a path holding a symlink, a dangling one included, instead of creating the link's target, is POSIX `mkdir(2)`'s contract. [unverified: recollection; neither POSIX nor mkdir(2) read] The collision arm pins the exit-status gate, because a `[ -d ]` test would pass the read-only key-directory case and mishandle a collision.
93. [engineer-verified: "Trim it (Recommended)"], the option label the engineer selected on the question "Trim the sandbox setup as the architect recommends (docs section cut to one sentence, plan edited, /plan-review re-run), or keep the full sandbox text?". Covers: the shipped doc carrying no sandbox grant instructions, and this plan dropping them. The question and the option's description were the dispatching session's own. [unverified: relayed]
94. [engineer-verified: "I don't use sandboxed Claude sessions at all. It's something I've been thinking of moving to"], the engineer's typed words earlier this session, relayed by the dispatching session. Covers only: the engineer does not use sandboxed sessions today. The second sentence states no decision.
95. The reasons behind row 93's recommendation are a `plan-architect` consult's, relayed by the dispatching session, not the engineer's. [unverified: the consult's reasoning]
    - Checking grant text needs a sandboxed session, which the engineer does not use today (row 94), so grant text shipped now would stand untested in a public doc.
    - The grant may not suffice for a real run (row 96).
    - The Ask never mentions the sandbox. [verified: the Context's `Ask:` line]
96. `git worktree add <sibling>`, run from inside a linked worktree under the sandbox, failed with `Read-only file system`, at the plain working-directory boundary rather than the shared-`.git` exception (`docs/case-studies/claude-code-sandbox-write-boundary.md:120-129`, `:417`). [verified] So `git worktree remove` of a sibling worktree, from a session anchored in a linked worktree, probably fails at the same boundary even with the archive granted. [unverified: inferred; `remove` not run under the sandbox]
97. [engineer-verified: "If so, keep it."], the engineer's typed words this session on D1's `select-tests.py` edit, in the message "Is the select test edit correct? If so, keep it. What is the downside of keeping it?". Covers only: keeping that edit if it is correct. The session checked the diff against the existing `SESSION_CONFIG_KEYS_LOG_DOC_MD` entry, and `test_select_tests.py` passes with it. [verified: this session's diff read and test run] The edit was a `code-writer` addition outside the first Critical files list, reported in that agent's hand-back. It is added here as item 8a. Its size, about 13 lines, is the dispatching session's own count. [unverified]
98. [engineer-verified: "Proceed anyway"], the option label the engineer selected at a diff-size gate during D1. Covers only: going on past that gate at D1's size. The gate's question and its 1,913-line figure reached this revision as the dispatching session's relay. [unverified: relayed] Row 103 records the answer at the final size.
99. [engineer-verified: "Trim it (Recommended)"], the option label the engineer selected after a `plan-architect` consult during code review. Covers: trimming the listing-proof layer, which refused an `absent` reading unless a listing of the worktree's parent proved the entry missing, so that a stat failing for want of search permission reads as `absent`. That reverses, for this layer only, the round-10 code-review choice that built it, which the review ledger records as engineer-selected scope. [verified: the branch's review ledger] The question and the option's description were the dispatching session's own. [unverified: relayed]
100. A probe found that files under a worktree survive `git worktree remove` when the worktree is mode 0600 or 0000, or an ancestor is mode 0000, and that with an unsearchable ancestor git may still unregister the worktree. [unverified: the dispatching session's probe, relayed] The permission-hidden case pins the worktree-0600 and ancestor-0000 shapes against a control worktree whose ignored finding `git worktree remove` does delete (`claude/.claude/scripts/tests/test_branch_records_archive.py:245-288`). [verified: read; unverified: not run by the architect] The reasoning behind row 99's recommendation was the consult's. [unverified: relayed]
101. The dispatching session made these decisions on code-review findings, some on `plan-architect` consults. They are not the engineer's. [verified: the branch's review-ledger rows carry no `decided_by`]
    - Step 1's empty-argument refusal as `LEDGER_KEY`, placed after the status re-run, and a missing worktree directory archiving `absent`.
    - Step 6's rescan of the copy before `diff -r`.
    - Splitting the lib tests at the status/archive seam once `test_branch_records_lib.py` reached 1,057 lines against the 1,000-line limit (row 53).
    - The lib and script cases added on review findings, the dangling-root arm and the `test -w` cap arm included.
    - Deleting the two script-level dry-run cases written during implementation, which Out of scope excludes.
    - The doc sentences on quoted secrets, page-cache verification, exporting the account's `CLAUDE_CONFIG_DIR` before `config-get.sh`, and the default-account hazard under `--all-projects`.
102. [engineer-verified: "Keep both (Recommended)"], the option label the engineer selected in `/plan-review` on whether to keep `claude/.claude/scripts/tests/test_branch_records_archive.py` and `claude/.claude/scripts/tests/_branch_records_helpers.py`, the two test files that joined Critical files item 3 after the plan's approval through row 101's split. Covers: keeping both in this PR's file list. The option description was /plan-review's own.
103. [engineer-verified: "Proceed anyway"], the option label the engineer selected in `/plan-review` on the measured `Projected diff:` of 2,529 changed lines, after asking "What does the architect think?" and receiving the consult's recommendation to proceed. Covers: shipping D1 and D2 as one PR at that size. The consult's reasons, relayed by the dispatching session, are not the engineer's. [unverified: relayed]
104. The dispatching session declined these `/plan-review` findings. They are the session's decisions, not the engineer's. [verified: this revision's dispatch names each decline and its reason]
    - A reused snapshot whose `agent-reviews` a second writer into the archive tree has replaced with a link back to the worktree: such a writer lies outside the cooperative tier.
    - A case for a `chmod` that fails or outlives the cap: no refusal depends on `chmod` (row 58), so such a case would pin only one more arm.
    - A key-off account under `--all-projects` removing worktrees with no line: the doc's Account bullet already warns of it.
    - A CI wall-time check against the workflow's 25-minute cap: nothing in this change adds a CI tool, and the cap is re-derived when a pass grows.
    - The account directory as the only purge unit, the sentinel's prose-only protection, and how to find one repo's snapshots: the doc does not state these today. They are known documentation gaps this change leaves open, and the engineer may ask for doc sentences.
105. [engineer-verified: "Refuse, no prompt (Recommended)"], the option label the engineer selected in the code-review session on how a malformed enabling line or an unreadable `claude-config.toml` is handled, after the engineer suggested a TTY prompt and asked for the architect's view. Covers: the archive reads `unavailable` with its own reason instead of reading the key as off, with no prompt. The option description and the architect's reasons were the dispatching session's own wording and a relayed consult, not the engineer's. A misspelled key name is not detected. Row 108 records the engineer's answer on that residual.
106. [engineer-verified: "Keep it (Recommended)"], the option label the engineer selected in `/plan-review` on whether to keep the keyword-only `config_dir` argument added to `_seed_session` in `claude/.claude/scripts/tests/conftest.py`, a file outside the first Critical files list. Covers: keeping that edit, now item 19. The option description was /plan-review's own.
107. [engineer-verified: "Ask the architect"], the engineer's reply to /plan-review's question on whether to keep a one-word edit to `docs/design-decisions/architect-disposition-challenge-pilot.md:346`. Covers only: consulting the architect. The architect's verdict was to keep the edit, because the Revert contract bullet is a forward instruction naming live code, not a record of something that happened. That verdict is the session's relay, not the engineer's words. Item 8 formerly left the file unedited on the plan author's own inference. [unverified: relayed]
108. [engineer-verified: "yeah I wouldn’t try to fix that in this PR"], the engineer's typed words this session on whether to close the misspelled or hyphenated key name in this PR. Covers only: not closing the misspelled or hyphenated key name in this PR. It does not cover calling the residual acceptable. The engineer said the reviewers raised a legitimate problem that should be fixed in a different way, so the PR body names it as a follow-up.
109. [engineer-verified: "Restore the engineer's value only (Recommended)"], the option label the engineer selected on what the `KEY_UNREADABLE` fix text tells the reader. Covers only: the fix text tells the reader to restore the value the engineer set and to ask the engineer if unsure, and it does not enumerate `= false`. The option's description was the dispatching session's own proposal, not the engineer's words.
110. [engineer-verified: "Proceed anyway"], the option label the engineer selected at a diff-size gate on the working tree's changed lines. Covers only: shipping D1 and D2 as one PR at the measured size of about 2,815 changed lines, re-measured at this revision with the command on the `Projected diff:` line. The gate's question and the option's description were the dispatching session's own. [unverified: relayed] Row 103 records the same answer at the earlier size. The answer covered the 2,815 figure, which was measured against the older base `13ccd953` and so counted commits merged from `origin/main`. The re-measured figure against the merge base with `origin/main` is 3,148 (row 110's `Projected diff:` line). Row 112 records the engineer's answer at the re-measured size.
111. [engineer-verified: "only what a first time user needs"], the engineer's typed words, with the follow-up "151 lines is way too much information. Trim aggressively please", on `docs/agent-branch-records.md`. Covers only: trimming `docs/agent-branch-records.md` to what a first-time user needs. It does not cover which individual statements were cut.
112. [engineer-verified: "Proceed anyway"], the option label the engineer selected in `/plan-review` on the re-measured `Projected diff:` of about 3,148 changed lines, after asking "What does the architect think?" and receiving the consult's recommendation to proceed as one PR. Covers only: shipping this branch as one PR at about 3,148 changed lines. The consult's size breakdown (about 488 production lines, about 2,450 test lines) is the consult's, not the engineer's.

**Mechanisms:**
- M1. The sentinel file, probed under `_lib_capped` by the status check each archive call re-runs. anchors: root, row21, row42. Lighter checks, and a second probe before the manifest `mv`, are set aside under Alternatives.
- M2. A fixed root, relocated by symlink. anchors: row1, row3, row30, row31.
- M3. A per-account subtree named by the percent-encoded canonical config dir. Its permissions come from three steps:
  - each directory is made by one `mkdir` under a subshell's `umask 077`;
  - the account directory gets `chmod -- 700`;
  - each snapshot gets `chmod -R -- go-rwx`.

  Every `chmod` is best-effort, because no code can make files owner-only on a volume that ignores modes. anchors: row6, row9, row54, row57, row58, row62. The owner, mode and symlink refusals `_lib.sh` uses are set aside under Alternatives (rows 45, 63).
- M4. No lock. anchors: row16, row92. Primitives checked, each set aside:
  - `_lib_acquire_append_lock`: once per process (row 16), and one cleanup run archives many worktrees.
  - `flock(1)`: unused in the repo [verified: exploration report], and absent from stock macOS [unverified].
  - Python `fcntl.flock`: a subprocess per worktree, guarding a race whose worst case is a duplicate snapshot.
  - A new `mkdir` lock: new stale-lock eviction to own.
- M5. The manifest, written last, marks a snapshot complete. anchors: root.
- M6. Moved to the D3 follow-up plan.
- M7. A per-repo status check plus a re-check inside each archive call. anchors: row7, row9.
- M8. A new lib, which every consumer gets on `git pull`. The two `set -e` cleanup scripts source it unguarded at the top, like their existing libs. anchors: row27, row32, row33.
- M9. Outcomes of the key read, one rule each. anchors: root, row38, row39, row51, row105.
  - `_config_enabled` statuses 2, 3 and 4 map to `unavailable` with `CONFIG`, which keeps every item.
  - Status 1 maps to `disabled` only when `_branch_records_toml_confirms_off` returns 0. Otherwise it maps to `unavailable` with `KEY_UNREADABLE` (row 105).
  - Status 0 goes on to the root check, so an enabling line with a malformed duplicate stays `ready`.
  - An internal resolver failure of the config reader falls back to the schema default `false`. It reads as `disabled`, and the consumer acts as with the key off.

  Every enabled real run's and dry run's `Archive: ready` line, and the setup doc's `config-get.sh` check, are the signal that the key read as on. List mode gives none. The fail-closed cases are statuses 2, 3 and 4 and `KEY_UNREADABLE`. Their cost is that a broken install stops removals for a default-off consumer too, and the refusal names the cause.
- M10. One test helper builds every archive case's `HOME` and `CLAUDE_CONFIG_DIR` under `tmp_path` and asserts it, so no case can resolve the real root. anchors: row19.
- M11. The idle script sources `_lib.sh` for the ledger-key formula instead of a copy of it. anchors: row10, row27.
- M12. Every external command touching `<root>` runs under `_lib_capped`, except the `agent-reviews/` copy, its rescan, its comparisons and step 8's removal of a partial copy. The shell builtins that touch `<root>` run uncapped, right after step 1's capped probes. A cap kill counts as a failure, except on a best-effort `chmod`. anchors: row42, row58.
- M13. The reuse check: a call whose worktree matches the newest complete snapshot on `tip_sha`, `ledger_key`, `agent_reviews` and `diff -r` returns that snapshot. Every other outcome but a cap kill falls to a fresh copy. anchors: row48, row69, row70, row87. Lighter primitives checked, each set aside under Alternatives: skipping the archive on a predicted refusal, and a documented manual prune.
- M14. A pre-scan, repeated on the copy, that refuses any `agent-reviews/` entry other than a regular file or a directory, and refuses a scan that fails, plus a refusal of a symlink at `agent-reviews` itself. A stat that fails for want of search permission reads as `absent` rather than refusing. anchors: row25, row54, row99, row100, row101. The listing-proof layer is set aside under Alternatives.
- M15. A `test -w` probe in `branch_records_status` of the account directory when it exists, else of the root, so the probe tests the directory the next write lands in. anchors: row76.
- M16. Subshell-scoped `cd` and `umask 077`, so neither function changes the caller's cwd, umask or `CDPATH`. anchors: row65, row70.
- M17. One lib variable per refusal cause, which the tests read back through bash's `${!prefix@}` expansion to hold every value to the doc's reason table. anchors: row87, row89. Literals written only in the tests are set aside under Alternatives.

## Critical files

Give each `code-writer` dispatch this Approach and its own file list. The ledger and Out of scope are reviewer reference, and no row, G or M tag appears in a shipped file.

- The two dispatches run in order, because D2 calls D1's functions and extends `docs/agent-branch-records.md`, which D1 creates.
- Both ship as one PR (row 56). D3, the ledger sweep, is a follow-up plan that ships as its own PR (rows 54, 73), so no doc or CHANGELOG text either dispatch writes claims the ledger is archived.
- The tests follow `claude/.claude/rules/shell-script-conventions.md`: the input matrix runs against the lib directly, and the script tests hold at most one case per branch.

**D1: key, library, docs home.**
1. `claude/.claude/hooks/config-keys.psv`: append the Approach's row.
2. **New:** `claude/.claude/scripts/_branch-records-lib.sh`, sourced and not executable, holding `branch_records_status`, `branch_records_archive_worktree` and the thirteen `_BRANCH_RECORDS_REASON_*` variables, plus the helper `_branch_records_toml_confirms_off`.
   - The header names its two consumers, says to source `hooks/_lib.sh` first, says neither function depends on the caller's `pipefail`, and cites `docs/agent-branch-records.md` as the layout's home.
   - It also says to call both functions in the current shell, not in a command substitution or pipeline, and that only commands run through `_lib_capped` and `_lib_jq` are capped.
   - Reuse `_config_enabled`, `_lib_config_dir`, `_lib_reviewer_round_state_key_for_branch`, `_lib_jq`, `_lib_capped` and `_lib_status_consistent_with_cap_kill`.
   - Also reuse `_config_line_key_value`, `_CONFIG_STATE_FILENAME` and `_config_file_lines`' single-BOM strip, copied inline. `_config_file_lines` itself is not called, because it cannot report a read failure.
   - These are the first external callers of private `_config.sh` functions. The helper reads only the config dir's `claude-config.toml`, which mirrors `_config_enabled` while the key's `config-keys.psv` resolution is `config-dir`.
3. **New:** `claude/.claude/scripts/tests/_branch_records_helpers.py`, **new:** `claude/.claude/scripts/tests/test_branch_records_lib.py`, and **new:** `claude/.claude/scripts/tests/test_branch_records_archive.py`.
   - The two test modules split at the status/archive seam, so neither crosses the 1,000-line limit (rows 53, 101, 102). The lib module holds the reason-variable, status and account-directory cases, and the archive module holds the `branch_records_archive_worktree` cases.
   - The helper module holds the M10 helper, the one function through which every archive case under `claude/.claude/scripts/tests/` builds its per-command environment. It:
     - sets `HOME` and `CLAUDE_CONFIG_DIR` under `tmp_path` and asserts both lie there;
     - layers onto a base environment the caller passes, such as `conftest.py`'s `_shimmed_env` result (:156), so the script tests keep their `gh` and `direnv` shims;
     - writes `claude-config.toml` with the key's line, or a raw line the caller passes;
     - creates the root and sentinel on request, or a root symlinked to a directory elsewhere, whose target it can leave missing.
   - The helper module also holds the shims, fixtures and assertions that both test modules and the two script test files share.
   - The lib module reads every `_BRANCH_RECORDS_REASON_*` variable's name and value from the sourced lib once, through bash's `${!_BRANCH_RECORDS_REASON_@}` expansion. One case asserts that:
     - the names are exactly the Approach's thirteen causes, so an added or dropped cause fails it;
     - the values are pairwise distinct;
     - each value is non-empty, holds no newline, and contains neither `agent-branch-records`, which the root path and the sentinel's name both hold, nor `touch`, `mkdir` or `ln` as a whole word;
     - each value appears in backticks in `docs/agent-branch-records.md`, where the reason table quotes it.
   - Every lib case below that expects `unavailable` or a return of 1 asserts that `BRANCH_RECORDS_REASON` equals its arm's variable, so it cannot pass on a neighbouring arm. Each of the thirteen causes has at least one such case.
   - Both test modules:
     - start each case in its own `tmp_path` through an autouse fixture that also clears inherited git variables. A pin case per module checks the `tmp_path` start. The archive module also pins that an exported `GIT_DIR` does not redirect its worktree setup into another repository;
     - source `hooks/_lib.sh`, then the lib, in `bash -c`, with the helper's environment passed per command;
     - run each status case twice, under `set -u` and under `set -euo pipefail` with the function called bare. Three status cases run under `set -euo pipefail` only: the probe-cap case and the two config-dir encoding cases;
     - run each archive case under `set -euo pipefail` with the call tested by `if`, as both scripts make it, and the manifest-fields and reuse-cap cases under `set -u` as well;
     - start each command with `CDPATH` set to a real directory and `umask 022`, print `pwd -P`, `umask` and `CDPATH` after every call, and assert them unchanged and the call's own stdout empty, on success and failure returns alike;
     - induce failures with PATH shims for `cp`, `sha256sum`, `mkdir`, `mv`, `jq`, `date`, `find` and `test`;
     - parse `manifest.json` with `json.loads`;
     - never assert which of two snapshots made within one second is the newest, since their order then follows the names' `<pid>` and `<n>` text, not time (row 69).
   - The cases. The status and account-directory groups live in the lib module, and the `branch_records_archive_worktree` group in the archive module.
     - status:
       - `disabled` with no key row; `ready`, leaving the root's file tree unchanged;
       - `unavailable` for a root without the sentinel, with a directory at the sentinel path, or for a dangling root symlink, in one parametrized body. The dangling arm also asserts that the status call creates no link target. The directory arm is the only case that tells the regular-file test from an existence test;
       - `unavailable` for a read-only root with no account directory, or for a read-only account directory under a writable root, in one parametrized body skipped when run as root. The arms assert `ROOT_NOT_WRITABLE` and `ACCOUNT_NOT_WRITABLE` in turn. The second arm is the only case of the account-directory probe;
       - `unavailable` for a relative `CLAUDE_CONFIG_DIR` (status 2 from the real resolver);
       - `unavailable` for a `test` shim that outlives the cap, in one parametrized body over the sentinel probe and the `test -w` probe, built with `helpers.write_scaled_timeout_shim` as `test_cleanup_merged_branches.py` does. It skips when that helper returns `False` (row 72), asserts the cap fired with `helpers.assert_cap_engaged`, and carries the `timing` marker;
       - `unavailable` with `KEY_UNREADABLE` in one parametrized body, skipped for the unreadable-file arm when run as root. Each arm asserts the root's file tree unchanged. The arms:
         - a quoted `"true"`;
         - `: true`;
         - a trailing comment;
         - a malformed line after a `= false` line;
         - the key name alone;
         - an unreadable file;
         - a directory at the toml path;
         - a dangling symlink at the toml path;
         - a quoted non-boolean such as `agent_branch_records = "on"`, the only arm where `_config_line_key_value` returns status 0 with a value other than `true` or `false`, so it alone reaches the helper's status-0 refusal. The `"true"` arm returns status 2 and reaches the substring refusal instead.
       - A separate case does the same for `yes` and also asserts `_config.sh`'s warning on stderr;
       - `unavailable` with `KEY_UNREADABLE` for an unsearchable config dir, skipped when run as root;
       - `ready` for `= true` followed by a malformed `= yes` duplicate, with `_config.sh`'s warning on stderr. It pins that the helper runs only after status 1;
       - `disabled` in one parametrized body over `= false`, no line for the key, a `#`-commented line, a commented line then `= false`, a malformed line for an unrelated key, no `claude-config.toml` and a missing config directory;
       - `unavailable` with `HOME` removed by `unset HOME` inside the command after sourcing, an absolute `CLAUDE_CONFIG_DIR` and the key on. The command never drops `HOME` from the subprocess environment, because bash would then set it from the password database (row 80). It is the only case of the `${HOME:-}` read;
     - account directory:
       - config dirs `<tmp>/a%2Fb` and `<tmp>/a/b` yield distinct components;
       - a symlinked config dir encodes its canonical path;
       - a new account directory and a pre-existing 0755 one, in one parametrized body: the account, `branches`, key and snapshot directories all end mode 0700 under a caller umask of 022;
       - a `cp` shim shows the snapshot directory is already mode 0700 during the copy, which the final `chmod -R` would otherwise hide;
       - a root made read-only after a first archive call has created the account directory: the status reads `ready`. A second call, after a change to the worktree's `agent-reviews/`, returns 0 with a new complete snapshot, not the first one. The case is skipped when run as root, and a `finally` restores the root's mode. It is the only case where the root is unwritable and a call must write, so it pins that no step writes the root once the account directory exists (row 76);
     - `branch_records_archive_worktree`:
       - a tree holding a nested subdirectory, a dotfile, a name with spaces and non-ASCII characters, an empty directory and a 0600 file: the snapshot matches what the test wrote, checked without `diff`, every copied file and `manifest.json` is owner-only, and every manifest field is asserted, `archived_at` in the Approach's UTC form included;
       - a `ledger_key` equal to the file name `review-ledger.sh append` writes from that worktree;
       - a worktree without a `.git`, nested in the main checkout, whose `ledger_repo_root` is its own path, not the enclosing repo's;
       - a missing worktree directory, which archives `absent` with its own path as `ledger_repo_root`, and a dangling `.git` symlink, whose `ledger_repo_root` is the worktree path;
       - skipped when run as root:
         - one standalone case: a worktree with read but no search permission and no `agent-reviews/` archives `absent`;
         - one parametrized body with two arms, an `agent-reviews/` hidden by a mode-0600 worktree or by a mode-0000 ancestor: each archives `absent`, and its finding survives a `git worktree remove` that deletes a control worktree's ignored finding (row 100);
       - `absent`, then `agent-reviews/` created with one file and the tip unchanged: the second call returns a new snapshot whose `agent_reviews` is `copied` and that holds the file. It is the only case where `agent_reviews` alone differs from the candidate's;
       - a symlink or a regular file at `agent-reviews`, in one parametrized body: each returns 1 with no snapshot. The regular-file arm is the only case of the any-other-type refusal;
       - a nested symlink to a sibling file inside `agent-reviews/` returns 1 with no snapshot. Its target exists, so `cp` and `diff -r` would pass it and only the pre-scan refuses it;
       - a nested FIFO, and, skipped when run as root, an unreadable subdirectory: each returns 1 as `UNSUPPORTED_ENTRY` with the root unchanged. The second is the only case of a failing pre-scan;
       - a second call on an unchanged worktree returns the first snapshot and adds none, with a manifest-less directory beside it named to sort last under `LC_ALL=C`. A changed file or a moved branch tip adds a second;
       - reuse picks the newer of two complete snapshots once the older is renamed to an earlier stamp, reuses an `absent` snapshot, and reuses a snapshot whose branch does not resolve, recording `tip_sha` empty;
       - two calls in one shell under a `date` shim that prints one fixed stamp make distinct complete snapshots, which pins the per-shell counter. Under `TZ=EST5EDT` the stamp and `archived_at` track UTC;
       - a reuse miss, in one parametrized body over three changes to the first snapshot: a copied file deleted, its `agent-reviews/` deleted, or its `manifest.json` replaced with non-JSON. Each next call on the unchanged worktree returns a new complete snapshot. It is the only case that alters a stored snapshot;
       - a copy failure after an earlier call's success and a change to the worktree, one case per `cp` shim:
         - shims: exit 1 after a partial copy (`COPY_FAILED`), exit 0 after truncating one copied file (`COPY_MISMATCH`), or exit 0 after planting a symlink in the source before the real copy (`UNSUPPORTED_ENTRY`);
         - each returns 1, leaves no new snapshot directory, and leaves the earlier snapshot intact;
         - the truncating shim is the only case where step 6's `diff -r` finds a mismatch, and the planting shim the only case of step 6's rescan;
       - a `cp` shim that plants links to a file and a directory outside the key directory: the call returns 1 as `UNSUPPORTED_ENTRY`, and step 8's removal leaves both targets intact;
       - a key directory made mode 0500 after an earlier call's success, then a change to the worktree: the call returns 1 with `SNAPSHOT_MKDIR`, the key directory holds only the earlier snapshot, and that snapshot is intact. The case is skipped when run as root, and a `finally` restores the mode, as the read-only-root case does. Unlike that case, whose call succeeds, this call must fail, and it is the only case where step 6's `mkdir` fails for lack of write permission;
       - a non-empty directory holding a `manifest.json`, which the shim creates at the exact path step 6 builds, after an earlier call's success and a change to the worktree, so step 5's reuse check misses: the call returns 1 with `SNAPSHOT_MKDIR`, and that directory and its contents survive.
         - A `mkdir` PATH shim forces the collision. `_lib_capped` runs `mkdir` through `timeout`, which finds it on PATH.
         - The shim creates the directory named by its last argument, puts a `manifest.json` inside it, then runs the real `mkdir` on it, which fails. The shim is on PATH for the second call only, because on the earlier call it would intercept that call's own `mkdir` calls and the earlier call would no longer succeed. The earlier call has made the account, `branches` and key directories, so step 6's is the only `mkdir` the call runs.
         - The arm needs no `date` shim, no `$$` and no counter name. It needs no read-only mode either, so it runs as root.
         - It is the only case where step 6's `mkdir` fails on a path that exists, and so the only one that tells a gate on `mkdir`'s exit status from a `[ -d ]` test;
       - a reuse lookup that outlives the cap, in one parametrized body over the manifest `find` and the manifest `jq` read: each returns 1 as `STALLED`, with the earlier snapshot alone and intact;
       - a `mkdir` shim that fails only the key directory: the call returns 1 as `DIRECTORY` with no key directory;
       - a `mkdir` shim that makes the account, `branches` or key directory and then fails, in one parametrized body: the call still archives, which pins step 4's `[ -d ]` re-test;
       - a `jq -n` failure and an `mv` failure: each returns 1 as `MANIFEST` and leaves only the earlier snapshot's manifest;
       - a `date` shim that fails the stamp format or the `archived_at` format, in one parametrized body: `SNAPSHOT_MKDIR` and `MANIFEST` in turn, with the earlier snapshot intact;
       - a `sha256sum` shim that prints nothing, or a `.git` file naming a missing gitdir so `rev-parse` fails, in one parametrized body: each returns 1 and creates no key directory. They are the only cases of step 3's failure, and the second is the only one of a failing `rev-parse` while `.git` exists;
       - two worktrees on different branches: the first call succeeds, the sentinel is removed, and the second call returns 1 with `BRANCH_RECORDS_SNAPSHOT` empty, no key directory for its branch, and the first snapshot intact. Its second call has no sentinel at entry, so it also stands for a sentinel missing before the first call;
       - each empty argument, in one parametrized body: `LEDGER_KEY` with no `branches` directory. With no sentinel, each empty argument instead reports the `SENTINEL` reason;
       - the key off: a return of 0 with no snapshot and the root unchanged, for an existing and a missing worktree, for each empty argument, and with TRIGGER omitted. With no root at all, a status call and an archive call create nothing under `HOME`;
       - a symlinked root reads `ready` and holds a complete snapshot on the link's target.
4. **New:** `docs/agent-branch-records.md`, the docs-anchor target, written for a first-time user. It holds only what that reader needs (row 111), in these sections:
   - Intro: what the key and the fixed root are for, and that the archive holds only a worktree's `agent-reviews/`. It does not define the account directory.
   - Setup: a preamble, then five one-time steps (volume target and link, sentinel, smoke test, enable and `--dry-run` check), with the sentinel never created from an agent session.
     - The preamble gives the path when there is no larger volume, and says to exclude the root from any backup or sync client before step 5.
     - The preamble also says moving an existing root is manual.
   - What a run prints (D2, item 14): the `Archive: ready` and `Archive: unavailable` lines for dry and real runs, the `kept (archive failed: ...)` line, and list mode printing no `Archive:` line.
   - When a script refuses: a table with one row per `_BRANCH_RECORDS_REASON_*` value, each reason string byte-identical to the lib's constant and quoted in the first column, with its fix:
     - the `UNSUPPORTED_ENTRY` row gives `find agent-reviews ! -type f ! -type d`;
     - the `KEY_UNREADABLE` row is the last.
       - It tells the engineer to restore the value they set.
       - It names no way to turn the key off, `= false` included (row 109).
       - It says an agent only reports the refusal.
   - Fail direction: a key that reads as off prints nothing and archives nothing, and an unreadable key refuses.
     - Residual: a misspelled or hyphenated key name reads as off with no snapshot (row 108).
     - Residual: a transient read failure in the config reader reads as off with no snapshot.
     - Residual: a stat error such as EIO, ESTALE or ETIMEDOUT under a searchable parent reads as off with no snapshot.
     - The copy steps are not time-capped.
   - Snapshots (D2, item 14):
     - The account directory and snapshot path layout.
     - A snapshot is complete only once `manifest.json` exists.
     - How to find one.
     - Restore is a manual `cp -R`.
     - The manifest and account directories are not an access boundary.
   - Known limits (D2, item 14): the ledger sweep's 30-day deletion is not covered, a worktree removed by other means gets no snapshot, and a repo on a key-off account is removed with no snapshot.

   Examples use placeholder paths and names only.
5. `docs/config-file.md`: the table row; 21 to 22 at :148, :180 and :265; the key added to the scaffold list at :180-185, where "all five" becomes "all six".
6. `claude/.claude/hooks/tests/config-schema-audit.md`: 21 to 22 at :3; "Remaining sixteen keys" to "Remaining seventeen keys" at :175; a `` ### `agent_branch_records` `` section among the remaining keys, modeled on `plugin_bootstrap` (:463-481). Its call site is `branch_records_status`. Its fail direction names one rule: statuses 2, 3 and 4, an undeterminable `claude-config.toml` and a skipped or non-boolean line naming the key are `unavailable`, so the consumer keeps every item. It also states that an internal resolver failure reads as off. It names the residuals too: the hyphenated or misspelled key name (row 108), and a transient read failure or a stat error other than EACCES, ELOOP or ENOTDIR, either of which can still read as off.
7. `install.sh:684` and `claude/.claude/scripts/migrate-legacy-config.sh:13`: 21 to 22.
8. `claude/.claude/scripts/tests/test_config_py.py`: add `"agent_branch_records": "false"` to the known-set dict (:91-98). Add the key to `_OPT_IN_PILOT_KEYS` and rename that list `_PER_ACCOUNT_OPT_IN_KEYS` (:26-31), since the new key is not a pilot. Item 20 updates `docs/design-decisions/architect-disposition-challenge-pilot.md:346`, which names the old list (row 107).
8a. `claude/.claude/scripts/select-tests.py`: add an `AGENT_BRANCH_RECORDS_DOC_MD` constant for `docs/agent-branch-records.md`, beside `SESSION_CONFIG_KEYS_LOG_DOC_MD`. Add it to the same `CROSS_DOMAIN_EXCEPTIONS` entry that holds `SESSION_CONFIG_KEYS_LOG_DOC_MD`, routing the doc to `SCRIPTS_TESTS_DIR`. The lib test reads the doc by path, and without the rule `test_select_tests.py::TestCrossDomainReadCompleteness` fails (row 97).

**D2: cleanup scripts.**

9. `claude/.claude/scripts/cleanup-merged-branches.sh`: per the Approach.
10. `claude/.claude/scripts/cleanup-idle-open-pr-worktrees.sh`: per the Approach.
11. `claude/.claude/scripts/tests/test_cleanup_merged_branches.py`. Add no isolation fixture, because `conftest.py` already pins `CLAUDE_CONFIG_DIR` (row 19). Archive cases build their environment with item 3's helper, and their fixture repos ignore `agent-reviews/` as this repo's `.gitignore:41` does. Cases:
    - `ready`, in one run:
      - the `Archive: ready` line, a snapshot whose decoded file text matches what the case wrote, and the worktree and its local and remote branch gone;
      - `manifest.json`, parsed with `json.loads`: `branch` is the removed branch, `trigger` is `cleanup-merged-branches.sh`, `worktree_path` is the porcelain path git reported for the worktree, `main_worktree_path` is the main checkout, and `tip_sha` is the branch tip recorded before the run;
      - a second merged branch with no worktree: its local and remote branch are gone, `branches/` holds only the worktree branch's key directory, and the exit is 0. It is the only key-on case whose branch takes the verdict's `none` arm. Item 12 has no counterpart, because the idle script skips such a branch first (row 88);
    - two merged worktrees on branches named so the one holding a FIFO in `agent-reviews/` sorts first, because both scripts walk branches in refname order (`cleanup-merged-branches.sh:792`, `cleanup-idle-open-pr-worktrees.sh:288`):
      - the FIFO worktree and its local and remote branch remain, and it has no snapshot;
      - its `kept (archive failed: ` line carries a non-empty reason, and the `Skipped (archive failed)` line prints;
      - the other worktree is archived and removed, and its local and remote branch are gone;
      - the exit is 1;
    - no sentinel: exit 1, the worktree and branch remain, and stderr names the reason and `docs/agent-branch-records.md` but not the sentinel path;
    - the `ready` case's manifest assertions when the script runs from a linked worktree, so `main_worktree_path` is the main checkout, not the runner;
    - a stale-locked worktree whose archive fails: it is kept and stays locked with no `unlocked stale lock` line, and its local and remote branch remain, which pins the archive call's place before the unlock;
    - a dirty worktree, run twice: each run archives before git refuses the removal and exits 0 with no `Skipped (archive failed)` line, and the second run reuses the first snapshot (row 48);
    - `--list-removable`: `unavailable` exits 1 with empty stdout, and `ready` prints exactly the list lines, with no `Archive:` line, and exits 0;
    - `--dry-run` with `ready` prints `Archive: ready`, exits 0 and leaves the root's file tree unchanged;
    - disabled: a real run exits 0 and removes the worktree and its local and remote branch, and `$HOME/.local/state` stays uncreated. Because the script prints worktree paths, the case deletes every occurrence of `str(tmp_path)` and of its resolved path from stdout and stderr, then asserts neither contains `archive` in any letter case. The assertion therefore holds under any test name, and the fixture's branch names hold no `archive`.
12. `claude/.claude/scripts/tests/test_cleanup_idle_open_pr_worktrees.py`, with the same helper and ignore rule. Cases:
    - `ready`, with item 11's manifest assertions and `trigger` being `cleanup-idle-open-pr-worktrees.sh`;
    - two candidates, the one holding a FIFO in `agent-reviews/` sorting first, with item 11's worktree, snapshot and reason assertions for both and exit 1;
    - unavailable, with the `gh` shim in its `{"__auth__": "unauth"}` mode: exit 1, the worktree remains, and stderr names the archive reason and `docs/agent-branch-records.md` but not the script's `gh is not authenticated` line. A status call placed after the `gh auth status` check would print that line instead (row 86);
    - item 11's linked-worktree manifest case;
    - `--dry-run` with `ready` prints `Archive: ready`, previews the removal, exits 0 and leaves the root's file tree unchanged;
    - a disabled run that exits 0, removes the worktree, leaves `$HOME/.local/state` uncreated, and passes item 11's path-stripped `archive` check.
13. `claude/.claude/hooks/tests/test_bash_floor.py`: add `cleanup-idle-open-pr-worktrees.sh` to `_SCRIPTS_SOURCING_LIB_SH_FIRST` (:444).
14. `docs/agent-branch-records.md`: D2 adds the What a run prints, Snapshots and Known limits sections, the three item 4 marks as D2's. D1's item 4 writes Intro, Setup, When a script refuses and Fail direction.
15. `docs/scripts.md`: one sentence in each cleanup entry (:253, :269) citing `docs/agent-branch-records.md`.
16. `CHANGELOG.md`: one `### Added` entry under `[Unreleased]`. It names the key, says that turning it on makes both cleanup scripts snapshot a worktree's `agent-reviews/` before removing it, and states the refusal behavior. The shipped entry's Refusals bullet states no rule for an unreadable key, and says refusal happens when the archive is unavailable. Item 16 requires the Refusals bullet to state one sentence: a key that reads as off but whose config cannot be read as on or off refuses with exit 1. The sentence carries no qualifier the trimmed doc does not state. It says nothing about the ledger sweep's window, which only the doc's Known limits section states.
17. `claude/.claude/scripts/tests/_branch_records_helpers.py`, item 3's helper module: D2 adds the fixtures and assertions that items 11 and 12 share, `worktree_holding_reviews`, `porcelain_paths`, `complete_snapshots`, `make_fifo_in_agent_reviews`, `assert_cleanup_manifest` and `assert_output_never_mentions_the_archive`.
18. `claude/.claude/scripts/tests/test_branch_records_archive.py`, an incidental D2 edit to a D1 file: it swaps a hand-written `.git/info/exclude` append for the shared `ignore_agent_reviews` from `claude/.claude/tests/helpers.py`.

19. `claude/.claude/scripts/tests/conftest.py`: give `_seed_session` a keyword-only `config_dir` argument, so the archive test module (`test_branch_records_archive.py:138`) writes its session registry through the shared helper instead of a hand-written copy (row 106). The default behaviour is unchanged.
20. `docs/design-decisions/architect-disposition-challenge-pilot.md:346`: name `_PER_ACCOUNT_OPT_IN_KEYS` in place of `_OPT_IN_PILOT_KEYS` in the Revert contract bullet (row 107).

Projected diff: about 3,148 changed lines (3,107 added, 41 deleted), re-measured at this revision on the working tree with `git diff --numstat` against the merge base with `origin/main`, excluding `.claude/plans` (`-- . ':!.claude/plans'`). The merge base comes from a separate `git merge-base HEAD origin/main` call, and `origin/main` is the local ref, not fetched. Row 110's "Proceed anyway" covered the earlier 2,815 figure, which was measured against an older base, and row 112 records the engineer's answer at this figure. The earlier 2,529 (2,502 added, 27 deleted) came from `git diff --numstat origin/main...HEAD -- . ':!.claude/plans'` at the D2 commit (row 103).

## Verification

1. After each dispatch, from the worktree root:
   - `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py`. Item 8a edits `select-tests.py`, a global trigger (`select-tests.py:476-480`, `:959-961`), so on this branch the selection is the full suite and its "must include" check cannot fail. `test_select_tests.py::TestCrossDomainReadCompleteness` is what verifies 8a's routing. A selection that omits that dispatch's own test files would still be a rule-table bug to report.
   - `git add` the new files, then run `scripts/list-shell-files.sh | xargs -0 scripts/venv-run.sh shellcheck`, because the list holds tracked files only (row 29).
   - `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/`.
2. Once each dispatch's tests pass, run a one-time guard-deletion check in place of a fail-first run. Neutralise each guard below in turn, run its named case, see that case fail, then restore the guard. A guard whose case stays green is a test gap to fix before the dispatch is done. Run the checks as a non-root user, because the read-only cases skip as root, and a skipped case is not a failing one.
   - D1, step 1's status re-run: the two-worktree sentinel case.
   - D1, step 1's empty-argument check: the empty-argument case.
   - D1, step 1's empty-argument check moved ahead of the status re-run: the key-off empty-argument case and the no-sentinel empty-argument case.
   - D1, the sentinel's regular-file test weakened to an existence test: the sentinel-directory arm.
   - D1, `${HOME:-}` read as `$HOME`: the unset-`HOME` case.
   - D1, the account-directory `test -w` probe: the read-only account directory arm.
   - D1, a root probe run even when the account directory exists: the read-only-root success case.
   - D1, step 3's `rev-parse` check: the `.git`-file arm.
   - D1, step 2's refusal of any other file type: the regular-file arm.
   - D1, step 2's nested-entry refusal, with the top-level classification kept: the nested-symlink case. A green result here means `cp -RPp` or `diff -r` already rejects the link, which disproves that case's premise (row 25), so the dispatch reports it instead of adding a case.
   - D1, step 2's check of the scan's own exit status: the unreadable-subdirectory case.
   - D1, step 4's `[ -d ]` re-test after a failed `mkdir`: the raced-directory case.
   - D1, step 5's `agent_reviews` comparison: the `absent`-then-`copied` case.
   - D1, step 6's `mkdir` status check: the read-only key-directory case. With the check neutralised, `cp` fails into the missing directory as `COPY_FAILED`, so the reason assertion fails the case.
   - D1, step 6's `mkdir` status gate replaced by a `[ -d <path> ]` test: the collision arm.
   - D1, step 6's stamp check and step 7's `archived_at` check: the two `date`-shim arms.
   - D1, step 6's rescan of the copy: the symlink-planting `cp` arm.
   - D1, `_branch_records_toml_confirms_off`'s searchable-ancestor test weakened to an existence test: the unsearchable-config-dir arm alone. A dangling symlink stops the helper's walk-up at its own symlink test and never reaches that branch.
   - D1, the helper's status-0 refusal arm deleted: the quoted non-boolean arm (`agent_branch_records = "on"`), which no other arm reaches.
   - D1, the helper run ahead of the status-0 path: the `= true` plus `= yes` `ready` case.
   - D1, step 6's `diff -r`: the exit-0 truncating `cp` arm.
   - D1, step 8's removal of the partial copy: the three `cp`-shim arms, which assert that no new snapshot directory is left.
   - D2, each script's `continue` past a failed archive: its FIFO case.
3. `/code-review` dispatches `claude-hook-review` for the `config-keys.psv` row (`.claude/rules/review-pipeline-dispatch.md`).
4. Manual, the engineer's call and not a merge gate.
   - On the target volume:
     - copy a sample tree with `cp -RPp` and rename it with `mv`, which settles rows 21 and 26 for that volume;
     - run `chmod -- 700` on a directory there and note its exit status and resulting mode. That result is informational: it tells the engineer which of row 58's two choices applies, and no step gates on it.
   - With a throwaway `CLAUDE_CONFIG_DIR` holding `agent_branch_records = true`, check that:
     - `~/.claude/scripts/config-get.sh agent_branch_records` exits 0;
     - `cleanup-merged-branches.sh --dry-run` prints `Archive: ready`;
     - one real run against a throwaway merged worktree leaves a complete snapshot;
     - the dry run prints `Archive: unavailable` once the sentinel is renamed away.
   - Run the doc's Setup smoke test (step 4) on the target volume.
   - The same steps on macOS check row 31, the condition row 30 sets.
   - Enabling the key on a real account waits on row 6.
5. Accepted gap: no case pins the writer side of the manifest-last rule, that step 7 writes the manifest only after step 6's copy is verified.
   - Every failure arm ends in step 8's removal, and every success arm inspects only the end state, so a manifest written ahead of the copy would pass every case.
   - The gap is accepted for this phase. The reuse check re-verifies a copied candidate with `diff -r` before returning it, so an early manifest could not let a removal rest on a partial copy, and nothing else reads the archive yet (Out of scope).
   - A later reader that trusts the manifest alone needs that case first.

## Out of scope

- Archiving the narrative-ledger files before the sweep deletes them (D3), which moves to a follow-up plan (row 54). It frees no disk space, and it is the only part that changes `review-ledger.sh append`. Until it lands, the sweep deletes stale ledger files with no copy, key on or off (row 60), and the engineer accepted shipping the two PRs separately (row 73).
- Archiving transcripts.
- De-provisioning paths in other repos. That includes any external consumer that removes worktrees itself from `--list-removable` output, which would bypass the archive (row 23).
- The machine notes file.
- A branch name reused at the same worktree path inheriting the previous live ledger.
- The decision on which accounts enable the key (row 6). The default stays false everywhere.
- Pruning or a retention policy for the archive itself.
- Tooling that reads the archive, such as `author_outcome.py`. Nothing reads it in this phase.
- `review-pr-finish.sh`'s removal of review-pr worktrees and findings (row 28). It is a sibling removal path, left out because it holds reviews of other authors' PRs, not this account's branch records. It is a candidate for a later phase.
- `fsync` before deletion. A crash inside the page-cache writeback window can lose an archive whose source is already deleted, a residual the trimmed doc does not state (row 111 covers the trim, not this cut).
- Splitting `test_cleanup_merged_branches.py`, which already exceeds the repo's 1,000-line limit and which this change grows (row 53). Nothing enforces that limit yet.
- A content digest per archived file in the manifest. A later phase that reads the archive would need it to detect a changed copy.
- A guard against an agent turning the key off in `claude-config.toml`. A reviewer's grep found no hook covering that file. This plan did not re-check that grep. No fix text in the reason table names `= false` or any other way to turn the key off (row 109), and the stderr reason says only that the line could not be read as true or false.
- A fresh `worktree_in_use` check before each idle candidate's archive. `worktree_in_use` reads the one snapshot `collect_process_cwds` takes, so a fresh check means a full process scan per candidate, which on a host without procfs runs `lsof` and clears the script's EXIT trap. It would still leave the gap between the copy and the remove (row 66).
- Script-level tests of the dry run with `unavailable`, of where `cleanup-merged-branches.sh`'s per-repo status call sits, and of the kept-archive exit under `--all-projects`. A fault in any of them changes an exit status or an output line, not what is removed. For the call's placement, that holds because each archive call re-runs the status check under the repo's own environment. Two such cases written during implementation were deleted (row 101). The idle script's call placement, before `gh auth status`, has item 12's unauthenticated case (row 86).
- Lib cases whose fault another case already pins, or that cannot occur:
  - a regular file at `branches`, whose failed `mkdir` and failed `[ -d ]` re-test return `DIRECTORY` as the key-directory `mkdir` case does;
  - a candidate whose `ledger_key` differs, which cannot occur, because the key directory is named from the same key.
- A lib case running two calls concurrently. Whether two background calls meet inside step 4 is up to the scheduler, so such a case would catch a missing `[ -d ]` re-test only on some runs. The raced-directory case pins that re-test deterministically instead. The no-lock rule rests on step 6's `mkdir`, which creates its path or fails (row 92), and on the manifest-last rule. The reuse case's manifest-less directory pins that rule's reader side, and Verification item 5 records its writer side as an accepted gap.
- A verified `sandbox.filesystem.allowWrite` setup, deferred until a sandboxed session can test it (row 94). It would have to settle the entry's path spelling through the root symlink, its path form, and which settings scope a session anchored in a linked worktree reads. It would also have to settle the sibling-removal problem (Alternatives, row 96).
