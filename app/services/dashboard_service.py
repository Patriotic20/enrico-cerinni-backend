from sqlalchemy.orm import Session
from sqlalchemy import func, desc, case, and_
from typing import List, Dict, Any, Optional
from decimal import Decimal
from datetime import datetime, timedelta
import hashlib
import json
from app.models.product import Product, display_name
from app.models.brand import Brand
from app.models.product_variant import ProductVariant
from app.models.client import Client
from app.models.sale import Sale, SaleStatus, SaleItem
from app.models.transaction import Transaction, TransactionType
from app.models.expense import Expense
from app.models.salary_payment import SalaryPayment
from app.services.expense_totals import expense_total, expense_totals_by_category
from app.services.product_service import ProductService
from app.services.sale_service import SaleService

# Transactions are always written with a positive amount (see SaleService), so
# the direction of the money comes from the type, never from the sign. Filtering
# on `amount < 0` silently matches nothing and reports every expense as zero.
INFLOW_TYPES = (TransactionType.SALE, TransactionType.DEBT_PAYMENT)
# Refunds are stored with a negative amount while everything else is positive,
# so outflow sums below take the absolute value — otherwise a refund subtracts
# from expenses and the total can go negative ("-1 217 so'm" on the dashboard).
OUTFLOW_TYPES = (
    TransactionType.EXPENSE,
    TransactionType.PURCHASE,
    TransactionType.REFUND,
)


# Module level: a service instance lives for one request, so a per-instance
# cache never hit. ponytail: per worker process; move to Redis if it matters.
_CACHE: Dict[str, Any] = {}

# Chart bucket width per interval; buckets start at the period's start_date.
_STEP = {"day": timedelta(days=1), "week": timedelta(weeks=1), "month": timedelta(days=30)}


def _label(interval: str, i: int, bucket_start: datetime) -> str:
    if interval == "day":
        return bucket_start.strftime("%d/%m")
    if interval == "week":
        return f"Hafta {i+1}"
    return bucket_start.strftime("%b")


class DashboardService:
    def __init__(self, db: Session):
        self.db = db
        self.product_service = ProductService(db)
        self.sale_service = SaleService(db)
        self._cache = _CACHE
        self._cache_ttl = 300  # 5 minutes cache

    def _get_cache_key(self, method_name: str, **kwargs) -> str:
        """Generate cache key for method with parameters."""
        key_data = {"method": method_name, **kwargs}
        return hashlib.md5(json.dumps(key_data, sort_keys=True, default=str).encode()).hexdigest()

    def _get_cached_data(self, cache_key: str) -> Optional[Any]:
        """Get cached data if not expired."""
        if cache_key in self._cache:
            data, timestamp = self._cache[cache_key]
            if datetime.now().timestamp() - timestamp < self._cache_ttl:
                return data
            self._cache.pop(cache_key, None)
        return None

    def _set_cache_data(self, cache_key: str, data: Any) -> None:
        """Set data in cache with timestamp."""
        self._cache[cache_key] = (data, datetime.now().timestamp())

    def get_dashboard_stats(self) -> Dict[str, Any]:
        """Get comprehensive dashboard statistics."""
        # Basic counts
        total_products = self.db.query(Product).count()
        total_clients = self.db.query(Client).count()
        total_sales = (
            self.db.query(Sale).filter(Sale.status == SaleStatus.COMPLETED).count()
        )

        # Revenue calculations
        total_revenue = self.db.query(func.sum(Sale.total_amount)).filter(
            Sale.status == SaleStatus.COMPLETED
        ).scalar() or Decimal("0")

        # Low stock product variants
        low_stock_count = (
            self.db.query(ProductVariant)
            .filter(ProductVariant.stock_quantity <= ProductVariant.min_stock_level)
            .count()
        )

        # Recent sales (last 7 days)
        week_ago = datetime.now() - timedelta(days=7)
        recent_sales = (
            self.db.query(Sale)
            .filter(Sale.status == SaleStatus.COMPLETED, Sale.created_at >= week_ago)
            .order_by(desc(Sale.created_at))
            .limit(5)
            .all()
        )

        recent_sales_data = []
        for sale in recent_sales:
            recent_sales_data.append(
                {
                    "id": sale.id,
                    "receipt_number": sale.receipt_number,
                    "amount": float(sale.total_amount),
                    "client_name": f"{sale.client.first_name} {sale.client.last_name}"
                    if sale.client
                    else "Walk-in",
                    "created_at": sale.created_at.isoformat(),
                }
            )

        # Monthly revenue (last 6 months), one grouped query.
        months = []
        for i in range(6):
            month_start = datetime.now().replace(day=1) - timedelta(days=30 * i)
            month_end = month_start.replace(day=28) + timedelta(days=4)
            month_end = month_end.replace(day=1) - timedelta(days=1)
            months.append((month_start, month_end))
        bucket = case(
            *[
                (and_(Sale.created_at >= ms, Sale.created_at <= me), i)
                for i, (ms, me) in enumerate(months)
            ],
            else_=None,
        ).label("bucket")
        revenue_by_month = dict(
            self.db.query(bucket, func.sum(Sale.total_amount))
            .filter(Sale.status == SaleStatus.COMPLETED, bucket.isnot(None))
            .group_by(bucket)
            .all()
        )
        monthly_revenue = [
            {
                "month": ms.strftime("%B %Y"),
                "revenue": float(revenue_by_month.get(i) or 0),
            }
            for i, (ms, _) in enumerate(months)
        ]

        # Top products by sales
        top_products = (
            self.db.query(
                Product.name,
                Brand.name.label("brand_name"),
                func.sum(SaleItem.quantity).label("total_sold"),
                func.sum(SaleItem.total_price).label("total_revenue"),
            )
            .join(ProductVariant, ProductVariant.product_id == Product.id)
            .join(SaleItem, SaleItem.product_variant_id == ProductVariant.id)
            .join(Sale, Sale.id == SaleItem.sale_id)
            .outerjoin(Brand, Product.brand_id == Brand.id)
            .filter(Sale.status == SaleStatus.COMPLETED)
            .group_by(Product.id, Product.name, Brand.name)
            .order_by(desc(func.sum(SaleItem.quantity)))
            .limit(5)
            .all()
        )

        top_products_data = []
        for product in top_products:
            top_products_data.append(
                {
                    "name": display_name(product.name, product.brand_name),
                    "total_sold": int(product.total_sold),
                    "total_revenue": float(product.total_revenue),
                }
            )

        # Clients with debts
        clients_with_debts = (
            self.db.query(Client)
            .join(Sale)
            .filter(Sale.status.in_([SaleStatus.DEBT, SaleStatus.PARTIALLY_PAID]))
            .distinct()
            .count()
        )

        # Total orders count (all completed sales)
        total_orders = total_sales

        # Same sources as the finance page: expense rows, salaries, stock purchases.
        # Refunds are not expenses — the cancelled sale already left revenue.
        monthly_expenses = expense_total(self.db, datetime.now() - timedelta(days=30))

        return {
            "total_products": total_products,
            "total_clients": total_clients,
            "total_sales": total_sales,
            "total_revenue": float(total_revenue),
            "total_orders": total_orders,
            "clients_with_debts": clients_with_debts,
            "monthly_expenses": float(monthly_expenses),
            "low_stock_products": low_stock_count,
            "recent_sales": recent_sales_data,
            "monthly_revenue": monthly_revenue,
            "top_products": top_products_data,
        }

    def get_recent_transactions(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent financial transactions."""
        transactions = (
            self.db.query(Transaction)
            .order_by(desc(Transaction.created_at))
            .limit(limit)
            .all()
        )

        transaction_data = []
        for transaction in transactions:
            transaction_data.append(
                {
                    "id": transaction.id,
                    "type": transaction.transaction_type.value if transaction.transaction_type else "UNKNOWN",
                    "amount": float(transaction.amount),
                    "description": transaction.description or "",
                    "created_at": transaction.created_at.isoformat(),
                }
            )

        return transaction_data

    def get_financial_summary(
        self, start_date: datetime = None, end_date: datetime = None
    ) -> Dict[str, Any]:
        """Get financial summary for a period."""
        query = self.db.query(Transaction)

        if start_date:
            query = query.filter(Transaction.created_at >= start_date)

        if end_date:
            query = query.filter(Transaction.created_at <= end_date)

        # Total transactions
        total_transactions = query.count()

        # Revenue (money coming in)
        revenue = query.filter(
            Transaction.transaction_type.in_(INFLOW_TYPES)
        ).with_entities(func.sum(Transaction.amount)).scalar() or Decimal("0")

        # Expenses (money going out)
        expenses = query.filter(
            Transaction.transaction_type.in_(OUTFLOW_TYPES)
        ).with_entities(func.sum(func.abs(Transaction.amount))).scalar() or Decimal("0")

        # Net profit
        net_profit = revenue - expenses

        # Transactions by type
        transactions_by_type = (
            query.with_entities(
                Transaction.transaction_type,
                func.count(Transaction.id).label("count"),
                # Magnitude only — the type already says which way it moved.
                func.sum(func.abs(Transaction.amount)).label("total"),
            )
            .group_by(Transaction.transaction_type)
            .all()
        )

        return {
            "total_transactions": total_transactions,
            "revenue": float(revenue),
            "expenses": float(expenses),
            "net_profit": float(net_profit),
            "transactions_by_type": [
                {"type": t.transaction_type.value, "count": t.count, "total": float(t.total)}
                for t in transactions_by_type
            ],
        }

    def _get_period_dates(self, period: str):
        """Get start and end dates for a given period."""
        now = datetime.now()
        
        if period == "1week":
            periods = 7
            interval = "day"
        elif period == "1month":
            periods = 4
            interval = "week"
        elif period == "3months":
            periods = 12
            interval = "week"
        elif period == "6months":
            periods = 6
            interval = "month"
        elif period == "1year":
            periods = 12
            interval = "month"
        else:
            # Default to 1 month
            periods = 4
            interval = "week"

        # Buckets must end exactly at now: 12 x 30-day buckets from now-365d
        # stopped 5 days short and dropped the latest sales from every chart.
        start_date = now - _STEP[interval] * periods
        return start_date, now, periods, interval

    def _bucketed(self, time_col, start, interval, periods, *aggregates, filters=(), joins=()):
        """{bucket index: aggregate row} in one grouped query (was 1-2 queries per bucket)."""
        step = _STEP[interval]
        k = func.floor(
            func.extract("epoch", time_col - start) / step.total_seconds()
        ).label("k")
        query = self.db.query(k, *aggregates)
        for j in joins:
            query = query.join(*j)
        rows = (
            query
            .filter(*filters, time_col >= start, time_col < start + step * periods)
            .group_by(k)
            .all()
        )
        return {int(r[0]): tuple(r[1:]) for r in rows}

    def _completed_sales_by_bucket(self, start, interval, periods):
        """{bucket: (revenue, order count)} for completed sales."""
        return self._bucketed(
            Sale.created_at, start, interval, periods,
            func.coalesce(func.sum(Sale.total_amount), 0), func.count(Sale.id),
            filters=(Sale.status == SaleStatus.COMPLETED,),
        )

    def _buckets(self, start, interval, periods):
        step = _STEP[interval]
        return [(i, _label(interval, i, start + step * i)) for i in range(periods)]

    def get_cashflow_data(self, period: str = "1month") -> List[Dict[str, Any]]:
        """Get cashflow data for charts."""
        cache_key = self._get_cache_key("get_cashflow_data", period=period)
        cached_data = self._get_cached_data(cache_key)
        if cached_data is not None:
            return cached_data

        start_date, end_date, periods, interval = self._get_period_dates(period)
        income_by = self._completed_sales_by_bucket(start_date, interval, periods)
        expense_by = self._bucketed(
            Transaction.created_at, start_date, interval, periods,
            func.coalesce(func.sum(func.abs(Transaction.amount)), 0),
            filters=(Transaction.transaction_type.in_(OUTFLOW_TYPES),),
        )

        data = []
        for i, label in self._buckets(start_date, interval, periods):
            income = income_by.get(i, (Decimal("0"), 0))[0]
            expenses = expense_by.get(i, (Decimal("0"),))[0]
            data.append({
                "month": label,
                "income": float(income),
                "expenses": float(expenses),
                "netFlow": float(income - expenses)
            })

        self._set_cache_data(cache_key, data)
        return data

    def get_profit_data(self, period: str = "1month") -> List[Dict[str, Any]]:
        """Get profit analysis data for charts."""
        cache_key = self._get_cache_key("get_profit_data", period=period)
        cached_data = self._get_cached_data(cache_key)
        if cached_data is not None:
            return cached_data

        start_date, end_date, periods, interval = self._get_period_dates(period)
        revenue_by = self._completed_sales_by_bucket(start_date, interval, periods)
        # Cost = what the sold goods cost (variant cost_price) + expenses + salaries.
        # ponytail: stock purchases are left out — counting them on top of the cost
        # of goods sold would charge the same goods twice. No cost_price = no cost.
        cogs_by = self._bucketed(
            Sale.created_at, start_date, interval, periods,
            func.coalesce(func.sum(SaleItem.quantity * func.coalesce(ProductVariant.cost_price, 0)), 0),
            filters=(Sale.status == SaleStatus.COMPLETED,),
            joins=((SaleItem, SaleItem.sale_id == Sale.id),
                   (ProductVariant, ProductVariant.id == SaleItem.product_variant_id)),
        )
        expense_by = self._bucketed(
            Expense.date, start_date, interval, periods,
            func.coalesce(func.sum(Expense.amount), 0),
        )
        salary_by = self._bucketed(
            SalaryPayment.payment_date, start_date, interval, periods,
            func.coalesce(func.sum(SalaryPayment.amount), 0),
        )

        data = []
        for i, label in self._buckets(start_date, interval, periods):
            revenue = revenue_by.get(i, (Decimal("0"), 0))[0]
            # Factory price of goods sold is kept apart from operating expenses.
            cost_of_goods = cogs_by.get(i, (Decimal("0"),))[0]
            expenses = expense_by.get(i, (Decimal("0"),))[0] + salary_by.get(i, (Decimal("0"),))[0]
            cost = cost_of_goods + expenses
            profit = revenue - cost
            margin = (profit / revenue * 100) if revenue > 0 else 0
            data.append({
                "month": label,
                "revenue": float(revenue),
                "cost": float(cost),
                "cost_of_goods": float(cost_of_goods),
                "expenses": float(expenses),
                "profit": float(profit),
                "margin": float(margin)
            })

        self._set_cache_data(cache_key, data)
        return data

    def get_sales_performance_data(self, period: str = "1month") -> List[Dict[str, Any]]:
        """Get sales performance data for charts."""
        cache_key = self._get_cache_key("get_sales_performance_data", period=period)
        cached_data = self._get_cached_data(cache_key)
        if cached_data is not None:
            return cached_data

        start_date, end_date, periods, interval = self._get_period_dates(period)
        sales_by = self._completed_sales_by_bucket(start_date, interval, periods)

        data = []
        previous_sales = 0
        for i, label in self._buckets(start_date, interval, periods):
            sales, orders = sales_by.get(i, (Decimal("0"), 0))
            avg_order = (sales / orders) if orders > 0 else 0
            growth = 0
            if previous_sales > 0:
                growth = ((float(sales) - previous_sales) / previous_sales) * 100
            previous_sales = float(sales)
            data.append({
                "month": label,
                "sales": float(sales),
                "orders": orders,
                "avgOrder": float(avg_order),
                "growth": growth
            })

        self._set_cache_data(cache_key, data)
        return data

    def get_expense_breakdown_data(self, period: str = "1month") -> List[Dict[str, Any]]:
        """Get expense breakdown data for charts."""
        cache_key = self._get_cache_key("get_expense_breakdown_data", period=period)
        cached_data = self._get_cached_data(cache_key)
        if cached_data is not None:
            return cached_data
            
        start_date, end_date, periods, interval = self._get_period_dates(period)
        
        totals = expense_totals_by_category(self.db, start_date, end_date)
        expense_data = sorted(
            ((k, v) for k, v in totals.items() if v), key=lambda kv: kv[1], reverse=True
        )

        # Define colors for different expense categories
        colors = [
            "#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", 
            "#06b6d4", "#64748b", "#f97316", "#84cc16", "#ec4899"
        ]
        
        data = []
        for i, (category, amount) in enumerate(expense_data):
            data.append({
                "name": category,  # category key; the chart maps it to a label
                "value": float(amount),
                "color": colors[i % len(colors)]
            })
        
        # If no expenses found, return sample data
        if not data:
            data = [
                {"name": "Xodimlar maoshi", "value": 0, "color": "#3b82f6"},
                {"name": "Mahsulot sotib olish", "value": 0, "color": "#10b981"},
                {"name": "Ijaraga to'lov", "value": 0, "color": "#f59e0b"},
                {"name": "Boshqa xarajatlar", "value": 0, "color": "#ef4444"},
            ]
        
        self._set_cache_data(cache_key, data)
        return data
