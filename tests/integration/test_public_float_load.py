"""E4a-S5: the public float load stores nine instant frames of dei:EntityPublicFloat (raw file
first) and their points; `marketdata universe suggest` ranks listed filers from them, and
`marketdata backfill public-float` queues the load for the worker (D-085)."""

import csv
import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest
from conftest import connect_to, sec_serving
from typer.testing import CliRunner

from marketdata import cli
from marketdata.db.repositories import frame_facts
from marketdata.ingest.fetch import RawFetcher
from marketdata.ingest.public_float import PublicFloatLoader
from marketdata.normalize.public_float import PUBLIC_FLOAT, instant_frames
from marketdata.sec import urls
from marketdata.sec.raw_store import LocalRawStore
from marketdata.tasks.kinds import PUBLIC_FLOAT_LOAD

Conn = psycopg.AsyncConnection[Any]
TODAY = date(2026, 10, 28)


def _frame(year: int, quarter: int, rows: list[tuple[int, str, int]]) -> bytes:
    name = urls.frame_period(year, quarter, instant=True)
    data = [
        {
            "accn": accession,
            "cik": cik,
            "entityName": f"FILER {cik}",
            "loc": "US-NY",
            "end": f"{year}-{quarter * 3:02d}-28",
            "val": value,
        }
        for cik, accession, value in rows
    ]
    document = {
        "taxonomy": "dei",
        "tag": "EntityPublicFloat",
        "ccp": name,
        "uom": "USD",
        "pts": len(data),
        "data": data,
    }
    return json.dumps(document).encode()


def _routes() -> dict[str, bytes | int]:
    routes: dict[str, bytes | int] = {}
    for year, quarter in instant_frames(TODAY):
        url = urls.frames("EntityPublicFloat", "USD", year, quarter, instant=True, taxonomy="dei")
        routes[url] = 404
    q2 = urls.frames("EntityPublicFloat", "USD", 2025, 2, instant=True, taxonomy="dei")
    q1 = urls.frames("EntityPublicFloat", "USD", 2025, 1, instant=True, taxonomy="dei")
    routes[q2] = _frame(2025, 2, [(27419, "0000027419-26-000016", 45284343058)])
    routes[q1] = _frame(2025, 1, [(320193, "0000320193-25-000079", 3253431000000)])
    return routes


async def test_the_load_stores_each_frame_and_its_points(conn: Conn, tmp_path: Path) -> None:
    async with sec_serving(_routes()) as sec:
        fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
        loader = PublicFloatLoader(fetcher, connect_to(conn))
        report = await loader.load(TODAY)
        again = await loader.load(TODAY)

    assert [(frame.frame, frame.points, frame.written) for frame in report.frames] == [
        ("CY2025Q2I", 1, 1),
        ("CY2025Q1I", 1, 1),
    ]
    assert len(report.missing) == 7 + 9  # frames the SEC doesn't have; no share counts here
    assert [frame.written for frame in again.frames] == [0, 0]
    stored = await frame_facts.points(conn, *PUBLIC_FLOAT)
    assert sorted((point.cik, point.value) for point in stored) == [
        (27419, 45284343058),
        (320193, 3253431000000),
    ]
    assert report.frames[0].raw_key.startswith(
        "raw/sec/api/frames/dei/EntityPublicFloat/USD/CY2025Q2I/"
    )


@pytest.fixture
def floats(
    migrated_database: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[str]:
    """Two listed filers with a public float, committed for the CLI; removed afterwards."""
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.setenv("DATABASE_URL", migrated_database)
    key = "raw/sec/api/frames/dei/EntityPublicFloat/USD/CY2025Q2I/20261028T000000.000000Z.json"
    with psycopg.connect(migrated_database, autocommit=True) as conn:
        conn.execute(
            "insert into raw_objects (key, url, sha256, size_bytes) "
            "values (%s, 'https://data.sec.gov/', %s, 1)",
            [key, "0" * 64],
        )
        listed = ((27419, "TGT", True, 45284343058), (2488, "AMD", False, 247000000000))
        for cik, ticker, universe, value in listed:
            conn.execute(
                "insert into companies (cik, name, filer_type, in_universe) "
                "values (%s, %s, %s, %s)",
                [cik, f"FILER {cik}", "domestic" if universe else "unknown", universe],
            )
            conn.execute(
                "insert into securities (cik, ticker, exchange, valid_from) "
                "values (%s, %s, 'NYSE', '2026-10-25')",
                [cik, ticker],
            )
            conn.execute(
                "insert into frame_facts (taxonomy, concept, unit, frame, cik, period_end, "
                "value, accession_no, raw_key) values ('dei', 'EntityPublicFloat', 'USD', "
                "'CY2025Q2I', %s, '2025-06-30', %s, '0000000001-26-000001', %s)",
                [cik, value, key],
            )
    yield migrated_database
    with psycopg.connect(migrated_database, autocommit=True) as conn:
        conn.execute("delete from frame_facts")
        conn.execute("delete from raw_objects where key = %s", [key])
        conn.execute("delete from tasks where kind = %s", [PUBLIC_FLOAT_LOAD])
        conn.execute("delete from securities where cik = any(%s)", [[27419, 2488]])
        conn.execute("delete from companies where cik = any(%s)", [[27419, 2488]])


def test_universe_suggest_writes_the_candidates_and_the_unranked(
    floats: str, tmp_path: Path
) -> None:
    out = tmp_path / "candidates.csv"

    result = CliRunner().invoke(cli.app, ["universe", "suggest", "--top", "5", "--out", str(out)])

    assert result.exit_code == 0, result.output
    assert "Ranked 2 listed filers by public float: 1 are not in the universe" in result.stdout
    with out.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [
        (row["rank"], row["tickers"], row["in_universe"], row["filer_class"]) for row in rows
    ] == [
        ("1", "AMD", "no", "not checked"),
        ("2", "TGT", "yes", "domestic"),
    ]
    assert (tmp_path / "candidates_unranked.csv").exists()


def test_backfill_public_float_queues_the_load(floats: str) -> None:
    result = CliRunner().invoke(cli.app, ["backfill", "public-float"])

    assert result.exit_code == 0, result.output
    today = datetime.now(UTC).date().isoformat()
    with psycopg.connect(floats) as conn:
        rows = conn.execute(
            "select key, payload from tasks where kind = %s", [PUBLIC_FLOAT_LOAD]
        ).fetchall()
    assert rows == [(today, {"day": today})]


def test_universe_suggest_needs_a_load_first(
    migrated_database: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", migrated_database)

    result = CliRunner().invoke(cli.app, ["universe", "suggest"])

    assert result.exit_code == 1
    assert "No public float loaded" in result.output
