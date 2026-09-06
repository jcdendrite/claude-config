# Three-tier redaction system

*Formerly `docs/design-decisions.md` §6.*

Tier 1 is always on and requires no setup: a regex blocks `[A-Z]{2,}-\d+` tokens not on the OSS allowlist, catching accidentally committed JIRA/Linear/GitHub tracker references before they land in a public commit or PR. Tier 2 is opt-in: the user drops `<config-dir>/private-projects.md` with client codenames; the hook does a case-insensitive whole-word literal match against every non-comment line. The opt-in design is deliberate — forcing everyone to maintain a blocklist they don't need creates friction for the majority to gain safety for the minority who work on sensitive projects. Tier 3 relies on reviewer discipline: structural fingerprints (domain vocabulary, architecture patterns that identify a client) can't be caught mechanically without false-positive rates that would make the hook unusable; that surface stays a human judgment call in the review step.
