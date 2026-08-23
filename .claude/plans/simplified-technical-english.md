# `tighten-prose`: a prose-tightening skill for PR descriptions

## Context

PR descriptions carry outsized review weight: a human reviewer often can't
carefully read every line of an AI-authored diff, so the PR body is the
primary surface for judging a change's design — and across a high volume of
agent-authored PRs, verbose or ambiguous prose costs real reviewer time. Why
now: the volume of agent-written PRs makes this a recurring cost, not a
one-off. Intended outcome: a new, separately-invocable skill that tightens
prose against a controlled-language style (short sentences, active voice,
one idea per sentence, consistent terminology) without dropping or softening
any fact, decision, or technical detail — wired into `/pr-description` as an
on-by-default pass with a discoverable account-level opt-out, and
independently invocable any time text needs the same treatment.

## Approach

Add `claude/.claude/skills/tighten-prose/SKILL.md`, a `skillOverrides:
name-only` skill (no `TRIGGER when:` block — invoke-only, following the
`root-cause-analysis` / `brief` precedent) that rewrites prose for clarity
and concision while preserving every fact and its original strength.
`/pr-description` dispatches it by name as a new pass, gated by an
account-level opt-out sentinel.

**Naming.** The user's original ask named ASD-STE100 specifically, and an
earlier revision of this plan kept that name on the skill while grounding
its actual rules elsewhere (see next paragraph). A supplementary Opus-level
review (run alongside `/plan-review`'s Sonnet-pinned specialists, per the
engineer's request given this plan's significance) flagged that as a real
risk, not a cosmetic one: `skillOverrides: name-only` means the description
never loads into the always-on listing, so the name is the *only*
always-visible surface — in the `/` menu, in `settings.json`, in
`docs/skills.md`, in every stow consumer's install. "Simplified Technical
English" is not a generic style descriptor; it's ASD's own designation for a
specific, owned, certification-adjacent specification. Naming the skill
after a standard it deliberately doesn't implement — for sound licensing
reasons — puts the disclaimer in `REFERENCES.md`, which is never loaded at
runtime, while the always-visible name asserts the thing the disclaimer
denies. This repo's own precedent points the other way: `plugin-semver` is
named after semver *and implements semver*. The skill is renamed
**`tighten-prose`** — verb-first, matching `read-docx-comments`, an accurate
functional name, and it now matches the `## Prose tightening pass` section
name already chosen in `/pr-description`. ASD-STE100 stays in
`REFERENCES.md` as an honest lineage note (the concept that inspired
"controlled technical language" as a category), not as branding the name
has to carry.

A follow-up comment on this plan raised a second naming concern: this repo
already has a `comment-discipline-reviewer` agent whose job also involves
prose discipline. A second, dispatched Opus review of that comment found the
two are already well separated by this repo's own naming grammar — every
flag-only reviewer is a noun-phrase ending `-reviewer` (`comment-discipline-
reviewer`, `staff-*`, `ciso-reviewer`); no skill is verb-first and no agent
is. `tighten-prose` vs. `comment-discipline-reviewer` sits on opposite sides
of that pattern already, so no further rename is warranted. The real gap
that review surfaced is different: because `skillOverrides: name-only` hides
this skill's description, and `REFERENCES.md` is never loaded at runtime,
the SKILL.md body is the *only* surface that reaches a running model — so
the object/action boundary between "rewrite drafted prose" and "flag durable
in-repo prose" has to be stated in the body itself, not left to a doc a
model never reads. See Critical files for the added scope rule.

**Grounding.** ASD-STE100's actual writing rules and ~900-word controlled
dictionary are owned by ASD, and ASD's own FAQ does not state
reproduction/licensing terms for that content — a public repo can't safely
quote or paraphrase the standard's rule text at the level `CLAUDE.md`'s
grounding requirement calls for. `/verify-sources` (dispatched mid-session)
surfaced an openly-licensed alternative that reaches the same functional
goal: the **Google Developer Documentation Style Guide**, whose pages are
explicitly "licensed under the Creative Commons Attribution 4.0 License"
(confirmed at `developers.google.com/style/sentence-structure`) — directly
quotable with attribution, and already this repo's trusted source for
PR-authoring standards (`pr-description`'s own `REFERENCES.md` cites Google
Engineering Practices). Digital.gov's Plain Language guidelines corroborate
the same rule directions as a second source (active voice, topic sentences,
audience-appropriate language), though its page carried no explicit
reuse-terms statement, so it's used as directional corroboration, not the
licensing anchor.

**Fact-preservation defense, revised after the Opus review.** The first
draft's defense against dropped facts (a same-context self-check comparing
input to output) does nothing against a rewrite that keeps every fact
present but changes its *force* — a hedge collapsed into certainty, a
conditional's binding broken by a sentence split, an obligation strength
adjusted. The revised design puts the load on carve-outs instead of
self-detection: exclude the highest-risk sentence classes from rewriting
entirely, rather than trusting a rewrite-then-check loop to get them right.
This costs the feature almost nothing — deploy/coordination notes,
security-invariant claims, and reviewer-action items are a small fraction of
a typical PR body, not the verbose narrative prose the feature exists to
fix. See Critical files for the specific rule changes.

**Root problem:** agent-authored PR prose is often verbose or ambiguous
enough to cost reviewer time at volume, and there's no reusable, low-budget-
cost mechanism in this repo to tighten it without duplicating rule text into
every consumer.

**Givens:**
- G1. `skillOverrides: name-only` requires Claude Code v2.1.129+; older
  clients silently fall back to `on` (description loaded). Owned by the
  Claude Code product, outside this design's reach — the 12 existing
  name-only skills already accept this degradation path, so this plan
  follows the same precedent rather than re-litigating it.
- G2. ASD-STE100's reproduction/licensing terms for its rule text and
  dictionary are not published by ASD (confirmed against `asd-ste100.org`'s
  FAQ and About pages). Owned by ASD; outside this design's reach to
  resolve, so the design routes around it via openly-licensed sources
  instead of waiting on it.
- G3. GitHub's `actor` object (REST/GraphQL event and edit metadata) exposes
  only account identity (`login`, `id`) — no field distinguishes a human's
  own token used by automation from the same human acting via the browser.
  Owned by GitHub's data model; the sync-mode design question this session
  raised (bot vs. human attribution) has no answer within this design's
  reach, which is why sync-mode integration is deferred rather than solved
  with an attribution mechanism that doesn't exist.

**Per-mechanism ledger:**

| # | Mechanism | Justification | anchors |
|---|---|---|---|
| 1 | `skillOverrides: name-only`, no TRIGGER block | Lighter than a full auto-trigger skill: a "tighten this prose" trigger surface is exactly the broad/unscopeable class this repo already moved off auto-trigger for (`error-handling`, `test-conventions`, `sql-query-conventions` precedent, `docs/design-decisions.md` §11). Lighter alternatives considered and rejected: (a) full `description`-driven auto-trigger — rejected, unscoped surface, matches the documented precedent for why those three skills moved to name-only; (b) `disable-model-invocation: true` (user-only slash command) — rejected, blocks `pr-description` from dispatching it programmatically, which the default-on integration requires. | root |
| 2 | `pr-description` dispatches the new skill via the `Skill` tool rather than inlining the rules | Lighter than duplicating rule text into `pr-description`'s own body. Lighter alternatives considered and rejected: (a) inline the rule list into `pr-description/SKILL.md` — rejected, duplicates content the standalone skill also needs (violates single-source-of-truth) and pushes `pr-description` toward its 200-line cap; (b) extract a shared `_shared/` partial — rejected, this repo's architecture notes explicitly reject shared partials across skills. | root |
| 3 | Account-level sentinel opt-out (`<config-dir>/pr-description-tighten-prose-optout`), registered in `install.sh`'s `SENTINEL_INVENTORY` with new `expected-content` and `polarity` fields, scoped to `_report_account_sentinel` only | Reuses the established sentinel-gate pattern (`pr-cost-disclosure`) for the gate mechanism itself, but that pattern's discoverability machinery (`install.sh`'s `report_sentinel_inventory`, `docs/hooks.md`) assumes opt-in polarity (`default_state` always `disabled`) — every existing row has absence-means-off. A `staff-platform-engineer` review of this piece (dispatched after the general polarity idea, once the plan called for touching shared array-parsing code) found the naive version had two further problems specific to this codebase: `_report_account_sentinel` is hardcoded to `pr-cost-disclosure`'s content-mode check and isn't reachable from a plain polarity field alone, and both `IFS='|' read -r` call sites over the array need the field count bumped in lockstep or the trailing fields corrupt `docs_anchor` on every row. The final design (Critical files, `install.sh` bullet) generalizes only `_report_account_sentinel` — the one function the new row's scope actually dispatches to — rather than every reporter. Lighter alternatives considered and rejected: (a) a `settings.json` boolean — rejected per the engineer's explicit choice this session (repo-shared scope is wrong for a personal writing-style preference, and it would introduce a second toggle family alongside the sentinel pattern); (b) register the sentinel in `docs/hooks.md` prose only, without touching `install.sh` — rejected, leaves the installer's automated report silent, or (worse, if registered without the function fix) actively printing wrong state for the new row; (c) flip the sentinel to opt-in (absent = off) — rejected, directly contradicts the engineer's explicit "on by default" requirement from the original ask. | row 3 |
| 4 | Supplementary Opus-model review pass, in addition to `/plan-review`'s Sonnet-pinned specialists | `CLAUDE.md`'s Model Routing convention pins `/plan-review`'s specialist agents (`staff-*`, `ciso-reviewer`) to Sonnet; the engineer explicitly asked for Opus in the review panel given this plan's significance. It surfaced the naming and fact-preservation findings above, which the Sonnet-pinned `/plan-review` pass did not, and — via two further dispatches after the plan was committed — the scope-rule and plan-file-tightening findings below. Lighter alternative considered and rejected: rely on `/plan-review`'s existing Sonnet-pinned specialists alone — rejected because the engineer's explicit ask overrides the routine default for this one plan, not because the routine default is wrong in general. | root |

**Assumption rows:**

- [engineer-verified] STE-compliance framing is "inspired-by, clearly
  labeled" — never claims ASD-STE100 certification or compliance. Source:
  engineer's explicit answer this session.
- [engineer-verified] Default-on integration applies in `pr-description`
  for this iteration; sync-mode read/verify checks still apply as before,
  but the tightening pass itself is not re-applied against changes made
  outside this pass — see Out of scope for the deferred whole-body
  re-tightening design.
- [engineer-verified] Opt-out is a single account-level sentinel file, not a
  `settings.json` field. Source: engineer's explicit answer this session.
- [engineer-verified] No rename beyond `tighten-prose` for the naming-
  collision comment; the fix is a scope rule in the SKILL.md body, not a
  further name change. Source: engineer's explicit answer this session.
- [engineer-verified] A prose-tightening pass over plan files themselves is
  deferred, not designed in this plan; tracked as a follow-up issue instead.
  Source: engineer's explicit answer this session.
- [verified: developers.google.com/style/sentence-structure] Google
  Developer Documentation Style Guide content is licensed CC BY 4.0 — page
  footer states "Except as otherwise noted, the content of this page is
  licensed under the Creative Commons Attribution 4.0 License."
- [verified: asd-ste100.org/STE_faq.html, asd-ste100.org/about_STE.html]
  ASD-STE100 is free to obtain (PDF request) but "fully owned by ASD,
  Brussels, Belgium," with no published reproduction/licensing terms for its
  53 writing rules or ~900-word dictionary.
- [verified: digital.gov/guides/plain-language/principles] Digital.gov's
  Plain Language guidelines corroborate the same rule directions (active
  voice, topic sentences, audience-appropriate language) but the fetched
  page carried no explicit reuse-terms statement — used as corroboration,
  not as the licensing anchor.
- [verified: install.sh:349-471, docs/hooks.md:52-60] Every existing
  sentinel in `SENTINEL_INVENTORY` uses opt-in polarity (`default_state`
  always `disabled`; `_sentinel_state_label` prints `ENABLED` on file
  presence unconditionally). An opt-out sentinel needs the array extended
  with a polarity field, not a bare new row — confirmed by reading
  `_sentinel_state_label` and its two callers directly.
- [verified: install.sh:366,482-515,539-550] `_report_account_sentinel` is
  hardcoded to a content-mode check (`mode = "dollars"`) with no
  `default_state`/polarity parameter, and is the sole handler the
  `case "$scope"` dispatch (`install.sh:540-550`) routes `account`-scope
  rows to — confirmed by reading the function and its one call site
  directly. `IFS='|' read -r` with fewer target variables than
  pipe-delimited fields dumps the unconsumed remainder (including literal
  `|` characters) into the last variable — confirmed empirically during
  review — which is why both `read` call sites (`install.sh:366,539`), not
  just one, need the field count updated together.
- [verified: claude/.claude/agents/comment-discipline-reviewer.md] That
  agent's own DO NOT TRIGGER clause already states "for PR bodies or commit
  messages (pr-description's lane, not this agent's)," and its body states
  "you name every violating site … you do not rewrite the text yourself" —
  confirmed by reading the agent definition directly; the agent side of the
  `tighten-prose` boundary needs no edit.
- [verified: require-plan-review.sh:72-74,183-206,261-275] The gate matches
  `Write`/`Edit`/`MultiEdit`/`ExitPlanMode` against a content-addressed
  marker for any modified plan file in `.claude/plans/`, with path
  exemptions only for `agent-reviews/` and out-of-repo targets — no
  exemption for the plan file itself. A tighten-prose pass that edits a
  plan file in place after `/plan-review` has run is denied outright, not
  merely re-armed; a pass that ran inside `/plan-review`'s active-marker
  window would record a review marker over bytes no specialist reviewer
  actually read — confirmed by reading the hook directly, which is why
  plan-file tightening is deferred rather than designed here (see Out of
  scope).
- [unverified] The specific rule set drafted for the new skill (sentence
  length target, noun-stack avoidance) is this session's own synthesis of
  the cited sources' general direction, not a verbatim transcription of any
  one source's rule list — flagged so a later reviewer knows to check the
  drafted rules against the cited quotes rather than assume a 1:1 mapping.

## Critical files

- `claude/.claude/skills/tighten-prose/SKILL.md` (new) — invoke-only, no
  `TRIGGER when:` clause, matching the `root-cause-analysis` frontmatter
  shape:
  ```yaml
  ---
  name: tighten-prose
  description: Rewrite prose for clarity and concision — short sentences, active voice, one idea per sentence, consistent terminology — without dropping or softening any fact, number, decision, or hedge. Operates on a drafted PR body, handoff note, or literal input text. Dispatched by /pr-description's prose-tightening pass; also invocable standalone any time drafted text needs the same treatment.
  ---
  ```
  Body, under the 200-line `check-skill-length.sh` cap (default tier, no
  exception needed):
  1. **Scope: drafted prose, not durable in-repo content.** PR bodies,
     handoff notes, and text the invoker hands over directly. Do not
     rewrite code comments, `REFERENCES.md`, doc files, README sections,
     skill or agent bodies, or a plan file under `.claude/plans/` — that
     prose is judged by a different standard, where the required action is
     to name the violating site, not to rewrite it (see
     `comment-discipline-reviewer`). Invoked against a durable doc or a
     plan file, say so and stop. A caller may still name a specific
     section of a non-plan durable doc to tighten, and then only that
     section is in scope — a plan file is never in scope, not even by
     named section (see Out of scope for why).
  2. **Preserve every fact and its original strength — stated first, as the
     overriding constraint.** Rewrite phrasing and structure only; never
     delete, merge away, or soften/harden a claim, number, decision, hedge,
     or conditional the input stated. When a rewrite would need to drop or
     flatten something to shorten it, keep the content and accept the
     longer sentence instead.
  3. **Carve-outs, left untouched verbatim:**
     - Syntactic: fenced code blocks, inline code spans, file paths,
       identifiers, proper nouns (tool/library/repo names), numbers and
       units, markdown headings, markdown tables, any machine-managed
       delimited block (e.g. `<!-- pr-cost:start -->`/`:end`).
     - Semantic (undetectable by pattern-matching, so excluded rather
       than trusted to a rewrite-then-check loop): hedges and modal verbs
       ("may," "should,"
       "could," "is likely to"), quantifiers ("some," "most," "all"),
       negation scope, and conditional clauses ("if X, then Y") — do not
       reword a sentence carrying any of these; leave the whole sentence
       as-is.
     - Whole-sentence-class: deploy/coordination steps, security-invariant
       claims, and reviewer-action items — leave these sentences untouched
       even when they're otherwise verbose. (`pr-description`'s own
       "Coordination-step preservation" section already treats this
       content as high-stakes; this skill defers to that judgment rather
       than re-deciding it.)
  4. **Rewrite rules** for everything outside the carve-outs above, each
     stated on its own rationale — no inline citation of Google or ASD by
     name, matching `pr-description`'s own convention of stating rules on
     their own terms and keeping source attribution in `REFERENCES.md`
     only: active voice except when the actor is unknown or irrelevant to
     the reader; target ~20-25 words per sentence; one idea per sentence —
     split compound sentences joined by "and" that carry two separate,
     unconditional claims (never split a sentence containing a carved-out
     conditional); pick one term per concept and keep it for the whole
     document (no elegant variation); avoid noun-stack phrases (rewrite
     into a verb phrase or prepositional phrase); prefer plain, common
     verbs over inflated ones ("start" not "commence").
  5. **Input handling.** If given a file path, `Read` it, apply the
     rewrite, `Edit` it in place, and report the actual changed lines (a
     diff-shaped before/after list, not a prose summary — a summary can't
     be checked by the reader; the real lines can). If given literal text
     (no existing file), return the rewritten text inline.
  6. **Self-check before returning.** Re-read input and output side by
     side, sentence by sentence; confirm every fact, number, decision, and
     hedge/conditional strength in the input matches the output. This is a
     secondary net for ordinary prose — the carve-outs above are the
     primary defense for the highest-risk sentence classes, not this check.
- `claude/.claude/skills/tighten-prose/REFERENCES.md` (new) — the
  citations from the Approach section above (Google Developer
  Documentation Style Guide CC BY 4.0 quote and the rules it grounds:
  active voice, second person, conditions-before-instructions,
  controlled-vocabulary word-list rationale; Digital.gov Plain Language
  quotes as corroboration, with the reuse-terms caveat noted), plus the
  ASD-STE100 FAQ/About-page findings (free to obtain, no published
  reproduction terms) as the lineage note explaining the name change from
  the user's original ask.
- `claude/.claude/skills/pr-description/SKILL.md` (modify) — new
  `## Prose tightening pass` section placed **after `## Cost section` and
  before `## Checks`**, not after Checks (moved from the first draft's
  placement, per the Opus review: the
  existing reader-coherence pass and content-claim verification in
  `## Checks` must run against the final, already-tightened bytes, not
  pre-rewrite text — otherwise `## Checks` validates a version of the body
  that never ships).
  ```bash
  case "${CLAUDE_CONFIG_DIR:-}" in
    /*) config_dir="${CLAUDE_CONFIG_DIR%/}" ;;
    *) config_dir="$HOME/.claude" ;;
  esac
  [ -e "$config_dir/pr-description-tighten-prose-optout" ]
  ```
  Same config-dir resolution as the Cost section's gate — restated here,
  not shared code, per the no-shared-partials convention. Presence of the
  file (any content, or none) opts out; absence means the pass runs. When
  the gate doesn't fire, dispatch `tighten-prose` against the drafted body
  file, instructing it to leave the `## Cost` / `## Deferred review
  findings` machine-managed blocks and the attribution trailer untouched
  (the skill's own carve-out rule already protects code spans, headings,
  identifiers, and file paths). Note explicitly in this section that the
  pass runs on the body *after* `$ARGUMENTS` has already been folded in
  per "What the body must carry" — it is a style pass over settled
  content, not a second paraphrase of the caller's account, so it doesn't
  reopen the "do not silently paraphrase `$ARGUMENTS`" rule.
- `install.sh` (modify) — a `staff-platform-engineer` review of this piece
  specifically (dispatched after reading the real array/functions, not
  just the plan's own line-range citation, which had stopped short of
  `_report_account_sentinel`) found two blockers in the first cut of this
  design; the precise fix below incorporates both. Only the new row's own
  scope (`account`) needs new behavior — `_sentinel_state_label`,
  `_report_machine_sentinel`, and `_report_repo_sentinel` are untouched;
  the new row is dispatched to `_report_account_sentinel` exclusively by
  the existing `case "$scope"` block (`install.sh:540-550`), so scoping
  the fix to that one function is correct, not an arbitrary narrowing.
  1. **Append two optional trailing fields** to the pipe-delimited row
     schema (`install.sh:380-394`'s comment, updated to document them):
     field 7 `expected-content` (empty = presence-only check, the current
     behavior for every row unchanged; a non-empty value generalizes
     `_report_account_sentinel`'s currently-hardcoded `"dollars"` literal
     — meaningful only for `scope=account` rows) and field 8 `polarity`
     (empty or `opt-in` = current behavior for every row: file absent
     means `default-state`, file present means `ENABLED`; `opt-out` =
     inverted, meaningful only for the new row). Appended *last*, after
     `docs-anchor` — `read` fills omitted trailing fields with empty
     strings, so none of the 13 non-account, non-`pr-cost-disclosure` rows
     need a string edit; their behavior is unchanged by construction.
  2. **Update both `IFS='|' read -r' call sites** to declare all 8
     positional variables: `configure_machine_level_opt_ins`
     (`install.sh:366`) and `report_sentinel_inventory`
     (`install.sh:539`). Both iterate the whole array regardless of scope,
     so both corrupt `docs_anchor` on every row (per `read`'s
     fewer-variables-than-fields behavior, confirmed empirically during
     review) if only one site is updated.
  3. **Generalize `_report_account_sentinel`** (`install.sh:482-515`) to
     take `default_state`, `expected_content`, and `polarity` as new
     parameters (the first already exists as row field 5 but currently
     isn't forwarded to this function — every other reporter already
     takes it). Branch: `expected_content` empty → presence-only check
     against `polarity`-adjusted semantics (mirrors `_sentinel_state_label`
     but account-scoped); `expected_content` non-empty → keep the
     existing trim/lowercase/compare logic, generalized to compare against
     the parameter instead of the literal `"dollars"`. CTA wording (`→ to
     enable:` / `→ to disable:`) follows `polarity`, not a hardcoded verb.
  4. **Edit exactly one existing row** — `pr-cost-disclosure` — to append
     `|dollars` as field 7 (field 8 omitted, defaults to `opt-in`),
     making its content-check explicit under the generalized function
     rather than relying on the old hardcoded literal. This is the one
     existing-row edit the design needs; every other existing row is
     untouched.
  5. **Register the new row:**
     `pr-description-tighten-prose-optout|account|Prose-tightening pass
     opt-out (this account)||enabled||opt-out|docs/hooks.md § Prose
     tightening opt-out` (field 7 empty — presence-only check; field 8
     `opt-out`).
- `claude/.claude/hooks/tests/test_install_sh_sentinel_inventory.py`
  (modify) — the existing `assert len(fields) == 6` (line 189) must accept
  6, 7, or 8 fields per row. Add: (a) a regression test that every
  pre-existing 6-field row, run through the *post-change* report path,
  reproduces its pre-change label and CTA text byte-for-byte — the actual
  backward-compatibility claim this design rests on, not just a
  hand-built fixture on each side; (b) a test for `pr-cost-disclosure`'s
  generalized content-check still requiring exactly `dollars`; (c) a test
  for the new row's opt-out presence/absence labels and CTA wording; (d)
  a test pinning behavior for an unrecognized `polarity` value (reject at
  definition time, or explicit fallback to `opt-in` — pick one during
  implementation and assert it).
- `docs/hooks.md` (modify) — add a `### Prose tightening opt-out`
  subsection under (or replacing) "Non-hook opt-in sentinels" (rename the
  section heading to cover opt-out too, since it will hold both patterns
  after this change), at the same documentation depth as the existing
  `pr-cost-disclosure` entry: what it gates, its opt-out polarity and why
  (mirrors `pr-cost-disclosure`'s own "why this needs explaining beyond
  the array" treatment), and its account scope. Matches the `docs_anchor`
  string used in the new `install.sh` row.
- `claude/.claude/settings.json` (modify) — add `"tighten-prose":
  "name-only"` to `skillOverrides`. Reuse: `claude/.claude/skills/tests/
  test_skills.py`'s `_name_only_skills()` derives its skill list from this
  map dynamically — no test-file edit needed for the new skill's structural
  contracts (`TestNameOnlySkillContracts` auto-covers it).
- `docs/skills.md` (modify) — add the skill to the main skills list and to
  the "Skills available by name" table, assigned to the workflow-utility
  category (alongside `brief`, `handoff`, `pr-description`, etc.) —
  bumping that category's count from seven to eight and the table's total
  from twelve to thirteen. Grep the file for every other count or roster
  naming the name-only set or the workflow-utility group (at minimum: the
  parenthetical roster of workflow utilities, and the `skillOverrides:
  name-only` count) and update each one found, not only the two named
  above. Also add a one-line boundary note next to the new entry —
  distinguishing it from `comment-discipline-reviewer` (rewrites drafted
  prose vs. flags durable in-repo prose) — since this is the human-facing
  surface where the two sit near each other in the same list.
- `README.md` (modify) — one line near the existing Cost-section paragraph
  (~line 346) documenting the default-on prose pass and its opt-out
  sentinel, matching that paragraph's existing style.
- `claude/.claude/skills/tests/test_skills.py` (modify) — extend
  `pr-description`'s existing test class with structural assertions for the
  new gate (mirroring `test_declares_account_scoped_mode_gate` and
  neighboring Cost-section tests at lines 682-706): declares the sentinel
  path, declares the section's placement before `## Checks`, declares the
  dispatch-by-name call. Also add a regression assertion on
  `tighten-prose`'s own SKILL.md body — mirroring
  `TestContinuityFileBucketCrosscheck`'s string-presence guard on
  `brief`'s critical-rule text (`test_skills.py:794`) — asserting that the
  scope-rule's carve-out phrases (`"REFERENCES.md"` and `".claude/plans/"`
  at minimum) are present, so a future edit can't silently drop the
  boundary the naming-collision comment required.

## Verification

- `../../../.venv/bin/pytest claude/.claude/` from the worktree — full
  suite, including the dynamic `TestNameOnlySkillContracts` coverage for the
  new skill, the new `pr-description` structural assertions, and the
  extended `test_install_sh_sentinel_inventory.py` polarity tests.
- `../../../.venv/bin/ruff check claude/.claude/` — lint (no Python beyond
  the sentinel-inventory test changes; run for parity with repo
  convention regardless).
- `./install.sh` (or its sentinel-inventory report path) manually, to
  confirm the new row prints an accurate ENABLED/DISABLED state and a
  correctly-worded CTA for its opt-out polarity — this is the one path
  that can't be fully covered by unit assertions on the string output
  alone; eyeball the real terminal rendering once.
- Manual: draft a PR body from a deliberately verbose/passive-voice
  `$ARGUMENTS` string via `/pr-description`; confirm the delivered body is
  tightened and every fact/decision/hedge from the input survives with its
  original strength, and that a sentence carrying a conditional or a
  coordination step is left untouched. Set the opt-out sentinel and confirm
  the pass is skipped. Invoke `/tighten-prose` standalone on an unrelated
  paragraph to confirm it works outside `pr-description` too. Invoke it
  against a specific comment inside an existing `.py` or `.sh` file, and
  separately against a doc file's prose, and confirm both decline per the
  new scope rule instead of rewriting. Invoke it against a file under
  `.claude/plans/` and confirm it declines outright, without attempting
  even a caller-named section.
- `/skill-review` runs automatically from `/code-review`'s per-file-type
  dispatch on the new and modified `SKILL.md` files (hook-enforced).

## Out of scope

- **Re-tightening prose an agent adds or edits later in a PR's review
  cycle.** The engineer raised a real drift concern (agents editing PR
  bodies repeatedly across a review cycle, so a one-shot pass could leave
  freshly-tightened and untightened prose side by side over time) and
  asked whether bot vs. human edits could be distinguished to resolve it.
  Confirmed via GitHub's REST event docs (G3 above): they can't, under a
  shared account. A whole-body idempotency-hash marker (same pattern as
  the existing Cost/Deferred blocks — re-tighten on any content change,
  skip when unchanged) was proposed as a way to route around needing that
  distinction, but the engineer chose to defer it rather than adopt it
  this round. The known interim behavior: a PR body that goes through
  multiple `pr-description` syncs across a review cycle gets tightened
  prose from its first author-mode draft, and untightened prose from
  whatever's added or changed afterward — worth stating in the PR body of
  the implementing PR as a known limitation, not just a silent deferral.
  Follow-up: revisit once this behavior has been observed in practice.
- **A prose-tightening pass over plan files.** Two shapes were considered
  and both set aside. Rewriting the plan file in place is blocked
  mechanically: `require-plan-review.sh` gates `Write`/`Edit`/`MultiEdit`
  on any uncommitted or modified file in `.claude/plans/`, exempts only
  `agent-reviews/` and out-of-repo targets, and matches markers on plan
  content — so a tighten after review is denied outright, and a tighten
  inside `/plan-review`'s active window would record a marker over bytes
  no specialist reviewer read. Printing an ephemeral tightened summary in
  chat instead avoids the gate entirely, but needs a capability this skill
  does not have: rule 2 above forbids dropping or flattening anything to
  shorten, so `tighten-prose` returns a rewritten plan, not a summary. It
  would also move the human's approval surface off the artifact the gate
  covers and the implementer reads. If plan prose is worth improving, the
  lever is `plan-it`'s own authoring guidance (it already requires a
  one-sentence Context goal and a plain-language Approach lead — the gap
  this plan's own Context section demonstrates is compliance with that
  existing rule, not a missing mechanism), not a post-hoc rewrite or
  summarization pass. Filed as a follow-up:
  https://github.com/jcdendrite/claude-config/issues/712.
- **ASD-STE100 certification/compliance.** This skill implements
  STE-inspired principles grounded on openly-licensed sources, not the
  certified ASD standard — see Approach.
- **Default-on integration elsewhere** (`/handoff`, `/brief`, commit
  messages). Only `/pr-description` gets default integration; the new
  skill stays invocable standalone anywhere else.
- **A dedicated bot/machine GitHub identity** for agent-made edits,
  considered while researching the sync-mode drift question and rejected
  as a disproportionate mechanism for a stylistic-preference feature.
