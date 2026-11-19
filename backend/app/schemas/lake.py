from typing import Optional
from pydantic import BaseModel, ConfigDict


class LakeBase(BaseModel):
    name: str
    area_ha: float
    risk_level: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class LakeCreate(LakeBase):
    pass


class Lake(LakeBase):
    id: int
    risk_level: str
    geom: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)
