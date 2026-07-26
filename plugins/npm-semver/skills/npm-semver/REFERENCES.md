# References — npm-semver

Source behind the bump-magnitude rules in SKILL.md. Not loaded at skill runtime;
read manually when verifying a rule still holds or adding new guidance.

## Semantic Versioning 2.0.0
https://semver.org/spec/v2.0.0.html

Grounds the three rows of the bump table in the "Bump magnitude" section, which
restate these three cases against a package's declared public API:

> "increment the: MAJOR version when you make incompatible API changes, MINOR
> version when you add functionality in a backward compatible manner, PATCH
> version when you make backward compatible bug fixes."

Grounds the declared-public-API precondition, and the heuristic the skill body
keeps in bare prose — that uncertainty about whether something belongs to the
public API means the surface was never declared, rather than meaning the bump
rules need another case:

> "If you're not sure whether to declare something as part of your public API,
> you're probably worried about the wrong things."
