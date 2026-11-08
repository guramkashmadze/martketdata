"""What a proof page shows (PLAN.md §9.10; E5b-S3): a value, its proof, the SEC links, the checks
it passed, the cross-check and its change history, read from clean tables with the API's own
functions, so a page and the API never disagree.

- A release (`/proof/earnings/{accession_no}`): the API's release object (D-097) with each
  slot's source quote, scale, checks or reasons and cross-check, and every change to its values,
  an 8-K/A's included.
- A metric value (`/proof/metrics/{ticker}/{metric}/{period_type}/{period_end}`): the row the
  API serves for that period (the company's, else a predecessor's, D-084), how it was obtained,
  the filings it reads and its change history.
- A Form 4 or 4/A (`/proof/insiders/{accession_no}`, E15): the filing as parsed, its owners and
  every line, served or superseded by a later 4/A or an earlier copy (E8-S3), each line's
  checks and flags, the footnotes and the change history; an insider trade on a page links to
  the filings it sums.
- A fund position (`/proof/funds/{manager_cik}/{quarter}/{cusip}`, E15): the position as
  `fund_holdings` serves it, the 13F reports and information-table lines it adds up, their unit
  and summary-page totals, its flags and its change history.
- A 5% stake (`/proof/stakes/{accession_no}`, E16): a structured Schedule 13D or 13G as parsed,
  every reporting person as filed, a 13D's Item 4 word for word, its checks, whether it is its
  holder's latest filing about the class (served) or which later filing replaced it, the
  holder's filings about the class and the change history.
"""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict

from marketdata.db.repositories import financial_metrics, holdings, insiders, stakes, value_changes
from marketdata.db.repositories.insiders import StoredFiling, StoredLine
from marketdata.db.repositories.stakes import StoredStakeFiling, StoredStakePerson
from marketdata.db.repositories.value_changes import StoredChange
from marketdata.normalize.insiders import CODE_LABELS, FiledTrade, group_trades
from marketdata.normalize.quality import DESCRIPTIONS
from marketdata.normalize.schedule13 import Schedule, schedule_of
from marketdata.normalize.stakes import headline, is_exited
from marketdata.sec import urls
from marketdata.service.companies import CompanyRef, company_refs, group_of, require, resolve_ticker
from marketdata.service.earnings import EarningsRelease, get_earnings
from marketdata.service.errors import InvalidParameter, NotFound
from marketdata.service.financials import (
    MetricView,
    check_metrics,
    check_period,
    metric_refs,
    metric_view,
)
from marketdata.service.funds import FundFiling, FundPosition, fund_filing, fund_holdings
from marketdata.service.insiders import Insider, insider_of
from marketdata.service.served import Number, Source, sources_of
from marketdata.service.stakes import StakePerson, company_filings, group_of_listing, person_of

SLOT_NAMES = {
    "revenue": "Revenue",
    "operating_income": "Operating income",
    "net_income": "Net income",
    "eps_diluted": "Diluted EPS",
}
CHECKS = {
    "in_source": "the printed figure is in the filed document, in the row quoted",
    "scale": "the value is the printed figure times the stated scale, exactly",
    "sanity": (
        "the change from a year earlier is within ±1000%, revenue is positive, and diluted EPS "
        "agrees with net income and the diluted shares where the table prints them"
    ),
}
CROSS_CHECKS = {
    "matched": "equal to the later 10-Q or 10-K within 0.5% (EPS: within $0.01)",
    "mismatch": "differs from the later 10-Q or 10-K by more than 0.5% (EPS: more than $0.01)",
    "not_comparable": "the later 10-Q or 10-K has no comparable value",
    "pending": "the 10-Q or 10-K for this quarter isn't loaded yet",
}
METHODS = {
    "reported": "reported for this period in the filing",
    "derived_ytd": (
        "a year-to-date value less the shorter year-to-date value before it, both from the "
        "filings named"
    ),
    "derived_q4": "the fiscal year less its first nine months (or its first three quarters)",
    "derived_ttm": (
        "the sum of the last four quarters; for a balance, the value at the latest quarter end"
    ),
    "computed": "computed from other metrics: a difference, a margin or a change in percent",
}
REASONS = {
    "correction": "our rules, an override or the calc version changed",
    "restatement": "a later filing reports the period with another value",
    "amendment": "the company amended the release (8-K/A)",
}
# E15: what an insider trade's checks and flags mean (§9.7, normalize/insiders.py).
INSIDER_CHECKS = {
    "issuer_matches_listing": "the filing's issuer is the company whose listing it is on",
    "direction_matches_code": "a purchase acquires shares and a sale disposes of them",
    "price_reported": "every line gives a price per share",
}
INSIDER_FLAGS = {
    "direction_differs_from_code": (
        "a purchase that disposes of shares, or a sale that acquires them, as filed"
    ),
    "price_not_reported": "no line gives a price; a footnote may give the average",
    "price_partly_reported": "some lines give no price",
    "trade_date_after_filing": "the trade is dated after the filing: the filer's typo, as filed",
}
# E15: what a fund position's flags mean (§9.8, normalize/holdings.py).
POSITION_FLAGS = {
    "unit_suspect": (
        "the value per share is more than 50% from the median of the other curated managers "
        "holding it that quarter: the value may be in another unit than the filing says"
    ),
    "split_adjusted": "the shares a quarter earlier are compared after the security's split",
    "no_prior_report": "no report for the quarter before, so no change is shown",
}
# E16: what a stake filing's checks and flags mean (§9.15, normalize/stakes.py).
STAKE_CHECKS = {
    "issuer_matches_listing": (
        "the filing's issuer is the company whose listing it is on, the subject's (file number "
        "005-)"
    ),
    "form_matches_listing": "the form in the XML is the form the SEC lists",
    "percent_in_range": "every reporting person's percent of the class is from 0 to 100",
    "powers_within_amount": (
        "each person's sole and shared voting powers, and its dispositive powers, add up to at "
        "most the amount it owns"
    ),
}
STAKE_FLAGS = {
    "form_differs_from_listing": "the form in the XML differs from the form the SEC lists",
    "percent_out_of_range": "a percent of the class below 0 or above 100, as filed",
    "powers_exceed_amount": (
        "a person's sole and shared powers add up to more than the amount it owns, as filed: "
        "the same shares stated as both"
    ),
}
STAKE_REASONS = {
    "amendment": "a later filing of the same holder about the class replaced the stake",
    "correction": "a new parser version reads the filing otherwise",
}
SUPERSEDED = {
    "amendment": "a later 4/A restates or corrects it",
    "duplicate": "the same filing was made twice; its first copy is served",
}


class HistoryEntry(BaseModel):
    """One change to a value on the page."""

    model_config = ConfigDict(frozen=True)

    change_id: int
    changed_at: Any  # datetime; Any keeps the template's formatting free
    reason: str
    detail: str | None
    what: str  # e.g. "Revenue, value" or "revenue, Q 2026-06-27"
    old_value: Number | None
    new_value: Number | None


class EarningsProof(BaseModel):
    model_config = ConfigDict(frozen=True)

    release: EarningsRelease
    sources: tuple[Source, ...]
    history: tuple[HistoryEntry, ...]
    flags: dict[str, str]  # each flag on the page, with what it means


class MetricProofPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticker: str
    cik: int
    company: str | None
    row: MetricView
    sources: tuple[Source, ...]
    history: tuple[HistoryEntry, ...]
    flags: dict[str, str]


async def earnings_proof(conn: AsyncConnection[Any], accession_no: str) -> EarningsProof:
    """A release with its proof and every change to its values, whatever its status."""
    served = await get_earnings(conn, accession_no)
    release = served.data
    stored = await value_changes.for_release(conn, release.release.accession_no)
    codes = {code for fact in release.facts for code in fact.quality_flags}
    return EarningsProof(
        release=release,
        sources=served.sources,
        history=tuple(_entry(change) for change in stored),
        flags={code: DESCRIPTIONS.get(code, code) for code in sorted(codes)},
    )


async def metric_proof(
    conn: AsyncConnection[Any], ticker: str, metric: str, period_type: str, period_end: str
) -> MetricProofPage:
    """The value the API serves for this period of a company (or its predecessor)."""
    check_metrics([metric])
    period = check_period(period_type)
    try:
        end = date.fromisoformat(period_end)
    except ValueError:
        raise InvalidParameter(f"period_end must be a day like 2026-06-30, not {period_end!r}")
    security = await resolve_ticker(conn, ticker)
    require(security, "fundamentals")
    group = await group_of(conn, security.cik)
    rows = await financial_metrics.rows_at(conn, [(cik, metric, period, end) for cik in group])
    rank = {cik: position for position, cik in enumerate(group)}
    if not rows:
        raise NotFound(
            f"No {metric} value of {security.ticker} is stored for the {period} period ending "
            f"{end}."
        )
    row = metric_view(min(rows, key=lambda stored: rank[stored.cik]))
    history = [
        change
        for change in await value_changes.for_metric(conn, row.cik, metric)
        if change.entity_key.get("period_type") == period
        and change.entity_key.get("period_end") == end.isoformat()
    ]
    return MetricProofPage(
        ticker=security.ticker,
        cik=security.cik,
        company=security.name,
        row=row,
        sources=await sources_of(conn, metric_refs([row])),
        history=tuple(_entry(change) for change in history),
        flags={code: DESCRIPTIONS.get(code, code) for code in row.quality_flags},
    )


def _entry(change: StoredChange) -> HistoryEntry:
    key = change.entity_key
    if change.entity == "earnings_fact":
        slot = str(key.get("slot"))
        field = "the year before" if key.get("field") == "prior_value" else "value"
        what = f"{SLOT_NAMES.get(slot, slot)}, {field}"
        if key.get("amends"):
            what += f" (8-K/A {key.get('accession_no')})"
    elif change.entity == "insider_transaction":
        what = f"line {key.get('line_no')} of {key.get('accession_no')}, {key.get('field')}"
        if key.get("served_from") and key.get("served_from") != key.get("accession_no"):
            what += f" (served from {key.get('served_from')})"
    elif change.entity == "fund_position":
        option = f" {key['put_call']}" if key.get("put_call") else ""
        what = f"{key.get('cusip')}{option}, {key.get('field')}"
    elif change.entity == "stake_holding":
        what = f"the stake of {key.get('accession_no')}, shares"
        if key.get("served_from") and key.get("served_from") != key.get("accession_no"):
            what += f" (served from {key.get('served_from')})"
    else:
        what = f"{key.get('metric')}, {key.get('period_type')} {key.get('period_end')}"
    return HistoryEntry(
        change_id=change.change_id,
        changed_at=change.changed_at,
        reason=change.reason,
        detail=change.detail,
        what=what,
        old_value=change.old_value,
        new_value=change.new_value,
    )


# --- E15: a Form 4's proof page.


class InsiderLineProof(BaseModel):
    """A line of a Form 4 as parsed, and whether it is served."""

    model_config = ConfigDict(frozen=True)

    line_no: int  # in document order, table I first
    derivative: bool  # table II: options, units, warrants
    security: str | None
    trade_date: date
    code: str
    code_label: str | None
    acquired_disposed: str | None  # A or D
    shares: Number | None
    price: Number | None  # USD per share, as filed
    value: Number | None  # shares times price, exact
    owned_after: Number | None
    direct_indirect: str | None
    nature_of_ownership: str | None
    timeliness: str | None
    exercise_price: Number | None
    exercise_date: date | None
    expiration_date: date | None
    underlying_title: str | None
    underlying_shares: Number | None
    footnotes: tuple[str, ...]  # the ids on any of its fields, in order
    served: bool
    superseded_by: str | None  # the 4/A, or the first copy of a filing made twice
    superseded_line: int | None  # the line that serves its trade now
    superseded_reason: str | None  # amendment or duplicate
    checks: tuple[str, ...]
    quality_flags: tuple[str, ...]


class InsiderFilingProof(BaseModel):
    model_config = ConfigDict(frozen=True)

    accession_no: str
    form: str  # 4 or 4/A
    filed_at: date
    period_of_report: date
    original_filed_at: date | None  # a 4/A's: the filing it amends
    issuer: CompanyRef
    insiders: tuple[Insider, ...]
    rule_10b5_1: bool | None  # the filing's box; null before April 2023
    not_subject_to_section16: bool | None
    filing_url: str  # the filing's folder on sec.gov
    document_url: str  # the XML the lines were read from
    schema_version: str | None
    method: str
    extractor_version: str
    parsed_at: datetime
    footnotes: dict[str, str]
    remarks: str | None
    lines: tuple[InsiderLineProof, ...]
    history: tuple[HistoryEntry, ...]
    checks: dict[str, str]  # each check on the page, with what it means
    flags: dict[str, str]


async def insider_filing_proof(conn: AsyncConnection[Any], accession_no: str) -> InsiderFilingProof:
    """A Form 4 or 4/A as parsed: every line, served or superseded, with its checks."""
    try:
        accession = urls.accession_no(accession_no)
    except ValueError:
        raise InvalidParameter(
            f"{accession_no!r} is not an accession number like 0000320193-26-000018"
        )
    filing = await insiders.get_filing(conn, accession)
    if filing is None:
        raise NotFound(f"No Form 4 has the accession number {accession}.")
    owners = await insiders.owners(conn, [accession])
    stored = await insiders.lines(conn, [accession])
    refs = await company_refs(conn, [filing.issuer_cik])
    lines = tuple(_line_proof(filing, line) for line in stored)
    checks = {code for line in lines for code in line.checks}
    flags = {code for line in lines for code in line.quality_flags}
    return InsiderFilingProof(
        accession_no=filing.accession_no,
        form=filing.form,
        filed_at=filing.filed_at,
        period_of_report=filing.period_of_report,
        original_filed_at=filing.original_filed_at,
        issuer=refs[filing.issuer_cik],
        insiders=tuple(insider_of(owner) for owner in sorted(owners, key=lambda o: o.position)),
        rule_10b5_1=filing.rule_10b5_1,
        not_subject_to_section16=filing.not_subject_to_section16,
        filing_url=urls.filing_folder(filing.issuer_cik, filing.accession_no),
        document_url=urls.filing_document(filing.issuer_cik, filing.accession_no, filing.doc_name),
        schema_version=filing.schema_version,
        method=filing.method,
        extractor_version=filing.extractor_version,
        parsed_at=filing.parsed_at,
        footnotes=filing.footnotes,
        remarks=filing.remarks,
        lines=lines,
        history=tuple(
            _entry(change) for change in await value_changes.for_insider_filing(conn, accession)
        ),
        checks={code: INSIDER_CHECKS.get(code, code) for code in sorted(checks)},
        flags={code: INSIDER_FLAGS.get(code, code) for code in sorted(flags)},
    )


def _line_proof(filing: StoredFiling, line: StoredLine) -> InsiderLineProof:
    # A line's checks are its own trade's, decided by the same rules as a served trade's.
    (alone,) = group_trades(
        [
            FiledTrade(
                filing.accession_no,
                tuple(filing.owner_ciks),
                filing.rule_10b5_1,
                filing.filed_at,
                line,
            )
        ]
    )
    footnotes = dict.fromkeys(note for ids in line.footnotes.values() for note in ids)
    return InsiderLineProof(
        line_no=line.line_no,
        derivative=line.is_derivative,
        security=line.security_title,
        trade_date=line.trade_date,
        code=line.code,
        code_label=CODE_LABELS.get(line.code),
        acquired_disposed=line.acquired_disposed,
        shares=line.shares,
        price=line.price,
        value=(
            line.shares * line.price if line.shares is not None and line.price is not None else None
        ),
        owned_after=line.owned_after,
        direct_indirect=line.direct_indirect,
        nature_of_ownership=line.nature_of_ownership,
        timeliness=line.timeliness,
        exercise_price=line.exercise_price,
        exercise_date=line.exercise_date,
        expiration_date=line.expiration_date,
        underlying_title=line.underlying_title,
        underlying_shares=line.underlying_shares,
        footnotes=tuple(footnotes),
        served=line.superseded_by is None,
        superseded_by=line.superseded_by,
        superseded_line=line.superseded_line,
        superseded_reason=line.superseded_reason,
        checks=alone.checks,
        quality_flags=alone.flags,
    )


# --- E15: a fund position's proof page.


class PositionLine(BaseModel):
    """A line of a 13F information table, as filed."""

    model_config = ConfigDict(frozen=True)

    accession_no: str
    line_no: int
    name_of_issuer: str | None
    title_of_class: str | None
    shares: Number  # or principal for PRN
    share_type: str
    value_as_filed: Number  # in the report's unit
    value_usd: Number
    investment_discretion: str | None
    other_managers: str | None


class PositionProofPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    manager_cik: int
    manager: str
    quarter: str
    report_period: date
    filed_at: date  # of the latest report for the quarter
    previous_period: date | None
    position: FundPosition
    filings: tuple[FundFiling, ...]  # the reports its lines were read from
    lines: tuple[PositionLine, ...]
    history: tuple[HistoryEntry, ...]
    flags: dict[str, str]


async def fund_position_proof(
    conn: AsyncConnection[Any],
    manager_cik: int,
    quarter: str,
    cusip: str,
    put_call: str | None = None,
) -> PositionProofPage:
    """A curated manager's position at a quarter end as `fund_holdings` serves it, with the
    lines it adds up; a position the manager no longer reports shows the quarter before's."""
    option = put_call.strip().upper() if put_call else None
    if option not in (None, "PUT", "CALL"):
        raise InvalidParameter(f"put_call must be PUT or CALL, not {put_call!r}")
    held = (await fund_holdings(conn, manager_cik, quarter=quarter, limit=None)).data
    wanted = (cusip.strip().upper(), option)
    position = next((p for p in held.positions if (p.cusip, p.put_call) == wanted), None)
    if position is None:
        shown = f"{wanted[0]} {option}" if option else wanted[0]
        raise NotFound(
            f"{held.manager}'s 13F report for {held.quarter} has no position in {shown}."
        )
    accessions = list(position.proof.accession_nos)
    lines = [
        line
        for line in await holdings.lines(conn, accessions)
        if (line.cusip, line.put_call) == wanted
    ]
    reports = sorted(
        await holdings.filings(conn, accessions), key=lambda f: (f.filed_at, f.accession_no)
    )
    changed = await value_changes.for_position(
        conn, held.manager_cik, held.report_period, position.cusip, position.put_call
    )
    return PositionProofPage(
        manager_cik=held.manager_cik,
        manager=held.manager,
        quarter=held.quarter,
        report_period=held.report_period,
        filed_at=held.filed_at,
        previous_period=held.previous_period,
        position=position,
        filings=tuple(fund_filing(report) for report in reports),
        lines=tuple(
            PositionLine(
                accession_no=line.accession_no,
                line_no=line.line_no,
                name_of_issuer=line.name_of_issuer,
                title_of_class=line.title_of_class,
                shares=line.shares,
                share_type=line.share_type,
                value_as_filed=line.value_as_filed,
                value_usd=line.value_usd,
                investment_discretion=line.investment_discretion,
                other_managers=line.other_managers,
            )
            for line in sorted(lines, key=lambda line: (line.accession_no, line.line_no))
        ),
        history=tuple(_entry(change) for change in changed),
        flags={code: POSITION_FLAGS.get(code, code) for code in position.quality_flags},
    )


# --- E16: a 5% stake's proof page.


class StakeRef(BaseModel):
    """One of a holder's filings about a class."""

    model_config = ConfigDict(frozen=True)

    accession_no: str
    form: str
    filed_at: date
    event_date: date
    shares: Number
    percent: Number
    exited: bool


class StakeFilingProof(BaseModel):
    model_config = ConfigDict(frozen=True)

    accession_no: str
    form: str
    schedule: Schedule
    filed_at: date
    event_date: date
    issuer: CompanyRef  # the company the filing is about, as known now
    issuer_name: str | None  # as filed
    filer_cik: int
    previous_accession: str | None
    amendment_no: int | None
    cusips: tuple[str, ...]  # as filed
    class_title: str | None
    ticker: str | None  # our ticker of the class, when the CUSIP map links its CUSIP
    rules: tuple[str, ...]
    ceased_5pct: bool | None
    purpose: str | None  # a 13D's Item 4, word for word
    persons: tuple[StakePerson, ...]
    shares: Number  # the largest amount one reporting person states
    percent: Number
    exited: bool
    served: bool  # the holder's latest filing about the class
    served_by: StakeRef | None  # the later filing that serves the holder's stake now
    holder_filings: tuple[StakeRef, ...]  # the holder's filings about the class, newest first
    filing_url: str  # the filing's folder on sec.gov
    index_url: str
    document_url: str  # the XML read
    schema_version: str | None
    method: str
    extractor_version: str
    parsed_at: datetime
    checks: dict[str, str]
    flags: dict[str, str]
    history: tuple[HistoryEntry, ...]


async def stake_filing_proof(conn: AsyncConnection[Any], accession_no: str) -> StakeFilingProof:
    """A structured 13D or 13G as parsed, with its place among its holder's filings."""
    try:
        accession = urls.accession_no(accession_no)
    except ValueError:
        raise InvalidParameter(
            f"{accession_no!r} is not an accession number like 0002100119-26-000052"
        )
    filing = await stakes.get_filing(conn, accession)
    if filing is None:
        raise NotFound(f"No structured Schedule 13D or 13G has the accession number {accession}.")
    found = await company_filings(conn, await group_of_listing(conn, filing.issuer_cik))
    serving = found.holdings.serving[accession]
    holding = next(h for h in found.holdings.holdings if h.accession_no == serving)
    refs = {
        number: _stake_ref(found.filings[number], found.persons.get(number, []))
        for number in (holding.accession_no, *holding.superseded)
    }
    people = sorted(found.persons.get(accession, []), key=lambda person: person.position)
    top = headline(people)
    company = await company_refs(conn, [filing.issuer_cik])
    return StakeFilingProof(
        accession_no=filing.accession_no,
        form=filing.form,
        schedule=schedule_of(filing.form),
        filed_at=filing.filed_at,
        event_date=filing.event_date,
        issuer=company[filing.issuer_cik],
        issuer_name=filing.issuer_name,
        filer_cik=filing.filer_cik,
        previous_accession=filing.previous_accession,
        amendment_no=filing.amendment_no,
        cusips=tuple(filing.cusips),
        class_title=filing.class_title,
        ticker=holding.class_key,
        rules=tuple(filing.rules),
        ceased_5pct=filing.ceased_5pct,
        purpose=filing.purpose,
        persons=tuple(person_of(person) for person in people),
        shares=top.amount,
        percent=top.percent,
        exited=is_exited(filing.ceased_5pct, people),
        served=serving == accession,
        served_by=refs[serving] if serving != accession else None,
        holder_filings=tuple(refs.values()),
        filing_url=urls.filing_folder(filing.issuer_cik, filing.accession_no),
        index_url=urls.filing_index_page(filing.issuer_cik, filing.accession_no),
        document_url=urls.filing_document(filing.issuer_cik, filing.accession_no, filing.doc_name),
        schema_version=filing.schema_version,
        method=filing.method,
        extractor_version=filing.extractor_version,
        parsed_at=filing.parsed_at,
        checks={code: STAKE_CHECKS.get(code, code) for code in filing.checks},
        flags={code: STAKE_FLAGS.get(code, code) for code in filing.quality_flags},
        history=tuple(
            _entry(change) for change in await value_changes.for_stake_filing(conn, accession)
        ),
    )


def _stake_ref(filing: StoredStakeFiling, people: Sequence[StoredStakePerson]) -> StakeRef:
    top = headline(people)
    return StakeRef(
        accession_no=filing.accession_no,
        form=filing.form,
        filed_at=filing.filed_at,
        event_date=filing.event_date,
        shares=top.amount,
        percent=top.percent,
        exited=is_exited(filing.ceased_5pct, people),
    )
