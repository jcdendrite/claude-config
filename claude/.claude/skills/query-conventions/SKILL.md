---
name: query-conventions
description: >
  Conventions for writing read-path database queries (SQL, PostgREST/Supabase
  `.from(...).select(...)`, ORMs, or raw drivers): pagination / explicit
  limits, avoiding N+1 from array iteration, explicit column selection, and
  the AI anti-patterns that silently scale badly. Write-path idempotency
  conventions live in project-level guardrails and in `test-conventions`,
  not here.
  TRIGGER when: writing or modifying a read-path query — adding a `SELECT`,
  a `.from(...)` / `.select(...)` chain, a list-returning ORM call, raw
  read SQL, or data-access helper; designing query shape or pagination.
  DO NOT TRIGGER when: writing INSERT/UPDATE/DELETE or other write-path
  code, reading existing queries without modification, editing test mocks
  or fixture seed data, purely-schema migrations (DDL only, no DML), or
  generated client code.
user-invocable: false
---

# Query Conventions

Three rules. Each one is an AI anti-pattern that scales badly and is
easy to miss in review because the query "works" at current data volume.

## 1. Every list query needs an explicit limit

Never emit an unbounded list query against a table that can grow. Always
set a limit — `LIMIT N` in SQL, `.limit(N)` / `.range(start, end)` in
PostgREST, `take` / `first` in ORMs. Do not rely on server defaults (no
ceiling in PostgREST; unbounded for raw Postgres).

- If the caller supplies a page size, validate it against a hard max.
- For "I just want one row" queries, use `.single()` / `LIMIT 1` rather than relying on the table having one row. The implicit assumption rots as data grows.

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
- SQL/PostgREST: `WHERE id IN (:ids)` / `.in('id', ids)`.
- Joins: if the loop is "for each row, fetch related row," use a join.
- Complex batching: wrap in a server-side function / stored procedure.

Why: loops over async DB calls scale to O(N) round-trips. The test suite
sees 2 items and passes in 50ms; production sees 500 and takes 25 seconds.

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
- **`SELECT COUNT(*)` for "does any row exist?":** use `SELECT 1 ... LIMIT 1` or `.limit(1).single()`. `COUNT(*)` scans the table.
- **Unindexed filter / sort columns:** if a list query filters or orders by a column that isn't indexed, call it out. Add a migration, or document the known scan if it's acceptable.
- **Reading a whole table to compute something that belongs in the database:** if the answer is a scalar (sum, max, rank), compute it in SQL — do not fetch rows to JavaScript/Python and reduce there.
