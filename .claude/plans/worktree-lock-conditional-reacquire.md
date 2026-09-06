# Worktree-lock fast path: stop reacquiring on reads against an absent lock

## Context

`require-worktree-for-git-writes.sh`'s fast path currently calls the
lock-acquiring collision guard unconditionally for every non-relocating
command reaching it — including read-only commands like `git status` —
whenever the worktree's `git worktree lock` is currently absent. PR #757
(merged) added an `additionalContext` message explaining this
reacquisition after the fact, but did not change the underlying
behavior: a read against a freshly-unlocked worktree still silently
re-locks it as a side effect, and a compound read-only chain (`git
status && git log`) gets re-locked even though neither record is a
write. The read-only allowlist in the slow path (`ALLOWED_RE`) already
allows reads through with *no* guard call at all, so the fast path's
guard call on a read produces only the acquisition side effect — it is
not load-bearing for the allow/deny outcome.

The engineer asked (mid-review of PR #757) not to over-optimize for
preserving existing test/behavior at the expense of considering whether
different behavior is actually more correct. This plan's goal: make the
fast path only acquire the lock when acquisition is actually necessary
(i.e., not for a read against an absent lock), routing the absent-lock
case through the same full-parsing fallthrough already used for
relocation-ambiguous commands, so reads never have a lock-acquisition
side effect regardless of which path evaluates them.

## Approach

Gate the fast path's collision-guard call on the lock already being present. When `<worktree-git-dir>/locked` is absent, skip the guard entirely and fall through to the existing full-parsing path, which allows reads with no guard call at all and re-runs the guard only for an actual write record. The result: acquisition becomes a write-only side effect on both paths, and no new acquisition logic is added anywhere — the absent-lock case is routed into machinery that already exists for relocation-ambiguous commands.

The concrete edit at `claude/.claude/hooks/require-worktree-for-git-writes.sh:237-244` replaces the `WAS_UNLOCKED` pre-check-plus-message block with a single branch condition:

```bash
    if ! _lib_worktree_lock_absent "$SESSION_GIT_DIR_ABS"; then
      _lib_worktree_collision_guard "$CWD" "$REPO_GIT_COMMON_DIR" >/dev/null && exit 0
    fi
```

Lock present → unchanged behavior (guard runs; self-recognition allows, foreign lock denies and falls through). Lock absent → no guard call, fall through. The fast path's `_lib_emit_allow_with_context` block and its `WAS_UNLOCKED` variable become dead and are deleted: with the guard reachable only when the lock is present, a fast-path allow can no longer be a deliberate fresh acquisition. The slow path's own `WAS_UNLOCKED` (line 405) is a separate, still-live variable — do not delete that one.

### Assumption ledger

**Root problem.** A read-only git command evaluated by the fast path acquires the worktree's exclusive lock as a side effect, because the guard call is placed before anything has established whether the command is a read or a write — and the guard cannot make that distinction itself.

**Givens** (fixed beyond this design's reach):

- **G1 — The fast path cannot classify read vs. write without `parse-git-command.py`.** Dissolving this would mean a second read/write classifier written in bash, which is the quote/heredoc misparse class the parser exists to eliminate (`require-worktree-for-git-writes.sh:26-29`: "tokenizes the raw command with the stdlib shlex module — quote- and heredoc-aware, unlike a regex/sed split on the raw string"). Introducing a competing classifier is a decision outside this plan.
- **G2 — A lock cannot be conditionally released after acquisition.** `git worktree unlock` has no ownership check (`_lib.sh:999-1002`, verified empirically per that comment), so an "acquire now, release if it turns out to be a read" design would be racy against a second evictor. The tool imposes this.
- **G3 — python3 is already a hard precondition of this hook.** `require-worktree-for-git-writes.sh:41-48` states it, and the parser path denies without it. This plan does not re-litigate that posture; it only narrows the carve-out the deny message advertises (row 8).

**Mechanisms:**

- **M1 — Branch on `! _lib_worktree_lock_absent "$SESSION_GIT_DIR_ABS"`.** *anchors: root.* The helper already exists and is already called at this exact point for `WAS_UNLOCKED`; promoting it from a message pre-check to the branch condition adds no plumbing and no new lock-state reader.
- **M2 — Fall through to full parsing on an absent lock rather than adding a new decision branch.** *anchors: row 1.* The slow path's write branch is already correct for a non-relocating write, so the absent-lock case needs routing, not new logic.
- **M3 — Delete the fast path's fresh-acquire `additionalContext` block.** *anchors: row 9.* A fast-path fresh acquisition survives only inside the documented TOCTOU race, which is already recorded as a silent-acquisition known gap.

**Over-powered-primitive check.** M2 spends a `python3` spawn on a path that previously spawned none. Two lighter primitives were examined and both fail:

1. *A bash-side "is this definitely a read?" pre-filter in the fast path* — would keep the absent-lock read case subprocess-free, but creates a second read/write classifier that can drift from `_lib_readonly_git_subcmds` (`_lib.sh:1474`) and reintroduces the regex-on-raw-string misparse class (G1). Rejected on single-source-of-truth grounds.
2. *A non-acquiring "peek" mode on `_lib_worktree_collision_guard`* — would let the fast path check contention without claiming the worktree, but the fast path still could not tell a read from a write, so a write would then be allowed without ever acquiring, dropping the single-writer invariant. The missing input is the read/write classification, not finer lock-state granularity; `_lib_worktree_lock_absent` already supplies everything peek would add. Rejected as strictly more surface for no gain.

**Assumption rows:**

1. **The slow path's write branch needs no conditional to be correct for non-relocating writes.** `[verified: require-worktree-for-git-writes.sh:365-415]` — `effective_cwd` and `eff_git_dir` are derived per record from `running_cwd`/`c_status`, with no assumption that relocation occurred. For a non-relocating record, `effective_cwd == $CWD` and `eff_git_dir == $SESSION_GIT_DIR_ABS` — the exact pair the fast path passes to the guard today. This resolves the plan's crux question: reach it via fallthrough unchanged.
2. **The observable message for a non-relocating write against an absent lock changes form, not substance.** `[verified: lines 242 vs. 412]` — the fast path's "Running this git command re-acquired the worktree lock for `<cwd>`" is replaced by the slow path's "Running 'git `<subcmd>`' re-acquired the worktree lock for `<effective_cwd>`", which names the subcommand and is strictly more specific. No existing test asserts either text verbatim.
3. **Exactly one existing test needs its assertions inverted.** `[verified: independent re-derivation of all 14 tests in TestWorktreeCollisionGuard, test_require_worktree_for_git_writes.py:1031-1343]` — `test_read_in_freshly_unlocked_worktree_still_acquires_lock` (1165). The five tests using a main-tree `cwd` never enter the fast path; the four present-lock tests keep an identical call sequence; `test_base_case_acquires_lock_and_allows` and `test_self_lock_reentry_*` already produce the same decision and context shape via the slow path.
4. **The two foreign-lock read tests are genuinely unaffected.** `[verified: lines 1107-1153 and 1155-1163]` — both call `_lock_worktree` before invoking the hook, so `_lib_worktree_lock_absent` returns false and the fast-path guard call is retained. The `worktree list --porcelain` count of 2 in `test_foreign_live_lock_still_allows_read_via_fast_path` is unchanged. (Independently re-derived, not taken from the discovery correction.)
5. **The acquisition was never load-bearing for the read invariant.** `[verified: header lines 97-99 "Reads stay an unconditional allow regardless of collision state"; slow path lines 346-351]` — the read allowlist `continue`s before any guard call, so a read's allow verdict never depended on the lock.
6. **A session doing real work still claims its worktree without any git write.** `[verified: require-worktree-for-file-writes.sh:155-166]` — the first `Edit`/`Write`/`MultiEdit` into a linked worktree runs the same guard and acquires. So dropping read-triggered acquisition does not leave an actively-working session unclaimed.
7. **The contention tiebreak moves from first-reader to first-writer.** `[verified: derived from rows 5 and 6]` — today a read-only session can claim a worktree and deny a later writer; afterwards the first writer (git write or file write) claims it. This is the correct direction for a guard whose stated purpose is preventing two sessions from writing to one worktree.
8. **The python3-missing carve-out narrows, and its deny message must be reworded.** `[verified: lines 258-259]` — the message currently offers "run this git operation from inside a linked worktree with no active lock contention, where the fast path above does not require python3." After the change the fast path only exits without python3 when the lock is present *and* self-held; an absent lock on a python3-less machine denies every git command, including reads. Given G3 this is accepted, but the message must name the narrower condition rather than mislead. This includes a bootstrap boundary case `[verified: staff-platform-engineer, /plan-review round 1]`: since a worktree's first-ever lock acquisition can now only happen through the parser (the fast path's guard call is reached only once a lock already exists), a python3-less machine can no longer complete a first git operation — read or write — against a freshly created worktree through the fast path at all, where before this change a plain `git status` or `git commit` with no `cd`/`-C` worked python3-free end-to-end.
9. **A fast-path fresh acquisition remains possible only inside the existing TOCTOU race.** `[verified: header lines 113-119]` — lock present at the branch check, cleared by a concurrent `git worktree unlock` before the guard's own read. That bullet already documents this as a silent acquisition; after the change it describes the fast path's *only* acquisition route and must be reworded to say so.
10. **A new deny class becomes reachable: a `||`- or `&`-chained git write from a worktree with an absent lock.** `[verified: line 360 deny condition; parse-git-command.py:24 "the operator immediately before this segment"]` — e.g. `git fetch || git commit -m x` is allowed today by the fast path (which excludes only `cd`, `-C`, `(`, and backtick) and will deny after the change until the lock is acquired. The shape appears in no agent-facing recipe in this repo: a repo-wide grep for `\|\|\s*git\s+[a-z]` and `&\s+git\s+[a-z]` returns only `&&`-chains (the `&`-arm also matches the tail of `&&`), two read-only `git cat-file -e ... || git cat-file -e ...` lines inside plugin hook scripts, and hook-internal shell — never a Bash-tool recipe. The deny names its own remedy, and the window self-heals on the first lock acquisition from any source (row 6). Accepted, documented as a known gap, and pinned by a test; the alternative fix is recorded in **Out of scope**.
11. **Steady-state cost is unchanged for a lock-present worktree; the absent-lock fallthrough recurs for the life of a read-only session, and replaces a more expensive call graph, not a cheaper one.** `[verified: lines 232-251; staff-platform-engineer subprocess-graph trace against _lib.sh's _lib_worktree_collision_guard and _lib_resolve_claude_pid, /plan-review round 1]` — with the lock present the call sequence is byte-for-byte what it is today. With the lock absent, the `python3` parser spawn recurs on every fast-path-eligible command for as long as the lock stays absent — the full duration of a read-only session, not just its first command. It still replaces a strictly more expensive path: today's unconditional guard call against an absent lock spawns roughly 8-10 processes (three `git` calls, a `ps`-per-ancestor-PID walk, an O_EXCL acquisition attempt, and a post-write porcelain re-read, each `_lib_capped`-doubled), versus the parser's two forks and no I/O after this change.
12. **The engineer directed that correctness take precedence over preserving current behavior and tests.** `[engineer-verified]` — stated mid-review of PR #757. Rows 7 and 10 are behavior changes made under that direction.

**Dispatch split.** One `code-writer` dispatch. The hook edit, its test inversions, and the new tests are a single non-separable unit — splitting them would force both dispatches to restate the same fast-path/slow-path shared-state background, which `plan-it` Step 5 names as the do-not-split condition. Doc updates ride along in the same dispatch.

## Critical files

**`claude/.claude/hooks/require-worktree-for-git-writes.sh`** (all edits in one file)

- **232-251 — the core change.** Replace lines 237-243 (the `WAS_UNLOCKED` pre-check, guard call, and `_lib_emit_allow_with_context` block) with the single `if ! _lib_worktree_lock_absent "$SESSION_GIT_DIR_ABS"; then` branch shown in **Approach**. Keep the guard call and its `>/dev/null` inside that branch, and keep the existing fallthrough. Do not touch the slow path's own `WAS_UNLOCKED` at line 405.
- **222-231 — fast-path preamble comment.** Add the absent-lock condition to the existing relocation-over-approximation explanation. Per `.claude/rules/shell-script-conventions.md` and CLAUDE.md's split-multi-fact-comments rule, state it as separate sentences: one for "the guard is called only when the lock is already present," one for "an absent lock falls through to full parsing, where a read is allowed with no guard call."
- **246-249 — fallthrough comment.** Currently scoped to "Collision guard denied." Widen it to cover both fallthrough triggers (guard denial, and the skipped guard on an absent lock).
- **13-17 — motivation paragraph.** "A **write** that resolves into a linked worktree also passes through `_lib_worktree_collision_guard`" is inaccurate today and still imprecise after the change — a read against a *present* lock also passes through it on the fast path. Reword so the sentence describes what passes through the guard, and add a separate sentence stating that only a write can cause an acquisition.
- **258-259 — python3 deny message.** Reword the trailing carve-out per row 8: the fast path stays python3-free only for a worktree whose lock this session already holds, not merely one with "no active lock contention."
- **55-139 — Known gaps list.** Five edits: (a) bullet 100-104 (fast-path denial falls through) gains the absent-lock fallthrough and its parser-spawn cost, per row 11's corrected framing — the parser spawn recurs for the duration of a read-only session against an absent lock, not a one-off, and it replaces a strictly more expensive call graph (the guard's own ~8-10-process acquisition attempt) rather than adding a new cost on top of a cheaper path; (b) bullet 105-112 rescoped — the pre-check now gates whether the guard runs on the fast path, and still backs the note on the slow path; (c) bullet 113-119 reworded per row 9 to drop the fast-path `WAS_UNLOCKED` reference and state that this race is now the fast path's only acquisition route; (d) a **new bullet** for row 10's `||`/`&` over-deny, naming that it is reachable only while the lock is absent and self-heals on the first acquisition; (e) a **new bullet** for row 8's bootstrap boundary case — a python3-less machine can no longer complete any first git operation, read or write, against a freshly created worktree through the fast path. Bullet 120-125 (same-session double-message) should be rescoped to the slow path and the file-writes hook, since the fast path no longer emits a message.

**`claude/.claude/hooks/tests/test_require_worktree_for_git_writes.py`**

- **1165-1187 — invert.** Rename to `test_read_in_freshly_unlocked_worktree_does_not_reacquire_lock`. New assertions: `run_hook_context(...) is None`, `run_hook(...) == "allow"` (pinned explicitly — `run_hook_context` alone cannot distinguish a silent allow from a deny, the same reason given at lines 1242-1244), and `_worktree_lock_reason(worktree) is None` afterwards. Rewrite the docstring to state the invariant positively: a read never acquires, on either path.
- **1107-1118 — optional one-line docstring clarification.** Its rationale ("the rejected simpler design — drop the guard from the fast path altogether") is now only rejected for the lock-present case; one clause noting that the retained fast-path guard call is specifically the present-lock case keeps it from reading as a blanket claim. Assertions unchanged.
- **New tests, appended to `TestWorktreeCollisionGuard`** (all with `cwd=worktree`, `opted_in_with_worktree`, asserting `_worktree_lock_reason(worktree) is None` as a fixture precondition):
  1. `test_read_only_chain_in_unlocked_worktree_does_not_acquire` — `git status && git log`. Asserts allow, no `additionalContext`, and the worktree still unlocked. Covers the compound-chain case the fast path's exclusion list (`cd`, `-C`, `(`, backtick) does not catch.
  2. `test_read_then_write_chain_in_unlocked_worktree_acquires_once_with_context` — `git status && git commit -m x`. Asserts allow, an `additionalContext` naming the worktree and `git worktree unlock`, and the worktree locked afterwards. Proves the write record still reaches the slow path's acquire branch (line 407) through the fallthrough.
  3. `test_or_chained_write_in_unlocked_worktree_denies_until_lock_held` — pins row 10 deliberately: from a worktree with an absent lock, `git fetch || git commit -m x` denies; after a plain `git commit -m foo` has acquired the lock, the same command allows via the fast path. Docstring must name this as the documented asymmetry from the header's known-gaps bullet, not an accident.

**`claude/.claude/hooks/tests/conftest.py`**

- **238-243 — `opted_in_with_worktree` docstring.** "`_lib_worktree_collision_guard` (which every allow-into-a-worktree path now runs through)" stops being true: a read into an unlocked worktree runs through no guard. The session seeding is still required for the write paths — correct the parenthetical, keep the seeding.

**`docs/hooks.md`**

- **Line 20 — `require-worktree-for-git-writes.sh` entry.** Same two corrections as the hook header (13-17 and the note's origin): state what passes through the guard, state that only a write acquires, and attribute the fresh-acquire `additionalContext` note to the full-parsing path. Add one clause that a read never acquires the lock regardless of path.
- **Line 21 — `require-worktree-for-file-writes.sh` entry.** "with the same fresh-acquire `additionalContext` note on the same pre-check basis" stays factually true (that hook is write-only). Re-read it after the line-20 rewrite and adjust only if "the same" now points at something that no longer says what it did.

**`docs/design-decisions.md`**

- **New `## 32.` entry**, following the shape of `## 29. Worktree-lock self-recognition keyed on session_id, not PID (2026-08-22)` — which is precedent for a worktree-lock decision entry citing a plan file as its source. Record why a read does not acquire, the first-reader→first-writer tiebreak shift (row 7), the accepted `||`/`&` tradeoff (row 10), and row 8's python3-less bootstrap boundary case (a python3-less machine can no longer complete a first git operation against a freshly created worktree through the fast path at all). This is the durable home for the *why*; the hook header carries only the one-line facts, per CLAUDE.md's comment rules.

**Reuse opportunities** (nothing new is introduced):

- `_lib_worktree_lock_absent` (`_lib.sh:966`) — promoted from message pre-check to branch condition. No new lock-state reader, and its documented contract ("Returns 0 (true) iff `WORKTREE_GIT_DIR/locked` does not exist yet") is exactly the predicate needed.
- The slow path's existing write branch (`require-worktree-for-git-writes.sh:401-415`) — used as-is per row 1; no acquisition logic is duplicated into the fast path.
- `_lib_readonly_git_subcmds` / `ALLOWED_RE` (`_lib.sh:1474`) — remains the single read/write classifier; no second one is added.
- Test fixtures `opted_in_with_worktree`, `_worktree_lock_reason`, `_lock_worktree`, and the `run_hook` / `run_hook_context` / `run_hook_reason` helpers — every new test is expressible with what already exists.

**Not touched:** `_lib.sh` (the guard and the pre-check helper are unchanged), `parse-git-command.py`, `require-worktree-for-file-writes.sh`, `README.md` (its Worktree-enforcement section at line 261 describes denials and the lock mechanism, not read-triggered acquisition, so it stays accurate).

## Verification

**Scoped suite** (per repo `CLAUDE.md` — agents run `select-tests.py`, not the full suite; CI runs the full suite on push):

```bash
.venv/bin/python3 claude/.claude/scripts/select-tests.py
```

It derives changed paths from git itself, so a hook change plus doc changes selects the hooks test directory without further arguments.

**Focused loop while iterating:**

```bash
.venv/bin/pytest claude/.claude/hooks/tests/test_require_worktree_for_git_writes.py -k TestWorktreeCollisionGuard
```

**Lint** (both required — the change touches shell only, but `ruff` covers the test file):

```bash
.venv/bin/ruff check claude/.claude/
scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck
```

**Behavioral cases the suite must cover after the change.** Every row is `cwd` = the linked worktree unless stated:

| Command | Lock state | Expected decision | Expected lock after | Expected `additionalContext` |
|---|---|---|---|---|
| `git status` | absent | allow | still absent | none |
| `git status && git log` | absent | allow | still absent | none |
| `git commit -m x` | absent | allow | acquired | names the worktree and `git worktree unlock` |
| `git status && git commit -m x` | absent | allow | acquired | names the worktree and `git worktree unlock` |
| `git fetch \|\| git commit -m x` | absent | **deny** (row 10) | still absent | n/a |
| `git fetch \|\| git commit -m x` | self-held | allow | unchanged | none |
| `git commit -m x` | self-held | allow | unchanged | none |
| `git status` | foreign live | allow (2 porcelain calls) | unchanged | none |
| `git commit -m x` | foreign live | deny naming the pid | unchanged | n/a |
| `cd <worktree> && git commit -m x` from main tree | absent | allow | acquired | names the worktree |

Rows 1-2 and 4-5 are the new tests; rows 3, 6-10 are existing coverage that must stay green unmodified — treat any failure among them as a regression in the fallthrough, not a test to update.

**Manual end-to-end sanity check** (in a scratch repo with `.claude/worktree-required` committed, from inside a linked worktree, run through the Bash tool so the hook actually fires):

1. `git worktree unlock <worktree>` from the interactive shell, then `git status` via the tool — expect a silent allow, and `git worktree list --porcelain` showing no `locked` line.
2. `git log --oneline -3` via the tool — still no `locked` line, confirming repeated reads never claim it.
3. `git commit --allow-empty -m probe` via the tool — expect the allow to carry the reacquisition note naming `git worktree unlock`, and `locked` present afterwards.
4. Repeat step 3 — expect a silent allow with no note (self-lock reentry).

**Post-change grep for stale prose:** search the repo for text asserting that any command, or a read, acquires the worktree lock — the hook header, `docs/hooks.md`, and `conftest.py`'s fixture docstring are the three known sites, and the grep guards against a fourth.

## Out of scope

- **`require-worktree-for-file-writes.sh`.** Its `case` at lines 69-72 admits only `Edit`/`Write`/`MultiEdit`, so every path reaching its sole guard call (line 159) is write-class by construction. No read/write ambiguity exists there and nothing about its fresh-acquire note changes.

- **Relaxing the slow path's `||`/`&` deny to eliminate row 10's over-deny.** The deny at line 360 exists because a preceding `cd` cannot be trusted across those operators — line 358 states the reason as "a backgrounded `cd` forks a subshell and never changes the parent shell's cwd either, so a write after `&` cannot trust whatever `running_cwd` currently holds." When a command contains *no* `CD` record at all and the record's `c_status` is `NONE`, `running_cwd` is still the session's own `$CWD`, so the stated reason does not apply and the deny is over-strict. The fix would be a `saw_cd` flag set when the first `CD` record is processed, with the `op` clause at line 360 applying only when `saw_cd` is true or `c_status != NONE`. It is deliberately deferred: it changes deny logic in the permissive direction on a security-relevant gate and deserves its own change, its own tests (main-tree `||` still denies; `cd <worktree> & git push` still denies; `cd /bad || git commit` still denies via the `resolvable` clause), and its own review — rather than riding along with a side-effect fix. Revisit it if the row-10 deny is observed in practice; the new known-gaps bullet and the pinning test are what will make that observable.

- **Any change to `_lib_worktree_collision_guard`'s acquisition mechanism**, including a non-acquiring "peek" mode (rejected in the Approach's over-powered-primitive check), lease/heartbeat semantics, or automatic eviction of a dead-PID lock. The header's existing known-gaps bullet at lines 83-86 already records why in-hook eviction is racy: `git worktree unlock` has no ownership check, so an evict-then-relock would race a second evictor.

- **The `_lib_worktree_lock_absent` TOCTOU window itself.** Both directions of that race are already documented (header lines 105-119) and this change only re-scopes which one applies to which path; closing it would need an atomic check-and-branch the filesystem does not offer at this granularity.

- **Making the fast path python3-free for reads against an absent lock.** That requires a second, bash-side read/write classifier — rejected in the Approach as a single-source-of-truth violation and a reintroduction of the misparse class `parse-git-command.py` exists to remove. The consequence is recorded as row 8, with the deny message reworded rather than the behavior worked around.
