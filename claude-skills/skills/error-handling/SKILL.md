---
name: error-handling
description: >
  Error-handling: envelope, propagation, and capture rules for server-side APIs.
  TRIGGER when: reviewing server-side catch blocks, response formatters, telemetry
  capture, or serverless error boundaries; defining or auditing application error
  codes or the code registry; reviewing API error response shapes.
  DO NOT TRIGGER when: reviewing client-side error display only with no server
  path in diff; reviewing test assertions on error shapes without touching
  production code.
---

# Error Handling Standard

## Core principle

One application error-code namespace. One response envelope. One mapper at the consumer. Infrastructure codes are logged metadata — never user-visible identifiers.

## The rules

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

### Rule 9 — Envelope is caller-invariant

The error response envelope's shape and information content do not vary by caller authentication status. Richer detail belongs in server-side logs only, never in any authenticated response tier.

## Error propagation & capture (server-side)

The rules above govern the response *envelope* — what crosses the wire to the consumer. These four govern the orthogonal axis: how an error travels from its origin to the response, and where it is reported to telemetry.

### P1 — Throw the unexpected; return the expected
Expected, recoverable conditions (invalid input, unauthorized, not found, conflict) are normal control flow — return the structured envelope. Genuinely unexpected failures (a query that should have succeeded, a broken dependency, an unreachable branch) are exceptions — throw them and let the boundary (P2) report and format. Reserve exceptions for the exceptional; do not use them for routine control flow, and do not swallow-and-return what the caller cannot act on.

### P2 — Capture at one centralized boundary
Telemetry capture (error tracker / APM) belongs in a single handler-boundary wrapper that every entry point routes through — not scattered across catch blocks, and not inside the response formatter (which stays pure: code → envelope). The boundary is where each error is triaged — an operational failure (a runtime problem in correct code, e.g. a dependency 500 or socket hang-up) versus a programmer error (a bug) — and where the capture-log-respond decision is made once.

### P3 — Throw a typed error carrying the registry code + cause
When you throw across a boundary, throw a typed error subclass carrying (a) the application error code from the single registry (Rule 1) so the boundary formats the correct envelope, and (b) the underlying cause so the original exception and its stack are preserved for capture. Do not discard the cause; do not stringify it into the user-facing message (Rule 4).

### P4 — Flush telemetry before a serverless/edge runtime terminates
In serverless and edge runtimes the execution environment is frozen or reclaimed once the response is returned, and telemetry events are sent asynchronously. The boundary must await the SDK's flush (vendor default ~2000 ms) before returning, or pending events are dropped. Background-task deferral APIs are for fire-and-forget work where delivery confirmation is not needed; error capture wants the flush completed before the response returns.

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

A code needs a real producer that emits it (Rule 1's registry) — never mint a code (e.g. a per-operation `<OP>_FAILED`) for the absence of one.

### C — HTTP status override

Never override the HTTP status to diverge from what the code's registered status maps to (e.g., emitting a 400-class code but forcing the response to `405`). Mint the correct code that owns the intended status instead.

### D — Capture inside the response formatter

Couples telemetry to HTTP-body formatting; forces sync/async contortions, a status gate, and still misses any hand-rolled response that bypasses the formatter. Capture belongs at the boundary (P2).

### E — Decide capture by inspecting the returned response status

By the time a 5xx Response object exists, the original exception has been discarded — you capture a stack-less event. Throw (P1) so the boundary holds the real error.

### F — Mask a captured failure as success via status override

Forcing a registry-500 code to a 200 response to suppress a retry hides the failure from status-based observers. Mint the code that owns the intended status (cross-ref Rule 8 / Anti-pattern C).

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
12. **Auth-conditional verbosity** — Does the error response vary in detail level based on caller authentication status? (Rule 9)
13. **Throw vs. return** — Are unexpected failures thrown (not returned), and expected recoverable conditions returned as structured envelopes? (P1)
14. **Capture boundary** — Is telemetry capture wired into a single handler-boundary wrapper rather than scattered across catch blocks or inside the response formatter? (P2)
15. **Typed throw** — When throwing across a boundary, does the error carry (a) the application error code from the single registry and (b) the underlying cause? Is the cause preserved rather than stringified into the user-facing message? (P3)
16. **Serverless flush** — In serverless/edge runtimes, does the boundary await the telemetry SDK's flush before returning? Are background-task deferral APIs reserved for fire-and-forget work rather than error capture? (P4)
17. **Anti-pattern D** — Is telemetry capture co-located with or inside the response formatter? It must live at the handler boundary. (D)
18. **Anti-pattern E** — Is there any code that decides whether to capture telemetry by inspecting the HTTP status of a returned Response object? That pattern discards the original exception; throw instead so the boundary holds the real error. (E)
19. **Anti-pattern F** — Is any code forcing a registry-mapped-5xx error to a 200 response to suppress retries? Mint the code that owns the intended status. (Rule 8 / C / F)

See `${CLAUDE_SKILL_DIR}/REFERENCES.md` for the research and primary-source citations behind these rules.
