from sqlalchemy.orm import Session, joinedload, selectinload
from sqlalchemy import and_, or_, select, func
from typing import List, Optional, Tuple
from decimal import Decimal
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.models.sale import SaleItem
from app.models.category import Category
from app.models.brand import Brand
from app.models.season import Season
from app.schemas.product import (
    ProductCreate,
    ProductUpdate,
    ProductFilter,
)
from app.utils.helpers import generate_sku, paginate_query, calculate_pagination_info
from fastapi import HTTPException, status



PRODUCT_DETAILS = (
    selectinload(Product.variants).options(
        joinedload(ProductVariant.color), joinedload(ProductVariant.size)
    ),
    joinedload(Product.brand),
    joinedload(Product.season),
    joinedload(Product.category),
)

class ProductService:
    def __init__(self, db: Session):
        self.db = db

    def create_product(self, product_data: ProductCreate) -> Product:
        """Create a new product."""
        # Generate SKU if not provided
        if not product_data.sku:
            product_data.sku = generate_sku()

        # Check if SKU already exists
        existing_product = (
            self.db.query(Product).filter(Product.sku == product_data.sku).first()
        )
        if existing_product:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Product with this SKU already exists",
            )

        # Validate category if provided
        if product_data.category_id:
            category = (
                self.db.query(Category)
                .filter(Category.id == product_data.category_id)
                .first()
            )
            if not category:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail="Category not found"
                )

        # Validate brand if provided
        if product_data.brand_id:
            brand = (
                self.db.query(Brand).filter(Brand.id == product_data.brand_id).first()
            )
            if not brand:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail="Brand not found"
                )

        # Validate season if provided
        if product_data.season_id:
            season = (
                self.db.query(Season)
                .filter(Season.id == product_data.season_id)
                .first()
            )
            if not season:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail="Season not found"
                )

        db_product = Product(**product_data.dict())
        self.db.add(db_product)
        self.db.commit()
        self.db.refresh(db_product)
        return db_product

    def get_product(self, product_id: int) -> Optional[Product]:
        """Get a product by ID."""
        return (
            self.db.query(Product)
            .options(*PRODUCT_DETAILS)
            .filter(Product.id == product_id)
            .first()
        )

    def get_products(self, filters: ProductFilter) -> Tuple[List[Product], dict]:
        query = self.db.query(Product)

        if filters.name:
            query = query.filter(Product.name.ilike(f"%{filters.name}%"))

        if filters.brand_id:
            query = query.filter(Product.brand_id == filters.brand_id)

        if filters.season_id:
            query = query.filter(Product.season_id == filters.season_id)

        if filters.category_id:
            query = query.filter(Product.category_id == filters.category_id)

        if filters.search:
            search_term = f"%{filters.search}%"
            # EXISTS instead of join + DISTINCT: no row multiplication, and each
            # branch can use its trigram index.
            query = query.filter(
                or_(
                    Product.name.ilike(search_term),
                    Product.description.ilike(search_term),
                    Product.sku.ilike(search_term),
                    Product.variants.any(ProductVariant.sku.ilike(search_term)),
                    Product.brand.has(Brand.name.ilike(search_term)),
                )
            )

        total = query.count()
        # Stable order: the frontend fetches pages in parallel and would
        # otherwise see duplicates/gaps. Everything the response reads is
        # loaded up front instead of lazily per row.
        paginated_query = paginate_query(
            query.order_by(Product.id).options(*PRODUCT_DETAILS), filters.page, filters.size
        )
        products = paginated_query.all()
        
        pagination_info = calculate_pagination_info(total, filters.page, filters.size)
        return products, pagination_info

    def update_product(
        self, product_id: int, product_data: ProductUpdate
    ) -> Optional[Product]:
        """Update a product."""
        product = self.get_product(product_id)
        if not product:
            return None

        # Validate category if provided
        if product_data.category_id:
            category = (
                self.db.query(Category)
                .filter(Category.id == product_data.category_id)
                .first()
            )
            if not category:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail="Category not found"
                )

        # Validate brand if provided
        if product_data.brand_id:
            brand = (
                self.db.query(Brand).filter(Brand.id == product_data.brand_id).first()
            )
            if not brand:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail="Brand not found"
                )

        # Validate season if provided
        if product_data.season_id:
            season = (
                self.db.query(Season)
                .filter(Season.id == product_data.season_id)
                .first()
            )
            if not season:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail="Season not found"
                )

        # Update fields
        update_data = product_data.dict(exclude_unset=True)
        for field, value in update_data.items():
            setattr(product, field, value)

        self.db.commit()
        self.db.refresh(product)
        return product

    def delete_product(self, product_id: int) -> bool:
        """Delete a product."""
        product = self.get_product(product_id)
        if not product:
            return False

        # Block delete if any variant was sold — sale_items FK would violate
        # and deleting would destroy sales history.
        sold = (
            self.db.query(SaleItem.id)
            .join(ProductVariant, SaleItem.product_variant_id == ProductVariant.id)
            .filter(ProductVariant.product_id == product_id)
            .first()
        )
        if sold:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot delete product with existing sales",
            )

        self.db.delete(product)
        self.db.commit()
        return True

    def get_product_by_variant_sku(self, sku: str):
        """Get full product with all variants by variant SKU."""
        # Matched case-insensitively and with surrounding whitespace stripped on
        # both sides: scanners and imports routinely introduce padding, and a
        # strict `==` then misses a code that is plainly visible in inventory.
        normalized_sku = (sku or "").strip()
        if not normalized_sku:
            return None

        product_variant = (
            self.db.query(ProductVariant)
            .filter(func.lower(func.trim(ProductVariant.sku)) == normalized_sku.lower())
            .first()
        )
        if not product_variant:
            return None
            
        # Then get the full product with all variants
        product = (
            self.db.query(Product)
            .options(
                joinedload(Product.variants).joinedload(ProductVariant.color),
                joinedload(Product.variants).joinedload(ProductVariant.size),
                joinedload(Product.brand),
                joinedload(Product.season),
                joinedload(Product.category)
            )
            .filter(Product.id == product_variant.product_id)
            .first()
        )
        
        return product