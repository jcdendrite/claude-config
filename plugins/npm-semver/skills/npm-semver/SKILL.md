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

Determine the bump by backward compatibility against the package's declared
public API, not by diff size. From [semver.org 2.0.0](https://semver.org/spec/v2.0.0.html):

> increment the: MAJOR version when you make incompatible API changes, MINOR
> version when you add functionality in a backward compatible manner, PATCH
> version when you make backward compatible bug fixes.

Semver.org also requires a declared public API for this to mean anything: "If
you're not sure whether to declare something as part of your public API,
you're probably worried about the wrong things." Define what the package
exposes (exports, CLI surface, documented config) and bump against that
surface, not against internal refactors that don't cross it.

| Change to the package | Bump |
|---|---|
| Backward-compatible bug fix — internal correction with no change to the declared public API | patch |
| Backward-compatible addition — new exported capability, a broadened accepted input, a new optional parameter | minor |
| Backward-incompatible change — a signature change, a removed or renamed export, a behavior change anyone relying on the prior version would notice | major |

## Propagate to consumers

A version bump alone does not update anything downstream. Each consuming
repo has to re-pin the new version, reinstall, and re-run its own validation
— on its own cadence, per that consumer's own pinning policy. This skill
cannot enumerate or update those consumers; that list and policy live in the
package's own docs.

## Checklist

Emit this on trigger and verify each item before committing:

```
npm semver checklist:
- [ ] the package's package.json `version` bumped
- [ ] Bump magnitude matches the change against the package's declared public API (see table above)
- [ ] Consuming repos identified and reminded to re-pin, reinstall, and re-run their own validation
```
