"""E8-S4: a quarter of the SEC's insider data set is fetched raw first through its landing page,
then read from the raw store; our parse of a stored Form 4 matches its row."""

import io
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import pytest
from conftest import connect_to, filing_routes, sec_serving

from marketdata.db.repositories import filings, raw_objects
from marketdata.ingest.fetch import RawFetcher
from marketdata.ingest.form4 import Form4Ingest
from marketdata.ingest.insider_data_set import fetch_data_set
from marketdata.normalize.filings import FilingRow
from marketdata.normalize.insider_data_set import quarter_dates, read_data_set, reconcile
from marketdata.normalize.security_master import SecFileError
from marketdata.sec import urls
from marketdata.sec.raw_store import LocalRawStore
from marketdata.service.insiders import filed_in

Conn = psycopg.AsyncConnection[Any]
TMQ = 1543418
SOUTH32 = "0001193125-26-378380"  # filed 2026-09-01: one sale by South32 and its subsidiary
LINK = "/files/datastandardsinnovation/data/insider-transactions-data-sets/2026q3_form345.zip"

SUBMISSION = (
    "ACCESSION_NUMBER\tFILING_DATE\tPERIOD_OF_REPORT\tDATE_OF_ORIG_SUB\tNO_SECURITIES_OWNED\t"
    "NOT_SUBJECT_SEC16\tFORM3_HOLDINGS_REPORTED\tFORM4_TRANS_REPORTED\tDOCUMENT_TYPE\tISSUERCIK\t"
    "ISSUERNAME\tISSUERTRADINGSYMBOL\tREMARKS\tAFF10B5ONE\n"
    f"{SOUTH32}\t01-SEP-2026\t28-AUG-2026\t\t\t1\t\t\t4\t0001543418\tTrilogy Metals Inc.\tTMQ\t\t"
    "false\n"
)
NONDERIV = (
    "ACCESSION_NUMBER\tNONDERIV_TRANS_SK\tSECURITY_TITLE\tSECURITY_TITLE_FN\tTRANS_DATE\t"
    "TRANS_DATE_FN\tDEEMED_EXECUTION_DATE\tDEEMED_EXECUTION_DATE_FN\tTRANS_FORM_TYPE\tTRANS_CODE\t"
    "EQUITY_SWAP_INVOLVED\tEQUITY_SWAP_TRANS_CD_FN\tTRANS_TIMELINESS\tTRANS_TIMELINESS_FN\t"
    "TRANS_SHARES\tTRANS_SHARES_FN\tTRANS_PRICEPERSHARE\tTRANS_PRICEPERSHARE_FN\t"
    "TRANS_ACQUIRED_DISP_CD\tTRANS_ACQUIRED_DISP_CD_FN\tSHRS_OWND_FOLWNG_TRANS\t"
    "SHRS_OWND_FOLWNG_TRANS_FN\tVALU_OWND_FOLWNG_TRANS\tVALU_OWND_FOLWNG_TRANS_FN\t"
    "DIRECT_INDIRECT_OWNERSHIP\tDIRECT_INDIRECT_OWNERSHIP_FN\tNATURE_OF_OWNERSHIP\t"
    "NATURE_OF_OWNERSHIP_FN\n"
    f"{SOUTH32}\t1\tCommon Stock\t\t28-AUG-2026\t\t\t\t4\tS\t0\t\t\t\t8215570.0\t\t2.17\tF2\tD\t\t"
    "10379741.0\t\t\t\tI\t\tBy wholly-owned subsidiary\tF1\n"
)
DERIV = "ACCESSION_NUMBER\tDERIV_TRANS_SK\tSECURITY_TITLE\n"


def _zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in (
            ("SUBMISSION", SUBMISSION),
            ("NONDERIV_TRANS", NONDERIV),
            ("DERIV_TRANS", DERIV),
        ):
            archive.writestr(f"{name}.tsv", text)
    return buffer.getvalue()


def _routes(page: bytes) -> dict[str, bytes | int]:
    return {urls.INSIDER_DATA_SETS: page, urls.www_path(LINK): _zip()}


async def test_the_quarter_is_fetched_raw_first_then_read_from_the_raw_store(
    conn: Conn, tmp_path: Path
) -> None:
    store = LocalRawStore(tmp_path / "raw")
    page = f'<a href="{LINK}" download>2026 Q3 345</a>'.encode()

    async with sec_serving(_routes(page)) as sec:
        key, data, fetched = await fetch_data_set(
            RawFetcher(sec, store, connect_to(conn)), connect_to(conn), "2026q3"
        )
    async with sec_serving({}) as sec:
        again = await fetch_data_set(
            RawFetcher(sec, store, connect_to(conn)), connect_to(conn), "2026q3"
        )

    assert (key, fetched) == ("raw/sec/datasets/form345/2026q3_form345.zip", True)
    assert again == (key, data, False)
    assert await raw_objects.get(conn, key) is not None


async def test_a_page_without_the_quarter_is_refused(conn: Conn, tmp_path: Path) -> None:
    async with sec_serving(_routes(b"<p>no links</p>")) as sec:
        fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
        with pytest.raises(SecFileError, match="links no 2026q3_form345.zip"):
            await fetch_data_set(fetcher, connect_to(conn), "2026q3")


async def test_our_parse_of_a_stored_form_4_matches_the_data_set(
    conn: Conn, tmp_path: Path
) -> None:
    row = FilingRow(
        SOUTH32,
        TMQ,
        "4",
        date(2026, 9, 1),
        None,
        None,
        None,
        "xslF345X06/ownership.xml",
        None,
        None,
    )
    await filings.insert_new(conn, [row])
    async with sec_serving(filing_routes()) as sec:
        fetcher = RawFetcher(sec, LocalRawStore(tmp_path / "raw"), connect_to(conn))
        await Form4Ingest(fetcher, connect_to(conn)).run(TMQ, SOUTH32)

    ours = await filed_in(conn, [TMQ], *quarter_dates("2026q3"))
    [check] = reconcile(ours, read_data_set(_zip(), [TMQ]))

    assert (check.accession_no, check.status, check.lines, check.differences) == (
        SOUTH32,
        "same",
        1,
        (),
    )
