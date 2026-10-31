"""13F reports as filed (E9-S2): each 13F-HR or 13F-HR/A of a curated manager parsed, and the
lines of its information table with their values in dollars (PLAN.md §9.8).

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-01
"""

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # One row per report parsed: its cover (raw first) and information table, what the summary
    # page states (line count, value total as filed) next to what the table holds, and the unit
    # its values were read in with the rule that decided it: the filing date's or an override's.
    # A report with no holdings has no table.
    op.execute("""
    create table f13_filings (
      accession_no         text primary key,
      manager_cik          bigint not null references managers(manager_cik),
      form                 text not null check (form in ('13F-HR', '13F-HR/A')),
      filed_at             date not null,
      report_period        date not null,
      report_type          text,
      amendment_no         int,
      amendment_type       text check (amendment_type in ('RESTATEMENT', 'NEW HOLDINGS')),
      date_reported        date,
      conf_denied_expired  boolean,
      other_managers       int,
      entry_total          int,
      value_total          numeric,
      lines                int not null check (lines >= 0),
      value_sum            numeric not null,
      value_unit           text not null check (value_unit in ('dollars', 'thousands')),
      unit_rule            text not null,
      cover_doc            text not null,
      cover_raw_key        text not null references raw_objects(key),
      table_doc            text,
      table_raw_key        text references raw_objects(key),
      schema_version       text,
      method               text not null,
      extractor_version    text not null,
      parsed_at            timestamptz not null default now(),
      check ((table_doc is null) = (table_raw_key is null))
    );
    create index f13_filings_by_manager on f13_filings (manager_cik, report_period, filed_at);
    """)
    # A line of the information table, numbered in document order: shares or principal as
    # filed, the value as filed and in dollars. Positions are built from these (E9-S4).
    op.execute("""
    create table holdings_raw (
      accession_no           text not null references f13_filings(accession_no),
      line_no                int not null check (line_no > 0),
      manager_cik            bigint not null references managers(manager_cik),
      report_period          date not null,
      name_of_issuer         text,
      title_of_class         text,
      cusip                  text not null,
      figi                   text,
      shares                 numeric not null,
      share_type             text not null check (share_type in ('SH', 'PRN')),
      value_as_filed         numeric not null,
      value_usd              numeric not null,
      put_call               text check (put_call in ('PUT', 'CALL')),
      investment_discretion  text,
      other_managers         text,
      method                 text not null,
      extractor_version      text not null,
      primary key (accession_no, line_no)
    );
    create index holdings_raw_by_cusip on holdings_raw (cusip, report_period);
    """)


def downgrade() -> None:
    op.execute("drop table holdings_raw")
    op.execute("drop table f13_filings")
