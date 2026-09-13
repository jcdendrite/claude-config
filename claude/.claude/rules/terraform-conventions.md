---
paths:
  - "**/*.tf"
  - "**/*.tfvars"
  - "**/*.tf.json"
  - "**/*.tfvars.json"
---

## Terraform conventions

HashiCorp's own Terraform documentation grounds the `validation`,
`nullable`, and module-versioning guidance below; `terraform-provider-aws`
supplies the one worked enum example. See `docs/rules-references.md` for
citations (2026-09). This rule covers input-variable design — typing,
validation, defaults, and changing a default on an existing module. State,
backend, apply-workflow, and security-posture concerns are
`staff-platform-engineer`'s and `ciso-reviewer`'s review angles, not this
rule's. The provider argument below is an illustration — apply the
equivalent for your provider. Whether a toggle belongs in Terraform at
all, versus a runtime layer, is the `feature-flags` skill's call — this
rule doesn't decide it.

- **Type a provider-native string enum as its own type, not a `bool`
  mapped through a ternary.** Terraform has no native enum type, so this
  is this repo's convention, not HashiCorp's. The reasoning follows the
  `validation` mechanism below. Flag this only when one of two conditions
  holds:
  - The mapped states aren't true opposites — e.g. a `legacy` vs.
    `current` compatibility mode, or a `retain` vs. `destroy` deletion
    behavior, where a bare `bool` forces the reader to remember which
    value maps to which mode.
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
  plan` (a local, fast check). A provider's closed legal-value set is
  exactly the "uniquely restrictive requirement" HashiCorp's own Style
  Guide reserves `validation` for — this isn't a blanket call to
  validate every variable.
- **Mirror the provider's own default** in the variable's `default` when
  one exists. Add a `validation` branch admitting `null` when the
  underlying argument is itself optional, or set `nullable = false` when
  it isn't. **Exception:** when the provider's documented default is a
  legacy or less-secure legal value rather than its own recommendation,
  set the recommended value instead and say why the module diverges in a
  comment.
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

  Narrowing the legal-value set is unconditionally at least as strong a
  major-version signal as a default change: a previously-valid caller
  value can now fail validation, with no opt-in. Purely additive
  widening is backward-compatible by semver's own definition: every
  previously-accepted value still validates. Treat it as a same-version
  (MINOR) change for an Enumerable module. Treat it as a major-version
  change only for an Open module, where you cannot rule out a caller
  relying on the gate's prior rejection of the now-legal value.
- **Word the `validation` error message to flag it as possibly-stale**,
  since the provider can add legal values the module's `validation`
  block will keep rejecting.
