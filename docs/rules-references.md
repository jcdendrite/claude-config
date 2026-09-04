# References — rules

Reference material for `.claude/rules/*.md` files. Not loaded at runtime —
read when editing a rule to verify a claim still holds or to add new
guidance. One section per rule file; one entry per claim within a section.

## Raw SQL & DDL conventions

Source for `claude/.claude/rules/sql-ddl-conventions.md`.

- `ALTER TABLE ... ADD COLUMN` rewrite behavior verified against
  `postgresql.org/docs/current/sql-altertable.html`.
- `ALTER TYPE ... ADD VALUE` transaction behavior verified against
  `sql-altertype.html` and the PostgreSQL 12 release notes ("Now it can be
  called in a later transaction, so long as the new enumerated value is not
  referenced until after it is committed").
- Schema-shape conventions distilled and specialist-verified from this repo's
  `staff-data-engineer.md` / `staff-analytics-engineer.md` review agents.

## CLAUDE.md and AGENTS.md conventions

Source for `claude/.claude/rules/claude-md-conventions.md`. Fetched
2026-09-03 against `code.claude.com/docs/en/memory`.

- [Claude Code — How Claude remembers your project](https://code.claude.com/docs/en/memory)
  — CLAUDE.md-loaded-not-AGENTS.md and the `@AGENTS.md` import pattern.

  > "Claude Code reads CLAUDE.md, not AGENTS.md. If your repository already uses
  > AGENTS.md for other coding agents, create a CLAUDE.md that imports it so both
  > tools read the same instructions without duplicating them."

  Independently corroborated: zero AGENTS.md entries in the Claude Code
  changelog, and Claude Code is absent from agents.md's supported-tools list.

## GitHub Actions workflow conventions

Source for `claude/.claude/rules/github-actions-workflows.md`. All fetched
2026-07-31 against `docs.github.com` and `github.com` unless noted.

- **Doc rename** — `docs.github.com/en/actions/reference/security/secure-use`
  ("Secure use reference"). The old URL
  `docs.github.com/en/actions/security-guides/security-hardening-for-github-actions`
  301-redirects here (verified via `curl -o /dev/null -w '%{http_code}'`).
  Drift note: the rule file's header previously cited the old title
  "Security hardening for GitHub Actions."

- **Pin actions to a commit SHA** —
  `docs.github.com/en/actions/reference/security/secure-use`, "Using
  third-party actions" section:
  > Pinning an action to a full-length commit SHA is currently the only way
  > to use an action as an immutable release. ... a tag can be moved or
  > deleted if a bad actor gains access to the repository storing the
  > action.

- **`permissions:` scope** —
  `docs.github.com/en/actions/reference/security/secure-use#use-secrets-for-sensitive-information`:
  > The permissions can then be increased, as required, for individual jobs
  > within the workflow file.
  Drift note: the rule file's prior wording truncated this to "...for
  individual jobs," dropping "within the workflow file."

- **`persist-credentials: false`** — `github.com/actions/checkout` README,
  Checkout v4 section:
  > The auth token is persisted in the local git config. This enables your
  > scripts to run authenticated git commands. The token is removed during
  > post-job cleanup. Set `persist-credentials: false` to opt-out.

- **Script injection via `${{ github.event.* }}`** —
  `docs.github.com/en/actions/concepts/security/script-injections`: values
  "typically end with `body`, `default_branch`, `email`, `head_ref`,
  `label`, `message`, `name`, `page_name`, `ref`, and `title`" are
  attacker-controlled and must not flow "into workflows, actions, API
  calls, or anywhere else where they could be interpreted as executable
  code" — the risk is defined by execution semantics, not by which YAML key
  carries the value. Same page, on branch names and email addresses:
  > In addition, there are other less obvious sources of potentially
  > untrusted input, such as branch names and email addresses, which can be
  > quite flexible in terms of their permitted content.

- **`with:` as the safe pattern (with a named exception)** —
  `docs.github.com/en/actions/reference/security/secure-use`, on passing a
  context value to a JavaScript action instead of inlining it in `run:`:
  > The recommended approach is to create a JavaScript action that
  > processes the context value as an argument. This approach is not
  > vulnerable to the injection attack, since the context value is not used
  > to generate a shell script, but is instead passed to the action as an
  > argument.
  This is safe specifically because the receiving action treats the value
  as an inert argument. `actions/github-script`'s `script:` input is also
  set via `with:`, but the receiving action executes that field as code —
  it is a sink despite the field name. Drift note: the rule file's prior
  wording scoped the risk to `run:` only and named `with:`/`if:`/
  `actions/github-script` as excluded fields; GitHub's own framing is
  field-agnostic (execution semantics, not field name), and no longer
  publishes an enumerated field list, only the suffix heuristic above.

- **`pull_request_target` and `workflow_run` privilege** —
  `docs.github.com/en/actions/reference/security/secure-use`:
  > The `pull_request_target` and `workflow_run` workflow triggers, when
  > used with the checkout of an untrusted pull request, expose the
  > repository to security compromises. These workflows are privileged,
  > which means they share the same cache of the main branch with other
  > privileged workflow triggers, and may have repository write access and
  > access to referenced secrets.
  On `workflow_run`'s own privileged-token behavior —
  `docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows`:
  > The workflow started by the `workflow_run` event is able to access
  > secrets and write tokens, even if the previous workflow was not.
  Same page's context table lists `GITHUB_SHA`/`GITHUB_REF` for this event
  as the last commit / ref of the default branch, not the triggering run's
  own commit — the default-branch-context claim.
  Same page, on untrusted-code checkout:
  > Workflows that use these triggers must not explicitly check out
  > untrusted code, including from pull request forks or from repositories
  > that are not under your control.
  Drift note: the rule file's prior wording covered only
  `pull_request_target`, omitting `workflow_run` (a trigger GitHub's own
  source sentence treats identically), and truncated the untrusted-code
  sentence to "...including from pull request forks."

- **OIDC over long-lived secrets** —
  `docs.github.com/en/actions/concepts/security/openid-connect`, "Benefits
  of using OIDC":
  > You won't need to duplicate your cloud credentials as long-lived GitHub
  > secrets. ... your cloud provider issues a short-lived access token that
  > is only valid for a single job, and then automatically expires.

- **OIDC subject-claim scoping** —
  `docs.github.com/en/actions/reference/security/oidc`:
  > To control how your cloud provider issues access tokens, you must
  > define at least one condition, so that untrusted repositories can't
  > request access tokens for your cloud resources.
  A repo-only condition still grants every branch, tag, and PR in that
  repo — narrower subjects (`repo:ORG/REPO:environment:NAME`,
  `repo:ORG/REPO:ref:refs/heads/BRANCH`) are available. Wildcards are not
  categorically wrong: GitHub's own AWS how-to
  (`docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws`)
  ships `"token.actions.githubusercontent.com:sub": "repo:octo-org/octo-repo:*"`
  as a supported example condition.

- **`timeout-minutes` default and platform backstop** —
  `docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idtimeout-minutes`:
  > The maximum number of minutes to let a job run before GitHub
  > automatically cancels it. Default: 360
  This default applies regardless of runner type. The platform backstop
  differs by runner type —
  `docs.github.com/en/actions/reference/limits`:
  > Each job in a workflow can run for up to 6 hours of execution time.
  (GitHub-hosted) / "Each job in a workflow can run for up to 5 days of
  execution time." (self-hosted)

- **Runner image mutation** — `github.com/actions/runner-images` README,
  "Image Releases" → "Cadence":
  > We typically deploy weekly updates to the software on the runner
  > images.
  Grounds the rule file's claim that a versioned OS-label pin
  (`ubuntu-24.04`) reduces drift but is not a full immutable pin the way an
  action SHA is.

- **Composite action (`action.yml`) schema — no `permissions:`,
  `concurrency:`, `runs-on:`, or `timeout-minutes` key** —
  `docs.github.com/en/actions/reference/workflows-and-actions/metadata-syntax`,
  fetched 2026-09-03. The documented `runs.steps[*]` key set for a composite
  step is `name`, `id`, `if`, `uses`, `run`, `shell`, `env`,
  `working-directory`, `with`, `continue-on-error`. None of `permissions`,
  `concurrency`, `runs-on`, or `timeout-minutes` appears in that key set.
  `runs.steps[*].uses` and `runs.steps[*].with` are documented and behave
  like their workflow-file counterparts, grounding the "transfers directly"
  guidance in the rule body for SHA-pinned nested `uses:` and
  `actions/checkout`'s `persist-credentials: false` `with:` input.

- **Composite-action context availability — `secrets` is the one
  documented inputs-only context; `github.event.*` carries no documented
  restriction** — `docs.github.com/en/actions/learn-github-actions/contexts`,
  "Context availability" and `github`-context-properties sections, fetched
  2026-09-03.

  The `secrets` context is the one documented restriction: "The secrets
  context is not available for composite actions due to security reasons.
  If you want to pass a secret to a composite action, you need to do it
  explicitly as an input."

  That page's "Context availability" table is keyed to workflow-file YAML
  locations (`run-name`, `concurrency`, `jobs.<job_id>.steps.run`, and
  similar). The string "composite" does not appear in that table, so it is
  silent on composite actions rather than an affirmative listing of
  `github` as available there. The claim instead rests on the `secrets`
  carve-out above being the only composite-action restriction GitHub
  documents: no equivalent carve-out names `github.event.*`.

  GitHub's metadata-syntax page (above) separately shows a canonical
  `runs.steps[*].run` example using `${{ github.action_path }}` directly,
  with no `env:` indirection — consistent with, though not proof of,
  `github.event.*` behaving the same way.

  The **contexts page** (not the metadata-syntax page) documents one
  exception to direct `run:` availability: `github.action_ref` and
  `github.action_repository` must not be used directly in a composite
  step's `run:` and require `env:` indirection instead.

  The "Context-dependent, not authorable in `action.yml`" bullet's
  `pull_request_target`/`workflow_run`-privilege and OIDC-subject-pinning
  claims, as applied to composite actions, restate the existing
  workflow-level citations above unchanged. A composite step runs inline
  within the calling job, inheriting the caller's trigger context and OIDC
  trust policy rather than having its own. No separate composite-action
  citation applies.

## `paths:` glob-dialect conventions

Source for `claude/.claude/rules/rule-authoring-conventions.md`. Verified
against `code.claude.com/docs/en/memory` §"Path-specific rules", fetched
2026-09-03. Single-sourced deliberately, not by omission: the `paths:`
glob dialect is Claude Code's own closed-source, single-vendor behavior,
with no second independent first-tier origin (spec, standards body, or
competing implementation) to triangulate against.

- **Brace expansion** — "You can specify multiple patterns and use brace
  expansion to match multiple extensions in one pattern"; `src/*.{ts,tsx}`
  expands to two patterns.
- **Brace-expansion budget** — a rule's whole `paths` list shares a budget
  of 1,000 expanded patterns and 4 MiB: "Claude Code uses any pattern that
  would exceed the budget unexpanded, and its literal braces match no
  files."
- **Malformed bracket expression** — "A pattern with a `[` that can't be
  read as a bracket expression, such as `photos [2024/**`, is invalid: it
  matches nothing, and the rule's other patterns keep working."
- **No `paths:` key** — the rule loads unconditionally at launch, "with the
  same priority as `.claude/CLAUDE.md`."
- **Zero-segment `**/` match — established by measurement, not by this
  source.** A `**/`-led pattern also matches a root-level file, confirmed
  via `InstructionsLoaded`-hook instrumentation. Not shown to transfer to
  interactive (non-`-p`) sessions. See
  `docs/case-studies/claude-md-glob-zero-segment.md` for the full trial
  record.
- **`?` support, leading-`/` anchoring, and trailing-`/` semantics are not
  stated at this source** — recorded as `[unverified]` in the rule body
  (`rule-authoring-conventions.md`) rather than restated or inferred here.
- **One intermediate segment is still unmeasured.** The trials above cover
  zero and three intermediate segments, so whether `**/CLAUDE.md` subsumes
  `**/.claude/CLAUDE.md` is open. `claude-md-conventions.md` keeps both
  forms for that reason, not by oversight. A depth-1 trial on the same
  instrument would settle it.
