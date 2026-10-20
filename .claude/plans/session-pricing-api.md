# Public per-session pricing API in transcript_analysis

## Context

Goal: give `transcript_analysis` a public, documented per-session pricing API that the `cost` command and an external consumer both call, so the session read, requestId dedup, per-turn pricing, drift-canary feed and rate-staleness judgment live in exactly one place.

Ask: the request came from an external consumer's planning session, relayed as a brief. It asks for "one public function in claude-config that owns that orchestration, so that `cost` and [the external consumer] both call it and nothing is restated", and relays the engineer's words from that session: "We ABSOLUTELY need to prevent duplicated logic across repos, that's unacceptable." (That quote is a relay from another session, not typed in this one.) In this session the engineer approved planning via label "Yes, run /plan-it (Recommended)", and answered the design questions via labels "Two layers (Recommended)", "Keep the 4 lines, document (Recommended)" and "No, `cost` only (Recommended)". The engineer then asked: "Ask the architect to review those answers". After that review, they selected "Yes, architect's seam (Recommended)" and "New module only (Recommended)".

Why now: the external consumer is building a per-session, per-UTC-day spend file. Its current plan imports eight underscore-prefixed names from `pricing.py` and `corpus.py` and re-implements the orchestration `cost.py:757-891` performs. That build step is blocked on this change.

The brief assumed `cost` already prices per session per UTC day. It does not: `cost` aggregates by token class, model, thread, context bucket, session, project and account, and only `cost-trend` buckets by time (ISO week, `cost.py:1133`). So the shared code sits one layer below the public function, and the per-UTC-day rollup is new code inside claude-config.

Intended outcome:
- A private session-level helper in `pricing.py` (dedup, spawn count, sidechain count, per-call drift detection) that `cost`'s report loop calls in place of `cost.py:758-759` and `787-788`. Per-turn pricing stays inline in `cost`.
- One public run-level function in a new `transcript_analysis/session_pricing.py`, built on that helper. It takes an ordered list of sessions and returns per-session, per-UTC-day priced spend and unpriced tokens, per-call canary state, unpriced model identities, a cross-session requestId collision count and the rate-vintage dates.
- `cost` output byte-identical, with existing tests unedited.
- A stated import path and stability promise for out-of-repo importers.

## Approach

Add one private per-session step to `pricing.py`, `_dedup_session`. It deduplicates a session's records and returns the subagent-spawn count, the sidechain assistant-turn count and a per-call usage-drift flag. Switch `cost`'s report loop to it with no change to `cost`'s output. Then build the one public function, `transcript_analysis.session_pricing.price_sessions_by_utc_day`, on that step. It reads an ordered list of session transcripts and returns frozen dataclasses: integer micro-dollars and unpriced tokens per session per UTC day, plus run-level facts on drift, staleness, unpriced models and requestId collisions. It is the only module in the package with a documented import path and a stability promise.

After this change, five things each have one home: the session read, requestId dedup, the per-turn arithmetic (`_price_turn`), the canary feed and the staleness rule. `session_pricing`'s short per-turn loop around `_price_turn` (skip empty usage, then split priced from unpriced) is one more instance of an idiom the package already repeats at about 16 sites, four of them in `cost.py` (row 46). Nothing is restated across repositories.

**How `cost` stays byte-identical, with no existing test edited.** `cost.py` changes at three sites only (M5):
- `_dedup_session` runs the same dedup body and the same `_count_subagent_spawns` call that `cost.py:758-759` runs. It counts the same records `cost.py:787-788` counts (rows 10, 11).
- `pricing._rate_expired(model, today)` evaluates the same expression as `cost.py:827`, `today > pricing._MODEL_RATE_EXPIRES[model]` (M3).
- The PRICING INTEGRITY banner still reads the process flags through `pricing._format_drift_detected()`. Those flags are set exactly when they are set today (M1).
- `cost` still calls `pricing._price_turn` inline, after its own filters (row 8), so the fault-injection test at `test_transcript_cost.py:1777-1785` still reaches it (row 9).
- The stderr lines and their order do not change (row 14). The one divergence needs malformed input after the usage-drift WARNING has already printed in the same process. It applies to every dedup caller in that process, not only `cost` (row 15).
- `corpus._parse_ts` delegates to the new `_parse_iso_datetime` and returns the same value or exception for every input (M14).

No existing test has to change. `cost.py` gains no public function, so its surface pin holds (row 32). The architecture-doc test passes once the new module's heading exists. Verification steps 3 and 4 check both claims.

**Alternatives weighed:**
- **Public function in `cost.py`.** Rejected. A new public function there fails the cost surface pin (row 32). `cost.py` also imports `scope` and `redaction`, which an importer does not need.
- **Public function in `pricing.py`.** Rejected. `pricing.py` is a review-bench import dependency (row 33). It also holds dozens of underscore names that other modules read, so the line the stability promise draws would blur.
- **A public per-session turn pricer that `cost` also calls.** This was the session's earlier option description for "Two layers". The engineer's choice superseded it (row 1). Pricing every turn in the shared layer would also raise on turns `cost` filters out (row 8).
- **`cost` calling the public function.** Rejected. `cost` filters by usage, by a rolling `--since` window and by branch. It accumulates by class, model, context bucket, account and project (row 8). A per-UTC-day rollup serves none of that, and routing `cost` through it would mean re-pricing or restructuring `cost`.
- **A private shared per-turn step, or a turn-level iterator, that `cost` and `session_pricing` both call.** Set aside. It could hold only the roughly three-line extraction: it cannot price, because `cost` prices after its filters (row 8), and it cannot parse timestamps, because `cost` reads naive timestamps in local time while `session_pricing` counts them as undated (rows 20, M14). It would cover 2 of about 16 sites (row 46), and sweeping all of them contradicts row 3.
- **A per-session public function the caller loops over.** Rejected. The subagent canary needs totals over every session in scope and has no once-only flag (row 16). RequestId collisions also span sessions (row 23).
- **Leaving the staleness comparison to the caller** by returning only `pricing_reverify_by`. Rejected, because the goal puts that judgment in one place (row 28). `stale_models` applies the judgment for the caller's `as_of`, and `pricing_reverify_by` still ships.
- **Raising on an undated turn, or dropping it.** Raising forces one policy on every caller, and dropping hides spend. A per-session count lets the caller refuse.
- **Returning the colliding requestIds, or raising on a collision.** Returning the IDs hands the caller identifiers it does not need, and raising forces one policy. A count is enough for the caller to refuse.
- **Returning exact `Fraction` day totals and letting the caller round.** Rejected. The consumer's rule is integer micro-dollars per session-day, rounded half-up (row 30). If every importer rounded for itself, each would restate that rule.
- **Reading an offset-less timestamp as UTC.** Rejected. That guesses silently. Counting the turn as undated makes a format change visible, and the one known consumer then refuses the run (rows 21, 47).

The alternatives for the result types, arithmetic, errors, stderr and the pin are under M6, M8, M10, M11 and M12.

**Declined review items:**
- **Per-session `usage_drift_detected` and collision counts on `SessionSpend`.** Declined for now. Both can be added later without breaking anything, and the one known consumer refuses on run-level facts (row 47). If Verification step 5 finds cross-session collisions, this comes back to the engineer before Dispatch 2.
- **A doc sentence telling a future in-repo caller to wrap the function for `--pooled`-style filtering.** Declined. The stderr contract already tells any caller exactly what can print.

### Assumption ledger

**Root:** An external consumer needs per-session, per-UTC-day spend from the same transcripts `cost` prices. `transcript_analysis` has no public entry point for it, so the consumer's current plan imports eight underscore-prefixed names and re-implements `cost.py:757-891`. The session read, requestId dedup, per-turn pricing, drift-canary feed and staleness judgment would then exist in two repositories, which the relayed Ask rules out.

**Givens:**
- G1. Claude Code writes the transcripts. It writes one JSONL record per assistant content block, and all records from one API call share a requestId. Subagent transcripts live under `<session>/subagents/*.jsonl` with `isSidechain: true`, and token counts sit in `message.usage`. Reason: the vendor owns the format, and this repo only reads it. Rows 21 and 24 hold the parts that were not checked.
- G3. The rates and the fetch date in `pricing.py` transcribe the vendor's pricing page. Reason: the vendor sets the rates. This plan reads them and changes none.

The external consumer's allowlist handling and this repo's Python floor are conditions the plan could change but deliberately does not; both are in **Out of scope**.

**Mechanisms:**
- **M1. Per-call drift results in `pricing.py`.**
  - `_warn_if_run_usage_drift(run) -> bool` compares the run's invariant usage keys on every call. When they differ and `_usage_drift_warned` is unset, it prints the existing WARNING and sets the flag, as it does today. It returns whether the run drifted.
  - `_merge_assistant_run` stops calling the canary and only merges.
  - New `_dedup_turns_reporting_usage_drift(records) -> tuple[list[dict], bool]` takes over the current body of `dedup_turns_by_request_id`. In the emission loop it builds each merging run once and calls `run_drifted = _warn_if_run_usage_drift(run)`, then `_merge_assistant_run(run)`. It accumulates with `usage_drift = usage_drift or run_drifted`. The call must come before the `or`, so short-circuiting never skips a print.
  - `dedup_turns_by_request_id` keeps its signature and docstring and returns `_dedup_turns_reporting_usage_drift(records)[0]`. Its roughly 20 callers and the shim alias stay untouched.
  - `_warn_if_subagent_format_drift` returns whether the drift signature matched. It prints and sets its flag as it does today.
  - Lighter primitives checked:
    - (a) Reading `_usage_drift_warned` before and after dedup fails. Once the flag is set, the comparison short-circuits and later drift is invisible (row 12).
    - (b) Resetting the flag on each call fails. It re-prints the WARNING on every call and changes when `cost`'s banner fires (row 12).
    - (c) Capturing stderr with `contextlib.redirect_stderr` and parsing it, as `review_rounds.py:1159-1181` does, fails. It swaps process-global state and hides the diagnostic from the caller.

  anchors: row12, row13, row14, row15
- **M2. `_DedupedSession` and `_dedup_session` in `pricing.py`.** `_DedupedSession(NamedTuple)` has four fields: `turns: list[dict]`, `subagent_spawns: int`, `sidechain_assistant_turns: int` and `usage_drift: bool`. `_dedup_session(records)` returns three things: the turns and the drift flag from M1's variant, `_count_subagent_spawns(turns)`, and the number of turns whose `type` is `"assistant"` and whose `isSidechain` is truthy. It takes records, not a path, and applies no filter, so `cost` keeps its own filters after it (rows 7, 8). A private NamedTuple matches the package's five existing typed results (row 31).

  anchors: row1, row7, row8, row10, row11, row31
- **M3. `_rate_expired(model, as_of) -> bool` in `pricing.py`.** It returns `as_of > _MODEL_RATE_EXPIRES[model]` and reads the table at call time, so tests that patch the table keep working. `cost.py:827` and `session_pricing` both call it, which gives the staleness judgment one home.

  anchors: row27, row28, row43
- **M4. `_validate_usage_token_fields(usage) -> None` in `session_pricing.py`.** `session_pricing` is its only caller, so the helper sits with that caller (CLAUDE.md § "Engineering Judgment": "A misplaced helper pushes up to the call site's own placement."). Placing it there also keeps Dispatch 1 a pure refactor and keeps the two dispatches' file sets disjoint. It raises `ValueError` unless all of the following hold:
  - `usage` is a dict.
  - Each of `input_tokens`, `output_tokens`, `cache_read_input_tokens` and `cache_creation_input_tokens` that is present is an `int`, is not a `bool`, and is at least 0.
  - `cache_creation`, when present and not `None`, is a dict whose `ephemeral_1h_input_tokens` and `ephemeral_5m_input_tokens`, where present, pass the same test.

  The message names the key, with a nested key written as `cache_creation.<key>`, or the received type for a non-dict `usage` or `cache_creation`. It never names a value. Keys outside the six token fields are not checked. `_price_turn` does not change, so `cost` keeps today's coercion.

  anchors: row18, row19, row42
- **M5. `cost.py`, three sites and nothing else:**
  - `:758-759` become four statements: `deduped_session = pricing._dedup_session(records)`, `records = deduped_session.turns`, `total_spawns += deduped_session.subagent_spawns` and `total_sidechain_turns += deduped_session.sidechain_assistant_turns`.
  - `:783-788`, the comment and the `isSidechain` count, are deleted.
  - `:827` becomes `if pricing._rate_expired(model, today):`.

  anchors: row1, row3, row9, row10
- **M6. Public surface of `session_pricing.py`.** The signature and types are in Critical files. Both result types are `@dataclass(frozen=True, kw_only=True)`.
  - `frozen` refuses field reassignment, and that is all it does. The two day maps are plain `dict`s typed `Mapping[date, int]`, so they stay mutable, and hashing a result raises `TypeError` (row 50). The plan therefore claims no immutability.
  - The promise covers reading named fields only. Mutating a map, constructing a result, hashing one, and comparing or serializing whole results all fall outside it.
  - `kw_only` makes field order irrelevant to construction.
  - `dataclasses.fields` gives the pin test a runtime field list. `__all__` names the four public names.
  - `format_drift_detected` is a promised, pinned property.
  - Lighter primitives checked:
    - (a) A NamedTuple fails. Tuple unpacking makes field position a cross-repo contract, and adding a field breaks every caller that unpacks.
    - (b) Plain dicts fail, because nothing defines or pins the keys.
    - (c) A TypedDict fails for the same reason at runtime, and it is mutable.
    - (d) `types.MappingProxyType` maps would refuse mutation. CPython cannot pickle or deep-copy a mappingproxy, though, so a result could not cross a process pool.
    - (e) A tuple of `(date, int)` pairs is immutable but drops the keyed lookup a per-day map exists for.

  anchors: root, row2, row4, row31, row34, row50
- **M7. Per-session processing, as private `_price_session(path, start, end)`.** It runs once per path, in input order:
  1. Read the session with `corpus._read_session_file_partitioned(Path(path), include_subagents=True)`. An empty result (`[]`) gives `main_file_readable=False`, empty maps and zero counts. Such a session contributes nothing to the canaries or the requestId map (row 17).
  2. Flatten the groups in order, as `corpus.read_session_file` does.
  3. Raise `TypeError` for any record that is not a dict. The message names only the received type, as in `transcript record is not a JSON object (got str)` (row 17).
  4. Call `pricing._dedup_session(records)`.
  5. Collect the session's set of truthy `requestId`s from deduplicated assistant records (rows 11, 23). Record each ID's first session index in a run-wide `dict`. When a later session carries an ID already in the dict, add it to a run-wide colliding set. Memory then holds one entry per distinct ID.
  6. For each deduplicated assistant record:
     - skip it when its usage is falsy, as `cost` does;
     - call `_validate_usage_token_fields(usage)`;
     - normalize the model to `""` unless it is a `str` (row 26);
     - parse the timestamp with `corpus._parse_iso_datetime` (M14). If the result is `None` or its `utcoffset()` is `None`, the turn is undated. Otherwise its day is `parsed.astimezone(UTC).date()`;
     - skip a dated turn whose day falls outside `[start, end]`;
     - price the turn with `pricing._price_turn(model, usage)`, by module attribute, so tests can patch it.
  7. An undated turn adds 1 to `undated_turn_count` when its unpriced tokens are nonzero or any of its priced class amounts is nonzero. It enters no day map. Its model enters neither the priced set nor the unpriced tally.
  8. A dated unpriced turn adds its tokens to that day and to the run's per-model unpriced tally. The model key is created even at 0 tokens, as `cost.py:824` does.
  9. A dated priced turn adds its model to the run's priced set, zero-token turns included. `cost.py:827` likewise checks staleness on every priced turn. The turn adds each class amount, as `_exact_dollars(amount)`, to that day's exact total.
  10. Each day's exact total becomes `_micro_dollars_half_up(total)`. Zero-valued days are dropped from both maps, and both maps are built in ascending date order.

  Validation runs before the window check, so a malformed turn fails its session in every window.

  anchors: row8, row11, row17, row18, row20, row22, row23, row26, row46
- **M8. Exact micro-dollars.** `_exact_dollars(amount)` returns `Fraction(repr(amount))`, which equals `Decimal(repr(amount))` exactly. `_micro_dollars_half_up(total)` returns `math.floor(total * 1_000_000 + Fraction(1, 2))`. That is half-up rounding for the non-negative totals M4 guarantees. Run totals are sums of the stored per-session-day integers.
  - Lighter primitives checked:
    - (a) Summing floats and rounding once is inexact at a half-micro tie (row 62).
    - (b) `Decimal` under the default 28-digit context rounds silently when the summed amounts span many orders of magnitude. Making it exact would need a raised precision plus an `Inexact` trap.

    `Fraction` is exact by construction and needs no context. This rounding is not unified with `pr_cost._usd_to_micros` (row 29).

  anchors: row29, row30, row62
- **M9. Run-level facts, computed after the last session:**
  - Call `pricing._warn_if_subagent_format_drift(total_spawns, total_sidechain_turns)` once. Its return value is `subagent_format_drift_detected` (row 16).
  - `usage_drift_detected` is the OR of every session's `usage_drift`. Dedup runs before the window check, so a drifting run outside the window still counts.
  - `request_id_collision_count` is the size of M7's colliding set, regardless of window. Colliding turns stay priced in every session that carries them (rows 23, 24).
  - `pricing_reverify_by` is the minimum `pricing._MODEL_RATE_EXPIRES[m]` over the priced set, or `None` when the set is empty.
  - `stale_models` is the sorted list of priced models for which `pricing._rate_expired(m, as_of)` is true.
  - `unpriced_models` is `sorted(pricing._reportable_unpriced_model_ids(tally))` (rows 25, 48).
  - `pricing_fetch_date` is `pricing._PRICING_FETCH_DATE`.

  anchors: row16, row23, row24, row25, row27, row28, row48
- **M10. Errors.**
  - **Argument errors.** These are raised before any read and are never wrapped:
    - `TypeError` when `start`, `end` or `as_of` is a `datetime` or is not a `date`;
    - `ValueError` when `start` is after `end`;
    - `TypeError` when `session_paths` is a `str`, `bytes` or `os.PathLike`. A bare string would otherwise iterate as characters and return one unreadable session per character;
    - `TypeError` when `os.fspath` of any element does not return a `str`.

    Each message names the argument, plus the element index where one applies, and the received type. No message names a value.
  - **Per-session errors.** Any `Exception` from M7 for the session at 0-based position `i` is re-raised as `SessionPricingError(i)`, chained with `raise ... from exc`.
    - A `BaseException` that is not an `Exception`, such as `KeyboardInterrupt` or `SystemExit`, passes through unwrapped.
    - The first failing session aborts the call, and no later session is read. The one known consumer refuses the whole run on any per-session exception, so this matches its policy (row 47).
    - A caller that wants every failing index pays one re-run per failure, and the docs say so.
  - **What a `SessionPricingError` means.** It means "this session could not be priced". It does not say whether the content is malformed or claude-config hit a defect or environment failure while processing that session. `__cause__` is diagnostic and outside the promise. Implementation tests still pin the causes this plan owns: `ValueError` from the validator and `TypeError` for a non-object record.
  - **Post-loop errors.** Failures in M9 propagate raw, because they belong to no session. Only a claude-config defect can cause one, such as a priced model missing from `_MODEL_RATE_EXPIRES`. So can the environment, such as a closed stderr when the subagent warning prints.
  - **The error class.** `SessionPricingError.__init__` passes `session_index` to `super().__init__` and sets `self.session_index`, so pickle and `copy` round-trip it (row 49). `__str__` returns `session_paths[<i>] could not be priced`.
  - Lighter primitives checked:
    - (a) Letting the raw exception propagate fails, because the caller cannot tell which session in the call failed.
    - (b) Returning a per-session error as data fails, because the run-level fields would then cover only some of the sessions. The one known consumer would refuse anyway. A collect-errors mode can be added later as a keyword-only parameter with a default.
    - (c) Validating every record field so the cause type becomes contractual fails. It adds a schema check `cost` never applies, it risks refusing real transcripts (row 19), and the one consumer prints only the cause's class name.

  anchors: root, row17, row18, row47, row49
- **M11. Stderr contract.** The function prints nothing itself. The four existing `pricing.py` lines may still appear. They come from three `print` calls, because the merged and rejected NOTICEs share one (row 56). The doc text for this is in Critical files.
  - Lighter alternatives to documenting checked:
    - (a) `redirect_stderr` fails, as M1 (c) explains.
    - (b) A quiet flag threaded through the three print sites is more invasive than documenting what already prints. It also changes `pricing.py` signatures for every caller.

  anchors: row5, row6, row36, row56
- **M12. Stability promise and its pin.** The architecture doc states the promise, and `test_transcript_session_pricing.py` pins it. The pin follows the pattern of `test_transcript_analysis_cost_import_direction.py`. It checks that every promised item is still present, so an additive change passes it untouched. A removal or retype fails it with a message that names the stability promise. It also runs the module in a fresh interpreter from the documented import path, and parses the changed modules at the 3.11 grammar as a partial guard (rows 51, 57, 59).
  - Lighter primitives checked:
    - (a) A doc-only promise fails, because nothing stops a contributor from breaking it.
    - (b) The `_` prefix alone fails. Other modules read underscore names today, and the package draws no public line.
    - (c) An exact-match pin fails. It would reject the additive changes the promise allows, and would train contributors to update it blindly.

  anchors: row4, row32, row51, row57, row59
- **M13. Where each piece of documentation lives:**
  - field meanings live in each dataclass's docstring, next to the code;
  - the import path, the stability promise, the error contract, the stderr contract and the disclosure limits live in a new `` ### `session_pricing.py` `` section of `docs/transcript-analysis-architecture.md`, which the doc test requires anyway;
  - the `session_pricing.py` module docstring and the `SessionPricingError` docstring point to that section;
  - the package docstring in `transcript_analysis/__init__.py` names the module and its promise;
  - one `CHANGELOG.md` `### Added` bullet carries the re-stow note.

  anchors: row32, row39, row40, row41, row44, row58
- **M14. `_parse_iso_datetime(ts_str) -> datetime | None` in `corpus.py`.** It holds `_parse_ts`'s current falsy check, outside any `try`, then the `.replace("Z", "+00:00")` and `fromisoformat` call inside `try`/`except (ValueError, TypeError)`, returning `None` on either. So a truthy non-string still raises `AttributeError` from `.replace`, uncaught, as today (row 20). `_parse_ts` then calls it, returns `None` for `None`, and returns `.timestamp()` inside its own `try`/`except (ValueError, TypeError)`. Every input therefore gives the same value or exception as today; Dispatch 2's `TestParseTsParity` pins this (corpus.py:213-219). `session_pricing` needs to see whether a timestamp carries an offset, so it reads the aware datetime directly. An offset-less timestamp counts as undated, so a turn's day never depends on the host's time zone.
  - Lighter primitives checked:
    - (a) Documenting that naive timestamps are read in host time fails. The result would vary by machine.
    - (b) Re-parsing the string inside `session_pricing` restates corpus's parse expression.
    - (c) Converting through `_parse_ts`'s epoch with `datetime.fromtimestamp(ts, tz=UTC)` (row 35) cannot tell naive from aware. `astimezone(UTC)` also needs no float round-trip at a day boundary.

  anchors: row20, row21, row35, row47

**Rows:**
1. The engineer chose the architect's seam: a private session-level helper in `pricing.py` that `cost`'s report loop calls, plus one public run-level function in a new `session_pricing.py`. So `cost` calls the shared helper, not the public function, and per-turn pricing stays inline in `cost`. [engineer-verified: "Yes, architect's seam (Recommended)"]
2. There are two layers: a shared lower layer and a public function above it. [engineer-verified: "Two layers (Recommended)"] The public lower layer in that option's description was the session's proposal, and row 1 replaced it.
3. Only `cost`'s report loop moves to the shared helper. [engineer-verified: "No, `cost` only (Recommended)"]
4. The stability promise covers only the new module. [engineer-verified: "New module only (Recommended)"]
5. The four existing `pricing.py` stderr lines stay and become a documented contract. [engineer-verified: "Keep the 4 lines, document (Recommended)"]
6. The function prints nothing itself and also returns canary state as data. [unverified] This comes from the option description behind row 5's label, which was the session's proposal, and from this dispatch's constraint. It is not the engineer's own words.
7. `cost` never reads transcripts itself. Its records come from `scope._resolve_project_scope`'s iterator, so the shared helper takes records, not paths. [verified: cost.py:622, 757]
8. `cost` prices a turn only after its usage, `--since` and `--branches` filters. `_price_turn` raises on a null token field (`int(None)`) or an unhashable model, so pricing every turn in the shared layer could raise on turns `cost` filters out. [verified: cost.py:789-821; pricing.py:163, 534-536]
9. A `cost` test replaces `pricing._price_turn` by module attribute and wraps its return dict, so any shared step must resolve `_price_turn` at call time and must not consume `dollars_by_class` before `cost` reads it. Row 8, not this test, is what keeps pricing inline in `cost`. [verified: test_transcript_cost.py:1777-1785]
10. `cost` counts spawns over each session's deduplicated records. It counts every deduplicated assistant record with a truthy `isSidechain`, before its usage filter. `_dedup_session` computes exactly these two counts. [verified: cost.py:758-759, 780-788]
11. Dedup groups on requestId alone and keeps each merged group's first record, with that record's `requestId`, `isSidechain` and timestamp. A group whose first record is not a sidechain record but whose later records are therefore loses its sidechain flag. `cost` and `_dedup_session` both count after dedup, so their counts still agree. Dedup does not change a session's set of assistant requestIds. [verified: pricing.py:247-249, 258-290, 308]
12. Once `_usage_drift_warned` is set, the comparison returns early, and `_format_drift_detected()` stays true for the rest of the process. Neither can report a later call's drift. The conftest resets both flags before every test for this reason. [verified: pricing.py:380-381, 512-517; conftest.py:1494-1507]
13. Dedup is the only caller of `_merge_assistant_run` (pricing.py:290). `_merge_assistant_run` is the only production caller of `_warn_if_run_usage_drift` (pricing.py:307). No test patches by name dedup, `_merge_assistant_run`, either canary function or `_count_subagent_spawns`. The one direct test call of `_warn_if_run_usage_drift` ignores its return value (test_transcript_review_rounds.py:5104). [verified: Grep over the repo's *.py]
14. Dedup prints the NOTICE lines in its grouping loop and the usage WARNING at merge time in its emission loop. Calling the canary in the emission loop just before each merge keeps that order. [verified: pricing.py:269-290]
15. Comparing on every call can now raise AttributeError where today's early return would not. This happens only for a merged run whose usage is a truthy non-dict, after the WARNING has already printed in the same process. It applies to every dedup caller in that process, not only `cost`. For a truthy non-dict `message`, `_merge_assistant_run` already raises today, so only the exception's type changes there. Accepted, because no test and no well-formed transcript reaches this case. [verified: pricing.py:307-314, 380-385]
16. The subagent canary has no once-only flag and needs totals over every session in scope. So the public function takes all sessions in one call and calls the canary once. [verified: pricing.py:487-509]
17. `_read_session_file_partitioned` returns `[]` when the main file cannot be opened or read, a missing file included. It returns `[[], ...]` for a readable empty main file. It skips invalid-JSON and non-UTF-8 lines, and it skips unreadable or empty subagent files. A valid JSON value that is not an object is kept as a record. [verified: corpus.py:92-115, 118-155]
18. `_price_turn` and `_cache_write_split` coerce token fields with `int()`, which accepts `"123"`, `1.5` and `True` and raises on `None`. [verified: pricing.py:211-212, 534-536]
19. No real transcript holds a null or non-integer value in any token field. That includes the flat `cache_creation_input_tokens` next to a nested `cache_creation` block, which `_price_turn` never reads. If this holds, validating every present field refuses nothing `cost` can price. [unverified] Verification step 5 checks it privately before Dispatch 2.
20. `_parse_ts` returns None for a missing, empty or unparseable string. It reads a naive ISO timestamp in local time, and a truthy non-string value raises AttributeError. [verified: corpus.py:215-221]
21. Claude Code writes every timestamp with a UTC offset (`Z`). If one lacks an offset, M14 counts the turn as undated, so results stay host-independent either way. The one known consumer would then refuse the run. [unverified] Verification step 5 checks it privately before Dispatch 2.
22. A merged turn takes its first record's timestamp and its last record's usage. That holds for a non-contiguous merged run too. [verified: pricing.py:255-257, 295-316]
23. Dedup groups only assistant records with a truthy requestId and handles nothing across sessions. `cost` prices a requestId shared by two sessions in both of them. [verified: pricing.py:247-254, 258-265]
24. It is not known whether real transcripts reuse a requestId across forked or resumed sessions. [unverified] The function counts collisions and still prices both sessions, and the caller's policy decides. Verification step 5 checks it privately before Dispatch 2.
25. `_reportable_unpriced_model_ids` lists every unpriced model key except `<synthetic>` at zero tokens, and `cost`'s EXCLUDED SPEND banner uses it. [verified: pricing.py:182-197; cost.py:434]
26. `cost` keys an absent model as `""` and a null model as `None`, and it sorts the keys at :1013, where a mix of `None` and `str` raises. Normalizing in shared code would change `cost`, so only `session_pricing` normalizes. [verified: cost.py:820-824, 1013]
27. Three sites state the staleness rule: `today > pricing._MODEL_RATE_EXPIRES[model]` at cost.py:827 and subagent_mix.py:660, and `today > min(...)` at review_rounds.py:960. [verified: Grep]
28. The plan's goal lists the rate-staleness judgment among the things that must live in one place. Its intended outcome lists the rate-vintage dates among the returned values. Returning `stale_models` for a caller-supplied date, plus `pricing_reverify_by`, satisfies both. [verified: .claude/plans/session-pricing-api.md Context]
29. `pr_cost._usd_to_micros` rounds through `f"{dollars:.6f}"`, which is a different rule from half-up rounding of the shortest-repr decimal. The PR-cost ledger stores its output. [verified: pr_cost.py:222-224]
30. The external consumer's rule reads: "Each class amount enters as `Decimal(repr(x))`. Each session-day total is stored as integer micro-dollars, rounded half-up." It also says: "The writer omits a day whose rounded value is 0." M8 implements exactly this. [verified: the external consumer's plan, read this session]
31. The only typed results in `transcript_analysis/` are five private NamedTuples (scope.py:935, 944; review_rounds.py:634; author_outcome.py:156; reviewer_yield.py:52). The package has no dataclass, TypedDict or `__all__`. `@dataclass(frozen=True)` is used in production code at `_config.py:34`. [verified: Grep]
32. A new public function in `cost.py` fails the cost surface pin. A new package module needs a `` ### `<name>.py` `` heading in the architecture doc. [verified: test_transcript_analysis_cost_import_direction.py:23, 58-65; test_transcript_analysis_architecture_doc.py:35-49]
33. `pricing.py` and `corpus.py` are review-bench import dependencies. Nothing in that set imports `session_pricing.py`. `pricing.py` gains only a stdlib import, and `corpus.py` gains none because it already imports `datetime`. So the ground-truth import walk sees no new edge. [verified: select-tests.py:74-88; corpus.py:9]
34. The repo's Python floor is 3.11, while ruff targets py312. [verified: README.md:100; pyproject.toml:3]
35. All 4 epoch-to-calendar conversions in the package use `datetime.fromtimestamp(x, tz=UTC)` (cost.py:1133, skill_pair.py:82, spend_over_threshold.py:69, render.py:79). [verified: Grep]
36. `review_rounds` already matches the full NOTICE line, so the NOTICE text is already constrained. Documenting only the prefixes adds no new constraint. [verified: review_rounds.py:1121-1131]
37. An autouse fixture resets both drift flags before every test, but it does not reset `_non_contiguous_merge_notices_logged`. [verified: conftest.py:1494-1507]
38. `_move_fidelity.py capture` runs a script under a pinned environment with a given `CLAUDE_CONFIG_DIR`, from the caller's working directory. It records each run's stdout, stderr and exit code, and `compare-captures` compares two capture directories byte for byte. [verified: tests/_move_fidelity.py:501-513, 618-664, 905-930]
39. The architecture doc says `pricing.py` is exercised only through `tests/test_transcript_analysis.py`, so a new pricing test file makes that sentence false. `README.md:79` already points readers to the architecture doc. [verified: docs/transcript-analysis-architecture.md:489-490; README.md:79]
40. The CHANGELOG has a precedent for a new stowed file: a re-stow note for directories that are not folded symlinks. [verified: CHANGELOG.md:305]
41. `install.sh` creates only `~/.claude` before stowing. A `scripts/` that does not yet exist therefore folds into one directory symlink, and a new file in it appears on `git pull`. The CHANGELOG's conditional re-stow note covers a `scripts/` that already existed as a real directory. [verified: install.sh:47-51; stow-packages.sh:24-26]
42. M4's strict token-field validator is in scope. [engineer-verified: "Strict token validator"]
43. M3's `_rate_expired` helper, and `cost.py:827` calling it, are in scope. [engineer-verified: "`_rate_expired` helper"]
44. The `CHANGELOG.md` bullet is in scope. [engineer-verified: "CHANGELOG bullet"]
45. The two quotes on the Ask line, relayed from the external consumer's session, state the request. [engineer-verified: "Yes, both stand"]
46. M7 step 6 adds one instance of a per-turn idiom the package already repeats: skip non-assistant records, skip falsy `message.usage`, read `message.model`, call `_price_turn`, and treat `None` as unpriced. `cost.py` alone has it four times (94-101, 157-168, 780-825, 1116-1131). About 16 sites in `transcript_analysis/` outside `pricing.py` read `message.usage` and skip on falsy, and `_price_turn` has 18 production call sites in 10 files. `_price_turn` stays the one home of the per-turn arithmetic. [verified: the plan-architect consult's Grep, this session; the 16 count is of the usage-extraction idiom, not of confirmed pricing loops]47. The external consumer's policy has these parts:
    - It refuses the whole run on any exception while reading, deduplicating or pricing one session, and prints only the exception class and the session's position.
    - It refuses on any non-empty requestId present in more than one session.
    - It refuses on any turn with a nonzero amount and no readable timestamp.
    - It records only the run-level format-drift flag.
    - It appends its config dir's `scripts` to the end of `sys.path`.
    - It will rewrite its pricing section against this plan's result type.
    - Its imports do not include `_merge_assistant_run`.

    [verified: the external consumer's plan, read this session]
48. The external consumer's `unpriced_models` text reads "the sorted, unique `message.model` values of in-window turns with unpriced tokens". `_reportable_unpriced_model_ids` also lists a non-synthetic unpriced model at zero tokens. This plan keeps `cost`'s rule (row 25), and the consumer adopts it when it rewrites against the result type (row 47). [verified: the external consumer's plan, read this session; pricing.py:182-197]
49. `BaseException` pickles its `args` and instance dict and rebuilds with `cls(*args)`, so an `__init__` that passes `session_index` as the only arg round-trips. [unverified] The pickle test in `TestPublicSurfacePin` pins it.
50. A frozen dataclass whose field holds a dict still lets that dict be mutated, and `hash()` on the instance raises `TypeError`. [unverified] The staff-sdet review reported running it.
51. CI runs Python 3.12 only, and ruff targets py312, so nothing executes the code under 3.11. [verified: .github/workflows/tests.yml:127; pyproject.toml:3]
52. `cost --since` takes `Nd`, counted back from now. `--summary` requires `--this-repo`, which matches the project directories named by `scope._path_to_project_slug` of each `git worktree list` path from the working directory. The CLI takes `today` from the clock. [verified: transcript-analysis.py:3844-3847, 3869-3877; scope.py:85-93, 591-595; cost.py:255]
53. Every model's re-verify-by date is 2026-12-04, so a CLI capture cannot print STALE PRICING before 2026-12-05. In-process tests pin both sides of that date. [verified: pricing.py:23, 53, 90; test_transcript_cost.py:375-401]
54. A linked worktree has no `.venv`. From `.claude/worktrees/<branch>/` the tools are under `../../../.venv/bin/`. pytest's `pythonpath` also names `claude/.claude/tests`. [verified: README.md:306, 523; pyproject.toml:18]
55. A non-contiguous run merges only when every record matches on the invariant usage keys plus `output_tokens`. The usage-drift canary therefore cannot fire on a non-contiguous merge. [verified: pricing.py:360, 409-418]
56. Three `print` calls produce the four stderr lines, and each prints one line. The NOTICE set is keyed by decision kind, so each NOTICE prints at most once per process. [verified: pricing.py:386-393, 424, 434-448, 502-509]
57. `<config-dir>/scripts` holds two packages, `tests` and `transcript_analysis`, and three top-level modules: `_config`, `_config_dir` and `_skill_auxiliary_files`. [verified: Glob of claude/.claude/scripts/*]
58. The package docstring lists the modules by group and calls them all transcript-analysis.py's decomposed modules. No test reads it. [verified: transcript_analysis/__init__.py:1-9; test_transcript_analysis_architecture_doc.py:29-32]
59. Fresh-interpreter test precedent: `subprocess.run([sys.executable, "-I", "-S", ...])`. [verified: hooks/tests/test_split_command_words.py:58; hooks/tests/test_lib.py:3736]
60. The shared test builders are as follows:
    - `_asst` defaults `usage` to `{}`, which `session_pricing` skips as falsy.
    - `_priced` emits explicit usage with a nested `cache_creation` block, millisecond timestamps, and optional `speed` and `inference_geo`.
    - `_write_subagent_jsonl` writes `<session>/subagents/<agent>.jsonl`.
    - `fake_projects` isolates a corpus for package-level calls.
    - `cost._cost_report(args, today)` takes the date as an argument.

    [verified: conftest.py:36, 734-740, 785-804, 834, 953-1003, 1378-1401; cost.py:502]
61. The POSIX TZ strings `UTC0`, `<-08>8` and `<+14>-14` set fixed offsets through `time.tzset()` on Linux and macOS without a zoneinfo database. [unverified] The TZ fixture's `tm_gmtoff` precondition fails loudly if not.
62. `3e-07 + 4.2e-06` evaluates to a float below `4.5e-06`, while the exact decimal sum is 4.5 micro-dollars. [unverified] The staff-sdet review reported running it. The test asserts it as a precondition.
63. M14's `corpus.py` change is in scope. [engineer-verified: "corpus.py helper"]
64. The `__init__.py` docstring edit is in scope. [engineer-verified: "__init__.py docstring"]
65. Rows 30, 47 and 48 keep their paraphrase of the external consumer's requirements, as a usage example for the public function. [engineer-verified: "I think keeping as written is actually better because it shows how this public function can be used"]
66. Keep row 1's seam and apply the consult's text-only fixes, with package-wide consolidation of the per-turn idiom as a follow-up. [engineer-verified: "Yes, keep seam + text fixes (Recommended)"]

## Critical files

**Before Dispatch 1:** the session commits this plan file (plan-it Step 7).

**Dispatch split.** Two `code-writer` dispatches, run in sequence, neither with `isolation: worktree`. Dispatch 2 calls the helpers Dispatch 1 adds. Their file sets do not overlap. Dispatch 1 is a pure refactor of `cost`'s shared step, and every doc edit is in Dispatch 2. In each dispatch, write each new function's tests first and watch them fail.

**Dispatch 1: the shared per-session step, and `cost`'s switch to it.** Verify with Verification steps 1-4.
- `claude/.claude/scripts/transcript_analysis/pricing.py` (modify). Apply M1-M3, plus:
  - Module docstring: replace the "Assumes a one-shot-per-process caller..." paragraph with: "Module-level flags such as _usage_drift_warned rate-limit stderr diagnostics per process, and only test fixtures reset them. Per-call drift results come from the return values of _dedup_session and the _warn_if_* functions."
  - New docstrings, one line each:
    - `_DedupedSession`: "One session's deduplicated turns plus the drift-canary inputs taken from them, before any caller-side filter."
    - `_dedup_session`: "Deduplicate one session's records and collect its drift-canary inputs, for callers that filter turns afterward."
    - `_dedup_turns_reporting_usage_drift`: "dedup_turns_by_request_id's turns, plus whether any merged run's input or cache usage drifted."
    - `_rate_expired`: "Whether as_of is past a priced model's re-verify-by date."
  - Append to `_warn_if_run_usage_drift`'s docstring: "Returns whether this run drifted, even after the per-process warning has printed." Append to `_warn_if_subagent_format_drift`'s docstring: "Returns whether the drift signature matched."
  - Place `_DedupedSession` and `_dedup_session` after `_count_subagent_spawns`.
  - The only new import is `from typing import NamedTuple`. Keep the code Python 3.11-compatible (row 34).
- `claude/.claude/scripts/transcript_analysis/cost.py` (modify): M5's three sites only.
- `claude/.claude/scripts/tests/test_transcript_pricing_session_helpers.py` (create).
  - Import builders with `from .conftest import ...` and reuse them (row 60). Use synthetic records only.
  - A module-level autouse fixture sets `pricing._non_contiguous_merge_notices_logged` to a fresh set for every test (row 37).
  - Tests:
  1. `_dedup_session(records).turns` equals a hand-written expected list. The fixture holds a contiguous run, a matching non-contiguous run, a non-matching non-contiguous run, an assistant record with no requestId, and a user record.
     - Each merged run is one turn with its first record's identity and timestamp, its concatenated content and its last record's usage.
     - The non-matching pair stays two turns.
  2. `subagent_spawns` counts main-thread `Agent` and `Task` spawns and ignores a spawn on a sidechain record.
  3. `sidechain_assistant_turns` counts a sidechain assistant record that has no usage. It counts a merged sidechain run once and ignores a sidechain record that is not an assistant record.
  4. `usage_drift` is True for a drifting contiguous run when `_usage_drift_warned` is already True, and stderr stays empty. It is False for a run whose `output_tokens` ascend.
  5. `_warn_if_run_usage_drift` returns True and prints once on the first drift. On a second drift it returns True and prints nothing. On a clean run it returns False.
  6. A single dedup call that produces both a NOTICE and the usage WARNING prints the NOTICE first (row 14).
  7. `_warn_if_subagent_format_drift` returns True for `(1, 0)` and sets `_subagent_format_drift_detected`. For `(0, 0)` and `(2, 1)` it returns False and leaves the flag False.
  8. `_rate_expired`, with `_MODEL_RATE_EXPIRES` patched to `{"model-x": D}`, returns False the day before D, False on D, and True the day after D.
  9. `cost` accumulates canary inputs across sessions. Use `fake_projects` and `cost._cost_report(_cost_args(), date(2026, 8, 2))`.
     - Session A has an `Agent` spawn and no sidechain turns. Session B has only a sidechain assistant turn, in its subagent file.
     - Parametrize the file names so A is read first in one case and last in the other.
     - In both orders, no subagent WARNING appears on stderr and no `PRICING INTEGRITY` appears on stdout.
     - In the mirror case, both sessions have spawns and neither has sidechain turns. The subagent WARNING appears exactly once, and the banner appears.
     - In a third case, session A has a spawn and session B has neither spawns nor sidechain turns, in both orders. The subagent WARNING appears exactly once, and the banner appears.
     - Run this test against the unmodified `cost.py` before the M5 edit. It must pass before and after the edit.

**Dispatch 2: the public module, its tests and the docs.** Start after Dispatch 1 is committed and Verification step 5 has run. Verify with Verification steps 1-4, then the session runs step 6.
- `claude/.claude/scripts/transcript_analysis/corpus.py` (modify): M14 only. Place `_parse_iso_datetime` directly above `_parse_ts`, with the docstring "Parse an ISO-8601 timestamp string, reading Z as UTC; None for a missing, empty, or unparseable string."
- `claude/.claude/scripts/transcript_analysis/__init__.py` (modify). In the first paragraph, replace ` (see docs/transcript-analysis-architecture.md).` with `, plus session_pricing, the one module with a stability promise, for importers outside this repository (see docs/transcript-analysis-architecture.md).`
- `claude/.claude/scripts/transcript_analysis/session_pricing.py` (create). Implements M4 and M6-M11.
  - From the package, import only `corpus` and `pricing`, by module: `from transcript_analysis import corpus, pricing`. Everything else comes from the standard library.
  - Import `os`, `Mapping`, `Sequence` and `date` at runtime, because the pin test resolves annotations with `typing.get_type_hints`.
  - Keep the code Python 3.11-compatible.
  - `_validate_usage_token_fields` docstring: "Raise ValueError unless usage is a dict whose present token fields, nested cache_creation ones included, are non-negative ints."
  - Public surface:

```python
__all__ = ["SessionPricingError", "SessionPricingResult", "SessionSpend", "price_sessions_by_utc_day"]

class SessionPricingError(Exception):
    def __init__(self, session_index: int) -> None: ...  # super().__init__(session_index); self.session_index = session_index
    def __str__(self) -> str: ...  # f"session_paths[{self.session_index}] could not be priced"

@dataclass(frozen=True, kw_only=True)
class SessionSpend:
    main_file_readable: bool
    micro_dollars_by_day: Mapping[date, int]
    unpriced_tokens_by_day: Mapping[date, int]
    undated_turn_count: int

@dataclass(frozen=True, kw_only=True)
class SessionPricingResult:
    sessions: tuple[SessionSpend, ...]
    total_micro_dollars: int
    total_unpriced_tokens: int
    unpriced_models: tuple[str, ...]
    stale_models: tuple[str, ...]
    pricing_fetch_date: date
    pricing_reverify_by: date | None
    usage_drift_detected: bool
    subagent_format_drift_detected: bool
    request_id_collision_count: int

    @property
    def format_drift_detected(self) -> bool: ...  # usage_drift_detected or subagent_format_drift_detected

def price_sessions_by_utc_day(
    session_paths: Sequence[str | os.PathLike[str]],
    *,
    start: date,
    end: date,
    as_of: date,
) -> SessionPricingResult: ...
```

  - Module docstring: "Per-session, per-UTC-day transcript pricing, the one transcript_analysis module with a stability promise for importers outside this repository. The session_pricing.py section of docs/transcript-analysis-architecture.md states the import path, the promise, and the error and stderr contracts."
  - `SessionPricingError` docstring: "One session could not be priced. str() names only session_index. The session_pricing.py section of docs/transcript-analysis-architecture.md states what __cause__ may carry."
  - `SessionSpend` docstring: "One input session's in-window spend. A day is the UTC date of a turn's timestamp, and a merged requestId run, contiguous or not, takes its first record's." Then one line per field:
    - main_file_readable: False when the main transcript could not be opened or read, a missing file included; the maps are then empty and the count is zero. True does not mean every line or subagent file was read, since undecodable lines and unreadable subagent files are skipped.
    - micro_dollars_by_day: priced spend per day in integer micro-dollars. Each class amount enters as the exact decimal of its repr, and each day's exact sum rounds half-up. Days that round to zero are absent, and keys ascend.
    - unpriced_tokens_by_day: tokens per day on turns whose model has no rate; days with zero tokens are absent; keys ascend.
    - undated_turn_count: turns with nonzero priced spend or unpriced tokens whose timestamp is missing, unparseable, or has no UTC offset. They enter no day map and no run-level model list.
  - `SessionPricingResult` docstring: "Every session's spend plus the facts that span the whole call. In-window means a turn's day lies in [start, end]." Then one line per field and for the property:
    - sessions: one SessionSpend per input path, in input order.
    - total_micro_dollars: the sum of every session's micro_dollars_by_day values.
    - total_unpriced_tokens: the sum of every session's unpriced_tokens_by_day values.
    - unpriced_models: sorted model IDs of in-window turns whose model has no rate, except "<synthetic>" at zero tokens; an absent, null, or non-string model is "".
    - stale_models: sorted model IDs priced in-window, zero-token turns included, whose re-verify-by date is before as_of.
    - pricing_fetch_date: the date the rate table was read from the vendor's pricing page.
    - pricing_reverify_by: the earliest re-verify-by date among models priced in-window, or None when no in-window turn was priced.
    - usage_drift_detected: a requestId run merged during this call, in or out of the window, had non-identical input or cache usage across its records.
    - subagent_format_drift_detected: this call's sessions recorded subagent spawns but no sidechain assistant turns, counting every record read regardless of window.
    - request_id_collision_count: distinct non-empty requestIds on assistant records in more than one session, regardless of window; their turns are priced in every such session.
    - format_drift_detected: usage_drift_detected or subagent_format_drift_detected.
  - `price_sessions_by_utc_day` docstring: "Price every session's in-window assistant turns by UTC day, plus the facts that span the whole call." Then:
    - session_paths: main transcript paths, each once. Each is also read with its <stem>/subagents/*.jsonl, so passing a subagent file or one path twice double-counts.
    - start, end: inclusive UTC dates.
    - as_of: the date stale_models is judged against.
    - Raises ValueError or TypeError for a bad argument before reading anything, and SessionPricingError for the first session that cannot be priced.
    - A session still being written gives a lower bound for its newest turn.
    - Prints nothing itself.
- `claude/.claude/scripts/tests/test_transcript_session_pricing.py` (create).
  - Write synthetic transcripts under `tmp_path` with the `conftest` builders, imported with `from .conftest import ...`. Priced fixtures use `_priced`, because `_asst` alone gives `usage: {}`, which is skipped (row 60).
  - Use sentinel names: a project directory `sentinel-project-slug`, session stems such as `sentinel-session-0001`, and an unpriced model `claude-sentinel-model-9`.
  - Module-level autouse fixtures:
    - a fresh `pricing._non_contiguous_merge_notices_logged` for every test;
    - a synthetic priced model `claude-sentinel-priced-1` at base rate `2.0`, added with `monkeypatch.setitem` to both `pricing._MODEL_BASE_INPUT_RATES` and `pricing._MODEL_RATE_EXPIRES`. Oracle values then do not move when the real rates are refreshed.

  Test classes:
  - `TestWindowAndDays`, run under three host time zones. A fixture sets `TZ` to `UTC0`, `<-08>8` and `<+14>-14` and calls `time.tzset()`. As a precondition it asserts that `time.localtime(0).tm_gmtoff` is 0, -28800 and 50400. In `finally` it restores `TZ` and calls `time.tzset()` again (row 61). The cases:
    - Turns at `start` 00:00:00.000Z and at `end` 23:59:59.999Z count. Turns at 00:00:00.000Z on the day after `end`, and at 23:59:59.999Z on the day before `start`, do not.
    - A `start == end` window counts only that day.
    - Turns at 23:59:59.999Z and at 00:00:00.000Z the next day land on two different day keys.
    - `2026-05-19T20:00:00.000-08:00` lands on 2026-05-20, and `2026-05-20T09:00:00.000+14:00` lands on 2026-05-19.
    - A priced turn with the offset-less timestamp `2026-05-19T10:00:00.000` adds 1 to `undated_turn_count` and enters no day.
    - A contiguous run whose first record is at 23:59:59.999Z on day D and whose last record is at 00:00:01.000Z on D+1 is priced on D, using the last record's usage. A matching non-contiguous run is priced on its first record's day.
    - A session with only out-of-window turns appears with empty maps. `sessions` follows input order, and map keys ascend.
    - `corpus._parse_iso_datetime` gives an aware result for `...Z` and an offset, a naive result for an offset-less string, and `None` for empty or garbage input.
  - `TestMicroDollars`:
    - Helper values: `_micro_dollars_half_up(Fraction(5, 2_000_000))` is 3, `Fraction(2_499_999, 10**12)` gives 2, and `Fraction(3, 10**6)` gives 3. `_exact_dollars(0.1)` equals `Fraction(1, 10)`, and `_exact_dollars(3e-07)` equals `Fraction(3, 10_000_000)`.
    - The following cases monkeypatch `pricing._price_turn` to return chosen class amounts for a turn with valid usage:
      - An exact tie that float summing misses: amounts `3e-07` and `4.2e-06` give 5 for that day. A precondition asserts `3e-07 + 4.2e-06 < 4.5e-06` (row 62).
      - Round-down: `2.499999e-06` gives 2.
      - Sum of rounded values: two sessions on the same day at `5e-07` each give 1 each and `total_micro_dollars == 2`.
      - A day at `4e-07` rounds to 0 and is absent.
    - Hand-derived values on the synthetic model:
      - a turn with input 1,000,000, output 100,000, cache read 1,000,000, 1h write 500,000 and 5m write 400,000 gives 6,200,000;
      - the same turn with `speed="fast"` gives 12,400,000;
      - with `inference_geo="us"` it gives 6,820,000;
      - with both it gives 13,640,000.

      Each value sits far from a half-micro boundary, so last-digit float error cannot move it.
    - The arithmetic-order guard is an end-to-end tie. Pick token counts on the synthetic model whose class amount has an exact half-micro `repr`, assert that `repr` first as a precondition, then assert the turn rounds up.
    - `total_micro_dollars` and `total_unpriced_tokens` equal the sums over two sessions and two days.
  - `TestUnpriced`:
    - Unpriced tokens land on their turn's day.
    - Absent, null, integer and list models together yield one `""` in `unpriced_models`.
    - `<synthetic>` at zero tokens is excluded, and a non-synthetic unpriced model at zero tokens is included.
    - An unpriced model seen only outside the window is excluded, and the tuple is sorted.
  - `TestReadability`:
    - A missing path and a directory path both give `main_file_readable=False`.
    - A missing main file whose `<stem>/subagents/` holds a priced sidechain turn gives False and zero spend.
    - An empty main file gives True. An empty main file with a priced sidechain subagent file gives True and counts that spend.
    - A main file whose every line is invalid JSON or non-UTF-8 gives True with empty maps. An invalid-JSON line next to a priced turn is skipped.
  - `TestUndatedTurns`:
    - A priced turn with no timestamp adds 1 and enters no day. So does one with an unparseable timestamp string.
    - A zero-token undated turn adds 0. An unpriced undated turn with tokens adds 1.
    - The model of an undated turn enters neither `unpriced_models` nor the priced set. When an undated turn is the only priced turn, `pricing_reverify_by` is `None`.
    - Usage of `{}`, `None` or absent is skipped without validation and adds nothing.
  - `TestStaleness`:
    - Patch `_MODEL_RATE_EXPIRES` so two priced models have different dates. `pricing_reverify_by` is the earlier date among models priced in the window, and a model priced only outside the window does not count.
    - A zero-token priced in-window turn puts its model in the priced set.
    - With `as_of` the day before, on, and the day after that date, `stale_models` is `()`, `()` and `(model,)`.
    - With no priced in-window turn, `pricing_reverify_by` is `None` and `stale_models` is `()`.
    - `pricing_fetch_date` equals `pricing._PRICING_FETCH_DATE`.
  - `TestRequestIdCollisions`:
    - One requestId on assistant records in two sessions gives 1, and both sessions keep their spend. The same requestId in three sessions also gives 1.
    - A shared requestId on turns outside the window still gives 1.
    - A requestId repeated inside one session gives 0. A requestId on user records in two sessions gives 0. Null and empty requestIds give 0.
    - The same path passed twice gives a count equal to that session's number of distinct requestIds.
  - `TestCanaries`:
    - A drifting session gives `usage_drift_detected` True and one WARNING.
    - A second call on another drifting session gives True and no new WARNING. A third call on a clean session gives False.
    - A drifting run entirely outside the window still gives True.
    - A session with a spawn and no sidechain turns gives `subagent_format_drift_detected` True and one subagent WARNING. A spawn in one session plus sidechain turns in another session gives False, in both input orders.
    - A spawn-only session followed by a quiet session (no spawns, no sidechain turns) gives True, and so does the reverse order.
    - Across two sessions in one call, a drifting session followed by a clean session gives `usage_drift_detected` True.
    - A healthy call after a subagent-drift call gives False, while `pricing._subagent_format_drift_detected` stays True.
    - The same inputs priced twice, with the process flags in different states, give equal results.
    - `format_drift_detected` is True when either flag is True and False when both are False.
  - `TestValidator`:
    - It accepts integer fields and `cache_creation: None`. It also accepts a realistic usage dict carrying non-token keys: `service_tier` as a string, `server_tool_use` as a dict, `speed` and `inference_geo`.
    - It raises `ValueError` for each of these:
      - non-dict usage;
      - a null field;
      - `"123"`;
      - `1.48151623`;
      - `True`;
      - `-48151623`;
      - a non-dict `cache_creation`;
      - a null nested field;
      - a flat `cache_creation_input_tokens` of `"SENTINEL_TOKEN_VALUE"` next to a valid nested block.
    - Each message names the key, with a nested key as `cache_creation.ephemeral_1h_input_tokens`. No message contains `48151623` or `SENTINEL_TOKEN_VALUE`.
  - `TestErrors`:
    - Each malformed usage shape from `TestValidator`, placed in the second of two sessions, raises `SessionPricingError` with `session_index == 1` and a `ValueError` cause. A malformed field on a turn outside the window also raises.
    - A JSON line that is not an object raises with a `TypeError` cause. A non-string timestamp raises with an `AttributeError` cause, and so does a non-dict `message`.
    - Disclosure checks cover five inputs, each with a pinned cause type: a sentinel string as a JSON line, a JSON list holding a sentinel, a list holding a sentinel as a timestamp, a sentinel string as a token field, and a sentinel string as `message`.
      - For each, the sentinel and `str(tmp_path)` are absent from `"".join(traceback.format_exception(err))`.
      - For each, `str(err)` is exactly `session_paths[1] could not be priced`.
    - Each argument error fires with a missing path and a malformed session in `session_paths`, and is not a `SessionPricingError`:
      - `start` after `end` raises `ValueError`;
      - a `datetime` as `start`, `end` or `as_of`, and a `str` as `as_of`, raise `TypeError`;
      - `session_paths` given as a `str`, `bytes` or `Path` raises `TypeError`;
      - an `int` element raises `TypeError` naming its index and type.
      - With `session_paths` given as the string `str(tmp_path / "sentinel-project-slug" / "sentinel-session-0001.jsonl")`, and separately with `"sentinel-session-0001"` as `as_of`, `str(err)` contains no sentinel and not `str(tmp_path)` (M10's no-value rule).
    - When sessions 0 and 1 are both malformed, `session_index` is 0.
  - `TestParseTsParity` (M14). A table compares `corpus._parse_ts` with a copy of its pre-change body, written inline in the test as the oracle, on: `None`, `""`, `"garbage"`, a `Z` timestamp, a `+05:30` offset timestamp, an offset-less timestamp under a non-UTC TZ (with the TZ fixture's save and restore), and the int `5`. Each row must give the same return value, or the same exception type, from both.
    - A `KeyboardInterrupt` raised from a patched `pricing._price_turn` propagates unwrapped.
    - An empty `session_paths` returns `sessions == ()`, zero totals and `pricing_reverify_by is None`.
  - `TestOutputContract`:
    - stdout stays empty.
    - One call triggers all four `pricing.py` lines:
      - its session sits at `sentinel-project-slug/sentinel-session-0001.jsonl`;
      - `claude-sentinel-model-9` is on the drifting contiguous run and on both non-contiguous runs;
      - one session spawns a subagent, and no session in the call has a sidechain turn;
      - the requestIds are not sentinels, because those lines print the requestId by design.
    - stderr holds exactly one line per documented prefix and no other line. It contains none of the sentinels and not `str(tmp_path)`.
    - The four prefixes live in one tuple in the test. The test also asserts each one appears in the architecture doc's `session_pricing.py` section.
  - `TestPublicSurfacePin`. Each failure message says the change breaks the stability promise and must be recorded in the architecture doc's `session_pricing.py` section. An additive change passes untouched. The checks:
    - `__all__` contains the four promised names. The module-defined public names equal `set(__all__)`, found by reflection: non-underscore entries of `vars(module)` whose value's `__module__` is the module. Every top-level assignment target in the module's AST other than `__all__` starts with `_`.
    - `inspect.signature(price_sessions_by_utc_day)` has `session_paths` first as positional-or-keyword, then `start`, `end` and `as_of` as keyword-only, none with a default. Any further parameter is keyword-only with a default.
    - Each promised field exists, in any position, with exactly its annotated type from `typing.get_type_hints`. Every field has `kw_only` set. Every field name appears in its class docstring.
    - `format_drift_detected` is a `property` on `SessionPricingResult`.
    - Assigning to a field raises `dataclasses.FrozenInstanceError`.
    - `SessionPricingError` subclasses `Exception`. `SessionPricingError(3).session_index` is 3, and its `str()` is exact. `pickle` and `copy.copy` round-trips keep both (row 49).
    - The module's AST imports nothing from `transcript_analysis` except `corpus` and `pricing`.
  - `TestImportPath`:
    - In a fresh interpreter, `subprocess.run([sys.executable, "-I", "-S", "-c", ...])` (row 59):
      - append the scripts directory to `sys.path`;
      - import `transcript_analysis.session_pricing`;
      - price one `_priced("claude-sonnet-5", input=1_000)` session;
      - print the sorted `transcript_analysis*` names in `sys.modules` and whether `total_micro_dollars > 0`.
    - The set must be exactly `transcript_analysis`, `.corpus`, `.pricing` and `.session_pricing`, and the total must be positive.
    - `ast.parse(source, feature_version=(3, 11))` succeeds for `session_pricing.py`, `pricing.py` and `corpus.py`. The test's docstring calls this a partial guard, since CI runs only 3.12 (row 51).
- `docs/transcript-analysis-architecture.md` (modify):
  1. Add this section immediately after the `` ### `pricing.py` `` section:

     > ### `session_pricing.py`
     >
     > Per-session, per-UTC-day pricing for importers outside this repository: `price_sessions_by_utc_day`, its frozen result dataclasses `SessionSpend` and `SessionPricingResult`, and `SessionPricingError`. Each field's meaning is in its dataclass's docstring. It reads each session with `corpus._read_session_file_partitioned`, deduplicates it and collects its drift-canary inputs with `pricing._dedup_session` (the step `cost.py`'s report loop also calls), checks usage with its own strict token-field validator, and prices each turn with `pricing._price_turn`. It imports only `corpus` and `pricing` from the package, by module (attribute access), plus the standard library.
     >
     > **Import path.** Append `<config-dir>/scripts` to the end of `sys.path`, where `<config-dir>` is the Claude Code config directory this repo is stowed into. Then run `from transcript_analysis import session_pricing`. Do not insert the directory at the front: it also holds top-level modules and packages such as `tests`, `_config` and `_config_dir`, which would then shadow an importer's own modules of the same name. The module needs only the standard library and the Python version `README.md` requires.
     >
     > **Stability promise.** This is the only module in `transcript_analysis` with a stability promise. Every other module, and every underscore-prefixed name anywhere in the package, may change without notice. The promise covers:
     >
     > - the names in `session_pricing.__all__`;
     > - the parameters of `price_sessions_by_utc_day`;
     > - every field of `SessionSpend` and `SessionPricingResult`, with its type and documented meaning;
     > - the `SessionPricingResult.format_drift_detected` property;
     > - `SessionPricingError`, its `session_index` attribute, and its index-only message;
     > - the error contract and the stderr contract below.
     >
     > The promise covers reading named fields. Constructing these types, relying on field order, hashing a result, mutating its day maps, and comparing or serializing whole results (for example with `dataclasses.asdict` or `repr`) are outside it.
     >
     > A new public name, a new result field, or a new keyword-only parameter with a default is an additive change. Any other change to the list above is a breaking change, and so is removing this module. A breaking change updates the pins in `tests/test_transcript_session_pricing.py` and states the break in this section.
     >
     > **Error contract.** `price_sessions_by_utc_day` raises `ValueError` when `start` is after `end`, and `TypeError` for a wrongly typed argument, before it reads any session. It raises `SessionPricingError` for the first session it cannot read, deduplicate, validate or price, and reads no later session. A missing or unreadable main file is not an error: it comes back as `main_file_readable=False`. That error does not say whether the session's content is malformed or claude-config failed while processing it. `__cause__` holds the original exception for diagnosis; its type and text are outside the promise. Any other exception signals a claude-config defect or a failing environment.
     >
     > **Stderr contract.** `price_sessions_by_utc_day` writes nothing to stdout or stderr itself. The `pricing.py` functions it calls may write four lines to stderr:
     >
     > - a usage-drift warning starting `WARNING: requestId `, at most once per process;
     > - a merge notice starting `NOTICE: non-contiguous requestId run merged -- `, at most once per process;
     > - a merge notice starting `NOTICE: non-contiguous requestId run rejected -- `, at most once per process;
     > - a subagent-format warning starting `WARNING: subagent spawns detected in main thread`, at most once per call.
     >
     > Each of the first three names one requestId, copied verbatim from the transcript. Only these prefixes are stable; the rest of each line may change. After a process has printed the usage-drift warning it prints it no more, but each call's `usage_drift_detected` still reports that call's own sessions.
     >
     > **What the output can disclose.** None of the four stderr lines names a transcript path, session ID, project slug, config directory, or model ID. `str()` of a `SessionPricingError` names only `session_index`. `__cause__`, `__context__` and tracebacks are outside this guarantee and may carry a path or transcript content. Record only `session_index` and the exception class in output you share. `unpriced_models` is the only result field whose strings come from transcript content; its entries are `message.model` values, verbatim.
  2. Append to the `` ### `pricing.py` `` section: "`_dedup_session` is the per-session step that `cost.py`'s report loop and `session_pricing.py` share: it deduplicates one session's records and returns its subagent-spawn count, its sidechain assistant-turn count, and whether any merged run's usage drifted during that call. `_rate_expired` is the re-verify-by staleness rule both apply."
  3. In `## Tests`, replace the first sentence ("Each of `corpus.py`, ... which calls into the shim.") with: "Each of `corpus.py`, `scope.py`, `redaction.py`, `pricing.py`, and `render.py` is exercised through `transcript-analysis.py`'s existing test suite (`tests/test_transcript_analysis.py`), which calls into the shim. `pricing.py`'s shared per-session step, staleness rule, and canary return values, and `cost.py`'s accumulation of that step's counts, also have direct tests in `tests/test_transcript_pricing_session_helpers.py`. `session_pricing.py`'s tests, including the pins its stability promise relies on, live in `tests/test_transcript_session_pricing.py`."
- `CHANGELOG.md` (modify). Add this bullet at the top of `### Added` (`:301`):

  > - **`transcript_analysis.session_pricing` is a public, stability-promised API for per-session, per-UTC-day spend, for importers outside this repository.** `price_sessions_by_utc_day` reads an ordered list of session transcripts and returns integer micro-dollars and unpriced tokens per session per UTC day, plus drift-canary, rate-staleness, unpriced-model, and cross-session requestId-collision facts. It prints nothing itself. `cost`'s output is unchanged. See `docs/transcript-analysis-architecture.md` for the import path, the stability promise, and the error and stderr contracts.
  >   - **Re-run `./install.sh` after pulling if the active config dir's `scripts/transcript_analysis/` is not inside a folded directory symlink.** A `git pull` alone does not link a new file into an existing stowed directory, so `<config-dir>/scripts` cannot import the module until you re-stow.

**Reuse, not reimplementation:**
- `corpus._read_session_file_partitioned`: the session read, and the distinction between a missing main file and an empty one.
- `corpus._parse_ts`'s parse, through the extracted `corpus._parse_iso_datetime` (M14).
- `pricing.dedup_turns_by_request_id` (through M1's variant) and `pricing._count_subagent_spawns`, both inside `_dedup_session`.
- `pricing._price_turn`: per-turn pricing.
- `pricing._reportable_unpriced_model_ids`: which unpriced models to list.
- `pricing._MODEL_RATE_EXPIRES` and `pricing._PRICING_FETCH_DATE`: rate vintage.
- `pricing._warn_if_subagent_format_drift`: the subagent canary's print and flag.
- The `conftest` builders `_write_jsonl`, `_asst`, `_priced`, `_agent_use`, `_write_subagent_jsonl`, `_cost_args` and `fake_projects` (row 60).
- `tests/_move_fidelity.py capture` and `compare-captures`: the byte-identity check.
- The autouse `_reset_pricing_format_drift_flags` fixture in `tests/conftest.py`.

**Declined review items:**
- **Moving the `pricing.py`-section doc append and the `## Tests` sentence into Dispatch 1.** Declined. Both dispatches land in one PR, so no stale doc reaches main, and keeping every doc edit in Dispatch 2 keeps the file sets disjoint.
- **A records-level seam so window, rounding and collision cases skip file I/O.** Declined. `tmp_path` writes are cheap, and `_price_session` already isolates the per-session logic.
- **A test for a usage-drifting run that merges non-contiguously.** Declined, because the case cannot occur (row 55).

## Verification

1. `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py` passes, run from this worktree (row 54). Its selection must include these files under `claude/.claude/scripts/tests/`: both new test files, `test_transcript_cost.py`, `test_transcript_analysis.py`, `test_transcript_analysis_architecture_doc.py` and `test_transcript_analysis_cost_import_direction.py`. Because `pricing.py` and `corpus.py` are review-bench import dependencies, it must also include the evals tests (row 33).
2. `../../../.venv/bin/ruff check claude/.claude/ claude-skills/ plugins/ evals/` is clean, matching the lint command in the repo's `CLAUDE.md` on `origin/main`. Before the final run of steps 1-4 after Dispatch 2, sync the branch with `origin/main` per the `git-feature-branch-sync` skill.
3. After each dispatch's commit, run `git diff --name-status --diff-filter=DMRT origin/main...HEAD -- ':(glob)**/test_*.py' ':(glob)**/conftest.py' ':(glob)**/tests/**'`. It must print nothing. That shows no existing test file anywhere in the repo was modified, deleted or renamed, `claude/.claude/tests` included (row 54).
4. Byte identity of `cost` against the merge-base, using `tests/_move_fidelity.py` (row 38). Run this step after Dispatch 1. Run it again after Dispatch 2. That second run covers M14's `_parse_ts` delegation only for the timestamp shapes in the fixture; `TestParseTsParity` covers the rest. Each round rebuilds the fixture and takes both captures on one UTC day, into fresh output directories.
   - Get the base commit with `git merge-base HEAD origin/main`. Then, as a separate call, run `git worktree add <tmp>/base <sha>`.
   - Build a scratch account at `<tmp>/account`, outside the repo. Date every turn relative to the capture's UTC day. It holds two projects:
     - `<tmp>/account/projects/<this-worktree-slug>/` holds one session. Get `<this-worktree-slug>` by calling `scope._path_to_project_slug` on this worktree's absolute path, not by hand (row 52).
       - Its main file has priced turns dated within the last 7 days.
       - It has a contiguous run whose `output_tokens` ascend, a matching non-contiguous pair split by a user record, a non-matching non-contiguous pair, and a contiguous pair whose `input_tokens` differ.
       - It has an unpriced-model turn, a turn with no `model` key, and an `Agent` spawn.
       - Its `<stem>/subagents/agent-1.jsonl` holds priced `isSidechain: true` turns dated 400 days back.
     - `<tmp>/account/projects/fixture-b/` holds one session with a `Task` spawn and no subagent file.
     - No turn carries `"model": null`; that crash is out of scope (row 26).
   - Write a runs file with six lines:
     - `cost`;
     - `cost --by-project`;
     - `cost --projects fixture-b`, which fires the subagent WARNING;
     - `cost --summary --this-repo`, the second banner and render path;
     - `cost --since 30d`, which excludes every sidechain turn from pricing, so only counting before the filters keeps the subagent WARNING silent;
     - `subagents`, a dedup caller other than `cost`.
   - From this worktree's root, capture the base: `../../../.venv/bin/python3 claude/.claude/scripts/tests/_move_fidelity.py capture --script <tmp>/base/claude/.claude/scripts/transcript-analysis.py --account <tmp>/account --out <tmp>/before --runs <runs>`.
   - Capture this worktree's script the same way, from the same directory, into `<tmp>/after`. Then run `compare-captures <tmp>/before <tmp>/after`.
   - The comparison must report no difference in stdout, stderr or exit code. Confirm that, across the runs, the before capture holds both NOTICE lines and both WARNING lines, so the fixture really exercised them.
   - This step cannot reach `cost.py:827`'s STALE PRICING path before 2026-12-05 (row 53). That swap is covered by `test_transcript_cost.py:375-401` and Dispatch 1 test 8 instead.
   - Remove the base worktree.
5. **Private real-corpus probe, run by the session before Dispatch 2 starts.** It needs nothing from Dispatch 1. It checks rows 19, 21 and 24 before the surface they shape is promised.
   - Write a scratch script outside the repo. It appends this worktree's `claude/.claude/scripts` to `sys.path` and resolves the account root with `_config_dir.config_dir()`.
   - For every `projects/*/*.jsonl` under that root, sorted, it reads the session with `corpus._read_session_file_partitioned(..., include_subagents=True)` and deduplicates it with `pricing.dedup_turns_by_request_id`.
   - It prints only these booleans:
     - whether any present token field fails M4's rule; when one does, it also prints the failing keys, which come from the six-key vocabulary;
     - whether any assistant timestamp string parses to an offset-less datetime;
     - whether any non-empty assistant requestId appears in more than one session;
     - whether any session raised, with the exception class name only.
   - Run it in one Bash call with stderr discarded (`2>/dev/null`). The script catches `Exception` itself.
   - Any true result means stop and bring it to the engineer before Dispatch 2:
     - a failing token field is row 19's question: narrow M4 or not;
     - offset-less timestamps are row 21's question: keep counting them as undated or not;
     - cross-session requestIds are row 24's question: should `SessionSpend` carry its own collision count.
6. **Private real-function smoke run, by the session after Dispatch 2.**
   - A scratch script set up like step 5's passes the same sorted paths to `price_sessions_by_utc_day`, with `start=date.min` and with `end` and `as_of` set to today in UTC.
   - It prints only these things: whether the call raised, with `session_index` and `type(err.__cause__).__name__`; the two drift flags; whether `request_id_collision_count` is zero; and whether every session's `undated_turn_count` is zero.
   - Run it with stderr discarded.
7. **Publication guard for steps 5 and 6.** No result, count, boolean or claim derived from either run may enter the plan, a commit message, the PR body, the CHANGELOG, docs or test fixtures.
   - Rows 19, 21 and 24 stay `[unverified]`. The session may append only "checked privately; result not recorded".
   - The PR body may say that steps 5 and 6 ran privately, and nothing about their outcome.
   - If a result leads to a design change, its committed rationale argues from the transcript format and synthetic fixtures only.
   - The session reports the results to the engineer in this session only.

## Out of scope

- **Moving the other copies of the orchestration onto `_dedup_session` or the public function.** These are `subagents.py`, `skill_pair.py`, `reviewer_yield.py`, `cost_ledger.py`, `subagent_mix.py`, the shim's cache-efficiency path, and `cost.py`'s own `_compute_pr_cost_branch_totals`, `_first_main_thread_turns_dollars_by_branch` and `compute_cost_trend_data`. The engineer chose `cost` only (row 3). `subagents.py` fits best as the first follow-up.
- **Pointing `subagent_mix.py:660` and `review_rounds.py:960` at `_rate_expired` (row 27)**, for the same reason.
- **Making `cost` count or exclude cross-session requestId collisions (row 23).** That would change `cost`'s figures.
- **Making `cost` treat offset-less timestamps as undated.** `cost` keeps `_parse_ts`'s host-local reading, because changing it would change `cost`'s figures.
- **`cost`'s crash on a null model mixed with string models at `cost.py:1013` (row 26).**
- **Unifying `pr_cost._usd_to_micros` with M8's rounding (row 29).** That would change stored PR-cost ledger values.
- **Adding `session_pricing.py` to `PRODUCTION_MODULES` in `test_transcript_package_module_prefixes.py`.** That edits an existing test file, and the new pin test already checks the module's package imports.
- **Replacing `pricing.py`'s process-global flags with per-call state.** The per-call return values cover this API's needs. The flags also drive `cost`'s banner and `review_rounds`' stderr filter.
- **Per-session drift and collision attribution in `SessionSpend`, and a mode that collects every failing session.** Either can be added later without breaking the promise (M10). Verification step 5 decides whether collision attribution comes back before Dispatch 2.
- **Read-only day maps at runtime.** M6 explains why the maps stay plain dicts.
- **A CLI subcommand or machine-readable output for per-day spend.**
- **Consolidating the package-wide priceable-turn idiom (row 46) into one helper.** It spans about 16 sites across about 10 modules, and the engineer chose `cost` only (row 3); it is a follow-up (row 66).
- **Resolving allowlist entries to transcript paths inside claude-config.** The function takes resolved paths. The external consumer keeps its allowlist loading, path resolution, path guards, refusal policy and `sys.path` setup, because moving them here would restate the consumer's allowlist format in this repo.
- **Raising the Python floor above 3.11.** `README.md:100` and `install.sh` impose 3.11 on every stow consumer, so new code stays 3.11-compatible (row 34).