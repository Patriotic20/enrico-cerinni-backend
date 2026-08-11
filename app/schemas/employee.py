from pydantic import BaseModel, ConfigDict, EmailStr
from datetime import datetime
from typing import Optional
from decimal import Decimal


class EmployeeBase(BaseModel):
    first_name: str
    last_name: str
    position: str
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    salary: Decimal
    hire_date: datetime


class EmployeeCreate(EmployeeBase):
    address: Optional[str] = None
    notes: Optional[str] = None
    is_active: bool = True


class EmployeeUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    position: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    salary: Optional[Decimal] = None
    hire_date: Optional[datetime] = None
    address: Optional[str] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None


class EmployeeResponse(EmployeeBase):
    id: int
    # Read off the model's `name` hybrid — the UI lists employees by full name.
    name: str
    address: Optional[str] = None
    notes: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
