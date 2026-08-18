# Raw API-body capture: retention

## What the capture is

`OTEL_LOG_RAW_API_BODIES=file:<dir>` is a Claude Code environment variable
([vendor docs](https://code.claude.com/docs/en/monitoring-usage)) that
writes the untruncated, wire-format request and response JSON for every API
attempt to `<dir>` — one `<uuid>.request.json` and one
`<request_id>.response.json` per attempt. It requires
`CLAUDE_CODE_ENABLE_TELEMETRY=1`; no `OTEL_LOGS_EXPORTER` value is needed
for `file:` mode specifically — confirmed by direct testing, files write
locally regardless of any configured exporter. It is the only instrument
that shows the assembled request as sent; transcripts do not record it.
See `docs/case-studies/cold-cache-attribution.md` for the investigation
this capture supports.

This hook bounds `~/.claude/otel-raw-bodies/` whenever something writes to
that pinned path. It does not arm capture itself, and reads no
configuration to decide whether to — capture is off by default, and
arming, if any, is a separate machine-local concern outside this hook's
scope. `test_settings_no_raw_body_capture.py` backstops one specific risk:
the committed `settings.json` must never carry the `env` block that would
enable capture for every stow user.

## The retention rule

`prune-otel-raw-bodies.sh` (`SessionStart`, matcher `startup`) prunes
`~/.claude/otel-raw-bodies/` by two bounds, oldest-first:

- **Age**: files older than 7 days.
- **Size**: if the directory still exceeds a 5 GiB ceiling after the age
  pass, oldest files are deleted until it doesn't.

Both constants are literals in the hook. To change the retention window,
edit `prune-otel-raw-bodies.sh` directly.

## Exposure facts the retention window is chosen against

- **Capture sits below every redaction hook this repo ships.** It is
  written by Claude Code's own OTEL exporter, not by a hook this repo
  controls — `redact-credential-values.sh` and its peers act on tool-call
  and hook payloads, not on the exporter's body assembly. A credential that
  ever appeared in tool output or pasted content lands in a captured body
  verbatim.
- **The bound is enforced only at session start.** A session left running
  for days is unbounded for its duration; the ceiling is eventual, not
  live.
- **The bound governs the live filesystem only.** A backup taken before a
  prune retains its copy outside the policy. Because the capture directory
  lives inside `~/.claude` (see `docs/design-decisions.md` §19 for why),
  any tool that backs up or syncs that directory as a unit sweeps the
  bodies along with it by default — a bespoke sibling path could have been
  excluded from such a tool, but would have lost the `chmod 700 ~/.claude`
  permission choke point in exchange.

## Other notes

- `OTEL_PRUNE_MAX_BYTES` overrides the size ceiling. It exists so the test
  suite can exercise the eviction algorithm without writing gigabytes of
  fixture data; setting it in a real shell profile is unsupported, and an
  invalid value (non-numeric, negative, zero) silently falls back to the
  5 GiB production literal rather than changing what an irreversible delete
  removes.
- **Turning capture off**: remove whatever `env` configuration armed
  `OTEL_LOG_RAW_API_BODIES`. The hook keeps running harmlessly afterward —
  it becomes a no-op the moment the directory no longer exists or is fully
  drained by its own age/size passes — so no separate removal step is
  needed.
- The supported capture path is the pinned one. A stow user who points
  `OTEL_LOG_RAW_API_BODIES` at a different directory gets no pruning: the
  hook reads no configuration to locate the directory (a config-derived
  path flowing into an irreversible `find`-based delete is exactly the
  blast-radius risk pinning the path removes), so an unpinned directory
  accumulates without bound.
