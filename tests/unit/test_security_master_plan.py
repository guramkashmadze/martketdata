"""E2-S2: what a load writes to companies and securities, decided from stored rows and the SEC
files, so that the same input writes nothing (PLAN.md §9.1-§9.2, D-052, D-053)."""

from dataclasses import dataclass
from datetime import date

from marketdata.normalize.security_master import (
    Classification,
    CompanyDetails,
    CompanyRow,
    FilerType,
    ListedCompany,
    Listing,
    UniverseCompany,
    diff_securities,
    plan_companies,
)
from marketdata.normalize.tickers import SecurityType


@dataclass(frozen=True)
class Stored:
    cik: int
    name: str | None
    sic_code: str | None
    sic_description: str | None
    fiscal_year_end: str | None
    state_of_inc: str | None
    filer_type: FilerType
    in_universe: bool


TARGET = CompanyDetails(27419, "TARGET CORP", "5331", "Retail-Variety Stores", "0201", "MN", ())


def member(details: CompanyDetails | None, filer_type: FilerType = "domestic") -> UniverseCompany:
    return UniverseCompany(27419, details, Classification(filer_type, "10-Q filed 2026-08-28"))


def stored_target(**changes: object) -> Stored:
    values: dict[str, object] = {
        "cik": 27419,
        "name": "TARGET CORP",
        "sic_code": "5331",
        "sic_description": "Retail-Variety Stores",
        "fiscal_year_end": "0201",
        "state_of_inc": "MN",
        "filer_type": "domestic",
        "in_universe": True,
    } | changes
    return Stored(**values)


def test_new_companies_come_from_the_lists() -> None:
    listed = {
        27419: ListedCompany(27419, "TARGET CORP", in_fund_list=False),
        36405: ListedCompany(36405, None, in_fund_list=True),
    }

    added, changed = plan_companies({}, listed, {})

    assert changed == []
    assert added == [
        CompanyRow(27419, "TARGET CORP", None, None, None, None, "unknown", False),
        CompanyRow(
            36405, None, None, None, None, None, "fund", False
        ),  # the fund list has no names
    ]


def test_universe_companies_take_details_and_class_from_submissions() -> None:
    listed = {27419: ListedCompany(27419, "TARGET CORP", in_fund_list=False)}

    added, _ = plan_companies({}, listed, {27419: member(TARGET)})

    assert added == [
        CompanyRow(
            27419, "TARGET CORP", "5331", "Retail-Variety Stores", "0201", "MN", "domestic", True
        )
    ]


def test_the_same_input_writes_nothing() -> None:
    listed = {27419: ListedCompany(27419, "TARGET CORP", in_fund_list=False)}

    assert plan_companies({27419: stored_target()}, listed, {27419: member(TARGET)}) == ([], [])


def test_failed_submissions_keep_what_is_stored() -> None:
    listed = {27419: ListedCompany(27419, "TARGET CORP (LIST)", in_fund_list=False)}
    unknown = member(None, "unknown")

    assert plan_companies({27419: stored_target()}, listed, {27419: unknown}) == ([], [])


def test_a_company_leaving_the_universe_keeps_its_details() -> None:
    listed = {27419: ListedCompany(27419, "TARGET CORP", in_fund_list=False)}

    _, changed = plan_companies({27419: stored_target()}, listed, {})

    assert changed == [
        CompanyRow(
            27419, "TARGET CORP", "5331", "Retail-Variety Stores", "0201", "MN", "domestic", False
        )
    ]


def test_outside_the_universe_the_exchange_list_names_the_company() -> None:
    listed = {27419: ListedCompany(27419, "Target Corp", in_fund_list=False)}
    stored = {27419: stored_target(in_universe=False)}

    _, changed = plan_companies(stored, listed, {})

    assert [row.name for row in changed] == ["Target Corp"]


def test_a_company_no_list_has_is_kept_outside_the_universe() -> None:
    _, changed = plan_companies({27419: stored_target()}, {}, {})

    assert [(row.cik, row.in_universe, row.name) for row in changed] == [
        (27419, False, "TARGET CORP")
    ]


@dataclass(frozen=True)
class Row:
    security_id: int
    cik: int
    ticker: str
    security_type: SecurityType
    exchange: str | None
    share_class: str | None
    valid_from: date


def listing(ticker: str, cik: int, exchange: str | None = "NYSE") -> Listing:
    return Listing(ticker, cik, "common", None, exchange, "exchange")


def test_securities_diff_opens_updates_and_keeps() -> None:
    current = [
        Row(1, 27419, "TGT", "common", None, None, date(2026, 10, 24)),
        Row(2, 320193, "AAPL", "common", "Nasdaq", None, date(2026, 10, 24)),
    ]
    snapshot = {
        "TGT": listing("TGT", 27419),  # exchange now known
        "AAPL": listing("AAPL", 320193, "Nasdaq"),
        "MSFT": listing("MSFT", 789019, "Nasdaq"),
    }

    changes = diff_securities(current, snapshot)

    assert changes.opened == (snapshot["MSFT"],)
    assert [(u.security_id, u.listing.exchange) for u in changes.updated] == [(1, "NYSE")]
    assert changes.unchanged == 1
    assert changes.stale == ()


def test_rows_the_snapshot_lists_elsewhere_or_not_at_all_are_stale() -> None:
    moved = Row(1, 34088, "XOM", "common", "NYSE", None, date(2026, 6, 30))
    gone = Row(2, 1, "GONE", "common", "NYSE", None, date(2026, 6, 30))

    changes = diff_securities([moved, gone], {"XOM": listing("XOM", 2115436)})

    assert changes.opened == () and changes.updated == ()
    assert changes.stale == (moved, gone)
