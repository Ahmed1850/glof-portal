import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Default to SQLite for local development without Docker
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./glof.db")

# Add connect_args only for SQLite
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(
    DATABASE_URL, connect_args=connect_args
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()