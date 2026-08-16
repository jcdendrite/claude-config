# Slow/network-bound Bash backgrounding: record the rejected lever

## Context

**Goal: record "default slow or network-bound Bash calls to
`run_in_background`" in `docs/cost-levers-considered.md` as an investigated
and rejected cost lever, with the measurement that falsified it.**

A handoff brief proposed adding guidance — to `claude/.claude/CLAUDE.md`,
`subagent-delegation/SKILL.md`, or both — that a Bash call expected to be slow
or network-bound (a branch push, a PR create, a CI-check poll) should default
to `run_in_background: true` rather than blocking. Its stated rationale was
that such calls had been observed occupying tens of minutes of main-thread
wall clock, opening idle gaps that expired the prompt cache and forced
full-context rebuilds at cache-write rates.

Scoping measured the premise and it did not hold: the durations are not
execution time, and the commands named are sub-second. Presented with the
refutation, the engineer chose to write no guidance and record the lever
instead. That register exists for exactly this — its own opening states it
consolidates closed levers "so a seventh plan doesn't re-measure ground already
covered" — and a lever refuted but unrecorded is the case it was built for.

## Approach

Add one section to `docs/cost-levers-considered.md` stating the lever, the
verdict, and the measured reason. Add a short dated follow-up paragraph under
that file's existing `context-cost-root-cause.md` section, recording that a gap
past five minutes is not by itself sufficient to force a rebuild on this
machine. Nothing else changes: no `CLAUDE.md` bullet, no skill edit, no
behavior change.

Three alternatives were weighed. **Narrowing the rule** to Bash calls whose
execution really is long was set aside because the harness's own Bash tool
description already documents `run_in_background`, so an always-loaded
`CLAUDE.md` bullet would restate it at every stow user's per-session token
cost for near-zero yield. **Re-scoping onto the cause the data points at** —
sessions stalling on permission prompts or plain operator absence — is
materially different work needing its own scoping pass, not a rewrite of this
one. **Shipping the brief as written** would put a rule in front of every stow
user whose stated rationale this session's measurements contradict.

### Assumption ledger

**Root problem:** a cost lever has been refuted by measurement, and nothing in
the repo records that, so the next investigation will re-derive it.

**Givens:**

- A Claude Code transcript carries no per-tool-call execution-duration field;
  the only timestamps around a Bash call are the assistant record holding the
  `tool_use` block and the user record holding the matching `tool_result`.
  The transcript format is harness-owned — adding such a field is not
  reachable from this repo.
- Permission evaluation happens before a Bash command executes, on the tool
  call as issued. The permission layer is harness-owned; this repo configures
  rules, not the point at which they are applied.

**Mechanisms:**

- A new section in `docs/cost-levers-considered.md` — `anchors: root`. This is
  the lightest surface that reaches the reader who would otherwise re-measure,
  and it is the surface that file exists to be. The two heavier options were
  enumerated and rejected above: a `CLAUDE.md` bullet spends always-loaded
  tokens in every session for every stow user, and a `subagent-delegation`
  edit would widen that skill's charter from "where work runs" to "how a wait
  is issued" — both disproportionate to a change with no behavioral delta.
- A dated follow-up paragraph under the existing `context-cost-root-cause.md`
  section — `anchors: row4`. That file's header already instructs readers to
  "read to the end of a section, not just its table," so a follow-up is the
  documented way to qualify a standing row without rewriting it.

**Rows:**

1. The multi-minute Bash-call durations behind the brief are not command
   execution time. `[verified: 30-day corpus scan reproduced this session over
   the default 6-root scope, 621 session files — 1,178 main-thread Bash calls
   with a tool_use-to-tool_result gap of 5 minutes or more, whose command
   shapes include `cd` (170), `git status` (45), `echo` (30) and `pwd` (30)
   beside `gh pr view` (200) and `git push` (94); the longest single gaps
   exceed 20 hours. Commands that cannot take minutes to run appear at the
   same magnitude as the ones the brief named.]`
2. The commands the brief named are sub-second in practice.
   `[verified: /usr/bin/time -p this session on this machine —
   `git push --dry-run origin HEAD` 0.72s real, `gh pr list --limit 5` 0.52s
   real]`
3. `run_in_background: true` governs whether a call detaches after being
   dispatched, not whether it is approved, so it does not shorten an
   approval wait. `[unverified]` — load-bearing and not checked, because
   checking it means triggering a real permission prompt.
4. Backgrounding does not close the gap even if row 3 is wrong. A backgrounded
   call returns immediately, but the main thread's next API call is then
   triggered by the command's completion unless there is independent work to
   interleave — so where there is none, it converts a blocking wait into an
   idle wait of the same length. The mechanism is
   `[verified: the Bash tool's own description — a backgrounded command "keeps
   running across turns and re-invokes you when it exits"]`. How often the
   no-interleaving case actually obtains is `[unverified]` and not measured
   here; interleaving is `run_in_background`'s designed use, so this row bounds
   the lever's ceiling rather than showing it always fails. Rows 3 and 4 are the
   pair that closes it: row 3 says backgrounding does not reach the approval
   wait, row 4 says it reaches the idle wait only when real interleavable work
   exists — which rows 1 and 5 give no reason to expect in the observed
   operator-absent population.
5. Operator idle is a real and large component of row 1's population, though
   not established as the majority. `[verified: row 1's own shape breakdown —
   the four named shapes with neither execution cost nor any plausible
   minutes-long approval friction (`cd`, `git status`, `echo`, `pwd`) account
   for 275 of the 1,178 calls, against 294 for `gh pr view` and `git push`
   combined. A 20-hour gap on `cd` is an absent operator whether or not a
   prompt was open, and neither state is reachable from a tool-call flag.]`
6. A gap past five minutes is not sufficient to force a cache rebuild on this
   machine. `[verified: reproduced this session — of the main-thread calls
   preceded by a 10-to-55-minute gap over 30 days, warm cache reads of 100,000
   tokens or more outnumbered cache writes that size by roughly 1.8 to 1.
   Counts are not deduped by `requestId`, which
   `.claude/plans/context-cost-root-cause.md` records as a 2.16x overcount
   factor, so the ratio is the finding and the absolutes are not published.]`
7. The repo's own `cache-rebuild` analysis sorts gaps against the vendor's
   documented tiers rather than discovering a threshold.
   `[verified: claude/.claude/scripts/transcript-analysis.py:7782-7783 —
   `_CACHE_REBUILD_IDLE_5M_SECONDS = 300`,
   `_CACHE_REBUILD_IDLE_1H_SECONDS = 3600`]`
8. Both cache tiers are vendor-documented, so a mixed result across sessions
   is expected rather than anomalous. `[verified: Anthropic prompt-caching
   documentation — "By default, the cache has a 5-minute lifetime." and
   "If you find that 5 minutes is too short, Anthropic also offers a 1-hour
   cache duration at additional cost."]`
9. The engineer, shown the refutation, chose to record the lever as rejected
   rather than ship narrowed guidance, and chose to note row 7's finding
   without opening work on it. `[engineer-verified]`

## Critical files

| Path | Change |
|---|---|
| `docs/cost-levers-considered.md` | Two additions, detailed below. |
| `.claude/plans/background-slow-bash-calls.md` | This plan, committed alongside the doc change per `branch-management`. |

### Addition 1 — the new lever section

Append at end of file. Use the plan-anchored header form the file's other
sections use, not the dateline form:
`## From \`background-slow-bash-calls.md\` — "Default slow/network-bound Bash calls to \`run_in_background\`"`,
followed by a two-row `Lever / Verdict / Measured reason` table. The rows
split the two independent rejection arguments, because one cell carrying both
runs denser than every sibling cell in the file: row one names the lever as
proposed and rejects it on the falsified premise; row two names the narrower
lever that survives granting the premise — backgrounding a command whose
execution genuinely does take minutes — and rejects that on the mechanism.
Row two must name its own lever rather than backreferencing row one, since a
reader scanning the Verdict column meets it alone.

The two **Measured reason** cells must carry four things between them, or a
future reader can reopen the lever on a point already closed:

1. The falsification — durations are not execution time (ledger row 1's shape
   evidence) and the named commands are sub-second (ledger row 2).
2. The mechanism — backgrounding does not reach either the approval wait
   (ledger row 3) or the idle wait (ledger row 4), so the lever fails even
   where a genuinely slow command exists.
3. The residual real driver, by pointer not restatement: the adjacent
   `context-cost-root-cause.md` section's concurrent-session-switching finding
   already holds it.
4. The precision caveat, mirroring the file's existing phrasing for one-off
   scans — the producing scan is not a rerunnable script.

### Addition 2 — the dated follow-up

Add a `**2026-08-16 follow-up:**` paragraph at the end of the existing
`## From \`context-cost-root-cause.md\`` section, carrying row 6: a gap past
five minutes is not by itself sufficient to force a rebuild, stated as a ratio,
with the same not-a-rerunnable-script caveat. This qualifies that section's
standing rows; it does not contradict them — the register's own text there
attributes rebuilds to "the vendor's 5-minute/1-hour cache TTL" lapsing, which
row 6 refines rather than refutes.

**Do not edit `.claude/plans/context-cost-root-cause.md`.** It is a merged plan
and a read-only historical record under CLAUDE.md's Axis 3. The phrase "a
five-minute pause is enough to force a rebuild" lives there, not in the
register — qualify the register's own wording and leave the merged plan alone.

**Reuse:** the section grammar, the "not a rerunnable script, so treat the
precision accordingly" caveat phrasing, and the dated-follow-up convention are
all already established in `docs/cost-levers-considered.md` — mirror them
rather than inventing a shape. The register indexes plans; it does not restate
their content.

**Not edited:** `claude/.claude/CLAUDE.md`, `claude/.claude/skills/**`, and
`claude/.claude/scripts/transcript-analysis.py` all stay untouched. The two
scratchpad scan scripts stay in the scratchpad and are never added to the repo.

## Verification

1. `../../../.venv/bin/pytest claude/.claude/` passes — a docs-only change
   should not move it, and `test_doc_counts.py` is the one place a docs edit
   could plausibly register.
2. Both additions carry the not-a-rerunnable-script caveat — the new section
   on whichever of its rows publishes the scan figures from ledger rows 1 and
   2, the follow-up for ledger row 6. Neither figure is reproducible from
   anything in the repo, so neither may be published bare.
3. The new section's Measured reason cells contain all four elements listed
   under Addition 1. Re-read them against that list; the mechanism element
   (2) and the pointer (3) are the two a naive fill would drop.
4. Every published figure traces to a ledger row, and no published figure is a
   single-session extremum. This governs both committed files in full — every
   line of the plan including its Context prose, not only the ledger rows and
   the two doc Additions; the plan ships publicly alongside the register.
   Aggregate per-shape counts, ratios, and rounded magnitude buckets ("gaps
   exceeding 20 hours") are permitted; an exact max-gap value, a duration tied
   to one identified call, and a dated sample are not. Prose restating the
   rejected brief's own figures is the miss-prone case: it sits outside the
   ledger and escapes a ledger-scoped re-read.
5. Read every number adjacent to a time unit in both committed files and
   confirm each is one of: a figure inside a `[verified: ...]` ledger tag, a
   rounded magnitude bucket, a configured threshold, or a publicly documented
   vendor constant. Anything else is a single-call fingerprint, integer or
   fractional alike. Numeric figures are invisible to all three redaction
   tiers, so a reader is the enforcement. A grep such as
   `grep -nE '[0-9]+(\.[0-9]+)?[ -]*(min|sec|hour)'` locates candidates, but
   the enumeration is the check and the pattern is only an aid — an earlier
   pattern here missed hyphenated forms and undercounted its own hits.
6. No project or client name, session identifier, or per-account config path
   appears in either committed file. This is a manual reviewer-discipline check:
   `deny-private-project-refs.sh` does scan the staged diff body, but its
   always-on detectors cover tracker IDs and six structural shapes, none of
   which match a name — name-absence is enforced only by the opt-in
   `~/.claude/private-projects.md` blocklist, which fails open when
   unpopulated. Do not treat a clean commit as confirmation of this item.
7. `git diff --stat` shows exactly two files: the register and this plan.
8. `/code-review` before commit, per the repo's mandatory review pipeline.

## Out of scope

- **The guidance the brief asked for.** No `run_in_background` rule is written
  anywhere. This is the deliberate outcome, not an omission.
- **Correcting `transcript-analysis.py`'s cache-rebuild threshold framing.**
  Row 7 is recorded and no work is opened on it, at the engineer's direction.
  A fix would touch a measurement script other analyses depend on.
- **The permission-prompt and operator-idle stalls** that rows 3, 4 and 5 leave
  as the residual explanation. Real, and larger than the lever being rejected,
  but a different investigation.
- **Everything the brief itself excluded** — review-gate architecture, the
  handoff-nudge threshold, a plan-before-every-Bash-call rule, and auditing
  compliance with existing delegation guidance — remains excluded.
