---
paths:
  - "**/.github/workflows/*.yml"
  - "**/.github/workflows/*.yaml"
  - "**/action.yml"
  - "**/action.yaml"
---

## GitHub Actions workflow conventions

Sources verified against GitHub's official docs (2026-07): GitHub's "Secure
use reference," the `actions/checkout` README, the OpenID Connect reference
guide, and the workflow-syntax and usage-limits references. Full citations,
verbatim quotes, and fetch dates live in `docs/rules-references.md` in the
claude-config repo — re-confirm there if precision matters.

**This rule also matches composite `action.yml` files.** Not every bullet
below applies to one. Verified against GitHub's Actions documentation,
fetched 2026-09-03; re-verify by 2026-12-03 (see `docs/rules-references.md`
for per-claim citations):

- **Does not transfer — no such key in the composite `action.yml` schema:**
  `permissions:`, `concurrency:`, `runs-on:` runner-image pinning, and
  composite-step `timeout-minutes`. The calling workflow remains the sole
  control point for all four. A composite action meant for reuse should
  document its token-scope and time-budget requirements for callers, since
  `action.yml` itself has no way to declare or enforce either.
- **Transfers directly:**
  - SHA-pinning a nested third-party `uses:` inside a composite step.
  - `persist-credentials: false` on `actions/checkout` — a `with:` input,
    usable identically in a composite step.
  - Untrusted-input interpolation into any field a composite step executes
    as code. `github.event.*` is directly available inside a composite
    step's own `run:`, same as a top-level workflow `run:` step. The
    script-injection risk transfers unchanged — it is not filtered through
    an `inputs.*`/`env.*` boundary (`docs/rules-references.md`). A small
    number of self-referential `github.action_*` properties
    (`github.action_ref`, `github.action_repository`) are the documented
    exception: they must not be used directly in `run:` and require `env:`
    indirection instead (`docs/rules-references.md`).
- **Inputs-only — the one context a composite action doesn't inherit:**
  `secrets`. A composite step's `run:` referencing `${{ secrets.FOO }}`
  directly resolves to an empty string rather than erroring `[unverified]`.
  That specific outcome is inferred from the `secrets`-context restriction
  plus general property-dereference behavior — GitHub doesn't state it
  directly for this case. The secret must be passed in via a `with:` input
  and read back as `inputs.*` to reach the step (`docs/rules-references.md`).
- **Context-dependent, not authorable in `action.yml`:**
  - `pull_request_target`/`workflow_run` privilege is inherited from the
    caller's trigger context, set in the workflow, not the action.
  - OIDC subject pinning is a cloud-provider-side trust policy, unless the
    composite action itself wraps the auth step.

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
  an untrusted fork PR run. Never check out or execute code from the
  triggering ref or its inputs (uploaded artifacts,
  `github.event.workflow_run.*`) under these events — use `pull_request` for
  untrusted contributions instead (citation: docs/rules-references.md).
- **When authenticating to a cloud provider, prefer OIDC over long-lived
  secrets** — but a repo-only OIDC trust condition still grants every
  branch, tag, and PR in that repo, so pin the narrowest subject the job
  needs (e.g. `repo:ORG/REPO:environment:NAME` or
  `repo:ORG/REPO:ref:refs/heads/BRANCH`; citation: docs/rules-references.md).
- **Give every job a real `timeout-minutes` budget.** The 360-minute default
  applies regardless of runner type. The platform backstop that would
  otherwise catch a too-high explicit value differs 20x: GitHub-hosted
  execution is capped at 6h no matter what you set, while self-hosted is
  capped at 5 days. On self-hosted, it's a too-high value — not an unset
  one — that leaves the runner (and its credentials) exposed for days if
  the job runs away.
- **`concurrency:` with `cancel-in-progress: true` for PR/feature-branch
  validation only** — not for deploy or push-to-default-branch workflows,
  where cancelling mid-run risks leaving a deploy or release partially applied.
- **Pin runner images to a specific OS-version label (`ubuntu-24.04`), not
  `ubuntu-latest`** — `latest` can break a build with zero change on your
  side, and even a versioned label still gets periodically rebuilt by
  GitHub, so it reduces but doesn't eliminate drift.
