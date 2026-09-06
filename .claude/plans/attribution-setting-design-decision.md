# Attribution-setting design decision

## Context

Record why this repo keeps its own attribution prose (`pr-description`
and `respond-pr` skills) instead of relying on Claude Code's native
`attribution` settings.json key. The user found the native setting
(via a Todoist task linking the Claude Code settings docs' Attribution
settings section, https://code.claude.com/docs/en/settings) and
asked what it gives the project, suspecting it doesn't reach every
place the repo wants attribution and wondering whether it lets any of
the repo's own prose be stripped. Investigation this session (schema
fetch, hook/skill grep, two GitHub issue reads) found it does not: the
native setting never reaches PR/issue review comments at all, and even
for the surfaces it does reach (commits, PR bodies) it's an unenforced
system-prompt nudge with documented reliability gaps, so none of the
repo's mechanically-enforced or self-checked attribution prose is safe
to remove. The user asked for this reasoning captured as a
`docs/design-decisions.md` entry so it isn't re-investigated later.
`attribution.sessionUrl` (the one lever that would have changed
project behavior — suppressing the personal claude.ai session link in
this public repo's commits/PRs) was separately considered and the user
chose to leave it at its default.

## Approach

Add one new numbered entry, **§40**, to `docs/design-decisions.md` recording why this repo's attribution requirements live in skill prose plus a `PreToolUse` gate rather than in Claude Code's native `attribution` settings key. Nothing else changes — no settings key is set, no skill body is edited, and no test is added. The entry's job is to make the investigation non-repeatable: it states the three independent reasons the native setting cannot absorb any existing prose, records the `sessionUrl` sub-decision and its reasoning, and names the conditions that would make the question worth reopening.

**Root problem.** This repo's attribution requirements are spread across a hook gate and two skill bodies with no recorded rationale, so a future session that discovers the native `attribution` settings key has no way to tell whether that prose is redundant — and the answer (it is not) takes a schema fetch, two upstream issue reads, and a hook/skill sweep to re-derive.

**Givens** (conditions the design treats as fixed and cannot reach):

- The native `attribution` key's property set is Anthropic's to define; this repo cannot add a review-comment property to it.
- The two upstream bug reports' resolution state is Anthropic's to change; both are closed with no fix version visible.
- Whether to pin commit-trailer text in this repo's own `.claude/settings.json` is reachable, not a true given — but this repo has never prescribed commit-trailer text anywhere, so there is no existing local convention to codify, and the one mechanism available (`attribution.commit`) is independently documented (GH #65657) as unreliable for the commit case specifically. `[engineer-verified: fixed after plan-review flagged the prior "outside any file in this repository" framing as false]`
- The user scoped the deliverable to a single doc entry. `[engineer-verified]`

**Assumption ledger:**

1. `attribution` exposes exactly three properties — `commit`, `pr`, `sessionUrl` — and none of them names review comments or replies. `[verified: schemastore.org/claude-code-settings.json, as fetched this session; superset of the settings-reference page]` `anchors: root`
2. This repo prescribes attribution at three surfaces, one of which (PR/issue comment replies) the native key cannot reach at all. `[verified: claude/.claude/skills/respond-pr/SKILL.md:93 requires the `**[Claude Code]**` prefix and the trailer on every reply]` `anchors: root`
3. `require-respond-pr.sh` is a routing gate, not a body-content check: it denies the comment write at the tool-call boundary unless routed through `/respond-pr`, and the skill is what applies the prefix. The entry must say "denies the write unless routed through the skill that applies the prefix," not "verifies the prefix is present." `[verified: claude/.claude/hooks/require-respond-pr.sh:272 and :323 deny messages, and :8 header comment]` `anchors: row2`
4. The `**[Claude Code]**` prefix does a second job disclosure alone does not: it is the discriminator `respond-pr` checks before a PATCH edit, because replies post under the user's own token so a `user.login` check cannot separate Claude's reply from the user's own comment. Deleting the prefix would remove a data-loss guard. `[verified: claude/.claude/skills/respond-pr/SKILL.md:109]` `anchors: row2`
5. On PR bodies — the one surface both mechanisms cover — the required shape differs: the trailer goes at both the first and last line of the body, while `attribution.pr` is a single appended string. `[verified: claude/.claude/skills/pr-description/SKILL.md:64, and :161's duplicate-trailer check]` `anchors: root`
6. `pr-description`'s trailer requirement is a skill self-check with no hook behind it; four hooks touch `gh pr` bodies and none checks for attribution. `[verified: handed-over grep of claude/.claude/hooks/*.sh — deny-escaped-backticks-in-pr-body.sh, deny-private-project-refs.sh, require-ready-for-review.sh, require-stow-reminder.sh]` `anchors: row5`
7. The native setting's effect is mediated by an instruction the model receives and must then follow, not by post-processing applied after the commit or body is written — the advisory tier §1 already distinguishes from hook enforcement. `[verified: GH issues #65657 and #77830, both of which locate the mechanism in the system prompt / Bash tool description]` `anchors: root`
8. #65657 (system-prompt trailer wins, `attribution.commit` never applied; closed as not planned) and #77830 (`Claude-Session:` trailer injected via Bash tool description ignores the setting, while the legacy footer *is* correctly suppressed; closed, labeled bug + reproduced) are mutually inconsistent about which half fails. `[verified: GH issue #65657 and #77830, pages as fetched — not the full comment threads]` `anchors: row7` — the entry records the discrepancy rather than resolving it, because either report alone is sufficient for the conclusion.
9. Whether the `Claude-Session:` trailer appears on commits authored from a plain CLI session is not established: the schema scopes it to "a web or Remote Control session," and #77830 does not name the surface it reproduced on. `[unverified]` `anchors: row10` — the entry states this as unestablished rather than asserting either way.
10. Claude Code sessions are private by default regardless of the surface that started them, becoming visible to others only on an explicit share, so the `Claude-Session:` link in a public commit resolves for the owner's account alone. `[verified: code.claude.com/docs/en/claude-code-on-the-web, "Share sessions"]` `anchors: root`
11. `sessionUrl` stays at its default `true`; no settings change of any kind ships. `[engineer-verified: "Yeah leave it alone, agreed."]` `anchors: root`
12. Neither `.claude/settings.json` nor `claude/.claude/settings.json` sets `attribution.*`, `includeCoAuthoredBy`, or `includeGitInstructions`, even in the current dirty working tree. `[verified: grepped both files this session; the only `model` key is `claude/.claude/settings.json:69`'s committed `"sonnet"` default per CHANGELOG.md:15]` `anchors: root`
13. Both settings.json files show `MM` in git status at session start, from changes unrelated to this work. `[verified: git status snapshot]` `anchors: row12` — the implementing session stages `docs/design-decisions.md` alone and leaves both settings files untouched; if either carries a per-session `effortLevel`/`model` override it is restored from `main`, not committed.
14. `docs/design-decisions.md` currently ends at §39, whose heading is at line 555 and whose last line (a `### Sources` entry) is line 570 — so §40 is the next free number and no renumbering is needed. `[verified: read of the file's §35–§39 range and `wc -l`]` `anchors: root`
15. `docs/design-decisions.md` may not contain a literal `~/.claude/<per-account-state>` path, but `settings.json` is not in the forbidden alternation. `[verified: claude/.claude/skills/tests/test_skills.py:2827 `_PER_ACCOUNT_STATE_PATH_RE`; §39 at line 561 already uses `~/.claude/settings.json` and passes]` `anchors: row16`
16. A `docs/**` change selects the hooks and skills test directories, so this diff is genuinely covered by tests rather than untested prose. `[verified: claude/.claude/scripts/select-tests.py:328, `_is_under(p, DOCS_DIR) → (HOOKS_TESTS_DIR, SKILLS_TESTS_DIR)`]` `anchors: root`
17. No existing entry in `docs/design-decisions.md` covers attribution, `respond-pr`, or `Co-Authored-By`. `[verified: grep of the file — the only hit is an unrelated use of "misattribution" at line 436]` `anchors: root`

**Mechanism justification.** One mechanism: a numbered prose entry in the existing `docs/design-decisions.md`, appended in the §35–§39 house style (dated heading, inline prose with bolded lead-ins for the parallel reasons, a closing `### Sources` list). `anchors: root` — this is the repo's established home for "non-obvious choice plus reasoning," it is the surface the user named, and it costs zero always-loaded context because nothing reads it at session start.

The over-powered-primitive check runs upward here, since the entry is already near the lightest durable option. Two heavier primitives were considered and rejected: a new standalone doc under `docs/` (`anchors: root`) is unwarranted for a single decision the existing numbered file was built to hold, and would fragment the record §39 and §1 are already cross-referenced from; a `docs/case-studies/` writeup (`anchors: root`) is the wrong genre — `docs/design-decisions.md:3` scopes case studies to longer-form writeups with primary-source citations, and this is a rejection rationale with five citations, not a measured investigation. Three lighter alternatives were also weighed and rejected: a note inside `pr-description/SKILL.md` and `respond-pr/SKILL.md` (`anchors: row2`) would duplicate the rationale across two always-loaded skill bodies, put a rejected-alternative argument at the wrong altitude for a procedure file, and drag a prose-only change through the hook-enforced `/skill-review` gate; a `CHANGELOG.md` bullet (`anchors: root`) is the user-facing record for stow-consumer-visible behavior, and this change alters none; an auto-memory file (`anchors: root`) is per-user and per-machine, so it would reach no other contributor and could not be cited from any durable surface.

### Entry prose to insert

Append after `docs/design-decisions.md:570` (the file's last line), separated by one blank line (note that §38's heading at line 534 has no preceding blank line — do not replicate that).

```markdown
## 40. Attribution stays in skill prose and a hook gate rather than the native `attribution` settings key (2026-09-01)

Claude Code exposes an `attribution` settings object with exactly three
properties — `commit`, `pr`, and `sessionUrl` — reaching two surfaces:
commit messages and pull request *bodies*. Neither this repo's
`.claude/settings.json` nor the stow-source `claude/.claude/settings.json`
sets it, and none of the attribution prose in `pr-description/SKILL.md`
or `respond-pr/SKILL.md` can be deleted in its favor. Three independent
reasons, any one sufficient.

**The largest surface is out of the setting's reach entirely.**
`respond-pr/SKILL.md`'s Attribution section requires every PR or issue
comment reply to open with `**[Claude Code]**` and close with the
disclosure trailer, and `require-respond-pr.sh` denies `gh pr comment`,
`gh issue comment`, `gh pr review`, the REST `comments`/`reviews`
endpoints, and the equivalent GraphQL mutations at the tool-call
boundary unless the write is routed through that skill. `attribution`
has no property covering review comments, so no candidate key exists to
replace any of it. The prefix also does a second job no attribution
string could: replies post through the user's own GitHub token and
appear under the user's account, so a `user.login` check cannot
distinguish a reply Claude wrote from a comment the user wrote, and the
prefix is what `respond-pr` verifies before a PATCH edit to avoid
overwriting the user's own text irrecoverably. Deleting it would remove
a data-loss guard, not only a disclosure line.

**On the one surface both cover, the required shapes differ.**
`pr-description/SKILL.md` places the trailer at both the first line and
the last line of the body. `attribution.pr` is a single appended string,
so it could supply the bottom copy at most, leaving the skill to
prescribe the top one regardless.

**The setting sits at the advisory tier §1 distinguishes from a hook.**
Its effect is mediated by an instruction the model receives and must
then choose to follow, not by post-processing applied to the commit or
body after the fact, so it is exposed to the same reasoned-around
failure that motivated hook enforcement everywhere else here. Two
upstream reports show that tier failing. anthropics/claude-code #65657
reports the system-prompt `Co-Authored-By` trailer taking precedence so
the `attribution.commit` value is never applied; it is closed as not
planned. #77830 reports the `Claude-Session:` trailer being injected
through the Bash tool description and ignoring `attribution`, while the
legacy footer *is* correctly suppressed by the same setting; it is
closed, labeled a bug, and marked reproduced. The two disagree about
which half fails, and both were read as rendered issue pages rather than
full comment threads, so the discrepancy is recorded rather than
resolved — either report alone rules out trading mechanically-gated
prose for the setting.

Commit trailers are a separate question from PR bodies, because this
repo has never prescribed commit-trailer text anywhere — no hook, no
`CLAUDE.md` line, nothing to codify. Setting `attribution.commit` in
this repo's own project-scoped `.claude/settings.json` would not be
pinning an existing convention; it would be inventing one under a task
scoped to this doc entry alone, and #65657 above already documents
that exact mechanism as unreliable for the commit case specifically.
Setting it in the stow-source `claude/.claude/settings.json` instead
would compound the problem by imposing that invented choice on every
consumer's every repository — wider than any condition this repo has,
the same scoping reasoning §39 applies to `claudeMdExcludes`.

`sessionUrl` was evaluated separately as the one property that would
have changed behavior, by suppressing the `Claude-Session:` deep link
this public repo's commits and PR bodies otherwise carry. It is left at
its default of `true`. The link resolves against the owner's own
account, and Claude Code sessions are private by default regardless of
the surface that started them, becoming visible to anyone else only when
their owner explicitly shares that session. The schema documents the
trailer as appended when running from a web or Remote Control session;
whether it also appears on commits authored from a plain CLI session is
not established, and #77830 does not name the surface it reproduced on.

**Revisit** when `attribution` gains a property covering PR or issue
review comments, or when both reports above close with a shipped fix.
Neither condition alone is enough: a fix with no review-comment property
still leaves `require-respond-pr.sh` and `respond-pr`'s prefix doing
work nothing else does.

### Sources

- [Claude Code settings reference](https://code.claude.com/docs/en/settings), Attribution settings section — the `attribution` object, its three properties, and the two surfaces it covers.
- [SchemaStore `claude-code-settings.json`](https://www.schemastore.org/claude-code-settings.json) — machine-readable property list confirming no review-comment property exists, and `sessionUrl`'s documented web/Remote-Control scoping.
- [anthropics/claude-code #65657](https://github.com/anthropics/claude-code/issues/65657) — report that the system-prompt trailer overrides `attribution.commit`; closed as not planned.
- [anthropics/claude-code #77830](https://github.com/anthropics/claude-code/issues/77830) — report that the `Claude-Session:` trailer is injected via the Bash tool description and ignores `attribution`; closed, labeled a bug and reproduced.
- [Claude Code on the web](https://code.claude.com/docs/en/claude-code-on-the-web), "Share sessions" — sessions are private by default and become visible to others only on an explicit share.
- `claude/.claude/skills/respond-pr/SKILL.md` — the Attribution section's prefix-and-trailer requirement, and the prefix's pre-PATCH self-authorship check.
- `claude/.claude/skills/pr-description/SKILL.md` — the top-and-bottom trailer placement rule.
- `claude/.claude/hooks/require-respond-pr.sh` — the tool-call-boundary gate routing every comment write through the skill that applies the prefix.
```

## Critical files

**One file, one `code-writer` dispatch** — do not split. The change is a single append with no file-set partition to divide along.

- **`docs/design-decisions.md`** (modify, repo root) — append the §40 entry above after line 570 (the file's current last line), preceded by one blank line. Reuse: match §35–§39's shape exactly — `## <N>. <title> (YYYY-MM-DD)` heading, inline prose with bolded lead-ins and no `###` subheadings other than the closing `### Sources`, and markdown-link source rows in §39's form. Do not renumber or edit any existing entry; §3's headings and prose are read by `test_doc_counts.py` occurrence patterns.

Explicitly **not** modified: `.claude/settings.json`, `claude/.claude/settings.json`, `claude/.claude/skills/pr-description/SKILL.md`, `claude/.claude/skills/respond-pr/SKILL.md`, `CHANGELOG.md`. Both settings files are already dirty (`MM`) from unrelated work at session start — stage `docs/design-decisions.md` alone, and if either settings file carries a per-session `effortLevel`/`model` override, restore it from `main` rather than committing it.

**Note for the implementing session:** the entry's claim that `require-respond-pr.sh` enforces attribution is precise in the drafted prose and easy to overstate in a rewrite: the hook denies the *write* unless it is routed through `/respond-pr`, and the *skill* is what applies the `**[Claude Code]**` prefix. The hook never inspects a comment body for the prefix. If the prose is edited during implementation, keep that distinction — an entry claiming the hook verifies the prefix would be a false record of this repo's own enforcement, in a file future sessions treat as authoritative.

## Verification

1. **`.venv/bin/python3 claude/.claude/scripts/select-tests.py`** — the repo's documented scoped command. For a `docs/**` change it selects the hooks and skills test directories (`select-tests.py:328`). What that actually exercises for this diff: `test_doc_counts.py`'s occurrence patterns against `docs/design-decisions.md` (§3's `## 3. Specialist reviewer roster (N personas)` heading digit and the two reviewer-count prose patterns) still match, confirming the append did not disturb pinned lines; `test_skills.py::TestPerAccountStatePathContract::test_doc_has_no_state_path` confirms the new prose introduces no literal `~/.claude/<per-account-state>` path; and `test_nudge_transcript_toolkit.py::TestNeverFiresOnMarkdown` picks the file up in its repo-wide `rglob("*.md")` content scan.
2. **Numbering and heading conventions, by inspection.** The new heading is `## 40.` — exactly one greater than the file's current last entry (§39 at line 555), with no gap and no duplicate. It carries a `(2026-09-01)` date suffix, matching §35–§39. `### Sources` is the entry's last element. One blank line separates the new heading from §39's closing source row.
3. **Resolve each of the five external URLs before committing** rather than trusting this plan's transcription — `verify-sources` discipline. Confirm the two GitHub issue numbers land on the titles quoted, and that the settings reference page still has an Attribution settings section.
4. **`/code-review`.** For a prose-only public-repo docs addition this is not a formality; it checks four things a test cannot: CLAUDE.md's Prose and Output Format rules (one idea per sentence, active voice, no fact dropped to shorten a sentence); all three redaction tiers (no private-project identifier, no real session ID or `claude.ai/code/session_<id>` value, no owner email, no structural fingerprint); the durable-docs self-test — the entry must read correctly to someone who never saw this PR, so no "we investigated this session," no "used to be," no PR-defined labels; and the quantitative-and-causal-claim grounding rule, which requires each behavioral claim about the native setting to trace to the cited schema or issue and each claim about this repo to trace to the cited file path.
5. **`/ready-for-review` before pushing**, per the standing pre-handoff rule.

No new test ships, so there is no new assertion to run — see Out of scope for why a settings-key guard was rejected rather than overlooked.

## Out of scope

- **Any `settings.json` change, in either file.** The user's explicit call, including for `attribution.sessionUrl` specifically. `[engineer-verified]`
- **Edits to `pr-description/SKILL.md` or `respond-pr/SKILL.md`.** The user scoped the deliverable to the doc entry. Independently: a SKILL.md edit pulls a prose-only change through the hook-enforced `/skill-review` gate, and the rationale would land at the wrong altitude inside a procedure file. `[engineer-verified]`
- **A `CHANGELOG.md` entry.** `[Unreleased]` is this repo's user-facing record for stow consumers, and this change alters no behavior a consumer can observe on `git pull` — the same contributor-facing-only exclusion `.claude/plans/discovery-audit-remediation-plan.md:115` applies to its Phase 9.
- **A test asserting no `attribution` key appears in either settings.json.** Considered, because this repo's convention is to enforce a new convention in the same PR. Rejected: §40 records a rationale, not an invariant. If the review-comment gap closes and the two bugs are fixed, setting `attribution` becomes a legitimate future choice, and a test would then be a tripwire against the correct decision rather than a guard against a wrong one. The entry's own Revisit paragraph is the right instrument for a rationale whose validity is time-bounded.
- **Resolving the #65657 / #77830 discrepancy** by reading full comment threads, searching for a fix commit, or reproducing either report locally. The conclusion does not depend on which report is right, and the entry says so.
- **Any change to how commit-trailer text is produced,** including at the account or session level. That text originates outside this repository and the repo has no committed stake in it today.
