from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models.cart import Cart, CartItem, CartStatus
from app.models.client import Client
from app.models.employee import Employee
from app.models.product_variant import ProductVariant
from app.schemas.cart import CartCreate

# ponytail: expiry is lazy — stale carts are released whenever a cart list is
# opened, not on a timer. Add a scheduled job if stock must free up on the dot.
CART_TTL = timedelta(hours=3)

CART_DETAILS = (
    selectinload(Cart.items)
    .joinedload(CartItem.product_variant)
    .options(
        joinedload(ProductVariant.product),
        joinedload(ProductVariant.color),
        joinedload(ProductVariant.size),
    ),
    joinedload(Cart.client),
    joinedload(Cart.seller),
)


def lock_variants(db: Session, ids: Iterable[int]) -> dict:
    """Variants under FOR UPDATE, in id order so concurrent lockers can't deadlock.
    populate_existing: the stock read must be the locked row, not a cached one."""
    return {
        v.id: v
        for v in db.query(ProductVariant)
        .filter(ProductVariant.id.in_(set(ids)))
        .order_by(ProductVariant.id)
        .with_for_update()
        .populate_existing()
        .all()
    }


def lock_pending_cart(db: Session, cart_id: int) -> Cart:
    cart = db.query(Cart).filter(Cart.id == cart_id).with_for_update().first()
    if not cart:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cart not found")
    if cart.status != CartStatus.PENDING.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Cart is no longer pending"
        )
    return cart


def release_stock(cart: Cart, variants: dict) -> None:
    """Hand a pending cart's reserved units back to stock (variants must be locked)."""
    for item in cart.items:
        variants[item.product_variant_id].stock_quantity += item.quantity


class CartService:
    def __init__(self, db: Session):
        self.db = db

    def get(self, cart_id: int) -> Optional[Cart]:
        return self.db.query(Cart).options(*CART_DETAILS).filter(Cart.id == cart_id).first()

    def create(self, seller: Employee, data: CartCreate) -> Cart:
        if data.client_id and not self.db.get(Client, data.client_id):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Client not found")

        wanted = Counter()
        for item in data.items:
            wanted[item.product_variant_id] += item.quantity

        variants = lock_variants(self.db, wanted)
        for vid, qty in wanted.items():
            v = variants.get(vid)
            if not v or not v.is_active:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Product variant with ID {vid} not found",
                )
            if v.stock_quantity < qty:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Insufficient stock for product variant {v.sku}",
                )
            v.stock_quantity -= qty

        cart = Cart(
            seller_id=seller.id,
            client_id=data.client_id,
            notes=data.notes,
            status=CartStatus.PENDING.value,
            items=[CartItem(product_variant_id=vid, quantity=qty) for vid, qty in wanted.items()],
        )
        self.db.add(cart)
        self.db.commit()
        return self.get(cart.id)

    def cancel(self, cart_id: int, seller_id: Optional[int] = None) -> Cart:
        """Cancel a pending cart and return its stock. seller_id limits it to own carts."""
        cart = lock_pending_cart(self.db, cart_id)
        if seller_id is not None and cart.seller_id != seller_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cart not found")
        release_stock(cart, lock_variants(self.db, (i.product_variant_id for i in cart.items)))
        cart.status = CartStatus.CANCELLED.value
        self.db.commit()
        return self.get(cart.id)

    def expire_stale(self) -> None:
        cutoff = datetime.now(timezone.utc) - CART_TTL
        stale = [
            cid
            for (cid,) in self.db.query(Cart.id).filter(
                Cart.status == CartStatus.PENDING.value, Cart.created_at < cutoff
            )
        ]
        for cid in stale:
            try:
                self.cancel(cid)
            except HTTPException:  # completed or cancelled meanwhile
                self.db.rollback()

    def pending(self):
        self.expire_stale()
        return (
            self.db.query(Cart)
            .options(*CART_DETAILS)
            .filter(Cart.status == CartStatus.PENDING.value)
            .order_by(Cart.created_at)
            .all()
        )

    def for_seller(self, seller_id: int, limit: int = 30):
        self.expire_stale()
        return (
            self.db.query(Cart)
            .options(*CART_DETAILS)
            .filter(Cart.seller_id == seller_id)
            .order_by(Cart.created_at.desc())
            .limit(limit)
            .all()
        )


def cart_response(cart: Cart) -> dict:
    items = []
    for i in cart.items:
        v = i.product_variant
        items.append({
            "product_variant_id": v.id,
            "quantity": i.quantity,
            "price": float(v.price),
            "sku": v.sku,
            "stock_quantity": v.stock_quantity,
            "product_id": v.product_id,
            "product_name": v.product.name,
            "image_url": v.product.image_url,
            "color_name": v.color.name if v.color else None,
            "color_hex": v.color.hex_code if v.color else None,
            "size_name": v.size.name if v.size else None,
        })
    return {
        "id": cart.id,
        "status": cart.status,
        "seller_id": cart.seller_id,
        "seller_name": cart.seller.name if cart.seller else None,
        "client_id": cart.client_id,
        "client_name": f"{cart.client.first_name} {cart.client.last_name}" if cart.client else None,
        "client_phone": cart.client.phone if cart.client else None,
        "notes": cart.notes,
        "sale_id": cart.sale_id,
        "total": sum(i["price"] * i["quantity"] for i in items),
        "items": items,
        "created_at": cart.created_at.isoformat() if cart.created_at else None,
        "expires_at": (cart.created_at + CART_TTL).isoformat() if cart.created_at else None,
    }
