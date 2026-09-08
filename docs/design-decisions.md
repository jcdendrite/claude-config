# Design Decisions

Non-obvious choices and the reasoning behind them, one file per decision under [`design-decisions/`](design-decisions/). Each file is named by a stable slug rather than a section number, so two branches recording a decision never collide on this path again.

A citation to `docs/design-decisions.md §N` written before the split resolves by grepping the directory for its frozen legacy number, e.g. `git grep '§45' docs/design-decisions/`. Every file under `design-decisions/` carries an italic provenance line — recording the section number it was migrated from, for a decision that predates the split, and a date instead for one recorded afterward.

The pre-split history of any individual decision is recoverable with `git log -S'cannot be talked out of firing' -- docs/design-decisions.md`. The split commit moved content without renaming files, so git does not track per-section history across it.

Sibling to [`case-studies.md`](case-studies.md), which carries longer-form writeups with primary-source citations behind some of these decisions.
