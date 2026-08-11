from datetime import datetime
from app.database import SessionLocal
from app.models.user import User, UserRole
from app.utils.auth import get_password_hash
from app.config import settings

from app.models.category import Category
from app.models.brand import Brand
from app.models.season import Season
from app.models.color import Color
from app.models.size import Size
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.models.client import Client
from app.models.supplier import Supplier
from app.models.employee import Employee
from app.models.expense import Expense

def seed_mock_data(db):
    # Check if Category table is empty to avoid duplicating mock data
    if db.query(Category).first():
        print("ℹ️ Database already contains category data. Skipping mock data seeding.")
        return

    print("🌱 Seeding database with mock data for testing...")

    # 1. Categories
    categories = [
        Category(name="Kostyumlar", description="Erkaklar va ayollar kostyumlari, nimchalar"),
        Category(name="Ko'ylaklar", description="Erkaklar klassik va sport ko'ylaklari"),
        Category(name="Shimlar", description="Klassik shimlar va jinsilar"),
        Category(name="Paltolar", description="Kuzgi va qishki ustki kiyimlar, paltolar"),
        Category(name="Aksessuarlar", description="Galstuklar, kamarlar, hamyonlar"),
    ]
    for c in categories:
        db.add(c)
    db.commit()

    # 2. Brands
    brands = [
        Brand(name="Enrico Cerrini", description="Premium darajadagi Italiya kiyim markasi"),
        Brand(name="Zara", description="Zamonaviy kundalik kiyimlar markasi"),
        Brand(name="Massimo Dutti", description="Klassik va oqlangan uslubdagi kiyimlar"),
    ]
    for b in brands:
        db.add(b)
    db.commit()

    # 3. Seasons
    seasons = [
        Season(name="Yoz 2026", description="2026-yilgi yozgi mavsum kolleksiyasi"),
        Season(name="Kuz/Qish 2026", description="2026-yilgi kuzgi va qishki mavsum kolleksiyasi"),
        Season(name="Bahor 2027", description="2027-yilgi bahorgi mavsum kolleksiyasi"),
    ]
    for s in seasons:
        db.add(s)
    db.commit()

    # 4. Colors
    colors = [
        Color(name="Qora", hex_code="#000000", description="Klassik qora rang"),
        Color(name="Oq", hex_code="#FFFFFF", description="Tiniq oq rang"),
        Color(name="To'q ko'k", hex_code="#000080", description="Nafis to'q ko'k rang"),
        Color(name="Kulrang", hex_code="#808080", description="Neytral kulrang"),
        Color(name="Jigarrang", hex_code="#8B4513", description="Tabiiy jigarrang"),
    ]
    for col in colors:
        db.add(col)
    db.commit()

    # 5. Sizes
    sizes = [
        Size(name="S", description="Small (Kichik)"),
        Size(name="M", description="Medium (O'rtacha)"),
        Size(name="L", description="Large (Katta)"),
        Size(name="XL", description="Extra Large (Juda katta)"),
        Size(name="XXL", description="Double Extra Large (Ikki barobar katta)"),
    ]
    for sz in sizes:
        db.add(sz)
    db.commit()

    # 6. Clients
    clients = [
        Client(first_name="Alijon", last_name="Valiev", phone="+998901234567", address="Toshkent sh., Chilonzor tumani", debt_amount=0.00, notes="Doimiy mijoz"),
        Client(first_name="Barno", last_name="Karimova", phone="+998939876543", address="Toshkent sh., Yunusobod tumani", debt_amount=150.00, notes="Muddatli to'lov oluvchi"),
        Client(first_name="Jasur", last_name="Axmedov", phone="+998974561230", address="Samarqand sh., Registon ko'chasi", debt_amount=0.00, notes="Yangi mijoz"),
    ]
    for cl in clients:
        db.add(cl)
    db.commit()

    # 7. Suppliers
    suppliers = [
        Supplier(name="Milano Textiles", contact_person="Giovanni Rossi", phone="+3902123456", email="giovanni@milanotex.it", address="Milan, Italy"),
        Supplier(name="Toshkent Premium Mato", contact_person="Diyorbek Alimov", phone="+998909998877", email="info@tpm.uz", address="Toshkent, Sergeli Sanoat zonasi"),
    ]
    for sup in suppliers:
        db.add(sup)
    db.commit()

    # 8. Employees
    employees = [
        Employee(first_name="Dilshod", last_name="Umarov", phone="+998911112233", email="dilshod@enrico.uz", position="Sotuvchi", salary=400.00, address="Toshkent sh., Olmazor tumani"),
        Employee(first_name="Shahnoza", last_name="Sodiqova", phone="+998922223344", email="shahnoza@enrico.uz", position="Kassir", salary=350.00, address="Toshkent sh., Yakkasaroy tumani"),
    ]
    for emp in employees:
        db.add(emp)
    db.commit()

    # 9. Expenses
    expenses = [
        Expense(description="Arenda to'lovi", amount=1200.00, date=datetime.now(), notes="Do'kon ijarasi uchun oylik to'lov"),
        Expense(description="Kantselyariya xarajatlari", amount=45.50, date=datetime.now(), notes="Ruchka, qog'oz va papkalar"),
    ]
    for exp in expenses:
        db.add(exp)
    db.commit()

    # 10. Products
    p1 = Product(sku="EC-SUIT-001", name="Enrico Cerrini Jun Kostyum-Shim", description="100% jun matodan tikilgan yuqori sifatli klassik erkaklar kostyumi", brand_id=brands[0].id, category_id=categories[0].id, season_id=seasons[1].id)
    p2 = Product(sku="EC-SHIRT-102", name="Klassik Oq Ko'ylak", description="Paxtali matodan tikilgan ergonomik klassik erkaklar ko'ylagi", brand_id=brands[0].id, category_id=categories[1].id, season_id=seasons[0].id)
    p3 = Product(sku="ZR-JEANS-301", name="Zara Slim Fit Jinsi", description="Zamonaviy cho'ziluvchan va mustahkam jinsi shim", brand_id=brands[1].id, category_id=categories[2].id, season_id=seasons[0].id)
    
    db.add(p1)
    db.add(p2)
    db.add(p3)
    db.commit()

    # 11. Product Variants
    variants = [
        # Jun Kostyum-Shim variants
        ProductVariant(product_id=p1.id, color_id=colors[2].id, size_id=sizes[2].id, sku="EC-SUIT-001-BL-L", price=350.00, cost_price=150.00, stock_quantity=10, min_stock_level=2),
        ProductVariant(product_id=p1.id, color_id=colors[2].id, size_id=sizes[3].id, sku="EC-SUIT-001-BL-XL", price=350.00, cost_price=150.00, stock_quantity=8, min_stock_level=2),
        ProductVariant(product_id=p1.id, color_id=colors[0].id, size_id=sizes[2].id, sku="EC-SUIT-001-BK-L", price=350.00, cost_price=150.00, stock_quantity=15, min_stock_level=3),
        
        # Klassik Oq Ko'ylak variants
        ProductVariant(product_id=p2.id, color_id=colors[1].id, size_id=sizes[1].id, sku="EC-SHIRT-102-WT-M", price=55.00, cost_price=20.00, stock_quantity=40, min_stock_level=5),
        ProductVariant(product_id=p2.id, color_id=colors[1].id, size_id=sizes[2].id, sku="EC-SHIRT-102-WT-L", price=55.00, cost_price=20.00, stock_quantity=50, min_stock_level=5),
        ProductVariant(product_id=p2.id, color_id=colors[2].id, size_id=sizes[2].id, sku="EC-SHIRT-102-BL-L", price=55.00, cost_price=20.00, stock_quantity=30, min_stock_level=5),

        # Zara Slim Fit Jinsi variants
        ProductVariant(product_id=p3.id, color_id=colors[2].id, size_id=sizes[1].id, sku="ZR-JEANS-301-BL-M", price=75.00, cost_price=30.00, stock_quantity=20, min_stock_level=3),
        ProductVariant(product_id=p3.id, color_id=colors[2].id, size_id=sizes[2].id, sku="ZR-JEANS-301-BL-L", price=75.00, cost_price=30.00, stock_quantity=25, min_stock_level=3),
    ]
    for v in variants:
        db.add(v)
    db.commit()

    print("✅ Seeded database with testing mock data successfully!")


def create_initial_admin():
    db = SessionLocal()
    try:
        # Check if an admin user already exists by email
        admin = db.query(User).filter(User.email == settings.admin_email).first()
        
        # If not found by email, try by username
        if not admin:
            admin = db.query(User).filter(User.username == settings.admin_username).first()

        if not admin:
            print(f"🚀 Creating initial admin user with email '{settings.admin_email}'...")
            admin_user = User(
                email=settings.admin_email,
                username=settings.admin_username,
                hashed_password=get_password_hash(settings.admin_password),
                role=UserRole.ADMIN,
                is_active=True,
                first_name="Admin",
                last_name="System",
            )
            db.add(admin_user)
            db.commit()
            print("✅ Initial admin user successfully created!")
        else:
            # Synchronize email and password in case they changed, to guarantee login ability
            print(f"ℹ️ Admin user '{admin.username}' already exists. Syncing email, password, and role...")
            admin.email = settings.admin_email
            admin.role = UserRole.ADMIN
            admin.hashed_password = get_password_hash(settings.admin_password)
            db.commit()
            print("✅ Admin user credentials synchronized successfully!")

        # Seed mock data
        seed_mock_data(db)
    except Exception as e:
        db.rollback()
        print(f"❌ Error during database initialization: {e}")
    finally:
        db.close()
