# References — test-conventions

Reference material that informed this skill. Not loaded during skill execution — consult when editing the skill to verify a rule still holds or to add new guidance.

## Test-first discipline and its recognized exceptions

The test-first invariant is most strongly stated in:

- **Kent Beck, *Test-Driven Development: By Example*** (Addison-Wesley, 2002) — canonical statement. Beck's later [Stack Overflow answer "How deep are your unit tests?"](https://stackoverflow.com/questions/153234/how-deep-are-your-unit-tests) frames the burden-of-proof clause: *"I get paid for code that works, not for tests, so my philosophy is to test as little as possible to reach a given level of confidence."*
- **Gerard Meszaros, *xUnit Test Patterns*** (Addison-Wesley, 2007) — operationalized test-first into mainstream xUnit practice. Defines the [Humble Object](http://xunitpatterns.com/Humble%20Object.html) pattern that the SKILL section names for the UI/view-layer exception.

Recognized exception categories are attested across:

- **Spike solutions** — Kent Beck, [Spike Solution](https://wiki.c2.com/?SpikeSolution=) and *TDD by Example* Part III; James Shore, [Spike Solutions](https://www.jamesshore.com/v2/books/aoad1/spike_solutions); Ron Jeffries on the c2 wiki. Definition: knowledge-limited exploratory code, thrown away and re-done test-first.
- **UI / view-layer code** — Meszaros, [Hard to Test Code](http://xunitpatterns.com/Hard%20to%20Test%20Code.html); Robert C. Martin, [When TDD doesn't work](https://blog.cleancoder.com/uncle-bob/2014/04/30/When-tdd-does-not-work.html); David Heinemeier Hansson, [Test-induced design damage](https://dhh.dk/2014/test-induced-design-damage.html); Google, [*Software Engineering at Google* ch. 14](https://abseil.io/resources/swe-book/html/ch14.html) — *"UI tests are notoriously unreliable and costly."*
- **Configuration and wiring** — Uncle Bob, *When TDD doesn't work*; Google, [*SWE at Google* ch. 11](https://abseil.io/resources/swe-book/html/ch11.html) — large tests are *"more about validating configuration than pieces of code."*
- **Physical / hardware boundaries** — Uncle Bob, *When TDD doesn't work*; Bissi et al., [*On the use of TDD for Embedded Systems*](https://www.sciencedirect.com/science/article/pii/S0950584925001181) (Sci. of Computer Programming, 2025).
- **Test infrastructure** — Meszaros, *Hard to Test Code*; Uncle Bob, *When TDD doesn't work*.

The 2014 [*Is TDD Dead?*](https://martinfowler.com/articles/is-tdd-dead/) conversation (Beck, Fowler, DHH) and Ian Cooper's [*TDD: Where Did It All Go Wrong?*](https://www.infoq.com/presentations/tdd-original/) (DevTernity 2017) frame the modern consensus: test at the module's public API rather than per-class, and treat TDD as context-dependent rather than universal. Beck's [Test Desiderata](https://medium.com/@kentbeck_7670/test-desiderata-94150638a4b3) (2019) reframes testing as a property tradeoff space.

## Regex and logic in test assertions over structured output

Two independent primary sources ground the "prefer a parser or production validator over hand-rolled regex in test assertions" rule:

**Software Engineering at Google, ch. 12 "Unit Testing" — "Don't Put Logic in Tests"**
Erik Kuefler; CC BY-NC-ND 4.0. URL: https://abseil.io/resources/swe-book/html/ch12.html

Verbatim:
> "Clear tests are trivially correct upon inspection; that is, it is obvious that a test is doing the correct thing just from glancing at it. This is possible in test code because each test needs to handle only a particular set of inputs, whereas production code must be generalized to handle any input. For production code, we're able to write tests that ensure complex logic is correct. But test code doesn't have that luxury—if you feel like you need to write a test to verify your test, something has gone wrong!
>
> Complexity is most often introduced in the form of *logic*. Logic is defined via the imperative parts of programming languages such as operators, loops, and conditionals. When a piece of code contains logic, you need to do a bit of mental computation to determine its result instead of just reading it off of the screen. It doesn't take much logic to make a test more difficult to reason about."

**Applies to the rule as:** A regex applied to expected output is "logic" by this definition — computing its result requires mental effort rather than a glance. The test is no longer trivially correct upon inspection.

**Gerard Meszaros, *xUnit Test Patterns: Refactoring Test Code* (Addison-Wesley, 2007) — Fragile Test / Overspecified Software**
URL: http://xunitpatterns.com/Fragile%20Test.html *(direct page fetch timed out; cite the book)*

The **Fragile Test** smell includes the **Overspecified Software** root cause: *"One problem occurs when the test needs to duplicate much of the logic in the SUT to calculate the expected results."* A test that uses a regex to re-parse the same structured output the production code parses is exactly this — the parsing logic is duplicated inside the assertion. The test then has two ways to fail: a real production regression and a benign format change that breaks only the test's copy of the parser.

**Applies to the rule as:** The runtime-output variant (regex over log lines, JSON, XML) is a Fragile Test via Overspecified Software; the fix is to call the production parse/validate path and assert on the parsed result, eliminating the duplicate logic.

**Note on "use a parser for structured formats":** There is no single canonical primary document for the general "don't parse structured formats with regex" principle as a *testing* rule. The rule follows as a corollary from the two sources above: a regex on structured output is both logic-in-tests (Source 1) and duplicated-SUT-logic (Source 2). The theoretical basis — that structured formats like JSON and XML are context-free languages and regex matches only regular languages — is formal language theory (Chomsky 1956), but that framing is too abstract for a code-review rule. Ground it via Sources 1 and 2 instead.

## Declaring a non-import cross-domain test dependency explicitly

Grounds the "Test double seams by dependency type" bullet on declaring a
subprocess-call or file-path-read edge that reaches outside a test's own
domain, for tooling that maps domains to tests by import or directory.

**Nx devkit reference — `ImplicitDependency`**
URL: https://nx.dev/docs/reference/devkit/ImplicitDependency

Verbatim: an implicit dependency is "a connection without an explicit reference in code" between two projects, and declaring one manually is "the best way to manually set up a dependency between two projects that Nx is not able to detect automatically."

**Applies to the rule as:** Nx's own affected-project graph is built primarily from static imports. `ImplicitDependency` is Nx's own escape hatch for a real dependency that graph can't see. A test that shells into another domain's script or reads its file by path — rather than importing it — is the same shape: invisible to both import-based and directory-based selection unless declared explicitly.
