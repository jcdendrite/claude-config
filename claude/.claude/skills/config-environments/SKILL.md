---
name: config-environments
description: >
  Design and review configuration that differs across environments (dev,
  staging, production): env var naming, credential isolation, secrets
  provisioning, and the anti-patterns that reintroduce tight coupling.
  TRIGGER when: designing new env-var schemes, reviewing code that branches
  on environment to pick credentials/config, proposing or reviewing variable
  names with environment suffixes (`_DEV`, `_PROD`, `_STAGING`), or planning
  secret-isolation strategies across dev and prod.
  DO NOT TRIGGER when: the config genuinely needs to hold both values
  simultaneously in the same process (migrations reading `OLD_*` and `NEW_*`
  at once, test harnesses stubbing real values, feature flags orthogonal to
  environment), or when the task is a single-environment app with no
  per-env variation.
user-invocable: false
---

# Config Across Environments

## Core principle

**Use the same variable name in every environment; different deployments populate it with different values.**

```
# Production (Vercel / Railway / Fly env store)
DATABASE_URL=postgres://prod-host/app

# Local (.env.local, not committed)
DATABASE_URL=postgres://localhost/app
```

Not:

```
# Anti-pattern — encoding environment in the variable name
DATABASE_URL_PROD=postgres://prod-host/app
DATABASE_URL_DEV=postgres://localhost/app
```

The environment is **implicit in which config source is loaded**, not explicit in the variable name.

## Runtime env-access APIs (quick reference)

| Runtime | Idiom |
|---|---|
| Node.js, Next.js server | `process.env.FOO` |
| Bun | `process.env.FOO` or `Bun.env.FOO` |
| Deno, Deno Deploy, Supabase Edge Functions | `Deno.env.get('FOO')` |
| Cloudflare Workers | `env.FOO` (typed via `Bindings`) |
| Browser bundles | Build-time only: Vite `import.meta.env.VITE_FOO`, Next.js `process.env.NEXT_PUBLIC_FOO` inlined at build |

Don't mix idioms across modules in the same runtime — it makes scattered reads harder to grep and breaks the "read once" pattern below.

## Why

- **12-Factor §III Config** explicitly rejects environment-named variables: *"env vars are granular controls, each fully orthogonal to other env vars. They are never grouped together as 'environments'."*
- **Suffix patterns duplicate logic.** The "which environment am I?" decision is already encoded in which config source loaded. Adding `_DEV` / `_PROD` to variable names fans that decision out to runtime code that branches on `NODE_ENV`, requiring both values to be present where only one is ever read, leaking the env selector into every consumer.
- **Rotation is cheaper with canonical names.** Rotating `STRIPE_SECRET_KEY` means updating one name in the prod store; rotating `STRIPE_SECRET_KEY_PROD` requires remembering which suffix prod reads and leaves a `_DEV` variant easy to update by mistake.
- **Mainstream frameworks resolve per-env config by file/store selection, not renaming:** Next.js lookup order, Rails per-env credentials, Kubernetes Kustomize overlays, Docker Compose `--env-file` — all load different sources for the same variable name.

## Anti-patterns to flag

### 1. Environment-suffixed variable names

```ts
// Bad
const clientId = process.env.NODE_ENV === 'production'
  ? process.env.OAUTH_CLIENT_ID_PROD
  : process.env.OAUTH_CLIENT_ID_DEV;

// Good
const clientId = process.env.OAUTH_CLIENT_ID;
```

### 2. Environment-branching for credential selection

```ts
// Bad
function getStripeKey() {
  return currentEnv() === 'production'
    ? Deno.env.get('STRIPE_SECRET_KEY_LIVE')
    : Deno.env.get('STRIPE_SECRET_KEY_TEST');
}
```

✅ **Recommended pattern:** the prod deploy populates `STRIPE_SECRET_KEY` with the live key; the dev `.env` populates it with the test key. Stripe's `sk_live_…` vs `sk_test_…` value prefix encodes the mode inside the *value* — no code branch needed.

### 3. Sentinel-value defaults that silently leak production-shaped state

```ts
// Bad — silently falls back to a prod-ish default
const bucket = process.env.STORAGE_BUCKET ?? 'prod-uploads';

// Good — missing config fails boot, not first write
const bucket = process.env.STORAGE_BUCKET;
if (!bucket) throw new Error('STORAGE_BUCKET missing');
```

**Convention defaults are fine** for non-sensitive ergonomics: `PORT ?? 3000`, `LOG_LEVEL ?? 'info'`, `MAX_RETRIES ?? 3`. The rule: if the default can point at live production state in the wrong env, fail fast (`STORAGE_BUCKET`, `DATABASE_URL`, `STRIPE_SECRET_KEY`). Otherwise a default is fine.

### 4. Assuming client-side frameworks "know" the environment

Client bundles (Vite, Next.js) bake env vars in at build time — the bundle is **frozen** with the values present when `next build` / `vite build` ran. Only `VITE_*` / `NEXT_PUBLIC_*` vars are inlined, at build time only. If CI builds the prod bundle under staging's env, the shipped prod bundle carries the wrong values permanently.

## Environment-aware *behavior* vs environment-aware *credentials*

- **Credential selection branching is the anti-pattern.** Reading `X_DEV` vs `X_PROD` based on `NODE_ENV` is duplicated logic.
- **Behavior branching can be legitimate.** If the *same canonical variable* drives the decision, the env check lives in one place and only affects behavior.

```ts
// Legitimate: behavior differs, credentials are a single-name read
const origins = currentEnv() === 'production' ? PRODUCTION_ORIGINS : DEV_ORIGINS;
const clientId = process.env.OAUTH_CLIENT_ID; // same name in every env
```

## When suffix patterns ARE legitimate

- **Co-located simultaneous values** — a migration script reading `OLD_DATABASE_URL` and `NEW_DATABASE_URL` in one process.
- **Test harness stubs** — `TEST_STRIPE_KEY` next to `STRIPE_SECRET_KEY` so a test hits test mode deliberately.
- **Feature flags orthogonal to environment** — `ENABLE_NEW_BILLING` is a rollout control, not an environment name.

If a single invocation needs both suffixed values at the same time, the suffix is fine. If it's just "I'm in dev vs prod," it's the anti-pattern.

## Centralize config at startup (`config.ts` pattern)

Read every env var **once**, in a single module, validate at process boot, freeze, and export a typed object.

```ts
// config.ts — the one place env vars are read.
import { z } from 'zod';

const schema = z.object({
  DATABASE_URL: z.string().url(),
  STRIPE_SECRET_KEY: z.string().startsWith('sk_'),
  OAUTH_CLIENT_ID: z.string().min(1),
  PORT: z.coerce.number().int().positive().default(3000),
  LOG_LEVEL: z.enum(['debug', 'info', 'warn', 'error']).default('info'),
});

export const config = Object.freeze(schema.parse(process.env));
```

Benefits: fail-fast (missing config crashes at boot), type safety, central audit surface (one grep of `process.env.`), testability (`vi.mock('./config', () => ({ config: { ... } }))`).

| Language / stack | Config module | Common libraries |
|---|---|---|
| TypeScript / Node / Bun | `src/config.ts` or `src/env.ts` | Zod, valibot, envalid, `@t3-oss/env-core` |
| Python | `config.py` or `settings.py` | `pydantic-settings`, `dynaconf` |
| Go | `internal/config/config.go` | `caarlos0/env`, `spf13/viper`; validate with `go-playground/validator` |
| Ruby / Rails | `config/environments/*.rb` + credentials | Built-in `Rails.application.credentials` |
| Java / Kotlin / Spring Boot | `@ConfigurationProperties` class | Jakarta Bean Validation |
| Rust | `src/config.rs` | `config`/`figment` + `serde`; validate with `validator`; `.env` via `dotenvy` |

## Isolation patterns that actually work

Enforce **credential isolation** at provisioning time, not in code:

- Register separate upstream credentials per env (OAuth clients, API keys, service accounts).
- Each env's config source holds only its own credential — prod values live only in the prod secret store.
- Code reads a single canonical name and trusts the source to have populated the right value.

## CI/CD and build-time config

- **GitHub Actions `secrets.*` vs `vars.*`.** `secrets.*` is masked in logs; `vars.*` is plaintext-logged — never put a secret in `vars.*`.
- **Build-time vs runtime env.** A Docker image built with `ENV FOO=bar` bakes `FOO` into every container. For `NEXT_PUBLIC_*` / `VITE_*`, build-time is the *only* injection point — see anti-pattern #4.

## Review checklist

1. **Are there variables whose names contain `_DEV`, `_PROD`, `_STAGING`, `_TEST`?** Flag unless the "simultaneous values in one process" criterion applies.
2. **Does the code branch on `NODE_ENV` / `ENVIRONMENT` to pick a credential?** Collapse to a single env var read.
3. **Are missing stateful env vars handled with a production-shaped sentinel default?** Fail loudly for credentials, URLs, bucket names.
4. **Does the client bundle resolve env vars at build time?** Verify the prod bundle was built with prod env vars.
5. **Is "environment" conflated with "tenant" or "feature flag"?** Those are different axes.
6. **Are `process.env` / `Deno.env.get(...)` reads scattered across many files?** Consolidate into `config.ts`.
7. **Are env vars read inside functions/handlers instead of at module scope?** Request-path reads hide missing-config failures from boot-time validation.
8. **Is there boot-time validation of required env vars?** Schema parsed on startup, not on first use.
9. **Do tests stub `process.env` directly instead of mocking the config module?** `vi.mock('./config', () => …)` is cleaner.
10. **Are env var reads flowing into callers untyped (`string | undefined`)?** Narrow via validation or assert non-null at boot.
