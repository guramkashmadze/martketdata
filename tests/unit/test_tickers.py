"""E2-S1/S3: ticker spelling (PLAN.md §9.2) and the security type a suffix shows (D-051)."""

import pytest

from marketdata.normalize.tickers import TickerError, canonical_ticker, ticker_type


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("BRK-B", "BRK-B"),
        ("brk.b", "BRK-B"),
        ("BRK/B", "BRK-B"),
        ("  aapl ", "AAPL"),
        ("jpm-pc", "JPM-PC"),
        ("NE-WTA", "NE-WTA"),
        # Fund-list spellings seen on 2026-10-25.
        ("elfnx", "ELFNX"),
        ("(NWAKX)", "NWAKX"),
        ("QVG2Q", "QVG2Q"),
    ],
)
def test_canonical_ticker(text: str, expected: str) -> None:
    assert canonical_ticker(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "NONE.",  # EBR Systems' entry in the exchange list
        "BRK..B",
        "-AAPL",
        "BRK B",
        "AAPL;",
        "ÄPPL",
        "ABCDEFGHIJKLM",
    ],
)
def test_text_that_is_not_a_ticker_is_refused(text: str) -> None:
    with pytest.raises(TickerError, match="for example BRK-B"):
        canonical_ticker(text)


@pytest.mark.parametrize(
    ("ticker", "security_type", "share_class"),
    [
        ("AAPL", "common", None),
        ("BRK-A", "common", "A"),
        ("BF-B", "common", "B"),
        ("MKC-V", "common", "V"),
        ("JPM-PC", "preferred", None),
        ("ETI-P", "preferred", None),
        ("VAL-WT", "other", None),
        ("NE-WTA", "other", None),
        ("KCAC-UN", "other", None),
        ("CELG-RI", "other", None),
        ("MSGK-WI", "other", None),
        ("ABC-XY", "other", None),
    ],
)
def test_suffix_shows_the_security_type(
    ticker: str, security_type: str, share_class: str | None
) -> None:
    assert ticker_type(ticker) == (security_type, share_class)


def test_a_plain_ticker_is_taken_as_common_stock() -> None:
    """Known limit: GOOGM is a preferred and AMJB an ETN; data/security_overrides.csv fixes
    those (D-051)."""
    assert ticker_type("GOOGM") == ("common", None)
    assert ticker_type("AMJB") == ("common", None)
