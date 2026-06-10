from app.database import SessionLocal
from app.models.user import User, UserRole
from app.utils.auth import get_password_hash
from app.config import settings

def create_initial_admin():
    db = SessionLocal()
    try:
        # Check if an admin user already exists in the database
        admin = db.query(User).filter(User.role == UserRole.ADMIN).first()
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
            print("ℹ️ Admin user already exists, skipping creation.")
    except Exception as e:
        db.rollback()
        print(f"❌ Error creating initial admin user: {e}")
    finally:
        db.close()
