"""Seller mobile app: PIN accounts and carts handed to the cashier

A seller signs in on their phone (SELLER user bound to an employee), builds a
cart that reserves stock, and the cashier turns it into a sale.

Revision ID: a7b8c9d0e1f2
Revises: c8d9e0f1a2b3
"""

from alembic import op
import sqlalchemy as sa

revision = "a7b8c9d0e1f2"
down_revision = "c8d9e0f1a2b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ADD VALUE can't be used in the transaction that adds it; commit it first.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'SELLER'")

    op.add_column("users", sa.Column("employee_id", sa.Integer(), sa.ForeignKey("employees.id"), nullable=True))
    op.create_unique_constraint("uq_users_employee_id", "users", ["employee_id"])

    op.create_table(
        "carts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("seller_id", sa.Integer(), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id"), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("sale_id", sa.Integer(), sa.ForeignKey("sales.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_carts_seller_id", "carts", ["seller_id"])
    op.create_index("ix_carts_status", "carts", ["status"])

    op.create_table(
        "cart_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cart_id", sa.Integer(), sa.ForeignKey("carts.id"), nullable=False),
        sa.Column("product_variant_id", sa.Integer(), sa.ForeignKey("product_variants.id"), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
    )
    op.create_index("ix_cart_items_cart_id", "cart_items", ["cart_id"])


def downgrade() -> None:
    # Pending carts hold stock: give it back before the reservations vanish.
    op.execute(
        "UPDATE product_variants pv SET stock_quantity = pv.stock_quantity + r.qty "
        "FROM (SELECT ci.product_variant_id, SUM(ci.quantity) AS qty FROM cart_items ci "
        "JOIN carts c ON c.id = ci.cart_id WHERE c.status = 'pending' "
        "GROUP BY ci.product_variant_id) r WHERE pv.id = r.product_variant_id"
    )
    op.drop_table("cart_items")
    op.drop_table("carts")
    op.drop_constraint("uq_users_employee_id", "users", type_="unique")
    op.drop_column("users", "employee_id")
    op.execute("DELETE FROM users WHERE role = 'SELLER'")
    # Postgres can't drop an enum value; the unused 'SELLER' label stays.
