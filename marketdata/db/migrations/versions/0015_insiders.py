"""Insider trades from Form 4 (E8-S2, S3): each filing parsed, its reporting owners and its trades.
Value changes can now be about an insider trade, when a 4/A or a new parser version changes a
served line (D-027).

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-31
"""

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # One row per Form 4 or 4/A parsed: the XML it was read from (raw first) and what holds for
    # every line. The 10b5-1 box is the filing's (null before April 2023); the owners' CIKs,
    # sorted, find the filing a 4/A amends; footnotes are the texts by id.
    op.execute("""
    create table insider_filings (
      accession_no              text primary key,
      issuer_cik                bigint not null,
      form                      text not null check (form in ('4', '4/A')),
      filed_at                  date not null,
      period_of_report          date not null,
      original_filed_at         date,
      owner_ciks                bigint[] not null check (cardinality(owner_ciks) > 0),
      rule_10b5_1               boolean,
      not_subject_to_section16  boolean,
      doc_name                  text not null,
      raw_key                   text not null references raw_objects(key),
      schema_version            text,
      footnotes                 jsonb not null default '{}',
      remarks                   text,
      method                    text not null,
      extractor_version         text not null,
      parsed_at                 timestamptz not null default now()
    );
    create index insider_filings_by_issuer on insider_filings (issuer_cik, filed_at);
    """)
    op.execute("""
    create table insider_owners (
      accession_no      text not null references insider_filings(accession_no),
      owner_cik         bigint not null,
      position          int not null,
      owner_name        text not null,
      is_director       boolean not null,
      is_officer        boolean not null,
      officer_title     text,
      is_ten_pct_owner  boolean not null,
      is_other          boolean not null,
      other_text        text,
      primary key (accession_no, owner_cik)
    );
    """)
    # A trade as filed: table I or II, numbered in document order. `footnotes` gives each
    # field's footnote ids. A line is superseded by the 4/A that restates or corrects it
    # (`superseded_line` is its line there, when one corrects it), or by the earlier copy of a
    # filing made twice; the default view serves only lines not superseded (§9.7).
    op.execute("""
    create table insider_transactions (
      accession_no         text not null references insider_filings(accession_no),
      line_no              int not null check (line_no > 0),
      issuer_cik           bigint not null,
      is_derivative        boolean not null,
      security_title       text,
      trade_date           date not null,
      code                 text not null,
      acquired_disposed    text check (acquired_disposed in ('A', 'D')),
      shares               numeric,
      price                numeric,
      owned_after          numeric,
      direct_indirect      text check (direct_indirect in ('D', 'I')),
      nature_of_ownership  text,
      timeliness           text check (timeliness in ('E', 'L')),
      exercise_price       numeric,
      exercise_date        date,
      expiration_date      date,
      underlying_title     text,
      underlying_shares    numeric,
      footnotes            jsonb not null default '{}',
      method               text not null,
      extractor_version    text not null,
      superseded_by        text,
      superseded_line      int,
      superseded_reason    text check (superseded_reason in ('amendment', 'duplicate')),
      primary key (accession_no, line_no),
      check ((superseded_by is null) = (superseded_reason is null)),
      check (superseded_line is null or superseded_by is not null)
    );
    create index insider_by_issuer on insider_transactions (issuer_cik, trade_date desc);
    """)
    op.execute("""
    alter table value_changes drop constraint value_changes_entity_check;
    alter table value_changes add constraint value_changes_entity_check
      check (entity in ('earnings_fact', 'financial_metric', 'insider_transaction'));
    """)


def downgrade() -> None:
    op.execute("""
    delete from value_changes where entity = 'insider_transaction';
    alter table value_changes drop constraint value_changes_entity_check;
    alter table value_changes add constraint value_changes_entity_check
      check (entity in ('earnings_fact', 'financial_metric'));
    """)
    op.execute("drop table insider_transactions")
    op.execute("drop table insider_owners")
    op.execute("drop table insider_filings")
