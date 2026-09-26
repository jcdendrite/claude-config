# Round-2 `plan-architect` consult pilot: revert

*2026-09-15.*

The pilot pre-registered in
[round2-consult-trigger-pilot.md](round2-consult-trigger-pilot.md) is
closed. Decision: **revert** — do not adopt the round-2 consult-gate
trigger. The existing round-3 gate
([round3-plan-architect-consult-gate.md](round3-plan-architect-consult-gate.md))
remains the unchanged default; per the pre-registration's own Scope
section, a reverted pilot does not supersede it.

G2 — the round-2-to-round-3 escalation rate, i.e. the share of a branch
population that still reaches a third code-review round — is the pilot's
decisive outcome measure, pre-registered in
[round2-consult-trigger-pilot.md](round2-consult-trigger-pilot.md), which
defines **gate-truth** and **proxy-on-proxy**, the two independently-computed
checks required to agree. Both had to clear the pre-registered
adopt-candidate bar — an escalation rate at or below 25% — for cap=1 to
become an adoption candidate; either check failing the bar means revert.
G2 failed the adopt bar on both required checks, each by a wide margin,
pooled across the two machines that ran the pilot. Pooled from each
machine's own `review-trace --this-repo --deny-only` / `--skill
code-review` output, computed 2026-09-15:

- **Gate-truth, primary:** 13/18 = 72.2%
- **Gate-truth, adverse sensitivity** (a stricter reading that counts every
  branch whose round-3 outcome isn't yet fully certain as if it had
  reached round 3, rather than excluding it): 14/19 = 73.7%
- **Proxy-on-proxy:** 26/35 = 74.3%

All three exceed the ≤25% adopt bar by roughly 3x, and all three exceed
the 50% historical baseline besides — branches in the pilot reached round
3 more often than branches did before the pilot ran, not less. Two are
individually significant in the wrong direction under the
pre-registration's own one-sided exact binomial test:

- Proxy-on-proxy: P(X≥26 | n=35, p=0.5)=0.0030 — about 3 times in 1,000 by
  chance if the true rate were 50%.
- Gate-truth adverse: P(X≥14 | n=19, p=0.5)=0.0318 — about 3 times in 100.
- Gate-truth primary is directionally consistent but not itself
  individually significant: P(X≥13 | n=18, p=0.5)=0.0481 — about 5 times
  in 100, just past the conventional 5%-chance significance threshold.

A separate, unrelated defect was found and fixed in the pilot's own
config-key resolver during this evaluation:
`_lib_reviewer_round_state_cap()` (`claude/.claude/hooks/_lib.sh`) never
read the pilot's
`round_consult_round2_pilot` `claude-config.toml` key, so the cap silently
reverted to its default the moment an unrelated TOML migration deleted the
legacy sentinel file the resolver depended on instead. That bug affected
only the pilot's *arming* mechanism after accrual had already finished —
every firing recorded during the pilot's active window carried CAP=1 per
its own deny-message text, so the treatment itself was applied correctly
throughout accrual. The G2 result above is not an artifact of that defect.
A second session found the same gap independently and merged the fix as
PR 1016 before this decision record's own PR did, so the resolver is
already corrected on `main`.

**Operational follow-up.** `round_consult_round2_pilot` must be set to
`false` in `claude-config.toml` on every machine that ran this pilot, once
PR 1016's resolver fix is pulled — otherwise the now-correctly-wired
resolver would silently re-arm CAP=1 treatment for a pilot that has
concluded.

## Sources

- [round2-consult-trigger-pilot.md](round2-consult-trigger-pilot.md) — the
  pre-registration: mechanism, G0–G4 gates, and the adopt/revert decision
  rule.
- [round3-plan-architect-consult-gate.md](round3-plan-architect-consult-gate.md) —
  the gate this pilot tested moving earlier.
- `docs/case-studies/opus-frontload-review-rounds.md` — the 50%
  round-2-to-round-3 historical baseline this pilot's G2 compares against.
- `claude/.claude/hooks/_lib.sh` — `_lib_reviewer_round_state_cap`, fixed to
  read `round_consult_round2_pilot` via `_config_enabled`.
- `claude/.claude/hooks/require-architect-consult.sh`,
  `claude/.claude/hooks/log-reviewer-round.sh` — the gate and recorder the
  fixed resolver feeds.
