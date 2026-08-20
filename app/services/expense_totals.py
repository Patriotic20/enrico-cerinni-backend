"""Shared expense aggregation.

Salaries and stock purchases are canonical EXPENSE_CATEGORIES but they are not
stored as Expense rows — salaries live in salary_payments and purchases are
PURCHASE transactions. Every consumer that groups spend by category has to fold
them in, so the logic lives here instead of being repeated per endpoint.
"""

from decimal import Decimal
from typing import Dict, Optional

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

    # Expense rows are filtered on `date` (when the money was spent) rather than
    # `created_at` (when the row was entered); the two diverge on backdating.
    expense_query = db.query(Expense)
    if start_date:
        expense_query = expense_query.filter(Expense.date >= start_date)
    if end_date:
        expense_query = expense_query.filter(Expense.date <= end_date)

    for expense in expense_query.all():
        key = expense.category or "other"
        totals[key] = totals.get(key, Decimal("0")) + expense.amount

    salary_query = db.query(SalaryPayment)
    if start_date:
        salary_query = salary_query.filter(SalaryPayment.payment_date >= start_date)
    if end_date:
        salary_query = salary_query.filter(SalaryPayment.payment_date <= end_date)

    salary_total = sum((p.amount for p in salary_query.all()), Decimal("0"))
    if salary_total:
        totals["salary"] = totals.get("salary", Decimal("0")) + salary_total

    purchase_query = db.query(Transaction).filter(
        Transaction.transaction_type == TransactionType.PURCHASE
    )
    if start_date:
        purchase_query = purchase_query.filter(Transaction.created_at >= start_date)
    if end_date:
        purchase_query = purchase_query.filter(Transaction.created_at <= end_date)

    purchase_total = sum((t.amount for t in purchase_query.all()), Decimal("0"))
    if purchase_total:
        totals["supplier_costs"] = (
            totals.get("supplier_costs", Decimal("0")) + purchase_total
        )

    return totals


def expense_total(db: Session, start_date=None, end_date=None) -> Decimal:
    """Grand total of everything expense_totals_by_category counts."""
    return sum(expense_totals_by_category(db, start_date, end_date).values(), Decimal("0"))
