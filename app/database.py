from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from app.config import settings

# Create database engine
engine = create_engine(
    settings.database.sync_database_url,
    pool_pre_ping=True,
    pool_recycle=300,
    # Per worker process. Keep pool_size+max_overflow times WEB_CONCURRENCY below
    # Postgres max_connections (100 by default).
    pool_size=10,
    max_overflow=10,
)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create base class for models
Base = declarative_base()


# Dependency to get database session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
