# Time-boxed round-2 `plan-architect` consult pilot, via an opt-in machine sentinel

*2026-09-08.*

The round-3-triggered `plan-architect MODE=consult` gate
([round3-plan-architect-consult-gate.md](round3-plan-architect-consult-gate.md))
interrupts a non-converging review loop only after two rounds have already
been paid for. Whether firing one round earlier — at entry to round 2
instead of round 3 — converts tail branches into converging ones is
untested: the only prior evidence,
`docs/case-studies/opus-frontload-review-rounds.md`'s G0–G3 arms, measures
front-loading Opus into authoring, not a `plan-architect` consult
specifically. This entry ships a pilot to test that hypothesis directly,
for a bounded, pre-registered sample, rather than widen the gate's default
on the strength of an untested analogy.

## Mechanism

An opt-in, presence-only, machine-scoped sentinel —
`<config-dir>/.round-consult-round2-pilot` — lowers the shared round-state
cap from 2 to 1, resolved by a new zero-arity function,
`_lib.sh`'s `_lib_reviewer_round_state_cap`, consumed by both
`require-architect-consult.sh` (the read/deny side) and
`log-reviewer-round.sh` (the write/recorder side). Every other stow
consumer keeps today's cap of 2 (entry to round 3 trips the gate)
unaffected — this is deliberately not a change to the default. Two lighter
alternatives were rejected:

- **Flipping `_LIB_REVIEWER_ROUND_STATE_CAP` to `1` for a pilot window** —
  fewer lines, but it ships a *deny* gate at roughly double frequency to
  every stow consumer for an experiment none of them opted into, and
  rollback would need a second PR rather than an `rm`.
- **An environment-variable override** — rejected because exposure
  attribution is the pilot's whole product: an env var can differ silently
  between terminals and appears in no `install.sh` inventory, so "was this
  branch under pilot conditions?" stops being answerable from machine
  state.

Hand-dispatching the consult at round 2 instead of adding code was also
considered and rejected: self-selected exposure (the engineer remembering
to do it on branches that already feel troubled) is exactly the confound
that would void the result, and this repo's own convention holds that an
automatic-trigger request needs a hook, not prose.

The deny message and both hooks' header comments state the resolved cap
and the fact that the current state is new, without hardcoding an ordinal
word — so the same text is accurate whether the cap is 2 or 1.
This wording lives in the shared code path, so it reaches every stow
consumer on their next `git pull`; only the deny *frequency* (the cap
value) stays pilot-scoped.

Firings, consults, and rounds are measured from existing instrumentation —
`review-trace --this-repo --deny-only` for denial events with branch
attribution, `consult` rows for dispatches, `/code-review` skill
invocations as a round proxy — and commits-to-merge come from `gh`. No new
logging mechanism ships: appending a pilot log to `log-reviewer-round.sh`
would be a new durable per-branch write path shipped to every consumer for
one machine's experiment, and suspending the recorder's own latch
short-circuit would be a second pilot-conditional behavior layered onto a
proxy that is already conservative in the direction that matters (it can
only undercount post-firing rounds, biasing against adoption). Local
session transcripts age out on a rolling 30 days, so the binding
operational consequence is that the analysis pass must run within 7 days
of the stop point, or the earliest firings become unrecoverable.

## Pre-registration

Gates are fixed before the sentinel is created, mirroring the precedent
`opus-frontload-review-rounds.md` itself cites (gates fixed before any scan
ran):

- **G0 — exposure recoverability** (week 1, abort gate): the first firing
  must be recoverable via `review-trace --deny-only` with branch
  attribution, or the pilot aborts rather than running a month blind.
- **G1 — accrual**: stop at 20 firings or 4 calendar weeks, whichever comes
  first; fewer than 12 firings at week 4 reports as under-accrued rather
  than extending by default.
- **G2 — primary outcome (binary, decisive)**: share of fired branches that
  still reach a 3rd review round, against the historical conditional rate
  of 29/58 = 50%. Adopt-candidate bar: ≤5/20 (25%) at n=20, the largest
  count clearing the exact-binomial α=0.05 one-sided bar, computed both
  proxy-on-proxy and gate-truth-denominator/proxy-numerator — the bar must
  clear on both.
- **G3 — supporting, not decisive (qualitative)**: per firing, whether the
  consult returned, named a specific foundation problem, and the branch
  acted on it, reported with its own bias direction stated (a consult
  asked "is the foundation wrong?" usually finds something and sessions
  usually comply, so this over-credits the treatment).
- **G4 — secondary, descriptive (commits-to-merge)**: mean plus bootstrap
  CI against the population-matched round-2-entrant baseline of 4.59
  commits, pre-committed as unable to drive adoption or rejection alone —
  at n=20 the arithmetic shows it cannot discriminate a plausible effect
  size from noise.

**Decision.** Adopt (make cap=1 the default for every consumer, in a
follow-up PR that supersedes the round-3 decision file) only if G2 clears
with G3 not contradicting it. Otherwise revert — remove the sentinel, with
the code path's own fate decided in the same follow-up. Extending past the
stopping point requires an explicit new engineer decision recorded before
the data is unblinded, never a default continuation.

A concurrent randomized control arm was considered and rejected on
proportionality: it would roughly halve treatment accrual while only
improving G4, which stays underpowered by an order of magnitude regardless
of arm size. G2 needs no control arm — it compares against a historical
base rate computed from the same instrument.

## Scope

The pilot is single-machine, single-repo by construction: a machine opts
in by touching the sentinel, and a second machine can join the same way,
but nothing here widens the corpus automatically. `claude/.claude/CLAUDE.md`
is untouched — its hook-deny carve-out already covers "a skill or hook
prescribing the dispatch" generically, and widening the self-recognition
sentence's round-3 anchor to round 2 would let a session self-dispatch
outside the mechanism this pilot measures, contaminating the exposure
definition it depends on. `README.md` is unaffected, since an opt-in
sentinel leaves its existing default-state description accurate as
written. [round3-plan-architect-consult-gate.md](round3-plan-architect-consult-gate.md)
is a preserved record and stays untouched — a scoped, time-boxed
experiment does not supersede it; supersession is edited in only if the
pilot adopts.

## Sources

- `.claude/plans/plan-architect-first-pass-review.md` — full 23-row
  assumption ledger, mechanism-by-mechanism reasoning, and verification
  steps; the committed pre-registration artifact itself.
- `docs/case-studies/opus-frontload-review-rounds.md` — the round-bucket
  commit-count table, the 50% round-2-to-round-3 historical rate, and the
  "Limits of this result" section this pilot's single-machine scope and
  30-day retention bound are shaped around.
- [round3-plan-architect-consult-gate.md](round3-plan-architect-consult-gate.md) —
  the gate and recorder this pilot's cap resolver extends, left untouched.
- `claude/.claude/hooks/_lib.sh` — `_lib_reviewer_round_state_cap`, the
  shared resolver.
- `claude/.claude/hooks/require-architect-consult.sh`,
  `claude/.claude/hooks/log-reviewer-round.sh` — the gate and recorder that
  consume the resolved cap.
