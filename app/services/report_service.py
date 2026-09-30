from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, and_, or_, desc, asc, select
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
from decimal import Decimal
import json

from app.models.sale import Sale, SaleItem, PaymentMethod, SaleStatus
from app.models.client import Client
from app.models.product import Product
from app.models.category import Category
from app.models.product_variant import ProductVariant
from app.models.size import Size
from app.models.color import Color
from app.models.expense import Expense
from app.models.transaction import Transaction
from app.models.report import Report, ReportTemplate, ReportExecution, ReportType
from app.schemas.report import (
    ReportFilters,
    SalesReportData, SalesMetric, TopProduct, SalesTrendPoint,
    FinanceReportData, FinanceMetric, ExpenseBreakdown, MonthlyFinanceData, PaymentMethodBreakdown,
    InventoryReportData, InventoryMetric, ProductMovement,
    ClientsReportData, ClientMetric, TopClient,
    PerformanceReportData, PerformanceMetric, KPIData,
    CustomReportData, CustomReportConfig
)
from app.services.expense_totals import expense_totals_by_category


class ReportService:
    def __init__(self, db: Session):
        self.db = db

    def _apply_date_filter(self, query, date_field, filters: Optional[ReportFilters]):
        """Apply date range filter to query."""
        if not filters or not filters.date_range:
            return query
        
        if filters.date_range.start_date:
            query = query.filter(date_field >= filters.date_range.start_date)
        
        if filters.date_range.end_date:
            query = query.filter(date_field <= filters.date_range.end_date)
        
        return query

    def _get_date_range(self, filters: Optional[ReportFilters]) -> Tuple[datetime, datetime]:
        """Get effective date range for reports."""
        if filters and filters.date_range:
            start_date = filters.date_range.start_date or (datetime.now() - timedelta(days=30))
            end_date = filters.date_range.end_date or datetime.now()
            # A bare YYYY-MM-DD parses as midnight, which silently dropped
            # every sale made on the last day of the range.
            if end_date == end_date.replace(hour=0, minute=0, second=0, microsecond=0):
                end_date += timedelta(days=1) - timedelta(microseconds=1)
        else:
            # Default to last 30 days
            end_date = datetime.now()
            start_date = end_date - timedelta(days=30)
        
        return start_date, end_date

    def generate_sales_report(self, filters: Optional[ReportFilters] = None) -> SalesReportData:
        """Generate comprehensive sales report."""
        start_date, end_date = self._get_date_range(filters)
        
        # Base query for sales in the period
        base_query = self.db.query(Sale).filter(
            Sale.created_at >= start_date,
            Sale.created_at <= end_date,
            Sale.status != SaleStatus.CANCELLED
        )
        
        # Apply additional filters
        if filters:
            if filters.client_ids:
                base_query = base_query.filter(Sale.client_id.in_(filters.client_ids))
            if filters.payment_methods:
                base_query = base_query.filter(Sale.payment_method.in_(filters.payment_methods))
            if filters.min_amount:
                base_query = base_query.filter(Sale.total_amount >= filters.min_amount)
            if filters.max_amount:
                base_query = base_query.filter(Sale.total_amount <= filters.max_amount)

        # Calculate metrics in SQL; loading every sale of a long period was the
        # slow part of this report.
        total_revenue, total_sales, unique_clients, outstanding = base_query.with_entities(
            func.coalesce(func.sum(Sale.total_amount), 0),
            func.count(Sale.id),
            func.count(func.distinct(Sale.client_id)),
            func.coalesce(
                func.sum(func.greatest(Sale.total_amount - Sale.paid_amount, 0)), 0
            ),
        ).one()
        total_revenue = Decimal(total_revenue)
        avg_order_value = total_revenue / total_sales if total_sales > 0 else Decimal('0')

        items_sold = (
            base_query.join(SaleItem, SaleItem.sale_id == Sale.id)
            .with_entities(func.coalesce(func.sum(SaleItem.quantity), 0))
            .scalar()
        )

        metrics = SalesMetric(
            total_revenue=total_revenue,
            total_sales=total_sales,
            avg_order_value=avg_order_value,
            items_sold=int(items_sold),
            unique_clients=unique_clients,
            outstanding=Decimal(outstanding),
        )

        # Top products, names joined in the same query (was 3+ queries per row).
        total_quantity = func.sum(SaleItem.quantity).label('total_quantity')
        top_products_query = (
            self.db.query(
                Product.id,
                Product.name,
                Size.name.label('size_name'),
                Color.name.label('color_name'),
                total_quantity,
                func.sum(SaleItem.total_price).label('total_revenue'),
            )
            .select_from(SaleItem)
            .join(Sale, SaleItem.sale_id == Sale.id)
            .join(ProductVariant, SaleItem.product_variant_id == ProductVariant.id)
            .join(Product, ProductVariant.product_id == Product.id)
            .outerjoin(Size, ProductVariant.size_id == Size.id)
            .outerjoin(Color, ProductVariant.color_id == Color.id)
            .filter(
                Sale.created_at >= start_date,
                Sale.created_at <= end_date,
                Sale.status != SaleStatus.CANCELLED
            )
            .group_by(SaleItem.product_variant_id, Product.id, Product.name, Size.name, Color.name)
            .order_by(desc('total_quantity'))
            .limit(10)
        )

        top_products = [
            TopProduct(
                product_id=row.id,
                product_name=row.name,
                variant_name=f"{row.size_name or ''} {row.color_name or ''}".strip(),
                sales_count=int(row.total_quantity),
                total_revenue=row.total_revenue,
            )
            for row in top_products_query.all()
        ]

        # Get sales trend (daily data)
        trend_query = (
            self.db.query(
                func.date(Sale.created_at).label('sale_date'),
                func.count(Sale.id).label('sales_count'),
                func.sum(Sale.total_amount).label('revenue')
            )
            .filter(
                Sale.created_at >= start_date,
                Sale.created_at <= end_date,
                Sale.status != SaleStatus.CANCELLED
            )
            .group_by(func.date(Sale.created_at))
            .order_by('sale_date')
        )

        # Days without sales are emitted as zeros; skipping them made the chart
        # draw a line straight across empty days as if sales had happened.
        by_day = {str(item.sale_date): item for item in trend_query.all()}
        sales_trend = []
        day = start_date.date() if isinstance(start_date, datetime) else start_date
        last_day = end_date.date() if isinstance(end_date, datetime) else end_date
        while day <= last_day:
            item = by_day.get(str(day))
            sales_trend.append(SalesTrendPoint(
                date=datetime.combine(day, datetime.min.time()),
                sales_count=item.sales_count if item else 0,
                revenue=(item.revenue or Decimal('0')) if item else Decimal('0'),
            ))
            day += timedelta(days=1)

        # Sales by payment method
        payment_query = (
            self.db.query(
                Sale.payment_method,
                func.sum(Sale.total_amount).label('total')
            )
            .filter(
                Sale.created_at >= start_date,
                Sale.created_at <= end_date,
                Sale.status != SaleStatus.CANCELLED
            )
            .group_by(Sale.payment_method)
        )

        sales_by_payment_method = {}
        for item in payment_query.all():
            sales_by_payment_method[item.payment_method.value] = item.total

        category_query = (
            self.db.query(
                func.coalesce(Category.name, 'Kategoriyasiz').label('name'),
                func.sum(SaleItem.total_price).label('total'),
            )
            .join(Sale, SaleItem.sale_id == Sale.id)
            .join(ProductVariant, SaleItem.product_variant_id == ProductVariant.id)
            .join(Product, ProductVariant.product_id == Product.id)
            .outerjoin(Category, Product.category_id == Category.id)
            .filter(
                Sale.created_at >= start_date,
                Sale.created_at <= end_date,
                Sale.status != SaleStatus.CANCELLED
            )
            .group_by(Category.name)
        )
        sales_by_category = {row.name: row.total or Decimal('0') for row in category_query.all()}

        return SalesReportData(
            metrics=metrics,
            top_products=top_products,
            sales_trend=sales_trend,
            sales_by_payment_method=sales_by_payment_method,
            sales_by_category=sales_by_category
        )

    def generate_finance_report(self, filters: Optional[ReportFilters] = None) -> FinanceReportData:
        """Generate comprehensive finance report."""
        start_date, end_date = self._get_date_range(filters)
        
        # Calculate revenue from sales
        revenue_query = (
            self.db.query(func.sum(Sale.total_amount))
            .filter(
                Sale.created_at >= start_date,
                Sale.created_at <= end_date,
                Sale.status != SaleStatus.CANCELLED
            )
        )
        total_revenue = revenue_query.scalar() or Decimal('0')

        # Calculate expenses. Salaries and stock purchases live in their own
        # tables, so the shared helper folds them in — counting only Expense
        # rows understated the total and left the suppliers/salaries slices of
        # the breakdown permanently at zero.
        expense_categories = expense_totals_by_category(self.db, start_date, end_date)
        total_expenses = sum(expense_categories.values(), Decimal('0'))

        # Calculate metrics
        net_profit = total_revenue - total_expenses
        profit_margin = float((net_profit / total_revenue * 100)) if total_revenue > 0 else 0.0
        cash_flow = net_profit  # Simplified calculation

        metrics = FinanceMetric(
            total_revenue=total_revenue,
            total_expenses=total_expenses,
            net_profit=net_profit,
            profit_margin=profit_margin,
            cash_flow=cash_flow
        )

        # Keys match the canonical values in app.models.expense.EXPENSE_CATEGORIES.
        named_categories = ['supplier_costs', 'salary', 'rent', 'utilities', 'marketing']
        expense_breakdown = ExpenseBreakdown(
            suppliers=expense_categories.get('supplier_costs', Decimal('0')),
            salaries=expense_categories.get('salary', Decimal('0')),
            rent=expense_categories.get('rent', Decimal('0')),
            utilities=expense_categories.get('utilities', Decimal('0')),
            marketing=expense_categories.get('marketing', Decimal('0')),
            other=sum(
                (v for k, v in expense_categories.items() if k not in named_categories),
                Decimal('0'),
            )
        )

        # Last 6 calendar months, oldest first. Stepping back 30 days at a time
        # skipped short months, and counting only Expense rows left salaries
        # and stock purchases out of the monthly expense bars.
        monthly_data = []
        anchor = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        for i in range(5, -1, -1):
            year, month = anchor.year, anchor.month - i
            while month < 1:
                year, month = year - 1, month + 12
            month_start = anchor.replace(year=year, month=month)
            month_end = (month_start + timedelta(days=32)).replace(day=1) - timedelta(microseconds=1)

            month_revenue = (
                self.db.query(func.sum(Sale.total_amount))
                .filter(
                    Sale.created_at >= month_start,
                    Sale.created_at <= month_end,
                    Sale.status != SaleStatus.CANCELLED
                )
                .scalar() or Decimal('0')
            )
            month_expenses = sum(
                expense_totals_by_category(self.db, month_start, month_end).values(),
                Decimal('0'),
            )

            monthly_data.append(MonthlyFinanceData(
                month=month_start.strftime('%m.%Y'),
                revenue=month_revenue,
                expenses=month_expenses,
                profit=month_revenue - month_expenses
            ))

        # Payment method breakdown
        payment_breakdown_query = (
            self.db.query(
                Sale.payment_method,
                func.sum(Sale.total_amount).label('total')
            )
            .filter(
                Sale.created_at >= start_date,
                Sale.created_at <= end_date,
                Sale.status != SaleStatus.CANCELLED
            )
            .group_by(Sale.payment_method)
        )

        payment_totals = {item.payment_method.value: item.total for item in payment_breakdown_query.all()}
        payment_methods = PaymentMethodBreakdown(
            cash=payment_totals.get('cash', Decimal('0')),
            card=payment_totals.get('card', Decimal('0')),
            transfer=payment_totals.get('transfer', Decimal('0')),
            # Unpaid remainder of the period's sales, not a payment method.
            debt=(
                self.db.query(func.coalesce(func.sum(Sale.total_amount - Sale.paid_amount), 0))
                .filter(
                    Sale.created_at >= start_date,
                    Sale.created_at <= end_date,
                    Sale.status != SaleStatus.CANCELLED,
                    Sale.total_amount > Sale.paid_amount,
                )
                .scalar() or Decimal('0')
            ),
        )

        return FinanceReportData(
            metrics=metrics,
            expense_breakdown=expense_breakdown,
            monthly_data=monthly_data,
            payment_methods=payment_methods
        )

    def generate_inventory_report(self, filters: Optional[ReportFilters] = None) -> InventoryReportData:
        """Stock position now, plus how fast each item sold over the period."""
        start_date, end_date = self._get_date_range(filters)
        days = max(round((end_date - start_date).total_seconds() / 86400), 1)

        total_products = self.db.query(Product).count()
        total_variants = self.db.query(ProductVariant).count()

        # "Low" follows each variant's own min_stock_level, same rule as the
        # inventory page, instead of one global threshold of 10.
        low_filter = and_(
            ProductVariant.stock_quantity > 0,
            ProductVariant.stock_quantity <= ProductVariant.min_stock_level,
        )
        low_stock_items = self.db.query(ProductVariant).filter(low_filter).count()
        out_of_stock_items = self.db.query(ProductVariant).filter(
            ProductVariant.stock_quantity <= 0
        ).count()

        total_inventory_value = (
            self.db.query(func.sum(ProductVariant.price * ProductVariant.stock_quantity))
            .filter(ProductVariant.stock_quantity > 0)
            .scalar() or Decimal('0')
        )

        metrics = InventoryMetric(
            total_products=total_products,
            total_variants=total_variants,
            low_stock_items=low_stock_items,
            out_of_stock_items=out_of_stock_items,
            total_inventory_value=total_inventory_value
        )

        sold_by_variant = dict(
            self.db.query(SaleItem.product_variant_id, func.sum(SaleItem.quantity))
            .join(Sale, SaleItem.sale_id == Sale.id)
            .filter(
                Sale.created_at >= start_date,
                Sale.created_at <= end_date,
                Sale.status != SaleStatus.CANCELLED,
            )
            .group_by(SaleItem.product_variant_id)
            .all()
        )

        def movement(variant):
            sold = int(sold_by_variant.get(variant.id, 0) or 0)
            return ProductMovement(
                product_id=variant.product_id,
                product_name=variant.product.name if variant.product else "Unknown",
                variant_name=f"{variant.size.name if variant.size else ''} {variant.color.name if variant.color else ''}".strip(),
                current_stock=variant.stock_quantity,
                sold_quantity=sold,
                movement_velocity=round(sold / days, 2),
            )

        with_names = (
            joinedload(ProductVariant.product),
            joinedload(ProductVariant.size),
            joinedload(ProductVariant.color),
        )
        low_stock_products = [
            movement(v) for v in
            self.db.query(ProductVariant)
            .options(*with_names)
            .filter(or_(low_filter, ProductVariant.stock_quantity <= 0))
            .order_by(asc(ProductVariant.stock_quantity))
            .limit(50)
            .all()
        ]

        top_ids = sorted(sold_by_variant, key=lambda k: sold_by_variant[k], reverse=True)[:10]
        top_variants = {
            v.id: v for v in
            self.db.query(ProductVariant).options(*with_names)
            .filter(ProductVariant.id.in_(top_ids)).all()
        } if top_ids else {}
        top_moving_products = [movement(top_variants[i]) for i in top_ids if i in top_variants]

        # Units on hand per category.
        inventory_by_category = {
            name: int(units or 0) for name, units in
            self.db.query(
                func.coalesce(Category.name, 'Kategoriyasiz'),
                func.sum(ProductVariant.stock_quantity),
            )
            .join(Product, ProductVariant.product_id == Product.id)
            .outerjoin(Category, Product.category_id == Category.id)
            .group_by(Category.name)
            .all()
        }

        return InventoryReportData(
            metrics=metrics,
            low_stock_products=low_stock_products,
            top_moving_products=top_moving_products,
            inventory_by_category=inventory_by_category
        )

    def generate_clients_report(self, filters: Optional[ReportFilters] = None) -> ClientsReportData:
        """Generate clients report."""
        start_date, end_date = self._get_date_range(filters)
        
        # Basic client metrics
        total_clients = self.db.query(Client).count()
        
        # Active clients (with purchases in period)
        active_clients = (
            self.db.query(Client.id)
            .join(Sale)
            .filter(
                Sale.created_at >= start_date,
                Sale.created_at <= end_date,
                Sale.status != SaleStatus.CANCELLED
            )
            .distinct()
            .count()
        )

        # New clients in period
        new_clients = (
            self.db.query(Client)
            .filter(
                Client.created_at >= start_date,
                Client.created_at <= end_date
            )
            .count()
        )

        # Average receipt of client-attached sales in the period, and lifetime
        # spend per client who has ever bought.
        period_client_sales = self.db.query(
            func.coalesce(func.sum(Sale.total_amount), 0), func.count(Sale.id)
        ).filter(
            Sale.client_id.isnot(None),
            Sale.created_at >= start_date,
            Sale.created_at <= end_date,
            Sale.status != SaleStatus.CANCELLED,
        ).one()
        avg_order_value = (
            Decimal(period_client_sales[0]) / period_client_sales[1]
            if period_client_sales[1] else Decimal('0')
        )
        lifetime = self.db.query(
            func.coalesce(func.sum(Sale.total_amount), 0), func.count(func.distinct(Sale.client_id))
        ).filter(
            Sale.client_id.isnot(None),
            Sale.status != SaleStatus.CANCELLED,
        ).one()
        customer_lifetime_value = Decimal(lifetime[0]) / lifetime[1] if lifetime[1] else Decimal('0')

        metrics = ClientMetric(
            total_clients=total_clients,
            active_clients=active_clients,
            new_clients=new_clients,
            avg_order_value=avg_order_value,
            customer_lifetime_value=customer_lifetime_value
        )

        # Top clients by purchase amount
        top_clients_query = (
            self.db.query(
                Client.id,
                Client.first_name,
                Client.last_name,
                Client.phone,
                func.sum(Sale.total_amount).label('total_purchases'),
                func.count(Sale.id).label('order_count'),
                func.max(Sale.created_at).label('last_purchase')
            )
            .join(Sale)
            .filter(
                Sale.created_at >= start_date,
                Sale.created_at <= end_date,
                Sale.status != SaleStatus.CANCELLED
            )
            .group_by(Client.id, Client.first_name, Client.last_name, Client.phone)
            .order_by(desc('total_purchases'))
            .limit(10)
        )

        top_clients = []
        for item in top_clients_query.all():
            top_clients.append(TopClient(
                client_id=item.id,
                client_name=f"{item.first_name} {item.last_name}",
                phone=item.phone,
                total_purchases=item.total_purchases,
                order_count=item.order_count,
                last_purchase_date=item.last_purchase
            ))

        # New clients per calendar month, last 6 months, oldest first.
        client_acquisition_trend = []
        anchor = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        for i in range(5, -1, -1):
            year, month = anchor.year, anchor.month - i
            while month < 1:
                year, month = year - 1, month + 12
            month_start = anchor.replace(year=year, month=month)
            month_end = (month_start + timedelta(days=32)).replace(day=1)
            client_acquisition_trend.append({
                "month": month_start.strftime('%m.%Y'),
                "new_clients": self.db.query(Client).filter(
                    Client.created_at >= month_start, Client.created_at < month_end
                ).count(),
            })

        # Mutually exclusive segments for the selected period, counted in SQL.
        def bought(*conditions):
            return (
                select(Sale.id)
                .where(Sale.client_id == Client.id, Sale.status != SaleStatus.CANCELLED, *conditions)
                .exists()
            )

        is_new = and_(Client.created_at >= start_date, Client.created_at <= end_date)
        in_period = bought(Sale.created_at >= start_date, Sale.created_at <= end_date)
        ever = bought()
        new_n, active_n, inactive_n, never_n = self.db.query(
            func.count().filter(is_new),
            func.count().filter(~is_new, in_period),
            func.count().filter(~is_new, ~in_period, ever),
            func.count().filter(~is_new, ~ever),
        ).select_from(Client).one()
        clients_by_segment = {
            "new": new_n,
            "active": active_n,
            "inactive": inactive_n,
            "never_bought": never_n,
        }

        return ClientsReportData(
            metrics=metrics,
            top_clients=top_clients,
            client_acquisition_trend=client_acquisition_trend,
            clients_by_segment=clients_by_segment
        )

    def generate_performance_report(self, filters: Optional[ReportFilters] = None) -> PerformanceReportData:
        """Generate performance report.

        Every figure here is computed by comparing the selected period against
        the immediately preceding period of the same length. It used to return
        hardcoded constants (15.2, 12.8, 4.2 ...) that never moved with the data.
        """
        start_date, end_date = self._get_date_range(filters)
        span = end_date - start_date
        prev_start, prev_end = start_date - span, start_date

        def period_sales(begin, finish):
            return self.db.query(Sale).filter(
                Sale.created_at >= begin,
                Sale.created_at <= finish,
                Sale.status != SaleStatus.CANCELLED,
            )

        def growth(current, previous):
            """Percentage change; 0 when there is no baseline to compare to."""
            if not previous:
                return 0.0
            return float((current - previous) / previous * 100)

        current_revenue = period_sales(start_date, end_date).with_entities(
            func.coalesce(func.sum(Sale.total_amount), 0)
        ).scalar() or Decimal("0")
        previous_revenue = period_sales(prev_start, prev_end).with_entities(
            func.coalesce(func.sum(Sale.total_amount), 0)
        ).scalar() or Decimal("0")

        current_count = period_sales(start_date, end_date).count()
        previous_count = period_sales(prev_start, prev_end).count()

        # Margin trend is the change in percentage points, not a ratio.
        current_expenses = sum(
            expense_totals_by_category(self.db, start_date, end_date).values(), Decimal("0")
        )
        previous_expenses = sum(
            expense_totals_by_category(self.db, prev_start, prev_end).values(), Decimal("0")
        )
        current_margin = (
            float((current_revenue - current_expenses) / current_revenue * 100)
            if current_revenue > 0 else 0.0
        )
        previous_margin = (
            float((previous_revenue - previous_expenses) / previous_revenue * 100)
            if previous_revenue > 0 else 0.0
        )

        # Turnover = cost of goods sold in the period / value of stock on hand.
        cogs = self.db.query(
            func.coalesce(func.sum(SaleItem.quantity * ProductVariant.cost_price), 0)
        ).join(
            ProductVariant, SaleItem.product_variant_id == ProductVariant.id
        ).join(
            Sale, SaleItem.sale_id == Sale.id
        ).filter(
            Sale.created_at >= start_date,
            Sale.created_at <= end_date,
            Sale.status != SaleStatus.CANCELLED,
        ).scalar() or Decimal("0")

        stock_value = self.db.query(
            func.coalesce(
                func.sum(ProductVariant.stock_quantity * ProductVariant.cost_price), 0
            )
        ).scalar() or Decimal("0")
        inventory_turnover = float(cogs / stock_value) if stock_value > 0 else 0.0

        # Retention: of the clients who bought last period, how many came back.
        def buyer_ids(begin, finish):
            rows = period_sales(begin, finish).filter(
                Sale.client_id.isnot(None)
            ).with_entities(Sale.client_id).distinct().all()
            return {row[0] for row in rows}

        previous_buyers = buyer_ids(prev_start, prev_end)
        returning = previous_buyers & buyer_ids(start_date, end_date)
        retention = (
            len(returning) / len(previous_buyers) * 100 if previous_buyers else 0.0
        )

        metrics = PerformanceMetric(
            revenue_growth_rate=round(growth(current_revenue, previous_revenue), 2),
            sales_growth_rate=round(growth(current_count, previous_count), 2),
            profit_margin_trend=round(current_margin - previous_margin, 2),
            inventory_turnover=round(inventory_turnover, 2),
            customer_retention_rate=round(retention, 2),
        )

        # Month-over-month growth for the last six months.
        monthly_performance = []
        month_anchor = datetime.now().replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        month_starts = [
            (month_anchor - timedelta(days=i * 30)).replace(day=1) for i in range(5, -1, -1)
        ]
        first_before = (month_starts[0] - timedelta(days=1)).replace(day=1)
        last_end = (month_starts[-1] + timedelta(days=32)).replace(day=1)
        # One grouped query for all seven months instead of four per month.
        month = func.date_trunc("month", Sale.created_at)
        by_month = {
            m.replace(tzinfo=None): (revenue, count)
            for m, revenue, count in period_sales(first_before, last_end)
            .filter(Sale.created_at < last_end)
            .with_entities(month, func.coalesce(func.sum(Sale.total_amount), 0), func.count(Sale.id))
            .group_by(month)
            .all()
        }

        for month_start in month_starts:
            before_start = (month_start - timedelta(days=1)).replace(day=1)
            revenue, count = by_month.get(month_start, (Decimal("0"), 0))
            revenue_before, count_before = by_month.get(before_start, (Decimal("0"), 0))

            monthly_performance.append({
                "month": month_start.strftime("%m.%Y"),
                "revenue": float(revenue),
                "revenue_growth": round(growth(revenue, revenue_before), 2),
                "sales_growth": round(growth(count, count_before), 2),
            })

        # KPIs are intentionally empty: targets are a business input and no
        # table holds them. Returning invented targets would look authoritative
        # while being fiction.
        return PerformanceReportData(
            metrics=metrics,
            kpis=[],
            monthly_performance=monthly_performance,
        )

    def generate_custom_report(self, config: CustomReportConfig, filters: Optional[ReportFilters] = None) -> CustomReportData:
        """Generate custom report based on configuration."""
        # This would implement a flexible report builder
        # For now, return placeholder data
        data = {
            "selected_metrics": config.selected_metrics,
            "chart_data": [{"name": "Sample", "value": 100}],
            "table_data": [{"column1": "value1", "column2": "value2"}]
        }
        
        charts = [
            {"type": "bar", "data": [1, 2, 3, 4, 5]},
            {"type": "line", "data": [10, 20, 15, 25, 30]}
        ]

        return CustomReportData(
            config=config,
            data=data,
            charts=charts
        )

    def save_report(self, report_type: ReportType, name: str, data: Dict[str, Any], user_id: int) -> Report:
        """Save a generated report to database."""
        report = Report(
            name=name,
            report_type=report_type,
            config={"filters": {}, "generated_data": data},
            status="completed",
            user_id=user_id,
            generated_at=datetime.now()
        )
        
        self.db.add(report)
        self.db.commit()
        self.db.refresh(report)
        
        return report

    def get_report_templates(self, report_type: Optional[ReportType] = None) -> List[ReportTemplate]:
        """Get available report templates."""
        query = self.db.query(ReportTemplate).filter(ReportTemplate.is_active == True)
        
        if report_type:
            query = query.filter(ReportTemplate.report_type == report_type)
        
        return query.all()

    def get_saved_reports(self, user_id: int, limit: int = 50, offset: int = 0) -> List[Report]:
        """Get user's saved reports."""
        return (
            self.db.query(Report)
            .filter(Report.user_id == user_id)
            .order_by(desc(Report.created_at))
            .limit(limit)
            .offset(offset)
            .all()
        )
