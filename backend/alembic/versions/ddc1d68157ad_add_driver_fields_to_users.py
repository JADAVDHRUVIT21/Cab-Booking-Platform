"""add driver fields to users

Revision ID: ddc1d68157ad
Revises: adb59ebc6a3a
Create Date: 2026-10-08 16:31:33.458955

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "ddc1d68157ad"
down_revision: Union[str, Sequence[str], None] = "adb59ebc6a3a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "users",
        sa.Column(
            "is_available",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    op.add_column(
        "users",
        sa.Column(
            "vehicle_type",
            sa.String(length=50),
            nullable=True,
        ),
    )

    op.add_column(
        "users",
        sa.Column(
            "vehicle_model",
            sa.String(length=100),
            nullable=True,
        ),
    )

    op.add_column(
        "users",
        sa.Column(
            "vehicle_number",
            sa.String(length=50),
            nullable=True,
        ),
    )

    op.create_unique_constraint(
        "uq_users_vehicle_number",
        "users",
        ["vehicle_number"],
    )

    op.alter_column(
        "users",
        "is_available",
        server_default=None,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "uq_users_vehicle_number",
        "users",
        type_="unique",
    )

    op.drop_column("users", "vehicle_number")
    op.drop_column("users", "vehicle_model")
    op.drop_column("users", "vehicle_type")
    op.drop_column("users", "is_available")