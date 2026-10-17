"""Add platform-owned study sponsor.

Revision ID: 0002_study_sponsor
Revises: 0001_control_plane
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_study_sponsor"
down_revision: str | None = "0001_control_plane"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("studies", sa.Column("sponsor", sa.String(255)))


def downgrade() -> None:
    op.drop_column("studies", "sponsor")
