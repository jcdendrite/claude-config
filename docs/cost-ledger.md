# Cost-trend ledger

A local, per-account, append-only record of this repo's own
volume-invariant workflow cost and efficiency metrics — one row per week per
machine, appended by `transcript-analysis.py cost-ledger --record`. Claude
Code deletes transcripts on a rolling window (`cleanupPeriodDays`, default
30 days), so a week not recorded while it is still observable cannot be
recovered later; the ledger survives past that deletion, but — unlike a
git-tracked file — has no built-in backup or cross-machine replication of
its own. See `.claude/plans/cost-trend-ledger.md` for the full design
rationale, including the error-path contracts the recorder implements and
why several candidate columns (a corpus fingerprint, a per-turn denial
denominator) were left out.

## Schema

| Column | Source | Why it is in the ledger |
|---|---|---|
| `week` | ISO week label | Join key |
| `machine` | operator-supplied `--machine-label` | Distinguishes rows from different machines when an operator compares ledger files by hand across them |
| `rates` | `_PRICING_FETCH_DATE` | Rows computed under different price tables are not comparable |
| `usd` | `cost-trend` | Volume, not efficiency — present for context, not for scoring |
| `context_pct` | `_compute_cost_trend_data` (`context_class_dollars`) | Context-class (cache read + both cache-write tiers) dollar share of the week's spend — the ~88%-of-the-bill thesis |
| `opus_pct` | `cost-trend` | Model-routing discipline |
| `ge200k_pct` | `cost-trend` (`context_over`) | Dollar share of turns whose context crossed the >=200k bucket — cost-trend's own existing "Context%" column, a distinct metric from `context_pct` |
| `denials` | `review-trace --deny-summary` | Gate friction, raw count |
| `reviewer_gap_pp` | `reviewer-yield` | Percentage-point gap between the findings-found and zero-finding cited-path edit rates |
| `note` | operator-supplied `--note` | What changed in the workflow that week -- must be printable ASCII (no em dashes, curly quotes, or accented characters) and must not contain markdown link/image syntax |

`context_pct` does not separate a warm cache read from an idle-gap TTL-expiry
rebuild write — both land in the same context-class dollar share; see
`cache-rebuild` for the write-specific breakdown.

`denials` and `reviewer_gap_pp` are scoped to the same Monday 00:00:00 UTC
through the following Monday 00:00:00 UTC (exclusive) ISO-week window as the
other computed columns, not the corpus lifetime. `reviewer_gap_pp` is left
empty when either the findings-found or zero-finding side has zero measured
(Active) dispatches that week — an unmeasured comparison, not a 0pp gap.

`denials` is a raw count and therefore volume-sensitive in the same way
`usd` is; read it alongside the percentage columns, not as a standalone
score.

`--machine-label` is checked only against the POSIX hostname
(`socket.gethostname()`), not macOS's separate `ComputerName` — avoid a
label matching either.

## Data

Ledger data lives outside this repo, at `$CLAUDE_CONFIG_DIR/cost-ledger.md`
by default (`~/.claude/cost-ledger.md` when `CLAUDE_CONFIG_DIR` is unset) —
a local, per-account file that `--record` creates on first use and never
enters this repo's git tree. Set `COST_LEDGER_PATH` to an absolute path to
record somewhere else instead (a private repo, a synced location) — export
it from a persistent shell init file, not an ad hoc session variable, since
an unset override on a later invocation silently falls back to the default
path instead of erroring.

`--record` unions multiple accounts (declared via
`~/.claude/transcript-config-dirs`) into a single row as usual, unless doing
so would write that union into a ledger path git could commit — then it
refuses (exit 2). The default path isn't one, so this refusal is dormant by
default; it can't detect a cloud-sync folder or bare-repo dotfile manager
(e.g. yadm) also syncing that path, so avoid pointing `COST_LEDGER_PATH` at
either when the union must stay private.
