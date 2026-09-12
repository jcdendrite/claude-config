# Feature Flags — References

Edit-time reference for `SKILL.md`. Not loaded at runtime. Read this file manually when updating skill rules to verify citations still hold or to add new guidance.

## OSS self-host platform comparison

| Platform | License | OSS self-host cap |
|---|---|---|
| Unleash | AGPL-3.0 | 1 project / 2 environments / 5,000 flags per instance |
| Flagsmith | BSD-3-Clause | No published self-hosted numeric cap — the 50,000 API calls/mo and 1-team-member limits attach to the Free/Cloud tier only |
| GrowthBook | MIT-Expat except three named directories (`packages/back-end/src/enterprise`, `packages/front-end/enterprise`, `packages/shared/src/enterprise`) | 1 project, despite unlimited flags/experiments/traffic |

---

## Martin Fowler / Pete Hodgson — Feature Toggles (aka Feature Flags)

**URL:** https://martinfowler.com/articles/feature-toggles.html
**Status:** VERIFIED (fetched 2026-09-11)

Author Pete Hodgson, dated 09 October 2017. Defines four toggle
categories — Release, Experiment, Ops, Permissioning — each with a
different longevity and dynamism profile. Permissioning Toggles are
described as legitimately long-lived and dynamic, needing "more
sophisticated Toggle Routers" for per-subject targeting (e.g. premium
feature gating).

**Cited for the skill's four-category taxonomy and the
lifespan-drives-implementation-weight axis.**

---

## 12-Factor App — §III Config

**URL:** https://12factor.net/config
**Status:** VERIFIED (fetched 2026-09-11)

Defines config as "everything that is likely to vary between deploys."
That deploy-versus-runtime boundary is the deploy-time half of the
skill's layering test: a value that only ever changes alongside a
deploy is config, not a toggle.

**Cited only for the "config is what varies between deploys" definition.**
This section has zero hits for "feature" — never treat it as a
feature-flag authority.
