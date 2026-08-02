from pydantic import BaseModel
from typing import Optional

class LakeBase(BaseModel):
    name: str
    area_ha: float
    risk_level: Optional[str] = None          # ← now optional
    latitude: Optional[float] = None
    longitude: Optional[float] = None

class LakeCreate(LakeBase):
    pass

class Lake(LakeBase):
    id: int
    risk_level: str                           # always present when reading

    class Config:
        from_attributes = True