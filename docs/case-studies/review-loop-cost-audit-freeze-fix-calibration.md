# Does this repo's corpus contain confirmed review-loop thrash?

*Part of the [claude-config case studies](../case-studies.md).*

**The question.** `review-loop-cost-audit` classifies a branch's post-freeze review rounds as **Stuck loop**, **Plan-grinding**, **Gate-denial churn**, or one of two legitimate-work verdicts, using a narrative read (Step 3(g), via `transcript-narrative`) as the only step the rubric allows to decide between them. Building a candidate list for that narrative read is cheap: rank branches by round count, plan-review share, ready-for-review share, or the share of review-round spend falling outside dated round windows. None of those proxies is allowed to decide a verdict on its own. This study asks the question those proxies raise but can't answer alone: does this repo's own corpus actually contain a branch that reads as thrash once the narrative step runs, and do the cheap proxies used to find candidates predict which branches will?

**Short answer.** Seven branches, selected across two passes by five different proxy signals, audited end to end. Zero produced a confirmed **Stuck loop**, **Plan-grinding**, or **Gate-denial churn** verdict. Four came back as one of the two legitimate-work verdicts once the narrative read ran. The remaining three never reached a verdict at all — each was mechanically **Inconclusive** for a reason unrelated to the proxy signal that selected it. No proxy signal used across either pass predicted which outcome a branch would get.

## Method

**Pass 1** (this skill's own post-merge calibration, prior to this document) ranked branches by `review-round-cost --this-repo`'s branch-dollar total, the skill's default sweep ranking. It selected three branches: the two carrying the corpus's highest round counts, and the one carrying the highest outside-dated-round-window spend share among the top-ranked set.

**Pass 2** (this document's own audit) deliberately selected against a different signal among the corpus's round-count outliers — the shape a **Plan-grinding** or **Gate-denial churn** verdict would produce, rather than a bare dollar-rank cut:

- an elevated plan-review share of rounds
- an elevated ready-for-review share of rounds
- an elevated outside-window spend share

Four branches met this bar and were deep-audited in full via `review-loop-cost-audit`'s Step 3 and Step 4's per-round-type verdict rubric. Step 3 covers:

- dated round table
- tiered code-freeze resolution
- commit classification
- freeze confirmation
- round-table partition
- per-session skew
- a `transcript-narrative` read of the post-freeze rounds

Each branch was audited independently, with no cross-branch assumptions carried over.

Branch names, exact round counts, and dollar figures are withheld throughout, per this skill's own Step 4 publication rule ("carry no dollar total, no per-branch cost share, and no figure from either corpus into the verdict text") and this repo's structural-fingerprint redaction convention. Branches are labeled A–G below.

## Results

| Branch | Selected for | Verdict | Basis |
|---|---|---|---|
| A (Pass 1) | Highest round count in the corpus | Inconclusive — still active | Freeze partition vacuous; the branch's most recent commits postdate the corpus's last recorded round |
| B (Pass 1) | Second-highest round count in the corpus | Inconclusive — vacuous partition | Freeze instant fell at or after the newest round of every round type; zero rounds fall after it |
| C (Pass 1) | Highest outside-window spend share among top-ranked branches | Legitimate follow-on work | One post-freeze `plan-review` round reviewed genuinely new work. Narrative read showed monotonic convergence, not re-surfaced findings. |
| D (Pass 2) | Elevated plan-review share among round-count outliers (plan-grinding candidate) | Inconclusive — still active | Tip commit postdates the branch's last recorded round by minutes. Freeze partition mechanically vacuous. |
| E (Pass 2) | Elevated outside-window spend share, second only to a Pass-1 branch (gate-denial-churn candidate) | Legitimate follow-on work / large-diff work | Only one post-freeze round, which completed cleanly. Narrative read showed new findings raised each pre-freeze round, not re-surfacing. |
| F (Pass 2) | Highest ready-for-review share among round-count outliers (gate-denial-churn candidate) | Inconclusive — still active | Freeze partition mechanically tripped by later merge-only commits carrying an empty diff, unrelated to the round-mix signal that selected this branch |
| G (Pass 2) | Elevated ready-for-review share and outside-window spend (gate-denial-churn candidate) | Legitimate follow-on work | Only post-freeze commit was artifact-only. Post-freeze `plan-review` rounds converged, raising new findings each round before going clean. |

Two of the three Inconclusive branches (A and D) share a root cause — a branch still moving after the corpus's last recorded round — despite being selected by two different proxy signals (round count and plan-review share). The third (F) was selected for its ready-for-review share specifically, the signal a Gate-denial-churn verdict would produce, but its Inconclusive verdict traces to unrelated empty-diff merge commits.

## The pattern

No proxy signal tested across either pass — round count, plan-review share, ready-for-review share, or outside-window spend share — correlated with a branch's eventual outcome. Every branch that reached a measurable verdict came back as legitimate work; every branch that didn't reach a verdict failed for a reason orthogonal to the signal that selected it. This confirms a rule the skill already states by design (Step 4: "Never decide from the outside-review-window share... the round-mix numbers alone never [determine a thrash verdict]") rather than surfacing a new finding about the rubric. The round-mix proxies' only intended role is building a candidate list cheaply. This pass found they carry no measured discriminating power on their own in this repo's corpus. The narrative read is not a formality layered on top of a mostly-reliable proxy; on this evidence, it is the only step doing any discriminating work at all.

Pass 1 also surfaced two rubric gaps in `review-loop-cost-audit` — a missing "Legitimate follow-on work" verdict for a post-freeze round reviewing genuinely new work, and a missing check that a `ready-for-review` denial was a genuine invocation attempt rather than an incidental mention of a gated command in a grep pattern or quoted example. Both are addressed in `claude-skills/skills/review-loop-cost-audit/SKILL.md` and were exercised, not re-tested, by this pass:

- three of the four Pass-2 branches resolved to the "Legitimate follow-on work" verdict
- Branch G's `ready-for-review` denials were confirmed as genuine invocation attempts

## Named residual

Seven branches audited, selected by five distinct proxy signals across two passes, and none has yet produced a confirmed **Stuck loop**, **Plan-grinding**, or **Gate-denial churn** verdict. This does not establish that this repo's corpus contains no thrash, or that these three verdicts can't fire — it establishes that the proxies available to find candidates cheaply have not yet located one, and that a branch's proxy signature is not informative about which outcome it will get. The corpus may contain a genuine positive that these particular signals don't surface, or it may not.

**Revisit trigger.** Re-run this audit against the next branch the corpus produces that clears two conditions simultaneously: a real freeze (not still-active, not vacuous) and a substantial post-freeze round count of a single type — no branch in this pass cleared both at once. A confirmed true positive for any of the three thrash verdicts closes the corresponding share of this residual.

## Sources

- `claude/.claude/scripts/transcript-analysis.py` — `review-round-cost`, `buckets`, `subagent-mix`, and `transcript-narrative`'s underlying data, used to rank candidates and run all seven deep audits.
- `claude-skills/skills/review-loop-cost-audit/SKILL.md` — the audited skill, including the Pass-1 follow-up edit (the `Legitimate follow-on work` verdict and the genuine-invocation-attempt clause) exercised by this pass.
- PR #987 — the freeze-partition and verdict-rubric fix both passes calibrate against.
