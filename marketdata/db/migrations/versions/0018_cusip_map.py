"""CUSIPs mapped to FIGIs and tickers (E9-S3): what OpenFIGI said about each CUSIP of a 13F
filing, and our security with that ticker (PLAN.md §9.8, D-121).

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-01
"""

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # One row per CUSIP asked about (or found invalid): OpenFIGI's FIGI, ticker, name and type
    # for a match, how many FIGIs it gave, and why a CUSIP isn't matched. `raw_key` is the
    # response it came from. The link to our security is dropped if that row goes (the
    # security master deletes a listing it stored by mistake). matched_at moves when the
    # mapping changes, checked_at whenever OpenFIGI is asked.
    op.execute("""
    create table cusip_map (
      cusip          text primary key,
      status         text not null check (status in ('matched', 'unmatched', 'ambiguous')),
      security_id    bigint references securities(security_id) on delete set null,
      figi           text,
      ticker         text,
      name           text,
      exch_code      text,
      security_type  text,
      market_sector  text,
      candidates     int not null default 0 check (candidates >= 0),
      note           text,
      raw_key        text references raw_objects(key),
      matched_at     timestamptz not null default now(),
      checked_at     timestamptz not null default now(),
      check ((status = 'matched') = (figi is not null))
    );
    create index cusip_map_by_security on cusip_map (security_id);
    """)


def downgrade() -> None:
    op.execute("drop table cusip_map")
