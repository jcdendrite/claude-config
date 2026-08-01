# Wire the durable-comment rule into the checklists that are supposed to enforce it, and add the length dimension it's missing

## Context

**Goal:** make the existing "durable code comments, not PR narration" rule actually catch violations before they ship, and close the one dimension it never covered — length/verbosity — while leaving the general PR-description-verbosity question unresolved rather than acting on weak evidence.

The engineer suspected two things but had no evidence: (1) that `CLAUDE.md`'s "Code Comments, Documentation, and Prose" section isn't actually stopping verbose/non-durable comments in practice, and (2) that PRs and comments in this ecosystem are generally more verbose than useful, with no way to tell whether that verbosity pays for itself. Investigation this session (via `error-mode-analysis`, direct file reads, and a cross-repo `git log` sweep) confirmed the first suspicion with a specific, fixable root cause, and found the second genuinely underdetermined — not something to guess at.

## Approach

### What the investigation found

**The rule exists but isn't wired into either checklist that's supposed to apply it.** `code-writer.md`'s baseline (`:55`) and self-review pass (`:81`) both instruct the agent to check its diff against `CLAUDE.md §Engineering Judgment and §Working Style` — and never cite `§Code Comments, Documentation, and Prose`, a third, sibling top-level section that specifically governs comment durability. The same gap exists independently in `code-review/SKILL.md`'s Step 1.5 ("Judgment-activation pass," `:49`), which cites the identical two-section pair and has three tripwire bullets, none matching "a comment narrates PR/incident history." A currently-open, not-yet-implemented PR (#520) already root-caused the `code-review/SKILL.md` half of this via its own `error-mode-analysis` run — a documented incident where three specialist reviewers read a file containing exactly this defect class and flagged nothing, because the checklist they were following never told them to look for it — but left the decision to actually make that edit pending engineer confirmation. That confirmation is given here: this plan makes the fix directly, in both files, since `code-writer.md` and `code-review/SKILL.md` are structural siblings sharing the identical omission (CLAUDE.md's own "audit structural siblings" rule).

**The rule that exists only covers durability, not length.** The current section forbids PR-only terminology and "used to be X" framing — content-permanence rules — but says nothing about how long a comment may run. Real fixes observed this session — one in this repo (a human-reviewer-caught PR, now merged, that trimmed PR-internal terminology and incident-narration prose after a full pipeline pass missed it) and a recurring pattern across multiple independent, unrelated local repositories (verified via `git log --all --grep` across five-plus repos this session) — consistently take the same shape: a multi-paragraph rationale comment condensed to one line naming the non-obvious constraint. One of those same repos also shows the opposite failure: a prior compression pass that cut a real caveat, later restored — evidence that "shorter" isn't automatically better if the compression drops the load-bearing fact. The new bullet added here targets the narration, not the fact.

**No industry standard exists for a comment-length threshold.** Checked this session (WebSearch): SonarSource deprecated its comment-density rule; ESLint core ships no comment-length rule; the one community plugin found (`eslint-plugin-comment-length`) caps characters-per-line at 80 (a wrap width, not a block-height cap), is JS-only, and is one niche plugin, not a standard. A regex/line-count hook was considered and explicitly rejected — see the assumption ledger.

**General PR-description / commit-message / response verbosity: evidence stayed weak after widening the search.** Zero instances in this repo's own transcript-plus-PR-comment history (last ~4.5 weeks of transcripts, ~8 weeks of PRs) of a hook, reviewer agent, or human flagging PR-body or commit-message length specifically. A cross-repo `git log` sweep across every locally-accessible repo (run per the engineer's explicit request to check further before deciding scope) surfaced verbosity-adjacent commits, but on inspection the great majority were false positives from keyword overlap (Kubernetes resource-name length limits, application log-verbosity flags, UI-copy shortening) rather than PR-description or comment-prose findings. The one genuinely on-topic private-repo investigation found (a prior `error-mode-analysis` run scoped to a different repo's PR-description quality) was itself diagnosis-only, proposed no committed fix, and its findings were specific to that repo's own local skills — not evidence of a claude-config-level gap. This plan does not add speculative PR-description or general-verbosity prose on the strength of that. The question stays open for a dedicated future investigation if the problem recurs.

### Assumption ledger

```
Root: the durable-comment CLAUDE.md rule is not mechanically checked by either
checklist meant to enforce it (the authoring agent's self-review, the review
skill's judgment pass), and separately, that rule has no length dimension —
only a content-permanence one.

Row 1 [mechanism, anchors: root]: add §Code Comments, Documentation, and Prose
  citation + a fourth "non-durable comment" tripwire to code-review/SKILL.md
  Step 1.5, matching the shape of its existing three tripwires
  [verified: read code-review/SKILL.md:47-54 this session; PR #520's own
  root-cause investigation independently found the identical citation gap
  and drafted matching tripwire language, reused here with engineer
  confirmation to proceed — "Resolve it here too"].
Row 2 [mechanism, anchors: root]: add the same citation + an equivalent
  tripwire to code-writer.md's baseline (:55) and self-review pass (:81)
  [verified: read code-writer.md this session — the identical omission,
  independently present in a structurally sibling checklist].
Row 3 [mechanism, anchors: root]: add a one-line verbosity bullet ("one line,
  not a paragraph; trim the narration, not the fact") to CLAUDE.md
  §Code Comments, Documentation, and Prose [verified: this repo's own
  merged PR history + git log across 5+ independent local repos, this
  session — recurring pattern of multi-paragraph-to-one-line trims, plus one
  counter-example of over-compression dropping a real caveat, informing the
  bullet's exact wording].
Row 4 [mechanism, anchors: root]: add one clarifying sentence scoping
  §Code Comments, Documentation, and Prose to code comments and durable docs
  only, not PR/commit-message prose (that's `pr-description`'s concern)
  [verified: current section text only mentions "code comments and durable
  in-repo documentation"; a private-repo investigation this session had
  flagged this exact scope question as open and unresolved].

Over-powered-primitive check for Row 3 (per Engineering Judgment):
  (a) A pre-commit hook regex-scanning comment block line count — REJECTED:
      no external standard exists to ground a numeric threshold in (verified
      via WebSearch this session: no SonarQube/ESLint-core/major-style-guide
      rule caps comment *line count*), and the checklist fixes (rows 1-2)
      already target the actual point of failure — a reviewer that reads the
      comment without a rule telling it to check length — so a hook would
      enforce an invented number where a citation fix closes the real gap.
  (b) Relocate the whole CLAUDE.md section under "Engineering Judgment" so
      every existing "§Engineering Judgment" citation inherits it for free —
      REJECTED: only two files in the repo currently cite the
      Engineering-Judgment/Working-Style pair for judgment-activation
      purposes (code-writer.md, code-review/SKILL.md — verified via grep
      across claude/.claude/{agents,skills,rules,hooks} this session); a
      third site, plan-review/REFERENCES.md, cites the same pair but for an
      unrelated heuristic (over-powered primitives), not comments. A
      structural section relocation is a bigger, less reviewable diff for
      the same effect on a population of two call sites.
Row 5 [assumption, engineer-verified]: general PR-description/response
  verbosity gets no prose change this run — evidence stayed weak after
  widening the search to every locally-accessible repo, per the engineer's
  explicit request — anchors: root (deliberately not acted on).
```

## Critical files

**Modify**

- `claude/.claude/CLAUDE.md` — §"Code Comments, Documentation, and Prose". Two changes:
  1. In the section's opening paragraph, after "...must be readable by a future contributor who has not read the PR description, commit message, or planning document." insert: `This section governs comments and durable docs only — PR body and commit-message conciseness is \`pr-description\`'s concern, not this section's.`
  2. Add a fourth bullet after the existing three ("No PR-defined terminology" / "No 'used to be X'" / "Self-test"): `**One line, not a paragraph.** State the non-obvious constraint in a single sentence. A multi-paragraph rationale block is a signal the comment is doing the PR description's job instead of the code's — trim the narration, not the fact: a compressed comment that drops the actual constraint is worse than a verbose one that keeps it.`

- `claude/.claude/agents/code-writer.md` — two edits:
  1. `:55` (baseline), change `let CLAUDE.md §Engineering Judgment and §Working Style actively steer choices: understand the intent of existing code before changing it, ground every choice (timeouts, suppressions, discriminator literals, new dependencies), default-suspect over-powered primitives, and respect scope discipline (Axis 1–4) — surface them at each decision point, not only at self-review.` to `let CLAUDE.md §Engineering Judgment, §Working Style, and §Code Comments, Documentation, and Prose actively steer choices: understand the intent of existing code before changing it, ground every choice (timeouts, suppressions, discriminator literals, new dependencies), default-suspect over-powered primitives, respect scope discipline (Axis 1–4), and write any comment as a one-line durable fact, not PR narration — surface them at each decision point, not only at self-review.`
  2. `:81` (self-review step 6), change `Re-read the diff once more specifically against CLAUDE.md §Engineering Judgment and §Working Style. Flag any unverified external-state claim, out-of-scope file edit, ungrounded timeout/literal, suppression without rationale, or new dependency without provenance research — the same set the code-review skill's Judgment-activation pass checks, applied here before handoff.` to `Re-read the diff once more specifically against CLAUDE.md §Engineering Judgment, §Working Style, and §Code Comments, Documentation, and Prose. Flag any unverified external-state claim, out-of-scope file edit, ungrounded timeout/literal, suppression without rationale, new dependency without provenance research, or a comment that narrates PR/incident history, references "this diff," or re-litigates a rejected alternative at length instead of stating a durable fact — the same set the code-review skill's Judgment-activation pass checks, applied here before handoff.`

  Requires `/agent-review` before commit (per `.claude/rules/skill-and-agent-self-review.md`; dispatcher-invoked but not hook-enforced per `.claude/rules/review-pipeline-dispatch.md`).

- `claude/.claude/skills/code-review/SKILL.md` — Step 1.5 (`:49-54`):
  1. Change the opening sentence from `Evaluate the diff against CLAUDE.md §Engineering Judgment and §Working Style — being loaded is not the same as being applied.` to `Evaluate the diff against CLAUDE.md §Engineering Judgment, §Working Style, and §Code Comments, Documentation, and Prose — being loaded is not the same as being applied.`
  2. Add a fourth tripwire bullet after "Preserved-record edits": `**Non-durable comment** — a new or modified comment that narrates PR/incident history, references "this diff," or re-litigates a rejected alternative at length, rather than stating a durable fact about the code.`

  Requires `/skill-review` before commit — **hook-enforced** (`require-skill-review.sh`).

**Reuse, not reimplement:** the fourth tripwire's language for `code-review/SKILL.md` reuses PR #520's already-drafted, well-scoped wording rather than re-deriving it from scratch.

**Do not touch:** `claude/.claude/skills/pr-description/SKILL.md` (row 5 — no PR-description evidence to act on), `claude/.claude/skills/plan-review/REFERENCES.md` (cites the Engineering-Judgment/Working-Style pair for an unrelated heuristic), any hook file (row 3's over-powered-primitive check rejected a hook-based approach).

## Verification

1. `../../../.venv/bin/pytest claude/.claude/` and `../../../.venv/bin/ruff check claude/.claude/` from the worktree — confirms no frontmatter/structural regression in the edited files.
2. `/agent-review` on the `code-writer.md` diff; `/skill-review` on the `code-review/SKILL.md` diff (hook-enforced on the latter regardless).
3. Manual read-through: for each edited checklist, confirm the new tripwire text actually matches the shape of its siblings (bullet format, specificity) rather than reading as a bolted-on afterthought.
4. Re-read the CLAUDE.md diff once against the section's own new "one line, not a paragraph" bullet — dogfood it: the addition itself must not be a paragraph.
5. `/code-review` before handoff.

## Out of scope

- Any prose change targeting PR-description or general response verbosity (row 5) — deliberately deferred pending stronger evidence, not silently dropped. A future session should run `error-mode-analysis` Steps 5–7 (full artifact split + multi-window trend pass) scoped to PR-description accretion specifically if this recurs.
- A comment-length-scanning hook — rejected in the assumption ledger's over-powered-primitive check, not merely unconsidered.
- PR #520's other three mechanisms (GitHub Actions rule citation grounding, `docs/rules-references.md`, the CI path-filter fail-open→fail-closed fix) — unrelated to comment durability, remain that PR's own scope. Once this plan merges, PR #520 will need to drop its now-redundant Mechanism 1 diff (the `code-review/SKILL.md` edit) on rebase.
