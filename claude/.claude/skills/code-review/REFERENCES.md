# References

Canonical sources that informed the rules in `SKILL.md`. Not loaded at
runtime — read this when editing the skill to verify a rule still
holds or to ground a new one.

## Finding disposition

### Default ADDRESS / opportunistic refactoring

**Martin Fowler — *Opportunistic Refactoring***
<https://martinfowler.com/bliki/OpportunisticRefactoring.html>

Key passages:

> "at any time someone sees some code that isn't as clear as it should
> be, they should take the opportunity to fix it right there and then
> — or at least within a few minutes."

> "Sometimes you see an opportunity when you're in the middle of
> something else. Rather than interrupt your current thought it's
> useful to make a note of it and come back to it when you are ready.
> Don't leave it for long, come back the same day, before you've hit
> that final point of being done."

> "Refactoring does depend on having a good regression suite."

> "There is a genuine danger of going down a rabbit hole here, as you
> fix one thing you spot another, and another, and before long you're
> deep in yak hair. Skillful opportunistic refactoring requires good
> judgement, where you decide when to call it a day."

Grounds the "Default ADDRESS" paragraph, criterion #1's
"tests already running" condition, and the 3+-DEFER smell test.

### Pre-existing problems and ticket discipline

**Google Engineering Practices — *What to look for in a code review***
<https://google.github.io/eng-practices/review/reviewer/looking-for.html>

Key passage:

> "encourage the author to file a bug and add a TODO for cleaning up
> existing code."

> "Don't accept CLs that degrade the code health of the system."

> "Encourage developers to solve the problem they know needs to be
> solved *now*, not the problem that the developer speculates *might*
> need to be solved in the future."

Grounds criterion #2's ticket-filing requirement when DEFER is
applied to pre-existing structural debt.

### Reviewer disposition framing

**Google Engineering Practices — *The Standard of Code Review***
<https://google.github.io/eng-practices/review/reviewer/standard.html>

Key passage:

> "In general, reviewers should favor approving a CL once it is in a
> state where it definitely improves the overall code health of the
> system being worked on, even if the CL isn't perfect."

Frames the bias toward ADDRESS without requiring perfection — every
finding gets disposed, but disposition includes ADDRESS-with-grouping
into this PR vs follow-ups.

### Author response to review concerns

**Google Engineering Practices — *Handling reviewer comments***
<https://google.github.io/eng-practices/review/developer/handling-comments.html>

Key passages:

> "If a reviewer says that they don't understand something in your
> code, your first response should be to clarify the code itself."

> "Writing a response in the code review tool doesn't help future code
> readers, but clarifying your code or adding code comments does help
> them."

> "if you understand the comments but disagree with them, it's
> important to think collaboratively, not combatively or defensively."

Grounds the principle that disposition discipline pushes toward
ADDRESS-via-code-change rather than ADDRESS-via-explanation-only — a
reviewer's "this is confusing" produces a code clarification, not a
reviewer-thread comment dismissing the concern.
