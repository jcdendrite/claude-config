# Changelog entries are not evidence of current behavior

## Context

Goal: stop agents from citing a CHANGELOG.md entry as proof of how something behaves now (GH-1450).

Ask: GH-1450 ("Agents cite CHANGELOG.md entries as evidence of current behavior"), whose proposed fix is one sentence: "A changelog entry or other preserved record (Scope discipline, Axis 3) shows what changed when it was written. For how something behaves now, cite the code or a living doc." Engineer's direction: "If we decided to add prose, I'd recommend putting it in the project-level CLAUDE.md because this repo is the only one I work with that has a CHANGELOG". Engineer selected the label "Path-scoped rule file" for placement, over folding the sentence into root CLAUDE.md, which sits at its 200-line limit.

Why now: a plan-architect dispatch cited a dated, conditional CHANGELOG entry about stow re-install as a standing fact, though the layout it covers is now documented as unsupported (`docs/hooks.md`). About 24 lines of CHANGELOG.md mention re-stow or real-directory layouts. Entries are preserved records that must stay as written.

Engineer's concern: the fix smells like compounding defensive engineering, so the plan keeps to one sentence in one place and adds no hook, deny rule, or header note. The issue already set aside a CHANGELOG header note, a verify-sources clause, and a read deny.

Intended outcome: an agent that opens CHANGELOG.md gets the one-sentence rule, in this repo only.

Evidence from exploration (this session):
- Root `CLAUDE.md` is 200 lines, and `claude/.claude/hooks/check-claude-md-length.sh` denies a commit that grows a file past 200 lines or 30,720 bytes.
- `.claude/rules/` holds path-scoped rules with `paths:` frontmatter, for example `.claude/rules/shell-floor.md`; `docs/rules-references.md` § "`paths:` glob-dialect conventions" records the dialect.
- `claude-skills/skills/verify-sources/SKILL.md:33` lists CHANGELOG/release notes as a ranked source for third-party libraries, which stays untouched.
- No `plan-it-*` project layer exists under `.claude/skills/`.
- Files flagged: `.claude/rules/shell-floor.md`, `docs/rules-references.md`, `CLAUDE.md`, `CHANGELOG.md`, `claude/.claude/CLAUDE.md`, `claude/.claude/hooks/check-claude-md-length.sh`.

## Approach

Add one project-scoped rule file, `.claude/rules/changelog-entries.md`, whose only `paths:` entry is the literal `CHANGELOG.md` and whose body is GH-1450's two-sentence fix, verbatim, under one heading. Nothing else changes: no `README.md`, `CHANGELOG.md`, `CLAUDE.md`, hook or test edit.

The file's frontmatter, in the shape `.claude/rules/design-decisions.md` already uses:

```markdown
---
paths:
  - "CHANGELOG.md"
---
```

Then a blank line, the H2 heading `Changelog entries are records, not current behavior`, a blank line, and this body, verbatim: "A changelog entry or other preserved record (Scope discipline, Axis 3) shows what changed when it was written. For how something behaves now, cite the code or a living doc."

The rule reaches an agent that opens CHANGELOG.md with `Read`, a line-range read included (row 8). It does not reach an agent that quotes a CHANGELOG.md line straight from `Grep` output without opening the file (row 9). That gap is listed in Out of scope.

Alternatives set aside:

- **Root `CLAUDE.md`.** It sits at its 200-line limit and loads in every session of this repo, although the sentence matters only when CHANGELOG.md is in play. The engineer chose the rule file instead (rows 1-2).
- **A stowed `claude/.claude/rules/` rule on `**/CHANGELOG.md`, or a line in the global `claude/.claude/CLAUDE.md`.** Either ships to every stow consumer, and the Ask confines the change to this repo (rows 4, 6).
- **A header note at the top of CHANGELOG.md.** It is lighter than a rule file but reaches only a read that covers the file's top. Agents find an entry by `Grep` and then read a line range around it (row 19). The issue also set it aside.
- **No change, relying on Axis 3 as it stands.** Axis 3 governs whether a record may be edited, not what it may be cited for. The motivating dispatch cited an entry anyway (Context).
- **A `**/CHANGELOG.md` glob.** The repo holds one CHANGELOG, at its root (row 10). A literal entry matches the precedent of the other project rules (row 11).

A hook, a deny rule and a `verify-sources` clause are heavier than a rule file, and the engineer and the issue both ruled them out (Context). The rule file is the lightest mechanism that delivers the sentence on any line-range read of the file.

### Assumption ledger

**Root:** Agents cite a CHANGELOG.md entry, which is a dated record of a change, as evidence of how the repo behaves now. Nothing an agent loads when it opens CHANGELOG.md says otherwise.

**Givens:**
- **G1.** Claude Code decides when a path-scoped rule loads. The repo controls only the `paths:` glob. Reason: the vendor owns this harness behavior. [verified: `docs/rules-references.md:256-263`, citing `code.claude.com/docs/en/memory`]
- **G2.** CHANGELOG.md entries are preserved records and stay as written. Reason: Axis 3 of the global `CLAUDE.md` governs them, and changing Axis 3 is a decision outside this plan. [verified: `claude/.claude/CLAUDE.md:71,73`]

**Rows:**
1. [engineer-verified: "Path-scoped rule file"] The sentence goes in a path-scoped rule file, not in root `CLAUDE.md`.
2. [engineer-verified: "ooh I think rule that applies on CHANGELOG is actually the correct answer - good suggestion"] The rule triggers on CHANGELOG.md.
3. [unverified] The session's own option text for row 1 said three things. The rule loads only when an agent opens CHANGELOG.md. It carries no always-loaded context. It was not verified to fire on a grep-then-line-range read. Rows 8 and 9 settle the third point.
4. [engineer-verified: "Yes, brief Out-of-scope note (Recommended)"] The global `CLAUDE.md` change is recorded in Out of scope as a brief note.
5. [unverified] The session's rationale for row 4, that stowed users don't keep changelogs, is its paraphrase. Row 6 holds the engineer's own words.
6. [engineer-verified: "this repo is the only one I work with that has a CHANGELOG"] The engineer confined the change to this repo (`Ask:` line).
7. [verified: this plan's `Ask:` line, quoting GH-1450] The rule body is the issue's proposed two sentences. This dispatch did not re-read the issue itself.
8. [verified: observed in this `plan-architect` dispatch's own session, a subagent anchored in a linked worktree] A `Read` of `docs/design-decisions.md` with offset 1 and limit 5 made the harness inject `.claude/rules/design-decisions.md`, whose `paths:` lists that literal path. A `Read` of `claude-skills/skills/plan-it/SKILL.md` with offset 47 and limit 100 likewise injected the three project rules whose `paths:` match it. So a line-range `Read` loads a matching project rule, in a subagent and in a worktree. These are single-trial observations of behavior, not a statement in the vendor docs.
9. [verified: same session] A content-mode `Grep` of `docs/design-decisions.md` returned its line 1 and injected no rule, before the row-8 `Read` injected it. An earlier `Grep` of `plan-it/SKILL.md` also injected nothing. So an agent that quotes a CHANGELOG.md line from `Grep` output without opening the file never sees this rule. Single trial.
10. [verified: `claude-skills/skills/tests/test_rules_frontmatter.py:225-235`; a Glob for `**/CHANGELOG*` found only the root `CHANGELOG.md`] A project rule may carry a fully literal `paths:` entry, and the test requires it to resolve to an existing path under the repo root. `CHANGELOG.md` does.
11. [verified: `.claude/rules/citation-grammar.md:7,11`; `.claude/rules/design-decisions.md:4`] Project rules already use literal entries (`README.md`, `CLAUDE.md`, `docs/design-decisions.md`).
12. [verified: `test_rules_frontmatter.py:285-320`; the `paths:` lists of all eight `.claude/rules/*.md` files; a grep for `CHANGELOG` outside `CHANGELOG.md` and `.claude/plans/` that matched no rule file] No rule declares the set `{"CHANGELOG.md"}`, so the duplicate-set test passes.
13. [verified: `claude/.claude/rules/rule-authoring-conventions.md`, "A stowed rule's referent is every consumer's repo" bullet; `test_rules_frontmatter.py:147-153`] The `**/`-led requirement binds stowed rules only. A project rule may use a literal path.
14. [verified: a grep of `CHANGELOG.md` for `shell-floor`, `citation-grammar`, `test-tree-packaging`, `bash-unit-test-seams`, `transcript-analysis-tests` and `rules/design-decisions`, zero matches; repo `CLAUDE.md` "Repo layout" names `claude/.claude/` and `claude-skills/` as the stow packages] No project `.claude/rules/` file has a CHANGELOG entry. The root `.claude/` tree is not stowed, so no stow consumer sees this change.
15. [verified: `README.md:274`] The Configuration files bullet lists five uses of `.claude/rules/` and already leaves out three of the eight project rules (citation grammar, design-decision files, transcript-analysis tests). Inference: a test requiring the list to be complete would already fail on those three, so none exists.
16. [verified: `claude/.claude/scripts/select-tests.py:904`] A change under `.claude/rules/` selects the skills and hooks test trees.
17. [verified: `.claude/rules/review-pipeline-dispatch.md:24-27`] `/code-review` invokes `ai-instruction-and-memory-files` for a rule file. No hook enforces it.
18. [verified: `claude/.claude/CLAUDE.md:62,71,73`; `.claude/rules/citation-grammar.md`] "Scope discipline" and "Axis 3" are the global `CLAUDE.md`'s own names, and Axis 3's item 2 is "Changelog entries documenting past events". The parenthetical therefore resolves for every agent that loads the global file. It carries no `§`, so the citation grammar does not apply to it.
19. [verified: `claude/.claude/CLAUDE.md:61`] Agents are told to `Grep` for the part they need and then `Read` that range, so a header note at the top of CHANGELOG.md is outside most reads.
20. [unverified] How the motivating `plan-architect` dispatch reached the CHANGELOG entry, by `Grep` output or by a `Read`, was not checked. If it was by `Grep` alone, this rule would not have reached it (row 9).

**Mechanisms:**
- **M1. New `.claude/rules/changelog-entries.md` with `paths: ["CHANGELOG.md"]` and the issue's sentence as its body.** anchors: root, row1, row2, row7, row8, row10, row11, row12, row13, row18.
- **M2. No `README.md`, `CHANGELOG.md`, `CLAUDE.md` or test edit.** anchors: row4, row6, row14, row15, row16.
- **M3. The Grep-only gap is accepted, not closed.** anchors: row1, row9, row20.

## Critical files

- **Create** `.claude/rules/changelog-entries.md`, the only file the diff touches outside `.claude/plans/`.
- **Reuse:**
  - The frontmatter shape of `.claude/rules/design-decisions.md`.
  - The existing validation in `claude-skills/skills/tests/test_rules_frontmatter.py`. It discovers the file by `rglob` and needs no new test.
- **Dispatch:** one `code-writer` dispatch for the single file, verified by the `select-tests.py` command below.

Projected diff: about 9 added lines, all in the new rule file.

## Verification

1. `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py` prints `select-tests: running` with the skills and hooks test trees and passes. That run includes `test_rule_has_parseable_paths_frontmatter[.claude/rules/changelog-entries.md]` and `test_no_two_rule_files_declare_the_same_full_paths_set`.
2. `git diff --name-only origin/main...HEAD` lists only `.claude/rules/changelog-entries.md` and this plan file.
3. Load check. Once the file exists, a session started after that point issues a `Read` of `CHANGELOG.md` with an offset and limit (e.g. offset 400, limit 10). The harness then injects `.claude/rules/changelog-entries.md` as a system reminder or a "Loaded" notice. A dispatched subagent works as the probe, since subagents receive rule injections (row 8). Whether a session picks up a rule file created after it started is [unverified]. If the probe shows no load, retry in a new session before concluding anything about the glob.
4. `/code-review` routes the rule file to `ai-instruction-and-memory-files` (row 17).

## Out of scope

- **Global `claude/.claude/CLAUDE.md`, and any stowed `claude/.claude/rules/` copy.** The engineer selected "Yes, brief Out-of-scope note (Recommended)". In the engineer's words, "this repo is the only one I work with that has a CHANGELOG".
- **Existing CHANGELOG.md entries**, including the roughly 24 lines on re-stow and real-directory layouts. They are preserved records under Axis 3 (G2).
- **A hook, deny rule, CHANGELOG header note or `verify-sources` clause.** The issue set these aside, and the engineer judged them compounding defensive layers. `claude-skills/skills/verify-sources/SKILL.md:33` stays as is, because it ranks a third-party library's changelog as a source, which is a different case.
- **Grep-only access.** An agent that quotes a CHANGELOG.md line from `Grep` output without opening the file is not reached (row 9). Closing that gap needs always-loaded text, such as root `CLAUDE.md` at its 200-line limit, or a hook. The engineer's placement choice set both aside.
- **Other preserved-record paths** (`docs/design-decisions/**`, `docs/case-studies/**`, `docs/reports/**`) stay out of `paths:`, because the Ask names CHANGELOG.md only.
- **A CHANGELOG.md entry for this change.** No project rule has one, and the root `.claude/` tree is not stowed (row 14).
- **`README.md:274`'s list of `.claude/rules/` uses.** It is already non-exhaustive, so it is not extended (row 15).
