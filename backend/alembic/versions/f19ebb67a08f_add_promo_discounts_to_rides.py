"""add promo discounts to rides

Revision ID: f19ebb67a08f
Revises: 6f666f5c3f71
"""

from alembic import op
import sqlalchemy as sa


revision = "f19ebb67a08f"
down_revision = "6f666f5c3f71"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add nullable columns first so existing rides remain valid.
    op.add_column(
        "rides",
        sa.Column("promo_code_id", sa.Integer(), nullable=True),
    )

    op.add_column(
        "rides",
        sa.Column("original_fare", sa.Float(), nullable=True),
    )

    op.add_column(
        "rides",
        sa.Column(
            "discount_amount",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0.0"),
        ),
    )

    # Link rides to promo codes.
    op.create_index(
        "ix_rides_promo_code_id",
        "rides",
        ["promo_code_id"],
        unique=False,
    )

    op.create_foreign_key(
        "fk_rides_promo_code_id_promo_codes",
        "rides",
        "promo_codes",
        ["promo_code_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Keep existing rides at a zero discount.
    op.alter_column(
        "rides",
        "discount_amount",
        existing_type=sa.Float(),
        server_default=None,
        existing_nullable=False,
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_rides_promo_code_id_promo_codes",
        "rides",
        type_="foreignkey",
    )

    op.drop_index(
        "ix_rides_promo_code_id",
        table_name="rides",
    )

    op.drop_column("rides", "discount_amount")
    op.drop_column("rides", "original_fare")
    op.drop_column("rides", "promo_code_id")