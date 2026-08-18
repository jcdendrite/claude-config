"""Pins docs/transcript-analysis-architecture.md's module list against the
transcript_analysis/ package directory it documents, so a module added or
removed there without a matching doc edit fails CI instead of rotting
silently.
"""
from __future__ import annotations

from helpers import REPO_ROOT

PACKAGE_DIR = REPO_ROOT / "claude" / ".claude" / "scripts" / "transcript_analysis"
ARCHITECTURE_DOC = REPO_ROOT / "docs" / "transcript-analysis-architecture.md"


def _documented_module_names() -> set[str]:
    """Module basenames (e.g. "corpus.py") named in a `### \\`<name>.py\\`` heading --
    the name must be the only thing on the line, or the line is skipped as
    undocumented rather than partially matched."""
    text = ARCHITECTURE_DOC.read_text()
    names = set()
    for line in text.splitlines():
        if not line.startswith("### "):
            continue
        name = line.removeprefix("### ").strip().strip("`")
        if name.endswith(".py"):
            names.add(name)
    return names


def _package_module_names() -> set[str]:
    """Every module in the package directory except __init__.py, which carries
    no responsibilities of its own to document."""
    return {p.name for p in PACKAGE_DIR.glob("*.py") if p.name != "__init__.py"}


def test_architecture_doc_documents_every_package_module():
    documented = _documented_module_names()
    on_disk = _package_module_names()

    missing_from_doc = on_disk - documented
    assert not missing_from_doc, (
        f"docs/transcript-analysis-architecture.md is missing a section for {missing_from_doc} -- "
        f"add a `### <module>.py` heading describing its responsibilities"
    )

    stale_in_doc = documented - on_disk
    assert not stale_in_doc, (
        f"docs/transcript-analysis-architecture.md documents {stale_in_doc}, "
        f"which no longer exists under {PACKAGE_DIR} -- update or remove that section"
    )


def test_package_directory_is_not_empty():
    """Guards the test above against a tautological pass: if PACKAGE_DIR glob
    ever silently returned nothing (e.g. a path typo), both diff sets would
    be empty and the test above would pass having compared nothing to
    nothing."""
    assert _package_module_names(), f"no modules found under {PACKAGE_DIR} -- PACKAGE_DIR is likely wrong"
