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

- **Pin third-party actions to a full 40-char commit SHA, not a tag.** A tag
  can be silently moved or deleted by a compromised maintainer, so it is not
  an immutable reference (see docs/rules-references.md for the GitHub
  citation). (Doesn't cover transitive actions the pinned action itself
  calls.)
- **Set an explicit, least-privilege `permissions:` block.** `{}` denies
  every `GITHUB_TOKEN` scope, including contents (so a bare
  `actions/checkout` would fail without an override); `{contents: read}`
  grants read and denies everything else (citation: docs/rules-references.md).
  Use `{}` if the workflow needs no token scope at all, `{contents: read}` if
  it needs to check out code — grant more only per job.
- **`persist-credentials: false` on `actions/checkout`** unless a later step
  needs authenticated git. `actions/checkout` defaults to persisting the
  token: "The auth token is persisted in the local git config" — readable by
  any later step or compromised dependency in the job.
- **Never interpolate untrusted `${{ github.event.* }}` values into any field
  the receiving action executes as code, not just `run:`** — check the
  action's docs to know which fields those are (see docs/rules-references.md
  for the attacker-controlled field list and GitHub's remediation guidance).
- **`pull_request_target` and `workflow_run` run with base-repo write access
  and secrets in context**, including for `workflow_run` when triggered by
  an untrusted fork PR run — never check out or execute code from the
  triggering ref or its inputs (uploaded artifacts,
  `github.event.workflow_run.*`) under these events; use `pull_request` for
  untrusted contributions instead (citation: docs/rules-references.md).
- **When authenticating to a cloud provider, prefer OIDC over long-lived
  secrets** — but a repo-only OIDC trust condition still grants every
  branch, tag, and PR in that repo, so pin the narrowest subject the job
  needs (e.g. `repo:ORG/REPO:environment:NAME` or
  `repo:ORG/REPO:ref:refs/heads/BRANCH`; citation: docs/rules-references.md).
- **Give every job a real `timeout-minutes` budget** — the 360-minute
  default applies regardless of runner type, but the platform backstop that
  would otherwise catch a too-high explicit value differs 20x: GitHub-hosted
  execution is capped at 6h no matter what you set, while self-hosted is
  capped at 5 days, so it's a too-high value on a self-hosted job — not an
  unset one — that leaves the runner (and its credentials) exposed for days
  if the job runs away.
- **`concurrency:` with `cancel-in-progress: true` for PR/feature-branch
  validation only** — not for deploy or push-to-default-branch workflows,
  where cancelling mid-run risks leaving a deploy or release partially applied.
- **Pin runner images to a specific OS-version label (`ubuntu-24.04`), not
  `ubuntu-latest`** — `latest` can break a build with zero change on your
  side, and even a versioned label still gets periodically rebuilt by
  GitHub, so it reduces but doesn't eliminate drift.
