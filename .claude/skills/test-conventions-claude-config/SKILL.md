---
name: test-conventions-claude-config
description: Project-specific layer for /test-conventions, loaded only when writing or reviewing tests in the claude-config repo itself.
disable-model-invocation: true
---

## Filesystem fixtures must not collide under case-insensitive filesystems

CI runs Linux only (`.github/workflows/tests.yml`), and contributors run the same
suite on macOS, whose default volume is case-insensitive and case-preserving. Two
paths in one directory that differ only by case are one path there. A second
`mkdir()` raises `FileExistsError`, a second `write_text()` silently overwrites the
first, and `exists()` returns True for the wrong case — on a contributor's machine,
never in CI.

No two entries in one fixture directory may address the same file on a
case-insensitive volume, and a test must not rely on `exists()` telling
case-only-different names apart. Names equal under `str.casefold()` are the case
to check for.

When a test builds a path from a value set — a loop, a `parametrize` list, a
factory helper's `name` argument — check the set for a casefold pair. If it has
one, give each value its own `tmp_path` via `@pytest.mark.parametrize` and keep
the directory name itself constant.
