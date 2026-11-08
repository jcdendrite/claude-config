"""Test helpers shared by test_transcript_cache_efficiency.py and by test_transcript_cache_rebuild.py's
cross-instrument reconciliation test.
"""


def _cache_efficiency_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    no_redact: bool = False,
    extra_config_dirs: list[str] | None = None,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "no_redact": no_redact,
        "extra_config_dirs": extra_config_dirs,
    })()
