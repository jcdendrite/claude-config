---
name: sql-query-conventions
description: >
  Conventions for writing read-path queries against SQL / relational
  stores (raw SQL, PostgREST/Supabase `.from(...).select(...)`,
  SQL-shaped ORMs): pagination, limits, avoiding N+1, batch-size
  ceilings, explicit column selection, and the AI anti-patterns that
  silently scale badly. Write-path idempotency conventions live in
  project-level guardrails and in `test-conventions`, not here.
  TRIGGER when: writing or modifying a read-path SQL or PostgREST
  query — adding a `SELECT`, a `.from(...)` / `.select(...)` chain, a
  list-returning ORM call, raw read SQL, or data-access helper;
  designing query shape, pagination, or batch size.
  DO NOT TRIGGER when: writing INSERT/UPDATE/DELETE or other write-path
  code, reading existing queries without modification, editing test
  mocks or fixture seed data, purely-schema migrations (DDL only, no
  DML), generated client code, or writing queries against document/KV
  stores (DynamoDB, MongoDB, Cassandra, etc. — those have different
  read-path conventions and belong in a separate skill).
user-invocable: false
---

# SQL Query Conventions

Read-path conventions for relational stores. Each rule below names an
AI anti-pattern that scales badly and is easy to miss in review because
the query "works" at current data volume.

## 1. Every list query needs an explicit limit

Never emit an unbounded list query against a table that can grow. Always
set a limit — `LIMIT N` in SQL, `.limit(N)` / `.range(start, end)` in
PostgREST, `take` / `first` in ORMs. Do not rely on server defaults (no
ceiling in PostgREST; unbounded for raw Postgres).

- If the caller supplies a page size, validate it against a hard max.
- **Single-row queries need the right tool.** `.single()` (PostgREST)
  *errors* if the result is not exactly one row — use it when the
  caller expects exactly one and wants to surface the violation.
  `.maybeSingle()` / `LIMIT 1` returns zero-or-one silently — use that
  when absence is a valid outcome. Conflating them teaches a bug either
  way (spurious errors on zero rows, or silently-wrong answers on
  multiple).
- **Stable pagination requires a total-ordered `ORDER BY`.** Without
  one, `LIMIT` / `OFFSET` can return arbitrary rows and shuffle between
  pages. Always include a deterministic tiebreaker (primary key).
- **Prefer keyset pagination over offset for large page numbers.**
  `OFFSET N` is O(N) in Postgres — the database must scan and discard N
  rows. Keyset form: `WHERE (created_at, id) < (:last_ts, :last_id)
  ORDER BY created_at DESC, id DESC LIMIT N`. Constant cost per page.

Why: unbounded queries DoS the database and the caller's memory under
growth. The bug appears at the scale where it hurts most.

## 2. No N+1 from iterating async DB calls

Never loop over records with per-item database calls. This is the most
common AI anti-pattern in data-access code.

```
# WRONG
for id in ids:
    row = await db.from('table').select().eq('id', id)
    results.append(row)
```

Fix by batching:
- **SQL/PostgREST:** `WHERE id IN (:ids)` / `.in('id', ids)`.
- **Joins:** if the loop is "for each row, fetch related row," use a
  join.
- **Complex batching:** wrap in a server-side function / stored
  procedure.

**If the list can grow unbounded, chunk it.** `IN`-list limits by
backend:

| Backend | Practical ceiling | When to switch approach |
|---|---|---|
| Postgres (driver bind params) | ~1,000–5,000 | Use `= ANY($1::int[])` — one bind param, unbounded by protocol |
| PostgREST / Supabase `.in()` | ~few hundred | URL-length bound (nginx/Kong default ~4–8 KB); switch to an RPC taking an array param |
| BigQuery | ~1,000 | Switch to `UNNEST(@ids)` or JOIN against a temp/staging table — 1 MB query text limit, planner broadcasts big IN lists |
| Snowflake | 16,384 IN expressions | Switch to `ARRAY_CONTAINS` / temp-table join well before the hard limit |

Why: loops over async DB calls scale to O(N) round-trips. The test suite
sees 2 items and passes in 50ms; production sees 500 and takes 25 seconds.
Big `IN` lists compound this with planner-side cost that grows with
list length and round-trip-size that blows through driver limits.

## 3. Prefer explicit column selection over `SELECT *`

When the table may grow wide columns (blobs, JSON, vectors, or
PII-sensitive fields), select only the columns the caller needs.

- Use `SELECT col1, col2` / `.select('col1, col2')` for read paths with a known shape.
- `SELECT *` is fine for internal admin tooling and tests.
- When a new wide column is added, callers that use `SELECT *` silently start fetching extra bytes — detectable only by someone spotting the bandwidth change.

Why: `SELECT *` couples read-path performance to schema evolution. Explicit
columns make the coupling local to the callsite that actually needs the
new column.

## Anti-patterns adjacent to these rules

- **Check-then-insert:** `SELECT ... EXISTS` followed by `INSERT` is racy. Use `INSERT ... ON CONFLICT` with a unique constraint.
- **Read-then-write without locking:** for the legitimate case of
  "read a row, decide based on its content, update it" in a
  transaction, use `SELECT ... FOR UPDATE` to lock the row until commit.
  Without the lock, a concurrent writer can change the row between
  your SELECT and UPDATE, and your decision is based on stale data.
- **`COUNT(*)` for existence checks:** use `EXISTS (SELECT 1 FROM ... WHERE ...)` or `SELECT 1 ... LIMIT 1` — both short-circuit on the first matching row. `COUNT(*)` reads every matching row even when you only need to know "any?". For actual counts (dashboard "N total"), `COUNT(*)` is the right tool; with a matching index and `WHERE` clause Postgres can plan an index-only scan.
- **Unindexed filter / sort columns:** if a list query filters or orders by a column that isn't indexed, call it out. Add a migration, or document the known scan if it's acceptable.
- **Reading a whole table to compute in app code what belongs in the database:** if the answer is a scalar, an aggregate, or a ranked/partitioned result, compute it in SQL — do not fetch rows to JavaScript/Python and reduce there. Specifically: `GROUP BY` for aggregates, `ROW_NUMBER() / RANK() / DENSE_RANK() OVER (PARTITION BY ... ORDER BY ...)` for ranking or "first N per group," `SUM/AVG/MIN/MAX` with `FILTER (WHERE ...)` for conditional aggregates. Most of these exist because AI agents don't reach for window functions by default.

## Verifying query plans

When a query involves a new index, a large `IN` list, a join order that
could be wrong, or a filter on a column whose selectivity is unclear,
verify with `EXPLAIN` (plan only) or `EXPLAIN ANALYZE` (plan + actual
execution) before declaring the query good. Specifically check:

- Did the planner use the expected index (`Index Scan` / `Index Only
  Scan`) rather than a `Seq Scan`?
- Is the join order sensible? A `Hash Join` over 10M rows when a
  `Nested Loop` over 20 indexed rows was intended is a red flag.
- Does the `Rows` estimate match reality (from `ANALYZE` output)? A
  large misestimate means out-of-date stats — run `ANALYZE <table>`.

Don't skip this step on the basis of "the query is simple" — planner
surprises on simple queries are how indexes silently stop being used.
