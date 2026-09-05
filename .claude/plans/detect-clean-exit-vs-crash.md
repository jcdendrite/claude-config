# Distinguish clean exits from crashes in post-crash-sessions

## Context

`post-crash-sessions.py` currently cannot tell a deliberate clean exit
(`/exit`, `/clear`, logout, closing the CLI normally) apart from an actual
crash once a session's process is dead — every "dead pid, mtime after last
boot" case, whether from the Claude Code CLI's own session registry or from
this repo's own `capture-session-id.sh` lookup files, is reported under
"Possible crash — process gone, clean exit not ruled out," forcing the user
to manually guess from context (paths, timestamps) which is which. The user
hit this ambiguity directly in a real report — one worktree session was
plainly `/exit`-ed intentionally, another crashed mid-session, and the report
gave no mechanical way to tell them apart. The intended outcome: sessions
whose process recorded a graceful `SessionEnd` hook firing are reclassified
into a new, distinct "Confirmed clean exit (SessionEnd recorded)" report
bucket, while sessions with no such record keep surfacing exactly as today.

## Approach

A new `SessionEnd` hook writes a small per-process record when Claude Code
shuts down gracefully; `post-crash-sessions.py` reads those records as a
fifth evidence source and reclassifies a dead-pid session into a new
"Confirmed clean exit (SessionEnd recorded)" bucket only when *every* dead
process instance tracked for that session has a matching record. Sessions
with no record, or with partial coverage across resume cycles, keep
reporting as "Possible crash" exactly as today.

**Design decisions, concretely:**

**Source E on disk.** `<config-dir>/session-end-records/<pid>`, one file per
pid, containing a single JSON object: `{"sessionId": "<id>", "reason":
"<SessionEnd reason or null>"}`. No timestamp field — the file's own mtime is
the record time, which keeps one time source shared by the match rule and
the sweep, and matches how Source D dates itself (`_read_lookup_entries`
uses `_safe_mtime` only). Directory name deliberately avoids `*-markers/` and
`.*-active.d/`, which `enforce-marker-script-shape.sh` reserves for
gate-release and gate-suspend semantics; this is corroborating evidence, not
a gate.

**The match rule** (this is the correctness core). A dead entry `E` is
*explained* by a record `R` iff all three hold:
1. `R.config_dir` and `E.config_dir` both resolve and are equal.
2. `R.pid == E.pid`.
3. `R.mtime >= E.mtime`.

Condition 3 is what makes pid reuse safe without storing a process-identity
field. Every Claude session writes its start-side evidence (registry
`sessions/<pid>.json` by the CLI, lookup `sessions/<pid>` by
`capture-session-id.sh`) at the same pid it later writes its end record to,
so a process that reuses pid P necessarily rewrites P's entry *after* the
previous occupant's record — its newer entry mtime then fails condition 3.
This holds for `/clear` too, since `SessionStart` fires with `source:
"clear"` in the same process and rewrites the lookup file at the same pid.
Condition 1 closes the one hole condition 3 alone leaves: on a multi-account
machine, account 2's session at pid P does not touch account 1's `sessions/`
directory, so a cross-account record could otherwise falsely explain account
1's stale entry.

**Config-dir comparison must resolve at comparison time, not rely on stored
values already being resolved.** `RegistryEntry.config_dir` and
`LookupEntry.config_dir` are populated with the raw, unresolved `Path` passed
into `build_report`'s `config_dirs` list (`post-crash-sessions.py:612` and
its lookup-entry analogue) — resolution to a canonical absolute path happens
only ad hoc, at specific comparison sites such as `account_of()`
(`post-crash-sessions.py:1332`, `config_dir.resolve()`), not at storage time.
`_graceful_end_record`'s condition 1 must therefore call `.resolve()` on both
`R.config_dir` and `E.config_dir` at comparison time, mirroring that
existing convention — comparing the stored values directly would silently
never match whenever a config dir reaches the two readers via differently
-normalized paths (e.g. a `--config-dir` CLI argument's literal text versus
`config_dir()`'s auto-discovered path for the same directory), which is
exactly the multi-account scenario condition 1 exists to get right.

**Interception, per branch.** Both safe interception points compute the same
coverage over their existing dead-entry list, then branch on it. Registry
branch (`if dead_after_boot:`, currently line 973) and lookup branch (`if
dead_lookups and not indeterminate_lookups:`, currently line 1015):
- **Full coverage** (every entry in the list explained) → return
  `CLASS_CONFIRMED_CLEAN_EXIT`, detail citing the newest record's reason and
  time. This applies in *both* arms of each branch — with and without a main
  transcript. A clean exit that wrote no transcript is still not a crash, so
  routing it to the new bucket rather than leaving it in `CLASS_UNKNOWN`
  keeps one rule and removes noise; the transcript fact stays in the detail
  text.
- **Partial coverage** (`0 < covered < total`) → keep today's classification
  (`CLASS_POSSIBLE_CRASH` / `CLASS_UNKNOWN`, unchanged) and append a coverage
  sentence to the detail: `" {covered} of {total} tracked process instances
  for this session recorded a graceful SessionEnd; at least one did not."`
  Build that sentence once per branch and interpolate into both arms rather
  than repeating the construction four times.
- **Zero coverage** → byte-identical to today, no appended note.

Partial coverage is the resume case the pid keying exists for: one session
id spans several process lifetimes, so a session that exited cleanly once
and crashed on a later resume must stay in "Possible crash."

**Report bucket and the sibling rename.** New constant
`CLASS_CONFIRMED_CLEAN_EXIT = "confirmed-clean-exit"`, rendered through the
compact `other_groups` path (not `render_resume_section`) — the bucket exists
to be skipped, so a copy-paste resume command would be a call to action on
rows the reader is meant to ignore, and this matches how the existing
no-action bucket renders. Section order becomes:

```python
other_groups = (
    (CLASS_CRASHED_NO_TRANSCRIPT, "Crashed, no transcript"),
    (CLASS_CONFIRMED_CLEAN_EXIT, "Confirmed clean exit (SessionEnd recorded)"),
    (CLASS_LIVE_PROCESS, "Still running (a live process matches a tracked pid)"),
    (CLASS_UNKNOWN, "Unknown"),
)
```

Rename `CLASS_CLEAN_EXIT` → `CLASS_LIVE_PROCESS = "live-process"` and retitle
its section as above. Grepping the file shows `CLASS_CLEAN_EXIT` has exactly
one producer (line 928, "a live process matches a tracked pid"), so both its
name and its current title "Not crash evidence (clean exit or still
running)" are already inaccurate — and shipping `CLASS_CLEAN_EXIT` alongside
`CLASS_CONFIRMED_CLEAN_EXIT`, two names differing by an adjective but meaning
unrelated things, would be a naming trap for the next reader. This is
in-file cleanup on a file the change already scopes, and the string value is
internal to the module.

**Rollout diagnostic.** Add `any_session_end_dir_found: bool` to `Report`,
mirroring the existing `any_sessions_dir_found`, and emit a header NOTE when
no `session-end-records/` directory exists in any scanned config dir.
Without it, the first weeks after install look identical to a broken hook.

**Self-sweep.** The hook sweeps its own directory on every fire, after
writing its record: `find "$RECORDS_DIR" -maxdepth 1 -type f -mtime +30
-delete 2>/dev/null || true`. 30 days and this exact idiom are the repo's
established convention for hook-owned state directories —
`advance-past-commit-stall.sh:207`, `nudge-worktree-anchor.sh:167`,
`nudge-error-mode-analysis.sh:151`, `log-reviewer-round.sh:114`, and
`nudge-handoff-near-context-cap.sh:555` all use it, and
`docs/error-mode-nudge.md:36` documents it as the standing replacement for
destructor-based cleanup. Sweeping *after* the write means a sweep failure
can never cost a record. If machine uptime exceeds 30 days, a swept record
stops explaining a still-post-boot registry entry and that row returns to
"Possible crash" — the pre-change behavior, so the degradation is fail-safe.

**Hook shape.** `record-session-end.sh`, `# hook-class: informational`,
every failure path `exit 0` with a one-line `[record-session-end] …`
diagnostic to stderr only. `SessionEnd` shares a 1.5s default execution
budget, and a shutdown hook that fails closed is strictly worse than a
missing record — a missing record already degrades to today's behavior. It
reuses `_lib_config_dir` and `_lib_valid_session_id_component` from
`_lib.sh`, and the pid-resolution block extracted below.

**`_lib.sh` extraction.** `capture-session-id.sh` lines 73-88 hold a
~15-line validate-then-select block (accept `$CLAUDE_PID` only when it
equals `$PPID` or `$PPID`'s immediate parent, else fall back to `$PPID`).
The new hook needs the identical logic, so extract it now as
`_lib_hook_claude_pid` and have both hooks call it. Its header comment must
state the one non-obvious fact: hooks are direct children of `claude`, so
this resolver applies and `_lib_resolve_claude_pid`'s ancestor walk does not.

**Alternatives set aside.** Three lighter mechanisms were checked against
the source before adopting a new hook plus a new on-disk source:
- *Append a third line to the existing Source D lookup file* instead of a
  new directory. Rejected: one file cannot carry two independently-ordered
  timestamps, and the start-vs-end mtime ordering is precisely what makes
  the pid-reuse guard sound. It would also perturb `_read_lookup_entries`'s
  positional line-1/line-2 contract, which `marker.sh` and the `/respond-pr`
  skill also read.
- *Infer clean exit from existing state* — the registry's `status` field or
  a transcript tail sentinel. Rejected: the registry is a CLI-owned
  undocumented format the module docstring already reads defensively
  field-by-field, no end-of-session sentinel is documented for transcripts,
  and both are absent in the exact case at issue (process gone, entry
  stale).
- *A `SessionEnd` destructor that deletes the Source D lookup file*, making
  absence mean clean exit. Rejected twice over: absence is unfalsifiable (a
  crash and a never-hooked session look identical), and it destroys the
  evidence Source D exists to supply. `capture-session-id.sh`'s own header
  (lines 38-42) already records why destructor-based cleanup was retired
  here.

### Assumption ledger

**Root:** `post-crash-sessions.py` cannot distinguish a graceful exit from a
crash once the process is dead, so every dead-pid session lands in
"Possible crash" and the operator must guess.

**Givens** (fixed beyond this design's reach):
- The session registry (`<config-dir>/sessions/<pid>.json`) is a Claude
  Code-owned undocumented format — Anthropic owns it, so nothing here writes
  to it or depends on a field beyond `sessionId`/`pid`.
- `SessionEnd`'s payload schema carries no `pid` field — Anthropic defines
  the payload, so the hook must resolve its own Claude-process pid.
- `claude -p` skips `SessionEnd` entirely — the CLI decides which events
  fire in headless mode.
- `SessionEnd` runs under a 1.5s default execution budget — the harness
  imposes it.

**Mechanisms:**
- New `SessionEnd` hook writing a per-pid record — the only documented
  mechanism that observes a graceful exit at all; three lighter alternatives
  enumerated and rejected above. `anchors: root`
- New Source E reader plus a new `CLASS_CONFIRMED_CLEAN_EXIT` bucket in
  `post-crash-sessions.py` — a record that no reader consults changes no
  report. `anchors: root`
- Pid keying rather than session-id keying — one session id spans multiple
  process lifetimes across resume cycles. `anchors: row2`
- `config_dir` + mtime-ordering in the match rule — closes cross-account and
  pid-reuse false matches without a new stored field. `anchors: row3, row4`
- `_lib_hook_claude_pid` extraction into `_lib.sh` — a second caller for
  logic that exists inline today. `anchors: row6`
- 30-day self-sweep on the hook's own directory — Source D's documented
  no-sweep posture has already produced a 2011-file backlog.
  `anchors: row8`

**Rows:**
1. `[unverified]` — `SessionEnd` does **not** fire on a hard kill (SIGKILL,
   OOM, terminal force-close, reboot). Both official hook pages were
   searched for "crash", "SIGKILL", "force-clos*", "reboot", "power", "kill"
   with no hit; the 1.5s budget is consistent with a graceful-shutdown-only
   code path but is not proof. This is the single load-bearing premise of
   the whole design — if `SessionEnd` fired on a kill, a record would
   falsely exonerate a real crash. Verification steps 4 and 5 test it
   empirically.
2. `[verified: post-crash-sessions.py:942-949, 1013]` — a session id maps to
   multiple pids across resume cycles; `dead_before_boot`, `dead_after_boot`,
   and `dead_lookups` are all lists for exactly this reason, and the report
   prints an "N underlying entries" count per row.
3. `[verified: capture-session-id.sh:106; post-crash-sessions.py:561-614]` —
   every Claude session writes start-side evidence at the same pid it will
   later write its end record to (the CLI writes `sessions/<pid>.json`;
   `capture-session-id.sh` writes `sessions/<pid>`), which is what makes
   `R.mtime >= E.mtime` a sound pid-reuse guard.
4. `[verified: post-crash-sessions.py:168, 193, 612, 1332]` — see the
   Approach section's "Config-dir comparison must resolve at comparison
   time" note for the underlying fact this row cites. One additional fact
   beyond that cross-reference: entries constructed directly in tests
   without a `config_dir` can never match a record — that is fail-safe, but
   the new tests must set it — and the existing
   `_registry_entry`/`_lock_entry`/`_lookup_entry` factory helpers in
   `test_post_crash_sessions.py` (lines 149-179) default `config_dir=None`
   and none currently accepts a `config_dir` kwarg, so this requires
   extending those helpers (see Critical files, Phase 2), not just passing a
   value the helpers don't yet accept.
5. `[verified: post-crash-sessions.py:409-465]` — registry `procStart` is
   platform-variant (lstart string on Darwin, `/proc` tick count on Linux),
   which is why the match rule uses mtime ordering rather than comparing a
   stored process-start value across sources.
6. `[verified: capture-session-id.sh:73-88, 19-24]` — `$CLAUDE_PID` is
   described as exported into "hook environments" generally, not as
   SessionStart-specific, so the same validate-then-select resolution
   applies to a `SessionEnd` hook.
7. `[verified: docs/en/hooks]` — the documented `reason` values are `clear`,
   `resume`, `logout`, `prompt_input_exit`, `other`; none denotes a crash.
   The hook therefore records whatever `reason` arrives without filtering,
   and the *presence* of a record — not its reason — is the signal.
8. `[verified: docs/error-mode-nudge.md:36; advance-past-commit-stall.sh:207;
   nudge-worktree-anchor.sh:167; nudge-error-mode-analysis.sh:151;
   log-reviewer-round.sh:114; nudge-handoff-near-context-cap.sh:555]` —
   `find … -maxdepth 1 -type f -mtime +30 -delete 2>/dev/null || true` on
   every qualifying fire is this repo's established self-sweep convention,
   at five call sites.
9. `[verified: settings.json; enforce-marker-script-shape.sh]` —
   `SessionEnd` is not currently a key in `settings.json`, so a new
   top-level array is required; `session-end-records/` matches neither
   reserved marker-path pattern.
10. `[verified: test_hook_alignment.py:427-469, 115-141]` — a new hook must
    carry a `# hook-class:` header with value `informational` and a
    line-start ``- **`record-session-end.sh`**`` bullet in `docs/hooks.md`,
    or the alignment tests fail.
11. `[engineer-verified]` — clean exits go into a new distinct bucket, not
    folded into the existing "Not crash evidence" bucket, and the new class
    constant is separate from the existing `CLASS_CLEAN_EXIT`.
12. `[unverified]` — whether a `SubagentStart` payload's `session_id` differs
    from its parent session's. If it does, `capture-session-id.sh` overwrites
    the main session's lookup file at the same pid with a subagent id, and
    those rows would be explained by the pid-keyed record anyway under the
    match rule (same OS process, record postdates the entry). Nothing in the
    design depends on resolving this; it only affects which rows benefit.
    Verification step 3 will show it in practice.

## Critical files

All paths below are repo-relative; writes must land in this branch's own
worktree, not in the main tree.

**Dispatch split — two sequential `code-writer` phases.** Phase 1 is the
writer side, Phase 2 the reader side. They touch disjoint file sets, but
Phase 1's on-disk output is Phase 2's fixture contract, and sequencing lets
Phase 2's manual check run against a record the real hook produced.

### Phase 1 — writer side

- **`claude/.claude/hooks/record-session-end.sh`** (new). `# hook-class:
  informational`. Reads stdin JSON, extracts `.session_id` and `.reason` via
  `jq -r '… // empty'`, sources `_lib.sh`, validates the session id, resolves
  the config dir, resolves its own Claude pid, `mkdir -p` the records dir,
  writes the record, then sweeps. Every failure path `exit 0` with a
  one-line stderr diagnostic, including a failed `mkdir -p` or a failed
  record write (disk full, permissions) — guard both the same way
  `capture-session-id.sh` guards its own `_lib_config_dir`/write failures,
  so an unwritable directory degrades to "no record" rather than a stray
  error. Reuse: `_lib_config_dir`, `_lib_valid_session_id_component`, and the
  new `_lib_hook_claude_pid` — reimplement none of them. Build the JSON with
  `jq -n --arg sid … --arg reason …` rather than hand-quoting; `jq` is
  already a hard dependency of this hook's own stdin parsing. Avoid bash-4
  constructs — `test_no_bash4_constructs.py` covers this directory.
  Register in `settings.json` as `~/.claude/hooks/record-session-end.sh`,
  mirroring the existing `SessionStart` entry's path form for
  `capture-session-id.sh`. The header must state, in the same one-fact-per-
  line style as `capture-session-id.sh`'s own header, two known gaps: `claude
  -p` skips `SessionEnd` entirely, and whether `SessionEnd` fires on a hard
  kill (SIGKILL, OOM, reboot) is undocumented upstream and verified only by
  manual test, not by an automated check that runs on every version upgrade
  — a future reader of the script itself, not only of `docs/hooks.md`, needs
  to see the limitation. Note for
  implementation: `_lib_hook_claude_pid` conditionally shells out to `ps -o
  ppid= -p "$PPID"` whenever `$CLAUDE_PID` is set and numeric (the common
  case per ledger row 6) — the hook's per-fire work is therefore `jq` (×2) +
  `ps` + `find` + `mkdir`, not `ps`-free; comfortably inside the 1.5s default
  `SessionEnd` budget at normal load, but worth naming rather than leaving
  implicit.
- **`claude/.claude/hooks/_lib.sh`** (modify). Add `_lib_hook_claude_pid`,
  lifted verbatim from `capture-session-id.sh:77-88`. One-line header fact:
  hooks are direct children of `claude`, so this resolver applies and
  `_lib_resolve_claude_pid`'s ancestor walk does not.
- **`claude/.claude/hooks/capture-session-id.sh`** (modify). Replace lines
  73-88 with a call to `_lib_hook_claude_pid`, keeping the existing stderr
  message and `exit 0` on an unresolvable pid. Behavior-preserving; the
  comment at lines 19-24 stays, since it documents the contract rather than
  the implementation.
- **`claude/.claude/settings.json`** (modify). New top-level `"SessionEnd"`
  array with one entry for `record-session-end.sh`, no matcher — every
  documented `reason` value qualifies, so filtering would only create a way
  to miss one.
- **`docs/hooks.md`** (modify). New ``- **`record-session-end.sh`**``
  bullet. There is no `SessionEnd` content in this file today, so the
  bullet must state what the event is as well as what the hook does.
- **`claude/.claude/hooks/tests/test_record_session_end.py`** (new). Model
  on `test_capture_session_id.py` (323 lines): one test class,
  `isolated_home` fixture, assertions reading the written file off disk.
  Reuse `run_hook_advisory` from `claude/.claude/tests/helpers.py` — the
  variant for hooks with no allow/deny decision. Cases: happy path writes
  the expected JSON at `<config>/session-end-records/<pid>`; missing
  `reason` still writes a record; empty stdin, malformed JSON, and missing
  `session_id` each exit 0 and write nothing; a traversal-shaped
  `session_id` writes nothing and leaves a planted canary untouched;
  `CLAUDE_CONFIG_DIR` override is honored; the sweep deletes a >30-day-old
  file and preserves a fresh one; the sweep runs after the write so a record
  survives an unsweepable directory; an unwritable records directory
  (`mkdir -p` or the record write itself failing — simulate via a read-only
  parent directory) still exits 0 and writes nothing; exit status is 0 on
  every path.
- **`claude/.claude/hooks/tests/test_lib.py`** (modify). Direct coverage for
  `_lib_hook_claude_pid`: `$CLAUDE_PID` equal to `$PPID` accepted, equal to
  `$PPID`'s parent accepted, unrelated value rejected in favor of `$PPID`,
  non-numeric rejected.
- `claude/.claude/hooks/tests/test_capture_session_id.py` — no new cases
  expected; the existing `$CLAUDE_PID` accept/reject tests are the
  regression net for the refactor. Adjust only if the extraction moves a
  stderr string.

### Phase 2 — reader side

- **`claude/.claude/scripts/post-crash-sessions.py`** (modify):
  - Module docstring — add source E and its evidence-weighting rule ("a
    record is exculpatory, not incriminating: it can only move a row out of
    possible-crash"), and update "Four evidence sources" to five. The
    "Read-only, always" opening stays true — the hook writes, this script
    does not.
  - Constants — add `CLASS_CONFIRMED_CLEAN_EXIT`; rename `CLASS_CLEAN_EXIT`
    → `CLASS_LIVE_PROCESS`.
  - New `SessionEndRecord` dataclass (`session_id`, `pid`, `reason`, `mtime`,
    `path`, `config_dir`), placed after `LookupEntry`.
  - New `_read_session_end_records(config_dirs)` returning
    `(records_by_key, any_dir_found)`, keyed `(resolved_config_dir, pid)`.
    Model on `_read_registry`'s per-config-dir loop and `_read_lock`'s
    defensive JSON parse. Skip non-digit filenames, unparsable JSON,
    non-dict payloads, empty `sessionId`, and files whose `_safe_mtime` is
    `None` (no mtime means no ordering, so no match). Reuse `_coerce_pid`,
    `_safe_mtime`, `_sanitize_for_terminal`. Keep the newest by mtime on a
    duplicate key. No crash-window filter on read — staleness is handled by
    the match rule, and a window would only re-suppress valid exculpatory
    evidence.
  - New `_graceful_end_record(entry, records)` implementing the
    three-condition match; it works on both `RegistryEntry` and
    `LookupEntry` via `.pid`/`.mtime`/`.config_dir`. Condition 1 must call
    `.resolve()` on both `entry.config_dir` and the record's `config_dir`
    before comparing — neither dataclass stores an already-resolved path
    (see the Approach section's "Config-dir comparison must resolve at
    comparison time" note) — and condition 3 is `>=` (an exact mtime tie
    counts as a match), not `>`.
  - `_classify_session` — new keyword arg for the records map; coverage
    computation and the three-way branch at lines 973 and 1015 as specified
    above. Leave `entry_count` semantics unchanged: a record corroborates an
    entry rather than being one, and changing the count would churn every
    existing test's expectations for no gain.
  - `build_report` — call the new reader inside the existing `config_dirs`
    flow (never account-specific), thread the map into the
    `_classify_session` list comprehension the same way `lookup_entries=` is
    threaded at line 1183, and carry `any_session_end_dir_found` onto
    `Report`.
  - `render_report` — new `other_groups` tuple and title as specified; new
    header NOTE when no records directory was found, mirroring the
    `any_sessions_dir_found` NOTE at line 1284.
- **`claude/.claude/scripts/tests/test_post_crash_sessions.py`** (modify).
  Follow the file's existing convention of a `test_classify_*` unit test
  plus a `test_build_report_*` fixture-driven test per bucket. First extend
  the three existing entry-factory helpers (`_registry_entry`, `_lock_entry`,
  `_lookup_entry`, lines 149-179) with a `config_dir` kwarg — default it to
  preserve each helper's current behavior at every existing call site — and
  add a new `_session_end_record` factory alongside them; without this, a
  test that "sets `config_dir`" per ledger row 4 has nowhere to pass it.
  New pairs for: registry `dead_after_boot` fully covered → confirmed clean
  exit; registry partially covered → possible crash with the coverage
  sentence; lookup `dead_lookups` fully covered → confirmed clean exit;
  lookup partially covered → possible crash with the coverage sentence; a
  record whose mtime predates the entry's → no match (the pid-reuse guard);
  a record whose mtime exactly equals the entry's → match (the `>=`
  boundary, using `os.utime()` for exact control as the suite already does
  at lines 1103/1105); a record under a different (but `.resolve()`-equal)
  config dir → no match (the cross-account guard); a malformed-JSON,
  non-dict-JSON, or empty-`sessionId` record file → degrades to the
  no-record classification, not a crash (mirroring
  `test_build_report_foreign_json_in_sessions_dir_produces_clean_report`'s
  pattern for Source A); a non-digit-named file under
  `session-end-records/` → silently skipped, not counted as parse-error
  noise; two record files resolving to the same `(config_dir, pid)` key with
  differing mtimes → the newer one governs the match; a record file that
  disappears between the directory scan and the read (simulate via a
  monkeypatched read raising mid-scan, matching Source A/B's own
  defensive-OSError pattern) → degrades to the no-record classification
  rather than raising; a lookup file at one pid rewritten under a second
  session id (the subagent-pid-overwrite shape from ledger row 12) → pins
  today's match-rule behavior as a regression net, without resolving whether
  subagent session ids differ from their parent's; no-transcript full
  coverage → confirmed clean exit rather than unknown; render ordering and
  the exact section title; the empty-source NOTE. Plus mechanical updates
  for the `CLASS_LIVE_PROCESS` rename and its new section title. Reuse the
  existing `conftest.py` `_dead_pid()` helper and the autouse
  `CLAUDE_CONFIG_DIR` fixture.
- **`docs/scripts.md`** (modify, line 37). Add the SessionEnd-records source
  to the cross-referenced-sources sentence. Keep "Read-only — writes no
  file, ever."

## Verification

1. **Automated, scoped to the diff:** `.venv/bin/python3
   claude/.claude/scripts/select-tests.py` from the worktree root. Its rule
   table already maps `claude/.claude/hooks/`, `claude/.claude/scripts/`,
   `settings.json`, and `docs/hooks.md` to the right test directories, so it
   selects the hook tests, the script tests, and `test_hook_alignment.py`
   without a manual widening. Do not run the full suite by hand.
2. **Lint:** `.venv/bin/ruff check claude/.claude/` for the Python change,
   and `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck` for the
   new hook.
3. **Manual smoke test — graceful exit produces a record and moves the
   bucket.** In a scratch terminal, start a Claude Code session, note its
   pid, exit with `/exit`. Confirm `<config-dir>/session-end-records/<pid>`
   exists with the expected `sessionId` and a `reason` of
   `prompt_input_exit`. Run `post-crash-sessions` and confirm that session
   now appears under "Confirmed clean exit (SessionEnd recorded)" rather
   than "Possible crash."
4. **Manual smoke test — hard kill produces no record (tests ledger row
   1).** Start a second session, note its pid, `kill -9` it. Confirm no
   `session-end-records/<pid>` file was created, and that
   `post-crash-sessions` still reports it under "Possible crash — process
   gone, clean exit not ruled out." A record appearing here falsifies the
   design's load-bearing premise; stop and report rather than shipping,
   because a `SessionEnd` that fires on SIGKILL would make the new bucket
   actively misleading. **Re-run this check after any Claude Code CLI
   minor or major version upgrade**, not only once at initial rollout —
   this is the plan's single load-bearing premise (ledger row 1) and
   nothing else re-verifies it on an ongoing basis.
5. **Partial-coverage check.** Start a session, exit cleanly, `claude
   --resume` it, then `kill -9` the resumed process. Confirm the row stays
   in "Possible crash" and its detail carries the "1 of 2 tracked process
   instances…" sentence — this is the resume-cycle correctness case that pid
   keying exists for.

## Out of scope

- **The transcript-only fallback branch** (`_classify_session` lines
  1074-1104). Sessions reaching it have no registry, lock, or lookup entry,
  hence no pid to correlate a record against. Matching them by session id
  alone would reintroduce exactly the staleness the pid keying eliminates: a
  session that exited cleanly once and crashed on a later resume would be
  masked. These rows keep reporting as "Possible crash."
- **The `dead_before_boot` registry arm and the lock branch.** Both were
  checked as structural siblings of the two intercepted branches. Neither
  produces a "Possible crash" row today — they yield `CLASS_RESUMABLE` or
  `CLASS_CRASHED_NO_TRANSCRIPT` — and the `RESUMABLE` rows carry a working
  resume command a user may still want for a cleanly-exited session, which
  reclassification would remove. One residual mislabel is knowingly left: a
  clean exit with no transcript that predates the last boot still renders
  under "Crashed, no transcript." Real but rare, and outside the reported
  pain point; a candidate follow-up rather than a defect introduced here.
- **`claude -p` headless runs.** They skip `SessionEnd` entirely
  (`docs/hooks.md:53`, `docs/error-mode-nudge.md:36`), so a headless run
  that exits cleanly will never carry a record and will keep surfacing as
  "Possible crash." This is a permanent limitation of any `SessionEnd`-based
  signal, not something this design can close; state it in the
  `docs/hooks.md` bullet so the next reader does not treat it as a bug.
- **Backfill for sessions that ended before the hook was installed.** No
  record can be reconstructed after the fact. The rollout NOTE in the report
  header is the mitigation; the bucket simply fills over time.
- **Records that outlive the 30-day sweep on a machine whose uptime exceeds
  30 days.** The affected row reverts to "Possible crash" — pre-change
  behavior, and the fail-safe direction. Raising the threshold would diverge
  from the repo-wide convention for a case that costs nothing.
- **Subagent-attributed lookup rows.** Ledger row 12 leaves open whether a
  `SubagentStart` payload carries a distinct session id. The design does not
  depend on the answer, and no work is planned to resolve it; verification
  step 3 will show which rows move in practice.
- **Any change to the session registry format or to Source D's file shape.**
  The registry is CLI-owned. Source D's two-line contract is read by
  `marker.sh` and `/respond-pr` as well as this script, and the new source
  is deliberately separate so the blast radius stays isolated.
