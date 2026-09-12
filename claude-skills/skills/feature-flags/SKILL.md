---
name: feature-flags
description: >
  Which layer a runtime toggle belongs at, and whether a dedicated
  flag-management platform is warranted.
  TRIGGER when: deciding where a toggle lives (a deploy-time IaC
  variable, a field in a datastore the team already runs, or a flag
  platform), evaluating or adopting a feature-flag vendor, or reviewing
  a toggle whose lifespan and implementation weight are mismatched.
  DO NOT TRIGGER when: naming or sourcing a config value across
  environments (that is config-environments), auditing an
  already-implemented flag's test coverage or default-off semantics
  (code-review owns that), or any non-toggle env-var question.
user-invocable: false
---

# Feature Flags

## Reach for the lightest layer that satisfies the need

CLAUDE.md's "Default-suspect over-powered primitives" applies to toggle
placement as directly as it does to any other mechanism choice: when a
design reaches for a heavier layer than the task requires, identify the
lighter primitive that could carry the same requirement before adopting
the heavier one. For toggles, the over-powered instinct is reaching for
a dedicated flag-management platform — audit trail, targeting rules,
experiment metrics, a management UI — when the requirement never needed
more than a typed deploy-time variable or a plain datastore field. The
escalation ordering below exists so that judgment isn't re-derived from
scratch each time a toggle question comes up.

## Fowler/Hodgson's four toggle categories

Feature toggles come in four categories, not two:

- **Release** — hides in-progress work behind a switch flipped once and
  then deleted.
- **Experiment** — runs an A/B test with per-user variance and a bounded
  lifespan.
- **Ops** — an operational kill switch, flipped by a human during an
  incident.
- **Permissioning** — gates a feature by which users or plans can see
  it; legitimately long-lived and dynamic.

Lifespan drives implementation weight: a Release toggle that lives for
days deserves the cheapest implementation that works, while a
Permissioning toggle that lives for years and needs per-subject dynamic
routing earns real investment in "more sophisticated Toggle Routers," in
the source article's own words. Treating every toggle as equally
disposable, or equally permanent, is the wrong axis — lifespan is.

## The deploy-time-vs-runtime layering test

Answer these three questions in order:

1. **Does flipping it require a deploy anyway**, because it changes
   infrastructure shape or a provider argument? → **deploy-time**. It is
   a typed IaC variable, not a flag at all.
2. **Must it flip faster than the deploy cycle**, by a human, during an
   incident? → **runtime**.
3. **Must it differ for different subjects at the same instant** —
   per-user, per-org, per-cohort, percentage rollout? → **runtime with
   targeting**. Only this answer can reach the platform tier below.

12-Factor's Config principle underpins the deploy-time half of this
test: config is what varies *between* deploys, not what a running
process needs to vary *within* one deploy. A value that only ever
changes alongside a deploy is config, not a toggle.

## Escalation ordering

1. **Deploy-time** — a Terraform variable with a `validation` block,
   set per-environment in `.tfvars`. This is the same default the
   `terraform-conventions` rule enforces for provider-native enums.
2. **Runtime, single global or per-subject boolean** — a field in a
   datastore the team already runs, read behind one accessor. This
   layer owes, by hand, the disciplines a platform gives for free: an
   audit trail, a default-off state, and a removal date.

   The accessor's read-failure default must resolve to the safe state
   of the gated path — a datastore timeout or connection-pool
   exhaustion must not silently resolve to "check disabled." If the
   accessor caches the value to survive a hot read path, the cache TTL
   must not exceed the tier's own reaction-speed requirement: this tier
   exists to flip faster than a deploy, and an uncapped cache defeats
   that during exactly the incident it was built for.

   When the toggle gates a security-sensitive or privileged path
   (disabling an auth check, bypassing a rate limit, skipping a
   verification step), the write path needs its own authorization
   check, scoped to a narrower principal set than the datastore's
   general write grant — not merely a separate code path the same
   principals can still pass — and enforced at every writer capable of
   setting that field (application code, migrations, admin tooling,
   background jobs), not only the primary write path. An audit record
   after the fact is not a substitute for gating the write itself.
3. **Multi-variant targeting, percentage rollout, or experimentation
   with metrics attribution** — a dedicated platform, and only here. A
   vendor's SaaS/cloud-hosted tier means the per-subject targeting
   attributes driving that rollout leave the team's infrastructure — a
   data-egress decision distinct from the license and self-host-cap
   comparison in `REFERENCES.md`.

**One narrower anti-pattern**, not a category-wide claim: a boolean that
only encodes a capability already tracked in billing or domain data
(e.g. `user.plan == 'pro'`) is domain-model state, not a toggle at all —
the "default-suspect over-powered primitives" principle applied
recursively lands on no flag layer whatsoever. Don't conflate this with
a genuine Permissioning Toggle, which does need per-subject routing for
the premium-feature case step 3 already covers — the anti-pattern is
only the degenerate case where no toggle was needed at all.

## What this skill does not own

This skill decides which layer a toggle belongs at. It does not own:

- Flag-state test coverage and stale-flag removal (`code-review`)
- Default-off rollout semantics, flag scope, and both-flag-states
  testability (`staff-product-engineer`)
- Kill-switch gating and flag-state observability (`staff-backend-engineer`)

Once the layer is chosen, implementation and review of that layer belong
to the owning surface named above — this skill does not restate their
content.
