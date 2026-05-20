---
name: lovable-cloud-migration-sync
description: >
  Post-apply cleanup for Lovable-duplicated migrations: diff originals, db reset, delete, open PR.
  TRIGGER when: the user signals Lovable's migration apply step completed, regardless of exact wording.
  DO NOT TRIGGER when: all originals already deleted, or user asks about mechanics without signaling completion.
user-invocable: true
disable-model-invocation: true
---

# Lovable Migration Sync

> _This skill was extracted from a production Lovable Cloud project and genericized. The workflow shape (identify unsynced migrations → diff against originals → db reset → delete originals → open PR) is generic; file names like `migration-sync-pr.yml` and test commands like `npm run verify` are project-specific examples — adapt for your project._

Lovable Cloud's Supabase instance has two non-obvious behaviors:

1. **It will not apply migration files it didn't create.** Migration files
   committed by Claude Code or engineers sit in the repo but are never
   executed on Lovable's database.
2. **Its migration runner is not a superuser.** Enforcement triggers fire
   during migrations, blocking data modifications to protected columns.

## Procedure

### 1. Identify unsynced migrations

Check the commit author for each migration file. Lovable commits use its bot
signature; anything else (Claude Code, engineer) is a candidate for being
unapplied:

```bash
git log --diff-filter=A --format='%an  %s' -- supabase/migrations/
```

Confirm by checking `src/integrations/supabase/types.ts` — if objects from a
migration are absent from types, that migration was not applied on Lovable Cloud.

### 2. Determine dependency order

Read the unsynced migration files and identify dependencies between them (e.g.,
table creation before FK references, function creation before trigger creation).
Order them so dependencies are satisfied.

### 3. Reference the PR comment (do not regenerate the message)

Your project's migration-sync workflow should post a comment on the PR with the
copy-paste Lovable message and a checklist. Direct the user to that comment —
do NOT regenerate the message in the terminal when the PR comment already exists.

The PR comment should include:
- The list of newly-added migration filenames.
- A copy-paste message in second person (to Lovable).
- A checklist item confirming Lovable has committed duplicate migrations.
- A caveat that ticking the box does not apply the migrations — only commits
  from Lovable's bot on `main` do.

**If the PR comment is missing**, the workflow has a bug — fix the workflow,
do not draft the message manually.

### 4. Pull and verify duplicates

After Lovable commits, switch to `main` and pull:

```bash
git stash  # if on a feature branch with uncommitted changes
git checkout main && git pull
```

Diff each Lovable-created duplicate against its original:

```bash
diff <(sed 's/[[:space:]]*$//' supabase/migrations/ORIGINAL.sql) \
     <(sed 's/[[:space:]]*$//' supabase/migrations/LOVABLE_UUID.sql)
```

**Acceptable differences:**
- Stripped or reworded comments (cosmetic)
- Added `DROP TRIGGER/POLICY IF EXISTS` before create (more idempotent)
- Added `IF NOT EXISTS` guards (more idempotent)
- Trigger disable/re-enable wrapping
- Extra `DROP POLICY IF EXISTS` for Lovable-specific policy name variants
- Whitespace, trailing newline differences

**Unacceptable differences:**
- Changed function logic (different WHERE clauses, missing conditions)
- Missing sections (tables, policies, triggers not created)
- Different column definitions or constraint logic
- Weakened security checks (removed role checks, relaxed RLS policies)

If unacceptable differences exist, do **not** proceed. Investigate and resolve
with the user.

### 5. Verify locally before deleting

Run `supabase db reset` followed by your project's verify entry point
(e.g. `npm run verify`, `pytest`, `make verify` — whatever its
verification command is) via the `Agent` tool with
`subagent_type: check-runner`. Enumerate both commands exactly in the
dispatch prompt and include the absolute working directory.

The subagent runs `supabase db reset` first to replay the migration set
against a clean database, then runs the verify entry point. Without the
reset, the verify run validates pre-replay state and the migration sync
is not actually exercised. NOTICEs from `DROP IF EXISTS` on
not-yet-created objects are expected during the reset and harmless —
Lovable duplicates run later in timestamp order than the originals did,
so the reset replays the originals' creation against the duplicates'
drops.

The subagent writes each command's full output to
`${TMPDIR:-/tmp}/<command-slug>-<epoch-ms>.txt` and returns: per-command
name/exit code/pass-fail, smallest failing excerpt, overall PASS or FAIL, and
the output file paths. If a step fails, Read the relevant output file rather
than re-running the suite. Do not proceed to step 6 (delete originals) on FAIL.

**Inline exception.** Single-test re-runs during debugging can stay inline.
Suite-level reruns route through the subagent.

### 6. Delete originals

```bash
git rm supabase/migrations/ORIGINAL_1.sql supabase/migrations/ORIGINAL_2.sql ...
```

### 7. Commit and PR

Create a branch, commit the deletions, push, and open a PR. The commit message
should reference which originals were replaced by which Lovable UUID files.
