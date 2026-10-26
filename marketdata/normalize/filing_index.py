"""A filing's index page, `{accession}-index.htm` (PLAN.md §8, E7a-S1).

Pure: takes the page's bytes and returns what it says. The page gives each document of the
filing with its type (EX-99.1), which the folder's index.json doesn't (it has icon names), and
the time EDGAR accepted the filing, in Eastern time. An earnings release takes its acceptance
time from here, and it is compared with the submissions listing's (D-057).

The press release is an EX-99 exhibit: EX-99.1, else EX-99, then EX-99.2 and on (§9.9).
Exhibits are tried in that order, and the extractor uses the first that holds the income
statement.
"""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime

from lxml import html

from marketdata.normalize.filings import EASTERN
from marketdata.normalize.security_master import SecFileError

# Documents an earnings exhibit can be read from; PDFs, images and spreadsheets are skipped.
TEXT_SUFFIXES = (".htm", ".html", ".txt")

_ACCESSION = re.compile(r"\b(\d{10}-\d{2}-\d{6})\b")
_ITEM = re.compile(r"Item\s+(\d+\.\d+)")
_CIK = re.compile(r"CIK\s*:\s*(\d{1,10})\b")
_FORM = re.compile(r"^Form\s+(.+)$")
_EX99 = re.compile(r"^EX-99(?:\.(\d+))?$", re.IGNORECASE)
_SPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class IndexDocument:
    seq: int | None
    description: str
    name: str  # the file in the filing folder, e.g. a8-kex991q3202606272026.htm
    doc_type: str  # e.g. 8-K, EX-99.1, GRAPHIC
    size: int | None  # as listed; not the served size (EDGE_CASES.md, E1)


@dataclass(frozen=True)
class FilingIndex:
    accession_no: str
    form: str  # e.g. 8-K
    filed_at: date
    accepted_at: datetime  # UTC
    period_of_report: date | None
    items: tuple[str, ...]  # 8-K items, e.g. ("2.02", "9.01")
    documents: tuple[IndexDocument, ...]  # the document format files, in the page's order
    filer_ciks: tuple[int, ...]  # every company the page lists for the filing


def parse_filing_index(data: bytes) -> FilingIndex:
    root = html.document_fromstring(data)
    accession = _ACCESSION.search(_text(root.xpath('//div[@id="secNum"]')))
    if accession is None:
        raise SecFileError("filing index page: no accession number")
    where = f"filing index page of {accession.group(1)}"
    info = _info(root)
    accepted = _moment(info.get("Accepted"))
    filed = _date(info.get("Filing Date"))
    if accepted is None or filed is None:
        raise SecFileError(f"{where}: no Filing Date or Accepted time")
    form = _FORM.match(_text(root.xpath('//div[@id="formName"]/strong')))
    return FilingIndex(
        accession_no=accession.group(1),
        form=form.group(1).strip() if form else "",
        filed_at=filed,
        accepted_at=accepted,
        period_of_report=_date(info.get("Period of Report")),
        items=tuple(_ITEM.findall(info.get("Items", ""))),
        documents=_documents(root, where),
        filer_ciks=tuple(
            int(cik) for cik in _CIK.findall(_text(root.xpath('//span[@class="companyName"]')))
        ),
    )


def earnings_exhibits(index: FilingIndex) -> list[IndexDocument]:
    """The EX-99 exhibits a press release can be in, in the order they are tried (§9.9):
    EX-99.1, EX-99, then EX-99.2 and on."""
    ranked = []
    for document in index.documents:
        match = _EX99.match(document.doc_type)
        if match is None or not document.name.lower().endswith(TEXT_SUFFIXES):
            continue
        number = int(match.group(1)) if match.group(1) else None
        rank = 0 if number == 1 else 1 if number is None else 1 + number
        ranked.append((rank, document.seq or 0, document))
    return [document for *_, document in sorted(ranked, key=lambda item: item[:2])]


def _info(root: html.HtmlElement) -> dict[str, str]:
    """The page's "infoHead" labels and their values; line breaks in a value become spaces."""
    values: dict[str, str] = {}
    for head in root.xpath('//div[@class="infoHead"]'):
        value = head.getnext()
        if value is not None and value.get("class") == "info":
            values[_text([head])] = " ".join(value.itertext())
    return values


def _documents(root: html.HtmlElement, where: str) -> tuple[IndexDocument, ...]:
    tables = root.xpath('//table[@class="tableFile"][@summary="Document Format Files"]')
    if not tables:
        raise SecFileError(f"{where}: no document table")
    documents = []
    for row in tables[0].xpath(".//tr[td]"):
        cells = row.xpath("./td")
        if len(cells) < 5:
            continue
        links = cells[2].xpath(".//a")
        name = _text(links[:1]) or _text(cells[2:3]).split(" ")[0]
        if not name:
            continue
        documents.append(
            IndexDocument(
                seq=_int(_text(cells[0:1])),
                description=_text(cells[1:2]),
                name=name,
                doc_type=_text(cells[3:4]),
                size=_int(_text(cells[4:5])),
            )
        )
    return tuple(documents)


def _text(elements: list[html.HtmlElement]) -> str:
    return _SPACE.sub(" ", " ".join(element.text_content() for element in elements)).strip()


def _moment(value: str | None) -> datetime | None:
    """An Eastern wall-clock time on the page, as UTC. EDGAR accepts filings from 06:00 to
    22:00, so the hour that repeats when daylight saving time ends never occurs."""
    try:
        eastern = datetime.fromisoformat(_SPACE.sub(" ", value or "").strip())
    except ValueError:
        return None
    return eastern.replace(tzinfo=EASTERN).astimezone(UTC)


def _date(value: str | None) -> date | None:
    try:
        return date.fromisoformat((value or "").strip())
    except ValueError:
        return None


def _int(text: str) -> int | None:
    return int(text) if text.isdigit() else None
