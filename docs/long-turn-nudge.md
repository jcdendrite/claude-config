# Long-Turn Nudge Hook

## What the hook does

`nudge-long-turn-subagent.sh` is registered on `PostToolBatch` only and fires solely for subagent dispatches, never the main session. See [`docs/hooks.md`](hooks.md)'s own entry for the trigger, the incremental per-dispatch scan mechanics, and the sampling cadence.

## Known limitations

- **A same-session fire can leak the scan lock directory, reclaimed only by the periodic `MARKER_DIR` sweep (up to 30 days).** Two independent races produce this:
  - A fire killed by SIGKILL while holding the lock blinds the nudge for the rest of that dispatch until the lock is reclaimed. The likely root cause is an unwrapped `rmdir`/`mktemp` hang hitting a harness execution timeout.
  - A `mkdir`/trap race:
    - A `mkdir` that completes after its own timeout's SIGTERM leaves exit 124 with the directory still created.
    - A trappable signal landing between `mkdir` succeeding and `LOCK_DIR` being assigned leaves the EXIT trap closing over an empty `LOCK_DIR` and never running `rmdir` on the just-created directory.

    Both leak the directory the same way, subject to the same 30-day reclaim.
- **Worst case for a sampled fire is ~20-22s.**
  - Hit when the window is truncated (`scan_window_end < current_size`) — the common case once windowing engages, since the truncation point rarely lands on a newline.
  - Unbounded when both `timeout` and `gtimeout` are absent from PATH.

  The sibling hook's own documented figure is 14-20s in [`docs/handoff-nudge.md`](handoff-nudge.md), for comparison.
- **A jq timeout driven by content rather than backlog size retries the identical window forever**, since the 2s `_lib_capped_for` cap applies to every retry.
- **A record whose own line exceeds `MAX_SCAN_WINDOW_BYTES` force-advances the offset past it**, undercounting that record's turn (see `_scan_turn_count_cached` for the resync mechanics).
- **A dispatch can permanently outpace the scan when transcript growth exceeds the average per-fire catch-up rate (`MAX_SCAN_WINDOW_BYTES` / `SAMPLE_CADENCE`, 200,000 bytes).**
  - A single sampled fire's own catch-up capacity is the full `MAX_SCAN_WINDOW_BYTES`, since only one fire in `SAMPLE_CADENCE` actually scans.
  - That rate isn't validated against real transcript growth, so `TURN_COUNT` can end up chronically undercounted with no visible signal.
  - A losing fire during lock contention contributes nothing at all to that fire's scan, not a partial scan.
  - That outpaced-scan-rate undercounting compounds across a burst of same-session fires rather than resetting each fire.
- **`_lib_capped_for`'s `timeout` wrapper is a soft SIGTERM-only backstop, not a hard bound.**
  - A child process stuck in uninterruptible disk-wait against an unresponsive mount is genuinely unbounded — `timeout` cannot deliver SIGTERM to a process in that state.
  - This applies to every `_lib.sh`-capped call across the hook suite, not only this hook.
  - This is currently the only hook that fires unconditionally on every subagent-dispatch `PostToolBatch`, so it is the first place this backstop's limits are load-bearing on every fire rather than a gated subset.
- **`_scan_turn_count_cached`'s windowed `head` read isn't wrapped in `_lib_capped_for`. Only the piped `jq -s` call is.** Low severity: the unwrapped read targets a freshly-created, `MAX_SCAN_WINDOW_BYTES`-bounded temp file, not an unbounded or externally-controlled source.
