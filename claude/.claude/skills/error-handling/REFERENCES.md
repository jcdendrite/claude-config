# Error Handling — References

Edit-time reference for `SKILL.md`. Not loaded at runtime. Read this file manually when updating skill rules to verify citations still hold or to add new guidance.

## RFC 9457 — Problem Details for HTTP APIs

**URL:** https://www.rfc-editor.org/rfc/rfc9457
**Status:** VERIFIED

Section 3.1 — Standard members: `type` (URI reference identifying problem type), `title` (human-readable summary), `status` (HTTP status code), `detail` (human-readable explanation), `instance` (URI identifying the specific occurrence).

**Cited for Rule 2.** The skill adopts a **simplified subset** — drops `type` URI, `status`, and `instance`; retains `detail` concept as `message`; adds `code` for programmatic discrimination. Rationale: `type` URIs are rarely dereferenceable in practice, `status` is redundant with the HTTP line, `instance` adds complexity without value for most applications. The simplified shape preserves machine-readability (`code`) and human-readability (`message`) while cutting the ceremony.

---

## RFC 9110 — HTTP Semantics

**URL:** https://www.rfc-editor.org/rfc/rfc9110 (§15.5.6)
**Status:** NOT FETCHABLE via automated tools (document too large); requirement is well-established.

Section 15.5.6 — 405 Method Not Allowed: "A server generating a 405 response MUST generate an Allow header field containing the list of methods presently supported by the target resource." (Verbatim quote from RFC 9110 §15.5.6 — not obtained via automated fetch; text is canonical.)

**Cited for Rule 8 and Anti-pattern C.** Minting a code that maps to 405 requires shipping the `Allow` header — status-code semantics carry protocol obligations. Override to 405 without minting the correct code omits this header.

---

## Google Cloud AIP-193 — Errors

**URL:** https://google.aip.dev/193
**Status:** VERIFIED

Section "Error messages": "Error messages should help a reasonably technical user understand and resolve the issue, and should not assume that the user is an expert in your particular API. Additionally, error messages must not assume that the user will know anything about its underlying implementation."

Section "ErrorInfo" — domain field: "The domain field is the logical grouping to which the reason belongs. The domain must be a globally unique value, and is typically the name of the service that generated the error."

**Cited for Rules 1, 3, 7.** Single error model scoped to a named service domain; implementation detail kept out of user-facing text.

---

## Stripe API Error Reference

**URL:** https://docs.stripe.com/api/errors
**Status:** VERIFIED

Error object fields: `type` — "The type of error returned. One of api_error, card_error, idempotency_error, or invalid_request_error"; `code` — "For some errors that could be handled programmatically, a short string indicating the error code reported"; `message` — "A human-readable message providing more details about the error"; `param` — "If the error is parameter-specific, the parameter related to the error."

**Cited for Rules 1, 6, 7.** A published flat code taxonomy with a per-code shape (type + code + message + optional param). Codes are short, programmatic strings — not infrastructure codes.

---

## Twilio Error/Warning Dictionary

**URL:** https://www.twilio.com/docs/api/errors
**Status:** PARTIALLY VERIFIED — the page organizes errors as a numeric list with product tags per entry. No explicit language was found on the page about the organizational principle (product/domain over layer of origin). The observable structure (numeric ranges + product tags per entry, not per-layer groupings) illustrates the single-namespace + product-tag approach, but this principle is the author's inference from structure, not a stated doctrine.

**Cited for Rule 1 (illustrative, not a direct quote).** Numeric code range + product/domain tag per entry, rather than per-infrastructure-layer codes.

---

## Nielsen Norman Group — Error-Message Guidelines

**URL:** https://www.nngroup.com/articles/error-message-guidelines/
**Status:** VERIFIED

Section "Use human-readable language": "Hide or minimize the use of obscure error codes or abbreviations; show them for technical diagnostic purposes only."

**Cited for Rules 3 and 4.** Codes are for technical diagnostics, not user-visible copy. User-facing messages must be human-readable.

---

## Apple Human Interface Guidelines

**URL:** https://developer.apple.com/design/human-interface-guidelines/ (search: managing errors / error alerts)
**Status:** URL not verified via automated fetch.

Supporting reference for the same consumer-UX principle as NN/g: no raw codes in user-facing copy; plain-language messages.

---

## Google Material Design — Error States

**URL:** https://m3.material.io/foundations/design-tokens/overview (search: error states / messaging)
**Status:** URL not verified via automated fetch.

Supporting reference for the same consumer-UX principle as NN/g: no raw codes in user-facing copy; plain-language messages.

---

## Microsoft .NET — Best Practices for Exceptions

**URL:** https://learn.microsoft.com/en-us/dotnet/standard/exceptions/best-practices-for-exceptions
**Status:** VERIFIED

"Use exception handling if the event doesn't occur often, that is, if the event is truly exceptional and indicates an error, such as an unexpected end-of-file." / "A common error case can be considered a normal flow of control."

Also: custom exceptions should include "a constructor that takes a string message and an inner exception"; "An alternative is to throw a new exception and include the original exception as the inner exception."

**Cited for P1** (throw the unexpected, return the expected) **and P3** (carry the underlying cause as inner exception).

---

## Microsoft .NET — Exceptions and Performance

**URL:** https://learn.microsoft.com/en-us/dotnet/standard/design-guidelines/exceptions-and-performance
**Status:** VERIFIED

Try-Parse pattern: "If the member fails for any reason other than the well-defined try, the member must still throw a corresponding exception."

**Cited for P1.** Expected failure modes have a Try-Parse return; all other failures throw.

---

## Oracle — Java Tutorials (Unchecked Exceptions — The Controversy)

**URL:** https://docs.oracle.com/javase/tutorial/essential/exceptions/runtime.html
**Status:** VERIFIED

"If a client can reasonably be expected to recover from an exception, make it a checked exception. If a client cannot do anything to recover from the exception, make it an unchecked exception." / "Runtime exceptions represent problems that are the result of a programming problem."

**Cited for P1.** Recoverable conditions → return (checked equivalent); programmer errors → throw unchecked.

---

## Python — Glossary (EAFP)

**URL:** https://docs.python.org/3/glossary.html
**Status:** VERIFIED

"Easier to ask for forgiveness than permission. This common Python coding style assumes the existence of valid keys or attributes and catches exceptions if the assumption proves false."

**Cited for P1.** Expected outcomes use exception handling; unexpected failures propagate up.

---

## Joyent / Node.js — Error Handling in Node.js (PARTIAL)

**URL:** https://www.tritondatacenter.com/node-js/production/design/errors (mirror: https://www.davepacheco.net/blog/2014/error-handling-nodejs/)
**Status:** PARTIAL — canonical host is JavaScript-gated; verbatim quotes confirmed via author Dave Pacheco's blog mirror and multiple corroborating sources.

"Operational errors represent run-time problems experienced by correctly-written programs. These are not bugs in the program." / "Programmer errors are bugs in the program. These are things that can always be avoided by changing the code."

**Cited for P2** (triage at the boundary — operational failure vs. programmer error). This is the primary source for the triage vocabulary used in P2.

---

## Microsoft — ASP.NET Core, Handle Errors

**URL:** https://learn.microsoft.com/en-us/aspnet/core/fundamentals/error-handling
**Status:** VERIFIED

UseExceptionHandler middleware "Catches and logs unhandled exceptions"; IExceptionHandler "gives the developer a callback for handling known exceptions in a central location."

**Cited for P2.** Centralized handler boundary is the framework-idiomatic capture point; scatter across catch blocks is the anti-pattern the middleware is designed to replace.

---

## Express — Error Handling

**URL:** https://expressjs.com/en/guide/error-handling.html
**Status:** VERIFIED

"You define error-handling middleware last, after other app.use() and routes calls"; signature `(err, req, res, next)`; "If synchronous code throws an error, then Express will catch and process it."

**Cited for P2.** Error-handling middleware is the canonical single boundary in Express; per-route catch blocks are the anti-pattern.

---

## Sentry — Handler Wrappers (Lambda / Cloudflare)

**URL:** https://docs.sentry.io/platforms/javascript/guides/aws-lambda/ (Sentry.wrapHandler) and https://docs.sentry.io/platforms/javascript/guides/cloudflare/ (Sentry.withSentry)
**Status:** VERIFIED

Wrapper functions are the SDK's recommended integration point for serverless handler boundaries.

**Cited for P2.** SDK-provided handler wrappers are the preferred capture boundary in serverless runtimes.

---

## Go (Google) — Working with Errors in Go 1.13

**URL:** https://go.dev/blog/go1.13-errors
**Status:** VERIFIED

`%w` wrapping — the wrapped error "will have an Unwrap method returning the argument"; "Wrap an error to expose it to callers. Do not wrap an error when doing so would expose implementation details."

**Cited for P3.** Wrapping preserves the original error and its stack through error chains; `%w` is Go's typed-cause idiom.

---

## Google Cloud AIP-193 — ErrorInfo

**URL:** https://google.aip.dev/193
**Status:** VERIFIED

Structured `ErrorInfo` (reason + domain + metadata) as machine-readable error carrier.

**Cited for P3.** Typed, structured error payload carries the registry code and cause as machine-readable fields — not stringified into a message.

---

## MDN — Error.cause / Custom Error Types

**URL:** https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Error/cause
**Status:** VERIFIED

"define your own error types deriving from Error … throw new MyError() … cleaner and more consistent error handling"; `cause` gives "access to the original error."

**Cited for P3.** JavaScript's `Error.cause` is the standard mechanism for preserving the original exception when re-throwing a typed subclass.

---

## AWS Lambda — Execution Environment Lifecycle

**URL:** https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtime-environment.html
**Status:** VERIFIED

"Lambda freezes the execution environment when the runtime and each extension have completed and there are no pending events." / "Make sure that any background processes or callbacks in your code are complete before the code exits."

**Cited for P4.** The execution environment is frozen on response return; async telemetry not awaited before that point is lost.

---

## Cloudflare Workers — context.waitUntil

**URL:** https://developers.cloudflare.com/workers/runtime-apis/context/
**Status:** VERIFIED

`waitUntil` "extends the lifetime of your Worker … may continue after a response is returned … for work that can run after the response is sent, such as logging, analytics."

**Cited for P4.** `waitUntil` is appropriate for fire-and-forget analytics; error capture that requires delivery confirmation must be awaited before the response is returned, not deferred into `waitUntil`.

---

## Google Cloud Run — CPU Allocation

**URL:** https://cloud.google.com/run/docs/configuring/cpu-allocation
**Status:** VERIFIED

"With request-based billing, CPU is only allocated during request processing."

**Cited for P4.** CPU (and therefore async work) is suspended when the response is returned; telemetry must flush within the request window.

---

## Sentry — Lambda Wrapper flushTimeout

**URL:** https://docs.sentry.io/platforms/javascript/guides/aws-lambda/configuration/lambda-wrapper/
**Status:** VERIFIED

"Sentry keeps the lambda function thread alive for up to 2 seconds to ensure reported errors are sent." (configurable `flushTimeout`)

**Cited for P4.** The ~2000 ms vendor default for telemetry flush is the grounding citation for the numeric value mentioned in P4.

---

## Sentry — flush() API

**URL:** https://docs.sentry.io/platforms/javascript/configuration/apis/
**Status:** VERIFIED

"Flushes all pending events." / timeout: "the client should wait to flush its event queue … wait until all events are sent before resolving the promise."

**Cited for P4.** `flush()` is the explicit API for awaiting telemetry delivery; the `timeout` parameter maps to the vendor-default value cited in the Lambda wrapper doc above.
