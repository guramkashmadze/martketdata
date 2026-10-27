"""E3-S4: filings from submissions JSON, the acceptance-time check (D-057), the store plan and
§4's routing. The four accessions are checked against the SEC's own index pages."""

import json
import re
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from marketdata.normalize.filings import (
    EARNINGS_EXTRACT,
    FACTS_REFRESH,
    FORM4_PARSE,
    FilingRow,
    check_acceptance_times,
    parse_submissions,
    plan_filings,
    route,
)
from marketdata.normalize.security_master import SecFileError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "sec"
MANIFEST = json.loads((FIXTURES / "MANIFEST.json").read_text())
EASTERN_INDEX = [  # accession, the submissions fixture whose times are right
    ("0000320193-26-000018", "AAPL-20261025.json"),
    ("0000320193-25-000079", "AAPL-20261025.json"),
    ("0001652044-26-000071", "GOOGL.json"),
    ("0000027419-26-000016", "TGT.json"),
]


def _accepted_on_index_page(accession: str) -> datetime:
    from zoneinfo import ZoneInfo

    page = (FIXTURES / "filing_index" / f"{accession}-index.htm").read_text()
    match = re.search(r'infoHead">Accepted</div>\s*<div class="info">([^<]+)<', page)
    assert match is not None
    eastern = datetime.fromisoformat(match.group(1)).replace(tzinfo=ZoneInfo("America/New_York"))
    return eastern.astimezone(UTC)


def _parse(name: str) -> dict[str, FilingRow]:
    data = (FIXTURES / "submissions" / name).read_bytes()
    return {row.accession_no: row for row in parse_submissions(data).filings}


def _fetched(name: str) -> datetime:
    return datetime.fromisoformat(MANIFEST[f"submissions/{name}"]["fetched_at"])


@pytest.mark.parametrize(("accession", "fixture"), EASTERN_INDEX)
def test_acceptance_times_match_the_index_pages(accession: str, fixture: str) -> None:
    rows = _parse(fixture)

    assert rows[accession].accepted_at == _accepted_on_index_page(accession)
    check = check_acceptance_times(list(rows.values()), _fetched(fixture))
    assert check.trusted, check.reason


def test_a_file_with_every_time_shifted_is_not_trusted() -> None:
    rows = _parse("AAPL.json")  # served on 2026-10-24

    late = rows["0000320193-26-000018"].accepted_at
    assert late is not None
    assert (late - _accepted_on_index_page("0000320193-26-000018")).total_seconds() == 4 * 3600
    check = check_acceptance_times(list(rows.values()), _fetched("AAPL.json"))
    assert not check.trusted
    assert (check.out_of_hours, check.covered) == (653, 994)
    assert "outside EDGAR's hours" in (check.reason or "")


def test_a_time_after_the_fetch_is_not_trusted() -> None:
    rows = list(_parse("TGT.json").values())

    check = check_acceptance_times(rows, datetime(2026, 1, 1, tzinfo=UTC))

    assert check.future > 0 and not check.trusted


def test_rows_carry_items_dates_and_the_listing_file_number() -> None:
    parsed = parse_submissions((FIXTURES / "submissions" / "AAPL-20261025.json").read_bytes())
    rows = {row.accession_no: row for row in parsed.filings}

    eight_k, ten_k = rows["0000320193-26-000018"], rows["0000320193-25-000079"]
    assert (eight_k.form, eight_k.items_8k, eight_k.file_number) == (
        "8-K",
        ("2.02", "9.01"),
        "001-36743",
    )
    assert (ten_k.items_8k, str(ten_k.period_of_report), ten_k.is_xbrl) == (
        None,
        "2025-09-27",
        True,
    )
    assert len(parsed.filings) == 1002 and parsed.cik == 320193
    assert [page.name for page in parsed.pages] == ["CIK0000320193-submissions-001.json"]


def test_a_file_without_its_columns_is_refused() -> None:
    with pytest.raises(SecFileError, match="no column"):
        parse_submissions(b'{"cik": "0000320193", "filings": {"recent": {"form": []}}}')


def _row(accession: str, form: str = "8-K", **values: object) -> FilingRow:
    base: dict[str, object] = {
        "accession_no": accession,
        "cik": 320193,
        "form": form,
        "filed_at": date(2026, 7, 30),
        "accepted_at": None,
        "period_of_report": None,
        "items_8k": None,
        "primary_doc": None,
        "is_xbrl": None,
        "file_number": None,
    }
    base.update(values)
    return FilingRow(**base)  # type: ignore[arg-type]


def test_the_plan_adds_new_listings_fills_missing_times_and_keeps_stored_ones() -> None:
    t1, t2 = datetime(2026, 7, 30, 20, tzinfo=UTC), datetime(2026, 7, 31, 0, tzinfo=UTC)
    listed = [_row("1"), _row("2", accepted_at=t1), _row("3", accepted_at=t2), _row("1")]

    plan = plan_filings({"2": None, "3": t1}, listed)

    assert [row.accession_no for row in plan.new] == ["1"]
    assert [(f.accession_no, f.accepted_at) for f in plan.fill] == [("2", t1)]
    assert [(d.accession_no, d.stored, d.listed) for d in plan.differ] == [("3", t1, t2)]


def test_routing_follows_section_four() -> None:
    earnings = _row("0000320193-26-000018", items_8k=("2.02", "9.01"))
    assert [r.kind for r in route(earnings, filer_type="domestic")] == [EARNINGS_EXTRACT]
    assert route(earnings, filer_type="foreign_private_issuer") == []
    assert route(_row("a", items_8k=("5.02",)), filer_type="domestic") == []
    ten_q = route(_row("b", form="10-Q"), filer_type="domestic")
    # Keyed by company, so every new 10-K or 10-Q queues a finished refresh again (D-081).
    assert [(r.kind, r.key, r.force) for r in ten_q] == [(FACTS_REFRESH, "320193", True)]
    assert not any(r.force for r in route(earnings, filer_type="domestic"))
    assert route(_row("c", form="10-Q"), filer_type="fund") == []
    assert [r.kind for r in route(_row("d", form="4"), filer_type="domestic")] == [FORM4_PARSE]
    owner = _row("e", form="4", file_number="001-41042")  # the company is the reporting owner
    assert route(owner, filer_type="domestic") == []
    assert route(_row("f", form="13F-HR"), filer_type="domestic") == []
    assert route(_row("g", form="SCHEDULE 13D"), filer_type="domestic") == []
