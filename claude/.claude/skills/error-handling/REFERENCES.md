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
