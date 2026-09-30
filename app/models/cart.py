import enum

from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class CartStatus(str, enum.Enum):
    PENDING = "pending"  # stock reserved, waiting at the till
    COMPLETED = "completed"  # paid: turned into sale_id
    CANCELLED = "cancelled"  # stock returned (by seller, cashier or expiry)


class Cart(Base):
    """Basket a seller builds on the phone and hands to the cashier.

    Submitting it takes the items out of stock; completing it turns that
    reservation into a sale, cancelling puts the stock back.
    """

    __tablename__ = "carts"

    id = Column(Integer, primary_key=True)
    seller_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=True)
    notes = Column(Text, nullable=True)
    status = Column(String(16), nullable=False, default=CartStatus.PENDING.value, index=True)
    sale_id = Column(Integer, ForeignKey("sales.id"), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    seller = relationship("Employee")
    client = relationship("Client")
    items = relationship("CartItem", backref="cart", cascade="all, delete-orphan")


class CartItem(Base):
    __tablename__ = "cart_items"

    id = Column(Integer, primary_key=True)
    cart_id = Column(Integer, ForeignKey("carts.id"), nullable=False, index=True)
    product_variant_id = Column(Integer, ForeignKey("product_variants.id"), nullable=False)
    quantity = Column(Integer, nullable=False)

    product_variant = relationship("ProductVariant")
