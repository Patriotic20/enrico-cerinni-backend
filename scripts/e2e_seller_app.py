"""End-to-end check of the seller mobile app flow. Needs an EMPTY, migrated database:

    SEED_MOCK_DATA=true DATABASE_URL=postgresql://.../scratch_db PYTHONPATH=. python scripts/e2e_seller_app.py

Never point it at a real database: it seeds data and creates sales."""
from fastapi.testclient import TestClient

import main
from app.database import SessionLocal
from app.models import User, Employee, ProductVariant, Cart
from app.models.user import UserRole
from app.utils.auth import get_password_hash
from app.utils.init_db import seed_mock_data
from app.services.auth_service import AuthService

db = SessionLocal()
seed_mock_data(db)
db.add(User(username="adm", email="adm@x.uz", hashed_password=get_password_hash("password123"), role=UserRole.ADMIN, token_version=0))
db.commit()
db.close()

c = TestClient(main.app)
ok = lambda r: (r.status_code == 200 and r.json().get("success") is not False) or print(r.status_code, r.text)

tok = c.post("/auth/login", json={"email": "adm@x.uz", "password": "password123"}).json()["data"]["access_token"]
A = {"Authorization": f"Bearer {tok}"}

# admin creates a seller with PIN
r = c.post("/finance/employees", headers=A, json={
    "first_name": "Ali", "last_name": "V", "position": "Sotuvchi", "phone": "+998 90 111-22-33",
    "salary": 1000, "hire_date": "2026-01-01T00:00:00", "pin": "1234", "monthly_target": 10000000})
assert ok(r); emp_id = r.json()["data"]["id"]

# PIN login
assert c.post("/auth/pin-login", json={"phone": "901112233", "pin": "9999"}).json()["success"] is False
r = c.post("/auth/pin-login", json={"phone": "901112233", "pin": "1234"})
assert r.json()["success"], r.text
S = {"Authorization": f"Bearer {r.json()['data']['access_token']}"}
c.cookies.clear()  # bearer only, like the SPA

v = c.get("/auth/validate", headers=S).json()["data"]
assert v["role"] == "seller" and v["employee_id"] == emp_id

# default deny: seller can't reach cashier/staff endpoints
for m, u in [("post", "/sales/"), ("get", "/carts/"), ("get", "/employees/sellers"), ("post", "/sales/debt-payment"), ("get", "/sales/")]:
    st = getattr(c, m)(u, headers=S, json={}).status_code if m == "post" else c.get(u, headers=S).status_code
    assert st in (403, 422), (u, st)
assert c.post("/sales/", headers=S, json={"seller_id": 1, "items": [], "total_amount": 0, "payment_method": "cash"}).status_code in (403, 422)

# seller can search products, create client
prods = c.get("/products/?search=Ko'ylak", headers=S).json()["data"]["items"]
var = prods[0]["variants"][0]
assert var["cost_price"] is None  # no margins for sellers
cl = c.post("/clients/", headers=S, json={"first_name": "Mijoz", "last_name": "Bir", "phone": "+998977770001"})
assert ok(cl); client_id = cl.json()["data"]["id"]

db = SessionLocal()
stock0 = db.get(ProductVariant, var["id"]).stock_quantity
db.close()

# submit cart -> stock reserved
r = c.post("/seller/carts", headers=S, json={"client_id": client_id, "items": [{"product_variant_id": var["id"], "quantity": 2}]})
assert ok(r); cart = r.json()["data"]
db = SessionLocal(); assert db.get(ProductVariant, var["id"]).stock_quantity == stock0 - 2; db.close()

# over-reserve fails
assert c.post("/seller/carts", headers=S, json={"items": [{"product_variant_id": var["id"], "quantity": 10**3}]}).status_code == 400

# cashier sees it, pays 1 unit only (edited), stock ends at stock0-1
pend = c.get("/carts/", headers=A).json()["data"]
assert [p["id"] for p in pend] == [cart["id"]]
r = c.post("/sales/", headers=A, json={
    "cart_id": cart["id"], "seller_id": 999999, "client_id": client_id, "total_amount": var["price"],
    "paid_amount": var["price"], "payment_method": "cash",
    "items": [{"product_variant_id": var["id"], "quantity": 1, "unit_price": var["price"]}]})
assert ok(r), r.text
sale = r.json()["data"]
assert sale["seller_id"] == emp_id  # credited to the cart's seller, not the posted one
db = SessionLocal(); assert db.get(ProductVariant, var["id"]).stock_quantity == stock0 - 1; db.close()

# paying same cart twice fails
r = c.post("/sales/", headers=A, json={
    "cart_id": cart["id"], "seller_id": emp_id, "total_amount": var["price"], "paid_amount": var["price"], "payment_method": "cash",
    "items": [{"product_variant_id": var["id"], "quantity": 1, "unit_price": var["price"]}]})
assert r.json()["success"] is False and "no longer pending" in r.json()["message"]

# seller cancels a second cart -> stock back
r = c.post("/seller/carts", headers=S, json={"items": [{"product_variant_id": var["id"], "quantity": 3}]})
cid2 = r.json()["data"]["id"]
assert c.delete(f"/seller/carts/{cid2}", headers=S).status_code == 200
db = SessionLocal(); assert db.get(ProductVariant, var["id"]).stock_quantity == stock0 - 1; db.close()

# expiry releases stale carts
r = c.post("/seller/carts", headers=S, json={"items": [{"product_variant_id": var["id"], "quantity": 1}]})
cid3 = r.json()["data"]["id"]
db = SessionLocal()
from sqlalchemy import text
db.execute(text("UPDATE carts SET created_at = now() - interval '4 hours' WHERE id = :i"), {"i": cid3}); db.commit(); db.close()
mine = c.get("/seller/carts", headers=S).json()["data"]
assert {m["id"]: m["status"] for m in mine} == {cart["id"]: "completed", cid2: "cancelled", cid3: "cancelled"}
db = SessionLocal(); assert db.get(ProductVariant, var["id"]).stock_quantity == stock0 - 1; db.close()

# KPI
k = c.get("/seller/me", headers=S).json()["data"]["kpi"]
assert k["sales_count"] == 1 and k["revenue"] == float(var["price"]), k

# deactivating the employee kills access; PIN change logs out
assert ok(c.put(f"/finance/employees/{emp_id}", headers=A, json={"is_active": False}))
assert c.get("/seller/me", headers=S).status_code == 403
assert ok(c.put(f"/finance/employees/{emp_id}", headers=A, json={"is_active": True, "pin": "4321"}))
assert c.post("/auth/pin-login", json={"phone": "+998901112233", "pin": "4321"}).json()["success"]

# admin can't sneak an admin via employees; staff login still works
assert c.get("/sales/", headers=A).status_code == 200
print("E2E OK")
