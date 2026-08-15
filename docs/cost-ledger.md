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

Ledger data lives outside this repo, at `$CLAUDE_CONFIG_DIR/cost-ledger.md`
by default (`~/.claude/cost-ledger.md` when `CLAUDE_CONFIG_DIR` is unset) —
a local, per-account file that `--record` creates on first use and never
enters this repo's git tree. Set `COST_LEDGER_PATH` to an absolute path to
record somewhere else instead (a private repo, a synced location) — export
it from a persistent shell init file, not an ad hoc session variable, since
an unset override on a later invocation silently falls back to the default
path instead of erroring.

When more than one Claude account is declared in scope (via
`~/.claude/transcript-config-dirs`), `--record` unions their corpora into a
single row as usual, unless doing so would write that union into a path git
could commit — it refuses (exit 2) only when the resolved ledger path sits
inside a git working tree. The default path isn't one, so this refusal is
dormant unless `COST_LEDGER_PATH` is pointed at a git-tracked location. The
check walks up from the ledger path to the nearest existing ancestor and
asks git directly, so it can't see two git-invisible ways the same figure
could still end up shared: a cloud-sync folder (Dropbox, iCloud, OneDrive)
syncing `$CLAUDE_CONFIG_DIR` or `COST_LEDGER_PATH`'s directory, and a
bare-repo dotfile manager (yadm-style, tracking `$HOME` via
`--git-dir`/`--work-tree` flags rather than an in-tree `.git`). Neither is
closed by this check; avoid both if the ledger's multi-account union
content should stay off a shared destination.
