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

1. **Deploy-time** — a typed variable with input validation in your IaC
   tool, set per-environment. In Terraform, that's a variable with a
   `validation` block set per-environment in `.tfvars` — the same
   default the `terraform-conventions` rule enforces for
   provider-native enums.
2. **Runtime, single global or per-subject boolean** — a field in a
   datastore the team already runs, read behind one accessor. This
   layer owes, by hand, the disciplines a platform gives for free: an
   audit trail, a default-off state, and a removal date.

   The accessor's read-failure default must resolve to the safe state
   of the gated path — a datastore timeout must not silently resolve to
   "check disabled." Within one authorization decision, read the toggle
   once and reuse that value; re-reading mid-flow reopens the same
   TOCTOU gap as any other authorization state that can change between
   check and action. Caching a hot read path is required, not optional:

   - An uncached per-request read saturates the datastore under normal
     load.
   - The cache TTL must not exceed this tier's own reaction-speed
     requirement — this tier exists to flip faster than a deploy, and
     an uncapped TTL defeats that during exactly the incident it was
     built for.
   - TTL bounds only per-instance staleness, not fleet-wide or
     replication-layer staleness.
   - A security-sensitive toggle needs a fleet-wide convergence bound
     instead of relying on TTL alone.
   - A security-sensitive toggle should use a strongly-consistent read
     when the datastore offers one.
   - On a read failure, jitter the refresh across instances rather
     than retry synchronously against a datastore that's already
     timing out.
   - Serve the last cached value only when it isn't riskier than the
     gated path's safe-state default.
   - Once past the TTL bound above, fall back to the safe default
     rather than keep serving stale.

   When the toggle gates a security-sensitive or privileged path
   (disabling an auth check, bypassing a rate limit, skipping a
   verification step), its write path needs its own security design
   review — consult `ciso-reviewer`'s privileged-action and IDOR angles
   for the authorization-check specifics. These properties hold
   regardless of that review's specifics:

   - No code path may complete the mutation without a corresponding
     audit-log entry existing. A same-transaction commit and a
     synchronous write to an isolated tamper-evident store both satisfy
     this. A fire-and-forget log call that can silently fail does not.
   - The log entry itself must be append-only and tamper-evident — the
     same bar `ciso-reviewer` applies to any privileged-action log.
   - Every application-layer writer must call through one writer-side
     mutator, symmetric with the read side's single accessor, rather
     than reimplementing authorization and logging at each call site.
   - Direct datastore access (a console update, an ad hoc fix, or a
     broad table-write role) bypasses any application-layer check
     entirely. It needs its own datastore-level control: a column-level
     grant, a row-level policy, or a restriction on who holds the
     table's write role at all.
   - A toggle that disables a security control globally warrants a
     stronger bar than a single-subject grant — consider a time-boxed
     override or two-person approval for that case.
3. **Multi-variant targeting, percentage rollout, or experimentation
   with metrics attribution** — a dedicated platform, and only here. A
   vendor's SaaS/cloud-hosted tier means the per-subject targeting
   attributes driving that rollout leave the team's infrastructure — a
   data-egress decision distinct from the license and self-host-cap
   comparison in `REFERENCES.md`. When a toggle reaching this tier is
   itself security-sensitive, the same write-path disciplines above
   apply, translated to the vendor surface: vendor console/API RBAC or
   SSO scoping in place of a datastore grant, the vendor's own audit log
   in place of an application audit table.

**One narrower anti-pattern**, not a category-wide claim: a boolean that
only mirrors state already tracked elsewhere (e.g. `user.plan == 'pro'`)
is domain-model state, not a toggle. A genuine Permissioning Toggle, by
contrast, needs per-subject routing (step 3).

## What this skill does not own

This skill decides which layer a toggle belongs at. It does not own:

- Flag-state test coverage and stale-flag removal (`code-review`)
- Default-off rollout semantics, flag scope, and both-flag-states
  testability (`staff-product-engineer`)
- Kill-switch gating and flag-state observability (`staff-backend-engineer`)
- Authorization-control design for a security-sensitive toggle's write
  path (`ciso-reviewer`)

Once the layer is chosen, implementation and review of that layer belong
to the owning surface named above — this skill does not restate their
content.
