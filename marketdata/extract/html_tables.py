"""HTML tables to grids, and a document's text with its tables as markdown (E7a-S2, §9.9).

Pure: takes the bytes of a stored document and returns its text and its tables.

Press release tables come from a handful of document generators, and they lay one printed
column out as several HTML cells: the `$` sign, the number, and a closing parenthesis or `%`
sign each get a cell, spacer cells sit between columns, and labels and headings span several
cells. A table becomes a grid in four steps:

1. Cells are placed on a physical grid, with colspan and rowspan expanded.
2. Within a row, a cell holding only `$` (or `(`) joins the value after it, and a cell holding
   only `)` or `%` joins the value before it.
3. The values of the rows that have values are grouped into printed columns: values in
   different rows whose physical spans overlap are in the same column. Rows without values, or
   with only years, take no part: a heading such as "Three Months Ended" spans several columns.
4. Every row becomes a label and one cell per printed column; a heading is placed in every
   column it spans.

Cells keep their text as printed: parentheses mark negatives and dashes nil, and are read as
numbers by the extractor and, separately, by verification (D-015). A label keeps its footnote
markers; `Row.clean_label` drops them for matching.

The document's text keeps paragraphs as lines and writes each table as markdown rows,
`| label | cell | cell |`. That text is what `documents.text` stores and what verification
reads, so each row's `line` is exactly a line of it.
"""

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from lxml import html
from lxml.html import HtmlElement

CONTEXT_LINES = 8  # lines of text kept before each table: titles and scale notes
MAX_SPAN = 100  # a colspan or rowspan beyond this is a generator's error; it counts as 1

_SKIP = frozenset({"script", "style", "head", "noscript", "template"})
_BLOCK = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "body",
        "br",
        "caption",
        "center",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "html",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "ul",
    }
)
# Superscripts that are part of the word, not footnote markers: 1st, 2nd, TM.
_SUPERSCRIPT_WORDS = frozenset({"st", "nd", "rd", "th", "tm", "®", "©"})

_SPACE = re.compile(r"[\s   -     　]+")
_INVISIBLE = re.compile(r"[­​‌‍⁠﻿]")
_XML_DECLARATION = re.compile(r"^\s*<\?xml[^>]*\?>", re.IGNORECASE)
_CURRENCY = re.compile(r"^(?:US|C|A)?[$€£¥]$")
_OPENING = frozenset({"(", "$(", "($", "$ ("})
_CLOSING = re.compile(r"^(?:\)|%|\)%|%\)|\)\s*%|pts?\.?|bps)$", re.IGNORECASE)
# A printed value: a number with its decorations, a dash for nil, or "not meaningful".
_DECORATION = re.compile(r"[\s$€£¥()%\[\]*]|US\$|(?<![A-Za-z])[xX](?![A-Za-z])")
_DIGITS = re.compile(r"^[-−–]?(?:\d{1,3}(?:,\d{3})+|\d+)?(?:\.\d+)?$")
_NIL = re.compile(r"^(?:[$€£¥]\s*)?[-—–‒―−]+\s*%?$")
_NOT_MEANINGFUL = re.compile(r"^(?:n\s*/\s*m|nm|n\s*/\s*a|nmf|\*+)$", re.IGNORECASE)
_YEAR = re.compile(r"^(?:19|20)\d{2}$")
# Footnote markers at the end of a label: (1), (a), (1)(2), *, †, ‡.
_TRAILING_MARKERS = re.compile(r"(?:\s*(?:\(\s*[0-9a-z]{1,2}\s*\)|\*+|[†‡§¶]+))+$", re.IGNORECASE)


@dataclass(frozen=True)
class Row:
    """One table row: its label and one cell per printed column, as printed."""

    label: str  # footnote markers included, e.g. "Total net sales (1)"
    cells: tuple[str, ...]  # "" for an empty cell; `$` and `%` joined to their numbers
    footnotes: tuple[str, ...]  # the label's superscript markers, e.g. ("1",)
    has_values: bool  # holds a number, a dash or "n/m" (years alone don't count)
    line: str  # the row as written in the document text

    @property
    def clean_label(self) -> str:
        """The label without footnote markers, for matching."""
        label = self.label
        for marker in self.footnotes:
            if label.endswith(marker):
                label = label[: -len(marker)].rstrip()
        return _TRAILING_MARKERS.sub("", label).strip()


@dataclass(frozen=True)
class Table:
    index: int  # position among the document's tables, from 0
    rows: tuple[Row, ...]
    width: int  # printed columns besides the label
    context: tuple[str, ...]  # the lines of text just before the table: titles, scale notes

    @property
    def lines(self) -> tuple[str, ...]:
        return tuple(row.line for row in self.rows)


@dataclass(frozen=True)
class ParsedDocument:
    text: str  # paragraphs as lines, tables as markdown rows
    tables: tuple[Table, ...]  # tables with at least one row, in document order


def parse_document(name: str, data: bytes) -> ParsedDocument:
    """A stored document: HTML by its tables and text, anything else (.txt) as plain text."""
    if name.lower().endswith((".htm", ".html", ".xhtml")):
        return parse_html(data)
    lines = (_collapse(line) for line in decode(data).splitlines())
    return ParsedDocument(text="\n".join(line for line in lines if line), tables=())


def parse_html(data: bytes) -> ParsedDocument:
    source = _XML_DECLARATION.sub("", decode(data), count=1)
    if not source.strip():
        return ParsedDocument(text="", tables=())
    root = html.document_fromstring(source)
    writer = _Writer()
    _walk(root, writer)
    writer.end_line()
    return ParsedDocument(text="\n".join(writer.lines), tables=tuple(writer.tables))


def decode(data: bytes) -> str:
    """UTF-8 when the bytes are valid UTF-8, else Windows-1252: EDGAR documents are ASCII with
    character references, UTF-8, or (older ones) Windows-1252 without saying which."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def is_value(text: str) -> bool:
    """A printed value: a number (with `$`, `%`, parentheses), a dash for nil, or "n/m"."""
    if _NIL.match(text) or _NOT_MEANINGFUL.match(text):
        return True
    core = _DECORATION.sub("", text)
    return bool(core) and any(ch.isdigit() for ch in core) and bool(_DIGITS.match(core))


def is_year(text: str) -> bool:
    return bool(_YEAR.match(text))


def markdown_row(cells: Sequence[str]) -> str:
    return "| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |"


class _Writer:
    """Collects the document's lines and tables while the tree is walked."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.tables: list[Table] = []
        self._buffer: list[str] = []
        self._context: list[str] = []  # lines since the last table with values

    def write(self, text: str) -> None:
        self._buffer.append(text)

    def end_line(self) -> None:
        line = _collapse("".join(self._buffer))
        self._buffer = []
        if line:
            self.lines.append(line)
            self._context.append(line)

    def table(self, element: HtmlElement) -> None:
        self.end_line()
        rows, width = _grid(element)
        if not rows:
            return
        context = tuple(self._context[-CONTEXT_LINES:])
        self.tables.append(Table(len(self.tables), tuple(rows), width, context))
        last_heading = _last_heading(rows)
        for position, row in enumerate(rows):
            self.lines.append(row.line)
            if position == last_heading:
                self.lines.append(markdown_row(["---"] * (width + 1)))
        if any(row.has_values for row in rows):
            self._context = []
        else:
            # A table of text (a title block, a caption) is context for the next table.
            self._context.extend(
                " ".join(part for part in (row.label, *row.cells) if part) for row in rows
            )


def _last_heading(rows: Sequence[Row]) -> int | None:
    """The last column-heading row above the first row with values: the markdown header ends
    there. Rows with a label only ("Net sales:") are section labels, not headings."""
    first = next((index for index, row in enumerate(rows) if row.has_values), None)
    if first is None:
        return None
    headings = [index for index in range(first) if any(rows[index].cells)]
    return headings[-1] if headings else None


def _walk(element: HtmlElement, writer: _Writer) -> None:
    tag = _tag(element)
    if tag is None or tag in _SKIP:
        return
    if tag == "table" and not element.xpath(".//table"):
        writer.table(element)
        return
    block = tag in _BLOCK
    if block:
        writer.end_line()
    if element.text:
        writer.write(element.text)
    for child in element:
        _walk(child, writer)
        if child.tail:
            writer.write(child.tail)
    if block:
        writer.end_line()


@dataclass(frozen=True)
class _Item:
    """A non-empty cell on the physical grid: columns [start, end)."""

    start: int
    end: int
    text: str
    footnotes: tuple[str, ...]


def _grid(table: HtmlElement) -> tuple[list[Row], int]:
    physical = [_merge(items) for items in _physical_rows(table)]
    physical = [items for items in physical if items]
    columns = _columns(physical)
    if not columns:
        return _unaligned(physical)
    rows = []
    for items in physical:
        has_values = any(is_value(item.text) and not is_year(item.text) for item in items)
        label: list[_Item] = []
        cells: list[list[str]] = [[] for _ in columns]
        for item in items:
            if not is_value(item.text) and item.start < columns[0][0]:
                label.append(item)
                continue
            for column in _placement(item, columns, spread=not has_values):
                cells[column].append(item.text)
        texts = [" ".join(texts) for texts in cells]
        label_text = " ".join(item.text for item in label)
        footnotes = tuple(marker for item in label for marker in item.footnotes)
        rows.append(
            Row(label_text, tuple(texts), footnotes, has_values, markdown_row([label_text, *texts]))
        )
    return rows, len(columns)


def _unaligned(physical: list[list[_Item]]) -> tuple[list[Row], int]:
    """A table without values (a title block, a list): its cells in order."""
    width = max((len(items) - 1 for items in physical), default=0)
    rows = []
    for items in physical:
        label, *rest = [item.text for item in items]
        cells = tuple(rest + [""] * (width - len(rest)))
        rows.append(Row(label, cells, items[0].footnotes, False, markdown_row([label, *cells])))
    return rows, width


def _physical_rows(table: HtmlElement) -> list[list[_Item]]:
    rows: list[list[_Item]] = []
    taken: set[tuple[int, int]] = set()  # (row, column) covered by a rowspan from above
    for number, tr in enumerate(table.iter("tr")):
        items: list[_Item] = []
        column = 0
        for cell in tr:
            if _tag(cell) not in ("td", "th"):
                continue
            while (number, column) in taken:
                column += 1
            across, down = _span(cell.get("colspan")), _span(cell.get("rowspan"))
            for below in range(number + 1, number + down):
                taken.update((below, spanned) for spanned in range(column, column + across))
            text, footnotes = _cell_text(cell)
            if text:
                items.append(_Item(column, column + across, text, footnotes))
            column += across
        rows.append(items)
    return rows


def _merge(items: list[_Item]) -> list[_Item]:
    """Join `$` and `(` cells to the value after them, `)` and `%` cells to the one before."""
    merged: list[_Item] = []
    waiting: _Item | None = None
    for item in items:
        if _CURRENCY.match(item.text) or item.text in _OPENING:
            waiting = item if waiting is None else _joined(waiting, item, " ")
            continue
        if waiting is not None:
            item = _joined(waiting, item, " " if _CURRENCY.match(waiting.text) else "")
            waiting = None
        if _CLOSING.match(item.text) and merged and is_value(merged[-1].text):
            merged[-1] = _joined(merged[-1], item, "" if item.text[0] in ")%" else " ")
            continue
        merged.append(item)
    if waiting is not None:
        merged.append(waiting)
    return merged


def _joined(first: _Item, second: _Item, separator: str) -> _Item:
    return _Item(
        min(first.start, second.start),
        max(first.end, second.end),
        f"{first.text}{separator}{second.text}",
        first.footnotes + second.footnotes,
    )


def _columns(physical: list[list[_Item]]) -> list[tuple[int, int]]:
    """Printed columns: the overlapping physical spans of values in rows that have values."""
    spans = sorted(
        (item.start, item.end)
        for items in physical
        if any(is_value(item.text) and not is_year(item.text) for item in items)
        for item in items
        if is_value(item.text)
    )
    columns: list[tuple[int, int]] = []
    for start, end in spans:
        if columns and start < columns[-1][1]:
            columns[-1] = (columns[-1][0], max(columns[-1][1], end))
        else:
            columns.append((start, end))
    return columns


def _placement(item: _Item, columns: list[tuple[int, int]], *, spread: bool) -> Iterator[int]:
    """The printed columns an item sits in. A heading (`spread`) goes in every column it
    overlaps; a value in the one it overlaps most. An item between columns goes to the
    nearest."""
    overlaps = [
        (min(item.end, end) - max(item.start, start), index)
        for index, (start, end) in enumerate(columns)
    ]
    covered = [index for overlap, index in overlaps if overlap > 0]
    if covered:
        yield from (covered if spread else [max(overlaps)[1]])
        return
    middle = (item.start + item.end) / 2
    yield min(
        range(len(columns)),
        key=lambda index: abs((columns[index][0] + columns[index][1]) / 2 - middle),
    )


def _cell_text(cell: HtmlElement) -> tuple[str, tuple[str, ...]]:
    parts: list[str] = []
    footnotes: list[str] = []
    _cell_parts(cell, parts, footnotes)
    return _collapse("".join(parts)), tuple(footnotes)


def _cell_parts(element: HtmlElement, parts: list[str], footnotes: list[str]) -> None:
    tag = _tag(element)
    if tag is None or tag in _SKIP:
        return
    if tag == "sup":
        marker = _collapse(element.text_content())
        if marker.lower() in _SUPERSCRIPT_WORDS:
            parts.append(marker)
        elif marker:
            parts.append(f" {marker} ")
            footnotes.append(marker)
        return
    if tag in _BLOCK:
        parts.append(" ")
    if element.text:
        parts.append(element.text)
    for child in element:
        _cell_parts(child, parts, footnotes)
        if child.tail:
            parts.append(child.tail)
    if tag in _BLOCK:
        parts.append(" ")


def _span(value: str | None) -> int:
    try:
        span = int((value or "1").strip())
    except ValueError:
        return 1
    return span if 1 <= span <= MAX_SPAN else 1


def _tag(element: HtmlElement) -> str | None:
    """The element's tag in lower case; None for comments and processing instructions."""
    tag = element.tag
    return tag.lower() if isinstance(tag, str) else None


def _collapse(text: str) -> str:
    return _SPACE.sub(" ", _INVISIBLE.sub("", text)).strip()
