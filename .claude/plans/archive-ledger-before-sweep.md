# Archive review-narrative-ledger files before the retention sweep deletes them

## Context

Goal: when the per-account `agent_branch_records` key is on, copy each stale `review-narrative-ledger/*.jsonl` file into the agent-branch-records archive before the retention sweep deletes it, and keep the file when the archive is unavailable or the copy fails.

Ask: "So I think you plan-it for (a)" — where (a), in the session's own words (not the engineer's), is "archive `review-narrative-ledger/*.jsonl` before the 30-day sweep, under the existing key". Layout answer: selected label "Timestamped copies (Recommended)". Failure answer: selected label "Keep file, warn, exit unchanged (Recommended)". `clear-stale` answer: selected label "Yes, same code path (Recommended)".

Why now: the archive for worktree `agent-reviews/` shipped and is in use. The ledger sweep is the other removal path that destroys per-branch review data without a copy. `docs/agent-branch-records.md` § "Known limits" lists it as uncovered.

Evidence gathered this session (exploration report, not design):

- The sweep is `_sweep_stale_ledger_files LEDGER_DIR WINDOW_DAYS DRY_RUN REPORT` in `claude/.claude/scripts/review-ledger.sh` (about lines 294-324). It runs `find ... -maxdepth 1 ( -name '*.jsonl' -o -name '*.lock' ) -mtime +N`, then `rm -f` per entry. It takes no lock and covers every repo hash in the directory.
- It runs from two places: the last step of `append code-review` (about lines 678-679, window fixed at `_LEDGER_SWEEP_FLOOR_DAYS`, silent), and `clear-stale [--dry-run]` (about lines 850-858, window from `cleanupPeriodDays`, prints `evict:` lines).
- `_LEDGER_SWEEP_FLOOR_DAYS` (30) is at `claude/.claude/hooks/_lib.sh:5741`. The sweep itself is not in `_lib.sh`.
- Ledger files are `<config>/review-narrative-ledger/<repo_hash>.<branch_hash>.jsonl`, or `<repo_hash>.<session_id>.jsonl` on a detached HEAD or the default branch.
- `claude/.claude/scripts/_branch-records-lib.sh` exposes `branch_records_status` (sets `BRANCH_RECORDS_STATE`, `BRANCH_RECORDS_REASON`, `BRANCH_RECORDS_ACCOUNT_DIR`) and a worktree-specific archive function. No `hooks/*.sh` file sources anything under `scripts/`; `review-ledger.sh` already sources `hooks/_lib.sh` and `_review-ledger-lib.sh`.
- The key is already registered (`hooks/config-keys.psv`, `docs/config-file.md`); no new key is needed.
- A writer can append between the sweep's copy and its `rm`. The Approach and its ledger name the other race shapes this implies.

## Approach

With the `agent_branch_records` key on, the retention sweep copies each stale `review-narrative-ledger/*.jsonl` file into the branch-records archive, under a name taken from the file's own mtime. It deletes the file only when the copy matches it byte for byte and the file is still stale. A file the sweep cannot archive stays where it is. A failure every later file would meet, a stall or an archive directory that cannot be created, ends archiving for that sweep, and any other failure keeps only its own file. One stderr line per sweep then reports every kept file, and neither `append`'s nor `clear-stale`'s exit status changes. With the key off, the sweep behaves as today, with one exception: a config the sweep cannot read as on or off makes the archive `unavailable`, so the due files stay and the sweep warns (row 11). Row 36's answer covers the schema case of that exception. The unreadable key-line and `claude-config.toml` cases come with `branch_records_status` (G2, row 49).

**Why the copy's name comes from the file's mtime.** The sweep selects only files idle past its window (row 8), and every write to a ledger file moves its mtime (row 29). A repeat sweep of an unchanged file therefore writes the same copy name, and a file that grew gets a new one. A file whose `rm` keeps failing keeps one copy, so the design needs no reuse check, no `.<pid>.<n>` suffix and no temporary name. Only a clock set back past the window could give a regrown file an old name, and its copy would then replace the older one.

**A stem's copies are segments.** A sweep that removes a file leaves the next `append` to start a new one (row 38), so one stem's copies can hold disjoint rows. A later copy holds an earlier one as a byte prefix only when the file stayed between the two sweeps. The doc's restore rule is to concatenate a stem's copies in stamp order, skipping each copy that is a byte prefix of the next.

**One file's copy.** A new public function, `branch_records_archive_ledger_file ACCOUNT_DIR FILE` in `claude/.claude/scripts/_branch-records-lib.sh`, copies one file. ACCOUNT_DIR is the `BRANCH_RECORDS_ACCOUNT_DIR` that a `ready` `branch_records_status` set. The function returns one of three statuses:
- 0, with `BRANCH_RECORDS_SNAPSHOT` naming a copy byte-equal to FILE.
- 1, with `BRANCH_RECORDS_REASON` set to the copy-failed or mismatch reason, for a failure that belongs to FILE.
- 2, with `BRANCH_RECORDS_REASON` set to the existing directory or stalled reason, for a failure every later file would meet.

Each step checks its own status:

1. The function clears `BRANCH_RECORDS_SNAPSHOT` and `BRANCH_RECORDS_REASON`, and reads its arguments as `${1-}` and `${2-}`. An empty or absent argument, or a FILE that is not a regular non-symlink file, returns the ledger copy-failed reason before any other filesystem call.
2. `date -u -r FILE +%Y%m%dT%H%M%SZ` reads FILE's mtime as a UTC stamp, in the form snapshot names use (`_branch-records-lib.sh:327`). It runs uncapped, like that call, because it reads only the ledger file, on the volume the sweep's uncapped `find` already reads. A failed `date` or an empty stamp returns the copy-failed reason before the archive is touched.
3. `_lib_capped test -d ACCOUNT_DIR/review-narrative-ledger` decides whether the subdirectory exists, as `branch_records_status`'s capped probes do (row 54):
   - Status 0 skips the directory step, so its best-effort `chmod` runs only when the subdirectory is created.
   - A status read as a cap kill returns 2 with the stalled reason.
   - Any other status runs `_branch_records_make_archive_dirs ACCOUNT_DIR ACCOUNT_DIR/review-narrative-ledger`, below, which creates both directories or returns the directory reason.
4. `<copy>` is `ACCOUNT_DIR/review-narrative-ledger/<stem>.<stamp>.jsonl`, where `<stem>` is FILE's basename without `.jsonl`. `( umask 077; _lib_capped cp -- FILE <copy> )` writes it, with no check of what the name already holds (Out of scope). An earlier copy at that name is overwritten and keeps its own mode. A failure returns the stalled reason when `_lib_status_consistent_with_cap_kill` reads its status as a cap kill (row 52), and the copy-failed reason otherwise.
5. `_lib_capped cmp -s -- FILE <copy>` decides the result:
   - Status 0 returns 0.
   - Status 1 returns the mismatch reason.
   - A status read as a cap kill returns the stalled reason.
   - Any other status, a missing `cmp` included, returns the copy-failed reason.

Every step sends its commands' stdout and stderr to `/dev/null`, as the worktree function does (`_branch-records-lib.sh:304`, `:341`), so the function adds nothing to a sweep's output.

The function never removes anything under the archive. A failed step leaves any partial copy where it is, and the next sweep of the unchanged file overwrites it at step 4. Removing it could delete the only copy: when a concurrent sweep has just archived and removed the source, this sweep's `cp` fails, and a cleanup would remove the copy the other sweep verified.

Two reason variables join the lib, and each gets a row in the doc's reason table:
- `_BRANCH_RECORDS_REASON_LEDGER_COPY_FAILED='copying a review ledger file failed'`
- `_BRANCH_RECORDS_REASON_LEDGER_COPY_MISMATCH='the archived copy of a review ledger file differs from the original'`

**The shared directory step.** `_branch_records_make_archive_dirs ACCOUNT_DIR [DIR...]` is extracted from `_branch-records-lib.sh:298-309`, and the worktree path's behavior does not change:
- It creates ACCOUNT_DIR and then each DIR in order, each with a capped plain `mkdir` under `umask 077`. Its loop variable is `local`.
- A failed `mkdir` counts only when the directory is still missing, so a concurrent creator is tolerated.
- It then runs a best-effort `chmod -- 700` on ACCOUNT_DIR.
- It returns 1 with the directory reason when a directory stays missing. The worktree function propagates that with `|| return 1`, and the ledger function with `|| return 2`.

`branch_records_archive_worktree` calls it with its account, `branches` and key directories, in that order.

**The sweep.** `_sweep_stale_ledger_files` in `claude/.claude/scripts/review-ledger.sh` keeps its `find`, its signature and both call sites. The script runs under `set -u` (row 17), so every local is declared with a value. The function's status is each trigger's exit status (row 9), so it ends with an explicit `return 0`.

The sweep calls `branch_records_status` at most once, and keeps the state, reason and account directory in its own locals:
- With REPORT=0 (`append`), the call happens at the first due regular `.jsonl` file. An `append` with nothing due reads no key and probes nothing.
- With REPORT=1 (`clear-stale`), the call happens before the ledger directory's existence check (row 53). So `clear-stale --dry-run` shows the archive state with nothing due, and before any `append` has created the directory (row 42).
- With REPORT=1 and any state but `disabled`, the sweep prints `Archive: ready (<account directory>)` or `Archive: unavailable (<reason>)`, the cleanup scripts' wording, before that check.

Per entry:
- A `.lock` entry, or a `.jsonl` entry that is not a regular non-symlink file, takes today's path in every state.
- `disabled`: today's path.
- `ready` with DRY_RUN=1: the file counts as evicted, and nothing is copied or created.
- `ready`: a per-file helper archives the file and removes it, as below.
- `unavailable`, or any state once archiving has ended: the file stays, under the status reason or the reason of the return that ended archiving, and the sweep calls nothing for it.

The per-file helper:
1. It calls `branch_records_archive_ledger_file` with the sweep's account directory and keeps its status. After a nonzero status, an empty `BRANCH_RECORDS_REASON` reads as the copy-failed reason.
2. Whatever the status, it repeats the sweep's staleness test on that one file, with an uncapped `find <file> -maxdepth 0 -mtime +<window> -print`. It captures that `find`'s stdout and discards its stderr, so neither reaches the sweep's output.
3. When the re-test prints nothing, a concurrent sweep removed the file (row 30), or it was written after `find` listed it (row 39). The file prints nothing, counts as neither kept nor evicted, and is not a failure. A copy already made stays in the archive, as a prefix of a later one. A `find` that fails also prints nothing, which keeps the file without a warning.
4. When the file is still stale and the status was 0, `rm -f` removes it. It counts as evicted whatever the `rm`'s status, as today's path counts (row 47).
5. When the file is still stale and the status was not 0, the file stays under the reason.
6. Any status but 0 and 1 ends archiving for the rest of the sweep, whatever the re-test showed.

A status-1 failure keeps only its own file, and the sweep goes on to the next. So a file that can never be copied, such as one `cp` cannot read, stays and warns on every `append` until someone removes it, and holds back no other file. After a status-2 return, every later due `.jsonl` file stays under that return's reason, which holds for it too, as under an `unavailable` archive.

Killed calls cost a sweep at most two, about 14 seconds: the best-effort `chmod` when the sweep creates the subdirectory, then the `cp` or the `cmp` (row 40). A killed subdirectory `test` returns 2 and ends archiving, so it replaces those two rather than adding a third. Calls that answer under the cap add their own time, per call and per due file.

Three groups of archive-path calls stay uncapped, and each hangs `append` under its lock only on a volume that stalls without a signal ending the call:
- The `date -r`, the staleness `find`, step 1's file tests and the `rm -f` read or write the ledger file, on the volume the sweep's own uncapped `find` and `rm` already use (row 8). They add no stall exposure that volume does not already carry.
- The directory step's `[ -d ]` tests (`_branch-records-lib.sh:302`, `:304`) run in the shell, which `_lib_capped` cannot interrupt (row 54). They run only when the subdirectory is missing: a created subdirectory skips them for later files, and a directory failure ends archiving. So they run at most once per sweep.
- `branch_records_status`'s `[ -d "$account_dir" ]` (`_branch-records-lib.sh:158`) runs in the shell too, between its capped sentinel probe and its capped `test -w`, so `_lib_capped` cannot interrupt it (row 54). It tests the account directory, so it hangs `append` on the archive volume. The sweep calls `branch_records_status` once, under `append`'s lock, whenever a file is due (REPORT=0) or always (REPORT=1). Unlike the directory step's tests, it runs whether or not the subdirectory is missing, so it runs once per sweep.

A file whose copy always outlives the cap ends archiving on every sweep, as a stalled volume does. Nothing carries a failure to the next `append`, which tries again.

`append` holds its own ledger file's lock until it exits, so its sweep runs under that lock, as today's sweep does (row 12). The archive step lengthens the hold. A concurrent `append` to the same file that exhausts its retries meanwhile proceeds unlocked, prints the lock primitive's warning, and at worst writes a duplicate row.

The loop stays `while ... done < <(find ...)`, so the lib's outputs live in the sweep's own shell (`_branch-records-lib.sh:7`). No `case` arm in `review-ledger.sh` may sit alone on its line at two-space indent, because `test_enforce_marker_script_shape.py:2836-2842` reads every such line as a subcommand.

**What the sweep prints.** A real run of either trigger prints at most one stderr line per sweep, after the loop. It prints it when at least one due `.jsonl` file stayed because the sweep could not archive it:

```text
review-ledger.sh: retention sweep kept <N> ledger file(s): <reason>
```

- `<N>` counts every such file.
- `<reason>` is the status reason, or the first failure's reason.
- The line names no file. A real `clear-stale` names each kept file with its own reason.
- `--dry-run` prints no stderr line.
- Besides this line, the sweep's only possible `append` stderr output is `_config.sh`'s own warnings about `claude-config.toml` or the schema, which the key read can print when a file is due.

With REPORT=1, `clear-stale` also prints, on stdout:
- The `Archive:` line, as above, before any per-file line and even when no ledger directory exists.
- For an archived file that is still stale: `archive and evict: <name>`, or `archive and evict (dry-run): <name>`.
- For a kept file: `keep: <name> (<reason>)`, or `keep (dry-run): <name> (<reason>)`.
- For a file a concurrent sweep removed, or one written since `find` listed it: no line.
- For every other entry: today's `evict` lines.
- Today's summary line. Its count covers every entry the sweep runs `rm` on, whatever the `rm`'s status, as today (row 47). Under `--dry-run` it counts the files that would be removed.

**Loading the lib.** `review-ledger.sh` sources `_branch-records-lib.sh` right after `_review-ledger-lib.sh`, with a `# shellcheck source=_branch-records-lib.sh` directive and the same exit-2 guard and install message, naming the new file.

**The call-tally comment.** The append call-tally comment (`review-ledger.sh:631-649`) gains one line naming `_sweep_stale_ledger_files` and the `_branch-records-lib.sh` functions it reaches as the sweep's added calls, some of them uncapped tests on the archive volume. It names the functions rather than listing their calls, because a per-state call list restates code-owned behavior. The three uncapped groups above record which calls those are.

**Alternatives set aside.**
- Clock-stamped copy names with a `.<pid>.<n>` suffix, as an earlier revision had: a file whose `rm` keeps failing gains one copy per `append`, so that design needed a reuse check over the archive directory to bound the growth.
- The clock stamp alone, `<stem>.<UTC stamp>.jsonl` (the session's description, row 2): two sweeps of one unchanged file in different seconds write two copies, so a file whose `rm` keeps failing still grows the archive.
- The bare stem, `<stem>.jsonl`: a file that regrows after a sweep would overwrite its earlier copy, and the name would carry no timestamp, which the selected layout label asks for (row 1).
- Copying to a temporary name and renaming it into place: it stops a concurrent sweep from rewriting a copy in place (row 34). It adds a per-process name, a cleanup arm per failed step, and `.tmp` files that a killed sweep leaves behind. It has a residual of its own, an older complete copy renamed over a newer one, which also needs two overlapping sweeps plus one more event.
- Skipping `cp` when the copy at its name already equals FILE: step 1's source check already stops a sweep that starts after another removed the source. The skip narrows row 34 further, a residual that needs two overlapping sweeps of one file and a kill of the second `cp` mid-write, at one more capped call and branch.
- Writing the copy only when its name is free (`set -o noclobber`): a copy truncated by a failed `cp` would never be replaced, so its source would stay and warn on every `append` until someone removed the copy by hand.
- A hard link (`ln`) in place of the copy: no bytes move, but it fails across filesystems, and the documented setup can put the archive root on another volume (`docs/agent-branch-records.md:11-12`).
- Moving the source with `mv`: across volumes it becomes a copy and an unlink with no comparison between them, and this plan cannot cite how either `mv` implementation handles a partial copy.
- `cp -p`: it copies the source's mode, which can be group- or world-readable, and the mtime it would preserve is already in the name.
- Reading the mtime as an epoch with `stat -c%Y -- FILE || stat -f%m -- FILE`, the shape at `marker.sh:350` and `reconcile-project-plugins.sh:209-210`: the name would differ from the snapshot stamp form, and formatting the epoch needs a second `date` call whose flags differ between GNU and BSD.
- Taking the append lock to close the window between the staleness re-test and the `rm`: the lock cannot exclude the sweep's own caller, which already holds it, and it can proceed unlocked (row 12).
- Relying on `cp`'s exit status alone: an append that lands after the copy would be deleted with the source.
- The staleness re-test alone, without the byte comparison: it catches a write, but not a copy that differs for any other reason, and `review-ledger.sh` already depends on `cmp` (row 15).
- Comparing the file's mtime with the copy's stamp in place of the staleness re-test: a write between `find` and `date -r` gives the copy a fresh stamp, which the comparison then matches.
- A second staleness re-test before the copy: the one after the lib returns already covers a write at any point since `find`, and the earlier one would only save a copy that the later test keeps.
- Re-testing staleness only after a return of 0, as the previous revision did: an `append` that lands before `cmp` makes the copy differ, so that live file would warn as kept, though the next `append` does not list it.
- A separate source-gone check before the re-test: `find` prints nothing for a missing file (row 39), so one test covers a concurrent removal and a fresh write.
- Resolving the status per file, as `branch_records_archive_worktree` does per call: it repeats the key read and two capped probes per file, and one sweep could split across two states.
- Resolving the status before the loop in `append`: every `append` would read the key and probe the root even with nothing due.
- One stderr line per kept file, as the previous revision had: a kept file stays due, so N kept files repeat N lines on every `append` (row 41).
- Ending archiving at the first failure of any kind, as the previous revision did: a failure that repeats on one file, such as a source `cp` cannot read or a name too long to extend, held back every due file `find` listed after it, on every sweep. Its reason then labeled files the sweep never tried.
- Going on past every failure: a stalled volume would cost each due file its killed calls.
- Comparing reason strings in the sweep in place of a status 2: the step that saw the cap kill knows the class, and an unknown status, such as 127 from a half-updated install, then also ends archiving.
- A distinct `keep` wording for files the sweep never tried (CISO R1's second ask): only a status-2 return leaves a file untried, and its reason holds for every later file.
- Running the directory step for every file: it repeats a capped `chmod` per file under `append`'s lock, and a stall that killed only that `chmod` would cost each due file a cap period.
- Bounding the files one `append` archives (Platform R3, declined): the bound would be a count with no source to ground it. The slow but answering volume it guards also needs a backlog of due files. Every file archived before a caller's own timeout stays done, so a rerun finishes the rest.
- Caching a failed archive state between `append` calls: it adds a state file, its writer and an expiry rule, for a condition the operator ends by fixing the volume.
- Re-probing the sentinel per file, to catch a volume unmounted mid-sweep: it adds a capped call per file to narrow a window that the doc's eject line already covers.
- A corrective `chmod -- 600` after `cp` overwrites a copy: the account directory's 0700 is the access boundary on a volume that honors modes, and on one that ignores them neither helps.
- Checking `rm`'s status on the archive path, with its own reason, a `keep:` line and a stderr warning (Platform R7, declined): a failed `rm` loses nothing, because the copy compared equal first. A repeat sweep of the unchanged file rewrites that one copy (M4), at one copy and compare per such file on each `append`. Today's path already counts and prints a failed `rm` as evicted (row 47). The check cost a third reason, a doc line and a non-root test with a mode restore.
- Uncapped `cp` and `cmp`, as the snapshot copy runs: the `append` sweep runs inside `/code-review`, so the 5-second cap bounds a stall the kernel can interrupt there (row 40). A ledger file too large to copy within the cap stays, warns and ends archiving for the sweep.
- Retaining due ledger files in place, with no copy, by skipping the `rm` while the key is on: the engineer selected a copy into the archive, so the request was to archive before the sweep deletes (rows 1, 3, 5), and an in-place file is a different outcome than the one selected. That is a scope fact, not a code fact. The sweep's stated purpose is to bound the ledger directory's footprint by evicting whole stale files (`_lib.sh:5667-5670`), which a key-on account would lose. [unverified: whether a retained file would change a resumed branch's dedup scan or round state, since `append` would extend the old file instead of starting a new one (row 38); not traced]
- Archiving only in `clear-stale`, so `append` never copies: the selected `clear-stale` answer put both triggers on one code path (rows 5, 6).
- The copy logic inline in `review-ledger.sh`: it would duplicate the directory and reason handling, and lose the direct-test seam `claude/.claude/rules/shell-script-conventions.md` asks for.
- The helper in `hooks/_lib.sh`: `.claude/rules/bash-unit-test-seams.md` puts a helper there once two hooks need it, and no hook does.
- A second copy of the directory loop in the new function: the single-source-of-truth rule in `claude/.claude/CLAUDE.md` names duplication a defect, and the loop holds the archive's directory policy.
- `mkdir -p` in the new function: it would also recreate a removed archive root, which the worktree path's plain `mkdir` refuses (row 16), and it skips the account directory's `chmod`.
- Sourcing the lib lazily inside the sweep, failing safe to `unavailable`: a key-off account with a broken install would keep its files and warn on every `append`, and the one sourced lib in `review-ledger.sh` is guarded eagerly (row 17).
- Integration tests in `claude/.claude/scripts/tests/` reusing `build_archive_env`: they would import `_run` and `_seed_session` from the hooks test tree, which needs a new `select-tests.py` import rule. The hooks-side file needs only `HOME`, which `_run` already confines (row 22).
- Exiting 1 from `clear-stale` when it kept a file, as the cleanup scripts do: the selected label reads "exit unchanged", and the `clear-stale` answer was "same code path" (rows 3, 5).
- A `clear-stale: kept N file(s)` stdout summary: the per-file `keep` lines and the stderr line already count those files.

### Assumption ledger

**Root:** `_sweep_stale_ledger_files` deletes every `review-narrative-ledger/*.jsonl` file idle past its window with no copy (`review-ledger.sh:305-325`), so a branch's review history is lost even on an account that turned `agent_branch_records` on (`docs/agent-branch-records.md:76`).

**Givens:**
- G1. `_lib_review_ledger_path` owns ledger file names and keys, and `_lib_acquire_append_lock` owns write exclusion. The round-3 gate's key, the dashboard and `log-reviewer-round.sh` share them, so changing either needs a decision outside this plan. [verified: `claude/.claude/hooks/_lib.sh:5551-5559`, `:5869-5887`]
- G2. The archive's root, sentinel, account encoding and key reading form the shipped `branch_records_status` contract, which the earlier archive plan settled and its doc publishes. [verified: `_branch-records-lib.sh:117-176`, `docs/agent-branch-records.md`]
- G3. Transcript handling, including `transcript_analysis/author_outcome.py`'s check for swept ledgers, belongs to another session. [unverified: the dispatching session's constraint, relayed]

**Rows:**
1. [engineer-verified: "Timestamped copies (Recommended)"], the selected layout label. Covers: a separate timestamped copy of each swept file.
2. The option's description, "each swept file is copied to `<account>/review-narrative-ledger/<name>.<UTC stamp>.jsonl` in a sibling subtree of the existing archive account dir; no merge, no lock", was the dispatching session's. [unverified: not the engineer's words] This plan keeps that path form, the subtree and the no-merge, no-lock shape. It takes the stamp from the source file's mtime rather than the clock, a choice the description leaves open (M4).
3. [engineer-verified: "Keep file, warn, exit unchanged (Recommended)"], the selected failure label.
4. The failure option's description was the session's. [unverified: not the engineer's words] It named one stderr warning with the reason. It left `append`'s exit status alone because the row had already succeeded. It said the next sweep retries. This plan reads "exit unchanged" as both triggers' exit status, and prints one warning per sweep.
5. [engineer-verified: "Yes, same code path (Recommended)"], the selected `clear-stale` label.
6. The `clear-stale` option's description was the session's. [unverified: not the engineer's words] It named one sweep function archiving in both triggers. It named a `--dry-run` that reports the archive status and what it would archive, without copying.
7. The earlier archive plan records the engineer's intent that the key covers "ledger files and agent reviews". [verified: `.claude/plans/archive-branch-records-before-removal.md:271`, its row 37]
8. The sweep is one `find -maxdepth 1 ( -name '*.jsonl' -o -name '*.lock' ) -mtime +N -print0` loop. It runs `rm -f` on each entry, counts it, prints `evict` lines and a summary only when REPORT=1, and returns 0. [verified: `review-ledger.sh:305-325`]
9. `append` sweeps at `:679` with the fixed floor and REPORT=0, after the row write at `:653-659` either succeeded or exited 2. `clear-stale` sweeps at `:857-858` with the settings window and REPORT=1. [verified: `review-ledger.sh:653-659`, `:678-679`, `:857-858`]
10. `branch_records_status` always returns 0, creates nothing, and sets `BRANCH_RECORDS_ACCOUNT_DIR` only for `ready`. With the key off, it returns before any capped probe. [verified: `_branch-records-lib.sh:117-176`]
11. A config schema the resolver cannot answer for reads `unavailable` whatever the key says, as the doc states for the cleanup scripts. [verified: `_branch-records-lib.sh:126-130`, `docs/agent-branch-records.md:56`] So a key-off account in that broken state keeps its due files and warns, where today it deletes them.
12. `_lib_acquire_append_lock` may run once per process, `append` holds it for its own ledger file when the sweep runs, and it proceeds unlocked after five failed tries. [verified: `_lib.sh:5568-5574`, `:5593-5612`, `review-ledger.sh:653`] Only an EXIT trap releases the lock. A caller that exhausts its retries prints a `proceeding unlocked` warning, and the primitive's note calls the outcome a low-consequence duplicate line. [verified: `_lib.sh:5571-5574`, `:5595`, `:5610-5611`]
13. A `.lock` file holds only its holder's PID. [verified: `_lib.sh:5594`]
14. A branch-scope ledger file's stem is the key `_lib_reviewer_round_state_key_for_branch` prints, the same function that sets a worktree snapshot's `ledger_key`. [verified: `_lib.sh:5884-5885`, `_branch-records-lib.sh:291`] Every stem holds exactly one `.`, because both halves of a branch stem are `sha256sum` hex digests and a session id admits no `.`. So copy names from two different stems never coincide. [verified: `_lib.sh:5440-5447`, `:1216-1222`, `:5847`, `:923`, `:3479-3481`]
15. `review-ledger.sh` already depends on `cmp -s --`. [verified: `_review-ledger-lib.sh:1199`]
16. The worktree function holds the lib's only directory-creation loop. Its plain `mkdir` starts at the account directory, so the lib never creates the archive root, which the doc's setup creates by hand. [verified: `_branch-records-lib.sh:298-309`, `docs/agent-branch-records.md:7`, `:11-13`]
17. `review-ledger.sh` runs under `set -u` only, and guards its one sourced sibling lib with exit 2 and an install message. [verified: `review-ledger.sh:8-17`] `_branch-records-lib.sh` already ships, so sourcing it adds no file a stow consumer lacks. [verified: present on this branch's base]
18. `install_review_ledger_scripts` links only `review-ledger.sh` and `_review-ledger-lib.sh` into a test home, and `test_announce_branch_review_history.py` is its one caller outside `helpers.py`. [verified: `claude/.claude/tests/helpers.py:2682-2690`; callers by Grep]
19. `test_enforce_marker_script_shape.py` treats every line of `review-ledger.sh` matching `^  ([a-z][a-z|-]*)\)$` as a subcommand. [verified: `:2836-2842`]
20. `_lib_capped` caps a call at 5 seconds, and runs it uncapped with a stderr note when neither `timeout` nor `gtimeout` exists. A cap kill exits 124, 137 or 143. [verified: `_lib.sh:50-52`, `:107-140`] The snapshot copy steps run uncapped by design. [verified: `_branch-records-lib.sh:341-347`, `docs/agent-branch-records.md:62`] `_config_enabled` is uncapped by design. [verified: `_lib.sh:5261`]
21. The reason test pins the cause set at thirteen. It requires each value in the doc in backticks, with no `agent-branch-records` text and no `touch`, `mkdir` or `ln` word. [verified: `claude/.claude/scripts/tests/test_branch_records_lib.py:56-60`, `:158-174`]
22. The existing review-ledger tests run with `HOME` under `tmp_path`, no `CLAUDE_CONFIG_DIR` and no `claude-config.toml`, so the archive resolves under `tmp_path` and reads `disabled`. [verified: `claude/.claude/hooks/tests/conftest.py:489-499`, `test_review_ledger_script.py:40-54`, and the absent-file path at `_branch-records-lib.sh:74-86`, `:131-133`] That `_config_enabled` returns 1 for an absent file rests on the earlier archive plan's rows 38 and 61. [verified there; `_config.sh` not reopened]
23. A change under `claude/.claude/scripts/` selects `claude/.claude/scripts/tests/`. A shell script there also selects `claude/.claude/hooks/tests/` and `claude-skills/skills/tests/`. [verified: `claude/.claude/scripts/select-tests.py:708`, `:972`] This plan also edits `claude/.claude/tests/helpers.py`, a global trigger, so `select-tests.py` selects the full suite. [verified: `select-tests.py:536-540`, `:1063-1065`]
24. `enforce-marker-script-shape.sh` classifies a path as a ledger target only under `/.claude/review-narrative-ledger/` or the config dir's own `review-narrative-ledger/`. The account component writes `/` as `%2F`, so no archive path matches. [verified: `enforce-marker-script-shape.sh:414-425`, `_branch-records-lib.sh:51-58`]
25. The repo caps code and test files at 1,000 lines, not yet enforced. `test_review_ledger_script.py` already exceeds it. `review-ledger.sh` has 865 lines and `_branch-records-lib.sh` 380. [verified: `docs/design-decisions/code-file-line-limit.md:19`, `:50-57`; each file read to its end this session]
26. `_lib.sh:5667-5670`, `docs/hooks.md:253`, `docs/scripts.md:185`, `docs/design-decisions/branch-scoped-review-ledger.md:111-113`, `:249` and the notice at `session-marker-dashboard.sh:154` describe the live ledger's removal, which still happens. No shell file under `claude/.claude/` reads the archive's ledger copies. [verified: each read this session; Grep of `claude/.claude/**/*.sh` for `review-narrative-ledger`]
27. Cap tests in this suite carry `@pytest.mark.timing` and use `write_scaled_timeout_shim` with `assert_cap_engaged`. [verified: `test_branch_records_lib.py:244-262`]
28. The six decisions the dispatching session listed as its own (helper placement, comparing before `rm`, `.lock` handling, one status per sweep, docs, tests) are not the engineer's. [unverified: session-proposed] M1, M2, M5, M7, M10 and M13 record where this plan keeps or changes each.
29. A ledger file only grows. `append` writes each row with `>>`, and a deduplicated row only touches the mtime. No other shell file under `claude/.claude/` writes the ledger directory. [verified: `_lib_append_json_line_locked` at `_lib.sh:5671-5732`, its `touch` at `:5715` and `>>` at `:5731`; Grep of `claude/.claude/**/*.sh` for `review-narrative-ledger`]
30. The sweep runs at the end of every `append` and takes no lock, so two sessions' appends can sweep one file at once. [verified: `review-ledger.sh:678-679`, `:305-325`]
31. A shipped hook already reads a file's mtime with `date -r FILE`, and its comment states that GNU and BSD `date` both accept it. [verified: `claude/.claude/hooks/session-marker-dashboard.sh:71`]
32. CI runs only on Linux, so no CI run exercises BSD `date -r`, nor the `stat -f%m` branch of the two `stat` call sites. [verified: `.github/workflows/tests.yml:28`, `:236`]
33. macOS `date` accepts a file after `-r`. [unverified: rests on row 31's comment; not run this session] Where it does not, step 2 fails, and every due file stays and warns.
34. `cp` truncates an existing destination and rewrites it in place, so a concurrent sweep's `cp` can rewrite a copy that another sweep has already compared and acted on. If that `cp` then fails partway, the archive keeps a truncated copy of a source that is gone. [unverified: tool behavior, not run this session] The doc lists it as a known limit. Step 1's source check stops a sweep that starts after another removed the source before it opens the copy.
35. [engineer-verified: "Keep the helper (Recommended)"], the answer to whether the plan may extract the worktree function's directory loop into a helper both functions call (M12). The option's description, one copy of the archive's directory policy, was the session's. [unverified: not the engineer's words]
36. [engineer-verified: "Accept: keep and warn (Recommended)"], the answer to whether a key-off account with an unreadable config schema keeps its due ledger files and warns (row 11). The option's description, that this fails toward keeping data as the cleanup scripts do, was the session's. [unverified: not the engineer's words]
37. [engineer-verified: "Proceed anyway"], the answer to the `Projected diff:` question at about 600 lines against the 400-line threshold.
37a. [engineer-verified: "Ask the architect"], the engineer's typed answer to the same question at about 900 lines. An architect consult then recommended one PR cut to about 700 lines. [unverified: the consult's recommendation and its ~700 figure, which scales this plan's own estimates and is not a measurement]
37b. [engineer-verified: "Proceed anyway"], the answer to the `Projected diff:` question at about 900 lines, asked again with the consult's recommendation shown.
37c. [engineer-verified: "Make the cut (Recommended)"], the answer to whether the doc's list of uncapped archive calls is cut to one line before a third code-review round. The option's description of what the line keeps and drops was the session's. [unverified: not the engineer's words]
38. A sweep that removes a file leaves the next `append` to create a new one holding only the new row, so one stem's copies can hold disjoint rows. [verified: `review-ledger.sh:314`, `_lib.sh:5731`]
39. The sweep tests staleness once, in its `find`. An `append` that lands after `find` listed a file moves its mtime, and a deduplicated one only `touch`es it, which no byte comparison sees. [verified: `review-ledger.sh:317`, `_lib.sh:5714-5716`, `:5731`] `find PATH -maxdepth 0` tests PATH alone on GNU and BSD `find`, and prints nothing on stdout for a missing PATH. [unverified: the platform review's GNU run, not repeated here; BSD not run]
40. `_lib_capped` runs `timeout -k 2 5`, so a killed call returns within about 7 seconds, and a call blocked in uninterruptible kernel I/O stays unbounded. [verified: `_lib.sh:69-86`, `:129-136`] The account directory's `chmod` is best-effort, with its status ignored. [verified: `_branch-records-lib.sh:309`]
41. `/code-review` runs `append` once per finding disposition. [verified: `claude-skills/skills/code-review/SKILL.md:449`] No caller parses `append`'s stderr. [unverified: the backend review's consumer trace, not repeated here]
42. `docs/agent-branch-records.md` § "Fail direction" says the cleanup scripts print no `Archive:` line with the key off. It names a missing `Archive: ready` line as the sign of the three cases that read as off. [verified: `docs/agent-branch-records.md:56-61`, `_branch-records-lib.sh:70-73`]
43. `config-schema-audit.md` records the key's call sites and fail directions, and names only the cleanup scripts. `test_doc_counts.py` checks only that each key has its section heading there. [verified: `claude/.claude/hooks/tests/config-schema-audit.md:483-507`, `test_doc_counts.py:821-826`]
44. `clear-stale` is ungated for every agent type. [verified: `enforce-marker-script-shape.sh:880`]
45. The Cursor adapter's sandbox grant lists only config-dir directories, and `install` does not write it while a gate stays listed in `parity-exceptions.json`. [verified: `docs/cursor-adapter.md:124-125`] So a sandboxed Shell cannot write the ledger directory today, and `append` fails before its sweep in that mode. [unverified: inferred from the doc, not run]
46. `cp` without `-R` writes through a symlink at its destination and copies into a directory there. `cmp` follows a symlink and exits 2 on a directory. An overwritten destination keeps its mode. [unverified: tool behavior, not run]
47. Today's path counts an entry before its `rm -f`, discards that `rm`'s status, and prints the `evict` line after it, so a failed removal still counts and prints as evicted. [verified: `review-ledger.sh:310`, `:314-315`]
48. The test helpers already provide what the new cases need. `run_lib` forwards `env`, so a case can set `TZ`. `write_shim` writes through `helpers.write_prewarmed_stub`. `call_archive` forwards `"$@"` verbatim. [verified: `_branch_records_helpers.py:67-71`, `:246-277`, `:403-410`; `helpers.py:3113-3124`] `config_schema_text_without_key` returns text only, and `isolated_home`'s `hooks/config-keys.psv` is a link to the repo's schema. So a home whose schema lacks one key needs that link removed before the text is written, as `hook_beside_degraded_schema` does. [verified: `helpers.py:2624-2648`, `:2661-2664`; `conftest.py:489-499`] `_run` always runs the repo's `review-ledger.sh`, whose `_lib.sh` reads the repo's schema, so a degraded home needs the link `install_review_ledger_scripts` returns. [verified: `test_review_ledger_script.py:35`, `:40-54`; `helpers.py:2682-2690`] A schema with no row for the key makes `_config.sh` print a no-row warning on stderr. [verified: `_config.sh:504-506`, `test_branch_records_lib.py:516-540`]
49. A key line that cannot be read as on or off, or a `claude-config.toml` that cannot be read, also makes the archive `unavailable`, even on an account that never set the key. [verified: `_branch-records-lib.sh:74-115`, `:131-133`] The cleanup scripts already refuse on it (`docs/agent-branch-records.md:57`). The sweep inherits that contract (G2), so such an account keeps its due files and warns. Row 36's answer covers the schema case only.
50. `date -u` formats in UTC whatever `TZ` holds, and a POSIX `TZ` string with a name and a nonzero hour-and-minute offset needs no zoneinfo file. [unverified: tool behavior, not run]
52. `_lib_status_consistent_with_cap_kill` reads 124, 137 and 143 as a cap kill and returns only 0 or 1. `branch_records_status` and the worktree function already map such a status to the stalled reason. [verified: `_lib.sh:142-154`; `_branch-records-lib.sh:146-152`, `:163-170`, `:320-323`] The stalled reason's doc row says a call hit the 5-second cap. [verified: `docs/agent-branch-records.md:42`]
53. `_sweep_stale_ledger_files` returns at once when the ledger directory is missing, and `append` creates that directory before its write, so only `clear-stale` can meet a missing one. [verified: `review-ledger.sh:307`, `:538-540`]
54. `_lib_capped` calls `_lib_capped_for`, which runs its command through `timeout -k 2 N "$@"`, so a capped `test` is the external binary, and `branch_records_status` already caps `test -f` and `test -w` and maps a cap-kill status to the stalled reason. [verified: `_lib.sh:50-52`, `:133`, `_branch-records-lib.sh:146-152`, `:163-170`] The directory step's `[ -d ]` tests are shell builtins, so `timeout` cannot signal them. [verified: `_branch-records-lib.sh:302`, `:304`; that a signal never reaches a builtin in the shell's own process is unverified: tool behavior, not run]

**Mechanisms:**
- M1. One status per sweep, resolved on the first due regular `.jsonl` under REPORT=0, and before the ledger directory's existence check under REPORT=1. anchors: row9, row10, row28, row42, row53. Per-file resolution, and resolution before the loop in `append`, are set aside under Alternatives.
- M2. `branch_records_archive_ledger_file` in `_branch-records-lib.sh`, which `review-ledger.sh` sources. anchors: root, row16, row28. `.claude/rules/bash-unit-test-seams.md` item 1 extends the sibling lib whose domain covers the helper. Lighter placements checked, each set aside under Alternatives: inline in `review-ledger.sh`, and `hooks/_lib.sh`.
- M3. `cp` straight to the final name, with nothing under the archive removed on any failure. anchors: row29, row30, row34, row46. Lighter primitives checked, each set aside under Alternatives: a hard link, and a `mv` of the source. The heavier temporary name and rename is also set aside there, and the residual it would close is row 34. A check of what the name already holds is declined under Out of scope.
- M4. Copy names `<stem>.<UTC stamp of FILE's mtime>.jsonl`, read with `date -u -r`. anchors: row1, row2, row8, row14, row29, row31, row33. Lighter names checked: the clock stamp alone, and the bare stem. Lighter mtime read checked: the `stat` pair. Each is set aside under Alternatives.
- M5. A byte comparison as the lib function's last step, then the sweep's staleness re-test after every return, then today's unchecked `rm -f` after a return of 0. anchors: row12, row15, row28, row39, row47. Lighter primitives checked, each set aside under Alternatives: `cp`'s exit status alone, the re-test alone, an mtime-to-stamp comparison, and a re-test after a return of 0 only. The heavier checked `rm` is also set aside there. An append that lands between the re-test and the `rm` is still lost, as one between `find` and the `rm` is today.
- M6. Capped `test -d`, `cp`, `cmp`, `mkdir` and `chmod`. A cap-killed `test`, `cp` or `cmp` reads as stalled, a `cmp` difference as a mismatch, and every other copy failure as copy-failed. anchors: row9, row20, row40, row52, row54. Lighter primitives checked, each set aside under Alternatives: uncapped calls like the snapshot copy, and archiving only in `clear-stale`.
- M7. `.lock` entries and non-regular `.jsonl` entries take today's path in every state. anchors: row8, row13, row28.
- M8. An eager, guarded source of the lib, and a test-helper link beside the script. anchors: row17, row18.
- M9. A file that is gone, or was written since `find` listed it, prints nothing and counts as no failure, whatever the lib returned. anchors: row8, row30, row39. The lighter primitive, no check, would report a file that a concurrent sweep archived, or that is live again, as kept.
- M10. One stderr line per sweep, naming the kept count and one reason, and no change to either exit status. anchors: row3, row4, row11, row28, row41, row49.
- M11. The `Archive:` line, and the `archive and evict` and `keep` lines, under REPORT=1. anchors: row5, row6, row42.
- M12. `_branch_records_make_archive_dirs`, shared by both public functions, and run by the ledger function only when its subdirectory is missing. anchors: row16, row40. Lighter primitives checked, each set aside under Alternatives: a second copy of the loop, and `mkdir -p`. Running the step for every file is also set aside there.
- M13. The lib's input matrix in a new `claude/.claude/scripts/tests/` file, and one subprocess case per sweep branch in a new `claude/.claude/hooks/tests/` file. anchors: row21, row22, row23, row25, row27, row28, row48.
- M14. A status-2 return, for a stall or a directory failure, ends archiving for the sweep, and a status-1 failure keeps only its own file. anchors: row20, row40, row41, row52. Lighter primitives checked, each set aside under Alternatives: a stop at any failure, and going on past every failure. Reason strings compared in the sweep, distinct wording for untried files, and a failure cached between appends are also set aside there.

## Critical files

Modify:
- `claude/.claude/scripts/_branch-records-lib.sh`. Reuse `_lib_capped` and `_lib_status_consistent_with_cap_kill`.
  - `branch_records_archive_ledger_file`, under a `# shellcheck disable=SC2034` line like the worktree function's (`:241`).
  - The two reasons.
  - `_branch_records_make_archive_dirs`, and the worktree function's `|| return 1` call to it.
  - The header's description, "Sourced by", "Provides", "Neither function" and "both functions" lines, which then cover the new function and `review-ledger.sh`.
- `claude/.claude/scripts/review-ledger.sh`. Reuse `branch_records_status` and `branch_records_archive_ledger_file`.
  - The guarded source, with its `shellcheck source=` directive.
  - The rewritten `_sweep_stale_ledger_files` and its per-file archive-then-remove helper.
  - The `append` usage text says a successful `append` may print the sweep's stderr line, and that the line never changes the exit status.
  - The `clear-stale` usage text states the archive step and the `Archive:` line.
  - The sweep's header comment, and the append call-tally comment.
- `claude/.claude/tests/helpers.py`: `install_review_ledger_scripts` links `_branch-records-lib.sh`, and its docstring says so.
- `claude/.claude/scripts/tests/_branch_records_helpers.py`:
  - A `call_archive_ledger` function in `_DRIVER`, which forwards `"$@"` verbatim, as `call_archive` does.
  - A `run_archive_ledger` runner that passes zero to two arguments as given, so an absent argument and an empty one stay distinct.
  - A runner flag that calls `branch_records_status` first and passes `$BRANCH_RECORDS_ACCOUNT_DIR` as ACCOUNT_DIR.
  - Reuse `run_lib`, which already checks each call's cwd, umask, `CDPATH` and empty stdout and forwards `env`, and `write_shim`, `write_cp_shim` and `shim_path_env`.
- `claude/.claude/scripts/tests/test_branch_records_lib.py`: the two causes in `_EXPECTED_REASON_CAUSES`, and "fifteen" in the reason test's name.
- `claude/.claude/hooks/tests/config-schema-audit.md`, its `agent_branch_records` section (row 43):
  - The call-site bullet names the sweep in `review-ledger.sh` as a third caller.
  - Both "keep every item" bullets add that the sweep keeps every due ledger file, warns, and leaves its exit status unchanged.
  - The internal-resolver-failure bullet adds that the sweep then deletes due ledger files with no copy.
- `docs/agent-branch-records.md`, which writes `<config-dir>` and never a literal home path under `.claude`, as `test_doc_has_no_state_path` requires:
  - The opening paragraph names the sweep as the archive's third writer.
  - The reason table:
    - Its lead sentence covers the sweep, which keeps the file and exits as before.
    - It gains a row per new reason.
    - The copy-failed row names its permission causes, a ledger file the running user cannot read and a `review-narrative-ledger/` subdirectory it cannot write or search, and gives no remedy that deletes or moves that subdirectory, which can hold the only copy of archived rows.
    - The stalled row says a ledger copy that outlives the cap also shows it, and that it ends archiving for that sweep.
    - Both new rows say the next sweep retries.
  - A new "Ledger copies" section after "Snapshots" states:
    - which files the sweep copies, and when;
    - the layout `<account directory>/review-narrative-ledger/<stem>.<UTC stamp>.jsonl`, with the stamp taken from the file's last write;
    - that a repeat sweep of an unchanged file rewrites the same copy;
    - that a stem's copies are segments, and the restore rule the Approach gives;
    - that `<ledger_key>.*.jsonl` finds a branch's copies from a snapshot manifest's `ledger_key`;
    - what each trigger prints, with the stderr line's text, and that the line repeats on every `append` until its cause is fixed;
    - that a real `clear-stale` names each kept file with its own reason, which is how to find a file that keeps failing;
    - that `clear-stale` prints its `Archive:` line even before any ledger directory exists;
    - that a copy holds finding text and the engineer's verbatim decision quotes (`_review-ledger-lib.sh:167-176`; the row's `engineer_quote` field, `review-ledger.sh:586`) for every repo on the account, because the sweep covers every repo hash in the ledger directory. The 30-day deletion bound no longer applies to a copy. Once the sweep removes a source, the archive holds the only copy, and only a hand removal clears it;
    - a pointer to "Fail direction" for how the key is read, rather than a restatement.
  - "Fail direction":
    - The key-off line:
      - It names `clear-stale` beside the cleanup scripts.
      - It says `_config.sh`'s warning about a bad line can also appear on an `append` with a due file.
      - It says the config exception keeps due ledger files and warns.
    - The read-as-off line says those three cases also delete due ledger files with no copy. The `append` sweep shows no sign of them, and `review-ledger.sh clear-stale --dry-run` is the check.
    - The uncapped-copy line is scoped to snapshots.
    - A new line on the ledger copy, at the level of the existing snapshot line (`:62`): calls on the archive are capped at 5 seconds where `timeout` or `gtimeout` is on PATH, and a stall ends archiving for that sweep and keeps the due files. Some calls stay uncapped, and a call blocked in uninterruptible I/O ignores the cap, so a stalled archive volume can still hang `append` while it holds its ledger lock. The Approach's three uncapped groups name those calls, and neither the doc nor the call-tally comment lists them.
    - A new line on kept ledger files:
      - An `unavailable` archive keeps every due ledger file.
      - A file the sweep can never copy stays and warns on every `append` until someone removes it.
  - The owner-only sentence (`:72`) also covers ledger copies.
  - "Known limits":
    - The ledger line gives way to the two residuals: an append between the staleness re-test and the `rm` (M5), and a concurrent sweep's rewrite that fails partway (row 34).
    - The crash-consistency line also covers a ledger copy. It names ejecting or unplugging the volume soon after a run beside power loss, and says to eject only while no review or cleanup runs.
    - The pruning line also names ledger copies.
- `docs/scripts.md`: the `review-ledger.sh` entry (`:167-168`) names both sourced libs in its text and its Install bullet, and one bullet points to `docs/agent-branch-records.md` for the sweep's archive step.
- `CHANGELOG.md`: one `[Unreleased]` § Changed entry:
  - With the key on, the sweep copies before it deletes. It keeps a file it cannot copy, prints one warning per sweep, and leaves exit statuses unchanged.
  - With the key off, the sweep deletes as before. The exception is a config it cannot read as on or off, which now keeps due files and warns.
  - The change is live on `git pull`, so an account that already has `agent_branch_records` on starts copying ledger files on its first `append` after that pull.
  - A copy holds finding text and the engineer's verbatim decision quotes for every repo on the account, and the 30-day deletion bound no longer applies to it.
  - The entry points to the "Ledger copies" section of `docs/agent-branch-records.md`.

Create:
- `claude/.claude/scripts/tests/test_branch_records_ledger_archive.py`: direct tests of `branch_records_archive_ledger_file`, named for that seam beside `test_branch_records_archive.py`. It imports from `._branch_records_helpers`.
- `claude/.claude/hooks/tests/test_review_ledger_sweep_archive.py`: one subprocess case per sweep branch. It imports `_run`, `_append_args`, `SID` and `REVIEW_LEDGER_SCRIPT` from `.test_review_ledger_script`, `_seed_session` from `.conftest`, and `write_prewarmed_stub`, `install_review_ledger_scripts` and `config_schema_text_without_key` from `helpers`. Its local helpers:
  - One writes `agent_branch_records = true` into `<isolated_home>/.claude/claude-config.toml`, creates the root and its sentinel under `isolated_home`, and asserts the root lies under `tmp_path`.
  - One computes the account directory from the resolved `isolated_home/.claude`, as `branch_records_status` canonicalizes it.
  - One writes `cp` and `test` shims with `write_prewarmed_stub`. Each shim runs the real binary that `shutil.which` found before PATH was shimmed.
  - One builds the row 36 home: it calls `install_review_ledger_scripts`, removes the `hooks/config-keys.psv` link, writes `config_schema_text_without_key("agent_branch_records")` in its place, and returns the installed `review-ledger.sh` link (row 48).
  - A literal for each of the five reasons the cases name: config, sentinel, directory, stalled and ledger copy-failed. The lib's reason test and the doc table pin those values, and any drift fails these cases' whole-line comparisons. Reading them through `reason_variables` would import the scripts test tree's helpers into the hooks tree, the cross-tree import the Approach's integration-test alternative avoids.

Exercised without edits: `test_branch_records_archive.py` covers the worktree path after the directory-helper extraction. `test_review_ledger_script.py` covers the `disabled` sweep and the missing-lib guard for `_review-ledger-lib.sh`. `test_announce_branch_review_history.py` and `test_enforce_marker_script_shape.py` run against the changed script. `test_doc_counts.py` reads the audit file's per-key headings.

Dispatch split: one `code-writer` dispatch covers every file above. The doc's reason table is what the lib's reason test reads, and the sweep consumes the new function's contract, so no partition into non-overlapping, separately specifiable file sets exists. Its verification command is `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py`, which selects the full suite here (row 23).

Projected diff: ~830 lines, an estimate scaled from the previous revision's per-file estimates, not a measurement. That is above the ~800 mark, and below the ~900 the engineer answered "Proceed anyway" to (row 37b). The tests drive it, at about 520 lines: about 200 in the direct file, about 290 in the subprocess file, and about 30 across the two helper files. The production change is about 230 lines across the lib and `review-ledger.sh`, and the docs, audit file and changelog about 80. The rise from the previous ~720 is mostly the new subprocess cases and assertions this revision adds, and a recount of the production and doc edits.

## Verification

1. `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py` passes. The `helpers.py` edit makes it select the full suite (row 23). Confirm the run collected both new test files, `test_branch_records_archive.py`, `test_branch_records_lib.py`, `test_review_ledger_script.py`, `test_announce_branch_review_history.py`, `test_enforce_marker_script_shape.py`, `test_doc_counts.py` and `test_shellcheck.py`. If `test_shellcheck.py` did not run, run the documented `scripts/list-shell-files.sh | xargs -0 scripts/venv-run.sh shellcheck`.
2. `scripts/venv-run.sh ruff check` passes on the five changed or new Python files.
3. `review-ledger.sh`, `_branch-records-lib.sh` and both new test files stay under 1,000 lines (row 25).
   - `review-ledger.sh` lands at about 955 lines by this plan's estimate: 865 plus about 90 net, mostly the sweep and its per-file helper.
   - If it would pass 1,000, the sweep's header comment shrinks to a one-line pointer at `docs/agent-branch-records.md`, and no code moves.
   - If it still passes 1,000, the code-writer stops and returns the count rather than splitting the file.
4. `docs/agent-branch-records.md` no longer says the archive does not cover ledger files.
5. Each guard, removed once, makes its named case fail, and the PR body records the eight runs:
   - the `cmp` step: the direct case whose `cmp` shim exits 1;
   - the staleness re-test: both variants of the subprocess case whose `cp` shim writes the source;
   - the keep after a failed copy: the real `clear-stale` run with a regular file at the subdirectory's path;
   - the keep under an `unavailable` archive, by treating `unavailable` as `disabled`: the row 36 schema case and the real `clear-stale` run with the sentinel missing;
   - the end of archiving after a status-2 return: the three-file case whose `cp` shim exits 124;
   - `umask 077` removed from the copy's `cp` subshell: the direct case that checks the copy's mode (0600 from a 0644 FILE);
   - `umask 077` removed from the shared directory step's `mkdir`: the direct case that walks the account directory and the subdirectory for group or other permission bits;
   - the directory step's best-effort `chmod -- 700` on the account directory removed: the variant of that walk whose account directory starts at 0755.
6. One run of `date -u -r <file> +%Y%m%dT%H%M%SZ` on macOS, with its output in the PR body, settles row 33. Until someone runs it, the PR names row 33 as unverified.

The direct tests in `test_branch_records_ledger_archive.py` show:
- A copy lands at `<account directory>/review-narrative-ledger/<stem>.<stamp>.jsonl` (rows 48, 50):
  - The test sets FILE's mtime to a time whose UTC date differs from its date under a `TZ` value that has a nonzero offset (a POSIX name-plus-offset string, as row 50 describes), which it passes in the call's environment. `<stamp>` is that time as `%Y%m%dT%H%M%SZ` in UTC.
  - FILE is seeded at mode 0644, and the copy holds FILE's bytes at mode 0600. The account directory is 0700.
  - It holds under both `status_shell_options`, for a one-line FILE, an empty FILE and a FILE whose last line has no newline, with a space in `HOME`'s path.
- After a copy, no path under the account directory, the `review-narrative-ledger` subdirectory included, has a group or other permission bit. The walk covers directories as well as files, which the sibling worktree test's file-only walk (`test_branch_records_archive.py:117-119`) does not. It holds under both `status_shell_options`, in two variants:
  - The account directory does not exist beforehand.
  - The account directory exists at 0755 and the subdirectory does not. The directory step runs because the subdirectory is missing, so its best-effort `chmod -- 700` ends the account directory at 0700. The design gives no such guarantee when the subdirectory already exists at the same time, because step 3 skips the directory step then. This variant pins only the stated behavior and does not assert that case.
- A second call with FILE unchanged returns the same path and leaves one file in the subdirectory.
- A second call after FILE changes and gets a later mtime returns a new name, and the first copy keeps its bytes. One parameter grows FILE by a line. The other removes FILE and writes a new one with different rows, so the two copies hold disjoint rows (row 38).
- A regular file already at the target name that differs from FILE is overwritten, and the call returns 0.
- Each failure case below returns the named status and reason with no `BRANCH_RECORDS_SNAPSHOT`, and leaves FILE untouched. Each runs under both `status_shell_options`:
  - An empty ACCOUNT_DIR or FILE, and a call with one argument or none: 1 and copy-failed, and nothing is written. The absent cases pass no argument, not an empty one.
  - A FILE that is missing, a symlink, or a directory: 1 and copy-failed, and nothing is created under ACCOUNT_DIR.
  - A regular file at `<account directory>/review-narrative-ledger`: 2 and the directory reason.
  - A `test` shim that exits 124 for the subdirectory path: 2 and the stalled reason, and nothing is created under ACCOUNT_DIR.
  - A failing `date` shim: 1 and copy-failed, and nothing is created under ACCOUNT_DIR.
  - A `cp` shim that writes part of FILE to the target and fails: 1 and copy-failed, and the partial copy stays.
  - A `cp` shim that fails without writing, with a complete copy already at the target name: 1 and copy-failed, and that copy keeps its bytes.
  - A `cmp` shim that exits 1: 1 and the mismatch reason. One that exits 2 or 127: 1 and copy-failed. One that exits 124: 2 and the stalled reason.
  - A `cp` that outlives the cap: 2 and the stalled reason, as a `timing` case (row 27).

The subprocess cases in `test_review_ledger_sweep_archive.py` compare each pinned stderr line whole, built from the file's reason literals. They show:
- Ready, `append`, two stale regular files, and a `test` shim that logs its calls: both copies hold equal bytes and both sources are gone. The shim logged exactly one probe of the sentinel. The exit is 0, the row is recorded, stderr is empty, and stdout names neither file.
- Ready, `append`, with a regular stale file beside a stale `.lock`, a dangling symlink `.jsonl`, a symlink `.jsonl` to a regular file outside the ledger directory, a directory `x.jsonl` and a FIFO `y.jsonl`. The run ends, and only the regular file lands in the archive. The lock, both links and the FIFO are removed, the link target keeps its bytes, and stderr is empty.
- Key `false` with a ready root and sentinel, under both triggers: the stale file is removed, and no account directory appears. Stdout holds no `Archive:` line, `clear-stale`'s lines are today's, and stderr is empty.
- The row 36 home, a schema without the `agent_branch_records` row and no key line, running the installed link under `append` with two stale files: both stay, the exit is 0, and the row is recorded. Stderr's last line is the sweep line, for 2 files and the config reason, and every line before it is `_config.sh`'s no-row warning naming `agent_branch_records`, of which there is at least one. The warning ties the config reason to the schema cause rather than its other two.
- Key on with nothing due but a stale `.lock`, and the sentinel missing: the exit is 0, stderr is empty, the lock is removed, and the `test` shim records no call naming the sentinel. The two-file ready case shows the same shim records a probe when one runs.
- Real `clear-stale` with the sentinel missing: the file stays. `Archive: unavailable (<reason>)`, `keep: <name> (<reason>)` and `clear-stale: evicted 0 file(s)` print, stderr holds the sweep line, and the exit is 0.
- Real `clear-stale`, ready, with a regular file at `<account directory>/review-narrative-ledger`: the file stays. `keep: <name> (<directory reason>)` and `clear-stale: evicted 0 file(s)` print, stderr holds the sweep line, and the exit is 0.
- Ready, `append`, three stale files, and a `cp` shim that logs each call and exits 124: the shim logged exactly one call, all three files stay, and stderr holds one sweep line for 3 files and the stalled reason.
- Ready, `append`, three stale files, and a `cp` shim that logs each call, exits 1 on its first and runs the real `cp` after: the shim logged three calls. The file it failed on stays, and the other two are archived and removed. Stderr holds one sweep line for 1 file and the copy-failed reason.
- Ready, under both triggers, with a `cp` shim that writes the source after `find` listed it (row 39), in two variants. One moves the source's mtime to now and then runs the real `cp`, and a copy exists. The other runs the real `cp` and then appends a row to the source, so `cmp` differs. In both, the source stays with its latest bytes, stderr is empty and the exit is 0. `clear-stale` prints no per-file line for the file and counts 0.
- Ready, under both triggers, with a complete copy already at the name the sweep computes, and a `cp` shim that removes the source and fails: that copy keeps its bytes and the source is gone. Stderr is empty, the exit is 0, and `clear-stale` prints no per-file line for the file and counts 0.
- `clear-stale --dry-run`, ready: `Archive: ready (<account directory>)` and `archive and evict (dry-run): <name>` print. No account directory is created, and the file stays.
- `clear-stale --dry-run`, ready, with no ledger directory (row 53): stdout is exactly the `Archive: ready (<account directory>)` line.
- `clear-stale --dry-run`, sentinel missing: `Archive: unavailable (<reason>)`, a `keep (dry-run)` line and `clear-stale: would evict 0 file(s)` print, and stderr is empty.
- `clear-stale`, ready: `archive and evict: <name>` and `clear-stale: evicted 1 file(s)` print.
- A scripts copy holding `review-ledger.sh` and `_review-ledger-lib.sh` but not `_branch-records-lib.sh` exits 2, names `_branch-records-lib.sh` and `./install.sh`, and writes no ledger.

## Out of scope

- Reading archived copies back, in `show`, `render`, the session dashboard or transcript analysis. Transcript handling belongs to another session (G3), and nothing in `review-ledger.sh` reads the archive.
- Pruning the archive. The doc's "Nothing prunes the archive" limit already covers it.
- Closing the window between the staleness re-test and the `rm` with a lock (M5). It needs a reworked append-lock primitive that `log-reviewer-round.sh` shares (G1, row 12).
- Closing the concurrent-rewrite residual (row 34). A temporary name and rename would close it at the cost set out under Alternatives, and the doc lists it as a known limit instead.
- Guarding the copy name against a planted entry, or the source against a symlink swapped in after step 1's check (CISO R3 and its R2 item 4, declined). Planting either needs a second actor with write access to the ledger directory or the archive, which the account directory's 0700 excludes on a volume that honors modes (`docs/agent-branch-records.md:72`).
  - A directory, or a symlink to one, at the copy name fails `cmp`, so the file stays and warns (row 46).
  - A symlink to a regular file there passes `cmp`, so `cp` overwrites the link's target with the ledger bytes and the sweep removes the source.
  - A check of the copy name would have narrowed only the planted case, without closing the window between check and `cp`, at one more step, three direct cases and a guard-removal run.
- Flushing copies to disk before the `rm`. The doc's crash-consistency limit covers it.
- Remembering an archive failure between `append` calls. Each `append` with a due file pays the failing sweep's cost again, and the doc says the warning repeats until the cause is fixed (M14).
- Adding the archive root to the Cursor adapter's sandbox grant (row 45). `install` does not write the grant today. Once it does, a sandboxed `append` on a key-on account fails its copy, keeps its files and warns, which fails toward keeping data. That change belongs to the grant's own work.
- Gating `clear-stale` for read-only agents (row 44). It already deletes ledger files ungated, and the copy writes only into the session owner's own archive.
- A test that a snapshot manifest's `ledger_key` finds that branch's ledger copies. A copy's name starts with FILE's own stem, which the first direct case pins, and row 14 ties the stem to the key.
- Hostile-name cases (CISO R2 item 3, declined): a name with a leading `-`, a space, a `%` and extra dots, and one too long to archive. The ledger's writer names every file from hex digests or a session id (row 14), and every name reaches a command as a path under the ledger or account directory, never as a bare name. A name too long to archive fails `cp`, the copy-failed return the failing `cp` shim cases already drive. The direct cases already run with a space in `HOME`'s path.
- A canary string checked in every subprocess case (CISO R2 item 5, declined). The sweep prints only names, fixed reasons and the account directory, and every inner command's output goes to `/dev/null`. Most subprocess cases assert stderr empty, the rest compare their sweep line whole, and `run_lib` checks that each direct call's stdout is empty.
- A mixed-outcome `clear-stale` counting case, with one file archived and one kept (SDET R9's second half, declined). The two real `clear-stale` keep cases assert `evicted 0` with the file kept, which a count taken before the keep decision fails, and the ready `clear-stale` case asserts `evicted 1`. The mixed `append` case pins the stderr line's `<N>`.
- A subprocess case for a mismatch whose source is still stale (SDET R3's request for a recorded reason). The sweep branches on the lib's status only, and the mixed case's failing `cp` drives the same status-1 arm. The direct `cmp` shim case pins the mismatch reason, and the re-test case's appending variant drives a real mismatch through the sweep.
- A doc statement that ledger copies are not tamper-evident, carry no gate protection and are not decision evidence (CISO R6, declined). Its precondition is a process acting against the engineer. Nothing in this repo reads a copy back (row 26). The doc's access paragraph (`docs/agent-branch-records.md:72`), which this plan extends to ledger copies, already says account directories are not an access boundary and that any process of the same OS user can write the manifest.
- Replacing control characters in the names `clear-stale` prints. Today's `evict` lines print names as they are, and the `append` sweep prints no name.
- Editing `_lib.sh:5667-5670`, `docs/hooks.md:253`, `docs/scripts.md:185`, `docs/design-decisions/branch-scoped-review-ledger.md` and `session-marker-dashboard.sh:154`. Each describes the live ledger's removal, which still happens (row 26).
- Extending the doc's step 4 smoke test. Its `cp -RPp` and `diff -r` already exercise the file write and the read-back that the ledger copy's `cp` and `cmp` need.
- Splitting `test_review_ledger_script.py` under the 1,000-line limit. The new subprocess cases go in their own file instead (row 25).
- A restore command. Restoring stays a manual `cp`, as for snapshots.
- Copying a ledger file at worktree removal. Removal deletes no ledger file (earlier archive plan row 14).
