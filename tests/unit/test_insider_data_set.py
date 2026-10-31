"""E8-S4: the SEC's insider data set is read from a quarter's ZIP and compared with our parse of
each Form 4: form, filing date, 10b5-1 box and every trade."""

import io
import zipfile
from datetime import date
from decimal import Decimal

import pytest

from marketdata.normalize.form4 import Line
from marketdata.normalize.insider_data_set import (
    OurFiling,
    data_set_link,
    quarter_dates,
    read_data_set,
    reconcile,
    zip_name,
)
from marketdata.normalize.security_master import SecFileError

SUBMISSION = [
    "ACCESSION_NUMBER\tFILING_DATE\tPERIOD_OF_REPORT\tDATE_OF_ORIG_SUB\tNO_SECURITIES_OWNED\t"
    "NOT_SUBJECT_SEC16\tFORM3_HOLDINGS_REPORTED\tFORM4_TRANS_REPORTED\tDOCUMENT_TYPE\tISSUERCIK\t"
    "ISSUERNAME\tISSUERTRADINGSYMBOL\tREMARKS\tAFF10B5ONE",
    *(
        "\t".join(
            [accession, filed, "29-JUL-2026", "", "", "0", "", "", form, cik, name, "", "", box]
        )
        for accession, filed, form, cik, name, box in (
            ("0000000001-26-000001", "31-JUL-2026", "4", "0000320193", "Apple Inc.", "1"),
            ("0000000001-26-000002", "01-AUG-2026", "3", "0000320193", "Apple Inc.", ""),
            ("0000000001-26-000003", "05-AUG-2026", "4", "0000000042", "Other", "false"),
        )
    ),
]
NONDERIV = [
    "ACCESSION_NUMBER\tNONDERIV_TRANS_SK\tSECURITY_TITLE\tSECURITY_TITLE_FN\tTRANS_DATE\t"
    "TRANS_DATE_FN\tDEEMED_EXECUTION_DATE\tDEEMED_EXECUTION_DATE_FN\tTRANS_FORM_TYPE\tTRANS_CODE\t"
    "EQUITY_SWAP_INVOLVED\tEQUITY_SWAP_TRANS_CD_FN\tTRANS_TIMELINESS\tTRANS_TIMELINESS_FN\t"
    "TRANS_SHARES\tTRANS_SHARES_FN\tTRANS_PRICEPERSHARE\tTRANS_PRICEPERSHARE_FN\t"
    "TRANS_ACQUIRED_DISP_CD\tTRANS_ACQUIRED_DISP_CD_FN\tSHRS_OWND_FOLWNG_TRANS\t"
    "SHRS_OWND_FOLWNG_TRANS_FN\tVALU_OWND_FOLWNG_TRANS\tVALU_OWND_FOLWNG_TRANS_FN\t"
    "DIRECT_INDIRECT_OWNERSHIP\tDIRECT_INDIRECT_OWNERSHIP_FN\tNATURE_OF_OWNERSHIP\t"
    "NATURE_OF_OWNERSHIP_FN",
    "0000000001-26-000001\t2\tCommon Stock\t\t29-JUL-2026\t\t\t\t4\tS\t0\t\t\t\t151.0\t\t"
    "343.08\tF1\tD\t\t39417.0\t\t\t\tD\t\t\t",
    "0000000001-26-000003\t3\tCommon Stock\t\t29-JUL-2026\t\t\t\t4\tP\t0\t\t\t\t5.0\t\t1\t\t"
    "A\t\t5.0\t\t\t\tD\t\t\t",
]
DERIV_HEADER = (
    "ACCESSION_NUMBER\tDERIV_TRANS_SK\tSECURITY_TITLE\tSECURITY_TITLE_FN\tCONV_EXERCISE_PRICE\t"
    "CONV_EXERCISE_PRICE_FN\tTRANS_DATE\tTRANS_DATE_FN\tDEEMED_EXECUTION_DATE\t"
    "DEEMED_EXECUTION_DATE_FN\tTRANS_FORM_TYPE\tTRANS_CODE\tEQUITY_SWAP_INVOLVED\t"
    "EQUITY_SWAP_TRANS_CD_FN\tTRANS_TIMELINESS\tTRANS_TIMELINESS_FN\tTRANS_SHARES\t"
    "TRANS_SHARES_FN\tTRANS_TOTAL_VALUE\tTRANS_TOTAL_VALUE_FN\tTRANS_PRICEPERSHARE\t"
    "TRANS_PRICEPERSHARE_FN\t"
    "TRANS_ACQUIRED_DISP_CD\tTRANS_ACQUIRED_DISP_CD_FN\tEXCERCISE_DATE\tEXCERCISE_DATE_FN\t"
    "EXPIRATION_DATE\tEXPIRATION_DATE_FN\tUNDLYNG_SEC_TITLE\tUNDLYNG_SEC_TITLE_FN\t"
    "UNDLYNG_SEC_SHARES\tUNDLYNG_SEC_SHARES_FN\tUNDLYNG_SEC_VALUE\tUNDLYNG_SEC_VALUE_FN\t"
    "SHRS_OWND_FOLWNG_TRANS\tSHRS_OWND_FOLWNG_TRANS_FN\tVALU_OWND_FOLWNG_TRANS\t"
    "VALU_OWND_FOLWNG_TRANS_FN\tDIRECT_INDIRECT_OWNERSHIP\tDIRECT_INDIRECT_OWNERSHIP_FN\t"
    "NATURE_OF_OWNERSHIP\tNATURE_OF_OWNERSHIP_FN"
)
DERIV = [
    DERIV_HEADER,
    "0000000001-26-000001\t1\tRestricted Stock Unit\t\t\tF1\t29-JUL-2026\t\t\t\t4\tM\t0\t\t\t\t"
    "330.0\t\t\t\t\tF1\tD\t\t\tF2\t\tF2\tCommon Stock\t\t330.0\t\t\t\t1982.0\t\t\t\tD\t\t\t",
]


def _zip(**files: list[str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, rows in files.items():
            archive.writestr(f"{name}.tsv", "\n".join(rows) + "\n")
    return buffer.getvalue()


DATA = _zip(SUBMISSION=SUBMISSION, NONDERIV_TRANS=NONDERIV, DERIV_TRANS=DERIV)


def test_a_quarter_is_written_like_2026q3() -> None:
    assert quarter_dates("2026q3") == (date(2026, 7, 1), date(2026, 9, 30))
    assert quarter_dates("2025q4") == (date(2025, 10, 1), date(2025, 12, 31))
    assert zip_name("2026q1") == "2026q1_form345.zip"
    with pytest.raises(ValueError, match="written like 2026q3"):
        quarter_dates("2026-Q3")


def test_the_landing_page_links_each_quarters_zip_under_its_own_folder() -> None:
    page = (
        b'<td><a href="/files/datastandardsinnovation/data/insider-transactions-data-sets/'
        b'2026q3_form345.zip" download>2026 Q3 345</a></td>'
        b'<td><a href="/files/structureddata/data/insider-transactions-data-sets/'
        b'2026q1_form345.zip" download>2026 Q1 345</a></td>'
    )

    assert data_set_link(page, "2026q1") == (
        "/files/structureddata/data/insider-transactions-data-sets/2026q1_form345.zip"
    )
    assert data_set_link(page, "2026q2") is None


def test_the_forms_4_of_the_issuers_asked_for_are_read_with_both_tables() -> None:
    filings = read_data_set(DATA, [320193])

    assert list(filings) == ["0000000001-26-000001"]  # the Form 3 and the other issuer left out
    filing = filings["0000000001-26-000001"]
    assert (filing.form, filing.filed_at, filing.rule_10b5_1) == ("4", date(2026, 7, 31), True)
    sale, rsu = filing.lines
    assert (sale.is_derivative, sale.code, sale.shares, sale.price, sale.owned_after) == (
        False,
        "S",
        Decimal("151.0"),
        Decimal("343.08"),
        Decimal("39417.0"),
    )
    assert (rsu.is_derivative, rsu.security_title, rsu.price) == (
        True,
        "Restricted Stock Unit",
        None,
    )


def test_a_row_with_the_wrong_number_of_fields_is_refused() -> None:
    broken = _zip(SUBMISSION=[SUBMISSION[0], "0000000001-26-000001\t31-JUL-2026"])

    with pytest.raises(SecFileError, match="SUBMISSION.tsv: a row has 2 fields, not 14"):
        read_data_set(broken, [320193])


def _line(line_no: int, code: str, shares: str, price: str | None, owned: str) -> Line:
    return Line(
        line_no=line_no,
        is_derivative=code == "M",
        security_title="Restricted Stock Unit" if code == "M" else "Common Stock",
        trade_date=date(2026, 7, 29),
        code=code,
        acquired_disposed="D",
        shares=Decimal(shares),
        price=Decimal(price) if price is not None else None,
        owned_after=Decimal(owned),
        direct_indirect="D",
        nature_of_ownership=None,
        timeliness=None,
    )


def _ours(*lines: Line, box: bool | None = True) -> dict[str, OurFiling]:
    accession = "0000000001-26-000001"
    return {accession: OurFiling(accession, 320193, "4", date(2026, 7, 31), box, lines)}


def test_the_same_trades_match_whatever_their_trailing_zeros_and_order() -> None:
    ours = _ours(_line(2, "M", "330", None, "1982"), _line(1, "S", "151", "343.08", "39417"))

    [check] = reconcile(ours, read_data_set(DATA, [320193]))

    assert (check.status, check.lines, check.differences) == ("same", 2, ())


def test_a_trade_or_box_that_differs_is_listed_from_both_sides() -> None:
    ours = _ours(
        _line(1, "S", "151", "343.80", "39417"), _line(2, "M", "330", None, "1982"), box=False
    )

    [check] = reconcile(ours, read_data_set(DATA, [320193]))

    assert check.status == "differs"
    assert check.differences == (
        "10b5-1 box: ours False, the data set's True",
        "only in our parse: table I common stock 2026-07-29 S D shares 151 price 343.80 holdings "
        "after 39417 D",
        "only in the data set: table I common stock 2026-07-29 S D shares 151.0 price 343.08 "
        "holdings after 39417.0 D",
    )


def test_a_filing_on_one_side_only_is_listed() -> None:
    other = {
        "0000000001-26-000009": OurFiling(
            "0000000001-26-000009", 320193, "4", date(2026, 8, 3), None, ()
        )
    }

    checks = reconcile(other, read_data_set(DATA, [320193]))

    assert [(check.accession_no, check.status) for check in checks] == [
        ("0000000001-26-000001", "only_data_set"),
        ("0000000001-26-000009", "only_ours"),
    ]


def test_trades_that_differ_only_by_the_data_sets_rounding_are_rounded_not_different() -> None:
    """The data set writes Meta's 602.7466 as 602.75 and Walmart's 52,458.807 shares as
    52,458.81."""
    ours = _ours(_line(1, "S", "151.004", "343.0751", "39417"), _line(2, "M", "330", None, "1982"))

    [check] = reconcile(ours, read_data_set(DATA, [320193]))

    assert (check.status, check.differences) == ("rounded", ())
