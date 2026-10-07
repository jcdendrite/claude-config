"""Test helpers shared by the audit-routing family's test files
(test_transcript_audit_routing*.py) and by test_transcript_analysis.py's
cross-subcommand tables."""
from __future__ import annotations


def _extract_corpus_class_tokens(out: str, cls: str) -> int:
    """Parse output-token value for a class from the corpus aggregate section."""
    # Locate the header to anchor the column index for "Output tokens".
    # "Output" is the unique leading word of the "Output tokens" column.
    header_line = next((ln for ln in out.splitlines() if "Class" in ln and "Output tokens" in ln), None)
    out_idx = header_line.split().index("Output") if header_line else 1
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith(cls):
            parts = stripped.split()
            if len(parts) > out_idx:
                return int(parts[out_idx].replace(",", ""))
    return 0


def _audit_routing_shape_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    since: str | None = None,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "since": since,
    })()


def _extract_shape_d1(out: str, bucket: str) -> tuple[int, int]:
    """Parse (turn_count, output_tokens) for a D1 bucket from audit-routing-shape output."""
    in_d1 = False
    header_line: str | None = None
    for line in out.splitlines():
        stripped = line.strip()
        if "D1" in stripped and "Files Read" in stripped:
            in_d1 = True
            continue
        if in_d1 and stripped.startswith("###"):
            break
        if in_d1 and "Bucket" in stripped and "Turns" in stripped:
            header_line = stripped
            continue
        if in_d1 and stripped.startswith(bucket):
            parts = stripped.split()
            # Anchor Turns and Output tokens columns by the header row.
            # "Output" is the unique leading word of the "Output tokens" column.
            turns_idx = header_line.split().index("Turns") if header_line else 1
            out_idx = header_line.split().index("Output") if header_line else turns_idx + 1
            if len(parts) > out_idx:
                return int(parts[turns_idx].replace(",", "")), int(parts[out_idx].replace(",", ""))
    return 0, 0


def _audit_routing_samples_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    since: str | None = None,
    sample: int = 100,
    seed: int | None = 42,
    output_format: str = "json",
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "since": since,
        "sample": sample,
        "seed": seed,
        "output_format": output_format,
    })()
