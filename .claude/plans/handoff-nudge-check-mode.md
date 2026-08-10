# Read-only `--check` mode for the handoff-nudge hook

## Context

**Goal:** let a session at the plan→implementation boundary ask what its
current context estimate is and get a number, instead of inferring whether a
one-shot hook happened to fire.

`nudge-handoff-near-context-cap.sh` fires as a `UserPromptSubmit`/`Stop` hook,
sums the latest assistant turn's four token fields, resolves the record's model
to a context window, and emits a nudge when the total crosses
`min(40% of window, HANDOFF_NUDGE_ABS_CAP)`. It fires at most once per session.

`plan-it` Step 7 tells a session to hand off when that hook "has fired for this
session," then hedges with three caveats because the signal is unreliable as a
query: it fires once, it is globally disableable via
`~/.claude/.handoff-nudge-disabled`, and it can stay silent on an unrecognized
model ID or a schema-drifted transcript. The `handoff` skill's "Before writing:
is a handoff warranted?" section carries a shorter version of the same hedge.

This adds a read-only `--check` mode that prints the current estimate, the
computed threshold, the resolved model, and the resolved window, writing
nothing. Both skill bodies then call it and report a fact.

The hook cannot be queried today: its fire path writes a `FIRED_MARKER` (which
consumes the session's single nudge) and appends a `nudged` line to
`~/.claude/.handoff-nudge.log` (which `transcript-analysis.py handoff-ratio`
reads as evidence). `--check` must therefore be a distinct, side-effect-free
path, not a flag threaded through the fire path.

## Approach

`--check` becomes an early dispatch inside the same hook file, sharing the
model→window table, the `min(pct, cap)` arithmetic, and the usage-block read
with the fire path via extracted shell functions. It resolves its own session
by walking process ancestors to the `claude` PID, reading that PID's entry in
`$CONFIG_DIR/sessions/`, and globbing the transcript by session id. It emits
one JSON object on stdout and exits 0 on every path.

**Resolution, and why the brief's "hard part" mostly dissolves.** The brief
frames transcript resolution as the risky part, citing worktree-slug divergence
and multi-account roots. Both are sidestepped by keying on the session id
rather than deriving a project slug: transcripts live at
`$CONFIG_DIR/projects/<slug>/<session-id>.jsonl`, so
`$CONFIG_DIR/projects/*/<session-id>.jsonl` finds the file without the slug
ever being computed. Measured on this very session, which began in the main
checkout and entered a worktree partway through: the glob returns exactly one
match, and its project directory was the one slugged from the worktree path
(`<repo-slug>--claude-worktrees-<branch>`), not the main-checkout slug a
cwd-based derivation would have produced. Multi-account scope dissolves for the same reason: the session id
comes from `$CONFIG_DIR/sessions/` and the transcript sits under that same
`$CONFIG_DIR`, which is by construction the config dir the running session
uses, so searching sibling account roots could only ever surface a different
session.

**Refusing is a first-class outcome.** A `--check` that guesses wrong reports a
confident number for the wrong session — strictly worse than the prose it
replaces, because Step 7 would present it as computed fact. Every unresolved
condition returns `status: "cannot-resolve"` with a specific `reason`, never a
fallback estimate. `capture-session-id.sh` stores each PID's start time
alongside the session id, which makes PID reuse detectable rather than silent.

**Output contract** — one JSON object, exit 0 on every path:

```json
{"status":"ok","session_id":"…","estimate":412345,"threshold":360000,
 "over_threshold":true,"model":"claude-opus-5","context_window":1000000,
 "model_recognized":false,"already_fired":false,"nudge_disabled":false}
```

```json
{"status":"cannot-resolve","reason":"session-id-stale-pid"}
{"status":"schema-drift","session_id":"…"}
```

`reason` values: `config-dir-unresolved`, `session-id-unresolved` (no
`sessions/<pid>` entry anywhere in the ancestor walk), `session-id-stale-pid`
(stored start time disagrees with the live process — PID reuse),
`session-id-malformed`, `transcript-not-found`, `transcript-ambiguous` (glob
matched more than one file), `usage-block-missing`, `jq-unavailable`.

`session_id` is the one emitted field with no consumer in the skill bodies —
it is there for an engineer diagnosing a wrong-session or `cannot-resolve`
report by hand, which is the only way to tell which session `--check` actually
bound to. The drafted Step 7 wording below tells the agent not to quote it into
prose that could reach a commit or PR, since a raw UUID is the shape this
repo's `deny-private-project-refs.sh` structurally detects at commit time — the
hook stays the backstop, not the primary control.

Alternatives weighed and set aside: `key=value` output matching the log's
existing `nudged session=… est=…` idiom (rejected — JSON has a defined escaping
story for a free-text `reason`, and the hook already hard-depends on jq and
emits JSON on its fire path, so it costs nothing new); a non-zero exit on
refusal (rejected — it breaks the file's "exit 0 on all paths" invariant and
the consumer is a skill body, not a shell script). Both were the engineer's
call.

`--check` ignores the kill-switch and reports it as `nudge_disabled`: the
kill-switch suppresses *notifying*, not *measuring*, and a session that
explicitly asks for a number should get one — while still being told that push
notification is off.

**Three implementation constraints that are easy to get wrong.**

1. *Do not wrap the pinned `ps` in `_lib_capped`.* `_lib_capped` is
   `timeout 5 "$@"`, and `timeout(1)` does not apply the shell's leading
   `VAR=val` rule — it execs `TZ=UTC` as the program name. Measured:
   `timeout 5 TZ=UTC LC_ALL=C ps -o lstart= -p <pid>` exits **127** with
   `failed to run command 'TZ=UTC'`. The start-time read must therefore stay
   unwrapped, exactly as `capture-session-id.sh:95` writes it, or go through
   `_lib_capped env TZ=UTC LC_ALL=C ps …`. The two `ps` call sites sit in the
   same function, so capping the walk's `ps -o ppid=` and generalizing that to
   its neighbour would make every PID-reuse comparison fail with an empty
   result and misreport the refusal reason.
2. *The transcript glob runs under `shopt -s nullglob`, saved and restored.*
   Bash's default expands a zero-match pattern to the literal unexpanded
   pattern string, which is indistinguishable from one match by array length
   alone — and the whole `transcript-not-found` / `transcript-ambiguous` split
   depends on telling 0 from 1 from ≥2. Collect matches into an array and
   branch on that array's element count — zero means `transcript-not-found`,
   one proceeds, two or more means `transcript-ambiguous` — rather than on
   whether a `-f` test succeeds. `_lib_marker_value_present` (`_lib.sh:257-266`)
   is the in-repo precedent for the save/restore, including restoring the prior
   `nullglob` state for callers that rely on the default.
3. *The ancestor walk stops at 6 hops.* Measured need is 2 (invoked directly
   from a Bash tool call) or 3 (through a subshell); 6 leaves headroom for
   wrapper shells without becoming unbounded. Terminate on any of: hop count
   reached, PID ≤ 1, or `ps` returning empty. A hard hop cap is required
   independently of any per-call timeout — an uncapped loop over `ps` is the
   one shape that could turn this file's "exit 0 on every path" contract into
   never reaching an exit at all, which is worse than every refusal the design
   names.

**Structural constraint.** The dispatch must precede `INPUT=$(cat 2>/dev/null)`
(line 39), which is unconditional today. Invoked from a Bash tool call with no
stdin redirect, that `cat` reads inherited stdin and can block. The file's order
therefore becomes: header → `--check` detection → `_lib.sh` source and
`CONFIG_DIR` → shared helper definitions → `--check` branch (exit 0) → existing
fire path. Bash executes top-down, so the helpers must be defined above both
callers.

Two second-order effects of that reorder, neither a correctness break but both
requiring a deliberate edit rather than a silent carry-over. `_lib.sh` sourcing
and `CONFIG_DIR` resolution move ahead of the `[ -z "$SESSION_ID" ]` early exit
at line 62, so they now run on every invocation instead of being
short-circuited; sourcing has no side effects, so this costs only a little work
on a path that used to skip it. And the comment at lines 72-73 narrates a
rationale tied to the current source-then-validate position ("SESSION_ID feeds
DRIFT_MARKER and FIRED_MARKER below … fail the same way an empty id already
does"); after the reorder it must be rewritten to state a fact true of the new
order rather than left describing the old one.

### Assumption ledger

```
Root: plan-it Step 7 and handoff's warrant check currently reason about
whether a one-shot hook fired, which is not answerable as a query, so both
hedge instead of reporting a fact.

Givens:
- The harness supplies session_id and transcript_path on stdin to hooks only,
  never to Bash tool calls — beyond reach: a Claude Code hook-contract
  property this plan cannot change from within it.

Row 1 [mechanism]: --check as an early-dispatch branch inside the hook file —
anchors: root — the table and threshold arithmetic it must report live here,
and a second location would drift from the table's dated staleness control.
  Lighter primitives rejected: (a) a standalone script under
  claude/.claude/scripts/ — fails, it would need its own copy of the table and
  the min(pct,cap) formula, the exact duplication brief §6 step 3 forbids;
  (b) a flag threaded through the existing fire path — fails, that path writes
  FIRED_MARKER and a nudged log line by construction, which is precisely what
  --check must not do.

Row 2 [mechanism]: bounded process-ancestor walk to find the claude PID —
anchors: row1 — the hook's own $PPID is the intermediate shell, not claude.
  Lighter primitives rejected: (a) read $PPID directly with no walk — fails,
  measured hop1=bash, hop2=claude when the script is invoked from a Bash tool
  call, and hop3 when invoked through a subshell; (b) have each skill body
  pass --session-id "$(head -n1 …/sessions/$PPID)" — fails, it duplicates the
  lookup into two skill bodies and drops the PID-reuse check unless that is
  duplicated too, and $PPID is only the claude PID when the caller is not
  itself inside a subshell.

Row 3 [mechanism]: session-id-keyed transcript glob under the active config
dir — anchors: row1 — resolves the transcript without computing a project
slug at all.
  Lighter primitives rejected: (a) cwd→slug derivation per
  transcript-analysis.py's _path_to_project_slug — fails, measured below:
  this session's transcript sits under the worktree slug while the session
  began in the main checkout; (b) reading a transcript path from the
  environment — fails, no such variable is exported to Bash tool calls.

Row 4 [mechanism]: compare $CONFIG_DIR/sessions/<pid> line 2 against the live
process's start time — anchors: row2 — makes PID reuse a detected refusal
rather than a confident number for the wrong session.

Row 5 [mechanism]: extract resolve_context_window / compute_threshold /
read_latest_usage as functions shared by both paths — anchors: row1 — the
table's entries and its dated re-verify comment move verbatim as one block,
so the staleness control stays attached to the only copy.

Row 6 [assumption]: transcripts live at
$CONFIG_DIR/projects/<slug>/<session-id>.jsonl and the session-id glob returns
exactly one match, including after EnterWorktree
[verified: globbing ~/.claude/projects/*/<this-session-id>.jsonl returned one
path, under the worktree slug] — anchors: row3

Row 7 [assumption]: the stored start time is byte-comparable to a live
TZ=UTC LC_ALL=C `ps -o lstart=` read on the same host
[verified: stored line 2 and a live probe both returned
"Mon Aug 10 07:11:31 2026"; capture-session-id.sh:90-95 documents the pinned
TZ/locale recipe and the same-host contract] — anchors: row4

Row 8 [assumption]: INPUT=$(cat 2>/dev/null) at line 39 is unconditional and
executes before any argument inspection, so a --check dispatch placed after it
would read inherited stdin
[verified: claude/.claude/hooks/nudge-handoff-near-context-cap.sh:39] —
anchors: row1

Row 9 [assumption]: this hook is excluded from test_hook_alignment.py's
GATE_HOOKS behavior tests, since GATE_HOOKS filters ALL_HOOKS to hook-class
`gate` at line 89 and this hook is `informational`; adding a mode does not
change the class. The brief's further claim that header-presence is its *only*
ALL_HOOKS coverage is wrong — test_hook_class_header_present (228),
test_hook_class_value_valid (236), and test_gate_naming_convention_enforced
(257) all execute against it, and test_hook_documented_in_hooks_md (111) runs
via the separate _MAIN_HOOKS list and asserts a docs/hooks.md entry exists
[verified: claude/.claude/hooks/tests/test_hook_alignment.py:89,111,228,236,257]
— anchors: row1

Row 10 [assumption]: JSON output with exit 0 on every path, kill-switch
ignored but reported, active config dir only, and skill-body wording shipping
in this same PR [engineer-verified] — anchors: root

Row 11 [assumption]: with HANDOFF_NUDGE_ABS_CAP unset — the shipped state —
--check and the fire path both fall back to the same 360000 literal, so the
reported threshold matches what the fire path would compute
[verified: neither claude/.claude/settings.json nor .claude/settings.json
defines the variable or carries an `env` block] — anchors: row1

Row 12 [assumption]: `timeout` does not apply the shell's leading VAR=val rule
and execs the assignment as a program name, so the pinned start-time read must
not be wrapped in _lib_capped
[verified: `timeout 5 TZ=UTC LC_ALL=C ps -o lstart= -p <pid>` exited 127 with
"failed to run command 'TZ=UTC'"] — anchors: row4

Row 13 [assumption]: test_doc_counts.py source-scans the hook for the literal
`PCT_THRESHOLD=$(( CONTEXT_WINDOW * N / 100 ))` and raises ValueError when it
is absent, so extracting that arithmetic into a function must preserve both
variable names [verified: claude/.claude/hooks/tests/test_doc_counts.py:189]
— anchors: row5

Row 14 [assumption]: a `--check` invoked mid-turn reads a usage record at most
one assistant step old, not one full turn, so the reported estimate is current
[verified: this session's transcript tail returned a usage record timestamped
seconds before the read, est=192821, model=claude-opus-5] — anchors: row3

Row 15 [assumption]: for a consumer who *does* override the cap, an export
reaching a Bash tool call's environment also reaches a harness hook's
[unverified] — anchors: row11
```

Row 15 is load-bearing only for a consumer who overrides the cap, and it
affects the reported `threshold` field rather than fire behavior. Rather than
resolve it speculatively, `docs/handoff-nudge.md` states the bound directly:
`--check` reports the threshold *as computed in its own environment*, which is
authoritative when the cap is unset and may differ from the fire path's if the
override is set somewhere only one of the two environments sees.

## Critical files

**Modify — `claude/.claude/hooks/nudge-handoff-near-context-cap.sh`**
Add the `--check` dispatch and the three shared helpers; reorder as described
above. Reuse `_lib_config_dir` and `_lib_valid_session_id_component` from
`_lib.sh` (already sourced at line 74) rather than re-implementing either, and
`_lib_capped` for the `ps` calls in the ancestor walk. Move the model→window
`case` and its dated source comment verbatim into `resolve_context_window`,
which also reports whether the ID matched a listed arm or fell through to the
1M default — that flag is what lets `--check` say "defaulted" instead of
silently reporting 1M as if it were resolved. Build output with `jq -n`,
matching the fire path's construction, with a hardcoded
`{"status":"cannot-resolve","reason":"jq-unavailable"}` literal as the fallback
when jq is absent.

Two constraints on that extraction:

- **Keep the literal text `PCT_THRESHOLD=$(( CONTEXT_WINDOW * 40 / 100 ))`
  intact.** `test_doc_counts.py:189` source-scans the hook for exactly
  `PCT_THRESHOLD=\$\(\( CONTEXT_WINDOW \* (\d+) / 100 \)\)` and raises
  `ValueError` if it cannot find it. This repo's shell conventions
  (`claude/.claude/rules/shell-script-conventions.md`) say "`local` for all
  function-scoped variables," which pushes an implementer straight toward
  `local pct_threshold=$(( context_window * 40 / 100 ))` inside the new
  `compute_threshold` — renaming both variables and breaking that regex. Either
  keep the two names unchanged inside the function, or update the test's ground
  truth in the same commit. This is a named exception to the `local` convention,
  not an oversight.
- **The header comment gains a `--check` section.** The existing header
  documents purpose, log format, kill-switch, fail-open posture, and known
  limitations; a new invocation mode belongs there too. State that `--check` is
  read-only, that it is invoked manually rather than registered in
  `settings.json`, that its stdout is a different JSON shape from the fire
  path's `hookSpecificOutput` and is never emitted on a hook-fired path (so the
  `# hook-class: informational` contract is unchanged), and that it inherits
  the file's deliberate no-strict-mode posture. One sentence per fact, per the
  repo's comment convention.

**Modify — `docs/hooks.md`**
This file is the canonical per-hook index — `test_hook_alignment.py:111`
(`test_hook_documented_in_hooks_md`) requires a `- **<name>**` bullet for every
hook in `claude/.claude/hooks/`. The existing entry describes the hook's
trigger, one-shot behavior, and kill-switch; add the `--check` mode to it,
pointing at `docs/handoff-nudge.md` for the JSON contract rather than
restating it.

**Modify — `claude/.claude/hooks/tests/test_nudge_handoff_near_context_cap.py`**
Reuse `_run_hook`, `_write_transcript`, `_record_totalling`, `_marker_path`,
`_log_path`, `LARGE_THRESHOLD`, and `ABOVE_LARGE`. `_run_hook` calls
`subprocess.run([str(NUDGE_HOOK)], …)` with a list argument and no
`shell=True`, so the hook's `$PPID` is the pytest process itself with no
intervening shell — a test can plant `$HOME/.claude/sessions/<pytest pid>`
holding the session id plus that PID's real `lstart`. Isolation comes from the
`tmp_path`-scoped `HOME`, not from PID uniqueness, so tests sharing the one
real pytest PID do not collide.

That fixture reaches the walk at hop 1 only, which is not where the walk's
logic lives. Two additions are needed rather than one helper:

- A **multi-hop fixture** that invokes the hook through an extra shell layer
  (`bash -c 'exec "$0"' <hook>`), so the sessions entry must be found at the
  grandparent. Without it, the loop that walks *past* a non-matching ancestor —
  the entire reason row 2 exists — has no coverage.
- A **stdin-never-read assertion.** The reorder exists because `INPUT=$(cat)`
  can block on inherited stdin, but under pytest stdin is already
  redirected/closed, so `cat` hits EOF immediately and a regressed ordering
  would still pass. Invoke `--check` with a stdin pipe that is deliberately
  left open and unwritten, and assert the call completes; that fails if the
  dispatch ever reaches the `cat`.

**Modify — `docs/handoff-nudge.md`**
Document the mode, its JSON contract and `reason` vocabulary, and that it
ignores the kill-switch while reporting it. Three entries belong under the
file's existing `## Known limitations` heading, each one sentence:

- The PID-reuse check compares a second-resolution start time, so a process
  exiting and its PID being reused within the same second, with a
  byte-identical `lstart`, would go undetected — a stale number reported once,
  not a boundary crossing.
- The walk trusts any `sessions/<pid>` entry it finds without confirming the
  process is actually `claude`, so `--check`'s number is advisory and should
  not be treated as authoritative for anything beyond display.
- `--check` reports the threshold as computed in its own environment, which is
  authoritative when `HANDOFF_NUDGE_ABS_CAP` is unset and may diverge from the
  fire path's if an override is visible to only one of the two environments.

**Modify — `claude/.claude/skills/plan-it/SKILL.md`** (Step 7) and
**`claude/.claude/skills/handoff/SKILL.md`** ("Before writing: is a handoff
warranted?")

These two edits change decision prose that every stow consumer's agent reads,
so the plan carries the intended wording rather than describing it at one
level of remove.

**Step 7 has three paragraphs; only the middle one is replaced.** The opening
("Commit the reviewed plan to the implementation branch…") and the closing
`code-writer`-is-a-separate-axis paragraph both survive verbatim — dropping
either while replacing "Step 7" wholesale is the likely failure here. The
middle paragraph becomes:

> Then choose the session. **Continue in this one by default.** A fresh session
> is not free: it re-pays for context this session already holds, and that
> rebuild dominates its first several turns, so handing off early costs more
> than it saves. Run
> `~/.claude/hooks/nudge-handoff-near-context-cap.sh --check` and act on its
> JSON (see [`docs/handoff-nudge.md`](docs/handoff-nudge.md) for the contract):
>
> - `"status":"ok"` — hand off when `over_threshold` is `true`, or when
>   `already_fired` is `true`. Report `estimate` and `threshold`. Say so when
>   `nudge_disabled` is `true`: the measurement is still valid, but no nudge
>   will arrive on its own. Treat `"model_recognized":false` as a soft number —
>   the window fell back to the 1M default, so the threshold may not match the
>   running model.
> - `"status":"cannot-resolve"` or `"status":"schema-drift"` — say the estimate
>   is unavailable, name the `reason`, and fall back to judgment: session
>   length, how much of the task remains, whether the plan boundary is a
>   natural seam.
>
> These are a floor, not the only signal: hand off regardless when the engineer
> asked, when the session is ending anyway, or when a `handoff` §2 reason
> applies on its own terms. Do not quote the raw `session_id` into prose that
> may reach a commit, PR body, or plan file.

Match the file's existing formatting when this lands: `plan-it/SKILL.md` and
`handoff/SKILL.md` both write body paragraphs as single unwrapped lines, so
the paragraph text above is hard-wrapped for plan readability only.

`handoff`'s "Before writing" section takes the same `--check` call and the same
`cannot-resolve` fallback, compressed to that section's shorter register, and
keeps its existing point that a §2 reason, an engineer request, or a
session ending anyway each warrant a handoff without a cost argument.

The "fires once" and "globally disableable" caveats are dropped only because
`already_fired` and `nudge_disabled` are surfaced above — deleting the caveats
without surfacing those two fields would be a net information loss, not a wash.
The unrecognized-model and schema-drift cases survive as reported conditions
rather than hedges.

## Verification

Targeted, per the brief's §4 note that the full local suite runs well over 30
minutes and is timing-fragile under concurrent runs. Run from the worktree:

```bash
../../../.venv/bin/pytest \
  claude/.claude/hooks/tests/test_nudge_handoff_near_context_cap.py \
  claude/.claude/hooks/tests/test_hook_alignment.py \
  claude/.claude/hooks/tests/test_doc_counts.py -q
../../../.venv/bin/ruff check claude/.claude/
scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck
```

New test cases:

1. `--check` writes no `FIRED_MARKER` and appends no log line.
2. `--check` reports `over_threshold: false` below the threshold.
3. `--check` reports `over_threshold: true` at and above the threshold.
4. `--check` completes without ever reading stdin (open, unwritten pipe).
5. `--check` resolves through a multi-hop ancestor walk, with the sessions
   entry planted at the grandparent rather than the parent.
6. `--check` refuses with `session-id-unresolved` when no `sessions/<pid>`
   entry exists anywhere in the walk.
7. `--check` refuses with `session-id-stale-pid` when the stored start time
   disagrees with the live process.
8. `--check` refuses with `session-id-malformed` when the sessions entry's
   first line contains `../`, `/`, or a glob metacharacter — and touches no
   path outside `$CONFIG_DIR`. The fire path has an analogous traversal test
   (`test_traversal_session_id_does_not_create_file_outside_marker_dir`);
   `--check` builds a glob out of on-disk content and has none.
9. `--check` refuses with `transcript-not-found` when no transcript matches.
10. `--check` refuses with `transcript-ambiguous` when two project dirs both
    hold a transcript for the session id — the case the zero-match nullglob
    behavior would otherwise mask.
11. `--check` refuses with `usage-block-missing` on a transcript with no
    assistant usage record.
12. `--check` refuses with `config-dir-unresolved` under a relative
    `CLAUDE_CONFIG_DIR`, mirroring the fire path's
    `test_relative_config_dir_fails_open`.
13. `--check` resolves `sessions/`, the kill-switch, and the transcript glob
    root from an overridden absolute `CLAUDE_CONFIG_DIR`, not `$HOME/.claude`.
14. An unrecognized model ID reports `model_recognized: false` alongside the 1M
    window, rather than resolving silently.
15. `--check` reports `nudge_disabled: true` with a normal `status: "ok"`
    payload when the kill-switch file exists.
16. A schema-drifted transcript reports `status: "schema-drift"` and writes
    neither the drift marker nor a log line.

The reorder's regression guard is the ~30 pre-existing fire-path cases
(`test_above_threshold_fires_nudge`, the full `KNOWN_MODEL_THRESHOLDS` and
`COLLIDING_MODEL_IDS` matrices, the three `CLAUDE_CONFIG_DIR` cases), all of
which stay in the suite and all of which exercise the moved code end to end. No
new case is added for that — a fresh "still fires, still writes one marker and
one log line" test would duplicate `test_above_threshold_fires_nudge` in
substance. `jq-unavailable` is likewise left uncovered: the existing suite has
no jq-absent harness for this hook (`test_hook_alignment.py`'s jq-absent
parametrization covers `gate` hooks only, and excludes this one), and building
one for a single fallback literal is disproportionate — noted here so the gap
is recorded rather than silently accepted.

`test_doc_counts.py` is included because `docs/handoff-nudge.md` changes; the
hook's `# hook-class: informational` header is unchanged, so
`test_hook_alignment.py`'s `ALL_HOOKS` assertion should stay green (row 9).

Known-unrelated failure, per brief §4:
`test_shellcheck.py::TestGateActuallyBites::test_xargs_zero_composition_exits_nonzero_on_empty_input`
fails on macOS (asserts GNU `xargs` behavior; BSD `xargs` differs) and fails on
a clean checkout. Do not fix it here.

Review pipeline before commit: `/skill-review` (two SKILL.md files change —
hook-enforced), `claude-hook-review` (hook changes), then `/code-review`.

## Out of scope

- **Retuning `HANDOFF_NUDGE_ABS_CAP`.** Inside this plan's reach and
  deliberately declined: retuning was evaluated and rejected with arithmetic in
  `.claude/plans/handoff-boundary-decision-rule.md` on main. `--check` surfaces
  the threshold and invites second-guessing it; don't.
- **Making `$CONFIG_DIR/sessions/<pid>` a guaranteed rather than best-effort
  lookup.** `capture-session-id.sh` is in this repo, so its absence is inside
  reach — but it fails open at SessionStart deliberately, since a fail-closed
  SessionStart hook would block session startup, which is worse than a
  `--check` that refuses. Hardening it is a separate change against a hook this
  plan otherwise only reads. Without that dependence `--check` would still need
  the ancestor walk; it would lose only the `session-id-unresolved` branch.
- **The two-tier or re-arming nudge.** `docs/handoff-nudge.md` defers it with a
  named revisit bar (session-share crossing 50%).
- **The nudge-to-handoff conversion report** against `transcript-analysis.py`.
- **A cost-per-output-token subcommand** for `transcript-analysis.py`.
- **The macOS `xargs` shellcheck test failure** — pre-existing and unrelated.
- **Refactoring the model→window table's entries.** Row 5 moves the block
  verbatim into a function; the entries, arms, and dated re-verify comment are
  untouched.
- **Logging `model_recognized` on the fire path.** `--check` reports it; the
  `nudged` log line's fields stay as they are.
