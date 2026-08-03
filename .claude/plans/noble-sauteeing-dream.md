# Fix GH-556: per-model context-window threshold for the handoff nudge

## Context

`nudge-handoff-near-context-cap.sh` nudges the model to suggest `/handoff` once
a session's estimated carried tokens cross `THRESHOLD=120000`, sourced in its
own comment as "60% of a 200k context window" for Claude 4.x models. Every
model in real current use — verified today at
`platform.claude.com/docs/en/about-claude/models/overview` — now ships a
native 1M-token window (Opus 5, Sonnet 5, Opus 4.8/4.7/4.6, Sonnet 4.6, Fable
5, Mythos 5; no beta header or opt-in required). Only four legacy 200k models
remain runnable: Haiku 4.5, Sonnet 4.5, Opus 4.5, Opus 4.1. So the constant
now fires at **12%** of the real window, not 60%, while the injected string
still asserts "60%" as fact. On the last 30 days this crossed for 216/290
sessions (74.5%) and drove a 55:6 handoff-to-compaction ratio — filed as
[GH-556](https://github.com/jcdendrite/claude-config/issues/556).

Two more claims turned out to need the same treatment during research:

- **README.md:413** cites "Anthropic best practices... cite 60%" and links
  `code.claude.com/docs/en/best-practices` — that exact page (fetched today)
  contains no "60%" and no compaction-percentage guidance anywhere. A second,
  independent stale citation for the same number.
- The injected string also claims handoff is **"~25% cheaper per turn than
  waiting for auto-compaction."** This traces to the original commit
  (`c92fadf`, PR #330) with no source anywhere. Real analysis of the last 30
  days (Sonnet 5, priced by token class) shows $/turn *does* scale with
  context (0–100k mean $0.042 → 200–300k mean $0.086 → 600–800k mean $0.215),
  but a fresh/resumed session's cold-start turn costs mean $0.153/median
  $0.126 — **more** than a typical single turn even at 200–300k context,
  because cache-write (cold start) prices at 1.25×–2× base input vs.
  cache-read's 0.1× for a continuing session. Handoff has real amortized-over-
  several-turns overhead; it is not a flat win, so no percentage belongs in
  the string.

Intended outcome: the threshold becomes genuinely 60% of the *resolved*
model's window (not a new arbitrary number — the original stated intent,
restored), the false claims are removed rather than replaced with new
unverifiable ones, and the two doc citations pointing at nonexistent sources
are corrected.

## Approach

### The core mechanism: resolve the model, look up its window, no new subprocess

The hook already sums the four usage fields out of `$USAGE_BLOCK` (itself
built by an earlier jq call at current lines 79–83) via one further `jq`
call (current lines 86–92). That same JSON blob's `.message.model` field
carries the bare model ID (e.g. `"claude-sonnet-5"` — confirmed via real
transcript grep; no `[1m]` suffix, that only appears on
`.toolUseResult.resolvedModel`, a different, subagent-only field). Extending
this second jq pass to also emit `.message.model` costs nothing — same
subprocess count, so `test_latency_under_500ms` is unaffected (confirmed by
`staff-platform-engineer` review: a fifth jq output line, a `case`, and
`$(( ))` arithmetic are negligible against the test's own documented
~25–50ms typical shell+jq startup, ~150–200ms under loaded-runner jitter,
against a 500ms bound).

**Exact read protocol (pinned, not left to implementation-time judgment —
`staff-platform-engineer` review flagged the abstract description below as
under-specified at the one place newline-handling matters):**

```bash
ESTIMATE=""
MODEL=""
{
  IFS= read -r ESTIMATE
  IFS= read -r MODEL
} < <(
  printf '%s\n' "$USAGE_BLOCK" \
    | jq -r '
        ((.message.usage.cache_read_input_tokens // 0)
       + (.message.usage.cache_creation_input_tokens // 0)
       + (.message.usage.input_tokens // 0)
       + (.message.usage.output_tokens // 0)),
        (.message.model // "" | tostring | gsub("[^a-zA-Z0-9._-]"; ""))
      ' 2>/dev/null
) 2>/dev/null || true
if [ -z "$ESTIMATE" ]; then
  exit 0
fi
```

Three properties this pins, each closing a real gap the review found:
- **`ESTIMATE` is read first, `MODEL` second.** A newline embedded in
  `.message.model` (or a non-string value, which `jq -r` on an object would
  render multi-line) can only truncate `MODEL` — it can never desync
  `ESTIMATE`, which the existing `-z` gate depends on.
- **`tostring | gsub("[^a-zA-Z0-9._-]"; "")` sanitizes `MODEL`.** `.message.model`
  is harness-written, not user input, so injection risk is already low — but
  the filter is nearly free and closes a real corruption path: an unsanitized
  `MODEL` value containing a newline could otherwise write a bogus
  `schema-drift`-prefixed line into `~/.claude/.handoff-nudge.log`, and
  `transcript-analysis.py`'s `handoff-ratio` counts drift lines by
  `startswith("schema-drift")`.
- **Both variables are pre-initialized to `""`** (mirroring the existing
  four-field read block at lines 36–39), so a failed `read` leaves `MODEL`
  empty rather than unset — consistent given strict mode is deliberately off
  in this file.

**Alternative considered and rejected:** shelling out to
`transcript-analysis.py`'s model→price table, or building a cached
`~/.claude/model-windows.json` refreshed from the Models API
(`/docs/en/api/models/list`, which returns `max_input_tokens` — the
authoritative, rot-free source). Both are heavier than this bug requires: the
first adds a python3 subprocess to a <500ms hot path; the second needs
credentials the hook may not have and a cache-staleness policy of its own.
Worth a follow-up issue, not this fix.

### Window table: source-cited, dated, minimal

```bash
# Context window in tokens per model ID; THRESHOLD is 60% of it.
# Source: https://platform.claude.com/docs/en/about-claude/models/overview,
# fetched 2026-08-03; re-verify by 2026-11-03.
# Verified 200k: Haiku 4.5, Sonnet 4.5, Opus 4.5, Opus 4.1. Verified 1M:
# Fable 5, Mythos 5, Opus 5, Opus 4.8/4.7/4.6, Sonnet 5, Sonnet 4.6.
# An unlisted ID takes the 1M default; see docs/handoff-nudge.md for why.
case "$MODEL" in
  claude-haiku-4-5*|claude-sonnet-4-5*|claude-opus-4-5*|claude-opus-4-1*)
    CONTEXT_WINDOW=200000 ;;
  *)
    CONTEXT_WINDOW=1000000 ;;
esac
THRESHOLD=$(( CONTEXT_WINDOW * 60 / 100 ))
```

Only the 200k arm is enumerated — a second arm listing the 1M models would be
dead weight with an identical body; the verified list lives in the comment,
where a re-verifier actually needs it. Exact-prefix globs only (`claude-
haiku-4-5*`, not `claude-haiku-*`): asserting only what's verified means a
future Haiku 5 at some other window takes the safe default instead of being
silently mis-capped by a broad glob — the same failure this fix corrects.
`case` is first-match, so no broad prefix (e.g. `claude-opus-4-*`) may be
added later without checking it doesn't shadow Opus 4.5/4.1 sitting between
the 1M Opus entries.

**Unknown/missing model ID → defaults to the 1M window, not 200k.** This is
the one point where I'm overriding my own earlier lean toward "conservative
means smaller." The nudge's payload is a claim injected into every session's
context, and the two defaults fail differently:

| Default | On a future/unrecognized model | Injected claim |
|---|---|---|
| 200k | A 1M model fires at 12% of its real window in ~75% of sessions — reproduces GH-556 exactly | Asserts "60% of the model window" when it's actually ~12% — the exact bug this PR fixes |
| **1M (chosen)** | A hypothetical future 200k model stays silent; auto-compaction (~83.5%, unchanged, a separate mechanism) still catches it | Asserts nothing false — it just says nothing |

4 of 5 currently-offered models are 1M, and every model shipped since Sonnet
4.6 is 1M — an unrecognized future ID is very likely 1M too. This hook is
`hook-class: informational`, not the safety control (auto-compaction is,
untouched here); "fail toward silence" is the safer asymmetry when the
alternative failure mode is re-injecting a false claim into every session.
The silent-failure risk is made observable rather than hidden (below).

**Ordering**: the window lookup runs *after* the existing schema-drift check
(current lines 102–112), so a record with all-zero usage (`<synthetic>`,
11 occurrences in the local 30-day corpus) exits on that path before any
threshold logic runs, unchanged from today.

### The injected string: true by construction, no invented percentage

`"Context is near 60% of the model window"` becomes literally true once the
threshold is actually 60% of the resolved window — keep it (reword "near" →
"past", since the comparison is `-lt`/fires-at-or-above). Drop `"~25%
cheaper"` and state the real conditional relationship instead:

> Context is past 60% of this model's context window. If the current task is
> not close to done, suggest running /handoff to the user — it captures state
> in a /tmp file and resumes in a fresh session. Per-turn cost rises with
> carried context, but a fresh session pays a one-time rebuild cost first, so
> handoff pays off over the next several turns rather than immediately. If the
> task is nearly complete, ignore this and finish.

No live numbers are interpolated into the string (e.g. "620,000 of
1,000,000") — that would add `--arg` plumbing to the one path that must never
fail, for a value GH-556 doesn't ask to be surfaced there.

### Rot-detection instead of a runtime staleness banner

`transcript-analysis.py`'s `cost` subcommand prints a `STALE PRICING` banner
because it's invoked on demand for a human to read. This hook has no runtime
reader — its only output channel is `additionalContext`, consumed by the
model on every prompt. Injecting a "this table may be stale" notice into
every session would spend tokens on a maintainer-facing message, give the
model a reason to distrust the nudge, and (since it would key off wall-clock
date) make hook stdout non-deterministic — turning every stdout-asserting
test into a ticking time bomb, the exact hazard `cmd_cost` avoided by taking
`today` as an explicit parameter rather than reading the clock, a seam a
one-shot bash hook has no equivalent for.

Instead: the dated source comment above (checked by a human), plus extending
the `nudged` log line with `model=` and `window=` so a *recognized* model's
resolved window is greppable against real sessions.

**This does not make the unknown-model default (1M) self-detecting, and the
plan must not claim it does** — `staff-platform-engineer` review caught this
as a real contradiction: a future 200k-ish model that resolves to the 1M
default may never accumulate enough tokens to cross 600,000, so it never
fires, so no `nudged` line is ever written for it. The silent-failure mode
the 1M default deliberately accepts (§ above) is exactly the one this log
line cannot see. Building a mechanism that *would* see it (a log line on the
below-threshold path, or a dedicated `unknown-model` event with its own
one-shot marker) is a materially bigger change than this bug forces — it
would add a marker file, touch `cleanup-handoff-nudge-marker.sh` and its
test, and log on every prompt rather than only on fire, none of which GH-556
asks for. The honest disposition is to rely on the dated source comment
(re-verify by 2026-11-03) as the actual staleness control, and state the gap
plainly rather than papering over it with a claim the mechanism can't back:

```bash
printf 'nudged session=%s est=%s model=%s window=%s\n' \
  "$SESSION_ID" "$ESTIMATE" "$MODEL" "$CONTEXT_WINDOW" >> "$NUDGE_LOG" 2>/dev/null || true
```

Checked: nothing else parses the `nudged` line format (`transcript-
analysis.py`'s `handoff-ratio` only checks `startswith("schema-drift")`), so
appending fields is safe.

### Two incidental doc/README corrections, in scope

- **`docs/handoff-nudge.md:60`** ("Threshold is a rough estimate... 120,000-
  token threshold is based on a 200k context window... adjust it locally if
  you use smaller-context models") directly contradicts the fixed behavior —
  smaller-context models are now handled automatically. This is a behavior
  description, not a preserved record (CLAUDE.md Axis 3 doesn't protect it),
  so it must be rewritten, not left standing: replace with the limitation
  that actually remains (the window table is hardcoded and dated; an
  unlisted ID takes the 1M default; `model=`/`window=` on `nudged` lines is
  how to check it against reality).
- **`README.md:413`** — keeping this fix in scope, listed under an
  "Incidental edits" PR section per CLAUDE.md's Axis-1 scope rule (small,
  non-cosmetic, visible value). The claim is verifiably false (fetched the
  cited URL myself; no "60%" anywhere on it), and it is the stated
  justification for the exact constant this PR re-derives — shipping the
  hook fix while README still cites a nonexistent vendor source for the same
  number would leave the PR internally incoherent to a reviewer. Fix: drop
  the false attribution, state the real basis (this repo's own chosen
  fraction, now computed against the resolved model's actual window), point
  to `docs/handoff-nudge.md`.

## Critical files

- **`claude/.claude/hooks/nudge-handoff-near-context-cap.sh`** — extend the
  existing usage-summing jq pass to also emit `.message.model` (one extra
  `read`, same subprocess count); add the window-lookup `case` block after
  the schema-drift check; replace the injected string; extend the `nudged`
  log line and its header-comment documentation (line 18).
- **`claude/.claude/hooks/tests/test_nudge_handoff_near_context_cap.py`** —
  19 existing tests, all built around a `"model": "claude-sonnet-4-6"`
  fixture and the old `120000` constant. Under the new logic that model
  resolves to the 1M window (threshold `600000`), so every fixture whose
  token sum was calibrated to cross the *old* 120,000 (lines 99–115: 135000;
  117: 135000; 131: 125000; 147: 125000; 160: 125000; 199: 130000 on its
  second record; 248: 125000; 270: 135000) must be rescaled to a value at or
  above 600,000, or it silently stops crossing the new threshold and the test
  passes for the wrong reason. `test_below_threshold_is_silent` (88, 50000),
  `test_partial_usage_block_falls_to_below_threshold` (320, 500),
  `test_schema_drift_logs_and_exits`/`test_schema_drift_only_logs_once_per_
  session` (217, 230 — both all-zero, unaffected by the threshold change),
  and the fail-open/missing-input/latency tests (173, 180, 190, 240, 301,
  336, 354) need no numeric change.

  Add module constants (`LARGE_WINDOW=1_000_000`, `SMALL_WINDOW=200_000`,
  `LARGE_THRESHOLD=600_000`, `SMALL_THRESHOLD=120_000`,
  `ABOVE_LARGE=650_000`) so no test hand-computes, and extend
  `_assistant_record` with a `model: str | None = "claude-sonnet-4-6"`
  parameter (`None` omits the key entirely, for the missing-field case) plus
  a `_record_totalling(total, *, model=...)` helper for the many tests that
  only need "one record whose four fields sum to X."

  New tests, each independently discriminating (a boundary test alone can
  pass against a wrong window, so pair fires-at/silent-below for every
  case):
  - Parametrized fires-at-exactly-threshold and silent-one-below, across
    all eight known model IDs (four 1M, four 200k) — the Opus 4.5/4.1 vs
    4.6/4.7/4.8 rows are the prefix-shadowing guard against a careless broad
    `case` arm.
  - `test_old_120k_constant_no_longer_fires_on_1m_models`: `claude-sonnet-5`
    at exactly 135,000 (the value that fired under the old constant) → now
    silent. The direct GH-556 regression test.
  - Unknown model ID, missing `model` key, and `"model": null` — all three
    take the 1M default (599,999 silent / 600,000 fires).
  - A dated-snapshot-suffix ID (`claude-sonnet-5-20260601`) resolves to the
    same window as its dateless form — pins the trailing-`*` globs.
  - `nudged` log line contains `model=` and `window=` matching the firing
    case.
  - Injected context string contains no `"25%"` substring (regression guard
    against the removed claim reappearing).
  - `<synthetic>` model with all-zero usage still takes the schema-drift
    path, not the window/threshold path — pins the ordering constraint.

- **`docs/handoff-nudge.md`** — line 5 (mechanism description) rewritten for
  per-model resolution with a compact window→threshold table; line 29
  (`nudged` log format) gets `model=<id> window=<n>`; line 60 (known
  limitations) rewritten to state plainly that the window table is hardcoded
  and dated (source URL + fetch date + re-verify-by date), that an unlisted
  model ID silently takes the 1M default, and — corrected per the platform
  review above — that this silent default is **not** self-detecting: a
  future smaller-window model mis-resolved to 1M may never cross threshold
  and therefore never appears in the log at all; the dated comment, checked
  manually, is the actual control. Line 58 is unaffected.
- **The hook's own header comment** (not just the external doc) gets a new
  "Known limitations" bullet alongside the existing `claude -p` one (current
  lines 25–27), stating the same fact in one sentence — per this repo's
  hook-review convention that a script's header lists the gaps it doesn't
  close, not only the accompanying doc.
- **`README.md:413`** — citation and percentage corrected per above.
  `README.md:166`'s generic hook-table row needs no change.

## Verification

```bash
# From the worktree (venv lives three levels up per repo convention)
../../../.venv/bin/pytest claude/.claude/hooks/tests/test_nudge_handoff_near_context_cap.py -q
../../../.venv/bin/pytest claude/.claude/hooks/tests/test_cleanup_handoff_nudge_marker.py \
  claude/.claude/hooks/tests/test_hook_alignment.py \
  claude/.claude/hooks/tests/test_doc_counts.py -q   # paired hook + header-format + doc-count blast radius
../../../.venv/bin/pytest claude/.claude/               # full suite
../../../.venv/bin/ruff check claude/.claude/
scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck   # must stay clean at .shellcheckrc's default `style` floor
```

Manual end-to-end check with a scratch `HOME` (both branches of the table, one-shot fire so use distinct session ids):

```bash
SB=$(mktemp -d)
printf '%s\n' '{"type":"assistant","message":{"model":"claude-haiku-4-5-20251001","usage":{"cache_read_input_tokens":120000,"cache_creation_input_tokens":0,"input_tokens":0,"output_tokens":0}}}' > "$SB/haiku.jsonl"
printf '%s\n' '{"type":"assistant","message":{"model":"claude-sonnet-5","usage":{"cache_read_input_tokens":120000,"cache_creation_input_tokens":0,"input_tokens":0,"output_tokens":0}}}' > "$SB/s5-low.jsonl"
printf '%s\n' '{"type":"assistant","message":{"model":"claude-sonnet-5","usage":{"cache_read_input_tokens":600000,"cache_creation_input_tokens":0,"input_tokens":0,"output_tokens":0}}}' > "$SB/s5-high.jsonl"
printf '{"session_id":"manual-haiku","transcript_path":"%s"}'  "$SB/haiku.jsonl"  | HOME="$SB" claude/.claude/hooks/nudge-handoff-near-context-cap.sh; echo "exit=$?"
printf '{"session_id":"manual-s5-low","transcript_path":"%s"}'  "$SB/s5-low.jsonl"  | HOME="$SB" claude/.claude/hooks/nudge-handoff-near-context-cap.sh; echo "exit=$?"
printf '{"session_id":"manual-s5-high","transcript_path":"%s"}' "$SB/s5-high.jsonl" | HOME="$SB" claude/.claude/hooks/nudge-handoff-near-context-cap.sh; echo "exit=$?"
cat "$SB/.claude/.handoff-nudge.log"   # expect model=/window= on the two nudged lines; s5-low silent
```

Expected: haiku fires (`window=200000`), sonnet-5 at 120,000 silent (the
GH-556 fix, directly observed), sonnet-5 at 600,000 fires (`window=1000000`),
all three exit 0. Also spot-check fail-open on `{}`, non-JSON input, and a
`"model": null` record — each must exit 0 with empty stdout.

Worth a cheap spot-check, not a blocker (`staff-platform-engineer` raised it,
lower confidence): `test_latency_under_500ms`'s fixture is 10,000 copies of one
short ~30-token line. `tail -n 200` bounds the read to the last 200 lines
regardless of total session size, and a single JSONL record's byte size is
driven by that turn's own content (tool output, file reads), not by the
session's cumulative token count — so the fixture's shape isn't obviously
invalidated by this change. Still, if a real 1M-context session's last-200-line
slice turns out to run measurably larger in practice (verbose sessions
plausibly correlate with larger per-turn output), confirm the 500ms bound
still holds against a real large-transcript sample; adjust the fixture only if
it doesn't.

Real-corpus sanity check after the change (predicted ~5% fire rate, down from
74.5%):

```bash
python3 claude/.claude/scripts/transcript-analysis.py handoff-ratio --since <30-days-ago>
```

## Out of scope

- A Models-API-backed cache to eliminate the hardcoded table (§ rejected
  alternative above) — worth its own follow-up issue, not bundled here.
- A separate `unknown-model` log event/marker mirroring `schema-drift` — would
  force edits to `cleanup-handoff-nudge-marker.sh` and its test, and would log
  on every prompt rather than only on fire, beyond what this bug forces.
  **Explicitly accepted, not solved:** this means a future model that
  mis-resolves to the 1M default and never crosses threshold produces no log
  signal at all — the `model=`/`window=` fields only make a *firing* session's
  resolution visible, not a silently-under-threshold one. The dated source
  comment (re-verify by 2026-11-03) is the real control for that gap.
- Re-litigating whether 60% (vs. some other fraction) is the right trigger
  point — GH-556 asks only that the constant's own stated basis be restored,
  which this does.

## Sequencing

Worktree enforcement is active; branch per this repo's actual convention
(nested `GH-<N>/<topic-slug>`, confirmed against `GH-482/per-record-branch-
attribution` and `GH-550/pr-issue-linking`):

```
git checkout main && git pull --ff-only
git worktree add .claude/worktrees/GH-556/per-model-context-window -b GH-556/per-model-context-window
```
then `EnterWorktree` with that absolute path before any other command touches it.

Test-first: rescale existing fixtures and add the new tests, confirm they
fail against the unmodified hook, then apply the hook changes and confirm
green, then the two doc/README edits, then the full verification block above.
