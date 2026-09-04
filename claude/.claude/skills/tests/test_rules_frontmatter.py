"""Structural validation for `.claude/rules/*.md` path-scoped rule files.

Validates `paths:` frontmatter on both project rules (`.claude/rules/`) and
stowed rules (`claude/.claude/rules/`, installs to `~/.claude/rules/` and
applies to every repo the user opens):
- Every entry is a non-empty string.
- A project rule's literal prefix resolves to a real path — a directory if
  a wildcard remainder follows, an existing file or directory if not.
- A stowed rule's literal prefix, if not a bare filename or
  `.claude/`-anchored, carries no wildcard remainder.

This catches a syntactically-valid but wrong/typo'd glob such as
`"cluade/.claude/rules/**"`.

It does NOT catch a typo inside a wildcard-interior segment (e.g.
`"**/.github/wrokflows/*.yml"`) — rejecting that would also reject a
legitimate forward-looking portable glob like
`"**/.github/actions/**/action.yml"`, so the two are provably incompatible
checks (see `rule-authoring-conventions.md`).

Also flagged, lexically and before any filesystem lookup, on both project
and stowed rules:
- An empty or whitespace-only entry.
- An absolute path.
- A path containing `..`.

The much more common failure this check catches is `paths` being:
- Entirely absent.
- Empty.
- The wrong type.
- Containing a non-string entry.

Any of those ships a rule that either loads unconditionally by accident or
breaks Claude Code's frontmatter parsing outright. It drops the rule's
guidance across every one of the user's repos with no visible error.

Repo-root resolution for project-rule prefixes is a working assumption
(unverified against a primary source for `paths:` itself; verified only for
the adjacent `claudeMdExcludes` setting).

Run with: pytest claude/.claude/
"""
from __future__ import annotations

import fnmatch
import itertools
from pathlib import Path, PurePosixPath

import pytest
import yaml
from validate_skill_structure import parse_frontmatter

_REPO_ROOT = Path(__file__).resolve().parents[4]
_PROJECT_RULES_DIR = _REPO_ROOT / ".claude" / "rules"
_STOWED_RULES_DIR = _REPO_ROOT / "claude" / ".claude" / "rules"
_CLAUDE_MD_CONVENTIONS_RULE = _STOWED_RULES_DIR / "claude-md-conventions.md"
# One representative path per instruction-file location shape this rule's
# `paths` list targets: bare root, one directory down, root `.claude/`, and
# a nested `.claude/` — plus CLAUDE.local.md, which has no `.claude/` form.
# Every basename (CLAUDE.md, AGENTS.md, CLAUDE.local.md) gets both a root
# and a `.claude/`-prefixed candidate so every one of the ten `paths`
# patterns is exercised by at least one candidate below — fnmatch has no
# `**` path-segment awareness, so a `.claude/`-prefixed bare pattern like
# `.claude/AGENTS.md` always co-matches with its `**/AGENTS.md` sibling and
# can never be the sole match for any candidate; "exercised" here means
# "appears in some candidate's match list," not "is that candidate's only
# match."
_CLAUDE_MD_CANDIDATE_PATHS = [
    "CLAUDE.md",
    "sub/CLAUDE.md",
    ".claude/CLAUDE.md",
    "sub/.claude/CLAUDE.md",
    "AGENTS.md",
    "sub/.claude/AGENTS.md",
    ".claude/AGENTS.md",
    "CLAUDE.local.md",
    "sub/CLAUDE.local.md",
]

# `{` is included even though `glob.has_magic()` doesn't treat it as magic.
# Claude Code's `paths:` dialect supports brace expansion (see
# rule-authoring-conventions.md). Therefore `{skills,agents}` is not literal
# text. `glob.has_magic()` would extend the literal prefix past a
# nonexistent directory and false-positive.
_GLOB_METACHARACTERS = frozenset("*?[{")


def _literal_prefix_segments(glob: str) -> tuple[str, ...]:
    """Return glob's leading run of path segments containing no glob metacharacter.

    Parsed with `PurePosixPath`, not `glob.split("/")`: `paths:` globs are
    `/`-separated regardless of host OS. `PurePosixPath` also absorbs `//`,
    a trailing `/`, and a leading `./` for free. This returns the full
    leading literal run, not just the first segment, so a typo in a later
    literal segment (`claude/.claude/skils/**`) is still caught.
    """
    def _is_literal_segment(segment: str) -> bool:
        return not any(ch in _GLOB_METACHARACTERS for ch in segment)

    parts = PurePosixPath(glob).parts
    return tuple(itertools.takewhile(_is_literal_segment, parts))


def _discover_rule_files() -> list[tuple[Path, bool]]:
    files: list[tuple[Path, bool]] = []
    for rules_dir, is_stowed in ((_PROJECT_RULES_DIR, False), (_STOWED_RULES_DIR, True)):
        if rules_dir.is_dir():
            files.extend((f, is_stowed) for f in sorted(rules_dir.rglob("*.md")))
    return files


def rule_frontmatter_violations(
    rule_file: Path, *, is_stowed: bool, repo_root: Path | None = None
) -> list[str]:
    """Return violation messages for rule_file's `paths` frontmatter.

    Empty list means the file passes. Missing `paths` is always a bug here,
    even though Claude Code treats an absent key as a legitimate
    unconditional-load choice in general.

    `is_stowed` selects which glob-portability rule applies. A stowed rule
    (`claude/.claude/rules/`) must carry no leading literal path segment,
    since its referent is every consumer's repo, not this one. Two shapes
    are exempt: a bare filename with no wildcard (e.g. `CLAUDE.md`, which
    targets one exact path rather than assuming a directory), and a
    `.claude/`-anchored path (e.g. `.claude/CLAUDE.md`, a Claude Code
    convention directory present or absent uniformly in every repo). A
    project rule (`.claude/rules/`) may have a literal prefix. A fully
    literal glob (no wildcard remainder) must resolve to an existing path;
    a glob with a wildcard remainder must resolve its literal prefix to an
    existing directory, both under `repo_root`.

    `repo_root` defaults to `_REPO_ROOT`. Tests pass a synthetic
    `tmp_path`-rooted tree instead, so a directory-resolution fixture
    doesn't depend on this repo's real layout.
    """
    repo_root = _REPO_ROOT if repo_root is None else repo_root
    content = rule_file.read_text()
    if not content.startswith("---"):
        return [f"{rule_file} has no YAML frontmatter (must start with '---')"]

    try:
        frontmatter = parse_frontmatter(rule_file)
    except (yaml.YAMLError, ValueError) as exc:
        # ValueError covers an unterminated frontmatter block (missing closing
        # '---'). parse_frontmatter's content.index() raises ValueError
        # directly for this case. yaml.YAMLError alone doesn't catch it.
        return [f"{rule_file} has invalid or unterminated YAML frontmatter: {exc}"]

    if "paths" not in frontmatter:
        return [
            f"{rule_file} frontmatter is missing a `paths` key — path-scope "
            "it as a real rule, or move edit-time reference material to "
            "`docs/` instead"
        ]

    paths = frontmatter["paths"]
    if not (isinstance(paths, list) and paths):
        return [f"{rule_file} `paths` must be a non-empty list, got: {paths!r}"]

    non_string_entries = [p for p in paths if not isinstance(p, str)]
    if non_string_entries:
        return [
            f"{rule_file} `paths` entries must all be strings, "
            f"found non-string entries: {non_string_entries!r}"
        ]

    violations: list[str] = []
    for glob in paths:
        if not glob.strip():
            violations.append(
                f"{rule_file} `paths` entry {glob!r} is empty or whitespace-only"
            )
            continue

        parsed = PurePosixPath(glob)
        if parsed.is_absolute():
            violations.append(
                f"{rule_file} `paths` entry {glob!r} is an absolute path — "
                "rule globs must be repo-relative"
            )
            continue
        if ".." in parsed.parts:
            violations.append(
                f"{rule_file} `paths` entry {glob!r} contains `..` — "
                "rule globs must not escape the repo root"
            )
            continue

        prefix = _literal_prefix_segments(glob)
        if is_stowed:
            is_bare_filename = bool(prefix) and len(parsed.parts) == 1
            is_dotclaude_anchored = bool(prefix) and prefix[0] == ".claude"
            if prefix and not (is_bare_filename or is_dotclaude_anchored):
                violations.append(
                    f"{rule_file} `paths` entry {glob!r} carries a leading "
                    "literal path segment — stowed rule globs apply in "
                    "every stow consumer's repo and must be fully portable "
                    "(no leading literal segment, e.g. `**/`-led). Exempt: "
                    "a bare filename with no wildcard (e.g. `CLAUDE.md`) or "
                    "a `.claude/`-anchored path (e.g. `.claude/CLAUDE.md`) "
                    "— both are Claude Code convention locations, present "
                    "or absent uniformly across every consumer repo, not "
                    "an assumption about this repo's own layout."
                )
        else:
            is_fully_literal = bool(prefix) and len(prefix) == len(parsed.parts)
            if is_fully_literal:
                # No wildcard remainder: the glob targets one exact file, so
                # existence (file or directory) is the right check, not
                # is_dir() — a literal single-file paths: entry is valid.
                if not repo_root.joinpath(*prefix).exists():
                    violations.append(
                        f"{rule_file} `paths` entry {glob!r} does not resolve "
                        f"to an existing path under {repo_root}"
                    )
            elif prefix and not repo_root.joinpath(*prefix).is_dir():
                violations.append(
                    f"{rule_file} `paths` entry {glob!r} does not resolve to "
                    f"an existing directory under {repo_root}"
                )

    return violations


_RULE_FILES = _discover_rule_files()
_RULE_IDS = [str(f.relative_to(_REPO_ROOT)) for f, _ in _RULE_FILES]


def test_rule_files_exist():
    """Sanity check the discovery glob itself isn't silently empty."""
    assert _RULE_FILES, (
        f"Expected at least one rule file under {_PROJECT_RULES_DIR} or "
        f"{_STOWED_RULES_DIR}"
    )


@pytest.mark.parametrize("rule_file,is_stowed", _RULE_FILES, ids=_RULE_IDS)
def test_rule_has_parseable_paths_frontmatter(rule_file: Path, is_stowed: bool):
    """Every discovered rule file has a non-empty `paths` list of portable, resolving globs."""
    violations = rule_frontmatter_violations(rule_file, is_stowed=is_stowed)
    assert not violations, "; ".join(violations)


@pytest.mark.skipif(
    not _CLAUDE_MD_CONVENTIONS_RULE.is_file(),
    reason="claude-md-conventions.md not present",
)
@pytest.mark.parametrize("candidate_path", _CLAUDE_MD_CANDIDATE_PATHS)
def test_claude_md_conventions_globs_match_representative_paths(candidate_path: str):
    """Self-consistency check: claude-md-conventions.md's ten `paths` globs,
    taken together, must fire on every representative instruction-file
    location it targets.

    This uses `fnmatch` against a literal repo-root-relative string, which is
    NOT the same matcher Claude Code's harness runs — whether a leading
    `**/` matches zero path segments is undocumented (see
    `docs/rules-references.md`'s glob-decision note), so a pass here is not
    proof the real matcher fires. It only catches the typo/self-inconsistency
    class this module's own docstring names (e.g. `"cluade/.claude/rules/**"`):
    a candidate path matched by none of the ten globs signals a broken or
    dropped pattern, which is exactly the failure this rule's defensive
    bare-basename-plus-`**/`-led pairing is meant to prevent.
    """
    frontmatter = parse_frontmatter(_CLAUDE_MD_CONVENTIONS_RULE)
    paths = frontmatter["paths"]
    assert len(paths) == 10
    matched = [pattern for pattern in paths if fnmatch.fnmatch(candidate_path, pattern)]
    assert matched, (
        f"{candidate_path!r} matched none of {paths!r} — "
        "typo or dropped pattern in claude-md-conventions.md's paths list"
    )


class TestRuleFrontmatterViolations:
    """Unit tests for rule_frontmatter_violations() — uses tmp_path fixtures.

    The parametrized test above only ever sees this repo's current,
    presumably-well-formed rule files — it has never observed its own
    assertions fail on a broken frontmatter shape. These fixtures prove the
    validation logic actually discriminates good from bad input, independent
    of what the current repo's rule files happen to contain (mirrors
    TestCorpusBudgetFunction in test_skills.py).
    """

    def _write_rule(self, tmp_path: Path, content: str) -> Path:
        rule_file = tmp_path / "rule.md"
        rule_file.write_text(content)
        return rule_file

    def _make_repo_root(
        self, tmp_path: Path, dirs: tuple[str, ...] = (), files: tuple[str, ...] = ()
    ) -> Path:
        """Build a synthetic repo-root tree, isolated from this repo's real layout."""
        root = tmp_path / "repo_root"
        for d in dirs:
            (root / d).mkdir(parents=True, exist_ok=True)
        for f in files:
            target = root / f
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("")
        return root

    def test_well_formed_rule_passes(self, tmp_path):
        f = self._write_rule(tmp_path, '---\npaths:\n  - "**/*.sql"\n---\n\nbody\n')
        assert rule_frontmatter_violations(f, is_stowed=False) == []

    def test_missing_frontmatter_fails(self, tmp_path):
        f = self._write_rule(tmp_path, "# heading, no frontmatter\n")
        violations = rule_frontmatter_violations(f, is_stowed=False)
        assert violations and "no YAML frontmatter" in violations[0]

    def test_unterminated_frontmatter_block_fails(self, tmp_path):
        f = self._write_rule(tmp_path, '---\npaths:\n  - "**/*.sql"\nno closing delimiter\n')
        violations = rule_frontmatter_violations(f, is_stowed=False)
        assert violations and "invalid or unterminated" in violations[0]

    def test_invalid_yaml_fails(self, tmp_path):
        f = self._write_rule(tmp_path, "---\npaths: [unterminated\n---\n\nbody\n")
        violations = rule_frontmatter_violations(f, is_stowed=False)
        assert violations and "invalid or unterminated" in violations[0]

    def test_missing_paths_key_fails(self, tmp_path):
        f = self._write_rule(tmp_path, "---\nother_key: x\n---\n\nbody\n")
        violations = rule_frontmatter_violations(f, is_stowed=False)
        assert violations and "missing a `paths` key" in violations[0]
        assert "move edit-time reference material to `docs/`" in violations[0]

    def test_empty_paths_list_fails(self, tmp_path):
        f = self._write_rule(tmp_path, "---\npaths: []\n---\n\nbody\n")
        violations = rule_frontmatter_violations(f, is_stowed=False)
        assert violations and "non-empty list" in violations[0]

    def test_paths_as_string_fails(self, tmp_path):
        f = self._write_rule(tmp_path, '---\npaths: "**/*.sql"\n---\n\nbody\n')
        violations = rule_frontmatter_violations(f, is_stowed=False)
        assert violations and "non-empty list" in violations[0]

    def test_non_string_path_entry_fails(self, tmp_path):
        f = self._write_rule(tmp_path, "---\npaths:\n  - 42\n---\n\nbody\n")
        violations = rule_frontmatter_violations(f, is_stowed=False)
        assert violations and "non-string entries" in violations[0]

    def test_typo_in_literal_prefix_of_project_rule_fails(self, tmp_path):
        f = self._write_rule(
            tmp_path, '---\npaths:\n  - "cluade/.claude/rules/**"\n---\n\nbody\n'
        )
        repo_root = self._make_repo_root(tmp_path, dirs=("claude/.claude/rules",))
        violations = rule_frontmatter_violations(f, is_stowed=False, repo_root=repo_root)
        assert violations and "does not resolve to an existing directory" in violations[0]

    def test_multiple_paths_entries_surfaces_only_the_failing_entry(self, tmp_path):
        f = self._write_rule(
            tmp_path,
            '---\npaths:\n  - "**/*.sql"\n  - "cluade/.claude/rules/**"\n---\n\nbody\n',
        )
        repo_root = self._make_repo_root(tmp_path, dirs=("claude/.claude/rules",))
        violations = rule_frontmatter_violations(f, is_stowed=False, repo_root=repo_root)
        assert len(violations) == 1
        assert "cluade" in violations[0]

    def test_two_violating_paths_entries_both_surface(self, tmp_path):
        f = self._write_rule(
            tmp_path,
            '---\npaths:\n  - "cluade/.claude/rules/**"\n'
            '  - "kalude/.claude/skills/**"\n---\n\nbody\n',
        )
        repo_root = self._make_repo_root(
            tmp_path, dirs=("claude/.claude/rules", "claude/.claude/skills")
        )
        violations = rule_frontmatter_violations(f, is_stowed=False, repo_root=repo_root)
        assert len(violations) == 2
        assert any("cluade" in v for v in violations)
        assert any("kalude" in v for v in violations)

    def test_portable_forward_looking_glob_in_stowed_rule_passes(self, tmp_path):
        f = self._write_rule(
            tmp_path,
            '---\npaths:\n  - "**/.github/actions/**/action.yml"\n---\n\nbody\n',
        )
        assert rule_frontmatter_violations(f, is_stowed=True) == []

    def test_nonportable_glob_in_stowed_rule_fails(self, tmp_path):
        f = self._write_rule(
            tmp_path, '---\npaths:\n  - "claude/.claude/skills/**"\n---\n\nbody\n'
        )
        violations = rule_frontmatter_violations(f, is_stowed=True)
        assert violations and "must be fully portable" in violations[0]

    def test_fully_literal_glob_in_stowed_rule_passes(self, tmp_path):
        # No wildcard anywhere in the glob: it targets one exact path in
        # every consumer's repo (e.g. a root-level CLAUDE.md), not a
        # directory this repo's own layout happens to have — exempt from
        # the leading-literal-segment portability check.
        f = self._write_rule(tmp_path, '---\npaths:\n  - "CLAUDE.md"\n---\n\nbody\n')
        assert rule_frontmatter_violations(f, is_stowed=True) == []

    def test_dotclaude_anchored_literal_glob_in_stowed_rule_passes(self, tmp_path):
        # ".claude/" is a Claude Code convention directory, present or
        # absent uniformly across every consumer repo — a multi-segment
        # literal glob anchored there is still portable, unlike one
        # anchored at this repo's own top-level directory names.
        f = self._write_rule(
            tmp_path, '---\npaths:\n  - ".claude/CLAUDE.md"\n---\n\nbody\n'
        )
        assert rule_frontmatter_violations(f, is_stowed=True) == []

    def test_multisegment_literal_glob_in_stowed_rule_fails(self, tmp_path):
        # No wildcard anywhere, but the literal path is multi-segment and
        # not ".claude/"-anchored — it only resolves inside this repo's own
        # layout, not a target this rule could match in a typical consumer
        # repo. The bare-filename and ".claude/"-anchored exemptions must
        # not widen to cover this shape.
        f = self._write_rule(
            tmp_path, '---\npaths:\n  - "claude/.claude/skills/tests"\n---\n\nbody\n'
        )
        violations = rule_frontmatter_violations(f, is_stowed=True)
        assert violations and "must be fully portable" in violations[0]

    def test_fully_literal_single_file_glob_in_project_rule_passes(self, tmp_path):
        # No wildcard anywhere: the glob targets one exact file. Requiring
        # it to resolve to a directory (the wildcard-bearing branch's rule)
        # would wrongly reject a legitimate single-file paths: entry.
        f = self._write_rule(
            tmp_path, '---\npaths:\n  - "docs/notes.md"\n---\n\nbody\n'
        )
        repo_root = self._make_repo_root(tmp_path, files=("docs/notes.md",))
        assert rule_frontmatter_violations(f, is_stowed=False, repo_root=repo_root) == []

    def test_fully_literal_nonexistent_file_glob_in_project_rule_fails(self, tmp_path):
        # No wildcard, but the target doesn't exist — still a typo, just
        # without a wildcard remainder to trigger the directory-resolution
        # branch. The fully-literal carve-out must check existence, not
        # skip validation entirely.
        f = self._write_rule(
            tmp_path, '---\npaths:\n  - "docs/cluade-notes.md"\n---\n\nbody\n'
        )
        repo_root = self._make_repo_root(tmp_path, dirs=("docs",))
        violations = rule_frontmatter_violations(f, is_stowed=False, repo_root=repo_root)
        assert violations and "does not resolve to an existing path" in violations[0]

    def test_typo_in_third_segment_of_project_rule_fails(self, tmp_path):
        f = self._write_rule(
            tmp_path, '---\npaths:\n  - "claude/.claude/skils/**"\n---\n\nbody\n'
        )
        repo_root = self._make_repo_root(tmp_path, dirs=("claude/.claude",))
        violations = rule_frontmatter_violations(f, is_stowed=False, repo_root=repo_root)
        assert violations and "does not resolve to an existing directory" in violations[0]

    def test_question_mark_wildcard_terminates_literal_prefix_in_project_rule_passes(
        self, tmp_path
    ):
        f = self._write_rule(tmp_path, '---\npaths:\n  - "docs/re?dme.md/**"\n---\n\nbody\n')
        repo_root = self._make_repo_root(tmp_path, dirs=("docs",))
        assert rule_frontmatter_violations(f, is_stowed=False, repo_root=repo_root) == []

    def test_absolute_path_glob_in_project_rule_fails(self, tmp_path):
        f = self._write_rule(
            tmp_path, '---\npaths:\n  - "/opt/checkout/claude/**"\n---\n\nbody\n'
        )
        violations = rule_frontmatter_violations(f, is_stowed=False)
        assert violations and "absolute path" in violations[0]

    def test_absolute_path_glob_in_stowed_rule_fails(self, tmp_path):
        f = self._write_rule(
            tmp_path, '---\npaths:\n  - "/opt/checkout/claude/**"\n---\n\nbody\n'
        )
        violations = rule_frontmatter_violations(f, is_stowed=True)
        assert violations and "absolute path" in violations[0]

    def test_empty_string_path_entry_fails(self, tmp_path):
        f = self._write_rule(tmp_path, '---\npaths:\n  - ""\n---\n\nbody\n')
        violations = rule_frontmatter_violations(f, is_stowed=False)
        assert violations and "empty or whitespace-only" in violations[0]

    def test_whitespace_only_path_entry_fails(self, tmp_path):
        f = self._write_rule(tmp_path, '---\npaths:\n  - "   "\n---\n\nbody\n')
        violations = rule_frontmatter_violations(f, is_stowed=True)
        assert violations and "empty or whitespace-only" in violations[0]

    def test_post_wildcard_dotdot_escape_in_project_rule_fails(self, tmp_path):
        f = self._write_rule(
            tmp_path, '---\npaths:\n  - "docs/**/../secret/**"\n---\n\nbody\n'
        )
        violations = rule_frontmatter_violations(f, is_stowed=False)
        assert violations and "must not escape the repo root" in violations[0]

    def test_post_wildcard_dotdot_escape_in_stowed_rule_fails(self, tmp_path):
        f = self._write_rule(
            tmp_path, '---\npaths:\n  - "**/../../secrets/**"\n---\n\nbody\n'
        )
        violations = rule_frontmatter_violations(f, is_stowed=True)
        assert violations and "must not escape the repo root" in violations[0]

    def test_literal_prefix_resolving_to_file_not_directory_fails(self, tmp_path):
        f = self._write_rule(
            tmp_path, '---\npaths:\n  - "docs/rules-references.md/**"\n---\n\nbody\n'
        )
        repo_root = self._make_repo_root(tmp_path, files=("docs/rules-references.md",))
        violations = rule_frontmatter_violations(f, is_stowed=False, repo_root=repo_root)
        assert violations and "does not resolve to an existing directory" in violations[0]

    def test_relative_escape_prefix_in_project_rule_fails(self, tmp_path):
        f = self._write_rule(tmp_path, '---\npaths:\n  - "../claude/**"\n---\n\nbody\n')
        violations = rule_frontmatter_violations(f, is_stowed=False)
        assert violations and "must not escape the repo root" in violations[0]

    def test_brace_expansion_treated_as_metacharacter_in_project_rule_passes(self, tmp_path):
        f = self._write_rule(
            tmp_path, '---\npaths:\n  - "claude/{skills,agents}/**"\n---\n\nbody\n'
        )
        repo_root = self._make_repo_root(tmp_path, dirs=("claude",))
        assert rule_frontmatter_violations(f, is_stowed=False, repo_root=repo_root) == []

    def test_brace_segment_before_wildcard_in_project_rule_passes(self, tmp_path):
        # The brace segment precedes "**", so `_literal_prefix_segments()` must
        # actually inspect it to know where the literal run stops — a glob like
        # "**/*.{yml,yaml}" would halt at the leading "**" first and never reach it.
        f = self._write_rule(tmp_path, '---\npaths:\n  - "docs/{en,es}/**"\n---\n\nbody\n')
        repo_root = self._make_repo_root(tmp_path, dirs=("docs",))
        assert rule_frontmatter_violations(f, is_stowed=False, repo_root=repo_root) == []
