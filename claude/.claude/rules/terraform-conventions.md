---
paths:
  - "**/*.tf"
  - "**/*.tfvars"
  - "**/*.tf.json"
  - "**/*.tfvars.json"
---

## Terraform conventions

Sources verified against `terraform-provider-aws`, the AWS Cognito API
reference, and HashiCorp's own Terraform documentation (2026-09); see
`docs/rules-references.md` for citations. This rule covers input-variable
design — typing, validation, defaults, and changing a default on an
existing module. State, backend, and apply-workflow concerns are
`staff-platform-engineer`'s review angles, not this rule's. AWS provider
arguments below are illustrations — apply the equivalent for your
provider. Whether a toggle belongs in Terraform at all, versus a runtime
layer, is the `feature-flags` skill's call — this rule doesn't decide it.

- **Type a provider-native string enum as its own type, not a `bool`
  mapped through a ternary.** Flag this only when one of two conditions
  holds:
  - The mapped states aren't true opposites — e.g.
    `aws_cognito_user_pool_client`'s `prevent_user_existence_errors`
    argument takes `LEGACY` or `ENABLED`, which differ in security
    posture rather than being simple negations of each other.
  - The enum can plausibly grow past two members — e.g.
    `aws_cognito_user_pool`'s `user_pool_tier` argument, `LITE`,
    `ESSENTIALS`, `PLUS`.

  A stable true/false enum is a legitimate boolean-convenience idiom the
  ecosystem's own registry modules use deliberately — not every
  two-valued provider enum needs flagging.
- **Pair the variable with a `validation` block enumerating the
  provider's documented legal values**, rather than leaving the type as a
  bare `string` with no guard against a typo or an unsupported value.
  For a provider whose schema doesn't already enforce this
  server-side, a `validation` block moves a bad-value failure from
  `terraform apply` (a live provider API round trip) to `terraform
  plan` (a local, fast check).
- **Mirror the provider's own default** in the variable's `default` when
  one exists, with a matching `validation` branch admitting `null` when
  the underlying argument is itself optional.
- **Exception: prefer the recommended value over an inferior provider
  default.** When the provider's documented default is the less-secure of
  two legal values, mirror the recommended value instead and state why
  the two diverge in a comment. For example,
  `aws_cognito_user_pool_client`'s `prevent_user_existence_errors`
  defaults to `LEGACY`; prefer `ENABLED`, which closes only the
  content-disclosure channel across a documented set of operations. It
  doesn't cover `SignUp` or a timing side channel — see
  `docs/rules-references.md` for the operation list and
  `ciso-reviewer.md`'s CWE-208 account-existence-disclosure angle.
- **On a retrofit** to an existing variable: a **default** change only
  affects callers that don't already override it. A **type or
  legal-value-set** change breaks callers that do — an existing caller
  passing the old type, or a value dropped from the legal set, fails
  validation. Treat either kind of change with the same scrutiny as any
  other behavior-changing edit to a shared module; the verification
  method depends on whether the caller set is enumerable:
  - **Enumerable** (an internal module with a small, tightly-versioned
    caller set): check existing callers, or diff a `terraform plan`
    across consumers, before merging.
  - **Open** (a published or registry-distributed module, or an
    internal module with a large, loosely-versioned caller set even if
    technically enumerable): treat the change as a new major version
    instead of a same-version flip, so a caller opts in by bumping
    their pin rather than inheriting the new behavior silently.

  A type or legal-value-set change is at least as strong a
  major-version signal as a default change, since the caller-visible
  literal values themselves change — including a purely additive
  widening, since a `validation` block can function as a caller-relied-on
  allow-list rather than a provider-schema mirror, and widening it changes
  what the gate permits.
- **Word the `validation` error message to flag it as possibly-stale**,
  since the provider can add legal values the module's `validation`
  block will keep rejecting.
