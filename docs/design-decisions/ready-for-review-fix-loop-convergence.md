# `/ready-for-review`'s step-3 fix loop: carried decisions, a contradiction route, and a dirty-pass cap

*2026-09-17.*

`/ready-for-review` (RFR) step 3 runs a mandatory, fully-unnarrowed `/code-review` over the cumulative PR-vs-base diff after every fix, per the Overview's loop rule: a fix produced by step 2, 3, or 4 returns the gate to step 2, and step 3 then re-reviews the fixed cumulative diff in full because its cache marker misses on the changed bytes. The byte-exact cumulative guarantee stays intact: a fix commit's staged-diff review alone does not suffice to re-push. Step 4 is the one step that does not re-run on its own output, so a skill-procedural-fidelity finding's own fix does not trigger a second step-4 pass. Nothing in this decision narrows the cumulative pass itself; the four changes below only shorten how many times it has to run before it comes back clean.

## Why RFR's prose is the only enforcement during a run

`require-ready-for-review.sh`'s push gate is bypassed outright while this session's active-run marker is live — from step 0's activation through step 8's deactivation. That bypass exists so the gate's own step-7 push isn't self-blocked, but it means no hook enforces "every pushed HEAD got a full cumulative review" for the duration of a run. RFR's own step text is what carries that guarantee, which is why the loop-back wording had to read one way, not two, and why the CI arm below is pinned rather than left to restate the rule in its own words.

## The CI arm

A CI failure is treated as a step-2 failure, not a separate case: step 0's `marker.sh activate` command re-runs to restore the bypass step 8 removed, and the fix then goes through the Overview's ordinary fix-loop rule, which carries it through the full step 2–8 sequence again. Step 4 is not skipped on this path — the CI diagnosis dispatches `/root-cause-analysis`, which is outside step 4's pipeline-skill exclusion list, so the diagnosis's own skill invocation is audited like any other.

## The disposition record

Each cumulative pass's `/code-review` writes its disposition table to `agent-reviews/code-review-dispositions-<suffix>.md` at RFR step 3 — the loop's altitude, not `code-review/SKILL.md`'s, since the record is about a branch's history of passes, not about one review. A pass with no findings writes one row whose finding cell reads `none`, and that row is what the Cap's newest-clean anchor rests on: without it a clean pass would leave no record, and the Cap would count every dirty record since the branch began. Whether a pass counts as clean or dirty is derived from the rows themselves, with no separate summary line: a status field could drift from the rows it summarizes, and deriving it structurally removes that failure mode entirely rather than adding a check for it.

Five lighter homes were rejected before landing on a persisted file:

- **Session context alone** — lost at step 1's handoff-deferral seam, which fires immediately after a fix commit lands, exactly where a long loop is most likely to hand off.
- **`review-ledger.sh`** — keyed by session rather than branch, and its outcome enum is `ADDRESS`/`DEFER` only, with no slot for a contradiction consult's *keep* verdict or a cap row.
- **Fix-commit messages** — no home for a *keep* verdict or a clean pass, and every stow consumer's commit history is public, so loop-internal bookkeeping would ship there permanently.
- **A PR-body block** — no PR exists yet before step 6 on a first run.
- **A `SendMessage` continuation of the prior reviewer instance** — already measured and rejected in `docs/cost-levers-considered.md`.

The record is a self-attestation: nothing correlates its write with a review that actually ran, the same residual [§44](ready-for-review-cumulative-diff-cache.md)'s second named residual already accepts for the `cumulative-review` marker itself. No record grants a review skip, because the `cumulative-review` marker stays the sole authorization to skip a re-review. A record's clean or dirty status can still relax the Cap, since the Cap counts records.

Any review-only agent can `Write` a path under `agent-reviews/`, because the reviewer-tree hook exempts that directory on ignore status alone. A record is therefore attested by whoever wrote the file, and a forged or future-dated record can disable the Cap for the branch, because the Cap orders records by a filename epoch the writer chooses. Narrowing the hook to a reviewer's own findings basename is a separate follow-up, not part of this change.

A record with no rows counts as unparseable, so a truncated write counts as dirty and fails toward more consults.

The branch-scoping key is the slug after its first hyphen, and `findings-path-suffix.sh` truncates the slug to 20 characters. Two branches sharing a prefix that long could therefore count each other's records. That fails in both directions, and it is an accepted residual. A sibling branch's dirty records inflate the count, which means more consults. A sibling's clean record newer than this branch's dirty ones becomes the newest clean record and resets the count, which means fewer consults.

Step 3's Disposition-record paragraph is pinned only from its clean/dirty definition onward. Its record-path, branch-scoping, and Outcome-column sentences are deliberately unpinned, following the neighboring pins' selective pinning, so a rewording of them is caught in review rather than by a test.

The Completion checklist's restatement of "a DEFERred or *keep current text* finding counts as resolved" is deliberately unpinned: `code-review/SKILL.md` § "Step — Record review completion" owns the rule, and the Completion text restates it only so it stands alone at the marker gate.

## The contradiction route

A finding whose fix would undo a fix an earlier round applied routes to a `plan-architect` consult rather than straight to `code-writer`, in every round, staged commit-gate rounds included — session context carries the earlier rounds' fixes there, since the persisted record only exists for RFR's own cumulative passes. The consult's judgment standard is that the current text wins: it returns *keep current text* unless the finding names a defect, under a stated rule, that the current text actually has. The verdict is a binary choice between keeping the current text and applying the round's fix, with a third outcome — *cannot choose* — escalating to the human rather than picking a side. A finding against a site an earlier verdict already settled, or that two earlier rounds' fixes already rewrote, skips the consult entirely and goes straight to the human, which is what bounds A→B→C drift at one site under a different rule each time.

A single consult settling *keep* on a finding outside the enforcement-invariant class is an accepted residual, not a gap this design closes: the contradiction route trusts one architect judgment per finding, the same way the rest of the review pipeline trusts one specialist's pass. Only the enforcement-invariant class — a finding that the diff opens a path around a gate, hook, permission check, or marker guarantee — is excluded from *keep* outright; that class always resolves to ADDRESS or a blocking stop-and-ask, never to a contradiction-consult *keep*.

## The cap

The cap fires at the second consecutive dirty cumulative pass, before its fix is dispatched, borrowing its number from the round-3 gate's `_LIB_REVIEWER_ROUND_STATE_CAP=2`. That number was measured on staged per-PR review rounds, not on cumulative passes, so it is a borrowed convention rather than a re-derived one — "2" is not independently validated for cumulative-pass churn, and no reader should take it as such.

Its timing is one step earlier than the round-3 gate's own trigger point. The round-3 gate can only see a reviewer-spawn event, so it fires at the third spawn; the cap lives in prose instead, so it can act at the point the fix is actually decided — one dirty pass earlier — rather than waiting for a spawn a hook could observe.

The round-3 hook cannot carry this cap itself: `require-architect-consult.sh` and `log-reviewer-round.sh` both return early for the duration of a live RFR active-session marker, so no reviewer spawn inside an RFR run is counted or gated by that hook at all. The cap has to be enforced where the hook is blind — in RFR's own step-3 prose.

Dispatching the cap consult (or a contradiction consult) with `MODE=consult` writes the round-3 gate's latch the same way any other `plan-architect` consult does, with no RFR-aware bypass in the recorder.

A cap or contradiction consult that fires inside an RFR run therefore permanently spends that branch's one round-3 firing. The latch's own stated meaning in [§45](round3-plan-architect-consult-gate.md) is "an architect consult ran on this branch recently," not "the round-3 gate's own prescribed consult ran."

The two mechanisms' trigger windows are mutually exclusive: the active-marker bypass means the round-3 gate can only fire outside an RFR run and this cap only inside one. The latch described above is their only coupling.

## How often the new stops interrupt a run

The shipped skill text has these blocking human stops in the fix loop.

From `ready-for-review/SKILL.md` step 3's Cap:

- A dirty pass while a cap row already sits among this branch's records newer than its newest clean one. The second consecutive dirty pass triggers the consult, and the human stop comes on the next dirty pass after a cap row, whatever that row's verdict.
- A consult verdict of *stop*.
- A `no verdict` cap row: the dispatch failed, returned nothing, or returned text that reads as neither verdict.
- An orchestrator disagreement with the consult's return.

From `code-review/SKILL.md`'s `code-review-contradiction-route` region:

- A finding against a site an earlier verdict already settled, or that two earlier rounds' fixes already rewrote, goes straight to the human with no consult.
- A consult verdict of *cannot choose*.
- A finding with no explicit per-finding verdict from the consult: failed dispatch, empty, hedged, or partial coverage.
- An orchestrator disagreement with the consult's return, by way of the heavier-mechanism rule the route follows.

From `code-review/SKILL.md`'s round-cap consult: a return that reads as none of the three verdicts, or one the orchestrator disagrees with.

From `code-review/SKILL.md`'s `code-review-defer-invariant` region: a finding that the diff opens a path around an enforcement invariant is never DEFER-eligible, and its disposition is ADDRESS or a blocking stop-and-ask to the human.

Every Cap stop fires only after two consecutive dirty passes. Every contradiction-route consult-outcome stop fires only on a finding already routed to a consult. The round-cap consult stop fires on a `require-architect-consult.sh` hook denial, then on the consult's return, and needs neither. The site stop has no non-convergence precondition. It fires on a later finding at a settled site, or on a third finding at a site two earlier fixes rewrote, whether or not the loop is converging. It needs no contradiction with an earlier fix and no revert, so a run whose successive findings each name a distinct defect still stops at that site. It applies in staged commit-gate rounds as well as RFR's cumulative passes, because session context carries the earlier rounds' fixes there.
The skill text does not define "site". No sentence in either SKILL.md gives it a granularity, and the only location field on a finding is the review ledger's optional `--source "<file:line>"`. The orchestrator therefore matches a finding's location against earlier rounds' fixes and verdicts by judgment.

These stops replace silent thrash: without them, a round-4 finding could revert a round-2 fix with no record either round happened, the shape of a round-4 fix reverting a round-2 fix, GH-752. Autonomous shipping's "without pausing" language covers routine progress through passes that hit none of the triggers above, not the stops themselves.

## The keep/cap interaction

A *keep* verdict counts as resolved, so a pass whose only open finding resolves *keep* is clean and resets the cap's consecutive-dirty count. The cap is therefore not expected to fire on the GH-752 revert shape at all — same-site drift is caught first by the contradiction route and the settled-site stop, which bound it independent of the cap's count. The cap's own named residual is new-finding churn across *different* sites: a run alternating dirty passes at distinct sites with clean passes between them never accumulates two *consecutive* dirty passes, so it escapes the cap by the same reset rule that lets *keep* clear it.

## The prose row

`code-writer`'s self-review table routes comments, docstrings, and durable-doc prose to `comment-discipline-reviewer`'s `## Core review angles` section, and its Step 5 scaling guidance runs that read on any added or rewritten prose beyond a whitespace or typo fix, even a single line — in a one-paragraph-per-line skill file, one line can be a whole paragraph. Two baseline bullets back it: text a skill or agent loads at runtime never moves into an edit-time-only file such as `REFERENCES.md`, and a change that would push a file past its line cap, or alter a test-pinned clause, stops and reports rather than trimming elsewhere or editing the pin.

## The `code-review/SKILL.md` line cap

`code-review/SKILL.md` sits at its 500-line cap with zero headroom. Further growth needs the Item ownership table extracted to a runtime-loaded file, following `plan-review`'s precedent for that table.

## Sources

- `claude-skills/skills/ready-for-review/SKILL.md` — the Overview's fix-loop rule, step 3's record and cap, and the CI watch's "Land the fix" arm.
- `claude-skills/skills/code-review/SKILL.md` — "Ripple effect triage"'s carry-forward rules and the `code-review-contradiction-route` and `code-review-round-cap-consult-verdict` regions.
- `claude/.claude/agents/code-writer.md` — the prose self-review row and Step 5 scaling guidance.
- `claude/.claude/hooks/require-architect-consult.sh`, `claude/.claude/hooks/log-reviewer-round.sh` — the round-3 gate and recorder, and their RFR-active-marker early return.
- `docs/design-decisions.md` [§45](round3-plan-architect-consult-gate.md) — the round-3 gate's cap number, its latch semantics, and the population it was measured on.
- `docs/design-decisions.md` [§44](ready-for-review-cumulative-diff-cache.md) — the self-attestation residual this decision's disposition record shares.
- `docs/cost-levers-considered.md` — prior rejection of a `SendMessage` reviewer continuation as a carry-forward mechanism.
