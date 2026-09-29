# Add Claude Sonnet 5.5 to the pricing rate table

## Context

Goal: price `claude-sonnet-5-5` turns so PR cost blocks stop excluding them as unpriced. `_MODEL_BASE_INPUT_RATES` in `pricing.py` has no entry for it. Its turns therefore land in the "unpriced turns / tokens excluded" count and the PR cost figure understates spend. Same failure and same fix as the Opus 5.5 addition (#1087).

## Approach

Add one row, `"claude-sonnet-5-5": 2.00`, to `_MODEL_BASE_INPUT_RATES`. `_model_rates` derives output, cache-write, and cache-read rates from that base, and `_MODEL_RATE_EXPIRES` derives from the dict's keys, so no other edit is needed. Sonnet 5.5 takes the standard 0.1x cache-read multiplier, so it gets no `_CACHE_READ_MULTIPLIER_OVERRIDES` entry.

Alternatives set aside: prefix-matching model IDs to price unknown `claude-sonnet-5-*` variants automatically. That would silently price future models at a guessed rate and remove the loud unpriced-model backstop; the exact-match table is deliberate.

Assumption ledger:
- Root problem: `claude-sonnet-5-5` has no rate row, so its turns are excluded from priced spend.
- Given: `claude-sonnet-5-5` is the string Claude Code writes to `message.model`. `[verified: grep of the local transcript store for "model":"claude-sonnet-5[-0-9a-z]*", 2026-09-28 — the exact string "claude-sonnet-5-5" occurs, with no dated-suffix variant]`.- Row 1 (anchors: root): base input $2/MTok. `[verified: platform.claude.com/docs/en/about-claude/pricing, fetched 2026-09-28: "Claude Sonnet 5.5 | $2 / MTok | $2.50 / MTok | $4 / MTok | $0.20 / MTok | $10 / MTok"]`.
- Row 2 (anchors: row1): no cache-read override needed. `[verified: same page, "All other models use the standard 0.1x multiplier"; footnotes 1 and 2 name only Fable 5.1, Mythos 5.1, and Opus 5.5]`. $2 × 0.1 = $0.20 matches the published cache-hit price.
- Row 3 (anchors: root): the nudge hook already resolves the 1M window for this ID. `[verified: nudge-handoff-near-context-cap.sh:123, arm "claude-sonnet-5-*" matches "claude-sonnet-5-5"]`. No hook change.

## Critical files

- `claude/.claude/scripts/transcript_analysis/pricing.py`: add the row after `"claude-sonnet-5"`.
- `claude/.claude/scripts/tests/test_transcript_cost.py`: add `TestSonnet55Pricing` after `TestOpus55Pricing`, with two tests and the same class-docstring caveat (tests validate rate arithmetic, never the model-ID string).
  - `_model_rates("claude-sonnet-5-5")` asserts the vendor-page literals: input $2.00, output $10.00, cache_write_5m $2.50, cache_write_1h $4.00, cache_read $0.20. The $0.20 literal also proves no cache-read override applies.
  - `_price_turn("claude-sonnet-5-5", usage)` for 1M input tokens returns non-None `dollars` and `unpriced == 0`, asserting the turn is priced rather than excluded. It carries no dollar-amount assertion: test 1's `input == 2.00` already pins the rate.
  - No differential against `claude-sonnet-5` (identical $2 base and 0.1x, so it would compare $0.20 to $0.20) and no `_cost_report` end-to-end test (it exists to prove an override flows to the report, which does not apply).

Single `code-writer` dispatch is unnecessary; the change is two small hunks in one phase.

## Verification

`.venv/bin/python3 claude/.claude/scripts/select-tests.py`, then `.venv/bin/ruff check claude/.claude/`.

## Out of scope

- Re-fetching `_PRICING_FETCH_DATE` and re-verifying every other row. Only the new row was checked (2026-09-28), so bumping the date would overstate verification.
