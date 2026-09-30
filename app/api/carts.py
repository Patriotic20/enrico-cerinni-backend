from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user, get_current_seller
from app.api.employees import _period
from app.database import get_db
from app.models.employee import Employee
from app.schemas.cart import CartCreate
from app.schemas.common import ResponseModel
from app.services.cart_service import CartService, cart_response
from app.services.employee_kpi_service import EmployeeKpiService

# Seller mobile app: build carts on the floor, follow own KPI.
seller_router = APIRouter(prefix="/seller", tags=["Seller app"])
# Till side: carts waiting to be paid. Paying one is POST /sales/ with cart_id.
router = APIRouter(
    prefix="/carts", tags=["Carts"], dependencies=[Depends(get_current_active_user)]
)


@seller_router.get("/me", response_model=ResponseModel)
def seller_me(
    start_date: Optional[date] = Query(None, description="Defaults to the first day of this month"),
    end_date: Optional[date] = Query(None, description="Inclusive; defaults to the last day of this month"),
    seller: Employee = Depends(get_current_seller),
    db: Session = Depends(get_db),
):
    """The seller's own KPI (plan, revenue, commission, rank) for the period."""
    start, end = _period(start_date, end_date)
    return ResponseModel(
        success=True,
        data=EmployeeKpiService(db).employee(seller, start, end),
        message="KPI retrieved successfully",
    )


@seller_router.get("/carts", response_model=ResponseModel)
def seller_carts(seller: Employee = Depends(get_current_seller), db: Session = Depends(get_db)):
    carts = CartService(db).for_seller(seller.id)
    return ResponseModel(success=True, data=[cart_response(c) for c in carts], message="Carts retrieved")


@seller_router.post("/carts", response_model=ResponseModel)
def seller_create_cart(
    data: CartCreate, seller: Employee = Depends(get_current_seller), db: Session = Depends(get_db)
):
    cart = CartService(db).create(seller, data)
    return ResponseModel(success=True, data=cart_response(cart), message="Cart sent to the cashier")


@seller_router.delete("/carts/{cart_id}", response_model=ResponseModel)
def seller_cancel_cart(
    cart_id: int, seller: Employee = Depends(get_current_seller), db: Session = Depends(get_db)
):
    cart = CartService(db).cancel(cart_id, seller_id=seller.id)
    return ResponseModel(success=True, data=cart_response(cart), message="Cart cancelled")


@router.get("/", response_model=ResponseModel)
def pending_carts(db: Session = Depends(get_db)):
    carts = CartService(db).pending()
    return ResponseModel(success=True, data=[cart_response(c) for c in carts], message="Carts retrieved")


@router.delete("/{cart_id}", response_model=ResponseModel)
def cancel_cart(cart_id: int, db: Session = Depends(get_db)):
    cart = CartService(db).cancel(cart_id)
    return ResponseModel(success=True, data=cart_response(cart), message="Cart cancelled")
