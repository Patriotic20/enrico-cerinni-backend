"""Seed the database with mock data for manual testing of the whole system.

Usage (inside the backend container):

    uv run python scripts/seed_mock_data.py           # seed, refuse if data exists
    uv run python scripts/seed_mock_data.py --reset   # wipe business tables, then seed

Users are never touched by --reset, so the admin account survives a re-seed.

The generated data respects the same invariants the API enforces, so the numbers
stay consistent with what the app itself would have produced:

* sale status is derived from paid_amount vs total_amount
  (0 -> DEBT, full -> COMPLETED, otherwise PARTIALLY_PAID)
* client.debt_amount equals the sum of unpaid remainders of that client's sales
* every paid amount has a matching SALE transaction, every debt repayment a
  DEBT_PAYMENT one
* variant stock is the initial stock minus everything that was ever sold
"""

import argparse
import random
import string
import sys
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import text

from app.database import SessionLocal
from app.models.brand import Brand
from app.models.broadcast import BroadcastHistory
from app.models.category import Category
from app.models.client import Client
from app.models.color import Color
from app.models.employee import Employee
from app.models.expense import Expense
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.models.report import ReportTemplate, ReportType
from app.models.salary_payment import SalaryPayment
from app.models.sale import PaymentMethod, Sale, SaleItem, SaleStatus
from app.models.season import Season
from app.models.size import Size
from app.models.supplier import Supplier
from app.models.transaction import Transaction, TransactionType
from app.models.user import User, UserRole
from app.utils.auth import get_password_hash

# Deterministic output so re-seeding gives comparable numbers.
random.seed(20260811)

NOW = datetime.now()
HISTORY_DAYS = 180

# Business tables cleared by --reset, ordered so children die before parents.
RESET_TABLES = [
    "report_executions",
    "reports",
    "report_templates",
    "broadcast_history",
    "transactions",
    "sale_items",
    "sales",
    "salary_payments",
    "expenses",
    "product_variants",
    "products",
    "clients",
    "employees",
    "suppliers",
    "seasons",
    "sizes",
    "colors",
    "brands",
    "categories",
]


# --------------------------------------------------------------------------
# reference data
# --------------------------------------------------------------------------

CATEGORIES = [
    ("Ko'ylaklar", "Ayollar uchun ko'ylaklar"),
    ("Kostyumlar", "Erkaklar va ayollar kostyumlari"),
    ("Shimlar", "Klassik va jinsi shimlar"),
    ("Ko'ylak-erkak", "Erkaklar ko'ylaklari"),
    ("Kurtkalar", "Demi-sezon va qishki kurtkalar"),
    ("Trikotaj", "Sviter, kofta va jemperlar"),
    ("Poyabzal", "Erkaklar va ayollar poyabzali"),
    ("Aksessuarlar", "Kamar, sumka, sharf va boshqalar"),
]

BRANDS = [
    ("Enrico Cerinni", "Asosiy brend - klassik va biznes uslub"),
    ("Milano Style", "Italiya uslubidagi zamonaviy kolleksiya"),
    ("Roma Classic", "Klassik erkaklar kiyimi"),
    ("Verona Donna", "Ayollar uchun premium kolleksiya"),
    ("Firenze Casual", "Kundalik casual kiyimlar"),
    ("Napoli Uomo", "Erkaklar uchun kostyum va ko'ylaklar"),
]

COLORS = [
    ("Qora", "#000000"),
    ("Oq", "#FFFFFF"),
    ("To'q ko'k", "#1E3A8A"),
    ("Qizil", "#DC2626"),
    ("Yashil", "#059669"),
    ("Kulrang", "#6B7280"),
    ("Bej", "#D6C7A1"),
    ("Jigarrang", "#78350F"),
    ("Bordo", "#7F1D1D"),
    ("Moviy", "#0EA5E9"),
]

SIZES = [
    ("XS", "Juda kichik"),
    ("S", "Kichik"),
    ("M", "O'rta"),
    ("L", "Katta"),
    ("XL", "Juda katta"),
    ("XXL", "Eng katta"),
    ("44", "Erkaklar 44"),
    ("46", "Erkaklar 46"),
    ("48", "Erkaklar 48"),
    ("50", "Erkaklar 50"),
    ("52", "Erkaklar 52"),
]

SEASONS = [
    ("Bahor-Yoz 2026", "2026 yilgi bahor-yoz kolleksiyasi"),
    ("Kuz-Qish 2025-2026", "2025-2026 kuz-qish kolleksiyasi"),
    ("Bahor-Yoz 2025", "O'tgan yilgi bahor-yoz kolleksiyasi"),
    ("Barcha mavsumlar", "Mavsumga bog'liq bo'lmagan mahsulotlar"),
]

SUPPLIERS = [
    ("Textile Group MChJ", "Alisher Raximov", "+998901234501", "info@textilegroup.uz",
     "Toshkent sh., Yunusobod tumani, Amir Temur ko'chasi 108"),
    ("Istanbul Moda Ltd", "Mehmet Yilmaz", "+998901234502", "sales@istanbulmoda.com",
     "Istanbul, Turkiya, Laleli mahallasi"),
    ("Samarqand To'qimachilik", "Dilshod Ergashev", "+998901234503", "info@samtex.uz",
     "Samarqand sh., Registon ko'chasi 15"),
    ("Milano Import", "Giulia Rossi", "+998901234504", "office@milanoimport.it",
     "Milano, Italiya, Via Montenapoleone 12"),
    ("Osiyo Savdo MChJ", "Bobur Karimov", "+998901234505", "osiyo@savdo.uz",
     "Toshkent sh., Chilonzor tumani, Bunyodkor 45"),
]

EMPLOYEES = [
    ("Aziz", "Karimov", "Do'kon menejeri", 8_500_000),
    ("Malika", "Tosheva", "Katta sotuvchi", 6_200_000),
    ("Jasur", "Rahimov", "Sotuvchi", 5_000_000),
    ("Nigora", "Yusupova", "Sotuvchi", 5_000_000),
    ("Sardor", "Aliyev", "Omborchi", 4_800_000),
    ("Kamola", "Nazarova", "Buxgalter", 7_000_000),
]

FIRST_NAMES = [
    "Aziz", "Malika", "Jasur", "Nigora", "Sardor", "Kamola", "Bobur", "Zilola",
    "Otabek", "Sevara", "Rustam", "Dilnoza", "Shohruh", "Gulnora", "Timur",
    "Feruza", "Akmal", "Nodira", "Ulugbek", "Barno", "Islom", "Shahzoda",
    "Davron", "Mohira", "Farrux", "Zarina", "Alisher", "Lola", "Bekzod", "Aziza",
]

LAST_NAMES = [
    "Karimov", "Tosheva", "Rahimov", "Yusupova", "Aliyev", "Nazarova",
    "Ergashev", "Saidova", "Xolmatov", "Umarova", "Qodirov", "Ibragimova",
    "Tursunov", "Mirzayeva", "Sobirov", "Yo'ldosheva", "G'aniyev", "Abdullayeva",
    "Normatov", "Hakimova", "Sharipov", "Jo'rayeva", "Mahmudov", "Sultonova",
    "Nabiyev", "Rasulova", "Toshmatov", "Ochilova", "Yodgorov", "Zokirova",
]

DISTRICTS = [
    "Yunusobod", "Chilonzor", "Mirzo Ulug'bek", "Shayxontohur", "Yakkasaroy",
    "Mirobod", "Olmazor", "Sergeli", "Uchtepa", "Bektemir",
]

# (name, category, base price in UZS)
PRODUCTS = [
    ("Klassik kostyum Torino", "Kostyumlar", 2_800_000),
    ("Biznes kostyum Milano", "Kostyumlar", 3_200_000),
    ("Kostyum-troyka Verona", "Kostyumlar", 3_900_000),
    ("Yengil kostyum Napoli", "Kostyumlar", 2_450_000),
    ("Oqshom ko'ylagi Bella", "Ko'ylaklar", 1_750_000),
    ("Kundalik ko'ylak Sofia", "Ko'ylaklar", 890_000),
    ("Ofis ko'ylagi Roma", "Ko'ylaklar", 1_120_000),
    ("Yozgi ko'ylak Capri", "Ko'ylaklar", 760_000),
    ("Uzun ko'ylak Firenze", "Ko'ylaklar", 1_380_000),
    ("Klassik shim Genova", "Shimlar", 680_000),
    ("Jinsi shim Slim Fit", "Shimlar", 590_000),
    ("Chino shim Casual", "Shimlar", 620_000),
    ("Kostyum shimi Torino", "Shimlar", 850_000),
    ("Oq ko'ylak Classic", "Ko'ylak-erkak", 480_000),
    ("Ko'ylak Slim Oxford", "Ko'ylak-erkak", 520_000),
    ("Ko'ylak Linen Summer", "Ko'ylak-erkak", 560_000),
    ("Rangli ko'ylak Casual", "Ko'ylak-erkak", 445_000),
    ("Qishki kurtka Alpina", "Kurtkalar", 2_100_000),
    ("Demi-sezon kurtka Vento", "Kurtkalar", 1_450_000),
    ("Charm kurtka Motto", "Kurtkalar", 3_400_000),
    ("Plash Trench Roma", "Kurtkalar", 1_980_000),
    ("Sviter Merino Wool", "Trikotaj", 720_000),
    ("Kofta Zip Casual", "Trikotaj", 640_000),
    ("Jemper V-yoqa", "Trikotaj", 580_000),
    ("Kardigan Classic", "Trikotaj", 810_000),
    ("Klassik tufli Oxford", "Poyabzal", 1_650_000),
    ("Loafer Milano", "Poyabzal", 1_420_000),
    ("Ayollar tuflisi Elegante", "Poyabzal", 1_280_000),
    ("Qishki botinka Inverno", "Poyabzal", 1_890_000),
    ("Charm kamar Classic", "Aksessuarlar", 320_000),
    ("Ipak sharf Milano", "Aksessuarlar", 280_000),
    ("Bo'yinbog' Business", "Aksessuarlar", 240_000),
    ("Charm sumka Donna", "Aksessuarlar", 1_150_000),
    ("Hamyon Portafoglio", "Aksessuarlar", 420_000),
]

# (description, amount range, recurring monthly?)
# (description, (low, high), recurring, category) — categories come from
# app.models.expense.EXPENSE_CATEGORIES.
EXPENSE_KINDS = [
    ("Do'kon ijara haqi", (12_000_000, 12_000_000), True, "rent"),
    ("Kommunal to'lovlar", (1_200_000, 2_400_000), True, "utilities"),
    ("Internet va telefon aloqasi", (450_000, 650_000), True, "utilities"),
    ("Reklama va marketing", (1_500_000, 4_500_000), False, "marketing"),
    ("Transport va yetkazib berish", (600_000, 1_800_000), False, "daily_expenses"),
    ("Do'kon jihozlari ta'miri", (350_000, 1_900_000), False, "maintenance"),
    ("Kantselyariya va qadoqlash", (180_000, 520_000), False, "daily_expenses"),
    ("Tozalash xizmati", (700_000, 900_000), True, "daily_expenses"),
    ("Bank xizmat haqi", (250_000, 480_000), True, "other"),
    ("Soliq va yig'imlar", (3_000_000, 6_500_000), False, "other"),
]

STAFF_USERS = [
    ("manager", "manager@enrico.uz", "Manager2026!", UserRole.MANAGER, "Aziz", "Karimov", "+998901234511"),
    ("kassir", "kassir@enrico.uz", "Kassir2026!", UserRole.USER, "Malika", "Tosheva", "+998901234512"),
]


def money(value) -> Decimal:
    """Round to 2 decimals — every money column is Numeric(10, 2)."""
    return Decimal(value).quantize(Decimal("0.01"))


def receipt_number(when: datetime, used: set) -> str:
    """Same shape as utils.helpers.generate_receipt_number, but backdated."""
    while True:
        suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
        number = f"RCP-{when.strftime('%Y%m%d')}-{suffix}"
        if number not in used:
            used.add(number)
            return number


def random_datetime_within(days_ago_max: int) -> datetime:
    """A random business-hours timestamp inside the history window."""
    day = NOW - timedelta(days=random.randint(0, days_ago_max))
    return day.replace(
        hour=random.randint(9, 20),
        minute=random.randint(0, 59),
        second=random.randint(0, 59),
        microsecond=0,
    )


def reset(db) -> None:
    print("Clearing business tables (users are kept)...")
    db.execute(text(f"TRUNCATE {', '.join(RESET_TABLES)} RESTART IDENTITY CASCADE"))
    db.commit()


def seed(db) -> dict:
    stats = {}

    # ---- staff users -----------------------------------------------------
    admin = db.query(User).filter(User.role == UserRole.ADMIN).first()
    if admin is None:
        raise SystemExit(
            "No admin user found. Create one first, then re-run this script."
        )

    created_users = 0
    for username, email, password, role, first, last, phone in STAFF_USERS:
        exists = (
            db.query(User)
            .filter((User.username == username) | (User.email == email))
            .first()
        )
        if exists:
            continue
        db.add(
            User(
                username=username,
                email=email,
                hashed_password=get_password_hash(password),
                role=role,
                first_name=first,
                last_name=last,
                phone=phone,
                is_active=True,
            )
        )
        created_users += 1
    db.commit()
    stats["users"] = created_users

    users = db.query(User).filter(User.is_active.is_(True)).all()

    # ---- reference tables ------------------------------------------------
    categories = [Category(name=n, description=d) for n, d in CATEGORIES]
    brands = [Brand(name=n, description=d) for n, d in BRANDS]
    colors = [Color(name=n, hex_code=h, description=f"{n} rang") for n, h in COLORS]
    sizes = [Size(name=n, description=d) for n, d in SIZES]
    seasons = [Season(name=n, description=d) for n, d in SEASONS]
    suppliers = [
        Supplier(name=n, contact_person=c, phone=p, email=e, address=a)
        for n, c, p, e, a in SUPPLIERS
    ]
    db.add_all(categories + brands + colors + sizes + seasons + suppliers)
    db.flush()

    stats["categories"] = len(categories)
    stats["brands"] = len(brands)
    stats["colors"] = len(colors)
    stats["sizes"] = len(sizes)
    stats["seasons"] = len(seasons)
    stats["suppliers"] = len(suppliers)

    category_by_name = {c.name: c for c in categories}
    clothing_sizes = [s for s in sizes if not s.name.isdigit()]
    numeric_sizes = [s for s in sizes if s.name.isdigit()]

    # ---- employees and their salary history ------------------------------
    employees = []
    for i, (first, last, position, salary) in enumerate(EMPLOYEES):
        employees.append(
            Employee(
                first_name=first,
                last_name=last,
                position=position,
                salary=money(salary),
                phone=f"+9989012345{20 + i:02d}",
                email=f"{first.lower()}.{last.lower()}@enrico.uz",
                address=f"Toshkent sh., {random.choice(DISTRICTS)} tumani",
                hire_date=NOW - timedelta(days=random.randint(180, 1500)),
                is_active=True,
                notes=None,
            )
        )
    db.add_all(employees)
    db.flush()
    stats["employees"] = len(employees)

    salary_payments = []
    for employee in employees:
        for months_ago in range(6):
            pay_date = (NOW.replace(day=5) - timedelta(days=30 * months_ago)).replace(
                hour=12, minute=0, second=0, microsecond=0
            )
            if pay_date > NOW:
                continue
            salary_payments.append(
                SalaryPayment(
                    employee_id=employee.id,
                    amount=money(employee.salary),
                    payment_date=pay_date,
                    notes=f"{pay_date.strftime('%B %Y')} oyi uchun oylik",
                    created_at=pay_date,
                )
            )
    db.add_all(salary_payments)
    stats["salary_payments"] = len(salary_payments)

    # ---- clients ---------------------------------------------------------
    clients = []
    for i in range(32):
        first = FIRST_NAMES[i % len(FIRST_NAMES)]
        last = LAST_NAMES[(i * 7) % len(LAST_NAMES)]
        clients.append(
            Client(
                first_name=first,
                last_name=last,
                phone=f"+9989{random.randint(10000000, 99999999)}",
                telegram_chat_id=str(random.randint(100000000, 999999999)) if i % 3 == 0 else None,
                address=f"Toshkent sh., {random.choice(DISTRICTS)} tumani, {random.randint(1, 120)}-uy",
                debt_amount=money(0),
                notes="Doimiy mijoz" if i % 5 == 0 else None,
                is_active=i % 11 != 0,
                created_at=random_datetime_within(HISTORY_DAYS),
            )
        )
    db.add_all(clients)
    db.flush()
    stats["clients"] = len(clients)

    # ---- products and variants -------------------------------------------
    products = []
    variants = []
    variant_meta = {}  # variant -> base price, used when building sale items

    for idx, (name, category_name, base_price) in enumerate(PRODUCTS, start=1):
        product = Product(
            sku=f"PRD-{idx:04d}",
            name=name,
            description=f"{name} - sifatli material, zamonaviy dizayn",
            brand_id=random.choice(brands).id,
            category_id=category_by_name[category_name].id,
            season_id=random.choice(seasons).id,
        )
        products.append(product)
    db.add_all(products)
    db.flush()

    for product, (_, category_name, base_price) in zip(products, PRODUCTS):
        size_pool = numeric_sizes if category_name in ("Kostyumlar", "Shimlar") else clothing_sizes
        if category_name == "Aksessuarlar":
            size_pool = clothing_sizes[:2]

        chosen_colors = random.sample(colors, k=random.randint(2, 4))
        chosen_sizes = random.sample(size_pool, k=min(len(size_pool), random.randint(2, 3)))

        for color in chosen_colors:
            for size in chosen_sizes:
                price = money(base_price * random.uniform(0.95, 1.15))
                variant = ProductVariant(
                    product_id=product.id,
                    color_id=color.id,
                    size_id=size.id,
                    sku=f"{product.sku}-{color.id:02d}-{size.id:02d}",
                    price=price,
                    cost_price=money(price * Decimal("0.62")),
                    stock_quantity=random.randint(15, 90),
                    min_stock_level=random.choice([3, 5, 8, 10]),
                    is_active=True,
                )
                variants.append(variant)
                variant_meta[variant] = price

    db.add_all(variants)
    db.flush()
    stats["products"] = len(products)
    stats["product_variants"] = len(variants)

    # ---- sales, sale items, transactions ---------------------------------
    sales_count = 220
    used_receipts = set()
    sales = []
    transactions = []
    debt_by_client = {}
    sold_units = 0

    for _ in range(sales_count):
        created = random_datetime_within(HISTORY_DAYS)

        # Pick variants that still have stock left to sell.
        candidates = [v for v in variants if v.stock_quantity > 3]
        if not candidates:
            break
        chosen = random.sample(candidates, k=min(len(candidates), random.randint(1, 4)))

        items = []
        total = Decimal("0")
        for variant in chosen:
            quantity = random.randint(1, min(3, variant.stock_quantity))
            unit_price = money(variant_meta[variant])
            line_total = money(unit_price * quantity)
            total += line_total
            variant.stock_quantity -= quantity
            sold_units += quantity
            items.append((variant, quantity, unit_price, line_total))

        total = money(total)

        # 70% walk-in vs named client; debts only make sense for named clients.
        client = random.choice(clients) if random.random() < 0.72 else None

        roll = random.random()
        if client is None or roll < 0.70:
            paid = total
        elif roll < 0.88:
            paid = money(total * Decimal(str(round(random.uniform(0.25, 0.75), 2))))
        else:
            paid = money(0)

        if paid == 0:
            status_value = SaleStatus.DEBT
        elif paid == total:
            status_value = SaleStatus.COMPLETED
        else:
            status_value = SaleStatus.PARTIALLY_PAID

        sale = Sale(
            receipt_number=receipt_number(created, used_receipts),
            client_id=client.id if client else None,
            total_amount=total,
            paid_amount=paid,
            payment_method=random.choice(list(PaymentMethod)),
            status=status_value,
            notes=None,
            user_id=random.choice(users).id,
            created_at=created,
        )
        db.add(sale)
        db.flush()
        sales.append(sale)

        for variant, quantity, unit_price, line_total in items:
            db.add(
                SaleItem(
                    sale_id=sale.id,
                    product_variant_id=variant.id,
                    quantity=quantity,
                    unit_price=unit_price,
                    total_price=line_total,
                    created_at=created,
                )
            )

        if paid > 0:
            transactions.append(
                Transaction(
                    transaction_type=TransactionType.SALE,
                    amount=paid,
                    description=f"Sale {sale.receipt_number} - Paid amount",
                    sale_id=sale.id,
                    client_id=sale.client_id,
                    user_id=sale.user_id,
                    created_at=created,
                )
            )

        if client is not None and paid < total:
            debt_by_client[client.id] = debt_by_client.get(client.id, Decimal("0")) + (total - paid)

    stats["sales"] = len(sales)
    stats["sold_units"] = sold_units

    # A few cancelled sales, so that status filters have something to show.
    # They are recorded without touching stock, debt or transactions.
    for _ in range(8):
        created = random_datetime_within(HISTORY_DAYS)
        variant = random.choice(variants)
        total = money(variant_meta[variant])
        sale = Sale(
            receipt_number=receipt_number(created, used_receipts),
            client_id=random.choice(clients).id,
            total_amount=total,
            paid_amount=money(0),
            payment_method=random.choice(list(PaymentMethod)),
            status=SaleStatus.CANCELLED,
            notes="Mijoz buyurtmadan voz kechdi",
            user_id=random.choice(users).id,
            created_at=created,
        )
        db.add(sale)
        db.flush()
        db.add(
            SaleItem(
                sale_id=sale.id,
                product_variant_id=variant.id,
                quantity=1,
                unit_price=total,
                total_price=total,
                created_at=created,
            )
        )
        stats["sales"] += 1

    # ---- partial debt repayments ------------------------------------------
    # Mirrors SaleService.pay_debt: bump paid_amount, refresh status,
    # decrease client debt, log a DEBT_PAYMENT transaction.
    repayments = 0
    unpaid_sales = [
        s for s in sales
        if s.status in (SaleStatus.DEBT, SaleStatus.PARTIALLY_PAID) and s.client_id
    ]
    for sale in random.sample(unpaid_sales, k=max(1, len(unpaid_sales) // 3)):
        remaining = money(sale.total_amount - sale.paid_amount)
        if remaining <= 0:
            continue
        full = random.random() < 0.45
        payment = remaining if full else money(remaining * Decimal("0.5"))
        if payment <= 0:
            continue

        paid_at = min(NOW, sale.created_at + timedelta(days=random.randint(3, 40)))

        sale.paid_amount = money(sale.paid_amount + payment)
        sale.status = (
            SaleStatus.COMPLETED
            if sale.paid_amount == sale.total_amount
            else SaleStatus.PARTIALLY_PAID
        )
        debt_by_client[sale.client_id] = debt_by_client.get(sale.client_id, Decimal("0")) - payment

        transactions.append(
            Transaction(
                transaction_type=TransactionType.DEBT_PAYMENT,
                amount=payment,
                description=f"Debt payment for sale {sale.receipt_number}",
                sale_id=sale.id,
                client_id=sale.client_id,
                user_id=random.choice(users).id,
                created_at=paid_at,
            )
        )
        repayments += 1
    stats["debt_payments"] = repayments

    client_by_id = {c.id: c for c in clients}
    for client_id, amount in debt_by_client.items():
        client_by_id[client_id].debt_amount = money(max(amount, Decimal("0")))
    stats["clients_with_debt"] = sum(1 for c in clients if c.debt_amount > 0)

    # ---- expenses (+ matching transactions) -------------------------------
    expenses = []
    for months_ago in range(6):
        month_anchor = NOW - timedelta(days=30 * months_ago)
        for description, (low, high), recurring, category in EXPENSE_KINDS:
            if not recurring and random.random() < 0.45:
                continue
            when = month_anchor.replace(
                day=min(28, random.randint(1, 28)),
                hour=random.randint(9, 18),
                minute=random.randint(0, 59),
                second=0,
                microsecond=0,
            )
            if when > NOW:
                continue
            amount = money(random.randint(low, high))
            expenses.append(
                Expense(
                    description=description,
                    amount=amount,
                    category=category,
                    date=when,
                    notes=f"{when.strftime('%B %Y')} oyi uchun" if recurring else None,
                    created_at=when,
                )
            )
            transactions.append(
                Transaction(
                    transaction_type=TransactionType.EXPENSE,
                    amount=amount,
                    description=description,
                    user_id=admin.id,
                    created_at=when,
                )
            )
    db.add_all(expenses)
    stats["expenses"] = len(expenses)

    # ---- purchases from suppliers -----------------------------------------
    for _ in range(24):
        when = random_datetime_within(HISTORY_DAYS)
        supplier = random.choice(suppliers)
        transactions.append(
            Transaction(
                transaction_type=TransactionType.PURCHASE,
                amount=money(random.randint(8_000_000, 45_000_000)),
                description=f"Tovar xaridi - {supplier.name}",
                user_id=admin.id,
                created_at=when,
            )
        )

    # ---- a few refunds -----------------------------------------------------
    completed_sales = [s for s in sales if s.status == SaleStatus.COMPLETED]
    for sale in random.sample(completed_sales, k=min(6, len(completed_sales))):
        when = min(NOW, sale.created_at + timedelta(days=random.randint(1, 14)))
        transactions.append(
            Transaction(
                transaction_type=TransactionType.REFUND,
                amount=money(sale.total_amount * Decimal("0.5")),
                description=f"Refund for sale {sale.receipt_number}",
                sale_id=sale.id,
                client_id=sale.client_id,
                user_id=random.choice(users).id,
                created_at=when,
            )
        )

    db.add_all(transactions)
    stats["transactions"] = len(transactions)

    # ---- low stock, so the dashboard warning has something to show ---------
    low_stock = random.sample(variants, k=14)
    for variant in low_stock:
        variant.stock_quantity = random.randint(0, variant.min_stock_level)
    stats["low_stock_variants"] = len(low_stock)

    # ---- marketing broadcast history ---------------------------------------
    broadcasts = [
        ("sms", "Yangi bahor kolleksiyasi keldi! Chegirmalar 30% gacha.", 120, 118, 2),
        ("telegram", "Hafta oxiri aksiya: barcha kostyumlarga 20% chegirma.", 86, 86, 0),
        ("sms", "Hurmatli mijoz, qarzdorligingizni to'lashni unutmang.", 24, 22, 2),
        ("telegram", "Yangi yil sovg'alari uchun maxsus takliflar!", 91, 89, 2),
        ("sms", "Do'konimiz yakshanba kuni 10:00 dan 20:00 gacha ishlaydi.", 132, 130, 2),
    ]
    broadcast_rows = []
    for i, (channel, message, total_recipients, sent, failed) in enumerate(broadcasts):
        when = NOW - timedelta(days=(i + 1) * 12)
        broadcast_rows.append(
            BroadcastHistory(
                channel=channel,
                message=message,
                total_recipients=total_recipients,
                attempted=total_recipients,
                sent=sent,
                failed=failed,
                error_summary="Nomer mavjud emas" if failed else None,
                created_by=admin.id,
                created_at=when,
            )
        )
    db.add_all(broadcast_rows)
    stats["broadcasts"] = len(broadcast_rows)

    # ---- report templates ---------------------------------------------------
    templates = [
        ("Kunlik savdo hisoboti", ReportType.SALES, {"period": "daily", "group_by": "payment_method"}),
        ("Oylik moliyaviy hisobot", ReportType.FINANCE, {"period": "monthly", "include_expenses": True}),
        ("Ombor qoldiqlari", ReportType.INVENTORY, {"only_low_stock": False}),
        ("Qarzdor mijozlar", ReportType.CLIENTS, {"with_debt": True}),
    ]
    template_rows = [
        ReportTemplate(
            name=name,
            description=f"{name} uchun tayyor shablon",
            report_type=report_type,
            config_template=config,
            is_system_template=True,
            is_active=True,
            created_by=admin.id,
        )
        for name, report_type, config in templates
    ]
    db.add_all(template_rows)
    stats["report_templates"] = len(template_rows)

    db.commit()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed mock data for testing.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Truncate business tables before seeding (users are preserved).",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        existing = db.query(Product).count()
        if existing and not args.reset:
            print(
                f"Database already holds {existing} products. "
                "Re-run with --reset to replace the existing data."
            )
            sys.exit(1)

        if args.reset:
            reset(db)

        stats = seed(db)

        print("\nMock data created:")
        for key in sorted(stats):
            print(f"  {key:22} {stats[key]}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
