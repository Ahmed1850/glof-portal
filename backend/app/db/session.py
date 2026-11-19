import atexit
import os
import sqlite3
import uuid
from pathlib import Path
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

# Anchor default SQLite DB to backend/glof.db so launching from root or backend/ uses the same file
_DEFAULT_DB_PATH = (Path(__file__).resolve().parent.parent.parent / "glof.db").as_posix()
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{_DEFAULT_DB_PATH}")

# For in-memory SQLite, use a per-engine shared-cache URI with QueuePool + an anchor connection
# so each AnyIO/TestClient thread gets its own sqlite3.Connection to the same in-memory DB.
if DATABASE_URL.startswith("sqlite") and (":memory:" in DATABASE_URL or "mode=memory" in DATABASE_URL):
    from sqlalchemy.pool import QueuePool

    _mem_uri = (
        DATABASE_URL.replace("sqlite:///", "", 1)
        if "mode=memory" in DATABASE_URL
        else f"file:glof_mem_{uuid.uuid4().hex}?mode=memory&cache=shared"
    )
    if "cache=shared" not in _mem_uri:
        _mem_uri += ("&" if "?" in _mem_uri else "?") + "cache=shared"

    _anchor_conn = sqlite3.connect(_mem_uri, uri=True, check_same_thread=False, timeout=30.0)

    def _mem_connect():
        conn = sqlite3.connect(_mem_uri, uri=True, check_same_thread=False, timeout=30.0)
        conn.execute("PRAGMA read_uncommitted = 1")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    engine = create_engine(
        "sqlite://",
        creator=_mem_connect,
        poolclass=QueuePool,
        pool_size=16,
        max_overflow=16,
    )

    def _close_anchor(*_args, **_kwargs):
        global _anchor_conn
        if _anchor_conn is not None:
            try:
                _anchor_conn.close()
            except Exception:
                pass
            _anchor_conn = None

    event.listen(engine, "engine_disposed", _close_anchor)
    atexit.register(_close_anchor)
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
