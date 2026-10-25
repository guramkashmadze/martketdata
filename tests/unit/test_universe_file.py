"""E2-S1: the universe file and the security overrides file are checked line by line, and
universe tickers are matched to the companies that list them (PLAN.md §9.1)."""

from datetime import date

import pytest

from marketdata.normalize.universe import (
    ClosedTicker,
    DataFileError,
    UniverseEntry,
    match_universe,
    parse_security_overrides,
    parse_universe,
)

GOOD = """ticker,note
AAPL,September year end
brk.b,Class A and B on one CIK
GOOG,Class C
"GOOGL","December, several share classes"
"""


def test_universe_file_gives_canonical_tickers_with_notes_and_lines() -> None:
    entries = parse_universe(GOOD)

    assert entries == [
        UniverseEntry(2, "AAPL", "September year end"),
        UniverseEntry(3, "BRK-B", "Class A and B on one CIK"),
        UniverseEntry(4, "GOOG", "Class C"),
        UniverseEntry(5, "GOOGL", "December, several share classes"),
    ]


def test_byte_order_mark_and_blank_lines_are_ignored() -> None:
    entries = parse_universe("﻿ticker,note\n\nAAPL,September year end\n\n")

    assert [entry.ticker for entry in entries] == ["AAPL"]


def test_every_problem_is_reported_with_its_line() -> None:
    text = (
        "ticker,note\n"
        "AAPL,September year end\n"
        "aapl,again\n"  # same ticker after canonical spelling
        "NONE.,not a ticker\n"
        "MSFT,\n"  # no note
        "TSLA,too,many\n"
    )

    with pytest.raises(DataFileError) as excinfo:
        parse_universe(text)

    assert excinfo.value.problems == (
        "line 3: AAPL is already listed on line 2",
        "line 4: 'NONE.' is not a ticker: expected letters and digits, with '.', '-' or '/' "
        "before a class suffix, for example BRK-B",
        "line 5: MSFT has no note; say why it is in the universe",
        "line 6: expected 2 fields (ticker,note), found 3; quote a value that has a comma",
    )
    assert str(excinfo.value).startswith("data/universe.csv:\n  - line 3:")


@pytest.mark.parametrize("text", ["", "symbol,note\nAAPL,x\n", "ticker\nAAPL\n"])
def test_wrong_header_stops_at_line_1(text: str) -> None:
    with pytest.raises(DataFileError, match="line 1: the header must be ticker,note"):
        parse_universe(text)


def test_a_universe_needs_at_least_one_ticker() -> None:
    with pytest.raises(DataFileError, match="lists no tickers"):
        parse_universe("ticker,note\n")


OVERRIDES = """ticker,security_type,source_url
GOOGM,preferred,https://www.sec.gov/Archives/edgar/data/1652044/000165204426000071/R1.htm
amjb,other,https://www.sec.gov/Archives/edgar/data/19617/000162828026054343/R1.htm
"""


def test_overrides_give_type_and_source() -> None:
    overrides = parse_security_overrides(OVERRIDES)

    assert [(o.line, o.ticker, o.security_type) for o in overrides] == [
        (2, "GOOGM", "preferred"),
        (3, "AMJB", "other"),
    ]
    assert overrides[1].source_url.endswith("/R1.htm")


def test_overrides_file_with_only_its_header_means_none() -> None:
    assert parse_security_overrides("ticker,security_type,source_url\n") == []


def test_override_problems_are_reported() -> None:
    text = (
        "ticker,security_type,source_url\n"
        "GOOGM,preferred stock,https://example.org/a\n"
        "AMJB,other,\n"
        "VYLD,other,https://example.org/b\n"
        "vyld,other,https://example.org/c\n"
    )

    with pytest.raises(DataFileError) as excinfo:
        parse_security_overrides(text)

    assert excinfo.value.problems == (
        "line 2: security_type 'preferred stock' for GOOGM must be one of common, preferred, "
        "fund, adr, other",
        "line 3: AMJB needs a source_url: the https link to the SEC page that shows its type",
        "line 5: VYLD is already listed on line 4",
    )


def entry(line: int, ticker: str) -> UniverseEntry:
    return UniverseEntry(line, ticker, "note")


def test_universe_tickers_match_current_listings_then_history() -> None:
    entries = [
        entry(2, "AAPL"),
        entry(3, "GOOGL"),
        entry(4, "GOOG"),
        entry(5, "OLD"),
        entry(6, "XYZ"),
    ]
    listed = {"AAPL": 320193, "GOOGL": 1652044, "GOOG": 1652044}
    closed = {"OLD": ClosedTicker(cik=34088, valid_to=date(2026, 7, 1))}

    match = match_universe(entries, listed, closed)

    assert [(e.ticker, cik) for e, cik in match.matched] == [
        ("AAPL", 320193),
        ("GOOGL", 1652044),
        ("GOOG", 1652044),
    ]
    assert [(e.ticker, c.cik) for e, c in match.closed] == [("OLD", 34088)]
    assert [e.ticker for e in match.unknown] == ["XYZ"]
    assert match.ciks == [320193, 1652044, 34088]
    assert match.shared == {1652044: ["GOOGL", "GOOG"]}
