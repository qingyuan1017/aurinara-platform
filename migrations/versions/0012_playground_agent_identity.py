"""Use the platform UUID and runtime ARN as Playground agent identities.

Revision ID: 0012_playground_agent_identity
Revises: 0011_playground
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_playground_agent_identity"
down_revision: str | None = "0011_playground"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("uq_playground_agents_key", "playground_agents", type_="unique")
    op.drop_column("playground_agents", "key")
    op.create_unique_constraint(
        "uq_playground_agents_runtime_arn", "playground_agents", ["runtime_arn"]
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_playground_agents_runtime_arn", "playground_agents", type_="unique"
    )
    op.add_column("playground_agents", sa.Column("key", sa.String(100), nullable=True))
    agents = sa.table(
        "playground_agents",
        sa.column("id", sa.Uuid()),
        sa.column("key", sa.String(100)),
    )
    op.execute(agents.update().values(key=sa.cast(agents.c.id, sa.String(100))))
    op.alter_column("playground_agents", "key", nullable=False)
    op.create_unique_constraint(
        "uq_playground_agents_key", "playground_agents", ["key"]
    )
