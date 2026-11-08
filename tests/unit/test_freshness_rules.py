"""D-135, D-136: when an answer's data was last checked against the SEC, when that check is
overdue, and when a filing a watch finds is still live."""

from datetime import UTC, date, datetime, timedelta

from marketdata.normalize.filings import FilingRow, found_live
from marketdata.normalize.freshness import (
    CHECK_OVERDUE_AFTER,
    PeriodicReport,
    check_overdue,
    financials_checked,
    oldest_check,
)

NOW = datetime(2026, 11, 8, 9, 0, tzinfo=UTC)
WATCHED = datetime(2026, 11, 8, 8, 50, tzinfo=UTC)


def test_a_check_is_overdue_after_a_day_or_when_it_never_ran() -> None:
    assert CHECK_OVERDUE_AFTER == timedelta(days=1)
    assert not check_overdue(NOW - timedelta(minutes=10), NOW)
    assert not check_overdue(NOW - timedelta(days=1), NOW)
    assert check_overdue(NOW - timedelta(days=1, seconds=1), NOW)
    # AMD's watch on 2026-11-08: last run on 2026-10-25, two weeks before.
    assert check_overdue(datetime(2026, 10, 25, 21, 25, 27, tzinfo=UTC), NOW)
    assert check_overdue(None, NOW)


def test_several_checks_are_as_fresh_as_the_oldest() -> None:
    older = NOW - timedelta(days=3)
    assert oldest_check([WATCHED, older, NOW]) == older
    assert oldest_check([WATCHED, None]) is None  # one was never watched
    assert oldest_check([]) is None


def _report(discovered: datetime, loaded: bool) -> PeriodicReport:
    return PeriodicReport(discovered_at=discovered, loaded=loaded)


def test_financials_are_checked_with_the_watch_while_the_newest_report_is_loaded() -> None:
    reports = [_report(NOW - timedelta(days=9), True), _report(NOW - timedelta(days=100), True)]

    assert financials_checked(WATCHED, reports) == WATCHED
    assert financials_checked(WATCHED, []) == WATCHED  # no 10-K or 10-Q listed


def test_financials_lacking_the_newest_reports_are_checked_when_the_first_was_found() -> None:
    """KO's July 10-Q, which companyfacts didn't list three months later (E4a): its financials
    lack it from the moment the watch found it, however often the watch runs since."""
    found = datetime(2026, 7, 29, 21, 0, tzinfo=UTC)
    reports = [
        _report(datetime(2026, 10, 28, 20, 5, tzinfo=UTC), False),
        _report(found, False),
        _report(datetime(2026, 4, 29, 20, 10, tzinfo=UTC), True),
        _report(datetime(2026, 2, 1, 12, 0, tzinfo=UTC), False),  # older than a loaded one
    ]

    assert financials_checked(WATCHED, reports) == found
    assert check_overdue(financials_checked(WATCHED, reports), NOW)


def test_a_report_still_loading_keeps_the_financials_current() -> None:
    """A 10-Q found minutes ago whose facts refresh hasn't run yet, or waits for companyfacts to
    list it (FactsNotPublished)."""
    found = NOW - timedelta(minutes=4)
    checked = financials_checked(WATCHED, [_report(found, False), _report(NOW, True)])

    assert checked == min(WATCHED, found)
    assert not check_overdue(checked, NOW)


def test_financials_of_a_company_never_watched_were_never_checked() -> None:
    assert financials_checked(None, [_report(NOW, True)]) is None


def _filing(accepted_at: datetime | None, filed_at: date) -> FilingRow:
    return FilingRow(
        accession_no="0000002488-26-000101",
        cik=2488,
        form="8-K",
        filed_at=filed_at,
        accepted_at=accepted_at,
        period_of_report=None,
        items_8k=("2.02", "9.01"),
        primary_doc="d1.htm",
        is_xbrl=False,
        file_number=None,
    )


def test_a_filing_found_within_a_day_of_its_acceptance_is_live() -> None:
    accepted = datetime(2026, 11, 4, 21, 5, 12, tzinfo=UTC)
    filing = _filing(accepted, date(2026, 11, 4))

    assert found_live(filing, accepted + timedelta(minutes=9))  # the next watch round
    assert found_live(filing, accepted + timedelta(hours=10))  # after a night without watches
    assert found_live(filing, accepted + timedelta(days=1))
    assert not found_live(filing, accepted + timedelta(days=1, seconds=1))
    # Found on 2026-11-08 after the watches had stopped since 2026-10-25: caught up.
    assert not found_live(filing, NOW)


def test_without_an_acceptance_time_the_filing_day_decides() -> None:
    """A file whose times failed the check gives none (D-057)."""
    filing = _filing(None, date(2026, 11, 7))

    assert found_live(filing, datetime(2026, 11, 7, 22, 0, tzinfo=UTC))
    assert found_live(filing, datetime(2026, 11, 8, 23, 59, tzinfo=UTC))
    assert not found_live(filing, datetime(2026, 11, 9, 0, 0, tzinfo=UTC))
