from sqlalchemy import Column, Integer, String, DateTime, Numeric, Boolean, Text
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.sql import func
from app.database import Base


class Employee(Base):
    __tablename__ = "employees"

    id = Column(Integer, primary_key=True)

    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    phone = Column(String)
    email = Column(String)

    position = Column(String, nullable=False)
    salary = Column(Numeric(14, 2), nullable=False)
    address = Column(Text, nullable=True)

    hire_date = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    is_active = Column(Boolean, nullable=False, server_default="true")

    # Seller KPI: only sellers are offered at checkout; commission is a % of
    # their revenue, monthly_target the revenue plan for every month.
    is_seller = Column(Boolean, nullable=False, server_default="true")
    commission_rate = Column(Numeric(5, 2), nullable=False, server_default="0")
    monthly_target = Column(Numeric(14, 2), nullable=False, server_default="0")

    notes = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    @hybrid_property
    def name(self) -> str:
        """Full name — what the API exposes and the UI lists employees by."""
        return f"{self.first_name} {self.last_name}".strip()

    @name.expression
    def name(cls):
        """SQL form of `name`, so it stays usable in filters and order_by."""
        return func.concat(cls.first_name, " ", cls.last_name)
