"""E2-S5: which data kinds apply to a company, with the reason (PLAN.md §9.1, D-018). In the MVP,
earnings and fundamentals apply to domestic filers in the universe only."""

from datetime import UTC, datetime

import pytest

from marketdata.db.repositories.companies import Company
from marketdata.normalize.security_master import FilerType
from marketdata.service.companies import capabilities_for


def company(filer_type: FilerType, in_universe: bool) -> Company:
    return Company(
        cik=1,
        name="Example",
        sic_code=None,
        sic_description=None,
        fiscal_year_end=None,
        state_of_inc=None,
        filer_type=filer_type,
        in_universe=in_universe,
        updated_at=datetime(2026, 10, 25, tzinfo=UTC),
    )


def test_a_domestic_filer_in_the_universe_gets_earnings_and_fundamentals() -> None:
    capabilities = capabilities_for(company("domestic", in_universe=True))

    assert [(c.kind, c.applies) for c in capabilities.kinds] == [
        ("earnings", True),
        ("fundamentals", True),
    ]
    assert capabilities.of("fundamentals").reason == (
        "A domestic filer in the covered universe: its 10-K and 10-Q apply."
    )


@pytest.mark.parametrize(
    ("filer_type", "in_universe", "earnings", "fundamentals"),
    [
        (
            "fund",
            True,
            "A fund: earnings releases don't apply.",
            "A fund: fundamentals don't apply.",
        ),
        (
            "fund",
            False,
            "A fund: earnings releases don't apply.",
            "A fund: fundamentals don't apply.",
        ),
        (
            "foreign_private_issuer",
            True,
            "A foreign private issuer: its results come in 6-K reports, which are not covered yet.",
            "A foreign private issuer: its 20-F and 40-F annual reports are not covered yet.",
        ),
        (
            "other",
            True,
            "Files no 10-K or 10-Q, so earnings releases don't apply.",
            "Files no 10-K or 10-Q, so fundamentals don't apply.",
        ),
        ("domestic", False, "Not in the covered universe.", "Not in the covered universe."),
        ("unknown", False, "Not in the covered universe.", "Not in the covered universe."),
        (
            "unknown",
            True,
            "Not classified yet: its SEC filings could not be loaded.",
            "Not classified yet: its SEC filings could not be loaded.",
        ),
    ],
)
def test_everyone_else_gets_a_reason(
    filer_type: FilerType, in_universe: bool, earnings: str, fundamentals: str
) -> None:
    capabilities = capabilities_for(company(filer_type, in_universe))

    assert not capabilities.of("earnings").applies and not capabilities.of("fundamentals").applies
    assert (capabilities.of("earnings").reason, capabilities.of("fundamentals").reason) == (
        earnings,
        fundamentals,
    )
