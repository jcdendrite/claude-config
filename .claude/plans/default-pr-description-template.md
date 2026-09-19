# Default PR description template

## Context

Give the `pr-description` skill a grounded default section template to fall
back on when the target repo has no `.github/PULL_REQUEST_TEMPLATE.md` of
its own, replacing today's bare two-heading fallback. Today, absent a repo
template, `claude-skills/skills/pr-description/SKILL.md`'s only structural
guidance is "Absent one, use `## Summary` and `## Test plan`" — everything
else about how a PR body is organized is left to per-run judgment, which the
engineer has observed produces inconsistent structure across repos. This
matters now because the engineer wants that judgment replaced with a
template grounded in verified best-practice evidence, extending this skill's
own existing precedent: GH-477 already grounded the skill's *content* rules
(What/Why, first-line-stands-alone, no commit-list, Test-plan-as-results) in
Google's "Writing good CL descriptions," but never extended that discipline
to the section skeleton itself. The intended outcome is a new co-located
runtime reference file holding the default section template (parallel to
`plan-review/ROUTING.md`'s "load-on-demand runtime reference" pattern),
wired into `SKILL.md`'s existing template-selection bullet.

This plan went through two research passes: a first, narrower pass (6
sources) that filtered to a strict "≥2-source convergence or it doesn't
count" cutoff, followed by a broader 21-source survey spanning official-org
guidance, OSS project templates, a canonical foundational doc, an essay, and
three empirical papers. The decisions below reflect the broad survey and
supersede an earlier scope (What/Why + Test plan + Screenshots + a mandatory
"feedback requested" line) that the engineer rejected as too narrow and,
separately, as having been squeezed to fit a line-cap constraint that then
got used to re-litigate the storage decision — see the broad survey's full
convergence tally and "honest read" for the underlying evidence.

### Decisions already made (engineer, this session)

1. **Template scope**, per the 21-source convergence tally: the default
   template covers `## Summary` (What/Why, near-universal across sources,
   10/21 each), `## Test plan` (testing instructions, 5/21), a conditional
   `## Screenshots` (6/21, "when applicable" in every source that mentions
   it), an "Alternatives considered" element (2/21: Shopify, Rails), and a
   "Context for an unfamiliar reviewer" element (2/21: Shopify, Linux
   kernel — write as though the reviewer has little to no context).
   Explicitly excluded: a pre-submit checklist (3/21, not requested),
   changelog entry (3/21, "most of the time not relevant" per engineer),
   and AI-assistance disclosure (1/21, K8s-only — already covered by this
   repo's own commit/PR attribution lines, so a second disclosure mechanism
   is redundant).
2. **Issue/ticket linkage is not a new template heading.** It's the
   single most-cited element in the survey (12/21) but this skill already
   covers it via its existing closing-keyword bullet (`Fixes <TICKET-ID>`)
   — that bullet is the canonical home; the template doesn't restate it.
3. **Heading-based structure**, not Google's prose-only dissent. Google's
   own eng-practices doc is the one source arguing against fixed headings
   in favor of what+why prose; every other org source that ships a
   template uses labeled headings. This is a deliberate majority-convention
   choice, made with the dissent on record rather than by silent vote.
4. **Reviewer-feedback-type is deferred, not included.** It's the MSR 2026
   paper's single strongest empirical finding (only 3/21 sources mention
   the element at all), but a mandated literal reader-facing question was
   already rejected once (engineer: "We're not having PRs be formatted
   contingent on each reviewer"), and an optional-aside framing (à la
   Kubernetes' "Special notes for your reviewer") would require its own
   verify-sources pass on what guidance an *AI* PR-author should give about
   desired feedback type — a human author's judgment call doesn't transfer
   directly. Out of scope for this PR; a candidate follow-up.

### Storage — resolved (engineer, this session)

**Co-located file** (`pr-description/DEFAULT_TEMPLATE.md`), not inline in
`SKILL.md`, and not by raising `SKILL.md`'s length cap. A first `plan-architect`
pass recommended inline + a cap raise, reasoning that a co-located auxiliary
file needs purpose-built gating hooks (`require-routing-read.sh`,
`log-routing-read.sh`, the only precedent in this repo — `plan-review/ROUTING.md`)
to be read reliably, and that this skill has no comparable chokepoint. The
engineer rejected this: `SKILL.md`'s existing "Section structure from the
repo's template" bullet (lines 45-49) *already* conditionally reads an
external template file — `.github/PULL_REQUEST_TEMPLATE.md` — via a plain
instruction ("read it and use its headings"), with no hook machinery at all.
That is the same chokepoint plan-architect said this skill lacked. A default
template is the same kind of artifact as a repo-supplied one (a structural
skeleton, not behavioral instruction) and belongs the same place: replace
"Absent one, use `## Summary` and `## Test plan`" with a symmetric
instruction to read the co-located default file instead. This also avoids
the cap question entirely — the new bullet is a short pointer, not the
template's full content, so `SKILL.md`'s line budget barely moves and no
cap raise is needed.

Still needs a real decision, not a skip: the registry fan-out across
`select-tests.py`'s `_is_skill_auxiliary_md_change`,
`test_skills.py::_citation_sources_for_skill_md` and `::_all_doc_paths`,
`require-skill-review.sh`'s `ROUTING_PATHSPEC`, `marker.sh`'s
`SKILL_REVIEW_PATHSPECS`, `check-skill-length.sh`'s staged-path pattern, and
`docs/skills.md`'s "two roles"/"ROUTING.md is the exception" language. Two of
those registries carry a comment scheduling a shared-constant refactor for
exactly this event (a third co-located auxiliary filename type) — this is
the anticipated case, not an exotic one.

## Approach

Add `claude-skills/skills/pr-description/DEFAULT_TEMPLATE.md` holding the
five-heading default skeleton, and replace `SKILL.md`'s "Absent one, use
`## Summary` and `## Test plan`" clause with a symmetric instruction to read
it — the same plain-instruction shape that already reads a repo's own
`.github/PULL_REQUEST_TEMPLATE.md`, with no gating hook. The edit is
line-neutral by construction (five wrapped lines replaced by five), which
matters because `pr-description/SKILL.md` is at exactly 210 lines against a
210-line cap: any growth at all would be denied at commit, so "no cap raise
needed" is a hard requirement here, not a convenience.

`DEFAULT_TEMPLATE.md` is self-demonstrating: its own `##` headings *are* the
template, in the confirmed order, with one line of instruction under each.
`## Summary` and `## Test plan` carry only a citation back to `SKILL.md` §
"What the body must carry", which already owns their content rules; the
three conditional headings carry their own one-line rules, because nothing
owns them today. The conditional-omission rule is stated once for all three
rather than repeated per heading.

The registry fan-out resolves into three code changes and three prose
changes, with four candidate sites declined on stated grounds (assumption
ledger rows 8-13 below). The two registries carrying a pre-committed "a
shared constant would be warranted if a third auxiliary filename type is
ever added" comment get that constant — this is the event those comments
named.

**Mechanisms**

- **Plain read instruction, no read-gate hook** — anchors: row3. `ROUTING.md`'s
  hooks exist because its read is *elected mid-task* at a spawn decision
  point the model can reach without it; this read is one branch of an
  instruction the skill must execute to produce any body at all, and its
  sibling branch already works unhooked.
- **`${CLAUDE_SKILL_DIR}/DEFAULT_TEMPLATE.md`, not a `~/.claude/...` literal**
  — anchors: row4. The harness-substituted variable is the documented
  self-reference form and resolves identically under every
  `CLAUDE_CONFIG_DIR` account; a literal home path would break non-personal
  accounts and trip `test_skill_body_has_no_state_path`'s sibling contract.
- **Shared filename constant in a new sibling module** — anchors: row7.
  Lighter primitives checked and rejected: (a) generalize
  `select-tests.py`'s predicate to "any non-`SKILL.md` `.md`" — rejected,
  `test_non_skill_auxiliary_file_under_skills_is_unmatched_and_falls_open`
  deliberately pins an unregistered skill-directory `.md` to fall open to
  the full suite, so generalizing deletes a tested governance tripwire; (b)
  put the constant in `plugins/skill-management/scripts/`, which already
  has a cross-domain row — rejected, that directory is on pytest's
  `pythonpath` but not on `select-tests.py`'s runtime `sys.path`, so the
  script itself could not import it without `__file__`-relative path
  surgery.
- **No `limit_for()` override; the new file falls to the 200-line default**
  — anchors: row6. Adding the path to the staged-path pattern needs one
  regex alternation and no new justified constant; the 200 default is the
  repo's own documented ceiling for skill-shipped content, so it closes the
  "extract to route around the cap" hole without inventing a number.
- **Attribution entirely in `REFERENCES.md`** — anchors: row2.
  Platform-genericness keeps named-org citations out of any stowed global
  runtime file, and `REFERENCES.md` is the established edit-time home for
  exactly this.

### Assumption ledger

```
Root: a PR body authored where the target repo ships no template is
structured by per-run judgment, so structure varies run to run across
repos — the fix is a grounded default skeleton stored where the skill
already knows how to read a skeleton from.

Givens: neither `gh pr create --body-file` nor `--body` applies
`.github/PULL_REQUEST_TEMPLATE.md` — GitHub applies it only in the web
compose view — beyond reach: vendor-imposed, already recorded in
pr-description/REFERENCES.md § "Tool behavior the skill depends on".
Givens: SKILL.md has no `includes:`/`import:`/`extends:` field and `@path`
is CLAUDE.md-only, so a co-located file is reachable only by an explicit
Read at runtime — beyond reach: imposed by the skill file format.
Givens: `${CLAUDE_SKILL_DIR}` substitution is harness-provided — beyond
reach: this plan consumes it and cannot change its semantics.

Row 1 [assumption]: storage is a co-located file, not inline in SKILL.md
and not a cap raise, because the repo-template bullet is an
already-working chokepoint of exactly this shape [engineer-verified] —
anchors: root
Row 2 [assumption]: template scope is the five elements (Summary,
Screenshots, Context for the reviewer, Alternatives considered, Test
plan) in that order, middle three conditional; checklist/changelog/
AI-disclosure excluded; issue linkage stays on the existing
closing-keyword bullet; heading-based over the prose-only dissent;
reviewer-feedback-type deferred [engineer-verified] — anchors: root
Row 3 [assumption]: pr-description/SKILL.md line 45-49 already reads an
external template file by plain instruction and that works today
[engineer-verified] — anchors: root
Row 4 [assumption]: `${CLAUDE_SKILL_DIR}` is the repo's existing
self-reference form, with exactly two sites today: plan-review/SKILL.md:250
and error-handling/SKILL.md:158. A third self-reference site is
in-convention [verified: both files read this session] — anchors: row3
Row 5 [assumption]: pr-description/SKILL.md is exactly 210 lines and
check-skill-length.sh::limit_for caps it at 210; the gate denies only when
over-limit and longer than the committed version, so a net-zero edit
passes and a +1 edit denies [verified: line count via ripgrep over the
file; cap read at check-skill-length.sh:105-106] — anchors: row1
Row 6 [assumption]: the five-line-for-five-line replacement is net zero,
so no cap change is needed and none is requested [verified: replacement
text drafted and wrapped against the file's existing wrap width] —
anchors: row5
Row 7 [assumption]: both select-tests.py::_is_skill_auxiliary_md_change
(line 273-279) and test_skills.py::_citation_sources_for_skill_md (line
3011-3023) carry a comment pre-committing to a shared constant "if a
third auxiliary filename type is ever added" [verified: both comments
read this session] — anchors: root
Row 8 [assumption]: neither require-skill-review.sh::ROUTING_PATHSPEC nor
marker.sh::SKILL_REVIEW_PATHSPECS gains the new path — docs/skills.md's
own gate criterion is "always-loaded context budget or route dispatcher
decisions"; a section skeleton carries neither, where ROUTING.md routes
every specialist spawn [verified: docs/skills.md:136; skill-review/
SKILL.md:131 and 192-199] — anchors: row3
Row 9 [assumption]: test_skills.py::_all_doc_paths does not gain the new
path — its corpus is prose docs plus edit-time REFERENCES.md; ROUTING.md
is deliberately absent, and widening would double-scan files
_citation_sources_for_skill_md already covers [verified: _all_doc_paths
at test_skills.py:4947-4971; _all_citation_extraction_doc_paths at
4974-4999] — anchors: row7
Row 10 [assumption]: ~/.claude/skills is a single folded directory symlink
into claude-skills/skills/, so a new file under an existing skill
directory goes live for every stow consumer on git pull with no
./install.sh re-run [verified: install.sh:47-51 and 232-251] — anchors:
row6
Row 11 [assumption]: pre-merge, `${CLAUDE_SKILL_DIR}` resolves into the
main checkout, not this worktree, so the live end-to-end check needs the
worktree path hand-substituted [verified: same install.sh symlink target
as row10] — anchors: row10
Row 12 [assumption]: every .md under claude-skills/skills/ today is
SKILL.md, REFERENCES.md, or ROUTING.md, so extending the allowlist to a
third name changes no existing path's selection [verified: glob over
claude-skills/skills/**/*.md] — anchors: row7
Row 13 [assumption]: docs/design-decisions/no-shared-skill-partials.md:7
currently states a runtime co-located auxiliary is "a last-resort
exception requiring that level of hook enforcement" — an ungated second
one contradicts the repo's own record unless amended in the same PR
[verified: file read this session] — anchors: row8
Row 14 [assumption]: the conditional headings' failure mode (heading kept,
stuffed with a placeholder or invented alternative) is already covered by
SKILL.md's existing reader-coherence markers, so no new check line is
needed [verified: SKILL.md:129-137] — anchors: row6
Row 15 [unverified]: the 21-source survey's URLs, quotes, and per-element
convergence counts must be transcribed into REFERENCES.md from the
"Evidence for REFERENCES.md" appendix at the end of this plan file, not
invented — anchors: row2
Row 16 [unverified]: an agent reliably follows the read instruction in
practice — row 3 is the engineer's direct observation of the sibling
branch, not a measured guarantee, and no mechanism catches a skipped read
— anchors: row3
```

## Critical files

Single `code-writer` dispatch — the file sets interlock (the `SKILL.md`
pointer, the file it points at, and the three registries that must
recognize the new filename all have to land together), so nothing here
partitions into independently specifiable sets.

**Create**

- `claude-skills/skills/pr-description/DEFAULT_TEMPLATE.md` — the template.
  Shape: an H1, a two-paragraph preamble (what this file is + "the prose
  under each heading is instruction, never text to copy"; then the
  conditional-omission rule naming the middle three headings once), then
  the five `##` headings in order. `## Summary` and `## Test plan` bodies
  are a single citation each — `` `SKILL.md` § "What the body must carry" ``
  plus the bullet's own name in prose — and state no content rules of their
  own. `## Screenshots`: before/after images or a short clip of a surface a
  reader can look at; omit for a change that renders nothing.
  `## Context for the reviewer`: what a reviewer with no prior exposure to
  this area needs and the diff does not show — the constraint that forced
  the shape, the surrounding subsystem, the behavior being replaced — plus
  one clause routing the caller's own `$ARGUMENTS` account here when it is
  background rather than what-and-why (new routing against `SKILL.md`'s
  "The caller's context, folded in" bullet, not a restatement).
  `## Alternatives considered`: each approach actually weighed during the
  work with its one-line set-aside reason, never a reconstructed one. No
  vendor names, no URLs, no pointer to `REFERENCES.md` (runtime file, wrong
  altitude). Target ~35-40 lines.
- `claude/.claude/scripts/_skill_auxiliary_files.py` — new sibling module
  exporting `SKILL_AUXILIARY_MD_NAMES: tuple[str, ...] = ("REFERENCES.md",
  "ROUTING.md", "DEFAULT_TEMPLATE.md")`. Underscore prefix matches the
  directory's existing helper convention (`_config.py`, `_config_dir.py`).
  Docstring must enumerate both consumer categories by name —
  `select-tests.py`'s domain predicate and `test_skills.py`'s
  citation-sibling expansion — since a cross-package importer is not
  discoverable from this directory.

**Modify**

- `claude-skills/skills/pr-description/SKILL.md` — lines 45-49 only.
  Replace the final clause; keep the first three-and-a-bit lines
  byte-identical:

  ```
  - **Section structure from the repo's template.** If the repo has
    `.github/PULL_REQUEST_TEMPLATE.md`, read it and use its headings — neither
    `gh pr create --body-file` nor `--body` applies the template, so it is
    honored only by reading it here. Absent one, read
    `${CLAUDE_SKILL_DIR}/DEFAULT_TEMPLATE.md` and use its headings the same way.
  ```

  Five lines in, five lines out. **The file must stay at exactly 210
  lines** (row 5). No other edit to this file.
- `claude-skills/skills/pr-description/REFERENCES.md` — new section for the
  section-template grounding, transcribed from this plan's "Evidence for
  REFERENCES.md" appendix below: the 21-source survey (source names +
  URLs), the per-element convergence tally, the explicit note that
  Google's eng-practices doc is the lone prose-only dissent and the choice
  was majority-convention with the dissent on record, the four excluded
  elements with reasons, and the deferred reviewer-feedback-type element
  with its MSR 2026 provenance. Reuse: the file's existing "Primary source
  for the authoring standard" / "Sources checked and set aside" headings
  are the right homes; extend rather than start a parallel structure.
- `claude/.claude/scripts/select-tests.py` — import
  `SKILL_AUXILIARY_MD_NAMES` alongside the existing `_config`/`_config_dir`
  sibling imports; `_is_skill_auxiliary_md_change` checks membership in it;
  replace the "must stay in sync with … a shared constant would be
  warranted" comment with a one-line pointer to the module. Add a
  hand-written cross-domain row mapping the new module to
  `SKILLS_TESTS_DIR` (with its rationale line in the block comment above
  `CROSS_DOMAIN_RULES`) — `TestCrossDomainReadCompleteness` resolves
  path-constant reads, not imports, so this dependency is invisible to it.
  Reuse: `_is_skill_management_or_evals_change` is the precedent for
  exactly this import-shaped cross-domain row.
- `claude-skills/skills/tests/test_skills.py` — import the same constant
  (`claude/.claude/scripts` is already on pytest's `pythonpath`);
  `_citation_sources_for_skill_md` iterates it instead of a literal tuple;
  update its docstring (currently says "the two co-located auxiliary
  files") and drop the sync comment. Leave `_all_doc_paths` untouched (row
  9).
- `claude/.claude/hooks/check-skill-length.sh` — add
  `|^claude-skills/skills/pr-description/DEFAULT_TEMPLATE\.md$` to
  `_lib_staged_length_gate`'s staged-path pattern. **No `limit_for()`
  entry** — 200-line default. Update the path-prefix comment block above
  the call (currently ends "the single hardcoded plan-review/ROUTING.md
  exception") to name both hardcoded paths and note the new one takes the
  default rather than an override.
- `docs/skills.md` — two edits. Around line 127: add
  `pr-description/DEFAULT_TEMPLATE.md` as a second runtime-auxiliary
  example (the "two roles" framing itself stays correct — roles, not
  files). After line 130: a new bullet establishing the second class, e.g.
  a runtime auxiliary holding a self-contained data artifact the skill
  consumes — not instruction text extracted from the body — is not that
  last-resort exception and needs no read-gate hook; cap pressure on the
  skill body never licenses one either way.
- `.claude/rules/skill-and-agent-self-review.md` — "`REFERENCES.md`
  (edit-time) and `plan-review/ROUTING.md` (runtime) are the two in use"
  becomes three, naming `pr-description/DEFAULT_TEMPLATE.md`. Editing this
  file routes `ai-instruction-and-memory-files` via `/code-review`'s
  rule-file dispatch.
- `docs/design-decisions/no-shared-skill-partials.md` — amend the final
  paragraph (row 13). Keep the `ROUTING.md` narrative intact as the
  preserved record it is; append the discriminator: the hook enforcement
  `ROUTING.md` required attaches to a read *elected mid-task at a decision
  the model can reach without it*, not to co-located runtime files as a
  class. State explicitly, alongside that discriminator, why
  `DEFAULT_TEMPLATE.md`'s content is itself genuinely load-bearing — a
  five-heading skeleton with a per-heading rule the skill has nowhere else
  to state, not a compressible bullet list — so the addition reads as
  meeting this file's own "genuinely load-bearing and can't be shortened"
  bar on its own terms, not only as avoiding the read-gate question.

**Tests**

- `claude/.claude/hooks/tests/test_check_skill_length.py` — one pair
  mirroring `test_plan_review_routing_md_uses_override`/
  `..._over_override_denies`, asserting `DEFAULT_TEMPLATE.md` falls to the
  **200** default (at/under and growing → allow; over 200 and growing →
  deny).
- `claude/.claude/scripts/tests/test_select_tests.py` — add
  `test_skill_default_template_md_change_selects_skills_tests` beside the
  `REFERENCES.md`/`ROUTING.md` cases, and update
  `test_non_skill_auxiliary_file_under_skills_is_unmatched_and_falls_open`'s
  docstring, which enumerates "neither SKILL.md, REFERENCES.md, nor
  ROUTING.md". Check whether the new cross-domain row must also be
  declared in `TestCrossDomainReadCompleteness`'s hand-maintained audit
  list.
- `claude-skills/skills/tests/test_skills.py` — add a
  `default-template-md-sibling-is-scanned` case to the synthetic-corpus
  parametrize block that already carries `routing-md-sibling-is-scanned`.
- `claude-skills/skills/tests/test_skills.py` — one new test pinning the
  wiring end to end: `pr-description/SKILL.md` contains the literal
  `${CLAUDE_SKILL_DIR}/DEFAULT_TEMPLATE.md`, the file exists, and its five
  `##` headings appear in the confirmed order.

## Verification

- `.venv/bin/python3 claude/.claude/scripts/select-tests.py` — the diff's
  paths map to `SKILLS_TESTS_DIR`, `HOOKS_TESTS_DIR`, and `SCRIPTS_TESTS_DIR`
  with no unmatched-path fallback. Do not widen to the full suite by hand.
- `.venv/bin/ruff check claude/.claude/ claude-skills/` — covers the new
  module and both edited test files.
- `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck` — covers
  the `check-skill-length.sh` regex edit.
- **Line-count assertion before staging:** `pr-description/SKILL.md` must
  still be exactly 210 lines. A +1 result means the replacement text needs
  rewrapping, not a cap change.
- `/skill-review` on the staged `SKILL.md` diff — hook-enforced;
  `require-skill-review.sh` blocks the commit without a matching marker.
  `DEFAULT_TEMPLATE.md` is outside the marker pathspec by design (row 8),
  but the reviewer sees it in the branch diff.
- `/plan-review` on this plan, then `/code-review` before the commit, with
  `ai-instruction-and-memory-files` dispatched for the `.claude/rules/` and
  `docs/design-decisions/` edits.
- **Behavioral, pre-merge:** run `/pr-description` in author mode against a
  scratch repo with no `.github/PULL_REQUEST_TEMPLATE.md`, hand-substituting
  this worktree's path for `${CLAUDE_SKILL_DIR}` (row 11). Confirm all five
  headings appear in order, that `## Screenshots` is omitted rather than
  stubbed for a change with no visible surface, and that
  `## Alternatives considered` is omitted rather than reconstructed.
- **Behavioral, post-merge:** repeat without the substitution, confirming
  the stowed path resolves for a consumer who only ran `git pull` (row 10).

## Out of scope

- **Reviewer-feedback-type element** — deferred per decision 4; needs its
  own `verify-sources` pass on what an AI PR author should say about
  desired feedback type, which does not transfer from human-author
  guidance.
- **Pre-submit checklist, changelog entry, AI-assistance disclosure, and
  issue/ticket linkage as headings** — excluded per decisions 1 and 2;
  linkage stays on the existing closing-keyword bullet, its canonical home.
- **Raising `pr-description/SKILL.md`'s 210-line cap.** Settled; the edit
  is line-neutral instead.
- **Hook-gating `DEFAULT_TEMPLATE.md` reads**, and adding it to
  `require-skill-review.sh`/`marker.sh`/`skill-review/SKILL.md` — declined
  on `docs/skills.md`'s own gate criterion (row 8).
- **Adding `ROUTING.md` or `DEFAULT_TEMPLATE.md` to `_all_doc_paths()`'s
  per-account-state-path corpus.** A pre-existing gap covering both files;
  closing it for one would leave an incoherent corpus. Raise as its own
  issue.
- **`claude-skills/skills/pr-description-claude-config/SKILL.md`** — the
  project layer is untouched; nothing in this change affects closing-keyword
  linking.
- **Restating or revising `SKILL.md`'s What-and-why or Test-plan-of-results
  content rules.** They stay the single source of truth;
  `DEFAULT_TEMPLATE.md` cites them.
- **Generalizing `select-tests.py`'s auxiliary predicate to any
  non-`SKILL.md` markdown** — rejected as deleting a tested tripwire
  (`test_non_skill_auxiliary_file_under_skills_is_unmatched_and_falls_open`).

## Evidence for REFERENCES.md (transcribe at implementation time)

This section exists only to carry the `verify-sources` survey results from
this planning session's conversation into a durable file — it is scratch
material for the `code-writer` dispatch, not prose intended to read well on
its own. Delete or trim this appendix once `REFERENCES.md` has absorbed it.

### Sources

1. Google, "Writing good CL descriptions" — official-org. Argues against
   rigid headings, for prose stating what+why with a bug/issue reference
   line. The one source opposing a heading-based template.
2. Microsoft, Code With Engineering Playbook —
   https://microsoft.github.io/code-with-engineering-playbook/code-reviews/pull-request-template/
   — 8-part template: Work Item ID → Description (what/impact/solution) →
   Steps to Reproduce Bug (bug-only, template says delete for non-bug PRs)
   → PR Checklist → Breaking-change flag+migration → Testing (OS, test
   sets, scenarios) → Logs/Outputs → Additional info/known
   dependencies/TODOs.
3. GitHub blog, "How to write the perfect pull request" —
   https://github.blog/developer-skills/github/how-to-write-the-perfect-pull-request/
   — no rigid template; purpose+why with links; explicit ask for desired
   feedback type; "[WIP]" prefix; @mention with rationale.
4. Atlassian, "The (written) unwritten guide to pull requests" —
   https://www.atlassian.com/blog/git/written-unwritten-guide-pull-requests
   — explicitly guidelines not rules; screenshots for frontend;
   useful/summary title; group related files for reviewer navigation.
5. GitLab, Development docs — Merge Request Workflow —
   https://docs.gitlab.com/development/contributing/merge_request_workflow/
   — clear title+description; setup/steps needed for reviewers (e.g.
   feature flags); before/after screenshots for UI; changelog entries "if
   necessary"; issue links as full URLs.
6. Chromium, contributing.md —
   https://chromium.googlesource.com/chromium/src/+/main/docs/contributing.md
   — Summary (one line) + longer description (why, context, previous vs.
   new behavior); `Bug:` footer; optional `Test:` footer.
7. Shopify Engineering, "On the Importance of Pull Request Discipline" —
   https://shopify.engineering/on-the-importance-of-pull-request-discipline
   — summaries answer what + "why this way"; write as though reviewer has
   little to no context; diagrams (mermaid.js) for complex changes;
   document alternatives considered and justify the chosen one; bug fixes
   detail background/how caught/resolution.
8. GNOME Handbook, Commit Messages —
   https://handbook.gnome.org/development/commit-messages.html — commit-
   message genre, not PR-description genre: summary line, why-focused
   body, issue links.
9. Google Blockly, "Write a good PR" —
   https://docs.blockly.com/guides/modify/contribute/write_a_good_pr —
   thin; defers to repo template; concise/complete; warns AI-drafted
   descriptions run long.
10. Creative Commons, PR guidelines —
    https://opensource.creativecommons.org/contributing-code/pr-guidelines/
    — use repo's PR template fully; detail over brevity; describe how
    tested; preview-check rendering.
11. opensource.guide (GitHub), how-to-contribute.md — explain changes + why
    valuable; reference issues; before/after screenshots for HTML/CSS.
12. Kubernetes community guide, pull-requests.md —
    https://github.com/kubernetes/community/blob/master/contributors/guide/pull-requests.md
    — WIP prefix; track remaining TODOs as a checklist; "Special notes for
    your reviewer" section for large/generated diffs; AI-disclosure
    requirement (single-source, 2026-era).
13. Kubernetes PULL_REQUEST_TEMPLATE.md — bare `Fixes <issue number>` +
    SIG-routing; no rationale.
14. Rails, .github/pull_request_template.md — Motivation/Background (why,
    issue link) → Detail (what) → Additional information (benchmarks,
    alternatives, references) → Checklist (single-topic scope, tests,
    CHANGELOG).
15. Automattic, wordpress-activitypub/docs/pull-request.md (single-repo) —
    issue link, proposed changes, testing instructions, changelog,
    screenshots, title+summary.
16. thoughtbot, guides/code-review/README.md — no PR-description-content
    guidance found.
17. Linux kernel, "Submitting patches" —
    https://docs.kernel.org/process/submitting-patches.html — email-patch
    genre, not GitHub PR: describe the underlying problem; describe
    user-visible impact; back performance/memory/size claims with numbers;
    why matters more than what.
18. Gergely Orosz, The Pragmatic Engineer, "Pull Request (or Diff) Best
    Practices" —
    https://blog.pragmaticengineer.com/pull-request-or-diff-best-practices/
    — opinion tier: short expressive title; clear "why" section; link
    tracking task; before/after screenshots/gifs for client-side changes.
19. arXiv 2602.14611 / MSR 2026, "The Value of Effective Pull Request
    Description" — mixed-methods, 80,000 PRs across 156 projects + 64-dev
    survey. Purpose/rationale preserves change history; stating desired
    feedback type is the single strongest predictor of change acceptance
    and reviewer engagement in the study, despite being rare in vendor
    templates. Do not quote a specific effect-size figure from this paper
    in REFERENCES.md — treat any number from it as unverified paraphrase,
    not a verbatim finding (ACM DL fetch returned HTTP 403; content
    reached this session via a fetch tool's summarizing pass over the
    arXiv preprint, not direct reading of decoded text).
20. Bacchelli & Bird, "Expectations, Outcomes, and Challenges of Modern
    Code Review" (ICSE 2013) — background only, not a content taxonomy;
    do not cite as a source for a specific template element.
21. Sadowski et al., "Modern Code Review: A Case Study at Google" (ICSE-SEIP
    2018) — fetch returned corrupted/binary text; do not cite.

### Convergence tally (N/21, no pre-filtering)

Issue/ticket link 12; what-changed 10; why/motivation 10; screenshots-when-
applicable 6; testing instructions 5; pre-submit checklist 3;
desired-feedback-type 3; changelog 3; title clarity 3; alternatives-
considered 2 (Shopify, Rails); context-for-unfamiliar-reviewer 2 (Shopify,
Linux kernel); setup/repro steps 2 (conditional); WIP flag 2;
additional-info/follow-ups 2; breaking-change flag 1; quantified perf
numbers 1; AI-disclosure 1; diagrams 1; related-file grouping 1.

### Quotes the rules rest on

- **`## Screenshots` (conditional):** Atlassian — "Add some screenshots for
  your front-end changes!"; GitLab — "before/after screenshots" for UI
  changes.
- **`## Alternatives considered` (conditional):** Shopify — "when multiple
  solutions existed, document the main solution justified"; Rails —
  "Additional information" heading includes "alternative solutions,
  references."
- **`## Context for the reviewer` (conditional):** Shopify — "write as
  though the reviewer has little to no context"; Linux kernel — "there
  must be an underlying problem that motivated you to do this work,"
  "describe user-visible impact."
- **Heading-based structure over prose-only:** every org source that ships
  a template (Microsoft, GitLab, Rails, Chromium, Kubernetes) uses labeled
  headings; only Google's "Writing good CL descriptions" argues for prose.

### Sources checked and set aside

- **thoughtbot** — no PR-description-content guidance found.
- **Google Blockly** — too thin to ground any specific element; defers to
  repo template.
- **GNOME Handbook, Linux kernel** — commit-message/email-patch genres,
  adjacent but not GitHub PR-description guidance; cited for background
  only where noted above.
- **Bacchelli & Bird (ICSE 2013), Sadowski et al. (ICSE-SEIP 2018)** — not
  usable as element-level citations (see notes above).
- **Pre-submit checklist, changelog entry, AI-assistance disclosure** —
  each cited by ≥1 source but excluded per the engineer's scope call
  (checklist not requested; changelog "most of the time not relevant";
  AI-disclosure redundant with this repo's own commit/PR attribution
  lines).
- **Reviewer-feedback-type** — the MSR 2026 paper's strongest empirical
  finding, deferred rather than included; see Out of scope.
