"""5% stakes from Schedules 13D and 13G (E16-S2): each structured filing parsed and its reporting
persons. Value changes can now be about a stake, when a later filing of its holder or a new
parser version changes a served one (D-027, D-138).

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-08
"""

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # One row per structured 13D or 13G (or amendment) parsed, about the listing's company: the
    # XML it was read from (raw first), its filer and the filing it names as previous, the class
    # and CUSIPs as filed, a 13G's rules and item 5 box, a 13D's Item 4 word for word, and the
    # checks it passed (§9.15). Whether it is served is decided when read: a holder's latest
    # filing about each class.
    op.execute("""
    create table stake_filings (
      accession_no        text primary key,
      issuer_cik          bigint not null,
      form                text not null check (form in ('SCHEDULE 13D', 'SCHEDULE 13D/A',
                                                        'SCHEDULE 13G', 'SCHEDULE 13G/A')),
      filed_at            date not null,
      filer_cik           bigint not null,
      previous_accession  text,
      amendment_no        int,
      issuer_name         text,
      cusips              text[] not null default '{}',
      class_title         text,
      event_date          date not null,
      rules               text[] not null default '{}',
      ceased_5pct         boolean,
      purpose             text,
      checks              text[] not null default '{}',
      quality_flags       text[] not null default '{}',
      doc_name            text not null,
      raw_key             text not null references raw_objects(key),
      schema_version      text,
      method              text not null,
      extractor_version   text not null,
      parsed_at           timestamptz not null default now()
    );
    create index stake_filings_by_issuer on stake_filings (issuer_cik, filed_at);
    """)
    # A reporting person as its cover page states it: a 13D's CIK (a 13G gives none), its types,
    # the amount and percent of the class, and its voting and dispositive powers. Persons'
    # amounts overlap and are never added.
    op.execute("""
    create table stake_persons (
      accession_no        text not null references stake_filings(accession_no),
      position            int not null check (position > 0),
      name                text not null,
      cik                 bigint,
      types               text[] not null default '{}',
      citizenship         text,
      amount              numeric not null,
      percent             numeric not null,
      sole_voting         numeric,
      shared_voting       numeric,
      sole_dispositive    numeric,
      shared_dispositive  numeric,
      member_of_group     text check (member_of_group in ('a', 'b')),
      method              text not null,
      extractor_version   text not null,
      primary key (accession_no, position)
    );
    """)
    op.execute("""
    alter table value_changes drop constraint value_changes_entity_check;
    alter table value_changes add constraint value_changes_entity_check
      check (entity in ('earnings_fact', 'financial_metric', 'insider_transaction',
                        'fund_position', 'stake_holding'));
    """)


def downgrade() -> None:
    op.execute("""
    delete from value_changes where entity = 'stake_holding';
    alter table value_changes drop constraint value_changes_entity_check;
    alter table value_changes add constraint value_changes_entity_check
      check (entity in ('earnings_fact', 'financial_metric', 'insider_transaction',
                        'fund_position'));
    """)
    op.execute("drop table stake_persons")
    op.execute("drop table stake_filings")
