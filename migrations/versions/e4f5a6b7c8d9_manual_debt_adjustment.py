"""Separate manually entered debt from sale-derived debt

The debts list computes a client's outstanding debt from their unpaid sales,
which is the right source of truth. But "Qarz qo'shish" wrote a total into
clients.debt_amount, a column that computation never reads — so adding a debt
returned 200 and changed nothing on screen.

Manual debt has no sale behind it, so it gets its own column and is added on
top of the sale-derived figure.

The backfill recovers debt that was added by hand and then lost: where the
stored total exceeds the sale-derived total, the excess is exactly what those
failed "add debt" clicks wrote, and it becomes the adjustment. Where the stored
total is *lower*, the column is simply stale — clamping at zero keeps the
sale-derived debt intact instead of cancelling it out.

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
"""

from alembic import op
import sqlalchemy as sa

revision = "e4f5a6b7c8d9"
down_revision = "d3e4f5a6b7c8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "clients",
        sa.Column(
            "manual_debt_adjustment",
            sa.Numeric(14, 2),
            nullable=False,
            server_default="0",
        ),
    )
    # GREATEST(..., 0) is the safety net: a stored total below the sale-derived
    # one means a stale column, not a credit, and must not reduce real debt.
    op.execute(
        """
        UPDATE clients c
        SET manual_debt_adjustment = GREATEST(
            c.debt_amount - COALESCE(s.debt, 0), 0
        )
        FROM (
            SELECT client_id, SUM(total_amount - paid_amount) AS debt
            FROM sales
            WHERE status IN ('DEBT', 'PARTIALLY_PAID')
            GROUP BY client_id
        ) s
        WHERE s.client_id = c.id
        """
    )
    # Clients with no unpaid sales: the whole stored total is manual debt.
    op.execute(
        """
        UPDATE clients c
        SET manual_debt_adjustment = GREATEST(c.debt_amount, 0)
        WHERE NOT EXISTS (
            SELECT 1 FROM sales s
            WHERE s.client_id = c.id AND s.status IN ('DEBT', 'PARTIALLY_PAID')
        )
        """
    )


def downgrade() -> None:
    op.drop_column("clients", "manual_debt_adjustment")
