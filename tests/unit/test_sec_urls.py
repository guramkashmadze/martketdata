"""E1-S4: SEC URL builders, one test per PLAN.md §8 endpoint row, and the parser."""

import pytest

from marketdata.sec import urls
from marketdata.sec.urls import SecUrl

AAPL = 320193
ACCESSION = "0000320193-24-000123"


# --- CIK padding and accession numbers ---


@pytest.mark.parametrize(
    ("cik", "padded"), [(1, "0000000001"), (320193, "0000320193"), (9_999_999_999, "9999999999")]
)
def test_cik_is_zero_padded_to_ten_digits(cik: int, padded: str) -> None:
    assert urls.cik10(cik) == padded


@pytest.mark.parametrize("cik", [0, -1, 10_000_000_000, True, "320193", 320193.0])
def test_bad_ciks_are_refused(cik: object) -> None:
    with pytest.raises(ValueError, match="CIK"):
        urls.cik10(cik)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value", ["0000320193-24-000123", "000032019324000123", " 0000320193-24-000123 "]
)
def test_accession_numbers_are_stored_with_dashes(value: str) -> None:
    assert urls.accession_no(value) == ACCESSION
    assert urls.accession_nodash(value) == "000032019324000123"


@pytest.mark.parametrize(
    "value", ["0000320193-24-00012", "0000320193_24_000123", "320193-24-000123", "", "abc"]
)
def test_bad_accession_numbers_are_refused(value: str) -> None:
    with pytest.raises(ValueError, match="accession number"):
        urls.accession_no(value)


# --- One test per §8 endpoint row ---


def test_ticker_to_cik_list() -> None:
    assert (
        urls.COMPANY_TICKERS_EXCHANGE == "https://www.sec.gov/files/company_tickers_exchange.json"
    )


def test_fund_and_etf_tickers() -> None:
    assert urls.COMPANY_TICKERS_MF == "https://www.sec.gov/files/company_tickers_mf.json"


def test_filing_history() -> None:
    assert urls.submissions(AAPL) == "https://data.sec.gov/submissions/CIK0000320193.json"
    assert (
        urls.submissions_page("CIK0000320193-submissions-001.json")
        == "https://data.sec.gov/submissions/CIK0000320193-submissions-001.json"
    )
    with pytest.raises(ValueError):
        urls.submissions_page("../CIK0000320193.json")


def test_all_xbrl_facts_of_a_company() -> None:
    assert (
        urls.companyfacts(AAPL) == "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"
    )


def test_one_concept() -> None:
    assert urls.companyconcept(AAPL, "AccountsPayableCurrent") == (
        "https://data.sec.gov/api/xbrl/companyconcept/CIK0000320193/us-gaap/"
        "AccountsPayableCurrent.json"
    )
    assert urls.companyconcept(AAPL, "EntityPublicFloat", taxonomy="dei").endswith(
        "/CIK0000320193/dei/EntityPublicFloat.json"
    )


def test_one_concept_across_companies() -> None:
    base = "https://data.sec.gov/api/xbrl/frames/us-gaap"
    assert urls.frames("Revenues", "USD", 2024, 1) == f"{base}/Revenues/USD/CY2024Q1.json"
    assert (
        urls.frames("Assets", "USD", 2024, 4, instant=True) == f"{base}/Assets/USD/CY2024Q4I.json"
    )
    assert urls.frames("Revenues", "USD", 2023) == f"{base}/Revenues/USD/CY2023.json"
    # Frames URLs spell the unit USD/shares as USD-per-shares.
    assert (
        urls.frames("EarningsPerShareDiluted", "USD/shares", 2024, 2)
        == f"{base}/EarningsPerShareDiluted/USD-per-shares/CY2024Q2.json"
    )


@pytest.mark.parametrize(
    ("year", "quarter", "instant"),
    [(2024, 5, False), (2024, 0, False), (2024, None, True), (24, 1, False)],
)
def test_bad_frame_periods_are_refused(year: int, quarter: int | None, instant: bool) -> None:
    with pytest.raises(ValueError):
        urls.frame_period(year, quarter, instant=instant)


def test_bulk_facts_and_submissions() -> None:
    assert (
        urls.BULK_COMPANYFACTS
        == "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"
    )
    assert (
        urls.BULK_SUBMISSIONS
        == "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip"
    )


def test_filing_folder_and_its_index_json() -> None:
    folder = "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/"
    assert urls.filing_folder(AAPL, ACCESSION) == folder
    assert urls.filing_folder(AAPL, "000032019324000123") == folder
    assert urls.filing_index_json(AAPL, ACCESSION) == folder + "index.json"


def test_filing_index_page() -> None:
    assert urls.filing_index_page(AAPL, "000032019324000123") == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/"
        "0000320193-24-000123-index.htm"
    )


def test_filing_documents() -> None:
    folder = "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/"
    assert urls.filing_document(AAPL, ACCESSION, "a8-kex991.htm") == folder + "a8-kex991.htm"
    assert (
        urls.filing_document(AAPL, ACCESSION, "xslF345X05/form4.xml")
        == folder + "xslF345X05/form4.xml"
    )


@pytest.mark.parametrize(
    "file", ["../index.json", "/etc/passwd", "a//b.htm", "", "a\\b.htm", "a b.htm"]
)
def test_document_paths_stay_inside_the_folder(file: str) -> None:
    with pytest.raises(ValueError, match="relative path|name"):
        urls.filing_document(AAPL, ACCESSION, file)


def test_insider_13f_and_nport_data_sets() -> None:
    assert urls.INSIDER_DATA_SETS == "https://www.sec.gov/dera/data/form-345"
    assert urls.F13_DATA_SETS == "https://www.sec.gov/dera/data/form-13f"
    assert (
        urls.NPORT_DATA_SETS
        == "https://www.sec.gov/data-research/sec-markets-data/form-n-port-data-sets"
    )
    # A ZIP link as the insider page listed it on 2026-10-24.
    link = "/files/datastandardsinnovation/data/insider-transactions-data-sets/2026q3_form345.zip"
    assert urls.www_path(link) == "https://www.sec.gov" + link


def test_openfigi_is_listed_but_not_an_sec_url() -> None:
    assert urls.OPENFIGI_MAPPING == "https://api.openfigi.com/v3/mapping"
    assert urls.parse(urls.OPENFIGI_MAPPING) is None


# --- Parsing URLs back ---


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (urls.COMPANY_TICKERS_EXCHANGE, SecUrl("company_tickers_exchange")),
        (urls.COMPANY_TICKERS_MF, SecUrl("company_tickers_mf")),
        (urls.BULK_COMPANYFACTS, SecUrl("bulk_companyfacts")),
        (urls.BULK_SUBMISSIONS, SecUrl("bulk_submissions")),
        (urls.submissions(AAPL), SecUrl("submissions", cik=AAPL)),
        (
            urls.submissions_page("CIK0000320193-submissions-001.json"),
            SecUrl("submissions", cik=AAPL, parts=("001",)),
        ),
        (urls.companyfacts(AAPL), SecUrl("companyfacts", cik=AAPL)),
        (
            urls.companyconcept(AAPL, "Revenues"),
            SecUrl("companyconcept", cik=AAPL, parts=("us-gaap", "Revenues")),
        ),
        (
            urls.frames("EarningsPerShareDiluted", "USD/shares", 2024, 2),
            SecUrl(
                "frames", parts=("us-gaap", "EarningsPerShareDiluted", "USD-per-shares", "CY2024Q2")
            ),
        ),
        (
            urls.filing_index_page(AAPL, ACCESSION),
            SecUrl("filing", cik=AAPL, accession_no=ACCESSION, file=f"{ACCESSION}-index.htm"),
        ),
        (
            urls.filing_document(AAPL, ACCESSION, "xslF345X05/form4.xml"),
            SecUrl("filing", cik=AAPL, accession_no=ACCESSION, file="xslF345X05/form4.xml"),
        ),
        (urls.filing_folder(AAPL, ACCESSION), SecUrl("filing", cik=AAPL, accession_no=ACCESSION)),
    ],
)
def test_parse_reverses_the_builders(url: str, expected: SecUrl) -> None:
    assert urls.parse(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany",
        "https://data.sec.gov/submissions/CIK320193.json",
        "https://data.sec.gov/submissions/CIK0000320193.json?x=1",
        "https://www.sec.gov/Archives/edgar/data/320193/0000320193-24-000123/x.htm",
        "https://example.com/files/company_tickers_exchange.json",
        "http://www.sec.gov/files/company_tickers_exchange.json",
    ],
)
def test_parse_returns_none_for_other_urls(url: str) -> None:
    assert urls.parse(url) is None
