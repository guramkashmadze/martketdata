"""API access and reads (E5a): the request log by key, key checks, and the indexes the data
endpoints read through.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-29
"""

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # The rate limit counts a key's requests of the last minute (E5a-S1).
    op.execute("create index api_requests_by_key on api_requests (key_id, at)")
    # A key is stored as the sha256 of the key, in lowercase hex; it allows at least one request
    # a minute.
    op.execute("""
    alter table api_keys add constraint api_keys_key_hash_check check (key_hash ~ '^[0-9a-f]{64}$');
    alter table api_keys add constraint api_keys_rate_per_min_check check (rate_per_min > 0);
    """)
    # Sources name each filing's form and date; filings older than the five-year filings
    # backfill are found through their facts (E5a-S2).
    op.execute("create index xbrl_facts_by_accession on xbrl_facts (accession_no)")
    # The earnings feed: verified releases, newest first.
    op.execute(
        "create index earnings_releases_by_acceptance "
        "on earnings_releases (accepted_at desc, accession_no desc)"
    )


def downgrade() -> None:
    op.execute("drop index earnings_releases_by_acceptance")
    op.execute("drop index xbrl_facts_by_accession")
    op.execute("""
    alter table api_keys drop constraint api_keys_rate_per_min_check;
    alter table api_keys drop constraint api_keys_key_hash_check;
    """)
    op.execute("drop index api_requests_by_key")
