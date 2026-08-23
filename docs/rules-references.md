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
