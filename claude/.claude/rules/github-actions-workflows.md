---
paths:
  - "**/.github/workflows/*.yml"
  - "**/.github/workflows/*.yaml"
---

## GitHub Actions workflow conventions

Sources verified against GitHub's official docs (2026-07): GitHub's "Secure
use reference," the `actions/checkout` README, the OpenID Connect reference
guide, and the workflow-syntax and usage-limits references. Full citations,
verbatim quotes, and fetch dates live in `docs/rules-references.md` in the
claude-config repo — re-confirm there if precision matters.

- **Pin third-party actions to a full 40-char commit SHA, not a tag.** GitHub:
  "Pinning an action to a full-length commit SHA is currently the only way to
  use an action as an immutable release." A tag "can be moved or deleted if a
  bad actor gains access to the repository storing the action" — even a
  trusted author's tag is not immutable. (Doesn't cover transitive actions the
  pinned action itself calls.)
- **Set an explicit, least-privilege `permissions:` block.** GitHub: "It's
  good security practice to set the default permission for the `GITHUB_TOKEN`
  to read access only for repository contents. The permissions can then be
  increased, as required, for individual jobs within the workflow file."
  `permissions: {}` and `permissions: {contents: read}` are NOT the same
  thing: `{}` denies every `GITHUB_TOKEN` scope, including contents (so a
  bare `actions/checkout` would fail without an override); `{contents: read}`
  grants read and denies everything else. Use `{}` if the workflow needs no
  token scope at all, `{contents: read}` if it needs to check out code —
  grant more only per job.
- **`persist-credentials: false` on `actions/checkout`** unless a later step
  needs authenticated git. `actions/checkout` defaults to persisting the
  token: "The auth token is persisted in the local git config" — readable by
  any later step or compromised dependency in the job.
- **Never interpolate untrusted `${{ github.event.* }}` values anywhere they
  will be interpreted as executable code — a `run:` script, your own
  composite/custom action, or a `with:` field the receiving action itself
  executes rather than treats as an inert argument (e.g.
  `actions/github-script`'s `script:` input).** GitHub treats the whole
  `github` context as potentially attacker-controlled — fields "typically end
  with `body`, `default_branch`, `email`, `head_ref`, `label`, `message`,
  `name`, `page_name`, `ref`, and `title`," plus branch names and email
  addresses. `${{ }}` expansion happens before the action runs regardless of
  the YAML key, so the test isn't "is this field named `with:`" — it's "does
  the receiving action execute this input, or only pass it through as data."
  Before trusting any `with:` field with an untrusted value, check the
  receiving action's own docs or source for whether it executes that field;
  `actions/github-script`'s `script:` is one instance of this class, not the
  only one. GitHub's preferred fix, in order: pass the value as an argument
  to a JavaScript action rather than inlining it; for inline scripts, "set
  the value of the expression to an intermediate environment variable" and
  reference `"$VAR"` quoted, not the raw `${{ }}` expression.
- **`pull_request_target` and `workflow_run` can run with base-repo write
  access and secrets.** GitHub: these triggers "may have repository write
  access and access to referenced secrets" — actual scope still depends on
  the workflow's own `permissions:` block, but the base-repo token/secrets
  are in context, unlike `pull_request`. `workflow_run`'s context is the
  default branch's workflow with that same privileged token; its inputs
  (uploaded artifacts, `github.event.workflow_run.*`) come from the completed
  run and are attacker-controlled if that run was triggered by a fork PR.
  GitHub: workflows using these triggers "must not explicitly check out
  untrusted code, including from pull request forks or from repositories
  that are not under your control." Use `pull_request` for untrusted
  contributions instead.
- **When authenticating to a cloud provider, prefer OIDC over long-lived
  secrets.** GitHub: with OIDC "you won't need to duplicate your cloud
  credentials as long-lived GitHub secrets," and "your cloud provider issues a
  short-lived access token that is only valid for a single job, and then
  automatically expires." The trust decision moves to the cloud side:
  GitHub says you "must define at least one condition, so that untrusted
  repositories can't request access tokens" — but a repo-only condition
  still grants every branch, tag, and PR in that repo. Pin the narrowest
  subject the job needs, typically `repo:ORG/REPO:environment:NAME` or
  `repo:ORG/REPO:ref:refs/heads/BRANCH`.
- **Give every job a real `timeout-minutes` budget.** The default is 360
  minutes (6h) regardless of runner type, but the platform backstop that
  catches a missing or too-high value differs 20x: GitHub-hosted job
  execution is capped at 6 hours; self-hosted at 5 days. Set the field
  explicitly, especially on self-hosted jobs, where the platform will not
  intervene for five days while a runaway or compromised job holds a
  persistent runner and its credentials.
- **`concurrency:` with `cancel-in-progress: true` for PR/feature-branch
  validation only** — not for deploy or push-to-default-branch workflows,
  where cancelling mid-run risks leaving a deploy or release partially applied.
- **Pin runner images to a specific OS-version label (`ubuntu-24.04`), not
  `ubuntu-latest`** — `latest` moves on GitHub's own schedule and can break a
  build with zero change in your own repo. A versioned label reduces drift but
  isn't a full immutable pin the way an action SHA is — GitHub still rebuilds
  it periodically (patched packages, tool version bumps) on its own schedule.
