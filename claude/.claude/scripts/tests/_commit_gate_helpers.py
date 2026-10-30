"""Test helpers shared by test_transcript_commit_gate.py and by test_transcript_analysis.py's
cross-subcommand tables.
"""


def _gate_args(skill: str, *, by_permission_mode: bool = False, projects: str = "*", this_repo: bool = False,
               exclude_projects: str | None = None, branches: str | None = None):
    """Build a minimal argparse.Namespace for cmd_commit_gate."""
    return type("A", (), {
        "skill": skill,
        "by_permission_mode": by_permission_mode,
        "projects": projects,
        "this_repo": this_repo,
        "exclude_projects": exclude_projects,
        "branches": branches,
    })()
