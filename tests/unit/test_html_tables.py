"""E7a-S2: HTML tables become grids (spans, `$` and `%` cells, parentheses, dashes, footnote
markers), and a document's text keeps its tables as markdown rows."""

import gzip
from pathlib import Path

import pytest

from marketdata.extract.html_tables import (
    ParsedDocument,
    Row,
    Table,
    is_value,
    parse_document,
    parse_html,
)

FILINGS = Path(__file__).resolve().parents[1] / "fixtures" / "sec" / "filings"
APPLE_RELEASE = FILINGS / "320193" / "0000320193-26-000018" / "a8-kex991q3202606272026.htm.gz"


def _table(body: str, before: str = "") -> Table:
    document = parse_html(f"<html><body>{before}<table>{body}</table></body></html>".encode())
    assert len(document.tables) == 1
    return document.tables[0]


def _row(table: Table, label: str) -> Row:
    return next(row for row in table.rows if row.clean_label == label)


def _apple() -> ParsedDocument:
    return parse_document(
        "a8-kex991q3202606272026.htm", gzip.decompress(APPLE_RELEASE.read_bytes())
    )


def test_colspans_place_values_in_printed_columns() -> None:
    table = _table("""
        <tr><td colspan="3"></td><td colspan="4">Three Months Ended</td></tr>
        <tr><td colspan="3"></td><td colspan="2">2026</td><td colspan="2">2025</td></tr>
        <tr><td colspan="3">Revenue</td><td>$</td><td>100</td><td>$</td><td>90</td></tr>
        <tr><td colspan="3">Cost</td><td colspan="2">60</td><td colspan="2">55</td></tr>
    """)

    assert table.width == 2
    assert [row.cells for row in table.rows] == [
        ("Three Months Ended", "Three Months Ended"),
        ("2026", "2025"),
        ("$ 100", "$ 90"),
        ("60", "55"),
    ]
    assert _row(table, "Revenue").line == "| Revenue | $ 100 | $ 90 |"


def test_rowspans_keep_later_cells_in_their_columns() -> None:
    table = _table("""
        <tr><td rowspan="2">Segment</td><td>Q3</td><td>Q2</td></tr>
        <tr><td>2026</td><td>2026</td></tr>
        <tr><td>Cloud</td><td>12</td><td>11</td></tr>
    """)

    assert [row.cells for row in table.rows] == [("Q3", "Q2"), ("2026", "2026"), ("12", "11")]
    assert table.rows[1].label == ""


def test_currency_and_closing_cells_join_their_numbers() -> None:
    table = _table("""
        <tr><td>Net income (loss)</td><td>$</td><td>(1,234</td><td>)</td><td></td>
            <td>$</td><td>567</td><td></td></tr>
        <tr><td>Margin</td><td></td><td>12.5</td><td>%</td><td></td><td></td><td>(3.0</td><td>)%</td></tr>
        <tr><td>Other</td><td>(</td><td>45</td><td>)</td><td></td><td></td><td>8</td><td></td></tr>
    """)

    assert _row(table, "Net income (loss)").cells == ("$ (1,234)", "$ 567")
    assert _row(table, "Margin").cells == ("12.5%", "(3.0)%")
    assert _row(table, "Other").cells == ("(45)", "8")


def test_dashes_are_values_and_years_alone_are_headings() -> None:
    table = _table("""
        <tr><td></td><td>2026</td><td>2025</td></tr>
        <tr><td>Gain on sale</td><td>&#8212;</td><td>$ &#8212;</td></tr>
    """)

    assert not table.rows[0].has_values
    assert table.rows[1].has_values
    assert table.rows[1].cells == ("—", "$ —")
    assert [is_value(text) for text in ("—", "-", "–", "$ —", "— %", "n/m", "NM")] == [True] * 7


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("1,234", True),
        ("$ (1,234)", True),
        ("(0.12)", True),
        ("12.5%", True),
        ("−3", True),
        ("2026", True),
        ("Three Months Ended", False),
        ("June 27, 2026", False),
        ("(in millions)", False),
        ("Q3", False),
        ("", False),
    ],
)
def test_what_counts_as_a_value(text: str, value: bool) -> None:
    assert is_value(text) is value


def test_footnote_markers_stay_in_labels_and_leave_clean_labels() -> None:
    table = _table("""
        <tr><td>Total net sales<sup>(1)</sup></td><td>100</td></tr>
        <tr><td>Net income attributable to Acme<sup>1</sup></td><td>10</td></tr>
        <tr><td>Operating income (a)</td><td>20</td></tr>
        <tr><td>Diluted EPS*</td><td>0.10</td></tr>
        <tr><td>Net income (loss)</td><td>5</td></tr>
        <tr><td>Revenue in the 3<sup>rd</sup> quarter</td><td>7</td></tr>
    """)

    labels = [(row.label, row.clean_label) for row in table.rows]
    assert labels == [
        ("Total net sales (1)", "Total net sales"),
        ("Net income attributable to Acme 1", "Net income attributable to Acme"),
        ("Operating income (a)", "Operating income"),
        ("Diluted EPS*", "Diluted EPS"),
        ("Net income (loss)", "Net income (loss)"),
        ("Revenue in the 3rd quarter", "Revenue in the 3rd quarter"),
    ]
    assert table.rows[0].footnotes == ("(1)",)


def test_line_breaks_inside_cells_become_spaces() -> None:
    table = _table("""
        <tr><td></td><td>June 27,<br/>2026</td><td><div>June 28,</div><div>2025</div></td></tr>
        <tr><td>Revenue</td><td>1</td><td>2</td></tr>
    """)

    assert table.rows[0].cells == ("June 27, 2026", "June 28, 2025")


def test_text_keeps_paragraphs_and_tables_as_markdown_rows() -> None:
    document = parse_html(b"""<?xml version='1.0' encoding='ASCII'?>
        <html><head><title>ignored</title><style>p {}</style></head><body>
        <p>Acme reports&#160;results</p><div>(in millions)</div>
        <table>
          <tr><td></td><td>Q3 2026</td><td>Q3 2025</td></tr>
          <tr><td>Section:</td><td></td><td></td></tr>
          <tr><td>Revenue</td><td>10</td><td>9</td></tr>
        </table>
        <p>Contact | press</p></body></html>""")

    assert document.text.splitlines() == [
        "Acme reports results",
        "(in millions)",
        "|  | Q3 2026 | Q3 2025 |",
        "| --- | --- | --- |",
        "| Section: |  |  |",
        "| Revenue | 10 | 9 |",
        "Contact | press",
    ]
    assert document.tables[0].context == ("Acme reports results", "(in millions)")


def test_a_table_of_text_is_context_for_the_next_table() -> None:
    document = parse_html(b"""<html><body>
        <table><tr><td>ACME CORP</td></tr><tr><td>(Dollars in thousands)</td></tr></table>
        <table><tr><td>Revenue</td><td>5</td></tr></table>
        <table><tr><td>Assets</td><td>7</td></tr></table>
        </body></html>""")

    assert document.tables[1].context == ("ACME CORP", "(Dollars in thousands)")
    assert document.tables[2].context == ()


def test_a_layout_table_around_tables_is_walked_as_text() -> None:
    document = parse_html(b"""<html><body><table><tr><td><p>Intro</p>
        <table><tr><td>Revenue</td><td>5</td></tr></table></td></tr></table></body></html>""")

    assert [table.rows[0].line for table in document.tables] == ["| Revenue | 5 |"]
    assert document.text.splitlines() == ["Intro", "| Revenue | 5 |"]


def test_windows_1252_bytes_decode() -> None:
    document = parse_html("<p>Net sales — “Total”</p>".encode("cp1252"))

    assert document.text == "Net sales — “Total”"


def test_plain_text_documents_have_no_tables() -> None:
    document = parse_document("ex99.txt", b"  ACME CORP\r\n\r\nRevenue   $  1,234\r\n")

    assert document == ParsedDocument(text="ACME CORP\nRevenue $ 1,234", tables=())


def test_apples_statement_of_operations() -> None:
    document = _apple()
    statement = document.tables[0]

    assert len(document.tables) == 3
    assert statement.width == 4
    assert statement.context[-2:] == (
        "CONDENSED CONSOLIDATED STATEMENTS OF OPERATIONS (Unaudited)",
        "(In millions, except number of shares, which are reflected in thousands, and per-share "
        "amounts)",
    )
    assert statement.rows[0].cells == ("Three Months Ended",) * 2 + ("Nine Months Ended",) * 2
    assert statement.rows[1].cells == (
        "June 27, 2026",
        "June 28, 2025",
        "June 27, 2026",
        "June 28, 2025",
    )
    assert _row(statement, "Total net sales").line == (
        "| Total net sales (1) | 109,417 | 94,036 | 364,357 | 313,695 |"
    )
    assert _row(statement, "Other income/(expense), net").cells == ("572", "(171)", "670", "(698)")
    assert _row(statement, "Net income").cells == ("$ 29,789", "$ 23,434", "$ 101,464", "$ 84,544")
    diluted = [row for row in statement.rows if row.clean_label == "Diluted"]
    assert [row.cells[0] for row in diluted] == ["$ 2.02", "14,714,676"]


def test_every_table_row_is_a_line_of_the_text() -> None:
    document = _apple()
    lines = set(document.text.splitlines())

    rows = [row for table in document.tables for row in table.rows]
    assert len(rows) == 114
    assert all(row.line in lines for row in rows)
    assert "| Proceeds from issuance of term debt, net | — | 4,481 |" in lines


def test_edgars_sgml_wrapper_is_not_part_of_the_document() -> None:
    document = parse_document(
        "ex99.htm",
        b"<DOCUMENT>\n<TYPE>EX-99.1\n<SEQUENCE>2\n<FILENAME>ex99.htm\n<DESCRIPTION>EX-99.1\n"
        b"<TEXT>\n<html><body><p>Acme reports results</p></body></html>\n</TEXT>\n</DOCUMENT>\n",
    )

    assert document.text == "Acme reports results"


def test_headings_land_over_the_columns_whose_values_they_cover_most() -> None:
    # One generator pads the second quarter column's cells (physical columns 6-10) into the
    # span of the next heading (QNT 0001628280-26-055699).
    table = _table("""
        <tr><td colspan="3"></td><td colspan="6">Three Months Ended</td>
            <td colspan="9">Six Months Ended</td></tr>
        <tr><td colspan="3">Revenue</td><td colspan="2">8</td><td></td><td colspan="5">2</td>
            <td></td><td colspan="2">13</td><td></td><td colspan="5">21</td></tr>
    """)

    assert table.rows[0].cells == (
        "Three Months Ended",
        "Three Months Ended",
        "Six Months Ended",
        "Six Months Ended",
    )


def test_pictures_are_counted() -> None:
    document = parse_html(b'<html><body><img src="p1.jpg"/><img src="p2.jpg"/></body></html>')

    assert (document.tables, document.images) == ((), 2)
