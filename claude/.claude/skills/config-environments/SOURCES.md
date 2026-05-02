# Sources — config-environments

Reference material that informed this skill. Not loaded during skill execution — consult when editing the skill to verify a rule still holds or to add new guidance.

## Canonical references

- **12-Factor App §III Config** — https://12factor.net/config  
  Defines the canonical argument against environment-named variables. Key quote: *"env vars are granular controls, each fully orthogonal to other env vars. They are never grouped together as 'environments'."*

- **Auth.js deployment guide** — https://authjs.dev/getting-started/deployment  
  Explicitly recommends separate OAuth apps per environment: *"we recommend using a different OAuth app for development/production so that you don't mix your test and production user base."* Supports the credential-isolation-at-provisioning-time rule.

- **Clerk instance prefixes** (`pk_test_` / `pk_live_`)  
  Clerk bakes the pattern into their SDK: instance type is determined by the key prefix, not by a runtime `NODE_ENV` branch. Same principle as Stripe's `sk_live_`/`sk_test_` value encoding.

## Framework resolution strategies (same variable name, different source)

These show that mainstream frameworks all resolve per-env config by **source selection**, not **variable renaming**:

- **Next.js** — lookup order: `process.env` → `.env.$(NODE_ENV).local` → `.env.local` → `.env.$(NODE_ENV)` → `.env`. Platform env stores (Vercel, Railway) populate `process.env` and win over file-based config.
- **Rails** — `config/credentials/#{Rails.env}.yml.enc` per-env encrypted file, fallback to `config/credentials.yml.enc`.
- **Kubernetes** — ConfigMap / Secret with Kustomize overlays or Helm values per env.
- **Docker Compose** — `--env-file` flag selects a different source per invocation, not per-variable suffixes.
- **Supabase Edge Functions** — local `supabase/functions/.env` vs `supabase secrets set FOO=…` in prod; same key name in both.

## Managed secret stores

AWS Secrets Manager, Doppler, HashiCorp Vault don't prescribe variable naming — they provide per-environment namespacing so the consumer's code keeps reading a single canonical name. The per-env isolation happens at the store level, not in code.

## Language config libraries (extended notes)

- **Go:** `kelseyhightower/envconfig` is widely used but largely unmaintained as of 2024; prefer `caarlos0/env` or `knadh/koanf`. In Go and Rust, parsing and validation are separate concerns (unlike Zod/pydantic which bundle them).
- **Ruby (non-Rails):** Sinatra / scripts typically use `ENV.fetch` with manual validation in a `config.rb`.
- **Rust:** `dotenvy` for `.env` loading; `config`/`figment` for parsing; `validator` or `garde` for field validation.
