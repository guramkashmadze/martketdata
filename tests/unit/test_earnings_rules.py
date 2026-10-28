"""E7a-S3: the rule extractor (PLAN.md §9.9) on real releases and on crafted tables, one per rule.

Real releases come from tests/fixtures/sec/filings: a September year end (Apple), a January
retailer with an EX-99 exhibit (Target), thousands (lululemon), a bank (JPMorgan), 12- and
16-week quarters (Costco), Q4 releases with full-year columns (Apple, Target), XOM's
predecessor, and the label and layout cases the backfill turned up.
"""

from datetime import date
from decimal import Decimal

import pytest
from release_fixtures import fixture_release

from marketdata.extract.earnings_rules import (
    RULES_VERSION,
    OverrideError,
    RuleExtractor,
    parse_number,
    parse_overrides,
)
from marketdata.extract.html_tables import parse_html
from marketdata.extract.interface import ExtractionResult, Release, ReleaseDocument, Slot

M = Decimal(1_000_000)
K = Decimal(1000)
BANK = {"revenue": "SIC 6021: a bank's revenue is not applicable (§9.9, D-072)"}

# cik, accession, revenue, operating income, net income, diluted EPS (current, prior; None:
# not found), period end. Values as printed, times the table's scale.
REAL = [
    # Apple, September year end; its Q4 release has twelve-month columns too.
    (320193, "0000320193-25-000077", (102466, 94930), (32427, 29591), (27466, 14736),
     ("1.85", "0.97"), date(2025, 9, 27), M),
    (320193, "0000320193-26-000018", (109417, 94036), (35695, 28202), (29789, 23434),
     ("2.02", "1.57"), date(2026, 6, 27), M),
    # Target: EX-99, a January year end, 52/53-week quarters.
    (27419, "0000027419-26-000012", (30453, 30915), (1380, 1467), (1046, 1103),
     ("2.30", "2.41"), date(2026, 1, 31), M),
    (27419, "0000027419-26-000034", (26539, 25211), (2560, 1317), (1877, 935),
     ("4.11", "2.05"), date(2026, 8, 1), M),
    # lululemon reports in thousands; its headings ("Second Quarter 2026") give no day.
    (1397187, "0001397187-26-000126", (2415631, 2525219), (453653, 523814), (329223, 370905),
     ("2.92", "3.10"), None, K),
    # Costco: a 12-week and a 16-week quarter.
    (909832, "0000909832-25-000164", (67307, 62151), (2463, 2196), (2001, 1798),
     ("4.50", "4.04"), date(2025, 11, 23), M),
    (909832, "0000909832-26-000084", (95723, 86156), (3801, 3341), (2998, 2610),
     ("6.75", "5.87"), date(2026, 8, 30), M),
    # XOM's predecessor: "Net income/(loss) attributable to ExxonMobil", "Earnings per common
    # share - assuming dilution (U.S. dollars)"; no operating income line.
    (34088, "0000034088-26-000065", (85138, 83130), None, (4183, 7713), ("1.00", "1.76"),
     date(2026, 3, 31), M),
    # XOM under its own CIK: the statement is in EX-99.2.
    (2115436, "0002115436-26-000006", (116017, 81506), None, (14525, 7082), ("3.48", "1.64"),
     date(2026, 6, 30), M),
    # Shopify: total revenues is an unlabeled row closing the "Revenues" section; no EPS line.
    (1594805, "0001594805-26-000046", (3583, 2680), (488, 291), (1502, 906), None,
     date(2026, 6, 30), M),
    # Visa: diluted EPS per class of stock; Class A is the listed one.
    (1403161, "0001403161-26-000103", (11633, 10172), (6877, 6177), (5628, 5272),
     ("2.97", "2.69"), date(2026, 6, 30), M),
    # IBM: "Earnings per share of common stock" > "Assuming dilution" > "TOTAL".
    (51143, "0000051143-26-000093", (17103, 16331), None, (2190, 1744), ("2.30", "1.84"),
     date(2026, 9, 30), M),
    # Coca-Cola: "Diluted Net Income Per Share1", a footnote digit without superscript.
    (21344, "0001628280-26-049922", (13380, 12535), (4672, 4280), (4425, 3810),
     ("1.03", "0.88"), date(2026, 7, 3), M),
    # Salesforce: a page number in the label column; the "as a percentage" copy is skipped.
    (1108524, "0001108524-26-000187", (11345, 10236), (2331, 2332), (3526, 1887),
     ("4.29", "1.96"), date(2026, 7, 31), M),
    # Micron: "4th Qtr." columns, a 14-week quarter.
    (723125, "0000723125-26-000018", (54229, 11315), (43751, 3654), (37701, 3201),
     ("32.87", "2.83"), date(2026, 9, 3), M),
    # Walmart: a safe-harbor paragraph naming non-GAAP measures sits above the statement.
    (104169, "0000104169-26-000145", (187937, 177402), (9383, 7286), (6366, 7026),
     ("0.80", "0.88"), date(2026, 7, 31), M),
    # Deere: the dates are in the title; revenue is the "Total" of "Net Sales and Revenues".
    (315189, "0001104659-26-098904", (12608, 12018), None, (1379, 1289), ("5.10", "4.75"),
     date(2026, 8, 2), M),
    (789019, "0001193125-26-323632", (90007, 76441), (40603, 34323), (35766, 27233),
     ("4.81", "3.65"), date(2026, 6, 30), M),
    (1045810, "0001045810-26-000073", (96221, 46743), (63734, 28440), (59688, 26422),
     ("2.46", "1.08"), date(2026, 7, 26), M),
    # Rivian: the period headings sit one column left of the years (rules version 3).
    (1874178, "0001874178-26-000053", (1658, 1303), (-836, -1114), (-833, -1117),
     ("-0.63", "-0.97"), date(2026, 6, 30), M),
    # Meta: a non-GAAP sentence closes the page above its statement of income, and its
    # highlights put the 2026 column under "2025" (rules version 3).
    (1326801, "0001628280-26-028364", (56311, 42314), (22872, 17555), (26773, 16644),
     ("10.44", "6.43"), date(2026, 3, 31), M),
    # Accenture: "Three Months Ended" spans only the 2025 columns (rules version 3).
    (1467373, "0001467373-25-000221", (18742125, 17689545), (2873698, 2948477),
     (2211561, 2278894), ("3.54", "3.59"), date(2025, 11, 30), K),
]  # fmt: skip


def _check(result: ExtractionResult, slot: Slot, expected: object, scale: Decimal) -> None:
    found = result.slot(slot)
    if expected is None:
        assert found.outcome == "not_found", found
        return
    assert found.outcome == "found", found
    assert found.current is not None and found.prior is not None
    current, prior = expected  # type: ignore[misc]
    if slot == "eps_diluted":
        assert (found.current.value, found.prior.value) == (Decimal(current), Decimal(prior))
        assert found.unit == "USD/shares" and found.current.scale == 1
    else:
        assert (found.current.value, found.prior.value) == (current * scale, prior * scale)
        assert found.unit == "USD" and found.current.scale == scale
    assert found.current.quote.startswith("| ")  # a line of the document text
    assert found.method == "rule"


@pytest.mark.parametrize(
    ("cik", "accession", "revenue", "operating", "net", "eps", "period_end", "scale"), REAL
)
def test_real_releases(
    cik: int,
    accession: str,
    revenue: object,
    operating: object,
    net: object,
    eps: object,
    period_end: date | None,
    scale: Decimal,
) -> None:
    result = RuleExtractor().extract(fixture_release(cik, accession))

    assert result.statement_found and result.income_lines_found
    assert (result.extractor, result.extractor_version) == ("rules", RULES_VERSION)
    assert result.period_end == period_end
    for slot, expected in zip(
        ("revenue", "operating_income", "net_income", "eps_diluted"),
        (revenue, operating, net, eps),
        strict=True,
    ):
        _check(result, slot, expected, scale)  # type: ignore[arg-type]
    for document in fixture_release(cik, accession).documents:
        if document.doc_name == result.doc_name:
            for found in result.slots:
                if found.current is not None:
                    assert found.current.quote in document.text.splitlines()


def test_a_bank_has_no_revenue_slot() -> None:
    release = fixture_release(19617, "0001628280-26-065582", not_applicable=BANK)

    result = RuleExtractor().extract(release)

    assert result.slot("revenue").outcome == "not_applicable"
    assert result.slot("revenue").note == BANK["revenue"]
    assert result.slot("operating_income").outcome == "not_found"  # JPMorgan prints none
    _check(result, "net_income", (15305, 14393), M)
    _check(result, "eps_diluted", ("5.57", "5.07"), M)
    # "3Q26" gives no day; the supplement says "three months ended September 30, 2026".
    assert result.period_end == date(2026, 9, 30)


def test_apples_share_counts_are_in_thousands_and_costcos_row_says_so() -> None:
    apple = RuleExtractor().extract(fixture_release(320193, "0000320193-26-000018"))
    costco = RuleExtractor().extract(fixture_release(909832, "0000909832-26-000084"))

    assert apple.diluted_shares is not None and costco.diluted_shares is not None
    assert apple.diluted_shares.value == Decimal(14_714_676) * K
    assert costco.diluted_shares.value == Decimal(444_364) * K  # "Shares used … (000's)"


@pytest.mark.parametrize(
    ("cik", "accession"),
    [(27419, "0000027419-26-000009"), (753308, "0000753308-26-000004")],
    ids=["an announcement", "no exhibit"],
)
def test_a_filing_without_income_lines_has_no_statement(cik: int, accession: str) -> None:
    result = RuleExtractor().extract(fixture_release(cik, accession))

    assert not result.statement_found and not result.income_lines_found
    assert {slot.outcome for slot in result.slots} == {"not_found"}


def test_an_exhibit_printed_as_pictures_may_hold_a_statement() -> None:
    pages = "".join(f'<p><img src="page{n}.jpg"></p>' for n in range(1, 4))
    parsed = parse_html(f"<html><body>{pages}</body></html>".encode())
    document = ReleaseDocument("ex991.htm", "EX-99.1", parsed.text, parsed.tables, parsed.images)
    release = Release(1, "0000000001-26-000001", "8-K", (document,), {}, date(2026, 7, 28), "1231")

    result = RuleExtractor().extract(release)

    # UnitedHealth, Tesla: the rules can't read it, so the release fails rather than being
    # taken for one without a statement (EDGE_CASES.md).
    assert not result.statement_found and result.income_lines_found
    assert result.notes == ("ex991.htm: no tables, 3 pictures",)


# --- Crafted tables -----------------------------------------------------------------------------

HEAD = """<tr><td></td><td>Three Months Ended</td><td>Three Months Ended</td></tr>
<tr><td></td><td>June 30, 2026</td><td>June 30, 2025</td></tr>"""


def _release(
    *tables: str, before: str = "", not_applicable: dict[Slot, str] | None = None
) -> Release:
    html = f"<html><body>{before}" + "".join(f"<table>{body}</table>" for body in tables)
    parsed = parse_html((html + "</body></html>").encode())
    document = ReleaseDocument("ex991.htm", "EX-99.1", parsed.text, parsed.tables)
    return Release(1, "0000000001-26-000001", "8-K", (document,), not_applicable or {},
                   date(2026, 7, 28), "1231")  # fmt: skip


STATEMENT = "<p>Condensed Consolidated Statements of Operations</p><p>(in millions)</p>"


def test_a_statement_split_over_two_tables_is_read_as_one() -> None:
    release = _release(
        HEAD + "<tr><td>Total revenues</td><td>100</td><td>90</td></tr>"
        "<tr><td>Operating income</td><td>20</td><td>18</td></tr>",
        "<tr><td>Net income</td><td>15</td><td>12</td></tr>"
        "<tr><td>Diluted earnings per share</td><td>1.50</td><td>1.20</td></tr>",
        before=STATEMENT,
    )

    result = RuleExtractor().extract(release)

    _check(result, "net_income", (15, 12), M)
    _check(result, "eps_diluted", ("1.50", "1.20"), M)


def test_a_per_diluted_share_row_belongs_to_the_line_above() -> None:
    release = _release(
        HEAD + "<tr><td>Total revenues</td><td>100</td><td>90</td></tr>"
        "<tr><td>Net income</td><td>15</td><td>12</td></tr>"
        "<tr><td>per diluted share</td><td>9.04</td><td>8.24</td></tr>"
        "<tr><td>Core income</td><td>16</td><td>13</td></tr>"
        "<tr><td>per diluted share</td><td>9.18</td><td>8.14</td></tr>",
        before=STATEMENT,
    )  # Travelers

    _check(RuleExtractor().extract(release), "eps_diluted", ("9.04", "8.24"), M)


def test_the_company_share_wins_over_the_total_with_the_minority() -> None:
    release = _release(
        HEAD + "<tr><td>Total revenues</td><td>100</td><td>90</td></tr>"
        "<tr><td>Net loss</td><td>(19)</td><td>(38)</td></tr>"
        "<tr><td>Less: Net loss attributable to non-controlling interest</td><td>13</td>"
        "<td>-</td></tr>"
        "<tr><td>Net loss attributable to common shareholders</td><td>(6)</td><td>(38)</td></tr>",
        before=STATEMENT,
    )  # FuboTV

    _check(RuleExtractor().extract(release), "net_income", (-6, -38), M)


def test_short_negatives_and_net_loss_with_comprehensive_loss() -> None:
    release = _release(
        HEAD + "<tr><td>Revenue</td><td>$ 1,572</td><td>$ 747</td></tr>"
        "<tr><td>Operating income (loss)</td><td>(89)</td><td>113</td></tr>"
        "<tr><td>Net loss and comprehensive loss</td><td>$ (452)</td><td>$ (51)</td></tr>",
        before=STATEMENT,
    )  # CoreWeave

    result = RuleExtractor().extract(release)

    _check(result, "operating_income", (-89, 113), M)
    _check(result, "net_income", (-452, -51), M)


def test_net_loss_attributed_to_the_company_wins_over_the_total() -> None:
    release = _release(
        HEAD + "<tr><td>Total revenues</td><td>$ 42,948</td><td>$ 29,109</td></tr>"
        "<tr><td>Net loss</td><td>(21,351)</td><td>(21,773)</td></tr>"
        "<tr><td>Net income (loss) attributable to redeemable non-controlling interest</td>"
        "<td>346</td><td>(45)</td></tr>"
        "<tr><td>Net loss attributed to Gevo, Inc.</td><td>$ (21,697)</td><td>$ (21,728)</td>"
        "</tr>",
        before=STATEMENT,
    )  # Gevo

    _check(RuleExtractor().extract(release), "net_income", (-21_697, -21_728), M)


def test_the_tables_own_title_decides_over_a_sentence_above_it() -> None:
    highlights = HEAD + (
        "<tr><td>Revenue</td><td>56,311</td><td>42,314</td></tr>"
        "<tr><td>Net income</td><td>26,773</td><td>16,644</td></tr>"
    )
    statement = (
        "<tr><td>CONDENSED CONSOLIDATED STATEMENTS OF INCOME</td><td></td><td></td></tr>"
        "<tr><td>(In millions, except per share amounts)</td><td></td><td></td></tr>" + highlights
    )
    sentence = (
        "<p>For more information on our non-GAAP financial measures and a reconciliation of GAAP"
        " to non-GAAP measures, see the table in this press release.</p><p>4</p>"
    )
    html = (
        "<html><body><p>Financial Highlights</p><p>In millions</p>"
        f"<table>{highlights}</table>{sentence}<table>{statement}</table></body></html>"
    )  # Meta
    parsed = parse_html(html.encode())
    document = ReleaseDocument("ex991.htm", "EX-99.1", parsed.text, parsed.tables)
    release = Release(1, "0000000001-26-000001", "8-K", (document,), {}, date(2026, 7, 28), "1231")

    result = RuleExtractor().extract(release)

    assert result.table_index == 1  # the statement, not the highlights above it
    _check(result, "revenue", (56_311, 42_314), M)


@pytest.mark.parametrize(
    ("ends", "why"),
    [
        (("June 30, 2025", "June 30, 2024"), "ends 2025-06-30, 393 days before the filing"),
        (
            ("December 31, 2026", "December 31, 2025"),
            "ends 2026-12-31, after the filing (2026-07-28)",
        ),
    ],
    ids=["a year early", "an outlook"],
)
def test_a_quarter_far_from_the_filing_is_not_the_releases(ends: tuple[str, str], why: str) -> None:
    release = _release(
        "<tr><td></td><td>Three Months Ended</td><td>Three Months Ended</td></tr>"
        f"<tr><td></td><td>{ends[0]}</td><td>{ends[1]}</td></tr>"
        "<tr><td>Total revenues</td><td>100</td><td>90</td></tr>"
        "<tr><td>Net income</td><td>15</td><td>12</td></tr>",
        before=STATEMENT,
    )  # filed 2026-07-28

    result = RuleExtractor().extract(release)

    # Meta's highlights put the 2026 column under the heading 2025: a quarter that ended more
    # than 130 days before the filing is not the one it reports.
    assert not result.statement_found and result.income_lines_found
    assert result.notes == (f"ex991.htm table 0: its latest quarter column {why}",)


def test_tables_titled_non_gaap_are_never_the_statement() -> None:
    release = _release(
        HEAD + "<tr><td>Revenue</td><td>200</td><td>190</td></tr>"
        "<tr><td>Net income</td><td>50</td><td>40</td></tr>",
        before="<p>Reconciliation of GAAP to Non-GAAP Results</p><p>(in millions)</p>",
    )

    result = RuleExtractor().extract(release)

    assert not result.statement_found and result.income_lines_found


def test_no_scale_fails_the_money_slots_but_not_eps() -> None:
    release = _release(
        HEAD + "<tr><td>Revenue</td><td>200</td><td>190</td></tr>"
        "<tr><td>Net income</td><td>50</td><td>40</td></tr>"
        "<tr><td>Diluted EPS</td><td>0.50</td><td>0.40</td></tr>",
        before="<p>Condensed Consolidated Statements of Operations</p>",
    )

    result = RuleExtractor().extract(release)

    assert result.slot("revenue").outcome == "failed"
    assert result.slot("revenue").flags == ("scale_unknown",)
    _check(result, "eps_diluted", ("0.50", "0.40"), M)


def test_a_bullet_of_highlights_is_not_the_tables_title() -> None:
    release = _release(
        HEAD + "<tr><td>Total revenues</td><td>124</td><td>120</td></tr>"
        "<tr><td>Net income</td><td>25</td><td>21</td></tr>",
        before="<p>•Strong growth in adjusted book value per share, up 14%.</p>"
        "<p>Consolidated Highlights</p><p>($ in millions)</p>",
    )  # Travelers' fourth-quarter release

    _check(RuleExtractor().extract(release), "net_income", (25, 21), M)


def test_a_note_in_the_paragraph_above_may_state_the_scale() -> None:
    paragraph = (
        "Earnings of Acme Inc. and its consolidated subsidiaries for the second quarter and first"
        " six months of 2026 and 2025 are summarized below. Earnings are stated on an after-tax"
        " basis. (Dollar amounts are in millions, except for per share amounts)."
    )
    release = _release(
        HEAD + "<tr><td>Net earnings attributable to Acme shareholders</td><td>25,667</td>"
        "<td>12,370</td></tr>",
        before=f"<p>{paragraph}</p>",
        not_applicable={"revenue": "override: the release presents no revenue line"},
    )  # Berkshire

    result = RuleExtractor().extract(release)

    _check(result, "net_income", (25_667, 12_370), M)
    note = "(Dollar amounts are in millions, except for per share amounts)"
    assert result.slot("net_income").scale_text == note


def test_full_year_columns_are_never_taken_for_a_quarter() -> None:
    release = _release(
        "<tr><td></td><td>Year Ended December 31,</td><td>Year Ended December 31,</td></tr>"
        "<tr><td></td><td>2025</td><td>2024</td></tr>"
        "<tr><td>Total revenues</td><td>160</td><td>17</td></tr>"
        "<tr><td>Net loss</td><td>(50)</td><td>(40)</td></tr>",
        before=STATEMENT,
    )  # Gevo's Q4 release

    result = RuleExtractor().extract(release)

    assert not result.statement_found and result.income_lines_found


def test_a_slot_that_does_not_apply_is_reported_as_such() -> None:
    release = _release(
        HEAD + "<tr><td>Net earnings attributable to Acme shareholders</td><td>25</td>"
        "<td>12</td></tr>",
        before="<p>(dollars in millions)</p>",
        not_applicable={"revenue": "override: the release presents no revenue line"},
    )  # Berkshire's release

    result = RuleExtractor().extract(release)

    assert result.slot("revenue").outcome == "not_applicable"
    _check(result, "net_income", (25, 12), M)


# --- Numbers ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cell", "value"),
    [
        ("1,234", "1234"),
        ("$ 29,789", "29789"),
        ("(89)", "-89"),
        ("$ (0.02)", "-0.02"),
        ("$ 2.31 (1)", "2.31"),
        ("0.75*", "0.75"),
        ("—", "0"),
        ("$ —", "0"),
        ("−3", "-3"),
        ("2,074.5", "2074.5"),
    ],
)
def test_numbers_as_printed(cell: str, value: str) -> None:
    assert parse_number(cell) == Decimal(value)


@pytest.mark.parametrize("cell", ["", "n/m", "N/A", "Three Months Ended"])
def test_cells_without_a_number(cell: str) -> None:
    assert parse_number(cell) is None


# --- Overrides ----------------------------------------------------------------------------------

SOURCE = "https://www.sec.gov/Archives/edgar/data/1/000000000126000001/ex991.htm"


def test_overrides_parse_and_pin_a_row() -> None:
    [override] = parse_overrides(f"""
        [[override]]
        cik = 1
        slot = "net_income"
        row_label = "Net income attributable to Acme"
        reason = "two net income lines; the company's is the second"
        source_url = "{SOURCE}"
    """)
    release = _release(
        HEAD + "<tr><td>Total revenues</td><td>100</td><td>90</td></tr>"
        "<tr><td>Net income</td><td>15</td><td>12</td></tr>"
        "<tr><td>Net income attributable to Acme</td><td>14</td><td>11</td></tr>",
        before=STATEMENT,
    )

    result = RuleExtractor([override]).extract(release)

    _check(result, "net_income", (14, 11), M)
    assert result.overrides == ("net_income: row 'Net income attributable to Acme'",)


def test_an_override_can_pin_the_statement_table() -> None:
    overrides = parse_overrides(f"""
        [[override]]
        cik = 1
        slot = "revenue"
        table = 1
        reason = "the first table is a summary"
        source_url = "{SOURCE}"
    """)
    release = _release(
        HEAD + "<tr><td>Revenue</td><td>1</td><td>1</td></tr>"
        "<tr><td>Net income</td><td>1</td><td>1</td></tr>",
        "<tr><td>(in millions)</td></tr>"
        + HEAD
        + "<tr><td>Revenue</td><td>100</td><td>90</td></tr>"
        "<tr><td>Net income</td><td>15</td><td>12</td></tr>",
        before=STATEMENT,
    )

    result = RuleExtractor(overrides).extract(release)

    assert result.table_index == 1
    _check(result, "revenue", (100, 90), M)


def test_an_empty_overrides_file_means_none() -> None:
    assert parse_overrides("# no overrides yet\n") == ()


@pytest.mark.parametrize(
    ("entry", "problem"),
    [
        ('cik = 1\nslot = "sales"\nrow_label = "x"', "slot must be one of"),
        ('cik = 1\nslot = "revenue"', "needs row_label, table or not_applicable"),
        ('cik = "1"\nslot = "revenue"\nrow_label = "x"', "cik must be a CIK"),
        ('cik = 1\nslot = "revenue"\nrow_label = "x"\nnot_applicable = "y"', "can't be combined"),
        ('cik = 1\nslot = "revenue"\nrow = "x"', "unknown keys"),
    ],
)
def test_override_problems_are_reported(entry: str, problem: str) -> None:
    text = f'[[override]]\n{entry}\nreason = "r"\nsource_url = "{SOURCE}"\n'

    with pytest.raises(OverrideError, match=problem):
        parse_overrides(text)


def test_an_override_needs_a_source() -> None:
    with pytest.raises(OverrideError, match="source_url must be"):
        parse_overrides(
            '[[override]]\ncik = 1\nslot = "revenue"\nrow_label = "x"\nreason = "r"\n'
            'source_url = "www.sec.gov/x"\n'
        )
