"""Link sales to the seller (employee) who made them

Sale.user_id is the logged-in cashier; the person on the floor who actually
sold is usually someone else. Recording them on the sale is what per-seller
KPI, plan completion and commission are computed from.

seller_id stays nullable: sales made before this change have no seller and
are reported as "unassigned". New sales require it at the API layer.

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
"""

from alembic import op
import sqlalchemy as sa

revision = "f5a6b7c8d9e0"
down_revision = "e4f5a6b7c8d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("employees", sa.Column("is_seller", sa.Boolean(), nullable=False, server_default="true"))
    op.add_column("employees", sa.Column("commission_rate", sa.Numeric(5, 2), nullable=False, server_default="0"))
    op.add_column("employees", sa.Column("monthly_target", sa.Numeric(14, 2), nullable=False, server_default="0"))
    op.add_column("sales", sa.Column("seller_id", sa.Integer(), sa.ForeignKey("employees.id"), nullable=True))
    op.create_index("ix_sales_seller_id", "sales", ["seller_id"])


def downgrade() -> None:
    op.drop_index("ix_sales_seller_id", table_name="sales")
    op.drop_column("sales", "seller_id")
    op.drop_column("employees", "monthly_target")
    op.drop_column("employees", "commission_rate")
    op.drop_column("employees", "is_seller")
