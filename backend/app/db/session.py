import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Anchor default SQLite DB to backend/glof.db so launching from root or backend/ uses the same file
_DEFAULT_DB_PATH = (Path(__file__).resolve().parent.parent.parent / "glof.db").as_posix()
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{_DEFAULT_DB_PATH}")

# Add connect_args only for SQLite; use StaticPool for in-memory SQLite so multi-threaded TestClient shares one DB
if DATABASE_URL.startswith("sqlite") and (":memory:" in DATABASE_URL or "mode=memory" in DATABASE_URL):
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
else:
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
