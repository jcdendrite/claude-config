"""Filenames of the co-located auxiliary markdown files a skill directory may hold beside its SKILL.md.

Two consumers import it, so a rename or addition here changes both:

- select-tests.py's `_is_skill_auxiliary_md_change` domain predicate, which
  selects SKILLS_TESTS_DIR for a change to any of these files.
- claude-skills/skills/tests/test_skills.py, which uses the names for:
  - citation-sibling expansion (`_citation_sources_for_skill_md`), which
    scans each of these files for `§` citations alongside its SKILL.md, and
    the per-name sibling-scan test that pins it;
  - an on-disk presence check per runtime-read name;
  - the URL-hygiene scan over runtime-read files.

Every name except REFERENCES.md, the edit-time reference, is treated as a
runtime-read file. A new edit-time auxiliary name would need that rule
revisited.

check-skill-length.sh keeps a hand-written exact-path regex for the
runtime-read subset of this tuple (ROUTING.md and DEFAULT_TEMPLATE.md), so a
new runtime-read name needs a matching entry there.

Entries are bare filenames with no path separator, because select-tests.py
matches `Path(path).name` against them.
"""

SKILL_AUXILIARY_MD_NAMES: tuple[str, ...] = ("REFERENCES.md", "ROUTING.md", "DEFAULT_TEMPLATE.md")
