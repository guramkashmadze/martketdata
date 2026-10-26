"""E1-S5: scripts/fetch_fixture.py trims and records fixtures; E7a: or keeps every byte
compressed. Synthetic data; no network."""

import gzip
import hashlib
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "fetch_fixture", ROOT / "scripts" / "fetch_fixture.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


script = _load_script()

COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK0000320193.json"


def fact(filed: str) -> dict[str, object]:
    return {"end": "2024-09-28", "val": 1, "accn": "0000320193-24-000123", "filed": filed}


def test_companyfacts_keeps_catalog_concepts_and_dei_cover_facts() -> None:
    data = {
        "cik": 320193,
        "entityName": "Apple Inc.",
        "facts": {
            "dei": {
                "EntityCommonStockSharesOutstanding": {"units": {"shares": [fact("2024-11-01")]}},
                "EntityAddressCityOrTown": {"units": {}},
            },
            "us-gaap": {
                "Revenues": {"label": "Revenues", "units": {"USD": [fact("2024-11-01")]}},
                "AccountsPayableCurrent": {"units": {"USD": [fact("2024-11-01")]}},
            },
            "srt": {"Something": {"units": {}}},
        },
    }

    trimmed, rule = script.trim_companyfacts(data)

    assert trimmed["cik"] == 320193 and trimmed["entityName"] == "Apple Inc."
    assert set(trimmed["facts"]) == {"us-gaap", "dei"}
    assert list(trimmed["facts"]["us-gaap"]) == ["Revenues"]
    assert list(trimmed["facts"]["dei"]) == ["EntityCommonStockSharesOutstanding"]
    assert trimmed["facts"]["us-gaap"]["Revenues"]["label"] == "Revenues"
    assert "§9.3" in rule


def test_companyfacts_since_year_keeps_later_filings() -> None:
    data = {
        "facts": {
            "us-gaap": {"Assets": {"units": {"USD": [fact("2018-02-01"), fact("2023-02-01")]}}}
        }
    }

    trimmed, rule = script.trim_companyfacts(data, since_year=2020)

    assert trimmed["facts"]["us-gaap"]["Assets"]["units"]["USD"] == [fact("2023-02-01")]
    assert "2020 or later" in rule


def test_submissions_keeps_the_newest_rows_of_every_column() -> None:
    recent = {
        "accessionNumber": ["a3", "a2", "a1"],
        "form": ["10-Q", "8-K", "4"],
        "items": ["", "2.02,9.01", ""],
    }
    data = {
        "cik": "320193",
        "filings": {"recent": recent, "files": [{"name": "CIK0000320193-submissions-001.json"}]},
    }

    trimmed, rule = script.trim_submissions(data, keep_recent=2)

    assert trimmed["filings"]["recent"] == {
        "accessionNumber": ["a3", "a2"],
        "form": ["10-Q", "8-K"],
        "items": ["", "2.02,9.01"],
    }
    assert trimmed["filings"]["files"] == data["filings"]["files"]
    assert rule == "submissions: newest 2 of 3 rows in filings.recent"


def test_trim_picks_the_rule_by_url_and_writes_compact_json() -> None:
    raw = json.dumps({"facts": {"us-gaap": {"Assets": {"units": {}}}}}, indent=2).encode()

    data, rule = script.trim(COMPANYFACTS_URL, raw, keep_recent=300, since_year=None)

    assert data == b'{"facts":{"us-gaap":{"Assets":{"units":{}}},"dei":{}}}'
    assert rule.startswith("companyfacts")


def test_trim_refuses_urls_without_a_rule() -> None:
    with pytest.raises(ValueError, match="no trim rule"):
        script.trim(
            "https://data.sec.gov/api/xbrl/frames/us-gaap/Assets/USD/CY2025Q4I.json",
            b"{}",
            keep_recent=1,
            since_year=None,
        )


def test_ticker_lists_keep_the_rows_of_chosen_companies() -> None:
    raw = json.dumps(
        {
            "fields": ["cik", "name", "ticker", "exchange"],
            "data": [[320193, "Apple Inc.", "AAPL", "Nasdaq"], [1, "Other", "OTHR", "NYSE"]],
        }
    ).encode()

    data, rule = script.trim(
        "https://www.sec.gov/files/company_tickers_exchange.json",
        raw,
        keep_recent=300,
        since_year=None,
        keep_ciks={320193},
    )

    assert json.loads(data)["data"] == [[320193, "Apple Inc.", "AAPL", "Nasdaq"]]
    assert rule == "company_tickers_exchange: rows of CIKs 320193 (1 of 2 rows)"


def test_ticker_lists_need_the_companies_to_keep() -> None:
    with pytest.raises(ValueError, match="needs --keep-ciks"):
        script.trim(
            "https://www.sec.gov/files/company_tickers_mf.json",
            b'{"fields": ["cik"], "data": []}',
            keep_recent=300,
            since_year=None,
        )


@pytest.mark.parametrize("name", ["../x.json", "/tmp/x.json", "MANIFEST.json"])
def test_fixture_names_stay_in_the_fixture_folder(name: str) -> None:
    with pytest.raises(ValueError, match="under tests/fixtures/sec"):
        script._target(name)


def test_manifest_entries_are_merged_and_sorted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(script, "MANIFEST", tmp_path / "MANIFEST.json")

    script.record("submissions/TGT.json", {"url": "u2"})
    script.record("companyfacts/AAPL.json", {"url": "u1"})
    script.record("submissions/TGT.json", {"url": "u3"})

    manifest = json.loads((tmp_path / "MANIFEST.json").read_text())
    assert list(manifest) == ["companyfacts/AAPL.json", "submissions/TGT.json"]
    assert manifest["submissions/TGT.json"] == {"url": "u3"}


def test_gzip_keeps_every_byte_and_records_the_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(script, "MANIFEST", tmp_path / "MANIFEST.json")
    url = "https://www.sec.gov/Archives/edgar/data/320193/000032019326000018/ex991.htm"
    raw = b"<html><body><table><tr><td>Net sales</td><td>1</td></tr></table></body></html>"
    target = tmp_path / "ex991.htm.gz"

    script.save(
        target,
        "filings/320193/0000320193-26-000018/ex991.htm.gz",
        url,
        raw,
        datetime(2026, 10, 25, tzinfo=UTC),
        trimmed=False,
        keep_recent=0,
        since_year=None,
        keep_ciks=None,
        compressed=True,
    )

    assert gzip.decompress(target.read_bytes()) == raw
    entry = json.loads((tmp_path / "MANIFEST.json").read_text())[
        "filings/320193/0000320193-26-000018/ex991.htm.gz"
    ]
    assert entry["trim"] == "gzip: every byte, compressed"
    assert entry["original_sha256"] == hashlib.sha256(raw).hexdigest()
    assert entry["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()


def test_gzip_needs_a_gz_name_and_no_trim(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="name the fixture"):
        script.save(
            tmp_path / "x.htm",
            "filings/x.htm",
            "https://www.sec.gov/x",
            b"x",
            datetime(2026, 10, 25, tzinfo=UTC),
            trimmed=False,
            keep_recent=0,
            since_year=None,
            keep_ciks=None,
            compressed=True,
        )


def test_script_needs_the_user_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)  # no .env
    monkeypatch.setenv("DATABASE_URL", "postgresql://marketdata:marketdata@127.0.0.1:1/marketdata")
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)

    assert script.main([SUBMISSIONS_URL, "submissions/AAPL.json"]) == 2
    assert "SEC_USER_AGENT is missing" in capsys.readouterr().err
