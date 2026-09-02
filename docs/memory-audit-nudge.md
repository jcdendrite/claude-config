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
10+ digits) fall back to the shipped default rather than letting the
threshold degrade toward zero or negative.

**Why this threshold.** The single primary source available is Anthropic's
own memory documentation:

> "The first 200 lines of `MEMORY.md`, or the first 25KB, whichever comes
> first, are loaded at the start of every conversation... Topic files like
> `debugging.md` or `patterns.md` are not loaded at startup. Claude reads
> them on demand..."

— [Claude Code — How Claude remembers your project](https://code.claude.com/docs/en/memory)

That sentence fixes 25 KB as the amount of memory content a session
actually loads, and establishes that everything past `MEMORY.md` reaches a
session only through an explicit recall read. A store holding more than one
load budget's worth of content is, by construction, mostly content not
paying its way through the load path. 25600 is 25 × 1024; the quoted "25KB"
carries no unit definition, so 25000 would also be a defensible reading —
the ~2.4% spread between the two is immaterial at this granularity, but it
is a choice, not something the source specifies. The scaling by project
count (rather than a single fixed byte threshold) makes the rule read as
"the average store has outgrown one session's memory budget," not "you have
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
A symlink inside a `memory/` directory is skipped: `find`'s default
(non-`-L`) `-type f` test does not match a symlink, only a real file.

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

Touch the kill-switch file to suppress the nudge globally:

```bash
touch "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.memory-audit-nudge-disabled"
```

Remove the file to re-enable:

```bash
rm "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.memory-audit-nudge-disabled"
```

The hook checks for this file before any filesystem scan.

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
- **No `--check` query mode.** Nothing consumes this hook's number
  programmatically, unlike the handoff nudge's `--check`, which `plan-it`
  and the `handoff` skill call mid-session.
- **The nudge never fires on a machine with neither `timeout(1)` nor
  `gtimeout(1)` on `PATH`.** Stock macOS without Homebrew coreutils is the
  common case. Rather than let `_lib_capped_for` run the find+wc measurement
  uncapped — which for this `SessionStart` hook risks holding session start
  itself open on a stalled filesystem, with no bound — the hook checks
  `_lib_timeout_binary_available` immediately after sourcing `_lib.sh`,
  before even the `.source` filter, and exits 0 before the scan when
  neither binary resolves: no scan, no log line, no state-file write.
  `install.sh`'s existing coreutils hint (printed at
  onboarding) does not name this hook or this consequence — it describes
  hooks that run *uncapped*, not one that stops firing *entirely*.
- **Threshold tuning is analytic, not corpus-derived.** See "Why this
  threshold" above — no percentile or measured figure from this machine's
  own memory stores can appear here without carrying private-corpus
  provenance into a public repo.
- **Issue filing and deletion approval are session-instruction-gated, not
  hook-enforced.** `/memory-store-audit` pauses on a blocking
  `AskUserQuestion` immediately before each `gh api` issue-filing call and
  each memory-file quarantine move, but no hook or marker makes either pause
  unskippable — nothing in this repo enforces it. Deletion bounds a skipped
  pause's blast radius structurally: an approved file moves to
  `<config-dir>/.memory-audit-quarantine/` rather than being removed, so a
  skipped approval costs a quarantined file, not an unrecoverable one. No
  equivalent bound exists for a skipped issue-filing pause.
