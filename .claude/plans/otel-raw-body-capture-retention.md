# Re-arm wire-level API-body capture under a bounded-retention policy

## Context

Re-enable Claude Code's raw request/response body capture on this machine's
personal account under an automated retention bound, so a future live
cold-prompt-cache event is caught at the wire without leaving unredacted
conversation history accumulating on disk indefinitely.

The cold-cache investigation (PR #658, merged) closed on an explicit
non-attribution finding: roughly two-thirds of cold events are explained by
neither the leading content-drift hypothesis, nor any harness-logged
attachment event, nor idle-time TTL expiry. The remaining lead — cold events
arriving in runs at a fixed survivor boundary, implying some region
immediately after the first cache breakpoint varies per turn — can only be
resolved by seeing the assembled wire-format request, which transcripts do
not record. `OTEL_LOG_RAW_API_BODIES=file:<dir>` is the documented
instrument for that.

The instrument is currently **off**: `claude/.claude/settings.local.json`
has no `env` key, and no capture directory exists. A memory note describes
capture as always-on with manual pruning; that description does not match
the machine's state, and its "on a schedule the engineer decides" clause was
never converted into a schedule. Re-arming without fixing that is how the
unbounded state recurs.

## Approach

Turn capture back on in the gitignored, machine-local settings file, pointed
at a directory **inside** `~/.claude/`, and bound it with a new `SessionStart`
hook that prunes that one fixed directory by age and by total size. The
retention constants live in the hook; the policy's rationale lives in a
durable repo doc; the memory note shrinks to the machine-local facts and the
one standing instruction no repo artifact can carry.

Two decisions drive the rest of the design:

**The capture directory goes at `~/.claude/otel-raw-bodies/`, not at a
sibling path.** `docs/design-decisions.md:225` (§19) established
`chmod 700 ~/.claude` at install time as "one choke point" that "blocks
traversal into every subdirectory at once," precisely to avoid a
per-directory permission recipe. A sibling directory falls outside it: this
machine's `umask` is `0022` and `~/.claude` is `drwx------`, so a sibling
would be created `755` with `644` files — world-readable to every local
account, holding unredacted conversation history. Placing it inside
`~/.claude/` inherits the existing protection and adds no hardening code.
`~/.claude` is a real directory with per-file stow symlinks, not a folded
symlink into this repo, so nothing lands inside the public checkout.

**The hook reads no configuration to locate the directory.** It does not
resolve `OTEL_LOG_RAW_API_BODIES` from the environment or from any settings
file; the path is a constant. A config-derived path flowing into
`find -delete` is this design's primary blast-radius risk, and pinning
collapses that validation into a constant-equality check — the
directory's existence becomes the only signal needed. This also removes the
`jq` dependency, the malformed-JSON failure mode, and any dependence on
whether hooks inherit `env` values.

The hook is not env-free, though: it reads `OTEL_PRUNE_MAX_BYTES` for the
size ceiling (see item 1). That variable can only widen or narrow *how much*
is deleted from an already-fixed, already-name-scoped file set — it cannot
change *which path* is walked — so it does not reintroduce the risk pinning
removed. Scope any future "reads no config" reasoning to the path
specifically.

Alternatives set aside: a fixed enable-then-auto-revert window (a cold run
can occur outside it, forcing repeated re-arming); manual per-session
flipping (requires predicting an unpredictable event); a manual check cadence
(the approach that already failed — the undecided schedule is why this task
exists).

### Assumption ledger

**Root problem.** Wire-level capture is the only instrument that can identify
what occupies the per-turn-varying region after the first cache breakpoint,
but enabling it writes unredacted full conversation history to disk with no
vendor-provided bound.

**Givens** — conditions this design treats as fixed and cannot dissolve:

| # | Given | Why beyond reach |
|---|---|---|
| 1 | `file:<dir>` mode performs no rotation, truncation, or size capping of its own | Vendor-owned: the documentation specifies the write path and file naming, and states nothing about lifecycle |
| 2 | Captured bodies are unredacted and contain the entire conversation history | Vendor-owned: enabling "implies consent to everything `OTEL_LOG_USER_PROMPTS`, `OTEL_LOG_TOOL_DETAILS`, and `OTEL_LOG_TOOL_CONTENT` would reveal" |
| 3 | Capture is written by Claude Code's exporter, below every redaction hook this repo ships | Vendor-owned: `redact-credential-values.sh` and its peers act on tool-call and hook payloads, not on the OTEL exporter's body assembly |

Keeping activation out of the committed settings file is *not* a given — this
plan could put it there and chooses not to. See **Out of scope**.

**Mechanisms:**

| Mechanism | Justification | anchors |
|---|---|---|
| `env` block in `~/.claude/settings.local.json` pointing at `~/.claude/otel-raw-bodies` | Turns the instrument on for this machine alone, inside the §19 permission choke point | `anchors: root` |
| New `SessionStart` hook `prune-otel-raw-bodies.sh`, pinned to that one path | Supplies the age and size bound the vendor does not, and shortens how long unredacted history sits at rest | `anchors: row1`, `anchors: row2` |
| New `docs/otel-raw-body-pruning.md`, referenced from `docs/hooks.md` | Durable home for the policy and for the exposure classes the retention window is chosen against; repo convention is one doc per hook | `anchors: row2`, `anchors: row3` |
| Rewrite the memory note | Activation lives in a gitignored file, so no repo artifact can record whether capture is on for a given machine | `anchors: row2` |
| `test_prune_otel_raw_bodies.py`, including deny-path cases | The bound is the whole point of the change, and the hook deletes irreversibly | `anchors: row1` |
| A test asserting the OTEL keys are absent from the committed `settings.json` | This change makes one specific mistake — writing the `env` block into the shared file — enable unredacted capture for every stow user | `anchors: root` |

**Over-powered-primitive check on the hook.** Lighter primitives in this
system, and why each fails:

1. *Append the prune to an existing `SessionStart` hook.* Fewer files, but
   all five existing entries are single-purpose and the repo pairs each hook
   with its own doc; folding an unrelated responsibility into one breaks both
   conventions.
2. *A plain utility script under `claude/.claude/scripts/`, run by hand.*
   This is the manual cadence the Context section describes as having already
   failed — an undecided schedule is the failure this task exists to correct.

Heavier options rejected in the other direction: a `launchd`/`cron` job (a
system-level scheduler outside this repo's versioning, for work needing no
schedule of its own), and a `Stop` hook (fires per assistant turn — far
hotter than a bound measured in days).

**Assumptions:**

| # | Assumption | Tag |
|---|---|---|
| 1 | Retention is 7 days; a size ceiling is wanted on top of the age cap | `[engineer-verified]` — both chosen in this session |
| 2 | `file:<dir>` writes one flat file per request (`<uuid>.request.json`) and per response (`<request_id>.response.json`) | `[verified: code.claude.com/docs/en/monitoring-usage]` |
| 3 | Capture requires `CLAUDE_CODE_ENABLE_TELEMETRY=1` and a non-`none` `OTEL_LOGS_EXPORTER` | `[verified: same]` |
| 4 | `~/.claude` is mode `700`; this machine's `umask` is `0022`; the data volume has hundreds of GiB free | `[verified: ls -ld, umask, df this session]` |
| 5 | `chmod 700 ~/.claude` at install is the repo's deliberate single choke point for everything beneath it | `[verified: docs/design-decisions.md:225]` |
| 6 | BSD `find -mtime +7` deletes only files aged 8+ days; `-mtime +6` is the correct expression for a 7-day bound | `[verified: probe run this session — files aged 5/6/7/8/9 days seeded; `+7` matched 8d and 9d only, `+6` matched 7d, 8d, 9d]` |
| 7 | Only BSD `find`/`stat` are available (no `gfind`, no GNU `-printf`) | `[verified: `command -v gfind` and `stat -f` probes this session]` |
| 8 | Five `SessionStart` hook entries already exist, so a sixth follows convention | `[verified: claude/.claude/settings.json:96-140]` |
| 9 | `_lib.sh` provides `_lib_capped` / `_lib_capped_for` (5s default) and no `settings.json` hook entry sets a harness-level timeout | `[verified: claude/.claude/hooks/_lib.sh:17,27,38]` |
| 10 | `_LIB_SIZE_THRESHOLD_BYTES` is a plain constant, **not** env-overridable — so an overridable ceiling is a new shape here, adopted for testability rather than by precedent | `[verified: claude/.claude/hooks/_lib.sh:1063]` |
| 11 | No repo tooling enumerates `~/.claude` subdirectories generically, so a new directory there collides with nothing | `[verified: grep over scripts/ and _config_dir.py this session]` |
| 12 | A 5 GiB ceiling is a sufficient analysis window | `[unverified]` — daily capture volume has never been measured; chosen as <1% of row-4 headroom and revisable from the size the hook reports |
| 13 | Claude Code creates the capture directory itself on first write | `[unverified]` — if it does not, arming must `mkdir -p` it; verification step 4 settles this before the policy is relied on |

## Critical files

Implement in the order below. **The hook ships before capture is armed** —
the reverse leaves a window where capture runs unbounded.

### 1. `claude/.claude/hooks/prune-otel-raw-bodies.sh` (new)

```
# hook-class: informational
```

Header must state, as the two single-purpose `SessionStart` siblings do
verbatim: *"Exit 0 always — this hook must not block session startup."*

- **Path is a constant**, `$HOME/.claude/otel-raw-bodies`. Nothing is read
  from the environment or from a settings file to locate it — no `jq`, no
  `OTEL_LOG_RAW_API_BODIES` lookup. (The size ceiling below is the hook's one
  env read.)
- Exit 0 if the path does not exist, is not a directory, or **is a symlink**
  — matching `track-permission-prompts.sh`'s existing symlink-refusal
  precedent for its own write target.
- Age pass: `-mtime +6`, not `+7` (assumption 6 — `+7` would silently give an
  8-day window). Scope every `find` with `-maxdepth 1 -type f` and to the
  vendor's two filename shapes (`*.request.json`, `*.response.json`), so a
  stray file in the directory is never deleted and the blast radius is
  bounded by name as well as by path — this is also what makes a plain text
  pipeline safe, since those names contain no spaces or newlines.
- Size pass: if the directory still exceeds the ceiling, delete oldest-first
  until under it. `stat -f` for mtime and size (BSD; `-printf` is GNU-only),
  `sort -n` stated explicitly rather than relying on epoch seconds being
  fixed-width.
- **Run the walks with the capture directory as the working directory**, so
  only vendor-controlled leaf names flow through the `stat`/`sort` pipeline
  and the home-directory prefix never appears in it. Scoping by filename
  (above) makes the leaf names safe, but the *prefix* is not this repo's to
  constrain: the hook ships to every stow user, and a home path containing
  whitespace would otherwise misparse the pipeline's fields. Fixing it
  structurally beats asserting that home paths are clean.
- Ceiling as `${OTEL_PRUNE_MAX_BYTES:-<5 GiB literal>}`, **validated as a
  positive integer before use and silently falling back to the literal
  otherwise**. Without that check, a typo'd value in a shell profile
  silently changes what an unconditional, irreversible delete removes. This
  override is a new shape in this suite (assumption 10), adopted so the
  eviction algorithm is testable without writing gigabytes; the production
  literal gets its own assertion, and the doc must state the override exists
  for tests and is unsupported in a real shell profile.
- Wrap the `find`/`stat` walks in `_lib_capped_for`. There is no harness-level
  timeout backstop (assumption 9), so an unwrapped walk on a stalled
  filesystem hangs session startup.
- `set -e` discipline per `claude/.claude/rules/shell-script-conventions.md`:
  guard arithmetic accumulation (`|| true` or `: $(( ))`), use `rm -f --`,
  treat a `stat` on an already-deleted file as non-fatal (a concurrent
  session can delete underneath this walk), and make the final exit
  unconditionally 0 wherever a loop stops.
- Report the post-prune size on `systemMessage`, not
  `hookSpecificOutput.additionalContext` — the number exists for the engineer
  to retune assumption 12, not for the model to read every session.
- Never pass `-L` to `find` or `stat`; add one sentence saying so, so a later
  edit does not add it for convenience.

### 2. `claude/.claude/settings.json`

Register as a sixth `SessionStart` entry with matcher `startup` — **not** an
absent matcher. `check-branch-divergence.sh` scopes to `startup` for the
stated reason that its target is on-disk state rather than session-scoped;
directory age and size are the same kind of state, and firing the walk on
every `compact` adds latency mid-session, when the user least wants it.

Do **not** add an `env` block here. That would enable unredacted capture for
every stow user.

### 3. `claude/.claude/hooks/tests/test_prune_otel_raw_bodies.py` (new)

Subprocess-with-`isolated_home` pattern per `conftest.py`; see
`test_track_permission_prompts.py` for a `SessionStart` example.

Allow-path: files past the cap deleted, recent ones kept; size pass evicts
oldest-first down to an injected ceiling; a file *inside* the age window
still evicted by the size pass.

Deny-path and negative assertions. A suite for an irreversible delete has to
prove what the hook leaves alone, not only what it removes:

- Directory absent, and directory present-but-a-symlink → no-op, exit 0.
- Files outside the capture directory (a canary in `$HOME` and in a sibling
  directory) survive a normal run.
- A file inside the directory not matching the two vendor filename shapes
  survives regardless of age.
- One file unreadable/undeletable mid-pass → hook still exits 0 and still
  prunes the rest.
- A separate assertion pinning the **production** ceiling literal, so an
  injected ceiling in the other tests cannot hide a units error in the real
  constant.
- `OTEL_PRUNE_MAX_BYTES` set to a non-numeric value, a negative number, and
  empty → each falls back to the production literal rather than deleting
  everything or nothing.

Age fixtures use `touch -mt` at cutoff−2 and cutoff+2 days, never at the
exact boundary: BSD `-mtime` truncates to 24-hour periods from the hook's own
invocation time, so a boundary fixture races the wall clock between fixture
setup and subprocess start — a real flake source under `pytest-xdist`.

### 4. `claude/.claude/hooks/tests/test_settings_no_raw_body_capture.py` (new)

Assert `OTEL_LOG_RAW_API_BODIES`, `CLAUDE_CODE_ENABLE_TELEMETRY`, and
`OTEL_LOGS_EXPORTER` are absent from the committed
`claude/.claude/settings.json`. Small, and it backstops the one mistake this
change makes consequential without reopening the broader `GUARDED_KEYS_JSON`
design question.

### 5. `docs/otel-raw-body-pruning.md` (new) + a reference line in `docs/hooks.md`

Repo convention is one doc per hook. Content: what the capture is; the
retention rule and how to change it; and three exposure facts the retention
window is chosen against —

- Capture sits below every redaction hook this repo ships (given 3), so a
  credential that ever appeared in tool output or pasted content lands
  verbatim, with no redaction layer in the path.
- **The bound is enforced only at session start.** A session left running
  for days is unbounded for its duration; the ceiling is eventual, not live.
- The bound governs the live filesystem only. A backup taken before a prune
  retains its copy outside the policy — and because capture lives inside
  `~/.claude`, any tool that backs up or syncs that directory as a unit
  sweeps the bodies along with it by default, which a bespoke sibling path
  could have been excluded from.
- `OTEL_PRUNE_MAX_BYTES` exists for the test suite; setting it in a real
  shell profile is unsupported.
- How to turn capture off, and that the hook keeps running harmlessly
  afterward, draining the directory rather than needing separate removal.
- The supported capture path is the pinned one; pointing
  `OTEL_LOG_RAW_API_BODIES` elsewhere means no pruning applies.

### 6. `~/.claude/settings.local.json` — arm capture **last**

Gitignored; the symlink resolves into the main checkout, not this worktree,
so a worktree-local edit to `claude/.claude/settings.local.json` would be a
different, ignored file that never reaches the live config. Paths under
`$HOME/.claude/` are exempt from the worktree write gate
(`require-worktree-for-file-writes.sh:87-96`), and the repo's
"never `>>` through a stow symlink" rule explicitly carves out gitignored
runtime state.

```json
"env": {
  "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
  "OTEL_LOGS_EXPORTER": "console",
  "OTEL_LOG_RAW_API_BODIES": "file:<absolute path to ~/.claude/otel-raw-bodies>"
}
```

Write the literal absolute path rather than a `$HOME`-interpolated one —
whether Claude Code expands variables in settings `env` values is unverified,
and the file is machine-local so a literal costs nothing. The path must match
the hook's constant exactly or pruning silently will not apply; verification
step 4 confirms it does.

### 7. `~/.claude/projects/<project>/memory/` — rewrite the note

Everything the current note prescribes becomes false, and its `MEMORY.md`
index line ("no automated cleanup, check periodically") is an
action-prescribing hook whose prescription would be wrong. Rewrite to carry:

- The machine-local state: capture is active on this account, bounded.
- An **action** clause, not a bare fact — treat files under the capture
  directory as unredacted conversation history and do not read or publish
  them. A purely declarative note fails the behavior test.
- The one forward-looking instruction from the current note that no doc
  reproduces (docs load on demand; memory loads every session): when the
  cold-cache investigation concludes, *offer* to remove the `env` block
  rather than leaving capture running with no purpose.

Rename the file to drop "needs-pruning" and update the index line, keeping
the guard on the action per index discipline and staying under 150
characters. Writing memory requires activating the
`ai-instruction-and-memory-files` gate first
(`marker.sh activate memory-skill`).

**Reuse:** `_lib.sh` for bootstrap and `_lib_capped_for`; `conftest.py`'s
`isolated_home`; the `[ -d … ] || exit 0` guard shape already in five hooks;
`track-permission-prompts.sh`'s symlink refusal.

## Verification

1. `../../../.venv/bin/pytest claude/.claude/hooks/tests/` — new tests plus
   the full suite for regressions.
2. `../../../.venv/bin/ruff check claude/.claude/` and
   `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck`.
3. Confirm the hook is inert before arming: start a session with no capture
   directory present and confirm exit 0, no output, no startup delay.
4. Arm capture, start a fresh session, and confirm `~/.claude/otel-raw-bodies/`
   exists and holds `*.request.json` / `*.response.json` files — settling
   assumption 13, and confirming the settings path and the hook's constant
   agree.
5. Confirm the bound bites, with fixtures rather than by waiting a week:
   seed backdated files, start a session, confirm the old ones are gone and
   recent ones remain.
6. Record the directory's size after a day of normal use and revise the
   assumption-12 ceiling if 5 GiB proves badly sized. This is follow-through,
   not optional — the ceiling is the only unmeasured number in the design.

## Out of scope

- Analyzing captured wire data. This change re-arms the instrument; reading
  what it catches is a separate task once a live cold event occurs.
- Editing the existing sections of `docs/case-studies/cold-cache-attribution.md`
  — a merged historical record.
- Extending capture to any other account on this machine.
- **Enabling capture for other stow users.** This plan could put the `env`
  block in the committed `claude/.claude/settings.json` and reach every stow
  user; it deliberately does not. That would write unredacted conversation
  history to disk on machines whose owners never asked for it. See
  `.claude/plans/cost-attribution-integrity.md`, Step 0, for the same
  reasoning applied to a comparable retention setting. The hook and doc still
  ship to everyone —
  inert unless a directory exists at the pinned path.
- **Pruning a non-default capture directory.** A stow user who points
  `OTEL_LOG_RAW_API_BODIES` somewhere other than the pinned path gets no
  pruning. Reading their config to find it is exactly the blast-radius
  surface this design removed; the doc names the supported path instead.
- **Raised to the reviewer, not bundled:** `env` is absent from
  `GUARDED_KEYS_JSON` in `guard-settings-session-keys.sh:85-92`, so an `env`
  block hand-written into the committed settings file is not caught
  pre-commit. Item 4 above backstops these three keys specifically; guarding
  the whole `env` key is over-broad — it would block legitimately shareable
  variables — so the general fix is a design question of its own.
