# Set the main-bucket prompt cache TTL to 5m

## Context

Prompt-cache rebuild cost on this machine is dominated by idle-gap cache
expiry, and the main-conversation bucket currently runs on the vendor's
1-hour cache TTL tier by default — a tier that bills cache writes at a
strictly higher rate than the 5-minute tier while, on the measured corpus,
not recovering that premium through warm reads. The measurement machinery
needed to decide this was built and shipped by two prior PRs (#995, #1021),
and a `cache-rebuild --ttl-verdict` run on 2026-09-19 returned `adopt` for
the `main` bucket in the 1h→5m direction and `decline` for the `subagent`
bucket. Every prior plan in this family deliberately scoped out the settings
change itself. The intended outcome of this plan is to flip that key — set
`promptCacheTtl: "5m"` in the stow-distributed settings file, leave
`subagentPromptCacheTtl` unset, and record the decision durably — making this
the first plan in the family to change behavior rather than measurement.

## Approach

Set `"promptCacheTtl": "5m"` in `claude/.claude/settings.json`, the stow-source
file that maps onto `~/.claude/settings.json` for every consumer who runs
`./install.sh`, and leave `subagentPromptCacheTtl` absent. Pin both facts — the
value and the deliberate absence — with a test in
`claude/.claude/hooks/tests/test_hook_alignment.py`, record the reasoning in a
new `docs/design-decisions/` file, announce the consumer-visible flip in
`CHANGELOG.md`, and correct the two sentences in `docs/transcript-analysis.md`
that currently say the main bucket's lever is scoped out of shipping.

### Root problem

The main-conversation prompt-cache bucket runs on the vendor's 1-hour TTL tier,
which bills every cache write at 2x base input price against the 5-minute
tier's 1.25x, and the analyzer shipped by PRs #995 and #1021 returned `adopt`
in the 1h→5m direction for that bucket under a decision rule pre-registered
before the run. Each project's transcript corpus was analyzed on its own rather
than pooled, and the 1h→5m recommendation held in every project the
pre-registered dominant-tier-share gate admitted to the verdict — a project the
gate holds back as a near-tie or as having no data contributes nothing in
either direction, by construction. Every plan in this family deliberately
deferred the settings change, so the measured verdict has never been acted on
and the corpus it was computed over is on a rolling deletion window.

### Givens (fixed beyond this design's reach)

- **G1 — The vendor owns the bucket split, the value domain, and the per-bucket
  defaults.** `promptCacheTtl` and `subagentPromptCacheTtl` each accept only
  `"5m"` or `"1h"`, cover disjoint request populations, and carry their own
  defaults; this design chooses within that structure and cannot introduce a
  third bucket or a per-request TTL.
  `[verified: https://code.claude.com/docs/en/settings-reference]`
  Both keys require Claude Code v2.1.242 or later; `subagentPromptCacheTtl`'s
  per-agent frontmatter sibling `experimental.cacheTtl` requires v2.1.248.
  `[verified: https://code.claude.com/docs/en/settings-reference for both key
  floors; https://code.claude.com/docs/en/sub-agents for the frontmatter floor]`
- **G2 — The vendor owns the six-rung TTL-selection precedence chain, and a
  bucket's *setting* sits above per-agent frontmatter in it.** Quoted verbatim
  from https://code.claude.com/docs/en/prompt-caching § "Choose the TTL
  yourself": "When more than one control applies, Claude Code takes the first
  match in this order: 1. `FORCE_PROMPT_CACHING_5M=1`, which forces five
  minutes for both buckets 2. The bucket's environment variable 3. The bucket's
  setting 4. For a subagent's requests, the `cacheTtl` value in the subagent's
  `experimental` frontmatter field... 5. `ENABLE_PROMPT_CACHING_1H=1`, which
  requests one hour for both buckets 6. The default for the request's bucket".
  This is one ordered fallback chain in which frontmatter is a legitimate rung,
  not a pair of mutually exclusive mechanisms — but because rung 3 precedes rung
  4, a global `subagentPromptCacheTtl` wins over any per-agent
  `experimental.cacheTtl` whenever both are present.
  `[verified: verbatim quote above]`
- **G3 — The vendor owns the price multipliers.** Quoted verbatim from
  https://platform.claude.com/docs/en/build-with-claude/prompt-caching:
  "5-minute cache write tokens are 1.25 times the base input tokens price" /
  "1-hour cache write tokens are 2 times the base input tokens price" / "Cache
  read tokens are 0.1 times the base input tokens price". These are published
  list multipliers, not corpus measurements. `[verified: verbatim quotes above]`
- **G4 — `claude/.claude/settings.json` is one committed file stowed to every
  consumer, and it cannot hold two values.** A default here is a default for
  everyone who pulls, which is why the pre-registered ship rule demanded
  unanimity across roots before the key became expressible at all.
  `[verified: root CLAUDE.md § "Working in this repo" repo-layout bullet and
  § "Plans in this repo affect all stow users"]`
- **G5 — JSON has no comment syntax, so a deliberately-absent key cannot explain
  its own absence in the file.** Any durable rationale for
  `subagentPromptCacheTtl` staying unset has to live outside `settings.json` or
  be enforced by a test. `[verified: the file itself is plain JSON; the repo's
  own precedent is that every prior settings-default decision is pinned in
  `test_hook_alignment.py` and explained in `docs/design-decisions/`]`
- **G6 — Claude Code deletes transcripts on a rolling window, so the verdict's
  corpus cannot be re-derived later from the same data.** The recorded verdict
  is the evidence of record; a re-run measures a different window, not the same
  one again. `[verified: prior plan G6 in
  `.claude/plans/cache-ttl-tuning-analysis.md:62`, citing
  `docs/transcript-analysis.md`'s cost-ledger section]`

### Mechanisms

- **M1 — Add `"promptCacheTtl": "5m"` as a top-level key in
  `claude/.claude/settings.json`, and nowhere else.** This is the single lever
  that reaches the bucket the verdict covered, at the one scope that reaches the
  population the verdict was computed over. That population was scored per
  project, separately, never pooled into one average, and the direction agreed
  across every project the gate admitted — which is the unanimity condition the
  prior plan pre-registered as the thing that makes a committed default
  expressible at all. `anchors: root, G1, G4, row1, row2, row14`
- **M2 — Over-powered-primitive check on M1.** A committed stow-distributed
  settings key is the widest primitive available here, so four lighter ones from
  the same vendor precedence chain were checked against G2's verbatim list, and
  each fails:
  - `FORCE_PROMPT_CACHING_5M=1` (rung 1) forces five minutes for *both* buckets,
    overriding the subagent bucket the verdict explicitly declined to move, and
    it is per-shell machine state that cannot ship through stow.
  - The main bucket's own environment variable (rung 2) is main-only and
    per-machine, but it outranks rung 3 — shipping the value there would
    silently foreclose a consumer's own settings-file override, a *wider*
    effective precedence than the settings key, not a narrower one. It also
    cannot be a committed artifact.
  - Per-agent `experimental.cacheTtl` (rung 4) is read only from subagent files
    and cannot reach main-conversation traffic at all — wrong bucket entirely.
  - A repo-local `.claude/settings.json` entry is genuinely narrower, but it
    reaches only sessions working in this repository, while the traffic the
    verdict scored is machine-wide main-conversation traffic across every root;
    it would leave most of the measured population uncovered and would put a
    second copy of the same default in the tree.

  The settings key at stow scope is the only primitive whose reach matches the
  measurement's reach. `anchors: root, G2, G4, row3`
- **M3 — Leave `subagentPromptCacheTtl` absent rather than pinning it to
  `"5m"`.** Setting it would change no behavior today (the subagent bucket
  already defaults to five minutes) while occupying rung 3 and thereby silencing
  rung 4 for every stow consumer — a capability revocation bought for nothing.
  The verdict for that bucket was `decline`, so there is no measured basis to
  write any value there. The alternative considered and set aside: pinning it to
  make the default explicit and immune to a vendor default change. Rejected
  because a vendor default change is a reason to re-run the verdict, not a
  reason to have pre-frozen an unmeasured value, and because the absence is made
  legible by M4 and M5 rather than by the key's presence.
  `anchors: G1, G2, G5, row4, row5`
- **M4 — Pin the set value and the deliberate absence as two separate tests in
  `claude/.claude/hooks/tests/test_hook_alignment.py`, each with its own
  docstring, modeled on
  `test_syncclaudeaiskills_stays_disabled_in_stow_source_settings`
  (`:666-682`).** That file is where every prior settings-default decision in
  this repo is pinned — `attribution.sessionUrl` (`:539`, `:557`), the
  `attribution` closed key set (`:582-589`), `syncClaudeAiSkills` (`:678`),
  `permissions.deny` membership for `EnterPlanMode` (`:517`) and
  `ScheduleWakeup` (`:605`). G5 means an absent key has no in-file place to
  explain itself, and an assertion is the only mechanism that makes
  "deliberately unset" survive a future well-intentioned edit. Two tests rather
  than one because Python's `assert` short-circuits: a single edit regressing
  both facts would report only the first and leave the second silently unguarded
  until the next run — which is why the repo already keeps
  `attribution.sessionUrl` and `syncClaudeAiSkills` as separate tests rather
  than one combined settings assertion. Each docstring carries the same
  disclosure its template does: it proves the *declared* config state, not that
  the harness honors the key at runtime. `anchors: G5, M1, M3, row6`
- **M5 — Record the decision in one new file under `docs/design-decisions/`, as
  the canonical home the CHANGELOG points at rather than restates.** The entry
  covers what the lever is, why 5m for main, why `subagentPromptCacheTtl` stays
  unset, the CLI version floor, the corpus's billing regime and what it bounds
  (M8), how to reverse the change and what reversal actually restores (row 20),
  and what a consumer with a different traffic mix does — that last led by the
  precedence-guaranteed environment-variable override rather than by the
  settings-file one (row 10). It also records that the figures behind the
  verdict are withheld by the repo's own redaction rule.
  `anchors: G5, M3, M8, row7, row8, row10, row19, row20`
- **M6 — Correct the stale "main has no lever" claim in all four arms it
  occupies, and hand-sync the two captured-sample passages that reproduce two of
  them.** The claim lives in four places across two files: narrative prose in
  `docs/transcript-analysis.md:964` ("so a main-origin split would have no lever
  to point at") and `:974` (the main row's lever is one "that
  `.claude/plans/cache-ttl-tuning-analysis.md` scopes out of shipping"), and
  printed string literals in
  `claude/.claude/scripts/transcript-analysis.py:7031-7033` and `:7084-7087`.
  All four are falsified by this PR. Per CLAUDE.md § "Audit structural siblings
  before scoping a fix narrowly", fixing a subset is the failure mode the rule
  names. The earlier draft of this plan deferred the two source-code arms on the
  belief that a committed regression test pinned that printed output; no such
  test exists (row 17), so the deferral had no basis. Once the printed strings
  change, the captured sample block at `docs/transcript-analysis.md:815-937` —
  one fence, reproducing the old text at `:872-874` and `:916-919` — becomes the
  thing that shows output the tool does not produce, inverting the prior plan's
  own reason for leaving it alone. Those two passages are therefore hand-edited
  to match the new printed text, character-for-character, and nothing else
  inside the fence is touched: no re-run, no regenerated sample, no new figure
  enters the file. `anchors: root, M1, row9, row17, row18`
- **M7 — Add a one-line forward pointer to `docs/cost-levers-considered.md`'s
  Cache-TTL row, and change nothing else in that file.** The row concludes "do
  not set either variable" (`:36-39`), which this PR reverses for
  `promptCacheTtl` and leaves standing for `subagentPromptCacheTtl`. A reader
  who meets the register first would otherwise be told not to do the thing the
  repo has now done. The pointer names the new design-decisions entry and states
  that the `promptCacheTtl` half is superseded there; it does not rewrite the
  verdict, restate the row's rationale, or touch the corpus share published in
  the sentence above it. The gated full register update stays out of scope.
  `anchors: root, M5, row22`
- **M8 — State the billing regime as a scope boundary on the verdict wherever
  the change is described.** The vendor makes the main bucket's *default* TTL
  billing-regime-conditional: one hour on a Claude subscription within plan
  usage, five minutes under usage credits, an API key, or a cloud provider.
  Three consequences the plan has to carry rather than assume away. The measured
  corpus is inferably subscription-billed — its main-bucket writes landed on the
  1-hour tier, which is consistent with subscription billing rather than proof of
  it, since `ENABLE_PROMPT_CACHING_1H=1` can force that tier under any regime and
  would not be visible to an in-tree grep — and this repo already records the
  qualifier in the same hedged form at `docs/transcript-analysis.md:939`. For a
  consumer on metered or cloud billing, M1 is a no-op that pins a value their
  harness was already using, so describing this as a behavior change for every
  consumer overstates its reach. And whether the verdict generalizes across
  billing regime is a different question from whether the analyzer's gate is
  sound — row 1 answers the second and says nothing about the first. `anchors:
  root, G1, row1, row19`

### Assumptions

| # | Assumption | Tag | Anchors |
|---|---|---|---|
| 1 | The verdict this change acts on is sound: the tool returned `adopt` for the `main` bucket in the 1h→5m direction and `decline` for `subagent`, and the informational tier-split line cannot affect either. The real gate is a zero-tolerance raw-token sign check of `Z` against `W1h` (`_cache_rebuild_token_tiebreaker_favors_5m`), consumed by `_cache_rebuild_root_verdict_input`; the tier-split line is a separate dollar-slice comparison whose own docstring states it "never feeds `root_inputs`, the verdict, or any count." They share no computed value, and every consistent 1h-tier main root reported `Clears=True`. | `[verified: transcript-analysis.py:6369-6381, :6401-6432, :7229 for the gate; :6435-6448 and :7245-7246 for the informative-only line; test_transcript_analysis.py:11452-11487 pins the observed shape]` | root, M1, row19 |
| 2 | Landing the key in the shared stowed settings file is the engineer's own selection. | `[engineer-verified: "Shared stowed settings"]` | root, M1 |
| 3 | That label refers specifically to `claude/.claude/settings.json` and not additionally to the repo-local `.claude/settings.json`, so the key lands in exactly one file. This reading is model-authored, not the engineer's; the `attribution.sessionUrl` precedent put the same key in both files, but only because its threat model was a public repo cloned by a contributor with no stow package, which does not apply to a machine-wide cost measurement. | `[unverified]` | M1, M2 |
| 4 | Leaving `subagentPromptCacheTtl` unset preserves per-agent `experimental.cacheTtl` as a future lever, and setting it to `"5m"` would change nothing today while removing that. Follows from G2's rung ordering plus the vendor's stated five-minute default for the subagent bucket. | `[verified: G2's verbatim precedence list; settings-reference statement that `subagentPromptCacheTtl` covers subagents, workflows, compaction, and session titles — "the requests `promptCacheTtl` doesn't"]` | M3 |
| 5 | Neither key appears anywhere in this repo today outside plan and doc prose — zero code files, zero settings files, zero agent frontmatter — so nothing in-tree is currently relying on either value. | `[verified: repo-wide grep for `promptCacheTtl` returned only `.claude/plans/*.md` and `docs/transcript-analysis.md`; `grep -rn "cacheTtl\|^experimental" claude/.claude/agents/*.md` returned nothing]` | M1, M3 |
| 6 | No committed test asserts a closed top-level key set for `claude/.claude/settings.json`, so adding a key breaks nothing; the only closed-set assertion in the file is scoped to the `attribution` object (`test_hook_alignment.py:582-589`). `guard-settings-session-keys.sh`'s `GUARDED_KEYS_JSON` covers `model`, `effortLevel`, `skipAutoPermissionPrompt`, `skipWorkflowUsageWarning`, `theme`, `tui`, `env.CLAUDE_CODE_EFFORT_LEVEL`, `env.ANTHROPIC_MODEL` — `promptCacheTtl` is not among them, so the commit-time gate will not fire on this edit. | `[verified: guard-settings-session-keys.sh:98-107; grep for top-level key-set assertions across `claude/.claude/` found only the attribution-scoped one]` | M4 |
| 7 | Adding a `docs/design-decisions/` entry alongside the commit-message and PR-body justification is the engineer's own selection. | `[engineer-verified: "Add a design-decisions entry too"]` | M5 |
| 8 | The entry's required shape: filename matching `^[a-z][a-z0-9-]*\.md$`, exactly one H1, and line 3 an italic single-asterisk provenance line. A decision recorded after the directory split carries a bare ISO date and **must not** carry a `Formerly docs/design-decisions.md §N.` clause — the legacy set is closed at §63 and a §64 fails `test_legacy_numbers_form_contiguous_range`. Every relative markdown link must resolve from `docs/design-decisions/`. A `## Sources` section is a strong convention but is not mechanically enforced. | `[verified: .claude/rules/design-decisions.md; test_design_decision_files.py:69-115, :250-297, :332-371, :450-478]` | M5 |
| 9 | Both narrative-prose sites in `docs/transcript-analysis.md` are outside captured tool output and are therefore freely editable: `:974` sits in prose about the switch-delta section, `:964` in prose about the cause-attribution table. The captured sample is a single fenced block running `:815-937`, and it reproduces the stale claim twice, at `:872-874` and at `:916-919` — the earlier draft of this row named one passage and the wrong fence bounds. | `[verified: read of docs/transcript-analysis.md:860-990; fence boundaries confirmed by scanning the file's fence lines]` | M6 |
| 10 | Consumer recourse has two tiers and the reader-facing copy must lead with the stronger one. G2's precedence chain guarantees that rung 1 and rung 2 — the environment variables — outrank the bucket's setting, so an environment-variable override is mechanically certain from the vendor's own ordered list. A higher-precedence settings file is the second option and is only *documented*, not observed: this repo has two recorded cases where a documented or assumed settings scope did not hold for sibling keys in this same file — `ui-notification-defaults-in-stow-source.md` found `theme`, `tui`, and `agentPushNotifEnabled` not honored at `~/.claude/settings.local.json`, the exact file a consumer reaches for first, and `schedulewakeup-denied-by-bare-tool-name.md` found neither project nor project-local scope re-enabled the tool when tried. Every reader-facing surface must name those two failures rather than only carrying the word "documented"; the `ScheduleWakeup` CHANGELOG entry's texture is the standard. The bucket environment variable's exact spelling must be read off the settings reference at writing time rather than guessed. | `[verified: G2's verbatim precedence list; settings-reference scope statement; the two design-decision files named]` + `[unverified]` on the env var's spelling | M5 |
| 11 | `promptCacheTtl` requires Claude Code v2.1.242 or later, which the CLI installed here clears. A consumer below that floor receives a settings key their CLI does not recognize; the expectation is that it is ignored and they keep the prior behavior, which is the status quo rather than a regression — but no source read states how an older CLI treats an unrecognized settings key, and this governs whether the flip is safe for every stow consumer, not only for this machine. SchemaStore's `claude-code-settings.json` — already in this repo's citation corpus via `attributionsessionurl-false-ships-in-both-settings.md`'s Sources list — sets top-level `additionalProperties: true` and lists neither cache-TTL key, which is real evidence that the published settings contract tolerates unrecognized top-level keys. That is a statement about the documented schema, not about whatever validation the CLI itself performs at load time, so it narrows the uncertainty without closing it. | `[unverified]` — no primary source on unknown-key handling; disclose as expected, not confirmed, wherever it is stated. Disposition chosen by the engineer: `[engineer-verified: "Disclose, don't verify"]` | root, M5, row12 |
| 12 | No `install.sh` gate is warranted for the v2.1.242 floor. README:99 supports exactly one conclusion here — that this repo declines to block installation over a version floor — and nothing beyond it. It does not establish equivalent safety, for two reasons: each skill behind that floor actively self-diagnoses the version gap at runtime and says so, which a settings key has no counterpart for, and that floor governs an opt-in surface while this key reaches every consumer unconditionally, a wider population rather than a narrower one. The residual safety argument rests on row 11, which is `[unverified]`, so this row claims only that an installer gate is disproportionate — not that below-floor consumers are demonstrably fine. A README Requirements bullet is likewise not added, since the floor's consequence is inaction rather than the silent half-run failure README:99's bullet exists to warn about. | `[unverified]` — a scoping judgment resting on row 11 | M5, row11 |
| 13 | The prior plans' framing that the two subagent levers are "mutually exclusive, not layered" (`.claude/plans/cache-ttl-tuning-analysis.md:58`) is wrong as to mechanism and right as to effect. The new entry states it the accurate way per G2; it does not edit the merged plan, which is a preserved record under CLAUDE.md § "Scope discipline" Axis 3, and no in-place supersession edit is due because `.claude/rules/design-decisions.md`'s supersession rule governs decision files, not plan files. | `[verified: G2's verbatim list; .claude/rules/design-decisions.md § "Supersession"]` | M5 |
| 14 | The per-project framing is releasable: the corpus was analyzed per project separately, and the recommendation still held. The word is "projects", never "clients" — independently required by root `CLAUDE.md` § "Working in this repo"'s terminology rule, not only by the owner's ask. | `[engineer-verified: "you can also say that different projects were analyzed separately (don't say clients) and that the recomendation still held."]` | root, M1, M5 |
| 15 | Row 14's release does not reach any cardinality. Publishing how many projects, roots, or accounts there were — including a per-disposition count such as how many the gate held back — stays barred alongside the authorization, so every surface states the gate's semantics ("a near-tie or no-data project contributes no verdict in either direction") instead of how often it fired. The narrowing of row 14's "still held" to "held in every project the gate admitted" is a model-authored correction for accuracy, not the owner's phrasing. | `[verified: docs/private-project-redaction.md:207-216, four bars surviving an authorization; docs/transcript-analysis.md:982-987 for the gate's published semantics]` + the narrowing itself `[unverified]` | root, row14, M5 |
| 16 | Row 14's release covers the committed plan file, the `docs/design-decisions/` entry, the commit message, and the PR body — not the plan file alone. | `[engineer-verified: "Plan, decision entry, and PR body"]` | row14, M5 |
| 17 | No committed test pins either printed caveat string, so correcting them costs no test churn. The prior plan's "M1 regression guard" (`.claude/plans/cache-ttl-tuning-analysis.md:66`) constrains the default-path *accumulators and their read sites*, not printed prose; the earlier draft of this plan over-read it into a prose freeze and deferred the fix on that basis. Two sites exist, not one. | `[verified: grep of claude/.claude/scripts/tests/ for "no corresponding lever", "no lever to point at", and "main-origin split" returned no matches; sites confirmed at transcript-analysis.py:7031-7033 and :7084-7087]` + `[engineer-verified: "Fix both sites in this PR"]` | M6 |
| 18 | Hand-editing the two captured-sample passages to match the new printed text is the right move, and regenerating the sample from a fresh run is not. A regenerated sample would pull current corpus figures into a public file and open a redaction question this PR has no authorization to answer; a surgical two-passage edit introduces no number that is not already in the file. The prior plan's refusal to hand-edit rested on the sample otherwise showing output the tool does not produce — once the tool's output changes, that reasoning points the other way. This inversion is model-authored; the engineer's selection covered the two source sites, not the sample. | `[unverified]` — a scoping judgment, model-authored | M6, row17 |
| 19 | The main bucket's *default* TTL is billing-regime-conditional — one hour on a Claude subscription within plan usage, five minutes under usage credits, an API key, or a cloud provider — so the verdict's reach is bounded by regime. The measured corpus is subscription-billed, inferable from its main-bucket writes landing on the 1-hour tier at all, and this repo already carries the qualifier. For a metered or cloud-billed consumer M1 pins a value their harness already used, making it a no-op rather than a flip. Whether the verdict generalizes across regime is untested and is a separate question from row 1's gate-soundness claim. | `[verified: https://code.claude.com/docs/en/prompt-caching for the conditional default; docs/transcript-analysis.md:939 for this repo's existing "on a subscription" qualifier; :941-944 for the list-price-not-actual-bill disclosure]` | root, row1, M8 |
| 20 | Deleting the key does not restore `"1h"` — it restores whatever the vendor's current default is for the reader's own billing regime, which per row 19 is conditional and can move without anyone touching this repo. A reader who wants `"1h"` specifically must set it explicitly rather than revert the diff. This is non-obvious enough that the entry has to say it outright. | `[verified: G2 rung 6 — "The default for the request's bucket" — read against row 19's conditional default]` | M5 |
| 21 | When the key takes effect is not established. Config-adjacent state in this harness appears session-start-scoped rather than hot-reloaded, so a consumer with a session already open when they `git pull` is *expected* to keep the prior tier until the next launch — but `attributionsessionurl-false-ships-in-both-settings.md` records exactly this class of claim, about this same file, as expected rather than confirmed, and cites supporting evidence drawn from a different subsystem. Every surface matches that hedge or cites a source; none states it as flat fact. | `[verified: docs/design-decisions/attributionsessionurl-false-ships-in-both-settings.md's rollout-window paragraph]` | M5, row11 |
| 22 | A one-line forward pointer in `docs/cost-levers-considered.md` is the engineer's own selection, and the full register update stays gated. | `[engineer-verified: "Add a one-line pointer"]` | M7 |

## Critical files

One `code-writer` dispatch (`model: sonnet`), not a split. The file sets would
partition cleanly, but every one of them needs the same shared background — the
verdict, the precedence chain, and the reasoning for the unset key — restated in
full, and two agents re-deriving "why `subagentPromptCacheTtl` is absent" in
separate contexts can land on different wordings across the test docstring, the
decision entry, and the CHANGELOG. That is precisely the do-not-split condition
in `plan-it/SKILL.md` § "Step 5 — Architecture design".

**Reuse — follow these, do not invent a shape:**

- `claude/.claude/hooks/tests/test_hook_alignment.py:666-682`
  (`test_syncclaudeaiskills_stays_disabled_in_stow_source_settings`) — the exact
  template for the new assertion: module-level `_SETTINGS_PATH`, `json.loads`, a
  failure message naming the repo-relative path and what the flip means, and a
  docstring disclosing that it proves declared config state rather than runtime
  behavior. Its sibling at `:582-589` is the template for the absence half.
- `docs/design-decisions/attributionsessionurl-false-ships-in-both-settings.md` —
  the structural model for a settings-decision entry: H1, italic dated
  provenance line, bolded one-claim-per-paragraph body, an explicit **Revisit**
  list, and a `## Sources` list. Its "every surface stating this coverage uses
  'expected,' not 'confirmed,' until verified" discipline is the one to copy for
  rows 10 and 11.
- `CHANGELOG.md`'s `## [Unreleased]` → `### Changed` entries for
  `disableArtifact`/`disableWorkflows` and for the `ScheduleWakeup` deny — both
  are stow-scope settings flips with a bolded lead sentence, a rationale, a
  `**Migration:**` clause naming the opt-back-in path and its reliability, and a
  pointer to the canonical design-decisions file rather than a restatement of it.

**Files to modify or create:**

- `claude/.claude/settings.json` — add one top-level `"promptCacheTtl": "5m"`
  key. Do not add `subagentPromptCacheTtl`. Do not reorder existing keys; the
  diff should be one line.
- `claude/.claude/hooks/tests/test_hook_alignment.py` — two new tests against
  `_SETTINGS_PATH`, not one. The first asserts
  `settings.get("promptCacheTtl") == "5m"`; the second asserts
  `"subagentPromptCacheTtl" not in settings`. Each gets its own docstring: the
  first states that it pins declared config state only, not runtime behavior;
  the second states that the absence is deliberate because the bucket setting
  outranks per-agent `experimental.cacheTtl` in the vendor's precedence chain.
  One line per fact, per CLAUDE.md § "Code Comments, Documentation, and Prose".
  Splitting is load-bearing, not stylistic — see M4.
- `docs/design-decisions/main-bucket-prompt-cache-ttl-5m.md` (new; slug satisfies
  the enforced grammar) — line 3 must be `*2026-09-20.*` with **no** `Formerly
  §N` clause. Body covers: the lever and its two-bucket structure (G1); why 5m
  for main, stated qualitatively — each project's corpus analyzed separately
  rather than pooled, the pre-registered per-root rule, the `adopt` verdict in
  the 1h→5m direction, agreement across every project the dominant-tier-share
  gate admitted, both boundary points cleared, and the raw-token tiebreaker
  agreeing — together with the note that the gate withholds a near-tie or
  no-data project from the verdict in either direction, and the explicit note
  that the underlying figures, the per-project decomposition, and the number of
  projects involved are all withheld under CLAUDE.md § "Redact
  private-project-identifying content" and `docs/private-project-redaction.md`
  § "Publishing a tooling measurement"; why `subagentPromptCacheTtl` stays
  unset, stating the precedence chain accurately as one ordered fallback in
  which rung 3 outranks rung 4 (row 13); the corpus's billing regime stated
  plainly — subscription-billed, inferable from its main-bucket writes landing
  on the 1-hour tier at all — together with the fact that the main bucket's
  default is billing-regime-conditional, so a metered, API-key, or cloud-billed
  consumer was already on five minutes and sees no behavior change from this
  key (row 19); how to reverse the change, stating outright that deleting the
  key restores the vendor's current conditional default rather than `"1h"`,
  and that a reader who wants one hour must set it explicitly (row 20); the
  v2.1.242 floor, cited to G1's source rather than asserted, with the
  below-floor behavior worded as expected rather than confirmed and the
  SchemaStore `additionalProperties: true` evidence cited as documented-schema
  tolerance rather than confirmed CLI validation (row 11); the consumer
  recourse led by the precedence-guaranteed environment-variable override,
  with the settings-file route second and explicitly marked
  documented-not-observed, naming both prior cases where a documented settings
  scope did not hold for sibling keys in this same file (row 10); and a
  **Revisit** list keyed to a later `cache-rebuild --ttl-verdict` re-run
  inverting the verdict, the subagent bucket's verdict changing, a vendor
  change to the multipliers or the precedence chain, **a vendor change to
  either bucket's default value — distinct from the multiplier and precedence
  triggers, because it silently changes what reversal restores (row 20)**, a
  change in this machine's own billing regime, and the
  `docs/cost-levers-considered.md` register row landing. Add a `## Sources`
  section with the three vendor URLs, `claude/.claude/settings.json`, and
  `.claude/plans/prompt-cache-ttl-main-tier.md`. Every relative link must
  resolve from inside `docs/design-decisions/`.
- `docs/transcript-analysis.md` — four edits, all narrow. Two in narrative
  prose: at `:974`, the clause saying the main row's lever is "scoped out of
  shipping" becomes a statement that the lever is now set in
  `claude/.claude/settings.json`, keeping the surrounding reconciliation-context
  point, which stays true; at `:964`, "so a main-origin split would have no
  lever to point at" is corrected the same way. Two inside the captured sample
  block (one fence, `:815-937`): the passages at `:872-874` and `:916-919` are
  hand-edited to match the new printed strings exactly. Nothing else inside the
  fence changes — no table, no number, no re-run. The `:939` sentence carrying
  the "on a subscription" qualifier is left as is; it is already accurate and
  M8 cites it rather than restating it.
- `claude/.claude/scripts/transcript-analysis.py` — two string-literal edits, no
  logic change. At `:7031-7033`, "Main origin is excluded: experimental.cacheTtl
  cannot reach main-conversation traffic, so a main-origin split would have no
  lever to point at." becomes a statement that main origin is excluded from this
  sub-table because `experimental.cacheTtl` is a subagent-frontmatter lever,
  while the main bucket's own lever is `promptCacheTtl` — the exclusion is
  unchanged behavior, only its stated reason was wrong. At `:7084-7087`, "The
  main row's Net$ has no corresponding lever in this plan's scope --
  experimental.cacheTtl is set in subagent frontmatter and cannot reach
  main-conversation traffic; read it as reconciliation context only." becomes a
  statement that the main row's lever is `promptCacheTtl`, now set in
  `claude/.claude/settings.json`, with the "read it as reconciliation context
  only" clause kept — it stays true. Keep both inside the existing `print(...)`
  literals, keep the ASCII-only `--` convention the surrounding block uses, and
  do not touch any accumulator, read site, or column. No figures.
- `docs/cost-levers-considered.md` — one added line in the Cache-TTL row at
  `:36-39`, naming
  `docs/design-decisions/main-bucket-prompt-cache-ttl-5m.md` and stating that
  the `promptCacheTtl` half of "do not set either variable" is superseded there
  while the `subagentPromptCacheTtl` half stands. Do not edit, recompute, or
  re-cite the corpus share published at `:36-37`; do not rewrite the verdict
  line; do not add a row.
- `CHANGELOG.md` — one `### Changed` entry under `## [Unreleased]`. PRs #995 and
  #1021 added no CHANGELOG entry, because an analyzer flag changes nothing for a
  consumer; this one does, for some consumers, which is why the settings-flip
  precedents above all carry one. No figures. The lead sentence must not claim a
  universal behavior change: the flip is real for a subscription-billed
  consumer within plan usage and a no-op for a metered, API-key, or cloud-billed
  one, whose main bucket already defaulted to five minutes (row 19). The
  `**Migration:**` clause states that the key is live on `git pull` with no
  re-install; hedges effect timing as expected rather than confirmed, matching
  `attributionsessionurl-false-ships-in-both-settings.md`'s rollout-window
  paragraph on this same file (row 21); leads the opt-out with the
  precedence-guaranteed environment-variable override and follows with the
  settings-file route marked documented-not-observed, naming the two prior
  sibling-key failures in the `ScheduleWakeup` entry's texture (row 10); states
  that deleting the key restores the vendor's conditional default rather than
  `"1h"` (row 20); and cites the v2.1.242 floor to G1's source. The entry points
  at the design-decisions file as canonical rather than restating it.

`select-tests.py` needs no rule-table change: `CLAUDE_SETTINGS_JSON` already maps
to `HOOKS_TESTS_DIR`, `SKILLS_TESTS_DIR`, and `SCRIPTS_TESTS_DIR`
(`select-tests.py:498`), `DOCS_DIR` to the first two (`:495`), and `CHANGELOG.md`
to none by design (`:380`).

## Verification

- `.venv/bin/python3 claude/.claude/scripts/select-tests.py` — the repo's
  documented scoped command (root `CLAUDE.md` § "Commands", which also directs
  agents to run this rather than the full suite). It widens on its own here: the
  `claude/.claude/settings.json` edit alone selects the hooks, skills, and
  scripts test domains.
- `.venv/bin/ruff check claude/.claude/ claude-skills/` — a Python test file
  changes. No shell file changes, so the ShellCheck sweep is not needed.
- The `transcript-analysis.py` edit is two string literals with no logic
  change, so `select-tests.py` picking up `SCRIPTS_TESTS_DIR` (via the
  `SCRIPTS_DIR` domain rule at `select-tests.py:374`) plus `ruff` is the whole
  mechanical surface: it proves the module still imports, the surrounding
  `cache-rebuild` tests still pass, and nothing that reads those code paths
  regressed. It proves nothing about the *content* of the new sentences.
- Nothing mechanically ties the captured sample in
  `docs/transcript-analysis.md` to what the tool actually prints, so the two
  hand-edited passages are verified by reading them against the new string
  literals character-for-character before commit. A test asserting that
  correspondence was considered and not planned here: it is brittle against
  any future wording change, and adding it would expand a scope the engineer
  approved for two specific fixes. This drift class recurring is a named
  residual, not an oversight — worth raising to the reviewer as a follow-up.
- What the run actually proves, stated plainly so the plan does not overclaim:
  the settings file still parses as JSON (many tests `json.loads` it, so a
  malformed edit fails loudly), the new key holds `"5m"` and
  `subagentPromptCacheTtl` is absent, the new design-decisions file satisfies the
  directory's filename, single-H1, provenance-line, closed-legacy-number, and
  link-resolution invariants, and nothing that reads
  `claude/.claude/settings.json` regressed. It proves nothing about whether the
  harness honors the key at runtime.
- Runtime honoring is not checkable pre-merge.
  `docs/design-decisions/attributionsessionurl-false-ships-in-both-settings.md`
  records that no CLI subcommand on this harness reports a resolved settings
  value and that `claude doctor` reads settings files without surfacing one, so
  there is no cheap probe. The real post-ship check is a later `cache-rebuild
  --ttl-verdict` run showing main-bucket writes landing on the 5-minute tier;
  that is a Revisit condition in the new entry, not a gate on this PR, and its
  output stays local — never quoted into a commit message, the PR body, or any
  committed file.
- Manual read-back before commit: the plan file, the design-decisions entry,
  the CHANGELOG entry, the two `transcript-analysis.py` string literals, the
  two hand-edited sample passages, the `docs/cost-levers-considered.md`
  pointer line, the PR body, and the commit message contain no dollar figure,
  token count, rebuild count, per-root or per-account count, share, ratio, or
  account label, and no statement bounding how many projects, roots, or
  accounts were in scope. The pointer line in particular must not restate,
  recompute, or update the corpus share already published in the row above
  it. Vendor list multipliers, the published break-even constants, the
  300s/60s boundary points, the committed dominance threshold, and CLI version
  numbers are the only numerals permitted.

## Out of scope

- **A full `docs/cost-levers-considered.md` register update.** That file is the
  canonical register for lever/verdict/reason/source-plan
  (`docs/design-decisions/cost-lever-register-consolidated.md`), and its
  Cache-TTL row's rationale, corpus share, and verdict line all predate this
  decision. Rewriting them is a published-artifact change carrying its own
  owner-approval gate and a stricter disclosure rule than local output, and
  `.claude/plans/cache-ttl-verdict-gate-fix.md:194` already routed it to "a
  separate call after this PR lands." What ships here is the one-line forward
  pointer only (M7), which leaves the row's own claims untouched while
  stopping it from silently contradicting the repo's current state. The
  design-decisions entry names the pending full update in its Revisit list.
- **A repo-local `.claude/settings.json` copy of the key.** Reaches only sessions
  in this repository while the measurement is machine-wide, and would create a
  second home for one default (CLAUDE.md § "Engineering Judgment", single source
  of truth).
- **Per-agent `experimental.cacheTtl` frontmatter on any agent.** The prior plans
  deferred it, it is rung 4 and therefore only actionable while rung 3 stays
  unset — which M3 preserves — and no measurement exists to choose a value per
  agent type.
- **Any environment-variable mechanism (`FORCE_PROMPT_CACHING_5M`,
  `ENABLE_PROMPT_CACHING_1H`, or the per-bucket variables).** Per-machine state
  that cannot ship through stow; evaluated and rejected in M2.
- **An `install.sh` version gate or a README Requirements bullet for the
  v2.1.242 floor.** Row 12.
- **Re-running, re-tuning, or re-litigating the verdict machinery.** The
  `(disagree)` tier-split question is settled by row 1 — nothing to re-run and
  nothing to fix in the analyzer. The dominance threshold, the margin fraction,
  and the boundary pair stay pre-registered and untouched; changing any of them
  after seeing a verdict is exactly what pre-registration exists to prevent.
- **Editing any merged plan file in `.claude/plans/`.** Preserved records under
  CLAUDE.md § "Scope discipline" Axis 3, including the one whose G2 framing row
  13 corrects.
- **A mechanical guard tying the captured sample in `docs/transcript-analysis.md`
  to the tool's printed output.** Declined on brittleness and scope; named as a
  residual in Verification.
