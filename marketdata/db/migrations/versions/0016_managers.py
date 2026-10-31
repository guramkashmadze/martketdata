"""Curated 13F managers (E9-S1): the investors whose portfolios are served, loaded from
data/managers.csv with the name EDGAR gives each (PLAN.md §9.8). A manager the file no longer
lists stays, not curated, so the holdings it filed keep their manager.

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-01
"""

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # `name` is EDGAR's, as filed; `display_name` is ours, unique among curated managers so a
    # manager can be asked for by name. updated_at moves only when a value changes.
    op.execute("""
    create table managers (
      manager_cik   bigint primary key check (manager_cik > 0),
      name          text not null,
      display_name  text not null check (display_name <> ''),
      curated       boolean not null default true,
      notes         text,
      updated_at    timestamptz not null default now()
    );
    create unique index managers_display_name on managers (lower(display_name)) where curated;
    """)


def downgrade() -> None:
    op.execute("drop table managers")
