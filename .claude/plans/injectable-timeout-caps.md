# GH-978 Unit 2 — Make the hook-test timeout caps injectable

## Context

Cut the real wall-clock time the hook and script test suites spend waiting
for a genuine `timeout(1)` kill to fire, without changing how any commit or
credential gate's production timeout actually behaves. GH-978 is a tracking
epic for cutting hook-chain and test-suite process overhead; Unit 1 (merged,
PR #985) consolidated the PreToolUse `_lib.sh` bootstrap. This is Unit 2,
from the epic body:

> **Unit 2 — Make the timeout caps injectable. Remove the real sleeps from
> the 54 cap-boundary tests. The design question is doing this without
> creating a production bypass of a security gate's timeout, since these are
> commit and credential gates.**

Every `_lib_capped`/`_lib_jq`/`_lib_capped_for` call (`claude/.claude/hooks/_lib.sh:20,31,41-51`)
ultimately runs `timeout SECONDS CMD...` (or `gtimeout`), where SECONDS is a
literal 2, 5, 10, or 15 depending on the call site. A set of tests proves
each cap actually fires by installing a fake `git`/`gh`/`jq` that sleeps past
the cap and asserting the wrapped call took at least a floor amount of real
time, so each one pays a genuine multi-second wall-clock wait rather than a
mocked one. Every one of them also carries `@pytest.mark.timing`, which
`pyproject.toml` runs serially (`-m timing -n0`) rather than under
`-n auto`, so the cost doesn't parallelize away.

**On the "54" figure:** at least 43 real-sleep cap-boundary tests are
confirmed (26 via the named `git_timeout_shim`/`gh_timeout_shim`/
`assert_cap_engaged` fixtures in `claude/.claude/hooks/tests/conftest.py`,
17 hand-rolling the identical pattern elsewhere, plus 2 more in
`claude/.claude/scripts/tests/` outside that grep —
`test_cleanup_merged_branches.py`, `test_ci_watch.py`). The epic's cited 54
does not reconcile exactly against this count and should not be treated as
verified. Separately, 62 tests repo-wide carry `@pytest.mark.timing`, but
that marker also covers tests serialized for subprocess-spawn contention
unrelated to a cap wait, so it isn't the same population either. Treat "at
least 43, not reconciled to 54" as the grounded figure, not "54" itself.

**Cost estimate:**
- ~38 tests at the shared 5s default cap: ~190s.
- 2 tests at the 15s `_lib_cumulative_diff_hash` cap with wider shim/floor
  overrides: ~30s.
- Several 2s-cap regression tests, plus the 2 `claude/.claude/scripts/tests/`
  tests (one alone at ~15s real cap + 30s stub sleep): further cost beyond
  the above.

Total serial cost is **≈220-260s (3.5-4.5 min)** of pure serial
sleep-and-wait today.

**Intended outcome:** make the *test's* view of the cap duration
injectable/scalable, while leaving every production hook and script
byte-identical — so the design question the epic poses (avoiding a
production bypass of a security gate's timeout) is answered by
construction rather than by a new guard. Each test's proof that a cap
fired also moves from an elapsed-time inference to a direct observation:
the fake `timeout` records the duration it was handed before running the
real binary, and records it a second time only if the command finished on
its own, so "a cap engaged" is a comparison of two files rather than a
threshold on a stopwatch. That severs the coupling that used to bound the
divisor — a wall-clock floor had to clear each test's own real work, and
raising the divisor walked the floor toward that work until it stopped
discriminating, silently and permanently. At divisor 3 this lands a ~60%
cut to the ≈220-260s serial timing lane (to ≈85-95s). 3 is now a
conservative stated default rather than a measured ceiling; Out of scope
names raising it as a one-line follow-up that needs no re-derivation.

## Approach

Make the test suite scale the **`timeout(1)` binary**, not the caller's
SECONDS argument: each cap-boundary test writes a tiny fake `timeout` into
the same tmp bin directory it already prepends to `PATH`, and that fake
divides the real, caller-supplied duration by 3 before running the genuine
`timeout`/`gtimeout`. Production hooks keep passing `2`/`5`/`10`/`15`;
`_lib.sh` keeps resolving `timeout` via `command -v` exactly as it does
today; **no file under `claude/.claude/hooks/` or `claude/.claude/scripts/`
(non-test) changes at all.** The epic's design question — "how do we do this
without creating a production bypass of a security gate's timeout" — is
answered by construction: there is no new code path in the gate to bypass,
because the gate's code is untouched. The only new artifact is a
test-written executable that exists for the lifetime of one pytest
`tmp_path`.

The same fake `timeout` is also the suite's evidence that a cap fired. It
appends the duration it was asked for to a `started` log before running the
real binary, and appends it again to a `completed` log only when the real
binary reports that the command finished on its own. A duration present in
`started` with no matching entry in `completed` is a killed invocation.
That is a direct observation of the event the tests are named for, and it
fails closed: a shim that was never invoked leaves both logs empty, so the
assertion errors loudly instead of a floor quietly clearing itself.

```bash
#!/bin/bash
# Test-only: scales an integer timeout(1) duration down so a cap-boundary test waits a fraction of the production cap.
# Only a 1-9-leading integer scales: bash arithmetic reads a leading zero as an octal prefix, so every other $1 runs at the caller's own duration.
if [[ "$1" =~ ^[1-9][0-9]*$ ]]; then
  requested="$1"
  scaled_ms=$(( requested * 1000 / <DIVISOR> ))
  printf -v scaled '%d.%03d' "$(( scaled_ms / 1000 ))" "$(( scaled_ms % 1000 ))"
  shift
  # One appended line per invocation, so a hook making several capped calls is counted rather than overwritten.
  printf '%s\n' "$requested" >> <STARTED_LOG>
  <REAL_TIMEOUT_ABSPATH> "$scaled" "$@"
  status=$?
  # timeout(1) exits 124 exactly when it kills the command, so any other status means the command finished before the cap.
  # Assumes timeout's own kill is the only way this process exits non-124.
  # An external signal (OOM, an outer test-runner kill) between fork and this line would misclassify.
  # That's a test-infra race, not a production reachability concern.
  [[ $status -eq 124 ]] || printf '%s\n' "$requested" >> <COMPLETED_LOG>
  exit "$status"
fi
exec <REAL_TIMEOUT_ABSPATH> "$@"
```

`<REAL_TIMEOUT_ABSPATH>` is `shutil.which("timeout") or shutil.which("gtimeout")`,
resolved in the Python process and embedded as an absolute path — a relative
name would re-resolve through the shadowed `PATH` and recurse forever. Only
a `timeout` fake is written, never a `gtimeout` one: `_lib_capped_for`
probes `timeout` first (`_lib.sh:44-47`), so a `gtimeout` fake would be
unreachable on every host. `<STARTED_LOG>` and `<COMPLETED_LOG>` are
absolute paths under a marker directory `write_scaled_timeout_shim` creates
inside the same `bin_dir`, each `shlex.quote`d at write time — the same
quoting `_write_conditional_sleep_shim` already applies to `fake_output`.
Baking the paths in rather than reading an env var matters: two sites build
a **closed** `PATH` with no inherited environment (row 19), and an env var
would need adding at every one of them.

The scaling branch no longer `exec`s, because a process that has been
replaced cannot report what happened to its replacement. The passthrough
branch still `exec`s, unchanged, so a `$1` the regex rejects behaves
exactly as it does today.

The regex admits `1`-`9`-leading integers only. `019` and `010` are
rejected on purpose: `$(( 019 ))` aborts with "value too great for base"
and `$(( 010 ))` silently evaluates to decimal 8, so both would break the
documented passthrough instead of degrading to it (row 9). Bare `0` is
rejected for a different reason — GNU `timeout` spells "no time limit" as
`0`, and passing it through unscaled preserves that meaning exactly.

**Two transforms, not three.** A cap-boundary site carries two numbers that
have to move together:

1. **The cap** (what the shim emits) divides by the divisor.
2. **A shim sleep that must outlast the cap** divides and rounds **up**
   (`math.ceil`), so `sleep > cap` stays an identity rather than a
   hand-picked literal. Rounding up is free: the extra time is only ever
   spent on a run where the cap failed to fire, i.e. a failing test.

The third transform this plan used to carry — a floor or an elapsed ceiling
that does not divide — is gone with the mechanism it served. No site
retains a wall-clock threshold of any kind.

One site inverts rule 2. `test_nudge_error_mode_analysis.py:510-534` proves
the **opposite** direction — a shim that finishes *inside* the cap must not
be killed — so its `sleep 3.5` must stay strictly under the scaled 10s cap.
It divides with **no rounding up** (`3.5 / 3 = 1.166` against a `3.333`
cap). Its paired `assert elapsed >= 3.5` at `:534` is replaced by
`assert_cap_not_engaged(fake_bin, production_cap=10)`, which states the same
thing without a stopwatch. Rounding that sleep up the way every other site
rounds up would still walk it toward the cap it must stay under, so
`scaled_under_cap_sleep` stays.

On a host with neither `timeout` nor `gtimeout`, `write_scaled_timeout_shim`
writes nothing and returns `False`, and every scaled value is then left
unscaled. Under markers that host would also produce no marker logs, so the
one site that calls `_write_conditional_sleep_shim` directly gains the same
skip guard the two named fixtures already carry (row 18).

### Counting invocations instead of naming them

A single hook run makes many capped calls — `nudge-handoff-near-context-cap.sh`
makes 21 — and three sites chain two capped calls whose caps must *both*
fire (row 8). A pair of fixed-name marker files would be overwritten by the
20 calls that legitimately complete. Appending one line per invocation and
comparing multisets handles every shape with no per-site naming scheme:

```python
def caps_that_fired(bin_dir: Path) -> Counter[str]:
    """Caller-supplied cap durations whose invocation started and never completed."""
    return _log_counts(bin_dir, "started") - _log_counts(bin_dir, "completed")
```

`Counter` subtraction drops non-positive counts, so a duration that started
and completed disappears and a duration that started twice and completed
once leaves `1`. Order does not matter, and two invocations sharing a
duration are counted, not conflated. `assert_cap_engaged` snapshots both
counters on entry and asserts on the delta, so a test that runs the hook
more than once inside one `tmp_path` is not confused by the earlier run's
records. `>>` opens with `O_APPEND`, so a short line stays atomic against
the one site that runs a hook on a background thread
(`test_nudge_long_turn_subagent.py:517`).

Recording the *duration* rather than a bare tick is what replaces
`CUMULATIVE_DIFF_CAP_FLOOR_SECONDS`. That constant existed so a silent
regression from `_lib_cumulative_diff_hash`'s 15s cap back to the shared 5s
default would still fail — a floor of 12s no 5s cap could clear.
`assert_cap_engaged(bin_dir, production_cap=15)` states that directly, and
is checkable in CI at sub-second cost, which the 12s floor was not
(the retired Verification step 5 had to carve tier B out as unverifiable by
any test in this suite).

### Choosing the divisor

`TIMEOUT_SCALE_DIVISOR = 3`, and it is no longer a calibrated quantity.
Under a wall-clock floor the divisor was bounded by measurement in both
directions at once: the floor had to clear the test's real non-cap work,
and the floor was a fixed fraction of the scaled cap, so a larger divisor
walked the floor into the workload and the test passed forever afterwards
without testing anything. A started/completed record has no such coupling —
it reports whether `timeout(1)` killed the command, at any cap duration, so
no choice of divisor can make it inert.

What that leaves:

- **Nothing bounds the divisor for correctness.** Its failure direction is
  now loud. If a scaled cap were short enough to kill a *legitimately
  completing* capped call in the same hook run, the hook produces the wrong
  output and the test fails, or `caps_that_fired` reports a kill the test
  did not assert. Neither is a silent pass. A parameter whose failure mode
  is a red CI run can be tuned from evidence rather than pre-committed by
  measurement.
- **The remaining floor is `math.ceil`, not the cap.** At divisor 3 the
  shim sleeps become 2/4/7/10s; at divisor 10 they become 1/1/2/3s. If row
  1's hypothesis holds — that elapsed time is governed by the orphaned
  `sleep` holding the captured stdout pipe, not by the cap — then the sleep,
  rounded up to a whole second, is what the next saving has to get past.
- **3 stays for this PR.** Row 1 is unverified and Verification step 4's
  before/after `--durations=0` table resolves it; raising the divisor after
  that is a one-line change to one constant with no assertion to re-derive.
  Raising it *in* this PR would compound two unreviewed changes — a new
  discriminator and a more aggressive divisor — in a single review surface,
  and the plan's own dispatch-split rationale is that the per-site judgment
  in Dispatch B is what makes this work hard to review. Out of scope names
  the follow-up and what it has to check first.

The fake `timeout` costs one extra `bash` fork+exec per capped call, plus —
new in this design — the shim process staying resident for the call's
duration and two short appends. A fork+exec model has different per-call
overhead than this fake-timeout-stays-resident design; Verification step 4
re-measures it.

**Alternatives set aside.**

1. **An env-var override read inside `_lib_capped_for`** (optionally
   double-gated on `PYTEST_CURRENT_TEST`, which pytest sets only "when
   running tests" per its own docs, though its value format is explicitly
   not guaranteed stable across releases so only presence — never content —
   could safely be checked) — rejected: it edits the security-relevant
   library function itself, on every invocation of every gate hook, forever,
   to buy a speedup the PATH shim already delivers with zero production
   diff.
2. **Build-time or source-time substitution of the ~147 literal SECONDS
   arguments** — rejected: it needs either a second `_lib.sh` or a
   templating step, and both touch the file every hook actually runs.
3. **Replacing the runtime waits with a static assertion that each call
   site is `_lib_capped`-wrapped** — rejected because these tests do not
   assert the wrapper's *presence*; they assert the caller's *post-timeout
   branch* (e.g. `test_marker_script.py:2096-2102`: the empty value a killed
   process yields must fall through to the absent path, not crash). A grep
   cannot reach that.
4. **Migrating the hand-rolled shims onto
   `git_timeout_shim`/`gh_timeout_shim`** — deferred: several hand-rolled
   bodies are shapes the shared helper's single `match_condition` parameter
   cannot express (`test_cleanup_merged_branches.py:2271-2275` loops over
   `"$@"` with a `case`; `test_nudge_error_mode_analysis.py:504` carries a
   `[ "$1" = "-c" ] && exit 0` preamble; `test_require_architect_consult.py:190`
   is a bare `case` arm), so the migration is a separable refactor with its
   own per-test drift risk.
5. **Per-tier divisors** — rejected. With floors and ceilings gone there is
   no per-tier quantity left for a second knob to tune; a second divisor
   would add a per-site judgment about which knob a site belongs to and buy
   nothing the single knob does not already buy.
6. **Putting the markers in the conditional sleep shim instead of the
   `timeout` shim** — rejected on blast radius and on what it measures. The sleep
   shim would touch `.started` before sleeping and `.completed` after, and a
   cap firing kills it mid-sleep. Only one of the thirteen hand-rolled files routes through
   `_write_conditional_sleep_shim`; the other twelve write their own bash,
   in four incompatible shapes (alternative 4), so the marker lines would
   have to be placed by hand inside a `case` arm, a `for` loop, and a
   guarded preamble — reintroducing exactly the per-site judgment the
   dispatch-split rationale calls the expensive part. It also observes one
   inference step away from the claim: "the stalling fake binary died"
   rather than "`timeout` killed the command."
7. **A single `.timedout` marker instead of a started/completed pair** —
   rejected: it cannot distinguish "the shim was never invoked" from "the
   shim ran and nothing killed it," and that distinction is the whole
   fail-closed property. Two logs cost one extra `printf`.
8. **One marker file per invocation, named by the shim's `$$`** — rejected:
   PID reuse inside one `tmp_path` would let a later completed invocation
   overwrite an earlier killed one's record, turning a kill invisible. That
   is a fail-open failure of exactly the kind this design exists to remove.
   Appending to a shared log is collision-free by construction.

### Assumption ledger

**Root problem:** cap-boundary tests spend real wall-clock seconds waiting
for a genuine `timeout(1)` kill, and cutting that wait must not change the
duration a live session's hooks actually enforce on a commit or credential
gate.

**Givens:**
- **G1 — hooks run as subprocesses with an inherited `PATH`, and the
  harness owns that contract.** The test suite controls only the `PATH` it
  hands its own subprocesses.
- **G2 — GNU coreutils fixes `timeout`'s duration grammar and its exit
  status.** This plan consumes the fractional-duration form, the
  `0`-means-no-limit spelling, and the documented `124`-on-kill status; it
  cannot change any of them.

**Mechanism justifications:**
- Fake `timeout` on `PATH` rather than an argument override → `anchors:
  root`. It is the same interception mechanism `git_timeout_shim` already
  uses, and it is strictly lighter than the two rejected alternatives, both
  of which modify a file that production gate hooks execute. Two lighter
  primitives were considered and named above (static wrapper assertion;
  reusing the shared fixtures for every site) — both fail on what the
  assertions actually pin.
- One divisor constant applied to cap and sleep → `anchors: row1, row8`. A
  single knob keeps `sleep > cap` an identity rather than a per-site
  judgment, and nothing in the discriminator is derived from it.
- Markers written by the **`timeout` shim**, not by each site's sleep shim
  → `anchors: root, row6, row8`. Two lighter primitives were weighed:
  marking inside the conditional sleep shim (rejected — alternative 6: it
  reaches only one of thirteen hand-rolled files through shared code and
  needs hand placement inside four incompatible bash shapes in the rest),
  and a single `.timedout` marker (rejected — alternative 7: it cannot
  separate "never invoked" from "not killed," which is the fail-closed
  property itself). One shared body covers every site because every site
  already installs this shim.
- An **appended duration log** rather than fixed or PID-named marker files
  → `anchors: row8`. Two lighter options were weighed: two fixed-name files
  (rejected — the 20 legitimately-completing calls in a 21-call hook run
  overwrite the one kill), and one file per invocation named by `$$`
  (rejected — alternative 8's PID-reuse fail-open). Multiset arithmetic over
  an append log is the cheapest form that is correct for one call, two
  chained calls, and twenty-one.
- Pinning the **caller-supplied duration** in the assertion rather than
  asserting only that some cap fired → `anchors: row5`. The lighter option —
  a bare fired/not-fired assertion — was rejected because it loses the
  regression guarantee `CUMULATIVE_DIFF_CAP_FLOOR_SECONDS` carried at
  `test_marker_script.py:2353` and the 2s-vs-5s distinction four
  `test_nudge_handoff_near_context_cap.py` sites claim in prose and never
  check (row 15). Recording `$1` costs nothing the shim was not already
  holding.
- A separate `scaled_under_cap_sleep` for the one inverted site →
  `anchors: row7`. Reusing the rounding-up helper there walks a sleep that
  must finish inside the cap toward the cap; one extra four-line function
  is lighter than a per-site comment asking future editors to notice the
  inversion.
- Helper **and the two assertion contextmanagers** live in
  `claude/.claude/tests/helpers.py`, not duplicated across two conftests →
  `anchors: row10, row13`. Row 13's two scripts-tree tests now need the same
  assertion the hooks tree uses, and `helpers.py` is the repo's existing
  cross-tree sharing mechanism, so this satisfies CLAUDE.md's
  single-source-of-truth rule without either conftest importing the other
  (which `_dead_pid`'s docstring at `conftest.py:60-69` explicitly rules
  out). `helpers.py`'s "no pytest decorators here" contract is preserved:
  `@contextmanager` is `contextlib`, and every `pytest.skip` stays at its
  call site.
- Coupling the timeout-shim write *inside* `_write_conditional_sleep_shim`
  → `anchors: row1, row18`. A site that scales one half but not the other
  either stays slow (sleep unscaled) or breaks outright (cap unscaled,
  sleep finishes first); coupling makes that unreachable on every path that
  routes through the shared helper.
- A source-scanning test pinning the five protected `test_lib.py` tests →
  `anchors: row4`. Two lighter options were weighed: the in-file comments
  alone (rejected — row 4's failure mode is silent, and a comment stops
  nobody sweeping the file with the helper), and a one-shot diff check
  during this change (rejected as the *only* guard — it expires the moment
  this PR merges, while the regression stays available forever). The repo
  already enforces conventions this way in
  `claude/.claude/tests/test_pytest_collection_config.py`.

**Rows:**

1. `[unverified]` — Elapsed time in the tests the epic reports at ≥9.5s is
   governed by the shim's `sleep` outliving the cap (the orphaned `sleep`
   holds the captured stdout pipe open past the kill), not by the cap
   alone. This is why both halves scale. Not confirmed empirically; the
   before/after `--durations=0` comparison in Verification resolves it
   either way, and scaling both is harmless if the premise is wrong.
2. `[verified: claude/.claude/hooks/tests/test_lib.py:638]` — a fractional
   duration is accepted: the repo already calls `_lib_capped_for 0.2 sleep
   0.6` against the real `timeout`. Corroborates `[engineer-verified]` GNU
   coreutils manual ("duration is a floating point number… followed by an
   optional unit").
3. `[verified: claude/.claude/hooks/_lib.sh:41-51]` — `_lib_capped_for`
   resolves `timeout`, then `gtimeout`, via `command -v` at call time, so a
   `PATH`-prepended fake intercepts every call site with no library change.
4. `[verified: claude/.claude/hooks/tests/test_lib.py:533-676]` — **five**
   consecutive tests, not three, must never receive the scaled shim
   (verified this session by direct read: exactly these five function
   boundaries, no others, span 533-676). Each builds its own closed `PATH`
   and never routes through `_write_conditional_sleep_shim` or
   `assert_cap_engaged`: `test_timeout_absent_fallback_valid_payload_returns_ok`
   (533-557, premise is that no `timeout` exists on `PATH`),
   `test_lib_capped_for_enforces_cap_when_timeout_present` (560-585),
   `…enforces_cap_via_gtimeout_when_timeout_absent` (588-615),
   `…runs_uncapped_when_neither_timeout_nor_gtimeout_present` (618-642),
   `…prefers_timeout_over_gtimeout_when_both_present` (645-676). Four of the
   five would **still pass** with a shim installed while no longer testing
   their own premise; only 618-642 would fail loudly.
5. `[verified: claude/.claude/hooks/_lib.sh:251]` — this repo already treats
   `124` as `timeout`'s kill status in production code ("jq non-zero exit
   (parse failure, timeout exit=124, missing jq binary)"). Corroborates
   `[engineer-verified]` GNU coreutils manual, which specifies exit status
   `124` "if COMMAND times out" and reserves `125`/`126`/`127` for
   `timeout`'s own failures. The whole discriminator rests on this one
   value, so the shim comments it at the comparison and
   `test_scaled_timeout_shim.py` pins it in both directions.
6. `[verified: claude/.claude/hooks/tests/ — direct grep this session]` —
   `assert_cap_engaged` is imported by **7** files and called at **25**
   sites: 23 bare, and 2 passing `floor=self.CUMULATIVE_DIFF_CAP_FLOOR_SECONDS`
   (`test_marker_script.py:2689,2720`). The bin dir differs per site —
   `tmp_path` at fixture sites, `tmp_path/"stub-bin-find"`
   (`test_lib.py:1492`), `tmp_path/"stub-bin-cat"` (`:1539`),
   `tmp_path/"stub-bin"` (`test_marker_script.py:2106`), `shim_dir` in
   `test_deny_private_project_refs.py` — so the new first argument has no
   ambient value it could default to. **22 of the 25 route through
   `_write_conditional_sleep_shim` and take only that one added argument
   under Dispatch A** (the `floor=` → `production_cap=15` swap at the two
   tier-B sites is 1:1 within that 22). **The remaining 3** —
   `test_lib.py:1495` (`stub-bin-find`), `test_lib.py:1542` (`stub-bin-cat`),
   and `test_marker_script.py:2119` (`stub-bin`) — write their own fake
   binary directly via `Path.write_text(...)`, never routing through
   `_write_conditional_sleep_shim`, so Dispatch A's edit to that helper
   doesn't reach their bin dir. Adding only the argument to their
   `assert_cap_engaged()` call, with no shim installed there, fails with the
   never-invoked message the moment Dispatch A lands alone. These 3 are
   Dispatch B's, not Dispatch A's — Critical files item 5 excludes them from
   its 22-site count for exactly this reason, and the Dispatch B table
   already carries their full shim-install treatment.
7. `[verified: claude/.claude/hooks/tests/test_nudge_error_mode_analysis.py:510-534]`
   — one site inverts the sleep invariant (re-verified this session by
   direct read). `test_friction_count_completes_when_slower_than_a_2s_cap`
   installs a shim that sleeps 3.5s under a **10s** cap and asserts the hook
   did *not* time out. Its sleep must stay strictly **under** the scaled
   cap, so it divides without rounding up. Its `assert elapsed >= 3.5` at
   `:534` becomes `assert_cap_not_engaged(fake_bin, production_cap=10)` —
   which additionally proves the shim was *invoked*, something the elapsed
   floor could only infer. Its sibling at `:493-508` (`sleep 15`, killed by
   the same cap) is the ordinary shape and today carries no timing
   assertion at all; it gains `assert_cap_engaged(fake_bin, production_cap=10)`.
8. `[verified: claude/.claude/hooks/tests/test_hook_alignment.py:1567-1570,
   test_record_session_end.py:312-316, test_marker_script.py:3498-3502]` —
   **three** sites, not two, chain two capped calls under one test and
   document that both must fire: two `_lib_jq` calls (12s = 2 × 5s + ~20%),
   two `_lib_jq` calls (20s = 2 × 5s + headroom), and the GNU and BSD `stat`
   forms "individually capped at 5s and tried in sequence" (14.5s). Each
   becomes `killed_calls=2`. No per-invocation marker name is needed: the
   append log records one line per invocation and the multiset difference
   counts them.
9. `[engineer-verified]` — `[[ "$1" =~ ^[0-9]+$ ]]` followed by `$(( ))`
   is an octal landmine, verified empirically this session: `019` aborts
   the arithmetic with "value too great for base" instead of reaching the
   passthrough, and `010` would silently evaluate as decimal 8. No current
   call site passes a zero-padded literal (every real site is a bare `2`,
   `5`, `10`, or `15`), and nothing pins that — hence the tightened regex
   and the `019` test case.
10. `[verified: pyproject.toml:18, claude/.claude/hooks/tests/conftest.py:21]`
    — `claude/.claude/tests` is on `pythonpath` and `conftest.py` already
    does `from helpers import HOOKS_DIR`, so `helpers.py` is importable from
    both test trees as a top-level module.
11. `[verified: claude/.claude/scripts/select-tests.py:243-246]` —
    `claude/.claude/tests/helpers.py` is in `GLOBAL_TRIGGER_PATHS`, so
    editing it makes `select-tests.py` select the full suite by its own
    rule. That is CLAUDE.md's documented case (1) for a legitimate
    full-suite run, not a manual widening.
12. `[engineer-verified]` — the exact population is **at least 43 confirmed
    real-sleep cap-boundary tests and is not reconciled to the epic's cited
    54**; do not treat 54 as verified. My own count adds context: 62
    `@pytest.mark.timing` occurrences repo-wide (58 in
    `claude/.claude/hooks/tests/` across 18 files, 4 in
    `claude/.claude/scripts/tests/` across 4 files), and ~26 hand-rolled
    shim-writing sites plus ~23 fixture `install(...)` calls with
    `sleep_seconds > 0`. A shim-writing site is not one-to-one with a test
    (one serves a parametrized pair at
    `test_deny_private_project_refs.py:4067-4072`), which is one source of
    the gap.
13. `[verified: claude/.claude/scripts/tests/test_cleanup_merged_branches.py:2255-2301,
    claude/.claude/scripts/tests/test_ci_watch.py:888-930]` — **two more
    cap-boundary tests exist in the scripts tree, outside the hooks-tree
    fixture/hand-rolled-pattern grep above.**
    `test_hung_pr_ref_fetch_is_capped_and_falls_back_to_stale_name` waits
    ~15s (15s cap, 30s stub sleep, floor 14.5, ceiling 19.5) and is the
    single most expensive test in the population.
    `test_direnv_export_wall_clock_cap_interrupts_stalled_envrc` waits ~5s
    (5s `_direnv-lib.sh` cap, 30s shim, floor 4.5, ceiling 20). Both carry
    their own `timeout(1)` skip guard already. Per CLAUDE.md's
    audit-structural-siblings rule both are in scope, and both are why the
    assertion helpers live in `helpers.py` rather than the hooks conftest.
14. `[verified: claude/.claude/hooks/tests/test_nudge_handoff_near_context_cap.py:646,688,743]`
    — that file's real-sleep sites use `sleep 3.5` against the 2s cap, not
    `sleep 10`. Three sites, all in the 2s tier.
15. `[verified: claude/.claude/hooks/tests/ — direct read this session]` —
    the previous Dispatch B table's timing-assertion column was incomplete
    in two directions, both corrected below. **Four ceilings were missing:**
    `test_marker_script.py:1953`, `:3466`, `:3500`, `:3547` (14 `elapsed <`
    sites repo-wide, not ten). **Four sites compute `elapsed` and never
    assert on it:** `test_nudge_handoff_near_context_cap.py:653,698,762,789`
    interpolate it only into failure messages that claim "the cap may have
    collapsed to the 5s `_lib_capped` default" — a claim no assertion in
    that file checks. `production_cap=2` checks it.
16. `[verified: claude/.claude/hooks/tests/test_nudge_long_turn_subagent.py:517-535]`
    — some hand-rolled tests carry auxiliary timing structure beyond the cap
    assertion: one test starts a thread, polls for `scan_state` on a 20s
    deadline, then probes a lock for 2.0s *while the stubbed `jq` is
    stalled*. The scaled 2s cap is 0.666s, so that 2.0s probe would outlive
    the stall it is supposed to observe. Any auxiliary window that must fall
    **inside** the stall divides by the same divisor and rounds **down**,
    and must end strictly before the scaled cap fires; a site where that
    cannot hold keeps its production-scale timing and is excluded from the
    sweep rather than made racy. **"Cannot hold" means less than 100ms of
    headroom between the rounded-down window and the scaled cap** — an
    absolute floor, not a percentage of an already-sub-second budget, so a
    site isn't declared safe by a margin that shrinks along with the cap.
    Below that floor, keep the site's production-scale timing rather than
    landing a window with no headroom against scheduling jitter. This site
    also runs the hook on a background thread, so it reads
    `caps_that_fired(stub_dir)` after `join()` rather than wrapping the run
    in the contextmanager.
17. `[verified: claude/.claude/hooks/tests/test_consume_durable_continuity_file_on_read.py:370]`
    — that site injects `RESUME_CONTEXT_HOOK_TIMEOUT_SECONDS="1"`, so the
    shim is handed `1`, not the hook's default 5
    (`consume-durable-continuity-file-on-read.sh:124`). Its assertion is
    `production_cap=1`. This is the one site where the caller-supplied
    duration is not a hook literal, and it is why the shim records `$1` as
    given rather than a tier name.
18. `[verified: claude/.claude/hooks/tests/conftest.py:175-176, :208-209 vs
    test_deny_private_project_refs.py:4079-4093]` — the named fixtures skip
    when neither binary is present; the direct `_write_conditional_sleep_shim`
    caller does not. Under a wall-clock floor that site merely ran slow on a
    coreutils-less host. Under markers it would **fail**, because no shim
    means no records, so that site gains the same two-line skip guard the
    fixtures carry. Behavior on those hosts stays what it is today.
19. `[verified: claude/.claude/hooks/tests/test_lib.py:511-523,
    test_hook_alignment.py:1545-1559]` — two sites build a **closed** `PATH`
    (`{"PATH": str(tmp_path)}`, no inherited entries) and symlink the real
    `timeout` binary into it. At those two, the shim **replaces** the
    symlink; it is not added alongside an inherited-PATH prepend the way
    every other Dispatch B site works. `Path.write_text` follows a symlink,
    so `write_scaled_timeout_shim` must `unlink(missing_ok=True)` its target
    before writing — writing through the surviving symlink would overwrite
    the host's real `timeout` binary.
20. `[unverified]` — no cap-boundary site nests one `_lib_capped` call
    inside another. A nested pair would let the outer cap's kill orphan the
    inner shim before it records completion, which reads as the inner cap
    firing. `production_cap=` pinning makes a nest at a *different* duration
    harmless; a same-duration nest would not be. Dispatch B confirms this
    per site while re-deriving each assertion, and the three chained sites
    of row 8 are sequential, not nested. **This is a one-time check, not a
    standing gate:** unlike the discriminator's other three failure modes
    (rows 5, 7, and the wrong-cap/wrong-count cases), a same-duration nest
    gets no committed test, so a future PR adding a new capped call site
    nested at the same duration as an existing one would not be caught by
    anything in this plan. Accepted as residual risk for this PR — see Out
    of scope — because a repo-wide static check for "a capped call
    reachable from within another capped call's own subprocess tree" is
    call-graph analysis across bash and Python, a heavier primitive than
    the risk (a false-positive-shaped test result, not a security bypass)
    justifies building now.

**Passthrough is never a silent pass, and it is no longer merely tolerable.**
A `$1` the regex rejects runs at the caller's own unscaled duration and
records nothing, so every marker-based assertion at that site fails loudly
with the never-invoked message. Under the retired floor mechanism the same
input made a floor-only site merely run slow and still pass. Converting that
case from slow-but-green to red is a direct consequence of the discriminator
change, not a separate guard.

The divisor is no longer a tradeoff: nothing in the discriminator depends
on its value, so it can be raised freely by a later follow-up.

## Critical files

**Dispatch A — shared mechanism. Must land and be verified before Dispatch
B starts.**

1. **`claude/.claude/tests/helpers.py`** (modify). Add one constant and
   seven functions, each encoding one transform or one observation so that
   no test site hand-picks a number or parses a log:
   - `TIMEOUT_SCALE_DIVISOR = 3`
   - `write_scaled_timeout_shim(bin_dir: Path) -> bool` — resolves
     `shutil.which("timeout") or shutil.which("gtimeout")`, creates the
     marker directory under `bin_dir`, `unlink(missing_ok=True)`s
     `bin_dir/"timeout"` (row 19 — a surviving symlink would send
     `write_text` through to the real binary), writes the script above at
     mode `0o755`, and returns `False` without writing when neither binary
     exists.
   - `scaled_cap(production_seconds: float) -> float` —
     `production_seconds / TIMEOUT_SCALE_DIVISOR`.
   - `scaled_shim_sleep(seconds: float) -> int` —
     `math.ceil(seconds / TIMEOUT_SCALE_DIVISOR)`, for a sleep that must
     **outlast** its cap.
   - `scaled_under_cap_sleep(seconds: float) -> float` — plain division, no
     rounding, for the one site whose sleep must **finish inside** its cap
     (row 7).
   - `caps_that_fired(bin_dir: Path) -> Counter[str]` — the multiset
     difference `started - completed`, keyed by the caller-supplied duration
     as written. Empty when no records exist.
   - `assert_cap_engaged(bin_dir, production_cap=None, killed_calls=1)` — a
     `@contextmanager`. Snapshots both logs on entry, re-reads on exit, and
     asserts on the delta so a second hook run inside the same `tmp_path`
     is not counted twice. Raises when the shim recorded nothing at all
     (the never-invoked case), with a message distinct from the one for
     "every invocation completed."
   - `assert_cap_not_engaged(bin_dir, production_cap=None)` — the inverse:
     the shim must have been invoked (at `production_cap`, when given) and
     nothing may have been killed. Used at row 7's inverted site and in the
     negative control.

   Each function carries a one-line docstring stating the durable fact only
   — that `124` is `timeout`'s documented kill status, that the logs are
   appended so a hook's several capped calls are counted rather than
   overwritten, that `scaled_under_cap_sleep` exists because rounding up
   would walk a sleep into the cap it must stay under.
   *Reuse:* this file is already the plain-module home for cross-tree test
   helpers and is already importable from both trees via `pyproject.toml`'s
   `pythonpath` — no new sharing mechanism, and no conftest-to-conftest
   import (which `_dead_pid`'s docstring at `conftest.py:60-69` explicitly
   rules out). Its "no pytest decorators here" contract is preserved:
   `@contextmanager` is `contextlib`, and every `pytest.skip` stays at its
   existing call site.

2. **`claude/.claude/hooks/tests/conftest.py`** (modify). Inside
   `_write_conditional_sleep_shim`, call `write_scaled_timeout_shim(bin_dir)`
   when `sleep_seconds > 0` and use `scaled_shim_sleep(sleep_seconds)` for
   the emitted `sleep` **only if that call returned `True`**. Delete
   `CAP_ENGAGED_FLOOR_SECONDS` (`:31`) and its comment, and delete
   `assert_cap_engaged` (`:224-244`) — it moves to `helpers.py` (row 13:
   two scripts-tree tests now need it and cannot import this conftest).
   Update the three docstrings that currently describe `sleep_seconds` in
   raw production seconds (`:119-129`, `:169-170`, `:202-203`) so they say
   the value is expressed in production-cap units and scaled on write.
   *Reuse:* `_write_conditional_sleep_shim` already takes `bin_dir` as its
   first parameter and both fixtures already prepend exactly that directory
   to `PATH` (`conftest.py:186`, `:219`) — the scaled `timeout` and its
   marker directory land in the existing bin dir through the existing
   parameter, with no second `PATH`-prepend mechanism and no new env var
   (which the two closed-`PATH` sites of row 19 could not receive).

3. **`claude/.claude/hooks/tests/test_scaled_timeout_shim.py`** (create).
   Two concerns, both sub-second:
   - **Bidirectional coverage of the shim.** An integer duration is scaled
     (`timeout 5 sleep 3` through the shim exits 124 in well under 1s); the
     emitted duration string is exactly `1.666`/`0.666`/`3.333`/`5.000` for
     inputs `5`/`2`/`10`/`15`; each of `019`, `0`, `2.5`, and an empty `$1`
     passes through **unscaled and records nothing** (row 9 — `019` is the
     regression case, and its absence from today's call sites is exactly
     why nothing else pins it); a killed invocation records its duration in
     `started` and not in `completed` while a completing one records it in
     both (row 5, both directions); three invocations of which one is killed
     leave `caps_that_fired() == {"5": 1}`, pinning the append-and-count
     behavior a 21-call hook run depends on; `scaled_shim_sleep` never
     returns a value at or below its tier's scaled cap (`10 → 4 > 1.666`,
     `20 → 7 > 5.0`, `30 → 10 > 5.0`, `3.5 → 2 > 0.666`);
     `scaled_under_cap_sleep(3.5)` stays below `scaled_cap(10)`; and a
     non-numeric `$1` (`abc`) and a negative one (`-5`) both take the same
     unscaled passthrough as `019`/`0`/`2.5`, rounding out the regex-boundary
     enumeration.
   - **`TestProtectedProbeOrderTestsAreUnscaled`.** For each of the five
     `test_lib.py` functions named in row 4, imported via
     `from .test_lib import …` (the tree is a package — see
     `.claude/rules/test-tree-packaging.md`), assert that
     `inspect.getsource(fn)` contains none of `write_scaled_timeout_shim`,
     `scaled_shim_sleep`, or `TIMEOUT_SCALE_DIVISOR`. Four of the five would
     otherwise keep passing while silently no longer testing their premise,
     so no runtime assertion in those tests can catch this — only a source
     check can. The class docstring names that fact; the import itself fails
     loudly if a protected test is renamed or deleted. **This is a tripwire
     against the likely accidental-drift path (an editor routing a protected
     test through the shared helper directly), not an exhaustive guarantee:**
     `inspect.getsource` reads only the named test function's own literal
     source, not its transitive call graph, so a future refactor that moves
     the scaling call into a shared setup helper the protected test then
     calls would evade it. Accepted for the same reason
     `test-conventions` names this pattern acceptable narrowly as a
     wiring-presence check, never as the sole guard.
     *Reuse:* `claude/.claude/tests/test_pytest_collection_config.py` is the
     existing precedent for a repo convention enforced by a collected test
     rather than by review attention.
   - **`TestCapMarkersDetectNonFiringCap`.** A committed negative control
     for the discriminator itself, at every site at once rather than per
     floor, because every site now shares one body. Four legs, all using
     `git_timeout_shim` and needing no shared-code change:
     - **Completed, not killed.** `git_timeout_shim('[ "$1" = "diff" ]',
       sleep_seconds=0)` writes a shim that never delays; wrap
       `pytest.raises(AssertionError)` around the same `assert_cap_engaged`
       call `test_deny_pii_in_commits.py`'s `test_staged_diff_git_timeout_denied`
       makes with the default `sleep_seconds`. `sleep_seconds=0` is already
       in production use in this suite (`test_marker_script.py:1083,1123`),
       so this adds a test case, not a toggle in shared infrastructure.
     - **Never invoked.** Against a bin dir where no shim was written,
       `assert_cap_engaged` must raise, and its message must name the
       never-invoked case rather than the completed case. This is the
       fail-closed property's own proof, and no wall-clock floor could have
       carried it.
     - **Wrong cap.** With a real 5s-cap kill recorded,
       `assert_cap_engaged(bin_dir, production_cap=15)` must raise while
       `production_cap=5` passes. This is the committed replacement for
       `CUMULATIVE_DIFF_CAP_FLOOR_SECONDS`'s regression guarantee, and
       unlike that floor it costs about a second and runs in CI.
     - **Wrong `killed_calls` count.** Record two kills at the same duration
       (two `git_timeout_shim` invocations that both stall past the cap
       inside one `with` block); `assert_cap_engaged(bin_dir,
       production_cap=5, killed_calls=2)` must pass,
       `killed_calls=1` and `killed_calls=3` against that same recording must
       both raise. The three row-8 chained-call sites depend on this
       comparison being exact, not merely truthy, and no other leg exercises
       `killed_calls` at any value but the implicit default of 1.
     *Reuse:* all four legs wrap an existing, already-tested call site with
     an existing fixture parameter. Unlike the floor-era version, none of
     them imports `TestMarkerScriptCumulativeReview`, reaches for the
     `cumulative_diff_repo` fixture across modules, or depends on
     `fake_output` to keep a zero-sleep shim off the real `gh` binary.

4. **`claude/.claude/hooks/tests/test_lib.py:533-676`** (modify — comments
   only). Add one line above each of the five protected tests naming the
   specific premise a scaled `timeout` shim would defeat there. Each must
   stand alone without this plan:
   - `:533` — `# Deliberately builds a PATH with no timeout(1): installing any fake timeout here removes the absent-binary condition this test is named for, and the OK assertion below would still pass.`
   - `:560` — `# The one cap-boundary test that runs against the real timeout(1) with nothing interposed, so the suite keeps end-to-end evidence that the binary itself enforces a cap.`
   - `:588` — `# A fake timeout(1) on this PATH would win _lib_capped_for's first probe, so the gtimeout branch under test would never execute and the exit-124 assertion would still pass.`
   - `:618` — `# Both binaries are absent on purpose: a fake timeout(1) here would cap the call and invert the uncapped result this asserts.`
   - `:645` — `# Probe order is the subject: a fake timeout(1) here would be the binary that wins, so the test would prove the fake was preferred rather than the real one.`

5. **Fixture-based files — one mechanical edit per call site, no logic
   change.** `test_deny_pii_in_commits.py` (4 sites), `test_check_skill_length.py`
   (5), `test_check_claude_md_length.py` (1), `test_require_ready_for_review.py`
   (6), `test_deny_private_project_refs.py` (4, plus the row-18 skip guard),
   `test_marker_script.py` (2, its two `floor=self.CUMULATIVE_DIFF_CAP_FLOOR_SECONDS`
   sites). Each site's `assert_cap_engaged()` gains the bin dir that site
   already holds (row 6 lists which, per file); the two `floor=...` sites
   become `production_cap=15` and the class attribute at
   `test_marker_script.py:2353` is deleted with its four-line comment. All
   7 files' `from .conftest import assert_cap_engaged` lines move to
   `from helpers import assert_cap_engaged`; three of them import other
   conftest names on the same line and split into two imports.
   **Excluded from this list, despite calling `assert_cap_engaged()` today:**
   `test_lib.py:1495` (`stub-bin-find`), `test_lib.py:1542` (`stub-bin-cat`),
   and `test_marker_script.py:2119` (`stub-bin`, the
   `test_code_review_value_computation_times_out_gracefully` site) each
   write their own fake binary directly via `Path.write_text(...)`, never
   routing through `_write_conditional_sleep_shim` — confirmed by direct
   read of all three sites. Dispatch A's edit to that helper does not reach
   their bin dir, so adding only the argument to their `assert_cap_engaged()`
   call, with no shim installed there, would fail with the never-invoked
   message the moment Dispatch A lands alone. All three already receive the
   full shim-install treatment as their own Dispatch B table rows; they must
   not also appear in this list or in Dispatch A's acceptance criterion below.
   Dispatch A's acceptance criterion is: every one of these 22 sites passes with
   a one-argument edit and no change to what it asserts about the hook.

**Dispatch B — hand-rolled sites, all in one dispatch.** Every site needs
the same three steps: install the scaled `timeout`, replace the literal
`sleep N` with `scaled_shim_sleep(N)`, and **replace every co-located
wall-clock assertion with the marker assertion**, deleting the now-unused
`start`/`elapsed` locals. The "Shim install" column distinguishes the two
shapes (row 19): *prepend* sites already prepend a stub dir to an inherited
`PATH`, so the shim is written into that same dir; *replace* sites build a
closed `PATH` and symlink the real `timeout` into it, so
`write_scaled_timeout_shim` unlinks that symlink first. Cap values marked †
were read from the site's own `elapsed <` message rather than from the hook
source — confirm each against its call site before asserting on it; a wrong
value fails loudly rather than passing.

| File | Shim install | Sites, and the assertion each one becomes |
|---|---|---|
| `claude/.claude/hooks/tests/test_marker_script.py` | prepend | `1934` + ceiling `1953` → `production_cap=5`†; `2112` (already wrapped at `2119`) → add `stub_dir`, `production_cap=5`; `3449` + ceiling `3466` → `production_cap=5`†; `3483` + ceiling `3500` → `production_cap=5, killed_calls=2` (GNU then BSD `stat`, row 8); `3528` + ceiling `3547` → `production_cap=5`†; `3578` is a tripwire sleep with no timing assertion — scale it, assert nothing; `2689`/`2720` → `production_cap=15`, deleting `CUMULATIVE_DIFF_CAP_FLOOR_SECONDS` at `2353` |
| `claude/.claude/hooks/tests/test_lib.py` | `511` **replace** (symlink at `:515`); `1495`, `1542` prepend | `511` + ceiling `530` → `assert_cap_engaged(tmp_path, production_cap=5)`; `1495` → `assert_cap_engaged(stub_dir, production_cap=5)` (`stub-bin-find`); `1542` → same with `stub-bin-cat`. **Do not touch `533-676`** (row 4, five tests) |
| `claude/.claude/hooks/tests/test_marker_lib.py` | prepend | `273` (`sleep 30`) + ceiling `289` → `production_cap=5`; `618` (`sleep 10`) + floor `639` → `production_cap=SLOW_PATH_TAIL_TIMEOUT_CAP_SECONDS` (2, `:19`). The `scaled_cap - 0.5` outlier problem disappears with the floor; delete the 4-line no-upper-bound comment at `635-638` with the assertion it explains |
| `claude/.claude/hooks/tests/test_require_plan_review.py` | prepend | `2308` + ceiling `2324` → `production_cap=5`†; `2362` + ceiling `2378` → `production_cap=5`† |
| `claude/.claude/hooks/tests/test_hook_alignment.py` | `1550` **replace** (closed PATH at `:1559`) | `1548` (`sleep 10`) + ceiling `1570` → `assert_cap_engaged(stub_bin, production_cap=5, killed_calls=2)`. The comment at `1567-1569` cross-referencing `test_lib.py`'s `elapsed < 6` describes current behavior rather than recording history, so it is deleted with the assertion |
| `claude/.claude/hooks/tests/test_consume_durable_continuity_file_on_read.py` | prepend | `361` + ceiling `374` → `production_cap=1` — the test injects `RESUME_CONTEXT_HOOK_TIMEOUT_SECONDS="1"` at `:370` (row 17), so the shim sees `1`, not the hook's default 5 |
| `claude/.claude/hooks/tests/test_nudge_error_mode_analysis.py` | prepend | `504` (`sleep 15` killed by the 10s cap, no timing assertion today) → gains `assert_cap_engaged(fake_bin, production_cap=10)`; `522`/`534` are the **inverted** shape (row 7): `scaled_under_cap_sleep(3.5)` = 1.166, and `assert elapsed >= 3.5` at `:534` becomes `assert_cap_not_engaged(fake_bin, production_cap=10)` |
| `claude/.claude/hooks/tests/test_record_session_end.py` | prepend | `291` + ceiling `316` → `assert_cap_engaged(stub_dir, production_cap=5, killed_calls=2)`. The `timeout_path` at `:284` is a skip probe only, not a symlink source — `write_scaled_timeout_shim` resolves its own |
| `claude/.claude/hooks/tests/test_require_architect_consult.py` | prepend | `190` + ceiling `205` → `production_cap=5`† |
| `claude/.claude/hooks/tests/test_nudge_long_turn_subagent.py` | prepend | `504`, `733`, `865`, `977` → `production_cap=2`†. The `504` site runs the hook on a background thread, so it reads `caps_that_fired(stub_dir)` after `join()` rather than using the contextmanager; its poll/probe deadlines at `524-535` are auxiliary windows under row 16's rule — scale down, round down, and confirm the probe ends before the 0.666s scaled cap fires, or leave that one site unscaled |
| `claude/.claude/hooks/tests/test_nudge_handoff_near_context_cap.py` | prepend | `646`, `688`, `743` (all `sleep 3.5`, 2s tier → 2) → `production_cap=2` at each of the four runs. Delete the `start`/`elapsed` locals at `649/653`, `694/698`, `760/762`, `787/789` and drop the `(took {elapsed:.1f}s)` interpolation from the four messages: the marker now checks the 2s-vs-5s claim those messages make (row 15) |
| `claude/.claude/scripts/tests/test_cleanup_merged_branches.py` | prepend (`stub_dir`, `:2283`) | `2269-2301`: `sleep 30 → 10`; delete floor `2290` and ceiling `2295` → `assert_cap_engaged(stub_dir, production_cap=15)` |
| `claude/.claude/scripts/tests/test_ci_watch.py` | prepend (the dir `fake_gh` prepends) | `888-930`: `_direnv_shim_source_stalls_without_reading_stdin(30) → (10)`; delete floor `917` and ceiling `921` → `assert_cap_engaged(<that dir>, production_cap=5)` |

At every site, confirm before moving on that the capped call under test is
not itself nested inside another capped call (row 20) — a nest at the same
duration is the one shape the marker cannot distinguish.

*Reuse across Dispatch B:* every site calls the same `helpers.py` functions,
and every site's discrimination now comes from the same shim body rather
than from a per-site threshold. Nothing is reimplemented per file; the
per-site work is locating the existing bin dir, the existing `sleep`
literal, and the production cap the site's own comment names.

**Dispatch split rationale.** Two sequenced `code-writer` dispatches, not
three. Dispatch A is a strict prerequisite — Dispatch B's every edit calls
symbols A creates, and A also changes 22 of the 25 `assert_cap_engaged` call
sites (the remaining 3 are hand-rolled and are Dispatch B's own work, per
Critical files item 5) that B's files share a helper with. The hand-rolled
files, though disjoint as file sets, must **not** be split across parallel
dispatches. Both halves
would need the identical shared-state background restated (the divisor, the
two transforms, the marker contract, row 16's per-site judgment, row 20's
nesting check), and two agents can resolve that judgment differently with
neither one's self-review seeing the other's — exactly the condition
`plan-it/SKILL.md` names for not splitting. Neither dispatch takes
`isolation: "worktree"`; both run in this branch's existing worktree.

## Verification

1. **Production diff is empty.** The plan's central safety claim is
   mechanically checkable, so check it rather than asserting it — but the
   naive form of this command cannot report zero once this plan's own work
   exists, because `claude/.claude/hooks/` recursively contains
   `claude/.claude/hooks/tests/`, and a `scripts/*.sh` glob silently covers
   neither `scripts/*.py` nor `scripts/transcript_analysis/`. Both sides
   must be recursive with the test trees excluded. **Diff against this
   branch's actual merge-base with `origin/main`, not against the literal
   ref** — verified this session: `origin/main` had already advanced by an
   unrelated merged PR by the time this plan was written, and diffing
   against the moving ref would show that unrelated commit's files as a
   false positive on this exact check:

   ```
   git merge-base HEAD origin/main
   # then, using that commit (do not inline the command substitution —
   # this repo's worktree-isolation Bash guard rejects nested $(...)):
   git diff --stat <merge-base-sha> -- \
     claude/.claude/hooks ':(exclude)claude/.claude/hooks/tests' \
     claude/.claude/scripts ':(exclude)claude/.claude/scripts/tests'
   ```

   Confirmed working this session against this exact worktree: the
   pathspec syntax runs clean (no git error), and diffing against the real
   merge-base (rather than the literal `origin/main`, which had already
   moved) correctly reports empty today, before any of this plan's edits
   exist. Single-quote each `:(exclude)` term so the shell does not treat
   the parentheses as syntax. Then run the over-accept/under-accept pair on
   the command itself, once this plan's edits exist: dropping the two
   exclude terms **must** list this plan's own test-tree edits (proving the
   pathspec reaches those files at all), and adding them back **must** drop
   exactly those files and nothing else.

   This check is this plan's central safety claim, and it is a manual
   command a human/agent runs and reads rather than an automated gate — the
   other two invariants this plan protects (the discriminator's own
   fail-closed property, the five protected `test_lib.py` tests) each also
   get a committed, permanently-running test; this one does not, because
   "this PR's diff against its own merge-base is empty" is not a standing
   repo invariant a future, unrelated PR could assert forever. Re-run this
   exact command again immediately before `/ready-for-review`, not only once
   mid-implementation, so it covers the branch's final state rather than an
   intermediate one.

2. **Baseline, captured before any edit** (needed for the comparison, so do
   this first):
   `.venv/bin/python3 -m pytest claude/.claude/ -m timing -n0 --durations=0`
   — record total wall clock and the per-test durations table.

3. **Full suite.** `.venv/bin/python3 claude/.claude/scripts/select-tests.py`
   selects the **whole suite** for this diff, because
   `claude/.claude/tests/helpers.py` is in `GLOBAL_TRIGGER_PATHS`
   (`select-tests.py:243-246`). That is CLAUDE.md's documented case (1) —
   `select-tests.py` widened on its own, so run it as-is; do not widen by
   hand and do not treat the width as a rule-table bug.

4. **Serial timing re-run, after.**
   `.venv/bin/python3 -m pytest claude/.claude/ -m timing -n0 --durations=0`.
   Three readings from the result:
   - **Wall clock.** Against the dispatching session's own ≈220-260s
     estimate of pure serial sleep-and-wait, a divisor of 3 projects
     ≈85-95s — roughly a 60% cut, for the reason stated in Approach. Report
     the measured before/after totals; the projection is arithmetic on an
     unreconciled population (row 12), not a measurement.
   - **Per-test ceiling.** The two 15s-cap tests become the slowest in the
     population at ~5s plus their own work; no individual test in the
     `-m timing -n0` table should exceed ~6.5s. Any test still parked near
     10s or 15s is a site the sweep missed, or row 1's sleep-governed
     hypothesis holding at a site where only the cap was scaled.
   - **Whether row 1 holds, and therefore whether a larger divisor would
     buy anything.** Compare each converted test's measured elapsed against
     its site's `scaled_shim_sleep(N)` and its `scaled_cap`. Elapsed
     tracking the sleep confirms row 1 and makes raising the divisor the
     obvious follow-up; elapsed tracking the cap refutes it and means the
     remaining cost is elsewhere. Record which, with the numbers — this is
     the input the Out-of-scope divisor bullet names. Also re-measure the
     shim's own per-call overhead in its new fork-and-wait form: a
     fork+exec model has different per-call overhead than this
     fake-timeout-stays-resident design.
     **This comparison is only clean for single-capped-call sites.** The
     marker log records the caller-supplied duration, not a per-invocation
     timestamp, so for the three row-8 chained-call sites, the 21-call
     `nudge-handoff-near-context-cap.sh` site, and the background-thread
     site (row 16), `--durations=0`'s one wall-clock number per test is a
     sum or max over several capped calls and cannot be attributed to any
     one call's sleep-vs-cap behavior. Answer row 1 from the single-call
     sites; treat the multi-call sites' numbers as directional only. Adding
     per-invocation timestamps to the marker log would resolve this but is
     its own instrumentation change with its own overhead (an added `date`
     fork per invocation) — left for the divisor follow-up in Out of scope
     to add only if the single-call sites' answer is inconclusive.

5. **Negative control.** Three legs. The shim's *scaling* is proven by two
   one-off manual checks; the shim's *discrimination* is proven by a
   committed test that now covers every site uniformly, because every site
   shares one shim body. Legs 1 and 2 stay manual: committing them means
   editing the fake `timeout`'s own emitted body under a permanent runtime
   toggle, embedding a switch that disables a security discriminator into
   test infrastructure — a heavier primitive than a one-time proof needs.
   - Edit the fake `timeout` to run the real binary with unmodified
     arguments, and confirm the cap-boundary tests return to their original
     durations and still pass (over-accept).
   - Edit it to drop the duration argument entirely (uncapped) and confirm
     they **fail** (under-accept, on the shim).
   - Confirmed by a committed test (`TestCapMarkersDetectNonFiringCap`,
     Critical files item 3): the assertion fails when the shim ran and
     nothing was killed, fails with a distinguishable message when the shim
     was never invoked, fails when the killed cap's duration is not the one
     the site asserts, and fails when the number of kills recorded doesn't
     match `killed_calls` exactly — the last of these is the one property
     the three row-8 chained-call sites depend on with no other coverage.
     This is the whole discrimination proof for all three of Dispatch A's
     shared-helper sites and all thirteen of Dispatch B's hand-rolled ones at
     once — a single discriminator with one body, in place of six
     independently-derived wall-clock floors that each had their own way to
     go inert.

   Revert the two manual edits.

6. **Protected `test_lib.py` range untouched.** Two checks, closing
   different gaps:
   - `.venv/bin/python3 -m pytest claude/.claude/hooks/tests/test_scaled_timeout_shim.py -k ProtectedProbeOrder`
     — the durable one; it catches a scaled shim being added to any of the
     five protected tests, and keeps catching it after this PR merges.
   - `git diff -U0 <merge-base-sha> -- claude/.claude/hooks/tests/test_lib.py | grep -E '^@@'`
     — same merge-base caveat as step 1: use this branch's actual fork
     point, not the literal `origin/main`, so an unrelated upstream commit
     to this file after this branch forked can't produce a false-positive
     hunk. No hunk's **old-side** range (the `-a,b` field, numbered against
     the merge-base and therefore unmoved by this branch's own edits) may
     intersect `533-676`, except for the five comment insertions of
     Critical files item 4. This catches any edit at all inside the range,
     including ones the source scan cannot see, such as a changed `sleep`
     literal.

7. **Lint.** `.venv/bin/ruff check claude/.claude/` and
   `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck` — the
   second only confirms no tracked shell file changed, since the shim is a
   Python-emitted string, not a tracked script.

8. `/code-review`, then `/ready-for-review` before opening the PR (both
   hook-enforced in this repo).

## Out of scope

- **Retuning the production caps themselves.** This plan could change
  2/5/10/15 — they are literal integers in this repo's own hook files — and
  deliberately does not. Each is grounded by its own inline measurement
  comment (`nudge-error-mode-analysis.sh:134-138`, `_lib.sh:636-638`,
  `_lib.sh:721-723`), and re-deriving one is a security-budget decision with
  a different threat model than this plan's. Every one stays byte-identical.
- **Raising `TIMEOUT_SCALE_DIVISOR` past 3, and making `scaled_shim_sleep`
  fractional.** These are the remaining levers, and the marker mechanism is
  what makes them cheap: the divisor is no longer derived from any
  measurement, so raising it changes one constant and re-derives no
  assertion. Two things gate the follow-up rather than this PR. First,
  Verification step 4's third reading tells you whether the saving is in the
  cap or in `math.ceil`'s one-second sleep floor — raising the divisor
  without that answer is guessing. Second, a scaled cap applies to the
  *legitimately completing* capped calls in the same hook run, not only to
  the stalling one, and the 2s tier is where that margin is thinnest
  (`nudge-handoff-near-context-cap.sh` makes 21 capped calls at 2s); the
  follow-up measures a single real capped call's duration on that tier
  before going past 3. Its failure mode is a loud test failure either way,
  which is why it is a follow-up rather than a blocker.
- **`check-branch-divergence.sh`'s independent `TIMEOUT_CMD` wrapper.** It
  does its own probe-then-run rather than routing through
  `_lib_capped_for`, and it has no cap-boundary real-sleep test today —
  `test_check_branch_divergence.py:368` exercises only the
  neither-binary-present fallback. There is no wall-clock cost to cut
  there. Worth aligning on the shared mechanism in a future pass for
  consistency, not this one.
- **Migrating the ~26 hand-rolled shim sites onto
  `git_timeout_shim`/`gh_timeout_shim`.** Deferred per the four bash shapes
  named in Approach; it is a separable refactor with per-test
  behavior-drift risk, and CLAUDE.md's Scope Discipline Axis 4 favors the
  mechanical in-place fix here. The marker mechanism does not depend on it:
  the shared observation lives in the `timeout` shim, which every site
  installs regardless of what shape its own sleep shim takes.
- **Changing which tests carry `@pytest.mark.timing`, or the `-m timing -n0`
  serial split.** This plan could shorten these tests enough to argue some
  no longer need serial isolation, and deliberately does not: the marker
  also covers tests serialized for subprocess-spawn contention unrelated to
  a cap wait (row 12), and `test_pytest_collection_config.py`'s
  complementary-partition test makes re-marking a change with its own
  blast radius. Re-examine it once the measured durations from Verification
  step 4 exist.
- **A repo-wide static check for same-duration nested capped calls (row
  20).** No cap-boundary site nests one `_lib_capped` call inside another
  today (confirmed per-site during Dispatch B), but nothing in this plan
  would catch a future PR introducing one at the same duration as its
  outer call — the one discriminator failure mode here with no committed
  test. Building a call-graph-aware static check now is disproportionate to
  a risk that surfaces as a wrong test result, not a security bypass;
  revisit if Dispatch B's per-site pass finds this pattern is more common
  than expected, or if a future nested-call bug report shows the risk is
  live.
- **A CI-level path-scoped diff gate backing Verification step 1.** This
  plan's central safety claim — zero production diff — is checked by a
  manual command run and read by a human/agent, unlike the discriminator's
  own correctness and the five protected `test_lib.py` tests, which each
  also get a committed, permanently-running test. A repo-wide standing
  invariant isn't the right shape here (a future, unrelated PR is expected
  to touch `claude/.claude/hooks`/`claude/.claude/scripts` again), but a
  PR-scoped CI check — this branch's diff against its target, failing on
  any non-test hit under those two paths — would make the claim a
  repeatable automated gate instead of a one-time read. Deferred as
  CI-workflow scope beyond this Unit.
- **The remaining 247-tests-at-≥1.0s band from the epic.** Unit 2 targets
  only the cap-boundary population. Whatever is left above 1.0s after this
  lands is input to a later unit, not a gap in this one.
