# Record the reviewed state per ledger round

## Context

Goal: have `review-ledger.sh append` record the git state each review round reviewed, so a later reader can place a finding's cited line inside or outside the preceding fix's delta.

Ask: GH-1481, "Record the reviewed state per ledger round so later findings can be placed inside or outside the preceding fix". The engineer started it with "Ok let's start 1481 in that case". Scope narrowing, from the engineer's selected labels: "Drop item 2 (Recommended)", "Two new row fields (Recommended)", "Record only, document the limit (Recommended)". Later widening, from the engineer: "Fair enough, we need to fix ordering", and the selected labels "Fold into GH-1481" (the ordering edit to `code-review/SKILL.md` ships in this PR) and "No, follow-up (Recommended)" (no `review-ledger.sh place` script in this PR). Further widening, from the engineer: "I think option 1 is the only viable option and additional size has to be estimated because we can't balloon storage space", and "Sure option 1", answering whether to take the pin design to the architect at about 1,040 lines. Narrowing, from the engineer: "Not a valid concern. Gold-plating. Here's why. If a credential is staged, it should be removed immediately. We never wait for a leaked credential to be pruned.", answering the security reviewer's concern that a pin keeps a mistakenly staged credential past git's prune window. The plan therefore carries no credential-release procedure. "Option 1" is the session's own label for pinning the recorded objects under a local ref, and the pin design is the session's and the Fable consult's proposal.

Why now: `docs/cost-levers-considered.md` defers the "steering review fixes away from fix-written churn" lever for more data. Nothing records the state a round reviewed. The branch reflog places cumulative tips only approximately, and staged `/code-review` rounds run on the index before any commit.

Intended outcome: every ledger row carries `reviewed_head` (`git rev-parse HEAD`) and `reviewed_tree` (`git write-tree`), best-effort. `git diff <previous round's tree> <this round's tree>` plus the row's cited range then says whether a line sat inside the fix delta, when the earlier round's rows were appended before its fix was staged and both trees still resolve. Rows written before this ships carry no state. No measurement script is added, and `code-writer` output is unchanged.

Decisions the engineer's labels settled:
- Proposal item 2 (normalize an ADDRESS `--source`) is dropped. The code already normalizes ADDRESS, DEFER and SETTLED sources (`review-ledger.sh:508-509`) and rejects an ADDRESS row with no `--source` (`_review-ledger-lib.sh:227`). The issue text is stale on this point.
- State lives in two additive row fields, not a sibling file. `schema_version` goes 5 to 6, and nothing reads it today.
- Pin refs. The engineer chose to pin the recorded objects under a local ref, because the analyses that read these rows run across many merged PRs, and a squash-merge leaves the recorded objects prunable (rows 39 and 40 give the mechanism). The plan estimates the added storage and does not bound it. The engineer chose to defer a bound with a stated trigger, by the selected label "Defer with a trigger (Recommended)".

Ordering: `claude-skills/skills/code-review/SKILL.md:449` gains one clause, so a round's ledger appends run before any fix is dispatched or applied. A recorded state then reflects the tree the round reviewed, and a reader can place a line against the preceding fix's delta. The docs still say that prose can be skipped, and name every outcome of a late append.

Not covered: register item 1 (which finding's suggested fix wrote each line), and the path form of citations in findings files.

Exploration evidence (a subagent report, checked against source for the item-2 correction):
- Row is built at `review-ledger.sh:577-588` with `id` spliced on at about `:596-606`. The 4095-byte cap is at `:37`.
- The dedup key is an explicit jq field list at `:654`. The retry check at `_review-ledger-lib.sh:~718-735` deletes `id`, `schema_version`, `event_time` and `source_repo_hash` and compares the rest.
- `test_the_dedup_key_field_tables_name_every_field_of_a_row_the_append_writes` (`test_review_ledger_script.py:2974`) requires each row field to appear in `_DEDUP_KEY_CHANGES` or `_DEDUP_KEY_EXEMPT_CHANGES` (`:2845-2854`).
- `render` reads a fixed 15-field tuple positionally (`_review-ledger-lib.sh:~527-575`, `:650-659`, `:747`). `show` passes fields through.
- The call tally comment at `review-ledger.sh:~626-646` lists the git and jq calls that can hang. It needs the two new git calls.
- No script pins `write-tree` objects. `evals/review_bench/defects.py:470-480` already pins under `refs/review-bench/` with `update-ref`.
- Other tests build full-field rows: `test_review_ledger_script.py:153` and `:328`, `claude/.claude/scripts/tests/test_author_outcome.py`, `claude/.claude/hooks/tests/conftest.py:109-142`.
- Docs naming the format: `docs/scripts.md:167-211`, `docs/hooks.md`, `docs/transcript-analysis.md:1646`, and the usage text at `review-ledger.sh:48-95`.

## Approach

`review-ledger.sh append` stores two new fields on every row: `reviewed_head`, HEAD's commit id, and `reviewed_tree`, the index written as a tree object from a private copy of the index. Each recorded tree is pinned under a local ref named by its own id, so `git gc` keeps it after a squash merge, and Settled question 10 estimates what that keeps. `/code-review`'s ledger step gains one clause, so a round's appends run before its fix is dispatched or applied.

The rest of the design, one fact per item:
- The capture and the pin run before the append's `umask 077`.
- `append` pins a non-empty `reviewed_tree` under `refs/review-ledger/<YYYY-MM>/<tree id>`, and leaves `reviewed_head` unpinned (M14).
- A failed pin prints its own stderr note, and the row lands with both fields.
- Every capture and pin call unsets git's redirect variables.
- `write-tree` runs with `core.splitIndex` off (M3).
- A failed capture stores `""`, and the append goes on.
- A failed step prints one stderr note, except a HEAD look-up git refuses, a missing index file, and a `write-tree` that fails on an unmerged index (M2).
- Both fields stay out of the dedup key and the retry check.
- `schema_version` becomes 6.
- `render` and `show` are unchanged.
- `docs/scripts.md`'s `review-ledger.sh` entry states, one topic per item:
  - How to pick and pair a round's state.
  - How to place and count a cited line.
  - What a delta can hold besides the fix.
  - How long the objects resolve.
  - What a pin keeps.
  - Which git use outside the script sends, deletes or lists a pin.
  - How to measure and audit the pins, and how to delete a month's pins.
  - Which empty fields print a note, and where.
- `docs/hooks.md` § "Gate deadlock recovery" names a ref lock that a cap-killed pin can strand (M14).
- The ordering clause is prose, so the docs still name every outcome of a late append (M6, M13).

**Design.**

- Seven functions join `_review-ledger-lib.sh`, added after `_review_ledger_site_hash` (`:395-446`) except the predicate (M1):
  - `_review_ledger_is_object_id VALUE` is true for exactly 40 or 64 lowercase hex digits. Its character class lists the digits, as `_review_ledger_is_row_id` does (`:115-123`).
  - `_review_ledger_repo_git REPO_ROOT INDEX_FILE GIT_ARGS...` runs `_lib_capped git -C REPO_ROOT GIT_ARGS...` with stderr discarded, in a subshell that unsets `GIT_DIR`, `GIT_WORK_TREE`, `GIT_COMMON_DIR` and `GIT_INDEX_FILE`. A non-empty INDEX_FILE is exported as `GIT_INDEX_FILE` inside that subshell. It returns git's status or the cap's. `_review_ledger_confirm_ignored` (`:948-953`) calls it in place of its own subshell, so the four-name list has one home in the lib (M11).
  - `_review_ledger_capture_note STEP CAUSE` prints `review-ledger.sh: the reviewed state was not fully recorded (STEP: CAUSE). Any row this append writes has an empty reviewed-state field.` to stderr (M2). Each caller words CAUSE as `timed out, exit N` after a cap-kill status, as `exit N` after any other failed step, or as the output fault, such as `no object id in the output`. A probe that times out after a failed `write-tree` gives `exit N, then ls-files -u timed out, exit M`, so the note names the call that timed out.
  - `_review_ledger_reviewed_head REPO_ROOT` runs `_review_ledger_repo_git REPO_ROOT "" rev-parse --verify -q 'HEAD^{commit}'`. By outcome:
    - A cap-kill status (row 51) prints the note (`HEAD look-up`, `timed out, exit N`) and returns 1.
    - Any other nonzero status, as on an unborn branch or in a repository git cannot read, prints nothing and returns 0 (M2).
    - Exit 0 with output the predicate rejects prints the note (`HEAD look-up`, `no object id in the output`) and returns 0.
    - Otherwise it prints the id and returns 0.
  - `_review_ledger_reviewed_tree REPO_ROOT` runs these steps, each only after the one before it succeeds:
    1. It resolves the index path with `_review_ledger_repo_git REPO_ROOT "" rev-parse --path-format=absolute --git-path index`. A nonzero status or an empty path prints the note (`index path look-up`) and returns 1 on a cap-kill status, else 0.
    2. When no file exists at that path, as in a repository that never staged anything, it returns 0 silently.
    3. It makes a work directory with `mktemp -d "${TMPDIR:-/tmp}/review-ledger-index.XXXXXX"`. `mktemp`'s own stderr is discarded. A failure prints the note (`temporary directory`, `mktemp failed`) as the one line and returns 0. A relative result gets `$PWD/` prefixed, because `git -C` changes directory before git reads `GIT_INDEX_FILE`.
    4. It calls `_review_ledger_reviewed_tree_in REPO_ROOT INDEX_PATH WORK || status=$?`. Then, on every path, it runs `rm -f -- "$work/index" "$work/index.lock"` and `rmdir -- "$work" 2>/dev/null` and returns that status, as `render` does with its own work directory (`:1145-1152`, row 18).
  - `_review_ledger_reviewed_tree_in REPO_ROOT INDEX_PATH WORK` is that function's body:
    - It runs `_lib_capped cp -- INDEX_PATH WORK/index 2>/dev/null`. A nonzero status prints the note (`index copy`) and returns 1 on a cap-kill status, else 0. `write-tree` therefore never reads a copy `cp` did not finish (row 53).
    - It then runs `_review_ledger_repo_git REPO_ROOT WORK/index -c core.splitIndex=false write-tree --missing-ok` (M3, M12). By outcome:
      - A cap-kill status prints the note (`write-tree`, `timed out, exit N`) and returns 1.
      - Any other nonzero status runs the probe `_review_ledger_repo_git REPO_ROOT WORK/index ls-files -u` on the same copy. By the probe's outcome (M2):
        - Exit 0 with output means an unmerged index (row 22), so it returns 0 silently.
        - A cap-kill status prints the note (`write-tree`, `exit N, then ls-files -u timed out, exit M`) and returns 1.
        - Any other outcome prints the note (`write-tree`, `exit N`, with write-tree's status) and returns 0.
      - Exit 0 with output the predicate rejects prints the note (`write-tree`, `no object id in the output`) and returns 0.
      - Otherwise it prints the id and returns 0.
  - `_review_ledger_pin_tree REPO_ROOT TREE_ID MONTH` runs `_review_ledger_repo_git REPO_ROOT "" update-ref "refs/review-ledger/MONTH/TREE_ID" TREE_ID`, and returns 0 on every path (M14). By outcome:
    - Exit 0 prints nothing.
    - A cap-kill status prints the pin note with `timed out, exit N` and runs no probe.
    - Any other nonzero status runs the probe `_review_ledger_repo_git REPO_ROOT "" rev-parse --verify -q` on the same ref. By the probe's outcome:
      - Exit 0 with output equal to TREE_ID means another appender already pinned the tree, so it prints nothing.
      - A cap-kill status prints the note with `exit N, then rev-parse timed out, exit M`.
      - Any other outcome prints the note with `exit N`.
    - The pin note goes to stderr: `review-ledger.sh: the reviewed tree was recorded but not pinned (update-ref: CAUSE). git gc can delete it once nothing else reaches it.`
- `append` moves the `EVENT_TIME` assignment and its comment (`review-ledger.sh:548-555`) up to just after the site-hash step (`:531-536`). It then captures both values and pins the tree, all before `umask 077` (`:538-539`), so git writes its objects, the ref file and the ref's directory under the invoking shell's umask (M10). The code is `REVIEWED_TREE=""`, then `if REVIEWED_HEAD=$(_review_ledger_reviewed_head "$REPO_ROOT"); then REVIEWED_TREE=$(_review_ledger_reviewed_tree "$REPO_ROOT"); fi`, then `if [ -n "$REVIEWED_TREE" ]; then _review_ledger_pin_tree "$REPO_ROOT" "$REVIEWED_TREE" "${EVENT_TIME:0:7}"; fi`. A HEAD look-up that timed out therefore skips the tree capture and the pin, and an empty tree is never pinned (M12, M14).
- Each value is captured once per invocation, like `event_time`. The build (`:571-588`) gains `--arg reviewed_head` and `--arg reviewed_tree`, with the two keys between `site_hash` and `event_time`.
- Every disposition records state, CLEAN, CONSULT, carries and `--relog` rows included (M6).
- The dedup key list at `review-ledger.sh:654` stays as it is, which leaves the two fields out. The retry check's `del(...)` at `_review-ledger-lib.sh:728` gains `.reviewed_head, .reviewed_tree` (M4).
- `_LEDGER_SCHEMA_VERSION` goes from 5 to 6 (M5).
- Every new lib line sits inside a function, because `test_skills.py:8306-8337` sources the lib alone under `set -u`.
- `code-review/SKILL.md:449` gains "before dispatching or applying any fix and" ahead of its existing "before review-findings persistence below" (M13). Critical files item 8 gives the line, and item 9 pins it.

**Settled questions.**

1. *Best-effort semantics.* M2 lists which empty fields print a note and which stay silent. An unstaged edit a reviewer read is not in the index tree, and the docs say so. The capture leaves git hooks enabled, and no case covers one. The hooks it can reach, one fact per item:
    - git is recalled, not run, to fire a `post-index-change` hook when `write-tree` rewrites the copy.
    - That hook runs inside `write-tree`'s cap, so a slow one ends in the `write-tree` note.
    - `update-ref` fires a `reference-transaction` hook (row 90), which likewise runs inside its cap.
    - M14 says why hooks stay on.
2. *Hang caps and the call tally.* Every git call and the index copy run under `_lib_capped`. M12 gives the bound and the uncapped calls, and Critical files item 2 gives the tally comment's text.
3. *4095-byte cap.* The overflow message stays true at both id widths (row 14).
4. *Retry check.* Its deny-list needs both fields. Without them, a retry after HEAD or the index moved reads as "no longer live" while the append itself would dedup it (row 10).
5. *Round selection, pairing and placement.* Critical files item 5's blockquote states the rule, and M6 gives the reasons the blockquote leaves out.
6. *Retention.* The append pins each recorded tree (M14), so a tree resolves until its pin is deleted, unless the pin failed. Rows 39 and 40 say how long an unpinned tree or a `reviewed_head` resolves. Critical files item 5's blockquote says how an unresolvable tree or head affects placement. M14's orphan-pin item lists the sources of a pin no row records. The plan gives no procedure for content staged by mistake (row 116).
7. *`docs/cost-levers-considered.md`.* The change lands as a dated follow-up line, not an edit to the section's list (row 33). Critical files item 7 gives its text.
8. *Tests.* Fixture updates and new cases are listed per file in Critical files.
9. *Dispatch split.* One `code-writer` dispatch (M9).
10. *Storage.* The engineer required the added size to be estimated (row 6) and deferred a bound with a trigger (row 101). The figures below are an estimate, not a bound, because nothing in this PR caps or expires what the pins keep. KB means 1,000 bytes and KiB 1,024.

    What a pin adds:
    - No object, because `git add` and the capture already wrote them.
    - Nothing before `gc.pruneExpire`, because `git gc` keeps an unreachable object until it is older than that anyway (row 39).
    - After that, what nothing else holds. For each path the preceding fix changed, that is the staged blob and one tree per directory level on the path. It also takes in committed content that a later history rewrite drops, because the pin still reaches it.
    - One ref per distinct tree.

    Assumed inputs, none measured:
    - 8 rounds per PR, with one recorded tree per round.
    - 10 changed paths in round 1 and 3 in each later round, so 31 path versions per PR.
    - 5 trees per path, as for `claude/.claude/hooks/tests/<file>`.

    The floor, with every version stored as a delta (row 94):
    - A blob delta of 0.1 to 2 KB and five tree deltas of about 50 bytes give 0.35 to 2.25 KB per path version.
    - That is about 11 to 70 KB per PR.
    - Per 1,000 PRs it is about 11 to 70 MB.
    - These delta figures hold only after a `git gc` that repacks every pack. `git gc --auto` usually packs only new objects, and a new pack deltas only against objects inside itself, so the first version of each path in each pack is stored whole (row 103).

    The ceiling, with no delta credit, from this repository's file sizes and directory widths (row 104):
    - Each path version is a whole compressed blob. At an assumed 3:1 to 5:1 zlib ratio, a version of each file row 104 sizes takes:
      - `review-ledger.sh`: 9 to 15 KB.
      - `docs/scripts.md`: 23 to 38 KB.
      - `test_review_ledger_lib.py`: 29 to 48 KB.
      - `test_review_ledger_script.py`: 45 to 75 KB.
      - `claude/.claude/hooks/_lib.sh`: 62 to 103 KB.
    - Each tree on the path is whole too. A tree entry holds a mode, a space, the name, a NUL and a 20-byte raw id (32 for SHA-256), and the ids barely compress. At an assumed 30-character name and row 104's entry counts, `claude/.claude/hooks/tests` is about 9 KB, and `claude/.claude/scripts/tests` about 11 KB.
    - With an assumed 10 to 20 KB of whole trees per path version, a version takes about 19 to 123 KB.
    - That is about 0.6 to 3.8 MB per PR.
    - Per 1,000 PRs it is about 0.6 to 3.8 GB.
    - The ceiling is reached only when no two versions of a path share a pack. Real growth sits between the floor and the ceiling, nearer the floor after a full repack.

    Where `git gc` never runs:
    - Each pin's ref is a loose file of one file-system block, commonly 4 KiB. That is 32 KiB per PR and about 33 MB per 1,000 PRs.
    - `git pack-refs`, which `git gc` runs (row 103), moves each ref into `packed-refs` at about 110 bytes, or 160 for SHA-256 ids. That is about 0.9 MB per 1,000 PRs.
    - Each object also sits loose, at one block or more. A path version's blob and five trees take at least 24 KiB, so about 0.76 MB per PR.
    - The pin writes none of those objects, as the first item under "What a pin adds" says. Where `git gc` never runs, nothing deletes them, pin or no pin, so the pin decides only whether a later `git gc` packs them or deletes them.

    Inputs that add pins beyond one per round:
    - A round whose appends straddle its fix staging records one tree on each side (Critical files item 5's late-append outcomes).
    - M14's orphan pins each keep a tree no row records.
    - Each extra pin keeps only the path versions no other pin holds, plus one ref.
    - With `core.logAllRefUpdates=always`, each pin also gets a reflog file, which `git pack-refs` does not pack (row 106).

    The figures describe this repository's shape: large shell, test and docs files, up to five directories deep, in directories of 150 to 190 entries. Every stow consumer's repository where `/code-review` runs takes the same default growth, scaled by its own file sizes and directory widths.

    The anchor is row 104's `size-pack`. Against it, the floor's 11 to 70 MB per 1,000 PRs is between about a quarter of and one and a half times the current packed size. The ceiling is 13 to 84 times it.

    Still unmeasured:
    - This repository's real rounds per PR, paths per fix and appends per round.
    - The deltas git actually chooses, and how often a full repack runs.
    - A large file in the index at a reviewed round that no kept commit holds, whether it was never committed or a history rewrite later dropped it. It stays at its packed size for each version a recorded tree holds, and no figure bounds it.

    The docs give the measuring command, which Verification step 7 checks on a scratch repository before and after `git gc`. Out of scope gives the proposed trigger at which the engineer decides on a bound.

**Assumption ledger.**

**Root:** a ledger row records which finding a round raised and how it was decided, but not the git state the round reviewed. A later reader therefore cannot diff two rounds' states to tell whether a cited line sat inside the fix written between them.

**Givens:**

- **G1.** Rows written before this change carry no state, and none can be backfilled. Reason: the state they reviewed was never captured, and the branch reflog places tips only approximately (Context). `[verified: review-ledger.sh:580-588]`
- **G2.** `git gc` can delete an object that no ref, index or reflog reaches once it is older than git's prune expiry (row 39). Reason: git imposes it. A pin is a ref, so M14 rests on the same rule. `[verified: git-gc(1), Git 2.50.1; row 39]`
- **G3.** A git command that writes an index takes that index's `.lock` file, and the session's own `git add` and `git commit` contend for the same lock. Reason: git imposes it. `[unverified: git lock-file behavior recalled, not run this session]`
- **G4.** Linked worktrees of one repository share every ref under `refs/` except a few per-worktree namespaces, such as `refs/worktree/` and `refs/bisect/`. Reason: git imposes it. `[unverified: git-worktree(1) REFS section recalled, not read this session]` The pin class's real-objects case checks it for `refs/review-ledger/`.

**Rows:**

1. `[engineer-verified: "Drop item 2 (Recommended)"]` The ADDRESS `--source` normalization item is not part of this plan.
2. `[verified: review-ledger.sh:505-510; _review-ledger-lib.sh:227, as the dispatching session checked it]` ADDRESS, DEFER and SETTLED sources are already normalized, and an ADDRESS row with no `--source` is already rejected.
3. `[engineer-verified: "Two new row fields (Recommended)"]` The state lives in two new fields of the existing row, not in a sibling file.
4. `[unverified]` The names `reviewed_head` and `reviewed_tree`, their exemption from the dedup key and the `schema_version` bump from 5 to 6 come from the dispatching session's option description, not the engineer's words. M4 and M5 keep all three on their own grounds.
5. `[engineer-verified: "Record only, document the limit (Recommended)"]` The plan records the state and documents its limits. Row 6, a later answer of the engineer's, widens the plan to pin the recorded trees.
6. `[engineer-verified: "I think option 1 is the only viable option and additional size has to be estimated because we can't balloon storage space"]` Choosing among the session's options, where "option 1" was the session's label for pinning the recorded objects under a local ref (Context), the engineer chose to pin and required the added storage to be estimated. Settled question 10 gives the estimate, and M14 the design.
7. `[verified: review-ledger.sh:38-40; grep of schema_version over claude/.claude/scripts/*.py and claude/.claude/hooks/*.sh]` No code reads the ledger's `schema_version`. The one other hit, `post-crash-sessions.py:1894`, is that script's own output schema.
8. `[verified: review-ledger.sh:571-588]` One `jq -nc` call builds the row from `--arg` values, and every optional string field stores `""` when absent.
9. `[verified: review-ledger.sh:653-654]` The append's dedup key is an explicit field list, so a field left out of it is exempt.
10. `[verified: _review-ledger-lib.sh:714-735]` The retry check compares every field except a deny-list (`id`, `schema_version`, `event_time`, `source_repo_hash`), and its header calls that comparison the append's dedup key.
11. `[verified: test_review_ledger_script.py:2843-2854, :2974-3000, :3107-3117]` The field-table test requires every row field in `_DEDUP_KEY_CHANGES` or `_DEDUP_KEY_EXEMPT_CHANGES`. Two tests are parametrized over the exempt table. The per-attempt test at `:2993` runs the retry check and the append once per exempt field, which tests row 10's deny-list. `test_a_row_differing_only_in_a_per_attempt_field_dedups` (`:3113`) rewrites the stored row and appends, which tests the key at `review-ledger.sh:654`.
12. `[verified: test_review_ledger_lib.py:37-41, :604-613, :1365-1382]` The lib tests import both tables. `_row` must carry exactly the script's keys (`:1366`), and it stores `schema_version` 5 (`:606`). The retry case at `:1372` names the exempt fields by hand.
13. `[verified: review-ledger.sh:37, :607-618; test_review_ledger_script.py:148-180, :1682, :2823, :3347, :3361, :4249]` The cap is checked on the final line. Five test functions size their free text through `_fields_for_row_of_bytes`, which subtracts `_row_envelope_bytes`. The envelope must therefore carry both fields at the width the append writes.
14. `[verified: arithmetic over the keys at review-ledger.sh:580-588 and the lanes at _lib.sh:4887-4900 and _review-ledger-lib.sh:27]` Each field costs `,"reviewed_<name>":"` (18 bytes), the id and a closing quote, so 118 bytes for two SHA-1 ids and 166 for two SHA-256 ids. The fixed part of the largest row (all 13 lanes, the longest enum values, both 12-digit hashes, two 64-digit ids) stays far below 4095 bytes, so "shortening those free-text fields always clears the overflow" (`review-ledger.sh:616`) still holds.
15. `[unverified]` The hooks-test `git_repo` fixture is a SHA-1 repository on CI. Its `git init` inherits the host's global git config (`hooks/tests/conftest.py:524-531`, `helpers.py:1479-1489`), so a host that defaults to SHA-256 stores 64-digit ids. The envelope reads the width from the fixture repository, so no test depends on this row.
16. `[verified: _lib.sh:39-52, :129-140]` `_lib_capped` caps a command at 5 s with `timeout -k 2`, and runs it uncapped when neither `timeout` nor `gtimeout` is on PATH.
17. `[verified: review-ledger.sh:631-649]` The call-tally comment lists each capped jq, awk and git call an append makes.
18. `[verified: _review-ledger-lib.sh:8, :1138-1153]` `render` makes a `mktemp -d` work directory, runs its body as a separate function with `|| status=$?`, then removes fixed file names and the directory on every path, with no trap. The lib sets no EXIT trap, because `_lib_acquire_append_lock` owns the script's one.
19. `[verified: marker.sh:188]` `git rev-parse --path-format=absolute` already runs under the repo's git floor.
20. `[unverified]` `git rev-parse --git-path index` resolves the current worktree's own index, a linked worktree's included. The linked-worktree test checks it.
21. `[unverified]` These three behaviors are git's source as recalled, not run this session:
    - `git write-tree` takes `<index>.lock` beside the index it reads.
    - It exits nonzero when that lock already exists.
    - It may rewrite that index to store its cache tree.

    The held-lock and no-lock cases pin the property the design needs, and Verification steps 5 and 6 test this row directly.
22. `[verified: git-write-tree(1), Git 2.50.1]` "The index must be in a fully merged state." The unmerged-index case checks that the capture then stores `""` with no note.
23. `[verified: _review-ledger-lib.sh:941-953; worktree-ensure.sh:13, :76]` The lib's one git call already runs in a subshell that unsets `GIT_DIR`, `GIT_WORK_TREE`, `GIT_COMMON_DIR` and `GIT_INDEX_FILE`, as `worktree-ensure.sh` does. A foreign-`GIT_DIR` test pins it (`test_review_ledger_lib.py:2700-2714`).
24. `[verified: _lib.sh:1383, :1717; _review-ledger-lib.sh:115-123]` Two inline checks in `_lib.sh` accept a 40- or 64-digit hex object id. The lib's row-id predicate lists its hex digits so that a collating locale cannot widen the class.
25. `[verified: _review-ledger-lib.sh:524-559, :658-659, :747; test_review_ledger_lib.py:2018-2025; _lib.sh:4796; docs/private-project-redaction.md:73]` The `--ref` look-up builds its 15-field line from named fields. `render` excludes the session id and site hash, and a test pins that exclusion. The redaction gate's long-hex detector matches a run of 32 or more hex digits.
26. `[verified: review-ledger.sh:28-36; code-review/SKILL.md:449]` Concurrent appenders to one ledger are expected, and `/code-review` appends once per finding.
27. `[verified: code-review/SKILL.md:449, :458, :461]` `/code-review` appends "Immediately once dispositions are finalized", a clean round writes one CLEAN row, and a failed ADDRESS, DEFER or SETTLED append is a blocking stop. The append template needs no new flag.
28. `[verified: hooks/tests/conftest.py:524-531]` `git_repo` holds one commit and one staged change, so its index tree differs from HEAD's tree.
29. `[verified: helpers.py:2891-2905]` `path_with_failing_tool_shim` fails one git argument shape and proxies every other call to the real git.
30. `[verified: test_author_outcome.py:1191-1269; hooks/tests/conftest.py:109-142]` The author-outcome integration tests run the real append and read rows back by field, so they need no change. `conftest.py:109-142` resolves the ledger path and builds no row.
31. `[verified: docs/scripts.md:167, :192, :195]` The `review-ledger.sh` entry records what the usage text does not, documents row fields such as `reviewers`, `id` and `site_hash`, and names the dedup key's exempt fields at `:195`.
32. `[verified: docs/transcript-analysis.md:1646]` That line says rows carry "a `schema_version` of at least 4", which 6 keeps true.
33. `[verified: docs/cost-levers-considered.md:747-756; grep counts 15 dated follow-up lines]` The third "Reopening would need" item is "A per-pass record of the commit each cumulative pass reviewed". The file records later state in dated follow-up lines, not by editing a section's list.
34. `[verified: CHANGELOG.md:3, :62-75]` The changelog documents notable changes, and earlier ledger row-shape changes sit under `[Unreleased]` → `### Changed`.
35. `[verified: docs/design-decisions/code-file-line-limit.md:50-57; no line-limit test under claude/.claude/tests]` The 1,000-line limit is not enforced yet, so growing the two test files fails no check.
36. `[engineer-verified: "Keep both (Recommended)"]` `CHANGELOG.md` and `docs/cost-levers-considered.md` stay in the diff, though the Ask does not name them.
37. `[engineer-verified: "Yes, keep it"]` The Ask line's quote "Ok let's start 1481 in that case" is the engineer's wording from the earlier session.
38. `[unverified]` On an inline `/code-review` run, where the session running the review stages the fix itself, whether its own edit or a `code-writer`'s output, a round's rows are appended before that fix is staged. No line orders the appends before the fix today:
    - `code-review/SKILL.md:449` appends "Immediately once dispositions are finalized".
    - `:426` makes a `code-writer` dispatch wait only for the challenge result, and only when that pilot is on.
    - On the fix-plan pilot path, `:399` names the table's `code-writer` before it says to log each row as "the ledger section below" prescribes, so a literal reading dispatches first.

    The clause M13 adds at `:449` orders the appends first in prose. Its "before dispatching or applying any fix" covers a fix-plan table's `code-writer` too, because `:399` defers to that section. Nothing checks that a session follows it. The `review-orchestrator` path does not rest on this row (row 65).
39. `[verified: git-gc(1) and git-config(1) as shipped with Git 2.50.1]` `gc.pruneExpire` defaults to `2.weeks.ago`, and `git gc` prunes loose unreachable objects older than that. git keeps objects that branches, tags, the index, remote-tracking branches, reflogs or other refs reference. "Any object with modification time newer than the --prune date is kept".
40. `[verified: docs/cost-levers-considered.md:750]` "repeated staged rounds on one commit are never committed apart", so no commit holds an intermediate staged round's tree.
41. `[verified: git-config(1) core.sharedRepository entry, Git 2.50.1; review-ledger.sh:538-539]` With `core.sharedRepository` at `umask` or `false`, "Git will use permissions reported by umask(2)". The append sets `umask 077` after validation and the site hash.
42. `[unverified]` `mktemp -d` creates its directory with at most mode 0700, whatever the umask.
43. `[verified: review-ledger.sh:544-546]` The append already prints an informational stderr line on an append that exits 0.
44. `[verified: git-write-tree(1), Git 2.50.1]` `--missing-ok` disables the check that "the objects referenced by the directory exist in the object database".
45. `[unverified]` That existence check is the step of `write-tree` that could fetch a missing blob from a partial clone's promisor remote, so `--missing-ok` keeps the capture local.
46. `[verified: helpers.py:1042-1049, :1809-1817, :2834-2843, :2891-2898, :2911-2932, :3765-3777; test_review_ledger_lib.py:21-32, :431-433; test_review_ledger_script.py:17-30]` The helpers, one fact each:
    - `_make_git_exiting_with_status` exits a given status on a matching argument.
    - `path_with_failing_tool_shim` takes an `exit_status`, 1 by default.
    - `_make_git_recording_argv` logs each argv and runs a caller-supplied bash action before the real git.
    - `ambient_umask` sets the process umask for a `with` body, and `file_mode` reads a path's permission bits.
    - `add_linked_worktree` creates its branch, `feature` by default, which the lib's `repo` fixture already checks out.
    - `build_conflicted_merge` leaves a real conflicted merge staged.

    The lib test file imports none of `_make_git_recording_argv`, `add_linked_worktree` or `build_conflicted_merge`, and the script test file imports none of `_make_git_recording_argv`, `ambient_umask` or `file_mode`.
47. `[verified: git-rev-parse(1) --git-path entry, Git 2.50.1]` `--git-path` "takes other path relocation variables such as $GIT_OBJECT_DIRECTORY, $GIT_INDEX_FILE... into account".
48. `[verified: git-update-index(1) split-index section, Git 2.50.1]` A split index keeps its entries in `$GIT_DIR/sharedindex.<SHA-1>`. git updates that file's modification time whenever a split index based on it is read.
49. `[unverified]` `git write-tree` run with `-c core.splitIndex=false` on a copy of a split index creates no file in the git directory or beside the copy. The split-index case checks both, with enough unshared entries that a rewrite of the copy without the flag would cross the shared-index threshold (row 66). If `write-tree` never rewrites the copy (row 21), the case still passes, and the property holds. Verification step 6 records whether the flag changes the outcome on this path.
50. `[verified: _lib.sh:5380-5405, :5464; review-ledger.sh:593-599, :653]` The append lock is a noclobber file beside one ledger file, and after its retries it proceeds unlocked. Its only caller builds the row, and hashes it into the row id, before taking the lock.
51. `[verified: _lib.sh:142-154]` `_lib_status_consistent_with_cap_kill` classifies 124, 137 and 143 as cap-kill statuses.
52. `[verified: test_review_ledger_script.py:385-405, :783-800]` The `show --out` case reads the shown rows back by field. The CLEAN case at `:783-800` appends a real CLEAN and reads its row at `:792`.
53. `[unverified]` A `GIT_INDEX_FILE` naming an absent file reads as an empty index, so `write-tree` would print the empty tree's valid-looking id. Verification step 5 checks it against the id `git hash-object -t tree /dev/null` prints, which is recalled as the empty tree's.
54. `[engineer-verified: "Proceed anyway"]` The plan's `Projected diff:` of about 560 lines stays in one PR.
55. `[engineer-verified: "Keep it"]` Answering whether to keep the refactor that moves `_review_ledger_confirm_ignored`'s git subshell into `_review_ledger_repo_git`, the engineer selected "Keep it".
56. `[engineer-verified: "Keep it"]` Answering whether to keep the one stderr line `append` prints when the environment defeats the capture, the engineer selected "Keep it".
57. `[unverified]` `git write-tree` exits with the same status for an unmerged index and for a tree object it cannot write, because both reach one "error building trees" path. Recalled from git's source, not run. The probe makes the design independent of it.
58. `[unverified]` `git ls-files -u` prints one line per unmerged entry of the index `GIT_INDEX_FILE` names, and nothing for a fully merged index. Recalled from git-ls-files(1), not run. The unmerged-index case and the failing-probe case check it.
59. `[unverified]` git looks for a split index's shared file in the current worktree's git directory, then beside the index file, and a linked worktree writes its shared index into its own git directory. Recalled from git's read-cache source, not run, and no case covers a linked worktree with a split index. If git cannot find the shared file, both `write-tree` and the probe fail reading the copy, so the capture prints the note rather than storing a silent empty.
60. `[unverified]` `git hash-object --stdin` without `-w` prints a blob id without storing the blob, and `git update-index --add --cacheinfo` stages an entry without checking that its blob exists. Recalled, not run. The absent-blob case asserts the first with `git cat-file -e` and fails at setup if the second is false. It also asserts that plain `write-tree` rejects the index, so it pins `--missing-ok` on every run, and Verification step 5 cross-checks it once.
61. `[verified: code-review/SKILL.md:25; review-ledger.sh:555]` At a session's first round, and after a compaction or a resume, `/code-review` continues from one past the ledger's highest round, so consecutive rounds can sit in different sessions. `event_time` has one-second resolution (`date -u +%Y-%m-%dT%H:%M:%SZ`).
62. `[verified: test_review_ledger_script.py:164, :2958-2972]` `_fields_for_row_of_bytes` takes `session_id` as its second positional parameter. `_retry_after_changing_one_field` stores the retirer's copy as `{**retirer, field: value}` beside a second retirer that keeps the decision retired, then repeats the first retirer's append.
63. `[engineer-verified: "Proceed anyway"]` Answering whether to split the plan or proceed at a `Projected diff:` of about 620 changed lines, the engineer selected "Proceed anyway". The plan stays in one PR at that figure.
64. `[engineer-verified: "Fold into GH-1481"]` Answering whether the ordering edit to `code-review/SKILL.md` ships in this PR or a follow-up, the engineer selected "Fold into GH-1481". The edit ships in this PR.
65. `[verified: code-writer.md:23-24; review-orchestrator.md:12, :16-19, :22-25; subagent-delegation/SKILL.md:194-197]` `code-writer` never stages and leaves its changes unstaged. `review-orchestrator` never stages, runs the skill's ledger appends inside its own dispatch, and sends each fix to `code-writer`. Its dispatcher stages the returned `Fix paths:` only after the dispatch returns. A round run through `review-orchestrator` therefore appends before its fix reaches the index, and a `code-writer` dispatch alone never moves either recorded state.
66. `[verified: git-config(1) splitIndex.maxPercentChange entry, Git 2.50.1]` "If the value is 0, then a new shared index is always written; if it is 100, a new shared index is never written. By default, the value is 20, so a new shared index is written if the number of entries in the split index would be greater than 20 percent of the total number of entries."
67. `[unverified]` In a `git diff -U0` hunk header, `+c,n` covers new-side lines c through c+n-1, `+c` with no count covers line c alone, and `+c,0` covers no line. Recalled from the unified diff format, not run on two tree ids. Verification step 7 checks all three forms on two recorded trees. The docs state it so two readers count a deletion-only hunk alike.
68. `[verified: review-ledger.sh:64-72, :89-95, :580-588; code-review/SKILL.md:390, :394, :453-454]` A row stores its finding's location in `source`. A carry also stores the reviewer's cited line in `cited_line`, and its `source` reproduces the decided text's range. An ADDRESS or DEFER `source` ranges the whole block, and so does a SETTLED keep's. An ADDRESS whose fix creates a file names the path alone.
69. `[verified: docs/scripts.md:170, :181]` In branch scope `show` also reads the worktree's session file, whose rows count toward `max_round` and carry no scope field. Its stderr header lists the branch file first after `files=`, and lists only non-empty files.
70. `[engineer-verified: "No, follow-up (Recommended)"]` Answering whether this PR adds a `review-ledger.sh place` script that applies the placement rule, the engineer selected "No, follow-up (Recommended)".
71. `[verified: claude-skills/skills/tests/test_skills.py:8373, :8544-8555; grep of "before review-findings persistence" and "Immediately once dispositions" over every *.py file]` `_REVIEW_LEDGER_PROSE_CONTROLS` holds `(skill, heading, token)` params, and `test_section_names_the_control` asserts that each token appears in its heading's section. No Python test pins any part of `code-review/SKILL.md:449`'s opening clause.
72. `[verified: helpers.py:2846-2880; test_require_code_review.py:1387-1391; test_review_ledger_lib.py:10-32]` `_make_git_blocking_on_arg` sleeps 20 s, past the 5 s cap, when an argument matches, and proxies other calls to `$REAL_GIT`. The existing cap cases mark a blocking case `@pytest.mark.timing` and skip it when `_timeout_binary_present()` is false. The lib test file imports neither helper, nor `time`.
73. `[unverified]` An append runs on an unborn branch. The repo hash and the branch key hash text, not a commit (`_lib.sh:647-651`, `:5233-5240`). That `git symbolic-ref -q --short HEAD` names an unborn branch is recalled, not run. The unborn-branch script case settles the row.
74. `[verified: review-ledger.sh:111-117, :580-588; code-review/SKILL.md:399, :449, :462, :466]` No row field marks a `--relog` row. It stores its `--ref` decision's lanes as `reviewers`, and a fresh ADDRESS or DEFER of a repeat also stores a `ref`. A reopen, a gate-denial recovery and a `--carry-forward` withdrawal are logged `--relog`.
75. `[verified: docs/cost-levers-considered.md:723, :735, :751; code-review/SKILL.md:420]` Two consecutive cumulative passes bracket a fix committed between them, and commits between two passes can carry work beyond the fix. A cumulative pass reviews the PR against its base. A cumulative pair's two `reviewed_head` values therefore differ.
76. `[verified: review-ledger.sh:725-741, :761-763; _lib.sh:5640, :5678]` The session file is `<repo hash>.<session id>.jsonl`, and the branch file is `<repo hash>.<branch hash>.jsonl`. In branch scope `show` reads both, and in session scope only the session file. It decodes one line at a time and skips a torn line or one that is not an object.
77. `[verified: review-orchestrator.md:48-52]` A `review-orchestrator` return is a fixed item list "and nothing else", and from the script's output it relays only the `carry of decision` lines.
78. `[verified: code-review/SKILL.md:333; docs/scripts.md:185]` A CONSULT row records a round-cap consult's verdict, not a decision. It is appended once the round acts on the verdict, before the round retries the denied spawn.
79. `[engineer-verified: "Proceed anyway"]` Answering whether to split the plan or proceed at a `Projected diff:` of about 790 changed lines, with the architect's recommendation to proceed as one PR relayed in the question, the engineer selected "Proceed anyway". The plan stays in one PR at that figure. The figure is an estimate and may move in later revisions.
80. `[unverified]` With `core.splitIndex=true` set and an index not yet split, a command that rewrites the index splits it and writes a new `sharedindex.<id>` into the git directory. Under `-c core.splitIndex=false`, the index a command writes is not split, whether or not the index it read was. Recalled from git's read-cache and split-index source by a reviewer, not run. The split-index case builds only an already-split copy, and the flag covers the unsplit shape too if this row holds.
81. `[unverified]` Each redirect variable changes the result of only some of the calls `_review_ledger_repo_git` runs. Recalled, not run, except where row 47 is cited:
    - `git rev-parse HEAD^{commit}` reads neither the index nor the work tree, so `GIT_INDEX_FILE` and `GIT_WORK_TREE` cannot change its output.
    - It resolves HEAD's branch through the common directory, so `GIT_COMMON_DIR` can.
    - `git rev-parse --git-path index` follows `GIT_INDEX_FILE` (row 47).
    - `git check-ignore` reads the work tree's `.gitignore` files, so `GIT_WORK_TREE` can change its result.

    Each case that pins one of the four variables asserts its own redirect as a premise.
82. `[verified: announce-branch-review-history.sh:3-7, :50, :59, :72; code-review/SKILL.md:388]` Two callers write `show --out` into `agent-reviews/`. The hook runs `show --out agent-reviews/review-history-<slug>.md --trend` on each `plan-architect` start, and tells the agent to "Read it only for a foundation or convergence question". The contradiction route writes the same kind of file, Greps it for a finding's path, and reads its rows' `finding` and `rationale`. No code parses either file.
83. `[verified: docs/cost-levers-considered.md:737-738]` "`comment-discipline-reviewer` sits outside the comparison, because its findings inside a fix are a first read of that text." "`skill-fidelity-reviewer`'s findings are excluded from the comparison, because the scan could not place them." Each ledger row stores its lanes as `reviewers` (row 74).
84. `[verified: select-tests.py:1208-1214, :1336; README.md:583]` `select-tests.py` hands its own arguments to pytest after the selected targets. CI runs "a parallel pass for everything not marked `timing`, and a serial pass for the `timing` tests, which flake under sibling-worker load", and README.md gives `-m timing -n0` to reproduce the serial pass.
85. `[verified: test_review_ledger_script.py:4240-4301]` `_append_near_limit_rows_from_distinct_sessions` runs its appends at once in `git_repo`, six in each of its two callers, returns each appender's stderr, and asserts that every line is 4,095 bytes. Its callers are the locked and the unlocked-fallback parallel cases.
86. `[verified: docs/hooks.md:463; _lib.sh:4986-4990; code-writer.md:23-30; review-ledger.sh:218-221; hooks/tests/conftest.py:55-85, :476-485]` Four facts bound who runs which Verification step:
    - The append gate denies a Bash `review-ledger.sh append` from any agent in `_LIB_NO_GATE_RELEASE_AGENTS`, which lists `code-writer`, because such a row would assert a decision the parent session never made.
    - `code-writer` may run narrowly-scoped, read-only checks on what it changed, must not run the full suite, and never stages.
    - An append with no resolvable session aborts with exit 2.
    - The script tests get a session from `_seed_session`, which writes the pytest process's own pid under the `HOME` that `isolated_home` sandboxes.
87. `[engineer-verified: "Sure option 1"]` Answering whether to take the pin design to the architect at about 1,040 lines, the engineer selected "Sure option 1".
88. `[unverified]` The pin's details come from the session's and a consult's proposal and from this plan, not from the engineer's words: pinning `reviewed_tree` only, and a ref named `refs/review-ledger/<YYYY-MM>/<tree id>` that points at the tree with no wrapper commit. Row 87 covers only taking the design to this revision, and row 101 covers the absence of an expiry or a cap. M14 keeps each detail on its own grounds.
89. `[unverified]` `git update-ref` accepts a tree as a ref's value outside `refs/heads/`, because git refuses a non-commit value only for a branch, and it checks only that the named object exists. `git rev-parse --verify` on such a ref prints the tree's id. Recalled from git's refs source, not run. The happy-path script case and the pin class's existing-ref parameter check both.
90. `[verified: claude/.claude/scripts/tests/test_transcript_gh_cli.py:2254-2255]` `git update-ref` fires the `reference-transaction` hook. That test's control asserts it.
91. `[unverified]` Five lock behaviors of `git update-ref`, recalled from git's refs source and git-config(1), not run:
    - In the files backend it locks `<ref>.lock` beside the ref, and in a reftable repository one lock guards the whole table list.
    - It retries a held loose-ref lock for `core.filesRefLockTimeout`, 100 ms by default, then fails.
    - git removes its lock files when SIGTERM kills it.
    - A stranded `<ref>.lock` blocks only writes of that one ref.
    - Its error on a held lock reads `cannot lock ref '<ref>': Unable to create '<path>.lock': File exists`, the same shape as an index lock's.

    `_lib.sh:122-125` says a SIGKILLed capped child can leave lock files behind. The probe makes the design independent of the retry, and the near-limit parallel cases run six concurrent pins of one tree (row 85).
92. `[verified: review-pr-checkout.sh:391-392; evals/review_bench/defects.py:465-483; claude-skills/skills/review-pr/REFERENCES.md:772-777]` Two local pins already exist under their own namespaces, and both name commits. `review-pr-checkout.sh` fetches a PR head into `refs/review-pr/pr-<N>` under a cap, and the ref stays until `git update-ref -d`. The review bench points `refs/review-bench/defect/<digest>` at a fix commit with `update-ref`, and re-running it with the same value leaves the ref unchanged.
93. `[verified: cleanup-merged-branches.sh:792; cleanup-idle-open-pr-worktrees.sh:290; worktree-ensure.sh:179; plugins/plugin-semver/hooks/require-plugin-version-bump.sh:113; plugins/npm-semver/hooks/require-npm-version-bump.sh:155; grep of for-each-ref, show-ref, rev-list --all, git gc, git prune and pack-refs over every non-test *.sh and *.py file]` Every shipped `for-each-ref` call is scoped to `refs/heads`, and every shipped `show-ref` call names one `refs/heads/` ref. No shipped code runs `rev-list --all`, `git gc`, `git prune` or `git pack-refs`. The `_lib.sh` hits are subcommand name lists. `_lib.sh:2289-2290` records `--all`, `--branches` and `--mirror` as push reasons.
94. `[unverified]` Settled question 10's storage inputs are recalled git behavior, not measured:
    - A packed tree that differs from another version by one entry is stored as a delta of about 50 bytes.
    - A packed blob version is a delta about the size of its edit, compressed.
    - `git gc` repacks loose reachable objects with a delta search, so versions of one path delta against each other.
    - A loose ref is one small file, and a `packed-refs` line is `<id> <refname>` and a newline.

    The per-PR inputs in Settled question 10 are assumptions. Verification step 7 checks the measuring command on known objects, not these figures.
95. `[unverified]` git creates a loose ref file and its directories under the process umask unless `core.sharedRepository` says otherwise, as it does objects (row 41). A reftable repository keeps its refs under `<git dir>/reftable/` instead of in loose files. Recalled, not run. The umask case checks the ref file and its month directory, and skips them in a reftable repository.
96. `[unverified]` Four behaviors the docs' measuring command rests on, recalled, not run:
    - `git rev-list --objects` lists a tree that a ref names, and everything under it.
    - `--glob` and `--exclude` patterns match across `/`, so `refs/review-ledger/*` covers the month level.
    - `comm -13` prints the lines found only in its second sorted input.
    - `%(objectsize:disk)` reports an object's bytes on disk, which for a packed delta is the delta's own size.

    Verification step 7 runs the command on known objects. The consult's form, `rev-list --objects --glob=... --not --branches --remotes --tags`, was set aside. `--not` marks a commit's tree uninteresting only at the edge of an interesting commit walk (recalled), and a walk from tree tips has no interesting commit, so it can count objects the default branch's history holds as pin-only.
97. `[unverified]` In a partial clone, `git cat-file --batch-check` fetches a missing object it is asked about from the promisor remote. Recalled, not run. The docs say their commands assume a full clone.
98. `[unverified]` Three facts about a pinned tree that names a missing object, recalled, not run:
    - In a full clone the index names only blobs `git add` or `git update-index` wrote, so a pinned tree's objects all exist unless the repository is already damaged.
    - `git gc` already packs every object the index names, so an index that names a missing blob already fails `git gc`. A pin keeps that failure after the index is repaired, until the pin is deleted.
    - In a partial clone, a blob that `--missing-ok` let the capture skip is a promisor object, which `git gc` and `git fsck` expect to be missing.

    No case runs `git gc` over such a tree.
99. `[verified: review-ledger.sh:651-652]` The append primitive "returns nonzero only when the row was neither written nor deduplicated", so its status cannot tell a written row from a deduped one.
100. `[verified: test_review_ledger_script.py:269 and the append cases read after it; test_require_code_review.py:897; test_enforce_marker_script_shape.py:2880; test_branch_records_archive.py:144; test_announce_branch_review_history.py:110-121, :130-138, :174-332; test_author_outcome.py:1191, :1223-1329]` Every append test found runs with a temporary repository as its working directory:
    - `test_announce_branch_review_history.py`'s `_append_branch_row` runs in a linked worktree of the checkout `_build_checkout` creates under `tmp_path`.
    - Each `TestReviewLedgerSubprocessIntegration` case in `test_author_outcome.py` appends with `cwd=repo`, a repository under `tmp_path`.

    Verification step 8 checks the real checkout for pins after the selected tests run.
101. `[engineer-verified: "Defer with a trigger (Recommended)"]` Answering whether this PR adds an expiry or a size cap for the pins, the engineer selected "Defer with a trigger (Recommended)". The tag covers only that this PR adds neither.
102. `[unverified]` The dispatching session described that option as no automatic expiry now, an honest ceiling from real file sizes, a documented measuring command and delete recipe, and a stated figure at which the engineer decides. That description is the session's, not the engineer's words. The trigger in Out of scope is this plan's proposal for the engineer to adjust: its 10 percent figure, its 30- and 90-day readings, the 90-day cadence after them, and who takes them.
103. `[unverified]` Seven `git gc` behaviors, recalled from git-gc(1) and git-repack(1), not run:
    - `git gc --prune=now` risks corrupting a repository that another git process writes to at the same time.
    - `git gc` runs `git pack-refs`, which moves loose refs into `packed-refs`.
    - `git gc --auto` packs only loose objects into a new pack while fewer than `gc.autoPackLimit` packs (50 by default) exist, and a new pack deltas only against objects inside itself.
    - A manual `git gc` repacks every object into one pack.
    - An object only a deleted pin kept leaves at a later `git gc` once it is older than `gc.pruneExpire`, or at once under `git gc --prune=now`, when nothing else holds it.
    - An object already in a pack leaves only at a `git gc` that repacks every pack.
    - `git gc --prune=now` drops an unreachable object whatever its modification time. With `gc.cruftPacks` off it writes no cruft pack, whose expiry would compare those times.

    Verification step 7 checks the `pack-refs` behavior, and runs the measuring command after a manual `git gc`. The pin class's real-objects case checks the deleted-pin behavior under `--prune=now` with `gc.cruftPacks` off, and step 7 runs that case five times back to back.
104. `[verified: wc -c, ls | wc -l and git count-objects -v, re-run in this checkout by the planning session before round 9]` This repository's figures, one per item:
    - `review-ledger.sh` is 45,254 bytes, `docs/scripts.md` 113,664, `test_review_ledger_lib.py` 145,361, `test_review_ledger_script.py` 226,320, and `claude/.claude/hooks/_lib.sh` 308,401.
    - `claude/.claude/hooks/tests` holds 157 entries, and `claude/.claude/scripts/tests` 184.
    - `git count-objects -v` read 8 packs, a `size-pack` of 44,232 KiB, and 5,386 loose objects at 88,656 KiB.
105. `[verified: _lib.sh:5460-5463, :5534; review-ledger.sh:305-326, :678-679, :847-858; docs/scripts.md:181]` `review-ledger.sh` calls `_sweep_stale_ledger_files` on each append, which evicts whole stale ledger files, with a 30-day floor. `clear-stale` calls the same function. The docs' Reset bullet tells the reader to delete a branch ledger file by hand. This plan assesses neither, and edits neither the sweep nor the Reset bullet. It names each as a source of pins no row records, and gives the sweep as the reason older months' pins mostly back rows no live ledger file holds (Out of scope).
106. `[unverified]` Five ref consumers outside shipped code, recalled, not run:
    - A fetch whose refspec maps into `refs/*`, as `git clone --mirror` sets up, deletes local refs the remote lacks when it prunes.
    - A remote whose `remote.<name>.mirror` is true makes a plain `git push` behave as `git push --mirror`, and `git clone --mirror` sets it on `origin`.
    - Unfiltered `git for-each-ref`, `git show-ref` and `git log --all` list every ref.
    - `git clone --mirror`, `git bundle create --all`, `git fast-export --all` and history-rewrite tools copy or walk every ref.
    - With `core.logAllRefUpdates=always`, git keeps a reflog for every ref it updates, and `git pack-refs` does not pack reflogs.

    The docs name each and say they are unverified.
107. `[verified: README.md:112; CHANGELOG.md:569]` The repo's git floor is 2.43.
108. `[unverified]` `git init --ref-format` first shipped in git 2.45. Recalled, not run.
109. `[unverified]` The docs' audit command and month deletion rest on three behaviors, recalled, not run:
    - Under `-R`, jq's `fromjson?` yields nothing for a line that does not decode.
    - jq's `.[0:7]` on a string takes its first seven characters.
    - `git for-each-ref` given a pattern that ends in `/` lists every ref under it, and prints nothing when none exists.

    Verification step 7 runs both commands.
110. `[verified: helpers.py:2839, :2857, :2918; grep of REAL_GIT over helpers.py, whose hits all sit outside :2891-2905, and over test_review_ledger_script.py]` `_make_git_exiting_with_status`, `_make_git_blocking_on_arg` and `_make_git_recording_argv` need `REAL_GIT` set to a real git, and the script test file sets it nowhere today. `path_with_failing_tool_shim` does not read it.
111. `[verified: deny-private-project-refs.sh:26-29; docs/private-project-redaction.md:73]` The redaction gate runs its content scans only in claude-config or in a repository with `.claude/private-projects-optin` at its toplevel. Its long-hex detector does not catch a shorter hex run, such as a short git SHA.
112. `[unverified]` A `delete` line under `git update-ref --stdin` deletes the ref's reflog with the ref, so the docs' month deletion leaves no reflog holding a deleted pin's tree under `core.logAllRefUpdates=always`. Recalled, not run. The pin class's real-objects case checks it. The lookup and release behaviors this row once listed went with the procedure row 116 rules out.
113. `[engineer-verified: "Deferral stands (Recommended)"]` Answering whether the deferral of expiry still stands at Settled question 10's ceiling of about 13 to 84 times the current packed size, given "we can't balloon storage space", the engineer selected "Deferral stands (Recommended)". The ceiling and the trigger are as the plan states them.
114. `[verified: git-update-ref(1), Git 2.50.1]` On `--stdin`: "If all <ref>s can be locked with matching <old-oid>s simultaneously, all modifications are performed. Otherwise, no modifications are performed." A delete line has the form `delete SP <ref> [SP <old-oid>] LF`. The docs' month deletion therefore deletes none of a month's pins when git prints an error.
115. `[verified: scripts/tests/conftest.py:85-102; helpers.py:1042-1049, :1479-1489, :1652-1661; test_review_ledger_lib.py:57-70, :426-427; grep of "from .conftest import" over scripts/tests]` The test helpers' environments, one fact each:
    - `_clean_env` returns the inherited test environment without `GIT_DIR`, `GIT_WORK_TREE`, `GIT_COMMON_DIR` and `GIT_INDEX_FILE`, with keyword overrides applied. Sibling test files import from `.conftest`.
    - `isolated_git_config_env` returns `GIT_CONFIG_GLOBAL` set to `os.devnull`, `GIT_CONFIG_NOSYSTEM`, `HOME` and `XDG_CONFIG_HOME`, and removes no variable.
    - `add_linked_worktree` passes no environment to git, and `init_git_repo` takes an `env` for every git call it makes.
    - The lib test file's `_bash` overlays its `env` on the inherited environment, and its `_git` passes no environment.
116. `[engineer-verified: "Not a valid concern. Gold-plating. Here's why. If a credential is staged, it should be removed immediately. We never wait for a leaked credential to be pruned."]` Answering the security reviewer's concern that a pin keeps a mistakenly staged credential past git's prune window, the engineer ruled the concern out. The plan therefore carries no lookup, release, confirmation or rotation step for content a pin holds.

**M1. The capture and pin functions live in `_review-ledger-lib.sh`, and the append calls each once.** `anchors: root, row3, row8, row16, row24`. `.claude/rules/bash-unit-test-seams.md` puts a helper in the sibling lib whose domain covers it, and the lib already holds the site hash. Two capture functions are used instead of one returning a pair, because a tab-separated pair loses a leading empty field in `read`, which is why `_REVIEW_LEDGER_FIELD_SEP` exists (`_review-ledger-lib.sh:34-36`). The id check keeps a truncated or garbled capture away from a later `git diff`. The object-id predicate stays in the sibling lib too:
- Its only caller is that lib, and the rule reserves `_lib.sh` for a helper that two or more hooks need.
- Adding it to `_lib.sh` would still leave the two inline checks there (row 24) as separate definitions, until a migration this plan leaves out of scope.

**M2. A failed capture stores `""`, the append goes on, and an empty field prints one note unless git reported an expected state.** `anchors: row3, row5, row8, row22, row27, row43, row51, row57, row58, row77`. Two alternatives were set aside:
- Abort the append. A failed ADDRESS, DEFER or SETTLED append blocks `/code-review` (row 27), so an optional measurement field would block a review.
- Store `null` or omit the key. Every other optional string field stores `""` (row 8), and one shape keeps readers simple.

Silence for every failure was set aside too. An environment that always defeats the capture, such as an unwritable `$TMPDIR` or object directory, would ship empty fields until the measurement runs, and those rounds cannot be backfilled (G1). The note fires on these failures and no others:
- The HEAD look-up: a cap-kill status, or output the predicate rejects.
- The index path look-up: any nonzero status, or an empty path.
- `mktemp` or `cp`: any failure.
- `write-tree`: a cap-kill status, output the predicate rejects, or any other failure unless the `ls-files -u` probe on the same copy exits 0 with output.

Three cases stay silent:
- A HEAD look-up git refuses without a cap-kill status. That covers an unborn branch, and also a repository git cannot read, which the docs say. A probe was set aside here, because the look-up only reads, so an unwritable object directory or a full disk cannot cause it.
- A missing index file.
- A `write-tree` failure on a copy with unmerged entries (row 22).

`write-tree`'s exit status cannot tell an unmerged index from an object it could not write (row 57), so the probe does. It runs only after a failed `write-tree`, so a capture that succeeds makes no extra call. Two lighter options were set aside:
- A note on every `write-tree` failure. A round reviewed mid-merge would then print it for an expected state.
- Matching git's stderr text, which is not a stable interface.

git's own stderr is discarded, as `_review_ledger_confirm_ignored` discards it. The append already prints an informational line on success (row 43), and the exit status stays 0. The note promises nothing about the row, because the append can still exit 2 or dedup to no new row after it. Recording the cause in the row was set aside, because the engineer chose two fields (row 3).

The note reaches only the appending session's tool output, and a `review-orchestrator` return does not relay it (row 77). It is therefore that session's diagnostic, not an alarm a human is sure to see, and the docs say where it appears. Relaying it in the round report, as carry lines are, was set aside, because that needs a second skill clause and an agent-file edit for an optional field.

**M3. The tree is written from a copy of the index.** `anchors: row20, row21, row26, row49, row50, row53, row80, row85`. Four lighter primitives fail:
- Plain `git write-tree` on the live index takes the live index lock (row 21). Three consequences follow:
  - A round's parallel appends would race each other for it (row 26).
  - A concurrent `git add` or `git commit` would fail on the held lock.
  - A cap kill could strand the lock until someone removes it by hand (`_lib.sh:122-125`).
- Serializing a live-index `write-tree` inside the ledger append lock fixes only the first consequence (row 50):
  - That lock orders appenders to one ledger file, and it proceeds unlocked once its retries run out.
  - It does not order the session's own `git add` or `git commit`, or a detached-HEAD session whose rows go to a per-session file in the same worktree.
  - It does not stop a cap kill from stranding the live lock.
  - It would also move the row build inside the lock, because the row id hashes the row, state included.
- A hash of `git diff --cached` is not a tree object, so `git diff` cannot compare two rounds with it.
- HEAD alone misses a staged round, which reviews the index before any commit (Context).

The copy needs four guards of its own:
- The capture skips a missing index file.
- It runs `write-tree` only after `cp` exits 0 (row 53).
- Every exit after `mktemp` reaches one cleanup path.
- It passes `-c core.splitIndex=false` to `write-tree`, so a rewrite of the copy never writes into the git directory a shared index that the live index does not use (rows 49 and 80). That flag was chosen over a second split-index case with the config on and the index unsplit, because the flag covers both index shapes, and the case would cover one.

Each guard closes a gap in the copy step itself, not a gap an earlier guard opened. The umask, stderr, unmerged-entry probe, environment, retention and round-selection changes would apply unchanged to a live-index `write-tree`, so they are not layers on the copy. If row 21 turns out false, the copy costs one more capped git call, one capped `cp` and a temporary directory.

**M4. Both fields are exempt from the dedup key and the retry check.** `anchors: row4, row9, row10, row11, row12`. The key exists so a retry is a no-op (`review-ledger.sh:118-121`), and the state is captured per attempt, like `event_time`. Including it would let a retry after the session staged or committed something land a duplicate row. Including it adds no row separation either, because rounds already separate by `round`, which is in the key. The kept row holds the first attempt's state, and the docs say so. A retry still pins its own attempt's tree, which M14's orphan-pin item counts.

**M5. `schema_version` goes from 5 to 6.** `anchors: row4, row7`. The constant exists to tell row shapes apart without probing for keys (`review-ledger.sh:38-39`). Keeping 5 would give rows with and without state the same number.

**M6. Every disposition records state, and `docs/scripts.md` holds the rule for picking, pairing and placing a round's state.** `anchors: root, row27, row38, row61, row64, row65, row67, row68, row69, row74, row75, row76, row78, row83`. A clean round writes only a CLEAN row, so that row supplies the state that ends the previous round's fix delta. CONSULT, carry and `--relog` rows share the one row build, so leaving them out would add a branch to skip them. Critical files item 5's blockquote is the rule's one home. The reasons it leaves out, one per item:
- The docs name every outcome of a late append and no single direction. M13's clause orders the append first, and the `review-orchestrator` path already does (row 65). The inline path still rests on prose a session can skip (row 38), and no row records which path wrote it.
- Rounds form, and pair, from every non-CONSULT row before the state is picked. Filtering rows by state first would drop a round whose capture failed, and the pairing would fall through to another session's round and read as placed.
- The blockquote leaves lane filtering to the reader, because which lane a measurement drops is that measurement's call. `docs/cost-levers-considered.md` names the lanes its own measurement drops, and Critical files item 7's line says which still apply (row 83).

**M7. `render` and `show` stay unchanged.** `anchors: row14, row25, row82, row111`. `render` reads named fields, so the ids never reach a PR body, where a 40- or 64-digit id would trip the long-hex detector. `show` passes every field through, so its output carries the ids. A render test pins the exclusion, and a new `show --out` case tests the inclusion.

Two callers write `show --out` into `agent-reviews/` (row 82). One is `announce-branch-review-history.sh`, whose history file every `plan-architect` dispatch is offered. The other is `/code-review`'s contradiction route, which Greps its file for a path. Both receive the ids and need no edit, because no code parses either file. The plan accepts the larger rows rather than dropping the two fields from `--out`:
- The history file is read only for a foundation or convergence question, so the extra bytes cost only on that conditional read, at most 166 per row (row 14).
- A field filter would add an option or a second output shape to `show` for that one reader.

Where the redaction gate runs, its long-hex detector blocks a row copied from either file into a commit or a PR body, so there the cost is friction. It runs only in claude-config and opted-in repositories, and it never catches an abbreviated id (row 111). Elsewhere, keeping the ids out of published text is the writer's job, and the docs say so.

**M8. Comments and docs.** `anchors: row17, row31, row32, row33, row34, row38, row39, row91`. Critical files gives the text. `docs/hooks.md` changes only in its lock-recovery paragraph, which gains the ref lock a cap-killed pin can strand (M14). `docs/transcript-analysis.md` needs no edit.

**M9. One `code-writer` dispatch and one commit.** `anchors: row11, row12, row13, row64, row86`. `test_review_ledger_lib.py` imports the dedup tables from `test_review_ledger_script.py`, the script's call site depends on the lib's contract, and the docs state the code's empty-value and dedup behavior. The docs also state the order the skill clause sets. Two lighter shapes were set aside:
- Code, then docs, as two sequenced dispatches. The docs dispatch would need the same behavior restated and could word it differently from what the code does.
- Script and lib as parallel dispatches. The call site depends on the lib functions, and both test files share the tables.

**M10. The capture and the pin run before `umask 077`.** `anchors: row41, row42, row95`. The append calls both above the umask, so git writes the objects, the pin's ref file and its month directory under the invoking shell's umask. git creates objects under the process umask unless `core.sharedRepository` says otherwise (row 41), and the ref file and month directory follow the same rule (row 95). A capture or pin after `umask 077` would therefore write owner-only files into a repository that other accounts may share through group permissions. The work directory needs no umask for privacy. The copy is no more exposed than the index it copies, and `mktemp -d` makes its directory owner-only besides (row 42). Set aside:
- Running `write-tree` in a subshell that restores the invoking umask. It has to save and restore the original value, and moving the call needs neither.

**M11. Every capture call unsets git's redirect variables.** `anchors: row23, row47, row81`. `_review_ledger_repo_git` runs each capture and pin call, and `_review_ledger_confirm_ignored`'s call, in a subshell that unsets the four variables the lib already unsets (row 23). It exports the copy's `GIT_INDEX_FILE` inside that subshell, so the variable never reaches the calling shell. An ambient `GIT_DIR` or `GIT_INDEX_FILE`, from a git hook or an agent shell, would otherwise record another repository's or another index's state as a valid id (row 47). Each of the four variables is pinned by a case on a call whose result it changes (row 81). Set aside:
- Honoring the environment. The ledger is keyed by `REPO_ROOT`, and the capture should describe that worktree.
- A `VAR=value _lib_capped ...` prefix, or `_lib_capped env GIT_INDEX_FILE=... git ...`. The subshell already scopes the variable.

**M12. A hung capture costs at most one cap.** `anchors: row16, row44, row45, row51, row84, row91`. A hang costs at most one capped call's 5 s plus its 2 s kill grace, after the steps that finished before it, not one cap per capped call (row 16). Five gates hold that bound:
- Within the tree capture, each step runs only after the one before it succeeds.
- The `ls-files -u` probe runs only after a `write-tree` failure that is not a cap kill.
- A HEAD look-up that ends in a cap-kill status skips the tree capture.
- The pin runs only after a tree capture that printed an id.
- The pin's `rev-parse` probe runs only after an `update-ref` failure that is not a cap kill.

That bound covers a hang, not a slow capture. Each step has its own cap, so a capture whose steps all finish can take the sum of their times, and `cp` and `write-tree` grow with the index. A time budget across steps was set aside, because it adds an elapsed-time check per step for an optional field. Three related facts:
- Verification step 4 runs Critical files item 4's two blocking cases serially, as CI runs `timing` cases (row 84).
- `write-tree --missing-ok` skips the object-existence check (row 44), which keeps the capture from reaching a partial clone's promisor remote (row 45).
- The uncapped index test, `mktemp`, `rm` and `rmdir` join the call tally's uncapped list.

**M13. One clause in `code-review/SKILL.md` orders a round's appends before its fix.** `anchors: row27, row38, row64, row65, row71`. A recorded state is the reviewed state only when the append runs before the fix reaches the index. The `review-orchestrator` path already appends first, because neither it nor `code-writer` stages (row 65). The clause covers the inline path, where the session stages its own fix, and the fix-plan table's `code-writer` (row 38). It sits in the ledger step's existing ordering sentence, beside "before review-findings persistence below", which already orders that step against a later one. One token pin in `test_skills.py` fails when a later edit drops the clause (row 71). It pins the text, not whether a session follows it. Three alternatives were set aside:
- A hook that denies `git add` until a round's appends land. Nothing records which round is open or which appends it owes, so the hook would need new round-state tracking to protect an optional measurement field.
- Moving the "Review-narrative ledger" section ahead of "Finding disposition". Its trigger, finalized dispositions, follows the challenge at `:426`, so the moved section would still need a clause saying when it runs.
- A check in `review-ledger.sh` that refuses a late append. The script sees only the current HEAD and index, never what the round reviewed, so it cannot tell a late append from an on-time one.

**M14. `append` pins a non-empty `reviewed_tree` under the local ref `refs/review-ledger/<YYYY-MM>/<tree id>`, which points at the tree itself.** `anchors: row6, row39, row40, row41, row50, row87, row88, row89, row90, row91, row92, row93, row94, row95, row96, row97, row98, row99, row100, row101, row102, row103, row104, row105, row106, row107, row108, row109, row110, row112, row114, row115, row116`. A squash merge leaves a staged round's tree reachable from nothing (rows 39 and 40), and the analyses that read these rows run across merged PRs (Context). A ref keeps the tree and every object under it from `git gc` until the ref is deleted (row 39), and the docs state that in one sentence, as a retention fact. They give no procedure for content a pin holds (row 116). The repo already keeps two local pins under their own namespaces (row 92). Two lighter options fail:
- No pin. The engineer chose to pin (row 6).
- A longer `gc.pruneExpire`. It is repository-wide config that keeps every unreachable object, and it still expires.

Three heavier options were set aside:
- A wrapper commit per tree, or one commit chain per branch. Each needs `commit-tree`, and a chain needs a compare-and-swap on one shared ref that concurrent appenders contend for. A ref at the tree needs neither.
- A lightweight tag. `git tag` lists `refs/tags/`, and `git push --tags` sends it.
- A `.keep` pack per round. It needs `pack-objects` per append, and git never repacks a kept pack, so its objects never delta against later versions.

The choices within the design, each with what it set aside:
- *Named by its own id.* The name is the value, so a round's appends, a retry and concurrent appenders write one ref with one value, with no lock and no new row field. An `update-ref` failure that is not a cap kill probes the ref and stays silent when it already names the tree, because git's lock on the ref can fail the loser of a race (row 91). The probe misses a winner that still holds the lock past git's retry window, so a lost race can print the note although the winner's pin lands. Set aside:
  - Relying on git's lock retry alone. It narrows the race without closing it.
  - Holding the ledger append lock around the pin. That lock orders one ledger file's appenders, not other sessions or worktrees that share the namespace, and it proceeds unlocked after its retries (row 50).
- *A month bucket.* The month is the first seven characters of the row's own `event_time`, so a later expiry deletes a month with one `for-each-ref` piped to `update-ref --stdin`, and a reader can tell which rows a deleted month affects. The `EVENT_TIME` block moves above the capture so the pin can read it. Set aside:
  - A flat namespace. A packed ref records no age, so an expiry would have to read every ledger row.
  - A second `date -u +%Y-%m` call. An append that straddles a month boundary would then name its pin by a month other than its row's.
- *`reviewed_head` stays unpinned.* A commit pin keeps every ancestor, and placement compares head ids without reading a commit (Critical files item 5). A head stays fetchable while a branch, a reflog or a remote's ref reaches it, such as a forge's PR ref (`review-pr-checkout.sh:392` fetches one). The docs state what that gives up: once a branch is deleted, a staged round's `git diff <reviewed_head> <reviewed_tree>` can stop resolving if its head was never pushed or was rewritten.
- *Before `umask 077`.* M10's reasoning covers the ref file and its month directory (row 95).
- *A failed pin never fails the append.* The row already holds both ids.
  - The pin prints its own note, worded apart from M2's because no field is empty.
  - An empty tree is never pinned.
  - The docs give an audit command that derives each row's ref from its `event_time` and `reviewed_tree`, and lists every recorded tree its ref does not name, in a month that still holds at least one pin (row 109). Run inside the `gc.pruneExpire` window, it finds a failed pin while the tree still resolves and can be pinned by hand.
  - A month the docs' month deletion emptied lists nothing until a later append pins a tree there.
  - The docs say never to hand-pin a tree from a deleted month, in the same bullet as the hand-pin instruction.
  - The month test also hides a month whose every pin failed, which the docs say.

  Set aside:
  - A row field that records whether the pin landed. The engineer chose two fields (row 3).
  - Listing every unpinned tree. After a month deletion, it would list every tree the deletion took, and the hand pin it advises would undo the deletion.
  - Keeping the full listing and dropping the hand pin. The audit then reports a failed pin with no way to keep its tree, which is the one action that needs the `gc.pruneExpire` window.
- *Hooks stay on.* `update-ref` fires a `reference-transaction` hook (row 90). A hook that rejects the update makes the pin print its note. Set aside:
  - `-c core.hooksPath=/dev/null`, as `review-pr-checkout.sh:392` passes for its fetch. A user's reference-transaction hook exists to see every ref update. That precedent also guards against a relative `core.hooksPath` resolving inside a tree under review. The plan accepts that case as no wider than the author's own `git commit` in the same tree, which fires the same hook directory.
- *A cap kill can strand a lock.* A SIGKILL after the cap's grace can leave the ref's lock behind (row 91):
  - In the files backend the lock blocks only a later pin of the same tree in the same month, which then prints the note.
  - In a reftable repository it blocks every ref update, `git commit` included, until someone removes it at the path git names (`_lib.sh:123-125`).
  - In either backend it fails a whole month deletion that lists the locked pin, which then deletes no pin (row 114).
  - The docs say a pin note naming `timed out` can mean a stranded lock, and `docs/hooks.md` § "Gate deadlock recovery" gains the ref lock (Critical files item 10).
  - `review-pr-checkout.sh:392` already caps a fetch that writes a ref.
  - No case runs a reftable repository (Out of scope).
- *Shared across worktrees.* Linked worktrees share the namespace (G4), so a pin outlives its worktree and branch. No shipped script lists, deletes or pushes the namespace (row 93). Outside shipped code, these meet the pins, and the docs name each (row 106):
  - Only `git push --mirror`, a plain push to a remote with `remote.<name>.mirror` set, or a refspec covering the namespace sends them. `_lib.sh:2290` already records `--mirror` as its own push reason.
  - A mirror-style fetch that prunes deletes them.
  - Tools that list or copy every ref meet them.
- *Orphan pins.* Three sources leave a pin that no row records, and the docs name each:
  - An append that aborts after the capture, because the pin runs before the row is built.
  - A retry deduped after the index moved (M4).
  - Any deletion of a ledger file, which takes its rows and leaves their pins. The stale-file sweep each append runs deletes idle files, `clear-stale` runs the same sweep, and the docs' Reset bullet has the reader delete one by hand (row 105). This plan does not assess the sweep (Out of scope).

  The new docs bullet's orphan-pin sentence is that fact's one home, and the Reset bullet stays as it is (row 105). Set aside:
  - Pinning after the append. It runs after `umask 077`, which M10 avoids, and the append's status cannot tell a written row from a deduped one (row 99).
- *Missing objects.* A pinned tree can name a blob the repository lacks only in a partial clone, where `--missing-ok` skipped the fetch, or in an already damaged repository (row 98).
- *No expiry or cap in this PR.* The engineer deferred both with a trigger (row 101). Out of scope states the tradeoff and the proposed trigger, Settled question 10 the storage estimate, and the docs the measuring and audit commands and the month deletion (rows 96, 97, 109, 112 and 114).
- *Real pins in tests stay in temporary repositories.* Every append test found runs in one (row 100), and Verification step 8 checks the real checkout after the selected tests run.

## Critical files

One `code-writer` dispatch covers every file below. Who runs each Verification step follows row 86:
- The dispatch prompt directs `code-writer` to run the one-off checks of Verification steps 5 to 7 as part of this plan. Each runs cases in the test files the dispatch edits, and steps 5 and 6 also edit and revert the dispatch's own files. Its return reports each of those steps' outcomes.
- No step has `code-writer` call `review-ledger.sh append` from Bash. Step 7's appends run inside a pytest case, so its rows land only under the test's isolated `HOME`, as every existing append case's rows do.
- The dispatching session runs step 8 once the dispatch returns, because the selected suites, the all-files shellcheck and the serial `timing` pass go past the narrowly-scoped checks `code-writer.md:25-27` allows.
- The PR body records both sets of outcomes (step 12).

1. `claude/.claude/scripts/_review-ledger-lib.sh`
   - Add `_review_ledger_is_object_id` after `_review_ledger_is_row_id` (`:115-123`). Add the other six functions in the Approach after `_review_ledger_site_hash` (`:395-446`).
   - Header comments, one fact per sentence:
     - "`_review_ledger_is_object_id VALUE`: true iff VALUE is a 40- or 64-digit lowercase hex git object id." Then: "The class lists its digits for the reason `_review_ledger_is_row_id` gives."
     - "`_review_ledger_repo_git REPO_ROOT INDEX_FILE GIT_ARGS...`: runs a capped `git -C REPO_ROOT GIT_ARGS...` with its stderr discarded, and returns git's status or the cap's." Then: "The subshell unsets the variables that redirect git to another repository, as worktree-ensure.sh and deny-reviewer-tree-mutation.sh do." Then: "A non-empty INDEX_FILE becomes that call's GIT_INDEX_FILE."
     - "`_review_ledger_capture_note STEP CAUSE`: prints the one stderr line an append gives for an empty reviewed-state field that git did not report as an expected state."
     - "`_review_ledger_reviewed_head REPO_ROOT`: prints HEAD's commit id, or nothing when git reports none, as on an unborn branch." Then: "A timeout, or output that is not an object id, also prints nothing, after one stderr note." Then: "Returns 1 only after a timeout, so the caller can skip the tree capture."
     - "`_review_ledger_reviewed_tree REPO_ROOT`: prints the id of the index written as a tree object." Then: "With no index file, or on an unmerged index, it prints nothing and no note." Then: "Any other failure prints nothing after one stderr note." Then: "It writes from a copy of the index, so it never takes the repository's index lock." Then: "A signal that kills the append mid-capture leaves the work directory behind, as it does for render." Then: "Returns 1 only after a timeout."
     - "`_review_ledger_reviewed_tree_in REPO_ROOT INDEX_PATH WORK_DIR`: the body of `_review_ledger_reviewed_tree`, with WORK_DIR for the index copy." Then: "A failed write-tree stays silent only when ls-files lists unmerged entries, because write-tree's exit status does not tell them from an object it could not write." Then: "write-tree runs with core.splitIndex off, so rewriting the copy never writes a shared index into the git directory."
     - "`_review_ledger_pin_tree REPO_ROOT TREE_ID MONTH`: points refs/review-ledger/MONTH/TREE_ID at TREE_ID, so git gc keeps the tree and everything under it." Then: "The ref's name is its value, so concurrent appenders write one ref with one value." Then: "A failed write stays silent when the ref already names the tree, as after another appender won the ref's lock, and otherwise prints one stderr note." Then: "Returns 0."
   - `_review_ledger_confirm_ignored` (`:941-953`) calls `_review_ledger_repo_git "$real_root" "" check-ignore -q -- "agent-reviews/$name"` in place of its subshell. Its header loses the sentence about the subshell, which now lives in the helper's header.
   - `_review_ledger_check_not_retired` (`:714-735`): the `del(...)` at `:728` gains `.reviewed_head, .reviewed_tree`. The header at `:718` reads "every one except id, schema_version, event_time, reviewed_head and reviewed_tree".
   - Reuse `_lib_capped`, `_lib_status_consistent_with_cap_kill`, the `render` work-directory shape at `:1138-1153`, and the digit class at `:119-121`. Neither `_lib.sh` function needs a header change, because the lib header at `:4-6` already names both.
2. `claude/.claude/scripts/review-ledger.sh`
   - `:40` becomes `_LEDGER_SCHEMA_VERSION=6`.
   - The `EVENT_TIME` comment and assignment (`:548-555`) move unchanged to just after `:536`, and the comment gains one line: "The reviewed-tree pin below names its ref by this value's month."
   - After it, and before the umask comment at `:538`, the capture-and-pin block from the Approach, under three comment lines:
     - "Best-effort: an empty value means git could not report that state, and the append goes on."
     - "Captured and pinned before the umask below, so git writes its objects and the pin's ref under the invoking shell's umask."
     - "A HEAD look-up that timed out skips the tree capture, so a hung HEAD look-up costs one cap."
   - The build (`:571-588`) gains two `--arg`s and the keys `reviewed_head` and `reviewed_tree` after `site_hash`.
   - The dedup comment (`:624-625`) lists "schema_version, event_time, id, reviewed_head and reviewed_tree", and a new sentence follows: "A retry after HEAD or the index moved is therefore still a no-op, and the landed row keeps the first attempt's state."
   - The tally (`:631-649`) changes in three places:
     - It opens with "the jq, awk, git and cp calls".
     - It gains one line: "The reviewed state adds capped git calls (the HEAD look-up, the index path look-up, write-tree, ls-files -u after a failed write-tree, the pin's update-ref, and rev-parse after a failed update-ref) and a capped cp of the index."
     - `:647` reads "The bare git rev-parse --show-toplevel, the sweep's find, and the reviewed-tree capture's index test, mktemp, rm and rmdir are uncapped."
   - The dedup key at `:654` is unchanged.
3. `claude/.claude/hooks/tests/test_review_ledger_script.py`
   - `_make_git_recording_argv`, `ambient_umask`, `file_mode` and `init_git_repo` join the `helpers` import (row 46).
   - Fixture updates:
     - A new `_object_id_digits(repo)` returns the length of `git rev-parse HEAD` in `repo`. `_row_envelope_bytes` (`:148-161`) takes it as a new keyword-only `object_id_digits` argument and gains `"reviewed_head"` and `"reviewed_tree"` of `"0" * object_id_digits`, after `site_hash` (rows 13 and 15).
     - `_fields_for_row_of_bytes` (`:164-180`) gains a keyword-only `repo` argument after `session_id`, which `:4249` passes positionally (row 62). It passes `repo` through, and its five callers (`:1682`, `:2823`, `:3347`, `:3361`, `:4249`) pass `repo=git_repo`.
     - `_DEDUP_KEY_EXEMPT_CHANGES` (`:2854`) gains `"reviewed_head": "f" * 40, "reviewed_tree": "f" * 40`. That drives the field-table test (`:2974`), the per-attempt retry test (`:2993`) and `test_a_row_differing_only_in_a_per_attempt_field_dedups` (`:3113`) for both fields (row 11).
     - `schema_version` 5 becomes 6 at `:153`, `:328`, `:781`, `:800`, `:1344` and `:3654`.
     - The v4 case (`:783-800`) also deletes both new keys, so its row is a faithful v4 row.
     - `_retry_after_changing_one_field`'s body (`:2958-2972`) moves into `_retry_against_rewritten_retirer(isolated_home, git_repo, rewrite)`, where `rewrite` maps the stored retirer to the copy the ledger keeps. The old helper calls it with `lambda row: {**row, field: value}` (row 62).
     - `_append_near_limit_rows_from_distinct_sessions` (`:4240-4263`) gains three assertions ahead of its size assertion at `:4260`, so a failed capture in either parallel case fails under its own message, not as a row-size failure (row 85, M3):
       - No appender's stderr contains "the reviewed state was not fully recorded".
       - Every landed row has a non-empty `reviewed_head` and `reviewed_tree`.
       - `git for-each-ref refs/review-ledger/` lists at least one ref, and every ref it lists names the rows' one `reviewed_tree` (M14).

       It asserts nothing about the pin note, because a lost race can print it while another appender's pin lands (M14). The pin class's lock-loser parameter pins the silent arm deterministically (rows 85 and 91).
   - New or extended cases. Each stderr assertion counts lines containing "the reviewed state was not fully recorded", or "the reviewed tree was recorded but not pinned" for the pin, because other capped calls may print an uncapped notice:
     - The happy path (`:316-349`) pops both new keys before its dict comparison, then asserts:
       - `reviewed_head` equals `git rev-parse HEAD`.
       - `reviewed_tree` equals what `git write-tree` prints after the append.
       - `reviewed_tree` differs from `HEAD^{tree}`, because `git_repo` holds a staged change (row 28).
       - `git rev-parse --verify refs/review-ledger/<the first seven characters of the row's event_time>/<reviewed_tree>` prints `reviewed_tree` (row 89, M14). Verification step 5 says what a failure of this assertion means.
     - A new CLEAN case appends a real CLEAN in `git_repo`, as the case at `:783-800` does. Its row's `reviewed_head` equals HEAD's id, and its `reviewed_tree` equals what `git write-tree` prints after the append, which differs from `HEAD^{tree}` (rows 28 and 52, M6). It is its own test, so a failure reads under the CLEAN-state name rather than the v4-dedup one.
     - A new `show --out` case appends one ADDRESS row, as the case at `:385-405` does. The shown row's `reviewed_head` and `reviewed_tree` equal the stored row's, and its `reviewed_head` equals HEAD's id (M7).
     - In an `init_git_repo` repository on branch `feature` with one staged file and no commit, a CLEAN append runs. The case pins that an empty HEAD id skips neither the tree capture nor the pin, and it settles row 73. It asserts:
       - The append exits 0 and lands one row.
       - The row's `reviewed_head` is `""`.
       - Its `reviewed_tree` equals what `git write-tree` prints after the append.
       - Stderr has no line containing "the reviewed state was not fully recorded".
       - `git for-each-ref refs/review-ledger/` lists exactly `refs/review-ledger/<the first seven characters of the row's event_time>/<reviewed_tree>`.
     - With `write-tree` exiting 1 through `path_with_failing_tool_shim(..., "git", "write-tree")`:
       - The append exits 0 and lands one row.
       - The row's `reviewed_tree` is `""`.
       - Its `reviewed_head` is set.
       - Stderr carries exactly one note, naming `write-tree` and `exit 1`, because the copy holds no unmerged entry (M2).
       - `git for-each-ref refs/review-ledger/` prints nothing, so an empty tree is never pinned (M14).
     - With `update-ref` exiting 1 through `path_with_failing_tool_shim(..., "git", "update-ref")` (M14):
       - The append exits 0 and lands one row with both fields set.
       - `git for-each-ref refs/review-ledger/` prints nothing.
       - Stderr carries exactly one line containing "the reviewed tree was recorded but not pinned", naming `update-ref` and `exit 1`.
       - Stderr carries no line containing "the reviewed state was not fully recorded".
     - With a `_make_git_recording_argv` shim whose action exits 124 when an argument is `HEAD^{commit}`, and `REAL_GIT` set to `shutil.which("git")` as that helper requires (row 110, M12):
       - The append exits 0 and lands one row.
       - Both fields are `""`.
       - Stderr carries exactly one note, naming `HEAD look-up`.
       - The argv log holds no `write-tree`, `--git-path` or `update-ref` call.
     - An identical append after another `git add`, with these assertions:
       - Before that append, `git write-tree` differs from the first row's `reviewed_tree`, so the state did move.
       - The append exits 0 and adds no row.
       - The kept row's `reviewed_tree` is the first row's.
       - `git for-each-ref refs/review-ledger/` then lists two refs, one per attempt's tree, because a deduped retry still pins its own tree (M4, M14).

       The case repeats the `:3113` coverage on purpose, because it is the only case where real state moves between attempts.
     - A `--ref` retry across the upgrade calls `_retry_against_rewritten_retirer` with a `rewrite` that deletes both new keys and sets `schema_version` 5. The repeated retirer append exits 0 and adds no row, so a pre-6 retirer passes both the retry check's `del(...)` and the dedup key.
     - Under `ambient_umask(0o022)`, each of these has its group-read and other-read bits set:
       - The loose object of the row's `reviewed_tree`, `<git dir>/objects/<first two digits>/<the rest>` (M10).
       - The pin's ref file, `<git dir>/refs/review-ledger/<the first seven characters of the row's event_time>/<reviewed_tree>` (row 95).
       - That ref file's month directory (row 95).

       When `<git dir>/reftable` exists, the case skips the two ref-file assertions, because a reftable repository keeps no loose ref file.
4. `claude/.claude/scripts/tests/test_review_ledger_lib.py`
   - `_make_git_recording_argv`, `add_linked_worktree`, `build_conflicted_merge`, `_make_git_blocking_on_arg` and `_timeout_binary_present` join the `helpers` import, and `time` joins the standard-library imports (rows 46 and 72). `from .conftest import _clean_env` is added for the pin class's real-objects case (row 115).
   - Fixture updates:
     - `_row` (`:604-613`) gains `"reviewed_head": "4" * 40, "reviewed_tree": "5" * 40`, and its `schema_version` becomes 6 (`:606`).
     - The retry case at `:1372` gives its retry a fresh value in every `_DEDUP_KEY_EXEMPT_CHANGES` field except `id`, and its name changes to say "outside its per-attempt fields".
     - The render case at `:2018` also asserts that `"4" * 40` and `"5" * 40` are absent from the output.
     - `test_a_foreign_git_dir_environment_does_not_launder_the_check` (`:2700-2714`) is parametrized over `GIT_DIR` and `GIT_WORK_TREE`, and renamed to say a foreign git environment. The `GIT_WORK_TREE` parameter names the foreign repository's work tree, whose `.gitignore` ignores `agent-reviews/`, and it keeps the premise assertion at `:2709` (row 81). If that premise fails, the parameter is dropped, and the return says `GIT_WORK_TREE` is unpinned.
   - A cross-version retry case: a retirer with neither new key at `schema_version` 5, and a retry carrying both, passes `_retry_check`.
   - A new class for the capture functions holds the head and tree cases below, under four conventions:
     - Every case sets `TMPDIR` to an empty test directory and asserts it is still empty afterward.
     - Each stderr assertion counts lines containing "the reviewed state was not fully recorded": zero where a case says no note, one where it says one note. On a host with neither `timeout` nor `gtimeout`, `_lib_capped_for` prints an uncapped notice for each capped call (`_lib.sh:137`), so an empty or exact stderr assertion would fail there.
     - A shim case sets `REAL_GIT` to `shutil.which("git")`.
     - Shim cases that check only the printed value, the exit status and the note share one parametrized test. Its parameters are the shim, the expected status and the words the note names. It also asserts that the note contains `timed out` if and only if the shim's status is 124, 137 or 143, as the `confirm_ignored` case at `:2675` asserts the absence.
   - The class's head cases:
     - After one commit, head is the commit's id, with exit 0 and no note.
     - In the unborn `repo` fixture, head is empty, with exit 0 and no note.
     - With `GIT_DIR` or `GIT_COMMON_DIR`, one parameter each, naming another repository's git directory, head is this repository's id (M11):
       - The other repository has a commit on the branch this one has checked out, differing in file content and message.
       - Each parameter first asserts that an unscrubbed `git -C <repo> rev-parse HEAD^{commit}` under its variable prints the foreign id, as the `confirm_ignored` case at `:2709` asserts its premise.
       - If the `GIT_COMMON_DIR` premise fails, that parameter is dropped, and the return says `GIT_COMMON_DIR` is unpinned.
       - `GIT_INDEX_FILE` and `GIT_WORK_TREE` are pinned at the decoy tree case and the `confirm_ignored` case instead, because the HEAD look-up reads neither the index nor the work tree (row 81).
     - With a `_make_git_recording_argv` action that prints `partialline` and exits 0 for `HEAD^{commit}`, head is empty, with exit 0 and one note naming `HEAD look-up`.
     - With `_make_git_exiting_with_status(..., "HEAD^{commit}", 124)`, head is empty, with exit 1 and one note naming `HEAD look-up` and `timed out`.
   - The class's tree cases:
     - After a staged change, tree equals what `git write-tree` prints afterward, and `git cat-file -t` on it prints `tree`. Before that `write-tree` runs, the live index is byte-identical to its state before the capture, and no `index.lock` exists.
     - With `<git-dir>/index.lock` present, tree is still an id, the lock file is still present, and the live index is byte-identical to before.
     - In a linked worktree with its own staged change, on a branch other than the `repo` fixture's `feature`, tree is that worktree's index tree, not the main worktree's. The main repository gets a commit before `add_linked_worktree`, because `git worktree add -b` needs a resolvable HEAD (recalled, not run). Afterward the case asserts that the two worktrees' `git write-tree` outputs differ, so the comparison can fail.
     - On an unmerged index from a real merge conflict, tree is empty, with exit 0 and no note.
     - Two cases on that unmerged index sit outside the shared parametrized test, because they need the conflicted repository (M2):
       - With `_make_git_exiting_with_status(..., "ls-files", 3)`, tree is empty, with exit 0 and one note naming `write-tree` and `exit 128`, and naming neither `exit 3` nor `timed out`. The case first asserts that `git write-tree --missing-ok` on that index exits 128, so the expected status is read rather than recalled (row 57).
       - With `_make_git_exiting_with_status(..., "ls-files", 124)`, tree is empty, with exit 1 and one note naming `write-tree`, `ls-files -u` and `timed out`.
     - In the unborn `repo` fixture, which has no index file, tree is empty, with exit 0 and no note.
     - With `_make_git_exiting_with_status(..., "--git-path", STATUS)`, tree is empty, and each status gives one note naming `index path look-up`:
       - Status 1 gives exit 0.
       - Status 124 gives exit 1, and the note also names `timed out`.
       - Status 0, which prints nothing, gives exit 0, and the note does not name `timed out`, so an empty path never reads as a missing index file.
     - With `path_with_failing_tool_shim(..., "cp")` on a repository whose index exists, tree is empty, with exit 0 and one note naming `index copy`. With `exit_status=124`, the exit is 1 and the note also names `timed out`.
     - With a `_make_git_recording_argv` action that, for `write-tree`, creates `"$GIT_INDEX_FILE.lock"` and exits 137, tree is empty, with exit 1 and one note naming `write-tree` and `timed out`.
     - With an action that prints `partialline` and exits 0 for `write-tree`, tree is empty, with exit 0 and one note naming `write-tree`.
     - With `TMPDIR` naming a directory that does not exist, tree is empty, with exit 0 and one note naming `temporary directory`, and that path still does not exist.
     - With the case's working directory set to the repository's parent, and `TMPDIR` a relative path that exists under it, tree is still the repository's index tree:
       - The working directory sits outside the repository, because one inside it would resolve the copy with or without the `$PWD/` prefix.
       - The repository holds a staged entry.
       - The case asserts that the expected tree differs from the id `git hash-object -t tree /dev/null` prints, because a missing copy would yield the empty tree (row 53).
     - In an `init_git_repo` repository whose path contains a space, after one commit and a staged change, with `TMPDIR` an empty directory whose path contains a space, head is the commit's id and tree equals what `git write-tree` prints afterward, each with exit 0 and no note. No other case puts a space in a path the capture passes to `_lib_capped`, `cp` or `GIT_INDEX_FILE`.
     - With an entry staged by `git update-index --add --cacheinfo` for the blob id that `git hash-object --stdin` prints, without `-w`, for content unique to the case, tree is an id and `git cat-file -t` on it prints `tree` (M12, row 60). Before the capture, the case asserts two premises:
       - `git cat-file -e <blob id>` exits nonzero, so the blob is absent.
       - Plain `git write-tree` on that index exits nonzero, so the case fails if `--missing-ok` is dropped.
     - With a split index, tree is an id, and the git directory's sorted top-level listing afterward equals the one recorded before the capture (row 49). The setup and premises, in order:
       - `core.splitIndex=true` is set, and the repository tracks five files.
       - `git update-index --split-index` runs after a first staged change.
       - Edits to three of the five files are staged with `git -c splitIndex.maxPercentChange=100 add`, so that write keeps them out of the shared index and the split part exceeds the default 20 percent (row 66).
       - Before the capture, the case asserts that a `sharedindex.*` file exists in the git directory and records the listing.
     - Two cases really block, so they test the cap and not only the status arms. Each is marked `@pytest.mark.timing` and skipped when `_timeout_binary_present()` is false, as at `test_require_code_review.py:1387-1391`, and each times its call with `time.monotonic()` (row 72):
       - With `_make_git_blocking_on_arg(bin_dir, "write-tree")`, tree is empty, with exit 1 and one note naming `write-tree` and `timed out`. The call returns in under 15 s, well before the shim's 20 s sleep, and `TMPDIR` is empty afterward. Every capture git call shares `_review_ledger_repo_git`, so this case pins that wrapper's cap.
       - With a `cp` shim in `bin_dir` that runs `sleep 20`, on a repository whose index exists, tree is empty, with exit 1 and one note naming `index copy` and `timed out`. The call returns in under 15 s, and `TMPDIR` is empty afterward. The `cp` has its own `_lib_capped` site, so the git case does not cover it.
     - With `GIT_INDEX_FILE` naming an absent decoy path, tree is this repository's index tree, and the decoy path still does not exist (M11). The case first asserts that an unscrubbed `git -C <repo> rev-parse --path-format=absolute --git-path index` under that variable prints the decoy path (rows 47 and 81).
   - A predicate class. Build every id at runtime (`"a" * 40`), because a literal run of 32 or more hex digits in a test source trips the redaction gate (row 25):
     - It accepts 40 and 64 lowercase hex digits.
     - It rejects 39, 41, 63 and 65 digits, an empty value, and 40 characters with one `g`.
     - It rejects uppercase hex under `_locale_that_widens_ascii_ranges` with `shopt -u globasciiranges`. The case first proves that the range form `*[!0-9a-f]*` would accept the probe, as `TestRowIdPredicate` does at `:666-682`.
   - A pin class, under the capture class's conventions. Each case stages a change, takes its tree from `git write-tree`, passes month `2000-01`, and counts stderr lines containing "the reviewed tree was recorded but not pinned" (M14):
     - Pinning the same tree twice exits 0 both times with no note. `git for-each-ref refs/review-ledger/` then lists exactly `refs/review-ledger/2000-01/<tree>`, which resolves to the tree.
     - One parametrized test covers the failure arms through `_make_git_recording_argv` actions. Each parameter exits 0 with the stated stderr:
       - `update-ref` exits 1 after a real pin already wrote the ref: no note (row 89).
       - `update-ref` exits 1 with no ref present: one note naming `update-ref` and `exit 1`, and not `timed out`.
       - `update-ref` exits 1 while `refs/review-ledger/2000-01/<tree>` already names another tree, which the case wrote first and pointed the ref at with the real git: one note naming `update-ref` and `exit 1`. The parameter tests the probe's equality check, not only its exit status.
       - `update-ref` exits 124: one note naming `update-ref` and `timed out`, and the argv log holds no `rev-parse --verify` call.
       - `update-ref` exits 1 and `rev-parse --verify` exits 124: one note naming `exit 1`, `rev-parse` and `timed out`.
     - A case on real objects proves that a pin keeps its tree through `git gc` across worktrees, and that deleting it lets `git gc` free the tree (G4, rows 103, 112 and 115). It uses neither the `repo` fixture nor `add_linked_worktree`, because neither passes an environment to git (row 115). Its rules:
       - Every git call it issues itself runs with `cwd` in its repository or one of its worktrees and `env=_clean_env(**isolated_git_config_env(tmp_path / "git-home"))`. That environment drops `GIT_DIR`, `GIT_WORK_TREE`, `GIT_COMMON_DIR` and `GIT_INDEX_FILE`, and reads no global or system config. The case builds its repository with `init_git_repo(..., env=...)` and adds worktrees with its own `git worktree add -q -b`.
       - The capture and pin calls pass `env=isolated_git_config_env(...)`, because `_bash` overlays its environment on the inherited one and the lib unsets the four variables itself.
       - Before its first `git worktree remove`, `git gc` or pin deletion, it asserts that `git rev-parse --path-format=absolute --git-common-dir`, run that way, prints a path under the resolved `tmp_path`.
       - The same guard asserts that `git rev-parse --path-format=absolute --git-path objects` prints a path under the resolved `tmp_path`, because an exported `GIT_OBJECT_DIRECTORY` moves where `git gc --prune=now` deletes.
       - Every `git gc` runs as `git gc --prune=now --quiet` with `gc.cruftPacks=false` set, so no outcome depends on an object's modification time or the host's gc mode (row 103).

       In order:
       1. The repository gets `core.logAllRefUpdates=always`, `gc.cruftPacks=false` and one commit. Worktrees on branches `pin-kept` and `pin-control` follow, each with its own staged file at its root, of content unique to the case.
       2. The tree capture runs in each, and the pin runs only for `pin-kept`'s tree. Both trees and both staged blobs resolve with `git cat-file -e`.
       3. Both worktrees are removed with `git worktree remove --force`, and `git gc` runs in the main worktree. Then:
          - The kept tree and its blob still resolve, and `git rev-parse --verify` on the kept ref, in the main worktree, prints the tree.
          - `pin-control`'s tree and blob no longer resolve, which shows that gc would have deleted the kept objects without the pin.
          - Unless `<common git dir>/reftable` exists, the kept ref's reflog file, `<common git dir>/logs/<ref>`, exists.
       4. `bash -c` runs `git for-each-ref --format='delete %(refname)' refs/review-ledger/2000-01/ | git update-ref --stdin`, the month deletion's shape, in the main worktree, and `git gc` follows. Then:
          - The kept tree and its blob no longer resolve.
          - `git for-each-ref refs/review-ledger/` prints nothing.
          - Where step 3 found the reflog file, this shows that a `delete` line leaves no reflog holding the tree (row 112).
          - The deletion pipeline's exit status is asserted, so a failed `update-ref --stdin` reads as a failed deletion and not as "tree still resolves".
          - A red step 3 reflog check or step 4 resolve check stops `code-writer`, which reports to the engineer. The case keeps `core.logAllRefUpdates=always` and its assertions, and a run that is red in any of the five is reported as flaky, not retried until green.
5. `docs/scripts.md`
   - `:195` reads "apart from `id`, `schema_version`, `event_time`, `reviewed_head` and `reviewed_tree`, which is the append's dedup key".
   - The **Reset.** bullet (`:181`) stays as it is, because the new bullet's orphan-pin item already names a Reset deletion (M14, row 105).
   - A new bullet after **Id and site hash.** (`:192`), at that entry's bullet depth. This plan's own text:

     > **Reviewed state.** From schema 6 each row stores `reviewed_head`, the commit HEAD named, and `reviewed_tree`, the index written as a tree object. Both are read when the row is appended.
     > - Either field can be empty, and the row lands either way. The row does not record why a field is empty.
     > - `append` prints a line starting `review-ledger.sh: the reviewed state was not fully recorded` when it leaves a field empty, except in three cases that print nothing:
     >   - `reviewed_head` when git reports no HEAD commit. That covers a branch with no commit, and also a repository git cannot read, so a silent empty `reviewed_head` can hide a broken repository.
     >   - `reviewed_tree` with no index file.
     >   - `reviewed_tree` when the index has unmerged entries.
     > - The note goes to `append`'s stderr, so only the session that ran the append sees it, and a `review-orchestrator` return does not relay it. A retry leaves an empty field empty, as the dedup bullet below says.
     > - A step that times out ends the capture, so a hung call costs one capped call's time limit after the steps before it. A capture that never times out takes the sum of its steps' times, which grows with the size of the index.
     > - The tree is written from a copy of the index, so an append never takes the repository's index lock.
     > - Both record the state at append time. `code-review/SKILL.md` § "Review-narrative ledger" runs a round's appends before any fix is dispatched or applied, but nothing checks that a session follows that order. A fix staged before the append, a change staged or committed between the review and the append, or an unstaged edit a reviewer read makes the stored state differ from what the round reviewed.
     > - `append` pins each non-empty `reviewed_tree` under the local ref `refs/review-ledger/<YYYY-MM>/<tree id>`, where `<YYYY-MM>` is the first seven characters of the row's `event_time`. The ref points at the tree, so `git gc` keeps the tree and every object under it until the pin is deleted. Rows that share a tree and a month share one ref.
     > - Linked worktrees share the namespace, so a pin outlives its worktree and its branch. `git push` sends a pin only under `--mirror`, to a mirror remote (below), or under a refspec that covers the namespace, such as `refs/*:refs/*`.
     > - Other git use meets the pins too. This is unverified:
     >   - A remote whose `remote.<name>.mirror` is true, which `git clone --mirror` sets on `origin`, makes a plain `git push` send the pins.
     >   - A fetch whose refspec maps into `refs/*`, as `git clone --mirror` sets up, deletes the pins when it prunes.
     >   - Unfiltered ref listings show them, such as `git for-each-ref`, `git show-ref`, `git log --all` and an editor's ref picker.
     >   - Tools that copy or walk every ref carry them, such as `git clone --mirror`, `git bundle create --all`, `git fast-export --all` and history-rewrite tools.
     >   - With `core.logAllRefUpdates=always`, each pin also gets a reflog file, which `git pack-refs` does not pack.
     > - A pin that fails prints a line starting `review-ledger.sh: the reviewed tree was recorded but not pinned` to the same stderr as the note above, and the row still lands with both fields. A tree with no pin resolves only until `git gc` deletes it, once nothing reaches it and it is older than `gc.pruneExpire`, two weeks by default (`git help gc`).
     > - Two appends that pin one tree at once can print that line although the other append's pin landed. The audit command below tells the two apart.
     > - That line naming `timed out` can mean the killed `git update-ref` left a lock file behind. Later writes of that ref then fail on it, and in a reftable repository every ref write does, `git commit` included. `append` discards git's error, so run the audit command's hand pin below for that tree to see git name the lock's path. `docs/hooks.md` § "Gate deadlock recovery" gives the recovery.
     > - A pin can outlive every row. An append that aborts after the capture, or a retry deduped after the index moved, still pins its own tree. Deleting a ledger file takes its rows with it and leaves their pins, whether the stale-file sweep each append runs deletes it, `clear-stale` does, or the reader does under the Reset bullet above.
     > - `reviewed_head` is not pinned, because a commit pin keeps every ancestor and placement compares head ids without reading the commit. A head commit resolves while a branch, a reflog or a remote's ref reaches it. Once its branch is deleted, a staged round's `git diff <reviewed_head> <reviewed_tree>` can stop resolving if that head was never pushed or was rewritten.
     > - A pin writes no object, because `git add` and the capture already wrote them. It stops `git gc` deleting what nothing else keeps under its tree: staged file versions no kept commit holds, and the directory trees above them. Each pin is a file of its own until `git pack-refs`, which `git gc` runs, moves it into `packed-refs`. A large file in the index at an append is kept once for each version a recorded tree holds.
     > - Run each command below in bash, from a worktree of the repository whose pins or ledger file it reads. Each assumes a full clone, because in a partial clone git can fetch from the promisor remote a missing object a command reads.
     > - To measure what the pins alone keep, run `LC_ALL=C comm -13 <(git rev-list --objects --exclude='refs/review-ledger/*' --all | cut -d' ' -f1 | LC_ALL=C sort -u) <(git rev-list --objects --glob='refs/review-ledger/*' | cut -d' ' -f1 | LC_ALL=C sort -u) | git cat-file --batch-check='%(objecttype) %(objectsize:disk)' | awk '{n[$1]++; s[$1]+=$2} END {for (t in n) print t, n[t], s[t]}'`. Its run time grows with the repository's object count, and it prints only when done. It prints one line per object type: the type, the object count and the bytes on disk. The pins' total is the sum of the byte column over every line. It also counts objects that only a reflog or an index holds, so it can overstate. `git for-each-ref refs/review-ledger/ | wc -l` counts the pins.
     > - To list recorded trees whose pin is missing, run this once for each ledger path `show`'s stderr header lists, as `FILE`: `jq -rR 'fromjson? | objects | select((.reviewed_tree // "") != "") | "\(.event_time[0:7]) \(.reviewed_tree)"' FILE | sort -u | while read -r month tree; do [ "$(git rev-parse --verify -q "refs/review-ledger/$month/$tree")" = "$tree" ] || [ -z "$(git for-each-ref --count=1 "refs/review-ledger/$month/")" ] || printf 'unpinned: %s %s\n' "$month" "$tree"; done`. It prints one `unpinned:` line for each recorded tree whose ref is missing or names another object, in a month that still holds a pin. Run it within `gc.pruneExpire` of the appends, while an unpinned tree still resolves. `git update-ref refs/review-ledger/<YYYY-MM>/<tree id> <tree id>` then pins it by hand, except for a tree from a month you deleted (next bullet).
     > - The audit therefore lists nothing for a month deleted as below, and nothing for a month whose every pin failed. Once a later append pins a tree in a deleted month, the audit lists that month's deleted trees again, so do not hand-pin a tree from a month you deleted.
     > - Each pin runs the repository's `reference-transaction` hook, if it has one. A hook that rejects the update makes the pin print its note.
     > - Nothing expires a pin. `git for-each-ref --format='delete %(refname)' refs/review-ledger/<YYYY-MM>/ | git update-ref --stdin` deletes one month's pins in one transaction. When git prints an error, it deleted none of them, as when a listed pin's lock is stranded. A month also holds the pins of PRs still open in it. Once their trees are gone, every pair with a round whose tree only that month pinned is not placed, whether both rounds fall in the month or the pair crosses it.
     > - Deleting a pin frees nothing by itself. A later `git gc` deletes what only that pin kept, once it is older than `gc.pruneExpire`, or at once under `git gc --prune=now`, when nothing else holds it. Run `--prune=now` only while no other git command writes to the repository. An object already in a pack goes only when a `git gc` repacks every pack, which `git gc --auto` does not do on every run. Once a tree is gone, every row that recorded it reads as not placed for good.
     > - Neither field is in the dedup key. A retry after HEAD or the index moved is a no-op, and the landed row keeps the first attempt's state.
     > - `render` never prints them. A full id is 40 or 64 hex digits, above the redaction gate's 32-digit hex-run detector. `show` and the file `show --out` writes carry them. The detector blocks a row copied from either into a commit or a PR body only where it runs, in claude-config and in a repository opted in with `.claude/private-projects-optin`, and it never catches an abbreviated id. Anywhere else, keeping the ids out of published text is the writer's job.
     > - Rows from before schema 6 have neither key.
     >
     > To compare two rounds:
     > - Read rows from the branch ledger file alone, one line at a time with `jq`, skipping a line that does not decode to an object, as `show` does. `show` itself merges in the session file, whose rows can share a `session_id` and continue the round count, and no row field tells the two files apart.
     > - The Reset bullet above says how `show`'s stderr header names the branch file. Nothing is compared when the header names no branch file:
     >   - The header reads `scope=session`.
     >   - The one path listed is the session file, which is named `<repo hash>.<session id>.jsonl` after the `session_id` its rows carry. It is listed alone when the branch file is empty or absent.
     > - A round is the rows sharing one `session_id` and `round`, because other sessions on the branch file can reuse a round number. CONSULT rows are left out, because a CONSULT row records a consult verdict rather than a finding, and it is appended before the round retries its denied spawn. Form rounds, and apply the two pairing rules below, from these whole groups, whatever their schema or state.
     > - Pair round N with round N+1 only when round N has an ADDRESS row. A CLEAN round starts no fix, so the delta after it is new work.
     > - Round N+1 is the same session's round N+1 when it has one. Otherwise it is the round-N+1 group whose earliest row is the first by `event_time` after round N's earliest row, because a resumed or new session continues the branch's round count.
     > - Take a round's state from its earliest row by `event_time` that has a `schema_version` of 6 or more and a non-empty `reviewed_tree`.
     > - A `--relog` row (a reopen, a gate-denial recovery or a `--carry-forward` withdrawal) and a retried append record the state at their own append time, which can follow the round's fix or commit.
     > - Each ADDRESS, DEFER and SETTLED row of round N+1 is placed by its cited location: its `source`, or its `cited_line` on a carry (`decided_by` of `carry`), whose `source` is the decided text's range. A `path:N` location is the one-line range N to N. A location with no line, such as the path alone that names a file a fix creates, is not placed.
     > - The tally is per row, and it covers every reviewer lane unless the reader filters rows on `reviewers`.
     > - A `--relog` row counts like any other row of its round, because no field marks one. It stores its `--ref` decision's lanes, and a fresh decision on a repeat also stores a `ref`. A gate-denial recovery therefore repeats its decision's location in the same round.
     > - A reopen or a `--carry-forward` withdrawal adds an earlier decision's location. Its line numbers belong to the text that decision cited, not to round N+1's tree, so whether it reads inside is arbitrary. Dropping rows whose `ref` is set and whose `cited_line` is empty removes them, along with gate-denial recoveries and fresh decisions on repeats.
     > - A location is inside when any of its lines falls in a hunk's `+` range of `git diff -U0 <round N tree> <round N+1 tree>` for its file, and outside otherwise. Default context lines would count unchanged neighbours as changed.
     > - Inside means changed between the two recorded states, not written by fix N. The delta also holds whatever else reached the index between the two appends, such as the engineer's own staged edits, a rebase or a merge. Each adds `+` ranges of its own, which push counts toward inside.
     > - Inside is block-granular. An ADDRESS, DEFER or SETTLED `source` ranges the whole block the finding concerns, so a finding reads as inside when the delta changed any line of that block, not only the defect's own line.
     > - A pair whose `reviewed_head` values differ, an empty one included, is placed and counted apart from pairs with one HEAD. A HEAD move means a commit, a rebase or a merge landed between the two appends. Consecutive cumulative passes bracket a committed fix, so leaving such pairs out would drop every cumulative pair.
     > - Hunk boundaries follow the reader's `diff.algorithm`, and with `diff.renames` off a renamed and edited file reads as all `+` lines. Counts from readers with different diff config are therefore approximate at that margin.
     > - A hunk header's `+c,n` covers lines c through c+n-1, and `+c` alone covers line c. A deletion-only hunk's `+c,0` covers no line, so the line before a deletion is not changed.
     > - A pair is not placed, which counts as neither inside nor outside, in five cases:
     >   - Either round has no row with a state.
     >   - Either round's `reviewed_tree` fails `git cat-file -e <id>^{tree}`. An unresolvable `reviewed_head` does not change a pair's placement.
     >   - The two trees are equal, so no delta was recorded. That happens when round N was appended after its fix was staged and round N+1 was not, or when fix N left the index unchanged by round N+1's append. The first is the one late-append pattern the rows show.
     >   - The diff exits nonzero.
     >   - An `event_time` tie between rows or groups with different trees leaves the round's state or its pair ambiguous.
     > - Placement assumes each round's rows were appended before that round's fix was staged, and no row records whether that held. A late append has these outcomes, so its error has no single direction:
     >   - Round N late and round N+1 on time: both trees hold fix N, so the trees are equal and the pair is not placed.
     >   - Both late: the delta is fix N+1, which was written for round N+1's own ADDRESS findings, so those findings read as inside. A line fix N wrote reads as outside.
     >   - Round N on time and round N+1 late: the delta holds both fixes, so round N+1's ADDRESS findings read as inside.
     >   - In the last two, the `+` ranges number the lines of the tree after fix N+1, not the tree round N+1 cited.
6. `CHANGELOG.md`, a new bullet under `[Unreleased]` → `### Changed`. This plan's own text:

   > - **`review-ledger.sh append` now records the git state each row was appended against.**
   >   - Each new row stores `reviewed_head` (HEAD's commit) and `reviewed_tree` (the index as a tree object), and `schema_version` is now 6.
   >   - A field is empty when git cannot produce it, and a failed capture never stops the append. `append` prints a stderr line for an empty field unless git reported no HEAD commit, no index file or an unmerged index.
   >   - Each append writes the index's tree objects into the repository's object store, from a copy of the index and under the invoking shell's umask. It pins each recorded tree under the local ref `refs/review-ledger/<YYYY-MM>/<tree id>`, so `git gc` keeps it. `reviewed_head` is not pinned, and nothing expires a pin.
   >   - `/code-review` now appends a round's ledger rows before it dispatches or applies any fix.
   >   - `docs/scripts.md`'s `review-ledger.sh` entry states how to compare two rounds, how long a recorded id resolves, what the pins keep, how to measure and audit them, how to delete a month's pins, and the other limits.
   >   - The change is live on `git pull`. A consumer cannot turn pinning off without editing a tracked file, so reverting this change is the off-switch, and the revert leaves the pins already written.
7. `docs/cost-levers-considered.md`, a new line after the `fix-delta-churn-measure.md` section's last follow-up (`:756`), dated with the commit date. This plan's own text:

   > **<commit date> follow-up:** review-ledger rows from schema 6 on store `reviewed_head` and `reviewed_tree` (see `docs/scripts.md`'s `review-ledger.sh` entry), so each round logged from then on records the commit and index it was appended against. `reviewed_tree` is the index at append time, so a staged round's state is recorded too. The record is per round, while "Reopening would need" asks for one per cumulative pass, and no row field marks a pass kind. The pass kind stays in `/ready-for-review`'s disposition record, not in the ledger, so a measurement of cumulative passes takes them from that record, and a pair whose `reviewed_head` values differ is a stratum, not a cumulative-pass label. Each recorded tree is pinned under a local ref, best-effort, so it resolves until that ref is deleted unless its pin failed, and that entry gives the audit command that finds a failed pin. A measurement read from these rows keeps `comment-discipline-reviewer` outside the comparison, for the reason the scan's limits give, and can place `skill-fidelity-reviewer`'s findings by each row's `source`, as it places any lane's. Nothing records which finding's suggested fix wrote a line, and the lever stays deferred.

8. `claude-skills/skills/code-review/SKILL.md`, in the same `code-writer` dispatch (M13, row 64).
   - At `:449`, the line's opening "Immediately once dispositions are finalized, before review-findings persistence below, call" becomes "Immediately once dispositions are finalized, before dispatching or applying any fix and before review-findings persistence below, call". The rest of the line and of the file stays as it is.
   - The append template needs no new flag (row 27).
   - Verification step 11 covers the skill review this edit needs.
9. `claude-skills/skills/tests/test_skills.py`, one token pin for item 8's clause (M13, row 71).
   - `_REVIEW_LEDGER_PROSE_CONTROLS` (`:8373`) gains `pytest.param("code-review", "## Review-narrative ledger", "before dispatching or applying any fix and before review-findings persistence below", id="ledger-appends-run-before-any-fix")`.
   - The docstring of `TestReviewLedgerPublishAndInjectionControls` (`:8545-8549`) adds "orders a round's ledger appends before its fix" to the controls it lists.
10. `docs/hooks.md` § "Gate deadlock recovery", its lock paragraph at `:529` (M8, M14, row 91).
    - After the paragraph's first sentence, add: "`review-ledger.sh append`'s capped `git update-ref` can strand a ref's lock the same way, and in a reftable repository that lock blocks every ref write, `git commit` included."
    - In the symptom sentence, `'<path>/index.lock'` becomes `'<path>.lock'`, and the sentence gains ", such as `<path>/index.lock` for an index" before its period, so it names the shape a ref lock's error shares (row 91).
    - After it, add: "`review-ledger.sh append` discards git's error, so a ref lock's path shows only once another git command writes a ref that lock blocks."
    - Its last sentence becomes "In a linked worktree an index lock lives under `.git/worktrees/<name>/`."
    - The rest of the paragraph and of the file stays as it is. Row keying and who may append need no edit there.

Not edited, which settles the exploration's other flags:
- `docs/transcript-analysis.md`: its schema floor of 4 stays true (row 32).
- `claude/.claude/scripts/tests/test_author_outcome.py` and `claude/.claude/hooks/tests/conftest.py`: they read rows by field and build none (row 30).
- `claude/.claude/tests/helpers.py` and `scripts/tests/conftest.py`: the existing helpers suffice (rows 46 and 115).

Projected diff: about 1,090 lines.

## Verification

1. `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py -m "not timing"` passes on the selection it makes for this diff, in step 8's run. Step 4 runs the `timing` cases, as CI splits them (row 84).
2. `scripts/list-shell-files.sh | xargs -0 scripts/venv-run.sh shellcheck` is clean in step 8's run.
3. `scripts/venv-run.sh ruff check claude/.claude/hooks/tests/test_review_ledger_script.py claude/.claude/scripts/tests/test_review_ledger_lib.py claude-skills/skills/tests/test_skills.py` is clean in step 8's run.
4. `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py -m timing -n0` passes in step 8's run. It runs the selected `timing` cases serially (row 84).
   - Critical files item 4's two blocking cases are the cap evidence. They pass where `timeout` or `gtimeout` is on PATH. Otherwise they skip, and the report reads "cap not verified locally" for them, never a pass.
   - A failure in any other selected `timing` case goes to step 10.
   - The report states which of `timeout` or `gtimeout` was on PATH, and whether the two blocking cases passed or skipped.
5. Six one-off mutation checks, each reverted afterward. Three rules govern them, in the order they apply:
   - They start from a run of the two edited ledger test files with `-m "not timing"`, which passes on the unmutated code. A red baseline goes under **Still uncertain** in the return, and no mutation runs against it.
   - If the happy-path script case's `rev-parse --verify` of the pin fails in that run, row 89 is false. `code-writer` then stops, adopts no fallback such as a wrapper commit or a tag (M14 sets both aside), and reports to the engineer.
   - Each check records its named case's result in that run and after its edit, so a fail-after reads as the edit's effect only after a pass-before.

   The checks:
   - Removing `.reviewed_head, .reviewed_tree` from the retry check's `del(...)` makes `test_a_retirer_differing_only_in_a_per_attempt_field_is_a_retry_that_adds_no_row` fail for both fields.
   - Adding `reviewed_tree` to the dedup key at `review-ledger.sh:654` makes `test_a_row_differing_only_in_a_per_attempt_field_dedups[reviewed_tree]` fail.
   - Pointing the tree capture at the live index, with no copy, makes the held-lock case or the no-lock assertions fail. If both still pass, row 21 is false. Record that result in the PR body and keep the copy, which costs little either way (M3). After the revert, a `Grep` finds the index copy and `write-tree` called with the copy as its index in `_review-ledger-lib.sh`, and the return quotes the matching lines.
   - Running `write-tree` even when `cp` failed makes the failing-`cp` case fail whichever way row 53 goes, so read the failing tree value:
     - A tree equal to the id `git hash-object -t tree /dev/null` prints confirms row 53.
     - An empty tree with a second note naming `write-tree` refutes it.
     - The PR body records which, and the guard stays either way.
   - Removing `GIT_DIR` from `_review_ledger_repo_git`'s unset list makes the foreign-environment head case's `GIT_DIR` parameter fail, and the `confirm_ignored` case's `GIT_DIR` parameter at `test_review_ledger_lib.py:2700` too (M11).
   - Removing `--missing-ok` makes the absent-blob case fail with an empty tree and a `write-tree` note, and the PR body records it (row 60). The case's own premises already make a pass under this mutation impossible, so the check confirms the case's wiring once.
6. The split-index case passes with `-c core.splitIndex=false` on the capture's `write-tree`. If it fails with the flag in place, stop and report to the engineer (row 49). One more one-off mutation removes the flag, re-runs the case, and is reverted:
   - A failure means a rewrite of the split copy wrote a shared index, so the flag is load-bearing on this path.
   - A pass means `write-tree` wrote none here (rows 21 and 49).
   - The PR body records which, and the flag stays either way (M3).
   - After the revert, a `Grep` for `core.splitIndex=false` in `_review-ledger-lib.sh` finds it on the `write-tree` call, and the return quotes the matching line.
7. A one-off check of the docs' comparison rule and pin commands (Critical files item 5), as a throwaway case named `test_scratch_comparison_rule` in `test_review_ledger_script.py`:
   - The case takes the `isolated_home` and `git_repo` fixtures, calls `_seed_session(isolated_home, SID)`, and runs every append and `show` through `_run`, as the file's existing append cases do (row 86). Its repository and rows live under the test's temporary directory, so no row reaches a real ledger and no pin reaches a real repository.
   - It checks out `feature`, as the case at `:390` does, and commits a tracked text file of at least twenty lines at the repository root.
   - Round 1 appends one ADDRESS row citing that file. Then, in that file, stage a one-line change, a two-line change and a one-line deletion, each at least two unchanged lines from the others.
   - Round 2 appends one ADDRESS row for each of four locations: the changed line, a line of the two-line change, an untouched line, and the line before the deletion.
   - Apply the rule literally, in this order:
     1. Find the branch file from `show`'s stderr header.
     2. Read it one line at a time with `jq`.
     3. Form and pair the rounds, and take each round's state.
     4. Run `git cat-file -e <id>^{tree}` on both trees.
     5. Run `git diff -U0 <round 1 tree> <round 2 tree>` for the file.
   - The case prints the three hunk headers and each location's reading, with no object id. It asserts:
     - Both trees resolve.
     - The headers read `+c`, `+c,2` and `+c,0` as the docs state.
     - The two changed locations read inside.
     - The untouched line and the line before the deletion read outside.
   - The case then runs the docs' pin commands as shipped text: the measuring command, the audit command, the audit's hand pin and the month deletion (rows 96, 103 and 109). Its rules:
     - It copies each command's text verbatim from `docs/scripts.md` into the case, and replaces each placeholder the command carries (`FILE`, `<YYYY-MM>` or `<tree id>`) with the scratch value. The hand pin takes its `<YYYY-MM>` and `<tree id>` from each `unpinned:` line.
     - Every command, and each of the case's own `git` calls below, runs with its working directory set to the scratch repository and an environment without `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE` and `GIT_COMMON_DIR`.
     - A docs command runs as `bash -c` on its text.
     - Before the first docs command, the case asserts that `git rev-parse --show-toplevel`, run that way, prints the scratch repository's resolved path.
     - It prints each copied command with no placeholder replaced. Beyond those, it prints only counts and byte figures, never an object id or a ref name.
   - The pin-command checks, in execution order:
     1. Round 1's tree equals `HEAD^{tree}`, and both rows' `event_time` values share a month. A run that straddles a month boundary is repeated.
     2. The measuring command prints exactly a `tree` line and a `blob` line, each with a count of 1 and a positive byte figure, for round 2's tree and its changed file.
     3. The audit command, given the branch file, prints nothing.
     4. `git gc --quiet` runs. Then the measuring command again prints exactly a `tree` line and a `blob` line, each with a count of 1 and a positive byte figure. The case prints both runs' byte figures.
     5. When `<git dir>/reftable` is absent, `<git dir>/packed-refs` holds both pins' ref names, so `git gc` ran `git pack-refs` (row 103).
     6. The case deletes round 2's ref with its own `git update-ref -d`, which stands in for a failed pin. Then:
        - The audit command prints exactly one line, because round 1's pin keeps the month.
        - The measuring command prints nothing.
     7. The audit's hand pin runs once per `unpinned:` line. Then the audit command prints nothing.
     8. The month deletion runs for the rows' month. Then:
        - `git for-each-ref refs/review-ledger/` lists nothing.
        - The audit command prints nothing, so a month deleted on purpose never reads as failed pins (M14).
   - `code-writer` runs `scripts/venv-run.sh pytest <the case's node id> -n0 -s`, because `-n auto` is the repo default and `-s` output needs `-n0`. The return quotes the printed output, each printed command included.
   - If git prints anything the docs do not state, or a pin command gives other readings than these, correct the docs text to what git printed or to a command that gives these readings. Re-run the case on the corrected text before deleting it, and quote the re-run's output in the return.
   - `code-writer` also runs the pin class's real-objects case five times back to back with `-n0`, and the return reports each run's result.
   - The case is deleted before step 8. A `Grep` for `test_scratch_comparison_rule` in the file then finds nothing, and the return says so. `git status --porcelain` cannot show a leftover case, because the file carries other edits.
   - Once the return lands, the dispatching session itself does three things, without relying on the return's statements:
     1. It runs a `Grep` for `test_scratch_comparison_rule`.
     2. It reads the `write-tree` call in `_review_ledger_reviewed_tree_in` and confirms that one line passes the copy as the index, `-c core.splitIndex=false` and `--missing-ok`, because a dropped copy or flag can leave every test green (steps 5 and 6).
     3. It retags row 67 to cite this step, or rewrites it to what git printed.
8. Once the dispatch returns, after the last edit or revert from steps 5 to 7, the dispatching session runs the commands of steps 1 to 4 (row 86). Steps 1 to 4 and step 12 report this run. If a later fix edits a Critical file, the run repeats after it. After each run, `git for-each-ref refs/review-ledger/` in this checkout prints nothing, so no selected test pinned a tree in the real repository (row 100).
   - A ref it prints means a test leaked a pin. Find the case, move its appends into a temporary repository, and delete each listed ref with `git update-ref -d <ref>` before the commit.
   - The check covers only the tests `select-tests.py` selected. CI runs no such check, so a leak from an unselected test stays unseen until a later local run selects it.
   - Each run also compares every command step 7's return quotes with the same command in the `docs/scripts.md` about to be committed, byte for byte. Two rules follow, in the order they apply:
     - A fix to a Critical file repeats the run, so the comparison also covers an edit from step 11's `/code-review` or skill review.
     - A command that differs goes back to `code-writer` for a step 7 re-run on the changed text before the commit, and the comparison then uses that run's quotes.
9. After staging and before the commit, `git diff --cached --name-only` lists exactly the ten Critical files, plus this plan file when the session stages it. `git status --porcelain` lists no other path except this plan file while it is untracked.
10. If a selected test fails, reproduce it at the merge-base before treating it as in scope, per `CLAUDE.md` § "Engineering Judgment".
11. `/code-review` runs on the staged diff before the commit.
    - It also runs `skill-management:skill-review` on this diff, because the diff edits `claude-skills/skills/code-review/SKILL.md`.
    - `plugins/skill-management/hooks/require-skill-review.sh` blocks the commit until that review's marker is written (`.claude/rules/review-pipeline-dispatch.md`).
    - The skill review also confirms that no sentence in the skill sequences a fix dispatch before the round's appends, reading `:399`'s fix-plan table under the new clause (row 38).
    - No agent file changes, so `/agent-review` does not apply.
12. The PR body records:
    - Step 4's outcome in step 8's run: which of `timeout` or `gtimeout` was on PATH, and whether the two blocking cases passed or skipped ("cap not verified locally").
    - The outcomes of step 5's checks on rows 21, 53 and 60, and of step 6's flag check.
    - Step 7's hunk headers and readings, which settle row 67, with no object id.
    - A rollback note. After a revert, the earlier retry check compares a candidate without the two keys against schema-6 rows that have them. A retry of a retired-`--ref` write that spans the revert is then rejected as "no longer live". Pins already written stay until deleted, and the reverted code never reads them. A revert also removes the docs' month deletion, so the note carries the cleanup:
      - `git for-each-ref --format='delete %(refname)' refs/review-ledger/ | git update-ref --stdin` deletes every pin.
      - A manual `git gc` afterward frees what only the pins kept, on the timing Out of scope's tradeoff item states.
    - An upgrade note. A row appended before the upgrade within 166 bytes of the 4095-byte cap, retried after it, is rejected as over the cap instead of deduped.
    - Settled question 10's storage estimate with its assumptions, and the docs' measuring command, so the engineer can run it on real PRs after merge.
    - Out of scope's proposed trigger, marked as this plan's proposal for the engineer to adjust (row 102).
    - Step 7's measuring-command output from the scratch repository, before and after its `git gc`, which holds counts and bytes only.

## Out of scope

- Pinning `reviewed_head` (M14).
- Expiring pins automatically, or capping the storage they keep. The engineer deferred both with a trigger (row 101). The tradeoff, one fact per item:
  - The month bucket serves age-based expiry only. It cannot express "keep until the PR merges", so deleting a month drops the pins of PRs still open in it, and a PR that spans a month boundary loses the pairs that cross it.
  - That cost falls mainly on the current and previous months. The stale-file sweep deletes a ledger file idle past its window, so an older month's pins mostly back rows no live ledger file holds (M14's orphan pins). Deleting a month that holds no live row drops placement only for rows an exported copy keeps.
  - A size cap would have to evict whole months, oldest first, because no ref records a size.
  - Deleting pins frees nothing by itself. Space returns only at a later `git gc`, after `gc.pruneExpire` (two weeks by default) unless it runs with `--prune=now`, and for objects already packed only at a `git gc` that repacks every pack (row 103).
  - Every stow consumer's repository where `/code-review` runs takes the default growth, and the docs give each the measuring and audit commands and the month deletion.
  - The proposed trigger, which is this plan's proposal for the engineer to adjust (row 102):
    - The engineer measures this repository 30 days after merge, and again at 90 days if the first reading is under the figure. While readings stay under it, the engineer reads again every 90 days after that.
    - Each reading runs a manual `git gc`, then the docs' measuring command and `git count-objects -v`.
    - The pins' figure is the sum of the measuring command's byte column over every line, divided by 1,024, because `git count-objects -v` prints `size-pack` in KiB.
    - When that figure passes 10 percent of `size-pack`, the engineer decides between an expiry and a cap.
- A switch that stops new pins without a revert. Reverting this PR's capture and schema bump is the intended off-switch, and Verification step 12's rollback note states its cost.
- Pushing pins, or sharing them between clones. They are local refs, and M14's shared-across-worktrees item names the pushes that send them.
- Ledger file retention or sweeping, which another session owns. This plan assesses neither. M14's orphan-pin item names the sweep and the Reset deletion as sources of pins no row records, and the expiry tradeoff above gives the sweep as the reason older months hold few live rows.
- Any handling of a credential or other content a pin holds: no lookup of the pins that hold a file, no release of them, no confirmation check and no rotation guidance. The engineer ruled the concern out (row 116).
- A row field that records why a capture was empty, or whether its pin landed. The engineer selected "Two new row fields (Recommended)", so each cause goes to stderr only, and the docs' audit command finds a missing pin from the existing fields.
- A reftable pin case. The repo's git floor is 2.43 (row 107), and `git init --ref-format` is recalled as arriving in git 2.45 (row 108), so the case cannot run at the floor. Reftable is untested for the pin, as it is for the umask case's ref-file assertions.
- A case that pins the single `date` call. Away from a month boundary, no case can tell `${EVENT_TIME:0:7}` from a second `date -u +%Y-%m` call, so that property rests on the call site the Approach's Design gives and stays untested.
- A `review-ledger.sh place` script that applies the docs' placement rule. The engineer selected "No, follow-up (Recommended)" (row 70), so it is a named follow-up. That follow-up could also count equal-tree pairs. That count is the late rounds followed by an on-time round plus the fixes that left the index unchanged. It is not a bound on the late-append rate. A session late in every round leaves no equal pair.
- A row field that marks a `--relog` row. The docs count re-log rows per row instead (row 74), and a third field would go beyond the "Two new row fields (Recommended)" the engineer selected (row 3).
- Recording which finding's suggested fix wrote each line, and the path form of citations in findings files (Context, "Not covered").
- Enforcing the append-before-fix order with a hook or a script check. M13 gives why each fails, and the docs name every outcome of a late append instead.
- Surfacing reviewed state in `render`, `show --trend` or the session dashboard.
- Merging the three 40-or-64-digit object-id checks (`_lib.sh:1383`, `:1717` and the new lib predicate) into one `_lib.sh` helper, which would touch unrelated `_lib.sh` call sites.
- Giving the dedup key one source of truth instead of the allow-list at `review-ledger.sh:654` and the deny-list at `_review-ledger-lib.sh:728`. The field-table and per-attempt tests keep the two in step. The deferral's cost is that every additive row field must join the deny-list, and a version without that entry rejects retries against rows that carry the field (Verification step 12's rollback note).
- Isolating the hooks-test `git_repo` fixture from host git config (row 15).
- Telling an unborn branch from a repository git cannot read when the HEAD look-up fails. The look-up only reads, and the docs say a silent empty `reviewed_head` covers both (M2).
- A linked-worktree split-index case. Split index is opt-in, the main-worktree case pins row 49, and a shared index the copy cannot find prints the note instead of a silent empty (row 59).
- A split-index case with `core.splitIndex` on and the index not yet split. The `write-tree` flag covers that shape too if row 80 holds (M3).
- Splitting `test_review_ledger_script.py` and `test_review_ledger_lib.py`, both already past the 1,000-line limit that nothing enforces yet (row 35).
