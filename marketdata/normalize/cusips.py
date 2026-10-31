"""CUSIPs from 13F filings mapped to FIGIs and tickers through OpenFIGI (E9-S3, PLAN.md §9.8).

Pure. A CUSIP is asked for as `ID_CUSIP` on the US composite listing (`exchCode` US), so a
listed share gives one FIGI and its ticker. The answer for each CUSIP is:

- `matched`: one FIGI. The position links to our security with that ticker, when we list it.
- `unmatched`: no FIGI (OpenFIGI's "No identifier found": bonds, delisted shares, unlisted
  units), an error for that CUSIP, or a CUSIP whose check digit is wrong (never asked).
- `ambiguous`: more than one FIGI; none is chosen.

Unmatched and ambiguous positions keep the issuer's name as filed, with no security (§9.8).
CUSIPs are licensed by CUSIP Global Services: they are sent only to OpenFIGI and served only as
a 13F filing states them, never as a lookup (D-121).
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

CUSIP_MAP = "cusip_map"  # task kind; key: "pending", the CUSIPs not mapped yet (E9-S3)
PENDING = "pending"

Status = Literal["matched", "unmatched", "ambiguous"]

_VALUES = {
    **{str(digit): digit for digit in range(10)},
    **{chr(ord("A") + index): 10 + index for index in range(26)},
    "*": 36,
    "@": 37,
    "#": 38,
}


def valid_cusip(cusip: str) -> bool:
    """Nine characters whose last is the check digit of the first eight (the modulus 10
    double-add-double rule)."""
    if len(cusip) != 9 or not cusip[8].isdigit() or any(c not in _VALUES for c in cusip[:8]):
        return False
    total = 0
    for position, character in enumerate(cusip[:8]):
        value = _VALUES[character] * (2 if position % 2 else 1)
        total += value // 10 + value % 10
    return (10 - total % 10) % 10 == int(cusip[8])


def mapping_jobs(cusips: Sequence[str]) -> list[dict[str, str]]:
    """One OpenFIGI job per CUSIP, for its US composite listing."""
    return [{"idType": "ID_CUSIP", "idValue": cusip, "exchCode": "US"} for cusip in cusips]


class MappingError(ValueError):
    """An OpenFIGI response that isn't one result per job."""


def parse_mapping(data: bytes, jobs: int) -> list[Mapping[str, Any]]:
    """The results of a mapping response, one per job, in the jobs' order."""
    try:
        results = json.loads(data)
    except ValueError as exc:
        raise MappingError(f"OpenFIGI response is not JSON: {exc}") from None
    if not isinstance(results, list) or len(results) != jobs:
        count = len(results) if isinstance(results, list) else "no list"
        raise MappingError(f"OpenFIGI answered {count} results for {jobs} jobs")
    if not all(isinstance(result, dict) for result in results):
        raise MappingError("OpenFIGI answered a result that is not an object")
    return results


@dataclass(frozen=True)
class CusipMatch:
    """What OpenFIGI said about one CUSIP."""

    cusip: str
    status: Status
    figi: str | None = None
    ticker: str | None = None
    name: str | None = None
    exch_code: str | None = None
    security_type: str | None = None  # OpenFIGI's: Common Stock, ADR, ETP, REIT ...
    market_sector: str | None = None
    candidates: int = 0  # the FIGIs OpenFIGI gave
    note: str | None = None  # why it isn't matched


def invalid(cusip: str) -> CusipMatch:
    return CusipMatch(cusip, "unmatched", note="not a CUSIP: the check digit is wrong")


def decide(cusip: str, result: Mapping[str, Any]) -> CusipMatch:
    """A CUSIP's mapping from its OpenFIGI result."""
    if "error" in result:
        return CusipMatch(cusip, "unmatched", note=f"OpenFIGI: {result['error']}")
    instruments = result.get("data")
    if not isinstance(instruments, list) or not instruments:
        warning = result.get("warning") or "no identifier found"
        return CusipMatch(cusip, "unmatched", note=f"OpenFIGI: {warning}")
    by_figi: dict[str, Mapping[str, Any]] = {}
    for instrument in instruments:
        if isinstance(instrument, dict) and isinstance(instrument.get("figi"), str):
            by_figi.setdefault(instrument["figi"], instrument)
    if len(by_figi) != 1:
        tickers = sorted({str(i.get("ticker")) for i in by_figi.values() if i.get("ticker")})
        return CusipMatch(
            cusip,
            "ambiguous" if by_figi else "unmatched",
            candidates=len(by_figi),
            note=f"OpenFIGI gave {len(by_figi)} FIGIs: {', '.join(tickers) or 'no tickers'}",
        )
    [(figi, instrument)] = by_figi.items()
    return CusipMatch(
        cusip,
        "matched",
        figi=figi,
        ticker=_text(instrument, "ticker"),
        name=_text(instrument, "name"),
        exch_code=_text(instrument, "exchCode"),
        security_type=_text(instrument, "securityType"),
        market_sector=_text(instrument, "marketSector"),
        candidates=1,
    )


def _text(instrument: Mapping[str, Any], name: str) -> str | None:
    value = instrument.get(name)
    return value.strip() or None if isinstance(value, str) else None
