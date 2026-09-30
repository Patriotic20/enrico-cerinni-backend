"""Indexes for the hot filter / join columns

Postgres does not index foreign keys on its own, so every report, debt lookup
and sale-items load was a sequential scan. Also:
- lower(trim(sku)) expression index: the barcode lookup compares that way.
- pg_trgm GIN indexes for the '%term%' searches (products, clients, sales).
- the ix_<table>_id indexes duplicated the primary keys; dropped.

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
"""

from alembic import op

revision = "c8d9e0f1a2b3"
down_revision = "b7c8d9e0f1a2"
branch_labels = None
depends_on = None

BTREE = [
    ("ix_sales_created_at", "sales", ["created_at"]),
    ("ix_sales_client_id_status", "sales", ["client_id", "status"]),
    ("ix_sales_status_created_at", "sales", ["status", "created_at"]),
    ("ix_sale_items_sale_id", "sale_items", ["sale_id"]),
    ("ix_sale_items_product_variant_id", "sale_items", ["product_variant_id"]),
    ("ix_product_variants_product_id", "product_variants", ["product_id"]),
    ("ix_transactions_created_at_type", "transactions", ["created_at", "transaction_type"]),
    ("ix_transactions_client_id", "transactions", ["client_id"]),
    ("ix_transactions_sale_id", "transactions", ["sale_id"]),
    ("ix_expenses_date", "expenses", ["date"]),
    ("ix_salary_payments_payment_date", "salary_payments", ["payment_date"]),
    ("ix_salary_payments_employee_id", "salary_payments", ["employee_id"]),
    ("ix_products_brand_id", "products", ["brand_id"]),
    ("ix_products_category_id", "products", ["category_id"]),
    ("ix_products_season_id", "products", ["season_id"]),
    ("ix_clients_created_at", "clients", ["created_at"]),
    ("ix_users_email", "users", ["email"]),
]

TRGM = [
    ("products", "name"),
    ("products", "sku"),
    ("product_variants", "sku"),
    ("brands", "name"),
    ("clients", "first_name"),
    ("clients", "last_name"),
    ("clients", "phone"),
    ("sales", "receipt_number"),
]

PK_DUPES = [
    "brands", "categories", "clients", "colors", "employees", "expenses", "seasons",
    "sizes", "suppliers", "users", "products", "report_templates", "reports",
    "salary_payments", "sales", "product_variants", "report_executions",
    "transactions", "sale_items", "broadcast_history",
]


def upgrade() -> None:
    for name, table, cols in BTREE:
        op.create_index(name, table, cols)
    op.execute(
        "CREATE INDEX ix_product_variants_sku_norm ON product_variants (lower(trim(sku)))"
    )
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    for table, col in TRGM:
        op.execute(
            f"CREATE INDEX ix_{table}_{col}_trgm ON {table} USING gin ({col} gin_trgm_ops)"
        )
    for table in PK_DUPES:
        op.execute(f"DROP INDEX IF EXISTS ix_{table}_id")


def downgrade() -> None:
    for table in PK_DUPES:
        op.execute(f"CREATE INDEX IF NOT EXISTS ix_{table}_id ON {table} (id)")
    for table, col in TRGM:
        op.execute(f"DROP INDEX IF EXISTS ix_{table}_{col}_trgm")
    op.execute("DROP INDEX IF EXISTS ix_product_variants_sku_norm")
    for name, table, _ in BTREE:
        op.drop_index(name, table_name=table)
