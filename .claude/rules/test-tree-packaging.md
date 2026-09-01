---
paths:
  - "claude/.claude/**/tests/**"
  - "claude/.claude/**/conftest.py"
---

## Test-tree packaging under `claude/.claude/`

A test directory that carries its own `conftest.py` needs an `__init__.py` in
both itself and its parent domain directory — `claude/.claude/<domain>/__init__.py`
and `claude/.claude/<domain>/tests/__init__.py`. Both are required: with only the
leaf marker, the tree resolves to a module name one level too shallow and
collides with a sibling tree's conftest. Each marker holds one docstring
line naming the test below, and nothing else.

In a test tree that has an `__init__.py`, import a same-directory sibling module
as `from .sibling import X`, never `from sibling import X` — the bare form stops
resolving once the directory is a package.

Do not add `claude/.claude/tests/__init__.py`. That directory stays unpackaged
deliberately. `helpers.py` is already importable as a top-level module through
`pyproject.toml`'s `pythonpath`. Packaging the directory would create a second
`helpers` module object with its own `REPO_ROOT`.

`claude/.claude/tests/test_pytest_collection_config.py` carries the mechanism:

- `TestConftestModuleNamesAreUnique` fails CI on a missing marker.
- `TestNoBareSameDirectorySiblingImports` fails CI on a bare sibling import.
