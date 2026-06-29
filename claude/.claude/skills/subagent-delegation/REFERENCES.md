# References — subagent-delegation

Edit-time reference. Not loaded at skill runtime — read manually when editing
`SKILL.md` to verify a rule still holds or to add new guidance.

## Debug-investigation probe: read-only probe vs. write-capable agent

`docs/design-decisions.md §18` — the authoritative rationale for why the probe
stays read-only: the parent retains the edit and the judgment; a write-capable
agent re-introduces the model-agency failure class documented in the check-runner
retirement (see below).

`docs/case-studies/check-runner.md` — the model-agency failure record and
retirement rationale. Also contains the corpus measurement that grounds the
inline-run policy: 953 inline check runs, median 117 chars, p90 2 KB, p99 9 KB,
max 24.5 KB — no run hit the 30 KB harness-truncation threshold.
