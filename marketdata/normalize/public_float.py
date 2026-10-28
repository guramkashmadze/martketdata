"""Universe growth by public float (D-035, D-085; E4a-S5).

Pure. `parse_frame` reads one frames response of the SEC (one fact per filer for a calendar
period); `rank` turns the latest value of each filer into the candidate list for review.

- `dei:EntityPublicFloat` is the market value of the shares non-affiliates held at the end of
  the filer's second fiscal quarter, as its 10-K cover page states it, so a filer that files
  on time has a value measured at most about 21 months ago. One older than two years is stale:
  listed apart, never ranked.
- Frames don't say a filer's class, and only universe companies are classified (D-052). Known
  funds and foreign private issuers are left out; every other filer with a current listed
  ticker is ranked, marked with its class when known and "not checked" otherwise.
- A company without a value of its own takes its predecessor's (D-068): ExxonMobil Holdings'
  first 10-K is still to come, Exxon Mobil Corp's states the float.
- Universe companies with no value at all are listed apart as missing.
- Some cover pages state the float a thousand or a million times too large (Cabot's 2025
  10-K: 4.4 quadrillion dollars). With no prices (D-017), the filer's latest cover-page share
  count is the check: a float that implies more than 10,000 dollars a share, or a float of 0,
  is suspect, listed apart and never ranked; one implying more than 1,000 is ranked with a
  note to check it, since a few shares trade that high (Booking, NVR) (D-085). The 10-K's own
  count is rarely in a frame, which goes to the cover date closest to the quarter's end,
  usually a 10-Q's; share counts move far less than a thousandfold. A filer with no count in
  the frames can't be checked.
"""

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from marketdata.normalize.security_master import SecFileError

PUBLIC_FLOAT = ("dei", "EntityPublicFloat", "USD")  # taxonomy, concept, unit
COVER_SHARES = ("dei", "EntityCommonStockSharesOutstanding", "shares")
STALE_AFTER = timedelta(days=730)
MAX_PRICE = Decimal(10_000)  # dollars a share a float may imply
CHECK_PRICE = Decimal(1_000)  # above this a ranked float carries a note to check its scale
NOT_RANKED = frozenset({"fund", "foreign_private_issuer", "other"})
_ACCESSION = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_FRAME = re.compile(r"^CY\d{4}(Q[1-4]I?)?$")


@dataclass(frozen=True)
class FramePoint:
    cik: int
    entity_name: str | None
    period_start: date | None
    period_end: date
    value: Decimal
    accession_no: str


@dataclass(frozen=True)
class Frame:
    taxonomy: str
    concept: str
    unit: str
    frame: str  # e.g. CY2025Q2I
    points: tuple[FramePoint, ...]
    skipped: int = 0  # points not in the expected shape


def parse_frame(data: bytes) -> Frame:
    try:
        document = json.loads(data, parse_float=Decimal, parse_constant=_no_constant)
    except ValueError as exc:
        raise SecFileError(f"frames JSON is not JSON: {exc}") from None
    if not isinstance(document, dict):
        raise SecFileError("frames JSON: expected an object")
    taxonomy, concept = document.get("taxonomy"), document.get("tag")
    unit, frame, rows = document.get("uom"), document.get("ccp"), document.get("data")
    if not (
        isinstance(taxonomy, str)
        and isinstance(concept, str)
        and isinstance(unit, str)
        and isinstance(frame, str)
        and _FRAME.match(frame)
        and isinstance(rows, list)
    ):
        raise SecFileError("frames JSON has no taxonomy, tag, uom, ccp or data")
    points, skipped = [], 0
    for row in rows:
        point = _point(row)
        if point is None:
            skipped += 1
        else:
            points.append(point)
    return Frame(taxonomy, concept, unit, frame, tuple(points), skipped)


def instant_frames(today: date, count: int = 9) -> list[tuple[int, int]]:
    """The `count` calendar quarters before the one `today` is in, newest first: a value
    measured up to about 27 months ago is in one of their instant frames."""
    year, quarter = today.year, (today.month - 1) // 3 + 1
    quarters = []
    for _ in range(count):
        year, quarter = (year, quarter - 1) if quarter > 1 else (year - 1, 4)
        quarters.append((year, quarter))
    return quarters


@dataclass(frozen=True)
class Filer:
    """What the security master knows about a filer."""

    cik: int
    name: str | None
    filer_type: str  # domestic, fund … or unknown outside the universe
    in_universe: bool
    tickers: tuple[str, ...]  # current listed tickers


@dataclass(frozen=True)
class Candidate:
    rank: int
    cik: int
    tickers: tuple[str, ...]
    name: str | None
    public_float: Decimal
    measured_on: date
    accession_no: str
    in_universe: bool
    filer_type: str
    note: str = ""


@dataclass(frozen=True)
class Unranked:
    cik: int
    tickers: tuple[str, ...]
    name: str | None
    status: str  # stale, suspect or missing
    public_float: Decimal | None
    measured_on: date | None
    accession_no: str | None
    note: str


@dataclass(frozen=True)
class Suggestion:
    candidates: tuple[Candidate, ...]
    unranked: tuple[Unranked, ...]  # stale and suspect values, universe companies without one
    left_out: int  # filers with a value but no current listed ticker, or a known other class


def rank(
    latest: Mapping[int, FramePoint],
    filers: Mapping[int, Filer],
    *,
    today: date,
    top: int,
    predecessors: Mapping[int, Sequence[int]] | None = None,
    shares: Mapping[int, Decimal] | None = None,
) -> Suggestion:
    """The `top` largest listed filers by their latest public float, and the ones that can't be
    ranked (D-035): known funds and foreign issuers are left out, stale and suspect values
    listed apart. `shares` is each filer's latest cover-page share count."""
    predecessors = predecessors or {}
    shares = shares or {}
    ranked: list[Candidate] = []
    unranked: list[Unranked] = []
    left_out = 0
    for cik, filer in sorted(filers.items()):
        point, note = latest.get(cik), ""
        if point is None:
            for earlier in predecessors.get(cik, ()):
                if earlier in latest:
                    point, note = latest[earlier], f"value of predecessor CIK {earlier}"
                    break
        if point is None:
            if filer.in_universe and filer.filer_type == "domestic":
                unranked.append(
                    Unranked(cik, filer.tickers, filer.name, "missing", None, None, None, "")
                )
            continue
        if filer.filer_type in NOT_RANKED or not filer.tickers:
            left_out += 1
            continue
        if point.period_end < today - STALE_AFTER:
            unranked.append(
                Unranked(
                    cik,
                    filer.tickers,
                    filer.name or point.entity_name,
                    "stale",
                    point.value,
                    point.period_end,
                    point.accession_no,
                    note or "no 10-K cover page in the last two years states it",
                )
            )
            continue
        count = shares.get(cik)
        price = point.value / count if count else None
        if point.value <= 0 or (price is not None and price > MAX_PRICE):
            why = (
                "the cover page states a float of 0"
                if point.value <= 0
                else f"{price:,.0f} dollars a share on the latest cover-page share count: a "
                "scale error?"
            )
            unranked.append(
                Unranked(
                    cik,
                    filer.tickers,
                    filer.name or point.entity_name,
                    "suspect",
                    point.value,
                    point.period_end,
                    point.accession_no,
                    why,
                )
            )
            continue
        if price is not None and price > CHECK_PRICE:
            check = f"{price:,.0f} dollars a share on the latest cover-page share count: check"
            note = f"{note}; {check}" if note else check
        ranked.append(
            Candidate(
                0,
                cik,
                filer.tickers,
                filer.name or point.entity_name,
                point.value,
                point.period_end,
                point.accession_no,
                filer.in_universe,
                filer.filer_type if filer.filer_type != "unknown" else "not checked",
                note,
            )
        )
    ranked.sort(key=lambda candidate: (-candidate.public_float, candidate.cik))
    top_ranked = [
        replace(candidate, rank=number) for number, candidate in enumerate(ranked[:top], 1)
    ]
    return Suggestion(tuple(top_ranked), tuple(unranked), left_out)


def latest_points(points: Iterable[FramePoint]) -> dict[int, FramePoint]:
    """Each filer's latest value: the latest period end, ties to the latest accession."""
    latest: dict[int, FramePoint] = {}
    for point in points:
        kept = latest.get(point.cik)
        if kept is None or (point.period_end, point.accession_no) > (
            kept.period_end,
            kept.accession_no,
        ):
            latest[point.cik] = point
    return latest


def _point(row: Any) -> FramePoint | None:
    if not isinstance(row, dict):
        return None
    cik, value, accession = row.get("cik"), row.get("val"), row.get("accn")
    end, start = _date(row.get("end")), _date(row.get("start"))
    if isinstance(cik, str) and cik.isdigit():
        cik = int(cik)
    if isinstance(cik, bool) or not isinstance(cik, int) or cik <= 0:
        return None
    if isinstance(value, bool) or not isinstance(value, int | Decimal):
        return None
    if end is None or not (isinstance(accession, str) and _ACCESSION.match(accession)):
        return None
    name = row.get("entityName")
    return FramePoint(
        cik, name if isinstance(name, str) and name else None, start, end, Decimal(value), accession
    )


def _date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _no_constant(name: str) -> Any:
    raise ValueError(f"{name} is not a number")
