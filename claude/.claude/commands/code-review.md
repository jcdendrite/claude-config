---
description: Principal engineer code review of changed/new code before presenting to user
---

Review the code that was just written or modified. Act as a principal engineer reviewing a junior engineer's work. Be thorough but not pedantic.

## Checklist

Evaluate the code against each item. Only flag items where there is a concrete issue — do not flag items just to show you checked them.

### Correctness

1. **API misuse** — Are libraries, frameworks, and language APIs used as designed? Flag any reliance on accidental or undocumented behavior (e.g., passing invalid arguments that happen to work, using internal methods, relying on side effects of unrelated calls).

2. **Silent error swallowing** — Are there catch blocks, fallback defaults, or error handlers that hide failures the caller would want to know about? Empty catch blocks, catch-and-return-null, and catch-and-log-only are all suspects.

3. **Race conditions** — Is shared mutable state accessed concurrently without synchronization? Check module-level variables, singletons, caches, and lazy-init patterns.

4. **Silent defaults for unexpected values** — Does the code silently substitute a default when it encounters an unexpected value (e.g., unknown enum variant, unrecognized config key)? In infrastructure and test code, prefer throwing over guessing.

### Hygiene

5. **Dead exports** — Are there exported types, functions, or constants that are not imported by any other file? Check with grep before flagging.

6. **Unnecessary wrappers** — Are there functions that simply delegate to another function without adding any logic, type narrowing, or meaningful naming? These add indirection without value.

7. **Inline business logic where a library method exists** — Is there hand-rolled logic (regex parsing, string manipulation, date math, data structure operations) where the project's existing dependencies already provide a tested, maintained function for the same thing?

### Clarity

8. **Undocumented limitations** — Does the code make assumptions or have known constraints that aren't visible to future readers? Examples: only handling the first element of a list, assuming single-tenant usage, ignoring edge cases by design.

9. **Misleading names** — Do function or variable names promise more or less than they deliver? A function called `validateUser` that only checks one field, or a variable called `allItems` that contains a filtered subset.

### Context

10. **Contradicts surrounding code comments** — Does the new code violate guidance written in comments in the same file or adjacent code (e.g., `// IMPORTANT:`, `// NOTE:`, `// TODO:` that the new code should have respected)?

### Scope discipline

11. **Pre-existing issues in unchanged code** — If you notice issues in code that was NOT written or modified in this change, flag them in a separate "Pre-existing issues" section. Do NOT fix them — they are informational only and out of scope.

## Exclusions — do NOT flag these

- Issues that a linter, typechecker, or compiler would catch (imports, type errors, formatting)
- Stylistic nitpicks in unchanged code (naming conventions, whitespace, comment style)
- Generic improvement suggestions ("add tests," "add docs," "improve error messages") not tied to a specific finding from the checklist above

## Output format

For each finding, state:

1. **Which checklist item** (by number and name)
2. **File and line**
3. **What the issue is** (one sentence)
4. **Why it matters** (one sentence)
5. **Suggested fix** (concrete, not "consider improving")

If no issues are found, say: "No issues found" — do not pad with praise or generic observations.
