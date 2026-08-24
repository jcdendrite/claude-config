---
paths:
  - "**/*.sql"
---

## Raw SQL & DDL conventions

Hand-written SQL: migrations, DDL, schema, functions. (Read-path SELECT /
pagination tuning lives in the `sql-query-conventions` skill — this rule
doesn't overlap it.) Postgres idioms below are illustrations — apply the
equivalent for your engine.

See `docs/rules-references.md` for sourcing and verification notes.
`lock_timeout`, `CREATE INDEX CONCURRENTLY`, `NOT VALID`/`VALIDATE CONSTRAINT`,
and RLS-fail-closed semantics are standard documented Postgres behavior;
re-verify against current docs if precision matters.

### Migration safety
- **Set a low `lock_timeout` before running DDL.** A blocked ALTER/CREATE
  INDEX sits at the head of the lock queue and blocks every query behind it —
  fail fast and retry rather than stalling all traffic.
- **`CREATE INDEX CONCURRENTLY` on any non-trivial table** — a plain
  `CREATE INDEX` blocks writes for the whole build. `CONCURRENTLY` cannot run
  in a transaction block, belongs in its own migration, and on failure leaves
  an INVALID index behind that must be explicitly `DROP`ped and rebuilt, not
  just re-run.
- **Adding a column with a volatile DEFAULT (e.g. `clock_timestamp()`,
  `random()`) forces a full-table rewrite; non-volatile defaults (e.g.
  `now()`, which is STABLE) don't.** Promoting to `NOT NULL` still full-scans
  to validate — on large tables, add nullable, backfill in batches, then set
  the constraint.
- **Add FK/CHECK constraints as `NOT VALID`, then `VALIDATE CONSTRAINT`
  separately.** A plain **CHECK** constraint takes ACCESS EXCLUSIVE and
  full-scans (blocks reads and writes); a plain **FK** constraint takes the
  lighter SHARE ROW EXCLUSIVE (on both tables) but still full-scans and
  blocks writes for the duration. Either way, `NOT VALID` skips the scan
  (enforces new rows only) and the later `VALIDATE` takes a lighter lock
  (SHARE UPDATE EXCLUSIVE), which is why the two-step form is preferred for
  both constraint types.
- **`ALTER TYPE ... ADD VALUE` runs inside a transaction (since PG12), but the
  new value can't be referenced until that transaction commits.**
- **Destructive changes (`DROP`, type narrowing, rename) use expand/contract**
  — add the new shape, deploy, migrate consumers, then remove the old shape —
  with a rollback path; old and new code must both run across the deploy
  window.

### Access control on new objects
- **Create the table/view, `ENABLE ROW LEVEL SECURITY`, at least one explicit
  policy, and grants — all in the same migration.** A new table exposed on an
  auto-generated API (PostgREST/Supabase) with no policy is a data-exposure
  default.
- **RLS enabled with zero policies denies all rows to ordinary roles
  (fail-closed) — but you still need at least one explicit policy for
  legitimate access.**
- **Table owners and `BYPASSRLS` roles always bypass RLS regardless of
  policies** — use `ALTER TABLE ... FORCE ROW LEVEL SECURITY` if the querying
  role can own the table.

### Schema choices with downstream cost
- **`timestamptz` over `timestamp`** — naive timestamps are timezone-ambiguous
  and break replication/CDC correctness and cross-region ordering.
- **Explicit `numeric(p,s)` for money/quantity, never float** — binary float
  causes non-deterministic rounding and non-idempotent aggregates downstream.
- **Include `created_at`/`updated_at` (+ a soft-delete marker where
  relevant); update `updated_at` on every mutation** (a trigger or app-level
  write — Postgres doesn't auto-maintain it). A stale `updated_at` is worse
  than none: absence, or staleness, forces full snapshots instead of
  incremental loads downstream.
- **Give every table a *stable* key; where the natural or composite key is
  mutable, add a surrogate.** Not "every table needs a surrogate" — a junction
  table keyed by two stable surrogate FKs, or an append-only event table keyed
  by a stable `event_id`, already satisfies this. Mutable natural keys and
  surrogate-less composite PKs break as-of/SCD2 history.
- **Constrain fixed categorical columns to a bounded domain** — a
  reference/lookup table (often more ELT-friendly than a native `ENUM`, which
  is painful to alter later) or a `varchar` + `CHECK` — not free `TEXT`, which
  drifts casing/synonyms silently and defeats downstream columnar dictionary
  compression.
- **Typed columns over wide `jsonb`** for fixed-shape data; reserve `jsonb`
  for genuinely dynamic shapes.
- **Index the columns new queries filter/join/order on, especially foreign
  keys on growing tables** — Postgres auto-indexes PK/unique constraints but
  NOT FK-referencing columns; an unindexed FK forces a full scan of the child
  table (and holds locks for the scan's duration) to enforce referential
  integrity on parent DELETE/UPDATE. (This is a source-side OLTP performance
  concern, not itself an ELT-readiness property.)
- **Never rename or drop a column in place without a deprecation window** —
  same expand/contract mechanism as the migration-safety bullet above, applied
  to a different consumer: downstream models, marts, and dashboards bind by
  column name and break silently, not just deploy-time app code.
