"""Period columns of a press release table (E7a-S3, PLAN.md §9.9, D-073).

Pure. A column's headings say how long its period is and when it ended, in many spellings:
"Three Months Ended" over "June 27, 2026"; "12 Weeks Ended" (Costco's quarters); "Q3 FY26";
"3Q26"; "Third Quarter" over "2026"; "THREE MONTHS ENDED" over "8/31/2026".

- A quarter is three months, a quarter, or 12 to 17 weeks (Costco's 12- and 16-week quarters,
  the 14-week fourth quarter of a 53-week year). Six, nine and twelve months, 18 weeks or more,
  years, year-to-date and full-year columns are never taken for quarterly slots.
- Change columns ("% Change", "Y/Y", "O/(U)", "vs") hold no period.
- Columns that compare years side by side ("2025 2026", then "2025 2026" again) are a run, and
  a run has one period length, as one heading spans it (rules version 3). When a column that
  took its length from the label column or the title has another than a column of its run
  naming its own, the headings sit out of line with the columns: the period headings go to
  the runs in the order printed (Rivian). A column of a run without a length takes the run's
  (Accenture's "Three Months Ended" spans only the first of its two years).
- The current quarter is the quarter column that ended last; the prior-year quarter is the
  quarter column that ended 350 to 380 days before it (52- and 53-week years). Without dates,
  the same quarter of the year before ("Q3 FY25" beside "Q3 FY26").
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import Literal

from marketdata.extract.html_tables import Row, Table

type Duration = Literal["quarter", "longer", "unknown"]

PRIOR_YEAR_DAYS = range(350, 381)

_NUMBERS = {
    "three": 3,
    "six": 6,
    "nine": 9,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "twenty-four": 24,
    "twenty four": 24,
    "twenty-six": 26,
    "twenty six": 26,
    "twenty-eight": 28,
    "thirty-six": 36,
    "thirty six": 36,
    "thirty-nine": 39,
    "thirty nine": 39,
    "forty": 40,
    "fifty-two": 52,
    "fifty two": 52,
    "fifty-three": 53,
    "fifty three": 53,
}
_COUNT = r"(?P<count>\d{1,2}|" + "|".join(sorted(_NUMBERS, key=len, reverse=True)) + ")"
_SPAN = re.compile(_COUNT + r"[\s-]+(?P<unit>months?|weeks?)\b", re.IGNORECASE)
_BOTH = re.compile(
    r"\b(three|six|nine|twelve|\d{1,2})\s+and\s+(three|six|nine|twelve|\d{1,2})\s+(months|weeks)\b",
    re.IGNORECASE,
)
_LONGER = re.compile(
    r"\byears?\s+ended\b|\byears?\s+ending\b|\bfiscal\s+years?\b|\bfull[\s-]+year\b"
    r"|\byear[\s-]+to[\s-]+date\b|\bytd\b|\btwelve\s+months\b|\bannual\b",
    re.IGNORECASE,
)
_QUARTER_WORDS = re.compile(
    r"\bquarters?\b|\bqtrs?\b\.?|\bq[1-4]\b|\bfq[1-4]\b|\b[1-4]q(?:\s*'?\d{2,4})?\b"
    r"|\bq[1-4](?=[\s-]?(?:fy)?'?\d)",
    re.IGNORECASE,
)
_FISCAL_YEAR_ONLY = re.compile(r"^\s*(?:fy|fiscal)\s*'?\d{2,4}\s*$", re.IGNORECASE)
# Columns that hold no period of their own: changes, and ratios such as "Percent to Sales".
_CHANGE = re.compile(
    r"\bchange\b|%|\bq/q\b|\by/y\b|\byoy\b|\byr/yr\b|\bvs\.?\b|\bversus\b|o/\(u\)|b/\(w\)"
    r"|\bvariance\b|\bincrease\b|\bdecrease\b|\bgrowth\b|\bbetter\b|\bworse\b|\bpercent"
    r"|\bmargin\b|\bratio\b",
    re.IGNORECASE,
)
_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_MONTH = (
    r"(?P<month>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
)
_MONTH_DAY_YEAR = re.compile(
    _MONTH + r"\s+(?P<day>\d{1,2})\s*,?\s*(?P<year>(?:19|20)\d{2})\b", re.IGNORECASE
)
_MONTH_DAY = re.compile(_MONTH + r"\s+(?P<day>\d{1,2})\b", re.IGNORECASE)
_ENDED_MONTH_DAY = re.compile(
    r"\b(?:months?|weeks?|quarters?)\s+end(?:ed|ing)\s+(?P<when>"
    + _MONTH.replace("?P<month>", "?:")
    + r"\s+\d{1,2})\b",
    re.IGNORECASE,
)
_NUMERIC_DATE = re.compile(r"\b(?P<month>\d{1,2})/(?P<day>\d{1,2})/(?P<year>\d{4}|\d{2})\b")
_ISO_DATE = re.compile(r"\b(?P<year>(?:19|20)\d{2})-(?P<month>\d{2})-(?P<day>\d{2})\b")
_YEAR = re.compile(r"(?<![\d/.-])(?P<year>(?:19|20)\d{2})(?![\d/-])")
# "Q3 FY26", "Q3 2026", "Q3-2026", "3Q26", "3Q 2026", "Third Quarter 2026", "Q3 FY 2026".
_QUARTER_YEAR = re.compile(
    r"\bf?q(?P<q1>[1-4])\s*[-' ]?\s*(?:fy|fiscal)?\s*'?(?P<y1>\d{4}|\d{2})\b"
    r"|\b(?P<q2>[1-4])q\s*'?(?P<y2>\d{4}|\d{2})\b"
    r"|\b(?P<w3>first|second|third|fourth)\s+quarter\s+(?:of\s+)?(?:fiscal\s+)?(?:year\s+)?"
    r"(?P<y3>\d{4})\b",
    re.IGNORECASE,
)
_QUARTER_ONLY = re.compile(
    r"\bf?q(?P<q1>[1-4])\b|\b(?P<q2>[1-4])q\b"
    r"|\b(?P<w3>first|second|third|fourth|1st|2nd|3rd|4th)\s+(?:quarter|qtr)\b",
    re.IGNORECASE,
)
_ORDINALS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "1st": 1,
    "2nd": 2,
    "3rd": 3,
    "4th": 4,
}


@dataclass(frozen=True)
class Column:
    index: int  # position among the table's printed columns
    heading: str  # its headings, top to bottom, as printed
    duration: Duration
    end: date | None  # the period's last day, when the headings give it
    year: int | None  # the year the headings name (fiscal or calendar)
    quarter: int | None  # the quarter they name: "Q3", "3Q", "Third Quarter"
    change: bool  # a change column: no period of its own


@dataclass(frozen=True)
class Periods:
    columns: tuple[Column, ...]
    current: Column | None  # the current quarter
    prior: Column | None  # the same quarter a year earlier
    problem: str | None  # why there is no current or prior column
    note: str | None = None  # how the headings were read, when not as placed


def table_periods(table: Table, context: Sequence[str] = ()) -> Periods:
    """The period of each column of `table`, and its current and prior-year quarters.

    `context` is the table's title lines. They and the headings in the label column ("Three
    Months Ended March 31," above a row of years) apply to every column: a column whose own
    headings name no duration, or no month and day, takes them from there."""
    headings = heading_rows(table)
    shared_text = " ".join([*context, *title_lines(table)]).strip()
    shared = _duration(shared_text) if shared_text else "unknown"
    columns = [
        _column(index, _own_heading(headings, index), shared, shared_text)
        for index in range(table.width)
    ]
    columns, note, problem = _line_up(columns, headings, shared_text)
    if problem is not None:
        return Periods(tuple(columns), None, None, problem)
    current, prior, problem = _choose(columns)
    return Periods(tuple(columns), current, prior, problem, note)


def heading_rows(table: Table) -> list[Row]:
    """The rows above the first row with values (titles, headings, section labels)."""
    first = next((index for index, row in enumerate(table.rows) if row.has_values), None)
    return list(table.rows[: first if first is not None else len(table.rows)])


def title_lines(table: Table) -> list[str]:
    """Text in the label column above the last column-heading row: a title ("CONDENSED
    CONSOLIDATED STATEMENTS OF INCOME"), a scale, a period ("Three Months Ended March 31,").
    Label-only rows after the column headings are section labels ("REVENUE BY SEGMENT")."""
    headings = heading_rows(table)
    last = max((index for index, row in enumerate(headings) if any(row.cells)), default=-1)
    return [row.label for row in headings[: last + 1] if row.label]


def heading_date(text: str, year_hint: int | None = None) -> date | None:
    """A period end date in heading text: "June 27, 2026", "8/31/2026", "2026-06-27", or
    "June 27," with the year elsewhere in the text (or `year_hint`)."""
    for pattern in (_MONTH_DAY_YEAR, _NUMERIC_DATE, _ISO_DATE):
        match = pattern.search(text)
        if match is not None:
            found = _date(match)
            if found is not None:
                return found
    partial = _MONTH_DAY.search(text)
    if partial is None:
        return None
    years = {int(match.group("year")) for match in _YEAR.finditer(text)}
    year = years.pop() if len(years) == 1 else year_hint
    if year is None:
        return None
    return _safe_date(year, _MONTHS[partial.group("month").lower()[:3]], int(partial.group("day")))


def _own_heading(headings: Sequence[Row], index: int, *, periods: bool = True) -> str:
    """A column's headings, top to bottom; without those naming a period length when not
    `periods`."""
    cells = [row.cells[index] for row in headings if row.cells[index]]
    return " ".join(cell for cell in cells if periods or _duration(cell) == "unknown").strip()


def _column(index: int, heading: str, shared: Duration, shared_text: str) -> Column:
    change = bool(_CHANGE.search(heading))
    duration = _duration(heading)
    if duration == "unknown" and not change:
        duration = shared
    year, quarter = _quarter_year(heading)
    end = heading_date(heading)
    if end is None and year is not None:
        end = _shared_date(shared_text, year)
    if end is not None:
        year = end.year
    return Column(index, heading, duration, end, year, quarter, change)


def _line_up(
    columns: list[Column], headings: Sequence[Row], shared_text: str
) -> tuple[list[Column], str | None, str | None]:
    """Each run of years with one period length (module docstring): the columns, a note when
    the headings were read in the order printed rather than as placed, and the problem when
    the table's periods can't be told."""
    runs = _runs(columns)
    lined = list(columns)
    if not any(_out_of_line(run) for run in runs):
        for run in runs:
            lengths = {column.duration for column in run} - {"unknown"}
            if len(lengths) == 1:
                length = lengths.pop()
                for column in run:
                    lined[column.index] = replace(column, duration=length)
        return lined, None, None
    named = _period_headings(headings)
    if len(named) != len(runs):
        problem = (
            f"the period headings ({len(named)}) don't line up with the {len(runs)} runs of "
            "years in the columns"
        )
        return lined, None, problem
    for heading, run in zip(named, runs, strict=True):
        for column in run:
            own = _own_heading(headings, column.index, periods=False)
            text = f"{heading} {own}".strip()
            lined[column.index] = _column(column.index, text, "unknown", shared_text)
    read = "; ".join(
        f"{heading!r} over {len(run)}" for heading, run in zip(named, runs, strict=True)
    )
    return lined, f"period headings out of line with the columns, read in order: {read}", None


def _out_of_line(run: Sequence[Column]) -> bool:
    """A run with a quarter and a longer column, one of them taking its length from the label
    column or the title. Columns that each name their own ("Fourth Quarter 2025", "Full Year
    2025", "Fourth Quarter 2024") are read as placed."""
    lengths = {column.duration for column in run}
    taken = any(_duration(column.heading) == "unknown" for column in run)
    return {"quarter", "longer"} <= lengths and taken


def _runs(columns: Sequence[Column]) -> list[list[Column]]:
    """Columns that compare years side by side: a new run starts where a year comes again.
    Change columns and columns without a year are in none."""
    runs: list[list[Column]] = []
    for column in columns:
        if column.change or column.year is None:
            continue
        if not runs or column.year in {other.year for other in runs[-1]}:
            runs.append([])
        runs[-1].append(column)
    return runs


def _period_headings(headings: Sequence[Row]) -> list[str]:
    """The headings naming a period length, in the order printed: a heading row's label first,
    then its cells left to right; one heading spanning several columns counts once."""
    named: list[str] = []
    for row in headings:
        previous = None
        for text in (row.label, *row.cells):
            if not text:
                continue
            if text != previous and _duration(text) != "unknown":
                named.append(text)
            previous = text
    return named


def _shared_date(text: str, year: int) -> date | None:
    """The end date a title gives a column of `year`: after "Months Ended", "Weeks Ended" or
    "Quarter Ended", the full date of that year ("For the Three and Nine Months Ended August
    2, 2026 and August 3, 2025"), or the month and day with the column's year ("Three Months
    Ended March 31," above a row of years). A sentence such as "The fiscal year ending January
    31, 2027 is referred to as 2026" gives none."""
    match = _ENDED_MONTH_DAY.search(text)
    if match is None:
        return None
    following = text[match.start("when") : match.start("when") + 80]
    for full in _MONTH_DAY_YEAR.finditer(following):
        found = _date(full)
        if found is not None and found.year == year:
            return found
    return heading_date(f"{match.group('when')} {year}")


def _duration(text: str) -> Duration:
    text = _BOTH.sub(r"\1 \3 and \2 \3", text)  # "Three and Nine Months" names both
    spans = [_months_or_weeks(match) for match in _SPAN.finditer(text)]
    quarters = [span for span in spans if span == "quarter"]
    longer = [span for span in spans if span == "longer"] or _LONGER.findall(text)
    if quarters and not longer:
        return "quarter"
    if longer and not quarters:
        return "longer"
    if quarters or longer:
        return "unknown"  # a heading naming both: can't tell which this column is
    if _QUARTER_WORDS.search(text):
        return "quarter"
    if _FISCAL_YEAR_ONLY.match(text):
        return "longer"
    return "unknown"


def _months_or_weeks(match: re.Match[str]) -> Duration:
    count_text = match.group("count").lower()
    count = int(count_text) if count_text.isdigit() else _NUMBERS[count_text]
    if match.group("unit").lower().startswith("month"):
        return "quarter" if count == 3 else "longer" if count in (6, 9, 12) else "unknown"
    return "quarter" if 12 <= count <= 17 else "longer" if count >= 18 else "unknown"


def _quarter_year(heading: str) -> tuple[int | None, int | None]:
    match = _QUARTER_YEAR.search(heading)
    if match is not None:
        quarter = match.group("q1") or match.group("q2")
        word = match.group("w3")
        number = int(quarter) if quarter else _ORDINALS[word.lower()]
        year_text = match.group("y1") or match.group("y2") or match.group("y3")
        year = int(year_text) if len(year_text) == 4 else 2000 + int(year_text)
        return year, number
    only = _QUARTER_ONLY.search(heading)
    years = {int(match.group("year")) for match in _YEAR.finditer(heading)}
    named = years.pop() if len(years) == 1 else None
    if only is not None:
        quarter = only.group("q1") or only.group("q2")
        number = int(quarter) if quarter else _ORDINALS[only.group("w3").lower()]
        return named, number
    return named, None


def _choose(columns: Sequence[Column]) -> tuple[Column | None, Column | None, str | None]:
    quarters = [column for column in columns if column.duration == "quarter" and not column.change]
    if not quarters:
        return None, None, "no column is a quarter (three months, a quarter, or 12 to 17 weeks)"
    dated = [column for column in quarters if column.end is not None]
    if dated:
        latest = max(column.end for column in dated if column.end is not None)
        current = [column for column in dated if column.end == latest]
        if len(current) > 1:
            return None, None, f"{len(current)} quarter columns end on {latest}"
        prior = [
            column
            for column in dated
            if column.end is not None and (latest - column.end).days in PRIOR_YEAR_DAYS
        ]
        if len(prior) > 1:
            return current[0], None, f"{len(prior)} quarter columns end a year before {latest}"
        return (
            current[0],
            prior[0] if prior else None,
            None if prior else (f"no quarter column ends a year before {latest}"),
        )
    labelled = [column for column in quarters if column.year is not None]
    if not labelled:
        return None, None, "the quarter columns name no date or year"
    latest_key = max((column.year or 0, column.quarter or 0) for column in labelled)
    current = [c for c in labelled if (c.year or 0, c.quarter or 0) == latest_key]
    if len(current) > 1:
        return None, None, f"{len(current)} quarter columns are labelled {current[0].heading!r}"
    year, quarter = latest_key
    prior = [c for c in labelled if c.year == year - 1 and (c.quarter or 0) == quarter]
    if len(prior) != 1:
        return current[0], None, f"no single quarter column for the same quarter of {year - 1}"
    return current[0], prior[0], None


def _date(match: re.Match[str]) -> date | None:
    month_text, day, year = match.group("month"), int(match.group("day")), match.group("year")
    month = int(month_text) if month_text.isdigit() else _MONTHS[month_text.lower()[:3]]
    full_year = int(year) if len(year) == 4 else 2000 + int(year)
    return _safe_date(full_year, month, day)


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None
