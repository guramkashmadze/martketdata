"""Release value vs later 10-Q/10-K XBRL value (E4b-S6, D-028, PLAN.md §9.10).

Pure, and blind to how either value was found (D-015): it compares two numbers.

- `matched`: the release's value is within 0.5% of the filing's (per-share values: within
  $0.01);
- `mismatch`: further apart; the fact is flagged `release_vs_filing_mismatch`. A mismatch
  never changes the release's value: it is shown, investigated and logged;
- `not_comparable`: one side has no value (a slot that failed or doesn't apply, a quarter the
  filing gives no value for);
- `pending`: the filing for the quarter isn't loaded yet (decided by the caller).
"""

from decimal import Decimal
from typing import Literal

type Status = Literal["pending", "matched", "mismatch", "not_comparable"]

PENDING: Status = "pending"
MATCHED: Status = "matched"
MISMATCH: Status = "mismatch"
NOT_COMPARABLE: Status = "not_comparable"
RELEASE_VS_FILING_MISMATCH = "release_vs_filing_mismatch"

RELATIVE = Decimal("0.005")  # 0.5% of the filing's value
PER_SHARE = Decimal("0.01")  # dollars per share


def compare(unit: str, release: Decimal | None, filing: Decimal | None) -> Status:
    """The status of a release value against the filing's value for the same quarter."""
    if release is None or filing is None:
        return NOT_COMPARABLE
    difference = abs(release - filing)
    if unit == "USD/shares":
        return MATCHED if difference <= PER_SHARE else MISMATCH
    return MATCHED if difference <= RELATIVE * abs(filing) else MISMATCH


def flags_after(flags: tuple[str, ...] | list[str], status: Status) -> tuple[str, ...]:
    """A fact's flags with `release_vs_filing_mismatch` set when, and only when, it mismatches."""
    kept = tuple(flag for flag in flags if flag != RELEASE_VS_FILING_MISMATCH)
    return (*kept, RELEASE_VS_FILING_MISMATCH) if status == MISMATCH else kept
