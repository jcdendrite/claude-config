# Deterministic markdown formatting for the PR-body cost report

## Context

`cost --summary`'s stdout — the block `pr-description`'s `## Cost` section embeds verbatim into every PR body — renders as a fixed-width plain-text table, so the three example PRs the user cited (#672, #671, #670) show three different hand-chosen presentations of the same data: #672 was manually reformatted into clean GitHub-flavored-markdown (GFM) tables, #671 pasted the fixed-width text raw (unreadable outside a monospace font), and #670 wrapped the raw text in a fenced code block (readable but not a real table, and its nested `##`/`###` headers read oddly inside a code fence). This costs both UI quality (two of three renderings are hard to read) and tokens (the agent re-derives a presentation choice on every PR sync instead of the tool doing it once). The fix moves table formatting into `transcript-analysis.py` itself so `--summary`'s stdout is already correct GFM and "embed verbatim" (the skill's existing instruction) stops requiring any agent judgment call.

## Approach

Make `cost --summary`'s three tables (token class, model ID, thread) print as GFM pipe tables instead of fixed-width text, and drop the redundant `## Cost summary (...)` heading that today collides with the `## Cost` heading the `pr-description` skill already supplies around the embedded block. Full-report mode (no `--summary`) is untouched — it has no external wrapper and is read directly in a terminal, where the existing fixed-width format is intended.

**Root problem:** `--summary`'s stdout is not itself valid, readable GFM, so every PR-body embed either violates "verbatim" (to reformat it) or violates "readable" (to embed it as-is). No givens beyond the platform/language boundary (Python's own `print`/f-string formatting, GitHub's GFM renderer) — everything else this plan relies on was checked this session, not assumed.

**Per-mechanism justification (anchors: root):**
- **Chosen: format `--summary`'s table output as GFM inside `_cost_report`/`_print_token_class_table`/`_print_model_id_table`, unconditionally for `summary_mode`.** No flag — `--summary` has exactly one call site in the repo outside its own argparse definition (`pr-description`'s generation command) and zero other consumers — [verified: grep -rn -- "--summary" over claude/.claude/ + docs/] — so a `--format {text,md}` flag (the shape `audit-routing-samples --format {json,md}` uses, `transcript-analysis.py:11131-11134`) would add a permanently-one-valued knob. Two lighter/heavier alternatives considered and rejected:
  - *Add `--format md` mirroring `audit-routing-samples`* — rejected: `audit-routing-samples` genuinely serves two consumers (JSON for tooling, md for human curation, per its own `--format` help text), `--summary` serves one; a flag with one real value in practice is the heavier primitive CLAUDE.md's over-powered-primitive check flags.
  - *Reformat in the `pr-description` skill instead of the script* — rejected: the skill has no deterministic table-formatting logic today (it says "embed verbatim"), so this would mean writing markdown-table-rendering logic in skill prose for an agent to execute freshly every sync — exactly the non-deterministic, token-costly status quo the user is asking to eliminate. `docs/cost-ledger.md`'s `_format_cost_ledger_row` (`transcript-analysis.py:8403`) is the existing precedent for pipe-table rendering living in the script, not the skill.

- **Drop the `## Cost summary ({title_since})` heading in `summary_mode`, keep the text as plain (non-heading) prose.** anchors: root. The heading is redundant once wrapped in the skill's own `## Cost` — PR #672's hand-edit independently converged on removing it. Full-mode's parallel `## Cost report ({title_since})` heading is untouched (it has no external wrapper).

**Assumption ledger:**
- The `pr-description` skill's Cost section prose needs no edit — [verified: claude/.claude/skills/pr-description/SKILL.md:69-95, "embed stdout verbatim under `## Cost`" already holds once stdout is well-formed GFM; nothing in that text assumes plain-text formatting].
- `_print_token_class_table` / `_print_model_id_table` have exactly three call sites, all inside `_cost_report`, all in this file — [verified: grep -n "_print_token_class_table(\|_print_model_id_table(" over transcript-analysis.py + its test file].
- No other script, hook, or doc parses `cost --summary`'s exact stdout shape (heading text or column layout) — [verified: grep -rn "Cost summary (" over claude/ and docs/, only hit outside this plan is a gitignored runtime log].
- `_table_cols`/`_extract_grand_total` (the test file's fixed-width-table parsers) are shared by ~150 call sites across unrelated subcommands — [verified: grep -c "_table_cols(" claude/.claude/scripts/tests/test_transcript_analysis.py → 152 hits]. Reusing them for markdown assertions would risk those other subcommands; new markdown-only parsing helpers avoid that, and test code is DAMP by CLAUDE.md's named exception.
- A model ID containing a literal `|` character would break its pipe-table row — accepted, not fixed: real Anthropic model IDs are plain hyphenated strings, and the existing `_format_cost_ledger_row` pipe-table renderer (`:8403`) doesn't escape `|` either, so this plan doesn't introduce a new class of risk relative to the codebase's current pipe-table convention — [verified: transcript-analysis.py:8403, no delimiter-escaping in `_format_cost_ledger_row`].

## Critical files

- `claude/.claude/scripts/transcript-analysis.py`
  - `_print_token_class_table` (`:5531`) — add `*, markdown: bool = False`; when true, print `### Cost by token class` + a GFM pipe table (header row, `|---|---|---|---|` separator, one row per `_TOKEN_CLASSES` entry, a closing `| **total** | **{grand_total:,.2f}** | | |` row). Plain-text branch unchanged.
  - `_print_model_id_table` (`:5543`) — same `markdown` parameter; GFM table `| Model | $ | Share |`.
  - New `_print_thread_table(main_total, subagent_total, grand_total, *, markdown: bool = False)` — extracts the inline "Cost by thread" print block (`:5969-5972`, currently duplicated nowhere but about to need a markdown branch) into its own function, mirroring the other two table printers. Reuse `_pct_of` (`:5218`), already imported/available in this scope.
  - `_cost_report` (`:5557`) — at `:5929-5937`, drop the `## ` prefix from the summary-mode heading line (keep the `{title_since}` text, since it's the only place that window information appears). At `:5946-5947` and `:5969-5972`, pass `markdown=summary_mode` through to the three print functions above.
  - No change to `cmd_cost`, argument parsing, pricing, or full-report (non-`--summary`) formatting.

- `claude/.claude/scripts/tests/test_transcript_analysis.py`
  - Add two markdown-aware parsing helpers near the existing `_table_cols`/`_extract_grand_total` (`:58`, `:188`): a `_md_table_cols(out, *, header_contains, row_contains, occurrence=None)` that locates a GFM pipe-table's header/data row by the same anchor-to-header-row approach `_table_cols` uses (just splitting on `|` instead of whitespace), and an `_extract_md_grand_total(out)` that reads the `| **total** | **X.XX** | | |` row via regex. Reuse opportunity: mirror `_table_cols`'s docstring conventions (anchoring, fail-loud-on-ambiguous-match) rather than inventing new failure semantics.
  - Every existing test calling `_table_cols(...)`/`_extract_grand_total(...)` against `--summary` output (i.e., built via `_cost_args(summary=True, ...)` or `cmd_cost` with `summary=True`), across **every `TestCost*`-prefixed class** — not just the class literally named `TestCost` (`:5620`); `TestCostSummary` (`:7504`) alone carries at least 4 `_extract_grand_total` calls and 1 `_table_cols` call against `--summary` stdout — switches to the new markdown helpers. Tests asserting only non-tabular lines (`Scope: ...`, `STALE PRICING`, `Unpriced tokens: ...`, absence of `## Cost by project`/`## Top`/etc.) are format-agnostic and need no change. Identify the exact affected set by running `pytest -k TestCost` (the Verification section's own filter, which substring-matches every `TestCost*` class) and fixing each failure — do not hand-transcribe the list here; the test run is the authoritative enumeration.
  - Add two new tests the reformatting pass won't produce on its own: (1) a zero-transcript `--summary` run asserts the model-ID markdown table still renders a well-formed header + separator row with no data rows (reusing the zero-transcript fixture `test_summary_stdout_never_leaks_a_home_rooted_path_in_the_zero_transcripts_warning`, `:7849`, already sets up), and (2) a priced-turn `--summary` run asserts the exact bolded total row shape `| **total** | **{amount}** | | |` via `_extract_md_grand_total` against a hand-computed total, not just an approximate `pytest.approx` match on the parsed number.
  - Add a direct unit test of `_print_thread_table`'s markdown branch (calling it directly with known `main_total`/`subagent_total`/`grand_total`, asserting the exact rendered lines) rather than relying solely on `--summary` integration coverage to catch a wiring bug (wrong argument order, wrong `_pct_of` denominator).
  - Full-report (non-`--summary`) tests, and every other test class's use of `_table_cols`/`_extract_grand_total`, are untouched.

- `docs/transcript-analysis.md` (`:552-659`, the `cost` subcommand section) — update the `--summary` sample-output code block (`:589-606`) to the new GFM shape; this documents current behavior (Axis 3 "descriptions," not a preserved record), so it's in scope. No change to the full-report sample block below it, or to prose describing flags/behavior that isn't about output formatting.

## Out of scope

- **Editing `claude/.claude/skills/pr-description/SKILL.md`.** In reach, not owned by another party, but declines to: see the Assumption ledger's first row above for why its existing text already produces the target shape unedited.
- **A `--format {text,md}` flag on `cost`**, mirroring `audit-routing-samples`. Considered and rejected in Approach above — `--summary` has one real consumer today, so a flag would carry a permanently-one-valued option.

## Verification

- `../../../.venv/bin/pytest claude/.claude/scripts/tests/test_transcript_analysis.py -k TestCost -q` — every summary-mode test passes against the new GFM output; every full-report-mode test is byte-for-byte unaffected.
- `../../../.venv/bin/pytest claude/.claude/ -q` — full suite, confirming no other test (e.g. anything asserting on `## Cost summary` heading text) broke.
- `../../../.venv/bin/ruff check claude/.claude/` — clean.
- Manual: run `python3 claude/.claude/scripts/transcript-analysis.py cost --this-repo --branches cost-report-markdown-format --summary` from the worktree and visually confirm the output matches PR #672's shape (GFM tables, no duplicate heading) when pasted under a `## Cost` heading in a scratch markdown preview.
- Re-fetch this branch's own eventual PR body once opened and confirm the machine-generated `## Cost` block renders as real tables on GitHub, not a code fence or raw text dump.
