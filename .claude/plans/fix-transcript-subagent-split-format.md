# Fix `transcript-analysis.py` subagent false-negative (split-transcript format)

## Context

**Goal:** Make `transcript-analysis.py subagents` (and the sibling `skill-followers`
metric) correctly count turns that ran inside named subagents — check-runner,
Explore, `staff-*` reviewers — instead of reporting zero.

**Problem & why now:** Running
`transcript-analysis.py subagents --branches day-285-sentry-edge-pr1` reports
**0 subagent turns / 208 main-thread turns**, even though that branch dispatched
many subagents. A real post-hoc analysis trusted this output and wrongly concluded
no check-runner agents were ever dispatched. The script is stowed from this repo
(`claude/.claude/scripts/transcript-analysis.py` → `~/.claude/scripts/`), so the
false metric ships to every stow user.

**Root cause (verified, not the brief's hypothesis):** The brief guessed a wrong
`isSidechain` field name or a missing flag. Neither is true. Subagent transcripts
have moved to **separate files** at
`~/.claude/projects/<project>/<session-id>/subagents/agent-*.jsonl`, each with a
`.meta.json` sidecar naming `agentType`. Those records carry `isSidechain: true`
and `gitBranch` **correctly**. The defect is in the shared `iter_sessions` helper
(line 314–329): its glob `projects_dir.glob(f"{projects_glob}/*.jsonl")` matches
only top-level session files and never descends into the `subagents/` subdir, so
subagent records are never loaded. The `subagents` filter (`isSidechain ?
"sidechain" : "main"`, line 566) is correct but only ever sees main-thread records.
No top-level session file contains `"isSidechain":true` (verified by grep), so the
old assumption that sidechain turns are inlined no longer holds.

**Verified evidence:** For `day-285-sentry-edge-pr1`, session `4319050d` has 9
subagent files carrying that branch with 18/40/3/39/13/4/5/40/19 sidechain
assistant turns respectively — so the corrected output will show dozens of
sidechain turns, not the brief's conservative "≥2".

**Sibling defect (same root cause, in scope per user decision):**
`cmd_skill_pair` (the `skill-pair` subcommand, defined line 991; its
`iter_sessions` call is at line 1009) does per-session correlation and tracks
`has_sidechain_follower` → `follower_sidechain_only`. Because the sidechain
follower records are never read, that bucket is **always 0** — a silently false
metric with the identical cause.

**Why existing tests didn't catch it:** The test fixtures model sidechain records
as inlined entries in the top-level session file (the old format) via
`_asst(..., sidechain=True)` written into the same `<id>.jsonl` (see
`test_leader_plus_sidechain_only_follower`, which passes today). The on-disk
reality moved to the split `subagents/` layout, which no fixture exercises.

**Two on-disk formats, mutually exclusive per session:** Old sessions inline
sidechain records into `<id>.jsonl` and have no `subagents/` dir; new sessions
split them into `<id>/subagents/*.jsonl` and never inline. A single session uses
one or the other, so merging the subagent-dir files cannot double-count against
inlined records. The merge is purely additive — it only *adds* subagent-dir
records — so the existing inlined-format tests remain valid and serve as the
regression guard that the old format still works.

## Approach

Add an **opt-in** parameter `include_subagents: bool = False` to
`iter_sessions`. When `True`, after reading a top-level session file
`<project>/<id>.jsonl`, also read every `*.jsonl` under
`<project>/<id>/subagents/` and **append** those records to the *same* session's
record list (merged, single yield per session). Default `False` preserves the
exact current behavior for every other subcommand — zero blast radius.

- Subagent dir derivation from the session file path:
  `jsonl.parent / jsonl.stem / "subagents"`, globbed for `*.jsonl`; guard for a
  missing dir (sessions with no subagents).
- Route `cmd_subagents` (iter_sessions call at line 558) and `cmd_skill_pair`
  (iter_sessions call at line 1009) through
  `iter_sessions(..., include_subagents=True)`. No other call site changes.
- Add a one-line comment in `iter_sessions` naming the `subagents/` subdir layout
  and that subagent records are the `isSidechain: true` turns, so the next
  diagnoser doesn't have to re-derive the on-disk shape from JSONL.

**Why merge into the parent session rather than yield subagent files separately:**
`cmd_subagents` only counts per branch/thread, so either shape works for it. But
`cmd_skill_pair` requires the main-thread leader (in `<id>.jsonl`) and the
sidechain follower (in a `subagents/` file) to co-occur in **one** record list;
yielding subagent files as separate sessions would put them in different
iterations and the correlation would never fire. Merging is the primitive that
serves both consumers, so it is the correct shared shape.

### Lighter alternatives considered

- **Fix only `cmd_subagents` to read its own subagent files (no `iter_sessions`
  change).** Rejected: duplicates the file-discovery + JSON-parse loop that
  `iter_sessions` already owns, and does nothing for the `skill-followers`
  sibling, leaving the second false metric live.
- **Rename / change the field the filter checks (the brief's hypothesis).**
  Rejected: the field is already correct (`isSidechain: true` is present on
  subagent records); the records are simply never read. A field change fixes
  nothing.
- **Globally broaden the glob** to `**/*.jsonl` or add `*/subagents/*.jsonl` for
  all subcommands. Rejected as over-powered: it feeds subagent records as
  separate "sessions" into every subcommand, breaking `skill-followers`
  per-session correlation and risking session-count/timing distortion in
  per-session subcommands (idle, struggle). The opt-in merge flag is the lighter
  primitive that touches only the two consumers that want sidechain data.

The audit of all 14 `isSidechain` references confirms exactly two consumers want
sidechain data (`subagents`, `skill-pair`); the rest either deliberately skip
sidechain turns (`main-only`) or count main-thread spawn tool_uses
(`subagent-mix`, `review-trace`) and are unaffected by the default-`False` flag.

### Format-drift canary (catch the next silent format change at point-of-use)

The failure that motivated this fix was **silent**: the format changed and the
tool returned `0`, which a human trusted. A hermetic unit test guards *our* code
against regressions but cannot catch Claude Code changing the on-disk format
again — a fixture pinned to today's shape goes stale exactly as the code did. So
add a runtime canary that validates **live data** against the shapes the script
accepts and warns loudly when they no longer match.

**Signal — corpus-wide cross-check of two independent counts already in the data:**
- `spawns` = main-thread subagent **spawn** tool_uses (`type==assistant`,
  `not isSidechain`, `tool_use` block with `name in ("Agent","Task")` —
  the exact predicate at `cmd_subagent_mix` line 956).
- `sidechain_turns` = subagent **turns** actually read (`isSidechain==true`
  assistant records, post-merge).
- Drift signature: `spawns > 0 and sidechain_turns == 0`. This catches **both**
  drift modes — the `subagents/` path relocating (files never read → 0 turns)
  **and** a field rename like `isSidechain`→something (files read but the filter
  matches 0). On a healthy corpus `sidechain_turns > 0`; on a genuinely
  subagent-free corpus `spawns == 0`; on the old inlined format the sidechain
  turns come from the top-level file so the count is non-zero — none of these
  fire.

**Computed corpus-wide, independent of `--branches`.** Format validity is a
global property of the on-disk data, not a per-branch one. Tallying across the
whole `--projects` corpus (ignoring the branch filter) is what makes the signal
robust: it removes the branch-mismatch false positive (a subagent whose
`gitBranch` differs from its spawning main turn) and the small-sample
zero-turn-subagent false positive (every subagent in the entire corpus producing
zero turns is effectively impossible). A binary `turns==0` test is correct here —
a ratio would only add noise at low counts.

**Placement — both sidechain consumers, via one shared warner.** The canary lives
in `cmd_subagents` **and** `cmd_skill_pair`; scoping it to `subagents` alone would
leave `cmd_skill_pair`'s `follower_sidechain_only` to silently re-zero on the next
drift — the original incident replayed on the sibling. Both already make a full
pass over every record, so each accumulates the two corpus-wide tallies during
that pass and calls a shared `_warn_if_subagent_format_drift(spawns,
sidechain_turns)` before printing.

**Output contract:** warning → `sys.stderr`, **exit code 0**, never interleaved
into the stdout table (so `| column -t` / `> file` of the table stays clean —
matches the file's existing `file=sys.stderr` convention, e.g. `cmd_fail_seq`
line 397). Text is self-explanatory on one line: names the `subagents/` subdir and
states the transcript format may have drifted, so a scrollback reader can act.

**Single-source the drifting discriminators (not a half-migration).** Introduce
two module-scope constants beside the existing format-knowledge constants
(`REVIEW_SKILLS` line 589): `SUBAGENT_SUBDIR = "subagents"` (the new subdir-path
knowledge, referenced by the reader, the canary, and the contract test) and
`_SPAWN_TOOL_NAMES = ("Agent", "Task")` (referenced by `cmd_subagent_mix` line 956
and the canary tally). Do **not** introduce constants for `isSidechain` /
`gitBranch`: those appear in ~14 sites, and a constant used in only the 2 new
sites creates a partial spelling where a reader can't tell which form is
authoritative — worse than leaving the literals. A full 14-site sweep is
out of scope; note the choice in the commit message.

## Critical files

- **`claude/.claude/scripts/transcript-analysis.py`**
  - Module-scope constants beside `REVIEW_SKILLS` (≈589): `SUBAGENT_SUBDIR =
    "subagents"`, `_SPAWN_TOOL_NAMES = ("Agent", "Task")`.
  - `iter_sessions` (≈314–329): add `include_subagents` param + subagent-dir
    read/merge (dir = `jsonl.parent / jsonl.stem / SUBAGENT_SUBDIR`, glob
    `*.jsonl`, guard missing dir) + explanatory comment.
  - `cmd_subagents` (iter_sessions call ≈558): pass `include_subagents=True`;
    accumulate corpus-wide `spawns` / `sidechain_turns`; call the shared warner.
  - `cmd_skill_pair` (function line 991; iter_sessions call ≈1009): pass
    `include_subagents=True`; accumulate the same two tallies; call the shared
    warner.
  - `cmd_subagent_mix` (≈956): reference `_SPAWN_TOOL_NAMES` instead of the inline
    `("Agent","Task")` literal — single-source the discriminator the canary shares.
  - New helpers: `_count_subagent_spawns(records)` (main-thread Agent/Task
    tool_uses) and `_warn_if_subagent_format_drift(spawns, sidechain_turns)`
    (stderr, exit-0, self-explanatory text). Add a one-line comment distinguishing
    the two guards: contract **test** = our-code-vs-our-expectation (runs in CI on
    fixtures); **canary** = our-expectation-vs-live-disk (runtime).
  - **Reuse:** the existing in-loop JSON-decode/`OSError` handling in
    `iter_sessions` — extend it over the subagent files, don't write a second
    reader. `_fam`, `_branch_filter`, `_projects_glob` are unchanged.
- **`claude/.claude/scripts/tests/test_transcript_analysis.py`**
  - Add a fixture helper that writes the **new** layout: a top-level
    `<id>.jsonl` plus `<id>/subagents/agent-*.jsonl` containing
    `isSidechain: true` records. Build on existing `_write_jsonl` / `_asst`.
  - New tests: (a) `subagents` counts sidechain turns from a subagent file and
    splits them by model family on the right branch; (b) `iter_sessions` default
    (`include_subagents=False`) still ignores subagent files — guards the
    zero-blast-radius claim; (c) `skill-pair` `follower_sidechain_only`
    increments when the leader is main-thread and the follower lives in a
    subagent file; (d) missing/empty `subagents/` dir is handled.
  - Canary tests: (e) drift case — a corpus with main-thread spawns but **no**
    readable subagent turns warns: assert the warning is in
    `capsys.readouterr().err` and `.out` contains only table rows (stdout stays
    clean); (f) no false positive — a subagent-free corpus (no spawns) and an
    old inlined-format corpus (sidechain turns in the top-level file) both emit
    **no** warning.
  - **Accepted-shapes contract test:** pin the structural invariants the reader
    depends on — subagent dir path `<id>/SUBAGENT_SUBDIR/*.jsonl`, and per record
    type: all subagent records carry `isSidechain` / `gitBranch` / `type`;
    `message.model` only on `assistant`-type records (a `user`-type subagent
    record has none). Derive the pinned shape from a real captured record, not the
    idealized `_asst` helper, so the test locks the actual on-disk contract.
  - Keep the existing inlined-format sidechain tests (e.g.
    `test_leader_plus_sidechain_only_follower`) unchanged — they confirm the
    merge stays additive and the old inlined format still counts.

## Verification

1. `../../../.venv/bin/pytest claude/.claude/scripts/tests/test_transcript_analysis.py`
   (from the implementation worktree root; `../../../` resolves to the repo root).
2. `.venv/bin/ruff check claude/.claude/scripts/`.
3. End-to-end repro against real data:
   `python3 ~/.claude/scripts/transcript-analysis.py subagents --branches day-285-sentry-edge-pr1`
   → expect a `sidechain` row with dozens of turns (not 0), main row still ~208.
4. Regression spot-check that other subcommands are unchanged: run `fail-seq` and
   `struggle` for the same branch and confirm output matches pre-change (they must
   not gain subagent turns — brief §7).
5. `skill-pair` smoke check on a corpus known to have sidechain followers →
   confirm the `Side` column can now be non-zero.
6. Canary smoke check: `subagents 2>/dev/null` against the real corpus prints the
   clean table to stdout with no warning (healthy data); confirm the warning text
   is well-formed by reading the `_warn_if_subagent_format_drift` unit test output.

## Out of scope

- Per-`agentType` breakdown of the `subagents` output using the `.meta.json`
  sidecar (check-runner vs Explore vs reviewer split) — useful, but an
  enhancement beyond the false-negative fix.
- Changing how `fail-seq` / `struggle` / `review-trace` / `subagent-mix` treat
  subagent turns (brief §7) — they keep `include_subagents=False`.
- Auditing other branches for the same symptom — the fix is general; the
  `day-285-sentry-edge-pr1` repro is sufficient validation.
- Any harness-level concern about the transcript format itself — we are adapting
  the reader to the format, not changing the format.
- A full sweep replacing the ~14 `isSidechain` / `gitBranch` string literals with
  named constants — only `SUBAGENT_SUBDIR` and `_SPAWN_TOOL_NAMES` (the
  discriminators the new code and canary actually share) are introduced now;
  a partial field-name constant would be worse than the literals.
- A standalone `validate-format` subcommand — rejected for its forgot-to-run
  failure mode; the inline point-of-use canary fires when the answer is consumed.
- The canary reports a corpus-wide "format looks wrong" signal; it does not
  pinpoint which branch or session drifted. Accepted — it is a soft pointer for a
  human to investigate, not a precise locator.
