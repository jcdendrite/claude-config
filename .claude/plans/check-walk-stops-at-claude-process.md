# Halt `--check`'s ancestor walk at the first claude process

## Context

**Goal:** `nudge-handoff-near-context-cap.sh --check` must refuse rather than
report a *parent* Claude session's context estimate when the session actually
asking has no `sessions/<pid>` lookup file of its own.

`--check` (shipped in PR #612) resolves its own `session_id` when invoked by
hand, because the harness supplies that field to registered hooks only. It
walks up to `CHECK_MAX_ANCESTOR_HOPS` (6) process ancestors looking for
`$CONFIG_DIR/sessions/<pid>` — a two-line file written at session start by
`capture-session-id.sh` — and stops at the first ancestor that has one.

The defect is that the walk stops at the first ancestor carrying an *entry*,
not the first ancestor that is *claude*. `capture-session-id.sh` fails open by
design (a fail-closed `SessionStart` hook would block session startup), so a
session's own entry can be absent. When that session is itself running
underneath another Claude Code session — a nested `claude` launched from a Bash
tool call — the walk climbs past its own claude process and finds the parent
session's entry. The parent's stored start time matches its own live process,
so the PID-reuse guard passes and `--check` returns `"status":"ok"` carrying
the wrong session's numbers. A confident number for the wrong session is the
single outcome `--check` exists to avoid; its contract makes refusing a
first-class result.

**Why now:** the limitation is currently documented in a way that points a
future reader at a fix that does not work (see Approach), so the wrong change
gets cheaper to make the longer this sits.

**Outcome.** Two populations change, and both are stated here because the
second is easy to miss:

1. *Nested session, own entry missing.* Today: `"status":"ok"` with the parent
   session's numbers. After: a refusal. This is the defect being fixed.
2. *Ordinary non-nested session, own entry missing.* Today: the walk climbs
   past the claude PID, finds nothing within the cap, and refuses with
   `session-id-unresolved`. After: the walk stops at the claude PID and refuses
   with the new `session-id-missing-at-claude`. **Both are refusals — no number
   changes — but the reason string does.** `session-id-unresolved` therefore
   narrows in meaning to "no claude ancestor within the hop cap," and its row
   in `docs/handoff-nudge.md` must say so.

## Approach

Keep the walk's existing per-hop entry check exactly as it is, and add one
terminating condition: after an iteration finds no entry at the current PID,
read that PID's process name in the same `ps` call that already reads its
parent PID, and refuse if the name is `claude`.

This is **inclusive-stop**: entries found at hops *below* the claude process
are still honored; the only new behavior is that the walk will not climb
*past* claude. That framing matters, and it is what makes the change small:

- An entry written under a PID below claude keeps resolving, because it belongs
  to the asking session's own subtree.
- A consumer whose CLI binary is not named `claude` is never newly refused —
  the walk simply does not stop early and behaves exactly as it does today.
- "No claude ancestor within the hop cap" needs no new code and no new reason:
  the loop exhausts and falls through to today's `session-id-unresolved`.
- Every existing `--check` test seeds its entry at the pytest PID (hop 1), so
  the walk breaks before a claude ancestor is ever reached. Exactly one existing
  test is affected.

**Alternatives set aside.**

- *Verify `ps -o comm=` on the PID where the entry was found.* This is what the
  current doc wording invites and it catches nothing: for an entry to exist,
  `capture-session-id.sh` wrote it while that PID was claude, and if the PID
  has since been reused the stored start time no longer matches, so `--check`
  already refuses with `session-id-stale-pid`. Same PID plus same start time is
  the same process. It also does not touch the nested case, where both
  processes genuinely are claude.
- *Exclusive-stop — refuse the moment the walk would leave the claude process,
  ignoring any entry found below it.* Same correctness on the nested case, but
  it discards a resolution path that works today for no benefit, and it would
  invalidate every `--check` test's hop-1 seeding.
- *A second `ps -o comm=` call per hop.* Doubles process spawns on a path whose
  latency is already pinned by a test; `ps -o ppid=,comm=` returns both fields
  in one call.
- *Read `/proc/<pid>/comm`.* Cheaper than `ps`, but Linux-only; this hook runs
  on macOS as well as ubuntu CI.
- *An env override naming the claude binary* (`HANDOFF_NUDGE_CLAUDE_COMM` or
  similar). Set aside by engineer decision: the no-override path degrades to
  today's behavior rather than to a refusal, so the knob would buy a documented
  env var and a test axis that nobody on a standard install needs.
- *Per-reason guidance in the two consuming skill bodies.* `plan-it` Step 7 and
  `handoff` both already point the reader at `docs/handoff-nudge.md` as the
  contract and instruct the agent to name the `reason`. Adding a sentence about
  this one reason to both bodies would duplicate the reason table into two more
  files and drift from it. Instead the new doc row carries an actionable gloss
  rather than mechanism alone, so the existing pointer is enough.

### Assumption ledger

**Root problem.** `--check` can bind a confident context estimate to a parent
Claude session because its ancestor walk terminates on entry presence rather
than on process identity.

**Givens** (conditions this design treats as fixed and does not attempt to
change):

- `capture-session-id.sh` is best-effort and can leave a session with no entry.
  *Reason:* it runs on `SessionStart`, where failing closed would block session
  startup — a harness-imposed constraint on hook failure posture, not a choice
  this plan can revisit from inside the hook.
- `ps -o comm=` rendering is OS-dependent (an exec path or a bare name, with a
  leading hyphen on login shells). *Reason:* the operating system owns the
  field's format.

**Mechanisms.**

1. *Merge `comm` into the existing per-hop `ps -o ppid=` call.* `anchors: root`
   — process identity is what the stop rule needs, and riding along with the
   parent-PID read keeps the walk at one process spawn per hop, unchanged.
2. *Refuse in-loop with a new ninth reason when the current PID's name resolves
   to `claude` and no entry was found there.* `anchors: root` — this is the
   behavior change; the reason value is what makes it diagnosable and testable
   as distinct from a broken walk.
3. *Match the bare, pathed, and hyphen-prefixed renderings as three `case`
   globs rather than calling `basename` or stripping a prefix first.*
   `anchors: row1` — required by the OS-dependent rendering given; the
   pure-glob form adds no process spawn and avoids the prefix-removal operator
   the repo's redaction hook rejects (A14).
4. *Simulate the claude ancestor in tests with a symlink to `bash` named
   `claude`.* `anchors: root` — the only simulation verified to report a
   `claude` basename; see A4/A5.
5. *Update the reason table and the stale limitation bullet in
   `docs/handoff-nudge.md`, and the condensed limitation bullet in the hook's
   own header.* `anchors: row2` — the reason vocabulary is a documented
   contract, and both places currently describe the pre-change behavior.

**Assumptions.**

- **A1** The live claude process reports bare `claude` for `ps -o comm=`.
  `[verified: ps ancestor probe run this session on this machine — macOS only]`.
  *Residual:* no Linux host was available to confirm this for a real installed
  `claude` CLI, and CI never runs the real binary (only the symlink
  simulation). If some install shape reports a wrapper or interpreter name
  instead, the walk simply does not stop early there — those users keep today's
  behavior rather than gaining a new failure mode. Record this in the rewritten
  `## Known limitations` bullet rather than implying A1 is OS-general.
- **A2** Login shells report a `-`-prefixed absolute path and other processes
  report absolute paths, so both a hyphen strip and a basename compare are
  load-bearing. `[verified: same probe — observed a `-`-prefixed login shell, an
  absolute-path `/usr/bin/login`, and a bare `claude` in one chain]`
- **A3** `ps -o ppid=,comm=` emits the two fields in the requested order on one
  line, so `read -r ancestor_ppid ancestor_comm` parses both and tolerates a
  name containing spaces. `[verified: same probe]`
- **A4** The obvious test simulation — a shebang script *named* `claude` — does
  **not** work on macOS: it reports the interpreter (`/bin/bash`), not the
  script name. A symlink to `bash` reports the symlink's own path.
  `[verified: same probe, both forms exercised]`
- **A5** On the ubuntu-24.04 CI runner, a symlink to `bash` named `claude`
  reports a `comm` whose basename is `claude`.
  `[unverified — no Linux host available this session]`. *Mitigation:* each new
  test has its fake-claude record its own `ps -o comm=` to a file, and the test
  asserts that basename is `claude` **before** asserting hook behavior, so a
  platform divergence fails loudly instead of silently testing nothing. Note the
  divergence runs the other way too: a shebang script named `claude` reports
  the script name on Linux, so the rejected simulation would have passed CI and
  failed locally.
- **A6** `capture-session-id.sh` writes the entry under the claude process's own
  PID, not under a shim below it. `[verified: capture-session-id.sh lines 73-84
  deliberately resolves `$CLAUDE_PID` past a one-hop shim before writing, and
  the live claude PID observed in the probe has a corresponding
  `sessions/<pid>` entry]`
- **A7** Neither the `plan-it` nor the `handoff` skill body enumerates reason
  values — both instruct the caller to "name the `reason`" and both point at
  `docs/handoff-nudge.md` as the contract — so adding a ninth value touches only
  that table. `[verified: grep across claude/.claude/skills/ and docs/]`
- **A8** Exactly one existing test is affected:
  `test_refuses_when_session_id_unresolved`, the only `--check` test with
  neither a seeded entry nor a wrapper chain, so its walk runs up the real
  process tree and would newly reach a `claude` ancestor when pytest is launched
  from inside a Claude Code session. Every other `--check` test either resolves
  at hop 1-2 via an entry (the `if [ -f "$entry" ]` break fires before the new
  `comm` read), exhausts the cap among `bash`-invoked wrappers, or exits before
  the loop starts (`test_refuses_on_unresolvable_config_dir`).
  `[verified: audit of all 21 tests in TestCheckMode]`
- **A9** A present-but-empty or malformed entry at any hop still refuses via the
  post-loop `[ -n "$session_id" ]` check with `session-id-unresolved`, because
  the loop breaks on entry *existence*, not validity; the new reason covers only
  "no entry file at the claude process."
  `[verified: read of the hook's walk and its post-loop guards]`
- **A10** CI runs `ubuntu-24.04`, so every new test must be portable.
  `[verified: .github/workflows/tests.yml]`
- **A11** The hook is `#!/bin/bash` with no `set -e`, so a bash herestring and a
  non-zero `read` are both safe here. `[verified: read of the hook header and a
  grep for set flags]`
- **A12** The new reason is a ninth value, `session-id-missing-at-claude`, and
  the process-name match is exact-basename with no env override.
  `[engineer-verified]`
- **A13** `_seed_session` writes `"<session_id>\n<lstart>\n"` where `<lstart>`
  comes from `_live_lstart`'s `TZ=UTC LC_ALL=C ps -o lstart=` with the trailing
  newline stripped — the same recipe `capture-session-id.sh` uses — so a shell
  equivalent inside a test fixture is exact, not approximate.
  `[verified: test file lines 195-224]`
- **A14** `deny-private-project-refs.sh`'s always-on Slack-channel-shape
  detector matches a `#` followed by lowercase/underscore/hyphen characters,
  which the two-character parameter-expansion prefix-removal operator
  satisfies. Any staged diff or commit message containing it is refused, so the
  leading-hyphen handling must be expressed as `case` globs instead.
  `[verified: the redaction gate blocked this plan's own first commit attempt on
  exactly that token; regex at claude/.claude/hooks/_lib.sh:1090]`
- **A15** The three-arm `case` form matches `claude`, `/usr/local/bin/claude`,
  `./claude`, `bin/claude`, and `-claude`, and does not match `bash`,
  `/usr/local/bin/bash`, a hyphen-prefixed login shell, `claude-code`,
  `/usr/local/bin/claude-code`, `node`, the empty string, or a value with
  embedded spaces — identical coverage to the strip-then-match form it
  replaces. `[verified: full match matrix executed, plus shellcheck run against
  a materialized copy of the patched hook under the repo-root .shellcheckrc]`

## Critical files

### `claude/.claude/hooks/nudge-handoff-near-context-cap.sh` (modify)

**The walk loop** inside `run_check_mode`. Add the three new names to the
existing `local` declaration line, then replace the bare parent-PID read at the
end of each iteration:

```bash
  local pid=$PPID hop=0 session_id="" stored_start="" entry
  local ps_line ancestor_ppid ancestor_comm
  ...
    # comm rides along with the parent PID so the walk stays at one ps per hop.
    ps_line=$(_lib_capped ps -o ppid=,comm= -p "$pid" 2>/dev/null)
    read -r ancestor_ppid ancestor_comm <<<"$ps_line"
    # A session missing its own entry must refuse rather than inherit its
    # parent session's, so the walk stops here instead of climbing past claude.
    # comm carries a leading hyphen on login shells and a path on most others.
    case "$ancestor_comm" in
      claude|*/claude|-claude) check_refuse "session-id-missing-at-claude" ;;
    esac
    pid=$ancestor_ppid
```

Match the renderings as `case` arms rather than stripping the leading hyphen
first with a parameter expansion; the strip-then-match form is blocked at
commit time (A14). Exactly these three arms — a fourth `-*/claude` arm is dead
code that `shellcheck` rejects with SC2222, because `*/claude`'s leading `*`
already matches a hyphen. `-claude` is *not* redundant: no other arm matches a
hyphen-prefixed bare name. Coverage is identical to the strip-then-match form
for every rendering in A2's set, including relative forms like `./claude`.

Declare the three new variables `local` explicitly as shown — `shellcheck`
under this repo's `.shellcheckrc` does **not** flag a missing `local` here, so
the repo's local-for-all-function-scoped-variables convention is the only thing
enforcing it.

Reuse, not reimplement: `_lib_capped` (already the wrapper on this call) and
`check_refuse` (already emits the `cannot-resolve` envelope and exits 0). The
start-time comparison immediately after the loop stays untouched — it is what
pins process identity — and the entry check at the top of each iteration is
unchanged. An empty `ps_line` (vanished PID, or `_lib_capped`'s `timeout`
firing) leaves both names empty, matches no `case` arm, and sets `pid=""`,
which the next iteration's `case "$pid" in ''|*[!0-9]*) break` catches — the
same fail-safe path as today.

**The header's condensed limitation bullet** (line ~53) currently reads that
`--check` "does not verify the resolved process is claude." After this change
the walk *does* perform a claude-identity check, so as worded it becomes
misleading in the same way the doc bullet already is. Rewrite it to say the
entry's own process identity is not re-verified (which stays true, and is the
deliberate choice recorded under Alternatives), keeping it to one sentence with
its existing pointer to `docs/handoff-nudge.md`.

### `claude/.claude/hooks/tests/test_nudge_handoff_near_context_cap.py` (modify)

Add a module-level helper next to `_wrapper_chain` (line 275) that builds the
fake-claude ancestor, and use it for four scenarios. The helper writes two
files: a bash script, and a symlink named `claude` pointing at the `bash` on
`PATH`. It is invoked as `subprocess.run([str(symlink), str(script), ...])`, so
the process running the script reports `comm` with basename `claude`. The
script must **not** `exec` the hook — the claude-named process has to survive as
the hook's ancestor — so the hook invocation is the script's last command,
matching `_wrapper_chain`'s existing forked-child note.

The script always records its own identity for the precondition assertion, and
conditionally writes its own `sessions/$$` entry using the exact recipe from
A13 (`capture-session-id.sh`'s own pinned form — this is why the entry is
written from shell rather than seeded from Python, which cannot know the PID
before the process exists):

```bash
ps -o comm= -p $$ > "$COMM_FILE"
printf '%s' "$$" > "$PID_FILE"
# only in the scenarios that need it:
printf '%s\n%s\n' "$SESSION_ID" "$(TZ=UTC LC_ALL=C ps -o lstart= -p $$)" \
  > "$CONFIG_DIR/sessions/$$"
```

Every one of the four tests asserts the simulation precondition first —
`Path(comm_file).read_text().strip().lstrip("-").rpartition("/")[2] == "claude"`
— before asserting anything about the hook, per A5.

1. **Claude ancestor has its own entry → resolves.** The fake claude writes its
   entry; the pytest PID gets none. Assert `status == "ok"`, `session_id ==
   SESSION_ID`, and that no `sessions/<pytest pid>` file exists. This is a
   no-regression test, not a test of the new branch: when the entry is present
   the `if [ -f "$entry" ]` break fires before the `comm` read, so the new
   `case` never executes. Say so in the docstring so a future reader does not
   mistake it for coverage of the stop rule.
2. **Claude ancestor has no entry, grandparent does → refuses.** The fake claude
   writes no entry; `_seed_session(config_dir, os.getpid())` seeds the pytest
   PID, and `_seed_transcript` makes that session resolvable. Assert `status ==
   "cannot-resolve"`, `reason == "session-id-missing-at-claude"`, `"estimate"
   not in payload`, and — the assertion that pins the defect —
   `payload.get("session_id") != SESSION_ID`. Without the fix this returns
   `"ok"` with the grandparent's session id. **This is the regression test.**
3. **Entry below the claude ancestor still resolves.** Chain pytest → fake
   claude (no entry) → one plain `bash` wrapper that writes its own entry → the
   hook. Assert `status == "ok"` and `session_id == SESSION_ID`. This pins the
   inclusive-stop design claim, which is otherwise only asserted in prose and
   is the sole behavioral difference from the rejected exclusive-stop
   alternative.
4. **Empty entry at the claude ancestor → `session-id-unresolved`, not the new
   reason.** The fake claude writes a zero-byte `sessions/$$`. Assert `reason ==
   "session-id-unresolved"`. This pins A9 at the one hop where the new code and
   the pre-existing malformed-entry path can interact;
   `test_refuses_malformed_sessions_entry` only covers it at the pytest PID.

**Delete `test_refuses_when_session_id_unresolved` (line 1039)** and move its
one unique assertion, `assert "estimate" not in payload`, into the
`one-past-cap` arm of `test_ancestor_walk_stops_at_the_hop_cap`. Rationale: the
only environment-independent way to keep it is to wrap it in
`CHECK_MAX_ANCESTOR_HOPS` bash wrappers so the cap is exhausted before any real
claude ancestor is reachable — at which point it exercises a code path
byte-identical to that existing parametrized arm (6 `bash`-comm hops, loop
exhausts, post-loop `session-id-unresolved`), and its docstring's claim of "no
entry anywhere" is no longer what it demonstrates. Extend that arm's docstring
to record that it is now the sole owner of `session-id-unresolved`.

`test_resolves_through_multi_hop_ancestor_walk` and the `at-cap` arm need no
change (A8) — confirm by running them, do not edit them.

### `docs/handoff-nudge.md` (modify)

- **Add** a reason-table row for `session-id-missing-at-claude`. Give it an
  actionable gloss, not mechanism alone, since this row is the only place the
  situation is explained and the skill bodies route the agent here rather than
  restating it: the walk reached the owning `claude` process and it has no
  `sessions/<pid>` entry — typically a nested Claude Code session, or one whose
  `SessionStart` capture did not run.
- **Narrow** the existing `session-id-unresolved` row. It currently reads "No
  `sessions/<pid>` entry anywhere in the ancestor walk"; after this change it
  fires only when no `claude` ancestor is reached within the hop cap either.
- **Rewrite** the `**`--check` trusts any `sessions/<pid>` entry it finds.**`
  bullet under `## Known limitations`. Its current text points a reader at the
  useless name-verification fix. Replace it with the residual that actually
  remains: the walk stops at the first `claude` ancestor, so a session whose own
  entry is missing refuses rather than inheriting a parent session's number;
  what remains is that a consumer whose CLI process reports some other name
  (a wrapper or interpreter rather than `claude`) gets no early stop and keeps
  the prior behavior — the A1 residual.
- **Update** the `## Querying the current estimate (--check)` prose, which
  currently says the walk "walks up to six process ancestors looking for the
  `sessions/<pid>` entry" — it now also stops at the owning claude.

## Verification

From inside the worktree (the contributor `.venv` lives only at the main
worktree root and is gitignored, so linked worktrees reach it three levels up):

```bash
../../../.venv/bin/pytest \
  claude/.claude/hooks/tests/test_nudge_handoff_near_context_cap.py \
  claude/.claude/hooks/tests/test_hook_alignment.py \
  claude/.claude/hooks/tests/test_doc_counts.py -q
../../../.venv/bin/ruff check claude/.claude/
scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck
```

Baseline on `main` for the pytest command above is 520 passed, 33 skipped;
`ruff` and `shellcheck` clean. Net test count after this change: +3 (four added,
one deleted).

**Negative control.** Before implementing, confirm new test 2 actually fails on
the unmodified hook — it must return `"status":"ok"` carrying the grandparent's
`SESSION_ID`. A regression test that passes before the fix is testing nothing.

**End-to-end manual check**, run from a Bash tool call inside a real session
(which has a resolvable entry): `~/.claude/hooks/nudge-handoff-near-context-cap.sh
--check` must still return `"status":"ok"`. This is the real-ancestor-chain
counterpart to the symlink simulation — the claude process sits at hop 3 from
the hook, well inside the cap.

`test_latency_under_500ms` in that file is jitter-sensitive on macOS and has
been observed failing on an unmodified checkout under full-suite load. A single
failure there is not evidence of a regression; re-run it in isolation before
investigating.

Then `claude-hook-review` (a hook changed) and `/code-review`. `/skill-review`
only if a `SKILL.md` lands in the diff — no skill body needs editing per A7 and
the Alternatives decision above, so it should not.

**Rollback** is a plain `git revert`. There is deliberately no runtime switch
for the new refusal: `.handoff-nudge-disabled` suppresses the fire path only and
`--check` documents that it does not honour it. This is acceptable because
`--check` is a manually-invoked diagnostic and the failure direction of an
over-refusal is "no number" rather than "wrong number" — the same posture the
mode already takes.

## Out of scope

- **Do not add a `comm == claude` check on the entry the walk already found.**
  It catches nothing (see Approach) and is the most likely wrong turn, because
  the current documentation wording suggests exactly that fix.
- **Do not remove or weaken the stored-start-time comparison.** It is what makes
  PID reuse detectable.
- **Do not change `CHECK_MAX_ANCESTOR_HOPS` (6).** Inclusive-stop means the cap
  needs no adjustment; it is bounded deliberately so a wedged process table
  becomes a fast refusal rather than a hang.
- **Do not add an env override for the claude binary name.** Engineer decision
  (A12); the no-override path degrades to current behavior, not to a refusal.
- **Do not add per-reason guidance to `plan-it` or `handoff`.** Decided under
  Alternatives: the doc row carries the gloss and both skill bodies already
  point at it, so restating it in two more files would duplicate the contract.
- **Do not retune `HANDOFF_NUDGE_ABS_CAP` (360000)** or touch the model→window
  table in the same hook.
- **Do not fix `test_latency_under_500ms`.** Its intermittent macOS failure is
  pre-existing and unrelated.
- **Do not build the nudge-to-handoff conversion report** against
  `transcript-analysis.py` — adjacent, but separate work.
- An unrelated uncommitted edit to `claude/.claude/hooks/_lib.sh` sits in the
  main working tree. It is not part of this branch and must not be picked up.
