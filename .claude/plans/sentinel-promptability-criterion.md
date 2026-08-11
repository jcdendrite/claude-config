# Explicit criterion for machine-promptable vs. machine (report-only) sentinels

## Context

`install.sh`'s `SENTINEL_INVENTORY` array classifies every machine-level
opt-in sentinel as either `machine-promptable` (offered interactively with
a `[y/N]`/`[Y/n]` question on every `./install.sh` run) or `machine`
(report-only — its state is printed, never prompted). No file in the repo
states an explicit criterion for which scope a new row should get;
`install.sh:379-397`'s schema comment documents only the mechanical
difference between the two, and a repo-wide search (this session) of
`README.md`, every `docs/*.md` page a sentinel references, and every
`.claude/plans/*.md` design doc that introduced a sentinel turned up only
per-sentinel design-time justifications, not a reusable rule. This surfaced
while auditing whether `.cost-ledger-enabled` (shipped in #617) should have
been promptable. The intended outcome: an explicit criterion recorded in
`install.sh` itself, applied to the 6 currently-report-only sentinels, with
any misclassified row reclassified.

## Approach

**Root problem** (verified this session): no explicit criterion exists
anywhere in the repo for `SENTINEL_INVENTORY` scope assignment between
`machine-promptable` and `machine`. [verified: `install.sh:379-397` schema
comment documents mechanics only; grep across `README.md`, `docs/*.md`, and
`.claude/plans/*.md` found no general rule — only per-sentinel history: the
worktree/autonomous-shipping pairing was made promptable for onboarding
consistency between two sibling settings
(`.claude/plans/commit-stall-block.md:121-134`), and
`track-permission-prompts` became the third promptable row when the array
itself was introduced, with no stated comparative rationale
(`.claude/plans/pr-cost-disclosure.md`)]

**Givens:**

- `account`-scope sentinels (currently only `pr-cost-disclosure`) are
  outside this criterion's reach — their promptability question is already
  settled, on different grounds than blast radius: their state is a
  content-based mode string, not boolean presence, and "a mode string has
  no Y/n form" (`.claude/plans/account-scoped-cost-disclosure.md:256-259`).
  [verified: cited source] Given because a separate design decision already
  answered this on a structural axis (scope value) this plan's criterion
  doesn't need to re-litigate.
- `_prompt_sentinel_opt_in`'s confinement to `$HOME/.claude/`-rooted paths
  (`install.sh:317-322`, a defense-in-depth guard) stays unchanged.
  [verified: `install.sh:317-322`] Given because every candidate row's path
  already lives under `$HOME/.claude/`, so this plan never needs to touch
  that guard.
- The 3 existing `machine-promptable` sentinels' own classification is
  outside this plan's reach, even though one (`track-permission-prompts`)
  is closer in shape (a single off-by-default hook, no repo-scope pairing)
  to the two rows this plan reclassifies than to the other two promptable
  sentinels (`worktree-required`, `autonomous-shipping-required`, both
  repo-wide enforcement mechanisms with a paired repo-scope opt-out).
  [engineer-verified — user selected "Audit all 6 non-promptable sentinels"
  via `AskUserQuestion`, not a revisit of the existing promptable trio]

**Criterion** (anchors: root) — to be recorded verbatim, adapted to fit the
existing comment style, in `install.sh`'s `SENTINEL_INVENTORY` schema
comment:

> A `machine`-scope row is promoted to `machine-promptable` when both hold:
> (1) its state is plain boolean file-presence — a content-based sentinel
> (a mode string, not existence) stays report-only under the `account`
> scope instead, since a Y/n prompt has no natural mapping to a mode value;
> (2) enabling the file opts **into** a new, off-by-default capability
> (enforcement, instrumentation, or durable recording) rather than opting
> **out of** an already-on-by-default one. Asking a new contributor "want
> to turn off a default you haven't experienced yet?" at install time is
> premature — a contributor who wants a default off will look it up when
> it bothers them, and the doc page named in the row's last field is where
> that lookup lands.

Applying it to the 6 `machine`-scope rows:

| Sentinel | Direction | Boolean state? | Verdict |
|---|---|---|---|
| `.error-mode-nudge-enabled` | opt **in** (off by default) | yes | **misclassified → promote** |
| `.cost-ledger-enabled` | opt **in** (off by default) | yes | **misclassified → promote** |
| `.handoff-nudge-disabled` | opt **out** (nudge on by default) | yes | correctly report-only |
| `.consume-durable-continuity-disabled` | opt **out** (on by default) | yes | correctly report-only |
| `.commit-stall-block-disabled` | opt **out** ("always-effective" kill switch per `docs/commit-stall-block.md:28`) | yes | correctly report-only |
| `.session-title-disabled` | opt **out** (on by default) | yes | correctly report-only |

Two rows are misclassified under this criterion: `.error-mode-nudge-enabled`
and `.cost-ledger-enabled` are both off-by-default, boolean, opt-into-a-new-
capability sentinels — structurally indistinguishable from
`track-permission-prompts` (already promptable), and not kill-switches like
the other 4. `.cost-ledger-enabled` has an especially direct argument for
surfacing at install time: its own doc states "a week not recorded while it
is still observable cannot be recovered later" (`docs/cost-ledger.md:5-7`)
— exactly the time-value-compounding case a Y/n prompt at onboarding exists
to serve.

**Alternatives considered:**

- *Blast-radius-only criterion* (promptable only for repo-wide enforcement
  mechanisms) — rejected: this would also fail to justify
  `track-permission-prompts`'s existing promptable status (a single
  `Notification` hook, same shape as the two rows this plan reclassifies),
  so it contradicts an existing, presumably-intentional classification
  rather than retrodicting it. A criterion consistent with current shape is
  stronger evidence than one that isn't.
- *Document only, promote nothing* — rejected per the user's explicit scope
  ("fixing any misclassified ones"), and because leaving two opt-in
  sentinels un-prompted while a shape-identical third is prompted is itself
  the inconsistency being audited, not something to leave undocumented and
  unfixed.

## Critical files

1. **`install.sh`** (`SENTINEL_INVENTORY` array, currently `install.sh:398-413`;
   the two target rows are at lines 402-403) —
   - Change `.error-mode-nudge-enabled` and `.cost-ledger-enabled` rows'
     `scope` field from `machine` to `machine-promptable`.
   - Populate both rows' `prompt-description` field (currently empty —
     mandatory per `test_machine_promptable_rows_carry_a_prompt_description`,
     which asserts every `machine-promptable` row has a non-empty field 3).
     **Every existing row's description is plain prose with no backticks or
     other bash-special characters** (`$`, `` ` ``, unescaped `"`) — the
     array is a set of double-quoted bash string literals, so an unescaped
     backtick or `$(...)` in a new field is live command substitution, not
     inert text; under this script's `set -e` a failed substitution aborts
     the `SENTINEL_INVENTORY=(` assignment itself, breaking `install.sh` for
     every user on every run. Write both new descriptions in the same
     backtick-free plain style as the existing 12 rows:
     - `.error-mode-nudge-enabled`: "Nudges you to run /error-mode-analysis
       after a repeated-failure sequence in a session, so a stuck debugging
       loop gets flagged instead of continuing silently. See
       docs/error-mode-nudge.md."
     - `.cost-ledger-enabled`: "Lets cost-ledger --record append this
       repo's weekly cost/efficiency figures to docs/cost-ledger.md —
       durable once the source transcripts age out and get deleted. See
       docs/cost-ledger.md."
   - Add the criterion (verbatim block above, condensed to match the
     existing comment's terse style) to the schema comment block preceding
     `SENTINEL_INVENTORY=(` (`install.sh:379-397`).
   - **Reuse, no new code path:** `configure_machine_level_opt_ins` and
     `_prompt_sentinel_opt_in` already iterate any row whose `scope` equals
     `machine-promptable` generically (`install.sh:354-373`, confirmed
     scope-generic — no per-sentinel-name branching exists to update).

2. **`README.md`** — add two new `###` subsections under `## Configuration`,
   inserted after `### Permission-prompt tracking` (ends ~line 358) and
   before `### Repo relocation` (~line 360). The 3 existing promptable
   sections are not a single uniform shape — only "Autonomous shipping"
   (`README.md:320-336`) carries the "`./install.sh` now offers this
   interactively on every run — the snippet below is the non-interactive/
   scripted alternative" disclaimer sentence; "Worktree enforcement" and
   "Permission-prompt tracking" omit it. Model both new sections on
   **"Autonomous shipping"'s full shape** specifically (intro paragraph
   naming the gated hook and its effect → the disclaimer sentence → fenced
   `touch ~/.claude/<sentinel>` snippet → doc-link sentence) — it's the
   closest precedent for a sentinel newly promoted from report-only to
   promptable, which is exactly this plan's situation for both rows.

3. **`claude/.claude/hooks/tests/test_install_sh_sentinel_inventory.py`** —
   no structural change required for the array-shape checks (confirmed:
   `TestSentinelInventoryArray`'s coverage is scope-generic, not
   per-sentinel-name; it will pick up both reclassified rows automatically
   once their `prompt-description` field is populated). **Add one new test
   asserting the real `SENTINEL_INVENTORY` block sources with empty
   stderr** — extend `test_nonzero_entry_count`'s sourcing approach (or add
   a sibling test) to assert `result.stderr == ""` in addition to the
   existing `returncode`/field-shape checks. This is a required addition,
   not optional: none of the existing tests assert on stderr for this
   block, which is exactly the gap that would have let a backtick-induced
   command-substitution failure ship silently — the field-count and
   whitespace checks all still pass even when a field's content triggered
   a failed substitution, since bash still populates the array (with the
   substitution's, here empty, stdout spliced in) before continuing.

4. **`claude/.claude/hooks/tests/test_install_sh_machine_level_opt_ins.py`**
   — add two `TestRealSentinelPaths`-style pinning tests (one per
   newly-promptable sentinel), matching the precedent already set by that
   class's existing two tests, `test_worktree_required_sentinel_created_on_y`
   and `test_autonomous_shipping_required_sentinel_created_on_y`
   (`test_install_sh_machine_level_opt_ins.py:231-251` — not
   `pr-cost-disclosure`, which is `account`-scope and never routed through
   `_prompt_sentinel_opt_in`, so it has no test in this class). Those two
   existing tests call `_prompt_sentinel_opt_in` directly with a synthetic
   `description` argument, bypassing the real array's field-3 text
   entirely. Match that same direct-call shape for the two new pinning
   tests, but pass each row's *real* field-3 text (via a new
   `_real_prompt_description(path_template)` helper that parses it out of
   the `SENTINEL_INVENTORY` block, same marker-delimited extraction
   convention as the sibling test file) rather than a synthetic
   placeholder — this is what actually exercises the real description text
   through the real prompting function, since the full interactive-loop
   test below turned out to be infeasible.

   The plan-review round's [FYI] suggestion of an additional end-to-end
   test — sourcing the real array and driving
   `configure_machine_level_opt_ins`'s real interactive loop with all 5
   `machine-promptable` rows answered via stdin — turned out to be
   infeasible with this suite's existing test harness, discovered during
   implementation: `configure_machine_level_opt_ins`'s `[ -t 0 ]` TTY gate
   always short-circuits under a `subprocess.run(input=...)` pipe (verified
   empirically — piped stdin never registers as a TTY), and no test in this
   suite allocates a pty. This doesn't reopen the gap the suggestion was
   meant to close: that gap (a field-3 value breaking the array's own
   sourcing via command substitution) is fully closed by the stderr-safety
   assertion in `test_nonzero_entry_count` (Critical files item 3) — that
   check fires at array-sourcing time, before any prompting logic runs,
   independent of TTY state.

5. **`docs/error-mode-nudge.md`**, **`docs/cost-ledger.md`** — read-only;
   both already document how to enable the sentinel and don't need content
   changes for this reclassification.

## Verification

- `pytest claude/.claude/hooks/tests/test_install_sh_sentinel_inventory.py claude/.claude/hooks/tests/test_install_sh_machine_level_opt_ins.py -q` —
  confirms the schema check picks up both reclassified rows, the extended
  `test_nonzero_entry_count` stderr assertion passes (proving the new
  descriptions are command-substitution-safe), and the two new
  real-description pinning tests pass.
- Full suite: `pytest claude/.claude/ -q` (run from `../../../.venv/bin/pytest`
  per this repo's worktree convention).
- `ruff check claude/.claude/`.
- `scripts/list-shell-files.sh | xargs -0 shellcheck` (covers `install.sh`).
- Manual read-through of the two new README sections against the existing
  three, for wording/structure consistency.

## Out of scope

- Reclassifying any of the 3 existing `machine-promptable` sentinels
  (including the shape-mismatch noted above for `track-permission-prompts`)
  — explicitly excluded by the user's chosen audit scope.
- `pr-cost-disclosure` / `account`-scope promptability — already settled by
  a prior, unrelated design decision (content-based state, not boolean).
- Any `repo`-scope sentinel's promptability — `install.sh`'s prompting path
  is confined to `$HOME/.claude/`-rooted paths by design
  (`.claude/plans/pr-cost-disclosure.md:545-548`'s explicit exclusion).
- Behavioral changes to any of the 4 kill-switch hooks themselves (only
  their sentinel's prompt-vs-report classification is in scope, unchanged
  here).
