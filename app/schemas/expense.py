from pydantic import BaseModel, ConfigDict, field_validator
from datetime import datetime
from typing import Optional
from decimal import Decimal

from app.models.expense import EXPENSE_CATEGORIES


def _validate_category(value: Optional[str]) -> Optional[str]:
    if value is not None and value not in EXPENSE_CATEGORIES:
        raise ValueError(
            f"category must be one of: {', '.join(EXPENSE_CATEGORIES)}"
        )
    return value


class ExpenseBase(BaseModel):
    description: str
    amount: Decimal
    category: str
    date: datetime
    notes: Optional[str] = None


class ExpenseCreate(ExpenseBase):
    _check_category = field_validator("category")(_validate_category)


class ExpenseUpdate(BaseModel):
    description: Optional[str] = None
    amount: Optional[Decimal] = None
    category: Optional[str] = None
    date: Optional[datetime] = None
    notes: Optional[str] = None

    _check_category = field_validator("category")(_validate_category)


class ExpenseResponse(ExpenseBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
