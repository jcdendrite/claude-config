> ## ⚠️ STATUS: IN PROGRESS — NOT REVIEWED, NOT APPROVED, PIVOT PENDING
>
> This plan was captured mid-session for off-machine backup before the engineer
> shut down their machine. It is **not** a finished, reviewed plan — do not
> implement from it as-is. Full continuity detail, including the exact open
> question and every decision that led here, is at
> `~/.claude/handoffs/gha-comment-durability-root-cause-pivot-handoff.md`
> (private, not in this repo). **Read that file first on resume.**
>
> In short: this plan went through two confirmed pivots and has a third,
> **unconfirmed**, proposal awaited from the engineer. Mechanism 1 below (the
> in-rule-file "durable comment" bullet) has been **deleted** per the
> engineer's explicit instruction — restating a general CLAUDE.md rule inside
> a domain-specific rule file is itself the anti-pattern this plan was trying
> to prevent. What replaces it is an open question — see "Mechanism 1
> (pending)" below. Mechanisms 2–4 (citation grounding, rule corrections, CI
> deny-list) are unaffected by this open question and were fully
> plan-reviewed and grounded this session.

# Ground the GitHub Actions rule's citations and give the review pipeline the missing durable-comment check

## Context

**Goal:** make it structurally harder to repeat a specific defect class — code comments that narrate PR/incident history instead of stating durable facts — and, separately, give `claude/.claude/rules/github-actions-workflows.md` verifiable, current sources for its security claims.

A reviewer on a downstream repo caught a new CI workflow file whose comments narrated an incident, referenced "this diff," and argued against a rejected alternative inline — all of which stop meaning anything once the PR merges. That violation is already fixed downstream; the ask is to make recurrence structurally harder.

**Root-cause investigation (this session, via `error-mode-analysis`) found the fix does not belong in the GitHub Actions rule file at all.** The violating commit went through a `/code-review` pass with three specialist reviewers before merging — all three read the violating file closely (one specialist even quoted its own since-removed comment approvingly as supporting evidence for an unrelated finding), and none flagged the comment prose. The reason: `claude/.claude/skills/code-review/SKILL.md`'s "Judgment-activation pass" (Step 1.5) instructs reviewers to evaluate the diff against `CLAUDE.md §Engineering Judgment and §Working Style` only — it never cites `§Code Comments, Documentation, and Prose`, the section that actually governs this defect class, and none of its three existing tripwires cover "a new comment narrates PR/incident history." This is a structural gap in the review pipeline's own instructions, not a reviewer-competence failure, and it would have let the same violation through in **any** file type, not only GitHub Actions workflows.

A separate, independently-motivated finding: `github-actions-workflows.md`'s security claims carry verbatim quotes with **no URLs at all**, so nobody can re-check them in place. Grounding those citations (fetched 2026-07-30, from `docs.github.com` and `github.com`) found real drift — GitHub renamed and moved the primary source, two quotes were truncated mid-sentence, and the rule was missing coverage for `workflow_run` (a trigger as privileged as the one it does name) and OIDC subject-claim scoping. This part of the plan stands on its own regardless of how the durable-comment question resolves.

**Out of scope, per the brief and a mid-session correction from the engineer:** a Notion IAC-standards page belongs in its own brief for a session anchored in a different repo. Nothing here touches Notion.

## Approach

### Mechanism 1 (PENDING — engineer has not yet confirmed)

**What was rejected:** a bullet inside `github-actions-workflows.md` restating the durable-comment standard for a GHA-specific audience. The engineer's explicit correction: the GHA rule file must not repeat general instructions — that's the anti-pattern, independent of how well-scoped the restatement is. Deleted, not narrowed.

**What was proposed as the replacement, awaiting confirmation:** fix the actual point of failure — `claude/.claude/skills/code-review/SKILL.md` Step 1.5 (`:49`). Two changes:
1. Add `§Code Comments, Documentation, and Prose` to the citation alongside the two sections already named.
2. Add a fourth tripwire bullet, matching the shape of the existing three (`unverified-external-state-claim`, `out-of-scope-file-edits`, `preserved-record-edits`): a new or modified comment that narrates PR/incident history, references "this diff," or re-litigates a rejected alternative at length, rather than stating a durable fact about the code.

This is higher blast radius than the deleted bullet — it changes every future `/code-review` invocation, not just GHA-domain reviews — and editing `claude/.claude/skills/*.md` requires `/skill-review` before commit (hook-enforced per `.claude/rules/review-pipeline-dispatch.md`). **Do not implement this without the engineer's explicit confirmation on resume.** If confirmed, draft the exact wording, then run `/skill-review` and a fresh full `/plan-review` (this is a materially different, higher-stakes mechanism than what was reviewed earlier this session) before touching the file. If rejected, ask what replaces it, or confirm the plan proceeds with only Mechanisms 2–4 below and no comment-durability fix in this PR at all.

### 2. `docs/rules-references.md` — a new flat file, not a REFERENCES.md

**A `REFERENCES.md` cannot live in `claude/.claude/rules/` at all.** Claude Code's docs state that in a rules directory "All `.md` files are discovered recursively," and "Rules without a `paths` field are loaded unconditionally and apply to all files" — at "the same priority as `.claude/CLAUDE.md`" (https://code.claude.com/docs/en/memory.md, verified live). A `REFERENCES.md` there would load into **every session for every stow user**. It would also fail this repo's `test_rules_frontmatter.py`, which `rglob("*.md")`s both rules directories (`:33-38`) and requires a non-empty `paths:` list (`:63-68`).

*Lighter primitives considered.* (a) **URLs inline in the rule header** — no new file, but leaves no room for verbatim quotes, fetch dates, or drift notes, and pays that cost in every repo a stow user opens. (b) **A sibling stowed directory** `claude/.claude/rules-references/` — unscanned and test-safe, but ships contributor-only material into every user's `~/.claude/`. `docs/` is the only location where extraction genuinely removes context cost, because it is neither stowed nor loaded.

**Flat file, not `docs/rules-references/<name>.md`.** A subdirectory holding one file advertises a convention this repo isn't completing for the other three rules files (brief forbids that sweep). A single file with one `## GitHub Actions workflows` section gives the others an obvious home later without scaffolding empty structure now.

**No count claims in the file.** Structure as one entry per claim: URL, verbatim source quote, fetch date, drift note where wording moved.

Content otherwise follows the established `REFERENCES.md` shape (n=17 surveyed: no frontmatter, H1, then a one-paragraph "not loaded at runtime — read when editing" opener — four files carry this sentence byte-identical; bare URLs are the dominant form).

**Diagnostic fix at the boundary.** `test_rules_frontmatter.py:64`'s violation message currently reads only `"... frontmatter is missing a \`paths\` key"` — a contributor who drops reference material in a rules directory reads that as "add `paths:`," the exact wrong fix (it would make the file load everywhere). Extend the message to name the resolution: path-scope it as a real rule, or move edit-time reference material to `docs/`.

### 3. Rule-file corrections — each re-grounded, not carried from an earlier draft

Citation research (2026-07-30, `docs.github.com` + `github.com`) found drift in the current rule file, and specialist review found that two *proposed* corrections were themselves ungrounded. All entries below reflect the final, source-verified wording.

| # | Current text | Correction | Grounding |
|---|---|---|---|
| 1 | Header cites "Security hardening for GitHub Actions" | GitHub renamed this doc to **"Secure use reference"**; old paths 301 to `/en/actions/reference/security/secure-use` | Verified via redirect trace |
| 2 | `…increased, as required, for individual jobs.` | Restore full sentence: `…for individual jobs within the workflow file.` | `docs.github.com/en/actions/reference/security/secure-use#use-secrets-for-sensitive-information`, verbatim |
| 3 | `…including from pull request forks.` | Restore full sentence: `…or from repositories that are not under your control.` | Same page, `#good-practices` |
| 4 | `pull_request_target` bullet omits `workflow_run` | Add `workflow_run`, non-optional (see exact replacement below) | See row below |
| 5 | "The hosted-runner implicit default is 360 minutes (6h) if unset" | See rewritten wording below — the runner-type framing in an earlier draft (both this plan's and a reviewer's) was wrong | `docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idtimeout-minutes` + `.../reference/limits`, verbatim |
| 6 | OIDC bullet has no subject-claim guidance | Add a scoped subject-claim clause | `docs.github.com/en/actions/reference/security/oidc`, verbatim |
| 7 | Injection bullet scoped to `run:` only, names 3 fields | Broaden to the `github` context generally, cite GitHub's own suffix heuristic | `docs.github.com/en/actions/concepts/security/script-injections`, verbatim |
| 8 | Header hedge: two claims "not pinned to a single fetched doc URL" | Replace with a pointer to `docs/rules-references.md` — both claims now have canonical URLs | — |

**Correction 5, exact replacement** (an earlier "self-hosted has no platform cap" framing, proposed mid-review, is contradicted by GitHub's own limits table, which caps self-hosted job execution at 5 days, not unbounded):

> **Give every job a real `timeout-minutes` budget.** The default is 360 minutes (6h) regardless of runner type, but the platform backstop that catches a missing or too-high value differs 20x: GitHub-hosted job execution is capped at 6 hours; self-hosted at 5 days. Set the field explicitly, especially on self-hosted jobs, where the platform will not intervene for five days while a runaway or compromised job holds a persistent runner and its credentials.

**Correction 4 + row 4, exact replacement for the `pull_request_target` bullet** (a reviewer-proposed "never a wildcard" OIDC claim, and a `with:`/`if:`-as-sinks framing for injection, were both checked against GitHub's docs and found unsupported or contradicted — see below):

> **`pull_request_target` and `workflow_run` can run with base-repo write access and secrets.** GitHub: these triggers "may have repository write access and access to referenced secrets" — actual scope still depends on the workflow's own `permissions:` block, but the base-repo token/secrets are in context, unlike `pull_request`. `workflow_run`'s context is the default branch's workflow with that same privileged token; its inputs (uploaded artifacts, `github.event.workflow_run.*`) come from the completed run and are attacker-controlled if that run was triggered by a fork PR. GitHub: workflows using these triggers "must not explicitly check out untrusted code, including from pull request forks or from repositories that are not under your control." Use `pull_request` for untrusted contributions instead.

*Why non-optional, not deferred.* GitHub's source sentence covers `pull_request_target` and `workflow_run` identically as privileged triggers. The existing bullet's own closing advice — "use `pull_request` for untrusted contributions instead" — creates the exact gap it leaves unnamed: a reader who needs write access for a fork PR follows that advice, hits `pull_request`'s read-only fork token, and reaches for the one privileged trigger the rule never mentions.

**OIDC bullet, subject-claim clause appended** (dropping the reviewer-proposed "never a wildcard" — GitHub's own AWS how-to ships `repo:octo-org/octo-repo:*` as a supported example, so that claim would misattribute vendor guidance):

> The trust decision moves to the cloud side: GitHub says you "must define at least one condition, so that untrusted repositories can't request access tokens" — but a repo-only condition still grants every branch, tag, and PR in that repo. Pin the narrowest subject the job needs, typically `repo:ORG/REPO:environment:NAME` or `repo:ORG/REPO:ref:refs/heads/BRANCH`.

**Injection bullet, replacement** (dropping `with:`/`if:`/`actions/github-script` as named sinks — GitHub presents `with:` on a JavaScript action as its *recommended safe pattern*; and GitHub no longer publishes an enumerated field list, only a suffix heuristic):

> **Never interpolate untrusted `${{ github.event.* }}` values into a `run:` script or your own composite/custom action.** GitHub treats the whole `github` context as potentially attacker-controlled — fields "typically end with `body`, `default_branch`, `email`, `head_ref`, `label`, `message`, `name`, `page_name`, `ref`, and `title`," plus branch names and email addresses. GitHub's preferred fix, in order: pass the value as an argument to a JavaScript action rather than inlining it; for inline scripts, "set the value of the expression to an intermediate environment variable" and reference `"$VAR"` quoted, not the raw `${{ }}` expression.

**Header, full replacement:**

> Sources verified against GitHub's official docs (2026-07): GitHub's "Secure use reference," the `actions/checkout` README, the OpenID Connect reference guide, and the workflow-syntax and usage-limits references. Full citations, verbatim quotes, and fetch dates live in `docs/rules-references.md` in the claude-config repo — re-confirm there if precision matters.

### 4. CI path-filter — invert to fail-closed, not enumerate

**The 13-path gap, not 2.** Deep verification (real `grep -E` against every test module's path constants under `claude/.claude/hooks/tests/`, `claude/.claude/skills/tests/`, `claude/.claude/scripts/tests/`) found `.github/workflows/tests.yml:71`'s `REGEX` misses 13 distinct paths that tests read: `evals/` (imported via `pyproject.toml` `pythonpath` — the single highest-severity miss), `install.sh`, `claude/.claude/CLAUDE.md`, both rules directories, `.claude/skills/`, `.claude-plugin/marketplace.json`, `plugins/` outside `skill-management/`, `claude/.claude/statusline-command.sh`, `.shellcheckrc`, `scripts/list-shell-files.sh`, `claude/.local/bin/*`, and `plugins/lovable-cloud/scripts/new-migration`.

**Why not enumerate all 13 and add a derivation test.** `REGEX` is an allow-list: any path it doesn't name defaults to `changed=false` and the suite silently skips — fail-open, in the dangerous direction. Full programmatic derivation of "every path a test reads" is not feasible: `test_shellcheck.py` reads every tracked file via `git ls-files -z`, so a mechanically honest ground truth resolves to `.*`; import-time dependencies (`from run_skill_evals import ...`) are invisible to any AST path-walk. A derivation test would be derivation-plus-a-hand-maintained-floor — the same mechanism that already rotted, one layer down.

**The foundation fix: invert to a deny-list.** Replace the allow-list with a small, verified deny-list of paths confirmed unread by any test — everything else defaults to running the suite. Drift can then only cause over-running, never under-running.

**Deny-list, verified by grep against every test file under the three test directories** — only paths confirmed to have zero references were included; `.claude/plans/`, `.claude/worktrees/`, and `agent-reviews/` appear in test source (fixture or real reads, not disambiguated) and were deliberately left **off** the deny-list, so they default to running the suite — the safe direction:

```
SKIP_REGEX='^(LICENSE$|CHANGELOG\.md$|CODE_OF_CONDUCT\.md$|SECURITY\.md$|CONTRIBUTING\.md$)'
```

`changed=true` unless every changed path matches `SKIP_REGEX`. `shell_changed`/`SHELL_REGEX` is untouched — it already has a sound design (`TestCiGateCoversDiscovery` derives its ground truth from the production discovery script `scripts/list-shell-files.sh`) and no gap was found there.

**Guard test, `claude/.claude/hooks/tests/test_ci_path_filter.py`** (new file, mirrors `test_shellcheck.py`'s `TestCiGateCoversDiscovery` shape — extract the pattern from `tests.yml` via `re.search`, shell out to real `grep -E`):
- Assert each of the five deny-list paths matches `SKIP_REGEX`.
- Assert a representative set of the 13 previously-missing paths does **not** match — `install.sh`, `evals/run_skill_evals.py`, `claude/.claude/CLAUDE.md`, `.claude/rules/skill-and-agent-self-review.md`, `claude/.claude/rules/github-actions-workflows.md`, `docs/rules-references.md`, `.claude-plugin/marketplace.json`, `claude/.claude/statusline-command.sh`, `.shellcheckrc`, `scripts/list-shell-files.sh`.

No chicken-and-egg risk: the new test file matches under the *old* `REGEX` and trivially runs under the new `SKIP_REGEX`.

### Assumption ledger

```
Root: comments that narrate PR/incident history instead of durable facts are a
recurring defect class; the GHA rule file's own claims also carried no URLs.

Row 1 [mechanism, SUPERSEDED]: a GHA-specific durable-comment bullet — REJECTED
  by the engineer as itself an anti-pattern (restating a general instruction in
  a domain rule file). Replacement mechanism proposed (fix code-review/SKILL.md
  Step 1.5's missing citation + missing tripwire) but NOT yet confirmed — see
  "Mechanism 1 (pending)" above. Root cause established via error-mode-analysis:
  the violating diff went through /code-review with 3 specialists who read the
  file closely and found nothing, because Step 1.5 never cited the relevant
  CLAUDE.md section and had no matching tripwire [verified: read the original
  commit's own code-review agent-review artifacts in full, this session].
Row 2 [mechanism]: docs/rules-references.md — anchors: root — holds URLs,
  verbatim quotes, and fetch dates outside any loaded surface.
Row 3 [mechanism]: rule-file corrections (citation drift + three security
  content gaps found in review) — anchors: row2.
Row 4 [mechanism]: tests.yml SKIP_REGEX inversion + guard test — anchors:
  root — derivation-plus-enumeration was found infeasible/unsound; corrected
  to a deny-list.
Row 5 [assumption]: any .md in a rules dir without `paths:` loads in every
  session at CLAUDE.md priority [verified: code.claude.com/docs/en/memory.md]
  — anchors: row2
Row 6 [assumption]: test_rules_frontmatter.py rglobs both rules dirs and fails
  a paths-less file [verified: claude/.claude/skills/tests/test_rules_frontmatter.py:33-38,63-68]
  — anchors: row2
Row 7 [assumption]: every corrected/added claim in row3 is confirmed verbatim
  against a current docs.github.com or github.com URL, fetched 2026-07-30 —
  anchors: row3
Row 8 [assumption]: two mid-review-proposed corrections were themselves wrong
  — "self-hosted has no timeout-minutes platform cap" and "OIDC subject must
  never use a wildcard" — both rejected in favor of grounded wording
  [verified: docs.github.com/en/actions/reference/limits, docs.github.com/
  en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws]
  — anchors: row3
Row 9 [assumption]: LICENSE, CHANGELOG.md, CODE_OF_CONDUCT.md, SECURITY.md,
  CONTRIBUTING.md are read by zero test modules; .claude/plans/,
  .claude/worktrees/, agent-reviews/ excluded from the deny-list as ambiguous
  [verified: grep -rl across claude/.claude/{hooks,skills,scripts}/tests/,
  executed this session] — anchors: row4
Row 10 [assumption]: this PR's own file set already triggers pytest under the
  CURRENT regex, so the CI fix is forward-looking only — an earlier ledger
  draft claimed the opposite and was wrong [verified: live REGEX executed
  against this PR's file set] — anchors: row4
Row 11 [assumption]: Notion IAC-standards page is a separate brief for a
  session in a different repo [engineer-verified] — anchors: root
```

## Critical files

**Modify**
- `claude/.claude/rules/github-actions-workflows.md` — header replacement; apply corrections 2, 3, 5, 6, 7. **No new prose bullet** — Mechanism 1's original bullet is deleted, not added here. Keep `paths:` frontmatter untouched.
- `claude/.claude/skills/tests/test_rules_frontmatter.py:64` — extend the violation message to name both resolutions.
- `.github/workflows/tests.yml:64-71` — replace `REGEX`/allow-list logic with `SKIP_REGEX`/deny-list.
- **PENDING:** `claude/.claude/skills/code-review/SKILL.md:49` — only if Mechanism 1's replacement is confirmed. Requires `/skill-review`.

**Create**
- `docs/rules-references.md` — one entry per claim (URL, blockquoted verbatim source text, fetch date, drift note where applicable). No count claims.
- `claude/.claude/hooks/tests/test_ci_path_filter.py` — guard test for `SKIP_REGEX`.

**Do not touch:** the other three rules files; anything in the downstream repo; `.claude/rules/` project-scoped rules; `SHELL_REGEX`/`shell_changed`.

## Verification

1. `.venv/bin/pytest claude/.claude/` — full suite, including the new `test_ci_path_filter.py` and the extended `test_rules_frontmatter.py` message.
2. `.venv/bin/ruff check claude/.claude/` and `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck`.
3. `.venv/bin/pytest claude/.claude/hooks/tests/test_ci_path_filter.py -v` before pushing.
4. Live load check: open any `.github/workflows/*.yml` in a fresh session and confirm the rule still fires after editing.
5. Every citation in `docs/rules-references.md` re-verified against the live doc at implementation time, not transcribed from this plan.
6. If Mechanism 1's replacement is confirmed: `/skill-review` on the `code-review/SKILL.md` edit, then a fresh full `/plan-review`, before `/code-review` on the implementation.
7. `/code-review` before handoff regardless.

## Out of scope

- **Notion IAC-standards page** — separate brief, different repo.
- **The downstream repo's PRs** — the violation is already fixed there.
- **`REFERENCES.md` for the other three rules files** — brief forbids the sweep.
- **The `actions/checkout` protection GitHub has since added for `pull_request_target`** — recorded as a drift note in the reference file only.
- **`if:` and `actions/github-script` as named injection sinks** — GitHub's own docs don't name them.
- **Diagnosing why the downstream session's authoring reasoning went wrong** — not observable; the review-pipeline gap is diagnosable and is what this plan addresses instead.
