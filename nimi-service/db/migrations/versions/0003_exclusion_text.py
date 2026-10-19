"""exclusion rules can match the message text

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-21

rca_exclusions.match_text: case-insensitive substring of the message's full text (e.g. a service name that
the parser keeps out of the title). The "at least one match field" check now includes it.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD = "num_nonnulls(match_signature_key, match_channel_id, match_sender_external_id, match_title, match_status) > 0"
NEW = ("num_nonnulls(match_signature_key, match_channel_id, match_sender_external_id, match_title, match_status, "
       "match_text) > 0")


def upgrade() -> None:
    op.add_column('rca_exclusions', sa.Column('match_text', sa.Text(), nullable=True))
    op.drop_constraint('rca_exclusions_has_match', 'rca_exclusions', type_='check')
    op.create_check_constraint('rca_exclusions_has_match', 'rca_exclusions', NEW)


def downgrade() -> None:
    op.execute("DELETE FROM rca_exclusions WHERE num_nonnulls(match_signature_key, match_channel_id, "
               "match_sender_external_id, match_title, match_status) = 0")
    op.drop_constraint('rca_exclusions_has_match', 'rca_exclusions', type_='check')
    op.create_check_constraint('rca_exclusions_has_match', 'rca_exclusions', OLD)
    op.drop_column('rca_exclusions', 'match_text')
