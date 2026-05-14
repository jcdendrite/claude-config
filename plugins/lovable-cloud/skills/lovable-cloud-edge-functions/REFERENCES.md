# lovable-cloud-edge-functions — References

## Why the tier table is shaped as it is

Supabase's hosted gateway `verify_jwt` setting can only verify **HS256**-signed
JWTs. Lovable Cloud projects use **ES256** signing keys for browser-issued user
JWTs, which the gateway cannot verify. This means browser-invoked functions must
disable gateway JWT verification (`verify_jwt = false`) and rely entirely on
in-code auth instead. Service-role keys use HS256, so gateway verification still
works for those callers.

This ES256/HS256 split is why Tier 1 (browser-invoked) and Tier 2 (service-role)
have opposite `verify_jwt` settings — not a design choice, but a platform
constraint.

Reference: https://supabase.com/docs/guides/functions/auth
