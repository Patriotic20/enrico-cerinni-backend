"""Shared expense aggregation.

Salaries and stock purchases are canonical EXPENSE_CATEGORIES but they are not
stored as Expense rows — salaries live in salary_payments and purchases are
PURCHASE transactions. Every consumer that groups spend by category has to fold
them in, so the logic lives here instead of being repeated per endpoint.
"""

from decimal import Decimal
from typing import Dict, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.expense import Expense
from app.models.salary_payment import SalaryPayment
from app.models.transaction import Transaction, TransactionType


def expense_totals_by_category(
    db: Session,
    start_date=None,
    end_date=None,
) -> Dict[str, Decimal]:
    """Total spend per category for the period, across all three sources."""
    totals: Dict[str, Decimal] = {}

    def in_range(query, column):
        if start_date:
            query = query.filter(column >= start_date)
        if end_date:
            query = query.filter(column <= end_date)
        return query

    # Expense rows are filtered on `date` (when the money was spent) rather than
    # `created_at` (when the row was entered); the two diverge on backdating.
    category = func.coalesce(Expense.category, "other")
    for key, amount in (
        in_range(db.query(category, func.sum(Expense.amount)), Expense.date)
        .group_by(category)
        .all()
    ):
        totals[key] = totals.get(key, Decimal("0")) + amount

    salary_total = in_range(
        db.query(func.sum(SalaryPayment.amount)), SalaryPayment.payment_date
    ).scalar()
    if salary_total:
        totals["salary"] = totals.get("salary", Decimal("0")) + salary_total

    purchase_total = in_range(
        db.query(func.sum(Transaction.amount)).filter(
            Transaction.transaction_type == TransactionType.PURCHASE
        ),
        Transaction.created_at,
    ).scalar()
    if purchase_total:
        totals["supplier_costs"] = (
            totals.get("supplier_costs", Decimal("0")) + purchase_total
        )

    return totals


def expense_total(db: Session, start_date=None, end_date=None) -> Decimal:
    """Grand total of everything expense_totals_by_category counts."""
    return sum(expense_totals_by_category(db, start_date, end_date).values(), Decimal("0"))
