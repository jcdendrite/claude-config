---
paths:
  - "**/.github/workflows/*.yml"
  - "**/.github/workflows/*.yaml"
---

## GitHub Actions workflow conventions

Sources verified against GitHub's official docs (2026-07): "Security hardening
for GitHub Actions", `actions/checkout` README, and the OIDC hardening guide.
`timeout-minutes` default and runner-image mutation are well-established
platform facts corroborated by multiple sources but not pinned to a single
fetched doc URL this session — re-confirm at point of use if precision matters.

- **Pin third-party actions to a full 40-char commit SHA, not a tag.** GitHub:
  "Pinning an action to a full-length commit SHA is currently the only way to
  use an action as an immutable release." A tag "can be moved or deleted if a
  bad actor gains access to the repository storing the action" — even a
  trusted author's tag is not immutable. (Doesn't cover transitive actions the
  pinned action itself calls.)
- **Set an explicit, least-privilege `permissions:` block.** GitHub: "It's
  good security practice to set the default permission for the `GITHUB_TOKEN`
  to read access only for repository contents. The permissions can then be
  increased, as required, for individual jobs." `permissions: {}` and
  `permissions: {contents: read}` are NOT the same thing: `{}` denies every
  `GITHUB_TOKEN` scope, including contents (so a bare `actions/checkout` would
  fail without an override); `{contents: read}` grants read and denies
  everything else. Use `{}` if the workflow needs no token scope at all,
  `{contents: read}` if it needs to check out code — grant more only per job.
- **`persist-credentials: false` on `actions/checkout`** unless a later step
  needs authenticated git. `actions/checkout` defaults to persisting the
  token: "The auth token is persisted in the local git config" — readable by
  any later step or compromised dependency in the job.
- **Never interpolate untrusted `${{ github.event.* }}` values into a `run:`
  script.** GitHub's stated fix: "For inline scripts, the preferred approach
  to handling untrusted input is to set the value of the expression to an
  intermediate environment variable" — then reference `"$VAR"` quoted, not the
  raw `${{ }}` expression. PR title/body/branch name are attacker-controlled.
- **`pull_request_target` can run with base-repo write access and secrets.**
  GitHub: these workflows "may have repository write access and access to
  referenced secrets" — actual scope still depends on the workflow's own
  `permissions:` block, but the base-repo token/secrets are in context, unlike
  `pull_request`. GitHub: "must not explicitly check out untrusted code,
  including from pull request forks." Use `pull_request` for untrusted
  contributions instead.
- **When authenticating to a cloud provider, prefer OIDC over long-lived
  secrets.** GitHub: with OIDC "you won't need to duplicate your cloud
  credentials as long-lived GitHub secrets," and "your cloud provider issues a
  short-lived access token that is only valid for a single job, and then
  automatically expires."
- **Give every job a real `timeout-minutes` budget.** The hosted-runner
  implicit default is 360 minutes (6h) if unset — pick a job-appropriate low
  value instead of relying on the default.
- **`concurrency:` with `cancel-in-progress: true` for PR/feature-branch
  validation only** — not for deploy or push-to-default-branch workflows,
  where cancelling mid-run risks leaving a deploy or release partially applied.
- **Pin runner images to a specific OS-version label (`ubuntu-24.04`), not
  `ubuntu-latest`** — `latest` moves on GitHub's own schedule and can break a
  build with zero change in your own repo. A versioned label reduces drift but
  isn't a full immutable pin the way an action SHA is — GitHub still rebuilds
  it periodically (patched packages, tool version bumps) on its own schedule.
