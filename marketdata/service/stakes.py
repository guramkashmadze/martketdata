"""5% stakes (PLAN.md §9.15, §10.2, E16): the holders that filed a Schedule 13D or 13G about a
company, read from the structured filings, with proof.

`major_holders` serves each holder's latest filing about each class of the company's shares (its
predecessors' too), however old: the holders by name, those whose latest filing reports below 5%
of the class, or ticks item 5, as exited, and the company's newest old-format filings (SC 13D,
SC 13G and their amendments, HTML or text) as links, not parsed. Nothing is ranked (D-029).

A holder's shares and percent are the largest amount one of its reporting persons states, with
that person's percent (normalize/stakes.py): persons' amounts overlap and are never added; every
person is served as filed.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict

from marketdata.db.repositories import cusips, filings, predecessors, securities, stakes
from marketdata.db.repositories.filings import Filing
from marketdata.db.repositories.stakes import StoredStakeFiling, StoredStakePerson
from marketdata.normalize.filing_events import SUBJECT_FILE_NUMBER
from marketdata.normalize.schedule13 import Schedule, schedule_of
from marketdata.normalize.stakes import (
    NOT_PARSED,
    Holding,
    Holdings,
    headline,
    holdings,
    is_exited,
    share_classes,
)
from marketdata.normalize.tickers import primary_of
from marketdata.sec import urls
from marketdata.service.companies import group_of, require, resolve_ticker
from marketdata.service.freshness import company_check
from marketdata.service.served import FlagRef, Number, Served, Source, newest

OLD_FORMS = ("SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A")
OLDER_LIMIT = 20  # old-format filings listed, newest first
ABOUT_STAKES = (
    "Whoever owns more than 5% of a class of a company's shares files a Schedule 13D, or a "
    "Schedule 13G when it is a passive investor or a qualified institution, and amends it as "
    "the stake changes. These are the holders' latest filings in the SEC's structured format "
    "(from 2024, required from 2024-12-18); older filings are listed as links and not read. A "
    "holder exited when its latest filing reports below 5% or says it no longer owns more than "
    "5%. Shares are the largest amount one reporting person states: joint filers' amounts "
    "overlap and are never added. Percents are of the class, as filed."
)


class StakePerson(BaseModel):
    """A reporting person as its cover page states it."""

    model_config = ConfigDict(frozen=True)

    name: str
    cik: int | None  # a 13D's; a 13G names none
    types: tuple[str, ...]  # IA investment adviser, HC holding company, IN individual ...
    citizenship: str | None  # citizenship or place of organization, as filed (EDGAR codes)
    shares: Number  # aggregate amount beneficially owned
    percent: Number  # of the class
    sole_voting: Number | None
    shared_voting: Number | None
    sole_dispositive: Number | None
    shared_dispositive: Number | None
    member_of_group: str | None  # a: a member of a group; b: not


class StakeProof(BaseModel):
    """Where a stake comes from (§9.10): the filing, its XML and the checks it passed."""

    model_config = ConfigDict(frozen=True)

    accession_no: str
    form: str
    filed_at: date
    filing_url: str  # the filing's folder on sec.gov
    document_url: str  # the XML the stake was read from
    method: str  # schedule13_xml
    extractor_version: str
    checks: tuple[str, ...]  # issuer_matches_listing, form_matches_listing, percent_in_range ...
    supersedes: tuple[str, ...]  # the holder's earlier filings about the class, newest first


class StakeHolder(BaseModel):
    """One holder's stake in one class of the company's shares, from its latest filing."""

    model_config = ConfigDict(frozen=True)

    holder: str  # the filing's first reporting person
    filer_cik: int  # who filed it
    schedule: Schedule  # 13D (may seek to influence) or 13G (passive)
    form: str  # SCHEDULE 13G, SCHEDULE 13G/A ...
    filed_at: date
    event_date: date  # the date of the event that required the filing
    class_title: str | None  # as filed, e.g. Common Stock
    ticker: str | None  # our ticker of the class, when the CUSIP map links its CUSIP
    cusips: tuple[str, ...]  # as filed
    shares: Number  # the largest amount one reporting person states
    percent: Number  # that person's percent of the class
    unit: str = "shares"
    status: Literal["held", "exited"]
    rules: tuple[str, ...]  # a 13G's: Rule 13d-1(b), (c) or (d)
    ceased_5pct: bool | None  # a 13G's item 5: no longer owns more than 5%
    reporting_persons: tuple[StakePerson, ...]
    purpose: str | None  # a 13D's Item 4, word for word
    filings: int  # the holder's filings about the class: this one and those it supersedes
    quality_flags: tuple[str, ...]
    proof: StakeProof


class OlderFiling(BaseModel):
    """A filing in the old format (HTML or text), listed but not read."""

    model_config = ConfigDict(frozen=True)

    accession_no: str
    form: str  # SC 13D, SC 13D/A, SC 13G or SC 13G/A
    filed_at: date
    url: str  # the filing's index page on sec.gov
    quality_flags: tuple[str, ...] = (NOT_PARSED,)


class CompanyStakes(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticker: str
    cik: int
    company: str | None
    holders: tuple[StakeHolder, ...]  # held, by holder name (never by size)
    exited: tuple[StakeHolder, ...]  # latest filing below 5%, by holder name
    older_filings: tuple[OlderFiling, ...]  # newest first, not parsed
    more_older_filings: bool
    note: str


@dataclass(frozen=True)
class CompanyFilings:
    """A company group's stored stake filings, decided into holdings."""

    filings: dict[str, StoredStakeFiling]
    persons: dict[str, list[StoredStakePerson]]
    holdings: Holdings
    classes: dict[str, str]
    primary: str | None


async def company_filings(conn: AsyncConnection[Any], group: Sequence[int]) -> CompanyFilings:
    """Every stake filing stored about a company group (the company first), with its holders."""
    stored = await stakes.filings_about(conn, group)
    people = await stakes.persons(conn, [filing.accession_no for filing in stored])
    by_filing: dict[str, list[StoredStakePerson]] = {}
    for person in people:
        by_filing.setdefault(person.accession_no, []).append(person)
    filed = sorted({cusip for filing in stored for cusip in filing.cusips})
    links = await cusips.links(conn, filed) if filed else []
    classes = share_classes(((link.cusip, link.ticker, link.cik) for link in links), set(group))
    listed = await securities.list_current_for_cik(conn, group[0])
    primary = primary_of((row.ticker, row.security_type) for row in listed)
    return CompanyFilings(
        filings={filing.accession_no: filing for filing in stored},
        persons=by_filing,
        holdings=holdings(stakes.as_stakes(stored, people), classes, primary),
        classes=classes,
        primary=primary,
    )


def person_of(person: StoredStakePerson) -> StakePerson:
    return StakePerson(
        name=person.name,
        cik=person.cik,
        types=tuple(person.types),
        citizenship=person.citizenship,
        shares=person.amount,
        percent=person.percent,
        sole_voting=person.sole_voting,
        shared_voting=person.shared_voting,
        sole_dispositive=person.sole_dispositive,
        shared_dispositive=person.shared_dispositive,
        member_of_group=person.member_of_group,
    )


def proof_of(filing: StoredStakeFiling, supersedes: Sequence[str] = ()) -> StakeProof:
    return StakeProof(
        accession_no=filing.accession_no,
        form=filing.form,
        filed_at=filing.filed_at,
        filing_url=urls.filing_folder(filing.issuer_cik, filing.accession_no),
        document_url=urls.filing_document(filing.issuer_cik, filing.accession_no, filing.doc_name),
        method=filing.method,
        extractor_version=filing.extractor_version,
        checks=tuple(filing.checks),
        supersedes=tuple(supersedes),
    )


def holder_of(found: CompanyFilings, holding: Holding) -> StakeHolder:
    """The stake a holding serves, from its latest filing."""
    filing = found.filings[holding.accession_no]
    people = sorted(found.persons.get(filing.accession_no, []), key=lambda person: person.position)
    top = headline(people)
    return StakeHolder(
        holder=people[0].name,
        filer_cik=filing.filer_cik,
        schedule=schedule_of(filing.form),
        form=filing.form,
        filed_at=filing.filed_at,
        event_date=filing.event_date,
        class_title=filing.class_title,
        ticker=holding.class_key,
        cusips=tuple(filing.cusips),
        shares=top.amount,
        percent=top.percent,
        status="exited" if is_exited(filing.ceased_5pct, people) else "held",
        rules=tuple(filing.rules),
        ceased_5pct=filing.ceased_5pct,
        reporting_persons=tuple(person_of(person) for person in people),
        purpose=filing.purpose,
        filings=1 + len(holding.superseded),
        quality_flags=tuple(filing.quality_flags),
        proof=proof_of(filing, holding.superseded),
    )


async def major_holders(
    conn: AsyncConnection[Any], ticker: str, *, older_limit: int = OLDER_LIMIT
) -> Served[CompanyStakes]:
    """The holders that filed a structured Schedule 13D or 13G about a company (its
    predecessors' too): each one's latest filing about each class, by name, those below 5% as
    exited, and the newest old-format filings as links."""
    security = await resolve_ticker(conn, ticker)
    require(security, "stakes")
    group = await group_of(conn, security.cik)
    found = await company_filings(conn, group)
    served = [holder_of(found, holding) for holding in found.holdings.holdings]
    held = sorted((h for h in served if h.status == "held"), key=_by_name)
    exited = sorted((h for h in served if h.status == "exited"), key=_by_name)
    older = _older(await filings.subject_listings(conn, group, OLD_FORMS, SUBJECT_FILE_NUMBER))
    used = [found.filings[holding.accession_no] for holding in found.holdings.holdings]
    check = await company_check(conn, security.cik)  # its subject listing's watch (D-135)
    return Served(
        data=CompanyStakes(
            ticker=security.ticker,
            cik=security.cik,
            company=security.name,
            holders=tuple(held),
            exited=tuple(exited),
            older_filings=tuple(older[:older_limit]),
            more_older_filings=len(older) > older_limit,
            note=ABOUT_STAKES,
        ),
        sources=tuple(
            Source(
                accession_no=filing.accession_no,
                form=filing.form,
                filed_at=filing.filed_at,
                url=urls.filing_folder(filing.issuer_cik, filing.accession_no),
            )
            for filing in sorted(
                used, key=lambda filing: (filing.filed_at, filing.accession_no), reverse=True
            )
        ),
        as_of=newest(filing.parsed_at for filing in used),
        quality_flags=tuple(
            FlagRef(code=code, metric="stake", period_end=holder.event_date, ticker=security.ticker)
            for holder in (*held, *exited)
            for code in holder.quality_flags
        ),
        checked_at=check.at,
        check_overdue=check.overdue,
    )


async def served_now(
    conn: AsyncConnection[Any], accessions: Sequence[str]
) -> dict[str, StakeHolder | None]:
    """For each of these stored filings, the stake its holder is served now (from its latest
    filing about the class), or None when the filing isn't stored."""
    stored = {filing.accession_no: filing for filing in await stakes.filings(conn, accessions)}
    found: dict[str, StakeHolder | None] = {accession: None for accession in accessions}
    decided: dict[int, CompanyFilings] = {}
    for accession, filing in stored.items():
        head = (await group_of_listing(conn, filing.issuer_cik))[0]
        if head not in decided:
            decided[head] = await company_filings(conn, await group_of(conn, head))
        company = decided[head]
        serving = company.holdings.serving.get(accession)
        holding = next((h for h in company.holdings.holdings if h.accession_no == serving), None)
        found[accession] = holder_of(company, holding) if holding is not None else None
    return found


async def group_of_listing(conn: AsyncConnection[Any], cik: int) -> list[int]:
    """The company group a listing CIK belongs to now: a predecessor's is its successor's."""
    head = (await predecessors.successors(conn, [cik])).get(cik, cik)
    return await group_of(conn, head)


def _older(listed: Sequence[Filing]) -> list[OlderFiling]:
    """Old-format filings newest first, each once (a predecessor's listing of the same filing
    counts once)."""
    seen: dict[str, OlderFiling] = {}
    for row in sorted(listed, key=lambda row: (row.filed_at, row.accession_no), reverse=True):
        seen.setdefault(
            row.accession_no,
            OlderFiling(
                accession_no=row.accession_no,
                form=row.form,
                filed_at=row.filed_at,
                url=urls.filing_index_page(row.cik, row.accession_no),
            ),
        )
    return list(seen.values())


def _by_name(holder: StakeHolder) -> tuple[str, str, str]:
    return holder.holder.casefold(), holder.ticker or "", holder.proof.accession_no
