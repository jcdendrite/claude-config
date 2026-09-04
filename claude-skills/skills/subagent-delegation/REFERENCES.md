# References — subagent-delegation

Edit-time reference. Not loaded at skill runtime — read manually when editing
`SKILL.md` to verify a rule still holds or to add new guidance.

## Heavy command output — harness truncation and check-suite sizes

Harness re-probe (bisection), 2026-09-02, Claude Code 2.1.259:

- Bash tool output truncates above 30,000 decimal bytes.
- The returned preview is exactly 2,000 decimal bytes and is
  first-bytes-only — the harness returns the *head* of the output, not
  the tail.
- Overflow persists byte-for-byte to a `tool-results/` file the model
  does not auto-read.

Pass-path figure: 9,429 bytes at 7573 passed / 54 skipped, 2026-09-02, Claude
Code 2.1.259 — the largest observed inline run of this repo's own documented
check commands, 31% of the truncation threshold.

Failure-path figure: 10,881 bytes at 1 failed, 7540 passed, 54 skipped, 1
warning, 2026-09-03, Claude Code 2.1.259 — one deliberately-failing assertion
introduced in a single test file, full suite (`pytest claude/.claude/`),
reverted immediately after measurement. The suite size differs from the
pass-path figure above because this worktree's collected item count had
already drifted between the two measurement dates. The figure is dated
against the count it actually ran with, not re-baselined to match.

For the 2026-05-22 – 2026-06-23 transcript-corpus measurement (953 inline
check runs, median 117 chars, p90 2 KB, p99 9 KB, max 24.5 KB, no run hit the
30 KB harness-truncation threshold), see `docs/case-studies/check-runner.md`
§ "Retirement (2026-06-23)" rather than re-deriving it here.

## Diagnosis-delegation: two variants, not one

`subagent-delegation/SKILL.md`'s own "Debug-investigation probe" section is
the canonical home for the "delegate the failure diagnosis" rule.
`ready-for-review` instead dispatches a full `/root-cause-analysis` run to
`general-purpose` on a CI failure, because none of the probe section's
preconditions hold there:

- The parent never held the remote CI output to begin with, so the Step 1
  carve-out for a failure or diff reasoned over line by line does not apply.
- Its context is already spent surveying the whole branch rather than one
  command's failure.
- Its own step 4 is offer-don't-act, so the parent relays the returned
  diagnosis rather than reasoning over it directly.

The two behaviors are deliberately not unified into one dispatch shape.

## Debug-investigation probe: read-only probe vs. write-capable agent

`docs/design-decisions.md §18` — the authoritative rationale for why the probe
stays read-only: the parent retains the edit and the judgment; a write-capable
agent re-introduces the model-agency failure class documented in the check-runner
retirement (see below).

`docs/case-studies/check-runner.md` — the model-agency failure record and
retirement rationale. See `docs/case-studies/check-runner.md` §
"Retirement (2026-06-23)" for the corpus measurement that grounds the
inline-run policy.
