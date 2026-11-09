"""Parsers over cost's rendered report, shared by test_transcript_cost.py and test_transcript_scope_symlinks.py."""
import re


def _extract_account_totals(out: str) -> dict[int, float]:
    """Map account ordinal -> that account's own token-class 'total' row
    dollar figure, by splitting cost's '## Cost by account' section on its
    own '### account-N' sub-headers."""
    totals: dict[int, float] = {}
    for block in out.split("### account-")[1:]:
        ordinal_str, _, rest = block.partition("\n")
        match = re.search(r"^total\s+([\d,]+\.\d\d)\s*$", rest, re.MULTILINE)
        assert match is not None, f"no total row found for account-{ordinal_str.strip()}"
        totals[int(ordinal_str.strip())] = float(match.group(1).replace(",", ""))
    return totals
