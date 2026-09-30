from sqlalchemy import Column, Integer, String, Boolean, DateTime, Enum, ForeignKey
from sqlalchemy.sql import func
from app.database import Base
import enum


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    MANAGER = "manager"
    USER = "user"
    # Mobile app only: signs in with phone + PIN, builds carts for the cashier.
    SELLER = "seller"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    email = Column(String, nullable=True, index=True)
    
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(Enum(UserRole), default=UserRole.MANAGER, nullable=False)
    
    # Set only for SELLER accounts: the employee whose KPI their carts count towards.
    employee_id = Column(Integer, ForeignKey("employees.id"), unique=True, nullable=True)

    is_active = Column(Boolean, default=True, nullable=False)
    # Bumped on logout / password change; refresh tokens carrying an older value are rejected.
    token_version = Column(Integer, default=0, server_default="0", nullable=False)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
