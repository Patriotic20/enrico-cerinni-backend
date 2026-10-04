from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    Text,
    ForeignKey,
)
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.database import Base


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True)
    sku = Column(String(50), unique=True, index=True, nullable=False)
    name = Column(String(200), index=True, nullable=False)
    description = Column(Text, nullable=True)
    
    brand_id = Column(Integer, ForeignKey("brands.id"), nullable=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    season_id = Column(Integer, ForeignKey("seasons.id"), nullable=True)
    
    image_url = Column(String(500), nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    brand = relationship("Brand", backref="products")
    season = relationship("Season", back_populates="products")
    category = relationship("Category", backref="products")
    variants = relationship(
        "ProductVariant", back_populates="product", cascade="all, delete-orphan"
    )

    @property
    def display_name(self):
        """Name with the brand in brackets, as shown on sales, carts and reports."""
        return display_name(self.name, self.brand.name if self.brand else None)


def display_name(name, brand_name):
    return f"{name} ({brand_name})" if brand_name else name
