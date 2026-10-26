"""Sanity checks on extracted values (E7a-S4, PLAN.md §9.9 check 3).

Pure, and independent of the extractor (D-015).

- The change from the prior-year quarter is at most 1000% either way.
- Revenue is above zero.
- When the table shows the diluted share count, diluted EPS times shares is within 2% of net
  income, or half a cent per share, whichever is larger (D-076): printed EPS is rounded to the
  cent, which alone is 10% at $0.05. Net income available to common shareholders is used when
  the table prints it (JPMorgan's EPS is after preferred dividends). A miss fails EPS only.
"""

from collections.abc import Sequence
from decimal import Decimal

SANITY = "sanity"
MAX_CHANGE_PCT = Decimal(1000)
EPS_TOLERANCE = Decimal("0.02")
HALF_CENT = Decimal("0.005")


def change_problem(value: Decimal, prior: Decimal | None) -> str | None:
    """A change beyond ±1000%. None without a prior value, when it is zero, or when the sign
    changes: §9.9 gives no change percentage then."""
    if prior is None or prior == 0 or (value < 0 < prior) or (prior < 0 < value):
        return None
    change = (value - prior) / abs(prior) * 100
    if abs(change) > MAX_CHANGE_PCT:
        return f"the change from the prior-year quarter is {change:.0f}%, above {MAX_CHANGE_PCT}%"
    return None


def revenue_problem(value: Decimal) -> str | None:
    return None if value > 0 else f"revenue is {value}, not above zero"


def eps_problem(
    eps: Decimal,
    diluted_shares: Sequence[Decimal],
    net_income: Decimal | None,
    net_income_to_common: Decimal | None = None,
) -> str | None:
    """EPS × diluted shares against net income; None when it agrees or can't be checked.

    `diluted_shares` are the readings of the printed count: scaled as the table states, and as
    printed. Many tables say "in thousands, except per share data" and print share counts
    whole (BigBear.ai's 448,158,249); the count is never served, so agreeing under either
    reading passes."""
    basis = net_income_to_common if net_income_to_common is not None else net_income
    counts = [count for count in diluted_shares if count > 0]
    if not counts or basis is None:
        return None
    misses = []
    for count in counts:
        implied = eps * count
        tolerance = max(EPS_TOLERANCE * abs(basis), HALF_CENT * count)
        if abs(implied - basis) <= tolerance:
            return None
        misses.append(f"{eps} × {count} shares is {implied:.0f}")
    return f"diluted EPS {'; '.join(misses)}: none within 2% of net income {basis}"
