import calendar
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.employee import Employee
from app.models.sale import Sale, SaleItem, SaleStatus
from app.schemas.sale import SaleFilter
from app.services.sale_service import SaleService


def month_bounds(today: Optional[date] = None) -> tuple[date, date]:
    today = today or date.today()
    return today.replace(day=1), today.replace(day=calendar.monthrange(today.year, today.month)[1])


def prorated_target(monthly_target, start: date, end: date) -> Decimal:
    """Monthly plan spread over the days of [start, end].

    Each day is worth 1/days_in_its_month of the plan, so a full calendar month
    is exactly the plan and ranges spanning months stay correct.
    """
    total = Decimal(0)
    day = start
    while day <= end:  # one step per calendar month, not per day
        days_in_month = calendar.monthrange(day.year, day.month)[1]
        seg_end = min(end, day.replace(day=days_in_month))
        total += Decimal(monthly_target) * ((seg_end - day).days + 1) / days_in_month
        day = seg_end + timedelta(days=1)
    return total


def _status(st) -> str:
    return st.value if hasattr(st, "value") else st


def _empty() -> dict:
    return {
        "sales_count": 0, "revenue": Decimal(0), "paid": Decimal(0),
        "debt_sales": 0, "cancelled_sales": 0, "items_sold": 0,
    }


class EmployeeKpiService:
    """Per-seller KPI over the same date filter the sales page uses.

    Cancelled sales are excluded from money totals (and so from commission):
    cancelling a sale takes it off the seller's KPI automatically.
    """

    def __init__(self, db: Session):
        self.db = db

    def _base(self, start: date, end: date):
        return SaleService(self.db).filtered_query(
            SaleFilter(start_date=start.isoformat(), end_date=end.isoformat())
        )

    def _aggregate(self, start: date, end: date) -> dict:
        """Raw per-seller_id totals; key None collects sales without a seller."""
        base = self._base(start, end)
        stats: dict = {}

        for seller_id, st, n, total, paid in (
            base.with_entities(
                Sale.seller_id, Sale.status, func.count(Sale.id),
                func.coalesce(func.sum(Sale.total_amount), 0),
                func.coalesce(func.sum(Sale.paid_amount), 0),
            ).group_by(Sale.seller_id, Sale.status).all()
        ):
            r = stats.setdefault(seller_id, _empty())
            st = _status(st)
            if st == SaleStatus.CANCELLED.value:
                r["cancelled_sales"] += n
                continue
            r["sales_count"] += n
            r["revenue"] += Decimal(total)
            r["paid"] += Decimal(paid)
            if st in (SaleStatus.DEBT.value, SaleStatus.PARTIALLY_PAID.value):
                r["debt_sales"] += n

        # Separate query: joining items into the one above would multiply the
        # sale totals by their item count.
        for seller_id, qty in (
            base.join(SaleItem, SaleItem.sale_id == Sale.id)
            .filter(Sale.status != SaleStatus.CANCELLED)
            .with_entities(Sale.seller_id, func.coalesce(func.sum(SaleItem.quantity), 0))
            .group_by(Sale.seller_id).all()
        ):
            stats.setdefault(seller_id, _empty())["items_sold"] = int(qty)
        return stats

    def leaderboard(self, start: date, end: date) -> dict:
        stats = self._aggregate(start, end)
        unassigned = stats.pop(None, None)
        # Every seller (so a zero-sales seller still shows up against their
        # plan), plus anyone who sold in the period but is no longer a seller.
        employees = (
            self.db.query(Employee)
            .filter(Employee.is_seller.is_(True) | Employee.id.in_(list(stats)))
            .all()
        )
        items = [self._kpi(e, stats.get(e.id, _empty()), start, end) for e in employees]
        items.sort(key=lambda k: (-k["revenue"], k["name"]))
        rank = 0
        for k in items:
            if k["sales_count"]:
                rank += 1
                k["rank"] = rank

        return {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "items": items,
            "totals": self._totals(items, unassigned),
            "unassigned": self._money(unassigned) if unassigned else None,
        }

    def employee(self, employee: Employee, start: date, end: date) -> dict:
        board = self.leaderboard(start, end)
        kpi = next((k for k in board["items"] if k["id"] == employee.id), None)
        if kpi is None:  # not a seller and no sales in the period
            kpi = self._kpi(employee, _empty(), start, end)

        day_col = func.date(Sale.created_at)
        by_day = {
            d if isinstance(d, date) else date.fromisoformat(str(d)): (n, Decimal(total))
            for d, n, total in (
                self._base(start, end)
                .filter(Sale.seller_id == employee.id, Sale.status != SaleStatus.CANCELLED)
                .with_entities(
                    day_col, func.count(Sale.id),
                    func.coalesce(func.sum(Sale.total_amount), 0),
                )
                .group_by(day_col).all()
            )
        }
        daily, day = [], start
        while day <= end:
            n, total = by_day.get(day, (0, Decimal(0)))
            daily.append({"date": day.isoformat(), "sales_count": n, "revenue": float(total)})
            day += timedelta(days=1)

        return {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "kpi": kpi,
            "daily": daily,
            "ranked": sum(1 for k in board["items"] if k["rank"]),
        }

    @staticmethod
    def _money(r: dict) -> dict:
        count = r["sales_count"]
        revenue, paid = r["revenue"], r["paid"]
        return {
            "sales_count": count,
            "revenue": float(revenue),
            "paid": float(paid),
            "outstanding": float(revenue - paid),
            "avg_check": float(revenue / count) if count else 0.0,
            "items_sold": r["items_sold"],
            "debt_sales": r["debt_sales"],
            "cancelled_sales": r["cancelled_sales"],
        }

    def _kpi(self, e: Employee, r: dict, start: date, end: date) -> dict:
        target = prorated_target(e.monthly_target or 0, start, end)
        commission = r["revenue"] * Decimal(e.commission_rate or 0) / 100
        return {
            "id": e.id,
            "name": e.name,
            "position": e.position,
            "phone": e.phone,
            "is_active": e.is_active,
            "is_seller": e.is_seller,
            "salary": float(e.salary or 0),
            "commission_rate": float(e.commission_rate or 0),
            "monthly_target": float(e.monthly_target or 0),
            **self._money(r),
            "target": float(target),
            "target_pct": float(r["revenue"] / target * 100) if target else None,
            "commission": float(commission),
            "rank": None,
        }

    @staticmethod
    def _totals(items: list, unassigned: Optional[dict]) -> dict:
        extra = unassigned or _empty()
        count = sum(k["sales_count"] for k in items) + extra["sales_count"]
        revenue = sum(k["revenue"] for k in items) + float(extra["revenue"])
        # Plan completion is judged on the people who carry a plan.
        planned = [k for k in items if k["is_active"] and k["target"]]
        target = sum(k["target"] for k in planned)
        return {
            "sales_count": count,
            "revenue": revenue,
            "avg_check": revenue / count if count else 0.0,
            "items_sold": sum(k["items_sold"] for k in items) + extra["items_sold"],
            "commission": sum(k["commission"] for k in items),
            "target": target,
            "target_pct": sum(k["revenue"] for k in planned) / target * 100 if target else None,
            "active_sellers": sum(1 for k in items if k["sales_count"]),
        }


if __name__ == "__main__":
    assert prorated_target(3000, date(2026, 9, 1), date(2026, 9, 30)) == 3000
    assert prorated_target(3100, date(2026, 1, 1), date(2026, 1, 10)) == 1000
    # 15 of 31 Jan days + 14 of 28 Feb days
    got = prorated_target(100, date(2026, 1, 17), date(2026, 2, 14))
    assert abs(got - (Decimal(100) * 15 / 31 + Decimal(100) * 14 / 28)) < Decimal("1e-20")
    assert prorated_target(100, date(2026, 3, 5), date(2026, 3, 4)) == 0
    print("ok")
