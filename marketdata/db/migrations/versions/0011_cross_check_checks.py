"""Release-vs-filing cross-check columns checked (E4b-S6, D-028).

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-29
"""

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # A matched or mismatched slot names the filing and the value it was compared with; the
    # filing is an accession number with dashes (§6). A slot not comparable may still hold the
    # filing's value, for investigation.
    op.execute("""
    alter table earnings_facts add constraint earnings_facts_cross_check_accession_check
      check (cross_check_accession ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$');
    alter table earnings_facts add constraint earnings_facts_cross_check_compared_check
      check (cross_check_status not in ('matched', 'mismatch')
             or (cross_check_value is not null and cross_check_accession is not null));
    """)


def downgrade() -> None:
    op.execute("""
    alter table earnings_facts drop constraint earnings_facts_cross_check_compared_check;
    alter table earnings_facts drop constraint earnings_facts_cross_check_accession_check;
    """)
