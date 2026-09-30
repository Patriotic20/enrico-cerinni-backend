from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user, require_staff
from app.database import get_db
from app.models.employee import Employee
from app.schemas.common import ResponseModel
from app.schemas.employee import SellerOption
from app.services.employee_kpi_service import EmployeeKpiService, month_bounds

# Employee CRUD lives at /finance/employees; this router is the seller side:
# who can be picked at checkout, and how each seller performs.
router = APIRouter(prefix="/employees", tags=["Employees"])


def _period(start_date: Optional[date], end_date: Optional[date]) -> tuple[date, date]:
    first, last = month_bounds()
    start, end = start_date or first, end_date or last
    if end < start:
        raise HTTPException(status_code=400, detail="end_date must not be before start_date")
    return start, end


@router.get("/sellers", response_model=ResponseModel, dependencies=[Depends(get_current_active_user)])
def get_sellers(db: Session = Depends(get_db)):
    """Sellers offered at checkout — open to cashiers, so no salary data."""
    sellers = (
        db.query(Employee)
        .filter(Employee.is_active.is_(True), Employee.is_seller.is_(True))
        .order_by(Employee.first_name, Employee.last_name)
        .all()
    )
    return ResponseModel(
        success=True,
        data=[SellerOption.model_validate(s) for s in sellers],
        message="Sellers retrieved successfully",
    )


@router.get("/kpi", response_model=ResponseModel, dependencies=[Depends(require_staff)])
def get_kpi(
    start_date: Optional[date] = Query(None, description="Defaults to the first day of this month"),
    end_date: Optional[date] = Query(None, description="Inclusive; defaults to the last day of this month"),
    db: Session = Depends(get_db),
):
    start, end = _period(start_date, end_date)
    return ResponseModel(
        success=True,
        data=EmployeeKpiService(db).leaderboard(start, end),
        message="Employee KPI retrieved successfully",
    )


@router.get("/{employee_id}/kpi", response_model=ResponseModel, dependencies=[Depends(require_staff)])
def get_employee_kpi(
    employee_id: int,
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    employee = db.query(Employee).filter(Employee.id == employee_id).first()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    start, end = _period(start_date, end_date)
    return ResponseModel(
        success=True,
        data=EmployeeKpiService(db).employee(employee, start, end),
        message="Employee KPI retrieved successfully",
    )
