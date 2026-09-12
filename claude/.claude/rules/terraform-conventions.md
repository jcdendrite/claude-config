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
- **Prefer `ENABLED` over `LEGACY` for `prevent_user_existence_errors`**
  (e.g. concretely applying the carve-out below) — `LEGACY` reintroduces
  a user-enumeration side channel during sign-in, sign-up, and
  password-recovery flows.
- **Pair the variable with a `validation` block enumerating the
  provider's documented legal values**, rather than leaving the type as a
  bare `string` with no guard against a typo or an unsupported value.
- **Mirror the provider's own default** in the variable's `default` when
  one exists, with a matching `validation` branch admitting `null` when
  the underlying argument is itself optional.
- **Exception: prefer the recommended value over an inferior provider
  default.** When the provider's documented default is itself the
  less-secure or otherwise-inferior of two legal values (as with
  `prevent_user_existence_errors`, which AWS defaults to `LEGACY`),
  mirror the recommended value instead, and state why the two diverge
  in a comment.
- **On a retrofit**, an existing variable's effective default changes
  for callers that don't already override it. Treat it with the same
  scrutiny as any other behavior-changing default to a shared module;
  the verification method depends on whether the caller set is
  enumerable:
  - **Enumerable** (an internal module): check existing callers, or
    diff a `terraform plan` across consumers, before merging.
  - **Open** (a published or registry-distributed module): treat the
    changed default as a new major version instead of a same-version
    flip, so a caller opts in by bumping their pin rather than
    inheriting the new behavior silently.
- **Word the `validation` error message to flag it as possibly-stale**,
  since the provider can add legal values the module's `validation`
  block will keep rejecting.
