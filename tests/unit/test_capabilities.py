"""E2-S5: which data kinds apply to a company, with the reason (PLAN.md §9.1, D-018). In the MVP,
earnings and fundamentals apply to domestic filers in the universe only; insider trades (E8) to
domestic filers and foreign private issuers in the universe; 5% stakes (E16) to every universe
company."""

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
        ("insiders", True),
        ("stakes", True),
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


def test_a_foreign_issuer_in_the_universe_gets_insiders_only() -> None:
    """Section 16 reports reached foreign private issuers' directors and officers in March 2026:
    TSM's first Form 4 on its listing is of 2026-03-24 (EDGE_CASES.md, E8)."""
    capabilities = capabilities_for(company("foreign_private_issuer", in_universe=True))

    assert [(c.kind, c.applies) for c in capabilities.kinds] == [
        ("earnings", False),
        ("fundamentals", False),
        ("insiders", True),
        ("stakes", True),
    ]
    assert capabilities.of("insiders").reason == (
        "A foreign private issuer in the covered universe: the Form 4 filings of its directors "
        "and officers apply, filed since March 2026."
    )


@pytest.mark.parametrize(
    ("filer_type", "in_universe", "reason"),
    [
        ("fund", True, "A fund: insider Form 4 filings don't apply."),
        ("fund", False, "A fund: insider Form 4 filings don't apply."),
        ("other", True, "Files no 10-K or 10-Q, so insider Form 4 filings aren't covered."),
        ("domestic", False, "Not in the covered universe."),
        ("foreign_private_issuer", False, "Not in the covered universe."),
        ("unknown", True, "Not classified yet: its SEC filings could not be loaded."),
    ],
)
def test_insiders_dont_apply_to_the_rest(
    filer_type: FilerType, in_universe: bool, reason: str
) -> None:
    insiders = capabilities_for(company(filer_type, in_universe)).of("insiders")

    assert (insiders.applies, insiders.reason) == (False, reason)


@pytest.mark.parametrize(
    ("filer_type", "in_universe", "applies", "reason"),
    [
        (
            "fund",
            True,
            True,
            "In the covered universe: the Schedule 13D and 13G filings about it apply, read from "
            "the SEC's structured format (from 2024).",
        ),
        ("foreign_private_issuer", True, True, None),
        ("other", True, True, None),
        ("domestic", False, False, "Not in the covered universe."),
        ("unknown", True, False, "Not classified yet: its SEC filings could not be loaded."),
    ],
)
def test_stakes_apply_to_every_universe_company(
    filer_type: FilerType, in_universe: bool, applies: bool, reason: str | None
) -> None:
    """E16: whoever owns more than 5% of a class files a 13D or 13G, whatever the company's
    class; only universe companies' listings are watched and parsed."""
    stakes = capabilities_for(company(filer_type, in_universe)).of("stakes")

    assert stakes.applies is applies
    if reason is not None:
        assert stakes.reason == reason
