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
- **Mirror the provider's own default** in the variable's `default` when
  one exists, with a matching `validation` branch admitting `null` when
  the underlying argument is itself optional.
- **Exception: prefer the recommended value over an inferior provider
  default.** When the provider's documented default is the less-secure of
  two legal values, mirror the recommended value instead and state why
  the two diverge in a comment. For example,
  `aws_cognito_user_pool_client`'s `prevent_user_existence_errors`
  defaults to `LEGACY`; prefer `ENABLED`, since `LEGACY` reintroduces a
  user-enumeration side channel during sign-in and password-recovery
  flows.
  - `ENABLED` normalizes the error content across `AdminInitiateAuth`,
    `AdminRespondToAuthChallenge`, `InitiateAuth`,
    `RespondToAuthChallenge`, `ForgotPassword`, `ConfirmForgotPassword`,
    `ConfirmSignUp`, and `ResendConfirmationCode`.
  - AWS documents this as a content change, not a timing guarantee.
  - The CWE-208 timing side channel `ciso-reviewer.md`'s
    account-existence-disclosure angle covers is a separate concern this
    setting doesn't close.
  - The initial registration call (`SignUp`) is outside this setting's
    coverage entirely, since AWS documents `SignUp` as unconditionally
    throwing `UsernameExistsException` regardless of this setting:
    - **Suppress it**: don't surface `UsernameExistsException` verbatim
      from `SignUp`.
    - **Accept it**: treat the trade-off as a deliberate product
      decision.
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
