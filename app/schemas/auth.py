from pydantic import BaseModel, ConfigDict, EmailStr, Field
from typing import Optional
from app.models.user import UserRole


class UserLogin(BaseModel):
    email: str
    password: str


class UserRegister(BaseModel):
    """New-user payload. `role` is intentionally not accepted from the request:
    it was previously honoured verbatim, so any caller could create themselves an
    admin. New users are always created as MANAGER."""

    model_config = ConfigDict(extra="forbid")

    email: str
    username: str
    password: str = Field(min_length=8)
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None


class UserResponse(BaseModel):
    id: int
    email: str
    username: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    role: UserRole
    is_active: bool
    created_at: str
    updated_at: Optional[str] = None
