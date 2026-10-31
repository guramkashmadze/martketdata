"""E3-S5: `marketdata backfill filings --years N` queues one backfill per universe company; the
worker runs them. E7a: `marketdata backfill earnings --quarters N` queues one earnings backfill
per domestic universe company. E4a: `marketdata backfill facts` queues one facts refresh per
domestic universe company, and both take `--ticker`. E8: `marketdata backfill insiders
--quarters N` queues one insiders backfill per domestic filer or foreign private issuer in the
universe. E9: `marketdata backfill funds --quarters N` queues one funds backfill per curated
manager, and `marketdata backfill cusips` the CUSIP mapping. The CLI commits, so the test removes
what it wrote."""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest
from typer.testing import CliRunner

from marketdata import cli
from marketdata.normalize.cusips import CUSIP_MAP
from marketdata.normalize.filings import FACTS_REFRESH
from marketdata.normalize.holdings import backfill_start
from marketdata.tasks.kinds import (
    EARNINGS_BACKFILL,
    FILINGS_BACKFILL,
    FUNDS_BACKFILL,
    INSIDERS_BACKFILL,
)
from marketdata.tasks.scheduler import months_before, years_before

CIKS = (320193, 1652044)
FUND = 884394  # SPY: in the universe, but earnings apply to domestic filers only (§9.1)
FOREIGN = 1046179  # TSM: its directors and officers file Forms 4 since March 2026 (E8)
MANAGERS = {1067983: "Berkshire Hathaway", 1096343: "Markel Group", 1336528: "Pershing Square"}


@pytest.fixture
def universe(
    migrated_database: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[str]:
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.setenv("DATABASE_URL", migrated_database)
    with psycopg.connect(migrated_database, autocommit=True) as conn:
        for cik in CIKS:
            conn.execute(
                "insert into companies (cik, name, filer_type, in_universe) "
                "values (%s, 'test', 'domestic', true)",
                [cik],
            )
    yield migrated_database
    with psycopg.connect(migrated_database, autocommit=True) as conn:
        conn.execute(
            "delete from tasks where kind = any(%s)",
            [
                [
                    FILINGS_BACKFILL,
                    EARNINGS_BACKFILL,
                    FACTS_REFRESH,
                    INSIDERS_BACKFILL,
                    FUNDS_BACKFILL,
                ]
            ],
        )
        conn.execute("delete from managers where manager_cik = any(%s)", [list(MANAGERS)])
        others = list(CIKS) + [FUND, FOREIGN]
        conn.execute("delete from securities where cik = any(%s)", [others])
        conn.execute("delete from companies where cik = any(%s)", [others])


def test_one_backfill_per_universe_company(universe: str) -> None:
    since = years_before(datetime.now(UTC).date(), 5)

    first = CliRunner().invoke(cli.app, ["backfill", "filings", "--years", "5"])
    again = CliRunner().invoke(cli.app, ["backfill", "filings", "--years", "3"])

    assert first.exit_code == 0, first.output
    assert f"Queued 2 filing backfills back to {since} for 2 universe companies" in first.stdout
    assert "2 were already pending and keep their own start date." in again.stdout
    with psycopg.connect(universe) as conn:
        rows = conn.execute(
            "select key, payload from tasks where kind = %s order by key", [FILINGS_BACKFILL]
        ).fetchall()
    assert rows == [
        (str(cik), {"cik": cik, "since": since.isoformat()}) for cik in sorted(CIKS, key=str)
    ]


def test_one_earnings_backfill_per_domestic_universe_company(universe: str) -> None:
    with psycopg.connect(universe, autocommit=True) as conn:
        conn.execute(
            "insert into companies (cik, name, filer_type, in_universe) "
            "values (%s, 'test', 'fund', true)",
            [FUND],
        )
    since = months_before(datetime.now(UTC).date(), 12)

    result = CliRunner().invoke(cli.app, ["backfill", "earnings", "--quarters", "4", "--force"])

    assert result.exit_code == 0, result.output
    assert f"Queued 2 earnings backfills back to {since} for 2 domestic" in result.stdout
    with psycopg.connect(universe) as conn:
        rows = conn.execute(
            "select key, payload, priority from tasks where kind = %s order by key",
            [EARNINGS_BACKFILL],
        ).fetchall()
    payload = {"since": since.isoformat(), "force": True}
    assert rows == [(str(cik), {"cik": cik, **payload}, -10) for cik in sorted(CIKS, key=str)]


def test_an_empty_universe_is_an_error(
    migrated_database: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", migrated_database)

    result = CliRunner().invoke(cli.app, ["backfill", "filings"])

    assert result.exit_code == 1
    assert "No universe companies" in result.output


def _fund_and_tickers(url: str) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            "insert into companies (cik, name, filer_type, in_universe) "
            "values (%s, 'test', 'fund', true)",
            [FUND],
        )
        for cik, ticker in ((320193, "AAPL"), (1652044, "GOOGL"), (FUND, "SPY")):
            conn.execute(
                "insert into securities (cik, ticker, valid_from) values (%s, %s, '2026-10-25')",
                [cik, ticker],
            )


def test_one_facts_refresh_per_domestic_universe_company(universe: str) -> None:
    _fund_and_tickers(universe)

    every = CliRunner().invoke(cli.app, ["backfill", "facts"])
    one = CliRunner().invoke(cli.app, ["backfill", "earnings", "--ticker", "aapl"])

    assert every.exit_code == 0, every.output
    assert "Queued facts refreshes for 2 domestic universe companies" in every.stdout
    assert one.exit_code == 0, one.output
    with psycopg.connect(universe) as conn:
        facts = conn.execute(
            "select key, payload, priority from tasks where kind = %s order by key",
            [FACTS_REFRESH],
        ).fetchall()
        earnings = conn.execute(
            "select key from tasks where kind = %s", [EARNINGS_BACKFILL]
        ).fetchall()
    assert facts == [(str(cik), {"cik": cik}, -10) for cik in sorted(CIKS, key=str)]
    assert earnings == [("320193",)]


def test_a_ticker_outside_the_domestic_universe_is_refused(universe: str) -> None:
    _fund_and_tickers(universe)

    fund = CliRunner().invoke(cli.app, ["backfill", "facts", "--ticker", "SPY"])
    unknown = CliRunner().invoke(cli.app, ["backfill", "facts", "--ticker", "ZZZZ"])

    assert fund.exit_code == 2
    assert "SPY is a fund company in the universe" in fund.output
    assert unknown.exit_code == 1
    with psycopg.connect(universe) as conn:
        assert conn.execute(
            "select count(*) from tasks where kind = %s", [FACTS_REFRESH]
        ).fetchone() == (0,)


def test_insiders_backfills_cover_domestic_filers_and_foreign_issuers(universe: str) -> None:
    with psycopg.connect(universe, autocommit=True) as conn:
        for cik, filer_type in ((FUND, "fund"), (FOREIGN, "foreign_private_issuer")):
            conn.execute(
                "insert into companies (cik, name, filer_type, in_universe) "
                "values (%s, 'test', %s, true)",
                [cik, filer_type],
            )
    since = months_before(datetime.now(UTC).date(), 24)

    result = CliRunner().invoke(cli.app, ["backfill", "insiders"])

    assert result.exit_code == 0, result.output
    assert f"Queued 3 insiders backfills back to {since} for 3 universe companies" in result.stdout
    with psycopg.connect(universe) as conn:
        rows = conn.execute(
            "select key, payload from tasks where kind = %s order by key", [INSIDERS_BACKFILL]
        ).fetchall()
    assert rows == [
        (str(cik), {"cik": cik, "since": since.isoformat(), "force": False})
        for cik in sorted((*CIKS, FOREIGN), key=str)
    ]


def test_funds_backfills_cover_the_curated_managers(universe: str) -> None:
    with psycopg.connect(universe, autocommit=True) as conn:
        for cik, name in MANAGERS.items():
            conn.execute(
                "insert into managers (manager_cik, name, display_name, curated) "
                "values (%s, %s, %s, %s)",
                [cik, name.upper(), name, cik != 1336528],  # the old Pershing CIK, dropped
            )
    since = backfill_start(datetime.now(UTC).date(), 8)

    every = CliRunner().invoke(cli.app, ["backfill", "funds"])
    one = CliRunner().invoke(cli.app, ["backfill", "funds", "--manager", "markel", "--force"])
    unknown = CliRunner().invoke(cli.app, ["backfill", "funds", "--manager", "scion"])

    assert every.exit_code == 0, every.output
    assert (
        f"Queued 2 funds backfills of 13F reports filed since {since} for 2 curated managers"
        in every.stdout
    )
    # Markel's task is still queued: it keeps its payload, without force.
    assert "Queued 1 funds backfills" in one.stdout and "for 1 curated managers" in one.stdout
    assert unknown.exit_code == 1
    assert "No curated manager matches 'scion'" in unknown.output
    with psycopg.connect(universe) as conn:
        rows = conn.execute(
            "select key, payload from tasks where kind = %s order by key", [FUNDS_BACKFILL]
        ).fetchall()
    assert rows == [
        (str(cik), {"cik": cik, "since": since.isoformat(), "force": False})
        for cik in (1067983, 1096343)
    ]


def test_the_cusip_mapping_is_queued_and_can_ask_unmatched_cusips_again(universe: str) -> None:
    plain = CliRunner().invoke(cli.app, ["backfill", "cusips"])

    assert plain.exit_code == 0, plain.output
    assert "CUSIP mapping of the CUSIPs never asked about: queued" in plain.stdout
    with psycopg.connect(universe, autocommit=True) as conn:
        conn.execute("update tasks set status = 'done' where kind = %s", [CUSIP_MAP])
    again = CliRunner().invoke(cli.app, ["backfill", "cusips", "--recheck"])
    with psycopg.connect(universe) as conn:
        row = conn.execute(
            "select payload, status from tasks where kind = %s", [CUSIP_MAP]
        ).fetchone()

    assert "and those not matched" in again.stdout
    assert row == ({"recheck": True}, "queued")
