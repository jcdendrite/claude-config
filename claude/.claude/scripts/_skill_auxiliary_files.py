"""Filenames of the co-located auxiliary markdown files a skill directory may hold beside its SKILL.md.

Two consumers import it, so a rename or addition here changes both:

- select-tests.py's `_is_skill_auxiliary_md_change` domain predicate, which
  selects SKILLS_TESTS_DIR for a change to any of these files.
- claude-skills/skills/tests/test_skills.py's `_citation_sources_for_skill_md`
  citation-sibling expansion, which scans each of these files for `§`
  citations alongside its SKILL.md.
"""

SKILL_AUXILIARY_MD_NAMES: tuple[str, ...] = ("REFERENCES.md", "ROUTING.md", "DEFAULT_TEMPLATE.md")
