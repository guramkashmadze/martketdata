"""Filings and the watcher's state (PLAN.md §7, E3-S4).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-25
"""

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # The §7 table with two changes (D-060): one row per filing and listing company, keyed by
    # (accession_no, cik), because a filing can be listed under several companies (a 13D under
    # its filer and its subject, a joint 10-Q under both registrants); and the listing's file
    # number, which tells the subject of a 13D or 13G from its filer.
    op.execute("""
    create table filings (
      accession_no      text not null check (accession_no ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'),
      cik               bigint not null check (cik > 0),  -- the company whose submissions list it
      form              text not null,
      filed_at          date not null,
      accepted_at       timestamptz,               -- null when the listing's times failed the check (D-057)
      period_of_report  date,
      items_8k          text[],                    -- e.g. '{2.02,9.01}'; null for forms other than 8-K
      primary_doc       text,
      is_xbrl           boolean,
      file_number       text,                      -- this listing's; '005-…' marks the subject of a 13D/13G
      discovered_at     timestamptz not null default now(),
      primary key (accession_no, cik)
    )
    """)
    op.execute("create index filings_by_cik on filings (cik, filed_at desc)")
    # The last submissions file each company's watch processed, so an unchanged file is neither
    # stored again nor parsed (D-059).
    op.execute("""
    create table watch_state (
      cik         bigint primary key check (cik > 0),
      sha256      text not null check (sha256 ~ '^[0-9a-f]{64}$'),
      raw_key     text not null references raw_objects(key),
      checked_at  timestamptz not null,           -- the last poll, changed or not
      changed_at  timestamptz not null            -- the last poll that found new bytes
    )
    """)


def downgrade() -> None:
    op.execute("drop table watch_state")
    op.execute("drop table filings")
