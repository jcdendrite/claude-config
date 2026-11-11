# Archive branch records before removal

## Context

Goal: copy a branch's per-branch review data (the worktree's `agent-reviews/` directory) to a relocatable archive root before a cleanup script removes the worktree, instead of destroying it. Archiving the narrative-ledger files the sweep deletes is the follow-up plan's part.

Ask: from the prior session's handoff, relaying the engineer's words from the session before it: "I don't want that path hard-coded in claude-config"; "That should be a config key I think, you're right. And if that's a config key then we probably don't need the tty"; "we should bring that in scope" (the idle-worktree cleanup script); the ledger sweep "should be a config key". This session the engineer revised the first of these: "We can just hard code the path and use something reasonable. I think that's fine as long as the path is valid on both Linux and macOS". On size, first "we don't have to split. It's okay to keep it as is", then at the larger projection "Ask the architect if it should be split or if some of the test should be cut", and the architect recommended splitting D3 out and cutting tests and design pieces, which the engineer accepted via label "All of it (Recommended)": this plan covers D1 and D2, and D3 (the ledger sweep) moves to a follow-up plan. Root directory name: the engineer selected the option label "agent-branch-records (Recommended)" this session.

Why now: the existing cleanup scripts reclaim disk space, but they run `git worktree remove` on worktrees whose gitignored `agent-reviews/` data then disappears. Losing this data blocks later analysis of review yield.

Intended outcome: with one per-account config key enabled, both cleanup scripts first write a verified copy of a worktree's `agent-reviews/` under a fixed root (`$HOME/.local/state/agent-branch-records/`). The engineer relocates that root with a symlink to a larger volume, and a sentinel file at the root gates writes so an unmounted volume makes the scripts refuse rather than delete. Transcript archiving, de-provisioning paths in other repos, and the machine notes file are out of scope for this phase.

## Approach

With the per-account `agent_branch_records` key on, both cleanup scripts copy a worktree's `agent-reviews/` and a manifest into a snapshot directory under `$HOME/.local/state/agent-branch-records/<account>/` before every `git worktree remove`. A worktree whose state matches its newest complete snapshot reuses that snapshot instead of adding one. A sentinel file the engineer creates at the root gates every write. The script keeps a worktree and exits nonzero when any of these holds:
- the sentinel is missing;
- the account directory, or the root while that directory does not exist yet, is not writable, or the root does not answer within the cap;
- the config dir or schema cannot be resolved;
- a copy fails verification.

A malformed or unreadable enabling line reads as the key being off, so every enabled real run and dry run prints an `Archive: ready` line to show the key read as on. List mode prints none, because its stdout carries list lines only. Every snapshot lands at a new path that its own `mkdir` creates, failing on any existing entry, and is never rewritten, so no lock is needed. The archive holds only `agent-reviews/`. Archiving the narrative-ledger files before the sweep deletes them moves to a follow-up plan (row 54).

**Archive layout.** `<root>` is `$HOME/.local/state/agent-branch-records`. It is fixed in code (row 30), and the engineer relocates it with a symlink (row 31).
- `<root>/.agent-branch-records-root` is the sentinel. The engineer creates it once by hand. No script ever creates it or `<root>`, and no script output names a command that would.
- `<root>/<account>/`: `<account>` is the canonical (`cd -P`) config dir from `_lib_config_dir`, percent-encoded into one path component (`%` to `%25`, then `/` to `%2F`). The scripts never use `mkdir -p`, so a missing `<root>` is never created. M3 states how the account directory is created.
- `<account>/branches/<repo-hash>.<branch-hash>/<UTC stamp>.<pid>.<n>/` is one snapshot per distinct worktree state, made by step 6's `mkdir`. It holds `agent-reviews/` when copied, and `manifest.json`. A snapshot is complete if and only if `manifest.json` exists, and the manifest is written last.
- `<UTC stamp>` is `date -u +%Y%m%dT%H%M%SZ`. `<pid>` is `$$`, which a subshell shares with its parent script. `<n>` is a counter the lib keeps per shell. So names are distinct within one shell and one second, but not guaranteed across subshells. A collision between subshells fails closed, because `mkdir` fails on the existing path and the worktree is kept.
- A snapshot directory with no `manifest.json` is incomplete, and readers ignore it. When one outlives its run, as after a kill, the engineer may delete it.

**Manifest fields.** `schema_version` (1), `archived_at` (UTC, as `date -u +%Y-%m-%dT%H:%M:%SZ` prints it, the form `review-ledger.sh:547` already writes), `trigger` (the calling script's file name), `branch`, `tip_sha` (empty when unresolvable), `worktree_path` (the porcelain path), `main_worktree_path`, `ledger_repo_root` (the string that was hashed), `ledger_key`, and `agent_reviews` (`copied` or `absent`). The manifest is built with `_lib_jq -n --arg`, written to `manifest.json.tmp`, then renamed with `mv`.

**New library `claude/.claude/scripts/_branch-records-lib.sh`.** The caller sources `hooks/_lib.sh` first.
- Every fallible step checks its own status with `if` or `||`. The scripts call `branch_records_status` bare under `set -euo pipefail` (row 49), and test `branch_records_archive_worktree` with `if`, where errexit is off inside the function (row 59).
- Neither function depends on the caller's `pipefail`, because `review-ledger.sh`, which the D3 follow-up will make source this lib, runs without it (row 33).
  - Every status a step acts on comes from one command's own capture, as `out=$(cmd) || status=$?`, never from a pipeline.
  - Each capture is assigned apart from its `local` declaration, because `local x=$(...)` masks the status.
- Every external command that touches `<root>` runs under `_lib_capped`, which caps it at 5 seconds (row 42). A cap kill counts as a failure. Three exceptions run uncapped:
  - the `agent-reviews/` copy and its `diff -r` comparisons, for the reason under Alternatives;
  - step 8's `rm -rf` of this call's partial copy, whose size is unbounded like the copy it undoes, for the same reason;
  - the shell builtins that touch `<root>`, the `[ -d ]` tests and the manifest's `>` redirect. Each runs only after the capped sentinel probe has answered in the same status call, which every archive call re-runs at step 1.
- Every `chmod` is best-effort. Its status, a cap kill included, is ignored, and no refusal depends on the mode it leaves (row 58).
- Neither function changes the caller's cwd, umask or `CDPATH` (M16).
  - Each `cd` runs in a `$(...)` subshell as `CDPATH='' cd -P -- <dir> && pwd -P`. That is the form `worktree_canon_path` uses, except that a failure or an empty result fails the call instead of falling back to the raw path (row 65).
  - Each `umask 077` runs in a subshell around one `mkdir`, as `_lib.sh:5738` does.
- No `BRANCH_RECORDS_REASON` value names the root path, the sentinel's file name, or a command that creates either. The doc's reason table is the one place that names a fix.
- Each `BRANCH_RECORDS_REASON` value is a fixed one-line string held in one lib variable per cause, and the functions set the reason only from these variables. Each variable is named `_BRANCH_RECORDS_REASON_<CAUSE>`, for these twelve causes:
  - `CONFIG`: `_config_enabled` returns 2, 3 or 4, `_lib_config_dir` fails, or `cd -P` cannot canonicalize the config dir;
  - `SENTINEL`: the sentinel is missing or not a regular file;
  - `STALLED`: a capped call in the status probes or the reuse check was killed at the cap;
  - `ROOT_NOT_WRITABLE`: no account directory exists and the root fails `test -w`;
  - `ACCOUNT_NOT_WRITABLE`: the account directory fails `test -w`;
  - `LEDGER_KEY`: step 2 cannot resolve `ledger_repo_root` or `ledger_key`;
  - `UNSUPPORTED_ENTRY`: step 3 refuses `agent-reviews` or an entry in it;
  - `DIRECTORY`: step 4 leaves a directory missing;
  - `SNAPSHOT_MKDIR`: step 6's stamp capture fails or prints nothing, or its `mkdir` exits nonzero or is killed at the cap;
  - `COPY_FAILED`: `cp` exits nonzero;
  - `COPY_MISMATCH`: step 6's `diff -r` exits nonzero;
  - `MANIFEST`: writing or renaming the manifest fails.
- `branch_records_status` always returns 0. It sets `BRANCH_RECORDS_STATE` (`disabled`, `ready` or `unavailable`), `BRANCH_RECORDS_REASON` and `BRANCH_RECORDS_ACCOUNT_DIR`. It creates nothing.
  - It runs `local key_status=0`, then `_config_enabled agent_branch_records || key_status=$?`. Status 1 means `disabled`, status 0 goes on to check the root, and any other status means `unavailable`.
  - A malformed or unreadable enabling line resolves to `false`, so it reads as `disabled` (row 38).
  - `unavailable` also covers a nonzero exit from `_lib_config_dir` and a config dir that `cd -P` cannot canonicalize, as `_lib_config_dir`'s call-site contract requires (`_config.sh:104-106`). Neither check gets a test, because after status 0 only a race reaches them (row 61).
  - `unavailable` also covers a sentinel that is not a regular file, a failed `test -w` probe, and a cap kill on any probe. The `test -w` probe tests the account directory when it is already a directory, and the root only when it is not, because once the account directory exists no step writes the root (row 76).
  - The probes run in this order: the capped sentinel test, then the builtin `[ -d <account dir> ]` that picks the `test -w` target, then the capped `test -w`. So no builtin touches `<root>` before a capped probe has shown the volume answers.
  - It reads `HOME` as `${HOME:-}`, so an unset `HOME` under `set -u` yields a root with no sentinel, which reads as `unavailable`, instead of aborting.
- `branch_records_archive_worktree WORKTREE BRANCH MAIN_WORKTREE TRIGGER` empties `BRANCH_RECORDS_SNAPSHOT` and `BRANCH_RECORDS_REASON` on entry. It returns 0 when the state is `disabled`, leaving `BRANCH_RECORDS_SNAPSHOT` empty, or when a complete snapshot holds the worktree's current state, setting `BRANCH_RECORDS_SNAPSHOT` to it. Otherwise it returns 1 and sets `BRANCH_RECORDS_REASON`.
  1. Re-run `branch_records_status`. `unavailable` returns 1.
  2. `ledger_repo_root` is `git -C WORKTREE rev-parse --show-toplevel` when `WORKTREE/.git` exists, else WORKTREE, because without a `.git` git walks up to the enclosing repo (row 43). When `.git` exists and `rev-parse` fails or prints nothing, return 1, creating nothing, because an empty string still hashes to a valid-looking key. `ledger_key` comes from `_lib_reviewer_round_state_key_for_branch`, and its failure returns 1. `tip_sha` is `git -C MAIN_WORKTREE rev-parse --verify -q "refs/heads/BRANCH^{commit}"`.
  3. Classify `WORKTREE/agent-reviews`.
     - A directory that is not a symlink is scanned first with `find`, without `-L`. Any entry that is neither a regular file nor a directory returns 1, whatever `cp` or `diff` would do with it (row 25).
     - Nothing at that path is `absent`. A symlink there, whatever its target, returns 1, and so does any other file type.
  4. Prepare the account directory (M3). Take the account directory, `branches` and the key directory in turn, and skip one that is already a directory. Otherwise run `( umask 077; _lib_capped mkdir -- <dir> )`. When that fails, re-test `[ -d ]`, because a concurrent first run may have just made it, and return 1 only when the directory is still missing. Then `chmod -- 700` the account directory.
  5. Reuse check. Every miss below falls to step 6, which makes and verifies a fresh copy, so a removal never rests on a doubtful snapshot. Only a cap kill, which says the volume is stalling, returns 1 instead of going on to the uncapped copy.
     - Capture `_lib_capped find <key dir> -mindepth 2 -maxdepth 2 -name manifest.json -type f` with its own status. A status consistent with a cap kill returns 1, and any other nonzero status is a miss.
     - The candidate is the last line of the captured text, sorted with `LC_ALL=C sort`. A directory without `manifest.json` is never a candidate, so an incomplete newest directory gives way to the newest complete one. No candidate is a miss.
     - One `_lib_jq -e` call compares the candidate's `tip_sha`, `ledger_key` and `agent_reviews` with this call's values. Status 1 is a miss, and so is any other status not consistent with a cap kill, an unparsable manifest included. `worktree_path` is recorded but not compared, for the reason under Alternatives (row 87).
     - When the candidate's stored `agent_reviews` is `copied`, a `diff -r` of the snapshot's `agent-reviews/` against the worktree's that exits nonzero is a miss. A missing or short copy therefore never matches.
     - A match sets `BRANCH_RECORDS_SNAPSHOT` to that snapshot and returns 0.
  6. Build the snapshot path `<key dir>/<UTC stamp>.<pid>.<n>` in a local variable. Capture the stamp with its own status, and a failed or empty capture returns 1 as `SNAPSHOT_MKDIR`. `<n>` is a lib global incremented on every step-6 entry, named outside the `_BRANCH_RECORDS_REASON_` prefix so M17's enumeration does not read it. Run `( umask 077; _lib_capped mkdir -- <path> )`, without `-p`, as step 4 does. A nonzero status, a cap kill included, returns 1 as `SNAPSHOT_MKDIR` and removes nothing. Exit 0 shows this call created an empty directory at exactly that path, because `mkdir` fails on any existing entry, a symlink included (row 92), so the path needs no further check. Unlike step 4, step 6 never re-tests `[ -d ]` after a failed `mkdir`, and decides on `mkdir`'s exit status alone. Copy with `cp -RPp WORKTREE/agent-reviews <snapshot>/agent-reviews`, naming the source without a trailing slash and a destination that does not exist yet. Compare the two with `diff -r`.
  7. Write the manifest temp file, run `chmod -R -- go-rwx` on the snapshot so the chmod covers the manifest too, then `mv` the manifest into place.
  8. On any failure after step 6's `mkdir` returned 0, `rm -rf --` the path step 6 built. This call's `mkdir` created it, so the removal reaches only this call's partial copy, as after `ENOSPC`. The removal runs uncapped and its own status is ignored, so the original reason stands. A leftover is the incomplete snapshot directory described in the Archive layout, a directory with no `manifest.json` that readers ignore. Then return 1.

**`cleanup-merged-branches.sh`.**
- Source the lib after the existing lib sources at :159-168, unguarded like them, because `set -euo pipefail` (:156) stops the script on a failed source (row 33).
- In `run_repo_cleanup`, right after the environment re-check at :355-360, call `branch_records_status`. The check runs per repo because `load_repo_environment` can change `CLAUDE_CONFIG_DIR` (row 9). The account it selects owns that repo's snapshots (row 46).
  - `unavailable` under `--list-removable`: one stderr line naming the reason, no stdout line, exit 1.
  - `unavailable` on a real run: one stderr line naming the reason and `docs/agent-branch-records.md` and saying to ask the engineer, then exit 1 before any classification or deletion.
  - No refusal line names the sentinel path, a command that creates it, or the key's off setting.
  - `ready` on a real run or under `--dry-run` prints `Archive: ready (<account dir>)`, the signal that the key read as on. Under `--list-removable` it prints nothing, because that stdout carries list lines only.
  - `--dry-run` with `unavailable` prints `Archive: unavailable (<reason>); a real run removes nothing in this repo and exits 1`, then previews as today and exits 0.
  - `disabled` prints nothing, so default output does not change.
- In the real run's `*` arm, call `branch_records_archive_worktree` before the unlock at :1079-1085. That arm reaches `dirty` and `submodules` too, by design (row 48), and the reuse check stops a repeated refusal from adding snapshots.
  - On failure, print `    worktree:       kept (archive failed: <reason>)`, record the branch, and `continue`. That also keeps the local and remote branch.
  - On success with a snapshot, print `    archive:        <snapshot path>`.
- After the end-of-run summary, print `Skipped (archive failed): <branch>` per kept branch, and exit 1 when there is one. Under `--all-projects` the sweep then prints its `WARNING: cleanup failed in <repo>` line (:1245) and moves to the next repo.
- `worktree_removal_verdict` stays unchanged. Archive availability is account state, not worktree state, and the real run, dry run and list each need their own arm either way.
- Header:
  - The exit-code table gains the `unavailable` refusal and the kept-archive exit 1. It names the kept archive as an exception to the clause that a handled per-branch failure does not trip exit 1 (:143-145).
  - The list-mode notes gain the `unavailable` exit 1. The known-divergence paragraph (:91-96) gains that a listed worktree can still be kept by a failed archive.
  - One line cites `docs/agent-branch-records.md`.

**`cleanup-idle-open-pr-worktrees.sh`.**
- Source `hooks/_lib.sh` in place of `hooks/_config.sh` at :72. `_lib.sh` sources `_config.sh` first (`_lib.sh:25`), so the floor check still runs before anything else. Then source the lib after `_worktree-lib.sh` (:76), unguarded like it (row 33).
- After argument validation (:97-104) and before the `gh` prerequisite checks (:110-119), call `branch_records_status`.
  - `unavailable` on a real run prints the refusal line described above and exits 1, before any `gh` call.
  - `--dry-run` with `unavailable` prints the `Archive: unavailable` line and continues.
  - `ready` prints the `Archive: ready` line on a real run and under `--dry-run`.
- In the removal loop, archive before each `git worktree remove`.
  - On failure, print `  <branch>: kept (archive failed: <reason>)` and skip the removal. After `print_summary`, exit 1 when any was kept.
  - On success with a snapshot, print `  <branch>: archived to <snapshot path>`.
- Header:
  - The exit-code table (:62-67) gains both exit-1 cases, plus one line citing `docs/agent-branch-records.md`.
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
- Refusing an account directory that is a symlink, is not owned by the caller, or is not mode 0700 after `chmod`, as `_lib.sh:5738-5744` does for its private directory (row 45). The engineer's intended volume ignores POSIX modes (row 57), so it reports an owner and mode the caller cannot change, and the owner and mode checks would refuse every archive there. No code can make files owner-only on a volume that ignores modes (row 58). The symlink refusal does not depend on modes, and the engineer chose to leave it out too (row 63).
- A runtime notice when the account directory keeps group or other bits after `chmod`: the engineer declined it (row 62).
- `permissions.additionalDirectories` as the setup step's sandbox grant: it also opens the root to Claude's file tools, which the scripts do not need. The setup step names `sandbox.filesystem.allowWrite` instead (row 64).
- Granting the whole root in `allowWrite`. The platform review made the case for it:
  - one entry covers every account, so there is no hand-made account directory, no per-account entry, and no refusal when a repo's `.envrc` selects an account nobody granted;
  - status always probes the root, which drops the conditional probe, its two test arms and their guard-deletion lines;
  - the narrow grant stops only sandboxed Bash, while Claude's file tools and an unsandboxed retry (row 83) still reach the sentinel, and an agent turning the key off is a larger bypass this plan already leaves to prose (Out of scope).

  It is set aside because the account-directory grant keeps the root and the sentinel outside every sandboxed command's write set, and a root grant would put both inside it. The narrow grant only moves a symlink plant down one level, from an account path to `branches` or the key directory. With the symlink refusal left out (row 63), nothing refuses that link, so a later unsandboxed run would write snapshots wherever it points, which the plan accepts. Row 63 covers only the account directory. Extending the omission to `branches` and the key directory is the dispatching session's decision, not confirmed by the engineer (row 90). The narrow grant costs one conditional and two test arms, and its operator cost is one-time and fails closed (rows 82, 87). It is a speed bump, not a boundary, and the doc says so.
- Shipping no grant text, with the doc saying only that the scripts fail closed under an ungranted sandbox and that a real run is made outside it. It is lighter, and it becomes the doc's text if Verification step 4 finds no entry the sandbox accepts. Until then it is set aside, because a sandboxed session's only path to a real run would then be an unsandboxed one, the escape hatch row 83 describes.
- Comparing `worktree_path` in the reuse check. Both scripts pass the porcelain path, which is stable for one worktree, and the key directory is already named from `--show-toplevel`, so no production input makes `worktree_path` the only difference. Where one worktree is named under two spellings, reuse is still correct, because `tip_sha` matches and `diff -r` verifies the content. The comparison would only add a duplicate snapshot, and its case rested on an unverified premise about `--show-toplevel` through a symlink (rows 79, 87).
- Reason literals written only in the tests: a reworded or added lib reason would then drift from the doc's table with nothing to catch it. Reading the lib's variables back through `${!_BRANCH_RECORDS_REASON_@}` ties the lib, the tests and the table together, without the source scan `claude-skills/skills/test-conventions/SKILL.md` § "9. Common authoring mistakes" allows only as a tripwire.
- Probing the root's writability on every status call: once the account directory exists no step writes the root (row 76), so that probe would refuse a setup whose grant covers only the account directory.
- A `ledger_files` manifest field naming every `<repo-hash>.*.jsonl` file: it would name every branch's and session's file in the repo, so it joins no session file to a branch. As a reuse field it would also change with unrelated sessions and with the sweep (row 71).
- Naming the first refused entry in the pre-scan's reason: a file name can hold a newline or control character that breaks the one-line output. The doc's reason table names the `find` command that lists every such entry.
- A finer snapshot stamp: within one second the newest pick can land on an older snapshot, and the reuse check returns it only when it matches, so the cost is one duplicate snapshot (row 69).
- Re-checking the sentinel before the manifest `mv`: a volume lost mid-run takes with it the snapshot directory step 6's `mkdir` made there, so the manifest write already fails. Each call's step-1 status re-run catches a root lost between worktrees.
- Archiving a symlinked `agent-reviews` as an uncopied link: a link there is rare. Refusing it keeps the worktree for the engineer to resolve, and saves one arm, one test and one manifest value.
- Explicit guards for an empty, unset or relative `$HOME`: the root such a `$HOME` yields has no sentinel, so the status already reads `unavailable`, though its reason then reports a missing sentinel rather than the bad `$HOME`.
- A `branches/index.jsonl`: it would be a second copy of every manifest, and `find <account>/branches -name manifest.json` rebuilds it.
- A basename or a hash for `<account>`: a basename can collide, and a hash is unreadable.
- Putting the helpers in `_worktree-lib.sh` or `_lib.sh`. `_worktree-lib.sh`'s domain is worktree inspection, and `review-ledger.sh`, which the D3 follow-up will make source this lib, does not source `_worktree-lib.sh`. `.claude/rules/bash-unit-test-seams.md` reserves `_lib.sh` for helpers two or more hooks need, and no hook needs these. A new `_<topic>-lib.sh` is that rule's third option.
- Only an up-front refusal, or only per-worktree checks: the per-repo check gives one clear refusal before any work, and the per-call re-check catches a root lost mid-run.
- Detecting a skipped enabling line inside `_config.sh`: it would change, for one key, the resolver every hook sources and its stated warn-and-skip rule (`_config.sh:278-281`). The `Archive: ready` line and the setup doc's `config-get.sh` check give the signal instead.
- Skipping the archive when the verdict predicts git's refusal (`dirty`, `submodules`): the `*` arm leaves that call to git by design (row 48), and a wrong prediction would remove a worktree with no snapshot.
- A documented manual prune in place of the reuse check: every refused run would still add a snapshot until someone prunes, on the volume the change exists to relieve.
- A free-space floor before each copy: a copy that hits `ENOSPC` already fails and its partial snapshot is removed, and no source grounds a threshold.
- Capping the `agent-reviews/` copy, its comparisons and the removal of a partial copy: they run only in the manually invoked cleanup scripts, right after a capped probe shows the volume answers. No source grounds a budget for a tree of unknown size, which the removal shares with the copy it undoes, and no cap interrupts uninterruptible kernel I/O (row 42).
- A caller-side manifest check before each removal: it would restate the lib's completeness rule in both callers, and the lib tests pin the return contract.
- `mktemp -d` for the snapshot directory: the path it prints is step 8's deletion target, so the lib would first have to prove that path is an empty directory it just created under the key directory. A lib-built name passed to `mkdir` needs no such check, because `mkdir`'s exit 0 already shows it (rows 91, 92).

**Dispatch split.** Critical files names two `code-writer` dispatches and each one's files.

### Assumption ledger

**Root:** With archiving off, today's only behavior, `git worktree remove` in either cleanup script deletes the worktree's gitignored `agent-reviews/` directory. Nothing keeps a copy, so later analysis of review yield loses that data. The fix copies first and never removes a worktree whose copy it cannot make and verify. The ledger sweep's deletion of `review-narrative-ledger/*.jsonl` files after 30 days without an append is the same problem, left to the D3 follow-up plan (row 54).

**Givens:**
- G1. `git worktree remove` without `--force` deletes ignored files along with the worktree. Git owns this. [unverified: git-worktree(1) not read this session. `_worktree-lib.sh:284-295` models the refusal on `git status --porcelain`, which lists no ignored file, and `.gitignore:41` ignores `agent-reviews/`]
- G3. Under the Claude Code sandbox, writes outside the working directory, added directories and `$TMPDIR` are denied. Anthropic owns this. [verified: `docs/case-studies/claude-code-sandbox-write-boundary.md:149-158`]

**Rows:**
1. [engineer-verified: "agent-branch-records (Recommended)"], the option label selected for the root's directory name under `$HOME/.local/state/`. Covers: that directory name. The "(Recommended)" description was the dispatching session's own. [unverified: relayed]
2. "I don't want that path hard-coded in claude-config"; "That should be a config key I think, you're right. And if that's a config key then we probably don't need the tty"; "Your original design was better"; "we should bring that in scope" (the idle-worktree script); the ledger sweep "should be a config key". [engineer-verified: "Yes, I said them"], the selected label answering /plan-review's question whether the engineer said these five quotes in those words. Covers: that these are the engineer's wording from the earlier session. Row 30 supersedes the first quote.
3. Reading of row 2: "that path" is the larger volume's location. The symlink keeps that location out of claude-config, so a fixed default root agrees with rows 1 and 2. No step prompts on a TTY. [unverified: the architect's reading] Row 30, the engineer's answer this session, settles the fixed root whichever path row 2 meant.
4. The sentinel gate, the refusal (keep the item, exit nonzero) when the root is unavailable, a bool config-dir key defaulting to false, and the phase-one scope were the prior session's proposals. The engineer did not object to them. [unverified: not an engineer decision]
5. The key is named `agent_branch_records`, after row 1's term, not the proposed `branch_metadata_archive`. [unverified: the architect's choice]
6. The decision on which accounts enable the key is still open. The default is false everywhere. [unverified: relayed as undecided]
7. `cleanup-merged-branches.sh`'s only `git worktree remove` is at :1087, in the real run's `*` arm, after the unlock at :1079-1085. A failed remove `continue`s past both branch deletions (:1112). [verified: `claude/.claude/scripts/cleanup-merged-branches.sh:1073-1145`]
8. `cleanup-idle-open-pr-worktrees.sh`'s only remove is at :372. It has no list mode, and it sources `hooks/_config.sh` at :72. [verified]
9. `run_repo_cleanup` evals the repo's `direnv export bash` before any branch work (:318-322, :348), so a repo's `.envrc` can change `CLAUDE_CONFIG_DIR` per repo. [verified for the eval; unverified that any `.envrc` sets it]
10. A branch-scope ledger file is `<repo-hash>.<branch-hash>.jsonl` (`_lib.sh:5662-5679`, `:5233-5240`). Both halves are sha256 of the raw string (`_lib.sh:647-651`, `:1209-1215`), and the repo string is `git rev-parse --show-toplevel` (`review-ledger.sh:230-238`). [verified]
11. A session-scope ledger file is `<repo-hash>.<session-id>.jsonl`, used on a detached HEAD or the default branch (`_lib.sh:5627-5679`). Session ids hold only letters, digits, `_` and `-` (`_lib.sh:3464-3474`), so no stem followed by `.` prefixes another stem. [verified]
12. Whether `--show-toplevel` and the porcelain path can spell one worktree differently is unknown. [unverified] The design hashes the `--show-toplevel` string whenever the worktree holds a `.git`, so the key matches what `review-ledger.sh` wrote. Without a `.git` it hashes the porcelain path, which may not match, and the manifest records both strings.
13. Ledger rows carry no branch or repo field, and hashed names cannot be reversed. [verified: Step 3 exploration report, not reopened]
14. Only `_sweep_stale_ledger_files` deletes ledger files. It runs after each `append code-review` (`review-ledger.sh:670-671`) and from `clear-stale` (:849-850). [verified for the two call sites; unverified that nothing else deletes them, per the exploration report]
15. Moved to the D3 follow-up plan.
16. `_lib_acquire_append_lock` must run at most once per process, and it installs a bare EXIT trap (`_lib.sh:5356-5363`). `collect_process_cwds` can install an EXIT trap in both cleanup scripts (`_worktree-lib.sh:33-34`). [verified]
17. A row with empty legacy columns and an empty prompt-description is not promptable, and `migrate-legacy-config.sh` scaffolds its default row (`config-keys.psv:82-84`, `docs/config-file.md:177-184`). [verified]
18. `test_doc_counts.py` pins the key count in `install.sh:684`, `migrate-legacy-config.sh:13` and `docs/config-file.md:147`, `:179` and `:264`. It pins the non-critical count in `config-schema-audit.md`'s "Remaining sixteen keys" heading (:175). It also requires, per key, a config-file table row, an audit section and an existing docs-anchor path. [verified: `claude/.claude/hooks/tests/test_doc_counts.py:672-713`, `:784-832`]
19. `claude/.claude/scripts/tests/conftest.py:1630-1657`'s autouse `_isolate_transcript_corpus_lookups` already pins `CLAUDE_CONFIG_DIR` to a tmp directory for every test there, the two cleanup test files included. `_base_test_env()` (:69-82) reads `os.environ` at call time, so the pin reaches each subprocess. Neither pins `HOME`, so a test that enables the key and forgets `HOME` resolves the engineer's real root. [verified]
20. Moved to the D3 follow-up plan.
21. The target volume is known only as row 57 relays it, and whether `cp -RPp` and `rename` work there is unknown. [unverified] A failure of either fails a copy or its verification, which keeps the worktree. On a volume that ignores POSIX modes no code can make the archive owner-only, so no refusal depends on a `chmod` result (row 58).
22. The tracked settings carry no `sandbox` key; machine-level settings were not read. [verified: grep of `claude/.claude/settings.json`; unverified: machine settings] Under G3, an archive write fails and nothing is removed. The case study names `permissions.additionalDirectories`, or `--add-dir`, as the documented setting that restores write access outside the sandbox's default set (`docs/case-studies/claude-code-sandbox-write-boundary.md:149-158`). [verified] Row 64 shows that setting is the wider grant, so the plan names `sandbox.filesystem.allowWrite` instead. Whether `test -w` sees a sandbox denial is unknown. On Linux a read-only bind mount would make it fail, as the case study's `Read-only file system` errors suggest (:29-34), and on macOS it may pass ahead of a denied write. [unverified: not run] Either way a denied write fails the archive call and keeps the worktree.
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
33. Both cleanup scripts run under `set -euo pipefail` and source their sibling libs unguarded (`cleanup-merged-branches.sh:156-168`, `cleanup-idle-open-pr-worktrees.sh:69-76`). `review-ledger.sh` runs under `set -u` only and guards its lib source, because there "an unchecked failed `.` would continue" (`review-ledger.sh:10-17`). [verified] That `set -e` stops a script on a failed `.` is inferred from that comment and the unguarded sibling sources. [unverified: bash manual not read]
34. [engineer-verified: "we don't have to split. It's okay to keep it as is"], the engineer's answer to /plan-review's size question. Covers: shipping D1, D2 and D3 as one PR at the projected size.
35. Superseded by row 54, which moves D3 to a follow-up plan. Nothing in D1 or D2 depends on D3.
36. [engineer-verified: "Keep the rename"], the selected label answering /plan-review's question whether item 8's rename of `_OPT_IN_PILOT_KEYS` to `_PER_ACCOUNT_OPT_IN_KEYS` stays in this PR. Covers: keeping that rename. The option description was /plan-review's own.
37. [engineer-verified: "I thought when the key is on we are moving all branch records including ledger files and agent reviews"], the engineer's words this session, relayed by the dispatching session, settling that D3 stays. Covers: the intent that both the worktree's `agent-reviews/` and the narrative-ledger files are archived when the key is on.
38. `_config.sh` warns on stderr and skips a malformed or wrong-type row for a bool key (`_config.sh:278-281`, `:337-346`). It reads an unreadable state file as an absent one (`:252-263`, `:581-584`). Either way the key resolves to its schema default, `false`, and `_config_enabled` returns 1. [verified]
39. `_config_resolve_fail_safe` returns a key's default on an internal resolver failure. Its comment rests that on every key but `worktree_required` defaulting to its fail-closed direction (`_config.sh:750-772`). For this key the default is the unarchived direction. [verified]
40. Moved to the D3 follow-up plan.
41. Moved to the D3 follow-up plan.
42. `_lib_capped` is the repo's 5-second cap for "any other command that reads the filesystem and can stall on it" (`_lib.sh:43-52`). It runs uncapped with a stderr note when neither `timeout` nor `gtimeout` is on PATH (:132-139), and no cap bounds uninterruptible kernel I/O (:85-86). `_lib_status_consistent_with_cap_kill` classifies a cap kill (:142-154). [verified]
43. In a directory without `.git`, git walks up and reports an enclosing repo (`cleanup-merged-branches.sh:1096-1100`), and the script's header lists a worktree without a `.git` as a real case (:102). [verified]
44. Both scripts list branches with `%(refname:short)` (`cleanup-merged-branches.sh:752`, `cleanup-idle-open-pr-worktrees.sh:254`). `resolve_worktree_for_branch` matches `branch refs/heads/<name>` exactly (`_worktree-lib.sh:227`). [verified] So a name shortened to `heads/<name>`, because a tag shares it, resolves no worktree and reaches no archive call. [unverified: that `for-each-ref` shortens as `symbolic-ref --short` does, per `_lib.sh:5613`]
45. `_lib.sh:5738-5744` makes a private directory under `umask 077`, refuses a symlink, a non-directory or another owner (`[ -O ]`), then runs `chmod -- 700`. `_lib.sh:5731` already uses `find -maxdepth 0 -perm`. [verified]
46. A worktree's snapshots belong to the account live after `load_repo_environment`. A session in that repo resolves its account through the same `.envrc`, and a repo with no override inherits the invoking account, as its sessions would. [unverified: the architect's reading of who owns the data]
47. Moved to the D3 follow-up plan.
48. `cleanup-merged-branches.sh`'s `*` arm reaches `git worktree remove` for `dirty` and `submodules` too, because "git decides, so a refusal is reported, not predicted" (:1073-1078). [verified]
49. `run_repo_cleanup` runs under `set -euo pipefail` (:156), directly or in a backgrounded subshell that keeps errexit (:1231-1243), so a bare call there runs with errexit on. [verified]
50. Moved to the D3 follow-up plan.
51. `config-get.sh <key>` exits 0 when the key is enabled and prints the effective value (`config-get.sh:5-18`). [verified]
52. Moved to the D3 follow-up plan.
53. The repo caps code and test files at 1,000 lines (`docs/design-decisions/code-file-line-limit.md:19`), and nothing enforces it yet (:50-57). `test_cleanup_merged_branches.py` already exceeds it, at 4,649 lines. [verified: line count by Grep]
54. [engineer-verified: "All of it (Recommended)"], the option label the engineer selected after the architect's consult on whether to split this plan or cut tests at its ~2,000-line projection. Covers: splitting D3 out of this plan, cutting tests and design pieces per that consult, and dropping M3's owner and mode refusals.
55. The description attached to row 54's label, "split D3 out, cut tests, apply the design drops, and fix the volume problem", was the dispatching session's own wording. The consult's recommendations reached this revision as that session's summary, not the consult's text. [unverified: not the engineer's words]
56. Row 54 supersedes row 34 for size. The PR ships D1 and D2, and D3 moves to a follow-up plan. [verified: rows 34 and 54]
57. The engineer's intended target volume ignores POSIX modes. [unverified: relayed by the dispatching session, not checked by the architect]
58. No `chmod` can make files owner-only on a volume that ignores POSIX modes. The engineer's two choices there are to remount it with their own uid and gid and an owner-only umask, or to accept that other local accounts can read the archive. [unverified: the volume's driver and its mount options not read] On such a volume `chmod` may succeed without effect, or it may fail. If it fails, `cp -RPp` likely fails too, because `-p` preserves the mode, so every copy fails and every worktree is kept. [unverified: the driver's manual and cp(1) not read]
59. Errexit is off inside a function that an `if`, `&&` or `||` tests, so each step there must check its own status. [verified: `claude/.claude/rules/shell-script-conventions.md`, "`-e` is also suppressed for commands inside an `if`/`&&`/`||` test"; unverified: bash manual not read]
60. With D3 moved out, the ledger sweep keeps deleting stale `.jsonl` files with no copy until the follow-up lands, key on or off. So row 37's intent holds only once both plans ship. [verified: row 14, and this plan edits no `review-ledger.sh`]
61. For this key, status 0 from `_config_enabled` means `_lib_config_dir` printed a non-empty path and `claude-config.toml` there set the key `true`. A relative, empty or `/` config dir returns 2, and a missing or untraversable one reads as the default `false`, which is status 1. [verified: `claude/.claude/hooks/_config.sh:110-124`, `:848-853`, `:875-947`, `:575-584`, `:670-693`]
62. [engineer-verified: "No notice."], the engineer's typed answer this session to whether the archive being readable by other local accounts on a volume that ignores POSIX modes is acceptable, asked with options that included a runtime notice. Covers: adding no runtime notice about group or other bits. The option descriptions were the dispatching session's own proposals.
63. [engineer-verified: "Leave it out"], the option label the engineer selected this session when asked whether the account-directory symlink refusal, dropped along with M3's owner and mode refusals, should come back. Covers: leaving that refusal out. The option descriptions were the dispatching session's own proposals.
64. The Claude Code sandboxing page documents `sandbox.filesystem.allowWrite`, an array of paths, to let sandboxed subprocess commands write outside the default set, and says those paths are enforced at the OS level (https://code.claude.com/docs/en/sandboxing.md, "Configure sandboxing"). The same page lists `permissions.additionalDirectories` among the default write locations, and the permissions page says files in additional directories get the same permission rules as the working directory, so Claude's file tools can read and edit them (https://code.claude.com/docs/en/permissions.md, "Working directories"). `additionalDirectories` is therefore the wider grant. [verified: a research subagent read both pages, the first 100,000 of the sandboxing page's 111,225 characters only; unverified: that `allowWrite` leaves Claude's file tools alone, because neither page states it]
65. `worktree_canon_path` canonicalizes in a subshell, as `(CDPATH='' cd -- "$p" 2>/dev/null && pwd -P)`, and falls back to the raw input on failure (`claude/.claude/scripts/_worktree-lib.sh:57-70`). `_lib.sh:5738` runs `( umask 077; mkdir -- "$dir" )`. [verified] The new lib's functions run in the caller's shell, because both scripts source it.
66. `collect_process_cwds` takes one process snapshot, and `worktree_in_use` reads only that snapshot (`_worktree-lib.sh:99-173`). Its `lsof` branch, the one a host without procfs takes, installs an EXIT trap and then clears it (`:118-146`). The idle script's header accepts the window between the snapshot and each removal (`cleanup-idle-open-pr-worktrees.sh:42-50`). `cleanup-merged-branches.sh` also snapshots once (:841-843), but its header states no rationale the archive makes wrong. [verified]
67. `_config.sh` prints `skipping line for unrecognized key` to stderr when it reads a line whose key has no schema row (`_config.sh:282`, `:332`). [verified] `migrate-legacy-config.sh` scaffolds this key's default line into an account's file (row 17), so the line can outlive the key's removal from the schema.
68. `_lib_config_dir` reads `CLAUDE_CONFIG_DIR` and `HOME` on every call (`_config.sh:110-124`), and `_config_memo_lookup` resets its memo when either changes (`:703-714`). [verified] So a status call after `load_repo_environment` changes `CLAUDE_CONFIG_DIR` reads the new account, and the plan adds no lib test of that.
69. `<UTC stamp>` has one-second resolution, so two snapshots made within one second order by the `<pid>` and `<n>` text in their names, not by time, and the newest pick can land on the older one. The reuse check returns a snapshot only when it matches the worktree, so the cost is one duplicate snapshot, not a wrong one. [verified: the `date -u +%Y%m%dT%H%M%SZ` format, step 5, and step 6's name]
70. The dispatching session made these decisions this round. They are not the engineer's. [unverified: the session's decisions]
    - The reuse check compares only fields that describe this worktree, orders its candidates under `LC_ALL=C`, and states an outcome for each miss.
    - `cd -P` and `umask 077` run in subshells, with a test that cwd, umask and `CDPATH` survive.
    - The doc's opening paragraph states the interim scope, and the doc carries the removal note.
    - The sandbox setup step states the grant's reach (row 64).
    - The setup steps create the volume directory first and remove an empty root directory before linking.
    - The idle header gains a note, with no fresh `worktree_in_use` check.
    - A failed step-4 `mkdir` re-tests `-d`.
    - This round's test additions and fixes.
71. Dropping `ledger_files` from the manifest is the architect's choice. [unverified: the architect's choice] The field named every `<repo-hash>.*.jsonl` file, every branch's and session's included, so it joined no session-scope file to a branch. `ledger_key` and `branch` still join the branch-scope name, and `ledger_repo_root` hashes to the repo half (row 10).
72. `helpers.write_scaled_timeout_shim` (:3071) returns `False`, writing nothing, when neither `timeout` nor `gtimeout` is on PATH (`claude/.claude/tests/helpers.py:3083-3085`). [verified]
73. [engineer-verified: "no, this is fine. PRs can ship separately. Totally fine."] Covers only: shipping D1 and D2 before the ledger follow-up, with the key archiving `agent-reviews/` only in between, is acceptable, and the PRs can ship separately.
74. Row 73's question and context, as the dispatching session relays them. The engineer asked whether running cleanup after this PR would stop ledger files being archived later. The session answered that removal deletes no ledger file (row 14), that the D3 sweep archives each stale file at sweep time with no worktree needed, and that a ledger file going 30 days without an append between the two PRs is deleted with no copy (row 60). The engineer then said "You're just changing the sweeper for the ledger". [unverified: relayed; the explanation is the session's own]
75. Every `append code-review` sweeps the whole ledger directory with the fixed floor `_LEDGER_SWEEP_FLOOR_DAYS=30` (`_lib.sh:5534`, `review-ledger.sh:670-671`), through `find "$ledger_dir" -maxdepth 1 ... -mtime "+$window_days"` (`review-ledger.sh:314`). `clear-stale` uses Claude Code's `cleanupPeriodDays`, floored at 30 (`review-ledger.sh:179-181`). [verified] So a ledger file that goes 30 days without an append is deleted at the next append anywhere.
76. Once the account directory exists, no step writes the root itself. `branch_records_status` creates nothing, step 4 skips an existing directory, and every later write lands under the account directory. [verified: this plan's steps 1-8] So the account directory, not the root, is what a `test -w` probe must test, and a sandbox grant on the account directory alone suffices. The read-only-root success case pins it.
77. No source read settles whether an `allowWrite` entry naming the symlinked root, or a path through it, covers writes that land on the link's target. The case study shows the sandbox judging a Bash write through a symlink by where it lands (`docs/case-studies/claude-code-sandbox-write-boundary.md:29-34`), so the doc names the resolved target. [unverified: Verification step 4's sandboxed run settles it]
78. `~/.claude/settings.json` is this repo's tracked `claude/.claude/settings.json` through stow (`CLAUDE.md`, "Repo layout"), so an entry there edits a tracked file. Whether other config dirs link the same file was not checked. A checkout's `.claude/settings.local.json` is gitignored (`.gitignore:12`) and overrides `settings.json` at that repository's scope (`docs/skills.md:240`). [verified] That the sandbox reads `sandbox.filesystem.allowWrite` from that scope is not checked. [unverified]
79. Superseded by row 87. The reuse check no longer compares `worktree_path`, so the symlinked-path case that rested on this row's premise is dropped.
80. Bash sets `HOME` from the password database at startup when its environment lacks it, so dropping `HOME` from a subprocess's environment would resolve the real home. [unverified: recollection; the bash manual not read] The unset-`HOME` case runs `unset HOME` inside its command instead, which is safe whether or not this holds.
81. The dispatching session made these decisions this round. They are not the engineer's. [unverified: the session's decisions]
    - The sandbox setup states `allowWrite`'s reach and settings scope, weighs granting only the account directory, and gains a sandbox-denial row in the reason table and a sandboxed run in Verification step 4.
    - The doc states the write side of a mode-ignoring volume and the unrefused symlinked account directory, with no claim about other stow users.
    - Only the doc's opening paragraph states the ledger sweep's window and the manual copy, and the CHANGELOG says nothing about it.
    - This round's test additions and fixes, and the guard-deletion check in place of a fail-first run.
82. Granting only each account directory, and probing it in place of the root once it exists, is the architect's choice. [unverified: the architect's choice] It keeps the sentinel's directory outside every sandboxed command's write set. Its cost is one hand-made directory and one `allowWrite` entry per account a run selects, and a run whose repo selects an ungranted account keeps that repo's worktrees.
83. A Bash call the sandbox denies can be retried with `dangerouslyDisableSandbox: true`. Under a permission mode that grants bare `Bash`, with `sandbox.allowUnsandboxedCommands` at its default `true`, that retry ran unsandboxed with no prompt and no visible signal. [verified: `docs/case-studies/claude-code-sandbox-write-boundary.md:196-211`, `:350`, `:490-493`] Whether the engineer's sessions set that key was not checked (row 22). [unverified]
84. `README.md:494` says Claude Code scopes `.claude/settings.local.json` to the repository root. `README.md:554` says a local-scope plugin install made from the main checkout root is active in sessions anchored in a linked worktree. [verified] Whether a linked-worktree session reads `sandbox.filesystem.allowWrite` from the main checkout's file is not checked. [unverified: Verification step 4 settles it] By G1, a linked worktree's own gitignored copy of the file is deleted with that worktree.
85. No source read states how `sandbox.filesystem.allowWrite` reads a path's prefix, such as whether a leading `/` is absolute or relative to the settings file. The security review reports the relative reading for permission rules in some scopes. [unverified: row 64's research read 100,000 of the page's 111,225 characters, and the review could not read the page] So the doc marks the entry form untested, and Verification step 4 records the form that worked.
86. The idle script's `gh auth status` check prints `ERROR: gh is not authenticated` and exits 1 (`cleanup-idle-open-pr-worktrees.sh:116-119`), after argument validation (:97-104). The test shim's `{"__auth__": "unauth"}` mode makes that check fail (`claude/.claude/scripts/tests/test_cleanup_idle_open_pr_worktrees.py:65`, `:82-85`). [verified] So under that mode a refusal placed after the check prints the `gh` error instead of the archive reason.
87. The dispatching session made these decisions on the round-5 review findings. They are not the engineer's. [unverified: the session's decisions]
    - Keep the account-directory-only grant (row 82), as the security review recommended, over the platform review's whole-root grant. Document Claude's file tools and the escape hatch (row 83) as outside the grant's reach, and add no machinery against them.
    - Drop `worktree_path` from the reuse comparison, together with the symlinked-path case and its guard-deletion line, and keep it as a manifest field.
    - Accept every other finding: the escape-hatch sentence, the untested marks and retries for the grant's spelling, form and scope, the throwaway probe file, Verification step 4's extra outcomes, the dotfile-safe move, the test fixes, the reason variables and their tie to the doc's table, the stated probe order, and the two prose splits.
88. The idle script skips a branch with no removable worktree before any removal step (`cleanup-idle-open-pr-worktrees.sh:300-305`). In `cleanup-merged-branches.sh`, a merged branch with no worktree takes the verdict's `none` arm (:1036-1038), which prints `worktree: not found` and never reaches the `*` arm's archive call. [verified]
89. Bash's `${!prefix@}` expansion lists the names of every set variable whose name starts with `prefix`, so the enumeration case can read the reason variables without scanning the lib's source. [unverified: recollection; the bash manual not read] A wrong premise fails the enumeration case when it is written.
90. The dispatching session made this decision. It is not the engineer's. [unverified: the session's decision]
    - Leave the symlink refusal out for `branches` and the key directory as well as the account directory, extending row 63.
91. The dispatching session and a `plan-architect` consult on the third review round made this decision. It is not the engineer's. [unverified: the session's and the consult's decision]
    - Step 6 builds the snapshot name in the lib and creates it with `mkdir`, in place of `mktemp -d` and the check of its output, so step 8 deletes only a path this call's `mkdir` created.
    - It drops `MKTEMP_REJECTED`, renames `MKTEMP_FAILED` to `SNAPSHOT_MKDIR`, and replaces the seven-arm `mktemp` shim case with one read-only key-directory case plus one collision arm.
    - Folding a failed or empty stamp capture into `SNAPSHOT_MKDIR` is the architect's addition in this revision, because without the old output check's basename test nothing else catches it.
92. `mkdir` without `-p` either creates its path or fails, so its exit 0 shows the calling process created that directory. The repo already relies on `mkdir(2)` being atomic (`claude/.claude/hooks/_lib.sh:5707`; `claude/.claude/hooks/nudge-long-turn-subagent.sh:340`). [verified: those comments] That it fails on a path holding a symlink, a dangling one included, instead of creating the link's target, is POSIX `mkdir(2)`'s contract. [unverified: recollection; neither POSIX nor mkdir(2) read] The collision arm pins the exit-status gate, because a `[ -d ]` test would pass the read-only key-directory case and mishandle a collision.

**Mechanisms:**
- M1. The sentinel file, probed under `_lib_capped` by the status check each archive call re-runs. anchors: root, row21, row42. Lighter checks, and a second probe before the manifest `mv`, are set aside under Alternatives.
- M2. A fixed root, relocated by symlink. anchors: row1, row3, row30, row31.
- M3. A per-account subtree named by the percent-encoded canonical config dir. Each of its directories is made by one `mkdir` under a subshell's `umask 077`, the account directory gets `chmod -- 700`, and each snapshot gets `chmod -R -- go-rwx`. Every `chmod` is best-effort, because no code can make files owner-only on a volume that ignores modes. anchors: row6, row9, row54, row57, row58, row62. The owner, mode and symlink refusals `_lib.sh` uses are set aside under Alternatives (rows 45, 63).
- M4. No lock. anchors: row16, row92. Primitives checked, each set aside:
  - `_lib_acquire_append_lock`: once per process (row 16), and one cleanup run archives many worktrees.
  - `flock(1)`: unused in the repo [verified: exploration report], and absent from stock macOS [unverified].
  - Python `fcntl.flock`: a subprocess per worktree, guarding a race whose worst case is a duplicate snapshot.
  - A new `mkdir` lock: new stale-lock eviction to own.
- M5. The manifest, written last, marks a snapshot complete. anchors: root.
- M6. Moved to the D3 follow-up plan.
- M7. A per-repo status check plus a re-check inside each archive call. anchors: row7, row9.
- M8. A new lib, which every consumer gets on `git pull`. The two `set -e` cleanup scripts source it unguarded at the top, like their existing libs. anchors: row27, row32, row33.
- M9. `_config_enabled` statuses 2, 3 and 4 map to `unavailable`, which keeps every item. A malformed or unreadable enabling line and an internal resolver failure resolve to `false` instead, so they read as `disabled` and the consumer acts as with the key off. Every enabled real run's and dry run's `Archive: ready` line, and the setup doc's `config-get.sh` check, are the signal that the key read as on. List mode gives none. The cost of the fail-closed statuses is that a broken install stops removals for a default-off consumer too, and the refusal names the cause. anchors: root, row38, row39, row51.
- M10. One test helper builds every archive case's `HOME` and `CLAUDE_CONFIG_DIR` under `tmp_path` and asserts it, so no case can resolve the real root. anchors: row19.
- M11. The idle script sources `_lib.sh` for the ledger-key formula instead of a copy of it. anchors: row10, row27.
- M12. Every external command touching `<root>` runs under `_lib_capped`, except the `agent-reviews/` copy, its comparisons and step 8's removal of a partial copy. The shell builtins that touch `<root>` run uncapped, right after step 1's capped probes. A cap kill counts as a failure, except on a best-effort `chmod`. anchors: row42, row58.
- M13. The reuse check: a call whose worktree matches the newest complete snapshot on `tip_sha`, `ledger_key`, `agent_reviews` and `diff -r` returns that snapshot. Every other outcome but a cap kill falls to a fresh copy. anchors: row48, row69, row70, row87. Lighter primitives checked, each set aside under Alternatives: skipping the archive on a predicted refusal, and a documented manual prune.
- M14. A pre-scan that refuses any `agent-reviews/` entry other than a regular file or a directory, plus a refusal of a symlink at `agent-reviews` itself. anchors: row25, row54.
- M15. A `test -w` probe in `branch_records_status` of the account directory when it exists, else of the root, so the probe tests the directory the next write lands in. anchors: row22, row76.
- M16. Subshell-scoped `cd` and `umask 077`, so neither function changes the caller's cwd, umask or `CDPATH`. anchors: row65, row70.
- M17. One lib variable per refusal cause, which the tests read back through bash's `${!prefix@}` expansion to hold every value to the doc's reason table. anchors: row87, row89. Literals written only in the tests are set aside under Alternatives.

## Critical files

Give each `code-writer` dispatch this Approach and its own file list. The ledger and Out of scope are reviewer reference, and no row, G or M tag appears in a shipped file.

- The two dispatches run in order, because D2 calls D1's functions and extends `docs/agent-branch-records.md`, which D1 creates.
- Both ship as one PR (row 56). D3, the ledger sweep, is a follow-up plan that ships as its own PR (rows 54, 73), so no doc or CHANGELOG text either dispatch writes claims the ledger is archived.
- The tests follow `claude/.claude/rules/shell-script-conventions.md`: the input matrix runs against the lib directly, and the script tests hold at most one case per branch.

**D1: key, library, docs home.**
1. `claude/.claude/hooks/config-keys.psv`: append the Approach's row.
2. **New:** `claude/.claude/scripts/_branch-records-lib.sh`, sourced and not executable, holding `branch_records_status`, `branch_records_archive_worktree` and the twelve `_BRANCH_RECORDS_REASON_*` variables.
   - The header names its two consumers, says to source `hooks/_lib.sh` first, says neither function depends on the caller's `pipefail`, and cites `docs/agent-branch-records.md` as the layout's home.
   - Reuse `_config_enabled`, `_lib_config_dir`, `_lib_reviewer_round_state_key_for_branch`, `_lib_jq`, `_lib_capped` and `_lib_status_consistent_with_cap_kill`.
3. **New:** `claude/.claude/scripts/tests/_branch_records_helpers.py` and **new:** `claude/.claude/scripts/tests/test_branch_records_lib.py`.
   - The helper module holds the M10 helper, the one function through which every archive case under `claude/.claude/scripts/tests/` builds its per-command environment. It:
     - sets `HOME` and `CLAUDE_CONFIG_DIR` under `tmp_path` and asserts both lie there;
     - layers onto a base environment the caller passes, such as `conftest.py`'s `_shimmed_env` result (:156), so the script tests keep their `gh` and `direnv` shims;
     - writes `claude-config.toml` with the key's line, or a raw line the caller passes;
     - creates the root and sentinel on request.
   - The test file reads every `_BRANCH_RECORDS_REASON_*` variable's name and value from the sourced lib once, through bash's `${!_BRANCH_RECORDS_REASON_@}` expansion. One case asserts that:
     - the names are exactly the Approach's twelve causes, so an added or dropped cause fails it;
     - the values are pairwise distinct;
     - each value is non-empty, holds no newline, and contains neither `agent-branch-records`, which the root path and the sentinel's name both hold, nor `touch`, `mkdir` or `ln` as a whole word;
     - each value appears in backticks in `docs/agent-branch-records.md`, where the reason table quotes it.
   - Every lib case below that expects `unavailable` or a return of 1 asserts that `BRANCH_RECORDS_REASON` equals its arm's variable, so it cannot pass on a neighbouring arm. `DIRECTORY` and `MANIFEST` have no lib case of their own (Out of scope), so only the enumeration case holds them to the table.
   - The test file:
     - sources `hooks/_lib.sh`, then the lib, in `bash -c`, with the helper's environment passed per command;
     - runs each status case twice, under `set -u` and under `set -euo pipefail` with the function called bare;
     - runs each archive case once, under `set -euo pipefail` with the call tested by `if`, as both scripts make it;
     - starts each command with `CDPATH` set to a real directory and `umask 022`, prints `pwd -P`, `umask` and `CDPATH` after every call, and asserts them unchanged and the call's own stdout empty, on success and failure returns alike;
     - induces failures with PATH shims for `cp` and `sha256sum`;
     - parses `manifest.json` with `json.loads`;
     - never asserts which of two snapshots made within one second is the newest, since their order then follows the names' `<pid>` and `<n>` text, not time (row 69).
   - The test file's cases. Each one added this round names the surviving case it does not duplicate.
     - status:
       - `disabled` with no key row; `ready`, leaving the root's file tree unchanged;
       - `unavailable` for a root without the sentinel, or with a directory at the sentinel path, in one parametrized body. A dangling root symlink fails the same sentinel test, so it gets no case of its own. The directory arm is the only case that tells the regular-file test from an existence test;
       - `unavailable` for a read-only root with no account directory, or for a read-only account directory under a writable root, in one parametrized body skipped when run as root. The arms assert `ROOT_NOT_WRITABLE` and `ACCOUNT_NOT_WRITABLE` in turn. The second arm is the only case of the account-directory probe;
       - `unavailable` for a relative `CLAUDE_CONFIG_DIR` (status 2 from the real resolver);
       - `unavailable` for a `test` shim that outlives the cap, built with `helpers.write_scaled_timeout_shim` as `test_cleanup_merged_branches.py` does. It skips when that helper returns `False` (row 72), asserts the cap fired with `helpers.assert_cap_engaged`, and carries the `timing` marker;
       - `disabled` for a malformed enabling line (`agent_branch_records = yes`), with `_config.sh`'s warning on stderr;
       - `unavailable` with `HOME` removed by `unset HOME` inside the command after sourcing, an absolute `CLAUDE_CONFIG_DIR` and the key on. The command never drops `HOME` from the subprocess environment, because bash would then set it from the password database (row 80). It is the only case of the `${HOME:-}` read;
     - account directory:
       - config dirs `<tmp>/a%2Fb` and `<tmp>/a/b` yield distinct components;
       - a symlinked config dir encodes its canonical path;
       - a new account directory is mode 0700, and a pre-existing 0755 one is repaired to 0700;
       - a root made read-only after a first archive call has created the account directory: the status reads `ready`. A second call, after a change to the worktree's `agent-reviews/`, returns 0 with a new complete snapshot, not the first one. The case is skipped when run as root, and a `finally` restores the root's mode. It is the only case where the root is unwritable and a call must write, so it pins that no step writes the root once the account directory exists (row 76);
     - `branch_records_archive_worktree`:
       - a tree holding a nested subdirectory, a dotfile, a name with spaces and non-ASCII characters, an empty directory and a 0600 file: the snapshot matches what the test wrote, checked without `diff`, every copied file and `manifest.json` is owner-only, and every manifest field is asserted, `archived_at` in the Approach's UTC form included;
       - a `ledger_key` equal to the file name `review-ledger.sh append` writes from that worktree;
       - a worktree without a `.git`, whose `ledger_repo_root` is its own path;
       - `absent`, then `agent-reviews/` created with one file and the tip unchanged: the second call returns a new snapshot whose `agent_reviews` is `copied` and that holds the file. It is the only case where `agent_reviews` alone differs from the candidate's;
       - a symlink or a regular file at `agent-reviews`, in one parametrized body: each returns 1 with no snapshot. The regular-file arm is the only case of the any-other-type refusal;
       - a nested symlink to a sibling file inside `agent-reviews/` returns 1 with no snapshot. Its target exists, so `cp` and `diff -r` would pass it and only the pre-scan refuses it;
       - a second call on an unchanged worktree returns the first snapshot and adds none, with a manifest-less directory beside it named to sort last under `LC_ALL=C`. A changed file or a moved branch tip adds a second;
       - a reuse miss, in one parametrized body over three changes to the first snapshot: a copied file deleted, its `agent-reviews/` deleted, or its `manifest.json` replaced with non-JSON. Each next call on the unchanged worktree returns a new complete snapshot. It is the only case that alters a stored snapshot;
       - a copy failure after an earlier call's success and a change to the worktree, one case per `cp` shim:
         - shims: exit 1 after a partial copy (`COPY_FAILED`), or exit 0 after truncating one copied file (`COPY_MISMATCH`);
         - each returns 1, leaves no new snapshot directory, and leaves the earlier snapshot intact;
         - the exit-0 shim is the only case where step 6's `diff -r` finds a mismatch;
       - a key directory made mode 0500 after an earlier call's success, then a change to the worktree: the call returns 1 with `SNAPSHOT_MKDIR`, the key directory holds only the earlier snapshot, and that snapshot is intact. The case is skipped when run as root, and a `finally` restores the mode, as the read-only-root case does. Unlike that case, whose call succeeds, this call must fail, and it is the only case where step 6's `mkdir` fails for lack of write permission;
       - a non-empty directory holding a `manifest.json`, which the shim creates at the exact path step 6 builds, after an earlier call's success and a change to the worktree, so step 5's reuse check misses: the call returns 1 with `SNAPSHOT_MKDIR`, and that directory and its contents survive.
         - A `mkdir` PATH shim forces the collision. `_lib_capped` runs `mkdir` through `timeout`, which finds it on PATH.
         - The shim creates the directory named by its last argument, puts a `manifest.json` inside it, then runs the real `mkdir` on it, which fails. The shim is on PATH for the second call only, because on the earlier call it would intercept that call's own `mkdir` calls and the earlier call would no longer succeed. The earlier call has made the account, `branches` and key directories, so step 6's is the only `mkdir` the call runs.
         - The arm needs no `date` shim, no `$$` and no counter name. It needs no read-only mode either, so it runs as root.
         - It is the only case where step 6's `mkdir` fails on a path that exists, and so the only one that tells a gate on `mkdir`'s exit status from a `[ -d ]` test;
       - a `sha256sum` shim that prints nothing, or a `.git` file naming a missing gitdir so `rev-parse` fails, in one parametrized body: each returns 1 and creates no key directory. They are the only cases of step 2's failure, and the second is the only one of a failing `rev-parse` while `.git` exists;
       - two worktrees on different branches: the first call succeeds, the sentinel is removed, and the second call returns 1 with `BRANCH_RECORDS_SNAPSHOT` empty, no key directory for its branch, and the first snapshot intact. It is the only archive case whose state is `unavailable`. Its second call has no sentinel at entry, so it also stands for a sentinel missing before the first call.
4. **New:** `docs/agent-branch-records.md`, the docs-anchor target.
   - Its opening paragraph says what the key, root, sentinel and account directory are for. It also says in plain words that the archive holds only a worktree's `agent-reviews/`, and that the ledger sweep deletes each `review-narrative-ledger/*.jsonl` file once it has gone 30 days without an append, with no copy, whether the key is on or off (rows 60, 75). One more sentence says such a file can be copied by hand into the archive root before then. Only this paragraph states either fact (row 73), and the D3 follow-up replaces both sentences.
   - setup:
     - `mkdir -p ~/.local/state`, since a macOS home may lack it;
     - create the target directory on the larger volume first (`mkdir <volume path>/agent-branch-records`), because a link to a missing target leaves a dangling root that reads as a missing sentinel;
     - when `~/.local/state/agent-branch-records` already exists as a directory, move every entry it holds into the volume directory with `find ~/.local/state/agent-branch-records -mindepth 1 -maxdepth 1 -exec mv -- {} <volume path>/agent-branch-records/ \;`, which also moves the dot-named sentinel that a `*` glob skips. Then remove the emptied directory with `rmdir`, and link only once that succeeds, since a failed `rmdir` means an entry was left behind. `ln -s` onto an existing directory creates the link inside it, and the sentinel would then sit on the home volume;
     - make the root a symlink to that directory (`ln -s <volume path>/agent-branch-records ~/.local/state/agent-branch-records`). A plain `mkdir` root sits on the volume cleanup is meant to relieve, where `ENOSPC` makes every archive fail;
     - use a volume as trusted as `$HOME`, because the archive holds finding text that can quote the engineer, plus branch names and worktree paths;
     - on a volume that ignores POSIX modes, such as FAT, exFAT, NTFS or many SMB mounts, any other local account can read, modify or replace the archive unless the volume is remounted with your own uid and gid and an owner-only umask (row 58);
     - no check refuses a symlink at the account, `branches` or key directory, so an account that can write the root or an account directory can redirect later snapshots to a directory it controls (row 63 for the account directory; the extension to `branches` and the key directory is the dispatching session's decision, not confirmed by the engineer, row 90);
     - create the sentinel only after confirming the volume is mounted, and never from an agent session, because a sentinel on the bare mountpoint passes the gate while the volume is absent;
     - set `agent_branch_records = true`, then confirm that `config-get.sh agent_branch_records` prints `true` (row 51);
     - under the Claude Code sandbox:
       - outside the sandbox, run `cleanup-merged-branches.sh --dry-run`, then create each account directory its `Archive: ready (...)` lines name with `mkdir -m 700`, because the grant names that directory rather than the root (row 76);
       - add each one's resolved path on the volume to `sandbox.filesystem.allowWrite`, not its spelling through the root symlink, which is the spelling the `Archive: ready` line prints (rows 64, 77). Find the resolved path with the POSIX `cd -P <account dir> && pwd -P`, not `readlink -f`;
       - put the entry in the gitignored `.claude/settings.local.json` at the root of the main checkout of the repository whose sessions run cleanup. The grant reaches only sessions launched from that repository. Never use a linked worktree's copy, which is deleted with that worktree, nor `~/.claude/settings.json`, which stow links to this repo's tracked `claude/.claude/settings.json` (rows 78, 84);
       - say that three things are untested: the path spelling, the entry's path form, and whether a session anchored in a linked worktree reads the main checkout's file (rows 84, 85). Once Verification step 4 has run, the doc gives the exact entry text that worked and drops this note;
       - state the grant's reach. Every sandboxed command in that repository's sessions, an agent's own Bash calls included, can create, change or delete anything under the account directory. The root and the sentinel stay outside the grant, so a sandboxed Bash call cannot create the sentinel. A denied Bash call can be retried with `dangerouslyDisableSandbox: true`, which runs it unsandboxed with no prompt under a permission mode that allows bare `Bash` while `sandbox.allowUnsandboxedCommands` keeps its default `true` (row 83). So unless that key is `false`, the rule against an agent creating the sentinel is advisory for an agent's Bash calls too. It is advisory for Claude's own file tools, which run outside the sandbox (`docs/case-studies/claude-code-sandbox-write-boundary.md:62-64`), and for any other unsandboxed command;
       - the step does not use `permissions.additionalDirectories`, which also opens the archive and the sentinel to Claude's own file tools (row 64);
   - what an agent does on a refusal: report it to the engineer, and never create the root or sentinel or change the key;
   - the fail direction: a malformed or unreadable enabling line reads as off, with only `_config.sh`'s stderr warning. `unavailable` keeps every item, for a default-off consumer too, until the install is fixed or the change reverted. List mode prints no `Archive:` line, so it gives no sign that the key read as on;
   - removing the key: when the key leaves the schema, delete the `agent_branch_records` line from each account's `claude-config.toml`, because `_config.sh` warns on every read of a line for an unrecognized key (row 67);
   - a reason table with one row per `_BRANCH_RECORDS_REASON_*` value, quoting the value in backticks, with its fix:
     - the `SENTINEL` row names a dangling root symlink as one cause;
     - the `UNSUPPORTED_ENTRY` row gives `find agent-reviews ! -type f ! -type d` to list the refused entries, and says the value also covers an `agent-reviews` that is not a plain directory;
     - the `ROOT_NOT_WRITABLE` row says that under the sandbox the fix is the setup's hand-made account directory, and otherwise the root's mode;
     - the `ACCOUNT_NOT_WRITABLE` row names the account directory's `allowWrite` entry under the sandbox, and otherwise its mode;
     - one more row covers a sandbox write denial, which has no value of its own and surfaces as either not-writable value, `DIRECTORY`, `SNAPSHOT_MKDIR` or `COPY_FAILED`. Its first fix to try is the grant's other spelling, through the root symlink, and then the setup's other steps;
   - that CI checks Linux only, so macOS is unverified beyond the path name;
   - retention: nothing prunes the archive, which keeps finding text until the engineer deletes it. Deleting `<root>/<account>/` removes one account's archive, and an incomplete snapshot is safe to delete.

   Examples use placeholder paths and names only.
5. `docs/config-file.md`: the table row; 21 to 22 at :147, :179 and :264; the key added to the scaffold list at :179-184, where "all five" becomes "all six".
6. `claude/.claude/hooks/tests/config-schema-audit.md`: 21 to 22 at :3; "Remaining sixteen keys" to "Remaining seventeen keys" at :175; a `` ### `agent_branch_records` `` section among the remaining keys, modeled on `plugin_bootstrap` (:463-481). Its call site is `branch_records_status`. Its fail direction names two cases. A malformed or unreadable enabling line resolves `false`, so the consumer acts as with the key off. Statuses 2, 3 and 4 are `unavailable`, so the consumer keeps every item.
7. `install.sh:684` and `claude/.claude/scripts/migrate-legacy-config.sh:13`: 21 to 22.
8. `claude/.claude/scripts/tests/test_config_py.py`: add `"agent_branch_records": "false"` to the known-set dict (:90-96). Add the key to `_OPT_IN_PILOT_KEYS` (:26-30) and rename that list `_PER_ACCOUNT_OPT_IN_KEYS`, since the new key is not a pilot. Leave `docs/design-decisions/architect-disposition-challenge-pilot.md:346`, which names the old list, unedited, because it is a dated decision record (Axis 3 of the Scope discipline bullet in `claude/.claude/CLAUDE.md`).

**D2: cleanup scripts.**

9. `claude/.claude/scripts/cleanup-merged-branches.sh`: per the Approach.
10. `claude/.claude/scripts/cleanup-idle-open-pr-worktrees.sh`: per the Approach.
11. `claude/.claude/scripts/tests/test_cleanup_merged_branches.py`. Add no isolation fixture, because `conftest.py` already pins `CLAUDE_CONFIG_DIR` (row 19). Archive cases build their environment with item 3's helper, and their fixture repos ignore `agent-reviews/` as this repo's `.gitignore:41` does. Cases:
    - `ready`: the `Archive: ready` line, a byte-identical snapshot, and the worktree and branch gone. The case parses `manifest.json` with `json.loads` and asserts that `branch` is the removed branch, `trigger` is `cleanup-merged-branches.sh`, `worktree_path` is the porcelain path git reported for the worktree, `main_worktree_path` is the main checkout, and `tip_sha` is the branch tip recorded before the run. The same run also holds a second merged branch with no worktree. Its local and remote branch are gone, `branches/` holds only the worktree branch's key directory, and the exit is 0. It is the only key-on case whose branch takes the verdict's `none` arm, and item 12 has no counterpart, because the idle script skips such a branch first (row 88);
    - two merged worktrees on branches named so the one holding a FIFO in `agent-reviews/` sorts first, because both scripts walk branches in refname order (`cleanup-merged-branches.sh:752`, `cleanup-idle-open-pr-worktrees.sh:254`):
      - the FIFO worktree and its local and remote branch remain, and it has no snapshot;
      - its `kept (archive failed: ` line carries a non-empty reason, and the `Skipped (archive failed)` line prints;
      - the other worktree is archived and removed, and its local and remote branch are gone;
      - the exit is 1;
    - no sentinel: exit 1, the worktree and branch remain, and stderr names the reason and `docs/agent-branch-records.md` but not the sentinel path;
    - `--list-removable`: `unavailable` exits 1 with empty stdout, and `ready` prints exactly the list lines, with no `Archive:` line, and exits 0;
    - `--dry-run` with `ready` prints `Archive: ready`, exits 0 and leaves the root's file tree unchanged;
    - disabled: a real run exits 0 and removes the worktree and its local and remote branch, and `$HOME/.local/state` stays uncreated. Because the script prints worktree paths, the case deletes every occurrence of `str(tmp_path)` and of its resolved path from stdout and stderr, then asserts neither contains `archive` in any letter case. The assertion therefore holds under any test name, and the fixture's branch names hold no `archive`.
12. `claude/.claude/scripts/tests/test_cleanup_idle_open_pr_worktrees.py`, with the same helper and ignore rule. Cases:
    - `ready`, with item 11's manifest assertions and `trigger` being `cleanup-idle-open-pr-worktrees.sh`;
    - two candidates, the one holding a FIFO in `agent-reviews/` sorting first, with item 11's worktree, snapshot and reason assertions for both and exit 1;
    - unavailable, with the `gh` shim in its `{"__auth__": "unauth"}` mode: exit 1, the worktree remains, and stderr names the archive reason and `docs/agent-branch-records.md` but not the script's `gh is not authenticated` line. A status call placed after the `gh auth status` check would print that line instead (row 86);
    - a disabled run that exits 0, removes the worktree, leaves `$HOME/.local/state` uncreated, and passes item 11's path-stripped `archive` check.
13. `claude/.claude/hooks/tests/test_bash_floor.py`: add `cleanup-idle-open-pr-worktrees.sh` to `_SCRIPTS_SOURCING_LIB_SH_FIRST` (:444).
14. `docs/agent-branch-records.md`: add the worktree snapshots (layout, manifest fields, completeness rule, reuse check), the two scripts' behavior, and which account owns a worktree's snapshots under `--all-projects` (row 46).
15. `docs/scripts.md`: one sentence in each cleanup entry (:253, :269) citing `docs/agent-branch-records.md`.
16. `CHANGELOG.md`: one `### Added` entry under `[Unreleased]`. It names the key, says that turning it on makes both cleanup scripts snapshot a worktree's `agent-reviews/` before removing it, and states the refusal behavior. It says nothing about the ledger sweep's window, which only the doc's opening paragraph states.

Projected diff: about 1,650 lines. By estimate, about 50 fewer than before, because step 6's `mktemp` output check and its seven-arm shim case give way to one `mkdir`, one read-only case and one collision arm. Also by estimate, about 50 of them exist because the grant names each account directory rather than the root: the conditional probe, its two test arms and the per-account setup steps.

## Verification

1. After each dispatch, from the worktree root:
   - `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py`. Its selection must include that dispatch's test files, plus `test_doc_counts.py` and `test_config_py.py` for D1, and `test_bash_floor.py` for D2. A miss is a rule-table bug to report, not a reason to widen the run by hand.
   - `git add` the new files, then run `scripts/list-shell-files.sh | xargs -0 scripts/venv-run.sh shellcheck`, because the list holds tracked files only (row 29).
   - `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/`.
2. Once each dispatch's tests pass, run a one-time guard-deletion check in place of a fail-first run. Neutralise each guard below in turn, run its named case, see that case fail, then restore the guard. A guard whose case stays green is a test gap to fix before the dispatch is done. Run the checks as a non-root user, because the read-only cases skip as root, and a skipped case is not a failing one.
   - D1, step 1's status re-run: the two-worktree sentinel case.
   - D1, the sentinel's regular-file test weakened to an existence test: the sentinel-directory arm.
   - D1, `${HOME:-}` read as `$HOME`: the unset-`HOME` case.
   - D1, the account-directory `test -w` probe: the read-only account directory arm.
   - D1, a root probe run even when the account directory exists: the read-only-root success case.
   - D1, step 2's `rev-parse` check: the `.git`-file arm.
   - D1, step 3's refusal of any other file type: the regular-file arm.
   - D1, step 3's nested-entry refusal, with the top-level classification kept: the nested-symlink case. A green result here means `cp -RPp` or `diff -r` already rejects the link, which disproves that case's premise (row 25), so the dispatch reports it instead of adding a case.
   - D1, step 5's `agent_reviews` comparison: the `absent`-then-`copied` case.
   - D1, step 6's `mkdir` status check: the read-only key-directory case. With the check neutralised, `cp` fails into the missing directory as `COPY_FAILED`, so the reason assertion fails the case.
   - D1, step 6's `mkdir` status gate replaced by a `[ -d <path> ]` test: the collision arm.
   - D1, step 6's `diff -r`: the exit-0 truncating `cp` arm.
   - D1, step 8's removal of the partial copy: the two `cp`-shim arms (the exit-1 and the exit-0 truncating shims), which assert that no new snapshot directory is left.
   - D2, each script's `continue` past a failed archive: its FIFO case.
3. `/code-review` dispatches `claude-hook-review` for the `config-keys.psv` row (`.claude/rules/review-pipeline-dispatch.md`).
4. Manual, the engineer's call and not a merge gate. On the target volume, copy a sample tree with `cp -RPp` and rename it with `mv`, which settles rows 21 and 26 for that volume. Run `chmod -- 700` on a directory there and note its exit status and resulting mode. That result is informational: it tells the engineer which of row 58's two choices applies, and no step gates on it. Then, with a throwaway `CLAUDE_CONFIG_DIR` holding `agent_branch_records = true`, check that:
   - `config-get.sh agent_branch_records` prints `true`;
   - `cleanup-merged-branches.sh --dry-run` prints `Archive: ready`;
   - one real run against a throwaway merged worktree leaves a complete snapshot;
   - the dry run prints `Archive: unavailable` once the sentinel is renamed away.

   Then check the sandbox setup from a sandboxed Claude Code session anchored in a linked worktree. Its only added grant is the throwaway account directory's resolved path, in the main checkout's `.claude/settings.local.json`, set up as the doc says (rows 77, 84, 85).
   - `touch` a throwaway file directly in the root, under a name that is not the sentinel's, such as `sandbox-probe.tmp`. Read the denial from the first attempt, and take no `dangerouslyDisableSandbox` retry, because an unsandboxed retry succeeds and would read as a failed grant (row 83). Remove the file if it was created.
   - One real run against a throwaway merged worktree leaves a complete snapshot. Record the exact `allowWrite` entry text that worked, prefix included, and correct the doc's setup step to it, dropping its untested note.
   - When that run is denied, change one thing per retry. First run from a session anchored in the main checkout, which separates the settings scope from the spelling. Then spell the path through the root symlink (row 77). Then write the entry in the absolute-path form the sandboxing page documents for `allowWrite`, if it documents one (row 85).
   - When every retry is denied, the doc drops its grant text and says to make a real run outside the sandbox.
   - Start a sandboxed session in that repository with the volume unmounted, so the granted path is absent, and note whether Bash still runs. If it does not, the doc warns that the grant breaks every sandboxed Bash call in that repository's sessions while the volume is unmounted.

   The same steps on macOS check row 31, the condition row 30 sets. Enabling the key on a real account waits on row 6.

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
- `fsync` before deletion. A crash inside the page-cache writeback window can lose an archive whose source is already deleted.
- Splitting `test_cleanup_merged_branches.py`, which already exceeds the repo's 1,000-line limit and which this change grows (row 53). Nothing enforces that limit yet.
- A content digest per archived file in the manifest. A later phase that reads the archive would need it to detect a changed copy.
- A guard against an agent turning the key off in `claude-config.toml`. A reviewer's grep found no hook covering that file, which this plan did not re-check, and no refusal text names that step.
- A fresh `worktree_in_use` check before each idle candidate's archive. `worktree_in_use` reads the one snapshot `collect_process_cwds` takes, so a fresh check means a full process scan per candidate, which on a host without procfs runs `lsof` and clears the script's EXIT trap. It would still leave the gap between the copy and the remove (row 66).
- Script-level tests of the dry run with `unavailable`, of where the per-repo status call sits, and of the kept-archive exit under `--all-projects`. A fault in any of them changes an exit status or an output line, not what is removed. For the call's placement, that holds because each archive call re-runs the status check under the repo's own environment.
- Lib cases whose fault a later step already turns into a return of 1, or that another case pins:
  - a failed step-4 `mkdir`, or a regular file at `branches`, after which step 6's `mkdir` fails;
  - a failed manifest `mv`, which leaves no `manifest.json` and reaches the cleanup the short-copy case pins;
  - an unreadable subdirectory, after which the copy fails;
  - a nested FIFO, whose pre-scan predicate the nested-symlink case pins;
  - a cap kill on a probe after the first, such as the reuse read, whose classification is accepted on inspection;
  - a candidate whose `ledger_key` differs, which cannot occur, because the key directory is named from the same key.
- A concurrent-callers lib case. Its assertions hold however the race goes, but whether two background calls meet inside step 4 is up to the scheduler, so it would catch a missing `[ -d ]` re-test only on some runs. A missing re-test only keeps a worktree. The no-lock rule rests on step 6's `mkdir`, which creates its path or fails (row 92), and on the manifest-last rule, which the reuse case's manifest-less directory pins.
- A lib case for a failed or empty stamp capture in step 6. Only a broken `date` reaches it. Were the check missing, the name would lose only its stamp, and `mkdir` would still make the path step 8 may remove, so the cost is at most a duplicate snapshot.
