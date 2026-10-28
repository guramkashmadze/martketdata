"""Public float of every filer from the SEC's frames API (E4a-S5, D-085).

`public_float_load` (queued by `marketdata backfill public-float`) fetches the instant frames of
`dei:EntityPublicFloat` in USD, and of the cover page's `dei:EntityCommonStockSharesOutstanding`
that checks it, for the nine calendar quarters before today; it keeps each response in the raw
store before parsing it, and writes its points to frame_facts. Nine requests of
about a megabyte replace the nightly companyfacts ZIP of more than a gigabyte (PLAN.md §8). A
quarter whose frame the SEC doesn't have yet (404) is skipped.
"""

from dataclasses import dataclass
from datetime import date

import structlog

from marketdata.db.repositories import frame_facts
from marketdata.ingest.fetch import Connect, RawFetcher
from marketdata.normalize.public_float import (
    COVER_SHARES,
    PUBLIC_FLOAT,
    instant_frames,
    parse_frame,
)
from marketdata.normalize.security_master import SecFileError
from marketdata.sec import urls
from marketdata.sec.client import SecNotFound

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class LoadedFrame:
    frame: str
    raw_key: str
    points: int
    written: int
    skipped: int


@dataclass(frozen=True)
class PublicFloatReport:
    frames: tuple[LoadedFrame, ...]
    missing: tuple[str, ...]  # frames the SEC doesn't have (404)


class PublicFloatLoader:
    def __init__(self, fetcher: RawFetcher, connect: Connect) -> None:
        self._fetcher = fetcher
        self._connect = connect

    async def load(self, today: date) -> PublicFloatReport:
        loaded: list[LoadedFrame] = []
        missing: list[str] = []
        for wanted in (PUBLIC_FLOAT, COVER_SHARES):
            await self._load(wanted, today, loaded, missing)
        return PublicFloatReport(tuple(loaded), tuple(missing))

    async def _load(
        self,
        wanted: tuple[str, str, str],
        today: date,
        loaded: list[LoadedFrame],
        missing: list[str],
    ) -> None:
        taxonomy, concept, unit = wanted
        for year, quarter in instant_frames(today):
            url = urls.frames(concept, unit, year, quarter, instant=True, taxonomy=taxonomy)
            name = urls.frame_period(year, quarter, instant=True)
            try:
                key, data = await self._fetcher.fetch_url(url)
            except SecNotFound:
                log.info("frame_not_found", frame=name, url=url)
                missing.append(f"{concept} {name}")
                continue
            frame = parse_frame(data)
            if (frame.taxonomy, frame.concept, frame.unit, frame.frame) != (*wanted, name):
                raise SecFileError(
                    f"{url} holds {frame.taxonomy}:{frame.concept} {frame.unit} {frame.frame}"
                )
            async with self._connect() as conn, conn.transaction():
                written = await frame_facts.upsert(conn, frame, key)
            log.info(
                "frame_loaded",
                concept=concept,
                frame=name,
                raw_key=key,
                points=len(frame.points),
                written=written,
                skipped=frame.skipped,
            )
            loaded.append(LoadedFrame(name, key, len(frame.points), written, frame.skipped))
