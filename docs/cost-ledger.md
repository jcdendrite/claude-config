# Cost-trend ledger

A durable, append-only record of this repo's own volume-invariant workflow
cost and efficiency metrics — one row per week per machine, appended by
`transcript-analysis.py cost-ledger --record`. Claude Code deletes
transcripts on a rolling window (`cleanupPeriodDays`, default 30 days), so a
week not recorded while it is still observable cannot be recovered later.
See `.claude/plans/cost-trend-ledger.md` for the full design rationale,
including the error-path contracts the recorder implements and why several
candidate columns (a corpus fingerprint, a per-turn denial denominator) were
left out.

## Schema

| Column | Source | Why it is in the ledger |
|---|---|---|
| `week` | ISO week label | Join key |
| `machine` | operator-supplied `--machine-label` | Distinguishes rows from different machines when merged via git |
| `rates` | `_PRICING_FETCH_DATE` | Rows computed under different price tables are not comparable |
| `usd` | `cost-trend` | Volume, not efficiency — present for context, not for scoring |
| `context_pct` | `_compute_cost_trend_data` (`context_class_dollars`) | Context-class (cache read + both cache-write tiers) dollar share of the week's spend — the ~88%-of-the-bill thesis |
| `opus_pct` | `cost-trend` | Model-routing discipline |
| `ge200k_pct` | `cost-trend` (`context_over`) | Dollar share of turns whose context crossed the >=200k bucket — cost-trend's own existing "Context%" column, a distinct metric from `context_pct` |
| `denials` | `review-trace --deny-summary` | Gate friction, raw count |
| `reviewer_gap_pp` | `reviewer-yield` | Percentage-point gap between the findings-found and zero-finding cited-path edit rates |
| `note` | operator-supplied `--note` | What changed in the workflow that week |

`denials` and `reviewer_gap_pp` are scoped to the same Monday 00:00:00 UTC
through the following Monday 00:00:00 UTC (exclusive) ISO-week window as the
other computed columns, not the corpus lifetime. `reviewer_gap_pp` is left
empty when either the findings-found or zero-finding side has zero measured
(Active) dispatches that week — an unmeasured comparison, not a 0pp gap.

`denials` is a raw count and therefore volume-sensitive in the same way
`usd` is; read it alongside the percentage columns, not as a standalone
score.

`--machine-label` is rejected when it case-insensitively equals this
machine's POSIX hostname (`socket.gethostname()`) — but that check does not
cover macOS's separate device/computer name (`scutil --get ComputerName`),
which can differ from the hostname. An operator whose device name differs
from its hostname should independently avoid choosing a label that matches
either.

## Data

| week | machine | rates | usd | context_pct | opus_pct | ge200k_pct | denials | reviewer_gap_pp | note |
|---|---|---|---|---|---|---|---|---|---|
