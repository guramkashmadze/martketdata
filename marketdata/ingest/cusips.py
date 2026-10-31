"""CUSIP mapping (E9-S3): the CUSIPs of stored 13F lines asked about on OpenFIGI.

A `cusip_map` task (key `pending`) asks about every CUSIP not asked yet, and again after 30
days about those not matched. A CUSIP whose check digit is wrong is stored unmatched without
asking. Each request and its response are kept in the raw store before the response is read
(`raw/openfigi/mapping/…`); each batch is stored in its own transaction, so a stopped task
loses at most one batch. A match links to our current security with OpenFIGI's ticker
(`BRK/B` is our `BRK-B`), when we list it; matches without a security are tried again each run,
as tickers are added to the security master. The task runs until nothing is pending, so CUSIPs
stored while it ran are asked about too.

The live f13_parse queues the task after each report it stores; the funds backfill queues it
once, after its reports (E9-S5).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from psycopg import AsyncConnection

from marketdata.db.repositories import cusips, securities
from marketdata.db.repositories.cusips import MappedCusip
from marketdata.ingest.fetch import Connect, RawFetcher
from marketdata.normalize.cusips import (
    CusipMatch,
    decide,
    invalid,
    mapping_jobs,
    parse_mapping,
    valid_cusip,
)
from marketdata.normalize.tickers import TickerError, canonical_ticker
from marketdata.openfigi.client import MAPPING_URL, OpenFigiClient
from marketdata.sec.raw_store import fetched_at_name

log = structlog.get_logger(__name__)

RECHECK_AFTER = timedelta(days=30)


@dataclass(frozen=True)
class MappingReport:
    asked: int  # CUSIPs sent to OpenFIGI
    requests: int
    matched: int
    unmatched: int  # of those asked, and the invalid ones never asked
    ambiguous: int
    invalid: int
    linked: int  # matches linked to our security now, also earlier matches


class CusipMapper:
    def __init__(
        self,
        client: OpenFigiClient,
        fetcher: RawFetcher,
        connect: Connect,
        *,
        recheck_after: timedelta = RECHECK_AFTER,
    ) -> None:
        self._client = client
        self._fetcher = fetcher
        self._connect = connect
        self._recheck_after = recheck_after

    async def run(self) -> MappingReport:
        """Ask about every pending CUSIP, then link the matches to our securities."""
        asked = requests = matched = unmatched = ambiguous = bad = 0
        done: set[str] = set()
        while True:
            async with self._connect() as conn:
                before = datetime.now(UTC) - self._recheck_after
                waiting = [c for c in await cusips.pending(conn, before) if c not in done]
            if not waiting:
                break
            wrong = [cusip for cusip in waiting if not valid_cusip(cusip)]
            if wrong:
                async with self._connect() as conn, conn.transaction():
                    await cusips.upsert(conn, [MappedCusip(invalid(c), None, None) for c in wrong])
                bad += len(wrong)
                done.update(wrong)
            good = [cusip for cusip in waiting if valid_cusip(cusip)]
            size = self._client.limits.jobs_per_request
            for start in range(0, len(good), size):
                batch = good[start : start + size]
                matches = await self._ask(batch)
                requests += 1
                asked += len(batch)
                matched += sum(1 for match, _ in matches if match.status == "matched")
                unmatched += sum(1 for match, _ in matches if match.status == "unmatched")
                ambiguous += sum(1 for match, _ in matches if match.status == "ambiguous")
                done.update(batch)
        linked = await self._link_unlinked()
        report = MappingReport(asked, requests, matched, unmatched + bad, ambiguous, bad, linked)
        log.info(
            "cusips_mapped",
            asked=asked,
            requests=requests,
            matched=matched,
            unmatched=report.unmatched,
            ambiguous=ambiguous,
            invalid=bad,
            linked=linked,
        )
        return report

    async def _ask(self, batch: Sequence[str]) -> list[tuple[CusipMatch, int | None]]:
        response = await self._client.map(mapping_jobs(batch))
        fetched_at = datetime.now(UTC)
        key = f"raw/openfigi/mapping/{fetched_at_name(fetched_at)}"
        await self._fetcher.keep(
            MAPPING_URL, f"{key}.request.json", response.request, "application/json", fetched_at
        )
        await self._fetcher.keep(
            MAPPING_URL, f"{key}.json", response.content, response.content_type, fetched_at
        )
        results = parse_mapping(response.content, len(batch))
        matches = [decide(cusip, result) for cusip, result in zip(batch, results, strict=True)]
        async with self._connect() as conn, conn.transaction():
            links = await _securities(conn, [match.ticker for match in matches if match.ticker])
            linked = [(match, links.get(_canonical(match.ticker))) for match in matches]
            await cusips.upsert(
                conn, [MappedCusip(match, security, f"{key}.json") for match, security in linked]
            )
        return linked

    async def _link_unlinked(self) -> int:
        """Link stored matches without a security to the security now listing their ticker."""
        async with self._connect() as conn, conn.transaction():
            unlinked = await cusips.unlinked(conn)
            links = await _securities(conn, [row.ticker for row in unlinked if row.ticker])
            found = [
                (row.cusip, links[ticker])
                for row in unlinked
                if (ticker := _canonical(row.ticker)) in links
            ]
            await cusips.set_securities(conn, found)
        return len(found)


async def _securities(conn: AsyncConnection[Any], tickers: Sequence[str]) -> dict[str | None, int]:
    """Our current security of each of these OpenFIGI tickers, by canonical ticker."""
    canonical = sorted({name for ticker in tickers if (name := _canonical(ticker))})
    rows = await securities.find_current(conn, canonical)
    return {row.ticker: row.security_id for row in rows}


def _canonical(ticker: str | None) -> str | None:
    if ticker is None:
        return None
    try:
        return canonical_ticker(ticker)
    except TickerError:
        return None
