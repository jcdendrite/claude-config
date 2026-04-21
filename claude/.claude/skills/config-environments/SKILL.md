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

- **12-Factor §III Config** (<https://12factor.net/config>) explicitly rejects environment-named variables: *"env vars are granular controls, each fully orthogonal to other env vars. They are never grouped together as 'environments', but instead are independently managed for each deploy."*
- **Mainstream frameworks resolve per-env config by file/store selection, not renaming:**
  - Next.js — lookup order `process.env` → `.env.$(NODE_ENV).local` → `.env.local` → `.env.$(NODE_ENV)` → `.env` (`.env.local` is skipped when `NODE_ENV=test`). Platform env stores (Vercel, Railway, etc.) populate `process.env` and win over file-based config.
  - Rails — `config/credentials/#{Rails.env}.yml.enc` per-env encrypted file with fallback to `config/credentials.yml.enc`.
  - Kubernetes — ConfigMap / Secret with Kustomize overlays or Helm values per env.
  - Docker Compose — supports different `.env` files per env (passed with `--env-file`) rather than renaming variables.
  - Supabase Edge Functions — local `supabase/functions/.env` vs `supabase secrets set FOO=…` in prod, same key name.
- **Suffix patterns duplicate logic.** The "which environment am I?" decision is already encoded in which config source loaded. Adding `_DEV` / `_PROD` to variable names fans that decision out to runtime code that branches on `NODE_ENV`/`ENVIRONMENT`, which:
  - Requires both values to be present where only one is ever read, forcing every config source to populate every suffix.
  - Leaks the env selector into every consumer.
  - Leaves unused-in-this-env variables sitting as placeholders in each config source, where a stale or missing value silently becomes live when the branch flips.
- **Rotation is cheaper with canonical names.** Rotating `STRIPE_SECRET_KEY` means updating one name in the prod store; rotating `STRIPE_SECRET_KEY_PROD` requires remembering which suffix prod actually reads, and leaves a `_DEV` variant that's easy to update by mistake. Secrets hygiene practices (scheduled rotation, breach response) work best when the variable name is stable and only the value churns.

## Anti-patterns to flag

### 1. Environment-suffixed variable names

```ts
// Bad
const clientId = process.env.NODE_ENV === 'production'
  ? process.env.OAUTH_CLIENT_ID_PROD
  : process.env.OAUTH_CLIENT_ID_DEV;
```

```ts
// Good
const clientId = process.env.OAUTH_CLIENT_ID;
```

The "bad" version requires both `_PROD` and `_DEV` to exist in *every* environment's config source even though only one is ever read — the other sits as a placeholder. Deploy-time config gets duplicated, and the env-branching logic is dead weight.

### 2. Environment-branching for credential selection

```ts
// Bad
function getStripeKey() {
  return currentEnv() === 'production'
    ? Deno.env.get('STRIPE_SECRET_KEY_LIVE')
    : Deno.env.get('STRIPE_SECRET_KEY_TEST');
}
```

Same anti-pattern: pushes environment awareness into credential lookup. Instead, the prod deploy populates `STRIPE_SECRET_KEY` with the live key; the dev `.env` populates `STRIPE_SECRET_KEY` with the test key. No code branch.

Stripe's own convention (`sk_live_…` vs `sk_test_…` prefix inside the *value*) is the cleanest version of this: the code just reads `STRIPE_SECRET_KEY` and the value itself encodes which mode it's in.

### 3. Sentinel-value defaults that silently leak production-shaped state

```ts
// Bad — silently falls back to a prod-ish default
const bucket = process.env.STORAGE_BUCKET ?? 'prod-uploads';
```

Missing **stateful** config should **fail fast**, not substitute a default that might be production-shaped. A dev environment with missing `STORAGE_BUCKET` writing to `prod-uploads` is the classic data-leak path.

```ts
// Good — missing config fails boot, not first write
const bucket = process.env.STORAGE_BUCKET;
if (!bucket) throw new Error('STORAGE_BUCKET missing');
```

**Convention defaults are fine** for non-sensitive ergonomics: `PORT ?? 3000`, `LOG_LEVEL ?? 'info'`, `MAX_RETRIES ?? 3`, `CACHE_TTL_SECONDS ?? 60`. The distinction:

- **Can the default point at live production state if it's read in the wrong env?** → No default. Fail fast. (`STORAGE_BUCKET`, `DATABASE_URL`, `STRIPE_SECRET_KEY`, `OAUTH_CLIENT_ID`.)
- **Is the default a neutral convention that works anywhere?** → Default is fine. (`PORT`, log levels, timeouts, retry counts.)

### 4. Assuming client-side frameworks "know" the environment

Client bundles (Vite, Next.js, React Native) bake env vars in at build time — the bundle that ships is **frozen** with the values present when `next build` / `vite build` ran. A value set in the production deploy's runtime env does **not** reach the prod bundle retroactively. Only specifically-prefixed vars are inlined (`VITE_*`, `NEXT_PUBLIC_*`, similar), and they're inlined at build time. If the CI pipeline builds the prod bundle under staging's env (or a missing env var falls back to a dev default), the shipped prod bundle carries the wrong values and no runtime config change will fix it. This is the single most common cause of "prod points at staging" incidents.

## Environment-aware *behavior* vs environment-aware *credentials*

Not all env-branching is an anti-pattern. The distinction is what the branch is *selecting:*

- **Credential selection branching is the anti-pattern.** Reading `X_DEV` vs `X_PROD` based on `NODE_ENV` is duplicated logic (see above).
- **Behavior branching can be legitimate.** If the *same canonical variable* drives the decision (e.g. `ENVIRONMENT` controls which origin allowlist applies, not which secret is read), the env check lives in one place and only affects behavior. The credentials are still a single-name read.

Example of a legitimate behavior branch:

```ts
// The allowlist itself differs per env (localhost only in dev).
// But credentials are a single-name read — no _DEV / _PROD suffixes.
const origins = currentEnv() === 'production'
  ? PRODUCTION_ORIGINS
  : DEV_ORIGINS;
const clientId = process.env.OAUTH_CLIENT_ID; // same name in every env
```

## When suffix patterns ARE legitimate

Narrow cases where both values must exist in the same process at the same time:

- **Co-located simultaneous values.** A migration script reading `OLD_DATABASE_URL` and `NEW_DATABASE_URL` in one process.
- **Test harness stubs alongside real values.** `TEST_STRIPE_KEY` held next to `STRIPE_SECRET_KEY` so a test file can hit the test mode deliberately without swapping files.
- **Feature flags genuinely orthogonal to environment.** `ENABLE_NEW_BILLING` is flipped per-deploy but the variable isn't an environment — it's a rollout control.

If a single invocation of the code needs both suffixed values at the same time, the suffix is fine. If it's just "I'm in dev vs I'm in prod," it's the anti-pattern.

## Centralize config at startup (`config.ts` pattern)

Read every env var **once**, in a single module, validate at process boot, freeze, and export a typed object. Every consumer imports the config module — no `process.env` / `Deno.env.get` at call sites.

```ts
// config.ts — the one place env vars are read.
import { z } from 'zod';

const schema = z.object({
  DATABASE_URL: z.string().url(),
  STRIPE_SECRET_KEY: z.string().startsWith('sk_'),
  OAUTH_CLIENT_ID: z.string().min(1),
  OAUTH_CLIENT_SECRET: z.string().min(1),
  PORT: z.coerce.number().int().positive().default(3000),
  LOG_LEVEL: z.enum(['debug', 'info', 'warn', 'error']).default('info'),
});

// Throws on boot with a clear validation message if anything is missing
// or malformed. The process never starts with bad config.
export const config = Object.freeze(schema.parse(process.env));
export type Config = typeof config;
```

```ts
// elsewhere.ts
import { config } from './config';

const stripe = new Stripe(config.STRIPE_SECRET_KEY); // typed: string, validated: sk_ prefix
app.listen(config.PORT);                             // typed: number
```

**Why this matters:**

- **Fail-fast.** Missing / malformed config crashes at boot with a clear error, not at 3am on the first checkout attempt.
- **Type safety.** `process.env.X` is `string | undefined`; `config.X` is whatever the schema said (validated `string`, `number`, enum, URL, etc.).
- **Central audit surface.** One grep of `process.env.` in your repo should return exactly one file. New env var = one place to add it.
- **Testability.** Tests mock the `./config` module (`vi.mock('./config', () => ({ config: { ... } }))`) rather than stubbing `process.env` — cleaner, safer, and doesn't leak across test isolation.
- **ORM / client init plays along.** Instead of Prisma/Drizzle/Stripe reading `process.env` directly at import time, construct them from `config`:
  ```ts
  // db.ts
  import { config } from './config';
  export const db = drizzle({ connectionString: config.DATABASE_URL });
  ```

Zod is shown above; `valibot`, `envalid`, `@t3-oss/env-core`, or hand-rolled validation all work. The point is a single validated module, not the library.

## Isolation patterns that actually work

If the goal is **credential isolation across environments** (so a dev leak can't compromise prod), enforce it at **provisioning time**, not in code:

- Register separate upstream credentials per env (OAuth clients, API keys, service accounts).
- Each env's config source (Vercel env, Railway env, managed secret store, `.env.local`) holds only its own credential.
- Code reads a single canonical name (`OAUTH_CLIENT_ID`, `STRIPE_SECRET_KEY`) and trusts the source to have populated the right value.
- The two sources never share state — prod values live only in the prod secret store, never on developer machines — so a dev-machine compromise physically cannot mint prod tokens.

Auth.js explicitly recommends this: *"we recommend using a different OAuth app for development/production so that you don't mix your test and production user base"* ([Auth.js deployment](https://authjs.dev/getting-started/deployment)). Clerk bakes the pattern in with `pk_test_` / `pk_live_` instance prefixes. Managed secret stores (AWS Secrets Manager, Doppler, HashiCorp Vault) don't prescribe naming conventions directly — they provide per-environment namespacing so the consumer's code can keep reading a single canonical name.

## CI/CD and build-time config

A subtlety that surfaces often in 2026 stacks:

- **GitHub Actions `secrets.*` vs `vars.*`.** `secrets.*` is masked in logs and appropriate for credentials; `vars.*` is plaintext-logged and appropriate only for non-sensitive config. Never put a secret in `vars.*` just to "see the value" for debugging — log-scraped `vars.*` values are the same exposure class as committed `.env` files.
- **Build-time vs runtime env.** A Docker image built with `ENV FOO=bar` bakes `FOO` into every running container. Runtime env (`docker run -e FOO=...`, Kubernetes `envFrom`) overrides per-container. For bundled frontend code (Next.js / Vite / etc.), build-time is the only injection point for `NEXT_PUBLIC_*` / `VITE_*` — see anti-pattern #4 above. Mixing the two silently (e.g. building the image with dev env, injecting prod env at runtime, but having `NEXT_PUBLIC_*` baked from the dev build) produces wrong-env client bundles.
- **Fail-fast at process startup.** Validate required env vars on boot, not at first use. A late failure (missing `STRIPE_SECRET_KEY` on first checkout attempt at 3am) is much harder to diagnose than a refused startup.

## Review checklist

When reviewing code or plans that touch multi-env config:

1. **Are there variables whose names contain `_DEV`, `_PROD`, `_STAGING`, `_TEST`?** Flag unless the "simultaneous values in one process" criterion applies.
2. **Does the code branch on `NODE_ENV` / `ENVIRONMENT` / equivalent to pick a credential?** Collapse to a single env var read. (Behavior branching on the same env check is fine — see the behavior/credentials section.)
3. **Are missing stateful env vars handled with a production-shaped sentinel default rather than fail-fast?** Fail loudly for credentials, URLs, bucket names. Convention defaults (`PORT`, `LOG_LEVEL`, retry counts) are fine.
4. **Does the client bundle resolve env vars at build time?** Verify the prod bundle was built with prod env vars, not dev defaults.
5. **Is "environment" conflated with "tenant" or "feature flag"?** Those are different axes; don't encode them in the same variable.
6. **Are `process.env` / `Deno.env.get(...)` reads scattered across many files?** Consolidate into one `config.ts` module that reads + validates + freezes + exports once at boot.
7. **Are env vars read inside functions/handlers instead of at module scope?** Request-path env reads re-parse strings on every call and hide missing-config failures from boot-time validation.
8. **Is there boot-time validation of required env vars?** Zod / valibot / envalid schema parsed on startup — not "check on first use."
9. **Do tests stub `process.env` directly instead of mocking the config module?** `vi.mock('./config', () => …)` keeps test isolation clean and gives typed overrides.
10. **Are env var reads flowing into callers untyped (`string | undefined`)?** Either narrow via validation (preferred) or assert non-null at boot — don't let the union leak.

## Sources

- [12-Factor App §III Config](https://12factor.net/config)
- [Next.js Environment Variables](https://nextjs.org/docs/app/guides/environment-variables)
- [Rails Configuration](https://guides.rubyonrails.org/configuring.html)
- [Kubernetes ConfigMap](https://kubernetes.io/docs/concepts/configuration/configmap/)
- [Docker Compose env-var best practices](https://docs.docker.com/compose/how-tos/environment-variables/best-practices/)
- [Supabase Functions Secrets](https://supabase.com/docs/guides/functions/secrets)
