"""E2-S4: a newer snapshot of the SEC lists closes rows for tickers that went away or moved to
another CIK and opens new ones; a snapshot that looks cut short is refused (D-054).

Two snapshot fixtures: today's trimmed exchange list, and a copy crafted to look like the list
before 2026-07-01 (XOM still on CIK 34088, no SpaceX, one more ticker; see MANIFEST.json).
"""

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

from marketdata.normalize.security_master import (
    SnapshotRefused,
    TickerSnapshot,
    build_snapshot,
    check_snapshot,
    diff_securities,
    parse_exchange_list,
    parse_fund_list,
)
from marketdata.normalize.tickers import SecurityType

TICKERS = Path(__file__).resolve().parents[1] / "fixtures" / "sec" / "tickers"
FUND = parse_fund_list((TICKERS / "company_tickers_mf.json").read_bytes())
BEFORE, AFTER = date(2026, 6, 30), date(2026, 10, 25)


def snapshot(name: str) -> TickerSnapshot:
    return build_snapshot(parse_exchange_list((TICKERS / name).read_bytes()), FUND)


@dataclass(frozen=True)
class Row:
    security_id: int
    cik: int
    ticker: str
    security_type: SecurityType
    exchange: str | None
    share_class: str | None
    valid_from: date


def stored(snap: TickerSnapshot, valid_from: date) -> list[Row]:
    listings = sorted(snap.listings.values(), key=lambda listing: listing.ticker)
    return [
        Row(i, x.cik, x.ticker, x.security_type, x.exchange, x.share_class, valid_from)
        for i, x in enumerate(listings, start=1)
    ]


OLD = snapshot("company_tickers_exchange_before.json")
NEW = snapshot("company_tickers_exchange.json")


def test_moved_and_gone_tickers_close_and_new_ones_open() -> None:
    current = stored(OLD, BEFORE)

    changes = diff_securities(current, NEW.listings, AFTER)

    assert [(c.row.ticker, c.row.cik, c.moved_to) for c in changes.moved] == [
        ("SPCX", 1719812, 1181412),  # the fund series, until SpaceX listed
        ("XOM", 34088, 2115436),  # Exxon Mobil Corp, until the holding company took the ticker
    ]
    assert [(c.row.ticker, c.row.cik) for c in changes.closed if c.moved_to is None] == [
        ("DLST", 9999999999)
    ]
    assert [(x.ticker, x.cik) for x in changes.opened] == [("SPCX", 1181412), ("XOM", 2115436)]
    assert changes.updated == () and changes.deleted == ()
    assert changes.unchanged == len(NEW.listings) - 2
    check_snapshot(current, changes, AFTER)  # ordinary churn passes


def test_a_row_opened_the_same_day_changes_in_place() -> None:
    current = stored(OLD, AFTER)  # the older lists were loaded earlier the same day

    changes = diff_securities(current, NEW.listings, AFTER)

    assert changes.closed == () and changes.opened == ()
    assert [row.ticker for row in changes.deleted] == ["DLST"]
    assert sorted((u.listing.ticker, u.listing.cik) for u in changes.updated) == [
        ("SPCX", 1181412),
        ("XOM", 2115436),
    ]


def test_a_snapshot_older_than_the_stored_rows_is_refused_even_when_forced() -> None:
    current = stored(NEW, AFTER)
    changes = diff_securities(current, OLD.listings, BEFORE)

    for force in (False, True):
        with pytest.raises(SnapshotRefused, match="newer than this snapshot of 2026-06-30"):
            check_snapshot(current, changes, BEFORE, force=force)


def test_a_list_cut_short_is_refused_unless_forced() -> None:
    current = stored(NEW, BEFORE)
    cut = {t: x for t, x in NEW.listings.items() if x.source == "exchange" or x.cik == 36405}

    changes = diff_securities(current, cut, AFTER)

    with pytest.raises(
        SnapshotRefused, match=r"the fund list would end 393 of 445 current tickers"
    ):
        check_snapshot(current, changes, AFTER)
    check_snapshot(current, changes, AFTER, force=True)
