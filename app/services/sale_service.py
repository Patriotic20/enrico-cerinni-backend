from sqlalchemy.orm import Session, selectinload, joinedload
from sqlalchemy import func, or_
from typing import List, Optional, Tuple
from decimal import Decimal
from datetime import datetime, timedelta
from app.models.sale import Sale, SaleItem, SaleStatus, PaymentMethod
from app.models.product_variant import ProductVariant
from app.models.client import Client
from app.models.employee import Employee
from app.models.transaction import Transaction, TransactionType
from app.schemas.sale import SaleCreate, SaleUpdate, SaleFilter
from app.utils.helpers import (
    generate_receipt_number,
    calculate_total_price,
    paginate_query,
    calculate_pagination_info,
)
from fastapi import HTTPException, status
from app.models.user import User
from app.models.cart import CartStatus
from app.services.cart_service import lock_pending_cart, lock_variants, release_stock

# Everything _sale_response touches, loaded up front instead of lazily per row.
SALE_DETAILS = (
    selectinload(Sale.items)
    .joinedload(SaleItem.product_variant)
    .options(
        joinedload(ProductVariant.product),
        joinedload(ProductVariant.color),
        joinedload(ProductVariant.size),
    ),
    joinedload(Sale.client),
    joinedload(Sale.seller),
)


class SaleService:
    def __init__(self, db: Session):
        self.db = db

    def create_sale(self, sale_data: SaleCreate, current_user: User) -> Sale:
        """Create a new sale with items."""
        client = None
        if sale_data.client_id:
            # Row lock: concurrent sales/payments for one client must not lose debt updates.
            client = (
                self.db.query(Client)
                .filter(Client.id == sale_data.client_id)
                .with_for_update()
                .first()
            )
            if not client:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail="Client not found"
                )

        cart = lock_pending_cart(self.db, sale_data.cart_id) if sale_data.cart_id else None
        seller_id = cart.seller_id if cart else sale_data.seller_id

        seller = self.db.query(Employee).filter(Employee.id == seller_id).first()
        if not seller or not seller.is_active or not seller.is_seller:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Seller not found"
            )

        # One locked read for every variant: stops two tills selling the last unit.
        variant_ids = {i.product_variant_id for i in sale_data.items}
        if cart:
            variant_ids |= {i.product_variant_id for i in cart.items}
        variants = lock_variants(self.db, variant_ids)
        if cart:
            # The cart's reservation goes back to stock and the sale takes what it
            # really sells from there, so the cashier may still edit the items.
            release_stock(cart, variants)

        total_amount = Decimal("0")
        sale_items = []
        requested = {}

        for item_data in sale_data.items:
            product_variant = variants.get(item_data.product_variant_id)
            if not product_variant or not product_variant.is_active:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Product variant with ID {item_data.product_variant_id} not found",
                )

            # Same variant may appear on several lines; check the combined quantity.
            requested[product_variant.id] = requested.get(product_variant.id, 0) + item_data.quantity
            if product_variant.stock_quantity < requested[product_variant.id]:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Insufficient stock for product variant {product_variant.sku}",
                )

            # Cashiers may discount but never charge above the list price.
            if item_data.unit_price > product_variant.price:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Price for {product_variant.sku} exceeds list price {product_variant.price}",
                )

            item_total = item_data.unit_price * item_data.quantity
            total_amount += item_total

            sale_items.append(
                {"product_variant": product_variant, "data": item_data, "total": item_total}
            )

        if sale_data.paid_amount > total_amount:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Paid amount cannot exceed total amount"
            )

        if sale_data.paid_amount == 0:
            sale_status = SaleStatus.DEBT
        elif sale_data.paid_amount == total_amount:
            sale_status = SaleStatus.COMPLETED
        else:
            sale_status = SaleStatus.PARTIALLY_PAID

        if sale_data.paid_amount < total_amount and client:
            client.debt_amount += total_amount - sale_data.paid_amount

        # Create sale
        receipt_number = generate_receipt_number()
        db_sale = Sale(
            receipt_number=receipt_number,
            client_id=sale_data.client_id,
            total_amount=total_amount,
            paid_amount=sale_data.paid_amount,
            payment_method=sale_data.payment_method,
            status=sale_status,
            notes=sale_data.notes,
            user_id=current_user.id,
            seller_id=seller.id,
        )

        self.db.add(db_sale)
        self.db.flush()  # Get the sale ID

        if cart:
            cart.status = CartStatus.COMPLETED.value
            cart.sale_id = db_sale.id

        # Create sale items and update stock
        for item_info in sale_items:
            sale_item = SaleItem(
                sale_id=db_sale.id,
                product_variant_id=item_info["product_variant"].id,
                quantity=item_info["data"].quantity,
                unit_price=item_info["data"].unit_price,
                total_price=item_info["total"],
            )
            self.db.add(sale_item)

            # Update product variant stock
            item_info["product_variant"].stock_quantity -= item_info["data"].quantity

        # Create transaction record only for paid amount
        if sale_data.paid_amount > 0:
            transaction = Transaction(
                transaction_type=TransactionType.SALE,
                amount=sale_data.paid_amount,
                description=f"Sale {receipt_number} - Paid amount",
                sale_id=db_sale.id,
                client_id=sale_data.client_id,
                user_id=current_user.id,
            )
            self.db.add(transaction)

        self.db.commit()
        return self.get_sale(db_sale.id)

    def get_sale(self, sale_id: int) -> Optional[Sale]:
        """Get a sale by ID."""
        return (
            self.db.query(Sale).options(*SALE_DETAILS).filter(Sale.id == sale_id).first()
        )

    def _lock_sale(self, sale_id: int) -> Optional[Sale]:
        """Sale row under FOR UPDATE: serialises cancel / pay-debt on the same sale."""
        return self.db.query(Sale).filter(Sale.id == sale_id).with_for_update().first()

    def filtered_query(self, filters: SaleFilter):
        """Sales query with every list filter applied (no ordering/paging)."""
        query = self.db.query(Sale)

        # Apply filters
        if filters.client_id:
            query = query.filter(Sale.client_id == filters.client_id)

        if filters.seller_id:
            query = query.filter(Sale.seller_id == filters.seller_id)

        if filters.payment_method:
            query = query.filter(Sale.payment_method == filters.payment_method)

        if filters.status:
            query = query.filter(Sale.status == filters.status)

        if filters.start_date:
            start_date = datetime.fromisoformat(filters.start_date)
            query = query.filter(Sale.created_at >= start_date)

        if filters.end_date:
            # A bare date means "through that day", not "up to its midnight".
            end_date = datetime.fromisoformat(filters.end_date)
            if len(filters.end_date) == 10:
                query = query.filter(Sale.created_at < end_date + timedelta(days=1))
            else:
                query = query.filter(Sale.created_at <= end_date)

        # Free-text search over the receipt number and the client's name, which
        # is what the sales page's search box offers.
        if filters.search:
            term = f"%{filters.search.strip()}%"
            query = query.outerjoin(Client, Sale.client_id == Client.id).filter(
                or_(
                    Sale.receipt_number.ilike(term),
                    Client.first_name.ilike(term),
                    Client.last_name.ilike(term),
                )
            )

        if filters.min_amount is not None:
            query = query.filter(Sale.total_amount >= filters.min_amount)

        if filters.max_amount is not None:
            query = query.filter(Sale.total_amount <= filters.max_amount)

        return query

    def get_sales(self, filters: SaleFilter) -> Tuple[List[Sale], dict]:
        """Get sales with filtering and pagination."""
        query = self.filtered_query(filters)

        # Get total count
        total = query.count()

        query = query.options(*SALE_DETAILS).order_by(Sale.created_at.desc())
        # Apply pagination
        query = paginate_query(query, filters.page, filters.size)

        # Get results
        sales = query.all()

        # Calculate pagination info
        pagination = calculate_pagination_info(total, filters.page, filters.size)

        return sales, pagination

    def get_stats(self, filters: SaleFilter) -> dict:
        """Aggregates over the same filtered set the list shows.

        Cancelled sales are counted separately and excluded from money totals.
        """
        rows = (
            self.filtered_query(filters)
            .with_entities(
                Sale.status,
                func.count(Sale.id),
                func.coalesce(func.sum(Sale.total_amount), 0),
                func.coalesce(func.sum(Sale.paid_amount), 0),
            )
            .group_by(Sale.status)
            .all()
        )
        count = revenue = paid = Decimal(0)
        cancelled = debt_sales = 0
        for st, n, total, paid_sum in rows:
            st = st.value if hasattr(st, "value") else st
            if st == SaleStatus.CANCELLED.value:
                cancelled += n
                continue
            count += n
            revenue += Decimal(total)
            paid += Decimal(paid_sum)
            if st in (SaleStatus.DEBT.value, SaleStatus.PARTIALLY_PAID.value):
                debt_sales += n
        return {
            "total_sales": int(count),
            "total_revenue": float(revenue),
            "paid_amount": float(paid),
            "outstanding": float(revenue - paid),
            "avg_order_value": float(revenue / count) if count else 0.0,
            "debt_sales": debt_sales,
            "cancelled_sales": cancelled,
        }

    def cancel_sale(self, sale_id: int, current_user: User) -> Optional[Sale]:
        """Cancel a sale and restore stock."""
        sale = self._lock_sale(sale_id)
        if not sale:
            return None

        if sale.status == SaleStatus.CANCELLED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Sale is already cancelled",
            )

        # Restore stock atomically in SQL so a concurrent sale can't lose the update.
        for item in sale.items:
            self.db.query(ProductVariant).filter(
                ProductVariant.id == item.product_variant_id
            ).update(
                {ProductVariant.stock_quantity: ProductVariant.stock_quantity + item.quantity},
                synchronize_session=False,
            )

        # Update client debt if applicable
        if sale.status in [SaleStatus.DEBT, SaleStatus.PARTIALLY_PAID] and sale.client_id:
            client = (
                self.db.query(Client).filter(Client.id == sale.client_id).with_for_update().first()
            )
            if client:
                client.debt_amount -= (sale.total_amount - sale.paid_amount)

        # Update sale status
        sale.status = SaleStatus.CANCELLED

        # Refund only the money actually taken; an unpaid debt sale refunds nothing.
        if sale.paid_amount > 0:
            self.db.add(Transaction(
                transaction_type=TransactionType.REFUND,
                amount=-sale.paid_amount,
                description=f"Refund for cancelled sale {sale.receipt_number}",
                sale_id=sale.id,
                client_id=sale.client_id,
                user_id=current_user.id,
            ))

        self.db.commit()
        return self.get_sale(sale.id)

    def pay_debt(self, sale_id: int, payment_amount: Decimal, current_user: User) -> Sale:
        """Pay remaining debt for a sale."""
        if payment_amount <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Payment amount must be greater than zero"
            )
        sale = self._lock_sale(sale_id)
        if not sale:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Sale not found"
            )

        if sale.status == SaleStatus.CANCELLED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot pay debt for cancelled sale"
            )

        if sale.status == SaleStatus.COMPLETED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Sale is already fully paid"
            )

        remaining_debt = sale.total_amount - sale.paid_amount
        if payment_amount > remaining_debt:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Payment amount exceeds remaining debt. Remaining debt: {remaining_debt}"
            )

        # Update client debt
        if sale.client_id:
            client = (
                self.db.query(Client).filter(Client.id == sale.client_id).with_for_update().first()
            )
            if client:
                client.debt_amount -= payment_amount

        # Update paid amount
        sale.paid_amount += payment_amount

        # Update sale status
        if sale.paid_amount == sale.total_amount:
            sale.status = SaleStatus.COMPLETED
        else:
            sale.status = SaleStatus.PARTIALLY_PAID

        # Create transaction for the payment
        transaction = Transaction(
            transaction_type=TransactionType.DEBT_PAYMENT,
            amount=payment_amount,
            description=f"Debt payment for sale {sale.receipt_number}",
            sale_id=sale.id,
            client_id=sale.client_id,
            user_id=current_user.id,
        )
        self.db.add(transaction)

        self.db.commit()
        return self.get_sale(sale.id)

    def get_client_debts(self, client_id: int) -> List[Sale]:
        """Get all debt sales for a specific client."""
        return (
            self.db.query(Sale)
            .options(*SALE_DETAILS)
            .filter(
                Sale.client_id == client_id,
                Sale.status.in_([SaleStatus.DEBT, SaleStatus.PARTIALLY_PAID])
            )
            .order_by(Sale.created_at.desc())
            .all()
        )
