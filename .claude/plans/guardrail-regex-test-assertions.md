# Guardrail: brittle regex/string-matching test assertions

## Context

**Goal:** add a claude-config skill-layer guardrail that stops sessions from writing brittle regex/string-matching test assertions over **runtime/structured output** when a parser library or the production parse/validate function is the correct tool — grounded in a cross-project transcript investigation and primary-source verification.

The user observed, across all projects, sessions adding brittle regex-based tests where a library or business-logic method was preferable. A confirmatory read-only pass over the transcript corpus (**4,742 JSONL transcripts across 53 project dirs**, via `~/.claude/scripts/transcript-analysis.py`'s storage layout) confirms regex-in-test is real and recurring — but reframes the scope:

- **~99% of the JS/TS signal and ~65% of the Python signal is the *source-scanning sibling*** — reading a file's source/DDL text as a string and regex-matching substrings — which `test-conventions` §9 **already covers** (line 199), and which `test-evaluation` §4 mirrors (line 69).
- The **genuinely-distinct** pattern — *regex-parsing runtime/structured output (log lines, XML/docx, JSON, generated config) instead of using a parser library or calling the production parse/validate function* — is real but a **minority** (clearest in the Python tooling tests: `re.findall`/`re.match` over emitted log lines and WordprocessingML).

**Intended outcome:** a sharp new *authoring* rule for the distinct case, plus stronger *review-time* detection of **both** failure modes, with the rationale grounded in primary sources and recorded in the co-located `REFERENCES.md`.

**Decisions locked with the user:** skill layer only (no CLAUDE.md edit); add a distinct new row **and** make `/code-review` actively flag the source-scan sibling; one-off evidence brief is sufficient (no new detection tooling).

## Approach

Skill-layer only. The repo's own routing rule (`ai-instruction-and-memory-files` §"Quick decision flow") puts a rule that fires only inside the test-authoring/review workflow in the skill body, not the always-loaded CLAUDE.md — and CLAUDE.md already carries the production-code version ("Hand-rolled logic in non-trivial domains"; `code-review` item #8 names "regex parsing"). The skill layer is still "claude-config level": `test-conventions`/`code-review` fire on every project's test work, satisfying "across all projects."

Four coordinated edits plus a source-grounding step:

1. **`test-conventions/SKILL.md` §9 (Common authoring mistakes)** — add **one distinct `| Mistake | Fix |` row** for the runtime-output case, adjacent to the source-scan row (line 199) and cross-referencing it so the two failure modes stay sharply named (text-presence vs. re-implemented parsing).
   - *Length constraint:* the file is at **201 lines, already over the 200 cap**. `check-skill-length.sh` denies a commit only when the staged file is over-limit **and longer than the committed version**, so the net line count must stay **≤ 201** (target **≤ 200** to bring the file back into policy). Reclaim ≥1 physical line by tightening prose — candidate: fold the §9 lead-in (line 190, "Avoid these when writing new tests:") into the section heading or table caption. **Shorten first, do not extract** (per `docs/skills.md` length-cap note).

2. **`code-review/SKILL.md` Hygiene checklist (near item #8, line 79)** — add **one new checklist item** that makes brittle test assertions first-class at review time, covering **both** source-scanning source text **and** regex over runtime/structured output, with the full rationale **deferred to `test-conventions` §9** (single source of truth — `code-review` already delegates test-authoring standards to `test-conventions` via the "Adds or modifies test code" dispatch row, ~line 230). Item #8 stays as-is (it targets *production* hand-rolled regex). Update the **Item ownership table** (~lines 307–349): owner `staff-sdet`, standard `test-conventions`. `code-review` has a 500-line ceiling, so headroom is not a concern.

3. **`test-evaluation/SKILL.md` §4 (Anti-patterns, ~lines 60–77)** — add a **parity mirror row** for the distinct runtime-output case in the `| Anti-pattern | Why it's wrong | Fix |` format, adjacent to the source-reading row (~line 69). The two skills deliberately mirror each other (source-scan, tautological, over-mocking all appear in both); this keeps that parity for the evaluator path. *(These `test-evaluation` line refs are from codebase exploration, not a direct read — confirm the exact rows by reading §4 before editing.)*

4. **`test-conventions/REFERENCES.md`** — add a short citation block grounding the new rule (output of the verify-sources step). `REFERENCES.md` is the edit-time co-located source home and is **not loaded at skill runtime**, so the rationale rides there, not in the skill body.

**Grounding step (verify-sources) — run before writing the rule's rationale.** Confirm these claims at the primary source and quote literal lines into `REFERENCES.md`:
   - Gerard Meszaros, *xUnit Test Patterns* (xunitpatterns.com) — the **"Fragile Test"** smell and **test logic duplicating production logic** (a test that re-implements production parsing passes when production is broken and breaks on benign format changes).
   - *Software Engineering at Google* (free online, testing chapters) — **"Test via Public APIs"** and **"Don't Put Logic in Tests"** (avoid re-deriving expected values with the same logic the code uses).
   - Language parser docs — Python `re` is not for nested/structured formats; use `json` / `xml.etree` / `html.parser` / `ast`. (Phrase the rule stack-agnostically per the repo's "global skill bodies stay platform-agnostic" rule — name the *concept* "parser library", not a specific package.)

**Rule content (the distinct anti-pattern), to be phrased in each skill's voice:**
> In a test, parsing or validating runtime/structured output (log lines, JSON, XML, generated config) with a hand-rolled regex instead of (a) parsing with the same library and asserting on the resulting object, or (b) calling the production parse/validate function and asserting on its result. The regex re-implements logic the production code owns, so the test passes when production is broken (it tests the regex, not the code) and breaks on benign format changes (Fragile Test). Distinct from source-scanning (reading the file-under-test's *source text*) — cross-reference, don't merge.

**Heavier alternatives considered and rejected** (the chosen surface is deliberately the lightest):
   - *A durable CLAUDE.md global rule* — heavier (always-loaded, costs attention budget on every non-test session) and partly duplicates the existing production-code principle. Rejected per user (skill layer only) and the repo routing rule.
   - *A reusable `transcript-analysis.py` content-grep subcommand* — heavier (new code + tests + skill-doc surface) for a one-time investigation. Rejected per user (one-off brief sufficient).

## Critical files

- `claude/.claude/skills/test-conventions/SKILL.md` — §9 table, add a row near line 199; reclaim ≥1 line to respect the length gate.
- `claude/.claude/skills/code-review/SKILL.md` — new Hygiene item near line 79; update Item ownership table (~lines 307–349).
- `claude/.claude/skills/test-evaluation/SKILL.md` — §4 table, parity row near line 69.
- `claude/.claude/skills/test-conventions/REFERENCES.md` — add citation block.

**Reuse / anchors (do not duplicate):**
- Existing **source-scan rows** — `test-conventions` line 199, `test-evaluation` line 69. Cross-reference these; the new rule is the *distinct* sibling.
- **`code-review` item #8** (line 79) — production hand-rolled regex. The new item is the test-side sibling; keep them distinct.
- **`code-review` dispatch row** (~line 230, "Adds or modifies test code → invoke `test-conventions`") — already wires the authoring standard into review; the new `code-review` item is the explicit first-class trigger, with rationale still owned by `test-conventions` §9.

## Verification

- **Length gate:** `bash claude/.claude/hooks/check-skill-length.sh`-equivalent check — confirm `test-conventions` net ≤ 201 (target ≤ 200); `code-review`/`test-evaluation` under their caps.
- **`/skill-review`** on each edited SKILL.md (`test-conventions`, `test-evaluation`, `code-review`) — hook-enforced behavioral-equivalence marker; run the skill on its own diff per repo CLAUDE.md, checking that additions to brevity-arguing skills stay tight.
- **`/code-review`** on the full diff (auto-dispatches `/skill-review` per file type).
- **Trigger evals:** `test-conventions/evals/trigger-cases.json` — confirm no trigger change is needed (the change is body content, not the description); add a case only if warranted.
- **Repo suite:** `.venv/bin/pytest claude/.claude/` and `.venv/bin/ruff check claude/.claude/` (from a worktree: `../../../.venv/bin/...`) — no code change, but confirm no skill-structure test regresses.
- **Spot-check fidelity:** verify the new row's wording matches a real transcript sample (the log-line `re.match`/`re.findall` case and the XML-via-regex case from the evidence brief).

## Out of scope

- No CLAUDE.md edit (production-code principle already exists there + in `code-review` #8).
- No reusable `transcript-analysis.py` subcommand (user: one-off brief sufficient).
- No broad rewrite of the existing source-scan rows beyond adding the new `code-review` detection item; the source-scan rule text itself stays as-is.

## Branch note

Plan written to the harness plan path while in plan mode. On approval: derive a slug via `branch-creation` (suggest `guardrail-regex-test-assertions`), `git worktree add .claude/worktrees/<slug> -b <slug>` (worktree enforcement is active), and move this plan to `.claude/plans/<slug>.md` on that branch. Per repo policy, an AI agent opening the PR does not merge it.
