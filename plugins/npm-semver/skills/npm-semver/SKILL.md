---
name: npm-semver
description: >
  Semver and version-field discipline for published npm packages, plus a
  reminder to propagate a bump into consuming repos.
  TRIGGER when: editing the source of a published npm package, or preparing
  to publish one.
  DO NOT TRIGGER when: the package.json has "private": true, or the repo
  only consumes the dependency rather than publishing it.
user-invocable: false
---

# npm semver

Fires when editing the source of a published npm package, or preparing to
publish one.

## Version field: one location only

The canonical version is `version` in the package's `package.json`. Do not
introduce a second version field elsewhere in the package (a duplicated
constant, a build-injected string) — every other site should read from
`package.json`, not restate it.

## Bump magnitude

Per semver.org 2.0.0, determine the bump by backward compatibility against the
package's declared public API, not by diff size.

Bump against the package's declared public-API surface (exports, CLI,
documented config), not diff size or internal refactors — if you're unsure
whether something is on that surface, the surface hasn't been declared yet.

| Change to the package | Bump |
|---|---|
| Backward-compatible bug fix — internal correction with no change to the declared public API | patch |
| Backward-compatible addition — new exported capability, a broadened accepted input, a new optional parameter | minor |
| Backward-incompatible change — a signature change, a removed or renamed export, a behavior change anyone relying on the prior version would notice | major |

## Propagate to consumers

A version bump doesn't propagate — each consuming repo must re-pin, reinstall,
and revalidate on its own cadence per its own pinning policy (see the
package's own docs for that consumer list); this skill can't do it for you.

## Checklist

Emit this on trigger and verify each item before committing:

```
npm semver checklist:
- [ ] the package's package.json `version` bumped
- [ ] Bump magnitude matches the change against the package's declared public API (see table above)
- [ ] Consuming repos identified and reminded to re-pin, reinstall, and re-run their own validation
```
