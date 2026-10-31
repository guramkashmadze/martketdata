"""A quarter of the SEC's Insider Transactions Data Sets (E8-S4), raw first. They only check our
parsing of each Form 4's XML (`marketdata insiders reconcile`).

The landing page is fetched and kept to find the quarter's ZIP (D-045); the ZIP, 8 to 13 MB, is
kept at a key of its name and read from the raw store after that: a published quarter doesn't
change. D-047's refusal of bulk files is for the gigabyte bulk files, not these.
"""

from datetime import UTC, datetime

import structlog

from marketdata.db.repositories import raw_objects
from marketdata.ingest.fetch import Connect, RawFetcher
from marketdata.normalize.insider_data_set import data_set_link, zip_name
from marketdata.normalize.security_master import SecFileError
from marketdata.sec import urls
from marketdata.sec.raw_store import data_set_key, fetched_at_name

log = structlog.get_logger(__name__)

DATA_SET = "form345"


async def fetch_data_set(
    fetcher: RawFetcher, connect: Connect, quarter: str
) -> tuple[str, bytes, bool]:
    """A quarter's ZIP: its raw key, its bytes and whether it was fetched now."""
    key = data_set_key(DATA_SET, zip_name(quarter))
    async with connect() as conn:
        stored = await raw_objects.get(conn, key)
    if stored is not None:
        return key, await fetcher.read(key), False
    now = datetime.now(UTC)
    page = await fetcher.fetch_and_store(
        urls.INSIDER_DATA_SETS,
        data_set_key(DATA_SET, f"landing/{fetched_at_name(now)}.html"),
        fetched_at=now,
    )
    path = data_set_link(page, quarter)
    if path is None:
        raise SecFileError(f"the insider data sets page links no {zip_name(quarter)}")
    data = await fetcher.fetch_and_store(urls.www_path(path), key)
    log.info("insider_data_set_stored", quarter=quarter, key=key, size_bytes=len(data))
    return key, data, True
