# Fewest-lines rule (GH-1364, piece 3)

## Context

Goal: add a short rule that steers implementation toward the fewest lines that solve the task, without ever cutting validation, error handling, security, or accessibility.

Ask: GH-1364's Plan item 3 — "Add a short rule: write only what the task needs, and never cut validation, error handling, security, or accessibility. It adapts the core rule of ponytail without importing that project wholesale. It must defer to the existing overlapping rules rather than restate them: `code-review`'s 'appropriately sized' check and CLAUDE.md's lighter-primitive and standard-library-first bullets."

Why now: GH-1364 found that review cost rises with PR size, and nothing pushes implementation toward the smallest diff that solves the problem. Pieces 1 and 2 (the `review-cost-by-size` subcommand and the `pr-cost` ledger `review_rounds` column) have merged. Piece 4 (the diff-size prompt) follows as a separate PR.

Source rule being adapted (ponytail README, MIT-licensed): "write only what the task needs, and never cut validation, error handling, security, or accessibility."

Existing rules the new one must defer to rather than restate:
- `claude-skills/skills/code-review/SKILL.md` line 57: the "appropriately sized" check.
- `claude/.claude/CLAUDE.md` §Engineering Judgment: "Default-suspect over-powered primitives".
- `claude/.claude/CLAUDE.md` §Engineering Judgment, "Ground every choice": the hand-rolled-logic bullet (search the standard library first).

Open design question, delegated to the plan: where the rule lives. The engineer, asked to choose between an always-loaded `claude/.claude/CLAUDE.md` bullet (with or without a `code-writer.md` pointer) and `code-writer.md` alone, answered: "I'm not sure. Wanna let the planning process decide?" `claude/.claude/CLAUDE.md` is at 180 of its 200-line commit-time cap.

The PR must not close GH-1364: piece 4 remains.

## Approach

M1 adds the floor to the first sentence of Scope discipline's **Axis 4 — Change size** in `claude/.claude/CLAUDE.md` (line 80): "Prefer minimal, targeted changes" gains ", but never cut validation, error handling, security, or accessibility", and every other byte of the line stays as in HEAD. The file gains 70 bytes and no line. `code-writer` needs no edit, because its write-time baseline already names "Axis 1–4". One row in the existing group-placement test pins the floor in Agent Core, and one CHANGELOG entry announces it.

**Changes.** Exact text is given because the byte gate and a test make the wording load-bearing.

- **M1: `claude/.claude/CLAUDE.md` line 80.** Replace the whole line with the line below. Only the first sentence changes: the clause goes in before its full stop. The two-space indent, the `**Axis 4 — Change size.**` lead-in, and the second and third sentences stay byte for byte.

```markdown
  **Axis 4 — Change size.** Prefer minimal, targeted changes, but never cut validation, error handling, security, or accessibility. Do not refactor entire files or expand scope beyond what was asked. If you see an opportunity for a broader improvement, mention it separately — do not bundle it in.
```

- **M2: `claude/.claude/hooks/tests/test_global_claude_md_groups.py`.** In `_PLACEMENTS`, under the `# Rules a dispatched agent needs.` comment, add this row after the `script-first-bash-recipes` row (line 100), in the single-line form its neighbours at lines 98-100 use:

```python
    ("change-size-floor", "never cut validation, error handling, security, or accessibility", "core"),
```

- **M3: `CHANGELOG.md`.** Add this as the first bullet under `## [Unreleased]` → `### Changed` (line 9):

```markdown
- **The global `CLAUDE.md`'s Scope discipline Axis 4 now also says never to cut validation, error handling, security, or accessibility.**
  - **Takes effect** for a session only if that session started after you pulled this change.
```

**Why this shape.**

- Axis 4 is already the file's write-time rule on change size, so the floor qualifies it in place. A new bullet would give one topic two homes, against CLAUDE.md's single-source-of-truth bullet (row 1).
- Axis 4 sits in Agent Core, so the floor reaches the main session's inline writes and every `code-writer` dispatch (rows 2-4).
- Only the floor is new. The Ask's other half, "write only what the task needs", overlaps the kept "Prefer minimal, targeted changes", so M1 omits it. This departs from the Ask's literal wording, by the engineer's selected label (rows 5-10).
- M1 fits G1's byte cap (rows 13-14), and Verification step 1 stops before the edit if it no longer fits.
- M1 restates none of the three rules the Ask names, and points to none of them (rows 11, 19).

**Accepted residuals.** Rows 17 and 18 record two residuals that follow from the engineer's selected label (row 9). They are accepted, not fixed.

**Alternatives set aside.**

- **Rewriting Axis 4 around "Write only what the task needs" and absorbing its other clauses** (this plan's earlier revision). The engineer judged the absorbed clauses, and "minimal" and "targeted", load-bearing (rows 6-8).
- **Replacing only Axis 4's first sentence with the ponytail sentence.** It drops "minimal" and "targeted", which the engineer called load-bearing (row 7).
- **Version A: appending the full ponytail sentence after "Prefer minimal, targeted changes."** Its "Write only what the task needs" would restate the kept sentence (row 5). By hand count it adds 103 bytes, or 117 with "to save lines", either way more than row 13's 80 bytes of headroom.
- **Version B with a closing "to save lines".** It would scope the floor to cuts made for size (row 17), but its 84 bytes exceed row 13's headroom by 4. The engineer chose B without it (row 9).
- **A new bullet plus a raise of G1's byte cap.** Change size would get a second home beside Axis 4 (row 1), and the raise is a decision outside this plan (G1).
- **A new bullet plus trims elsewhere in `CLAUDE.md`.** It is still a second home. Each trim of an unrelated rule also widens the diff past the Ask and owes its own compression-diff audit.
- **`code-writer.md` only.** It misses the main session's inline writes (row 3), and it would restate Axis 4 in a second file.
- **A path-scoped `claude/.claude/rules/` file.** It loads when a matching file is read, not when code is written. A glob broad enough to cover code loads it in nearly every coding session anyway, and it would still be a second home.
- **`code-review` Step 1 only.** That is review time; the Ask's gap is at write time.
- **A pointer clause naming the over-powered-primitives bullet, the hand-rolled-logic bullet, and `/code-review` Step 1.** The shortest wording drafted cost about 118 bytes, for targets already in reach (row 19). The engineer chose no pointer (row 11).
- **Ponytail README's narrower floor, "trust-boundary validation, data-loss handling".** It departs from the Ask's four terms.
- **A whole-line pin of Axis 4 in a test.** M2 already pins the floor's presence and group. A whole-line pin would block wording edits without guarding anything more.

**Assumption ledger.**

**Root:** Axis 4 — Change size is the write-time size rule for every stow consumer's code-writing agents, meaning the main session writing inline and `code-writer`. It says "Prefer minimal, targeted changes" but sets no floor under that preference. Nothing in it stops an agent that is minimizing a change from cutting validation, error handling, security, or accessibility.

**Givens:**

- **G1: the global `CLAUDE.md` length gate stays at 200 lines and 30,720 bytes.** `check-claude-md-length.sh` denies a staged file that is over a limit and longer than its committed version. Raising the cap is a decision outside this plan, and the hook's own comment requires a new basis for any raise past 32,768 bytes. `[verified: claude/.claude/hooks/check-claude-md-length.sh:6-9, :54, :59, :123]`
- **G2: ponytail's wording belongs to the ponytail project.** The plan adapts the floor half of its core sentence and imports nothing else. `[unverified: relayed by the dispatching session from the ponytail README, not read this session; its SKILL.md and AGENTS.md wording unconfirmed]`
- **G3: no private-corpus figure enters the PR.** The repo is public, and its redaction rules bar per-project figures, so no figure from GH-1364's wider-corpus analysis and no count from local transcripts appears in the diff, CHANGELOG, or PR body. `[verified: repo-root CLAUDE.md § "Redact private-project-identifying content"]`

**Rows:**

1. `[verified: claude/.claude/CLAUDE.md:62, :80]` Axis 4 — Change size is Scope discipline's write-time rule on how much a change does. `anchors: root, M1`
2. `[verified: claude/.claude/CLAUDE.md:3, :47, :62]` Scope discipline sits under Agent Core's Working Style, which every agent follows. `anchors: M1`
3. `[verified: claude/.claude/CLAUDE.md:165]` The main session also writes code inline: routing to `code-writer` "does not change when the parent delegates versus writes inline". A home in `code-writer.md` alone misses those writes. `anchors: root, M1`
4. `[verified: claude/.claude/agents/code-writer.md:72-79; claude/.claude/hooks/tests/test_agent_roster.py:559-567]` `code-writer` loads the global `CLAUDE.md`, since no agent may set `omitClaudeMd`. Its baseline already says "Respect scope discipline (Axis 1–4)", so M1 reaches it with no `code-writer.md` edit. `anchors: M1`
5. `[verified: .claude/plans/fewest-lines-rule.md:7]` The Ask's rule text is "write only what the task needs, and never cut validation, error handling, security, or accessibility". M1 carries only the floor, from "never cut" on. The first half overlaps Axis 4's kept "Prefer minimal, targeted changes", since both tell an agent to do no more than the task needs. The floor is the part that is new. `anchors: root, M1`
6. `[engineer-verified: "I think prefer minimal targeted changes is load bearing too. Is it not? Is this PR replacing it with something else?"]` The engineer questioned replacing "Prefer minimal, targeted changes". The quote covers only that question. `anchors: M1`
7. `[engineer-verified: "I still think “minimal” and “targeted” are load bearing."]` M1 keeps "minimal" and "targeted" in Axis 4's first sentence. `anchors: M1`
8. `[engineer-verified: "Those extractions seem load-bearing to me, I would either ratchet up the byte limit or look elsewhere to trim"]` On the earlier draft's rewrite of Axis 4, the engineer judged the clauses that draft absorbed load-bearing. As the way to make room instead, they named a byte-limit raise or trims elsewhere. M1 keeps Axis 4's second and third sentences byte for byte. `anchors: M1, G1`
9. `[engineer-verified: "B without 'to save lines' (Recommended)"]` The engineer chose version B, without "to save lines", as the final shape. `anchors: M1`
10. `[unverified: the dispatching session's option descriptions, not the engineer's words]` Version B keeps "Prefer minimal, targeted changes" and adds the floor, with no separate "Write only what the task needs" sentence and no "to save lines". The other options shown are listed under Alternatives set aside. `anchors: M1`
11. `[engineer-verified: "No pointer (Recommended)"]` The dispatching session relayed this label without the question's text. The plan adds neither pointer it weighed: a clause in M1 naming the three rules the Ask names, or a line in `code-writer.md`. `anchors: M1`
12. `[engineer-verified: "Keep CHANGELOG entry (Recommended)"]` M3 ships. `anchors: M3`
13. `[verified: wc -c claude/.claude/CLAUDE.md printed 30640 at HEAD bff48804 this session, and again from the reviewer round]` The file is 30,640 bytes, 80 under G1's cap. Another PR can consume the headroom before this one merges, so Verification steps 1 and 7 measure again. `anchors: G1, M1`
14. `[verified: hand count this session; agrees with the dispatching session's relayed 232 and 302 bytes for line 80 before and after]` M1 inserts the 70-byte ASCII string ", but never cut validation, error handling, security, or accessibility" before line 80's first full stop and changes no other byte. The file gains 70 bytes and 0 lines, leaving 10 bytes of headroom at row 13's figure. `anchors: G1, M1`
15. `[verified: claude-skills/skills/ai-instruction-and-memory-files/SKILL.md:67-68, :73]` The compression-diff audit fires "For any diff that removes or shortens lines". M1 does neither: the full stop after "changes" moves to the end of the lengthened sentence, and nothing else on the line changes. The behavior test applies to the added clause instead, and the clause passes it. An agent trimming a diff that would drop an input check or an error path now meets a rule against that cut. `anchors: M1`
16. `[verified: claude-skills/skills/code-review/SKILL.md:62-63]` Step 1 counts "Layers that duplicate a higher-level abstraction already running the same work" and "Defensive paths against an actor or input the declared tier excludes" as over-elaboration. `anchors: M1`
17. `[unverified]` Without "to save lines", M1's floor does not say it bars only cuts made for size, so an agent could read it as shielding row 16's two markers. "But" ties the floor to "Prefer minimal, targeted changes", which points its plain reading at cuts made while minimizing. Step 1 still names both markers and runs after every code change (`claude/.claude/CLAUDE.md:135`). How agents read the clause is unmeasured. This is the engineer-chosen tradeoff of row 9, accepted, not a defect. `anchors: M1`
18. `[unverified]` M1 adds no new pressure toward smaller diffs, the gap the Context's Why-now line names (`.claude/plans/fewest-lines-rule.md:9`). That pressure stays with the kept "Prefer minimal, targeted changes" (rows 6-7), and GH-1364 piece 4 remains. This follows from row 9's choice and is accepted. `anchors: root`
19. `[verified: claude/.claude/CLAUDE.md:29, :44, :135; claude-skills/skills/code-review/SKILL.md:57-67]` M1's clause names no mechanism weight, no standard library, and no over-elaboration marker. It restates none of the over-powered-primitives bullet, the hand-rolled-logic bullet, and Step 1, and it leaves each the sole home of its rule. `anchors: M1`
20. `[verified: repo-wide grep outside .claude/plans/ for "Prefer minimal, targeted", "bundle it in", "refactor entire files" and "Change size" matches only claude/.claude/CLAUDE.md:80]` No skill, agent, doc or test quotes line 80, so M1 breaks no pin or citation. `anchors: M1`
21. `[verified: claude/.claude/hooks/tests/test_global_claude_md_groups.py:49-51, :91-100, :216-229, :255-260; grep of claude/.claude/CLAUDE.md for "never cut" and "accessibility" matches no line; pyproject.toml:2]` `_PLACEMENTS` pins selected bullets to their group and has a "Rules a dispatched agent needs" block. The test matches each fragment as a plain substring of fence-blanked lines and requires it on exactly one line. After M1, only line 80 carries M2's fragment. The row pins the floor and keeps it in Agent Core, the reach row 4 depends on. Its single-line form is about 103 characters, under the 130-character limit. `anchors: M2`
22. `[verified: CHANGELOG.md:5-9, :115-119, :152]` Rule changes to the global `CLAUDE.md` get an `[Unreleased]` entry, and `### Changed` opens at line 7 with its first bullet at line 9. The precedent wording for load timing is "**Takes effect** for a session only if that session started after you pulled this change." `anchors: M3`
23. `[engineer-verified: "I'm not sure. Wanna let the planning process decide?"]` The engineer left the rule's home to the plan. The quote covers only that delegation. `anchors: root, M1`
24. `[unverified]` The three homes first put to the engineer were the dispatching session's proposal, not the engineer's choice: a §Engineering Judgment bullet plus a `code-writer.md` name-only line, the bullet alone, or `code-writer.md` alone. They appear here only as alternatives. `anchors: M1`
25. `[verified: claude-skills/skills/code-review/SKILL.md:207; claude/.claude/agents/code-writer.md:169; .claude/rules/review-pipeline-dispatch.md]` At code-review time, M1 routes to `ai-instruction-and-memory-files`, which applies row 15's behavior test. No agent, SKILL.md or plugin file changes, so `/agent-review`, `/skill-review` and `plugin-semver` do not fire. `anchors: M1, M2, M3`

## Critical files

- `claude/.claude/CLAUDE.md`: M1, line 80 only.
- `claude/.claude/hooks/tests/test_global_claude_md_groups.py`: M2, one `_PLACEMENTS` row.
- `CHANGELOG.md`: M3, one entry.
- `.claude/plans/fewest-lines-rule.md`: this plan, committed with the change.

Reuse: `code-writer.md:79`'s existing "Respect scope discipline (Axis 1–4)" pointer carries the rule to `code-writer`. `_PLACEMENTS` and `test_pinned_bullet_sits_in_its_group` carry the pin with no new test function.

Dispatch split: one `code-writer` dispatch for M1-M3, handed this plan's path. No further split, because all three edits carry the same clause. The dispatch runs in this order:

1. Make M2, then run Verification step 4's pre-edit check and report its result.
2. Run Verification step 1's pre-edit measurement.
3. Make M1.
4. Make M3.

## Verification

1. Before M1, run `wc -c claude/.claude/CLAUDE.md`. If it prints more than 30650, stop and report, because fewer than 70 bytes remain below G1's 30,720 cap. Making room, by a cap raise or by trims elsewhere, is the engineer's choice (row 8). After M1, check three things:
   - `wc -l claude/.claude/CLAUDE.md` matches `git show HEAD:claude/.claude/CLAUDE.md | wc -l`.
   - `wc -c` is exactly 70 above the pre-edit figure.
   - `wc -c` is no more than 30720.
2. `git diff HEAD --numstat -- claude/.claude/CLAUDE.md` reports 1 line added and 1 deleted.
3. `grep -c -F 'Prefer minimal, targeted changes, but never cut validation, error handling, security, or accessibility. Do not refactor entire files or expand scope beyond what was asked. If you see an opportunity for a broader improvement, mention it separately — do not bundle it in.' claude/.claude/CLAUDE.md` prints `1`.
4. `scripts/venv-run.sh pytest claude/.claude/hooks/tests/test_global_claude_md_groups.py -k change-size-floor` selects one test in both runs.
   - Before M1, that test fails with a message containing "not found on any line" (`test_global_claude_md_groups.py:220-223`). Any other failure, or a run that selects no test, means the row is wrong.
   - After M1, it passes.
5. `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py -n 2` passes.
6. `/code-review` dispatches `ai-instruction-and-memory-files` for M1. It owes no compression-diff table, because M1 removes and shortens nothing (row 15), and its behavior test passes the added clause.
7. The commit passes `check-claude-md-length.sh`. After the last rebase onto `main` before handoff, `wc -c claude/.claude/CLAUDE.md` still prints no more than 30720, because another PR may have used the headroom.
8. The PR body must pass four checks:
   - It references GH-1364 with no GitHub closing keyword (close, closes, closed, fix, fixes, fixed, resolve, resolves, resolved, before either `#1364` or `GH-1364`).
     - `pr-description-claude-config` otherwise requires a closing keyword on a `GH-<N>` branch, so the run of `/pr-description` is told to use a non-closing reference such as "Part of #1364".
     - Check the published body with `gh pr view --json body` after the last body write, because later commits rewrite it.
   - It says piece 4 remains.
   - It credits ponytail as GH-1364 names it.
   - It carries no figure from GH-1364's wider-corpus analysis and no count from local transcripts (G3).

## Out of scope

- GH-1364 piece 4, the diff-size prompt, ships as its own PR. This PR does not close GH-1364.
- Raising G1's byte cap. M1 fits (rows 13-14). If Verification step 1 finds less room, the raise or a trim elsewhere goes back to the engineer, who named both (row 8).
- Extracting the `# Main session` section from the global `CLAUDE.md` to free bytes. It belongs to a separate existing effort, tracked under GH-1085 (`claude/.claude/hooks/tests/test_global_claude_md_groups.py:3-11`).
- Trimming other `CLAUDE.md` text: M1 fits without it.
- `code-writer.md`: its baseline already names Axis 1–4 (row 4), so a name-only line would restate it, and the engineer chose no pointer (row 11).
- `code-review` Step 1 and `plan-review/SKILL.md:89`: review-time and plan-time sizing already exist. A back-pointer to Axis 4, or a Step 1 note reconciling the floor with its over-elaboration markers (row 17), would add a SKILL.md edit, with its skill-review gate, for no check either step lacks.
- Ponytail beyond its floor. Its "write only what the task needs" overlaps the kept "Prefer minimal, targeted changes" (row 5), and nothing else from ponytail enters.
