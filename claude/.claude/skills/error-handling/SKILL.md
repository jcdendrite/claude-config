---
name: error-handling
description: >
  Standard error-handling: single code namespace, RFC 9457–derived envelope,
  developer-only message fields, and anti-patterns for call-site drift.
---

# Error Handling Standard

## Core principle

One application error-code namespace. One response envelope. One mapper at the consumer. Infrastructure codes are logged metadata — never user-visible identifiers.

## The eight rules

### Rule 1 — Single application code namespace

One code identifies an error regardless of origin layer (database, server, client). Infrastructure codes (DB SQLSTATE, vendor error codes) are logged as metadata, never propagated as user-visible identifiers. No per-layer namespaces; no translation maps between layers.

```
// Bad — propagating a DB error code to the client
return { error: { code: 'DB_ERROR_23505', message: err.message } }

// Good — application code only, infra detail logged
logger.error({ infraCode: err.code, detail: err.detail }, 'unique constraint violated')
return { error: { code: 'USER_ALREADY_EXISTS', message: 'A user with that email already exists.' } }
```

### Rule 2 — Standard response envelope

`{ error: { code, message, details? } }` body + a `Request-Id` response header for traceability. This is a simplified RFC 9457 shape — drops `type` URI, `status`, and `instance`; the header carries traceability. `Request-Id` must be a cryptographically random value (UUID v4 or equivalent) — never sequential, never encoding session or tenant information.

```
// Response body
{ "error": { "code": "USER_ALREADY_EXISTS", "message": "A user with that email already exists." } }

// Response header
Request-Id: <uuid>
```

### Rule 3 — Codes are never user-visible

The client maps `code` → plain-language copy via a single central mapper. Support correlates via `Request-Id` + account + timestamp. Raw codes never appear in toasts, modals, or error messages shown to users.

### Rule 4 — `message` is developer-facing only

Developer-controlled text. Never derived from an exception or user input. Never pass `err.message`, `String(err)`, or raw exception text to a user-facing UI element.

```
// Bad
return { error: { code: 'UNKNOWN', message: err.message } }

// Good
logger.error({ err }, 'unexpected failure in createUser')
return { error: { code: 'INTERNAL_ERROR', message: 'An internal error occurred.' } }
```

### Rule 5 — Log before suppressing

Every catch block logs the real error before returning the generic envelope. Observability is lost when raw errors stop being propagated but the catch block doesn't log them first.

### Rule 6 — Single source of truth for the registry

One module, re-exported across layers. No codegen, no parallel registries, no translation maps, no database table of codes. (Codegen earns its keep only across format boundaries — e.g., OpenAPI→TypeScript — not for same-language sharing.)

### Rule 7 — Granularity: one code per distinct actionable failure mode

Decision test: would the client or support team act differently on this code than on another? If yes, separate codes. Don't collapse everything into one generic code; don't mint hyper-specific codes nobody acts on.

### Rule 8 — HTTP status flows from the registered code

Never override the response status so it diverges from what the code's registry entry maps to. If a path needs a new status, mint the code that owns that status (including any protocol-required headers, e.g. `Allow` for 405 per RFC 9110 §15.5.6).

## Anti-patterns

### A — Hardcoded call-site copy

Codeless/unknown errors render the generic mapped message (`mapper(code ?? FALLBACK_CODE)`), never a hardcoded call-site literal. Resisting "preserve specificity" by hardcoding toast strings at the call site inverts the architecture: the mapper is the single UI surface for codes; the call site should not duplicate it.

```
# Bad
on error err:
    show_toast("Failed to save — please try again")

# Good
on error err:
    code = parse_error_envelope(err).code
    show_toast(message_mapper(code or FALLBACK_CODE))
```

### B — Phantom codes (codes with no producer)

A code is a contract between a producer that emits it and a consumer that maps it. Specific user copy is earned by a producer emitting a real code the registry defines — never by inventing a code for the *absence* of a code. A per-operation `<OP>_FAILED` code that no server path ever emits inverts the contract.

### C — HTTP status override

Never override the HTTP status to diverge from what the code's registered status maps to (e.g., emitting a 400-class code but forcing the response to `405`). Mint the correct code that owns the intended status instead.

## Review checklist

1. **Single namespace** — Are infrastructure codes (DB SQLSTATE, vendor codes) logged only, never propagated as user-visible identifiers?
2. **Envelope shape** — Does every error response follow `{ error: { code, message, details? } }` + `Request-Id` header?
3. **Code visibility** — Does any user-facing UI (toast, modal, error message) display a raw code string? It should map via a central mapper only.
4. **Message source** — Does any `message` field contain `err.message`, `String(err)`, or user input? It must be developer-controlled text only.
5. **Log before suppress** — Does every catch block log the real error before returning the envelope?
6. **Single registry** — Is there more than one module defining error codes, or any translation map between layers?
7. **Code granularity** — Does each code correspond to a distinct actionable failure mode? Are there `<OP>_FAILED` codes with no producer, or collapsed codes that different callers need to distinguish?
8. **Status fidelity** — Does the HTTP status match what the code's registry entry specifies? Are 405 responses sending an `Allow` header?
9. **Anti-pattern A** — Are there hardcoded toast/modal strings in catch blocks instead of `mapper(code ?? FALLBACK)`?
10. **Anti-pattern B** — Are there codes in the registry that no server path ever emits?
11. **Anti-pattern C** — Are there HTTP status overrides that diverge from the code's registered status?
12. **Auth-conditional verbosity** — Does the error response vary in detail level based on caller authentication status? The same envelope shape and information content must be returned to all callers — richer detail belongs in server-side logs only, not in any authenticated response tier.

See `~/.claude/skills/error-handling/REFERENCES.md` for the research and primary-source citations behind these rules.
