---
name: test-conventions
description: >
  Testing conventions and principles for any codebase: test pyramid, test isolation,
  mock design, coverage judgment, and common anti-patterns.
  TRIGGER when: discussing test strategy, planning tests for new code, reviewing
  test coverage, when someone proposes a testing approach that seems wrong-layered,
  or when writing test infrastructure (mocks, helpers, fixtures).
  DO NOT TRIGGER when: a project-level test skill is already loaded, or the task
  is purely mechanical (running tests, fixing a single assertion, updating a snapshot).
user-invocable: false
---

# Testing Conventions

## 1. Test pyramid

| Layer | Speed | External deps | What it covers | Volume |
|-------|-------|---------------|----------------|--------|
| **Unit** (stubbed deps) | Milliseconds | None | Branch logic, error paths, edge cases, data transformations | Many — every code path |
| **Integration** (real local services) | Hundreds of ms | Local DB, local services | Auth boundaries, response contracts, wiring between layers | Few — happy path + key error paths per endpoint or boundary |
| **Contract** (schema verification) | Milliseconds–seconds | None (consumer) or local (provider) | API schemas between services match consumer expectations | One per service boundary |
| **Smoke / E2E** (real external APIs) | Seconds | Third-party APIs, real infra | Full flow works end-to-end | 1-2 per feature, run on deploy not on every commit |

**Contract tests** verify that service-to-service API schemas stay compatible without requiring a running instance of the other service. Use them at any service boundary where teams deploy independently.

### Signs of an inverted pyramid
- Most tests call real services (DB, APIs) to test internal branching logic
- Tests are slow (>1s each) because of network round-trips
- Individual unit tests taking >50-100ms, indicating a hidden real dependency or overly complex setup
- External API rate limits constrain how often you can run the test suite
- Tests are flaky because of network timeouts or service availability
- You can't run the test suite offline or in CI without credentials
- Adding a new code branch requires setting up complex test state in a real database

### When to move a test down the pyramid

**Integration → unit when:**
- The test sets up complex DB state to exercise one `if` branch
- The test hits an external API just to test error handling
- The test is slow (>500ms) and tests pure logic, not wiring
- You're hitting rate limits on external APIs

**Smoke → integration when:**
- The test calls a real third-party API to verify your code handles errors
- The test is flaky because of network conditions
- The test creates real resources (emails, contacts) as a side effect

## 2. Design for testability

### Dependency injection over internal construction
Functions should accept their dependencies as parameters, not create them internally. A function that constructs its own client internally cannot be tested without hitting the real service. Accepting the client as a parameter lets callers pass a test double.

### Make expected failure paths easy to assert on
In languages with explicit error types (Go, Rust, functional patterns), returning errors as values simplifies test assertions. In exception-oriented languages (Python, Java, C#, Ruby), use the framework's exception-assertion utilities (e.g., `pytest.raises`, JUnit's `assertThrows`). The key principle: expected failure paths should be as easy to test as success paths, using whatever the language's idiomatic mechanism is.

### Choose the right test double

| Double | Behavior | Use when |
|--------|----------|----------|
| **Stub** | Returns canned data, no call tracking | Testing your code's behavior given specific inputs |
| **Mock** | Records calls for assertion | Verifying your code called a dependency with the right arguments |
| **Fake** | Lightweight real implementation (in-memory DB, local HTTP server) | Testing without real infrastructure; also useful in unit tests when stubs are too complex (e.g., an in-memory repository that maintains state across calls) |
| **Spy** | Wraps the real implementation, records calls | You need real behavior but want to verify interaction |

Use the narrowest double that covers the test's intent. Prefer stubs for unit tests of pure logic; use mocks only when verifying interaction is the point of the test.

### Test double seams by dependency type
- **Database calls:** Stub/fake the client object, or use transaction rollback isolation (see section 3)
- **External HTTP APIs:** Intercept by URL pattern or use a fake HTTP server
- **Env vars / config:** Set and restore in setup/teardown blocks
- **Time:** Inject timestamps as parameters, or set up data relative to "now"

## 3. Test isolation

### Tests must be independent
- Never rely on test execution order
- Each test creates its own data and cleans up in teardown
- Use unique identifiers (timestamps, counters) to prevent cross-test collision

### Global state must be saved and restored
When tests modify global state (env vars, global functions, singletons), always save the original and restore in a guaranteed cleanup block (teardown, `finally`, `defer`, etc.). **Never** unconditionally delete or overwrite global state — a test that removes a value without saving it first will break every subsequent test that needs it.

### Test double cleanup must be guaranteed
When replacing globals (HTTP client, fetch function, clock), always restore the original in a guaranteed cleanup block, even if the test fails or throws.

### Database test isolation
- **Transaction rollback pattern:** Wrap each test in a transaction and roll back at the end. Standard in Django, Rails, Spring, and most ORMs — more efficient than truncation.
- **Test database lifecycle:** Use a dedicated test database, run migrations before the suite, reset state between tests.
- **In-memory vs. real engine:** In-memory databases (e.g., SQLite in-memory mode) are fast but have dialect differences (JSON columns, CTEs, locking behavior). When dialect fidelity matters, use the same engine as production.

### Parallel-safe tests
When tests run in parallel (pytest-xdist, Jest workers, Go's `t.Parallel()`, JUnit parallel mode):
- Never bind to hardcoded ports; use port 0 or dynamic allocation
- Use unique temp directories per test (e.g., `mkdtemp`, test-scoped `tmp_path`)
- When sharing a test database, use per-test schemas or transaction rollback isolation
- If a test mutates module-level state, it cannot safely run in parallel — document this constraint explicitly

## 4. Test naming and structure

Test names should describe the **scenario** and **expected outcome**, not just the function name:
- Good: `rejects_expired_token_with_401`, `returns_empty_list_when_no_matches`
- Bad: `test_refreshToken`, `test_search`, `it_works` (language-required prefixes like `test_` or `Test` are fine — the problem is having no scenario or expected outcome after the prefix)

A reader should understand what the test verifies without reading its body.

### Common naming structures
- `action_condition_expectedResult` — e.g., `search_withEmptyQuery_returnsEmptyList`
- `given_when_then` — e.g., `givenExpiredToken_whenRefreshCalled_thenReturns401`

Pick one convention per project and apply it consistently.

### Test body structure
Each test should follow the **Arrange / Act / Assert** (or Given / When / Then) pattern with clear visual separation between setup, action, and verification. Mixing these phases makes tests harder to diagnose when they fail.

### Regression test intent
For tests that guard against a specific past bug, include a comment or docstring referencing the issue. This prevents future developers from deleting a test that looks redundant but guards against a known failure.

## 5. Test data

- Use **factory/builder helpers** that supply sensible defaults; tests override only the fields relevant to the scenario
- **Avoid magic values** — if a test uses `status: 3`, name the constant or comment why 3 matters
- **Prefer inline construction** over shared fixtures when the data is central to the test's assertion
- **Shared fixtures** are appropriate for expensive setup (DB schemas, server instances), not for simple data objects
- For tests against shared databases, use unique prefixes/suffixes (run ID, timestamp) so parallel runs and stale data don't collide

## 6. Coverage judgment

### What each test layer should verify

**Unit tests:**
- Every `if/else` branch in the function
- Error return paths (missing input, API failure, invalid state)
- Edge cases (zero values, null, empty strings, boundary conditions)
- Side effects via mocks (did it call `.update()` with the right payload?)

**Integration tests:**
- **HTTP-level:** Auth rejection (401/403), authorized success (2xx), response shape (expected fields and types)
- **Service/module-level:** Wiring between layers maps results correctly, shared modules are called with expected arguments
- Not every integration test needs to go through HTTP — service-level integration tests are cheaper when you're verifying wiring, not auth or response shape
- Don't re-test every branch — that's the unit tests' job

**Contract tests:**
- Consumer expectations match the provider's actual schema
- Run when either side changes; no need for a live instance of the other service

**Smoke tests:**
- One happy-path test per external API integration
- Run on deploy or post-merge to main, not on feature branches or pre-merge CI
- Use test/sandbox accounts
- Never use smoke tests to verify branching logic

### Security controls require both allow and deny paths
For any access control, auth check, or privilege boundary:
- **Deny test:** unauthorized caller is rejected (403/401)
- **Allow test:** authorized caller succeeds (200 + correct response)
- Untested security controls are indistinguishable from absent ones

### Concurrency coverage
For endpoints or functions that handle concurrent writes:
- Test with parallel requests to verify idempotency and conflict resolution
- Test for expected behavior under contention (optimistic locking failures, retry semantics, deadlock avoidance)

### Happy path alone is insufficient when:
- The function has validation logic (test invalid inputs)
- The function has auth/authorization (test unauthorized callers)
- The function handles partial failure (test one item failing in a batch)
- The function has opt-out/preference logic (test opted-out path)

## 7. Mock design principles

### Stub/mock fidelity
- Test doubles should behave like the real thing for the patterns actually used
- Document known limitations (e.g., "only supports single filter per chain")
- Unknown methods should fail loudly (throw), not silently return wrong data

### Mutation recording (mocks)
When mocking a client that performs writes, record the mutations for assertion:
- Capture table name, operation type, payload, and filter values
- Let tests assert "this function called update on table X with payload Y"

### Tautological mock test
If an assertion checks a value that was set up directly in the test double rather than derived by the code under test, you're testing the test double, not the code. The test will always pass regardless of the production code's behavior.

**Bad (tautological):** stub `getUser` to return `{name: "Alice"}`, then assert the result equals `"Alice"`. This always passes — you're testing the stub, not the code.

**Good (tests real logic):** stub `getUser` to return `{name: "Alice", role: "admin"}`, then assert the formatted display string equals `"Alice (Admin)"`. This tests the formatting/transformation logic the code actually performs.

## 8. Flaky tests

When a test is intermittently failing:

1. **Root-cause first** — diagnose the specific cause:
   - Test isolation failure (shared state, execution order dependency)
   - External service timing (network, container startup, API availability)
   - Time zone sensitivity (test passes in one TZ, fails in another — common with date boundary logic)
   - Locale-dependent formatting (number/date formats vary by system locale)
   - Floating point comparison (use approximate equality, not exact)
   - Async timing (asserting on async state without proper synchronization)
   - Non-deterministic iteration order (hash maps/sets with no guaranteed order)
   - Port or resource conflicts in parallel execution
2. **Fix the layer** — if the test is flaky because it hits a real service to test logic, push it down the pyramid (see section 1)
3. **Quarantine in-test retries** — retry loops inside test code mask the underlying issue. If a fix isn't immediate, quarantine the test (skip with a tracking issue) rather than letting it erode trust in the suite. CI-level retry policies (rerun failed tests once before failing the build) are a separate, reasonable practice for transient infrastructure issues — but track retry rates and investigate if a test needs retries frequently
4. **Never delete without replacement** — a flaky integration test that covers an auth boundary still represents needed coverage. Replace it with a reliable test at the right layer before removing it

## 9. Anti-patterns

| Anti-pattern | Why it's wrong | Fix |
|---|---|---|
| All tests are integration tests | Slow, flaky, can't test every branch | Extract logic, add unit tests with stubs |
| Testing the test double | Assertions check values set up in the stub, not derived by code under test | Assert on values the code computed or transformed |
| No integration tests at all | Auth bugs, response shape regressions | Keep a few focused integration tests per endpoint |
| Smoke tests on every commit | Slow CI, rate limit exhaustion, flaky | Run smoke tests on deploy only |
| Testing implementation details | Tests break on refactor with no behavior change | Test inputs and outputs, not internal mechanics |
| Tautological assertions | `assert("error" in body or "data" in body)` passes on any response | Assert specific values |
| Duplicating production code in tests | Test passes with stale copy, drift | Import or call via integration |
| Reading source files to test behavior | Tests source text, not runtime behavior | Call function, assert output |
| Unconditional global state deletion | Breaks subsequent tests that need the value | Save and restore in guaranteed cleanup |
| In-test retry loops for flaky tests | Masks root cause, inflates suite duration | Root-cause and fix or quarantine |
| Test interdependence | Test B depends on state from Test A; reorder breaks both | Each test sets up and tears down its own state |
| Assertion-free tests | Test runs code but never asserts; passes as long as nothing throws | Every test must assert on a specific expected outcome |
| Sleep-based synchronization | `sleep(2)` to wait for async work; slow and still flaky | Use polling with timeout, await, or synchronization primitives |
| Hardcoded colliding test data | Every test uses `id=1` or `email=test@example.com`; parallel runs collide | Generate unique identifiers per test |
| Over-mocking | So many mocks that the test encodes the implementation, not behavior; brittle to refactors | Mock only direct dependencies; let integration tests cover wiring |
| Mocking third-party internals | Mocking a library's internal API rather than its public interface | Use the library's test utilities or mock at your own abstraction boundary |
