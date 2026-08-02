from sqlalchemy import Column, Integer, String, Enum
from app.db.base import Base
import enum

class Role(str, enum.Enum):
    OPERATOR = "OPERATOR"
    ANALYST = "ANALYST"
    COMMANDER = "COMMANDER"
    ADMIN = "ADMIN"

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(Enum(Role), default=Role.OPERATOR)
