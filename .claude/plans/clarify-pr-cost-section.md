# Clarify the PR cost report's Scope line

## Context

The `cost --summary` block that `pr-cost-section.sh` embeds verbatim into
every PR body (via the `pr-description` skill's Cost section, gated on the
`pr-cost-disclosure` sentinel) currently prints a Scope line that is
confusing to a PR reader with no context on this internal toolkit: `Scope:
this account only, all time (716 transcripts scanned, 5 priced sessions,
1,332 priced turns) — dropping --summary reports every declared account
too` (verbatim from
[PR #924](https://github.com/jcdendrite/claude-config/pull/924)). Confirmed
by reading `claude/.claude/scripts/transcript_analysis/cost.py:804-820`,
`docs/transcript-analysis.md`'s `cost` section, and
`claude/.claude/scripts/tests/test_transcript_cost.py`'s summary-mode
tests, three problems stand out:

1. "Transcript", "priced session", and "priced turn" are internal
   vocabulary never defined anywhere a PR reader would see them.
2. The three counts are jammed into one prose parenthetical even though
   every other structured fact in this same block renders as a GFM table.
3. The trailing clause reads as a broken sentence and gives no PR reader a
   reason to care, since most readers don't run this multi-account setup
   at all. It was added specifically to disclose that `cost --summary`
   narrows to the *active* Claude account only, and that dropping
   `--summary` would union every account declared in
   `~/.claude/transcript-config-dirs` — see
   `test_summary_totals_equal_active_root_only_when_second_account_declared`
   and its sibling
   `test_without_summary_the_same_fixture_still_unions_both_accounts`.

The intended outcome is a Scope block that a stranger unfamiliar with this
toolkit can read and understand unaided, without losing the scope facts
the block exists to disclose.

**Context on the third problem:** a maintainer running several isolated
Claude Code accounts on one machine (see `CLAUDE.local.md`'s "Multi-account
Claude Code setup" for the general mechanism) could encounter this scenario
on `claude-config` specifically, since it is the one repo stowed
identically into every account's config dir — structurally the most
plausible site, of any repo on such a machine, for switching Claude
accounts mid-branch. This is a narrow, low-frequency scenario that should
not drive PR-cost design generally; the question of whether `claude-config`
warrants its own carve-out was routed to `plan-architect` rather than
settled here.

## Clarifying answers (Step 4)

1. **Layout of the three counts:** render as a small GFM table (one row:
   Transcripts scanned | Priced sessions | Priced turns, with an
   Unreadable column/cell included only when nonzero — same
   only-when-nonzero convention the current prose clause already uses).
   The Scope/window text (`this account only, all time`) stays a one-line
   prose lead-in above the table, not a table cell itself.
2. **Where definitions live:** canonical definitions of "transcript",
   "priced session", and "priced turn" go in `docs/transcript-analysis.md`
   (this doc's own audience is the maintainer running the tool directly;
   it currently uses these terms throughout with no single definition
   site). The embedded PR block additionally gets a short inline
   parenthetical gloss next to each term (or column header), since a PR
   reader on GitHub won't go read `docs/transcript-analysis.md` to
   understand three numbers — this is a "place prose where its reader and
   altitude match" split (`CLAUDE.md` §Code Comments, Documentation, and
   Prose), not a duplication: the doc holds the full definition, the
   embedded block holds only the minimal gloss needed to parse the number
   in place.
3. **The trailing "dropping --summary" clause:** the disclosure itself is
   real and mechanism-backed (confirmed via the two tests named above), not
   dead prose — it should not simply vanish. But its current phrasing
   assumes the reader knows `--summary` is a CLI flag and knows what
   "declared account" means. `plan-architect` should design the reworded
   text (or a `claude-config`-specific carve-out, if warranted — see the
   engineer-supplied context above) and justify whichever it picks against
   the goal that a PR reader unfamiliar with this toolkit can parse the
   line unaided, while readers who do run multi-account setups still get
   an honest disclosure that the total may be incomplete.

## Approach

Replace the `Scope:` line's prose parenthetical with a one-line scope caption, a small GFM table carrying the four counts under self-glossing headers, and a reworded completeness sentence below the table — and give `docs/transcript-analysis.md` a canonical `## Terms` section that defines the vocabulary once for the whole doc. **No `claude-config` carve-out**: the reworded sentence prints unconditionally on every stow consumer's machine, and nothing in `cost.py` learns which repo it is running in.

The rendered block becomes (synthetic counts):

```
> [!IMPORTANT]
> Computed locally at API list price — this is a compute estimate, not an invoice, and may not match what your plan or contract actually bills.

Scope: this account only, all time.

| Transcript files scanned | Sessions with priced turns | Priced turns |
|---|---|---|
| 716 | 5 | 1,332 |

Spend recorded under a different Claude account on the same machine is not counted here — ask the author for the full report, which covers every Claude account configured on that machine.
```

With a nonzero unreadable count the table gains a second column, `Of those, unreadable`, keeping today's only-when-nonzero convention.

**Why no carve-out, and why not a conditional either.** Three options were live. (a) A `claude-config`-specific carve-out — rejected on two independent grounds. First, `claude/` installs to every stow consumer (G1), so a repo-keyed branch in `cost.py` would ship one contributor's machine topology as code every consumer runs. Second, encoding "this repo's author switches accounts" into a public repo is a structural fingerprint of the owner's setup, which `CLAUDE.md` § "Also redact structural fingerprints and provenance" bars regardless of whether a name appears. (b) Make the sentence fire only when `~/.claude/transcript-config-dirs` declares more than one root — this is the tempting lighter-looking option and it is actually the heavier one: it makes published PR text depend on machine topology, so its presence (or absence) discloses whether the author runs more than one Claude account. That is a new field on the published path, and the prior plan settled deliberately on "zero new fields on the published path" (`.claude/plans/cost-disclosure-trust.md` row 8's "Why the summary/full split on ID strings"). It also costs a new coupling to `scope.declared_transcript_roots()` and a two-arm test matrix, to buy nothing a static sentence does not already buy. (c) The chosen option — one unconditional sentence. It is honest for the single-account consumer (nothing is being hidden from them; the sentence simply does not apply), honest for the owner's multi-account case, and its uniformity is precisely what keeps the block safe to publish by default: a reader cannot tell one author's account count from another's. G3's engineer instruction ("should not be factored into PR cost" design generally) is satisfied by construction, because the owner's case is covered by text that names no repo and reads no machine state.

**Why a table does not reverse the prior plan's decision.** `.claude/plans/cost-disclosure-trust.md` rows 8–9 folded the transcript/unreadable counts into the scope line as part of pruning three standalone noise lines from `--summary`; the intent was that scan diagnostics stay subordinate to the dollar figures and stop occupying alarm-shaped lines. Both hold here. The counts' membership in the block is unchanged — only their rendering changes, from prose to the same table idiom `### Cost by token class` / `### Cost by model ID` / `### Cost by thread` already use. The table is deliberately given no `###` heading of its own: it stays captioned by the `Scope:` line rather than becoming a fourth peer section, so it reads as metadata about the scan rather than a fourth breakdown of the grand total.

**Root problem.** The `--summary` block publishes four counts and a single-account disclosure in vocabulary defined nowhere the reader can see (`transcript`, `priced session`, `priced turn`) and in a trailing clause that presumes the reader knows `--summary` is a CLI flag — on a surface every stow consumer publishes to strangers on GitHub.

**Givens** (fixed conditions beyond this design's reach):

- **G1.** Everything under `claude/` installs to every stow consumer, so this text's audience is every consumer's PR readers, not this repo's owner — the repo's stow contract fixes that, and no wording choice here narrows it. `[verified: CLAUDE.md § "Working in this repo"; § "Plans in this repo affect all stow users"]`
- **G2.** GitHub renders the embedded block as GFM and terminates a table at a blank line; GitHub owns that renderer, so the block's blank-line adjacency is imposed from outside and can only be respected, not designed around. `[verified: pr-cost-section.sh:49-50 and cost.py:810-813, both of which record this constraint as already-established in this repo]`
- **G3.** A maintainer may run several isolated Claude accounts on one machine and switch accounts mid-branch, but this must not drive PR-cost design generally. `[engineer-verified]`

**Mechanisms.**

1. **The counts move into a GFM table printed by a new `_print_scan_coverage_table` in `cost.py`.** Four `print` calls in the exact idiom of `_print_token_class_table`'s markdown branch (`cost.py:314-323`): header row, `|---|` delimiter row, one data row, every integer `,`-formatted. Takes `(transcripts_scanned, transcripts_unreadable, priced_sessions, priced_turns)` and emits the `Of those, unreadable` column only when `transcripts_unreadable` is nonzero. `anchors: root`
2. **No `markdown: bool` parameter on the new printer.** Its three siblings carry one because the full report needs a plain-text arm; this table has no full-report counterpart — the full report discloses the same facts per root through `cost: account-N: scanned N transcripts, M skipped (unreadable)` (`cost.py:556-557`), which `--summary` prunes. A `markdown=False` branch would be dead on arrival. This is the structural-siblings check reaching the answer "the arms already differ deliberately," not "add the fourth arm." `anchors: row1`
3. **The lead-in keeps the literal prefix `Scope: this account only, {title_since}`, now terminated by a period instead of a parenthetical.** Two live surfaces cite that literal — `claude-skills/skills/transcript-analysis/SKILL.md:24` (`Scope: this account only (...)`, itself pinned verbatim by `test_transcript_analysis.py:18121-18126`) and `docs/transcript-analysis.md:40` ("states its single-account scope on its own `Scope:` line instead"). Keeping the prefix stable keeps both accurate and keeps a hook-enforced `/skill-review` round out of this change. `anchors: root` `[verified: transcript-analysis/SKILL.md:24; test_transcript_analysis.py:18121-18126; docs/transcript-analysis.md:40]`
4. **The trailing clause becomes a standalone sentence in a module-level constant beside `_LIST_PRICE_CAVEAT` (`cost.py:27-37`), printed after the table.** Exact text: `Spend recorded under a different Claude account on the same machine is not counted here — ask the author for the full report, which covers every Claude account configured on that machine.` It names no flag, so it stays true for a reader whose printed command is `~/.claude/scripts/pr-cost-section.sh` — which cannot have `--summary` dropped from it, the false premise in today's wording. It reuses "the full report" and the "ask the author" register the `EXCLUDED SPEND` summary banner already established (`cost.py:377-382`). It avoids "on this branch," which would be wrong for a hand-run `cost --summary` with no `--branches`. The disclosure it must preserve is mechanism-backed and unchanged: `--summary` narrows to the active root while the same run without it unions every declared root. `anchors: root, G3` `[verified: test_transcript_cost.py:2269-2296 (both arms); cost.py:377-382]`
5. **Placement, and the blank-line contract.** Order is: caveat alert, blank, `Scope:` caption, blank, table rows, blank, the row-4 sentence. The blank line before the header row is mandatory — a table opening on the line directly under a paragraph line is not a shape this repo has verified renders, and a preceding blank line is unconditionally correct, so the question is dissolved rather than answered. The blank line after the last data row is supplied by the row-4 print's own leading `\n`, which terminates the table per G2. Extend the comment at `cost.py:810-813` to state the table's own blank-line adjacency alongside the alert's; keep it one sentence per fact. `anchors: G2, row1`
6. **`docs/transcript-analysis.md` gets a `## Terms` section immediately after the intro (after line 8, before the `---` at line 9), and it is the only definition site.** The vocabulary is toolkit-wide, not `cost`-specific — `transcript`, `turn`, and `unpriced turns` appear under `audit-routing`, `cost-trend`, and `review-round-cost` too — so a glossary buried in the `cost` section would be the wrong altitude. Six entries, one per bullet:
   - **transcript** — one session log file under `<config-dir>/projects/<project-dir>/*.jsonl`. A session's subagent records are merged into their parent's file by `read_session_file`, never counted as their own transcript.
   - **transcripts scanned** — files matched by the run's `--projects`/`--this-repo` scope, counted before any `--branches`/`--since` record filter narrows what is priced. It is legitimately far larger than the priced counts.
   - **unreadable** — the subset of scanned transcripts that failed an open probe. A subset, never an addition.
   - **turn** — one assistant reply record.
   - **priced turn** — a turn whose `message.model` has a pricing-table rate and survives the run's record filters.
   - **priced session** — a transcript with at least one priced turn. Always ≤ transcripts scanned.

   Use the doc's existing `<config-dir>` placeholder form (line 3), never a home-rooted literal. The `--summary` flag entry at line 601 then cites `see "Terms" above` instead of restating. `anchors: root` `[verified: scope.py:598-611 (scanned/skipped are one file list and its failed-open subset); scope.py:254-258 (session iteration is the same non-recursive `*.jsonl` glob, so priced sessions ⊆ scanned); cost.py:536-559 (the scan is filesystem-only, never branch-filtered)]`
7. **Column headers do the in-place glossing; no parentheses, no footnote line.** `Transcript files scanned` / `Of those, unreadable` / `Sessions with priced turns` / `Priced turns`. Each keeps the canonical noun the doc defines (one term per concept holds between the PR block and the doc) while adding the one word a stranger needs — "files" says a transcript is a file, "Of those" says unreadable is a subset, "Sessions with priced turns" defines a priced session in place. "Priced" needs no further gloss: the `> [!IMPORTANT]` alert directly above already says "at API list price." Parenthetical glosses inside four header cells were rejected as making the numeric row unreadable at PR-body width. `anchors: row1, row6`
8. **Tests follow the strings, and one existing coverage gap closes.** `test_transcript_cost.py:2196-2197`'s `"1 priced sessions"` / `"1 priced turns"` substring asserts become `_md_table_cols` parses. `test_summary_scope_line_states_single_account_and_that_dropping_flag_widens_it` (`:2298-2323`) is renamed and rewritten to pin row 4's sentence and the caption, and its docstring's "dropping `--summary` from the printed command" framing is dropped — the printed command a PR reader sees is the wrapper, so that framing described the maintainer's invocation, not the reader's. Two new tests cover the `Of those, unreadable` column's present/absent arms; summary-mode unreadable counts have **no** test today, so this is new coverage, not a port. New assertions read the table through `_md_table_cols`, and the row-4 sentence is asserted as a string literal, never as `_mod._SINGLE_ACCOUNT_SCOPE_NOTE` — importing the constant would make the assertion pass under any rewording. `anchors: row1, row3, row4, row7` `[verified: no unreadable assertion exists in any summary-mode test in test_transcript_cost.py]`
9. **Two comments in `cost.py` name today's rendering and go stale.** `:532-535` ("Folded into `--summary`'s scope line as a conditional clause") becomes a conditional *column* in the scan-coverage table; `:571` ("Its own scope line below reports `total_transcripts_scanned` instead") becomes the scope block. Both are one-line factual updates. `anchors: row1`

## Critical files

Paths are repo-root-relative. One `code-writer` dispatch, not split: all four files are coupled through the same exact output strings, and a second agent re-deriving the table's header wording in its own context is exactly the shared-state restatement `plan-it` § Step 5 says not to split on.

**`claude/.claude/scripts/transcript_analysis/cost.py`**
- `:27-37` — add `_SINGLE_ACCOUNT_SCOPE_NOTE` beside `_LIST_PRICE_CAVEAT` / `_LIST_PRICE_CAVEAT_ALERT`, with a one-line comment stating the durable fact (this text is published into every consumer's PR bodies and names no flag, because the command a reader sees is the wrapper script). No PR-defined labels, no "used to be" framing.
- `:310` — add `_print_scan_coverage_table` immediately above `_print_token_class_table`, matching print order. **Reuse:** copy `:314-323`'s markdown-branch idiom literally (header, `|---|` delimiter, `,`-formatted ints); do not invent a second table style.
- `:532-535`, `:571` — comment updates per mechanism 9.
- `:809-820` — the `summary_mode` block. Delete `unreadable_clause` and the three-part `print`; emit the caption (`print(f"\nScope: this account only, {title_since}.\n")`), then the table call, then `print(f"\n{_SINGLE_ACCOUNT_SCOPE_NOTE}")`. Extend the `:810-813` comment per mechanism 5.
- Do **not** touch the `else` arm at `:821-823` or the per-root scan line at `:556-557`.

**`claude/.claude/scripts/tests/test_transcript_cost.py`**
- `:2196-2197` — replace both substring asserts with `_md_table_cols` parses.
- `:2298-2323` — rename and rewrite per mechanism 8.
- Add the two unreadable-column tests to the same summary-mode class. **Reuse:** the `_cost_args(summary=True, this_repo=True)` + `fake_run` git-worktree monkeypatch fixture block (`:2304-2318`) verbatim; `_md_table_cols` (`:46-89`) for every table assertion, with a digit needle as `row_contains` (the `|---|` delimiter row contains no digits, so a digit needle can only match the data row). For the nonzero-unreadable fixture, **do not** reuse `:1589-1598`/`:3160-3168`'s `os.chmod`-on-the-root-directory pattern — those chmod the scan *root* itself, which trips `_scan_root_transcripts`'s whole-root `os.access` check (`scope.py:594-595`) and raises `PermissionError`, a different branch that `cost.py`'s caller (`:549-555`) catches by forcing `scanned, skipped = 0, 0` — the opposite of a nonzero-unreadable, nonzero-scanned fixture. Instead: write one readable session `.jsonl` plus one additional `.jsonl` in the same project dir, then `os.chmod` that **second file itself** (not its parent directory) to `0o000`, guarded by the same `@pytest.mark.skipif(os.geteuid() == 0, ...)` decorator, so `_scan_root_transcripts`'s per-file `open()` probe (`scope.py:604-609`) fails on that one file and increments `skipped` while the root-level `os.access` check still passes. There is no existing precedent for this exact fixture shape in this file — write it fresh.
- Add one further test asserting the row-4 sentence and `Scope:` caption are **byte-identical** between the existing single-root fixture and the existing two-declared-root fixture (`_two_declared_roots_with_this_repo_sessions`, already driving `test_summary_totals_equal_active_root_only_when_second_account_declared` at `:2269` and its sibling at `:2286`) — the design's rejection of a declared-root-count-conditional sentence (Approach, option (b)) is a claim that the text is constant regardless of topology, and that claim needs a test pinning it, not just prose in this plan. Run `cmd_cost(_cost_args(summary=True, this_repo=True, branches="main"))` against both fixtures and assert the two outputs' `Scope:`-caption-through-row-4-sentence span is identical, using string equality (not a substring/contains check) on both spans.
- `_extract_md_grand_total` (`:104-109`) is regex-anchored on `**total**` and is unaffected by a new table; no header needle used elsewhere (`"Class"`, `"Model"`, `"Thread"`) may appear in the new headers.

**`docs/transcript-analysis.md`**
- After `:8`, before the `---` at `:9` — the new `## Terms` section (mechanism 6).
- `:601` — the `--summary` flag entry: replace the sentence describing the folded scope line with the new shape, and cite `see "Terms" above` rather than restating any definition.
- `:613-647` — regenerate the `--summary` sample block and rewrite the trailing `:647` sentence (which describes the `, 1 unreadable` clause) to describe the conditional column. The sample stays **synthetic** and stays labeled as such; do not paste real corpus output into the doc.

**Not edited, checked and deliberately left:** `claude/.claude/scripts/pr-cost-section.sh` (its blank-line-before-trailer contract already holds), `claude-skills/skills/pr-description/SKILL.md` (the verbatim-embedding contract is unchanged), `claude-skills/skills/transcript-analysis/SKILL.md:24` and its pinning test (mechanism 3).

## Verification

Run from the worktree root.

1. `.venv/bin/python3 claude/.claude/scripts/select-tests.py` — the project's documented scoped command (root `CLAUDE.md` § Commands).
2. `.venv/bin/pytest claude/.claude/scripts/tests/test_transcript_cost.py` — **required, not belt-and-braces.** This diff's changed paths make `select-tests.py` emit both `claude/.claude/scripts/tests` (from the `cost.py` domain rule) and `claude/.claude/scripts/tests/test_select_tests.py` (from the `_is_test_source_change` cross-domain exception at `select-tests.py:450-459`, fired by editing a test file under a selectable test directory) — a domain directory plus a file contained inside it, which is the GH-882 under-collection shape where the directory can be silently dropped. `CLAUDE.md` § Commands names "the specific pytest file" as a sanctioned form, so this is not a hand-widened run.
3. `.venv/bin/ruff check claude/.claude/scripts/transcript_analysis/cost.py claude/.claude/scripts/tests/test_transcript_cost.py`
4. **Render check, which no test can perform.** Run `claude/.claude/scripts/transcript-analysis.py cost --this-repo --branches <this branch> --summary` from the worktree (the stow symlink under `~/.claude/scripts/` points at the main checkout, so it would run the old code), and paste stdout into a GitHub markdown preview pane. Confirm: the `> [!IMPORTANT]` block renders as a callout, the scan-coverage table renders as a table (not as pipe-literal text), the row-4 sentence renders as its own paragraph, and `### Cost by token class` still opens a heading. Do not commit that output anywhere — the doc sample stays synthetic.
5. `git grep -n "transcripts scanned"` and `git grep -n "dropping --summary"` — confirm the only remaining hits are prior plan files under `.claude/plans/`, which are historical records and read-only per `CLAUDE.md` § Scope discipline Axis 3.

## Out of scope

- **`--summary`'s single-account narrowing itself.** This plan rewords the disclosure; it does not widen, narrow, or condition what `cost --summary` scans. That behavior is pinned by `test_summary_totals_equal_active_root_only_when_second_account_declared` and `test_without_summary_the_same_fixture_still_unions_both_accounts`, both untouched.
- **Making the multi-account sentence conditional on the declared-root count.** Rejected in Approach (b): it adds a machine-topology-dependent field to the published path, against the prior plan's settled "zero new fields" line.
- **Any `claude-config`-specific carve-out.** Rejected in Approach (a).
- **Editing `claude-skills/skills/transcript-analysis/SKILL.md:24`.** Its `Scope: this account only (...)` citation stays literally true under mechanism 3's preserved prefix; editing it would pull a hook-enforced `/skill-review` round into a change that needs none. If a reviewer disagrees that `(...)` still reads as elision, the follow-up is that skill line plus `test_transcript_analysis.py:18121-18126` together, in its own change.
- **The full (non-`--summary`) report's per-root `cost: account-N: scanned …` line.** It is maintainer-facing terminal output, not published prose, and mechanism 2 depends on it staying as-is.
- **Extracting a shared markdown-table helper across the now-four table printers.** Each is 4–7 lines with a different column shape; a helper would be a four-call-site refactor bundled into a wording change, against `CLAUDE.md` § Scope discipline Axis 4.
- **The rest of `docs/transcript-analysis.md`'s vocabulary.** Mechanism 6 adds the canonical `## Terms` block and rewires only the `--summary` entry to cite it; sweeping every other section to defer to it is a separate, larger DRY pass.
- **A pointer, in the printed PR block itself, to `docs/transcript-analysis.md`'s "disclosed fields are not neutral" caveat.** Raised by `/plan-review`'s CISO pass (FYI, non-blocking): this plan's legibility fix makes the underlying counts easier for a PR stranger to read as an engagement-scale signal, while the doc's own caveat about that signal stays in a file that audience is least likely to have read. Real, but a framing question about the whole disclosure feature rather than this wording fix — a candidate for its own follow-up.
