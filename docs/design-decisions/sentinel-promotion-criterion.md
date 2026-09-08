# Sentinel promotion criterion: machine -> machine-promptable

*2026-08-11. Formerly `docs/design-decisions.md` §23.*

`install.sh`'s `SENTINEL_INVENTORY` array classifies each machine-level opt-in sentinel as either `machine` (report-only — mentioned in docs, never offered interactively) or `machine-promptable` (offered as a `[y/N]` prompt on every `./install.sh` run). Two rows, `.error-mode-nudge-enabled` and `.cost-ledger-enabled`, were reclassified from `machine` to `machine-promptable` after auditing all six `machine`-scope rows against an explicit criterion, both legs of which must hold:

1. **State is plain boolean file-presence, not a content-based value.** A sentinel whose meaning depends on file *content* (e.g. a mode string) has no natural mapping onto a `[y/N]` prompt and stays report-only (account scope) instead.
2. **Enabling the file opts INTO a new, off-by-default capability, not OUT of an already-on-by-default one.** Asking a contributor to disable a default they haven't yet experienced at install time is premature — a kill-switch sentinel (e.g. `.handoff-nudge-disabled`) stays report-only until the contributor has lived with the default long enough to want to suppress it.

The other four `machine`-scope rows audited (`.handoff-nudge-disabled`, `.consume-durable-continuity-disabled`, `.commit-stall-block-disabled`, `.session-title-disabled`) are all kill switches — each fails leg 2 — and stay report-only.

This criterion is structural only: it tests the sentinel's state shape and opt-in direction, not the security weight of the capability it gates. `worktree-required` and `autonomous-shipping-required` — two of the three pre-existing `machine-promptable` rows, both security/governance controls — happen to also satisfy both legs, but that is incidental, not evidence the criterion accounts for security impact. A future sentinel that is itself a security control (auth/authz gating, privilege grant) satisfying both legs still needs a separate security-impact discussion before promotion; this criterion alone is necessary but not sufficient for that case.
