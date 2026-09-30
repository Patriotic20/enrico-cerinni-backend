from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime, date
from decimal import Decimal

from app.database import get_db
from app.services.sale_service import SaleService
from app.schemas.sale import (
    SaleCreate,
    SaleUpdate,
    SaleResponse,
    SaleFilter,
    SaleItemResponse,
    PaginatedSaleResponse,
    DebtPaymentRequest,
)
from app.schemas.common import ResponseModel, PaginatedResponse
from app.api.deps import get_current_active_user, require_staff
from app.models.user import User
from app.models import Sale, Client, Transaction
from app.models.sale import SaleStatus
from app.models.transaction import TransactionType

router = APIRouter(prefix="/sales", tags=["Sales"])


def _sale_response(sale: Sale) -> SaleResponse:
    """One place that turns a Sale row into its API shape."""
    return SaleResponse(
        id=sale.id,
        receipt_number=sale.receipt_number,
        client_id=sale.client_id,
        total_amount=sale.total_amount,
        paid_amount=sale.paid_amount,
        payment_method=sale.payment_method,
        status=sale.status,
        notes=sale.notes,
        created_at=sale.created_at.isoformat(),
        updated_at=sale.updated_at.isoformat() if sale.updated_at else None,
        items=[
            SaleItemResponse(
                id=item.id,
                product_variant_id=item.product_variant_id,
                quantity=item.quantity,
                unit_price=item.unit_price,
                total_price=item.total_price,
                product_variant_sku=item.product_variant.sku,
                product_name=item.product_variant.product.name,
                color_name=item.product_variant.color.name,
                size_name=item.product_variant.size.name,
                created_at=item.created_at.isoformat(),
            )
            for item in sale.items
        ],
        client_name=f"{sale.client.first_name} {sale.client.last_name}"
        if sale.client
        else None,
        seller_id=sale.seller_id,
        seller_name=sale.seller.name if sale.seller else None,
    )


@router.post("/", response_model=ResponseModel)
def create_sale(
    sale_data: SaleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Create a new sale."""
    sale_service = SaleService(db)
    try:
        sale = sale_service.create_sale(sale_data, current_user)

        return ResponseModel(
            success=True,
            data=_sale_response(sale),
            message="Sale created successfully",
        )
    except HTTPException as e:
        return ResponseModel(success=False, message=e.detail)




@router.get("/", response_model=ResponseModel, dependencies=[Depends(require_staff)])
def get_sales(
    client_id: Optional[int] = Query(None, description="Filter by client ID"),
    seller_id: Optional[int] = Query(None, description="Filter by seller (employee) ID"),
    payment_method: Optional[str] = Query(None, description="Filter by payment method"),
    status: Optional[str] = Query(None, description="Filter by sale status"),
    start_date: Optional[str] = Query(None, description="Filter by start date"),
    end_date: Optional[str] = Query(None, description="Filter by end date"),
    search: Optional[str] = Query(None, description="Search by receipt number or client name"),
    min_amount: Optional[Decimal] = Query(None, description="Minimum total amount"),
    max_amount: Optional[Decimal] = Query(None, description="Maximum total amount"),
    page: int = Query(1, ge=1, description="Page number"),
    size: int = Query(10, ge=1, le=100, description="Page size"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Get all sales with filtering and pagination."""
    filters = SaleFilter(
        client_id=client_id,
        seller_id=seller_id,
        payment_method=payment_method,
        status=status,
        start_date=start_date,
        end_date=end_date,
        search=search,
        min_amount=min_amount,
        max_amount=max_amount,
        page=page,
        size=size,
    )

    sale_service = SaleService(db)
    sales, pagination = sale_service.get_sales(filters)

    sale_responses = [_sale_response(sale) for sale in sales]

    return ResponseModel(
        success=True,
        data=PaginatedResponse(items=sale_responses, pagination=pagination),
        message="Sales retrieved successfully",
    )


@router.get("/stats/", response_model=ResponseModel, dependencies=[Depends(require_staff)])
def get_sales_stats(
    client_id: Optional[int] = Query(None),
    seller_id: Optional[int] = Query(None),
    payment_method: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    min_amount: Optional[Decimal] = Query(None),
    max_amount: Optional[Decimal] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Sales statistics over the same filters as the list."""
    filters = SaleFilter(
        client_id=client_id,
        seller_id=seller_id,
        payment_method=payment_method,
        status=status,
        start_date=start_date,
        end_date=end_date,
        search=search,
        min_amount=min_amount,
        max_amount=max_amount,
    )
    return ResponseModel(
        success=True,
        data=SaleService(db).get_stats(filters),
        message="Sales statistics retrieved successfully",
    )


@router.post("/debt-payment", response_model=ResponseModel)
def process_debt_payment(
    payment_data: DebtPaymentRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Process a debt payment for a client."""
    # FOR UPDATE on client + its unpaid sales: two concurrent payments must not
    # both pass the outstanding check.
    client = (
        db.query(Client).filter(Client.id == payment_data.client_id).with_for_update().first()
    )
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    if payment_data.payment_amount <= 0:
        raise HTTPException(
            status_code=400, detail="Payment amount must be greater than zero"
        )

    # Outstanding debt is derived from unpaid sales — that is what ClientService
    # reports and what the debts page shows. Client.debt_amount is a denormalized
    # cache that drifts, so it must not be the basis for the check.
    outstanding_sales = (
        db.query(Sale)
        .filter(
            Sale.client_id == client.id,
            Sale.status.in_([SaleStatus.DEBT, SaleStatus.PARTIALLY_PAID]),
        )
        .order_by(Sale.created_at.asc(), Sale.id.asc())
        .with_for_update()
        .all()
    )
    sales_outstanding = sum(
        (sale.total_amount - sale.paid_amount for sale in outstanding_sales),
        Decimal("0"),
    )
    # Debt entered by hand has no sale to settle against, so it is part of what
    # the client owes and must be payable too.
    manual_outstanding = Decimal(client.manual_debt_adjustment or 0)
    outstanding = sales_outstanding + manual_outstanding

    if payment_data.payment_amount > outstanding:
        raise HTTPException(
            status_code=400, detail="Payment amount exceeds debt amount"
        )

    # Spread the payment over the client's unpaid sales, oldest first. Without
    # this the sales keep their original paid_amount, so the debt the UI computes
    # never moves and the payment looks like it did nothing.
    remaining = payment_data.payment_amount
    for sale in outstanding_sales:
        if remaining <= 0:
            break
        due = sale.total_amount - sale.paid_amount
        if due <= 0:
            continue
        applied = min(due, remaining)
        sale.paid_amount += applied
        sale.status = (
            SaleStatus.COMPLETED
            if sale.paid_amount >= sale.total_amount
            else SaleStatus.PARTIALLY_PAID
        )
        remaining -= applied

    # Whatever the sales could not absorb comes off the manual adjustment.
    if remaining > 0:
        client.manual_debt_adjustment = manual_outstanding - remaining
    else:
        client.manual_debt_adjustment = manual_outstanding

    new_debt = outstanding - payment_data.payment_amount
    # Keep the cached column in step with the recomputed outstanding balance.
    client.debt_amount = new_debt

    transaction = Transaction(
        client_id=payment_data.client_id,
        user_id=current_user.id,
        amount=payment_data.payment_amount,
        transaction_type=TransactionType.DEBT_PAYMENT,
        description=f"Debt payment of {payment_data.payment_amount}",
    )
    db.add(transaction)

    # Sales, client balance and transaction must land in a single commit —
    # a partial write would leave the client's debt out of sync with the sales.
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    return ResponseModel(
        success=True,
        data={
            "client_id": payment_data.client_id,
            "payment_amount": float(payment_data.payment_amount),
            "new_debt_amount": float(new_debt),
        },
        message="Debt payment processed successfully",
    )


@router.get("/client/{client_id}/debt-history", response_model=ResponseModel)
def get_client_debt_history(
    client_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Get debt history for a specific client."""
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    # Get all transactions for this client
    transactions = (
        db.query(Transaction)
        .filter(Transaction.client_id == client_id)
        .order_by(Transaction.created_at.desc())
        .all()
    )

    debt_history = []
    balance = 0

    for transaction in transactions:
        if transaction.transaction_type == TransactionType.SALE:
            balance += transaction.amount
        elif transaction.transaction_type == TransactionType.DEBT_PAYMENT:
            balance -= transaction.amount

        debt_history.append(
            {
                "id": transaction.id,
                "type": transaction.transaction_type.value,
                "amount": float(transaction.amount),
                "balance": float(balance),
                "created_at": transaction.created_at.isoformat(),
            }
        )

    return ResponseModel(
        success=True,
        data=debt_history,
        message="Client debt history retrieved successfully",
    )


@router.patch("/{sale_id}/cancel", response_model=ResponseModel, dependencies=[Depends(require_staff)])
def cancel_sale(
    sale_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Cancel a sale."""
    sale_service = SaleService(db)
    try:
        sale = sale_service.cancel_sale(sale_id, current_user)
        if not sale:
            return ResponseModel(success=False, message="Sale not found")

        return ResponseModel(success=True, message="Sale cancelled successfully")
    except HTTPException as e:
        return ResponseModel(success=False, message=e.detail)


@router.post("/{sale_id}/pay-debt", response_model=ResponseModel)
def pay_sale_debt(
    sale_id: int,
    payment_amount: Decimal = Query(..., gt=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Pay debt for a specific sale."""
    sale_service = SaleService(db)
    try:
        sale = sale_service.pay_debt(sale_id, payment_amount, current_user)
        
        return ResponseModel(
            success=True,
            data=_sale_response(sale),
            message="Debt payment processed successfully",
        )
    except HTTPException as e:
        return ResponseModel(success=False, message=e.detail)


@router.get("/client/{client_id}/debts", response_model=ResponseModel)
def get_client_debts(
    client_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Get all debt sales for a specific client."""
    sale_service = SaleService(db)
    debts = sale_service.get_client_debts(client_id)
    
    debt_responses = [_sale_response(sale) for sale in debts]

    return ResponseModel(
        success=True,
        data=debt_responses,
        message="Client debts retrieved successfully",
    )


@router.get("/debt-stats", response_model=ResponseModel)
def get_debt_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Aggregate outstanding debt: total owed, number of debtors and the average.

    Deactivated clients are included — their debt is still owed, and the debts
    table lists them too, so the tiles must add up to what the table shows.
    """
    from sqlalchemy import func

    # Must mirror ClientService.get_clients: unpaid sales plus debt entered by
    # hand. Counting sales alone left these tiles disagreeing with the table
    # right below them for every client with a manual debt.
    sales_debt = (
        db.query(
            Sale.client_id.label("client_id"),
            func.coalesce(func.sum(Sale.total_amount - Sale.paid_amount), 0).label("debt"),
        )
        .filter(Sale.status.in_(["debt", "partially_paid"]))
        .group_by(Sale.client_id)
        .subquery()
    )

    owed = func.coalesce(sales_debt.c.debt, 0) + func.coalesce(
        Client.manual_debt_adjustment, 0
    )

    total_debt, total_clients = (
        db.query(
            func.coalesce(func.sum(owed), 0),
            func.count(Client.id),
        )
        .select_from(Client)
        .outerjoin(sales_debt, sales_debt.c.client_id == Client.id)
        .filter(owed > 0)
        .one()
    )

    total_debt = float(total_debt or 0)
    total_clients = int(total_clients or 0)
    average_debt = total_debt / total_clients if total_clients else 0.0

    return ResponseModel(
        success=True,
        data={
            "total_debt": total_debt,
            "clients_with_debt": total_clients,
            "avg_debt": average_debt,
        },
        message="Debt statistics retrieved successfully",
    )


@router.get("/debt-trend", response_model=ResponseModel)
def get_debt_trend(
    days: int = Query(30, ge=1, le=365, description="Number of days to get trend data"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Get debt trend data over time.

    Point k is start + k days; it counts every still-unpaid sale created at or
    before that instant. One grouped query per series instead of two per day.
    """
    from datetime import datetime, timedelta
    from sqlalchemy import func

    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    unpaid = (
        Sale.status.in_([SaleStatus.DEBT, SaleStatus.PARTIALLY_PAID]),
        Sale.created_at <= end_date,
    )

    # First point a sale belongs to: ceil((created_at - start) / 1 day), min 0.
    def first_point(col):
        return func.greatest(func.ceil(func.extract("epoch", col - start_date) / 86400), 0)

    k = first_point(Sale.created_at).label("k")
    debt_by_k = {
        int(b): amount
        for b, amount in db.query(k, func.sum(Sale.total_amount - Sale.paid_amount))
        .filter(*unpaid).group_by(k).all()
    }
    # A client counts from their first unpaid sale onwards.
    first_sale = (
        db.query(func.min(Sale.created_at).label("first_at"))
        .filter(*unpaid, Sale.client_id.isnot(None))
        .group_by(Sale.client_id)
        .subquery()
    )
    ck = first_point(first_sale.c.first_at).label("k")
    clients_by_k = {int(b): n for b, n in db.query(ck, func.count()).group_by(ck).all()}

    trend_data = []
    total_debt, client_count = Decimal(0), 0
    for i in range(days + 1):
        total_debt += debt_by_k.get(i, 0) or 0
        client_count += clients_by_k.get(i, 0)
        trend_data.append({
            "date": (start_date + timedelta(days=i)).strftime("%Y-%m-%d"),
            "total_debt": float(total_debt),
            "client_count": client_count,
        })

    return ResponseModel(
        success=True,
        data=trend_data,
        message="Debt trend data retrieved successfully",
    )


@router.get("/payment-trend", response_model=ResponseModel)
def get_payment_trend(
    days: int = Query(30, ge=1, le=365, description="Number of days to get payment trend data"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Get payment trend data over time.

    Bucket k covers [start + k days, start + k+1 days); one grouped query.
    """
    from datetime import datetime, timedelta
    from sqlalchemy import func

    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)

    k = func.floor(
        func.extract("epoch", Transaction.created_at - start_date) / 86400
    ).label("k")
    rows = (
        db.query(k, func.sum(Transaction.amount), func.count(Transaction.id))
        .filter(
            Transaction.transaction_type == TransactionType.DEBT_PAYMENT,
            Transaction.created_at >= start_date,
            Transaction.created_at < start_date + timedelta(days=days + 1),
        )
        .group_by(k)
        .all()
    )
    by_k = {int(b): (total, n) for b, total, n in rows}

    trend_data = []
    for i in range(days + 1):
        total, n = by_k.get(i, (0, 0))
        trend_data.append({
            "date": (start_date + timedelta(days=i)).strftime("%Y-%m-%d"),
            "total_payments": float(total or 0),
            "payment_count": n,
        })

    return ResponseModel(
        success=True,
        data=trend_data,
        message="Payment trend data retrieved successfully",
    )


@router.get("/{sale_id}", response_model=ResponseModel, dependencies=[Depends(require_staff)])
def get_sale(
    sale_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Get a specific sale by ID."""
    sale_service = SaleService(db)
    sale = sale_service.get_sale(sale_id)

    if not sale:
        return ResponseModel(success=False, message="Sale not found")

    return ResponseModel(
        success=True,
        data=_sale_response(sale),
        message="Sale retrieved successfully",
    )
