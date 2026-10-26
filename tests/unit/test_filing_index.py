"""E7a-S1: a filing's index page gives each document's type and the Eastern "Accepted" time,
read as UTC (D-057); the press release exhibits are tried in §9.9's order."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from marketdata.normalize.filing_index import (
    FilingIndex,
    IndexDocument,
    earnings_exhibits,
    parse_filing_index,
)
from marketdata.normalize.security_master import SecFileError

INDEX_PAGES = Path(__file__).resolve().parents[1] / "fixtures" / "sec" / "filing_index"


def _page(accession: str) -> FilingIndex:
    return parse_filing_index((INDEX_PAGES / f"{accession}-index.htm").read_bytes())


@pytest.mark.parametrize(
    ("accession", "form", "accepted"),
    [
        # The UTC times E3 checked the submissions listings against (D-057): EDT is UTC-4.
        ("0000320193-26-000018", "8-K", datetime(2026, 7, 30, 20, 30, 28, tzinfo=UTC)),
        ("0000320193-25-000079", "10-K", datetime(2025, 10, 31, 10, 1, 26, tzinfo=UTC)),
        ("0001652044-26-000071", "10-Q", datetime(2026, 7, 23, 1, 15, 54, tzinfo=UTC)),
        ("0000027419-26-000016", "10-K", datetime(2026, 3, 11, 20, 42, 0, tzinfo=UTC)),
    ],
)
def test_the_accepted_time_is_eastern_and_read_as_utc(
    accession: str, form: str, accepted: datetime
) -> None:
    index = _page(accession)

    assert (index.accession_no, index.form, index.accepted_at) == (accession, form, accepted)


def test_an_earnings_8k_lists_its_items_and_documents() -> None:
    index = _page("0000320193-26-000018")

    assert index.filed_at == date(2026, 7, 30)
    assert index.period_of_report == date(2026, 7, 30)
    assert index.items == ("2.02", "9.01")
    assert index.filer_ciks == (320193,)
    assert index.documents[:2] == (
        IndexDocument(1, "8-K", "aapl-20260730.htm", "8-K", 38350),
        IndexDocument(2, "EX-99.1", "a8-kex991q3202606272026.htm", "EX-99.1", 173484),
    )
    assert [document.name for document in earnings_exhibits(index)] == [
        "a8-kex991q3202606272026.htm"
    ]


def test_a_10k_has_no_earnings_exhibit() -> None:
    assert earnings_exhibits(_page("0000027419-26-000016")) == []


def test_exhibits_are_tried_99_1_then_99_then_the_rest_in_order() -> None:
    documents = tuple(
        IndexDocument(seq, doc_type, name, doc_type, None)
        for seq, doc_type, name in [
            (1, "8-K", "form8k.htm"),
            (2, "EX-99.3", "ex993.htm"),
            (3, "EX-99.2", "ex992.htm"),
            (4, "EX-99", "ex99.htm"),
            (5, "EX-99.01", "ex9901.htm"),
            (6, "EX-99.4", "slides.pdf"),
            (7, "EX-10.1", "ex101.htm"),
            (8, "EX-99.5", "ex995.txt"),
        ]
    )
    index = FilingIndex(
        "0000000001-26-000001",
        "8-K",
        date(2026, 1, 1),
        datetime(2026, 1, 1, tzinfo=UTC),
        None,
        ("2.02",),
        documents,
        (1,),
    )

    names = [document.name for document in earnings_exhibits(index)]

    assert names == ["ex9901.htm", "ex99.htm", "ex992.htm", "ex993.htm", "ex995.txt"]


def test_a_page_without_an_accepted_time_is_refused() -> None:
    page = (INDEX_PAGES / "0000320193-26-000018-index.htm").read_bytes()

    with pytest.raises(SecFileError, match="no Filing Date or Accepted time"):
        parse_filing_index(page.replace(b">Accepted<", b">Received<"))
