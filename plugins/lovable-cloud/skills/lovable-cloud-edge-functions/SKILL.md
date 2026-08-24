---
name: lovable-cloud-edge-functions
description: >
  Supabase edge function authentication, config.toml, and Lovable Cloud
  deployment. TRIGGER when: adding, modifying, or reviewing an edge
  function in a Lovable Cloud project, or modifying a file that
  configures one (e.g. supabase/config.toml) or invokes one (e.g.
  pg_cron). DO NOT TRIGGER when: no edge function configured, invoked,
  or modified.
user-invocable: false
---

# Edge Function Authentication Framework

## Auth tiers

### Tier 1: Browser-invoked functions

Functions called from the browser via `supabase.functions.invoke()`.

| Setting | Value |
|---------|-------|
| `verify_jwt` | `false` |
| In-code auth | User JWT validation (e.g., `requireUserJwt()` or equivalent) |

**Why:** The gateway can't verify the browser's ES256 JWT, so in-code auth is the only access control for Tier 1 — removing it fully opens the function.

Adapt file paths and error-code namespaces to your project's own conventions.

Your project will have its own auth utility location and naming convention for the user JWT validation helper.

In-code JWT validation must cryptographically verify the signature and check `exp` — decode-only isn't sufficient since Tier 1 has no gateway backup.

### Tier 2: Service-role / cron / trigger functions

Functions called by pg_cron, pg_net database triggers, or other edge functions
using the service role key. Not called by browsers.

| Setting | Value |
|---------|-------|
| `verify_jwt` | `true` |
| In-code auth | Service-role key validation (e.g., `requireServiceRole()` or equivalent) |

**Why:** Service-role keys use HS256, which the gateway can verify — both gateway and in-code auth enforce access, giving defense in depth.

**Caution:** `verify_jwt=true` accepts any HS256 JWT including anon tokens, so in-code auth must check the decoded `role` claim equals `service_role` (never a caller-supplied header) or anon callers can invoke service-role functions.

### Tier 3: Webhook functions

Functions called by external services (Stripe, Lovable, etc.) that cannot
send a Supabase JWT of any kind.

| Setting | Value |
|---------|-------|
| `verify_jwt` | `false` |
| In-code auth | Signature verification (Stripe signature, HMAC, etc.) |

**Why:** External callers have no Supabase JWT, so the function authenticates them via a service-specific request signature instead.

**Critical:** Signature verification must consume the raw body (`await req.arrayBuffer()`/`await req.text()`) before any JSON parse — parsing first corrupts the HMAC check and can silently fall through to processing an unverified payload.

### Tier 4: Intentionally public functions

Functions that are designed to be publicly accessible (e.g., sitemap, health
check).

| Setting | Value |
|---------|-------|
| `verify_jwt` | `false` |
| In-code auth | None — but must not expose secrets or sensitive data |

**Why:** No auth needed by design, but `verify_jwt = false` with no in-code auth must be justified in a comment in both `config.toml` and the function source.

## config.toml rules

`supabase/config.toml` is **security-critical infrastructure**.

1. **Never regenerate, overwrite, or remove entries** from `config.toml`.
2. Every edge function in `supabase/functions/` **must** have an explicit
   `[functions.<name>]` section with a `verify_jwt` setting.
3. **Never bulk-flip** `verify_jwt` entries — change one function at a time and document why, since each setting was chosen per its own tier.
4. When changing a function's `verify_jwt` setting, verify it matches the
   correct tier above. Flipping a non-browser function from `true` to `false`
   removes a defense layer and requires justification.

## Adding a new edge function

1. Determine the function's tier. If the caller is an external service (not a browser user, not your own cron/trigger, not an explicitly no-auth endpoint like a sitemap or health check) — it is Tier 3, not Tier 4. Both Tier 3 and Tier 4 use `verify_jwt = false`, so miscategorizing Tier 3 as Tier 4 produces a silently open endpoint. Default to Tier 3 when uncertain about external callers.
2. Add a `[functions.<name>]` entry to `config.toml` with the correct
   `verify_jwt` setting per the tier table above.
3. Add a comment above the entry if `verify_jwt = false`, stating which tier
   applies and why.
4. Add the corresponding in-code auth call for the tier.
5. For browser-invoked functions: in-code auth is the **only** control.
   Do not skip it.

## In-code auth

Do not remove Authorization header validation or the in-code auth call from
edge functions.

Tier 1's in-code auth is the sole access control (no gateway backup, unlike Tier 2) — do not remove it.

For service-role functions (Tier 2), in-code auth is defense-in-depth on top
of gateway verification. Both should be present.

## Deployment: Lovable does NOT auto-deploy external changes

Edge functions added or modified by Claude Code or engineers are **not
automatically deployed** by Lovable. After merging a PR that adds, modifies,
or changes `config.toml` for edge functions, the user must explicitly ask
Lovable to deploy them. This applies to:

- New edge functions (code + config.toml entry)
- Modified edge function code
- `verify_jwt` changes in config.toml

Tell the user to send Lovable: `"Deploy these edge functions: <name1>, <name2>"`

Without this step, the function code is in the repo but returns 404 (new
functions) or runs with stale code/config (modified functions).
