# Prose that restates the code is deleted, not corrected

*2026-10-26.*

`claude/.claude/CLAUDE.md` § "Durable text" states the rule that prose restating the code is deleted. `claude-skills/skills/code-review/SKILL.md` § "Output format" carries the remedy in item 5. This record keeps only why the rule has that shape.

## Why deletion, not correction

A corrected or shorter restatement still restates the code, so it drifts at the next code change and draws the next accuracy finding. Without a named deletion remedy, a finding on stale restating prose offers only a choice between keeping the stale text and applying a corrected rewrite. Neither choice removes the prose, so the prose keeps drifting and the finding keeps recurring.

## Why the remedy lives in item 5

Item 5 shapes the suggested fix of every finding, from every reviewer. `claude-skills/skills/code-review/SKILL.md` § "Finding disposition" defines the fix route, which applies the fix on each ADDRESS row. It also defines the contradiction route, whose "apply this round's fix" verdict is an ordinary row on the fix route. Once item 5 names a deletion for restating prose, every route that applies a fix applies the deletion, so the contradiction route needs no verdict of its own. A finding that asks to restore deleted restating prose names no defect the current text has, so the contradiction route's consult keeps the current text.

## Why restating the code is its own review angle

`comment-discipline-reviewer` checks sound prose in the wrong place under Prose at the wrong altitude, and the fix is relocation. Prose that restates the code should not exist anywhere, and the fix is deletion. One angle carrying both remedies leaves a README passage on how a feature works resolvable either way. With two angles, each remedy has its own check. A passage that matches both is deleted, because the restating angle takes precedence.

## Why instruction files are not exempt from the altitude angle

`claude/.claude/CLAUDE.md` § "Durable text" says what prose is acceptable, and it applies to instruction files like any other durable text. `ai-instruction-and-memory-files` decides which surface misplaced instruction-file content moves to, which is the domain-specific half of a relocation. A skill or rule that loads for a file type owns where all misplaced content in those files goes, not only rule content, so the skill gains a row for content that is not a rule in `ai-instruction-and-memory-files/SKILL.md` § "Where does a given rule belong?".

Exempting instruction files from the altitude angle would avoid running one angle twice when both reviewers run. But the skill's body applies no altitude test, so the exemption would leave instruction-file altitude unreviewed.

## Why the reviewer moves to `effort: high`

The trigger in [paragraph-readability-review-angle.md](paragraph-readability-review-angle.md)'s "Revisiting the angle count" section fired: the new angle cross-references the altitude angle. The reviewer moves to `effort: high` and stays one agent. Its tool design stays closed-form, since it reads a diff against a fixed rule set and has no `Bash`. What changed is the judgment at the boundary between angles, now that one angle overrides another.

## Why there is no published-API exception

The rule exempts no prose on the ground that it documents a published API.

- The maintainer judges published APIs rare.
- When one exists, a human who knows it is published can direct a docstring pass afterwards.
- An agent cannot reliably tell whether a given piece of code is published.
- Asking every time whether code is published is disruptive.

PEP 8, Go and Google give every public name a docstring, and Google and Rust document a public API's errors. This rule departs from them by design.

## Sources

Each quote below was checked against its URL on 2026-10-26.

- PEP 8, https://peps.python.org/pep-0008/: "Comments that contradict the code are worse than no comments."; "Inline comments are unnecessary and in fact distracting if they state the obvious."; "Write docstrings for all public modules, functions, classes, and methods."
- PEP 257, https://peps.python.org/pep-0257/: the one-line docstring "should NOT be a 'signature' reiterating the function/method parameters".
- Google Python Style Guide, https://google.github.io/styleguide/pyguide.html: "never describe the code. Assume the person reading the code knows Python (though not what you're trying to do) better than you do."; a docstring is "mandatory for every function that has one or more of the following properties": being part of the public API, nontrivial size, non-obvious logic; Args, Returns and Raises "can be omitted in cases where the function's name and signature are informative enough"; the Raises section: "List all exceptions that are relevant to the interface".
- Google engineering practices, https://google.github.io/eng-practices/review/reviewer/looking-for.html: "Usually comments are useful when they explain why some code exists, and should not be explaining what some code is doing."; "mostly comments are for information that the code itself can't possibly contain, like the reasoning behind a decision."; "regular expressions and complex algorithms often benefit greatly from comments that explain what they're doing".
- Go, https://go.dev/doc/comment: "Every exported (capitalized) name should have a doc comment."; "Doc comments should not explain internal details such as the algorithm used in the current implementation."
- Rust API guidelines, https://rust-lang.github.io/api-guidelines/documentation.html: a public function's errors and panics are documented in its Errors and Panics sections. This entry is a description, not a quote.
- Diátaxis, https://diataxis.fr/how-to-guides/: "How-to guides must be written from the perspective of the user, not of the machinery."; how-to guides "are about goals, projects and problems, not about tools."
