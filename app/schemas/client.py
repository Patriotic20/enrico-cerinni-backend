from pydantic import BaseModel, Field, EmailStr, model_validator
from typing import Optional, List
from decimal import Decimal
from app.schemas.common import PaginationModel
from datetime import datetime


class ClientBase(BaseModel):
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    phone: Optional[str] = Field(None, max_length=20)
    address: Optional[str] = None
    notes: Optional[str] = None


# telegram_chat_id is read-only: only the signed /start link (telegram_link.sync)
# may set it, otherwise staff could redirect a client's messages to any chat.
class ClientCreate(ClientBase):
    pass


class ClientUpdate(BaseModel):
    first_name: Optional[str] = Field(None, min_length=1, max_length=100)
    last_name: Optional[str] = Field(None, min_length=1, max_length=100)
    email: Optional[str] = None
    phone: Optional[str] = Field(None, max_length=20)
    address: Optional[str] = None
    notes: Optional[str] = None


class ClientResponse(ClientBase):
    id: int
    telegram_chat_id: Optional[str] = None
    debt_amount: Decimal
    is_active: bool
    created_at: str
    updated_at: Optional[str] = None
    # Date of the client's most recent sale. The clients table has a column for
    # it and the "active clients" card counts on it, so it has to be returned.
    last_purchase_date: Optional[str] = None


class ClientDebtUpdate(BaseModel):
    # Exactly one: debt_amount sets the total, add_amount adds to the current
    # total server-side (no lost update when two people add debt at once).
    debt_amount: Optional[Decimal] = Field(None, ge=0)
    add_amount: Optional[Decimal] = Field(None, gt=0)

    @model_validator(mode="after")
    def one_of(self):
        if (self.debt_amount is None) == (self.add_amount is None):
            raise ValueError("Provide exactly one of debt_amount or add_amount")
        return self


class ClientFilter(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    has_debt: Optional[bool] = None
    # Single free-text box matching name or phone, as used by the debts page.
    search: Optional[str] = None
    sort_by: Optional[str] = None
    # Exposed by the debts page's filter panel.
    min_debt: Optional[Decimal] = None
    max_debt: Optional[Decimal] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    page: int = Field(1, ge=1)
    size: int = Field(10, ge=1, le=100)


class PaginatedClientResponse(BaseModel):
    items: List[ClientResponse]
    pagination: PaginationModel
