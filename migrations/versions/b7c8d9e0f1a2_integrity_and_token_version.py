"""Stock can't go negative, one variant per product/color/size, token revocation

- CHECK stock_quantity >= 0 is added NOT VALID: new writes are enforced without
  failing on (or scanning) rows that are already negative.
- Duplicate (product_id, color_id, size_id) variants must be merged by hand
  first; the upgrade stops with their ids rather than guessing.
- users.token_version lets logout / password change revoke refresh tokens.

Revision ID: b7c8d9e0f1a2
Revises: a6b7c8d9e0f1
"""

from alembic import op
import sqlalchemy as sa

revision = "b7c8d9e0f1a2"
down_revision = "a6b7c8d9e0f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    dups = op.get_bind().execute(sa.text(
        "SELECT product_id, color_id, size_id, array_agg(id ORDER BY id) "
        "FROM product_variants GROUP BY 1, 2, 3 HAVING count(*) > 1"
    )).fetchall()
    if dups:
        raise RuntimeError(
            "Duplicate product variants (product, color, size -> ids): "
            + "; ".join(f"{p},{c},{s} -> {ids}" for p, c, s, ids in dups)
            + ". Merge them, then rerun the migration."
        )
    op.create_unique_constraint(
        "uq_product_variants_product_color_size",
        "product_variants",
        ["product_id", "color_id", "size_id"],
    )
    op.execute(
        "ALTER TABLE product_variants ADD CONSTRAINT ck_product_variants_stock_nonneg "
        "CHECK (stock_quantity >= 0) NOT VALID"
    )
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("users", "token_version")
    op.drop_constraint("ck_product_variants_stock_nonneg", "product_variants", type_="check")
    op.drop_constraint("uq_product_variants_product_color_size", "product_variants", type_="unique")
