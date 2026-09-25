# Long-Turn Nudge Hook

## What the hook does

`nudge-long-turn-subagent.sh` is registered on `PostToolBatch` only and fires solely for subagent dispatches, never the main session. See [`docs/hooks.md`](hooks.md)'s own entry for the trigger, the incremental per-dispatch scan mechanics, and the sampling cadence.

## Per-fire cost

The sampled cadence bounds the `tail`/`jq -s` scan's cost specifically — O(fire count) drops to O(fire count / `SAMPLE_CADENCE`) — not total per-fire hook cost. Two `_lib_capped_for`-wrapped forked external-process calls run on every subagent fire regardless of sampling: the input-parsing `jq` call and the invocation-counter's `wc -c` read. Total per-fire cost still scales linearly with raw fire count via those two always-on forks. `mkdir -p "$MARKER_DIR"` forks only on a fire that finds the directory missing, so the common steady-state fire skips it once the directory exists from the dispatch's first fire. Only the `MARKER_DIR` cleanup sweep (`find … -mtime +30 -delete`) is gated to the sampled fire alongside the scan.

## Known limitations

- **A same-session fire can leak the scan lock directory, reclaimed only by the periodic `MARKER_DIR` sweep (up to 30 days).** Two independent races produce this:
  - A fire killed by SIGKILL while holding the lock blinds the nudge for the rest of that dispatch until the lock is reclaimed. The likely root cause is an unwrapped `rmdir`/`mktemp` hang hitting a harness execution timeout.
  - A `mkdir`/trap race:
    - A `mkdir` that completes after its own timeout's SIGTERM or SIGKILL leaves a cap-kill exit status with the directory still created. `_lib_capped_for`'s header in `claude/.claude/hooks/_lib.sh` lists the statuses.
    - A BusyBox `timeout` that SIGTERM-kills the `mkdir` leaks the directory the same way.
    - BusyBox returns the `mkdir`'s own status when the `mkdir` completes and then races the cap. Nothing leaks in that case.
    - A trappable signal landing between `mkdir` succeeding and `LOCK_DIR` being assigned leaves the EXIT trap closing over an empty `LOCK_DIR` and never running `rmdir` on the just-created directory.

    Every case above except the BusyBox own-status one leaks the directory the same way, subject to the same 30-day reclaim.
- **Worst case for a sampled fire is ~26-30s.**
  - That is 13 to 15 independently-capped `_lib_capped_for 2` calls on a fire that scans and emits the nudge, at 2s each when every call honors SIGTERM.
  - The count is 13 when the scanned window's last byte is a newline. It is 15 when `_lib_advance_offset_past_complete_lines` takes its slow path, which is the common case once windowing engages, since the window's truncation point (`scan_window_end < current_size`) rarely lands on a newline.
  - Bounded only by the harness's own hook timeout, not indefinitely, when both `timeout` and `gtimeout` are absent from PATH, because `_lib_capped_for` then runs the command uncapped. [`docs/handoff-nudge.md`](handoff-nudge.md) gives that timeout.

  See [`docs/handoff-nudge.md`](handoff-nudge.md) § "Known limitations" for the SIGKILL grace's effect on these figures and for the sibling hook's own worst case.
- **A jq timeout driven by content rather than backlog size retries the identical window forever**, since the 2s `_lib_capped_for` cap applies to every retry.
- **A record whose own line exceeds `MAX_SCAN_WINDOW_BYTES` force-advances the offset past it**, undercounting that record's turn (see `_scan_turn_count_cached` for the resync mechanics).
- **A dispatch can permanently outpace the scan when transcript growth exceeds the average per-fire catch-up rate (`MAX_SCAN_WINDOW_BYTES` / `SAMPLE_CADENCE`, 200,000 bytes).**
  - A single sampled fire's own catch-up capacity is the full `MAX_SCAN_WINDOW_BYTES`, since only one fire in `SAMPLE_CADENCE` actually scans.
  - That rate isn't validated against real transcript growth, so `TURN_COUNT` can end up chronically undercounted with no visible signal.
  - A losing fire during lock contention contributes nothing at all to that fire's scan, not a partial scan.
  - That outpaced-scan-rate undercounting compounds across a burst of same-session fires rather than resetting each fire.
- **`_lib_capped_for`'s `timeout` wrapper escalates to SIGKILL after a 2s grace, but is still not a hard bound.**
  - A child process stuck in uninterruptible disk-wait against an unresponsive mount is genuinely unbounded — `timeout` cannot deliver SIGTERM or SIGKILL to a process in that state.
  - This applies to every `_lib.sh`-capped call across the hook suite, not only this hook.
  - This is currently the only hook that fires unconditionally on every subagent-dispatch `PostToolBatch`, so it is the first place this backstop's limits are load-bearing on every fire rather than a gated subset.
- **`_scan_turn_count_cached`'s windowed `head` read isn't wrapped in `_lib_capped_for`. Only the piped `jq -s` call is.** Low severity: the unwrapped read targets a freshly-created, `MAX_SCAN_WINDOW_BYTES`-bounded temp file, not an unbounded or externally-controlled source.
- **The transcript-existence check, the fired-marker check, and the invocation-counter append carry no timeout protection at all, on every fire rather than just the sampled one.** `[ ! -f "$TRANSCRIPT_PATH" ]`, `[ -e "$FIRED_MARKER" ]`, and `printf '.' >> "$INVOCATION_MARKER"` are bash builtins and redirections, so they can't be wrapped in `_lib_capped_for` the way an external process can. A stalled or unresponsive `$CONFIG_DIR` mount hangs any of these on every fire, not only the sampled 1-in-10 that reaches the scan. Accepted, disclosed gap: wrapping a bash builtin in a capped subshell is a heavier fix than this narrow-risk edge case warrants.
