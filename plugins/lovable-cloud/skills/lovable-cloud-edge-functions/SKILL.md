---
name: lovable-cloud-edge-functions
description: >
  Guide for adding, modifying, and deploying Supabase edge functions —
  authentication, config.toml settings, and Lovable Cloud deployment.
  TRIGGER when: adding, modifying, or reviewing edge functions in a Lovable
  Cloud project, or modifying supabase/config.toml.
  DO NOT TRIGGER when: not working on edge functions or config.toml.
user-invocable: false
---

# Edge Function Authentication Framework

## Background: ES256 and the hosted gateway

Supabase's hosted gateway `verify_jwt` setting **cannot verify ES256-signed
user JWTs** — it only supports HS256 verification. Supabase Cloud projects
using ES256 signing keys must disable gateway JWT verification for
browser-invoked functions and rely on in-code auth instead.

Service-role keys use HS256, so `verify_jwt = true` still works for functions
called exclusively with the service role key.

Reference: https://supabase.com/docs/guides/functions/auth

## Auth tiers

### Tier 1: Browser-invoked functions

Functions called from the browser via `supabase.functions.invoke()`.

| Setting | Value |
|---------|-------|
| `verify_jwt` | `false` |
| In-code auth | User JWT validation (e.g., `requireUserJwt()` or equivalent) |

**Why:** The browser sends an ES256 user JWT. The gateway cannot verify it.
In-code auth is the **only** access control for these functions — there is no
defense-in-depth layer. This makes in-code auth removal especially dangerous
for browser-invoked functions.

> _These patterns were extracted from a production Lovable Cloud project and genericized. Adapt file paths and error-code namespaces for your project._

Your project will have its own auth utility location and naming convention for the user JWT validation helper.

### Tier 2: Service-role / cron / trigger functions

Functions called by pg_cron, pg_net database triggers, or other edge functions
using the service role key. Not called by browsers.

| Setting | Value |
|---------|-------|
| `verify_jwt` | `true` |
| In-code auth | Service-role key validation (e.g., `requireServiceRole()` or equivalent) |

**Why:** Service-role keys use HS256, which the gateway can verify. Both the
gateway and in-code auth enforce access — defense in depth.

### Tier 3: Webhook functions

Functions called by external services (Stripe, Lovable, etc.) that cannot
send a Supabase JWT of any kind.

| Setting | Value |
|---------|-------|
| `verify_jwt` | `false` |
| In-code auth | Signature verification (Stripe signature, HMAC, etc.) |

**Why:** External callers have no Supabase JWT. The function authenticates the
caller by verifying a request signature specific to that service.

### Tier 4: Intentionally public functions

Functions that are designed to be publicly accessible (e.g., sitemap, health
check).

| Setting | Value |
|---------|-------|
| `verify_jwt` | `false` |
| In-code auth | None — but must not expose secrets or sensitive data |

**Why:** No auth needed by design. The `verify_jwt = false` setting and
absence of in-code auth must be justified with a comment in `config.toml`
and in the function source.

## config.toml rules

`supabase/config.toml` is **security-critical infrastructure**.

1. **Never regenerate, overwrite, or remove entries** from `config.toml`.
2. Every edge function in `supabase/functions/` **must** have an explicit
   `[functions.<name>]` section with a `verify_jwt` setting.
3. **Never bulk-flip** existing `verify_jwt` entries. Each function's setting
   was chosen individually based on its tier. If a single function needs to
   change, change that one function and document why.
4. When changing a function's `verify_jwt` setting, verify it matches the
   correct tier above. Flipping a non-browser function from `true` to `false`
   removes a defense layer and requires justification.

## Adding a new edge function

1. Determine the function's tier (browser, service-role, webhook, or public).
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

For browser-invoked functions (Tier 1), in-code auth is the **sole** access
control — removing it makes the function fully open. This is more critical
than for Tier 2 functions, where the gateway provides a backup layer.

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
