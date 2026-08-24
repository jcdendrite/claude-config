# lovable-cloud-edge-functions — References

## Why the tier table is shaped as it is

Gateway `verify_jwt` only verifies HS256; Lovable Cloud browser JWTs are ES256
(unverifiable by the gateway) while service-role keys are HS256 — so Tier 1 sets
`verify_jwt = false` and Tier 2 keeps it `true`.

Reference: https://supabase.com/docs/guides/functions/auth
