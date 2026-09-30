"""Check that cashiers are kept out of staff-only endpoints.

Needs the demo users from seed_mock_data.py. Usage:

    PYTHONUTF8=1 uv run python scripts/check_roles.py
"""

import sys

from fastapi.testclient import TestClient

sys.path.insert(0, ".")
from main import app  # noqa: E402


def login(email, password):
    client = TestClient(app)
    r = client.post("/auth/login", json={"email": email, "password": password})
    r.raise_for_status()
    client.headers["Authorization"] = f"Bearer {r.json()['data']['access_token']}"
    return client


# (method, path, cashier allowed)
CASES = [
    ("GET", "/dashboard/stats", False),
    ("GET", "/finance/expenses", False),
    ("GET", "/marketing/stats", False),
    ("GET", "/reports/templates", False),
    ("GET", "/sales/", False),
    ("GET", "/sales/stats/", False),
    ("POST", "/brands", False),
    ("DELETE", "/clients/999999", False),
    ("PATCH", "/clients/999999/debt", False),
    ("PATCH", "/sales/999999/cancel", False),
    ("GET", "/employees/kpi", False),
    ("GET", "/employees/1/kpi", False),
    ("GET", "/labels/template", False),
    ("PUT", "/labels/template", False),
    ("GET", "/employees/sellers", True),
    ("GET", "/products/", True),
    ("GET", "/clients/", True),
    ("GET", "/brands", True),
    ("GET", "/sales/debt-stats", True),
    ("GET", "/settings/categories", True),
]

cashier = login("kassir@enrico.uz", "Kassir2026!")
manager = login("manager@enrico.uz", "Manager2026!")
failures = 0
for method, path, cashier_allowed in CASES:
    kw = {"json": {}} if method in ("POST", "PATCH") else {}
    c = cashier.request(method, path, **kw).status_code
    m = manager.request(method, path, **kw).status_code
    denied = (401, 403)
    ok = (c not in denied) == cashier_allowed and m not in denied
    failures += not ok
    print("OK  " if ok else "FAIL", f"{method:6} {path:26} cashier={c} manager={m}")

anon = TestClient(app).get("/products/barcode/x").status_code
failures += anon != 401
print("OK  " if anon == 401 else "FAIL", f"anonymous barcode lookup -> {anon}")

sys.exit(1 if failures else 0)
