---
name: lovable-cloud-migration-author
description: >
  Supabase migration file authoring in a Lovable Cloud project — UTC timestamp
  enforcement via the new-migration generator.
  TRIGGER when: about to author a new Supabase migration file (Write to
  supabase/migrations/*.sql with a new timestamp).
  DO NOT TRIGGER when: editing an existing migration; not a Supabase project.
user-invocable: false
---

# Lovable Cloud — Migration Authoring

Supabase applies migrations in **filename-lexical order**. Lovable Cloud
timestamps migration filenames in UTC. Claude Code's local clock may be in a
different timezone, so every Claude-authored migration filename must also use
UTC to preserve apply order.

## Workflow

1. **Generate the filename.** Run the `new-migration` script with a short slug
   that describes the migration's purpose:

   ```bash
   $(${CLAUDE_PLUGIN_ROOT}/scripts/new-migration "<your-slug>")
   ```

   (`CLAUDE_PLUGIN_ROOT` is resolved to the full path in the deny message if triggered.)

   The script prints a UTC-timestamped filename, e.g.
   `20260612191215_add-co-parent-index.sql`, and writes a one-shot
   authorization token.

2. **Write the migration at the printed path.** Use the exact filename the
   generator printed:

   ```
   supabase/migrations/<printed-filename>
   ```

   The PreToolUse hook checks for the token before allowing the Write. If the
   filename was not produced by the generator, the Write is denied with a
   reminder to use the generator.

## Key rules

- Always call `new-migration` first — a hand-typed filename without a token
  will be blocked.
- The generator emits to stdout; capture it and use it as the migration path.
- One token per generated filename: the PostToolUse hook consumes the token
  after the Write succeeds, so a given filename can only be written once
  (re-generate if the Write is retried).
- Lovable-emitted UUID filenames (e.g. `20260612191215_fa10d453-8db5-...sql`)
  are exempt from the token check — those are managed by Lovable, not Claude.
