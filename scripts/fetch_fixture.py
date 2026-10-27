"""Save an SEC response under tests/fixtures/sec/ for offline tests (E1-S5).

    pdm run python scripts/fetch_fixture.py <url> <name> [--trim | --gzip]
    pdm run python scripts/fetch_fixture.py --from-raw <raw key> <name> [--trim | --gzip]

The request goes through the SEC client (User-Agent, rate limit, retries), so this script
needs SEC_USER_AGENT. With --from-raw it makes no request: it copies a file already in the raw
store (E3), with the URL and fetch time its raw_objects row records, so a fixture can be the
exact response a past run saw. `name` is a path under tests/fixtures/sec/, e.g.
`companyfacts/AAPL.json`. `--trim` keeps a fixture small (PLAN.md §6):

- companyfacts: only the concepts in the metric catalog (PLAN.md §9.3) and the dei cover
  facts; with --since-year, only facts from filings in or after that year;
- submissions: only the newest --keep-recent rows of `filings.recent` (every column);
- company_tickers_exchange and company_tickers_mf: only the rows of the --keep-ciks companies.

`--gzip` keeps every byte instead and compresses them (`name` ends in .gz): press releases are
proof documents, so they are never trimmed, and compressed they take about a tenth of the
space (E7a).

Each fixture is recorded in tests/fixtures/sec/MANIFEST.json with its source URL, fetch time,
the size and sha256 of the original response, the trim applied, and the size and sha256 of
the file saved. This is the only code outside the SEC client that reaches the network, and
unit tests never run it.
"""

import argparse
import asyncio
import gzip
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

from marketdata.config import SettingsError, load_settings
from marketdata.db.repositories import raw_objects
from marketdata.normalize.metric_catalog import concepts_by_taxonomy
from marketdata.sec import urls
from marketdata.sec.client import SecClient, SecError
from marketdata.sec.raw_store import RawStoreError, create_raw_store

GZIP_RULE = "gzip: every byte, compressed"

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "sec"
MANIFEST = FIXTURES / "MANIFEST.json"

# The metric catalog's concepts (PLAN.md §9.3, normalize/metric_catalog.py) and the dei cover
# facts, of which universe growth reads the public float (E4a-S5).
CATALOG_CONCEPTS = {
    taxonomy: list(dict.fromkeys(names + (("EntityPublicFloat",) if taxonomy == "dei" else ())))
    for taxonomy, names in concepts_by_taxonomy().items()
}


def trim_companyfacts(data: dict[str, Any], since_year: int | None = None) -> tuple[Any, str]:
    facts = data.get("facts", {})
    kept: dict[str, Any] = {}
    for taxonomy, concepts in CATALOG_CONCEPTS.items():
        available = facts.get(taxonomy, {})
        kept[taxonomy] = {}
        for concept in concepts:
            if concept not in available:
                continue
            entry = dict(available[concept])
            if since_year is not None:
                entry["units"] = {
                    unit: [fact for fact in rows if int(fact["filed"][:4]) >= since_year]
                    for unit, rows in entry.get("units", {}).items()
                }
            kept[taxonomy][concept] = entry
    trimmed = {**data, "facts": kept}
    rule = "companyfacts: PLAN.md §9.3 catalog concepts and dei cover facts only"
    if since_year is not None:
        rule += f"; facts filed in {since_year} or later"
    return trimmed, rule


def trim_submissions(data: dict[str, Any], keep_recent: int) -> tuple[Any, str]:
    recent = data["filings"]["recent"]
    total = len(recent["accessionNumber"])
    trimmed_recent = {column: values[:keep_recent] for column, values in recent.items()}
    trimmed = {**data, "filings": {**data["filings"], "recent": trimmed_recent}}
    rule = f"submissions: newest {min(keep_recent, total)} of {total} rows in filings.recent"
    return trimmed, rule


def trim_ticker_list(data: dict[str, Any], endpoint: str, keep_ciks: set[int]) -> tuple[Any, str]:
    column = data["fields"].index("cik")
    rows = data["data"]
    kept = [row for row in rows if row[column] in keep_ciks]
    ciks = ", ".join(str(cik) for cik in sorted(keep_ciks))
    rule = f"{endpoint}: rows of CIKs {ciks} ({len(kept)} of {len(rows)} rows)"
    return {**data, "data": kept}, rule


def trim(
    url: str,
    raw: bytes,
    *,
    keep_recent: int,
    since_year: int | None,
    keep_ciks: set[int] | None = None,
) -> tuple[bytes, str]:
    parsed = urls.parse(url)
    endpoint = parsed.endpoint if parsed else None
    data = json.loads(raw)
    if endpoint == "companyfacts":
        trimmed, rule = trim_companyfacts(data, since_year)
    elif endpoint == "submissions":
        trimmed, rule = trim_submissions(data, keep_recent)
    elif endpoint in ("company_tickers_exchange", "company_tickers_mf"):
        if not keep_ciks:
            raise ValueError(f"trimming {endpoint} needs --keep-ciks")
        trimmed, rule = trim_ticker_list(data, endpoint, keep_ciks)
    else:
        raise ValueError(f"no trim rule for {url}; save it untrimmed")
    # Compact, like the SEC's own JSON; key order is kept.
    return json.dumps(trimmed, separators=(",", ":"), ensure_ascii=False).encode(), rule


def record(name: str, entry: dict[str, Any]) -> None:
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    manifest[name] = entry
    MANIFEST.write_text(
        json.dumps(dict(sorted(manifest.items())), indent=2, ensure_ascii=False) + "\n"
    )


def _target(name: str) -> Path:
    target = (FIXTURES / name).resolve()
    if not target.is_relative_to(FIXTURES) or name == MANIFEST.name:
        raise ValueError(f"{name!r} must be a path under tests/fixtures/sec/")
    return target


async def fetch(
    url: str,
    name: str,
    *,
    trimmed: bool,
    keep_recent: int,
    since_year: int | None,
    keep_ciks: set[int] | None = None,
    compressed: bool = False,
) -> Path:
    target = _target(name)
    settings = load_settings()
    async with SecClient.from_settings(settings) as sec:
        response = await sec.get(url)
    fetched_at = datetime.now(UTC)
    return save(
        target,
        name,
        url,
        response.content,
        fetched_at,
        trimmed=trimmed,
        keep_recent=keep_recent,
        since_year=since_year,
        keep_ciks=keep_ciks,
        compressed=compressed,
    )


async def copy_from_raw(
    key: str,
    name: str,
    *,
    trimmed: bool,
    keep_recent: int,
    since_year: int | None,
    keep_ciks: set[int] | None = None,
    compressed: bool = False,
) -> Path:
    """Save the file at raw `key` as fixture `name`, with its raw_objects URL and fetch time."""
    target = _target(name)
    settings = load_settings()
    raw = await create_raw_store(settings).get(key)
    async with await psycopg.AsyncConnection.connect(str(settings.database_url)) as conn:
        row = await raw_objects.get(conn, key)
    if row is None:
        raise ValueError(f"no raw_objects row for {key}")
    if row.sha256 != hashlib.sha256(raw).hexdigest():
        raise ValueError(f"{key}: the raw store's bytes don't match the raw_objects row")
    return save(
        target,
        name,
        row.url,
        raw,
        row.fetched_at,
        trimmed=trimmed,
        keep_recent=keep_recent,
        since_year=since_year,
        keep_ciks=keep_ciks,
        raw_key=key,
        compressed=compressed,
    )


def save(
    target: Path,
    name: str,
    url: str,
    raw: bytes,
    fetched_at: datetime,
    *,
    trimmed: bool,
    keep_recent: int,
    since_year: int | None,
    keep_ciks: set[int] | None,
    raw_key: str | None = None,
    compressed: bool = False,
) -> Path:
    if compressed and (trimmed or not name.endswith(".gz")):
        raise ValueError("--gzip keeps every byte, so not with --trim; name the fixture *.gz")
    if compressed:
        data, rule = gzip.compress(raw, mtime=0), GZIP_RULE
    elif trimmed:
        data, rule = trim(
            url, raw, keep_recent=keep_recent, since_year=since_year, keep_ciks=keep_ciks
        )
    else:
        data, rule = raw, None
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    entry: dict[str, Any] = {
        "url": url,
        "fetched_at": fetched_at.astimezone(UTC).isoformat(timespec="seconds"),
        "original_size_bytes": len(raw),
        "original_sha256": hashlib.sha256(raw).hexdigest(),
        "trim": rule,
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    if raw_key is not None:
        entry["raw_key"] = raw_key
    record(name, entry)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "url",
        help="SEC URL, e.g. https://data.sec.gov/submissions/CIK0000320193.json; with --from-raw "
        "a raw key, e.g. raw/sec/api/submissions/320193/20261025T161850.560938Z.json",
    )
    parser.add_argument("name", help="path under tests/fixtures/sec/, e.g. submissions/AAPL.json")
    parser.add_argument(
        "--from-raw", action="store_true", help="copy from the raw store; no SEC request"
    )
    parser.add_argument("--trim", action="store_true", help="apply the endpoint's trim rule")
    parser.add_argument(
        "--gzip", action="store_true", help="keep every byte, compressed (name ends in .gz)"
    )
    parser.add_argument("--keep-recent", type=int, default=300, help="submissions rows to keep")
    parser.add_argument(
        "--since-year", type=int, help="companyfacts: keep facts filed from this year"
    )
    parser.add_argument("--keep-ciks", help="ticker lists: comma-separated CIKs whose rows to keep")
    args = parser.parse_args(argv)
    keep_ciks = {int(cik) for cik in args.keep_ciks.split(",")} if args.keep_ciks else None
    run = copy_from_raw if args.from_raw else fetch
    try:
        target = asyncio.run(
            run(
                args.url,
                args.name,
                trimmed=args.trim,
                keep_recent=args.keep_recent,
                since_year=args.since_year,
                keep_ciks=keep_ciks,
                compressed=args.gzip,
            )
        )
    except (SettingsError, SecError, RawStoreError, psycopg.Error, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 2 if isinstance(exc, SettingsError | ValueError) else 1
    print(f"{target.relative_to(ROOT)} ({target.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
