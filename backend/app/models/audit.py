from sqlalchemy import Column, Integer, String, DateTime
from app.db.base import Base
from datetime import datetime

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, index=True) # Or Integer if we have a user ID
    action = Column(String)
    timestamp = Column(DateTime, default=datetime.utcnow)
    ip_address = Column(String)
