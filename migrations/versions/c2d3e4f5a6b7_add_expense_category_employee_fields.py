"""add expense category and employee hire_date/is_active

The Pydantic schemas, the API layer and the finance UI have always spoken about
`Expense.category`, `Employee.hire_date` and `Employee.is_active`, but the
tables never carried those columns — every finance endpoint answered 500. This
adds them and backfills the existing rows so the data stays meaningful.

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-08-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c2d3e4f5a6b7'
down_revision: Union[str, None] = 'b1c2d3e4f5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Existing rows only carry a description, so the category is derived from it.
# Anything unmatched keeps the 'other' default.
CATEGORY_BY_DESCRIPTION = {
    "Do'kon ijara haqi": "rent",
    "Kommunal to'lovlar": "utilities",
    "Internet va telefon aloqasi": "utilities",
    "Reklama va marketing": "marketing",
    "Transport va yetkazib berish": "daily_expenses",
    "Do'kon jihozlari ta'miri": "maintenance",
    "Kantselyariya va qadoqlash": "daily_expenses",
    "Tozalash xizmati": "daily_expenses",
    "Bank xizmat haqi": "other",
    "Soliq va yig'imlar": "other",
}


def upgrade() -> None:
    op.add_column(
        'expenses',
        sa.Column('category', sa.String(), nullable=False, server_default='other'),
    )
    op.create_index(op.f('ix_expenses_category'), 'expenses', ['category'])

    for description, category in CATEGORY_BY_DESCRIPTION.items():
        op.execute(
            sa.text("UPDATE expenses SET category = :category WHERE description = :description")
            .bindparams(category=category, description=description)
        )

    # Expenses booked against a supplier or an employee are categorised by that
    # link regardless of their description.
    op.execute(
        "UPDATE expenses SET category = 'supplier_costs' WHERE expense_target_type = 'supplier'"
    )
    op.execute(
        "UPDATE expenses SET category = 'salary' WHERE expense_target_type = 'employee'"
    )

    op.add_column(
        'employees',
        sa.Column(
            'hire_date',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text('now()'),
        ),
    )
    op.add_column(
        'employees',
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
    )

    # Existing employees were created by the seed without a hire date; their
    # row creation time is the closest thing on record.
    op.execute("UPDATE employees SET hire_date = created_at WHERE created_at IS NOT NULL")


def downgrade() -> None:
    op.drop_column('employees', 'is_active')
    op.drop_column('employees', 'hire_date')
    op.drop_index(op.f('ix_expenses_category'), table_name='expenses')
    op.drop_column('expenses', 'category')
