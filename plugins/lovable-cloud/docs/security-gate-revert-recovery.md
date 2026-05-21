# Security-Gate Revert Recovery Runbook

This runbook applies when a downstream security-gate workflow rejects a Lovable-bot commit that touched `supabase/migrations/*.sql`. The lasting fix is to **prevent** the gate from running `git revert` on a migration commit in the first place — see the "Prevention" section at the bottom of this runbook. The procedure below reconciles repo↔DB drift when a revert has already shipped.

## Why this runbook exists

The Lovable Cloud build applies a migration's SQL to the Cloud database **before** any downstream gate runs. A `git revert` of the commit removes the migration file from `main` while the SQL remains live in `pg_proc`, `pg_policies`, `pg_class`, etc. The database is now permanently ahead of `main`, and the only record of what's running in prod sits on a `lovable-backup-main-*` branch nobody is watching.

## When to use this runbook

Use this runbook when **all three** are true:

- A `git revert` of a Lovable-bot commit landed on `main`.
- The reverted commit contained at least one `supabase/migrations/*.sql` file.
- DB inspection confirms the reverted DDL is live in the Cloud database.

If the reverted commit had no migrations, the gate behaved correctly — no recovery needed.

## Step 1. Read the verbatim reject reason — judge ratify vs. compensate

Read the verbatim reject reason from the tracker issue (do not paraphrase or keyword-match). If neither branch below affirmatively applies, ask a human before drafting the reconciling migration.

- **Compensate** when the SQL is genuinely unsafe — over-broad grant (`EXECUTE` to `PUBLIC` on `SECURITY DEFINER`), unrestricted write grant to `anon`, predicate-stripped RLS (`USING (true)`, `WITH CHECK (true)`), `SECURITY DEFINER` escalation, missing `search_path` pin. Author a reconciling migration that walks the unsafe object back.
- **Ratify** only when the rejection was policy-fit (out-of-band schema namespace, missing review step) **AND** a privilege-delta audit confirms no permission grant, RLS predicate change, or role elevation. Author an idempotent migration that re-records the already-live DDL.

The branch is **structurally asymmetric**: ratify requires positive proof of no privilege delta. An unknown or ambiguous pattern defaults to compensate (or asking a human) — never ratify by default.

## Step 2. Detect orphaned objects

Find suspected gate-reverts in history:

```bash
git log --author='github-actions[bot]' --grep='^Revert' -- supabase/migrations/
```

For each reverted migration, inspect what is live in the DB but absent from the repo. The queries below cover function signatures, policy bodies, and column grants; also check `pg_trigger`, `pg_extension`, table-level ACLs, and `pg_class.relrowsecurity` for objects the reverted DDL touches:

```sql
-- Function signature disambiguation
SELECT n.nspname, p.proname, pg_get_function_identity_arguments(p.oid)
FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE p.proname = '<function_name>';

-- Policy body — diff against the migration's CREATE POLICY
SELECT schemaname, tablename, policyname, cmd, qual, with_check
FROM pg_policies WHERE tablename = '<table>';

-- Column-grant audit
SELECT grantee, privilege_type, table_schema, table_name, column_name
FROM information_schema.column_privileges WHERE table_name = '<table>';
```

## Step 3. Author the reconciling migration, then sync via the migration-sync skill

**Compensate**: walk each unsafe object back — `REVOKE` over-broad grants, re-`CREATE OR REPLACE POLICY` correctly, re-define `SECURITY DEFINER` functions with `SET search_path` pinned to referenced schemas + `pg_catalog, pg_temp` (do not copy the original's unpinned setting).

**Ratify**: write idempotent `CREATE OR REPLACE` / `DROP ... IF EXISTS` SQL matching the live DB. Lovable will duplicate the file; idempotent guards keep the duplicate safe against pre-existing state.

In both cases the migration header must record the reverted SHA, verbatim reject reason, and branch chosen (ratify or compensate). This audit trail is mandatory — a future responder reading the file in `git blame` must be able to reconstruct why this migration exists without re-reading the tracker issue.

Then run the `lovable-cloud-migration-sync` skill (`plugins/lovable-cloud/skills/lovable-cloud-migration-sync/SKILL.md`) to sync the new migration through Lovable. The skill's existing Procedure (diff against Lovable's duplicate, `db reset`, verify, delete originals, PR) handles the apply-side mechanics.

## Prevention

The recovery procedure above only exists because the gate workflow ran `git revert` on a migration commit. The lasting fix lives **upstream of recovery**: patch the gate so it never runs `git revert` on a commit that touched `supabase/migrations/*.sql`. Instead, the gate should:

1. Detect that the rejected commit contains migration files (`git diff-tree --no-commit-id --name-only -r <SHA> | grep -E '^supabase/migrations/.*\.sql$'`).
2. **Skip** the revert step entirely — leave the commit on `main` so repo state matches DB state.
3. Open a high-severity tracker issue tagged "db-drift-risk" containing the rejected SHA, the verbatim gate reject reason (no paraphrase, no keyword compression), and the list of migration filenames in the commit.
4. Point the issue body at this runbook so the responder reaches Step 1 directly.

That patch lives in the consumer's gate-workflow YAML, not in `claude-config`. With it in place, this runbook becomes a backstop for legacy gate-revert artifacts rather than an ongoing recovery surface.
