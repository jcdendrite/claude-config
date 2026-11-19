"""feature-flags and terraform-conventions pins, and the agent and skill bodies that point at convention skills."""
from __future__ import annotations

import re

import pytest
from _skill_md_helpers import (
    _agent_body,
    _skill_body,
)

# pyproject.toml's pythonpath also puts claude/.claude/tests on the import
# path, where these shared test helpers live.
from helpers import (
    CLAUDE_DIR,
    REPO_ROOT,
    SKILLS_DIR,
)

# feature-flags is name-only, so these pointer sentences are its only
# reachability path. config-environments has two pointer sites, hence
# two entries below.
_FEATURE_FLAGS_POINTER_SITES = [
    (
        "claude-skills/skills/config-environments/SKILL.md",
        "is the `feature-flags` skill's call, not this one's",
    ),
    (
        "claude-skills/skills/config-environments/SKILL.md",
        "Toggle placement is the `feature-flags` skill's call",
    ),
    (
        "claude-skills/skills/code-review/SKILL.md",
        "For where the toggle should live in the first place, invoke the `feature-flags` skill",
    ),
    (
        "claude/.claude/agents/staff-product-engineer.md",
        "For where the toggle should live and whether it needs a platform, Read `~/.claude/skills/feature-flags/SKILL.md`",
    ),
    (
        "claude/.claude/agents/staff-backend-engineer.md",
        "For where the toggle should live and whether it needs a platform, Read `~/.claude/skills/feature-flags/SKILL.md`",
    ),
    (
        "claude/.claude/rules/terraform-conventions.md",
        "is the `feature-flags` skill's call — this rule doesn't decide it",
    ),
]


@pytest.mark.parametrize("relative_path, pointer_phrase", _FEATURE_FLAGS_POINTER_SITES)
def test_feature_flags_pointer_sites_still_name_the_skill(relative_path, pointer_phrase):
    """Pins each site's exact pointer phrase (not a "feature-flags"
    substring) so a meaning-reversing rewrite that keeps the word is
    still caught. Whitespace-normalized so a benign paragraph rewrap
    (e.g. terraform-conventions.md's hard-wrapped prose) doesn't fail
    this test."""
    content = re.sub(r"\s+", " ", (REPO_ROOT / relative_path).read_text())
    normalized_phrase = re.sub(r"\s+", " ", pointer_phrase)
    assert normalized_phrase in content, (
        f"{relative_path} no longer contains {pointer_phrase!r} — "
        "this site is one of feature-flags' six hand-off pointer sites, "
        "its sole reachability mechanism since the skill is name-only"
    )


# Matching is whitespace-normalized, so a benign re-wrap passes but a
# word change doesn't. Each param pins a bullet's full text, not just its
# first sentence, so a carve-out or remediation clause added later can't
# be silently dropped either. This guards all seven parallel write-path
# invariants in feature-flags/SKILL.md.
_FEATURE_FLAGS_WRITE_PATH_INVARIANT_SENTENCES = (
    pytest.param(
        "The writer-side mutator must itself enforce an authorization check "
        "before completing the mutation. That check must be scoped to a "
        "narrower principal set than the datastore's general write access. "
        "An audit-log entry records that a change happened. It does not "
        "substitute for preventing an unauthorized one.",
        id="mutator-authorization-check",
    ),
    pytest.param(
        "No code path may complete the mutation without a corresponding "
        "audit-log entry existing. A same-transaction commit and a "
        "synchronous write to an isolated tamper-evident store both satisfy "
        "this. A fire-and-forget log call that can silently fail does not.",
        id="audit-log-mandatory",
    ),
    pytest.param(
        "The log entry itself must be append-only and tamper-evident — the "
        "same bar `ciso-reviewer` applies to any privileged-action log.",
        id="tamper-evident",
    ),
    pytest.param(
        "Every application-layer writer must call through one writer-side "
        "mutator, symmetric with the read side's single accessor, rather "
        "than reimplementing authorization and logging at each call site.",
        id="single-writer-mutator",
    ),
    pytest.param(
        "Direct datastore access (a console update, an ad hoc fix, or a "
        "broad table-write role) bypasses any application-layer check "
        "entirely. It needs its own datastore-level control: a "
        "column-level grant, a row-level policy, or a restriction on who "
        "holds the table's write role at all. That control must be at "
        "least as narrow as the mutator's own principal set from the "
        "first bullet. A broader datastore-level grant reopens the door "
        "the first bullet just closed.",
        id="datastore-level-control",
    ),
    pytest.param(
        "A separate principal with write access to the cache tier "
        "backing the toggle's read path can flip the effective state "
        "without going through the mutator, the audit log, or the "
        "datastore-level control. The cache-population and "
        "cache-invalidation path must be gated by the same "
        "authorization check as the mutator itself.",
        id="cache-write-bypass",
    ),
    pytest.param(
        "A toggle that disables a security control globally must use a "
        "stronger bar than a single-subject grant. The two options below "
        "are not interchangeable: - **Two-person approval** — when the "
        "threat is a single compromised or malicious principal acting "
        "alone. - **Time-boxed override** — when the threat is only how "
        "long an authorized unilateral action stays in effect; it does "
        "not by itself require collusion to misuse.",
        id="global-disable-stronger-bar",
    ),
)


@pytest.mark.parametrize("sentence", _FEATURE_FLAGS_WRITE_PATH_INVARIANT_SENTENCES)
def test_feature_flags_write_path_invariant_pinned_verbatim(sentence):
    """Each of the seven parallel write-path security invariants in
    feature-flags/SKILL.md must survive verbatim (whitespace-normalized) --
    see _FEATURE_FLAGS_WRITE_PATH_INVARIANT_SENTENCES."""
    content = re.sub(
        r"\s+", " ", (SKILLS_DIR / "feature-flags" / "SKILL.md").read_text()
    )
    assert sentence in content, (
        f"feature-flags/SKILL.md is missing a pinned write-path security "
        f"invariant verbatim:\n{sentence!r}"
    )


# Matching is whitespace-normalized, so a benign re-wrap passes but a word
# change doesn't. Guards terraform-conventions.md's major-version-signal
# paragraph against a silent revert to treating purely-additive widening
# as always a major bump, or against dropping the Enumerable/Open
# distinction.
_TERRAFORM_CONVENTIONS_MAJOR_VERSION_SIGNAL_SENTENCES = (
    pytest.param(
        "Narrowing the legal-value set is unconditionally at least as "
        "strong a major-version signal as a default change: a "
        "previously-valid caller value can now fail validation, with no "
        "opt-in.",
        id="narrowing-is-major-version-signal",
    ),
    pytest.param(
        "Purely additive widening is backward-compatible by semver's own "
        "definition: every previously-accepted value still validates. "
        "Treat it as a same-version (MINOR) change for an Enumerable "
        "module. Treat it as a major-version change only for an Open "
        "module, where you cannot rule out a caller relying on the gate's "
        "prior rejection of the now-legal value.",
        id="widening-enumerable-vs-open",
    ),
)


@pytest.mark.parametrize(
    "sentence", _TERRAFORM_CONVENTIONS_MAJOR_VERSION_SIGNAL_SENTENCES
)
def test_terraform_conventions_major_version_signal_pinned_verbatim(sentence):
    """Both halves of terraform-conventions.md's major-version-signal
    paragraph must survive verbatim (whitespace-normalized) -- see
    _TERRAFORM_CONVENTIONS_MAJOR_VERSION_SIGNAL_SENTENCES."""
    content = re.sub(
        r"\s+",
        " ",
        (CLAUDE_DIR / "rules" / "terraform-conventions.md").read_text(),
    )
    assert sentence in content, (
        f"terraform-conventions.md is missing a pinned major-version-signal "
        f"sentence verbatim:\n{sentence!r}"
    )


class TestConventionSkillWiring:
    """Assert that name-dispatched skills are explicitly wired into consumers.

    test-conventions, sql-query-conventions, and pr-description rely on
    explicit Read/invoke pointers rather than description-based auto-trigger
    (which never fires). These tests prevent silent regression of the wiring.
    """

    def _agent_body(self, name: str) -> str:
        return _agent_body(name)

    def _skill_body(self, name: str) -> str:
        return _skill_body(name)

    def test_code_writer_references_test_conventions(self):
        """code-writer must tell the writer to consult test-conventions for test code."""
        assert "test-conventions/SKILL.md" in self._agent_body("code-writer")

    def test_code_writer_references_sql_query_conventions(self):
        """code-writer must tell the writer to consult sql-query-conventions for read-path SQL."""
        assert "sql-query-conventions/SKILL.md" in self._agent_body("code-writer")

    def test_code_writer_self_review_cross_checks_porcelain(self):
        """code-writer's self-review must cross-check tracked paths against `git status --porcelain`."""
        assert "git status --porcelain" in self._agent_body("code-writer")

    def test_code_writer_self_review_avoids_git_diff_head(self):
        """code-writer's self-review must warn against `git diff HEAD`, which pulls a prior round's staged work into the diff."""
        body = self._agent_body("code-writer")
        lines_with_git_diff_head = [
            line for line in body.splitlines() if "git diff HEAD" in line
        ]
        assert lines_with_git_diff_head, (
            "code-writer body no longer mentions `git diff HEAD` at all."
        )
        assert any("never" in line.lower() for line in lines_with_git_diff_head), (
            "code-writer mentions `git diff HEAD` but no line prohibits it."
        )

    def test_staff_sdet_reads_test_conventions_body(self):
        """staff-sdet must Read test-conventions/SKILL.md to ground its §N citations."""
        body = self._agent_body("staff-sdet")
        assert "test-conventions/SKILL.md" in body
        assert "test-conventions §4" not in body  # §4 is Test isolation; §5 is naming/regression-test intent

    def test_staff_backend_reads_sql_query_conventions_body(self):
        """staff-backend-engineer must Read sql-query-conventions/SKILL.md."""
        body = self._agent_body("staff-backend-engineer")
        assert "sql-query-conventions/SKILL.md" in body

    def test_code_review_invokes_test_conventions(self):
        """code-review must have an actionable invoke pointer to test-conventions."""
        assert "invoke `test-conventions`" in self._skill_body("code-review")

    def test_code_review_invokes_sql_query_conventions(self):
        """code-review must have an actionable invoke pointer to sql-query-conventions."""
        assert "invoke the `sql-query-conventions`" in self._skill_body("code-review")

    def test_ready_for_review_invokes_pr_description(self):
        """ready-for-review step 5 must have an actionable invoke pointer to pr-description."""
        assert "Invoke the `pr-description`" in self._skill_body("ready-for-review")

    def test_handoff_runs_pr_description(self):
        """handoff's pre-write checklist must have an actionable run pointer to pr-description."""
        assert "run the `pr-description`" in self._skill_body("handoff")

    def test_ready_for_review_continues_past_pr_description(self):
        """ready-for-review step 5 must tell the model pr-description's ending is not this gate's stopping point."""
        # Normalized so a future hard-wrap of this paragraph doesn't break the pin.
        body = " ".join(self._skill_body("ready-for-review").split())
        assert "continue to the next step in the same turn" in body

    def test_handoff_continues_past_pr_description(self):
        """handoff's pre-write checklist must tell the model pr-description's ending is not this checklist's stopping point."""
        # Normalized so a future hard-wrap of this paragraph doesn't break the pin.
        body = " ".join(self._skill_body("handoff").split())
        assert "write the handoff file in the same turn" in body
