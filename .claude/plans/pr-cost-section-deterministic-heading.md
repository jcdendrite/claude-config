# PR cost section: script-produced heading and trailer, not prose-composed

## Context

Stop the `## Cost (list-price estimate)` PR-body section from intermittently
rendering without its heading and its "Exact command that produced this"
reproducibility trailer. Comparing a malformed instance
(https://github.com/jcdendrite/claude-config/pull/893, still open) against a
correctly-rendered one
(https://github.com/jcdendrite/claude-config/pull/874, merged) shows the same
generator producing two different results: in #874, `<!-- pr-cost:start -->`
is immediately followed by `## Cost (list-price estimate)`, and the block
closes with `Exact command that produced this: `~/.claude/scripts/pr-cost-section.sh``
before `<!-- pr-cost:end -->`; in #893, both are simply absent — the alert and
tables sit directly between the two delimiter comments with nothing else.
`claude/.claude/scripts/pr-cost-section.sh`'s stdout is only ever the raw
`transcript-analysis.py cost --summary` output (alert + tables); the heading,
the delimiters, and the trailer line are not produced by any script — they
are prose instructions in `claude-skills/skills/pr-description/SKILL.md`'s
"Cost section" that the authoring model re-composes from memory every time it
drafts or syncs a PR body, and that composition step is exactly what a prior
plan (`pr-cost-block-render-and-caveat-alert`, merged as PR #881) flagged as
untestable: "the embedding step is executed by a model reading SKILL.md, not
by a script. No unit test in this repo can reach it." #893 was authored after
that PR merged, so the render-only fix in #881 (no code fence) did not close this
gap. The intended outcome is for `pr-cost-section.sh`'s stdout on exit 0 to
already be the complete, byte-exact section — delimiters, heading, tables,
and trailer included — so the authoring model's only remaining job is to
embed that stdout verbatim, with no boilerplate left to recompose or drop.

## Approach

Move the five boilerplate lines the model composes today — the two delimiter comments, the `## Cost (list-price estimate)` heading, and the reproducibility trailer — out of `pr-description/SKILL.md`'s prose and into `pr-cost-section.sh`'s exit-0 `printf`, so the script's stdout on exit 0 is the finished block and the model's only remaining act is a verbatim paste. `pr-description`'s Cost section shrinks to "call the script, embed its stdout, add nothing"; its `## Checks` lift-out rule is rekeyed on the delimiter pair rather than the heading, and its no-code-fence check is untouched. The enforcing test is the exit-0 stdout equality assertion already present in three classes of `test_pr_cost_section.py`, widened from the raw report to the whole block — which is what makes this defect testable at all, closing the gap the prior plan's G1 named as beyond any test in this repo.

**This reverses a decision the prior plan made explicitly, and that is the load-bearing judgment here.** `pr-cost-block-render-and-caveat-alert.md`'s Out of scope contains: *"Moving the `<!-- pr-cost:start -->` / `<!-- pr-cost:end -->` delimiters or the `## Cost (list-price estimate)` heading into `pr-cost-section.sh`. It would not prevent fencing — a model can fence a self-contained span just as easily — while expanding the wrapper's contract and churning its behavioral suite."* That reasoning is correct about fencing and silent about omission. #893 is not a fencing defect; the heading and trailer are simply gone. A self-contained span cannot be *partially* dropped the way a five-part recomposition can, so the rejection's premise does not transfer, and the two costs it named both invert here: the wrapper's contract expanding is the point, and churning its behavioral suite is how the fix becomes enforceable. The prior plan's other rejection — a PreToolUse hook on `gh pr edit --body-file` — is untouched and still stands.

Alternatives weighed and set aside: a `--patch-body` mode that splices the block into a body file (declined by the engineer in Step 4, and it would give the wrapper write access to a file it never touches today); keeping the boilerplate in prose but adding a `## Checks` bullet that re-reads for a missing heading (a second prose layer over a prose defect — the compounding-layers tell, and #881 already spent the one useful prose bullet); and emitting the heading from `cost.py --summary` itself (rejected — `--summary`'s no-`## `-H2 invariant is pinned by test, and the delimiters and trailer are PR-body concerns that have no business in a cost renderer).

### Assumption ledger

**Root problem.** The `## Cost (list-price estimate)` block's delimiters, heading, and reproducibility trailer exist only as prose instructions in `pr-description/SKILL.md`, recomposed from memory by the authoring model on every draft and every sync; under load that recomposition silently drops parts of itself, and nothing downstream re-reads the body to notice.

**Givens** (fixed beyond this plan's reach):

- **G1.** GitHub owns GFM's block grammar — table termination, HTML-comment block boundaries, alert nesting. This repo can only arrange bytes to satisfy it. `[verified: same constraint the prior plan recorded as its own G2, quoting docs.github.com]`

Whether the final embed into the PR body stays a model action, with no script in this repo writing a PR body, is **not** listed as a given: a `--patch-body` splice mode could dissolve it, that mode was in reach, and the engineer declined it in Step 4 for reasons unrelated to feasibility — see Out of scope's first bullet and row18, which already carry this fact with the `[engineer-verified]` tag it actually needs. Recording it a second time as a "beyond this plan's reach" given would misstate a chosen scope boundary as a structural one.

**Assumptions:**

- **row1.** The body of #893 has the alert and tables sitting directly between the two delimiter comments with no heading and no trailer; the body of #874 has both, with `<!-- pr-cost:start -->` immediately followed by `## Cost (list-price estimate)` and the trailer immediately before `<!-- pr-cost:end -->`. `[verified: gh pr view --json body of both PRs, Step 3]`
- **row2.** #893 was authored after #881 merged, so the render-only fix in #881 (no code fence) did not close this gap and this is not a regression from it. `[verified: Step 3]`
- **row3.** The prior plan's rejection of this exact move was argued only against fencing, not against omission. `[verified: .claude/plans/pr-cost-block-render-and-caveat-alert.md, Out of scope, "Moving the … delimiters or the … heading into pr-cost-section.sh"]`
- **row4.** `pr-cost-section.sh` adds nothing to `--summary`'s stdout today. `[verified: pr-cost-section.sh:45 is a bare printf '%s\n' "$cost_output"]`
- **row5.** `$(...)` strips trailing newlines, so `$cost_output` never ends with one and the wrapper's own trailing structure is deterministic regardless of what the report's tail looks like. `[verified: pr-cost-section.sh:39 captures via $(); POSIX command-substitution rule]`
- **row6.** `--summary` emits no `## `-prefixed line, so the wrapper's H2 cannot collide with a heading inside the report. `[verified: test_transcript_cost.py:2499 asserts not any(line.startswith("## ") for line in lines)]`
- **row7.** `--summary`'s first line is `> [!IMPORTANT]` with no leading blank line, so the wrapper's `heading\n\n` supplies exactly one separator and never two. `[verified: cost.py:814 prints _LIST_PRICE_CAVEAT_ALERT first; test_transcript_cost.py:2492 pins lines[0]]`
- **row8.** GFM breaks a table only at an empty line or the start of another block-level structure, so a trailer paragraph placed directly under the last `| subagent | … |` row would be parsed as one more table row rather than as a paragraph. This is what makes the blank line before the trailer load-bearing rather than cosmetic. `[unverified — quoted from the GFM tables extension from recall; this dispatch has no fetch tool. Verification step 5c is what settles it, on the rendered page.]`
- **row9.** An HTML comment is a CommonMark HTML block that ends on the line carrying `-->`, so `## Cost (list-price estimate)` on the immediately following line parses as a heading with no blank line between. #874 renders correctly with whatever placement it actually has, which is why Verification step 2 byte-compares against it before the shape is frozen. `[unverified — same source and same settling check as row8]`
- **row10.** Every literal moved into the script is pinned byte-exactly by the three existing exit-0 stdout equality assertions, so this convention's enforcing test ships in the same PR. `[verified: test_pr_cost_section.py:199, :215, :315]`
- **row11.** `test_skills.py`'s wiring class needs no new assertion. Its four existing tests all stay true and still matter (the body still names the script, both delimiters, the heading, and the raw-markdown rule), and its own docstring already routes runtime behavior to `test_pr_cost_section.py`. `[verified: test_skills.py:929-965]`
- **row12.** The `## Cost section` H2 string itself must not be renamed: `docs/transcript-analysis.md:598` cites it by exact heading, and `test_skills.py:1005` indexes the body on that literal for its section-ordering assertion. Only the section's body content changes. `[verified: both lines]`
- **row13.** Every file this plan touches is already mapped by `select-tests.py` — no rule-table gap to report. `[verified: select-tests.py:352 SCRIPTS_DIR→SCRIPTS_TESTS_DIR; :353 SKILL.md→SKILLS_TESTS_DIR; :456 _is_scripts_dir_shell_script_change→(HOOKS_TESTS_DIR, SKILLS_TESTS_DIR); :449 _is_hooks_or_skills_change→TRANSCRIPT_ANALYSIS_TEST_GLOB; :475 py-source→TICKET_REFERENCE_DISCIPLINE_TEST_PATH; :476 test-source→SELECT_TESTS_TEST_PATH; :357 PLANS_DIR→(); :358 CHANGELOG_MD→()]`
- **row14.** Neither `docs/transcript-analysis.md` nor `docs/hooks.md` documents the wrapper's stdout shape, so neither needs an edit. The `--summary` flag bullet and its sample block document `--summary`'s own raw output, which does not change; `docs/hooks.md`'s entry documents the sentinel's mode grammar, also unchanged. `[verified: docs/transcript-analysis.md:598 and :610-644; docs/hooks.md:71-77]`
- **row15.** `cost.py:810-811`'s comment ("pr-cost-section.sh embeds this stdout verbatim, with no separator of its own") states a reason that becomes false. The fact it protects — no leading blank line — stays true, for a new reason. `[verified: cost.py:809-814]`
- **row16.** `test_transcript_cost.py:2473-2474` attributes the `## Cost (list-price estimate)` wrapper to `pr-description`. Its assertion stays valid; only the attribution goes stale. `[verified: test_transcript_cost.py:2468-2499]`
- **row17.** The trailer must carry `~/.claude/scripts/pr-cost-section.sh` as a literal string and must never be derived from `$0`. Under a non-default `CLAUDE_CONFIG_DIR` or when run from a linked worktree, `$0` resolves to an account-scoped or worktree-local absolute path, and this block lands in a public PR body. `[verified: pr-cost-section.sh:13 and :39 both resolve siblings via $(dirname "$0"); CLAUDE.md's redaction rules bar publishing non-home-rooted paths]`
- **row18.** Locating and removing a stale block during a sync stays a model-executed text search — no PR-body parser exists and none is being built here. `[engineer-verified]`
- **row19.** Resyncing the live body of PR #893 is a user follow-up, not a deliverable of this plan. `[engineer-verified]`
- **row20.** `pr-description/SKILL.md` is 206 lines and this edit is net-negative, so no length gate is implicated. `[verified: full read of the file, last line 206]`

**Mechanisms:**

- **M1 — `pr-cost-section.sh` emits the complete delimited block on exit 0.** `anchors: root, row1, row3, row4`. This is the *lightest* primitive available, not a heavier one: five literal lines move from prose into a `printf` inside the same script the model already calls, and nothing about the script's capability surface changes — same sentinel gate, same `git rev-parse`, same downstream call, same exit codes 1/2/3 with no stdout, same single call site. The two heavier mechanisms considered and rejected: (a) a `--patch-body` mode that finds and replaces the delimited span in a body file, which would hand a read-only reporter write access to a file it never touches and was declined in Step 4; (b) a PreToolUse hook on `gh pr create` / `gh pr edit --body-file` validating the block's shape — the prior plan's M2 rejection, which still holds unchanged: an always-on privileged execution context shipped to every stow user, which must fail open on any parse ambiguity or block PR creation outright, bought for a formatting outcome.

- **M2 — `printf` with the report passed as an argument, never a heredoc.** `anchors: M1, row17`. `printf` interprets format specifiers only in its format string, so a report body containing `%`, `$`, a backtick, or a backslash passes through `%s` untouched. An unquoted heredoc would command-substitute the backticks in the trailer and expand `$12.34`-shaped tokens in the report; `<<'EOF'` would fix that but then cannot interpolate `$cost_output` at all. One `printf` call with five arguments and five specifiers is the whole mechanism.

- **M3 — one blank line before the trailer, none before the heading.** `anchors: G1, row8, row9`. The blank line before the trailer is required by GFM table termination; its absence turns the reproducibility line into a phantom row of the "Cost by thread" table. The absence of one after `<!-- pr-cost:start -->` follows from HTML-block termination. Both are settled empirically by the byte-compare against #874 and the rendered-page check, not by this plan's recall of the spec.

- **M4 — `pr-description/SKILL.md`'s Cost section becomes call-then-embed.** `anchors: root, M1`. The exit-0 sentence stops naming a heading to type and a trailer to compose, and states instead that stdout is already the finished block. Exits 1/2/3 stop naming the heading and refer to "the block." The heading literal survives in exactly one descriptive place — enough for a reader to recognize what the block renders as, and enough to keep `test_declares_cost_heading_literal` meaningful — while every *instruction* to type it is gone.

- **M5 — the `## Checks` lift-out rule rekeys on the delimiter pair and states delete-and-replace.** `anchors: row18, M4`. The delimiters are the mechanical locator and always were; naming the heading there was redundant. The rule gains the one sentence that was previously left to inference: delete the lifted span outright, put the current run's stdout in its place. The neighbouring no-code-fence bullet is unchanged — a model can still fence a self-contained span, so the check #881 added keeps earning its place.

- **M6 — the exit-0 stdout equality assertions widen to the whole block; `test_skills.py` gets no new assertion.** `anchors: row10, row11`. Three classes already compare stdout for byte equality, which is the strongest available pin and now covers every literal the model used to type. Deliberately *not* added: a source-text pin in `test_skills.py` asserting the new prose ("add nothing," "already carries," a heading-occurrence count). Each of those is a proxy that fails in both directions — it over-rejects a legitimate reword and under-detects a single mention still phrased as an instruction — and stacking one on top of a behavioral pin that already covers the same fact is the compounding-layers tell. The convention-gets-its-test-in-the-same-PR rule is satisfied by the byte pin.

- **M7 — one new test class pinning metacharacter survival.** `anchors: M2`. A fake report line carrying `$`, a backtick, `%s`, and a backslash must survive byte-identically inside the block. This is the only assertion that distinguishes the chosen `printf`-argument form from the two forms an implementer would plausibly reach for instead, and no existing test would fail if the wrapper were rewritten as a heredoc.

- **M8 — two comment/docstring accuracy touches, in the same commit as the change that invalidates them.** `anchors: row15, row16`. Both describe how the code currently behaves rather than recording an event, so they are in scope under CLAUDE.md's records-vs-descriptions test. Landing them separately would let a `git revert` of this change restore a comment that is wrong about the reverted state.

- **M9 — one `CHANGELOG.md` entry under `[Unreleased]` → `### Fixed`.** `anchors: root`. `claude/.claude/**` goes live on `git pull` for every stow consumer, and a consumer with the sentinel set to `dollars` gets a behavior change on their next `/pr-description` run.

## Critical files

**Dispatch split: one `code-writer` dispatch, not two.** The coupling fact is a single byte sequence that appears in the script, in the test's expected constant, and by reference in the skill body — split across two prompts, each agent would re-derive the blank-line placement independently and neither one's self-review would see the other's. Seven files, well under a hundred lines. Verification command for that dispatch: `.venv/bin/python3 claude/.claude/scripts/select-tests.py`, `.venv/bin/ruff check claude/.claude/ claude-skills/`, and `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck`.

After it returns, the **parent** runs `/code-review` — it dispatches `/skill-review`, whose marker is hook-enforced at `git commit` for any staged `SKILL.md` (`.claude/rules/review-pipeline-dispatch.md`), and `code-writer` cannot run review skills.

**Reuse:** no new helper is warranted anywhere in this diff. The script reuses its existing `_lib_config_dir` gate and its existing `$cost_output` capture; the tests reuse `script_fixture`, `_run_script`, `_write_sentinel`, `_make_repo_with_remote`, and `_base_test_env`; `test_skills.py` reuses `_body()`.

Modify:

1. **`claude/.claude/scripts/pr-cost-section.sh`** — two sites.

   Header comment, exit-0 line (lines 4-5):

   ```
   # Exit 0: sentinel enabled and HEAD resolves to a branch -- the complete cost
   #         block (delimiters, heading, transcript-analysis.py cost --summary
   #         report, reproducibility trailer) is printed to stdout for verbatim
   #         embedding in a PR body.
   ```

   Line 45, replacing `printf '%s\n' "$cost_output"`:

   ```bash
   # The trailer names the stow install path as a literal; "$0" would publish a
   # worktree-local or account-scoped absolute path into a public PR body.
   # The blank line before the trailer is load-bearing: GFM ends a table at an
   # empty line, so an adjacent paragraph parses as one more table row.
   printf '%s\n%s\n\n%s\n\n%s\n%s\n' \
     '<!-- pr-cost:start -->' \
     '## Cost (list-price estimate)' \
     "$cost_output" \
     'Exact command that produced this: `~/.claude/scripts/pr-cost-section.sh`' \
     '<!-- pr-cost:end -->'
   ```

   Five specifiers, five arguments — do not let the count drift, since `printf` silently reuses its format string on a sixth argument. Single-quote every literal: the trailer's backticks and the delimiters' `!` must not reach the shell. Nothing above line 45 changes; the sentinel gate, the detached-HEAD check, the `2>/dev/null` redirect, and exits 1/2/3 are all untouched.

2. **`claude/.claude/scripts/tests/test_pr_cost_section.py`** — the enforcement point.

   Add one module-level constant after `_LIB_SH` (line 27):

   ```python
   # The complete block pr-cost-section.sh emits on exit 0. Written out as a
   # literal rather than composed from parts, because composing it would
   # re-implement the script's own layout and pass on a wrong shape. The two
   # interior report lines come from _fake_transcript_analysis_source().
   _EXPECTED_COST_BLOCK = (
       "<!-- pr-cost:start -->\n"
       "## Cost (list-price estimate)\n"
       "\n"
       "ARGS: cost --this-repo --branches main --summary\n"
       "total: $12.34\n"
       "\n"
       "Exact command that produced this: `~/.claude/scripts/pr-cost-section.sh`\n"
       "<!-- pr-cost:end -->\n"
   )
   ```

   Replace the stdout assertion in all three exit-0 classes with `assert result.stdout == _EXPECTED_COST_BLOCK`:
   - `TestSentinelEnabledBranchResolves::test_prints_cost_report_verbatim_and_exit_zero` (line 199) — rename to `test_prints_the_complete_cost_block_and_exit_zero`, since "verbatim" now describes what the *caller* does with the stdout, not what the script does with the report.
   - `TestSentinelMixedCase::test_uppercase_sentinel_still_enables` (line 215) — assertion only.
   - `TestStderrDiagnosticsDiscardedOnSuccess::test_stdout_is_exact_cost_body_and_stderr_omits_child_diagnostics` (line 315) — assertion only; its `"STDERR-MARKER" not in result.stderr` line is unchanged.

   Amend `_fake_transcript_analysis_source`'s docstring (lines 31-33): the fixed body is what `_EXPECTED_COST_BLOCK` wraps, not what the exit-0 case asserts directly.

   Add one new class and its fixture:

   ```python
   class TestCostBodyWithShellMetacharacters:
       """A report line carrying $, a backtick, a printf specifier, and a
       backslash survives byte-identically: the wrapper passes the report as a
       printf argument, never as its format string or an unquoted heredoc
       body."""
   ```

   Its fake prints one such line (use a raw string for the fake's source so the backslash reaches the generated file intact — e.g. a body line containing `` $HOME `date` %s 50% C:\path ``). Build a second literal constant, `_EXPECTED_COST_BLOCK_WITH_METACHARACTERS` (same five-part shape as `_EXPECTED_COST_BLOCK`, with the metacharacter line substituted for the fake's two-line body), and assert `result.returncode == 0` and `result.stdout == _EXPECTED_COST_BLOCK_WITH_METACHARACTERS` — full equality, matching the three sibling exit-0 classes, not a weaker `in`/`startswith`/`endswith` check. A weaker check would still pass on a block that appeared twice (the exact "`printf` reuses its format string on a sixth argument" duplication footgun this class exists to guard against) or on a missing/extra blank line adjacent to the metacharacter body. Model the fixture on `stderr_diagnostics_script_fixture` (lines 154-175) — same five-step layout, different fake source.

   Add one more fixture and test — fold it into the class above as a second method with its own fixture, or a sibling class (`TestCostBodyEndsWithTableRow`): a fake whose report body's final line is table-row-shaped (e.g. `| subagent | 2.82 | 43.5% |`), asserting full `result.stdout` equality against a third literal constant built the same way. No existing or otherwise-proposed fixture ends its fake report body with anything table-row-shaped, so nothing in this suite automatically pins M3's central invariant — the blank line before the trailer, required by GFM table termination so the trailer paragraph doesn't parse as a phantom row of the preceding table — leaving it to rely solely on Verification step 5's one-time manual render check. This fixture converts that byte-layout half of the invariant to automated, every-run coverage; it does not replace step 5's real GFM-render check, since no markdown parser runs in this test.

   Unchanged: `TestSentinelAbsent`, `TestSentinelWrongValue`, `TestSentinelEnabledDetachedHead`, `TestDownstreamCostCallFails`, `TestDownstreamCostCallFailsAfterPartialOutput`, `TestSentinelBlankLineThenDollars`. All assert `result.stdout == ""`, which the change cannot affect — the `printf` is unreachable on every one of those paths.

3. **`claude-skills/skills/pr-description/SKILL.md`** — three sites, all inside the existing `## Cost section` / `## Prose tightening pass` / `## Checks` headings. **Do not rename `## Cost section`** (row12).

   Replace lines 76-85's exit-0 sentence and its exit-1/2/3 clauses with:

   > Exit 0: enabled and the branch resolved cleanly — stdout is the finished
   > `## Cost (list-price estimate)` block, with its delimiters, heading, tables,
   > and reproducibility trailer already in it. Embed that stdout **verbatim** and
   > add nothing around it: no heading, no trailer, and no delimiters of your own.
   > Never recompose, round, or re-narrate the figures. Embed it as raw markdown,
   > never inside a code fence. A fenced block renders its GFM alert and its
   > `### Cost by …` tables as literal text instead of a callout and tables.
   > Exit 1: disabled, unreadable, or malformed `<config-dir>/pr-cost-disclosure`
   > — delete the block if one exists, no stdout. Exit 2: enabled but the branch is
   > the literal `HEAD` (detached) — omit the block and say why, no stdout. Exit 3:
   > branch resolved but the downstream cost report itself failed — omit the block
   > and note in the body that the report failed to generate, unlike exit 1's
   > silent deletion.

   Everything from "The sentinel check (`<config-dir>/pr-cost-disclosure`…" to the end of the section (lines 85-95) is unchanged, as is the opening paragraph at line 68 and the fenced script call at 72-74. "raw markdown" and "code fence" both survive, so `test_declares_raw_markdown_not_code_fence` stays green.

   Line 99 (Prose tightening pass): `` leaving the `## Cost (list-price estimate)` / `## Deferred review findings` blocks and the attribution trailer untouched `` → `` leaving the cost block (`<!-- pr-cost:start -->` / `<!-- pr-cost:end -->`), the `## Deferred review findings` block, and the attribution trailer untouched ``. The rest of that sentence, including "Cost section's gate above", is unchanged — `test_declares_account_scoped_opt_out_sentinel` pins it.

   Lines 112-115 (Checks, machine-managed blocks): `` A `## Cost (list-price estimate)` section (`<!-- pr-cost:start -->` / `<!-- pr-cost:end -->`, "Cost section" above) gets the same lift-out treatment but not the same reinsert rule, stated here rather than left to proximity: it regenerates fresh every sync, never reinserted verbatim. `` → `` The cost block (`<!-- pr-cost:start -->` / `<!-- pr-cost:end -->`, "Cost section" above) gets the same lift-out treatment but not the same reinsert rule, stated here rather than left to proximity: delete the lifted span outright and put the current script run's stdout in its place. ``

   Lines 165-167 (the code-fenced-cost-block check): **unchanged.** Fencing is still a model-reachable defect and the bullet #881 added still covers it.

   Net effect on the heading literal: it appears once, in the Cost section, descriptively. `test_declares_cost_heading_literal` stays green.

4. **`claude-skills/skills/tests/test_skills.py`** — docstring only, no assertion changes.

   `TestPrDescriptionCostSectionWiring`'s closing sentence (lines 941-942) currently reads "This class only proves the delimiters, the script-call wiring, and the raw-markdown embedding rule are present in the skill body's source text." Amend it to name the new division of responsibility: the delimiters, the heading, and the reproducibility trailer are emitted by `pr-cost-section.sh` and pinned byte-exactly by `test_pr_cost_section.py`; this class only proves the skill body still names the script, both delimiters, the heading, and the raw-markdown rule. All four test methods are unchanged.

5. **`claude/.claude/scripts/transcript_analysis/cost.py`** — comment only, lines 810-811.

   ```python
   # No leading blank line: pr-cost-section.sh prints this stdout directly under
   # its own heading, which already supplies the separating blank line.
   ```

   The next two lines (the `Scope:` / lazy-continuation fact) are unchanged, as is every `print` in the function. No behavior change anywhere in this file.

6. **`claude/.claude/scripts/tests/test_transcript_cost.py`** — docstring only, lines 2473-2474.

   `"one would collide with pr-description's own '## Cost (list-price estimate)' wrapper"` → name `pr-cost-section.sh` as the wrapper that supplies that heading. The assertion at line 2499 is unchanged and still load-bearing — it is now what keeps `--summary` from colliding with the script's own H2.

7. **`CHANGELOG.md`** — one entry under `[Unreleased]` → `### Fixed` (create the subsection if `[Unreleased]` has only `### Changed`; the file already uses `### Fixed` at line 80). State the two facts a stow consumer needs: `pr-cost-section.sh`'s exit-0 stdout is now the complete `## Cost (list-price estimate)` block rather than the bare report, so the heading and the reproducibility trailer can no longer be dropped by the drafting pass; and any PR body already carrying a malformed block is repaired by its next `/pr-description` sync, with nothing back-filled.

**Not modified, checked rather than assumed:** `docs/transcript-analysis.md` and `docs/hooks.md` (row14 — neither documents the wrapper's stdout), `claude/.claude/scripts/select-tests.py` (row13 — no rule-table gap), `claude-skills/skills/tighten-prose/SKILL.md` (its carve-out at line 50 already keys on the delimiter pair, which is what the script now emits), `install.sh`, `claude/.claude/hooks/_lib.sh`.

## Verification

1. `.venv/bin/python3 claude/.claude/scripts/select-tests.py` — the repo's documented scoped command. Expected selection for this diff: `claude/.claude/scripts/tests/`, `claude-skills/skills/tests/`, and `claude/.claude/hooks/tests/` (the `.sh` change reaches the last two via `_is_scripts_dir_shell_script_change`), plus `test_transcript_analysis*.py`, `test_ticket_reference_discipline.py`, and `test_select_tests.py`. If it selects less than that, report the discrepancy instead of widening by hand. Confirm the branch's merge-base includes `ea64d3e` (GH-882, PR #891) before trusting the run — this diff selects a domain directory *and* files inside it, the exact shape that silently under-collected before that fix.
2. `.venv/bin/ruff check claude/.claude/ claude-skills/` — four Python files changed across both trees.
3. `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck` — required this round; `pr-cost-section.sh` changes. The script comment's own caution against letting the five-specifier/five-argument count drift is a courtesy; ShellCheck's format-string/argument-count rule is the actual enforcement — confirm once, locally, that a deliberately-mismatched argument count on this exact call shape trips a ShellCheck finding, rather than assuming it does.
4. **Byte-compare the emitted block against PR #874 before freezing the shape.** From this worktree: `gh pr view 874 --json body -q .body | sed -n '/pr-cost:start/,/pr-cost:end/p' | cat -A | head -8` (GNU `cat -A`; `-A` is unavailable on BSD). Confirm the blank-line placement the script emits matches the placement in #874 — no blank line between `<!-- pr-cost:start -->` and the heading, one after the heading, one before the trailer, none before `<!-- pr-cost:end -->`. **If #874 differs, adopt the placement from #874 and update `_EXPECTED_COST_BLOCK` to match**: it is the known-good render, and rows 8 and 9 are the only `[unverified]` rows in this plan. The heading text and the trailer wording are fixed either way.
5. **Manual render check — required, since the final embed into the PR body stays a model action beyond any test's reach (Out of scope's first bullet, row18).** Three parts:
   - **Local, pre-PR:** `~/.claude/scripts/pr-cost-section.sh | head -3` prints `<!-- pr-cost:start -->`, then `## Cost (list-price estimate)`, then an empty line; `| tail -2` prints the trailer then `<!-- pr-cost:end -->`. This needs the active account's `<config-dir>/pr-cost-disclosure` to read exactly `dollars`; on exit 1 the block is disabled and this part cannot run — say so rather than reporting it as passed, and rely on the third part.
   - **In the drafted body, pre-sync:** the span between the delimiters appears exactly once, and no line the model authored sits between `<!-- pr-cost:start -->` and `<!-- pr-cost:end -->`.
   - **On the rendered PR page, after `/pr-description` syncs this branch's own body:** this is live-fire, not a dry run — it publishes this branch's real session count, token volumes, and per-model dollar figures to a public page, which is the feature working as designed for an opted-in maintainer on this repo's own PR, and it opens no new path. Confirm (a) exactly one `## Cost (list-price estimate)` H2 renders inside the delimiters, (b) the caveat renders as a bordered callout rather than literal `> [!IMPORTANT]` text, (c) the three `### Cost by …` blocks render as real tables **and the trailer renders as its own paragraph below them, not as a fourth row of the thread table** — this is what settles row8, (d) the line immediately before `<!-- pr-cost:start -->` is not a ``` fence.

## Out of scope

- **A `--patch-body` mode that splices the block into an existing PR body file.** Declined in Step 4. It would give a read-only reporter write access to a file it never touches, and locating the old span stays a model text search either way (row18). `[engineer-verified]`
- **Resyncing the live body of PR #893.** A separate follow-up action for the user, not a plan deliverable. Its block regenerates correctly on its next `/pr-description` sync once this lands. `[engineer-verified]`
- **Any change to `--summary`'s own output contract in `cost.py`.** Its refusals, its scope guarantee, its aggregate-only rendering, and its no-`## `-H2 invariant are all unrelated to this defect and all stay as they are. Only the wrapper around it changes; `cost.py`'s sole edit is a two-line comment.
- **`docs/transcript-analysis.md`'s `--summary` bullet and its sample output block.** Both document `--summary`'s raw stdout, which is unchanged (row14). Regenerating the sample to show the wrapper's block would make the doc describe the wrong producer.
- **The stale citation path at `docs/transcript-analysis.md:598`** — it cites `` `claude/.claude/skills/pr-description/SKILL.md` § "Cost section" ``, but no `claude/.claude/skills/` directory exists; the file is at `claude-skills/skills/pr-description/SKILL.md`. Pre-existing, unrelated to this defect, and unguarded by `test_skill_citations_resolve_to_real_headings` (whose corpus is the skill tree, not `docs/`). **Raised to the reviewer** rather than bundled — the same stale prefix likely appears elsewhere under `docs/`, and auditing that is its own change.
- **A PreToolUse hook validating the block at `gh pr create` / `gh pr edit --body-file`.** The prior plan's M2 rejection stands unchanged: an always-on privileged context shipped to every stow user, which must fail open on parse ambiguity, bought for a formatting outcome.
- **A source-text pin in `test_skills.py` for the new "compose nothing" prose.** Rejected as a bidirectionally-failing proxy stacked on a behavioral pin that already covers the same fact (M6).
- **A test pinning that `$(...)` strips a report's trailing blank lines.** True and load-bearing (row5), but it is a property of POSIX command substitution, not of anything this change introduces — such a test would pin the shell, not the diff.
- **Making the reproducibility trailer's path account-aware.** Under a non-default `CLAUDE_CONFIG_DIR` the literal `~/.claude/scripts/pr-cost-section.sh` is not where the script actually lives. Deriving it from `$0` is barred outright (row17); resolving it from the config dir would publish an account-scoped path just as surely. The literal is what a reader needs to reproduce the figure on a standard stow install, and that is the only audience a public PR body has.
- **Flipping the `pr-cost-disclosure` sentinel's default, or any change to its grammar.** The gate stays opt-in and fail-closed; this plan does not touch lines 15-28 of the script.
- **Back-filling already-merged PR bodies with malformed blocks.** Editing a merged PR's body rewrites a historical record for no functional gain.
