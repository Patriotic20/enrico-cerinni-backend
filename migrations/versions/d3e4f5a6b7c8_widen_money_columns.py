"""Widen money columns from Numeric(10, 2) to Numeric(14, 2)

Numeric(10, 2) caps every amount at 99,999,999.99. Prices here are in UZS,
where that ceiling is roughly a hundred million — routinely exceeded by
wholesale purchases, accumulated client debt and larger sales. Exceeding it
raised psycopg2.errors.NumericValueOutOfRange, which surfaced to the client as
a bare 500 (seen on PATCH /clients/{id}/debt with 150,000,000).

Widening precision never loses data, so this migration needs no backfill.

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
"""

from alembic import op
import sqlalchemy as sa

revision = "d3e4f5a6b7c8"
down_revision = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None

MONEY_COLUMNS = [
    ("clients", "debt_amount"),
    ("employees", "salary"),
    ("expenses", "amount"),
    ("product_variants", "cost_price"),
    ("product_variants", "price"),
    ("salary_payments", "amount"),
    ("sale_items", "total_price"),
    ("sale_items", "unit_price"),
    ("sales", "paid_amount"),
    ("sales", "total_amount"),
    ("transactions", "amount"),
]


def upgrade() -> None:
    for table, column in MONEY_COLUMNS:
        op.alter_column(
            table, column,
            existing_type=sa.Numeric(10, 2),
            type_=sa.Numeric(14, 2),
        )


def downgrade() -> None:
    # Narrowing can fail on rows that already exceed the old ceiling.
    for table, column in MONEY_COLUMNS:
        op.alter_column(
            table, column,
            existing_type=sa.Numeric(14, 2),
            type_=sa.Numeric(10, 2),
        )
