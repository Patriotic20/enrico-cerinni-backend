from pydantic import BaseModel, ConfigDict, EmailStr, Field
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
    is_seller: bool = True
    commission_rate: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    monthly_target: Decimal = Field(default=Decimal("0"), ge=0)


class EmployeeCreate(EmployeeBase):
    address: Optional[str] = None
    notes: Optional[str] = None
    is_active: bool = True
    # Write-only: sets the seller's mobile app PIN (login is phone + PIN).
    pin: Optional[str] = Field(default=None, pattern=r"^\d{4,6}$")


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
    is_seller: Optional[bool] = None
    commission_rate: Optional[Decimal] = Field(default=None, ge=0, le=100)
    monthly_target: Optional[Decimal] = Field(default=None, ge=0)
    # Write-only: sets the seller's mobile app PIN (login is phone + PIN).
    pin: Optional[str] = Field(default=None, pattern=r"^\d{4,6}$")


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


class SellerOption(BaseModel):
    """What the checkout seller picker needs — nothing salary-related."""
    id: int
    name: str
    position: str

    model_config = ConfigDict(from_attributes=True)
