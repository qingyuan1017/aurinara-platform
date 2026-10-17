"""Canonicalize Aurinara permission ownership.

Revision ID: 0008_aurinara_permissions
Revises: 0007_central_repository
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_aurinara_permissions"
down_revision: str | None = "0007_central_repository"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE permissions SET product = 'aurinara' "
            "WHERE code LIKE 'artifact.%' AND product <> 'aurinara'"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE permissions SET product = 'artifact' "
            "WHERE code LIKE 'artifact.%' AND code <> 'artifact.object.reference' "
            "AND product = 'aurinara'"
        )
    )
