---
paths:
  - "**/*.tf"
  - "**/*.tfvars"
  - "**/*.tf.json"
  - "**/*.tfvars.json"
---

## Terraform conventions

Sources verified against `terraform-provider-aws` and the AWS Cognito API
reference (2026-09); see `docs/rules-references.md` for citations. AWS
provider arguments below are illustrations — apply the equivalent for your
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

  A stable true/false enum (e.g. an S3 bucket's `Enabled`/`Suspended`
  versioning status) is a legitimate boolean-convenience idiom the
  ecosystem's own registry modules use deliberately — not every
  two-valued provider enum needs flagging.
- **Prefer `ENABLED` over `LEGACY` for `prevent_user_existence_errors`** —
  `LEGACY` reintroduces a user-enumeration side channel during sign-in,
  sign-up, and password-recovery flows.
- **Pair the variable with a `validation` block enumerating the
  provider's documented legal values**, rather than leaving the type as a
  bare `string` with no guard against a typo or an unsupported value.
- **Mirror the provider's own default** in the variable's `default` when
  one exists, with a matching `validation` branch admitting `null` when
  the underlying argument is itself optional — except when the
  provider's documented default is itself the less-secure or
  otherwise-inferior of two legal values: mirror the recommended value
  instead (as with `prevent_user_existence_errors`, which AWS defaults
  to `LEGACY`), and state why the two diverge in a comment.
- **Word the `validation` error message to name the staleness risk it
  carries** — a block enumerating literal values is a maintenance surface:
  when the provider adds a new legal value, Terraform's own schema accepts
  it while the module's `validation` block still rejects it. Say so in the
  message, so a future failure reads as "this list may be stale, check the
  provider docs" rather than "you passed something invalid."
