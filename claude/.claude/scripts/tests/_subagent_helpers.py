"""Test helpers shared by the subagent family's test files (test_transcript_subagents.py,
test_transcript_subagent_mix*.py) and by test_transcript_analysis.py's cross-subcommand tables."""


def _sum_column_across_rows(out: str, *, header_contains: str, label: str, row_prefix: str) -> int:
    """Sum one single-token integer column across every row whose leading
    label starts with `row_prefix` (e.g. every multi-root "account-" row).

    _table_cols can't be reused directly here: it asserts exactly one
    matching data row, and a multi-root sum needs several. This still
    anchors the column position to the header row's own token index
    (`header.split().index(label)`) instead of a bare `line.split()[N]`
    index, so a column reorder fails with a clear ValueError ("label not in
    list") instead of silently summing the wrong column.
    """
    lines = out.splitlines()
    headers = [ln for ln in lines if header_contains in ln]
    assert len(headers) == 1, f"header match not unique for {header_contains!r}: {len(headers)}"
    header_idx = lines.index(headers[0])
    col_idx = headers[0].split().index(label)
    total = 0
    matched_any = False
    for ln in lines[header_idx + 1:]:
        if ln == "":
            break
        if not ln.startswith(row_prefix):
            continue
        matched_any = True
        total += int(ln.split()[col_idx])
    assert matched_any, f"no rows starting with {row_prefix!r} found under header {header_contains!r}"
    return total


def _subagent_mix_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    branches: str | None = None,
    per_session: bool = False,
    since: str | None = None,
    since_date: str | None = None,
    until_date: str | None = None,
    reprice_as: str | None = None,
    extra_config_dirs: list[str] | None = None,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "branches": branches,
        "per_session": per_session,
        "since": since,
        "since_date": since_date,
        "until_date": until_date,
        "reprice_as": reprice_as,
        "extra_config_dirs": extra_config_dirs,
    })()


def _subagents_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    branches: str | None = None,
    since: str | None = None,
    extra_config_dirs: list[str] | None = None,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "branches": branches,
        "since": since,
        "extra_config_dirs": extra_config_dirs,
    })()
