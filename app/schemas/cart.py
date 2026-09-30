from typing import List, Optional
from pydantic import BaseModel, Field


class CartItemCreate(BaseModel):
    product_variant_id: int
    quantity: int = Field(gt=0, le=1000)


class CartCreate(BaseModel):
    client_id: Optional[int] = None
    notes: Optional[str] = Field(None, max_length=500)
    items: List[CartItemCreate] = Field(min_length=1, max_length=100)
