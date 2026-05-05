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
