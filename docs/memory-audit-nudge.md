# Memory-Store Audit Nudge

## What the hook does

`nudge-memory-store-audit.sh` is registered on `SessionStart`, matcher
`startup` only. Once per session start it measures the total byte size of
every Claude Code auto-memory store on the machine — every
`<config-dir>/projects/*/memory/` directory (`<config-dir>` means
`$CLAUDE_CONFIG_DIR` when set, else `~/.claude`) — and, when that total
crosses a count-scaled threshold, emits a `hookSpecificOutput.additionalContext`
advisory naming `/memory-store-audit`. It never blocks, never edits a
memory file, and never names a project directory.

| Constant | Default | Overridable via |
|---|---|---|
| Per-project byte threshold | 25600 (25 × 1024) | `MEMORY_AUDIT_NUDGE_PER_PROJECT_BYTES` |
| Re-arm band | 25600 | `MEMORY_AUDIT_NUDGE_REARM_BYTES` |

The fire rule: `total_bytes >= 25600 × (number of project stores holding any
memory content)`. What counts as a project store "holding content":

- A store counts if the byte-measurement scan finds at least one file under
  it, including a zero-byte `MEMORY.md`.
- An empty or absent `memory/` directory does not count.

Malformed override values (empty, a literal zero, non-digit, zero-padded, or
9+ digits) fall back to the shipped default rather than letting the
threshold degrade toward zero or negative.

**Why this threshold.** The single primary source available is Anthropic's
own memory documentation:

> "The first 200 lines of `MEMORY.md`, or the first 25KB, whichever comes
> first, are loaded at the start of every conversation... Topic files like
> `debugging.md` or `patterns.md` are not loaded at startup. Claude reads
> them on demand..."

— [Claude Code — How Claude remembers your project](https://code.claude.com/docs/en/memory)

That sentence fixes 25 KB as the amount of `MEMORY.md` content a session
loads at startup, and establishes that topic files reach a session only
through an explicit recall read. The hook sums every file in a store, topic
files included, so it measures accumulated store size rather than what any
one session loads: a store holding more than 25 KB is, by construction,
mostly content that never loads at startup. 25600 is 25 × 1024; the quoted "25KB"
carries no unit definition, so 25000 would also be a defensible reading —
the ~2.4% spread between the two is immaterial at this granularity, but it
is a choice, not something the source specifies. The scaling by project
count (rather than a single fixed byte threshold) makes the rule read as
"the average store has outgrown one startup load's worth of memory," not "you have
many projects" — a fixed machine-wide threshold would fire earlier the more
projects a machine accumulates, punishing breadth rather than bloat.

**Why this spacing.** The re-arm band uses the same constant and the same
source as the threshold itself: another session's-worth of growth since the
last nudge. Unlike `HANDOFF_NUDGE_REARM_SPACING`, this figure is not
corpus-tuned — the only corpus available for tuning is this machine's own
memory stores, which span private projects, so a percentile read off them
would carry private-corpus provenance into a public repo. The derivation is
analytic, and `MEMORY_AUDIT_NUDGE_REARM_BYTES` is overridable for the same
reason `HANDOFF_NUDGE_ABS_CAP` is: this repo's own chosen ceiling, not a
vendor-specified figure.

The byte measurement scans `<config-dir>/projects/*/memory` as glob-expanded
`find` start points — never `find <config-dir>/projects -path '*/memory/*'`,
which would walk every session transcript in the tree. `find … -type f -exec
wc -c {} +` feeds a single `awk` pass that computes both the byte total and
the project-store count together, excluding any `wc`-emitted "total" row
from the sum (`-exec … {} +` can batch into multiple `wc` invocations, each
capable of emitting its own total row). Each per-file line's path already
carries its own project's memory directory as a prefix
(`.../projects/<project>/memory/...`), so the same `awk` pass buckets by
that prefix to count which project stores hold at least one file. This
avoids a separate per-project-directory `grep` pass, which would scale with
project count rather than file count.
`find -H` follows a `memory` start point that is itself a symlink to a
directory, so a store kept behind a symlink still counts. This also means a
symlinked `memory` start point can fold in bytes from outside the resolved
config directory entirely — including a different `CLAUDE_CONFIG_DIR`-scoped
account's tree on the same machine, if that account's `memory` happens to be
the symlink target. A symlink met during traversal is skipped: without `-L`,
the `-type f` test matches only a real file. A path containing a newline is pruned, because `wc` prints paths
verbatim and a newline in one would let a directory name forge extra rows in
the byte total.

The project-store count buckets each file by the text up to its rightmost
`/memory/`, on the assumption that a store is flat. A store containing a
subdirectory literally named `memory` therefore counts as two stores.

A `find` or `awk` pass killed by its 5s cap (`_lib.sh`'s cap-kill statuses)
is discarded: the hook exits without touching the state file, the log, or
stdout. Any other nonzero `find` status, such as an unreadable or vanished
file, keeps the measurement. That partial listing can undercount and lower the
recorded high-water mark, which causes one extra fire on the next session
start. A `find` that dies on a signal outside the cap-kill statuses (exit 129,
130, or 141) is not discarded either, and can undercount the same way. The 5 seconds is `_lib_capped`'s shared default.

A machine-global state file, `<config-dir>/.memory-audit-nudge-fired`,
records the byte total at the last fire. The hook re-arms once the current
total reaches that recorded value plus the re-arm band. A recorded value
above the current total (a partial audit that didn't drop the store below
threshold) is rewritten down to the current total without firing, so the
next genuine growth re-arms from there rather than having to clear the old
high-water mark again. A missing or malformed state-file record fires
rather than suppresses — the fail-toward-firing posture `.handoff-nudge`'s
own marker corruption handling already established.

## How to disable

Set the `memory_audit_nudge` config key to `false` to suppress nudges globally:

```bash
printf 'memory_audit_nudge = false\n' >> "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/claude-config.toml"
```

Set it back to `true` to re-enable — see [`docs/config-file.md`](config-file.md) for the file's hand-edit contract:

```bash
printf 'memory_audit_nudge = true\n' >> "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/claude-config.toml"
```

The hook checks this key before any filesystem scan. The grammar:

- `memory_audit_nudge = false` disables the nudge.
- With no row for the key, the legacy `<config-dir>/.memory-audit-nudge-disabled` file disables it.
- An explicit `memory_audit_nudge = true` row beats the legacy file, so the re-enable recipe above (which appends `true`) also switches the legacy file off.
- A non-bare value such as `"false"` is a malformed line, and the nudge keeps firing.
- A schema-read failure (unreadable, absent, or truncated `config-keys.psv` row) keeps the nudge enabled.

## Why the filing rules are shaped this way

`memory-store-audit/SKILL.md` Step 4 states each rule; the reasons live here.

- **Body by file.** `--body-file` makes the bytes the engineer approved, the
  bytes the redaction gate scans, and the bytes GitHub receives the same
  bytes, and no memory-derived text passes through shell interpretation.
- **Flag order (`--body-file` before `--title`).** Per
  `claude/.claude/hooks/deny-private-project-refs.sh`'s own header bullet on
  xargs tokenization failure, `extract_body_source_paths`'s `xargs -n1`
  tokenizer drops only the paths after a failing token, so `--body-file` goes
  first to stay read even when a newline in the title later breaks
  tokenization.
- **Title allowlist.** The title has to sit inside a single-quoted shell
  argument, so allowing only ASCII letters, digits, spaces, and `-_.,:/`
  rules out quote, backtick, `$`, backslash, and control-character injection.
- **Started inside the checkout, no `cd` or `-R`.** The redaction gate scopes
  itself by the hook process's cwd, so a chained `cd` or a `-R` aimed at the
  checkout from outside it escapes the scan.
- **`GH_REPO` check.** `GH_REPO` retargets `gh issue create` but not
  `gh repo view`, so the approval prompt's repo line can name a repo other
  than the one that receives the issue.
- **Showing the login.** The authenticated login tells the engineer which
  account the issue will be filed under before they approve it.
- **No retry.** `gh issue create` has no dedupe flag, so retrying a failed
  call can create a duplicate issue.
- **Temp directory.** `mktemp -d` gives each item an unpredictable,
  per-item directory outside the checkout, so no other file or process can
  pre-place or share the body path.

Quarantine is not deletion: a memory file that holds a credential stays on
disk under `<config-dir>/.memory-audit-quarantine/` until the engineer removes it.

## Log location

The hook appends one line per fire to
`<config-dir>/.memory-audit-nudge.log`:

```
nudged total=<bytes> projects=<n> threshold=<bytes> source=startup
```

Counts and byte totals only — never a project directory name or path. The
log is append-only and not rotated automatically. Trim it periodically if
disk space is a concern: `> "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.memory-audit-nudge.log"`.

## Known limitations

- **No mid-session firing.** The nudge arrives at the next session start,
  not at the moment a write pushes a store over threshold — `SessionStart`
  is the cheapest event that reaches every session for a signal that only
  changes on memory writes.
- **The advisory text is cross-project.** The measured total is a
  machine-wide aggregate across every project's memory store, not anything
  scoped to the triggering session's own project. The nudge surfaces inside
  whatever project's session happens to trigger it, regardless of which
  project that is.
- **No `--check` query mode.** Nothing consumes this hook's number
  programmatically, unlike the handoff nudge's `--check`, which `plan-it`
  and the `handoff` skill call mid-session.
- **The scan is uncapped without `timeout(1)` or `gtimeout(1)`.** Stock macOS
  without Homebrew coreutils is the common case. The scan then runs `find` and
  `awk` uncapped, as `_lib_capped_for` does for every other hook. A persistent
  stall then costs up to the registration timeout at every session start, with
  no log line and no state write. `install.sh`'s generic coreutils hint does
  not name this hook.
- **The `"timeout": 10` registration is the only whole-process bound.**
  The value is a repo-chosen ceiling above the 7 s single-cap worst case (5 s
  cap plus 2 s kill grace). Setting it lowers the harness's command-hook
  default. Its unit is seconds, per the
  [hooks reference](https://code.claude.com/docs/en/hooks). Whether the harness cancels a stalled `SessionStart` hook at that value is
  unverified. The hook fails open on every path.
  `_lib_capped` caps the `find`, `awk`, and `jq` processes it wraps, and it
  signals only that process. The `wc` that `find -exec wc -c {} +` spawns is a
  grandchild holding the capture pipe, so a `wc` that ignores or cannot honour
  SIGTERM, such as one stuck in uninterruptible I/O on a hung mount, is not
  bounded by the cap. Such a `wc` keeps the hook alive past every cap. The
  hook makes up to four sequential capped calls (jq parse, `find`, `awk`,
  fire-time `jq`), so the cumulative worst case can exceed one 5 s cap and the
  10 s registration timeout. The initial `projects/*/memory` glob,
  `mkdir -p`, the state and log redirects, the in-shell config reads, and the
  stdin read are not under any cap.
- **A stalled or cap-killed scan is silent.** A run that stalls past the
  registration bound, or whose `find` or `awk` an inner cap kills, yields no
  nudge, no log line, and no state change. The state file is untouched, so the
  next session start retries the scan, and a persistent stall repeats at every
  session start with no signal. Silence cannot distinguish an under-threshold
  run from a discarded one — running the hook by hand against the real config
  dir would write the state file and log when over threshold and consume the
  re-arm band, so check without side effects instead:
  1. Run the hook with `CLAUDE_CONFIG_DIR` set to a throwaway directory whose
     `projects` entry is a symlink to the real `<config-dir>/projects`.
  2. Pass `{"source":"startup"}` on stdin.
  3. Note the baseline: an empty stdin exits in about 1 s without scanning
     and looks healthy.
  4. Treat a run that takes 5 s or longer, or never returns, as a stall — a
     stall can outlast every cap, as the timeout item above describes. The
     kill-switch is the remedy.
- **A stale peak delays the first re-crossing by one band.** The state file is
  rewritten only on a fire or a shrink, never on a below-threshold scan. After
  an audit drops the total below threshold, the old peak persists, so the next
  crossing is absorbed by the shrink rewrite and the nudge fires one
  `REARM_BYTES` of growth later.
- **Threshold tuning is analytic, not corpus-derived.** See "Why this
  threshold" above — no percentile or measured figure from this machine's
  own memory stores can appear here without carrying private-corpus
  provenance into a public repo.
- **The audit's approval pauses and cross-file isolation are prose-only.** No
  hook enforces them; `memory-store-audit/SKILL.md`'s Step 7 "What holds these
  gates" is their single home.
- **Issue filing rests on live per-item approval inside the skill.** No hook
  enforces that pause. Deletion moves an approved file to
  `<config-dir>/.memory-audit-quarantine/` rather than removing it, which
  bounds the blast radius of a skipped approval without new enforcement
  machinery.
- **Quarantined files are restored by hand and never purged automatically.**
  To restore a file, move it from
  `<config-dir>/.memory-audit-quarantine/<audit-start-UTC-timestamp>/<project-dir-name>/`
  back into `<config-dir>/projects/<project-dir-name>/memory/` and re-add its
  `MEMORY.md` index line. Nothing removes the quarantine directory; delete old
  timestamp directories by hand once they are no longer needed.
