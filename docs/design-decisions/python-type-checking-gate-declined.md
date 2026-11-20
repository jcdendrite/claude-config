# Python type-checking gate declined

*2026-11-20.*

The question is whether a static type checker should run as a check on this repo's Python (`claude/.claude/`, `claude-skills/`, `plugins/`, `evals/`), in CI, in `select-tests.py`, or in `/ready-for-review`. It should not. The working hypothesis was that a checker gives a net benefit, especially to AI agents working in the code. The published evidence does not measure that. Two measurements over this repo's tracked source and git history found a large backlog of diagnostics and no defect the checker would have caught. Two further reads, of review findings and of session transcripts, did not change the decision. [pyright-lsp-installed-per-contributor.md](pyright-lsp-installed-per-contributor.md) stands: a contributor can still get post-edit diagnostics in a session, and nothing blocks on them.

The decision covers a gate. It does not cover annotations, which contributors and agents may keep writing.

## Published evidence

No study was found that measures whether type-checker feedback improves agent-written or agent-maintained Python, and none that compares agents working in typed and untyped Python repositories. Each quoted sentence and figure below was checked on 2026-11-20 against its source listed under Sources, except where an item says otherwise. The characterizations around them are this record's reading of those sources.

- **Agents, other languages.** Mündler et al., "Type-Constrained Code Generation with Language Models" (arXiv preprint), report that the approach "reduces compilation errors by more than half". The technique constrains decoding, which needs token-level access to the model, and the paper demonstrates it on TypeScript.
- **Agents, static checks in the loop.** Yang et al., "SWE-agent" (arXiv preprint), gate file edits on a linter, not a type checker. This repo already runs `ruff`. This item was not checked at its source. It rests on an earlier reading of the paper.
- **Human baseline.** Khan et al., "An Empirical Study of Type-Related Defects in Python Projects" (IEEE Transactions on Software Engineering), studied 210 projects: "We observe that 15% of the defects could have been prevented by mypy." The authors call the figure a lower bound. They added the annotations themselves and did not measure false positives or annotation cost.
- **Cost.** Rak-amnouykit et al., "Python 3 Types in the Wild: A Tale of Two Type Systems" (DLS 2020), hand-classified 52 of the 105 mypy errors they examined (49%) as false positives, sampling 15 errors per error category. The paper names `None` checks as a main source. The mypy it measured dates from 2020.
- **Vendor claim.** The Claude Code documentation says that with a language server running, "Claude gets the errors and warnings the server reports" after each edit. It gives no measurement.

## Diagnostics on the current tree

Pyright 1.1.414 ran at its default settings, with no configuration file, over all 576 tracked Python files at commit `6cc9e053` on 2026-11-20. That is about 446,000 lines, of which about 67,000 are outside test files. `git ls-tree -r --name-only 6cc9e053`, filtered to names ending in `.py`, lists the files, and `git grep -c '' 6cc9e053 -- '*.py'` counts their lines.

- **Backlog.** 4,323 errors: 3,208 in test files and 1,115 in non-test files.
- **Structural noise.** 855 of the non-test errors are `reportUndefinedVariable` in the `cursor-adapter` scripts. At that commit the adapter was a set of part files that shared one namespace by design.
- **Run time.** About 4 minutes of CPU for the whole tree with no cache. Wall time was not isolated from other load on the machine.
- **Hidden directory.** That pyright version skips `claude/.claude/`, which holds 463 of the 576 files, even when the directory is named on the command line. The pyright configuration documentation on its `main` branch, as read on 2026-11-20, describes a `useDefaultExcludes` setting. Version 1.1.414 rejects that setting as unrecognized. The files under `claude/.claude/` were measured from a copy at a non-hidden path.
- **Platform.** On macOS pyright reports the `sched_getaffinity` import in `select-tests.py`, which the code guards with `try/except ImportError`.

## Triage of a random sample

The 260 non-test errors that remain after removing the `cursor-adapter` noise were sampled at random, 40 of 260, and each site was read against the code.

- **Real defect: 0.** No sampled site has a reachable input that raises or computes a wrong result.
- **False positive: 29.** A guard the checker cannot follow protects the value: a regex or validator helper, an early return, or two conditions that always hold together.
- **Inaccurate annotation: 6.** The runtime behaviour is correct and the declared type is wrong, such as a helper that always exits and is annotated `-> None`.
- **Latent: 5.** The site is safe only because a caller elsewhere filters the bad value.

## History replay

The pool is the 251 non-merge commits reachable from `6cc9e053` that modified an existing non-test Python file under the four directories. Commits that only added or deleted such files are outside it. A test file is one named `test_*.py` or `conftest.py`, or one under a `tests/` directory. Of those 251 commits, 60 were sampled at random. Pyright ran on each commit's changed files before and after the change. A hit is a diagnostic on a line the commit changed that is absent afterwards.

- **Defects caught: 0 of 60.**
- **Hits: 3, all refactors.** In two, the flagged code moved to another module, where the same diagnostic still fires. In the third, pyright flagged an attribute that a module-level `__getattr__` supplies at runtime.
- **Trend.** Diagnostics in the changed files rose from 912 before to 955 after. Twelve commits added diagnostics and two removed some.

The replay sees one shape of bug. A defect fixed by editing a different line does not register.

## Review findings and session transcripts

Two further reads covered work that never reaches git history. Neither produced a figure this record can publish, because no command that refuses a wider corpus produced them (`docs/private-project-redaction.md` § "Publishing a tooling measurement").

- **Review ledger.** Addressed `/code-review` findings, filtered to non-test Python paths under the four directories, were classified by whether a type checker could report the defect described. The read did not change the decision.
- **Session transcripts.** Python errors in tool output, filtered to sessions run from checkouts of this repo, were classified by whether pyright's default settings would have reported them before the run. The read did not change the decision.

## Limits

- **Default settings on partly annotated code.** Pyright treats an unannotated parameter as unknown, so a defect that flows through one is invisible to it. A strict configuration on fully annotated code could score differently, at a higher annotation cost.
- **Sample sizes.** Zero in 40 and zero in 60 bound the rate from above. They do not show that it is zero.
- **Triage by reading.** Each sampled site was classified by reading the code, not by running it.

## Reconsideration trigger

Revisit if any of these holds:

1. A published study measures type-checker feedback on agent-written Python and reports a benefit.
2. A defect reaches `main` that a checker's default settings would have reported and that the tests and `ruff` missed.
3. The non-test code becomes annotated enough that a sample of its diagnostics is mostly real defects.

A revisit scopes any gate to non-test code first, since test files hold about three quarters of the backlog.

## Sources

- https://swag.uwaterloo.ca/publications/an-empirical-study-of-type-related-defects-in-python-projects.html
- https://www.cs.rpi.edu/~milanova/docs/dls2020.pdf
- https://arxiv.org/abs/2504.09246
- https://arxiv.org/abs/2405.15793
- https://code.claude.com/docs/en/plugins/code-intelligence.md
- https://raw.githubusercontent.com/microsoft/pyright/main/docs/configuration.md
- `docs/design-decisions/pyright-lsp-installed-per-contributor.md`
- `docs/private-project-redaction.md`
