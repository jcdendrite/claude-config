# Numeric fingerprints: close the tier-3 gap

## Context

`claude-config`'s public-repo redaction rule (`CLAUDE.md` "Redact
private-project-identifying content") has no surface, mechanical or
reviewer-facing, that names numeric fingerprints as a class — a precise
duration, percentage, dollar figure, or count is not tracker-ID-shaped, is
none of the six structural detectors, and is not a name, so it clears every
mechanical gate while still being able to identify a single call, session,
account, or private engagement. This surfaced concretely when a
decimal-precision duration pair drawn from a private-engagement corpus
survived two authoring passes over a short document and was caught only by
a reviewer on a third pass; the corrective applied there was scoped to that
one change's two files and does not generalize. The intended outcome is a
durable, repo-wide rule naming the class and a review-time check that
enforces it on every commit, chosen only if the false-positive cost of a
mechanical detector doesn't disqualify it first.

## Approach

Add a **numeric-fingerprints** subsection to `CLAUDE.md`'s existing
"Redact private-project-identifying content" tier-3 bucket (sibling to
"Also redact structural fingerprints"), and enforce it with a new
`/code-review` base-checklist item — not a hook. The measured false-positive
rate against this repo's own committed prose disqualifies a blanket
mechanical detector for two of the three candidate numeric shapes, and the
class's core distinction (an aggregate statistic vs. a single-measurement
extremum) needs semantic judgment a regex cannot make even where the hit
rate is low.

**Assumption ledger**

- **Root problem:** numeric fingerprints are tier-3 ("reviewer discipline
  only") by construction — no doc names the class, and no checklist item
  operationalizes it — so reviewer attention alone is the current backstop,
  and it has already failed once.
- **Given:** the three-tier model and the six structural detectors are a
  deliberate, considered design — not something this plan revisits.
  [engineer-verified: brief §4 states this explicitly and scopes it out]
- **Given:** the opt-in blocklist's fail-open behavior when
  `~/.claude/private-projects.md` is absent is out of scope for this work.
  [engineer-verified: brief §6.5 requires separate authorization for any
  fail-closed change]

| # | Assumption | Tag |
|---|---|---|
| 1 | A candidate regex for "number + time unit" (Pattern A), run over `docs/` and `claude/.claude/skills/` (68 files, ~10.9K lines, excluding `docs/reports/`), hits 61 times; 5/61 (~8.2%) are risky (a precise, unqualified figure with no rounding/threshold/vendor-constant/`[verified:]` qualifier) | [verified: subagent grep run this session over the full population, all 61 hits hand-classified] |
| 2 | A candidate regex for "$ + number" (Pattern B) hits 22 times; 4/22 (~18.2%) are risky, and a further 5/22 (~23%) are pure false positives unrelated to money (`$1`/`$2` shell positional params, a SQL bind parameter) that a naive dollar regex cannot distinguish without code-fence/language awareness | [verified: same run, full population, all 22 hits hand-classified] |
| 3 | A candidate regex for "number + %" (Pattern C) hits 150 times; 65/150 (~43.3%) are risky | [verified: same run, full population, all 150 hits hand-classified] |
| 4 | Most of Pattern C's "risky" hits are not false positives of the detector — they are real instances of this repo's own engineering-history docs (`docs/design-decisions.md`, `docs/cost-levers-considered.md`, `docs/case-studies/*.md`) citing precise measured percentages with an inline narrative source (a command, a corpus size, a bootstrap count) rather than a bracketed `[verified: ...]` tag. A minority single out one account or one earlier measurement by name (`docs/handoff-nudge.md:12`, `docs/design-decisions.md:316`) — the exact single-instance-extremum shape this rule targets | [verified: subagent's per-hit file:line citations, read directly this session] |
| 5 | A mechanical detector cannot distinguish an aggregate statistic (safe — "44% of turns are subagent turns", computed over a large population) from a single-instance extremum (risky — a figure attributable to one identifiable private engagement) by pattern-matching the surrounding text alone; that distinction is what actually determines fingerprint risk, not precision, rounding, or aggregate-vs-instance *phrasing* by itself — a small-population "average" reframed to sound aggregate carries the same risk as a bare instance figure | [unverified — no counter-example regex was attempted this session; asserted from reading the classified hit set, not from exhausting the design space of a smarter pattern] |
| 5a | Two of the measured "risky" Pattern C hits this session's ledger row 4 named (`docs/handoff-nudge.md:12,107`, `docs/design-decisions.md:316`) are, on a closer read of their full sentences, operational tool-usage metrics (Claude Code's own context-window nudge frequency per local account config; this repo's own CI test-suite timing) with no private-client engagement content, not instances of the class this plan targets — precision and per-instance framing alone over-classified them | [engineer-verified: confirmed this session — leave both files as-is, no edit] |
| 5b | "Count" (the fourth shape named in Context and Critical files, alongside duration/percentage/dollar) was not run through the empirical measurement this session — only Patterns A (time), B (dollar), C (percentage) were | [unverified — included in the rule's scope on the same judgment basis as the three measured shapes, not on its own measured hit rate] |
| 6 | `ciso-reviewer`'s trigger conditions (auth, secrets, tokens, access-control policies, privileged functions, input validation, logging of sensitive data, third-party data sharing) do not include plain prose/doc edits, so it would not fire on most commits that could introduce this class (a `docs/design-decisions.md` edit, a case-study addition) | [verified: claude/.claude/agents/ciso-reviewer.md TRIGGER list, read this session] |
| 7 | `/code-review`'s base checklist runs unconditionally on every dispatch (unlike domain-scoped items), and already carries a structurally identical precedent — item 9c, "Ungrounded numeric literal in network/timeout/retry context" — flagging a bare numeric literal without a grounding citation | [verified: claude/.claude/skills/code-review/SKILL.md:88, read this session] |
| 8 | `docs/private-project-redaction.md` is scoped to `deny-private-project-refs.sh`'s mechanical scans only (it defers to the README for the three-tier overview and never itself describes reviewer-discipline tier 3), so it needs no edit; README's "Private-project redaction" section (lines 74, 406–414) does summarize tier 3 in prose and would go stale relative to `CLAUDE.md` without a matching update | [verified: both files read this session] |

**Alternatives considered**

- **Hook (`deny-private-project-refs.sh` seventh structural detector).** Rejected on the measured numbers: Pattern C's 43.3% risky rate would deny a large fraction of this repo's own well-sourced, already-grounded commits (`CLAUDE.md`'s own "Ground every choice" rule requires exactly the narrative-citation style the pattern can't recognize as legitimate), and Pattern B's dollar regex can't separate money from shell/SQL positional-parameter syntax without language-aware parsing — a heavier lift than a six-line ERE constant. Even Pattern A's comparatively low 8.2% doesn't generalize past the one benchmark table it clusters in. All three share the deeper problem in ledger row 5: the real signal is aggregate-vs-instance, which is a semantic distinction, not a lexical one — the class this rule targets is a poor fit for the always-on, no-judgment mechanism a hook provides regardless of pattern tuning.
- **Guidance text in `docs/private-project-redaction.md` alone (no checklist item).** Rejected: `CLAUDE.md` already states the general redaction rule, and that alone was insufficient — the precedent case survived two authoring passes before a reviewer caught it on a third, meaning passive guidance without an active review-time check already failed once at the exact task this surface would perform.
- **`ciso-reviewer` agent instruction.** Rejected per ledger row 6 — its trigger conditions are auth/secrets/access-control-shaped, not doc-prose-shaped, so it would not run on most of the commits that could introduce this class. Piggybacking on the wrong trigger buys no real coverage.
- **`/code-review` base-checklist item (chosen).** Runs unconditionally on every review, mirrors an existing precedent's structure (item 9c) so it costs no new pattern to learn, and puts a human/model judgment call exactly where the aggregate-vs-instance distinction needs one — at review time, not at commit-time pattern-matching.

## Critical files

- `CLAUDE.md` — add `### Also redact numeric fingerprints` immediately after
  `### Also redact structural fingerprints` (line 144), before
  `### Secrets, tokens, credentials` (line 151). Drafted text:

  > A precise duration, percentage, dollar figure, or count can identify one
  > call, session, account, or private engagement even with no name attached.
  > A statistic computed over a population large or diverse enough that no
  > single source dominates or is inferable from it, a publicly documented
  > vendor/protocol constant, a configured threshold, and a figure already
  > inside a `[verified: ...]` assumption-ledger tag are not in this class; a
  > figure — however phrased — attributable to one identifiable instance is.
  > No mechanical detector covers this class — separating a small-population
  > figure from a genuine aggregate needs semantic judgment a hook can't
  > apply, so `/code-review`'s base checklist is the enforcement point.

  The population-size framing (not "is it phrased as an aggregate")
  replaces an earlier draft that exempted anything phrased as a corpus-wide
  statistic — a small-N average reframed to sound aggregate would have
  passed that draft while carrying the same risk (ledger row 5).
  No PR-number or commit-log narration (this doc's own preserved-content and
  comment-discipline rules apply to new prose here too).
- `claude/.claude/skills/code-review/SKILL.md` — add item **13a** under
  `### Security` (after item 13, line 112, before `### Scope discipline` at
  line 114). Drafted text:

  > 13a. **Numeric fingerprint tied to one identifiable instance** — Does
  > the diff add a precise duration, percentage, dollar figure, or count
  > presented as an empirical result? Flag it unless it's a statistic over a
  > population large or diverse enough that no single source dominates it, a
  > publicly documented vendor/protocol constant, a configured/documented
  > threshold, or already inside a `[verified: ...]` assumption-ledger tag —
  > a figure attributable to one call, session, account, or private
  > engagement can identify it even with no name attached, however the
  > figure is phrased. See CLAUDE.md "Also redact numeric fingerprints".

  **Reuse:** item 9c (`SKILL.md:88`) is the direct structural template —
  same "flag unless grounded" shape, same one-line-per-exemption density.
- `README.md` — update the tier-3 one-liner (line 74) and bullet 3 (line
  412) of "Private-project redaction" to say "structural and numeric
  fingerprints" instead of "structural fingerprints" alone, so the summary
  doesn't drift from the `CLAUDE.md` section it summarizes.

## Verification

1. Re-read the new `CLAUDE.md` subsection against the repo's own
   Code-Comments rules (no PR-defined terminology, no "used to be X"
   framing, one line per non-obvious constraint, survives-the-PR-being-
   merged self-test) — this file is exactly the kind of durable doc those
   rules govern.
2. Re-read the new checklist item against item 9c's precedent: does it
   name what to flag, what's exempt, and where the rule lives, in the same
   density?
3. `git diff --stat` shows exactly three files: `CLAUDE.md`,
   `claude/.claude/skills/code-review/SKILL.md`, `README.md`.
4. `../../../.venv/bin/pytest claude/.claude/` passes — no hook or `_lib.sh`
   regex is touched by this plan, so this is a regression check, not an
   expected-move check.
5. Run `/skill-review` on the `code-review` SKILL.md diff (hook-enforced at
   commit time regardless).
6. `/code-review`, then commit, per the repo's mandatory pipeline.

## Out of scope

- **The six existing structural detectors and the hook itself.** Not
  touched — the measured numbers rule out extending them for this class.
- **The opt-in blocklist's fail-open behavior.** Confirmed out of scope by
  the engineer via the brief (§6.5); any fail-closed change needs separate
  authorization.
- **Editing `docs/handoff-nudge.md` and `docs/design-decisions.md`.** This
  session traced the two sharpest candidate hits back to their full
  sentences and confirmed with the engineer they are operational tool-usage
  metrics (Claude Code's own nudge frequency per account config; this repo's
  CI test-suite timing), not private-engagement content (ledger row 5a) —
  left as-is by explicit engineer decision, not a silent pass.
- **A non-blocking advisory nudge at authoring or commit time**, surfacing
  candidate high-precision numeric hits (the same Pattern A/B/C shapes
  measured this session) without blocking, mirroring this repo's existing
  nudge-hook pattern (e.g. the handoff-context-cap nudge) rather than the
  deny-hook already rejected above. The checklist item alone reproduces the
  single-layer, judgment-only pattern that already missed the precedent case
  twice before a reviewer caught it on a third pass — a second, independent
  detection point at authoring time would close that specific gap. Deferred
  to a follow-up plan because it needs its own hook design and a
  `claude-hook-review` pass, not because the gap isn't real.
- **`docs/private-project-redaction.md`.** Scoped to the hook's mechanical
  scans by its own structure (see ledger row 8); the new tier-3 rule doesn't
  belong there.
