"""E1-S3: the raw key layout."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from marketdata.sec import urls
from marketdata.sec.raw_store import api_response_key, fetched_at_name, filing_key, key_for_url

AAPL = 320193
ACCESSION = "0000320193-24-000123"
AT = datetime(2026, 10, 24, 20, 55, 1, 123456, tzinfo=UTC)
STAMP = "20261024T205501.123456Z"


def test_fetch_time_is_utc_without_colons() -> None:
    tbilisi = timezone(timedelta(hours=4))

    assert fetched_at_name(AT) == STAMP
    assert fetched_at_name(AT.astimezone(tbilisi)) == STAMP
    with pytest.raises(ValueError, match="timezone-aware"):
        fetched_at_name(datetime(2026, 10, 24))  # noqa: DTZ001


def test_filing_documents() -> None:
    assert filing_key(AAPL, "000032019324000123", "index.json") == (
        "raw/sec/320193/0000320193-24-000123/index.json"
    )
    assert filing_key(AAPL, ACCESSION, "xslF345X05/form4.xml") == (
        "raw/sec/320193/0000320193-24-000123/xslF345X05/form4.xml"
    )


def test_api_responses() -> None:
    assert api_response_key("submissions", AT, cik=AAPL) == (
        f"raw/sec/api/submissions/320193/{STAMP}.json"
    )
    assert api_response_key("company_tickers_exchange", AT) == (
        f"raw/sec/api/company_tickers_exchange/{STAMP}.json"
    )


@pytest.mark.parametrize(
    ("url", "key"),
    [
        (urls.submissions(AAPL), f"raw/sec/api/submissions/320193/{STAMP}.json"),
        (
            urls.submissions_page("CIK0000320193-submissions-001.json"),
            f"raw/sec/api/submissions/320193/001/{STAMP}.json",
        ),
        (urls.companyfacts(AAPL), f"raw/sec/api/companyfacts/320193/{STAMP}.json"),
        (
            urls.companyconcept(AAPL, "Revenues"),
            f"raw/sec/api/companyconcept/320193/us-gaap/Revenues/{STAMP}.json",
        ),
        (
            urls.frames("EarningsPerShareDiluted", "USD/shares", 2024, 2),
            "raw/sec/api/frames/us-gaap/EarningsPerShareDiluted/USD-per-shares/CY2024Q2/"
            f"{STAMP}.json",
        ),
        (urls.COMPANY_TICKERS_EXCHANGE, f"raw/sec/api/company_tickers_exchange/{STAMP}.json"),
        (urls.COMPANY_TICKERS_MF, f"raw/sec/api/company_tickers_mf/{STAMP}.json"),
        (
            urls.filing_index_json(AAPL, ACCESSION),
            "raw/sec/320193/0000320193-24-000123/index.json",
        ),
        (
            urls.filing_index_page(AAPL, ACCESSION),
            "raw/sec/320193/0000320193-24-000123/0000320193-24-000123-index.htm",
        ),
        (
            urls.filing_document(AAPL, ACCESSION, "a8-kex991.htm"),
            "raw/sec/320193/0000320193-24-000123/a8-kex991.htm",
        ),
    ],
)
def test_key_for_url(url: str, key: str) -> None:
    assert key_for_url(url, AT) == key


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        (urls.BULK_COMPANYFACTS, "streaming"),
        (urls.BULK_SUBMISSIONS, "streaming"),
        (urls.filing_folder(AAPL, ACCESSION), "index.json"),
        ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany", "pass the key"),
    ],
)
def test_urls_without_a_key_layout_are_refused(url: str, reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        key_for_url(url, AT)
