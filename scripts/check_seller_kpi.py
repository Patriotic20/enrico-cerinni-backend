"""Seller KPI self-check against the seeded database.

Run: PYTHONPATH=. PYTHONUTF8=1 uv run python scripts/check_seller_kpi.py

Checks that KPI revenue adds up to /sales/stats for the same period, that a
sale cannot be created without a seller, and that cancelling a sale takes it
off its seller's KPI. The cancel check mutates data — run on a dev DB only.
"""
import sys
from datetime import date

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def login(email, password):
    r = client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


manager = login("manager@enrico.uz", "Manager2026!")
cashier = login("kassir@enrico.uz", "Kassir2026!")
period = {"start_date": "2020-01-01", "end_date": date.today().isoformat()}

board = client.get("/employees/kpi", params=period, headers=manager).json()["data"]
stats = client.get("/sales/stats/", params=period, headers=manager).json()["data"]
assert abs(board["totals"]["revenue"] - stats["total_revenue"]) < 0.01, (board["totals"], stats)
assert board["totals"]["sales_count"] == stats["total_sales"]
ranked = [k for k in board["items"] if k["rank"]]
assert ranked and all(a["revenue"] >= b["revenue"] for a, b in zip(ranked, ranked[1:]))
for k in board["items"]:
    assert abs(k["commission"] - k["revenue"] * k["commission_rate"] / 100) < 0.01
print("totals match /sales/stats:", board["totals"]["revenue"])

sellers = client.get("/employees/sellers", headers=cashier).json()["data"]
assert sellers and "salary" not in sellers[0]

r = client.post("/sales/", headers=cashier, json={
    "total_amount": 1, "paid_amount": 1, "payment_method": "cash", "items": [],
})
assert r.status_code == 422, r.text
print("sale without seller rejected")

top = ranked[0]
sales = client.get("/sales/", params={**period, "seller_id": top["id"], "status": "completed", "size": 1},
                   headers=manager).json()["data"]["items"]
sale = sales[0]
assert sale["seller_id"] == top["id"] and sale["seller_name"] == top["name"]
assert client.patch(f"/sales/{sale['id']}/cancel", headers=manager).json()["success"]
after = client.get(f"/employees/{top['id']}/kpi", params=period, headers=manager).json()["data"]["kpi"]
assert abs(top["revenue"] - after["revenue"] - float(sale["total_amount"])) < 0.01
assert after["cancelled_sales"] == top["cancelled_sales"] + 1
print("cancel removed", sale["total_amount"], "from", top["name"])

print("OK")
sys.exit(0)
