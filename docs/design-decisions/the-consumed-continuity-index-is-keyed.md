# The consumed-continuity index is keyed per-EUID, so every process at that uid can enumerate recently-consumed slugs

*2026-09-05. Formerly `docs/design-decisions.md` §56.*

`_lib_resume_context_index_dir` (`claude/.claude/hooks/_lib.sh`) keys the consumed-continuity index by `$EUID` only, with no `$CLAUDE_CONFIG_DIR` dimension folded into the path. Every process at that EUID shares one set of day-files, and `find-consumed-continuity-file.sh` with no argument prints all of their still-live rows in one call. On a machine running several `$CLAUDE_CONFIG_DIR`-scoped Claude accounts under one OS user, that includes every one of those accounts' rows. `docs/hooks.md`'s `pr-cost-disclosure` and prose-tightening entries scope their own sentinels to `$CLAUDE_CONFIG_DIR` precisely so one account's opt-in doesn't activate under another; this index does not follow that convention.

The same per-uid write access this read-side risk describes also permits row forgery: any process at that EUID can append a row directly to a day-file, bypassing `record_consumed_destination` entirely, to redirect the recovery flow at a file of its own choosing or to inject a raw terminal escape into a field `find-consumed-continuity-file.sh` prints. That forgery risk is why the reader validates `$dest`'s tmpdir-root/basename contract before trusting it and sanitizes all three printed fields ($stamp, $dest, $src), not only $src — see `find-consumed-continuity-file.sh`'s own header for the mechanics.

That tmpdir-root/basename contract narrows where a forged row's `$dest` can point, but does not verify provenance: it confirms `$dest` lives at `$TMPDIR_ROOT/resume-context.*` and is owned, not a symlink, but never confirms the file was actually produced by `record_consumed_destination`'s own `mktemp` call. A co-resident process at the same EUID can still plant its own file at a conforming path, with fully attacker-authored content, and forge a row pointing at it — that row passes every check above, and this script recommends the file via `claude --append-system-prompt-file`. Accepted as a scoped tradeoff, not an oversight: closing it would need a provenance mechanism (e.g. tagging each written file so the reader can verify it against the index row) on top of the location/ownership/naming contract this design already relies on, and the underlying write access is the same per-uid multi-writer surface already accepted for the read-side risk above.

The destination filename is already fixed and non-descriptive, independent of this index: `resume-context.sh` uses a fixed, non-descriptive `mktemp` prefix specifically so `/tmp` doesn't leak a continuity file's slug via `ls`, and every process at that uid writes into the same tmp root. This index's `$src` field is the original, descriptive `<config-dir>/handoffs/<slug>-handoff.md` (or `briefs/`) path, readable in one command by any process at that uid rather than requiring a content grep of every `resume-context.*` file in `/tmp`.

Accepted as a scoped tradeoff, not an oversight. The threat model this feature targets is a different-uid adversary on a shared box — the same boundary `/tmp`'s own sticky bit and this index's ownership/symlink guards already draw. A same-EUID reader was never in scope. Folding `$CLAUDE_CONFIG_DIR` into the index path would stop two cooperating accounts from commingling rows by accident; it would not create an isolation boundary, since any process at that uid can read any of those directories regardless of path. Closing the residual against a hostile same-uid process needs a different mechanism and its own stated goal.

**Revisit** if any of:

- Account-level isolation on one uid becomes a stated goal elsewhere in this repo, not just an assumption this index happens to rely on.
- A concrete report surfaces of a consumed handoff/brief slug reaching a reader its consuming operator did not intend — a co-resident account reading another's row is one example, not the only shape.

The same-EUID `$src` exposure accepted here also carries [§57](libsanitizeforterminal-drops-its-bidizero-width-code.md)'s bidi/zero-width residual: a reading process renders a crafted `$src` written by a different process at the same EUID without ever loading that writer's file content into its own system prompt, so [§57](libsanitizeforterminal-drops-its-bidizero-width-code.md)'s dominance argument (real content already loaded) doesn't transfer across the writer/reader boundary this entry accepts. This entry predates [§57](libsanitizeforterminal-drops-its-bidizero-width-code.md)'s collapse and didn't have that factor in view; it's folded in here rather than reopening the tradeoff above.

## Sources

- `.claude/plans/handoff-consume-tmp-index.md` — Assumption ledger row 20, and the "Out of scope" section's same-uid multi-writer residual.
- `claude/.claude/scripts/resume-context.sh` — the destination-filename non-goal this index's `$src` field newly extends past, and its own header note pointing here.
- `docs/scripts.md`'s `find-consumed-continuity-file.sh` entry — the reader-side cross-reference to this entry.
- `docs/hooks.md`'s `pr-cost-disclosure` and "Prose tightening opt-out" entries — the existing `$CLAUDE_CONFIG_DIR`-scoping convention this index departs from.
